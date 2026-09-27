import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const operatorPath = path.join(root, "tools", "django-postgres-maintenance.ps1");
const servicePath = path.join(root, "tools", "django-local-service.ps1");
const helperPath = path.join(root, "tools", "postgres-consistent-backup.py");
const financeHealthPath = path.join(root, "backend", "teruisi_backend", "health.py");
const powershell = path.join(
  process.env.SystemRoot ?? "C:\\Windows",
  "System32",
  "WindowsPowerShell",
  "v1.0",
  "powershell.exe",
);
const runtimePython = "D:\\teruisi-runtime\\django-sales\\venv\\Scripts\\python.exe";

test("finance.0006 source, receipt and all physical tables stay behind formal gates", async () => {
  const [service, operator, backup] = await Promise.all([
    readFile(servicePath, "utf8"),
    readFile(operatorPath, "utf8"),
    readFile(helperPath, "utf8"),
  ]);
  assert.match(service, /0006_raw_workbook_bytes_v2\.py/);
  assert.match(operator, /0006_raw_workbook_bytes_v2/);
  for (const table of ["finance_raw_workbook_attestations",
    "finance_raw_workbook_columns", "finance_raw_workbook_cells"]) {
    assert.match(operator, new RegExp(table));
    assert.match(backup, new RegExp(table));
  }
  assert.match(backup, /finance raw workbook daily backup is not admitted/);
});

test("0075 source and receipt cannot enter formal app or restore", async () => {
  const [service, operator, helper, audit] = await Promise.all([
    readFile(servicePath, "utf8"),
    readFile(operatorPath, "utf8"),
    readFile(helperPath, "utf8"),
    readFile(path.join(root, "tools", "protected-ai-restore-static-audit.py"), "utf8"),
  ]);
  assert.match(service, /'0075_business_v4_report_restricted_page\.py'/);
  assert.match(operator, /67\|68\|69\|70\|71\|72\|73\|74\|75/);
  assert.match(helper, /"0075_business_v4_report_restricted_page"/);
  assert.match(audit, /formal restore gate omits protected 0075 receipt/);
});

test("protected 0073 stays behind the formal restore and backup preflight", async () => {
  const [operator, helper] = await Promise.all([
    readFile(operatorPath, "utf8"), readFile(helperPath, "utf8"),
  ]);
  assert.match(operator, /67\|68\|69\|70\|71\|72\|73/);
  assert.match(operator, /Assert-MaintenanceProtectedArchiveUnsupported/);
  assert.match(helper, /0073_business_promotion_budget_v11_login_attestation/);
  assert.match(helper, /protected_business_budget_v11_login_attestations/);
  assert.match(helper, /teruisi_ai_budget_v11_attestor_v2_login/);
});

test("finance.0003 marker guards are migration-gated in backup and readiness", async () => {
  const [backup, health, datasetManifest] = await Promise.all([
    readFile(helperPath, "utf8"),
    readFile(financeHealthPath, "utf8"),
    readFile(path.join(root, "backend", "system_datasets", "manifest.json"), "utf8"),
  ]);
  for (const source of [backup, health]) {
    assert.match(source, /0003_finance_source_revision_guard/);
    assert.match(source, /finance_source_revision_markers/);
    for (const trigger of ["finance_line_revision_required",
      "finance_month_revision_required", "finance_batch_revision_required",
      "finance_source_revision_required"]) {
      assert.ok(source.includes(trigger), `missing ${trigger}`);
    }
    assert.match(source, /finance_source_mark_revision_required/);
    assert.match(source, /finance_source_revision_required_at_commit/);
    assert.match(source, /tgdeferrable.*tginitdeferred/);
    assert.match(source, /prosecdef.*proconfig/);
    assert.match(source, /has_table_privilege/);
    assert.match(source, /has_column_privilege/);
    assert.match(source, /has_function_privilege/);
  }
  assert.doesNotMatch(datasetManifest, /finance_source_revision_markers/);
});

test("finance.0004 and netshop.0003 maintenance guards remain migration-gated", async () => {
  const [backup, health, datasetManifest] = await Promise.all([
    readFile(helperPath, "utf8"),
    readFile(financeHealthPath, "utf8"),
    readFile(path.join(root, "backend", "system_datasets", "manifest.json"), "utf8"),
  ]);
  for (const source of [backup, health]) {
    for (const name of ["0004_finance_revision_monotonic",
      "finance_revision_monotonic", "finance_revision_monotonic_guard",
      "0003_netshop_source_revision_guard", "netshop_source_revision_markers",
      "netshop_row_revision_required", "netshop_batch_revision_required",
      "netshop_source_revision_required", "netshop_source_revision_monotonic"]) {
      assert.ok(source.includes(name), `missing ${name}`);
    }
    assert.match(source, /pg_has_role/);
    assert.match(source, /has_column_privilege/);
    assert.match(source, /tgdeferrable.*tginitdeferred/);
    assert.match(source, /prosecdef.*proconfig/);
  }
  assert.doesNotMatch(datasetManifest, /netshop_source_revision_markers/);
});

