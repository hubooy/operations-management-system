$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "..\tools\operations-system-service.ps1"
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$null, [ref]$parseErrors)
if ($parseErrors -and $parseErrors.Count -gt 0) {
  throw "operations-system-service.ps1 has parse errors: $(($parseErrors | ForEach-Object { $_.Message }) -join '; ')"
}

$previousLibraryOnly = [Environment]::GetEnvironmentVariable("TERUISI_SYSTEM_SERVICE_LIBRARY_ONLY", "Process")
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("teruisi-system-service-" + [Guid]::NewGuid().ToString("N"))
try {
  $env:TERUISI_SYSTEM_SERVICE_LIBRARY_ONLY = "1"
  . $scriptPath -Action Status
  if ($ServiceVersion -ne "teruisi-operations-system-service-v1") { throw "library mode did not load script variables" }
  if ($SystemControlMutexName -ne "Local\TERUISI.Operations.SystemControl.v2") { throw "mutex name must match the control panel" }

  # Log helpers must tolerate a file still being written by a child process and only forward new bytes.
  [IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
  $logPath = Join-Path $temporaryRoot "sample.stdout.log"
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($logPath, "第一行`n第二行`n", $utf8)
  $forwarded = Copy-NewOutput $logPath 0
  if ($forwarded -isnot [int] -or $forwarded -le 0) { throw "Copy-NewOutput must return the forwarded byte offset" }
  [IO.File]::AppendAllText($logPath, "第三行`n", $utf8)
  $again = Copy-NewOutput $logPath $forwarded
  if ($again -le $forwarded) { throw "Copy-NewOutput must advance past appended output" }
  if ((Copy-NewOutput $logPath $again) -ne $again) { throw "Copy-NewOutput must be stable when nothing new was written" }
  $tail = Get-BoundedTail $logPath 6
  if ($tail -ne "第三行" -and $tail.Length -gt 6) { throw "Get-BoundedTail must bound the returned text" }
  if ((Get-BoundedTail (Join-Path $temporaryRoot "missing.log")) -ne "") { throw "missing log must yield an empty tail" }

  # Log rotation keeps the newest files only.
  $script:LogRoot = Join-Path $temporaryRoot "logs"
  [IO.Directory]::CreateDirectory($script:LogRoot) | Out-Null
  for ($index = 0; $index -lt ($LogRetention + 5); $index++) {
    $file = Join-Path $script:LogRoot ("old-$index.stdout.log")
    [IO.File]::WriteAllText($file, "x", $utf8)
    [IO.File]::SetLastWriteTimeUtc($file, [DateTime]::UtcNow.AddMinutes(-$index))
  }
  Remove-OldLogs
  $remaining = @(Get-ChildItem -LiteralPath $script:LogRoot -Filter "*.log" -File)
  if ($remaining.Count -ne $LogRetention) { throw "Remove-OldLogs must keep exactly $LogRetention logs, kept $($remaining.Count)" }
  if (-not (Test-Path (Join-Path $script:LogRoot "old-0.stdout.log"))) { throw "Remove-OldLogs must keep the newest log" }
  $pair = New-LogPair "unit"
  if (-not ($pair.Stdout -like "*unit-*.stdout.log") -or -not ($pair.Stderr -like "*unit-*.stderr.log")) { throw "New-LogPair naming is wrong" }

  # A background receipt whose PID is gone, or was reused by another process, must not count as running.
  $script:BackgroundReceiptPath = Join-Path $temporaryRoot "background.json"
  if ($null -ne (Read-BackgroundReceipt)) { throw "missing receipt must read as null" }
  $current = Get-Process -Id $PID
  $currentStart = Get-ProcessStartUnixMilliseconds $current
  $reused = [ordered]@{
    version = "teruisi-operations-system-service-v1"; action = "Start"; processId = [int]$PID
    processStartedAtUnixMs = $currentStart - 30000
    startedAt = (Get-Date).ToString("o"); stdoutLog = $logPath; stderrLog = $logPath
  }
  [IO.File]::WriteAllText($script:BackgroundReceiptPath, ($reused | ConvertTo-Json -Depth 4), $utf8)
  $receipt = Read-BackgroundReceipt
  if ($receipt.running) { throw "a receipt whose start time differs from the live process must not be running" }
  $exact = [ordered]@{
    version = "teruisi-operations-system-service-v1"; action = "Start"; processId = [int]$PID
    processStartedAtUnixMs = $currentStart
    startedAt = (Get-Date).ToString("o"); stdoutLog = $logPath; stderrLog = $logPath
  }
  [IO.File]::WriteAllText($script:BackgroundReceiptPath, ($exact | ConvertTo-Json -Depth 4), $utf8)
  if (-not (Read-BackgroundReceipt).running) { throw "an exact live receipt must be running" }
  [IO.File]::WriteAllText($script:BackgroundReceiptPath, "{not json", $utf8)
  if ($null -ne (Read-BackgroundReceipt)) { throw "a corrupt receipt must read as null" }

  Write-Output "PASS: operations-system-service helpers parse, forward bounded logs, rotate, and bind background receipts to process identity"
} finally {
  [Environment]::SetEnvironmentVariable("TERUISI_SYSTEM_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process")
  if (Test-Path -LiteralPath $temporaryRoot) {
    $resolved = [IO.Path]::GetFullPath($temporaryRoot)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
      throw "temporary test root escaped the OS temp directory"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
