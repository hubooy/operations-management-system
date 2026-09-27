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

$MarketCredentialPath = Join-Path $RuntimeRoot "secrets\market-credentials.dpapi.json"
$MarketReaderPidPath = Join-Path $RunDirectory "django-market-reader.pid.json"
$MarketWriterPidPath = Join-Path $RunDirectory "django-market-writer.pid.json"
$MarketReaderHealthUrl = "http://127.0.0.1:8031/health/ready"
$MarketWriterHealthUrl = "http://127.0.0.1:8032/health/ready"
$MarketStartupPath = Join-Path $RuntimeRoot "market-service-enabled.json"
$MarketReaderMaxBodyBytes = 1048576
$MarketWriterMaxBodyBytes = 134217728

function Assert-MarketRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "市场服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=market"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-MarketPortsFree([string]$Operation) {
  if (@(Get-PortListeners 8031).Count -gt 0 -or @(Get-PortListeners 8032).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 市场 reader/writer"
  }
  if (Resolve-OwnedProcess "django-market-reader" $MarketReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 市场 reader"
  }
  if (Resolve-OwnedProcess "django-market-writer" $MarketWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 市场 writer"
  }
}

function Read-MarketCredentials {
  $payload = Read-JsonFile $MarketCredentialPath "Django 市场 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseMarketReader", "databaseMarketWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 市场 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseMarketReader) "databaseMarketReader"
  $writer = Unprotect-Value ([string]$payload.databaseMarketWriter) "databaseMarketWriter"
  Assert-StrongSecret $reader "databaseMarketReader"
  Assert-StrongSecret $writer "databaseMarketWriter"
  return [pscustomobject]@{
    ReaderPassword = $reader
    WriterPassword = $writer
  }
}

function Configure-MarketCredentials {
  Assert-MarketRuntimeEntry
  Assert-MarketPortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $MarketCredentialPath -PathType Leaf) {
    Read-MarketCredentials | Out-Null
    Write-Output "Django 市场 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $MarketCredentialPath ([ordered]@{
      version = 1
      databaseMarketReader = Protect-Value $reader
      databaseMarketWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "market_credentials_configured"
    Write-Output "Django 市场 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally {
    $reader = $null
    $writer = $null
  }
}

