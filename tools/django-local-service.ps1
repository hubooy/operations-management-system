[CmdletBinding()]
param(
  [ValidateSet(
    "Configure", "PrepareApp", "DeployApp", "HardenAcl", "Start", "Stop", "Status", "AggregateStatus",
    "ProvisionFinanceRoles", "RollbackApp",
    "StartFinance", "StopFinance", "FinanceStatus",
    "InstallStartup", "RemoveStartup", "PlanSalesD1Retirement", "RetireSalesD1",
    "CreateSalesCutoverSmokeReceipt", "MaintenanceStatus"
  )]
  [string]$Action = "Status",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$ErpSourceD1 = "",
  [string]$CutoverId = "",
  [string]$AttestationPath = "",
  [string]$AttestationSha256 = "",
  [string]$ApprovedPlanId = "",
  [string]$SmokeReceiptPath = "",
  [string]$SmokeReceiptSha256 = "",
  [string]$SupervisorExpectedDesiredStateSha256 = "",
  [string]$MaintenanceId = "",
  [string]$PreparedAppId = "",
  [string]$PreparedAppSha256 = "",
  [switch]$KeepPostgres,
  [switch]$Json,
  [switch]$Execute
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = [Text.UTF8Encoding]::new($false)
$ExecutionRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $ExecutionRoot "backend"
$DingTalkReplenishmentConfigSource = Join-Path $ExecutionRoot "config\dingtalk-replenishment.json"
$InventoryWarehouseMappingConfigSource = Join-Path $ExecutionRoot "config\inventory-warehouse-mapping.json"
$InstalledAppRoot = Join-Path $RuntimeRoot "app"
$InstalledScriptPath = Join-Path $InstalledAppRoot "tools\django-local-service.ps1"
$InstalledNetshopScriptPath = Join-Path $InstalledAppRoot "tools\django-netshop-service.ps1"
$NetshopStartupEnabledPath = Join-Path $RuntimeRoot "netshop-service-enabled.json"
$InstalledMarketScriptPath = Join-Path $InstalledAppRoot "tools\django-market-service.ps1"
$MarketStartupEnabledPath = Join-Path $RuntimeRoot "market-service-enabled.json"
$InstalledProductsScriptPath = Join-Path $InstalledAppRoot "tools\django-products-service.ps1"
$ProductsStartupEnabledPath = Join-Path $RuntimeRoot "products-service-enabled.json"
$InstalledWorkflowScriptPath = Join-Path $InstalledAppRoot "tools\django-workflow-service.ps1"
$WorkflowStartupEnabledPath = Join-Path $RuntimeRoot "workflow-service-enabled.json"
$InstalledInventoryScriptPath = Join-Path $InstalledAppRoot "tools\django-inventory-service.ps1"
$InventoryStartupEnabledPath = Join-Path $RuntimeRoot "inventory-service-enabled.json"
$InstalledCustomerServiceScriptPath = Join-Path $InstalledAppRoot "tools\django-customer-service.ps1"
$CustomerServiceStartupEnabledPath = Join-Path $RuntimeRoot "customer-service-enabled.json"
$InstalledAccessControlScriptPath = Join-Path $InstalledAppRoot "tools\django-access-control.ps1"
$AccessControlStartupEnabledPath = Join-Path $RuntimeRoot "access-control-enabled.json"
$InstalledAiScriptPath = Join-Path $InstalledAppRoot "tools\django-ai.ps1"
$AiStartupEnabledPath = Join-Path $RuntimeRoot "ai-enabled.json"
$InstalledErpReferenceScriptPath = Join-Path $InstalledAppRoot "tools\django-erp-reference.ps1"
$ErpReferenceStartupEnabledPath = Join-Path $RuntimeRoot "erp-reference-enabled.json"
$InstalledBiScriptPath = Join-Path $InstalledAppRoot "tools\django-bi-service.ps1"
$BiStartupEnabledPath = Join-Path $RuntimeRoot "bi-service-enabled.json"
$DeploymentManifestPath = Join-Path $InstalledAppRoot "deployment.json"
$ConfigPath = Join-Path $RuntimeRoot "service.json"
$CredentialPath = Join-Path $RuntimeRoot "secrets\credentials.dpapi.json"
$PostgresBin = Join-Path $RuntimeRoot "postgresql-17.11\bin"
$Psql = Join-Path $PostgresBin "psql.exe"
$PostgresData = Join-Path $RuntimeRoot "postgres-data"
$Python = Join-Path $RuntimeRoot "venv\Scripts\python.exe"
$Waitress = Join-Path $RuntimeRoot "venv\Scripts\waitress-serve.exe"
$LogDirectory = Join-Path $RuntimeRoot "logs"
$RunDirectory = Join-Path $RuntimeRoot "run"
$MaintenancePath = Join-Path $RunDirectory "system-maintenance.json"
$DjangoReaderPidPath = Join-Path $RunDirectory "django-reader.pid.json"
$DjangoWriterPidPath = Join-Path $RunDirectory "django-writer.pid.json"
$DjangoFinanceReaderPidPath = Join-Path $RunDirectory "django-finance-reader.pid.json"
$DjangoFinanceWriterPidPath = Join-Path $RunDirectory "django-finance-writer.pid.json"
$DjangoNetshopReaderPidPath = Join-Path $RunDirectory "django-netshop-reader.pid.json"
$DjangoNetshopWriterPidPath = Join-Path $RunDirectory "django-netshop-writer.pid.json"
$DjangoMarketReaderPidPath = Join-Path $RunDirectory "django-market-reader.pid.json"
$DjangoMarketWriterPidPath = Join-Path $RunDirectory "django-market-writer.pid.json"
$DjangoProductsReaderPidPath = Join-Path $RunDirectory "django-products-reader.pid.json"
$DjangoProductsWriterPidPath = Join-Path $RunDirectory "django-products-writer.pid.json"
$DjangoWorkflowReaderPidPath = Join-Path $RunDirectory "django-workflow-reader.pid.json"
$DjangoWorkflowWriterPidPath = Join-Path $RunDirectory "django-workflow-writer.pid.json"
$DjangoInventoryReaderPidPath = Join-Path $RunDirectory "django-inventory-reader.pid.json"
$DjangoInventoryWriterPidPath = Join-Path $RunDirectory "django-inventory-writer.pid.json"
$DjangoCustomerServiceReaderPidPath = Join-Path $RunDirectory "django-customer-service-reader.pid.json"
$DjangoCustomerServiceWriterPidPath = Join-Path $RunDirectory "django-customer-service-writer.pid.json"
$DjangoAccessControlReaderPidPath = Join-Path $RunDirectory "django-access-control-reader.pid.json"
$DjangoAccessControlWriterPidPath = Join-Path $RunDirectory "django-access-control-writer.pid.json"
$DjangoAiReaderPidPath = Join-Path $RunDirectory "django-ai-reader.pid.json"
$DjangoAiWriterPidPath = Join-Path $RunDirectory "django-ai-writer.pid.json"
$DjangoErpReferenceReaderPidPath = Join-Path $RunDirectory "django-erp-reference-reader.pid.json"
$DjangoErpReferenceWriterPidPath = Join-Path $RunDirectory "django-erp-reference-writer.pid.json"
$DjangoBiReaderPidPath = Join-Path $RunDirectory "django-bi-reader.pid.json"
$ErpReferenceSyncPidPath = Join-Path $RunDirectory "erp-reference-sync.pid.json"
$DjangoSupervisorPidPath = Join-Path $RunDirectory "django-supervisor.pid.json"
$SupervisorDesiredStatePath = Join-Path $RunDirectory "django-supervisor-desired-state.json"
$RetirementAuditDirectory = Join-Path $RuntimeRoot "audits\sales-retirement"
$RetirementOperator = Join-Path $InstalledAppRoot "tools\sales-d1-retirement.ts"
$RetirementMigration = Join-Path $InstalledAppRoot "drizzle\0092_sales_domain_retirement.sql"
$Node = Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "nodejs\node.exe"
$LauncherLogPath = Join-Path $LogDirectory "launcher.jsonl"
$DjangoReaderHealthUrl = "http://127.0.0.1:8001/health/ready"
$DjangoWriterHealthUrl = "http://127.0.0.1:8002/health/ready"
$DjangoFinanceReaderHealthUrl = "http://127.0.0.1:8011/health/ready"
$DjangoFinanceWriterHealthUrl = "http://127.0.0.1:8012/health/ready"
$DjangoCustomerServiceReaderHealthUrl = "http://127.0.0.1:8071/health/ready"
$DjangoCustomerServiceWriterHealthUrl = "http://127.0.0.1:8072/health/ready"
$DjangoAccessControlReaderHealthUrl = "http://127.0.0.1:8101/health/ready"
$DjangoAccessControlWriterHealthUrl = "http://127.0.0.1:8102/health/ready"
$DjangoAiReaderHealthUrl = "http://127.0.0.1:8111/health/ready"
$DjangoAiWriterHealthUrl = "http://127.0.0.1:8112/health/ready"
$DjangoBiReaderHealthUrl = "http://127.0.0.1:8081/health/ready"
$StartupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "TERUISI Django Sales.lnk"
$RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$ReaderStatementTimeoutMs = 7000
$WriterStatementTimeoutMs = 900000
$MaxHeaderBytes = 32768
$ReaderMaxBodyBytes = 1048576
$WriterMaxBodyBytes = 16777216
$ErpReferenceSyncIntervalSeconds = 15
$MinimumPostgresConnectionsForFullStack = 120
$ApplicationFingerprintAlgorithm = "relative-path-file-sha256-ordinal-v2"
$OrchestratedLifecycleAclContextVariable = "TERUISI_DJANGO_ORCHESTRATED_LIFECYCLE_ACL_CONTEXT"
$OrchestratedLifecycleAclContextVersion = "teruisi-django-orchestrated-lifecycle-acl-v1"
$OrchestratedLifecycleAclContextMaxAgeMilliseconds = 900000

function Get-CanonicalPath([string]$Path) {
  return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-FullyQualifiedPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  try {
    [void][IO.Path]::GetFullPath($Path)
  } catch {
    return $false
  }
  if ([IO.Path]::DirectorySeparatorChar -eq "\") {
    return $Path -match "^[A-Za-z]:[\\/]" -or
      $Path -match "^[\\/]{2}[^\\/]+[\\/]+[^\\/]+(?:[\\/]|$)"
  }
  return $Path.StartsWith("/", [StringComparison]::Ordinal)
}

function Assert-RuntimeChildPath([string]$Path) {
  $root = Get-CanonicalPath $RuntimeRoot
  $candidate = Get-CanonicalPath $Path
  if ($candidate -eq $root -or -not $candidate.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝操作运行目录之外的路径：$candidate"
  }
  return $candidate
}

function Get-Sha256Text([string]$Value) {
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $algorithm.Dispose()
  }
}

function Get-FileSha256([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "missing" }
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
  } finally {
    $algorithm.Dispose()
    $stream.Dispose()
  }
}

function Protect-LogText([string]$Value) {
  if ($null -eq $Value) { return "" }
  return [regex]::Replace($Value, "postgres(?:ql)?://[^@\s]+@", "postgresql://[redacted]@")
}

function ConvertTo-PythonBase64Launcher([string]$Code, [string]$SourceName) {
  if ([string]::IsNullOrEmpty($Code)) {
    throw "Python launcher code must not be empty"
  }
  if ([string]::IsNullOrWhiteSpace($SourceName) -or $SourceName -cnotmatch "^[A-Za-z0-9._-]{1,128}$") {
    throw "Python launcher source name is invalid"
  }
  $encodedCode = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code))
  $launcher = (
    "import base64;exec(compile(base64.b64decode('$encodedCode')," +
    "'$SourceName','exec'))"
  )
  if ($launcher -cmatch "[^\x00-\x7f]" -or $launcher.Contains("`r") -or
      $launcher.Contains("`n")) {
    throw "Python launcher must be one ASCII line"
  }
  return $launcher
}

function Get-BoundedNativeDiagnostic([object[]]$Output) {
  $records = @($Output)
  $maxRecords = 32
  $maxCharacters = 4096
  $builder = [Text.StringBuilder]::new()
  $truncated = $records.Count -gt $maxRecords
  $recordLimit = [Math]::Min($records.Count, $maxRecords)
  $capturedRecords = 0
  for ($index = 0; $index -lt $recordLimit; $index++) {
    if ($builder.Length -ge $maxCharacters) {
      $truncated = $true
      break
    }
    if ($builder.Length -gt 0) { [void]$builder.Append("`n") }
    $remaining = $maxCharacters - $builder.Length
    if ($remaining -le 0) {
      $truncated = $true
      break
    }
    $line = Protect-LogText ([string]$records[$index])
    $line = [regex]::Replace(
      $line,
      "(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|authorization)(\s*[:=]\s*)[^\s;,]+",
      "credential`$1[redacted]"
    )
    $line = [regex]::Replace(
      $line,
      "(?i)\b[a-z][a-z0-9+.-]*://[^/@\s:]+:[^@\s]+@",
      "uri://[redacted]@"
    )
    $capturedRecords = $index + 1
    if ($line.Length -gt $remaining) {
      [void]$builder.Append($line.Substring(0, $remaining))
      $truncated = $true
      break
    }
    [void]$builder.Append($line)
  }
  return [pscustomobject][ordered]@{
    OutputRecordCount = [int]$records.Count
    CapturedRecordCount = [int]$capturedRecords
    OutputTruncated = [bool]$truncated
    OutputSha256 = Get-Sha256Text $builder.ToString()
  }
}

function Invoke-BoundedNativeProcess(
  [string]$Executable,
  [string[]]$Arguments,
  [string]$WorkingDirectory = ""
) {
  $output = @()
  $nativeExitCode = $null
  $launchFailed = $false
  if ([string]::IsNullOrWhiteSpace($Executable) -or
      -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    $output = @("native executable unavailable")
    $launchFailed = $true
  } elseif (-not [string]::IsNullOrWhiteSpace($WorkingDirectory) -and
      -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
    $output = @("native working directory unavailable")
    $launchFailed = $true
  } else {
    $outerErrorActionPreference = $ErrorActionPreference
    $outerLastExitCode = $global:LASTEXITCODE
    $locationPushed = $false
    try {
      if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        Push-Location -LiteralPath $WorkingDirectory
        $locationPushed = $true
      }
      # Windows PowerShell 5 promotes native stderr to ErrorRecord. Capture it
      # under Continue, then decide success only from the exact native exit code.
      $ErrorActionPreference = "Continue"
      $global:LASTEXITCODE = $null
      try {
        $output = @(& $Executable @Arguments 2>&1)
        $nativeExitCode = $global:LASTEXITCODE
      } catch {
        $output = @($_)
        $nativeExitCode = $null
        $launchFailed = $true
      }
    } finally {
      $ErrorActionPreference = $outerErrorActionPreference
      $global:LASTEXITCODE = $outerLastExitCode
      if ($locationPushed) { Pop-Location }
    }
  }
  $exitCode = if ($null -eq $nativeExitCode) { -1 } else { [int]$nativeExitCode }
  return [pscustomobject][ordered]@{
    ExitCode = $exitCode
    LaunchFailed = [bool]$launchFailed
    Output = @($output)
    Diagnostic = Get-BoundedNativeDiagnostic $output
  }
}

function Get-NativeFailureSummary([object]$Run) {
  return (
    "exitCode=$([int]$Run.ExitCode); " +
    "launchFailed=$([bool]$Run.LaunchFailed); " +
    "outputRecordCount=$([int]$Run.Diagnostic.OutputRecordCount); " +
    "capturedRecordCount=$([int]$Run.Diagnostic.CapturedRecordCount); " +
    "outputTruncated=$([bool]$Run.Diagnostic.OutputTruncated); " +
    "outputSha256=$([string]$Run.Diagnostic.OutputSha256)"
  )
}

function ConvertFrom-UniqueNativeJson([object]$Run, [string]$Operation) {
  if ($Run.ExitCode -ne 0) {
    throw "$Operation 失败（$(Get-NativeFailureSummary $Run)）"
  }
  $payloads = [Collections.Generic.List[object]]::new()
  foreach ($record in @($Run.Output)) {
    $text = [string]$record
    if ([string]::IsNullOrWhiteSpace($text)) { continue }
    try {
      $candidate = $text | ConvertFrom-Json -ErrorAction Stop
    } catch {
      continue
    }
    if ($candidate -is [pscustomobject]) {
      [void]$payloads.Add($candidate)
    }
  }
  if ($payloads.Count -ne 1) {
    throw "$Operation 未返回唯一 JSON 对象（$(Get-NativeFailureSummary $Run)）"
  }
  return $payloads[0]
}

function Write-NativeDiagnosticLog(
  [string]$Path,
  [string]$Operation,
  [object]$Run
) {
  $line = "operation=$Operation; $(Get-NativeFailureSummary $Run)"
  [IO.File]::AppendAllText($Path, $line + [Environment]::NewLine, $Utf8NoBom)
}

function Rotate-LauncherLog {
  if (-not (Test-Path -LiteralPath $LauncherLogPath -PathType Leaf)) { return }
  if ((Get-Item -LiteralPath $LauncherLogPath).Length -lt 5MB) { return }
  for ($index = 3; $index -ge 1; $index--) {
    $source = "$LauncherLogPath.$index"
    $target = "$LauncherLogPath.$($index + 1)"
    if (Test-Path -LiteralPath $source) {
      if ($index -eq 3) {
        Remove-Item -LiteralPath $source -Force
      } else {
        Move-Item -LiteralPath $source -Destination $target -Force
      }
    }
  }
  Move-Item -LiteralPath $LauncherLogPath -Destination "$LauncherLogPath.1" -Force
}

function Write-LauncherEvent([string]$Level, [string]$Event, [string]$Message = "") {
  try {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    Rotate-LauncherLog
    $payload = [ordered]@{
      timestamp = [DateTimeOffset]::Now.ToString("o")
      runId = $RunId
      action = $Action
      level = $Level
      event = $Event
      message = Protect-LogText $Message
    } | ConvertTo-Json -Compress
    [IO.File]::AppendAllText($LauncherLogPath, $payload + [Environment]::NewLine, $Utf8NoBom)
  } catch {
    # Logging must never replace the original deployment failure.
  }
}

function Remove-OldServiceLogs([string]$Prefix, [int]$Keep = 10) {
  if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) { return }
  $files = @(Get-ChildItem -LiteralPath $LogDirectory -File -Filter "$Prefix.*.log" | Sort-Object LastWriteTimeUtc -Descending)
  if ($files.Count -le $Keep) { return }
  foreach ($file in $files[$Keep..($files.Count - 1)]) {
    Remove-Item -LiteralPath $file.FullName -Force
  }
}

function Write-AtomicJson([string]$Path, [object]$Value) {
  $directory = Split-Path -Parent $Path
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $temporary = Join-Path $directory (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), [Guid]::NewGuid().ToString("N"))
  try {
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8), $Utf8NoBom)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
  }
}

function Write-ServiceDesiredState([string]$DesiredState, [string]$Reason) {
  if ($DesiredState -notin @("running", "stopped") -or
      $Reason -cnotmatch "^[a-z][a-z0-9_]{2,63}$") {
    throw "Django supervisor desired-state 参数无效"
  }
  Write-AtomicJson $SupervisorDesiredStatePath ([pscustomobject][ordered]@{
    version = "teruisi-django-supervisor-desired-state-v1"
    desiredState = $DesiredState
    reason = $Reason
    updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
    serviceScriptSha256 = Get-FileSha256 $PSCommandPath
  })
}

function Assert-SupervisorStartFence([string]$ExpectedSha256) {
  if ($ExpectedSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      (Get-FileSha256 $SupervisorDesiredStatePath) -cne $ExpectedSha256) {
    throw "Django supervisor Start desired-state fence 已变化"
  }
  $state = Read-JsonFile $SupervisorDesiredStatePath "Django supervisor desired-state"
  $updatedAt = [DateTimeOffset]::MinValue
  if (-not (Test-ExactObjectPropertyNames $state @(
        "version", "desiredState", "reason", "updatedAt", "serviceScriptSha256"
      )) -or
      [string]$state.version -cne "teruisi-django-supervisor-desired-state-v1" -or
      [string]$state.desiredState -cne "running" -or
      [string]$state.reason -cnotmatch "^[a-z][a-z0-9_]{2,63}$" -or
      -not [DateTimeOffset]::TryParse([string]$state.updatedAt, [ref]$updatedAt) -or
      [string]$state.serviceScriptSha256 -cne (Get-FileSha256 $PSCommandPath)) {
    throw "Django supervisor Start desired-state fence 无效"
  }
}

function Read-JsonFile([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label 不存在：$Path"
  }
  try {
    return Get-Content -Raw -LiteralPath $Path -Encoding UTF8 | ConvertFrom-Json
  } catch {
    throw "$Label 不是有效 JSON：$Path"
  }
}

function Unprotect-Value([string]$ProtectedValue, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($ProtectedValue)) { throw "$Label 缺失" }
  try {
    $secure = ConvertTo-SecureString $ProtectedValue
    $credential = [System.Management.Automation.PSCredential]::new("local", $secure)
    return $credential.GetNetworkCredential().Password
  } catch {
    throw "$Label 无法由当前 Windows 用户解密"
  }
}

function Protect-Value([string]$PlainValue) {
  if ([string]::IsNullOrWhiteSpace($PlainValue)) { throw "拒绝保护空凭据" }
  $secure = ConvertTo-SecureString $PlainValue -AsPlainText -Force
  try {
    return ConvertFrom-SecureString $secure
  } finally {
    $secure.Dispose()
  }
}

function New-RandomSecret {
  $bytes = [byte[]]::new(48)
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
    return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
  } finally {
    $generator.Dispose()
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}

function Assert-StrongSecret([string]$Value, [string]$Label) {
  $placeholders = @(
    "unsafe-local-development-key", "replace-me",
    "replace-with-at-least-32-random-bytes", "change-me", "changeme", "placeholder"
  )
  if (
    [Text.Encoding]::UTF8.GetByteCount($Value) -lt 32 -or
    $placeholders -contains $Value.Trim().ToLowerInvariant() -or
    @($Value.ToCharArray() | Select-Object -Unique).Count -lt 4
  ) {
    throw "$Label 必须至少 32 字节且不能使用占位值"
  }
}

function Read-Secrets {
  $payload = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  if ([int]$payload.version -ne 3) {
    throw "Django 本机 DPAPI 凭据库版本无效；财务凭据必须已完成受控配置"
  }
  $owner = Unprotect-Value ([string]$payload.databaseOwner) "databaseOwner"
  $reader = Unprotect-Value ([string]$payload.databaseReader) "databaseReader"
  $writer = Unprotect-Value ([string]$payload.databaseWriter) "databaseWriter"
  $financeReader = Unprotect-Value ([string]$payload.databaseFinanceReader) "databaseFinanceReader"
  $financeWriter = Unprotect-Value ([string]$payload.databaseFinanceWriter) "databaseFinanceWriter"
  $django = Unprotect-Value ([string]$payload.djangoSecretKey) "djangoSecretKey"
  $internal = Unprotect-Value ([string]$payload.internalSecret) "internalSecret"
  Assert-StrongSecret $django "djangoSecretKey"
  Assert-StrongSecret $internal "internalSecret"
  if (
    [string]::IsNullOrWhiteSpace($owner) -or
    [string]::IsNullOrWhiteSpace($reader) -or
    [string]::IsNullOrWhiteSpace($writer) -or
    [string]::IsNullOrWhiteSpace($financeReader) -or
    [string]::IsNullOrWhiteSpace($financeWriter)
  ) {
    throw "数据库 owner/reader/writer/finance 凭据不能为空"
  }
  return [pscustomobject]@{
    OwnerPassword = $owner
    ReaderPassword = $reader
    WriterPassword = $writer
    FinanceReaderPassword = $financeReader
    FinanceWriterPassword = $financeWriter
    DjangoSecretKey = $django
    InternalSecret = $internal
  }
}

function Database-Url(
  [string]$User,
  [string]$Password,
  [string]$ApplicationName,
  [int]$StatementTimeoutMilliseconds,
  [string]$DatabaseName = "teruisi_sales"
) {
  if (
    ($DatabaseName -cne "postgres" -and $DatabaseName -cnotmatch "^teruisi_sales(?:_rehearsal_[0-9a-f]{12})?$") -or
    ($DatabaseName -ceq "postgres" -and $User -cne "postgres")
  ) {
    throw "数据库名称或角色不在本机销售运行白名单"
  }
  $escapedUser = [Uri]::EscapeDataString($User)
  $escapedPassword = [Uri]::EscapeDataString($Password)
  $escapedApplication = [Uri]::EscapeDataString($ApplicationName)
  $escapedDatabase = [Uri]::EscapeDataString($DatabaseName)
  $idleTimeout = [Math]::Max($StatementTimeoutMilliseconds + 5000, 60000)
  $options = [Uri]::EscapeDataString("-c statement_timeout=$StatementTimeoutMilliseconds -c idle_in_transaction_session_timeout=$idleTimeout")
  return "postgresql://${escapedUser}:${escapedPassword}@127.0.0.1:5432/${escapedDatabase}?sslmode=disable&application_name=${escapedApplication}&connect_timeout=5&options=${options}"
}

