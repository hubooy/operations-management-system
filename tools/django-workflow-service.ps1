[CmdletBinding()]
param(
  [ValidateSet(
    "ConfigureCredentials", "ProvisionRoles", "Start", "Stop", "Status",
    "EnableStartup", "DisableStartup", "EnableWeeklyReport", "DisableWeeklyReport",
    "RunWeeklyReport", "ForceWeeklyReport", "DryRunWeeklyReport"
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

$WorkflowCredentialPath = Join-Path $RuntimeRoot "secrets\workflow-credentials.dpapi.json"
$WorkflowReaderPidPath = Join-Path $RunDirectory "django-workflow-reader.pid.json"
$WorkflowWriterPidPath = Join-Path $RunDirectory "django-workflow-writer.pid.json"
$WorkflowReaderHealthUrl = "http://127.0.0.1:8061/health/ready"
$WorkflowWriterHealthUrl = "http://127.0.0.1:8062/health/ready"
$WorkflowStartupPath = Join-Path $RuntimeRoot "workflow-service-enabled.json"
$WorkflowReaderMaxBodyBytes = 1048576
$WorkflowWriterMaxBodyBytes = 1048576
$MinimumPostgresConnectionsForWorkflow = 120

function Assert-WorkflowRuntimeEntry([string]$LifecycleAclToken = "") {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "运营事务新品服务操作必须从受保护的 runtime app 控制器执行；请先运行 DeployApp"
  }
  if (Test-OrchestratedLifecycleAclContext $LifecycleAclToken) {
    Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_reused" "domain=workflow"
    return
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
}

function Assert-WorkflowPortsFree([string]$Operation) {
  if (@(Get-PortListeners 8061).Count -gt 0 -or @(Get-PortListeners 8062).Count -gt 0) {
    throw "$Operation 前必须先停止 Django 运营事务新品 reader/writer"
  }
  if (Resolve-OwnedProcess "django-workflow-reader" $WorkflowReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 运营事务新品 reader"
  }
  if (Resolve-OwnedProcess "django-workflow-writer" $WorkflowWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django 运营事务新品 writer"
  }
}

function Read-WorkflowCredentials {
  $payload = Read-JsonFile $WorkflowCredentialPath "Django 运营事务新品 DPAPI 凭据库"
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "version", "databaseWorkflowReader", "databaseWorkflowWriter", "createdAt"
      )) -or [int]$payload.version -ne 1) {
    throw "Django 运营事务新品 DPAPI 凭据库结构无效"
  }
  $reader = Unprotect-Value ([string]$payload.databaseWorkflowReader) "databaseWorkflowReader"
  $writer = Unprotect-Value ([string]$payload.databaseWorkflowWriter) "databaseWorkflowWriter"
  Assert-StrongSecret $reader "databaseWorkflowReader"
  Assert-StrongSecret $writer "databaseWorkflowWriter"
  return [pscustomobject]@{
    ReaderPassword = $reader
    WriterPassword = $writer
  }
}

