$ErrorActionPreference = "Stop"
$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$TestRoot = Join-Path $WorkspaceRoot (".runtime\service-rollback-test-" + [Guid]::NewGuid().ToString("N"))
$ExpectedPrefix = (Join-Path $WorkspaceRoot ".runtime\service-rollback-test-")
$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"

function Write-TestApplication([string]$Root, [string]$Version, [switch]$LegacyV1) {
  $wranglerRoot = Join-Path $Root "runtime-tools\node_modules\wrangler"
  New-Item -ItemType Directory -Path `
    (Join-Path $Root "backend"),
    (Join-Path $Root "tools"),
    (Join-Path $wranglerRoot "wrangler-dist") -Force | Out-Null
  [IO.File]::WriteAllText((Join-Path $Root "backend\version.txt"), $Version, $Utf8NoBom)
  [IO.File]::WriteAllText((Join-Path $Root "tools\django-local-service.ps1"), "# $Version", $Utf8NoBom)
  [IO.File]::WriteAllText(
    (Join-Path $wranglerRoot "package.json"),
    '{"name":"wrangler","version":"4.92.0"}',
    $Utf8NoBom
  )
  [IO.File]::WriteAllText(
    (Join-Path $wranglerRoot "wrangler-dist\cli.js"),
    @'
const args = process.argv.slice(2).join(" ");
if (args === "--version") {
  process.stdout.write("4.92.0\n");
  process.exit(0);
}
if (args === "r2 object delete --help") {
  process.stdout.write("wrangler r2 object delete <objectPath>\n\nDelete an object in an R2 bucket\n");
  process.exit(0);
}
process.exit(2);
'@,
    $Utf8NoBom
  )
  Write-AtomicJson (Join-Path $Root "runtime-tools\wrangler-dependencies.json") ([ordered]@{
    version = 1
    rootPackage = "wrangler"
    rootVersion = "4.92.0"
    packageLockSha256 = ("0" * 64)
    packages = @(
      [ordered]@{ path = "node_modules/wrangler"; name = "wrangler"; version = "4.92.0" },
      [ordered]@{ path = "node_modules/fake-runtime-dependency"; name = "fake-runtime-dependency"; version = "1.0.0" }
    )
  })
  if ($LegacyV1.IsPresent) {
    $fingerprint = if (Test-IsWindowsPowerShell51) {
      Get-ApplicationTreeFingerprintLegacyV1 $Root
    } else {
      "0" * 64
    }
    Write-AtomicJson (Join-Path $Root "deployment.json") ([ordered]@{
      version = 1
      deployedAt = [DateTimeOffset]::Now.ToString("o")
      sourceRoot = $WorkspaceRoot
      appFingerprint = $fingerprint
    })
  } else {
    $fingerprintEvidence = Get-ApplicationTreeFingerprintEvidence $Root
    Write-AtomicJson (Join-Path $Root "deployment.json") ([ordered]@{
      version = 2
      deployedAt = [DateTimeOffset]::Now.ToString("o")
      sourceRoot = $WorkspaceRoot
      fingerprintAlgorithm = "relative-path-file-sha256-ordinal-v2"
      fileCount = [int64]$fingerprintEvidence.FileCount
      appFingerprint = [string]$fingerprintEvidence.Fingerprint
    })
  }
}

try {
  . $ServiceScript -RuntimeRoot $TestRoot
  # The stop gate is independently source-tested and uses real global ports.
  # This isolated test replaces only that dependency so the directory swap can
  # be verified without touching the user's running local services.
  function Assert-ServiceStackStopped([string]$Operation) { }

  Write-TestApplication $InstalledAppRoot "current"
  Write-TestApplication (Join-Path $TestRoot "app.previous") "previous"
  Assert-DeployedApplication
  Rollback-Application
  Assert-DeployedApplication
  Assert-ApplicationTreeManifest (Join-Path $TestRoot "app.previous") "rollback backup" | Out-Null

  $legacyRoot = Join-Path $TestRoot "legacy.previous"
  Write-TestApplication $legacyRoot "legacy" -LegacyV1
  if (Test-IsWindowsPowerShell51) {
    Assert-ApplicationTreeManifest $legacyRoot "legacy rollback backup" | Out-Null
  } else {
    $legacyRejected = $false
    try {
      Assert-ApplicationTreeManifest $legacyRoot "legacy rollback backup" | Out-Null
    } catch {
      $legacyRejected = $_.Exception.Message -match "Windows PowerShell 5\.1"
    }
    if (-not $legacyRejected) {
      throw "pwsh did not fail closed with the Windows PowerShell 5.1 legacy guidance"
    }
  }

  $current = [IO.File]::ReadAllText((Join-Path $InstalledAppRoot "backend\version.txt"), $Utf8NoBom)
  $previous = [IO.File]::ReadAllText((Join-Path $TestRoot "app.previous\backend\version.txt"), $Utf8NoBom)
  if ($current -cne "previous" -or $previous -cne "current") {
    throw "RollbackApp did not atomically exchange current and previous code"
  }
  if (Test-Path -LiteralPath (Join-Path $TestRoot "postgres-data")) {
    throw "code-only rollback unexpectedly created or changed a database directory"
  }
  Write-Output "PASS: code-only rollback exchanged verified manifests"
} finally {
  Remove-Item Env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY -ErrorAction SilentlyContinue
  $canonicalTestRoot = [IO.Path]::GetFullPath($TestRoot)
  $canonicalPrefix = [IO.Path]::GetFullPath($ExpectedPrefix)
  if (
    [IO.Directory]::Exists($canonicalTestRoot) -and
    $canonicalTestRoot.StartsWith($canonicalPrefix, [StringComparison]::OrdinalIgnoreCase)
  ) {
    [IO.Directory]::Delete($canonicalTestRoot, $true)
  }
}
