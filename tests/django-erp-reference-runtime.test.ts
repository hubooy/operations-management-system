import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const controllerUrl = new URL("../tools/django-erp-reference.ps1", import.meta.url);
const baseControllerUrl = new URL("../tools/django-local-service.ps1", import.meta.url);
const healthUrl = new URL("../backend/teruisi_backend/health.py", import.meta.url);
const cutoverUrl = new URL("../tools/django-erp-reference-cutover.ps1", import.meta.url);
const smokeUrl = new URL("../tools/erp-reference-production-smoke.ps1", import.meta.url);
const consumerSmokeUrl = new URL("../tools/erp-reference-consumer-smoke.ts", import.meta.url);
const retirementUrl = new URL("../backend/erp_reference/management/commands/retire_erp_reference_d1.py", import.meta.url);
const publicRouteUrl = new URL("../app/api/imports/erp/route.ts", import.meta.url);
const chunkRouteUrl = new URL("../app/api/imports/erp/chunks/route.ts", import.meta.url);
const globalSearchUrl = new URL("../lib/search/global-search.ts", import.meta.url);
const aiToolsUrl = new URL("../lib/ai/page-data-tools.ts", import.meta.url);

test("ERP runtime uses isolated endpoints, roles, bodies, and process receipts", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /127\.0\.0\.1:8091\/health\/ready/);
  assert.match(source, /127\.0\.0\.1:8092\/health\/ready/);
  assert.match(source, /django-erp-reference-reader\.pid\.json/);
  assert.match(source, /django-erp-reference-writer\.pid\.json/);
  assert.match(source, /--listen=127\.0\.0\.1:8091/);
  assert.match(source, /--listen=127\.0\.0\.1:8092/);
  assert.match(source, /\$ErpReferenceWriterMaxBodyBytes = 67108864/);
  assert.match(source, /\$MinimumPostgresConnectionsForErpReference = 120/);
  assert.match(source, /cursor\.execute\("SHOW max_connections"\)/);
  assert.match(source, /max_connections 低于完整 Django\/BI 运行栈所需的 120/);
  assert.match(source, /ALTER ROLE teruisi_erp_reference_reader SET default_transaction_read_only=on/);
  assert.match(source, /NOINHERIT NOREPLICATION NOBYPASSRLS/);
  assert.match(source, /GRANT UPDATE \(resolved_category\) ON sales_order_lines TO teruisi_erp_reference_writer/);
});

test("ERP authority, readiness, deployment and login chain fail closed together", async () => {
  const [source, base, health] = await Promise.all([
    readFile(controllerUrl, "utf8"), readFile(baseControllerUrl, "utf8"), readFile(healthUrl, "utf8"),
  ]);
  assert.match(source, /function Get-ErpReferenceWriteAuthority/);
  assert.match(source, /erp-reference-enabled\.json/);
  assert.match(source, /\^erp-reference-\[0-9a-f\]\{32\}\$/);
  assert.match(base, /TERUISI_DJANGO_ERP_AUTHORITY_EPOCH/);
  assert.match(base, /TERUISI_DJANGO_ERP_CUTOVER_ID/);
  assert.match(base, /tools\\django-erp-reference\.ps1/);
  assert.match(base, /drizzle\\0109_erp_reference_write_authority\.sql/);
  assert.match(base, /drizzle\\0110_erp_reference_domain_retirement\.sql/);
  assert.match(base, /ErpReferenceStartupEnabledPath/);
  assert.match(base, /InstalledErpReferenceScriptPath -Action Start/);
  assert.match(base, /InstalledErpReferenceScriptPath -Action Stop/);
  assert.match(health, /_validate_erp_reference_writer_authority/);
  assert.match(health, /_validate_erp_reference_writer_permissions/);
  assert.match(health, /validate_erp_reference_runtime_state/);
});

