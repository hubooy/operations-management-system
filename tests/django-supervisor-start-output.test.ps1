$ErrorActionPreference='Stop'
$fixtureRoot=Join-Path ([IO.Path]::GetTempPath()) ('supervisor-output-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot|Out-Null
$source=Join-Path $PSScriptRoot '..\tools\django-runtime-supervisor.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$null,[ref]$null)
$definition=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-SupervisorStartOperator'},$true)
Invoke-Expression $definition.Extent.Text
$SupervisorMonitorDirectory=$fixtureRoot;$InstalledAppRoot=$fixtureRoot
function ConvertTo-ProcessArgument([string]$Value){'"'+$Value.Replace('"','\"')+'"'}
function Get-BoundedNativeDiagnostic($Output){@{OutputSha256='a'*64}}
function Read-SupervisorState {throw 'fixture direct parent must have already exited'}
$python='D:\teruisi-runtime\django-sales\venv\Scripts\python.exe'
$fixture=Join-Path $fixtureRoot 'start with spaces.py'
[IO.File]::WriteAllText($fixture,@'
import subprocess, sys, pathlib
root=pathlib.Path(__file__).parent
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)'],stdout=sys.stdout,stderr=sys.stderr,creationflags=subprocess.CREATE_NO_WINDOW)
(root/'child.pid').write_text(str(p.pid))
print('operator complete',flush=True)
sys.exit(23)
'@)
try {
  $timer=[Diagnostics.Stopwatch]::StartNew()
  $result=Invoke-SupervisorStartOperator $python @($fixture)
  $timer.Stop()
  if($result.success -or $result.exitCode -ne 23){throw 'direct parent exit code lost'}
  if($timer.Elapsed.TotalSeconds -gt 4){throw 'waited for descendant output EOF'}
  $childPid=[int][IO.File]::ReadAllText((Join-Path $fixtureRoot 'child.pid'))
  $child=Get-Process -Id $childPid -ErrorAction SilentlyContinue
  if(-not $child){throw 'healthy descendant was killed with parent'}
  if(-not $child.WaitForExit(10000)){throw 'fixture descendant did not naturally exit'}
  @{directExit=23;descendantPreserved=$true;noProductionChanges=$true}|ConvertTo-Json -Compress
} finally {
  if(Test-Path -LiteralPath (Join-Path $fixtureRoot 'child.pid')){
    $pending=Get-Process -Id ([int][IO.File]::ReadAllText((Join-Path $fixtureRoot 'child.pid'))) -ErrorAction SilentlyContinue
    if($pending){[void]$pending.WaitForExit(10000)}
  }
  $resolved=[IO.Path]::GetFullPath($fixtureRoot)
  if((Split-Path -Parent $resolved) -ieq ([IO.Path]::GetTempPath()).TrimEnd('\') -and (Split-Path -Leaf $resolved) -match '^supervisor-output-[0-9a-f]{32}$'){
    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
  }
}
