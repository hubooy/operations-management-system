$ErrorActionPreference = "Stop"
$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TestRoot = Join-Path $WorkspaceRoot (".runtime\d1-config-test-" + [Guid]::NewGuid().ToString("N"))
$ExpectedPrefix = Join-Path $WorkspaceRoot ".runtime\d1-config-test-"
$PreviousLibraryOnly = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
try {
  . (Join-Path $WorkspaceRoot "tools\django-local-service.ps1") -RuntimeRoot $TestRoot
  New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
  $FixtureConfig = [ordered]@{
    version = 5
    readerAddress = "127.0.0.1:8001"; writerAddress = "127.0.0.1:8002"
    financeReaderAddress = "127.0.0.1:8011"; financeWriterAddress = "127.0.0.1:8012"
    customerServiceReaderAddress = "127.0.0.1:8071"; customerServiceWriterAddress = "127.0.0.1:8072"
    postgresAddress = "127.0.0.1:5432"
    erpSourceD1 = Join-Path $TestRoot "missing.sqlite"
  }
  function Save-Fixture { [IO.File]::WriteAllText($ConfigPath, ($FixtureConfig | ConvertTo-Json), [Text.UTF8Encoding]::new($false)) }
  function Assert-Rejected([scriptblock]$Call) {
    $Rejected = $false
    try { & $Call | Out-Null } catch { $Rejected = $true }
    if (-not $Rejected) { throw "Expected invalid fixture to fail closed" }
  }
  Save-Fixture
  $Read = Get-ServiceConfig
  if ($Read.erpSourceD1 -cne $FixtureConfig.erpSourceD1) { throw "Legacy lineage metadata changed" }
  Assert-Rejected { Get-ServiceConfig -RequireLegacyD1 }
  $FixtureConfig.version = 6
  $FixtureConfig.backend = "django-postgresql"
  $FixtureConfig.Remove("erpSourceD1")
  Save-Fixture
  Get-ServiceConfig | Out-Null
  $FixtureConfig.backend = "d1"
  Save-Fixture
  Assert-Rejected { Get-ServiceConfig }
  $FixtureConfig.backend = "django-postgresql"
  $FixtureConfig.readerAddress = "127.0.0.1:9999"
  Save-Fixture
  Assert-Rejected { Get-ServiceConfig }
  if (Test-Path -LiteralPath (Join-Path $TestRoot "missing.sqlite")) { throw "Configuration read created a D1 file" }
  Write-Output "D1 metadata compatibility and fail-closed checks passed"
} finally {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = $PreviousLibraryOnly
  $ResolvedTestRoot = [IO.Path]::GetFullPath($TestRoot)
  if (-not $ResolvedTestRoot.StartsWith($ExpectedPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe test cleanup target" }
  if (Test-Path -LiteralPath $ResolvedTestRoot) { Remove-Item -LiteralPath $ResolvedTestRoot -Recurse -Force }
}
