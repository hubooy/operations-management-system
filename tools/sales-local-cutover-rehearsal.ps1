[CmdletBinding()]
param(
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$BackupDirectory = "",
  [string]$ApprovedBackupManifestSha256 = "",
  [string]$ApprovedRehearsalResultSha256 = "",
  [Parameter(Mandatory = $true)]
  [string]$RehearsalId,
  [switch]$Execute,
  [switch]$ConfirmedIsolatedRehearsal,
  [switch]$CleanupFailedRehearsal,
  [switch]$ConfirmedFailedRehearsalCleanup,
  [switch]$AbandonCompletedRehearsal,
  [switch]$ConfirmedAbandonBeforeForwardRecovery
)

$ErrorActionPreference = "Stop"
$FixedRuntimeRoot = "D:\teruisi-runtime\django-sales"
$MaxBackupAgeMinutes = 360
$FixedFreeSpaceReserveBytes = [int64](8GB)
$MinimumPostgresWorkingBytes = [int64](4GB)
$PostgresDumpExpansionFactor = [int64]16

function Test-FullyQualifiedPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  try { [void][IO.Path]::GetFullPath($Path) } catch { return $false }
  if ([IO.Path]::DirectorySeparatorChar -eq "\") {
    return $Path -match "^[A-Za-z]:[\\/]" -or
      $Path -match "^[\\/]{2}[^\\/]+[\\/]+[^\\/]+(?:[\\/]|$)"
  }
  return $Path.StartsWith("/", [StringComparison]::Ordinal)
}

if ($RehearsalId -cnotmatch "^[0-9a-f]{12}$") {
  throw "RehearsalId 必须是 12 位小写十六进制标识"
}
if ($CleanupFailedRehearsal.IsPresent -and $AbandonCompletedRehearsal.IsPresent) {
  throw "失败演练清理与 completed 演练放弃模式互斥"
}
if ($CleanupFailedRehearsal.IsPresent) {
  if (-not $Execute.IsPresent -or
      -not $ConfirmedFailedRehearsalCleanup.IsPresent -or
      $ConfirmedIsolatedRehearsal.IsPresent -or
      $ConfirmedAbandonBeforeForwardRecovery.IsPresent -or
      -not [string]::IsNullOrWhiteSpace($BackupDirectory) -or
      -not [string]::IsNullOrWhiteSpace($ApprovedBackupManifestSha256) -or
      -not [string]::IsNullOrWhiteSpace($ApprovedRehearsalResultSha256)) {
    throw "失败演练清理必须仅显式提供 -Execute、-CleanupFailedRehearsal 与 -ConfirmedFailedRehearsalCleanup"
  }
} elseif ($AbandonCompletedRehearsal.IsPresent) {
  if (-not $Execute.IsPresent -or
      -not $ConfirmedAbandonBeforeForwardRecovery.IsPresent -or
      $ConfirmedIsolatedRehearsal.IsPresent -or
      $ConfirmedFailedRehearsalCleanup.IsPresent -or
      -not [string]::IsNullOrWhiteSpace($BackupDirectory) -or
      $ApprovedBackupManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      $ApprovedRehearsalResultSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "放弃 completed 演练必须显式提供 -Execute、-AbandonCompletedRehearsal、-ConfirmedAbandonBeforeForwardRecovery 与两个批准 SHA-256"
  }
} else {
  if (-not $Execute.IsPresent -or -not $ConfirmedIsolatedRehearsal.IsPresent -or
      $ConfirmedFailedRehearsalCleanup.IsPresent -or
      $ConfirmedAbandonBeforeForwardRecovery.IsPresent -or
      -not [string]::IsNullOrWhiteSpace($ApprovedRehearsalResultSha256)) {
    throw "隔离演练必须显式提供 -Execute 与 -ConfirmedIsolatedRehearsal"
  }
  if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    throw "隔离演练必须提供 BackupDirectory"
  }
  if ($ApprovedBackupManifestSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "ApprovedBackupManifestSha256 必须是 64 位小写 SHA-256"
  }
}
if (-not (Test-FullyQualifiedPath $RuntimeRoot)) {
  throw "RuntimeRoot 必须是绝对路径"
}

