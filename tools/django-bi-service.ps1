[CmdletBinding()]
param(
  [ValidateSet(
    "ConfigureCredentials", "ProvisionRole", "PlanMigration", "ApplyMigration", "VerifyMigration",
    "Start", "Stop", "Status", "EnableStartup", "DisableStartup"
  )]
  [string]$Action = "Status",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$OrchestratedLifecycleAclToken = "",
  [string]$ApprovedPlanId = "",
  [string]$ApprovedRunId = "",
  [switch]$Json
)

$ErrorActionPreference = "Stop"
$RequestedAction = $Action
$RequestedJson = $Json.IsPresent
$RequestedOrchestratedLifecycleAclToken = $OrchestratedLifecycleAclToken
$RequestedApprovedPlanId = $ApprovedPlanId
$RequestedApprovedRunId = $ApprovedRunId
$BaseScript = Join-Path $PSScriptRoot "django-local-service.ps1"
if (-not (Test-Path -LiteralPath $BaseScript -PathType Leaf)) { throw "缺少 Django 本机服务基础控制器" }
$PreviousLibraryOnly = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process")
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $BaseScript -RuntimeRoot $RuntimeRoot
} finally {
  [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $PreviousLibraryOnly, "Process")
}
$Action = $RequestedAction
$Json = [switch]$RequestedJson
$OrchestratedLifecycleAclToken = $RequestedOrchestratedLifecycleAclToken
$ApprovedPlanId = $RequestedApprovedPlanId
$ApprovedRunId = $RequestedApprovedRunId

$BiCredentialPath = Join-Path $RuntimeRoot "secrets\bi-credentials.dpapi.json"
$BiReaderPidPath = Join-Path $RunDirectory "django-bi-reader.pid.json"
$BiReaderHealthUrl = "http://127.0.0.1:8081/health/ready"
$BiStartupPath = Join-Path $RuntimeRoot "bi-service-enabled.json"
$BiReaderMaxBodyBytes = 1048576

function Assert-BiRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "BI 服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=bi"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-BiPortFree([string]$Operation) {
  if (@(Get-PortListeners 8081).Count -gt 0) { throw "$Operation 前必须先停止 Django BI reader" }
  if (Resolve-OwnedProcess "django-bi-reader" $BiReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django BI reader"
  }
}

function Read-BiCredentials {
  $payload = Read-JsonFile $BiCredentialPath "Django BI DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @("version", "databaseBiReader", "createdAt")) -or [int]$payload.version -ne 1) {
    throw "Django BI DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseBiReader) "databaseBiReader"
  Assert-StrongSecret $reader "databaseBiReader"
  return [pscustomobject]@{ ReaderPassword = $reader }
}

