$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$TestRoot = Join-Path $WorkspaceRoot (
  ".runtime\service-fingerprint-test-" + [Guid]::NewGuid().ToString("N")
)
$ExpectedPrefix = Join-Path $WorkspaceRoot ".runtime\service-fingerprint-test-"
$AppRoot = Join-Path $TestRoot "app"
$previousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)
$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"

function Write-TestText([string]$Path, [string]$Value) {
  $parent = [IO.Path]::GetDirectoryName($Path)
  if (-not [string]::IsNullOrWhiteSpace($parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
}

function Assert-SameEvidence([object]$Left, [object]$Right, [string]$Label) {
  if ([string]$Left.Algorithm -cne [string]$Right.Algorithm -or
      [int64]$Left.FileCount -ne [int64]$Right.FileCount -or
      [string]$Left.Fingerprint -cne [string]$Right.Fingerprint) {
    throw "$Label fingerprint evidence changed"
  }
  $leftRows = [string[]]@($Left.Rows)
  $rightRows = [string[]]@($Right.Rows)
  if ($leftRows.Count -ne $rightRows.Count) {
    throw "$Label row count changed"
  }
  for ($index = 0; $index -lt $leftRows.Count; $index++) {
    if ($leftRows[$index] -cne $rightRows[$index]) {
      throw "$Label row ordering or digest changed"
    }
  }
}

try {
  . $ServiceScript -RuntimeRoot $TestRoot
  New-Item -ItemType Directory -Path $AppRoot -Force | Out-Null

  $chineseName = ([string][char]0x4e2d) + ([string][char]0x6587) + ".txt"
  $composedName = ([string][char]0x00e9) + ".txt"
  $decomposedName = "e" + ([string][char]0x0301) + ".txt"
  $contentPath = Join-Path $AppRoot "names\content.txt"
  $removePath = Join-Path $AppRoot "names\remove-me.txt"
  $fixture = @(
    [pscustomobject]@{ Relative = "CaseUpper\item.txt"; Value = "upper" },
    [pscustomobject]@{ Relative = "case-lower\item.txt"; Value = "lower" },
    [pscustomobject]@{ Relative = "names\Alpha.txt"; Value = "alpha" },
    [pscustomobject]@{ Relative = "names\beta.txt"; Value = "beta" },
    [pscustomobject]@{ Relative = "names\under_score.txt"; Value = "underscore" },
    [pscustomobject]@{ Relative = "names\dot.name.txt"; Value = "dot" },
    [pscustomobject]@{ Relative = ("names\" + $chineseName); Value = "chinese-name" },
    [pscustomobject]@{ Relative = ("names\" + $composedName); Value = "composed-name" },
    [pscustomobject]@{ Relative = ("names\" + $decomposedName); Value = "decomposed-name" },
    [pscustomobject]@{ Relative = "names\content.txt"; Value = "stable-content" },
    [pscustomobject]@{ Relative = "names\remove-me.txt"; Value = "remove-me" }
  )
  foreach ($entry in $fixture) {
    Write-TestText (Join-Path $AppRoot ([string]$entry.Relative)) ([string]$entry.Value)
  }
  Write-TestText (Join-Path $AppRoot "deployment.json") '{"excluded":true}'
  Write-TestText (Join-Path $AppRoot "__pycache__\ignored.py") "ignored-cache"
  Write-TestText (Join-Path $AppRoot ".pytest_cache\ignored.txt") "ignored-pytest"
  Write-TestText (Join-Path $AppRoot ".mypy_cache\ignored.txt") "ignored-mypy"
  Write-TestText (Join-Path $AppRoot "ignored.pyc") "ignored-bytecode"
  Write-TestText (Join-Path $AppRoot "ignored.pyo") "ignored-optimized-bytecode"

  $baseline = Get-ApplicationTreeFingerprintEvidence $AppRoot
  if ([string]$baseline.Algorithm -cne "relative-path-file-sha256-ordinal-v2" -or
      [int64]$baseline.FileCount -ne $fixture.Count -or
      [int64]$baseline.FileCount -ne @($baseline.Rows).Count -or
      [string]$baseline.Fingerprint -cnotmatch "^[0-9a-f]{64}$") {
    throw "baseline fingerprint evidence is invalid"
  }
  $paths = @($baseline.Rows | ForEach-Object { ([string]$_ -split "`n", 2)[0] })
  if ($paths -contains "deployment.json" -or
      @($paths | Where-Object {
        $_ -match "(?:^|/)(?:__pycache__|\.pytest_cache|\.mypy_cache)(?:/|$)" -or
        $_ -match "\.(?:pyc|pyo)$"
      }).Count -ne 0) {
    throw "deployment.json or Python cache material entered the fingerprint"
  }
  $ordinalRows = [string[]]@($baseline.Rows)
  [Array]::Sort($ordinalRows, [StringComparer]::Ordinal)
  for ($index = 0; $index -lt $ordinalRows.Count; $index++) {
    if ($ordinalRows[$index] -cne [string]$baseline.Rows[$index]) {
      throw "fingerprint rows are not ordinally sorted"
    }
  }
  Assert-SameEvidence $baseline (Get-ApplicationTreeFingerprintEvidence $AppRoot) "repeat"

  Write-TestText (Join-Path $AppRoot "deployment.json") '{"excluded":false,"changed":true}'
  Write-TestText (Join-Path $AppRoot "__pycache__\ignored.py") "changed-cache"
  Assert-SameEvidence $baseline (Get-ApplicationTreeFingerprintEvidence $AppRoot) "excluded material"

  Write-TestText $contentPath "changed-content"
  $contentChanged = Get-ApplicationTreeFingerprintEvidence $AppRoot
  if ([int64]$contentChanged.FileCount -ne [int64]$baseline.FileCount -or
      [string]$contentChanged.Fingerprint -ceq [string]$baseline.Fingerprint) {
    throw "included content change did not change only the fingerprint"
  }
  Write-TestText $contentPath "stable-content"
  Assert-SameEvidence $baseline (Get-ApplicationTreeFingerprintEvidence $AppRoot) "content restore"

  $addedPath = Join-Path $AppRoot "names\added.txt"
  Write-TestText $addedPath "added"
  $added = Get-ApplicationTreeFingerprintEvidence $AppRoot
  if ([int64]$added.FileCount -ne ([int64]$baseline.FileCount + 1) -or
      [string]$added.Fingerprint -ceq [string]$baseline.Fingerprint) {
    throw "included file addition did not change count and fingerprint"
  }
  [IO.File]::Delete($addedPath)
  Assert-SameEvidence $baseline (Get-ApplicationTreeFingerprintEvidence $AppRoot) "addition removal"

  [IO.File]::Delete($removePath)
  $removed = Get-ApplicationTreeFingerprintEvidence $AppRoot
  if ([int64]$removed.FileCount -ne ([int64]$baseline.FileCount - 1) -or
      [string]$removed.Fingerprint -ceq [string]$baseline.Fingerprint) {
    throw "included file removal did not change count and fingerprint"
  }
  Write-TestText $removePath "remove-me"
  Assert-SameEvidence $baseline (Get-ApplicationTreeFingerprintEvidence $AppRoot) "removal restore"

  [ordered]@{
    status = "completed"
    algorithm = [string]$baseline.Algorithm
    fileCount = [int64]$baseline.FileCount
    fingerprint = [string]$baseline.Fingerprint
    rows = [string[]]@($baseline.Rows)
  } | ConvertTo-Json -Compress -Depth 4
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
  )
  $canonicalTestRoot = [IO.Path]::GetFullPath($TestRoot)
  $canonicalPrefix = [IO.Path]::GetFullPath($ExpectedPrefix)
  if ([IO.Directory]::Exists($canonicalTestRoot) -and
      $canonicalTestRoot.StartsWith($canonicalPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    [IO.Directory]::Delete($canonicalTestRoot, $true)
  }
}