test("terminal core startup rejects the retired ERP bridge", async () => {
  const base = await readFile(baseControllerUrl, "utf8");
  const start = base.slice(base.indexOf("function Start-ServiceStack"), base.indexOf("function Stop-ServiceStack"));
  assert.doesNotMatch(start, /Start-ErpReferenceSync|Invoke-ErpReferenceSyncOnce/);
  assert.match(start, /旧 ERP reference sync 必须保持停止/);
  assert.doesNotMatch(base.slice(base.lastIndexOf("switch \($Action\)")), /ProvisionErpRole|InitializeErpReference/);
});

test("live ERP entrypoints cannot import the retired D1 domain implementation", async () => {
  const sources = await Promise.all([
    publicRouteUrl, chunkRouteUrl, globalSearchUrl, aiToolsUrl,
  ].map((url) => readFile(url, "utf8")));
  const retired = /@\/lib\/erp-reference\/(?:database|import-service|projection-outbox)/;
  for (const source of sources) assert.doesNotMatch(source, retired);
  assert.match(sources[0]!, /createDjangoErpReferenceService/);
  assert.match(sources[0]!, /importErpReferenceToDjango/);
  assert.match(sources[1]!, /importErpReferenceToDjango/);
  assert.match(sources[2]!, /createDjangoErpReferenceConsumerReader/);
  assert.match(sources[3]!, /createDjangoErpReferenceService/);
});

test("ERP cutover binds exact migration, smoke, R2 and terminal D1 retirement evidence", async () => {
  const [cutover, smoke, consumerSmoke, retirement, base] = await Promise.all([
    readFile(cutoverUrl, "utf8"), readFile(smokeUrl, "utf8"),
    readFile(consumerSmokeUrl, "utf8"),
    readFile(retirementUrl, "utf8"), readFile(baseControllerUrl, "utf8"),
  ]);
  assert.match(cutover, /migrate_erp_reference_from_d1/);
  assert.match(cutover, /"PrepareRuntime"/);
  assert.match(cutover, /Start-Postgres/);
  assert.match(cutover, /Invoke-DjangoMigrations/);
  assert.match(cutover, /Assert-ApplicationProcessesStopped "准备ERP 主数据 PostgreSQL 迁移"/);
  assert.match(cutover, /--mode", \$Mode/);
  assert.match(cutover, /--approve-run-id/);
  assert.match(cutover, /--verify-run-id/);
  assert.match(cutover, /drizzle\\0109_erp_reference_write_authority\.sql/);
  assert.match(cutover, /retire_erp_reference_d1/);
  assert.match(cutover, /--approved-plan-id/);
  assert.match(cutover, /erp-reference-r2-retirement-evidence\.py/);
  assert.match(smoke, /127\.0\.0\.1:8091\/health\/ready/);
  assert.match(smoke, /127\.0\.0\.1:8092\/health\/ready/);
  assert.match(smoke, /\/api\/imports\/erp\?source=products/);
  assert.match(smoke, /erp-reference-d1-rejection-smoke\.py/);
  assert.match(smoke, /TERUISI_DJANGO_ERP_MODE/);
  assert.match(smoke, /SkipHttpErrorCheck = \$true/);
  assert.ok(smoke.indexOf('"/api/imports/erp/chunks"') < smoke.indexOf('"/api/imports/erp" "POST"'));
  assert.match(consumerSmoke, /operation: "product_search",\s*query: "[^"]+"/);
  assert.match(retirement, /0110_erp_reference_domain_retirement\.sql/);
  assert.match(retirement, /erp-reference-system-test-receipt-v1/);
  assert.match(retirement, /erp-reference-r2-retirement-evidence-v1/);
  for (const file of [
    "tools\\django-erp-reference-cutover.ps1",
    "tools\\erp-reference-production-smoke.ps1",
    "tools\\erp-reference-consumer-smoke.ts",
    "tools\\erp-reference-d1-rejection-smoke.py",
    "tools\\erp-reference-r2-retirement-evidence.py",
    "tools\\erp-reference-d1-snapshot.py",
  ]) assert.match(base, new RegExp(file.replace(/\\/g, "\\\\")));
});