function Provision-MarketRoles {
  Assert-MarketRuntimeEntry
  Assert-MarketPortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置市场角色" }
  $runtimeSecrets = Read-Secrets
  $marketSecrets = Read-MarketCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_MARKET_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_MARKET_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url `
      "postgres" $superuser "teruisi_market_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_MARKET_READER_PASSWORD = $marketSecrets.ReaderPassword
    $env:TERUISI_PROVISION_MARKET_WRITER_PASSWORD = $marketSecrets.WriterPassword
    $code = @'
import os

import psycopg
from psycopg import sql

roles = {
    "teruisi_market_reader": os.environ["TERUISI_PROVISION_MARKET_READER_PASSWORD"],
    "teruisi_market_writer": os.environ["TERUISI_PROVISION_MARKET_WRITER_PASSWORD"],
}
reader_tables = (
    "market_analysis_options", "market_analysis_options_state",
    "market_import_batches", "market_ranking_entries", "market_master_identities",
    "market_sku_gmv_totals", "market_price_snapshots", "market_data_revisions",
    "market_import_scope_heads", "market_import_attempts", "market_import_fingerprints",
    "market_write_authority", "market_image_cache", "market_image_cache_jobs",
    "market_image_cache_job_items", "market_image_cache_claims",
    "market_price_band_versions", "market_price_band_items",
    "market_master_mapping_rules", "market_subcategory_taxonomy",
    "market_brand_suggestions", "market_brand_recognition_jobs", "market_brand_seeds",
    "market_download_configs", "market_download_tasks", "market_master_audit_logs",
    "market_annotation_prompt_versions", "market_annotation_jobs",
    "market_annotation_items", "market_sku_annotations",
    "market_annotation_commit_receipts", "market_annotation_validation_samples",
    "market_annotation_validation_runs", "market_annotation_validation_results",
    "market_annotation_prompt_audits", "market_annotation_local_agents",
    "market_annotation_concurrency_settings", "market_annotation_cloud_runs",
    "market_netshop_projection", "market_netshop_projection_control",
)
writer_privileges = {
    "market_analysis_options": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_analysis_options_state": ("SELECT", "UPDATE"),
    "market_import_batches": ("SELECT", "INSERT", "UPDATE"),
    "market_ranking_entries": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_master_identities": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_sku_gmv_totals": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_price_snapshots": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_data_revisions": ("SELECT", "INSERT", "UPDATE"),
    "market_import_scope_heads": ("SELECT", "INSERT", "UPDATE"),
    "market_import_attempts": ("SELECT", "INSERT", "UPDATE"),
    "market_import_fingerprints": ("SELECT", "INSERT"),
    "market_write_authority": ("SELECT",),
    "market_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_image_cache": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_image_cache_jobs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_image_cache_job_items": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_image_cache_claims": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_price_band_versions": ("SELECT", "INSERT", "UPDATE"),
    "market_price_band_items": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_master_mapping_rules": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_subcategory_taxonomy": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_brand_suggestions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_brand_recognition_jobs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_brand_seeds": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_download_configs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_download_tasks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_master_audit_logs": ("SELECT", "INSERT"),
    "market_annotation_prompt_versions": ("SELECT", "INSERT", "UPDATE"),
    "market_annotation_jobs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_annotation_items": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_sku_annotations": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_annotation_commit_receipts": ("SELECT", "INSERT"),
    "market_annotation_validation_samples": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_annotation_validation_runs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_annotation_validation_results": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_annotation_prompt_audits": ("SELECT", "INSERT"),
    "market_annotation_local_agents": ("SELECT", "INSERT", "UPDATE"),
    "market_annotation_concurrency_settings": ("SELECT", "INSERT", "UPDATE"),
    "market_annotation_cloud_runs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_netshop_projection": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "market_netshop_projection_control": ("SELECT", "INSERT", "UPDATE"),
}
auto_id_tables = (
    "market_analysis_options",
    "market_ranking_entries", "market_master_identities", "market_import_fingerprints",
    "market_image_cache_job_items", "market_annotation_concurrency_settings",
    "market_netshop_projection",
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

    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_market_reader").format(
        sql.SQL(",").join(sql.Identifier(name) for name in reader_tables)
    ))
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_market_writer").format(
            sql.SQL(",").join(sql.SQL(value) for value in privileges),
            sql.Identifier(table),
        ))
    for table in auto_id_tables:
        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", (f"public.{table}",))
        row = cursor.fetchone()
        if row and row[0]:
            schema, sequence = row[0].split(".", 1)
            cursor.execute(sql.SQL(
                "GRANT USAGE ON SEQUENCE {}.{} TO teruisi_market_writer"
            ).format(sql.Identifier(schema), sql.Identifier(sequence)))

    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "market")
    for role in ("teruisi_market_reader", "teruisi_market_writer"):
        cursor.execute(sql.SQL("GRANT SELECT (email, role, status, scope, version) ON access_control_users TO {}").format(sql.Identifier(role)))
    cursor.execute("ALTER ROLE teruisi_market_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_market_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute(
            "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname=%s",
            (role,),
        )
        flags = cursor.fetchone()
        if flags is None or any(flags):
            raise RuntimeError("market runtime role attributes are excessive")
        cursor.execute(
            "SELECT has_schema_privilege(%s,'public','CREATE'),"
            "has_database_privilege(%s,current_database(),'CREATE')",
            (role, role),
        )
        if any(cursor.fetchone()):
            raise RuntimeError("market runtime role retains DDL privileges")

    cursor.execute(
        "SELECT n.nspname,c.relname,"
        "has_table_privilege('teruisi_market_writer',c.oid,'INSERT'),"
        "has_table_privilege('teruisi_market_writer',c.oid,'UPDATE'),"
        "has_table_privilege('teruisi_market_writer',c.oid,'DELETE'),"
        "has_table_privilege('teruisi_market_writer',c.oid,'TRUNCATE'),"
        "has_any_column_privilege('teruisi_market_writer',c.oid,'INSERT'),"
        "has_any_column_privilege('teruisi_market_writer',c.oid,'UPDATE') "
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
            raise RuntimeError(f"market writer DML escaped allowlist: {schema}.{table}")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "market_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog `
      (Join-Path $LogDirectory "market-role-provision.$RunId.log") `
      "market_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) {
      throw "配置市场最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）"
    }
    Write-LauncherEvent "INFO" "market_database_roles_provisioned"
    Write-Output "Django 市场 reader/writer 最小权限角色已配置；其他域未改变。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_MARKET_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_MARKET_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null
    $marketSecrets = $null
    $superuser = $null
  }
}

function Get-MarketWriteAuthority([object]$RuntimeSecrets, [object]$MarketSecrets) {
  $writerUrl = Database-Url `
    "teruisi_market_writer" $MarketSecrets.WriterPassword `
    "teruisi_market_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from market.models import MarketWriteAuthority

authority = MarketWriteAuthority.objects.filter(id=1).first()
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "market_authority_probe.py"
  $payload = Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "migration_writer" $false $MarketReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess `
        $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 市场写入权威"
    }
  $writerUrl = $null
  if ([string]$payload.status -cnotin @("d1", "postgres")) {
    throw "PostgreSQL 市场写入权威状态无效"
  }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^market-[0-9a-f]{24}$")
    )) {
    throw "PostgreSQL 市场写入权威证据不完整"
  }
  return $payload
}

