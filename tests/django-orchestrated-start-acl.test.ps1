$ErrorActionPreference = "Stop"
$root = Join-Path ([IO.Path]::GetTempPath()) ("teruisi-lifecycle-acl-" + [Guid]::NewGuid().ToString("N"))
$app = Join-Path $root "app"
$manifest = Join-Path $app "deployment.json"
$contextName = "TERUISI_DJANGO_ORCHESTRATED_LIFECYCLE_ACL_CONTEXT"
$previousLibraryOnly = [Environment]::GetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process")
$previousContextVariable = Get-Variable -Scope Global -Name $contextName -ErrorAction SilentlyContinue
$hadPreviousContext = $null -ne $previousContextVariable
$previousContext = if ($hadPreviousContext) { $previousContextVariable.Value } else { $null }

try {
  [IO.Directory]::CreateDirectory($app) | Out-Null
  [IO.File]::WriteAllText($manifest, '{"version":2}', [Text.UTF8Encoding]::new($false))
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . (Join-Path $PSScriptRoot "..\tools\django-local-service.ps1") -RuntimeRoot $root

  $script:rootAclChecks = 0
  function Assert-RuntimeRootAclHardened { $script:rootAclChecks += 1 }
  function Write-LauncherEvent {}

  $token = New-OrchestratedLifecycleAclToken
  if ($token -cnotmatch "^[0-9a-f]{64}$") { throw "token format is invalid" }
  Set-OrchestratedLifecycleAclContext $token
  if (-not (Test-OrchestratedLifecycleAclContext $token)) { throw "fresh exact context was rejected" }
  if ($script:rootAclChecks -ne 2) { throw "exact context did not retain root ACL checks" }
  if (Test-OrchestratedLifecycleAclContext ("0" * 64)) { throw "wrong token was accepted" }

  [IO.File]::WriteAllText($manifest, '{"version":3}', [Text.UTF8Encoding]::new($false))
  if (Test-OrchestratedLifecycleAclContext $token) { throw "changed deployment manifest was accepted" }
  [IO.File]::WriteAllText($manifest, '{"version":2}', [Text.UTF8Encoding]::new($false))

  Set-OrchestratedLifecycleAclContext $token
  $payload = (Get-Variable -Scope Global -Name $contextName).Value
  $payload.processId = [int]$PID + 1
  Set-Variable -Scope Global -Name $contextName -Value $payload -Force
  if (Test-OrchestratedLifecycleAclContext $token) { throw "foreign process context was accepted" }

  Set-OrchestratedLifecycleAclContext $token
  $payload = (Get-Variable -Scope Global -Name $contextName).Value
  $payload.issuedAtUnixMilliseconds = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - 900001
  Set-Variable -Scope Global -Name $contextName -Value $payload -Force
  if (Test-OrchestratedLifecycleAclContext $token) { throw "expired context was accepted" }

  Write-Output "PASS: orchestrated lifecycle ACL context is exact, bounded, and fail-closed"
} finally {
  [Environment]::SetEnvironmentVariable("TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryOnly, "Process")
  if ($hadPreviousContext) {
    Set-Variable -Scope Global -Name $contextName -Value $previousContext -Force
  } else {
    Remove-Variable -Scope Global -Name $contextName -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath $root) {
    $resolved = [IO.Path]::GetFullPath($root)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
      throw "temporary test root escaped the OS temp directory"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
