[CmdletBinding()]
param(
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [Parameter(Mandatory = $true)]
  [string]$ApprovedCurrentDeploymentManifestSha256,
  [Parameter(Mandatory = $true)]
  [string[]]$BackupDirectoryNames,
  [switch]$Execute,
  [switch]$ConfirmedDeleteStaleBackups
)

$ErrorActionPreference = "Stop"
$FixedRuntimeRoot = "D:\teruisi-runtime\django-sales"
$OrphanIncompleteGraceMinutes = 60

if (-not $Execute.IsPresent -or -not $ConfirmedDeleteStaleBackups.IsPresent) {
  throw "旧备份清理必须显式提供 -Execute 与 -ConfirmedDeleteStaleBackups"
}
if ($ApprovedCurrentDeploymentManifestSha256 -cnotmatch "^[0-9a-f]{64}$") {
  throw "ApprovedCurrentDeploymentManifestSha256 必须是 64 位小写 SHA-256"
}
if ($BackupDirectoryNames.Count -lt 1 -or
    @($BackupDirectoryNames | Select-Object -Unique).Count -ne $BackupDirectoryNames.Count) {
  throw "必须提供非空且不重复的旧备份目录名"
}

$canonicalRuntime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd("\", "/")
if ($canonicalRuntime -ine $FixedRuntimeRoot) {
  throw "旧备份清理只允许固定受保护 Django runtime"
}
$serviceScript = Join-Path $canonicalRuntime "app\tools\django-local-service.ps1"
if (-not (Test-Path -LiteralPath $serviceScript -PathType Leaf)) {
  throw "缺少受保护的 runtime Django 服务脚本"
}

$previousLibraryOnly = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)
$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
try {
  . $serviceScript -Action Status -RuntimeRoot $canonicalRuntime
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
  )
}

function Get-BackupTreeEvidence([string]$Path) {
  $rootItem = Get-Item -LiteralPath $Path -Force
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "旧备份目录不得是重解析点"
  }
  $directories = [Collections.Queue]::new()
  $directories.Enqueue($rootItem)
  $fileCount = [int64]0
  $sizeBytes = [int64]0
  $latestWriteTimeUtc = $rootItem.LastWriteTimeUtc
  while ($directories.Count -gt 0) {
    $directory = $directories.Dequeue()
    foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force)) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "旧备份目录不得包含重解析点"
      }
      if ($item.LastWriteTimeUtc -gt $latestWriteTimeUtc) {
        $latestWriteTimeUtc = $item.LastWriteTimeUtc
      }
      if ($item.PSIsContainer) {
        $directories.Enqueue($item)
      } else {
        $fileCount += 1
        $sizeBytes += [int64]$item.Length
      }
    }
  }
  return [pscustomobject]@{
    FileCount = $fileCount
    SizeBytes = $sizeBytes
    LatestWriteTimeUtc = $latestWriteTimeUtc
  }
}

function Assert-NoReparsePoints([string]$Path, [string]$Label) {
  $root = Get-Item -LiteralPath $Path -Force
  if (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Label 不得包含重解析点"
  }
  $directories = [Collections.Queue]::new()
  if ($root.PSIsContainer) { $directories.Enqueue($root) }
  while ($directories.Count -gt 0) {
    $directory = $directories.Dequeue()
    foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force)) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label 不得包含重解析点"
      }
      if ($item.PSIsContainer) { $directories.Enqueue($item) }
    }
  }
}

function Test-ExactPropertySet([object]$Value, [string[]]$Expected) {
  if ($null -eq $Value) { return $false }
  $actual = @($Value.PSObject.Properties.Name | Sort-Object)
  $wanted = @($Expected | Sort-Object)
  if ($actual.Count -ne $wanted.Count) { return $false }
  for ($index = 0; $index -lt $wanted.Count; $index++) {
    if ([string]$actual[$index] -cne [string]$wanted[$index]) { return $false }
  }
  return $true
}

function Test-PathIsSameOrDescendant([string]$Candidate, [string]$Root) {
  $candidatePath = Get-CanonicalPath $Candidate
  $rootPath = Get-CanonicalPath $Root
  return (
    $candidatePath -ieq $rootPath -or
    $candidatePath.StartsWith(
      $rootPath + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )
  )
}

