$ErrorActionPreference = "Stop"

$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) (
  "tds-status-bounded-" + [Guid]::NewGuid().ToString("N")
)
$previousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY",
  "Process"
)

try {
  New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $testRoot

  $script:rootAclChecks = 0
  $script:deepAclChecks = 0
  function Assert-RuntimeRootAclHardened {
    $script:rootAclChecks += 1
  }
  function Assert-RuntimeAclHardened {
    $script:deepAclChecks += 1
    throw "deep ACL verification must not run from Status"
  }
  function Get-PortListeners([int]$Port) { return @() }
  function Resolve-OwnedProcess { return $null }
  function Get-ErpReferenceSyncCandidates { return @() }
  function Invoke-WebRequest {
    return [pscustomobject]@{ StatusCode = 200 }
  }
  function Get-ServiceConfig {
    return [pscustomobject]@{ erpSourceD1 = (Join-Path $testRoot "fixture.sqlite") }
  }

  $watch = [Diagnostics.Stopwatch]::StartNew()
  $output = Show-ServiceStatus | Out-String
  $watch.Stop()

  if ($script:rootAclChecks -ne 1 -or $script:deepAclChecks -ne 0) {
    throw "Status did not use exactly one root-only ACL verification"
  }
  if ($output -notmatch "RuntimeAcl\s*:\s*root_hardened" -or
      $output -notmatch "RuntimeAclVerification\s*:\s*root_only_status") {
    throw "Status did not label the bounded ACL verification scope"
  }
  if ($watch.Elapsed.TotalSeconds -ge 5) {
    throw "Isolated Status fixture exceeded its bounded runtime"
  }

  [pscustomobject][ordered]@{
    status = "completed"
    rootAclChecks = $script:rootAclChecks
    deepAclChecks = $script:deepAclChecks
    elapsedMilliseconds = [math]::Round($watch.Elapsed.TotalMilliseconds, 1)
  } | ConvertTo-Json -Compress
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY",
    $previousLibraryOnly,
    "Process"
  )
  if (Test-Path -LiteralPath $testRoot -PathType Container) {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
  }
}
