param(
  [string]$ChildShell = "powershell.exe",
  [string]$Mode = "suite",
  [string]$TestRoot,
  [string]$ResultPath,
  [int]$WaitSeconds = 900,
  [string]$ExpectedFence = ""
)
$ErrorActionPreference = "Stop"
$service = Join-Path $PSScriptRoot "..\tools\django-local-service.ps1"
$worker = Join-Path $PSScriptRoot "..\tools\worker-local-service.ps1"

if ($Mode -ne "suite") {
  try {
    if ($Mode -eq "django") {
      $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
      . $service -RuntimeRoot $TestRoot
      $Action = "Start"
      [IO.File]::WriteAllText("$ResultPath.entered", "ready")
      Invoke-WithServiceMutex {
        if ($ExpectedFence) { Assert-SupervisorStartFence $ExpectedFence }
        [IO.File]::WriteAllText("$ResultPath.effect", "started")
      } -StartWaitSeconds $WaitSeconds
    } else {
      . $worker -FunctionsOnly -AllowTestRuntimeRoot -RuntimeRoot $TestRoot
      $Action = "Start"
      [IO.File]::WriteAllText("$ResultPath.entered", "ready")
      $lease = Enter-WorkerServiceMutex -StartWaitSeconds $WaitSeconds
      try {
        if (-not $lease.Joined) { throw "The fixture did not exercise contention" }
      } finally { $lease.Mutex.ReleaseMutex(); $lease.Mutex.Dispose() }
    }
    [IO.File]::WriteAllText($ResultPath, '{"ok":true}')
  } catch {
    [IO.File]::WriteAllText($ResultPath, (@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress))
  }
  exit 0
}