function Resolve-ErpSourceD1([string]$Path, [switch]$MetadataOnly) {
  if ([string]::IsNullOrWhiteSpace($Path)) {
    throw "必须提供 ERP 权威 D1 的精确 SQLite 文件路径"
  }
  if (-not (Test-FullyQualifiedPath $Path)) {
    throw "ERP 权威 D1 必须使用绝对路径"
  }
  $canonical = Get-CanonicalPath $Path
  if (-not $MetadataOnly -and -not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
    throw "ERP 权威 D1 不存在：$canonical"
  }
  if ([IO.Path]::GetExtension($canonical) -ine ".sqlite") {
    throw "ERP 权威 D1 必须精确指向 .sqlite 文件"
  }
  return $canonical
}

function Get-ServiceConfig([switch]$RequireLegacyD1) {
  $config = Read-JsonFile $ConfigPath "Django 本机服务配置"
  if ([int]$config.version -notin @(5, 6)) { throw "Django 本机服务配置版本不受支持；请重新执行 Configure" }
  if ([int]$config.version -eq 6 -and [string]$config.backend -cne "django-postgresql") {
    throw "Django 本机服务配置必须使用 django-postgresql"
  }
  if (
    [string]$config.readerAddress -cne "127.0.0.1:8001" -or
    [string]$config.writerAddress -cne "127.0.0.1:8002" -or
    [string]$config.financeReaderAddress -cne "127.0.0.1:8011" -or
    [string]$config.financeWriterAddress -cne "127.0.0.1:8012" -or
    [string]$config.customerServiceReaderAddress -cne "127.0.0.1:8071" -or
    [string]$config.customerServiceWriterAddress -cne "127.0.0.1:8072" -or
    [string]$config.postgresAddress -cne "127.0.0.1:5432"
  ) {
    throw "Django 本机服务地址配置不符合固定回环契约"
  }
  # Version 5 retains the historical source identity, not a runtime dependency.
  # Only an explicitly requested historical operator may inspect that file.
  if ($RequireLegacyD1 -or [int]$config.version -eq 5 -or -not [string]::IsNullOrWhiteSpace([string]$config.erpSourceD1)) {
    $resolvedSource = Resolve-ErpSourceD1 ([string]$config.erpSourceD1) -MetadataOnly:(-not $RequireLegacyD1)
    if ([string]$config.erpSourceD1 -cne $resolvedSource) {
      throw "历史 D1 配置必须是规范绝对路径；请重新执行 Configure"
    }
  }
  return $config
}

function Get-AllowedAclSids {
  $current = [Security.Principal.WindowsIdentity]::GetCurrent().User
  if ($null -eq $current) { throw "无法解析当前 Windows 用户 SID" }
  return @(
    $current,
    [Security.Principal.SecurityIdentifier]::new("S-1-5-18"),
    [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
  )
}

function Get-RuntimeTreeItemsNoReparse {
  if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
    throw "运行目录不存在：$RuntimeRoot"
  }
  $root = Get-CanonicalPath $RuntimeRoot
  $rootItem = [IO.DirectoryInfo]::new($root)
  $rootItem.Refresh()
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "运行目录不得包含重解析点：$($rootItem.FullName)"
  }
  $items = [Collections.Generic.List[object]]::new()
  $directories = [Collections.Stack]::new()
  $items.Add([pscustomobject]@{
    FullName = $rootItem.FullName
    Attributes = $rootItem.Attributes
    PSIsContainer = $true
  })
  $directories.Push($rootItem)
  while ($directories.Count -gt 0) {
    $directory = [IO.DirectoryInfo]$directories.Pop()
    foreach ($item in $directory.EnumerateFileSystemInfos()) {
      $item.Refresh()
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "运行目录不得包含重解析点：$($item.FullName)"
      }
      $isDirectory = $item -is [IO.DirectoryInfo]
      $items.Add([pscustomobject]@{
        FullName = $item.FullName
        Attributes = $item.Attributes
        PSIsContainer = $isDirectory
      })
      if ($isDirectory) { $directories.Push($item) }
    }
  }
  return $items
}

function Get-RuntimeItemAccessControl([object]$Item) {
  # Get-Acl creates a provider object and crosses the PowerShell provider
  # boundary for every path.  That costs more than two minutes on the current
  # 90k-item runtime.  The FileSystemAclExtensions API reads the same Windows
  # DACL directly and preserves the exact inherited/explicit ACE evidence used
  # by Assert-ExactRuntimeAclEntry.
  if ([bool]$Item.PSIsContainer) {
    $entry = [IO.DirectoryInfo]::new([string]$Item.FullName)
  } else {
    $entry = [IO.FileInfo]::new([string]$Item.FullName)
  }
  $entry.Refresh()
  if (-not $entry.Exists) {
    throw [IO.InvalidDataException]::new("运行目录对象在 ACL 检查期间消失：$($Item.FullName)")
  }
  if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw [IO.InvalidDataException]::new("运行目录不得包含重解析点：$($entry.FullName)")
  }
  if ($null -ne ("System.IO.FileSystemAclExtensions" -as [type])) {
    return [IO.FileSystemAclExtensions]::GetAccessControl($entry)
  }
  # Windows PowerShell 5.1 exposes the same DACL through the Security module,
  # while the accelerated full-tree verifier is compiled below against the
  # framework instance APIs. This fallback is used only for the bounded root
  # status check and keeps the production hot path provider-free on pwsh.
  return Microsoft.PowerShell.Security\Get-Acl -LiteralPath ([string]$Item.FullName)
}

function Initialize-RuntimeAclVerifier {
  if ($null -ne ("Teruisi.RuntimeAclVerifier" -as [type])) { return }
  Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;

namespace Teruisi {
  public static class RuntimeAclVerifier {
    private static readonly MethodInfo DirectoryAccessControl = ResolveAccessControl(typeof(DirectoryInfo));
    private static readonly MethodInfo FileAccessControl = ResolveAccessControl(typeof(FileInfo));

    public static long Verify(string rootPath, string[] allowedSidValues) {
      if (String.IsNullOrWhiteSpace(rootPath) || allowedSidValues == null || allowedSidValues.Length == 0) {
        throw new InvalidDataException("运行目录 ACL 校验参数无效");
      }
      string root = Path.GetFullPath(rootPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
      var allowed = new HashSet<string>(allowedSidValues, StringComparer.OrdinalIgnoreCase);
      if (allowed.Count != allowedSidValues.Length) {
        throw new InvalidDataException("运行目录 ACL 允许主体包含重复项");
      }

      var rootInfo = new DirectoryInfo(root);
      rootInfo.Refresh();
      if (!rootInfo.Exists) throw new InvalidDataException("运行目录不存在：" + root);
      VerifyEntry(rootInfo, true, allowed);

      long count = 1;
      var directories = new Stack<DirectoryInfo>();
      directories.Push(rootInfo);
      while (directories.Count != 0) {
        DirectoryInfo directory = directories.Pop();
        foreach (FileSystemInfo entry in directory.EnumerateFileSystemInfos()) {
          entry.Refresh();
          VerifyEntry(entry, false, allowed);
          count += 1;
          DirectoryInfo child = entry as DirectoryInfo;
          if (child != null) directories.Push(child);
        }
      }
      return count;
    }

    private static void VerifyEntry(FileSystemInfo entry, bool isRoot, HashSet<string> allowed) {
      if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) {
        throw new InvalidDataException("运行目录不得包含重解析点：" + entry.FullName);
      }
      FileSystemSecurity security;
      DirectoryInfo directory = entry as DirectoryInfo;
      if (directory != null) {
        security = ReadAccessControl(directory, DirectoryAccessControl);
      } else {
        security = ReadAccessControl((FileInfo)entry, FileAccessControl);
      }
      if (isRoot && !security.AreAccessRulesProtected) {
        throw new InvalidDataException("运行目录根 ACL 尚未禁用父目录继承");
      }
      if (!isRoot && security.AreAccessRulesProtected) {
        throw new InvalidDataException("运行目录子项必须继承受保护根 ACL：" + entry.FullName);
      }

      AuthorizationRuleCollection rules = security.GetAccessRules(true, true, typeof(SecurityIdentifier));
      if (rules.Count != allowed.Count) {
        throw new InvalidDataException(String.Format(
          "运行目录 ACL 规则数量不符合精确契约（{0} expected={1} actual={2}）：{3}",
          isRoot ? "root" : "descendant", allowed.Count, rules.Count, entry.FullName));
      }
      var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
      const InheritanceFlags expectedInheritance = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
      foreach (FileSystemAccessRule rule in rules) {
        string sid = ((SecurityIdentifier)rule.IdentityReference).Value;
        if (!allowed.Contains(sid)) {
          throw new InvalidDataException("运行目录 ACL 包含未授权主体：" + sid);
        }
        if (rule.AccessControlType != AccessControlType.Allow) {
          throw new InvalidDataException("运行目录 ACL 不得包含拒绝规则");
        }
        if ((rule.FileSystemRights & FileSystemRights.FullControl) != FileSystemRights.FullControl) {
          throw new InvalidDataException("运行目录 ACL 主体缺少 FullControl");
        }
        if (isRoot) {
          if (rule.IsInherited || rule.InheritanceFlags != expectedInheritance || rule.PropagationFlags != PropagationFlags.None) {
            throw new InvalidDataException("运行目录根 ACL 继承标志不符合精确契约");
          }
        } else if (!rule.IsInherited) {
          throw new InvalidDataException("运行目录子项不得包含显式 ACL 规则：" + entry.FullName);
        }
        if (!seen.Add(sid)) throw new InvalidDataException("运行目录 ACL 包含重复主体");
      }
      foreach (string sid in allowed) {
        if (!seen.Contains(sid)) throw new InvalidDataException("运行目录 ACL 缺少受控主体");
      }
    }

    private static MethodInfo ResolveAccessControl(Type entryType) {
      MethodInfo instance = entryType.GetMethod("GetAccessControl", Type.EmptyTypes);
      if (instance != null) return instance;
      Type extensions = Type.GetType(
        "System.IO.FileSystemAclExtensions, System.IO.FileSystem.AccessControl", false
      );
      if (extensions == null) {
        foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies()) {
          extensions = assembly.GetType("System.IO.FileSystemAclExtensions", false);
          if (extensions != null) break;
        }
      }
      if (extensions == null) {
        throw new PlatformNotSupportedException("File-system ACL API is unavailable");
      }
      MethodInfo method = extensions.GetMethod(
        "GetAccessControl", BindingFlags.Public | BindingFlags.Static,
        null, new Type[] { entryType }, null
      );
      if (method == null) {
        throw new PlatformNotSupportedException("Exact file-system ACL API is unavailable");
      }
      return method;
    }

    private static FileSystemSecurity ReadAccessControl(FileSystemInfo entry, MethodInfo method) {
      object target = method.IsStatic ? null : (object)entry;
      object[] arguments = method.IsStatic ? new object[] { entry } : null;
      try {
        return (FileSystemSecurity)method.Invoke(target, arguments);
      } catch (TargetInvocationException error) {
        if (error.InnerException != null) throw error.InnerException;
        throw;
      }
    }
  }
}
'@
}

function Invoke-AcceleratedRuntimeAclVerification([string]$Root, [string[]]$AllowedValues) {
  Initialize-RuntimeAclVerifier
  try {
    return [Teruisi.RuntimeAclVerifier]::Verify($Root, $AllowedValues)
  } catch {
    if ($_.Exception.InnerException -is [IO.InvalidDataException]) {
      throw $_.Exception.InnerException
    }
    throw
  }
}

function Get-SystemIcaclsPath {
  $icacls = Join-Path ([Environment]::SystemDirectory) "icacls.exe"
  if (-not (Test-Path -LiteralPath $icacls -PathType Leaf)) {
    throw "系统 icacls.exe 不存在"
  }
  return Get-CanonicalPath $icacls
}

function Invoke-IcaclsDaclOnlyChecked(
  [string]$Executable,
  [string]$Target,
  [string[]]$Arguments,
  [string]$FailureLabel
) {
  $nativeRun = Invoke-BoundedNativeProcess $Executable (@($Target) + @($Arguments)) $RuntimeRoot
  if ($nativeRun.ExitCode -ne 0) {
    throw "$FailureLabel（$(Get-NativeFailureSummary $nativeRun)）"
  }
}

function Set-DirectoryDaclOnly(
  [string]$Path,
  [Security.AccessControl.DirectorySecurity]$Dacl
) {
  $root = Get-CanonicalPath $RuntimeRoot
  $target = Get-CanonicalPath $Path
  if (-not $target.Equals($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝在运行目录根之外发布根 DACL"
  }
  $directory = Get-Item -LiteralPath $target -Force
  if (-not $directory.PSIsContainer -or
      ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "运行目录根不是普通目录"
  }
  if ($null -ne ("System.IO.FileSystemAclExtensions" -as [type])) {
    [IO.FileSystemAclExtensions]::SetAccessControl(
      [IO.DirectoryInfo]$directory,
      $Dacl
    )
  } else {
    $directory.SetAccessControl($Dacl)
  }
}

function Reset-RuntimeDescendantDaclWithIcacls([string]$Root) {
  $root = Get-CanonicalPath $Root
  $expectedRoot = Get-CanonicalPath $RuntimeRoot
  if (-not $root.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝在运行目录之外重置子项 DACL"
  }
  [void]@(Get-RuntimeTreeItemsNoReparse)
  $topLevelItems = @(Get-ChildItem -LiteralPath $root -Force)
  if ($topLevelItems.Count -gt 128) {
    throw "运行目录直属子项过多，拒绝逐项启动 ACL 工具"
  }
  $icacls = Get-SystemIcaclsPath
  foreach ($item in $topLevelItems) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "运行目录不得包含重解析点：$($item.FullName)"
    }
    $itemPath = Get-CanonicalPath $item.FullName
    $parentPath = Get-CanonicalPath ([IO.Path]::GetDirectoryName($itemPath))
    if (-not $parentPath.Equals($root, [StringComparison]::OrdinalIgnoreCase)) {
      throw "运行目录 ACL 子项越界"
    }
    $freshItem = Get-Item -LiteralPath $itemPath -Force
    if (($freshItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "运行目录不得包含重解析点：$($freshItem.FullName)"
    }
    $freshPath = Get-CanonicalPath $freshItem.FullName
    if (-not $freshPath.Equals($itemPath, [StringComparison]::OrdinalIgnoreCase)) {
      throw "运行目录 ACL 子项身份在检查期间发生变化"
    }
    # icacls only changes the DACL for /reset. /L prevents a raced reparse
    # point from redirecting this recursive operation outside the runtime.
    Invoke-IcaclsDaclOnlyChecked $icacls $freshPath @(
      "/reset", "/T", "/Q", "/L"
    ) "无法重置运行目录子项 DACL"
  }
  [void]@(Get-RuntimeTreeItemsNoReparse)
}

function Set-RuntimeDescendantDaclInheritance([object[]]$RuntimeItems, [string]$Root) {
  $requiresDaclOnlyFallback = $false
  foreach ($item in @($RuntimeItems | Select-Object -Skip 1)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "运行目录不得包含重解析点：$($item.FullName)"
    }
    $itemPath = Assert-RuntimeChildPath $item.FullName
    $freshItem = Get-Item -LiteralPath $itemPath -Force
    if (($freshItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "运行目录不得包含重解析点：$($freshItem.FullName)"
    }
    $freshPath = Assert-RuntimeChildPath $freshItem.FullName
    if (-not $freshPath.Equals($itemPath, [StringComparison]::OrdinalIgnoreCase)) {
      throw "运行目录 ACL 子项身份在检查期间发生变化"
    }
    $acl = if ($freshItem.PSIsContainer) {
      [Security.AccessControl.DirectorySecurity]::new()
    } else {
      [Security.AccessControl.FileSecurity]::new()
    }
    $acl.SetAccessRuleProtection($false, $false)
    try {
      Set-Acl -LiteralPath $freshPath -AclObject $acl
    } catch [System.Security.AccessControl.PrivilegeNotHeldException] {
      $requiresDaclOnlyFallback = $true
      break
    }
  }
  if ($requiresDaclOnlyFallback) {
    Reset-RuntimeDescendantDaclWithIcacls $Root
  }
}

function New-RuntimeRootDacl {
  $rootAcl = [Security.AccessControl.DirectorySecurity]::new()
  $rootAcl.SetAccessRuleProtection($true, $false)
  $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
    [Security.AccessControl.InheritanceFlags]::ObjectInherit
  foreach ($sid in @(Get-AllowedAclSids)) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
      $sid,
      [Security.AccessControl.FileSystemRights]::FullControl,
      $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    $rootAcl.AddAccessRule($rule)
  }
  return $rootAcl
}

function Set-RuntimeRootDacl(
  [string]$Root,
  [Security.AccessControl.DirectorySecurity]$Dacl
) {
  try {
    Set-Acl -LiteralPath $Root -AclObject $Dacl
  } catch [System.Security.AccessControl.PrivilegeNotHeldException] {
    # Set-Acl can request SeSecurityPrivilege for an existing SACL. The direct
    # access-control API persists only this fresh descriptor's exact DACL.
    Set-DirectoryDaclOnly $Root $Dacl
  }
  Assert-RuntimeRootAclHardened
}

function Set-RuntimeAcl {
  if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) { throw "运行目录不存在：$RuntimeRoot" }
  $runtimeItems = @(Get-RuntimeTreeItemsNoReparse)
  Assert-DeployedApplication
  try {
    Assert-RuntimeAclHardened
    Write-LauncherEvent "INFO" "runtime_acl_already_hardened"
    Write-Output "Django 本机运行目录 ACL 已满足限制，无需重复修改。"
    return
  } catch [IO.InvalidDataException] {
    # Continue with a complete DACL replacement below.
  }
  $root = Get-CanonicalPath $RuntimeRoot
  # Build a fresh DACL-only descriptor. Reusing Get-Acl can carry the system
  # audit ACL back into Set-Acl and require SeSecurityPrivilege even though
  # this operation only needs to replace access rules.
  $rootAcl = New-RuntimeRootDacl
  Set-RuntimeRootDacl $root $rootAcl

  # Protect only the root. Descendants inherit the restricted root DACL; making
  # every descendant protected before granting it would create empty DACLs.
  # Historical audit-bearing files can make Set-Acl request SeSecurityPrivilege;
  # that exact failure switches the whole descendant tree to the DACL-only path.
  Set-RuntimeDescendantDaclInheritance $runtimeItems $root
  Assert-RuntimeAclHardened
  Write-LauncherEvent "INFO" "runtime_acl_hardened"
  Write-Output "Django 本机运行目录 ACL 已限制为当前用户、SYSTEM 和 Administrators。"
}

function Assert-ExactRuntimeAclEntry(
  [object]$Item,
  [Security.AccessControl.FileSystemSecurity]$Acl,
  [string[]]$AllowedValues,
  [string]$Root
) {
  $isRoot = (Get-CanonicalPath $Item.FullName).Equals(
    $Root,
    [StringComparison]::OrdinalIgnoreCase
  )
  if ($isRoot -and -not $Acl.AreAccessRulesProtected) {
    throw [IO.InvalidDataException]::new("运行目录根 ACL 尚未禁用父目录继承")
  }
  if (-not $isRoot -and $Acl.AreAccessRulesProtected) {
    throw [IO.InvalidDataException]::new("运行目录子项必须继承受保护根 ACL：$($Item.FullName)")
  }
  $rules = @($Acl.GetAccessRules(
      $true,
      $true,
      [Security.Principal.SecurityIdentifier]
    ))
  if ($rules.Count -ne $AllowedValues.Count) {
    $entryKind = if ($isRoot) { "root" } else { "descendant" }
    throw [IO.InvalidDataException]::new(
      "运行目录 ACL 规则数量不符合精确契约（$entryKind expected=$($AllowedValues.Count) actual=$($rules.Count)）"
    )
  }
  $seen = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  $expectedInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
    [Security.AccessControl.InheritanceFlags]::ObjectInherit
  foreach ($rule in $rules) {
    $sid = ([Security.Principal.SecurityIdentifier]$rule.IdentityReference).Value
    if ($AllowedValues -notcontains $sid) {
      throw [IO.InvalidDataException]::new("运行目录 ACL 包含未授权主体：$($rule.IdentityReference)")
    }
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
      throw [IO.InvalidDataException]::new("运行目录 ACL 不得包含拒绝规则")
    }
    if (
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
      [Security.AccessControl.FileSystemRights]::FullControl
    ) {
      throw [IO.InvalidDataException]::new("运行目录 ACL 主体缺少 FullControl")
    }
    if ($isRoot) {
      if ($rule.IsInherited -or
          $rule.InheritanceFlags -ne $expectedInheritance -or
          $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
        throw [IO.InvalidDataException]::new("运行目录根 ACL 继承标志不符合精确契约")
      }
    } elseif (-not $rule.IsInherited) {
      throw [IO.InvalidDataException]::new("运行目录子项不得包含显式 ACL 规则")
    }
    if (-not $seen.Add($sid)) {
      throw [IO.InvalidDataException]::new("运行目录 ACL 包含重复主体")
    }
  }
  foreach ($allowedValue in $AllowedValues) {
    if (-not $seen.Contains($allowedValue)) {
      throw [IO.InvalidDataException]::new("运行目录 ACL 缺少受控主体")
    }
  }
}

function Assert-RuntimeRootAclHardened {
  if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
    throw "运行目录不存在：$RuntimeRoot"
  }
  $root = Get-CanonicalPath $RuntimeRoot
  $rootItem = [pscustomobject]@{
    FullName = $root
    Attributes = [IO.File]::GetAttributes($root)
    PSIsContainer = $true
  }
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "运行目录不得包含重解析点：$($rootItem.FullName)"
  }
  $allowedValues = @((Get-AllowedAclSids) | ForEach-Object { $_.Value })
  Assert-ExactRuntimeAclEntry $rootItem (Get-RuntimeItemAccessControl $rootItem) $allowedValues $root
}

function Assert-RuntimeAclHardened {
  if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) { throw "运行目录不存在：$RuntimeRoot" }
  $allowedValues = @((Get-AllowedAclSids) | ForEach-Object { $_.Value })
  $root = Get-CanonicalPath $RuntimeRoot
  $timer = [Diagnostics.Stopwatch]::StartNew()
  $objectCount = Invoke-AcceleratedRuntimeAclVerification $root $allowedValues
  $timer.Stop()
  Write-LauncherEvent "INFO" "runtime_acl_verified" (
    "objects=$objectCount elapsedMilliseconds=$($timer.ElapsedMilliseconds)"
  )
}

function New-OrchestratedLifecycleAclToken {
  $bytes = [byte[]]::new(32)
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
    return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
  } finally {
    $generator.Dispose()
  }
}

function Set-OrchestratedLifecycleAclContext([string]$Token) {
  if ($Token -cnotmatch "^[0-9a-f]{64}$") {
    throw "orchestrated lifecycle ACL token 无效"
  }
  Assert-RuntimeRootAclHardened
  if (-not (Test-Path -LiteralPath $DeploymentManifestPath -PathType Leaf)) {
    throw "Django runtime app 部署清单缺失"
  }
  $payload = [ordered]@{
    version = $OrchestratedLifecycleAclContextVersion
    token = $Token
    processId = [int]$PID
    runtimeRootPathSha256 = Get-Sha256Text ((Get-CanonicalPath $RuntimeRoot).ToUpperInvariant())
    deploymentManifestSha256 = Get-FileSha256 $DeploymentManifestPath
    issuedAtUnixMilliseconds = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  }
  Set-Variable -Scope Global -Name $OrchestratedLifecycleAclContextVariable `
    -Value ([pscustomobject]$payload) -Force
  Write-LauncherEvent "INFO" "orchestrated_lifecycle_acl_context_issued"
}

function Test-OrchestratedLifecycleAclContext([string]$Token) {
  if ([string]::IsNullOrWhiteSpace($Token)) { return $false }
  if ($Token -cnotmatch "^[0-9a-f]{64}$") { return $false }
  try {
    $contextVariable = Get-Variable -Scope Global -Name $OrchestratedLifecycleAclContextVariable `
      -ErrorAction SilentlyContinue
    if ($null -eq $contextVariable) { return $false }
    $payload = $contextVariable.Value
    if (-not (Test-ExactObjectPropertyNames $payload @(
          "version", "token", "processId", "runtimeRootPathSha256",
          "deploymentManifestSha256", "issuedAtUnixMilliseconds"
        ))) { return $false }
    $processId = 0
    $issuedAt = [int64]0
    if ([string]$payload.version -cne $OrchestratedLifecycleAclContextVersion -or
        [string]$payload.token -cne $Token -or
        -not [int]::TryParse([string]$payload.processId, [ref]$processId) -or
        $processId -ne [int]$PID -or
        -not [int64]::TryParse([string]$payload.issuedAtUnixMilliseconds, [ref]$issuedAt)) {
      return $false
    }
    $age = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $issuedAt
    if ($age -lt 0 -or $age -gt $OrchestratedLifecycleAclContextMaxAgeMilliseconds) { return $false }
    if ([string]$payload.runtimeRootPathSha256 -cne
        (Get-Sha256Text ((Get-CanonicalPath $RuntimeRoot).ToUpperInvariant()))) { return $false }
    if (-not (Test-Path -LiteralPath $DeploymentManifestPath -PathType Leaf) -or
        [string]$payload.deploymentManifestSha256 -cne (Get-FileSha256 $DeploymentManifestPath)) {
      return $false
    }
    Assert-RuntimeRootAclHardened
    return $true
  } catch {
    return $false
  }
}

