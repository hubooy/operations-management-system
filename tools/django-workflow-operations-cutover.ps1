[CmdletBinding()]
param(
  [ValidateSet(
    "InstallD1Authority", "Snapshot", "MigratePlan", "MigrateApply", "MigrateVerify",
    "AuthorityStatus", "AuthorityPrepare", "AuthorityAbort", "AuthorityActivate",
    "RetirePlan", "RetireApply"
  )]
  [string]$Action = "AuthorityStatus",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$WorkflowSource = "",
  [string]$ApprovedRunId = "",
  [string]$WorkflowCutoverId = "",
  [string]$SmokeReceipt = "",
  [string]$ApprovedPlanId = "",
  [string]$AuditOutput = ""
)

$ErrorActionPreference = "Stop"
$CutoverAction = $Action
$CutoverApprovedPlanId = $ApprovedPlanId
$ServiceScript = Join-Path $PSScriptRoot "django-local-service.ps1"
$previousLibraryMode = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $RuntimeRoot
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryMode, "Process"
  )
}
$Action = $CutoverAction
$ApprovedPlanId = $CutoverApprovedPlanId
$WorkflowAuditRoot = Join-Path $RuntimeRoot "audits\workflow-operations-cutover"

function Assert-InstalledWorkflowOperationsOperator {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "Workflow operations cutover must run from the protected runtime app after DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) {
    throw "PostgreSQL is not ready for workflow operations cutover"
  }
}

function Resolve-LiveWorkflowD1 {
  $config = Get-ServiceConfig
  return Resolve-ErpSourceD1 ([string]$config.erpSourceD1)
}

function Assert-WorkflowStackStopped([string]$Operation) {
  if (Resolve-OwnedProcess "django-workflow-writer" $DjangoWorkflowWriterPidPath $Waitress) {
    throw "$Operation 前必须停止 Workflow writer"
  }
  if (Resolve-OwnedProcess "django-workflow-reader" $DjangoWorkflowReaderPidPath $Waitress) {
    throw "$Operation 前必须停止 Workflow reader"
  }
  if (@(Get-PortListeners 8061).Count -gt 0 -or @(Get-PortListeners 8062).Count -gt 0) {
    throw "$Operation 前端口 8061/8062 必须没有监听者"
  }
}

function Assert-WorkflowWorkerStopped([string]$Operation) {
  if (@(Get-PortListeners 3000).Count -gt 0) {
    throw "$Operation 前必须通过统一控制器停止 Worker"
  }
}

function Resolve-WorkflowSnapshot([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) {
    throw "Workflow migration requires an absolute controlled snapshot path"
  }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $WorkflowAuditRoot
  if (-not $canonical.StartsWith(
      $auditRoot + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Workflow snapshot must be inside the protected workflow operations audit directory"
  }
  if (-not (Test-Path -LiteralPath $canonical -PathType Leaf) -or
      [IO.Path]::GetExtension($canonical) -ine ".sqlite") {
    throw "Workflow snapshot is missing or is not a .sqlite file"
  }
  return $canonical
}

function Resolve-WorkflowSnapshotManifest([string]$Source) {
  $manifest = Get-CanonicalPath (Join-Path (Split-Path -Parent $Source) "workflow-source-manifest.json")
  [void](Assert-RuntimeChildPath $manifest)
  if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Workflow migration controlled source manifest is missing"
  }
  $payload = Read-JsonFile $manifest "Workflow operations controlled source manifest"
  if (
    [string]$payload.formatVersion -cne "workflow-operations-d1-snapshot-v1" -or
    [string]$payload.outputSha256 -cne (Get-FileSha256 $Source) -or
    [string]$payload.sourcePathSha256 -cnotmatch "^[0-9a-f]{64}$" -or
    [int]$payload.counts.tasks -lt 0 -or
    [int]$payload.counts.operationRecords -lt 0 -or
    [string]$payload.authority.owner -cne "legacy" -or
    [int]$payload.authority.epoch -lt 1 -or
    -not [string]::IsNullOrEmpty([string]$payload.authority.cutoverId)
  ) {
    throw "Workflow operations controlled source manifest does not match the snapshot"
  }
  return $manifest
}