function Configure-WorkflowCredentials {
  Assert-WorkflowRuntimeEntry
  Assert-WorkflowPortsFree "ConfigureCredentials"
  if (Test-Path -LiteralPath $WorkflowCredentialPath -PathType Leaf) {
    Read-WorkflowCredentials | Out-Null
    Write-Output "Django 运营事务新品 DPAPI 凭据已存在且通过校验；未轮换。"
    return
  }
  $reader = New-RandomSecret
  $writer = New-RandomSecret
  try {
    Write-AtomicJson $WorkflowCredentialPath ([ordered]@{
      version = 1
      databaseWorkflowReader = Protect-Value $reader
      databaseWorkflowWriter = Protect-Value $writer
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-LauncherEvent "INFO" "workflow_credentials_configured"
    Write-Output "Django 运营事务新品 reader/writer DPAPI 凭据已创建；未配置数据库角色。"
  } finally {
    $reader = $null
    $writer = $null
  }
}

function Provision-WorkflowRoles {
  Assert-WorkflowRuntimeEntry
  Assert-WorkflowPortsFree "ProvisionRoles"
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝配置运营事务新品角色" }
  $runtimeSecrets = Read-Secrets
  $workflowSecrets = Read-WorkflowCredentials
  $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  $superuser = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_WORKFLOW_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_WORKFLOW_WRITER_PASSWORD", "Process")
  try {
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url `
      "postgres" $superuser "teruisi_workflow_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_WORKFLOW_READER_PASSWORD = $workflowSecrets.ReaderPassword
    $env:TERUISI_PROVISION_WORKFLOW_WRITER_PASSWORD = $workflowSecrets.WriterPassword
    $code = @'
import os

import psycopg
from psycopg import sql

roles = {
    "teruisi_workflow_reader": os.environ["TERUISI_PROVISION_WORKFLOW_READER_PASSWORD"],
    "teruisi_workflow_writer": os.environ["TERUISI_PROVISION_WORKFLOW_WRITER_PASSWORD"],
}
reader_tables = (
    "workflow_data_revisions",
    "workflow_write_authority",
    "workflow_operations_write_authority",
    "workflow_new_product_projects",
    "workflow_new_product_targets",
    "workflow_new_product_stages",
    "workflow_new_product_activities",
    "workflow_new_product_lines",
    "workflow_new_product_line_codes",
    "workflow_new_product_weekly_report_config",
    "workflow_new_product_weekly_deliveries",
    "workflow_tasks",
    "workflow_task_comments",
    "workflow_task_activity_logs",
    "workflow_task_reminders",
    "workflow_task_templates",
    "workflow_task_entity_links",
    "workflow_task_attachments",
    "workflow_operation_records",
    "workflow_operation_activities",
    "sales_order_lines",
    "sales_import_batches",
    "erp_product_master",
)
writer_privileges = {
    "workflow_data_revisions": ("SELECT", "UPDATE"),
    "workflow_write_authority": ("SELECT",),
    "workflow_operations_write_authority": ("SELECT",),
    "workflow_write_request_receipts": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "workflow_new_product_projects": ("SELECT", "INSERT", "UPDATE"),
    "workflow_new_product_targets": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "workflow_new_product_stages": ("SELECT", "INSERT", "UPDATE"),
    "workflow_new_product_activities": ("SELECT", "INSERT"),
    "workflow_new_product_lines": ("SELECT", "INSERT", "UPDATE"),
    "workflow_new_product_line_codes": ("SELECT", "INSERT", "UPDATE"),
    "workflow_new_product_weekly_report_config": ("SELECT", "UPDATE"),
    "workflow_new_product_weekly_deliveries": ("SELECT", "INSERT", "UPDATE"),
    "workflow_tasks": ("SELECT", "INSERT", "UPDATE"),
    "workflow_task_comments": ("SELECT", "INSERT"),
    "workflow_task_activity_logs": ("SELECT", "INSERT"),
    "workflow_task_reminders": ("SELECT", "INSERT", "UPDATE"),
    "workflow_task_templates": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "workflow_task_entity_links": ("SELECT", "INSERT", "DELETE"),
    "workflow_task_attachments": ("SELECT", "INSERT", "DELETE"),
    "workflow_attachment_cleanup_queue": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "workflow_operation_records": ("SELECT", "INSERT", "UPDATE"),
    "workflow_operation_activities": ("SELECT", "INSERT"),
    "sales_order_lines": ("SELECT",),
    "sales_import_batches": ("SELECT",),
    "erp_product_master": ("SELECT",),
}

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

    cursor.execute(sql.SQL("GRANT SELECT ON {} TO teruisi_workflow_reader").format(
        sql.SQL(",").join(sql.Identifier(name) for name in reader_tables)
    ))
    for table, privileges in writer_privileges.items():
        cursor.execute(sql.SQL("GRANT {} ON {} TO teruisi_workflow_writer").format(
            sql.SQL(",").join(sql.SQL(value) for value in privileges),
            sql.Identifier(table),
        ))

    from system_datasets.permissions import grant_columns
    grant_columns(cursor, "workflow")
    cursor.execute("ALTER ROLE teruisi_workflow_reader SET default_transaction_read_only=on")
    cursor.execute("ALTER ROLE teruisi_workflow_writer RESET default_transaction_read_only")
    for role in roles:
        cursor.execute(
            "SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname=%s",
            (role,),
        )
        flags = cursor.fetchone()
        if flags is None or any(flags):
            raise RuntimeError("workflow runtime role attributes are excessive")
        cursor.execute(
            "SELECT has_schema_privilege(%s,'public','CREATE'),"
            "has_database_privilege(%s,current_database(),'CREATE')",
            (role, role),
        )
        if any(cursor.fetchone()):
            raise RuntimeError("workflow runtime role retains DDL privileges")

    cursor.execute(
        "SELECT n.nspname,c.relname,"
        "has_table_privilege('teruisi_workflow_writer',c.oid,'INSERT'),"
        "has_table_privilege('teruisi_workflow_writer',c.oid,'UPDATE'),"
        "has_table_privilege('teruisi_workflow_writer',c.oid,'DELETE'),"
        "has_table_privilege('teruisi_workflow_writer',c.oid,'TRUNCATE'),"
        "has_any_column_privilege('teruisi_workflow_writer',c.oid,'INSERT'),"
        "has_any_column_privilege('teruisi_workflow_writer',c.oid,'UPDATE') "
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
            raise RuntimeError(f"workflow writer DML escaped allowlist: {schema}.{table}")
connection.close()
'@
    $launcher = ConvertTo-PythonBase64Launcher $code "workflow_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog `
      (Join-Path $LogDirectory "workflow-role-provision.$RunId.log") `
      "workflow_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) {
      throw "配置运营事务新品最小权限数据库角色失败（$(Get-NativeFailureSummary $nativeRun)）"
    }
    Write-LauncherEvent "INFO" "workflow_database_roles_provisioned"
    Write-Output "Django 运营事务新品 reader/writer 最小权限角色已配置；其他域未改变。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_WORKFLOW_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_WORKFLOW_WRITER_PASSWORD", $previousWriter, "Process")
    $runtimeSecrets = $null
    $workflowSecrets = $null
    $superuser = $null
  }
}

function Get-WorkflowWriteAuthority([object]$RuntimeSecrets, [object]$WorkflowSecrets) {
  $writerUrl = Database-Url `
    "teruisi_workflow_writer" $WorkflowSecrets.WriterPassword `
    "teruisi_workflow_authority_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection
from workflow.models import WorkflowOperationsWriteAuthority, WorkflowWriteAuthority

authority = WorkflowWriteAuthority.objects.filter(id=1).first()
operations = WorkflowOperationsWriteAuthority.objects.filter(id=1).first()
with connection.cursor() as cursor:
    cursor.execute("SHOW max_connections")
    max_connections = int(cursor.fetchone()[0])
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
    "migrationRunId": authority.migration_verify_run_id if authority else "",
    "operationsStatus": operations.status if operations else "missing",
    "operationsAuthorityEpoch": str(operations.authority_epoch) if operations and operations.authority_epoch else "",
    "operationsCutoverId": operations.cutover_id if operations else "",
    "operationsMigrationRunId": operations.migration_verify_run_id if operations else "",
    "maxConnections": max_connections,
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "workflow_authority_probe.py"
  $payload = Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "migration_writer" $false $WorkflowReaderMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess `
        $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 运营事务新品写入权威"
    }
  $writerUrl = $null
  if (-not (Test-ExactObjectPropertyNames $payload @(
        "status", "authorityEpoch", "cutoverId", "migrationRunId",
        "operationsStatus", "operationsAuthorityEpoch", "operationsCutoverId",
        "operationsMigrationRunId", "maxConnections"
      ))) {
    throw "PostgreSQL 运营事务新品写入权威探针结构无效"
  }
  if ([int]$payload.maxConnections -lt $MinimumPostgresConnectionsForWorkflow) {
    throw "PostgreSQL max_connections 低于运营事务新品正式运行所需容量；拒绝启动运营事务新品服务"
  }
  if ([string]$payload.status -cnotin @("disabled", "postgres")) {
    throw "PostgreSQL 运营事务新品写入权威状态无效"
  }
  if ([string]$payload.operationsStatus -cnotin @("disabled", "postgres")) {
    throw "PostgreSQL 运营事务全板块写入权威状态无效"
  }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.migrationRunId -match "^workflow-[0-9a-f]{32}$")
    )) {
    throw "PostgreSQL 运营事务新品写入权威证据不完整"
  }
  if ([string]$payload.operationsStatus -ceq "postgres" -and (
      -not ([string]$payload.operationsAuthorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.operationsCutoverId -match "^[A-Za-z0-9._:-]{8,128}$") -or
      -not ([string]$payload.operationsMigrationRunId -match "^workflow-ops-[0-9a-f]{32}$")
    )) {
    throw "PostgreSQL 运营事务全板块写入权威证据不完整"
  }
  return $payload
}

