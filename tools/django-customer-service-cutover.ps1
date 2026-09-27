[CmdletBinding()]
param(
  [ValidateSet(
    "Snapshot", "MigrateDryRun", "MigrateApply", "MigrateVerify",
    "InstallD1Authority", "AuthorityStatus", "AuthorityPrepare",
    "AuthorityAbort", "AuthorityActivate", "R2Evidence",
    "RetirementPlan", "RetirementApply"
  )]
  [string]$Action = "AuthorityStatus",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$CustomerServiceD1 = "",
  [string]$CustomerServiceSource = "",
  [string]$ApprovedRunId = "",
  [string]$CustomerServiceCutoverId = "",
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
$CustomerServiceAuditRoot = Join-Path $RuntimeRoot "audits\customer-service-cutover"
$CustomerServiceReaderPidPath = Join-Path $RunDirectory "django-customer-service-reader.pid.json"
$CustomerServiceWriterPidPath = Join-Path $RunDirectory "django-customer-service-writer.pid.json"

function Assert-InstalledCustomerServiceOperator {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) { throw "客服 cutover 必须从 DeployApp 后的受保护 runtime app 执行" }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪，拒绝执行客服 cutover" }
}

function Resolve-LiveCustomerServiceD1 {
  if ([string]::IsNullOrWhiteSpace($CustomerServiceD1) -or -not (Test-FullyQualifiedPath $CustomerServiceD1)) { throw "客服 cutover 必须提供精确的客服 D1 绝对路径" }
  $canonical = Get-CanonicalPath $CustomerServiceD1
  if (-not (Test-Path -LiteralPath $canonical -PathType Leaf) -or [IO.Path]::GetExtension($canonical) -notin @(".sqlite", ".sqlite3")) { throw "客服 D1 必须是普通 .sqlite 或 .sqlite3 文件" }
  return $canonical
}

function Resolve-CustomerServiceSnapshot([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) { throw "客服迁移需要绝对路径的受控源快照" }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $CustomerServiceAuditRoot
  if (-not $canonical.StartsWith($auditRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "客服源快照必须位于 customer-service-cutover 审计目录" }
  if (-not (Test-Path -LiteralPath $canonical -PathType Leaf) -or [IO.Path]::GetExtension($canonical) -ine ".sqlite") { throw "客服源快照不存在或不是 .sqlite 文件" }
  return $canonical
}

function Resolve-CustomerServiceEvidencePath([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) { throw "$Label 必须是客服 audit 目录内的绝对文件路径" }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $CustomerServiceAuditRoot
  if (-not $canonical.StartsWith($auditRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $canonical -PathType Leaf)) { throw "$Label 不在受保护客服切换 audit 目录或文件不存在" }
  return $canonical
}

function Assert-CustomerServiceStackStopped([string]$Operation) {
  if (Resolve-OwnedProcess "django-customer-service-reader" $CustomerServiceReaderPidPath $Waitress) { throw "$Operation 前必须通过客服控制器停止 reader" }
  if (Resolve-OwnedProcess "django-customer-service-writer" $CustomerServiceWriterPidPath $Waitress) { throw "$Operation 前必须通过客服控制器停止 writer" }
  if (@(Get-PortListeners 8071).Count -gt 0 -or @(Get-PortListeners 8072).Count -gt 0) { throw "$Operation 前端口 8071/8072 必须没有监听者" }
}

function Assert-CustomerServiceWorkerStopped([string]$Operation) {
  if (@(Get-PortListeners 3000).Count -gt 0) { throw "$Operation 前必须通过统一控制器停止 Worker" }
}

function Invoke-CustomerServiceManagementCommand([string[]]$Arguments, [string]$Operation) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_customer_service_cutover" $WriterStatementTimeoutMs
  $commandArguments = @((Join-Path $BackendRoot "manage.py")) + $Arguments
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" $false $WriterMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python $commandArguments $BackendRoot
      Write-NativeDiagnosticLog (Join-Path $LogDirectory "customer-service-cutover.$RunId.log") $Operation $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun $Operation
    }
  } finally { $ownerUrl = $null; $secrets = $null }
}

