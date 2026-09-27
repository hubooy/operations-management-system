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
  [string]$ApprovedPlanId = "",
  [string]$BackupDirectory = "",
  [string]$ApprovedBackupSha256 = "",
  [string]$RestoreReceipt = ""
)
$ErrorActionPreference = "Stop"
$AiCutoverRequest = [pscustomobject]@{ Action=$Action; Source=$Source; ApprovedRunId=$ApprovedRunId; CutoverId=$CutoverId; ReleaseRoot=$ReleaseRoot; SmokeReceipt=$SmokeReceipt; ApprovedPlanId=$ApprovedPlanId; BackupDirectory=$BackupDirectory; ApprovedBackupSha256=$ApprovedBackupSha256; RestoreReceipt=$RestoreReceipt }
$previousLibraryMode = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process")
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . (Join-Path $PSScriptRoot "django-local-service.ps1") -Action Status -RuntimeRoot $RuntimeRoot
} finally { [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryMode, "Process") }

function Assert-AiCutoverPath([string]$Value, [bool]$AuditOnly) {
  if (-not (Test-FullyQualifiedPath $Value)) { throw "必须提供精确绝对文件路径" }
  $target = Get-CanonicalPath $Value
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "源或证据文件不存在" }
  $entry = Get-Item -LiteralPath $target -Force
  while ($null -ne $entry) {
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "源与证据路径全链不得包含重解析点" }
    $entry = if ($entry -is [IO.DirectoryInfo]) { $entry.Parent } else { $entry.Directory }
  }
  if ($AuditOnly -and -not $target.StartsWith((Join-Path $RuntimeRoot "audits\ai-cutover") + "\", [StringComparison]::OrdinalIgnoreCase)) { throw "迁移快照/证据必须在AI 助理域受保护审计目录" }
  return $target
}

function Invoke-AiCutoverCommand([string[]]$Arguments) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_ai_cutover" $WriterStatementTimeoutMs
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" $false $WriterMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python (@((Join-Path $BackendRoot "manage.py")) + $Arguments) $BackendRoot
      Write-NativeDiagnosticLog (Join-Path $LogDirectory "ai-cutover.$RunId.log") "ai_cutover" $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun "ai_cutover"
    }
  } finally { $ownerUrl=$null; $secrets=$null }
}

