[CmdletBinding()]
param(
  [ValidateSet("Start", "Stop", "Restart", "Status", "Logs", "Menu")]
  [string]$Action = "Status",
  # 在隐藏的独立 PowerShell 进程中执行 Start/Stop/Restart，本进程立即返回；用 -Action Logs / Status 查看进度。
  [switch]$Background,
  # Stop/Restart 只作用于网页 Worker，保留 Django/PostgreSQL 后端继续运行。
  [switch]$KeepBackend,
  # Start/Restart 完成后打开浏览器。
  [switch]$Open,
  [switch]$Json
)

# 运营管理系统 Windows 生命周期入口：启动 / 停止 / 重启 / 状态 / 日志，可后台执行。
#
# 本脚本不复制任何启动逻辑：
#   - Start  统一走 tools/operations-system-control.ps1 -Action Start，即唯一启动引擎
#            tools/worker-local-service.ps1 -Action Start（Django/PostgreSQL -> Worker）的界面层；
#   - Stop   先通过 tools/worker-local-service.ps1 -Action Stop 按身份门禁停止 Worker，再调用
#            运行目录中的 django-local-service.ps1 -Action Stop 停止各域 Django 与 PostgreSQL；
#   - Restart 默认仍为完整 Stop + Start；传入 -KeepBackend 时走 Worker 原子热重启；
#   - Status 复用 operations-system-control.ps1 -Action Status -Json 的组合状态；
#   - -Background 只是把同一命令放到隐藏进程里执行，并把输出落到 tmp\system-service\ 日志。
# 全部 Stop/Restart 与桌面控制面板共用同一个系统互斥，避免面板启动与本脚本停止交错。

$ErrorActionPreference = "Stop"

# 测试可设置 TERUISI_SYSTEM_SERVICE_LIBRARY_ONLY=1 只加载函数定义，不执行任何动作。
$LibraryOnly = ($env:TERUISI_SYSTEM_SERVICE_LIBRARY_ONLY -eq "1")
if (-not $LibraryOnly -and -not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
  throw "本脚本只用于 Windows 本机正式环境；macOS/Linux 开发机请使用 npm run backend:dev 与 npx vinext dev。"
}

$ServiceVersion = "teruisi-operations-system-service-v1"
$SystemControlMutexName = "Local\TERUISI.Operations.SystemControl.v2"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ControllerPath = Join-Path $ProjectRoot "tools\operations-system-control.ps1"
$WorkerServicePath = Join-Path $ProjectRoot "tools\worker-local-service.ps1"
# 运行目录固定在 D 盘；用字符串拼接而不是 Join-Path，便于在非 Windows 机器上以库模式加载做单元测试。
$DjangoRuntimeRoot = "D:\teruisi-runtime\django-sales"
$DjangoServicePath = "$DjangoRuntimeRoot\app\tools\django-local-service.ps1"
$ServerUrl = "http://localhost:3000/"
$LogRoot = Join-Path $ProjectRoot "tmp\system-service"
$BackgroundReceiptPath = Join-Path $LogRoot "background.json"
$LogRetention = 20

$WindowsPowerShell = (Get-Command "powershell.exe" -ErrorAction SilentlyContinue)
if (-not $WindowsPowerShell -and -not $LibraryOnly) { throw "未找到 powershell.exe" }
$WindowsPowerShellPath = if ($WindowsPowerShell) { $WindowsPowerShell.Source } else { "" }
$DjangoPowerShell = Get-Command "pwsh.exe" -ErrorAction SilentlyContinue
if (-not $DjangoPowerShell) { $DjangoPowerShell = Get-Command "pwsh" -ErrorAction SilentlyContinue }
$DjangoPowerShellPath = if ($DjangoPowerShell) { $DjangoPowerShell.Source } else { "" }

# 进度行只写控制台，不进入管道，避免被函数返回值捕获。
function Write-Line([string]$Message) {
  if (-not $Json) { Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) }
}

function Write-ServiceResult([object]$Result) {
  if ($Json) {
    Write-Output ($Result | ConvertTo-Json -Compress -Depth 10)
    return
  }
  if ($Result.PSObject.Properties.Name -contains "message") {
    Write-Output ([string]$Result.message)
  } else {
    Write-Output ($Result | Format-List | Out-String)
  }
}

