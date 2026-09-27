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

$InventoryCredentialPath = Join-Path $RuntimeRoot "secrets\inventory-credentials.dpapi.json"
$InventoryReaderPidPath = Join-Path $RunDirectory "django-inventory-reader.pid.json"
$InventoryWriterPidPath = Join-Path $RunDirectory "django-inventory-writer.pid.json"
$InventoryReaderHealthUrl = "http://127.0.0.1:8051/health/ready"
$InventoryWriterHealthUrl = "http://127.0.0.1:8052/health/ready"
$InventoryStartupPath = Join-Path $RuntimeRoot "inventory-service-enabled.json"
$InventoryReaderMaxBodyBytes = 1048576
$InventoryWriterMaxBodyBytes = 67108864
$MinimumPostgresConnectionsForInventory = 120

function Assert-InventoryRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "库存服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=inventory"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-InventoryPortsFree([string]$Operation) {
  if (@(Get-PortListeners 8051).Count -gt 0 -or @(Get-PortListeners 8052).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 库存 reader/writer"
  }
  if (Resolve-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 库存 reader"
  }
  if (Resolve-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 库存 writer"
  }
}

function Read-InventoryCredentials {
  $payload = Read-JsonFile $InventoryCredentialPath "Django 库存 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseInventoryReader", "databaseInventoryWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 库存 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseInventoryReader) "databaseInventoryReader"
  $writer = Unprotect-Value ([string]$payload.databaseInventoryWriter) "databaseInventoryWriter"
  Assert-StrongSecret $reader "databaseInventoryReader"
  Assert-StrongSecret $writer "databaseInventoryWriter"
  return [pscustomobject]@{
    ReaderPassword = $reader
    WriterPassword = $writer
  }
}

function Configure-InventoryCredentials {
  Assert-InventoryRuntimeEntry
  Assert-InventoryPortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $InventoryCredentialPath -PathType Leaf) {
    Read-InventoryCredentials | Out-Null
    Write-Output "Django 库存 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $InventoryCredentialPath ([ordered]@{
      version = 1
      databaseInventoryReader = Protect-Value $reader
      databaseInventoryWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "inventory_credentials_configured"
    Write-Output "Django 库存 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally {
    $reader = $null
    $writer = $null
  }
}

