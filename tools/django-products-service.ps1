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

$ProductsCredentialPath = Join-Path $RuntimeRoot "secrets\products-credentials.dpapi.json"
$ProductsReaderPidPath = Join-Path $RunDirectory "django-products-reader.pid.json"
$ProductsWriterPidPath = Join-Path $RunDirectory "django-products-writer.pid.json"
$ProductsReaderHealthUrl = "http://127.0.0.1:8041/health/ready"
$ProductsWriterHealthUrl = "http://127.0.0.1:8042/health/ready"
$ProductsStartupPath = Join-Path $RuntimeRoot "products-service-enabled.json"
$ProductsReaderMaxBodyBytes = 1048576
$ProductsWriterMaxBodyBytes = 33554432
$MinimumPostgresConnectionsForProducts = 120

function Assert-ProductsRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "商品经营服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=products"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-ProductsPortsFree([string]$Operation) {
  if (@(Get-PortListeners 8041).Count -gt 0 -or @(Get-PortListeners 8042).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 商品经营 reader/writer"
  }
  if (Resolve-OwnedProcess "django-products-reader" $ProductsReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 商品经营 reader"
  }
  if (Resolve-OwnedProcess "django-products-writer" $ProductsWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 商品经营 writer"
  }
}

function Read-ProductsCredentials {
  $payload = Read-JsonFile $ProductsCredentialPath "Django 商品经营 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseProductsReader", "databaseProductsWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 商品经营 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseProductsReader) "databaseProductsReader"
  $writer = Unprotect-Value ([string]$payload.databaseProductsWriter) "databaseProductsWriter"
  Assert-StrongSecret $reader "databaseProductsReader"
  Assert-StrongSecret $writer "databaseProductsWriter"
  return [pscustomobject]@{
    ReaderPassword = $reader
    WriterPassword = $writer
  }
}

function Configure-ProductsCredentials {
  Assert-ProductsRuntimeEntry
  Assert-ProductsPortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $ProductsCredentialPath -PathType Leaf) {
    Read-ProductsCredentials | Out-Null
    Write-Output "Django 商品经营 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $ProductsCredentialPath ([ordered]@{
      version = 1
      databaseProductsReader = Protect-Value $reader
      databaseProductsWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "products_credentials_configured"
    Write-Output "Django 商品经营 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally {
    $reader = $null
    $writer = $null
  }
}

