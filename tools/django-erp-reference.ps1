[CmdletBinding()]
param(
  [ValidateSet(
    "ConfigureCredentials", "ProvisionRoles", "Start", "Stop", "Status",
    "EnableStartup", "DisableStartup"
  )]
  [string]$Action = "Status",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$OrchestratedLifecycleAclToken = "",
  [switch]$Json
)

$ErrorActionPreference = "Stop"
$RequestedAction = $Action
$RequestedJson = $Json.IsPresent
$RequestedOrchestratedLifecycleAclToken = $OrchestratedLifecycleAclToken
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

$ErpReferenceCredentialPath = Join-Path $RuntimeRoot "secrets\erp-reference-credentials.dpapi.json"
$ErpReferenceReaderPidPath = Join-Path $RunDirectory "django-erp-reference-reader.pid.json"
$ErpReferenceWriterPidPath = Join-Path $RunDirectory "django-erp-reference-writer.pid.json"
$ErpReferenceReaderHealthUrl = "http://127.0.0.1:8091/health/ready"
$ErpReferenceWriterHealthUrl = "http://127.0.0.1:8092/health/ready"
$ErpReferenceStartupPath = Join-Path $RuntimeRoot "erp-reference-enabled.json"
$ErpReferenceReaderMaxBodyBytes = 1048576
$ErpReferenceWriterMaxBodyBytes = 67108864
$MinimumPostgresConnectionsForErpReference = 120

function Assert-ErpReferenceRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "ERP 主数据服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=erp-reference"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-ErpReferencePortsFree([string]$Operation) {
  if (@(Get-PortListeners 8091).Count -gt 0 -or @(Get-PortListeners 8092).Count -gt 0) {
    throw "$Operation 前必须先停止 Django ERP 主数据 reader/writer"
  }
  if (Resolve-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django ERP 主数据 reader"
  }
  if (Resolve-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django ERP 主数据 writer"
  }
}

function Read-ErpReferenceCredentials {
  $payload = Read-JsonFile $ErpReferenceCredentialPath "Django ERP 主数据 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseErpReferenceReader", "databaseErpReferenceWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django ERP 主数据 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseErpReferenceReader) "databaseErpReferenceReader"
  $writer = Unprotect-Value ([string]$payload.databaseErpReferenceWriter) "databaseErpReferenceWriter"
  Assert-StrongSecret $reader "databaseErpReferenceReader"
  Assert-StrongSecret $writer "databaseErpReferenceWriter"
  return [pscustomobject]@{ ReaderPassword = $reader; WriterPassword = $writer }
}

function Configure-ErpReferenceCredentials {
  Assert-ErpReferenceRuntimeEntry
  Assert-ErpReferencePortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $ErpReferenceCredentialPath -PathType Leaf) {
    Read-ErpReferenceCredentials | Out-Null
    Write-Output "Django ERP 主数据 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $ErpReferenceCredentialPath ([ordered]@{
      version = 1
      databaseErpReferenceReader = Protect-Value $reader
      databaseErpReferenceWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "erp_reference_credentials_configured"
    Write-Output "Django ERP 主数据 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally { $reader = $null; $writer = $null }
}

