[CmdletBinding()]
param(
  [ValidateSet("Backup", "Verify", "RestoreRehearsal", "Prune", "Status", "ProtectedAiPreflight")]
  [string]$Action = "Status",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$BackupDirectory = "",
  [string]$ApprovedManifestSha256 = "",
  [string]$RehearsalId = "",
  [ValidateRange(55432, 55999)]
  [int]$RehearsalPort = 55432,
  [ValidateRange(7, 3650)]
  [int]$RetentionDays = 30,
  [ValidateRange(3, 365)]
  [int]$MinimumSuccessfulBackups = 7,
  [switch]$Execute,
  [switch]$ConfirmedIsolatedRestore,
  [switch]$ConfirmedPrune
)

$ErrorActionPreference = "Stop"
$MaintenanceUtf8NoBom = [Text.UTF8Encoding]::new($false)
$MaintenanceFixedRuntimeRoot = "D:\teruisi-runtime\django-sales"
$MaintenanceBackupVersion = "teruisi-postgres-daily-backup-v1"
$MaintenanceRestoreVersion = "teruisi-postgres-restore-rehearsal-v1"
$MaintenancePruneVersion = "teruisi-postgres-backup-prune-v1"
$MaintenanceRehearsalRoles = @(
  "teruisi_sales_owner",
  "teruisi_sales_reader",
  "teruisi_sales_writer",
  "teruisi_erp_reference_reader",
  "teruisi_erp_reference_writer",
  "teruisi_finance_reader",
  "teruisi_finance_writer",
  "teruisi_netshop_reader",
  "teruisi_netshop_writer",
  "teruisi_market_reader",
  "teruisi_market_writer",
  "teruisi_products_reader",
  "teruisi_products_writer",
  "teruisi_inventory_reader",
  "teruisi_inventory_writer",
  "teruisi_workflow_reader",
  "teruisi_workflow_writer",
  "teruisi_customer_service_reader",
  "teruisi_customer_service_writer",
  "teruisi_bi_reader",
  "teruisi_access_control_reader",
  "teruisi_access_control_writer",
  "teruisi_ai_reader",
  "teruisi_ai_writer"
)
$MaintenanceRequest = [pscustomobject][ordered]@{
  Action = $Action
  RuntimeRoot = $RuntimeRoot
  BackupDirectory = $BackupDirectory
  ApprovedManifestSha256 = $ApprovedManifestSha256
  RehearsalId = $RehearsalId
  RehearsalPort = $RehearsalPort
  RetentionDays = $RetentionDays
  MinimumSuccessfulBackups = $MinimumSuccessfulBackups
  Execute = $Execute.IsPresent
  ConfirmedIsolatedRestore = $ConfirmedIsolatedRestore.IsPresent
  ConfirmedPrune = $ConfirmedPrune.IsPresent
}

function Test-MaintenanceFullyQualifiedPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  try { [void][IO.Path]::GetFullPath($Path) } catch { return $false }
  if ([IO.Path]::DirectorySeparatorChar -eq "\") {
    return $Path -match "^[A-Za-z]:[\\/]" -or
      $Path -match "^[\\/]{2}[^\\/]+[\\/]+[^\\/]+(?:[\\/]|$)"
  }
  return $Path.StartsWith("/", [StringComparison]::Ordinal)
}

function Get-MaintenanceCanonicalPath([string]$Path) {
  return [IO.Path]::GetFullPath($Path).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
  )
}

function Assert-MaintenanceExactPropertySet(
  [object]$Value,
  [string[]]$Expected,
  [string]$Label
) {
  if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
    throw "$Label 必须是 JSON 对象"
  }
  $actual = @($Value.PSObject.Properties.Name | Sort-Object)
  $wanted = @($Expected | Sort-Object)
  if ($actual.Count -ne $wanted.Count) { throw "$Label 字段集合无效" }
  for ($index = 0; $index -lt $wanted.Count; $index++) {
    if ([string]$actual[$index] -cne [string]$wanted[$index]) {
      throw "$Label 字段集合无效"
    }
  }
}

function Test-MaintenanceInteger([object]$Value) {
  if ($null -eq $Value) { return $false }
  return [Type]::GetTypeCode($Value.GetType()) -in @(
    [TypeCode]::SByte, [TypeCode]::Byte,
    [TypeCode]::Int16, [TypeCode]::UInt16,
    [TypeCode]::Int32, [TypeCode]::UInt32,
    [TypeCode]::Int64, [TypeCode]::UInt64
  )
}

function Assert-MaintenanceNoReparsePoints([string]$Path, [string]$Label) {
  $root = Get-Item -LiteralPath $Path -Force
  if (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Label 不得是重解析点"
  }
  if (-not $root.PSIsContainer) { return }
  $directories = [Collections.Queue]::new()
  $directories.Enqueue($root)
  while ($directories.Count -gt 0) {
    $directory = $directories.Dequeue()
    foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force)) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label 不得包含重解析点"
      }
      if ($item.PSIsContainer) { $directories.Enqueue($item) }
    }
  }
}

function Resolve-MaintenanceDirectChildDirectory(
  [string]$Path,
  [string]$Parent,
  [string]$NamePattern,
  [string]$Label
) {
  if (-not (Test-MaintenanceFullyQualifiedPath $Path)) {
    throw "$Label 必须是绝对路径"
  }
  $resolved = Get-MaintenanceCanonicalPath $Path
  $resolvedParent = Get-MaintenanceCanonicalPath $Parent
  if (-not (Test-Path -LiteralPath $resolved -PathType Container) -or
      (Get-MaintenanceCanonicalPath (Split-Path -Parent $resolved)) -ine $resolvedParent -or
      [IO.Path]::GetFileName($resolved) -cnotmatch $NamePattern) {
    throw "$Label 不是受控根目录的合法直接子目录"
  }
  Assert-MaintenanceNoReparsePoints $resolved $Label
  return $resolved
}

function Write-MaintenanceAtomicText([string]$Path, [string]$Value) {
  $directory = Split-Path -Parent $Path
  $temporary = Join-Path $directory (
    ".{0}.{1}.tmp" -f [IO.Path]::GetFileName($Path), [Guid]::NewGuid().ToString("N")
  )
  try {
    [IO.File]::WriteAllText($temporary, $Value, $MaintenanceUtf8NoBom)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force
    }
  }
}

function Invoke-MaintenancePgEnvironment(
  [hashtable]$Values,
  [scriptblock]$Operation
) {
  $names = @(
    "PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD",
    "PGAPPNAME", "PGOPTIONS", "PGCLIENTENCODING"
  )
  $previous = @{}
  foreach ($name in $names) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
  }
  try {
    foreach ($name in $Values.Keys) {
      if ($name -notin $names) { throw "拒绝设置未批准的 libpq 环境变量" }
      [Environment]::SetEnvironmentVariable($name, [string]$Values[$name], "Process")
    }
    & $Operation
  } finally {
    foreach ($name in $names) {
      [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
    }
  }
}

function Invoke-MaintenanceMutex([scriptblock]$Operation) {
  $name = "Local\TERUISI-DjangoPostgresMaintenance-" + (
    Get-Sha256Text (Get-MaintenanceCanonicalPath $MaintenanceRequest.RuntimeRoot)
  ).Substring(0, 20)
  $mutex = [Threading.Mutex]::new($false, $name)
  $acquired = $false
  try {
    try {
      $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds(5))
    } catch [Threading.AbandonedMutexException] {
      $acquired = $true
    }
    if (-not $acquired) { throw "另一个 PostgreSQL 维护操作仍在运行" }
    & $Operation
  } finally {
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
  }
}

function Get-MaintenanceBackupRoot([bool]$Create = $false) {
  $root = Assert-RuntimeChildPath (
    Join-Path $MaintenanceRequest.RuntimeRoot "backups\postgres-daily"
  )
  if ($Create -and -not (Test-Path -LiteralPath $root)) {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
  }
  if (Test-Path -LiteralPath $root -PathType Container) {
    $item = Get-Item -LiteralPath $root -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "PostgreSQL 日常备份根目录不得是重解析点"
    }
  } elseif ($Create) {
    throw "PostgreSQL 日常备份根目录无效"
  }
  return $root
}

function Assert-MaintenanceRuntimeContext {
  Assert-DeployedApplication
  Assert-RuntimeRootAclHardened
  $config = Get-ServiceConfig
  if ([string]$config.postgresAddress -cne "127.0.0.1:5432") {
    throw "Django 本机 PostgreSQL 配置不符合固定回环契约"
  }
  $expectedTool = Join-Path $InstalledAppRoot "tools\postgres-consistent-backup.py"
  if (-not (Test-Path -LiteralPath $expectedTool -PathType Leaf)) {
    throw "runtime app 缺少一致性备份工具"
  }
  return $expectedTool
}

