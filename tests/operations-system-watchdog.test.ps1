$ErrorActionPreference='Stop'
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('watchdog-tests-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot|Out-Null
. (Join-Path $PSScriptRoot '..\tools\operations-system-watchdog.ps1') -FunctionsOnly
$WatchdogRoot=$testRoot;$StatePath=Join-Path $testRoot 'state.json'
$script:checks=0
function Assert-Watch($Condition,$Message){if(-not $Condition){throw $Message};$script:checks++}
function Fixture-Snapshot {
  @{at='2026-09-19T15:00:00Z';admission=@{mode='running';fence=('a'*64)};healthy=$false;system='StaleReceipt';backend='NotReady';worker='stale_or_invalid_receipt';supervisor='stopped';supervisorHealth='stopped';probeError=$false;ports=@()}
}
try {
  $s=Fixture-Snapshot;$state=New-WatchState;$now=[DateTimeOffset]::UtcNow
  Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'confirm_failure') 'first failure must not recover'
  $state.failures=2
  Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'recover') 'missing services should recover after confirmation'
  foreach($mode in @('maintenance','stopped')){$s.admission.mode=$mode;Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'suspended') 'explicit stop/maintenance must remain quiet'}
  $s.admission.mode='invalid';Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'malformed gate must not authorize recovery'
  $s.admission.mode='running';$s.worker='foreign_or_ambiguous';Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'unknown port owner must not be taken over'
  $s.worker='starting_exact_release';Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'in-flight startup must not be repeated'
  $s=Fixture-Snapshot;$s.supervisor='ownership_error';Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'supervisor PID reuse must fail closed'
  $s=Fixture-Snapshot;$s.probeError=$true;Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'status errors must not authorize recovery'
  $s=Fixture-Snapshot;$s.worker='stopped';$s.backend='Ready';Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'clean Worker-only Stop must not be undone'
  $s=Fixture-Snapshot;$s.worker='exact_release';$s.backend='Ready';$s.supervisor='running';Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'HTTP degradation must not restart a live process'
  $s=Fixture-Snapshot;$state.attempts=@($now.AddMinutes(-1).ToString('o'));Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'cooldown') 'bounded backoff required'
  $state.attempts=@(-12,-7,-2|ForEach-Object {$now.AddMinutes($_).ToString('o')});Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'budget_exhausted') 'three attempts per 15 minutes maximum'
  $state.attempts=@($now.AddMinutes(-16).ToString('o'));Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'recover') 'expired budget can recover'

  # Exercise the real state persistence and cycle with only external effects replaced.
  function Start-Sleep {}
  function Get-WatchEvidence($Snapshot){@{snapshot=$Snapshot}}
  function Get-WatchBusinessHealth {@{status='unknown';reason='synthetic_no_observation'}}
  function Send-WatchAlert {$script:alerts++}
  function Invoke-WatchRecovery {$script:recoveries++}
  $script:alerts=0;$script:recoveries=0
  $script:fixture=Fixture-Snapshot
  function Get-WatchSnapshot {return $script:fixture}
  Write-WatchJson $StatePath (New-WatchState)
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'confirm_failure' -and $script:recoveries -eq 0 -and $script:alerts -eq 0) 'first sample must have no recovery or notification side effects'
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'recovery_failed' -and $script:recoveries -eq 1 -and $script:alerts -eq 1) 'confirmed failure should attempt once then alert'
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($script:recoveries -eq 1) 'next tick must respect persisted cooldown'
  $script:fixture.admission.mode='maintenance';$r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'suspended' -and $script:recoveries -eq 1) 'maintenance suspends mutations'
  $script:fixture=Fixture-Snapshot;$script:fixture.healthy=$true;$script:sample=0
  function Get-WatchSnapshot {$script:sample++;$v=Fixture-Snapshot;$v.healthy=($script:sample -eq 1);return $v}
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'verification_pending') 'one healthy sample followed by failure must not close incident'
  function Get-WatchSnapshot {$v=Fixture-Snapshot;$v.healthy=$true;return $v}
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'healthy' -and (Read-WatchJson $StatePath).failures -eq 0) 'two healthy samples close incident'

  function Get-WatchSnapshot {$v=Fixture-Snapshot;$v.healthy=$true;$v.worker='exact_release';$v.backend='Ready';$v.supervisor='running';return $v}
  function Get-WatchBusinessHealth {@{status='degraded';reason='market_queries_failing'}}
  Write-WatchJson $StatePath (New-WatchState)
  $r=Invoke-WatchCycle -Recover;$r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'alert_only') 'confirmed query failures must alert without a restart'
  function Get-WatchBusinessHealth {@{status='unknown';reason='no_recent_observation'}}
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'alert_only' -and (Read-WatchJson $StatePath).marketFailureOpen) 'aging out observations must not close a known business incident'
  function Get-WatchBusinessHealth {@{status='healthy';reason='recent_reads_succeeded'}}
  $r=Invoke-WatchCycle -Recover
  Assert-Watch ($r.status -eq 'healthy' -and -not (Read-WatchJson $StatePath).marketFailureOpen) 'fresh successes are required to close the query incident'

  # Re-load the real delivery function to verify at-most-once admission even when remote result is unknown.
  $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '..\tools\operations-system-watchdog.ps1'),[ref]$null,[ref]$null)
  $function=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Send-WatchAlert'},$true)
  Invoke-Expression $function.Extent.Text
  function Get-WatchRecipient {@{profile='fixture';user='fixture';bot='fixture'}}
  $script:sends=0
  function Invoke-WatchDws {
    $script:sends++
    Assert-Watch ((Read-WatchJson $StatePath).notification -eq 'sending') 'delivery reservation must be durable before network send'
    throw 'synthetic ambiguous remote result'
  }
  $state=New-WatchState;$state.incident='fixture';$state.firstFailure=$now.ToString('o')
  Send-WatchAlert $state (Fixture-Snapshot) 'recovery_failed'
  Assert-Watch ($state.notification -eq 'unknown') 'remote ambiguity must remain unknown'
  Send-WatchAlert $state (Fixture-Snapshot) 'recovery_failed'
  Assert-Watch ($script:sends -eq 1) 'unknown result must never resend'
  $saved=Get-Content -LiteralPath (Join-Path $testRoot 'alerts\fixture.json') -Raw
  Assert-Watch ($saved -notmatch 'robotCode|userId|profile|credential') 'alert audit must omit recipient IDs and credentials'

  $script:identityCalls=0
  function Get-WatchRecipient {$script:identityCalls++;throw 'owner_mismatch'}
  $state=New-WatchState;$state.incident='identity-fixture';$state.firstFailure=$now.ToString('o')
  Send-WatchAlert $state (Fixture-Snapshot) 'alert_only'
  Assert-Watch ($state.notification -eq 'identity_failed' -and $state.notificationReason -eq 'identity:owner_mismatch') 'pre-send failure needs a safe diagnosable reason'
  Send-WatchAlert $state (Fixture-Snapshot) 'alert_only'
  Assert-Watch ($script:identityCalls -eq 1) 'pre-send retry must obey cooldown'
  $state.lastIdentityAttempt=$now.AddMinutes(-16).ToString('o')
  Send-WatchAlert $state (Fixture-Snapshot) 'alert_only'
  Assert-Watch ($script:identityCalls -eq 2 -and $script:sends -eq 1) 'identity recheck is allowed without replaying a send'
  $state.identityAttempts=3;$state.lastIdentityAttempt=$now.AddMinutes(-16).ToString('o')
  Send-WatchAlert $state (Fixture-Snapshot) 'alert_only'
  Assert-Watch ($script:identityCalls -eq 2) 'identity retries are bounded per incident'

  $s=Fixture-Snapshot;$s.system='Running';$s.backend='Ready';$s.worker='exact_release';$s.supervisor='running'
  $s.business=@{status='degraded'};$state=New-WatchState;$state.failures=2
  Assert-Watch ((Get-WatchDecision $s $state $now) -eq 'alert_only') 'business failure must not restart healthy processes'

  # Exercise recovery admission under a real OS mutex. Only the underlying service actions are fixtures.
  $restore=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-WatchRecovery'},$true)
  Invoke-Expression $restore.Extent.Text
  $WorkerPath=Join-Path $testRoot 'worker-fixture.ps1';$DesiredPath=Join-Path $testRoot 'desired.json'
  Write-WatchJson $DesiredPath @{desiredState='running'};$fence=Get-WatchHash $DesiredPath
  $script:recoveryStages=@();$script:reused=$false;$script:maintenance=$false
  $script:testMutex='Local\TERUISI.Watchdog.Test.'+[guid]::NewGuid().ToString('N')
  [IO.File]::WriteAllText($WorkerPath,@'
param([switch]$FunctionsOnly,[string]$Action)
function Get-WorkerServiceMutexName { $script:testMutex }
function Assert-WorkerMaintenanceInactive {if($script:maintenance){throw 'maintenance_active'}}
function Get-CurrentManifestPath {'fixture'}
function Get-ManifestIdentity { @{ReleaseId='fixture'} }
function Get-WorkerStatusInternal { @{State='stale_or_invalid_receipt';Receipt=@{supervisorPid=123}} }
function Get-CimInstance {if($script:reused){return @{ProcessId=123}}}
function Invoke-WorkerSystemStart {
  $code='$m=[Threading.Mutex]::new($false,"'+$script:testMutex+'");$v=$m.WaitOne(0);if($v){$m.ReleaseMutex()};$m.Dispose();Write-Output $v'
  $answer=& $PowerShellPath -NoProfile -Command $code
  if([string]$answer -ne 'False'){throw 'worker_mutex_not_held'}
  $script:recoveryStages+='unique_start'
}
'@)
  function Restore-WatchSupervisor {$script:recoveryStages+='supervisor'}
  Invoke-WatchRecovery $fence
  Assert-Watch (($script:recoveryStages -join ',') -eq 'unique_start,supervisor') 'unique engine and supervisor must share Worker mutex in order'
  foreach($case in @('fence','maintenance','reused')){
    $script:recoveryStages=@();$script:maintenance=($case -eq 'maintenance');$script:reused=($case -eq 'reused')
    $passedFence=if($case -eq 'fence'){'b'*64}else{$fence}
    $blocked=$false;try{Invoke-WatchRecovery $passedFence}catch{$blocked=$true}
    Assert-Watch ($blocked -and $script:recoveryStages.Count -eq 0) "unsafe $case recovery reached Start"
  }
  @{passed=$script:checks;productionServicesTouched=$false;messagesSent=$false}|ConvertTo-Json -Compress
} finally {
  # Exact task-owned temp root only; no production paths are used by fixtures.
  if($testRoot.StartsWith([IO.Path]::GetTempPath()) -and (Split-Path $testRoot -Leaf) -match '^watchdog-tests-[0-9a-f]{32}$'){Remove-Item -LiteralPath $testRoot -Recurse -Force}
}
