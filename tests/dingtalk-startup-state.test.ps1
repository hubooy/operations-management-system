param([Parameter(Mandatory=$true)][string]$Controller)
$ErrorActionPreference = 'Stop'
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($Controller),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw 'Controller syntax errors' }
foreach ($name in @('Read-DingTalkStartup','Set-DingTalkStartup','Start-ConfiguredDingTalkReceiver','Start-DingTalkWorkers','Invoke-DingTalkReceiver','Get-DingTalkConnectionState')) {
  $fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true)
  if (-not $fn) { throw "Missing $name" }
  . ([scriptblock]::Create($fn.Extent.Text))
}
$originalReceiver = ${function:Invoke-DingTalkReceiver}
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('ding-startup-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$DingTalkStartupPath=Join-Path $fixture 'startup.json'
$DingTalkConfigPath=Join-Path $fixture 'config.json'
$AiStartupPath=Join-Path $fixture 'ai.json'
$LogDirectory=$fixture
$script:calls=@(); $script:checkFails=$false
function Assert-AiRuntimeEntry { $script:calls += 'guard' }
function Read-JsonFile($Path,$Label) { Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json }
function Get-FileSha256($Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Test-ExactObjectPropertyNames($Value,$Names) { (@(Compare-Object @($Value.PSObject.Properties.Name | Sort-Object) @($Names | Sort-Object)).Count -eq 0) }
function Write-AtomicJson($Path,$Value) { [IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Compress)) }
function Write-LauncherEvent { }
function Invoke-DingTalkReceiver([bool]$CheckOnly, [object]$StartupApproval=$null, [switch]$Schedules) {
  $script:calls += $(if($CheckOnly){'check'}else{'start'})
  if($CheckOnly -and $script:checkFails){throw 'fixture verification failure'}
}
function Assert-Rejected([scriptblock]$Operation) {
  $denied=$false; try { & $Operation | Out-Null } catch { $denied=$true }
  if(-not $denied){throw 'Expected rejection'}
}
try {
  [IO.File]::WriteAllLines((Join-Path $fixture 'django-ai-dingtalk.fixture-run.stdout.log'), @(
    '{"status":"starting"}', '{"event":"callback_accepted"}', '{"status":"connected"}', '{"status":"recovering"}'
  ))
  if((Get-DingTalkConnectionState 'fixture-run') -cne 'recovering' -or
      (Get-DingTalkConnectionState '../invalid') -cne 'unknown' -or
      (Get-DingTalkConnectionState 'missing-run') -cne 'starting') { throw 'Connection state parsing failed' }
  # Missing approval does not read credentials, call DWS, or start a receiver.
  Start-ConfiguredDingTalkReceiver | Out-Null
  if(($script:calls -join ',') -cne 'guard'){throw 'Missing approval dispatched'}
  Write-AtomicJson $DingTalkConfigPath @{enabled=$true}
  Write-AtomicJson $AiStartupPath @{authorityEpoch='fixture-epoch';cutoverId='fixture-cutover'}
  $script:checkFails=$true
  Assert-Rejected { Set-DingTalkStartup $true }
  if(Test-Path $DingTalkStartupPath){throw 'Failed verification saved approval'}
  $script:checkFails=$false; $script:calls=@()
  Set-DingTalkStartup $true
  if(($script:calls -join ',') -cne 'guard,check' -or -not (Read-DingTalkStartup)){throw 'Enable sent or did not verify'}
  $script:calls=@(); Start-ConfiguredDingTalkReceiver | Out-Null
  if(($script:calls -join ',') -cne 'guard,start,start'){throw 'Enabled startup did not delegate exactly once'}
  # Approval cannot silently survive a changed runtime identity/configuration.
  Write-AtomicJson $DingTalkConfigPath @{enabled=$true;changed=$true}
  $script:calls=@(); Assert-Rejected { Start-ConfiguredDingTalkReceiver }
  if($script:calls -contains 'start'){throw 'Changed config dispatched'}
  Write-AtomicJson $DingTalkConfigPath @{enabled=$true}
  Set-DingTalkStartup $true
  Write-AtomicJson $AiStartupPath @{authorityEpoch='other';cutoverId='fixture-cutover'}
  Assert-Rejected { Start-ConfiguredDingTalkReceiver }
  # Manual stop persists opt-out; disabling does not stop an already running process.
  $script:calls=@(); Set-DingTalkStartup $false; Start-ConfiguredDingTalkReceiver | Out-Null
  if(Read-DingTalkStartup){throw 'Opt-out was ignored'}
  if($script:calls -contains 'start' -or $script:calls -contains 'check'){throw 'Disabled startup dispatched'}
  foreach($invalid in @('{','{"version":1,"enabled":"true"}','{"version":1,"enabled":true,"configSha256":"bad","authorityEpoch":"fixture","cutoverId":"fixture"}')) {
    [IO.File]::WriteAllText($DingTalkStartupPath,$invalid)
    Assert-Rejected { Start-ConfiguredDingTalkReceiver }
  }
  # Execute the real receiver entry with isolated adapters: duplicate process never launches,
  # and an identity conflict fails closed instead of stopping or taking over a process.
  ${function:Invoke-DingTalkReceiver}=$originalReceiver
  $DingTalkBotCredentialPath=Join-Path $fixture 'bot.json'; Write-AtomicJson $DingTalkBotCredentialPath @{fixture=$true}
  $DingTalkSchedulePidPath='fixture-schedule-pid'; $DingTalkPidPath='fixture-pid'; $Python='fixture-python'; $BackendRoot='fixture-backend'
  $AiWriterHealthUrl='fixture-health'; $WriterStatementTimeoutMs=1; $AiWriterMaxBodyBytes=1
  function Assert-PostgresListenerOwnership {}
  function Test-PostgresReady { $true }
  function Read-Secrets { @{} }
  function Read-AiCredentials { @{WriterPassword='fixture'} }
  function Get-AiWriteAuthority { @{status='postgres';authorityEpoch='fixture';cutoverId='fixture'} }
  function Database-Url { 'fixture-db' }
  function Wait-DjangoReady {}
  function Invoke-WithAiEnvironment($a,$b,$c,$d,$e,$f,$g,[scriptblock]$Operation){ & $Operation }
  function Invoke-BoundedNativeProcess { 'fixture-dependencies' }
  function Get-Sha256Text { 'a'*64 }
  function Get-ConfigFingerprint { 'fixture-fingerprint' }
  function Resolve-OwnedProcess { if($script:identityConflict){throw 'fixture identity conflict'}; if($script:processPresent){@{ProcessId=123}} }
  function Start-ManagedProcess { $script:launches++; $script:processPresent=$true }
  $script:identityConflict=$false; $script:processPresent=$true; $script:launches=0
  $RunId='fixture'
  Invoke-DingTalkReceiver $false | Out-Null
  if($script:launches -ne 0){throw 'Duplicate process launch'}
  $script:processPresent=$false
  Invoke-DingTalkReceiver $false | Out-Null
  Invoke-DingTalkReceiver $false | Out-Null
  if($script:launches -ne 1){throw 'Fresh startup or subsequent singleton check failed'}
  Assert-Rejected { Invoke-DingTalkReceiver $false @{authorityEpoch='stale';cutoverId='fixture';configSha256=(Get-FileSha256 $DingTalkConfigPath)} }
  Assert-Rejected { Invoke-DingTalkReceiver $false @{authorityEpoch='fixture';cutoverId='fixture';configSha256=('b'*64)} }
  $script:identityConflict=$true
  Assert-Rejected { Invoke-DingTalkReceiver $false }
  # Independent process ownership and startup: a Stream dependency failure must
  # leave the already started scheduler untouched, with no duplicate on retry.
  $script:workers=@{}; $script:workerStarts=@(); $script:dependencyFailure=$false
  function Resolve-OwnedProcess($Service) { if($script:workers.ContainsKey($Service)){@{ProcessId=$script:workers[$Service]}} }
  function Start-ManagedProcess($Service,$Executable,$Arguments) {
    $script:workers[$Service]=100+$script:workerStarts.Count
    $script:workerStarts += $Service
    if($Service -ceq 'django-ai-dingtalk-schedule' -and $Arguments -notcontains 'dingtalk_schedule'){throw 'Wrong schedule command'}
    if($Arguments -notcontains '--bot-credentials'){throw 'Unbound credentials'}
  }
  function Invoke-BoundedNativeProcess { if($script:dependencyFailure){throw 'Stream SDK unavailable'}; 'fixture-dependencies' }
  Start-DingTalkWorkers | Out-Null
  Start-DingTalkWorkers | Out-Null
  if(($script:workerStarts -join ',') -cne 'django-ai-dingtalk-schedule,django-ai-dingtalk'){throw 'Independent singleton/start order failed'}
  $script:workers=@{}; $script:workerStarts=@(); $script:dependencyFailure=$true
  Assert-Rejected { Start-DingTalkWorkers }
  if(($script:workerStarts -join ',') -cne 'django-ai-dingtalk-schedule' -or -not $script:workers.ContainsKey('django-ai-dingtalk-schedule')){throw 'Stream failure blocked or stopped scheduler'}
  Write-Output 'DingTalk startup isolated state and process tests passed'
} finally {
  # Delete only the exact unique fixture directory under the OS temp directory.
  $resolved=[IO.Path]::GetFullPath($fixture)
  if((Split-Path -Parent $resolved).TrimEnd('\') -ine ([IO.Path]::GetTempPath()).TrimEnd('\') -or (Split-Path -Leaf $resolved) -notlike 'ding-startup-*'){throw 'Unsafe fixture path'}
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
