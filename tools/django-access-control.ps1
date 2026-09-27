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

$AccessControlCredentialPath = Join-Path $RuntimeRoot "secrets\access-control-credentials.dpapi.json"
$AccessControlReaderPidPath = Join-Path $RunDirectory "django-access-control-reader.pid.json"
$AccessControlWriterPidPath = Join-Path $RunDirectory "django-access-control-writer.pid.json"
$AccessControlReaderHealthUrl = "http://127.0.0.1:8101/health/ready"
$AccessControlWriterHealthUrl = "http://127.0.0.1:8102/health/ready"
$AccessControlStartupPath = Join-Path $RuntimeRoot "access-control-enabled.json"
$AccessControlReaderMaxBodyBytes = 1048576
$AccessControlWriterMaxBodyBytes = 1048576

function Assert-AccessControlRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "权限服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=access-control"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-AccessControlPortsFree([string]$Operation) {
  if (@(Get-PortListeners 8101).Count -gt 0 -or @(Get-PortListeners 8102).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 权限 reader/writer"
  }
  if (Resolve-OwnedProcess "django-access-control-reader" $AccessControlReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 权限 reader"
  }
  if (Resolve-OwnedProcess "django-access-control-writer" $AccessControlWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 权限 writer"
  }
}

function Read-AccessControlCredentials {
  $payload = Read-JsonFile $AccessControlCredentialPath "Django 权限 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseAccessControlReader", "databaseAccessControlWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 权限 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseAccessControlReader) "databaseAccessControlReader"
  $writer = Unprotect-Value ([string]$payload.databaseAccessControlWriter) "databaseAccessControlWriter"
  Assert-StrongSecret $reader "databaseAccessControlReader"
  Assert-StrongSecret $writer "databaseAccessControlWriter"
  return [pscustomobject]@{ ReaderPassword = $reader; WriterPassword = $writer }
}

function Configure-AccessControlCredentials {
  Assert-AccessControlRuntimeEntry
  Assert-AccessControlPortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $AccessControlCredentialPath -PathType Leaf) {
    Read-AccessControlCredentials | Out-Null
    Write-Output "Django 权限 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $AccessControlCredentialPath ([ordered]@{
      version = 1
      databaseAccessControlReader = Protect-Value $reader
      databaseAccessControlWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "access_control_credentials_configured"
    Write-Output "Django 权限 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally { $reader = $null; $writer = $null }
}

function Provision-AccessControlRoles {
  Assert-AccessControlRuntimeEntry
  Assert-AccessControlPortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置权限角色" }
  $runtimeSecrets = Read-Secrets
  $accessControlSecrets = Read-AccessControlCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_ACCESS_CONTROL_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_ACCESS_CONTROL_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url "postgres" $superuser "teruisi_access_control_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_ACCESS_CONTROL_READER_PASSWORD = $accessControlSecrets.ReaderPassword
    $env:TERUISI_PROVISION_ACCESS_CONTROL_WRITER_PASSWORD = $accessControlSecrets.WriterPassword
    $code = @'
import os
import psycopg
from psycopg import sql

roles = {
    "teruisi_access_control_reader": os.environ["TERUISI_PROVISION_ACCESS_CONTROL_READER_PASSWORD"],
    "teruisi_access_control_writer": os.environ["TERUISI_PROVISION_ACCESS_CONTROL_WRITER_PASSWORD"],
}
reader_tables = (
    "access_control_roles", "access_control_users",
    "access_control_permission_audits", "access_control_data_revisions",
)
writer_privileges = {
    "access_control_roles": ("SELECT",),
    "access_control_users": ("SELECT", "INSERT", "UPDATE"),
    "access_control_permission_audits": ("SELECT", "INSERT"),
    "access_control_data_revisions": ("SELECT", "UPDATE"),
    "access_control_write_authority": ("SELECT",),
    "access_control_write_request_receipts": ("SELECT", "INSERT", "UPDATE"),
}
connection = psycopg.connect(os.environ["TERUISI_PROVISION_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
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
    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_access_control_reader").format(sql.SQL(",").join(sql.Identifier(table) for table in reader_tables)))
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_access_control_writer").format(sql.SQL(",").join(sql.SQL(item) for item in privileges), sql.Identifier(table)))
    cursor.execute("SELECT pg_get_serial_sequence('public.access_control_permission_audits','sequence')")
    row = cursor.fetchone()
    if not row or not row[0]: raise RuntimeError("access-control audit sequence is missing")
    schema, sequence = row[0].split(".", 1)
    cursor.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO teruisi_access_control_writer").format(sql.Identifier(schema), sql.Identifier(sequence)))
    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "access_control")
    cursor.execute("ALTER ROLE teruisi_access_control_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_access_control_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (role,))
        flags = cursor.fetchone()
        if flags is None or any(flags): raise RuntimeError("access-control runtime role attributes are excessive")
        cursor.execute("SELECT has_schema_privilege(%s,'public','CREATE'),has_database_privilege(%s,current_database(),'CREATE')", (role, role))
        if any(cursor.fetchone()): raise RuntimeError("access-control runtime role retains DDL privileges")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "access_control_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog (Join-Path $LogDirectory "access-control-role-provision.$RunId.log") "access_control_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) { throw "配置权限最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）" }
    Write-LauncherEvent "INFO" "access_control_database_roles_provisioned"
    Write-Output "Django 权限 reader/writer 最小权限角色已配置。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_ACCESS_CONTROL_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_ACCESS_CONTROL_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null; $accessControlSecrets = $null; $superuser = $null
  }
}

