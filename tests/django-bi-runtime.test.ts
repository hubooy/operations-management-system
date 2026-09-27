import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const controllerUrl = new URL("../tools/django-bi-service.ps1", import.meta.url);
const baseControllerUrl = new URL("../tools/django-local-service.ps1", import.meta.url);
const healthUrl = new URL("../backend/teruisi_backend/health.py", import.meta.url);
const maintenanceUrl = new URL("../tools/django-postgres-maintenance.ps1", import.meta.url);
const backupHelperUrl = new URL("../tools/postgres-consistent-backup.py", import.meta.url);

test("BI runtime is one isolated read-only process", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /127\.0\.0\.1:8081\/health\/ready/);
  assert.match(source, /django-bi-reader\.pid\.json/);
  assert.match(source, /--listen=127\.0\.0\.1:8081/);
  assert.match(source, /Database-Url "teruisi_bi_reader"/);
  assert.match(source, /ALTER ROLE teruisi_bi_reader SET default_transaction_read_only=on/);
  assert.match(source, /BI reader DML escaped read-only allowlist/);
  assert.match(source, /"PlanMigration" \{ Invoke-WithServiceMutex/);
  assert.match(source, /"ApplyMigration" \{ Invoke-WithServiceMutex/);
  assert.match(source, /"VerifyMigration" \{ Invoke-WithServiceMutex/);
  assert.match(source, /Database-Url "teruisi_sales_owner"/);
  assert.match(source, /ConvertFrom-UniqueNativeJson \$run "BI 迁移证据探针"/);
  assert.match(source, /ConvertFrom-UniqueNativeJson \$run "BI \$Mode"/);
  assert.match(source, /django\.setup\(\)/);
  assert.match(source, /\$RequestedApprovedPlanId = \$ApprovedPlanId/);
  assert.match(source, /\$RequestedApprovedRunId = \$ApprovedRunId/);
  assert.match(source, /\$ApprovedPlanId = \$RequestedApprovedPlanId/);
  assert.match(source, /\$ApprovedRunId = \$RequestedApprovedRunId/);
  assert.doesNotMatch(source, /\$run\.Stdout/);
  assert.doesNotMatch(source, /bi_writer|django-bi-writer|8082/);
});

test("BI role sees only the bounded source projection and audit receipt", async () => {
  const source = await readFile(controllerUrl, "utf8");
  for (const table of [
    "bi_migration_runs",
    "sales_data_revisions",
    "sales_import_batches",
    "sales_order_lines",
    "erp_product_master",
    "erp_combo_items",
    "erp_reference_import_batches_pg",
    "erp_reference_import_scope_heads",
    "erp_reference_write_authority",
    "inventory_import_batches",
    "inventory_stock_lines",
    "inventory_age_lines",
    "inventory_data_revisions",
    "replenishment_plan_items",
    "inventory_operating_settings",
    "inventory_guangdong_monitor_items",
    "inventory_guangdong_supplier_cycles",
  ]) {
    assert.match(source, new RegExp(`"${table}"`));
  }
  assert.match(source, /CREATE POLICY bi_revision_reader/);
  assert.match(source, /domain IN \('sales','erp'\)/);
  assert.match(source, /REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public/);
});

test("BI readiness, migration receipt, deploy, and login chain fail closed together", async () => {
  const [source, baseSource, health, maintenance, backupHelper] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(baseControllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
    readFile(maintenanceUrl, "utf8"),
    readFile(backupHelperUrl, "utf8"),
  ]);
  assert.match(source, /verified BI migration run missing/);
  assert.match(source, /bi-service-enabled\.json/);
  assert.match(source, /bi-dashboard-read-model-v1/);
  assert.match(baseSource, /tools\\django-bi-service\.ps1/);
  assert.match(baseSource, /BiStartupEnabledPath/);
  assert.match(baseSource, /InstalledBiScriptPath -Action Start/);
  assert.match(baseSource, /InstalledBiScriptPath -Action Stop/);
  assert.match(baseSource, /Get-PortListeners 8081/);
  assert.match(baseSource, /Bi = \$bi/);
  assert.match(health, /_validate_bi_reader_state/);
  assert.match(health, /bi_reader_migration_unverified/);
  assert.match(health, /bi_reader_unavailable/);
  assert.match(maintenance, /"teruisi_bi_reader"/);
  assert.match(maintenance, /requiredTables \+= @\("bi_migration_runs"\)/);
  assert.match(backupHelper, /"bi_"/);
  assert.match(backupHelper, /required\.add\("bi_migration_runs"\)/);
});
