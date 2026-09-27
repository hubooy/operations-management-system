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
if (-not (Test-Path -LiteralPath $BaseScript -PathType Leaf)) {
  throw "缺少 Django 本机服务基础控制器"
}
$PreviousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $BaseScript -RuntimeRoot $RuntimeRoot
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $PreviousLibraryOnly, "Process"
  )
}
$Action = $RequestedAction
$Json = [switch]$RequestedJson
$OrchestratedLifecycleAclToken = $RequestedOrchestratedLifecycleAclToken

$NetshopCredentialPath = Join-Path $RuntimeRoot "secrets\netshop-credentials.dpapi.json"
$NetshopReaderPidPath = Join-Path $RunDirectory "django-netshop-reader.pid.json"
$NetshopWriterPidPath = Join-Path $RunDirectory "django-netshop-writer.pid.json"
$NetshopReaderHealthUrl = "http://127.0.0.1:8021/health/ready"
$NetshopWriterHealthUrl = "http://127.0.0.1:8022/health/ready"
$NetshopStartupPath = Join-Path $RuntimeRoot "netshop-service-enabled.json"
$NetshopReaderMaxBodyBytes = 1048576
$NetshopWriterMaxBodyBytes = 134217728

function Assert-NetshopRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "网店服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=netshop"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-NetshopPortsFree([string]$Operation) {
  if (@(Get-PortListeners 8021).Count -gt 0 -or @(Get-PortListeners 8022).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 网店 reader/writer"
  }
  if (Resolve-OwnedProcess "django-netshop-reader" $NetshopReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 网店 reader"
  }
  if (Resolve-OwnedProcess "django-netshop-writer" $NetshopWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 网店 writer"
  }
}

function Read-NetshopCredentials {
  $payload = Read-JsonFile $NetshopCredentialPath "Django 网店 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseNetshopReader", "databaseNetshopWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 网店 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseNetshopReader) "databaseNetshopReader"
  $writer = Unprotect-Value ([string]$payload.databaseNetshopWriter) "databaseNetshopWriter"
  Assert-StrongSecret $reader "databaseNetshopReader"
  Assert-StrongSecret $writer "databaseNetshopWriter"
  return [pscustomobject]@{
    ReaderPassword = $reader
    WriterPassword = $writer
  }
}

function Configure-NetshopCredentials {
  Assert-NetshopRuntimeEntry
  Assert-NetshopPortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $NetshopCredentialPath -PathType Leaf) {
    Read-NetshopCredentials | Out-Null
    Write-Output "Django 网店 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $NetshopCredentialPath ([ordered]@{
      version = 1
      databaseNetshopReader = Protect-Value $reader
      databaseNetshopWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "netshop_credentials_configured"
    Write-Output "Django 网店 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally {
    $reader = $null
    $writer = $null
  }
}