function Assert-MaintenanceEvidence(
  [object]$Evidence,
  [string]$ExpectedDatabase,
  [string]$ExpectedUser,
  [int]$ExpectedPort
) {
  $hasBiMigration = @($Evidence.migrations | Where-Object {
      [string]$_.app -ceq "bi" -and [string]$_.name -ceq "0001_initial"
    }).Count -gt 0
  $hasAccessControlMigration = @($Evidence.migrations | Where-Object {
      [string]$_.app -ceq "access_control" -and [string]$_.name -ceq "0001_initial"
    }).Count -gt 0
  $hasAccessControl = $null -ne $Evidence.PSObject.Properties["accessControl"]
  $hasAiMigration = @($Evidence.migrations | Where-Object { [string]$_.app -ceq "ai_assistant" -and [string]$_.name -ceq "0001_initial" }).Count -gt 0
  $hasAi = $null -ne $Evidence.PSObject.Properties["aiAssistant"]
  if ($hasAiMigration -ne $hasAi) { throw "PostgreSQL AI 域证据字段不完整" }
  $workspaceMigration = @($Evidence.migrations | Where-Object {
    [string]$_.app -ceq "ai_assistant" -and [string]$_.name -ceq "0006_conversation_workspaces"
  })
  $dingTalkMigration = @($Evidence.migrations | Where-Object {
    [string]$_.app -ceq "ai_assistant" -and [string]$_.name -ceq "0007_dingtalk_readonly"
  })
  $dingTalkSettingsMigration = @($Evidence.migrations | Where-Object {
    [string]$_.app -ceq "ai_assistant" -and [string]$_.name -ceq "0008_dingtalk_settings"
  })
  if ($dingTalkSettingsMigration.Count -gt 0 -and ($dingTalkMigration.Count -ne 1 -or -not $hasAiMigration -or -not $hasAi)) {
    throw "PostgreSQL 钉钉设置迁移缺少完整前置证据"
  }
  if ($dingTalkMigration.Count -gt 0 -and ($workspaceMigration.Count -ne 1 -or -not $hasAiMigration -or -not $hasAi)) {
    throw "PostgreSQL 钉钉问数迁移缺少完整前置证据"
  }
  if ($workspaceMigration.Count -gt 0 -and (-not $hasAiMigration -or -not $hasAi)) {
    throw "PostgreSQL AI 会话迁移缺少完整领域证据"
  }

  if ($hasAccessControlMigration -ne $hasAccessControl) {
    throw "PostgreSQL 权限域证据字段不完整"
  }
  $hasErpReferenceMigration = @($Evidence.migrations | Where-Object {
      [string]$_.app -ceq "erp_reference" -and [string]$_.name -like "0002_*"
    }).Count -gt 0
  $hasErpReferenceAuthority = $null -ne $Evidence.PSObject.Properties["erpReferenceWriteAuthority"]
  if ($hasErpReferenceMigration -ne $hasErpReferenceAuthority) {
    throw "PostgreSQL ERP 主数据证据字段不完整"
  }
  $hasNetshopRevisions = $null -ne $Evidence.PSObject.Properties["netshopRevisions"]
  $hasNetshopAuthority = $null -ne $Evidence.PSObject.Properties["netshopWriteAuthority"]
  if ($hasNetshopRevisions -ne $hasNetshopAuthority) {
    throw "PostgreSQL 网店证据字段不完整"
  }
  $hasMarketRevisions = $null -ne $Evidence.PSObject.Properties["marketRevisions"]
  $hasMarketAuthority = $null -ne $Evidence.PSObject.Properties["marketWriteAuthority"]
  if ($hasMarketRevisions -ne $hasMarketAuthority) {
    throw "PostgreSQL 市场证据字段不完整"
  }
  $hasProductsRevisions = $null -ne $Evidence.PSObject.Properties["productsRevisions"]
  $hasProductsAuthority = $null -ne $Evidence.PSObject.Properties["productsWriteAuthority"]
  if ($hasProductsRevisions -ne $hasProductsAuthority) {
    throw "PostgreSQL 商品经营证据字段不完整"
  }
  $hasInventoryRevisions = $null -ne $Evidence.PSObject.Properties["inventoryRevisions"]
  $hasInventoryAuthority = $null -ne $Evidence.PSObject.Properties["inventoryWriteAuthority"]
  if ($hasInventoryRevisions -ne $hasInventoryAuthority) {
    throw "PostgreSQL 库存证据字段不完整"
  }
  $hasWorkflowRevisions = $null -ne $Evidence.PSObject.Properties["workflowRevisions"]
  $hasWorkflowAuthority = $null -ne $Evidence.PSObject.Properties["workflowWriteAuthority"]
  $hasWorkflowOperationsAuthority = $null -ne $Evidence.PSObject.Properties["workflowOperationsWriteAuthority"]
  if ($hasWorkflowRevisions -ne $hasWorkflowAuthority -or
      $hasWorkflowRevisions -ne $hasWorkflowOperationsAuthority) {
    throw "PostgreSQL 运营事务新品证据字段不完整"
  }
  $hasCustomerServiceRevisions = $null -ne $Evidence.PSObject.Properties["customerServiceRevisions"]
  $hasCustomerServiceAuthority = $null -ne $Evidence.PSObject.Properties["customerServiceWriteAuthority"]
  if ($hasCustomerServiceRevisions -ne $hasCustomerServiceAuthority) {
    throw "PostgreSQL 客服证据字段不完整"
  }
  $evidenceProperties = @(
    "database", "tables", "migrations", "revisions", "writeAuthority",
    "contentSha256", "canonicalSha256"
  )
  if ($hasErpReferenceAuthority) {
    $evidenceProperties += @("erpReferenceWriteAuthority")
  }
  if ($hasNetshopRevisions) {
    $evidenceProperties += @("netshopRevisions", "netshopWriteAuthority")
  }
  if ($hasMarketRevisions) {
    $evidenceProperties += @("marketRevisions", "marketWriteAuthority")
  }
  if ($hasProductsRevisions) {
    $evidenceProperties += @("productsRevisions", "productsWriteAuthority")
  }
  if ($hasInventoryRevisions) {
    $evidenceProperties += @("inventoryRevisions", "inventoryWriteAuthority")
  }
  if ($hasWorkflowRevisions) {
    $evidenceProperties += @(
      "workflowRevisions", "workflowWriteAuthority", "workflowOperationsWriteAuthority"
    )
  }
  if ($hasCustomerServiceRevisions) {
    $evidenceProperties += @("customerServiceRevisions", "customerServiceWriteAuthority")
  }
  if ($hasAccessControl) { $evidenceProperties += @("accessControl") }
  if ($hasAi) { $evidenceProperties += @("aiAssistant") }
  Assert-MaintenanceExactPropertySet $Evidence $evidenceProperties "PostgreSQL 证据"
  foreach ($digest in @(
    [string]$Evidence.contentSha256,
    [string]$Evidence.canonicalSha256
  )) {
    if ($digest -cnotmatch "^[0-9a-f]{64}$") {
      throw "PostgreSQL 证据摘要无效"
    }
  }

  Assert-MaintenanceExactPropertySet $Evidence.database @(
    "name", "user", "serverAddress", "serverPort", "inRecovery",
    "serverVersionNumber"
  ) "PostgreSQL 身份证据"
  if ([string]$Evidence.database.name -cne $ExpectedDatabase -or
      [string]$Evidence.database.user -cne $ExpectedUser -or
      [string]$Evidence.database.serverAddress -notin @("127.0.0.1", "::1") -or
      -not (Test-MaintenanceInteger $Evidence.database.serverPort) -or
      [int]$Evidence.database.serverPort -ne $ExpectedPort -or
      [bool]$Evidence.database.inRecovery -or
      -not (Test-MaintenanceInteger $Evidence.database.serverVersionNumber) -or
      [int]$Evidence.database.serverVersionNumber -lt 170000) {
    throw "PostgreSQL 身份证据不符合批准目标"
  }

  if ($Evidence.tables -isnot [pscustomobject]) {
    throw "PostgreSQL 表行数证据无效"
  }
  $requiredTables = @(
    "django_migrations", "sales_data_revisions", "sales_import_batches",
    "sales_order_lines", "sales_write_authority", "erp_product_master"
  )
  if ($hasErpReferenceAuthority) {
    $requiredTables += @(
      "erp_combo_items", "erp_reference_import_batches_pg",
      "erp_reference_import_scope_heads", "erp_reference_import_fingerprints",
      "erp_reference_import_attempts", "erp_reference_write_authority",
      "erp_reference_write_request_receipts", "erp_reference_migration_runs",
      "erp_reference_raw_upload_sessions", "erp_reference_raw_upload_chunks"
    )
  }
  if ($hasBiMigration) {
    $requiredTables += @("bi_migration_runs")
  }
  if ($hasAi) {
    $requiredTables += @("ai_agent_checkpoints", "ai_agent_events", "ai_agent_jobs", "ai_agent_provider_dispatches", "ai_agent_provider_results", "ai_agent_tool_dispatches", "ai_agent_tool_results", "ai_analysis_runs", "ai_artifact_deliveries", "ai_artifacts", "ai_channel_callback_events", "ai_channels", "ai_chat_provider_dispatches", "ai_chat_request_receipts", "ai_conversation_deletion_audits", "ai_conversation_messages", "ai_conversation_scopes", "ai_conversations", "ai_data_revisions", "ai_knowledge_entries", "ai_memory_audit_logs", "ai_memory_commit_guards", "ai_memory_entries", "ai_migration_runs", "ai_models", "ai_mutation_audits", "ai_space_admin_audits", "ai_space_asset_cleanup_queue", "ai_space_asset_favorites", "ai_space_asset_payloads", "ai_space_assets", "ai_space_dispatch_receipts", "ai_space_dispatch_results", "ai_space_job_items", "ai_space_jobs", "ai_space_model_profiles", "ai_space_schema_upgrades", "ai_space_templates", "ai_system_settings", "ai_tool_audit_logs", "ai_workflow_events", "ai_workflow_node_runs", "ai_workflow_runs", "ai_write_authority", "ai_write_request_receipts")
    if ($dingTalkSettingsMigration.Count -gt 0) {
      $requiredTables += @("ai_dingtalk_settings")
    }
    if ($dingTalkMigration.Count -gt 0) {
      if ($workspaceMigration.Count -ne 1) { throw "钉钉问数迁移缺少前置会话迁移" }
      $requiredTables += @("ai_dingtalk_sessions", "ai_dingtalk_receipts")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0010_dingtalk_schedules" }).Count -gt 0) {
      $requiredTables += @("ai_dingtalk_schedules", "ai_dingtalk_schedule_runs")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0011_prompt_settings" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0010_dingtalk_schedules" }).Count -ne 1) { throw "AI 配置迁移缺少前置定时任务迁移" }
      $requiredTables += @("ai_prompt_settings_revisions")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0012_report_library" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0011_prompt_settings" }).Count -ne 1) { throw "AI 报告迁移缺少前置配置迁移" }
      $requiredTables += @("ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0013_dingtalk_schedule_media" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0012_report_library" }).Count -ne 1) { throw "AI 媒体任务迁移缺少前置报告迁移" }
      $requiredTables += @("ai_dingtalk_schedules", "ai_dingtalk_schedule_runs")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0014_business_evidence" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0013_dingtalk_schedule_media" }).Count -ne 1) { throw "AI 经营证据迁移缺少前置媒体迁移" }
      $requiredTables += @("ai_business_evidence_runs", "ai_business_evidence_chunks")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0016_business_files" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0015_business_collection" }).Count -ne 1 -or @($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0014_business_evidence" }).Count -ne 1) { throw "AI 报告文件迁移缺少前置采集迁移" }
      $requiredTables += @("ai_business_file_runs", "ai_business_file_chunks")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0019_business_source_directory" }).Count -gt 0) {
      foreach ($sourceDirectoryPredecessor in @("0014_business_evidence", "0015_business_collection", "0016_business_files", "0017_business_file_renderer", "0018_business_excel_renderer")) {
        if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq $sourceDirectoryPredecessor }).Count -ne 1) { throw "AI 来源目录迁移缺少完整前置证据/文件迁移" }
      }
      $requiredTables += @("ai_business_evidence_sources")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0032_business_source_tool_receipts" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0031_business_daily_v3_source_pages" }).Count -ne 1) { throw "AI v3工具收据迁移缺少日来源前驱迁移" }
      $requiredTables += @("ai_business_source_tool_receipts")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0034_business_v3_report_intent" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0033_business_v3_parent_seal" }).Count -ne 1) { throw "AI v3暂停报告意图迁移缺少封存前驱迁移" }
      $requiredTables += @("ai_business_v3_report_intents")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0035_business_v4_ledger" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0034_business_v3_report_intent" }).Count -ne 1) { throw "AI v4物理账迁移缺少v3暂停意图前驱迁移" }
      $requiredTables += @("ai_business_v4_runs", "ai_business_v4_sources", "ai_business_v4_chunks", "ai_business_v4_tool_receipts")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0020_business_volume_files" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0019_business_source_directory" }).Count -ne 1) { throw "AI 多卷文件迁移缺少前置来源目录迁移" }
      $requiredTables += @("ai_business_volume_chunks")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0021_business_budget_plans" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0020_business_volume_files" }).Count -ne 1) { throw "AI 固定预算迁移缺少前置多卷迁移" }
      $requiredTables += @("ai_business_budget_plans")
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0024_business_screening_runtime" }).Count -gt 0) {
      if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0023_business_screening_storage" }).Count -ne 1) { throw "AI 筛查执行迁移缺少前置固定筛查存储迁移" }
    }
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq "0023_business_screening_storage" }).Count -gt 0) {
      foreach ($screeningPredecessor in @("0022_business_integrated_reports", "0021_business_budget_plans")) {
        if (@($Evidence.migrations | Where-Object { $_.app -ceq "ai_assistant" -and $_.name -ceq $screeningPredecessor }).Count -ne 1) { throw "AI 固定筛查存储缺少前置集成报告迁移" }
      }
      $requiredTables += @("ai_business_screening_runs", "ai_business_screening_pages")
    }
    if ($workspaceMigration.Count -gt 0) {
      $requiredTables += @("ai_conversation_workspaces")
    }
    Assert-MaintenanceExactPropertySet $Evidence.aiAssistant @("revision", "sourceDigest", "status", "authorityEpoch", "cutoverId", "migrationRunId") "AI 域证据"
    $aiEvidence = $Evidence.aiAssistant
    if (-not (Test-MaintenanceInteger $aiEvidence.revision) -or [int64]$aiEvidence.revision -lt 0 -or [string]$aiEvidence.status -cnotin @("d1", "postgres")) { throw "AI 版本或 authority 无效" }
    if ([string]$aiEvidence.migrationRunId -ne "") {
      if ([string]$aiEvidence.migrationRunId -cnotmatch "^ai-apply-[0-9a-f]{32}$" -or [string]$aiEvidence.sourceDigest -cnotmatch "^[0-9a-f]{64}$" -or [int64]$aiEvidence.revision -lt 1) { throw "AI 采用证据无效" }
    } elseif ([int64]$aiEvidence.revision -ne 0 -or [string]$aiEvidence.sourceDigest -ne "") { throw "AI 未采用状态包含数据变化" }
    if ([string]$aiEvidence.status -ceq "postgres") {
      if ([int64]$aiEvidence.revision -lt 1 -or [string]$aiEvidence.authorityEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or [string]$aiEvidence.cutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or [string]$aiEvidence.migrationRunId -cnotmatch "^ai-apply-[0-9a-f]{32}$") { throw "AI PostgreSQL 激活证据不完整" }
    } elseif ([string]$aiEvidence.authorityEpoch -ne "" -or [string]$aiEvidence.cutoverId -ne "") { throw "AI 未激活状态包含激活证据" }
  }
  if ($hasAccessControl) {
    $requiredTables += @(
      "access_control_users", "access_control_roles", "access_control_permission_audits",
      "access_control_data_revisions", "access_control_write_authority",
      "access_control_write_request_receipts", "access_control_migration_runs"
    )
    Assert-MaintenanceExactPropertySet $Evidence.accessControl @(
      "revision", "sourceDigest", "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "权限域写入权威证据"
    $accessEvidence = $Evidence.accessControl
    if (-not (Test-MaintenanceInteger $accessEvidence.revision) -or
        [int64]$accessEvidence.revision -lt 0 -or
        [string]$accessEvidence.sourceDigest -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$accessEvidence.status -cnotin @("d1", "postgres") -or
        ([string]$accessEvidence.migrationRunId -ne "" -and
          [string]$accessEvidence.migrationRunId -cnotmatch "^access-control-[0-9a-f]{32}$")) {
      throw "权限域版本或迁移证据无效"
    }
    if ([string]$accessEvidence.status -ceq "postgres") {
      if ([int64]$accessEvidence.revision -lt 1 -or
          [string]$accessEvidence.authorityEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          [string]$accessEvidence.cutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          [string]$accessEvidence.migrationRunId -cnotmatch "^access-control-[0-9a-f]{32}$") {
        throw "权限域 PostgreSQL 写入权威证据不完整"
      }
    } elseif ([string]$accessEvidence.authorityEpoch -ne "" -or [string]$accessEvidence.cutoverId -ne "") {
      throw "未激活的权限域写入权威包含激活证据"
    }
  }
  if ($hasNetshopRevisions) {
    $requiredTables += @(
      "netshop_data_revisions", "netshop_import_batches",
      "netshop_rows", "netshop_write_authority"
    )
  }
  if ($hasMarketRevisions) {
    $requiredTables += @(
      "market_data_revisions", "market_import_batches",
      "market_ranking_entries", "market_write_authority"
    )
  }
  if ($hasProductsRevisions) {
    $requiredTables += @(
      "product_data_revisions", "product_shipping_rate_import_batches",
      "product_shipping_rates", "product_inventory_projection", "product_write_authority"
    )
  }
  if ($hasInventoryRevisions) {
    $requiredTables += @(
      "inventory_data_revisions", "inventory_import_batches",
      "inventory_stock_lines", "inventory_age_lines", "inventory_write_authority",
      "inventory_operating_settings", "replenishment_plan_items",
      "inventory_replenishment_group_deliveries", "inventory_guangdong_monitor_items",
      "inventory_guangdong_supplier_cycles", "inventory_guangdong_monitor_audits"
    )
  }
  if ($hasWorkflowRevisions) {
    $requiredTables += @(
      "workflow_data_revisions", "workflow_write_authority",
      "workflow_operations_write_authority",
      "workflow_new_product_projects", "workflow_new_product_targets",
      "workflow_new_product_stages", "workflow_new_product_activities",
      "workflow_new_product_lines", "workflow_new_product_line_codes",
      "workflow_new_product_weekly_report_config", "workflow_new_product_weekly_deliveries",
      "workflow_tasks", "workflow_task_comments", "workflow_task_activity_logs",
      "workflow_task_reminders", "workflow_task_templates", "workflow_task_entity_links",
      "workflow_task_attachments", "workflow_attachment_cleanup_queue",
      "workflow_operation_records", "workflow_operation_activities"
    )
  }
  if ($hasCustomerServiceRevisions) {
    $requiredTables += @(
      "customer_service_data_revisions", "customer_service_import_batches",
      "customer_service_conversations", "customer_service_deletion_audits",
      "customer_service_import_scope_heads", "customer_service_import_fingerprints",
      "customer_service_import_attempts", "customer_service_write_authority",
      "customer_service_write_request_receipts", "customer_service_migration_runs",
      "customer_service_raw_upload_sessions", "customer_service_raw_upload_chunks"
    )
  }
  $tableNames = @($Evidence.tables.PSObject.Properties.Name)
  $salesOptionsMigration = @($Evidence.migrations | Where-Object { $_.app -ceq "sales" -and $_.name -ceq "0010_analysis_options" }).Count
  $salesOptionsTables = @("sales_analysis_options", "sales_analysis_options_state")
  if ($salesOptionsMigration -eq 1) {
    if (@($Evidence.migrations | Where-Object { $_.app -ceq "sales" -and $_.name -ceq "0009_postgres_raw_upload_payload" }).Count -ne 1) { throw "ERP选项迁移缺少前置迁移" }
    $requiredTables += $salesOptionsTables
  } elseif (@($tableNames | Where-Object { $_ -cin $salesOptionsTables }).Count -gt 0) { throw "ERP选项表缺少对应迁移依据" }
  $marketOptionsMigration = @($Evidence.migrations | Where-Object { $_.app -ceq "market" -and $_.name -ceq "0006_analysis_options" }).Count
  $marketFacetMigration = @($Evidence.migrations | Where-Object { $_.app -ceq "market" -and $_.name -ceq "0005_filter_facet_indexes" }).Count
  $marketOptionsTables = @("market_analysis_options", "market_analysis_options_state")
  if ($marketFacetMigration -eq 1 -and @($Evidence.migrations | Where-Object { $_.app -ceq "market" -and $_.name -ceq "0004_projection_sync_fencing" }).Count -ne 1) { throw "市场筛选索引迁移缺少前置迁移" }
  if ($marketOptionsMigration -eq 1) {
    if ($marketFacetMigration -ne 1) { throw "市场选项迁移缺少前置筛选索引迁移" }
    $requiredTables += $marketOptionsTables
  } elseif (@($tableNames | Where-Object { $_ -cin $marketOptionsTables }).Count -gt 0) { throw "市场选项表缺少对应迁移依据" }
  foreach ($required in $requiredTables) {
    if ($required -notin $tableNames) { throw "PostgreSQL 证据缺少关键表" }
  }
  foreach ($property in $Evidence.tables.PSObject.Properties) {
    if ($property.Name -cne "django_migrations" -and
        $property.Name -cnotmatch "^(?:sales|erp|finance|netshop|market|product|inventory|replenishment|workflow|customer_service|bi|access_control|ai)_[a-z0-9_]+$") {
      throw "PostgreSQL 证据包含越界表"
    }
    if ($property.Name -clike "ai_*" -and $property.Name -cnotin $requiredTables) {
      throw "PostgreSQL AI 表不在已采用的闭合清单中"
    }
    if (-not (Test-MaintenanceInteger $property.Value) -or
        [int64]$property.Value -lt 0) {
      throw "PostgreSQL 表行数证据无效"
    }
  }

  $migrations = @($Evidence.migrations)
  if ($migrations.Count -lt 1) { throw "Django migration 证据为空" }
  foreach ($migration in $migrations) {
    Assert-MaintenanceExactPropertySet $migration @("app", "name") "Django migration 证据"
    if ([string]$migration.app -cnotmatch "^[A-Za-z0-9_]{1,100}$" -or
        [string]$migration.name -cnotmatch "^[A-Za-z0-9_]{1,200}$") {
      throw "Django migration 身份无效"
    }
  }

  if ($Evidence.revisions -isnot [pscustomobject] -or
      $null -eq $Evidence.revisions.PSObject.Properties["sales"] -or
      $null -eq $Evidence.revisions.PSObject.Properties["erp"]) {
    throw "PostgreSQL revision 证据不完整"
  }
  foreach ($property in $Evidence.revisions.PSObject.Properties) {
    if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$" -or
        -not (Test-MaintenanceInteger $property.Value) -or
        [int64]$property.Value -lt 0) {
      throw "PostgreSQL revision 证据无效"
    }
  }

  Assert-MaintenanceExactPropertySet $Evidence.writeAuthority @(
    "status", "authorityEpoch", "cutoverId"
  ) "销售写入权威证据"
  if ([string]$Evidence.writeAuthority.status -cne "active" -or
      [string]$Evidence.writeAuthority.authorityEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
      [string]$Evidence.writeAuthority.cutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
    throw "销售写入权威证据无效"
  }

  if ($hasErpReferenceAuthority) {
    Assert-MaintenanceExactPropertySet $Evidence.erpReferenceWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "ERP 主数据写入权威证据"
    $erpStatus = [string]$Evidence.erpReferenceWriteAuthority.status
    $erpEpoch = [string]$Evidence.erpReferenceWriteAuthority.authorityEpoch
    $erpCutoverId = [string]$Evidence.erpReferenceWriteAuthority.cutoverId
    $erpRunId = [string]$Evidence.erpReferenceWriteAuthority.migrationRunId
    if ($erpStatus -notin @("d1", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($erpRunId) -and
          $erpRunId -cnotmatch "^erp-reference-[0-9a-f]{32}$")) {
      throw "ERP 主数据写入权威证据无效"
    }
    if ($erpStatus -ceq "postgres") {
      if ($erpEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $erpCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $erpRunId -cnotmatch "^erp-reference-[0-9a-f]{32}$") {
        throw "ERP 主数据 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($erpEpoch) -or
              -not [string]::IsNullOrEmpty($erpCutoverId)) {
      throw "未激活 ERP 主数据权威包含激活身份"
    }
  }

  if ($hasNetshopRevisions) {
    if ($Evidence.netshopRevisions -isnot [pscustomobject] -or
        $null -eq $Evidence.netshopRevisions.PSObject.Properties["netshop"]) {
      throw "PostgreSQL 网店 revision 证据不完整"
    }
    foreach ($property in $Evidence.netshopRevisions.PSObject.Properties) {
      if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$") {
        throw "PostgreSQL 网店 revision 身份无效"
      }
      Assert-MaintenanceExactPropertySet $property.Value @(
        "revision", "sourceDigest"
      ) "PostgreSQL 网店 revision 证据"
      if (-not (Test-MaintenanceInteger $property.Value.revision) -or
          [int64]$property.Value.revision -lt 0 -or
          [string]$property.Value.sourceDigest -cnotmatch "^[0-9a-f]{64}$") {
        throw "PostgreSQL 网店 revision 证据无效"
      }
    }
    Assert-MaintenanceExactPropertySet $Evidence.netshopWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "网店写入权威证据"
    $netshopStatus = [string]$Evidence.netshopWriteAuthority.status
    $netshopEpoch = [string]$Evidence.netshopWriteAuthority.authorityEpoch
    $netshopCutoverId = [string]$Evidence.netshopWriteAuthority.cutoverId
    $netshopRunId = [string]$Evidence.netshopWriteAuthority.migrationRunId
    if ($netshopStatus -notin @("d1", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($netshopRunId) -and
          $netshopRunId -cnotmatch "^netshop-[0-9a-f]{24}$")) {
      throw "网店写入权威证据无效"
    }
    if ($netshopStatus -ceq "postgres") {
      if ($netshopEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $netshopCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $netshopRunId -cnotmatch "^netshop-[0-9a-f]{24}$") {
        throw "网店 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($netshopEpoch) -or
              -not [string]::IsNullOrEmpty($netshopCutoverId)) {
      throw "未激活的网店写入权威包含激活证据"
    }
  }

  if ($hasMarketRevisions) {
    if ($Evidence.marketRevisions -isnot [pscustomobject] -or
        $null -eq $Evidence.marketRevisions.PSObject.Properties["market"]) {
      throw "PostgreSQL 市场 revision 证据不完整"
    }
    foreach ($property in $Evidence.marketRevisions.PSObject.Properties) {
      if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$") {
        throw "PostgreSQL 市场 revision 身份无效"
      }
      Assert-MaintenanceExactPropertySet $property.Value @(
        "revision", "sourceDigest"
      ) "PostgreSQL 市场 revision 证据"
      if (-not (Test-MaintenanceInteger $property.Value.revision) -or
          [int64]$property.Value.revision -lt 1 -or
          [string]$property.Value.sourceDigest -cnotmatch "^[0-9a-f]{64}$") {
        throw "PostgreSQL 市场 revision 证据无效"
      }
    }
    Assert-MaintenanceExactPropertySet $Evidence.marketWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "市场写入权威证据"
    $marketStatus = [string]$Evidence.marketWriteAuthority.status
    $marketEpoch = [string]$Evidence.marketWriteAuthority.authorityEpoch
    $marketCutoverId = [string]$Evidence.marketWriteAuthority.cutoverId
    $marketRunId = [string]$Evidence.marketWriteAuthority.migrationRunId
    if ($marketStatus -notin @("d1", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($marketRunId) -and
          $marketRunId -cnotmatch "^market-[0-9a-f]{24}$")) {
      throw "市场写入权威证据无效"
    }
    if ($marketStatus -ceq "postgres") {
      if ($marketEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $marketCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $marketRunId -cnotmatch "^market-[0-9a-f]{24}$") {
        throw "市场 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($marketEpoch) -or
              -not [string]::IsNullOrEmpty($marketCutoverId)) {
      throw "未激活的市场写入权威包含激活证据"
    }
  }

  if ($hasProductsRevisions) {
    if ($Evidence.productsRevisions -isnot [pscustomobject] -or
        $null -eq $Evidence.productsRevisions.PSObject.Properties["products"]) {
      throw "PostgreSQL 商品经营 revision 证据不完整"
    }
    foreach ($property in $Evidence.productsRevisions.PSObject.Properties) {
      if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$") {
        throw "PostgreSQL 商品经营 revision 身份无效"
      }
      Assert-MaintenanceExactPropertySet $property.Value @(
        "revision", "sourceDigest"
      ) "PostgreSQL 商品经营 revision 证据"
      $productDigest = [string]$property.Value.sourceDigest
      if (-not (Test-MaintenanceInteger $property.Value.revision) -or
          [int64]$property.Value.revision -lt 0 -or
          ([int64]$property.Value.revision -gt 0 -and $productDigest -cnotmatch "^[0-9a-f]{64}$")) {
        throw "PostgreSQL 商品经营 revision 证据无效"
      }
    }
    Assert-MaintenanceExactPropertySet $Evidence.productsWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "商品经营写入权威证据"
    $productsStatus = [string]$Evidence.productsWriteAuthority.status
    $productsEpoch = [string]$Evidence.productsWriteAuthority.authorityEpoch
    $productsCutoverId = [string]$Evidence.productsWriteAuthority.cutoverId
    $productsRunId = [string]$Evidence.productsWriteAuthority.migrationRunId
    if ($productsStatus -notin @("d1", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($productsRunId) -and
          $productsRunId -cnotmatch "^products-apply-[0-9a-f]{32}$")) {
      throw "商品经营写入权威证据无效"
    }
    if ($productsStatus -ceq "postgres") {
      if ([int64]$Evidence.productsRevisions.products.revision -lt 1 -or
          $productsEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $productsCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $productsRunId -cnotmatch "^products-apply-[0-9a-f]{32}$") {
        throw "商品经营 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($productsEpoch) -or
              -not [string]::IsNullOrEmpty($productsCutoverId)) {
      throw "未激活的商品经营写入权威包含激活证据"
    }
  }

  if ($hasInventoryRevisions) {
    if ($Evidence.inventoryRevisions -isnot [pscustomobject] -or
        $null -eq $Evidence.inventoryRevisions.PSObject.Properties["inventory"]) {
      throw "PostgreSQL 库存 revision 证据不完整"
    }
    foreach ($property in $Evidence.inventoryRevisions.PSObject.Properties) {
      if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$") {
        throw "PostgreSQL 库存 revision 身份无效"
      }
      Assert-MaintenanceExactPropertySet $property.Value @(
        "revision", "sourceDigest"
      ) "PostgreSQL 库存 revision 证据"
      $inventoryDigest = [string]$property.Value.sourceDigest
      if (-not (Test-MaintenanceInteger $property.Value.revision) -or
          [int64]$property.Value.revision -lt 0 -or
          ([int64]$property.Value.revision -gt 0 -and $inventoryDigest -cnotmatch "^[0-9a-f]{64}$")) {
        throw "PostgreSQL 库存 revision 证据无效"
      }
    }
    Assert-MaintenanceExactPropertySet $Evidence.inventoryWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "库存写入权威证据"
    $inventoryStatus = [string]$Evidence.inventoryWriteAuthority.status
    $inventoryEpoch = [string]$Evidence.inventoryWriteAuthority.authorityEpoch
    $inventoryCutoverId = [string]$Evidence.inventoryWriteAuthority.cutoverId
    $inventoryRunId = [string]$Evidence.inventoryWriteAuthority.migrationRunId
    if ($inventoryStatus -notin @("d1", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($inventoryRunId) -and
          $inventoryRunId -cnotmatch "^inventory-apply-[0-9a-f]{32}$")) {
      throw "库存写入权威证据无效"
    }
    if ($inventoryStatus -ceq "postgres") {
      if ([int64]$Evidence.inventoryRevisions.inventory.revision -lt 1 -or
          $inventoryEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $inventoryCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $inventoryRunId -cnotmatch "^inventory-apply-[0-9a-f]{32}$") {
        throw "库存 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($inventoryEpoch) -or
              -not [string]::IsNullOrEmpty($inventoryCutoverId)) {
      throw "未激活的库存写入权威包含激活证据"
    }
  }

  if ($hasWorkflowRevisions) {
    if ($Evidence.workflowRevisions -isnot [pscustomobject] -or
        $null -eq $Evidence.workflowRevisions.PSObject.Properties["workflow"]) {
      throw "PostgreSQL 运营事务新品 revision 证据不完整"
    }
    foreach ($property in $Evidence.workflowRevisions.PSObject.Properties) {
      if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$") {
        throw "PostgreSQL 运营事务新品 revision 身份无效"
      }
      Assert-MaintenanceExactPropertySet $property.Value @(
        "revision", "sourceDigest"
      ) "PostgreSQL 运营事务新品 revision 证据"
      $workflowDigest = [string]$property.Value.sourceDigest
      if (-not (Test-MaintenanceInteger $property.Value.revision) -or
          [int64]$property.Value.revision -lt 0 -or
          ([int64]$property.Value.revision -gt 0 -and $workflowDigest -cnotmatch "^[0-9a-f]{64}$")) {
        throw "PostgreSQL 运营事务新品 revision 证据无效"
      }
    }
    Assert-MaintenanceExactPropertySet $Evidence.workflowWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "运营事务新品写入权威证据"
    $workflowStatus = [string]$Evidence.workflowWriteAuthority.status
    $workflowEpoch = [string]$Evidence.workflowWriteAuthority.authorityEpoch
    $workflowCutoverId = [string]$Evidence.workflowWriteAuthority.cutoverId
    $workflowRunId = [string]$Evidence.workflowWriteAuthority.migrationRunId
    if ($workflowStatus -notin @("disabled", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($workflowRunId) -and
          $workflowRunId -cnotmatch "^workflow-[0-9a-f]{32}$")) {
      throw "运营事务新品写入权威证据无效"
    }
    if ($workflowStatus -ceq "postgres") {
      if ([int64]$Evidence.workflowRevisions.workflow.revision -lt 1 -or
          $workflowEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $workflowCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $workflowRunId -cnotmatch "^workflow-[0-9a-f]{32}$") {
        throw "运营事务新品 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($workflowEpoch) -or
              -not [string]::IsNullOrEmpty($workflowCutoverId)) {
      throw "未激活的运营事务新品写入权威包含激活证据"
    }
    Assert-MaintenanceExactPropertySet $Evidence.workflowOperationsWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "运营事务全板块写入权威证据"
    $workflowOperationsStatus = [string]$Evidence.workflowOperationsWriteAuthority.status
    $workflowOperationsEpoch = [string]$Evidence.workflowOperationsWriteAuthority.authorityEpoch
    $workflowOperationsCutoverId = [string]$Evidence.workflowOperationsWriteAuthority.cutoverId
    $workflowOperationsRunId = [string]$Evidence.workflowOperationsWriteAuthority.migrationRunId
    if ($workflowOperationsStatus -notin @("disabled", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($workflowOperationsRunId) -and
          $workflowOperationsRunId -cnotmatch "^workflow-ops-[0-9a-f]{32}$")) {
      throw "运营事务全板块写入权威证据无效"
    }
    if ($workflowOperationsStatus -ceq "postgres") {
      if ([int64]$Evidence.workflowRevisions.workflow.revision -lt 1 -or
          $workflowOperationsEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $workflowOperationsCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $workflowOperationsRunId -cnotmatch "^workflow-ops-[0-9a-f]{32}$") {
        throw "运营事务全板块 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($workflowOperationsEpoch) -or
              -not [string]::IsNullOrEmpty($workflowOperationsCutoverId)) {
      throw "未激活的运营事务全板块写入权威包含激活证据"
    }
  }

  if ($hasCustomerServiceRevisions) {
    if ($Evidence.customerServiceRevisions -isnot [pscustomobject] -or
        $null -eq $Evidence.customerServiceRevisions.PSObject.Properties["customer-service"]) {
      throw "PostgreSQL 客服 revision 证据不完整"
    }
    foreach ($property in $Evidence.customerServiceRevisions.PSObject.Properties) {
      if ($property.Name -cnotmatch "^[a-z][a-z0-9_-]{0,63}$") {
        throw "PostgreSQL 客服 revision 身份无效"
      }
      Assert-MaintenanceExactPropertySet $property.Value @(
        "revision", "sourceDigest"
      ) "PostgreSQL 客服 revision 证据"
      $customerServiceDigest = [string]$property.Value.sourceDigest
      if (-not (Test-MaintenanceInteger $property.Value.revision) -or
          [int64]$property.Value.revision -lt 0 -or
          ([int64]$property.Value.revision -gt 0 -and
            $customerServiceDigest -cnotmatch "^[0-9a-f]{64}$")) {
        throw "PostgreSQL 客服 revision 证据无效"
      }
    }
    Assert-MaintenanceExactPropertySet $Evidence.customerServiceWriteAuthority @(
      "status", "authorityEpoch", "cutoverId", "migrationRunId"
    ) "客服写入权威证据"
    $customerServiceStatus = [string]$Evidence.customerServiceWriteAuthority.status
    $customerServiceEpoch = [string]$Evidence.customerServiceWriteAuthority.authorityEpoch
    $customerServiceCutoverId = [string]$Evidence.customerServiceWriteAuthority.cutoverId
    $customerServiceRunId = [string]$Evidence.customerServiceWriteAuthority.migrationRunId
    if ($customerServiceStatus -notin @("d1", "postgres") -or
        (-not [string]::IsNullOrWhiteSpace($customerServiceRunId) -and
          $customerServiceRunId -cnotmatch "^customer-service-[0-9a-f]{32}$")) {
      throw "客服写入权威证据无效"
    }
    if ($customerServiceStatus -ceq "postgres") {
      if ([int64]$Evidence.customerServiceRevisions.'customer-service'.revision -lt 1 -or
          $customerServiceEpoch -cnotmatch "^[0-9a-fA-F-]{36}$" -or
          $customerServiceCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $customerServiceRunId -cnotmatch "^customer-service-[0-9a-f]{32}$") {
        throw "客服 PostgreSQL 写入权威证据不完整"
      }
    } elseif (-not [string]::IsNullOrEmpty($customerServiceEpoch) -or
              -not [string]::IsNullOrEmpty($customerServiceCutoverId)) {
      throw "未激活的客服写入权威包含激活证据"
    }
  }
}

function Assert-MaintenanceBackupPayload([object]$Payload) {
  Assert-MaintenanceExactPropertySet $Payload @(
    "version", "status", "snapshotIdSha256", "evidence", "nativeDiagnostic"
  ) "一致性备份工具结果"
  if ([string]$Payload.version -cne "teruisi-postgres-consistent-backup-v1" -or
      [string]$Payload.status -cne "completed" -or
      [string]$Payload.snapshotIdSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "一致性备份工具结果无效"
  }
  Assert-MaintenanceExactPropertySet $Payload.nativeDiagnostic @(
    "exitCode", "outputBytes", "capturedBytes", "outputTruncated", "outputSha256"
  ) "pg_dump 诊断"
  if ([int]$Payload.nativeDiagnostic.exitCode -ne 0 -or
      [string]$Payload.nativeDiagnostic.outputSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "pg_dump 诊断未通过"
  }
  Assert-MaintenanceEvidence $Payload.evidence "teruisi_sales" "teruisi_sales_owner" 5432
}

function Read-MaintenanceArchive(
  [string]$Directory,
  [string]$ApprovedSha256 = "",
  [switch]$RequireCurrentDeployment
) {
  $manifestPath = Join-Path $Directory "backup-manifest.json"
  $sidecarPath = Join-Path $Directory "backup-manifest.json.sha256"
  $dumpPath = Join-Path $Directory "teruisi-sales.dump"
  $expectedFiles = @(
    "backup-manifest.json", "backup-manifest.json.sha256", "teruisi-sales.dump"
  )
  $actualFiles = @(
    Get-ChildItem -LiteralPath $Directory -Force | ForEach-Object {
      if ($_.PSIsContainer) { throw "PostgreSQL 备份目录不得包含子目录" }
      $_.Name
    } | Sort-Object
  )
  $wantedFiles = @($expectedFiles | Sort-Object)
  if ($actualFiles.Count -ne $wantedFiles.Count) {
    throw "PostgreSQL 备份文件集合无效"
  }
  for ($index = 0; $index -lt $wantedFiles.Count; $index++) {
    if ([string]$actualFiles[$index] -cne [string]$wantedFiles[$index]) {
      throw "PostgreSQL 备份文件集合无效"
    }
  }

  $manifestSha = Get-FileSha256 $manifestPath
  $sidecarSha = (Get-Content -Raw -LiteralPath $sidecarPath -Encoding UTF8).Trim()
  if ($manifestSha -cnotmatch "^[0-9a-f]{64}$" -or
      $sidecarSha -cne $manifestSha -or
      (-not [string]::IsNullOrWhiteSpace($ApprovedSha256) -and
        $ApprovedSha256 -cne $manifestSha)) {
    throw "PostgreSQL 备份 manifest 摘要未获批准或已变化"
  }

  $manifest = Read-JsonFile $manifestPath "PostgreSQL 备份 manifest"
  Assert-MaintenanceExactPropertySet $manifest @(
    "version", "status", "backupId", "createdAt", "completedAt", "database",
    "dump", "evidence", "software"
  ) "PostgreSQL 备份 manifest"
  if ([string]$manifest.version -cne $MaintenanceBackupVersion -or
      [string]$manifest.status -cne "completed" -or
      [string]$manifest.backupId -cnotmatch "^daily-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$") {
    throw "PostgreSQL 备份 manifest 身份无效"
  }
  $created = [DateTimeOffset]::MinValue
  $completed = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse([string]$manifest.createdAt, [ref]$created) -or
      -not [DateTimeOffset]::TryParse([string]$manifest.completedAt, [ref]$completed) -or
      $completed -lt $created) {
    throw "PostgreSQL 备份时间证据无效"
  }

  Assert-MaintenanceExactPropertySet $manifest.database @(
    "name", "host", "port", "sourceRole", "consistency"
  ) "PostgreSQL 备份数据库身份"
  if ([string]$manifest.database.name -cne "teruisi_sales" -or
      [string]$manifest.database.host -cne "127.0.0.1" -or
      [int]$manifest.database.port -ne 5432 -or
      [string]$manifest.database.sourceRole -cne "teruisi_sales_owner" -or
      [string]$manifest.database.consistency -cne "exported-snapshot") {
    throw "PostgreSQL 备份数据库身份无效"
  }

  Assert-MaintenanceExactPropertySet $manifest.dump @(
    "fileName", "sizeBytes", "sha256", "archiveEntryCount", "snapshotIdSha256"
  ) "PostgreSQL dump 证据"
  if ([string]$manifest.dump.fileName -cne "teruisi-sales.dump" -or
      -not (Test-MaintenanceInteger $manifest.dump.sizeBytes) -or
      [int64]$manifest.dump.sizeBytes -lt 1 -or
      [int64](Get-Item -LiteralPath $dumpPath).Length -ne [int64]$manifest.dump.sizeBytes -or
      [string]$manifest.dump.sha256 -cnotmatch "^[0-9a-f]{64}$" -or
      (Get-FileSha256 $dumpPath) -cne [string]$manifest.dump.sha256 -or
      -not (Test-MaintenanceInteger $manifest.dump.archiveEntryCount) -or
      [int]$manifest.dump.archiveEntryCount -lt 10 -or
      [string]$manifest.dump.snapshotIdSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "PostgreSQL dump 证据无效"
  }

  Assert-MaintenanceEvidence $manifest.evidence "teruisi_sales" "teruisi_sales_owner" 5432
  Assert-MaintenanceExactPropertySet $manifest.software @(
    "deploymentManifestSha256", "serviceConfigSha256", "serviceScriptSha256",
    "operatorScriptSha256", "evidenceToolSha256", "pgDumpSha256", "pgRestoreSha256"
  ) "PostgreSQL 备份软件证据"
  foreach ($property in $manifest.software.PSObject.Properties) {
    if ([string]$property.Value -cnotmatch "^[0-9a-f]{64}$") {
      throw "PostgreSQL 备份软件摘要无效"
    }
  }
  if ($RequireCurrentDeployment.IsPresent -and
      ((Get-FileSha256 $DeploymentManifestPath) -cne [string]$manifest.software.deploymentManifestSha256 -or
       (Get-FileSha256 $ConfigPath) -cne [string]$manifest.software.serviceConfigSha256)) {
    throw "PostgreSQL 备份与当前部署或服务配置不一致"
  }

  $pgRestore = Join-Path $PostgresBin "pg_restore.exe"
  $archiveRun = Invoke-BoundedNativeProcess $pgRestore @("--list", $dumpPath) $Directory
  if ($archiveRun.ExitCode -ne 0 -or
      @($archiveRun.Output).Count -ne [int]$manifest.dump.archiveEntryCount) {
    throw "PostgreSQL dump 归档目录复验失败（$(Get-NativeFailureSummary $archiveRun)）"
  }
  return [pscustomobject][ordered]@{
    Directory = $Directory
    ManifestPath = $manifestPath
    ManifestSha256 = $manifestSha
    DumpPath = $dumpPath
    Manifest = $manifest
  }
}

