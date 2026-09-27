[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ToolScript
)

$ErrorActionPreference = "Stop"
$env:TERUISI_DJANGO_SUPERVISOR_LIBRARY_ONLY = "1"
. $ToolScript

function Get-Sha256Text([string]$Value) {
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $algorithm.Dispose()
  }
}

function Get-FileSha256([string]$Path) {
  return "a" * 64
}

function Read-JsonFile([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label missing"
  }
  return Get-Content -Raw -LiteralPath $Path -Encoding UTF8 | ConvertFrom-Json
}

function New-FixtureStatus {
  return [pscustomobject][ordered]@{
    PostgreSQL = "running"
    DjangoReader = "running"
    DjangoWriter = "running"
    ReaderReadiness = "ready"
    WriterReadiness = "ready"
    RuntimeAcl = "root_hardened"
    RuntimeAclVerification = "root_only_status"
    Startup = "installed"
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
}

$healthy = Get-SupervisorHealthClassification (New-FixtureStatus)
if ($healthy.health -cne "healthy" -or $healthy.recoverable) { exit 2 }

$postgresStopped = New-FixtureStatus
$postgresStopped.PostgreSQL = "stopped"
$postgresStopped.ReaderReadiness = "not_ready"
$postgresStopped.WriterReadiness = "not_ready"
$classification = Get-SupervisorHealthClassification $postgresStopped
if (-not $classification.recoverable -or
    $classification.code -cne "postgresql_process_stopped") { exit 3 }

$childStopped = New-FixtureStatus
$childStopped.DjangoReader = "stopped"
$childStopped.ReaderReadiness = "not_ready"
$classification = Get-SupervisorHealthClassification $childStopped
if (-not $classification.recoverable -or
    $classification.code -cne "managed_child_process_stopped") { exit 4 }

$foreign = New-FixtureStatus
$foreign.DjangoWriter = "foreign_port_owner"
$foreign.WriterReadiness = "not_ready"
$classification = Get-SupervisorHealthClassification $foreign
if ($classification.recoverable -or
    $classification.code -cne "ownership_or_port_conflict") { exit 5 }

$acl = New-FixtureStatus
$acl.RuntimeAcl = "not_hardened"
$classification = Get-SupervisorHealthClassification $acl
if ($classification.recoverable -or
    $classification.code -cne "runtime_acl_not_hardened") { exit 7 }

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) (
  "teruisi-django-supervisor-" + [Guid]::NewGuid().ToString("N")
)
try {
  New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
  $SupervisorScriptPath = $ToolScript
  $SupervisorDesiredStatePath = Join-Path $fixtureRoot "missing-desired.json"
  $desired = Get-SupervisorDesiredState
  if ($desired.desiredState -cne "stopped" -or
      $desired.reason -cne "missing_fail_closed") { exit 8 }

  $SupervisorStatePath = Join-Path $fixtureRoot "state.json"
  $state = New-SupervisorState
  $state.supervisorScriptSha256 = "a" * 64
  [IO.File]::WriteAllText(
    $SupervisorStatePath,
    ($state | ConvertTo-Json -Depth 8),
    [Text.UTF8Encoding]::new($false)
  )
  $loaded = Read-SupervisorState
  if ($loaded.code -cne "not_probed") { exit 9 }

  $state.restartAttemptsInWindow = -1
  [IO.File]::WriteAllText(
    $SupervisorStatePath,
    ($state | ConvertTo-Json -Depth 8),
    [Text.UTF8Encoding]::new($false)
  )
  try {
    Read-SupervisorState | Out-Null
    exit 10
  } catch {}
} finally {
  if (Test-Path -LiteralPath $fixtureRoot) {
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\", "/")
    if ((Split-Path -Parent $resolved) -ieq $temp -and
        [IO.Path]::GetFileName($resolved) -cmatch "^teruisi-django-supervisor-[0-9a-f]{32}$") {
      Remove-Item -LiteralPath $resolved -Recurse -Force
    }
  }
}

Write-Output '{"status":"completed"}'
