[CmdletBinding()]
param(
  [ValidateSet(
    "Snapshot", "MigrateDryRun", "MigrateApply", "MigrateVerify",
    "InstallD1Authority", "AuthorityStatus", "AuthorityPrepare",
    "AuthorityAbort", "AuthorityActivate"
  )]
  [string]$Action = "AuthorityStatus",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$FinanceSource = "",
  [string]$ApprovedRunId = "",
  [string]$VerifyRunId = "",
  [string]$FinanceCutoverId = ""
)

$ErrorActionPreference = "Stop"
$CutoverAction = $Action
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
$FinanceAuditRoot = Join-Path $RuntimeRoot "audits\finance-cutover"

function Assert-InstalledFinanceOperator {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "Finance cutover must run from the protected runtime app after DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL is not ready for finance cutover"
  }
}

function Resolve-LiveFinanceD1 {
  $config = Get-ServiceConfig
  $source = Resolve-ErpSourceD1 ([string]$config.erpSourceD1)
  return $source
}

function Resolve-FinanceSnapshot([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) {
    throw "Finance migration requires an absolute controlled snapshot path"
  }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $FinanceAuditRoot
  if (-not $canonical.StartsWith(
      $auditRoot + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Finance snapshot must be inside the protected finance-cutover audit directory"
  }
  if (-not (Test-Path -LiteralPath $canonical -PathType Leaf) -or
      [IO.Path]::GetExtension($canonical) -ine ".sqlite") {
    throw "Finance snapshot is missing or is not a .sqlite file"
  }
  return $canonical
}

function Resolve-FinanceSnapshotManifest([string]$Source) {
  $manifest = Get-CanonicalPath (Join-Path (Split-Path -Parent $Source) "finance-source-manifest.json")
  [void](Assert-RuntimeChildPath $manifest)
  if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Finance migration controlled source manifest is missing"
  }
  $payload = Read-JsonFile $manifest "Finance migration controlled source manifest"
  if (
    [string]$payload.formatVersion -cne "finance-d1-rehearsal-snapshot-v1" -or
    [string]$payload.outputSha256 -cne (Get-FileSha256 $Source) -or
    [string]$payload.sourcePathSha256 -cnotmatch "^[0-9a-f]{64}$" -or
    [string]$payload.sourceFinanceDigest -cnotmatch "^[0-9a-f]{64}$"
  ) {
    throw "Finance migration controlled source manifest does not match the snapshot"
  }
  return $manifest
}

function Invoke-FinanceManagementCommand([string[]]$Arguments, [string]$Operation) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_finance_cutover" $WriterStatementTimeoutMs
  $manage = Join-Path $BackendRoot "manage.py"
  $commandArguments = @($manage) + @($Arguments)
  $operationLabel = [string]$Operation
  $diagnosticLogPath = Join-Path $LogDirectory "finance-cutover.$RunId.log"
  if ($operationLabel -cnotmatch "^[a-z0-9_-]{1,64}$" -or
      @($commandArguments | Where-Object { [string]::IsNullOrWhiteSpace([string]$_) }).Count -gt 0) {
    throw "Finance management command contract is invalid"
  }
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" (
      $false
    ) $WriterMaxBodyBytes "" "" {
      $nativeRun = Invoke-BoundedNativeProcess (
        $Python
      ) $commandArguments $BackendRoot
      Write-NativeDiagnosticLog $diagnosticLogPath $operationLabel $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun $operationLabel
    }
  } finally {
    $ownerUrl = $null
    $secrets = $null
  }
}

function Invoke-FinanceSnapshot {
  Assert-InstalledFinanceOperator
  $source = Resolve-LiveFinanceD1
  $directory = Join-Path $FinanceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $snapshot = Join-Path $directory "finance-source.sqlite"
  $manifest = Join-Path $directory "finance-source-manifest.json"
  $tool = Join-Path $InstalledAppRoot "tools\finance_d1_rehearsal_snapshot.py"
  $authoritySql = Join-Path $InstalledAppRoot "drizzle\0093_finance_write_authority.sql"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    $tool,
    "--source", $source,
    "--output", $snapshot,
    "--authority-sql", $authoritySql,
    "--manifest", $manifest
  ) $InstalledAppRoot
  $logPath = Join-Path $LogDirectory "finance-snapshot.$RunId.log"
  Write-NativeDiagnosticLog $logPath "finance_snapshot" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "create controlled finance D1 snapshot"
  Write-Output ([ordered]@{
    status = "succeeded"
    source = $snapshot
    sourceSha256 = Get-FileSha256 $snapshot
    manifest = $manifest
    manifestSha256 = Get-FileSha256 $manifest
    sourceFinanceDigest = [string]$payload.sourceFinanceDigest
  } | ConvertTo-Json -Compress)
}

