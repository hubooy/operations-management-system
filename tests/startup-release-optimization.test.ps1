param([string]$ChildShell = "powershell.exe")
$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$root = Join-Path ([IO.Path]::GetTempPath()) ("teruisi-prepare-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($root) | Out-Null
$previous = $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY
function Load-Function([string]$Path, [string]$Name) {
  $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$errors)
  if ($errors.Count) { throw "Parse failed: $Path" }
  $node = $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true) | Select-Object -First 1
  if (-not $node) { throw "Missing function $Name" }
  return $node
}
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . (Join-Path $workspace "tools/django-local-service.ps1") -RuntimeRoot $root
  $script:events = [Collections.Generic.List[string]]::new()
  function Write-LauncherEvent {}
  function Assert-DeployedApplication {}
  function Assert-RuntimeAclHardened {}
  function Assert-SalesRetirementWorkerStopped {}
  function Assert-ApplicationProcessesStopped { $script:events.Add("applications-stopped") }
  function Assert-ServiceStackStopped { $script:events.Add("full-stopped") }
  function Assert-PostgresListenerOwnership { if ($script:foreignPg) { throw "foreign PostgreSQL" } }
  function Test-PostgresReady { return (-not $script:pgNotReady) }
  $MaintenanceId = "a" * 32
  $KeepPostgres = $true
  Begin-SystemMaintenance
  Assert-ApplicationDeploymentStopped "DeployApp"
  if (($script:events -join ',') -ne 'applications-stopped') { throw "Application maintenance required a stopped database" }
  $script:foreignPg = $true
  try { End-SystemMaintenance; throw "foreign PostgreSQL accepted" } catch { if ($_.Exception.Message -ne "foreign PostgreSQL") { throw } }
  if (-not (Read-SystemMaintenance)) { throw "Failed exit removed maintenance" }
  $script:foreignPg = $false
  $script:pgNotReady = $true
  try { End-SystemMaintenance; throw "unready PostgreSQL accepted" } catch { if ($_.Exception.Message -notmatch 'remain ready') { throw } }
  $script:pgNotReady = $false
  $KeepPostgres = $false
  try { Begin-SystemMaintenance; throw "scope change accepted" } catch { if ($_.Exception.Message -notmatch 'scope changed') { throw } }
  End-SystemMaintenance
  $script:events.Clear()
  Begin-SystemMaintenance
  Assert-ApplicationDeploymentStopped "DeployApp"
  if (($script:events -join ',') -ne 'full-stopped') { throw "Full maintenance no longer requires PostgreSQL stopped" }
  End-SystemMaintenance
  try { Test-MaintenanceKeepsPostgres ([pscustomobject]@{keepPostgres="false"}); throw "malformed scope accepted" } catch { if ($_.Exception.Message -notmatch 'Invalid maintenance') { throw } }

  # A small independently hashed app fixture exercises prepared artifact admission.
  $id = 'b' * 32
  $stage = Join-Path $root ("app.deploy-" + $id)
  [IO.Directory]::CreateDirectory((Join-Path $stage 'tools')) | Out-Null
  [IO.File]::WriteAllText((Join-Path $stage 'tools/django-local-service.ps1'), '# fixture')
  [IO.File]::WriteAllText((Join-Path $stage 'payload.txt'), 'candidate')
  function Assert-WranglerRuntimeCli {}
  function Get-InstalledApplicationBinding { return $script:predecessor }
  $script:predecessor = 'c' * 64
  $evidence = Get-ApplicationTreeFingerprintEvidence $stage
  Write-AtomicJson (Join-Path $stage 'deployment.json') ([ordered]@{
    version=2; deployedAt=[DateTimeOffset]::UtcNow.ToString('o'); sourceRoot=$workspace
    fingerprintAlgorithm=$ApplicationFingerprintAlgorithm; fileCount=$evidence.FileCount; appFingerprint=$evidence.Fingerprint
  })
  $receiptPath = Join-Path $root ("app.prepare-" + $id + '.json')
  Write-AtomicJson $receiptPath ([ordered]@{
    version='teruisi-django-prepared-app-v1'; id=$id; runtimeRoot=Get-CanonicalPath $root
    predecessorManifestSha256=$script:predecessor; candidateManifestSha256=Get-FileSha256 (Join-Path $stage 'deployment.json')
  })
  $approved = Get-FileSha256 $receiptPath
  if ((Get-PreparedApplication $id $approved) -ine $stage) { throw 'Prepared artifact was not admitted' }
  $script:predecessor = 'd' * 64
  try { Get-PreparedApplication $id $approved; throw 'stale predecessor accepted' } catch { if ($_.Exception.Message -notmatch 'predecessor changed') { throw } }
  $script:predecessor = 'c' * 64
  [IO.File]::WriteAllText((Join-Path $stage 'payload.txt'), 'tampered')
  try { Get-PreparedApplication $id $approved; throw 'tampered candidate accepted' } catch { if ($_.Exception.Message -notmatch '部署清单不一致') { throw } }
  [IO.File]::WriteAllText((Join-Path $stage 'payload.txt'), 'candidate')
  [IO.File]::AppendAllText($receiptPath, ' ')
  try { Get-PreparedApplication $id $approved; throw 'receipt tampering accepted' } catch { if ($_.Exception.Message -notmatch 'SHA-256 changed') { throw } }
  try { Get-PreparedApplication '../outside' $approved; throw 'traversal accepted' } catch { if ($_.Exception.Message -notmatch 'exact id') { throw } }

  $readers = @(
    @('django-local-service.ps1','Start-DjangoReader'), @('django-local-service.ps1','Start-DjangoFinanceReader'),
    @('django-netshop-service.ps1','Start-NetshopReader'), @('django-market-service.ps1','Start-MarketReader'),
    @('django-products-service.ps1','Start-ProductsReader'), @('django-inventory-service.ps1','Start-InventoryReader'),
    @('django-workflow-service.ps1','Start-WorkflowReader'), @('django-customer-service.ps1','Start-CustomerServiceReader'),
    @('django-access-control.ps1','Start-AccessControlReader'), @('django-ai.ps1','Start-AiReader'),
    @('django-erp-reference.ps1','Start-ErpReferenceReader')
  )
  foreach ($item in $readers) {
    & {
      $node = Load-Function (Join-Path $workspace ('tools/' + $item[0])) $item[1]
      . ([scriptblock]::Create($node.Extent.Text))
      $call = @{}
      foreach ($parameter in $node.Parameters) {
        $name = $parameter.Name.VariablePath.UserPath
        if ($name -ne 'DeferReady') { $call[$name] = [pscustomobject]@{ ReaderPassword='fixture'; authorityEpoch='epoch'; cutoverId='cutover' } }
      }
      $script:owned = $false; $script:foreignPort = $false; $script:failReady = $false
      $Waitress = Join-Path $stage 'payload.txt'
      function Get-ConfigFingerprint { return 'fingerprint' }
      function Get-Sha256Text { return 'fingerprint' }
      function Get-FileHash { return @{Hash='fixture'} }
      function Resolve-OwnedProcess { return $script:owned }
      function Get-PortListeners { if ($script:foreignPort) { return 42 } }
      function Remove-OldServiceLogs {}
      function Database-Url { return 'fixture' }
      function Invoke-WithDjangoEnvironment { & $args[-1] }
      function Invoke-WithAiEnvironment { & $args[-1] }
      function Invoke-WithCustomerServiceEnvironment { & $args[-1] }
      function Invoke-WithAccessControlEnvironment { & $args[-1] }
      function Invoke-WithErpEnvironment { & $args[-1] }
      function Start-ManagedProcess { $script:events.Add('launch') }
      function Wait-DjangoReady { $script:events.Add('ready'); if ($script:failReady) { throw 'readiness failed' } }
      function Stop-OwnedProcess { $script:events.Add('stop') }
      $script:events.Clear()
      $started = & $item[1] @call -DeferReady
      if ($started -ne $true -or ($script:events -join ',') -ne 'launch') { throw "Deferred reader failed: $($item[1]) $script:events" }
      $script:events.Clear()
      $started = & $item[1] @call
      if ($started -ne $true -or ($script:events -join ',') -ne 'launch,ready') { throw "Standalone reader changed: $($item[1])" }
      $script:events.Clear(); $script:owned = $true
      $started = & $item[1] @call -DeferReady
      if ($started -ne $false -or ($script:events -join ',') -ne 'ready') { throw "Existing reader was relaunched: $($item[1])" }
      $script:events.Clear(); $script:owned = $false; $script:foreignPort = $true
      try { & $item[1] @call -DeferReady; throw 'foreign port accepted' } catch { if ($_.Exception.Message -notmatch '端口') { throw } }
      if ($script:events.Count) { throw 'Foreign process was modified' }
      $script:foreignPort = $false; $script:failReady = $true
      try { & $item[1] @call; throw 'bad readiness accepted' } catch { if ($_.Exception.Message -ne 'readiness failed') { throw } }
      if (($script:events -join ',') -ne 'launch,ready,stop') { throw 'Failed new reader was not cleaned up' }
    }
  }
  & {
    $node = Load-Function (Join-Path $workspace 'tools/django-market-service.ps1') 'Start-MarketStack'
    . ([scriptblock]::Create($node.Extent.Text))
    $MarketStartupPath = Join-Path $root 'absent-startup.json'
    $MarketReaderPidPath = 'fixture-reader'; $MarketWriterPidPath = 'fixture-writer'
    function Assert-MarketRuntimeEntry {}
    function Read-Secrets { return @{} }
    function Read-MarketCredentials { return @{} }
    function Get-MarketWriteAuthority { return @{status='postgres'} }
    function Start-MarketReader {
      param($RuntimeSecrets, $MarketSecrets, [switch]$DeferReady)
      if (-not $DeferReady) { throw 'Stack failed to request overlapping startup' }
      $script:events.Add('reader-launch'); return (-not $script:existingReader)
    }
    function Start-MarketWriter { $script:events.Add('writer-launch'); if ($script:writerFailure) { throw 'writer failed' }; return $true }
    function Wait-DjangoReady { $script:events.Add('readiness'); throw 'reader failed' }
    function Stop-OwnedProcess($Name) { $script:events.Add($Name) }
    foreach ($existing in @($false, $true)) {
      $script:events.Clear(); $script:existingReader=$existing; $script:writerFailure=$false
      try { Start-MarketStack; throw 'readiness failure accepted' } catch { if ($_.Exception.Message -ne 'reader failed') { throw } }
      $expected = 'reader-launch,writer-launch,readiness,django-market-writer'
      if (-not $existing) { $expected += ',django-market-reader' }
      if (($script:events -join ',') -ne $expected) { throw 'Stack rollback changed an existing process or leaked a new one' }
    }
    $script:events.Clear(); $script:existingReader=$false; $script:writerFailure=$true
    try { Start-MarketStack; throw 'writer failure accepted' } catch { if ($_.Exception.Message -ne 'writer failed') { throw } }
    if (($script:events -join ',') -ne 'reader-launch,writer-launch,django-market-reader') { throw 'Writer failure leaked the deferred reader' }
  }
  Write-Output 'PASS: application maintenance, prepared artifact fencing, all 11 deferred readers and stack rollback'
} finally {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = $previous
  $canonical = [IO.Path]::GetFullPath($root)
  $prefix = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) 'teruisi-prepare-'))
  if (-not $canonical.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup' }
  Remove-Item -LiteralPath $canonical -Recurse -Force
}
