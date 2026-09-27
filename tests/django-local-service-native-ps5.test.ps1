$ErrorActionPreference = "Stop"

$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceScript = Join-Path $WorkspaceRoot "tools\django-local-service.ps1"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) (
  "tds-native-ps5-" + [Guid]::NewGuid().ToString("N")
)
$probeScript = Join-Path $testRoot "native-probe.ps1"
$previousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY",
  "Process"
)
$Utf8NoBom = [Text.UTF8Encoding]::new($false)

try {
  New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
  [IO.File]::WriteAllText(
    $probeScript,
    @'
param([int]$ExitCode)
[Console]::Out.WriteLine('{"status":"completed"}')
[Console]::Out.Flush()
Start-Sleep -Milliseconds 50
[Console]::Error.WriteLine('warning password=do-not-print postgresql://user:do-not-print@127.0.0.1/db')
[Console]::Error.Flush()
exit $ExitCode
'@,
    $Utf8NoBom
  )

  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $testRoot

  $nativePowerShell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
  $probeArguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $probeScript
  )

  $global:LASTEXITCODE = 73
  $success = Invoke-BoundedNativeProcess $nativePowerShell (
    $probeArguments + @("0")
  ) $testRoot
  if ($success.ExitCode -ne 0 -or $success.LaunchFailed -or
      $success.Output.Count -lt 2 -or $ErrorActionPreference -cne "Stop" -or
      $global:LASTEXITCODE -ne 73) {
    throw "native success capture did not preserve the PS5 preference/exit contract"
  }
  $successPayload = ConvertFrom-UniqueNativeJson $success "PS5 native success probe"
  if ([string]$successPayload.status -cne "completed") {
    throw "unique JSON parser did not tolerate stderr emitted after stdout JSON"
  }
  $successSummary = Get-NativeFailureSummary $success
  if ($successSummary -notmatch "^exitCode=0; " -or
      $successSummary -notmatch "outputSha256=[0-9a-f]{64}$" -or
      $successSummary -match "do-not-print|postgresql://|password=") {
    throw "native success summary exposed raw stderr or omitted bounded evidence"
  }

  $global:LASTEXITCODE = 73
  $failure = Invoke-BoundedNativeProcess $nativePowerShell (
    $probeArguments + @("19")
  ) $testRoot
  if ($failure.ExitCode -ne 19 -or $failure.LaunchFailed -or
      $failure.Output.Count -lt 2 -or $ErrorActionPreference -cne "Stop" -or
      $global:LASTEXITCODE -ne 73) {
    throw "native failure capture was bypassed by PS5 NativeCommandError"
  }
  $failureSummary = Get-NativeFailureSummary $failure
  if ($failureSummary -notmatch "^exitCode=19; " -or
      $failureSummary -notmatch "outputSha256=[0-9a-f]{64}$" -or
      $failureSummary -match "do-not-print|postgresql://|password=") {
    throw "native failure summary exposed raw stderr or omitted bounded evidence"
  }

  $duplicateOutput = @('{"status":"first"}', 'warning after JSON', '{"status":"second"}')
  $duplicateRun = [pscustomobject][ordered]@{
    ExitCode = 0
    LaunchFailed = $false
    Output = $duplicateOutput
    Diagnostic = Get-BoundedNativeDiagnostic $duplicateOutput
  }
  $duplicateRejected = $false
  try {
    ConvertFrom-UniqueNativeJson $duplicateRun "duplicate JSON probe" | Out-Null
  } catch {
    $duplicateMessage = [string]$_.Exception.Message
    $duplicateHasIdentity = $duplicateMessage.StartsWith(
      "duplicate JSON probe ",
      [StringComparison]::Ordinal
    ) -and $duplicateMessage -match "outputRecordCount=3"
    $duplicateLeaksOutput = $duplicateMessage -match "first|second|warning after JSON"
    $duplicateRejected = (
      $duplicateHasIdentity -and -not $duplicateLeaksOutput
    )
  }
  if (-not $duplicateRejected) {
    throw "unique JSON parser accepted ambiguity or exposed native output"
  }

  $global:LASTEXITCODE = 73
  $missing = Invoke-BoundedNativeProcess (
    (Join-Path $testRoot "missing-native.exe")
  ) @() $testRoot
  if ($missing.ExitCode -ne -1 -or -not $missing.LaunchFailed -or
      $ErrorActionPreference -cne "Stop" -or $global:LASTEXITCODE -ne 73) {
    throw "missing native executable did not fail closed"
  }

  $multiline = "print('first')`nprint('中文')"
  $launcher = ConvertTo-PythonBase64Launcher $multiline "ps5_probe.py"
  if ($launcher -cmatch "[^\x00-\x7f]" -or $launcher.Contains("`r") -or
      $launcher.Contains("`n") -or $launcher -notmatch "base64\.b64decode") {
    throw "multiline Python was not converted into one ASCII argument"
  }

  [pscustomobject][ordered]@{
    status = "completed"
    successExitCode = [int]$success.ExitCode
    failureExitCode = [int]$failure.ExitCode
    missingExitCode = [int]$missing.ExitCode
    errorActionPreferenceRestored = $ErrorActionPreference -ceq "Stop"
    lastExitCodeRestored = $global:LASTEXITCODE -eq 73
    launcherAscii = $launcher -cnotmatch "[^\x00-\x7f]"
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