function Provision-ProductsRoles {
  Assert-ProductsRuntimeEntry
  Assert-ProductsPortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置商品经营角色" }
  $runtimeSecrets = Read-Secrets
  $productsSecrets = Read-ProductsCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_PRODUCTS_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_PRODUCTS_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url `
      "postgres" $superuser "teruisi_products_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_PRODUCTS_READER_PASSWORD = $productsSecrets.ReaderPassword
    $env:TERUISI_PROVISION_PRODUCTS_WRITER_PASSWORD = $productsSecrets.WriterPassword
    $code = @'
import os

import psycopg
from psycopg import sql

roles = {
    "teruisi_products_reader": os.environ["TERUISI_PROVISION_PRODUCTS_READER_PASSWORD"],
    "teruisi_products_writer": os.environ["TERUISI_PROVISION_PRODUCTS_WRITER_PASSWORD"],
}
reader_tables = (
    "sales_data_revisions", "sales_import_batches", "sales_order_lines",
    "erp_product_master", "erp_combo_items", "erp_reference_import_batches_pg",
    "erp_reference_import_scope_heads", "erp_reference_write_authority",
    "product_shipping_rate_import_batches", "product_shipping_rates",
    "product_data_revisions",
    "product_inventory_projection", "product_inventory_projection_control",
)
writer_privileges = {
    "product_shipping_rate_import_batches": ("SELECT", "INSERT", "UPDATE"),
    "product_shipping_rates": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "product_import_scope_heads": ("SELECT", "UPDATE"),
    "product_import_attempts": ("SELECT", "INSERT", "UPDATE"),
    "product_import_fingerprints": ("SELECT", "INSERT", "UPDATE"),
    "product_data_revisions": ("SELECT", "UPDATE"),
    "product_write_authority": ("SELECT",),
    "product_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "product_inventory_projection": ("SELECT", "INSERT", "DELETE"),
    "product_inventory_projection_control": ("SELECT", "UPDATE"),
    "product_raw_upload_sessions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "product_raw_upload_chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}
auto_id_tables = (
    "product_import_fingerprints", "product_inventory_projection",
    "product_raw_upload_chunks",
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

    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_products_reader").format(
        sql.SQL(",").join(sql.Identifier(name) for name in reader_tables)
    ))
    # sales_data_revisions is protected by RLS.  A table-level SELECT grant is
    # intentionally insufficient: expose only the sales/ERP revision rows that
    # products readiness and snapshot fencing consume.
    cursor.execute(
        "DROP POLICY IF EXISTS products_revision_reader ON sales_data_revisions"
    )
    cursor.execute(
        "CREATE POLICY products_revision_reader ON sales_data_revisions "
        "FOR SELECT TO teruisi_products_reader "
        "USING (domain IN ('sales', 'erp'))"
    )
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_products_writer").format(
            sql.SQL(",").join(sql.SQL(value) for value in privileges),
            sql.Identifier(table),
        ))
    for table in auto_id_tables:
        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", (f"public.{table}",))
        row = cursor.fetchone()
        if row and row[0]:
            schema, sequence = row[0].split(".", 1)
            cursor.execute(sql.SQL(
                "GRANT USAGE ON SEQUENCE {}.{} TO teruisi_products_writer"
            ).format(sql.Identifier(schema), sql.Identifier(sequence)))

    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "products")
    cursor.execute("ALTER ROLE teruisi_products_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_products_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute(
            "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname=%s",
            (role,),
        )
        flags = cursor.fetchone()
        if flags is None or any(flags):
            raise RuntimeError("products runtime role attributes are excessive")
        cursor.execute(
            "SELECT has_schema_privilege(%s,'public','CREATE'),"
            "has_database_privilege(%s,current_database(),'CREATE')",
            (role, role),
        )
        if any(cursor.fetchone()):
            raise RuntimeError("products runtime role retains DDL privileges")

    cursor.execute(
        "SELECT p.polcmd,r.rolname,pg_get_expr(p.polqual,p.polrelid) "
        "FROM pg_policy p "
        "JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_roles r ON r.oid=ANY(p.polroles) "
        "WHERE n.nspname='public' AND c.relname='sales_data_revisions' "
        "AND p.polname='products_revision_reader'"
    )
    policy_rows = cursor.fetchall()
    if (
        len(policy_rows) != 1
        or policy_rows[0][0] != "r"
        or policy_rows[0][1] != "teruisi_products_reader"
        or "sales" not in str(policy_rows[0][2])
        or "erp" not in str(policy_rows[0][2])
    ):
        raise RuntimeError("products reader revision RLS policy is invalid")

    cursor.execute(
        "SELECT n.nspname,c.relname,"
        "has_table_privilege('teruisi_products_writer',c.oid,'INSERT'),"
        "has_table_privilege('teruisi_products_writer',c.oid,'UPDATE'),"
        "has_table_privilege('teruisi_products_writer',c.oid,'DELETE'),"
        "has_table_privilege('teruisi_products_writer',c.oid,'TRUNCATE'),"
        "has_any_column_privilege('teruisi_products_writer',c.oid,'INSERT'),"
        "has_any_column_privilege('teruisi_products_writer',c.oid,'UPDATE') "
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
            raise RuntimeError(f"products writer DML escaped allowlist: {schema}.{table}")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "products_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog `
      (Join-Path $LogDirectory "products-role-provision.$RunId.log") `
      "products_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) {
      throw "配置商品经营最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）"
    }
    Write-LauncherEvent "INFO" "products_database_roles_provisioned"
    Write-Output "Django 商品经营 reader/writer 最小权限角色已配置；其他域未改变。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_PRODUCTS_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_PRODUCTS_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null
    $productsSecrets = $null
    $superuser = $null
  }
}

function Get-ProductsWriteAuthority([object]$RuntimeSecrets, [object]$ProductsSecrets) {
  $writerUrl = Database-Url `
    "teruisi_products_writer" $ProductsSecrets.WriterPassword `
    "teruisi_products_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection
from products.models import ProductWriteAuthority

authority = ProductWriteAuthority.objects.filter(id=1).first()
with connection.cursor() as cursor:
    cursor.execute("SHOW max_connections")
    max_connections = int(cursor.fetchone()[0])
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
    "maxConnections": max_connections,
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "products_authority_probe.py"
  $payload = Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "migration_writer" $false $ProductsReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess `
        $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 商品经营写入权威"
    }
  $writerUrl = $null
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "status", "authorityEpoch", "cutoverId", "migrationRunId", "maxConnections"
      ))) {
    throw "PostgreSQL 商品经营写入权威探针结构无效"
  }
  if ([int]$payload.maxConnections -lt $MinimumPostgresConnectionsForProducts) {
    throw "PostgreSQL max_connections 低于商品经营正式运行所需容量；拒绝启动商品经营服务"
  }
  if ([string]$payload.status -cnotin @("d1", "postgres")) {
    throw "PostgreSQL 商品经营写入权威状态无效"
  }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^products-apply-[0-9a-f]{32}$")
    )) {
    throw "PostgreSQL 商品经营写入权威证据不完整"
  }
  return $payload
}

