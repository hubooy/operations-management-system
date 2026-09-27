[CmdletBinding()]
param(
  [ValidateSet("Panel", "Start", "Status", "StopWorker")]
  [string]$Action = "Panel",
  [switch]$Json,
  [switch]$Open,
  [switch]$Check,
  [switch]$StopWorker
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$UnifiedStartControlVersion = "teruisi-operations-system-control-v2"
$SystemControlMutexName = "Local\TERUISI.Operations.SystemControl.v2"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ControllerPath = $PSCommandPath
$ServerUrl = "http://localhost:3000/"
$LivenessUrl = "http://127.0.0.1:3000/_teruisi/local/health/live"
$ReadinessUrl = "http://127.0.0.1:3000/_teruisi/local/health/ready"
$HelperHealthUrl = "http://127.0.0.1:5791/health"
$LocalWorkerStarter = Join-Path $ProjectRoot "tools\worker-local-service.ps1"
$DjangoRuntimeRoot = "D:\teruisi-runtime\django-sales"
$DjangoRuntimeTools = Join-Path $DjangoRuntimeRoot "app\tools"
$DjangoService = Join-Path $DjangoRuntimeTools "django-local-service.ps1"
$DjangoNetshopService = Join-Path $DjangoRuntimeTools "django-netshop-service.ps1"
$DjangoMarketService = Join-Path $DjangoRuntimeTools "django-market-service.ps1"
$DjangoProductsService = Join-Path $DjangoRuntimeTools "django-products-service.ps1"
$DjangoWorkflowService = Join-Path $DjangoRuntimeTools "django-workflow-service.ps1"
$DjangoInventoryService = Join-Path $DjangoRuntimeTools "django-inventory-service.ps1"
$DjangoCustomerService = Join-Path $DjangoRuntimeTools "django-customer-service.ps1"
$DjangoAccessControlService = Join-Path $DjangoRuntimeTools "django-access-control.ps1"
$DjangoErpReferenceService = Join-Path $DjangoRuntimeTools "django-erp-reference.ps1"
$DjangoBiService = Join-Path $DjangoRuntimeTools "django-bi-service.ps1"
$PowerShellCommand = Get-Command "pwsh.exe" -ErrorAction SilentlyContinue
if (-not $PowerShellCommand) { $PowerShellCommand = Get-Command "pwsh" -ErrorAction SilentlyContinue }
if (-not $PowerShellCommand) { $PowerShellCommand = Get-Command "powershell.exe" -ErrorAction SilentlyContinue }
$PowerShellExecutable = if ($PowerShellCommand) { $PowerShellCommand.Source } else { "" }
$script:launchProcess = $null
$script:launchStartedAt = $null
$script:launchStdoutLog = $null
$script:launchStderrLog = $null
$script:lastDjangoCheckAt = $null
$script:lastDjangoState = $null
$script:lastWorkerCheckAt = $null
$script:lastWorkerStatus = $null
$script:lastHealthCheckAt = $null
$script:lastHealthState = "Unresponsive"

if ($Check -and $StopWorker) {
  throw "-Check 与 -StopWorker 不能同时使用"
}
if ($Check) { $Action = "Status" }
if ($StopWorker) { $Action = "StopWorker" }
if ($Open -and $Action -notin @("Start", "Panel")) {
  throw "-Open 只允许与 Start 或 Panel 一起使用"
}

function Get-GoogleChromeExecutable {
  $candidates = New-Object System.Collections.Generic.List[string]
  foreach ($basePath in @(
      $env:ProgramFiles,
      [Environment]::GetEnvironmentVariable("ProgramFiles(x86)"),
      $env:LOCALAPPDATA
    )) {
    if (-not [string]::IsNullOrWhiteSpace($basePath)) {
      [void]$candidates.Add((Join-Path $basePath "Google\Chrome\Application\chrome.exe"))
    }
  }
  $pathCommand = Get-Command "chrome.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pathCommand -and -not [string]::IsNullOrWhiteSpace($pathCommand.Source)) {
    [void]$candidates.Add($pathCommand.Source)
  }
  foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  throw "未找到 Google Chrome，无法打开运营管理系统页面。请安装 Google Chrome 后重试。"
}

function Open-SystemInGoogleChrome {
  param([string]$Url = $ServerUrl)

  Start-Process -FilePath (Get-GoogleChromeExecutable) -ArgumentList @($Url) | Out-Null
}

function Get-BoundedText {
  param(
    [object]$Value,
    [int]$MaximumLength = 500
  )

  $textValue = (($Value | Out-String).Trim())
  if ([string]::IsNullOrWhiteSpace($textValue)) { return $null }
  if ($textValue.Length -gt $MaximumLength) {
    return $textValue.Substring($textValue.Length - $MaximumLength, $MaximumLength)
  }
  return $textValue
}

function Write-Stage {
  param([string]$Message)

  if (-not $Json) {
    Write-Output ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
  }
}

function Write-ControlResult {
  param([object]$Result)

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

function Assert-ControllerDependencies {
  foreach ($requiredPath in @(
    $PowerShellExecutable,
    $LocalWorkerStarter,
    $DjangoService,
    $DjangoNetshopService,
    $DjangoMarketService,
    $DjangoProductsService,
    $DjangoWorkflowService,
    $DjangoInventoryService,
    $DjangoCustomerService,
    $DjangoAccessControlService,
    $DjangoErpReferenceService,
    $DjangoBiService
  )) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
      throw "缺少受控启动依赖：$requiredPath"
    }
  }
}