function Invoke-CustomerServiceSnapshot {
  Assert-InstalledCustomerServiceOperator
  Assert-CustomerServiceStackStopped "创建客服 D1 一致快照"
  Assert-CustomerServiceWorkerStopped "创建客服 D1 一致快照"
  $source = Resolve-LiveCustomerServiceD1
  $directory = Join-Path $CustomerServiceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $snapshot = Join-Path $directory "customer-service-source.sqlite"
  $manifest = Join-Path $directory "customer-service-source-manifest.json"
  $tool = Join-Path $InstalledAppRoot "tools\customer-service-d1-snapshot.py"
  $nativeRun = Invoke-BoundedNativeProcess $Python @($tool, "--source", $source, "--output", $snapshot, "--manifest", $manifest) $InstalledAppRoot
  Write-NativeDiagnosticLog (Join-Path $LogDirectory "customer-service-snapshot.$RunId.log") "customer_service_snapshot" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "创建客服受控 D1 快照"
  Write-Output ([ordered]@{ status = "succeeded"; source = $snapshot; sourceSha256 = Get-FileSha256 $snapshot; manifest = $manifest; manifestSha256 = Get-FileSha256 $manifest; counts = $payload.counts } | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-CustomerServiceMigration([string]$Mode) {
  Assert-InstalledCustomerServiceOperator
  Assert-CustomerServiceStackStopped "执行客服正式迁移"
  Assert-CustomerServiceWorkerStopped "执行客服正式迁移"
  $source = Resolve-CustomerServiceSnapshot $CustomerServiceSource
  $arguments = @("migrate_customer_service_from_d1", "--source", $source)
  if ($Mode -eq "apply") {
    if ($ApprovedRunId -notmatch "^customer-service-plan-[0-9a-f]{32}$") { throw "客服 apply 需要有效 approved plan id" }
    $arguments += @("--apply", "--approved-plan-id", $ApprovedRunId)
  } elseif ($Mode -eq "verify") {
    if ($ApprovedRunId -notmatch "^customer-service-[0-9a-f]{32}$") { throw "客服 verify 需要有效 approved apply run id" }
    $arguments += @("--verify", "--approved-run-id", $ApprovedRunId)
  } elseif ($Mode -eq "dry-run") {
    if (-not [string]::IsNullOrWhiteSpace($ApprovedRunId)) { throw "客服 dry-run 不接受 approved run id" }
    $arguments += "--plan"
  } else { throw "未知客服迁移操作" }
  $payload = Invoke-CustomerServiceManagementCommand $arguments "customer_service_migration_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Assert-CustomerServiceWriterStopped {
  if (Resolve-OwnedProcess "django-customer-service-writer" $DjangoCustomerServiceWriterPidPath $Waitress) { throw "客服 writer 必须在 authority 变更前停止" }
  if (@(Get-PortListeners 8072).Count -gt 0) { throw "端口 8072 在 authority 变更前不得监听" }
}

function Invoke-InstallCustomerServiceD1Authority {
  Assert-InstalledCustomerServiceOperator
  Assert-CustomerServiceStackStopped "安装客服 D1 写入权威门禁"
  Assert-CustomerServiceWorkerStopped "安装客服 D1 写入权威门禁"
  $source = Resolve-LiveCustomerServiceD1
  $directory = Join-Path $CustomerServiceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $backup = Join-Path $directory "d1-before-customer-service-authority.sqlite"
  $receipt = Join-Path $directory "authority-install-receipt.json"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    (Join-Path $InstalledAppRoot "tools\customer-service-d1-authority-install.py"),
    "--source", $source, "--sql", (Join-Path $InstalledAppRoot "drizzle\0107_customer_service_write_authority.sql"),
    "--backup", $backup, "--receipt", $receipt
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog (Join-Path $LogDirectory "customer-service-authority-install.$RunId.log") "customer_service_authority_install" $nativeRun
  Write-Output ((ConvertFrom-UniqueNativeJson $nativeRun "安装客服 D1 写保护") | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-CustomerServiceAuthority([string]$Mode) {
  Assert-InstalledCustomerServiceOperator
  if ($Mode -ne "status") {
    Assert-CustomerServiceStackStopped "变更客服写入权威"
    Assert-CustomerServiceWorkerStopped "变更客服写入权威"
  } else {
    Assert-CustomerServiceWriterStopped
  }
  $source = Resolve-LiveCustomerServiceD1
  $arguments = @("customer_service_write_authority", "--source", $source)
  if ($Mode -ne "status") {
    if ($ApprovedRunId -notmatch "^customer-service-[0-9a-f]{32}$") { throw "客服 authority 变更需要有效 verify run id" }
    if ($CustomerServiceCutoverId -notmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "客服 authority 变更需要有效 cutover id" }
    $flag = switch ($Mode) { "prepare" { "--prepare" } "abort" { "--abort-pending" } "activate" { "--activate" } default { throw "未知客服 authority 操作" } }
    $arguments += @($flag, "--approved-run-id", $ApprovedRunId, "--cutover-id", $CustomerServiceCutoverId)
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRunId) -or -not [string]::IsNullOrWhiteSpace($CustomerServiceCutoverId)) { throw "客服 authority status 不接受变更参数" }
  $payload = Invoke-CustomerServiceManagementCommand $arguments "customer_service_authority_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-CustomerServiceR2Evidence {
  Assert-InstalledCustomerServiceOperator
  Assert-CustomerServiceWorkerStopped "生成客服 R2 退役证据"
  $source = Resolve-LiveCustomerServiceD1
  $d1ObjectRoot = Split-Path -Parent $source
  $d1Root = Split-Path -Parent $d1ObjectRoot
  if ((Split-Path -Leaf $d1Root) -cne "d1") { throw "权威 D1 不在固定 Wrangler persist/v3/d1 根内" }
  $v3Root = Split-Path -Parent $d1Root
  $r2Root = Join-Path $v3Root "r2\miniflare-R2BucketObject"
  if (-not (Test-Path -LiteralPath $r2Root -PathType Container)) { throw "固定 Wrangler R2 metadata 根不存在" }
  $directory = Join-Path $CustomerServiceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $output = Join-Path $directory "customer-service-r2-retirement-evidence.json"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    (Join-Path $InstalledAppRoot "tools\customer-service-r2-retirement-evidence.py"),
    "--r2-root", $r2Root, "--output", $output
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog (Join-Path $LogDirectory "customer-service-r2-evidence.$RunId.log") "customer_service_r2_evidence" $nativeRun
  Write-Output ((ConvertFrom-UniqueNativeJson $nativeRun "证明客服旧 R2 命名空间为空") | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-CustomerServiceRetirement([bool]$Apply) {
  Assert-InstalledCustomerServiceOperator
  Assert-CustomerServiceStackStopped "执行客服 D1/R2 终态退役"
  Assert-CustomerServiceWorkerStopped "执行客服 D1/R2 终态退役"
  if ($ApprovedRunId -notmatch "^customer-service-[0-9a-f]{32}$") { throw "客服退役需要有效 verify run id" }
  if ($CustomerServiceCutoverId -notmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "客服退役需要有效 cutover id" }
  $smoke = Resolve-CustomerServiceEvidencePath $SmokeReceipt "客服系统测试 receipt"
  $r2 = Resolve-CustomerServiceEvidencePath $R2Evidence "客服 R2 退役证据"
  $arguments = @(
    "retire_customer_service_d1", "--source", (Resolve-LiveCustomerServiceD1),
    "--cutover-id", $CustomerServiceCutoverId,
    "--approved-run-id", $ApprovedRunId,
    "--smoke-receipt", $smoke, "--r2-evidence", $r2
  )
  if ($Apply) {
    if ($ApprovedRetirementPlanId -notmatch "^[0-9a-f]{64}$") { throw "客服 retirement apply 需要精确 plan id" }
    $directory = Join-Path $CustomerServiceAuditRoot $RunId
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $arguments += @(
      "--apply", "--approved-plan-id", $ApprovedRetirementPlanId,
      "--audit-output", (Join-Path $directory "customer-service-retirement-audit.json")
    )
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRetirementPlanId)) { throw "客服 retirement plan 不接受 approved plan id" }
  $payload = Invoke-CustomerServiceManagementCommand $arguments ($(if ($Apply) { "customer_service_retirement_apply" } else { "customer_service_retirement_plan" }))
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 10)
}

Invoke-WithServiceMutex {
  switch ($Action) {
    "Snapshot" { Invoke-CustomerServiceSnapshot }
    "MigrateDryRun" { Invoke-CustomerServiceMigration "dry-run" }
    "MigrateApply" { Invoke-CustomerServiceMigration "apply" }
    "MigrateVerify" { Invoke-CustomerServiceMigration "verify" }
    "InstallD1Authority" { Invoke-InstallCustomerServiceD1Authority }
    "AuthorityStatus" { Invoke-CustomerServiceAuthority "status" }
    "AuthorityPrepare" { Invoke-CustomerServiceAuthority "prepare" }
    "AuthorityAbort" { Invoke-CustomerServiceAuthority "abort" }
    "AuthorityActivate" { Invoke-CustomerServiceAuthority "activate" }
    "R2Evidence" { Invoke-CustomerServiceR2Evidence }
    "RetirementPlan" { Invoke-CustomerServiceRetirement $false }
    "RetirementApply" { Invoke-CustomerServiceRetirement $true }
  }
}