function Resolve-WorkflowAuditFile([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) {
    throw "$Label 必须是受控审计目录内的绝对文件路径"
  }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $WorkflowAuditRoot
  if (-not $canonical.StartsWith(
      $auditRoot + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "$Label 必须位于受保护的 workflow operations 审计目录"
  }
  return $canonical
}

function Invoke-WorkflowManagementCommand([string[]]$Arguments, [string]$Operation) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_workflow_operations_cutover" $WriterStatementTimeoutMs
  $manage = Join-Path $BackendRoot "manage.py"
  $commandArguments = @($manage) + @($Arguments)
  if ($Operation -cnotmatch "^[a-z0-9_-]{1,64}$" -or
      @($commandArguments | Where-Object { [string]::IsNullOrWhiteSpace([string]$_) }).Count -gt 0) {
    throw "Workflow operations management command contract is invalid"
  }
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" (
      $false
    ) $WriterMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python $commandArguments $BackendRoot
      Write-NativeDiagnosticLog (
        Join-Path $LogDirectory "workflow-operations-cutover.$RunId.log"
      ) $Operation $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun $Operation
    }
  } finally {
    $ownerUrl = $null
    $secrets = $null
  }
}

function Invoke-InstallWorkflowD1Authority {
  Assert-InstalledWorkflowOperationsOperator
  Assert-WorkflowStackStopped "安装 D1 运营事务全板块写入权威门禁"
  Assert-WorkflowWorkerStopped "安装 D1 运营事务全板块写入权威门禁"
  $source = Resolve-LiveWorkflowD1
  $directory = Join-Path $WorkflowAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $backup = Join-Path $directory "d1-before-workflow-operations-authority.sqlite"
  $receipt = Join-Path $directory "authority-install-receipt.json"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    (Join-Path $InstalledAppRoot "tools\workflow-operations-d1-authority-install.py"),
    "--source", $source,
    "--sql", (Join-Path $InstalledAppRoot "drizzle\0105_workflow_operations_write_authority.sql"),
    "--backup", $backup,
    "--receipt", $receipt
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog (
    Join-Path $LogDirectory "workflow-operations-authority-install.$RunId.log"
  ) "workflow_operations_authority_install" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "install D1 workflow operations write guard"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-WorkflowSnapshot {
  Assert-InstalledWorkflowOperationsOperator
  Assert-WorkflowStackStopped "创建 D1 运营事务迁移快照"
  Assert-WorkflowWorkerStopped "创建 D1 运营事务迁移快照"
  $directory = Join-Path $WorkflowAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $snapshot = Join-Path $directory "workflow-source.sqlite"
  $manifest = Join-Path $directory "workflow-source-manifest.json"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    (Join-Path $InstalledAppRoot "tools\workflow-operations-d1-snapshot.py"),
    "--source", (Resolve-LiveWorkflowD1),
    "--output", $snapshot,
    "--manifest", $manifest
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog (
    Join-Path $LogDirectory "workflow-operations-snapshot.$RunId.log"
  ) "workflow_operations_snapshot" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "create controlled workflow operations D1 snapshot"
  Write-Output ([ordered]@{
    status = "succeeded"
    source = $snapshot
    sourceSha256 = Get-FileSha256 $snapshot
    manifest = $manifest
    manifestSha256 = Get-FileSha256 $manifest
    counts = $payload.counts
    authority = $payload.authority
  } | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-WorkflowMigration([string]$Mode) {
  Assert-InstalledWorkflowOperationsOperator
  Assert-WorkflowStackStopped "迁移运营事务全板块"
  Assert-WorkflowWorkerStopped "迁移运营事务全板块"
  $source = Resolve-WorkflowSnapshot $WorkflowSource
  [void](Resolve-WorkflowSnapshotManifest $source)
  $commandMode = switch ($Mode) {
    "plan" { "dry-run" }
    "apply" { "apply" }
    "verify" { "verify-only" }
    default { throw "Unknown workflow operations migration mode" }
  }
  $arguments = @(
    "migrate_workflow_operations_from_d1", "--source", $source, "--mode", $commandMode
  )
  if ($Mode -in @("apply", "verify")) {
    if ($ApprovedRunId -cnotmatch "^workflow-ops-[0-9a-f]{32}$") {
      throw "Workflow operations migration requires a valid approved run id"
    }
    $arguments += @("--approved-run-id", $ApprovedRunId)
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRunId)) {
    throw "Workflow operations dry-run does not accept an approved run id"
  }
  $payload = Invoke-WorkflowManagementCommand $arguments "workflow_operations_migration_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 10)
}

function Invoke-WorkflowAuthority([string]$Mode) {
  Assert-InstalledWorkflowOperationsOperator
  Assert-WorkflowStackStopped "切换运营事务全板块写入权威"
  if ($Mode -ne "status") {
    Assert-WorkflowWorkerStopped "切换运营事务全板块写入权威"
  }
  $arguments = @(
    "workflow_operations_write_authority", "--source", (Resolve-LiveWorkflowD1)
  )
  if ($Mode -ne "status") {
    if ($ApprovedRunId -cnotmatch "^workflow-ops-[0-9a-f]{32}$") {
      throw "Workflow operations authority transition requires a valid approved run id"
    }
    if ($WorkflowCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
      throw "Workflow operations authority transition requires a valid cutover id"
    }
    $flag = switch ($Mode) {
      "prepare" { "--prepare" }
      "abort" { "--abort-pending" }
      "activate" { "--activate" }
      default { throw "Unknown workflow operations authority action" }
    }
    $arguments += @(
      $flag, "--approved-run-id", $ApprovedRunId, "--cutover-id", $WorkflowCutoverId
    )
  } elseif (
    -not [string]::IsNullOrWhiteSpace($ApprovedRunId) -or
    -not [string]::IsNullOrWhiteSpace($WorkflowCutoverId)
  ) {
    throw "Workflow operations authority status does not accept transition parameters"
  }
  $payload = Invoke-WorkflowManagementCommand $arguments "workflow_operations_authority_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-WorkflowRetirement([bool]$Apply) {
  Assert-InstalledWorkflowOperationsOperator
  Assert-WorkflowStackStopped "终态退役 D1 运营事务全板块"
  Assert-WorkflowWorkerStopped "终态退役 D1 运营事务全板块"
  if ($ApprovedRunId -cnotmatch "^workflow-ops-[0-9a-f]{32}$" -or
      $WorkflowCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
    throw "Workflow operations retirement requires exact approved run and cutover ids"
  }
  $smoke = Resolve-WorkflowAuditFile $SmokeReceipt "运营事务系统测试 receipt"
  if (-not (Test-Path -LiteralPath $smoke -PathType Leaf)) {
    throw "Workflow operations smoke receipt is missing"
  }
  $arguments = @(
    "retire_workflow_operations_d1",
    "--source", (Resolve-LiveWorkflowD1),
    "--cutover-id", $WorkflowCutoverId,
    "--approved-run-id", $ApprovedRunId,
    "--smoke-receipt", $smoke
  )
  if ($Apply) {
    if ($ApprovedPlanId -cnotmatch "^[0-9a-f]{64}$") {
      throw "Workflow operations retirement apply requires an exact approved plan id"
    }
    $audit = Resolve-WorkflowAuditFile $AuditOutput "运营事务退役审计输出"
    $arguments += @("--apply", "--approved-plan-id", $ApprovedPlanId, "--audit-output", $audit)
  } elseif (
    -not [string]::IsNullOrWhiteSpace($ApprovedPlanId) -or
    -not [string]::IsNullOrWhiteSpace($AuditOutput)
  ) {
    throw "Workflow operations retirement plan does not accept apply-only arguments"
  }
  $payload = Invoke-WorkflowManagementCommand $arguments $(
    if ($Apply) { "workflow_operations_retirement_apply" } else { "workflow_operations_retirement_plan" }
  )
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 10)
}

Invoke-WithServiceMutex {
  switch ($Action) {
    "InstallD1Authority" { Invoke-InstallWorkflowD1Authority }
    "Snapshot" { Invoke-WorkflowSnapshot }
    "MigratePlan" { Invoke-WorkflowMigration "plan" }
    "MigrateApply" { Invoke-WorkflowMigration "apply" }
    "MigrateVerify" { Invoke-WorkflowMigration "verify" }
    "AuthorityStatus" { Invoke-WorkflowAuthority "status" }
    "AuthorityPrepare" { Invoke-WorkflowAuthority "prepare" }
    "AuthorityAbort" { Invoke-WorkflowAuthority "abort" }
    "AuthorityActivate" { Invoke-WorkflowAuthority "activate" }
    "RetirePlan" { Invoke-WorkflowRetirement $false }
    "RetireApply" { Invoke-WorkflowRetirement $true }
  }
}
