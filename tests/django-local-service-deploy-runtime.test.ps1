$ErrorActionPreference = "Stop"
$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$TemporaryRoot = [IO.Path]::GetTempPath()
$TestPrefix = Join-Path $TemporaryRoot "tdsrt-"
$TestRoot = $TestPrefix + [Guid]::NewGuid().ToString("N")
$TargetPrefix = Join-Path $TemporaryRoot "tdsrt-target-"
$TargetRoot = $TargetPrefix + [Guid]::NewGuid().ToString("N")
$UnsafeJunction = Join-Path $TestRoot "unsafe-junction"

try {
  & $ServiceScript -Action DeployApp -RuntimeRoot $TestRoot | Out-Null
  $deployedSupervisor = Join-Path $TestRoot "app\tools\django-runtime-supervisor.ps1"
  $sourceSupervisor = Join-Path $WorkspaceRoot "tools\django-runtime-supervisor.ps1"
  if (-not (Test-Path -LiteralPath $deployedSupervisor -PathType Leaf) -or
      (Get-FileHash -LiteralPath $deployedSupervisor -Algorithm SHA256).Hash -cne
        (Get-FileHash -LiteralPath $sourceSupervisor -Algorithm SHA256).Hash) {
    throw "DeployApp did not include the exact Django runtime supervisor"
  }

  New-Item -ItemType Directory -Path $TargetRoot -Force | Out-Null
  [IO.File]::WriteAllText(
    (Join-Path $TargetRoot "acl-sentinel.txt"), "acl-sentinel",
    [Text.UTF8Encoding]::new($false)
  )
  $targetAclBefore = (Get-Acl -LiteralPath $TargetRoot).Sddl
  New-Item -ItemType Junction -Path $UnsafeJunction -Target $TargetRoot | Out-Null
  $reparseRejected = $false
  try {
    & $ServiceScript -Action HardenAcl -RuntimeRoot $TestRoot | Out-Null
  } catch {
    $reparseRejected = $true
  }
  if (-not $reparseRejected) {
    throw "HardenAcl accepted a runtime junction"
  }
  if ((Get-Acl -LiteralPath $TargetRoot).Sddl -cne $targetAclBefore) {
    throw "HardenAcl changed the ACL of a junction target outside runtime"
  }
  $junctionItem = Get-Item -LiteralPath $UnsafeJunction -Force
  if (($junctionItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
    throw "Unsafe test junction lost its reparse-point identity"
  }
  [IO.Directory]::Delete($UnsafeJunction, $false)

  & $ServiceScript -Action HardenAcl -RuntimeRoot $TestRoot | Out-Null

  $previousLibraryOnly = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
  )
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  try {
    . $ServiceScript -Action Status -RuntimeRoot $TestRoot
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
    )
  }
  Assert-DeployedApplication
  Assert-WranglerLocalR2RoundTrip $InstalledAppRoot

  $miniflarePath = [IO.Path]::GetFullPath(
    (Join-Path $InstalledAppRoot "runtime-tools\node_modules\miniflare")
  )
  $canonicalTestRoot = [IO.Path]::GetFullPath($TestRoot).TrimEnd("\", "/")
  if (-not $miniflarePath.StartsWith(
      $canonicalTestRoot + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    ) -or -not [IO.Directory]::Exists($miniflarePath)) {
    throw "Test miniflare path escaped the unique temporary runtime"
  }
  [IO.Directory]::Delete($miniflarePath, $true)
  $rejected = $false
  try {
    Assert-WranglerRuntimeCli $InstalledAppRoot
  } catch {
    $rejected = $true
  }
  if (-not $rejected) {
    throw "Wrangler smoke did not fail closed after runtime miniflare was removed"
  }

  Write-Output "PASS: deployed supervisor and Wrangler closure survived DeployApp/HardenAcl and rejected a missing dependency"
} finally {
  $canonicalTestRoot = [IO.Path]::GetFullPath($TestRoot)
  $canonicalPrefix = [IO.Path]::GetFullPath($TestPrefix)
  $canonicalJunction = [IO.Path]::GetFullPath($UnsafeJunction)
  $safeToDeleteTestRoot = $true
  if ([IO.Directory]::Exists($canonicalJunction)) {
    $junctionItem = Get-Item -LiteralPath $canonicalJunction -Force
    if (
      ($junctionItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -and
      $canonicalJunction.StartsWith(
        $canonicalTestRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      )
    ) {
      [IO.Directory]::Delete($canonicalJunction, $false)
    } else {
      $safeToDeleteTestRoot = $false
    }
  }
  if (
    $safeToDeleteTestRoot -and
    [IO.Directory]::Exists($canonicalTestRoot) -and
    $canonicalTestRoot.StartsWith($canonicalPrefix, [StringComparison]::OrdinalIgnoreCase)
  ) {
    [IO.Directory]::Delete($canonicalTestRoot, $true)
  }
  $canonicalTargetRoot = [IO.Path]::GetFullPath($TargetRoot)
  $canonicalTargetPrefix = [IO.Path]::GetFullPath($TargetPrefix)
  if (
    [IO.Directory]::Exists($canonicalTargetRoot) -and
    $canonicalTargetRoot.StartsWith(
      $canonicalTargetPrefix,
      [StringComparison]::OrdinalIgnoreCase
    )
  ) {
    [IO.Directory]::Delete($canonicalTargetRoot, $true)
  }
}