function Configure-BiCredentials {
  Assert-BiRuntimeEntry
  Assert-BiPortFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $BiCredentialPath -PathType Leaf) {
    Read-BiCredentials | Out-Null
    Write-Output "Django BI DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  try {
    Write-AtomicJson $BiCredentialPath ([ordered]@{
      version = 1
      databaseBiReader = Protect-Value $reader
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "bi_credentials_configured"
    Write-Output "Django BI reader DPAPI 凭据已创建；未配置数据库角色。"
  } finally { $reader = $null }
}

function Provision-BiRole {
  Assert-BiRuntimeEntry
  Assert-BiPortFree "ProvisionRole"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置 BI reader 角色" }
  $biSecrets = Read-BiCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousPassword = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_BI_READER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url "postgres" $superuser "teruisi_bi_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_BI_READER_PASSWORD = $biSecrets.ReaderPassword
    $code = @'
import os
import psycopg
from psycopg import sql

role = "teruisi_bi_reader"
tables = (
    "bi_migration_runs",
    "sales_data_revisions", "sales_import_batches", "sales_order_lines",
    "erp_product_master", "erp_combo_items", "erp_reference_import_batches_pg",
    "erp_reference_import_scope_heads", "erp_reference_write_authority",
    "inventory_import_batches", "inventory_stock_lines", "inventory_age_lines", "inventory_data_revisions",
    "replenishment_plan_items", "inventory_operating_settings",
    "inventory_guangdong_monitor_items", "inventory_guangdong_supplier_cycles",
)
connection = psycopg.connect(os.environ["TERUISI_PROVISION_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
    if cursor.fetchone() is None:
        cursor.execute(sql.SQL(
            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(role)))
    cursor.execute(sql.SQL(
        "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
    ).format(sql.Identifier(role), sql.Literal(os.environ["TERUISI_PROVISION_BI_READER_PASSWORD"])))
    cursor.execute(
        "SELECT parent.rolname FROM pg_auth_members membership "
        "JOIN pg_roles parent ON parent.oid=membership.roleid "
        "JOIN pg_roles member ON member.oid=membership.member WHERE member.rolname=%s",
        (role,),
    )
    for (parent,) in cursor.fetchall():
        cursor.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(parent), sql.Identifier(role)))
    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(sql.Identifier(role)))
    cursor.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(sql.Identifier(connection.info.dbname), sql.Identifier(role)))
    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(connection.info.dbname), sql.Identifier(role)))
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
    cursor.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(
        sql.SQL(",").join(sql.Identifier(table) for table in tables), sql.Identifier(role)
    ))
    cursor.execute("DROP POLICY IF EXISTS bi_revision_reader ON sales_data_revisions")
    cursor.execute(
        "CREATE POLICY bi_revision_reader ON sales_data_revisions FOR SELECT TO teruisi_bi_reader "
        "USING (domain IN ('sales','erp'))"
    )
    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "bi")
    cursor.execute("ALTER ROLE teruisi_bi_reader SET default_transaction_read_only=on")
    cursor.execute(
        "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=%s", (role,)
    )
    flags = cursor.fetchone()
    if flags is None or any(flags):
        raise RuntimeError("BI reader role attributes are excessive")
    cursor.execute(
        "SELECT has_schema_privilege(%s,'public','CREATE'),"
        "has_database_privilege(%s,current_database(),'CREATE')", (role, role)
    )
    if any(cursor.fetchone()):
        raise RuntimeError("BI reader retains DDL privileges")
    for table in tables:
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)", (role, table, privilege))
            if cursor.fetchone()[0]:
                raise RuntimeError("BI reader DML escaped read-only allowlist")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "bi_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog (Join-Path $LogDirectory "bi-role-provision.$RunId.log") "bi_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) { throw "独立 BI reader 数据库角色配置失败（$(Get-NativeFailureSummary $nativeRun)）" }
    Write-LauncherEvent "INFO" "bi_database_role_provisioned"
    Write-Output "独立 BI reader 数据库角色已按只读白名单配置。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_BI_READER_PASSWORD", $previousPassword, "Process")
    $biSecrets = $null
    $superuser = $null
  }
}

function Get-BiMigrationState([object]$RuntimeSecrets, [object]$BiSecrets) {
  $readerUrl = Database-Url "teruisi_bi_reader" $BiSecrets.ReaderPassword "teruisi_bi_migration_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
import django

django.setup()
from bi.models import BiMigrationRun

run = BiMigrationRun.objects.filter(status="verified").order_by("-verified_at", "-created_at").first()
if run is None:
    raise RuntimeError("verified BI migration run missing")
print(json.dumps({
    "migrationRunId": run.id,
    "sourceDigest": run.source_digest,
    "contractVersion": run.contract_version,
}, separators=(",", ":")))
'@
  try {
    $json = Invoke-WithDjangoEnvironment $RuntimeSecrets $readerUrl "bi_reader" $true $BiReaderMaxBodyBytes "" "" {
      $launcher = ConvertTo-PythonBase64Launcher $code "bi_migration_probe.py"
      $run = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
      if ($run.ExitCode -ne 0) { throw "BI 迁移证据探针失败（$(Get-NativeFailureSummary $run)）" }
      Write-Output ((ConvertFrom-UniqueNativeJson $run "BI 迁移证据探针") | ConvertTo-Json -Compress)
    }
    $payload = ((@($json) | Select-Object -Last 1) | ConvertFrom-Json)
    if (-not (Test-ExactObjectPropertyNames $payload @("migrationRunId", "sourceDigest", "contractVersion")) -or
        [string]$payload.migrationRunId -cnotmatch "^bi-apply-[0-9a-f]{32}$" -or
        [string]$payload.sourceDigest -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$payload.contractVersion -cne "bi-dashboard-read-model-v1") {
      throw "BI 迁移证据结构无效"
    }
    return $payload
  } finally { $readerUrl = $null }
}

