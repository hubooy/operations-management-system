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
  if ([string]::IsNullOrWhiteSpace($value)) { throw "ERP 主数据正式 smoke 参数不完整" }
}
if ($MigrationRunId -cnotmatch "^erp-reference-[0-9a-f]{32}$" -or
    $CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
    $SourceDigest -cnotmatch "^[0-9a-f]{64}$" -or
    $TargetDigest -cne $SourceDigest -or
    $WorkerBuildSha256 -cnotmatch "^[0-9a-f]{64}$") {
  throw "ERP 主数据正式 smoke 身份或摘要无效"
}
$ReleaseRoot = Resolve-ExistingPath $ReleaseRoot "Worker release root" $true
$AuditDirectory = Resolve-ExistingPath $AuditDirectory "ERP 主数据审计目录" $true
$D1Path = Resolve-ExistingPath $D1Path "ERP 主数据 D1" $false
$R2Evidence = Resolve-ExistingPath $R2Evidence "ERP 主数据 R2 证据" $false
$Python = Resolve-ExistingPath $Python "Python runtime" $false
$sourceSnapshot = Resolve-ExistingPath (Join-Path $ReleaseRoot "source-snapshot") "Worker source snapshot" $true

$reader = Invoke-WebRequest -Uri "http://127.0.0.1:8091/health/ready" -UseBasicParsing -TimeoutSec 30
if ([int]$reader.StatusCode -ne 200) { throw "ERP 主数据 Django reader 未就绪" }
$writer = Invoke-WebRequest -Uri "http://127.0.0.1:8092/health/ready" -UseBasicParsing -TimeoutSec 30
if ([int]$writer.StatusCode -ne 200) { throw "ERP 主数据 Django writer 未就绪" }

$history = Get-SmokeJson "/api/imports/erp?source=products&page=1&pageSize=1"
if ($null -eq $history.items -or $null -eq $history.pagination -or @($history.items).Count -gt 1) { throw "ERP 主数据货品导入历史公开 API 契约失败" }
$comboHistory = Get-SmokeJson "/api/imports/erp?source=combos&page=1&pageSize=1"
if ($null -eq $comboHistory.items -or $null -eq $comboHistory.pagination -or @($comboHistory.items).Count -gt 1) { throw "ERP 主数据组合装导入历史公开 API 契约失败" }

$chunk = Invoke-SmokeRequest "/api/imports/erp/chunks" "POST" '{"source":"products","action":"unknown"}'
if ([int]$chunk.StatusCode -ne 400) { throw "ERP 主数据分片上传负向请求未稳定拒绝" }
$negative = Invoke-SmokeRequest "/api/imports/erp" "POST" "{}"
if ([int]$negative.StatusCode -ne 415) { throw "ERP 主数据直传 writer 负向请求未稳定拒绝" }

$search = Get-SmokeJson "/api/search?q=ERP&group=products&page=1&pageSize=1"
$erpGroup = @($search.groups | Where-Object key -eq "products")
if ($erpGroup.Count -ne 1 -or @($erpGroup[0].items).Count -gt 1 -or @($search.unavailableDomains) -contains "products") { throw "ERP 主数据全局搜索 consumer 失败" }
$catalog = Get-SmokeJson "/api/ai/tools"
if (@($catalog.items | ForEach-Object name) -notcontains "get_import_status") { throw "ERP 主数据 AI 工具未进入正式 catalog" }

$d1Tool = Resolve-ExistingPath (Join-Path $sourceSnapshot "tools\erp-reference-d1-rejection-smoke.py") "ERP 主数据 D1 rejection smoke" $false
$d1Text = (& $Python $d1Tool --source $D1Path | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "ERP 主数据旧 D1 拒绝 smoke 失败" }
$d1Result = $d1Text | ConvertFrom-Json
if ($d1Result.status -cne "passed" -or $d1Result.factsRejected -ne $true -or $d1Result.uploadsRejected -ne $true) { throw "ERP 主数据旧 D1 拒绝证据无效" }

$r2 = Get-Content -Raw -LiteralPath $R2Evidence | ConvertFrom-Json
if ($r2.version -cne "erp-reference-r2-retirement-evidence-v1" -or $r2.status -cne "passed" -or
    $r2.prefix -cne "inventory-upload/" -or [int64]$r2.objectCount -ne 0 -or
    [int64]$r2.objectBytes -ne 0 -or [int64]$r2.multipartUploadCount -ne 0 -or
    [int64]$r2.multipartPartCount -ne 0) { throw "ERP 主数据旧 R2 拒绝证据无效" }

