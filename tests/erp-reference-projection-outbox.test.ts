import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return { url: "data:text/javascript,export const env={};", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { ensureErpReferenceSchema, saveProductMasterImport } = await import(
  "../lib/erp-reference/database"
);

type ErpReferenceDatabase = import("../lib/erp-reference/database").ErpReferenceDatabase;
type RunHook = (sql: string, values: readonly SQLInputValue[]) => void;

function sqliteAdapter(sqlite: DatabaseSync, hook: { beforeRun?: RunHook } = {}) {
  let batchTail: Promise<unknown> = Promise.resolve();
  const prepare = (sql: string) => {
    let values: SQLInputValue[] = [];
    return {
      bind(...nextValues: unknown[]) {
        values = nextValues as SQLInputValue[];
        return this;
      },
      async first<T>() {
        return (sqlite.prepare(sql).get(...values) ?? null) as T | null;
      },
      async all<T>() {
        return { results: sqlite.prepare(sql).all(...values) as T[] };
      },
      async run() {
        hook.beforeRun?.(sql, values);
        const result = sqlite.prepare(sql).run(...values);
        return { meta: { changes: Number(result.changes) } };
      },
    };
  };
  return {
    prepare,
    batch(statements: Array<{ run(): Promise<unknown> }>) {
      const execute = async () => {
        sqlite.exec("BEGIN");
        try {
          const results = [];
          for (const statement of statements) results.push(await statement.run());
          sqlite.exec("COMMIT");
          return results;
        } catch (error) {
          sqlite.exec("ROLLBACK");
          throw error;
        }
      };
      const result = batchTail.then(execute, execute);
      batchTail = result.then(() => undefined, () => undefined);
      return result;
    },
  };
}

function productRow(productCode: string, productName: string, sourceRowNumber: number) {
  return {
    sourceRowNumber,
    productCode,
    productName,
    brand: "品牌",
    specification: "规格",
    barcode: "",
    category: "类目",
    supplier: "供应商",
    productStatus: "启用",
  };
}

function input(hash: string, rows = [productRow("P1", "货品1", 1)]) {
  return {
    id: `products:${hash}`,
    fileName: `products-${hash.slice(0, 4)}.xlsx`,
    fileSizeBytes: 100,
    fileHash: hash,
    sheetName: "货品",
    rows,
    warnings: [],
    totals: { source: "test" },
    contentHash: hash,
  };
}

function splitMigration(source: string) {
  return source.split("--> statement-breakpoint").map((item) => item.trim()).filter(Boolean);
}

test("ERP schema initialization preserves the retired inventory-age tombstone", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE VIEW erp_inventory_age_lines AS
      SELECT 'inventory-domain-retired-v1' AS retirement_tombstone WHERE 0;
  `);

  await ensureErpReferenceSchema(sqliteAdapter(sqlite) as unknown as ErpReferenceDatabase);

  assert.equal(
    sqlite.prepare("SELECT type FROM sqlite_master WHERE name='erp_inventory_age_lines'").get()?.type,
    "view",
  );
  assert.equal(
    sqlite.prepare("SELECT type FROM sqlite_master WHERE name='erp_product_master'").get()?.type,
    "table",
  );
  assert.equal(
    sqlite.prepare("SELECT type FROM sqlite_master WHERE name='erp_combo_items'").get()?.type,
    "table",
  );
  sqlite.close();
});

test("0091 bootstraps an ERP-owned stable epoch and revision without historical events", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL, erp_product_revision INTEGER NOT NULL
    );
    INSERT INTO sales_overview_cache_state VALUES (1, 9, 5);
    CREATE TABLE erp_reference_import_batches (
      id TEXT PRIMARY KEY, source_key TEXT NOT NULL, source_label TEXT NOT NULL DEFAULT '',
      file_name TEXT NOT NULL DEFAULT '', file_size_bytes INTEGER NOT NULL DEFAULT 0,
      file_hash TEXT NOT NULL DEFAULT '', sheet_name TEXT NOT NULL DEFAULT '', snapshot_date TEXT,
      status TEXT NOT NULL, row_count INTEGER NOT NULL, inserted_count INTEGER NOT NULL DEFAULT 0,
      updated_count INTEGER NOT NULL DEFAULT 0, excluded_count INTEGER NOT NULL DEFAULT 0,
      warning_count INTEGER NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]',
      totals_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
    );
    CREATE TABLE erp_product_master (
      product_code TEXT PRIMARY KEY, product_name TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
      specification TEXT NOT NULL DEFAULT '', barcode TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT '', supplier TEXT NOT NULL DEFAULT '',
      product_status TEXT NOT NULL DEFAULT '', source_row_number INTEGER NOT NULL,
      last_import_batch_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO erp_reference_import_batches (
      id, source_key, status, row_count, totals_json, created_at, completed_at
    ) VALUES ('erp-baseline', 'products', 'completed', 1, '{"contentHash":"${"a".repeat(64)}"}',
      '2026-08-28 10:00:00', '2026-08-28 10:01:00');
    INSERT INTO erp_product_master (
      product_code, product_name, source_row_number, last_import_batch_id
    ) VALUES ('P1', '货品1', 1, 'erp-baseline');
  `);
  const migration = await readFile(
    new URL("../drizzle/0091_erp_reference_projection.sql", import.meta.url),
    "utf8",
  );
  for (const statement of splitMigration(migration)) sqlite.exec(statement);

  const state = sqlite.prepare(
    "SELECT erp_revision erpRevision, source_batch_id sourceBatchId, row_count rowCount, content_hash contentHash FROM erp_product_projection_state WHERE id=1",
  ).get();
  assert.deepEqual({ ...state! }, {
    erpRevision: 5,
    sourceBatchId: "erp-baseline",
    rowCount: 1,
    contentHash: "a".repeat(64),
  });
  const epoch = String(sqlite.prepare(
    "SELECT source_epoch FROM erp_reference_projection_source_state WHERE id=1",
  ).get()?.source_epoch);
  assert.match(epoch, /^[0-9a-f]{32}$/);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_reference_projection_outbox").get()?.count, 0);

  for (const statement of splitMigration(migration)) sqlite.exec(statement);
  assert.equal(sqlite.prepare(
    "SELECT source_epoch FROM erp_reference_projection_source_state WHERE id=1",
  ).get()?.source_epoch, epoch);
  sqlite.close();
});

