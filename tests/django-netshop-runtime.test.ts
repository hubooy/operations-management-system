import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const controllerUrl = new URL("../tools/django-netshop-service.ps1", import.meta.url);
const baseControllerUrl = new URL("../tools/django-local-service.ps1", import.meta.url);

test("netshop runtime uses independent fixed reader/writer endpoints and process receipts", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /127\.0\.0\.1:8021\/health\/ready/);
  assert.match(source, /127\.0\.0\.1:8022\/health\/ready/);
  assert.match(source, /django-netshop-reader\.pid\.json/);
  assert.match(source, /django-netshop-writer\.pid\.json/);
  assert.match(source, /--listen=127\.0\.0\.1:8021/);
  assert.match(source, /--listen=127\.0\.0\.1:8022/);
  assert.match(source, /Stop-OwnedProcess "django-netshop-reader"/);
  assert.match(source, /Stop-OwnedProcess "django-netshop-writer"/);
});

test("netshop role provisioning safely quotes passwords and enforces a dynamic DML allowlist", async () => {
  const source = await readFile(controllerUrl, "utf8");
  assert.match(source, /sql\.Literal\(password\)/);
  assert.doesNotMatch(source, /PASSWORD %s/);
  assert.match(source, /CREATE ROLE \{\} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE/);
  assert.match(source, /NOINHERIT NOREPLICATION NOBYPASSRLS/);
  assert.match(source, /ALTER ROLE teruisi_netshop_reader SET default_transaction_read_only=on/);
  assert.match(source, /ALTER ROLE teruisi_netshop_writer RESET default_transaction_read_only/);
  assert.match(source, /REVOKE CREATE ON DATABASE/);
  assert.match(source, /REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public/);
  assert.match(source, /netshop writer DML escaped allowlist/);
  assert.match(source, /has_any_column_privilege\('teruisi_netshop_writer',c\.oid,'INSERT'\)/);
  assert.match(source, /"netshop_write_authority": \("SELECT",\)/);
  assert.doesNotMatch(
    source.match(/writer_privileges = \{[\s\S]*?\n\}/)?.[0] ?? "",
    /netshop_migration_runs/,
  );
});

test("netshop writer startup is fenced by PostgreSQL authority and immutable startup evidence", async () => {
  const source = await readFile(controllerUrl, "utf8");
  const baseSource = await readFile(baseControllerUrl, "utf8");
  assert.match(source, /function Get-NetshopWriteAuthority/);
  assert.match(source, /if \(\[string\]\$Authority\.status -cne "postgres"\)/);
  assert.match(source, /authorityEpoch/);
  assert.match(source, /migrationRunId/);
  assert.match(source, /netshop-service-enabled\.json/);
  assert.match(baseSource, /TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH/);
  assert.match(baseSource, /TERUISI_DJANGO_NETSHOP_CUTOVER_ID/);
  assert.match(source, /Test-ExactObjectPropertyNames \$startup/);
});

test("base deployment includes the netshop controller and all operator migrations", async () => {
  const source = await readFile(baseControllerUrl, "utf8");
  assert.match(source, /django-netshop-service\.ps1/);
  assert.match(source, /drizzle\\0094_netshop_write_authority\.sql/);
  assert.match(source, /drizzle\\0095_market_netshop_projection\.sql/);
  assert.match(source, /drizzle\\0096_netshop_domain_retirement\.sql/);
});
