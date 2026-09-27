param(
  [Parameter(Mandatory=$true)][string]$Repository,
  [Parameter(Mandatory=$true)][string]$Scratch,
  [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ApprovedPlanSha256,
  [switch]$Apply,
  [ValidateRange(1,137)][int]$MaximumCandidates = 1
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RuntimeRoot = 'D:\teruisi-runtime\teruisi-worker-sales'
$ReleasesRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot 'releases'))
$Tool = Join-Path $PSScriptRoot 'storage-dependency-cleanup.mjs'
$Scratch = [IO.Path]::GetFullPath($Scratch)
$RunId = [guid]::NewGuid().ToString('N')
$Preflight = Join-Path $Scratch ('cleanup-preflight-' + $RunId + '.json')
$Audit = Join-Path $Scratch ('cleanup-audit-' + $RunId + '.jsonl')
$Mutex = [Threading.Mutex]::new($false, 'Local\TERUISI.Worker.LocalService.v1')
$Held = $false

function Invoke-NodeJson([string[]]$Arguments) {
  $Json = & node $Tool @Arguments
  if ($LASTEXITCODE -ne 0) { throw 'Dependency cleanup verification failed' }
  return ($Json -join "`n" | ConvertFrom-Json)
}
function Write-DurableAudit($Value) {
  $Bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 30 -Compress) + "`n")
  $Stream = [IO.FileStream]::new($Audit, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
  try { $Stream.Write($Bytes, 0, $Bytes.Length); $Stream.Flush($true) } finally { $Stream.Dispose() }
}
function Assert-ExactTarget([string]$Target, [string]$ReleaseId) {
  if ($ReleaseId -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{16}$') { throw 'Invalid release ID' }
  $Expected = [IO.Path]::GetFullPath((Join-Path (Join-Path $ReleasesRoot $ReleaseId) 'node_modules'))
  if (-not [string]::Equals([IO.Path]::GetFullPath($Target), $Expected, [StringComparison]::OrdinalIgnoreCase)) { throw 'Target escaped exact scope' }
  $Cursor = Get-Item -LiteralPath $Expected -Force
  while ($null -ne $Cursor) {
    if (($Cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point rejected' }
    $Cursor = $Cursor.Parent
  }
  if (-not (Test-Path -LiteralPath $Expected -PathType Container)) { throw 'Dependency directory missing' }
  return $Expected
}
function Assert-NoProcessReference([string]$ReleaseRoot) {
  # Only the count leaves this function; process arguments may contain secrets.
  $Processes = @(Get-CimInstance Win32_Process -OperationTimeoutSec 30 -ErrorAction Stop)
  if (@($Processes | Where-Object { $_.Name -in @('node.exe', 'workerd.exe') -and (-not $_.CommandLine -or -not $_.ExecutablePath) }).Count -ne 0) {
    throw 'Cannot verify all Worker process identities'
  }
  $References = @($Processes | Where-Object {
    ($_.CommandLine -and $_.CommandLine.Replace('/', '\').IndexOf($ReleaseRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
    ($_.ExecutablePath -and $_.ExecutablePath.Replace('/', '\').IndexOf($ReleaseRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
  })
  if ($References.Count -ne 0) { throw 'Candidate release is referenced by a running process' }
}

try {
  # Nonblocking acquisition: do not queue a cleanup behind an active release.
  try { $Held = $Mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $Held = $true }
  if (-not $Held) { throw 'Worker lifecycle operation is active; cleanup has not started' }
  $Verified = Invoke-NodeJson -Arguments @('preflight', $Repository, $Scratch, $ApprovedPlanSha256, $Preflight)
  $Snapshot = Get-Content -LiteralPath $Preflight -Raw | ConvertFrom-Json
  $PreflightSha = (Get-FileHash -LiteralPath $Preflight -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($PreflightSha -ne $Verified.preflightSha256 -or $Snapshot.plan.planSha256 -ne $ApprovedPlanSha256) { throw 'Preflight changed' }
  Write-DurableAudit @{event='preflight'; at=[datetime]::UtcNow.ToString('o'); preflightSha256=$PreflightSha; apply=[bool]$Apply; maximumCandidates=$MaximumCandidates}
  $Count = 0
  foreach ($Candidate in $Snapshot.plan.candidates) {
    if ($Count -ge $MaximumCandidates) { break }
    if ($Snapshot.plan.retainedReleaseIds -contains $Candidate.releaseId) { throw 'Retained release entered candidate list' }
    $Expected = Join-Path (Join-Path $ReleasesRoot $Candidate.releaseId) 'node_modules'
    if (-not (Test-Path -LiteralPath $Expected)) { continue }
    $Checked = Invoke-NodeJson -Arguments @('candidate', $Preflight, $PreflightSha, $ApprovedPlanSha256, $Candidate.releaseId)
    $Target = Assert-ExactTarget $Checked.target $Candidate.releaseId
    Assert-NoProcessReference ([IO.Path]::GetDirectoryName($Target))
    Write-DurableAudit @{event='verified'; at=[datetime]::UtcNow.ToString('o'); candidate=$Checked}
    if ($Apply) {
      Write-DurableAudit @{event='delete-reserved'; at=[datetime]::UtcNow.ToString('o'); target=$Target; preflightSha256=$PreflightSha}
      # Native PowerShell, one shell end to end; no wildcard, constructed shell,
      # recursive worktree deletion, or removal of any surrounding release file.
      Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction Stop
      if (Test-Path -LiteralPath $Target) { throw 'Dependency removal incomplete' }
      Write-DurableAudit @{event='deleted'; at=[datetime]::UtcNow.ToString('o'); target=$Target}
    }
    $Count++
    Write-Host ('Verified old dependency payload ' + $Count + ': ' + $Candidate.releaseId)
  }
  $After = Invoke-NodeJson -Arguments @('postflight', $ApprovedPlanSha256)
  Write-DurableAudit @{event='completed'; at=[datetime]::UtcNow.ToString('o'); processed=$Count; apply=[bool]$Apply; postflight=$After}
  @{processed=$Count; applied=[bool]$Apply; audit=$Audit; postflight=$After} | ConvertTo-Json -Depth 10 -Compress
} catch {
  if (Test-Path -LiteralPath $Audit) { Write-DurableAudit @{event='failed-stop'; at=[datetime]::UtcNow.ToString('o'); error='Cleanup stopped; inspect local verification failure before any retry.'} }
  throw
} finally {
  if ($Held) { $Mutex.ReleaseMutex() }
  $Mutex.Dispose()
}
