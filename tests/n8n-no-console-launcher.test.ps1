$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path $repo ('.runtime\n8n-no-console-test-' + [guid]::NewGuid().ToString('N'))
$build = & (Join-Path $repo 'tools\install-n8n-no-console.ps1') -BuildOnly -InstallRoot $testRoot | ConvertFrom-Json
$exe = $build.Executable
function Assert-Equal($actual, $expected, $label) {
  if ($actual -ne $expected) { throw "$label expected $expected; got $actual" }
}
function Invoke-Launcher([string]$arguments) {
  $p = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -PassThru
  if (-not $p.WaitForExit(20000)) { throw 'Launcher failed to exit within 20 seconds.' }
  $p.Refresh()
  return $p.ExitCode
}
# PE subsystem 2 means Windows GUI; no console is allocated for the wrapper.
$pe = [IO.File]::ReadAllBytes($exe)
$offset = [BitConverter]::ToInt32($pe, 0x3c)
Assert-Equal ([BitConverter]::ToUInt16($pe, $offset + 24 + 68)) 2 'PE subsystem'

$probeSource = Join-Path $testRoot 'Probe.cs'
@'
using System;
using System.IO;
using System.Runtime.InteropServices;
class Probe {
  [DllImport("kernel32.dll")] static extern IntPtr GetConsoleWindow();
  static int Main(string[] args) {
    File.WriteAllText(args[0], GetConsoleWindow().ToInt64().ToString());
    if (args.Length == 2) {
      File.WriteAllText(args[1], System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
      System.Threading.Thread.Sleep(60000);
    }
    return 0;
  }
}
'@ | Set-Content -LiteralPath $probeSource -Encoding UTF8
$probe = Join-Path $testRoot 'Probe.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:exe "/out:$probe" $probeSource
if ($LASTEXITCODE -ne 0) { throw 'Probe compilation failed.' }
$script = Join-Path $testRoot '中文 service probe.ps1'
@'
Get-NetTCPConnection -State Listen -ErrorAction Stop | Out-Null
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
[ConsoleProbe]::GetConsoleWindow().ToInt64() | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'powershell-console.txt')
& (Join-Path $PSScriptRoot 'Probe.exe') (Join-Path $PSScriptRoot 'grandchild-console.txt')
# Output without a console must not block or cause a child failure.
for ($i=0; $i -lt 3000; $i++) { [Console]::Out.WriteLine(('o' * 100)); [Console]::Error.WriteLine(('e' * 100)) }
Start-Sleep -Milliseconds 500
exit 37
'@ | Set-Content -LiteralPath $script -Encoding UTF8
$clock = [Diagnostics.Stopwatch]::StartNew()
Assert-Equal (Invoke-Launcher ('"' + $script + '"')) 37 'Child exit code'
if ($clock.ElapsedMilliseconds -lt 500) { throw 'Launcher did not wait for its child.' }
Assert-Equal ([IO.File]::ReadAllText((Join-Path $testRoot 'powershell-console.txt')).Trim()) '0' 'PowerShell console handle'
Assert-Equal ([IO.File]::ReadAllText((Join-Path $testRoot 'grandchild-console.txt')).Trim()) '0' 'Native grandchild console handle'
Assert-Equal (Invoke-Launcher ('"' + (Join-Path $testRoot 'missing.ps1') + '"')) 64 'Missing script rejection'
Assert-Equal (Invoke-Launcher ('"' + $script + '" extra')) 64 'Extra arguments rejection'
Assert-Equal (Invoke-Launcher 'relative.ps1') 64 'Relative path rejection'
'exit 0' | Set-Content -LiteralPath $script -Encoding UTF8
Assert-Equal (Invoke-Launcher ('"' + $script + '"')) 0 'Success exit code'
# Real Node warning + large concurrent stdout/stderr through Windows PS 5.1
# and the production Job launcher. This must survive stderr and retain bytes.
$nativeFixture = Join-Path $testRoot 'native warning fixture.cjs'
@'
const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
process.emitWarning('synthetic warning', 'DeprecationWarning');
const content = '测试UTF8\n' + 'x'.repeat(1024 * 1024) + '\n';
Promise.all([
  new Promise(resolve => process.stdout.write(content, resolve)),
  new Promise(resolve => process.stderr.write(content, resolve)),
]).then(() => setTimeout(() => {
  fs.writeFileSync(path.join(root, 'native-survived.txt'), 'survived');
  process.exit(37);
}, 300));
'@ | Set-Content -LiteralPath $nativeFixture -Encoding UTF8
$nativeWrapper = Join-Path $testRoot 'native-wrapper.ps1'
@'
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'tools\n8n-native-process.ps1')
$code = Invoke-N8nNativeProcess -NodePath (Get-Command node.exe).Source -EntryPath (Join-Path $PSScriptRoot 'native warning fixture.cjs') -StdoutPath (Join-Path $PSScriptRoot 'native.stdout.log') -StderrPath (Join-Path $PSScriptRoot 'native.stderr.log')
exit $code
'@ | Set-Content -LiteralPath $nativeWrapper -Encoding UTF8
Assert-Equal (Invoke-Launcher ('"' + $nativeWrapper + '"')) 37 'Warning does not override native exit code'
Assert-Equal ([IO.File]::ReadAllText((Join-Path $testRoot 'native-survived.txt'))) 'survived' 'Native process survived warning'
$nativeOut = [IO.File]::ReadAllText((Join-Path $testRoot 'native.stdout.log'), [Text.Encoding]::UTF8)
$nativeErr = [IO.File]::ReadAllText((Join-Path $testRoot 'native.stderr.log'), [Text.Encoding]::UTF8)
if (-not $nativeOut.StartsWith('测试UTF8') -or -not $nativeErr.Contains('测试UTF8') -or
    -not $nativeErr.Contains('synthetic warning') -or $nativeOut.Length -lt 1048576 -or $nativeErr.Length -lt 1048576) {
  throw 'Native log streams were truncated, lost, or corrupted.'
}
Assert-Equal (Invoke-Launcher ('"' + $nativeWrapper + '"')) 37 'Native log append exit code'
Assert-Equal ([IO.File]::ReadAllText((Join-Path $testRoot 'native.stdout.log')).Length) ($nativeOut.Length * 2) 'Native logs append without truncation'
# Verify the actual Windows scheduler lifecycle, using only a disposable probe task.
@'
Get-NetTCPConnection -State Listen -ErrorAction Stop | Out-Null
$PID | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'scheduled-child.txt')
& (Join-Path $PSScriptRoot 'Probe.exe') (Join-Path $PSScriptRoot 'scheduled-console.txt') (Join-Path $PSScriptRoot 'scheduled-native.txt')
'@ | Set-Content -LiteralPath $script -Encoding UTF8
$taskName = 'TERUISI-NoConsole-Test-' + [guid]::NewGuid().ToString('N')
$registered = $false
try {
  $action = New-ScheduledTaskAction -Execute $exe -Argument ('"' + $script + '"')
  $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal | Out-Null
  $registered = $true
  Start-ScheduledTask -TaskName $taskName
  $deadline = [DateTime]::UtcNow.AddSeconds(15)
  while (-not (Test-Path -LiteralPath (Join-Path $testRoot 'scheduled-native.txt'))) {
    if ([DateTime]::UtcNow -gt $deadline) { throw 'Scheduled child did not start.' }
    Start-Sleep -Milliseconds 100
  }
  $childId = [int](Get-Content -LiteralPath (Join-Path $testRoot 'scheduled-child.txt'))
  $nativeId = [int](Get-Content -LiteralPath (Join-Path $testRoot 'scheduled-native.txt'))
  Assert-Equal ([string](Get-ScheduledTask -TaskName $taskName).State) 'Running' 'Task waits for service'
  Assert-Equal ([IO.File]::ReadAllText((Join-Path $testRoot 'scheduled-console.txt')).Trim()) '0' 'Scheduled native console'
  Stop-ScheduledTask -TaskName $taskName
  $deadline = [DateTime]::UtcNow.AddSeconds(10)
  while ((Get-Process -Id $childId,$nativeId -ErrorAction SilentlyContinue)) {
    if ([DateTime]::UtcNow -gt $deadline) { throw 'Scheduler left an orphan child.' }
    Start-Sleep -Milliseconds 100
  }
} finally {
  if ($registered) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
  }
}
# Verify installation keeps unrelated scheduler settings but makes the service
# resilient to brief AC/battery transitions.
$policyTaskName = 'TERUISI-NoConsole-Policy-Test-' + [guid]::NewGuid().ToString('N')
$policyRegistered = $false
try {
  $serviceScript = Join-Path $repo 'tools\start-n8n-service.ps1'
  $legacyAction = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $serviceScript + '"') `
    -WorkingDirectory $repo
  $policySettings = New-ScheduledTaskSettingsSet `
    -RestartCount 7 `
    -RestartInterval (New-TimeSpan -Minutes 3) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $policyTaskName -Action $legacyAction -Principal $principal -Settings $policySettings | Out-Null
  $policyRegistered = $true
  $policyInstallRoot = Join-Path $testRoot 'policy-install'
  $policyResult = & (Join-Path $repo 'tools\install-n8n-no-console.ps1') `
    -ProjectRoot $repo `
    -InstallRoot $policyInstallRoot `
    -TaskName $policyTaskName | ConvertFrom-Json
  $policyReadback = Get-ScheduledTask -TaskName $policyTaskName
  Assert-Equal $policyReadback.Settings.DisallowStartIfOnBatteries $false 'Task allows battery start'
  Assert-Equal $policyReadback.Settings.StopIfGoingOnBatteries $false 'Task continues on battery'
  Assert-Equal $policyReadback.Settings.RestartCount 7 'Restart count preserved'
  Assert-Equal $policyReadback.Settings.RestartInterval 'PT3M' 'Restart interval preserved'
  Assert-Equal $policyReadback.Settings.ExecutionTimeLimit 'PT0S' 'Execution limit preserved'
  Assert-Equal $policyResult.ContinueOnBattery $true 'Installer power policy result'
} finally {
  if ($policyRegistered) { Unregister-ScheduledTask -TaskName $policyTaskName -Confirm:$false }
}
[pscustomobject]@{ Result='passed'; NoConsole=$true; NativeGrandchildNoConsole=$true; ExitCodes=@(0,37,64); UnattachedOutput='passed'; SchedulerStopTree='passed'; ContinueOnBattery='passed'; TestRoot=$testRoot } | ConvertTo-Json
