[CmdletBinding()]
param(
  [ValidateSet(
    "Run", "Probe", "Status", "Arm", "Disarm",
    "InstallStartup", "RestoreOneShotStartup"
  )]
  [string]$Action = "Status",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [switch]$Execute,
  [switch]$ConfirmedStartupReplacement
)

$ErrorActionPreference = "Stop"
$SupervisorUtf8NoBom = [Text.UTF8Encoding]::new($false)
$SupervisorFixedRuntimeRoot = "D:\teruisi-runtime\django-sales"
$SupervisorVersion = "teruisi-django-runtime-supervisor-v2"
$SupervisorDesiredStateVersion = "teruisi-django-supervisor-desired-state-v1"
$SupervisorStateVersion = "teruisi-django-supervisor-state-v1"
$SupervisorReceiptVersion = "teruisi-django-supervisor-process-v1"
$SupervisorProbeIntervalSeconds = 15
$SupervisorFailureThreshold = 2
$SupervisorRestartWindowMinutes = 15
$SupervisorMaxRestartAttempts = 3
$SupervisorHealthyHeartbeatMinutes = 5
$SupervisorRequest = [pscustomobject][ordered]@{
  Action = $Action
  RuntimeRoot = $RuntimeRoot
  Execute = $Execute.IsPresent
  ConfirmedStartupReplacement = $ConfirmedStartupReplacement.IsPresent
}

function Test-SupervisorFullyQualifiedPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  try { [void][IO.Path]::GetFullPath($Path) } catch { return $false }
  if ([IO.Path]::DirectorySeparatorChar -eq "\") {
    return $Path -match "^[A-Za-z]:[\\/]" -or
      $Path -match "^[\\/]{2}[^\\/]+[\\/]+[^\\/]+(?:[\\/]|$)"
  }
  return $Path.StartsWith("/", [StringComparison]::Ordinal)
}

function Get-SupervisorCanonicalPath([string]$Path) {
  return [IO.Path]::GetFullPath($Path).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
  )
}

function Assert-SupervisorExactPropertySet(
  [object]$Value,
  [string[]]$Expected,
  [string]$Label
) {
  if ($null -eq $Value -or $Value -isnot [pscustomobject]) {
    throw "$Label 必须是 JSON 对象"
  }
  $actual = @($Value.PSObject.Properties.Name | Sort-Object)
  $wanted = @($Expected | Sort-Object)
  if ($actual.Count -ne $wanted.Count) { throw "$Label 字段集合无效" }
  for ($index = 0; $index -lt $wanted.Count; $index++) {
    if ([string]$actual[$index] -cne [string]$wanted[$index]) {
      throw "$Label 字段集合无效"
    }
  }
}

function Test-SupervisorInteger([object]$Value) {
  if ($null -eq $Value) { return $false }
  return [Type]::GetTypeCode($Value.GetType()) -in @(
    [TypeCode]::SByte, [TypeCode]::Byte,
    [TypeCode]::Int16, [TypeCode]::UInt16,
    [TypeCode]::Int32, [TypeCode]::UInt32,
    [TypeCode]::Int64, [TypeCode]::UInt64
  )
}

function Test-SupervisorOptionalTimestamp([string]$Value) {
  if ([string]::IsNullOrEmpty($Value)) { return $true }
  $parsed = [DateTimeOffset]::MinValue
  return [DateTimeOffset]::TryParse($Value, [ref]$parsed)
}

function Write-SupervisorAtomicText([string]$Path, [string]$Value) {
  $directory = Split-Path -Parent $Path
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $temporary = Join-Path $directory (
    ".{0}.{1}.tmp" -f [IO.Path]::GetFileName($Path), [Guid]::NewGuid().ToString("N")
  )
  try {
    [IO.File]::WriteAllText($temporary, $Value, $SupervisorUtf8NoBom)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force
    }
  }
}

function Get-SupervisorDesiredState {
  if (-not (Test-Path -LiteralPath $SupervisorDesiredStatePath -PathType Leaf)) {
    return [pscustomobject][ordered]@{
      version = $SupervisorDesiredStateVersion
      desiredState = "stopped"
      reason = "missing_fail_closed"
      updatedAt = ""
      serviceScriptSha256 = ""
    }
  }
  $state = Read-JsonFile $SupervisorDesiredStatePath "Django supervisor desired-state"
  Assert-SupervisorExactPropertySet $state @(
    "version", "desiredState", "reason", "updatedAt", "serviceScriptSha256"
  ) "Django supervisor desired-state"
  $updatedAt = [DateTimeOffset]::MinValue
  if ([string]$state.version -cne $SupervisorDesiredStateVersion -or
      [string]$state.desiredState -notin @("running", "stopped") -or
      [string]$state.reason -cnotmatch "^[a-z][a-z0-9_]{2,63}$" -or
      -not [DateTimeOffset]::TryParse([string]$state.updatedAt, [ref]$updatedAt) -or
      [string]$state.serviceScriptSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "Django supervisor desired-state 无效"
  }
  return $state
}

function Set-SupervisorDesiredState([string]$State, [string]$Reason) {
  if ($State -notin @("running", "stopped") -or
      $Reason -cnotmatch "^[a-z][a-z0-9_]{2,63}$") {
    throw "Django supervisor desired-state 参数无效"
  }
  Write-AtomicJson $SupervisorDesiredStatePath ([pscustomobject][ordered]@{
    version = $SupervisorDesiredStateVersion
    desiredState = $State
    reason = $Reason
    updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
    serviceScriptSha256 = Get-FileSha256 $ServiceScriptPath
  })
}