function Resolve-MaintenanceBackupArchive(
  [string]$RequestedDirectory,
  [string]$ApprovedSha256 = "",
  [switch]$RequireCurrentDeployment
) {
  $backupRoot = Get-MaintenanceBackupRoot $false
  if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) {
    throw "PostgreSQL 日常备份根目录不存在"
  }
  $directory = Resolve-MaintenanceDirectChildDirectory (
    $RequestedDirectory
  ) $backupRoot "^daily-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$" "PostgreSQL 备份目录"
  $archive = Read-MaintenanceArchive $directory $ApprovedSha256 $RequireCurrentDeployment
  if ([IO.Path]::GetFileName($directory) -cne [string]$archive.Manifest.backupId) {
    throw "PostgreSQL 备份目录与 manifest 身份不一致"
  }
  return $archive
}

function Remove-MaintenanceIncompleteDirectory(
  [string]$Directory,
  [string]$BackupRoot,
  [string]$ExpectedName
) {
  if (-not (Test-Path -LiteralPath $Directory)) { return }
  $resolved = Get-MaintenanceCanonicalPath $Directory
  if ((Get-MaintenanceCanonicalPath (Split-Path -Parent $resolved)) -ine
      (Get-MaintenanceCanonicalPath $BackupRoot) -or
      [IO.Path]::GetFileName($resolved) -cne $ExpectedName -or
      $ExpectedName -cnotmatch "^\.daily-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.[0-9a-f]{32}\.incomplete$") {
    throw "拒绝清理身份不明确的未发布备份目录"
  }
  Assert-MaintenanceNoReparsePoints $resolved "未发布 PostgreSQL 备份目录"
  Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Invoke-MaintenanceBackup {
  if (-not $MaintenanceRequest.Execute) {
    throw "创建日常 PostgreSQL 备份必须显式提供 -Execute"
  }
  $evidenceTool = Assert-MaintenanceRuntimeContext
  if (@(Get-PortListeners 5432).Count -ne 1) {
    throw "权威 PostgreSQL 当前未运行；日常备份不会自动启停服务"
  }
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) {
    throw "权威 PostgreSQL 未就绪；日常备份不会自动启停服务"
  }
  $protectedState = Invoke-MaintenanceProtectedAiPreflight
  if (@($protectedState.appliedProtectedMigrations).Count -gt 0) {
    throw "受保护 AI 迁移尚无正式备份契约；拒绝创建备份工作目录"
  }

  $backupRoot = Get-MaintenanceBackupRoot $true
  $timestamp = [DateTimeOffset]::UtcNow
  $backupId = "daily-{0}-{1}" -f (
    $timestamp.ToString("yyyyMMdd'T'HHmmss'Z'")
  ), ([Guid]::NewGuid().ToString("N").Substring(0, 12))
  $finalDirectory = Assert-RuntimeChildPath (Join-Path $backupRoot $backupId)
  if (Test-Path -LiteralPath $finalDirectory) { throw "目标日常备份目录已存在" }
  $nonce = [Guid]::NewGuid().ToString("N")
  $workingName = ".$backupId.$nonce.incomplete"
  $workingDirectory = Assert-RuntimeChildPath (Join-Path $backupRoot $workingName)
  $published = $false
  $secrets = $null
  try {
    New-Item -ItemType Directory -Path $workingDirectory | Out-Null
    $dumpPath = Join-Path $workingDirectory "teruisi-sales.dump"
    $pgDump = Join-Path $PostgresBin "pg_dump.exe"
    $pgRestore = Join-Path $PostgresBin "pg_restore.exe"
    foreach ($tool in @($Python, $pgDump, $pgRestore, $evidenceTool)) {
      if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw "PostgreSQL 日常备份缺少受控运行工具"
      }
    }

    $secrets = Read-Secrets
    $payload = Invoke-MaintenancePgEnvironment @{
      PGHOST = "127.0.0.1"
      PGPORT = "5432"
      PGUSER = "teruisi_sales_owner"
      PGDATABASE = "teruisi_sales"
      PGPASSWORD = $secrets.OwnerPassword
      PGAPPNAME = "teruisi_daily_backup"
      PGOPTIONS = "-c statement_timeout=1800000 -c idle_in_transaction_session_timeout=1860000"
      PGCLIENTENCODING = "UTF8"
    } {
      $run = Invoke-BoundedNativeProcess $Python @(
        $evidenceTool, "backup",
        "--pg-dump", $pgDump,
        "--output", $dumpPath,
        "--expected-database", "teruisi_sales",
        "--expected-user", "teruisi_sales_owner",
        "--port", "5432",
        "--timeout-seconds", "1800"
      ) $InstalledAppRoot
      return ConvertFrom-UniqueNativeJson $run "创建 PostgreSQL 一致性备份"
    }
    Assert-MaintenanceBackupPayload $payload

    $archiveRun = Invoke-BoundedNativeProcess $pgRestore @("--list", $dumpPath) $workingDirectory
    $archiveEntries = @($archiveRun.Output)
    if ($archiveRun.ExitCode -ne 0 -or $archiveEntries.Count -lt 10) {
      throw "PostgreSQL dump 归档目录验证失败（$(Get-NativeFailureSummary $archiveRun)）"
    }
    $manifest = [pscustomobject][ordered]@{
      version = $MaintenanceBackupVersion
      status = "completed"
      backupId = $backupId
      createdAt = $timestamp.ToString("o")
      completedAt = [DateTimeOffset]::UtcNow.ToString("o")
      database = [pscustomobject][ordered]@{
        name = "teruisi_sales"
        host = "127.0.0.1"
        port = 5432
        sourceRole = "teruisi_sales_owner"
        consistency = "exported-snapshot"
      }
      dump = [pscustomobject][ordered]@{
        fileName = "teruisi-sales.dump"
        sizeBytes = [int64](Get-Item -LiteralPath $dumpPath).Length
        sha256 = Get-FileSha256 $dumpPath
        archiveEntryCount = [int]$archiveEntries.Count
        snapshotIdSha256 = [string]$payload.snapshotIdSha256
      }
      evidence = $payload.evidence
      software = [pscustomobject][ordered]@{
        deploymentManifestSha256 = Get-FileSha256 $DeploymentManifestPath
        serviceConfigSha256 = Get-FileSha256 $ConfigPath
        serviceScriptSha256 = Get-FileSha256 $InstalledScriptPath
        operatorScriptSha256 = Get-FileSha256 $PSCommandPath
        evidenceToolSha256 = Get-FileSha256 $evidenceTool
        pgDumpSha256 = Get-FileSha256 $pgDump
        pgRestoreSha256 = Get-FileSha256 $pgRestore
      }
    }
    $manifestPath = Join-Path $workingDirectory "backup-manifest.json"
    Write-AtomicJson $manifestPath $manifest
    Write-MaintenanceAtomicText (
      Join-Path $workingDirectory "backup-manifest.json.sha256"
    ) ((Get-FileSha256 $manifestPath) + [Environment]::NewLine)
    Read-MaintenanceArchive $workingDirectory | Out-Null

    Move-Item -LiteralPath $workingDirectory -Destination $finalDirectory
    $published = $true
    $verified = Resolve-MaintenanceBackupArchive $finalDirectory (
      Get-FileSha256 (Join-Path $finalDirectory "backup-manifest.json")
    ) -RequireCurrentDeployment
    return [pscustomobject][ordered]@{
      status = "completed"
      backupId = $backupId
      backupDirectory = $finalDirectory
      manifestSha256 = $verified.ManifestSha256
      dumpSha256 = [string]$verified.Manifest.dump.sha256
      contentSha256 = [string]$verified.Manifest.evidence.contentSha256
      completedAt = [string]$verified.Manifest.completedAt
      serviceStateChanged = $false
    }
  } finally {
    $secrets = $null
    if (-not $published) {
      Remove-MaintenanceIncompleteDirectory $workingDirectory $backupRoot $workingName
    }
  }
}