function Get-BoundedTail([string]$Path, [int]$MaximumCharacters = 1200) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
  try {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
      $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
      $text = $reader.ReadToEnd()
    } finally {
      $stream.Dispose()
    }
  } catch {
    return ""
  }
  $text = $text.Trim()
  if ($text.Length -gt $MaximumCharacters) { return $text.Substring($text.Length - $MaximumCharacters) }
  return $text
}

function Remove-OldLogs {
  if (-not (Test-Path -LiteralPath $LogRoot -PathType Container)) { return }
  $logs = @(Get-ChildItem -LiteralPath $LogRoot -Filter "*.log" -File | Sort-Object LastWriteTime -Descending)
  if ($logs.Count -le $LogRetention) { return }
  foreach ($old in $logs[$LogRetention..($logs.Count - 1)]) {
    try { Remove-Item -LiteralPath $old.FullName -Force -ErrorAction Stop } catch { }
  }
}

function New-LogPair([string]$Label) {
  [System.IO.Directory]::CreateDirectory($LogRoot) | Out-Null
  Remove-OldLogs
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  return [pscustomobject]@{
    Stdout = Join-Path $LogRoot "$Label-$stamp.stdout.log"
    Stderr = Join-Path $LogRoot "$Label-$stamp.stderr.log"
  }
}

function Assert-Dependencies([switch]$RequireDjango) {
  foreach ($required in @($ControllerPath, $WorkerServicePath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "缺少受控脚本：$required" }
  }
  if ($RequireDjango) {
    if (-not (Test-Path -LiteralPath $DjangoServicePath -PathType Leaf)) {
      throw "缺少运行目录中的 Django 控制器：$DjangoServicePath"
    }
    if ([string]::IsNullOrWhiteSpace($DjangoPowerShellPath)) {
      throw "运行目录中的 Django 控制器需要 PowerShell 7（pwsh.exe）"
    }
  }
}

# 与控制面板相同的非阻塞系统互斥：拿不到就说明面板或另一份本脚本正在启动/停止。
function Enter-SystemControlMutex {
  $mutex = New-Object System.Threading.Mutex($false, $SystemControlMutexName)
  $acquired = $false
  try {
    $acquired = $mutex.WaitOne([TimeSpan]::Zero)
  } catch [System.Threading.AbandonedMutexException] {
    $acquired = $true
  }
  return [pscustomobject]@{ Mutex = $mutex; Acquired = $acquired }
}

function Exit-SystemControlMutex([object]$Lease) {
  if (-not $Lease) { return }
  if ($Lease.Acquired) { try { $Lease.Mutex.ReleaseMutex() } catch { } }
  $Lease.Mutex.Dispose()
}