test("PostgreSQL maintenance operators parse under Windows PowerShell 5", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  const escapedPath = operatorPath.replaceAll("'", "''");
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
});

test("Windows PowerShell 5 can generate the restore rehearsal secret", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  const escapedPath = servicePath.replaceAll("'", "''");
  const command = [
    "$env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY='1';",
    `. '${escapedPath}';`,
    "$first=New-RandomSecret; $second=New-RandomSecret;",
    "if($first -cnotmatch '^[0-9a-f]{96}$'){exit 2};",
    "if($second -cnotmatch '^[0-9a-f]{96}$'){exit 3};",
    "if($first -ceq $second){exit 4};",
    "exit 0",
  ].join(" ");
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-Command", command,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(result.stdout, "");
});

test("maintenance reuses the deployed service's strict configuration contract", async () => {
  const script = await readFile(operatorPath, "utf8");
  const context = script.match(
    /function Assert-MaintenanceRuntimeContext \{([\s\S]*?)\r?\n\}/,
  )?.[1];
  assert.ok(context, "runtime context validator must remain discoverable");
  assert.match(context, /\$config = Get-ServiceConfig/);
  assert.match(context, /\$config\.postgresAddress -cne "127\.0\.0\.1:5432"/);
  assert.doesNotMatch(context, /config\.version -ne 3/);
});

test("daily backup is online read-only and never changes managed service state", async () => {
  const script = await readFile(operatorPath, "utf8");
  const backupBlock = script.match(
    /function Invoke-MaintenanceBackup \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Assert-MaintenanceRehearsalListenerOwnership/,
  )?.[1];
  assert.ok(backupBlock, "backup function must remain discoverable");
  assert.match(backupBlock, /Assert-PostgresListenerOwnership/);
  assert.match(backupBlock, /Test-PostgresReady/);
  assert.match(backupBlock, /权威 PostgreSQL 当前未运行；日常备份不会自动启停服务/);
  assert.match(backupBlock, /postgres-consistent-backup\.py|\$evidenceTool/);
  assert.ok(backupBlock.indexOf("Invoke-MaintenanceProtectedAiPreflight")
    < backupBlock.indexOf("Get-MaintenanceBackupRoot $true"),
    "protected schema must be refused before a backup directory is created");
  assert.match(backupBlock, /serviceStateChanged = \$false/);
  assert.doesNotMatch(backupBlock, /Start-Postgres|Stop-Postgres|Start-ServiceStack|Stop-ServiceStack/);
  assert.doesNotMatch(script, /Invoke-WithServiceMutex/);
});

test("backup evidence and archive are bound to one exported PostgreSQL snapshot", async () => {
  const helper = await readFile(helperPath, "utf8");
  const operator = await readFile(operatorPath, "utf8");
  assert.match(helper, /ISOLATION LEVEL REPEATABLE READ READ ONLY/);
  assert.match(helper, /SELECT pg_export_snapshot\(\)/);
  assert.match(helper, /--snapshot=\{snapshot\}/);
  assert.match(helper, /--format=custom/);
  assert.match(helper, /--no-owner/);
  assert.match(helper, /--no-privileges/);
  assert.match(helper, /contentSha256/);
  assert.match(helper, /canonicalSha256/);
  assert.match(helper, /sales_write_authority/);
  assert.match(helper, /netshop_write_authority/);
  assert.match(helper, /netshop_data_revisions/);
  assert.match(helper, /netshopRevisions/);
  assert.match(helper, /netshopWriteAuthority/);
  assert.match(helper, /market_data_revisions/);
  assert.match(helper, /market_write_authority/);
  assert.match(helper, /marketRevisions/);
  assert.match(helper, /marketWriteAuthority/);
  assert.match(helper, /product_data_revisions/);
  assert.match(helper, /product_write_authority/);
  assert.match(helper, /productsRevisions/);
  assert.match(helper, /productsWriteAuthority/);
  assert.match(helper, /inventory_data_revisions/);
  assert.match(helper, /inventory_write_authority/);
  assert.match(helper, /inventoryRevisions/);
  assert.match(helper, /inventoryWriteAuthority/);
  assert.match(helper, /inventory_replenishment_group_deliveries/);
  assert.match(operator, /inventory_replenishment_group_deliveries/);
  assert.match(helper, /customer_service_data_revisions/);
  assert.match(helper, /customer_service_write_authority/);
  assert.match(helper, /customerServiceRevisions/);
  assert.match(helper, /customerServiceWriteAuthority/);
  assert.match(helper, /erpReferenceWriteAuthority/);
  assert.match(operator, /customer_service_raw_upload_chunks/);
  assert.match(operator, /PostgreSQL 客服 revision 证据/);
  assert.match(operator, /客服 PostgreSQL 写入权威证据/);
  assert.match(helper, /django_migrations/);
  assert.match(helper, /startswith\(ALLOWED_TABLE_PREFIXES\)/);
  assert.match(helper, /"sales_", "erp_", "finance_", "netshop_", "market_", "product_"/);
  assert.match(operator, /PostgreSQL 网店 revision 证据/);
  assert.match(operator, /网店 PostgreSQL 写入权威证据/);
  assert.match(operator, /PostgreSQL 市场 revision 证据/);
  assert.match(operator, /市场 PostgreSQL 写入权威证据/);
  assert.match(operator, /PostgreSQL 商品经营 revision 证据/);
  assert.match(operator, /商品经营 PostgreSQL 写入权威证据/);
  assert.match(operator, /PostgreSQL 库存 revision 证据/);
  assert.match(operator, /库存 PostgreSQL 写入权威证据/);
  assert.match(operator, /pg_restore\.exe/);
  assert.match(operator, /@\("--list", \$dumpPath\)/);
  assert.match(operator, /backup-manifest\.json\.sha256/);
  assert.match(operator, /Read-MaintenanceArchive \$workingDirectory/);
  assert.match(operator, /Move-Item -LiteralPath \$workingDirectory -Destination \$finalDirectory/);
});