function Start-MarketReader([object]$RuntimeSecrets, [object]$MarketSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8031", "--threads=6", "--connection-limit=60",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-market-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$MarketReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-market-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-market-reader" $MarketReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "market-reader" $MarketReaderHealthUrl "127.0.0.1:8031"
    return $false
  }
  if (@(Get-PortListeners 8031).Count -gt 0) { throw "端口 8031 被非本部署服务占用" }
  Remove-OldServiceLogs "django-market-reader"
  $readerUrl = Database-Url `
    "teruisi_market_reader" $MarketSecrets.ReaderPassword `
    "teruisi_django_market_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $readerUrl "market_reader" $true $MarketReaderMaxBodyBytes "" "" {
      Start-ManagedProcess "django-market-reader" $Waitress $arguments $BackendRoot `
        $MarketReaderPidPath $fingerprint `
        (Join-Path $LogDirectory "django-market-reader.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-market-reader.$RunId.stderr.log") | Out-Null
    }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "market-reader" $MarketReaderHealthUrl "127.0.0.1:8031"
    return $true
  } catch {
    Stop-OwnedProcess "django-market-reader" $MarketReaderPidPath $Waitress
    throw
  }
}

function Start-MarketWriter(
  [object]$RuntimeSecrets,
  [object]$MarketSecrets,
  [object]$Authority
) {
  if ([string]$Authority.status -cne "postgres") {
    throw "PostgreSQL 尚未成为市场唯一写入源；拒绝启动市场 writer"
  }
  $arguments = @(
    "--listen=127.0.0.1:8032", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-market-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$MarketWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-market-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-market-writer" $MarketWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "market-writer" $MarketWriterHealthUrl "127.0.0.1:8032"
    return $false
  }
  if (@(Get-PortListeners 8032).Count -gt 0) { throw "端口 8032 被非本部署服务占用" }
  Remove-OldServiceLogs "django-market-writer"
  $writerUrl = Database-Url `
    "teruisi_market_writer" $MarketSecrets.WriterPassword `
    "teruisi_django_market_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "market_writer" $false $MarketWriterMaxBodyBytes `
    ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
      Start-ManagedProcess "django-market-writer" $Waitress $arguments $BackendRoot `
        $MarketWriterPidPath $fingerprint `
        (Join-Path $LogDirectory "django-market-writer.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-market-writer.$RunId.stderr.log") | Out-Null
    }
  $writerUrl = $null
  try {
    Wait-DjangoReady "market-writer" $MarketWriterHealthUrl "127.0.0.1:8032"
    return $true
  } catch {
    Stop-OwnedProcess "django-market-writer" $MarketWriterPidPath $Waitress
    throw
  }
}

