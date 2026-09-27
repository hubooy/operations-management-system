import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const rehearsalPath = path.join(root, "tools", "sales-local-cutover-rehearsal.ps1");
const retirementPath = path.join(root, "tools", "sales-d1-retirement.ts");
const nativePs5Fixture = path.join(root, "tests", "sales-local-cutover-native-ps5.test.ps1");
const runtimePython = "D:\\teruisi-runtime\\django-sales\\venv\\Scripts\\python.exe";

test("rehearsal PowerShell operator parses and is bound to the protected deployed app", async (t) => {
  if (process.platform !== "win32") {
    t.skip("PowerShell AST validation is Windows-only");
    return;
  }
  const powershell = path.join(process.env.SystemRoot ?? "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  const escapedPath = rehearsalPath.replaceAll("'", "''");
  const command = [
    "$tokens=$null; $errors=$null;",
    `$source=[IO.File]::ReadAllText('${escapedPath}',[Text.Encoding]::UTF8);`,
    "[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors) | Out-Null;",
    "if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}",
  ].join(" ");
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-Command", command,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /-Execute 与 -ConfirmedIsolatedRehearsal/);
  assert.match(script, /\$FixedRuntimeRoot = "D:\\teruisi-runtime\\django-sales"/);
  assert.match(script, /expectedSelf[\s\S]*sales-local-cutover-rehearsal\.ps1/);
  assert.match(script, /Assert-DeployedApplication/);
  assert.match(script, /Assert-WranglerLocalR2RoundTrip \$InstalledAppRoot/);
  assert.match(script, /Assert-RuntimeAclHardened/);
  assert.match(script, /Invoke-WithServiceMutex \{[\s\S]*CleanupFailedRehearsal\.IsPresent[\s\S]*Invoke-ExplicitFailedRehearsalCleanup[\s\S]*Invoke-IsolatedRehearsal/);
});

test("rehearsal native calls are PS5-safe, bounded, and preserve exact exit state", async (t) => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /function ConvertTo-AsciiPythonLauncher/);
  assert.match(script, /Python launcher must be one ASCII line/);
  assert.match(script, /function Invoke-BoundedNativeProcess/);
  assert.match(script, /\$outerErrorActionPreference = \$ErrorActionPreference/);
  assert.match(script, /\$outerLastExitCode = \$global:LASTEXITCODE/);
  assert.match(script, /\$ErrorActionPreference = "Continue"/);
  assert.match(script, /\$global:LASTEXITCODE = \$null/);
  assert.match(script, /\$nativeExitCode = \$global:LASTEXITCODE/);
  assert.match(script, /\$global:LASTEXITCODE = \$outerLastExitCode/);
  assert.match(script, /OutputRecordCount/);
  assert.match(script, /CapturedRecordCount/);
  assert.match(script, /OutputTruncated/);
  assert.match(script, /OutputSha256/);
  assert.match(script, /launchFailed=/);
  assert.match(script, /native executable unavailable/);
  assert.match(script, /Invoke-PythonJsonCode \$dropScript/);
  assert.match(script, /Invoke-PythonJsonCode \$databaseProbe/);
  assert.match(script, /Invoke-PythonJsonCode \$databaseCreate/);
  assert.match(script, /Invoke-BoundedNativeProcess \$Node @\("--version"\)/);
  assert.match(script, /Invoke-BoundedNativeProcess \$pgRestore/);
  assert.doesNotMatch(script, /@\(& \$(?:Node|Python|pgRestore)/);
  assert.doesNotMatch(script, /\n\s*& \$pgRestore/);
  assert.doesNotMatch(script, /output\[-1\]/);
  for (const rawCode of ["dropScript", "databaseProbe", "databaseCreate", "d1EvidenceCode", "code"]) {
    assert.doesNotMatch(
      script,
      new RegExp(`Invoke-JsonProcess \\$Python @\\(\"-c\", \\$${rawCode}\\)`),
    );
  }

  if (process.platform !== "win32") {
    t.skip("Windows PowerShell 5 fixture is Windows-only");
    return;
  }
  const powershell = path.join(
    process.env.SystemRoot ?? "C:\\Windows",
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
  );
  if (!existsSync(powershell) || !existsSync(runtimePython)) {
    t.skip("Windows PowerShell 5 or the controlled Python runtime is unavailable");
    return;
  }
  const fixtureSecret = `rehearsal-ps5-${createHash("sha256")
    .update(`${process.pid}-${Date.now()}`)
    .digest("hex")}`;
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", nativePs5Fixture,
    "-Mode", "rehearsal",
    "-ToolScript", rehearsalPath,
    "-Python", runtimePython,
  ], {
    encoding: "utf8",
    windowsHide: true,
    env: { ...process.env, TERUISI_CUTOVER_NATIVE_PS5_FIXTURE_SECRET: fixtureSecret },
  });
  const externalOutput = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  assert.equal(externalOutput.includes(fixtureSecret), false);
  assert.equal(externalOutput.includes("postgresql://owner:"), false);
  const outputDigest = createHash("sha256").update(externalOutput).digest("hex");
  assert.equal(result.status, 0, `PS5 rehearsal fixture failed; outputSha256=${outputDigest}`);
  const payload = JSON.parse(result.stdout.trim()) as Record<string, unknown>;
  assert.equal(payload.status, "completed");
  assert.equal(payload.mode, "rehearsal");
  assert.equal(payload.powershellEdition, "Desktop");
  assert.match(String(payload.powershellVersion), /^5\.1\./);
  assert.equal(payload.successExitCode, 0);
  assert.equal(payload.failureExitCode, 23);
  assert.equal(payload.missingExecutableExitCode, -1);
  assert.equal(payload.diagnosticTruncated, true);
  assert.equal(payload.processStateRestored, true);
});