function Start-ProductsReader([object]$RuntimeSecrets, [object]$ProductsSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8041", "--threads=6", "--connection-limit=60",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-products-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$ProductsReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-products-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-products-reader" $ProductsReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "products-reader" $ProductsReaderHealthUrl "127.0.0.1:8041"
    return $false
  }
  if (@(Get-PortListeners 8041).Count -gt 0) { throw "端口 8041 被非本部署服务占用" }
  Remove-OldServiceLogs "django-products-reader"
  $readerUrl = Database-Url `
    "teruisi_products_reader" $ProductsSecrets.ReaderPassword `
    "teruisi_django_products_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $readerUrl "products_reader" $true $ProductsReaderMaxBodyBytes "" "" {
      Start-ManagedProcess "django-products-reader" $Waitress $arguments $BackendRoot `
        $ProductsReaderPidPath $fingerprint `
        (Join-Path $LogDirectory "django-products-reader.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-products-reader.$RunId.stderr.log") | Out-Null
    }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "products-reader" $ProductsReaderHealthUrl "127.0.0.1:8041"
    return $true
  } catch {
    Stop-OwnedProcess "django-products-reader" $ProductsReaderPidPath $Waitress
    throw
  }
}

function Start-ProductsWriter(
  [object]$RuntimeSecrets,
  [object]$ProductsSecrets,
  [object]$Authority
) {
  if ([string]$Authority.status -cne "postgres") {
    throw "PostgreSQL 尚未成为商品经营唯一写入源；拒绝启动商品经营 writer"
  }
  $arguments = @(
    "--listen=127.0.0.1:8042", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-products-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$ProductsWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-products-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-products-writer" $ProductsWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "products-writer" $ProductsWriterHealthUrl "127.0.0.1:8042"
    return $false
  }
  if (@(Get-PortListeners 8042).Count -gt 0) { throw "端口 8042 被非本部署服务占用" }
  Remove-OldServiceLogs "django-products-writer"
  $writerUrl = Database-Url `
    "teruisi_products_writer" $ProductsSecrets.WriterPassword `
    "teruisi_django_products_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "products_writer" $false $ProductsWriterMaxBodyBytes `
    ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
      Start-ManagedProcess "django-products-writer" $Waitress $arguments $BackendRoot `
        $ProductsWriterPidPath $fingerprint `
        (Join-Path $LogDirectory "django-products-writer.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-products-writer.$RunId.stderr.log") | Out-Null
    }
  $writerUrl = $null
  try {
    Wait-DjangoReady "products-writer" $ProductsWriterHealthUrl "127.0.0.1:8042"
    return $true
  } catch {
    Stop-OwnedProcess "django-products-writer" $ProductsWriterPidPath $Waitress
    throw
  }
}

