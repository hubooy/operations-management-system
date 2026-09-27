[CmdletBinding()]
param(
  [ValidateSet(
    "InstallD1Authority", "MigratePlan", "MigrateApply", "MigrateVerify",
    "AuthorityStatus", "AuthorityPrepare", "AuthorityAbort", "AuthorityActivate",
    "R2Evidence", "RetirementPlan", "RetirementApply"
  )]
  [string]$Action = "AuthorityStatus",
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales",
  [string]$ApprovedRunId = "",
  [string]$InventoryCutoverId = "",
  [string]$SmokeReceipt = "",
  [string]$R2Evidence = "",
  [string]$ApprovedRetirementPlanId = ""
)

$ErrorActionPreference = "Stop"
$CutoverAction = $Action
$ServiceScript = Join-Path $PSScriptRoot "django-local-service.ps1"
$previousLibraryMode = [Environment]::GetEnvironmentVariable(
  "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", "Process"
)
try {
  $env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY = "1"
  . $ServiceScript -Action Status -RuntimeRoot $RuntimeRoot
} finally {
  [Environment]::SetEnvironmentVariable(
    "TERUISI_DJANGO_SERVICE_LIBRARY_ONLY", $previousLibraryMode, "Process"
  )
}
$Action = $CutoverAction
$InventoryAuditRoot = Join-Path $RuntimeRoot "audits\inventory-cutover"
$InventoryReaderPidPath = Join-Path $RunDirectory "django-inventory-reader.pid.json"
$InventoryWriterPidPath = Join-Path $RunDirectory "django-inventory-writer.pid.json"

function Assert-InstalledInventoryOperator {
  if ((Get-CanonicalPath $ExecutionRoot) -ine (Get-CanonicalPath $InstalledAppRoot)) {
    throw "Inventory cutover must run from the protected runtime app after DeployApp"
  }
  Assert-DeployedApplication
  Assert-RuntimeAclHardened
  Get-ServiceConfig | Out-Null
  Assert-PostgresListenerOwnership | Out-Null
  if (-not (Test-PostgresReady)) { throw "PostgreSQL is not ready for inventory cutover" }
}

function Resolve-LiveInventoryD1 {
  $config = Get-ServiceConfig
  return Resolve-ErpSourceD1 ([string]$config.erpSourceD1)
}

function Assert-InventoryStackStopped([string]$Operation) {
  if (Resolve-OwnedProcess "django-inventory-reader" $InventoryReaderPidPath $Waitress) {
    throw "$Operation 前必须通过库存控制器停止 inventory reader"
  }
  if (Resolve-OwnedProcess "django-inventory-writer" $InventoryWriterPidPath $Waitress) {
    throw "$Operation 前必须通过库存控制器停止 inventory writer"
  }
  if (@(Get-PortListeners 8051).Count -gt 0 -or @(Get-PortListeners 8052).Count -gt 0) {
    throw "$Operation 前端口 8051/8052 必须没有监听者"
  }
}

function Assert-InventoryWorkerStopped([string]$Operation) {
  if (@(Get-PortListeners 3000).Count -gt 0) {
    throw "$Operation 前必须通过统一控制器停止 Worker"
  }
}

function Resolve-InventoryEvidencePath([string]$Value, [string]$Label) {
  if ([string]::IsNullOrWhiteSpace($Value) -or -not (Test-FullyQualifiedPath $Value)) {
    throw "$Label 必须是 audit 目录内的绝对文件路径"
  }
  $canonical = Get-CanonicalPath $Value
  [void](Assert-RuntimeChildPath $canonical)
  $auditRoot = Get-CanonicalPath $InventoryAuditRoot
  if (-not $canonical.StartsWith(
      $auditRoot + [IO.Path]::DirectorySeparatorChar,
      [StringComparison]::OrdinalIgnoreCase
    ) -or -not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
    throw "$Label 不在受保护库存切换 audit 目录或文件不存在"
  }
  return $canonical
}