$productionSources = @(
  "app\api\imports\erp\route.ts",
  "app\api\imports\erp\chunks\route.ts",
  "lib\erp-reference\django-import-service.ts",
  "lib\erp-reference\django-chunked-upload.ts",
  "lib\django\erp-reference-service.ts",
  "lib\django\erp-reference-consumer-reader.ts",
  "lib\search\global-search.ts",
  "lib\ai\page-data-tools.ts"
)
foreach ($relative in $productionSources) {
  $source = Join-Path $sourceSnapshot $relative
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "ERP 主数据正式源码清单缺失" }
  $text = Get-Content -Raw -LiteralPath $source
  if ($text -match "@/lib/erp-reference/(database|import-service|projection-outbox)|getErpReferenceDatabase|beginInventoryUpload|finishInventoryUpload") { throw "ERP 主数据正式源码仍可达旧 D1/R2 路径" }
}

$varsPath = Resolve-ExistingPath (Join-Path $ReleaseRoot ".dev.vars") "active Worker .dev.vars" $false
$needed = @(
  "TERUISI_DJANGO_INTERNAL_SECRET",
  "TERUISI_DJANGO_ERP_MODE",
  "TERUISI_DJANGO_ERP_READER_BASE_URL",
  "TERUISI_DJANGO_ERP_WRITER_BASE_URL",
  "TERUISI_DJANGO_ERP_TIMEOUT_MS",
  "TERUISI_DJANGO_ERP_MAX_REQUEST_BYTES",
  "TERUISI_DJANGO_ERP_MAX_RESPONSE_BYTES"
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
if ($loaded["TERUISI_DJANGO_ERP_MODE"] -cne "django" -or
    $loaded["TERUISI_DJANGO_ERP_READER_BASE_URL"] -cne "http://127.0.0.1:8091" -or
    $loaded["TERUISI_DJANGO_ERP_WRITER_BASE_URL"] -cne "http://127.0.0.1:8092" -or
    ([string]$loaded["TERUISI_DJANGO_INTERNAL_SECRET"]).Length -lt 32) {
  throw "active Worker 的ERP 主数据 Django 配置无效"
}

$env:NODE_PATH = Join-Path $ReleaseRoot "node_modules"
$tsx = Resolve-ExistingPath (Join-Path $ReleaseRoot "node_modules\.bin\tsx.cmd") "tsx runtime" $false
$consumerTool = Resolve-ExistingPath (Join-Path $sourceSnapshot "tools\erp-reference-consumer-smoke.ts") "ERP 主数据 consumer smoke" $false
Push-Location $sourceSnapshot
try {
  $consumerText = (& $tsx $consumerTool | Out-String).Trim()
} finally {
  Pop-Location
}
if ($LASTEXITCODE -ne 0) { throw "ERP 主数据 consumer smoke 进程失败" }
$consumer = $consumerText | ConvertFrom-Json
if ($consumer.status -cne "passed" -or [int]$consumer.consumerReturned -gt 1 -or [int]$consumer.aiReturned -gt 1) { throw "ERP 主数据 consumer smoke 结果无效" }

$homeResponse = Invoke-SmokeRequest "/"
if ([int]$homeResponse.StatusCode -ne 200) { throw "主页在ERP 主数据切换后不可用" }
$inventory = Invoke-SmokeRequest "/api/inventory/overview?page=1&pageSize=1"
$products = Invoke-SmokeRequest "/api/products/summary?range=last30&page=1&pageSize=1"
if ([int]$inventory.StatusCode -ne 200 -or [int]$products.StatusCode -ne 200) { throw "ERP 主数据切换影响了其他 Django 域" }

$checks = [ordered]@{
  djangoReader = "passed"
  djangoWriterNegative = "passed"
  publicImportHistory = "passed"
  publicDirectImport = "passed"
  publicChunkUpload = "passed"
  globalSearchConsumer = "passed"
  aiConsumer = "passed"
  legacyD1Rejected = "passed"
  legacyR2Rejected = "passed"
  otherDomainsPreserved = "passed"
}
$receipt = [ordered]@{
  version = "erp-reference-system-test-receipt-v1"
  status = "passed"
  cutoverId = $CutoverId
  migrationRunId = $MigrationRunId
  sourceDigest = $SourceDigest
  targetDigest = $TargetDigest
  workerBuildSha256 = $WorkerBuildSha256
  checks = $checks
  recordedAt = [DateTimeOffset]::UtcNow.ToString("o")
}
$receiptPath = Join-Path $AuditDirectory "erp-reference-system-test-receipt.json"
if (Test-Path -LiteralPath $receiptPath) { throw "ERP 主数据系统测试 receipt 已存在，拒绝覆盖" }
$temporary = Join-Path $AuditDirectory (".erp-reference-system-test-receipt.{0}.tmp" -f [guid]::NewGuid().ToString("N"))
try {
  [IO.File]::WriteAllText($temporary, ($receipt | ConvertTo-Json -Compress -Depth 8) + "`n", (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $temporary -Destination $receiptPath
} finally {
  if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
}
Write-Output ([ordered]@{ status = "passed"; receipt = $receiptPath; checks = $checks } | ConvertTo-Json -Compress -Depth 8)