function Get-ApplicationTreeFingerprintEvidence([string]$AppRoot) {
  if (-not (Test-Path -LiteralPath $AppRoot -PathType Container)) {
    return [pscustomobject]@{
      Algorithm = $ApplicationFingerprintAlgorithm
      FileCount = [int64]0
      Rows = [string[]]@()
      Fingerprint = "missing"
    }
  }
  $root = Get-CanonicalPath $AppRoot
  $rows = [Collections.Generic.List[string]]::new()
  foreach ($file in @(Get-ChildItem -LiteralPath $root -File -Recurse)) {
    if ($file.FullName -eq (Join-Path $root "deployment.json")) { continue }
    $relative = $file.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
    $segments = $relative -split "/"
    if (@($segments | Where-Object { $_ -in @("__pycache__", ".pytest_cache", ".mypy_cache") }).Count -gt 0) { continue }
    if ($file.Extension -in @(".pyc", ".pyo")) { continue }
    $rows.Add("$relative`n$(Get-FileSha256 $file.FullName)")
  }
  $rows.Sort([StringComparer]::Ordinal)
  $rowArray = [string[]]$rows.ToArray()
  return [pscustomobject]@{
    Algorithm = $ApplicationFingerprintAlgorithm
    FileCount = [int64]$rowArray.Count
    Rows = $rowArray
    Fingerprint = Get-Sha256Text ([string]::Join("`n", $rowArray))
  }
}

function Get-ApplicationTreeFingerprint([string]$AppRoot) {
  $evidence = Get-ApplicationTreeFingerprintEvidence $AppRoot
  return [string]$evidence.Fingerprint
}