# 受控脚本会拉起长期存活的子进程，原生管道可能一直等待被继承的句柄；
# 因此统一用文件重定向启动，只等待直接子进程，并在等待期间把新输出转发到控制台。
function Invoke-ControlledScript(
  [string]$Executable,
  [string]$ScriptPath,
  [string[]]$Arguments,
  [string]$Label,
  [switch]$CaptureJson
) {
  $logs = New-LogPair $Label
  $argumentList = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "`"$ScriptPath`"") + $Arguments
  if ($CaptureJson) { $argumentList += "-Json" }
  $process = Start-Process -FilePath $Executable -ArgumentList $argumentList -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden -RedirectStandardOutput $logs.Stdout -RedirectStandardError $logs.Stderr -PassThru
  $forwarded = 0
  try {
    while (-not $process.WaitForExit(500)) {
      if (-not $CaptureJson -and -not $Json) { $forwarded = Copy-NewOutput $logs.Stdout $forwarded }
    }
    if (-not $CaptureJson -and -not $Json) { $forwarded = Copy-NewOutput $logs.Stdout $forwarded }
    $exitCode = [int]$process.ExitCode
  } finally {
    $process.Dispose()
  }
  $stdout = Get-BoundedTail $logs.Stdout 4000
  $stderr = Get-BoundedTail $logs.Stderr 1200
  if ($exitCode -ne 0) {
    $detail = @($stderr, $stdout) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($detail)) { $detail = "退出码 $exitCode" }
    throw "$Label 失败：$detail（日志：$($logs.Stdout)）"
  }
  if ($CaptureJson) {
    if ([string]::IsNullOrWhiteSpace($stdout)) { throw "$Label 未返回 JSON" }
    $lastLine = ($stdout -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1)
    if (-not $lastLine) { throw "$Label 未返回 JSON" }
    try { return ($lastLine | ConvertFrom-Json -ErrorAction Stop) } catch { throw "$Label 返回了无效 JSON" }
  }
  return $null
}

function Copy-NewOutput([string]$Path, [int]$AlreadyForwarded) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $AlreadyForwarded }
  try {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
      if ($stream.Length -le $AlreadyForwarded) { return $AlreadyForwarded }
      $stream.Position = $AlreadyForwarded
      $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
      $chunk = $reader.ReadToEnd()
      $position = [int]$stream.Length
    } finally {
      $stream.Dispose()
    }
  } catch {
    return $AlreadyForwarded
  }
  foreach ($line in ($chunk -split "`r?`n")) {
    if (-not [string]::IsNullOrWhiteSpace($line)) { Write-Host ("    " + $line.TrimEnd()) }
  }
  return $position
}

function Get-SystemStatus {
  Assert-Dependencies
  return Invoke-ControlledScript $WindowsPowerShellPath $ControllerPath @("-Action", "Status") "status" -CaptureJson
}

function Invoke-Start {
  Assert-Dependencies
  Write-Line "通过唯一总控启动运营管理系统（Django/PostgreSQL -> Worker），完整性校验可能需要较长时间……"
  $arguments = @("-Action", "Start")
  if ($Open) { $arguments += "-Open" }
  # 控制面板的 Start 自带最终组合状态与主页 HTTP 回查，退出码 0 即代表 Running/BackendDegraded；
  # 前台模式转发它的阶段输出，JSON 模式直接透传它的结果，不再额外做一轮耗时的全域状态探测。
  if ($Json) {
    $result = Invoke-ControlledScript $WindowsPowerShellPath $ControllerPath $arguments "start" -CaptureJson
    if ([string]$result.status -eq "failed" -or [string]$result.state -notin @("Running", "BackendDegraded")) {
      throw "启动结束但系统未达到可用状态：$([string]$result.state) $([string]$result.reason)"
    }
    $status = [string]$result.status
    $state = [string]$result.state
  } else {
    Invoke-ControlledScript $WindowsPowerShellPath $ControllerPath $arguments "start" | Out-Null
    $status = "started"
    $state = "Running"
  }
  Write-ServiceResult ([pscustomobject]@{
    version = $ServiceVersion
    action = "Start"
    status = $status
    state = $state
    url = $ServerUrl
    message = "运营管理系统已通过唯一总控完成启动与回查：$ServerUrl"
    checkedAt = (Get-Date).ToString("o")
  })
}

function Invoke-StopWorkerOnly {
  $arguments = @("-Action", "Stop")
  if (-not $KeepBackend) { $arguments += "-IncludeBackend" }
  $stopStatus = Invoke-ControlledScript $WindowsPowerShellPath $WorkerServicePath $arguments "stop-worker" -CaptureJson
  if ([string]$stopStatus.status -notin @("stopped", "already_stopped", "stale_receipt_cleared")) {
    throw "不可变 Worker 停止回执无效：$([string]$stopStatus.status)"
  }
  if (-not $KeepBackend -and $stopStatus.backendStopped -ne $true) { throw "Full system stop did not confirm backend shutdown" }
  Write-Line "网页 Worker：$([string]$stopStatus.status)"
  return [string]$stopStatus.status
}

function Invoke-Stop {
  Assert-Dependencies -RequireDjango:(-not $KeepBackend)
  $lease = Enter-SystemControlMutex
  try {
    if (-not $lease.Acquired) {
      throw "唯一总控正在启动或停止系统；为避免交错，本次停止请求已拒绝，请稍后重试"
    }
    $workerStatus = Invoke-StopWorkerOnly
  } finally {
    Exit-SystemControlMutex $lease
  }
  $message = if ($KeepBackend) {
    "网页 Worker 已停止（$workerStatus）；Django/PostgreSQL 后端继续运行。"
  } else {
    "运营管理系统已完整停止：Worker（$workerStatus）、各域 Django 服务与 PostgreSQL。"
  }
  Write-ServiceResult ([pscustomobject]@{
    version = $ServiceVersion
    action = "Stop"
    status = "stopped"
    workerStatus = $workerStatus
    backendStopped = (-not $KeepBackend)
    message = $message
    checkedAt = (Get-Date).ToString("o")
  })
}

function Invoke-Restart {
  if ($KeepBackend) {
    Assert-Dependencies -RequireDjango:$false
    $lease = Enter-SystemControlMutex
    try {
      if (-not $lease.Acquired) {
        throw "唯一总控正在启动或停止系统；为避免交错，本次热重启请求已拒绝，请稍后重试"
      }
      Write-Line "后端保持运行，正在校验并热重启网页 Worker……"
      $restartStatus = Invoke-ControlledScript $WindowsPowerShellPath $WorkerServicePath @("-Action", "Restart") "restart-worker" -CaptureJson
      if ([string]$restartStatus.status -ne "restarted") {
        throw "不可变 Worker 热重启回执无效：$([string]$restartStatus.status)"
      }
    } finally {
      Exit-SystemControlMutex $lease
    }
    if ($Open) { Start-Process $ServerUrl | Out-Null }
    Write-ServiceResult ([pscustomobject]@{
      version = $ServiceVersion
      action = "Restart"
      status = "restarted"
      mode = "hot"
      backendRestarted = $false
      elapsedMilliseconds = if ($restartStatus.PSObject.Properties.Name -contains "elapsedMilliseconds") { [int64]$restartStatus.elapsedMilliseconds } else { $null }
      url = $ServerUrl
      message = "网页 Worker 已完成热重启，Django/PostgreSQL 全程保持运行：$ServerUrl"
      checkedAt = (Get-Date).ToString("o")
    })
    return
  }
  Assert-Dependencies -RequireDjango:$true
  $lease = Enter-SystemControlMutex
  try {
    if (-not $lease.Acquired) { throw "Another system lifecycle operation is in progress" }
    $result = Invoke-ControlledScript $WindowsPowerShellPath $WorkerServicePath @("-Action", "RestartFull") "restart-full" -CaptureJson
    if ($result.status -ne "restarted" -or $result.backendRestarted -ne $true) { throw "Full restart receipt is invalid" }
    if ($Open) { Start-Process $ServerUrl | Out-Null }
    Write-ServiceResult ([pscustomobject]@{
      version = $ServiceVersion; action = "Restart"; status = "restarted"; mode = "full"
      backendRestarted = $true; message = "运营管理系统已完成完整重启。"; checkedAt = (Get-Date).ToString("o")
    })
  } finally { Exit-SystemControlMutex $lease }
}

function Get-ProcessStartUnixMilliseconds([object]$Process) {
  return ([DateTimeOffset]::new($Process.StartTime.ToUniversalTime(), [TimeSpan]::Zero)).ToUnixTimeMilliseconds()
}

function Read-BackgroundReceipt {
  if (-not (Test-Path -LiteralPath $BackgroundReceiptPath -PathType Leaf)) { return $null }
  try {
    $receipt = Get-Content -LiteralPath $BackgroundReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
  } catch {
    return $null
  }
  $process = Get-Process -Id ([int]$receipt.processId) -ErrorAction SilentlyContinue
  $running = $false
  if ($process) {
    # 用 Unix 毫秒整数比对进程创建时间，避免 PowerShell 7 的 ConvertFrom-Json 把 ISO 时间串自动转成 DateTime。
    try {
      $running = ((Get-ProcessStartUnixMilliseconds $process) -eq [int64]$receipt.processStartedAtUnixMs)
    } catch {
      $running = $false
    }
  }
  $receipt | Add-Member -NotePropertyName running -NotePropertyValue $running -Force
  return $receipt
}

function Start-InBackground {
  $existing = Read-BackgroundReceipt
  if ($existing -and $existing.running) {
    throw "已有后台任务在执行（$([string]$existing.action)，PID $([int]$existing.processId)，$([string]$existing.startedAt) 开始）；请先用 -Action Logs 查看进度"
  }
  $logs = New-LogPair ("background-" + $Action.ToLowerInvariant())
  $arguments = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Action", $Action)
  if ($KeepBackend) { $arguments += "-KeepBackend" }
  if ($Open) { $arguments += "-Open" }
  $process = Start-Process -FilePath $WindowsPowerShellPath -ArgumentList $arguments -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden -RedirectStandardOutput $logs.Stdout -RedirectStandardError $logs.Stderr -PassThru
  $startedAt = $null
  for ($attempt = 0; $attempt -lt 20 -and -not $startedAt; $attempt++) {
    try { $startedAt = Get-ProcessStartUnixMilliseconds $process } catch { Start-Sleep -Milliseconds 100 }
  }
  $receipt = [ordered]@{
    version = $ServiceVersion
    action = $Action
    processId = [int]$process.Id
    processStartedAtUnixMs = $startedAt
    startedAt = (Get-Date).ToString("o")
    stdoutLog = $logs.Stdout
    stderrLog = $logs.Stderr
  }
  [System.IO.File]::WriteAllText($BackgroundReceiptPath, (($receipt | ConvertTo-Json -Depth 4)), (New-Object System.Text.UTF8Encoding($false)))
  Write-ServiceResult ([pscustomobject]@{
    version = $ServiceVersion
    action = $Action
    status = "background_started"
    processId = [int]$process.Id
    stdoutLog = $logs.Stdout
    stderrLog = $logs.Stderr
    message = "$Action 已在后台执行（PID $($process.Id)）。查看进度：tools\operations-system-service.ps1 -Action Logs；查看结果：-Action Status。日志：$($logs.Stdout)"
    checkedAt = (Get-Date).ToString("o")
  })
}

function Show-Status {
  $status = Get-SystemStatus
  $background = Read-BackgroundReceipt
  $backgroundSummary = $null
  if ($background) {
    $backgroundSummary = [pscustomobject]@{
      action = [string]$background.action
      running = [bool]$background.running
      processId = [int]$background.processId
      startedAt = [string]$background.startedAt
      stdoutLog = [string]$background.stdoutLog
    }
  }
  $stateText = switch ([string]$status.state) {
    "Running" { "运行中" }
    "BackendDegraded" { "运行中（Django 后端未就绪）" }
    "Starting" { "启动中" }
    "Stopped" { "已停止" }
    default { [string]$status.state }
  }
  $message = "运营管理系统状态：$stateText"
  if ($status.PSObject.Properties.Name -contains "reason" -and -not [string]::IsNullOrWhiteSpace([string]$status.reason)) {
    $message += "；$([string]$status.reason)"
  }
  if ($backgroundSummary) {
    $message += if ($backgroundSummary.running) {
      "；后台 $($backgroundSummary.action) 正在执行（PID $($backgroundSummary.processId)，$($backgroundSummary.startedAt) 开始）"
    } else {
      "；最近一次后台 $($backgroundSummary.action) 已结束，日志：$($backgroundSummary.stdoutLog)"
    }
  }
  Write-ServiceResult ([pscustomobject]@{
    version = $ServiceVersion
    action = "Status"
    state = [string]$status.state
    backendState = if ($status.PSObject.Properties.Name -contains "backendState") { $status.backendState } else { $null }
    workerState = if ($status.PSObject.Properties.Name -contains "workerState") { $status.workerState } else { $null }
    reason = if ($status.PSObject.Properties.Name -contains "reason") { $status.reason } else { $null }
    background = $backgroundSummary
    url = $ServerUrl
    message = $message
    checkedAt = (Get-Date).ToString("o")
  })
  if ([string]$status.state -notin @("Running", "BackendDegraded")) { $script:exitCode = 2 }
}

function Show-Logs {
  $background = Read-BackgroundReceipt
  if ($background) {
    $state = if ($background.running) { "正在执行" } else { "已结束" }
    Write-Output "后台任务：$([string]$background.action)（PID $([int]$background.processId)，$([string]$background.startedAt) 开始，$state）"
    foreach ($logPath in @([string]$background.stdoutLog, [string]$background.stderrLog)) {
      $tail = Get-BoundedTail $logPath 6000
      if (-not [string]::IsNullOrWhiteSpace($tail)) {
        Write-Output "----- $logPath -----"
        Write-Output $tail
      }
    }
    return
  }
  if (-not (Test-Path -LiteralPath $LogRoot -PathType Container)) {
    Write-Output "尚无日志：$LogRoot"
    return
  }
  $latest = Get-ChildItem -LiteralPath $LogRoot -Filter "*.stdout.log" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $latest) {
    Write-Output "尚无日志：$LogRoot"
    return
  }
  Write-Output "----- $($latest.FullName) -----"
  Write-Output (Get-BoundedTail $latest.FullName 6000)
}

function Show-InteractiveMenu {
  while ($true) {
    Clear-Host
    Write-Host "=========================================="
    Write-Host " 运营管理系统"
    Write-Host "=========================================="
    Write-Host " 1  启动（前台，显示进度）"
    Write-Host " 2  后台启动（立即返回，用 6 查看进度）"
    Write-Host " 3  停止（Worker + Django/PostgreSQL）"
    Write-Host " 4  只停止网页 Worker（保留后端）"
    Write-Host " 5  热重启网页（后端保持运行，目标 30 秒）"
    Write-Host " 6  查看日志"
    Write-Host " 7  查看状态"
    Write-Host " 8  完整重启（Worker + Django/PostgreSQL）"
    Write-Host " 0  退出"
    Write-Host ""
    $choice = Read-Host "请选择"
    if ($choice -eq "0") { return }
    try {
      switch ($choice) {
        "1" {
          $script:Open = $true
          Invoke-Start
        }
        "2" {
          $script:Action = "Start"
          $script:Open = $true
          Start-InBackground
        }
        "3" {
          $script:KeepBackend = $false
          Invoke-Stop
        }
        "4" {
          $script:KeepBackend = $true
          Invoke-Stop
        }
        "5" {
          $script:Open = $true
          $script:KeepBackend = $true
          Invoke-Restart
        }
        "6" { Show-Logs }
        "7" { Show-Status }
        "8" {
          $script:Open = $true
          $script:KeepBackend = $false
          Invoke-Restart
        }
        default { Write-Host "无效选项：$choice" -ForegroundColor Yellow }
      }
    } catch {
      $failure = [string]$_.Exception.Message
      if ($failure.Length -gt 800) { $failure = $failure.Substring(0, 800) }
      Write-Host "操作失败：$failure" -ForegroundColor Red
    } finally {
      $script:Action = "Menu"
      $script:Open = $false
      $script:KeepBackend = $false
      $script:exitCode = 0
    }
    [void](Read-Host "按 Enter 返回菜单")
  }
}

if ($LibraryOnly) { return }

$script:exitCode = 0
try {
  if ($Background -and $Action -in @("Start", "Stop", "Restart")) {
    Start-InBackground
  } else {
    switch ($Action) {
      "Start" { Invoke-Start }
      "Stop" { Invoke-Stop }
      "Restart" { Invoke-Restart }
      "Status" { Show-Status }
      "Logs" { Show-Logs }
      "Menu" { Show-InteractiveMenu }
    }
  }
  exit $script:exitCode
} catch {
  $failure = [string]$_.Exception.Message
  if ($failure.Length -gt 800) { $failure = $failure.Substring(0, 800) }
  if ($Json) {
    Write-ServiceResult ([pscustomobject]@{
      version = $ServiceVersion
      action = $Action
      status = "failed"
      reason = $failure
      message = "运营管理系统 $Action 失败：$failure"
      checkedAt = (Get-Date).ToString("o")
    })
  } else {
    Write-Error "运营管理系统 $Action 失败：$failure"
  }
  exit 1
}