function Provision-NetshopRoles {
  Assert-NetshopRuntimeEntry
  Assert-NetshopPortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置网店角色" }
  $runtimeSecrets = Read-Secrets
  $netshopSecrets = Read-NetshopCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_NETSHOP_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_NETSHOP_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url `
      "postgres" $superuser "teruisi_netshop_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_NETSHOP_READER_PASSWORD = $netshopSecrets.ReaderPassword
    $env:TERUISI_PROVISION_NETSHOP_WRITER_PASSWORD = $netshopSecrets.WriterPassword
    $code = @'
import os

import psycopg
from psycopg import sql

roles = {
    "teruisi_netshop_reader": os.environ["TERUISI_PROVISION_NETSHOP_READER_PASSWORD"],
    "teruisi_netshop_writer": os.environ["TERUISI_PROVISION_NETSHOP_WRITER_PASSWORD"],
}
reader_tables = (
    "netshop_import_batches", "netshop_rows", "netshop_promotion_product_daily",
    "netshop_promotion_shop_daily", "netshop_promotion_aggregate_state",
    "netshop_promotion_aggregate_manifest", "netshop_product_daily_revisions",
    "netshop_product_daily_scope_revisions", "netshop_promotion_scope_revisions",
    "netshop_data_revisions",
)
writer_privileges = {
    "netshop_import_batches": ("SELECT", "INSERT", "UPDATE"),
    "netshop_rows": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_promotion_product_daily": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_promotion_shop_daily": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_promotion_aggregate_state": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_promotion_aggregate_manifest": ("SELECT", "INSERT", "UPDATE"),
    "netshop_promotion_aggregate_control": ("SELECT", "INSERT", "UPDATE"),
    "netshop_product_daily_revisions": ("SELECT", "INSERT", "UPDATE"),
    "netshop_product_daily_scope_revisions": ("SELECT", "INSERT", "UPDATE"),
    "netshop_promotion_scope_revisions": ("SELECT", "INSERT", "UPDATE"),
    "netshop_data_revisions": ("SELECT", "INSERT", "UPDATE"),
    "netshop_import_scope_heads": ("SELECT", "INSERT", "UPDATE"),
    "netshop_import_attempts": ("SELECT", "INSERT", "UPDATE"),
    "netshop_import_fingerprints": ("SELECT", "INSERT"),
    "netshop_asset_uploads": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_asset_upload_chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_asset_upload_results": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "netshop_write_authority": ("SELECT",),
    "netshop_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}
auto_id_tables = (
    "netshop_rows", "netshop_promotion_product_daily", "netshop_promotion_shop_daily",
    "netshop_promotion_aggregate_state", "netshop_product_daily_scope_revisions",
    "netshop_promotion_scope_revisions", "netshop_import_fingerprints",
    "netshop_asset_upload_chunks",
)

connection = psycopg.connect(os.environ["TERUISI_PROVISION_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
    for role, password in roles.items():
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(role)))
        cursor.execute(sql.SQL(
            "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(role), sql.Literal(password)))
        cursor.execute(
            "SELECT parent.rolname FROM pg_auth_members membership "
            "JOIN pg_roles parent ON parent.oid=membership.roleid "
            "JOIN pg_roles member ON member.oid=membership.member "
            "WHERE member.rolname=%s",
            (role,),
        )
        for (parent,) in cursor.fetchall():
            cursor.execute(sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(parent), sql.Identifier(role)
            ))
        cursor.execute(sql.SQL(
            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}"
        ).format(sql.Identifier(role)))
        cursor.execute(sql.SQL(
            "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}"
        ).format(sql.Identifier(role)))
        cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(
            sql.Identifier(role)
        ))
        cursor.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
            sql.Identifier(connection.info.dbname), sql.Identifier(role)
        ))
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(connection.info.dbname), sql.Identifier(role)
        ))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
            sql.Identifier(role)
        ))

    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_netshop_reader").format(
        sql.SQL(",").join(sql.Identifier(name) for name in reader_tables)
    ))
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_netshop_writer").format(
            sql.SQL(",").join(sql.SQL(value) for value in privileges),
            sql.Identifier(table),
        ))
    for table in auto_id_tables:
        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", (f"public.{table}",))
        row = cursor.fetchone()
        if row and row[0]:
            schema, sequence = row[0].split(".", 1)
            cursor.execute(sql.SQL(
                "GRANT USAGE ON SEQUENCE {}.{} TO teruisi_netshop_writer"
            ).format(sql.Identifier(schema), sql.Identifier(sequence)))

    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "netshop")
    from netshop.analysis_permissions import grant_actor_read
    grant_actor_read(cursor)
    cursor.execute("ALTER ROLE teruisi_netshop_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_netshop_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute(
            "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname=%s",
            (role,),
        )
        flags = cursor.fetchone()
        if flags is None or any(flags):
            raise RuntimeError("netshop runtime role attributes are excessive")
        cursor.execute(
            "SELECT has_schema_privilege(%s,'public','CREATE'),"
            "has_database_privilege(%s,current_database(),'CREATE')",
            (role, role),
        )
        if any(cursor.fetchone()):
            raise RuntimeError("netshop runtime role retains DDL privileges")

    cursor.execute(
        "SELECT n.nspname,c.relname,"
        "has_table_privilege('teruisi_netshop_writer',c.oid,'INSERT'),"
        "has_table_privilege('teruisi_netshop_writer',c.oid,'UPDATE'),"
        "has_table_privilege('teruisi_netshop_writer',c.oid,'DELETE'),"
        "has_table_privilege('teruisi_netshop_writer',c.oid,'TRUNCATE'),"
        "has_any_column_privilege('teruisi_netshop_writer',c.oid,'INSERT'),"
        "has_any_column_privilege('teruisi_netshop_writer',c.oid,'UPDATE') "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname <> 'information_schema' "
        "AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'"
    )
    for schema, table, insert, update, delete, truncate, column_insert, column_update in cursor.fetchall():
        allowed = set(writer_privileges.get(table, ())) if schema == "public" else set()
        actual = {
            "INSERT": bool(insert) or bool(column_insert),
            "UPDATE": bool(update) or bool(column_update),
            "DELETE": bool(delete),
            "TRUNCATE": bool(truncate),
        }
        if any(granted and privilege not in allowed for privilege, granted in actual.items()):
            raise RuntimeError(f"netshop writer DML escaped allowlist: {schema}.{table}")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "netshop_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog `
      (Join-Path $LogDirectory "netshop-role-provision.$RunId.log") `
      "netshop_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) {
      throw "配置网店最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）"
    }
    Write-LauncherEvent "INFO" "netshop_database_roles_provisioned"
    Write-Output "Django 网店 reader/writer 最小权限角色已配置；其他域未改变。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_NETSHOP_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_NETSHOP_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null
    $netshopSecrets = $null
    $superuser = $null
  }
}

function Get-NetshopWriteAuthority([object]$RuntimeSecrets, [object]$NetshopSecrets) {
  $writerUrl = Database-Url `
    "teruisi_netshop_writer" $NetshopSecrets.WriterPassword `
    "teruisi_netshop_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from netshop.models import NetshopWriteAuthority

authority = NetshopWriteAuthority.objects.filter(id=1).first()
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "netshop_authority_probe.py"
  $payload = Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "migration_writer" $false $NetshopReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess `
        $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 网店写入权威"
    }
  $writerUrl = $null
  if ([string]$payload.status -cnotin @("d1", "postgres")) {
    throw "PostgreSQL 网店写入权威状态无效"
  }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^netshop-[0-9a-f]{24}$")
    )) {
    throw "PostgreSQL 网店写入权威证据不完整"
  }
  return $payload
}