test("credentials stay in bounded process environment and diagnostics are redacted", async () => {
  const helper = await readFile(helperPath, "utf8");
  const operator = await readFile(operatorPath, "utf8");
  assert.match(operator, /Invoke-MaintenancePgEnvironment/);
  assert.match(operator, /PGPASSWORD = \$secrets\.OwnerPassword/);
  assert.match(operator, /\[Environment\]::SetEnvironmentVariable\(\$name, \$previous\[\$name\], "Process"\)/);
  assert.doesNotMatch(operator, /--password|--database-url/i);
  assert.match(helper, /psycopg\.connect\(""\)/);
  assert.match(helper, /MAX_NATIVE_DIAGNOSTIC_BYTES/);
  assert.match(helper, /outputSha256/);
  assert.match(helper, /if ":\/\/" in message or "password" in message\.lower\(\)/);
  assert.doesNotMatch(helper, /print\([^\n]*(?:stderr|stdout)/);
});

test("restore rehearsal uses a separate cluster and never creates or drops a production database", async () => {
  const script = await readFile(operatorPath, "utf8");
  const helper = await readFile(helperPath, "utf8");
  const pgCtlStartBlock = script.match(
    /function Invoke-MaintenancePgCtlStart\(([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Remove-MaintenanceRehearsalData/,
  )?.[1];
  const restoreBlock = script.match(
    /function Invoke-MaintenanceRestoreRehearsal \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Get-MaintenancePrunePlan/,
  )?.[1];
  assert.ok(pgCtlStartBlock, "bounded pg_ctl start helper must remain discoverable");
  assert.ok(restoreBlock, "restore function must remain discoverable");
  assert.match(pgCtlStartBlock, /Start-Process -FilePath \$PgCtl/);
  assert.match(pgCtlStartBlock, /-WindowStyle Hidden/);
  assert.match(pgCtlStartBlock, /-RedirectStandardOutput \$stdoutPath/);
  assert.match(pgCtlStartBlock, /-RedirectStandardError \$stderrPath/);
  assert.match(pgCtlStartBlock, /\.WaitForExit\(45000\)/);
  assert.doesNotMatch(pgCtlStartBlock, /Start-Process[^\r\n]*\s-Wait(?:\s|$)/);
  assert.match(restoreBlock, /initdb\.exe/);
  assert.match(restoreBlock, /createuser\.exe/);
  assert.doesNotMatch(script, /createuser[\s\S]{0,800}--dbname/);
  assert.match(restoreBlock, /rehearsals\\postgres-restore/);
  assert.match(restoreBlock, /--auth-host=scram-sha-256/);
  assert.match(restoreBlock, /-h 127\.0\.0\.1/);
  assert.match(restoreBlock, /max_connections=10/);
  assert.match(restoreBlock, /shared_buffers=128MB/);
  assert.match(restoreBlock, /"restore"/);
  assert.match(restoreBlock, /--timeout-seconds", "1800"/);
  assert.match(helper, /"--single-transaction"/);
  assert.match(helper, /subprocess\.TimeoutExpired/);
  assert.match(restoreBlock, /expectedContentSha256/);
  assert.match(restoreBlock, /restoredContentSha256/);
  assert.match(restoreBlock, /productionDatabaseTouched = \$false/);
  assert.match(restoreBlock, /serviceStateChanged = \$false/);
  assert.match(restoreBlock, /Initialize-MaintenanceRehearsalRoles/);
  assert.ok(
    restoreBlock.indexOf("Assert-MaintenanceProtectedArchiveUnsupported")
      < restoreBlock.indexOf("initdb.exe"),
    "protected archive must be refused before a restore cluster is created",
  );
  assert.match(
    restoreBlock,
    /Assert-MaintenanceRehearsalListenerOwnership[\s\S]*?Initialize-MaintenanceRehearsalRoles/,
  );
  assert.ok(
    restoreBlock.indexOf("Initialize-MaintenanceRehearsalRoles")
      < restoreBlock.indexOf('"restore"'),
    "policy roles must exist before pg_restore replays row-level policies",
  );
  for (const role of [
    "teruisi_sales_owner",
    "teruisi_sales_reader",
    "teruisi_sales_writer",
    "teruisi_erp_reference_reader",
    "teruisi_erp_reference_writer",
    "teruisi_finance_reader",
    "teruisi_finance_writer",
    "teruisi_netshop_reader",
    "teruisi_netshop_writer",
    "teruisi_market_reader",
    "teruisi_market_writer",
    "teruisi_products_reader",
    "teruisi_products_writer",
  ]) {
    assert.match(script, new RegExp(`"${role}"`));
  }
  assert.match(
    restoreBlock,
    /Assert-MaintenanceRehearsalListenerOwnership[\s\S]*?\$isolatedStarted = \$true/,
  );
  assert.doesNotMatch(restoreBlock, /postgresSuperuser|Get-ErpRoleProvisioningSecrets/);
  assert.doesNotMatch(restoreBlock, /DROP DATABASE|CREATE DATABASE/);
  assert.doesNotMatch(restoreBlock, /(?:PGPORT|--port)[^\n]*5432/);
});

test("protected AI preflight is explicit, read-only and leaves formal archive flags unchanged", async () => {
  const operator = await readFile(operatorPath, "utf8");
  const helper = await readFile(helperPath, "utf8");
  assert.match(operator, /"ProtectedAiPreflight" \{ Invoke-MaintenanceProtectedAiPreflight \}/);
  assert.match(operator, /function Invoke-MaintenanceProtectedAiPreflight/);
  assert.match(operator, /default_transaction_read_only=on/);
  assert.match(operator, /Assert-MaintenanceProtectedArchiveUnsupported/);
  assert.match(helper, /BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY/);
  assert.match(helper, /protected-preflight/);
  assert.match(helper, /FORMAL_DUMP_FLAGS = \("--no-owner", "--no-privileges"\)/);
  assert.match(helper, /FORMAL_RESTORE_FLAGS = \("--no-owner", "--no-privileges"\)/);
});

test("protected archive is rejected by pure operator guard before restore startup", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  const escapedScript = operatorPath.replaceAll("'", "''");
  const command = [
    "$env:TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY='1';",
    `. '${escapedScript}';`,
    "$old=[pscustomobject]@{evidence=[pscustomobject]@{migrations=@([pscustomobject]@{app='ai_assistant';name='0066_business_promotion_budget_v11_durable_stage'});tables=[pscustomobject]@{}}};",
    "Assert-MaintenanceProtectedArchiveUnsupported $old;",
    "$protected=[pscustomobject]@{evidence=[pscustomobject]@{migrations=@([pscustomobject]@{app='ai_assistant';name='0068_business_promotion_budget_v11_verifier_receipt'});tables=[pscustomobject]@{}}};",
    "try {Assert-MaintenanceProtectedArchiveUnsupported $protected; exit 9} catch {};",
    "$rawReceipt=[pscustomobject]@{evidence=[pscustomobject]@{migrations=@([pscustomobject]@{app='finance';name='0005_raw_column_evidence_v2'});tables=[pscustomobject]@{}}};",
    "try {Assert-MaintenanceProtectedArchiveUnsupported $rawReceipt; exit 10} catch {};",
    "$rawTable=[pscustomobject]@{evidence=[pscustomobject]@{migrations=@();tables=[pscustomobject]@{finance_raw_column_evidence_cells=0}}};",
    "try {Assert-MaintenanceProtectedArchiveUnsupported $rawTable; exit 11} catch {};",
    "exit 0",
  ].join(" ");
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-Command", command,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("restore cleanup and retention deletion are constrained to exact child identities", async () => {
  const script = await readFile(operatorPath, "utf8");
  assert.match(script, /restore-\$ExpectedRehearsalId/);
  assert.match(script, /\[IO\.Path\]::GetFileName\(\$data\) -cne "data"/);
  assert.match(script, /@\(Get-PortListeners \$Port\)\.Count -ne 0/);
  assert.match(script, /daily-\[0-9\]\{8\}T\[0-9\]\{6\}Z-\[0-9a-f\]\{12\}/);
  assert.match(script, /minimumSuccessfulBackups/);
  assert.match(script, /ConfirmedPrune/);
  assert.match(script, /\.prune-\[0-9a-f\]\{32\}\\\.quarantine/);
  assert.doesNotMatch(script, /Remove-Item[^\n]*(?:\$RuntimeRoot|\$MaintenanceRequest\.RuntimeRoot)[^\n]*-Recurse/);
});

test("maintenance pure validation rejects schema drift under library mode", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), "teruisi-pg-maintenance-"));
  try {
    const parent = path.join(tempRoot, "backups");
    const child = path.join(parent, "daily-20260830T010203Z-012345abcdef");
    const escapedScript = operatorPath.replaceAll("'", "''");
    const escapedParent = parent.replaceAll("'", "''");
    const escapedChild = child.replaceAll("'", "''");
    const command = [
      "$env:TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY='1';",
      `. '${escapedScript}';`,
      `New-Item -ItemType Directory -Path '${escapedChild}' -Force | Out-Null;`,
      `$resolved=Resolve-MaintenanceDirectChildDirectory '${escapedChild}' '${escapedParent}' '^daily-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$' 'fixture';`,
      `if($resolved -ine [IO.Path]::GetFullPath('${escapedChild}')){exit 2};`,
      "$good=[pscustomobject][ordered]@{a=1;b=2};",
      "Assert-MaintenanceExactPropertySet $good @('a','b') 'fixture';",
      "try {Assert-MaintenanceExactPropertySet $good @('a') 'fixture'; exit 3} catch {};",
      "if(-not (Test-MaintenanceInteger ([int64]1))){exit 4};",
      "if(Test-MaintenanceInteger ([double]1)){exit 5};",
      "exit 0",
    ].join(" ");
    const result = spawnSync(powershell, [
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
      "-Command", command,
    ], { encoding: "utf8", windowsHide: true });
    assert.equal(result.status, 0, result.stderr || result.stdout);
  } finally {
    await rm(tempRoot, { recursive: true, force: true });
  }
});