function Assert-SupervisorStatusPayload([object]$Status) {
  Assert-SupervisorExactPropertySet $Status @(
    "PostgreSQL", "DjangoReader", "DjangoWriter",
    "ReaderReadiness", "WriterReadiness", "RuntimeAcl",
    "RuntimeAclVerification", "Startup", "CheckedAt"
  ) "Django service status"
  if ([string]$Status.PostgreSQL -notin @(
        "stopped", "running", "not_ready", "foreign_or_unverified"
      ) -or
      [string]$Status.DjangoReader -notin @(
        "stopped", "running", "foreign_port_owner", "ownership_error"
      ) -or
      [string]$Status.DjangoWriter -notin @(
        "stopped", "running", "foreign_port_owner", "ownership_error"
      ) -or
      [string]$Status.ReaderReadiness -notin @("ready", "not_ready") -or
      [string]$Status.WriterReadiness -notin @("ready", "not_ready") -or
      [string]$Status.RuntimeAcl -notin @("root_hardened", "not_hardened") -or
      [string]$Status.RuntimeAclVerification -cne "root_only_status" -or
      [string]$Status.Startup -notin @("installed", "not_installed")) {
    throw "Django service status 枚举无效"
  }
  $checkedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse([string]$Status.CheckedAt, [ref]$checkedAt)) {
    throw "Django service status 时间无效"
  }
}

function Invoke-SupervisorServiceStatus {
  $powershell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
  $run = Invoke-BoundedNativeProcess $powershell @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", $ServiceScriptPath,
    "-Action", "Status",
    "-RuntimeRoot", $SupervisorRequest.RuntimeRoot,
    "-Json"
  ) $InstalledAppRoot
  $payload = ConvertFrom-UniqueNativeJson $run "读取 Django 服务状态"
  Assert-SupervisorStatusPayload $payload
  return $payload
}

function Get-SupervisorHealthClassification([object]$Status) {
  Assert-SupervisorStatusPayload $Status
  $fingerprintMaterial = [pscustomobject][ordered]@{
    PostgreSQL = [string]$Status.PostgreSQL
    DjangoReader = [string]$Status.DjangoReader
    DjangoWriter = [string]$Status.DjangoWriter
    ReaderReadiness = [string]$Status.ReaderReadiness
    WriterReadiness = [string]$Status.WriterReadiness
    RuntimeAcl = [string]$Status.RuntimeAcl
  } | ConvertTo-Json -Compress
  $fingerprint = Get-Sha256Text $fingerprintMaterial
  $healthy = (
    [string]$Status.PostgreSQL -ceq "running" -and
    [string]$Status.DjangoReader -ceq "running" -and
    [string]$Status.DjangoWriter -ceq "running" -and
    [string]$Status.ReaderReadiness -ceq "ready" -and
    [string]$Status.WriterReadiness -ceq "ready" -and
    [string]$Status.RuntimeAcl -ceq "root_hardened"
  )
  if ($healthy) {
    return [pscustomobject][ordered]@{
      health = "healthy"
      recoverable = $false
      code = "all_components_ready"
      fingerprint = $fingerprint
    }
  }

  if ([string]$Status.RuntimeAcl -cne "root_hardened") {
    return [pscustomobject][ordered]@{
      health = "unhealthy"
      recoverable = $false
      code = "runtime_acl_not_hardened"
      fingerprint = $fingerprint
    }
  }
  if ([string]$Status.PostgreSQL -in @("not_ready", "foreign_or_unverified") -or
      [string]$Status.DjangoReader -in @("foreign_port_owner", "ownership_error") -or
      [string]$Status.DjangoWriter -in @("foreign_port_owner", "ownership_error")) {
    return [pscustomobject][ordered]@{
      health = "unhealthy"
      recoverable = $false
      code = "ownership_or_port_conflict"
      fingerprint = $fingerprint
    }
  }

  if ([string]$Status.PostgreSQL -ceq "stopped") {
    return [pscustomobject][ordered]@{
      health = "unhealthy"
      recoverable = $true
      code = "postgresql_process_stopped"
      fingerprint = $fingerprint
    }
  }
  if ([string]$Status.PostgreSQL -ceq "running" -and
      ([string]$Status.DjangoReader -ceq "stopped" -or
       [string]$Status.DjangoWriter -ceq "stopped")) {
    return [pscustomobject][ordered]@{
      health = "unhealthy"
      recoverable = $true
      code = "managed_child_process_stopped"
      fingerprint = $fingerprint
    }
  }
  return [pscustomobject][ordered]@{
    health = "unhealthy"
    recoverable = $false
    code = "running_process_not_ready"
    fingerprint = $fingerprint
  }
}

function New-SupervisorState {
  return [pscustomobject][ordered]@{
    version = $SupervisorStateVersion
    updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
    desiredState = "stopped"
    health = "unknown"
    code = "not_probed"
    recoverable = $false
    statusFingerprint = ""
    consecutiveFailures = 0
    restartWindowStartedAt = ""
    restartAttemptsInWindow = 0
    nextRestartEligibleAt = ""
    lastHealthyAt = ""
    lastRestartAttemptAt = ""
    lastAlertAt = ""
    lastAlertKey = ""
    supervisorProcessId = 0
    supervisorScriptSha256 = Get-FileSha256 $SupervisorScriptPath
  }
}

