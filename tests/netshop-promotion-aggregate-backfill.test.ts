import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";
import {
  buildPromotionAggregatePublishStatements,
  type PromotionAggregateDatabase,
} from "../lib/netshop/promotion-aggregate";
import type { NetshopRowInput } from "../lib/netshop/database";
import {
  parsePromotionBackfillArgs,
  runPromotionAggregateBackfill,
} from "../tools/netshop-promotion-aggregate-backfill";

class SqliteStatement {
  private values: SQLInputValue[] = [];

  constructor(private readonly sqlite: DatabaseSync, private readonly sql: string) {}

  bind(...values: unknown[]) {
    this.values = values as SQLInputValue[];
    return this;
  }

  first<T = unknown>() {
    return Promise.resolve((this.sqlite.prepare(this.sql).get(...this.values) as T | undefined) ?? null);
  }

  all<T = unknown>() {
    return Promise.resolve({ results: this.sqlite.prepare(this.sql).all(...this.values) as T[] });
  }

  run() {
    return Promise.resolve(this.execute());
  }

  execute() {
    return this.sqlite.prepare(this.sql).run(...this.values);
  }
}

function aggregateAdapter(sqlite: DatabaseSync): PromotionAggregateDatabase {
  return {
    prepare(sql: string) {
      return new SqliteStatement(sqlite, sql);
    },
    async batch(statements: SqliteStatement[]) {
      sqlite.exec("BEGIN IMMEDIATE");
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

function jdPromotionRow(input: { shop: string; date: string; sku: string; spendCents: number }): NetshopRowInput {
  return {
    sourceRowNumber: 1,
    sourceRowKey: JSON.stringify(["jd_promotion", "ad", "京东", input.shop, input.date, input.sku]),
    sourceRowHash: String(input.spendCents).padStart(64, "0").slice(-64),
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: input.shop,
    businessDate: input.date,
    snapshotDate: "",
    productCode: "",
    productName: "商品",
    skuId: input.sku,
    spuId: "",
    warehouseType: "",
    metrics: {
      spendCents: input.spendCents,
      netTransactionAmountCents: 300,
      grossTransactionAmountCents: 300,
      impressions: 10,
      clicks: 2,
      netOrders: 1,
    },
    raw: { 产品线: "厨电" },
  };
}

function createProjectTempDirectory() {
  return mkdtempSync(join(resolve("."), ".promotion-backfill-test-"));
}

function cleanupProjectTempDirectory(directory: string) {
  const root = resolve(".");
  const target = resolve(directory);
  assert.equal(dirname(target), root);
  assert.ok(target.startsWith(join(root, ".promotion-backfill-test-")));
  if (existsSync(target)) rmSync(target, { recursive: true, force: true });
}

function createDatabase(databasePath: string) {
  const sqlite = new DatabaseSync(databasePath);
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
  return sqlite;
}

function insertJdRow(sqlite: DatabaseSync, input: { shop: string; date: string; sku: string; metrics?: string }) {
  sqlite.prepare(`INSERT INTO netshop_rows
    (source,dataset,platform,shop_name,business_date,sku_id,product_name,metrics_json,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    "jd_promotion", "ad", "京东", input.shop, input.date, input.sku, "商品",
    input.metrics ?? JSON.stringify({ 花费: 1, 总订单金额: 3, 展现数: 10, 点击数: 2, 总订单行: 1 }),
    JSON.stringify({ 产品线: "厨电" }), "batch-1",
  );
}

test("backfill CLI requires an explicit absolute project database path and validates filters", () => {
  assert.throws(() => parsePromotionBackfillArgs([]), /必须显式提供/);
  assert.throws(() => parsePromotionBackfillArgs(["--database", "relative.sqlite"]), /绝对路径/);
  assert.throws(() => parsePromotionBackfillArgs(["--database", resolve("x.sqlite"), "--apply", "--dry-run"]), /不能同时/);
  assert.throws(() => parsePromotionBackfillArgs(["--database", resolve("x.sqlite"), "--platform", "淘宝"]), /京东 或 天猫/);
  assert.throws(() => parsePromotionBackfillArgs(["--database", resolve("x.sqlite"), "--start-date", "2026-02-30"]), /有效/);
  assert.throws(() => parsePromotionBackfillArgs([
    "--database", resolve("x.sqlite"), "--start-date", "2026-08-02", "--end-date", "2026-08-01",
  ]), /开始日期/);
});

test("dry-run is read-only, uses exact Chinese shop binding, and plans at most 31 days per chunk", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  const sqlite = createDatabase(databasePath);
  const exactShop = "中文店铺' OR 1=1 --";
  insertJdRow(sqlite, { shop: exactShop, date: "2026-08-01", sku: "SKU-1" });
  insertJdRow(sqlite, { shop: exactShop, date: "2026-09-01", sku: "SKU-2" });
  insertJdRow(sqlite, { shop: "另一店铺", date: "2026-08-02", sku: "SKU-3" });
  sqlite.close();

  const lines: string[] = [];
  const result = await runPromotionAggregateBackfill({
    database: databasePath,
    apply: false,
    platform: "京东",
    shop: exactShop,
  }, (line) => lines.push(line));

  assert.equal(result.mode, "dry-run");
  assert.equal(result.scopeCount, 1);
  assert.equal(result.chunkCount, 2);
  assert.deepEqual(result.chunks.map((chunk) => [chunk.startDate, chunk.endDate, chunk.rawRows]), [
    ["2026-08-01", "2026-08-31", 1],
    ["2026-09-01", "2026-09-01", 1],
  ]);
  assert.equal(lines.length, 3);

  const inspected = new DatabaseSync(databasePath, { readOnly: true });
  const aggregateTableCount = inspected.prepare(`SELECT COUNT(*) AS count FROM sqlite_master
    WHERE type = 'table' AND name LIKE 'netshop_promotion_%'`).get()!.count;
  const rawCount = inspected.prepare("SELECT COUNT(*) AS count FROM netshop_rows").get()!.count;
  inspected.close();
  assert.equal(aggregateTableCount, 0);
  assert.equal(rawCount, 3);
});

test("a range-limited apply rebuilds state but cannot install a platform completeness manifest", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  const sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "店铺A", date: "2026-08-01", sku: "SKU-1" });
  insertJdRow(sqlite, { shop: "店铺A", date: "2026-08-01", sku: "SKU-1" });
  insertJdRow(sqlite, { shop: "店铺A", date: "2026-08-02", sku: "SKU-2" });
  sqlite.close();

  const options = {
    database: databasePath,
    apply: true,
    platform: "京东" as const,
    shop: "店铺A",
    startDate: "2026-08-01",
    endDate: "2026-08-02",
  };
  const first = await runPromotionAggregateBackfill(options, () => undefined);
  const second = await runPromotionAggregateBackfill(options, () => undefined);
  assert.equal(first.chunkCount, 1);
  assert.equal(first.rawRows, 3);
  assert.equal(first.productRows, 2);
  assert.equal(first.readyDays, 2);
  assert.deepEqual(first.manifestPlatforms, []);
  assert.equal(first.chunks[0]?.canUse, false);
  assert.equal(second.chunks[0]?.canUse, false);

  const inspected = new DatabaseSync(databasePath, { readOnly: true });
  const rawCount = inspected.prepare("SELECT COUNT(*) AS count FROM netshop_rows").get()!.count;
  const productCount = inspected.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count;
  const states = inspected.prepare(`SELECT ready,raw_row_count,product_row_count
    FROM netshop_promotion_aggregate_state ORDER BY business_date`).all()
    .map((row) => ({ ...row }));
  const manifest = inspected.prepare(`SELECT ready,data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!;
  const control = inspected.prepare(`SELECT maintenance_token,maintenance_version,maintenance_previous_ready
    FROM netshop_promotion_aggregate_control WHERE platform='京东'`).get()!;
  inspected.close();
  assert.equal(rawCount, 3);
  assert.equal(productCount, 2);
  assert.deepEqual({ ...manifest }, { ready: 0, data_version: 2 });
  assert.match(String(control.maintenance_token), /^[A-Za-z0-9_-]{16,128}$/);
  assert.equal(control.maintenance_version, 2);
  assert.equal(control.maintenance_previous_ready, 0, "a legacy ready=0 sentinel must not acquire publication intent on retry");
  assert.deepEqual(states, [
    { ready: 1, raw_row_count: 2, product_row_count: 1 },
    { ready: 1, raw_row_count: 1, product_row_count: 1 },
  ]);
});

test("only a full-platform historical apply installs the manifest after every legacy shop is verified", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  const sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "历史店铺A", date: "2026-08-01", sku: "SKU-A" });
  insertJdRow(sqlite, { shop: "历史店铺B", date: "2026-08-01", sku: "SKU-B" });
  sqlite.close();

  const partial = await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
    shop: "历史店铺A",
  }, () => undefined);
  assert.deepEqual(partial.manifestPlatforms, []);
  assert.equal(partial.chunks[0]?.canUse, false);
  let inspected = new DatabaseSync(databasePath, { readOnly: true });
  assert.equal(inspected.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count, 1);
  assert.deepEqual({ ...inspected.prepare(`SELECT ready,data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()! }, { ready: 0, data_version: 1 });
  inspected.close();

  const complete = await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
  }, () => undefined);
  assert.deepEqual(complete.manifestPlatforms, ["京东"]);
  assert.equal(complete.scopeCount, 2);
  assert.equal(complete.chunks.every((chunk) => chunk.canUse === true), true);
  inspected = new DatabaseSync(databasePath);
  assert.deepEqual({ ...inspected.prepare(`SELECT ready,source_shop_count,raw_row_count,product_row_count,shop_day_count,state_day_count,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get() }, {
    ready: 1,
    source_shop_count: 2,
    raw_row_count: 2,
    product_row_count: 2,
    shop_day_count: 2,
    state_day_count: 2,
    data_version: 2,
  });
  assert.deepEqual({ ...inspected.prepare(`SELECT maintenance_token,maintenance_version
    FROM netshop_promotion_aggregate_control WHERE platform='京东'`).get()! }, {
    maintenance_token: "",
    maintenance_version: 0,
  });

  inspected.prepare(`UPDATE netshop_rows SET metrics_json='{"spendCents":999}' WHERE shop_name='历史店铺B'`).run();
  assert.equal(inspected.prepare(`SELECT ready FROM netshop_promotion_aggregate_state
    WHERE platform='京东' AND shop_name='历史店铺B' AND business_date='2026-08-01'`).get()!.ready, 0);
  assert.equal(inspected.prepare(`SELECT ready FROM netshop_promotion_aggregate_state
    WHERE platform='京东' AND shop_name='历史店铺A' AND business_date='2026-08-01'`).get()!.ready, 1);
  inspected.close();
});

