function Invoke-N8nNativeProcess {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory=$true)][string]$NodePath,
    [Parameter(Mandatory=$true)][string]$EntryPath,
    [Parameter(Mandatory=$true)][string]$StdoutPath,
    [Parameter(Mandatory=$true)][string]$StderrPath
  )

  # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecords.
  # Copy both native byte streams directly so a warning cannot unwind the
  # service script and close its kill-on-close Job Object.
  foreach ($path in @($NodePath, $EntryPath, $StdoutPath, $StderrPath)) {
    if (-not [IO.Path]::IsPathRooted($path) -or $path -match '["\r\n]') {
      throw 'Native process paths must be absolute and contain no quotes or newlines.'
    }
  }
  if ([IO.Path]::GetFullPath($StdoutPath) -eq [IO.Path]::GetFullPath($StderrPath)) {
    throw 'Native stdout and stderr require separate log files.'
  }
  $stdout = $null
  $stderr = $null
  $process = New-Object Diagnostics.Process
  $started = $false
  try {
    $stdout = [IO.FileStream]::new($StdoutPath, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read, 1)
    $stderr = [IO.FileStream]::new($StderrPath, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read, 1)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $NodePath
    $info.Arguments = '"' + $EntryPath + '" start'
    $info.WorkingDirectory = (Get-Location).ProviderPath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process.StartInfo = $info
    $started = $process.Start()
    if (-not $started) { throw 'Native service process did not start.' }
    $process.StandardInput.Close()
    $outputCopy = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
    $errorCopy = $process.StandardError.BaseStream.CopyToAsync($stderr)
    while (-not $process.WaitForExit(250)) {
      if ($outputCopy.IsFaulted -or $errorCopy.IsFaulted) {
        throw 'Native service log stream failed.'
      }
    }
    [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($outputCopy, $errorCopy))
    $stdout.Flush()
    $stderr.Flush()
    return $process.ExitCode
  } finally {
    if ($started -and -not $process.HasExited) {
      $process.Kill()
      $process.WaitForExit()
    }
    $process.Dispose()
    if ($stdout) { $stdout.Dispose() }
    if ($stderr) { $stderr.Dispose() }
  }
}