test("rehearsal retirement blockers accept only the exact all-zero integer object", async (t) => {
  if (process.platform !== "win32") {
    t.skip("PowerShell runtime validation is Windows-only");
    return;
  }
  const powershell = path.join(process.env.SystemRoot ?? "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  const escapedPath = rehearsalPath.replaceAll("'", "''");
  const command = [
    "$tokens=$null; $errors=$null;",
    `$source=[IO.File]::ReadAllText('${escapedPath}',[Text.Encoding]::UTF8);`,
    "$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors);",
    "if($errors.Count){exit 1}",
    "$names=@('Assert-ExactPropertySet','Assert-RetirementPlanBlockersClear');",
    "$definitions=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $node.Name},$true) | Sort-Object {$_.Extent.StartOffset});",
    "if($definitions.Count -ne 2){exit 2}",
    "$definitions | ForEach-Object {Invoke-Expression $_.Extent.Text};",
    "$good='{" + "\"processingBatches\":0,\"activeUploads\":0,\"invalidUploadExpiries\":0,\"uploadChunks\":0,\"processingFingerprints\":0,\"processingScopeHeads\":0,\"processingAttempts\":0" + "}' | ConvertFrom-Json;",
    "Assert-RetirementPlanBlockersClear $good;",
    "$bad=$good.PSObject.Copy(); $bad.uploadChunks=1; try {Assert-RetirementPlanBlockersClear $bad; exit 3} catch {}",
    "$missing='{" + "\"processingBatches\":0" + "}' | ConvertFrom-Json; try {Assert-RetirementPlanBlockersClear $missing; exit 4} catch {}",
    "$stringZero='{" + "\"processingBatches\":\"0\",\"activeUploads\":0,\"invalidUploadExpiries\":0,\"uploadChunks\":0,\"processingFingerprints\":0,\"processingScopeHeads\":0,\"processingAttempts\":0" + "}' | ConvertFrom-Json; try {Assert-RetirementPlanBlockersClear $stringZero; exit 5} catch {}",
    "exit 0",
  ].join(" ");
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-Command", command,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("rehearsal result uses a property-bearing object for exact schema validation", async (t) => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(
    script,
    /\$result = \[pscustomobject\]\[ordered\]@\{[\s\S]*?version = "teruisi-sales-cutover-rehearsal-result-v1"/,
  );

  if (process.platform !== "win32") {
    t.skip("PowerShell runtime validation is Windows-only");
    return;
  }
  const powershell = path.join(process.env.SystemRoot ?? "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  const escapedPath = rehearsalPath.replaceAll("'", "''");
  const command = [
    "$tokens=$null; $errors=$null;",
    `$source=[IO.File]::ReadAllText('${escapedPath}',[Text.Encoding]::UTF8);`,
    "$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors);",
    "if($errors.Count){exit 1}",
    "$definition=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Assert-ExactPropertySet'},$true));",
    "if($definition.Count -ne 1){exit 2}",
    "Invoke-Expression $definition[0].Extent.Text;",
    "$value=[pscustomobject][ordered]@{status='completed';version='v1';completedAt=''};",
    "Assert-ExactPropertySet $value @('status','version','completedAt') 'result';",
    "$value.completedAt=[DateTimeOffset]::UtcNow.ToString('o');",
    "Assert-ExactPropertySet $value @('status','version','completedAt') 'result';",
    "$dictionary=[ordered]@{status='completed';version='v1';completedAt=''}; try {Assert-ExactPropertySet $dictionary @('status','version','completedAt') 'result'; exit 3} catch {}",
    "exit 0",
  ].join(" ");
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-Command", command,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("rehearsal accepts only a fresh, approved, fully hashed backup under runtime backups", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /\$MaxBackupAgeMinutes = 360/);
  assert.match(script, /ApprovedBackupManifestSha256/);
  assert.match(script, /backup-manifest\.json\.sha256/);
  assert.match(script, /Get-FileSha256 \$manifestPath/);
  assert.match(script, /deploymentManifestSha256[\s\S]*\$DeploymentManifestPath/);
  assert.match(script, /serviceConfigSha256[\s\S]*\$ConfigPath/);
  assert.match(script, /sourceD1\.sha256/);
  assert.match(script, /postgresql\.sha256/);
  assert.match(script, /Assert-R2TreeMatchesManifest/);
  assert.match(script, /Get-Sha256Text \$currentProductionSource/);
  assert.match(script, /Assert-ExactPropertySet \$manifest\.sourceD1\.counts/);
  assert.match(script, /Assert-ExactPropertySet \$manifest\.postgresql\.evidence/);
  assert.match(script, /PostgreSQL 备份在 restore 前发生变化/);
  assert.match(script, /Assert-NoReparsePoints/);
  assert.match(script, /legacy_absent", "pending/);
  assert.match(script, /备份不在固定新鲜度窗口内/);
  for (const field of [
    "productionCutoverId", "deploymentManifestSha256", "serviceConfigSha256",
    "sourcePathSha256", "backupCreatedAt", "sourceD1Sha256",
    "r2ManifestSha256", "postgresqlDumpSha256",
  ]) {
    assert.match(script, new RegExp(`\\b${field}\\b`));
  }
  assert.match(script, /Assert-ExactPropertySet \$result/);
  assert.match(script, /rehearsal-result\.json/);
  assert.match(script, /rehearsal-result\.json\.sha256/);
  assert.match(script, /teruisi-sales-cutover-rehearsal-reference-v1/);
});

test("rehearsal restores only into a new strict database and cleanup can drop only that exact database", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /teruisi_sales_rehearsal_\$RehearsalId/);
  assert.match(script, /\^teruisi_sales_rehearsal_\[0-9a-f\]\{12\}\$/);
  assert.match(script, /SELECT 1 FROM pg_database WHERE datname = %s/);
  assert.match(script, /数据库已存在；拒绝覆盖、复用或删除/);
  assert.match(script, /CREATE DATABASE \{\} OWNER teruisi_sales_owner TEMPLATE template0/);
  assert.match(script, /--dbname=\$databaseName/);
  assert.match(script, /--single-transaction/);
  assert.match(script, /Invoke-DjangoMigrations \$secrets \$databaseName/);
  assert.match(script, /Invoke-RehearsalDatabaseDisposition/);
  assert.match(script, /identity != \("postgres", "postgres"\)/);
  assert.match(script, /sql\.SQL\("DROP DATABASE \{\} WITH \(FORCE\)"\)\.format\(sql\.Identifier\(name\)\)/);
  assert.match(script, /name == "teruisi_sales"/);
  assert.doesNotMatch(script, /Remove-Item[^\n]*rehearsalRoot/i);
  assert.match(script, /databaseRetained/);
});

