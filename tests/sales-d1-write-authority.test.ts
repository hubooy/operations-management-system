import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import {
  inspectSalesD1WriteAuthority,
  parseSalesD1WriteAuthorityArguments,
  transitionSalesD1WriteAuthority,
} from "../tools/sales-d1-write-authority";

async function database() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
    CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE sales_import_uploads (
      id TEXT PRIMARY KEY, status TEXT NOT NULL, expires_at TEXT NOT NULL DEFAULT '2999-01-01T00:00:00Z'
    );
    CREATE TABLE sales_import_upload_chunks (upload_id TEXT, chunk_index INTEGER);
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL, erp_product_revision INTEGER NOT NULL
    );
    INSERT INTO sales_overview_cache_state VALUES (1, 1, 1);
    CREATE TABLE sales_projection_source_state (
      id INTEGER PRIMARY KEY, source_epoch TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO sales_projection_source_state (id, source_epoch) VALUES (1, 'source-epoch-1');
    CREATE TABLE sales_overview_response_cache (cache_key TEXT PRIMARY KEY, payload_json TEXT);
    CREATE TABLE sales_projection_outbox (event_sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL);
    CREATE TABLE import_content_fingerprints (
      sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'completed'
    );
    CREATE TABLE import_content_attempts (
      sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, outcome TEXT NOT NULL
    );
    CREATE TABLE import_scope_heads (
      domain TEXT NOT NULL, scope_key TEXT NOT NULL, status TEXT NOT NULL,
      PRIMARY KEY (domain, scope_key)
    );
  `);
  const migration = await readFile(new URL("../drizzle/0090_sales_write_authority.sql", import.meta.url), "utf8");
  sqlite.exec(migration.replaceAll("--> statement-breakpoint", ""));
  return sqlite;
}

test("D1 sales authority defaults to D1 and fences every sales-owned write after pending", async () => {
  const sqlite = await database();
  try {
    sqlite.prepare("INSERT INTO sales_order_lines (id) VALUES (1)").run();
    const before = inspectSalesD1WriteAuthority(sqlite);
    assert.equal(before.owner, "d1");
    assert.equal(before.epoch, 1);

    const pending = transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1",
      expectedEpoch: 1,
      targetOwner: "pending",
      cutoverId: "cutover-test-001",
    });
    assert.equal(pending.owner, "pending");
    assert.equal(pending.epoch, 2);

    for (const sql of [
      "INSERT INTO sales_order_lines (id) VALUES (2)",
      "UPDATE sales_order_lines SET value = 'late' WHERE id = 1",
      "DELETE FROM sales_order_lines WHERE id = 1",
      "INSERT INTO sales_import_batches (id, status) VALUES ('late', 'processing')",
      "INSERT INTO sales_import_uploads (id, status) VALUES ('late', 'uploading')",
      "INSERT INTO sales_import_upload_chunks (upload_id, chunk_index) VALUES ('late', 0)",
      "INSERT INTO sales_overview_response_cache (cache_key, payload_json) VALUES ('late', '{}')",
      "UPDATE sales_overview_cache_state SET sales_revision = sales_revision + 1 WHERE id = 1",
      "DELETE FROM sales_overview_cache_state WHERE id = 1",
      "UPDATE sales_projection_source_state SET source_epoch = 'late' WHERE id = 1",
      "DELETE FROM sales_projection_source_state WHERE id = 1",
      "INSERT INTO sales_projection_outbox (event_sequence, domain) VALUES (1, 'sales')",
      "INSERT INTO import_content_fingerprints (sequence, domain) VALUES (1, 'sales')",
      "INSERT INTO import_content_attempts (sequence, domain, outcome) VALUES (1, 'sales', 'failed')",
      "INSERT INTO import_scope_heads (domain, scope_key, status) VALUES ('sales', 'sales', 'ready')",
    ]) {
      assert.throws(() => sqlite.exec(sql), /sales_write_authority_not_d1/);
    }

    sqlite.exec("INSERT INTO sales_projection_outbox (event_sequence, domain) VALUES (2, 'erp')");
    sqlite.exec("INSERT INTO import_content_attempts (sequence, domain, outcome) VALUES (2, 'inventory', 'completed')");
    sqlite.exec("UPDATE sales_overview_cache_state SET erp_product_revision = erp_product_revision + 1 WHERE id = 1");
    sqlite.exec("INSERT OR IGNORE INTO sales_overview_cache_state VALUES (1, 1, 1)");
    sqlite.exec("INSERT OR IGNORE INTO sales_projection_source_state (id, source_epoch) VALUES (1, 'ignored')");
    assert.equal(sqlite.prepare("SELECT erp_product_revision value FROM sales_overview_cache_state WHERE id=1").get()?.value, 2);
    assert.equal(sqlite.prepare("SELECT source_epoch value FROM sales_projection_source_state WHERE id=1").get()?.value, "source-epoch-1");
  } finally {
    sqlite.close();
  }
});

test("authority transition is quiescence-gated, CAS-fenced, and cannot roll back after PG activation", async () => {
  const sqlite = await database();
  try {
    sqlite.exec("INSERT INTO sales_import_uploads (id, status, expires_at) VALUES ('active', 'uploading', '2999-01-01T00:00:00Z')");
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1", expectedEpoch: 1, targetOwner: "pending", cutoverId: "cutover-test-002",
    }), /尚未静默/);
    sqlite.exec("DELETE FROM sales_import_uploads WHERE id = 'active'");
    sqlite.exec("INSERT INTO sales_import_uploads (id, status, expires_at) VALUES ('expired', 'ready', '2000-01-01T00:00:00Z')");

    sqlite.exec("INSERT INTO sales_import_uploads (id, status, expires_at) VALUES ('invalid', 'ready', 'not-a-time')");
    const malformed = inspectSalesD1WriteAuthority(sqlite);
    assert.equal(malformed.blockers.invalidUploadExpiries, 1);
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1", expectedEpoch: 1, targetOwner: "pending", cutoverId: "cutover-test-002",
    }), /尚未静默/);
    sqlite.exec("DELETE FROM sales_import_uploads WHERE id = 'invalid'");

    sqlite.exec("INSERT INTO sales_import_upload_chunks (upload_id, chunk_index) VALUES ('expired', 0)");
    const retainedChunk = inspectSalesD1WriteAuthority(sqlite);
    assert.equal(retainedChunk.blockers.uploadChunks, 1);
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1", expectedEpoch: 1, targetOwner: "pending", cutoverId: "cutover-test-002",
    }), /尚未静默/);
    sqlite.exec("DELETE FROM sales_import_upload_chunks");

    sqlite.exec("INSERT INTO import_content_fingerprints (sequence, domain, status) VALUES (1, 'sales', 'processing')");
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1", expectedEpoch: 1, targetOwner: "pending", cutoverId: "cutover-test-002",
    }), /尚未静默/);
    sqlite.exec("DELETE FROM import_content_fingerprints WHERE sequence = 1");

    transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1", expectedEpoch: 1, targetOwner: "pending", cutoverId: "cutover-test-002",
    });
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "pending", expectedEpoch: 1, targetOwner: "postgresql", cutoverId: "cutover-test-002",
    }), /已变化/);
    const active = transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "pending", expectedEpoch: 2, targetOwner: "postgresql", cutoverId: "cutover-test-002",
    });
    assert.equal(active.owner, "postgresql");
    assert.equal(active.epoch, 3);
    assert.throws(() => sqlite.exec(
      "UPDATE sales_write_authority SET owner='d1', epoch=4, cutover_id='cutover-test-002' WHERE id=1",
    ), /sales_write_authority_invalid_transition/);
    assert.throws(() => sqlite.exec("DELETE FROM sales_write_authority WHERE id=1"), /sales_write_authority_delete_forbidden/);
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "postgresql", expectedEpoch: 3, targetOwner: "d1" as never, cutoverId: "cutover-test-002",
    }), /只允许/);
  } finally {
    sqlite.close();
  }
});

test("pending is a point of no return in SQL, the operator API, and the CLI", async () => {
  const sqlite = await database();
  try {
    transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "d1", expectedEpoch: 1, targetOwner: "pending", cutoverId: "cutover-test-003",
    });
    assert.throws(() => sqlite.exec(`
      INSERT OR REPLACE INTO sales_write_authority
        (id, owner, epoch, cutover_id, updated_at)
      VALUES (1, 'd1', 3, 'cutover-test-003', CURRENT_TIMESTAMP)
    `), /sales_write_authority_recreate_forbidden/);
    assert.throws(() => sqlite.exec(
      "UPDATE sales_write_authority SET owner='d1', epoch=3, cutover_id='cutover-test-003' WHERE id=1",
    ), /sales_write_authority_invalid_transition/);
    assert.throws(() => transitionSalesD1WriteAuthority(sqlite, {
      expectedOwner: "pending", expectedEpoch: 2, targetOwner: "d1" as never, cutoverId: "cutover-test-003",
    }), /不可回退点/);
    assert.throws(() => parseSalesD1WriteAuthorityArguments([
      "--source", path.resolve("unused.sqlite"),
      "--expected-owner", "pending",
      "--expected-epoch", "2",
      "--target-owner", "d1",
      "--cutover-id", "cutover-test-003",
    ]), /--target-owner 只允许 pending 或 postgresql/);
    assert.throws(() => parseSalesD1WriteAuthorityArguments([
      "--source", path.resolve("unused.sqlite"),
      "--inspect",
      "--target-owner", "postgresql",
    ]), /--inspect 只接受 --source/);
    assert.throws(() => parseSalesD1WriteAuthorityArguments([
      "--source", path.resolve("unused.sqlite"),
      "--source", path.resolve("unused.sqlite"),
      "--inspect",
    ]), /参数重复/);
    const pending = inspectSalesD1WriteAuthority(sqlite);
    assert.equal(pending.owner, "pending");
    assert.equal(pending.epoch, 2);
    assert.throws(() => sqlite.exec(
      "INSERT INTO sales_import_batches (id, status) VALUES ('restored', 'completed')",
    ), /sales_write_authority_not_d1/);
  } finally {
    sqlite.close();
  }
});