function Test-IsWindowsPowerShell51 {
  return (
    $PSVersionTable.PSEdition -ceq "Desktop" -and
    $PSVersionTable.PSVersion.Major -eq 5 -and
    [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
  )
}

function Get-ApplicationTreeFingerprintLegacyV1([string]$AppRoot) {
  if (-not (Test-IsWindowsPowerShell51)) {
    throw "legacy deployment manifest v1 只能使用 Windows PowerShell 5.1 验证"
  }
  if (-not (Test-Path -LiteralPath $AppRoot -PathType Container)) { return "missing" }
  $root = Get-CanonicalPath $AppRoot
  $rows = foreach ($file in @(
      Get-ChildItem -LiteralPath $root -File -Recurse | Sort-Object FullName
    )) {
    if ($file.FullName -eq (Join-Path $root "deployment.json")) { continue }
    $relative = $file.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
    $segments = $relative -split "/"
    if (@($segments | Where-Object { $_ -in @("__pycache__", ".pytest_cache", ".mypy_cache") }).Count -gt 0) { continue }
    if ($file.Extension -in @(".pyc", ".pyo")) { continue }
    "$relative`n$(Get-FileSha256 $file.FullName)"
  }
  return Get-Sha256Text ($rows -join "`n")
}

function Invoke-WranglerRuntimeProcess(
  [string]$AppRoot,
  [string[]]$Arguments,
  [int]$TimeoutSeconds = 30
) {
  $cli = Join-Path $AppRoot "runtime-tools\node_modules\wrangler\wrangler-dist\cli.js"
  if (-not (Test-Path -LiteralPath $Node -PathType Leaf) -or
      -not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    throw "受保护 Wrangler runtime 缺少 Node.js 或 CLI"
  }
  $startInfo = [Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $Node
  $startInfo.WorkingDirectory = $AppRoot
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.EnvironmentVariables["CI"] = "1"
  $startInfo.EnvironmentVariables["WRANGLER_SEND_METRICS"] = "false"
  foreach ($name in @(
    "NODE_PATH", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_EMAIL", "CF_API_TOKEN", "CF_API_KEY"
  )) {
    [void]$startInfo.EnvironmentVariables.Remove($name)
  }
  $startInfo.Arguments = (@($cli) + @($Arguments) | ForEach-Object {
    ConvertTo-ProcessArgument ([string]$_)
  }) -join " "

  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  $operation = (@($Arguments | Select-Object -First 3) -join " ")
  try {
    if (-not $process.Start()) { throw "受保护 Wrangler CLI 无法启动" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      try { $process.Kill($true) } catch {
        try { $process.Kill() } catch { }
      }
      if (-not $process.WaitForExit(5000)) {
        throw "受保护 Wrangler CLI 超时后无法在 5 秒内终止"
      }
      throw "受保护 Wrangler CLI 执行超时（operation=$operation；timeoutSeconds=$TimeoutSeconds）"
    }
    $process.WaitForExit()
    return [pscustomobject]@{
      ExitCode = [int]$process.ExitCode
      Stdout = [string]$stdoutTask.GetAwaiter().GetResult()
      Stderr = [string]$stderrTask.GetAwaiter().GetResult()
    }
  } finally {
    $process.Dispose()
  }
}

function Get-WranglerRuntimeFailureDigest([object]$Result) {
  return Get-Sha256Text (Protect-LogText (
    ([string]$Result.Stdout) + "`n" + ([string]$Result.Stderr)
  ))
}

function Assert-WranglerRuntimeCli([string]$AppRoot) {
  $closurePath = Join-Path $AppRoot "runtime-tools\wrangler-dependencies.json"
  $closure = Read-JsonFile $closurePath "Wrangler runtime 依赖清单"
  if ([int]$closure.version -ne 1 -or
      [string]$closure.rootPackage -cne "wrangler" -or
      [string]$closure.rootVersion -cnotmatch "^[0-9]+\.[0-9]+\.[0-9]+$" -or
      @($closure.packages).Count -lt 2) {
    throw "Wrangler runtime 依赖清单无效"
  }

  $versionResult = Invoke-WranglerRuntimeProcess $AppRoot @("--version") 15
  $versionOutput = (([string]$versionResult.Stdout) + ([string]$versionResult.Stderr)).Trim()
  if ($versionResult.ExitCode -ne 0 -or $versionOutput -cne [string]$closure.rootVersion) {
    throw "Wrangler runtime 版本 smoke 失败（outputSha256=$(Get-WranglerRuntimeFailureDigest $versionResult)）"
  }

  $helpResult = Invoke-WranglerRuntimeProcess $AppRoot @(
    "r2", "object", "delete", "--help"
  ) 15
  $helpOutput = ([string]$helpResult.Stdout) + "`n" + ([string]$helpResult.Stderr)
  if ($helpResult.ExitCode -ne 0 -or
      $helpOutput -notmatch [regex]::Escape("wrangler r2 object delete <objectPath>") -or
      $helpOutput -notmatch [regex]::Escape("Delete an object in an R2 bucket") -or
      $helpOutput -match "MODULE_NOT_FOUND|ERR_MODULE_NOT_FOUND|Cannot find package") {
    throw "Wrangler R2 CLI load smoke 失败（outputSha256=$(Get-WranglerRuntimeFailureDigest $helpResult)）"
  }
}

function Assert-WranglerLocalR2RoundTrip([string]$AppRoot) {
  Assert-WranglerRuntimeCli $AppRoot
  $smokeRoot = Assert-RuntimeChildPath (Join-Path $RuntimeRoot (
    "run\wrangler-smoke-" + [Guid]::NewGuid().ToString("N")
  ))
  $smokePrefix = Get-CanonicalPath (Join-Path $RuntimeRoot "run\wrangler-smoke-")
  if (-not $smokeRoot.StartsWith(
      $smokePrefix,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Wrangler smoke 目录不在 runtime run 临时范围内"
  }
  $persistRoot = Join-Path $smokeRoot "state"
  $inputPath = Join-Path $smokeRoot "input.bin"
  $outputPath = Join-Path $smokeRoot "output.bin"
  $missingPath = Join-Path $smokeRoot "missing.bin"
  $objectPath = "teruisi-runtime-smoke/__runtime_smoke__/" +
    [Guid]::NewGuid().ToString("N") + ".bin"
  try {
    New-Item -ItemType Directory -Path $persistRoot -Force | Out-Null
    [IO.File]::WriteAllBytes(
      $inputPath,
      [Text.Encoding]::UTF8.GetBytes("teruisi-wrangler-runtime-smoke-v1")
    )
    $put = Invoke-WranglerRuntimeProcess $AppRoot @(
      "r2", "object", "put", $objectPath,
      "--local", "--persist-to", $persistRoot, "--file", $inputPath
    ) 30
    if ($put.ExitCode -ne 0) {
      throw "Wrangler local R2 put smoke 失败（outputSha256=$(Get-WranglerRuntimeFailureDigest $put)）"
    }
    $get = Invoke-WranglerRuntimeProcess $AppRoot @(
      "r2", "object", "get", $objectPath,
      "--local", "--persist-to", $persistRoot, "--file", $outputPath
    ) 30
    if ($get.ExitCode -ne 0 -or
        -not (Test-Path -LiteralPath $outputPath -PathType Leaf) -or
        (Get-FileSha256 $inputPath) -cne (Get-FileSha256 $outputPath)) {
      throw "Wrangler local R2 get/hash smoke 失败（outputSha256=$(Get-WranglerRuntimeFailureDigest $get)）"
    }
    $delete = Invoke-WranglerRuntimeProcess $AppRoot @(
      "r2", "object", "delete", $objectPath,
      "--local", "--persist-to", $persistRoot, "--force"
    ) 30
    if ($delete.ExitCode -ne 0) {
      throw "Wrangler local R2 delete smoke 失败（outputSha256=$(Get-WranglerRuntimeFailureDigest $delete)）"
    }
    $missing = Invoke-WranglerRuntimeProcess $AppRoot @(
      "r2", "object", "get", $objectPath,
      "--local", "--persist-to", $persistRoot, "--file", $missingPath
    ) 30
    $missingOutput = ([string]$missing.Stdout) + "`n" + ([string]$missing.Stderr)
    if ($missing.ExitCode -eq 0 -or $missingOutput -notmatch "does not exist") {
      throw "Wrangler local R2 delete 后缺失回查失败（outputSha256=$(Get-WranglerRuntimeFailureDigest $missing)）"
    }
  } finally {
    $candidate = Get-CanonicalPath $smokeRoot
    if ($candidate.StartsWith(
        $smokePrefix,
        [StringComparison]::OrdinalIgnoreCase
      ) -and (Test-Path -LiteralPath $candidate -PathType Container)) {
      Remove-Item -LiteralPath $candidate -Recurse -Force
    }
  }
}

function Test-ExactObjectPropertyNames([object]$Value, [string[]]$Expected) {
  if ($null -eq $Value) { return $false }
  $actual = @($Value.PSObject.Properties.Name)
  if ($actual.Count -ne $Expected.Count) { return $false }
  foreach ($name in $Expected) {
    if ($actual -cnotcontains $name) { return $false }
  }
  return $true
}

function Test-DeploymentInteger([object]$Value) {
  if ($null -eq $Value) { return $false }
  return [Type]::GetTypeCode($Value.GetType()) -in @(
    [TypeCode]::SByte,
    [TypeCode]::Byte,
    [TypeCode]::Int16,
    [TypeCode]::UInt16,
    [TypeCode]::Int32,
    [TypeCode]::UInt32,
    [TypeCode]::Int64,
    [TypeCode]::UInt64
  )
}

function Assert-ApplicationTreeManifest([string]$AppRoot, [string]$Label) {
  $manifestPath = Join-Path $AppRoot "deployment.json"
  $manifest = Read-JsonFile $manifestPath "$Label 部署清单"
  if (-not (Test-DeploymentInteger $manifest.version) -or
      [string]$manifest.appFingerprint -cnotmatch "^[0-9a-f]{64}$") {
    throw "$Label 部署清单无效"
  }

  $version = [int64]$manifest.version
  if ($version -eq 1) {
    if (-not (Test-ExactObjectPropertyNames $manifest @(
        "version", "deployedAt", "sourceRoot", "appFingerprint"
      ))) {
      throw "$Label legacy deployment manifest v1 字段集合无效"
    }
    if (-not (Test-IsWindowsPowerShell51)) {
      throw "$Label 使用 legacy deployment manifest v1；请使用 Windows PowerShell 5.1 验证或先重新部署为 v2"
    }
    $actualFingerprint = Get-ApplicationTreeFingerprintLegacyV1 $AppRoot
  } elseif ($version -eq 2) {
    if (-not (Test-ExactObjectPropertyNames $manifest @(
        "version", "deployedAt", "sourceRoot", "fingerprintAlgorithm",
        "fileCount", "appFingerprint"
      )) -or
        [string]$manifest.fingerprintAlgorithm -cne $ApplicationFingerprintAlgorithm -or
        -not (Test-DeploymentInteger $manifest.fileCount) -or
        [int64]$manifest.fileCount -lt 1) {
      throw "$Label deployment manifest v2 字段集合或算法无效"
    }
    $evidence = Get-ApplicationTreeFingerprintEvidence $AppRoot
    if ([int64]$manifest.fileCount -ne [int64]$evidence.FileCount) {
      throw "$Label 文件数与部署清单不一致"
    }
    $actualFingerprint = [string]$evidence.Fingerprint
  } else {
    throw "$Label 部署清单版本无效"
  }

  if ($actualFingerprint -cne [string]$manifest.appFingerprint) {
    throw "$Label 文件与部署清单不一致"
  }
  $scriptPath = Join-Path $AppRoot "tools\django-local-service.ps1"
  if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "$Label 缺少受保护启动脚本"
  }
  Assert-WranglerRuntimeCli $AppRoot
  return $manifest
}

function Assert-DeployedApplication {
  Assert-ApplicationTreeManifest $InstalledAppRoot "Django runtime app" | Out-Null
}

function Copy-ApplicationTree(
  [string]$Source,
  [string]$Destination,
  [switch]$ExcludeNodeModules
) {
  $sourceRoot = Get-CanonicalPath $Source
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  foreach ($file in @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse)) {
    $relative = $file.FullName.Substring($sourceRoot.Length).TrimStart("\", "/")
    $segments = $relative -split "[\\/]"
    if ($ExcludeNodeModules -and $segments -contains "node_modules") { continue }
    if (@($segments | Where-Object { $_ -in @(".runtime", "__pycache__", ".pytest_cache", ".mypy_cache", "tests") }).Count -gt 0) {
      continue
    }
    if ($file.Extension -in @(".pyc", ".pyo")) { continue }
    $target = Join-Path $Destination $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
  }
}

function Resolve-NodePackageDirectory(
  [string]$FromPackageDirectory,
  [string]$PackageName,
  [string]$SourceBoundary
) {
  if ($PackageName -notmatch "^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$") {
    throw "Node 依赖包名无效"
  }
  $boundary = Get-CanonicalPath $SourceBoundary
  $cursor = Get-CanonicalPath $FromPackageDirectory
  while ($cursor -eq $boundary -or $cursor.StartsWith(
      $boundary + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    $candidate = Get-CanonicalPath (Join-Path (Join-Path $cursor "node_modules") $PackageName)
    if (Test-Path -LiteralPath (Join-Path $candidate "package.json") -PathType Leaf) {
      $item = Get-Item -LiteralPath $candidate -Force
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Node 依赖包目录不得是重解析点"
      }
      return $candidate
    }
    if ($cursor -eq $boundary) { break }
    $parent = Split-Path -Parent $cursor
    if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
    $cursor = Get-CanonicalPath $parent
  }
  return $null
}

function Get-NodePackageDependencyRequests([object]$PackageJson) {
  $requests = @{}
  if ($null -ne $PackageJson.dependencies) {
    foreach ($property in $PackageJson.dependencies.PSObject.Properties) {
      $requests[[string]$property.Name] = $false
    }
  }
  if ($null -ne $PackageJson.optionalDependencies) {
    foreach ($property in $PackageJson.optionalDependencies.PSObject.Properties) {
      if (-not $requests.ContainsKey([string]$property.Name)) {
        $requests[[string]$property.Name] = $true
      }
    }
  }
  if ($null -ne $PackageJson.peerDependencies) {
    foreach ($property in $PackageJson.peerDependencies.PSObject.Properties) {
      $name = [string]$property.Name
      $meta = if ($null -ne $PackageJson.peerDependenciesMeta) {
        $PackageJson.peerDependenciesMeta.PSObject.Properties[$name]
      } else { $null }
      if ($null -ne $meta -and [bool]$meta.Value.optional) { continue }
      $requests[$name] = $false
    }
  }
  return @($requests.Keys | Sort-Object | ForEach-Object {
    [pscustomobject]@{ Name = [string]$_; Optional = [bool]$requests[$_] }
  })
}

function Read-PackageLockIndex([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "源码 package-lock.json 不存在"
  }
  try {
    if ($PSVersionTable.PSEdition -eq "Core") {
      $payload = [IO.File]::ReadAllText($Path) | ConvertFrom-Json -AsHashtable
    } else {
      if (-not ("System.Web.Script.Serialization.JavaScriptSerializer" -as [type])) {
        Add-Type -AssemblyName System.Web.Extensions
      }
      $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
      $serializer.MaxJsonLength = [int]::MaxValue
      $payload = $serializer.DeserializeObject([IO.File]::ReadAllText($Path))
    }
    $packages = $payload["packages"]
    if ([int]$payload["lockfileVersion"] -lt 3 -or
        $null -eq $packages -or -not ($packages -is [Collections.IDictionary])) {
      throw "unsupported package lock"
    }
    return [pscustomobject]@{
      LockfileVersion = [int]$payload["lockfileVersion"]
      Packages = $packages
    }
  } catch {
    throw "源码 package-lock.json 不支持 Wrangler runtime 闭包校验"
  }
}

function Copy-WranglerRuntimeClosure([string]$RuntimeToolsRoot) {
  $sourceNodeModules = Get-CanonicalPath (Join-Path $ExecutionRoot "node_modules")
  $wranglerSource = Get-CanonicalPath (Join-Path $sourceNodeModules "wrangler")
  $lockPath = Join-Path $ExecutionRoot "package-lock.json"
  if (-not (Test-Path -LiteralPath (Join-Path $wranglerSource "wrangler-dist\cli.js") -PathType Leaf)) {
    throw "Django runtime 部署缺少固定 Wrangler CLI"
  }
  $packageLock = Read-PackageLockIndex $lockPath

  $destinationNodeModules = Join-Path $RuntimeToolsRoot "node_modules"
  New-Item -ItemType Directory -Path $destinationNodeModules -Force | Out-Null
  $queue = [Collections.Queue]::new()
  $queue.Enqueue($wranglerSource)
  $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  $entries = @()
  while ($queue.Count -gt 0) {
    $packageSource = Get-CanonicalPath ([string]$queue.Dequeue())
    if (-not $packageSource.StartsWith(
        $sourceNodeModules + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      )) {
      throw "Wrangler runtime 依赖逃逸源码 node_modules"
    }
    $relative = $packageSource.Substring($sourceNodeModules.Length).TrimStart("\", "/")
    $relativeKey = $relative.Replace("\", "/")
    if ([string]::IsNullOrWhiteSpace($relativeKey) -or
        $relativeKey -eq ".." -or $relativeKey.StartsWith("../")) {
      throw "Wrangler runtime 依赖相对路径无效"
    }
    if (-not $seen.Add($relativeKey)) { continue }

    $packageJson = Read-JsonFile (Join-Path $packageSource "package.json") "Node 依赖 package.json"
    $name = [string]$packageJson.name
    $version = [string]$packageJson.version
    if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($version)) {
      throw "Node 依赖缺少名称或版本"
    }
    $lockKey = "node_modules/$relativeKey"
    $lockedPackage = $packageLock.Packages[$lockKey]
    if ($null -eq $lockedPackage -or
        [string]$lockedPackage["version"] -cne $version) {
      throw "Wrangler runtime 依赖与 package-lock.json 不一致：$lockKey"
    }

    Copy-ApplicationTree $packageSource (Join-Path $destinationNodeModules $relative) `
      -ExcludeNodeModules
    $entries += [pscustomobject]@{
      path = $lockKey
      name = $name
      version = $version
    }
    foreach ($request in @(Get-NodePackageDependencyRequests $packageJson)) {
      $resolved = Resolve-NodePackageDirectory $packageSource $request.Name $ExecutionRoot
      if ($null -eq $resolved) {
        if ($request.Optional) { continue }
        throw "Wrangler runtime 缺少必需依赖：$($request.Name)"
      }
      $queue.Enqueue($resolved)
    }
  }

  $wranglerEntry = @($entries | Where-Object { $_.name -ceq "wrangler" })
  if ($wranglerEntry.Count -ne 1 -or $entries.Count -lt 2) {
    throw "Wrangler runtime 依赖闭包不完整"
  }
  $manifest = [ordered]@{
    version = 1
    rootPackage = "wrangler"
    rootVersion = [string]$wranglerEntry[0].version
    packageLockSha256 = Get-FileSha256 $lockPath
    packages = @($entries | Sort-Object path)
  }
  Write-AtomicJson (Join-Path $RuntimeToolsRoot "wrangler-dependencies.json") $manifest
  return $manifest
}

function Assert-NoUnapprovedProtectedAiMigration([string]$Operation, [string]$CandidateBackendRoot = $BackendRoot) {
  # The protected AI sidecars need a separate privileged installer, private
  # backup identity and cross-cluster role/ACL recovery. Never enter a formal
  # app release with these migrations through the ordinary sales owner path.
  $formalRoot = [IO.Path]::GetFullPath('D:\teruisi-runtime\django-sales').TrimEnd('\')
  $currentRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
  if ($currentRoot -ine $formalRoot) { return }
  $migrationRoot = Join-Path $CandidateBackendRoot 'ai_assistant\migrations'
  foreach ($name in @(
      '0067_business_promotion_budget_v11_attestation.py',
      '0068_business_promotion_budget_v11_verifier_receipt.py',
      '0069_business_market_v2_paid_round_rehearsal.py',
      '0070_business_promotion_budget_v11_limited_identity.py',
      '0071_business_v4_report_source_link.py',
      '0072_business_market_v2_authority_proposals.py',
      '0073_business_promotion_budget_v11_login_attestation.py',
      '0074_business_market_v2_human_cap_approval.py',
      '0075_business_v4_report_restricted_page.py',
      '0076_business_promotion_budget_v11_ticket_bound_signer.py',
      '0077_business_market_v6_paused_topology.py',
      '0078_business_promotion_budget_v11_signed_publication.py',
      '0079_business_market_v6_source_ticket.py')) {
    if (Test-Path -LiteralPath (Join-Path $migrationRoot $name) -PathType Leaf) {
      throw "$Operation refuses protected AI migration release until the privileged installation and backup/restore gates are verified"
    }
  }
  $financeRawMigration = Join-Path $CandidateBackendRoot 'finance\migrations\0005_raw_column_evidence_v2.py'
  if (Test-Path -LiteralPath $financeRawMigration -PathType Leaf) {
    throw "$Operation refuses finance raw evidence v2 migration release until its independent backup/restore contract is verified"
  }
  $financeBytesMigration = Join-Path $CandidateBackendRoot 'finance\migrations\0006_raw_workbook_bytes_v2.py'
  if (Test-Path -LiteralPath $financeBytesMigration -PathType Leaf) {
    throw "$Operation refuses finance raw workbook bytes v2 migration release until its independent backup/restore contract is verified"
  }
}

function Prepare-Application {
  if ((Get-CanonicalPath $ExecutionRoot) -eq (Get-CanonicalPath $InstalledAppRoot)) {
    throw "DeployApp 必须从源码工作树脚本执行，不能从 runtime app 自我覆盖"
  }
  Assert-NoUnapprovedProtectedAiMigration "PrepareApp"
  if (-not (Test-Path -LiteralPath $BackendRoot -PathType Container)) { throw "源码 backend 不存在" }
  if (-not (Test-Path -LiteralPath $DingTalkReplenishmentConfigSource -PathType Leaf)) {
    throw "源码缺少钉钉备货计划同步配置"
  }
  if (-not (Test-Path -LiteralPath $InventoryWarehouseMappingConfigSource -PathType Leaf)) {
    throw "源码缺少库存仓库类型映射配置"
  }
  New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
  $predecessor = Get-InstalledApplicationBinding
  $id = [Guid]::NewGuid().ToString("N")
  $staging = Assert-RuntimeChildPath (Join-Path $RuntimeRoot ("app.deploy-" + $id))
  $receiptPath = Assert-RuntimeChildPath (Join-Path $RuntimeRoot ("app.prepare-" + $id + ".json"))
  $prepared = $false
  try {
    New-Item -ItemType Directory -Path (Join-Path $staging "backend"), (Join-Path $staging "config"), (Join-Path $staging "tools"), (Join-Path $staging "drizzle"), (Join-Path $staging "runtime-tools") -Force | Out-Null
    Copy-ApplicationTree $BackendRoot (Join-Path $staging "backend")
    Copy-Item -LiteralPath $DingTalkReplenishmentConfigSource `
      -Destination (Join-Path $staging "config\dingtalk-replenishment.json") -Force
    Copy-Item -LiteralPath $InventoryWarehouseMappingConfigSource `
      -Destination (Join-Path $staging "config\inventory-warehouse-mapping.json") -Force
    Copy-WranglerRuntimeClosure (Join-Path $staging "runtime-tools") | Out-Null
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $staging "tools\django-local-service.ps1") -Force
    foreach ($relative in @(
      "tools\sales-d1-retirement.ts",
      "tools\sales-d1-write-authority.ts",
      "tools\sales-legacy-r2-cleanup.ts",
      "tools\sales-local-cutover.ts",
      "tools\sales-local-cutover-operator.ps1",
      "tools\sales-local-cutover-rehearsal.ps1",
      "tools\sales-cutover-snapshot-gate.py",
      "tools\worker-local-release.mjs",
      "tools\sqlite-consistent-backup.py",
      "tools\sales-local-cutover-backup.ps1",
      "tools\sales-local-cutover-backup-prune.ps1",
      "tools\django-finance-cutover.ps1",
      "tools\django-runtime-supervisor.ps1",
      "tools\django-netshop-service.ps1",
      "tools\django-market-service.ps1",
      "tools\django-products-service.ps1",
      "tools\django-workflow-service.ps1",
      "tools\django-workflow-cutover.ps1",
      "tools\django-workflow-operations-cutover.ps1",
      "tools\workflow-operations-production-smoke.ps1",
      "tools\workflow-operations-consumer-smoke.ts",
      "tools\workflow-operations-d1-rejection-smoke.py",
      "tools\django-inventory-service.ps1",
      "tools\django-customer-service.ps1",
      "tools\django-access-control.ps1",
      "tools\django-ai.ps1",
      "tools\django-ai-cutover.ps1",
      "tools\ai-domain-snapshot.py",
      "tools\django-access-control-cutover.ps1",
      "tools\django-erp-reference.ps1",
      "tools\django-bi-service.ps1",
      "tools\django-customer-service-cutover.ps1",
      "tools\django-erp-reference-cutover.ps1",
      "tools\customer-service-production-smoke.ps1",
      "tools\customer-service-consumer-smoke.ts",
      "tools\customer-service-d1-rejection-smoke.py",
      "tools\customer-service-r2-retirement-evidence.py",
      "tools\erp-reference-production-smoke.ps1",
      "tools\erp-reference-consumer-smoke.ts",
      "tools\erp-reference-d1-rejection-smoke.py",
      "tools\erp-reference-r2-retirement-evidence.py",
      "tools\erp-reference-d1-snapshot.py",
      "tools\django-inventory-cutover.ps1",
      "tools\django-postgres-maintenance.ps1",
      "tools\finance-d1-authority-install.py",
      "tools\finance_d1_rehearsal_snapshot.py",
      "tools\workflow-d1-authority-install.py",
      "tools\workflow-d1-snapshot.py",
      "tools\workflow-launch-r2-retirement-evidence.py",
      "tools\workflow-operations-d1-authority-install.py",
      "tools\workflow-operations-d1-snapshot.py",
      "tools\inventory-d1-authority-install.py",
      "tools\inventory-r2-retirement-evidence.py",
      "tools\customer-service-d1-authority-install.py",
      "tools\erp-reference-d1-authority-install.py",
      "tools\customer-service-d1-snapshot.py",
      "tools\postgres-consistent-backup.py",
      "drizzle\0090_sales_write_authority.sql",
      "drizzle\0091_erp_reference_projection.sql",
      "drizzle\0092_sales_domain_retirement.sql",
      "drizzle\0093_finance_write_authority.sql",
      "drizzle\0094_netshop_write_authority.sql",
      "drizzle\0095_market_netshop_projection.sql",
      "drizzle\0096_netshop_domain_retirement.sql",
      "drizzle\0097_market_write_authority.sql",
      "drizzle\0098_market_domain_retirement.sql",
      "drizzle\0099_product_write_authority.sql",
      "drizzle\0100_product_domain_retirement.sql",
      "drizzle\0101_inventory_write_authority.sql",
      "drizzle\0109_erp_reference_write_authority.sql",
      "drizzle\0110_erp_reference_domain_retirement.sql",
      "drizzle\0111_access_control_write_authority.sql",
      "drizzle\0112_access_control_domain_retirement.sql",
      "drizzle\0113_ai_write_authority.sql",
      "drizzle\0114_ai_domain_retirement.sql",
      "drizzle\0102_inventory_domain_retirement.sql",
      "drizzle\0103_workflow_launch_write_authority.sql",
      "drizzle\0104_workflow_launch_domain_retirement.sql",
      "drizzle\0105_workflow_operations_write_authority.sql",
      "drizzle\0106_workflow_operations_domain_retirement.sql",
      "drizzle\0107_customer_service_write_authority.sql",
      "drizzle\0108_customer_service_domain_retirement.sql"
    )) {
      $source = Join-Path $ExecutionRoot $relative
      if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Django runtime 部署缺少受控 retirement operator 文件：$relative"
      }
      Copy-Item -LiteralPath $source -Destination (Join-Path $staging $relative) -Force
    }
    Assert-WranglerLocalR2RoundTrip $staging
    $fingerprintEvidence = Get-ApplicationTreeFingerprintEvidence $staging
    $fingerprint = [string]$fingerprintEvidence.Fingerprint
    Write-AtomicJson (Join-Path $staging "deployment.json") ([ordered]@{
      version = 2
      deployedAt = [DateTimeOffset]::Now.ToString("o")
      sourceRoot = $ExecutionRoot
      fingerprintAlgorithm = $ApplicationFingerprintAlgorithm
      fileCount = [int64]$fingerprintEvidence.FileCount
      appFingerprint = $fingerprint
    })
    if ((Get-InstalledApplicationBinding) -cne $predecessor) { throw "Django predecessor changed during preparation" }
    Write-AtomicJson $receiptPath ([ordered]@{
      version = "teruisi-django-prepared-app-v1"; id = $id
      runtimeRoot = Get-CanonicalPath $RuntimeRoot
      predecessorManifestSha256 = $predecessor
      candidateManifestSha256 = Get-FileSha256 (Join-Path $staging "deployment.json")
    })
    $prepared = $true
    Write-LauncherEvent "INFO" "runtime_app_prepared" $fingerprint
    return [pscustomobject]@{ id = $id; receiptSha256 = Get-FileSha256 $receiptPath; appFingerprint = $fingerprint }
  } finally {
    if (-not $prepared -and (Test-Path -LiteralPath $staging)) {
      try { Remove-Item -LiteralPath $staging -Recurse -Force }
      catch { Write-LauncherEvent "WARN" "prepared_cleanup_incomplete" $id }
    }
  }
}

function Get-InstalledApplicationBinding {
  if (-not (Test-Path -LiteralPath $InstalledAppRoot)) { return "absent" }
  Assert-DeployedApplication
  return Get-FileSha256 $DeploymentManifestPath
}

function Get-PreparedApplication([string]$Id, [string]$ApprovedSha256) {
  if ($Id -cnotmatch "^[0-9a-f]{32}$" -or $ApprovedSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "Prepared app requires an exact id and approved receipt SHA-256"
  }
  $staging = Assert-RuntimeChildPath (Join-Path $RuntimeRoot ("app.deploy-" + $Id))
  $receiptPath = Assert-RuntimeChildPath (Join-Path $RuntimeRoot ("app.prepare-" + $Id + ".json"))
  foreach ($candidatePath in @($staging, $receiptPath)) {
    $cursor = $candidatePath
    while ($cursor) {
      $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
      if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Prepared app contains a reparse point" }
      $cursor = Split-Path -Parent $cursor
    }
  }
  if ((Get-Item -LiteralPath $receiptPath).Length -gt 4096 -or (Get-FileSha256 $receiptPath) -cne $ApprovedSha256) {
    throw "Prepared app receipt SHA-256 changed"
  }
  $receipt = Read-JsonFile $receiptPath "Prepared app receipt"
  if (-not (Test-ExactObjectPropertyNames $receipt @("version", "id", "runtimeRoot", "predecessorManifestSha256", "candidateManifestSha256")) -or
      $receipt.version -cne "teruisi-django-prepared-app-v1" -or $receipt.id -cne $Id -or
      $receipt.runtimeRoot -ine (Get-CanonicalPath $RuntimeRoot) -or
      [string]$receipt.candidateManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      (Get-FileSha256 (Join-Path $staging "deployment.json")) -cne $receipt.candidateManifestSha256) {
    throw "Prepared app receipt binding is invalid"
  }
  if ((Get-InstalledApplicationBinding) -cne $receipt.predecessorManifestSha256) { throw "Prepared app predecessor changed; prepare again" }
  $directories = [Collections.Stack]::new()
  $directories.Push([IO.DirectoryInfo]::new($staging))
  while ($directories.Count -gt 0) {
    $directory = [IO.DirectoryInfo]$directories.Pop()
    foreach ($entry in $directory.EnumerateFileSystemInfos()) {
      if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Prepared app contains a reparse point" }
      if ($entry -is [IO.DirectoryInfo]) { $directories.Push($entry) }
    }
  }
  Assert-ApplicationTreeManifest $staging "Prepared Django app" | Out-Null
  return $staging
}

function Deploy-Application {
  if ((Get-CanonicalPath $ExecutionRoot) -eq (Get-CanonicalPath $InstalledAppRoot)) {
    throw "DeployApp must run from a source checkout, never from the installed app"
  }
  Assert-ProductionMaintenance "DeployApp"
  Assert-ApplicationDeploymentStopped "DeployApp"
  if ([string]::IsNullOrWhiteSpace($PreparedAppId) -and [string]::IsNullOrWhiteSpace($PreparedAppSha256)) {
    $prepared = Prepare-Application
    $id = $prepared.id; $approvedSha256 = $prepared.receiptSha256
  } else { $id = $PreparedAppId; $approvedSha256 = $PreparedAppSha256 }
  $staging = Get-PreparedApplication $id $approvedSha256
  Assert-NoUnapprovedProtectedAiMigration "DeployApp" (Join-Path $staging "backend")
  $backup = Assert-RuntimeChildPath (Join-Path $RuntimeRoot "app.previous")
  Assert-ProductionMaintenance "DeployApp"
  Assert-ApplicationDeploymentStopped "DeployApp"
  if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
  if (Test-Path -LiteralPath $InstalledAppRoot) {
    Move-Item -LiteralPath $InstalledAppRoot -Destination $backup
  }
  try {
    Move-Item -LiteralPath $staging -Destination $InstalledAppRoot
    Assert-DeployedApplication
  } catch {
    if (Test-Path -LiteralPath $InstalledAppRoot) { Remove-Item -LiteralPath $InstalledAppRoot -Recurse -Force }
    if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $InstalledAppRoot }
    throw
  }
  Write-LauncherEvent "INFO" "runtime_app_deployed" (Get-FileSha256 $DeploymentManifestPath)
  Write-Output "Django app 已复制到受控 runtime；安装自启动前还必须执行 HardenAcl。"
}

function Rollback-Application {
  Assert-ProductionMaintenance "RollbackApp"
  Assert-ApplicationDeploymentStopped "RollbackApp"
  $backup = Assert-RuntimeChildPath (Join-Path $RuntimeRoot "app.previous")
  if (-not (Test-Path -LiteralPath $InstalledAppRoot -PathType Container)) {
    throw "RollbackApp 缺少当前 runtime app"
  }
  if (-not (Test-Path -LiteralPath $backup -PathType Container)) {
    throw "RollbackApp 没有可用的 app.previous"
  }
  Assert-ApplicationTreeManifest $InstalledAppRoot "当前 runtime app" | Out-Null
  $previousManifest = Assert-ApplicationTreeManifest $backup "待回滚 runtime app"
  $holding = Assert-RuntimeChildPath (Join-Path $RuntimeRoot ("app.rollback-" + [Guid]::NewGuid().ToString("N")))
  try {
    Move-Item -LiteralPath $InstalledAppRoot -Destination $holding
    try {
      Move-Item -LiteralPath $backup -Destination $InstalledAppRoot
      Assert-DeployedApplication
      Move-Item -LiteralPath $holding -Destination $backup
    } catch {
      if (Test-Path -LiteralPath $InstalledAppRoot -PathType Container) {
        Move-Item -LiteralPath $InstalledAppRoot -Destination $backup
      }
      if (Test-Path -LiteralPath $holding -PathType Container) {
        Move-Item -LiteralPath $holding -Destination $InstalledAppRoot
      }
      throw
    }
    Write-LauncherEvent "INFO" "runtime_app_rolled_back" ([string]$previousManifest.appFingerprint)
    Write-Output "Django runtime app 已仅回滚代码；数据库 migration 与业务数据未改变。"
  } finally {
    if (Test-Path -LiteralPath $holding -PathType Container) {
      throw "RollbackApp 未能收口临时代码目录：$holding"
    }
  }
}

function Get-ProcessCreation([object]$Process) {
  return ([datetime]$Process.CreationDate).ToUniversalTime().ToString("o")
}

function ConvertTo-CanonicalCreationDate([object]$Value) {
  if ($null -eq $Value) { throw "进程所有权记录 creationDate 缺失" }
  if ($Value -is [datetime]) {
    return ([datetime]$Value).ToUniversalTime().ToString("o")
  }
  if ($Value -is [DateTimeOffset]) {
    return ([DateTimeOffset]$Value).UtcDateTime.ToString("o")
  }
  $parsed = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParseExact(
    [string]$Value,
    "o",
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::RoundtripKind,
    [ref]$parsed
  )) {
    throw "进程所有权记录 creationDate 不是规范 ISO 时间"
  }
  return $parsed.UtcDateTime.ToString("o")
}

function Get-SystemBootTimeUtc {
  try {
    $operatingSystem = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
  } catch {
    throw "无法读取 Windows 当前启动时间"
  }
  if ($null -eq $operatingSystem -or $null -eq $operatingSystem.LastBootUpTime) {
    throw "Windows 当前启动时间缺失"
  }
  try {
    return ([datetime]$operatingSystem.LastBootUpTime).ToUniversalTime()
  } catch {
    throw "Windows 当前启动时间无效"
  }
}

function Test-ProcessRecordFromPreviousBoot(
  [string]$PidPath,
  [object]$CreationDate,
  [object]$StartedAt
) {
  try {
    $bootTimeUtc = Get-SystemBootTimeUtc
    $creationTimeUtc = [DateTimeOffset]::ParseExact(
      (ConvertTo-CanonicalCreationDate $CreationDate),
      "o",
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
    $startedTimeUtc = [DateTimeOffset]::ParseExact(
      (ConvertTo-CanonicalCreationDate $StartedAt),
      "o",
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
    $recordFile = Get-Item -LiteralPath $PidPath -ErrorAction Stop
    return (
      $creationTimeUtc -lt $bootTimeUtc -and
      $startedTimeUtc -lt $bootTimeUtc -and
      $recordFile.LastWriteTimeUtc -lt $bootTimeUtc
    )
  } catch {
    return $false
  }
}

function Remove-PreviousBootProcessRecordIfSafe(
  [string]$PidPath,
  [object]$CreationDate,
  [object]$StartedAt
) {
  if (-not (Test-ProcessRecordFromPreviousBoot $PidPath $CreationDate $StartedAt)) {
    return $false
  }
  Remove-Item -LiteralPath $PidPath -Force
  Write-LauncherEvent "WARN" "previous_boot_process_record_removed" $PidPath
  return $true
}

function Get-ProcessSnapshot([int]$ProcessId, [int]$Attempts = 20) {
  for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($process -and $process.ExecutablePath -and $process.CommandLine -and $process.CreationDate) {
      return [pscustomobject]@{
        Process = $process
        ProcessId = [int]$process.ProcessId
        CreationDate = Get-ProcessCreation $process
        ExecutablePath = Get-CanonicalPath ([string]$process.ExecutablePath)
        CommandLine = [string]$process.CommandLine
      }
    }
    Start-Sleep -Milliseconds 100
  }
  return $null
}

function Test-ProcessSnapshotIdentity([object]$Left, [object]$Right) {
  return (
    [int]$Left.ProcessId -eq [int]$Right.ProcessId -and
    [string]$Left.CreationDate -ceq [string]$Right.CreationDate -and
    [string]$Left.ExecutablePath -ieq [string]$Right.ExecutablePath -and
    [string]$Left.CommandLine -ceq [string]$Right.CommandLine
  )
}

function Get-VerifiedProcessDescendants([object]$RootSnapshot) {
  $rootProcessId = [int]$RootSnapshot.ProcessId
  $pending = [Collections.Generic.Queue[object]]::new()
  $pending.Enqueue($RootSnapshot)
  $seen = @{}
  $seen[$RootProcessId] = $true
  $descendants = [Collections.Generic.List[object]]::new()
  while ($pending.Count -gt 0) {
    $parentSnapshot = $pending.Dequeue()
    $parentId = [int]$parentSnapshot.ProcessId
    $currentParent = Get-ProcessSnapshot $parentId 1
    if ($null -eq $currentParent -or -not (Test-ProcessSnapshotIdentity $currentParent $parentSnapshot)) {
      throw "服务进程谱系在枚举前发生变化，已拒绝停止：$parentId"
    }
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId" -ErrorAction Stop)
    $currentParent = Get-ProcessSnapshot $parentId 1
    if ($null -eq $currentParent -or -not (Test-ProcessSnapshotIdentity $currentParent $parentSnapshot)) {
      throw "服务进程谱系在枚举后发生变化，已拒绝停止：$parentId"
    }
    foreach ($child in $children) {
      $childId = [int]$child.ProcessId
      if ($seen.ContainsKey($childId)) { continue }
      $seen[$childId] = $true
      $snapshot = Get-ProcessSnapshot $childId 1
      if ($null -eq $snapshot -or [int]$snapshot.Process.ParentProcessId -ne $parentId) { continue }
      if (
        $snapshot.CreationDate -cne (Get-ProcessCreation $child) -or
        $snapshot.ExecutablePath -ine (Get-CanonicalPath ([string]$child.ExecutablePath)) -or
        $snapshot.CommandLine -cne [string]$child.CommandLine
      ) {
        throw "服务子进程身份在枚举期间发生变化，已拒绝停止：$childId"
      }
      [void]$descendants.Add($snapshot)
      $pending.Enqueue($snapshot)
    }
  }
  return @($descendants.ToArray())
}

function Stop-VerifiedProcessTree([object]$RootSnapshot) {
  $currentRoot = Get-ProcessSnapshot ([int]$RootSnapshot.ProcessId) 1
  if ($null -eq $currentRoot) { return 0 }
  if (-not (Test-ProcessSnapshotIdentity $currentRoot $RootSnapshot)) {
    Write-LauncherEvent "WARN" "root_pid_reused" "pid=$($RootSnapshot.ProcessId)"
    throw "服务根 PID 已复用，已拒绝停止：$($RootSnapshot.ProcessId)"
  }
  $descendants = @(Get-VerifiedProcessDescendants $RootSnapshot)
  $currentRoot = Get-ProcessSnapshot ([int]$RootSnapshot.ProcessId) 1
  if ($currentRoot -and -not (Test-ProcessSnapshotIdentity $currentRoot $RootSnapshot)) {
    Write-LauncherEvent "WARN" "root_pid_reused" "pid=$($RootSnapshot.ProcessId)"
    throw "服务根 PID 在停止前已复用，已拒绝停止：$($RootSnapshot.ProcessId)"
  }
  if ($currentRoot) {
    Stop-Process -Id $RootSnapshot.ProcessId -Force -ErrorAction Stop
    Wait-Process -Id $RootSnapshot.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
  }
  $remainingRoot = Get-ProcessSnapshot ([int]$RootSnapshot.ProcessId) 1
  if ($remainingRoot -and (Test-ProcessSnapshotIdentity $remainingRoot $RootSnapshot)) {
    throw "已验证的服务根进程未能停止：$($RootSnapshot.ProcessId)"
  }

  [array]::Reverse($descendants)
  foreach ($snapshot in $descendants) {
    $current = Get-ProcessSnapshot ([int]$snapshot.ProcessId) 1
    if ($null -eq $current) { continue }
    if (-not (Test-ProcessSnapshotIdentity $current $snapshot)) {
      Write-LauncherEvent "WARN" "descendant_pid_reused" "pid=$($snapshot.ProcessId)"
      continue
    }
    Stop-Process -Id $snapshot.ProcessId -Force -ErrorAction Stop
    Wait-Process -Id $snapshot.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    $remaining = Get-ProcessSnapshot ([int]$snapshot.ProcessId) 1
    if ($remaining -and (Test-ProcessSnapshotIdentity $remaining $snapshot)) {
      throw "已验证的服务子进程未能停止：$($snapshot.ProcessId)"
    }
  }
  return $descendants.Count
}

function Test-ExactStringArray([object[]]$Left, [object[]]$Right) {
  if ($Left.Count -ne $Right.Count) { return $false }
  for ($index = 0; $index -lt $Left.Count; $index++) {
    if ([string]$Left[$index] -cne [string]$Right[$index]) { return $false }
  }
  return $true
}

function Test-CommandLineReferencesPath([string]$CommandLine, [string]$Path) {
  $canonical = Get-CanonicalPath $Path
  $variants = @($canonical, $canonical.Replace("\", "/"))
  foreach ($variant in $variants) {
    if ($CommandLine.IndexOf($variant, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
  }
  return $false
}

function Get-ConfigFingerprint([string]$Service, [string]$Executable, [string[]]$Arguments) {
  $criticalFiles = @(
    $ConfigPath, $CredentialPath, $PSCommandPath,
    (Join-Path $BackendRoot "teruisi_backend\settings.py"),
    (Join-Path $BackendRoot "teruisi_backend\health.py"),
    (Join-Path $BackendRoot "teruisi_backend\security.py"),
    (Join-Path $BackendRoot "sales\authority.py"),
    (Join-Path $BackendRoot "sales\write_service.py"),
    (Join-Path $BackendRoot "sales\write_views.py"),
    (Join-Path $BackendRoot "erp_reference\import_service.py"),
    (Join-Path $BackendRoot "erp_reference\query.py"),
    (Join-Path $BackendRoot "erp_reference\views.py")
  )
  $material = [ordered]@{
    service = $Service
    runtimeRoot = Get-CanonicalPath $RuntimeRoot
    executionRoot = Get-CanonicalPath $ExecutionRoot
    executable = Get-CanonicalPath $Executable
    executableHash = Get-FileSha256 $Executable
    arguments = @($Arguments)
    files = @($criticalFiles | ForEach-Object { [ordered]@{ path = $_; sha256 = Get-FileSha256 $_ } })
  } | ConvertTo-Json -Depth 8 -Compress
  return Get-Sha256Text $material
}

function Resolve-OwnedProcess(
  [string]$Service,
  [string]$PidPath,
  [string]$ExpectedLauncher,
  [string[]]$ExpectedArguments = $null,
  [string]$ExpectedFingerprint = ""
) {
  if (-not (Test-Path -LiteralPath $PidPath -PathType Leaf)) { return $null }
  $record = Read-JsonFile $PidPath "进程所有权记录"
  $processId = 0
  if (
    [int]$record.version -ne 2 -or
    [string]$record.service -cne $Service -or
    -not [int]::TryParse([string]$record.processId, [ref]$processId) -or
    -not ([string]$record.configFingerprint -match "^[0-9a-f]{64}$") -or
    [string]::IsNullOrWhiteSpace([string]$record.launcherPath)
  ) {
    throw "进程所有权记录格式无效：$PidPath"
  }
  $recordCreationDate = ConvertTo-CanonicalCreationDate $record.creationDate
  $snapshot = Get-ProcessSnapshot $processId 1
  if ($null -eq $snapshot) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-LauncherEvent "WARN" "stale_process_record_removed" $PidPath
    return $null
  }
  $recordArguments = @($record.arguments | ForEach-Object { [string]$_ })
  $matches = (
    $snapshot.CreationDate -ceq $recordCreationDate -and
    $snapshot.ExecutablePath -ieq (Get-CanonicalPath ([string]$record.executablePath)) -and
    (Get-CanonicalPath ([string]$record.launcherPath)) -ieq (Get-CanonicalPath $ExpectedLauncher) -and
    $snapshot.CommandLine -ceq [string]$record.commandLine -and
    (Test-CommandLineReferencesPath $snapshot.CommandLine $ExpectedLauncher)
  )
  if ($null -ne $ExpectedArguments) { $matches = $matches -and (Test-ExactStringArray $recordArguments $ExpectedArguments) }
  if ($ExpectedFingerprint) { $matches = $matches -and ([string]$record.configFingerprint -ceq $ExpectedFingerprint) }
  if (-not $matches) {
    if (Remove-PreviousBootProcessRecordIfSafe $PidPath $record.creationDate $record.startedAt) {
      return $null
    }
    throw "PID 已复用或进程身份与所有权记录不一致；拒绝接管或终止：$PidPath"
  }
  return $snapshot
}

function ConvertTo-ProcessArgument([string]$Value) {
  if ($Value.Contains('"')) { throw "进程参数不得包含双引号" }
  if ($Value -match "\s") { return '"' + $Value + '"' }
  return $Value
}

function Start-ManagedProcess(
  [string]$Service,
  [string]$Executable,
  [string[]]$Arguments,
  [string]$WorkingDirectory,
  [string]$PidPath,
  [string]$ConfigFingerprint,
  [string]$StdoutPath,
  [string]$StderrPath
) {
  $launchArguments = @($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ })
  $process = $null
  try {
    $process = Start-Process -FilePath $Executable -ArgumentList $launchArguments `
      -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
      -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -PassThru
    $snapshot = Get-ProcessSnapshot $process.Id
    if ($null -eq $snapshot) { throw "$Service 进程未能建立可验证身份" }
    Write-AtomicJson $PidPath ([ordered]@{
      version = 2
      service = $Service
      processId = $snapshot.ProcessId
      creationDate = $snapshot.CreationDate
      launcherPath = Get-CanonicalPath $Executable
      executablePath = $snapshot.ExecutablePath
      commandLine = $snapshot.CommandLine
      arguments = @($Arguments)
      configFingerprint = $ConfigFingerprint
      startedAt = [DateTimeOffset]::Now.ToString("o")
      runId = $RunId
    })
    Write-LauncherEvent "INFO" "process_started" "$Service pid=$($snapshot.ProcessId)"
    return $snapshot.Process
  } catch {
    if ($process -and -not $process.HasExited) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $PidPath) { Remove-Item -LiteralPath $PidPath -Force }
    throw
  }
}

function Stop-OwnedProcess([string]$Service, [string]$PidPath, [string]$ExpectedExecutable) {
  $process = Resolve-OwnedProcess $Service $PidPath $ExpectedExecutable
  if ($process) {
    $descendantCount = Stop-VerifiedProcessTree $process
    Write-LauncherEvent "INFO" "process_stopped" "$Service pid=$($process.ProcessId) descendants=$descendantCount"
  }
  if (Test-Path -LiteralPath $PidPath) { Remove-Item -LiteralPath $PidPath -Force }
}

function Get-ErpReferenceSyncCandidates {
  $managePath = Join-Path $InstalledAppRoot "backend\manage.py"
  try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  } catch {
    throw "无法可靠枚举 ERP reference sync 进程；拒绝继续服务操作"
  }
  return @(
    $processes | Where-Object {
      $_.CommandLine -and
      (Test-CommandLineReferencesPath ([string]$_.CommandLine) $managePath) -and
      ([string]$_.CommandLine -match '(?i)(?:^|\s|")sync_erp_reference(?:\s|"|$)')
    }
  )
}

function Get-DjangoSupervisorCandidates {
  $supervisorPath = Join-Path $InstalledAppRoot "tools\django-runtime-supervisor.ps1"
  try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  } catch {
    throw "无法可靠枚举 Django supervisor 进程；拒绝继续服务操作"
  }
  return @(
    $processes | Where-Object {
      $_.CommandLine -and
      (Test-CommandLineReferencesPath ([string]$_.CommandLine) $supervisorPath) -and
      ([string]$_.CommandLine -match '(?i)(?:^|\s|"|-)Action(?:\s+|"\s*)Run(?:\s|"|$)')
    }
  )
}

