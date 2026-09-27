import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";
import {
  aggregatePromotionRows,
  buildPromotionAggregatePublishStatements,
  canUsePromotionAggregates,
  ensurePromotionAggregateSchema,
  PROMOTION_AGGREGATE_REBUILD_MAX_DAYS,
  PROMOTION_AGGREGATE_SCHEMA_STATEMENTS,
  readPromotionAggregateVersion,
  rebuildPromotionAggregates,
  type PromotionAggregateDatabase,
  type PromotionAggregateScope,
} from "../lib/netshop/promotion-aggregate";
import type { NetshopRowInput } from "../lib/netshop/database";

class SqliteStatement {
  private values: unknown[] = [];

  constructor(private readonly sqlite: DatabaseSync, private readonly sql: string) {}

  bind(...values: unknown[]) {
    this.values = values;
    return this;
  }

  first<T = unknown>() {
    return Promise.resolve((this.sqlite.prepare(this.sql).get(...this.values as SQLInputValue[]) as T | undefined) ?? null);
  }

  all<T = unknown>() {
    return Promise.resolve({ results: this.sqlite.prepare(this.sql).all(...this.values as SQLInputValue[]) as T[] });
  }

  run() {
    return Promise.resolve(this.sqlite.prepare(this.sql).run(...this.values as SQLInputValue[]));
  }

  execute() {
    return this.sqlite.prepare(this.sql).run(...this.values as SQLInputValue[]);
  }
}