function Start-WorkflowReader([object]$RuntimeSecrets, [object]$WorkflowSecrets, [switch]$DeferReady) {
  $arguments = @(
    "--listen=127.0.0.1:8061", "--threads=6", "--connection-limit=60",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-workflow-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$WorkflowReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-workflow-reader" $Waitress $arguments
  if (Resolve-OwnedProcess "django-workflow-reader" $WorkflowReaderPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "workflow-reader" $WorkflowReaderHealthUrl "127.0.0.1:8061"
    return $false
  }
  if (@(Get-PortListeners 8061).Count -gt 0) { throw "端口 8061 被非本部署服务占用" }
  Remove-OldServiceLogs "django-workflow-reader"
  $readerUrl = Database-Url `
    "teruisi_workflow_reader" $WorkflowSecrets.ReaderPassword `
    "teruisi_django_workflow_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $readerUrl "workflow_reader" $true $WorkflowReaderMaxBodyBytes "" "" {
      Start-ManagedProcess "django-workflow-reader" $Waitress $arguments $BackendRoot `
        $WorkflowReaderPidPath $fingerprint `
        (Join-Path $LogDirectory "django-workflow-reader.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-workflow-reader.$RunId.stderr.log") | Out-Null
    }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "workflow-reader" $WorkflowReaderHealthUrl "127.0.0.1:8061"
    return $true
  } catch {
    Stop-OwnedProcess "django-workflow-reader" $WorkflowReaderPidPath $Waitress
    throw
  }
}