function Invoke-BiMigration([ValidateSet("plan", "apply", "verify")][string]$Mode) {
  Assert-BiRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝执行 BI 迁移" }
  if ($Mode -eq "apply" -and $ApprovedPlanId -cnotmatch "^bi-plan-[0-9a-f]{32}$") {
    throw "ApplyMigration 必须提供精确 approved plan id"
  }
  if ($Mode -eq "verify" -and $ApprovedRunId -cnotmatch "^bi-apply-[0-9a-f]{32}$") {
    throw "VerifyMigration 必须提供精确 approved run id"
  }
  $runtimeSecrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $runtimeSecrets.OwnerPassword `
    "teruisi_bi_migration_$Mode" $WriterStatementTimeoutMs
  try {
    $arguments = @((Join-Path $BackendRoot "manage.py"), "migrate_bi_read_model", "--$Mode")
    if ($Mode -eq "apply") { $arguments += @("--approved-plan-id", $ApprovedPlanId) }
    if ($Mode -eq "verify") { $arguments += @("--approved-run-id", $ApprovedRunId) }
    $json = Invoke-WithDjangoEnvironment $runtimeSecrets $ownerUrl "migration_writer" $false $ReaderMaxBodyBytes "" "" {
      $run = Invoke-BoundedNativeProcess $Python $arguments $BackendRoot
      Write-NativeDiagnosticLog (Join-Path $LogDirectory "bi-migration-$Mode.$RunId.log") `
        "bi_migration_$Mode" $run
      if ($run.ExitCode -ne 0) { throw "BI $Mode 失败（$(Get-NativeFailureSummary $run)）" }
      Write-Output ((ConvertFrom-UniqueNativeJson $run "BI $Mode") | ConvertTo-Json -Compress -Depth 8)
    }
    $payload = ((@($json) | Select-Object -Last 1) | ConvertFrom-Json)
    if ([string]$payload.mode -cne $Mode -or
        [string]$payload.contractVersion -cne "bi-dashboard-read-model-v1" -or
        [string]$payload.sourceDigest -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$payload.planId -cnotmatch "^bi-plan-[0-9a-f]{32}$" -or
        [bool]$payload.factCopyRequired) {
      throw "BI $Mode 返回了无效迁移凭据"
    }
    Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
  } finally {
    $runtimeSecrets = $null
    $ownerUrl = $null
  }
}

function Start-BiReader([object]$RuntimeSecrets, [object]$BiSecrets) {
  $arguments = @(
    "--listen=127.0.0.1:8081", "--threads=6", "--connection-limit=60", "--channel-timeout=35",
    "--cleanup-interval=30", "--ident=teruisi-django-bi-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$BiReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-bi-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-bi-reader" $BiReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "bi-reader" $BiReaderHealthUrl "127.0.0.1:8081"
    return $false
  }
  if (@(Get-PortListeners 8081).Count -gt 0) { throw "端口 8081 被非本部署服务占用" }
  Remove-OldServiceLogs "django-bi-reader"
  $readerUrl = Database-Url "teruisi_bi_reader" $BiSecrets.ReaderPassword "teruisi_django_bi_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $readerUrl "bi_reader" $true $BiReaderMaxBodyBytes "" "" {
    Start-ManagedProcess "django-bi-reader" $Waitress $arguments $BackendRoot $BiReaderPidPath $fingerprint `
      (Join-Path $LogDirectory "django-bi-reader.$RunId.stdout.log") `
      (Join-Path $LogDirectory "django-bi-reader.$RunId.stderr.log") | Out-Null
  }
  $readerUrl = $null
  try { Wait-DjangoReady "bi-reader" $BiReaderHealthUrl "127.0.0.1:8081"; return $true }
  catch { Stop-OwnedProcess "django-bi-reader" $BiReaderPidPath $Waitress; throw }
}