function adapter(sqlite: DatabaseSync, bindCounts?: number[], reads?: string[]): PromotionAggregateDatabase {
  return {
    prepare(sql: string) {
      reads?.push(sql);
      const statement = new SqliteStatement(sqlite, sql);
      if (!bindCounts) return statement;
      const originalBind = statement.bind.bind(statement);
      statement.bind = (...values: unknown[]) => {
        bindCounts.push(values.length);
        return originalBind(...values);
      };
      return statement;
    },
    async batch(statements: SqliteStatement[]) {
      sqlite.exec("BEGIN");
      try {
        const results = statements.map((statement) => statement.execute());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  } as PromotionAggregateDatabase;
}

function installPlatformManifest(sqlite: DatabaseSync, platform = "京东") {
  sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_manifest (platform,ready,completed_at)
    VALUES (?,1,CURRENT_TIMESTAMP)
    ON CONFLICT(platform) DO UPDATE SET ready=1,completed_at=CURRENT_TIMESTAMP`).run(platform);
}

function createRawTable(sqlite: DatabaseSync) {
  sqlite.exec(`CREATE TABLE netshop_import_batches (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL
  );
  CREATE TABLE netshop_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    business_date TEXT,
    sku_id TEXT NOT NULL DEFAULT '',
    spu_id TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}',
    last_import_batch_id TEXT NOT NULL DEFAULT ''
  )`);
}

function row(input: Partial<NetshopRowInput> & Pick<NetshopRowInput, "source" | "dataset" | "platform" | "shopName" | "businessDate">): NetshopRowInput {
  return {
    sourceRowNumber: 1,
    sourceRowKey: "key",
    sourceRowHash: "hash",
    snapshotDate: "",
    productCode: "",
    productName: "商品",
    skuId: "",
    spuId: "",
    warehouseType: "",
    metrics: {},
    raw: {},
    ...input,
  };
}

const jdScope: PromotionAggregateScope = {
  source: "jd_promotion",
  dataset: "ad",
  platform: "京东",
  shopName: "店铺A",
  startDate: "2026-08-01",
  endDate: "2026-08-02",
};

test("pure promotion aggregation preserves shop identity and converts legacy JD money to cents", () => {
  const result = aggregatePromotionRows([
    row({ ...jdScope, source: "jd_promotion", dataset: "ad", businessDate: "2026-08-01", skuId: "SKU-1", productName: "B", metrics: { 花费: 1.25, 总订单金额: 10, 展现数: 100, 点击数: 5, 总订单行: 2 }, raw: { 产品线: "厨电" } }),
    row({ ...jdScope, source: "jd_promotion", dataset: "ad", businessDate: "2026-08-01", skuId: "SKU-1", productName: "A", metrics: { 花费: 2, 总订单金额: 4, 展现数: 50, 点击数: 2, 总订单行: 1 }, raw: { 产品线: "商用" } }),
    row({ source: "tmall_promotion", dataset: "promotion_daily", platform: "天猫", shopName: "店铺A", businessDate: "2026-08-01", spuId: "SKU-1", metrics: { spendCents: 999 } }),
  ], "batch-1");

  assert.equal(result.products.length, 2);
  const jd = result.products.find((item) => item.platform === "京东")!;
  assert.deepEqual({
    productName: jd.productName,
    productLine: jd.productLine,
    spendCents: jd.spendCents,
    netTransactionAmountCents: jd.netTransactionAmountCents,
    grossTransactionAmountCents: jd.grossTransactionAmountCents,
    impressions: jd.impressions,
    clicks: jd.clicks,
    netOrders: jd.netOrders,
    sourceRowCount: jd.sourceRowCount,
  }, {
    productName: "B",
    productLine: "厨电,商用",
    spendCents: 325,
    netTransactionAmountCents: 1400,
    grossTransactionAmountCents: 1400,
    impressions: 150,
    clicks: 7,
    netOrders: 3,
    sourceRowCount: 2,
  });
  assert.equal(result.shops.length, 2);
});

test("runtime aggregate schema matches the forward migration without backfilling raw history", async () => {
  const runtime = new DatabaseSync(":memory:");
  createRawTable(runtime);
  await ensurePromotionAggregateSchema(adapter(runtime));
  runtime.prepare(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东','店铺A','2026-08-01','SKU-1','{"花费":1}','batch-1')`).run();
  assert.equal(runtime.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count, 0);
  assert.equal(runtime.prepare("SELECT ready FROM netshop_promotion_aggregate_state").get()!.ready, 0);

  const migrated = new DatabaseSync(":memory:");
  createRawTable(migrated);
  const migrations = await Promise.all([
    readFile(new URL("../drizzle/0067_netshop_promotion_daily_aggregates.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0070_netshop_promotion_aggregate_manifest.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0071_netshop_promotion_snapshot_fence.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0074_netshop_promotion_maintenance_fence.sql", import.meta.url), "utf8"),
  ]);
  for (const migration of migrations) {
    for (const statement of migration.split(/--> statement-breakpoint\s*/).map((sql) => sql.trim()).filter(Boolean)) migrated.exec(statement);
  }
  migrated.prepare(`INSERT INTO netshop_promotion_product_daily
    (platform,shop_name,business_date,product_id,source,spend_cents)
    VALUES ('京东','迁移先行店铺','2026-08-01','SKU-MIGRATION-FIRST','jd_promotion',456)`).run();
  migrated.prepare(`INSERT INTO netshop_promotion_aggregate_manifest
    (platform,ready,raw_row_count,data_version,completed_at)
    VALUES ('京东',1,9,7,CURRENT_TIMESTAMP)`).run();
  await ensurePromotionAggregateSchema(adapter(migrated));
  assert.deepEqual({ ...migrated.prepare(`SELECT product_id,spend_cents
    FROM netshop_promotion_product_daily`).get()! }, {
    product_id: "SKU-MIGRATION-FIRST",
    spend_cents: 456,
  });
  assert.deepEqual({ ...migrated.prepare(`SELECT ready,raw_row_count,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 1,
    raw_row_count: 9,
    data_version: 7,
  });

  const schemaObjects = (sqlite: DatabaseSync) => sqlite.prepare(`SELECT type,name
    FROM sqlite_master
    WHERE name LIKE 'netshop_promotion_%'
    ORDER BY type,name`).all();
  assert.deepEqual(schemaObjects(runtime), schemaObjects(migrated));
  assert.equal(PROMOTION_AGGREGATE_SCHEMA_STATEMENTS.length, schemaObjects(runtime).length);
  assert.deepEqual(
    runtime.prepare("PRAGMA table_info(netshop_promotion_aggregate_manifest)").all().map((row) => ({ ...row })),
    migrated.prepare("PRAGMA table_info(netshop_promotion_aggregate_manifest)").all().map((row) => ({ ...row })),
  );
});

test("an old 0065 database can run runtime ensure before 0069/0070/0073 without duplicate schema or fact changes", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const oldMigration = await readFile(new URL("../drizzle/0067_netshop_promotion_daily_aggregates.sql", import.meta.url), "utf8");
  for (const statement of oldMigration.split(/--> statement-breakpoint\s*/).map((sql) => sql.trim()).filter(Boolean)) sqlite.exec(statement);
  sqlite.prepare(`INSERT INTO netshop_promotion_product_daily
    (platform,shop_name,business_date,product_id,source,spend_cents)
    VALUES ('京东','旧库店铺','2026-08-01','SKU-OLD','jd_promotion',123)`).run();

  await ensurePromotionAggregateSchema(adapter(sqlite));
  sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_manifest (platform,ready,raw_row_count,completed_at)
    VALUES ('京东',1,17,CURRENT_TIMESTAMP)`).run();
  const forwardMigrations = await Promise.all([
    readFile(new URL("../drizzle/0070_netshop_promotion_aggregate_manifest.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0071_netshop_promotion_snapshot_fence.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0074_netshop_promotion_maintenance_fence.sql", import.meta.url), "utf8"),
  ]);
  for (const migration of forwardMigrations) {
    for (const statement of migration.split(/--> statement-breakpoint\s*/).map((sql) => sql.trim()).filter(Boolean)) sqlite.exec(statement);
  }

  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,raw_row_count,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 1,
    raw_row_count: 17,
    data_version: 0,
  });
  assert.deepEqual(sqlite.prepare(`SELECT product_id,spend_cents FROM netshop_promotion_product_daily`).all().map((row) => ({ ...row })), [
    { product_id: "SKU-OLD", spend_cents: 123 },
  ]);
  assert.equal(sqlite.prepare(`SELECT COUNT(*) AS count FROM sqlite_master
    WHERE type='index' AND name='netshop_promotion_aggregate_state_stale_platform_date_idx'`).get()!.count, 1);
  sqlite.close();
});

