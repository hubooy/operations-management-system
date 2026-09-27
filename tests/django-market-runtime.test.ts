import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const controllerUrl = new URL("../tools/django-market-service.ps1", import.meta.url);
const baseControllerUrl = new URL("../tools/django-local-service.ps1", import.meta.url);
const healthUrl = new URL("../backend/teruisi_backend/health.py", import.meta.url);

test("market runtime uses isolated fixed endpoints, identities, receipts, and bounded bodies", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /127\.0\.0\.1:8031\/health\/ready/);
  assert.match(source, /127\.0\.0\.1:8032\/health\/ready/);
  assert.match(source, /django-market-reader\.pid\.json/);
  assert.match(source, /django-market-writer\.pid\.json/);
  assert.match(source, /--listen=127\.0\.0\.1:8031/);
  assert.match(source, /--listen=127\.0\.0\.1:8032/);
  assert.match(source, /\$MarketReaderMaxBodyBytes = 1048576/);
  assert.match(source, /\$MarketWriterMaxBodyBytes = 134217728/);
  assert.match(source, /Stop-OwnedProcess "django-market-reader"/);
  assert.match(source, /Stop-OwnedProcess "django-market-writer"/);
});

test("market roles quote secrets and enforce the same explicit DML allowlist as readiness", async () => {
  const [source, health] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
  ]);
  assert.match(source, /sql\.Literal\(password\)/);
  assert.doesNotMatch(source, /PASSWORD %s/);
  assert.match(source, /CREATE ROLE \{\} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE/);
  assert.match(source, /NOINHERIT NOREPLICATION NOBYPASSRLS/);
  assert.match(source, /ALTER ROLE teruisi_market_reader SET default_transaction_read_only=on/);
  assert.match(source, /ALTER ROLE teruisi_market_writer RESET default_transaction_read_only/);
  assert.match(source, /REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public/);
  assert.match(source, /market writer DML escaped allowlist/);
  assert.match(source, /has_any_column_privilege\('teruisi_market_writer',c\.oid,'INSERT'\)/);
  assert.match(source, /"market_write_authority": \("SELECT",\)/);
  assert.match(health, /MARKET_WRITER_TABLE_PRIVILEGES/);
  assert.match(health, /"market_write_authority": \("SELECT",\)/);
  for (const table of [
    "market_ranking_entries",
    "market_price_snapshots",
    "market_annotation_items",
    "market_netshop_projection",
    "market_write_request_receipts",
  ]) {
    assert.match(source, new RegExp(`"${table}"`));
    assert.match(health, new RegExp(`"${table}"`));
  }
  const privileges = source.match(/writer_privileges = \{[\s\S]*?\n\}/)?.[0] ?? "";
  assert.doesNotMatch(privileges, /market_migration_runs/);
});

test("market writer startup is fenced by immutable PostgreSQL authority evidence", async () => {
  const [source, baseSource, health] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(baseControllerUrl, "utf8"),
    readFile(healthUrl, "utf8"),
  ]);
  assert.match(source, /function Get-MarketWriteAuthority/);
  assert.match(source, /if \(\[string\]\$Authority\.status -cne "postgres"\)/);
  assert.match(source, /authorityEpoch/);
  assert.match(source, /migrationRunId/);
  assert.match(source, /market-service-enabled\.json/);
  assert.match(source, /Test-ExactObjectPropertyNames \$startup/);
  assert.match(baseSource, /TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH/);
  assert.match(baseSource, /TERUISI_DJANGO_MARKET_CUTOVER_ID/);
  assert.match(health, /_validate_market_writer_authority/);
  assert.match(health, /market_writer_authority_mismatch/);
});

test("base deployment and login chain include market without replacing other domain controllers", async () => {
  const source = await readFile(baseControllerUrl, "utf8");
  assert.match(source, /tools\\django-market-service\.ps1/);
  assert.match(source, /drizzle\\0097_market_write_authority\.sql/);
  assert.match(source, /drizzle\\0098_market_domain_retirement\.sql/);
  assert.match(source, /MarketStartupEnabledPath/);
  assert.match(source, /InstalledMarketScriptPath -Action Start/);
  assert.match(source, /InstalledMarketScriptPath -Action Stop/);
  assert.match(source, /django-netshop-service\.ps1/);
  assert.match(source, /--listen=127\.0\.0\.1:8001/);
  assert.match(source, /--listen=127\.0\.0\.1:8011/);
});
