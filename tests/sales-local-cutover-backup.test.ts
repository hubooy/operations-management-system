import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { copyFile, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

const executeFile = promisify(execFile);
const repositoryRoot = fileURLToPath(new URL("..", import.meta.url));
const sqliteBackupTool = path.join(repositoryRoot, "tools", "sqlite-consistent-backup.py");
const pruneTool = path.join(repositoryRoot, "tools", "sales-local-cutover-backup-prune.ps1");
const backupTool = path.join(repositoryRoot, "tools", "sales-local-cutover-backup.ps1");
const ps5LauncherFixture = path.join(
  repositoryRoot,
  "tests",
  "sales-local-cutover-backup-ps5-launcher.test.ps1",
);
const runtimePython = "D:\\teruisi-runtime\\django-sales\\venv\\Scripts\\python.exe";
const pythonExecutable = process.platform === "win32" && existsSync(runtimePython)
  ? runtimePython
  : "python";

async function fileSha256(filePath: string) {
  return createHash("sha256").update(await readFile(filePath)).digest("hex");
}

test("consistent SQLite backup is verified and never overwrites its source or destination", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-sqlite-backup-"));
  const source = path.join(root, "source.sqlite");
  const destination = path.join(root, "backup.sqlite");
  const executableBackupTool = path.join(root, "sqlite-consistent-backup.py");
  await copyFile(sqliteBackupTool, executableBackupTool);
  const database = new DatabaseSync(source);
  try {
    database.exec(`
      CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY);
      INSERT INTO sales_order_lines VALUES (1), (2);
      CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY);
      INSERT INTO sales_import_batches VALUES ('batch-1');
      CREATE TABLE erp_product_master (product_code TEXT PRIMARY KEY);
      INSERT INTO erp_product_master VALUES ('P-1');
      CREATE TABLE sales_import_uploads (id TEXT PRIMARY KEY);
      CREATE TABLE sales_import_upload_chunks (upload_id TEXT, chunk_index INTEGER);
      CREATE TABLE sales_overview_cache_state (
        id INTEGER PRIMARY KEY, sales_revision INTEGER, erp_product_revision INTEGER
      );
      INSERT INTO sales_overview_cache_state VALUES (1, 8, 5);
    `);
  } finally {
    database.close();
  }

  try {
    const sourceBefore = await fileSha256(source);
    const { stdout } = await executeFile(pythonExecutable, [
      executableBackupTool,
      "--source", source,
      "--destination", destination,
    ], { cwd: repositoryRoot, windowsHide: true });
    const result = JSON.parse(stdout.trim()) as Record<string, unknown>;
    assert.equal(result.status, "completed");
    assert.equal(result.quickCheck, "ok");
    assert.equal((result.counts as Record<string, number>).sales_order_lines, 2);
    assert.deepEqual(result.revisions, { erp: 5, sales: 8 });
    assert.equal(await fileSha256(source), sourceBefore);
    assert.equal(await fileSha256(destination), result.sha256);

    const backup = new DatabaseSync(destination, { readOnly: true });
    try {
      assert.equal(backup.prepare("PRAGMA quick_check").get()?.quick_check, "ok");
      assert.equal(backup.prepare("SELECT COUNT(*) AS count FROM sales_order_lines").get()?.count, 2);
    } finally {
      backup.close();
    }

    await assert.rejects(
      executeFile(pythonExecutable, [
        executableBackupTool,
        "--source", source,
        "--destination", destination,
      ], { cwd: repositoryRoot, windowsHide: true }),
      /destination must be a new file/,
    );
    assert.equal(await fileSha256(source), sourceBefore);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("cutover backup launcher is explicit, maintenance-gated, secret-safe, and non-overwriting", async () => {
  const script = await readFile(backupTool, "utf8");
  assert.match(script, /-not \$Execute -or -not \$ConfirmedMaintenance/);
  assert.match(script, /Assert-ApplicationProcessesStopped "CutoverBackup"/);
  assert.match(script, /Invoke-WithServiceMutex/);
  assert.match(script, /foreach \(\$port in @\(3000, 5791\)\)/);
  assert.match(script, /\[string\]\$config\.erpSourceD1 -cne \$source/);
  assert.match(script, /D1 与 R2 必须来自同一个固定 Wrangler persist 根目录/);
  assert.match(script, /Join-Path \$persistRoot "v3\\r2"/);
  assert.match(script, /@\("miniflare-R2BucketObject", "site-creator-r2"\)/);
  assert.match(script, /R2 状态源不得包含重解析点/);
  assert.match(script, /runtime app 缺少受控 SQLite 备份工具/);
  assert.match(script, /--format=custom/);
  assert.match(script, /\$env:PGPASSWORD = \$secrets\.OwnerPassword/);
  assert.match(script, /SetEnvironmentVariable\("PGPASSWORD", \$previousPgPassword/);
  assert.match(script, /PostgreSQL 与 D1 备份前行数不一致/);
  assert.match(script, /PostgreSQL 与 D1 备份前 revision 不一致/);
  assert.match(script, /to_regclass\('public\.sales_write_authority'\)/);
  assert.match(script, /authority_status = "legacy_absent"/);
  assert.match(script, /ConvertTo-AsciiPythonLauncher \$evidenceCode/);
  assert.match(script, /\[Convert\]::ToBase64String\(/);
  assert.match(script, /\[Text\.Encoding\]::UTF8\.GetBytes\(\$Code\)/);
  assert.match(script, /Python launcher must be one ASCII line/);
  assert.doesNotMatch(script, /& \$Python "-c" \$evidenceCode/);
  assert.match(script, /function Invoke-BoundedNativeProcess/);
  assert.match(script, /\$outerErrorActionPreference = \$ErrorActionPreference/);
  assert.match(script, /\$ErrorActionPreference = "Continue"/);
  assert.match(script, /\$outerLastExitCode = \$global:LASTEXITCODE/);
  assert.match(script, /\$global:LASTEXITCODE = \$null/);
  assert.match(script, /\$nativeExitCode = \$global:LASTEXITCODE/);
  assert.match(script, /catch \{\s*\$output = @\(\$_\)\s*\$nativeExitCode = \$null/);
  assert.match(script, /\$global:LASTEXITCODE = \$outerLastExitCode/);
  assert.match(script, /\$ErrorActionPreference = \$outerErrorActionPreference/);
  assert.match(script, /function Get-BoundedNativeDiagnostic/);
  assert.match(script, /\$maxRecords = 32/);
  assert.match(script, /\$maxCharacters = 4096/);
  assert.match(script, /Protect-LogText \(\[string\]\(\$records\[\$index\]\)\)/);
  assert.match(script, /credential`\$1\[redacted\]/);
  assert.match(script, /uri:\/\/\[redacted\]@/);
  assert.match(script, /outputSha256=/);
  assert.equal((script.match(/Invoke-BoundedNativeProcess \$/g) ?? []).length, 4);
  assert.doesNotMatch(script, /@\(& \$Python/);
  assert.doesNotMatch(script, /@\(& \$pgRestore/);
  assert.doesNotMatch(script, /& \$pgDump/);
  assert.match(script, /backup-manifest\.json\.sha256/);
  assert.match(script, /Assert-WranglerLocalR2RoundTrip \$InstalledAppRoot/);
  assert.doesNotMatch(script, /postgresql:\/\//i);
  assert.match(script, /\$backupPublished = \$false/);
  assert.match(script, /\$backupAttemptFailed = \$true/);
  assert.match(script, /\$backupPublished = \$true/);
  assert.match(script, /try \{\s*# From the first creation[\s\S]*New-Item -ItemType Directory -Path \$workingDirectory/);
  assert.match(script, /\.sales-cutover-\[0-9a-f\]\{24\}\\\.\[0-9a-f\]\{32\}\\\.incomplete/);
  assert.match(script, /Get-CanonicalPath \(Split-Path -Parent \$workingCanonical\)/);
  assert.match(script, /未发布备份工作目录不得包含重解析点/);
  assert.match(script, /Remove-Item -LiteralPath \$workingCanonical -Recurse -Force/);
  assert.match(script, /teruisi-sales-cutover-unpublished-cleanup-v1/);
  assert.match(script, /workingDirectoryNameSha256 = Get-Sha256Text \$expectedWorkingName/);
  assert.doesNotMatch(script, /Remove-Item -LiteralPath \$finalDirectory/i);
  assert.doesNotMatch(script, /Remove-Item -LiteralPath \$backupRoot/i);
});

test("cutover backup Python launcher preserves multiline code and exact failures in Windows PowerShell 5", async (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows PowerShell 5 fixture is Windows-only");
    return;
  }
  const powershell = path.join(
    process.env.SystemRoot ?? "C:\\Windows",
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
  );
  if (!existsSync(powershell) || !existsSync(pythonExecutable)) {
    t.skip("Windows PowerShell 5 or the controlled Python runtime is unavailable");
    return;
  }

  const fixtureSecret = `ps5-fixture-${createHash("sha256")
    .update(`${process.pid}-${Date.now()}`)
    .digest("hex")}`;
  const result = spawnSync(powershell, [
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    ps5LauncherFixture,
    "-BackupScript",
    backupTool,
    "-Python",
    pythonExecutable,
  ], {
    encoding: "utf8",
    windowsHide: true,
    env: {
      ...process.env,
      TERUISI_BACKUP_PS5_FIXTURE_SECRET: fixtureSecret,
    },
  });
  const externalOutput = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  assert.equal(externalOutput.includes(fixtureSecret), false);
  assert.equal(externalOutput.includes("postgresql://owner:"), false);
  const outputDigest = createHash("sha256").update(externalOutput).digest("hex");
  assert.equal(result.status, 0, `PS5 launcher fixture failed; outputSha256=${outputDigest}`);
  const payload = JSON.parse(result.stdout.trim()) as Record<string, unknown>;
  assert.equal(payload.status, "completed");
  assert.equal(payload.powershellEdition, "Desktop");
  assert.match(String(payload.powershellVersion), /^5\.1\./);
  assert.equal(payload.successExitCode, 0);
  assert.equal(payload.failureExitCode, 23);
  assert.equal(payload.countMismatchRejected, true);
  assert.equal(payload.missingExecutableExitCode, -1);
  assert.equal(payload.redactionBeforeHashVerified, true);
  assert.equal(payload.errorActionPreferenceRestored, true);
  assert.match(String(payload.failureOutputSha256), /^[0-9a-f]{64}$/);
});

test("stale cutover backup pruning is explicit, reference-safe, and direct-child only", async () => {
  const script = await readFile(pruneTool, "utf8");
  assert.match(script, /-Execute 与 -ConfirmedDeleteStaleBackups/);
  assert.match(script, /ApprovedCurrentDeploymentManifestSha256/);
  assert.match(script, /Assert-ApplicationProcessesStopped "CutoverBackupPrune"/);
  assert.match(script, /Get-PortListeners \$port/);
  assert.match(script, /@\(3000, 5791, 5432, 8001, 8002\)/);
  assert.match(script, /Assert-RuntimeAclHardened/);
  assert.match(script, /runtime backups 的现有直接子目录/);
  assert.match(script, /Get-BackupTreeEvidence/);
  assert.match(script, /ReparsePoint/);
  assert.match(script, /deploymentManifestSha256 -ceq \$currentDeploymentSha256/);
  assert.match(script, /Assert-BackupHasNoBlockingReferences \$manifestSha256/);
  assert.match(script, /teruisi-sales-forward-recovery-v1/);
  assert.match(script, /teruisi-sales-forward-recovery-v2/);
  assert.match(script, /teruisi-sales-forward-recovery-v3/);
  assert.match(script, /sourceCanonicalSnapshotSha256/);
  assert.match(script, /workerReleaseManifestSha256/);
  assert.match(script, /workerGuardReceiptSha256/);
  assert.match(script, /workerAuthoritySha256/);
  assert.match(script, /roll_forward_required/);
  assert.match(script, /backupManifestSha256 -ceq \$BackupManifestSha256/);
  assert.match(script, /teruisi-sales-cutover-rehearsal-v1/);
  assert.match(script, /completed backup 仍被未完成或未处置的 rehearsal state 引用/);
  assert.match(script, /Test-CompletedRehearsalAbandoned/);
  assert.match(script, /audits\\cutover-abandon\\recoveries\\\$rehearsalId/);
  assert.match(script, /Never prune the backup needed to resume/);
  assert.match(script, /teruisi-sales-cutover-abandon-v1/);
  assert.match(script, /teruisi-sales-cutover-abandon-archive-v1/);
  assert.match(script, /teruisi-sales-cutover-abandon-preflight-v1/);
  assert.match(script, /approved_for_controlled_prune/);
  assert.match(script, /abandon-result\.json\.sha256/);
  assert.match(script, /archive-manifest\.json\.sha256/);
  assert.match(script, /initialPreflightEvidenceSha256/);
  assert.match(script, /finalPreflightEvidenceSha256/);
  assert.match(script, /sales-postgresql-authority\.json/);
  assert.match(script, /Assert-NoReparsePoints \$archiveRoot/);
  assert.match(script, /currentRehearsalResultPath/);
  assert.match(script, /archivedState\.status -cne "completed"/);
  assert.match(script, /Test-CompletedRehearsalAbandoned `[\s\S]*\$BackupManifestSha256 \$BackupCutoverId/);
  assert.match(script, /payloadDisposition -cne "cleaned"/);
  assert.match(script, /payloadCleanupAuditPath/);
  assert.match(script, /payloadCleanupAuditSha256/);
  assert.match(script, /teruisi-sales-rehearsal-payload-cleanup-v1/);
  assert.match(script, /audit\.backupManifestSha256 -cne \[string\]\$State\.backupManifestSha256/);
  assert.match(script, /Test-ExactPropertySet \$audit/);
  assert.match(script, /database\.verifiedAbsent/);
  assert.match(script, /payload\.verifiedAbsent/);
  assert.match(script, /State\.databaseRetained -isnot \[bool\]/);
  assert.match(script, /expectedTargets = @\(/);
  assert.match(script, /completed backup 仍被正式前向恢复记录引用/);
  assert.match(script, /completed backup 仍被正式 mutation state 引用/);
  assert.match(script, /if \(-not \$matchesBackup\) \{ continue \}/);
  assert.match(script, /\$OrphanIncompleteGraceMinutes = 60/);
  assert.match(script, /\.sales-cutover-\[0-9a-f\]\{24\}\\\.\[0-9a-f\]\{32\}\\\.incomplete/);
  assert.match(script, /LatestWriteTimeUtc/);
  assert.match(script, /未发布 incomplete backup 未超过安全宽限或时间无效/);
  assert.match(script, /\[Environment\]::CurrentDirectory/);
  assert.match(script, /Test-PathIsSameOrDescendant/);
  assert.match(script, /拒绝删除当前进程工作目录/);
  assert.match(script, /freshEvidence = Get-BackupTreeEvidence \$target\.Path/);
  assert.match(script, /Remove-Item -LiteralPath \$target\.Path -Recurse -Force/);
  assert.match(script, /teruisi-sales-cutover-backup-prune-v1/);
  assert.doesNotMatch(script, /failed-\[A-Za-z0-9/);
  assert.doesNotMatch(script, /Get-ChildItem[^\n]*backups[^\n]*\*/i);
});