test("runtime schema can precede the complete 0065/0069/0070/0073 migration chain", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  await ensurePromotionAggregateSchema(adapter(sqlite));
  sqlite.prepare(`INSERT INTO netshop_promotion_product_daily
    (platform,shop_name,business_date,product_id,source,spend_cents)
    VALUES ('京东','代码先行店铺','2026-08-01','SKU-RUNTIME-FIRST','jd_promotion',789)`).run();
  sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_manifest
    (platform,ready,raw_row_count,data_version,completed_at)
    VALUES ('京东',1,11,5,CURRENT_TIMESTAMP)`).run();

  const migrations = await Promise.all([
    readFile(new URL("../drizzle/0067_netshop_promotion_daily_aggregates.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0070_netshop_promotion_aggregate_manifest.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0071_netshop_promotion_snapshot_fence.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0074_netshop_promotion_maintenance_fence.sql", import.meta.url), "utf8"),
  ]);
  for (const migration of migrations) {
    for (const statement of migration.split(/--> statement-breakpoint\s*/).map((sql) => sql.trim()).filter(Boolean)) sqlite.exec(statement);
  }

  assert.deepEqual({ ...sqlite.prepare(`SELECT product_id,spend_cents
    FROM netshop_promotion_product_daily`).get()! }, {
    product_id: "SKU-RUNTIME-FIRST",
    spend_cents: 789,
  });
  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,raw_row_count,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 1,
    raw_row_count: 11,
    data_version: 5,
  });
  sqlite.close();
});

test("raw trigger increments a platform version once for 1000 rows in one stale shop-day", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db);
  installPlatformManifest(sqlite);
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东','批量店铺','2026-08-01',?,?,'batch-1')`);
  sqlite.exec("BEGIN");
  for (let index = 0; index < 1_000; index += 1) {
    insert.run(`SKU-${index}`, JSON.stringify({ spendCents: index + 1 }));
  }
  sqlite.exec("COMMIT");
  assert.equal(sqlite.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version, 1);
  assert.equal(sqlite.prepare(`SELECT ready FROM netshop_promotion_aggregate_state
    WHERE platform='京东' AND shop_name='批量店铺' AND business_date='2026-08-01'`).get()!.ready, 0);

  sqlite.prepare(`UPDATE netshop_promotion_aggregate_state SET ready=1
    WHERE platform='京东' AND shop_name='批量店铺' AND business_date='2026-08-01'`).run();
  sqlite.prepare(`UPDATE netshop_rows SET metrics_json='{"spendCents":999}'
    WHERE platform='京东' AND shop_name='批量店铺' AND business_date='2026-08-01'`).run();
  assert.equal(sqlite.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version, 2);
  sqlite.close();
});

