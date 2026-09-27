param(
  [ValidateSet("Deploy", "Verify", "Start", "Restart", "RestartFull", "Stop", "Status", "InstallStartup", "VerifyStartup", "RemoveStartup", "EnterMaintenance", "ExitMaintenance", "MaintenanceStatus")]
  [string]$Action = "Status",
  [string]$SourceRoot,
  [string]$RuntimeRoot = "D:\teruisi-runtime\teruisi-worker-sales",
  [string]$ManifestPath,
  [string]$DevVarsSource,
  [string]$PersistRoot,
  [string]$SourceD1Path,
  [string]$StartupShortcutPath,
  [switch]$Json,
  [switch]$AllowTestRuntimeRoot,
  [switch]$IncludeBackend,
  [switch]$KeepPostgres,
  [string]$MaintenanceId,
  [switch]$FunctionsOnly
)

$ErrorActionPreference = "Stop"
$FixedRuntimeRoot = "D:\teruisi-runtime\teruisi-worker-sales"
$FixedSourceRoot = "D:\运营管理系统-sales-django-release"
$FixedProtectedRoot = "D:\运营管理系统"
$FixedDevVarsSource = "D:\运营管理系统\.dev.vars"
$FixedPersistRoot = "D:\运营管理系统\.wrangler\state"
$FixedDjangoRuntimeRoot = "D:\teruisi-runtime\django-sales"
$DjangoRuntimeTools = Join-Path $FixedDjangoRuntimeRoot "app\tools"
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
$WorkerPort = 3000
$WorkerHost = "127.0.0.1"
$HelperPort = 5791
$HelperHost = "127.0.0.1"
$StatusVersion = "teruisi-local-worker-status-v1"
$ProcessReceiptVersion = "teruisi-local-worker-process-v1"
$StartupOwnershipGraceMilliseconds = 3000
$HotRestartBudgetMilliseconds = 30000
$ReleaseTool = Join-Path $PSScriptRoot "worker-local-release.mjs"
$RotationTool = Join-Path $PSScriptRoot "worker-local-release-rotation.mjs"
$StartupShortcut = if ([string]::IsNullOrWhiteSpace($StartupShortcutPath)) {
  Join-Path ([Environment]::GetFolderPath("Startup")) "TERUISI Operations Worker.lnk"
} else {
  if (-not $AllowTestRuntimeRoot) { throw "StartupShortcutPath is test-only" }
  if (-not [System.IO.Path]::IsPathRooted($StartupShortcutPath)) { throw "StartupShortcutPath must be absolute" }
  [System.IO.Path]::GetFullPath($StartupShortcutPath)
}
$MutatingActions = @("Deploy", "Start", "Restart", "RestartFull", "Stop", "InstallStartup", "RemoveStartup", "EnterMaintenance", "ExitMaintenance")
$ServiceMutex = $null

if (-not ("Teruisi.NativeCommandLine" -as [type])) {
  Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace Teruisi {
  public static class NativeCommandLine {
    [DllImport("shell32.dll", SetLastError = true)]
    private static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string commandLine, out int argc);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr LocalFree(IntPtr value);
    public static string[] Parse(string commandLine) {
      int argc;
      IntPtr argv = CommandLineToArgvW(commandLine, out argc);
      if (argv == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
      try {
        var result = new List<string>(argc);
        for (int i = 0; i < argc; i++) {
          IntPtr item = Marshal.ReadIntPtr(argv, i * IntPtr.Size);
          result.Add(Marshal.PtrToStringUni(item));
        }
        return result.ToArray();
      } finally { LocalFree(argv); }
    }
  }
}
"@
}

