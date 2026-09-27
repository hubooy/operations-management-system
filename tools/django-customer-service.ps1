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

$CustomerServiceCredentialPath = Join-Path $RuntimeRoot "secrets\customer-service-credentials.dpapi.json"
$CustomerServiceReaderPidPath = Join-Path $RunDirectory "django-customer-service-reader.pid.json"
$CustomerServiceWriterPidPath = Join-Path $RunDirectory "django-customer-service-writer.pid.json"
$CustomerServiceReaderHealthUrl = "http://127.0.0.1:8071/health/ready"
$CustomerServiceWriterHealthUrl = "http://127.0.0.1:8072/health/ready"
$CustomerServiceStartupPath = Join-Path $RuntimeRoot "customer-service-enabled.json"
$CustomerServiceReaderMaxBodyBytes = 1048576
$CustomerServiceWriterMaxBodyBytes = 33554432

function Assert-CustomerServiceRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "客服服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=customer-service"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-CustomerServicePortsFree([string]$Operation) {
  if (@(Get-PortListeners 8071).Count -gt 0 -or @(Get-PortListeners 8072).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 客服 reader/writer"
  }
  if (Resolve-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 客服 reader"
  }
  if (Resolve-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 客服 writer"
  }
}

function Read-CustomerServiceCredentials {
  $payload = Read-JsonFile $CustomerServiceCredentialPath "Django 客服 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseCustomerServiceReader", "databaseCustomerServiceWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 客服 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseCustomerServiceReader) "databaseCustomerServiceReader"
  $writer = Unprotect-Value ([string]$payload.databaseCustomerServiceWriter) "databaseCustomerServiceWriter"
  Assert-StrongSecret $reader "databaseCustomerServiceReader"
  Assert-StrongSecret $writer "databaseCustomerServiceWriter"
  return [pscustomobject]@{ ReaderPassword = $reader; WriterPassword = $writer }
}

function Configure-CustomerServiceCredentials {
  Assert-CustomerServiceRuntimeEntry
  Assert-CustomerServicePortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $CustomerServiceCredentialPath -PathType Leaf) {
    Read-CustomerServiceCredentials | Out-Null
    Write-Output "Django 客服 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $CustomerServiceCredentialPath ([ordered]@{
      version = 1
      databaseCustomerServiceReader = Protect-Value $reader
      databaseCustomerServiceWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "customer_service_credentials_configured"
    Write-Output "Django 客服 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally { $reader = $null; $writer = $null }
}

function Provision-CustomerServiceRoles {
  Assert-CustomerServiceRuntimeEntry
  Assert-CustomerServicePortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置客服角色" }
  $runtimeSecrets = Read-Secrets
  $customerSecrets = Read-CustomerServiceCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_CUSTOMER_SERVICE_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_CUSTOMER_SERVICE_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url "postgres" $superuser "teruisi_customer_service_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_CUSTOMER_SERVICE_READER_PASSWORD = $customerSecrets.ReaderPassword
    $env:TERUISI_PROVISION_CUSTOMER_SERVICE_WRITER_PASSWORD = $customerSecrets.WriterPassword
    $code = @'
import os
import psycopg
from psycopg import sql

roles = {
    "teruisi_customer_service_reader": os.environ["TERUISI_PROVISION_CUSTOMER_SERVICE_READER_PASSWORD"],
    "teruisi_customer_service_writer": os.environ["TERUISI_PROVISION_CUSTOMER_SERVICE_WRITER_PASSWORD"],
}
tables = (
    "customer_service_data_revisions", "customer_service_write_authority",
    "customer_service_import_batches", "customer_service_conversations",
    "customer_service_deletion_audits", "customer_service_import_scope_heads",
    "customer_service_import_fingerprints", "customer_service_import_attempts",
    "customer_service_write_request_receipts", "customer_service_migration_runs",
    "customer_service_raw_upload_sessions", "customer_service_raw_upload_chunks",
)
writer_privileges = {
    "customer_service_data_revisions": ("SELECT", "UPDATE"),
    "customer_service_write_authority": ("SELECT",),
    "customer_service_import_batches": ("SELECT", "INSERT", "UPDATE"),
    "customer_service_conversations": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "customer_service_deletion_audits": ("SELECT", "INSERT"),
    "customer_service_import_scope_heads": ("SELECT", "INSERT", "UPDATE"),
    "customer_service_import_fingerprints": ("SELECT", "INSERT"),
    "customer_service_import_attempts": ("SELECT", "INSERT", "UPDATE"),
    "customer_service_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "customer_service_raw_upload_sessions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "customer_service_raw_upload_chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
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
    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_customer_service_reader").format(sql.SQL(",").join(sql.Identifier(table) for table in tables)))
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_customer_service_writer").format(sql.SQL(",").join(sql.SQL(item) for item in privileges), sql.Identifier(table)))
    for table in (
        "customer_service_conversations", "customer_service_import_fingerprints",
        "customer_service_raw_upload_chunks",
    ):
        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", (f"public.{table}",))
        row = cursor.fetchone()
        if row and row[0]:
            schema, sequence = row[0].split(".", 1)
            cursor.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO teruisi_customer_service_writer").format(sql.Identifier(schema), sql.Identifier(sequence)))
    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "customer_service")
    cursor.execute("ALTER ROLE teruisi_customer_service_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_customer_service_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (role,))
        flags = cursor.fetchone()
        if flags is None or any(flags): raise RuntimeError("customer-service runtime role attributes are excessive")
        cursor.execute("SELECT has_schema_privilege(%s,'public','CREATE'),has_database_privilege(%s,current_database(),'CREATE')", (role, role))
        if any(cursor.fetchone()): raise RuntimeError("customer-service runtime role retains DDL privileges")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "customer_service_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog (Join-Path $LogDirectory "customer-service-role-provision.$RunId.log") "customer_service_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) { throw "配置客服最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）" }
    Write-LauncherEvent "INFO" "customer_service_database_roles_provisioned"
    Write-Output "Django 客服 reader/writer 最小权限角色已配置。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_CUSTOMER_SERVICE_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_CUSTOMER_SERVICE_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null; $customerSecrets = $null; $superuser = $null
  }
}