function Invoke-JsonServiceAction {
  param(
    [string]$ScriptPath,
    [string[]]$Arguments,
    [string]$Label
  )

  $serviceArguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath
  ) + $Arguments + @("-Json")
  $serviceOutput = & $PowerShellExecutable @serviceArguments 2>&1
  $serviceExitCode = $LASTEXITCODE
  if ($serviceExitCode -ne 0) {
    $serviceDetail = Get-BoundedText -Value $serviceOutput
    if ([string]::IsNullOrWhiteSpace($serviceDetail)) { $serviceDetail = "退出码 $serviceExitCode" }
    throw "$Label 失败：$serviceDetail"
  }
  $serviceText = (($serviceOutput | Out-String).Trim())
  if ([string]::IsNullOrWhiteSpace($serviceText)) {
    throw "$Label 未返回 JSON 状态"
  }
  try {
    return ($serviceText | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    throw "$Label 返回了无效 JSON"
  }
}

function Invoke-VisibleServiceAction {
  param(
    [string]$ScriptPath,
    [string[]]$Arguments,
    [string]$Label
  )

  $serviceArguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$ScriptPath`""
  ) + $Arguments

  # The Worker service intentionally launches a durable supervisor. Invoking it
  # through PowerShell's native pipeline can keep the caller waiting on pipe
  # handles inherited by that process tree even after the service process has
  # exited. Redirect to exact files and wait only on the direct process object.
  $invocationLogRoot = Join-Path $ProjectRoot "tmp"
  [System.IO.Directory]::CreateDirectory($invocationLogRoot) | Out-Null
  $invocationId = [Guid]::NewGuid().ToString("N")
  $serviceStdoutPath = Join-Path $invocationLogRoot "system-control-service-$invocationId.stdout.log"
  $serviceStderrPath = Join-Path $invocationLogRoot "system-control-service-$invocationId.stderr.log"
  $serviceProcess = $null
  $serviceStdout = ""
  $serviceStderr = ""
  $serviceExitCode = $null
  try {
    $serviceProcess = Start-Process -FilePath $PowerShellExecutable -ArgumentList $serviceArguments `
      -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $serviceStdoutPath `
      -RedirectStandardError $serviceStderrPath -PassThru
    $serviceProcess.WaitForExit()
    $serviceExitCode = [int]$serviceProcess.ExitCode
    if (Test-Path -LiteralPath $serviceStdoutPath -PathType Leaf) {
      try {
        $serviceStdout = [System.IO.File]::ReadAllText($serviceStdoutPath)
      } catch [System.IO.IOException] {
        # A successful durable child may retain the redirected handle. The
        # direct service exit code remains authoritative; output is optional.
      }
    }
    if (Test-Path -LiteralPath $serviceStderrPath -PathType Leaf) {
      try {
        $serviceStderr = [System.IO.File]::ReadAllText($serviceStderrPath)
      } catch [System.IO.IOException] {
        # Preserve the direct exit result and fall back to its numeric code if
        # a failed service also left an unreadable diagnostic handle.
      }
    }
  } finally {
    if ($serviceProcess) { $serviceProcess.Dispose() }
    foreach ($temporaryLog in @($serviceStdoutPath, $serviceStderrPath)) {
      if (Test-Path -LiteralPath $temporaryLog -PathType Leaf) {
        try {
          [System.IO.File]::Delete($temporaryLog)
        } catch [System.IO.IOException] {
          # A durable Worker descendant may still hold the redirected file.
          # Keep the bounded ignored diagnostic instead of turning a successful
          # system start into a false failure during best-effort cleanup.
        }
      }
    }
  }

  $serviceOutput = @($serviceStdout, $serviceStderr) | Where-Object {
    -not [string]::IsNullOrWhiteSpace([string]$_)
  }
  if (-not $Json -and $serviceOutput.Count -gt 0) {
    $serviceOutput | ForEach-Object { Write-Output (([string]$_).TrimEnd()) }
  }
  if ($null -eq $serviceExitCode -or $serviceExitCode -ne 0) {
    $serviceDetail = Get-BoundedText -Value ($serviceOutput -join [Environment]::NewLine)
    if ([string]::IsNullOrWhiteSpace($serviceDetail)) { $serviceDetail = "退出码 $serviceExitCode" }
    throw "$Label 失败：$serviceDetail"
  }
}

function Test-CoreDjangoReady {
  param([object]$Status)

  return (
    $Status -and
    [string]$Status.PostgreSQL -ceq "running" -and
    [string]$Status.DjangoReader -ceq "running" -and
    [string]$Status.DjangoWriter -ceq "running" -and
    [string]$Status.ReaderReadiness -ceq "ready" -and
    [string]$Status.WriterReadiness -ceq "ready" -and
    [string]$Status.RuntimeAcl -ceq "root_hardened" -and
    [string]$Status.RuntimeAclVerification -ceq "root_only_status"
  )
}

function Test-DjangoDomainReady {
  param(
    [object]$Status,
    [string]$ReaderProperty,
    [string]$WriterProperty,
    [string]$AuthorityProperty = ""
  )

  if (-not $Status) { return $false }
  if ([string]$Status.PSObject.Properties[$ReaderProperty].Value -cne "running" -or
      [string]$Status.PSObject.Properties[$WriterProperty].Value -cne "running" -or
      [string]$Status.ReaderReadiness -cne "ready" -or
      [string]$Status.WriterReadiness -cne "ready") {
    return $false
  }
  if (-not [string]::IsNullOrWhiteSpace($AuthorityProperty) -and
      [string]$Status.PSObject.Properties[$AuthorityProperty].Value -cne "postgres") {
    return $false
  }
  return $true
}

function Test-DjangoReaderReady {
  param([object]$Status, [string]$ReaderProperty)
  return (
    $Status -and
    [string]$Status.PSObject.Properties[$ReaderProperty].Value -ceq "running" -and
    [string]$Status.ReaderReadiness -ceq "ready"
  )
}

function Test-DjangoAggregateStatusSupported {
  if (-not (Test-Path -LiteralPath $DjangoService -PathType Leaf)) { return $false }
  $source = [System.IO.File]::ReadAllText($DjangoService)
  return $source.Contains('"AggregateStatus" { Show-AggregateServiceStatus }') -and
    $source.Contains('teruisi-django-aggregate-status-v1')
}

function Get-DjangoAggregateState {
  param([switch]$Refresh)

  $checkTime = Get-Date
  if (-not $Refresh -and $script:lastDjangoCheckAt -and $script:lastDjangoState -and
      (($checkTime - $script:lastDjangoCheckAt).TotalSeconds -lt 10)) {
    return $script:lastDjangoState
  }

  try {
    Assert-ControllerDependencies
    if (Test-DjangoAggregateStatusSupported) {
      $aggregateStatus = Invoke-JsonServiceAction -ScriptPath $DjangoService `
        -Arguments @("-Action", "AggregateStatus") -Label "Django/PostgreSQL 聚合状态检查"
      if ([string]$aggregateStatus.Version -cne "teruisi-django-aggregate-status-v1") {
        throw "Django/PostgreSQL 聚合状态版本无效"
      }
      $coreStatus = $aggregateStatus.Core
      $financeStatus = $aggregateStatus.Finance
      $netshopStatus = $aggregateStatus.Netshop
      $marketStatus = $aggregateStatus.Market
      $productsStatus = $aggregateStatus.Products
      $workflowStatus = $aggregateStatus.Workflow
      $inventoryStatus = $aggregateStatus.Inventory
      $customerServiceStatus = $aggregateStatus.CustomerService
      $accessControlStatus = $aggregateStatus.AccessControl
      $aiStatus = if ($aggregateStatus.PSObject.Properties.Name -contains "Ai") { $aggregateStatus.Ai } else { $null }
      $erpReferenceStatus = $aggregateStatus.ErpReference
      $biStatus = $aggregateStatus.Bi
    } else {
      $aiStatus = $null
      # Keep the source controller usable while a reviewed runtime deployment is
      # staged. The optimized path becomes active immediately after DeployApp.
      $coreStatus = Invoke-JsonServiceAction -ScriptPath $DjangoService -Arguments @("-Action", "Status") -Label "Django/PostgreSQL 状态检查"
      $financeStatus = Invoke-JsonServiceAction -ScriptPath $DjangoService -Arguments @("-Action", "FinanceStatus") -Label "财务域状态检查"
      $netshopStatus = Invoke-JsonServiceAction -ScriptPath $DjangoNetshopService -Arguments @("-Action", "Status") -Label "网店域状态检查"
      $marketStatus = Invoke-JsonServiceAction -ScriptPath $DjangoMarketService -Arguments @("-Action", "Status") -Label "市场域状态检查"
      $productsStatus = Invoke-JsonServiceAction -ScriptPath $DjangoProductsService -Arguments @("-Action", "Status") -Label "商品经营域状态检查"
      $workflowStatus = Invoke-JsonServiceAction -ScriptPath $DjangoWorkflowService -Arguments @("-Action", "Status") -Label "运营事务新品域状态检查"
      $inventoryStatus = Invoke-JsonServiceAction -ScriptPath $DjangoInventoryService -Arguments @("-Action", "Status") -Label "库存域状态检查"
      $customerServiceStatus = Invoke-JsonServiceAction -ScriptPath $DjangoCustomerService -Arguments @("-Action", "Status") -Label "客服域状态检查"
      $accessControlStatus = Invoke-JsonServiceAction -ScriptPath $DjangoAccessControlService -Arguments @("-Action", "Status") -Label "权限域状态检查"
      $erpReferenceStatus = Invoke-JsonServiceAction -ScriptPath $DjangoErpReferenceService -Arguments @("-Action", "Status") -Label "ERP 主数据域状态检查"
      $biStatus = Invoke-JsonServiceAction -ScriptPath $DjangoBiService -Arguments @("-Action", "Status") -Label "BI 状态检查"
    }

    $componentReadiness = [ordered]@{
      core = Test-CoreDjangoReady -Status $coreStatus
      finance = Test-DjangoDomainReady -Status $financeStatus -ReaderProperty "FinanceReader" -WriterProperty "FinanceWriter" -AuthorityProperty "PostgreSQLAuthority"
      netshop = Test-DjangoDomainReady -Status $netshopStatus -ReaderProperty "NetshopReader" -WriterProperty "NetshopWriter"
      market = Test-DjangoDomainReady -Status $marketStatus -ReaderProperty "MarketReader" -WriterProperty "MarketWriter"
      products = Test-DjangoDomainReady -Status $productsStatus -ReaderProperty "ProductsReader" -WriterProperty "ProductsWriter"
      workflow = Test-DjangoDomainReady -Status $workflowStatus -ReaderProperty "WorkflowReader" -WriterProperty "WorkflowWriter"
      inventory = Test-DjangoDomainReady -Status $inventoryStatus -ReaderProperty "InventoryReader" -WriterProperty "InventoryWriter"
      customerService = Test-DjangoDomainReady -Status $customerServiceStatus -ReaderProperty "CustomerServiceReader" -WriterProperty "CustomerServiceWriter"
      accessControl = Test-DjangoDomainReady -Status $accessControlStatus -ReaderProperty "AccessControlReader" -WriterProperty "AccessControlWriter"
      erpReference = Test-DjangoDomainReady -Status $erpReferenceStatus -ReaderProperty "ErpReferenceReader" -WriterProperty "ErpReferenceWriter"
      bi = Test-DjangoReaderReady -Status $biStatus -ReaderProperty "BiReader"
    }
    if (Test-Path -LiteralPath (Join-Path $DjangoRuntimeRoot "ai-enabled.json") -PathType Leaf) {
      $componentReadiness["ai"] = Test-DjangoDomainReady -Status $aiStatus -ReaderProperty "AiReader" -WriterProperty "AiWriter"
    }
    $notReadyComponents = @($componentReadiness.Keys | Where-Object { -not $componentReadiness[$_] })
    $aggregateState = [pscustomobject]@{
      State = if ($notReadyComponents.Count -eq 0) { "Ready" } else { "NotReady" }
      Reason = if ($notReadyComponents.Count -eq 0) { $null } else { "未就绪组件：$($notReadyComponents -join ', ')" }
      Components = [pscustomobject]$componentReadiness
      CheckedAt = (Get-Date).ToString("o")
    }
  } catch {
    $aggregateState = [pscustomobject]@{
      State = "Error"
      Reason = Get-BoundedText -Value $_.Exception.Message -MaximumLength 300
      Components = $null
      CheckedAt = (Get-Date).ToString("o")
    }
  }

  $script:lastDjangoCheckAt = $checkTime
  $script:lastDjangoState = $aggregateState
  return $aggregateState
}