$root = Join-Path ([IO.Path]::GetTempPath()) ("teruisi-cold-start-" + [guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($root) | Out-Null
$previousLibrary = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
$children = @()
$held = @()
function Start-Waiter([string]$Kind, [string]$Label, [int]$Timeout = 900, [string]$Fence = "") {
  $result = Join-Path $root "$Label.json"
  $arguments = @("-NoProfile", "-NonInteractive", "-File", ('"' + $PSCommandPath + '"'),
    "-Mode", $Kind, "-TestRoot", ('"' + $root + '"'), "-ResultPath", ('"' + $result + '"'), "-WaitSeconds", "$Timeout")
  if ($Fence) { $arguments += @("-ExpectedFence", $Fence) }
  $p = Start-Process -FilePath (Get-Command $ChildShell).Source -ArgumentList $arguments -WindowStyle Hidden `
    -RedirectStandardOutput "$result.stdout" -RedirectStandardError "$result.stderr" -PassThru
  $script:children += $p
  $deadline = [DateTime]::UtcNow.AddSeconds(12)
  while (-not (Test-Path -LiteralPath "$result.entered")) {
    if ($p.HasExited -or [DateTime]::UtcNow -ge $deadline) { throw "Waiter failed to enter: $(Get-Content -LiteralPath "$result.stderr" -Raw)" }
    Start-Sleep -Milliseconds 30
  }
  return @{Process=$p;Path=$result}
}
function Read-Waiter($Waiter) {
  if (-not $Waiter.Process.WaitForExit(15000)) { throw "Waiter did not finish within fixture bound" }
  if (-not (Test-Path -LiteralPath $Waiter.Path)) { throw "Waiter returned no evidence" }
  return (Get-Content -LiteralPath $Waiter.Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}
function Hold-Lock([string]$Name) {
  $m = [Threading.Mutex]::new($false, $Name)
  if (-not $m.WaitOne(0)) { throw "Isolated lock unexpectedly occupied" }
  $script:held += $m
  return $m
}
function Release-Lock($Mutex) {
  $Mutex.ReleaseMutex(); $Mutex.Dispose()
  $script:held = @($script:held | Where-Object { $_ -ne $Mutex })
}
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $service -RuntimeRoot $root
  Write-ServiceDesiredState "running" "explicit_start"
  $djangoMutexName = "Local\TERUISI-DjangoSales-" + (Get-Sha256Text (Get-CanonicalPath $root)).Substring(0, 24)
  $djangoMutex = Hold-Lock $djangoMutexName
  $djangoWaiter = Start-Waiter "django" "cold-django"
  $workerMutexName = & {
    . $worker -FunctionsOnly -AllowTestRuntimeRoot -RuntimeRoot $root
    Get-WorkerServiceMutexName
  }
  $workerMutex = Hold-Lock $workerMutexName
  $workerWaiter = Start-Waiter "worker" "cold-worker"
  # A real competing OS process holds both independent test mutexes beyond the
  # old Django 30-second limit; no production service, port or data is touched.
  Start-Sleep -Seconds 33
  if ($djangoWaiter.Process.HasExited -or $workerWaiter.Process.HasExited) { throw "Concurrent Start failed before the existing startup finished" }
  Release-Lock $djangoMutex
  Release-Lock $workerMutex
  foreach ($waiter in @($djangoWaiter,$workerWaiter)) {
    $result = Read-Waiter $waiter
    if (-not $result.ok) { throw "Concurrent Start did not resume: $($result.error)" }
  }
  if (-not (Test-Path -LiteralPath "$($djangoWaiter.Path).effect")) { throw "Django continuation did not run" }

  foreach ($kind in @("django", "worker")) {
    $name = if ($kind -eq "django") { $djangoMutexName } else { $workerMutexName }
    $mutex = Hold-Lock $name
    $waiter = Start-Waiter $kind "timeout-$kind" 1
    $result = Read-Waiter $waiter
    Release-Lock $mutex
    if ($result.ok -or (Test-Path -LiteralPath "$($waiter.Path).effect")) { throw "Timeout was treated as success" }
  }

  foreach ($scenario in @("stop", "maintenance", "fence-change", "corrupt-intent")) {
    Write-ServiceDesiredState "running" "explicit_start"
    $fence = Get-FileSha256 $SupervisorDesiredStatePath
    $mutex = Hold-Lock $djangoMutexName
    $waiter = Start-Waiter "django" $scenario 10 $(if ($scenario -eq "fence-change") { $fence } else { "" })
    Start-Sleep -Milliseconds 150
    switch ($scenario) {
      "stop" { Write-ServiceDesiredState "stopped" "explicit_stop" }
      "maintenance" {
        Write-AtomicJson $MaintenancePath (@{
          version="teruisi-system-maintenance-v1";id=("a"*32);runtimeRoot=(Get-CanonicalPath $root)
          createdAt=[DateTimeOffset]::UtcNow.ToString("o");keepPostgres=$false
        })
      }
      "fence-change" { Write-ServiceDesiredState "running" "changed_intent" }
      "corrupt-intent" { [IO.File]::WriteAllText($SupervisorDesiredStatePath, "{invalid") }
    }
    Release-Lock $mutex
    $result = Read-Waiter $waiter
    if ($result.ok -or (Test-Path -LiteralPath "$($waiter.Path).effect")) { throw "Queued Start ignored $scenario" }
    if (Test-Path -LiteralPath $MaintenancePath) { [IO.File]::Delete($MaintenancePath) }
  }

  & {
    . $worker -FunctionsOnly -AllowTestRuntimeRoot -RuntimeRoot $root
    $FixedDjangoRuntimeRoot = $root
    $identity = @{ReleaseId="fixture";Sha256=("b"*64)}
    function Get-WorkerStatusInternal { return @{State=$script:fixtureState} }
    function Get-DjangoSystemReadiness { return @{Ready=$script:fixtureReady;Missing=@("fixture")} }
    function Invoke-WorkerSystemStart { throw "Joining must not restart anything" }
    function Ensure-DjangoSystemReady { throw "Joining must not start the backend" }
    function Start-SystemDingTalkReceiver { throw "Joining must not start channels" }
    function Invoke-WebRequest { return @{StatusCode=$script:fixturePage} }
    function Invoke-RestMethod { return @{ok=$script:fixtureHelper} }
    $script:fixturePage=200; $script:fixtureHelper=$true
    $script:fixtureState="exact_release"; $script:fixtureReady=$true
    if ((Get-JoinedWorkerStartResult $identity).status -cne "already_running") { throw "Ready concurrent result rejected" }
    foreach ($state in @("stopped", "starting_exact_release", "stale_or_invalid_receipt", "unknown")) {
      $script:fixtureState=$state
      $rejected=$false
      try { Get-JoinedWorkerStartResult $identity | Out-Null } catch { $rejected=$true }
      if (-not $rejected) { throw "Joining accepted $state" }
    }
    $script:fixtureState="exact_release"; $script:fixtureReady=$false
    try { Get-JoinedWorkerStartResult $identity | Out-Null; throw "backend accepted" } catch { if ($_.Exception.Message -eq "backend accepted") { throw } }
    $script:fixtureReady=$true
    $script:fixturePage=503
    try { Get-JoinedWorkerStartResult $identity | Out-Null; throw "homepage accepted" } catch { if ($_.Exception.Message -eq "homepage accepted") { throw } }
    $script:fixturePage=200; $script:fixtureHelper=$false
    try { Get-JoinedWorkerStartResult $identity | Out-Null; throw "helper accepted" } catch { if ($_.Exception.Message -eq "helper accepted") { throw } }
    $script:fixtureHelper=$true
    [IO.Directory]::CreateDirectory((Join-Path $root "run")) | Out-Null
    [IO.File]::WriteAllText((Join-Path $root "run\system-maintenance.json"), "{invalid")
    try { Get-JoinedWorkerStartResult $identity | Out-Null; throw "maintenance accepted" } catch { if ($_.Exception.Message -eq "maintenance accepted") { throw } }
  }
  Write-Output "PASS: real 33-second Django/Worker contention, bounded timeout, stop/maintenance/fence rejection, observation-only Worker join"
} finally {
  foreach ($mutex in $held) { $mutex.ReleaseMutex(); $mutex.Dispose() }
  foreach ($child in $children) { if (-not $child.HasExited) { $child.Kill(); $child.WaitForExit() }; $child.Dispose() }
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY=$previousLibrary
  $resolved=[IO.Path]::GetFullPath($root)
  if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase) -or
      [IO.Path]::GetFileName($resolved) -notlike "teruisi-cold-start-*") { throw "Unsafe test cleanup path" }
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
