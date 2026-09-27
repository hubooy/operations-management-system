[CmdletBinding()]
param(
  [ValidateSet(
    "PrepareRuntime", "Snapshot", "MigrateDryRun", "MigrateApply", "MigrateVerify",
    "InstallD1Authority", "AuthorityStatus", "AuthorityPrepare",
    "AuthorityAbort", "AuthorityActivate", "R2Evidence",
    "RetirementPlan", "RetirementApply"
  )]
  [string]$Action = "AuthorityStatus",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$ErpReferenceD1 = "",
  [string]$ErpReferenceSource = "",
  [string]$ApprovedRunId = "",
  [string]$ErpReferenceCutoverId = "",
  [string]$SmokeReceipt = "",
  [string]$R2Evidence = "",
  [string]$ApprovedRetirementPlanId = ""
)

$ErrorActionPreference = "Stop"
$CutoverAction = $Action
$ServiceScript = Join-Path $PSScriptRoot "django-local-service.ps1"
$previousLibraryMode = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process")
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $RuntimeRoot
} finally { [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryMode, "Process") }
$Action = $CutoverAction
$ErpReferenceAuditRoot = Join-Path $RuntimeRoot "audits\erp-reference-cutover"
$ErpReferenceReaderPidPath = Join-Path $RunDirectory "django-erp-reference-reader.pid.json"
$ErpReferenceWriterPidPath = Join-Path $RunDirectory "django-erp-reference-writer.pid.json"

function Invoke-ErpReferencePrepareRuntime {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "ERP 主数据 runtime 准备必须从 DeployApp 后的受保护 runtime app 执行"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-ApplicationProcessesStopped "准备ERP 主数据 PostgreSQL 迁移"
  Assert-ErpReferenceWorkerStopped "准备ERP 主数据 PostgreSQL 迁移"
  $postgresStarted = $false
  $secrets = $null
  try {
    $postgresStarted = Start-Postgres
    $secrets = Read-Secrets
    Invoke-DjangoMigrations $secrets
    Write-Output ([ordered]@{
      status = "prepared"
      postgres = "running"
      migrations = "applied"
      postgresStartedByAction = [bool]$postgresStarted
    } | ConvertTo-Json -Compress)
  } catch {
    if ($postgresStarted) {
      try { Stop-Postgres } catch {
        Write-LauncherEvent "ERROR" "erp_reference_prepare_runtime_cleanup_failed" $_.Exception.Message
      }
    }
    throw
  } finally {
    $secrets = $null
  }
}

function Assert-InstalledErpReferenceOperator {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) { throw "ERP 主数据 cutover 必须从 DeployApp 后的受保护 runtime app 执行" }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪，拒绝执行ERP 主数据 cutover" }
}

function Resolve-LiveErpReferenceD1 {
  if ([string]::IsNullOrWhiteSpace($ErpReferenceD1) -or -not (Test-FullyQualifiedPath $ErpReferenceD1)) { throw "ERP 主数据 cutover 必须提供精确的ERP 主数据 D1 绝对路径" }
  $canonical = Get-CanonicalPath $ErpReferenceD1
  if (-not (Test-Path -LiteralPath $canonical -PathType Leaf) -or [IO.Path]::GetExtension($canonical) -notin @(".sqlite", ".sqlite3")) { throw "ERP 主数据 D1 必须是普通 .sqlite 或 .sqlite3 文件" }
  return $canonical
}