test("rehearsal fails closed on capacity before payload copy or PostgreSQL restore", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /AvailableFreeSpace/);
  assert.match(script, /\$FixedFreeSpaceReserveBytes = \[int64\]\(8GB\)/);
  assert.match(script, /\$MinimumPostgresWorkingBytes = \[int64\]\(4GB\)/);
  assert.match(script, /sourcePayloadBytes/);
  assert.match(script, /postgresRestoreReserveBytes/);
  assert.match(script, /postgresWorkingBytes/);
  assert.match(script, /fixedFreeSpaceReserveBytes/);
  const gate = script.indexOf("$diskCapacity = Get-RehearsalDiskCapacityEvidence");
  const d1Copy = script.indexOf("Copy-Item -LiteralPath $backup.D1");
  const r2Copy = script.indexOf("Copy-Item -LiteralPath $backup.R2");
  const restore = script.indexOf("$restoreRun = Invoke-BoundedNativeProcess $pgRestore");
  assert.ok(gate > 0 && gate < d1Copy && gate < r2Copy && gate < restore);
});

test("failed rehearsal cleanup is exact, audited, and leaves prune coordination fields fail closed", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /\[switch\]\$CleanupFailedRehearsal/);
  assert.match(script, /\[switch\]\$ConfirmedFailedRehearsalCleanup/);
  assert.match(script, /失败演练清理必须仅显式提供/);
  assert.match(script, /status -cne "failed"/);
  assert.match(script, /rehearsal_database_created/);
  assert.match(script, /payload-cleanup-audit\.json/);
  assert.match(script, /version = "teruisi-sales-rehearsal-payload-cleanup-v1"/);
  assert.match(script, /status = if \(\$completed\) \{ "completed" \} else \{ "failed" \}/);
  assert.match(script, /payloadDisposition" "unresolved"/);
  assert.match(script, /\$payloadDisposition = if \(\$completed\)/);
  assert.match(script, /payloadDisposition" \$payloadDisposition/);
  assert.match(script, /payloadCleanupAuditPath/);
  assert.match(script, /payloadCleanupAuditSha256/);
  assert.match(script, /backupManifestSha256 = \[string\]\$State\.backupManifestSha256/);
  assert.match(script, /Get-RehearsalPayloadTargets/);
  for (const relativePath of [".wrangler", "r2-state", "source-d1.sqlite", "teruisi-sales.dump", "postgresql-restore.dump"]) {
    assert.match(script, new RegExp(relativePath.replaceAll(".", "\\.")));
  }
  assert.match(script, /Assert-NoReparsePoints \$target\.FullPath/);
  assert.match(script, /Remove-Item -LiteralPath \$target\.FullPath/);
  assert.match(script, /Filesystem payloads are independent copies/);
  assert.match(script, /unsafeFilesystemErrors/);
  assert.match(script, /owned_process_stop/);
  assert.match(script, /maintenance_precondition/);
  assert.doesNotMatch(
    script,
    /if \(\$errors\.Count -eq 0\) \{\s*try \{\s*\$payloadResult = Remove-ValidatedRehearsalPayload/s,
  );
  assert.doesNotMatch(script, /Remove-Item[^\n]*\$rehearsalRoot/i);
  assert.match(script, /Invoke-RehearsalPayloadCleanup[\s\S]*\$state \$statePath \$rehearsalRoot/);
});

test("completed rehearsal abandonment is create-only, pre-PNR, and double fenced", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /\[switch\]\$AbandonCompletedRehearsal/);
  assert.match(script, /\[switch\]\$ConfirmedAbandonBeforeForwardRecovery/);
  assert.match(script, /ApprovedRehearsalResultSha256/);
  assert.match(script, /失败演练清理与 completed 演练放弃模式互斥/);
  assert.match(script, /function Invoke-AbandonCompletedRehearsal/);
  assert.match(script, /function Assert-AbandonBeforeForwardRecovery/);
  assert.match(script, /@\(3000, 5791, 8001, 8002\)/);
  assert.match(script, /\.forward-recovery\.json/);
  assert.match(script, /formalStateCount/);
  assert.match(script, /sales-postgresql-authority\.json/);
  assert.match(script, /mode=ro/);
  assert.match(script, /ConvertTo-AsciiPythonLauncher/);
  assert.match(script, /Invoke-PythonJsonCode \$d1EvidenceCode/);
  assert.match(script, /Invoke-PythonJsonCode \$code/);
  assert.match(script, /sales_cutover_abandon_d1_evidence\.py/);
  assert.match(script, /sales_cutover_abandon_postgres_evidence\.py/);
  assert.doesNotMatch(script, /Invoke-JsonProcess \$Python @\("-c", \$d1EvidenceCode\)/);
  assert.doesNotMatch(script, /Invoke-JsonProcess \$Python @\("-c", \$code\) \$canonicalRuntime \(\s*"completed rehearsal abandon PostgreSQL/);
  assert.match(script, /formalSchemaSha256/);
  assert.match(script, /authorityRowsSha256/);
  assert.match(script, /retirementRowsSha256/);
  assert.match(script, /D1 行数已偏离批准 backup/);
  assert.match(script, /PostgreSQL authority 已偏离批准 backup/);
  assert.match(script, /Assert-R2TreeMatchesManifest \$liveR2/);
  assert.match(script, /teruisi-sales-cutover-abandon-archive-v1/);
  assert.match(script, /teruisi-sales-cutover-abandon-preflight-v1/);
  assert.match(script, /teruisi-sales-cutover-abandon-v1/);
  assert.match(script, /teruisi-sales-cutover-abandon-reference-v1/);
  assert.match(script, /approved_for_controlled_prune/);
  assert.match(script, /function Resolve-AbandonIncompleteRecovery/);
  assert.match(script, /function Complete-AbandonIncompleteRecovery/);
  assert.match(script, /audits\\cutover-abandon[\s\S]*recoveries/);
  assert.match(script, /abandon incomplete recovery marker/);
  assert.match(script, /相同 RehearsalId 存在多个 abandon incomplete staging/);
  assert.match(script, /跨 archive\/result 存在多个 incomplete staging/);
  assert.match(script, /abandon final 与 incomplete staging 同时存在/);
  assert.match(script, /Get-AbandonIncompleteTreeEvidence/);
  assert.match(script, /deployment\/config 已偏离批准 backup/);
  assert.match(script, /\[IO\.Directory\]::Move\(\$staging, \$Paths\.ArchiveRoot\)/);
  assert.match(script, /\[IO\.Directory\]::Move\(\$staging, \$Paths\.ResultRoot\)/);
  assert.match(script, /archive-manifest\.json\.sha256/);
  assert.match(script, /abandon-result\.json\.sha256/);
  assert.match(script, /Assert-AbandonPayloadCleaned/);
  assert.doesNotMatch(script, /Remove-Item[^\n]*\$Backup\.Directory/i);

  const initial = script.indexOf('Assert-AbandonBeforeForwardRecovery $backup $secrets "initial"');
  const recover = script.indexOf("Resolve-AbandonIncompleteRecovery", initial);
  const secondInitial = script.indexOf(
    'Assert-AbandonBeforeForwardRecovery $backup $secrets "initial"', initial + 1,
  );
  const archive = script.indexOf("Publish-AbandonArchive", secondInitial);
  const cleanup = script.indexOf("Invoke-RehearsalPayloadCleanup", archive);
  const final = script.indexOf('Assert-AbandonBeforeForwardRecovery $backup $secrets "final"', cleanup);
  const archiveRecheck = script.indexOf("Assert-AbandonArchive $control.ArchiveRoot", final);
  const publish = script.indexOf("Publish-AbandonResult", final);
  const completeRecovery = script.indexOf("Complete-AbandonIncompleteRecovery", publish);
  assert.ok(
    initial >= 0 && recover > initial && secondInitial > recover && archive > secondInitial
      && cleanup > archive && final > cleanup && archiveRecheck > final
      && publish > archiveRecheck && completeRecovery > publish,
  );
});

test("abandon crash staging recovery is resumable and rejects unsafe residue", async (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows PowerShell 5.1 is Windows-only");
    return;
  }
  const powershell = path.join(
    process.env.SystemRoot ?? "C:\\Windows",
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
  );
  const escapedPath = rehearsalPath.replaceAll("'", "''");
  const command = `
$ErrorActionPreference='Stop'
$source='${escapedPath}'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'source parse failed'}
foreach($name in @('Get-AbandonIncompleteTreeEvidence','Resolve-AbandonIncompleteRecovery','Complete-AbandonIncompleteRecovery')){
  $fn=@($ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true))
  if($fn.Count -ne 1){throw "missing function $name"}
  Invoke-Expression $fn[0].Extent.Text
}
function Get-CanonicalPath([string]$Path){[IO.Path]::GetFullPath($Path).TrimEnd('\\','/')}
function Get-FileSha256([string]$Path){
  $sha=[Security.Cryptography.SHA256]::Create()
  try{([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($Path)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}
function Get-Sha256Text([string]$Value){
  $sha=[Security.Cryptography.SHA256]::Create()
  try{([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}
function Read-ExactSha256File([string]$Path,[string]$Label){
  $value=[IO.File]::ReadAllText($Path,[Text.Encoding]::UTF8).Trim()
  if($value -cnotmatch '^[0-9a-f]{64}$'){throw "$Label invalid"};$value
}
function Assert-NoReparsePoints([string]$Path,[string]$Label){
  $queue=[Collections.Queue]::new();$queue.Enqueue((Get-Item -LiteralPath $Path -Force))
  while($queue.Count -gt 0){$item=$queue.Dequeue();if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint)-ne 0){throw "$Label reparse"};if($item.PSIsContainer){foreach($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)){$queue.Enqueue($child)}}}
}
function Assert-ExactPropertySet([object]$Value,[string[]]$Names,[string]$Label){
  $actual=@($Value.PSObject.Properties.Name|Sort-Object);$expected=@($Names|Sort-Object)
  if(($actual -join "\n") -cne ($expected -join "\n")){throw "$Label properties"}
}
function Write-LauncherEvent([string]$Level,[string]$Event,[string]$Detail){}
$RehearsalId='0123456789ab'
$ApprovedRehearsalResultSha256=(-join @('a')*64)
$root=[IO.Path]::Combine([IO.Path]::GetTempPath(),'teruisi-abandon-recovery-'+[guid]::NewGuid().ToString('N'))
$archiveParent=Join-Path $root 'archives';$resultParent=Join-Path $root 'results';$recoveryParent=Join-Path $root 'recoveries'
[IO.Directory]::CreateDirectory($archiveParent)|Out-Null;[IO.Directory]::CreateDirectory($resultParent)|Out-Null
$paths=[pscustomobject]@{ArchiveParent=$archiveParent;ArchiveRoot=(Join-Path $archiveParent $RehearsalId);ResultParent=$resultParent;ResultRoot=(Join-Path $resultParent $RehearsalId);RecoveryParent=$recoveryParent;RecoveryRoot=(Join-Path $recoveryParent $RehearsalId)}
$h=(-join @('b')*64);$r2=(-join @('c')*64);$deploy=(-join @('d')*64);$config=(-join @('e')*64)
$backup=[pscustomobject]@{Manifest=[pscustomobject]@{cutoverId='cutover-test-0001';r2State=[pscustomobject]@{manifestSha256=$r2};deploymentManifestSha256=$deploy;serviceConfigSha256=$config};ManifestSha256=$h}
$rehearsal=[pscustomobject]@{ResultSha256=$ApprovedRehearsalResultSha256}
$preflight=[pscustomobject][ordered]@{version='teruisi-sales-cutover-abandon-preflight-v1';status='verified';stage='initial';rehearsalId=$RehearsalId;productionCutoverId='cutover-test-0001';backupManifestSha256=$h;forwardRecoveryRecordCount=0;formalStateCount=0;workerAuthorityFileAbsent=$true;workerAuthoritySidecarAbsent=$true;d1EvidenceSha256=$h;postgresqlEvidenceSha256=$h;r2ManifestSha256=$r2;deploymentManifestSha256=$deploy;serviceConfigSha256=$config;checkedAt=[DateTimeOffset]::UtcNow.ToString('o')}
$stage=Join-Path $archiveParent ('.'+$RehearsalId+'.'+(-join @('f')*32)+'.incomplete')
try{
  [IO.Directory]::CreateDirectory($stage)|Out-Null;[IO.File]::WriteAllText((Join-Path $stage 'partial.json'),'{}')
  $first=Resolve-AbandonIncompleteRecovery $paths $backup $rehearsal $preflight
  if(-not $first.RecoveryRequired -or $first.RemovedCount -ne 1 -or (Test-Path -LiteralPath $stage) -or -not (Test-Path -LiteralPath $paths.RecoveryRoot -PathType Container)){throw 'resumable recovery failed'}
  $second=Resolve-AbandonIncompleteRecovery $paths $backup $rehearsal $preflight
  if(-not $second.RecoveryRequired -or $second.RemovedCount -ne 0){throw 'marker resume failed'}
  $published=[pscustomobject]@{ResultPath=(Join-Path $paths.ResultRoot 'abandon-result.json');ResultSha256=$h}
  $rejected=$false;try{Complete-AbandonIncompleteRecovery $paths $published}catch{$rejected=$true}
  if(-not $rejected -or -not (Test-Path -LiteralPath $paths.RecoveryRoot)){throw 'premature marker release did not fail closed'}
  [IO.Directory]::CreateDirectory($paths.ArchiveRoot)|Out-Null;[IO.Directory]::CreateDirectory($paths.ResultRoot)|Out-Null
  $archiveManifest=Join-Path $paths.ArchiveRoot 'archive-manifest.json';[IO.File]::WriteAllText($archiveManifest,'{}');[IO.File]::WriteAllText("$archiveManifest.sha256",(Get-FileSha256 $archiveManifest))
  $resultPath=Join-Path $paths.ResultRoot 'abandon-result.json';[IO.File]::WriteAllText($resultPath,'{}');$published.ResultSha256=Get-FileSha256 $resultPath;[IO.File]::WriteAllText("$resultPath.sha256",$published.ResultSha256)
  Complete-AbandonIncompleteRecovery $paths $published
  if(Test-Path -LiteralPath $paths.RecoveryRoot){throw 'validated marker release failed'}
  $unsafe=Join-Path $resultParent ('.'+$RehearsalId+'.'+(-join @('1')*32)+'.incomplete')
  [IO.Directory]::CreateDirectory($unsafe)|Out-Null
  $bad=$preflight|Select-Object *;$bad.forwardRecoveryRecordCount=1
  $rejected=$false;try{Resolve-AbandonIncompleteRecovery $paths $backup $rehearsal $bad|Out-Null}catch{$rejected=$true}
  if(-not $rejected -or -not (Test-Path -LiteralPath $unsafe)){throw 'mutation preflight did not fail closed'}
  $rejected=$false;try{Resolve-AbandonIncompleteRecovery $paths $backup $rehearsal $preflight|Out-Null}catch{$rejected=$true}
  if(-not $rejected -or -not (Test-Path -LiteralPath $unsafe)){throw 'final sibling did not fail closed'}
  '{"status":"passed"}'
}finally{if([IO.Directory]::Exists($root)){[IO.Directory]::Delete($root,$true)}}
`;
  const encoded = Buffer.from(command, "utf16le").toString("base64");
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /"status":"passed"/);
});