function Test-RehearsalPayloadDispositionCleaned(
  [string]$RehearsalRoot,
  [object]$State
) {
  if ([string]$State.payloadDisposition -cne "cleaned" -or
      [string]$State.payloadCleanupAuditSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]::IsNullOrWhiteSpace([string]$State.payloadCleanupAuditPath)) {
    return $false
  }
  $expectedAuditPath = Get-CanonicalPath (
    Join-Path $RehearsalRoot "payload-cleanup-audit.json"
  )
  try {
    $auditPath = Get-CanonicalPath ([string]$State.payloadCleanupAuditPath)
  } catch {
    return $false
  }
  if ($auditPath -ine $expectedAuditPath -or
      -not (Test-Path -LiteralPath $auditPath -PathType Leaf)) {
    return $false
  }
  $auditItem = Get-Item -LiteralPath $auditPath -Force
  if (($auditItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
      (Get-FileSha256 $auditPath) -cne [string]$State.payloadCleanupAuditSha256) {
    return $false
  }
  try {
    $audit = Read-JsonFile $auditPath "rehearsal payload cleanup 审计"
  } catch {
    return $false
  }
  if (-not (Test-ExactPropertySet $audit @(
      "version", "status", "rehearsalId", "backupManifestSha256", "databaseName",
      "startedAt", "completedAt", "database", "payload", "errors"
    )) -or
      [string]$audit.version -cne "teruisi-sales-rehearsal-payload-cleanup-v1" -or
      [string]$audit.status -cne "completed" -or
      [string]$audit.rehearsalId -cne [string]$State.rehearsalId -or
      [string]$audit.backupManifestSha256 -cne [string]$State.backupManifestSha256 -or
      [string]$audit.databaseName -cne "teruisi_sales_rehearsal_$([string]$State.rehearsalId)" -or
      @($audit.errors).Count -ne 0 -or
      $State.databaseRetained -isnot [bool] -or [bool]$State.databaseRetained) {
    return $false
  }
  $startedAt = [DateTimeOffset]::MinValue
  $completedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse([string]$audit.startedAt, [ref]$startedAt) -or
      -not [DateTimeOffset]::TryParse([string]$audit.completedAt, [ref]$completedAt) -or
      $completedAt -lt $startedAt) {
    return $false
  }
  if (-not (Test-ExactPropertySet $audit.database @(
      "name", "status", "existed", "dropped", "verifiedAbsent"
    )) -or
      [string]$audit.database.name -cne [string]$audit.databaseName -or
      [string]$audit.database.status -notin @("absent", "dropped") -or
      $audit.database.existed -isnot [bool] -or
      $audit.database.dropped -isnot [bool] -or
      $audit.database.verifiedAbsent -isnot [bool] -or
      -not [bool]$audit.database.verifiedAbsent) {
    return $false
  }
  if (-not (Test-ExactPropertySet $audit.payload @(
      "removedBytes", "targets", "verifiedAbsent"
    )) -or
      $audit.payload.verifiedAbsent -isnot [bool] -or
      -not [bool]$audit.payload.verifiedAbsent -or
      [int64]$audit.payload.removedBytes -lt 0) {
    return $false
  }
  $expectedTargets = @(
    ".wrangler", "r2-state", "source-d1.sqlite", "teruisi-sales.dump",
    "postgresql-restore.dump"
  )
  $targetRows = @($audit.payload.targets)
  if ($targetRows.Count -ne $expectedTargets.Count) { return $false }
  $seenTargets = @{}
  foreach ($row in $targetRows) {
    if (-not (Test-ExactPropertySet $row @(
        "relativePath", "existed", "removed", "sizeBytes"
      )) -or
        [string]$row.relativePath -notin $expectedTargets -or
        $seenTargets.ContainsKey([string]$row.relativePath) -or
        $row.existed -isnot [bool] -or $row.removed -isnot [bool] -or
        [bool]$row.removed -ne [bool]$row.existed -or
        [int64]$row.sizeBytes -lt 0) {
      return $false
    }
    $seenTargets[[string]$row.relativePath] = $true
    if (Test-Path -LiteralPath (Join-Path $RehearsalRoot ([string]$row.relativePath))) {
      return $false
    }
  }
  return $true
}