function Resolve-ErpReferenceSnapshot([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) { throw "ERP 主数据迁移需要绝对路径的受控源快照" }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $ErpReferenceAuditRoot
  if (-not $canonical.StartsWith($auditRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "ERP 主数据源快照必须位于 erp-reference-cutover 审计目录" }
  if (-not (Test-Path -LiteralPath $canonical -PathType Leaf) -or [IO.Path]::GetExtension($canonical) -ine ".sqlite") { throw "ERP 主数据源快照不存在或不是 .sqlite 文件" }
  return $canonical
}

function Resolve-ErpReferenceEvidencePath([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) { throw "$Label 必须是ERP 主数据 audit 目录内的绝对文件路径" }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $ErpReferenceAuditRoot
  if (-not $canonical.StartsWith($auditRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $canonical -PathType Leaf)) { throw "$Label 不在受保护ERP 主数据切换 audit 目录或文件不存在" }
  return $canonical
}

function Assert-ErpReferenceStackStopped([string]$Operation) {
  if (Resolve-OwnedProcess "django-erp-reference-reader" $ErpReferenceReaderPidPath $Waitress) { throw "$Operation 前必须通过ERP 主数据控制器停止 reader" }
  if (Resolve-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress) { throw "$Operation 前必须通过ERP 主数据控制器停止 writer" }
  if (@(Get-PortListeners 8091).Count -gt 0 -or @(Get-PortListeners 8092).Count -gt 0) { throw "$Operation 前端口 8091/8092 必须没有监听者" }
}

function Assert-ErpReferenceWorkerStopped([string]$Operation) {
  if (@(Get-PortListeners 3000).Count -gt 0) { throw "$Operation 前必须通过统一控制器停止 Worker" }
}

function Invoke-ErpReferenceManagementCommand([string[]]$Arguments, [string]$Operation) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_erp_reference_cutover" $WriterStatementTimeoutMs
  $commandArguments = @((Join-Path $BackendRoot "manage.py")) + $Arguments
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" $false $WriterMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python $commandArguments $BackendRoot
      Write-NativeDiagnosticLog (Join-Path $LogDirectory "erp-reference-cutover.$RunId.log") $Operation $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun $Operation
    }
  } finally { $ownerUrl = $null; $secrets = $null }
}