test("product replace, ERP revision, completed batch, and ERP-only event publish atomically", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureErpReferenceSchema(db as unknown as ErpReferenceDatabase);

  const first = input("b".repeat(64), [
    productRow("P1", "货品1", 1),
    productRow("P2", "货品2", 2),
  ]);
  assert.equal((await saveProductMasterImport(db as unknown as ErpReferenceDatabase, first)).created, true);
  const changed = input("c".repeat(64), [productRow("P1", "货品1更新", 1)]);
  assert.equal((await saveProductMasterImport(db as unknown as ErpReferenceDatabase, changed)).created, true);

  assert.deepEqual(sqlite.prepare(
    "SELECT product_code productCode, product_name productName FROM erp_product_master",
  ).all().map((row) => ({ ...row })), [{ productCode: "P1", productName: "货品1更新" }]);
  assert.deepEqual({ ...sqlite.prepare(
    "SELECT erp_revision erpRevision, source_batch_id sourceBatchId, row_count rowCount, content_hash contentHash FROM erp_product_projection_state WHERE id=1",
  ).get()! }, {
    erpRevision: 3,
    sourceBatchId: changed.id,
    rowCount: 1,
    contentHash: changed.contentHash,
  });
  const events = sqlite.prepare(
    "SELECT event_sequence eventSequence, event_id eventId, source_epoch sourceEpoch, domain, operation, scope_json scopeJson, erp_revision erpRevision, row_count rowCount, content_hash contentHash, canonical_format_version canonicalVersion FROM erp_reference_projection_outbox ORDER BY event_sequence",
  ).all().map((row) => ({ ...row }));
  assert.equal(events.length, 2);
  assert.ok(events.every((event) => event.domain === "erp" && event.operation === "replace_all"));
  assert.deepEqual(events.map((event) => ({
    eventSequence: event.eventSequence,
    eventId: String(event.eventId).slice(`${event.sourceEpoch}:`.length),
    scopeJson: event.scopeJson,
    erpRevision: event.erpRevision,
    rowCount: event.rowCount,
    contentHash: event.contentHash,
    canonicalVersion: event.canonicalVersion,
  })), [
    {
      eventSequence: 1,
      eventId: `erp:${first.id}`,
      scopeJson: JSON.stringify({ source: "products" }),
      erpRevision: 2,
      rowCount: 2,
      contentHash: first.contentHash,
      canonicalVersion: "erp-reference-projection-v1",
    },
    {
      eventSequence: 2,
      eventId: `erp:${changed.id}`,
      scopeJson: JSON.stringify({ source: "products" }),
      erpRevision: 3,
      rowCount: 1,
      contentHash: changed.contentHash,
      canonicalVersion: "erp-reference-projection-v1",
    },
  ]);

  assert.equal((await saveProductMasterImport(db as unknown as ErpReferenceDatabase, changed)).created, false);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_reference_projection_outbox").get()?.count, 2);
  sqlite.close();
});