function Get-WorkerServiceMutexName {
  $name = "Local\TERUISI.Worker.LocalService.v1"
  if ($AllowTestRuntimeRoot -and [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') -ine $FixedRuntimeRoot) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $name += ".test." + ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($RuntimeRoot).ToLowerInvariant())))).Replace("-", "") }
    finally { $hash.Dispose() }
  }
  return $name
}

function Enter-WorkerServiceMutex([ValidateRange(0, 900)][int]$StartWaitSeconds = 900) {
  $mutex = [System.Threading.Mutex]::new($false, (Get-WorkerServiceMutexName))
  $acquired = $false
  $joined = $false
  try {
    try {
      $acquired = $mutex.WaitOne([TimeSpan]::Zero)
      if (-not $acquired -and $Action -ceq "Start") {
        $joined = $true
        if (-not $Json) { Write-Host "An existing lifecycle operation is running; waiting to verify its result" }
        $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($StartWaitSeconds))
      }
    } catch [System.Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { throw "Another Worker/system lifecycle operation is in progress; request rejected" }
    return [pscustomobject]@{ Mutex = $mutex; Joined = $joined }
  } catch {
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    throw
  }
}

$JoinedConcurrentLifecycle = $false
if (-not $FunctionsOnly -and $Action -in $MutatingActions) {
  $lease = Enter-WorkerServiceMutex
  $ServiceMutex = $lease.Mutex
  $lockAcquired = $true
  $JoinedConcurrentLifecycle = $lease.Joined
}

function Assert-FixedRuntimeRoot {
  if ($AllowTestRuntimeRoot) { return }
  $actual = [System.IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
  $expected = [System.IO.Path]::GetFullPath($FixedRuntimeRoot).TrimEnd('\')
  if (-not $actual.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Worker runtime root must remain fixed at $FixedRuntimeRoot"
  }
}

function Get-NodeExecutable {
  $node = Get-Command "node.exe" -ErrorAction SilentlyContinue
  if (-not $node) { $node = Get-Command "node" -ErrorAction SilentlyContinue }
  if (-not $node) { throw "Node.js 24.x is required" }
  return $node.Source
}

function Get-DjangoControlPowerShell {
  $djangoPowerShell = Get-Command "pwsh.exe" -ErrorAction SilentlyContinue
  if (-not $djangoPowerShell) { $djangoPowerShell = Get-Command "pwsh" -ErrorAction SilentlyContinue }
  if (-not $djangoPowerShell) {
    throw "PowerShell 7 is required for the installed Django runtime controller"
  }
  return $djangoPowerShell.Source
}

function Invoke-DjangoStatusJson([string]$ScriptPath, [string]$StatusAction, [string]$Label) {
  if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    throw "Missing installed Django controller for ${Label}: $ScriptPath"
  }
  $djangoOutput = @(& (Get-DjangoControlPowerShell) -NoProfile -File $ScriptPath -Action $StatusAction -Json 2>&1)
  $djangoExitCode = $LASTEXITCODE
  $djangoText = (($djangoOutput | ForEach-Object { $_.ToString() }) -join "`n").Trim()
  if ($djangoExitCode -ne 0) {
    if ($djangoText.Length -gt 500) { $djangoText = $djangoText.Substring($djangoText.Length - 500, 500) }
    throw "$Label failed: exit=$djangoExitCode; $djangoText"
  }
  try { return ($djangoText | ConvertFrom-Json -ErrorAction Stop) }
  catch { throw "$Label did not return valid JSON" }
}

function Invoke-DjangoStartProcess(
  [string]$Controller = $DjangoService,
  [ValidateSet("Start", "Stop", "AutoStartDingTalk")][string]$ControlAction = "Start",
  [switch]$PreservePostgres
) {
  if (-not (Test-Path -LiteralPath $Controller -PathType Leaf)) {
    throw "Missing installed Django controller: $Controller"
  }

  # Django Start creates durable PostgreSQL, Waitress, and ERP descendants.
  # A native PowerShell pipeline can keep waiting on stdout/stderr handles that
  # those descendants inherited after the direct controller has exited. Use
  # file redirection and make the direct process exit code authoritative.
  $invocationLogRoot = Join-Path $RuntimeRoot "logs"
  [System.IO.Directory]::CreateDirectory($invocationLogRoot) | Out-Null
  $invocationId = [Guid]::NewGuid().ToString("N")
  $stdoutPath = Join-Path $invocationLogRoot "django-start-$invocationId.stdout.log"
  $stderrPath = Join-Path $invocationLogRoot "django-start-$invocationId.stderr.log"
  $arguments = @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", "`"$Controller`"", "-Action", $ControlAction,
    "-RuntimeRoot", "`"$FixedDjangoRuntimeRoot`""
  )
  if ($PreservePostgres) { $arguments += "-KeepPostgres" }
  $process = $null
  $exitCode = $null
  $stdoutTail = $null
  $stderrTail = $null
  try {
    $process = Start-Process -FilePath (Get-DjangoControlPowerShell) -ArgumentList $arguments `
      -WorkingDirectory $DjangoRuntimeTools -WindowStyle Hidden `
      -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $process.WaitForExit()
    $exitCode = [int]$process.ExitCode
    $stdoutTail = Get-BoundedLogTail $stdoutPath 800
    $stderrTail = Get-BoundedLogTail $stderrPath 800
  } finally {
    if ($process) { $process.Dispose() }
    foreach ($temporaryLog in @($stdoutPath, $stderrPath)) {
      if (-not (Test-Path -LiteralPath $temporaryLog -PathType Leaf)) { continue }
      try {
        [System.IO.File]::Delete($temporaryLog)
      } catch [System.IO.IOException] {
        # A durable descendant may still hold the redirected handle. Leaving
        # this bounded diagnostic is safer than converting a successful Start
        # into a false failure.
      } catch [System.UnauthorizedAccessException] {
        # Runtime ACLs can also keep best-effort diagnostic cleanup pending.
      }
    }
  }

  return [pscustomobject]@{
    ExitCode = $exitCode
    StdoutTail = $stdoutTail
    StderrTail = $stderrTail
  }
}

function Start-SystemDingTalkReceiver {
  if (Test-IsIsolatedTestRuntime) { return }
  $startup = Join-Path $FixedDjangoRuntimeRoot "config\dingtalk-startup.json"
  if (-not (Test-Path -LiteralPath $startup)) { return }
  $controller = Join-Path $DjangoRuntimeTools "django-ai.ps1"
  Assert-NoReparsePath $startup
  Assert-NoReparsePath $controller
  $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3000/" -TimeoutSec 10 -MaximumRedirection 0
  if ($page.StatusCode -ne 200) { throw "Worker HTTP is not ready for DingTalk automatic startup" }
  # The deployed AI operator owns configuration, authority, mutex and exact process identity.
  # Do not start from Django's pre-Worker phase: scheduled tools require the ready Worker.
  $result = Invoke-DjangoStartProcess $controller "AutoStartDingTalk"
  if ([int]$result.ExitCode -ne 0) {
    throw "System services are running, but configured DingTalk receiver startup failed; check the AI runtime logs."
  }
  if (-not $Json) { Write-Host "DingTalk automatic startup configuration checked by the AI runtime controller" }
}

function Test-DjangoDomainReady(
  [object]$Status,
  [string]$ReaderProperty,
  [string]$WriterProperty,
  [string]$AuthorityProperty = ""
) {
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

function Test-DjangoReaderReady([object]$Status, [string]$ReaderProperty) {
  return (
    $Status -and
    [string]$Status.PSObject.Properties[$ReaderProperty].Value -ceq "running" -and
    [string]$Status.ReaderReadiness -ceq "ready"
  )
}

function Test-IsIsolatedTestRuntime {
  if (-not $AllowTestRuntimeRoot) { return $false }
  $actualRuntime = [System.IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
  $productionRuntime = [System.IO.Path]::GetFullPath($FixedRuntimeRoot).TrimEnd('\')
  return -not $actualRuntime.Equals($productionRuntime, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-DjangoAggregateStatusSupported {
  if (-not (Test-Path -LiteralPath $DjangoService -PathType Leaf)) { return $false }
  Assert-NoReparsePath $DjangoService
  $source = [System.IO.File]::ReadAllText($DjangoService)
  return $source.Contains('"AggregateStatus" { Show-AggregateServiceStatus }') -and
    $source.Contains('teruisi-django-aggregate-status-v1')
}

function Get-DjangoSystemReadiness {
  if (Test-IsIsolatedTestRuntime) {
    return [pscustomobject]@{ Ready = $true; Missing = @() }
  }

  if (Test-DjangoAggregateStatusSupported) {
    $aggregateStatus = Invoke-DjangoStatusJson `
      $DjangoService "AggregateStatus" "Django/PostgreSQL aggregate status"
    if ([string]$aggregateStatus.Version -cne "teruisi-django-aggregate-status-v1") {
      throw "Django/PostgreSQL aggregate status version is invalid"
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
    # Safe rolling-upgrade compatibility. Once the new runtime app is deployed,
    # the aggregate branch above is mandatory for the startup performance target.
    $coreStatus = Invoke-DjangoStatusJson $DjangoService "Status" "Django/PostgreSQL status"
    $financeStatus = Invoke-DjangoStatusJson $DjangoService "FinanceStatus" "Django finance status"
    $netshopStatus = Invoke-DjangoStatusJson $DjangoNetshopService "Status" "Django netshop status"
    $marketStatus = Invoke-DjangoStatusJson $DjangoMarketService "Status" "Django market status"
    $productsStatus = Invoke-DjangoStatusJson $DjangoProductsService "Status" "Django products status"
    $workflowStatus = Invoke-DjangoStatusJson $DjangoWorkflowService "Status" "Django workflow status"
    $inventoryStatus = Invoke-DjangoStatusJson $DjangoInventoryService "Status" "Django inventory status"
    $customerServiceStatus = Invoke-DjangoStatusJson $DjangoCustomerService "Status" "Django customer-service status"
    $accessControlStatus = Invoke-DjangoStatusJson $DjangoAccessControlService "Status" "Django access-control status"
    $erpReferenceStatus = Invoke-DjangoStatusJson $DjangoErpReferenceService "Status" "Django ERP reference status"
    $biStatus = Invoke-DjangoStatusJson $DjangoBiService "Status" "Django BI status"
  }

  $checks = [ordered]@{
    core = (
      [string]$coreStatus.PostgreSQL -ceq "running" -and
      [string]$coreStatus.DjangoReader -ceq "running" -and
      [string]$coreStatus.DjangoWriter -ceq "running" -and
      [string]$coreStatus.ReaderReadiness -ceq "ready" -and
      [string]$coreStatus.WriterReadiness -ceq "ready" -and
      [string]$coreStatus.RuntimeAcl -ceq "root_hardened" -and
      [string]$coreStatus.RuntimeAclVerification -ceq "root_only_status"
    )
    finance = Test-DjangoDomainReady $financeStatus "FinanceReader" "FinanceWriter" "PostgreSQLAuthority"
    netshop = Test-DjangoDomainReady $netshopStatus "NetshopReader" "NetshopWriter"
    market = Test-DjangoDomainReady $marketStatus "MarketReader" "MarketWriter"
    products = Test-DjangoDomainReady $productsStatus "ProductsReader" "ProductsWriter"
    workflow = Test-DjangoDomainReady $workflowStatus "WorkflowReader" "WorkflowWriter"
    inventory = Test-DjangoDomainReady $inventoryStatus "InventoryReader" "InventoryWriter"
    customerService = Test-DjangoDomainReady $customerServiceStatus "CustomerServiceReader" "CustomerServiceWriter"
    accessControl = Test-DjangoDomainReady $accessControlStatus "AccessControlReader" "AccessControlWriter"
    erpReference = Test-DjangoDomainReady $erpReferenceStatus "ErpReferenceReader" "ErpReferenceWriter"
    bi = Test-DjangoReaderReady $biStatus "BiReader"
  }
  if (Test-Path -LiteralPath (Join-Path $FixedDjangoRuntimeRoot "ai-enabled.json")) {
    if (-not (Test-DjangoAggregateStatusSupported)) { throw "AI authority requires current aggregate readiness" }
    $checks["ai"] = ($null -ne $aiStatus -and (Test-DjangoDomainReady $aiStatus "AiReader" "AiWriter"))
  }
  $missing = @($checks.Keys | Where-Object { -not $checks[$_] })
  return [pscustomobject]@{
    Ready = ($missing.Count -eq 0)
    Missing = $missing
  }
}

function Ensure-DjangoSystemReady {
  if (Test-IsIsolatedTestRuntime) { return }

  $readiness = Get-DjangoSystemReadiness
  if ($readiness.Ready) {
    if (-not $Json) { Write-Host "Django/PostgreSQL full stack is already ready" }
    return
  }
  if (-not $Json) {
    Write-Host "Starting Django/PostgreSQL full stack; missing=$($readiness.Missing -join ',')"
  }
  $djangoStart = Invoke-DjangoStartProcess
  $djangoStartExitCode = $djangoStart.ExitCode
  $djangoStartText = (@(
    [string]$djangoStart.StdoutTail,
    [string]$djangoStart.StderrTail
  ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join "`n"
  if (-not $Json -and -not [string]::IsNullOrWhiteSpace($djangoStartText)) {
    Write-Host $djangoStartText
  }
  if ($djangoStartExitCode -ne 0) {
    if ([string]::IsNullOrWhiteSpace($djangoStartText)) { $djangoStartText = "no readable diagnostic" }
    throw "Django/PostgreSQL full start failed: exit=$djangoStartExitCode; $djangoStartText"
  }
  # The installed Start controller does not exit successfully until every
  # enabled reader/writer, including ERP reference, has passed its own bounded
  # readiness gate. Re-running seven heavyweight Status controllers here only
  # regenerates the same evidence and can add minutes of process startup cost.
}

function Assert-NoReparsePath([string]$Path, [switch]$AllowMissingLeaf) {
  $absolute = [System.IO.Path]::GetFullPath($Path)
  $root = [System.IO.Path]::GetPathRoot($absolute)
  $relative = $absolute.Substring($root.Length)
  $current = $root
  $parts = @($relative.Split(@('\', '/'), [System.StringSplitOptions]::RemoveEmptyEntries))
  for ($index = 0; $index -lt $parts.Count; $index++) {
    $current = Join-Path $current $parts[$index]
    if (-not (Test-Path -LiteralPath $current)) {
      if ($AllowMissingLeaf) { return }
      throw "Path component is missing: $current"
    }
    $item = Get-Item -LiteralPath $current -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "Reparse points are forbidden in controlled Worker paths: $current"
    }
  }
}

function Assert-EntityDirectory([string]$Path) {
  Assert-NoReparsePath $Path
  $item = Get-Item -LiteralPath $Path -Force
  if (-not $item.PSIsContainer) { throw "Controlled Worker path is not a directory: $Path" }
}

function ConvertFrom-ExactJson([string]$InputJson, [string]$Label) {
  try {
    $command = Get-Command ConvertFrom-Json -CommandType Cmdlet -ErrorAction Stop
    if ($command.Parameters.ContainsKey("DateKind")) {
      return ConvertFrom-Json -InputObject $InputJson -DateKind String -ErrorAction Stop
    }
    return ConvertFrom-Json -InputObject $InputJson -ErrorAction Stop
  } catch {
    throw "$Label is not valid JSON"
  }
}

function ConvertTo-CanonicalJson([object]$Value) {
  if ($null -eq $Value) { return "null" }
  if ($Value -is [string] -or $Value -is [char]) { return ($Value.ToString() | ConvertTo-Json -Compress) }
  if ($Value -is [bool]) { return $(if ($Value) { "true" } else { "false" }) }
  if ($Value -is [byte] -or $Value -is [sbyte] -or $Value -is [int16] -or $Value -is [uint16] -or
      $Value -is [int32] -or $Value -is [uint32] -or $Value -is [int64] -or $Value -is [uint64] -or
      $Value -is [single] -or $Value -is [double] -or $Value -is [decimal]) {
    return [System.Convert]::ToString($Value, [System.Globalization.CultureInfo]::InvariantCulture)
  }
  if ($Value -is [System.Collections.IDictionary]) {
    $names = [string[]]@($Value.Keys | ForEach-Object { [string]$_ })
    [System.Array]::Sort($names, [System.StringComparer]::Ordinal)
    return "{" + (($names | ForEach-Object { (ConvertTo-CanonicalJson $_) + ":" + (ConvertTo-CanonicalJson $Value[$_]) }) -join ",") + "}"
  }
  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [pscustomobject])) {
    return "[" + ((@($Value) | ForEach-Object { ConvertTo-CanonicalJson $_ }) -join ",") + "]"
  }
  $properties = @($Value.PSObject.Properties | Where-Object { $_.MemberType -in @("NoteProperty", "Property", "AliasProperty") })
  $propertyNames = [string[]]@($properties | ForEach-Object { [string]$_.Name })
  [System.Array]::Sort($propertyNames, [System.StringComparer]::Ordinal)
  return "{" + (($propertyNames | ForEach-Object {
    $propertyName = $_
    (ConvertTo-CanonicalJson $propertyName) + ":" + (ConvertTo-CanonicalJson ($Value.PSObject.Properties[$propertyName].Value))
  }) -join ",") + "}"
}

function Assert-CanonicalJsonPayload([byte[]]$Raw, [object]$Value, [string]$PayloadField, [string]$Label) {
  if ($Raw.Length -eq 0 -or ($Raw.Length -ge 3 -and $Raw[0] -eq 0xEF -and $Raw[1] -eq 0xBB -and $Raw[2] -eq 0xBF)) {
    throw "$Label must be UTF-8 without BOM"
  }
  $canonical = ConvertTo-CanonicalJson $Value
  $expectedRaw = [System.Text.UTF8Encoding]::new($false).GetBytes($canonical + "`n")
  if (-not [System.Linq.Enumerable]::SequenceEqual([byte[]]$Raw, [byte[]]$expectedRaw)) {
    $limit = [System.Math]::Min($Raw.Length, $expectedRaw.Length)
    $difference = 0
    while ($difference -lt $limit -and $Raw[$difference] -eq $expectedRaw[$difference]) { $difference++ }
    $actualByte = if ($difference -lt $Raw.Length) { [int]$Raw[$difference] } else { -1 }
    $expectedByte = if ($difference -lt $expectedRaw.Length) { [int]$expectedRaw[$difference] } else { -1 }
    throw "$Label is not canonical JSON at byte $difference (actual=$actualByte expected=$expectedByte actualLength=$($Raw.Length) expectedLength=$($expectedRaw.Length))"
  }
  $core = [ordered]@{}
  foreach ($property in $Value.PSObject.Properties) {
    if ($property.Name -cne $PayloadField) { $core[$property.Name] = $property.Value }
  }
  $actualPayloadSha256 = [string]$Value.PSObject.Properties[$PayloadField].Value
  $expectedPayloadSha256 = Get-Sha256Bytes ([System.Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $core)))
  if ($actualPayloadSha256 -cne $expectedPayloadSha256) {
    throw "$Label self hash is invalid"
  }
}

function Get-Sha256Bytes([byte[]]$Bytes) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try { return ([System.BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant() }
  finally { $sha.Dispose() }
}

function Get-Sha256File([string]$Path) {
  return Get-Sha256Bytes ([System.IO.File]::ReadAllBytes($Path))
}

function Get-CanonicalPathSha256([string]$Path) {
  $value = [System.IO.Path]::GetFullPath($Path).Replace('/', '\')
  if ($value.Length -gt 3) { $value = $value.TrimEnd('\') }
  $value = $value.ToUpperInvariant()
  return Get-Sha256Bytes ([System.Text.Encoding]::UTF8.GetBytes($value))
}

function Assert-ExactProperties([object]$Value, [string[]]$Names, [string]$Label) {
  if (-not $Value) { throw "$Label is missing" }
  $actual = @($Value.PSObject.Properties.Name | Sort-Object)
  $expected = @($Names | Sort-Object)
  if (($actual -join "`n") -cne ($expected -join "`n")) { throw "$Label has an invalid property set" }
}

function Test-CommandLineContainsExactPath([string]$CommandLine, [string]$ExpectedPath) {
  if ([string]::IsNullOrWhiteSpace($CommandLine) -or [string]::IsNullOrWhiteSpace($ExpectedPath)) { return $false }
  $escaped = [System.Text.RegularExpressions.Regex]::Escape([System.IO.Path]::GetFullPath($ExpectedPath))
  return [System.Text.RegularExpressions.Regex]::IsMatch($CommandLine, '(?i)(?:^|\s)(?:"' + $escaped + '"|' + $escaped + ')(?=\s|$)')
}

function Get-CurrentManifestPath {
  $requestedManifest = $null
  if (-not [string]::IsNullOrWhiteSpace($ManifestPath)) {
    if (-not [System.IO.Path]::IsPathRooted($ManifestPath)) { throw "ManifestPath must be absolute" }
    $requestedManifest = [System.IO.Path]::GetFullPath($ManifestPath)
  }
  if (Test-Path -LiteralPath $RuntimeRoot) { Assert-EntityDirectory $RuntimeRoot }
  $pointerPath = Join-Path $RuntimeRoot "current-deployment.json"
  if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) { return $null }
  Assert-NoReparsePath $pointerPath
  $pointerRaw = [System.IO.File]::ReadAllBytes($pointerPath)
  $pointer = ConvertFrom-ExactJson ([System.Text.UTF8Encoding]::new($false, $true).GetString($pointerRaw)) "Worker current pointer"
  Assert-ExactProperties $pointer @("version", "releaseId", "manifestRelativePath", "manifestSha256", "pointerPayloadSha256") "Worker current pointer"
  Assert-CanonicalJsonPayload $pointerRaw $pointer "pointerPayloadSha256" "Worker current pointer"
  if ($pointer.version -cne "teruisi-local-worker-current-v1" -or $pointer.releaseId -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{16}$') {
    throw "Worker current pointer identity is invalid"
  }
  $expected = "releases/$($pointer.releaseId)/deployment-manifest.json"
  if ($pointer.manifestRelativePath -cne $expected) { throw "Worker current pointer path is invalid" }
  $resolved = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot ($expected.Replace('/', '\'))))
  Assert-NoReparsePath $resolved
  if ((Get-Sha256File $resolved) -cne $pointer.manifestSha256) { throw "Worker current pointer manifest hash mismatch" }
  $authorityPath = Join-Path $RuntimeRoot "state\sales-postgresql-authority.json"
  $successorRoot = Join-Path $RuntimeRoot "state\worker-release-successors"
  if (-not (Test-Path -LiteralPath $authorityPath -PathType Leaf) -and
      -not (Test-Path -LiteralPath $successorRoot)) {
    if ($requestedManifest -and -not $requestedManifest.Equals(
        $resolved, [System.StringComparison]::OrdinalIgnoreCase
      )) { throw "ManifestPath is not the bootstrap current release" }
    return $resolved
  }
  if (-not (Test-Path -LiteralPath $RotationTool -PathType Leaf)) { throw "Worker release rotation resolver is missing" }
  Assert-NoReparsePath $RotationTool
  $resolveArgs = @($RotationTool, "resolve", "--json")
  if ($AllowTestRuntimeRoot) {
    $resolveArgs += @("--allow-test-runtime-root", "--runtime-root", $RuntimeRoot)
  }
  $outerErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $resolveOutput = @(& (Get-NodeExecutable) @resolveArgs 2>&1)
    $resolveExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $outerErrorActionPreference
  }
  $resolveText = (($resolveOutput | ForEach-Object { $_.ToString() }) -join "`n").Trim()
  if ($resolveExitCode -ne 0) { throw "Worker effective release resolution failed: exit=$resolveExitCode; $resolveText" }
  $resolution = ConvertFrom-ExactJson $resolveText "Worker effective release resolution"
  Assert-ExactProperties $resolution @(
    "status", "version", "releaseId", "manifestPath", "manifestSha256", "guardReceiptSha256",
    "successorCount", "chainStateSha256"
  ) "Worker effective release resolution"
  if ([string]$resolution.status -cne "resolved" -or
      [string]$resolution.version -cne "teruisi-local-worker-effective-release-v1" -or
      [string]$resolution.manifestSha256 -notmatch '^[0-9a-f]{64}$' -or
      [string]$resolution.guardReceiptSha256 -notmatch '^[0-9a-f]{64}$' -or
      [string]$resolution.chainStateSha256 -notmatch '^[0-9a-f]{64}$') {
    throw "Worker effective release resolution identity is invalid"
  }
  $effectiveManifest = [System.IO.Path]::GetFullPath([string]$resolution.manifestPath)
  if ($requestedManifest -and -not $requestedManifest.Equals(
      $effectiveManifest, [System.StringComparison]::OrdinalIgnoreCase
    )) { throw "ManifestPath is not the authorized effective head release" }
  return $effectiveManifest
}

function Get-ManifestIdentity([string]$Path) {
  if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Worker release manifest is missing" }
  Assert-NoReparsePath $Path
  $releaseRoot = Split-Path -Parent $Path
  $releaseId = Split-Path -Leaf $releaseRoot
  $releasesRoot = Split-Path -Parent $releaseRoot
  $derivedRuntime = Split-Path -Parent $releasesRoot
  if ((Split-Path -Leaf $Path) -cne "deployment-manifest.json" -or (Split-Path -Leaf $releasesRoot) -cne "releases") {
    throw "Worker manifest path is outside a bounded release"
  }
  if (-not ([System.IO.Path]::GetFullPath($derivedRuntime).TrimEnd('\')).Equals([System.IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Worker manifest runtime root mismatch"
  }
  $manifestRaw = [System.IO.File]::ReadAllBytes($Path)
  $manifest = ConvertFrom-ExactJson ([System.Text.UTF8Encoding]::new($false, $true).GetString($manifestRaw)) "Worker release manifest"
  Assert-ExactProperties $manifest @("version", "releaseId", "createdAt", "source", "build", "runtime", "artifacts", "processIdentity", "manifestPayloadSha256") "Worker release manifest"
  Assert-CanonicalJsonPayload $manifestRaw $manifest "manifestPayloadSha256" "Worker release manifest"
  if ($manifest.version -cne "teruisi-local-worker-release-v1" -or $manifest.releaseId -cne $releaseId) { throw "Worker release identity is invalid" }
  Assert-ExactProperties $manifest.processIdentity @(
    "supervisorEntrypoint", "serviceControl", "manifestFile", "processReceipt", "processReceiptVersion",
    "wranglerEntrypoint", "wranglerCliEntrypoint", "fixedWranglerArguments", "helperEntrypoint", "fixedHelperArguments"
  ) "Worker process identity"
  if ($manifest.runtime.runtimeRootPathSha256 -cne (Get-CanonicalPathSha256 $RuntimeRoot) -or
      $manifest.runtime.releaseRootPathSha256 -cne (Get-CanonicalPathSha256 $releaseRoot) -or
      $manifest.runtime.helperMode -cne "supervisor_managed_immutable_bundle" -or
      $manifest.runtime.helperHost -cne $HelperHost -or [int]$manifest.runtime.helperPort -ne $HelperPort -or
      $manifest.runtime.helperMutableRoot -cne $manifest.runtime.protectedSourceRoot -or
      $manifest.runtime.helperMutableRootPathSha256 -cne (Get-CanonicalPathSha256 $manifest.runtime.helperMutableRoot) -or
      $manifest.processIdentity.helperEntrypoint -cne "helper/tmall-workflow-helper.mjs" -or
      (@($manifest.processIdentity.fixedHelperArguments) -join "`n") -cne (@("serve", "--port", "$HelperPort") -join "`n")) {
    throw "Worker manifest runtime/release path identity is invalid"
  }
  return [pscustomobject]@{
    Path = [System.IO.Path]::GetFullPath($Path)
    Sha256 = Get-Sha256Bytes $manifestRaw
    ReleaseId = $releaseId
    ReleaseRoot = $releaseRoot
    Manifest = $manifest
  }
}

function Invoke-ReleaseVerification(
  [object]$Identity,
  [string]$ProcessPolicy,
  [switch]$WriteSupervisorPrelaunchReceipt
) {
  $node = Get-NodeExecutable
  $args = @(
    $ReleaseTool, "verify", "--manifest", $Identity.Path,
    "--approved-manifest-sha256", $Identity.Sha256,
    "--expected-source-d1-path-sha256", $Identity.Manifest.runtime.sourceD1PathSha256,
    "--expected-persist-root-path-sha256", $Identity.Manifest.runtime.persistRootPathSha256,
    "--expected-host", $WorkerHost, "--expected-port", "$WorkerPort",
    "--require-sales-retired-code-receipt", "--process-policy", $ProcessPolicy, "--json"
  )
  if ($WriteSupervisorPrelaunchReceipt) { $args += "--write-supervisor-prelaunch-receipt" }
  if ($AllowTestRuntimeRoot) { $args += "--allow-test-runtime-root" }
  $output = & $node @args 2>&1
  if ($LASTEXITCODE -ne 0) { throw (($output | Out-String).Trim()) }
  return (ConvertFrom-ExactJson (($output | Out-String).Trim()) "Worker release verification")
}

function Remove-ExactSupervisorPrelaunchVerificationReceipt([string]$ExpectedSha256) {
  if ($ExpectedSha256 -cnotmatch "^[0-9a-f]{64}$") { return }
  $receiptPath = Join-Path $RuntimeRoot "state\worker-startup-verification.json"
  if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { return }
  if ((Get-Sha256File $receiptPath) -cne $ExpectedSha256) {
    throw "Worker supervisor prelaunch verification receipt changed before cleanup"
  }
  Remove-Item -LiteralPath $receiptPath -Force
}

function Get-PortProcessIds([int]$Port = $WorkerPort, [string]$ExpectedHost = $WorkerHost) {
  try {
    $listeners = @([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners())
  } catch {
    throw "Unable to enumerate active TCP listeners: $($_.Exception.Message)"
  }
  $portListeners = @($listeners | Where-Object { $_.Port -eq $Port })
  if ($portListeners.Count -eq 0) { return @() }
  if (@($portListeners | Where-Object { $_.Address.ToString() -cne $ExpectedHost }).Count -gt 0) {
    throw "Port $Port has a wildcard, IPv6, or non-loopback listener; refusing controlled ownership"
  }
  try {
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
  } catch {
    throw "Port $Port is listening but its owning process could not be enumerated"
  }
  if (@($connections | Where-Object { $_.LocalAddress -cne $ExpectedHost }).Count -gt 0) {
    throw "Port $Port owning connection is not bound exactly to $ExpectedHost"
  }
  $ids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
  if ($ids.Count -lt 1) { throw "Port $Port is listening without an enumerable owning process" }
  return $ids
}

function Get-UniqueProcess([int]$Id, [object[]]$Processes) {
  $matches = @($Processes | Where-Object { $_.ProcessId -eq $Id })
  if ($matches.Count -ne 1) { return $null }
  return $matches[0]
}

function Get-ProcessTree([int]$RootId, [object[]]$Processes) {
  $queue = [System.Collections.Generic.Queue[object]]::new()
  $queue.Enqueue([pscustomobject]@{ Id = $RootId; Depth = 0 })
  $visited = [System.Collections.Generic.HashSet[int]]::new()
  $entries = @()
  while ($queue.Count -gt 0) {
    $candidate = $queue.Dequeue()
    if (-not $visited.Add([int]$candidate.Id)) { continue }
    $process = Get-UniqueProcess ([int]$candidate.Id) $Processes
    if (-not $process) { continue }
    $entries += [pscustomobject]@{ Process = $process; Depth = [int]$candidate.Depth }
    $parentTicks = Get-CreationTicks $process
    foreach ($child in @($Processes | Where-Object { $_.ParentProcessId -eq $process.ProcessId })) {
      $childTicks = Get-CreationTicks $child
      # Win32 ParentProcessId can point at a reused PID.  A real child cannot
      # predate its exact parent creation identity, so ignore inverted edges.
      if ($childTicks -lt $parentTicks) { continue }
      $queue.Enqueue([pscustomobject]@{ Id = [int]$child.ProcessId; Depth = ([int]$candidate.Depth + 1) })
    }
  }
  return $entries
}

function Get-CreationIdentity([object]$Process) {
  if ($Process.CreationDate -is [DateTime]) { return $Process.CreationDate.ToUniversalTime().ToString("o") }
  return ([string]$Process.CreationDate)
}

function Get-CreationTicks([object]$Process) {
  if ($Process.CreationDate -is [DateTime]) { return $Process.CreationDate.ToUniversalTime().Ticks }
  $text = [string]$Process.CreationDate
  $parsed = [DateTimeOffset]::MinValue
  if ([DateTimeOffset]::TryParse($text, [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$parsed)) {
    return $parsed.UtcDateTime.Ticks
  }
  try { return ([System.Management.ManagementDateTimeConverter]::ToDateTime($text)).ToUniversalTime().Ticks }
  catch { throw "Process $($Process.ProcessId) has an unreadable creation identity" }
}

function Test-ExactProcessArguments([object]$Process, [string[]]$Expected, [int[]]$PathIndexes) {
  if ([string]::IsNullOrWhiteSpace([string]$Process.CommandLine) -or
      [string]::IsNullOrWhiteSpace([string]$Process.ExecutablePath)) { return $false }
  try { $actual = [string[]][Teruisi.NativeCommandLine]::Parse([string]$Process.CommandLine) }
  catch { return $false }
  if ($actual.Count -ne $Expected.Count) { return $false }
  $pathSet = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($index in $PathIndexes) { [void]$pathSet.Add($index) }
  for ($index = 0; $index -lt $Expected.Count; $index++) {
    if ($pathSet.Contains($index)) {
      try {
        if (-not ([System.IO.Path]::GetFullPath($actual[$index])).Equals(
          [System.IO.Path]::GetFullPath($Expected[$index]), [System.StringComparison]::OrdinalIgnoreCase
        )) { return $false }
      } catch { return $false }
    } elseif ($actual[$index] -cne $Expected[$index]) { return $false }
  }
  try {
    return ([System.IO.Path]::GetFullPath([string]$Process.ExecutablePath)).Equals(
      [System.IO.Path]::GetFullPath($Expected[0]), [System.StringComparison]::OrdinalIgnoreCase
    )
  } catch { return $false }
}

function Get-ProcessReceiptCore([object]$Identity, [object]$Supervisor) {
  return [ordered]@{
    manifestPathSha256 = Get-CanonicalPathSha256 $Identity.Path
    manifestSha256 = $Identity.Sha256
    releaseId = $Identity.ReleaseId
    supervisorCreationDate = Get-CreationIdentity $Supervisor
    supervisorEntrypointPathSha256 = Get-CanonicalPathSha256 (Join-Path $Identity.ReleaseRoot "tools\worker-local-runtime-supervisor.mjs")
    supervisorPid = [int]$Supervisor.ProcessId
    version = $ProcessReceiptVersion
  }
}

function Get-ProcessReceiptPayloadHash([object]$Core) {
  $json = $Core | ConvertTo-Json -Compress
  return Get-Sha256Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

function Write-ProcessReceipt([object]$Identity, [object]$Supervisor) {
  $core = Get-ProcessReceiptCore $Identity $Supervisor
  $receipt = [ordered]@{
    manifestPathSha256 = $core.manifestPathSha256
    manifestSha256 = $core.manifestSha256
    receiptPayloadSha256 = Get-ProcessReceiptPayloadHash $core
    releaseId = $core.releaseId
    supervisorCreationDate = $core.supervisorCreationDate
    supervisorEntrypointPathSha256 = $core.supervisorEntrypointPathSha256
    supervisorPid = $core.supervisorPid
    version = $core.version
  }
  $stateRoot = Join-Path $RuntimeRoot "state"
  Assert-EntityDirectory $RuntimeRoot
  if (-not (Test-Path -LiteralPath $stateRoot)) { [System.IO.Directory]::CreateDirectory($stateRoot) | Out-Null }
  Assert-EntityDirectory $stateRoot
  $target = Join-Path $stateRoot "worker-process.json"
  if (Test-Path -LiteralPath $target) { throw "A Worker process receipt already exists; refusing to replace it" }
  Assert-NoReparsePath $target -AllowMissingLeaf
  $temporary = "$target.tmp-$([Guid]::NewGuid().ToString('N'))"
  $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($receipt | ConvertTo-Json -Compress) + "`n")
  $stream = $null
  try {
    $stream = [System.IO.FileStream]::new(
      $temporary, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
      [System.IO.FileShare]::None, 4096, [System.IO.FileOptions]::WriteThrough
    )
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush($true)
    $stream.Dispose()
    $stream = $null
    [System.IO.File]::Move($temporary, $target)
  } finally {
    if ($stream) { $stream.Dispose() }
    if (Test-Path -LiteralPath $temporary) { [System.IO.File]::Delete($temporary) }
  }
}

function Read-ProcessReceipt([object]$Identity) {
  $target = Join-Path $RuntimeRoot "state\worker-process.json"
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { return $null }
  Assert-EntityDirectory (Split-Path -Parent $target)
  Assert-NoReparsePath $target
  $raw = [System.IO.File]::ReadAllBytes($target)
  $receipt = ConvertFrom-ExactJson ([System.Text.Encoding]::UTF8.GetString($raw)) "Worker process receipt"
  Assert-ExactProperties $receipt @(
    "manifestPathSha256", "manifestSha256", "receiptPayloadSha256", "releaseId", "supervisorCreationDate",
    "supervisorEntrypointPathSha256", "supervisorPid", "version"
  ) "Worker process receipt"
  $core = [ordered]@{
    manifestPathSha256 = $receipt.manifestPathSha256
    manifestSha256 = $receipt.manifestSha256
    releaseId = $receipt.releaseId
    supervisorCreationDate = $receipt.supervisorCreationDate
    supervisorEntrypointPathSha256 = $receipt.supervisorEntrypointPathSha256
    supervisorPid = [int]$receipt.supervisorPid
    version = $receipt.version
  }
  $normalized = [ordered]@{
    manifestPathSha256 = $core.manifestPathSha256
    manifestSha256 = $core.manifestSha256
    receiptPayloadSha256 = Get-ProcessReceiptPayloadHash $core
    releaseId = $core.releaseId
    supervisorCreationDate = $core.supervisorCreationDate
    supervisorEntrypointPathSha256 = $core.supervisorEntrypointPathSha256
    supervisorPid = $core.supervisorPid
    version = $core.version
  }
  $expectedBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($normalized | ConvertTo-Json -Compress) + "`n")
  if (-not [System.Linq.Enumerable]::SequenceEqual([byte[]]$raw, [byte[]]$expectedBytes)) { throw "Worker process receipt is not canonical" }
  if ($receipt.receiptPayloadSha256 -cne $normalized.receiptPayloadSha256 -or
      $receipt.version -cne $ProcessReceiptVersion -or $receipt.releaseId -cne $Identity.ReleaseId -or
      $receipt.manifestSha256 -cne $Identity.Sha256 -or $receipt.manifestPathSha256 -cne (Get-CanonicalPathSha256 $Identity.Path) -or
      $receipt.supervisorEntrypointPathSha256 -cne (Get-CanonicalPathSha256 (Join-Path $Identity.ReleaseRoot "tools\worker-local-runtime-supervisor.mjs"))) {
    throw "Worker process receipt identity is invalid"
  }
  $receipt | Add-Member -NotePropertyName "RawSha256" -NotePropertyValue (Get-Sha256Bytes $raw)
  return $receipt
}

function Test-ExactHelperProcess([object]$Process, [object]$Identity, [int]$SupervisorId) {
  if ([int]$Process.ParentProcessId -ne $SupervisorId) { return $false }
  $name = ([string]$Process.Name).ToLowerInvariant()
  if ($name -notin @("node", "node.exe")) { return $false }
  $nodePath = Get-NodeExecutable
  $helperPath = Join-Path $Identity.ReleaseRoot ($Identity.Manifest.processIdentity.helperEntrypoint.Replace('/', '\'))
  $fixed = [string[]]@($Identity.Manifest.processIdentity.fixedHelperArguments | ForEach-Object { [string]$_ })
  $expected = [string[]]@($nodePath, $helperPath) + $fixed
  return Test-ExactProcessArguments $Process $expected @(0, 1)
}

function Get-HelperOwnershipContext([object[]]$Tree, [object]$Identity, [int]$SupervisorId) {
  $candidates = @($Tree | Where-Object { Test-ExactHelperProcess $_.Process $Identity $SupervisorId })
  if ($candidates.Count -gt 1) { throw "Multiple immutable helper processes claim the same supervisor" }
  $helper = if ($candidates.Count -eq 1) { $candidates[0].Process } else { $null }
  $descendants = [System.Collections.Generic.HashSet[int]]::new()
  if ($helper) {
    [void]$descendants.Add([int]$helper.ProcessId)
    foreach ($entry in @($Tree | Sort-Object Depth)) {
      if ($descendants.Contains([int]$entry.Process.ParentProcessId)) {
        [void]$descendants.Add([int]$entry.Process.ProcessId)
      }
    }
  }
  $helperIds = [System.Collections.Generic.HashSet[int]]::new()
  if ($helper) { [void]$helperIds.Add([int]$helper.ProcessId) }
  return [pscustomobject]@{ Helper = $helper; HelperIds = $helperIds; DescendantIds = $descendants }
}

function Test-AllowedTreeProcess([object]$Process, [object]$Identity, [int]$SupervisorId, [object]$HelperContext = $null) {
  $name = ([string]$Process.Name).ToLowerInvariant()
  $nodePath = Get-NodeExecutable
  $supervisorPath = Join-Path $Identity.ReleaseRoot "tools\worker-local-runtime-supervisor.mjs"
  $wranglerPath = Join-Path $Identity.ReleaseRoot "node_modules\wrangler\bin\wrangler.js"
  $wranglerCliPath = Join-Path $Identity.ReleaseRoot "node_modules\wrangler\wrangler-dist\cli.js"
  if ($Process.ProcessId -eq $SupervisorId) {
    $expected = [string[]]@($nodePath, $supervisorPath, "--manifest", $Identity.Path, "--approved-manifest-sha256", $Identity.Sha256)
    return $name -in @("node", "node.exe") -and (Test-ExactProcessArguments $Process $expected @(0, 1, 3))
  }
  if ($HelperContext -and $HelperContext.HelperIds -and $HelperContext.HelperIds.Contains([int]$Process.ProcessId)) {
    return Test-ExactHelperProcess $Process $Identity $SupervisorId
  }
  if ($HelperContext -and $HelperContext.DescendantIds.Contains([int]$Process.ProcessId)) {
    # The helper itself was exact and Get-ProcessTree admitted only monotonic
    # creation-time edges, so its business children remain within its owner.
    return $true
  }
  if ($name -in @("node", "node.exe")) {
    $fixed = [string[]]@($Identity.Manifest.processIdentity.fixedWranglerArguments | ForEach-Object { [string]$_ })
    $wrapperExpected = [string[]]@($nodePath, $wranglerPath) + $fixed
    if (Test-ExactProcessArguments $Process $wrapperExpected @(0, 1)) { return $true }
    $cliExpected = [string[]]@($nodePath, "--no-warnings", "--experimental-vm-modules", $wranglerCliPath) + $fixed
    return Test-ExactProcessArguments $Process $cliExpected @(0, 3)
  }
  if ($name -in @("workerd", "workerd.exe", "esbuild", "esbuild.exe")) {
    $root = ([System.IO.Path]::GetFullPath((Join-Path $Identity.ReleaseRoot "node_modules")).TrimEnd('\') + '\')
    return -not [string]::IsNullOrWhiteSpace([string]$Process.ExecutablePath) -and
      ([System.IO.Path]::GetFullPath([string]$Process.ExecutablePath)).StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
  }
  if ($name -eq "conhost.exe") { return $Process.ParentProcessId -eq $SupervisorId }
  return $false
}

function Get-StartupOwnershipDecision(
  [object]$Status,
  [object]$FirstAnomalyAt,
  [datetime]$ObservedAt,
  [int]$GraceMilliseconds = $StartupOwnershipGraceMilliseconds
) {
  if (-not $Status) {
    return [pscustomobject]@{ Action = "Fail"; FirstAnomalyAt = $FirstAnomalyAt }
  }
  if ($Status.State -eq "exact_release") {
    return [pscustomobject]@{ Action = "Ready"; FirstAnomalyAt = $null }
  }
  if ($Status.State -eq "starting_exact_release") {
    return [pscustomobject]@{ Action = "Wait"; FirstAnomalyAt = $null }
  }
  if ($Status.State -notin @("stopped", "stale_or_invalid_receipt", "foreign_or_ambiguous")) {
    return [pscustomobject]@{ Action = "Fail"; FirstAnomalyAt = $FirstAnomalyAt }
  }

  $anomalyAt = if ($null -eq $FirstAnomalyAt) { $ObservedAt } else { [datetime]$FirstAnomalyAt }
  $elapsedMilliseconds = [math]::Max(0, ($ObservedAt - $anomalyAt).TotalMilliseconds)
  $action = if ($elapsedMilliseconds -lt $GraceMilliseconds) { "Grace" } else { "Fail" }
  return [pscustomobject]@{ Action = $action; FirstAnomalyAt = $anomalyAt }
}

function Get-BoundedLogTail([string]$Path, [int]$MaximumCharacters = 1600) {
  if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  try {
    $text = ((Get-Content -LiteralPath $Path -Tail 20 -ErrorAction Stop) -join " | ").Trim()
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    if ($text.Length -gt $MaximumCharacters) { return "…" + $text.Substring($text.Length - $MaximumCharacters) }
    return $text
  } catch {
    return $null
  }
}

function Test-MonotonicAncestor([int]$ProcessId, [int]$AncestorId, [object[]]$Processes) {
  $current = Get-UniqueProcess $ProcessId $Processes
  $seen = [System.Collections.Generic.HashSet[int]]::new()
  for ($depth = 0; $depth -lt 32 -and $current; $depth++) {
    if (-not $seen.Add([int]$current.ProcessId)) { return $false }
    if ([int]$current.ProcessId -eq $AncestorId) { return $true }
    $parent = Get-UniqueProcess ([int]$current.ParentProcessId) $Processes
    if (-not $parent) { return $false }
    if ((Get-CreationTicks $current) -lt (Get-CreationTicks $parent)) { return $false }
    $current = $parent
  }
  return $false
}

function Test-SameProcessIdentity([object]$Left, [object]$Right) {
  return $Left -and $Right -and [int]$Left.ProcessId -eq [int]$Right.ProcessId -and
    (Get-CreationIdentity $Left) -ceq (Get-CreationIdentity $Right)
}

function Get-StopHelperOwnershipContext([object[]]$Tree, [object]$Identity, [int]$SupervisorId) {
  # Stop can race a bounded helper restart.  Preserve every exact helper
  # generation discovered under the same supervisor creation identity; this
  # lets later sweeps follow children through an already-exited helper anchor.
  $helpers = @($Tree | Where-Object { Test-ExactHelperProcess $_.Process $Identity $SupervisorId })
  $helperIds = [System.Collections.Generic.HashSet[int]]::new()
  $descendants = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($entry in $helpers) {
    [void]$helperIds.Add([int]$entry.Process.ProcessId)
    [void]$descendants.Add([int]$entry.Process.ProcessId)
  }
  foreach ($entry in @($Tree | Sort-Object Depth)) {
    if ($descendants.Contains([int]$entry.Process.ParentProcessId)) {
      [void]$descendants.Add([int]$entry.Process.ProcessId)
    }
  }
  return [pscustomobject]@{ Helper = $null; Helpers = @($helpers.Process); HelperIds = $helperIds; DescendantIds = $descendants }
}

function Get-ControlledStopSweep(
  [object]$Identity,
  [object]$SupervisorSnapshot,
  [object[]]$KnownSnapshots,
  [object[]]$Processes,
  [long]$DirectChildCutoffTicks
) {
  $supervisorId = [int]$SupervisorSnapshot.ProcessId
  $currentById = @{}
  foreach ($process in $Processes) {
    $id = [int]$process.ProcessId
    if ($currentById.ContainsKey($id)) { throw "Process snapshot contains duplicate PID $id" }
    $currentById[$id] = $process
  }
  $knownById = @{}
  foreach ($process in @($KnownSnapshots) + @($SupervisorSnapshot)) {
    $id = [int]$process.ProcessId
    if ($knownById.ContainsKey($id) -and -not (Test-SameProcessIdentity $knownById[$id] $process)) {
      throw "Controlled stop ledger contains a reused PID $id"
    }
    $knownById[$id] = $process
  }

  $augmented = @($Processes)
  foreach ($id in @($knownById.Keys)) {
    if ($currentById.ContainsKey($id)) {
      if (-not (Test-SameProcessIdentity $knownById[$id] $currentById[$id])) {
        throw "PID $id was reused while stopping the immutable Worker; refusing ambiguous termination"
      }
    } else {
      $augmented += $knownById[$id]
    }
  }
  $tree = @(Get-ProcessTree $supervisorId $augmented)
  if ($tree.Count -lt 1 -or -not (Test-SameProcessIdentity $tree[0].Process $SupervisorSnapshot)) {
    throw "Controlled stop lineage lost the exact supervisor creation identity"
  }
  $helperContext = Get-StopHelperOwnershipContext $tree $Identity $supervisorId
  foreach ($entry in $tree) {
    $process = $entry.Process
    if ([int]$process.ProcessId -ne $supervisorId -and [int]$process.ParentProcessId -eq $supervisorId -and
        (Get-CreationTicks $process) -gt $DirectChildCutoffTicks) {
      throw "A process claiming the stopped supervisor was created after its termination fence"
    }
    if (-not (Test-AllowedTreeProcess $process $Identity $supervisorId $helperContext)) {
      throw "Controlled stop discovered an unapproved process in the immutable supervisor lineage"
    }
    $id = [int]$process.ProcessId
    if ($knownById.ContainsKey($id) -and -not (Test-SameProcessIdentity $knownById[$id] $process)) {
      throw "Controlled stop lineage reused PID $id"
    }
    $knownById[$id] = $process
  }
  $liveEntries = @($tree | Where-Object {
    $id = [int]$_.Process.ProcessId
    $currentById.ContainsKey($id) -and (Test-SameProcessIdentity $_.Process $currentById[$id])
  })
  return [pscustomobject]@{
    Tree = $tree
    LiveEntries = $liveEntries
    KnownSnapshots = @($knownById.Values | Sort-Object { [int]$_.ProcessId })
  }
}

function Get-ExactCurrentProcess([int]$Id) {
  $matches = @(Get-CimInstance Win32_Process -Filter "ProcessId = $Id" -ErrorAction Stop)
  if ($matches.Count -gt 1) { throw "Win32 process enumeration returned duplicate PID $Id" }
  return $(if ($matches.Count -eq 1) { $matches[0] } else { $null })
}

function Stop-ExactProcessIdentity([object]$Snapshot) {
  $current = Get-ExactCurrentProcess ([int]$Snapshot.ProcessId)
  if (-not $current) { return }
  if (-not (Test-SameProcessIdentity $current $Snapshot)) {
    throw "PID $($Snapshot.ProcessId) changed creation identity before termination"
  }
  try {
    Stop-Process -Id ([int]$Snapshot.ProcessId) -Force -ErrorAction Stop
  } catch {
    $failure = $_
    $after = Get-ExactCurrentProcess ([int]$Snapshot.ProcessId)
    if (-not $after) { return }
    if (-not (Test-SameProcessIdentity $after $Snapshot)) {
      throw "PID $($Snapshot.ProcessId) was reused during termination"
    }
    throw $failure
  }
}

function Get-WorkerStatusInternal([object]$Identity) {
  $workerPortIds = @(Get-PortProcessIds $WorkerPort $WorkerHost)
  $helperPortIds = @(Get-PortProcessIds $HelperPort $HelperHost)
  $portsPresent = $workerPortIds.Count -gt 0 -or $helperPortIds.Count -gt 0
  $empty = [pscustomobject]@{
    State = $(if ($portsPresent) { "foreign_or_ambiguous" } else { "stopped" })
    Identity = $Identity; Supervisor = $null; Tree = @(); PortIds = $workerPortIds; HelperPortIds = $helperPortIds
    Helper = $null; Receipt = $null; Reason = $null
  }
  if (-not $Identity) { return $empty }
  try { $receipt = Read-ProcessReceipt $Identity } catch {
    $empty.State = $(if ($portsPresent) { "foreign_or_ambiguous" } else { "stale_or_invalid_receipt" })
    $empty.Reason = "Worker process receipt could not be validated"
    return $empty
  }
  if (-not $receipt) { return $empty }
  $empty.Receipt = $receipt
  $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  $supervisor = Get-UniqueProcess ([int]$receipt.supervisorPid) $processes
  if (-not $supervisor -or (Get-CreationIdentity $supervisor) -cne $receipt.supervisorCreationDate -or
      -not (Test-AllowedTreeProcess $supervisor $Identity ([int]$receipt.supervisorPid))) {
    $empty.State = $(if ($portsPresent) { "foreign_or_ambiguous" } else { "stale_or_invalid_receipt" })
    $empty.Reason = "Exact supervisor creation identity is unavailable or changed"
    return $empty
  }
  $tree = @(Get-ProcessTree ([int]$receipt.supervisorPid) $processes)
  $helperContext = Get-HelperOwnershipContext $tree $Identity ([int]$receipt.supervisorPid)
  $rejectedTreeProcesses = @($tree | Where-Object {
      -not (Test-AllowedTreeProcess $_.Process $Identity ([int]$receipt.supervisorPid) $helperContext)
    })
  if ($tree.Count -lt 1 -or $rejectedTreeProcesses.Count -gt 0) {
    $empty.State = $(if ($portsPresent) { "foreign_or_ambiguous" } else { "stale_or_invalid_receipt" })
    if ($rejectedTreeProcesses.Count -gt 0) {
      $rejected = $rejectedTreeProcesses[0].Process
      $empty.Reason = "Unapproved or not-yet-stable supervisor descendant: name=$([string]$rejected.Name), pid=$([int]$rejected.ProcessId)"
    } else {
      $empty.Reason = "Exact supervisor process tree is temporarily unavailable"
    }
    return $empty
  }
  $workerOwned = $true
  foreach ($portId in $workerPortIds) {
    if ($helperContext.DescendantIds.Contains([int]$portId) -or
        -not (Test-MonotonicAncestor ([int]$portId) ([int]$receipt.supervisorPid) $processes)) { $workerOwned = $false }
  }
  $helperOwned = $helperPortIds.Count -eq 0 -or (
    $helperPortIds.Count -eq 1 -and $helperContext.Helper -and
    [int]$helperPortIds[0] -eq [int]$helperContext.Helper.ProcessId -and
    (Test-MonotonicAncestor ([int]$helperPortIds[0]) ([int]$receipt.supervisorPid) $processes)
  )
  if (-not $workerOwned -or -not $helperOwned) {
    return [pscustomobject]@{
      State = "foreign_or_ambiguous"; Identity = $Identity; Supervisor = $null; Tree = @(); PortIds = $workerPortIds
      HelperPortIds = $helperPortIds; Helper = $null; Receipt = $receipt; Reason = "Port ownership does not match the exact supervisor lineage"
    }
  }
  $state = if ($workerPortIds.Count -gt 0 -and $helperPortIds.Count -gt 0) { "exact_release" } else { "starting_exact_release" }
  return [pscustomobject]@{
    State = $state; Identity = $Identity; Supervisor = $supervisor; Tree = $tree; PortIds = $workerPortIds
    HelperPortIds = $helperPortIds; Helper = $helperContext.Helper; Receipt = $receipt; Reason = $null
  }
}

function Get-PublicStatus([object]$Internal) {
  return [ordered]@{
    version = $StatusVersion
    state = $Internal.State
    releaseId = if ($Internal.Identity) { $Internal.Identity.ReleaseId } else { $null }
    manifestSha256 = if ($Internal.Identity) { $Internal.Identity.Sha256 } else { $null }
    supervisorProcessId = if ($Internal.Supervisor) { [int]$Internal.Supervisor.ProcessId } else { $null }
    portProcessId = if ($Internal.PortIds.Count -eq 1) { [int]$Internal.PortIds[0] } else { $null }
    reason = $Internal.Reason
  }
}

function Test-ExactWorkerRootProcess([object]$Process, [object]$Identity, [int]$SupervisorId) {
  if ([int]$Process.ParentProcessId -ne $SupervisorId) { return $false }
  $name = ([string]$Process.Name).ToLowerInvariant()
  if ($name -notin @("node", "node.exe")) { return $false }
  $nodePath = Get-NodeExecutable
  $wranglerPath = Join-Path $Identity.ReleaseRoot "node_modules\wrangler\bin\wrangler.js"
  $fixed = [string[]]@($Identity.Manifest.processIdentity.fixedWranglerArguments | ForEach-Object { [string]$_ })
  $expected = [string[]]@($nodePath, $wranglerPath) + $fixed
  return Test-ExactProcessArguments $Process $expected @(0, 1)
}

function Restart-ExactWorkerChild([object]$Internal, [datetime]$DeadlineUtc) {
  if (-not $Internal.Supervisor -or $Internal.State -ne "exact_release") {
    throw "Hot restart requires an exact running Worker supervisor"
  }
  $supervisor = $Internal.Supervisor
  $supervisorId = [int]$supervisor.ProcessId
  $currentSupervisor = Get-ExactCurrentProcess $supervisorId
  if (-not $currentSupervisor -or
      -not (Test-SameProcessIdentity $currentSupervisor $supervisor) -or
      -not (Test-AllowedTreeProcess $currentSupervisor $Internal.Identity $supervisorId)) {
    throw "Immutable Worker supervisor identity changed before hot restart"
  }

  $snapshots = @($Internal.Tree | ForEach-Object { $_.Process })
  $workerRoots = @($snapshots | Where-Object {
    Test-ExactWorkerRootProcess $_ $Internal.Identity $supervisorId
  })
  if ($workerRoots.Count -ne 1) {
    throw "Hot restart requires exactly one manifest-bound Worker child"
  }
  $workerRoot = $workerRoots[0]
  $workerRootId = [int]$workerRoot.ProcessId
  $workerTree = @($Internal.Tree | Where-Object {
    [int]$_.Process.ProcessId -eq $workerRootId -or
    (Test-MonotonicAncestor ([int]$_.Process.ProcessId) $workerRootId $snapshots)
  })
  if ($workerTree.Count -lt 1) { throw "Exact Worker child tree is unavailable" }
  foreach ($entry in $workerTree) {
    if (-not (Test-AllowedTreeProcess $entry.Process $Internal.Identity $supervisorId)) {
      throw "Hot restart discovered an unapproved Worker descendant"
    }
  }
  $oldPortIds = @($Internal.PortIds | ForEach-Object { [int]$_ })

  # Quiesce the only process in this branch that can create new descendants,
  # then sweep its exact old lineage. The supervisor waits for port release
  # before starting the replacement, so this cannot target the new branch.
  Stop-ExactProcessIdentity $workerRoot
  $workerRootStopped = $false
  for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $currentRoot = Get-ExactCurrentProcess $workerRootId
    if (-not $currentRoot) { $workerRootStopped = $true; break }
    if (-not (Test-SameProcessIdentity $currentRoot $workerRoot)) {
      throw "Worker root PID was reused before the hot-restart fence completed"
    }
    Start-Sleep -Milliseconds 50
  }
  if (-not $workerRootStopped) { throw "Exact Worker root did not terminate within 2 seconds" }

  $currentProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  $currentById = @{}
  foreach ($process in $currentProcesses) { $currentById[[int]$process.ProcessId] = $process }
  if ($currentById.ContainsKey($workerRootId)) {
    throw "Worker root PID was reused after the hot-restart fence completed"
  }
  $augmented = @($currentProcesses)
  foreach ($snapshot in $snapshots) {
    $id = [int]$snapshot.ProcessId
    if ($currentById.ContainsKey($id)) {
      if (-not (Test-SameProcessIdentity $currentById[$id] $snapshot)) {
        throw "Worker descendant PID $id was reused during hot restart"
      }
    } else {
      $augmented += $snapshot
    }
  }
  $remainingTree = @(Get-ProcessTree $workerRootId $augmented)
  if ($remainingTree.Count -lt 1 -or -not (Test-SameProcessIdentity $remainingTree[0].Process $workerRoot)) {
    throw "Hot restart lost the exact old Worker root identity"
  }
  foreach ($entry in @($remainingTree | Sort-Object Depth -Descending)) {
    $id = [int]$entry.Process.ProcessId
    if ($id -eq $workerRootId -or -not $currentById.ContainsKey($id)) { continue }
    if (-not (Test-AllowedTreeProcess $entry.Process $Internal.Identity $supervisorId)) {
      throw "Hot restart sweep discovered an unapproved Worker descendant"
    }
    Stop-ExactProcessIdentity $entry.Process
  }

  $readyStatus = $null
  while ([DateTime]::UtcNow -lt $DeadlineUtc) {
    Start-Sleep -Milliseconds 250
    $candidate = Get-WorkerStatusInternal $Internal.Identity
    if ($candidate.Supervisor -and -not (Test-SameProcessIdentity $candidate.Supervisor $supervisor)) {
      throw "Immutable Worker supervisor identity changed during hot restart"
    }
    $oldPortStillPresent = @($candidate.PortIds | Where-Object { $oldPortIds -contains [int]$_ }).Count -gt 0
    if ($candidate.State -eq "exact_release" -and -not $oldPortStillPresent) {
      $readyStatus = $candidate
      break
    }
  }
  if (-not $readyStatus) {
    $state = if ($candidate) { [string]$candidate.State } else { "unavailable" }
    $reason = if ($candidate -and $candidate.Reason) { "; reason=$([string]$candidate.Reason)" } else { "" }
    throw "Worker child did not return before the hot-restart deadline: state=$state$reason"
  }
  if (@($readyStatus.PortIds | Where-Object { $oldPortIds -contains [int]$_ }).Count -gt 0) {
    throw "Hot restart did not replace the exact Worker port process"
  }
  return $readyStatus
}

function Stop-ExactWorkerSnapshot([object]$Internal) {
  if (-not $Internal.Supervisor -or $Internal.State -notin @("starting_exact_release", "exact_release")) { return }
  $supervisor = $Internal.Supervisor
  $supervisorId = [int]$supervisor.ProcessId
  $currentSupervisor = Get-ExactCurrentProcess $supervisorId
  if ($currentSupervisor) {
    if (-not (Test-SameProcessIdentity $currentSupervisor $supervisor) -or
        -not (Test-AllowedTreeProcess $currentSupervisor $Internal.Identity $supervisorId)) {
      throw "Immutable Worker supervisor identity changed before Stop"
    }
    Stop-ExactProcessIdentity $supervisor
  }
  # First quiesce the only process allowed to create new helper/Worker roots.
  # Do not establish the direct-child cutoff until its exact creation identity
  # is absent; a reused PID is ambiguity, never a target.
  $supervisorStopped = $false
  for ($attempt = 0; $attempt -lt 50; $attempt++) {
    $currentSupervisor = Get-ExactCurrentProcess $supervisorId
    if (-not $currentSupervisor) { $supervisorStopped = $true; break }
    if (-not (Test-SameProcessIdentity $currentSupervisor $supervisor)) {
      throw "Supervisor PID was reused before the Stop fence completed"
    }
    Start-Sleep -Milliseconds 100
  }
  if (-not $supervisorStopped) { throw "Exact immutable Worker supervisor did not terminate within 5 seconds" }
  $directChildCutoffTicks = (Get-Date).ToUniversalTime().Ticks

  $knownSnapshots = @($Internal.Tree | ForEach-Object { $_.Process })
  $stableEmptySweeps = 0
  for ($attempt = 0; $attempt -lt 100; $attempt++) {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $sweep = Get-ControlledStopSweep $Internal.Identity $supervisor $knownSnapshots $processes $directChildCutoffTicks
    $knownSnapshots = @($sweep.KnownSnapshots)
    $live = @($sweep.LiveEntries | Where-Object { [int]$_.Process.ProcessId -ne $supervisorId })
    if ($live.Count -eq 0) {
      $stableEmptySweeps += 1
      if ($stableEmptySweeps -ge 3) { return }
    } else {
      $stableEmptySweeps = 0
      # Kill exact lineage parents first so they cannot create another child;
      # subsequent bounded sweeps retain their creation identities as anchors
      # and collect any child that raced the previous enumeration.
      foreach ($entry in @($live | Sort-Object Depth)) {
        Stop-ExactProcessIdentity $entry.Process
      }
    }
    Start-Sleep -Milliseconds 100
  }
  throw "Controlled immutable Worker/helper lineage did not quiesce within 10 seconds"
}

function Remove-ExactProcessReceipt([object]$Identity) {
  $receiptPath = Join-Path $RuntimeRoot "state\worker-process.json"
  if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { return }
  $receipt = Read-ProcessReceipt $Identity
  Assert-NoReparsePath $receiptPath
  if ((Get-Sha256File $receiptPath) -cne $receipt.RawSha256) { throw "Worker process receipt changed before removal" }
  [System.IO.File]::Delete($receiptPath)
}

function Test-IsControlledStartupShortcut([object]$Shortcut) {
  $powershellPath = (Get-Command "powershell.exe").Source
  if (-not $Shortcut -or -not ([string]$Shortcut.TargetPath).Equals($powershellPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
  $match = [regex]::Match(
    [string]$Shortcut.Arguments,
    '^-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "([^"]+\\worker-local-service\.ps1)" -Action Start -ManifestPath "([^"]+\\deployment-manifest\.json)"$'
  )
  if (-not $match.Success) { return $false }
  try {
    $service = [System.IO.Path]::GetFullPath($match.Groups[1].Value)
    $manifest = [System.IO.Path]::GetFullPath($match.Groups[2].Value)
    $releaseRoot = Split-Path -Parent $manifest
    $expectedService = Join-Path $releaseRoot "tools\worker-local-service.ps1"
    $boundedRoot = ([System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "releases")).TrimEnd('\') + '\')
    if (-not ($releaseRoot.StartsWith($boundedRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
      $service.Equals($expectedService, [System.StringComparison]::OrdinalIgnoreCase) -and
      (Test-Path -LiteralPath $service -PathType Leaf) -and (Test-Path -LiteralPath $manifest -PathType Leaf))) { return $false }
    Assert-NoReparsePath $service
    Assert-NoReparsePath $manifest
    $oldIdentity = Get-ManifestIdentity $manifest
    $serviceEvidence = @($oldIdentity.Manifest.artifacts.keyFiles | Where-Object { $_.relativePath -ceq "tools/worker-local-service.ps1" })
    return $serviceEvidence.Count -eq 1 -and
      [string]$serviceEvidence[0].sha256 -match '^[0-9a-f]{64}$' -and
      (Get-Sha256File $service) -ceq [string]$serviceEvidence[0].sha256
  } catch { return $false }
}

function Save-StartupShortcutAtomic([object]$Shell, [string]$Target, [string]$Arguments, [string]$WorkingDirectory) {
  Assert-EntityDirectory (Split-Path -Parent $StartupShortcut)
  $temporary = "$StartupShortcut.tmp-$([Guid]::NewGuid().ToString('N')).lnk"
  $backup = "$StartupShortcut.backup-$([Guid]::NewGuid().ToString('N')).lnk"
  try {
    $shortcut = $Shell.CreateShortcut($temporary)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.WindowStyle = 7
    $shortcut.Description = "TERUISI immutable local Worker"
    $shortcut.Save()
    if (Test-Path -LiteralPath $StartupShortcut -PathType Leaf) {
      [System.IO.File]::Replace($temporary, $StartupShortcut, $backup)
      if (Test-Path -LiteralPath $backup) { [System.IO.File]::Delete($backup) }
    } else {
      [System.IO.File]::Move($temporary, $StartupShortcut)
    }
  } finally {
    if (Test-Path -LiteralPath $temporary) { [System.IO.File]::Delete($temporary) }
    if (Test-Path -LiteralPath $backup) { [System.IO.File]::Delete($backup) }
  }
}

function Test-StartupShortcutExact([object]$Shell, [string]$Target, [string]$Arguments, [string]$WorkingDirectory) {
  if (-not (Test-Path -LiteralPath $StartupShortcut -PathType Leaf)) { return $false }
  Assert-NoReparsePath $StartupShortcut
  $shortcut = $Shell.CreateShortcut($StartupShortcut)
  return (Test-IsControlledStartupShortcut $shortcut) -and
    ([string]$shortcut.TargetPath).Equals($Target, [System.StringComparison]::OrdinalIgnoreCase) -and
    [string]$shortcut.Arguments -ceq $Arguments -and
    ([string]$shortcut.WorkingDirectory).Equals($WorkingDirectory, [System.StringComparison]::OrdinalIgnoreCase)
}

function Write-Result([object]$Value) {
  if ($Json) { Write-Output ($Value | ConvertTo-Json -Compress -Depth 8) }
  else { $Value | Format-List | Out-String | Write-Output }
}

function Start-VerifiedWorkerSupervisor(
  [object]$Identity,
  [string]$StartupVerificationReceiptSha256,
  [string]$ResultStatus = "started"
) {
  if ($StartupVerificationReceiptSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "Worker full verification did not publish an exact supervisor prelaunch receipt"
  }
  $logRoot = Join-Path $RuntimeRoot "logs"
  [System.IO.Directory]::CreateDirectory($logRoot) | Out-Null
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $stdout = Join-Path $logRoot "worker-$stamp.stdout.log"
  $stderr = Join-Path $logRoot "worker-$stamp.stderr.log"
  $supervisorPath = Join-Path $Identity.ReleaseRoot "tools\worker-local-runtime-supervisor.mjs"
  try {
    $process = Start-Process -FilePath (Get-NodeExecutable) -ArgumentList @(
      "`"$supervisorPath`"", "--manifest", "`"$($Identity.Path)`"", "--approved-manifest-sha256", $Identity.Sha256
    ) -WorkingDirectory $Identity.ReleaseRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
  } catch {
    Remove-ExactSupervisorPrelaunchVerificationReceipt $StartupVerificationReceiptSha256
    throw
  }
  $supervisor = $null
  for ($attempt = 0; $attempt -lt 20 -and -not $supervisor; $attempt++) {
    Start-Sleep -Milliseconds 100
    $supervisor = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction SilentlyContinue
  }
  if (-not $supervisor -or -not (Test-AllowedTreeProcess $supervisor $Identity $process.Id)) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    Remove-ExactSupervisorPrelaunchVerificationReceipt $StartupVerificationReceiptSha256
    throw "Immutable Worker supervisor identity could not be established"
  }
  $receiptWritten = $false
  try {
    Write-ProcessReceipt $Identity $supervisor
    $receiptWritten = $true
    $readyStatus = $null
    $startupAnomalyAt = $null
    for ($attempt = 0; $attempt -lt 360; $attempt++) {
      Start-Sleep -Milliseconds 250
      $process.Refresh()
      if ($process.HasExited) { break }
      $workerPortOwners = @(Get-PortProcessIds $WorkerPort $WorkerHost)
      $helperPortOwners = @(Get-PortProcessIds $HelperPort $HelperHost)
      if ($workerPortOwners.Count -eq 0 -and $helperPortOwners.Count -eq 0) { continue }
      $readyStatus = Get-WorkerStatusInternal $Identity
      $decision = Get-StartupOwnershipDecision $readyStatus $startupAnomalyAt (Get-Date)
      $startupAnomalyAt = $decision.FirstAnomalyAt
      if ($decision.Action -eq "Ready" -or $decision.Action -eq "Fail") { break }
    }
    if (-not $readyStatus -or $readyStatus.State -ne "exact_release") {
      $process.Refresh()
      $logTail = Get-BoundedLogTail $stderr
      if ($process.HasExited) {
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $detail = if ($logTail) { "; stderr=$logTail" } else { "; stderr log is empty: $stderr" }
        throw "Immutable Worker supervisor exited before readiness: exit=$exitCode$detail"
      }
      $state = if ($readyStatus) { [string]$readyStatus.State } else { "unavailable" }
      $reason = if ($readyStatus -and -not [string]::IsNullOrWhiteSpace([string]$readyStatus.Reason)) { "; reason=$([string]$readyStatus.Reason)" } else { "" }
      $detail = if ($logTail) { "; stderr=$logTail" } else { "" }
      throw "Immutable Worker/helper did not establish stable exact 3000/5791 ownership: state=$state$reason$detail"
    }
    $helperHealth = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5791/health" -TimeoutSec 15
    if (-not $helperHealth -or $helperHealth.ok -ne $true) { throw "Immutable helper /health did not report ready" }
    return [ordered]@{
      status = $ResultStatus
      version = $StatusVersion
      releaseId = $Identity.ReleaseId
      manifestSha256 = $Identity.Sha256
      supervisorProcessId = $process.Id
    }
  } catch {
    $startError = $_
    try { Remove-ExactSupervisorPrelaunchVerificationReceipt $StartupVerificationReceiptSha256 } catch {}
    try {
      $owned = Get-WorkerStatusInternal $Identity
      if ($owned.State -in @("starting_exact_release", "exact_release")) {
        Stop-ExactWorkerSnapshot $owned
      } else {
        $currentSupervisor = Get-CimInstance Win32_Process -Filter "ProcessId = $($supervisor.ProcessId)" -ErrorAction SilentlyContinue
        if ($currentSupervisor -and (Get-CreationIdentity $currentSupervisor) -ceq (Get-CreationIdentity $supervisor) -and
            (Test-AllowedTreeProcess $currentSupervisor $Identity $supervisor.ProcessId)) {
          Stop-Process -Id $currentSupervisor.ProcessId -Force -ErrorAction SilentlyContinue
        }
      }
    } catch {}
    if ($receiptWritten) { try { Remove-ExactProcessReceipt $Identity } catch {} }
    throw $startError
  }
}

function Read-WorkerSystemMaintenance {
  $path = Join-Path $FixedDjangoRuntimeRoot "run\system-maintenance.json"
  Assert-NoReparsePath $path -AllowMissingLeaf
  if (-not (Test-Path -LiteralPath $path)) { return $null }
  $entry = Get-Item -LiteralPath $path -Force
  if ($entry.PSIsContainer -or $entry.Length -gt 4096) { throw "Invalid maintenance state file" }
  try { $record = [IO.File]::ReadAllText($path) | ConvertFrom-Json -ErrorAction Stop }
  catch { throw "Unreadable system maintenance state; refusing startup" }
  if ($record.version -cne "teruisi-system-maintenance-v1" -or [string]$record.id -cnotmatch "^[0-9a-f]{32}$" -or
      [IO.Path]::GetFullPath([string]$record.runtimeRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($FixedDjangoRuntimeRoot).TrimEnd('\')) {
    throw "Invalid system maintenance binding; refusing startup"
  }
  return $record
}

function Assert-WorkerMaintenanceInactive {
  $record = Read-WorkerSystemMaintenance
  if ($record) { throw "System maintenance is active: $($record.id). Start and restart are disabled until ExitMaintenance." }
}

function Invoke-DjangoLifecycleAction([string]$ControlAction, [string]$OperationId = "", [switch]$PreservePostgres) {
  if ($ControlAction -in @("BeginMaintenance", "EndMaintenance")) {
    if ($OperationId -cnotmatch "^[0-9a-f]{32}$") { throw "Invalid maintenance operation id" }
    # A reviewed source checkout can establish the gate for its first upgrade;
    # immutable Worker releases use the already-deployed Django controller.
    $maintenanceLibrary = Join-Path $PSScriptRoot "django-local-service.ps1"
    if (-not (Test-Path -LiteralPath $maintenanceLibrary -PathType Leaf)) { $maintenanceLibrary = $DjangoService }
    Assert-NoReparsePath $maintenanceLibrary
    # Keep both mutexes on this same thread: Worker -> Django. Maintenance
    # mutation has no separate CLI path that could bypass the Worker mutex.
    # Library scope prevents Django variables from changing Worker bindings.
    & {
      param($maintenanceController, $maintenanceRuntime, $maintenanceOperation, $maintenanceOperationId, $preserveDatabase)
      $previousLibraryMode = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
      try {
        $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
        . $maintenanceController -RuntimeRoot $maintenanceRuntime -MaintenanceId $maintenanceOperationId
        $KeepPostgres = [bool]$preserveDatabase
        # Dot-sourcing creates the base controller's ValidateSet on Action.
        # The internal library operation is deliberately not a public CLI verb.
        Remove-Variable -Name Action -Scope Local -Force
        $Action = $maintenanceOperation
        Invoke-WithServiceMutex {
          if ($maintenanceOperation -eq "BeginMaintenance") { Begin-SystemMaintenance }
          else { End-SystemMaintenance }
        }
      } finally { $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = $previousLibraryMode }
    } $maintenanceLibrary $FixedDjangoRuntimeRoot $ControlAction $OperationId ([bool]$PreservePostgres)
    return
  }
  $result = Invoke-DjangoStartProcess $DjangoService $ControlAction -PreservePostgres:$PreservePostgres
  if ([int]$result.ExitCode -ne 0) { throw "Django $ControlAction failed: $($result.StdoutTail) $($result.StderrTail)" }
}

function Invoke-WorkerSystemStop([object]$identity, [switch]$WithBackend, [switch]$PreservePostgres) {
  $result = Stop-WorkerOnly $identity
  if ($WithBackend) {
    Invoke-DjangoLifecycleAction "Stop" -PreservePostgres:$PreservePostgres
    $result["backendStopped"] = $true
  }
  return $result
}

function Invoke-WorkerFullRestart([object]$identity) {
  Assert-WorkerMaintenanceInactive
  [void](Invoke-WorkerSystemStop $identity -WithBackend)
  $result = Invoke-WorkerSystemStart $identity
  $result["status"] = "restarted"
  $result["backendRestarted"] = $true
  return $result
}

function Get-JoinedWorkerStartResult([object]$identity) {
  # Joining is observation only: never restart after a concurrent Stop, failed
  # Start, or maintenance operation. The existing owner decides all mutations.
  Assert-WorkerMaintenanceInactive
  $status = Get-WorkerStatusInternal $identity
  if ($status.State -cne "exact_release") {
    throw "Concurrent lifecycle operation finished without a ready exact Worker; state=$($status.State)"
  }
  $readiness = Get-DjangoSystemReadiness
  if (-not $readiness.Ready) {
    throw "Concurrent startup did not make the full backend ready; missing=$($readiness.Missing -join ',')"
  }
  $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3000/" -TimeoutSec 10 -MaximumRedirection 0
  if ($page.StatusCode -ne 200) { throw "Concurrent startup did not make the Worker homepage ready" }
  $helperHealth = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5791/health" -TimeoutSec 15
  if (-not $helperHealth -or $helperHealth.ok -ne $true) { throw "Concurrent startup did not make the helper ready" }
  return ([ordered]@{ status = "already_running"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256 })
}

function Invoke-WorkerSystemStart([object]$identity) {
    Assert-WorkerMaintenanceInactive
    $status = Get-WorkerStatusInternal $identity
    if ($status.State -eq "starting_exact_release") { throw "The exact immutable Worker release is already starting" }
    $preflightStaleReceipt = $status.State -eq "stale_or_invalid_receipt" -and -not $status.Supervisor -and $status.Receipt
    if ($status.State -notin @("stopped", "exact_release") -and -not $preflightStaleReceipt) {
      throw "Port 3000/5791 or process receipt is unknown/ambiguous; refusing takeover"
    }

    Ensure-DjangoSystemReady

    $status = Get-WorkerStatusInternal $identity
    if ($status.State -eq "exact_release") {
      Start-SystemDingTalkReceiver
      return ([ordered]@{ status = "already_running"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256 })
    }
    if ($status.State -eq "starting_exact_release") { throw "The exact immutable Worker release is already starting" }
    $repairStaleReceipt = $status.State -eq "stale_or_invalid_receipt" -and -not $status.Supervisor -and $status.Receipt
    if ($status.State -ne "stopped" -and -not $repairStaleReceipt) {
      throw "Worker ownership changed while Django was starting; refusing takeover"
    }
    if ($repairStaleReceipt) {
      Remove-ExactProcessReceipt $identity
      $status = Get-WorkerStatusInternal $identity
      if ($status.State -ne "stopped") {
        throw "Validated stale Worker receipt was cleared but the service did not stabilize as stopped"
      }
    }
    $releaseVerification = Invoke-ReleaseVerification $identity "stopped" -WriteSupervisorPrelaunchReceipt
    $startupVerificationReceiptSha256 = [string]$releaseVerification.supervisorPrelaunchReceiptSha256
    if ($startupVerificationReceiptSha256 -cnotmatch "^[0-9a-f]{64}$") {
      throw "Worker full verification did not publish an exact supervisor prelaunch receipt"
    }
    $startResult = Start-VerifiedWorkerSupervisor $identity $startupVerificationReceiptSha256 "started"
    Start-SystemDingTalkReceiver
    return $startResult
}

function Stop-WorkerOnly([object]$identity) {
    $status = Get-WorkerStatusInternal $identity
    if ($status.State -eq "stopped") {
      return ([ordered]@{ status = "already_stopped"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256 })
    }
    if ($status.State -eq "stale_or_invalid_receipt" -and -not $status.Supervisor -and $status.Receipt) {
      Remove-ExactProcessReceipt $identity
      return ([ordered]@{ status = "stale_receipt_cleared"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256 })
    }
    if ($status.State -notin @("starting_exact_release", "exact_release")) { throw "Port/process receipt is not owned by the exact immutable release; refusing stop" }
    Stop-ExactWorkerSnapshot $status
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
      if ((Get-PortProcessIds $WorkerPort $WorkerHost).Count -eq 0 -and
          (Get-PortProcessIds $HelperPort $HelperHost).Count -eq 0) { break }
      Start-Sleep -Milliseconds 250
    }
    if ((Get-PortProcessIds $WorkerPort $WorkerHost).Count -gt 0 -or
        (Get-PortProcessIds $HelperPort $HelperHost).Count -gt 0) {
      throw "Immutable Worker/helper did not release ports 3000/5791 within 15 seconds"
    }
    Remove-ExactProcessReceipt $identity
    return ([ordered]@{ status = "stopped"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256 })
}

if ($FunctionsOnly) { return }

Assert-FixedRuntimeRoot

try {
  if ($Action -eq "Deploy") {
    if (-not $SourceRoot) { $SourceRoot = $FixedSourceRoot }
    if (-not $AllowTestRuntimeRoot -and -not ([System.IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')).Equals(
      [System.IO.Path]::GetFullPath($FixedSourceRoot).TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase
    )) { throw "Worker deployment source must remain fixed at $FixedSourceRoot" }
    foreach ($required in @("DevVarsSource", "PersistRoot", "SourceD1Path")) {
      if ([string]::IsNullOrWhiteSpace((Get-Variable -Name $required -ValueOnly))) { throw "$required is required for Deploy" }
    }
    if (-not $AllowTestRuntimeRoot -and (
      -not ([System.IO.Path]::GetFullPath($DevVarsSource)).Equals([System.IO.Path]::GetFullPath($FixedDevVarsSource), [System.StringComparison]::OrdinalIgnoreCase) -or
      -not ([System.IO.Path]::GetFullPath($PersistRoot).TrimEnd('\')).Equals([System.IO.Path]::GetFullPath($FixedPersistRoot).TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)
    )) { throw "Worker deployment must use the main checkout .dev.vars and exact Wrangler persist root under $FixedProtectedRoot" }
    $portIds = @(Get-PortProcessIds $WorkerPort $WorkerHost)
    $helperPortIds = @(Get-PortProcessIds $HelperPort $HelperHost)
    if ($portIds.Count -gt 0 -or $helperPortIds.Count -gt 0) { throw "Ports 3000 and 5791 must be stopped before immutable Worker deployment" }
    $existingManifest = Get-CurrentManifestPath
    $existingIdentity = if ($existingManifest) { Get-ManifestIdentity $existingManifest } else { $null }
    if ($existingIdentity) {
      throw "Worker current pointer is already published; ordinary Deploy is permanently disabled"
    } elseif (Test-Path -LiteralPath (Join-Path $RuntimeRoot "state\worker-process.json")) {
      throw "An unbound Worker process receipt exists; refusing deployment"
    }
    $args = @(
      $ReleaseTool, "deploy", "--source-root", $SourceRoot, "--runtime-root", $RuntimeRoot,
      "--dev-vars-source", $DevVarsSource, "--persist-root", $PersistRoot, "--source-d1-path", $SourceD1Path,
      "--json"
    )
    if ($AllowTestRuntimeRoot) { $args += "--allow-test-runtime-root" }
    $outerErrorActionPreference = $ErrorActionPreference
    try {
      $ErrorActionPreference = "Continue"
      $output = @(& (Get-NodeExecutable) @args 2>&1)
      $nodeExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $outerErrorActionPreference
    }
    $outputText = (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    if ($nodeExitCode -ne 0) { throw "Worker release tool failed: exit=$nodeExitCode; $outputText" }
    Write-Output $outputText
    exit 0
  }

  $resolvedManifest = Get-CurrentManifestPath
  $identity = if ($resolvedManifest) { Get-ManifestIdentity $resolvedManifest } else { $null }

  if ($Action -eq "MaintenanceStatus") { Write-Result (@{ maintenance = Read-WorkerSystemMaintenance }); exit 0 }

  if ($Action -eq "Status") {
    Write-Result (Get-PublicStatus (Get-WorkerStatusInternal $identity))
    exit 0
  }
  if (-not $identity) { throw "No immutable Worker release is installed" }

  if ($Action -eq "Verify") {
    Write-Result (Invoke-ReleaseVerification $identity "stopped-or-exact-release")
    exit 0
  }

  if ($Action -eq "Start") {
    $startResult = if ($JoinedConcurrentLifecycle) { Get-JoinedWorkerStartResult $identity } else { Invoke-WorkerSystemStart $identity }
    Write-Result $startResult
    exit 0
  }

  if ($Action -eq "Restart") {
    Assert-WorkerMaintenanceInactive
    $hotRestartTimer = [Diagnostics.Stopwatch]::StartNew()
    if (-not (Test-DjangoAggregateStatusSupported)) {
      throw "Hot restart requires the aggregate-status Django runtime deployment"
    }
    $readiness = Get-DjangoSystemReadiness
    if (-not $readiness.Ready) {
      throw "Hot restart requires the Django/PostgreSQL stack to remain ready; missing=$($readiness.Missing -join ',')"
    }
    if ($hotRestartTimer.ElapsedMilliseconds -ge ($HotRestartBudgetMilliseconds - 3000)) {
      throw "Hot restart preflight exhausted its budget before changing the Worker process"
    }
    $status = Get-WorkerStatusInternal $identity
    if ($status.State -ne "exact_release") {
      throw "Hot restart requires the exact immutable Worker release to be running"
    }
    $oldSupervisorId = [int]$status.Supervisor.ProcessId
    $oldWorkerPortId = if ($status.PortIds.Count -eq 1) { [int]$status.PortIds[0] } else { $null }
    $childDeadline = [DateTime]::UtcNow.AddMilliseconds(
      [Math]::Max(1000, $HotRestartBudgetMilliseconds - $hotRestartTimer.ElapsedMilliseconds - 3000)
    )
    $readyStatus = Restart-ExactWorkerChild $status $childDeadline
    $remainingMilliseconds = $HotRestartBudgetMilliseconds - $hotRestartTimer.ElapsedMilliseconds
    if ($remainingMilliseconds -lt 1000) { throw "Hot restart exhausted its 30-second budget before HTTP readiness" }
    $pageTimeoutSeconds = [Math]::Max(1, [Math]::Min(10, [Math]::Floor($remainingMilliseconds / 1000)))
    $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3000/" -TimeoutSec $pageTimeoutSeconds
    if ($page.StatusCode -ne 200) { throw "Hot-restarted Worker homepage did not report HTTP 200" }
    $remainingMilliseconds = $HotRestartBudgetMilliseconds - $hotRestartTimer.ElapsedMilliseconds
    if ($remainingMilliseconds -lt 1000) { throw "Hot restart exhausted its 30-second budget before helper readiness" }
    $helperTimeoutSeconds = [Math]::Max(1, [Math]::Min(5, [Math]::Floor($remainingMilliseconds / 1000)))
    $helperHealth = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5791/health" -TimeoutSec $helperTimeoutSeconds
    if (-not $helperHealth -or $helperHealth.ok -ne $true) { throw "Immutable helper /health did not remain ready" }
    $hotRestartTimer.Stop()
    if ($hotRestartTimer.ElapsedMilliseconds -ge $HotRestartBudgetMilliseconds) {
      throw "Hot restart exceeded its 30-second wall-clock budget"
    }
    Write-Result ([ordered]@{
      status = "restarted"
      version = $StatusVersion
      releaseId = $identity.ReleaseId
      manifestSha256 = $identity.Sha256
      supervisorProcessId = $oldSupervisorId
      previousWorkerPortProcessId = $oldWorkerPortId
      workerPortProcessId = if ($readyStatus.PortIds.Count -eq 1) { [int]$readyStatus.PortIds[0] } else { $null }
      backendRestarted = $false
      helperRestarted = $false
      elapsedMilliseconds = [int64]$hotRestartTimer.ElapsedMilliseconds
    })
    exit 0
  }

  if ($Action -eq "Stop") { Write-Result (Invoke-WorkerSystemStop $identity -WithBackend:$IncludeBackend); exit 0 }
  if ($Action -eq "RestartFull") { Write-Result (Invoke-WorkerFullRestart $identity); exit 0 }
  if ($Action -eq "EnterMaintenance") {
    if (-not $MaintenanceId) { $MaintenanceId = [Guid]::NewGuid().ToString("N") }
    Invoke-DjangoLifecycleAction "BeginMaintenance" $MaintenanceId -PreservePostgres:$KeepPostgres
    try { $stopped = Invoke-WorkerSystemStop $identity -WithBackend -PreservePostgres:$KeepPostgres }
    catch { throw "Maintenance $MaintenanceId remains active after stop failure: $($_.Exception.Message)" }
    Write-Result ([ordered]@{ status = "maintenance"; maintenanceId = $MaintenanceId; backendStopped = $true; postgresPreserved = [bool]$KeepPostgres })
    exit 0
  }
  if ($Action -eq "ExitMaintenance") {
    if ((Get-WorkerStatusInternal $identity).State -ne "stopped") { throw "Worker must be stopped before ending maintenance" }
    Invoke-DjangoLifecycleAction "EndMaintenance" $MaintenanceId
    Write-Result ([ordered]@{ status = "maintenance_ended"; maintenanceId = $MaintenanceId })
    exit 0
  }

  $shell = New-Object -ComObject WScript.Shell
  $expectedTarget = (Get-Command "powershell.exe").Source
  $servicePath = Join-Path $identity.ReleaseRoot "tools\worker-local-service.ps1"
  $expectedArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$servicePath`" -Action Start -ManifestPath `"$($identity.Path)`""
  if ($Action -eq "InstallStartup") {
    if (Test-Path -LiteralPath $StartupShortcut -PathType Leaf) {
      Assert-NoReparsePath $StartupShortcut
      $existing = $shell.CreateShortcut($StartupShortcut)
      if (-not (Test-IsControlledStartupShortcut $existing)) { throw "Refusing to overwrite an unverified Startup shortcut" }
      if (Test-StartupShortcutExact $shell $expectedTarget $expectedArguments $identity.ReleaseRoot) {
        Write-Result ([ordered]@{ status = "already_installed"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256; startupVerified = $true })
        exit 0
      }
    }
    Save-StartupShortcutAtomic $shell $expectedTarget $expectedArguments $identity.ReleaseRoot
    if (-not (Test-StartupShortcutExact $shell $expectedTarget $expectedArguments $identity.ReleaseRoot)) {
      throw "Startup shortcut write-back did not bind the exact effective head release"
    }
    Write-Result ([ordered]@{ status = "installed"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256; startupVerified = $true })
    exit 0
  }
  if ($Action -eq "VerifyStartup") {
    if (-not (Test-StartupShortcutExact $shell $expectedTarget $expectedArguments $identity.ReleaseRoot)) {
      throw "Startup shortcut is not bound to the exact effective head release"
    }
    Write-Result ([ordered]@{ status = "verified"; version = $StatusVersion; releaseId = $identity.ReleaseId; manifestSha256 = $identity.Sha256; startupVerified = $true })
    exit 0
  }
  if ($Action -eq "RemoveStartup") {
    if (-not (Test-Path -LiteralPath $StartupShortcut -PathType Leaf)) {
      Write-Result ([ordered]@{ status = "already_removed"; version = $StatusVersion; releaseId = $identity.ReleaseId })
      exit 0
    }
    Assert-NoReparsePath $StartupShortcut
    $existing = $shell.CreateShortcut($StartupShortcut)
    if (-not (Test-IsControlledStartupShortcut $existing)) { throw "Refusing to remove an unverified Startup shortcut" }
    [System.IO.File]::Delete($StartupShortcut)
    Write-Result ([ordered]@{ status = "removed"; version = $StatusVersion; releaseId = $identity.ReleaseId })
    exit 0
  }
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
} finally {
  if ($ServiceMutex -and $lockAcquired) { $ServiceMutex.ReleaseMutex(); $ServiceMutex.Dispose() }
}