function Get-WorkerReleaseStatus {
  param([switch]$Refresh)

  $checkTime = Get-Date
  if (-not $Refresh -and $script:lastWorkerCheckAt -and $script:lastWorkerStatus -and
      (($checkTime - $script:lastWorkerCheckAt).TotalSeconds -lt 10)) {
    return $script:lastWorkerStatus
  }

  try {
    if (-not (Test-Path -LiteralPath $LocalWorkerStarter -PathType Leaf)) {
      throw "缺少不可变 Worker 控制器：$LocalWorkerStarter"
    }
    $releaseStatus = Invoke-JsonServiceAction -ScriptPath $LocalWorkerStarter -Arguments @("-Action", "Status") -Label "不可变 Worker 状态检查"
    $workerStatus = [pscustomobject]@{
      State = [string]$releaseStatus.state
      Reason = [string]$releaseStatus.reason
      ReleaseId = [string]$releaseStatus.releaseId
      SupervisorProcessId = $releaseStatus.supervisorProcessId
      PortProcessId = $releaseStatus.portProcessId
      Raw = $releaseStatus
    }
  } catch {
    $workerStatus = [pscustomobject]@{
      State = "status_error"
      Reason = Get-BoundedText -Value $_.Exception.Message -MaximumLength 300
      ReleaseId = $null
      SupervisorProcessId = $null
      PortProcessId = $null
      Raw = $null
    }
  }

  $script:lastWorkerCheckAt = $checkTime
  $script:lastWorkerStatus = $workerStatus
  return $workerStatus
}