function Provision-InventoryRoles {
  Assert-InventoryRuntimeEntry
  Assert-InventoryPortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置库存角色" }
  $runtimeSecrets = Read-Secrets
  $inventorySecrets = Read-InventoryCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_INVENTORY_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_INVENTORY_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url `
      "postgres" $superuser "teruisi_inventory_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_INVENTORY_READER_PASSWORD = $inventorySecrets.ReaderPassword
    $env:TERUISI_PROVISION_INVENTORY_WRITER_PASSWORD = $inventorySecrets.WriterPassword
    $code = @'
import os

import psycopg
from psycopg import sql

roles = {
    "teruisi_inventory_reader": os.environ["TERUISI_PROVISION_INVENTORY_READER_PASSWORD"],
    "teruisi_inventory_writer": os.environ["TERUISI_PROVISION_INVENTORY_WRITER_PASSWORD"],
}
reader_tables = (
    "inventory_guangdong_monitor_items", "inventory_guangdong_supplier_cycles",
    "sales_data_revisions", "sales_import_batches", "sales_order_lines",
    "erp_product_master", "erp_combo_items", "erp_reference_import_batches_pg",
    "erp_reference_import_scope_heads", "erp_reference_write_authority",
    "inventory_import_batches", "inventory_stock_lines", "inventory_age_lines",
    "inventory_data_revisions", "replenishment_plan_items",
    "inventory_operating_settings",
)
writer_privileges = {
    "inventory_guangdong_monitor_items": ("SELECT", "INSERT", "UPDATE"),
    "inventory_guangdong_supplier_cycles": ("SELECT", "INSERT", "UPDATE"),
    "inventory_guangdong_monitor_audits": ("SELECT", "INSERT"),
    "inventory_import_batches": ("SELECT", "INSERT", "UPDATE"),
    "inventory_stock_lines": ("SELECT", "INSERT", "DELETE"),
    "inventory_age_lines": ("SELECT", "INSERT", "DELETE"),
    "inventory_import_scope_heads": ("SELECT", "UPDATE"),
    "inventory_import_attempts": ("SELECT", "INSERT", "UPDATE"),
    "inventory_import_fingerprints": ("SELECT", "INSERT"),
    "inventory_data_revisions": ("SELECT", "UPDATE"),
    "inventory_write_authority": ("SELECT",),
    "inventory_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "inventory_raw_upload_sessions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "inventory_raw_upload_chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "replenishment_plan_items": ("SELECT", "INSERT", "UPDATE"),
    "inventory_replenishment_group_deliveries": ("SELECT", "INSERT", "UPDATE"),
    "inventory_operating_settings": ("SELECT", "UPDATE"),
}
auto_id_tables = (
    "inventory_stock_lines", "inventory_age_lines", "inventory_import_fingerprints",
    "inventory_raw_upload_chunks",
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

    for role in ("teruisi_inventory_reader", "teruisi_inventory_writer"):
        cursor.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(
            sql.SQL(",").join(sql.Identifier(name) for name in reader_tables),
            sql.Identifier(role),
        ))
    # sales_data_revisions is protected by RLS.  A table-level SELECT grant is
    # intentionally insufficient: expose only the sales/ERP revision rows that
    # inventory readiness and snapshot fencing consume.
    cursor.execute(
        "DROP POLICY IF EXISTS inventory_revision_reader ON sales_data_revisions"
    )
    cursor.execute(
        "CREATE POLICY inventory_revision_reader ON sales_data_revisions "
        "FOR SELECT TO teruisi_inventory_reader, teruisi_inventory_writer "
        "USING (domain IN ('sales', 'erp'))"
    )
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_inventory_writer").format(
            sql.SQL(",").join(sql.SQL(value) for value in privileges),
            sql.Identifier(table),
        ))
    for table in auto_id_tables:
        cursor.execute("SELECT pg_get_serial_sequence(%s,'id')", (f"public.{table}",))
        row = cursor.fetchone()
        if row and row[0]:
            schema, sequence = row[0].split(".", 1)
            cursor.execute(sql.SQL(
                "GRANT USAGE ON SEQUENCE {}.{} TO teruisi_inventory_writer"
            ).format(sql.Identifier(schema), sql.Identifier(sequence)))

    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "inventory")
    cursor.execute("ALTER ROLE teruisi_inventory_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_inventory_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute(
            "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname=%s",
            (role,),
        )
        flags = cursor.fetchone()
        if flags is None or any(flags):
            raise RuntimeError("inventory runtime role attributes are excessive")
        cursor.execute(
            "SELECT has_schema_privilege(%s,'public','CREATE'),"
            "has_database_privilege(%s,current_database(),'CREATE')",
            (role, role),
        )
        if any(cursor.fetchone()):
            raise RuntimeError("inventory runtime role retains DDL privileges")

    cursor.execute(
        "SELECT p.polcmd,r.rolname,pg_get_expr(p.polqual,p.polrelid) "
        "FROM pg_policy p "
        "JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_roles r ON r.oid=ANY(p.polroles) "
        "WHERE n.nspname='public' AND c.relname='sales_data_revisions' "
        "AND p.polname='inventory_revision_reader'"
    )
    policy_rows = cursor.fetchall()
    if (
        len(policy_rows) != 2
        or {row[1] for row in policy_rows}
        != {"teruisi_inventory_reader", "teruisi_inventory_writer"}
        or any(row[0] != "r" for row in policy_rows)
        or any("sales" not in str(row[2]) or "erp" not in str(row[2]) for row in policy_rows)
    ):
        raise RuntimeError("inventory reader revision RLS policy is invalid")

    cursor.execute(
        "SELECT n.nspname,c.relname,"
        "has_table_privilege('teruisi_inventory_writer',c.oid,'INSERT'),"
        "has_table_privilege('teruisi_inventory_writer',c.oid,'UPDATE'),"
        "has_table_privilege('teruisi_inventory_writer',c.oid,'DELETE'),"
        "has_table_privilege('teruisi_inventory_writer',c.oid,'TRUNCATE'),"
        "has_any_column_privilege('teruisi_inventory_writer',c.oid,'INSERT'),"
        "has_any_column_privilege('teruisi_inventory_writer',c.oid,'UPDATE') "
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
            raise RuntimeError(f"inventory writer DML escaped allowlist: {schema}.{table}")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "inventory_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog `
      (Join-Path $LogDirectory "inventory-role-provision.$RunId.log") `
      "inventory_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) {
      throw "配置库存最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）"
    }
    Write-LauncherEvent "INFO" "inventory_database_roles_provisioned"
    Write-Output "Django 库存 reader/writer 最小权限角色已配置；其他域未改变。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_INVENTORY_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_INVENTORY_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null
    $inventorySecrets = $null
    $superuser = $null
  }
}

function Get-InventoryWriteAuthority([object]$RuntimeSecrets, [object]$InventorySecrets) {
  $writerUrl = Database-Url `
    "teruisi_inventory_writer" $InventorySecrets.WriterPassword `
    "teruisi_inventory_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection
from inventory.models import InventoryWriteAuthority

authority = InventoryWriteAuthority.objects.filter(id=1).first()
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
  $launcher = ConvertTo-PythonBase64Launcher $code "inventory_authority_probe.py"
  $payload = Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "migration_writer" $false $InventoryReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess `
        $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 库存写入权威"
    }
  $writerUrl = $null
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "status", "authorityEpoch", "cutoverId", "migrationRunId", "maxConnections"
      ))) {
    throw "PostgreSQL 库存写入权威探针结构无效"
  }
  if ([int]$payload.maxConnections -lt $MinimumPostgresConnectionsForInventory) {
    throw "PostgreSQL max_connections 低于库存正式运行所需容量；拒绝启动库存服务"
  }
  if ([string]$payload.status -cnotin @("d1", "postgres")) {
    throw "PostgreSQL 库存写入权威状态无效"
  }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^inventory-apply-[0-9a-f]{32}$")
    )) {
    throw "PostgreSQL 库存写入权威证据不完整"
  }
  return $payload
}

