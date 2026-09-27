#requires -Version 7.0
[CmdletBinding()]
param(
  [ValidateSet('Check','Install','Status','TestNotification')][string]$Action='Check',
  [switch]$Execute,
  [switch]$FunctionsOnly
)
$ErrorActionPreference='Stop'
$WatchdogVersion='teruisi-operations-watchdog-v2'
$TaskName='TERUISI Operations Watchdog'
$ProjectRoot='D:\运营管理系统'
$WatchdogRoot='D:\teruisi-runtime\operations-watchdog'
$BackendRoot='D:\teruisi-runtime\django-sales'
$ControlPath=Join-Path $ProjectRoot 'tools\operations-system-control.ps1'
$WorkerPath=Join-Path $ProjectRoot 'tools\worker-local-service.ps1'
$SupervisorPath=Join-Path $BackendRoot 'app\tools\django-runtime-supervisor.ps1'
$DesiredPath=Join-Path $BackendRoot 'run\django-supervisor-desired-state.json'
$MaintenancePath=Join-Path $BackendRoot 'run\system-maintenance.json'
$StatePath=Join-Path $WatchdogRoot 'state.json'
$PowerShellPath=(Get-Process -Id $PID).Path
$DwsPath=Join-Path $env:APPDATA 'npm\node_modules\dingtalk-workspace-cli\vendor\dws.exe'

