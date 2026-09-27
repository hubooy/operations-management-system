[CmdletBinding()]
param(
  [string]$BaseUrl = "http://localhost:3000",
  [string]$ReleaseRoot,
  [string]$D1Path,
  [string]$R2Evidence,
  [string]$AuditDirectory,
  [string]$CutoverId,
  [string]$MigrationRunId,
  [string]$SourceDigest,
  [string]$TargetDigest,
  [string]$WorkerBuildSha256,
  [string]$Python = "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

function Invoke-SmokeRequest([string]$Path, [string]$Method = "GET", [string]$Body = "") {
  $arguments = @{
    Uri = "$BaseUrl$Path"
    Method = $Method
    UseBasicParsing = $true
    SkipHttpErrorCheck = $true
    TimeoutSec = 180
  }
  if (-not [string]::IsNullOrEmpty($Body)) {
    $arguments.ContentType = "application/json"
    $arguments.Body = $Body
  }
  return Invoke-WebRequest @arguments
}

function Get-SmokeJson([string]$Path) {
  $response = Invoke-SmokeRequest $Path
  if ([int]$response.StatusCode -ne 200) { throw "$Path status $($response.StatusCode)" }
  return $response.Content | ConvertFrom-Json
}

function Test-FullyQualifiedPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  try { [void][IO.Path]::GetFullPath($Path) } catch { return $false }
  return $Path -match '^[A-Za-z]:[\\/]'
}

function Resolve-ExistingPath([string]$Value, [string]$Label, [bool]$Container) {
  if (-not (Test-FullyQualifiedPath $Value)) { throw "$Label 必须是绝对路径" }
  $resolved = [IO.Path]::GetFullPath($Value)
  $pathType = if ($Container) { "Container" } else { "Leaf" }
  if (-not (Test-Path -LiteralPath $resolved -PathType $pathType)) { throw "$Label 不存在" }
  $item = Get-Item -LiteralPath $resolved -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "$Label 不得是重解析点" }
  return $resolved
}

foreach ($value in @(
  $ReleaseRoot, $D1Path, $R2Evidence, $AuditDirectory, $CutoverId,
  $MigrationRunId, $SourceDigest, $TargetDigest, $WorkerBuildSha256, $Python
)) {
  if ([string]::IsNullOrWhiteSpace($value)) { throw "客服正式 smoke 参数不完整" }
}
if ($MigrationRunId -cnotmatch "^customer-service-[0-9a-f]{32}$" -or
    $CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
    $SourceDigest -cnotmatch "^[0-9a-f]{64}$" -or
    $TargetDigest -cne $SourceDigest -or
    $WorkerBuildSha256 -cnotmatch "^[0-9a-f]{64}$") {
  throw "客服正式 smoke 身份或摘要无效"
}
$ReleaseRoot = Resolve-ExistingPath $ReleaseRoot "Worker release root" $true
$AuditDirectory = Resolve-ExistingPath $AuditDirectory "客服审计目录" $true
$D1Path = Resolve-ExistingPath $D1Path "客服 D1" $false
$R2Evidence = Resolve-ExistingPath $R2Evidence "客服 R2 证据" $false
$Python = Resolve-ExistingPath $Python "Python runtime" $false
$sourceSnapshot = Resolve-ExistingPath (Join-Path $ReleaseRoot "source-snapshot") "Worker source snapshot" $true

$reader = Invoke-WebRequest -Uri "http://127.0.0.1:8071/health/ready" -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 30
if ([int]$reader.StatusCode -ne 200) { throw "客服 Django reader 未就绪" }
$writer = Invoke-WebRequest -Uri "http://127.0.0.1:8072/health/ready" -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 30
if ([int]$writer.StatusCode -ne 200) { throw "客服 Django writer 未就绪" }

$conversations = Get-SmokeJson "/api/customer-service/conversations?page=1&pageSize=1&includeOptions=false"
if ($null -eq $conversations.items -or $null -eq $conversations.pagination -or $null -eq $conversations.summary -or @($conversations.items).Count -gt 1) { throw "客服会话公开 API 契约失败" }
$history = Get-SmokeJson "/api/customer-service/import-history?page=1&pageSize=1"
if ($null -eq $history.items -or $null -eq $history.pagination -or @($history.items).Count -gt 1) { throw "客服导入历史公开 API 契约失败" }

