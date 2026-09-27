[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$BackupScript,
  [Parameter(Mandatory = $true)]
  [string]$Python
)

$ErrorActionPreference = "Stop"

$tokens = $null
$parseErrors = $null
$source = [IO.File]::ReadAllText($BackupScript, [Text.Encoding]::UTF8)
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
  $source, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw "backup script parse failed" }

$functionNames = @(
  "ConvertTo-AsciiPythonLauncher",
  "Get-BoundedNativeDiagnostic",
  "Invoke-BoundedNativeProcess",
  "Get-NativeFailureSummary"
)
$definitions = @($ast.FindAll({
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $functionNames -ccontains $node.Name
}, $true))
if ($definitions.Count -ne $functionNames.Count) {
  throw "backup launcher functions are missing or ambiguous"
}
foreach ($name in $functionNames) {
  $definition = @($definitions | Where-Object { $_.Name -ceq $name })
  if ($definition.Count -ne 1) { throw "backup launcher function identity mismatch" }
  Invoke-Expression $definition[0].Extent.Text
}

function Protect-LogText([string]$Value) {
  if ($null -eq $Value) { return "" }
  return [regex]::Replace(
    $Value, "postgres(?:ql)?://[^@\s]+@", "postgresql://[redacted]@"
  )
}

$diagnosticA = Get-BoundedNativeDiagnostic @(
  "postgresql://owner:alpha-secret@127.0.0.1:5432/private",
  "raw-secret=alpha-secret"
)
$diagnosticB = Get-BoundedNativeDiagnostic @(
  "postgresql://owner:beta-secret@127.0.0.1:5432/private",
  "raw-secret=beta-secret"
)
if ([string]$diagnosticA.OutputSha256 -cne [string]$diagnosticB.OutputSha256) {
  throw "native diagnostics were hashed before credential redaction"
}

$successCode = @'
import json

payload = {"doubleQuote": "\"ok\"", "lineCount": 3}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
'@
$successLauncher = ConvertTo-AsciiPythonLauncher $successCode "ps5_success_fixture.py"
if ($successLauncher -match "[^\x00-\x7f]" -or $successLauncher.Contains("`r") -or
    $successLauncher.Contains("`n")) {
  throw "success launcher is not one ASCII line"
}
$successRun = Invoke-BoundedNativeProcess $Python @("-c", $successLauncher)
if ($ErrorActionPreference -cne "Stop") {
  throw "native launcher did not restore ErrorActionPreference after success"
}
$successOutput = @($successRun.Output)
if ($successRun.ExitCode -ne 0 -or $successOutput.Count -ne 1) {
  throw "multiline Python success fixture failed ($(Get-NativeFailureSummary $successRun))"
}
$successPayload = [string]$successOutput[0] | ConvertFrom-Json
if ([string]$successPayload.doubleQuote -cne '"ok"' -or
    [int]$successPayload.lineCount -ne 3) {
  throw "multiline Python success payload mismatch"
}

$countMismatchCode = @'
print('{"line":1}')
print('{"line":2}')
'@
$countMismatchLauncher = ConvertTo-AsciiPythonLauncher (
  $countMismatchCode
) "ps5_count_mismatch_fixture.py"
$countMismatchRun = Invoke-BoundedNativeProcess $Python @(
  "-c", $countMismatchLauncher
)
$countMismatchOutput = @($countMismatchRun.Output)
$countMismatchRejected = $false
try {
  if ($countMismatchRun.ExitCode -ne 0 -or $countMismatchOutput.Count -ne 1) {
    throw "evidence output shape rejected ($(Get-NativeFailureSummary $countMismatchRun))"
  }
} catch {
  $countMismatchRejected = $true
  $countMismatchFailure = [string]$_.Exception.Message
  if ($countMismatchFailure.Length -gt 512 -or
      $countMismatchFailure -match '\{"line"') {
    throw "count mismatch diagnostic is not bounded and secret-safe"
  }
}
if (-not $countMismatchRejected -or $countMismatchRun.ExitCode -ne 0 -or
    $countMismatchOutput.Count -ne 2) {
  throw "exit-zero multi-record evidence was not rejected"
}

$missingExecutable = Join-Path ([IO.Path]::GetTempPath()) (
  "teruisi-missing-native-$([Guid]::NewGuid().ToString('N')).exe"
)
$global:LASTEXITCODE = 0
$missingRun = Invoke-BoundedNativeProcess $missingExecutable @()
if ($missingRun.ExitCode -ne -1 -or $ErrorActionPreference -cne "Stop" -or
    $global:LASTEXITCODE -ne 0) {
  throw "missing native executable reused a stale exit code or leaked process state"
}
$missingSummary = Get-NativeFailureSummary $missingRun
if ($missingSummary.Length -gt 512 -or $missingSummary.Contains($missingExecutable) -or
    $missingSummary -cnotmatch "^exitCode=-1;") {
  throw "missing native executable diagnostic is not bounded and secret-safe"
}

$secret = [Environment]::GetEnvironmentVariable(
  "TERUISI_BACKUP_PS5_FIXTURE_SECRET", "Process"
)
if ([string]::IsNullOrWhiteSpace($secret)) { throw "fixture secret is missing" }
$failureCode = @'
import os
import sys

secret = os.environ["TERUISI_BACKUP_PS5_FIXTURE_SECRET"]
sys.stderr.write("postgresql://owner:" + secret + "@127.0.0.1:5432/private\n")
sys.stderr.write("raw-secret=" + secret + "\n")
sys.exit(23)
'@
$failureLauncher = ConvertTo-AsciiPythonLauncher $failureCode "ps5_failure_fixture.py"
$failureRun = Invoke-BoundedNativeProcess $Python @("-c", $failureLauncher)
if ($ErrorActionPreference -cne "Stop") {
  throw "native launcher did not restore ErrorActionPreference after failure"
}
if ($failureRun.ExitCode -ne 23) {
  throw "native launcher lost the exact nonzero exit code"
}
$capturedFailure = (@($failureRun.Output) | ForEach-Object { [string]$_ }) -join "`n"
if (-not $capturedFailure.Contains($secret)) {
  throw "failure fixture did not exercise secret-bearing stderr"
}
$failureSummary = Get-NativeFailureSummary $failureRun
if ($failureSummary.Contains($secret) -or $failureSummary.Contains("postgresql://") -or
    $failureSummary.Length -gt 512 -or
    $failureSummary -cnotmatch "^exitCode=23; outputRecordCount=[0-9]+; capturedRecordCount=[0-9]+; outputTruncated=(True|False); outputSha256=[0-9a-f]{64}$") {
  throw "failure summary is not bounded and secret-safe"
}

[ordered]@{
  status = "completed"
  powershellEdition = [string]$PSVersionTable.PSEdition
  powershellVersion = [string]$PSVersionTable.PSVersion
  successExitCode = [int]$successRun.ExitCode
  failureExitCode = [int]$failureRun.ExitCode
  failureOutputRecordCount = [int]$failureRun.Diagnostic.OutputRecordCount
  failureOutputSha256 = [string]$failureRun.Diagnostic.OutputSha256
  countMismatchRejected = [bool]$countMismatchRejected
  missingExecutableExitCode = [int]$missingRun.ExitCode
  redactionBeforeHashVerified = $true
  errorActionPreferenceRestored = $ErrorActionPreference -ceq "Stop"
} | ConvertTo-Json -Compress