test("readiness SQL is metadata-only and uses bounded stale-state indexes", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  await ensurePromotionAggregateSchema(adapter(sqlite));
  installPlatformManifest(sqlite);

  const noShopReads: string[] = [];
  assert.equal(await readPromotionAggregateVersion(adapter(sqlite, undefined, noShopReads), {
    platform: "京东",
    startDate: "2026-08-01",
    endDate: "2026-08-31",
  }), 0);
  const noShopStateSql = noShopReads[0]!;
  const noShopPlan = sqlite.prepare(`EXPLAIN QUERY PLAN ${noShopStateSql}`)
    .all(JSON.stringify([{
      platform: "京东",
      startDate: "2026-08-01",
      endDate: "2026-08-31",
      shopNames: [],
    }])) as Array<{ detail: string }>;
  assert.ok(noShopPlan.some((row) => /netshop_promotion_aggregate_state_stale_platform_date_idx/.test(row.detail)), JSON.stringify(noShopPlan));

  const shopReads: string[] = [];
  assert.equal(await readPromotionAggregateVersion(adapter(sqlite, undefined, shopReads), {
    platform: "京东",
    shopNames: Array.from({ length: 50 }, (_, index) => `店铺-${index}`),
    startDate: "2026-08-01",
    endDate: "2026-08-31",
  }), 0);
  const shopStateSql = shopReads[0]!;
  const shopPlan = sqlite.prepare(`EXPLAIN QUERY PLAN ${shopStateSql}`)
    .all(JSON.stringify([{
      platform: "京东",
      shopNames: Array.from({ length: 50 }, (_, index) => `店铺-${index}`),
      startDate: "2026-08-01",
      endDate: "2026-08-31",
    }])) as Array<{ detail: string }>;
  assert.match(shopStateSql, /FROM json_each\(requested\.shop_names\) selected_shop[\s\S]*WHERE EXISTS/);
  assert.ok(shopPlan.some((row) => /SCAN selected_shop VIRTUAL TABLE/.test(row.detail)), JSON.stringify(shopPlan));
  assert.ok(shopPlan.some((row) => /netshop_promotion_aggregate_state_ready_scope_date_idx/.test(row.detail)
    && /ready=.*platform=.*shop_name=.*business_date/.test(row.detail)), JSON.stringify(shopPlan));
  assert.ok([...noShopReads, ...shopReads].every((sql) => !/GROUP BY|netshop_rows|netshop_promotion_product_daily|netshop_promotion_shop_daily/i.test(sql)));
  sqlite.close();
});