function Assert-DjangoSupervisorStopped([string]$Operation) {
  if (Test-Path -LiteralPath $DjangoSupervisorPidPath -PathType Leaf) {
    $receipt = Read-JsonFile $DjangoSupervisorPidPath "Django supervisor process receipt"
    if (-not (Test-ExactObjectPropertyNames $receipt @(
          "version", "processId", "creationDate", "executablePath", "commandLine",
          "scriptPathSha256", "scriptSha256", "startedAt"
        )) -or
        [string]$receipt.version -cne "teruisi-django-supervisor-process-v1") {
      throw "$Operation 发现无效 Django supervisor process receipt；拒绝修改运行环境"
    }
    $snapshot = Get-ProcessSnapshot ([int]$receipt.processId) 1
    if ($null -ne $snapshot) {
      $creation = ConvertTo-CanonicalCreationDate $receipt.creationDate
      if ([string]$snapshot.CreationDate -cne $creation -or
          [string]$snapshot.ExecutablePath -ine [string]$receipt.executablePath -or
          [string]$snapshot.CommandLine -cne [string]$receipt.commandLine) {
        if (-not (Remove-PreviousBootProcessRecordIfSafe `
              $DjangoSupervisorPidPath $receipt.creationDate $receipt.startedAt)) {
          throw "$Operation 发现 Django supervisor PID 复用或身份变化"
        }
        $snapshot = $null
      }
      if ($null -ne $snapshot) {
        throw "$Operation 前必须先 Disarm 并等待 Django supervisor 退出"
      }
    }
    if (Test-Path -LiteralPath $DjangoSupervisorPidPath -PathType Leaf) {
      Remove-Item -LiteralPath $DjangoSupervisorPidPath -Force
    }
  }
  if (@(Get-DjangoSupervisorCandidates).Count -gt 0) {
    throw "$Operation 发现未登记的 Django supervisor；拒绝修改或自动终止"
  }
}

function Assert-ApplicationProcessesStopped([string]$Operation) {
  Assert-DjangoSupervisorStopped $Operation
  if (
    @(Get-PortListeners 8001).Count -gt 0 -or
    @(Get-PortListeners 8002).Count -gt 0 -or
    @(Get-PortListeners 8011).Count -gt 0 -or
    @(Get-PortListeners 8012).Count -gt 0 -or
    @(Get-PortListeners 8021).Count -gt 0 -or
    @(Get-PortListeners 8022).Count -gt 0 -or
    @(Get-PortListeners 8031).Count -gt 0 -or
    @(Get-PortListeners 8032).Count -gt 0 -or
    @(Get-PortListeners 8041).Count -gt 0 -or
    @(Get-PortListeners 8042).Count -gt 0 -or
    @(Get-PortListeners 8051).Count -gt 0 -or
    @(Get-PortListeners 8052).Count -gt 0 -or
    @(Get-PortListeners 8061).Count -gt 0 -or
    @(Get-PortListeners 8062).Count -gt 0 -or
    @(Get-PortListeners 8071).Count -gt 0 -or
    @(Get-PortListeners 8072).Count -gt 0 -or
    @(Get-PortListeners 8081).Count -gt 0 -or
    @(Get-PortListeners 8091).Count -gt 0 -or
    @(Get-PortListeners 8092).Count -gt 0 -or
    @(Get-PortListeners 8101).Count -gt 0 -or
    @(Get-PortListeners 8102).Count -gt 0
  ) {
    throw "$Operation 前必须停止全部 Django 业务域 reader/writer"
  }
  if (Resolve-OwnedProcess "django-reader" $DjangoReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django reader"
  }
  if (Resolve-OwnedProcess "django-writer" $DjangoWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django writer"
  }
  if (Resolve-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django finance reader"
  }
  if (Resolve-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 Stop 停止 Django finance writer"
  }
  if (Resolve-OwnedProcess "django-netshop-reader" $DjangoNetshopReaderPidPath $Waitress) {
    throw "$Operation 前必须通过网店控制器 Stop 停止 Django netshop reader"
  }
  if (Resolve-OwnedProcess "django-netshop-writer" $DjangoNetshopWriterPidPath $Waitress) {
    throw "$Operation 前必须通过网店控制器 Stop 停止 Django netshop writer"
  }
  if (Resolve-OwnedProcess "django-market-reader" $DjangoMarketReaderPidPath $Waitress) {
    throw "$Operation 前必须通过市场控制器 Stop 停止 Django market reader"
  }
  if (Resolve-OwnedProcess "django-market-writer" $DjangoMarketWriterPidPath $Waitress) {
    throw "$Operation 前必须通过市场控制器 Stop 停止 Django market writer"
  }
  if (Resolve-OwnedProcess "django-workflow-reader" $DjangoWorkflowReaderPidPath $Waitress) {
    throw "$Operation 前必须通过运营事务新品控制器 Stop 停止 Django workflow reader"
  }
  if (Resolve-OwnedProcess "django-workflow-writer" $DjangoWorkflowWriterPidPath $Waitress) {
    throw "$Operation 前必须通过运营事务新品控制器 Stop 停止 Django workflow writer"
  }
  if (Resolve-OwnedProcess "django-inventory-reader" $DjangoInventoryReaderPidPath $Waitress) {
    throw "$Operation 前必须通过库存控制器 Stop 停止 Django inventory reader"
  }
  if (Resolve-OwnedProcess "django-inventory-writer" $DjangoInventoryWriterPidPath $Waitress) {
    throw "$Operation 前必须通过库存控制器 Stop 停止 Django inventory writer"
  }
  if (Resolve-OwnedProcess "django-customer-service-reader" $DjangoCustomerServiceReaderPidPath $Waitress) {
    throw "$Operation 前必须通过客服控制器 Stop 停止 Django customer-service reader"
  }
  if (Resolve-OwnedProcess "django-customer-service-writer" $DjangoCustomerServiceWriterPidPath $Waitress) {
    throw "$Operation 前必须通过客服控制器 Stop 停止 Django customer-service writer"
  }
  foreach ($dingService in @("django-ai-dingtalk", "django-ai-dingtalk-schedule")) {
    if (Resolve-OwnedProcess $dingService (Join-Path $RunDirectory "$dingService.pid.json") $Python) {
      throw "$Operation requires the owned DingTalk processes to be stopped"
    }
  }
  if ((Resolve-OwnedProcess "django-ai-reader" $DjangoAiReaderPidPath $Waitress) -or (Resolve-OwnedProcess "django-ai-writer" $DjangoAiWriterPidPath $Waitress) -or @(Get-PortListeners 8111).Count -gt 0 -or @(Get-PortListeners 8112).Count -gt 0) {
    throw "$Operation 前必须通过 AI 控制器 Stop 停止 AI 服务并核验端口身份"
  }
  if (@(Get-PortListeners 8121).Count -gt 0 -or (Resolve-OwnedProcess "ai-pandas-wsl" (Join-Path $RunDirectory "ai-pandas-wsl.pid.json") (Join-Path $env:SystemRoot "System32\wsl.exe"))) {
    throw "$Operation requires the pandas broker and owned WSL keepalive to be stopped"
  }
  if (Resolve-OwnedProcess "django-access-control-reader" $DjangoAccessControlReaderPidPath $Waitress) {
    throw "$Operation 前必须通过权限控制器 Stop 停止 Django access-control reader"
  }
  if (Resolve-OwnedProcess "django-access-control-writer" $DjangoAccessControlWriterPidPath $Waitress) {
    throw "$Operation 前必须通过权限控制器 Stop 停止 Django access-control writer"
  }
  if (Resolve-OwnedProcess "django-erp-reference-reader" $DjangoErpReferenceReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 ERP 主数据控制器 Stop 停止 Django ERP reader"
  }
  if (Resolve-OwnedProcess "django-erp-reference-writer" $DjangoErpReferenceWriterPidPath $Waitress) {
    throw "$Operation 前必须通过 ERP 主数据控制器 Stop 停止 Django ERP writer"
  }
  if (Resolve-OwnedProcess "django-bi-reader" $DjangoBiReaderPidPath $Waitress) {
    throw "$Operation 前必须通过 BI 控制器 Stop 停止 Django BI reader"
  }
  if (Resolve-OwnedProcess "erp-reference-sync" $ErpReferenceSyncPidPath $Python) {
    throw "$Operation 前必须通过 Stop 停止 ERP reference sync"
  }
  if (@(Get-ErpReferenceSyncCandidates).Count -gt 0) {
    throw "$Operation 发现未登记的 ERP reference sync 进程；拒绝修改或自动终止"
  }
}

function Test-MaintenanceKeepsPostgres([object]$Record) {
  if ($null -eq $Record -or $Record.PSObject.Properties.Name -cnotcontains "keepPostgres") { return $false }
  if ($Record.keepPostgres -isnot [bool]) { throw "Invalid maintenance PostgreSQL scope" }
  return $Record.keepPostgres
}

function Assert-ApplicationDeploymentStopped([string]$Operation) {
  if (Test-MaintenanceKeepsPostgres (Read-SystemMaintenance)) {
    Assert-ApplicationProcessesStopped $Operation
    Assert-PostgresListenerOwnership | Out-Null
    if (-not (Test-PostgresReady)) { throw "$Operation requires the owned PostgreSQL to remain ready" }
  } else { Assert-ServiceStackStopped $Operation }
}

function Assert-ServiceStackStopped([string]$Operation) {
  Assert-ApplicationProcessesStopped $Operation
  if (@(Get-PortListeners 5432).Count -gt 0) {
    throw "$Operation 前必须停止 PostgreSQL"
  }
  $pgCtl = Join-Path $PostgresBin "pg_ctl.exe"
  if (
    (Test-Path -LiteralPath $pgCtl -PathType Leaf) -and
    (Test-Path -LiteralPath $PostgresData -PathType Container)
  ) {
    $statusRun = Invoke-BoundedNativeProcess $pgCtl @(
      "status", "-D", $PostgresData
    ) $RuntimeRoot
    if ($statusRun.ExitCode -eq 0) { throw "$Operation 前必须停止 PostgreSQL" }
    if ($statusRun.ExitCode -ne 3) {
      throw "$Operation 无法验证 PostgreSQL 已停止（$(Get-NativeFailureSummary $statusRun)）"
    }
  }
}

function Get-PortListeners([int]$Port) {
  try {
    return @(
      Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { [int]$_.LocalPort -eq $Port }
    )
  } catch {
    throw "无法可靠枚举本机 TCP 监听器；拒绝继续服务操作"
  }
}

function Assert-PostgresListenerOwnership {
  $listeners = @(Get-PortListeners 5432)
  if ($listeners.Count -ne 1) { throw "PostgreSQL 5432 必须且只能有一个监听器" }
  $listener = $listeners[0]
  if ([string]$listener.LocalAddress -ne "127.0.0.1") { throw "PostgreSQL 未严格绑定 127.0.0.1" }
  $snapshot = Get-ProcessSnapshot ([int]$listener.OwningProcess) 3
  $expectedExecutable = Get-CanonicalPath (Join-Path $PostgresBin "postgres.exe")
  if ($null -eq $snapshot -or $snapshot.ExecutablePath -ine $expectedExecutable) {
    throw "5432 监听器不是本部署 PostgreSQL 可执行文件"
  }
  $dataMatches = [regex]::Matches($snapshot.CommandLine, '(?:^|\s)(?:"-D"|-D)\s+(?:"([^"]+)"|(\S+))')
  if ($dataMatches.Count -ne 1) { throw "PostgreSQL 命令行缺少唯一 -D 数据目录" }
  $rawData = if ($dataMatches[0].Groups[1].Success) { $dataMatches[0].Groups[1].Value } else { $dataMatches[0].Groups[2].Value }
  if ((Get-CanonicalPath ($rawData.Replace("/", "\"))) -ine (Get-CanonicalPath $PostgresData)) {
    throw "5432 PostgreSQL 使用了非本部署数据目录"
  }
  return $snapshot.Process
}

function Test-PostgresReady {
  $pgIsReady = Join-Path $PostgresBin "pg_isready.exe"
  if (-not (Test-Path -LiteralPath $pgIsReady -PathType Leaf)) { return $false }
  $readyRun = Invoke-BoundedNativeProcess $pgIsReady @(
    "-q", "-h", "127.0.0.1", "-p", "5432"
  ) $RuntimeRoot
  return $readyRun.ExitCode -eq 0
}

function Invoke-PgCtl([string[]]$Arguments, [string]$Operation, [int]$TimeoutSeconds = 40) {
  $pgCtl = Join-Path $PostgresBin "pg_ctl.exe"
  $launchArguments = @($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ })
  $stdoutPath = Join-Path $LogDirectory "pgctl.$Operation.$RunId.stdout.log"
  $stderrPath = Join-Path $LogDirectory "pgctl.$Operation.$RunId.stderr.log"
  $process = Start-Process -FilePath $pgCtl -ArgumentList $launchArguments `
    -WorkingDirectory $RuntimeRoot -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
  if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "PostgreSQL $Operation 控制命令超时"
  }
  # Flush redirected output without waiting for the detached database process.
  $process.WaitForExit()
  return [int]$process.ExitCode
}

function Start-Postgres {
  $pgCtl = Join-Path $PostgresBin "pg_ctl.exe"
  if (-not (Test-Path -LiteralPath $pgCtl -PathType Leaf)) { throw "缺少 PostgreSQL 17 运行文件" }
  $listeners = @(Get-PortListeners 5432)
  if ($listeners.Count -gt 0) {
    Assert-PostgresListenerOwnership | Out-Null
    if (-not (Test-PostgresReady)) { throw "本部署 PostgreSQL 正在监听但未接受连接" }
    return $false
  }
  $postgresLog = Join-Path $LogDirectory "postgres.$RunId.log"
  $exitCode = Invoke-PgCtl -Arguments @("start", "-D", $PostgresData, "-l", $postgresLog, "-w", "-t", "30") -Operation "start"
  if ($exitCode -ne 0) { throw "PostgreSQL 17 启动失败" }
  try {
    Assert-PostgresListenerOwnership | Out-Null
    if (-not (Test-PostgresReady)) { throw "PostgreSQL 启动后未通过就绪检查" }
  } catch {
    try {
      Invoke-PgCtl -Arguments @("stop", "-D", $PostgresData, "-m", "fast", "-w", "-t", "30") -Operation "rollback-stop" | Out-Null
    } catch {}
    throw
  }
  Write-LauncherEvent "INFO" "postgres_started"
  return $true
}

function Stop-Postgres {
  $pgCtl = Join-Path $PostgresBin "pg_ctl.exe"
  if (-not (Test-Path -LiteralPath $pgCtl -PathType Leaf)) { return }
  $listeners = @(Get-PortListeners 5432)
  if ($listeners.Count -gt 0) { Assert-PostgresListenerOwnership | Out-Null }
  $statusRun = Invoke-BoundedNativeProcess $pgCtl @(
    "status", "-D", $PostgresData
  ) $RuntimeRoot
  if ($statusRun.ExitCode -eq 3) { return }
  if ($statusRun.ExitCode -ne 0) {
    throw "无法验证 PostgreSQL 运行状态（$(Get-NativeFailureSummary $statusRun)）"
  }
  $exitCode = Invoke-PgCtl -Arguments @("stop", "-D", $PostgresData, "-m", "fast", "-w", "-t", "30") -Operation "stop"
  if ($exitCode -ne 0) { throw "PostgreSQL 停止失败" }
  Write-LauncherEvent "INFO" "postgres_stopped"
}

function Get-FinanceRoleProvisioningSecrets {
  $payload = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
  if ([int]$payload.version -notin @(2, 3)) {
    throw "配置财务数据库角色需要版本 2 或 3 的受控凭据库"
  }
  $superuser = Unprotect-Value ([string]$payload.postgresSuperuser) "postgresSuperuser"
  if ([int]$payload.version -eq 3) {
    $financeReader = Unprotect-Value ([string]$payload.databaseFinanceReader) "databaseFinanceReader"
    $financeWriter = Unprotect-Value ([string]$payload.databaseFinanceWriter) "databaseFinanceWriter"
  } else {
    $financeReader = New-RandomSecret
    $financeWriter = New-RandomSecret
    $updated = [ordered]@{}
    foreach ($property in $payload.PSObject.Properties) {
      $updated[$property.Name] = $property.Value
    }
    $updated.version = 3
    $updated.databaseFinanceReader = Protect-Value $financeReader
    $updated.databaseFinanceWriter = Protect-Value $financeWriter
    Write-AtomicJson $CredentialPath $updated
    Write-LauncherEvent "INFO" "credential_vault_upgraded" "version=3"
  }
  Assert-StrongSecret $financeReader "databaseFinanceReader"
  Assert-StrongSecret $financeWriter "databaseFinanceWriter"
  return [pscustomobject]@{
    SuperuserPassword = $superuser
    FinanceReaderPassword = $financeReader
    FinanceWriterPassword = $financeWriter
  }
}

function Provision-FinanceDatabaseRoles {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "ProvisionFinanceRoles 必须从受保护的 runtime app 启动脚本执行；请先运行 DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Assert-ApplicationProcessesStopped "ProvisionFinanceRoles"
  Get-ServiceConfig | Out-Null
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  $provisioning = Get-FinanceRoleProvisioningSecrets
  $postgresStarted = $false
  $previousUrl = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", "Process")
  $previousReader = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_FINANCE_READER_PASSWORD", "Process")
  $previousWriter = [Environment]::GetEnvironmentVariable("TERUISI_PROVISION_FINANCE_WRITER_PASSWORD", "Process")
  try {
    $postgresStarted = Start-Postgres
    $env:TERUISI_PROVISION_DATABASE_URL = Database-Url "postgres" $provisioning.SuperuserPassword "teruisi_finance_role_provision" $ReaderStatementTimeoutMs
    $env:TERUISI_PROVISION_FINANCE_READER_PASSWORD = $provisioning.FinanceReaderPassword
    $env:TERUISI_PROVISION_FINANCE_WRITER_PASSWORD = $provisioning.FinanceWriterPassword
    $code = @'
import os

import psycopg
from psycopg import sql

roles = {
    "teruisi_finance_reader": os.environ["TERUISI_PROVISION_FINANCE_READER_PASSWORD"],
    "teruisi_finance_writer": os.environ["TERUISI_PROVISION_FINANCE_WRITER_PASSWORD"],
}
connection = psycopg.connect(os.environ["TERUISI_PROVISION_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
    for role, password in roles.items():
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOINHERIT NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role))
            )
        cursor.execute(
            sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role))
        )
        cursor.execute(
            "SELECT parent.rolname FROM pg_auth_members membership "
            "JOIN pg_roles parent ON parent.oid = membership.roleid "
            "JOIN pg_roles member ON member.oid = membership.member "
            "WHERE member.rolname = %s",
            (role,),
        )
        for parent_role in (row[0] for row in cursor.fetchall()):
            cursor.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(parent_role), sql.Identifier(role)
                )
            )
        cursor.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    cursor.execute(
        "ALTER ROLE teruisi_finance_reader SET default_transaction_read_only = on"
    )
    cursor.execute(
        "ALTER ROLE teruisi_finance_writer RESET default_transaction_read_only"
    )
    cursor.execute(
        "GRANT CONNECT ON DATABASE teruisi_sales TO "
        "teruisi_finance_reader, teruisi_finance_writer"
    )
connection.close()
'@
    $logPath = Join-Path $LogDirectory "finance-role-provision.$RunId.log"
    $launcher = ConvertTo-PythonBase64Launcher $code "finance_role_provision.py"
    $nativeRun = Invoke-BoundedNativeProcess $Python @("-c", $launcher) $BackendRoot
    Write-NativeDiagnosticLog $logPath "finance_role_provision" $nativeRun
    if ($nativeRun.ExitCode -ne 0) {
      throw "独立财务 reader/writer 数据库角色配置失败（$(Get-NativeFailureSummary $nativeRun)）"
    }
    Write-LauncherEvent "INFO" "finance_database_roles_provisioned"
    Write-Output "独立财务 reader/writer 数据库角色与 DPAPI 凭据已配置。"
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_DATABASE_URL", $previousUrl, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_FINANCE_READER_PASSWORD", $previousReader, "Process")
    [Environment]::SetEnvironmentVariable("TERUISI_PROVISION_FINANCE_WRITER_PASSWORD", $previousWriter, "Process")
    $provisioning = $null
    if ($postgresStarted) {
      try { Stop-Postgres } catch {
        Write-LauncherEvent "ERROR" "provision_cleanup_failed" $_.Exception.Message
      }
    }
  }
}