function Start-BiStack([string]$LifecycleAclToken = "") {
  Assert-BiRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动 BI reader" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets
  $biSecrets = Read-BiCredentials
  try {
    $migration = Get-BiMigrationState $runtimeSecrets $biSecrets
    if (Test-Path -LiteralPath $BiStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $BiStartupPath "Django BI 开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @("version", "migrationRunId", "sourceDigest", "enabledAt")) -or
          [int]$startup.version -ne 1 -or
          [string]$startup.migrationRunId -cne [string]$migration.migrationRunId -or
          [string]$startup.sourceDigest -cne [string]$migration.sourceDigest) {
        throw "Django BI 开机启动凭据与已验证迁移证据不一致"
      }
    }
    Start-BiReader $runtimeSecrets $biSecrets | Out-Null
    Wait-DjangoReady "bi-reader" $BiReaderHealthUrl "127.0.0.1:8081"
    Write-Output "Django BI reader 已就绪：http://127.0.0.1:8081。"
  } finally { $runtimeSecrets = $null; $biSecrets = $null }
}

function Stop-BiStack([string]$LifecycleAclToken = "") {
  Assert-BiRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-bi-reader" $BiReaderPidPath $Waitress
  Write-Output "Django BI reader 已停止；上游业务域、ERP 与 PostgreSQL 未改变。"
}

function Enable-BiStartup {
  Assert-BiRuntimeEntry
  $runtimeSecrets = Read-Secrets
  $biSecrets = Read-BiCredentials
  try {
    $migration = Get-BiMigrationState $runtimeSecrets $biSecrets
    Start-BiStack
    Write-AtomicJson $BiStartupPath ([ordered]@{
      version = 1
      migrationRunId = [string]$migration.migrationRunId
      sourceDigest = [string]$migration.sourceDigest
      enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django BI reader 已加入现有受控开机启动链。"
  } finally { $runtimeSecrets = $null; $biSecrets = $null }
}

function Disable-BiStartup {
  Assert-BiRuntimeEntry
  if (Test-Path -LiteralPath $BiStartupPath -PathType Leaf) { Remove-Item -LiteralPath $BiStartupPath -Force }
  Write-Output "Django BI reader 已退出开机启动链；当前运行进程未改变。"
}

function Show-BiStatus {
  $reader = "stopped"
  try {
    if (Resolve-OwnedProcess "django-bi-reader" $BiReaderPidPath $Waitress) { $reader = "running" }
    elseif (@(Get-PortListeners 8081).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  $readiness = "not_ready"
  if ($reader -eq "running") {
    try {
      if ((Invoke-WebRequest -UseBasicParsing -Uri $BiReaderHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8081" }).StatusCode -eq 200) {
        $readiness = "ready"
      }
    } catch {}
  }
  $status = [pscustomobject][ordered]@{
    BiReader = $reader
    ReaderReadiness = $readiness
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) } else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-BiCredentials } }
    "ProvisionRole" { Invoke-WithServiceMutex { Provision-BiRole } }
    "PlanMigration" { Invoke-WithServiceMutex { Invoke-BiMigration "plan" } }
    "ApplyMigration" { Invoke-WithServiceMutex { Invoke-BiMigration "apply" } }
    "VerifyMigration" { Invoke-WithServiceMutex { Invoke-BiMigration "verify" } }
    "Start" { Invoke-WithServiceMutex { Start-BiStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-BiStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-BiStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-BiStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-BiStartup } }
  }
} catch { Write-LauncherEvent "ERROR" "bi_action_failed" $_.Exception.Message; throw }