function Provision-ErpReferenceRoles {
  Assert-ErpReferenceRuntimeEntry
  Assert-ErpReferencePortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置ERP 主数据角色" }
  $runtimeSecrets = Read-Secrets
  $erpSecrets = Read-ErpReferenceCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_ERP_REFERENCE_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_ERP_REFERENCE_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url "postgres" $superuser "teruisi_erp_reference_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_ERP_REFERENCE_READER_PASSWORD = $erpSecrets.ReaderPassword
    $env:TERUISI_PROVISION_ERP_REFERENCE_WRITER_PASSWORD = $erpSecrets.WriterPassword
    $code = @'
import os
import psycopg
from psycopg import sql

roles = {
    "teruisi_erp_reference_reader": os.environ["TERUISI_PROVISION_ERP_REFERENCE_READER_PASSWORD"],
    "teruisi_erp_reference_writer": os.environ["TERUISI_PROVISION_ERP_REFERENCE_WRITER_PASSWORD"],
}
reader_tables = (
    "sales_data_revisions", "erp_reference_write_authority",
    "erp_reference_import_batches_pg", "erp_product_master", "erp_combo_items",
    "erp_reference_import_scope_heads",
)
writer_privileges = {
    "sales_data_revisions": ("SELECT", "UPDATE"),
    "erp_reference_write_authority": ("SELECT",),
    "erp_reference_import_batches_pg": ("SELECT", "INSERT", "UPDATE"),
    "erp_product_master": ("SELECT", "INSERT", "DELETE"),
    "erp_combo_items": ("SELECT", "INSERT", "DELETE"),
    "erp_reference_import_scope_heads": ("SELECT", "UPDATE"),
    "erp_reference_import_fingerprints": ("SELECT", "INSERT"),
    "erp_reference_import_attempts": ("SELECT", "INSERT", "UPDATE"),
    "erp_reference_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "erp_reference_raw_upload_sessions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "erp_reference_raw_upload_chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}
connection = psycopg.connect(os.environ["TERUISI_PROVISION_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname='teruisi_erp_reference_sync'")
    if cursor.fetchone() is not None:
        cursor.execute("ALTER ROLE teruisi_erp_reference_sync NOLOGIN")
    for role, password in roles.items():
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        cursor.execute("SELECT parent.rolname FROM pg_auth_members membership JOIN pg_roles parent ON parent.oid=membership.roleid JOIN pg_roles member ON member.oid=membership.member WHERE member.rolname=%s", (role,))
        for (parent,) in cursor.fetchall():
            cursor.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(parent), sql.Identifier(role)))
        cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(connection.info.dbname), sql.Identifier(role)))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_erp_reference_reader").format(sql.SQL(",").join(sql.Identifier(table) for table in reader_tables)))
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_erp_reference_writer").format(sql.SQL(",").join(sql.SQL(item) for item in privileges), sql.Identifier(table)))
    for table in (
        "erp_combo_items", "erp_reference_import_fingerprints",
        "erp_reference_raw_upload_chunks",
    ):
        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", (f"public.{table}",))
        row = cursor.fetchone()
        if row and row[0]:
            schema, sequence = row[0].split(".", 1)
            cursor.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO teruisi_erp_reference_writer").format(sql.Identifier(schema), sql.Identifier(sequence)))
    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "erp_reference")
    cursor.execute("ALTER ROLE teruisi_erp_reference_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_erp_reference_writer RESET default_transaction_read_only")
    cursor.execute("GRANT SELECT (product_code,category,resolved_category) ON sales_order_lines TO teruisi_erp_reference_writer")
    cursor.execute("GRANT UPDATE (resolved_category) ON sales_order_lines TO teruisi_erp_reference_writer")
    cursor.execute("ALTER TABLE sales_data_revisions ENABLE ROW LEVEL SECURITY")
    cursor.execute("DROP POLICY IF EXISTS erp_reference_revision_reader ON sales_data_revisions")
    cursor.execute("DROP POLICY IF EXISTS erp_reference_revision_writer ON sales_data_revisions")
    cursor.execute("CREATE POLICY erp_reference_revision_reader ON sales_data_revisions FOR SELECT TO teruisi_erp_reference_reader,teruisi_erp_reference_writer USING (domain='erp')")
    cursor.execute("CREATE POLICY erp_reference_revision_writer ON sales_data_revisions FOR UPDATE TO teruisi_erp_reference_writer USING (domain='erp') WITH CHECK (domain='erp')")
    for role in roles:
        cursor.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (role,))
        flags = cursor.fetchone()
        if flags is None or any(flags): raise RuntimeError("erp-reference runtime role attributes are excessive")
        cursor.execute("SELECT has_schema_privilege(%s,'public','CREATE'),has_database_privilege(%s,current_database(),'CREATE')", (role, role))
        if any(cursor.fetchone()): raise RuntimeError("erp-reference runtime role retains DDL privileges")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "erp_reference_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog (Join-Path $LogDirectory "erp-reference-role-provision.$RunId.log") "erp_reference_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) { throw "配置ERP 主数据最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）" }
    Write-LauncherEvent "INFO" "erp_reference_database_roles_provisioned"
    Write-Output "Django ERP 主数据 reader/writer 最小权限角色已配置。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_ERP_REFERENCE_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_ERP_REFERENCE_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null; $erpSecrets = $null; $superuser = $null
  }
}