$negative = Invoke-SmokeRequest "/api/customer-service/conversations" "PATCH" "{}"
if ([int]$negative.StatusCode -ne 400) { throw "客服 writer 负向请求未稳定拒绝" }
$chunk = Invoke-SmokeRequest "/api/customer-service/import/chunks" "POST" '{"action":"unknown"}'
if ([int]$chunk.StatusCode -ne 400) { throw "客服分片上传负向请求未稳定拒绝" }

$search = Get-SmokeJson "/api/search?q=%E5%AE%A2%E6%9C%8D&group=customer_service&page=1&pageSize=1"
$customerGroup = @($search.groups | Where-Object key -eq "customer_service")
if ($customerGroup.Count -ne 1 -or @($customerGroup[0].items).Count -gt 1 -or @($search.unavailableDomains) -contains "customer_service") { throw "客服全局搜索 consumer 失败" }
$catalog = Get-SmokeJson "/api/ai/tools"
if (@($catalog.items | ForEach-Object name) -notcontains "get_customer_service_conversations") { throw "客服 AI 工具未进入正式 catalog" }

$d1Tool = Resolve-ExistingPath (Join-Path $sourceSnapshot "tools\customer-service-d1-rejection-smoke.py") "客服 D1 rejection smoke" $false
$d1Text = (& $Python $d1Tool --source $D1Path | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "客服旧 D1 拒绝 smoke 失败" }
$d1Result = $d1Text | ConvertFrom-Json
if ($d1Result.status -cne "passed" -or $d1Result.factsRejected -ne $true -or $d1Result.uploadsRejected -ne $true) { throw "客服旧 D1 拒绝证据无效" }

$r2 = Get-Content -Raw -LiteralPath $R2Evidence | ConvertFrom-Json
if ($r2.version -cne "customer-service-r2-retirement-evidence-v1" -or $r2.status -cne "passed" -or
    $r2.prefix -cne "inventory-upload/" -or [int64]$r2.objectCount -ne 0 -or
    [int64]$r2.objectBytes -ne 0 -or [int64]$r2.multipartUploadCount -ne 0 -or
    [int64]$r2.multipartPartCount -ne 0) { throw "客服旧 R2 拒绝证据无效" }

$productionSources = @(
  "app\api\customer-service\conversations\route.ts",
  "app\api\customer-service\import-history\route.ts",
  "app\api\customer-service\import\route.ts",
  "app\api\customer-service\import\chunks\route.ts",
  "lib\customer-service\database.ts",
  "lib\customer-service\chunked-upload.ts",
  "lib\django\customer-service.ts"
)
foreach ($relative in $productionSources) {
  $source = Join-Path $sourceSnapshot $relative
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "客服正式源码清单缺失" }
  $text = Get-Content -Raw -LiteralPath $source
  if ($text -match "SALES_IMPORT_FILES|R2Bucket|beginInventoryUpload|getCustomerServiceDatabase|ensureCustomerServiceSchema") { throw "客服正式源码仍可达旧 D1/R2 路径" }
}