test("apply rolls back a failed block and refuses databases outside the project", async (t) => {
  const outsideDirectory = mkdtempSync(join(tmpdir(), "promotion-backfill-outside-"));
  t.after(() => rmSync(outsideDirectory, { recursive: true, force: true }));
  const outsidePath = join(outsideDirectory, "outside.sqlite");
  createDatabase(outsidePath).close();
  await assert.rejects(runPromotionAggregateBackfill({ database: outsidePath, apply: false }, () => undefined), /项目目录之外/);

  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  const sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "店铺A", date: "2026-08-01", sku: "SKU-1", metrics: "not-json" });
  sqlite.close();

  await assert.rejects(runPromotionAggregateBackfill({ database: databasePath, apply: true }, () => undefined));
  const inspected = new DatabaseSync(databasePath, { readOnly: true });
  const rawCount = inspected.prepare("SELECT COUNT(*) AS count FROM netshop_rows").get()!.count;
  const productCount = inspected.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_product_daily").get()!.count;
  const stateCount = inspected.prepare("SELECT COUNT(*) AS count FROM netshop_promotion_aggregate_state").get()!.count;
  const failedManifest = inspected.prepare(`SELECT ready,data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!;
  const failedControl = inspected.prepare(`SELECT maintenance_token FROM netshop_promotion_aggregate_control
    WHERE platform='京东'`).get()!;
  inspected.close();
  assert.equal(rawCount, 1);
  assert.equal(productCount, 0);
  assert.equal(stateCount, 0);
  assert.deepEqual({ ...failedManifest }, { ready: 0, data_version: 1 });
  assert.notEqual(failedControl.maintenance_token, "");
});

test("an existing ready platform is unreadable between chunks and keeps its bumped version after publication", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  const sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "跨月店铺", date: "2026-08-01", sku: "SKU-AUG" });
  insertJdRow(sqlite, { shop: "跨月店铺", date: "2026-09-01", sku: "SKU-SEP" });
  sqlite.close();

  await runPromotionAggregateBackfill({ database: databasePath, apply: true, platform: "京东" }, () => undefined);
  let inspected = new DatabaseSync(databasePath, { readOnly: true });
  const openingVersion = Number(inspected.prepare(`SELECT data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.data_version);
  inspected.close();

  let observedChunks = 0;
  const result = await runPromotionAggregateBackfill(
    { database: databasePath, apply: true, platform: "京东" },
    (line) => {
      const event = JSON.parse(line) as { shop?: string };
      if (!event.shop) return;
      observedChunks += 1;
      const during = new DatabaseSync(databasePath, { readOnly: true });
      const manifest = during.prepare(`SELECT ready,data_version FROM netshop_promotion_aggregate_manifest
        WHERE platform='京东'`).get()!;
      during.close();
      assert.deepEqual({ ...manifest }, { ready: 0, data_version: openingVersion + 1 });
    },
  );
  assert.equal(observedChunks, 2);
  assert.equal(result.chunks.length, 2);
  inspected = new DatabaseSync(databasePath, { readOnly: true });
  assert.deepEqual({ ...inspected.prepare(`SELECT ready,data_version FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()! }, { ready: 1, data_version: openingVersion + 1 });
  assert.equal(inspected.prepare(`SELECT maintenance_token FROM netshop_promotion_aggregate_control
    WHERE platform='京东'`).get()!.maintenance_token, "");
  inspected.close();
});

test("a failed ready-platform maintenance keeps publication intent across a partial retry and fences the old owner", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  let sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "重试店铺", date: "2026-08-01", sku: "SKU-RETRY" });
  insertJdRow(sqlite, { shop: "稳定店铺", date: "2026-08-01", sku: "SKU-STABLE" });
  sqlite.close();

  await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
  }, () => undefined);

  sqlite = new DatabaseSync(databasePath);
  sqlite.prepare(`UPDATE netshop_rows SET metrics_json='not-json'
    WHERE platform='京东' AND shop_name='重试店铺'`).run();
  sqlite.close();

  await assert.rejects(runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
    shop: "重试店铺",
  }, () => undefined));

  sqlite = new DatabaseSync(databasePath);
  const failedManifest = sqlite.prepare(`SELECT ready,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()!;
  const failedControl = sqlite.prepare(`SELECT maintenance_token,maintenance_version,maintenance_previous_ready
    FROM netshop_promotion_aggregate_control WHERE platform='京东'`).get()!;
  assert.equal(failedManifest.ready, 0);
  assert.equal(failedControl.maintenance_version, failedManifest.data_version);
  assert.equal(failedControl.maintenance_previous_ready, 1);
  assert.match(String(failedControl.maintenance_token), /^[A-Za-z0-9_-]{16,128}$/);
  const failedToken = String(failedControl.maintenance_token);
  const failedVersion = Number(failedControl.maintenance_version);
  sqlite.prepare(`UPDATE netshop_rows SET metrics_json='{"花费":2,"总订单金额":3}'
    WHERE platform='京东' AND shop_name='重试店铺'`).run();
  sqlite.close();

  let observedReplacementFence = false;
  const retried = await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
    shop: "重试店铺",
  }, (line) => {
    const event = JSON.parse(line) as { shop?: string };
    if (!event.shop) return;
    const during = new DatabaseSync(databasePath, { readOnly: true });
    const current = during.prepare(`SELECT control.maintenance_token,control.maintenance_version,
        control.maintenance_previous_ready,manifest.ready,manifest.data_version
      FROM netshop_promotion_aggregate_control control
      JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform=control.platform
      WHERE control.platform='京东'`).get()!;
    const staleOwnerHeld = during.prepare(`SELECT COUNT(*) AS count
      FROM netshop_promotion_aggregate_control control
      JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform=control.platform
      WHERE control.platform='京东' AND control.maintenance_token=?
        AND control.maintenance_version=? AND manifest.ready=0 AND manifest.data_version=?`)
      .get(failedToken, failedVersion, failedVersion)!.count;
    during.close();
    assert.notEqual(current.maintenance_token, failedToken);
    assert.equal(current.maintenance_version, failedVersion + 1);
    assert.equal(current.maintenance_previous_ready, 1);
    assert.equal(current.ready, 0);
    assert.equal(current.data_version, failedVersion + 1);
    assert.equal(staleOwnerHeld, 0);
    observedReplacementFence = true;
  });

  assert.equal(observedReplacementFence, true);
  assert.deepEqual(retried.manifestPlatforms, ["京东"]);
  assert.equal(retried.chunks[0]?.canUse, true);
  const inspected = new DatabaseSync(databasePath, { readOnly: true });
  assert.deepEqual({ ...inspected.prepare(`SELECT ready,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 1,
    data_version: failedVersion + 1,
  });
  assert.deepEqual({ ...inspected.prepare(`SELECT maintenance_token,maintenance_version,maintenance_previous_ready
    FROM netshop_promotion_aggregate_control WHERE platform='京东'`).get()! }, {
    maintenance_token: "",
    maintenance_version: 0,
    maintenance_previous_ready: 0,
  });
  inspected.close();
});

test("a failed ready-platform maintenance keeps publication intent across an intervening promotion import", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  let sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "导入重试店铺", date: "2026-08-01", sku: "SKU-RETRY" });
  insertJdRow(sqlite, { shop: "完整性店铺", date: "2026-08-01", sku: "SKU-STABLE" });
  sqlite.close();

  await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
  }, () => undefined);

  await assert.rejects(runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
    shop: "导入重试店铺",
  }, (line) => {
    const event = JSON.parse(line) as { shop?: string };
    if (event.shop) throw new Error("模拟聚合块完成后的任务失败");
  }), /模拟聚合块完成后的任务失败/);

  sqlite = new DatabaseSync(databasePath);
  const failedControl = sqlite.prepare(`SELECT maintenance_token,maintenance_version,maintenance_previous_ready
    FROM netshop_promotion_aggregate_control WHERE platform='京东'`).get()!;
  const failedToken = String(failedControl.maintenance_token);
  const failedVersion = Number(failedControl.maintenance_version);
  assert.match(failedToken, /^[A-Za-z0-9_-]{16,128}$/);
  assert.equal(failedControl.maintenance_previous_ready, 1);
  assert.deepEqual({ ...sqlite.prepare(`SELECT ready,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 0,
    data_version: failedVersion,
  });

  const batchId = "promotion-import-during-maintenance";
  const importedRow = jdPromotionRow({
    shop: "导入重试店铺",
    date: "2026-08-01",
    sku: "SKU-RETRY",
    spendCents: 200,
  });
  sqlite.prepare("INSERT INTO netshop_import_batches (id,status) VALUES (?,'processing')").run(batchId);
  const db = aggregateAdapter(sqlite);
  await db.batch([
    db.prepare(`UPDATE netshop_rows
      SET metrics_json=?, raw_json=?, last_import_batch_id=?
      WHERE platform='京东' AND shop_name=? AND business_date=? AND sku_id=?`).bind(
      JSON.stringify(importedRow.metrics),
      JSON.stringify(importedRow.raw),
      batchId,
      importedRow.shopName,
      importedRow.businessDate,
      importedRow.skuId,
    ),
    ...buildPromotionAggregatePublishStatements(db, {
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName: importedRow.shopName,
      startDate: importedRow.businessDate,
      endDate: importedRow.businessDate,
      batchId,
      rows: [importedRow],
    }),
    db.prepare("UPDATE netshop_import_batches SET status='completed' WHERE id=?").bind(batchId),
  ]);

  const afterImport = sqlite.prepare(`SELECT control.maintenance_token,control.maintenance_version,
      control.maintenance_previous_ready,manifest.ready,manifest.data_version
    FROM netshop_promotion_aggregate_control control
    JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform=control.platform
    WHERE control.platform='京东'`).get()!;
  assert.equal(afterImport.maintenance_token, failedToken);
  assert.equal(afterImport.maintenance_version, failedVersion);
  assert.equal(afterImport.maintenance_previous_ready, 1);
  assert.equal(afterImport.ready, 0);
  assert.equal(afterImport.data_version, failedVersion + 1, "the legal import must invalidate the failed maintenance fence");
  assert.equal(sqlite.prepare(`SELECT ready FROM netshop_promotion_aggregate_state
    WHERE platform='京东' AND shop_name='导入重试店铺' AND business_date='2026-08-01'`).get()!.ready, 1);
  assert.equal(sqlite.prepare(`SELECT COUNT(*) AS count
    FROM netshop_promotion_aggregate_control control
    JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform=control.platform
    WHERE control.platform='京东' AND control.maintenance_token=?
      AND control.maintenance_version=? AND manifest.ready=0 AND manifest.data_version=?`)
    .get(failedToken, failedVersion, failedVersion)!.count, 0, "the old owner must remain fenced after the import bump");
  sqlite.close();

  const retried = await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
    shop: "导入重试店铺",
  }, () => undefined);
  assert.deepEqual(retried.manifestPlatforms, ["京东"]);
  assert.equal(retried.chunks[0]?.canUse, true);

  const inspected = new DatabaseSync(databasePath, { readOnly: true });
  assert.deepEqual({ ...inspected.prepare(`SELECT ready,source_shop_count,raw_row_count,
      product_row_count,shop_day_count,state_day_count,data_version
    FROM netshop_promotion_aggregate_manifest WHERE platform='京东'`).get()! }, {
    ready: 1,
    source_shop_count: 2,
    raw_row_count: 2,
    product_row_count: 2,
    shop_day_count: 2,
    state_day_count: 2,
    data_version: failedVersion + 2,
  });
  assert.deepEqual({ ...inspected.prepare(`SELECT maintenance_token,maintenance_version,maintenance_previous_ready
    FROM netshop_promotion_aggregate_control WHERE platform='京东'`).get()! }, {
    maintenance_token: "",
    maintenance_version: 0,
    maintenance_previous_ready: 0,
  });
  inspected.close();
});

