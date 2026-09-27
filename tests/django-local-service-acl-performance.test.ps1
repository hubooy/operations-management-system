$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSEdition -eq "Desktop") {
  $env:PSModulePath = @(
    (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "WindowsPowerShell\Modules"),
    (Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "WindowsPowerShell\Modules"),
    (Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\Modules")
  ) -join [IO.Path]::PathSeparator
}
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop

$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$TestPrefix = Join-Path ([IO.Path]::GetTempPath()) "tds-acl-performance-"
$TestRoot = $TestPrefix + [Guid]::NewGuid().ToString("N")
$previousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)

try {
  [IO.Directory]::CreateDirectory($TestRoot) | Out-Null
  for ($directoryIndex = 0; $directoryIndex -lt 100; $directoryIndex++) {
    $directory = Join-Path $TestRoot ("items-{0:D3}" -f $directoryIndex)
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    for ($fileIndex = 0; $fileIndex -lt 50; $fileIndex++) {
      [IO.File]::WriteAllText(
        (Join-Path $directory ("item-{0:D3}.txt" -f $fileIndex)),
        "fixture",
        [Text.UTF8Encoding]::new($false)
      )
    }
  }

  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $TestRoot
  Set-RuntimeRootDacl $TestRoot (New-RuntimeRootDacl)
  Reset-RuntimeDescendantDaclWithIcacls $TestRoot

  $timer = [Diagnostics.Stopwatch]::StartNew()
  Assert-RuntimeAclHardened
  $timer.Stop()
  if ($timer.ElapsedMilliseconds -ge 5000) {
    throw "5,101-object exact ACL verification exceeded 5 seconds: $($timer.ElapsedMilliseconds)ms"
  }
  [ordered]@{
    status = "passed"
    objectCount = 5101
    elapsedMilliseconds = [int64]$timer.ElapsedMilliseconds
    budgetMilliseconds = 5000
  } | ConvertTo-Json -Compress
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
  )
  $canonicalRoot = [IO.Path]::GetFullPath($TestRoot)
  $canonicalPrefix = [IO.Path]::GetFullPath($TestPrefix)
  if (
    [IO.Directory]::Exists($canonicalRoot) -and
    $canonicalRoot.StartsWith($canonicalPrefix, [StringComparison]::OrdinalIgnoreCase)
  ) {
    [IO.Directory]::Delete($canonicalRoot, $true)
  }
}
