import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import { dailyRowKey } from "../lib/netshop/daily-contract";
import { importNetshopBytes } from "../lib/netshop/import-service";
import {
  DAILY_ROW_NATURAL_IDENTITY_INDEX_NAME,
  DAILY_ROW_NATURAL_IDENTITY_INDEX_SQL,
  DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES,
  DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE,
  DAILY_ROW_NATURAL_KEY_MIGRATION,
  DAILY_ROW_NATURAL_KEY_LOSER_PROBE_SQL,
  ensureDailyRowNaturalKeys,
  NetshopSchemaUpgradePendingError,
  type DailyRowMigrationDatabase,
} from "../lib/netshop/daily-row-migration";

type MigrationObservation = { allSizes: number[]; batchSizes: number[] };

function sqliteAdapter(sqlite: DatabaseSync, observation?: MigrationObservation): DailyRowMigrationDatabase {
  return {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (statement.get(...values) ?? null) as T | null; },
        async all<T>() {
          const results = statement.all(...values) as T[];
          observation?.allSizes.push(results.length);
          return { results };
        },
        async run() { return statement.run(...values); },
      };
    },
    async batch(statements) {
      observation?.batchSizes.push(statements.length);
      sqlite.exec("BEGIN");
      try {
        for (const statement of statements) await statement.run();
        sqlite.exec("COMMIT");
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  } as DailyRowMigrationDatabase;
}

function createSchema(sqlite: DatabaseSync) {
  sqlite.exec(`
    CREATE TABLE netshop_schema_migrations (
      migration_key TEXT PRIMARY KEY,
      completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE netshop_import_batches (
      id TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      completed_at TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE netshop_rows (
      id INTEGER PRIMARY KEY,
      source_row_key TEXT NOT NULL UNIQUE,
      last_import_batch_id TEXT NOT NULL,
      source TEXT NOT NULL,
      dataset TEXT NOT NULL,
      platform TEXT NOT NULL,
      shop_name TEXT NOT NULL,
      business_date TEXT,
      sku_id TEXT NOT NULL DEFAULT '',
      spu_id TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL
    );
    CREATE TABLE row_update_audit (count INTEGER NOT NULL);
    INSERT INTO row_update_audit VALUES (0);
    CREATE TRIGGER audit_netshop_update AFTER UPDATE ON netshop_rows BEGIN
      UPDATE row_update_audit SET count=count+1;
    END;
  `);
  sqlite.exec(DAILY_ROW_NATURAL_IDENTITY_INDEX_SQL);
}