test("publish statements are atomic/idempotent and raw mutations invalidate exact ready scopes", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db);
  sqlite.prepare("INSERT INTO netshop_import_batches (id,status) VALUES ('batch-1','processing')").run();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,product_name,metrics_json,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);
  insert.run("jd_promotion", "ad", "京东", "店铺A", "2026-08-01", "SKU-1", "商品1", JSON.stringify({ 花费: 2, 总订单金额: 8, 展现数: 50 }), JSON.stringify({ 产品线: "厨电" }), "batch-1");
  const rows = [row({ ...jdScope, source: "jd_promotion", dataset: "ad", businessDate: "2026-08-01", skuId: "SKU-1", productName: "商品1", metrics: { 花费: 2, 总订单金额: 8, 展现数: 50 }, raw: { 产品线: "厨电" } })];
  const statements = buildPromotionAggregatePublishStatements(db, { ...jdScope, batchId: "batch-1", rows });
  await db.batch(statements);
  let repeatedControlInserts = 0;
  sqlite.function("observe_repeated_control_insert", () => {
    repeatedControlInserts += 1;
    return null;
  });
  sqlite.exec(`CREATE TRIGGER observe_repeated_control_insert
    AFTER INSERT ON netshop_promotion_aggregate_control
    BEGIN SELECT observe_repeated_control_insert(); END`);
  await db.batch(buildPromotionAggregatePublishStatements(db, { ...jdScope, batchId: "batch-1", rows }));
  assert.equal(repeatedControlInserts, 0, "an existing manifest/control must not re-enter the historical bootstrap trigger");

  assert.equal(sqlite.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count, 1);
  assert.equal(sqlite.prepare("SELECT spend_cents FROM netshop_promotion_product_daily").get()!.spend_cents, 200);
  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,raw_row_count,product_row_count,shop_day_count,state_day_count,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 1,
    raw_row_count: 1,
    product_row_count: 1,
    shop_day_count: 1,
    state_day_count: 1,
    data_version: 0,
  });
  sqlite.prepare("UPDATE netshop_import_batches SET status='completed' WHERE id='batch-1'").run();
  const conflictingRows = [row({ ...jdScope, source: "jd_promotion", dataset: "ad", businessDate: "2026-08-01", skuId: "SKU-2", metrics: { 花费: 99 } })];
  await db.batch(buildPromotionAggregatePublishStatements(db, { ...jdScope, batchId: "batch-1", rows: conflictingRows }));
  assert.deepEqual(sqlite.prepare("SELECT product_id,spend_cents FROM netshop_promotion_product_daily").all().map((item) => ({ ...item })), [
    { product_id: "SKU-1", spend_cents: 200 },
  ]);
  installPlatformManifest(sqlite);
  assert.equal(await canUsePromotionAggregates(db, { platform: "京东", shopNames: ["店铺A"], startDate: "2026-08-01", endDate: "2026-08-02" }), true);

  sqlite.prepare(`UPDATE netshop_rows SET metrics_json = '{"花费":3}' WHERE sku_id = 'SKU-1'`).run();
  assert.equal(sqlite.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version, 1);
  assert.equal(await canUsePromotionAggregates(db, { platform: "京东", shopNames: ["店铺A"], startDate: "2026-08-01", endDate: "2026-08-02" }), false);
  sqlite.prepare("INSERT INTO netshop_import_batches (id,status) VALUES ('batch-2','processing')").run();
  const republishedRows = [row({
    ...jdScope,
    source: "jd_promotion",
    dataset: "ad",
    businessDate: "2026-08-01",
    skuId: "SKU-1",
    productName: "商品1",
    metrics: { 花费: 3, 总订单金额: 8, 展现数: 50 },
    raw: { 产品线: "厨电" },
  })];
  await db.batch([
    db.prepare(`UPDATE netshop_rows SET last_import_batch_id='batch-2' WHERE sku_id='SKU-1'`),
    ...buildPromotionAggregatePublishStatements(db, { ...jdScope, batchId: "batch-2", rows: republishedRows }),
  ]);
  assert.equal(sqlite.prepare("SELECT spend_cents FROM netshop_promotion_product_daily").get()!.spend_cents, 300);
  assert.equal(sqlite.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version, 1);
  assert.equal(await canUsePromotionAggregates(db, { platform: "京东", shopNames: ["店铺A"], startDate: "2026-08-01", endDate: "2026-08-02" }), true);

  sqlite.prepare("DELETE FROM netshop_rows WHERE sku_id = 'SKU-1'").run();
  assert.equal(sqlite.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count, 1);
  assert.equal(sqlite.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version, 2);
  assert.equal(await canUsePromotionAggregates(db, { platform: "京东", shopNames: ["店铺A"], startDate: "2026-08-01", endDate: "2026-08-02" }), false);
});

