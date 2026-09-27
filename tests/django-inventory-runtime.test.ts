import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const controllerUrl = new URL("../tools/django-inventory-service.ps1", import.meta.url);
const baseControllerUrl = new URL("../tools/django-local-service.ps1", import.meta.url);
const healthUrl = new URL("../backend/teruisi_backend/health.py", import.meta.url);

test("inventory runtime uses isolated endpoints, roles, bodies, and process receipts", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /127\.0\.0\.1:8051\/health\/ready/);
  assert.match(source, /127\.0\.0\.1:8052\/health\/ready/);
  assert.match(source, /django-inventory-reader\.pid\.json/);
  assert.match(source, /django-inventory-writer\.pid\.json/);
  assert.match(source, /--listen=127\.0\.0\.1:8051/);
  assert.match(source, /--listen=127\.0\.0\.1:8052/);
  assert.match(source, /\$InventoryReaderMaxBodyBytes = 1048576/);
  assert.match(source, /\$InventoryWriterMaxBodyBytes = 67108864/);
  assert.match(source, /Stop-OwnedProcess "django-inventory-reader"/);
  assert.match(source, /Stop-OwnedProcess "django-inventory-writer"/);
});

test("inventory runtime and readiness share an explicit least-privilege allowlist", async () => {
  const [source, health, writerFence] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
    readFile(new URL("../backend/inventory/write_requests.py", import.meta.url), "utf8"),
  ]);
  assert.match(source, /CREATE ROLE \{\} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE/);
  assert.match(source, /NOINHERIT NOREPLICATION NOBYPASSRLS/);
  assert.match(source, /ALTER ROLE teruisi_inventory_reader SET default_transaction_read_only=on/);
  assert.match(source, /ALTER ROLE teruisi_inventory_writer RESET default_transaction_read_only/);
  assert.match(source, /FOR SELECT TO teruisi_inventory_reader, teruisi_inventory_writer/);
  assert.match(source, /inventory writer DML escaped allowlist/);
  assert.match(source, /"inventory_write_authority": \("SELECT",\)/);
  assert.match(writerFence, /InventoryWriteAuthority\.objects\.get\(id=1\)/);
  assert.doesNotMatch(writerFence, /InventoryWriteAuthority\.objects\.select_for_update/);
  assert.match(health, /INVENTORY_WRITER_TABLE_PRIVILEGES/);
  assert.match(health, /_validate_inventory_writer_permissions/);
  for (const table of [
    "inventory_stock_lines",
    "inventory_age_lines",
    "inventory_import_attempts",
    "inventory_raw_upload_sessions",
    "inventory_write_request_receipts",
    "replenishment_plan_items",
    "inventory_replenishment_group_deliveries",
  ]) {
    assert.match(source, new RegExp(`"${table}"`));
    assert.match(health, new RegExp(`"${table}"`));
  }
  const privileges = source.match(/writer_privileges = \{[\s\S]*?\n\}/)?.[0] ?? "";
  assert.doesNotMatch(privileges, /inventory_migration_runs/);
});

test("inventory authority, readiness, deployment, and login chain fail closed together", async () => {
  const [source, baseSource, health] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(baseControllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
  ]);
  assert.match(source, /function Get-InventoryWriteAuthority/);
  assert.match(source, /if \(\[string\]\$Authority\.status -cne "postgres"\)/);
  assert.match(source, /inventory-service-enabled\.json/);
  assert.match(source, /Test-ExactObjectPropertyNames \$startup/);
  assert.match(source, /\^inventory-apply-\[0-9a-f\]\{32\}\$/);
  assert.match(baseSource, /TERUISI_DJANGO_INVENTORY_AUTHORITY_EPOCH/);
  assert.match(baseSource, /TERUISI_DJANGO_INVENTORY_CUTOVER_ID/);
  assert.match(health, /_validate_inventory_writer_authority/);
  assert.match(health, /inventory_writer_authority_mismatch/);
  assert.match(health, /_validate_reader_state\(cursor\)/);
  assert.match(baseSource, /tools\\django-inventory-service\.ps1/);
  assert.match(baseSource, /drizzle\\0101_inventory_write_authority\.sql/);
  assert.match(baseSource, /drizzle\\0102_inventory_domain_retirement\.sql/);
  assert.match(baseSource, /InventoryStartupEnabledPath/);
  assert.match(baseSource, /InstalledInventoryScriptPath -Action Start/);
  assert.match(baseSource, /InstalledInventoryScriptPath -Action Stop/);
  assert.match(baseSource, /Get-PortListeners 8051/);
  assert.match(baseSource, /Get-PortListeners 8052/);
});
