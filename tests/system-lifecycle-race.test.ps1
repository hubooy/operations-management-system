param([string]$ChildShell = "powershell.exe")
$ErrorActionPreference = "Stop"
$root = Join-Path ([IO.Path]::GetTempPath()) ("teruisi-race-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($root) | Out-Null
$service = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\tools\django-local-service.ps1"))
$worker = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\tools\worker-local-service.ps1"))
$previous = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $service -RuntimeRoot $root
  function Assert-DeployedApplication {}
  function Assert-RuntimeAclHardened {}
  function Assert-ServiceStackStopped {}
  function Assert-SalesRetirementWorkerStopped {}
  function Write-LauncherEvent {}
  $MaintenanceId = "a" * 32
  Begin-SystemMaintenance
  Begin-SystemMaintenance
  Remove-Variable -Name Action -Scope Local -Force
  foreach ($Action in @("Start", "StartFinance", "StartDingTalk", "AutoStartDingTalk")) {
    $blocked = $false
    try { Invoke-WithServiceMutex { throw "must not reach start" } } catch { $blocked = $_.Exception.Message -match "maintenance is active" }
    if (-not $blocked) { throw "Maintenance failed to block $Action" }
  }
  & {
    function Assert-ServiceStackStopped { throw "synthetic live backend" }
    try { End-SystemMaintenance; throw "active backend accepted" } catch { if ($_.Exception.Message -ne "synthetic live backend") { throw } }
    if (-not (Read-SystemMaintenance)) { throw "Failed end removed the maintenance gate" }
  }
  $MaintenanceId = "b" * 32
  try { End-SystemMaintenance; throw "wrong owner accepted" } catch { if ($_.Exception.Message -notmatch "ownership changed") { throw } }
  if (-not (Read-SystemMaintenance)) { throw "Wrong owner removed maintenance" }
  $MaintenanceId = "a" * 32
  End-SystemMaintenance
  if (Read-SystemMaintenance) { throw "Exact owner did not end maintenance" }
  & {
    # Exercise the production admission decision with synthetic reads only.
    $RuntimeRoot = "D:\teruisi-runtime\django-sales"
    function Read-SystemMaintenance { return @{ id = "a" * 32 } }
    function Assert-SalesRetirementWorkerStopped {}
    function Test-Path { return $true }
    try { Assert-ProductionMaintenance "DeployApp"; throw "live supervisor accepted" } catch { if ($_.Exception.Message -notmatch "supervisor receipt") { throw } }
    function Test-Path { return $false }
    Assert-ProductionMaintenance "DeployApp"
    function Read-SystemMaintenance { return $null }
    try { Assert-ProductionMaintenance "RollbackApp"; throw "missing maintenance accepted" } catch { if ($_.Exception.Message -notmatch "requires EnterMaintenance") { throw } }
  }

  # Load the real Start/Stop switch bodies, replacing only side effects with
  # phase probes. A separate process must fail to acquire the service mutex
  # during *every* phase, including nested domain callbacks.
  $parseErrors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile($service, [ref]$null, [ref]$parseErrors)
  if ($parseErrors.Count) { throw "Django controller parse error" }
  $switch = $ast.FindAll({ param($n) $n -is [Management.Automation.Language.SwitchStatementAst] }, $true) |
    Where-Object { $_.Extent.Text -match '"DeployApp" \{ Invoke-WithServiceMutex' } | Select-Object -First 1
  $script:phases = @()
  function Probe-Phase([string]$phase) {
    $name = "Local\TERUISI-DjangoSales-" + (Get-Sha256Text (Get-CanonicalPath $RuntimeRoot)).Substring(0, 24)
    $code = '$m=[Threading.Mutex]::new($false,''' + $name + ''');$got=$m.WaitOne(0);if($got){$m.ReleaseMutex()};$m.Dispose();Write-Output $got'
    $output = & $ChildShell -NoProfile -NonInteractive -Command $code
    if ($LASTEXITCODE -ne 0 -or [string]$output -ne "False") { throw "Django lifecycle mutex was released in $phase" }
    $script:phases += $phase
  }
  function Start-ServiceStack { Probe-Phase "core-start" }
  function Write-ServiceDesiredState { Probe-Phase "desired-state" }
  function New-OrchestratedLifecycleAclToken { return ("a" * 64) }
  function Set-OrchestratedLifecycleAclContext {}
  function Invoke-EnabledDjangoDomainStarts { Invoke-WithServiceMutex { Probe-Phase "domain-start" } }
  function Invoke-InstalledDjangoDomainStops { Invoke-WithServiceMutex { Probe-Phase "domain-stop" } }
  function Stop-ServiceStack { Probe-Phase "core-stop" }
  foreach ($operation in @("Start", "Stop")) {
    $Action = $operation
    $clause = $switch.Clauses | Where-Object { $_.Item1.Value -eq $operation } | Select-Object -First 1
    $body = $clause.Item2.Extent.Text
    & ([scriptblock]::Create($body.Substring(1, $body.Length - 2)))
  }
  if (($script:phases -join ',') -ne "core-start,desired-state,domain-start,desired-state,domain-stop,core-stop") { throw "Lifecycle phase coverage is incomplete" }

  # Independent Worker mutex namespace keeps tests away from production.
  . $worker -FunctionsOnly -AllowTestRuntimeRoot -RuntimeRoot $root
  $FixedDjangoRuntimeRoot = $root
  $workerAst = [Management.Automation.Language.Parser]::ParseFile($worker, [ref]$null, [ref]$parseErrors)
  $libraryScope = $workerAst.FindAll({ param($n)
    $n -is [Management.Automation.Language.ScriptBlockExpressionAst] -and
    $n.ScriptBlock.ParamBlock -and $n.ScriptBlock.ParamBlock.Extent.Text -match '\$maintenanceController'
  }, $true) | Select-Object -First 1
  $fixtureController = Join-Path $root "maintenance-library.ps1"
  [IO.File]::WriteAllText($fixtureController, @'
param([ValidateSet("Status")][string]$Action="Status", [string]$RuntimeRoot, [string]$MaintenanceId)
function Invoke-WithServiceMutex([scriptblock]$Operation) { & $Operation }
function Begin-SystemMaintenance { [IO.File]::WriteAllText((Join-Path $RuntimeRoot "library-result.txt"), $MaintenanceId) }
function End-SystemMaintenance { throw "synthetic library failure" }
'@)
  $libraryBlock = $libraryScope.ScriptBlock.GetScriptBlock()
  $priorLibraryFlag = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
  & $libraryBlock $fixtureController $root "BeginMaintenance" ("c" * 32)
  if ([IO.File]::ReadAllText((Join-Path $root "library-result.txt")) -ne ("c" * 32) -or $RuntimeRoot -ne $root) { throw "Maintenance library lost operation/root binding" }
  try { & $libraryBlock $fixtureController $root "EndMaintenance" ("c" * 32); throw "library error ignored" } catch { if ($_.Exception.Message -ne "synthetic library failure") { throw } }
  if ($env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY -ne $priorLibraryFlag) { throw "Maintenance library leaked its environment on failure" }
  $mutex = [Threading.Mutex]::new($false, (Get-WorkerServiceMutexName))
  if (-not $mutex.WaitOne(0)) { throw "Isolated Worker lock unexpectedly busy" }
  try {
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Get-Command $ChildShell).Source
    $startInfo.Arguments = '-NoProfile -NonInteractive -File "' + $worker + '" -Action Stop -AllowTestRuntimeRoot -RuntimeRoot "' + $root + '"'
    $startInfo.UseShellExecute = $false; $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardError = $true; $startInfo.RedirectStandardOutput = $true
    $child = [Diagnostics.Process]::Start($startInfo)
    if (-not $child.WaitForExit(15000)) { $child.Kill(); throw "A concurrent Stop queued instead of rejecting" }
    $diagnostic = $child.StandardError.ReadToEnd()
    if ($child.ExitCode -eq 0 -or $diagnostic -notmatch "lifecycle operation is in progress") { throw "Concurrent stop was not rejected at the Worker gate: $diagnostic" }
    $child.Dispose()
  } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }

  $script:order = @()
  function Stop-WorkerOnly { $script:order += "worker-stop"; return [ordered]@{ status = "already_stopped" } }
  function Invoke-DjangoLifecycleAction { $script:order += "backend-stop" }
  function Invoke-WorkerSystemStart { $script:order += "start"; return [ordered]@{ status = "started" } }
  $result = Invoke-WorkerFullRestart @{}
  if (($script:order -join ',') -ne "worker-stop,backend-stop,start" -or -not $result.backendRestarted) { throw "Full restart order invalid" }
  $script:order = @()
  function Invoke-DjangoLifecycleAction { throw "synthetic stop failure" }
  try { Invoke-WorkerFullRestart @{}; throw "failure ignored" } catch { if ($_.Exception.Message -ne "synthetic stop failure") { throw } }
  if ($script:order -contains "start") { throw "Restart continued after backend stop failure" }
  [IO.File]::WriteAllText((Join-Path $root "run\system-maintenance.json"), "{corrupt")
  try { Assert-WorkerMaintenanceInactive; throw "corrupt state allowed" } catch { if ($_.Exception.Message -notmatch "Unreadable") { throw } }
  Write-Output "PASS: maintenance ownership/corruption, continuous Django mutex, competing Worker stop, full restart ordering and stop failure"
} finally {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = $previous
  $resolved = [IO.Path]::GetFullPath($root)
  if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase) -or
      [IO.Path]::GetFileName($resolved) -notlike "teruisi-race-*") { throw "Unsafe temporary test path" }
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
