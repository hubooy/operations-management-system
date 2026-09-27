[CmdletBinding()]
param(
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [Parameter(Mandatory = $true)]
  [string]$SourceD1,
  [Parameter(Mandatory = $true)]
  [string]$R2PersistTo,
  [Parameter(Mandatory = $true)]
  [string]$CutoverId,
  [switch]$Execute,
  [switch]$ConfirmedMaintenance
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

function ConvertTo-AsciiPythonLauncher(
  [string]$Code,
  [string]$SourceName
) {
  if ([string]::IsNullOrEmpty($Code)) {
    throw "Python launcher code must not be empty"
  }
  if ($SourceName -cnotmatch "^[A-Za-z0-9._-]{1,128}$") {
    throw "Python launcher source name is invalid"
  }
  $encodedCode = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($Code)
  )
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
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try {
    $digestBytes = $algorithm.ComputeHash(
      [Text.Encoding]::UTF8.GetBytes($builder.ToString())
    )
    $digest = ([BitConverter]::ToString($digestBytes)).Replace("-", "").ToLowerInvariant()
  } finally {
    $algorithm.Dispose()
  }
  return [pscustomobject][ordered]@{
    OutputRecordCount = [int]$records.Count
    CapturedRecordCount = [int]$capturedRecords
    OutputTruncated = [bool]$truncated
    OutputSha256 = $digest
  }
}

function Invoke-BoundedNativeProcess(
  [string]$Executable,
  [string[]]$Arguments
) {
  $outerErrorActionPreference = $ErrorActionPreference
  $outerLastExitCode = $global:LASTEXITCODE
  $output = @()
  $nativeExitCode = $null
  try {
    # Windows PowerShell 5 converts native stderr into ErrorRecord objects.
    # Capture those records without letting the script-wide Stop preference
    # bypass the exact native exit-code check.
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null
    try {
      $output = @(& $Executable @Arguments 2>&1)
      $nativeExitCode = $global:LASTEXITCODE
    } catch {
      $output = @($_)
      $nativeExitCode = $null
    }
  } finally {
    $ErrorActionPreference = $outerErrorActionPreference
    $global:LASTEXITCODE = $outerLastExitCode
  }
  $exitCode = if ($null -eq $nativeExitCode) { -1 } else { [int]$nativeExitCode }
  return [pscustomobject][ordered]@{
    ExitCode = $exitCode
    Output = @($output)
    Diagnostic = Get-BoundedNativeDiagnostic $output
  }
}

function Get-NativeFailureSummary([object]$Run) {
  return (
    "exitCode=$([int]$Run.ExitCode); " +
    "outputRecordCount=$([int]$Run.Diagnostic.OutputRecordCount); " +
    "capturedRecordCount=$([int]$Run.Diagnostic.CapturedRecordCount); " +
    "outputTruncated=$([bool]$Run.Diagnostic.OutputTruncated); " +
    "outputSha256=$([string]$Run.Diagnostic.OutputSha256)"
  )
}

if (-not $Execute -or -not $ConfirmedMaintenance) {
  throw "切换备份必须显式提供 -Execute 与 -ConfirmedMaintenance"
}
if ($CutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
  throw "CutoverId 必须是 8 到 128 位安全标识"
}
if (-not (Test-FullyQualifiedPath $RuntimeRoot)) {
  throw "RuntimeRoot 必须是绝对路径"
}