function Invoke-MaintenanceProtectedAiPreflight {
  # Explicit read-only action. It never returns verifier key bytes or opens an archive.
  $evidenceTool = Assert-MaintenanceRuntimeContext
  if (@(Get-PortListeners 5432).Count -ne 1) {
    throw "权威 PostgreSQL 当前未运行；受保护 AI 预检不会启动服务"
  }
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) {
    throw "权威 PostgreSQL 未就绪；受保护 AI 预检不会启动服务"
  }
  $secrets = Read-Secrets
  try {
    $payload = Invoke-MaintenancePgEnvironment @{
      PGHOST = "127.0.0.1"
      PGPORT = "5432"
      PGUSER = "teruisi_sales_owner"
      PGDATABASE = "teruisi_sales"
      PGPASSWORD = $secrets.OwnerPassword
      PGAPPNAME = "teruisi_protected_ai_preflight"
      PGOPTIONS = "-c statement_timeout=15000 -c default_transaction_read_only=on"
      PGCLIENTENCODING = "UTF8"
    } {
      $run = Invoke-BoundedNativeProcess $Python @(
        $evidenceTool, "protected-preflight",
        "--expected-database", "teruisi_sales",
        "--expected-user", "teruisi_sales_owner",
        "--port", "5432"
      ) $InstalledAppRoot
      return ConvertFrom-UniqueNativeJson $run "受保护 AI 只读预检"
    }
    Assert-MaintenanceExactPropertySet $payload @(
      "version", "status", "readOnly", "appliedProtectedMigrations",
      "exactProtectedRoleCount", "issues"
    ) "受保护 AI 预检结果"
    if ([string]$payload.version -cne "teruisi-postgres-consistent-backup-v1" -or
        [string]$payload.status -cne "blocked" -or
        $payload.readOnly -cne $true -or
        @($payload.issues).Count -lt 1) {
      throw "受保护 AI 预检结果无效或意外放行"
    }
    return $payload
  } finally {
    $secrets = $null
  }
}