function Read-WatchJson([string]$Path) {
  if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){return $null}
  $f=Get-Item -LiteralPath $Path -Force
  if($f.Attributes -band [IO.FileAttributes]::ReparsePoint -or $f.Length -gt 1048576){throw 'invalid_state_file'}
  $options=@{}; if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$options.DateKind='String'}
  return [IO.File]::ReadAllText($Path)|ConvertFrom-Json @options
}
function Write-WatchJson([string]$Path,[object]$Value) {
  $parent=Split-Path -Parent $Path
  if(-not(Test-Path -LiteralPath $parent)){New-Item -ItemType Directory -Path $parent|Out-Null}
  $temp="$Path.tmp-$([guid]::NewGuid().ToString('N'))"; $backup="$temp.bak"
  try{
    [IO.File]::WriteAllText($temp,($Value|ConvertTo-Json -Depth 16),[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temp,$Path,$backup,$true)}else{[IO.File]::Move($temp,$Path)}
  }finally{foreach($p in @($temp,$backup)){if(Test-Path -LiteralPath $p){[IO.File]::Delete($p)}}}
}
function Get-WatchHash([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Invoke-WatchProcess([string]$Executable,[string[]]$Arguments,[int]$Seconds=60) {
  # Only read operators / DWS run here. Service recovery runs in-process while holding its mutex.
  $info=[Diagnostics.ProcessStartInfo]::new($Executable); $info.UseShellExecute=$false
  $info.CreateNoWindow=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
  $info.WorkingDirectory=$WatchdogRoot
  foreach($a in $Arguments){$info.ArgumentList.Add($a)}
  $p=[Diagnostics.Process]::new(); $p.StartInfo=$info
  try{
    [void]$p.Start();$out=$p.StandardOutput.ReadToEndAsync();$err=$p.StandardError.ReadToEndAsync()
    if(-not $p.WaitForExit($Seconds*1000)){$p.Kill($true);throw 'probe_timeout'}
    if($p.ExitCode -ne 0){throw 'operator_failed'}
    $text=$out.GetAwaiter().GetResult().Trim()
    if($text.Length -gt 1048576){throw 'response_too_large'}
    return $text|ConvertFrom-Json
  }finally{$p.Dispose()}
}
function Invoke-WatchScript([string]$Path,[string[]]$Arguments){
  Invoke-WatchProcess $PowerShellPath (@('-NoProfile','-NonInteractive','-File',$Path)+$Arguments)
}
function Get-Admission {
  try{
    $desired=Read-WatchJson $DesiredPath
    if(-not $desired -or $desired.version -cne 'teruisi-django-supervisor-desired-state-v1' -or
      $desired.desiredState -notin @('running','stopped') -or
      $desired.serviceScriptSha256 -cne (Get-WatchHash (Join-Path $BackendRoot 'app\tools\django-local-service.ps1'))){throw 'invalid_desired_state'}
    if(Test-Path -LiteralPath $MaintenancePath){
      $maintenance=Read-WatchJson $MaintenancePath
      if($maintenance.version -cne 'teruisi-system-maintenance-v1' -or $maintenance.id -cnotmatch '^[0-9a-f]{32}$' -or
        [IO.Path]::GetFullPath([string]$maintenance.runtimeRoot).TrimEnd('\') -ine $BackendRoot){throw 'invalid_maintenance'}
      return @{mode='maintenance';fence=Get-WatchHash $DesiredPath}
    }
    return @{mode=[string]$desired.desiredState;fence=Get-WatchHash $DesiredPath}
  }catch{return @{mode='invalid';fence=$null}}
}
function Test-WatchHttp([string]$Url,[string]$Kind){
  $handler=[Net.Http.HttpClientHandler]::new();$handler.UseProxy=$false;$handler.AllowAutoRedirect=$false
  $client=[Net.Http.HttpClient]::new($handler);$client.Timeout=[TimeSpan]::FromSeconds(5)
  $request=[Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get,$Url);$response=$null
  try{
    [void]$request.Headers.TryAddWithoutValidation('x-teruisi-local-health','1')
    $response=$client.SendAsync($request,[Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    if([int]$response.StatusCode -ne 200){return @{ok=$false;status=[int]$response.StatusCode}}
    if($Kind -eq 'homepage'){return @{ok=$true;status=200}}
    $cts=[Threading.CancellationTokenSource]::new(5000)
    try{
      $stream=$response.Content.ReadAsStreamAsync().GetAwaiter().GetResult();$bytes=[byte[]]::new(8193);$n=0
      while($n -lt $bytes.Length){$read=$stream.ReadAsync($bytes,$n,$bytes.Length-$n,$cts.Token).GetAwaiter().GetResult();if($read -eq 0){break};$n+=$read}
      if($n -gt 8192){throw 'health_body_too_large'}
      $body=[Text.Encoding]::UTF8.GetString($bytes,0,$n)|ConvertFrom-Json
      $ok=$body.ok -eq $true
      if($Kind -in @('live','ready')){$ok=$ok -and $body.status -ceq $Kind}
      return @{ok=$ok;status=200}
    }finally{$cts.Dispose()}
  }catch{return @{ok=$false;status=0}}
  finally{if($response){$response.Dispose()};$request.Dispose();$client.Dispose()}
}
function Get-WatchSnapshot {
  $admission=Get-Admission
  $snapshot=@{at=[DateTimeOffset]::UtcNow.ToString('o');admission=$admission;system='unprobed';backend='unprobed';worker='unprobed';supervisor='unprobed';supervisorHealth='unprobed';components=@{};ports=@();probes=@{};healthy=$false;probeError=$false}
  # Planned shutdown is quiet, and a corrupt gate must never be treated as permission.
  if($admission.mode -ne 'running'){return $snapshot}
  try{
    $status=Invoke-WatchScript $ControlPath @('-Action','Status','-Json')
    $supervisor=Invoke-WatchScript $SupervisorPath @('-Action','Status')
    $snapshot.system=[string]$status.state;$snapshot.backend=[string]$status.backendState;$snapshot.worker=[string]$status.workerState
    $snapshot.supervisor=[string]$supervisor.supervisorProcess;$snapshot.supervisorHealth=[string]$supervisor.health
    $monitor=Read-WatchJson (Join-Path $BackendRoot 'monitoring\django-runtime\state.json')
    if(-not $monitor -or ([DateTimeOffset]::UtcNow-[DateTimeOffset]$monitor.updatedAt).TotalMinutes -gt 7){$snapshot.supervisorHealth='stale'}
    foreach($p in $status.components.PSObject.Properties){$snapshot.components[$p.Name]=[bool]$p.Value}
    $snapshot.releaseId=[string]$status.releaseId;$snapshot.workerPid=$status.portProcessId;$snapshot.supervisorPid=$status.supervisorProcessId
  }catch{$snapshot.probeError=$true}
  foreach($kind in @('homepage','live','ready','helper')){
    $url=switch($kind){homepage{'http://127.0.0.1:3000/'} helper{'http://127.0.0.1:5791/health'} default{"http://127.0.0.1:3000/_teruisi/local/health/$kind"}}
    $snapshot.probes[$kind]=Test-WatchHttp $url $kind
  }
  $snapshot.ports=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|Where-Object LocalPort -in @(3000,5791,5432)|ForEach-Object {@{port=[int]$_.LocalPort;pid=[int]$_.OwningProcess}})
  $snapshot.healthy=(-not $snapshot.probeError -and $snapshot.system -ceq 'Running' -and $snapshot.backend -ceq 'Ready' -and $snapshot.worker -ceq 'exact_release' -and $snapshot.supervisor -ceq 'running' -and $snapshot.supervisorHealth -ceq 'healthy' -and $snapshot.components.Count -ge 12 -and @($snapshot.components.Values|Where-Object {-not $_}).Count -eq 0 -and @($snapshot.probes.Values|Where-Object {-not $_.ok}).Count -eq 0)
  return $snapshot
}
function Get-WatchBusinessHealth {
  # Passive reader telemetry only: never create a full ranking scan every minute.
  $path=Join-Path $WatchdogRoot 'business-health.json'
  $now=[DateTimeOffset]::UtcNow
  try {
    $cached=Read-WatchJson $path
    if($cached -and $cached.version -eq 'market-read-probe-v1' -and $cached.status -in @('healthy','degraded','observing','unknown') -and
        ($now-[DateTimeOffset]$cached.checkedAt).TotalSeconds -ge 0 -and
        ($now-[DateTimeOffset]$cached.checkedAt).TotalSeconds -lt 300){return $cached}
  }catch{$cached=$null}
  $result=@{version='market-read-probe-v1';checkedAt=$now.ToString('o');status='unknown';reason='no_recent_observation'}
  $handler=[Net.Http.HttpClientHandler]::new();$handler.UseProxy=$false;$handler.AllowAutoRedirect=$false
  $client=[Net.Http.HttpClient]::new($handler);$client.Timeout=[TimeSpan]::FromSeconds(3);$client.MaxResponseContentBufferSize=8192
  try {
    $response=$client.GetAsync('http://127.0.0.1:8031/api/market/health/reads').GetAwaiter().GetResult()
    try {
      if([int]$response.StatusCode -eq 404){$result.reason='reader_upgrade_required'}
      elseif([int]$response.StatusCode -ne 200){$result.reason='probe_unavailable'}
      else {
        $body=$response.Content.ReadAsStringAsync().GetAwaiter().GetResult()|ConvertFrom-Json
        if($body.version -cne 'market-read-health-v1' -or $body.status -notin @('healthy','degraded','observing','unknown')){throw 'business_probe_invalid'}
        $result.status=[string]$body.status
        $result.reason=if($body.status -eq 'degraded'){'market_queries_failing'}elseif($body.status -eq 'healthy'){'recent_reads_succeeded'}else{'no_confirmed_failure'}
      }
    }finally{$response.Dispose()}
  }catch{$result.reason='probe_unavailable'}finally{$client.Dispose()}
  Write-WatchJson $path $result
  return $result
}
function New-WatchState {
  @{version=$WatchdogVersion;failures=0;lastCheck=$null;lastHealthy=$null;incident=$null;attempts=@();lastMode='unknown';lastFence=$null;notification=$null}
}
function Get-WatchDecision($Snapshot,$State,[DateTimeOffset]$Now) {
  if($Snapshot.admission.mode -in @('maintenance','stopped')){return 'suspended'}
  if($Snapshot.healthy){return 'healthy'}
  if([int]$State.failures -lt 2){return 'confirm_failure'}
  if($Snapshot.admission.mode -ne 'running' -or $Snapshot.probeError -or $Snapshot.supervisor -in @('ownership_error','status_error') -or $Snapshot.worker -notin @('stopped','stale_or_invalid_receipt','exact_release')){return 'alert_only'}
  # Clean Worker Stop leaves the backend running: do not reverse that explicit action.
  if($Snapshot.worker -eq 'stopped' -and $Snapshot.backend -eq 'Ready'){return 'alert_only'}
  $recent=@($State.attempts|Where-Object {($Now-[DateTimeOffset]$_).TotalMinutes -lt 15})
  if($recent.Count -ge 3){return 'budget_exhausted'}
  if($recent.Count -gt 0 -and ($Now-[DateTimeOffset]$recent[-1]).TotalMinutes -lt 5){return 'cooldown'}
  if($Snapshot.worker -eq 'exact_release' -and $Snapshot.backend -eq 'Ready' -and $Snapshot.supervisor -eq 'running'){return 'alert_only'}
  return 'recover'
}
function Restore-WatchSupervisor {
  $name='Local\TERUISI-DjangoSales-'+([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($BackendRoot))).ToLowerInvariant()).Substring(0,24)
  $guard=[Threading.Mutex]::new($false,$name);$locked=$false
  try {
  try{$locked=$guard.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
  if(-not $locked){throw 'django_lifecycle_busy'}
  $admission=Get-Admission;if($admission.mode -ne 'running'){throw 'supervisor_start_fenced'}
  $status=Invoke-WatchScript $SupervisorPath @('-Action','Status')
  if($status.supervisorProcess -eq 'running'){return}
  if($status.supervisorProcess -ne 'stopped'){throw 'supervisor_ownership_error'}
  $old=$env:PSModulePath
  try{
    $env:PSModulePath=[Environment]::GetEnvironmentVariable('PSModulePath','Machine')
    $id=[guid]::NewGuid().ToString('N')
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -WindowStyle Hidden -WorkingDirectory $BackendRoot -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$SupervisorPath,'-Action','Run','-Execute') -RedirectStandardOutput (Join-Path $WatchdogRoot "supervisor-$id.out.log") -RedirectStandardError (Join-Path $WatchdogRoot "supervisor-$id.err.log")|Out-Null
  }finally{$env:PSModulePath=$old}
  } finally {if($locked){$guard.ReleaseMutex()};$guard.Dispose()}
}
function Invoke-WatchRecovery([string]$ExpectedFence) {
  # Same OS thread holds the very same Worker mutex as Stop/Restart/EnterMaintenance.
  # Call the existing unique Start engine in library mode; no duplicate lifecycle implementation.
  & {
    param($Library,$Fence,$DesiredFile,$RestoreSupervisor)
    . $Library -FunctionsOnly -Action Status
    $lease=[Threading.Mutex]::new($false,(Get-WorkerServiceMutexName));$held=$false
    try{
      try{$held=$lease.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
      if(-not $held){throw 'lifecycle_busy'}
      Assert-WorkerMaintenanceInactive
      if((Get-FileHash -LiteralPath $DesiredFile).Hash.ToLowerInvariant() -cne $Fence){throw 'desired_state_changed'}
      $desired=Get-Content -LiteralPath $DesiredFile -Raw|ConvertFrom-Json
      if($desired.desiredState -cne 'running'){throw 'desired_state_stopped'}
      $identity=Get-ManifestIdentity (Get-CurrentManifestPath)
      $status=Get-WorkerStatusInternal $identity
      if($status.State -eq 'stale_or_invalid_receipt' -and $status.Receipt){
        # Reject reused PID even when both ports are empty; only absent owners are recoverable.
        if(Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$status.Receipt.supervisorPid)") {throw 'stale_pid_reused'}
      }
      [void](Invoke-WorkerSystemStart $identity)
      Assert-WorkerMaintenanceInactive
      & $RestoreSupervisor
    }finally{if($held){$lease.ReleaseMutex()};$lease.Dispose()}
  } $WorkerPath $ExpectedFence $DesiredPath ${function:Restore-WatchSupervisor}
}
function Get-WatchEvidence($Snapshot){
  $events=@()
  foreach($log in @('System','Application')){
    $filter=@{LogName=$log;StartTime=(Get-Date).AddMinutes(-20);Id=@(41,1074,6008,2004,1000,1001,1002)}
    $events+=@(Get-WinEvent -FilterHashtable $filter -MaxEvents 30 -ErrorAction SilentlyContinue|ForEach-Object {@{time=$_.TimeCreated.ToUniversalTime().ToString('o');id=$_.Id;provider=$_.ProviderName}})
  }
  $postgres=Get-ChildItem -LiteralPath (Join-Path $BackendRoot 'logs') -Filter 'postgresql-*.log' -File|Sort-Object LastWriteTime -Descending|Select-Object -First 1
  $recovery=@();if($postgres){$recovery=@(Get-Content -LiteralPath $postgres.FullName -Tail 160|Where-Object {$_ -match 'database system was interrupted|not properly shut down|redo done|ready to accept connections'}|ForEach-Object {($_ -split ' LOG: ',2)[0]+' '+ $(if($_ -match 'ready to accept'){'database_ready'}else{'database_recovery'})})}
  return @{snapshot=$Snapshot;events=$events;databaseRecovery=$recovery;capturedAt=[DateTimeOffset]::UtcNow.ToString('o')}
}
function Invoke-WatchDws([string[]]$Arguments){
  $result=Invoke-WatchProcess $DwsPath ($Arguments+@('--format','json','--timeout','15')) 25
  if($result.success -eq $false){throw 'dws_rejected'}
  return $result
}
function Get-WatchRecipient {
  $profiles=Invoke-WatchDws @('profile','list')
  $profile=@($profiles.profiles|Where-Object {$_.isCurrent -eq $true -and $_.corpName -ceq '佛山市登荣科技有限公司' -and $_.userName -ceq '邓伟章'})
  if($profile.Count -ne 1){throw 'owner_profile_ambiguous'}
  $binding=@('--profile',[string]$profile[0].profile)
  $self=Invoke-WatchDws (@('contact','user','get-self')+$binding)
  $rows=@($self.result);if($rows.Count -ne 1){throw 'owner_ambiguous'}
  $employee=$rows[0].orgEmployeeModel
  if($employee.userId -cne $profile[0].userId -or $employee.corpId -cne $profile[0].corpId -or $employee.orgUserName -cne '邓伟章'){throw 'owner_mismatch'}
  $search=Invoke-WatchDws (@('contact','user','search','--query','邓伟章')+$binding)
  $matches=@($search.result|Where-Object {$_.name -ceq '邓伟章'})
  if($matches.Count -ne 1 -or $matches[0].userId -cne $employee.userId){throw 'owner_search_ambiguous'}
  $bots=Invoke-WatchDws (@('chat','bot','search','--name','志高助手','--page','1','--size','30')+$binding)
  $exact=@($bots.robotList|Where-Object robotName -CEQ '志高助手')
  if(@($bots.robotList).Count -ge 30 -or $exact.Count -ne 1 -or -not $exact[0].robotCode){throw 'bot_ambiguous'}
  return @{profile=$profile[0].profile;user=$employee.userId;bot=$exact[0].robotCode}
}
function Send-WatchAlert($State,$Snapshot,[string]$Outcome,[switch]$DryRun){
  if(-not $DryRun -and $State.notification){
    if($State.notification -ne 'identity_failed'){return}
    # Only pre-send failures are retryable. Reservation/unknown/sent stay terminal.
    if([int]$State.identityAttempts -ge 3){return}
    if($State.lastIdentityAttempt -and ([DateTimeOffset]::UtcNow-[DateTimeOffset]$State.lastIdentityAttempt).TotalMinutes -lt 15){return}
  }
  $alertPath=Join-Path $WatchdogRoot "alerts\$($State.incident).json"
  $alert=@{incident=$State.incident;at=[DateTimeOffset]::UtcNow.ToString('o');outcome=$Outcome;status='pending_identity';ports=$Snapshot.ports;businessData=$false}
  if(-not $DryRun){Write-WatchJson $alertPath $alert}
  $stage='identity'
  try{
    if(-not $DryRun){$State.identityAttempts=[int]$State.identityAttempts+1;$State.lastIdentityAttempt=[DateTimeOffset]::UtcNow.ToString('o');Write-WatchJson $StatePath $State}
    $recipient=Get-WatchRecipient
    $label=switch($Outcome){recovered{'已恢复，并通过两轮检查'} recovery_failed{'自动恢复未通过'} budget_exhausted{'已达到本轮自动恢复次数上限'} cooldown{'等待下一次有间隔的安全恢复'} alert_only{'需人工检查进程归属或服务状态'} default{$Outcome}}
    $ports=(@(3000,5791,5432)|ForEach-Object {"$_="+$(if(@($Snapshot.ports|Where-Object port -eq $_).Count -gt 0){'监听中'}else{'未监听'})}) -join '、'
    $detail=if($Snapshot.business -and $Snapshot.business.status -eq 'degraded'){'市场查询连续失败；服务在线，未自动重启。'}elseif($Snapshot.supervisorHealth -eq 'stale'){'Django 守护状态超过 7 分钟未更新；需检查守护，业务服务未强制重启。'}else{''}
    $message="运营管理系统运行提醒。故障首次发现：$($State.firstFailure)。恢复结果：$label。首页/后端状态：$($Snapshot.system)/$($Snapshot.backend)。$detail 端口：$ports。需要人工处理："+$(if($Outcome -eq 'recovered'){'否。'}else{'是，请检查本机系统控制面板；未知进程或配置异常不会被强制接管。'})
    $arguments=@('chat','+messages-send','--as','bot','--profile',$recipient.profile,'--robot-code',$recipient.bot,'--users',$recipient.user,'--text',$message)
    if($DryRun){$preview=Invoke-WatchDws ($arguments+@('--dry-run'));if(-not $preview.dry_run -or $preview.executed){throw 'preview_invalid'};return @{status='dry_run_verified';executed=$false;targets=1}}
    # Persist BEFORE the non-idempotent remote call. Crashes/unknown delivery are never replayed.
    $alert.status='sending';Write-WatchJson $alertPath $alert
    $State.notification='sending';Write-WatchJson $StatePath $State
    $stage='delivery'
    $sent=Invoke-WatchDws ($arguments+@('--yes'))
    if($sent.success -ne $true -or $sent.failedCount -gt 0){throw 'delivery_unconfirmed'}
    $alert.status='sent';$State.notification='sent';$State.notificationReason=$null
  }catch{
    $alert.status=if($State.notification -eq 'sending'){'unknown'}else{'identity_failed'}
    $State.notification=$alert.status
    $known=@('owner_profile_ambiguous','owner_ambiguous','owner_mismatch','owner_search_ambiguous','bot_ambiguous','dws_rejected','operator_failed','probe_timeout','delivery_unconfirmed')
    $code=if($_.Exception.Message -cin $known){$_.Exception.Message}else{'preflight_failed'}
    $alert.reason="$stage`:$code";$State.notificationReason=$alert.reason
  }
  if(-not $DryRun){Write-WatchJson $alertPath $alert;Write-WatchJson $StatePath $State}
  elseif($alert.status -ne 'pending_identity'){throw 'notification_preflight_failed'}
}
function Invoke-WatchCycle([switch]$Recover){
  $previous=Read-WatchJson $StatePath
  $state=if($previous){$previous|ConvertTo-Json -Depth 10|ConvertFrom-Json -AsHashtable}else{New-WatchState}
  if($state.version -cne $WatchdogVersion){throw 'watchdog_state_invalid'}
  $snapshot=Get-WatchSnapshot;$now=[DateTimeOffset]::UtcNow
  if($snapshot.admission.mode -eq 'running'){
    $snapshot.business=Get-WatchBusinessHealth
    if($snapshot.business.status -eq 'degraded'){$state.marketFailureOpen=$true}
    elseif($snapshot.business.status -eq 'healthy'){$state.marketFailureOpen=$false}
    elseif($state.marketFailureOpen){$snapshot.business=@{status='degraded';reason='awaiting_successful_reads';checkedAt=$now.ToString('o')}}
    if($snapshot.business.status -eq 'degraded'){$snapshot.healthy=$false}
  }
  $state.lastCheck=$now.ToString('o')
  if($snapshot.admission.mode -in @('stopped','maintenance')){
    $state.failures=0;$state.lastMode=$snapshot.admission.mode;$state.lastFence=$snapshot.admission.fence
    Write-WatchJson $StatePath $state;return @{status='suspended';serviceChanged=$false}
  }
  if(-not $snapshot.healthy){
    if(-not $state.incident){$state.incident=[guid]::NewGuid().ToString('N');$state.firstFailure=$now.ToString('o');$state.notification=$null}
    $state.failures=[int]$state.failures+1
  }
  $decision=Get-WatchDecision $snapshot $state $now
  # First installation, maintenance exit, a new explicit Start or a recovered incident requires two healthy snapshots.
  if($snapshot.healthy -and ($state.incident -or $state.lastMode -ne 'running' -or $state.lastFence -ne $snapshot.admission.fence)){
    Start-Sleep -Seconds 5;$second=Get-WatchSnapshot
    $second.business=$snapshot.business
    if($second.business -and $second.business.status -eq 'degraded'){$second.healthy=$false}
    if(-not $second.healthy -or $second.admission.fence -ne $snapshot.admission.fence){$snapshot=$second;$decision='verification_pending'}
  }
  if($decision -eq 'recover' -and $Recover){
    $state.attempts=@($state.attempts|Where-Object {($now-[DateTimeOffset]$_).TotalMinutes -lt 15})+@($now.ToString('o'))
    Write-WatchJson $StatePath $state
    Write-WatchJson (Join-Path $WatchdogRoot "evidence\$($state.incident)-before.json") (Get-WatchEvidence $snapshot)
    try{
      Invoke-WatchRecovery $snapshot.admission.fence
      $first=Get-WatchSnapshot;Start-Sleep -Seconds 5;$second=Get-WatchSnapshot
      $first.business=$snapshot.business;$second.business=$snapshot.business
      if($snapshot.business -and $snapshot.business.status -eq 'degraded'){$first.healthy=$false;$second.healthy=$false}
      if($first.healthy -and $second.healthy -and $first.admission.fence -eq $second.admission.fence){$decision='recovered'}else{$decision='recovery_failed'}
      $snapshot=$second
    }catch{$decision='recovery_failed'}
  }
  if($state.incident){
    Write-WatchJson (Join-Path $WatchdogRoot "evidence\$($state.incident)-latest.json") (Get-WatchEvidence $snapshot)
    if($Recover -and [int]$state.failures -ge 2 -and $decision -notin @('confirm_failure','verification_pending')){Send-WatchAlert $state $snapshot $decision}
  }
  if($decision -in @('healthy','recovered')){$state.failures=0;$state.lastHealthy=[DateTimeOffset]::UtcNow.ToString('o');$state.incident=$null;$state.notification=$null;$state.notificationReason=$null;$state.identityAttempts=0;$state.lastIdentityAttempt=$null}
  $state.lastMode=if($decision -eq 'verification_pending'){'verification_pending'}else{'running'}
  $state.lastFence=$snapshot.admission.fence;$state.decision=$decision
  Write-WatchJson $StatePath $state
  Write-WatchJson (Join-Path $WatchdogRoot 'latest.json') $snapshot
  return @{status=$decision;serviceChanged=($decision -eq 'recovered');checkedAt=$snapshot.at}
}
function Install-Watchdog {
  if(-not $Execute){throw 'Install requires Execute'}
  $root=[IO.Path]::GetFullPath($WatchdogRoot)
  if($root -cne 'D:\teruisi-runtime\operations-watchdog'){throw 'invalid_install_root'}
  New-Item -ItemType Directory -Path $root -Force|Out-Null
  $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
  $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false);$acl.SetOwner($sid)
  foreach($s in @($sid,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'),[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($s,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
  $existingAcl=Get-Acl -LiteralPath $root
  $allowedSids=@($sid.Value,'S-1-5-18','S-1-5-32-544')
  $unexpected=@($existingAcl.Access|Where-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowedSids})
  if(-not $existingAcl.AreAccessRulesProtected -or $unexpected.Count -gt 0){Set-Acl -LiteralPath $root -AclObject $acl}
  $verifiedAcl=Get-Acl -LiteralPath $root
  if(-not $verifiedAcl.AreAccessRulesProtected -or @($verifiedAcl.Access|Where-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowedSids}).Count -gt 0){throw 'installation_acl_invalid'}
  $target=Join-Path $root 'operations-system-watchdog.ps1'
  Copy-Item -LiteralPath $PSCommandPath -Destination $target -Force
  if((Get-WatchHash $target) -cne (Get-WatchHash $PSCommandPath)){throw 'installation_hash_mismatch'}
  $launcherSource=Join-Path $PSScriptRoot 'watchdog-launcher\NoConsoleLauncher.cs'
  $launcherSourceHash=Get-WatchHash $launcherSource
  $launcher=Join-Path $root ("Watchdog.NoConsole-"+$launcherSourceHash.Substring(0,16)+'.exe')
  if(-not(Test-Path -LiteralPath $launcher)){
    $compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    & $compiler /nologo /target:winexe /platform:anycpu /optimize+ "/out:$launcher" $launcherSource
    if($LASTEXITCODE -ne 0){throw 'watchdog_launcher_compile_failed'}
  }
  $config=@{version=$WatchdogVersion;scriptSha256=Get-WatchHash $target;launcherSourceSha256=$launcherSourceHash;launcherSha256=Get-WatchHash $launcher;installedAt=[DateTimeOffset]::UtcNow.ToString('o')}
  Write-WatchJson (Join-Path $root 'installation.json') $config
  $taskAction=New-ScheduledTaskAction -Execute $launcher -WorkingDirectory $root -Argument "`"$PowerShellPath`" `"$target`""
  $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $triggers=@((New-ScheduledTaskTrigger -AtLogOn -User $user),(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
  $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
  $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  $existing=Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if($existing){
    $expectedLegacy="-NoProfile -NonInteractive -WindowStyle Hidden -File `"$target`" -Action Check -Execute"
    $knownLegacy=$existing.Actions.Execute -ceq $PowerShellPath -and $existing.Actions.Arguments -ceq $expectedLegacy
    $knownLauncher=$existing.Actions.Execute -ceq $launcher -and $existing.Actions.Arguments -ceq $taskAction.Arguments
    if(@($existing.Actions).Count -ne 1 -or -not($knownLegacy -or $knownLauncher) -or $existing.Actions.WorkingDirectory -cne $root){throw 'unrecognized_watchdog_task_action'}
    if($existing.State -eq 'Running'){throw 'wait_for_existing_watchdog_check_to_finish'}
    Set-ScheduledTask -TaskName $TaskName -Action $taskAction -ErrorAction Stop|Out-Null
    Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop|Out-Null
  }else{
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $triggers -Settings $settings -Principal $principal -ErrorAction Stop|Out-Null
  }
  $task=Get-ScheduledTask -TaskName $TaskName
  if($task.Actions.Execute -cne $launcher -or $task.Actions.Arguments -cne $taskAction.Arguments -or $task.Triggers.Count -ne 2 -or $task.Triggers[1].Repetition.Interval -ne 'PT1M'){throw 'task_readback_mismatch'}
  Start-ScheduledTask -TaskName $TaskName
  return @{status='installed';cadence='independent_minutely_and_logon';task=$TaskName}
}
if($FunctionsOnly){return}
$env:PATH=(Split-Path $PowerShellPath)+[IO.Path]::PathSeparator+$env:PATH
if($Action -eq 'Install'){Install-Watchdog|ConvertTo-Json -Compress;return}
if($Action -eq 'Status'){@{task=[string](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State;state=Read-WatchJson $StatePath}|ConvertTo-Json -Depth 8;return}
if(-not(Test-Path -LiteralPath $WatchdogRoot)){New-Item -ItemType Directory -Path $WatchdogRoot|Out-Null}
$mutex=[Threading.Mutex]::new($false,'Local\TERUISI.Operations.Watchdog.v2');$held=$false
try{
  try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
  if(-not $held){@{status='busy'}|ConvertTo-Json;return}
  if($Action -eq 'TestNotification'){Send-WatchAlert @{incident='preview';firstFailure='preview';notification=$null} @{system='preview';backend='preview';ports=@()} 'preview' -DryRun|ConvertTo-Json -Compress;return}
  if($Execute){
    $installation=Read-WatchJson (Join-Path $WatchdogRoot 'installation.json')
    if(-not $installation -or $installation.scriptSha256 -cne (Get-WatchHash $PSCommandPath)){throw 'installed_watchdog_binding_invalid'}
  }
  Invoke-WatchCycle -Recover:$Execute|ConvertTo-Json -Compress
}finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