function Invoke-WithDjangoEnvironment(
  [object]$Secrets,
  [string]$DatabaseUrl,
  [string]$ProcessRole,
  [bool]$ExpectReadOnly,
  [int]$BodyBytes,
  [string]$AuthorityEpoch,
  [string]$CutoverId,
  [scriptblock]$Operation,
  [string]$WorkflowOperationsAuthorityEpoch = "",
  [string]$WorkflowOperationsCutoverId = ""
) {
  $names = @(
    "TERUISI_DJANGO_DATABASE_URL", "TERUISI_DJANGO_INTERNAL_SECRET",
    "DJANGO_SECRET_KEY", "DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS",
    "TERUISI_DJANGO_ENVIRONMENT", "TERUISI_DJANGO_PROCESS_ROLE",
    "TERUISI_DJANGO_EXPECT_READ_ONLY", "TERUISI_DJANGO_SALES_CACHE_SECONDS",
    "TERUISI_DJANGO_ERP_SYNC_MAX_AGE_SECONDS",
    "TERUISI_DJANGO_LOG_LEVEL", "TERUISI_DJANGO_SIGNATURE_MAX_AGE_SECONDS",
    "TERUISI_DJANGO_DB_CONN_MAX_AGE", "TERUISI_DJANGO_SALES_AUTHORITY_EPOCH",
    "TERUISI_DJANGO_SALES_CUTOVER_ID",
    "TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH", "TERUISI_DJANGO_FINANCE_CUTOVER_ID",
    "TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH", "TERUISI_DJANGO_NETSHOP_CUTOVER_ID",
    "TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH", "TERUISI_DJANGO_MARKET_CUTOVER_ID",
    "TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH", "TERUISI_DJANGO_PRODUCTS_CUTOVER_ID",
    "TERUISI_DJANGO_INVENTORY_AUTHORITY_EPOCH", "TERUISI_DJANGO_INVENTORY_CUTOVER_ID",
    "TERUISI_DJANGO_WORKFLOW_AUTHORITY_EPOCH", "TERUISI_DJANGO_WORKFLOW_CUTOVER_ID",
    "TERUISI_DJANGO_WORKFLOW_OPERATIONS_AUTHORITY_EPOCH",
    "TERUISI_DJANGO_WORKFLOW_OPERATIONS_CUTOVER_ID",
    "TERUISI_DJANGO_CUSTOMER_SERVICE_AUTHORITY_EPOCH",
    "TERUISI_DJANGO_CUSTOMER_SERVICE_CUTOVER_ID",
    "TERUISI_DJANGO_ACCESS_CONTROL_AUTHORITY_EPOCH",
    "TERUISI_DJANGO_ACCESS_CONTROL_CUTOVER_ID",
    "TERUISI_DJANGO_AI_AUTHORITY_EPOCH", "TERUISI_DJANGO_AI_CUTOVER_ID",
    "AI_SECRET_ENCRYPTION_KEY", "AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST", "AI_ALLOW_LOCAL_MODEL_ENDPOINTS", "TERUISI_DJANGO_AI_EDGE_BASE_URL",
    "TERUISI_PANDAS_RUNNER_KEY_FILE", "TERUISI_PANDAS_RUNNER_IMAGE",
    "TERUISI_DJANGO_ERP_AUTHORITY_EPOCH", "TERUISI_DJANGO_ERP_CUTOVER_ID",
    "TERUISI_DJANGO_MAX_HEADER_BYTES", "TERUISI_DJANGO_MAX_BODY_BYTES",
    "DJANGO_SETTINGS_MODULE", "PYTHONUTF8", "PYTHONPATH", "PYTHONHOME"
  )
  $previous = @{}
  foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
  try {
    $env:TERUISI_DJANGO_DATABASE_URL = $DatabaseUrl
    $env:TERUISI_DJANGO_INTERNAL_SECRET = $Secrets.InternalSecret
    $env:DJANGO_SECRET_KEY = $Secrets.DjangoSecretKey
    $env:DJANGO_DEBUG = "false"
    $env:DJANGO_ALLOWED_HOSTS = "127.0.0.1,localhost"
    $env:TERUISI_DJANGO_ENVIRONMENT = "production"
    $env:TERUISI_DJANGO_PROCESS_ROLE = $ProcessRole
    $env:TERUISI_DJANGO_EXPECT_READ_ONLY = if ($ExpectReadOnly) { "true" } else { "false" }
    $env:TERUISI_DJANGO_SALES_CACHE_SECONDS = if ($ProcessRole -eq "reader") { "300" } else { "0" }
    $env:TERUISI_DJANGO_ERP_SYNC_MAX_AGE_SECONDS = "60"
    $env:TERUISI_DJANGO_INVENTORY_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_INVENTORY_CUTOVER_ID = ""
    $env:TERUISI_DJANGO_WORKFLOW_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_WORKFLOW_CUTOVER_ID = ""
    $env:TERUISI_DJANGO_WORKFLOW_OPERATIONS_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_WORKFLOW_OPERATIONS_CUTOVER_ID = ""
    $env:TERUISI_DJANGO_CUSTOMER_SERVICE_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_CUSTOMER_SERVICE_CUTOVER_ID = ""
    $env:TERUISI_DJANGO_ACCESS_CONTROL_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_ACCESS_CONTROL_CUTOVER_ID = ""
    $env:TERUISI_DJANGO_AI_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_AI_CUTOVER_ID = ""
    $env:AI_SECRET_ENCRYPTION_KEY = ""
    $env:AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST = ""
    $env:AI_ALLOW_LOCAL_MODEL_ENDPOINTS = ""
    $env:TERUISI_DJANGO_AI_EDGE_BASE_URL = ""
    $env:TERUISI_PANDAS_RUNNER_KEY_FILE = ""
    $env:TERUISI_PANDAS_RUNNER_IMAGE = ""
    $env:TERUISI_DJANGO_ERP_AUTHORITY_EPOCH = ""
    $env:TERUISI_DJANGO_ERP_CUTOVER_ID = ""
    if ($ProcessRole -eq "workflow_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_WORKFLOW_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_WORKFLOW_CUTOVER_ID = $CutoverId
      $env:TERUISI_DJANGO_WORKFLOW_OPERATIONS_AUTHORITY_EPOCH = $WorkflowOperationsAuthorityEpoch
      $env:TERUISI_DJANGO_WORKFLOW_OPERATIONS_CUTOVER_ID = $WorkflowOperationsCutoverId
    } elseif ($ProcessRole -eq "inventory_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_INVENTORY_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_INVENTORY_CUTOVER_ID = $CutoverId
    } elseif ($ProcessRole -eq "finance_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = $CutoverId
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
    } elseif ($ProcessRole -eq "netshop_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = $CutoverId
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
    } elseif ($ProcessRole -eq "market_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = $CutoverId
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
    } elseif ($ProcessRole -eq "products_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = $CutoverId
    } elseif ($ProcessRole -eq "erp_reference_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_ERP_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_ERP_CUTOVER_ID = $CutoverId
    } elseif ($ProcessRole -eq "customer_service_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_CUSTOMER_SERVICE_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_CUSTOMER_SERVICE_CUTOVER_ID = $CutoverId
    } elseif ($ProcessRole -in @("ai_reader", "ai_writer")) {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_AI_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_AI_CUTOVER_ID = $CutoverId
    } elseif ($ProcessRole -eq "access_control_writer") {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_ACCESS_CONTROL_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_ACCESS_CONTROL_CUTOVER_ID = $CutoverId
    } else {
      $env:TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = $AuthorityEpoch
      $env:TERUISI_DJANGO_SALES_CUTOVER_ID = $CutoverId
      $env:TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_FINANCE_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_NETSHOP_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_MARKET_CUTOVER_ID = ""
      $env:TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH = ""
      $env:TERUISI_DJANGO_PRODUCTS_CUTOVER_ID = ""
    }
    $env:TERUISI_DJANGO_LOG_LEVEL = "INFO"
    $env:TERUISI_DJANGO_SIGNATURE_MAX_AGE_SECONDS = "60"
    $env:TERUISI_DJANGO_DB_CONN_MAX_AGE = "60"
    $env:TERUISI_DJANGO_MAX_HEADER_BYTES = [string]$MaxHeaderBytes
    $env:TERUISI_DJANGO_MAX_BODY_BYTES = [string]$BodyBytes
    $env:DJANGO_SETTINGS_MODULE = "teruisi_backend.settings"
    $env:PYTHONUTF8 = "1"
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("PYTHONHOME", $null, "Process")
    & $Operation
  } finally {
    foreach ($name in $names) {
      [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
    }
    $DatabaseUrl = $null
  }
}

function Assert-PostgresConnectionCapacity([object]$Secrets) {
  $requiredConnections = if (Test-Path -LiteralPath $AiStartupEnabledPath -PathType Leaf) { 128 } else { $MinimumPostgresConnectionsForFullStack }
  $ownerUrl = Database-Url `
    "teruisi_sales_owner" $Secrets.OwnerPassword `
    "teruisi_connection_capacity_probe" $ReaderStatementTimeoutMs
  $code = @'
import json
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SHOW max_connections")
    max_connections = int(cursor.fetchone()[0])
print(json.dumps({"maxConnections": max_connections}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "connection_capacity_probe.py"
  try {
    $payload = Invoke-WithDjangoEnvironment `
      $Secrets $ownerUrl "migration_writer" $false $ReaderMaxBodyBytes "" "" {
        $nativeRun = Invoke-BoundedNativeProcess `
          $Python @((Join-Path $BackendRoot "manage.py"), "shell", "-c", $launcher) $BackendRoot
        return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 全栈连接容量"
      }
  } finally {
    $ownerUrl = $null
  }
  if (-not (Test-ExactObjectPropertyNames $payload @("maxConnections")) -or
      [int]$payload.maxConnections -lt $requiredConnections) {
    throw "PostgreSQL max_connections 低于完整运行栈所需的 $requiredConnections；拒绝启动服务"
  }
  return [int]$payload.maxConnections
}

function Invoke-DjangoMigrations(
  [object]$Secrets,
  [string]$DatabaseName = "teruisi_sales"
) {
  if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "缺少 Python 运行文件" }
  Assert-NoUnapprovedProtectedAiMigration "Django migrate"
  $ownerUrl = Database-Url "teruisi_sales_owner" $Secrets.OwnerPassword "teruisi_django_migrate" $WriterStatementTimeoutMs $DatabaseName
  $logPath = Join-Path $LogDirectory "django-migrate.$DatabaseName.$RunId.log"
  $manage = Join-Path $BackendRoot "manage.py"
  Invoke-WithDjangoEnvironment $Secrets $ownerUrl "migration_writer" $false $WriterMaxBodyBytes "" "" {
    $migrationRun = Invoke-BoundedNativeProcess $Python @($manage, "migrate", "--noinput") $BackendRoot
    Write-NativeDiagnosticLog $logPath "django_migrate" $migrationRun
    if ($migrationRun.ExitCode -ne 0) {
      throw "Django 数据库迁移失败（$(Get-NativeFailureSummary $migrationRun)）"
    }

    $grantCode = @'
from django.db import connection

roles = (
    "teruisi_sales_reader",
    "teruisi_sales_writer",
    "teruisi_finance_reader",
    "teruisi_finance_writer",
)
quote = connection.ops.quote_name
with connection.cursor() as c:
    c.execute(
        "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
        "rolbypassrls FROM pg_roles WHERE rolname = ANY(%s)",
        [list(roles)],
    )
    role_flags = {row[0]: row[1:] for row in c.fetchall()}
    if set(role_flags) != set(roles) or any(any(flags) for flags in role_flags.values()):
        raise RuntimeError("Django runtime roles are missing or have privileged PostgreSQL attributes")
    c.execute("SELECT current_database()")
    current_database = quote(str(c.fetchone()[0]))
    c.execute(
        f"GRANT CONNECT ON DATABASE {current_database} TO "
        "teruisi_sales_reader, teruisi_sales_writer, "
        "teruisi_finance_reader, teruisi_finance_writer"
    )
    c.execute(
        "SELECT member.rolname, parent.rolname FROM pg_auth_members membership "
        "JOIN pg_roles parent ON parent.oid = membership.roleid "
        "JOIN pg_roles member ON member.oid = membership.member "
        "WHERE member.rolname = ANY(%s)",
        [list(roles)],
    )
    if c.fetchone() is not None:
        raise RuntimeError("Django runtime roles must not inherit or SET ROLE into another role")
    for role in roles:
        c.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {quote(role)}")
        c.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {quote(role)}")
        c.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {quote(role)}")
        c.execute(
            "SELECT table_schema, table_name, column_name "
            "FROM information_schema.column_privileges "
            "WHERE grantee = %s AND table_schema = 'public' "
            "ORDER BY table_schema, table_name, column_name",
            [role],
        )
        grouped = {}
        for schema, table, column in c.fetchall():
            grouped.setdefault((schema, table), []).append(column)
        for (schema, table), columns in grouped.items():
            names = ", ".join(quote(column) for column in columns)
            c.execute(
                f"REVOKE ALL PRIVILEGES ({names}) ON TABLE "
                f"{quote(schema)}.{quote(table)} FROM {quote(role)}"
            )

    c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE teruisi_sales_owner IN SCHEMA public REVOKE ALL ON TABLES FROM teruisi_sales_reader, teruisi_sales_writer, teruisi_finance_reader, teruisi_finance_writer")
    c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE teruisi_sales_owner IN SCHEMA public REVOKE ALL ON SEQUENCES FROM teruisi_sales_reader, teruisi_sales_writer, teruisi_finance_reader, teruisi_finance_writer")
    c.execute("GRANT USAGE ON SCHEMA public TO teruisi_sales_reader, teruisi_sales_writer, teruisi_finance_reader, teruisi_finance_writer")

    c.execute("GRANT SELECT ON sales_order_lines, sales_import_batches, sales_data_revisions, erp_product_master, erp_combo_items, erp_reference_import_batches_pg, erp_reference_import_scope_heads, erp_reference_write_authority TO teruisi_sales_reader")
    from system_datasets.permissions import grant_columns
    grant_columns(c, "sales")
    grant_columns(c, "finance")
    from sales.analysis_options_permissions import provision as grant_sales_options
    grant_sales_options(c)

    c.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sales_order_lines TO teruisi_sales_writer")
    c.execute("GRANT SELECT, INSERT, UPDATE ON sales_import_batches, sales_data_revisions, sales_import_scope_heads, sales_import_attempts, sales_raw_upload_sessions, sales_staged_import_sessions, sales_write_request_receipts TO teruisi_sales_writer")
    c.execute("GRANT SELECT, INSERT ON sales_import_fingerprints TO teruisi_sales_writer")
    c.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sales_raw_upload_chunks, sales_staged_import_chunks TO teruisi_sales_writer")
    c.execute("GRANT SELECT ON sales_write_authority, sales_cutover_attestations, erp_product_master, erp_combo_items, erp_reference_import_batches_pg, erp_reference_import_scope_heads, erp_reference_write_authority TO teruisi_sales_writer")
    for table in (
        "sales_order_lines",
        "sales_import_fingerprints",
        "sales_raw_upload_chunks",
        "sales_staged_import_chunks",
    ):
        c.execute("SELECT pg_get_serial_sequence(%s, 'id')", [f"public.{table}"])
        sequence = c.fetchone()[0]
        if sequence:
            schema, name = sequence.split(".", 1)
            c.execute(
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCE "
                f"{quote(schema)}.{quote(name)} TO teruisi_sales_writer"
            )

    c.execute(
        "GRANT SELECT ON finance_import_batches, finance_months, finance_lines, "
        "finance_targets_scoped, finance_data_revisions TO teruisi_finance_reader"
    )
    from finance.business_source_permissions import grant_actor_read as grant_finance_source_actor
    grant_finance_source_actor(c)

    c.execute("GRANT SELECT, INSERT, UPDATE ON finance_import_batches, finance_months, finance_import_scope_heads, finance_import_attempts, finance_data_revisions, finance_write_request_receipts TO teruisi_finance_writer")
    c.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON finance_lines, finance_targets_scoped TO teruisi_finance_writer")
    c.execute("GRANT SELECT, INSERT ON finance_target_deletion_audits, finance_import_fingerprints TO teruisi_finance_writer")
    c.execute("GRANT SELECT ON finance_write_authority TO teruisi_finance_writer")
    for table in ("finance_lines", "finance_import_fingerprints"):
        c.execute("SELECT pg_get_serial_sequence(%s, 'id')", [f"public.{table}"])
        sequence = c.fetchone()[0]
        if sequence:
            schema, name = sequence.split(".", 1)
            c.execute(
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCE "
                f"{quote(schema)}.{quote(name)} TO teruisi_finance_writer"
            )

    c.execute("ALTER TABLE sales_data_revisions ENABLE ROW LEVEL SECURITY")
    c.execute("DROP POLICY IF EXISTS sales_revision_reader ON sales_data_revisions")
    c.execute("DROP POLICY IF EXISTS sales_revision_writer ON sales_data_revisions")
    c.execute("DROP POLICY IF EXISTS sales_revision_writer_read ON sales_data_revisions")
    c.execute("DROP POLICY IF EXISTS sales_revision_writer_insert ON sales_data_revisions")
    c.execute("DROP POLICY IF EXISTS sales_revision_writer_update ON sales_data_revisions")
    c.execute("DROP POLICY IF EXISTS erp_revision_writer ON sales_data_revisions")
    c.execute("CREATE POLICY sales_revision_reader ON sales_data_revisions FOR SELECT TO teruisi_sales_reader USING (domain IN ('sales', 'erp'))")
    c.execute("CREATE POLICY sales_revision_writer_read ON sales_data_revisions FOR SELECT TO teruisi_sales_writer USING (domain IN ('sales', 'erp'))")
    c.execute("CREATE POLICY sales_revision_writer_insert ON sales_data_revisions FOR INSERT TO teruisi_sales_writer WITH CHECK (domain = 'sales')")
    c.execute("CREATE POLICY sales_revision_writer_update ON sales_data_revisions FOR UPDATE TO teruisi_sales_writer USING (domain = 'sales') WITH CHECK (domain = 'sales')")
    c.execute("SELECT 1 FROM pg_roles WHERE rolname='teruisi_erp_reference_sync'")
    if c.fetchone() is not None:
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE teruisi_sales_owner IN SCHEMA public REVOKE ALL ON TABLES FROM teruisi_erp_reference_sync")
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE teruisi_sales_owner IN SCHEMA public REVOKE ALL ON SEQUENCES FROM teruisi_erp_reference_sync")
        c.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM teruisi_erp_reference_sync")
        c.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM teruisi_erp_reference_sync")
        c.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM teruisi_erp_reference_sync")
        c.execute(
            "SELECT table_schema,table_name,column_name FROM information_schema.column_privileges "
            "WHERE grantee='teruisi_erp_reference_sync' AND table_schema='public'"
        )
        grouped = {}
        for schema, table, column in c.fetchall():
            grouped.setdefault((schema, table), []).append(column)
        for (schema, table), columns in grouped.items():
            names = ", ".join(quote(column) for column in columns)
            c.execute(
                f"REVOKE ALL PRIVILEGES ({names}) ON TABLE "
                f"{quote(schema)}.{quote(table)} FROM teruisi_erp_reference_sync"
            )
        c.execute("REVOKE CONNECT ON DATABASE " + current_database + " FROM teruisi_erp_reference_sync")

    for table in (
        "sales_write_authority",
        "erp_product_master",
        "erp_combo_items",
        "erp_reference_import_batches_pg",
        "erp_reference_import_scope_heads",
        "erp_reference_write_authority",
        "sales_cutover_attestations",
        "sales_legacy_upload_audits",
    ):
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            c.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                ["teruisi_sales_writer", table, privilege],
            )
            if c.fetchone()[0]:
                raise RuntimeError(
                    f"sales writer unexpectedly has {privilege} on {table}"
                )
            if privilege in ("INSERT", "UPDATE"):
                c.execute(
                    "SELECT has_any_column_privilege(%s, %s, %s)",
                    ["teruisi_sales_writer", table, privilege],
                )
                if c.fetchone()[0]:
                    raise RuntimeError(
                        f"sales writer unexpectedly has column {privilege} on {table}"
                    )
    c.execute(
        "SELECT policyname, cmd FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = 'sales_data_revisions' "
        "AND 'teruisi_sales_writer' = ANY(roles) ORDER BY policyname"
    )
    if c.fetchall() != [
        ("sales_revision_writer_insert", "INSERT"),
        ("sales_revision_writer_read", "SELECT"),
        ("sales_revision_writer_update", "UPDATE"),
    ]:
        raise RuntimeError("sales writer revision RLS policies are not least-privilege")
'@
    $grantLauncher = ConvertTo-PythonBase64Launcher $grantCode "django_runtime_grants.py"
    $grantRun = Invoke-BoundedNativeProcess $Python @(
      $manage, "shell", "-c", $grantLauncher
    ) $BackendRoot
    Write-NativeDiagnosticLog $logPath "django_runtime_grants" $grantRun
    if ($grantRun.ExitCode -ne 0) {
      throw "Django 销售/财务 reader、writer 与退役 ERP bridge 权限回收失败（$(Get-NativeFailureSummary $grantRun)）"
    }
  }
  $ownerUrl = $null
  Write-LauncherEvent "INFO" "django_migrations_applied"
}

function Get-ActiveWriteAuthority([object]$Secrets) {
  $writerUrl = Database-Url "teruisi_sales_writer" $Secrets.WriterPassword "teruisi_authority_probe" $ReaderStatementTimeoutMs
  $manage = Join-Path $BackendRoot "manage.py"
  $payload = Invoke-WithDjangoEnvironment $Secrets $writerUrl "migration_writer" $false $ReaderMaxBodyBytes "" "" {
    $nativeRun = Invoke-BoundedNativeProcess $Python @(
      $manage, "sales_write_authority", "status"
    ) $BackendRoot
    return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 销售写入权威"
  }
  $writerUrl = $null
  if (
    [string]$payload.status -cne "active" -or
    -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
    -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$")
  ) {
    throw "PostgreSQL 尚未成为销售唯一写入源；拒绝启动 writer"
  }
  return $payload
}

function Get-FinanceWriteAuthority([object]$Secrets) {
  $writerUrl = Database-Url "teruisi_finance_writer" $Secrets.FinanceWriterPassword "teruisi_finance_authority_probe" $ReaderStatementTimeoutMs
  $manage = Join-Path $BackendRoot "manage.py"
  $code = @'
import json
from finance.models import FinanceWriteAuthority

authority = FinanceWriteAuthority.objects.filter(id=1).first()
print(json.dumps({
    "status": authority.status if authority else "missing",
    "authorityEpoch": str(authority.authority_epoch) if authority and authority.authority_epoch else "",
    "cutoverId": authority.cutover_id if authority else "",
}, separators=(",", ":")))
'@
  $launcher = ConvertTo-PythonBase64Launcher $code "finance_authority_probe.py"
  $payload = Invoke-WithDjangoEnvironment $Secrets $writerUrl "migration_writer" $false $ReaderMaxBodyBytes "" "" {
    $nativeRun = Invoke-BoundedNativeProcess $Python @($manage, "shell", "-c", $launcher) $BackendRoot
    return ConvertFrom-UniqueNativeJson $nativeRun "读取 PostgreSQL 财务写入权威"
  }
  $writerUrl = $null
  if ([string]$payload.status -cnotin @("d1", "postgres")) {
    throw "PostgreSQL 财务写入权威状态无效"
  }
  if ([string]$payload.status -ceq "postgres" -and (
      -not ([string]$payload.authorityEpoch -match "^[0-9a-fA-F-]{36}$") -or
      -not ([string]$payload.cutoverId -match "^[A-Za-z0-9._:-]{8,128}$")
    )) {
    throw "PostgreSQL 财务写入权威身份无效"
  }
  return $payload
}

function Wait-DjangoReady([string]$Label, [string]$HealthUrl, [string]$HostHeader, [int]$Seconds = 30) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  $lastStatus = "connection_failed"
  do {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2 -Headers @{ Host = $HostHeader }
      $lastStatus = [string]$response.StatusCode
      if ($response.StatusCode -eq 200) { return }
    } catch {
      if ($_.Exception.Response) { $lastStatus = [string][int]$_.Exception.Response.StatusCode }
      Start-Sleep -Milliseconds 300
    }
  } while ((Get-Date) -lt $deadline)
  throw "Django $Label 未在 ${Seconds} 秒内就绪（lastStatus=$lastStatus）"
}

function Start-DjangoReader([object]$Secrets, [switch]$DeferReady) {
  if (-not (Test-Path -LiteralPath $Waitress -PathType Leaf)) { throw "缺少 Waitress 运行文件" }
  $arguments = @(
    "--listen=127.0.0.1:8001", "--threads=8", "--connection-limit=100",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$ReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-reader" $Waitress $arguments
  $existing = Resolve-OwnedProcess "django-reader" $DjangoReaderPidPath $Waitress $arguments $fingerprint
  if ($existing) {
    Wait-DjangoReady "reader" $DjangoReaderHealthUrl "127.0.0.1:8001"
    return $false
  }
  if (@(Get-PortListeners 8001).Count -gt 0) { throw "端口 8001 被非本部署的服务占用" }
  Remove-OldServiceLogs "django-reader"
  $stdout = Join-Path $LogDirectory "django-reader.$RunId.stdout.log"
  $stderr = Join-Path $LogDirectory "django-reader.$RunId.stderr.log"
  $readerUrl = Database-Url "teruisi_sales_reader" $Secrets.ReaderPassword "teruisi_django_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment $Secrets $readerUrl "reader" $true $ReaderMaxBodyBytes "" "" {
    Start-ManagedProcess "django-reader" $Waitress $arguments $BackendRoot $DjangoReaderPidPath $fingerprint $stdout $stderr | Out-Null
  }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "reader" $DjangoReaderHealthUrl "127.0.0.1:8001"
    return $true
  } catch {
    Stop-OwnedProcess "django-reader" $DjangoReaderPidPath $Waitress
    throw
  }
}

function Start-DjangoWriter([object]$Secrets, [object]$Authority) {
  if (-not (Test-Path -LiteralPath $Waitress -PathType Leaf)) { throw "缺少 Waitress 运行文件" }
  $arguments = @(
    "--listen=127.0.0.1:8002", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$WriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-writer" $Waitress $arguments
  $existing = Resolve-OwnedProcess "django-writer" $DjangoWriterPidPath $Waitress $arguments $fingerprint
  if ($existing) {
    Wait-DjangoReady "writer" $DjangoWriterHealthUrl "127.0.0.1:8002"
    return $false
  }
  if (@(Get-PortListeners 8002).Count -gt 0) { throw "端口 8002 被非本部署的服务占用" }
  Remove-OldServiceLogs "django-writer"
  $stdout = Join-Path $LogDirectory "django-writer.$RunId.stdout.log"
  $stderr = Join-Path $LogDirectory "django-writer.$RunId.stderr.log"
  $writerUrl = Database-Url "teruisi_sales_writer" $Secrets.WriterPassword "teruisi_django_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment $Secrets $writerUrl "sales_writer" $false $WriterMaxBodyBytes ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
    Start-ManagedProcess "django-writer" $Waitress $arguments $BackendRoot $DjangoWriterPidPath $fingerprint $stdout $stderr | Out-Null
  }
  $writerUrl = $null
  try {
    Wait-DjangoReady "writer" $DjangoWriterHealthUrl "127.0.0.1:8002"
    return $true
  } catch {
    Stop-OwnedProcess "django-writer" $DjangoWriterPidPath $Waitress
    throw
  }
}