function Invoke-ErpReferenceSnapshot {
  Assert-InstalledErpReferenceOperator
  Assert-ErpReferenceStackStopped "创建ERP 主数据 D1 一致快照"
  Assert-ErpReferenceWorkerStopped "创建ERP 主数据 D1 一致快照"
  $source = Resolve-LiveErpReferenceD1
  $directory = Join-Path $ErpReferenceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $snapshot = Join-Path $directory "erp-reference-source.sqlite"
  $manifest = Join-Path $directory "erp-reference-source-manifest.json"
  $tool = Join-Path $InstalledAppRoot "tools\erp-reference-d1-snapshot.py"
  $nativeRun = Invoke-BoundedNativeProcess $Python @($tool, "--source", $source, "--output", $snapshot, "--manifest", $manifest) $InstalledAppRoot
  Write-NativeDiagnosticLog (Join-Path $LogDirectory "erp-reference-snapshot.$RunId.log") "erp_reference_snapshot" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "创建ERP 主数据受控 D1 快照"
  Write-Output ([ordered]@{ status = "succeeded"; source = $snapshot; sourceSha256 = Get-FileSha256 $snapshot; manifest = $manifest; manifestSha256 = Get-FileSha256 $manifest; counts = $payload.counts } | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-ErpReferenceMigration([string]$Mode) {
  Assert-InstalledErpReferenceOperator
  Assert-ErpReferenceStackStopped "执行ERP 主数据正式迁移"
  Assert-ErpReferenceWorkerStopped "执行ERP 主数据正式迁移"
  $source = Resolve-ErpReferenceSnapshot $ErpReferenceSource
  $arguments = @("migrate_erp_reference_from_d1", "--source", $source, "--mode", $Mode)
  if ($Mode -eq "apply") {
    if ($ApprovedRunId -notmatch "^erp-reference-plan-[0-9a-f]{32}$") { throw "ERP 主数据 apply 需要有效 approved plan id" }
    $arguments += @("--approve-run-id", $ApprovedRunId)
  } elseif ($Mode -eq "verify") {
    if ($ApprovedRunId -notmatch "^erp-reference-[0-9a-f]{32}$") { throw "ERP 主数据 verify 需要有效 approved apply run id" }
    $arguments += @("--verify-run-id", $ApprovedRunId)
  } elseif ($Mode -eq "dry-run") {
    if (-not [string]::IsNullOrWhiteSpace($ApprovedRunId)) { throw "ERP 主数据 dry-run 不接受 approved run id" }
    $arguments[$arguments.Count - 1] = "plan"
  } else { throw "未知ERP 主数据迁移操作" }
  $payload = Invoke-ErpReferenceManagementCommand $arguments "erp_reference_migration_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Assert-ErpReferenceWriterStopped {
  if (Resolve-OwnedProcess "django-erp-reference-writer" $ErpReferenceWriterPidPath $Waitress) { throw "ERP 主数据 writer 必须在 authority 变更前停止" }
  if (@(Get-PortListeners 8092).Count -gt 0) { throw "端口 8092 在 authority 变更前不得监听" }
}

function Invoke-InstallErpReferenceD1Authority {
  Assert-InstalledErpReferenceOperator
  Assert-ErpReferenceStackStopped "安装ERP 主数据 D1 写入权威门禁"
  Assert-ErpReferenceWorkerStopped "安装ERP 主数据 D1 写入权威门禁"
  $source = Resolve-LiveErpReferenceD1
  $directory = Join-Path $ErpReferenceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $backup = Join-Path $directory "d1-before-erp-reference-authority.sqlite"
  $receipt = Join-Path $directory "authority-install-receipt.json"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    (Join-Path $InstalledAppRoot "tools\erp-reference-d1-authority-install.py"),
    "--source", $source, "--sql", (Join-Path $InstalledAppRoot "drizzle\0109_erp_reference_write_authority.sql"),
    "--backup", $backup, "--receipt", $receipt
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog (Join-Path $LogDirectory "erp-reference-authority-install.$RunId.log") "erp_reference_authority_install" $nativeRun
  Write-Output ((ConvertFrom-UniqueNativeJson $nativeRun "安装ERP 主数据 D1 写保护") | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-ErpReferenceAuthority([string]$Mode) {
  Assert-InstalledErpReferenceOperator
  if ($Mode -ne "status") {
    Assert-ErpReferenceStackStopped "变更ERP 主数据写入权威"
    Assert-ErpReferenceWorkerStopped "变更ERP 主数据写入权威"
  } else {
    Assert-ErpReferenceWriterStopped
  }
  $source = Resolve-LiveErpReferenceD1
  $arguments = @("erp_reference_write_authority", "--source", $source)
  if ($Mode -ne "status") {
    if ($ApprovedRunId -notmatch "^erp-reference-[0-9a-f]{32}$") { throw "ERP 主数据 authority 变更需要有效 verify run id" }
    if ($ErpReferenceCutoverId -notmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "ERP 主数据 authority 变更需要有效 cutover id" }
    $flag = switch ($Mode) { "prepare" { "--prepare" } "abort" { "--abort-pending" } "activate" { "--activate" } default { throw "未知ERP 主数据 authority 操作" } }
    $arguments += @($flag, "--approved-run-id", $ApprovedRunId, "--cutover-id", $ErpReferenceCutoverId)
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRunId) -or -not [string]::IsNullOrWhiteSpace($ErpReferenceCutoverId)) { throw "ERP 主数据 authority status 不接受变更参数" }
  $payload = Invoke-ErpReferenceManagementCommand $arguments "erp_reference_authority_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-ErpReferenceR2Evidence {
  Assert-InstalledErpReferenceOperator
  Assert-ErpReferenceWorkerStopped "生成ERP 主数据 R2 退役证据"
  $source = Resolve-LiveErpReferenceD1
  $d1ObjectRoot = Split-Path -Parent $source
  $d1Root = Split-Path -Parent $d1ObjectRoot
  if ((Split-Path -Leaf $d1Root) -cne "d1") { throw "权威 D1 不在固定 Wrangler persist/v3/d1 根内" }
  $v3Root = Split-Path -Parent $d1Root
  $r2Root = Join-Path $v3Root "r2\miniflare-R2BucketObject"
  if (-not (Test-Path -LiteralPath $r2Root -PathType Container)) { throw "固定 Wrangler R2 metadata 根不存在" }
  $directory = Join-Path $ErpReferenceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $output = Join-Path $directory "erp-reference-r2-retirement-evidence.json"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    (Join-Path $InstalledAppRoot "tools\erp-reference-r2-retirement-evidence.py"),
    "--r2-root", $r2Root, "--output", $output
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog (Join-Path $LogDirectory "erp-reference-r2-evidence.$RunId.log") "erp_reference_r2_evidence" $nativeRun
  Write-Output ((ConvertFrom-UniqueNativeJson $nativeRun "证明ERP 主数据旧 R2 命名空间为空") | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-ErpReferenceRetirement([bool]$Apply) {
  Assert-InstalledErpReferenceOperator
  Assert-ErpReferenceStackStopped "执行ERP 主数据 D1/R2 终态退役"
  Assert-ErpReferenceWorkerStopped "执行ERP 主数据 D1/R2 终态退役"
  if ($ApprovedRunId -notmatch "^erp-reference-[0-9a-f]{32}$") { throw "ERP 主数据退役需要有效 verify run id" }
  if ($ErpReferenceCutoverId -notmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "ERP 主数据退役需要有效 cutover id" }
  $smoke = Resolve-ErpReferenceEvidencePath $SmokeReceipt "ERP 主数据系统测试 receipt"
  $r2 = Resolve-ErpReferenceEvidencePath $R2Evidence "ERP 主数据 R2 退役证据"
  $arguments = @(
    "retire_erp_reference_d1", "--source", (Resolve-LiveErpReferenceD1),
    "--cutover-id", $ErpReferenceCutoverId,
    "--approved-run-id", $ApprovedRunId,
    "--smoke-receipt", $smoke, "--r2-evidence", $r2
  )
  if ($Apply) {
    if ($ApprovedRetirementPlanId -notmatch "^[0-9a-f]{64}$") { throw "ERP 主数据 retirement apply 需要精确 plan id" }
    $directory = Join-Path $ErpReferenceAuditRoot $RunId
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $arguments += @(
      "--apply", "--approved-plan-id", $ApprovedRetirementPlanId,
      "--audit-output", (Join-Path $directory "erp-reference-retirement-audit.json")
    )
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRetirementPlanId)) { throw "ERP 主数据 retirement plan 不接受 approved plan id" }
  $payload = Invoke-ErpReferenceManagementCommand $arguments ($(if ($Apply) { "erp_reference_retirement_apply" } else { "erp_reference_retirement_plan" }))
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 10)
}

Invoke-WithServiceMutex {
  switch ($Action) {
    "PrepareRuntime" { Invoke-ErpReferencePrepareRuntime }
    "Snapshot" { Invoke-ErpReferenceSnapshot }
    "MigrateDryRun" { Invoke-ErpReferenceMigration "dry-run" }
    "MigrateApply" { Invoke-ErpReferenceMigration "apply" }
    "MigrateVerify" { Invoke-ErpReferenceMigration "verify" }
    "InstallD1Authority" { Invoke-InstallErpReferenceD1Authority }
    "AuthorityStatus" { Invoke-ErpReferenceAuthority "status" }
    "AuthorityPrepare" { Invoke-ErpReferenceAuthority "prepare" }
    "AuthorityAbort" { Invoke-ErpReferenceAuthority "abort" }
    "AuthorityActivate" { Invoke-ErpReferenceAuthority "activate" }
    "R2Evidence" { Invoke-ErpReferenceR2Evidence }
    "RetirementPlan" { Invoke-ErpReferenceRetirement $false }
    "RetirementApply" { Invoke-ErpReferenceRetirement $true }
  }
}