function Invoke-InventoryManagementCommand([string[]]$Arguments, [string]$Operation) {
  $secrets = Read-Secrets
  $ownerUrl = Database-Url `
    "teruisi_sales_owner" $secrets.OwnerPassword "teruisi_inventory_cutover" `
    $WriterStatementTimeoutMs
  $manage = Join-Path $BackendRoot "manage.py"
  $commandArguments = @($manage) + @($Arguments)
  $diagnosticLogPath = Join-Path $LogDirectory "inventory-cutover.$RunId.log"
  if ($Operation -cnotmatch "^[a-z0-9_-]{1,64}$" -or
      @($commandArguments | Where-Object { [string]::IsNullOrWhiteSpace([string]$_) }).Count -gt 0) {
    throw "Inventory management command contract is invalid"
  }
  try {
    return Invoke-WithDjangoEnvironment $secrets $ownerUrl "migration_writer" (
      $false
    ) 67108864 "" "" {
      $nativeRun = Invoke-BoundedNativeProcess $Python $commandArguments $BackendRoot
      Write-NativeDiagnosticLog $diagnosticLogPath $Operation $nativeRun
      return ConvertFrom-UniqueNativeJson $nativeRun $Operation
    }
  } finally {
    $ownerUrl = $null
    $secrets = $null
  }
}

function Invoke-InstallInventoryD1Authority {
  Assert-InstalledInventoryOperator
  Assert-InventoryStackStopped "安装 D1 库存写入权威门禁"
  Assert-InventoryWorkerStopped "安装 D1 库存写入权威门禁"
  $source = Resolve-LiveInventoryD1
  $directory = Join-Path $InventoryAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $backup = Join-Path $directory "d1-before-inventory-authority.sqlite"
  $receipt = Join-Path $directory "authority-install-receipt.json"
  $tool = Join-Path $InstalledAppRoot "tools\inventory-d1-authority-install.py"
  $sql = Join-Path $InstalledAppRoot "drizzle\0101_inventory_write_authority.sql"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    $tool, "--source", $source, "--sql", $sql,
    "--backup", $backup, "--receipt", $receipt
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog `
    (Join-Path $LogDirectory "inventory-authority-install.$RunId.log") `
    "inventory_authority_install" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "install D1 inventory write guard"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 6)
}

function Invoke-InventoryMigration([string]$Mode) {
  Assert-InstalledInventoryOperator
  Assert-InventoryStackStopped "执行库存正式迁移"
  Assert-InventoryWorkerStopped "执行库存正式迁移"
  $source = Resolve-LiveInventoryD1
  $arguments = @("migrate_inventory_from_d1", "--source", $source, "--mode", $Mode)
  if ($Mode -eq "apply") {
    if ($ApprovedRunId -cnotmatch "^inventory-plan-[0-9a-f]{32}$") {
      throw "Inventory apply requires an exact plan run id"
    }
    $arguments += @("--approve-run-id", $ApprovedRunId)
  } elseif ($Mode -eq "verify") {
    if ($ApprovedRunId -cnotmatch "^inventory-apply-[0-9a-f]{32}$") {
      throw "Inventory verify requires an exact apply run id"
    }
    $arguments += @("--verify-run-id", $ApprovedRunId)
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRunId)) {
    throw "Inventory plan does not accept an approved run id"
  }
  $payload = Invoke-InventoryManagementCommand $arguments "inventory_migration_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-InventoryAuthority([string]$Mode) {
  Assert-InstalledInventoryOperator
  Assert-InventoryStackStopped "切换库存写入权威"
  if ($Mode -ne "status") { Assert-InventoryWorkerStopped "切换库存写入权威" }
  $source = Resolve-LiveInventoryD1
  $arguments = @("inventory_write_authority", "--source", $source)
  if ($Mode -ne "status") {
    if ($ApprovedRunId -cnotmatch "^inventory-apply-[0-9a-f]{32}$") {
      throw "Inventory authority transition requires an exact apply run id"
    }
    if ($InventoryCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
      throw "Inventory authority transition requires a valid cutover id"
    }
    $flag = switch ($Mode) {
      "prepare" { "--prepare" }
      "abort" { "--abort-pending" }
      "activate" { "--activate" }
      default { throw "Unknown inventory authority action" }
    }
    $arguments += @(
      $flag, "--approved-run-id", $ApprovedRunId,
      "--cutover-id", $InventoryCutoverId
    )
  } elseif (
    -not [string]::IsNullOrWhiteSpace($ApprovedRunId) -or
    -not [string]::IsNullOrWhiteSpace($InventoryCutoverId)
  ) {
    throw "Inventory authority status does not accept transition parameters"
  }
  $payload = Invoke-InventoryManagementCommand $arguments "inventory_authority_$Mode"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Invoke-InventoryR2Evidence {
  Assert-InstalledInventoryOperator
  Assert-InventoryWorkerStopped "生成库存 R2 退役证据"
  $source = Resolve-LiveInventoryD1
  $d1ObjectRoot = Split-Path -Parent $source
  $d1Root = Split-Path -Parent $d1ObjectRoot
  if ((Split-Path -Leaf $d1Root) -cne "d1") {
    throw "权威 D1 不在固定 Wrangler persist/v3/d1 根内"
  }
  $v3Root = Split-Path -Parent $d1Root
  $r2Root = Join-Path $v3Root "r2\miniflare-R2BucketObject"
  if (-not (Test-Path -LiteralPath $r2Root -PathType Container)) {
    throw "固定 Wrangler R2 metadata 根不存在"
  }
  $directory = Join-Path $InventoryAuditRoot $RunId
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
  $output = Join-Path $directory "inventory-r2-retirement-evidence.json"
  $tool = Join-Path $InstalledAppRoot "tools\inventory-r2-retirement-evidence.py"
  $nativeRun = Invoke-BoundedNativeProcess $Python @(
    $tool, "--r2-root", $r2Root, "--output", $output
  ) $InstalledAppRoot
  Write-NativeDiagnosticLog `
    (Join-Path $LogDirectory "inventory-r2-evidence.$RunId.log") `
    "inventory_r2_evidence" $nativeRun
  $payload = ConvertFrom-UniqueNativeJson $nativeRun "prove inventory R2 namespace empty"
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 6)
}

