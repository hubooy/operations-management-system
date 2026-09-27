$ErrorActionPreference='Stop'
$testDirectory=Join-Path ([IO.Path]::GetTempPath()) ('watchdog-window-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory|Out-Null
try{
  $exe=Join-Path $testDirectory 'Watchdog.NoConsole.exe'
  $source=Join-Path $PSScriptRoot '..\tools\watchdog-launcher\NoConsoleLauncher.cs'
  & "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /optimize+ "/out:$exe" $source
  if($LASTEXITCODE -ne 0){throw 'compile failed'}
  $bytes=[IO.File]::ReadAllBytes($exe);$pe=[BitConverter]::ToInt32($bytes,60)
  if([BitConverter]::ToUInt16($bytes,$pe+24+68) -ne 2){throw 'launcher must use Windows GUI subsystem'}
  $fixture=Join-Path $testDirectory 'fixture with spaces.ps1'
  [IO.File]::WriteAllText($fixture,@'
param([string]$Action,[switch]$Execute)
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
if($Action -cne 'Check' -or -not $Execute){exit 81}
if([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero){exit 82}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'console-proof.txt'),'no-console')
[Console]::Out.Write(('o'*100000));[Console]::Error.Write(('e'*100000))
exit 17
'@)
  $shell=(Get-Process -Id $PID).Path
  $p=Start-Process -FilePath $exe -ArgumentList @(('"'+$shell+'"'),('"'+$fixture+'"')) -WindowStyle Hidden -PassThru
  if(-not $p.WaitForExit(15000)){throw 'launcher deadlocked on redirected output'}
  if($p.ExitCode -ne 17){throw "child exit code not propagated: $($p.ExitCode)"}
  if([IO.File]::ReadAllText((Join-Path $testDirectory 'console-proof.txt')) -cne 'no-console'){throw 'console proof missing'}
  $invalid=Start-Process -FilePath $exe -WindowStyle Hidden -ArgumentList 'relative.ps1' -PassThru
  $invalid.WaitForExit();if($invalid.ExitCode -ne 64){throw 'invalid arguments not rejected'}
  @{passed=5;subsystem='GUI';childConsole='none';exitCodePropagated=$true;productionTouched=$false}|ConvertTo-Json -Compress
}finally{
  if($testDirectory.StartsWith([IO.Path]::GetTempPath()) -and (Split-Path $testDirectory -Leaf) -match '^watchdog-window-test-[0-9a-f]{32}$'){Remove-Item -LiteralPath $testDirectory -Recurse -Force}
}