function Assert-MaintenanceProtectedArchiveUnsupported([object]$Manifest) {
  $protected = @($Manifest.evidence.migrations | Where-Object {
    [string]$_.app -ceq "ai_assistant" -and
    [string]$_.name -cmatch "^00(67|68|69|70|71|72|73|74|75|76|77|78|79)_business_"
  })
  if ($protected.Count -gt 0) {
    throw "受保护 AI 归档尚无角色、owner/ACL 与私钥隔离恢复契约；拒绝开始恢复演练"
  }
  $financeRaw = @($Manifest.evidence.migrations | Where-Object {
    [string]$_.app -ceq "finance" -and
    [string]$_.name -cin @("0005_raw_column_evidence_v2",
      "0006_raw_workbook_bytes_v2")
  })
  $financeRawTables = @("finance_raw_column_evidence_months",
    "finance_raw_column_evidence_columns", "finance_raw_column_evidence_cells",
    "finance_raw_workbook_attestations", "finance_raw_workbook_columns",
    "finance_raw_workbook_cells")
  $presentFinanceRaw = @($financeRawTables | Where-Object {
    $_ -cin @($Manifest.evidence.tables.PSObject.Properties.Name)
  })
  if ($financeRaw.Count -gt 0 -or $presentFinanceRaw.Count -gt 0) {
    throw "财报原始列证据侧车归档尚无正式角色与恢复契约；拒绝启动隔离恢复"
  }
}

