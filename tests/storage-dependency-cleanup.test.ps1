$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Tool = Join-Path $PSScriptRoot '../tools/storage-dependency-cleanup.ps1'
$Tokens = $null
$Errors = $null
$Ast = [Management.Automation.Language.Parser]::ParseFile($Tool, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw 'Operator parse failed' }
foreach ($Name in @('Assert-ExactTarget', 'Assert-NoProcessReference', 'Write-DurableAudit')) {
  $Function = $Ast.Find({ param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name }, $true)
  if ($null -eq $Function) { throw 'Expected operator function missing' }
  . ([scriptblock]::Create($Function.Extent.Text))
}
function Expect-Failure([scriptblock]$Action) {
  $Failed = $false
  try { & $Action } catch { $Failed = $true }
  if (-not $Failed) { throw 'Unsafe operation unexpectedly accepted' }
}
function Get-CimInstance { param($ClassName, $OperationTimeoutSec, $ErrorAction) return $script:FakeProcesses }
$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$TestRoot = Join-Path $TempRoot ('teruisi-storage-operator-test-' + [guid]::NewGuid().ToString('N'))
$ReleasesRoot = Join-Path $TestRoot 'releases'
$ReleaseId = '20260829T194525Z-9a33f178481e6c8e'
$ReleaseRoot = Join-Path $ReleasesRoot $ReleaseId
$Target = Join-Path $ReleaseRoot 'node_modules'
$Audit = Join-Path $TestRoot 'audit.jsonl'
try {
  [void](New-Item -ItemType Directory -Path $Target)
  if ((Assert-ExactTarget $Target $ReleaseId) -ne $Target) { throw 'Exact target rejected' }
  Expect-Failure { Assert-ExactTarget (Join-Path $TestRoot 'outside') $ReleaseId }
  Expect-Failure { Assert-ExactTarget $Target '../outside' }
  $script:FakeProcesses = @([pscustomobject]@{Name='node.exe'; CommandLine='node unrelated.js'; ExecutablePath='C:\tools\node.exe'})
  Assert-NoProcessReference $ReleaseRoot
  $script:FakeProcesses = @([pscustomobject]@{Name='node.exe'; CommandLine=('node "' + $ReleaseRoot.Replace('\','/').ToUpperInvariant() + '/helper.mjs"'); ExecutablePath='C:\tools\node.exe'})
  Expect-Failure { Assert-NoProcessReference $ReleaseRoot }
  $script:FakeProcesses = @([pscustomobject]@{Name='workerd.exe'; CommandLine=$null; ExecutablePath=$null})
  Expect-Failure { Assert-NoProcessReference $ReleaseRoot }
  Write-DurableAudit @{event='delete-reserved'; target=$Target}
  Write-DurableAudit @{event='synthetic-verification-only'}
  $Lines = @(Get-Content -LiteralPath $Audit)
  if ($Lines.Count -ne 2 -or ($Lines[0] | ConvertFrom-Json).event -ne 'delete-reserved') { throw 'Durable audit failed' }
  Write-Output 'Operator target, process identity, alternate separators and durable audit checks passed.'
} finally {
  $Resolved = [IO.Path]::GetFullPath($TestRoot)
  if ([IO.Path]::GetDirectoryName($Resolved) -ne $TempRoot -or [IO.Path]::GetFileName($Resolved) -notlike 'teruisi-storage-operator-test-*') { throw 'Test cleanup escaped temporary root' }
  if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
}