function Invoke-FinanceMigration([string]$Mode) {
  Assert-InstalledFinanceOperator
  $source = Resolve-FinanceSnapshot $FinanceSource
  $manifest = Resolve-FinanceSnapshotManifest $source
  $arguments = @(
    "migrate_finance_from_d1", "--source", $source,
    "--source-manifest", $manifest
  )
  if ($Mode -eq "apply") {
    if ($ApprovedRunId -cnotmatch "^[0-9a-f]{32}$") { throw "Finance apply requires a valid approved run id" }
    $arguments += @("--apply", "--approved-run-id", $ApprovedRunId)
  } elseif ($Mode -eq "verify") {
    if ($ApprovedRunId -cnotmatch "^[0-9a-f]{32}$") { throw "Finance verify requires a valid approved run id" }
    $arguments += @("--verify-only", "--approved-run-id", $ApprovedRunId)
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRunId)) {
    throw "Finance dry-run does not accept an approved run id"
  }
  $payload = Invoke-FinanceManagementCommand $arguments "finance_migration_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Assert-FinanceWriterStopped {
  if (Resolve-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress) {
    throw "Finance writer must be stopped before an authority transition"
  }
  if (@(Get-PortListeners 8012).Count -gt 0) {
    throw "Port 8012 must have no listener before an authority transition"
  }
}

function Invoke-InstallFinanceD1Authority {
  Assert-InstalledFinanceOperator
  Assert-FinanceWriterStopped
  $source = Resolve-LiveFinanceD1
  $directory = Join-Path $FinanceAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $backup = Join-Path $directory "d1-before-finance-authority.sqlite"
  $receipt = Join-Path $directory "authority-install-receipt.json"
  $tool = Join-Path $InstalledAppRoot "tools\finance-d1-authority-install.py"
  $sql = Join-Path $InstalledAppRoot "drizzle\0093_finance_write_authority.sql"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    $tool,
    "--source", $source,
    "--sql", $sql,
    "--backup", $backup,
    "--receipt", $receipt
  ) $InstalledAppRoot
  $logPath = Join-Path $LogDirectory "finance-authority-install.$RunId.log"
  Write-NativeDiagnosticLog $logPath "finance_authority_install" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "install D1 finance write guard"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 6)
}

function Invoke-FinanceAuthority([string]$Mode) {
  Assert-InstalledFinanceOperator
  Assert-FinanceWriterStopped
  $source = Resolve-LiveFinanceD1
  $arguments = @("finance_write_authority", "--source", $source)
  if ($Mode -ne "status") {
    if ($VerifyRunId -cnotmatch "^[0-9a-f]{32}$") { throw "Finance authority transition requires a valid verify run id" }
    if ($FinanceCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "Finance authority transition requires a valid cutover id" }
    $flag = switch ($Mode) {
      "prepare" { "--prepare" }
      "abort" { "--abort-pending" }
      "activate" { "--activate" }
      default { throw "Unknown finance authority action" }
    }
    $arguments += @($flag, "--verify-run-id", $VerifyRunId, "--cutover-id", $FinanceCutoverId)
  } elseif (
    -not [string]::IsNullOrWhiteSpace($VerifyRunId) -or
    -not [string]::IsNullOrWhiteSpace($FinanceCutoverId)
  ) {
    throw "Finance authority status does not accept transition parameters"
  }
  $payload = Invoke-FinanceManagementCommand $arguments "finance_authority_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

Invoke-WithServiceMutex {
  switch ($Action) {
    "Snapshot" { Invoke-FinanceSnapshot }
    "MigrateDryRun" { Invoke-FinanceMigration "dry-run" }
    "MigrateApply" { Invoke-FinanceMigration "apply" }
    "MigrateVerify" { Invoke-FinanceMigration "verify" }
    "InstallD1Authority" { Invoke-InstallFinanceD1Authority }
    "AuthorityStatus" { Invoke-FinanceAuthority "status" }
    "AuthorityPrepare" { Invoke-FinanceAuthority "prepare" }
    "AuthorityAbort" { Invoke-FinanceAuthority "abort" }
    "AuthorityActivate" { Invoke-FinanceAuthority "activate" }
  }
}
