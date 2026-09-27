[CmdletBinding()]
param(
  [ValidateSet("Plan", "Execute")]
  [string]$Action = "Plan",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [Parameter(Mandatory = $true)]
  [string]$CutoverId,
  [string]$ApprovedR2CleanupManifestId = "",
  [string]$ApprovedBackupManifestSha256 = "",
  [string]$RehearsalResultPath = "",
  [string]$ApprovedRehearsalResultSha256 = "",
  [Parameter(Mandatory = $true)]
  [string]$WorkerReleaseManifestPath,
  [string]$ApprovedWorkerReleaseManifestSha256 = "",
  [switch]$Execute,
  [switch]$ConfirmedMaintenance
)

$ErrorActionPreference = "Stop"

$result = & {
  param(
    [string]$RequestedAction,
    [string]$ServiceRuntime,
    [string]$RequestedCutoverId,
    [string]$RequestedR2Approval,
    [string]$RequestedBackupApproval,
    [string]$RequestedRehearsalResultPath,
    [string]$RequestedRehearsalApproval,
    [string]$RequestedWorkerReleaseManifestPath,
    [string]$RequestedWorkerReleaseApproval,
    [bool]$ExecuteRequested,
    [bool]$MaintenanceConfirmed
  )

  $serviceScript = Join-Path $ServiceRuntime "app\tools\django-local-service.ps1"
  $expectedSelf = Join-Path $ServiceRuntime "app\tools\sales-local-cutover-operator.ps1"
  if (-not (Test-Path -LiteralPath $serviceScript -PathType Leaf) -or
      [IO.Path]::GetFullPath($PSCommandPath).TrimEnd("\", "/") -ine
      [IO.Path]::GetFullPath($expectedSelf).TrimEnd("\", "/")) {
    throw "销售切换 operator 只能从受保护的 runtime app 执行"
  }
  $previousLibraryOnly = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
  )
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  try {
    . $serviceScript -Action Status -RuntimeRoot $ServiceRuntime
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
    )
  }

  return Invoke-WithServiceMutex {

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
      $line = Protect-LogText ([string]($records[$index]))
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
    [string]$WorkingDirectory
  ) {
    $output = @()
    $nativeExitCode = $null
    $launchFailed = $false
    if ([string]::IsNullOrWhiteSpace($Executable) -or
        -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
      $output = @("native executable unavailable")
      $launchFailed = $true
    } elseif ([string]::IsNullOrWhiteSpace($WorkingDirectory) -or
        -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
      $output = @("native working directory unavailable")
      $launchFailed = $true
    } else {
      $outerErrorActionPreference = $ErrorActionPreference
      $outerLastExitCode = $global:LASTEXITCODE
      $locationPushed = $false
      try {
        Push-Location -LiteralPath $WorkingDirectory
        $locationPushed = $true
        # Windows PowerShell 5 promotes native stderr to ErrorRecord.  Capture
        # it under Continue, then decide success only from the exact exit code.
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

  function ConvertFrom-NativeJsonRun([object]$Run, [string]$Label) {
    if ($Run.ExitCode -ne 0) {
      throw "$Label 失败（$(Get-NativeFailureSummary $Run)）"
    }
    $jsonResults = @()
    foreach ($line in @($Run.Output)) {
      try {
        $jsonResults += ([string]$line | ConvertFrom-Json)
      } catch {
        # A successful native process may emit a benign warning on stderr.
      }
    }
    if ($jsonResults.Count -ne 1) {
      throw "$Label 未返回唯一 JSON 结果（$(Get-NativeFailureSummary $Run)）"
    }
    return $jsonResults[0]
  }

  if ($RequestedCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
    throw "cutoverId 必须是 8 到 128 位安全标识"
  }
  Assert-DeployedApplication
  Assert-WranglerLocalR2RoundTrip $InstalledAppRoot
  Assert-RuntimeAclHardened
  Assert-ApplicationProcessesStopped "SalesCutover"
  foreach ($port in @(3000, 5791)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "销售切换要求 Worker/工作流辅助端口 $port 已停止"
    }
  }
  foreach ($port in @(8001, 8002)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "销售切换要求 Django 端口 $port 已停止"
    }
  }

  $config = Get-ServiceConfig
  $source = Resolve-ErpSourceD1 ([string]$config.erpSourceD1)
  $sourceItem = Get-Item -LiteralPath $source -Force
  if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "权威 D1 不得是重解析点"
  }
  $cursor = $sourceItem.Directory
  $d1Root = $null
  while ($null -ne $cursor) {
    if ($cursor.Name -ceq "d1" -and $null -ne $cursor.Parent -and $cursor.Parent.Name -ceq "v3") {
      $d1Root = $cursor
      break
    }
    $cursor = $cursor.Parent
  }
  if ($null -eq $d1Root -or $null -eq $d1Root.Parent.Parent) {
    throw "权威 D1 不在固定 Wrangler persist/v3/d1 根内"
  }
  $persistRoot = Get-CanonicalPath $d1Root.Parent.Parent.FullName
  $r2Root = Join-Path $persistRoot "v3\r2"
  if (-not (Test-Path -LiteralPath $r2Root -PathType Container)) {
    throw "固定 Wrangler R2 persist 根不存在"
  }
  foreach ($item in @((Get-Item -LiteralPath $r2Root -Force)) + @(
    Get-ChildItem -LiteralPath $r2Root -Force -Recurse
  )) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "Wrangler R2 persist 不得包含重解析点"
    }
  }

  $cutoverTool = Join-Path $InstalledAppRoot "tools\sales-local-cutover.ts"
  $r2Tool = Join-Path $InstalledAppRoot "tools\sales-legacy-r2-cleanup.ts"
  $snapshotGateTool = Join-Path $InstalledAppRoot "tools\sales-cutover-snapshot-gate.py"
  $workerReleaseTool = Join-Path $InstalledAppRoot "tools\worker-local-release.mjs"
  $protectedWorkerSourceRoot = "D:\运营管理系统"
  $wranglerCli = Join-Path $InstalledAppRoot "runtime-tools\node_modules\wrangler\wrangler-dist\cli.js"
  $pgRestore = Join-Path $PostgresBin "pg_restore.exe"
  foreach ($required in @(
    $cutoverTool,
    $r2Tool,
    $snapshotGateTool,
    $workerReleaseTool,
    $wranglerCli,
    (Join-Path $InstalledAppRoot "drizzle\0090_sales_write_authority.sql"),
    (Join-Path $InstalledAppRoot "drizzle\0091_erp_reference_projection.sql"),
    (Join-Path $BackendRoot "manage.py"),
    $Python,
    $Node,
    $pgRestore
  )) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "受保护 runtime 缺少销售切换依赖"
    }
  }
  if (-not (Test-Path -LiteralPath $protectedWorkerSourceRoot -PathType Container)) {
    throw "受保护的主 Worker 源码根不存在"
  }
  $nodeVersionRun = Invoke-BoundedNativeProcess $Node @("--version") $InstalledAppRoot
  $nodeVersion = @($nodeVersionRun.Output)
  if ($nodeVersionRun.ExitCode -ne 0 -or $nodeVersion.Count -ne 1 -or
      [string]$nodeVersion[0] -cnotmatch "^v24\.[0-9]+\.[0-9]+$") {
    throw (
      "销售切换必须使用固定 Node.js 24 原生 TypeScript runtime（" +
      "$(Get-NativeFailureSummary $nodeVersionRun)）"
    )
  }

  function Assert-ExactPropertySet([object]$Value, [string[]]$Expected, [string]$Label) {
    if ($null -eq $Value) { throw "$Label 缺失" }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if ($actual.Count -ne $wanted.Count) { throw "$Label 字段集合无效" }
    for ($index = 0; $index -lt $wanted.Count; $index++) {
      if ([string]$actual[$index] -cne [string]$wanted[$index]) {
        throw "$Label 字段集合无效"
      }
    }
  }

  function Get-WorkerCanonicalPathSha256([string]$Path, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-FullyQualifiedPath $Path)) {
      throw "$Label 必须是带盘符的绝对路径"
    }
    $normalized = [IO.Path]::GetFullPath($Path).Replace("/", "\")
    if ($normalized.Length -gt 3) { $normalized = $normalized.TrimEnd([char]92) }
    return Get-Sha256Text $normalized.ToUpperInvariant()
  }

  if ([string]::IsNullOrWhiteSpace($RequestedWorkerReleaseManifestPath) -or
      -not (Test-FullyQualifiedPath $RequestedWorkerReleaseManifestPath)) {
    throw "必须提供不可变 Worker release manifest 绝对路径"
  }
  $workerReleaseManifestPath = Get-CanonicalPath $RequestedWorkerReleaseManifestPath
  if (-not (Test-Path -LiteralPath $workerReleaseManifestPath -PathType Leaf)) {
    throw "Worker release manifest 不存在"
  }
  $workerReleaseManifestSha256 = Get-FileSha256 $workerReleaseManifestPath
  $sourceD1PathSha256 = Get-WorkerCanonicalPathSha256 $source "权威 D1 路径"
  $persistRootPathSha256 = Get-WorkerCanonicalPathSha256 $persistRoot "Wrangler persist 根"
  $protectedWorkerSourceRootPathSha256 = Get-WorkerCanonicalPathSha256 `
    $protectedWorkerSourceRoot "受保护的主 Worker 源码根"
  $djangoDeploymentManifestSha256 = Get-FileSha256 $DeploymentManifestPath

  $cutoverDigest = Get-Sha256Text $RequestedCutoverId
  $backupDirectory = Assert-RuntimeChildPath (
    Join-Path $ServiceRuntime "backups\sales-cutover-$($cutoverDigest.Substring(0, 24))"
  )
  $backupManifestPath = Join-Path $backupDirectory "backup-manifest.json"
  $backupManifest = Read-JsonFile $backupManifestPath "销售切换备份清单"
  $backupManifestSha256 = Get-FileSha256 $backupManifestPath
  Assert-ExactPropertySet $backupManifest @(
    "version", "cutoverId", "createdAt", "sourcePathSha256", "sourceD1",
    "r2State", "postgresql", "deploymentManifestSha256", "serviceConfigSha256"
  ) "销售切换备份清单"
  Assert-ExactPropertySet $backupManifest.sourceD1 @(
    "status", "version", "destinationName", "sizeBytes", "sha256", "quickCheck",
    "counts", "revisions"
  ) "D1 备份证据"
  Assert-ExactPropertySet $backupManifest.r2State @(
    "manifestSha256", "fileCount", "sizeBytes"
  ) "R2 备份证据"
  Assert-ExactPropertySet $backupManifest.postgresql @(
    "fileName", "sizeBytes", "sha256", "archiveEntryCount", "evidence"
  ) "PostgreSQL 备份证据"
  $backupCreatedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse(
      [string]$backupManifest.createdAt,
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::RoundtripKind,
      [ref]$backupCreatedAt
    )) {
    throw "销售切换备份 createdAt 无效"
  }
  $backupAge = [DateTimeOffset]::UtcNow - $backupCreatedAt.ToUniversalTime()
  $backupAgeExceeded = $backupAge -gt [TimeSpan]::FromMinutes(360)
  if ($backupAge -lt [TimeSpan]::FromMinutes(-5)) {
    throw "销售切换备份时间在未来"
  }
  $backupShaFile = Join-Path $backupDirectory "backup-manifest.json.sha256"
  $recordedBackupSha = if (Test-Path -LiteralPath $backupShaFile -PathType Leaf) {
    [IO.File]::ReadAllText($backupShaFile, [Text.Encoding]::UTF8).Trim()
  } else { "" }
  if ([string]$backupManifest.version -cne "teruisi-sales-cutover-backup-v1" -or
      [string]$backupManifest.cutoverId -cne $RequestedCutoverId -or
      [string]$backupManifest.sourcePathSha256 -cne (Get-Sha256Text $source) -or
      [string]$backupManifest.deploymentManifestSha256 -cne (Get-FileSha256 $DeploymentManifestPath) -or
      [string]$backupManifest.serviceConfigSha256 -cne (Get-FileSha256 $ConfigPath) -or
      [string]$backupManifest.sourceD1.status -cne "completed" -or
      [string]$backupManifest.sourceD1.version -cne "teruisi-sqlite-backup-v1" -or
      [string]$backupManifest.sourceD1.quickCheck -cne "ok" -or
      $backupManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      $recordedBackupSha -cne $backupManifestSha256) {
    throw "销售切换备份清单身份或证据无效"
  }
  $d1BackupPath = Join-Path $backupDirectory "source-d1.sqlite"
  $pgBackupPath = Join-Path $backupDirectory "teruisi-sales.dump"
  $r2ManifestPath = Join-Path $backupDirectory "r2-manifest.json"
  $r2BackupRoot = Join-Path $backupDirectory "r2-state"
  foreach ($artifact in @($d1BackupPath, $pgBackupPath, $r2ManifestPath)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf) -or
        ((Get-Item -LiteralPath $artifact -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "销售切换备份缺少普通文件 artifact"
    }
  }
  if (-not (Test-Path -LiteralPath $r2BackupRoot -PathType Container)) {
    throw "销售切换备份缺少 R2 状态目录"
  }
  foreach ($item in @((Get-Item -LiteralPath $r2BackupRoot -Force)) + @(
    Get-ChildItem -LiteralPath $r2BackupRoot -Force -Recurse
  )) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "R2 备份状态目录不得包含重解析点"
    }
  }
  if ([string]$backupManifest.sourceD1.destinationName -cne "source-d1.sqlite" -or
      [int64]$backupManifest.sourceD1.sizeBytes -ne [int64](Get-Item -LiteralPath $d1BackupPath).Length -or
      [string]$backupManifest.sourceD1.sha256 -cne (Get-FileSha256 $d1BackupPath) -or
      [string]$backupManifest.postgresql.fileName -cne "teruisi-sales.dump" -or
      [int64]$backupManifest.postgresql.sizeBytes -ne [int64](Get-Item -LiteralPath $pgBackupPath).Length -or
      [string]$backupManifest.postgresql.sha256 -cne (Get-FileSha256 $pgBackupPath)) {
    throw "D1 或 PostgreSQL 备份 artifact 摘要不一致"
  }
  $archiveListRun = Invoke-BoundedNativeProcess $pgRestore @(
    "--list", $pgBackupPath
  ) $ServiceRuntime
  $archiveList = @($archiveListRun.Output)
  if ($archiveListRun.ExitCode -ne 0 -or
      $archiveList.Count -ne [int]$backupManifest.postgresql.archiveEntryCount) {
    throw (
      "PostgreSQL 备份归档目录与清单不一致（" +
      "$(Get-NativeFailureSummary $archiveListRun)）"
    )
  }
  $r2Manifest = Read-JsonFile $r2ManifestPath "R2 备份清单"
  Assert-ExactPropertySet $r2Manifest @("version", "fileCount", "sizeBytes", "files") "R2 备份清单"
  if ([string]$r2Manifest.version -cne "teruisi-r2-state-backup-v1" -or
      [string]$backupManifest.r2State.manifestSha256 -cne (Get-FileSha256 $r2ManifestPath) -or
      [int64]$r2Manifest.fileCount -ne [int64]$backupManifest.r2State.fileCount -or
      [int64]$r2Manifest.sizeBytes -ne [int64]$backupManifest.r2State.sizeBytes -or
      @($r2Manifest.files).Count -ne [int]$r2Manifest.fileCount) {
    throw "R2 备份清单身份或汇总不一致"
  }
  $r2CanonicalRoot = Get-CanonicalPath $r2BackupRoot
  $verifiedR2Bytes = [int64]0
  $verifiedR2Paths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::Ordinal
  )
  foreach ($entry in @($r2Manifest.files)) {
    Assert-ExactPropertySet $entry @("path", "sizeBytes", "sha256") "R2 备份文件项"
    $relative = [string]$entry.path
    if ([string]::IsNullOrWhiteSpace($relative) -or (Test-FullyQualifiedPath $relative)) {
      throw "R2 备份文件相对路径无效"
    }
    $candidate = [IO.Path]::GetFullPath((Join-Path $r2CanonicalRoot $relative.Replace("/", "\")))
    if (-not $candidate.StartsWith(
        $r2CanonicalRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      ) -or -not $verifiedR2Paths.Add($candidate) -or
      -not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      throw "R2 备份文件路径越界、重复或缺失"
    }
    $item = Get-Item -LiteralPath $candidate -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [int64]$item.Length -ne [int64]$entry.sizeBytes -or
        (Get-FileSha256 $candidate) -cne [string]$entry.sha256) {
      throw "R2 备份文件摘要不一致"
    }
    $verifiedR2Bytes += [int64]$item.Length
  }
  $actualR2Files = @(Get-ChildItem -LiteralPath $r2BackupRoot -File -Recurse)
  if ($actualR2Files.Count -ne [int]$r2Manifest.fileCount -or
      $verifiedR2Bytes -ne [int64]$r2Manifest.sizeBytes) {
    throw "R2 备份实际文件集合与清单不一致"
  }

  function Assert-NoAbandonEvidenceForBackup([string]$ExactRehearsalId = "") {
    if (-not [string]::IsNullOrWhiteSpace($ExactRehearsalId) -and
        $ExactRehearsalId -cnotmatch "^[0-9a-f]{12}$") {
      throw "abandon gate rehearsalId 无效"
    }
    $recoveryParent = Assert-RuntimeChildPath (
      Join-Path $ServiceRuntime "audits\cutover-abandon\recoveries"
    )
    if (Test-Path -LiteralPath $recoveryParent) {
      if (-not (Test-Path -LiteralPath $recoveryParent -PathType Container)) {
        throw "abandon recovery gate 根必须是受保护目录"
      }
      $recoveryParentItem = Get-Item -LiteralPath $recoveryParent -Force
      if (($recoveryParentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "abandon recovery gate 不得包含重解析点"
      }
      foreach ($entry in @(Get-ChildItem -LiteralPath $recoveryParent -Force)) {
        if (-not $entry.PSIsContainer -or
            $entry.Name -cnotmatch "^[0-9a-f]{12}$" -or
            ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            @(Get-ChildItem -LiteralPath $entry.FullName -Force).Count -ne 0) {
          throw "abandon recovery gate 存在无效 marker"
        }
        # Recovery markers are created before stale staging is removed and
        # released only after both create-only finals validate.  Any marker
        # therefore blocks every formal attempt, including a different tuple.
        throw "abandon incomplete recovery 尚未完成；拒绝 Plan/Execute"
      }
    }
    foreach ($kind in @("archives", "results")) {
      $parent = Assert-RuntimeChildPath (
        Join-Path $ServiceRuntime "audits\cutover-abandon\$kind"
      )
      if (-not (Test-Path -LiteralPath $parent)) { continue }
      if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "abandon gate 根必须是受保护目录"
      }
      $parentItem = Get-Item -LiteralPath $parent -Force
      if (($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "abandon gate 证据不得包含重解析点"
      }
      $pendingDirectories = [Collections.Queue]::new()
      $pendingDirectories.Enqueue($parentItem)
      while ($pendingDirectories.Count -gt 0) {
        $pendingDirectory = $pendingDirectories.Dequeue()
        foreach ($entry in @(Get-ChildItem -LiteralPath $pendingDirectory.FullName -Force)) {
          if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "abandon gate 证据不得包含重解析点"
          }
          if ($entry.PSIsContainer) { $pendingDirectories.Enqueue($entry) }
        }
      }
      foreach ($directory in @(Get-ChildItem -LiteralPath $parent -Directory -Force)) {
        $isFinal = $directory.Name -cmatch "^[0-9a-f]{12}$"
        $isIncomplete = $directory.Name -cmatch
          "^\.[0-9a-f]{12}\.[0-9a-f]{32}\.incomplete$"
        if (-not $isFinal -and -not $isIncomplete) {
          throw "abandon gate 存在未知证据目录"
        }
        if ($isIncomplete) {
          throw "abandon gate 存在未发布证据；必须先受控恢复或清理"
        }
        if (-not [string]::IsNullOrWhiteSpace($ExactRehearsalId) -and
            $directory.Name -ceq $ExactRehearsalId) {
          throw "rehearsal 已进入不可逆 abandon 协议；拒绝 Plan/Execute"
        }
        $evidencePath = if ($kind -ceq "archives") {
          Join-Path $directory.FullName "archive-manifest.json"
        } else {
          Join-Path $directory.FullName "abandon-result.json"
        }
        $evidenceShaPath = "$evidencePath.sha256"
        $expectedVersion = if ($kind -ceq "archives") {
          "teruisi-sales-cutover-abandon-archive-v1"
        } else { "teruisi-sales-cutover-abandon-v1" }
        if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $evidenceShaPath -PathType Leaf) -or
            [IO.File]::ReadAllText(
              $evidenceShaPath, [Text.Encoding]::UTF8
            ).Trim() -cne (Get-FileSha256 $evidencePath)) {
          throw "abandon gate 缺少 create-only 终态证据"
        }
        $evidence = Read-JsonFile $evidencePath "销售切换 abandon 证据"
        if ([string]$evidence.version -cne $expectedVersion -or
            [string]$evidence.status -cne "completed" -or
            [string]$evidence.rehearsalId -cne $directory.Name -or
            [string]$evidence.productionCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
            [string]$evidence.backupManifestSha256 -cnotmatch "^[0-9a-f]{64}$") {
          throw "abandon gate 证据身份无效"
        }
        if ([string]$evidence.productionCutoverId -ceq $RequestedCutoverId -or
            [string]$evidence.backupManifestSha256 -ceq $backupManifestSha256) {
          throw "当前 backup/cutover 已被 create-only abandon 证据撤销"
        }
      }
      if (@(Get-ChildItem -LiteralPath $parent -File -Force).Count -ne 0) {
        throw "abandon gate 根存在未知普通文件"
      }
    }
  }

  Assert-NoAbandonEvidenceForBackup

  $auditDirectory = Assert-RuntimeChildPath (Join-Path $ServiceRuntime "audits\sales-cutover")
  New-Item -ItemType Directory -Path $auditDirectory -Force | Out-Null
  Assert-RuntimeAclHardened
  $cleanupManifest = Join-Path $auditDirectory (
    "sales-cutover-$($cutoverDigest.Substring(0, 24)).legacy-r2-cleanup.json"
  )

  function Invoke-NodeJson([string[]]$Arguments, [string]$Label) {
    $run = Invoke-BoundedNativeProcess $Node $Arguments $InstalledAppRoot
    return ConvertFrom-NativeJsonRun $run $Label
  }

  function Invoke-PythonJson([string[]]$Arguments, [string]$Label) {
    $run = Invoke-BoundedNativeProcess $Python $Arguments $InstalledAppRoot
    return ConvertFrom-NativeJsonRun $run $Label
  }

  function Assert-WorkerReleaseGate([object]$Gate, [string]$Label) {
    Assert-ExactPropertySet $Gate @(
      "status", "version", "manifestSha256", "releaseId", "sourceFingerprint",
      "buildFingerprint", "sourceD1PathSha256", "persistRootPathSha256",
      "contractReceiptSha256", "processState"
    ) $Label
    if ([string]$Gate.status -cne "verified" -or
        [string]$Gate.version -cne "teruisi-local-worker-release-verification-v1" -or
        [string]$Gate.manifestSha256 -cne $workerReleaseManifestSha256 -or
        [string]$Gate.releaseId -cnotmatch "^\d{8}T\d{6}Z-[0-9a-f]{16}$" -or
        [string]$Gate.sourceFingerprint -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$Gate.buildFingerprint -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$Gate.sourceD1PathSha256 -cne $sourceD1PathSha256 -or
        [string]$Gate.persistRootPathSha256 -cne $persistRootPathSha256 -or
        [string]$Gate.contractReceiptSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$Gate.processState -cne "stopped") {
      throw "$Label 身份、指纹、数据路径或进程状态无效"
    }
  }

  function Invoke-WorkerReleaseGate([string]$Label) {
    $gate = Invoke-NodeJson @(
      $workerReleaseTool,
      "verify",
      "--manifest", $workerReleaseManifestPath,
      "--approved-manifest-sha256", $workerReleaseManifestSha256,
      "--expected-source-d1-path-sha256", $sourceD1PathSha256,
      "--expected-persist-root-path-sha256", $persistRootPathSha256,
      "--expected-host", "127.0.0.1",
      "--expected-port", "3000",
      "--require-sales-retired-code-receipt",
      "--process-policy", "stopped",
      "--json"
    ) $Label
    Assert-WorkerReleaseGate $gate $Label
    return $gate
  }

  function Assert-WorkerGuardGate([object]$Gate, [object]$ReleaseGate, [string]$Label) {
    Assert-ExactPropertySet $Gate @(
      "status", "version", "manifestSha256", "releaseId", "guardReceiptSha256",
      "protectedSourceRootPathSha256", "sourceD1PathSha256",
      "persistRootPathSha256", "processState"
    ) $Label
    if ([string]$Gate.status -cne "verified" -or
        [string]$Gate.version -cne "teruisi-legacy-worker-guard-verification-v1" -or
        [string]$Gate.manifestSha256 -cne [string]$ReleaseGate.manifestSha256 -or
        [string]$Gate.releaseId -cne [string]$ReleaseGate.releaseId -or
        [string]$Gate.guardReceiptSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$Gate.protectedSourceRootPathSha256 -cne
          $protectedWorkerSourceRootPathSha256 -or
        [string]$Gate.sourceD1PathSha256 -cne $sourceD1PathSha256 -or
        [string]$Gate.persistRootPathSha256 -cne $persistRootPathSha256 -or
        [string]$Gate.processState -cne "stopped") {
      throw "$Label 未证明主 Worker 入口已安装且与不可变 release 一致"
    }
  }

  function Invoke-WorkerGuardGate([object]$ReleaseGate, [string]$Label) {
    $gate = Invoke-NodeJson @(
      $workerReleaseTool,
      "verify-guard",
      "--manifest", $workerReleaseManifestPath,
      "--approved-manifest-sha256", $workerReleaseManifestSha256,
      "--expected-protected-source-root-path-sha256", $protectedWorkerSourceRootPathSha256,
      "--expected-source-d1-path-sha256", $sourceD1PathSha256,
      "--expected-persist-root-path-sha256", $persistRootPathSha256,
      "--process-policy", "stopped",
      "--json"
    ) $Label
    Assert-WorkerGuardGate $gate $ReleaseGate $Label
    return $gate
  }

  function Assert-WorkerAuthorityResult(
    [object]$Value,
    [string[]]$AllowedStatuses,
    [object]$ReleaseGate,
    [object]$GuardGate,
    [string]$Label
  ) {
    Assert-ExactPropertySet $Value @(
      "status", "version", "authoritySha256", "cutoverId", "workerReleaseId",
      "workerReleaseManifestSha256", "djangoDeploymentManifestSha256",
      "guardReceiptSha256", "sourceD1PathSha256", "persistRootPathSha256"
    ) $Label
    if ([string]$Value.status -cnotin $AllowedStatuses -or
        [string]$Value.version -cne "teruisi-sales-postgresql-authority-v1" -or
        [string]$Value.authoritySha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$Value.cutoverId -cne $RequestedCutoverId -or
        [string]$Value.workerReleaseId -cne [string]$ReleaseGate.releaseId -or
        [string]$Value.workerReleaseManifestSha256 -cne $workerReleaseManifestSha256 -or
        [string]$Value.djangoDeploymentManifestSha256 -cne
          $djangoDeploymentManifestSha256 -or
        [string]$Value.guardReceiptSha256 -cne [string]$GuardGate.guardReceiptSha256 -or
        [string]$Value.sourceD1PathSha256 -cne $sourceD1PathSha256 -or
        [string]$Value.persistRootPathSha256 -cne $persistRootPathSha256) {
      throw "$Label 与正式切换 tuple 不一致"
    }
  }

  function Invoke-WorkerAuthority(
    [ValidateSet("write-authority", "verify-authority")]
    [string]$Command,
    [object]$ReleaseGate,
    [object]$GuardGate,
    [string]$Label
  ) {
    $arguments = @(
      $workerReleaseTool,
      $Command,
      "--manifest", $workerReleaseManifestPath,
      "--approved-manifest-sha256", $workerReleaseManifestSha256,
      "--django-deployment-manifest-sha256", $djangoDeploymentManifestSha256,
      "--cutover-id", $RequestedCutoverId,
      "--expected-guard-receipt-sha256", ([string]$GuardGate.guardReceiptSha256),
      "--expected-source-d1-path-sha256", $sourceD1PathSha256,
      "--expected-persist-root-path-sha256", $persistRootPathSha256
    )
    if ($Command -ceq "write-authority") {
      $arguments += @("--process-policy", "stopped")
    }
    $arguments += "--json"
    $authority = Invoke-NodeJson $arguments $Label
    $allowed = if ($Command -ceq "write-authority") {
      @("written", "already_present", "sidecar_repaired")
    } else { @("verified") }
    Assert-WorkerAuthorityResult $authority $allowed $ReleaseGate $GuardGate $Label
    return $authority
  }

  $planArguments = @(
    $r2Tool,
    "--dry-run",
    "--source", $source,
    "--cutover-id", $RequestedCutoverId,
    "--bucket", "site-creator-r2",
    "--persist-to", $persistRoot,
    "--manifest", $cleanupManifest
  )
  if ($RequestedAction -ceq "Plan") {
    if ($ExecuteRequested -or $MaintenanceConfirmed -or
        -not [string]::IsNullOrWhiteSpace($RequestedR2Approval) -or
        -not [string]::IsNullOrWhiteSpace($RequestedBackupApproval) -or
        -not [string]::IsNullOrWhiteSpace($RequestedRehearsalResultPath) -or
        -not [string]::IsNullOrWhiteSpace($RequestedRehearsalApproval) -or
        -not [string]::IsNullOrWhiteSpace($RequestedWorkerReleaseApproval)) {
      throw "Plan 只生成只读 R2 清理计划，不接受执行或批准参数"
    }
    if ($backupAgeExceeded) {
      throw "销售切换备份已过期"
    }
    $workerReleaseGate = Invoke-WorkerReleaseGate "Worker release 只读验证"
    $workerGuardGate = Invoke-WorkerGuardGate $workerReleaseGate `
      "主 Worker fail-closed guard 只读验证"
    $plan = Invoke-NodeJson $planArguments "销售切换 R2 dry-run"
    if ([string]$plan.status -cne "dry_run_completed" -or
        [string]$plan.manifestId -cnotmatch "^[0-9a-f]{64}$") {
      throw "销售切换 R2 dry-run 结果无效"
    }
    return [ordered]@{
      status = "planned"
      cutoverId = $RequestedCutoverId
      backupManifestSha256 = $backupManifestSha256
      r2CleanupManifestId = [string]$plan.manifestId
      workerReleaseManifestPath = $workerReleaseManifestPath
      workerReleaseManifestSha256 = $workerReleaseManifestSha256
      workerReleaseId = [string]$workerReleaseGate.releaseId
      workerSourceFingerprint = [string]$workerReleaseGate.sourceFingerprint
      workerBuildFingerprint = [string]$workerReleaseGate.buildFingerprint
      workerGuardReceiptSha256 = [string]$workerGuardGate.guardReceiptSha256
      sessions = [int]$plan.sessions
      objects = [int]$plan.objects
    }
  }

  if (-not $ExecuteRequested -or -not $MaintenanceConfirmed) {
    throw "Execute 必须显式提供 -Execute 与 -ConfirmedMaintenance"
  }
  if ($RequestedR2Approval -cnotmatch "^[0-9a-f]{64}$" -or
      $RequestedBackupApproval -cnotmatch "^[0-9a-f]{64}$" -or
      $RequestedRehearsalApproval -cnotmatch "^[0-9a-f]{64}$" -or
      $RequestedWorkerReleaseApproval -cnotmatch "^[0-9a-f]{64}$") {
    throw "Execute 必须提供备份、R2、演练与 Worker release 的四个 64 位小写批准摘要"
  }
  if ($RequestedBackupApproval -cne $backupManifestSha256) {
    throw "批准的备份清单 SHA-256 与现场清单不一致"
  }
  if ($RequestedWorkerReleaseApproval -cne $workerReleaseManifestSha256) {
    throw "批准的 Worker release manifest SHA-256 与现场文件不一致"
  }
  $plan = Invoke-NodeJson $planArguments "销售切换 R2 dry-run 复验"
  if ([string]$plan.manifestId -cne $RequestedR2Approval) {
    throw "批准的 R2 cleanup manifestId 与现场重算不一致"
  }

  if ([string]::IsNullOrWhiteSpace($RequestedRehearsalResultPath) -or
      -not (Test-FullyQualifiedPath $RequestedRehearsalResultPath)) {
    throw "Execute 必须提供受保护 runtime 内的 rehearsal result 绝对路径"
  }
  $rehearsalResultPath = Get-CanonicalPath $RequestedRehearsalResultPath
  $rehearsalsRoot = Assert-RuntimeChildPath (Join-Path $ServiceRuntime "rehearsals")
  $rehearsalRoot = Get-CanonicalPath (Split-Path -Parent $rehearsalResultPath)
  if ([IO.Path]::GetFileName($rehearsalResultPath) -cne "rehearsal-result.json" -or
      -not (Test-Path -LiteralPath $rehearsalResultPath -PathType Leaf) -or
      (Get-CanonicalPath (Split-Path -Parent $rehearsalRoot)) -ine
        (Get-CanonicalPath $rehearsalsRoot) -or
      [IO.Path]::GetFileName($rehearsalRoot) -cnotmatch "^[0-9a-f]{12}$") {
    throw "rehearsal result 不在唯一受控演练目录"
  }
  foreach ($item in @((Get-Item -LiteralPath $rehearsalRoot -Force)) + @(
    Get-ChildItem -LiteralPath $rehearsalRoot -Force -Recurse
  )) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "rehearsal 证据目录不得包含重解析点"
    }
  }
  if ((Get-FileSha256 $rehearsalResultPath) -cne $RequestedRehearsalApproval) {
    throw "批准的 rehearsal result SHA-256 与现场文件不一致"
  }
  $rehearsalShaPath = Join-Path $rehearsalRoot "rehearsal-result.json.sha256"
  $recordedRehearsalSha = if (Test-Path -LiteralPath $rehearsalShaPath -PathType Leaf) {
    [IO.File]::ReadAllText($rehearsalShaPath, [Text.Encoding]::UTF8).Trim()
  } else { "" }
  if ($recordedRehearsalSha -cne $RequestedRehearsalApproval) {
    throw "rehearsal result 的不可变摘要旁证不一致"
  }
  $rehearsal = Read-JsonFile $rehearsalResultPath "销售切换 rehearsal result"
  Assert-ExactPropertySet $rehearsal @(
    "status", "version", "rehearsalId", "cutoverId", "productionCutoverId",
    "databaseName", "backupCreatedAt", "backupManifestSha256",
    "deploymentManifestSha256", "serviceConfigSha256", "sourcePathSha256",
    "sourceD1Sha256", "r2ManifestSha256", "postgresqlDumpSha256",
    "attestationPayloadSha256", "smokeReceiptSha256", "retirementAuditId",
    "preservedEvidenceSha256", "evidenceDirectory", "completedAt"
  ) "销售切换 rehearsal result"
  $rehearsalId = [IO.Path]::GetFileName($rehearsalRoot)
  $rehearsalCompletedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse(
      [string]$rehearsal.completedAt,
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::RoundtripKind,
      [ref]$rehearsalCompletedAt
    )) {
    throw "rehearsal completedAt 无效"
  }
  $rehearsalAge = [DateTimeOffset]::UtcNow - $rehearsalCompletedAt.ToUniversalTime()
  $rehearsalAgeExceeded = $rehearsalAge -gt [TimeSpan]::FromMinutes(360)
  $rehearsalHexFields = @(
    "attestationPayloadSha256", "smokeReceiptSha256", "retirementAuditId",
    "preservedEvidenceSha256"
  )
  if ([string]$rehearsal.status -cne "completed" -or
      [string]$rehearsal.version -cne "teruisi-sales-cutover-rehearsal-result-v1" -or
      [string]$rehearsal.rehearsalId -cne $rehearsalId -or
      [string]$rehearsal.cutoverId -cne "rehearsal-$rehearsalId" -or
      [string]$rehearsal.productionCutoverId -cne $RequestedCutoverId -or
      [string]$rehearsal.databaseName -cne "teruisi_sales_rehearsal_$rehearsalId" -or
      [string]$rehearsal.backupCreatedAt -cne [string]$backupManifest.createdAt -or
      [string]$rehearsal.backupManifestSha256 -cne $backupManifestSha256 -or
      [string]$rehearsal.deploymentManifestSha256 -cne (Get-FileSha256 $DeploymentManifestPath) -or
      [string]$rehearsal.serviceConfigSha256 -cne (Get-FileSha256 $ConfigPath) -or
      [string]$rehearsal.sourcePathSha256 -cne [string]$backupManifest.sourcePathSha256 -or
      [string]$rehearsal.sourceD1Sha256 -cne [string]$backupManifest.sourceD1.sha256 -or
      [string]$rehearsal.r2ManifestSha256 -cne [string]$backupManifest.r2State.manifestSha256 -or
      [string]$rehearsal.postgresqlDumpSha256 -cne [string]$backupManifest.postgresql.sha256 -or
      (Get-CanonicalPath ([string]$rehearsal.evidenceDirectory)) -ine $rehearsalRoot -or
      $rehearsalCompletedAt -lt $backupCreatedAt -or
      $rehearsalAge -lt [TimeSpan]::FromMinutes(-5) -or
      @($rehearsalHexFields | Where-Object {
        [string]$rehearsal.$_ -cnotmatch "^[0-9a-f]{64}$"
      }).Count -gt 0) {
    throw "rehearsal result 未绑定本次备份、部署、配置或完整终态证据"
  }
  Assert-NoAbandonEvidenceForBackup $rehearsalId

  $rehearsalCutoverDigest = Get-Sha256Text ([string]$rehearsal.cutoverId)
  $rehearsalStatePath = Get-CanonicalPath (Join-Path $rehearsalRoot (
    "audit\cutover\sales-cutover-$($rehearsalCutoverDigest.Substring(0, 24)).state.json"
  ))
  $rehearsalRetirementAuditPath = Get-CanonicalPath (
    Join-Path $rehearsalRoot "audit\sales-retirement.json"
  )
  foreach ($evidencePath in @($rehearsalStatePath, $rehearsalRetirementAuditPath)) {
    if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf) -or
        ((Get-Item -LiteralPath $evidencePath -Force).Attributes -band
          [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "rehearsal 缺少唯一普通文件 cutover/retirement 证据"
    }
  }

  Assert-ApplicationProcessesStopped "SalesCutoverExecute"
  if (@(Get-PortListeners 3000).Count -gt 0 -or
      @(Get-PortListeners 5791).Count -gt 0 -or
      @(Get-PortListeners 8001).Count -gt 0 -or
      @(Get-PortListeners 8002).Count -gt 0) {
    throw "正式切换执行前维护端口已被重新占用"
  }

  # This is the first immutable public-Worker release fence.  It runs from the
  # Django protected app, not from the candidate release, so a candidate cannot
  # self-certify.  Both the built tree and every supported legacy launcher must
  # be stopped and bound to the same guarded release before any authority state
  # or schema/data mutation is allowed.
  $workerReleaseGate = Invoke-WorkerReleaseGate "正式 Worker release 门禁"
  $workerGuardGate = Invoke-WorkerGuardGate $workerReleaseGate `
    "正式主 Worker fail-closed guard 门禁"

  # This gate is deliberately after the second maintenance/port fence and
  # before forward-recovery.json or any D1/PostgreSQL schema/data mutation.
  # It reuses the deployed v4 canonical reader directly and never creates a
  # SalesMigrationRun or opens the target PostgreSQL database.
  $snapshotGate = Invoke-PythonJson @(
    $snapshotGateTool,
    "verify-live",
    "--backend-dir", $BackendRoot,
    "--source", $source,
    "--backup-source", $d1BackupPath,
    "--rehearsal-state", $rehearsalStatePath,
    "--rehearsal-root", $rehearsalRoot,
    "--rehearsal-id", $rehearsalId
  ) "正式切换实时 D1 canonical snapshot 门禁"
  Assert-ExactPropertySet $snapshotGate @(
    "status", "canonicalFormatVersion", "sourceRevision", "snapshotSha256",
    "digestKeyCount"
  ) "正式切换实时 D1 canonical snapshot 门禁结果"
  if ([string]$snapshotGate.status -cne "verified" -or
      [string]$snapshotGate.canonicalFormatVersion -cne "sales-projection-v4" -or
      [string]$snapshotGate.sourceRevision -cnotmatch "^\d+:\d+$" -or
      [string]$snapshotGate.snapshotSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [int]$snapshotGate.digestKeyCount -lt 1) {
    throw "正式切换实时 D1 canonical snapshot 门禁结果无效"
  }

  $retirementAuditGate = Invoke-PythonJson @(
    $snapshotGateTool,
    "verify-retirement-audit",
    "--audit", $rehearsalRetirementAuditPath,
    "--rehearsal-id", $rehearsalId,
    "--audit-id", ([string]$rehearsal.retirementAuditId),
    "--preserved-evidence-sha256", ([string]$rehearsal.preservedEvidenceSha256),
    "--attestation-payload-sha256", ([string]$rehearsal.attestationPayloadSha256),
    "--smoke-receipt-sha256", ([string]$rehearsal.smokeReceiptSha256)
  ) "rehearsal retirement audit 回查"
  Assert-ExactPropertySet $retirementAuditGate @(
    "status", "cutoverId", "auditId", "preservedEvidenceSha256"
  ) "rehearsal retirement audit 回查结果"
  if ([string]$retirementAuditGate.status -cne "verified" -or
      [string]$retirementAuditGate.cutoverId -cne [string]$rehearsal.cutoverId -or
      [string]$retirementAuditGate.auditId -cne [string]$rehearsal.retirementAuditId -or
      [string]$retirementAuditGate.preservedEvidenceSha256 -cne
        [string]$rehearsal.preservedEvidenceSha256) {
    throw "rehearsal retirement audit 回查结果与 result 不一致"
  }

  # The first schema/data mutation is deliberately a roll-forward boundary,
  # even while D1 remains the write owner.  A failed execution is resumed with
  # this exact cutover/backup/rehearsal tuple; code-only rollback is forbidden.
  $forwardRecoveryPath = Join-Path $auditDirectory (
    "sales-cutover-$($cutoverDigest.Substring(0, 24)).forward-recovery.json"
  )
  $validatedRollForwardRecovery = $false
  if (Test-Path -LiteralPath $forwardRecoveryPath -PathType Leaf) {
    $existingRecovery = Read-JsonFile $forwardRecoveryPath "销售切换前向恢复凭证"
    $recoveryFields = @(
      "version", "status", "cutoverId", "boundary", "recoveryAction", "runtimeRoot",
      "backupManifestSha256", "r2CleanupManifestId", "rehearsalResultPath",
      "rehearsalResultSha256", "sourceCanonicalSnapshotSha256",
      "rehearsalRetirementAuditId", "djangoDeploymentManifestSha256",
      "workerReleaseManifestSha256", "workerReleaseId", "workerGuardReceiptSha256",
      "workerProtectedSourceRootPathSha256", "workerSourceD1PathSha256",
      "workerPersistRootPathSha256", "createdAt", "updatedAt"
    )
    if ([string]$existingRecovery.status -ceq "completed") {
      $recoveryFields += @(
        "completedAt", "attestationPayloadSha256", "workerAuthoritySha256"
      )
    } elseif ([string]$existingRecovery.status -cne "roll_forward_required") {
      throw "已有前向恢复凭证状态无效"
    }
    Assert-ExactPropertySet $existingRecovery $recoveryFields "销售切换前向恢复凭证"
    if ([string]$existingRecovery.version -cne "teruisi-sales-forward-recovery-v3" -or
        [string]$existingRecovery.cutoverId -cne $RequestedCutoverId -or
        [string]$existingRecovery.boundary -cne "first_schema_or_data_mutation" -or
        [string]$existingRecovery.recoveryAction -cne "rerun_same_runtime_operator_execute" -or
        (Get-CanonicalPath ([string]$existingRecovery.runtimeRoot)) -ine
          (Get-CanonicalPath $ServiceRuntime) -or
        [string]$existingRecovery.backupManifestSha256 -cne $backupManifestSha256 -or
        [string]$existingRecovery.r2CleanupManifestId -cne $RequestedR2Approval -or
        (Get-CanonicalPath ([string]$existingRecovery.rehearsalResultPath)) -ine
          $rehearsalResultPath -or
        [string]$existingRecovery.rehearsalResultSha256 -cne $RequestedRehearsalApproval -or
        [string]$existingRecovery.sourceCanonicalSnapshotSha256 -cne
          [string]$snapshotGate.snapshotSha256 -or
        [string]$existingRecovery.rehearsalRetirementAuditId -cne
          [string]$rehearsal.retirementAuditId -or
        [string]$existingRecovery.djangoDeploymentManifestSha256 -cne
          $djangoDeploymentManifestSha256 -or
        [string]$existingRecovery.workerReleaseManifestSha256 -cne
          $workerReleaseManifestSha256 -or
        [string]$existingRecovery.workerReleaseId -cne
          [string]$workerReleaseGate.releaseId -or
        [string]$existingRecovery.workerGuardReceiptSha256 -cne
          [string]$workerGuardGate.guardReceiptSha256 -or
        [string]$existingRecovery.workerProtectedSourceRootPathSha256 -cne
          $protectedWorkerSourceRootPathSha256 -or
        [string]$existingRecovery.workerSourceD1PathSha256 -cne $sourceD1PathSha256 -or
        [string]$existingRecovery.workerPersistRootPathSha256 -cne $persistRootPathSha256 -or
        ([string]$existingRecovery.status -ceq "completed" -and
          ([string]$existingRecovery.attestationPayloadSha256 -cnotmatch "^[0-9a-f]{64}$" -or
           [string]$existingRecovery.workerAuthoritySha256 -cnotmatch "^[0-9a-f]{64}$"))) {
      throw "已有前向恢复凭证与本次批准材料不一致"
    }
    if ([string]$existingRecovery.status -ceq "completed") {
      throw "已有前向恢复凭证已 completed；拒绝降级为 roll_forward_required 或重放正式切换"
    }
    $validatedRollForwardRecovery = $true
  }
  if (($backupAgeExceeded -or $rehearsalAgeExceeded) -and
      -not $validatedRollForwardRecovery) {
    throw "销售切换备份或 rehearsal 已过期；仅允许精确 v3 roll_forward_required 同 tuple 前向恢复豁免年龄"
  }
  $recoveryRecord = [ordered]@{
    version = "teruisi-sales-forward-recovery-v3"
    status = "roll_forward_required"
    cutoverId = $RequestedCutoverId
    boundary = "first_schema_or_data_mutation"
    recoveryAction = "rerun_same_runtime_operator_execute"
    runtimeRoot = Get-CanonicalPath $ServiceRuntime
    backupManifestSha256 = $backupManifestSha256
    r2CleanupManifestId = $RequestedR2Approval
    rehearsalResultPath = $rehearsalResultPath
    rehearsalResultSha256 = $RequestedRehearsalApproval
    sourceCanonicalSnapshotSha256 = [string]$snapshotGate.snapshotSha256
    rehearsalRetirementAuditId = [string]$rehearsal.retirementAuditId
    djangoDeploymentManifestSha256 = $djangoDeploymentManifestSha256
    workerReleaseManifestSha256 = $workerReleaseManifestSha256
    workerReleaseId = [string]$workerReleaseGate.releaseId
    workerGuardReceiptSha256 = [string]$workerGuardGate.guardReceiptSha256
    workerProtectedSourceRootPathSha256 = $protectedWorkerSourceRootPathSha256
    workerSourceD1PathSha256 = $sourceD1PathSha256
    workerPersistRootPathSha256 = $persistRootPathSha256
    createdAt = if ($null -ne $existingRecovery) {
      [string]$existingRecovery.createdAt
    } else { [DateTimeOffset]::UtcNow.ToString("o") }
    updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  Write-AtomicJson $forwardRecoveryPath $recoveryRecord

  # The recovery record is durable before this external authority state changes
  # what the main desktop launcher is allowed to start.  The authority writer is
  # deterministic and idempotent for this exact tuple; any different existing
  # sentinel fails closed.
  $workerAuthority = Invoke-WorkerAuthority "write-authority" `
    $workerReleaseGate $workerGuardGate "Worker sales PostgreSQL authority 发布"

  # Close the race in which an old launcher starts after the second port fence
  # but before the authority sentinel becomes visible.  A race leaves recovery
  # and the sentinel in place, performs no business mutation, and can only be
  # resumed with this same tuple after the process is stopped.
  Assert-ApplicationProcessesStopped "SalesCutoverPostWorkerAuthority"
  if (@(Get-PortListeners 3000).Count -gt 0 -or
      @(Get-PortListeners 5791).Count -gt 0 -or
      @(Get-PortListeners 8001).Count -gt 0 -or
      @(Get-PortListeners 8002).Count -gt 0) {
    throw "Worker authority 发布后第三次维护端口栅栏失败"
  }
  $workerReleaseGateAfterAuthority = Invoke-WorkerReleaseGate `
    "Worker authority 发布后 release 复验"
  $workerGuardGateAfterAuthority = Invoke-WorkerGuardGate `
    $workerReleaseGateAfterAuthority "Worker authority 发布后 guard 复验"
  if ([string]$workerReleaseGateAfterAuthority.releaseId -cne
        [string]$workerReleaseGate.releaseId -or
      [string]$workerReleaseGateAfterAuthority.sourceFingerprint -cne
        [string]$workerReleaseGate.sourceFingerprint -or
      [string]$workerReleaseGateAfterAuthority.buildFingerprint -cne
        [string]$workerReleaseGate.buildFingerprint -or
      [string]$workerGuardGateAfterAuthority.guardReceiptSha256 -cne
        [string]$workerGuardGate.guardReceiptSha256) {
    throw "Worker authority 发布前后 release/guard 身份发生变化"
  }
  $workerAuthorityVerified = Invoke-WorkerAuthority "verify-authority" `
    $workerReleaseGateAfterAuthority $workerGuardGateAfterAuthority `
    "Worker sales PostgreSQL authority 回读"
  if ([string]$workerAuthorityVerified.authoritySha256 -cne
      [string]$workerAuthority.authoritySha256) {
    throw "Worker authority 发布与回读的原始文件摘要不一致"
  }

  $secrets = $null
  $ownerUrl = $null
  $erpUrl = $null
  $previousErpUrl = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_ERP_DATABASE_URL", "Process"
  )
  $previousWrangler = [Environment]::GetEnvironmentVariable(
    "TERUISI_WRANGLER_CLI_JS", "Process"
  )
  $previousManaged = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_CUTOVER_MANAGED", "Process"
  )
  $previousRehearsalManaged = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED", "Process"
  )
  try {
    Start-Postgres | Out-Null
    $secrets = Read-Secrets
    Invoke-DjangoMigrations $secrets "teruisi_sales"
    $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword (
      "teruisi_cutover_" + $cutoverDigest.Substring(0, 12)
    ) $WriterStatementTimeoutMs "teruisi_sales"
    $erpUrl = Database-Url "teruisi_erp_reference_sync" $secrets.ErpSyncPassword (
      "teruisi_cutover_erp_" + $cutoverDigest.Substring(0, 12)
    ) $WriterStatementTimeoutMs "teruisi_sales"
    $payload = Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" $false $WriterMaxBodyBytes "" "" {
      $env:TERUISI_DJANGO_ERP_DATABASE_URL = $erpUrl
      $env:TERUISI_WRANGLER_CLI_JS = $wranglerCli
      $env:TERUISI_DJANGO_CUTOVER_MANAGED = "1"
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED", $null, "Process"
      )
      Invoke-NodeJson @(
        $cutoverTool,
        "--managed-execute",
        "--confirmed-maintenance",
        "--runtime-root", $ServiceRuntime,
        "--source", $source,
        "--cutover-id", $RequestedCutoverId,
        "--audit-dir", $auditDirectory,
        "--backend-dir", $BackendRoot,
        "--python", $Python,
        "--r2-persist-to", $persistRoot,
        "--approved-r2-cleanup-manifest-id", $RequestedR2Approval,
        "--expected-source-canonical-snapshot-sha256", ([string]$snapshotGate.snapshotSha256),
        "--repository-root", $InstalledAppRoot
      ) "销售 Django/PostgreSQL cutover"
    }
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_ERP_DATABASE_URL", $previousErpUrl, "Process"
    )
    [Environment]::SetEnvironmentVariable(
      "TERUISI_WRANGLER_CLI_JS", $previousWrangler, "Process"
    )
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_CUTOVER_MANAGED", $previousManaged, "Process"
    )
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED", $previousRehearsalManaged, "Process"
    )
    $ownerUrl = $null
    $erpUrl = $null
    $secrets = $null
  }
  $requiredSteps = @(
    "d1_0090_0091_pre_schema",
    "postgres_schema_migrated",
    "erp_reference_checkpoint_caught_up",
    "sales_snapshot_dry_run",
    "sales_snapshot_applied",
    "sales_snapshot_verified_before_prepare",
    "d1_locked_verify_cleanup_pending",
    "d1_authority_postgresql_terminal",
    "d1_terminal_attested",
    "postgres_authority_activated"
  )
  if ([string]$payload.status -cne "completed" -or
      [string]$payload.cutoverId -cne $RequestedCutoverId -or
      @($requiredSteps | Where-Object { @($payload.steps) -notcontains $_ }).Count -gt 0) {
    throw "销售 cutover 结果缺少终态或必需步骤"
  }
  $statePath = Join-Path $auditDirectory (
    "sales-cutover-$($cutoverDigest.Substring(0, 24)).state.json"
  )
  $state = Read-JsonFile $statePath "销售 cutover state"
  $attestationStep = @($state.steps | Where-Object {
    [string]$_.name -ceq "d1_terminal_attested"
  })
  if ($attestationStep.Count -ne 1 -or
      [string]$attestationStep[0].result.payloadSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "销售 cutover state 缺少唯一 attestation"
  }
  $attestationPath = Get-CanonicalPath ([string]$attestationStep[0].result.attestationPath)
  if (-not $attestationPath.StartsWith(
      (Get-CanonicalPath $auditDirectory) + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    ) -or -not (Test-Path -LiteralPath $attestationPath -PathType Leaf)) {
    throw "销售 cutover attestation 不在受保护 audit 目录"
  }
  $recoveryRecord["status"] = "completed"
  $recoveryRecord["updatedAt"] = [DateTimeOffset]::UtcNow.ToString("o")
  $recoveryRecord["completedAt"] = $recoveryRecord["updatedAt"]
  $recoveryRecord["attestationPayloadSha256"] = [string]$attestationStep[0].result.payloadSha256
  $recoveryRecord["workerAuthoritySha256"] = [string]$workerAuthorityVerified.authoritySha256
  Write-AtomicJson $forwardRecoveryPath $recoveryRecord
  return [ordered]@{
    status = "completed"
    cutoverId = $RequestedCutoverId
    backupManifestSha256 = $backupManifestSha256
    r2CleanupManifestId = $RequestedR2Approval
    rehearsalId = $rehearsalId
    rehearsalResultSha256 = $RequestedRehearsalApproval
    sourceCanonicalSnapshotSha256 = [string]$snapshotGate.snapshotSha256
    workerReleaseManifestSha256 = $workerReleaseManifestSha256
    workerReleaseId = [string]$workerReleaseGate.releaseId
    workerSourceFingerprint = [string]$workerReleaseGate.sourceFingerprint
    workerBuildFingerprint = [string]$workerReleaseGate.buildFingerprint
    workerGuardReceiptSha256 = [string]$workerGuardGate.guardReceiptSha256
    workerAuthoritySha256 = [string]$workerAuthorityVerified.authoritySha256
    forwardRecoveryPath = $forwardRecoveryPath
    attestationPath = $attestationPath
    attestationPayloadSha256 = [string]$attestationStep[0].result.payloadSha256
    steps = @($payload.steps)
  }
  }
} $Action $RuntimeRoot $CutoverId $ApprovedR2CleanupManifestId `
  $ApprovedBackupManifestSha256 $RehearsalResultPath $ApprovedRehearsalResultSha256 `
  $WorkerReleaseManifestPath $ApprovedWorkerReleaseManifestSha256 `
  $Execute.IsPresent $ConfirmedMaintenance.IsPresent

$result | ConvertTo-Json -Compress -Depth 8