test("an ERP outbox failure rolls back products, state, and import batch", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let rejectOutbox = false;
  const db = sqliteAdapter(sqlite, {
    beforeRun(sql) {
      if (rejectOutbox && sql.includes("INSERT INTO erp_reference_projection_outbox")) {
        throw new Error("injected ERP outbox failure");
      }
    },
  });
  await ensureErpReferenceSchema(db as unknown as ErpReferenceDatabase);
  const baseline = input("d".repeat(64), [
    productRow("P1", "货品1", 1),
    productRow("P2", "货品2", 2),
  ]);
  await saveProductMasterImport(db as unknown as ErpReferenceDatabase, baseline);
  rejectOutbox = true;

  const rejected = input("e".repeat(64), [productRow("P1", "不应发布", 1)]);
  await assert.rejects(
    saveProductMasterImport(db as unknown as ErpReferenceDatabase, rejected),
    /injected ERP outbox failure/,
  );

  assert.deepEqual(sqlite.prepare(
    "SELECT product_code productCode, product_name productName FROM erp_product_master ORDER BY product_code",
  ).all().map((row) => ({ ...row })), [
    { productCode: "P1", productName: "货品1" },
    { productCode: "P2", productName: "货品2" },
  ]);
  assert.equal(sqlite.prepare(
    "SELECT erp_revision FROM erp_product_projection_state WHERE id=1",
  ).get()?.erp_revision, 2);
  assert.equal(sqlite.prepare(
    "SELECT COUNT(*) count FROM erp_reference_import_batches WHERE id=?",
  ).get(rejected.id)?.count, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_reference_projection_outbox").get()?.count, 1);
  sqlite.close();
});

test("ERP stream schema rejects sales events and protects append-only state", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureErpReferenceSchema(db as unknown as ErpReferenceDatabase);
  const published = input("f".repeat(64));
  await saveProductMasterImport(db as unknown as ErpReferenceDatabase, published);
  const epoch = String(sqlite.prepare(
    "SELECT source_epoch FROM erp_reference_projection_source_state WHERE id=1",
  ).get()?.source_epoch);

  assert.throws(() => sqlite.prepare(
    "INSERT INTO erp_reference_projection_outbox (event_id, source_epoch, domain, operation, scope_json, source_batch_id, erp_revision, row_count, content_hash, canonical_format_version) VALUES (?, ?, 'sales', 'replace_all', ?, ?, 2, 1, ?, ?)",
  ).run(
    `${epoch}:sales:${published.id}`,
    epoch,
    JSON.stringify({ source: "products" }),
    published.id,
    published.contentHash,
    "erp-reference-projection-v1",
  ));
  assert.throws(() => sqlite.exec("DELETE FROM erp_reference_projection_outbox"), /append-only/);
  assert.throws(() => sqlite.exec(
    "UPDATE erp_reference_projection_source_state SET source_epoch='00000000000000000000000000000000' WHERE id=1",
  ), /immutable/);
  sqlite.close();
});

test("runtime schema refuses to invent revision one for a non-empty legacy ERP master", async () => {
  const sqlite = new DatabaseSync(":memory:");
  await ensureErpReferenceSchema(sqliteAdapter(sqlite) as unknown as ErpReferenceDatabase);
  sqlite.prepare(
    "INSERT INTO erp_product_master (product_code, product_name, source_row_number, last_import_batch_id) VALUES ('LEGACY', '历史货品', 1, 'legacy-batch')",
  ).run();
  sqlite.exec("DROP TABLE erp_product_projection_state");
  await ensureErpReferenceSchema(sqliteAdapter(sqlite) as unknown as ErpReferenceDatabase);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM erp_product_projection_state").get()?.count, 0);

  await assert.rejects(
    saveProductMasterImport(
      sqliteAdapter(sqlite) as unknown as ErpReferenceDatabase,
      input("9".repeat(64), [productRow("P1", "新货品", 1)]),
    ),
    /requires projection event/,
  );
  assert.deepEqual(sqlite.prepare(
    "SELECT product_code productCode, product_name productName FROM erp_product_master",
  ).all().map((row) => ({ ...row })), [{ productCode: "LEGACY", productName: "历史货品" }]);
  sqlite.close();
});