test("rehearsal copies D1 and R2, runs a fresh v4 cutover, and separates owner from ERP", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /Copy-Item -LiteralPath \$backup\.D1 -Destination \$rehearsalD1/);
  assert.match(script, /Copy-Item -LiteralPath \$backup\.R2 -Destination \$rehearsalR2 -Recurse/);
  assert.match(script, /runtime-tools\\node_modules\\wrangler\\wrangler-dist\\cli\.js/);
  assert.match(script, /TERUISI_WRANGLER_CLI_JS/);
  assert.match(script, /--managed-rehearsal-execute/);
  assert.match(script, /--runtime-root", \$canonicalRuntime/);
  assert.match(script, /TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED = "1"/);
  assert.match(script, /TERUISI_DJANGO_CUTOVER_MANAGED", \$null/);
  assert.match(script, /Database-Url "teruisi_sales_owner"/);
  assert.match(script, /Database-Url "teruisi_erp_reference_sync"/);
  assert.match(script, /TERUISI_DJANGO_ERP_DATABASE_URL/);
  assert.match(script, /0090_sales_write_authority\.sql/);
  assert.match(script, /0091_erp_reference_projection\.sql/);
  assert.match(script, /--approved-r2-cleanup-manifest-id/);
  assert.match(script, /sales_snapshot_dry_run/);
  assert.match(script, /sales_snapshot_applied/);
  assert.match(script, /sales-projection-v4/);
  assert.doesNotMatch(script, /existing-migration-apply-run-id/);
  assert.doesNotMatch(script, /approved-run-id[^\n]*9e5/i);
});

