[CmdletBinding()]
param(
  [string]$BaseUrl = "http://localhost:3000",
  [string]$ReleaseRoot,
  [string]$D1Path,
  [string]$AuditDirectory,
  [string]$CutoverId,
  [string]$MigrationRunId,
  [string]$SourceDigest,
  [string]$WorkerBuildSha256
)

$ErrorActionPreference = "Stop"

function Test-FullyQualifiedPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  try { [void][IO.Path]::GetFullPath($Path) } catch { return $false }
  if ([IO.Path]::DirectorySeparatorChar -eq "\") {
    return $Path -match "^[A-Za-z]:[\\/]" -or
      $Path -match "^[\\/]{2}[^\\/]+[\\/]+[^\\/]+(?:[\\/]|$)"
  }
  return $Path.StartsWith("/", [StringComparison]::Ordinal)
}

function Resolve-ExistingPath([string]$Value, [string]$Label, [bool]$Container) {
  if (-not (Test-FullyQualifiedPath $Value)) {
    throw "$Label 必须是绝对路径"
  }
  $resolved = [IO.Path]::GetFullPath($Value)
  $pathType = if ($Container) { "Container" } else { "Leaf" }
  if (-not (Test-Path -LiteralPath $resolved -PathType $pathType)) {
    throw "$Label 不存在"
  }
  $item = Get-Item -LiteralPath $resolved -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Label 不得是重解析点"
  }
  return $resolved
}

function Get-HeaderValue($Headers, [string]$Name) {
  $value = $Headers[$Name]
  if ($null -eq $value) { return "" }
  return (@($value) -join ",")
}

function Invoke-SmokeWebRequest(
  [string]$Uri,
  [string]$Method,
  [int]$TimeoutSec,
  [string]$ContentType = "",
  [string]$Body = "",
  [string]$OutFile = ""
) {
  $request = [Net.HttpWebRequest]::Create($Uri)
  $request.Method = $Method
  $request.Timeout = $TimeoutSec * 1000
  $request.ReadWriteTimeout = $TimeoutSec * 1000
  $request.AllowAutoRedirect = $false
  $request.KeepAlive = $false
  if (-not [string]::IsNullOrEmpty($ContentType)) { $request.ContentType = $ContentType }
  if (-not [string]::IsNullOrEmpty($Body)) {
    $bodyBytes = [Text.UTF8Encoding]::new($false).GetBytes($Body)
    $request.ContentLength = $bodyBytes.Length
    $requestStream = $request.GetRequestStream()
    try {
      $requestStream.Write($bodyBytes, 0, $bodyBytes.Length)
    } finally {
      $requestStream.Dispose()
    }
  }
  $response = $null
  try {
    $response = $request.GetResponse()
  } catch {
    $failure = $_.Exception
    while ($null -ne $failure -and $null -eq $response) {
      if ($null -ne $failure.PSObject.Properties["Response"]) {
        $response = $failure.Response
      }
      $failure = $failure.InnerException
    }
    if ($null -eq $response) { throw }
  }
  $stream = $null
  $reader = $null
  $output = $null
  try {
    $statusCode = [int]$response.StatusCode
    $headers = @{}
    foreach ($name in @($response.Headers.AllKeys)) {
      $headers[$name] = @($response.Headers.GetValues($name))
    }
    $stream = $response.GetResponseStream()
    $content = ""
    if (-not [string]::IsNullOrEmpty($OutFile)) {
      $output = [IO.File]::Open($OutFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
      $stream.CopyTo($output)
    } elseif ($null -ne $stream) {
      $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8, $true)
      $content = $reader.ReadToEnd()
    }
    return [pscustomobject]@{
      StatusCode = $statusCode
      Headers = $headers
      Content = $content
    }
  } finally {
    if ($null -ne $output) { $output.Dispose() }
    if ($null -ne $reader) { $reader.Dispose() }
    elseif ($null -ne $stream) { $stream.Dispose() }
    try {
      $response.Dispose()
    } catch {
      $response.Close()
    }
  }
}