function Read-SupervisorState {
  if (-not (Test-Path -LiteralPath $SupervisorStatePath -PathType Leaf)) {
    return New-SupervisorState
  }
  $state = Read-JsonFile $SupervisorStatePath "Django supervisor state"
  Assert-SupervisorExactPropertySet $state @(
    "version", "updatedAt", "desiredState", "health", "code", "recoverable",
    "statusFingerprint", "consecutiveFailures", "restartWindowStartedAt",
    "restartAttemptsInWindow", "nextRestartEligibleAt", "lastHealthyAt",
    "lastRestartAttemptAt", "lastAlertAt", "lastAlertKey",
    "supervisorProcessId", "supervisorScriptSha256"
  ) "Django supervisor state"
  if ([string]$state.version -cne $SupervisorStateVersion -or
      [string]$state.desiredState -notin @("running", "stopped") -or
      [string]$state.health -notin @("unknown", "healthy", "unhealthy", "stopped") -or
      [string]$state.code -cnotmatch "^[a-z][a-z0-9_]{2,95}$" -or
      $state.recoverable -isnot [bool] -or
      ([string]$state.statusFingerprint -ne "" -and
        [string]$state.statusFingerprint -cnotmatch "^[0-9a-f]{64}$") -or
      -not (Test-SupervisorInteger $state.consecutiveFailures) -or
      [int]$state.consecutiveFailures -lt 0 -or
      [int]$state.consecutiveFailures -gt 1000 -or
      -not (Test-SupervisorInteger $state.restartAttemptsInWindow) -or
      [int]$state.restartAttemptsInWindow -lt 0 -or
      [int]$state.restartAttemptsInWindow -gt $SupervisorMaxRestartAttempts -or
      -not (Test-SupervisorInteger $state.supervisorProcessId) -or
      [int64]$state.supervisorProcessId -lt 0 -or
      -not (Test-SupervisorOptionalTimestamp ([string]$state.updatedAt)) -or
      -not (Test-SupervisorOptionalTimestamp ([string]$state.restartWindowStartedAt)) -or
      -not (Test-SupervisorOptionalTimestamp ([string]$state.nextRestartEligibleAt)) -or
      -not (Test-SupervisorOptionalTimestamp ([string]$state.lastHealthyAt)) -or
      -not (Test-SupervisorOptionalTimestamp ([string]$state.lastRestartAttemptAt)) -or
      -not (Test-SupervisorOptionalTimestamp ([string]$state.lastAlertAt)) -or
      [string]$state.supervisorScriptSha256 -cne (Get-FileSha256 $SupervisorScriptPath)) {
    throw "Django supervisor state 版本或脚本摘要无效"
  }
  return $state
}

function Write-SupervisorState([object]$State) {
  $State.updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
  $State.supervisorProcessId = [int]$PID
  $State.supervisorScriptSha256 = Get-FileSha256 $SupervisorScriptPath
  Write-AtomicJson $SupervisorStatePath $State
}

function Write-SupervisorAlert(
  [object]$State,
  [string]$Severity,
  [string]$Code,
  [string]$StatusFingerprint
) {
  if ($Severity -notin @("warning", "critical", "recovered") -or
      $Code -cnotmatch "^[a-z][a-z0-9_]{2,95}$" -or
      $StatusFingerprint -cnotmatch "^[0-9a-f]{64}$") {
    throw "Django supervisor alert 参数无效"
  }
  $now = [DateTimeOffset]::UtcNow
  $alertKey = "$Severity`:$Code`:$StatusFingerprint"
  $lastAlertAt = [DateTimeOffset]::MinValue
  $recentDuplicate = (
    [string]$State.lastAlertKey -ceq $alertKey -and
    [DateTimeOffset]::TryParse([string]$State.lastAlertAt, [ref]$lastAlertAt) -and
    ($now - $lastAlertAt).TotalMinutes -lt 30
  )
  if ($recentDuplicate) { return $false }

  New-Item -ItemType Directory -Path $SupervisorAlertDirectory -Force | Out-Null
  $eventId = "alert-{0}-{1}" -f (
    $now.ToString("yyyyMMdd'T'HHmmss'Z'")
  ), ([Guid]::NewGuid().ToString("N").Substring(0, 12))
  $path = Join-Path $SupervisorAlertDirectory "$eventId.json"
  Write-AtomicJson $path ([pscustomobject][ordered]@{
    version = "teruisi-django-health-alert-v1"
    eventId = $eventId
    createdAt = $now.ToString("o")
    severity = $Severity
    code = $Code
    statusFingerprint = $StatusFingerprint
    notificationStatus = "pending_local_outbox"
    notificationConstraint = "志高助手_to_测试群聊_only"
    containsBusinessData = $false
    containsCredentials = $false
  })
  $State.lastAlertAt = $now.ToString("o")
  $State.lastAlertKey = $alertKey
  return $true
}

function Reset-SupervisorRestartWindowIfExpired([object]$State) {
  $now = [DateTimeOffset]::UtcNow
  $window = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse(
        [string]$State.restartWindowStartedAt,
        [ref]$window
      ) -or ($now - $window).TotalMinutes -ge $SupervisorRestartWindowMinutes) {
    $State.restartWindowStartedAt = $now.ToString("o")
    $State.restartAttemptsInWindow = 0
    $State.nextRestartEligibleAt = ""
  }
}

function Test-SupervisorRestartEligible([object]$State) {
  Reset-SupervisorRestartWindowIfExpired $State
  if ([int]$State.restartAttemptsInWindow -ge $SupervisorMaxRestartAttempts) {
    return $false
  }
  $next = [DateTimeOffset]::MinValue
  if ([DateTimeOffset]::TryParse([string]$State.nextRestartEligibleAt, [ref]$next) -and
      [DateTimeOffset]::UtcNow -lt $next) {
    return $false
  }
  return $true
}