function Get-ErpReferenceWriteAuthority([object]$RuntimeSecrets, [object]$ErpSecrets) {
  $writerUrl = Database-Url "teruisi_erp_reference_writer" $ErpSecrets.WriterPassword "teruisi_erp_reference_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection
from erp_reference.models import ErpReferenceWriteAuthority
from sales.models import SalesDataRevision
authority = ErpReferenceWriteAuthority.objects.filter(id=1).first()
revision = SalesDataRevision.objects.filter(domain="erp").first()
with connection.cursor() as cursor:
    cursor.execute("SHOW max_connections")
    max_connections = int(cursor.fetchone()[0])
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
    "revision": int(revision.revision) if revision else -1,
    "maxConnections": max_connections,
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "erp_reference_authority_probe.py"
  try {
    $payload = Invoke-WithDjangoEnvironment $RuntimeSecrets $writerUrl "migration_writer" $false $ErpReferenceReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL ERP 主数据写入权威"
    }
  } finally { $writerUrl = $null }
  if (-not (Test-ExactObjectPropertyNames $payload @("status", "authorityEpoch", "cutoverId", "migrationRunId", "revision", "maxConnections"))) { throw "PostgreSQL ERP 主数据写入权威探针结构无效" }
  if ([int]$payload.maxConnections -lt $MinimumPostgresConnectionsForErpReference) {
    throw "PostgreSQL max_connections 低于完整 Django/BI 运行栈所需的 120；拒绝启动 ERP 主数据服务"
  }
  if ([string]$payload.status -cnotin @("d1", "postgres")) { throw "PostgreSQL ERP 主数据写入权威状态无效" }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^erp-reference-[0-9a-f]{32}$") -or
      [int]$payload.revision -lt 1)) { throw "PostgreSQL ERP 主数据写入权威证据不完整" }
  return $payload
}

function Start-ErpReferenceReader([object]$RuntimeSecrets, [object]$ErpSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8091", "--threads=6", "--connection-limit=60", "--channel-timeout=35",
    "--cleanup-interval=30", "--ident=teruisi-django-erp-reference-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$ErpReferenceReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-erp-reference-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "erp-reference-reader" $ErpReferenceReaderHealthUrl "127.0.0.1:8091"; return $false
  }
  if (@(Get-PortListeners 8091).Count -gt 0) { throw "端口 8091 被非本部署服务占用" }
  Remove-OldServiceLogs "django-erp-reference-reader"
  $readerUrl = Database-Url "teruisi_erp_reference_reader" $ErpSecrets.ReaderPassword "teruisi_erp_reference_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $readerUrl "erp_reference_reader" $true $ErpReferenceReaderMaxBodyBytes "" "" {
    Start-ManagedProcess "django-erp-reference-reader" $Waitress $arguments $BackendRoot $ErpReferenceReaderPidPath $fingerprint `
      (Join-Path $LogDirectory "django-erp-reference-reader.$RunId.stdout.log") (Join-Path $LogDirectory "django-erp-reference-reader.$RunId.stderr.log") | Out-Null
  }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try { Wait-DjangoReady "erp-reference-reader" $ErpReferenceReaderHealthUrl "127.0.0.1:8091"; return $true }
  catch { Stop-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress; throw }
}

function Start-ErpReferenceWriter([object]$RuntimeSecrets, [object]$ErpSecrets, [object]$Authority) {
  if ([string]$Authority.status -cne "postgres") { throw "PostgreSQL 尚未成为ERP 主数据唯一写入源；拒绝启动ERP 主数据 writer" }
  $arguments = @(
    "--listen=127.0.0.1:8092", "--threads=4", "--connection-limit=20", "--channel-timeout=960",
    "--cleanup-interval=30", "--ident=teruisi-django-erp-reference-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$ErpReferenceWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-erp-reference-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "erp-reference-writer" $ErpReferenceWriterHealthUrl "127.0.0.1:8092"; return $false
  }
  if (@(Get-PortListeners 8092).Count -gt 0) { throw "端口 8092 被非本部署服务占用" }
  Remove-OldServiceLogs "django-erp-reference-writer"
  $writerUrl = Database-Url "teruisi_erp_reference_writer" $ErpSecrets.WriterPassword "teruisi_erp_reference_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $writerUrl "erp_reference_writer" $false $ErpReferenceWriterMaxBodyBytes ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
    Start-ManagedProcess "django-erp-reference-writer" $Waitress $arguments $BackendRoot $ErpReferenceWriterPidPath $fingerprint `
      (Join-Path $LogDirectory "django-erp-reference-writer.$RunId.stdout.log") (Join-Path $LogDirectory "django-erp-reference-writer.$RunId.stderr.log") | Out-Null
  }
  $writerUrl = $null
  try { Wait-DjangoReady "erp-reference-writer" $ErpReferenceWriterHealthUrl "127.0.0.1:8092"; return $true }
  catch { Stop-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress; throw }
}