function Test-CompletedRehearsalAbandoned(
  [string]$RehearsalRoot,
  [object]$State,
  [string]$BackupManifestSha256,
  [string]$BackupCutoverId
) {
  try {
    if ([string]$State.status -cne "completed" -or
        [string]$State.rehearsalId -cnotmatch "^[0-9a-f]{12}$" -or
        [string]$State.backupManifestSha256 -cne $BackupManifestSha256 -or
        [string]$State.backupCutoverId -cne $BackupCutoverId -or
        [string]$State.resultFileSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        -not (Test-RehearsalPayloadDispositionCleaned $RehearsalRoot $State)) {
      return $false
    }
    $rehearsalId = [string]$State.rehearsalId
    $recoveryRoot = Get-CanonicalPath (
      Join-Path $canonicalRuntime "audits\cutover-abandon\recoveries\$rehearsalId"
    )
    if (Test-Path -LiteralPath $recoveryRoot) {
      # A recovery marker is removed only after both create-only abandon
      # finals have been revalidated.  Never prune the backup needed to resume.
      return $false
    }
    $resultRoot = Get-CanonicalPath (
      Join-Path $canonicalRuntime "audits\cutover-abandon\results\$rehearsalId"
    )
    $expectedResultParent = Get-CanonicalPath (
      Join-Path $canonicalRuntime "audits\cutover-abandon\results"
    )
    if ((Get-CanonicalPath (Split-Path -Parent $resultRoot)) -ine $expectedResultParent -or
        [IO.Path]::GetFileName($resultRoot) -cne $rehearsalId -or
        -not (Test-Path -LiteralPath $resultRoot -PathType Container)) {
      return $false
    }
    $resultRootItem = Get-Item -LiteralPath $resultRoot -Force
    if (($resultRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      return $false
    }
    $resultPath = Join-Path $resultRoot "abandon-result.json"
    $resultShaPath = Join-Path $resultRoot "abandon-result.json.sha256"
    $finalPreflightPath = Join-Path $resultRoot "final-preflight.json"
    foreach ($filePath in @($resultPath, $resultShaPath, $finalPreflightPath)) {
      if (-not (Test-Path -LiteralPath $filePath -PathType Leaf) -or
          ((Get-Item -LiteralPath $filePath -Force).Attributes -band
            [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
      }
    }
    if (@(Get-ChildItem -LiteralPath $resultRoot -File -Force).Count -ne 3) {
      return $false
    }
    $resultSha256 = Get-FileSha256 $resultPath
    if ($resultSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [IO.File]::ReadAllText($resultShaPath, [Text.Encoding]::UTF8).Trim() -cne
          $resultSha256) {
      return $false
    }
    $result = Read-JsonFile $resultPath "completed rehearsal abandon result"
    if (-not (Test-ExactPropertySet $result @(
        "version", "status", "rehearsalId", "productionCutoverId", "backupCutoverId",
        "backupDirectoryName", "backupManifestSha256", "rehearsalResultSha256",
        "archiveManifestPath", "archiveManifestSha256",
        "initialPreflightEvidenceSha256", "finalPreflightEvidenceSha256",
        "payloadCleanupAuditPath", "payloadCleanupAuditSha256",
        "backupDisposition", "completedAt"
      )) -or
        [string]$result.version -cne "teruisi-sales-cutover-abandon-v1" -or
        [string]$result.status -cne "completed" -or
        [string]$result.rehearsalId -cne $rehearsalId -or
        [string]$result.productionCutoverId -cne $BackupCutoverId -or
        [string]$result.backupCutoverId -cne $BackupCutoverId -or
        [string]$result.backupDirectoryName -cne
          "sales-cutover-$((Get-Sha256Text $BackupCutoverId).Substring(0, 24))" -or
        [string]$result.backupManifestSha256 -cne $BackupManifestSha256 -or
        [string]$result.rehearsalResultSha256 -cne [string]$State.resultFileSha256 -or
        [string]$result.archiveManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$result.initialPreflightEvidenceSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$result.finalPreflightEvidenceSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$result.payloadCleanupAuditSha256 -cne
          [string]$State.payloadCleanupAuditSha256 -or
      (Get-CanonicalPath ([string]$result.payloadCleanupAuditPath)) -ine
          (Get-CanonicalPath ([string]$State.payloadCleanupAuditPath)) -or
        [string]$result.backupDisposition -cne "approved_for_controlled_prune") {
      return $false
    }

    $archiveRoot = Get-CanonicalPath (
      Join-Path $canonicalRuntime "audits\cutover-abandon\archives\$rehearsalId"
    )
    $archiveManifestPath = Join-Path $archiveRoot "archive-manifest.json"
    $archiveManifestShaPath = Join-Path $archiveRoot "archive-manifest.json.sha256"
    if ((Get-CanonicalPath ([string]$result.archiveManifestPath)) -ine
          (Get-CanonicalPath $archiveManifestPath) -or
        -not (Test-Path -LiteralPath $archiveRoot -PathType Container) -or
        ((Get-Item -LiteralPath $archiveRoot -Force).Attributes -band
          [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not (Test-Path -LiteralPath $archiveManifestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $archiveManifestShaPath -PathType Leaf)) {
      return $false
    }
    Assert-NoReparsePoints $archiveRoot "completed rehearsal abandon archive"
    $archiveManifestSha256 = Get-FileSha256 $archiveManifestPath
    if ($archiveManifestSha256 -cne [string]$result.archiveManifestSha256 -or
        [IO.File]::ReadAllText(
          $archiveManifestShaPath, [Text.Encoding]::UTF8
        ).Trim() -cne $archiveManifestSha256) {
      return $false
    }
    $archive = Read-JsonFile $archiveManifestPath "completed rehearsal abandon archive"
    if (-not (Test-ExactPropertySet $archive @(
        "version", "status", "rehearsalId", "productionCutoverId",
        "backupCutoverId", "backupDirectoryName", "backupManifestSha256",
        "rehearsalResultSha256", "initialPreflightEvidenceSha256", "files", "createdAt"
      )) -or
        [string]$archive.version -cne "teruisi-sales-cutover-abandon-archive-v1" -or
        [string]$archive.status -cne "completed" -or
        [string]$archive.rehearsalId -cne $rehearsalId -or
        [string]$archive.productionCutoverId -cne $BackupCutoverId -or
        [string]$archive.backupCutoverId -cne $BackupCutoverId -or
        [string]$archive.backupDirectoryName -cne [string]$result.backupDirectoryName -or
        [string]$archive.backupManifestSha256 -cne $BackupManifestSha256 -or
        [string]$archive.rehearsalResultSha256 -cne [string]$State.resultFileSha256 -or
        [string]$archive.initialPreflightEvidenceSha256 -cne
          [string]$result.initialPreflightEvidenceSha256) {
      return $false
    }
    $seen = @{}
    foreach ($row in @($archive.files)) {
      if (-not (Test-ExactPropertySet $row @("path", "sizeBytes", "sha256")) -or
          [string]::IsNullOrWhiteSpace([string]$row.path) -or
          [string]$row.path -match "^[A-Za-z]:[\\/]" -or
          [string]$row.path -match "^[\\/]{2}" -or
          [string]$row.path -match "(^|[\\/])\.\.([\\/]|$)" -or
          $seen.ContainsKey([string]$row.path) -or
          [int64]$row.sizeBytes -lt 0 -or
          [string]$row.sha256 -cnotmatch "^[0-9a-f]{64}$") {
        return $false
      }
      $seen[[string]$row.path] = $true
      $archivedFile = Get-CanonicalPath (Join-Path $archiveRoot ([string]$row.path))
      if (-not $archivedFile.StartsWith(
          $archiveRoot + [IO.Path]::DirectorySeparatorChar,
          [StringComparison]::OrdinalIgnoreCase
        ) -or -not (Test-Path -LiteralPath $archivedFile -PathType Leaf)) {
        return $false
      }
      $archivedItem = Get-Item -LiteralPath $archivedFile -Force
      if (($archivedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
          [int64]$archivedItem.Length -ne [int64]$row.sizeBytes -or
          (Get-FileSha256 $archivedFile) -cne [string]$row.sha256) {
        return $false
      }
    }
    $actualArchiveFiles = @(Get-ChildItem -LiteralPath $archiveRoot -File -Recurse -Force |
      Where-Object {
        $_.FullName -ine $archiveManifestPath -and
        $_.FullName -ine $archiveManifestShaPath
      })
    if ($actualArchiveFiles.Count -ne $seen.Count) { return $false }
    foreach ($required in @(
      "rehearsal/rehearsal-state.json", "rehearsal/rehearsal-result.json",
      "rehearsal/rehearsal-result.json.sha256", "backup/backup-manifest.json",
      "backup/backup-manifest.json.sha256", "backup/r2-manifest.json",
      "preflight/initial.json"
    )) {
      if (-not $seen.ContainsKey($required)) { return $false }
    }
    if ((Get-FileSha256 (Join-Path $archiveRoot "backup\backup-manifest.json")) -cne
          $BackupManifestSha256 -or
        [IO.File]::ReadAllText(
          (Join-Path $archiveRoot "backup\backup-manifest.json.sha256"),
          [Text.Encoding]::UTF8
        ).Trim() -cne $BackupManifestSha256 -or
        (Get-FileSha256 (Join-Path $archiveRoot "rehearsal\rehearsal-result.json")) -cne
          [string]$State.resultFileSha256 -or
        [IO.File]::ReadAllText(
          (Join-Path $archiveRoot "rehearsal\rehearsal-result.json.sha256"),
          [Text.Encoding]::UTF8
        ).Trim() -cne [string]$State.resultFileSha256 -or
        (Get-FileSha256 (Join-Path $archiveRoot "preflight\initial.json")) -cne
          [string]$result.initialPreflightEvidenceSha256) {
      return $false
    }
    $currentRehearsalResultPath = Join-Path $RehearsalRoot "rehearsal-result.json"
    $currentRehearsalResultShaPath = Join-Path $RehearsalRoot "rehearsal-result.json.sha256"
    if (-not (Test-Path -LiteralPath $currentRehearsalResultPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $currentRehearsalResultShaPath -PathType Leaf) -or
        (Get-FileSha256 $currentRehearsalResultPath) -cne [string]$State.resultFileSha256 -or
        [IO.File]::ReadAllText(
          $currentRehearsalResultShaPath, [Text.Encoding]::UTF8
        ).Trim() -cne [string]$State.resultFileSha256) {
      return $false
    }
    $archivedState = Read-JsonFile (
      (Join-Path $archiveRoot "rehearsal\rehearsal-state.json")
    ) "archived completed rehearsal state"
    if ([string]$archivedState.version -cne "teruisi-sales-cutover-rehearsal-v1" -or
        [string]$archivedState.status -cne "completed" -or
        [string]$archivedState.rehearsalId -cne $rehearsalId -or
        [string]$archivedState.backupCutoverId -cne $BackupCutoverId -or
        [string]$archivedState.backupManifestSha256 -cne $BackupManifestSha256 -or
        [string]$archivedState.resultFileSha256 -cne [string]$State.resultFileSha256) {
      return $false
    }

    foreach ($preflightPair in @(
      [pscustomobject]@{
        Path = (Join-Path $archiveRoot "preflight\initial.json")
        Stage = "initial"
        Sha256 = [string]$result.initialPreflightEvidenceSha256
      },
      [pscustomobject]@{
        Path = $finalPreflightPath
        Stage = "final"
        Sha256 = [string]$result.finalPreflightEvidenceSha256
      }
    )) {
      if ((Get-FileSha256 $preflightPair.Path) -cne $preflightPair.Sha256) {
        return $false
      }
      $preflight = Read-JsonFile $preflightPair.Path "completed rehearsal abandon preflight"
      if (-not (Test-ExactPropertySet $preflight @(
          "version", "status", "stage", "rehearsalId", "productionCutoverId",
          "backupManifestSha256", "forwardRecoveryRecordCount", "formalStateCount",
          "workerAuthorityFileAbsent", "workerAuthoritySidecarAbsent",
          "d1EvidenceSha256", "postgresqlEvidenceSha256", "r2ManifestSha256",
          "deploymentManifestSha256", "serviceConfigSha256", "checkedAt"
        )) -or
          [string]$preflight.version -cne "teruisi-sales-cutover-abandon-preflight-v1" -or
          [string]$preflight.status -cne "verified" -or
          [string]$preflight.stage -cne [string]$preflightPair.Stage -or
          [string]$preflight.rehearsalId -cne $rehearsalId -or
          [string]$preflight.productionCutoverId -cne $BackupCutoverId -or
          [string]$preflight.backupManifestSha256 -cne $BackupManifestSha256 -or
          [int]$preflight.forwardRecoveryRecordCount -ne 0 -or
          [int]$preflight.formalStateCount -ne 0 -or
          $preflight.workerAuthorityFileAbsent -isnot [bool] -or
          -not [bool]$preflight.workerAuthorityFileAbsent -or
          $preflight.workerAuthoritySidecarAbsent -isnot [bool] -or
          -not [bool]$preflight.workerAuthoritySidecarAbsent -or
          [string]$preflight.d1EvidenceSha256 -cnotmatch "^[0-9a-f]{64}$" -or
          [string]$preflight.postgresqlEvidenceSha256 -cnotmatch "^[0-9a-f]{64}$" -or
          [string]$preflight.r2ManifestSha256 -cnotmatch "^[0-9a-f]{64}$") {
        return $false
      }
    }

    $workerAuthorityPath = "D:\teruisi-runtime\teruisi-worker-sales\state\sales-postgresql-authority.json"
    if ((Test-Path -LiteralPath $workerAuthorityPath) -or
        (Test-Path -LiteralPath "$workerAuthorityPath.sha256")) {
      return $false
    }
    return $true
  } catch {
    return $false
  }
}

function Assert-BackupHasNoBlockingReferences(
  [string]$BackupManifestSha256,
  [string]$BackupCutoverId
) {
  $forwardRoot = Get-CanonicalPath (Join-Path $canonicalRuntime "audits\sales-cutover")
  if (Test-Path -LiteralPath $forwardRoot -PathType Container) {
    foreach ($file in @(Get-ChildItem -LiteralPath $forwardRoot -File -Force -Filter (
        "*.forward-recovery.json"
      ))) {
      if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "前向恢复记录不得是重解析点"
      }
      try {
        $record = Read-JsonFile $file.FullName "销售切换前向恢复记录"
      } catch {
        throw "存在无法验证的前向恢复记录；拒绝删除 completed backup"
      }
      $expectedForwardFields = @(
        "version", "status", "cutoverId", "boundary", "recoveryAction", "runtimeRoot",
        "backupManifestSha256", "r2CleanupManifestId", "rehearsalResultPath",
        "rehearsalResultSha256", "createdAt", "updatedAt"
      )
      $forwardVersion = [string]$record.version
      if ($forwardVersion -cin @(
          "teruisi-sales-forward-recovery-v2",
          "teruisi-sales-forward-recovery-v3"
        )) {
        $expectedForwardFields += @(
          "sourceCanonicalSnapshotSha256", "rehearsalRetirementAuditId"
        )
      }
      if ($forwardVersion -ceq "teruisi-sales-forward-recovery-v3") {
        $expectedForwardFields += @(
          "djangoDeploymentManifestSha256", "workerReleaseManifestSha256",
          "workerReleaseId", "workerGuardReceiptSha256",
          "workerProtectedSourceRootPathSha256", "workerSourceD1PathSha256",
          "workerPersistRootPathSha256"
        )
      }
      if ([string]$record.status -ceq "completed") {
        $expectedForwardFields += @("completedAt", "attestationPayloadSha256")
        if ($forwardVersion -ceq "teruisi-sales-forward-recovery-v3") {
          $expectedForwardFields += "workerAuthoritySha256"
        }
      }
      if (-not (Test-ExactPropertySet $record $expectedForwardFields) -or
          $forwardVersion -cnotin @(
            "teruisi-sales-forward-recovery-v1",
            "teruisi-sales-forward-recovery-v2",
            "teruisi-sales-forward-recovery-v3"
          ) -or
          [string]$record.status -notin @("roll_forward_required", "completed") -or
          [string]$record.cutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          [string]$record.backupManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
          [string]$record.boundary -cne "first_schema_or_data_mutation" -or
          [string]$record.recoveryAction -cne "rerun_same_runtime_operator_execute" -or
          (Get-CanonicalPath ([string]$record.runtimeRoot) -ine $canonicalRuntime) -or
          [string]$record.r2CleanupManifestId -cnotmatch "^[0-9a-f]{64}$" -or
          [string]$record.rehearsalResultSha256 -cnotmatch "^[0-9a-f]{64}$" -or
          ($forwardVersion -cin @(
              "teruisi-sales-forward-recovery-v2",
              "teruisi-sales-forward-recovery-v3"
            ) -and (
              [string]$record.sourceCanonicalSnapshotSha256 -cnotmatch "^[0-9a-f]{64}$" -or
              [string]$record.rehearsalRetirementAuditId -cnotmatch "^[0-9a-f]{64}$"
            )) -or
          ($forwardVersion -ceq "teruisi-sales-forward-recovery-v3" -and (
              [string]$record.djangoDeploymentManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
              [string]$record.workerReleaseManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
              [string]$record.workerReleaseId -cnotmatch "^\d{8}T\d{6}Z-[0-9a-f]{16}$" -or
              [string]$record.workerGuardReceiptSha256 -cnotmatch "^[0-9a-f]{64}$" -or
              [string]$record.workerProtectedSourceRootPathSha256 -cnotmatch "^[0-9a-f]{64}$" -or
              [string]$record.workerSourceD1PathSha256 -cnotmatch "^[0-9a-f]{64}$" -or
              [string]$record.workerPersistRootPathSha256 -cnotmatch "^[0-9a-f]{64}$"
            )) -or
          ([string]$record.status -ceq "completed" -and
            ([string]$record.attestationPayloadSha256 -cnotmatch "^[0-9a-f]{64}$" -or
             ($forwardVersion -ceq "teruisi-sales-forward-recovery-v3" -and
              [string]$record.workerAuthoritySha256 -cnotmatch "^[0-9a-f]{64}$"))) -or
          $file.Name -cne "sales-cutover-$((Get-Sha256Text ([string]$record.cutoverId)).Substring(0, 24)).forward-recovery.json") {
        throw "存在无效的前向恢复记录；拒绝删除 completed backup"
      }
      if (
          [string]$record.backupManifestSha256 -ceq $BackupManifestSha256 -or
          [string]$record.cutoverId -ceq $BackupCutoverId
        ) {
        throw "completed backup 仍被正式前向恢复记录引用"
      }
    }
    $formalStatePath = Join-Path $forwardRoot (
      "sales-cutover-$((Get-Sha256Text $BackupCutoverId).Substring(0, 24)).state.json"
    )
    if (Test-Path -LiteralPath $formalStatePath) {
      throw "completed backup 仍被正式 mutation state 引用"
    }
  }

  $rehearsalRootParent = Get-CanonicalPath (Join-Path $canonicalRuntime "rehearsals")
  if (-not (Test-Path -LiteralPath $rehearsalRootParent -PathType Container)) { return }
  foreach ($directory in @(Get-ChildItem -LiteralPath $rehearsalRootParent -Directory -Force)) {
    if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $directory.Name -cnotmatch "^[0-9a-f]{12}$") {
      throw "存在无效的 rehearsal 证据目录；拒绝删除 completed backup"
    }
    $statePath = Join-Path $directory.FullName "rehearsal-state.json"
    try {
      $state = Read-JsonFile $statePath "rehearsal state"
    } catch {
      throw "存在无法验证的 rehearsal state；拒绝删除 completed backup"
    }
    if ([string]$state.version -cne "teruisi-sales-cutover-rehearsal-v1" -or
        [string]$state.rehearsalId -cne $directory.Name -or
        [string]$state.status -notin @("running", "failed", "completed") -or
        [string]$state.backupCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
        [string]$state.backupManifestSha256 -cnotmatch "^[0-9a-f]{64}$") {
      throw "存在无效的 rehearsal state；拒绝删除 completed backup"
    }
    $matchesBackup = (
      [string]$state.backupManifestSha256 -ceq $BackupManifestSha256 -or
      [string]$state.backupCutoverId -ceq $BackupCutoverId
    )
    if (-not $matchesBackup) { continue }
    if ([string]$state.status -ceq "failed" -and
        (Test-RehearsalPayloadDispositionCleaned $directory.FullName $state)) {
      continue
    }
    if ([string]$state.status -ceq "completed" -and
        (Test-CompletedRehearsalAbandoned `
          $directory.FullName $state $BackupManifestSha256 $BackupCutoverId)) {
      continue
    }
    throw "completed backup 仍被未完成或未处置的 rehearsal state 引用"
  }
}

$result = Invoke-WithServiceMutex {
  Assert-DeployedApplication
  Assert-ApplicationProcessesStopped "CutoverBackupPrune"
  foreach ($port in @(3000, 5791, 5432, 8001, 8002)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "旧备份清理要求端口 $port 已停止"
    }
  }

  $deploymentManifestPath = Join-Path $InstalledAppRoot "deployment.json"
  $currentDeploymentSha256 = Get-FileSha256 $deploymentManifestPath
  if ($currentDeploymentSha256 -cne $ApprovedCurrentDeploymentManifestSha256) {
    throw "当前部署摘要与显式批准值不一致"
  }
  Assert-RuntimeAclHardened

  $backupRoot = Get-CanonicalPath (Join-Path $canonicalRuntime "backups")
  if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) {
    throw "runtime backups 目录不存在"
  }
  $targets = @()
  $totalFiles = [int64]0
  $totalBytes = [int64]0
  $currentProcessDirectory = Get-CanonicalPath ([Environment]::CurrentDirectory)
  $currentPowerShellLocation = $null
  if ((Get-Location).Provider.Name -ceq "FileSystem") {
    $currentPowerShellLocation = Get-CanonicalPath (Get-Location).ProviderPath
  }
  foreach ($name in $BackupDirectoryNames) {
    $isCompletedBackup = $name -cmatch "^sales-cutover-[0-9a-f]{24}$"
    $isOrphanIncomplete = $name -cmatch (
      "^\.sales-cutover-[0-9a-f]{24}\.[0-9a-f]{32}\.incomplete$"
    )
    if (-not $isCompletedBackup -and -not $isOrphanIncomplete) {
      throw "旧备份目录名不在受控白名单"
    }
    $target = Get-CanonicalPath (Join-Path $backupRoot $name)
    if ((Get-CanonicalPath (Split-Path -Parent $target)) -ine $backupRoot -or
        [IO.Path]::GetFileName($target) -cne $name -or
        -not (Test-Path -LiteralPath $target -PathType Container)) {
      throw "旧备份必须是 runtime backups 的现有直接子目录"
    }
    if ((Test-PathIsSameOrDescendant $currentProcessDirectory $target) -or
        ($null -ne $currentPowerShellLocation -and
          (Test-PathIsSameOrDescendant $currentPowerShellLocation $target))) {
      throw "拒绝删除当前进程工作目录"
    }

    $evidence = Get-BackupTreeEvidence $target
    $manifestPath = Join-Path $target "backup-manifest.json"
    $targetKind = "orphan_incomplete"
    if ($isCompletedBackup) {
      if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "completed backup 缺少备份清单"
      }
      $manifest = Read-JsonFile $manifestPath "旧备份清单"
      $manifestSha256 = Get-FileSha256 $manifestPath
      $sidecarPath = Join-Path $target "backup-manifest.json.sha256"
      if (-not (Test-Path -LiteralPath $sidecarPath -PathType Leaf) -or
          [IO.File]::ReadAllText($sidecarPath, [Text.Encoding]::UTF8).Trim() -cne
            $manifestSha256 -or
          [string]$manifest.version -cne "teruisi-sales-cutover-backup-v1" -or
          [string]$manifest.cutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
          $name -cne "sales-cutover-$((Get-Sha256Text ([string]$manifest.cutoverId)).Substring(0, 24))" -or
          [string]$manifest.deploymentManifestSha256 -ceq $currentDeploymentSha256) {
        throw "旧备份清单无效或仍绑定当前部署；拒绝删除"
      }
      Assert-BackupHasNoBlockingReferences $manifestSha256 ([string]$manifest.cutoverId)
      $targetKind = "completed_stale"
    } else {
      $orphanAge = [DateTimeOffset]::UtcNow - [DateTimeOffset]$evidence.LatestWriteTimeUtc
      if ($orphanAge -lt [TimeSpan]::FromMinutes($OrphanIncompleteGraceMinutes) -or
          $orphanAge -gt [TimeSpan]::FromDays(3650)) {
        throw "未发布 incomplete backup 未超过安全宽限或时间无效"
      }
    }

    $totalFiles += [int64]$evidence.FileCount
    $totalBytes += [int64]$evidence.SizeBytes
    $targets += [pscustomobject]@{
      Name = $name
      Path = $target
      Kind = $targetKind
      ManifestSha256 = if ($isCompletedBackup) { $manifestSha256 } else { "" }
      CutoverId = if ($isCompletedBackup) { [string]$manifest.cutoverId } else { "" }
      FileCount = [int64]$evidence.FileCount
      SizeBytes = [int64]$evidence.SizeBytes
      LatestWriteTimeUtc = $evidence.LatestWriteTimeUtc
    }
  }

  $auditRoot = Assert-RuntimeChildPath (Join-Path $canonicalRuntime "audits\backup-prune")
  New-Item -ItemType Directory -Path $auditRoot -Force | Out-Null
  $auditId = "{0}-{1}" -f (
    Get-Date -Format "yyyyMMdd-HHmmss"), ([Guid]::NewGuid().ToString("N").Substring(0, 12))
  $auditPath = Assert-RuntimeChildPath (Join-Path $auditRoot "$auditId.json")
  $audit = [ordered]@{
    version = "teruisi-sales-cutover-backup-prune-v1"
    auditId = $auditId
    status = "planned"
    currentDeploymentManifestSha256 = $currentDeploymentSha256
    requestedDirectoryNameSha256 = @($targets | ForEach-Object { Get-Sha256Text $_.Name })
    targetKinds = @($targets | ForEach-Object { $_.Kind })
    directoryCount = $targets.Count
    fileCount = $totalFiles
    sizeBytes = $totalBytes
    plannedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
  Write-AtomicJson $auditPath $audit

  foreach ($target in $targets) {
    $freshEvidence = Get-BackupTreeEvidence $target.Path
    if ([int64]$freshEvidence.FileCount -ne [int64]$target.FileCount -or
        [int64]$freshEvidence.SizeBytes -ne [int64]$target.SizeBytes -or
        $freshEvidence.LatestWriteTimeUtc -ne $target.LatestWriteTimeUtc -or
        (Test-PathIsSameOrDescendant ([Environment]::CurrentDirectory) $target.Path) -or
        ((Get-Location).Provider.Name -ceq "FileSystem" -and
          (Test-PathIsSameOrDescendant (Get-Location).ProviderPath $target.Path))) {
      throw "旧备份在删除前发生变化或包含当前工作目录"
    }
    if ([string]$target.Kind -ceq "completed_stale") {
      $freshManifestPath = Join-Path $target.Path "backup-manifest.json"
      if ((Get-FileSha256 $freshManifestPath) -cne [string]$target.ManifestSha256) {
        throw "completed backup 清单在删除前发生变化"
      }
      Assert-BackupHasNoBlockingReferences (
        [string]$target.ManifestSha256
      ) ([string]$target.CutoverId)
    }
    Remove-Item -LiteralPath $target.Path -Recurse -Force
    if (Test-Path -LiteralPath $target.Path) {
      throw "旧备份删除后仍存在"
    }
  }
  $audit.status = "completed"
  $audit.completedAt = [DateTimeOffset]::UtcNow.ToString("o")
  Write-AtomicJson $auditPath $audit
  return [ordered]@{
    status = "completed"
    version = "teruisi-sales-cutover-backup-prune-result-v1"
    auditId = $auditId
    auditSha256 = Get-FileSha256 $auditPath
    directoriesDeleted = $targets.Count
    filesDeleted = $totalFiles
    bytesDeleted = $totalBytes
  }
}

$result | ConvertTo-Json -Compress