function Start-InventoryReader([object]$RuntimeSecrets, [object]$InventorySecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8051", "--threads=6", "--connection-limit=60",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-inventory-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$InventoryReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-inventory-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "inventory-reader" $InventoryReaderHealthUrl "127.0.0.1:8051"
    return $false
  }
  if (@(Get-PortListeners 8051).Count -gt 0) { throw "端口 8051 被非本部署服务占用" }
  Remove-OldServiceLogs "django-inventory-reader"
  $readerUrl = Database-Url `
    "teruisi_inventory_reader" $InventorySecrets.ReaderPassword `
    "teruisi_django_inventory_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $readerUrl "inventory_reader" $true $InventoryReaderMaxBodyBytes "" "" {
      Start-ManagedProcess "django-inventory-reader" $Waitress $arguments $BackendRoot `
        $InventoryReaderPidPath $fingerprint `
        (Join-Path $LogDirectory "django-inventory-reader.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-inventory-reader.$RunId.stderr.log") | Out-Null
    }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "inventory-reader" $InventoryReaderHealthUrl "127.0.0.1:8051"
    return $true
  } catch {
    Stop-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress
    throw
  }
}

function Start-InventoryWriter(
  [object]$RuntimeSecrets,
  [object]$InventorySecrets,
  [object]$Authority
) {
  if ([string]$Authority.status -cne "postgres") {
    throw "PostgreSQL 尚未成为库存唯一写入源；拒绝启动库存 writer"
  }
  $arguments = @(
    "--listen=127.0.0.1:8052", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-inventory-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$InventoryWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-inventory-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "inventory-writer" $InventoryWriterHealthUrl "127.0.0.1:8052"
    return $false
  }
  if (@(Get-PortListeners 8052).Count -gt 0) { throw "端口 8052 被非本部署服务占用" }
  Remove-OldServiceLogs "django-inventory-writer"
  $writerUrl = Database-Url `
    "teruisi_inventory_writer" $InventorySecrets.WriterPassword `
    "teruisi_django_inventory_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "inventory_writer" $false $InventoryWriterMaxBodyBytes `
    ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
      Start-ManagedProcess "django-inventory-writer" $Waitress $arguments $BackendRoot `
        $InventoryWriterPidPath $fingerprint `
        (Join-Path $LogDirectory "django-inventory-writer.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-inventory-writer.$RunId.stderr.log") | Out-Null
    }
  $writerUrl = $null
  try {
    Wait-DjangoReady "inventory-writer" $InventoryWriterHealthUrl "127.0.0.1:8052"
    return $true
  } catch {
    Stop-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress
    throw
  }
}