Invoke-WithServiceMutex {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) { throw "AI 助理 cutover 必须从 DeployApp 后的受保护 runtime 执行" }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  $operation = [string]$AiCutoverRequest.Action
  if ($operation -ne "Smoke") {
    if (@(Get-PortListeners 3000).Count -gt 0 -or @(Get-PortListeners 8112).Count -gt 0) { throw "必须先受控停止 Worker 与AI 助理 writer" }
  }
  if ($operation -eq "PrepareRuntime") {
    Assert-ApplicationProcessesStopped "AI 助理域 schema 迁移"
    Start-Postgres | Out-Null
    $secrets = Read-Secrets
    try { Invoke-DjangoMigrations $secrets } finally { $secrets=$null }
    Write-Output '{"status":"prepared","postgres":"running","migrations":"applied"}'
    return
  }
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪" }
  if ($operation -in @("AuthorityActivate", "RetirementApply")) {
    if ([string]$AiCutoverRequest.ApprovedBackupSha256 -cnotmatch "^[0-9a-f]{64}$") { throw "AI 激活/退役需要精确批准的 PostgreSQL 备份摘要" }
    $previousMaintenanceLibrary = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY", "Process")
    try {
      $env:TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY = "1"
      . (Join-Path $PSScriptRoot "django-postgres-maintenance.ps1") -RuntimeRoot $RuntimeRoot
    } finally { [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY", $previousMaintenanceLibrary, "Process") }
    $backup = Resolve-MaintenanceBackupArchive ([string]$AiCutoverRequest.BackupDirectory) ([string]$AiCutoverRequest.ApprovedBackupSha256) -RequireCurrentDeployment
    if ([string]$backup.Manifest.evidence.aiAssistant.migrationRunId -cne [string]$AiCutoverRequest.ApprovedRunId -or ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse([string]$backup.Manifest.completedAt)).TotalHours -gt 24) { throw "备份未绑定本次 AI apply 或超过 24 小时" }
    if ($operation -eq "RetirementApply" -and ([string]$backup.Manifest.evidence.aiAssistant.status -cne "postgres" -or [string]$backup.Manifest.evidence.aiAssistant.cutoverId -cne [string]$AiCutoverRequest.CutoverId)) { throw "D1 终态退役必须使用本次 AI 激活后的 PostgreSQL 备份，不能使用激活前备份" }
    $restorePath = Assert-AiCutoverPath ([string]$AiCutoverRequest.RestoreReceipt) $false
    if (-not $restorePath.StartsWith((Join-Path $RuntimeRoot "rehearsals") + "\", [StringComparison]::OrdinalIgnoreCase)) { throw "恢复演练证据必须来自受保护的独立演练目录" }
    $restore = Read-JsonFile $restorePath "AI 切换备份恢复演练"
    if ([string]$restore.version -cne "teruisi-postgres-restore-rehearsal-v1" -or [string]$restore.status -cne "completed" -or $restore.productionDatabaseTouched -ne $false -or [string]$restore.backupManifestSha256 -cne [string]$backup.ManifestSha256 -or [string]$restore.dumpSha256 -cne [string]$backup.Manifest.dump.sha256 -or [string]$restore.expectedContentSha256 -cne [string]$restore.restoredContentSha256 -or [string]$restore.expectedContentSha256 -cne [string]$backup.Manifest.evidence.contentSha256) { throw "AI 切换缺少与备份一致的成功隔离恢复证据" }
  }
  $auditDirectory = Join-Path $RuntimeRoot ("audits\ai-cutover\" + $RunId)
  New-Item -ItemType Directory -Path $auditDirectory -Force | Out-Null
  $output = Join-Path $auditDirectory ($operation + ".json")
  $auditSource = $operation -in @("MigrateDryRun", "MigrateApply", "MigrateVerify")
  $source = Assert-AiCutoverPath ([string]$AiCutoverRequest.Source) $auditSource
  if (-not $auditSource) {
    $fixedD1 = "D:\运营管理系统\.wrangler\state\v3\d1\miniflare-D1DatabaseObject\faaf2b0445ab934c3aac48ddf0cdfade8f9bac050be98993748742cdd2cb05fb.sqlite"
    if ($source -ine $fixedD1) { throw "AI 助理 cutover 只接受已确认本机权威 D1，不接受其他数据库" }
  }
  if ($operation -eq "Snapshot") {
    $snapshot = Join-Path $auditDirectory "ai-source.sqlite"
    $nativeRun = Invoke-BoundedNativeProcess $Python @((Join-Path $InstalledAppRoot "tools\ai-domain-snapshot.py"), "--source", $source, "--destination", $snapshot) $InstalledAppRoot
    Write-NativeDiagnosticLog (Join-Path $LogDirectory "ai-snapshot.$RunId.log") "ai_snapshot" $nativeRun
    $payload = ConvertFrom-UniqueNativeJson $nativeRun "ai_snapshot"
    Write-AtomicJson $output $payload
    Write-Output (@{status="completed"; source=$snapshot; sha256=Get-FileSha256 $snapshot; receipt=$output} | ConvertTo-Json -Compress)
    return
  }
  if ($auditSource) {
    $mode = @{MigrateDryRun="dry-run"; MigrateApply="apply"; MigrateVerify="verify-only"}[$operation]
    $arguments = @("migrate_ai_from_d1", "--source", $source, "--mode", $mode)
    if ($operation -eq "MigrateApply") { $arguments += @("--approve-run-id", [string]$AiCutoverRequest.ApprovedRunId) }
  } elseif ($operation -in @("AuthorityPrepare", "AuthorityActivate")) {
    $flag = if ($operation -eq "AuthorityPrepare") { "--prepare" } else { "--activate" }
    $arguments = @("ai_write_authority", "--source", $source, $flag, "--approved-run-id", [string]$AiCutoverRequest.ApprovedRunId, "--cutover-id", [string]$AiCutoverRequest.CutoverId)
  } else {
    $mode = @{InstallD1Authority="install-authority"; Smoke="smoke"; RetirementPlan="retirement-plan"; RetirementApply="retirement-apply"}[$operation]
    $arguments = @("ai_cutover_check", "--action", $mode, "--source", $source, "--output", $output)
    if ($operation -ne "InstallD1Authority") { $arguments += @("--approved-run-id", [string]$AiCutoverRequest.ApprovedRunId, "--cutover-id", [string]$AiCutoverRequest.CutoverId) }
    if ($operation -eq "Smoke") { $arguments += @("--release-root", [string]$AiCutoverRequest.ReleaseRoot) }
    if ($operation -in @("RetirementPlan", "RetirementApply")) { $arguments += @("--smoke-receipt", (Assert-AiCutoverPath ([string]$AiCutoverRequest.SmokeReceipt) $true)) }
    if ($operation -eq "RetirementApply") { $arguments += @("--approved-plan-id", [string]$AiCutoverRequest.ApprovedPlanId) }
  }
  $payload = Invoke-AiCutoverCommand $arguments
  if (-not (Test-Path -LiteralPath $output)) { Write-AtomicJson $output $payload }
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 12)
}