test("daily natural-key migration marks an already-correct database without rewriting rows", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSchema(sqlite);
  const key = dailyRowKey("sku_daily", "京东", "测试店", "2026-07-29", "SKU-1");
  sqlite.prepare("INSERT INTO netshop_import_batches VALUES ('batch-1','completed','2026-07-30 08:00:00','2026-07-30 07:00:00')").run();
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source_row_key,last_import_batch_id,source,dataset,platform,shop_name,business_date,sku_id,updated_at)
    VALUES (1,?,'batch-1','jd_sku_daily','sku_daily','京东','测试店','2026-07-29','SKU-1','2026-07-30 08:00:00')`).run(key);

  const db = sqliteAdapter(sqlite);
  await ensureDailyRowNaturalKeys(db);
  await ensureDailyRowNaturalKeys(db);

  assert.equal((sqlite.prepare("SELECT count FROM row_update_audit").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT updated_at FROM netshop_rows").get() as { updated_at: string }).updated_at, "2026-07-30 08:00:00");
  assert.ok(sqlite.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key=?").get(DAILY_ROW_NATURAL_KEY_MIGRATION));
  sqlite.close();
});

test("daily natural-key migration deduplicates legacy rows and updates only the winner", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSchema(sqlite);
  sqlite.exec(`
    INSERT INTO netshop_import_batches VALUES
      ('old','completed','2026-07-29 08:00:00','2026-07-29 07:00:00'),
      ('new','completed','2026-07-30 08:00:00','2026-07-30 07:00:00');
    INSERT INTO netshop_rows
      (id,source_row_key,last_import_batch_id,source,dataset,platform,shop_name,business_date,sku_id,updated_at)
    VALUES
      (1,'legacy-old','old','jd_sku_daily','sku_daily','京东','测试店','2026-07-29','SKU-1','2026-07-29 08:00:00'),
      (2,'legacy-new','new','jd_sku_daily','sku_daily','京东','测试店','2026-07-29','SKU-1','2026-07-30 08:00:00');
  `);

  await ensureDailyRowNaturalKeys(sqliteAdapter(sqlite));

  const rows = sqlite.prepare("SELECT id,source_row_key FROM netshop_rows").all() as Array<{ id: number; source_row_key: string }>;
  assert.deepEqual(rows.map((row) => ({ ...row })), [{
    id: 2,
    source_row_key: dailyRowKey("sku_daily", "京东", "测试店", "2026-07-29", "SKU-1"),
  }]);
  assert.equal((sqlite.prepare("SELECT count FROM row_update_audit").get() as { count: number }).count, 1);
  sqlite.close();
});

test("daily loser probe uses the partial natural-identity index at 10k-row scale", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSchema(sqlite);
  sqlite.prepare("INSERT INTO netshop_import_batches VALUES ('batch-1','completed','2026-07-30 08:00:00','2026-07-30 07:00:00')").run();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source_row_key,last_import_batch_id,source,dataset,platform,shop_name,business_date,sku_id,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);
  sqlite.exec("BEGIN");
  for (let index = 0; index < 10_000; index += 1) {
    const sku = `SKU-SCALE-${index}`;
    insert.run(
      index + 1,
      dailyRowKey("sku_daily", "京东", "规模测试店", "2026-07-29", sku),
      "batch-1",
      "jd_sku_daily",
      "sku_daily",
      "京东",
      "规模测试店",
      "2026-07-29",
      sku,
      "2026-07-30 08:00:00",
    );
  }
  sqlite.exec("COMMIT");

  const plan = sqlite.prepare(`EXPLAIN QUERY PLAN ${DAILY_ROW_NATURAL_KEY_LOSER_PROBE_SQL}`).all(1) as Array<{ detail: string }>;
  assert.ok(plan.some((row) => row.detail.includes(DAILY_ROW_NATURAL_IDENTITY_INDEX_NAME)), JSON.stringify(plan));
  const startedAt = performance.now();
  await ensureDailyRowNaturalKeys(sqliteAdapter(sqlite));
  const elapsedMs = performance.now() - startedAt;
  assert.ok(elapsedMs < 1_000, `10k-row loser probe took ${Math.round(elapsedMs)}ms`);
  assert.ok(sqlite.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key=?").get(DAILY_ROW_NATURAL_KEY_MIGRATION));
  sqlite.close();
});

test("daily natural-key migration bounds every legacy read batch", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSchema(sqlite);
  sqlite.exec(`INSERT INTO netshop_import_batches VALUES
    ('old','completed','2026-07-29 08:00:00','2026-07-29 07:00:00'),
    ('new','completed','2026-07-30 08:00:00','2026-07-30 07:00:00')`);
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source_row_key,last_import_batch_id,source,dataset,platform,shop_name,business_date,sku_id,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);
  const identities = DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE + 25;
  sqlite.exec("BEGIN");
  for (let index = 0; index < identities; index += 1) {
    const sku = `SKU-${index}`;
    insert.run(index * 2 + 1, `legacy-old-${index}`, "old", "jd_sku_daily", "sku_daily", "京东", "测试店", "2026-07-29", sku, "2026-07-29 08:00:00");
    insert.run(index * 2 + 2, `legacy-new-${index}`, "new", "jd_sku_daily", "sku_daily", "京东", "测试店", "2026-07-29", sku, "2026-07-30 08:00:00");
  }
  sqlite.exec("COMMIT");

  const observation: MigrationObservation = { allSizes: [], batchSizes: [] };
  await ensureDailyRowNaturalKeys(sqliteAdapter(sqlite, observation));

  assert.ok(Math.max(...observation.allSizes) <= DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE);
  assert.ok(observation.batchSizes.length <= DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES);
  assert.ok(observation.batchSizes.every((size) => size <= DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE));
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM netshop_rows").get() as { count: number }).count, identities);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM netshop_rows WHERE source_row_key LIKE 'legacy-%'").get() as { count: number }).count, 0);
  assert.ok(sqlite.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key=?").get(DAILY_ROW_NATURAL_KEY_MIGRATION));
  sqlite.close();
});

test("daily natural-key migration resumes across bounded calls and blocks writes while pending", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSchema(sqlite);
  sqlite.exec(`INSERT INTO netshop_import_batches VALUES
    ('old','completed','2026-07-29 08:00:00','2026-07-29 07:00:00'),
    ('new','completed','2026-07-30 08:00:00','2026-07-30 07:00:00')`);
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source_row_key,last_import_batch_id,source,dataset,platform,shop_name,business_date,sku_id,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);
  const identities = 501;
  sqlite.exec("BEGIN");
  for (let index = 0; index < identities; index += 1) {
    const sku = `SKU-LARGE-${index}`;
    insert.run(index * 2 + 1, `legacy-old-large-${index}`, "old", "jd_sku_daily", "sku_daily", "京东", "大库测试店", "2026-07-29", sku, "2026-07-29 08:00:00");
    insert.run(index * 2 + 2, `legacy-new-large-${index}`, "new", "jd_sku_daily", "sku_daily", "京东", "大库测试店", "2026-07-29", sku, "2026-07-30 08:00:00");
  }
  sqlite.exec("COMMIT");

  const observations: MigrationObservation[] = [];
  const firstObservation: MigrationObservation = { allSizes: [], batchSizes: [] };
  observations.push(firstObservation);
  let guardedWriteRan = false;
  await assert.rejects(async () => {
    await ensureDailyRowNaturalKeys(sqliteAdapter(sqlite, firstObservation));
    guardedWriteRan = true;
  }, NetshopSchemaUpgradePendingError);
  assert.equal(guardedWriteRan, false);
  assert.equal(sqlite.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key=?").get(DAILY_ROW_NATURAL_KEY_MIGRATION), undefined);

  let completed = false;
  for (let attempt = 0; attempt < 10 && !completed; attempt += 1) {
    const observation: MigrationObservation = { allSizes: [], batchSizes: [] };
    observations.push(observation);
    try {
      await ensureDailyRowNaturalKeys(sqliteAdapter(sqlite, observation));
      completed = true;
    } catch (error) {
      assert.ok(error instanceof NetshopSchemaUpgradePendingError);
      assert.equal(sqlite.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key=?").get(DAILY_ROW_NATURAL_KEY_MIGRATION), undefined);
    }
  }

  assert.equal(completed, true);
  assert.ok(observations.length > 2, "more than 800 legacy rows must require multiple resumable calls");
  for (const observation of observations) {
    assert.ok(observation.allSizes.length <= DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES + 1);
    assert.ok(observation.allSizes.every((size) => size <= DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE));
    assert.ok(observation.batchSizes.length <= DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES);
    assert.ok(observation.batchSizes.every((size) => size <= DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE));
  }
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM netshop_rows").get() as { count: number }).count, identities);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM netshop_rows WHERE source_row_key LIKE 'legacy-%'").get() as { count: number }).count, 0);
  assert.ok(sqlite.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key=?").get(DAILY_ROW_NATURAL_KEY_MIGRATION));
  sqlite.close();
});

test("netshop import stops before touching the database while schema migration is pending", async () => {
  const databaseAccesses: PropertyKey[] = [];
  const database = new Proxy({}, {
    get(_target, property) {
      databaseAccesses.push(property);
      throw new Error(`unexpected database access: ${String(property)}`);
    },
  });
  let ensureCalls = 0;
  await assert.rejects(importNetshopBytes({
    bytes: new Uint8Array([1, 2, 3]),
    fileName: "blocked.xlsx",
    fileSizeBytes: 3,
    source: "jd_sku_daily",
  }, {
    getNetshopDatabase: () => database,
    ensureNetshopSchema: async () => {
      ensureCalls += 1;
      throw new NetshopSchemaUpgradePendingError();
    },
  } as never), NetshopSchemaUpgradePendingError);
  assert.equal(ensureCalls, 1);
  assert.deepEqual(databaseAccesses, []);
});
