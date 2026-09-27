import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const controllerUrl = new URL("../tools/django-products-service.ps1", import.meta.url);
const baseControllerUrl = new URL("../tools/django-local-service.ps1", import.meta.url);
const healthUrl = new URL("../backend/teruisi_backend/health.py", import.meta.url);

test("products runtime uses isolated endpoints, identities, and bounded bodies", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /127\.0\.0\.1:8041\/health\/ready/);
  assert.match(source, /127\.0\.0\.1:8042\/health\/ready/);
  assert.match(source, /django-products-reader\.pid\.json/);
  assert.match(source, /django-products-writer\.pid\.json/);
  assert.match(source, /--listen=127\.0\.0\.1:8041/);
  assert.match(source, /--listen=127\.0\.0\.1:8042/);
  assert.match(source, /\$ProductsReaderMaxBodyBytes = 1048576/);
  assert.match(source, /\$ProductsWriterMaxBodyBytes = 33554432/);
  assert.match(source, /\$MinimumPostgresConnectionsForProducts = 120/);
  assert.match(source, /Stop-OwnedProcess "django-products-reader"/);
  assert.match(source, /Stop-OwnedProcess "django-products-writer"/);
});

test("products roles enforce the same explicit least-privilege allowlist as readiness", async () => {
  const [source, health, writerFence] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
    readFile(new URL("../backend/products/write_requests.py", import.meta.url), "utf8"),
  ]);
  assert.match(source, /sql\.Literal\(password\)/);
  assert.doesNotMatch(source, /PASSWORD %s/);
  assert.match(source, /CREATE ROLE \{\} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE/);
  assert.match(source, /NOINHERIT NOREPLICATION NOBYPASSRLS/);
  assert.match(source, /ALTER ROLE teruisi_products_reader SET default_transaction_read_only=on/);
  assert.match(source, /ALTER ROLE teruisi_products_writer RESET default_transaction_read_only/);
  assert.match(
    source,
    /CREATE POLICY products_revision_reader ON sales_data_revisions[\s\S]*?FOR SELECT TO teruisi_products_reader[\s\S]*?USING \(domain IN \('sales', 'erp'\)\)/,
  );
  assert.match(source, /products reader revision RLS policy is invalid/);
  assert.match(source, /FROM pg_policy p/);
  for (const table of [
    "erp_product_master", "erp_combo_items", "erp_reference_import_batches_pg",
    "erp_reference_import_scope_heads", "erp_reference_write_authority",
  ]) assert.match(source, new RegExp(`"${table}"`));
  assert.match(source, /products writer DML escaped allowlist/);
  assert.match(source, /"product_write_authority": \("SELECT",\)/);
  assert.match(writerFence, /ProductWriteAuthority\.objects\.get\(id=1\)/);
  assert.doesNotMatch(
    writerFence,
    /ProductWriteAuthority\.objects\.select_for_update\(\)\.get\(id=1\)/,
  );
  assert.match(health, /PRODUCTS_WRITER_TABLE_PRIVILEGES/);
  assert.match(health, /_validate_products_writer_permissions/);
  for (const table of [
    "product_shipping_rates",
    "product_import_attempts",
    "product_inventory_projection",
    "product_raw_upload_sessions",
    "product_write_request_receipts",
  ]) {
    assert.match(source, new RegExp(`"${table}"`));
    assert.match(health, new RegExp(`"${table}"`));
  }
  const privileges = source.match(/writer_privileges = \{[\s\S]*?\n\}/)?.[0] ?? "";
  assert.doesNotMatch(privileges, /product_migration_runs/);
});

test("products readiness and writer startup fail closed on authority or dependency drift", async () => {
  const [source, baseSource, health] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(baseControllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
  ]);
  assert.match(source, /function Get-ProductsWriteAuthority/);
  assert.match(source, /if \(\[string\]\$Authority\.status -cne "postgres"\)/);
  assert.match(source, /products-service-enabled\.json/);
  assert.match(source, /Test-ExactObjectPropertyNames \$startup/);
  assert.match(source, /cursor\.execute\("SHOW max_connections"\)/);
  assert.match(source, /\[int\]\$payload\.maxConnections -lt \$MinimumPostgresConnectionsForProducts/);
  assert.match(source, /max_connections 低于商品经营正式运行所需容量/);
  assert.match(baseSource, /TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH/);
  assert.match(baseSource, /TERUISI_DJANGO_PRODUCTS_CUTOVER_ID/);
  assert.match(health, /_validate_products_writer_authority/);
  assert.match(health, /products_writer_authority_mismatch/);
  assert.match(health, /_validate_reader_state\(cursor\)/);
});

test("base deployment and login chain include products without replacing other domains", async () => {
  const source = await readFile(baseControllerUrl, "utf8");
  assert.match(source, /tools\\django-products-service\.ps1/);
  assert.match(source, /drizzle\\0099_product_write_authority\.sql/);
  assert.match(source, /drizzle\\0100_product_domain_retirement\.sql/);
  assert.match(source, /ProductsStartupEnabledPath/);
  assert.match(source, /InstalledProductsScriptPath -Action Start/);
  assert.match(source, /InstalledProductsScriptPath -Action Stop/);
  assert.match(source, /Get-PortListeners 8041/);
  assert.match(source, /Get-PortListeners 8042/);
  assert.match(source, /django-market-service\.ps1/);
  assert.match(source, /django-netshop-service\.ps1/);
});