function Invoke-SystemHealthProbe {
  param(
    [string]$Uri,
    [int]$TimeoutSeconds = 3
  )

  $httpClient = $null
  $httpRequest = $null
  $httpResponse = $null
  try {
    $httpHandler = [System.Net.Http.HttpClientHandler]::new()
    $httpHandler.UseProxy = $false
    $httpClient = [System.Net.Http.HttpClient]::new($httpHandler)
    $httpClient.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    $httpRequest = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Uri)
    [void]$httpRequest.Headers.TryAddWithoutValidation("x-teruisi-local-health", "1")
    $httpResponse = $httpClient.SendAsync($httpRequest).GetAwaiter().GetResult()
    $responseContent = $httpResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    return [pscustomobject]@{
      StatusCode = [int]$httpResponse.StatusCode
      Content = $responseContent
      Error = $null
    }
  } catch {
    return [pscustomobject]@{
      StatusCode = $null
      Content = $null
      Error = Get-BoundedText -Value $_.Exception.Message -MaximumLength 200
    }
  } finally {
    if ($httpResponse) { $httpResponse.Dispose() }
    if ($httpRequest) { $httpRequest.Dispose() }
    if ($httpClient) { $httpClient.Dispose() }
  }
}

function ConvertFrom-SystemHealthContent {
  param([object]$Probe)

  if (-not $Probe -or [string]::IsNullOrWhiteSpace([string]$Probe.Content)) { return $null }
  try { return ($Probe.Content | ConvertFrom-Json -ErrorAction Stop) }
  catch { return $null }
}

function Get-SystemHealthState {
  param([switch]$Refresh)

  $checkTime = Get-Date
  if (-not $Refresh -and $script:lastHealthCheckAt -and
      (($checkTime - $script:lastHealthCheckAt).TotalSeconds -lt 5)) {
    return $script:lastHealthState
  }

  $healthState = "Unresponsive"
  $livenessProbe = Invoke-SystemHealthProbe -Uri $LivenessUrl
  $livenessPayload = ConvertFrom-SystemHealthContent -Probe $livenessProbe
  $helperProbe = Invoke-SystemHealthProbe -Uri $HelperHealthUrl
  $helperPayload = ConvertFrom-SystemHealthContent -Probe $helperProbe
  if ($livenessProbe.StatusCode -eq 200 -and $livenessPayload.ok -eq $true -and
      $livenessPayload.status -eq "live" -and $helperProbe.StatusCode -eq 200 -and
      $helperPayload.ok -eq $true) {
    $readinessProbe = Invoke-SystemHealthProbe -Uri $ReadinessUrl
    $readinessPayload = ConvertFrom-SystemHealthContent -Probe $readinessProbe
    if ($readinessProbe.StatusCode -eq 200 -and $readinessPayload.ok -eq $true -and
        $readinessPayload.status -eq "ready") {
      $healthState = "Running"
    } elseif (
      $readinessProbe.StatusCode -eq 503 -and
      $readinessPayload.ok -eq $false -and
      $readinessPayload.status -eq "degraded" -and
      $readinessPayload.code -eq "django_unavailable"
    ) {
      $healthState = "BackendDegraded"
    }
  }

  $script:lastHealthCheckAt = $checkTime
  $script:lastHealthState = $healthState
  return $healthState
}