$canonicalRuntime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd("\", "/")
if ($canonicalRuntime -ine $FixedRuntimeRoot) {
  throw "隔离演练只允许固定受保护 Django runtime"
}
$serviceScript = Join-Path $canonicalRuntime "app\tools\django-local-service.ps1"
$expectedSelf = Join-Path $canonicalRuntime "app\tools\sales-local-cutover-rehearsal.ps1"
if (-not (Test-Path -LiteralPath $serviceScript -PathType Leaf) -or
    [IO.Path]::GetFullPath($PSCommandPath).TrimEnd("\", "/") -ine
    [IO.Path]::GetFullPath($expectedSelf).TrimEnd("\", "/")) {
  throw "隔离演练只能从受保护的 runtime app operator 执行"
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

function Assert-ExactPropertySet(
  [object]$Value,
  [string[]]$Expected,
  [string]$Label
) {
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

function Assert-RetirementPlanBlockersClear([object]$Blockers) {
  $expectedNames = @(
    "processingBatches",
    "activeUploads",
    "invalidUploadExpiries",
    "uploadChunks",
    "processingFingerprints",
    "processingScopeHeads",
    "processingAttempts"
  )
  Assert-ExactPropertySet $Blockers $expectedNames "隔离 D1 retirement blockers"
  $integerTypeCodes = @(
    [TypeCode]::SByte,
    [TypeCode]::Byte,
    [TypeCode]::Int16,
    [TypeCode]::UInt16,
    [TypeCode]::Int32,
    [TypeCode]::UInt32,
    [TypeCode]::Int64,
    [TypeCode]::UInt64
  )
  foreach ($name in $expectedNames) {
    $value = $Blockers.PSObject.Properties[$name].Value
    if ($null -eq $value -or
        [Type]::GetTypeCode($value.GetType()) -notin $integerTypeCodes -or
        [decimal]$value -ne 0) {
      throw "隔离 D1 retirement blocker 必须全部为整数 0"
    }
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

function Resolve-DirectChildDirectory(
  [string]$Path,
  [string]$Parent,
  [string]$Label
) {
  if (-not (Test-FullyQualifiedPath $Path)) { throw "$Label 必须是绝对路径" }
  $resolved = Get-CanonicalPath $Path
  $resolvedParent = Get-CanonicalPath $Parent
  if (-not (Test-Path -LiteralPath $resolved -PathType Container) -or
      (Get-CanonicalPath (Split-Path -Parent $resolved)) -ine $resolvedParent) {
    throw "$Label 必须是受控目录的直接子目录"
  }
  Assert-NoReparsePoints $resolved $Label
  return $resolved
}

function Add-CheckedBytes([int64]$Left, [int64]$Right, [string]$Label) {
  if ($Left -lt 0 -or $Right -lt 0 -or $Left -gt ([int64]::MaxValue - $Right)) {
    throw "$Label 字节预算溢出"
  }
  return [int64]($Left + $Right)
}

function Multiply-CheckedBytes([int64]$Value, [int64]$Factor, [string]$Label) {
  if ($Value -lt 0 -or $Factor -lt 1 -or $Value -gt [math]::Floor([int64]::MaxValue / $Factor)) {
    throw "$Label 字节预算溢出"
  }
  return [int64]($Value * $Factor)
}

function Get-RehearsalDiskCapacityEvidence(
  [object]$Backup,
  [string]$RehearsalParent
) {
  $d1Bytes = [int64]$Backup.Manifest.sourceD1.sizeBytes
  $r2Bytes = [int64]$Backup.Manifest.r2State.sizeBytes
  $dumpBytes = [int64]$Backup.Manifest.postgresql.sizeBytes
  if ($d1Bytes -lt 1 -or $r2Bytes -lt 1 -or $dumpBytes -lt 1) {
    throw "隔离演练容量预算缺少正数来源证据"
  }
  $sourcePayloadBytes = Add-CheckedBytes $d1Bytes $r2Bytes "D1/R2 payload"
  $expandedDumpBytes = Multiply-CheckedBytes `
    $dumpBytes $PostgresDumpExpansionFactor "PostgreSQL dump expansion"
  $postgresRestoreReserveBytes = [int64][math]::Max(
    [double]$d1Bytes,
    [double]$expandedDumpBytes
  )
  $postgresWorkBaseBytes = Add-CheckedBytes `
    $d1Bytes $postgresRestoreReserveBytes "PostgreSQL working base"
  $postgresWorkingBytes = [int64][math]::Max(
    [double]$MinimumPostgresWorkingBytes,
    [math]::Ceiling([double]$postgresWorkBaseBytes / 4.0)
  )
  $requiredBytes = Add-CheckedBytes `
    $sourcePayloadBytes $postgresRestoreReserveBytes "rehearsal payload and PG restore"
  $requiredBytes = Add-CheckedBytes `
    $requiredBytes $postgresWorkingBytes "rehearsal PG working reserve"
  $requiredBytes = Add-CheckedBytes `
    $requiredBytes $FixedFreeSpaceReserveBytes "rehearsal fixed free-space floor"

  $targetRoot = [IO.Path]::GetPathRoot((Get-CanonicalPath $RehearsalParent))
  $postgresRoot = [IO.Path]::GetPathRoot((Get-CanonicalPath $PostgresData))
  if ([string]::IsNullOrWhiteSpace($targetRoot) -or $targetRoot -ine $postgresRoot) {
    throw "隔离演练 payload 与 PostgreSQL 数据目录必须位于同一受控卷"
  }
  $drive = [IO.DriveInfo]::new($targetRoot)
  if (-not $drive.IsReady) { throw "隔离演练目标卷未就绪" }
  $availableBytes = [int64]$drive.AvailableFreeSpace
  if ($availableBytes -lt $requiredBytes) {
    throw "隔离演练目标卷可用空间不足；已在 D1/R2 复制和 PostgreSQL restore 前失败关闭"
  }
  return [ordered]@{
    volumeNameSha256 = Get-Sha256Text $drive.Name.ToUpperInvariant()
    availableBytes = $availableBytes
    requiredBytes = $requiredBytes
    sourcePayloadBytes = $sourcePayloadBytes
    postgresRestoreReserveBytes = $postgresRestoreReserveBytes
    postgresWorkingBytes = $postgresWorkingBytes
    fixedFreeSpaceReserveBytes = $FixedFreeSpaceReserveBytes
  }
}

function Resolve-ExactRehearsalRoot([string]$Id, [bool]$MustExist) {
  if ($Id -cnotmatch "^[0-9a-f]{12}$") { throw "RehearsalId 身份无效" }
  $parent = Assert-RuntimeChildPath (Join-Path $canonicalRuntime "rehearsals")
  $root = Assert-RuntimeChildPath (Join-Path $parent $Id)
  if ((Get-CanonicalPath (Split-Path -Parent $root)) -ine (Get-CanonicalPath $parent) -or
      [IO.Path]::GetFileName($root) -cne $Id) {
    throw "演练目录不是固定 rehearsals 下的精确 ID 直接子目录"
  }
  if ($MustExist) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
      throw "指定失败演练目录不存在"
    }
    $rootItem = Get-Item -LiteralPath $root -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "失败演练根目录不得是重解析点"
    }
  }
  return [pscustomobject]@{ Parent = $parent; Root = $root }
}

function Get-RehearsalPayloadTargets([string]$RehearsalRoot) {
  $root = Get-CanonicalPath $RehearsalRoot
  $definitions = @(
    [pscustomobject]@{ RelativePath = ".wrangler"; Kind = "directory" },
    [pscustomobject]@{ RelativePath = "r2-state"; Kind = "directory" },
    [pscustomobject]@{ RelativePath = "source-d1.sqlite"; Kind = "file" },
    [pscustomobject]@{ RelativePath = "teruisi-sales.dump"; Kind = "file" },
    [pscustomobject]@{ RelativePath = "postgresql-restore.dump"; Kind = "file" }
  )
  return @($definitions | ForEach-Object {
    $candidate = Get-CanonicalPath (Join-Path $root $_.RelativePath)
    if (-not $candidate.StartsWith(
        $root + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      ) -or (Get-CanonicalPath (Split-Path -Parent $candidate)) -ine $root) {
      throw "演练 payload 清理目标越界"
    }
    [pscustomobject]@{
      RelativePath = [string]$_.RelativePath
      Kind = [string]$_.Kind
      FullPath = $candidate
    }
  })
}

function Get-PathPayloadSize([string]$Path) {
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    return [int64](Get-Item -LiteralPath $Path -Force).Length
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return [int64]0 }
  $total = [int64]0
  foreach ($file in @(Get-ChildItem -LiteralPath $Path -File -Force -Recurse)) {
    $total = Add-CheckedBytes $total ([int64]$file.Length) "rehearsal payload size"
  }
  return $total
}

function Remove-ValidatedRehearsalPayload([string]$RehearsalRoot) {
  $rows = @()
  $removedBytes = [int64]0
  foreach ($target in @(Get-RehearsalPayloadTargets $RehearsalRoot)) {
    $exists = Test-Path -LiteralPath $target.FullPath
    $bytes = [int64]0
    if ($exists) {
      $item = Get-Item -LiteralPath $target.FullPath -Force
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "演练 payload 清理拒绝重解析点：$($target.RelativePath)"
      }
      if (($target.Kind -ceq "directory") -ne [bool]$item.PSIsContainer) {
        throw "演练 payload 清理目标类型不匹配：$($target.RelativePath)"
      }
      Assert-NoReparsePoints $target.FullPath "演练 payload $($target.RelativePath)"
      $bytes = Get-PathPayloadSize $target.FullPath
      if ($target.Kind -ceq "directory") {
        Remove-Item -LiteralPath $target.FullPath -Recurse -Force
      } else {
        Remove-Item -LiteralPath $target.FullPath -Force
      }
      if (Test-Path -LiteralPath $target.FullPath) {
        throw "演练 payload 删除后仍存在：$($target.RelativePath)"
      }
      $removedBytes = Add-CheckedBytes $removedBytes $bytes "removed rehearsal payload"
    }
    $rows += [ordered]@{
      relativePath = [string]$target.RelativePath
      existed = [bool]$exists
      removed = [bool]$exists
      sizeBytes = $bytes
    }
  }
  return [ordered]@{ removedBytes = $removedBytes; targets = $rows }
}

function Read-ExactSha256File([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label 缺失" }
  $value = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8).Trim()
  if ($value -cnotmatch "^[0-9a-f]{64}$") { throw "$Label 无效" }
  return $value
}

function Assert-R2TreeMatchesManifest(
  [string]$R2Root,
  [object]$R2Manifest,
  [string]$Label
) {
  Assert-ExactPropertySet $R2Manifest @(
    "version", "fileCount", "sizeBytes", "files"
  ) "$Label manifest"
  if ([string]$R2Manifest.version -cne "teruisi-r2-state-backup-v1" -or
      [int64]$R2Manifest.fileCount -lt 1 -or
      [int64]$R2Manifest.sizeBytes -lt 1 -or
      @($R2Manifest.files).Count -ne [int]$R2Manifest.fileCount) {
    throw "$Label manifest 身份或总量无效"
  }
  Assert-NoReparsePoints $R2Root $Label
  $expected = @{}
  $expectedBytes = [int64]0
  foreach ($row in @($R2Manifest.files)) {
    Assert-ExactPropertySet $row @("path", "sizeBytes", "sha256") "$Label 文件证据"
    $relative = [string]$row.path
    if ([string]::IsNullOrWhiteSpace($relative) -or
        (Test-FullyQualifiedPath $relative) -or
        $relative.Contains("\") -or
        @($relative.Split("/") | Where-Object { $_ -in @("", ".", "..") }).Count -gt 0 -or
        [string]$row.sha256 -cnotmatch "^[0-9a-f]{64}$" -or
        [int64]$row.sizeBytes -lt 0 -or
        $expected.ContainsKey($relative)) {
      throw "$Label 文件证据包含越界、重复或无效路径"
    }
    $expected[$relative] = $row
    $expectedBytes += [int64]$row.sizeBytes
  }
  if ($expectedBytes -ne [int64]$R2Manifest.sizeBytes) {
    throw "$Label manifest 字节总量不一致"
  }
  $actualFiles = @(Get-ChildItem -LiteralPath $R2Root -File -Recurse)
  if ($actualFiles.Count -ne $expected.Count) { throw "$Label 文件集合不完整" }
  foreach ($file in $actualFiles) {
    $relative = $file.FullName.Substring($R2Root.Length).TrimStart("\", "/").Replace("\", "/")
    if (-not $expected.ContainsKey($relative)) { throw "$Label 出现未签收文件" }
    $evidence = $expected[$relative]
    if ([int64]$file.Length -ne [int64]$evidence.sizeBytes -or
        (Get-FileSha256 $file.FullName) -cne [string]$evidence.sha256) {
      throw "$Label 文件大小或 SHA-256 不一致"
    }
  }
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

function Invoke-JsonProcess(
  [string]$Executable,
  [string[]]$Arguments,
  [string]$WorkingDirectory,
  [string]$Label
) {
  $run = Invoke-BoundedNativeProcess $Executable $Arguments $WorkingDirectory
  $output = @($run.Output)
  if ($run.ExitCode -ne 0) {
    throw "$Label 失败（$(Get-NativeFailureSummary $run)）"
  }
  $jsonLines = @()
  foreach ($line in $output) {
    try {
      $jsonLines += ([string]$line | ConvertFrom-Json)
    } catch {
      # Django migration progress may precede the final command JSON.
    }
  }
  if ($jsonLines.Count -ne 1) {
    throw "$Label 未返回唯一 JSON 结果（$(Get-NativeFailureSummary $run)）"
  }
  return $jsonLines[0]
}

function Invoke-PythonJsonCode(
  [string]$Code,
  [string]$SourceName,
  [string]$WorkingDirectory,
  [string]$Label
) {
  $launcher = ConvertTo-AsciiPythonLauncher $Code $SourceName
  return Invoke-JsonProcess $Python @("-c", $launcher) $WorkingDirectory $Label
}

function Set-RehearsalStateValue([object]$State, [string]$Name, [object]$Value) {
  if ($State -is [Collections.IDictionary]) {
    $State[$Name] = $Value
    return
  }
  $property = $State.PSObject.Properties[$Name]
  if ($null -eq $property) {
    $State | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
  } else {
    $property.Value = $Value
  }
}

function Invoke-RehearsalDatabaseDisposition(
  [string]$DatabaseName,
  [bool]$AllowDrop
) {
  if ($DatabaseName -cnotmatch "^teruisi_sales_rehearsal_[0-9a-f]{12}$" -or
      $DatabaseName -ceq "teruisi_sales") {
    throw "演练数据库清理名称不符合固定白名单"
  }
  if ([string]::IsNullOrWhiteSpace($env:TERUISI_REHEARSAL_ADMIN_DATABASE_URL)) {
    throw "演练数据库清理缺少受控本机 admin 连接"
  }
  $previousDatabaseName = [Environment]::GetEnvironmentVariable(
    "TERUISI_REHEARSAL_DATABASE_NAME", "Process"
  )
  $previousAllowDrop = [Environment]::GetEnvironmentVariable(
    "TERUISI_REHEARSAL_ALLOW_DROP", "Process"
  )
  try {
    $env:TERUISI_REHEARSAL_DATABASE_NAME = $DatabaseName
    $env:TERUISI_REHEARSAL_ALLOW_DROP = if ($AllowDrop) { "1" } else { "0" }
    $dropScript = @'
import json
import os
import re

import psycopg
from psycopg import sql

name = os.environ["TERUISI_REHEARSAL_DATABASE_NAME"]
allow_drop = os.environ.get("TERUISI_REHEARSAL_ALLOW_DROP") == "1"
if not re.fullmatch(r"teruisi_sales_rehearsal_[0-9a-f]{12}", name) or name == "teruisi_sales":
    raise RuntimeError("rehearsal database name rejected")
connection = psycopg.connect(os.environ["TERUISI_REHEARSAL_ADMIN_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute("SELECT current_user, current_database()")
    identity = cursor.fetchone()
    if identity != ("postgres", "postgres"):
        raise RuntimeError("rehearsal cleanup admin identity rejected")
    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    existed = cursor.fetchone() is not None
    if existed and not allow_drop:
        raise RuntimeError("rehearsal database exists without exact ownership proof")
    if existed:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        cursor.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )
    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    absent = cursor.fetchone() is None
connection.close()
if not absent:
    raise RuntimeError("rehearsal database drop readback failed")
print(json.dumps({
    "status": "absent" if not existed else "dropped",
    "existed": existed,
    "dropped": existed,
    "verifiedAbsent": absent,
}, separators=(",", ":")))
'@
    return Invoke-PythonJsonCode $dropScript (
      "sales_cutover_rehearsal_database_cleanup.py"
    ) $RuntimeRoot "PostgreSQL exact rehearsal database cleanup"
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_REHEARSAL_DATABASE_NAME", $previousDatabaseName, "Process"
    )
    [Environment]::SetEnvironmentVariable(
      "TERUISI_REHEARSAL_ALLOW_DROP", $previousAllowDrop, "Process"
    )
  }
}

function Invoke-RehearsalPayloadCleanup(
  [object]$State,
  [string]$StatePath,
  [string]$RehearsalRoot,
  [string]$DatabaseName,
  [bool]$DatabaseMayExist,
  [bool]$AllowDatabaseDrop,
  [object[]]$InitialErrors = @()
) {
  $root = Get-CanonicalPath $RehearsalRoot
  $expectedStatePath = Get-CanonicalPath (Join-Path $root "rehearsal-state.json")
  if ((Get-CanonicalPath $StatePath) -ine $expectedStatePath -or
      -not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "演练 payload 清理 state/root 身份不一致"
  }
  $auditPath = Get-CanonicalPath (Join-Path $root "payload-cleanup-audit.json")
  if ((Get-CanonicalPath (Split-Path -Parent $auditPath)) -ine $root) {
    throw "演练 payload 清理 audit 路径越界"
  }
  if (Test-Path -LiteralPath $auditPath) {
    $auditItem = Get-Item -LiteralPath $auditPath -Force
    if ($auditItem.PSIsContainer -or
        ($auditItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "演练 payload 清理 audit 必须是根目录内固定普通文件"
    }
  }

  Set-RehearsalStateValue $State "payloadDisposition" "unresolved"
  Set-RehearsalStateValue $State "payloadCleanupAuditPath" ""
  Set-RehearsalStateValue $State "payloadCleanupAuditSha256" ""
  Write-AtomicJson $StatePath $State

  $startedAt = [DateTimeOffset]::UtcNow.ToString("o")
  $errors = @($InitialErrors)
  $databaseResult = [ordered]@{
    name = $DatabaseName
    status = if ($DatabaseMayExist) { "pending" } else { "not_created_by_attempt" }
    existed = $false
    dropped = $false
    verifiedAbsent = -not $DatabaseMayExist
  }
  if ($DatabaseMayExist -and $errors.Count -eq 0) {
    try {
      $database = Invoke-RehearsalDatabaseDisposition $DatabaseName $AllowDatabaseDrop
      $databaseResult = [ordered]@{
        name = $DatabaseName
        status = [string]$database.status
        existed = [bool]$database.existed
        dropped = [bool]$database.dropped
        verifiedAbsent = [bool]$database.verifiedAbsent
      }
    } catch {
      $errors += [ordered]@{
        stage = "database"
        failureClass = $_.Exception.GetType().FullName
        failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
      }
    }
  }

  # Filesystem payloads are independent copies of the immutable backup.  Reclaim
  # them even if PostgreSQL cleanup needs a later retry, while keeping the
  # overall disposition unresolved until both sides verify absent.  A process
  # stop/maintenance failure is different: a surviving owned process may still
  # hold or mutate these files, so that case remains fail-closed for deletion.
  $payloadResult = $null
  $unsafeFilesystemErrors = @($errors | Where-Object {
    [string]$_.stage -in @("owned_process_stop", "maintenance_precondition")
  })
  if ($unsafeFilesystemErrors.Count -eq 0) {
    try {
      $payloadResult = Remove-ValidatedRehearsalPayload $root
    } catch {
      $errors += [ordered]@{
        stage = "filesystem"
        failureClass = $_.Exception.GetType().FullName
        failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
      }
    }
  }
  $payloadsAbsent = @(
    Get-RehearsalPayloadTargets $root | Where-Object {
      Test-Path -LiteralPath $_.FullPath
    }
  ).Count -eq 0
  if (-not $payloadsAbsent -and
      @($errors | Where-Object { [string]$_.stage -ceq "filesystem" }).Count -eq 0) {
    $errors += [ordered]@{
      stage = "filesystem_readback"
      failureClass = "RehearsalPayloadReadbackError"
      failureMessageSha256 = Get-Sha256Text "rehearsal payload remains after cleanup"
    }
  }
  $completed = (
    $errors.Count -eq 0 -and
    [bool]$databaseResult.verifiedAbsent -and
    $payloadsAbsent
  )
  $completedAt = [DateTimeOffset]::UtcNow.ToString("o")
  $audit = [ordered]@{
    version = "teruisi-sales-rehearsal-payload-cleanup-v1"
    status = if ($completed) { "completed" } else { "failed" }
    rehearsalId = $RehearsalId
    backupManifestSha256 = [string]$State.backupManifestSha256
    databaseName = $DatabaseName
    startedAt = $startedAt
    completedAt = $completedAt
    database = $databaseResult
    payload = if ($null -eq $payloadResult) {
      [ordered]@{ removedBytes = [int64]0; targets = @(); verifiedAbsent = $payloadsAbsent }
    } else {
      [ordered]@{
        removedBytes = [int64]$payloadResult.removedBytes
        targets = @($payloadResult.targets)
        verifiedAbsent = $payloadsAbsent
      }
    }
    errors = @($errors)
  }
  Write-AtomicJson $auditPath $audit
  $auditItem = Get-Item -LiteralPath $auditPath -Force
  if ($auditItem.PSIsContainer -or
      ($auditItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "演练 payload 清理 audit 写入后不是普通文件"
  }
  $auditSha256 = Get-FileSha256 $auditPath
  if ($auditSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "演练 payload 清理 audit SHA-256 无效"
  }
  Set-RehearsalStateValue $State "updatedAt" $completedAt
  Set-RehearsalStateValue $State "databaseRetained" (-not [bool]$databaseResult.verifiedAbsent)
  Set-RehearsalStateValue $State "payloadCleanupAuditPath" $auditPath
  Set-RehearsalStateValue $State "payloadCleanupAuditSha256" $auditSha256
  $payloadDisposition = if ($completed) { "cleaned" } else { "unresolved" }
  Set-RehearsalStateValue $State "payloadDisposition" $payloadDisposition
  Write-AtomicJson $StatePath $State
  return [pscustomobject]@{
    Completed = $completed
    AuditPath = $auditPath
    AuditSha256 = $auditSha256
    ErrorCount = $errors.Count
  }
}

function Add-RehearsalStep(
  [Collections.IDictionary]$State,
  [string]$StatePath,
  [string]$Name,
  [object]$Result
) {
  if (@($State.steps | Where-Object { [string]$_.name -ceq $Name }).Count -gt 0) {
    throw "隔离演练步骤重复：$Name"
  }
  $completedAt = [DateTimeOffset]::UtcNow.ToString("o")
  $State.steps = @($State.steps) + @([ordered]@{
    name = $Name
    completedAt = $completedAt
    result = $Result
  })
  $State.updatedAt = $completedAt
  Write-AtomicJson $StatePath $State
}

function Invoke-DjangoJson(
  [object]$Secrets,
  [string]$DatabaseUrl,
  [string]$ProcessRole,
  [bool]$ExpectReadOnly,
  [int]$BodyBytes,
  [string]$AuthorityEpoch,
  [string]$CutoverId,
  [string[]]$Arguments,
  [string]$Label
) {
  $manage = Join-Path $BackendRoot "manage.py"
  return Invoke-WithDjangoEnvironment $Secrets $DatabaseUrl $ProcessRole `
    $ExpectReadOnly $BodyBytes $AuthorityEpoch $CutoverId {
      Invoke-JsonProcess $Python (@($manage) + @($Arguments)) $BackendRoot $Label
    }
}

function Wait-RehearsalErpCaughtUp(
  [object]$Secrets,
  [string]$ErpUrl,
  [string]$Source,
  [string]$BaselineCheckedAt = "",
  [int]$Seconds = 45
) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    try {
      $status = Invoke-DjangoJson $Secrets $ErpUrl "erp_reference_sync" $false `
        $ReaderMaxBodyBytes "" "" @(
          "sync_erp_reference", "--source", $Source,
          "--status", "--max-age-seconds", "60"
        ) "ERP rehearsal status"
      if ([string]$status.status -ceq "caught_up" -and
          -not [string]::IsNullOrWhiteSpace([string]$status.lastCheckedAt) -and
          ([string]::IsNullOrWhiteSpace($BaselineCheckedAt) -or
           [DateTimeOffset]::Parse([string]$status.lastCheckedAt).ToUniversalTime() -gt
           [DateTimeOffset]::Parse($BaselineCheckedAt).ToUniversalTime())) {
        return $status
      }
    } catch {
      # The watch process may still be establishing its first transaction.
    }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  throw "ERP rehearsal sync 未在时限内产生新的 caught_up 心跳"
}

function Assert-BackupAndReturnEvidence(
  [string]$RequestedDirectory,
  [bool]$RequireFresh = $true
) {
  $backupRoot = Assert-RuntimeChildPath (Join-Path $canonicalRuntime "backups")
  $backup = Resolve-DirectChildDirectory $RequestedDirectory $backupRoot "销售切换备份"
  if ([IO.Path]::GetFileName($backup) -cnotmatch "^sales-cutover-[0-9a-f]{24}$") {
    throw "销售切换备份目录名称无效"
  }
  $manifestPath = Join-Path $backup "backup-manifest.json"
  $manifestShaPath = Join-Path $backup "backup-manifest.json.sha256"
  $manifestSha = Get-FileSha256 $manifestPath
  if ($manifestSha -cnotmatch "^[0-9a-f]{64}$" -or
      $manifestSha -cne (Read-ExactSha256File $manifestShaPath "备份清单 SHA-256") -or
      $manifestSha -cne $ApprovedBackupManifestSha256) {
    throw "备份清单未通过显式批准与 SHA-256 复验"
  }
  $manifest = Read-JsonFile $manifestPath "销售切换备份清单"
  Assert-ExactPropertySet $manifest @(
    "version", "cutoverId", "createdAt", "sourcePathSha256", "sourceD1",
    "r2State", "postgresql", "deploymentManifestSha256", "serviceConfigSha256"
  ) "销售切换备份清单"
  if ([string]$manifest.version -cne "teruisi-sales-cutover-backup-v1" -or
      [string]$manifest.cutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$" -or
      [string]$manifest.sourcePathSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "销售切换备份清单身份无效"
  }
  $expectedDirectoryName = "sales-cutover-$((Get-Sha256Text ([string]$manifest.cutoverId)).Substring(0, 24))"
  if ([IO.Path]::GetFileName($backup) -cne $expectedDirectoryName) {
    throw "销售切换备份目录与 cutoverId 不一致"
  }
  $createdAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse(
      [string]$manifest.createdAt,
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::RoundtripKind,
      [ref]$createdAt
    )) {
    throw "销售切换备份 createdAt 无效"
  }
  $age = [DateTimeOffset]::UtcNow - $createdAt.ToUniversalTime()
  if ($age.TotalMinutes -lt -5 -or
      ($RequireFresh -and $age.TotalMinutes -gt $MaxBackupAgeMinutes)) {
    throw "销售切换备份不在固定新鲜度窗口内"
  }
  if ([string]$manifest.deploymentManifestSha256 -cne (Get-FileSha256 $DeploymentManifestPath) -or
      [string]$manifest.serviceConfigSha256 -cne (Get-FileSha256 $ConfigPath)) {
    throw "备份不属于当前受保护部署与服务配置"
  }
  $currentConfig = Get-ServiceConfig
  $currentProductionSource = Resolve-ErpSourceD1 ([string]$currentConfig.erpSourceD1)
  if ([string]$manifest.sourcePathSha256 -cne (Get-Sha256Text $currentProductionSource)) {
    throw "备份 sourcePathSha256 与当前 runtime 生产 D1 路径不一致"
  }

  Assert-ExactPropertySet $manifest.sourceD1 @(
    "status", "version", "destinationName", "sizeBytes", "sha256",
    "quickCheck", "counts", "revisions"
  ) "D1 备份证据"
  Assert-ExactPropertySet $manifest.sourceD1.counts @(
    "sales_order_lines", "sales_import_batches", "erp_product_master",
    "sales_import_uploads", "sales_import_upload_chunks"
  ) "D1 备份行数证据"
  Assert-ExactPropertySet $manifest.sourceD1.revisions @("sales", "erp") (
    "D1 备份 revision 证据"
  )
  foreach ($countName in @(
    "sales_order_lines", "sales_import_batches", "erp_product_master",
    "sales_import_uploads", "sales_import_upload_chunks"
  )) {
    if ([int64]$manifest.sourceD1.counts.$countName -lt 0) {
      throw "D1 备份行数证据无效"
    }
  }
  foreach ($revisionName in @("sales", "erp")) {
    if ([int64]$manifest.sourceD1.revisions.$revisionName -lt 0) {
      throw "D1 备份 revision 证据无效"
    }
  }
  $d1Backup = Join-Path $backup "source-d1.sqlite"
  if ([string]$manifest.sourceD1.status -cne "completed" -or
      [string]$manifest.sourceD1.version -cne "teruisi-sqlite-backup-v1" -or
      [string]$manifest.sourceD1.destinationName -cne "source-d1.sqlite" -or
      [string]$manifest.sourceD1.quickCheck -cne "ok" -or
      [int64]$manifest.sourceD1.sizeBytes -lt 1 -or
      [string]$manifest.sourceD1.sha256 -cnotmatch "^[0-9a-f]{64}$" -or
      -not (Test-Path -LiteralPath $d1Backup -PathType Leaf) -or
      [int64](Get-Item -LiteralPath $d1Backup).Length -ne [int64]$manifest.sourceD1.sizeBytes -or
      (Get-FileSha256 $d1Backup) -cne [string]$manifest.sourceD1.sha256) {
    throw "D1 备份文件或一致性证据无效"
  }

  Assert-ExactPropertySet $manifest.r2State @("manifestSha256", "fileCount", "sizeBytes") "R2 备份证据"
  $r2ManifestPath = Join-Path $backup "r2-manifest.json"
  if ([string]$manifest.r2State.manifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      (Get-FileSha256 $r2ManifestPath) -cne [string]$manifest.r2State.manifestSha256) {
    throw "R2 备份 manifest SHA-256 无效"
  }
  $r2Manifest = Read-JsonFile $r2ManifestPath "R2 备份 manifest"
  if ([int64]$manifest.r2State.fileCount -ne [int64]$r2Manifest.fileCount -or
      [int64]$manifest.r2State.sizeBytes -ne [int64]$r2Manifest.sizeBytes) {
    throw "R2 备份总量证据不一致"
  }
  $r2Backup = Join-Path $backup "r2-state"
  Assert-R2TreeMatchesManifest $r2Backup $r2Manifest "R2 备份"

  Assert-ExactPropertySet $manifest.postgresql @(
    "fileName", "sizeBytes", "sha256", "archiveEntryCount", "evidence"
  ) "PostgreSQL 备份证据"
  if ([string]$manifest.postgresql.fileName -cne "teruisi-sales.dump" -or
      [int64]$manifest.postgresql.sizeBytes -lt 1 -or
      [string]$manifest.postgresql.sha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [int64]$manifest.postgresql.archiveEntryCount -lt 10) {
    throw "PostgreSQL 备份证据无效"
  }
  $databaseDump = Join-Path $backup "teruisi-sales.dump"
  if (-not (Test-Path -LiteralPath $databaseDump -PathType Leaf) -or
      [int64](Get-Item -LiteralPath $databaseDump).Length -ne [int64]$manifest.postgresql.sizeBytes -or
      (Get-FileSha256 $databaseDump) -cne [string]$manifest.postgresql.sha256) {
    throw "PostgreSQL 备份归档大小或 SHA-256 不一致"
  }
  Assert-ExactPropertySet $manifest.postgresql.evidence @(
    "counts", "revisions", "authorityStatus", "authorityCutoverId"
  ) "PostgreSQL 备份数据库证据"
  Assert-ExactPropertySet $manifest.postgresql.evidence.counts @(
    "sales_order_lines", "sales_import_batches", "erp_product_master"
  ) "PostgreSQL 备份行数证据"
  Assert-ExactPropertySet $manifest.postgresql.evidence.revisions @("sales", "erp") (
    "PostgreSQL 备份 revision 证据"
  )
  foreach ($tableName in @(
    "sales_order_lines", "sales_import_batches", "erp_product_master"
  )) {
    if ([int64]$manifest.postgresql.evidence.counts.$tableName -lt 0 -or
        [int64]$manifest.postgresql.evidence.counts.$tableName -ne
          [int64]$manifest.sourceD1.counts.$tableName) {
      throw "PostgreSQL 与 D1 备份行数证据不一致"
    }
  }
  foreach ($revisionName in @("sales", "erp")) {
    if ([int64]$manifest.postgresql.evidence.revisions.$revisionName -lt 0 -or
        [int64]$manifest.postgresql.evidence.revisions.$revisionName -ne
          [int64]$manifest.sourceD1.revisions.$revisionName) {
      throw "PostgreSQL 与 D1 备份 revision 证据不一致"
    }
  }
  $authorityStatus = [string]$manifest.postgresql.evidence.authorityStatus
  $authorityCutoverId = [string]$manifest.postgresql.evidence.authorityCutoverId
  if ($authorityStatus -notin @("legacy_absent", "pending") -or
      -not [string]::IsNullOrWhiteSpace($authorityCutoverId)) {
    throw "PostgreSQL 备份不是切换前的未激活权威状态"
  }
  return [pscustomobject]@{
    Directory = $backup
    Manifest = $manifest
    ManifestSha256 = $manifestSha
    D1 = $d1Backup
    R2 = $r2Backup
    R2Manifest = $r2Manifest
    DatabaseDump = $databaseDump
  }
}

function Get-AbandonControlPaths {
  $controlRoot = Assert-RuntimeChildPath (
    Join-Path $canonicalRuntime "audits\cutover-abandon"
  )
  $archiveParent = Assert-RuntimeChildPath (Join-Path $controlRoot "archives")
  $resultParent = Assert-RuntimeChildPath (Join-Path $controlRoot "results")
  $recoveryParent = Assert-RuntimeChildPath (Join-Path $controlRoot "recoveries")
  $archiveRoot = Assert-RuntimeChildPath (Join-Path $archiveParent $RehearsalId)
  $resultRoot = Assert-RuntimeChildPath (Join-Path $resultParent $RehearsalId)
  $recoveryRoot = Assert-RuntimeChildPath (Join-Path $recoveryParent $RehearsalId)
  foreach ($directory in @($controlRoot, $archiveParent, $resultParent, $recoveryParent)) {
    if (Test-Path -LiteralPath $directory) {
      $item = Get-Item -LiteralPath $directory -Force
      if (-not $item.PSIsContainer -or
          ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "completed 演练 abandon 控制根必须是普通目录"
      }
    }
  }
  foreach ($pair in @(
    [pscustomobject]@{ Path = $archiveRoot; Parent = $archiveParent },
    [pscustomobject]@{ Path = $resultRoot; Parent = $resultParent }
  )) {
    if ((Get-CanonicalPath (Split-Path -Parent $pair.Path)) -ine
          (Get-CanonicalPath $pair.Parent) -or
        [IO.Path]::GetFileName($pair.Path) -cne $RehearsalId) {
      throw "completed 演练放弃证据路径越界"
    }
    if (Test-Path -LiteralPath $pair.Parent -PathType Container) {
      $candidates = @(Get-ChildItem -LiteralPath $pair.Parent -Force |
        Where-Object { $_.Name -clike ".$RehearsalId.*" })
      foreach ($candidate in $candidates) {
        if ($candidate.Name -cnotmatch
              "^\.$RehearsalId\.[0-9a-f]{32}\.incomplete$" -or
            -not $candidate.PSIsContainer -or
            ($candidate.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
          throw "存在相同 RehearsalId 的未知 abandon staging"
        }
        Assert-NoReparsePoints $candidate.FullName "未发布 abandon staging"
      }
      if ($candidates.Count -gt 1) {
        throw "相同 RehearsalId 存在多个 abandon staging"
      }
      if ($candidates.Count -eq 1 -and (Test-Path -LiteralPath $pair.Path)) {
        throw "abandon final 与 incomplete staging 同时存在"
      }
    }
  }
  if (Test-Path -LiteralPath $recoveryRoot) {
    $recoveryItem = Get-Item -LiteralPath $recoveryRoot -Force
    if (-not $recoveryItem.PSIsContainer -or
        ($recoveryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        @(Get-ChildItem -LiteralPath $recoveryRoot -Force).Count -ne 0) {
      throw "abandon incomplete recovery marker 必须是空普通目录"
    }
  }
  return [pscustomobject]@{
    ControlRoot = $controlRoot
    ArchiveParent = $archiveParent
    ArchiveRoot = $archiveRoot
    ResultParent = $resultParent
    ResultRoot = $resultRoot
    RecoveryParent = $recoveryParent
    RecoveryRoot = $recoveryRoot
  }
}

function Get-AbandonIncompleteTreeEvidence(
  [string]$Path,
  [string]$Kind
) {
  $item = Get-Item -LiteralPath $Path -Force
  if (-not $item.PSIsContainer -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "abandon incomplete staging 必须是普通目录"
  }
  Assert-NoReparsePoints $Path "abandon incomplete staging"
  $root = Get-CanonicalPath $Path
  $files = @()
  foreach ($file in @(Get-ChildItem -LiteralPath $root -File -Recurse -Force |
      Sort-Object FullName)) {
    $relative = $file.FullName.Substring($root.Length).TrimStart("\", "/").Replace("\", "/")
    $files += [ordered]@{
      path = $relative
      sizeBytes = [int64]$file.Length
      sha256 = Get-FileSha256 $file.FullName
    }
  }
  $directories = @(Get-ChildItem -LiteralPath $root -Directory -Recurse -Force)
  $payload = [ordered]@{
    kind = $Kind
    name = [IO.Path]::GetFileName($root)
    directoryCount = [int64]$directories.Count
    files = $files
  }
  return [pscustomobject]@{
    Path = $root
    Kind = $Kind
    FileCount = [int64]$files.Count
    DirectoryCount = [int64]$directories.Count
    TreeSha256 = Get-Sha256Text ($payload | ConvertTo-Json -Compress -Depth 8)
  }
}

function Resolve-AbandonIncompleteRecovery(
  [object]$Paths,
  [object]$Backup,
  [object]$Rehearsal,
  [object]$Preflight
) {
  Assert-ExactPropertySet $Preflight @(
    "version", "status", "stage", "rehearsalId", "productionCutoverId",
    "backupManifestSha256", "forwardRecoveryRecordCount", "formalStateCount",
    "workerAuthorityFileAbsent", "workerAuthoritySidecarAbsent",
    "d1EvidenceSha256", "postgresqlEvidenceSha256", "r2ManifestSha256",
    "deploymentManifestSha256", "serviceConfigSha256", "checkedAt"
  ) "abandon incomplete recovery preflight"
  if ([string]$Preflight.version -cne "teruisi-sales-cutover-abandon-preflight-v1" -or
      [string]$Preflight.status -cne "verified" -or
      [string]$Preflight.stage -cne "initial" -or
      [string]$Preflight.rehearsalId -cne $RehearsalId -or
      [string]$Preflight.productionCutoverId -cne [string]$Backup.Manifest.cutoverId -or
      [string]$Preflight.backupManifestSha256 -cne [string]$Backup.ManifestSha256 -or
      [int]$Preflight.forwardRecoveryRecordCount -ne 0 -or
      [int]$Preflight.formalStateCount -ne 0 -or
      $Preflight.workerAuthorityFileAbsent -isnot [bool] -or
      -not [bool]$Preflight.workerAuthorityFileAbsent -or
      $Preflight.workerAuthoritySidecarAbsent -isnot [bool] -or
      -not [bool]$Preflight.workerAuthoritySidecarAbsent -or
      [string]$Preflight.d1EvidenceSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]$Preflight.postgresqlEvidenceSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]$Preflight.r2ManifestSha256 -cne
        [string]$Backup.Manifest.r2State.manifestSha256 -or
      [string]$Preflight.deploymentManifestSha256 -cne
        [string]$Backup.Manifest.deploymentManifestSha256 -or
      [string]$Preflight.serviceConfigSha256 -cne
        [string]$Backup.Manifest.serviceConfigSha256 -or
      [string]$Rehearsal.ResultSha256 -cne $ApprovedRehearsalResultSha256) {
    throw "abandon incomplete recovery 缺少完整 initial preflight 批准"
  }
  $found = @()
  foreach ($pair in @(
    [pscustomobject]@{
      Kind = "archive"
      Parent = $Paths.ArchiveParent
      Final = $Paths.ArchiveRoot
    },
    [pscustomobject]@{
      Kind = "result"
      Parent = $Paths.ResultParent
      Final = $Paths.ResultRoot
    }
  )) {
    if (-not (Test-Path -LiteralPath $pair.Parent -PathType Container)) { continue }
    $candidates = @(Get-ChildItem -LiteralPath $pair.Parent -Force |
      Where-Object { $_.Name -clike ".$RehearsalId.*" })
    if ($candidates.Count -gt 1) {
      throw "相同 RehearsalId 存在多个 abandon incomplete staging"
    }
    foreach ($candidate in $candidates) {
      if ($candidate.Name -cnotmatch
            "^\.$RehearsalId\.[0-9a-f]{32}\.incomplete$" -or
          -not $candidate.PSIsContainer -or
          ($candidate.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
          (Get-CanonicalPath (Split-Path -Parent $candidate.FullName)) -ine
            (Get-CanonicalPath $pair.Parent) -or
          (Test-Path -LiteralPath $pair.Final)) {
        throw "abandon incomplete staging 身份、边界或 final sibling 状态无效"
      }
      $found += Get-AbandonIncompleteTreeEvidence $candidate.FullName $pair.Kind
    }
  }
  if ($found.Count -gt 1) {
    throw "相同 RehearsalId 跨 archive/result 存在多个 incomplete staging"
  }

  $markerExists = Test-Path -LiteralPath $Paths.RecoveryRoot
  if ($found.Count -gt 0 -and -not $markerExists) {
    New-Item -ItemType Directory -Path $Paths.RecoveryParent -Force | Out-Null
    if (Test-Path -LiteralPath $Paths.RecoveryRoot) {
      throw "abandon incomplete recovery marker 被并发创建"
    }
    [IO.Directory]::CreateDirectory($Paths.RecoveryRoot) | Out-Null
    $markerExists = $true
  }
  if ($markerExists) {
    $marker = Get-Item -LiteralPath $Paths.RecoveryRoot -Force
    if (-not $marker.PSIsContainer -or
        ($marker.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        @(Get-ChildItem -LiteralPath $Paths.RecoveryRoot -Force).Count -ne 0) {
      throw "abandon incomplete recovery marker 无效"
    }
  }
  foreach ($evidence in $found) {
    $fresh = Get-AbandonIncompleteTreeEvidence $evidence.Path $evidence.Kind
    if ([string]$fresh.TreeSha256 -cne [string]$evidence.TreeSha256 -or
        [int64]$fresh.FileCount -ne [int64]$evidence.FileCount -or
        [int64]$fresh.DirectoryCount -ne [int64]$evidence.DirectoryCount) {
      throw "abandon incomplete staging 在清理前发生变化"
    }
    Remove-Item -LiteralPath $evidence.Path -Recurse -Force
    if (Test-Path -LiteralPath $evidence.Path) {
      throw "abandon incomplete staging 未能受控清理"
    }
  }
  if ($found.Count -gt 0) {
    Write-LauncherEvent "WARN" "abandon_incomplete_staging_recovered" (
      Get-Sha256Text (($found | ForEach-Object { $_.TreeSha256 }) -join "`n")
    )
  }
  return [pscustomobject]@{
    RecoveryRequired = [bool]$markerExists
    RemovedCount = [int]$found.Count
  }
}

function Complete-AbandonIncompleteRecovery(
  [object]$Paths,
  [object]$Published
) {
  if (-not (Test-Path -LiteralPath $Paths.RecoveryRoot)) { return }
  $resultPath = Join-Path $Paths.ResultRoot "abandon-result.json"
  $resultShaPath = "$resultPath.sha256"
  $archiveManifestPath = Join-Path $Paths.ArchiveRoot "archive-manifest.json"
  $archiveManifestShaPath = "$archiveManifestPath.sha256"
  if ([string]$Published.ResultSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      (Get-CanonicalPath ([string]$Published.ResultPath)) -ine
        (Get-CanonicalPath $resultPath) -or
      -not (Test-Path -LiteralPath $Paths.ArchiveRoot -PathType Container) -or
      -not (Test-Path -LiteralPath $Paths.ResultRoot -PathType Container) -or
      -not (Test-Path -LiteralPath $resultPath -PathType Leaf) -or
      -not (Test-Path -LiteralPath $resultShaPath -PathType Leaf) -or
      -not (Test-Path -LiteralPath $archiveManifestPath -PathType Leaf) -or
      -not (Test-Path -LiteralPath $archiveManifestShaPath -PathType Leaf) -or
      (Get-FileSha256 $resultPath) -cne [string]$Published.ResultSha256 -or
      (Read-ExactSha256File $resultShaPath "abandon result sidecar") -cne
        [string]$Published.ResultSha256 -or
      (Read-ExactSha256File $archiveManifestShaPath "abandon archive sidecar") -cne
        (Get-FileSha256 $archiveManifestPath)) {
    throw "abandon recovery marker 只能在 archive/result 终态回查后释放"
  }
  $marker = Get-Item -LiteralPath $Paths.RecoveryRoot -Force
  if (-not $marker.PSIsContainer -or
      ($marker.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
      @(Get-ChildItem -LiteralPath $Paths.RecoveryRoot -Force).Count -ne 0) {
    throw "abandon recovery marker 释放前状态无效"
  }
  Remove-Item -LiteralPath $Paths.RecoveryRoot -Force
  if (Test-Path -LiteralPath $Paths.RecoveryRoot) {
    throw "abandon recovery marker 未能释放"
  }
}

function Assert-OrdinaryEvidenceFile([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label 缺失" }
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.PSIsContainer -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Label 必须是普通文件"
  }
  return $item
}

function Copy-AbandonEvidenceFile(
  [string]$Source,
  [string]$StagingRoot,
  [string]$RelativePath
) {
  Assert-OrdinaryEvidenceFile $Source "放弃归档源证据" | Out-Null
  if ([string]::IsNullOrWhiteSpace($RelativePath) -or
      (Test-FullyQualifiedPath $RelativePath) -or
      $RelativePath.Contains("..") -or $RelativePath.Contains(":")) {
    throw "放弃归档相对路径无效"
  }
  $root = Get-CanonicalPath $StagingRoot
  $destination = Get-CanonicalPath (Join-Path $root $RelativePath)
  if (-not $destination.StartsWith(
      $root + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "放弃归档目标路径越界"
  }
  $parent = Split-Path -Parent $destination
  New-Item -ItemType Directory -Path $parent -Force | Out-Null
  Assert-NoReparsePoints $parent "放弃归档目标父目录"
  [IO.File]::Copy((Get-CanonicalPath $Source), $destination, $false)
  Assert-OrdinaryEvidenceFile $destination "放弃归档目标证据" | Out-Null
}

function Assert-CompletedRehearsalForAbandon(
  [string]$RehearsalRoot,
  [object]$State,
  [string]$StatePath,
  [object]$Backup
) {
  $databaseName = "teruisi_sales_rehearsal_$RehearsalId"
  if ([string]$State.version -cne "teruisi-sales-cutover-rehearsal-v1" -or
      [string]$State.rehearsalId -cne $RehearsalId -or
      [string]$State.cutoverId -cne "rehearsal-$RehearsalId" -or
      [string]$State.databaseName -cne $databaseName -or
      [string]$State.backupCutoverId -cne [string]$Backup.Manifest.cutoverId -or
      [string]$State.backupManifestSha256 -cne [string]$Backup.ManifestSha256 -or
      [string]$State.status -cne "completed" -or
      [string]$State.resultFileSha256 -cne $ApprovedRehearsalResultSha256) {
    throw "放弃操作只接受与批准 backup/result 精确绑定的 completed rehearsal"
  }
  $createdSteps = @($State.steps | Where-Object {
    [string]$_.name -ceq "rehearsal_database_created" -and
    [string]$_.result.databaseName -ceq $databaseName
  })
  if ($createdSteps.Count -ne 1) {
    throw "completed rehearsal 缺少本次唯一 CREATE DATABASE 所有权证明"
  }
  $resultPath = Join-Path $RehearsalRoot "rehearsal-result.json"
  $resultShaPath = Join-Path $RehearsalRoot "rehearsal-result.json.sha256"
  Assert-OrdinaryEvidenceFile $StatePath "completed rehearsal state" | Out-Null
  Assert-OrdinaryEvidenceFile $resultPath "completed rehearsal result" | Out-Null
  Assert-OrdinaryEvidenceFile $resultShaPath "completed rehearsal result sidecar" | Out-Null
  if ((Get-FileSha256 $resultPath) -cne $ApprovedRehearsalResultSha256 -or
      (Read-ExactSha256File $resultShaPath "completed rehearsal result SHA-256") -cne
        $ApprovedRehearsalResultSha256) {
    throw "completed rehearsal result 未通过显式批准与 sidecar 复验"
  }
  $rehearsalResult = Read-JsonFile $resultPath "completed rehearsal result"
  Assert-ExactPropertySet $rehearsalResult @(
    "status", "version", "rehearsalId", "cutoverId", "productionCutoverId",
    "databaseName", "backupCreatedAt", "backupManifestSha256",
    "deploymentManifestSha256", "serviceConfigSha256", "sourcePathSha256",
    "sourceD1Sha256", "r2ManifestSha256", "postgresqlDumpSha256",
    "attestationPayloadSha256", "smokeReceiptSha256", "retirementAuditId",
    "preservedEvidenceSha256", "evidenceDirectory", "completedAt"
  ) "completed rehearsal result"
  if ([string]$rehearsalResult.status -cne "completed" -or
      [string]$rehearsalResult.version -cne "teruisi-sales-cutover-rehearsal-result-v1" -or
      [string]$rehearsalResult.rehearsalId -cne $RehearsalId -or
      [string]$rehearsalResult.cutoverId -cne "rehearsal-$RehearsalId" -or
      [string]$rehearsalResult.productionCutoverId -cne
        [string]$Backup.Manifest.cutoverId -or
      [string]$rehearsalResult.databaseName -cne $databaseName -or
      [string]$rehearsalResult.backupManifestSha256 -cne
        [string]$Backup.ManifestSha256 -or
      [string]$rehearsalResult.sourceD1Sha256 -cne
        [string]$Backup.Manifest.sourceD1.sha256 -or
      [string]$rehearsalResult.r2ManifestSha256 -cne
        [string]$Backup.Manifest.r2State.manifestSha256 -or
      [string]$rehearsalResult.postgresqlDumpSha256 -cne
        [string]$Backup.Manifest.postgresql.sha256 -or
      (Get-CanonicalPath ([string]$rehearsalResult.evidenceDirectory)) -ine
        (Get-CanonicalPath $RehearsalRoot)) {
    throw "completed rehearsal result 与 backup/state 身份不一致"
  }
  return [pscustomobject]@{
    Result = $rehearsalResult
    ResultPath = Get-CanonicalPath $resultPath
    ResultShaPath = Get-CanonicalPath $resultShaPath
    ResultSha256 = $ApprovedRehearsalResultSha256
    DatabaseName = $databaseName
  }
}

function Get-AbandonD1Evidence([string]$CurrentD1, [string]$BackupD1) {
  $previousCurrent = [Environment]::GetEnvironmentVariable(
    "TERUISI_ABANDON_CURRENT_D1", "Process"
  )
  $previousBackup = [Environment]::GetEnvironmentVariable(
    "TERUISI_ABANDON_BACKUP_D1", "Process"
  )
  $d1EvidenceCode = @'
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

COUNT_TABLES = (
    "sales_order_lines", "sales_import_batches", "erp_product_master",
    "sales_import_uploads", "sales_import_upload_chunks",
)
FORMAL_TABLES = (
    "sales_write_authority", "erp_reference_projection_source_state",
    "erp_product_projection_state", "erp_reference_projection_outbox",
    "domain_retirement_receipts",
)

def canonical_sha(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def connect_read_only(value):
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".sqlite" or path.is_symlink():
        raise RuntimeError("D1 evidence path rejected")
    resolved = path.resolve(strict=True)
    uri = f"file:{quote(resolved.as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.execute("PRAGMA query_only=ON")
    return connection

def snapshot(value):
    connection = connect_read_only(value)
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or quick[0] != "ok":
            raise RuntimeError("D1 quick_check rejected")
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if any(name not in tables for name in COUNT_TABLES) or "sales_overview_cache_state" not in tables:
            raise RuntimeError("live sales D1 domain is not intact")
        counts = {
            name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in COUNT_TABLES
        }
        revision = connection.execute(
            "SELECT sales_revision, erp_product_revision "
            "FROM sales_overview_cache_state WHERE id=1"
        ).fetchone()
        if revision is None:
            raise RuntimeError("D1 revision singleton missing")
        schema_rows = [
            {"type": str(row[0]), "name": str(row[1]), "sql": str(row[2] or "")}
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE "
                "name IN (" + ",".join("?" for _ in FORMAL_TABLES) + ") "
                "OR name LIKE 'sales_authority_%' "
                "OR name LIKE 'sales_retired_%' "
                "OR name LIKE 'erp_reference_projection_%' "
                "OR name LIKE 'erp_product_projection_%' "
                "OR name = 'erp_product_import_requires_projection_event' "
                "ORDER BY type, name",
                FORMAL_TABLES,
            ).fetchall()
        ]
        authority = []
        if "sales_write_authority" in tables:
            authority = [list(row) for row in connection.execute(
                "SELECT id, owner, epoch, cutover_id FROM sales_write_authority ORDER BY id"
            ).fetchall()]
        retirement = []
        if "domain_retirement_receipts" in tables:
            retirement = [list(row) for row in connection.execute(
                "SELECT domain, version, status, cutover_id, plan_id, "
                "attestation_sha256, smoke_receipt_sha256, preflight_evidence_sha256, "
                "migration_sha256, audit_id, preserved_evidence_sha256, created_at, completed_at "
                "FROM domain_retirement_receipts ORDER BY domain"
            ).fetchall()]
        retired_trigger_count = sum(
            1 for row in schema_rows if row["type"] == "trigger" and row["name"].startswith("sales_retired_")
        )
        authority_owner = "legacy_absent"
        authority_cutover_id = ""
        if authority:
            if len(authority) != 1 or authority[0][0] != 1:
                raise RuntimeError("D1 authority singleton rejected")
            authority_owner = str(authority[0][1])
            authority_cutover_id = str(authority[0][3] or "")
        return {
            "counts": counts,
            "revisions": {"sales": int(revision[0]), "erp": int(revision[1])},
            "formalSchemaSha256": canonical_sha(schema_rows),
            "authorityRowsSha256": canonical_sha(authority),
            "retirementRowsSha256": canonical_sha(retirement),
            "authorityOwner": authority_owner,
            "authorityCutoverId": authority_cutover_id,
            "retirementReceiptCount": len(retirement),
            "retiredTriggerCount": retired_trigger_count,
        }
    finally:
        connection.close()

current = snapshot(os.environ["TERUISI_ABANDON_CURRENT_D1"])
backup = snapshot(os.environ["TERUISI_ABANDON_BACKUP_D1"])
print(json.dumps({"current": current, "backup": backup}, sort_keys=True, separators=(",", ":")))
'@
  try {
    $env:TERUISI_ABANDON_CURRENT_D1 = Get-CanonicalPath $CurrentD1
    $env:TERUISI_ABANDON_BACKUP_D1 = Get-CanonicalPath $BackupD1
    return Invoke-PythonJsonCode $d1EvidenceCode (
      "sales_cutover_abandon_d1_evidence.py"
    ) $canonicalRuntime "completed rehearsal abandon D1 authority preflight"
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_ABANDON_CURRENT_D1", $previousCurrent, "Process"
    )
    [Environment]::SetEnvironmentVariable(
      "TERUISI_ABANDON_BACKUP_D1", $previousBackup, "Process"
    )
  }
}

function Get-AbandonPostgresEvidence([object]$Secrets) {
  $previousUrl = [Environment]::GetEnvironmentVariable(
    "TERUISI_ABANDON_DATABASE_URL", "Process"
  )
  $code = @'
import json
import os
import psycopg

with psycopg.connect(os.environ["TERUISI_ABANDON_DATABASE_URL"]) as connection:
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
            cursor.execute("SELECT status, cutover_id FROM sales_write_authority WHERE id=1")
            authority = cursor.fetchone()
            if authority is None:
                raise RuntimeError("PostgreSQL authority singleton missing")
            authority_status = str(authority[0])
            authority_cutover_id = str(authority[1] or "")
print(json.dumps({
    "counts": counts,
    "revisions": revisions,
    "authorityStatus": authority_status,
    "authorityCutoverId": authority_cutover_id,
}, sort_keys=True, separators=(",", ":")))
'@
  try {
    $env:TERUISI_ABANDON_DATABASE_URL = Database-Url (
      "teruisi_sales_owner"
    ) $Secrets.OwnerPassword "teruisi_abandon_preflight_$RehearsalId" (
      $WriterStatementTimeoutMs
    ) "teruisi_sales"
    return Invoke-PythonJsonCode $code (
      "sales_cutover_abandon_postgres_evidence.py"
    ) $canonicalRuntime "completed rehearsal abandon PostgreSQL authority preflight"
  } finally {
    [Environment]::SetEnvironmentVariable(
      "TERUISI_ABANDON_DATABASE_URL", $previousUrl, "Process"
    )
  }
}

function Assert-AbandonBeforeForwardRecovery(
  [object]$Backup,
  [object]$Secrets,
  [string]$Stage
) {
  Assert-ApplicationProcessesStopped "SalesCutoverAbandon$Stage"
  foreach ($port in @(3000, 5791, 8001, 8002)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "completed 演练放弃要求端口 $port 已停止"
    }
  }
  if ((Get-FileSha256 $DeploymentManifestPath) -cne
        [string]$Backup.Manifest.deploymentManifestSha256 -or
      (Get-FileSha256 $ConfigPath) -cne [string]$Backup.Manifest.serviceConfigSha256) {
    throw "放弃 preflight 期间 Django deployment/config 已偏离批准 backup"
  }
  $formalAuditRoot = Assert-RuntimeChildPath (
    Join-Path $canonicalRuntime "audits\sales-cutover"
  )
  $forwardRecords = @()
  $formalStates = @()
  if (Test-Path -LiteralPath $formalAuditRoot -PathType Container) {
    Assert-NoReparsePoints $formalAuditRoot "正式切换 audit 根目录"
    $forwardRecords = @(Get-ChildItem -LiteralPath $formalAuditRoot -File -Force |
      Where-Object { $_.Name -clike "*.forward-recovery.json" })
    $formalStates = @(Get-ChildItem -LiteralPath $formalAuditRoot -File -Force |
      Where-Object { $_.Name -cmatch "^sales-cutover-[0-9a-f]{24}\.state\.json$" })
  }
  if ($forwardRecords.Count -ne 0 -or $formalStates.Count -ne 0) {
    throw "已存在正式 forward-recovery 或 mutation state；禁止放弃 rehearsal"
  }

  $workerAuthorityPath = "D:\teruisi-runtime\teruisi-worker-sales\state\sales-postgresql-authority.json"
  $workerAuthorityShaPath = "$workerAuthorityPath.sha256"
  if ((Test-Path -LiteralPath $workerAuthorityPath) -or
      (Test-Path -LiteralPath $workerAuthorityShaPath)) {
    throw "已存在 Worker PostgreSQL authority sentinel；禁止放弃 rehearsal"
  }

  $config = Get-ServiceConfig
  $source = Resolve-ErpSourceD1 ([string]$config.erpSourceD1)
  if ([string]$Backup.Manifest.sourcePathSha256 -cne (Get-Sha256Text $source)) {
    throw "放弃 preflight 的生产 D1 路径与 backup 不一致"
  }
  $d1Evidence = Get-AbandonD1Evidence $source $Backup.D1
  foreach ($side in @($d1Evidence.current, $d1Evidence.backup)) {
    Assert-ExactPropertySet $side @(
      "counts", "revisions", "formalSchemaSha256", "authorityRowsSha256",
      "retirementRowsSha256", "authorityOwner", "authorityCutoverId",
      "retirementReceiptCount", "retiredTriggerCount"
    ) "放弃 preflight D1 证据"
  }
  if ([string]$d1Evidence.current.formalSchemaSha256 -cne
        [string]$d1Evidence.backup.formalSchemaSha256 -or
      [string]$d1Evidence.current.authorityRowsSha256 -cne
        [string]$d1Evidence.backup.authorityRowsSha256 -or
      [string]$d1Evidence.current.retirementRowsSha256 -cne
        [string]$d1Evidence.backup.retirementRowsSha256 -or
      [string]$d1Evidence.current.authorityOwner -cne
        [string]$d1Evidence.backup.authorityOwner -or
      [string]$d1Evidence.current.authorityOwner -notin @("legacy_absent", "d1") -or
      -not [string]::IsNullOrWhiteSpace([string]$d1Evidence.current.authorityCutoverId) -or
      [int]$d1Evidence.current.retirementReceiptCount -ne 0 -or
      [int]$d1Evidence.current.retiredTriggerCount -ne 0) {
    throw "D1 authority/formal schema 已偏离 backup 的切换前状态"
  }
  foreach ($name in @(
    "sales_order_lines", "sales_import_batches", "erp_product_master",
    "sales_import_uploads", "sales_import_upload_chunks"
  )) {
    if ([int64]$d1Evidence.current.counts.$name -ne
        [int64]$Backup.Manifest.sourceD1.counts.$name) {
      throw "D1 行数已偏离批准 backup；拒绝解除引用"
    }
  }
  foreach ($name in @("sales", "erp")) {
    if ([int64]$d1Evidence.current.revisions.$name -ne
        [int64]$Backup.Manifest.sourceD1.revisions.$name) {
      throw "D1 revision 已偏离批准 backup；拒绝解除引用"
    }
  }

  $postgresEvidence = Get-AbandonPostgresEvidence $Secrets
  Assert-ExactPropertySet $postgresEvidence @(
    "counts", "revisions", "authorityStatus", "authorityCutoverId"
  ) "放弃 preflight PostgreSQL 证据"
  foreach ($name in @("sales_order_lines", "sales_import_batches", "erp_product_master")) {
    if ([int64]$postgresEvidence.counts.$name -ne
        [int64]$Backup.Manifest.postgresql.evidence.counts.$name) {
      throw "PostgreSQL 行数已偏离批准 backup；拒绝解除引用"
    }
  }
  foreach ($name in @("sales", "erp")) {
    if ([int64]$postgresEvidence.revisions.$name -ne
        [int64]$Backup.Manifest.postgresql.evidence.revisions.$name) {
      throw "PostgreSQL revision 已偏离批准 backup；拒绝解除引用"
    }
  }
  if ([string]$postgresEvidence.authorityStatus -cne
        [string]$Backup.Manifest.postgresql.evidence.authorityStatus -or
      [string]$postgresEvidence.authorityCutoverId -cne
        [string]$Backup.Manifest.postgresql.evidence.authorityCutoverId -or
      [string]$postgresEvidence.authorityStatus -notin @("legacy_absent", "pending") -or
      -not [string]::IsNullOrWhiteSpace([string]$postgresEvidence.authorityCutoverId)) {
    throw "PostgreSQL authority 已偏离批准 backup 的切换前状态"
  }

  $objectDirectory = Get-CanonicalPath (Split-Path -Parent $source)
  $d1Root = Get-CanonicalPath (Split-Path -Parent $objectDirectory)
  $v3Root = Get-CanonicalPath (Split-Path -Parent $d1Root)
  if ([IO.Path]::GetFileName($d1Root) -cne "d1" -or
      [IO.Path]::GetFileName($v3Root) -cne "v3") {
    throw "生产 D1 不在固定 Wrangler persist v3/d1 布局"
  }
  $liveR2 = Get-CanonicalPath (Join-Path $v3Root "r2")
  Assert-R2TreeMatchesManifest $liveR2 $Backup.R2Manifest "生产 R2 放弃 preflight"

  return [pscustomobject][ordered]@{
    version = "teruisi-sales-cutover-abandon-preflight-v1"
    status = "verified"
    stage = $Stage
    rehearsalId = $RehearsalId
    productionCutoverId = [string]$Backup.Manifest.cutoverId
    backupManifestSha256 = [string]$Backup.ManifestSha256
    forwardRecoveryRecordCount = [int]$forwardRecords.Count
    formalStateCount = [int]$formalStates.Count
    workerAuthorityFileAbsent = $true
    workerAuthoritySidecarAbsent = $true
    d1EvidenceSha256 = Get-Sha256Text (
      $d1Evidence | ConvertTo-Json -Compress -Depth 8
    )
    postgresqlEvidenceSha256 = Get-Sha256Text (
      $postgresEvidence | ConvertTo-Json -Compress -Depth 8
    )
    r2ManifestSha256 = [string]$Backup.Manifest.r2State.manifestSha256
    deploymentManifestSha256 = Get-FileSha256 $DeploymentManifestPath
    serviceConfigSha256 = Get-FileSha256 $ConfigPath
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
  }
}

function Assert-AbandonArchive(
  [string]$ArchiveRoot,
  [object]$Backup,
  [object]$Rehearsal
) {
  if (-not (Test-Path -LiteralPath $ArchiveRoot -PathType Container)) {
    throw "completed 演练放弃归档不存在"
  }
  Assert-NoReparsePoints $ArchiveRoot "completed 演练放弃归档"
  $manifestPath = Join-Path $ArchiveRoot "archive-manifest.json"
  $manifestShaPath = Join-Path $ArchiveRoot "archive-manifest.json.sha256"
  Assert-OrdinaryEvidenceFile $manifestPath "放弃归档 manifest" | Out-Null
  Assert-OrdinaryEvidenceFile $manifestShaPath "放弃归档 manifest sidecar" | Out-Null
  $manifestSha256 = Get-FileSha256 $manifestPath
  if ((Read-ExactSha256File $manifestShaPath "放弃归档 manifest SHA-256") -cne
      $manifestSha256) {
    throw "放弃归档 manifest sidecar 不一致"
  }
  $manifest = Read-JsonFile $manifestPath "completed 演练放弃归档 manifest"
  Assert-ExactPropertySet $manifest @(
    "version", "status", "rehearsalId", "productionCutoverId",
    "backupCutoverId", "backupDirectoryName", "backupManifestSha256",
    "rehearsalResultSha256", "initialPreflightEvidenceSha256", "files", "createdAt"
  ) "completed 演练放弃归档 manifest"
  if ([string]$manifest.version -cne "teruisi-sales-cutover-abandon-archive-v1" -or
      [string]$manifest.status -cne "completed" -or
      [string]$manifest.rehearsalId -cne $RehearsalId -or
      [string]$manifest.productionCutoverId -cne [string]$Backup.Manifest.cutoverId -or
      [string]$manifest.backupCutoverId -cne [string]$Backup.Manifest.cutoverId -or
      [string]$manifest.backupDirectoryName -cne [IO.Path]::GetFileName($Backup.Directory) -or
      [string]$manifest.backupManifestSha256 -cne [string]$Backup.ManifestSha256 -or
      [string]$manifest.rehearsalResultSha256 -cne [string]$Rehearsal.ResultSha256 -or
      [string]$manifest.initialPreflightEvidenceSha256 -cnotmatch "^[0-9a-f]{64}$") {
    throw "completed 演练放弃归档 manifest 身份无效"
  }
  $rows = @($manifest.files)
  $seen = @{}
  foreach ($row in $rows) {
    Assert-ExactPropertySet $row @("path", "sizeBytes", "sha256") "放弃归档文件证据"
    $relative = [string]$row.path
    if ([string]::IsNullOrWhiteSpace($relative) -or
        (Test-FullyQualifiedPath $relative) -or $relative.Contains("..") -or
        $seen.ContainsKey($relative) -or [int64]$row.sizeBytes -lt 0 -or
        [string]$row.sha256 -cnotmatch "^[0-9a-f]{64}$") {
      throw "放弃归档文件证据无效"
    }
    $seen[$relative] = $true
    $file = Get-CanonicalPath (Join-Path $ArchiveRoot $relative)
    if (-not $file.StartsWith(
        (Get-CanonicalPath $ArchiveRoot) + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      )) { throw "放弃归档文件路径越界" }
    $item = Assert-OrdinaryEvidenceFile $file "放弃归档文件"
    if ([int64]$item.Length -ne [int64]$row.sizeBytes -or
        (Get-FileSha256 $file) -cne [string]$row.sha256) {
      throw "放弃归档文件大小或 SHA-256 不一致"
    }
  }
  $actualEvidenceFiles = @(Get-ChildItem -LiteralPath $ArchiveRoot -File -Recurse -Force |
    Where-Object { $_.FullName -ine $manifestPath -and $_.FullName -ine $manifestShaPath })
  if ($actualEvidenceFiles.Count -ne $rows.Count) {
    throw "放弃归档存在未签收或缺失文件"
  }
  foreach ($required in @(
    "rehearsal/rehearsal-state.json", "rehearsal/rehearsal-result.json",
    "rehearsal/rehearsal-result.json.sha256", "backup/backup-manifest.json",
    "backup/backup-manifest.json.sha256", "backup/r2-manifest.json",
    "preflight/initial.json"
  )) {
    if (-not $seen.ContainsKey($required)) { throw "放弃归档缺少必需证据：$required" }
  }
  if ((Get-FileSha256 (Join-Path $ArchiveRoot "backup\backup-manifest.json")) -cne
        [string]$Backup.ManifestSha256 -or
      (Read-ExactSha256File (
        Join-Path $ArchiveRoot "backup\backup-manifest.json.sha256"
      ) "归档 backup manifest sidecar") -cne [string]$Backup.ManifestSha256 -or
      (Get-FileSha256 (Join-Path $ArchiveRoot "rehearsal\rehearsal-result.json")) -cne
        [string]$Rehearsal.ResultSha256 -or
      (Read-ExactSha256File (
        Join-Path $ArchiveRoot "rehearsal\rehearsal-result.json.sha256"
      ) "归档 rehearsal result sidecar") -cne [string]$Rehearsal.ResultSha256 -or
      (Get-FileSha256 (Join-Path $ArchiveRoot "preflight\initial.json")) -cne
        [string]$manifest.initialPreflightEvidenceSha256) {
    throw "放弃归档关键证据未通过原始批准 SHA-256 回查"
  }
  $archivedState = Read-JsonFile (
    Join-Path $ArchiveRoot "rehearsal\rehearsal-state.json"
  ) "归档 rehearsal state"
  if ([string]$archivedState.version -cne "teruisi-sales-cutover-rehearsal-v1" -or
      [string]$archivedState.rehearsalId -cne $RehearsalId -or
      [string]$archivedState.status -cne "completed" -or
      [string]$archivedState.backupManifestSha256 -cne [string]$Backup.ManifestSha256 -or
      [string]$archivedState.resultFileSha256 -cne [string]$Rehearsal.ResultSha256) {
    throw "归档 rehearsal state 身份无效"
  }
  return [pscustomobject]@{
    Root = Get-CanonicalPath $ArchiveRoot
    Manifest = $manifest
    ManifestPath = Get-CanonicalPath $manifestPath
    ManifestSha256 = $manifestSha256
  }
}

function Publish-AbandonArchive(
  [object]$Paths,
  [string]$RehearsalRoot,
  [string]$StatePath,
  [object]$Backup,
  [object]$Rehearsal,
  [object]$InitialPreflight
) {
  New-Item -ItemType Directory -Path $Paths.ArchiveParent -Force | Out-Null
  if (Test-Path -LiteralPath $Paths.ArchiveRoot) {
    return Assert-AbandonArchive $Paths.ArchiveRoot $Backup $Rehearsal
  }
  $staging = Assert-RuntimeChildPath (Join-Path $Paths.ArchiveParent (
    ".$RehearsalId.$([Guid]::NewGuid().ToString('N')).incomplete"
  ))
  try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    Copy-AbandonEvidenceFile $StatePath $staging "rehearsal/rehearsal-state.json"
    Copy-AbandonEvidenceFile $Rehearsal.ResultPath $staging "rehearsal/rehearsal-result.json"
    Copy-AbandonEvidenceFile $Rehearsal.ResultShaPath $staging (
      "rehearsal/rehearsal-result.json.sha256"
    )
    Copy-AbandonEvidenceFile (
      Join-Path $Backup.Directory "backup-manifest.json"
    ) $staging "backup/backup-manifest.json"
    Copy-AbandonEvidenceFile (
      Join-Path $Backup.Directory "backup-manifest.json.sha256"
    ) $staging "backup/backup-manifest.json.sha256"
    Copy-AbandonEvidenceFile (
      Join-Path $Backup.Directory "r2-manifest.json"
    ) $staging "backup/r2-manifest.json"
    $auditSource = Join-Path $RehearsalRoot "audit"
    if (Test-Path -LiteralPath $auditSource -PathType Container) {
      Assert-NoReparsePoints $auditSource "completed rehearsal audit"
      foreach ($file in @(Get-ChildItem -LiteralPath $auditSource -File -Recurse -Force |
          Sort-Object FullName)) {
        $relative = $file.FullName.Substring($auditSource.Length).TrimStart("\", "/")
        Copy-AbandonEvidenceFile $file.FullName $staging (
          "rehearsal/audit/" + $relative.Replace("\", "/")
        )
      }
    }
    $initialPath = Join-Path $staging "preflight\initial.json"
    New-Item -ItemType Directory -Path (Split-Path -Parent $initialPath) -Force | Out-Null
    Write-AtomicJson $initialPath $InitialPreflight
    $initialSha256 = Get-FileSha256 $initialPath
    $files = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $staging -File -Recurse -Force |
        Sort-Object FullName)) {
      $files += [ordered]@{
        path = $file.FullName.Substring($staging.Length).TrimStart("\", "/").Replace("\", "/")
        sizeBytes = [int64]$file.Length
        sha256 = Get-FileSha256 $file.FullName
      }
    }
    $manifest = [ordered]@{
      version = "teruisi-sales-cutover-abandon-archive-v1"
      status = "completed"
      rehearsalId = $RehearsalId
      productionCutoverId = [string]$Backup.Manifest.cutoverId
      backupCutoverId = [string]$Backup.Manifest.cutoverId
      backupDirectoryName = [IO.Path]::GetFileName($Backup.Directory)
      backupManifestSha256 = [string]$Backup.ManifestSha256
      rehearsalResultSha256 = [string]$Rehearsal.ResultSha256
      initialPreflightEvidenceSha256 = $initialSha256
      files = $files
      createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $manifestPath = Join-Path $staging "archive-manifest.json"
    Write-AtomicJson $manifestPath $manifest
    [IO.File]::WriteAllText(
      (Join-Path $staging "archive-manifest.json.sha256"),
      (Get-FileSha256 $manifestPath) + [Environment]::NewLine,
      $Utf8NoBom
    )
    if (Test-Path -LiteralPath $Paths.ArchiveRoot) {
      throw "相同 RehearsalId 的放弃归档已并发发布"
    }
    [IO.Directory]::Move($staging, $Paths.ArchiveRoot)
  } finally {
    if (Test-Path -LiteralPath $staging -PathType Container) {
      Assert-NoReparsePoints $staging "未发布放弃归档"
      Remove-Item -LiteralPath $staging -Recurse -Force
    }
  }
  return Assert-AbandonArchive $Paths.ArchiveRoot $Backup $Rehearsal
}

function Assert-AbandonPayloadCleaned(
  [string]$RehearsalRoot,
  [object]$State,
  [string]$StatePath
) {
  if ([string]$State.payloadDisposition -cne "cleaned" -or
      $State.databaseRetained -isnot [bool] -or [bool]$State.databaseRetained -or
      [string]$State.payloadCleanupAuditSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]::IsNullOrWhiteSpace([string]$State.payloadCleanupAuditPath)) {
    throw "completed rehearsal payload 尚未完整处置"
  }
  $auditPath = Get-CanonicalPath (Join-Path $RehearsalRoot "payload-cleanup-audit.json")
  if ((Get-CanonicalPath ([string]$State.payloadCleanupAuditPath)) -ine $auditPath) {
    throw "completed rehearsal payload cleanup audit 路径无效"
  }
  Assert-OrdinaryEvidenceFile $auditPath "completed rehearsal payload cleanup audit" | Out-Null
  if ((Get-FileSha256 $auditPath) -cne [string]$State.payloadCleanupAuditSha256) {
    throw "completed rehearsal payload cleanup audit SHA-256 不一致"
  }
  $audit = Read-JsonFile $auditPath "completed rehearsal payload cleanup audit"
  if ([string]$audit.version -cne "teruisi-sales-rehearsal-payload-cleanup-v1" -or
      [string]$audit.status -cne "completed" -or
      [string]$audit.rehearsalId -cne $RehearsalId -or
      [string]$audit.backupManifestSha256 -cne [string]$State.backupManifestSha256 -or
      [string]$audit.database.name -cne "teruisi_sales_rehearsal_$RehearsalId" -or
      $audit.database.verifiedAbsent -isnot [bool] -or
      -not [bool]$audit.database.verifiedAbsent -or
      $audit.payload.verifiedAbsent -isnot [bool] -or
      -not [bool]$audit.payload.verifiedAbsent -or
      @($audit.errors).Count -ne 0 -or
      @(Get-RehearsalPayloadTargets $RehearsalRoot | Where-Object {
        Test-Path -LiteralPath $_.FullPath
      }).Count -ne 0) {
    throw "completed rehearsal payload cleanup audit 未证明全部目标缺失"
  }
  Assert-OrdinaryEvidenceFile $StatePath "清理后的 rehearsal state" | Out-Null
  return [pscustomobject]@{
    AuditPath = $auditPath
    AuditSha256 = [string]$State.payloadCleanupAuditSha256
  }
}

function Assert-AbandonResult(
  [object]$Paths,
  [object]$Backup,
  [object]$Rehearsal,
  [object]$Archive,
  [object]$State,
  [string]$StatePath
) {
  if (-not (Test-Path -LiteralPath $Paths.ResultRoot -PathType Container)) {
    throw "completed 演练放弃 result 不存在"
  }
  Assert-NoReparsePoints $Paths.ResultRoot "completed 演练放弃 result"
  $resultPath = Join-Path $Paths.ResultRoot "abandon-result.json"
  $resultShaPath = Join-Path $Paths.ResultRoot "abandon-result.json.sha256"
  $finalPreflightPath = Join-Path $Paths.ResultRoot "final-preflight.json"
  foreach ($file in @($resultPath, $resultShaPath, $finalPreflightPath)) {
    Assert-OrdinaryEvidenceFile $file "completed 演练放弃 result 证据" | Out-Null
  }
  if (@(Get-ChildItem -LiteralPath $Paths.ResultRoot -File -Force).Count -ne 3) {
    throw "completed 演练放弃 result 目录字段集合无效"
  }
  $resultSha256 = Get-FileSha256 $resultPath
  if ((Read-ExactSha256File $resultShaPath "completed 演练放弃 result SHA-256") -cne
      $resultSha256) {
    throw "completed 演练放弃 result sidecar 不一致"
  }
  $result = Read-JsonFile $resultPath "completed 演练放弃 result"
  Assert-ExactPropertySet $result @(
    "version", "status", "rehearsalId", "productionCutoverId", "backupCutoverId",
    "backupDirectoryName", "backupManifestSha256", "rehearsalResultSha256",
    "archiveManifestPath", "archiveManifestSha256",
    "initialPreflightEvidenceSha256", "finalPreflightEvidenceSha256",
    "payloadCleanupAuditPath", "payloadCleanupAuditSha256",
    "backupDisposition", "completedAt"
  ) "completed 演练放弃 result"
  $cleanup = Assert-AbandonPayloadCleaned $Rehearsal.Result.evidenceDirectory $State $StatePath
  if ([string]$result.version -cne "teruisi-sales-cutover-abandon-v1" -or
      [string]$result.status -cne "completed" -or
      [string]$result.rehearsalId -cne $RehearsalId -or
      [string]$result.productionCutoverId -cne [string]$Backup.Manifest.cutoverId -or
      [string]$result.backupCutoverId -cne [string]$Backup.Manifest.cutoverId -or
      [string]$result.backupDirectoryName -cne [IO.Path]::GetFileName($Backup.Directory) -or
      [string]$result.backupManifestSha256 -cne [string]$Backup.ManifestSha256 -or
      [string]$result.rehearsalResultSha256 -cne [string]$Rehearsal.ResultSha256 -or
      (Get-CanonicalPath ([string]$result.archiveManifestPath)) -ine $Archive.ManifestPath -or
      [string]$result.archiveManifestSha256 -cne [string]$Archive.ManifestSha256 -or
      [string]$result.initialPreflightEvidenceSha256 -cne
        [string]$Archive.Manifest.initialPreflightEvidenceSha256 -or
      [string]$result.finalPreflightEvidenceSha256 -cne
        (Get-FileSha256 $finalPreflightPath) -or
      (Get-CanonicalPath ([string]$result.payloadCleanupAuditPath)) -ine
        (Get-CanonicalPath $cleanup.AuditPath) -or
      [string]$result.payloadCleanupAuditSha256 -cne [string]$cleanup.AuditSha256 -or
      [string]$result.backupDisposition -cne "approved_for_controlled_prune") {
    throw "completed 演练放弃 result 与 archive/cleanup/backup 身份不一致"
  }
  $finalPreflight = Read-JsonFile $finalPreflightPath "放弃 final preflight"
  if ([string]$finalPreflight.version -cne "teruisi-sales-cutover-abandon-preflight-v1" -or
      [string]$finalPreflight.status -cne "verified" -or
      [string]$finalPreflight.stage -cne "final" -or
      [string]$finalPreflight.rehearsalId -cne $RehearsalId -or
      [string]$finalPreflight.backupManifestSha256 -cne [string]$Backup.ManifestSha256 -or
      [int]$finalPreflight.forwardRecoveryRecordCount -ne 0 -or
      [int]$finalPreflight.formalStateCount -ne 0 -or
      $finalPreflight.workerAuthorityFileAbsent -isnot [bool] -or
      -not [bool]$finalPreflight.workerAuthorityFileAbsent -or
      $finalPreflight.workerAuthoritySidecarAbsent -isnot [bool] -or
      -not [bool]$finalPreflight.workerAuthoritySidecarAbsent) {
    throw "completed 演练放弃 final preflight 证据无效"
  }
  return [pscustomobject]@{
    Result = $result
    ResultPath = Get-CanonicalPath $resultPath
    ResultSha256 = $resultSha256
  }
}

function Publish-AbandonResult(
  [object]$Paths,
  [object]$Backup,
  [object]$Rehearsal,
  [object]$Archive,
  [object]$State,
  [string]$StatePath,
  [object]$FinalPreflight
) {
  New-Item -ItemType Directory -Path $Paths.ResultParent -Force | Out-Null
  if (Test-Path -LiteralPath $Paths.ResultRoot) {
    return Assert-AbandonResult $Paths $Backup $Rehearsal $Archive $State $StatePath
  }
  $staging = Assert-RuntimeChildPath (Join-Path $Paths.ResultParent (
    ".$RehearsalId.$([Guid]::NewGuid().ToString('N')).incomplete"
  ))
  try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    $finalPreflightPath = Join-Path $staging "final-preflight.json"
    Write-AtomicJson $finalPreflightPath $FinalPreflight
    $cleanup = Assert-AbandonPayloadCleaned $Rehearsal.Result.evidenceDirectory $State $StatePath
    $result = [ordered]@{
      version = "teruisi-sales-cutover-abandon-v1"
      status = "completed"
      rehearsalId = $RehearsalId
      productionCutoverId = [string]$Backup.Manifest.cutoverId
      backupCutoverId = [string]$Backup.Manifest.cutoverId
      backupDirectoryName = [IO.Path]::GetFileName($Backup.Directory)
      backupManifestSha256 = [string]$Backup.ManifestSha256
      rehearsalResultSha256 = [string]$Rehearsal.ResultSha256
      archiveManifestPath = [string]$Archive.ManifestPath
      archiveManifestSha256 = [string]$Archive.ManifestSha256
      initialPreflightEvidenceSha256 = [string]$Archive.Manifest.initialPreflightEvidenceSha256
      finalPreflightEvidenceSha256 = Get-FileSha256 $finalPreflightPath
      payloadCleanupAuditPath = [string]$cleanup.AuditPath
      payloadCleanupAuditSha256 = [string]$cleanup.AuditSha256
      backupDisposition = "approved_for_controlled_prune"
      completedAt = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $resultPath = Join-Path $staging "abandon-result.json"
    Write-AtomicJson $resultPath $result
    [IO.File]::WriteAllText(
      (Join-Path $staging "abandon-result.json.sha256"),
      (Get-FileSha256 $resultPath) + [Environment]::NewLine,
      $Utf8NoBom
    )
    if (Test-Path -LiteralPath $Paths.ResultRoot) {
      throw "相同 RehearsalId 的放弃 result 已并发发布"
    }
    [IO.Directory]::Move($staging, $Paths.ResultRoot)
  } finally {
    if (Test-Path -LiteralPath $staging -PathType Container) {
      Assert-NoReparsePoints $staging "未发布放弃 result"
      Remove-Item -LiteralPath $staging -Recurse -Force
    }
  }
  return Assert-AbandonResult $Paths $Backup $Rehearsal $Archive $State $StatePath
}

function Invoke-AbandonCompletedRehearsal {
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $paths = Resolve-ExactRehearsalRoot $RehearsalId $true
  $rehearsalRoot = $paths.Root
  $statePath = Get-CanonicalPath (Join-Path $rehearsalRoot "rehearsal-state.json")
  $state = Read-JsonFile $statePath "completed rehearsal state"
  if ([string]$state.backupCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
    throw "completed rehearsal backupCutoverId 无效"
  }
  $backupName = "sales-cutover-$((Get-Sha256Text ([string]$state.backupCutoverId)).Substring(0, 24))"
  $backupPath = Join-Path (Join-Path $canonicalRuntime "backups") $backupName
  $backup = Assert-BackupAndReturnEvidence $backupPath $false
  $rehearsal = Assert-CompletedRehearsalForAbandon `
    $rehearsalRoot $state $statePath $backup
  $control = Get-AbandonControlPaths
  $postgresStarted = $false
  $secrets = $null
  $superuserPassword = $null
  $previousAdminUrl = [Environment]::GetEnvironmentVariable(
    "TERUISI_REHEARSAL_ADMIN_DATABASE_URL", "Process"
  )
  try {
    $secrets = Read-Secrets
    $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
    if ([int]$vault.version -ne 2) {
      throw "completed 演练放弃要求 DPAPI v2 凭据库"
    }
    $superuserPassword = Unprotect-Value (
      [string]$vault.postgresSuperuser
    ) "postgresSuperuser"
    if ([string]::IsNullOrWhiteSpace($superuserPassword)) {
      throw "completed 演练放弃缺少 PostgreSQL superuser DPAPI 凭据"
    }
    $postgresStarted = Start-Postgres
    $env:TERUISI_REHEARSAL_ADMIN_DATABASE_URL = Database-Url "postgres" (
      $superuserPassword
    ) "teruisi_abandon_cleanup_$RehearsalId" $ReaderStatementTimeoutMs "postgres"

    $initialPreflight = Assert-AbandonBeforeForwardRecovery $backup $secrets "initial"
    $incompleteRecovery = Resolve-AbandonIncompleteRecovery `
      $control $backup $rehearsal $initialPreflight
    if ($incompleteRecovery.RecoveryRequired) {
      # The first full fence authorizes only bounded staging recovery.  Re-run
      # the same D1/PG/R2/port/authority fence after that filesystem mutation
      # before publishing either create-only final.
      $initialPreflight = Assert-AbandonBeforeForwardRecovery $backup $secrets "initial"
    }
    $archive = Publish-AbandonArchive `
      $control $rehearsalRoot $statePath $backup $rehearsal $initialPreflight

    if ([string]$state.payloadDisposition -ceq "cleaned") {
      Assert-AbandonPayloadCleaned $rehearsalRoot $state $statePath | Out-Null
    } else {
      $cleanup = Invoke-RehearsalPayloadCleanup `
        $state $statePath $rehearsalRoot $rehearsal.DatabaseName $true $true @()
      if ($null -eq $cleanup -or -not $cleanup.Completed) {
        throw "completed rehearsal 大 payload/数据库清理未完成"
      }
    }
    $state = Read-JsonFile $statePath "清理后的 completed rehearsal state"
    Assert-AbandonPayloadCleaned $rehearsalRoot $state $statePath | Out-Null

    $finalPreflight = Assert-AbandonBeforeForwardRecovery $backup $secrets "final"
    $archive = Assert-AbandonArchive $control.ArchiveRoot $backup $rehearsal
    $resultAlreadyExisted = Test-Path -LiteralPath $control.ResultRoot -PathType Container
    $published = Publish-AbandonResult `
      $control $backup $rehearsal $archive $state $statePath $finalPreflight
    Complete-AbandonIncompleteRecovery $control $published
    return [ordered]@{
      status = if ($resultAlreadyExisted) { "already_completed" } else { "completed" }
      version = "teruisi-sales-cutover-abandon-reference-v1"
      rehearsalId = $RehearsalId
      productionCutoverId = [string]$backup.Manifest.cutoverId
      backupManifestSha256 = [string]$backup.ManifestSha256
      rehearsalResultSha256 = [string]$rehearsal.ResultSha256
      archiveManifestSha256 = [string]$archive.ManifestSha256
      abandonResultPath = [string]$published.ResultPath
      abandonResultSha256 = [string]$published.ResultSha256
      backupDisposition = "approved_for_controlled_prune"
    }
  } finally {
    if ($postgresStarted) {
      try { Stop-Postgres } catch {
        Write-LauncherEvent "WARN" "abandon_rehearsal_postgres_stop_failed" (
          Get-Sha256Text (Protect-LogText $_.Exception.Message)
        )
      }
    }
    [Environment]::SetEnvironmentVariable(
      "TERUISI_REHEARSAL_ADMIN_DATABASE_URL", $previousAdminUrl, "Process"
    )
    $secrets = $null
    $superuserPassword = $null
  }
}

function Invoke-ExplicitFailedRehearsalCleanup {
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  $paths = Resolve-ExactRehearsalRoot $RehearsalId $true
  $rehearsalRoot = $paths.Root
  $statePath = Get-CanonicalPath (Join-Path $rehearsalRoot "rehearsal-state.json")
  if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    throw "失败演练缺少 rehearsal-state.json"
  }
  $stateItem = Get-Item -LiteralPath $statePath -Force
  if (($stateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "失败演练 state 不得是重解析点"
  }
  $state = Read-JsonFile $statePath "失败演练 state"
  $databaseName = "teruisi_sales_rehearsal_$RehearsalId"
  if ([string]$state.version -cne "teruisi-sales-cutover-rehearsal-v1" -or
      [string]$state.rehearsalId -cne $RehearsalId -or
      [string]$state.cutoverId -cne "rehearsal-$RehearsalId" -or
      [string]$state.databaseName -cne $databaseName -or
      [string]$state.backupManifestSha256 -cnotmatch "^[0-9a-f]{64}$" -or
      [string]$state.status -cne "failed") {
    throw "显式清理只接受身份完整且 status=failed 的精确演练"
  }
  $disposition = [string]$state.payloadDisposition
  if (-not [string]::IsNullOrWhiteSpace($disposition) -and
      $disposition -notin @("unresolved", "cleaned")) {
    throw "失败演练 payloadDisposition 无效"
  }
  $createdSteps = @($state.steps | Where-Object {
    [string]$_.name -ceq "rehearsal_database_created" -and
    [string]$_.result.databaseName -ceq $databaseName
  })
  $databaseRetained = [bool]$state.databaseRetained
  $allowDatabaseDrop = $databaseRetained -and $createdSteps.Count -eq 1
  if ($databaseRetained -and -not $allowDatabaseDrop) {
    throw "失败演练声称保留数据库，但缺少本次唯一 CREATE DATABASE 所有权证明"
  }

  $readerPidPath = Join-Path $rehearsalRoot "run\django-reader.pid.json"
  $writerPidPath = Join-Path $rehearsalRoot "run\django-writer.pid.json"
  $erpPidPath = Join-Path $rehearsalRoot "run\erp-reference-sync.pid.json"
  $preconditionErrors = @()
  foreach ($owned in @(
    [pscustomobject]@{
      Service = "sales-rehearsal-writer-$RehearsalId"
      Pid = $writerPidPath
      Executable = $Waitress
    },
    [pscustomobject]@{
      Service = "sales-rehearsal-reader-$RehearsalId"
      Pid = $readerPidPath
      Executable = $Waitress
    },
    [pscustomobject]@{
      Service = "sales-rehearsal-erp-$RehearsalId"
      Pid = $erpPidPath
      Executable = $Python
    }
  )) {
    try {
      Stop-OwnedProcess $owned.Service $owned.Pid $owned.Executable
    } catch {
      $preconditionErrors += [ordered]@{
        stage = "owned_process_stop"
        failureClass = $_.Exception.GetType().FullName
        failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
      }
    }
  }
  try {
    Assert-ApplicationProcessesStopped "FailedSalesCutoverRehearsalCleanup"
    foreach ($port in @(3000, 5791, 8001, 8002)) {
      if (@(Get-PortListeners $port).Count -gt 0) {
        throw "失败演练清理要求受保护业务端口全部停止"
      }
    }
  } catch {
    $preconditionErrors += [ordered]@{
      stage = "maintenance_precondition"
      failureClass = $_.Exception.GetType().FullName
      failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
    }
  }

  $postgresStarted = $false
  $secrets = $null
  $superuserPassword = $null
  $previousAdminUrl = [Environment]::GetEnvironmentVariable(
    "TERUISI_REHEARSAL_ADMIN_DATABASE_URL", "Process"
  )
  $cleanup = $null
  try {
    if ($preconditionErrors.Count -eq 0) {
      $secrets = Read-Secrets
      $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
      if ([int]$vault.version -ne 2) {
        throw "失败演练清理要求 DPAPI v2 凭据库"
      }
      $superuserPassword = Unprotect-Value (
        [string]$vault.postgresSuperuser
      ) "postgresSuperuser"
      if ([string]::IsNullOrWhiteSpace($superuserPassword)) {
        throw "失败演练清理缺少 PostgreSQL superuser DPAPI 凭据"
      }
      $postgresStarted = Start-Postgres
      $env:TERUISI_REHEARSAL_ADMIN_DATABASE_URL = Database-Url "postgres" (
        $superuserPassword
      ) "teruisi_rehearsal_cleanup_$RehearsalId" $ReaderStatementTimeoutMs "postgres"
    }
  } catch {
    $preconditionErrors += [ordered]@{
      stage = "database_cleanup_precondition"
      failureClass = $_.Exception.GetType().FullName
      failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
    }
  }
  try {
    $cleanup = Invoke-RehearsalPayloadCleanup `
      $state $statePath $rehearsalRoot $databaseName $true $allowDatabaseDrop `
      $preconditionErrors
  } finally {
    if ($postgresStarted) {
      try { Stop-Postgres } catch {
        Write-LauncherEvent "WARN" "failed_rehearsal_cleanup_postgres_stop_failed" (
          Get-Sha256Text (Protect-LogText $_.Exception.Message)
        )
      }
    }
    [Environment]::SetEnvironmentVariable(
      "TERUISI_REHEARSAL_ADMIN_DATABASE_URL", $previousAdminUrl, "Process"
    )
    $secrets = $null
    $superuserPassword = $null
  }
  if ($null -eq $cleanup -or -not $cleanup.Completed) {
    throw "失败演练 payload 清理未完成；state 保持 payloadDisposition=unresolved"
  }
  return [ordered]@{
    status = "completed"
    version = "teruisi-sales-rehearsal-payload-cleanup-reference-v1"
    rehearsalId = $RehearsalId
    backupManifestSha256 = [string]$state.backupManifestSha256
    payloadDisposition = "cleaned"
    payloadCleanupAuditPath = [string]$cleanup.AuditPath
    payloadCleanupAuditSha256 = [string]$cleanup.AuditSha256
  }
}

function Invoke-IsolatedRehearsal {
  Assert-DeployedApplication
  Assert-WranglerLocalR2RoundTrip $InstalledAppRoot
  Assert-RuntimeAclHardened
  Assert-ApplicationProcessesStopped "SalesCutoverRehearsal"
  foreach ($port in @(3000, 5791, 8001, 8002)) {
    if (@(Get-PortListeners $port).Count -gt 0) {
      throw "隔离演练要求端口 $port 已停止；operator 不会终止或接管既有服务"
    }
  }
  Get-ServiceConfig | Out-Null

  $requiredRuntimeFiles = @(
    (Join-Path $InstalledAppRoot "tools\sales-local-cutover-rehearsal.ps1"),
    (Join-Path $InstalledAppRoot "tools\sales-local-cutover.ts"),
    (Join-Path $InstalledAppRoot "tools\sales-legacy-r2-cleanup.ts"),
    (Join-Path $InstalledAppRoot "tools\sales-d1-write-authority.ts"),
    (Join-Path $InstalledAppRoot "tools\sales-d1-retirement.ts"),
    (Join-Path $InstalledAppRoot "runtime-tools\node_modules\wrangler\wrangler-dist\cli.js"),
    (Join-Path $InstalledAppRoot "drizzle\0090_sales_write_authority.sql"),
    (Join-Path $InstalledAppRoot "drizzle\0091_erp_reference_projection.sql"),
    (Join-Path $InstalledAppRoot "drizzle\0092_sales_domain_retirement.sql"),
    (Join-Path $BackendRoot "manage.py"),
    $Python,
    $Waitress,
    $Node,
    (Join-Path $PostgresBin "pg_restore.exe")
  )
  foreach ($required in $requiredRuntimeFiles) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "受保护 runtime 缺少隔离演练依赖"
    }
  }
  $nodeVersionRun = Invoke-BoundedNativeProcess $Node @("--version") $canonicalRuntime
  $nodeVersion = @($nodeVersionRun.Output)
  if ($nodeVersionRun.ExitCode -ne 0 -or $nodeVersion.Count -ne 1 -or
      [string]$nodeVersion[0] -cnotmatch "^v24\.[0-9]+\.[0-9]+$") {
    throw (
      "隔离演练必须使用固定 Node.js 24 原生 TypeScript runtime（" +
      "$(Get-NativeFailureSummary $nodeVersionRun)）"
    )
  }

  $backup = Assert-BackupAndReturnEvidence $BackupDirectory
  $databaseName = "teruisi_sales_rehearsal_$RehearsalId"
  if ($databaseName -cnotmatch "^teruisi_sales_rehearsal_[0-9a-f]{12}$" -or
      $databaseName -ceq "teruisi_sales") {
    throw "隔离演练数据库名称不符合固定白名单"
  }
  $rehearsalPaths = Resolve-ExactRehearsalRoot $RehearsalId $false
  $rehearsalParent = $rehearsalPaths.Parent
  New-Item -ItemType Directory -Path $rehearsalParent -Force | Out-Null
  $rehearsalRoot = $rehearsalPaths.Root
  if (Test-Path -LiteralPath $rehearsalRoot) {
    throw "相同 RehearsalId 的证据目录已存在；拒绝覆盖或续写"
  }

  New-Item -ItemType Directory -Path $rehearsalRoot | Out-Null
  New-Item -ItemType Directory -Path (
    (Join-Path $rehearsalRoot "audit"),
    (Join-Path $rehearsalRoot "logs"),
    (Join-Path $rehearsalRoot "run")
  ) -Force | Out-Null
  $startedAt = [DateTimeOffset]::UtcNow.ToString("o")
  $rehearsalCutoverId = "rehearsal-$RehearsalId"
  $statePath = Join-Path $rehearsalRoot "rehearsal-state.json"
  $state = [ordered]@{
    version = "teruisi-sales-cutover-rehearsal-v1"
    rehearsalId = $RehearsalId
    cutoverId = $rehearsalCutoverId
    databaseName = $databaseName
    backupCutoverId = [string]$backup.Manifest.cutoverId
    backupManifestSha256 = [string]$backup.ManifestSha256
    startedAt = $startedAt
    updatedAt = $startedAt
    status = "running"
    steps = @()
    payloadDisposition = "unresolved"
    payloadCleanupAuditPath = ""
    payloadCleanupAuditSha256 = ""
  }
  Write-AtomicJson $statePath $state

  $secrets = $null
  $superuserPassword = $null
  $postgresStarted = $false
  $databaseCreated = $false
  $databasePreflightAbsent = $false
  $readerPidPath = Join-Path $rehearsalRoot "run\django-reader.pid.json"
  $writerPidPath = Join-Path $rehearsalRoot "run\django-writer.pid.json"
  $erpPidPath = Join-Path $rehearsalRoot "run\erp-reference-sync.pid.json"
  $readerService = "sales-rehearsal-reader-$RehearsalId"
  $writerService = "sales-rehearsal-writer-$RehearsalId"
  $erpService = "sales-rehearsal-erp-$RehearsalId"
  $operationError = $null
  $cleanupError = $null
  $previousPgPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "Process")
  $previousAdminUrl = [Environment]::GetEnvironmentVariable(
    "TERUISI_REHEARSAL_ADMIN_DATABASE_URL", "Process"
  )
  $previousDatabaseName = [Environment]::GetEnvironmentVariable(
    "TERUISI_REHEARSAL_DATABASE_NAME", "Process"
  )
  $result = $null

  try {
    $diskCapacity = Get-RehearsalDiskCapacityEvidence $backup $rehearsalParent
    Add-RehearsalStep $state $statePath "disk_capacity_verified" $diskCapacity
    $secrets = Read-Secrets
    $vault = Read-JsonFile $CredentialPath "Django 本机 DPAPI 凭据库"
    if ([int]$vault.version -ne 2) {
      throw "隔离演练要求只读使用已完成升级的 DPAPI v2 凭据库"
    }
    $superuserPassword = Unprotect-Value ([string]$vault.postgresSuperuser) "postgresSuperuser"
    if ([string]::IsNullOrWhiteSpace($superuserPassword)) {
      throw "PostgreSQL superuser DPAPI 凭据缺失"
    }
    $postgresStarted = Start-Postgres

    $env:TERUISI_REHEARSAL_ADMIN_DATABASE_URL = Database-Url "postgres" (
      $superuserPassword
    ) "teruisi_rehearsal_admin_$RehearsalId" $ReaderStatementTimeoutMs "postgres"
    $env:TERUISI_REHEARSAL_DATABASE_NAME = $databaseName
    $databaseProbe = @'
import json
import os
import re

import psycopg

name = os.environ["TERUISI_REHEARSAL_DATABASE_NAME"]
if not re.fullmatch(r"teruisi_sales_rehearsal_[0-9a-f]{12}", name):
    raise RuntimeError("rehearsal database name rejected")
with psycopg.connect(os.environ["TERUISI_REHEARSAL_ADMIN_DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        exists = cursor.fetchone() is not None
print(json.dumps({"exists": exists}, separators=(",", ":")))
'@
    $probe = Invoke-PythonJsonCode $databaseProbe (
      "sales_cutover_rehearsal_database_preflight.py"
    ) $RuntimeRoot "PostgreSQL rehearsal database preflight"
    if ([bool]$probe.exists) {
      throw "隔离演练数据库已存在；拒绝覆盖、复用或删除"
    }
    $databasePreflightAbsent = $true
    Add-RehearsalStep $state $statePath "backup_verified" ([ordered]@{
      manifestSha256 = [string]$backup.ManifestSha256
      sourceD1Sha256 = [string]$backup.Manifest.sourceD1.sha256
      postgresqlDumpSha256 = [string]$backup.Manifest.postgresql.sha256
      r2ManifestSha256 = [string]$backup.Manifest.r2State.manifestSha256
    })

    $persistRoot = Join-Path $rehearsalRoot ".wrangler\state"
    $d1Directory = Join-Path $persistRoot "v3\d1\rehearsal-D1DatabaseObject"
    $r2Parent = Join-Path $persistRoot "v3"
    New-Item -ItemType Directory -Path $d1Directory, $r2Parent -Force | Out-Null
    $rehearsalD1 = Join-Path $d1Directory "source-d1.sqlite"
    Copy-Item -LiteralPath $backup.D1 -Destination $rehearsalD1
    if ((Get-FileSha256 $rehearsalD1) -cne [string]$backup.Manifest.sourceD1.sha256) {
      throw "隔离 D1 副本 SHA-256 回查失败"
    }
    $rehearsalR2 = Join-Path $r2Parent "r2"
    Copy-Item -LiteralPath $backup.R2 -Destination $rehearsalR2 -Recurse
    Assert-R2TreeMatchesManifest $rehearsalR2 $backup.R2Manifest "隔离 R2 副本"
    Add-RehearsalStep $state $statePath "d1_r2_copied" ([ordered]@{
      sourceD1Sha256 = Get-FileSha256 $rehearsalD1
      r2FileCount = [int64]$backup.R2Manifest.fileCount
      r2SizeBytes = [int64]$backup.R2Manifest.sizeBytes
    })

    $databaseCreate = @'
import json
import os
import re

import psycopg
from psycopg import sql

name = os.environ["TERUISI_REHEARSAL_DATABASE_NAME"]
if not re.fullmatch(r"teruisi_sales_rehearsal_[0-9a-f]{12}", name):
    raise RuntimeError("rehearsal database name rejected")
connection = psycopg.connect(os.environ["TERUISI_REHEARSAL_ADMIN_DATABASE_URL"])
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if cursor.fetchone() is not None:
        raise RuntimeError("rehearsal database already exists")
    cursor.execute(
        sql.SQL("CREATE DATABASE {} OWNER teruisi_sales_owner TEMPLATE template0 ENCODING 'UTF8'").format(
            sql.Identifier(name)
        )
    )
connection.close()
print(json.dumps({"status": "created", "databaseName": name}, separators=(",", ":")))
'@
    $created = Invoke-PythonJsonCode $databaseCreate (
      "sales_cutover_rehearsal_database_create.py"
    ) $RuntimeRoot "PostgreSQL rehearsal database create"
    if ([string]$created.status -cne "created" -or
        [string]$created.databaseName -cne $databaseName) {
      throw "隔离演练数据库创建回查失败"
    }
    $databaseCreated = $true
    Add-RehearsalStep $state $statePath "rehearsal_database_created" ([ordered]@{
      databaseName = $databaseName
    })

    $pgRestore = Join-Path $PostgresBin "pg_restore.exe"
    if ([int64](Get-Item -LiteralPath $backup.DatabaseDump).Length -ne
          [int64]$backup.Manifest.postgresql.sizeBytes -or
        (Get-FileSha256 $backup.DatabaseDump) -cne
          [string]$backup.Manifest.postgresql.sha256) {
      throw "PostgreSQL 备份在 restore 前发生变化"
    }
    $archiveListRun = Invoke-BoundedNativeProcess $pgRestore @(
      "--list", $backup.DatabaseDump
    ) $canonicalRuntime
    $archiveList = @($archiveListRun.Output)
    if ($archiveListRun.ExitCode -ne 0 -or
        $archiveList.Count -ne [int]$backup.Manifest.postgresql.archiveEntryCount) {
      throw (
        "PostgreSQL 备份归档目录复验失败（" +
        "$(Get-NativeFailureSummary $archiveListRun)）"
      )
    }
    $env:PGPASSWORD = $secrets.OwnerPassword
    $restoreRun = Invoke-BoundedNativeProcess $pgRestore @(
      "--host=127.0.0.1", "--port=5432", "--username=teruisi_sales_owner",
      "--dbname=$databaseName", "--exit-on-error", "--single-transaction",
      "--no-owner", "--no-privileges", $backup.DatabaseDump
    ) $canonicalRuntime
    if ($restoreRun.ExitCode -ne 0) {
      throw (
        "PostgreSQL 备份恢复到隔离数据库失败（" +
        "$(Get-NativeFailureSummary $restoreRun)）"
      )
    }
    Invoke-DjangoMigrations $secrets $databaseName
    Add-RehearsalStep $state $statePath "postgresql_dump_restored" ([ordered]@{
      databaseName = $databaseName
      archiveEntryCount = $archiveList.Count
      migrationsApplied = $true
    })

    $cutoverTool = Join-Path $InstalledAppRoot "tools\sales-local-cutover.ts"
    $r2Tool = Join-Path $InstalledAppRoot "tools\sales-legacy-r2-cleanup.ts"
    $retirementTool = Join-Path $InstalledAppRoot "tools\sales-d1-retirement.ts"
    $wranglerCli = Join-Path $InstalledAppRoot "runtime-tools\node_modules\wrangler\wrangler-dist\cli.js"
    $auditDirectory = Join-Path $rehearsalRoot "audit\cutover"
    New-Item -ItemType Directory -Path $auditDirectory -Force | Out-Null
    $cutoverDigest = Get-Sha256Text $rehearsalCutoverId
    $r2CleanupManifest = Join-Path $auditDirectory (
      "sales-cutover-$($cutoverDigest.Substring(0, 24)).legacy-r2-cleanup.json"
    )
    $cleanupPlan = Invoke-JsonProcess $Node @(
      $r2Tool,
      "--dry-run",
      "--source", $rehearsalD1,
      "--cutover-id", $rehearsalCutoverId,
      "--bucket", "site-creator-r2",
      "--persist-to", $persistRoot,
      "--manifest", $r2CleanupManifest
    ) $InstalledAppRoot "隔离 R2 cleanup dry-run"
    if ([string]$cleanupPlan.status -cne "dry_run_completed" -or
        [string]$cleanupPlan.manifestId -cnotmatch "^[0-9a-f]{64}$") {
      throw "隔离 R2 cleanup dry-run 结果无效"
    }
    Add-RehearsalStep $state $statePath "r2_cleanup_planned" ([ordered]@{
      manifestId = [string]$cleanupPlan.manifestId
      sessions = [int64]$cleanupPlan.sessions
      objects = [int64]$cleanupPlan.objects
    })

    $ownerUrl = Database-Url "teruisi_sales_owner" $secrets.OwnerPassword (
      "teruisi_rehearsal_cutover_$RehearsalId"
    ) $WriterStatementTimeoutMs $databaseName
    $erpUrl = Database-Url "teruisi_erp_reference_sync" $secrets.ErpSyncPassword (
      "teruisi_rehearsal_erp_$RehearsalId"
    ) $WriterStatementTimeoutMs $databaseName
    $previousErpUrl = [Environment]::GetEnvironmentVariable(
      "TERUISI_DJANGO_ERP_DATABASE_URL", "Process"
    )
    $previousWrangler = [Environment]::GetEnvironmentVariable(
      "TERUISI_WRANGLER_CLI_JS", "Process"
    )
    $previousRehearsalCutoverManaged = [Environment]::GetEnvironmentVariable(
      "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED", "Process"
    )
    $previousProductionCutoverManaged = [Environment]::GetEnvironmentVariable(
      "TERUISI_DJANGO_CUTOVER_MANAGED", "Process"
    )
    try {
      $cutoverResult = Invoke-WithDjangoEnvironment $secrets $ownerUrl `
        "migration_writer" $false $WriterMaxBodyBytes "" "" {
          $env:TERUISI_DJANGO_ERP_DATABASE_URL = $erpUrl
          $env:TERUISI_WRANGLER_CLI_JS = $wranglerCli
          $env:TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED = "1"
          [Environment]::SetEnvironmentVariable(
            "TERUISI_DJANGO_CUTOVER_MANAGED", $null, "Process"
          )
          Invoke-JsonProcess $Node @(
            $cutoverTool,
            "--managed-rehearsal-execute",
            "--confirmed-maintenance",
            "--runtime-root", $canonicalRuntime,
            "--source", $rehearsalD1,
            "--cutover-id", $rehearsalCutoverId,
            "--audit-dir", $auditDirectory,
            "--backend-dir", $BackendRoot,
            "--python", $Python,
            "--r2-persist-to", $persistRoot,
            "--approved-r2-cleanup-manifest-id", ([string]$cleanupPlan.manifestId),
            "--repository-root", $InstalledAppRoot
          ) $InstalledAppRoot "隔离 Django/PostgreSQL cutover"
        }
    } finally {
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_ERP_DATABASE_URL", $previousErpUrl, "Process"
      )
      [Environment]::SetEnvironmentVariable(
        "TERUISI_WRANGLER_CLI_JS", $previousWrangler, "Process"
      )
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED",
        $previousRehearsalCutoverManaged,
        "Process"
      )
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_CUTOVER_MANAGED",
        $previousProductionCutoverManaged,
        "Process"
      )
    }
    $requiredCutoverSteps = @(
      "d1_0090_0091_pre_schema",
      "postgres_schema_migrated",
      "erp_reference_checkpoint_caught_up",
      "sales_snapshot_dry_run",
      "sales_snapshot_applied",
      "sales_snapshot_verified_before_prepare",
      "d1_locked_verify_cleanup_pending",
      "postgres_cutover_evidence_verified",
      "d1_authority_postgresql_terminal",
      "d1_terminal_attested",
      "postgres_authority_activated"
    )
    if ([string]$cutoverResult.status -cne "completed" -or
        [string]$cutoverResult.cutoverId -cne $rehearsalCutoverId -or
        @($requiredCutoverSteps | Where-Object {
          @($cutoverResult.steps) -notcontains $_
        }).Count -gt 0) {
      throw "隔离 cutover 缺少终态或必需步骤"
    }
    $cutoverStatePath = Join-Path $auditDirectory (
      "sales-cutover-$($cutoverDigest.Substring(0, 24)).state.json"
    )
    $cutoverState = Read-JsonFile $cutoverStatePath "隔离 cutover state"
    $dryStep = @($cutoverState.steps | Where-Object {
      [string]$_.name -ceq "sales_snapshot_dry_run"
    })
    $applyStep = @($cutoverState.steps | Where-Object {
      [string]$_.name -ceq "sales_snapshot_applied"
    })
    $attestationStep = @($cutoverState.steps | Where-Object {
      [string]$_.name -ceq "d1_terminal_attested"
    })
    if ($dryStep.Count -ne 1 -or $applyStep.Count -ne 1 -or
        $attestationStep.Count -ne 1 -or
        [string]$dryStep[0].result.canonicalFormatVersion -cne "sales-projection-v4" -or
        [string]$applyStep[0].result.canonicalFormatVersion -cne "sales-projection-v4" -or
        [string]$dryStep[0].result.runId -cnotmatch "^[0-9a-f]{32,64}$" -or
        [string]$applyStep[0].result.runId -cnotmatch "^[0-9a-f]{32,64}$" -or
        [string]$attestationStep[0].result.payloadSha256 -cnotmatch "^[0-9a-f]{64}$") {
      throw "隔离 cutover 没有生成 fresh v4 dry/apply/attestation 证据"
    }
    $attestationSha256 = [string]$attestationStep[0].result.payloadSha256
    $attestationPath = Get-CanonicalPath ([string]$attestationStep[0].result.attestationPath)
    if (-not $attestationPath.StartsWith(
        (Get-CanonicalPath $auditDirectory) + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
      ) -or -not (Test-Path -LiteralPath $attestationPath -PathType Leaf)) {
      throw "隔离 attestation 不在演练 audit 目录"
    }
    Add-RehearsalStep $state $statePath "cutover_completed" ([ordered]@{
      dryRunId = [string]$dryStep[0].result.runId
      applyRunId = [string]$applyStep[0].result.runId
      canonicalFormatVersion = "sales-projection-v4"
      attestationPayloadSha256 = $attestationSha256
    })

    $writerUrl = Database-Url "teruisi_sales_writer" $secrets.WriterPassword (
      "teruisi_rehearsal_writer_probe_$RehearsalId"
    ) $WriterStatementTimeoutMs $databaseName
    $readerUrl = Database-Url "teruisi_sales_reader" $secrets.ReaderPassword (
      "teruisi_rehearsal_reader_$RehearsalId"
    ) $ReaderStatementTimeoutMs $databaseName
    $authority = Invoke-DjangoJson $secrets $writerUrl "migration_writer" $false `
      $ReaderMaxBodyBytes "" "" @("sales_write_authority", "status") (
        "隔离 PostgreSQL authority status"
      )
    if ([string]$authority.status -cne "active" -or
        [string]$authority.cutoverId -cne $rehearsalCutoverId -or
        [string]$authority.authorityEpoch -cnotmatch "^[0-9a-fA-F-]{36}$") {
      throw "隔离 PostgreSQL authority 未进入 active"
    }

    $manage = Join-Path $BackendRoot "manage.py"
    $erpArguments = @(
      $manage,
      "sync_erp_reference",
      "--source", $rehearsalD1,
      "--interval-seconds", "15",
      "--max-events", "1000",
      "--batch-size", "1000",
      "--source-change-retries", "3",
      "--transient-db-retries", "5",
      "--watch"
    )
    $erpFingerprint = Get-Sha256Text (
      (Get-ConfigFingerprint $erpService $Python $erpArguments) + "|" +
      $databaseName + "|" + (Get-Sha256Text $rehearsalD1)
    )
    $erpStdout = Join-Path $rehearsalRoot "logs\erp.stdout.log"
    $erpStderr = Join-Path $rehearsalRoot "logs\erp.stderr.log"
    Invoke-WithDjangoEnvironment $secrets $erpUrl "erp_reference_sync" $false `
      $ReaderMaxBodyBytes "" "" {
        Start-ManagedProcess $erpService $Python $erpArguments $BackendRoot `
          $erpPidPath $erpFingerprint $erpStdout $erpStderr | Out-Null
      }
    $erpStatus = Wait-RehearsalErpCaughtUp $secrets $erpUrl $rehearsalD1

    $readerArguments = @(
      "--listen=127.0.0.1:8001", "--threads=8", "--connection-limit=100",
      "--channel-timeout=35", "--cleanup-interval=30",
      "--ident=teruisi-django-rehearsal-reader",
      "--max-request-header-size=$MaxHeaderBytes",
      "--max-request-body-size=$ReaderMaxBodyBytes",
      "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
    )
    $readerFingerprint = Get-Sha256Text (
      (Get-ConfigFingerprint $readerService $Waitress $readerArguments) + "|" +
      $databaseName
    )
    Invoke-WithDjangoEnvironment $secrets $readerUrl "reader" $true `
      $ReaderMaxBodyBytes "" "" {
        Start-ManagedProcess $readerService $Waitress $readerArguments $BackendRoot `
          $readerPidPath $readerFingerprint `
          (Join-Path $rehearsalRoot "logs\reader.stdout.log") `
          (Join-Path $rehearsalRoot "logs\reader.stderr.log") | Out-Null
      }
    Wait-DjangoReady "rehearsal reader" "http://127.0.0.1:8001/health/ready" (
      "127.0.0.1:8001"
    ) 45

    $writerArguments = @(
      "--listen=127.0.0.1:8002", "--threads=4", "--connection-limit=20",
      "--channel-timeout=960", "--cleanup-interval=30",
      "--ident=teruisi-django-rehearsal-writer",
      "--max-request-header-size=$MaxHeaderBytes",
      "--max-request-body-size=$WriterMaxBodyBytes",
      "--no-expose-tracebacks", "teruisi_backend.wsgi:application"
    )
    $writerFingerprint = Get-Sha256Text (
      (Get-ConfigFingerprint $writerService $Waitress $writerArguments) + "|" +
      $databaseName + "|" + [string]$authority.authorityEpoch + "|" +
      $rehearsalCutoverId
    )
    Invoke-WithDjangoEnvironment $secrets $writerUrl "sales_writer" $false `
      $WriterMaxBodyBytes ([string]$authority.authorityEpoch) $rehearsalCutoverId {
        Start-ManagedProcess $writerService $Waitress $writerArguments $BackendRoot `
          $writerPidPath $writerFingerprint `
          (Join-Path $rehearsalRoot "logs\writer.stdout.log") `
          (Join-Path $rehearsalRoot "logs\writer.stderr.log") | Out-Null
      }
    Wait-DjangoReady "rehearsal writer" "http://127.0.0.1:8002/health/ready" (
      "127.0.0.1:8002"
    ) 45
    Add-RehearsalStep $state $statePath "django_services_ready" ([ordered]@{
      reader = "127.0.0.1:8001"
      writer = "127.0.0.1:8002"
      erpStatus = [string]$erpStatus.status
      worker3000Started = $false
      helper5791Started = $false
    })

    $retirementAudit = Join-Path $rehearsalRoot "audit\sales-retirement.json"
    $retirementPlan = Invoke-JsonProcess $Node @(
      $retirementTool,
      "--source", $rehearsalD1,
      "--cutover-id", $rehearsalCutoverId,
      "--attestation", $attestationPath,
      "--attestation-sha256", $attestationSha256,
      "--audit-output", $retirementAudit
    ) $InstalledAppRoot "隔离 D1 retirement plan"
    if ([string]$retirementPlan.status -cne "planned" -or
        [string]$retirementPlan.planId -cnotmatch "^[0-9a-f]{64}$") {
      throw "隔离 D1 retirement plan 未通过"
    }
    Assert-RetirementPlanBlockersClear $retirementPlan.blockers
    Add-RehearsalStep $state $statePath "retirement_planned" ([ordered]@{
      planId = [string]$retirementPlan.planId
      preservedEvidenceSha256 = [string]$retirementPlan.preservedEvidenceSha256
    })

    $smokeBundle = Join-Path $rehearsalRoot "audit\smoke-receipt-bundle"
    $smoke = Invoke-DjangoJson $secrets $writerUrl "migration_writer" $false `
      $WriterMaxBodyBytes ([string]$authority.authorityEpoch) $rehearsalCutoverId @(
        "sales_cutover_smoke_receipt",
        "--plan-id", ([string]$retirementPlan.planId),
        "--cutover-id", $rehearsalCutoverId,
        "--attestation-sha256", $attestationSha256,
        "--output-directory", $smokeBundle,
        "--reader-base-url", "http://127.0.0.1:8001",
        "--writer-base-url", "http://127.0.0.1:8002"
      ) "隔离正式 smoke receipt"
    $smokeReceiptPath = Join-Path $smokeBundle "receipt.json"
    if ([string]$smoke.status -cne "completed" -or
        [string]$smoke.planId -cne [string]$retirementPlan.planId -or
        [string]$smoke.cutoverId -cne $rehearsalCutoverId -or
        [string]$smoke.attestationPayloadSha256 -cne $attestationSha256 -or
        [string]$smoke.receiptSha256 -cnotmatch "^[0-9a-f]{64}$" -or
        (Get-FileSha256 $smokeReceiptPath) -cne [string]$smoke.receiptSha256) {
      throw "隔离正式 smoke receipt 身份或 SHA-256 无效"
    }
    Add-RehearsalStep $state $statePath "formal_smoke_completed" ([ordered]@{
      planId = [string]$smoke.planId
      receiptSha256 = [string]$smoke.receiptSha256
      attestationPayloadSha256 = $attestationSha256
    })

    $rehearsalRetirementUrl = Database-Url "teruisi_sales_writer" (
      $secrets.WriterPassword
    ) "teruisi_sales_rehearsal_retirement_$RehearsalId" (
      $WriterStatementTimeoutMs
    ) $databaseName
    $previousRehearsalManaged = [Environment]::GetEnvironmentVariable(
      "TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED", "Process"
    )
    $previousProductionManaged = [Environment]::GetEnvironmentVariable(
      "TERUISI_DJANGO_RETIREMENT_MANAGED", "Process"
    )
    try {
      $env:TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED = "1"
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_RETIREMENT_MANAGED", $null, "Process"
      )
      $retired = Invoke-WithDjangoEnvironment $secrets $rehearsalRetirementUrl `
        "migration_writer" $false $WriterMaxBodyBytes `
        ([string]$authority.authorityEpoch) $rehearsalCutoverId {
          Invoke-JsonProcess $Node @(
            $retirementTool,
            "--managed-rehearsal-execute",
            "--runtime-root", $canonicalRuntime,
            "--rehearsal-root", $rehearsalRoot,
            "--rehearsal-id", $RehearsalId,
            "--database-name", $databaseName,
            "--source", $rehearsalD1,
            "--cutover-id", $rehearsalCutoverId,
            "--attestation", $attestationPath,
            "--attestation-sha256", $attestationSha256,
            "--audit-output", $retirementAudit,
            "--approved-plan-id", ([string]$retirementPlan.planId),
            "--smoke-receipt", $smokeReceiptPath,
            "--smoke-receipt-sha256", ([string]$smoke.receiptSha256)
          ) $rehearsalRoot "隔离 D1 0092 retirement"
        }
    } finally {
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED",
        $previousRehearsalManaged,
        "Process"
      )
      [Environment]::SetEnvironmentVariable(
        "TERUISI_DJANGO_RETIREMENT_MANAGED", $previousProductionManaged, "Process"
      )
      $rehearsalRetirementUrl = $null
    }
    if ([string]$retired.status -cne "completed" -or
        [string]$retired.cutoverId -cne $rehearsalCutoverId -or
        [string]$retired.approvedPlanId -cne [string]$retirementPlan.planId -or
        [string]$retired.auditId -cnotmatch "^[0-9a-f]{64}$" -or
        [string]$retired.preservedEvidenceSha256 -cne
          [string]$retirementPlan.preservedEvidenceSha256) {
      throw "隔离 D1 0092 retirement 或 ERP/非 sales 保留验证失败"
    }

    $postRetirementPlan = Invoke-JsonProcess $Node @(
      $retirementTool,
      "--source", $rehearsalD1,
      "--cutover-id", $rehearsalCutoverId,
      "--attestation", $attestationPath,
      "--attestation-sha256", $attestationSha256,
      "--audit-output", $retirementAudit
    ) $InstalledAppRoot "隔离 D1 retirement 终态回查"
    if ([string]$postRetirementPlan.status -cne "already_completed" -or
        [string]$postRetirementPlan.planId -cne [string]$retirementPlan.planId -or
        [string]$postRetirementPlan.auditId -cne [string]$retired.auditId) {
      throw "隔离 D1 0092 retirement 终态回查失败"
    }
    $postErpStatus = Wait-RehearsalErpCaughtUp $secrets $erpUrl $rehearsalD1 (
      [string]$erpStatus.lastCheckedAt
    ) 45
    Wait-DjangoReady "post-retirement rehearsal reader" (
      "http://127.0.0.1:8001/health/ready"
    ) "127.0.0.1:8001" 30
    Wait-DjangoReady "post-retirement rehearsal writer" (
      "http://127.0.0.1:8002/health/ready"
    ) "127.0.0.1:8002" 30
    if (@(Get-PortListeners 3000).Count -gt 0 -or
        @(Get-PortListeners 5791).Count -gt 0) {
      throw "隔离演练不得启动 Worker/工作流辅助端口 3000/5791"
    }
    Add-RehearsalStep $state $statePath "d1_0092_retired_and_preserved" ([ordered]@{
      auditId = [string]$retired.auditId
      approvedPlanId = [string]$retired.approvedPlanId
      preservedEvidenceSha256 = [string]$retired.preservedEvidenceSha256
      erpStatus = [string]$postErpStatus.status
      terminalStatus = [string]$postRetirementPlan.status
    })

    $result = [pscustomobject][ordered]@{
      status = "completed"
      version = "teruisi-sales-cutover-rehearsal-result-v1"
      rehearsalId = $RehearsalId
      cutoverId = $rehearsalCutoverId
      productionCutoverId = [string]$backup.Manifest.cutoverId
      databaseName = $databaseName
      backupCreatedAt = [string]$backup.Manifest.createdAt
      backupManifestSha256 = [string]$backup.ManifestSha256
      deploymentManifestSha256 = [string]$backup.Manifest.deploymentManifestSha256
      serviceConfigSha256 = [string]$backup.Manifest.serviceConfigSha256
      sourcePathSha256 = [string]$backup.Manifest.sourcePathSha256
      sourceD1Sha256 = [string]$backup.Manifest.sourceD1.sha256
      r2ManifestSha256 = [string]$backup.Manifest.r2State.manifestSha256
      postgresqlDumpSha256 = [string]$backup.Manifest.postgresql.sha256
      attestationPayloadSha256 = $attestationSha256
      smokeReceiptSha256 = [string]$smoke.receiptSha256
      retirementAuditId = [string]$retired.auditId
      preservedEvidenceSha256 = [string]$retired.preservedEvidenceSha256
      evidenceDirectory = $rehearsalRoot
      completedAt = ""
    }
  } catch {
    $operationError = $_.Exception
  }

  $payloadCleanupPreconditionErrors = @()
  foreach ($owned in @(
    [pscustomobject]@{ Service = $writerService; Pid = $writerPidPath; Executable = $Waitress },
    [pscustomobject]@{ Service = $readerService; Pid = $readerPidPath; Executable = $Waitress },
    [pscustomobject]@{ Service = $erpService; Pid = $erpPidPath; Executable = $Python }
  )) {
    try {
      Stop-OwnedProcess $owned.Service $owned.Pid $owned.Executable
    } catch {
      if ($null -eq $cleanupError) { $cleanupError = $_.Exception }
      $payloadCleanupPreconditionErrors += [ordered]@{
        stage = "owned_process_stop"
        failureClass = $_.Exception.GetType().FullName
        failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
      }
    }
  }

  if ($null -eq $operationError -and $null -eq $cleanupError) {
    try {
      if ($postgresStarted) {
        Stop-Postgres
        $postgresStarted = $false
      }
      Add-RehearsalStep $state $statePath "rehearsal_processes_stopped" ([ordered]@{
        reader = "stopped"
        writer = "stopped"
        erp = "stopped"
        databaseRetained = $true
      })
      $completedAt = [DateTimeOffset]::UtcNow.ToString("o")
      $result.completedAt = $completedAt
      Assert-ExactPropertySet $result @(
        "status", "version", "rehearsalId", "cutoverId", "productionCutoverId",
        "databaseName", "backupCreatedAt", "backupManifestSha256",
        "deploymentManifestSha256", "serviceConfigSha256", "sourcePathSha256",
        "sourceD1Sha256", "r2ManifestSha256", "postgresqlDumpSha256",
        "attestationPayloadSha256", "smokeReceiptSha256", "retirementAuditId",
        "preservedEvidenceSha256", "evidenceDirectory", "completedAt"
      ) "隔离演练结果"
      if ([string]$result.deploymentManifestSha256 -cne
            (Get-FileSha256 $DeploymentManifestPath) -or
          [string]$result.serviceConfigSha256 -cne (Get-FileSha256 $ConfigPath)) {
        throw "隔离演练期间 runtime 部署或服务配置发生变化"
      }
      $resultPath = Join-Path $rehearsalRoot "rehearsal-result.json"
      $resultShaPath = Join-Path $rehearsalRoot "rehearsal-result.json.sha256"
      Write-AtomicJson $resultPath $result
      $resultSha256 = Get-FileSha256 $resultPath
      if ($resultSha256 -cnotmatch "^[0-9a-f]{64}$") {
        throw "隔离演练结果 SHA-256 生成失败"
      }
      [IO.File]::WriteAllText(
        $resultShaPath,
        $resultSha256 + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
      )
      if ((Read-ExactSha256File $resultShaPath "隔离演练结果 SHA-256") -cne
          (Get-FileSha256 $resultPath)) {
        throw "隔离演练结果与 SHA-256 sidecar 回查失败"
      }
      $state.status = "completed"
      $state.updatedAt = $completedAt
      $state.completedAt = $completedAt
      $state.resultFileSha256 = $resultSha256
      Write-AtomicJson $statePath $state
      $result = [ordered]@{
        status = "completed"
        version = "teruisi-sales-cutover-rehearsal-reference-v1"
        rehearsalId = $RehearsalId
        resultPath = $resultPath
        resultSha256 = $resultSha256
      }
    } catch {
      if ($null -eq $cleanupError) { $cleanupError = $_.Exception }
    }
  }

  $payloadCleanup = $null
  if ($null -ne $operationError -or $null -ne $cleanupError) {
    $errorValue = if ($null -ne $operationError) { $operationError } else { $cleanupError }
    $failedAt = [DateTimeOffset]::UtcNow.ToString("o")
    $state.status = "failed"
    $state.updatedAt = $failedAt
    $state.failedAt = $failedAt
    $state.failureClass = $errorValue.GetType().FullName
    $state.failureMessageSha256 = Get-Sha256Text (Protect-LogText $errorValue.Message)
    $state.databaseRetained = $databasePreflightAbsent
    Write-AtomicJson $statePath $state

    if ($databasePreflightAbsent -and -not (Test-PostgresReady)) {
      try {
        $postgresStarted = Start-Postgres
      } catch {
        $payloadCleanupPreconditionErrors += [ordered]@{
          stage = "database_cleanup_postgres_start"
          failureClass = $_.Exception.GetType().FullName
          failureMessageSha256 = Get-Sha256Text (Protect-LogText $_.Exception.Message)
        }
      }
    }
    try {
      $payloadCleanup = Invoke-RehearsalPayloadCleanup `
        $state $statePath $rehearsalRoot $databaseName $databasePreflightAbsent `
        $databasePreflightAbsent $payloadCleanupPreconditionErrors
    } catch {
      if ($null -eq $cleanupError) { $cleanupError = $_.Exception }
    }
    if ($null -eq $payloadCleanup -or -not $payloadCleanup.Completed) {
      if ($null -eq $cleanupError) {
        $cleanupError = [InvalidOperationException]::new(
          "失败演练 payload 清理未完成；state 保持 payloadDisposition=unresolved"
        )
      }
    }
  }

  if ($postgresStarted) {
    try {
      Stop-Postgres
      $postgresStarted = $false
    } catch {
      Write-LauncherEvent "WARN" "rehearsal_postgres_stop_failed" (
        Get-Sha256Text (Protect-LogText $_.Exception.Message)
      )
      if ($null -eq $cleanupError) { $cleanupError = $_.Exception }
    }
  }
  [Environment]::SetEnvironmentVariable("PGPASSWORD", $previousPgPassword, "Process")
  [Environment]::SetEnvironmentVariable(
    "TERUISI_REHEARSAL_ADMIN_DATABASE_URL", $previousAdminUrl, "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "TERUISI_REHEARSAL_DATABASE_NAME", $previousDatabaseName, "Process"
  )
  $secrets = $null
  $superuserPassword = $null

  if ($null -ne $operationError -or $null -ne $cleanupError) {
    throw "隔离演练失败；大体积 payload 与本次演练数据库已按 cleanup audit 处置或保持 unresolved，生产数据未触碰"
  }
  return $result
}

$result = Invoke-WithServiceMutex {
  if ($CleanupFailedRehearsal.IsPresent) {
    Invoke-ExplicitFailedRehearsalCleanup
  } elseif ($AbandonCompletedRehearsal.IsPresent) {
    Invoke-AbandonCompletedRehearsal
  } else {
    Invoke-IsolatedRehearsal
  }
}
$result | ConvertTo-Json -Compress -Depth 8