function Start-ProductsStack([string]$LifecycleAclToken = "") {
  Assert-ProductsRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动商品经营服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets
  $productsSecrets = Read-ProductsCredentials
  $readerStarted = $false
  $writerStarted = $false
  try {
    $authority = Get-ProductsWriteAuthority $runtimeSecrets $productsSecrets
    if (Test-Path -LiteralPath $ProductsStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $ProductsStartupPath "Django 商品经营开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @(
            "version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt"
          )) -or
          [int]$startup.version -ne 1 -or
          [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or
          [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django 商品经营开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-ProductsReader $runtimeSecrets $productsSecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") {
      $writerStarted = Start-ProductsWriter $runtimeSecrets $productsSecrets $authority
    }
    Wait-DjangoReady "products-reader" $ProductsReaderHealthUrl "127.0.0.1:8041"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "products-writer" $ProductsWriterHealthUrl "127.0.0.1:8042"
      Write-Output "Django 商品经营服务已就绪：reader=http://127.0.0.1:8041 writer=http://127.0.0.1:8042。"
    } else {
      Write-Output "Django 商品经营 reader 已就绪；PostgreSQL 商品经营写权尚未激活，writer 保持停止。"
    }
  } catch {
    $original = $_.Exception
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-products-writer" $ProductsWriterPidPath $Waitress } catch {}
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-products-reader" $ProductsReaderPidPath $Waitress } catch {}
    }
    throw $original
  } finally {
    $runtimeSecrets = $null
    $productsSecrets = $null
  }
}

function Stop-ProductsStack([string]$LifecycleAclToken = "") {
  Assert-ProductsRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-products-writer" $ProductsWriterPidPath $Waitress
  Stop-OwnedProcess "django-products-reader" $ProductsReaderPidPath $Waitress
  Write-Output "Django 商品经营 reader/writer 已停止；销售、财务、网店、市场、ERP 与 PostgreSQL 未改变。"
}

function Enable-ProductsStartup {
  Assert-ProductsRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用商品经营开机启动" }
  $runtimeSecrets = Read-Secrets
  $productsSecrets = Read-ProductsCredentials
  try {
    $authority = Get-ProductsWriteAuthority $runtimeSecrets $productsSecrets
    if ([string]$authority.status -cne "postgres") {
      throw "只有 PostgreSQL 已取得商品经营唯一写权后才能启用商品经营开机启动"
    }
    Start-ProductsStack
    Write-AtomicJson $ProductsStartupPath ([ordered]@{
      version = 1
      authorityEpoch = [string]$authority.authorityEpoch
      cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId
      enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 商品经营域已加入现有受控开机启动链。"
  } finally {
    $runtimeSecrets = $null
    $productsSecrets = $null
  }
}

function Disable-ProductsStartup {
  Assert-ProductsRuntimeEntry
  if (Test-Path -LiteralPath $ProductsStartupPath -PathType Leaf) {
    Remove-Item -LiteralPath $ProductsStartupPath -Force
  }
  Write-Output "Django 商品经营域已退出开机启动链；当前运行进程未改变。"
}

function Show-ProductsStatus {
  $reader = "stopped"
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-products-reader" $ProductsReaderPidPath $Waitress) {
      $reader = "running"
    } elseif (@(Get-PortListeners 8041).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  try {
    if (Resolve-OwnedProcess "django-products-writer" $ProductsWriterPidPath $Waitress) {
      $writer = "running"
    } elseif (@(Get-PortListeners 8042).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  $writerReady = "not_ready"
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $ProductsReaderHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8041" }).StatusCode -eq 200) {
      $readerReady = "ready"
    }
  } catch {}
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $ProductsWriterHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8042" }).StatusCode -eq 200) {
      $writerReady = "ready"
    }
  } catch {}
  $status = [pscustomobject][ordered]@{
    ProductsReader = $reader
    ProductsWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) }
  else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-ProductsCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-ProductsRoles } }
    "Start" { Invoke-WithServiceMutex { Start-ProductsStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-ProductsStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-ProductsStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-ProductsStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-ProductsStartup } }
  }
} catch {
  Write-LauncherEvent "ERROR" "products_action_failed" $_.Exception.Message
  throw
}