function Invoke-InventoryRetirement([bool]$Apply) {
  Assert-InstalledInventoryOperator
  Assert-InventoryWorkerStopped "执行库存 D1/R2 终态退役"
  if ($ApprovedRunId -cnotmatch "^inventory-apply-[0-9a-f]{32}$") {
    throw "Inventory retirement requires an exact apply run id"
  }
  if ($InventoryCutoverId -cnotmatch "^[A-Za-z0-9._:-]{8,128}$") {
    throw "Inventory retirement requires a valid cutover id"
  }
  $smoke = Resolve-InventoryEvidencePath $SmokeReceipt "系统测试 receipt"
  $r2 = Resolve-InventoryEvidencePath $R2Evidence "库存 R2 退役证据"
  $source = Resolve-LiveInventoryD1
  $arguments = @(
    "retire_inventory_d1", "--source", $source,
    "--cutover-id", $InventoryCutoverId,
    "--approved-run-id", $ApprovedRunId,
    "--smoke-receipt", $smoke,
    "--r2-evidence", $r2
  )
  if ($Apply) {
    if ($ApprovedRetirementPlanId -cnotmatch "^[0-9a-f]{64}$") {
      throw "Inventory retirement apply requires an exact plan id"
    }
    $directory = Join-Path $InventoryAuditRoot $RunId
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $audit = Join-Path $directory "inventory-retirement-audit.json"
    $arguments += @(
      "--apply", "--approved-plan-id", $ApprovedRetirementPlanId,
      "--audit-output", $audit
    )
  } elseif (-not [string]::IsNullOrWhiteSpace($ApprovedRetirementPlanId)) {
    throw "Inventory retirement plan does not accept an approved plan id"
  }
  $payload = Invoke-InventoryManagementCommand $arguments (
    $(if ($Apply) { "inventory_retirement_apply" } else { "inventory_retirement_plan" })
  )
  Write-Output ($payload | ConvertTo-Json -Compress -Depth 10)
}

Invoke-WithServiceMutex {
  switch ($Action) {
    "InstallD1Authority" { Invoke-InstallInventoryD1Authority }
    "MigratePlan" { Invoke-InventoryMigration "plan" }
    "MigrateApply" { Invoke-InventoryMigration "apply" }
    "MigrateVerify" { Invoke-InventoryMigration "verify" }
    "AuthorityStatus" { Invoke-InventoryAuthority "status" }
    "AuthorityPrepare" { Invoke-InventoryAuthority "prepare" }
    "AuthorityAbort" { Invoke-InventoryAuthority "abort" }
    "AuthorityActivate" { Invoke-InventoryAuthority "activate" }
    "R2Evidence" { Invoke-InventoryR2Evidence }
    "RetirementPlan" { Invoke-InventoryRetirement $false }
    "RetirementApply" { Invoke-InventoryRetirement $true }
  }
}
