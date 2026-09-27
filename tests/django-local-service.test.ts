import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";

const script = readFileSync("tools/django-local-service.ps1", "utf8");
test("Django lifecycle treats legacy D1 paths as metadata and supports PostgreSQL-only configuration", { skip: process.platform !== "win32" }, () => {
  const result = spawnSync("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests/django-local-service-d1-metadata.test.ps1"], {
    encoding: "utf8", windowsHide: true, timeout: 30_000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});
const health = readFileSync("backend/teruisi_backend/health.py", "utf8");
const retirementOperator = readFileSync("tools/sales-d1-retirement.ts", "utf8");
const deploymentRuntimeTest = readFileSync(
  "tests/django-local-service-deploy-runtime.test.ps1",
  "utf8",
);
const aclFallbackRuntimeTest = readFileSync(
  "tests/django-local-service-acl-fallback.test.ps1",
  "utf8",
);
const fingerprintRuntimeTest = readFileSync(
  "tests/django-local-service-fingerprint.test.ps1",
  "utf8",
);
const nativePs5RuntimeTest = readFileSync(
  "tests/django-local-service-native-ps5.test.ps1",
  "utf8",
);
const financeCutover = readFileSync("tools/django-finance-cutover.ps1", "utf8");
const erpReferenceService = readFileSync("tools/django-erp-reference.ps1", "utf8");
const orchestratedDomainServices = [
  ["netshop", readFileSync("tools/django-netshop-service.ps1", "utf8")],
  ["market", readFileSync("tools/django-market-service.ps1", "utf8")],
  ["products", readFileSync("tools/django-products-service.ps1", "utf8")],
  ["inventory", readFileSync("tools/django-inventory-service.ps1", "utf8")],
  ["workflow", readFileSync("tools/django-workflow-service.ps1", "utf8")],
] as const;

test("Django local service binds PostgreSQL and Waitress to loopback with bounded requests", () => {
  assert.match(script, /127\.0\.0\.1:5432\/\$\{escapedDatabase\}/);
  assert.match(script, /\^teruisi_sales\(\?:_rehearsal_/);
  assert.match(script, /--listen=127\.0\.0\.1:8001/);
  assert.match(script, /--listen=127\.0\.0\.1:8002/);
  assert.match(script, /--max-request-header-size=\$MaxHeaderBytes/);
  assert.match(script, /--max-request-body-size=\$ReaderMaxBodyBytes/);
  assert.match(script, /--max-request-body-size=\$WriterMaxBodyBytes/);
  assert.match(script, /\$WriterMaxBodyBytes = 16777216/);
  assert.match(script, /\$WriterStatementTimeoutMs = 900000/);
  assert.match(script, /\$MinimumPostgresConnectionsForFullStack = 120/);
  assert.match(script, /function Assert-PostgresConnectionCapacity/);
  assert.match(script, /cursor\.execute\("SHOW max_connections"\)/);
  assert.match(script, /\$requiredConnections = if \(Test-Path -LiteralPath \$AiStartupEnabledPath -PathType Leaf\) \{ 128 \}/);
  assert.match(script, /--channel-timeout=960/);
  assert.doesNotMatch(script, /--channel-timeout=120/);
  assert.doesNotMatch(script, /--listen=0\.0\.0\.0|listen_addresses\s*=\s*['"]\*/);
});

test("full-stack startup checks PostgreSQL capacity before schema work and process launch", () => {
  const startBlock = script.slice(
    script.indexOf("function Start-ServiceStack"),
    script.indexOf("function Stop-ServiceStack"),
  );
  assert.ok(startBlock.indexOf("Assert-PostgresConnectionCapacity") > -1);
  assert.ok(
    startBlock.indexOf("Assert-PostgresConnectionCapacity") <
      startBlock.indexOf("Invoke-DjangoMigrations"),
  );
  assert.ok(
    startBlock.indexOf("Assert-PostgresConnectionCapacity") <
      startBlock.indexOf("Start-DjangoReader"),
  );
});

test("protected AI migrations are rejected before formal preparation and before migrate", () => {
  const gate = script.slice(
    script.indexOf("function Assert-NoUnapprovedProtectedAiMigration"),
    script.indexOf("function Prepare-Application"),
  );
  assert.match(gate, /0070_business_promotion_budget_v11_limited_identity\.py/);
  assert.match(gate, /0072_business_market_v2_authority_proposals\.py/);
  assert.match(gate, /0073_business_promotion_budget_v11_login_attestation\.py/);
  assert.match(gate, /finance\\migrations\\0005_raw_column_evidence_v2\.py/);
  assert.match(gate, /D:\\teruisi-runtime\\django-sales/);

  const prepare = script.slice(
    script.indexOf("function Prepare-Application"),
    script.indexOf("function Deploy-Application"),
  );
  assert.ok(prepare.indexOf('Assert-NoUnapprovedProtectedAiMigration "PrepareApp"') > -1);
  assert.ok(
    prepare.indexOf('Assert-NoUnapprovedProtectedAiMigration "PrepareApp"') <
      prepare.indexOf("New-Item -ItemType Directory -Path $RuntimeRoot"),
  );

  const deploy = script.slice(
    script.indexOf("function Deploy-Application"),
    script.indexOf("function Rollback-Application"),
  );
  assert.ok(deploy.includes('Assert-NoUnapprovedProtectedAiMigration "DeployApp" (Join-Path $staging "backend")'));
  assert.ok(
    deploy.indexOf('Assert-NoUnapprovedProtectedAiMigration "DeployApp"') <
      deploy.indexOf('Move-Item -LiteralPath $staging -Destination $InstalledAppRoot'),
  );

  const migration = script.slice(
    script.indexOf("function Invoke-DjangoMigrations"),
    script.indexOf("function ", script.indexOf("function Invoke-DjangoMigrations") + 1),
  );
  assert.ok(migration.indexOf('Assert-NoUnapprovedProtectedAiMigration "Django migrate"') > -1);
  assert.ok(
    migration.indexOf('Assert-NoUnapprovedProtectedAiMigration "Django migrate"') <
      migration.indexOf('"migrate", "--noinput"'),
  );
});

test("Django local status keeps ACL verification bounded and labels its root-only scope", () => {
  const statusBlock = script.match(
    /function Show-ServiceStatus \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Show-FinanceServiceStatus/,
  )?.[1];
  assert.ok(statusBlock, "Show-ServiceStatus block must remain discoverable");
  assert.match(statusBlock, /Assert-RuntimeRootAclHardened/);
  assert.doesNotMatch(statusBlock, /Assert-RuntimeAclHardened/);
  assert.match(statusBlock, /RuntimeAclVerification = "root_only_status"/);
  assert.match(statusBlock, /\$acl = "root_hardened"/);
});

test("one top-level lifecycle ACL audit is reused only by its bounded in-process domain chain", () => {
  assert.match(script, /TERUISI_DJANGO_ORCHESTRATED_LIFECYCLE_ACL_CONTEXT/);
  assert.match(script, /teruisi-django-orchestrated-lifecycle-acl-v1/);
  assert.match(script, /OrchestratedLifecycleAclContextMaxAgeMilliseconds = 900000/);
  assert.match(script, /processId = \[int\]\$PID/);
  assert.match(script, /runtimeRootPathSha256 = Get-Sha256Text/);
  assert.match(script, /deploymentManifestSha256 = Get-FileSha256 \$DeploymentManifestPath/);
  assert.match(script, /Assert-RuntimeRootAclHardened/);

  const startBlock = script.slice(
    script.indexOf('"Start" {'),
    script.indexOf('"Stop" {'),
  );
  assert.ok(startBlock.indexOf("Start-ServiceStack") < startBlock.indexOf("Set-OrchestratedLifecycleAclContext"));
  assert.ok(startBlock.indexOf("Set-OrchestratedLifecycleAclContext") < startBlock.indexOf("Invoke-EnabledDjangoDomainStarts"));
  assert.match(startBlock, /finally \{[\s\S]*?Set-Variable -Scope Global[\s\S]*?\$previousAclContext/);

  const stopBlock = script.slice(
    script.indexOf('"Stop" {'),
    script.indexOf('"Status" {'),
  );
  assert.ok(stopBlock.indexOf("Assert-DeployedApplication") < stopBlock.indexOf("Assert-RuntimeAclHardened"));
  assert.ok(stopBlock.indexOf("Assert-RuntimeAclHardened") < stopBlock.indexOf("Set-OrchestratedLifecycleAclContext"));
  assert.ok(stopBlock.indexOf("Set-OrchestratedLifecycleAclContext") < stopBlock.indexOf("Invoke-InstalledDjangoDomainStops"));
  assert.ok(stopBlock.indexOf("Invoke-InstalledDjangoDomainStops") < stopBlock.indexOf("Stop-ServiceStack"));
  assert.match(stopBlock, /finally \{[\s\S]*?Set-Variable -Scope Global[\s\S]*?\$previousAclContext/);

  for (const [domain, domainScript] of orchestratedDomainServices) {
    assert.match(domainScript, /\[string\]\$OrchestratedLifecycleAclToken = ""/);
    assert.match(domainScript, /Test-OrchestratedLifecycleAclContext \$LifecycleAclToken[\s\S]*?return[\s\S]*?Assert-DeployedApplication[\s\S]*?Assert-RuntimeAclHardened/);
    assert.match(domainScript, new RegExp(`orchestrated_lifecycle_acl_reused" "domain=${domain}"`));
    assert.match(domainScript, /"Start" \{ Invoke-WithServiceMutex \{ Start-[A-Za-z]+Stack \$OrchestratedLifecycleAclToken \} \}/);
    assert.match(domainScript, /"Stop" \{ Invoke-WithServiceMutex \{ Stop-[A-Za-z]+Stack \$OrchestratedLifecycleAclToken \} \}/);
  }
});

test("Django local service uses deterministic production secrets and least-privilege roles", () => {
  assert.match(script, /ConvertTo-SecureString \$ProtectedValue/);
  assert.match(script, /databaseWriter/);
  assert.match(script, /teruisi_sales_writer/);
  assert.doesNotMatch(script, /Unprotect-Value \(\[string\]\$payload\.databaseErpSync\)/);
  assert.doesNotMatch(script, /ErpSyncPassword/);
  assert.doesNotMatch(script, /CREATE ROLE teruisi_erp_reference_sync/);
  assert.doesNotMatch(script, /ALTER ROLE teruisi_erp_reference_sync NOLOGIN/);
  assert.match(erpReferenceService, /ALTER ROLE teruisi_erp_reference_sync NOLOGIN/);
  assert.match(script, /NOBYPASSRLS/);
  assert.match(script, /REVOKE ALL PRIVILEGES ON ALL TABLES/);
  assert.match(script, /REVOKE ALL PRIVILEGES \(\{names\}\)/);
  assert.match(script, /ENABLE ROW LEVEL SECURITY/);
  assert.match(script, /domain = 'sales'/);
  assert.match(script, /domain IN \('sales', 'erp'\)/);
  assert.doesNotMatch(script, /GRANT UPDATE \(resolved_category\).*teruisi_erp_reference_sync/);
  assert.match(
    script,
    /GRANT SELECT ON sales_write_authority, sales_cutover_attestations, erp_product_master, erp_combo_items, erp_reference_import_batches_pg, erp_reference_import_scope_heads, erp_reference_write_authority TO teruisi_sales_writer/,
  );
  const writerGrants = [...script.matchAll(/c\.execute\("(GRANT [^"]+ TO teruisi_sales_writer)"\)/g)]
    .map((match) => match[1])
    .join("\n");
  assert.doesNotMatch(
    writerGrants,
    /GRANT .*\b(?:INSERT|UPDATE|DELETE|TRUNCATE)\b.*\b(?:sales_write_authority|erp_product_master|erp_combo_items|erp_reference_import_batches_pg|erp_reference_import_scope_heads|erp_reference_write_authority|sales_cutover_attestations|sales_legacy_upload_audits)\b/,
  );
  assert.match(script, /"sales_write_authority",\s*"erp_product_master"/);
  assert.match(script, /has_any_column_privilege/);
  assert.match(health, /"sales_write_authority": \("INSERT", "UPDATE", "DELETE", "TRUNCATE"\)/);
  assert.match(health, /has_any_column_privilege/);
  assert.match(
    script,
    /CREATE POLICY sales_revision_writer_read .* FOR SELECT TO teruisi_sales_writer USING \(domain IN \('sales', 'erp'\)\)/,
  );
  assert.match(
    script,
    /CREATE POLICY sales_revision_writer_insert .* FOR INSERT TO teruisi_sales_writer WITH CHECK \(domain = 'sales'\)/,
  );
  assert.match(
    script,
    /CREATE POLICY sales_revision_writer_update .* FOR UPDATE TO teruisi_sales_writer USING \(domain = 'sales'\) WITH CHECK \(domain = 'sales'\)/,
  );
  assert.doesNotMatch(
    script,
    /CREATE POLICY sales_revision_writer .* FOR ALL TO teruisi_sales_writer/,
  );
  assert.match(script, /sales writer unexpectedly has \{privilege\} on \{table\}/);
  assert.match(script, /"sales_cutover_attestations"/);
  assert.match(script, /"sales_legacy_upload_audits"/);
  assert.match(script, /sales writer revision RLS policies are not least-privilege/);
  assert.doesNotMatch(script, /GRANT SELECT ON ALL TABLES/);
  assert.doesNotMatch(script, /GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES/);
  assert.match(script, /Database-Url "teruisi_sales_owner" \$Secrets\.OwnerPassword "teruisi_django_migrate"/);
  assert.match(script, /TERUISI_DJANGO_ENVIRONMENT = "production"/);
  assert.match(script, /TERUISI_DJANGO_PROCESS_ROLE = \$ProcessRole/);
  assert.match(script, /TERUISI_DJANGO_SALES_AUTHORITY_EPOCH = \$AuthorityEpoch/);
  assert.match(script, /TERUISI_DJANGO_SALES_CUTOVER_ID = \$CutoverId/);
  assert.match(script, /statement_timeout=\$StatementTimeoutMilliseconds/);
  assert.match(script, /\$ReaderStatementTimeoutMs = 7000/);
  assert.doesNotMatch(script, /--(?:password|secret|database-url)/i);
});

test("writer readiness fails closed on divergent terminal ERP PostgreSQL authority", () => {
  for (const table of [
    "erp_product_master",
    "erp_combo_items",
    "erp_reference_import_batches_pg",
    "erp_reference_import_scope_heads",
    "erp_reference_write_authority",
  ]) assert.match(health, new RegExp(`"${table}": \\("SELECT",\\)`));
  assert.match(health, /validate_erp_reference_runtime_state/);
  assert.match(health, /WRITER_FORBIDDEN_PROTECTED_TABLE_PRIVILEGES/);
  assert.match(
    health,
    /"sales_raw_upload_chunks": \{[\s\S]*?"payload"/,
  );
  assert.match(
    health,
    /if writer_process:[\s\S]*?_validate_writer_permissions\(cursor\)[\s\S]*?_validate_reader_state\(cursor\)/,
  );
});

test("managed PID ownership survives venv launchers without weakening identity checks", () => {
  assert.match(script, /creationDate = \$snapshot\.CreationDate/);
  assert.match(script, /ConvertTo-CanonicalCreationDate \$record\.creationDate/);
  assert.match(script, /launcherPath = Get-CanonicalPath \$Executable/);
  assert.match(script, /executablePath = \$snapshot\.ExecutablePath/);
  assert.match(script, /\$snapshot\.CommandLine -ceq \[string\]\$record\.commandLine/);
  assert.match(script, /\[string\]\$record\.service -cne \$Service/);
  assert.match(script, /Test-ExactStringArray \$recordArguments \$ExpectedArguments/);
  assert.match(script, /Test-CommandLineReferencesPath \$snapshot\.CommandLine \$ExpectedLauncher/);
  assert.match(script, /Stop-VerifiedProcessTree \$process/);
  assert.match(script, /Test-ProcessSnapshotIdentity \$currentRoot \$RootSnapshot/);
  assert.match(script, /Queue\[object\]/);
  assert.match(script, /Test-ProcessSnapshotIdentity \$currentParent \$parentSnapshot/);
  assert.match(script, /Get-ProcessCreation \$child/);
  assert.match(script, /root_pid_reused/);
  assert.match(script, /function Get-SystemBootTimeUtc/);
  assert.match(script, /recordFile\.LastWriteTimeUtc -lt \$bootTimeUtc/);
  assert.match(script, /previous_boot_process_record_removed/);
  assert.match(
    script,
    /Remove-PreviousBootProcessRecordIfSafe \$PidPath \$record\.creationDate \$record\.startedAt/,
  );
  assert.doesNotMatch(script, /\$snapshot\.ExecutablePath -ieq \(Get-CanonicalPath \$ExpectedLauncher\)/);
});

test("PostgreSQL reuse proves listener executable and exact data directory before credentials", () => {
  assert.match(script, /Get-NetTCPConnection -State Listen -ErrorAction Stop/);
  assert.match(script, /LocalPort -eq \$Port/);
  assert.match(script, /Assert-PostgresListenerOwnership/);
  assert.match(script, /\[regex\]::Matches\(\$snapshot\.CommandLine, '[^']*-D/);
  assert.match(script, /Get-CanonicalPath \$PostgresData/);
  assert.match(script, /Invoke-PgCtl/);
  assert.match(script, /WaitForExit\(\$TimeoutSeconds \* 1000\)/);
  assert.doesNotMatch(script, /& \$pgCtl start/);
});

test("startup is mutexed, authority gated, bridge-rejecting, rollback-safe, and logged", () => {
  assert.match(script, /\[Threading\.Mutex\]::new/);
  assert.match(script, /Invoke-DjangoMigrations/);
  assert.match(script, /Get-ActiveWriteAuthority/);
  assert.doesNotMatch(script, /function (?:Invoke|Start|Wait|Read)-ErpReference/);
  assert.doesNotMatch(script, /sync_erp_reference(?:\s|"|')/);
  assert.match(script, /Get-ErpReferenceSyncCandidates/);
  assert.match(script, /旧 ERP reference sync 必须保持停止/);
  assert.match(script, /InstalledErpReferenceScriptPath -Action Start/);
  assert.match(script, /\$Psql = Join-Path \$PostgresBin "psql\.exe"/);
  assert.match(script, /django_migrations_applied/);
  assert.match(script, /if \(\$writerStarted\) \{\s+try \{ Stop-OwnedProcess/);
  assert.match(script, /if \(\$readerStarted\) \{\s+try \{ Stop-OwnedProcess/);
  assert.match(script, /if \(\$postgresStarted\) \{\s+try \{ Stop-Postgres/);
  assert.match(script, /launcher\.jsonl/);
  assert.match(script, /Remove-OldServiceLogs/);
  assert.doesNotMatch(script, /ProjectionSync|sync_sales_projection|projection_caught_up/);
});

test("critical Django native calls are PS5-safe and report only bounded redacted evidence", () => {
  assert.match(script, /function Invoke-BoundedNativeProcess/);
  assert.match(script, /\$outerErrorActionPreference = \$ErrorActionPreference/);
  assert.match(script, /\$ErrorActionPreference = "Continue"/);
  assert.match(script, /\$global:LASTEXITCODE = \$outerLastExitCode/);
  assert.match(script, /function Get-BoundedNativeDiagnostic/);
  assert.match(script, /function ConvertFrom-UniqueNativeJson/);
  assert.match(script, /\$maxRecords = 32/);
  assert.match(script, /\$maxCharacters = 4096/);
  assert.match(script, /outputSha256=\$\(\[string\]\$Run\.Diagnostic\.OutputSha256\)/);
  assert.match(script, /ConvertTo-PythonBase64Launcher \$grantCode/);
  assert.match(script, /Invoke-BoundedNativeProcess \$Node \$Arguments/);
  assert.match(script, /Invoke-BoundedNativeProcess \$Python @\(\s*\$manage, "sales_write_authority"/);
  assert.match(script, /sales_cutover_smoke_receipt[\s\S]*?ConvertFrom-UniqueNativeJson \$nativeRun/);
  assert.doesNotMatch(script, /@\(& \$(?:Python|Node)\b/);
  assert.doesNotMatch(script, /& \$(?:pgCtl|pgIsReady)\b/);
  assert.doesNotMatch(script, /"-c" \$(?:code|grantCode)\b/);
  assert.doesNotMatch(script, /\.Output\[-1\] \| ConvertFrom-Json/);
  assert.doesNotMatch(script, /\$Operation 失败：\$message/);
  assert.match(nativePs5RuntimeTest, /NativeCommandError/);
  assert.match(nativePs5RuntimeTest, /do-not-print/);
  assert.match(nativePs5RuntimeTest, /lastExitCodeRestored/);
  assert.match(nativePs5RuntimeTest, /launcherAscii/);
  assert.match(nativePs5RuntimeTest, /stderr emitted after stdout JSON/);
  assert.match(nativePs5RuntimeTest, /duplicate JSON probe/);
});

test("configuration retains the exact ERP D1 only for controlled cutover and exposes the terminal ERP service", () => {
  assert.match(script, /version = 6/);
  assert.match(script, /readerAddress = "127\.0\.0\.1:8001"/);
  assert.match(script, /writerAddress = "127\.0\.0\.1:8002"/);
  assert.match(script, /customerServiceReaderAddress = "127\.0\.0\.1:8071"/);
  assert.match(script, /customerServiceWriterAddress = "127\.0\.0\.1:8072"/);
  assert.match(script, /erpSourceD1 = \$resolvedErpSource/);
  assert.match(script, /TERUISI_DJANGO_PROCESS_ROLE = \$ProcessRole/);
  assert.match(script, /DjangoReader = \$reader/);
  assert.match(script, /DjangoWriter = \$writer/);
  assert.match(script, /ErpReference = \$erpReference/);
  assert.match(script, /django-erp-reference-reader\.pid\.json/);
  assert.match(script, /django-erp-reference-writer\.pid\.json/);
  assert.doesNotMatch(script, /function Initialize-ErpReferenceCheckpoint/);
  assert.doesNotMatch(script, /function Provision-ErpDatabaseRole/);
  assert.doesNotMatch(script, /sales_projection_source|sales_projection_outbox/);
});

test("finance runtime uses independent loopback processes, credentials, permissions, and cutover gates", () => {
  assert.match(script, /--listen=127\.0\.0\.1:8011/);
  assert.match(script, /--listen=127\.0\.0\.1:8012/);
  assert.match(script, /databaseFinanceReader/);
  assert.match(script, /databaseFinanceWriter/);
  assert.match(script, /Database-Url "teruisi_finance_reader" \$Secrets\.FinanceReaderPassword/);
  assert.match(script, /Database-Url "teruisi_finance_writer" \$Secrets\.FinanceWriterPassword/);
  assert.match(script, /TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH = \$AuthorityEpoch/);
  assert.match(script, /TERUISI_DJANGO_FINANCE_CUTOVER_ID = \$CutoverId/);
  assert.match(script, /ALTER ROLE teruisi_finance_reader SET default_transaction_read_only = on/);
  assert.match(script, /GRANT SELECT ON finance_import_batches, finance_months, finance_lines/);
  assert.match(script, /GRANT SELECT ON finance_write_authority TO teruisi_finance_writer/);
  assert.match(script, /Start-DjangoFinanceWriter \$secrets \$financeAuthority/);
  assert.match(script, /\$salesCoreReady = \$true[\s\S]*?Start-DjangoFinanceReader/);
  assert.match(script, /if \(\$salesCoreReady\) \{[\s\S]*?finance_domain_start_failed_sales_preserved[\s\S]*?throw \$originalError/);
  assert.match(script, /Stop-OwnedProcess "django-finance-writer"/);
  assert.match(script, /Stop-OwnedProcess "django-finance-reader"/);
  assert.doesNotMatch(script, /Database-Url "teruisi_sales_(?:reader|writer)" \$Secrets\.Finance/);

  assert.match(financeCutover, /Assert-InstalledFinanceOperator/);
  assert.match(financeCutover, /Resolve-FinanceSnapshot/);
  assert.match(financeCutover, /Resolve-FinanceSnapshotManifest/);
  assert.match(financeCutover, /migrate_finance_from_d1/);
  assert.match(financeCutover, /--source-manifest/);
  assert.match(financeCutover, /sourcePathSha256/);
  assert.match(financeCutover, /outputSha256 -cne \(Get-FileSha256 \$Source\)/);
  assert.match(financeCutover, /--approved-run-id/);
  assert.match(financeCutover, /finance-d1-authority-install\.py/);
  assert.match(financeCutover, /Assert-FinanceWriterStopped/);
  assert.match(financeCutover, /finance_write_authority/);
  assert.match(financeCutover, /Invoke-WithServiceMutex/);
  assert.match(financeCutover, /\[void\]\(Assert-RuntimeChildPath \$canonical\)/);
  assert.match(financeCutover, /\$commandArguments = @\(\$manage\) \+ @\(\$Arguments\)/);
  assert.match(financeCutover, /\$operationLabel = \[string\]\$Operation/);
  assert.match(financeCutover, /Write-NativeDiagnosticLog \$diagnosticLogPath \$operationLabel/);
  assert.doesNotMatch(financeCutover, /Write-NativeDiagnosticLog \$logPath \$Operation/);
  assert.doesNotMatch(financeCutover, /GetNewClosure/);
  assert.doesNotMatch(financeCutover, /(?:password|secret)[=:]/i);
});

test("runtime deployment includes every finance cutover dependency", () => {
  assert.match(script, /tools\\django-finance-cutover\.ps1/);
  assert.match(script, /tools\\finance-d1-authority-install\.py/);
  assert.match(script, /tools\\finance_d1_rehearsal_snapshot\.py/);
  assert.match(script, /drizzle\\0093_finance_write_authority\.sql/);
});

test("runtime deployment includes the protected PostgreSQL backup operator", () => {
  assert.match(script, /tools\\django-postgres-maintenance\.ps1/);
  assert.match(script, /tools\\postgres-consistent-backup\.py/);
});

test("runtime deployment includes the persistent Django supervisor", () => {
  assert.match(script, /tools\\django-runtime-supervisor\.ps1/);
});

test("runtime deployment includes the exact DingTalk replenishment mapping", () => {
  assert.match(script, /DingTalkReplenishmentConfigSource/);
  assert.match(script, /config\\dingtalk-replenishment\.json/);
  assert.match(
    script,
    /Copy-Item -LiteralPath \$DingTalkReplenishmentConfigSource[\s\S]*?-Destination \(Join-Path \$staging "config\\dingtalk-replenishment\.json"\)/,
  );
});

test("runtime deployment includes the exact inventory warehouse mapping", () => {
  assert.match(script, /InventoryWarehouseMappingConfigSource/);
  assert.match(script, /config\\inventory-warehouse-mapping\.json/);
  assert.match(
    script,
    /Copy-Item -LiteralPath \$InventoryWarehouseMappingConfigSource[\s\S]*?-Destination \(Join-Path \$staging "config\\inventory-warehouse-mapping\.json"\)/,
  );
});

test("configuration requires a stopped database and app deployment follows the persistent maintenance scope", () => {
  assert.match(script, /function Assert-ServiceStackStopped/);
  assert.match(script, /Assert-ServiceStackStopped "Configure"/);
  assert.match(script, /Assert-ApplicationDeploymentStopped "DeployApp"/);
  assert.match(script, /Assert-ApplicationDeploymentStopped "RollbackApp"/);
  assert.match(script, /function Assert-ApplicationDeploymentStopped[\s\S]*?Assert-ApplicationProcessesStopped[\s\S]*?Assert-PostgresListenerOwnership[\s\S]*?Test-PostgresReady/);
  assert.match(script, /Get-CimInstance Win32_Process -ErrorAction Stop/);
  assert.match(script, /Get-ErpReferenceSyncCandidates/);
  assert.match(script, /未登记的 ERP reference sync 进程/);
  assert.match(script, /旧 ERP reference sync 必须保持停止/);
});

test("retired ERP bridge provisioning is unreachable and application rollback remains bounded", () => {
  assert.doesNotMatch(script, /"ProvisionErpRole"/);
  assert.doesNotMatch(script, /function Provision-ErpDatabaseRole/);
  assert.doesNotMatch(script, /CREATE ROLE teruisi_erp_reference_sync/);
  assert.doesNotMatch(script, /ALTER ROLE teruisi_erp_reference_sync NOLOGIN/);
  assert.match(erpReferenceService, /ALTER ROLE teruisi_erp_reference_sync NOLOGIN/);
  assert.match(script, /"RollbackApp" \{ Invoke-WithServiceMutex \{ Rollback-Application \} \}/);
  assert.match(script, /Assert-ApplicationTreeManifest \$backup/);
  assert.match(script, /runtime_app_rolled_back/);
  assert.match(script, /数据库 migration 与业务数据未改变/);
  assert.doesNotMatch(script, /TERUISI_PROVISION_ERP_PASSWORD\s*=\s*["'][^$]/);
});

test("runtime deployment and ACL hardening precede an exact startup shortcut", () => {
  assert.match(script, /"DeployApp", "HardenAcl"/);
  assert.match(script, /SetAccessRuleProtection\(\$true, \$false\)/);
  assert.match(script, /SetAccessRuleProtection\(\$false, \$false\)/);
  assert.match(script, /function Reset-RuntimeDescendantDaclWithIcacls/);
  assert.match(
    script,
    /catch \[System\.Security\.AccessControl\.PrivilegeNotHeldException\][\s\S]*?break[\s\S]*?Reset-RuntimeDescendantDaclWithIcacls \$Root/,
  );
  assert.match(
    script,
    /Invoke-IcaclsDaclOnlyChecked \$icacls \$freshPath @\([\s\S]*?"\/reset", "\/T", "\/Q", "\/L"/,
  );
  assert.match(script, /Join-Path \(\[Environment\]::SystemDirectory\) "icacls\.exe"/);
  assert.doesNotMatch(script, /Join-Path \$env:SystemRoot "System32\\icacls\.exe"/);
  assert.doesNotMatch(script, /"\/grant:r"|"\/inheritance:r"/);
  assert.match(script, /function Set-DirectoryDaclOnly/);
  assert.match(script, /System\.IO\.FileSystemAclExtensions" -as \[type\]/);
  assert.match(script, /\[IO\.FileSystemAclExtensions\]::SetAccessControl/);
  assert.match(script, /\$directory\.SetAccessControl\(\$Dacl\)/);
  assert.match(script, /Assert-RuntimeRootAclHardened/);
  assert.match(script, /function Assert-ExactRuntimeAclEntry/);
  assert.match(script, /\$rules\.Count -ne \$AllowedValues\.Count/);
  assert.match(script, /AccessControlType -ne \[Security\.AccessControl\.AccessControlType\]::Allow/);
  assert.match(script, /FileSystemRights -band \[Security\.AccessControl\.FileSystemRights\]::FullControl/);
  assert.match(script, /\$rule\.IsInherited/);
  assert.match(script, /运行目录子项不得包含显式 ACL 规则/);
  assert.match(script, /\$topLevelItems\.Count -gt 128/);
  assert.match(script, /function Initialize-RuntimeAclVerifier/);
  assert.match(script, /DirectoryInfo directory = entry as DirectoryInfo/);
  assert.match(script, /directory\.EnumerateFileSystemInfos\(\)/);
  assert.match(script, /security\.GetAccessRules\(true, true, typeof\(SecurityIdentifier\)\)/);
  assert.match(script, /Invoke-AcceleratedRuntimeAclVerification \$root \$allowedValues/);
  assert.match(script, /runtime_acl_verified/);
  assert.match(script, /objects=\$objectCount elapsedMilliseconds=/);
  assert.match(script, /Get-CanonicalPath \(\[IO\.Path\]::GetDirectoryName\(\$itemPath\)\)/);
  assert.match(
    script,
    /\$itemPath = Assert-RuntimeChildPath \$item\.FullName[\s\S]*?Get-Item -LiteralPath \$itemPath -Force[\s\S]*?\$freshPath = Assert-RuntimeChildPath \$freshItem\.FullName/,
  );
  assert.match(
    script,
    /Invoke-IcaclsDaclOnlyChecked[\s\S]*?Invoke-BoundedNativeProcess \$Executable[\s\S]*?Get-NativeFailureSummary \$nativeRun/,
  );
  assert.doesNotMatch(
    script.match(
      /function Reset-RuntimeDescendantDaclWithIcacls[\s\S]*?\n}\r?\n\r?\nfunction Set-RuntimeDescendantDaclInheritance/,
    )?.[0] ?? "",
    /"\/C"|"\/restore"|"\/setowner"|"\/setintegritylevel"/i,
  );
  assert.match(script, /catch \[IO\.InvalidDataException\]/);
  assert.doesNotMatch(
    script,
    /try \{\s*Assert-RuntimeAclHardened[\s\S]*?runtime_acl_already_hardened[\s\S]*?\} catch \{\s*# Continue with a complete DACL replacement below\./,
  );
  assert.match(script, /Assert-RuntimeAclHardened/);
  assert.match(script, /\$InstalledScriptPath/);
  assert.match(script, /-RuntimeRoot `"\$RuntimeRoot`"/);
  assert.match(script, /Assert-DeployedApplication/);
  assert.match(script, /Copy-WranglerRuntimeClosure/);
  assert.match(script, /\$ApplicationFingerprintAlgorithm = "relative-path-file-sha256-ordinal-v2"/);
  assert.match(script, /function Get-ApplicationTreeFingerprintEvidence/);
  assert.match(script, /\$rows\.Sort\(\[StringComparer\]::Ordinal\)/);
  assert.match(script, /Fingerprint = Get-Sha256Text \(\[string\]::Join\("`n", \$rowArray\)\)/);
  assert.match(
    script,
    /version = 2[\s\S]*?fingerprintAlgorithm = \$ApplicationFingerprintAlgorithm[\s\S]*?fileCount = \[int64\]\$fingerprintEvidence\.FileCount/,
  );
  assert.match(
    script,
    /"version", "deployedAt", "sourceRoot", "fingerprintAlgorithm",[\s\S]*?"fileCount", "appFingerprint"/,
  );
  assert.match(script, /legacy deployment manifest v1；请使用 Windows PowerShell 5\.1/);
  assert.match(script, /Get-ApplicationTreeFingerprintLegacyV1/);
  assert.match(script, /wrangler-dependencies\.json/);
  assert.match(script, /package-lock\.json/);
  assert.match(script, /ExcludeNodeModules/);
  assert.match(script, /Assert-WranglerRuntimeCli/);
  assert.match(script, /Assert-WranglerLocalR2RoundTrip \$staging[\s\S]*Get-ApplicationTreeFingerprintEvidence \$staging/);
  assert.match(script, /WRANGLER_SEND_METRICS/);
  assert.match(script, /EnvironmentVariables\.Remove\(\$name\)/);
  assert.match(script, /"r2", "object", "put"/);
  assert.match(script, /"r2", "object", "get"/);
  assert.match(script, /"r2", "object", "delete"/);
  assert.match(script, /Get-FileSha256 \$inputPath[\s\S]*Get-FileSha256 \$outputPath/);
  assert.doesNotMatch(script, /NODE_PATH[^\n]*=[^\n]*node_modules/i);
  assert.match(script, /__pycache__/);
  assert.match(script, /\.pyc/);
  assert.match(script, /Start 必须从受保护的 runtime app 启动脚本执行/);
  assert.match(script, /RemoveStartup/);
});

test("aggregate status loads one controller and reuses exact in-process process checks", () => {
  assert.match(script, /"AggregateStatus" \{ Show-AggregateServiceStatus \}/);
  const aggregateBlock = script.slice(
    script.indexOf("function Show-AggregateServiceStatus"),
    script.indexOf("function Install-StartupShortcut"),
  );
  assert.match(aggregateBlock, /Invoke-InProcessJsonStatus \{ Show-ServiceStatus \}/);
  assert.match(aggregateBlock, /Invoke-InProcessJsonStatus \{ Show-FinanceServiceStatus \}/);
  for (const service of ["netshop", "market", "products", "inventory", "workflow"]) {
    assert.match(aggregateBlock, new RegExp(`Get-SimpleDjangoDomainStatus[\\s\\S]*?django-${service}-reader`));
  }
  assert.match(aggregateBlock, /Get-SimpleDjangoReaderStatus[\s\S]*?django-bi-reader/);
  assert.match(aggregateBlock, /teruisi-django-aggregate-status-v1/);
  assert.match(aggregateBlock, /ElapsedMilliseconds = \[int64\]\$timer\.ElapsedMilliseconds/);
  assert.doesNotMatch(aggregateBlock, /Start-Process|powershell\.exe|pwsh\.exe/);
  assert.equal(script.match(/if \(\$reader -eq "running"\) \{/g)?.length, 4);
  assert.equal(script.match(/if \(\$writer -eq "running"\) \{/g)?.length, 3);
});

test("Wrangler deployment integration test is external, destructive only inside its unique temp runtime", () => {
  assert.match(deploymentRuntimeTest, /\[IO\.Path\]::GetTempPath\(\)/);
  assert.match(deploymentRuntimeTest, /tdsrt-/);
  assert.match(deploymentRuntimeTest, /-Action DeployApp/);
  assert.match(deploymentRuntimeTest, /-Action HardenAcl/);
  assert.match(deploymentRuntimeTest, /New-Item -ItemType Junction/);
  assert.match(deploymentRuntimeTest, /HardenAcl accepted a runtime junction/);
  assert.match(deploymentRuntimeTest, /changed the ACL of a junction target outside runtime/);
  assert.match(aclFallbackRuntimeTest, /PrivilegeNotHeldException/);
  assert.match(aclFallbackRuntimeTest, /ACL fallback changed audit evidence bytes/);
  assert.match(aclFallbackRuntimeTest, /ACL fallback did not restore descendant inheritance/);
  assert.match(aclFallbackRuntimeTest, /icacls nonzero exit was swallowed/);
  assert.match(aclFallbackRuntimeTest, /scan-to-write junction race/);
  assert.match(aclFallbackRuntimeTest, /changed a junction target outside runtime/);
  assert.match(aclFallbackRuntimeTest, /accepted a descendant explicit deny/);
  assert.match(aclFallbackRuntimeTest, /accepted missing SYSTEM and Administrators/);
  assert.match(aclFallbackRuntimeTest, /trusted the process SystemRoot override/);
  assert.match(deploymentRuntimeTest, /Assert-WranglerLocalR2RoundTrip/);
  assert.match(deploymentRuntimeTest, /runtime-tools\\node_modules\\miniflare/);
  assert.match(deploymentRuntimeTest, /Wrangler smoke did not fail closed after runtime miniflare was removed/);
  assert.match(deploymentRuntimeTest, /StartsWith\(\$canonicalPrefix/);
  assert.doesNotMatch(deploymentRuntimeTest, /D:\\运营管理系统\\\.wrangler|site-creator-r2/);
  assert.match(fingerprintRuntimeTest, /\[StringComparer\]::Ordinal/);
  assert.match(fingerprintRuntimeTest, /0x4e2d/);
  assert.match(fingerprintRuntimeTest, /0x0301/);
  assert.match(fingerprintRuntimeTest, /included content change did not change only the fingerprint/);
  assert.match(fingerprintRuntimeTest, /included file addition did not change count and fingerprint/);
  assert.match(fingerprintRuntimeTest, /included file removal did not change count and fingerprint/);
  assert.match(fingerprintRuntimeTest, /deployment\.json or Python cache material entered the fingerprint/);
});

test("formal D1 retirement actions use the protected runtime, DPAPI writer, live preflight and controlled audit", () => {
  assert.match(script, /"PlanSalesD1Retirement", "RetireSalesD1"/);
  assert.match(script, /"PlanSalesD1Retirement" \{ Invoke-WithServiceMutex \{ Invoke-PlanSalesD1Retirement \} \}/);
  assert.match(script, /"RetireSalesD1" \{ Invoke-WithServiceMutex \{ Invoke-RetireSalesD1 \} \}/);
  assert.match(script, /Assert-DeployedApplication[\s\S]*Assert-RuntimeAclHardened/);
  assert.match(script, /sales-d1-retirement\.ts/);
  assert.match(script, /sales-d1-write-authority\.ts/);
  assert.match(script, /sales-legacy-r2-cleanup\.ts/);
  assert.match(script, /sqlite-consistent-backup\.py/);
  assert.match(script, /sales-local-cutover-backup\.ps1/);
  assert.match(script, /worker-local-release\.mjs/);
  assert.match(script, /0092_sales_domain_retirement\.sql/);
  assert.match(script, /\$context\.Source/);
  assert.match(script, /\$config\.erpSourceD1/);
  assert.match(script, /audits\\sales-retirement/);
  assert.match(script, /RetireSalesD1 必须显式提供 -Execute/);
  assert.match(script, /Wait-DjangoReady "reader"/);
  assert.match(script, /Wait-DjangoReady "writer"/);
  assert.match(script, /Read-Secrets/);
  assert.match(script, /Database-Url "teruisi_sales_writer"/);
  assert.match(script, /TERUISI_DJANGO_RETIREMENT_MANAGED = "1"/);
  assert.match(script, /"migration_writer" \$false/);
  assert.match(script, /\^v24\\\./);
  assert.doesNotMatch(script, /RetireSalesD1[\s\S]{0,300}confirmed-postgresql-smoke/);

  assert.match(
    script,
    /function Assert-SalesRetirementWorkerStopped\(\[string\]\$Boundary\)[\s\S]*?foreach \(\$port in @\(3000, 5791\)\)[\s\S]*?Get-PortListeners \$port[\s\S]*?Worker\/工作流辅助端口 \$port 已停止/,
  );
  const planBody = script.match(
    /function Invoke-PlanSalesD1Retirement \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Invoke-RetireSalesD1/,
  )?.[1] ?? "";
  assert.equal((planBody.match(/Assert-SalesRetirementWorkerStopped/g) ?? []).length, 2);
  assert.match(
    planBody,
    /Assert-SalesRetirementWorkerStopped "D1 sales retirement plan 前"[\s\S]*?Invoke-RetirementNode[\s\S]*?Assert-SalesRetirementWorkerStopped "D1 sales retirement plan 后"/,
  );
  const retireBody = script.match(
    /function Invoke-RetireSalesD1 \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Invoke-CreateSalesCutoverSmokeReceipt/,
  )?.[1] ?? "";
  assert.equal((retireBody.match(/Assert-SalesRetirementWorkerStopped/g) ?? []).length, 2);
  assert.match(
    retireBody,
    /Assert-SalesRetirementWorkerStopped "D1 sales retirement execute 前"[\s\S]*?Invoke-WithDjangoEnvironment[^\{]+\{\s*Assert-SalesRetirementWorkerStopped "D1 sales retirement managed execute 最终栅栏"\s*\$env:TERUISI_DJANGO_RETIREMENT_MANAGED = "1"[\s\S]*?Invoke-RetirementNode/,
  );

  assert.match(retirementOperator, /createDjangoRetirementPreflightRunner/);
  assert.match(retirementOperator, /sales_cutover_retirement_preflight/);
  assert.match(retirementOperator, /D:\\\\teruisi-runtime\\\\django-sales/);
  assert.match(retirementOperator, /runtime service config/);
  assert.match(retirementOperator, /serviceConfig\.erpSourceD1/);
  assert.match(
    retirementOperator,
    /DEPLOYMENT_FINGERPRINT_ALGORITHM = "relative-path-file-sha256-ordinal-v2"/,
  );
  assert.match(
    retirementOperator,
    /"version", "deployedAt", "sourceRoot", "fingerprintAlgorithm",[\s\S]*?"fileCount", "appFingerprint"/,
  );
  assert.match(retirementOperator, /deployment\.version !== 2/);
  assert.match(retirementOperator, /Number\.isSafeInteger\(deployment\.fileCount\)/);
  assert.doesNotMatch(retirementOperator, /export async function executeSalesD1Retirement\(/);
  assert.match(retirementOperator, /executeSalesD1RetirementForTest/);
  assert.match(retirementOperator, /NODE_TEST_CONTEXT/);
  assert.match(retirementOperator, /shell: false/);
  assert.match(retirementOperator, /windowsHide: true/);
  assert.match(retirementOperator, /TERUISI_DJANGO_DATABASE_URL/);
  assert.match(retirementOperator, /teruisi_sales_retirement/);
  assert.match(retirementOperator, /statement_timeout=900000/);
  assert.match(retirementOperator, /teruisi_sales_writer/);
  assert.match(retirementOperator, /--managed-execute/);
});

test("formal smoke receipt action runs fixed loopback reader/writer checks under the writer role", () => {
  assert.match(script, /CreateSalesCutoverSmokeReceipt/);
  assert.match(script, /sales_cutover_smoke_receipt/);
  assert.match(script, /Wait-DjangoReady "reader"/);
  assert.match(script, /Wait-DjangoReady "writer"/);
  assert.match(script, /Invoke-WithDjangoEnvironment \$secrets \$writerUrl "sales_writer"/);
  assert.match(script, /Get-FileSha256 \$receiptPath/);
  const smokeBody = script.match(
    /function Invoke-CreateSalesCutoverSmokeReceipt \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Start-ServiceStack/,
  )?.[1] ?? "";
  assert.equal((smokeBody.match(/Assert-SalesRetirementWorkerStopped/g) ?? []).length, 2);
  assert.match(
    smokeBody,
    /Assert-SalesRetirementWorkerStopped "正式本机 sales smoke 前"[\s\S]*?sales_cutover_smoke_receipt[\s\S]*?Assert-SalesRetirementWorkerStopped "正式本机 sales smoke 后"/,
  );
});

test("Node 24 executes the deployed retirement TypeScript without tsx or an extensionless import", () => {
  const result = spawnSync(process.execPath, ["tools/sales-d1-retirement.ts", "--bad"], {
    encoding: "utf8",
    timeout: 10_000,
  });
  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /未知参数：--bad/);
  assert.doesNotMatch(result.stderr, /ERR_MODULE_NOT_FOUND|Unknown file extension|tsx/);
});

const fingerprintShells = ["powershell.exe", "pwsh.exe"] as const;
const fingerprintShellProbes = fingerprintShells.map((shell) => ({
  shell,
  probe: spawnSync(shell, ["-NoProfile", "-Command", "exit 0"], { timeout: 10_000 }),
}));

test(
  "application fingerprint v2 is ordinal and identical in Windows PowerShell 5.1 and pwsh",
  {
    skip: process.platform !== "win32"
      || fingerprintShellProbes.some(({ probe }) => probe.error !== undefined || probe.status !== 0),
  },
  () => {
    const payloads = fingerprintShellProbes.map(({ shell }) => {
      const result = spawnSync(
        shell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests\\django-local-service-fingerprint.test.ps1"],
        { encoding: "utf8", timeout: 30_000 },
      );
      assert.equal(result.status, 0, `${shell}\n${result.stdout}\n${result.stderr}`);
      const line = result.stdout.trim().split(/\r?\n/).at(-1) ?? "";
      const payload = JSON.parse(line) as {
        status: string;
        algorithm: string;
        fileCount: number;
        fingerprint: string;
        rows: string[];
      };
      assert.equal(payload.status, "completed");
      assert.equal(payload.algorithm, "relative-path-file-sha256-ordinal-v2");
      assert.equal(payload.fileCount, payload.rows.length);
      assert.match(payload.fingerprint, /^[0-9a-f]{64}$/);
      assert.ok(payload.rows.some((row) => row.startsWith("names/under_score.txt\n")));
      assert.ok(payload.rows.some((row) => row.startsWith("names/中文.txt\n")));
      assert.ok(payload.rows.some((row) => row.startsWith("names/é.txt\n")));
      assert.ok(payload.rows.some((row) => row.startsWith("names/é.txt\n")));
      return payload;
    });
    assert.deepEqual(payloads[0], payloads[1]);
  },
);

for (const shell of ["powershell.exe", "pwsh.exe"]) {
  const shellProbe = spawnSync(shell, ["-NoProfile", "-Command", "exit 0"], { timeout: 10_000 });
  test(
    `orchestrated lifecycle ACL evidence is bounded and fail-closed in ${shell}`,
    {
      skip: process.platform !== "win32"
        || shellProbe.error !== undefined
        || shellProbe.status !== 0,
    },
    () => {
      const result = spawnSync(
        shell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests\\django-orchestrated-start-acl.test.ps1"],
        { encoding: "utf8", timeout: 30_000 },
      );
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      assert.match(result.stdout, /PASS: orchestrated lifecycle ACL context is exact, bounded, and fail-closed/);
    },
  );

  test(
    `descendant ACL fallback is bounded and fail-closed in ${shell}`,
    {
      skip: process.platform !== "win32"
        || shellProbe.error !== undefined
        || shellProbe.status !== 0,
    },
    () => {
      const result = spawnSync(
        shell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests\\django-local-service-acl-fallback.test.ps1"],
        { encoding: "utf8", timeout: 30_000 },
      );
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      assert.match(result.stdout, /PASS: descendant ACL fallback is DACL-only, bounded, and fail-closed/);
    },
  );

  test(
    `accelerated exact ACL verification stays inside its startup budget in ${shell}`,
    {
      skip: process.platform !== "win32"
        || shellProbe.error !== undefined
        || shellProbe.status !== 0,
    },
    () => {
      const result = spawnSync(
        shell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests\\django-local-service-acl-performance.test.ps1"],
        { encoding: "utf8", timeout: 30_000 },
      );
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      const payload = JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1) ?? "") as {
        status: string;
        objectCount: number;
        elapsedMilliseconds: number;
        budgetMilliseconds: number;
      };
      assert.equal(payload.status, "passed");
      assert.equal(payload.objectCount, 5101);
      assert.ok(payload.elapsedMilliseconds < payload.budgetMilliseconds, JSON.stringify(payload));
    },
  );

  test(
    `Windows managed-process round trip accepts protected venv launchers in ${shell}`,
    {
      skip: process.platform !== "win32"
        || !existsSync("D:\\teruisi-runtime\\django-sales\\venv\\Scripts\\python.exe")
        || shellProbe.error !== undefined
        || shellProbe.status !== 0,
    },
    () => {
      const result = spawnSync(
        shell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests\\django-local-service-process-identity.test.ps1"],
        { encoding: "utf8", timeout: 30_000 },
      );
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      assert.match(result.stdout, /PASS: launcher=/);
      assert.match(result.stdout, /descendants=\d+/);
    },
  );

  test(
    `code-only rollback exchanges verified application manifests in ${shell}`,
    {
      skip: process.platform !== "win32"
        || shellProbe.error !== undefined
        || shellProbe.status !== 0,
    },
    () => {
      const result = spawnSync(
        shell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tests\\django-local-service-rollback.test.ps1"],
        { encoding: "utf8", timeout: 30_000 },
      );
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      assert.match(result.stdout, /PASS: code-only rollback exchanged verified manifests/);
    },
  );
}