function Get-SmokeJson([string]$Path, [bool]$RequireRevision = $true) {
  $response = Invoke-SmokeWebRequest -Uri "$BaseUrl$Path" -Method GET -TimeoutSec 180
  if ([int]$response.StatusCode -ne 200) {
    throw "$Path status $($response.StatusCode)"
  }
  if ((Get-HeaderValue $response.Headers "Cache-Control") -notmatch "(?i)(?:^|,)\s*no-store(?:,|$)") {
    throw "$Path 没有禁止缓存"
  }
  $revision = Get-HeaderValue $response.Headers "x-workflow-data-revision"
  if ($RequireRevision -and $revision -cnotmatch "^\d+:[0-9a-f]{12}$") {
    throw "$Path 缺少有效 workflow revision"
  }
  try {
    $data = $response.Content | ConvertFrom-Json
  } catch {
    throw "$Path 没有返回有效 JSON"
  }
  return [pscustomobject]@{
    Status = [int]$response.StatusCode
    Revision = $revision
    Data = $data
  }
}

function Assert-PagedResult($Response, [string]$Label) {
  if ($null -eq $Response.Data.items -or $null -eq $Response.Data.pagination -or
      [int64]$Response.Data.pagination.total -lt 0) {
    throw "$Label 分页契约无效"
  }
}

$ReleaseRoot = Resolve-ExistingPath $ReleaseRoot "Worker release root" $true
$D1Path = Resolve-ExistingPath $D1Path "D1 source" $false
$AuditDirectory = Resolve-ExistingPath $AuditDirectory "审计目录" $true
if ($CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
    $MigrationRunId -cnotmatch "^workflow-ops-[0-9a-f]{32}$" -or
    $SourceDigest -cnotmatch "^[0-9a-f]{64}$" -or
    $WorkerBuildSha256 -cnotmatch "^[0-9a-f]{64}$") {
  throw "正式 workflow smoke 的 cutover、迁移或摘要参数无效"
}
$baseUri = [Uri]$BaseUrl
if ($baseUri.Scheme -ne "http" -or
    $baseUri.Host -notin @("localhost", "127.0.0.1", "::1") -or
    -not [string]::IsNullOrEmpty($baseUri.Query) -or
    -not [string]::IsNullOrEmpty($baseUri.Fragment) -or
    $baseUri.AbsolutePath -ne "/") {
  throw "workflow production smoke 只允许访问本机 HTTP Worker 根地址"
}
$receiptPath = Join-Path $AuditDirectory "workflow-operations-smoke-receipt.json"
$detailsPath = Join-Path $AuditDirectory "workflow-operations-smoke-details.json"
foreach ($output in @($receiptPath, $detailsPath)) {
  if (Test-Path -LiteralPath $output) { throw "workflow smoke 审计输出已存在，拒绝覆盖" }
}

$tasks = Get-SmokeJson "/api/workflow/tasks?page=1&pageSize=100"
Assert-PagedResult $tasks "工作计划"
if ([int64]$tasks.Data.pagination.total -lt 1 -or @($tasks.Data.items).Count -lt 1) {
  throw "工作计划迁移后为空"
}
$taskTotalBefore = [int64]$tasks.Data.pagination.total
$allTasks = [Collections.Generic.List[object]]::new()
$expectedTaskPages = [Math]::Ceiling($taskTotalBefore / 100.0)
foreach ($page in 1..$expectedTaskPages) {
  $taskPage = if ($page -eq 1) { $tasks } else {
    Get-SmokeJson "/api/workflow/tasks?page=$page&pageSize=100"
  }
  Assert-PagedResult $taskPage "工作计划"
  foreach ($item in @($taskPage.Data.items)) { $allTasks.Add($item) }
}
if ($allTasks.Count -ne $taskTotalBefore) { throw "工作计划分页总数不稳定" }

