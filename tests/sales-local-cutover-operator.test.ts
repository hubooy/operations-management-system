import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const operatorPath = path.join(root, "tools", "sales-local-cutover-operator.ps1");
const nativePs5Fixture = path.join(root, "tests", "sales-local-cutover-native-ps5.test.ps1");
const runtimePython = "D:\\teruisi-runtime\\django-sales\\venv\\Scripts\\python.exe";

test("production operator is UTF-8 BOM and parses from disk in Windows PowerShell 5.1", async (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows PowerShell 5.1 is Windows-only");
    return;
  }
  const operator = operatorPath;
  const bytes = await readFile(operator);
  assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
  const powershell = path.join(
    process.env.SystemRoot ?? "C:\\Windows",
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
  );
  const escaped = operator.replaceAll("'", "''");
  const command = `$tokens=$null;$errors=$null;`
    + `[System.Management.Automation.Language.Parser]::ParseFile('${escaped}',[ref]$tokens,[ref]$errors)|Out-Null;`
    + `if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}`;
  const result = spawnSync(powershell, ["-NoProfile", "-NonInteractive", "-Command", command], {
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("production operator native calls are PS5-safe and never expose raw native errors", async (t) => {
  const script = await readFile(operatorPath, "utf8");
  assert.match(script, /function Invoke-BoundedNativeProcess/);
  assert.match(script, /function ConvertFrom-NativeJsonRun/);
  assert.match(script, /\$outerErrorActionPreference = \$ErrorActionPreference/);
  assert.match(script, /\$outerLastExitCode = \$global:LASTEXITCODE/);
  assert.match(script, /\$ErrorActionPreference = "Continue"/);
  assert.match(script, /\$global:LASTEXITCODE = \$null/);
  assert.match(script, /\$nativeExitCode = \$global:LASTEXITCODE/);
  assert.match(script, /\$global:LASTEXITCODE = \$outerLastExitCode/);
  assert.match(script, /native executable unavailable/);
  assert.match(script, /OutputRecordCount/);
  assert.match(script, /CapturedRecordCount/);
  assert.match(script, /OutputTruncated/);
  assert.match(script, /OutputSha256/);
  assert.match(script, /launchFailed=/);
  assert.match(script, /Invoke-BoundedNativeProcess \$Node @\("--version"\)/);
  assert.match(script, /Invoke-BoundedNativeProcess \$pgRestore/);
  assert.match(script, /Invoke-BoundedNativeProcess \$Node \$Arguments/);
  assert.match(script, /Invoke-BoundedNativeProcess \$Python \$Arguments/);
  assert.doesNotMatch(script, /@\(& \$(?:Node|Python|pgRestore)/);
  assert.doesNotMatch(script, /\n\s*& \$(?:Node|Python|pgRestore)/);
  assert.doesNotMatch(script, /output\[-1\]/);
  assert.doesNotMatch(script, /throw "\$Label 失败：\$detail"/);

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
  const fixtureSecret = `operator-ps5-${createHash("sha256")
    .update(`${process.pid}-${Date.now()}`)
    .digest("hex")}`;
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", nativePs5Fixture,
    "-Mode", "operator",
    "-ToolScript", operatorPath,
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
  assert.equal(result.status, 0, `PS5 operator fixture failed; outputSha256=${outputDigest}`);
  const payload = JSON.parse(result.stdout.trim()) as Record<string, unknown>;
  assert.equal(payload.status, "completed");
  assert.equal(payload.mode, "operator");
  assert.equal(payload.powershellEdition, "Desktop");
  assert.match(String(payload.powershellVersion), /^5\.1\./);
  assert.equal(payload.successExitCode, 0);
  assert.equal(payload.failureExitCode, 23);
  assert.equal(payload.missingExecutableExitCode, -1);
  assert.equal(payload.diagnosticTruncated, true);
  assert.equal(payload.processStateRestored, true);
});

test("fully-qualified path gate works in Windows PowerShell 5.1 and rejects drive-relative paths", async (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows PowerShell 5.1 is Windows-only");
    return;
  }
  const service = path.join(root, "tools", "django-local-service.ps1");
  const powershell = path.join(
    process.env.SystemRoot ?? "C:\\Windows",
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
  );
  const escaped = service.replaceAll("'", "''");
  const command = `$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY='1';. '${escaped}' -Action Status;`
    + `if(-not (Test-FullyQualifiedPath 'C:\\absolute\\file.json')){exit 2};`
    + `if(-not (Test-FullyQualifiedPath '\\\\server\\share\\file.json')){exit 3};`
    + `if((Test-FullyQualifiedPath 'C:relative\\file.json')){exit 4};`
    + `if((Test-FullyQualifiedPath '\\rooted-but-drive-relative')){exit 5};`
    + `if((Test-FullyQualifiedPath 'relative\\file.json')){exit 6};exit 0`;
  const result = spawnSync(powershell, ["-NoProfile", "-NonInteractive", "-Command", command], {
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("production cutover operator is runtime-bound, backup-gated, and role-separated", async () => {
  const script = await readFile(path.join(root, "tools", "sales-local-cutover-operator.ps1"), "utf8");
  assert.match(script, /只能从受保护的 runtime app 执行/);
  assert.match(script, /Assert-DeployedApplication/);
  assert.match(script, /Assert-WranglerLocalR2RoundTrip \$InstalledAppRoot/);
  assert.match(script, /Assert-RuntimeAclHardened/);
  assert.match(script, /Assert-ApplicationProcessesStopped "SalesCutover"/);
  assert.match(script, /Get-PortListeners 3000/);
  assert.match(script, /foreach \(\$port in @\(3000, 5791\)\)/);
  assert.equal((script.match(/Get-PortListeners 5791/g) ?? []).length, 2);
  assert.match(script, /ApprovedWorkerReleaseManifestSha256/);
  assert.match(script, /worker-local-release\.mjs/);
  assert.match(script, /D:\\运营管理系统/);
  assert.match(script, /"verify"/);
  assert.match(script, /"verify-guard"/);
  assert.match(script, /--require-sales-retired-code-receipt/);
  assert.match(script, /--expected-protected-source-root-path-sha256/);
  assert.match(script, /--process-policy", "stopped"/);
  assert.match(script, /teruisi-local-worker-release-verification-v1/);
  assert.match(script, /teruisi-legacy-worker-guard-verification-v1/);
  assert.match(script, /backupManifestSha256/);
  assert.match(script, /RequestedBackupApproval -cne \$backupManifestSha256/);
  assert.match(script, /Invoke-WithServiceMutex/);
  assert.match(script, /backupAge[\s\S]*FromMinutes\(360\)/);
  assert.match(script, /\$backupAgeExceeded = \$backupAge -gt \[TimeSpan\]::FromMinutes\(360\)/);
  assert.match(script, /\$rehearsalAgeExceeded = \$rehearsalAge -gt \[TimeSpan\]::FromMinutes\(360\)/);
  assert.match(
    script,
    /\(\$backupAgeExceeded -or \$rehearsalAgeExceeded\)[\s\S]*-not \$validatedRollForwardRecovery/,
  );
  assert.match(script, /deploymentManifestSha256[\s\S]*DeploymentManifestPath/);
  assert.match(script, /serviceConfigSha256[\s\S]*ConfigPath/);
  assert.match(script, /Get-FileSha256 \$d1BackupPath/);
  assert.match(script, /Get-FileSha256 \$pgBackupPath/);
  assert.match(script, /R2 备份实际文件集合与清单不一致/);
  assert.match(script, /ApprovedRehearsalResultSha256/);
  assert.match(script, /function Assert-NoAbandonEvidenceForBackup/);
  assert.match(script, /audits\\cutover-abandon\\recoveries/);
  assert.match(script, /abandon incomplete recovery 尚未完成；拒绝 Plan\/Execute/);
  assert.match(script, /abandon recovery gate 存在无效 marker/);
  assert.match(script, /audits\\cutover-abandon\\\$kind/);
  assert.match(script, /@\("archives", "results"\)/);
  assert.match(script, /abandon gate 存在未发布证据/);
  assert.match(script, /当前 backup\/cutover 已被 create-only abandon 证据撤销/);
  assert.match(script, /rehearsal 已进入不可逆 abandon 协议/);
  assert.match(script, /\$evidenceShaPath = "\$evidencePath\.sha256"/);
  assert.match(script, /teruisi-sales-cutover-abandon-archive-v1/);
  assert.match(script, /teruisi-sales-cutover-abandon-v1/);
  assert.match(script, /teruisi-sales-cutover-rehearsal-result-v1/);
  assert.match(script, /productionCutoverId/);
  assert.match(script, /roll_forward_required/);
  assert.match(script, /rerun_same_runtime_operator_execute/);
  assert.match(script, /sales-cutover-snapshot-gate\.py/);
  assert.match(script, /verify-live/);
  assert.match(script, /sourceCanonicalSnapshotSha256/);
  assert.match(script, /--expected-source-canonical-snapshot-sha256/);
  assert.match(script, /teruisi-sales-forward-recovery-v3/);
  assert.match(script, /workerReleaseManifestSha256/);
  assert.match(script, /workerGuardReceiptSha256/);
  assert.match(script, /djangoDeploymentManifestSha256/);
  assert.match(script, /workerAuthoritySha256/);
  assert.match(script, /Assert-ExactPropertySet \$existingRecovery \$recoveryFields/);
  assert.match(script, /existingRecovery\.status -ceq "completed"/);
  assert.match(script, /existingRecovery\.status -cne "roll_forward_required"/);
  const completedRecoveryReject = script.indexOf("拒绝降级为 roll_forward_required");
  const exactRecoveryReject = script.indexOf("已有前向恢复凭证与本次批准材料不一致");
  const recoveryValidated = script.indexOf("$validatedRollForwardRecovery = $true", exactRecoveryReject);
  const staleRecoveryGate = script.indexOf("仅允许精确 v3 roll_forward_required 同 tuple 前向恢复豁免年龄", recoveryValidated);
  const recoveryRecordRewrite = script.indexOf("$recoveryRecord = [ordered]@");
  assert.ok(completedRecoveryReject >= 0 && recoveryRecordRewrite > completedRecoveryReject);
  assert.ok(exactRecoveryReject >= 0 && completedRecoveryReject > exactRecoveryReject);
  assert.ok(recoveryValidated > completedRecoveryReject);
  assert.ok(staleRecoveryGate > recoveryValidated && recoveryRecordRewrite > staleRecoveryGate);
  const planBranch = script.indexOf('if ($RequestedAction -ceq "Plan")');
  const planStaleReject = script.indexOf('throw "销售切换备份已过期"', planBranch);
  const planWorkerGate = script.indexOf('Invoke-WorkerReleaseGate "Worker release 只读验证"', planBranch);
  assert.ok(planBranch >= 0 && planStaleReject > planBranch && planWorkerGate > planStaleReject);
  assert.match(script, /verify-retirement-audit/);
  assert.match(script, /audit\\sales-retirement\.json/);
  const secondPortFence = script.indexOf("正式切换执行前维护端口已被重新占用");
  const workerReleaseGate = script.indexOf("正式 Worker release 门禁", secondPortFence);
  const workerGuardGate = script.indexOf("正式主 Worker fail-closed guard 门禁", workerReleaseGate);
  const snapshotGate = script.indexOf('"verify-live"', secondPortFence);
  const recoveryWrite = script.indexOf("Write-AtomicJson $forwardRecoveryPath");
  const commonAbandonGate = script.indexOf("\n  Assert-NoAbandonEvidenceForBackup\n");
  const recoveryMarkerGate = script.indexOf("abandon incomplete recovery 尚未完成");
  const exactAbandonGate = script.indexOf("Assert-NoAbandonEvidenceForBackup $rehearsalId");
  const authorityWrite = script.indexOf('Invoke-WorkerAuthority "write-authority"', recoveryWrite);
  const thirdPortFence = script.indexOf("Worker authority 发布后第三次维护端口栅栏失败", authorityWrite);
  const postAuthorityReleaseGate = script.indexOf("Worker authority 发布后 release 复验", thirdPortFence);
  const authorityVerify = script.indexOf('Invoke-WorkerAuthority "verify-authority"', postAuthorityReleaseGate);
  const postgresMutation = script.indexOf("Invoke-DjangoMigrations $secrets");
  assert.ok(secondPortFence >= 0 && workerReleaseGate > secondPortFence);
  assert.ok(workerGuardGate > workerReleaseGate && snapshotGate > workerGuardGate);
  assert.ok(recoveryWrite > snapshotGate);
  assert.ok(commonAbandonGate >= 0 && commonAbandonGate < planBranch);
  assert.ok(recoveryMarkerGate >= 0 && recoveryMarkerGate < commonAbandonGate);
  assert.ok(exactAbandonGate > planBranch && exactAbandonGate < recoveryWrite);
  assert.ok(authorityWrite > recoveryWrite);
  assert.ok(thirdPortFence > authorityWrite);
  assert.ok(postAuthorityReleaseGate > thirdPortFence);
  assert.ok(authorityVerify > postAuthorityReleaseGate);
  assert.ok(postgresMutation > authorityVerify);
  assert.doesNotMatch(
    script.slice(secondPortFence, recoveryWrite),
    /migrate_sales_from_d1[\s\S]*--dry-run/,
  );
  assert.match(script, /Invoke-DjangoMigrations \$secrets "teruisi_sales"/);
  assert.match(script, /Database-Url "teruisi_sales_owner"/);
  assert.match(script, /Database-Url "teruisi_erp_reference_sync"/);
  assert.match(script, /TERUISI_DJANGO_ERP_DATABASE_URL/);
  assert.match(script, /TERUISI_WRANGLER_CLI_JS/);
  assert.match(script, /TERUISI_DJANGO_CUTOVER_MANAGED = "1"/);
  assert.match(script, /--managed-execute/);
  assert.match(script, /--runtime-root", \$ServiceRuntime/);
  assert.match(script, /--approved-r2-cleanup-manifest-id/);
  assert.match(script, /--repository-root", \$InstalledAppRoot/);
  assert.match(script, /d1_terminal_attested/);
  assert.match(script, /attestationPayloadSha256/);
  assert.doesNotMatch(
    script,
    /0092_sales_domain_retirement|PlanSalesD1Retirement|CreateSalesCutoverSmokeReceipt|RetireSalesD1/,
    "formal operator must stop at PostgreSQL authority/terminal attestation; live D1 retirement is a later guarded phase",
  );
  assert.doesNotMatch(script, /existing-migration-apply-run-id/);
  assert.doesNotMatch(script, /postgresql:\/\//i);
});

test("cutover runtime packages fixed Wrangler and exact pre-schema migrations", async () => {
  const service = await readFile(path.join(root, "tools", "django-local-service.ps1"), "utf8");
  const cutover = await readFile(path.join(root, "tools", "sales-local-cutover.ts"), "utf8");
  assert.match(service, /sales-local-cutover-operator\.ps1/);
  assert.match(service, /sales-local-cutover-rehearsal\.ps1/);
  assert.match(service, /sales-cutover-snapshot-gate\.py/);
  assert.match(service, /worker-local-release\.mjs/);
  assert.match(service, /sales-local-cutover\.ts/);
  assert.match(service, /0090_sales_write_authority\.sql/);
  assert.match(service, /0091_erp_reference_projection\.sql/);
  assert.match(service, /runtime-tools\\node_modules\\wrangler/);
  assert.match(service, /Copy-WranglerRuntimeClosure/);
  assert.match(cutover, /"runtime-tools", "node_modules", "wrangler", "wrangler-dist", "cli\.js"/);
  assert.doesNotMatch(cutover, /"runtime-tools", "wrangler", "wrangler-dist", "cli\.js"/);
});