function Start-ErpReferenceStack([string]$LifecycleAclToken = "") {
  Assert-ErpReferenceRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动ERP 主数据服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets; $erpSecrets = Read-ErpReferenceCredentials
  $readerStarted = $false; $writerStarted = $false
  try {
    $authority = Get-ErpReferenceWriteAuthority $runtimeSecrets $erpSecrets
    if (Test-Path -LiteralPath $ErpReferenceStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $ErpReferenceStartupPath "Django ERP 主数据开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @("version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt")) -or
          [int]$startup.version -ne 1 -or [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django ERP 主数据开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-ErpReferenceReader $runtimeSecrets $erpSecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") { $writerStarted = Start-ErpReferenceWriter $runtimeSecrets $erpSecrets $authority }
    Wait-DjangoReady "erp-reference-reader" $ErpReferenceReaderHealthUrl "127.0.0.1:8091"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "erp-reference-writer" $ErpReferenceWriterHealthUrl "127.0.0.1:8092"
      Write-Output "Django ERP 主数据服务已就绪：reader=http://127.0.0.1:8091 writer=http://127.0.0.1:8092。"
    } else { Write-Output "Django ERP 主数据 reader 已就绪；PostgreSQL ERP 主数据写权尚未激活，writer 保持停止。" }
  } catch {
    $original = $_.Exception
    if ($writerStarted) { try { Stop-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress } catch {} }
    if ($readerStarted) { try { Stop-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress } catch {} }
    throw $original
  } finally { $runtimeSecrets = $null; $erpSecrets = $null }
}

function Stop-ErpReferenceStack([string]$LifecycleAclToken = "") {
  Assert-ErpReferenceRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress
  Stop-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress
  Write-Output "Django ERP 主数据 reader/writer 已停止；其他业务域、ERP 与 PostgreSQL 未改变。"
}

function Enable-ErpReferenceStartup {
  Assert-ErpReferenceRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用ERP 主数据开机启动" }
  $runtimeSecrets = Read-Secrets; $erpSecrets = Read-ErpReferenceCredentials
  try {
    $authority = Get-ErpReferenceWriteAuthority $runtimeSecrets $erpSecrets
    if ([string]$authority.status -cne "postgres") { throw "只有 PostgreSQL 已取得ERP 主数据唯一写权后才能启用ERP 主数据开机启动" }
    Start-ErpReferenceStack
    Write-AtomicJson $ErpReferenceStartupPath ([ordered]@{
      version = 1; authorityEpoch = [string]$authority.authorityEpoch; cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId; enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django ERP 主数据域已加入现有受控开机启动链。"
  } finally { $runtimeSecrets = $null; $erpSecrets = $null }
}

function Disable-ErpReferenceStartup {
  Assert-ErpReferenceRuntimeEntry
  if (Test-Path -LiteralPath $ErpReferenceStartupPath -PathType Leaf) { Remove-Item -LiteralPath $ErpReferenceStartupPath -Force }
  Write-Output "Django ERP 主数据域已退出开机启动链；当前运行进程未改变。"
}

function Show-ErpReferenceStatus {
  $reader = "stopped"; $writer = "stopped"
  try { if (Resolve-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress) { $reader = "running" } elseif (@(Get-PortListeners 8091).Count -gt 0) { $reader = "foreign_port_owner" } } catch { $reader = "ownership_error" }
  try { if (Resolve-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress) { $writer = "running" } elseif (@(Get-PortListeners 8092).Count -gt 0) { $writer = "foreign_port_owner" } } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"; $writerReady = "not_ready"
  try { if ((Invoke-WebRequest -UseBasicParsing -Uri $ErpReferenceReaderHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8091" }).StatusCode -eq 200) { $readerReady = "ready" } } catch {}
  try { if ((Invoke-WebRequest -UseBasicParsing -Uri $ErpReferenceWriterHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8092" }).StatusCode -eq 200) { $writerReady = "ready" } } catch {}
  $status = [pscustomobject][ordered]@{ ErpReferenceReader = $reader; ErpReferenceWriter = $writer; ReaderReadiness = $readerReady; WriterReadiness = $writerReady; CheckedAt = [DateTimeOffset]::UtcNow.ToString("o") }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) } else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-ErpReferenceCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-ErpReferenceRoles } }
    "Start" { Invoke-WithServiceMutex { Start-ErpReferenceStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-ErpReferenceStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-ErpReferenceStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-ErpReferenceStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-ErpReferenceStartup } }
  }
} catch { Write-LauncherEvent "ERROR" "erp_reference_action_failed" $_.Exception.Message; throw }