function Start-DjangoFinanceReader([object]$Secrets, [switch]$DeferReady) {
  if (-not (Test-Path -LiteralPath $Waitress -PathType Leaf)) { throw "缺少 Waitress 运行文件" }
  $arguments = @(
    "--listen=127.0.0.1:8011", "--threads=6", "--connection-limit=60",
    "--channel-timeout=35", "--cleanup-interval=30", "--ident=teruisi-django-finance-reader",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$ReaderMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-finance-reader" $Waitress $arguments
  $existing = Resolve-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress $arguments $fingerprint
  if ($existing) {
    Wait-DjangoReady "finance-reader" $DjangoFinanceReaderHealthUrl "127.0.0.1:8011"
    return $false
  }
  if (@(Get-PortListeners 8011).Count -gt 0) { throw "端口 8011 被非本部署的服务占用" }
  Remove-OldServiceLogs "django-finance-reader"
  $stdout = Join-Path $LogDirectory "django-finance-reader.$RunId.stdout.log"
  $stderr = Join-Path $LogDirectory "django-finance-reader.$RunId.stderr.log"
  $readerUrl = Database-Url "teruisi_finance_reader" $Secrets.FinanceReaderPassword "teruisi_django_finance_read" $ReaderStatementTimeoutMs
  Invoke-WithDjangoEnvironment $Secrets $readerUrl "finance_reader" $true $ReaderMaxBodyBytes "" "" {
    Start-ManagedProcess "django-finance-reader" $Waitress $arguments $BackendRoot $DjangoFinanceReaderPidPath $fingerprint $stdout $stderr | Out-Null
  }
  $readerUrl = $null
  # The stack owns rollback after launch; its final readiness barrier remains mandatory.
  if ($DeferReady) { return $true }
  try {
    Wait-DjangoReady "finance-reader" $DjangoFinanceReaderHealthUrl "127.0.0.1:8011"
    return $true
  } catch {
    Stop-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress
    throw
  }
}

function Start-DjangoFinanceWriter([object]$Secrets, [object]$Authority) {
  if ([string]$Authority.status -cne "postgres") {
    throw "PostgreSQL 尚未成为财务唯一写入源；拒绝启动 finance writer"
  }
  if (-not (Test-Path -LiteralPath $Waitress -PathType Leaf)) { throw "缺少 Waitress 运行文件" }
  $arguments = @(
    "--listen=127.0.0.1:8012", "--threads=4", "--connection-limit=20",
    "--channel-timeout=960", "--cleanup-interval=30", "--ident=teruisi-django-finance-writer",
    "--max-request-header-size=$MaxHeaderBytes", "--max-request-body-size=$WriterMaxBodyBytes",
    "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
  )
  $fingerprint = Get-ConfigFingerprint "django-finance-writer" $Waitress $arguments
  $existing = Resolve-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress $arguments $fingerprint
  if ($existing) {
    Wait-DjangoReady "finance-writer" $DjangoFinanceWriterHealthUrl "127.0.0.1:8012"
    return $false
  }
  if (@(Get-PortListeners 8012).Count -gt 0) { throw "端口 8012 被非本部署的服务占用" }
  Remove-OldServiceLogs "django-finance-writer"
  $stdout = Join-Path $LogDirectory "django-finance-writer.$RunId.stdout.log"
  $stderr = Join-Path $LogDirectory "django-finance-writer.$RunId.stderr.log"
  $writerUrl = Database-Url "teruisi_finance_writer" $Secrets.FinanceWriterPassword "teruisi_django_finance_write" $WriterStatementTimeoutMs
  Invoke-WithDjangoEnvironment $Secrets $writerUrl "finance_writer" $false $WriterMaxBodyBytes ([string]$Authority.authorityEpoch) ([string]$Authority.cutoverId) {
    Start-ManagedProcess "django-finance-writer" $Waitress $arguments $BackendRoot $DjangoFinanceWriterPidPath $fingerprint $stdout $stderr | Out-Null
  }
  $writerUrl = $null
  try {
    Wait-DjangoReady "finance-writer" $DjangoFinanceWriterHealthUrl "127.0.0.1:8012"
    return $true
  } catch {
    Stop-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress
    throw
  }
}

function Configure-Service {
  Assert-ServiceStackStopped "Configure"
  $resolvedErpSource = if ([string]::IsNullOrWhiteSpace($ErpSourceD1)) { "" } else { Resolve-ErpSourceD1 $ErpSourceD1 }
  New-Item -ItemType Directory -Path $RuntimeRoot, $LogDirectory, $RunDirectory -Force | Out-Null
  Write-AtomicJson $ConfigPath ([ordered]@{
    version = 6
    backend = "django-postgresql"
    configuredAt = [DateTimeOffset]::Now.ToString("o")
    configuredFrom = $ExecutionRoot
    readerAddress = "127.0.0.1:8001"
    writerAddress = "127.0.0.1:8002"
    financeReaderAddress = "127.0.0.1:8011"
    financeWriterAddress = "127.0.0.1:8012"
    customerServiceReaderAddress = "127.0.0.1:8071"
    customerServiceWriterAddress = "127.0.0.1:8072"
    postgresAddress = "127.0.0.1:5432"
    erpSourceD1 = $resolvedErpSource
  })
  Write-LauncherEvent "INFO" "service_configured"
  Write-Output "Django 本机业务域 reader/writer 与 BI reader 配置已固定；ERP D1 路径仅供受控迁移和退役使用，未启动服务。"
}

function Assert-Hex64([string]$Value, [string]$Label) {
  if ($Value -cnotmatch "^[0-9a-f]{64}$") { throw "$Label 必须是 64 位小写 SHA-256" }
}

function Resolve-RetirementJsonArtifact([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) {
    throw "$Label 必须是绝对 JSON 文件路径"
  }
  $resolved = Get-CanonicalPath $Value
  if ([IO.Path]::GetExtension($resolved) -ine ".json" -or -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
    throw "$Label 不存在或不是 JSON 文件"
  }
  if (((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Label 不得是重解析点"
  }
  return $resolved
}

function Assert-SalesRetirementWorkerStopped([string]$Boundary) {
  foreach ($port in @(3000, 5791)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "$Boundary 要求本机 Worker/工作流辅助端口 $port 已停止"
    }
  }
}

function Get-RetirementRuntimeContext {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "$Action 必须从受保护的 runtime app 启动脚本执行；请先运行 DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  if (-not (Test-Path -LiteralPath $RetirementOperator -PathType Leaf) -or
      -not (Test-Path -LiteralPath $RetirementMigration -PathType Leaf)) {
    throw "受保护 runtime app 缺少 retirement operator 或 0092"
  }
  if (-not (Test-Path -LiteralPath $Node -PathType Leaf)) { throw "缺少固定 Program Files Node.js 运行文件" }
  $nodeVersionRun = Invoke-BoundedNativeProcess $Node @("--version") $InstalledAppRoot
  $nodeVersionLines = @($nodeVersionRun.Output | ForEach-Object {
    [string]$_
  } | Where-Object { $_ -cmatch "^v24\.[0-9]+\.[0-9]+$" })
  if ($nodeVersionRun.ExitCode -ne 0 -or $nodeVersionLines.Count -ne 1) {
    throw "retirement operator 必须使用固定 Node.js 24 原生 TypeScript runtime（$(Get-NativeFailureSummary $nodeVersionRun)）"
  }
  $config = Get-ServiceConfig -RequireLegacyD1
  New-Item -ItemType Directory -Path $RetirementAuditDirectory -Force | Out-Null
  Assert-RuntimeAclHardened
  return [pscustomobject]@{
    Config = $config
    Source = [string]$config.erpSourceD1
    AuditOutput = Join-Path $RetirementAuditDirectory (
      "sales-d1-{0}.retirement.json" -f (Get-Sha256Text $CutoverId).Substring(0, 24)
    )
  }
}

function Invoke-RetirementNode([string[]]$Arguments, [string]$Operation) {
  $nativeRun = Invoke-BoundedNativeProcess $Node $Arguments $InstalledAppRoot
  return ConvertFrom-UniqueNativeJson $nativeRun $Operation
}

function Invoke-PlanSalesD1Retirement {
  if ($CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "cutoverId 无效" }
  Assert-Hex64 $AttestationSha256 "attestation SHA-256"
  if ($Execute.IsPresent -or -not [string]::IsNullOrWhiteSpace($ApprovedPlanId) -or
      -not [string]::IsNullOrWhiteSpace($SmokeReceiptPath) -or
      -not [string]::IsNullOrWhiteSpace($SmokeReceiptSha256)) {
    throw "PlanSalesD1Retirement 只生成只读计划，不接受 execute/plan/smoke 批准参数"
  }
  $attestation = Resolve-RetirementJsonArtifact $AttestationPath "attestation"
  $context = Get-RetirementRuntimeContext
  Assert-SalesRetirementWorkerStopped "D1 sales retirement plan 前"
  $payload = Invoke-RetirementNode @(
    $RetirementOperator,
    "--source", $context.Source,
    "--cutover-id", $CutoverId,
    "--attestation", $attestation,
    "--attestation-sha256", $AttestationSha256,
    "--audit-output", $context.AuditOutput
  ) "D1 sales retirement plan"
  if ([string]$payload.status -notin @("planned", "already_completed", "recovery_required") -or
      [string]$payload.cutoverId -cne $CutoverId -or
      [string]$payload.sourcePathSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]$payload.migrationSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]$payload.attestationSha256 -cne $AttestationSha256) {
    throw "D1 sales retirement plan 结果身份无效"
  }
  Assert-SalesRetirementWorkerStopped "D1 sales retirement plan 后"
  Write-LauncherEvent "INFO" "sales_d1_retirement_planned" ([string]$payload.status)
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-RetireSalesD1 {
  if (-not $Execute.IsPresent) { throw "RetireSalesD1 必须显式提供 -Execute" }
  if ($CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "cutoverId 无效" }
  Assert-Hex64 $AttestationSha256 "attestation SHA-256"
  Assert-Hex64 $ApprovedPlanId "approved plan id"
  Assert-Hex64 $SmokeReceiptSha256 "smoke receipt SHA-256"
  $attestation = Resolve-RetirementJsonArtifact $AttestationPath "attestation"
  $smokeReceipt = Resolve-RetirementJsonArtifact $SmokeReceiptPath "smoke receipt"
  $context = Get-RetirementRuntimeContext
  Assert-SalesRetirementWorkerStopped "D1 sales retirement execute 前"
  Wait-DjangoReady "reader" $DjangoReaderHealthUrl "127.0.0.1:8001"
  Wait-DjangoReady "writer" $DjangoWriterHealthUrl "127.0.0.1:8002"
  $secrets = Read-Secrets
  $previousManaged = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_RETIREMENT_MANAGED", "Process")
  try {
    $authority = Get-ActiveWriteAuthority $secrets
    if ([string]$authority.cutoverId -cne $CutoverId) {
      throw "PostgreSQL active authority 不属于本次 cutoverId"
    }
    $writerUrl = Database-Url "teruisi_sales_writer" $secrets.WriterPassword "teruisi_sales_retirement" $WriterStatementTimeoutMs
    $payload = Invoke-WithDjangoEnvironment $secrets $writerUrl "migration_writer" $false $WriterMaxBodyBytes ([string]$authority.authorityEpoch) $CutoverId {
      Assert-SalesRetirementWorkerStopped "D1 sales retirement managed execute 最终栅栏"
      $env:TERUISI_DJANGO_RETIREMENT_MANAGED = "1"
      Invoke-RetirementNode @(
        $RetirementOperator,
        "--managed-execute",
        "--runtime-root", (Get-CanonicalPath $RuntimeRoot),
        "--source", $context.Source,
        "--cutover-id", $CutoverId,
        "--attestation", $attestation,
        "--attestation-sha256", $AttestationSha256,
        "--audit-output", $context.AuditOutput,
        "--approved-plan-id", $ApprovedPlanId,
        "--smoke-receipt", $smokeReceipt,
        "--smoke-receipt-sha256", $SmokeReceiptSha256
      ) "D1 sales retirement execute"
    }
    $writerUrl = $null
  } finally {
    [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_RETIREMENT_MANAGED", $previousManaged, "Process")
    $secrets = $null
    $writerUrl = $null
  }
  if ([string]$payload.status -notin @("completed", "already_completed") -or
      [string]$payload.cutoverId -cne $CutoverId -or
      [string]$payload.approvedPlanId -cne $ApprovedPlanId -or
      [string]$payload.auditId -cnotmatch "^[0-9a-f]{64}$" -or
      [string]$payload.auditOutputPathSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "D1 sales retirement execute 结果身份无效"
  }
  Write-LauncherEvent "INFO" "sales_d1_retirement_completed" ([string]$payload.auditId)
  Write-Output ($payload | ConvertTo-Json -Compress)
}

function Invoke-CreateSalesCutoverSmokeReceipt {
  if (-not $Execute.IsPresent) {
    throw "CreateSalesCutoverSmokeReceipt 必须显式提供 -Execute"
  }
  if ($CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") { throw "cutoverId 无效" }
  Assert-Hex64 $ApprovedPlanId "approved plan id"
  Assert-Hex64 $AttestationSha256 "attestation SHA-256"
  if (-not [string]::IsNullOrWhiteSpace($AttestationPath) -or
      -not [string]::IsNullOrWhiteSpace($SmokeReceiptPath) -or
      -not [string]::IsNullOrWhiteSpace($SmokeReceiptSha256)) {
    throw "smoke receipt action 不接受 attestation/receipt 路径或已有 receipt 摘要"
  }
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "CreateSalesCutoverSmokeReceipt 必须从受保护的 runtime app 执行"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-SalesRetirementWorkerStopped "正式本机 sales smoke 前"
  Wait-DjangoReady "reader" $DjangoReaderHealthUrl "127.0.0.1:8001"
  Wait-DjangoReady "writer" $DjangoWriterHealthUrl "127.0.0.1:8002"
  $smokeParent = Assert-RuntimeChildPath (Join-Path $RetirementAuditDirectory "smoke")
  New-Item -ItemType Directory -Path $smokeParent -Force | Out-Null
  Assert-RuntimeAclHardened
  $destination = Assert-RuntimeChildPath (Join-Path $smokeParent (
    "receipt-{0}-{1}" -f (Get-Sha256Text $CutoverId).Substring(0, 16), [Guid]::NewGuid().ToString("N")
  ))
  $secrets = Read-Secrets
  $writerUrl = $null
  try {
    $authority = Get-ActiveWriteAuthority $secrets
    if ([string]$authority.cutoverId -cne $CutoverId) {
      throw "PostgreSQL active authority 不属于本次 cutoverId"
    }
    $writerUrl = Database-Url "teruisi_sales_writer" $secrets.WriterPassword (
      "teruisi_cutover_smoke_" + (Get-Sha256Text $CutoverId).Substring(0, 12)
    ) $WriterStatementTimeoutMs
    $payload = Invoke-WithDjangoEnvironment $secrets $writerUrl "sales_writer" $false $WriterMaxBodyBytes ([string]$authority.authorityEpoch) $CutoverId {
      $nativeRun = Invoke-BoundedNativeProcess $Python @(
        (Join-Path $BackendRoot "manage.py"),
        "sales_cutover_smoke_receipt",
        "--plan-id", $ApprovedPlanId,
        "--cutover-id", $CutoverId,
        "--attestation-sha256", $AttestationSha256,
        "--output-directory", $destination
      ) $BackendRoot
      return ConvertFrom-UniqueNativeJson $nativeRun "正式本机 sales smoke"
    }
  } finally {
    $writerUrl = $null
    $secrets = $null
  }
  $receiptPath = Join-Path $destination "receipt.json"
  if ([string]$payload.status -cne "completed" -or
      [string]$payload.planId -cne $ApprovedPlanId -or
      [string]$payload.cutoverId -cne $CutoverId -or
      [string]$payload.attestationPayloadSha256 -cne $AttestationSha256 -or
      [string]$payload.receiptSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      -not (Test-Path -LiteralPath $receiptPath -PathType Leaf) -or
      (Get-FileSha256 $receiptPath) -cne [string]$payload.receiptSha256) {
    throw "正式本机 sales smoke receipt 证据无效"
  }
  Assert-SalesRetirementWorkerStopped "正式本机 sales smoke 后"
  Write-LauncherEvent "INFO" "sales_cutover_smoke_completed" ([string]$payload.receiptSha256)
  Write-Output ([ordered]@{
    status = "completed"
    cutoverId = $CutoverId
    planId = $ApprovedPlanId
    receiptPath = $receiptPath
    receiptSha256 = [string]$payload.receiptSha256
    checkedAt = [string]$payload.checkedAt
  } | ConvertTo-Json -Compress)
}

function Start-ServiceStack {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "Start 必须从受保护的 runtime app 启动脚本执行；请先运行 DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $config = Get-ServiceConfig
  New-Item -ItemType Directory -Path $LogDirectory, $RunDirectory -Force | Out-Null
  Remove-OldServiceLogs "postgres"
  $secrets = Read-Secrets
  $postgresStarted = $false
  $readerStarted = $false
  $writerStarted = $false
  $financeReaderStarted = $false
  $financeWriterStarted = $false
  $salesCoreReady = $false
  try {
    if ((Resolve-OwnedProcess "erp-reference-sync" $ErpReferenceSyncPidPath $Python) -or
        @(Get-ErpReferenceSyncCandidates).Count -gt 0) {
      throw "ERP 主数据已切换 PostgreSQL；旧 ERP reference sync 必须保持停止"
    }
    $postgresStarted = Start-Postgres
    Assert-PostgresConnectionCapacity $secrets | Out-Null
    Invoke-DjangoMigrations $secrets
    $authority = Get-ActiveWriteAuthority $secrets
    $readerStarted = Start-DjangoReader $secrets -DeferReady
    $writerStarted = Start-DjangoWriter $secrets $authority
    Wait-DjangoReady "reader" $DjangoReaderHealthUrl "127.0.0.1:8001"
    Wait-DjangoReady "writer" $DjangoWriterHealthUrl "127.0.0.1:8002"
    $salesCoreReady = $true
    $financeReaderStarted = Start-DjangoFinanceReader $secrets -DeferReady
    $financeAuthority = Get-FinanceWriteAuthority $secrets
    if ([string]$financeAuthority.status -ceq "postgres") {
      $financeWriterStarted = Start-DjangoFinanceWriter $secrets $financeAuthority
    }
    Wait-DjangoReady "finance-reader" $DjangoFinanceReaderHealthUrl "127.0.0.1:8011"
    if ([string]$financeAuthority.status -ceq "postgres") {
      Wait-DjangoReady "finance-writer" $DjangoFinanceWriterHealthUrl "127.0.0.1:8012"
    }
    Write-LauncherEvent "INFO" "service_stack_ready"
    $financeWriterState = if ([string]$financeAuthority.status -ceq "postgres") { "ready" } else { "not_active" }
    Write-Output "Django 本机服务已就绪：sales reader=8001 writer=8002，finance reader=8011 writer=$financeWriterState；ERP 主数据由独立 PostgreSQL 服务承担。"
  } catch {
    $originalError = $_.Exception
    Write-LauncherEvent "ERROR" "service_stack_start_failed" $originalError.Message
    if ($financeWriterStarted) {
      try { Stop-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress } catch {
        Write-LauncherEvent "ERROR" "rollback_failed" "django-finance-writer: $($_.Exception.Message)"
      }
    }
    if ($financeReaderStarted) {
      try { Stop-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress } catch {
        Write-LauncherEvent "ERROR" "rollback_failed" "django-finance-reader: $($_.Exception.Message)"
      }
    }
    if ($salesCoreReady) {
      Write-LauncherEvent "ERROR" "finance_domain_start_failed_sales_preserved" $originalError.Message
      throw $originalError
    }
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-writer" $DjangoWriterPidPath $Waitress } catch {
        Write-LauncherEvent "ERROR" "rollback_failed" "django-writer: $($_.Exception.Message)"
      }
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-reader" $DjangoReaderPidPath $Waitress } catch {
        Write-LauncherEvent "ERROR" "rollback_failed" "django-reader: $($_.Exception.Message)"
      }
    }
    if ($postgresStarted) {
      try { Stop-Postgres } catch {
        Write-LauncherEvent "ERROR" "rollback_failed" "postgres: $($_.Exception.Message)"
      }
    }
    throw $originalError
  } finally {
    $secrets = $null
  }
}

function Stop-ServiceStack {
  if ($KeepPostgres -and -not (Test-MaintenanceKeepsPostgres (Read-SystemMaintenance))) {
    throw "Keeping PostgreSQL requires application maintenance through the Worker controller"
  }
  Stop-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress
  Stop-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress
  Stop-OwnedProcess "django-writer" $DjangoWriterPidPath $Waitress
  Stop-OwnedProcess "django-reader" $DjangoReaderPidPath $Waitress
  if (@(Get-ErpReferenceSyncCandidates).Count -gt 0) {
    throw "Stop 发现终态禁止的 ERP reference sync 进程；拒绝自动终止该进程或停止其 PostgreSQL"
  }
  if ($KeepPostgres) {
    Assert-PostgresListenerOwnership | Out-Null
    if (-not (Test-PostgresReady)) { throw "PostgreSQL was not preserved ready" }
    Write-Output "Django 应用进程已停止；PostgreSQL 保持运行。"
  } else {
    Stop-Postgres
    Write-Output "Django 本机服务已停止；数据目录未删除。"
  }
}

function Start-FinanceStack {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "StartFinance 必须从受保护的 runtime app 启动脚本执行；请先运行 DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL 未就绪；拒绝启动财务服务" }
  $secrets = Read-Secrets
  $readerStarted = $false
  $writerStarted = $false
  try {
    $readerStarted = Start-DjangoFinanceReader $secrets -DeferReady
    $authority = Get-FinanceWriteAuthority $secrets
    if ([string]$authority.status -ceq "postgres") {
      $writerStarted = Start-DjangoFinanceWriter $secrets $authority
    }
    Wait-DjangoReady "finance-reader" $DjangoFinanceReaderHealthUrl "127.0.0.1:8011"
    if ([string]$authority.status -ceq "postgres") {
      Wait-DjangoReady "finance-writer" $DjangoFinanceWriterHealthUrl "127.0.0.1:8012"
      Write-Output "Django 财务服务已就绪：reader=http://127.0.0.1:8011 writer=http://127.0.0.1:8012。"
    } else {
      Write-Output "Django 财务 reader 已就绪；PostgreSQL 财务写权尚未激活，writer 保持停止。"
    }
  } catch {
    $originalError = $_.Exception
    if ($writerStarted) {
      try { Stop-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress } catch {}
    }
    if ($readerStarted) {
      try { Stop-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress } catch {}
    }
    throw $originalError
  } finally {
    $secrets = $null
  }
}

function Stop-FinanceStack {
  Stop-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress
  Stop-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress
  Write-Output "Django 财务 reader/writer 已停止；销售、ERP 与 PostgreSQL 未改变。"
}