function Get-SystemState {
  param([switch]$Refresh)

  $backendState = Get-DjangoAggregateState -Refresh:$Refresh
  $workerStatus = Get-WorkerReleaseStatus -Refresh:$Refresh
  $combinedState = "StatusError"
  $combinedReason = $null

  switch ([string]$workerStatus.State) {
    "foreign_or_ambiguous" {
      $combinedState = "PortInUse"
      $combinedReason = if ([string]::IsNullOrWhiteSpace([string]$workerStatus.Reason)) {
        "3000/5791 端口或进程归属无法安全确认"
      } else { [string]$workerStatus.Reason }
    }
    "status_error" {
      $combinedState = "StatusError"
      $combinedReason = [string]$workerStatus.Reason
    }
    "starting_exact_release" {
      $combinedState = "Starting"
      $combinedReason = "不可变 Worker 已领取启动所有权"
    }
    "stale_or_invalid_receipt" {
      $combinedState = "StaleReceipt"
      $combinedReason = "上次 Worker 未完成受控收尾；底层状态已证明端口当前没有冲突"
    }
    "stopped" {
      if ($backendState.State -eq "Error") {
        $combinedState = "StatusError"
        $combinedReason = [string]$backendState.Reason
      } elseif ($backendState.State -eq "Ready") {
        $combinedState = "WorkerStopped"
        $combinedReason = "Django/PostgreSQL 已就绪，网页 Worker 尚未运行"
      } else {
        $combinedState = "Stopped"
        $combinedReason = [string]$backendState.Reason
      }
    }
    "exact_release" {
      if ($backendState.State -eq "Error") {
        $combinedState = "StatusError"
        $combinedReason = [string]$backendState.Reason
      } elseif ($backendState.State -ne "Ready") {
        $combinedState = "BackendUnavailable"
        $combinedReason = [string]$backendState.Reason
      } else {
        $combinedState = Get-SystemHealthState -Refresh:$Refresh
        if ($combinedState -eq "Unresponsive") {
          $combinedReason = "Worker 进程归属正确，但本地存活、就绪或辅助服务探针未通过"
        }
      }
    }
    default {
      $combinedState = "StatusError"
      $combinedReason = "不可变 Worker 返回未知状态：$([string]$workerStatus.State)"
    }
  }

  return [pscustomobject]@{
    version = $UnifiedStartControlVersion
    state = $combinedState
    backendState = [string]$backendState.State
    workerState = [string]$workerStatus.State
    releaseId = $workerStatus.ReleaseId
    supervisorProcessId = $workerStatus.SupervisorProcessId
    portProcessId = $workerStatus.PortProcessId
    url = $ServerUrl
    reason = $combinedReason
    components = $backendState.Components
    checkedAt = (Get-Date).ToString("o")
  }
}

function Reset-SystemStateCache {
  $script:lastDjangoCheckAt = $null
  $script:lastDjangoState = $null
  $script:lastWorkerCheckAt = $null
  $script:lastWorkerStatus = $null
  $script:lastHealthCheckAt = $null
  $script:lastHealthState = "Unresponsive"
}

function Enter-SystemControlMutex {
  $controlMutex = [System.Threading.Mutex]::new($false, $SystemControlMutexName)
  $mutexAcquired = $false
  try {
    $mutexAcquired = $controlMutex.WaitOne([TimeSpan]::Zero)
  } catch [System.Threading.AbandonedMutexException] {
    $mutexAcquired = $true
  }
  return [pscustomobject]@{
    Mutex = $controlMutex
    Acquired = $mutexAcquired
  }
}

function Exit-SystemControlMutex {
  param([object]$MutexLease)

  if (-not $MutexLease) { return }
  try {
    if ($MutexLease.Acquired) { $MutexLease.Mutex.ReleaseMutex() }
  } finally {
    $MutexLease.Mutex.Dispose()
  }
}

function Wait-ForExactWorkerRelease {
  param([int]$MaximumSeconds = 900)

  $deadline = (Get-Date).AddSeconds($MaximumSeconds)
  do {
    Start-Sleep -Seconds 1
    $currentWorker = Get-WorkerReleaseStatus
    if ($currentWorker.State -ceq "exact_release") { return $currentWorker }
    if ($currentWorker.State -notin @("starting_exact_release", "stopped")) {
      throw "等待 Worker 启动时状态变为：$([string]$currentWorker.State)；$([string]$currentWorker.Reason)"
    }
  } while ((Get-Date) -lt $deadline)
  throw "等待不可变 Worker 就绪超过 $MaximumSeconds 秒"
}

