[CmdletBinding()]
param(
  [string]$ProjectRoot = 'D:\运营管理系统',
  [string]$InstallRoot = 'D:\teruisi-runtime\n8n-launcher',
  [string]$TaskName = 'TERUISI-n8n-Service',
  [string]$TaskPath = '\',
  [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'n8n-launcher\NoConsoleLauncher.cs'
$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
$release = Join-Path $InstallRoot $sourceHash
$executable = Join-Path $release 'Teruisi.N8n.NoConsole.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'C# compiler unavailable.' }
if (Test-Path -LiteralPath $release) { throw 'Release directory already exists; choose a fresh InstallRoot.' }
New-Item -ItemType Directory -Path $release -Force | Out-Null
& $compiler /nologo /target:winexe /platform:anycpu /optimize+ "/out:$executable" $source
if ($LASTEXITCODE -ne 0) { throw 'No-console launcher compilation failed.' }
$binaryHash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
if ($BuildOnly) {
  [pscustomobject]@{ Executable=$executable; SourceSha256=$sourceHash; BinarySha256=$binaryHash } | ConvertTo-Json
  return
}

$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$script = Join-Path $project 'tools\start-n8n-service.ps1'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'n8n service script unavailable.' }
$task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction Stop
if ($task.State -eq 'Running') { throw 'n8n task must be stopped before its launcher or power policy is updated.' }
$expectedArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script + '"'
if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne 'powershell.exe' -or
    $task.Actions[0].Arguments -ne $expectedArguments -or $task.Actions[0].WorkingDirectory -ne $project) {
  throw 'Task action differs from the verified original; refusing to replace it.'
}
# Preserve a reviewable rollback artifact before changing only the executable/action.
$before = Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
$backup = Join-Path $release 'task-before.xml'
[IO.File]::WriteAllText($backup, $before, [Text.Encoding]::Unicode)
if ((Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath) -ne $before) { throw 'Task changed during preparation.' }
$newAction = New-ScheduledTaskAction -Execute $executable -Argument ('"' + $script + '"') -WorkingDirectory $project
$updatedSettings = $task.Settings
$updatedSettings.DisallowStartIfOnBatteries = $false
$updatedSettings.StopIfGoingOnBatteries = $false
Set-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $newAction -Settings $updatedSettings | Out-Null
$after = Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
[xml]$beforeXml = $before
[xml]$afterXml = $after
$beforeXml.Task.RemoveChild($beforeXml.Task.Actions) | Out-Null
$afterXml.Task.RemoveChild($afterXml.Task.Actions) | Out-Null
foreach ($settingName in @('DisallowStartIfOnBatteries', 'StopIfGoingOnBatteries')) {
  foreach ($document in @($beforeXml, $afterXml)) {
    $node = $document.Task.Settings.SelectSingleNode("./*[local-name()='$settingName']")
    if ($node) { $document.Task.Settings.RemoveChild($node) | Out-Null }
  }
}
$readback = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
if ($beforeXml.OuterXml -ne $afterXml.OuterXml -or
    $readback.Actions[0].Execute -ne $executable -or
    $readback.Actions[0].Arguments -ne ('"' + $script + '"') -or
    $readback.Actions[0].WorkingDirectory -ne $project -or
    $readback.Settings.DisallowStartIfOnBatteries -or
    $readback.Settings.StopIfGoingOnBatteries) {
  throw "Task readback mismatch; original task is preserved at $backup."
}
[IO.File]::WriteAllText((Join-Path $release 'task-after.xml'), $after, [Text.Encoding]::Unicode)
[pscustomobject]@{
  Executable=$executable; SourceSha256=$sourceHash; BinarySha256=$binaryHash
  Task=$TaskName; Backup=$backup; UnrelatedSettingsPreserved=$true
  ContinueOnBattery=$true; Restarted=$false
} | ConvertTo-Json
