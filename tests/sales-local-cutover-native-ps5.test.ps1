[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("rehearsal", "operator")]
  [string]$Mode,
  [Parameter(Mandatory = $true)]
  [string]$ToolScript,
  [Parameter(Mandatory = $true)]
  [string]$Python
)

$ErrorActionPreference = "Stop"

$tokens = $null
$parseErrors = $null
$source = [IO.File]::ReadAllText($ToolScript, [Text.Encoding]::UTF8)
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
  $source, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw "$Mode script parse failed" }

$functionNames = @(
  "Get-BoundedNativeDiagnostic",
  "Invoke-BoundedNativeProcess",
  "Get-NativeFailureSummary"
)
if ($Mode -ceq "rehearsal") {
  $functionNames += @(
    "ConvertTo-AsciiPythonLauncher",
    "Invoke-JsonProcess",
    "Invoke-PythonJsonCode"
  )
} else {
  $functionNames += @(
    "ConvertFrom-NativeJsonRun",
    "Invoke-NodeJson",
    "Invoke-PythonJson"
  )
}
$definitions = @($ast.FindAll({
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $functionNames -ccontains $node.Name
}, $true))
if ($definitions.Count -ne $functionNames.Count) {
  throw "$Mode native functions are missing or ambiguous"
}
foreach ($name in $functionNames) {
  $definition = @($definitions | Where-Object { $_.Name -ceq $name })
  if ($definition.Count -ne 1) { throw "$Mode native function identity mismatch" }
  Invoke-Expression $definition[0].Extent.Text
}

function Protect-LogText([string]$Value) {
  if ($null -eq $Value) { return "" }
  return [regex]::Replace(
    $Value, "postgres(?:ql)?://[^@\s]+@", "postgresql://[redacted]@"
  )
}

function Get-Sha256Text([string]$Value) {
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString(
      $algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
    )).Replace("-", "").ToLowerInvariant()
  } finally {
    $algorithm.Dispose()
  }
}