test("first normal import leaves one fail-closed legacy sentinel and never retries its historical proof", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  sqlite.exec(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东','未回填旧店','2026-07-01','LEGACY','{"花费":1}','legacy')`);
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db);
  sqlite.exec(`INSERT INTO netshop_import_batches (id,status) VALUES ('batch-new','processing');
    INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东','新店','2026-08-01','NEW','{"花费":2}','batch-new')`);
  await db.batch(buildPromotionAggregatePublishStatements(db, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "新店",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    batchId: "batch-new",
    rows: [row({
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName: "新店",
      businessDate: "2026-08-01",
      skuId: "NEW",
      metrics: { 花费: 2 },
    })],
  }));
  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 0,
    data_version: 0,
  });
  assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM netshop_promotion_aggregate_control
    WHERE platform='京东' AND bootstrap_batch_id='batch-new'`).get()!.count, 1);
  assert.equal(await canUsePromotionAggregates(db, {
    platform: "京东", startDate: "2026-07-01", endDate: "2026-08-01",
  }), false);
  let repeatedControlInserts = 0;
  sqlite.function("observe_legacy_control_insert", () => {
    repeatedControlInserts += 1;
    return null;
  });
  sqlite.exec(`CREATE TRIGGER observe_legacy_control_insert
    AFTER INSERT ON netshop_promotion_aggregate_control
    BEGIN SELECT observe_legacy_control_insert(); END`);
  await db.batch(buildPromotionAggregatePublishStatements(db, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "新店",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    batchId: "batch-new",
    rows: [row({
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName: "新店",
      businessDate: "2026-08-01",
      skuId: "NEW",
      metrics: { 花费: 2 },
    })],
  }));
  assert.equal(repeatedControlInserts, 0, "a legacy sentinel must make later imports take the O(1) conflict path");
  sqlite.close();
});

test("a residual current-batch legacy aggregate cannot be blessed as a complete new platform", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db);
  sqlite.prepare("INSERT INTO netshop_import_batches (id,status) VALUES ('batch-new','processing')").run();
  sqlite.exec(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东','新店','2026-08-01','NEW','{"花费":2}','batch-new');
    INSERT INTO netshop_promotion_product_daily
    (platform,shop_name,business_date,product_id,source,source_batch_id,source_batch_count)
    VALUES ('京东','残缺旧店','2026-07-01','LEGACY','jd_promotion','batch-new',1)`);
  await db.batch(buildPromotionAggregatePublishStatements(db, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "新店",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    batchId: "batch-new",
    rows: [row({
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName: "新店",
      businessDate: "2026-08-01",
      skuId: "NEW",
      metrics: { 花费: 2 },
    })],
  }));
  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,product_row_count
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 0,
    product_row_count: 0,
  });
  assert.equal(await canUsePromotionAggregates(db, {
    platform: "京东", startDate: "2026-07-01", endDate: "2026-08-01",
  }), false);
  sqlite.close();
});