function Start-NetshopReader([object]$RuntimeSecrets, [object]$NetshopSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8021", "--threads=6", "--connection-limit=60",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-netshop-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$NetshopReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-netshop-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-netshop-reader" $NetshopReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "netshop-reader" $NetshopReaderHealthUrl "127.0.0.1:8021"
    return $false
  }
  if (@(Get-PortListeners 8021).Count -gt 0) { throw "端口 8021 被非本部署服务占用" }
  Remove-OldServiceLogs "django-netshop-reader"
  $readerUrl = Database-Url `
    "teruisi_netshop_reader" $NetshopSecrets.ReaderPassword `
    "teruisi_django_netshop_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $readerUrl "netshop_reader" $true $NetshopReaderMaxBodyBytes "" "" {
      Start-ManagedProcess "django-netshop-reader" $Waitress $arguments $BackendRoot `
        $NetshopReaderPidPath $fingerprint `
        (Join-Path $LogDirectory "django-netshop-reader.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-netshop-reader.$RunId.stderr.log") | Out-Null
    }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "netshop-reader" $NetshopReaderHealthUrl "127.0.0.1:8021"
    return $true
  } catch {
    Stop-OwnedProcess "django-netshop-reader" $NetshopReaderPidPath $Waitress
    throw
  }
}

function Start-NetshopWriter(
  [object]$RuntimeSecrets,
  [object]$NetshopSecrets,
  [object]$Authority
) {
  if ([string]$Authority.status -cne "postgres") {
    throw "PostgreSQL 尚未成为网店唯一写入源；拒绝启动网店 writer"
  }
  $arguments = @(
    "--listen=127.0.0.1:8022", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-netshop-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$NetshopWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-netshop-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-netshop-writer" $NetshopWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "netshop-writer" $NetshopWriterHealthUrl "127.0.0.1:8022"
    return $false
  }
  if (@(Get-PortListeners 8022).Count -gt 0) { throw "端口 8022 被非本部署服务占用" }
  Remove-OldServiceLogs "django-netshop-writer"
  $writerUrl = Database-Url `
    "teruisi_netshop_writer" $NetshopSecrets.WriterPassword `
    "teruisi_django_netshop_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "netshop_writer" $false $NetshopWriterMaxBodyBytes `
    ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
      Start-ManagedProcess "django-netshop-writer" $Waitress $arguments $BackendRoot `
        $NetshopWriterPidPath $fingerprint `
        (Join-Path $LogDirectory "django-netshop-writer.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-netshop-writer.$RunId.stderr.log") | Out-Null
    }
  $writerUrl = $null
  try {
    Wait-DjangoReady "netshop-writer" $NetshopWriterHealthUrl "127.0.0.1:8022"
    return $true
  } catch {
    Stop-OwnedProcess "django-netshop-writer" $NetshopWriterPidPath $Waitress
    throw
  }
}