function ConvertTo-FixtureLauncher([string]$Code, [string]$SourceName) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code))
  return (
    "import base64;exec(compile(base64.b64decode('$encoded')," +
    "'$SourceName','exec'))"
  )
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
  "teruisi-$Mode-native-$([Guid]::NewGuid().ToString('N'))"
)
[IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
$InstalledAppRoot = $temporaryRoot
$Node = $Python

try {
  $diagnosticA = Get-BoundedNativeDiagnostic @(
    "postgresql://owner:alpha-secret@127.0.0.1:5432/private",
    "raw-secret=alpha-secret"
  )
  $diagnosticB = Get-BoundedNativeDiagnostic @(
    "postgresql://owner:beta-secret@127.0.0.1:5432/private",
    "raw-secret=beta-secret"
  )
  if ([string]$diagnosticA.OutputSha256 -cne [string]$diagnosticB.OutputSha256) {
    throw "$Mode diagnostics were hashed before credential redaction"
  }
  $bounded = Get-BoundedNativeDiagnostic @(
    1..64 | ForEach-Object { "warning-$($_)-password=hidden" }
  )
  if (-not [bool]$bounded.OutputTruncated -or
      [int]$bounded.CapturedRecordCount -gt 32 -or
      [string]$bounded.OutputSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "$Mode diagnostic bounds failed"
  }

  $successCode = @'
import json
import sys

sys.stderr.write("benign native warning\n")
print(json.dumps({"status":"completed","quote":"\"ok\""}, separators=(",",":")))
'@
  $successLauncher = if ($Mode -ceq "rehearsal") {
    ConvertTo-AsciiPythonLauncher $successCode "ps5_rehearsal_success.py"
  } else {
    ConvertTo-FixtureLauncher $successCode "ps5_operator_success.py"
  }
  if ($successLauncher -cmatch "[^\x00-\x7f]" -or
      $successLauncher.Contains("`r") -or $successLauncher.Contains("`n")) {
    throw "$Mode success launcher is not one ASCII line"
  }

  $global:LASTEXITCODE = 73
  $successRun = Invoke-BoundedNativeProcess $Python @(
    "-c", $successLauncher
  ) $temporaryRoot
  if ($successRun.ExitCode -ne 0 -or [bool]$successRun.LaunchFailed -or
      $ErrorActionPreference -cne "Stop" -or $global:LASTEXITCODE -ne 73) {
    throw "$Mode success invocation lost native process state"
  }
  $successPayload = if ($Mode -ceq "rehearsal") {
    Invoke-PythonJsonCode $successCode "ps5_rehearsal_json.py" $temporaryRoot (
      "PS5 rehearsal JSON fixture"
    )
  } else {
    $nodePayload = Invoke-NodeJson @("-c", $successLauncher) (
      "PS5 operator Node JSON fixture"
    )
    if ([string]$nodePayload.status -cne "completed") {
      throw "operator Node JSON wrapper did not tolerate benign stderr"
    }
    Invoke-PythonJson @("-c", $successLauncher) (
      "PS5 operator Python JSON fixture"
    )
  }
  if ([string]$successPayload.status -cne "completed" -or
      [string]$successPayload.quote -cne '"ok"' -or
      $ErrorActionPreference -cne "Stop" -or $global:LASTEXITCODE -ne 73) {
    throw "$Mode JSON wrapper did not preserve process state"
  }

  $missingExecutable = Join-Path $temporaryRoot "missing-native.exe"
  $missingRun = Invoke-BoundedNativeProcess $missingExecutable @() $temporaryRoot
  $missingSummary = Get-NativeFailureSummary $missingRun
  if ($missingRun.ExitCode -ne -1 -or -not [bool]$missingRun.LaunchFailed -or
      $ErrorActionPreference -cne "Stop" -or $global:LASTEXITCODE -ne 73 -or
      $missingSummary.Contains($missingExecutable) -or
      $missingSummary.Length -gt 512) {
    throw "$Mode missing executable gate reused stale state or leaked a path"
  }

  $secret = [Environment]::GetEnvironmentVariable(
    "TERUISI_CUTOVER_NATIVE_PS5_FIXTURE_SECRET", "Process"
  )
  if ([string]::IsNullOrWhiteSpace($secret)) { throw "fixture secret is missing" }
  $failureCode = @'
import os
import sys

secret = os.environ["TERUISI_CUTOVER_NATIVE_PS5_FIXTURE_SECRET"]
sys.stderr.write("postgresql://owner:" + secret + "@127.0.0.1:5432/private\n")
sys.stderr.write("password=" + secret + "\n")
sys.exit(23)
'@
  $failureLauncher = ConvertTo-FixtureLauncher $failureCode "ps5_failure.py"
  $failureRun = Invoke-BoundedNativeProcess $Python @(
    "-c", $failureLauncher
  ) $temporaryRoot
  $failureOutput = (@($failureRun.Output) | ForEach-Object { [string]$_ }) -join "`n"
  $failureSummary = Get-NativeFailureSummary $failureRun
  if ($failureRun.ExitCode -ne 23 -or [bool]$failureRun.LaunchFailed -or
      -not $failureOutput.Contains($secret) -or
      $failureSummary.Contains($secret) -or
      $failureSummary.Contains("postgresql://owner:") -or
      $failureSummary.Length -gt 512 -or
      $ErrorActionPreference -cne "Stop" -or $global:LASTEXITCODE -ne 73) {
    throw "$Mode nonzero native failure was not exact, bounded, and secret-safe"
  }

  [ordered]@{
    status = "completed"
    mode = $Mode
    powershellEdition = [string]$PSVersionTable.PSEdition
    powershellVersion = [string]$PSVersionTable.PSVersion
    successExitCode = [int]$successRun.ExitCode
    failureExitCode = [int]$failureRun.ExitCode
    missingExecutableExitCode = [int]$missingRun.ExitCode
    warningRecordCount = [int]$successRun.Diagnostic.OutputRecordCount
    diagnosticTruncated = [bool]$bounded.OutputTruncated
    processStateRestored = (
      $ErrorActionPreference -ceq "Stop" -and $global:LASTEXITCODE -eq 73
    )
  } | ConvertTo-Json -Compress
} finally {
  if ([IO.Directory]::Exists($temporaryRoot)) {
    [IO.Directory]::Delete($temporaryRoot, $true)
  }
}