test("failed aggregate publication rolls back without marking stale raw facts ready", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db);
  sqlite.prepare("INSERT INTO netshop_import_batches (id,status) VALUES ('batch-1','processing')").run();
  sqlite.exec(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东','店铺A','2026-08-01','SKU-1','{"花费":1}','batch-1')`);
  const rows = [row({ ...jdScope, source: "jd_promotion", dataset: "ad", businessDate: "2026-08-01", skuId: "SKU-1", metrics: { 花费: 1 } })];
  const publish = buildPromotionAggregatePublishStatements(db, { ...jdScope, batchId: "batch-1", rows });
  await assert.rejects(db.batch([...publish, db.prepare("INSERT INTO missing_table VALUES (1)")]));
  assert.equal(sqlite.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count, 0);
  assert.equal(sqlite.prepare("SELECT ready FROM netshop_promotion_aggregate_state").get()!.ready, 0);
});

test("a partial historical shop backfill cannot authorize an unfiltered platform read", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,metrics_json,last_import_batch_id)
    VALUES ('jd_promotion','ad','京东',?,'2026-08-01',?,'{"spendCents":100}','legacy-batch')`);
  insert.run("历史店铺A", "SKU-A");
  insert.run("历史店铺B", "SKU-B");
  const db = adapter(sqlite);
  await ensurePromotionAggregateSchema(db);
  await rebuildPromotionAggregates(db, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "历史店铺A",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  });
  assert.equal(sqlite.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count, 1);
  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 0,
    data_version: 1,
  });

  const reads: string[] = [];
  assert.equal(await canUsePromotionAggregates(adapter(sqlite, undefined, reads), {
    platform: "京东",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  }), false);
  assert.equal(reads.length, 1);
  assert.match(reads[0]!, /netshop_promotion_aggregate_manifest/);
  assert.doesNotMatch(reads[0]!, /netshop_rows|netshop_promotion_product_daily|netshop_promotion_shop_daily/);
  sqlite.close();
});

test("historical rebuild is deliberately bounded", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  await assert.rejects(rebuildPromotionAggregates(adapter(sqlite), {
    ...jdScope,
    startDate: "2026-01-01",
    endDate: "2026-02-01",
  }), new RegExp(`最多 ${PROMOTION_AGGREGATE_REBUILD_MAX_DAYS} 天`));
});

test("50-store readiness gate uses bounded state queries without scanning raw promotion rows", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createRawTable(sqlite);
  const bindCounts: number[] = [];
  const reads: string[] = [];
  const db = adapter(sqlite, bindCounts, reads);
  await ensurePromotionAggregateSchema(db);
  bindCounts.length = 0;
  reads.length = 0;
  const shops = Array.from({ length: 50 }, (_, index) => `中文店铺-${index + 1}`);
  installPlatformManifest(sqlite);
  const insertState = sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_state
    (platform,shop_name,business_date,source,ready,raw_row_count,product_row_count)
    VALUES ('京东',?,'2026-08-01','jd_promotion',1,1,1)`);
  for (const shop of shops) {
    insertState.run(shop);
  }
  assert.equal(await canUsePromotionAggregates(db, {
    platform: "京东",
    shopNames: shops,
    startDate: "2026-08-01",
    endDate: "2026-08-22",
  }), true);
  assert.equal(reads.length, 2);
  assert.equal(bindCounts.length, 2);
  assert.ok(Math.max(...bindCounts) <= 100, `maximum bind count was ${Math.max(...bindCounts)}`);
  assert.equal(Math.max(...bindCounts), 1);
  assert.ok(reads.every((sql) => /netshop_promotion_(?:aggregate_(?:manifest|state)|scope_revisions)/.test(sql)));
  assert.ok(reads.every((sql) => !/GROUP BY|netshop_rows|netshop_promotion_product_daily|netshop_promotion_shop_daily/i.test(sql)));
  sqlite.close();
});