test("Python helper imports with the controlled runtime", async (t) => {
  if (!existsSync(runtimePython)) {
    t.skip("controlled Django Python runtime is unavailable");
    return;
  }
  const result = spawnSync(runtimePython, [helperPath, "--help"], {
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /\{backup,probe,protected-preflight,restore\}/);
  assert.equal(result.stderr, "");
});

test("maintenance validates complete AI backup evidence before and after activation", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  const manifest = await readFile(path.join(root, "backend/ai_assistant/table_manifest.py"), "utf8");
  const frozenManifest = manifest.match(/AI_TABLES_PRE_TOOL_RECEIPTS = \(([\s\S]*?)\n\)/);
  assert.ok(frozenManifest, "Historical pre-0032 AI table inventory is missing");
  const historicalAiTables = [...frozenManifest[1].matchAll(/"(ai_[a-z_]+)"/g)].map(match => match[1]);
  assert.equal(new Set(historicalAiTables).size, 65);
  assert.match(manifest, /AI_TABLES_PRE_V3_REPORT_INTENTS = \(\*AI_TABLES_PRE_TOOL_RECEIPTS, "ai_business_source_tool_receipts"\)/);
  assert.match(manifest, /AI_TABLES_PRE_V4_LEDGER = \(\*AI_TABLES_PRE_V3_REPORT_INTENTS, "ai_business_v3_report_intents"\)/);
  assert.match(manifest, /AI_TABLES_PRE_V4_VALIDATION = \(\*AI_TABLES_PRE_V4_LEDGER,/);
  assert.match(manifest, /AI_TABLES_PRE_V4_SEALS = \(\*AI_TABLES_PRE_V4_VALIDATION,/);
  assert.match(manifest, /AI_TABLES_PRE_V4_TICKETS = \(\*AI_TABLES_PRE_V4_SEALS, "ai_business_v4_seals"\)/);
  assert.match(manifest, /AI_TABLES_PRE_V4_CONSUMPTIONS = \(\*AI_TABLES_PRE_V4_TICKETS,/);
  assert.match(manifest, /AI_TABLES = \(\*AI_TABLES_PRE_MARKET_V2_PAID_REHEARSAL,/);
  const aiTables = historicalAiTables.filter(name => !["ai_business_evidence_runs", "ai_business_evidence_chunks", "ai_business_file_runs", "ai_business_file_chunks", "ai_business_evidence_sources", "ai_business_volume_chunks", "ai_business_budget_plans", "ai_business_screening_runs", "ai_business_screening_pages"].includes(name));
  assert.ok(aiTables.includes("ai_conversation_workspaces"));
  const base = {
    database: { name: "fixture", user: "fixture", serverAddress: "127.0.0.1", serverPort: 55449, inRecovery: false, serverVersionNumber: 170011 },
    tables: Object.fromEntries(["django_migrations", "sales_data_revisions", "sales_import_batches", "sales_order_lines", "sales_write_authority", "erp_product_master"].map(name => [name, 0])),
    migrations: [{ app: "sales", name: "0001_initial" }],
    revisions: { sales: 1, erp: 1 },
    writeAuthority: { status: "active", authorityEpoch: "11111111-1111-1111-1111-111111111111", cutoverId: "fixture-sales" },
    contentSha256: "a".repeat(64), canonicalSha256: "b".repeat(64),
  };
  const candidate = {
    ...structuredClone(base),
    tables: { ...base.tables, ...Object.fromEntries(aiTables.map(name => [name, 0])) },
    migrations: [...base.migrations, { app: "ai_assistant", name: "0001_initial" }, { app: "ai_assistant", name: "0006_conversation_workspaces" }, { app: "ai_assistant", name: "0007_dingtalk_readonly" }, { app: "ai_assistant", name: "0008_dingtalk_settings" }, { app: "ai_assistant", name: "0009_model_generation_capabilities" }, { app: "ai_assistant", name: "0010_dingtalk_schedules" }, { app: "ai_assistant", name: "0011_prompt_settings" }, { app: "ai_assistant", name: "0012_report_library" }],
    aiAssistant: { revision: 0, sourceDigest: "", status: "d1", authorityEpoch: "", cutoverId: "", migrationRunId: "" },
  };
  const adopted = structuredClone(candidate);
  Object.assign(adopted.aiAssistant, { revision: 1, sourceDigest: "c".repeat(64), migrationRunId: `ai-apply-${"d".repeat(32)}` });
  const active = structuredClone(adopted);
  Object.assign(active.aiAssistant, { status: "postgres", authorityEpoch: "22222222-2222-2222-2222-222222222222", cutoverId: "fixture-ai" });
  const missing = structuredClone(active);
  delete missing.tables.ai_models;
  const unknown = structuredClone(active);
  unknown.tables.ai_unregistered = 0;
  const unbound = { ...structuredClone(base), tables: { ...base.tables, ai_models: 0 } };
  const metadataMissing = structuredClone(active) as Partial<typeof active>;
  delete metadataMissing.aiAssistant;
  const orphanWorkspaceMigration = { ...structuredClone(base), migrations: [...base.migrations, { app: "ai_assistant", name: "0006_conversation_workspaces" }] };
  const orphanSettingsMigration = { ...structuredClone(base), migrations: [...base.migrations, { app: "ai_assistant", name: "0008_dingtalk_settings" }] };
  const beforeWorkspaceMigration = structuredClone(active);
  const beforeSchedule = structuredClone(active);
  delete beforeSchedule.tables.ai_dingtalk_schedules;
  delete beforeSchedule.tables.ai_dingtalk_schedule_runs;
  beforeSchedule.migrations = beforeSchedule.migrations.filter(item => item.name !== "0010_dingtalk_schedules");
  const scheduleMissing = structuredClone(active);
  delete scheduleMissing.tables.ai_dingtalk_schedule_runs;
  for (const evidence of [beforeWorkspaceMigration]) {
    delete evidence.tables.ai_dingtalk_schedules;
    delete evidence.tables.ai_dingtalk_schedule_runs;
  }
  delete beforeWorkspaceMigration.tables.ai_dingtalk_settings;
  delete beforeWorkspaceMigration.tables.ai_conversation_workspaces;
  delete beforeWorkspaceMigration.tables.ai_dingtalk_sessions;
  delete beforeWorkspaceMigration.tables.ai_dingtalk_receipts;
  beforeWorkspaceMigration.migrations = beforeWorkspaceMigration.migrations.filter(item => !["0006_conversation_workspaces", "0007_dingtalk_readonly", "0008_dingtalk_settings", "0010_dingtalk_schedules"].includes(item.name));
  const beforeDingTalk = structuredClone(active);
  delete beforeDingTalk.tables.ai_dingtalk_schedules;
  delete beforeDingTalk.tables.ai_dingtalk_schedule_runs;
  delete beforeDingTalk.tables.ai_dingtalk_settings;
  delete beforeDingTalk.tables.ai_dingtalk_sessions;
  delete beforeDingTalk.tables.ai_dingtalk_receipts;
  beforeDingTalk.migrations = beforeDingTalk.migrations.filter(item => !["0007_dingtalk_readonly", "0008_dingtalk_settings", "0010_dingtalk_schedules"].includes(item.name));
  const beforeSettings = structuredClone(active);
  delete beforeSettings.tables.ai_dingtalk_schedules;
  delete beforeSettings.tables.ai_dingtalk_schedule_runs;
  delete beforeSettings.tables.ai_dingtalk_settings;
  beforeSettings.migrations = beforeSettings.migrations.filter(item => !["0008_dingtalk_settings", "0010_dingtalk_schedules"].includes(item.name));
  const settingsMissing = structuredClone(active);
  delete settingsMissing.tables.ai_dingtalk_settings;
  const dingTalkMissing = structuredClone(active);
  delete dingTalkMissing.tables.ai_dingtalk_receipts;
  const dingTalkUnbound = structuredClone(active);
  dingTalkUnbound.migrations = dingTalkUnbound.migrations.filter(item => item.name !== "0007_dingtalk_readonly");
  const workspaceMissing = structuredClone(active);
  delete workspaceMissing.tables.ai_conversation_workspaces;
  const workspaceMigrationMissing = structuredClone(active);
  workspaceMigrationMissing.migrations = workspaceMigrationMissing.migrations.filter(item => item.name !== "0006_conversation_workspaces");
  const beforePrompt = structuredClone(active);
  for (const evidence of [beforePrompt, beforeSchedule, beforeWorkspaceMigration, beforeDingTalk, beforeSettings]) {
    for (const name of ["ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries"]) delete evidence.tables[name];
    evidence.migrations = evidence.migrations.filter(item => item.name !== "0012_report_library");
    delete evidence.tables.ai_prompt_settings_revisions;
    evidence.migrations = evidence.migrations.filter(item => item.name !== "0011_prompt_settings");
  }
  const promptMissing = structuredClone(active);
  delete promptMissing.tables.ai_prompt_settings_revisions;
  const promptUnbound = structuredClone(active);
  promptUnbound.migrations = promptUnbound.migrations.filter(item => item.name !== "0011_prompt_settings");
  const media = structuredClone(active);
  media.migrations.push({ app: "ai_assistant", name: "0013_dingtalk_schedule_media" });
  const mediaWithoutReport = structuredClone(media);
  mediaWithoutReport.migrations = mediaWithoutReport.migrations.filter(item => item.name !== "0012_report_library");
  const business = structuredClone(media);
  business.migrations.push({ app: "ai_assistant", name: "0014_business_evidence" });
  business.tables.ai_business_evidence_runs = 0;
  business.tables.ai_business_evidence_chunks = 0;
  const files = structuredClone(business);
  files.migrations.push({ app: "ai_assistant", name: "0015_business_collection" }, { app: "ai_assistant", name: "0016_business_files" });
  files.tables.ai_business_file_runs = 0;
  files.tables.ai_business_file_chunks = 0;
  const directory = structuredClone(files);
  directory.migrations.push({ app: "ai_assistant", name: "0017_business_file_renderer" }, { app: "ai_assistant", name: "0018_business_excel_renderer" }, { app: "ai_assistant", name: "0019_business_source_directory" });
  directory.tables.ai_business_evidence_sources = 0;
  assert.equal(Object.keys(directory.tables).filter(name => name.startsWith("ai_")).length, 61);
  const volumes = structuredClone(directory);
  volumes.migrations.push({ app: "ai_assistant", name: "0020_business_volume_files" });
  volumes.tables.ai_business_volume_chunks = 0;
  assert.equal(Object.keys(volumes.tables).filter(name => name.startsWith("ai_")).length, 62);
  const budgets = structuredClone(volumes);
  budgets.migrations.push({ app: "ai_assistant", name: "0021_business_budget_plans" });
  budgets.tables.ai_business_budget_plans = 0;
  assert.equal(Object.keys(budgets.tables).filter(name => name.startsWith("ai_")).length, 63);
  const screenings = structuredClone(budgets);
  screenings.migrations.push({ app: "ai_assistant", name: "0022_business_integrated_reports" }, { app: "ai_assistant", name: "0023_business_screening_storage" });
  screenings.tables.ai_business_screening_runs = 0;
  screenings.tables.ai_business_screening_pages = 0;
  assert.equal(Object.keys(screenings.tables).filter(name => name.startsWith("ai_")).length, 65);
  const screeningRuntime = structuredClone(screenings);
  screeningRuntime.migrations.push({ app: "ai_assistant", name: "0024_business_screening_runtime" });
  const runtimeWithoutStorage = structuredClone(screeningRuntime);
  runtimeWithoutStorage.migrations = runtimeWithoutStorage.migrations.filter(item => item.name !== "0023_business_screening_storage");
  delete runtimeWithoutStorage.tables.ai_business_screening_runs;
  delete runtimeWithoutStorage.tables.ai_business_screening_pages;
  const screeningsInvalid = ["ai_business_screening_runs", "ai_business_screening_pages"].map(name => {
    const evidence = structuredClone(screenings); delete evidence.tables[name]; return evidence;
  });
  for (const name of ["0021_business_budget_plans", "0022_business_integrated_reports", "0023_business_screening_storage"]) {
    const evidence = structuredClone(screenings); evidence.migrations = evidence.migrations.filter(item => item.name !== name); screeningsInvalid.push(evidence);
  }
  const budgetsMissing = structuredClone(budgets);
  delete budgetsMissing.tables.ai_business_budget_plans;
  const budgetsUnbound = structuredClone(budgets);
  budgetsUnbound.migrations = budgetsUnbound.migrations.filter(item => item.name !== "0021_business_budget_plans");
  const budgetPredecessorsMissing = ["0014_business_evidence", "0015_business_collection", "0016_business_files", "0017_business_file_renderer", "0018_business_excel_renderer", "0019_business_source_directory", "0020_business_volume_files"].map(name => {
    const evidence = structuredClone(budgets);
    evidence.migrations = evidence.migrations.filter(item => item.name !== name);
    return evidence;
  });
  const volumesMissing = structuredClone(volumes);
  delete volumesMissing.tables.ai_business_volume_chunks;
  const volumesUnbound = structuredClone(volumes);
  volumesUnbound.migrations = volumesUnbound.migrations.filter(item => item.name !== "0020_business_volume_files");
  const volumePredecessorsMissing = ["0014_business_evidence", "0015_business_collection", "0016_business_files", "0017_business_file_renderer", "0018_business_excel_renderer", "0019_business_source_directory"].map(name => {
    const evidence = structuredClone(volumes);
    evidence.migrations = evidence.migrations.filter(item => item.name !== name);
    return evidence;
  });
  const directoryMissing = structuredClone(directory);
  delete directoryMissing.tables.ai_business_evidence_sources;
  const directoryUnbound = structuredClone(directory);
  directoryUnbound.migrations = directoryUnbound.migrations.filter(item => item.name !== "0019_business_source_directory");
  const directoryPredecessorsMissing = ["0014_business_evidence", "0015_business_collection", "0016_business_files", "0017_business_file_renderer", "0018_business_excel_renderer"].map(name => {
    const evidence = structuredClone(directory);
    evidence.migrations = evidence.migrations.filter(item => item.name !== name);
    return evidence;
  });
  const filesMissing = structuredClone(files);
  delete filesMissing.tables.ai_business_file_chunks;
  const filesPredecessorMissing = structuredClone(files);
  filesPredecessorMissing.migrations = filesPredecessorMissing.migrations.filter(item => item.name !== "0015_business_collection");
  const businessMissingChunk = structuredClone(business);
  delete businessMissingChunk.tables.ai_business_evidence_chunks;
  const businessMissingPredecessor = structuredClone(business);
  businessMissingPredecessor.migrations = businessMissingPredecessor.migrations.filter(item => item.name !== "0013_dingtalk_schedule_media");
  const cases = [
    ...[base, beforePrompt, candidate, adopted, active, media, business, files, directory, volumes, budgets, screenings, screeningRuntime, beforeSchedule, beforeWorkspaceMigration, beforeDingTalk, beforeSettings].map(evidence => ({ valid: true, evidence })),
    { valid: false, evidence: runtimeWithoutStorage },
    ...screeningsInvalid.map(evidence => ({ valid: false, evidence })),
    ...[budgetsMissing, budgetsUnbound, ...budgetPredecessorsMissing].map(evidence => ({ valid: false, evidence })),
    ...[volumesMissing, volumesUnbound, ...volumePredecessorsMissing].map(evidence => ({ valid: false, evidence })),
    ...[businessMissingChunk, businessMissingPredecessor, filesMissing, filesPredecessorMissing, directoryMissing, directoryUnbound, ...directoryPredecessorsMissing].map(evidence => ({ valid: false, evidence })),
    ...[promptMissing, promptUnbound, mediaWithoutReport, scheduleMissing, orphanSettingsMigration, settingsMissing, missing, unknown, unbound, metadataMissing, workspaceMissing, workspaceMigrationMissing, orphanWorkspaceMigration, dingTalkMissing, dingTalkUnbound].map(evidence => ({ valid: false, evidence })),
  ];
  const encoded = Buffer.from(JSON.stringify(cases)).toString("base64");
  const command = `
$ErrorActionPreference='Stop'
$env:TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY='1'
$operator=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${Buffer.from(operatorPath).toString("base64")}'))
. $operator
$cases=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}')) | ConvertFrom-Json
foreach($case in $cases) {
  $accepted=$false
  try { Assert-MaintenanceEvidence $case.evidence 'fixture' 'fixture' 55449; $accepted=$true } catch { if($case.valid){throw} }
  if($accepted -ne $case.valid){throw 'AI evidence boundary failed'}
}
Write-Output '${cases.length} AI backup evidence cases passed'
`;
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), "teruisi-ai-backup-contract-"));
  try {
    const scriptPath = path.join(tempRoot, "validate.ps1");
    await writeFile(scriptPath, command);
    const result = spawnSync(powershell, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", scriptPath], { encoding: "utf8", windowsHide: true, timeout: 30000 });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.match(result.stdout, new RegExp(`${cases.length} AI backup evidence cases passed`));
  } finally {
    await rm(tempRoot, { recursive: true, force: true });
  }
});

test("Python helper snapshot and restore behavior passes isolated unit fixtures", async (t) => {
  if (!existsSync(runtimePython)) {
    t.skip("controlled Django Python runtime is unavailable");
    return;
  }
  const fixture = path.join(root, "tests", "postgres-consistent-backup.test.py");
  const result = spawnSync(runtimePython, [fixture], {
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const count = result.stderr.match(/Ran (\d+) tests/);
  assert.ok(count && Number(count[1]) >= 13, result.stderr);
  assert.match(result.stderr, /OK/);
});