function Get-AccessControlWriteAuthority([object]$RuntimeSecrets, [object]$AccessControlSecrets) {
  $writerUrl = Database-Url "teruisi_access_control_writer" $AccessControlSecrets.WriterPassword "teruisi_access_control_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection
from access_control.models import AccessControlDataRevision, AccessControlWriteAuthority
authority = AccessControlWriteAuthority.objects.filter(id=1).first()
revision = AccessControlDataRevision.objects.filter(domain="access-control").first()
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
    "revision": int(revision.revision) if revision else -1,
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "access_control_authority_probe.py"
  try {
    $payload = Invoke-WithDjangoEnvironment $RuntimeSecrets $writerUrl "migration_writer" $false $AccessControlReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 权限写入权威"
    }
  } finally { $writerUrl = $null }
  if (-not (Test-ExactObjectPropertyNames $payload @("status", "authorityEpoch", "cutoverId", "migrationRunId", "revision"))) { throw "PostgreSQL 权限写入权威探针结构无效" }
  if ([string]$payload.status -cnotin @("d1", "postgres")) { throw "PostgreSQL 权限写入权威状态无效" }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^access-control-[0-9a-f]{32}$") -or
      [int]$payload.revision -lt 1)) { throw "PostgreSQL 权限写入权威证据不完整" }
  return $payload
}

function Start-AccessControlReader([object]$RuntimeSecrets, [object]$AccessControlSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8101", "--threads=6", "--connection-limit=60", "--channel-timeout=35",
    "--cleanup-interval=30", "--ident=teruisi-django-access-control-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$AccessControlReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-access-control-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-access-control-reader" $AccessControlReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "access-control-reader" $AccessControlReaderHealthUrl "127.0.0.1:8101"; return $false
  }
  if (@(Get-PortListeners 8101).Count -gt 0) { throw "端口 8101 被非本部署服务占用" }
  Remove-OldServiceLogs "django-access-control-reader"
  $readerUrl = Database-Url "teruisi_access_control_reader" $AccessControlSecrets.ReaderPassword "teruisi_access_control_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $readerUrl "access_control_reader" $true $AccessControlReaderMaxBodyBytes "" "" {
    Start-ManagedProcess "django-access-control-reader" $Waitress $arguments $BackendRoot $AccessControlReaderPidPath $fingerprint `
      (Join-Path $LogDirectory "django-access-control-reader.$RunId.stdout.log") (Join-Path $LogDirectory "django-access-control-reader.$RunId.stderr.log") | Out-Null
  }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try { Wait-DjangoReady "access-control-reader" $AccessControlReaderHealthUrl "127.0.0.1:8101"; return $true }
  catch { Stop-OwnedProcess "django-access-control-reader" $AccessControlReaderPidPath $Waitress; throw }
}

function Start-AccessControlWriter([object]$RuntimeSecrets, [object]$AccessControlSecrets, [object]$Authority) {
  if ([string]$Authority.status -cne "postgres") { throw "PostgreSQL 尚未成为权限唯一写入源；拒绝启动权限 writer" }
  $arguments = @(
    "--listen=127.0.0.1:8102", "--threads=4", "--connection-limit=20", "--channel-timeout=960",
    "--cleanup-interval=30", "--ident=teruisi-django-access-control-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$AccessControlWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-access-control-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-access-control-writer" $AccessControlWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "access-control-writer" $AccessControlWriterHealthUrl "127.0.0.1:8102"; return $false
  }
  if (@(Get-PortListeners 8102).Count -gt 0) { throw "端口 8102 被非本部署服务占用" }
  Remove-OldServiceLogs "django-access-control-writer"
  $writerUrl = Database-Url "teruisi_access_control_writer" $AccessControlSecrets.WriterPassword "teruisi_access_control_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $writerUrl "access_control_writer" $false $AccessControlWriterMaxBodyBytes ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
    Start-ManagedProcess "django-access-control-writer" $Waitress $arguments $BackendRoot $AccessControlWriterPidPath $fingerprint `
      (Join-Path $LogDirectory "django-access-control-writer.$RunId.stdout.log") (Join-Path $LogDirectory "django-access-control-writer.$RunId.stderr.log") | Out-Null
  }
  $writerUrl = $null
  try { Wait-DjangoReady "access-control-writer" $AccessControlWriterHealthUrl "127.0.0.1:8102"; return $true }
  catch { Stop-OwnedProcess "django-access-control-writer" $AccessControlWriterPidPath $Waitress; throw }
}