function Invoke-SupervisorServiceStart([string]$ExpectedDesiredStateSha256) {
  if ($ExpectedDesiredStateSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "Django supervisor Start fence 摘要无效"
  }
  $powershell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
  $arguments = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", $ServiceScriptPath,
    "-Action", "Start",
    "-RuntimeRoot", $SupervisorRequest.RuntimeRoot,
    "-SupervisorExpectedDesiredStateSha256", $ExpectedDesiredStateSha256
  )
  return Invoke-SupervisorStartOperator $powershell $arguments
}

function Invoke-SupervisorStartOperator([string]$Executable,[string[]]$Arguments) {
  # A service descendant can inherit a native pipeline handle after its parent
  # exits. Waiting for pipeline EOF would freeze supervision indefinitely.
  # Redirect to files and wait ONLY for the direct Start operator's exit code.
  $id=[guid]::NewGuid().ToString('N')
  $stdout=Join-Path $SupervisorMonitorDirectory "start-$id.stdout.log"
  $stderr=Join-Path $SupervisorMonitorDirectory "start-$id.stderr.log"
  $quoted=@($Arguments|ForEach-Object {ConvertTo-ProcessArgument $_})
  $process=Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $InstalledAppRoot `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
  try {
    # Keep the native process handle before exit; Windows PowerShell 5 may
    # otherwise expose a null ExitCode on Start-Process's returned wrapper.
    $nativeHandle=$process.Handle
    while(-not $process.WaitForExit(15000)) {
      # Do not kill the Start operator or detach a second recovery. Its original
      # lifecycle mutex and desired-state fence still own the in-flight action.
      $pending=Read-SupervisorState
      $pending.health='unknown';$pending.code='recovery_start_pending';$pending.recoverable=$false
      Write-SupervisorState $pending
    }
    if($null -eq $process.ExitCode){throw 'supervisor_start_exit_unavailable'}
    $exitCode=[int]$process.ExitCode
    $output=@(foreach($path in @($stdout,$stderr)) { if(Test-Path -LiteralPath $path){Get-Content -LiteralPath $path -Tail 64} })
    $diagnostic=Get-BoundedNativeDiagnostic $output
  } finally { $process.Dispose() }
  return [pscustomobject][ordered]@{
    success = $exitCode -eq 0
    exitCode = $exitCode
    diagnosticSha256 = [string]$diagnostic.OutputSha256
  }
}

function Invoke-SupervisorProbe {
  try {
    $status = Invoke-SupervisorServiceStatus
    $classification = Get-SupervisorHealthClassification $status
    if (Test-Path -LiteralPath $AiStartupEnabledPath -PathType Leaf) {
      $ai = Get-SimpleDjangoDomainStatus "django-ai-reader" $DjangoAiReaderPidPath $DjangoAiWriterPidPath 8111 8112 $DjangoAiReaderHealthUrl $DjangoAiWriterHealthUrl "AiReader" "AiWriter"
      $aiIdentity = [ordered]@{ Reader = [string]$ai.AiReader; Writer = [string]$ai.AiWriter; ReaderReady = [string]$ai.ReaderReadiness; WriterReady = [string]$ai.WriterReadiness }
      $classification.fingerprint = Get-Sha256Text ([string]$classification.fingerprint + ($aiIdentity | ConvertTo-Json -Compress))
      $identityError = [string]$ai.AiReader -notin @("running", "stopped") -or [string]$ai.AiWriter -notin @("running", "stopped")
      $readinessError = ([string]$ai.AiReader -ceq "running" -and [string]$ai.ReaderReadiness -cne "ready") -or ([string]$ai.AiWriter -ceq "running" -and [string]$ai.WriterReadiness -cne "ready")
      if ($identityError -or $readinessError) {
        $classification.health = "unhealthy"; $classification.recoverable = $false
        $classification.code = if ($identityError) { "ai_process_identity_mismatch" } else { "ai_readiness_failed" }
      } elseif ([string]$classification.health -ceq "healthy" -and ([string]$ai.AiReader -ceq "stopped" -or [string]$ai.AiWriter -ceq "stopped")) {
        $classification.health = "unhealthy"; $classification.recoverable = $true; $classification.code = "ai_process_stopped"
      }
    }
    return [pscustomobject][ordered]@{
      probeStatus = "completed"
      status = $status
      health = [string]$classification.health
      recoverable = [bool]$classification.recoverable
      code = [string]$classification.code
      fingerprint = [string]$classification.fingerprint
    }
  } catch {
    $digest = Get-Sha256Text (Protect-LogText $_.Exception.Message)
    return [pscustomobject][ordered]@{
      probeStatus = "failed"
      status = $null
      health = "unknown"
      recoverable = $false
      code = "status_probe_failed"
      fingerprint = $digest
    }
  }
}

function Invoke-SupervisorCycle {
  $desired = Get-SupervisorDesiredState
  $state = Read-SupervisorState
  $state.desiredState = [string]$desired.desiredState
  if ([string]$desired.desiredState -cne "running") {
    $state.health = "stopped"
    $state.code = "desired_state_stopped"
    $state.recoverable = $false
    $state.consecutiveFailures = 0
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "stopped"
      shouldContinue = $false
      restarted = $false
      code = "desired_state_stopped"
    }
  }

  $probe = Invoke-SupervisorProbe
  $previousHealth = [string]$state.health
  $state.health = [string]$probe.health
  $state.code = [string]$probe.code
  $state.recoverable = [bool]$probe.recoverable
  $state.statusFingerprint = [string]$probe.fingerprint
  if ([string]$probe.health -ceq "healthy") {
    $state.consecutiveFailures = 0
    $now = [DateTimeOffset]::UtcNow
    $lastStateWrite = [DateTimeOffset]::MinValue
    $heartbeatDue = (
      -not [DateTimeOffset]::TryParse([string]$state.updatedAt, [ref]$lastStateWrite) -or
      ($now - $lastStateWrite).TotalMinutes -ge $SupervisorHealthyHeartbeatMinutes
    )
    $state.lastHealthyAt = $now.ToString("o")
    if ($previousHealth -notin @("healthy", "unknown", "stopped")) {
      Write-SupervisorAlert $state "recovered" "django_runtime_recovered" (
        [string]$probe.fingerprint
      ) | Out-Null
    }
    if ($previousHealth -cne "healthy" -or $heartbeatDue) {
      Write-SupervisorState $state
    }
    return [pscustomobject][ordered]@{
      status = "healthy"
      shouldContinue = $true
      restarted = $false
      code = [string]$probe.code
    }
  }

  $state.consecutiveFailures = [Math]::Min(
    [int]$state.consecutiveFailures + 1,
    1000
  )
  if (-not [bool]$probe.recoverable) {
    Write-SupervisorAlert $state "critical" ([string]$probe.code) (
      [string]$probe.fingerprint
    ) | Out-Null
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "unhealthy"
      shouldContinue = $true
      restarted = $false
      code = [string]$probe.code
    }
  }

  if ([int]$state.consecutiveFailures -lt $SupervisorFailureThreshold) {
    Write-SupervisorAlert $state "warning" ([string]$probe.code) (
      [string]$probe.fingerprint
    ) | Out-Null
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "confirming_failure"
      shouldContinue = $true
      restarted = $false
      code = [string]$probe.code
    }
  }

  if (-not (Test-SupervisorRestartEligible $state)) {
    Write-SupervisorAlert $state "critical" "restart_budget_exhausted" (
      [string]$probe.fingerprint
    ) | Out-Null
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "restart_budget_exhausted"
      shouldContinue = $true
      restarted = $false
      code = "restart_budget_exhausted"
    }
  }

  # Re-probe immediately before mutation. A transient outage or newly foreign
  # port owner must never be turned into an automatic Start operation.
  $confirmed = Invoke-SupervisorProbe
  if ([string]$confirmed.health -ceq "healthy") {
    $state.health = "healthy"
    $state.code = "recovered_before_restart"
    $state.recoverable = $false
    $state.statusFingerprint = [string]$confirmed.fingerprint
    $state.consecutiveFailures = 0
    $state.lastHealthyAt = [DateTimeOffset]::UtcNow.ToString("o")
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "healthy"
      shouldContinue = $true
      restarted = $false
      code = "recovered_before_restart"
    }
  }
  if (-not [bool]$confirmed.recoverable -or
      [string]$confirmed.code -cne [string]$probe.code) {
    $state.health = [string]$confirmed.health
    $state.code = [string]$confirmed.code
    $state.recoverable = [bool]$confirmed.recoverable
    $state.statusFingerprint = [string]$confirmed.fingerprint
    Write-SupervisorAlert $state "critical" "restart_preflight_changed" (
      [string]$confirmed.fingerprint
    ) | Out-Null
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "unhealthy"
      shouldContinue = $true
      restarted = $false
      code = "restart_preflight_changed"
    }
  }

  Reset-SupervisorRestartWindowIfExpired $state
  $state.restartAttemptsInWindow = [int]$state.restartAttemptsInWindow + 1
  $state.lastRestartAttemptAt = [DateTimeOffset]::UtcNow.ToString("o")
  $backoffSeconds = [Math]::Min(
    15 * [Math]::Pow(2, [int]$state.restartAttemptsInWindow - 1),
    120
  )
  $state.nextRestartEligibleAt = [DateTimeOffset]::UtcNow.AddSeconds(
    $backoffSeconds
  ).ToString("o")
  Write-SupervisorState $state
  $restartDesired = Get-SupervisorDesiredState
  if ([string]$restartDesired.desiredState -cne "running") {
    $state.desiredState = "stopped"
    $state.health = "stopped"
    $state.code = "desired_state_stopped_before_restart"
    $state.recoverable = $false
    $state.consecutiveFailures = 0
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "stopped"
      shouldContinue = $false
      restarted = $false
      code = "desired_state_stopped_before_restart"
    }
  }
  $desiredStateSha256 = Get-FileSha256 $SupervisorDesiredStatePath
  if ($desiredStateSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "Django supervisor Start fence 文件摘要无效"
  }
  $start = Invoke-SupervisorServiceStart $desiredStateSha256
  $after = Invoke-SupervisorProbe
  if ($start.success -and [string]$after.health -ceq "healthy") {
    $state.health = "healthy"
    $state.code = "restart_completed"
    $state.recoverable = $false
    $state.statusFingerprint = [string]$after.fingerprint
    $state.consecutiveFailures = 0
    $state.lastHealthyAt = [DateTimeOffset]::UtcNow.ToString("o")
    Write-SupervisorAlert $state "recovered" "restart_completed" (
      [string]$after.fingerprint
    ) | Out-Null
    Write-SupervisorState $state
    return [pscustomobject][ordered]@{
      status = "healthy"
      shouldContinue = $true
      restarted = $true
      code = "restart_completed"
    }
  }

  $state.health = [string]$after.health
  $state.code = "restart_failed"
  $state.recoverable = [bool]$after.recoverable
  $state.statusFingerprint = [string]$after.fingerprint
  Write-SupervisorAlert $state "critical" "restart_failed" (
    [string]$after.fingerprint
  ) | Out-Null
  Write-SupervisorState $state
  return [pscustomobject][ordered]@{
    status = "unhealthy"
    shouldContinue = $true
    restarted = $false
    code = "restart_failed"
    startExitCode = [int]$start.exitCode
    startDiagnosticSha256 = [string]$start.diagnosticSha256
  }
}

function Write-SupervisorProcessReceipt {
  $snapshot = Get-ProcessSnapshot ([int]$PID) 5
  if ($null -eq $snapshot -or
      -not (Test-CommandLineReferencesPath $snapshot.CommandLine $SupervisorScriptPath)) {
    throw "无法建立 Django supervisor 当前进程身份"
  }
  Write-AtomicJson $SupervisorPidPath ([pscustomobject][ordered]@{
    version = $SupervisorReceiptVersion
    processId = [int]$snapshot.ProcessId
    creationDate = [string]$snapshot.CreationDate
    executablePath = [string]$snapshot.ExecutablePath
    commandLine = [string]$snapshot.CommandLine
    scriptPathSha256 = Get-Sha256Text (Get-SupervisorCanonicalPath $SupervisorScriptPath)
    scriptSha256 = Get-FileSha256 $SupervisorScriptPath
    startedAt = [DateTimeOffset]::UtcNow.ToString("o")
  })
  return $snapshot
}

function Resolve-SupervisorProcess {
  if (-not (Test-Path -LiteralPath $SupervisorPidPath -PathType Leaf)) { return $null }
  $receipt = Read-JsonFile $SupervisorPidPath "Django supervisor process receipt"
  Assert-SupervisorExactPropertySet $receipt @(
    "version", "processId", "creationDate", "executablePath", "commandLine",
    "scriptPathSha256", "scriptSha256", "startedAt"
  ) "Django supervisor process receipt"
  if ([string]$receipt.version -cne $SupervisorReceiptVersion -or
      [string]$receipt.scriptPathSha256 -cne (
        Get-Sha256Text (Get-SupervisorCanonicalPath $SupervisorScriptPath)
      ) -or
      [string]$receipt.scriptSha256 -cne (Get-FileSha256 $SupervisorScriptPath)) {
    throw "Django supervisor process receipt 与当前 operator 不一致"
  }
  $snapshot = Get-ProcessSnapshot ([int]$receipt.processId) 1
  if ($null -eq $snapshot) {
    Remove-Item -LiteralPath $SupervisorPidPath -Force
    return $null
  }
  $creation = ConvertTo-CanonicalCreationDate $receipt.creationDate
  if ([string]$snapshot.CreationDate -cne $creation -or
      [string]$snapshot.ExecutablePath -ine [string]$receipt.executablePath -or
      [string]$snapshot.CommandLine -cne [string]$receipt.commandLine -or
      -not (Test-CommandLineReferencesPath $snapshot.CommandLine $SupervisorScriptPath)) {
    if (Remove-PreviousBootProcessRecordIfSafe `
          $SupervisorPidPath $receipt.creationDate $receipt.startedAt) {
      return $null
    }
    throw "Django supervisor PID 已复用或进程身份不一致"
  }
  return $snapshot
}