test("rehearsal starts only real loopback reader/writer, creates formal smoke, and stops owned processes", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  assert.match(script, /--listen=127\.0\.0\.1:8001/);
  assert.match(script, /--listen=127\.0\.0\.1:8002/);
  assert.match(script, /Wait-DjangoReady "rehearsal reader"/);
  assert.match(script, /Wait-DjangoReady "rehearsal writer"/);
  assert.match(script, /sales_cutover_smoke_receipt/);
  assert.match(script, /sales_write_transaction_rollback_probe|正式 smoke receipt/);
  assert.match(script, /Get-PortListeners 3000/);
  assert.equal((script.match(/@\(3000, 5791, 8001, 8002\)/g) ?? []).length, 3);
  assert.match(script, /Get-PortListeners 5791/);
  assert.match(script, /worker3000Started = \$false/);
  assert.match(script, /helper5791Started = \$false/);
  assert.doesNotMatch(script, /Start-(?:Process|ManagedProcess)[^\n]*3000/);
  assert.doesNotMatch(script, /Start-(?:Process|ManagedProcess)[^\n]*5791/);
  assert.match(script, /Stop-OwnedProcess \$owned\.Service \$owned\.Pid \$owned\.Executable/);
});

test("0092 runs through a separate managed rehearsal capability with real PG preflight", async () => {
  const script = await readFile(rehearsalPath, "utf8");
  const retirement = await readFile(retirementPath, "utf8");
  assert.match(script, /--managed-rehearsal-execute/);
  assert.match(script, /TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED/);
  assert.match(script, /teruisi_sales_rehearsal_retirement_\$RehearsalId/);
  assert.match(script, /0092_sales_domain_retirement\.sql/);
  assert.match(script, /postRetirementPlan\.status -cne "already_completed"/);
  assert.match(script, /preservedEvidenceSha256/);
  assert.match(script, /Wait-RehearsalErpCaughtUp[\s\S]*\$erpStatus\.lastCheckedAt/);
  assert.match(script, /Assert-RetirementPlanBlockersClear \$retirementPlan\.blockers/);
  assert.match(script, /Assert-ExactPropertySet \$Blockers \$expectedNames/);
  assert.match(script, /\[decimal\]\$value -ne 0/);
  assert.doesNotMatch(script, /@\(\$retirementPlan\.blockers\)\.Count/);
  assert.doesNotMatch(script, /NODE_TEST_CONTEXT/);
  assert.doesNotMatch(script, /executeSalesD1RetirementForTest/);

  assert.match(retirement, /executeSalesD1RetirementRehearsalWithDjangoPreflight/);
  assert.match(retirement, /argv\[0\] === "--managed-rehearsal-execute"/);
  assert.match(retirement, /TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED/);
  assert.match(retirement, /process\.env\.TERUISI_DJANGO_RETIREMENT_MANAGED === "1"/);
  assert.match(retirement, /teruisi_sales_rehearsal_\$\{input\.rehearsalId\}/);
  assert.match(retirement, /rehearsals", options\.rehearsalId/);
  assert.match(retirement, /\.wrangler", "state", "v3", "d1"/);
  assert.match(retirement, /\.wrangler", "state", "v3", "r2"/);
  assert.match(retirement, /SELECT current_user, current_database\(\)/);
  assert.match(retirement, /databaseIdentity\.currentUser !== "teruisi_sales_writer"/);
  assert.match(retirement, /sales_cutover_retirement_preflight/);
  assert.match(retirement, /executeSalesD1RetirementInternal/);
});

test("production and rehearsal retirement CLI markers remain mutually exclusive", async () => {
  const retirement = await readFile(retirementPath, "utf8");
  assert.match(retirement, /argv\[0\] !== "--managed-execute"[\s\S]*TERUISI_DJANGO_RETIREMENT_MANAGED/);
  assert.match(retirement, /databaseUrl\.pathname !== "\/teruisi_sales"/);
  assert.match(retirement, /application_name"\) !== "teruisi_sales_retirement"/);
  assert.match(retirement, /argv\[0\] !== "--managed-rehearsal-execute"/);
  assert.match(retirement, /managed rehearsal retirement 参数集合不完整/);

  const environment = { ...process.env };
  delete environment.TERUISI_DJANGO_RETIREMENT_REHEARSAL_MANAGED;
  delete environment.TERUISI_DJANGO_RETIREMENT_MANAGED;
  const result = spawnSync(process.execPath, [retirementPath, "--managed-rehearsal-execute"], {
    env: environment,
    encoding: "utf8",
    windowsHide: true,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /只能由受控隔离演练 operator 调用/);
});