function Show-ServiceStatus {
  $postgres = "stopped"
  if (@(Get-PortListeners 5432).Count -gt 0) {
    try {
      Assert-PostgresListenerOwnership | Out-Null
      $postgres = if (Test-PostgresReady) { "running" } else { "not_ready" }
    } catch {
      $postgres = "foreign_or_unverified"
    }
  }
  $reader = "stopped"
  try {
    if (Resolve-OwnedProcess "django-reader" $DjangoReaderPidPath $Waitress) { $reader = "running" }
    elseif (@(Get-PortListeners 8001).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-writer" $DjangoWriterPidPath $Waitress) { $writer = "running" }
    elseif (@(Get-PortListeners 8002).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  if ($reader -eq "running") {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $DjangoReaderHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8001" }
      if ($response.StatusCode -eq 200) { $readerReady = "ready" }
    } catch {}
  }
  $writerReady = "not_ready"
  if ($writer -eq "running") {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $DjangoWriterHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8002" }
      if ($response.StatusCode -eq 200) { $writerReady = "ready" }
    } catch {}
  }
  # Status must stay operationally bounded even when retained backup/rehearsal
  # evidence contains tens of thousands of files.  A complete descendant ACL
  # audit remains mandatory in HardenAcl and every sensitive lifecycle action;
  # the read-only status path reports its deliberately narrower root contract.
  $acl = "not_hardened"
  try { Assert-RuntimeRootAclHardened; $acl = "root_hardened" } catch {}
  $status = [pscustomobject][ordered]@{
    PostgreSQL = $postgres
    DjangoReader = $reader
    DjangoWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    RuntimeAcl = $acl
    RuntimeAclVerification = "root_only_status"
    Startup = if (Test-Path -LiteralPath $StartupShortcut) { "installed" } else { "not_installed" }
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($Json.IsPresent) {
    Write-Output ($status | ConvertTo-Json -Compress)
  } else {
    $status | Format-List
  }
}

function Show-FinanceServiceStatus {
  $reader = "stopped"
  try {
    if (Resolve-OwnedProcess "django-finance-reader" $DjangoFinanceReaderPidPath $Waitress) { $reader = "running" }
    elseif (@(Get-PortListeners 8011).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess "django-finance-writer" $DjangoFinanceWriterPidPath $Waitress) { $writer = "running" }
    elseif (@(Get-PortListeners 8012).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  if ($reader -eq "running") {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $DjangoFinanceReaderHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8011" }
      if ($response.StatusCode -eq 200) { $readerReady = "ready" }
    } catch {}
  }
  $writerReady = "not_ready"
  if ($writer -eq "running") {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $DjangoFinanceWriterHealthUrl -TimeoutSec 2 -Headers @{ Host = "127.0.0.1:8012" }
      if ($response.StatusCode -eq 200) { $writerReady = "ready" }
    } catch {}
  }
  $authority = "unknown"
  try {
    if (Test-PostgresReady) {
      $statusSecrets = Read-Secrets
      try {
        $authorityPayload = Get-FinanceWriteAuthority $statusSecrets
        $authority = [string]$authorityPayload.status
      } finally {
        $statusSecrets = $null
      }
    }
  } catch {}
  $status = [pscustomobject][ordered]@{
    FinanceReader = $reader
    FinanceWriter = $writer
    ReaderReadiness = $readerReady
    WriterReadiness = $writerReady
    PostgreSQLAuthority = $authority
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($Json.IsPresent) {
    Write-Output ($status | ConvertTo-Json -Compress)
  } else {
    $status | Format-List
  }
}

function Invoke-InProcessJsonStatus([scriptblock]$Operation, [string]$Label) {
  $previousJson = $Json
  try {
    $script:Json = [switch]$true
    $raw = ((& $Operation) | Out-String).Trim()
  } finally {
    $script:Json = $previousJson
  }
  if ([string]::IsNullOrWhiteSpace($raw)) { throw "$Label 未返回 JSON" }
  try { return ($raw | ConvertFrom-Json -ErrorAction Stop) }
  catch { throw "$Label 返回了无效 JSON" }
}

function Get-SimpleDjangoDomainStatus(
  [string]$Service,
  [string]$ReaderPidPath,
  [string]$WriterPidPath,
  [int]$ReaderPort,
  [int]$WriterPort,
  [string]$ReaderHealthUrl,
  [string]$WriterHealthUrl,
  [string]$ReaderProperty,
  [string]$WriterProperty
) {
  $reader = "stopped"
  try {
    if (Resolve-OwnedProcess $Service $ReaderPidPath $Waitress) { $reader = "running" }
    elseif (@(Get-PortListeners $ReaderPort).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  $writerService = $Service.Replace("-reader", "-writer")
  $writer = "stopped"
  try {
    if (Resolve-OwnedProcess $writerService $WriterPidPath $Waitress) { $writer = "running" }
    elseif (@(Get-PortListeners $WriterPort).Count -gt 0) { $writer = "foreign_port_owner" }
  } catch { $writer = "ownership_error" }
  $readerReady = "not_ready"
  if ($reader -eq "running") {
    try {
      if ((Invoke-WebRequest -UseBasicParsing -Uri $ReaderHealthUrl -TimeoutSec 2 `
            -Headers @{ Host = "127.0.0.1:$ReaderPort" }).StatusCode -eq 200) {
        $readerReady = "ready"
      }
    } catch {}
  }
  $writerReady = "not_ready"
  if ($writer -eq "running") {
    try {
      if ((Invoke-WebRequest -UseBasicParsing -Uri $WriterHealthUrl -TimeoutSec 2 `
            -Headers @{ Host = "127.0.0.1:$WriterPort" }).StatusCode -eq 200) {
        $writerReady = "ready"
      }
    } catch {}
  }
  $status = [ordered]@{}
  $status[$ReaderProperty] = $reader
  $status[$WriterProperty] = $writer
  $status["ReaderReadiness"] = $readerReady
  $status["WriterReadiness"] = $writerReady
  $status["CheckedAt"] = [DateTimeOffset]::UtcNow.ToString("o")
  return [pscustomobject]$status
}

function Get-SimpleDjangoReaderStatus(
  [string]$Service,
  [string]$ReaderPidPath,
  [int]$ReaderPort,
  [string]$ReaderHealthUrl,
  [string]$ReaderProperty
) {
  $reader = "stopped"
  try {
    if (Resolve-OwnedProcess $Service $ReaderPidPath $Waitress) { $reader = "running" }
    elseif (@(Get-PortListeners $ReaderPort).Count -gt 0) { $reader = "foreign_port_owner" }
  } catch { $reader = "ownership_error" }
  $readerReady = "not_ready"
  if ($reader -eq "running") {
    try {
      if ((Invoke-WebRequest -UseBasicParsing -Uri $ReaderHealthUrl -TimeoutSec 2 `
            -Headers @{ Host = "127.0.0.1:$ReaderPort" }).StatusCode -eq 200) {
        $readerReady = "ready"
      }
    } catch {}
  }
  $status = [ordered]@{}
  $status[$ReaderProperty] = $reader
  $status["ReaderReadiness"] = $readerReady
  $status["CheckedAt"] = [DateTimeOffset]::UtcNow.ToString("o")
  return [pscustomobject]$status
}

function Show-AggregateServiceStatus {
  # Load the base controller once and inspect all exact process receipts in the
  # same process.  The previous implementation cold-started seven pwsh
  # interpreters serially, repeatedly parsing this controller before any
  # service could start.
  $timer = [Diagnostics.Stopwatch]::StartNew()
  $core = Invoke-InProcessJsonStatus { Show-ServiceStatus } "Django/PostgreSQL 状态"
  $finance = Invoke-InProcessJsonStatus { Show-FinanceServiceStatus } "Django 财务状态"
  $netshop = Get-SimpleDjangoDomainStatus `
    "django-netshop-reader" $DjangoNetshopReaderPidPath $DjangoNetshopWriterPidPath `
    8021 8022 "http://127.0.0.1:8021/health/ready" "http://127.0.0.1:8022/health/ready" `
    "NetshopReader" "NetshopWriter"
  $market = Get-SimpleDjangoDomainStatus `
    "django-market-reader" $DjangoMarketReaderPidPath $DjangoMarketWriterPidPath `
    8031 8032 "http://127.0.0.1:8031/health/ready" "http://127.0.0.1:8032/health/ready" `
    "MarketReader" "MarketWriter"
  $products = Get-SimpleDjangoDomainStatus `
    "django-products-reader" $DjangoProductsReaderPidPath $DjangoProductsWriterPidPath `
    8041 8042 "http://127.0.0.1:8041/health/ready" "http://127.0.0.1:8042/health/ready" `
    "ProductsReader" "ProductsWriter"
  $inventory = Get-SimpleDjangoDomainStatus `
    "django-inventory-reader" $DjangoInventoryReaderPidPath $DjangoInventoryWriterPidPath `
    8051 8052 "http://127.0.0.1:8051/health/ready" "http://127.0.0.1:8052/health/ready" `
    "InventoryReader" "InventoryWriter"
  $workflow = Get-SimpleDjangoDomainStatus `
    "django-workflow-reader" $DjangoWorkflowReaderPidPath $DjangoWorkflowWriterPidPath `
    8061 8062 "http://127.0.0.1:8061/health/ready" "http://127.0.0.1:8062/health/ready" `
    "WorkflowReader" "WorkflowWriter"
  $customerService = Get-SimpleDjangoDomainStatus `
    "django-customer-service-reader" $DjangoCustomerServiceReaderPidPath $DjangoCustomerServiceWriterPidPath `
    8071 8072 "http://127.0.0.1:8071/health/ready" "http://127.0.0.1:8072/health/ready" `
    "CustomerServiceReader" "CustomerServiceWriter"
  $accessControl = Get-SimpleDjangoDomainStatus `
    "django-access-control-reader" $DjangoAccessControlReaderPidPath $DjangoAccessControlWriterPidPath `
    8101 8102 "http://127.0.0.1:8101/health/ready" "http://127.0.0.1:8102/health/ready" `
    "AccessControlReader" "AccessControlWriter"
  $erpReference = Get-SimpleDjangoDomainStatus `
    "django-erp-reference-reader" $DjangoErpReferenceReaderPidPath $DjangoErpReferenceWriterPidPath `
    8091 8092 "http://127.0.0.1:8091/health/ready" "http://127.0.0.1:8092/health/ready" `
    "ErpReferenceReader" "ErpReferenceWriter"
  $ai = if (Test-Path -LiteralPath $AiStartupEnabledPath -PathType Leaf) {
    Get-SimpleDjangoDomainStatus "django-ai-reader" $DjangoAiReaderPidPath $DjangoAiWriterPidPath 8111 8112 $DjangoAiReaderHealthUrl $DjangoAiWriterHealthUrl "AiReader" "AiWriter"
  } else { $null }
  if ($null -ne $ai -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot "config\pandas-sandbox.json"))) {
    $pandasReadiness = "not_ready"
    try {
      $pandas = Read-JsonFile (Join-Path $RuntimeRoot "config\pandas-sandbox.json") "pandas configuration"
      $pandasKey = Join-Path $RuntimeRoot "secrets\pandas-sandbox.dpapi.json"
      if (-not (Test-ExactObjectPropertyNames $pandas @("version", "image", "keySha256")) -or [int]$pandas.version -ne 1 -or
          (Get-FileHash -LiteralPath $pandasKey -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$pandas.keySha256) { throw "invalid pandas configuration" }
      $probeRun = Invoke-BoundedNativeProcess $Python @("-B", "-m", "pandas_runner.probe", "--key-file", $pandasKey, "--image", [string]$pandas.image) $BackendRoot
      $probe = ConvertFrom-UniqueNativeJson $probeRun "pandas readiness"
      if ([string]$probe.status -ceq "ready") { $pandasReadiness = "ready" }
    } catch {}
    $ai | Add-Member -NotePropertyName PandasReadiness -NotePropertyValue $pandasReadiness
    if ($pandasReadiness -cne "ready") { $ai.WriterReadiness = "not_ready" }
  }
  $bi = Get-SimpleDjangoReaderStatus `
    "django-bi-reader" $DjangoBiReaderPidPath 8081 $DjangoBiReaderHealthUrl "BiReader"
  $timer.Stop()
  $status = [pscustomobject][ordered]@{
    Version = "teruisi-django-aggregate-status-v1"
    Core = $core
    Finance = $finance
    Netshop = $netshop
    Market = $market
    Products = $products
    Inventory = $inventory
    Workflow = $workflow
    CustomerService = $customerService
    AccessControl = $accessControl
    Ai = $ai
    ErpReference = $erpReference
    Bi = $bi
    ElapsedMilliseconds = [int64]$timer.ElapsedMilliseconds
    CheckedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  if ($Json.IsPresent) { Write-Output ($status | ConvertTo-Json -Depth 5 -Compress) }
  else { $status | Format-List }
}

function Install-StartupShortcut {
  Get-ServiceConfig | Out-Null
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  if ((Get-CanonicalPath $PSCommandPath) -ine (Get-CanonicalPath $InstalledScriptPath)) {
    if ((Get-FileSha256 $PSCommandPath) -ne (Get-FileSha256 $InstalledScriptPath)) {
      throw "runtime app 启动脚本不是当前版本；请重新执行 DeployApp"
    }
  }
  if ($InstalledScriptPath.Contains('"') -or $RuntimeRoot.Contains('"')) { throw "自启动路径不得包含双引号" }
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($StartupShortcut)
  $shortcut.TargetPath = (Get-Command "powershell.exe").Source
  $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstalledScriptPath`" -Action Start -RuntimeRoot `"$RuntimeRoot`""
  $shortcut.WorkingDirectory = $InstalledAppRoot
  $shortcut.WindowStyle = 7
  $shortcut.Description = "TERUISI Django sales service"
  $shortcut.Save()
  Write-LauncherEvent "INFO" "startup_installed"
  Write-Output "已安装当前 Windows 用户登录自启动。"
}

function Read-SystemMaintenance {
  $candidate = Assert-RuntimeChildPath $MaintenancePath
  while ($candidate) {
    if (Test-Path -LiteralPath $candidate) {
      $entry = Get-Item -LiteralPath $candidate -Force
      if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Maintenance paths must not contain reparse points" }
    }
    $candidate = Split-Path -Parent $candidate
  }
  if (-not (Test-Path -LiteralPath $MaintenancePath)) { return $null }
  $entry = Get-Item -LiteralPath $MaintenancePath -Force
  if ($entry.PSIsContainer -or $entry.Length -gt 4096) { throw "Invalid maintenance state file" }
  try { $record = [IO.File]::ReadAllText($MaintenancePath) | ConvertFrom-Json -ErrorAction Stop }
  catch { throw "Maintenance state is unreadable; refusing lifecycle changes" }
  if ($record.version -cne "teruisi-system-maintenance-v1" -or
      [string]$record.id -cnotmatch "^[0-9a-f]{32}$" -or
      [string]$record.runtimeRoot -ine (Get-CanonicalPath $RuntimeRoot)) {
    throw "Maintenance state binding is invalid; refusing lifecycle changes"
  }
  return $record
}

function Assert-NoSystemMaintenance {
  $record = Read-SystemMaintenance
  if ($record) { throw "System maintenance is active: $($record.id). Explicitly end that maintenance before starting services." }
}

function Assert-ProductionMaintenance([string]$Operation) {
  # Existing disposable runtime deployment fixtures remain usable. Production
  # deployments require a durable gate that survives the stop/deploy gap.
  if ((Get-CanonicalPath $RuntimeRoot) -ine (Get-CanonicalPath "D:\teruisi-runtime\django-sales")) { return }
  if (-not (Read-SystemMaintenance)) { throw "$Operation requires EnterMaintenance through the Worker lifecycle controller" }
  Assert-SalesRetirementWorkerStopped $Operation
  # An idle port alone is insufficient: a live supervisor can be between
  # children. Successful Worker Stop removes this identity-bound receipt.
  if (Test-Path -LiteralPath "D:\teruisi-runtime\teruisi-worker-sales\state\worker-process.json") {
    throw "$Operation requires the Worker supervisor receipt to be cleared by its controlled Stop"
  }
}

function Begin-SystemMaintenance {
  if ($MaintenanceId -cnotmatch "^[0-9a-f]{32}$") { throw "MaintenanceId must be an explicit 32-character lowercase operation id" }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $existing = Read-SystemMaintenance
  if ($existing) {
    if ($existing.id -cne $MaintenanceId) { throw "Another maintenance operation is active: $($existing.id)" }
    if ((Test-MaintenanceKeepsPostgres $existing) -ne [bool]$KeepPostgres) { throw "Maintenance scope changed" }
    return
  }
  Write-AtomicJson $MaintenancePath ([ordered]@{
    version = "teruisi-system-maintenance-v1"; id = $MaintenanceId
    runtimeRoot = Get-CanonicalPath $RuntimeRoot; createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    keepPostgres = [bool]$KeepPostgres
  })
  Write-LauncherEvent "INFO" "system_maintenance_entered" $MaintenanceId
}

function End-SystemMaintenance {
  if ($MaintenanceId -cnotmatch "^[0-9a-f]{32}$") { throw "An exact MaintenanceId is required" }
  $record = Read-SystemMaintenance
  if (-not $record -or $record.id -cne $MaintenanceId) { throw "Maintenance ownership changed; refusing to clear the gate" }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Assert-ApplicationDeploymentStopped "EndMaintenance"
  Assert-SalesRetirementWorkerStopped "EndMaintenance"
  [IO.File]::Delete((Assert-RuntimeChildPath $MaintenancePath))
  Write-LauncherEvent "INFO" "system_maintenance_ended" $MaintenanceId
}

function Invoke-WithServiceMutex(
  [scriptblock]$Operation,
  [ValidateRange(0, 900)][int]$StartWaitSeconds = 900
) {
  $name = "Local\TERUISI-DjangoSales-" + (Get-Sha256Text (Get-CanonicalPath $RuntimeRoot)).Substring(0, 24)
  $mutex = [Threading.Mutex]::new($false, $name)
  $acquired = $false
  $joinedStart = $false
  try {
    try {
      $acquired = $mutex.WaitOne([TimeSpan]::Zero)
      if (-not $acquired) {
        $joinedStart = $Action -ceq "Start"
        $waitSeconds = if ($joinedStart) { $StartWaitSeconds } else { 30 }
        if ($joinedStart) { Write-LauncherEvent "INFO" "concurrent_start_waiting" }
        $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($waitSeconds))
      }
    } catch [Threading.AbandonedMutexException] {
      $acquired = $true
      Write-LauncherEvent "WARN" "abandoned_mutex_recovered"
    }
    if (-not $acquired) { throw "另一个 Django 本机服务操作仍在运行" }
    if ($Action -match "^(Start|AutoStart)") { Assert-NoSystemMaintenance }
    if ($joinedStart) {
      # A queued Start must not reverse a Stop or adopt a changed deployment.
      # Only a valid running intent permits continuing the original Start path;
      # the caller still rechecks its exact supervisor fence and full readiness.
      Assert-SupervisorStartFence (Get-FileSha256 $SupervisorDesiredStatePath)
      Write-LauncherEvent "INFO" "concurrent_start_resumed"
    }
    & $Operation
  } finally {
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
  }
}

function Invoke-EnabledDjangoDomainStarts([string]$OrchestratedLifecycleAclToken) {
  if ($OrchestratedLifecycleAclToken -cnotmatch "^[0-9a-f]{64}$") {
    throw "orchestrated lifecycle ACL token 无效"
  }
  if (Test-Path -LiteralPath $NetshopStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledNetshopScriptPath -PathType Leaf)) {
      throw "网店开机启动已启用，但受控网店服务脚本缺失"
    }
    & $InstalledNetshopScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $MarketStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledMarketScriptPath -PathType Leaf)) {
      throw "市场开机启动已启用，但受控市场服务脚本缺失"
    }
    & $InstalledMarketScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $ProductsStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledProductsScriptPath -PathType Leaf)) {
      throw "商品经营开机启动已启用，但受控商品经营服务脚本缺失"
    }
    & $InstalledProductsScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InventoryStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledInventoryScriptPath -PathType Leaf)) {
      throw "库存开机启动已启用，但受控库存服务脚本缺失"
    }
    & $InstalledInventoryScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $WorkflowStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledWorkflowScriptPath -PathType Leaf)) {
      throw "运营事务新品开机启动已启用，但受控运营事务新品服务脚本缺失"
    }
    & $InstalledWorkflowScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $CustomerServiceStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledCustomerServiceScriptPath -PathType Leaf)) {
      throw "客服开机启动已启用，但受控客服服务脚本缺失"
    }
    & $InstalledCustomerServiceScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $AccessControlStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledAccessControlScriptPath -PathType Leaf)) {
      throw "权限开机启动已启用，但受控权限服务脚本缺失"
    }
    & $InstalledAccessControlScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $AiStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledAiScriptPath -PathType Leaf)) {
      throw "AI 助理开机启动已启用，但受控AI 助理服务脚本缺失"
    }
    & $InstalledAiScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $ErpReferenceStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledErpReferenceScriptPath -PathType Leaf)) {
      throw "ERP 主数据开机启动已启用，但受控 ERP 服务脚本缺失"
    }
    & $InstalledErpReferenceScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $BiStartupEnabledPath -PathType Leaf) {
    if (-not (Test-Path -LiteralPath $InstalledBiScriptPath -PathType Leaf)) {
      throw "BI 开机启动已启用，但受控 BI 服务脚本缺失"
    }
    & $InstalledBiScriptPath -Action Start -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
}

function Invoke-InstalledDjangoDomainStops([string]$OrchestratedLifecycleAclToken) {
  if ($OrchestratedLifecycleAclToken -cnotmatch "^[0-9a-f]{64}$") {
    throw "orchestrated lifecycle ACL token 无效"
  }
  if (Test-Path -LiteralPath $InstalledBiScriptPath -PathType Leaf) {
    & $InstalledBiScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledAiScriptPath -PathType Leaf) {
    & $InstalledAiScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledAccessControlScriptPath -PathType Leaf) {
    & $InstalledAccessControlScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledInventoryScriptPath -PathType Leaf) {
    & $InstalledInventoryScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledWorkflowScriptPath -PathType Leaf) {
    & $InstalledWorkflowScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledProductsScriptPath -PathType Leaf) {
    & $InstalledProductsScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledMarketScriptPath -PathType Leaf) {
    & $InstalledMarketScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledNetshopScriptPath -PathType Leaf) {
    & $InstalledNetshopScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledCustomerServiceScriptPath -PathType Leaf) {
    & $InstalledCustomerServiceScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
  if (Test-Path -LiteralPath $InstalledErpReferenceScriptPath -PathType Leaf) {
    & $InstalledErpReferenceScriptPath -Action Stop -RuntimeRoot $RuntimeRoot `
      -OrchestratedLifecycleAclToken $OrchestratedLifecycleAclToken
  }
}

if ($env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY -ne "1") {
  try {
    Write-LauncherEvent "INFO" "action_started"
    switch ($Action) {
      "Configure" { Invoke-WithServiceMutex { Configure-Service } }
      "PrepareApp" { Prepare-Application | ConvertTo-Json -Compress }
      "DeployApp" { Invoke-WithServiceMutex { Deploy-Application } }
      "RollbackApp" { Invoke-WithServiceMutex { Rollback-Application } }
      "HardenAcl" { Invoke-WithServiceMutex { Set-RuntimeAcl } }
      "MaintenanceStatus" { @{ maintenance = Read-SystemMaintenance } | ConvertTo-Json -Depth 4 -Compress }
      "Start" {
        $previousAclContextVariable = Get-Variable -Scope Global `
          -Name $OrchestratedLifecycleAclContextVariable -ErrorAction SilentlyContinue
        $hadPreviousAclContext = $null -ne $previousAclContextVariable
        $previousAclContext = if ($hadPreviousAclContext) { $previousAclContextVariable.Value } else { $null }
        try {
          Invoke-WithServiceMutex {
            if ([string]::IsNullOrWhiteSpace($SupervisorExpectedDesiredStateSha256)) {
              Start-ServiceStack
              Write-ServiceDesiredState "running" "explicit_start"
            } else {
              Assert-SupervisorStartFence $SupervisorExpectedDesiredStateSha256
              Start-ServiceStack
            }
            $orchestratedLifecycleAclToken = New-OrchestratedLifecycleAclToken
            Set-OrchestratedLifecycleAclContext $orchestratedLifecycleAclToken
            Invoke-EnabledDjangoDomainStarts $orchestratedLifecycleAclToken
          }
        } finally {
          if ($hadPreviousAclContext) {
            Set-Variable -Scope Global -Name $OrchestratedLifecycleAclContextVariable `
              -Value $previousAclContext -Force
          } else {
            Remove-Variable -Scope Global -Name $OrchestratedLifecycleAclContextVariable `
              -ErrorAction SilentlyContinue
          }
        }
      }
      "Stop" {
        if ($KeepPostgres -and -not (Test-MaintenanceKeepsPostgres (Read-SystemMaintenance))) {
          throw "Keeping PostgreSQL requires application maintenance through the Worker controller"
        }
        $previousAclContextVariable = Get-Variable -Scope Global `
          -Name $OrchestratedLifecycleAclContextVariable -ErrorAction SilentlyContinue
        $hadPreviousAclContext = $null -ne $previousAclContextVariable
        $previousAclContext = if ($hadPreviousAclContext) { $previousAclContextVariable.Value } else { $null }
        try {
          Invoke-WithServiceMutex {
            Write-ServiceDesiredState "stopped" "explicit_stop"
            Assert-DeployedApplication
            Assert-RuntimeAclHardened
            $orchestratedLifecycleAclToken = New-OrchestratedLifecycleAclToken
            Set-OrchestratedLifecycleAclContext $orchestratedLifecycleAclToken
            Invoke-InstalledDjangoDomainStops $orchestratedLifecycleAclToken
            Stop-ServiceStack
          }
        } finally {
          if ($hadPreviousAclContext) {
            Set-Variable -Scope Global -Name $OrchestratedLifecycleAclContextVariable `
              -Value $previousAclContext -Force
          } else {
            Remove-Variable -Scope Global -Name $OrchestratedLifecycleAclContextVariable `
              -ErrorAction SilentlyContinue
          }
        }
      }
      "Status" { Show-ServiceStatus }
      "AggregateStatus" { Show-AggregateServiceStatus }
      "ProvisionFinanceRoles" { Invoke-WithServiceMutex { Provision-FinanceDatabaseRoles } }
      "StartFinance" { Invoke-WithServiceMutex { Start-FinanceStack } }
      "StopFinance" { Invoke-WithServiceMutex { Stop-FinanceStack } }
      "FinanceStatus" { Show-FinanceServiceStatus }
      "PlanSalesD1Retirement" { Invoke-WithServiceMutex { Invoke-PlanSalesD1Retirement } }
      "RetireSalesD1" { Invoke-WithServiceMutex { Invoke-RetireSalesD1 } }
      "CreateSalesCutoverSmokeReceipt" { Invoke-WithServiceMutex { Invoke-CreateSalesCutoverSmokeReceipt } }
      "InstallStartup" { Invoke-WithServiceMutex { Install-StartupShortcut } }
      "RemoveStartup" {
        Invoke-WithServiceMutex {
          if (Test-Path -LiteralPath $StartupShortcut) { Remove-Item -LiteralPath $StartupShortcut -Force }
          Write-LauncherEvent "INFO" "startup_removed"
          Write-Output "已移除当前 Windows 用户登录自启动；服务与数据未删除。"
        }
      }
    }
    Write-LauncherEvent "INFO" "action_completed"
  } catch {
    Write-LauncherEvent "ERROR" "action_failed" $_.Exception.Message
    throw
  }
}