function Remove-OwnSupervisorReceipt([object]$Snapshot) {
  if (-not (Test-Path -LiteralPath $SupervisorPidPath -PathType Leaf)) { return }
  try {
    $receipt = Read-JsonFile $SupervisorPidPath "Django supervisor process receipt"
    if ([int]$receipt.processId -eq [int]$Snapshot.ProcessId -and
        (ConvertTo-CanonicalCreationDate $receipt.creationDate) -ceq
          [string]$Snapshot.CreationDate) {
      Remove-Item -LiteralPath $SupervisorPidPath -Force
    }
  } catch {
    # Never remove an identity record that cannot still be tied to this process.
  }
}

function Invoke-SupervisorRun {
  if (-not $SupervisorRequest.Execute) {
    throw "运行 Django supervisor 必须显式提供 -Execute"
  }
  Assert-DeployedApplication
  Assert-RuntimeRootAclHardened
  $desired = Get-SupervisorDesiredState
  if ([string]$desired.desiredState -cne "running" -or
      [string]$desired.serviceScriptSha256 -cne (Get-FileSha256 $ServiceScriptPath)) {
    throw "Django supervisor desired-state 不是当前 operator 的 running；拒绝自动启动服务"
  }
  $name = "Local\TERUISI-DjangoRuntimeSupervisor-" + (
    Get-Sha256Text (Get-SupervisorCanonicalPath $SupervisorRequest.RuntimeRoot)
  ).Substring(0, 20)
  $mutex = [Threading.Mutex]::new($false, $name)
  $acquired = $false
  $snapshot = $null
  try {
    try {
      $acquired = $mutex.WaitOne([TimeSpan]::Zero)
    } catch [Threading.AbandonedMutexException] {
      $acquired = $true
    }
    if (-not $acquired) { throw "另一个 Django supervisor 已在运行" }
    if ($null -ne (Resolve-SupervisorProcess)) {
      throw "已有经过身份验证的 Django supervisor 进程"
    }
    $snapshot = Write-SupervisorProcessReceipt
    while ($true) {
      try {
        $cycle = Invoke-SupervisorCycle
        if (-not [bool]$cycle.shouldContinue) { break }
      } catch {
        $state = Read-SupervisorState
        $digest = Get-Sha256Text (Protect-LogText $_.Exception.Message)
        $state.health = "unknown"
        $state.code = "supervisor_cycle_failed"
        $state.recoverable = $false
        $state.statusFingerprint = $digest
        Write-SupervisorAlert $state "critical" "supervisor_cycle_failed" $digest | Out-Null
        Write-SupervisorState $state
      }
      Start-Sleep -Seconds $SupervisorProbeIntervalSeconds
    }
  } finally {
    if ($null -ne $snapshot) { Remove-OwnSupervisorReceipt $snapshot }
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
  }
}