$canonicalRuntime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd("\", "/")
$installedScript = Join-Path $canonicalRuntime "app\tools\django-local-service.ps1"
if (-not (Test-Path -LiteralPath $installedScript -PathType Leaf)) {
  throw "缺少受保护的 runtime Django 服务脚本；请先 DeployApp"
}

$result = & {
  param(
    [string]$ServiceScript,
    [string]$ServiceRuntime,
    [string]$RequestedSource,
    [string]$RequestedPersistRoot,
    [string]$RequestedCutoverId
  )

  $previousLibraryOnly = [Environment]::GetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
  )
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  try {
    . $ServiceScript -Action Status -RuntimeRoot $ServiceRuntime
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process"
    )
  }

  return Invoke-WithServiceMutex {

  Assert-DeployedApplication
  Assert-WranglerLocalR2RoundTrip $InstalledAppRoot
  Assert-RuntimeAclHardened
  Assert-ApplicationProcessesStopped "CutoverBackup"
  foreach ($port in @(3000, 5791)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "切换备份要求本机 Worker/工作流辅助端口 $port 已停止"
    }
  }
  foreach ($port in @(8001, 8002)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "切换备份要求 Django 端口 $port 已停止"
    }
  }

  $config = Get-ServiceConfig
  $source = Resolve-ErpSourceD1 $RequestedSource
  if ([string]$config.erpSourceD1 -cne $source) {
    throw "备份源必须精确等于服务配置中的 ERP/D1 权威文件"
  }
  $sourceItem = Get-Item -LiteralPath $source -Force
  if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "D1 备份源不得是重解析点"
  }

  if (-not (Test-FullyQualifiedPath $RequestedPersistRoot)) {
    throw "R2PersistTo 必须是绝对目录"
  }
  $persistRoot = [IO.Path]::GetFullPath($RequestedPersistRoot).TrimEnd("\", "/")
  if (-not (Test-Path -LiteralPath $persistRoot -PathType Container)) {
    throw "R2 persist 根目录不存在"
  }
  $persistD1Root = [IO.Path]::GetFullPath(
    (Join-Path $persistRoot "v3\d1")
  ).TrimEnd("\", "/")
  if (-not $source.StartsWith(
    $persistD1Root + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
  )) {
    throw "D1 与 R2 必须来自同一个固定 Wrangler persist 根目录"
  }
  $r2Source = Join-Path $persistRoot "v3\r2"
  if (-not (Test-Path -LiteralPath $r2Source -PathType Container)) {
    throw "固定 Miniflare R2 状态根目录不存在"
  }
  $r2Item = Get-Item -LiteralPath $r2Source -Force
  if (($r2Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "R2 状态目录不得是重解析点"
  }
  foreach ($requiredR2Child in @("miniflare-R2BucketObject", "site-creator-r2")) {
    if (-not (Test-Path -LiteralPath (Join-Path $r2Source $requiredR2Child) -PathType Container)) {
      throw "R2 状态根目录缺少受控 metadata 或 blob 子目录"
    }
  }
  foreach ($r2SourceItem in @(Get-ChildItem -LiteralPath $r2Source -Force -Recurse)) {
    if (($r2SourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "R2 状态源不得包含重解析点"
    }
  }

  $backupRoot = Assert-RuntimeChildPath (Join-Path $ServiceRuntime "backups")
  New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
  $cutoverDigest = Get-Sha256Text $RequestedCutoverId
  $finalDirectory = Assert-RuntimeChildPath (
    Join-Path $backupRoot "sales-cutover-$($cutoverDigest.Substring(0, 24))"
  )
  if (Test-Path -LiteralPath $finalDirectory) {
    throw "本 cutoverId 的最终备份目录已存在；拒绝覆盖"
  }
  $workingNonce = [Guid]::NewGuid().ToString("N")
  $expectedWorkingName = ".sales-cutover-$($cutoverDigest.Substring(0, 24)).$workingNonce.incomplete"
  $workingDirectory = Assert-RuntimeChildPath (
    Join-Path $backupRoot $expectedWorkingName
  )
  $secrets = $null
  $postgresStarted = $false
  $backupPublished = $false
  $backupAttemptFailed = $false
  $backupFailureDigest = ""
  $previousPgPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
  $previousEvidenceUrl = [Environment]::GetEnvironmentVariable(
    "TERUISI_BACKUP_DATABASE_URL", "Process"
  )
  try {
    # From the first creation of the unpublished directory onward, every exit
    # flows through the identity-checked cleanup in finally.
    New-Item -ItemType Directory -Path $workingDirectory | Out-Null
    $sqliteTool = Join-Path $InstalledAppRoot "tools\sqlite-consistent-backup.py"
    if (-not (Test-Path -LiteralPath $sqliteTool -PathType Leaf)) {
      throw "runtime app 缺少受控 SQLite 备份工具"
    }
    $d1Backup = Join-Path $workingDirectory "source-d1.sqlite"
    $sqliteRun = Invoke-BoundedNativeProcess $Python @(
      $sqliteTool, "--source", $source, "--destination", $d1Backup
    )
    $sqliteOutput = @($sqliteRun.Output)
    if ($sqliteRun.ExitCode -ne 0 -or $sqliteOutput.Count -ne 1) {
      throw "D1 一致性备份失败；未发布备份目录（$(Get-NativeFailureSummary $sqliteRun)）"
    }
    try {
      $d1Evidence = [string]$sqliteOutput[0] | ConvertFrom-Json
    } catch {
      throw "D1 备份工具未返回有效证据"
    }
    if (
      [string]$d1Evidence.status -cne "completed" -or
      [string]$d1Evidence.quickCheck -cne "ok" -or
      [string]$d1Evidence.sha256 -cnotmatch "^[0-9a-f]{64}$"
    ) {
      throw "D1 备份证据未通过"
    }

    $r2Destination = Join-Path $workingDirectory "r2-state"
    Copy-Item -LiteralPath $r2Source -Destination $r2Destination -Recurse
    $r2Rows = @()
    $r2Bytes = [int64]0
    foreach ($file in @(Get-ChildItem -LiteralPath $r2Destination -File -Recurse | Sort-Object FullName)) {
      if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "R2 备份中出现重解析点"
      }
      $relative = $file.FullName.Substring($r2Destination.Length).TrimStart("\", "/").Replace("\", "/")
      $r2Bytes += [int64]$file.Length
      $r2Rows += [ordered]@{
        path = $relative
        sizeBytes = [int64]$file.Length
        sha256 = Get-FileSha256 $file.FullName
      }
    }
    if ($r2Rows.Count -lt 1 -or $r2Bytes -lt 1) {
      throw "R2 状态备份为空"
    }
    $r2Manifest = [ordered]@{
      version = "teruisi-r2-state-backup-v1"
      fileCount = $r2Rows.Count
      sizeBytes = $r2Bytes
      files = $r2Rows
    }
    $r2ManifestPath = Join-Path $workingDirectory "r2-manifest.json"
    Write-AtomicJson $r2ManifestPath $r2Manifest

    $secrets = Read-Secrets
    $postgresStarted = Start-Postgres
    $pgDump = Join-Path $PostgresBin "pg_dump.exe"
    $pgRestore = Join-Path $PostgresBin "pg_restore.exe"
    if (
      -not (Test-Path -LiteralPath $pgDump -PathType Leaf) -or
      -not (Test-Path -LiteralPath $pgRestore -PathType Leaf)
    ) {
      throw "缺少 PostgreSQL 逻辑备份工具"
    }
    $databaseBackup = Join-Path $workingDirectory "teruisi-sales.dump"
    $env:PGPASSWORD = $secrets.OwnerPassword
    $pgDumpRun = Invoke-BoundedNativeProcess $pgDump @(
      "--host=127.0.0.1", "--port=5432", "--username=teruisi_sales_owner",
      "--dbname=teruisi_sales", "--format=custom", "--compress=6",
      "--no-owner", "--no-privileges", "--lock-wait-timeout=5000",
      "--file=$databaseBackup"
    )
    if ($pgDumpRun.ExitCode -ne 0) {
      throw "PostgreSQL pg_dump 失败（$(Get-NativeFailureSummary $pgDumpRun)）"
    }
    $pgRestoreRun = Invoke-BoundedNativeProcess $pgRestore @("--list", $databaseBackup)
    $archiveList = @($pgRestoreRun.Output)
    if ($pgRestoreRun.ExitCode -ne 0 -or $archiveList.Count -lt 10) {
      throw "PostgreSQL 备份归档目录验证失败（$(Get-NativeFailureSummary $pgRestoreRun)）"
    }

    $env:TERUISI_BACKUP_DATABASE_URL = Database-Url (
      "teruisi_sales_owner"
    ) $secrets.OwnerPassword "teruisi_cutover_backup_evidence" $WriterStatementTimeoutMs
    $evidenceCode = @'
import json
import os
import psycopg

with psycopg.connect(os.environ["TERUISI_BACKUP_DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        counts = {}
        for table in ("sales_order_lines", "sales_import_batches", "erp_product_master"):
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            counts[table] = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT domain, revision FROM sales_data_revisions "
            "WHERE domain IN ('sales', 'erp') ORDER BY domain"
        )
        revisions = {str(row[0]): int(row[1]) for row in cursor.fetchall()}
        cursor.execute("SELECT to_regclass('public.sales_write_authority')")
        if cursor.fetchone()[0] is None:
            authority_status = "legacy_absent"
            authority_cutover_id = ""
        else:
            cursor.execute(
                "SELECT status, cutover_id FROM sales_write_authority WHERE id=1"
            )
            authority = cursor.fetchone()
            if authority is None:
                raise RuntimeError("sales_write_authority singleton is missing")
            authority_status = str(authority[0])
            authority_cutover_id = str(authority[1] or "")
print(json.dumps({
    "counts": counts,
    "revisions": revisions,
    "authorityStatus": authority_status,
    "authorityCutoverId": authority_cutover_id,
}, sort_keys=True, separators=(",", ":")))
'@
    $evidenceLauncher = ConvertTo-AsciiPythonLauncher $evidenceCode (
      "sales_cutover_backup_evidence.py"
    )
    $pgEvidenceRun = Invoke-BoundedNativeProcess $Python @("-c", $evidenceLauncher)
    $pgEvidenceOutput = @($pgEvidenceRun.Output)
    if ($pgEvidenceRun.ExitCode -ne 0 -or $pgEvidenceOutput.Count -ne 1) {
      throw "PostgreSQL 备份前证据查询失败（$(Get-NativeFailureSummary $pgEvidenceRun)）"
    }
    try {
      $pgEvidence = [string]$pgEvidenceOutput[0] | ConvertFrom-Json
    } catch {
      throw "PostgreSQL 备份证据 JSON 无效"
    }
    foreach ($table in @("sales_order_lines", "sales_import_batches", "erp_product_master")) {
      if ([int64]$pgEvidence.counts.$table -ne [int64]$d1Evidence.counts.$table) {
        throw "PostgreSQL 与 D1 备份前行数不一致：$table"
      }
    }
    if (
      [int64]$pgEvidence.revisions.sales -ne [int64]$d1Evidence.revisions.sales -or
      [int64]$pgEvidence.revisions.erp -ne [int64]$d1Evidence.revisions.erp
    ) {
      throw "PostgreSQL 与 D1 备份前 revision 不一致"
    }

    $backupManifest = [ordered]@{
      version = "teruisi-sales-cutover-backup-v1"
      cutoverId = $RequestedCutoverId
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
      sourcePathSha256 = Get-Sha256Text $source
      sourceD1 = $d1Evidence
      r2State = [ordered]@{
        manifestSha256 = Get-FileSha256 $r2ManifestPath
        fileCount = $r2Rows.Count
        sizeBytes = $r2Bytes
      }
      postgresql = [ordered]@{
        fileName = [IO.Path]::GetFileName($databaseBackup)
        sizeBytes = [int64](Get-Item -LiteralPath $databaseBackup).Length
        sha256 = Get-FileSha256 $databaseBackup
        archiveEntryCount = $archiveList.Count
        evidence = $pgEvidence
      }
      deploymentManifestSha256 = Get-FileSha256 $DeploymentManifestPath
      serviceConfigSha256 = Get-FileSha256 $ConfigPath
    }
    if (
      [int64]$backupManifest.postgresql.sizeBytes -lt 1 -or
      [string]$backupManifest.postgresql.sha256 -cnotmatch "^[0-9a-f]{64}$"
    ) {
      throw "PostgreSQL 备份文件为空或摘要无效"
    }
    $manifestPath = Join-Path $workingDirectory "backup-manifest.json"
    Write-AtomicJson $manifestPath $backupManifest
    [IO.File]::WriteAllText(
      (Join-Path $workingDirectory "backup-manifest.json.sha256"),
      (Get-FileSha256 $manifestPath) + [Environment]::NewLine,
      $Utf8NoBom
    )

    if ($postgresStarted) {
      Stop-Postgres
      $postgresStarted = $false
    }
    Move-Item -LiteralPath $workingDirectory -Destination $finalDirectory
    $backupPublished = $true
    return [ordered]@{
      status = "completed"
      version = "teruisi-sales-cutover-backup-result-v1"
      cutoverId = $RequestedCutoverId
      backupDirectory = $finalDirectory
      manifestSha256 = Get-FileSha256 (Join-Path $finalDirectory "backup-manifest.json")
    }
  } catch {
    $backupAttemptFailed = $true
    $backupFailureDigest = Get-Sha256Text (Protect-LogText $_.Exception.Message)
    throw
  } finally {
    [Environment]::SetEnvironmentVariable("PGPASSWORD", $previousPgPassword, "Process")
    [Environment]::SetEnvironmentVariable(
      "TERUISI_BACKUP_DATABASE_URL", $previousEvidenceUrl, "Process"
    )
    $secrets = $null
    if ($postgresStarted) {
      try { Stop-Postgres } catch {
        Write-LauncherEvent "ERROR" "cutover_backup_postgres_stop_failed" $_.Exception.Message
      }
    }
    if ($backupAttemptFailed -and -not $backupPublished) {
      $cleanupStatus = "not_present"
      $cleanupFailureDigest = ""
      try {
        if (Test-Path -LiteralPath $workingDirectory) {
          $workingCanonical = Get-CanonicalPath $workingDirectory
          if ((Get-CanonicalPath (Split-Path -Parent $workingCanonical)) -ine
                (Get-CanonicalPath $backupRoot) -or
              [IO.Path]::GetFileName($workingCanonical) -cne $expectedWorkingName -or
              [IO.Path]::GetFileName($workingCanonical) -cnotmatch
                "^\.sales-cutover-[0-9a-f]{24}\.[0-9a-f]{32}\.incomplete$" -or
              -not (Test-Path -LiteralPath $workingCanonical -PathType Container)) {
            throw "未发布备份工作目录身份无效；拒绝自动删除"
          }
          $directories = [Collections.Queue]::new()
          $rootItem = Get-Item -LiteralPath $workingCanonical -Force
          if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "未发布备份工作目录不得是重解析点"
          }
          $directories.Enqueue($rootItem)
          while ($directories.Count -gt 0) {
            $directory = $directories.Dequeue()
            foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force)) {
              if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "未发布备份工作目录不得包含重解析点"
              }
              if ($item.PSIsContainer) { $directories.Enqueue($item) }
            }
          }
          Remove-Item -LiteralPath $workingCanonical -Recurse -Force
          if (Test-Path -LiteralPath $workingCanonical) {
            throw "未发布备份工作目录删除后仍存在"
          }
          $cleanupStatus = "cleaned"
        }
      } catch {
        $cleanupStatus = "cleanup_failed"
        $cleanupFailureDigest = Get-Sha256Text (Protect-LogText $_.Exception.Message)
        Write-LauncherEvent "ERROR" "cutover_backup_unpublished_cleanup_failed" $_.Exception.Message
      }
      try {
        $cleanupAuditRoot = Assert-RuntimeChildPath (
          Join-Path $ServiceRuntime "audits\backup-attempts"
        )
        New-Item -ItemType Directory -Path $cleanupAuditRoot -Force | Out-Null
        $cleanupAuditPath = Assert-RuntimeChildPath (
          Join-Path $cleanupAuditRoot "$workingNonce.json"
        )
        Write-AtomicJson $cleanupAuditPath ([ordered]@{
          version = "teruisi-sales-cutover-unpublished-cleanup-v1"
          status = $cleanupStatus
          cutoverIdSha256 = Get-Sha256Text $RequestedCutoverId
          workingDirectoryNameSha256 = Get-Sha256Text $expectedWorkingName
          deploymentManifestSha256 = Get-FileSha256 $DeploymentManifestPath
          failureMessageSha256 = $backupFailureDigest
          cleanupFailureMessageSha256 = $cleanupFailureDigest
          recordedAt = [DateTimeOffset]::UtcNow.ToString("o")
        })
      } catch {
        Write-LauncherEvent "ERROR" "cutover_backup_cleanup_audit_failed" $_.Exception.Message
      }
    }
  }
  }
} $installedScript $canonicalRuntime $SourceD1 $R2PersistTo $CutoverId

$result | ConvertTo-Json -Compress