function Start-MarketStack([string]$LifecycleAclToken = "") {
  Assert-MarketRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动市场服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets
  $marketSecrets = Read-MarketCredentials
  $readerStarted = $false
  $writerStarted = $false
  try {
    $authority = Get-MarketWriteAuthority $runtimeSecrets $marketSecrets
    if (Test-Path -LiteralPath $MarketStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $MarketStartupPath "Django 市场开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @(
            "version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt"
          )) -or
          [int]$startup.version -ne 1 -or
          [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or
          [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django 市场开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-MarketReader $runtimeSecrets $marketSecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") {
      $writerStarted = Start-MarketWriter $runtimeSecrets $marketSecrets $authority
    }
    Wait-DjangoReady "market-reader" $MarketReaderHealthUrl "127.0.0.1:8031"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "market-writer" $MarketWriterHealthUrl "127.0.0.1:8032"
      Write-Output "Django 市场服务已就绪：reader=http://127.0.0.1:8031 writer=http://127.0.0.1:8032。"
    } else {
      Write-Output "Django 市场 reader 已就绪；PostgreSQL 市场写权尚未激活，writer 保持停止。"
    }
  } catch {
    $original = $_.Exception
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-market-writer" $MarketWriterPidPath $Waitress } catch {}
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-market-reader" $MarketReaderPidPath $Waitress } catch {}
    }
    throw $original
  } finally {
    $runtimeSecrets = $null
    $marketSecrets = $null
  }
}

function Stop-MarketStack([string]$LifecycleAclToken = "") {
  Assert-MarketRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-market-writer" $MarketWriterPidPath $Waitress
  Stop-OwnedProcess "django-market-reader" $MarketReaderPidPath $Waitress
  Write-Output "Django 市场 reader/writer 已停止；销售、财务、网店、ERP 与 PostgreSQL 未改变。"
}

function Enable-MarketStartup {
  Assert-MarketRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用市场开机启动" }
  $runtimeSecrets = Read-Secrets
  $marketSecrets = Read-MarketCredentials
  try {
    $authority = Get-MarketWriteAuthority $runtimeSecrets $marketSecrets
    if ([string]$authority.status -cne "postgres") {
      throw "只有 PostgreSQL 已取得市场唯一写权后才能启用市场开机启动"
    }
    Start-MarketStack
    Write-AtomicJson $MarketStartupPath ([ordered]@{
      version = 1
      authorityEpoch = [string]$authority.authorityEpoch
      cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId
      enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 市场域已加入现有受控开机启动链。"
  } finally {
    $runtimeSecrets = $null
    $marketSecrets = $null
  }
}

function Disable-MarketStartup {
  Assert-MarketRuntimeEntry
  if (Test-Path -LiteralPath $MarketStartupPath -PathType Leaf) {
    Remove-Item -LiteralPath $MarketStartupPath -Force
  }
  Write-Output "Django 市场域已退出开机启动链；当前运行进程未改变。"
}

function Show-MarketStatus {
  $reader = "stopped"
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-market-reader" $MarketReaderPidPath $Waitress) {
      $reader = "running"
    } elseif (@(Get-PortListeners 8031).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  try {
    if (Resolve-OwnedProcess "django-market-writer" $MarketWriterPidPath $Waitress) {
      $writer = "running"
    } elseif (@(Get-PortListeners 8032).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  $writerReady = "not_ready"
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $MarketReaderHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8031" }).StatusCode -eq 200) {
      $readerReady = "ready"
    }
  } catch {}
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $MarketWriterHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8032" }).StatusCode -eq 200) {
      $writerReady = "ready"
    }
  } catch {}
  $status = [pscustomobject][ordered]@{
    MarketReader = $reader
    MarketWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) }
  else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-MarketCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-MarketRoles } }
    "Start" { Invoke-WithServiceMutex { Start-MarketStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-MarketStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-MarketStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-MarketStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-MarketStartup } }
  }
} catch {
  Write-LauncherEvent "ERROR" "market_action_failed" $_.Exception.Message
  throw
}