function Invoke-SupervisorArm {
  if (-not $SupervisorRequest.Execute) {
    throw "启用 Django supervisor 必须显式提供 -Execute"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $probe = Invoke-SupervisorProbe
  if ([string]$probe.health -cne "healthy") {
    throw "只有整套 Django 服务已健康时才能启用 supervisor"
  }
  Set-SupervisorDesiredState "running" "explicit_arm"
  return [pscustomobject][ordered]@{
    status = "armed"
    desiredState = "running"
    serviceStateChanged = $false
    fingerprint = [string]$probe.fingerprint
  }
}

function Invoke-SupervisorDisarm {
  if (-not $SupervisorRequest.Execute) {
    throw "停用 Django supervisor 必须显式提供 -Execute"
  }
  Set-SupervisorDesiredState "stopped" "explicit_disarm"
  return [pscustomobject][ordered]@{
    status = "disarmed"
    desiredState = "stopped"
    serviceStateChanged = $false
  }
}

function Install-SupervisorStartup {
  if (-not $SupervisorRequest.Execute -or
      -not $SupervisorRequest.ConfirmedStartupReplacement) {
    throw "替换登录启动项必须显式提供 -Execute 与 -ConfirmedStartupReplacement"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $probe = Invoke-SupervisorProbe
  if ([string]$probe.health -cne "healthy") {
    throw "只有整套 Django 服务已健康时才能安装 supervisor 登录启动项"
  }
  $powershell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
  $expectedArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SupervisorScriptPath`" -Action Run -RuntimeRoot `"$($SupervisorRequest.RuntimeRoot)`" -Execute"
  $shell = New-Object -ComObject WScript.Shell
  $existingSnapshot = $null
  if (Test-Path -LiteralPath $StartupShortcut -PathType Leaf) {
    $existing = $shell.CreateShortcut($StartupShortcut)
    $allowedServiceArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstalledScriptPath`" -Action Start -RuntimeRoot `"$($SupervisorRequest.RuntimeRoot)`""
    if ([string]$existing.TargetPath -ine $powershell -or
        [string]$existing.Arguments -notin @($allowedServiceArguments, $expectedArguments)) {
      throw "现有 Django 登录启动项不是受控 one-shot 或 supervisor；拒绝覆盖"
    }
    $existingSnapshot = [pscustomobject][ordered]@{
      targetPath = [string]$existing.TargetPath
      arguments = [string]$existing.Arguments
      workingDirectory = [string]$existing.WorkingDirectory
      windowStyle = [int]$existing.WindowStyle
      description = [string]$existing.Description
    }
  }
  try {
    $shortcut = $shell.CreateShortcut($StartupShortcut)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = $expectedArguments
    $shortcut.WorkingDirectory = $InstalledAppRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = "TERUISI Django sales runtime supervisor"
    $shortcut.Save()
    $readback = $shell.CreateShortcut($StartupShortcut)
    if ([string]$readback.TargetPath -ine $powershell -or
        [string]$readback.Arguments -cne $expectedArguments -or
        [string]$readback.WorkingDirectory -ine $InstalledAppRoot) {
      throw "Django supervisor 登录启动项写入后回读不一致"
    }
    Set-SupervisorDesiredState "running" "startup_installed"
  } catch {
    if ($null -ne $existingSnapshot) {
      $rollback = $shell.CreateShortcut($StartupShortcut)
      $rollback.TargetPath = [string]$existingSnapshot.targetPath
      $rollback.Arguments = [string]$existingSnapshot.arguments
      $rollback.WorkingDirectory = [string]$existingSnapshot.workingDirectory
      $rollback.WindowStyle = [int]$existingSnapshot.windowStyle
      $rollback.Description = [string]$existingSnapshot.description
      $rollback.Save()
    } elseif (Test-Path -LiteralPath $StartupShortcut -PathType Leaf) {
      $candidate = $shell.CreateShortcut($StartupShortcut)
      if ([string]$candidate.TargetPath -ieq $powershell -and
          [string]$candidate.Arguments -ceq $expectedArguments) {
        Remove-Item -LiteralPath $StartupShortcut -Force
      }
    }
    throw
  }
  return [pscustomobject][ordered]@{
    status = "installed"
    desiredState = "running"
    serviceStateChanged = $false
    shortcut = "TERUISI Django Sales.lnk"
  }
}

function Restore-OneShotStartup {
  if (-not $SupervisorRequest.Execute -or
      -not $SupervisorRequest.ConfirmedStartupReplacement) {
    throw "恢复 one-shot 登录启动项必须显式提供 -Execute 与 -ConfirmedStartupReplacement"
  }
  $desired = Get-SupervisorDesiredState
  if ([string]$desired.desiredState -cne "stopped") {
    throw "恢复 one-shot 登录启动项前必须先 Disarm supervisor"
  }
  if ($null -ne (Resolve-SupervisorProcess)) {
    throw "恢复 one-shot 登录启动项前必须等待 supervisor 进程退出"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $powershell = (Get-Command "powershell.exe" -ErrorAction Stop).Source
  $supervisorArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SupervisorScriptPath`" -Action Run -RuntimeRoot `"$($SupervisorRequest.RuntimeRoot)`" -Execute"
  $oneShotArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstalledScriptPath`" -Action Start -RuntimeRoot `"$($SupervisorRequest.RuntimeRoot)`""
  $shell = New-Object -ComObject WScript.Shell
  if (-not (Test-Path -LiteralPath $StartupShortcut -PathType Leaf)) {
    throw "Django 登录启动项不存在；拒绝把恢复操作变成隐式新建"
  }
  $existing = $shell.CreateShortcut($StartupShortcut)
  if ([string]$existing.TargetPath -ine $powershell -or
      [string]$existing.Arguments -notin @($supervisorArguments, $oneShotArguments)) {
    throw "现有 Django 登录启动项身份不受控；拒绝覆盖"
  }
  $shortcut = $shell.CreateShortcut($StartupShortcut)
  $shortcut.TargetPath = $powershell
  $shortcut.Arguments = $oneShotArguments
  $shortcut.WorkingDirectory = $InstalledAppRoot
  $shortcut.WindowStyle = 7
  $shortcut.Description = "TERUISI Django sales service"
  $shortcut.Save()
  $readback = $shell.CreateShortcut($StartupShortcut)
  if ([string]$readback.TargetPath -ine $powershell -or
      [string]$readback.Arguments -cne $oneShotArguments -or
      [string]$readback.WorkingDirectory -ine $InstalledAppRoot) {
    throw "Django one-shot 登录启动项写入后回读不一致"
  }
  return [pscustomobject][ordered]@{
    status = "restored"
    desiredState = "stopped"
    serviceStateChanged = $false
    shortcut = "TERUISI Django Sales.lnk"
  }
}

if ($env:TERUISI_DJANGO_SUPERVISOR_LIBRARY_ONLY -ne "1") {
  if (-not (Test-SupervisorFullyQualifiedPath $SupervisorRequest.RuntimeRoot)) {
    throw "RuntimeRoot 必须是绝对路径"
  }
  $canonicalRuntime = Get-SupervisorCanonicalPath $SupervisorRequest.RuntimeRoot
  if ($canonicalRuntime -ine $SupervisorFixedRuntimeRoot) {
    throw "Django supervisor 只允许固定受保护 runtime"
  }
  $ServiceScriptPath = Join-Path $canonicalRuntime "app\tools\django-local-service.ps1"
  $SupervisorScriptPath = Join-Path $canonicalRuntime "app\tools\django-runtime-supervisor.ps1"
  if (-not (Test-Path -LiteralPath $ServiceScriptPath -PathType Leaf) -or
      (Get-SupervisorCanonicalPath $PSCommandPath) -ine
        (Get-SupervisorCanonicalPath $SupervisorScriptPath)) {
    throw "Django supervisor 只能从受保护的 runtime app operator 执行"
  }
  $previousLibraryOnly = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
  )
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  try {
    . $ServiceScriptPath -Action Status -RuntimeRoot $canonicalRuntime
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
    )
  }
  $SupervisorMonitorDirectory = Assert-RuntimeChildPath (
    Join-Path $canonicalRuntime "monitoring\django-runtime"
  )
  $SupervisorAlertDirectory = Assert-RuntimeChildPath (
    Join-Path $SupervisorMonitorDirectory "alerts\pending"
  )
  $SupervisorStatePath = Assert-RuntimeChildPath (
    Join-Path $SupervisorMonitorDirectory "state.json"
  )
  $SupervisorPidPath = Assert-RuntimeChildPath (
    Join-Path $RunDirectory "django-supervisor.pid.json"
  )

  $result = switch ($SupervisorRequest.Action) {
    "Run" { Invoke-SupervisorRun }
    "Probe" { Invoke-SupervisorProbe }
    "Arm" { Invoke-SupervisorArm }
    "Disarm" { Invoke-SupervisorDisarm }
    "InstallStartup" { Install-SupervisorStartup }
    "RestoreOneShotStartup" { Restore-OneShotStartup }
    "Status" {
      $desired = Get-SupervisorDesiredState
      $state = Read-SupervisorState
      $process = $null
      $processStatus = "stopped"
      try {
        $process = Resolve-SupervisorProcess
        if ($null -ne $process) { $processStatus = "running" }
      } catch {
        $processStatus = "ownership_error"
      }
      [pscustomobject][ordered]@{
        status = "completed"
        desiredState = [string]$desired.desiredState
        supervisorProcess = $processStatus
        health = [string]$state.health
        code = [string]$state.code
        lastHealthyAt = [string]$state.lastHealthyAt
        restartAttemptsInWindow = [int]$state.restartAttemptsInWindow
        pendingAlertCount = if (Test-Path -LiteralPath $SupervisorAlertDirectory) {
          @(
            Get-ChildItem -LiteralPath $SupervisorAlertDirectory -File -Filter "alert-*.json"
          ).Count
        } else { 0 }
        serviceStateChanged = $false
      }
    }
  }
  if ($SupervisorRequest.Action -cne "Run") {
    Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
  }
}