function Start-WorkflowWriter(
  [object]$RuntimeSecrets,
  [object]$WorkflowSecrets,
  [object]$Authority
) {
  if (
    [string]$Authority.status -cne "postgres" -or
    [string]$Authority.operationsStatus -cne "postgres"
  ) {
    throw "PostgreSQL 尚未成为运营事务完整板块唯一写入源；拒绝启动运营事务 writer"
  }
  $arguments = @(
    "--listen=127.0.0.1:8062", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-workflow-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$WorkflowWriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-workflow-writer" $Waitress $arguments
  if (Resolve-OwnedProcess "django-workflow-writer" $WorkflowWriterPidPath $Waitress $arguments $fingerprint) {
    Wait-DjangoReady "workflow-writer" $WorkflowWriterHealthUrl "127.0.0.1:8062"
    return $false
  }
  if (@(Get-PortListeners 8062).Count -gt 0) { throw "端口 8062 被非本部署服务占用" }
  Remove-OldServiceLogs "django-workflow-writer"
  $writerUrl = Database-Url `
    "teruisi_workflow_writer" $WorkflowSecrets.WriterPassword `
    "teruisi_django_workflow_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment `
    $RuntimeSecrets $writerUrl "workflow_writer" $false $WorkflowWriterMaxBodyBytes `
    ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
      Start-ManagedProcess "django-workflow-writer" $Waitress $arguments $BackendRoot `
        $WorkflowWriterPidPath $fingerprint `
        (Join-Path $LogDirectory "django-workflow-writer.$RunId.stdout.log") `
        (Join-Path $LogDirectory "django-workflow-writer.$RunId.stderr.log") | Out-Null
    } -WorkflowOperationsAuthorityEpoch ([string]$Authority.operationsAuthorityEpoch) `
      -WorkflowOperationsCutoverId ([string]$Authority.operationsCutoverId)
  $writerUrl = $null
  try {
    Wait-DjangoReady "workflow-writer" $WorkflowWriterHealthUrl "127.0.0.1:8062"
    return $true
  } catch {
    Stop-OwnedProcess "django-workflow-writer" $WorkflowWriterPidPath $Waitress
    throw
  }
}

function Start-WorkflowStack([string]$LifecycleAclToken = "") {
  Assert-WorkflowRuntimeEntry $LifecycleAclToken
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动运营事务新品服务" }
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $runtimeSecrets = Read-Secrets
  $workflowSecrets = Read-WorkflowCredentials
  $readerStarted = $false
  $writerStarted = $false
  try {
    $authority = Get-WorkflowWriteAuthority $runtimeSecrets $workflowSecrets
    if (Test-Path -LiteralPath $WorkflowStartupPath -PathType Leaf) {
      $startup = Read-JsonFile $WorkflowStartupPath "Django 运营事务新品开机启动凭据"
      if (-not (Test-ExactObjectPropertyNames $startup @(
            "version", "authorityEpoch", "cutoverId", "migrationRunId",
            "operationsAuthorityEpoch", "operationsCutoverId", "operationsMigrationRunId",
            "enabledAt"
          )) -or
          [int]$startup.version -ne 2 -or
          [string]$startup.authorityEpoch -cne [string]$authority.authorityEpoch -or
          [string]$startup.cutoverId -cne [string]$authority.cutoverId -or
          [string]$startup.migrationRunId -cne [string]$authority.migrationRunId -or
          [string]$startup.operationsAuthorityEpoch -cne [string]$authority.operationsAuthorityEpoch -or
          [string]$startup.operationsCutoverId -cne [string]$authority.operationsCutoverId -or
          [string]$startup.operationsMigrationRunId -cne [string]$authority.operationsMigrationRunId) {
        throw "Django 运营事务新品开机启动凭据与当前 PostgreSQL authority 不一致"
      }
    }
    $readerStarted = Start-WorkflowReader $runtimeSecrets $workflowSecrets -DeferReady
    if (
      [string]$authority.status -ceq "postgres" -and
      [string]$authority.operationsStatus -ceq "postgres"
    ) {
      $writerStarted = Start-WorkflowWriter $runtimeSecrets $workflowSecrets $authority
    }
    Wait-DjangoReady "workflow-reader" $WorkflowReaderHealthUrl "127.0.0.1:8061"
    if (
      [string]$authority.status -ceq "postgres" -and
      [string]$authority.operationsStatus -ceq "postgres"
    ) {
      Wait-DjangoReady "workflow-writer" $WorkflowWriterHealthUrl "127.0.0.1:8062"
      Write-Output "Django 运营事务完整板块服务已就绪：reader=http://127.0.0.1:8061 writer=http://127.0.0.1:8062。"
    } else {
      Write-Output "Django 运营事务 reader 已就绪；完整板块写权尚未全部激活，writer 保持停止。"
    }
  } catch {
    $original = $_.Exception
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-workflow-writer" $WorkflowWriterPidPath $Waitress } catch {}
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-workflow-reader" $WorkflowReaderPidPath $Waitress } catch {}
    }
    throw $original
  } finally {
    $runtimeSecrets = $null
    $workflowSecrets = $null
  }
}

function Stop-WorkflowStack([string]$LifecycleAclToken = "") {
  Assert-WorkflowRuntimeEntry $LifecycleAclToken
  Stop-OwnedProcess "django-workflow-writer" $WorkflowWriterPidPath $Waitress
  Stop-OwnedProcess "django-workflow-reader" $WorkflowReaderPidPath $Waitress
  Write-Output "Django 运营事务新品 reader/writer 已停止；销售、财务、网店、市场、ERP 与 PostgreSQL 未改变。"
}

function Enable-WorkflowStartup {
  Assert-WorkflowRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启用运营事务新品开机启动" }
  $runtimeSecrets = Read-Secrets
  $workflowSecrets = Read-WorkflowCredentials
  try {
    $authority = Get-WorkflowWriteAuthority $runtimeSecrets $workflowSecrets
    if (
      [string]$authority.status -cne "postgres" -or
      [string]$authority.operationsStatus -cne "postgres"
    ) {
      throw "只有 PostgreSQL 已取得运营事务完整板块唯一写权后才能启用开机启动"
    }
    Start-WorkflowStack
    Write-AtomicJson $WorkflowStartupPath ([ordered]@{
      version = 2
      authorityEpoch = [string]$authority.authorityEpoch
      cutoverId = [string]$authority.cutoverId
      migrationRunId = [string]$authority.migrationRunId
      operationsAuthorityEpoch = [string]$authority.operationsAuthorityEpoch
      operationsCutoverId = [string]$authority.operationsCutoverId
      operationsMigrationRunId = [string]$authority.operationsMigrationRunId
      enabledAt = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Set-RuntimeAcl
    Write-Output "Django 运营事务新品域已加入现有受控开机启动链。"
  } finally {
    $runtimeSecrets = $null
    $workflowSecrets = $null
  }
}

function Disable-WorkflowStartup {
  Assert-WorkflowRuntimeEntry
  if (Test-Path -LiteralPath $WorkflowStartupPath -PathType Leaf) {
    Remove-Item -LiteralPath $WorkflowStartupPath -Force
  }
  Write-Output "Django 运营事务新品域已退出开机启动链；当前运行进程未改变。"
}

function Invoke-NewProductWeeklyReport([bool]$DryRun, [bool]$Force = $false) {
  Assert-WorkflowRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝执行新品销售周报" }
  $runtimeSecrets = Read-Secrets
  $workflowSecrets = Read-WorkflowCredentials
  try {
    $authority = Get-WorkflowWriteAuthority $runtimeSecrets $workflowSecrets
    if (
      [string]$authority.status -cne "postgres" -or
      [string]$authority.operationsStatus -cne "postgres"
    ) {
      throw "PostgreSQL 尚未成为运营事务完整板块唯一写入源；拒绝执行新品销售周报"
    }
    $writerUrl = Database-Url `
      "teruisi_workflow_writer" $workflowSecrets.WriterPassword `
      "teruisi_new_product_weekly_report" $WriterStatementTimeoutMs
    $arguments = @((Join-Path $BackendRoot "manage.py"), "new_product_weekly_report")
    if ($DryRun) {
      $arguments += "--dry-run"
    } else {
      $arguments += "--send"
      if ($Force) { $arguments += "--force" }
    }
    $payload = Invoke-WithDjangoEnvironment `
      $runtimeSecrets $writerUrl "workflow_writer" $false $WorkflowWriterMaxBodyBytes `
      ([string]$authority.authorityEpoch) ([string]$authority.cutoverId) {
        $nativeRun = Invoke-BoundedNativeProcess $Python $arguments $BackendRoot
        return ConvertFrom-UniqueNativeJson $nativeRun "执行新品销售周报"
      } -WorkflowOperationsAuthorityEpoch ([string]$authority.operationsAuthorityEpoch) `
        -WorkflowOperationsCutoverId ([string]$authority.operationsCutoverId)
    if ($RequestedJson) { Write-Output ($payload | ConvertTo-Json -Depth 8 -Compress) }
    else { $payload | Format-List }
  } finally {
    $writerUrl = $null
    $runtimeSecrets = $null
    $workflowSecrets = $null
  }
}

