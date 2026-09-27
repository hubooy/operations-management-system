param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop).Path
$n8nEntry = Join-Path $env:APPDATA "npm\node_modules\n8n\bin\n8n"
$nodeCommand = (Get-Command node.exe -ErrorAction Stop).Source
$logDirectory = Join-Path $projectRoot ".runtime\n8n"
$stdoutLog = Join-Path $logDirectory "service.stdout.utf8.log"
$stderrLog = Join-Path $logDirectory "service.stderr.utf8.log"

if (-not (Test-Path -LiteralPath $n8nEntry -PathType Leaf)) {
  throw "The local n8n entry point is unavailable."
}

$commandModule = Join-Path $env:APPDATA 'npm\node_modules\n8n\node_modules\n8n-nodes-base\dist\nodes\ExecuteCommand\ExecuteCommand.node.js'
& $nodeCommand (Join-Path $PSScriptRoot 'n8n-command-no-console.mjs') --verify $commandModule | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'n8n command no-console patch verification failed; review the installed n8n version.' }

$existingListener = @(Get-NetTCPConnection -State Listen -LocalPort 5678 -ErrorAction SilentlyContinue)
if ($existingListener.Count -gt 0) {
  $nonLoopbackListener = @($existingListener | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") })
  if ($nonLoopbackListener.Count -gt 0) {
    throw "Port 5678 is listening on a non-loopback address; the retry endpoints must not be externally reachable."
  }
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5678/healthz" -TimeoutSec 5
    if ([int]$response.StatusCode -eq 200) {
      exit 0
    }
  } catch {
    throw "Port 5678 is occupied, but the n8n health endpoint is unavailable."
  }
  throw "Port 5678 is occupied by an unhealthy service."
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $projectRoot

$env:N8N_LISTEN_ADDRESS = "127.0.0.1"
$env:NODES_EXCLUDE = '["n8n-nodes-base.localFileTrigger"]'
. (Join-Path $PSScriptRoot 'n8n-native-process.ps1')
$exitCode = Invoke-N8nNativeProcess -NodePath $nodeCommand -EntryPath $n8nEntry -StdoutPath $stdoutLog -StderrPath $stderrLog
exit $exitCode
