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
$TemporaryRoot = [IO.Path]::GetTempPath()
$TestPrefix = Join-Path $TemporaryRoot "tds-acl-"
$TestRoot = $TestPrefix + [Guid]::NewGuid().ToString("N")
$OutsidePrefix = Join-Path $TemporaryRoot "tds-acl-outside-"
$OutsideRoot = $OutsidePrefix + [Guid]::NewGuid().ToString("N")
$RaceChild = Join-Path $TestRoot "race-child"
$RaceJunctionCreated = $false
$previousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)
$previousSystemRoot = $env:SystemRoot

try {
  New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
  New-Item -ItemType Directory -Path $OutsideRoot -Force | Out-Null
  [IO.File]::WriteAllText(
    (Join-Path $OutsideRoot "outside-sentinel.txt"), "outside",
    [Text.UTF8Encoding]::new($false)
  )
  $outsideAclBefore = (Get-Acl -LiteralPath $OutsideRoot).Sddl

  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $TestRoot

  $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
    [Security.AccessControl.InheritanceFlags]::ObjectInherit
  $rootAcl = New-RuntimeRootDacl
  try {
    function Set-Acl {
      [CmdletBinding()]
      param([string]$LiteralPath, [object]$AclObject)
      throw [System.Security.AccessControl.PrivilegeNotHeldException]::new(
        "SeSecurityPrivilege"
      )
    }
    Set-RuntimeRootDacl $TestRoot (New-RuntimeRootDacl)
  } finally {
    Remove-Item -LiteralPath Function:\Set-Acl -ErrorAction SilentlyContinue
  }

  $auditBundle = Join-Path $TestRoot "rehearsals\fixture\audit\smoke-receipt-bundle"
  New-Item -ItemType Directory -Path $auditBundle -Force | Out-Null
  New-Item -ItemType Directory -Path $RaceChild -Force | Out-Null
  $unicodeDirectory = Join-Path $TestRoot "中文 [audit] bundle"
  New-Item -ItemType Directory -Path $unicodeDirectory -Force | Out-Null
  $auditEvidence = Join-Path $auditBundle "receipt.json"
  [IO.File]::WriteAllText(
    $auditEvidence, '{"status":"fixture"}',
    [Text.UTF8Encoding]::new($false)
  )
  $auditEvidenceHash = (Get-FileHash -LiteralPath $auditEvidence -Algorithm SHA256).Hash
  $auditEvidenceAcl = Get-Acl -LiteralPath $auditEvidence
  $auditOwnerBefore = $auditEvidenceAcl.Owner
  $auditGroupBefore = $auditEvidenceAcl.Group
  $auditEvidenceAcl.SetAccessRuleProtection($true, $true)
  Microsoft.PowerShell.Security\Set-Acl -LiteralPath $auditEvidence -AclObject $auditEvidenceAcl

  $realReset = (Get-Item Function:\Reset-RuntimeDescendantDaclWithIcacls).ScriptBlock
  $script:fallbackCount = 0
  try {
    function Reset-RuntimeDescendantDaclWithIcacls([string]$Root) {
      $script:fallbackCount += 1
      & $realReset $Root
    }
    function Set-Acl {
      [CmdletBinding()]
      param([string]$LiteralPath, [object]$AclObject)
      throw [System.Security.AccessControl.PrivilegeNotHeldException]::new(
        "SeSecurityPrivilege"
      )
    }
    Set-RuntimeDescendantDaclInheritance @(Get-RuntimeTreeItemsNoReparse) $TestRoot
  } finally {
    Remove-Item -LiteralPath Function:\Set-Acl -ErrorAction SilentlyContinue
    Set-Item -LiteralPath Function:\Reset-RuntimeDescendantDaclWithIcacls -Value $realReset
  }
  if ($script:fallbackCount -ne 1) {
    throw "PrivilegeNotHeld did not trigger exactly one descendant fallback"
  }
  if (-not (Test-Path -LiteralPath $auditEvidence -PathType Leaf)) {
    throw "ACL fallback deleted an audit evidence file"
  }
  if ((Get-FileHash -LiteralPath $auditEvidence -Algorithm SHA256).Hash -cne $auditEvidenceHash) {
    throw "ACL fallback changed audit evidence bytes"
  }
  $auditAclAfter = Get-Acl -LiteralPath $auditEvidence
  if ($auditAclAfter.AreAccessRulesProtected) {
    throw "ACL fallback did not restore descendant inheritance"
  }
  if ($auditAclAfter.Owner -cne $auditOwnerBefore -or $auditAclAfter.Group -cne $auditGroupBefore) {
    throw "DACL-only fallback changed audit evidence owner or group"
  }
  Assert-RuntimeAclHardened

  $invalidDescendantAcl = Get-Acl -LiteralPath $auditEvidence
  $invalidDescendantAcl.AddAccessRule(
    [Security.AccessControl.FileSystemAccessRule]::new(
      [Security.Principal.SecurityIdentifier]::new("S-1-5-21-111-222-333-444"),
      [Security.AccessControl.FileSystemRights]::ReadAttributes,
      [Security.AccessControl.InheritanceFlags]::None,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Deny
    )
  )
  Microsoft.PowerShell.Security\Set-Acl -LiteralPath $auditEvidence -AclObject $invalidDescendantAcl
  $explicitDenyRejected = $false
  try {
    Assert-RuntimeAclHardened
  } catch [IO.InvalidDataException] {
    $explicitDenyRejected = $true
  }
  if (-not $explicitDenyRejected) {
    throw "Exact ACL assertion accepted a descendant explicit deny"
  }
  Reset-RuntimeDescendantDaclWithIcacls $TestRoot

  $incompleteRootAcl = [Security.AccessControl.DirectorySecurity]::new()
  $incompleteRootAcl.SetAccessRuleProtection($true, $false)
  $incompleteRootAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent().User,
    [Security.AccessControl.FileSystemRights]::FullControl,
    $inheritance,
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow
  ))
  Set-DirectoryDaclOnly $TestRoot $incompleteRootAcl
  $missingPrincipalsRejected = $false
  try {
    Assert-RuntimeRootAclHardened
  } catch [IO.InvalidDataException] {
    $missingPrincipalsRejected = $true
  }
  if (-not $missingPrincipalsRejected) {
    throw "Exact ACL assertion accepted missing SYSTEM and Administrators"
  }
  Set-DirectoryDaclOnly $TestRoot $rootAcl
  Reset-RuntimeDescendantDaclWithIcacls $TestRoot
  Assert-RuntimeAclHardened

  $env:SystemRoot = Join-Path $TestRoot "spoofed-system-root"
  $selectedIcacls = Get-SystemIcaclsPath
  $expectedIcacls = Get-CanonicalPath (
    Join-Path ([Environment]::SystemDirectory) "icacls.exe"
  )
  if (-not $selectedIcacls.Equals($expectedIcacls, [StringComparison]::OrdinalIgnoreCase)) {
    throw "ACL fallback trusted the process SystemRoot override"
  }
  $env:SystemRoot = $previousSystemRoot

  $nativeFailureClosed = $false
  try {
    Invoke-IcaclsDaclOnlyChecked $env:ComSpec "/c" @(
      "exit", "/b", "7"
    ) "fixture native failure"
  } catch {
    if ($_.Exception.Message -notmatch "exitCode=7; launchFailed=False; .*outputSha256=[0-9a-f]{64}") {
      throw
    }
    $nativeFailureClosed = $true
  }
  if (-not $nativeFailureClosed) {
    throw "icacls nonzero exit was swallowed"
  }

  $realRunner = (Get-Item Function:\Invoke-IcaclsDaclOnlyChecked).ScriptBlock
  $raceRejected = $false
  try {
    function Invoke-IcaclsDaclOnlyChecked(
      [string]$Executable,
      [string]$Target,
      [string[]]$Arguments,
      [string]$FailureLabel
    ) {
      if ($Target.Equals($RaceChild, [StringComparison]::OrdinalIgnoreCase)) {
        [IO.Directory]::Delete($RaceChild, $false)
        New-Item -ItemType Junction -Path $RaceChild -Target $OutsideRoot | Out-Null
        $script:RaceJunctionCreated = $true
      }
      & $realRunner $Executable $Target $Arguments $FailureLabel
    }
    try {
      Reset-RuntimeDescendantDaclWithIcacls $TestRoot
    } catch {
      $raceRejected = $true
    }
  } finally {
    Set-Item -LiteralPath Function:\Invoke-IcaclsDaclOnlyChecked -Value $realRunner
  }
  if (-not $raceRejected) {
    throw "ACL fallback accepted a scan-to-write junction race"
  }
  if ((Get-Acl -LiteralPath $OutsideRoot).Sddl -cne $outsideAclBefore) {
    throw "ACL fallback changed a junction target outside runtime"
  }

  Write-Output "PASS: descendant ACL fallback is DACL-only, bounded, and fail-closed"
} finally {
  $env:SystemRoot = $previousSystemRoot
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
  )
  $canonicalTestRoot = [IO.Path]::GetFullPath($TestRoot)
  $canonicalTestPrefix = [IO.Path]::GetFullPath($TestPrefix)
  $canonicalRaceChild = [IO.Path]::GetFullPath($RaceChild)
  $safeToDeleteTestRoot = $true
  if ([IO.Directory]::Exists($canonicalRaceChild)) {
    $raceItem = Get-Item -LiteralPath $canonicalRaceChild -Force
    if (
      ($raceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -and
      $canonicalRaceChild.StartsWith(
        $canonicalTestRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      )
    ) {
      [IO.Directory]::Delete($canonicalRaceChild, $false)
    } elseif ($RaceJunctionCreated) {
      $safeToDeleteTestRoot = $false
    }
  }
  if (
    $safeToDeleteTestRoot -and
    [IO.Directory]::Exists($canonicalTestRoot) -and
    $canonicalTestRoot.StartsWith(
      $canonicalTestPrefix,
      [StringComparison]::OrdinalIgnoreCase
    )
  ) {
    [IO.Directory]::Delete($canonicalTestRoot, $true)
  }
  $canonicalOutsideRoot = [IO.Path]::GetFullPath($OutsideRoot)
  $canonicalOutsidePrefix = [IO.Path]::GetFullPath($OutsidePrefix)
  if (
    [IO.Directory]::Exists($canonicalOutsideRoot) -and
    $canonicalOutsideRoot.StartsWith(
      $canonicalOutsidePrefix,
      [StringComparison]::OrdinalIgnoreCase
    )
  ) {
    [IO.Directory]::Delete($canonicalOutsideRoot, $true)
  }
}