function Set-NewProductWeeklyReportEnabled([bool]$Enabled) {
  Assert-WorkflowRuntimeEntry
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝修改新品销售周报配置" }
  $runtimeSecrets = Read-Secrets
  $workflowSecrets = Read-WorkflowCredentials
  try {
    $authority = Get-WorkflowWriteAuthority $runtimeSecrets $workflowSecrets
    if (
      [string]$authority.status -cne "postgres" -or
      [string]$authority.operationsStatus -cne "postgres"
    ) {
      throw "PostgreSQL 尚未成为运营事务完整板块唯一写入源；拒绝修改新品销售周报配置"
    }
    $writerUrl = Database-Url `
      "teruisi_workflow_writer" $workflowSecrets.WriterPassword `
      "teruisi_new_product_weekly_report_config" $WriterStatementTimeoutMs
    $arguments = @((Join-Path $BackendRoot "manage.py"), "configure_new_product_weekly_report")
    $arguments += $(if ($Enabled) { "--enable" } else { "--disable" })
    $payload = Invoke-WithDjangoEnvironment `
      $runtimeSecrets $writerUrl "workflow_writer" $false $WorkflowWriterMaxBodyBytes `
      ([string]$authority.authorityEpoch) ([string]$authority.cutoverId) {
        $nativeRun = Invoke-BoundedNativeProcess $Python $arguments $BackendRoot
        return ConvertFrom-UniqueNativeJson $nativeRun "修改新品销售周报配置"
      } -WorkflowOperationsAuthorityEpoch ([string]$authority.operationsAuthorityEpoch) `
        -WorkflowOperationsCutoverId ([string]$authority.operationsCutoverId)
    if ($RequestedJson) { Write-Output ($payload | ConvertTo-Json -Depth 8 -Compress) }
    else { $payload | Format-List }
  } finally {
    $writerUrl = $null
    $runtimeSecrets = $null
    $workflowSecrets = $null
  }
}

function Show-WorkflowStatus {
  $reader = "stopped"
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-workflow-reader" $WorkflowReaderPidPath $Waitress) {
      $reader = "running"
    } elseif (@(Get-PortListeners 8061).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  try {
    if (Resolve-OwnedProcess "django-workflow-writer" $WorkflowWriterPidPath $Waitress) {
      $writer = "running"
    } elseif (@(Get-PortListeners 8062).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  $writerReady = "not_ready"
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $WorkflowReaderHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8061" }).StatusCode -eq 200) {
      $readerReady = "ready"
    }
  } catch {}
  try {
    if ((Invoke-WebRequest -UseBasicParsing -Uri $WorkflowWriterHealthUrl -TimeoutSec 2 `
          -Headers @{ Host = "127.0.0.1:8062" }).StatusCode -eq 200) {
      $writerReady = "ready"
    }
  } catch {}
  $status = [pscustomobject][ordered]@{
    WorkflowReader = $reader
    WorkflowWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($RequestedJson) { Write-Output ($status | ConvertTo-Json -Compress) }
  else { $status | Format-List }
}