$varsPath = Resolve-ExistingPath (Join-Path $ReleaseRoot ".dev.vars") "active Worker .dev.vars" $false
$needed = @(
  "TERUISI_DJANGO_INTERNAL_SECRET",
  "TERUISI_DJANGO_CUSTOMER_SERVICE_MODE",
  "TERUISI_DJANGO_CUSTOMER_SERVICE_READER_BASE_URL",
  "TERUISI_DJANGO_CUSTOMER_SERVICE_WRITER_BASE_URL",
  "TERUISI_DJANGO_CUSTOMER_SERVICE_TIMEOUT_MS",
  "TERUISI_DJANGO_CUSTOMER_SERVICE_MAX_REQUEST_BYTES",
  "TERUISI_DJANGO_CUSTOMER_SERVICE_MAX_RESPONSE_BYTES"
)
$loaded = @{}
foreach ($name in $needed) { [Environment]::SetEnvironmentVariable($name, $null, "Process") }
foreach ($line in Get-Content -LiteralPath $varsPath) {
  if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$' -and $needed -contains $matches[1]) {
    $value = $matches[2].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    $loaded[$matches[1]] = $value
    [Environment]::SetEnvironmentVariable($matches[1], $value, "Process")
  }
}
if ($loaded["TERUISI_DJANGO_CUSTOMER_SERVICE_MODE"] -cne "django" -or
    $loaded["TERUISI_DJANGO_CUSTOMER_SERVICE_READER_BASE_URL"] -cne "http://127.0.0.1:8071" -or
    $loaded["TERUISI_DJANGO_CUSTOMER_SERVICE_WRITER_BASE_URL"] -cne "http://127.0.0.1:8072" -or
    ([string]$loaded["TERUISI_DJANGO_INTERNAL_SECRET"]).Length -lt 32) {
  throw "active Worker 的客服 Django 配置无效"
}

$env:NODE_PATH = Join-Path $ReleaseRoot "node_modules"
$tsx = Resolve-ExistingPath (Join-Path $ReleaseRoot "node_modules\.bin\tsx.cmd") "tsx runtime" $false
$consumerTool = Resolve-ExistingPath (Join-Path $sourceSnapshot "tools\customer-service-consumer-smoke.ts") "客服 consumer smoke" $false
Push-Location $sourceSnapshot
try {
  $consumerText = (& $tsx $consumerTool | Out-String).Trim()
} finally {
  Pop-Location
}
if ($LASTEXITCODE -ne 0) { throw "客服 consumer smoke 进程失败" }
$consumer = $consumerText | ConvertFrom-Json
if ($consumer.status -cne "passed" -or [int]$consumer.consumerReturned -gt 1 -or [int]$consumer.aiReturned -gt 1) { throw "客服 consumer smoke 结果无效" }

$homeResponse = Invoke-SmokeRequest "/"
if ([int]$homeResponse.StatusCode -ne 200) { throw "主页在客服切换后不可用" }
$inventory = Invoke-SmokeRequest "/api/inventory/overview?page=1&pageSize=1"
$products = Invoke-SmokeRequest "/api/products/summary?range=last30&page=1&pageSize=1"
if ([int]$inventory.StatusCode -ne 200 -or [int]$products.StatusCode -ne 200) { throw "客服切换影响了其他 Django 域" }

$checks = [ordered]@{
  djangoReader = "passed"
  djangoWriterNegative = "passed"
  publicConversations = "passed"
  publicImportHistory = "passed"
  publicChunkUpload = "passed"
  globalSearchConsumer = "passed"
  aiConsumer = "passed"
  legacyD1Rejected = "passed"
  legacyR2Rejected = "passed"
  otherDomainsPreserved = "passed"
}
$receipt = [ordered]@{
  version = "customer-service-system-test-receipt-v1"
  status = "passed"
  cutoverId = $CutoverId
  migrationRunId = $MigrationRunId
  sourceDigest = $SourceDigest
  targetDigest = $TargetDigest
  workerBuildSha256 = $WorkerBuildSha256
  checks = $checks
  recordedAt = [DateTimeOffset]::UtcNow.ToString("o")
}
$receiptPath = Join-Path $AuditDirectory "customer-service-system-test-receipt.json"
if (Test-Path -LiteralPath $receiptPath) { throw "客服系统测试 receipt 已存在，拒绝覆盖" }
$temporary = Join-Path $AuditDirectory (".customer-service-system-test-receipt.{0}.tmp" -f [guid]::NewGuid().ToString("N"))
try {
  [IO.File]::WriteAllText($temporary, ($receipt | ConvertTo-Json -Compress -Depth 8) + "`n", (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $temporary -Destination $receiptPath
} finally {
  if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
}
Write-Output ([ordered]@{ status = "passed"; receipt = $receiptPath; checks = $checks } | ConvertTo-Json -Compress -Depth 8)