function Assert-MaintenanceRehearsalListenerOwnership(
  [int]$Port,
  [string]$DataDirectory
) {
  $listeners = @(Get-PortListeners $Port)
  if ($listeners.Count -ne 1 -or
      [string]$listeners[0].LocalAddress -ne "127.0.0.1") {
    throw "隔离恢复 PostgreSQL 未严格绑定批准的回环端口"
  }
  $snapshot = Get-ProcessSnapshot ([int]$listeners[0].OwningProcess) 3
  $expectedExecutable = Get-MaintenanceCanonicalPath (Join-Path $PostgresBin "postgres.exe")
  if ($null -eq $snapshot -or $snapshot.ExecutablePath -ine $expectedExecutable) {
    throw "隔离恢复端口不是受控 PostgreSQL 17 进程"
  }
  $matches = [regex]::Matches(
    $snapshot.CommandLine,
    '(?:^|\s)(?:"-D"|-D)\s+(?:"([^"]+)"|(\S+))'
  )
  if ($matches.Count -ne 1) { throw "隔离恢复 PostgreSQL 缺少唯一数据目录" }
  $raw = if ($matches[0].Groups[1].Success) {
    $matches[0].Groups[1].Value
  } else {
    $matches[0].Groups[2].Value
  }
  if ((Get-MaintenanceCanonicalPath ($raw.Replace("/", "\"))) -ine
      (Get-MaintenanceCanonicalPath $DataDirectory)) {
    throw "隔离恢复 PostgreSQL 使用了非批准数据目录"
  }
  return $snapshot
}

function Invoke-MaintenancePgCtlStart(
  [string]$PgCtl,
  [string]$DataDirectory,
  [string]$LogPath,
  [string]$ServerOptions,
  [string]$WorkingDirectory
) {
  $stdoutPath = Join-Path $WorkingDirectory "pgctl-start.stdout.log"
  $stderrPath = Join-Path $WorkingDirectory "pgctl-start.stderr.log"
  $output = @()
  $exitCode = -1
  $launchFailed = $false
  $process = $null
  try {
    foreach ($value in @($PgCtl, $DataDirectory, $LogPath, $WorkingDirectory)) {
      if ([string]::IsNullOrWhiteSpace($value)) {
        throw "隔离 PostgreSQL 启动参数缺失"
      }
    }
    if (-not (Test-Path -LiteralPath $PgCtl -PathType Leaf) -or
        -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container) -or
        $DataDirectory.Contains('"') -or $LogPath.Contains('"') -or
        $ServerOptions.Contains('"')) {
      throw "隔离 PostgreSQL 启动参数无效"
    }
    $argumentLine = (
      'start -D "{0}" -l "{1}" -w -t 30 -o "{2}"' -f
      $DataDirectory, $LogPath, $ServerOptions
    )
    $process = Start-Process -FilePath $PgCtl -ArgumentList $argumentLine `
      -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if (-not $process.WaitForExit(45000)) {
      try {
        Stop-Process -Id $process.Id -Force -ErrorAction Stop
      } catch {
        # The caller revalidates the exact rehearsal listener before any cleanup.
      }
      $output = @("pg_ctl start exceeded bounded wait")
    } else {
      $exitCode = [int]$process.ExitCode
      foreach ($path in @($stdoutPath, $stderrPath)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
          $output += @(Get-Content -LiteralPath $path -ErrorAction SilentlyContinue)
        }
      }
    }
  } catch {
    $output = @($_)
    $launchFailed = $true
  } finally {
    if ($null -ne $process) { $process.Dispose() }
  }
  return [pscustomobject][ordered]@{
    ExitCode = [int]$exitCode
    LaunchFailed = [bool]$launchFailed
    Output = @($output)
    Diagnostic = Get-BoundedNativeDiagnostic $output
  }
}

function Initialize-MaintenanceRehearsalRoles(
  [string]$CreateUser,
  [string]$WorkingDirectory,
  [int]$Port
) {
  foreach ($roleName in $MaintenanceRehearsalRoles) {
    if ($roleName -cnotmatch "^teruisi_[a-z_]{1,64}$") {
      throw "隔离恢复角色契约无效"
    }
    $roleRun = Invoke-BoundedNativeProcess $CreateUser @(
      "--host=127.0.0.1", "--port=$Port", "--username=postgres",
      "--no-password", "--no-login",
      "--no-superuser", "--no-createdb", "--no-createrole",
      "--no-replication", "--no-bypassrls", $roleName
    ) $WorkingDirectory
    if ($roleRun.ExitCode -ne 0) {
      throw "隔离恢复所需数据库角色创建失败（$(Get-NativeFailureSummary $roleRun)）"
    }
  }
}

function Remove-MaintenanceRehearsalData(
  [string]$DataDirectory,
  [string]$RehearsalRoot,
  [string]$ExpectedRehearsalId,
  [int]$Port
) {
  if (-not (Test-Path -LiteralPath $DataDirectory)) { return }
  $root = Get-MaintenanceCanonicalPath $RehearsalRoot
  $data = Get-MaintenanceCanonicalPath $DataDirectory
  $expectedParent = Assert-RuntimeChildPath (
    Join-Path $MaintenanceRequest.RuntimeRoot "rehearsals\postgres-restore"
  )
  if ([IO.Path]::GetFileName($root) -cne "restore-$ExpectedRehearsalId" -or
      (Get-MaintenanceCanonicalPath (Split-Path -Parent $root)) -ine
        (Get-MaintenanceCanonicalPath $expectedParent) -or
      (Get-MaintenanceCanonicalPath (Split-Path -Parent $data)) -ine $root -or
      [IO.Path]::GetFileName($data) -cne "data" -or
      $ExpectedRehearsalId -cnotmatch "^[0-9a-f]{12}$" -or
      @(Get-PortListeners $Port).Count -ne 0) {
    throw "拒绝清理身份不明确或仍在运行的隔离恢复数据目录"
  }
  Assert-MaintenanceNoReparsePoints $data "隔离恢复数据目录"
  Remove-Item -LiteralPath $data -Recurse -Force
}

function Invoke-MaintenanceRestoreRehearsal {
  if (-not $MaintenanceRequest.Execute -or
      -not $MaintenanceRequest.ConfirmedIsolatedRestore -or
      $MaintenanceRequest.RehearsalId -cnotmatch "^[0-9a-f]{12}$" -or
      $MaintenanceRequest.ApprovedManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]::IsNullOrWhiteSpace($MaintenanceRequest.BackupDirectory)) {
    throw "隔离恢复演练必须提供 -Execute、-ConfirmedIsolatedRestore、12 位 RehearsalId、备份目录与批准 manifest SHA-256"
  }
  $evidenceTool = Assert-MaintenanceRuntimeContext
  $backup = Resolve-MaintenanceBackupArchive (
    $MaintenanceRequest.BackupDirectory
  ) $MaintenanceRequest.ApprovedManifestSha256
  Assert-MaintenanceProtectedArchiveUnsupported $backup.Manifest
  if (@(Get-PortListeners $MaintenanceRequest.RehearsalPort).Count -ne 0) {
    throw "隔离恢复端口已被占用；拒绝接管或终止现有进程"
  }

  $rehearsalParent = Assert-RuntimeChildPath (
    Join-Path $MaintenanceRequest.RuntimeRoot "rehearsals\postgres-restore"
  )
  New-Item -ItemType Directory -Path $rehearsalParent -Force | Out-Null
  $rehearsalRoot = Assert-RuntimeChildPath (
    Join-Path $rehearsalParent "restore-$($MaintenanceRequest.RehearsalId)"
  )
  if (Test-Path -LiteralPath $rehearsalRoot) {
    throw "相同 RehearsalId 的隔离恢复记录已存在；拒绝覆盖或复用"
  }
  New-Item -ItemType Directory -Path $rehearsalRoot | Out-Null
  $dataDirectory = Join-Path $rehearsalRoot "data"
  $logPath = Join-Path $rehearsalRoot "postgres.log"
  $passwordPath = Join-Path $rehearsalRoot ".postgres-password.tmp"
  $resultPath = Join-Path $rehearsalRoot "rehearsal-result.json"
  $startedAt = [DateTimeOffset]::UtcNow
  $isolatedStarted = $false
  $cleanupStatus = "not_required"
  $result = $null
  $failure = $null

  $initDb = Join-Path $PostgresBin "initdb.exe"
  $pgCtl = Join-Path $PostgresBin "pg_ctl.exe"
  $createUser = Join-Path $PostgresBin "createuser.exe"
  $createdb = Join-Path $PostgresBin "createdb.exe"
  $pgRestore = Join-Path $PostgresBin "pg_restore.exe"
  $pgIsReady = Join-Path $PostgresBin "pg_isready.exe"
  foreach ($tool in @($initDb, $pgCtl, $createUser, $createdb, $pgRestore, $pgIsReady, $Python, $evidenceTool)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
      throw "隔离恢复演练缺少受控 PostgreSQL/Python 工具"
    }
  }

  $password = New-RandomSecret
  try {
    Write-MaintenanceAtomicText $passwordPath ($password + [Environment]::NewLine)
    $initRun = Invoke-BoundedNativeProcess $initDb @(
      "--pgdata=$dataDirectory", "--username=postgres", "--pwfile=$passwordPath",
      "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
      "--encoding=UTF8", "--locale=C"
    ) $rehearsalRoot
    if ($initRun.ExitCode -ne 0) {
      throw "隔离 PostgreSQL initdb 失败（$(Get-NativeFailureSummary $initRun)）"
    }
    Remove-Item -LiteralPath $passwordPath -Force

    $serverOptions = "-p $($MaintenanceRequest.RehearsalPort) -h 127.0.0.1 -c max_connections=10 -c shared_buffers=128MB -c log_min_messages=warning"
    $startRun = Invoke-MaintenancePgCtlStart $pgCtl $dataDirectory $logPath (
      $serverOptions
    ) $rehearsalRoot
    if ($startRun.ExitCode -ne 0) {
      throw "隔离 PostgreSQL 启动失败（$(Get-NativeFailureSummary $startRun)）"
    }
    $isolatedStarted = $true
    Assert-MaintenanceRehearsalListenerOwnership (
      $MaintenanceRequest.RehearsalPort
    ) $dataDirectory | Out-Null
    $readyRun = Invoke-BoundedNativeProcess $pgIsReady @(
      "-q", "-h", "127.0.0.1", "-p", [string]$MaintenanceRequest.RehearsalPort,
      "-U", "postgres", "-d", "postgres"
    ) $rehearsalRoot
    if ($readyRun.ExitCode -ne 0) { throw "隔离 PostgreSQL 未就绪" }

    $probe = Invoke-MaintenancePgEnvironment @{
      PGHOST = "127.0.0.1"
      PGPORT = [string]$MaintenanceRequest.RehearsalPort
      PGUSER = "postgres"
      PGDATABASE = "postgres"
      PGPASSWORD = $password
      PGAPPNAME = "teruisi_restore_rehearsal"
      PGOPTIONS = "-c statement_timeout=1800000 -c idle_in_transaction_session_timeout=1860000"
      PGCLIENTENCODING = "UTF8"
    } {
      Assert-MaintenanceRehearsalListenerOwnership (
        $MaintenanceRequest.RehearsalPort
      ) $dataDirectory | Out-Null
      Initialize-MaintenanceRehearsalRoles $createUser $rehearsalRoot (
        $MaintenanceRequest.RehearsalPort
      )
      $createRun = Invoke-BoundedNativeProcess $createdb @(
        "--host=127.0.0.1", "--port=$($MaintenanceRequest.RehearsalPort)",
        "--username=postgres", "--owner=postgres", "teruisi_sales"
      ) $rehearsalRoot
      if ($createRun.ExitCode -ne 0) {
        throw "隔离恢复数据库创建失败（$(Get-NativeFailureSummary $createRun)）"
      }
      $restoreRun = Invoke-BoundedNativeProcess $Python @(
        $evidenceTool, "restore",
        "--pg-restore", $pgRestore,
        "--archive", $backup.DumpPath,
        "--expected-database", "teruisi_sales",
        "--expected-user", "postgres",
        "--port", [string]$MaintenanceRequest.RehearsalPort,
        "--timeout-seconds", "1800"
      ) $InstalledAppRoot
      $restorePayload = ConvertFrom-UniqueNativeJson $restoreRun "隔离 PostgreSQL restore"
      Assert-MaintenanceExactPropertySet $restorePayload @(
        "version", "status", "nativeDiagnostic"
      ) "隔离 PostgreSQL restore 结果"
      if ([string]$restorePayload.version -cne "teruisi-postgres-consistent-backup-v1" -or
          [string]$restorePayload.status -cne "completed" -or
          [int]$restorePayload.nativeDiagnostic.exitCode -ne 0) {
        throw "隔离 PostgreSQL restore 结果无效"
      }
      [Environment]::SetEnvironmentVariable("PGDATABASE", "teruisi_sales", "Process")
      $probeRun = Invoke-BoundedNativeProcess $Python @(
        $evidenceTool, "probe",
        "--expected-database", "teruisi_sales",
        "--expected-user", "postgres"
      ) $InstalledAppRoot
      return ConvertFrom-UniqueNativeJson $probeRun "读取隔离恢复证据"
    }
    Assert-MaintenanceExactPropertySet $probe @("version", "status", "evidence") "隔离恢复探针结果"
    if ([string]$probe.version -cne "teruisi-postgres-consistent-backup-v1" -or
        [string]$probe.status -cne "completed") {
      throw "隔离恢复探针结果无效"
    }
    Assert-MaintenanceEvidence $probe.evidence "teruisi_sales" "postgres" (
      $MaintenanceRequest.RehearsalPort
    )
    if ([string]$probe.evidence.contentSha256 -cne
        [string]$backup.Manifest.evidence.contentSha256) {
      throw "隔离恢复内容证据与备份快照不一致"
    }

    $stopRun = Invoke-BoundedNativeProcess $pgCtl @(
      "stop", "-D", $dataDirectory, "-m", "fast", "-w", "-t", "30"
    ) $rehearsalRoot
    if ($stopRun.ExitCode -ne 0) {
      throw "隔离 PostgreSQL 停止失败（$(Get-NativeFailureSummary $stopRun)）"
    }
    $isolatedStarted = $false
    if (@(Get-PortListeners $MaintenanceRequest.RehearsalPort).Count -ne 0) {
      throw "隔离 PostgreSQL 停止后端口仍被占用"
    }
    Remove-MaintenanceRehearsalData $dataDirectory $rehearsalRoot (
      $MaintenanceRequest.RehearsalId
    ) $MaintenanceRequest.RehearsalPort
    $cleanupStatus = "isolated_data_removed"
    $result = [pscustomobject][ordered]@{
      version = $MaintenanceRestoreVersion
      status = "completed"
      rehearsalId = $MaintenanceRequest.RehearsalId
      startedAt = $startedAt.ToString("o")
      completedAt = [DateTimeOffset]::UtcNow.ToString("o")
      backupId = [string]$backup.Manifest.backupId
      backupManifestSha256 = [string]$backup.ManifestSha256
      dumpSha256 = [string]$backup.Manifest.dump.sha256
      expectedContentSha256 = [string]$backup.Manifest.evidence.contentSha256
      restoredContentSha256 = [string]$probe.evidence.contentSha256
      isolatedPort = [int]$MaintenanceRequest.RehearsalPort
      productionDatabaseTouched = $false
      serviceStateChanged = $false
      cleanupStatus = $cleanupStatus
    }
  } catch {
    $failure = $_.Exception
    if (-not $isolatedStarted -and
        @(Get-PortListeners $MaintenanceRequest.RehearsalPort).Count -gt 0) {
      try {
        Assert-MaintenanceRehearsalListenerOwnership (
          $MaintenanceRequest.RehearsalPort
        ) $dataDirectory | Out-Null
        $isolatedStarted = $true
      } catch {
        $cleanupStatus = "isolated_process_requires_manual_review"
      }
    }
    if ($isolatedStarted) {
      try {
        Assert-MaintenanceRehearsalListenerOwnership (
          $MaintenanceRequest.RehearsalPort
        ) $dataDirectory | Out-Null
        $stopRun = Invoke-BoundedNativeProcess $pgCtl @(
          "stop", "-D", $dataDirectory, "-m", "fast", "-w", "-t", "30"
        ) $rehearsalRoot
        if ($stopRun.ExitCode -ne 0) { throw "受控 stop 未成功" }
        $isolatedStarted = $false
      } catch {
        $cleanupStatus = "isolated_process_requires_manual_review"
      }
    }
    if (-not $isolatedStarted -and (Test-Path -LiteralPath $dataDirectory)) {
      try {
        Remove-MaintenanceRehearsalData $dataDirectory $rehearsalRoot (
          $MaintenanceRequest.RehearsalId
        ) $MaintenanceRequest.RehearsalPort
        $cleanupStatus = "isolated_data_removed_after_failure"
      } catch {
        $cleanupStatus = "isolated_data_requires_manual_review"
      }
    }
    $result = [pscustomobject][ordered]@{
      version = $MaintenanceRestoreVersion
      status = "failed"
      rehearsalId = $MaintenanceRequest.RehearsalId
      startedAt = $startedAt.ToString("o")
      completedAt = [DateTimeOffset]::UtcNow.ToString("o")
      backupId = [string]$backup.Manifest.backupId
      backupManifestSha256 = [string]$backup.ManifestSha256
      failureSha256 = Get-Sha256Text (Protect-LogText $failure.Message)
      isolatedPort = [int]$MaintenanceRequest.RehearsalPort
      productionDatabaseTouched = $false
      serviceStateChanged = $false
      cleanupStatus = $cleanupStatus
    }
  } finally {
    $password = $null
    if (Test-Path -LiteralPath $passwordPath) {
      Remove-Item -LiteralPath $passwordPath -Force
    }
    if ($null -ne $result) {
      Write-AtomicJson $resultPath $result
      Write-MaintenanceAtomicText "$resultPath.sha256" (
        (Get-FileSha256 $resultPath) + [Environment]::NewLine
      )
    }
  }
  if ($null -ne $failure) {
    throw "隔离恢复演练失败；已保留脱敏结果：$resultPath"
  }
  return $result
}

function Get-MaintenancePrunePlan {
  $backupRoot = Get-MaintenanceBackupRoot $false
  $verified = @()
  $skipped = @()
  if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) {
    return [pscustomobject][ordered]@{
      version = $MaintenancePruneVersion
      generatedAt = [DateTimeOffset]::UtcNow.ToString("o")
      retentionDays = [int]$MaintenanceRequest.RetentionDays
      minimumSuccessfulBackups = [int]$MaintenanceRequest.MinimumSuccessfulBackups
      verifiedBackupCount = 0
      candidates = @()
      skippedInvalid = @()
    }
  }
  foreach ($directory in @(
    Get-ChildItem -LiteralPath $backupRoot -Directory -Force |
      Where-Object { $_.Name -cmatch "^daily-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$" }
  )) {
    try {
      $archive = Resolve-MaintenanceBackupArchive $directory.FullName
      $verified += [pscustomobject]@{
        archive = $archive
        completedAt = [DateTimeOffset]::Parse([string]$archive.Manifest.completedAt)
      }
    } catch {
      $skipped += [pscustomobject][ordered]@{
        backupId = $directory.Name
        reasonSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
      }
    }
  }
  $ordered = @($verified | Sort-Object completedAt -Descending)
  $cutoff = [DateTimeOffset]::UtcNow.AddDays(-1 * $MaintenanceRequest.RetentionDays)
  $candidates = @()
  for ($index = 0; $index -lt $ordered.Count; $index++) {
    if ($index -lt $MaintenanceRequest.MinimumSuccessfulBackups) { continue }
    if ($ordered[$index].completedAt -ge $cutoff) { continue }
    $candidates += [pscustomobject][ordered]@{
      backupId = [string]$ordered[$index].archive.Manifest.backupId
      directory = [string]$ordered[$index].archive.Directory
      manifestSha256 = [string]$ordered[$index].archive.ManifestSha256
      completedAt = $ordered[$index].completedAt.ToString("o")
    }
  }
  return [pscustomobject][ordered]@{
    version = $MaintenancePruneVersion
    generatedAt = [DateTimeOffset]::UtcNow.ToString("o")
    retentionDays = [int]$MaintenanceRequest.RetentionDays
    minimumSuccessfulBackups = [int]$MaintenanceRequest.MinimumSuccessfulBackups
    verifiedBackupCount = [int]$ordered.Count
    candidates = @($candidates)
    skippedInvalid = @($skipped)
  }
}

function Invoke-MaintenancePrune {
  Assert-MaintenanceRuntimeContext | Out-Null
  $plan = Get-MaintenancePrunePlan
  if (-not $MaintenanceRequest.Execute) {
    return [pscustomobject][ordered]@{
      status = "planned"
      plan = $plan
      serviceStateChanged = $false
    }
  }
  if (-not $MaintenanceRequest.ConfirmedPrune) {
    throw "删除过期 PostgreSQL 备份必须显式提供 -ConfirmedPrune"
  }
  $auditRoot = Assert-RuntimeChildPath (
    Join-Path $MaintenanceRequest.RuntimeRoot "audits\postgres-backup-prune"
  )
  New-Item -ItemType Directory -Path $auditRoot -Force | Out-Null
  $auditId = "prune-{0}-{1}" -f (
    [DateTimeOffset]::UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'")
  ), ([Guid]::NewGuid().ToString("N").Substring(0, 12))
  $auditDirectory = Join-Path $auditRoot $auditId
  New-Item -ItemType Directory -Path $auditDirectory | Out-Null
  Write-AtomicJson (Join-Path $auditDirectory "plan.json") $plan

  $backupRoot = Get-MaintenanceBackupRoot $false
  $removed = @()
  foreach ($candidate in @($plan.candidates)) {
    $archive = Resolve-MaintenanceBackupArchive (
      [string]$candidate.directory
    ) ([string]$candidate.manifestSha256)
    if ([string]$archive.Manifest.backupId -cne [string]$candidate.backupId) {
      throw "待清理备份身份在执行前发生变化"
    }
    $quarantineName = ".prune-$([Guid]::NewGuid().ToString('N')).quarantine"
    $quarantine = Assert-RuntimeChildPath (Join-Path $backupRoot $quarantineName)
    Move-Item -LiteralPath $archive.Directory -Destination $quarantine
    if ((Get-MaintenanceCanonicalPath (Split-Path -Parent $quarantine)) -ine
        (Get-MaintenanceCanonicalPath $backupRoot) -or
        [IO.Path]::GetFileName($quarantine) -cnotmatch "^\.prune-[0-9a-f]{32}\.quarantine$") {
      throw "备份隔离清理目录身份无效"
    }
    Assert-MaintenanceNoReparsePoints $quarantine "待清理 PostgreSQL 备份"
    Remove-Item -LiteralPath $quarantine -Recurse -Force
    $removed += [pscustomobject][ordered]@{
      backupId = [string]$candidate.backupId
      manifestSha256 = [string]$candidate.manifestSha256
    }
  }
  $result = [pscustomobject][ordered]@{
    version = $MaintenancePruneVersion
    status = "completed"
    auditId = $auditId
    completedAt = [DateTimeOffset]::UtcNow.ToString("o")
    removed = @($removed)
    skippedInvalid = @($plan.skippedInvalid)
    serviceStateChanged = $false
  }
  $resultPath = Join-Path $auditDirectory "result.json"
  Write-AtomicJson $resultPath $result
  Write-MaintenanceAtomicText "$resultPath.sha256" (
    (Get-FileSha256 $resultPath) + [Environment]::NewLine
  )
  return $result
}

function Show-MaintenanceStatus {
  Assert-MaintenanceRuntimeContext | Out-Null
  $backupRoot = Get-MaintenanceBackupRoot $false
  $directories = @()
  if (Test-Path -LiteralPath $backupRoot -PathType Container) {
    $directories = @(
      Get-ChildItem -LiteralPath $backupRoot -Directory -Force |
        Where-Object { $_.Name -cmatch "^daily-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$" } |
        Sort-Object Name -Descending
    )
  }
  $latest = $null
  if ($directories.Count -gt 0) {
    try {
      $archive = Resolve-MaintenanceBackupArchive $directories[0].FullName
      $latest = [pscustomobject][ordered]@{
        backupId = [string]$archive.Manifest.backupId
        completedAt = [string]$archive.Manifest.completedAt
        manifestSha256 = [string]$archive.ManifestSha256
        contentSha256 = [string]$archive.Manifest.evidence.contentSha256
      }
    } catch {
      $latest = [pscustomobject][ordered]@{
        backupId = $directories[0].Name
        status = "invalid"
        reasonSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
      }
    }
  }
  return [pscustomobject][ordered]@{
    status = "completed"
    backupCount = [int]$directories.Count
    latestBackup = $latest
    serviceStateChanged = $false
  }
}

if ($env:TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY -ne "1") {
  if (-not (Test-MaintenanceFullyQualifiedPath $MaintenanceRequest.RuntimeRoot)) {
    throw "RuntimeRoot 必须是绝对路径"
  }
  $canonicalRuntime = Get-MaintenanceCanonicalPath $MaintenanceRequest.RuntimeRoot
  if ($canonicalRuntime -ine $MaintenanceFixedRuntimeRoot) {
    throw "PostgreSQL 维护只允许固定受保护 Django runtime"
  }
  $serviceScript = Join-Path $canonicalRuntime "app\tools\django-local-service.ps1"
  $expectedSelf = Join-Path $canonicalRuntime "app\tools\django-postgres-maintenance.ps1"
  if (-not (Test-Path -LiteralPath $serviceScript -PathType Leaf) -or
      (Get-MaintenanceCanonicalPath $PSCommandPath) -ine
        (Get-MaintenanceCanonicalPath $expectedSelf)) {
    throw "PostgreSQL 维护只能从受保护的 runtime app operator 执行"
  }
  $previousLibraryOnly = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
  )
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  try {
    . $serviceScript -Action Status -RuntimeRoot $canonicalRuntime
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
    )
  }

  $output = Invoke-MaintenanceMutex {
    switch ($MaintenanceRequest.Action) {
      "Backup" { Invoke-MaintenanceBackup }
      "Verify" {
        if ([string]::IsNullOrWhiteSpace($MaintenanceRequest.BackupDirectory)) {
          throw "Verify 必须提供 BackupDirectory"
        }
        $archive = Resolve-MaintenanceBackupArchive (
          $MaintenanceRequest.BackupDirectory
        ) $MaintenanceRequest.ApprovedManifestSha256
        [pscustomobject][ordered]@{
          status = "completed"
          backupId = [string]$archive.Manifest.backupId
          manifestSha256 = [string]$archive.ManifestSha256
          dumpSha256 = [string]$archive.Manifest.dump.sha256
          contentSha256 = [string]$archive.Manifest.evidence.contentSha256
          serviceStateChanged = $false
        }
      }
      "RestoreRehearsal" { Invoke-MaintenanceRestoreRehearsal }
      "ProtectedAiPreflight" { Invoke-MaintenanceProtectedAiPreflight }
      "Prune" { Invoke-MaintenancePrune }
      "Status" { Show-MaintenanceStatus }
    }
  }
  Write-Output ($output | ConvertTo-Json -Depth 12 -Compress)
}