try {
  switch ($Action) {
    "ConfigureCredentials" { Invoke-WithServiceMutex { Configure-WorkflowCredentials } }
    "ProvisionRoles" { Invoke-WithServiceMutex { Provision-WorkflowRoles } }
    "Start" { Invoke-WithServiceMutex { Start-WorkflowStack $OrchestratedLifecycleAclToken } }
    "Stop" { Invoke-WithServiceMutex { Stop-WorkflowStack $OrchestratedLifecycleAclToken } }
    "Status" { Show-WorkflowStatus }
    "EnableStartup" { Invoke-WithServiceMutex { Enable-WorkflowStartup } }
    "DisableStartup" { Invoke-WithServiceMutex { Disable-WorkflowStartup } }
    "EnableWeeklyReport" { Invoke-WithServiceMutex { Set-NewProductWeeklyReportEnabled $true } }
    "DisableWeeklyReport" { Invoke-WithServiceMutex { Set-NewProductWeeklyReportEnabled $false } }
    "RunWeeklyReport" { Invoke-WithServiceMutex { Invoke-NewProductWeeklyReport $false $false } }
    "ForceWeeklyReport" { Invoke-WithServiceMutex { Invoke-NewProductWeeklyReport $false $true } }
    "DryRunWeeklyReport" { Invoke-WithServiceMutex { Invoke-NewProductWeeklyReport $true $false } }
  }
} catch {
  Write-LauncherEvent "ERROR" "workflow_action_failed" $_.Exception.Message
  throw
}
