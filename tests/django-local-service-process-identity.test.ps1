$ErrorActionPreference = "Stop"
$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$TestPython = if ($env:TERUISI_DJANGO_TEST_PYTHON) {
  $env:TERUISI_DJANGO_TEST_PYTHON
} else {
  "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $TestPython -PathType Leaf)) {
  Write-Output "SKIP: managed venv Python is not installed"
  exit 0
}

$TestRoot = Join-Path $WorkspaceRoot (".runtime\service-identity-test-" + [Guid]::NewGuid().ToString("N"))
$LogRoot = Join-Path $TestRoot "logs"
$RunRoot = Join-Path $TestRoot "run"
$PidFile = Join-Path $RunRoot "identity.json"
$Stdout = Join-Path $LogRoot "stdout.log"
$Stderr = Join-Path $LogRoot "stderr.log"
$Arguments = @("-c", "import time;time.sleep(20)")
$Fingerprint = "e" * 64
$OwnedSnapshot = $null
$DescendantSnapshots = @()
$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"

try {
  . $ServiceScript -RuntimeRoot $TestRoot
  New-Item -ItemType Directory -Path $TestRoot, $LogRoot, $RunRoot -Force | Out-Null
  # No-listener is a normal result, while CIM failures must still fail closed.
  [void]@(Get-PortListeners 65534)
  if (@(Get-ErpReferenceSyncCandidates).Count -ne 0) {
    throw "temporary runtime unexpectedly matched an ERP sync process"
  }
  Start-ManagedProcess "identity-test" $TestPython $Arguments $TestRoot $PidFile $Fingerprint $Stdout $Stderr | Out-Null
  $OwnedSnapshot = Resolve-OwnedProcess "identity-test" $PidFile $TestPython $Arguments $Fingerprint
  if (-not $OwnedSnapshot) { throw "managed process round-trip returned no process" }
  $record = Get-Content -Raw -LiteralPath $PidFile -Encoding UTF8 | ConvertFrom-Json
  $recordFromServiceReader = Read-JsonFile $PidFile "test record"
  if ((ConvertTo-CanonicalCreationDate $record.creationDate) -cne (ConvertTo-CanonicalCreationDate $recordFromServiceReader.creationDate)) {
    throw "creationDate canonicalization is not stable across JSON runtimes"
  }
  if ((Get-CanonicalPath ([string]$record.launcherPath)) -ine (Get-CanonicalPath $TestPython)) {
    throw "PID record lost the protected launcher path"
  }
  if (-not (Test-CommandLineReferencesPath ([string]$record.commandLine) $TestPython)) {
    throw "actual command line does not reference the protected launcher"
  }
  $originalRecord = Get-Content -Raw -LiteralPath $PidFile -Encoding UTF8
  $record.creationDate = "2000-01-01T00:00:00.0000000Z"
  [IO.File]::WriteAllText($PidFile, ($record | ConvertTo-Json -Depth 8), $Utf8NoBom)
  $reuseRejected = $false
  try {
    Resolve-OwnedProcess "identity-test" $PidFile $TestPython $Arguments $Fingerprint | Out-Null
  } catch {
    $reuseRejected = $true
  }
  if (-not $reuseRejected) { throw "tampered PID creation identity was accepted" }
  [IO.File]::WriteAllText($PidFile, $originalRecord, $Utf8NoBom)

  # A receipt that is provably older than the current Windows boot cannot own
  # a live process, even when that numeric PID has already been reused.  It is
  # safe to remove the receipt, but never to terminate the reused process.
  $previousBootRecord = $originalRecord | ConvertFrom-Json
  $previousBootTime = (Get-SystemBootTimeUtc).AddMinutes(-5)
  $previousBootTimestamp = $previousBootTime.ToString("o")
  $previousBootRecord.creationDate = $previousBootTimestamp
  $previousBootRecord.startedAt = $previousBootTimestamp
  [IO.File]::WriteAllText(
    $PidFile,
    ($previousBootRecord | ConvertTo-Json -Depth 8),
    $Utf8NoBom
  )
  $currentBootRewriteRejected = $false
  try {
    Resolve-OwnedProcess "identity-test" $PidFile $TestPython $Arguments $Fingerprint | Out-Null
  } catch {
    $currentBootRewriteRejected = $true
  }
  if (-not $currentBootRewriteRejected) {
    throw "a receipt rewritten during the current boot was treated as previous-boot evidence"
  }
  [IO.File]::SetLastWriteTimeUtc($PidFile, $previousBootTime)
  $reusedProcessBefore = Get-ProcessSnapshot ([int]$OwnedSnapshot.ProcessId) 1
  if ($null -eq $reusedProcessBefore) { throw "identity-test process exited before reboot reuse simulation" }
  $previousBootResolution = Resolve-OwnedProcess `
    "identity-test" $PidFile $TestPython $Arguments $Fingerprint
  if ($null -ne $previousBootResolution) { throw "previous-boot PID receipt was accepted" }
  if (Test-Path -LiteralPath $PidFile) { throw "previous-boot PID receipt was not removed" }
  $reusedProcessAfter = Get-ProcessSnapshot ([int]$OwnedSnapshot.ProcessId) 1
  if ($null -eq $reusedProcessAfter -or
      -not (Test-ProcessSnapshotIdentity $reusedProcessAfter $reusedProcessBefore)) {
    throw "previous-boot cleanup terminated or changed the reused live process"
  }

  [IO.File]::WriteAllText($PidFile, $originalRecord, $Utf8NoBom)
  $DescendantSnapshots = @(Get-VerifiedProcessDescendants $OwnedSnapshot)
  Stop-OwnedProcess "identity-test" $PidFile $TestPython
  foreach ($snapshot in $DescendantSnapshots) {
    $current = Get-ProcessSnapshot ([int]$snapshot.ProcessId) 1
    if ($current -and (Test-ProcessSnapshotIdentity $current $snapshot)) {
      throw "verified venv descendant survived Stop-OwnedProcess: $($snapshot.ProcessId)"
    }
  }
  Write-Output ("PASS: launcher={0}; actual={1}; descendants={2}" -f $record.launcherPath, $record.executablePath, $DescendantSnapshots.Count)
} finally {
  Remove-Item Env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY -ErrorAction SilentlyContinue
  if ($OwnedSnapshot) {
    $currentRoot = Get-ProcessSnapshot ([int]$OwnedSnapshot.ProcessId) 1
    if ($currentRoot -and (Test-ProcessSnapshotIdentity $currentRoot $OwnedSnapshot)) {
      Stop-VerifiedProcessTree $currentRoot | Out-Null
    }
  }
  foreach ($snapshot in $DescendantSnapshots) {
    $current = Get-ProcessSnapshot ([int]$snapshot.ProcessId) 1
    if ($current -and (Test-ProcessSnapshotIdentity $current $snapshot)) {
      Stop-Process -Id $snapshot.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($file in @($PidFile, $Stdout, $Stderr, (Join-Path $LogRoot "launcher.jsonl"))) {
    if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
  }
  foreach ($directory in @($LogRoot, $RunRoot, $TestRoot)) {
    if ([IO.Directory]::Exists($directory)) { [IO.Directory]::Delete($directory, $false) }
  }
}
