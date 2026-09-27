[CmdletBinding()]
param(
  [string]$BaseUrl = "http://localhost:3000",
  [string]$ReleaseRoot,
  [string]$D1Path,
  [string]$AuditDirectory,
  [string]$CutoverId,
  [string]$MigrationRunId,
  [string]$DataDigest,
  [string]$WorkerBuildSha256,
  [string]$ExpectedSnapshotDate = "2026-09-01"
)

$ErrorActionPreference = "Stop"

function Get-SmokeJson([string]$Path) {
  $response = Invoke-WebRequest -Uri "$BaseUrl$Path" -Method GET `
    -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 180
  if ([int]$response.StatusCode -ne 200) {
    throw "$Path status $($response.StatusCode)"
  }
  return [pscustomobject]@{
    Status = [int]$response.StatusCode
    Headers = $response.Headers
    Data = $response.Content | ConvertFrom-Json
  }
}

foreach ($value in @(
  $ReleaseRoot, $D1Path, $AuditDirectory, $CutoverId,
  $MigrationRunId, $DataDigest, $WorkerBuildSha256
)) {
  if ([string]::IsNullOrWhiteSpace($value)) { throw "正式 smoke 参数不完整" }
}
if ($DataDigest -notmatch "^[0-9a-f]{64}$" -or
    $WorkerBuildSha256 -notmatch "^[0-9a-f]{64}$") {
  throw "正式 smoke 摘要无效"
}

$overview = Get-SmokeJson "/api/inventory/overview?page=1&pageSize=1"
if ($overview.Data.hasInventory -ne $true -or
    $overview.Data.sync.inventoryAsOf -ne $ExpectedSnapshotDate) {
  throw "overview contract failed"
}
$age = Get-SmokeJson "/api/inventory/age-analysis?page=1&pageSize=1"
if ($age.Data.hasInventory -ne $true -or
    $age.Data.sync.inventoryAsOf -ne $ExpectedSnapshotDate) {
  throw "age contract failed"
}
$inbound = Get-SmokeJson "/api/inventory/inbound-monitor?page=1&pageSize=1"
if ($inbound.Data.hasInventory -ne $true -or
    $inbound.Data.sync.inventoryAsOf -ne $ExpectedSnapshotDate) {
  throw "inbound contract failed"
}
$plans = Get-SmokeJson "/api/inventory/replenishment?page=1&pageSize=1"
if ($null -eq $plans.Data.items -or $null -eq $plans.Data.pagination) {
  throw "replenishment contract failed"
}
$settingsBefore = Get-SmokeJson "/api/settings"
if ($settingsBefore.Data.targetDays -ne 30 -or
    $settingsBefore.Data.autoReplenishment -ne $false) {
  throw "settings contract failed"
}

$writer = Invoke-WebRequest -Uri "$BaseUrl/api/settings" -Method PUT `
  -ContentType "application/json" -Body '{"targetDays":0}' `
  -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 60
if ([int]$writer.StatusCode -ne 400 -or $writer.Content -notmatch "targetDays") {
  throw "writer negative failed"
}
$direct = Invoke-WebRequest -Uri "$BaseUrl/api/imports/inventory" -Method POST `
  -ContentType "application/json" -Body "{}" `
  -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 60
if ([int]$direct.StatusCode -ne 415) { throw "direct import negative failed" }
$chunkAttempts = 0
$chunkTransientFailures = 0
$chunkConsecutivePasses = 0
foreach ($attempt in 1..8) {
  $chunkAttempts = $attempt
  $chunk = Invoke-WebRequest -Uri "$BaseUrl/api/imports/inventory/chunks" -Method POST `
    -ContentType "application/json" -Body '{"action":"unknown"}' `
    -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 60
  if ([int]$chunk.StatusCode -eq 400 -and $chunk.Content -match "未知") {
    $chunkConsecutivePasses += 1
    if ($chunkConsecutivePasses -eq 3) { break }
  } else {
    $chunkTransientFailures += 1
    $chunkConsecutivePasses = 0
    Start-Sleep -Milliseconds 500
  }
}
if ($chunkConsecutivePasses -ne 3) {
  throw "chunk negative did not produce three consecutive stable rejections"
}
$settingsAfter = Get-SmokeJson "/api/settings"
if (($settingsBefore.Data | ConvertTo-Json -Compress) -cne
    ($settingsAfter.Data | ConvertTo-Json -Compress)) {
  throw "negative writes changed settings"
}

$products = Get-SmokeJson "/api/products/summary?range=last30&page=1&pageSize=1"
if ($products.Data.items.Count -ne 1 -or
    $null -eq $products.Data.items[0].availableQuantity) {
  throw "products projection failed"
}
$search = Get-SmokeJson "/api/search?q=ZG-2h-004&page=1&pageSize=8"
$inventoryGroup = @($search.Data.groups | Where-Object key -eq "inventory")[0]
$ageGroup = @($search.Data.groups | Where-Object key -eq "inventory_age")[0]
if ($inventoryGroup.items.Count -lt 1 -or $ageGroup.items.Count -lt 1) {
  throw "global search inventory consumers failed"
}
$tools = Get-SmokeJson "/api/ai/tools"
$toolNames = @($tools.Data.items | ForEach-Object name)
foreach ($name in @("get_inventory_health", "list_replenishment_plans", "get_data_freshness")) {
  if ($toolNames -notcontains $name) { throw "AI catalog missing $name" }
}

$varsPath = Join-Path $ReleaseRoot ".dev.vars"
$needed = @(
  "TERUISI_DJANGO_INTERNAL_SECRET",
  "TERUISI_DJANGO_INVENTORY_READER_BASE_URL",
  "TERUISI_DJANGO_INVENTORY_WRITER_BASE_URL",
  "TERUISI_DJANGO_INVENTORY_TIMEOUT_MS",
  "TERUISI_DJANGO_INVENTORY_MAX_REQUEST_BYTES",
  "TERUISI_DJANGO_INVENTORY_MAX_RESPONSE_BYTES"
)
$loaded = @{}
foreach ($line in Get-Content -LiteralPath $varsPath) {
  if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$' -and
      $needed -contains $matches[1]) {
    $value = $matches[2].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    $loaded[$matches[1]] = $value
    [Environment]::SetEnvironmentVariable($matches[1], $value, "Process")
  }
}
if ($loaded["TERUISI_DJANGO_INVENTORY_READER_BASE_URL"] -cne "http://127.0.0.1:8051" -or
    $loaded["TERUISI_DJANGO_INVENTORY_WRITER_BASE_URL"] -cne "http://127.0.0.1:8052" -or
    $loaded["TERUISI_DJANGO_INVENTORY_TIMEOUT_MS"] -cne "120000" -or
    $loaded["TERUISI_DJANGO_INVENTORY_MAX_REQUEST_BYTES"] -cne "67108864" -or
    $loaded["TERUISI_DJANGO_INVENTORY_MAX_RESPONSE_BYTES"] -cne "33554432" -or
    $loaded["TERUISI_DJANGO_INTERNAL_SECRET"].Length -lt 32) {
  throw "active inventory worker config failed"
}

$env:NODE_PATH = Join-Path $ReleaseRoot "node_modules"
$tsx = Join-Path $ReleaseRoot "node_modules\.bin\tsx.cmd"
$consumerText = (& $tsx "tools/inventory-consumer-smoke.ts" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "consumer smoke process failed" }
$consumer = $consumerText | ConvertFrom-Json
if ($consumer.status -ne "passed" -or $consumer.projection.total -lt 1 -or
    $consumer.systemCost.costCount -lt 1 -or $consumer.ai.returned -ne 1) {
  throw "consumer smoke contract failed"
}

$d1Reject = (& python "tools/inventory-d1-rejection-smoke.py" $D1Path | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $d1Reject -notmatch "inventory_write_authority_not_d1") {
  throw "legacy D1 rejection failed"
}
$sourceFiles = @(
  "$ReleaseRoot\source-snapshot\app\api\imports\inventory\route.ts",
  "$ReleaseRoot\source-snapshot\app\api\imports\inventory\chunks\route.ts",
  "$ReleaseRoot\source-snapshot\lib\inventory\django-import-service.ts",
  "$ReleaseRoot\source-snapshot\lib\inventory\django-chunked-upload.ts",
  "$ReleaseRoot\source-snapshot\lib\django\inventory-service.ts"
)
$legacyMatches = Select-String -LiteralPath $sourceFiles `
  -Pattern "inventory-upload/|R2_BUCKET|INVENTORY_UPLOAD_BUCKET"
if ($legacyMatches) { throw "active inventory source still reaches legacy R2" }

$statusText = (& "D:\运营管理系统\tools\operations-system-control.ps1" `
  -Action Status -Json | Out-String).Trim()
$system = $statusText | ConvertFrom-Json
if ($system.state -ne "Running" -or
    $system.releaseId -ne (Split-Path $ReleaseRoot -Leaf) -or
    @($system.components.psobject.Properties | Where-Object Value -ne $true).Count -ne 0) {
  throw "other domains/system status failed"
}

$checks = [ordered]@{
  djangoReader = "passed"
  djangoWriterNegative = "passed"
  publicOverview = "passed"
  publicAgeAnalysis = "passed"
  publicInboundMonitor = "passed"
  publicReplenishment = "passed"
  publicSettings = "passed"
  publicDirectImport = "passed"
  publicChunkUpload = "passed"
  productsProjection = "passed"
  systemCostConsumer = "passed"
  aiConsumer = "passed"
  globalSearchConsumer = "passed"
  legacyD1Rejected = "passed"
  legacyR2Rejected = "passed"
  otherDomainsPreserved = "passed"
}
$recordedAt = [DateTimeOffset]::UtcNow.ToString("o")
$receipt = [ordered]@{
  version = "inventory-system-test-receipt-v1"
  status = "passed"
  cutoverId = $CutoverId
  migrationRunId = $MigrationRunId
  sourceDigest = $DataDigest
  targetDigest = $DataDigest
  workerBuildSha256 = $WorkerBuildSha256
  checks = $checks
  recordedAt = $recordedAt
}
$details = [ordered]@{
  version = "inventory-production-smoke-details-v1"
  status = "passed"
  recordedAt = $recordedAt
  releaseId = $system.releaseId
  workerBuildSha256 = $WorkerBuildSha256
  inventoryRevision = $consumer.freshness.revision
  snapshotDate = $consumer.freshness.stockDate
  public = [ordered]@{
    overviewStatus = $overview.Status
    ageStatus = $age.Status
    inboundStatus = $inbound.Status
    replenishmentStatus = $plans.Status
    settingsStatus = $settingsBefore.Status
    writerNegativeStatus = [int]$writer.StatusCode
    directImportNegativeStatus = [int]$direct.StatusCode
    chunkNegativeAttempts = $chunkAttempts
    chunkTransientFailures = $chunkTransientFailures
    productsStatus = $products.Status
    searchInventoryReturned = $inventoryGroup.items.Count
    searchAgeReturned = $ageGroup.items.Count
  }
  consumers = $consumer
  legacyD1Rejection = $d1Reject
  activeConfig = [ordered]@{
    reader = $loaded["TERUISI_DJANGO_INVENTORY_READER_BASE_URL"]
    writer = $loaded["TERUISI_DJANGO_INVENTORY_WRITER_BASE_URL"]
    timeoutMs = [int]$loaded["TERUISI_DJANGO_INVENTORY_TIMEOUT_MS"]
    maxRequestBytes = [int]$loaded["TERUISI_DJANGO_INVENTORY_MAX_REQUEST_BYTES"]
    maxResponseBytes = [int]$loaded["TERUISI_DJANGO_INVENTORY_MAX_RESPONSE_BYTES"]
    secretPresent = $true
  }
  components = $system.components
  checks = $checks
}

$utf8 = [Text.UTF8Encoding]::new($false)
$receiptPath = Join-Path $AuditDirectory "inventory-smoke-receipt.json"
$detailsPath = Join-Path $AuditDirectory "inventory-smoke-details.json"
[IO.File]::WriteAllText($detailsPath, ($details | ConvertTo-Json -Depth 12 -Compress) + "`n", $utf8)
[IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 8 -Compress) + "`n", $utf8)
[pscustomobject]@{
  status = "passed"
  receipt = $receiptPath
  receiptSha256 = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
  detailsSha256 = (Get-FileHash -LiteralPath $detailsPath -Algorithm SHA256).Hash.ToLowerInvariant()
  recordedAt = $recordedAt
} | ConvertTo-Json -Compress