function Start-InventoryStack([string]$LifecycleAclToken = "") {
  Assert-InventoryRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动库存服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets
  $inventorySecrets = Read-InventoryCredentials
  $readerStarted = $false
  $writerStarted = $false
  try {
    $authority = Get-InventoryWriteAuthority $runtimeSecrets $inventorySecrets
    if (Test-Path -LiteralPath $InventoryStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $InventoryStartupPath "Django 库存开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @(
            "version", "authorityEpoch", "cutoverId", "migrationRunId", "enabledAt"
          )) -or
          [int]$startup.version -ne 1 -or
          [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or
          [string]$startup.migrationRunId -cne [string]$authority.migrationRunId) {
        throw "Django 库存开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-InventoryReader $runtimeSecrets $inventorySecrets -DeferReady
    if ([string]$authority.status -ceq "postgres") {
      $writerStarted = Start-InventoryWriter $runtimeSecrets $inventorySecrets $authority
    }
    Wait-DjangoReady "inventory-reader" $InventoryReaderHealthUrl "127.0.0.1:8051"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "inventory-writer" $InventoryWriterHealthUrl "127.0.0.1:8052"
      Write-Output "Django 库存服务已就绪：reader=http://127.0.0.1:8051 writer=http://127.0.0.1:8052。"
    } else {
      Write-Output "Django 库存 reader 已就绪；PostgreSQL 库存写权尚未激活，writer 保持停止。"
    }
  } catch {
    $original = $_.Exception
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress } catch {}
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress } catch {}
    }
    throw $original
  } finally {
    $runtimeSecrets = $null
    $inventorySecrets = $null
  }
}

function Stop-InventoryStack([string]$LifecycleAclToken = "") {
  Assert-InventoryRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress
  Stop-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress
  Write-Output "Django 库存 reader/writer 已停止；销售、财务、网店、市场、ERP 与 PostgreSQL 未改变。"
}

function Enable-InventoryStartup {
  Assert-InventoryRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用库存开机启动" }
  $runtimeSecrets = Read-Secrets
  $inventorySecrets = Read-InventoryCredentials
  try {
    $authority = Get-InventoryWriteAuthority $runtimeSecrets $inventorySecrets
    if ([string]$authority.status -cne "postgres") {
      throw "只有 PostgreSQL 已取得库存唯一写权后才能启用库存开机启动"
    }
    Start-InventoryStack
    Write-AtomicJson $InventoryStartupPath ([ordered]@{
      version = 1
      authorityEpoch = [string]$authority.authorityEpoch
      cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId
      enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 库存域已加入现有受控开机启动链。"
  } finally {
    $runtimeSecrets = $null
    $inventorySecrets = $null
  }
}

function Disable-InventoryStartup {
  Assert-InventoryRuntimeEntry
  if (Test-Path -LiteralPath $InventoryStartupPath -PathType Leaf) {
    Remove-Item -LiteralPath $InventoryStartupPath -Force
  }
  Write-Output "Django 库存域已退出开机启动链；当前运行进程未改变。"
}

function Show-InventoryStatus {
  $reader = "stopped"
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress) {
      $reader = "running"
    } elseif (@(Get-PortListeners 8051).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  try {
    if (Resolve-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress) {
      $writer = "running"
    } elseif (@(Get-PortListeners 8052).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  $writerReady = "not_ready"
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $InventoryReaderHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8051" }).StatusCode -eq 200) {
      $readerReady = "ready"
    }
  } catch {}
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $InventoryWriterHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8052" }).StatusCode -eq 200) {
      $writerReady = "ready"
    }
  } catch {}
  $status = [pscustomobject][ordered]@{
    InventoryReader = $reader
    InventoryWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) }
  else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-InventoryCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-InventoryRoles } }
    "Start" { Invoke-WithServiceMutex { Start-InventoryStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-InventoryStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-InventoryStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-InventoryStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-InventoryStartup } }
  }
} catch {
  Write-LauncherEvent "ERROR" "inventory_action_failed" $_.Exception.Message
  throw
}