$attachmentMetadata = [Collections.Generic.List[object]]::new()
$collaborationCount = 0
foreach ($task in $allTasks) {
  if ([string]::IsNullOrWhiteSpace([string]$task.id)) { throw "工作计划缺少标识" }
  $taskId = [Uri]::EscapeDataString([string]$task.id)
  $collaboration = Get-SmokeJson "/api/workflow/tasks/$taskId/collaboration"
  foreach ($field in @("comments", "activity", "reminders", "links", "attachments")) {
    if ($null -eq $collaboration.Data.$field) { throw "工作事项协作契约缺少 $field" }
  }
  foreach ($attachment in @($collaboration.Data.attachments)) {
    if ($null -ne $attachment.objectKey -or
        [string]::IsNullOrWhiteSpace([string]$attachment.id) -or
        [string]$attachment.sha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [int64]$attachment.sizeBytes -lt 0 -or
        [string]::IsNullOrWhiteSpace([string]$attachment.downloadUrl)) {
      throw "附件公开元数据泄露对象键或缺少完整性字段"
    }
    $attachmentMetadata.Add($attachment)
  }
  $collaborationCount += 1
}

$downloadRoot = Join-Path $AuditDirectory (".workflow-attachment-smoke-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $downloadRoot | Out-Null
$downloadedBytes = [int64]0
try {
  foreach ($attachment in $attachmentMetadata) {
    $downloadPath = Join-Path $downloadRoot ([Guid]::NewGuid().ToString("N") + ".bin")
    $downloadUrl = [string]$attachment.downloadUrl
    if (-not $downloadUrl.StartsWith("/api/workflow/tasks/", [StringComparison]::Ordinal)) {
      throw "附件下载地址不属于 workflow API"
    }
    $response = Invoke-SmokeWebRequest -Uri "$BaseUrl$downloadUrl" -Method GET `
      -OutFile $downloadPath -TimeoutSec 180
    if ([int]$response.StatusCode -ne 200) { throw "附件下载失败: $($response.StatusCode)" }
    $file = Get-Item -LiteralPath $downloadPath
    if ([int64]$file.Length -ne [int64]$attachment.sizeBytes -or
        (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
          ([string]$attachment.sha256).ToLowerInvariant()) {
      throw "附件 R2 字节与 PostgreSQL 元数据不一致"
    }
    $downloadedBytes += [int64]$file.Length
  }
} finally {
  $resolvedDownloadRoot = [IO.Path]::GetFullPath($downloadRoot)
  if ((Split-Path -Parent $resolvedDownloadRoot) -cne $AuditDirectory) {
    throw "附件 smoke 临时目录越界"
  }
  if (Test-Path -LiteralPath $resolvedDownloadRoot) {
    Remove-Item -LiteralPath $resolvedDownloadRoot -Recurse -Force
  }
}

$templates = Get-SmokeJson "/api/workflow/templates"
if ($null -eq $templates.Data.items) { throw "工作模板公开契约无效" }
$records = Get-SmokeJson "/api/workflow/operations-records?page=1&pageSize=1"
Assert-PagedResult $records "运营记录"
$launch = Get-SmokeJson "/api/workflow/launch-projects?page=1&pageSize=1"
Assert-PagedResult $launch "新品项目"
if ($launch.Data.structured -ne $true -or $launch.Data.backendMode -cne "django") {
  throw "新品项目子域未保持 Django 契约"
}

$invalidTask = Invoke-SmokeWebRequest -Uri "$BaseUrl/api/workflow/tasks" -Method POST `
  -ContentType "application/json" -Body '{"workflowSmokeUnknownField":true}' `
  -TimeoutSec 60
if ([int]$invalidTask.StatusCode -ne 400) { throw "工作计划 writer 负向校验失败" }
$invalidInventory = Invoke-SmokeWebRequest -Uri "$BaseUrl/api/inventory/work-items" -Method POST `
  -ContentType "application/json" -Body '{}' `
  -TimeoutSec 60
if ([int]$invalidInventory.StatusCode -ne 400) { throw "库存执行事项桥接负向校验失败" }
$tasksAfter = Get-SmokeJson "/api/workflow/tasks?page=1&pageSize=1"
Assert-PagedResult $tasksAfter "工作计划负向校验后回读"
if ([int64]$tasksAfter.Data.pagination.total -ne $taskTotalBefore) {
  throw "负向请求意外改变了工作计划"
}

$firstTitle = ([string]$allTasks[0].title).Trim()
if ($firstTitle.Length -lt 2) { throw "工作计划标题不足以执行消费链路 smoke" }
if ($firstTitle.Length -gt 80) { $firstTitle = $firstTitle.Substring(0, 80) }
$encodedQuery = [Uri]::EscapeDataString($firstTitle)
$search = Get-SmokeJson "/api/search?q=$encodedQuery&page=1&pageSize=8" $false
$workflowGroup = @($search.Data.groups | Where-Object key -eq "workflow")[0]
if ($null -eq $workflowGroup -or @($workflowGroup.items).Count -lt 1) {
  throw "全局搜索没有返回迁移后的工作计划"
}

$varsPath = Join-Path $ReleaseRoot ".dev.vars"
if (-not (Test-Path -LiteralPath $varsPath -PathType Leaf)) { throw "active Worker 缺少 .dev.vars" }
$needed = @(
  "TERUISI_DJANGO_INTERNAL_SECRET",
  "TERUISI_DJANGO_WORKFLOW_MODE",
  "TERUISI_DJANGO_WORKFLOW_READER_BASE_URL",
  "TERUISI_DJANGO_WORKFLOW_WRITER_BASE_URL",
  "TERUISI_DJANGO_WORKFLOW_TIMEOUT_MS",
  "TERUISI_DJANGO_WORKFLOW_MAX_REQUEST_BYTES",
  "TERUISI_DJANGO_WORKFLOW_MAX_RESPONSE_BYTES"
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
if ($loaded["TERUISI_DJANGO_WORKFLOW_MODE"] -cne "django" -or
    $loaded["TERUISI_DJANGO_WORKFLOW_READER_BASE_URL"] -cne "http://127.0.0.1:8061" -or
    $loaded["TERUISI_DJANGO_WORKFLOW_WRITER_BASE_URL"] -cne "http://127.0.0.1:8062" -or
    ([string]$loaded["TERUISI_DJANGO_INTERNAL_SECRET"]).Length -lt 32) {
  throw "active Worker 的 workflow 配置无效"
}

$sourceSnapshot = Resolve-ExistingPath (Join-Path $ReleaseRoot "source-snapshot") "Worker source snapshot" $true
$tsx = Resolve-ExistingPath (Join-Path $ReleaseRoot "node_modules\.bin\tsx.cmd") "tsx runtime" $false
$consumerTool = Resolve-ExistingPath (Join-Path $sourceSnapshot "tools\workflow-operations-consumer-smoke.ts") "workflow consumer smoke" $false
$env:NODE_PATH = Join-Path $ReleaseRoot "node_modules"
$env:TERUISI_WORKFLOW_SMOKE_QUERY = $firstTitle
Push-Location $sourceSnapshot
try {
  $consumerText = (& $tsx $consumerTool | Out-String).Trim()
} finally {
  Pop-Location
}
if ($LASTEXITCODE -ne 0) { throw "workflow consumer smoke process failed" }
$consumer = $consumerText | ConvertFrom-Json
if ($consumer.status -ne "passed" -or $consumer.searchTaskReturned -lt 1 -or
    $consumer.aiTaskReturned -lt 1 -or $consumer.scopedOperationReturned -ne 0 -or
    $consumer.scopedOperationMode -cne "restricted") {
  throw "workflow consumer smoke contract failed"
}

$d1Tool = Resolve-ExistingPath (Join-Path $sourceSnapshot "tools\workflow-operations-d1-rejection-smoke.py") "workflow D1 rejection smoke" $false
$d1Text = (& python $d1Tool $D1Path | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "workflow D1 rejection smoke process failed" }
$d1Rejection = $d1Text | ConvertFrom-Json
if ($d1Rejection.status -ne "passed" -or
    $d1Rejection.rejection -cne "workflow_operations_authority_not_legacy" -or
    [int64]$d1Rejection.activeTaskCount -ne $taskTotalBefore -or
    [int64]$d1Rejection.activeAttachmentCount -ne $attachmentMetadata.Count) {
  throw "legacy D1 拒写或迁移数量回读失败"
}

$sourceFiles = @(
  "$sourceSnapshot\app\api\workflow\tasks\route.ts",
  "$sourceSnapshot\app\api\workflow\templates\route.ts",
  "$sourceSnapshot\app\api\workflow\operations-records\route.ts",
  "$sourceSnapshot\lib\inventory\work-items.ts"
)
$legacyMatches = Select-String -CaseSensitive -LiteralPath $sourceFiles -Pattern @(
  "getD1Database", "workflow_task_bootstrap", "workflow_operation_records"
)
if ($legacyMatches) { throw "active workflow source still reaches legacy D1 domain tables" }

$statusText = (& "D:\运营管理系统\tools\operations-system-control.ps1" `
  -Action Status -Json | Out-String).Trim()
$system = $statusText | ConvertFrom-Json
if ($system.state -ne "Running" -or
    $system.releaseId -ne (Split-Path $ReleaseRoot -Leaf) -or
    @($system.components.psobject.Properties | Where-Object Value -ne $true).Count -ne 0) {
  throw "运营系统其他领域状态未保持完整"
}

$checks = [ordered]@{
  djangoReader = "passed"
  djangoWriterNegative = "passed"
  publicTasks = "passed"
  publicTaskCollaboration = "passed"
  publicTaskAttachmentsMetadata = "passed"
  publicTemplates = "passed"
  publicOperationRecords = "passed"
  scopedOperationRecords = "passed"
  inventoryWorkItemBridge = "passed"
  globalSearchConsumer = "passed"
  aiConsumer = "passed"
  legacyD1Rejected = "passed"
  attachmentR2Preserved = "passed"
  otherWorkflowDomainsPreserved = "passed"
}
$recordedAt = [DateTimeOffset]::UtcNow.ToString("o")
$receipt = [ordered]@{
  version = "workflow-operations-system-test-receipt-v1"
  status = "passed"
  cutoverId = $CutoverId
  migrationRunId = $MigrationRunId
  sourceDigest = $SourceDigest
  workerBuildSha256 = $WorkerBuildSha256
  checks = $checks
  recordedAt = $recordedAt
}
$details = [ordered]@{
  version = "workflow-operations-production-smoke-details-v1"
  status = "passed"
  recordedAt = $recordedAt
  releaseId = $system.releaseId
  workerBuildSha256 = $WorkerBuildSha256
  sourceDigest = $SourceDigest
  workflowRevision = $tasks.Revision
  migrated = [ordered]@{
    taskCount = $taskTotalBefore
    collaborationCount = $collaborationCount
    attachmentCount = $attachmentMetadata.Count
    attachmentBytesVerified = $downloadedBytes
    templateCount = @($templates.Data.items).Count
    operationRecordCount = [int64]$records.Data.pagination.total
    launchProjectCount = [int64]$launch.Data.pagination.total
  }
  public = [ordered]@{
    tasksStatus = $tasks.Status
    writerNegativeStatus = [int]$invalidTask.StatusCode
    inventoryBridgeNegativeStatus = [int]$invalidInventory.StatusCode
    globalSearchWorkflowReturned = @($workflowGroup.items).Count
  }
  consumers = $consumer
  legacyD1Rejection = $d1Rejection
  activeConfig = [ordered]@{
    mode = $loaded["TERUISI_DJANGO_WORKFLOW_MODE"]
    reader = $loaded["TERUISI_DJANGO_WORKFLOW_READER_BASE_URL"]
    writer = $loaded["TERUISI_DJANGO_WORKFLOW_WRITER_BASE_URL"]
    secretPresent = $true
  }
  components = $system.components
  checks = $checks
}

$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($detailsPath, ($details | ConvertTo-Json -Depth 12 -Compress) + "`n", $utf8)
[IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 8 -Compress) + "`n", $utf8)
[pscustomobject]@{
  status = "passed"
  receipt = $receiptPath
  receiptSha256 = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
  details = $detailsPath
  detailsSha256 = (Get-FileHash -LiteralPath $detailsPath -Algorithm SHA256).Hash.ToLowerInvariant()
  recordedAt = $recordedAt
} | ConvertTo-Json -Compress
