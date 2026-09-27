$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') {
  $env:PSModulePath = (Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell/v1.0/Modules')
}
$workspace = Split-Path -Parent $PSScriptRoot
$prefix = Join-Path ([IO.Path]::GetTempPath()) 'trp-'
$root = $prefix + [Guid]::NewGuid().ToString('N')
$previous = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = '1'
  . (Join-Path $workspace 'tools/django-local-service.ps1') -RuntimeRoot $root
  # Only filesystem deployment is under test. The isolated tree contains no
  # credentials, database, service receipts, processes or production storage.
  function Assert-ApplicationDeploymentStopped {
    if ((Get-CanonicalPath $RuntimeRoot) -ine (Get-CanonicalPath $root) -or
        -not $RuntimeRoot.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture root escaped' }
  }
  $timer = [Diagnostics.Stopwatch]::StartNew()
  $prepared = Prepare-Application
  if (Test-Path -LiteralPath $InstalledAppRoot) { throw 'Preparation activated app/' }
  $PreparedAppId = $prepared.id; $PreparedAppSha256 = $prepared.receiptSha256
  $stage = Get-PreparedApplication $PreparedAppId $PreparedAppSha256
  $preparationMilliseconds = $timer.ElapsedMilliseconds
  $timer.Restart()
  Deploy-Application
  Assert-DeployedApplication
  $deploymentMilliseconds = $timer.ElapsedMilliseconds
  if (Test-Path -LiteralPath $stage) { throw 'Prepared stage was not consumed by activation' }
  $binding = Get-FileSha256 $DeploymentManifestPath
  $timer.Restart()
  $next = Prepare-Application
  if ((Get-FileSha256 $DeploymentManifestPath) -cne $binding) { throw 'Online preparation changed the installed app' }
  $nextStage = Get-PreparedApplication $next.id $next.receiptSha256
  if (-not (Test-Path -LiteralPath $nextStage)) { throw 'Second candidate missing' }
  Write-Output ("PASS: real copy/hash/R2 preparation and activation; prepareMs={0} deployMs={1} nextPrepareMs={2}" -f $preparationMilliseconds,$deploymentMilliseconds,$timer.ElapsedMilliseconds)
} finally {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = $previous
  $canonical = [IO.Path]::GetFullPath($root)
  if (-not $canonical.StartsWith([IO.Path]::GetFullPath($prefix), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup' }
  if (Test-Path -LiteralPath $canonical) { Remove-Item -LiteralPath $canonical -Recurse -Force }
}
