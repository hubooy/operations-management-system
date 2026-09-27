import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const migrationDirectory = new URL("../drizzle/", import.meta.url);

function statements(source: string) {
  return source.split("--> statement-breakpoint").map((value) => value.trim()).filter(Boolean);
}

async function apply(sqlite: DatabaseSync, name: string) {
  const source = await readFile(new URL(name, migrationDirectory), "utf8");
  for (const statement of statements(source)) sqlite.exec(statement);
}

async function createPreCutoverSchema(sqlite: DatabaseSync) {
  const normal = (await readdir(migrationDirectory))
    .filter((name) => /^\d{4}_.+\.sql$/.test(name) && Number(name.slice(0, 4)) <= 91)
    .sort();
  for (const name of normal) await apply(sqlite, name);
  await apply(sqlite, "0099_product_write_authority.sql");
}

test("product D1 authority fences every legacy write surface and retirement is terminal", async () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    await createPreCutoverSchema(sqlite);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT owner,epoch,cutover_id cutoverId FROM product_write_authority WHERE id=1").get() },
      { owner: "d1", epoch: 1, cutoverId: "" },
    );
    assert.throws(
      () => sqlite.exec("UPDATE product_write_authority SET owner='postgresql',epoch=2,cutover_id='products-test' WHERE id=1"),
      /product_write_authority_invalid_transition/,
    );
    sqlite.exec("UPDATE product_write_authority SET owner='pending',epoch=2,cutover_id='products-test' WHERE id=1");
    assert.throws(
      () => sqlite.exec("INSERT INTO product_shipping_rate_import_batches DEFAULT VALUES"),
      /product_write_authority_not_d1/,
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO import_content_attempts(domain) VALUES ('product-shipping-rates')"),
      /product_write_authority_not_d1/,
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO inventory_import_uploads(fingerprint) VALUES ('sku-shipping-rates:old')"),
      /product_write_authority_not_d1/,
    );
    sqlite.exec("UPDATE product_write_authority SET owner='postgresql',epoch=3,cutover_id='products-test' WHERE id=1");

    const retirementSource = await readFile(new URL("0100_product_domain_retirement.sql", migrationDirectory), "utf8");
    const retirementStatements = statements(retirementSource);
    for (const statement of retirementStatements.slice(0, 4)) sqlite.exec(statement);
    sqlite.prepare(`INSERT INTO domain_retirement_receipts (
      domain,version,status,cutover_id,plan_id,attestation_sha256,
      smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,
      audit_id,preserved_evidence_sha256,created_at,completed_at
    ) VALUES ('products','products-domain-retirement-receipt-v1','approved',?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, NULL)`).run(
      "products-test",
      "a".repeat(64),
      "b".repeat(64),
      "c".repeat(64),
      "d".repeat(64),
      "e".repeat(64),
      "f".repeat(64),
      "0".repeat(64),
    );
    for (const statement of retirementStatements.slice(4)) sqlite.exec(statement);

    const tombstones = [...retirementSource.matchAll(/CREATE VIEW `([^`]+)` AS/g)].map((match) => match[1]!);
    assert.deepEqual(tombstones.sort(), [
      "product_shipping_rate_import_batches",
      "product_shipping_rates",
      "product_write_authority",
    ]);
    const guards = sqlite.prepare(
      "SELECT COUNT(*) count FROM sqlite_master WHERE type='trigger' AND name LIKE 'product_retired_%_guard'",
    ).get() as { count: number };
    assert.equal(guards.count, 18);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT domain,status,cutover_id cutoverId FROM domain_retirement_receipts WHERE domain='products'").get() },
      { domain: "products", status: "completed", cutoverId: "products-test" },
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO product_shipping_rates DEFAULT VALUES"),
      /cannot modify product_shipping_rates because it is a view/,
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO import_content_attempts(domain) VALUES ('product-shipping-rates')"),
      /product_domain_retired/,
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO inventory_import_uploads(fingerprint) VALUES ('sku-shipping-rates:new')"),
      /product_domain_retired/,
    );
  } finally {
    sqlite.close();
  }
});