function Start-AccessControlStack([string]$LifecycleAclToken = "") {
  Assert-AccessControlRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动权限服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets; $accessControlSecrets = Read-AccessControlCredentials
  $readerStarted = $false; $writerStarted = $false
  try {
    $authority = Get-AccessControlWriteAuthority $runtimeSecrets $accessControlSecrets
    if (Test-Path -LiteralPath $AccessControlStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $AccessControlStartupPath "Django 权限开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @("version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt")) -or
          [int]$startup.version -ne 1 -or [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django 权限开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-AccessControlReader $runtimeSecrets $accessControlSecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") { $writerStarted = Start-AccessControlWriter $runtimeSecrets $accessControlSecrets $authority }
    Wait-DjangoReady "access-control-reader" $AccessControlReaderHealthUrl "127.0.0.1:8101"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "access-control-writer" $AccessControlWriterHealthUrl "127.0.0.1:8102"
      Write-Output "Django 权限服务已就绪：reader=http://127.0.0.1:8101 writer=http://127.0.0.1:8102。"
    } else { Write-Output "Django 权限 reader 已就绪；PostgreSQL 权限写权尚未激活，writer 保持停止。" }
  } catch {
    $original = $_.Exception
    if ($writerStarted) { try { Stop-OwnedProcess "django-access-control-writer" $AccessControlWriterPidPath $Waitress } catch {} }
    if ($readerStarted) { try { Stop-OwnedProcess "django-access-control-reader" $AccessControlReaderPidPath $Waitress } catch {} }
    throw $original
  } finally { $runtimeSecrets = $null; $accessControlSecrets = $null }
}

function Stop-AccessControlStack([string]$LifecycleAclToken = "") {
  Assert-AccessControlRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-access-control-writer" $AccessControlWriterPidPath $Waitress
  Stop-OwnedProcess "django-access-control-reader" $AccessControlReaderPidPath $Waitress
  Write-Output "Django 权限 reader/writer 已停止；其他业务域、ERP 与 PostgreSQL 未改变。"
}

function Enable-AccessControlStartup {
  Assert-AccessControlRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用权限开机启动" }
  $runtimeSecrets = Read-Secrets; $accessControlSecrets = Read-AccessControlCredentials
  try {
    $authority = Get-AccessControlWriteAuthority $runtimeSecrets $accessControlSecrets
    if ([string]$authority.status -cne "postgres") { throw "只有 PostgreSQL 已取得权限唯一写权后才能启用权限开机启动" }
    Start-AccessControlStack
    Write-AtomicJson $AccessControlStartupPath ([ordered]@{
      version = 1; authorityEpoch = [string]$authority.authorityEpoch; cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId; enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 权限域已加入现有受控开机启动链。"
  } finally { $runtimeSecrets = $null; $accessControlSecrets = $null }
}

function Disable-AccessControlStartup {
  Assert-AccessControlRuntimeEntry
  if (Test-Path -LiteralPath $AccessControlStartupPath -PathType Leaf) { Remove-Item -LiteralPath $AccessControlStartupPath -Force }
  Write-Output "Django 权限域已退出开机启动链；当前运行进程未改变。"
}

function Show-AccessControlStatus {
  $reader = "stopped"; $writer = "stopped"
  try { if (Resolve-OwnedProcess "django-access-control-reader" $AccessControlReaderPidPath $Waitress) { $reader = "running" } elseif (@(Get-PortListeners 8101).Count -gt 0) { $reader = "foreign_port_owner" } } catch { $reader = "ownership_error" }
  try { if (Resolve-OwnedProcess "django-access-control-writer" $AccessControlWriterPidPath $Waitress) { $writer = "running" } elseif (@(Get-PortListeners 8102).Count -gt 0) { $writer = "foreign_port_owner" } } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"; $writerReady = "not_ready"
  try { if ((Invoke-WebRequest -UseBasicParsing -Uri $AccessControlReaderHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8101" }).StatusCode -eq 200) { $readerReady = "ready" } } catch {}
  try { if ((Invoke-WebRequest -UseBasicParsing -Uri $AccessControlWriterHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8102" }).StatusCode -eq 200) { $writerReady = "ready" } } catch {}
  $status = [pscustomobject][ordered]@{ AccessControlReader = $reader; AccessControlWriter = $writer; ReaderReadiness = $readerReady; WriterReadiness = $writerReady; CheckedAt = [DateTimeOffset]::UtcNow.ToString("o") }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) } else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-AccessControlCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-AccessControlRoles } }
    "Start" { Invoke-WithServiceMutex { Start-AccessControlStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-AccessControlStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-AccessControlStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-AccessControlStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-AccessControlStartup } }
  }
} catch { Write-LauncherEvent "ERROR" "access_control_action_failed" $_.Exception.Message; throw }