function Start-NetshopStack([string]$LifecycleAclToken = "") {
  Assert-NetshopRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动网店服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets
  $netshopSecrets = Read-NetshopCredentials
  $readerStarted = $false
  $writerStarted = $false
  try {
    $authority = Get-NetshopWriteAuthority $runtimeSecrets $netshopSecrets
    if (Test-Path -LiteralPath $NetshopStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $NetshopStartupPath "Django 网店开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @(
            "version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt"
          )) -or
          [int]$startup.version -ne 1 -or
          [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or
          [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django 网店开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-NetshopReader $runtimeSecrets $netshopSecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") {
      $writerStarted = Start-NetshopWriter $runtimeSecrets $netshopSecrets $authority
    }
    Wait-DjangoReady "netshop-reader" $NetshopReaderHealthUrl "127.0.0.1:8021"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "netshop-writer" $NetshopWriterHealthUrl "127.0.0.1:8022"
      Write-Output "Django 网店服务已就绪：reader=http://127.0.0.1:8021 writer=http://127.0.0.1:8022。"
    } else {
      Write-Output "Django 网店 reader 已就绪；PostgreSQL 网店写权尚未激活，writer 保持停止。"
    }
  } catch {
    $original = $_.Exception
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-netshop-writer" $NetshopWriterPidPath $Waitress } catch {}
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-netshop-reader" $NetshopReaderPidPath $Waitress } catch {}
    }
    throw $original
  } finally {
    $runtimeSecrets = $null
    $netshopSecrets = $null
  }
}

function Stop-NetshopStack([string]$LifecycleAclToken = "") {
  Assert-NetshopRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-netshop-writer" $NetshopWriterPidPath $Waitress
  Stop-OwnedProcess "django-netshop-reader" $NetshopReaderPidPath $Waitress
  Write-Output "Django 网店 reader/writer 已停止；销售、财务、ERP 与 PostgreSQL 未改变。"
}

function Enable-NetshopStartup {
  Assert-NetshopRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用网店开机启动" }
  $runtimeSecrets = Read-Secrets
  $netshopSecrets = Read-NetshopCredentials
  try {
    $authority = Get-NetshopWriteAuthority $runtimeSecrets $netshopSecrets
    if ([string]$authority.status -cne "postgres") {
      throw "只有 PostgreSQL 已取得网店唯一写权后才能启用网店开机启动"
    }
    Start-NetshopStack
    Write-AtomicJson $NetshopStartupPath ([ordered]@{
      version = 1
      authorityEpoch = [string]$authority.authorityEpoch
      cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId
      enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 网店域已加入现有受控开机启动链。"
  } finally {
    $runtimeSecrets = $null
    $netshopSecrets = $null
  }
}

function Disable-NetshopStartup {
  Assert-NetshopRuntimeEntry
  if (Test-Path -LiteralPath $NetshopStartupPath -PathType Leaf) {
    Remove-Item -LiteralPath $NetshopStartupPath -Force
  }
  Write-Output "Django 网店域已退出开机启动链；当前运行进程未改变。"
}

function Show-NetshopStatus {
  $reader = "stopped"
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-netshop-reader" $NetshopReaderPidPath $Waitress) {
      $reader = "running"
    } elseif (@(Get-PortListeners 8021).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  try {
    if (Resolve-OwnedProcess "django-netshop-writer" $NetshopWriterPidPath $Waitress) {
      $writer = "running"
    } elseif (@(Get-PortListeners 8022).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  $writerReady = "not_ready"
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $NetshopReaderHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8021" }).StatusCode -eq 200) {
      $readerReady = "ready"
    }
  } catch {}
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $NetshopWriterHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8022" }).StatusCode -eq 200) {
      $writerReady = "ready"
    }
  } catch {}
  $status = [pscustomobject][ordered]@{
    NetshopReader = $reader
    NetshopWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) }
  else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-NetshopCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-NetshopRoles } }
    "Start" { Invoke-WithServiceMutex { Start-NetshopStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-NetshopStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-NetshopStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-NetshopStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-NetshopStartup } }
  }
} catch {
  Write-LauncherEvent "ERROR" "netshop_action_failed" $_.Exception.Message
  throw
}