function Get-CustomerServiceWriteAuthority([object]$RuntimeSecrets, [object]$CustomerSecrets) {
  $writerUrl = Database-Url "teruisi_customer_service_writer" $CustomerSecrets.WriterPassword "teruisi_customer_service_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection
from customer_service.models import CustomerServiceDataRevision, CustomerServiceWriteAuthority
authority = CustomerServiceWriteAuthority.objects.filter(id=1).first()
revision = CustomerServiceDataRevision.objects.filter(domain="customer-service").first()
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
    "revision": int(revision.revision) if revision else -1,
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "customer_service_authority_probe.py"
  try {
    $payload = Invoke-WithDjangoEnvironment $RuntimeSecrets $writerUrl "migration_writer" $false $CustomerServiceReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 客服写入权威"
    }
  } finally { $writerUrl = $null }
  if (-not (Test-ExactObjectPropertyNames $payload @("status", "authorityEpoch", "cutoverId", "migrationRunId", "revision"))) { throw "PostgreSQL 客服写入权威探针结构无效" }
  if ([string]$payload.status -cnotin @("d1", "postgres")) { throw "PostgreSQL 客服写入权威状态无效" }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^customer-service-[0-9a-f]{32}$") -or
      [int]$payload.revision -lt 1)) { throw "PostgreSQL 客服写入权威证据不完整" }
  return $payload
}

function Start-CustomerServiceReader([object]$RuntimeSecrets, [object]$CustomerSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8071", "--threads=6", "--connection-limit=60", "--channel-timeout=35",
    "--cleanup-interval=30", "--ident=teruisi-django-customer-service-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$CustomerServiceReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-customer-service-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "customer-service-reader" $CustomerServiceReaderHealthUrl "127.0.0.1:8071"; return $false
  }
  if (@(Get-PortListeners 8071).Count -gt 0) { throw "端口 8071 被非本部署服务占用" }
  Remove-OldServiceLogs "django-customer-service-reader"
  $readerUrl = Database-Url "teruisi_customer_service_reader" $CustomerSecrets.ReaderPassword "teruisi_customer_service_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $readerUrl "customer_service_reader" $true $CustomerServiceReaderMaxBodyBytes "" "" {
    Start-ManagedProcess "django-customer-service-reader" $Waitress $arguments $BackendRoot $CustomerServiceReaderPidPath $fingerprint `
      (Join-Path $LogDirectory "django-customer-service-reader.$RunId.stdout.log") (Join-Path $LogDirectory "django-customer-service-reader.$RunId.stderr.log") | Out-Null
  }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try { Wait-DjangoReady "customer-service-reader" $CustomerServiceReaderHealthUrl "127.0.0.1:8071"; return $true }
  catch { Stop-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress; throw }
}

function Start-CustomerServiceWriter([object]$RuntimeSecrets, [object]$CustomerSecrets, [object]$Authority) {
  if ([string]$Authority.status -cne "postgres") { throw "PostgreSQL 尚未成为客服唯一写入源；拒绝启动客服 writer" }
  $arguments = @(
    "--listen=127.0.0.1:8072", "--threads=4", "--connection-limit=20", "--channel-timeout=960",
    "--cleanup-interval=30", "--ident=teruisi-django-customer-service-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$CustomerServiceWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-customer-service-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "customer-service-writer" $CustomerServiceWriterHealthUrl "127.0.0.1:8072"; return $false
  }
  if (@(Get-PortListeners 8072).Count -gt 0) { throw "端口 8072 被非本部署服务占用" }
  Remove-OldServiceLogs "django-customer-service-writer"
  $writerUrl = Database-Url "teruisi_customer_service_writer" $CustomerSecrets.WriterPassword "teruisi_customer_service_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment $RuntimeSecrets $writerUrl "customer_service_writer" $false $CustomerServiceWriterMaxBodyBytes ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
    Start-ManagedProcess "django-customer-service-writer" $Waitress $arguments $BackendRoot $CustomerServiceWriterPidPath $fingerprint `
      (Join-Path $LogDirectory "django-customer-service-writer.$RunId.stdout.log") (Join-Path $LogDirectory "django-customer-service-writer.$RunId.stderr.log") | Out-Null
  }
  $writerUrl = $null
  try { Wait-DjangoReady "customer-service-writer" $CustomerServiceWriterHealthUrl "127.0.0.1:8072"; return $true }
  catch { Stop-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress; throw }
}