test("full-platform manifest publication rejects orphan aggregate/state rows", async (t) => {
  const directory = createProjectTempDirectory();
  t.after(() => cleanupProjectTempDirectory(directory));
  const databasePath = join(directory, "fixture.sqlite");
  let sqlite = createDatabase(databasePath);
  insertJdRow(sqlite, { shop: "真实店铺", date: "2026-08-01", sku: "SKU-REAL" });
  sqlite.close();
  await runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
    shop: "真实店铺",
  }, () => undefined);

  sqlite = new DatabaseSync(databasePath);
  sqlite.prepare(`INSERT INTO netshop_promotion_product_daily
    (platform,shop_name,business_date,product_id,source)
    VALUES ('京东','幽灵店铺','2026-08-01','SKU-GHOST','jd_promotion')`).run();
  sqlite.prepare(`INSERT INTO netshop_promotion_shop_daily
    (platform,shop_name,business_date,source,product_count,source_row_count)
    VALUES ('京东','幽灵店铺','2026-08-01','jd_promotion',1,1)`).run();
  sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_state
    (platform,shop_name,business_date,source,ready,raw_row_count,product_row_count)
    VALUES ('京东','幽灵店铺','2026-08-01','jd_promotion',1,1,1)`).run();
  sqlite.close();

  await assert.rejects(runPromotionAggregateBackfill({
    database: databasePath,
    apply: true,
    platform: "京东",
  }, () => undefined), /平台完整性验证失败/);
  const inspected = new DatabaseSync(databasePath, { readOnly: true });
  assert.equal(inspected.prepare(`SELECT ready FROM netshop_promotion_aggregate_manifest
    WHERE platform='京东'`).get()!.ready, 0);
  assert.equal(inspected.prepare("SELECT COUNT(*) AS count FROM netshop_rows").get()!.count, 1);
  inspected.close();
});
