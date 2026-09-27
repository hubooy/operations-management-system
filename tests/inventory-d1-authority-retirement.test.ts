import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const migrationDirectory = new URL("../drizzle/", import.meta.url);
const LEGACY_INVENTORY_AGE_SCOPE_KEY = "c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4";

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
  await apply(sqlite, "0101_inventory_write_authority.sql");
}

function insertAttempt(sqlite: DatabaseSync, domain: string, attemptId: string) {
  sqlite.prepare(`INSERT INTO import_content_attempts (
    attempt_id,domain,batch_id,scope_key,scope_json,import_hash,
    raw_file_hash,content_hash,row_count,outcome
  ) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    attemptId,
    domain,
    `${attemptId}-batch`,
    `${attemptId}-scope`,
    "{}",
    "a".repeat(64),
    "b".repeat(64),
    "c".repeat(64),
    1,
    "completed",
  );
}

function insertUpload(sqlite: DatabaseSync, fingerprint: string, id: string) {
  sqlite.prepare(`INSERT INTO inventory_import_uploads (
    id,fingerprint,file_name,file_size_bytes,chunk_size_bytes,chunk_count,expires_at
  ) VALUES (?,?,?,?,?,?,?)`).run(
    id,
    fingerprint,
    "fixture.xlsx",
    1,
    1,
    1,
    "2099-01-01T00:00:00Z",
  );
}

test("inventory D1 authority fences exact namespaces and retirement preserves other domains", async () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    await createPreCutoverSchema(sqlite);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT owner,epoch,cutover_id cutoverId FROM inventory_write_authority WHERE id=1").get() },
      { owner: "d1", epoch: 1, cutoverId: "" },
    );

    insertAttempt(sqlite, "product-shipping-rates", "other-attempt");
    insertUpload(sqlite, "sku-shipping-rates:other-domain", "other-upload");
    sqlite.exec("INSERT INTO system_settings(key,value_json) VALUES ('non-inventory-setting','{}')");
    sqlite.prepare(`INSERT INTO erp_reference_import_batches (
      id,source_key,source_label,file_name,file_size_bytes,file_hash,sheet_name,
      snapshot_date,status,completed_at
    ) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)`).run(
      "inventory_age:test-batch",
      "inventory_age",
      "库存库龄",
      "age.xlsx",
      1,
      "d".repeat(64),
      "库龄",
      "2026-09-01",
      "completed",
    );
    sqlite.prepare(`INSERT INTO import_scope_heads (
      domain,scope_key,state_token,status,owner_token,current_batch_id,generation
    ) VALUES (?,?,?,?,?,?,?)`).run(
      "erp-reference",
      LEGACY_INVENTORY_AGE_SCOPE_KEY,
      "1".repeat(64),
      "ready",
      "",
      "inventory_age:test-batch",
      1,
    );
    sqlite.prepare(`INSERT INTO import_scope_heads (
      domain,scope_key,state_token,status,owner_token,current_batch_id,generation
    ) VALUES (?,?,?,?,?,?,?)`).run(
      "erp-reference",
      "7".repeat(64),
      "2".repeat(64),
      "ready",
      "",
      "",
      1,
    );

    assert.throws(
      () => sqlite.exec("UPDATE inventory_write_authority SET owner='postgresql',epoch=2,cutover_id='inventory-test' WHERE id=1"),
      /inventory_write_authority_invalid_transition/,
    );
    sqlite.exec("UPDATE inventory_write_authority SET owner='pending',epoch=2,cutover_id='inventory-test' WHERE id=1");
    assert.throws(
      () => sqlite.exec("INSERT INTO inventory_import_batches DEFAULT VALUES"),
      /inventory_write_authority_not_d1/,
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO erp_reference_import_batches(source_key) VALUES ('inventory_age')"),
      /inventory_write_authority_not_d1/,
    );
    assert.throws(
      () => insertAttempt(sqlite, "inventory-stock", "blocked-attempt"),
      /inventory_write_authority_not_d1/,
    );
    assert.throws(
      () => insertUpload(sqlite, "erp:inventory_age:new", "blocked-upload"),
      /inventory_write_authority_not_d1/,
    );
    assert.throws(
      () => sqlite.prepare(
        "UPDATE import_scope_heads SET generation=generation WHERE domain='erp-reference' AND scope_key=?",
      ).run(LEGACY_INVENTORY_AGE_SCOPE_KEY),
      /inventory_write_authority_not_d1/,
    );
    sqlite.exec("UPDATE inventory_write_authority SET owner='postgresql',epoch=3,cutover_id='inventory-test' WHERE id=1");

    const retirementSource = await readFile(new URL("0102_inventory_domain_retirement.sql", migrationDirectory), "utf8");
    const retirementStatements = statements(retirementSource);
    for (const statement of retirementStatements.slice(0, 4)) sqlite.exec(statement);
    sqlite.prepare(`INSERT INTO domain_retirement_receipts (
      domain,version,status,cutover_id,plan_id,attestation_sha256,
      smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,
      audit_id,preserved_evidence_sha256,created_at,completed_at
    ) VALUES ('inventory','inventory-domain-retirement-receipt-v1','approved',?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, NULL)`).run(
      "inventory-test",
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
      "erp_inventory_age_lines",
      "inventory_age_metrics",
      "inventory_import_batches",
      "inventory_stock_lines",
      "inventory_write_authority",
      "replenishment_plan_items",
    ]);
    const guards = sqlite.prepare(
      "SELECT COUNT(*) count FROM sqlite_master WHERE type='trigger' AND name LIKE 'inventory_retired_%_guard'",
    ).get() as { count: number };
    assert.equal(guards.count, 24);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT domain,status,cutover_id cutoverId FROM domain_retirement_receipts WHERE domain='inventory'").get() },
      { domain: "inventory", status: "completed", cutoverId: "inventory-test" },
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO inventory_stock_lines DEFAULT VALUES"),
      /cannot modify inventory_stock_lines because it is a view/,
    );
    assert.throws(
      () => insertAttempt(sqlite, "inventory-stock", "retired-attempt"),
      /inventory_domain_retired/,
    );
    assert.throws(
      () => sqlite.exec("INSERT INTO system_settings(key,value_json) VALUES ('operating','{}')"),
      /inventory_domain_retired/,
    );
    assert.throws(
      () => insertUpload(sqlite, "inventory-v1:new", "retired-upload"),
      /inventory_domain_retired/,
    );
    assert.throws(
      () => sqlite.prepare(`INSERT INTO import_scope_heads (
        domain,scope_key,state_token,status,owner_token,current_batch_id,generation
      ) VALUES ('erp-reference',?,?,'ready','',NULL,0)`).run(
        LEGACY_INVENTORY_AGE_SCOPE_KEY,
        "3".repeat(64),
      ),
      /inventory_domain_retired/,
    );
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM import_scope_heads WHERE scope_key=?")
        .get(LEGACY_INVENTORY_AGE_SCOPE_KEY)?.count,
      0,
    );
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM import_scope_heads WHERE scope_key=?")
        .get("7".repeat(64))?.count,
      1,
    );

    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM import_content_attempts WHERE domain='product-shipping-rates'").get()?.count,
      1,
    );
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM inventory_import_uploads WHERE fingerprint='sku-shipping-rates:other-domain'").get()?.count,
      1,
    );
    insertUpload(sqlite, "erp:products::retained-upload", "retained-upload");
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM inventory_import_uploads WHERE id='retained-upload'").get()?.count,
      1,
    );
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) count FROM system_settings WHERE key='non-inventory-setting'").get()?.count,
      1,
    );
  } finally {
    sqlite.close();
  }
});