function Invoke-SystemStart {
  $mutexLease = Enter-SystemControlMutex
  if (-not $mutexLease.Acquired) {
    Write-ControlResult ([pscustomobject]@{
      version = $UnifiedStartControlVersion
      status = "start_in_progress"
      state = "Starting"
      message = "已有唯一总控启动任务正在执行；本次请求未重复启动任何组件。"
      checkedAt = (Get-Date).ToString("o")
    })
    Exit-SystemControlMutex -MutexLease $mutexLease
    return
  }

  try {
    Assert-ControllerDependencies
    Write-Stage "唯一总控已取得启动互斥锁，开始检查完整系统状态"
    Reset-SystemStateCache
    # The canonical Worker engine performs the authoritative Django aggregate
    # preflight before it changes any process.  On a cold start, running the
    # same all-domain probe here first only repeats every stopped receipt/port
    # check and consumes a material part of the two-minute budget.  Keep the
    # fast Worker ownership gate here; retain the full combined status only for
    # the already-running no-op path and for the mandatory final verification.
    $initialWorker = Get-WorkerReleaseStatus -Refresh
    if ($initialWorker.State -notin @(
        "stopped", "starting_exact_release", "exact_release", "stale_or_invalid_receipt"
      )) {
      throw "启动前 Worker 状态不可安全推进：$([string]$initialWorker.State)；$([string]$initialWorker.Reason)"
    }
    $changed = $false
    if ($initialWorker.State -ceq "exact_release") {
      $initialState = Get-SystemState -Refresh
      if ($initialState.state -in @("PortInUse", "StatusError", "Unresponsive")) {
        throw "启动前状态不可安全推进：$($initialState.reason)"
      }
      if ($initialState.state -in @("Running", "BackendDegraded")) {
        Write-Stage "Django/PostgreSQL 全域与不可变 Worker 已经就绪，无需重复启动"
      } else {
        Write-Stage "不可变 Worker 已运行但后端未完全就绪，调用唯一启动引擎恢复后端"
        Invoke-VisibleServiceAction -ScriptPath $LocalWorkerStarter -Arguments @("-Action", "Start") -Label "运营管理系统唯一启动引擎"
        $changed = $true
      }
    } elseif ($initialWorker.State -ceq "starting_exact_release") {
      Write-Stage "不可变 Worker 已由受控入口启动，等待唯一引擎完成就绪门禁"
      [void](Wait-ForExactWorkerRelease)
    } else {
      Write-Stage "调用唯一启动引擎；它将按 Django/PostgreSQL → Worker 顺序执行，完整性校验可能需要数分钟"
      Invoke-VisibleServiceAction -ScriptPath $LocalWorkerStarter -Arguments @("-Action", "Start") -Label "运营管理系统唯一启动引擎"
      $changed = $true
    }

    Reset-SystemStateCache
    $finalState = Get-SystemState -Refresh
    if ($finalState.state -notin @("Running", "BackendDegraded")) {
      throw "启动结束但系统未达到可用状态：$($finalState.state)；$($finalState.reason)"
    }
    $pageProbe = Invoke-SystemHealthProbe -Uri $ServerUrl -TimeoutSeconds 10
    if ($pageProbe.StatusCode -ne 200) {
      throw "系统健康检查通过，但主页 HTTP 状态不是 200"
    }
    if ($Open) { Open-SystemInGoogleChrome }

    $resultStatus = if ($finalState.state -eq "BackendDegraded") {
      "started_degraded"
    } elseif ($changed) {
      "started"
    } else {
      "already_running"
    }
    $resultMessage = if ($resultStatus -eq "already_running") {
      "运营管理系统已在运行，未重复启动任何组件。访问地址：$ServerUrl"
    } elseif ($resultStatus -eq "started_degraded") {
      "系统已启动；Django 后端部分服务未就绪，正常域保持可用。访问地址：$ServerUrl"
    } else {
      "运营管理系统已由唯一总控完成启动与回查。访问地址：$ServerUrl"
    }
    Write-ControlResult ([pscustomobject]@{
      version = $UnifiedStartControlVersion
      status = $resultStatus
      state = $finalState.state
      backendState = $finalState.backendState
      workerState = $finalState.workerState
      releaseId = $finalState.releaseId
      supervisorProcessId = $finalState.supervisorProcessId
      portProcessId = $finalState.portProcessId
      url = $ServerUrl
      message = $resultMessage
      checkedAt = (Get-Date).ToString("o")
    })
  } finally {
    Exit-SystemControlMutex -MutexLease $mutexLease
  }
}

function Invoke-StopWorker {
  $mutexLease = Enter-SystemControlMutex
  if (-not $mutexLease.Acquired) {
    Exit-SystemControlMutex -MutexLease $mutexLease
    throw "唯一总控正在启动系统；为避免交错，本次暂停 Worker 请求已拒绝"
  }
  try {
    Assert-ControllerDependencies
    $stopStatus = Invoke-JsonServiceAction -ScriptPath $LocalWorkerStarter -Arguments @("-Action", "Stop") -Label "不可变 Worker 停止"
    if ([string]$stopStatus.status -notin @("stopped", "already_stopped", "stale_receipt_cleared")) {
      throw "不可变 Worker 停止回执无效"
    }
    Reset-SystemStateCache
    $stoppedState = Get-SystemState -Refresh
    Write-ControlResult ([pscustomobject]@{
      version = $UnifiedStartControlVersion
      status = [string]$stopStatus.status
      state = $stoppedState.state
      message = "网页 Worker 已通过底层身份门禁暂停；Django/PostgreSQL 后端继续运行。"
      checkedAt = (Get-Date).ToString("o")
    })
  } finally {
    Exit-SystemControlMutex -MutexLease $mutexLease
  }
}