function Start-CustomerServiceStack([string]$LifecycleAclToken = "") {
  Assert-CustomerServiceRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动客服服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets; $customerSecrets = Read-CustomerServiceCredentials
  $readerStarted = $false; $writerStarted = $false
  try {
    $authority = Get-CustomerServiceWriteAuthority $runtimeSecrets $customerSecrets
    if (Test-Path -LiteralPath $CustomerServiceStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $CustomerServiceStartupPath "Django 客服开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @("version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt")) -or
          [int]$startup.version -ne 1 -or [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django 客服开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-CustomerServiceReader $runtimeSecrets $customerSecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") { $writerStarted = Start-CustomerServiceWriter $runtimeSecrets $customerSecrets $authority }
    Wait-DjangoReady "customer-service-reader" $CustomerServiceReaderHealthUrl "127.0.0.1:8071"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "customer-service-writer" $CustomerServiceWriterHealthUrl "127.0.0.1:8072"
      Write-Output "Django 客服服务已就绪：reader=http://127.0.0.1:8071 writer=http://127.0.0.1:8072。"
    } else { Write-Output "Django 客服 reader 已就绪；PostgreSQL 客服写权尚未激活，writer 保持停止。" }
  } catch {
    $original = $_.Exception
    if ($writerStarted) { try { Stop-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress } catch {} }
    if ($readerStarted) { try { Stop-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress } catch {} }
    throw $original
  } finally { $runtimeSecrets = $null; $customerSecrets = $null }
}

function Stop-CustomerServiceStack([string]$LifecycleAclToken = "") {
  Assert-CustomerServiceRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress
  Stop-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress
  Write-Output "Django 客服 reader/writer 已停止；其他业务域、ERP 与 PostgreSQL 未改变。"
}

function Enable-CustomerServiceStartup {
  Assert-CustomerServiceRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用客服开机启动" }
  $runtimeSecrets = Read-Secrets; $customerSecrets = Read-CustomerServiceCredentials
  try {
    $authority = Get-CustomerServiceWriteAuthority $runtimeSecrets $customerSecrets
    if ([string]$authority.status -cne "postgres") { throw "只有 PostgreSQL 已取得客服唯一写权后才能启用客服开机启动" }
    Start-CustomerServiceStack
    Write-AtomicJson $CustomerServiceStartupPath ([ordered]@{
      version = 1; authorityEpoch = [string]$authority.authorityEpoch; cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId; enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 客服域已加入现有受控开机启动链。"
  } finally { $runtimeSecrets = $null; $customerSecrets = $null }
}

function Disable-CustomerServiceStartup {
  Assert-CustomerServiceRuntimeEntry
  if (Test-Path -LiteralPath $CustomerServiceStartupPath -PathType Leaf) { Remove-Item -LiteralPath $CustomerServiceStartupPath -Force }
  Write-Output "Django 客服域已退出开机启动链；当前运行进程未改变。"
}

function Show-CustomerServiceStatus {
  $reader = "stopped"; $writer = "stopped"
  try { if (Resolve-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress) { $reader = "running" } elseif (@(Get-PortListeners 8071).Count -gt 0) { $reader = "foreign_port_owner" } } catch { $reader = "ownership_error" }
  try { if (Resolve-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress) { $writer = "running" } elseif (@(Get-PortListeners 8072).Count -gt 0) { $writer = "foreign_port_owner" } } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"; $writerReady = "not_ready"
  try { if ((Invoke-WebRequest -UseBasicParsing -Uri $CustomerServiceReaderHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8071" }).StatusCode -eq 200) { $readerReady = "ready" } } catch {}
  try { if ((Invoke-WebRequest -UseBasicParsing -Uri $CustomerServiceWriterHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8072" }).StatusCode -eq 200) { $writerReady = "ready" } } catch {}
  $status = [pscustomobject][ordered]@{ CustomerServiceReader = $reader; CustomerServiceWriter = $writer; ReaderReadiness = $readerReady; WriterReadiness = $writerReady; CheckedAt = [DateTimeOffset]::UtcNow.ToString("o") }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) } else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-CustomerServiceCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-CustomerServiceRoles } }
    "Start" { Invoke-WithServiceMutex { Start-CustomerServiceStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-CustomerServiceStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-CustomerServiceStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-CustomerServiceStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-CustomerServiceStartup } }
  }
} catch { Write-LauncherEvent "ERROR" "customer_service_action_failed" $_.Exception.Message; throw }
