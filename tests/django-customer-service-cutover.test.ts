import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("customer-service cutover retires its exact D1 and legacy R2 paths", async () => {
  const [
    controller,
    service,
    authority,
    retirementSql,
    retirementCommand,
    r2Evidence,
    smoke,
    d1Smoke,
    chunkRoute,
    chunkClient,
    customerServiceLauncher,
    consumerSmoke,
  ] = await Promise.all([
    readFile(new URL("../tools/django-customer-service-cutover.ps1", import.meta.url), "utf8"),
    readFile(new URL("../tools/django-local-service.ps1", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0107_customer_service_write_authority.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0108_customer_service_domain_retirement.sql", import.meta.url), "utf8"),
    readFile(new URL("../backend/customer_service/management/commands/retire_customer_service_d1.py", import.meta.url), "utf8"),
    readFile(new URL("../tools/customer-service-r2-retirement-evidence.py", import.meta.url), "utf8"),
    readFile(new URL("../tools/customer-service-production-smoke.ps1", import.meta.url), "utf8"),
    readFile(new URL("../tools/customer-service-d1-rejection-smoke.py", import.meta.url), "utf8"),
    readFile(new URL("../app/api/customer-service/import/chunks/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/customer-service/chunked-upload.ts", import.meta.url), "utf8"),
    readFile(new URL("../tools/django-customer-service.ps1", import.meta.url), "utf8"),
    readFile(new URL("../tools/customer-service-consumer-smoke.ts", import.meta.url), "utf8"),
  ]);

  assert.match(controller, /"R2Evidence",\s*"RetirementPlan",\s*"RetirementApply"/);
  assert.match(controller, /Assert-CustomerServiceWorkerStopped/);
  assert.match(controller, /Assert-CustomerServiceStackStopped/);
  assert.match(controller, /\$Mode -eq "dry-run"[\s\S]*?\$arguments \+= "--plan"/);
  assert.match(controller, /customer-service-r2-retirement-evidence\.py/);
  assert.match(controller, /retire_customer_service_d1/);
  for (const file of [
    "customer-service-production-smoke.ps1",
    "customer-service-consumer-smoke.ts",
    "customer-service-d1-rejection-smoke.py",
    "customer-service-r2-retirement-evidence.py",
  ]) assert.match(service, new RegExp(file.replaceAll(".", "\\.")));

  assert.match(authority, /customer_service_uploads_insert_guard/);
  assert.match(authority, /fingerprint` LIKE 'customer-service:%'/);
  assert.match(retirementSql, /customer-service-domain-retired-v1/);
  assert.match(retirementSql, /DROP TABLE `customer_service_write_authority`/);
  assert.match(retirementSql, /DELETE FROM `inventory_import_uploads` WHERE `fingerprint` LIKE 'customer-service:%'/);
  assert.equal((retirementSql.match(/CREATE TRIGGER `customer_service_retired_[^`]+_guard`/g) ?? []).length, 18);
  assert.match(retirementCommand, /R2_EVIDENCE_VERSION = "customer-service-r2-retirement-evidence-v1"/);
  assert.match(retirementCommand, /r2EvidenceSha256/);
  assert.match(retirementCommand, /_shared_receipt\(source\) != plan\["shared"\]/);
  assert.match(r2Evidence, /PREFIX = "inventory-upload\/"/);
  assert.match(r2Evidence, /legacy customer-service R2 namespace is not empty/);
  assert.match(smoke, /customer-service-system-test-receipt-v1/);
  assert.match(smoke, /Join-Path \$ReleaseRoot "source-snapshot"/);
  assert.match(smoke, /TERUISI_DJANGO_CUSTOMER_SERVICE_MODE/);
  assert.match(smoke, /TERUISI_DJANGO_CUSTOMER_SERVICE_READER_BASE_URL/);
  assert.match(smoke, /TERUISI_DJANGO_CUSTOMER_SERVICE_WRITER_BASE_URL/);
  assert.match(smoke, /legacyD1Rejected = "passed"/);
  assert.match(smoke, /legacyR2Rejected = "passed"/);
  assert.doesNotMatch(smoke, /\$home\s*=/i);
  assert.match(d1Smoke, /customer_service_authority_not_legacy/);

  for (const source of [chunkRoute, chunkClient]) {
    assert.doesNotMatch(source, /SALES_IMPORT_FILES|R2Bucket|beginInventoryUpload|getCustomerServiceDatabase/);
  }

  assert.match(
    customerServiceLauncher,
    /"customer_service_import_fingerprints": \("SELECT", "INSERT"\)/,
  );
  assert.doesNotMatch(
    customerServiceLauncher,
    /"customer_service_migration_runs": \("SELECT", "INSERT", "UPDATE"\)/,
  );
  assert.match(
    customerServiceLauncher,
    /for table in \(\s*"customer_service_conversations", "customer_service_import_fingerprints",\s*"customer_service_raw_upload_chunks",\s*\):/,
  );
  assert.doesNotMatch(consumerSmoke, /lib\/customer-service\/database/);
  assert.match(consumerSmoke, /createDjangoCustomerService/);
  assert.match(consumerSmoke, /page=1&pageSize=1&includeOptions=false/);
});