if ($Action -ne "Panel") {
  try {
    switch ($Action) {
      "Start" { Invoke-SystemStart }
      "Status" { Write-ControlResult (Get-SystemState -Refresh) }
      "StopWorker" { Invoke-StopWorker }
    }
    exit 0
  } catch {
    $failureReason = Get-BoundedText -Value $_.Exception.Message -MaximumLength 800
    if ($Json) {
      Write-ControlResult ([pscustomobject]@{
        version = $UnifiedStartControlVersion
        status = "failed"
        state = "StatusError"
        reason = $failureReason
        message = "唯一启动总控执行失败：$failureReason"
        checkedAt = (Get-Date).ToString("o")
      })
    } else {
      Write-Error "唯一启动总控执行失败：$failureReason"
    }
    exit 1
  }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function Get-LaunchLogSummary {
  $candidateLogs = @($script:launchStderrLog, $script:launchStdoutLog) | Where-Object {
    -not [string]::IsNullOrWhiteSpace([string]$_) -and (Test-Path -LiteralPath $_ -PathType Leaf)
  }
  foreach ($candidateLog in $candidateLogs) {
    try {
      $summary = ((Get-Content -LiteralPath $candidateLog -Tail 4 -ErrorAction Stop) -join " ").Trim()
      if ([string]::IsNullOrWhiteSpace($summary)) { continue }
      if ($summary.Length -gt 260) { return $summary.Substring($summary.Length - 260, 260) }
      return $summary
    } catch {}
  }
  return $null
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "运营管理系统唯一启动总控"
$form.StartPosition = "CenterScreen"
$form.ClientSize = New-Object System.Drawing.Size(470, 315)
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(247, 249, 252)

$title = New-Object System.Windows.Forms.Label
$title.Text = "运营管理系统"
$title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 18, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(28, 22)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "唯一启动总控 · Django/PostgreSQL → Worker"
$subtitle.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(92, 102, 117)
$subtitle.AutoSize = $true
$subtitle.Location = New-Object System.Drawing.Point(30, 62)

$statusDot = New-Object System.Windows.Forms.Label
$statusDot.Text = "●"
$statusDot.Font = New-Object System.Drawing.Font("Segoe UI", 18)
$statusDot.AutoSize = $true
$statusDot.Location = New-Object System.Drawing.Point(29, 102)

$status = New-Object System.Windows.Forms.Label
$status.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 11, [System.Drawing.FontStyle]::Bold)
$status.AutoSize = $true
$status.Location = New-Object System.Drawing.Point(57, 105)

$details = New-Object System.Windows.Forms.Label
$details.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
$details.ForeColor = [System.Drawing.Color]::FromArgb(92, 102, 117)
$details.Location = New-Object System.Drawing.Point(31, 143)
$details.Size = New-Object System.Drawing.Size(410, 48)

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = "启动系统"
$startButton.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10, [System.Drawing.FontStyle]::Bold)
$startButton.Size = New-Object System.Drawing.Size(125, 42)
$startButton.Location = New-Object System.Drawing.Point(31, 202)
$startButton.BackColor = [System.Drawing.Color]::FromArgb(28, 118, 235)
$startButton.ForeColor = [System.Drawing.Color]::White
$startButton.FlatStyle = "Flat"
$startButton.FlatAppearance.BorderSize = 0

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Text = "暂停网页服务"
$stopButton.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)
$stopButton.Size = New-Object System.Drawing.Size(125, 42)
$stopButton.Location = New-Object System.Drawing.Point(172, 202)

$openButton = New-Object System.Windows.Forms.Button
$openButton.Text = "打开系统"
$openButton.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)
$openButton.Size = New-Object System.Drawing.Size(125, 42)
$openButton.Location = New-Object System.Drawing.Point(313, 202)

$runContinueButton = New-Object System.Windows.Forms.Button
$runContinueButton.Text = "执行待处理继续任务"
$runContinueButton.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
$runContinueButton.Size = New-Object System.Drawing.Size(190, 34)
$runContinueButton.Location = New-Object System.Drawing.Point(31, 260)

$viewLogButton = New-Object System.Windows.Forms.Button
$viewLogButton.Text = "查看启动日志"
$viewLogButton.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
$viewLogButton.Size = New-Object System.Drawing.Size(190, 34)
$viewLogButton.Location = New-Object System.Drawing.Point(248, 260)

$form.Controls.AddRange(@(
  $title, $subtitle, $statusDot, $status, $details,
  $startButton, $stopButton, $openButton, $runContinueButton, $viewLogButton
))

function Set-UiState {
  param(
    [System.Drawing.Color]$Color,
    [string]$Title,
    [string]$Detail,
    [bool]$CanStart,
    [bool]$CanStop,
    [bool]$CanOpen,
    [bool]$CanContinue
  )

  $statusDot.ForeColor = $Color
  $status.Text = $Title
  $details.Text = $Detail
  $startButton.Enabled = $CanStart
  $stopButton.Enabled = $CanStop
  $openButton.Enabled = $CanOpen
  $runContinueButton.Enabled = $CanContinue
}

function Update-Status {
  if ($script:launchProcess -and -not $script:launchProcess.HasExited) {
    $elapsedSeconds = [math]::Floor(((Get-Date) - $script:launchStartedAt).TotalSeconds)
    $progressSummary = Get-LaunchLogSummary
    $progressDetail = if ($progressSummary) {
      "$progressSummary（已等待 $elapsedSeconds 秒）"
    } else {
      "总控正在执行分阶段启动与回查（已等待 $elapsedSeconds 秒）。"
    }
    Set-UiState -Color ([System.Drawing.Color]::FromArgb(28, 118, 235)) -Title "系统正在启动…" -Detail $progressDetail -CanStart $false -CanStop $false -CanOpen $false -CanContinue $false
    return
  }

  if ($script:launchProcess -and $script:launchProcess.HasExited) {
    $launchExitCode = $null
    try {
      $script:launchProcess.WaitForExit()
      $script:launchProcess.Refresh()
      $launchExitCode = [int]$script:launchProcess.ExitCode
    } catch {}
    if ($null -eq $launchExitCode -or $launchExitCode -ne 0) {
      $failureSummary = Get-LaunchLogSummary
      $failureDetail = if ($failureSummary) {
        "$failureSummary。点击【查看启动日志】可查看完整记录。"
      } else {
        "启动进程未返回可用错误详情，请查看启动日志。"
      }
      $failureTitle = if ($null -eq $launchExitCode) { "启动失败" } else { "启动失败，退出码 $launchExitCode" }
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(202, 64, 64)) -Title $failureTitle -Detail $failureDetail -CanStart $true -CanStop $false -CanOpen $false -CanContinue $false
      return
    }
    $script:launchProcess = $null
    Reset-SystemStateCache
  }

  $systemState = Get-SystemState
  switch ([string]$systemState.state) {
    "Running" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(28, 160, 88)) -Title "完整系统正在运行" -Detail "全部 Django/PostgreSQL 域与不可变 Worker 已就绪。访问地址：$ServerUrl" -CanStart $false -CanStop $true -CanOpen $true -CanContinue $true
    }
    "BackendDegraded" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(224, 133, 27)) -Title "Django 后端部分服务未就绪" -Detail "正常域与 Worker 保持运行；不会因就绪检查失败自动重启。" -CanStart $false -CanStop $true -CanOpen $false -CanContinue $false
    }
    "Starting" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(28, 118, 235)) -Title "Worker 正在启动…" -Detail "底层不可变 Worker 已领取所有权，总控不会创建第二个实例。" -CanStart $false -CanStop $false -CanOpen $false -CanContinue $false
    }
    "WorkerStopped" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(148, 158, 173)) -Title "网页 Worker 已暂停" -Detail "Django/PostgreSQL 后端已经就绪；点击【启动系统】恢复网页服务。" -CanStart $true -CanStop $false -CanOpen $false -CanContinue $false
    }
    "Stopped" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(148, 158, 173)) -Title "系统尚未完整启动" -Detail "点击【启动系统】，总控将按后端 → Worker 顺序启动并回查。" -CanStart $true -CanStop $false -CanOpen $false -CanContinue $false
    }
    "BackendUnavailable" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(224, 133, 27)) -Title "后端服务未全部就绪" -Detail "$($systemState.reason)。点击【启动系统】执行受控补齐。" -CanStart $true -CanStop $true -CanOpen $false -CanContinue $false
    }
    "StaleReceipt" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(224, 133, 27)) -Title "检测到 Worker 异常退出" -Detail "点击【启动系统】后，仅在底层再次验证安全时清理陈旧回执。" -CanStart $true -CanStop $true -CanOpen $false -CanContinue $false
    }
    "Unresponsive" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(202, 64, 64)) -Title "Worker 无响应或重启中" -Detail "$($systemState.reason)。总控不会自动接管或强制重启。" -CanStart $false -CanStop $true -CanOpen $false -CanContinue $false
    }
    "PortInUse" {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(224, 133, 27)) -Title "端口或进程归属冲突" -Detail "$($systemState.reason)。为避免影响其他程序，总控拒绝接管。" -CanStart $false -CanStop $false -CanOpen $false -CanContinue $false
    }
    default {
      Set-UiState -Color ([System.Drawing.Color]::FromArgb(202, 64, 64)) -Title "无法确认完整系统状态" -Detail "$($systemState.reason)" -CanStart $false -CanStop $false -CanOpen $false -CanContinue $false
    }
  }
}

