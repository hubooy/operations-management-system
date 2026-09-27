[CmdletBinding()]
param(
  [ValidateSet("PrepareRuntime", "Snapshot", "MigrateDryRun", "MigrateApply", "MigrateVerify", "InstallD1Authority", "AuthorityPrepare", "AuthorityActivate", "Smoke", "RetirementPlan", "RetirementApply")]
  [string]$Action,
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$Source = "",
  [string]$ApprovedRunId = "",
  [string]$CutoverId = "",
  [string]$ReleaseRoot = "",
  [string]$SmokeReceipt = "",
  [string]$ApprovedPlanId = ""
)
$ErrorActionPreference = "Stop"
$AccessCutoverRequest = [pscustomobject]@{ Action=$Action; Source=$Source; ApprovedRunId=$ApprovedRunId; CutoverId=$CutoverId; ReleaseRoot=$ReleaseRoot; SmokeReceipt=$SmokeReceipt; ApprovedPlanId=$ApprovedPlanId }
$previousLibraryMode = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process")
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . (Join-Path $PSScriptRoot "django-local-service.ps1") -Action Status -RuntimeRoot $RuntimeRoot
} finally { [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryMode, "Process") }

function Assert-AccessCutoverPath([string]$Value, [bool]$AuditOnly) {
  if (-not (Test-FullyQualifiedPath $Value)) { throw "必须提供精确绝对文件路径" }
  $target = Get-CanonicalPath $Value
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "源或证据文件不存在" }
  $entry = Get-Item -LiteralPath $target -Force
  while ($null -ne $entry) {
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "源与证据路径全链不得包含重解析点" }
    $entry = if ($entry -is [IO.DirectoryInfo]) { $entry.Parent } else { $entry.Directory }
  }
  if ($AuditOnly -and -not $target.StartsWith((Join-Path $RuntimeRoot "audits\access-control-cutover") + "\", [StringComparison]::OrdinalIgnoreCase)) { throw "迁移快照/证据必须在权限域受保护审计目录" }
  return $target
}

function Invoke-AccessCutoverCommand([string[]]$Arguments) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_access_control_cutover" $WriterStatementTimeoutMs
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" $false $WriterMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python (@((Join-Path $BackendRoot "manage.py")) + $Arguments) $BackendRoot
      Write-NativeDiagnosticLog (Join-Path $LogDirectory "access-control-cutover.$RunId.log") "access_control_cutover" $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun "access_control_cutover"
    }
  } finally { $ownerUrl=$null; $secrets=$null }
}

Invoke-WithServiceMutex {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) { throw "权限 cutover 必须从 DeployApp 后的受保护 runtime 执行" }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  $operation = [string]$AccessCutoverRequest.Action
  if ($operation -ne "Smoke") {
    if (@(Get-PortListeners 3000).Count -gt 0 -or @(Get-PortListeners 8102).Count -gt 0) { throw "必须先受控停止 Worker 与权限 writer" }
  }
  if ($operation -eq "PrepareRuntime") {
    Assert-ApplicationProcessesStopped "权限域 schema 迁移"
    Start-Postgres | Out-Null
    $secrets = Read-Secrets
    try { Invoke-DjangoMigrations $secrets } finally { $secrets=$null }
    Write-Output '{"status":"prepared","postgres":"running","migrations":"applied"}'
    return
  }
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪" }
  $auditDirectory = Join-Path $RuntimeRoot ("audits\access-control-cutover\" + $RunId)
  New-Item -ItemType Directory -Path $auditDirectory -Force | Out-Null
  $output = Join-Path $auditDirectory ($operation + ".json")
  $auditSource = $operation -in @("MigrateDryRun", "MigrateApply", "MigrateVerify")
  $source = Assert-AccessCutoverPath ([string]$AccessCutoverRequest.Source) $auditSource
  if (-not $auditSource) {
    $fixedD1 = "D:\运营管理系统\.wrangler\state\v3\d1\miniflare-D1DatabaseObject\faaf2b0445ab934c3aac48ddf0cdfade8f9bac050be98993748742cdd2cb05fb.sqlite"
    if ($source -ine $fixedD1) { throw "权限 cutover 只接受已确认本机权威 D1，不接受其他数据库" }
  }
  if ($operation -eq "Snapshot") {
    $snapshot = Join-Path $auditDirectory "access-control-source.sqlite"
    $nativeRun = Invoke-BoundedNativeProcess $Python @((Join-Path $InstalledAppRoot "tools\sqlite-consistent-backup.py"), "--source", $source, "--destination", $snapshot) $InstalledAppRoot
    Write-NativeDiagnosticLog (Join-Path $LogDirectory "access-control-snapshot.$RunId.log") "access_control_snapshot" $nativeRun
    $payload = ConvertFrom-UniqueNativeJson $nativeRun "access_control_snapshot"
    Write-AtomicJson $output $payload
    Write-Output (@{status="completed"; source=$snapshot; sha256=Get-FileSha256 $snapshot; receipt=$output} | ConvertTo-Json -Compress)
    return
  }
  if ($auditSource) {
    $mode = @{MigrateDryRun="dry-run"; MigrateApply="apply"; MigrateVerify="verify-only"}[$operation]
    $arguments = @("migrate_access_control_from_d1", "--source", $source, "--mode", $mode)
    if ($operation -eq "MigrateApply") { $arguments += @("--approve-run-id", [string]$AccessCutoverRequest.ApprovedRunId) }
  } elseif ($operation -in @("AuthorityPrepare", "AuthorityActivate")) {
    $flag = if ($operation -eq "AuthorityPrepare") { "--prepare" } else { "--activate" }
    $arguments = @("access_control_write_authority", "--source", $source, $flag, "--approved-run-id", [string]$AccessCutoverRequest.ApprovedRunId, "--cutover-id", [string]$AccessCutoverRequest.CutoverId)
  } else {
    $mode = @{InstallD1Authority="install-authority"; Smoke="smoke"; RetirementPlan="retirement-plan"; RetirementApply="retirement-apply"}[$operation]
    $arguments = @("access_control_cutover_check", "--action", $mode, "--source", $source, "--output", $output)
    if ($operation -ne "InstallD1Authority") { $arguments += @("--approved-run-id", [string]$AccessCutoverRequest.ApprovedRunId, "--cutover-id", [string]$AccessCutoverRequest.CutoverId) }
    if ($operation -eq "Smoke") { $arguments += @("--release-root", [string]$AccessCutoverRequest.ReleaseRoot) }
    if ($operation -in @("RetirementPlan", "RetirementApply")) { $arguments += @("--smoke-receipt", (Assert-AccessCutoverPath ([string]$AccessCutoverRequest.SmokeReceipt) $true)) }
    if ($operation -eq "RetirementApply") { $arguments += @("--approved-plan-id", [string]$AccessCutoverRequest.ApprovedPlanId) }
  }
  $payload = Invoke-AccessCutoverCommand $arguments
  if (-not (Test-Path -LiteralPath $output)) { Write-AtomicJson $output $payload }
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 12)
}