$startButton.Add_Click({
  $currentState = Get-SystemState -Refresh
  if ($currentState.state -in @("Running", "BackendDegraded", "Starting")) {
    Update-Status
    return
  }
  if ($currentState.state -in @("PortInUse", "StatusError", "Unresponsive")) {
    [System.Windows.Forms.MessageBox]::Show(
      "$($currentState.reason)。本次启动已取消。",
      "无法安全启动",
      "OK",
      "Warning"
    ) | Out-Null
    Update-Status
    return
  }

  $logDirectory = Join-Path $ProjectRoot "tmp"
  New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
  $logStamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $script:launchStdoutLog = Join-Path $logDirectory "system-control-$logStamp.stdout.log"
  $script:launchStderrLog = Join-Path $logDirectory "system-control-$logStamp.stderr.log"
  $script:launchStartedAt = Get-Date
  $script:launchProcess = Start-Process -FilePath $PowerShellExecutable -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$ControllerPath`"", "-Action", "Start"
  ) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $script:launchStdoutLog -RedirectStandardError $script:launchStderrLog -PassThru
  Update-Status
})

$stopButton.Add_Click({
  if ($script:launchProcess -and -not $script:launchProcess.HasExited) {
    [System.Windows.Forms.MessageBox]::Show(
      "唯一总控正在执行启动门禁。为避免启动与停止交错，请等待本轮结束。",
      "启动仍在进行",
      "OK",
      "Information"
    ) | Out-Null
    return
  }
  try {
    Invoke-StopWorker
  } catch {
    [System.Windows.Forms.MessageBox]::Show(
      "$(Get-BoundedText -Value $_.Exception.Message -MaximumLength 500)",
      "无法安全暂停 Worker",
      "OK",
      "Warning"
    ) | Out-Null
  }
  Reset-SystemStateCache
  Update-Status
})

$openButton.Add_Click({
  if ((Get-SystemState -Refresh).state -eq "Running") {
    Open-SystemInGoogleChrome
  } else {
    Update-Status
  }
})

$runContinueButton.Add_Click({
  $nodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
  if (-not $nodeCommand) { $nodeCommand = Get-Command "node" -ErrorAction SilentlyContinue }
  if (-not $nodeCommand) {
    [System.Windows.Forms.MessageBox]::Show("未找到 Node.js。", "无法执行", "OK", "Error") | Out-Null
    return
  }
  $continueHelper = Join-Path $ProjectRoot "tools\jackyun-continue-helper.ts"
  if (-not (Test-Path -LiteralPath $continueHelper -PathType Leaf)) {
    [System.Windows.Forms.MessageBox]::Show("未找到继续任务脚本。", "无法执行", "OK", "Error") | Out-Null
    return
  }
  Start-Process -FilePath $nodeCommand.Source -ArgumentList @("--import", "tsx", $continueHelper) -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
})

$viewLogButton.Add_Click({
  $logPath = if ($script:launchStderrLog -and (Test-Path -LiteralPath $script:launchStderrLog) -and
      (Get-Item -LiteralPath $script:launchStderrLog).Length -gt 0) {
    $script:launchStderrLog
  } else { $script:launchStdoutLog }
  if (-not $logPath -or -not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
    [System.Windows.Forms.MessageBox]::Show("尚未生成启动日志。", "暂无日志", "OK", "Information") | Out-Null
    return
  }
  Start-Process -FilePath "notepad.exe" -ArgumentList @("`"$logPath`"") | Out-Null
})

$statusTimer = New-Object System.Windows.Forms.Timer
$statusTimer.Interval = 3000
$statusTimer.Add_Tick({ Update-Status })
$form.Add_Shown({
  Update-Status
  if ($Open -and (Get-SystemState).state -eq "Running") { Open-SystemInGoogleChrome }
  $statusTimer.Start()
})
$form.Add_FormClosed({ $statusTimer.Stop() })

[void]$form.ShowDialog()
