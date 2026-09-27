import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import type { AppPrincipal } from "../lib/auth/authorization";
import type { SalesConsumerReader } from "../lib/django/sales-consumer-reader";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return { url: "data:text/javascript,export const env={};", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const {
  ensureNetshopSchema,
  getNetshopProductCatalog: getNetshopProductCatalogCore,
  getNetshopProductCatalogPage: getNetshopProductCatalogPageCore,
  getNetshopProductPerformance,
  getNetshopProductPerformancePage,
  getNetshopProductPerformanceSummary,
  getNetshopPromotionItems,
  getNetshopPromotionOverview,
  getNetshopPromotionPerformance,
  NETSHOP_DAILY_SERIES_LIMIT,
  saveNetshopImport,
} = await import("../lib/netshop/database");
const { resolveNetshopSalesOutletMatches } = await import("../lib/netshop/sales-shop-aliases");
const { ensurePromotionAggregateSchema, rebuildPromotionAggregates } = await import("../lib/netshop/promotion-aggregate");
const { PublicApiError } = await import("../lib/http/api-error");
const { netshopBatchId } = await import("../lib/netshop/batch-identity");
const {
  buildImportAttemptHash,
  buildImportContentFingerprint,
  ensureImportFingerprintSchema,
  reserveImportFingerprint,
} = await import("../lib/imports/content-fingerprint");

const testPrincipal: AppPrincipal = {
  email: "netshop-test@example.com",
  displayName: "Netshop test",
  role: "admin",
  scope: null,
};

function channelMatchesPlatform(channel: string, platform: string) {
  const normalizedChannel = channel.trim();
  const normalizedPlatform = platform.trim();
  return normalizedChannel === normalizedPlatform
    || ["-", "—", "–", ":", "："].some((separator) => normalizedChannel.startsWith(`${normalizedPlatform}${separator}`));
}

function sqliteSalesReader(sqlite: DatabaseSync): SalesConsumerReader {
  const currentRevision = () => {
    try {
      const row = sqlite.prepare("SELECT sales_revision FROM sales_overview_cache_state WHERE id=1").get() as
        | { sales_revision?: number | string }
        | undefined;
      return `sales:test:${String(row?.sales_revision ?? 1)}`;
    } catch {
      return "sales:test:1";
    }
  };
  return {
    async read(actualPrincipal, request) {
      assert.equal(actualPrincipal, testPrincipal);
      if (request.operation === "freshness") {
        return {
          revision: currentRevision(),
          data: {
            dataStartDate: "2026-08-01",
            dataCutoffDate: "2026-08-03",
            latestBatch: { id: "sales-batch", fileName: "sales.xlsx", completedAt: "2026-08-03 12:00:00", rowCount: 1 },
          },
        } as never;
      }
      assert.equal(request.operation, "netshop_product_metrics");
      if (request.operation !== "netshop_product_metrics") throw new Error("unexpected sales operation");
      const sourceRows = sqlite.prepare(`SELECT ship_time,warehouse,product_code,product_name,platform,channel,
        shop_name,online_spec_code,allocated_amount_cents,gross_profit_cents,quantity,cost_amount_cents
        FROM sales_order_lines`).all() as Array<Record<string, string | number>>;
      const allowedChannels = request.allowedChannels ?? null;
      const eligible = sourceRows.filter((row) => String(row.warehouse).trim() !== "刷刷仓"
        && channelMatchesPlatform(String(row.channel), String(row.platform))
        && (allowedChannels === null || allowedChannels.includes(String(row.channel).trim())));
      const scoped = eligible.filter((row) => request.outletScopes.some((scope) =>
        String(row.platform).trim() === scope.platform
        && String(row.shop_name).trim() === scope.rawShopName
        && (scope.rawChannel === null || String(row.channel).trim() === scope.rawChannel)));
      const dataCutoffDate = scoped.map((row) => String(row.ship_time).slice(0, 10)).sort().at(-1) ?? null;
      const combined = new Map<string, {
        platform: string;
        shopName: string;
        salesProductCode: string;
        grossSalesCents: number;
        refundAmountCents: number;
        netSalesCents: number;
        grossProfitCents: number;
        absoluteQuantity: number;
        absoluteCostCents: number;
      }>();
      const startDate = request.startDate ?? null;
      const endDate = request.endDate ?? null;
      if (startDate !== null && endDate !== null) {
        for (const row of eligible) {
          const businessDate = String(row.ship_time).slice(0, 10);
          if (businessDate < startDate || businessDate >= endDate
            || String(row.product_code) === "ERP_PRICE_ADJUSTMENT"
            || String(row.product_name).trim() === "补差价专用") continue;
          const salesProductCode = String(row.online_spec_code || row.product_code);
          const targets = new Set<string>();
          for (const identity of request.identities) {
            if (String(row.platform).trim() !== identity.platform
              || String(row.shop_name).trim() !== identity.rawShopName
              || salesProductCode !== identity.salesProductCode
              || (identity.rawChannel !== null && String(row.channel).trim() !== identity.rawChannel)) continue;
            const key = JSON.stringify([identity.platform, identity.canonicalShopName, identity.salesProductCode]);
            if (targets.has(key)) continue;
            targets.add(key);
            const aggregate = combined.get(key) ?? {
              platform: identity.platform,
              shopName: identity.canonicalShopName,
              salesProductCode: identity.salesProductCode,
              grossSalesCents: 0,
              refundAmountCents: 0,
              netSalesCents: 0,
              grossProfitCents: 0,
              absoluteQuantity: 0,
              absoluteCostCents: 0,
            };
            const sales = Number(row.allocated_amount_cents);
            aggregate.grossSalesCents += Math.max(0, sales);
            aggregate.refundAmountCents += Math.max(0, -sales);
            aggregate.netSalesCents += sales;
            aggregate.grossProfitCents += Number(row.gross_profit_cents);
            aggregate.absoluteQuantity += Math.abs(Number(row.quantity));
            aggregate.absoluteCostCents += Math.abs(Number(row.cost_amount_cents));
            combined.set(key, aggregate);
          }
        }
      }
      return {
        revision: currentRevision(),
        data: { dataCutoffDate, platform: "京东", rows: [...combined.values()] },
      } as never;
    },
  };
}

function getNetshopProductCatalog(
  db: unknown,
  input: Parameters<typeof getNetshopProductCatalogCore>[2],
) {
  const sqlite = (db as { __sqlite: DatabaseSync }).__sqlite;
  return getNetshopProductCatalogCore(
    db as never,
    testPrincipal,
    input,
    sqliteSalesReader(sqlite),
  );
}

function getNetshopProductCatalogPage(
  db: unknown,
  input: Parameters<typeof getNetshopProductCatalogPageCore>[2],
) {
  const sqlite = (db as { __sqlite: DatabaseSync }).__sqlite;
  return getNetshopProductCatalogPageCore(
    db as never,
    testPrincipal,
    input,
    sqliteSalesReader(sqlite),
  );
}

function createDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE netshop_rows (
      id INTEGER PRIMARY KEY,
      source TEXT NOT NULL,
      dataset TEXT NOT NULL,
      platform TEXT NOT NULL,
      shop_name TEXT NOT NULL,
      business_date TEXT,
      snapshot_date TEXT,
      product_code TEXT NOT NULL DEFAULT '',
      product_name TEXT NOT NULL DEFAULT '',
      sku_id TEXT NOT NULL DEFAULT '',
      spu_id TEXT NOT NULL DEFAULT '',
      metrics_json TEXT NOT NULL DEFAULT '{}',
      raw_json TEXT NOT NULL DEFAULT '{}',
      last_import_batch_id TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX netshop_rows_source_dataset_scope_date_idx
      ON netshop_rows (source,dataset,platform,shop_name,business_date);
    CREATE TABLE netshop_import_batches (
      id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT '', dataset TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, platform TEXT NOT NULL, shop_name TEXT NOT NULL,
      snapshot_date TEXT, created_at TEXT NOT NULL, completed_at TEXT
    );
    CREATE TABLE netshop_product_daily_revisions (
      platform TEXT PRIMARY KEY NOT NULL,
      data_version INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE netshop_product_daily_scope_revisions (
      platform TEXT NOT NULL,
      shop_name TEXT NOT NULL,
      data_version INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (platform,shop_name)
    );
  `);
  return sqlite;
}

function createProductCatalogDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE netshop_import_batches (
      id TEXT PRIMARY KEY, source TEXT NOT NULL, dataset TEXT NOT NULL, platform TEXT NOT NULL,
      shop_name TEXT NOT NULL, file_name TEXT NOT NULL, file_size_bytes INTEGER NOT NULL,
      file_hash TEXT NOT NULL, sheet_name TEXT NOT NULL, status TEXT NOT NULL,
      row_count INTEGER NOT NULL, inserted_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL,
      warning_count INTEGER NOT NULL, date_min TEXT, date_max TEXT, snapshot_date TEXT,
      warnings_json TEXT NOT NULL, totals_json TEXT NOT NULL, note TEXT NOT NULL,
      created_at TEXT NOT NULL, completed_at TEXT
    );
    CREATE TABLE netshop_rows (
      id INTEGER PRIMARY KEY, source_row_key TEXT NOT NULL, source_row_hash TEXT NOT NULL,
      first_import_batch_id TEXT NOT NULL, last_import_batch_id TEXT NOT NULL,
      source_row_number INTEGER NOT NULL, source TEXT NOT NULL, dataset TEXT NOT NULL,
      platform TEXT NOT NULL, shop_name TEXT NOT NULL, business_date TEXT, snapshot_date TEXT,
      product_code TEXT NOT NULL, product_name TEXT NOT NULL, sku_id TEXT NOT NULL, spu_id TEXT NOT NULL,
      warehouse_type TEXT NOT NULL, metrics_json TEXT NOT NULL, raw_json TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE sales_order_lines (
      id INTEGER PRIMARY KEY, ship_time TEXT NOT NULL, warehouse TEXT NOT NULL,
      product_code TEXT NOT NULL, product_name TEXT NOT NULL, platform TEXT NOT NULL,
      channel TEXT NOT NULL, shop_name TEXT NOT NULL, online_spec_code TEXT NOT NULL, allocated_amount_cents INTEGER NOT NULL,
      gross_profit_cents INTEGER NOT NULL, quantity REAL NOT NULL, cost_amount_cents INTEGER NOT NULL
    );
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL,
      erp_product_revision INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO sales_overview_cache_state (id,sales_revision,erp_product_revision) VALUES (1,1,1);
    CREATE INDEX netshop_rows_product_batch_page_idx
      ON netshop_rows (last_import_batch_id,shop_name,product_name,sku_id,platform,id)
      WHERE source IN ('jd_product_master','tmall_product_master') AND dataset='product_master';
    INSERT INTO netshop_import_batches VALUES (
      'master-batch','jd_product_master','product_master','京东','目录店铺','master.xlsx',1,
      'hash','sheet','completed',102,102,0,0,NULL,NULL,'2026-08-01','[]','{}','',
      '2026-08-01 08:00:00','2026-08-01 08:01:00'
    );
  `);
  const product = sqlite.prepare(`INSERT INTO netshop_rows VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const sale = sqlite.prepare(`INSERT INTO sales_order_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  sqlite.exec("BEGIN");
  for (let index = 0; index < 100; index += 1) {
    const sku = `SKU-PAGE-${String(index).padStart(3, "0")}`;
    product.run(
      index + 1, `row-${index}`, `hash-${index}`, "master-batch", "master-batch", index + 1,
      "jd_product_master", "product_master", "京东", "目录店铺", null, "2026-08-01",
      sku, `商品-${index}`, sku, `SPU-${index}`, "", "{}",
      JSON.stringify({ "商家SKU": sku, "商品状态": "上架" }),
      "2026-08-01 08:00:00", "2026-08-01 08:00:00",
    );
    sale.run(index + 1, "2026-08-01 12:00:00", "主仓", sku, `商品-${index}`, "京东", "京东", "目录店铺", sku, 100, 20, 1, 80);
  }
  const extraBatches = [
    { id: "tm-same-batch", platform: "天猫", shopName: "目录店铺", source: "tmall_product_master" },
    { id: "jd-other-batch", platform: "京东", shopName: "另一店铺", source: "jd_product_master" },
    { id: "tm-other-batch", platform: "天猫", shopName: "另一店铺", source: "tmall_product_master" },
  ];
  const batchInsert = sqlite.prepare(`INSERT INTO netshop_import_batches VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  for (const batch of extraBatches) {
    const rowCount = batch.id === "jd-other-batch" ? 2 : 1;
    batchInsert.run(
      batch.id, batch.source, "product_master", batch.platform, batch.shopName, `${batch.id}.xlsx`, 1,
      `${batch.id}-hash`, "sheet", "completed", rowCount, rowCount, 0, 0, null, null, "2026-08-01", "[]", "{}", "",
      "2026-08-01 08:00:00", "2026-08-01 08:01:00",
    );
  }
  const extraRows = [
    { id: 101, batchId: "master-batch", source: "jd_product_master", platform: "京东", shopName: "目录店铺", code: "PAIR-JD-SAME" },
    { id: 102, batchId: "tm-same-batch", source: "tmall_product_master", platform: "天猫", shopName: "目录店铺", code: "PAIR-TM-SAME" },
    { id: 103, batchId: "jd-other-batch", source: "jd_product_master", platform: "京东", shopName: "另一店铺", code: "PAIR-JD-OTHER" },
    { id: 104, batchId: "tm-other-batch", source: "tmall_product_master", platform: "天猫", shopName: "另一店铺", code: "PAIR-TM-OTHER" },
    { id: 105, batchId: "master-batch", source: "jd_product_master", platform: "京东", shopName: "目录店铺", code: "CROSS-SHOP-A", salesCode: "SHARED-SALES-SKU" },
    { id: 106, batchId: "jd-other-batch", source: "jd_product_master", platform: "京东", shopName: "另一店铺", code: "CROSS-SHOP-B", salesCode: "SHARED-SALES-SKU" },
  ];
  for (const row of extraRows) {
    product.run(
      row.id, `row-${row.id}`, `hash-${row.id}`, row.batchId, row.batchId, row.id,
      row.source, "product_master", row.platform, row.shopName, null, "2026-08-01",
      row.code, row.code, row.code, `SPU-${row.code}`, "", "{}",
      JSON.stringify({ "商家SKU": "salesCode" in row ? row.salesCode : row.code, "商品状态": "上架" }),
      "2026-08-01 08:00:00", "2026-08-01 08:00:00",
    );
  }
  sale.run(101, "2026-08-01 12:00:00", "主仓", "P-A", "跨店商品A", "京东", "京东", "目录店铺", "SHARED-SALES-SKU", 100, 20, 1, 80);
  sale.run(102, "2026-08-02 12:00:00", "主仓", "P-B", "跨店商品B", "京东", "京东", "另一店铺", "SHARED-SALES-SKU", 900, 180, 1, 720);
  sale.run(103, "2026-08-03 12:00:00", "主仓", "P-C", "错误渠道商品", "京东", "天猫", "目录店铺", "SHARED-SALES-SKU", 700, 140, 1, 560);
  sqlite.exec("COMMIT");
  return sqlite;
}

function adapter(
  sqlite: DatabaseSync,
  reads: string[],
  bindCounts: number[] = [],
  afterRead?: (sql: string) => void | Promise<void>,
  transformFirst?: (sql: string, value: unknown) => unknown,
) {
  return {
    __sqlite: sqlite,
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() {
          reads.push(sql);
          bindCounts.push(values.length);
          const result = (sqlite.prepare(sql).get(...values) ?? null) as T | null;
          await afterRead?.(sql);
          return (transformFirst ? transformFirst(sql, result) : result) as T | null;
        },
        async all<T>() {
          reads.push(sql);
          bindCounts.push(values.length);
          const results = sqlite.prepare(sql).all(...values) as T[];
          await afterRead?.(sql);
          return { results };
        },
        async run() { return sqlite.prepare(sql).run(...values); },
      };
    },
  };
}

function transactionalAdapter(sqlite: DatabaseSync) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() {
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
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
    },
  };
}

function installPromotionManifest(sqlite: DatabaseSync, platform: "京东" | "天猫") {
  sqlite.prepare(`INSERT INTO netshop_promotion_aggregate_manifest (platform,ready,completed_at)
    VALUES (?,1,CURRENT_TIMESTAMP)
    ON CONFLICT(platform) DO UPDATE SET ready=1,completed_at=CURRENT_TIMESTAMP`).run(platform);
  sqlite.prepare(`UPDATE netshop_promotion_aggregate_control
    SET maintenance_token='',maintenance_version=0,maintenance_previous_ready=0,
      maintenance_started_at=NULL,updated_at=CURRENT_TIMESTAMP
    WHERE platform=?`).run(platform);
}

test("product catalog runtime and projection migration indexes match the paging total order", async () => {
  const expectedColumns = ["last_import_batch_id", "shop_name", "product_name", "sku_id", "platform", "id"];
  const runtimeSqlite = new DatabaseSync(":memory:");
  await ensureNetshopSchema(transactionalAdapter(runtimeSqlite) as never);
  assert.deepEqual(
    (runtimeSqlite.prepare("PRAGMA index_info('netshop_rows_product_batch_page_idx')").all() as Array<{ name: string }>).map((row) => row.name),
    expectedColumns,
  );
  runtimeSqlite.close();

  const migrationSqlite = new DatabaseSync(":memory:");
  migrationSqlite.exec(`
    CREATE TABLE netshop_rows (
      id INTEGER PRIMARY KEY,
      last_import_batch_id TEXT NOT NULL,
      shop_name TEXT NOT NULL,
      product_name TEXT NOT NULL,
      sku_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      source TEXT NOT NULL,
      dataset TEXT NOT NULL
    );
    CREATE INDEX netshop_rows_product_batch_page_idx
      ON netshop_rows (last_import_batch_id,shop_name,product_name,sku_id)
      WHERE source IN ('jd_product_master','tmall_product_master') AND dataset='product_master';
  `);
  const migrationSql = readFileSync(
    new URL("../drizzle/0076_netshop_product_query_projections.sql", import.meta.url),
    "utf8",
  );
  migrationSqlite.exec(migrationSql);
  migrationSqlite.exec(migrationSql);
  assert.deepEqual(
    (migrationSqlite.prepare("PRAGMA index_info('netshop_rows_product_batch_page_idx')").all() as Array<{ name: string }>).map((row) => row.name),
    expectedColumns,
  );
  migrationSqlite.close();
});

test("netshop publish maps a proven commit-fence takeover to a safe 409 and rolls back facts", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = transactionalAdapter(sqlite);
  let takeOverBeforeNextBatch = false;
  const db = {
    prepare: base.prepare,
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      if (takeOverBeforeNextBatch) {
        takeOverBeforeNextBatch = false;
        sqlite.prepare("UPDATE import_scope_heads SET owner_token='new-owner' WHERE domain='netshop'").run();
      }
      return base.batch(statements);
    },
  };
  await ensureNetshopSchema(db as never);
  await ensureImportFingerprintSchema(db as never);
  const row = {
    sourceRowNumber: 1,
    sourceRowKey: JSON.stringify(["sku_daily", "京东", "测试店", "2026-08-01", "SKU-1"]),
    sourceRowHash: "a".repeat(64),
    source: "jd_sku_daily" as const,
    dataset: "sku_daily",
    platform: "京东",
    shopName: "测试店",
    businessDate: "2026-08-01",
    snapshotDate: "",
    productCode: "SKU-1",
    productName: "测试商品",
    skuId: "SKU-1",
    spuId: "",
    warehouseType: "",
    metrics: { visitors: 1 },
    raw: {},
  };
  const fingerprint = await buildImportContentFingerprint({
    domain: "netshop",
    scope: { source: row.source, dataset: row.dataset, platform: row.platform, shopName: row.shopName, startDate: row.businessDate, endDate: row.businessDate },
    lockScope: { source: row.source, dataset: row.dataset, platform: row.platform, shopName: row.shopName },
    rows: [row],
  });
  const fileHash = await buildImportAttemptHash({ fingerprint, currentStateToken: "initial" });
  const batchId = netshopBatchId({ source: row.source, platform: row.platform, shopName: row.shopName, fileHash });
  const owner = await reserveImportFingerprint(db as never, {
    ...fingerprint,
    batchId,
    importHash: fileHash,
    rawFileHash: "b".repeat(64),
    currentStateToken: "initial",
  });
  takeOverBeforeNextBatch = true;
  await assert.rejects(saveNetshopImport(db as never, {
    source: row.source,
    dataset: row.dataset,
    platform: row.platform,
    shopName: row.shopName,
    fileHash,
    fileName: "daily.xlsx",
    fileSizeBytes: 1,
    sheetName: "SKU",
    rows: [row],
    warnings: [],
    totals: {},
    note: "",
    replaceScope: { startDate: row.businessDate, endDate: row.businessDate },
    reservationFence: { domain: fingerprint.domain, scopeKey: fingerprint.scopeKey, batchId, attemptId: owner.attemptId },
  }), (error: unknown) => error instanceof PublicApiError && error.status === 409 && error.code === "conflict");
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM netshop_rows").get()?.count, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM netshop_import_batches WHERE id=?").get(batchId)?.count, 0);
  assert.equal(sqlite.prepare("SELECT owner_token ownerToken FROM import_scope_heads WHERE domain='netshop'").get()?.ownerToken, "new-owner");
  sqlite.close();
});

test("100k product-day query stays equivalent, bounded, and avoids the duplicate total scan", async () => {
  const sqlite = createDatabase();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`);
  const metrics = JSON.stringify({
    pageViews: 10,
    visitors: 5,
    transactionAmountCents: 10_000,
    transactionQuantity: 1,
    transactionCustomers: 1,
  });
  sqlite.exec("BEGIN");
  for (let index = 0; index < 100_000; index += 1) {
    const sku = `SKU-${String(index % 5_000).padStart(5, "0")}`;
    const day = String(index % 20 + 1).padStart(2, "0");
    insert.run(index + 1, "jd_sku_daily", "sku_daily", "京东", `店铺-${index % 4}`,
      `2026-07-${day}`, sku, `商品-${sku}`, sku, metrics, "{}");
  }
  sqlite.exec("COMMIT");
  insert.run(100_001, "jd_sku_daily", "sku_daily", "京东", "店铺-边界",
    "2026-07-21", "SKU-BOUNDARY", "区间外商品", "SKU-BOUNDARY", JSON.stringify({
      pageViews: 1,
      visitors: 1,
      transactionAmountCents: 999_999_999,
      transactionQuantity: 1,
      transactionCustomers: 1,
    }), "{}");

  const reads: string[] = [];
  const startedAt = performance.now();
  const result = await getNetshopProductPerformance(adapter(sqlite, reads) as never, {
    dimension: "sku",
    startDate: "2026-07-01",
    endDate: "2026-07-20",
    page: 1,
    pageSize: 20,
  });
  const elapsedMs = performance.now() - startedAt;
  const reference = sqlite.prepare(`SELECT
      COUNT(DISTINCT platform || char(31) || shop_name || char(31) || sku_id) AS products,
      SUM(CAST(json_extract(metrics_json,'$.transactionAmountCents') AS INTEGER)) AS amount
    FROM netshop_rows
    WHERE source='jd_sku_daily' AND dataset='sku_daily'
      AND business_date>='2026-07-01' AND business_date<'2026-07-21'`).get() as { products: number; amount: number };

  assert.equal(result.pagination.total, reference.products);
  assert.equal(result.summary.productCount, reference.products);
  assert.equal(result.summary.transactionAmountCents, reference.amount);
  assert.equal(result.pagination.returned, 20);
  assert.equal(result.coverage.actualDates.length, 20);
  assert.equal(reads.length, 3);
  const fullProjectionReads = reads.filter((sql) => /WITH filtered AS MATERIALIZED/i.test(sql));
  assert.equal(fullProjectionReads.length, 1, "full product performance should use one fact projection query");
  assert.match(fullProjectionReads[0]!, /available_facts AS MATERIALIZED/i);
  assert.match(fullProjectionReads[0]!, /grouped_items AS MATERIALIZED/i);
  assert.equal(reads.some((sql) => /AS total FROM netshop_rows/.test(sql)), false);
  assert.ok(elapsedMs < 5_000, `100k product-day query took ${Math.round(elapsedMs)}ms`);
  const readsBeforeInvalidInput = reads.length;
  await assert.rejects(
    getNetshopProductPerformance(adapter(sqlite, reads) as never, {
      dimension: "sku",
      startDate: "2026-07-31",
      endDate: "2026-07-01",
    }),
    { name: "NetshopQueryError" },
  );
  assert.equal(reads.length, readsBeforeInvalidInput, "invalid ranges must fail before any database read");
  sqlite.close();
});

test("platform-qualified outlet filters isolate same-name shops and never expand cross-platform pairs", async () => {
  const sqlite = createDatabase();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
  const identities = [
    { platform: "京东", shopName: "同名店", id: "JD-SAME" },
    { platform: "天猫", shopName: "同名店", id: "TM-SAME" },
    { platform: "京东", shopName: "另一店", id: "JD-OTHER" },
    { platform: "天猫", shopName: "另一店", id: "TM-OTHER" },
  ];
  let rowId = 1;
  for (const identity of identities) {
    insert.run(
      rowId++,
      identity.platform === "京东" ? "jd_sku_daily" : "tmall_product_daily",
      "spu_daily",
      identity.platform,
      identity.shopName,
      "2026-08-01",
      identity.id,
      identity.id,
      `${identity.id}-SKU`,
      identity.id,
      JSON.stringify({ visitors: 1, transactionAmountCents: 100, transactionCustomers: 1 }),
      "{}",
    );
    insert.run(
      rowId++,
      identity.platform === "京东" ? "jd_promotion" : "tmall_promotion",
      identity.platform === "京东" ? "ad" : "promotion_daily",
      identity.platform,
      identity.shopName,
      "2026-08-01",
      identity.id,
      identity.id === "JD-SAME" ? "" : identity.id,
      identity.platform === "京东" ? identity.id : "",
      identity.platform === "天猫" ? identity.id : "",
      JSON.stringify({ spendCents: 10, netTransactionAmountCents: 100, impressions: 10, clicks: 1, netOrders: 1 }),
      identity.id === "JD-SAME" ? JSON.stringify({ 产品线: "京东产品线" }) : "{}",
    );
  }

  const sameNameOnly = await getNetshopProductPerformance(adapter(sqlite, []) as never, {
    dimension: "spu",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    pageSize: 100,
    outlets: [{ platform: "京东", shopName: "同名店" }],
  });
  assert.equal(sameNameOnly.summary.productCount, 1);
  assert.deepEqual(sameNameOnly.items.map((item) => `${item.platform}/${item.id}`), ["京东/JD-SAME"]);
  const invalidSelectionReads: string[] = [];
  await assert.rejects(
    getNetshopProductPerformance(adapter(sqlite, invalidSelectionReads) as never, {
      dimension: "spu",
      platformNames: ["京东"],
      outlets: [{ platform: "天猫", shopName: "同名店" }],
    }),
    /outlet 平台必须属于当前 platform 筛选/,
  );
  assert.equal(invalidSelectionReads.length, 0);

  const exactPairs = [
    { platform: "京东", shopName: "同名店" },
    { platform: "天猫", shopName: "另一店" },
  ];
  const product = await getNetshopProductPerformance(adapter(sqlite, []) as never, {
    dimension: "spu",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    pageSize: 100,
    outlets: exactPairs,
  });
  assert.equal(product.summary.productCount, 2);
  assert.deepEqual(new Set(product.items.map((item) => `${item.platform}/${item.id}`)), new Set(["京东/JD-SAME", "天猫/TM-OTHER"]));

  const promotion = await getNetshopPromotionPerformance(adapter(sqlite, []) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    pageSize: 500,
    outlets: exactPairs,
  });
  assert.equal(promotion.summary.productCount, 2);
  assert.deepEqual(new Set(promotion.items.map((item) => `${item.platform}/${item.id}`)), new Set(["京东/JD-SAME", "天猫/TM-OTHER"]));
  assert.deepEqual(promotion.filterOptions, {
    shops: [
      { platform: "京东", shopName: "另一店" },
      { platform: "京东", shopName: "同名店" },
      { platform: "天猫", shopName: "另一店" },
      { platform: "天猫", shopName: "同名店" },
    ],
    pagination: { total: 4, returned: 4, truncated: false },
  });

  await ensurePromotionAggregateSchema(transactionalAdapter(sqlite) as never);
  await rebuildPromotionAggregates(transactionalAdapter(sqlite) as never, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "同名店",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  });
  await rebuildPromotionAggregates(transactionalAdapter(sqlite) as never, {
    source: "tmall_promotion",
    dataset: "promotion_daily",
    platform: "天猫",
    shopName: "另一店",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  });
  installPromotionManifest(sqlite, "京东");
  installPromotionManifest(sqlite, "天猫");
  const overviewReads: string[] = [];
  const overview = await getNetshopPromotionOverview(adapter(sqlite, overviewReads) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东", "天猫"],
    outlets: exactPairs,
  });
  const itemReads: string[] = [];
  const items = await getNetshopPromotionItems(adapter(sqlite, itemReads) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东", "天猫"],
    pageSize: 20,
    outlets: exactPairs,
  });
  assert.deepEqual(overview.summary, promotion.summary);
  assert.deepEqual(overview.coverage, promotion.coverage);
  assert.deepEqual(overview.daily, promotion.daily);
  assert.deepEqual(overview.filterOptions, {
    shops: [
      { platform: "京东", shopName: "同名店" },
      { platform: "天猫", shopName: "另一店" },
    ],
    pagination: { total: 2, returned: 2, truncated: false },
  });
  assert.deepEqual(items.items, promotion.items);
  assert.deepEqual(items.pagination, { page: 1, pageSize: 20, total: 2, returned: 2, truncated: false });
  assert.equal(overviewReads.filter((sql) => /SUM\([^)]*metrics_json/i.test(sql)).length, 1);
  assert.ok(overviewReads.some((sql) => /netshop_promotion_shop_daily/i.test(sql)));
  assert.ok(overviewReads.every((sql) => !/FROM netshop_rows r[\s\S]*jd_promotion|FROM netshop_rows r[\s\S]*tmall_promotion/i.test(sql)));
  assert.equal(overviewReads.filter((sql) => /all_spend_cents/i.test(sql)).length, 1);
  assert.ok(itemReads.some((sql) => /COUNT\(\*\) OVER \(\) AS total_items/i.test(sql)));

  await rebuildPromotionAggregates(transactionalAdapter(sqlite) as never, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "同名店",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  });
  installPromotionManifest(sqlite, "京东");
  const aggregateOverviewReads: string[] = [];
  const aggregateOverview = await getNetshopPromotionOverview(adapter(sqlite, aggregateOverviewReads) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "同名店" }],
  });
  const aggregateItemReads: string[] = [];
  const aggregateItems = await getNetshopPromotionItems(adapter(sqlite, aggregateItemReads) as never, {
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "同名店" }],
    query: "JD-SAME",
  });
  assert.equal(aggregateOverview.summary.productCount, 1);
  assert.equal(aggregateOverview.summary.spendCents, 10);
  assert.deepEqual(aggregateItems.items.map((item) => item.id), ["JD-SAME"]);
  assert.equal(aggregateItems.items[0]?.productName, "京东产品线");
  const aggregateOverviewSql = aggregateOverviewReads.find((sql) => /WITH daily_series/i.test(sql) && /netshop_promotion_shop_daily/i.test(sql));
  const aggregateItemsSql = aggregateItemReads.find((sql) => /WITH grouped_items/i.test(sql));
  assert.ok(aggregateOverviewSql);
  assert.ok(aggregateItemsSql);
  assert.match(aggregateOverviewSql, /INNER JOIN netshop_promotion_aggregate_state/);
  assert.match(aggregateItemsSql, /INNER JOIN netshop_promotion_aggregate_state/);
  assert.doesNotMatch(aggregateOverviewSql, /metrics_json|raw_json/);
  assert.doesNotMatch(aggregateItemsSql, /metrics_json|raw_json/);
  assert.match(aggregateItemsSql, /p\.product_line LIKE \?/);
  sqlite.close();
});

test("split promotion reads fail explicitly when aggregates are not backfilled and never fall back to raw facts", async () => {
  const sqlite = createDatabase();
  await ensurePromotionAggregateSchema(transactionalAdapter(sqlite) as never);
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,sku_id,metrics_json,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);
  insert.run(1, "jd_promotion", "ad", "京东", "已回填店铺", "2026-08-01", "SKU-JD", '{"spendCents":100}', "{}", "jd-batch");
  insert.run(2, "tmall_promotion", "promotion_daily", "天猫", "未回填店铺", "2026-08-01", "", '{"spendCents":200}', "{}", "tmall-batch");
  sqlite.prepare("UPDATE netshop_rows SET spu_id='SPU-TM' WHERE id=2").run();
  await rebuildPromotionAggregates(transactionalAdapter(sqlite) as never, {
    source: "jd_promotion",
    dataset: "ad",
    platform: "京东",
    shopName: "已回填店铺",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  });
  installPromotionManifest(sqlite, "京东");
  const reads: string[] = [];
  const db = adapter(sqlite, reads) as never;
  for (const read of [getNetshopPromotionOverview, getNetshopPromotionItems]) {
    await assert.rejects(
      () => read(db, { startDate: "2026-08-01", endDate: "2026-08-01", platformNames: ["京东", "天猫"] }),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable"
        && /尚未完成回填或已失效/.test(error.message),
    );
  }
  assert.ok(reads.length > 0);
  assert.equal(reads.some((sql) => /FROM netshop_rows/i.test(sql)), false);
  assert.equal(reads.some((sql) => /GROUP BY r\.platform, r\.shop_name, r\.business_date/i.test(sql)), false);
  const readsBeforeInvalidInputs = reads.length;
  await assert.rejects(
    () => getNetshopPromotionOverview(db, { platformNames: ["京东"] }),
    (error: unknown) => error instanceof Error && error.name === "NetshopQueryError" && /显式提供 startDate/.test(error.message),
  );
  await assert.rejects(
    () => getNetshopPromotionItems(db, { startDate: "2026-08-01", endDate: "2026-08-01" }),
    (error: unknown) => error instanceof Error && error.name === "NetshopQueryError" && /显式选择京东或天猫/.test(error.message),
  );
  assert.equal(reads.length, readsBeforeInvalidInputs);
  sqlite.close();
});

test("SPU 商品明细用一次有界查询按平台、店铺与 SPUID 关联最新商品图", async () => {
  const sqlite = createDatabase();
  const productImageHash = "3".repeat(64);
  sqlite.prepare(`INSERT INTO netshop_import_batches
    (id,source,dataset,status,platform,shop_name,snapshot_date,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?)`)
    .run("asset-batch", "tmall_product_assets", "spu_assets", "completed", "天猫", "天猫-志高炊之王专卖店", "2026-08-23", "2026-08-23 08:00:00", "2026-08-23 08:01:00");
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,snapshot_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    1, "tmall_product_daily", "spu_daily", "天猫", "天猫-志高炊之王专卖店", "2026-08-22", null,
    "SPU-CODE", "炊之王商品", "", "562048375368", JSON.stringify({ visitors: 3, transactionAmountCents: 12300, transactionCustomers: 1 }), "{}", "daily-batch",
  );
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,snapshot_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    2, "tmall_product_assets", "spu_assets", "天猫", "天猫-志高炊之王专卖店", null, "2026-08-23",
    "", "炊之王商品", "", "562048375368", "{}", JSON.stringify({
      商品链接: "https://item.taobao.com/item.htm?id=562048375368",
      图片内容SHA256: productImageHash,
      图片对象键: `netshop-product-images/v1/${productImageHash}.jpg`,
      图片MIME: "image/jpeg",
      图片字节数: 7,
    }), "asset-batch",
  );

  const reads: string[] = [];
  const bindCounts: number[] = [];
  const result = await getNetshopProductPerformance(adapter(sqlite, reads, bindCounts) as never, {
    dimension: "spu",
    startDate: "2026-08-22",
    endDate: "2026-08-22",
    pageSize: 100,
    outlets: [{ platform: "天猫", shopName: "天猫-志高炊之王专卖店" }],
  });

  assert.equal(result.items[0]?.imageUrl, `/api/netshop/product-images/${productImageHash}`);
  assert.equal(result.items[0]?.productUrl, "https://item.taobao.com/item.htm?id=562048375368");
  assert.equal(reads.filter((sql) => /asset\.source = 'tmall_product_assets'/.test(sql)).length, 1);
  assert.ok(Math.max(...bindCounts) <= 100);

  sqlite.prepare(`INSERT INTO netshop_import_batches
    (id,source,dataset,status,platform,shop_name,snapshot_date,created_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?)`)
    .run("asset-batch-empty-latest", "tmall_product_assets", "spu_assets", "completed", "天猫", "天猫-志高炊之王专卖店", "2026-08-24", "2026-08-24 08:00:00", "2026-08-24 08:01:00");
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,snapshot_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    3, "tmall_product_assets", "spu_assets", "天猫", "天猫-志高炊之王专卖店", null, "2026-08-24",
    "", "最新快照中的其他商品", "", "999999999999", "{}", "{}", "asset-batch-empty-latest",
  );
  await assert.rejects(
    () => getNetshopProductPerformancePage(adapter(sqlite, []) as never, {
      dimension: "spu",
      startDate: "2026-08-22",
      endDate: "2026-08-22",
      pageSize: 100,
      outlets: [{ platform: "天猫", shopName: "天猫-志高炊之王专卖店" }],
      snapshotToken: result.snapshotToken,
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
    "a newer SPU asset head invalidates the old item-page token",
  );
  const withoutHistoricalFallback = await getNetshopProductPerformance(adapter(sqlite, []) as never, {
    dimension: "spu",
    startDate: "2026-08-22",
    endDate: "2026-08-22",
    pageSize: 100,
    outlets: [{ platform: "天猫", shopName: "天猫-志高炊之王专卖店" }],
  });
  assert.equal(withoutHistoricalFallback.items[0]?.imageUrl, "");
  assert.equal(withoutHistoricalFallback.items[0]?.productUrl, "");
  sqlite.close();
});

test("SPU product performance pins every fact aggregation to the composite scope index", async () => {
  const sqlite = createDatabase();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
  insert.run(
    1, "jd_sku_daily", "spu_daily", "京东", "京东店", "2026-08-01",
    "JD-SPU", "京东商品", "", "JD-SPU", JSON.stringify({ visitors: 3, transactionAmountCents: 300 }), "{}",
  );
  insert.run(
    2, "tmall_product_daily", "spu_daily", "天猫", "天猫店", "2026-08-01",
    "TM-SPU", "天猫商品", "", "TM-SPU", JSON.stringify({ visitors: 5, transactionAmountCents: 500 }), "{}",
  );

  const reads: string[] = [];
  const result = await getNetshopProductPerformance(adapter(sqlite, reads) as never, {
    dimension: "spu",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    page: 1,
    pageSize: 50,
  });
  assert.equal(result.summary.productCount, 2);
  assert.equal(result.summary.visitors, 8);
  assert.equal(result.summary.transactionAmountCents, 800);

  const factReads = reads.filter((sql) => /FROM netshop_rows r(?:\s|$)/i.test(sql));
  assert.equal(factReads.length, 1, `unexpected SPU fact query count: ${factReads.length}`);
  const projectionSql = factReads[0]!;
  assert.equal(
    (projectionSql.match(/FROM netshop_rows r INDEXED BY netshop_rows_source_dataset_scope_date_idx/gi) ?? []).length,
    2,
    "the bounded-period metrics and full-history options each scan the composite scope index once",
  );
  assert.match(projectionSql, /filtered AS MATERIALIZED/i);
  assert.match(projectionSql, /available_facts AS MATERIALIZED/i);
  const plan = sqlite.prepare(`EXPLAIN QUERY PLAN ${projectionSql}`).all(
    "spu_daily",
    "2026-08-01",
    "2026-08-02",
    "spu_daily",
    50,
    0,
    NETSHOP_DAILY_SERIES_LIMIT,
  ) as Array<{ detail: string }>;
  assert.ok(plan.some((row) => /MATERIALIZE filtered/i.test(row.detail)), JSON.stringify(plan));
  assert.ok(plan.some((row) => /MATERIALIZE available_facts/i.test(row.detail)), JSON.stringify(plan));
  assert.equal(
    plan.filter((row) => /netshop_rows_source_dataset_scope_date_idx/i.test(row.detail)).length,
    2,
    JSON.stringify(plan),
  );
  sqlite.close();
});

test("omitted product and promotion periods return only the latest bounded daily series with explicit truncation", async () => {
  const sqlite = createDatabase();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
  const start = Date.UTC(2023, 0, 1);
  sqlite.exec("BEGIN");
  for (let index = 0; index < NETSHOP_DAILY_SERIES_LIMIT + 70; index += 1) {
    const date = new Date(start + index * 86_400_000).toISOString().slice(0, 10);
    insert.run(
      index * 2 + 1,
      "jd_sku_daily",
      "sku_daily",
      "京东",
      "长期店铺",
      date,
      "SKU-LONG",
      "长期商品",
      "SKU-LONG",
      "SPU-LONG",
      JSON.stringify({ visitors: 1, transactionAmountCents: 100, transactionCustomers: 1 }),
      "{}",
    );
    insert.run(
      index * 2 + 2,
      "jd_promotion",
      "ad",
      "京东",
      "长期店铺",
      date,
      "SKU-LONG",
      "长期商品",
      "SKU-LONG",
      "SPU-LONG",
      JSON.stringify({ spendCents: 10, netTransactionAmountCents: 100, impressions: 10, clicks: 1, netOrders: 1 }),
      "{}",
    );
  }
  sqlite.exec("COMMIT");

  const productReads: string[] = [];
  const product = await getNetshopProductPerformance(adapter(sqlite, productReads) as never, {
    dimension: "sku",
    page: 1,
    pageSize: 1,
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "长期店铺" }],
  });
  assert.deepEqual(product.dailyPagination, {
    total: NETSHOP_DAILY_SERIES_LIMIT + 70,
    returned: NETSHOP_DAILY_SERIES_LIMIT,
    truncated: true,
  });
  assert.equal(product.coverage.actualDates.length, NETSHOP_DAILY_SERIES_LIMIT);
  assert.equal(product.coverage.truncated, true);

  const promotionReads: string[] = [];
  const promotion = await getNetshopPromotionPerformance(adapter(sqlite, promotionReads) as never, {
    page: 1,
    pageSize: 1,
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "长期店铺" }],
  });
  assert.deepEqual(promotion.dailyPagination, {
    total: NETSHOP_DAILY_SERIES_LIMIT + 70,
    returned: NETSHOP_DAILY_SERIES_LIMIT,
    truncated: true,
  });
  assert.equal(promotion.coverage.promotionDatesPagination.truncated, true);
  assert.equal(promotion.coverage.productDailyDatesPagination.truncated, true);
  assert.equal(promotion.coverage.intersectionTruncated, true);
  assert.equal(promotion.items[0]?.dates.length, 0);
  assert.equal(promotion.items[0]?.datesTruncated, true);
  assert.ok([...productReads, ...promotionReads].filter((sql) => /daily_series/i.test(sql)).every((sql) => /LIMIT \?/i.test(sql)));
  sqlite.close();
});

test("maximum legal netshop filters stay below D1's 100-bind ceiling", async () => {
  const sqlite = createDatabase();
  const reads: string[] = [];
  const bindCounts: number[] = [];
  await getNetshopProductPerformance(adapter(sqlite, reads, bindCounts) as never, {
    dimension: "sku",
    query: "missing-product",
    page: 10_000,
    pageSize: 100,
    startDate: "2026-01-01",
    endDate: "2026-12-31",
    platformNames: Array.from({ length: 20 }, (_, index) => `platform-${index}`),
    outlets: Array.from({ length: 50 }, (_, index) => ({ platform: `platform-${index % 20}`, shopName: `shop-${index}` })),
  });
  assert.ok(bindCounts.length > 0);
  assert.ok(Math.max(...bindCounts) <= 100, `maximum bind count was ${Math.max(...bindCounts)}`);
  sqlite.close();
});

test("product performance projections keep summary and page reads bounded and fence exact-shop versions", async () => {
  const sqlite = createDatabase();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
  insert.run(1, "jd_sku_daily", "sku_daily", "京东", "A店", "2026-08-01", "SKU-A", "A商品", "SKU-A", "SPU-A",
    JSON.stringify({ visitors: 7, transactionAmountCents: 700, transactionCustomers: 2 }), "{}");
  insert.run(2, "jd_sku_daily", "sku_daily", "京东", "B店", "2026-08-01", "SKU-B", "B商品", "SKU-B", "SPU-B",
    JSON.stringify({ visitors: 9, transactionAmountCents: 900, transactionCustomers: 3 }), "{}");
  insert.run(3, "jd_sku_daily", "sku_daily", "京东", "A店", "2026-07-30", "SKU-A", "A商品", "SKU-A", "SPU-A",
    JSON.stringify({ visitors: 99, transactionAmountCents: 9_900, transactionCustomers: 9 }), "{}");
  sqlite.prepare("INSERT INTO netshop_product_daily_revisions (platform,data_version) VALUES ('京东',2)").run();
  sqlite.prepare("INSERT INTO netshop_product_daily_scope_revisions (platform,shop_name,data_version) VALUES ('京东','A店',1),('京东','B店',1)").run();
  const scope = {
    dimension: "sku" as const,
    query: "SKU",
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    outlets: [{ platform: "京东", shopName: "A店" }],
    pageSize: 1,
  };

  const fullReads: string[] = [];
  const full = await getNetshopProductPerformance(adapter(sqlite, fullReads) as never, scope);
  assert.equal(full.summary.visitors, 7);
  assert.deepEqual(full.shops.map((shop) => shop.shopName), ["A店", "B店"], "full keeps platform-wide shop options");
  assert.deepEqual(full.shops.map((shop) => shop.productCount), [1, 1]);
  assert.deepEqual(full.coverage, {
    actualDates: ["2026-08-01"],
    missingDates: [],
    availableDateMin: "2026-07-30",
    availableDateMax: "2026-08-01",
    total: 1,
    returned: 1,
    truncated: false,
  });
  assert.deepEqual(full.daily, [{
    date: "2026-08-01",
    pageViews: 0,
    visitors: 7,
    transactionCustomers: 2,
    transactionQuantity: 0,
    transactionAmountCents: 700,
    refundAmountCents: 0,
    favorites: 0,
    addCartCustomers: 0,
    addCartQuantity: 0,
  }]);
  assert.equal(fullReads.filter((sql) => /WITH filtered AS MATERIALIZED/i.test(sql)).length, 1);
  assert.ok(fullReads.some((sql) => /available_facts AS MATERIALIZED/i.test(sql)));

  const summaryReads: string[] = [];
  const summary = await getNetshopProductPerformanceSummary(adapter(sqlite, summaryReads) as never, scope);
  assert.deepEqual(Object.keys(summary).sort(), [
    "dataCutoffDate", "dataset", "dateMin", "dimension", "monetaryUnit",
    "requestedPeriod", "snapshotToken", "summary", "visitorAggregation",
  ]);
  assert.equal(summary.summary.visitors, 7);
  assert.equal(summaryReads.length, 3);
  assert.equal(summaryReads.some((sql) => /daily_series|tmall_product_assets|GROUP BY r\.platform, r\.shop_name/i.test(sql)), false);

  const pageReads: string[] = [];
  const page = await getNetshopProductPerformancePage(adapter(sqlite, pageReads) as never, {
    ...scope,
    snapshotToken: full.snapshotToken,
  });
  assert.deepEqual(Object.keys(page).sort(), ["items", "pagination", "snapshotToken"]);
  assert.equal(page.items[0]?.id, "SKU-A");
  assert.equal(page.pagination.total, 1);
  assert.deepEqual(page.items, full.items, "split page and materialized full projection keep identical item semantics");
  assert.equal(pageReads.length, 3);
  assert.ok(pageReads.some((sql) => /WITH grouped_items[\s\S]*COUNT\(\*\) OVER \(\) AS total_items/i.test(sql)));
  assert.equal(pageReads.some((sql) => /daily_series|AS available_date|tmall_product_assets/i.test(sql)), false);

  const changedQueryReads: string[] = [];
  await assert.rejects(
    () => getNetshopProductPerformancePage(adapter(sqlite, changedQueryReads) as never, {
      ...scope,
      query: "SKU-A",
      snapshotToken: full.snapshotToken,
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  assert.equal(changedQueryReads.some((sql) => /WITH grouped_items/i.test(sql)), false, "mismatched query fails before fact reads");

  sqlite.prepare("UPDATE netshop_product_daily_scope_revisions SET data_version=data_version+1 WHERE platform='京东' AND shop_name='B店'").run();
  sqlite.prepare("UPDATE netshop_product_daily_revisions SET data_version=data_version+1 WHERE platform='京东'").run();
  await assert.doesNotReject(() => getNetshopProductPerformancePage(adapter(sqlite, []) as never, {
    ...scope,
    snapshotToken: full.snapshotToken,
  }));

  sqlite.prepare("UPDATE netshop_product_daily_scope_revisions SET data_version=data_version+1 WHERE platform='京东' AND shop_name='A店'").run();
  sqlite.prepare("UPDATE netshop_product_daily_revisions SET data_version=data_version+1 WHERE platform='京东'").run();
  await assert.rejects(
    () => getNetshopProductPerformancePage(adapter(sqlite, []) as never, { ...scope, snapshotToken: full.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );

  const refreshed = await getNetshopProductPerformance(adapter(sqlite, []) as never, scope);
  let mutatedDuringPage = false;
  await assert.rejects(
    () => getNetshopProductPerformancePage(adapter(sqlite, [], [], (sql) => {
      if (!mutatedDuringPage && /WITH grouped_items/i.test(sql)) {
        mutatedDuringPage = true;
        sqlite.prepare("UPDATE netshop_product_daily_scope_revisions SET data_version=data_version+1 WHERE platform='京东' AND shop_name='A店'").run();
        sqlite.prepare("UPDATE netshop_product_daily_revisions SET data_version=data_version+1 WHERE platform='京东'").run();
      }
    }) as never, { ...scope, snapshotToken: refreshed.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503 && /读取期间.*已更新/.test(error.message),
  );
  assert.equal(mutatedDuringPage, true);
  sqlite.close();
});

test("product performance pagination totally orders cross-platform duplicate SPU ids and same-name shops", async () => {
  const sqlite = createDatabase();
  const insert = sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
  const tiedMetrics = JSON.stringify({
    visitors: 5,
    transactionAmountCents: 500,
    transactionCustomers: 1,
  });
  const identities = [
    { id: 1, platform: "天猫", shopName: "B店", source: "tmall_product_daily" },
    { id: 2, platform: "京东", shopName: "B店", source: "jd_sku_daily" },
    { id: 3, platform: "天猫", shopName: "A店", source: "tmall_product_daily" },
    { id: 4, platform: "京东", shopName: "A店", source: "jd_sku_daily" },
  ] as const;
  for (const identity of identities) {
    insert.run(
      identity.id,
      identity.source,
      "spu_daily",
      identity.platform,
      identity.shopName,
      "2026-08-01",
      "SPU-TIE",
      "完全并列商品",
      "",
      "SPU-TIE",
      tiedMetrics,
      "{}",
    );
  }
  sqlite.prepare(
    "INSERT INTO netshop_product_daily_revisions (platform,data_version) VALUES ('京东',1),('天猫',1)",
  ).run();
  sqlite.prepare(`INSERT INTO netshop_product_daily_scope_revisions (platform,shop_name,data_version) VALUES
    ('京东','A店',1),('京东','B店',1),('天猫','A店',1),('天猫','B店',1)`).run();

  const scope = {
    dimension: "spu" as const,
    startDate: "2026-08-01",
    endDate: "2026-08-01",
    pageSize: 1,
  };
  const expectedItems = [
    "京东/A店/SPU-TIE",
    "京东/B店/SPU-TIE",
    "天猫/A店/SPU-TIE",
    "天猫/B店/SPU-TIE",
  ];
  const fullReads: string[] = [];
  const full = await getNetshopProductPerformance(adapter(sqlite, fullReads) as never, scope);
  assert.deepEqual(
    full.items.map((item) => `${item.platform}/${item.shopNames[0]}/${item.id}`),
    expectedItems.slice(0, 1),
  );
  assert.deepEqual(
    full.shops.map((shop) => `${shop.shopName}/${shop.platform}`),
    ["A店/京东", "A店/天猫", "B店/京东", "B店/天猫"],
  );
  const fullProjectionSql = fullReads.find((sql) => /WITH filtered AS MATERIALIZED/i.test(sql)) ?? "";
  assert.match(
    fullProjectionSql,
    /ORDER BY transaction_amount DESC, visitors DESC, product_name COLLATE NOCASE ASC,\s*id ASC, platform ASC, shop_name COLLATE NOCASE ASC, shop_name ASC/,
  );
  assert.match(
    fullProjectionSql,
    /SELECT \* FROM shop_rows\s+ORDER BY shop_name COLLATE NOCASE ASC, shop_name ASC, platform ASC/,
  );

  const pagedItems: string[] = [];
  for (let page = 1; page <= expectedItems.length; page += 1) {
    const pageReads: string[] = [];
    const result = await getNetshopProductPerformancePage(adapter(sqlite, pageReads) as never, {
      ...scope,
      page,
      snapshotToken: full.snapshotToken,
    });
    assert.deepEqual(result.pagination, {
      page,
      pageSize: 1,
      total: expectedItems.length,
      returned: 1,
      truncated: page < expectedItems.length,
    });
    pagedItems.push(...result.items.map((item) => `${item.platform}/${item.shopNames[0]}/${item.id}`));
    const pageSql = pageReads.find((sql) => /WITH grouped_items AS/i.test(sql)) ?? "";
    assert.match(
      pageSql,
      /ORDER BY transaction_amount DESC, visitors DESC, product_name COLLATE NOCASE ASC,\s*id ASC, platform ASC, shop_name COLLATE NOCASE ASC, shop_name ASC/,
    );
  }
  assert.deepEqual(pagedItems, expectedItems);
  sqlite.close();
});

test("product performance full projection rejects malformed JSON rows before returning a partial payload", async () => {
  const sqlite = createDatabase();
  sqlite.prepare(`INSERT INTO netshop_rows
    (id,source,dataset,platform,shop_name,business_date,product_code,product_name,sku_id,spu_id,metrics_json,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    1,
    "jd_sku_daily",
    "sku_daily",
    "京东",
    "安全投影店",
    "2026-08-01",
    "SKU-SAFE",
    "安全投影商品",
    "SKU-SAFE",
    "SPU-SAFE",
    JSON.stringify({ visitors: 1, transactionAmountCents: 100 }),
    "{}",
  );
  await assert.rejects(
    () => getNetshopProductPerformance(adapter(sqlite, [], [], undefined, (sql, value) => {
      if (!/WITH filtered AS MATERIALIZED/i.test(sql) || !value || typeof value !== "object") return value;
      return { ...(value as Record<string, unknown>), items_json: '[{"id":"incomplete"}]' };
    }) as never, {
      dimension: "sku",
      startDate: "2026-08-01",
      endDate: "2026-08-01",
    }),
    (error: unknown) => error instanceof PublicApiError
      && error.status === 503
      && error.code === "service_unavailable"
      && /商品明细投影结构无效/.test(error.message),
  );
  sqlite.close();
});

test("published JD SPU imports atomically invalidate old performance tokens, including closing-fence races", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const importDb = transactionalAdapter(sqlite);
  await ensureNetshopSchema(importDb as never);
  const saveSpuVersion = (version: number) => saveNetshopImport(importDb as never, {
    source: "jd_sku_daily",
    dataset: "spu_daily",
    platform: "京东",
    shopName: "SPU版本店",
    fileHash: String(version).repeat(64),
    fileName: `spu-v${version}.xlsx`,
    fileSizeBytes: version,
    sheetName: "SPU",
    rows: [{
      sourceRowNumber: 1,
      sourceRowKey: JSON.stringify(["spu_daily", "京东", "SPU版本店", "2026-08-01", "SPU-1"]),
      sourceRowHash: String(version).repeat(64),
      source: "jd_sku_daily" as const,
      dataset: "spu_daily",
      platform: "京东",
      shopName: "SPU版本店",
      businessDate: "2026-08-01",
      snapshotDate: "",
      productCode: "SPU-1",
      productName: `SPU商品-v${version}`,
      skuId: "",
      spuId: "SPU-1",
      warehouseType: "",
      metrics: { visitors: version, transactionAmountCents: version * 100 },
      raw: {},
    }],
    warnings: [],
    totals: {},
    note: "",
    replaceScope: { startDate: "2026-08-01", endDate: "2026-08-01" },
  });
  const scope = {
    dimension: "spu" as const,
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "SPU版本店" }],
    startDate: "2026-08-01",
    endDate: "2026-08-01",
  };

  await saveSpuVersion(1);
  const first = await getNetshopProductPerformance(importDb as never, scope);
  await saveSpuVersion(2);
  assert.deepEqual({ ...sqlite.prepare(`SELECT
      (SELECT data_version FROM netshop_product_daily_revisions WHERE platform='京东') platformVersion,
      (SELECT data_version FROM netshop_product_daily_scope_revisions WHERE platform='京东' AND shop_name='SPU版本店') shopVersion`).get()! }, {
    platformVersion: 2,
    shopVersion: 2,
  });
  await assert.rejects(
    () => getNetshopProductPerformancePage(importDb as never, { ...scope, snapshotToken: first.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
    "a completed JD spu_daily publish must invalidate the old token",
  );

  const second = await getNetshopProductPerformance(importDb as never, scope);
  let publishedDuringPage = false;
  await assert.rejects(
    () => getNetshopProductPerformancePage(adapter(sqlite, [], [], async (sql) => {
      if (!publishedDuringPage && /WITH grouped_items/i.test(sql)) {
        publishedDuringPage = true;
        await saveSpuVersion(3);
      }
    }) as never, { ...scope, snapshotToken: second.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503 && /读取期间.*已更新/.test(error.message),
  );
  assert.equal(publishedDuringPage, true);
  assert.deepEqual({ ...sqlite.prepare(`SELECT
      (SELECT data_version FROM netshop_product_daily_revisions WHERE platform='京东') platformVersion,
      (SELECT data_version FROM netshop_product_daily_scope_revisions WHERE platform='京东' AND shop_name='SPU版本店') shopVersion`).get()! }, {
    platformVersion: 3,
    shopVersion: 3,
  });
  sqlite.close();
});

test("a 100-item product page reads sales through the bounded Django consumer contract", async () => {
  const sqlite = createProductCatalogDatabase();
  const reads: string[] = [];
  const bindCounts: number[] = [];
  const result = await getNetshopProductCatalog(adapter(sqlite, reads, bindCounts) as never, {
    query: "SKU-PAGE-",
    page: 1,
    pageSize: 100,
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "目录店铺" }],
    salesChannels: ["京东", ...Array.from({ length: 49 }, (_, index) => `无关渠道-${index}`)],
    salesStartDate: "2026-08-01",
    salesEndDate: "2026-08-01",
  });
  assert.equal(result.items.length, 100);
  assert.equal(result.items.every((item) => item.salesMatched), true);
  assert.ok(Math.max(...bindCounts) <= 100, `maximum bind count was ${Math.max(...bindCounts)}`);
  assert.ok(reads.every((sql) => !/sales_order_lines/i.test(sql)));
  sqlite.close();
});

test("product catalog page uses authoritative batch totals, the batch-prefix index, and scoped fences", async () => {
  const sqlite = createProductCatalogDatabase();
  const scope = {
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "目录店铺" }],
  };
  const full = await getNetshopProductCatalog(adapter(sqlite, []) as never, { ...scope, pageSize: 20 });
  assert.deepEqual(Object.keys(full).sort(), [
    "batch", "items", "pagination", "sales", "shops", "snapshotToken", "summary",
  ]);
  assert.equal(full.summary.totalSkus, 102);

  const pageReads: string[] = [];
  const page = await getNetshopProductCatalogPage(adapter(sqlite, pageReads) as never, {
    ...scope,
    pageSize: 20,
    snapshotToken: full.snapshotToken,
  });
  assert.deepEqual(Object.keys(page).sort(), ["items", "pagination", "snapshotToken"]);
  assert.equal(page.pagination.total, 102);
  assert.equal(
    Number((sqlite.prepare("SELECT COUNT(*) AS total FROM netshop_rows WHERE last_import_batch_id='master-batch'").get() as { total: number }).total),
    page.pagination.total,
    "completed product-master batch.row_count must equal its published fact count",
  );
  assert.equal(pageReads.some((sql) => /COUNT\(\*\) OVER/i.test(sql)), false);
  assert.equal(pageReads.some((sql) => /SELECT COUNT\(\*\) AS total\s+FROM netshop_rows product/i.test(sql)), false);
  const pageSql = pageReads.find((sql) => /FROM json_each\(\?\) requested_batch CROSS JOIN netshop_rows product[\s\S]*LIMIT \? OFFSET \?/i.test(sql));
  assert.ok(pageSql);
  assert.match(pageSql, /product\.last_import_batch_id = CAST\(requested_batch\.value AS TEXT\)/);
  assert.match(pageSql, /product\.source IN \('jd_product_master', 'tmall_product_master'\)[\s\S]*product\.dataset = 'product_master'/);
  assert.match(
    pageSql,
    /ORDER BY product\.shop_name ASC, product\.product_name ASC, product\.sku_id ASC,\s*product\.platform ASC, product\.id ASC/,
  );
  assert.deepEqual(
    (sqlite.prepare("PRAGMA index_info('netshop_rows_product_batch_page_idx')").all() as Array<{ name: string }>).map((row) => row.name),
    ["last_import_batch_id", "shop_name", "product_name", "sku_id", "platform", "id"],
  );

  const plan = sqlite.prepare(`EXPLAIN QUERY PLAN
    SELECT product.sku_id
    FROM json_each(?) requested_batch
    CROSS JOIN netshop_rows product
    WHERE product.source IN ('jd_product_master','tmall_product_master')
      AND product.dataset='product_master'
      AND product.last_import_batch_id = CAST(requested_batch.value AS TEXT)
    ORDER BY product.shop_name,product.product_name,product.sku_id,product.platform,product.id
    LIMIT ? OFFSET ?`).all(JSON.stringify(["master-batch"]), 20, 0) as Array<{ detail: string }>;
  assert.ok(plan.some((row) => /netshop_rows_product_batch_page_idx/i.test(row.detail)), JSON.stringify(plan));

  const searchedReads: string[] = [];
  const searched = await getNetshopProductCatalogPage(adapter(sqlite, searchedReads) as never, {
    ...scope,
    query: "PAIR-JD-SAME",
    pageSize: 10,
    snapshotToken: full.snapshotToken,
  });
  assert.equal(searched.pagination.total, 1, "catalog token is reusable across q and pageSize");
  assert.ok(searchedReads.some((sql) => /SELECT COUNT\(\*\) AS total\s+FROM json_each\(\?\) requested_batch CROSS JOIN netshop_rows product/i.test(sql)));

  const batch = sqlite.prepare(`INSERT INTO netshop_import_batches VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const product = sqlite.prepare(`INSERT INTO netshop_rows VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  batch.run(
    "jd-other-new", "jd_product_master", "product_master", "京东", "另一店铺", "other-new.xlsx", 1,
    "other-new-hash", "sheet", "completed", 1, 1, 0, 0, null, null, "2026-08-02", "[]", "{}", "",
    "2026-08-10 08:00:00", "2026-08-10 08:01:00",
  );
  product.run(
    600, "other-new-row", "other-new-row-hash", "jd-other-new", "jd-other-new", 1,
    "jd_product_master", "product_master", "京东", "另一店铺", null, "2026-08-02",
    "OTHER-NEW", "另一店新商品", "OTHER-NEW", "SPU-OTHER-NEW", "", "{}", "{}",
    "2026-08-10 08:00:00", "2026-08-10 08:00:00",
  );
  await assert.doesNotReject(() => getNetshopProductCatalogPage(adapter(sqlite, []) as never, {
    ...scope,
    snapshotToken: full.snapshotToken,
  }), "another shop's product head must not invalidate an exact-shop page token");

  batch.run(
    "jd-image-blank", "jd_yimei_sku", "sku_image", "京东", "", "blank-image.xlsx", 1,
    "blank-image-hash", "sheet", "completed", 1, 1, 0, 0, null, null, "2026-08-03", "[]", "{}", "",
    "2026-08-11 08:00:00", "2026-08-11 08:01:00",
  );
  product.run(
    601, "blank-image-row", "blank-image-row-hash", "jd-image-blank", "jd-image-blank", 1,
    "jd_yimei_sku", "sku_image", "京东", "", null, "2026-08-03",
    "SKU-PAGE-000", "共享图片", "SKU-PAGE-000", "", "", "{}", JSON.stringify({ 主图链接: "https://example.test/new.jpg" }),
    "2026-08-11 08:00:00", "2026-08-11 08:00:00",
  );
  await assert.rejects(
    () => getNetshopProductCatalogPage(adapter(sqlite, []) as never, { ...scope, snapshotToken: full.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
    "blank-shop JD image heads participate in the page fence",
  );

  const refreshed = await getNetshopProductCatalog(adapter(sqlite, []) as never, scope);
  batch.run(
    "master-new", "jd_product_master", "product_master", "京东", "目录店铺", "master-new.xlsx", 1,
    "master-new-hash", "sheet", "completed", 1, 1, 0, 0, null, null, "2026-08-04", "[]", "{}", "",
    "2026-08-12 08:00:00", "2026-08-12 08:01:00",
  );
  product.run(
    602, "master-new-row", "master-new-row-hash", "master-new", "master-new", 1,
    "jd_product_master", "product_master", "京东", "目录店铺", null, "2026-08-04",
    "MASTER-NEW", "目录店新商品", "MASTER-NEW", "SPU-MASTER-NEW", "", "{}", "{}",
    "2026-08-12 08:00:00", "2026-08-12 08:00:00",
  );
  await assert.rejects(
    () => getNetshopProductCatalogPage(adapter(sqlite, []) as never, { ...scope, snapshotToken: refreshed.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  sqlite.close();

  const mismatchDb = createProductCatalogDatabase();
  mismatchDb.prepare("UPDATE netshop_import_batches SET row_count=101 WHERE id='master-batch'").run();
  await assert.rejects(
    () => getNetshopProductCatalog(adapter(mismatchDb, []) as never, scope),
    (error: unknown) => error instanceof PublicApiError
      && error.status === 503
      && /批次元数据与已发布事实不一致/.test(error.message),
    "full must fail closed before issuing a token for legacy row_count drift",
  );
  mismatchDb.close();

  const raceDb = createProductCatalogDatabase();
  const raceFull = await getNetshopProductCatalog(adapter(raceDb, []) as never, scope);
  let raced = false;
  await assert.rejects(
    () => getNetshopProductCatalogPage(adapter(raceDb, [], [], (sql) => {
      if (!raced && /FROM json_each\(\?\) requested_batch CROSS JOIN netshop_rows product[\s\S]*LIMIT \? OFFSET \?/i.test(sql)) {
        raced = true;
        raceDb.prepare("UPDATE sales_overview_cache_state SET sales_revision=sales_revision+1 WHERE id=1").run();
      }
    }) as never, { ...scope, snapshotToken: raceFull.snapshotToken }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503 && /读取期间.*已更新/.test(error.message),
  );
  assert.equal(raced, true);
  raceDb.close();
});

test("product catalog OFFSET pages remain complete for cross-platform rows with identical visible sort keys", async () => {
  const sqlite = createProductCatalogDatabase();
  const product = sqlite.prepare(`INSERT INTO netshop_rows VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const tiedRows = [
    { id: 704, batchId: "tm-same-batch", source: "tmall_product_master", platform: "天猫", code: "TIE-TM-2" },
    { id: 702, batchId: "master-batch", source: "jd_product_master", platform: "京东", code: "TIE-JD-2" },
    { id: 703, batchId: "tm-same-batch", source: "tmall_product_master", platform: "天猫", code: "TIE-TM-1" },
    { id: 701, batchId: "master-batch", source: "jd_product_master", platform: "京东", code: "TIE-JD-1" },
  ] as const;
  for (const row of tiedRows) {
    product.run(
      row.id, `row-${row.id}`, `hash-${row.id}`, row.batchId, row.batchId, row.id,
      row.source, "product_master", row.platform, "目录店铺", null, "2026-08-01",
      row.code, "完全并列目录商品", "TIE-SKU", "TIE-SPU", "", "{}",
      JSON.stringify({ "商家SKU": "TIE-SKU", "商品状态": "上架" }),
      "2026-08-01 08:00:00", "2026-08-01 08:00:00",
    );
  }
  sqlite.prepare("UPDATE netshop_import_batches SET row_count=row_count+2, inserted_count=inserted_count+2 WHERE id IN ('master-batch','tm-same-batch')").run();

  const query = "完全并列目录商品";
  const full = await getNetshopProductCatalog(adapter(sqlite, []) as never, { query, pageSize: 1 });
  const expected = [
    "京东/TIE-JD-1",
    "京东/TIE-JD-2",
    "天猫/TIE-TM-1",
    "天猫/TIE-TM-2",
  ];
  assert.equal(full.pagination.total, expected.length);
  assert.deepEqual(full.items.map((item) => `${item.platform}/${item.productCode}`), expected.slice(0, 1));

  const paged: string[] = [];
  for (let page = 1; page <= expected.length; page += 1) {
    const result = await getNetshopProductCatalogPage(adapter(sqlite, []) as never, {
      query,
      page,
      pageSize: 1,
      snapshotToken: full.snapshotToken,
    });
    assert.deepEqual(result.pagination, {
      page,
      pageSize: 1,
      total: expected.length,
      returned: 1,
      truncated: page < expected.length,
    });
    paged.push(...result.items.map((item) => `${item.platform}/${item.productCode}`));
  }
  assert.deepEqual(paged, expected);
  assert.equal(new Set(paged).size, expected.length, "adjacent OFFSET pages must neither repeat nor omit tied rows");
  sqlite.close();
});

test("product catalog applies selected outlets as exact platform and shop pairs", async () => {
  const sqlite = createProductCatalogDatabase();
  const sameNameOnly = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "PAIR-",
    pageSize: 100,
    outlets: [{ platform: "京东", shopName: "目录店铺" }],
  });
  assert.deepEqual(sameNameOnly.items.map((item) => `${item.platform}/${item.productCode}`), ["京东/PAIR-JD-SAME"]);

  const exactPairs = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "PAIR-",
    pageSize: 100,
    outlets: [
      { platform: "京东", shopName: "目录店铺" },
      { platform: "天猫", shopName: "另一店铺" },
    ],
  });
  assert.equal(exactPairs.pagination.total, 2);
  assert.deepEqual(
    new Set(exactPairs.items.map((item) => `${item.platform}/${item.productCode}`)),
    new Set(["京东/PAIR-JD-SAME", "天猫/PAIR-TM-OTHER"]),
  );
  sqlite.close();
});

test("product catalog image enrichment keeps platform and resolved shop in the image identity", async () => {
  const sqlite = createProductCatalogDatabase();
  const batch = sqlite.prepare(`INSERT INTO netshop_import_batches VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  for (const input of [
    { id: "image-cross-platform", platform: "京东", shopName: "目录店铺", completedAt: "2026-08-04 08:00:00" },
    { id: "image-correct", platform: "京东", shopName: "目录店铺", completedAt: "2026-08-02 08:00:00" },
    { id: "image-other-shop", platform: "京东", shopName: "另一店铺", completedAt: "2026-08-05 08:00:00" },
  ]) {
    batch.run(
      input.id, "jd_yimei_sku", "yimei_sku", input.platform, input.shopName, `${input.id}.xlsx`, 1,
      `${input.id}-hash`, "sheet", "completed", 1, 1, 0, 0, null, null, "2026-08-01", "[]", "{}", "",
      input.completedAt, input.completedAt,
    );
  }
  for (const input of [
    { id: "tmall-asset-correct", shopName: "目录店铺", completedAt: "2026-08-03 08:00:00" },
    { id: "tmall-asset-other-shop", shopName: "另一店铺", completedAt: "2026-08-06 08:00:00" },
  ]) {
    batch.run(
      input.id, "tmall_product_assets", "spu_assets", "天猫", input.shopName, `${input.id}.xlsx`, 1,
      `${input.id}-hash`, "商品图", "completed", 1, 1, 0, 0, null, null, "2026-08-03", "[]", "{}", "",
      input.completedAt, input.completedAt,
    );
  }
  const image = sqlite.prepare(`INSERT INTO netshop_rows VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  image.run(
    107, "image-cross-platform", "hash-107", "image-cross-platform", "image-cross-platform", 1,
    "jd_yimei_sku", "yimei_sku", "京东", "目录店铺", null, "2026-08-01",
    "PAIR-TM-SAME", "京东同编码商品", "PAIR-TM-SAME", "", "", "{}",
    JSON.stringify({ 主图链接: "https://example.test/cross-platform.jpg" }),
    "2026-08-04 08:00:00", "2026-08-04 08:00:00",
  );
  image.run(
    108, "image-correct", "hash-108", "image-correct", "image-correct", 1,
    "jd_yimei_sku", "yimei_sku", "京东", "目录店铺", null, "2026-08-01",
    "PAIR-JD-SAME", "京东正确商品", "PAIR-JD-SAME", "", "", "{}",
    JSON.stringify({ 主图链接: "https://example.test/correct.jpg" }),
    "2026-08-02 08:00:00", "2026-08-02 08:00:00",
  );
  image.run(
    109, "image-other-shop", "hash-109", "image-other-shop", "image-other-shop", 1,
    "jd_yimei_sku", "yimei_sku", "京东", "", null, "2026-08-01",
    "PAIR-JD-SAME", "另一店同编码商品", "PAIR-JD-SAME", "", "", "{}",
    JSON.stringify({ 主图链接: "https://example.test/other-shop.jpg" }),
    "2026-08-05 08:00:00", "2026-08-05 08:00:00",
  );
  const productImageHash = "1".repeat(64);
  image.run(
    110, "tmall-asset-correct", "hash-110", "tmall-asset-correct", "tmall-asset-correct", 2,
    "tmall_product_assets", "spu_assets", "天猫", "目录店铺", null, "2026-08-03",
    "", "天猫正确商品图", "", "SPU-PAIR-TM-SAME", "", "{}",
    JSON.stringify({
      商品ID: "SPU-PAIR-TM-SAME",
      商品链接: "https://item.taobao.com/item.htm?id=812345678901",
      图片内容SHA256: productImageHash,
      图片对象键: `netshop-product-images/v1/${productImageHash}.jpg`,
      图片MIME: "image/jpeg",
      图片字节数: 7,
    }),
    "2026-08-03 08:00:00", "2026-08-03 08:00:00",
  );
  image.run(
    111, "tmall-asset-other-shop", "hash-111", "tmall-asset-other-shop", "tmall-asset-other-shop", 2,
    "tmall_product_assets", "spu_assets", "天猫", "另一店铺", null, "2026-08-03",
    "", "跨店错误商品图", "", "SPU-PAIR-TM-SAME", "", "{}",
    JSON.stringify({ 商品链接: "https://example.test/wrong-shop", 图片内容SHA256: "2".repeat(64), 图片对象键: `netshop-product-images/v1/${"2".repeat(64)}.jpg`, 图片MIME: "image/jpeg", 图片字节数: 7 }),
    "2026-08-06 08:00:00", "2026-08-06 08:00:00",
  );

  const tmall = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "PAIR-TM-SAME",
    pageSize: 10,
    outlets: [{ platform: "天猫", shopName: "目录店铺" }],
  });
  assert.equal(tmall.items[0]?.imageUrl, `/api/netshop/product-images/${productImageHash}`);
  assert.equal(tmall.items[0]?.productUrl, "https://item.taobao.com/item.htm?id=812345678901");

  batch.run(
    "tmall-asset-empty-latest", "tmall_product_assets", "spu_assets", "天猫", "目录店铺", "empty-latest.xlsx", 1,
    "tmall-asset-empty-latest-hash", "商品图", "completed", 1, 1, 0, 0, null, null, "2026-08-04", "[]", "{}", "",
    "2026-08-07 08:00:00", "2026-08-07 08:01:00",
  );
  image.run(
    112, "tmall-asset-empty-latest", "hash-112", "tmall-asset-empty-latest", "tmall-asset-empty-latest", 2,
    "tmall_product_assets", "spu_assets", "天猫", "目录店铺", null, "2026-08-04",
    "", "最新快照中的其他商品", "", "SPU-OTHER-LATEST", "", "{}", "{}",
    "2026-08-07 08:00:00", "2026-08-07 08:01:00",
  );
  const tmallWithoutHistoricalFallback = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "PAIR-TM-SAME",
    pageSize: 10,
    outlets: [{ platform: "天猫", shopName: "目录店铺" }],
  });
  assert.equal(tmallWithoutHistoricalFallback.items[0]?.imageUrl, "");
  assert.equal(tmallWithoutHistoricalFallback.items[0]?.productUrl, "https://detail.tmall.com/item.htm?id=SPU-PAIR-TM-SAME");

  const jd = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "PAIR-JD-SAME",
    pageSize: 10,
    outlets: [{ platform: "京东", shopName: "目录店铺" }],
  });
  assert.equal(jd.items[0]?.imageUrl, "https://example.test/correct.jpg");
  sqlite.close();
});

test("product catalog sales metrics use platform, shop, and SKU as one identity", async () => {
  const sqlite = createProductCatalogDatabase();
  const selected = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "CROSS-SHOP-",
    pageSize: 100,
    platformNames: ["京东"],
    outlets: [{ platform: "京东", shopName: "目录店铺" }],
    salesChannels: ["京东"],
    salesStartDate: "2026-08-01",
    salesEndDate: "2026-08-03",
  });
  assert.equal(selected.items.length, 1);
  assert.equal(selected.items[0]?.netSalesCents, 100);
  assert.equal(selected.items[0]?.costPriceCents, 80);
  assert.equal(selected.sales.dataCutoffDate, "2026-08-01");

  const allShops = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
    query: "CROSS-SHOP-",
    pageSize: 100,
    platformNames: ["京东"],
    salesChannels: ["京东"],
    salesStartDate: "2026-08-01",
    salesEndDate: "2026-08-03",
  });
  assert.deepEqual(
    new Map(allShops.items.map((item) => [item.shopName, item.netSalesCents])),
    new Map([["目录店铺", 100], ["另一店铺", 900]]),
  );
  assert.equal(allShops.sales.dataCutoffDate, "2026-08-02");

  for (const salesChannels of [[], ["天猫"]]) {
    const restricted = await getNetshopProductCatalog(adapter(sqlite, []) as never, {
      query: "CROSS-SHOP-A",
      pageSize: 100,
      outlets: [{ platform: "京东", shopName: "目录店铺" }],
      salesChannels,
      salesStartDate: "2026-08-01",
      salesEndDate: "2026-08-03",
    });
    assert.equal(restricted.items[0]?.salesMatched, false);
    assert.equal(restricted.items[0]?.netSalesCents, null);
    assert.equal(restricted.sales.dataCutoffDate, null);
  }
  sqlite.close();
});

test("product catalog bridges only controlled JD canonical shops to exact approved ERP shop/channel pairs", async () => {
  assert.deepEqual(
    [
      "志高商用设备旗舰店",
      "志高切肉机旗舰店",
      "志高商用洗碗机旗舰店",
    ].map((shopName) => resolveNetshopSalesOutletMatches("京东", shopName)[0]),
    [
      {
        platform: "京东",
        canonicalShopName: "志高商用设备旗舰店",
        rawShopName: "志高商用设备旗舰店（亿用）",
        rawChannel: "京东-志高商用设备旗舰店（亿用）",
      },
      {
        platform: "京东",
        canonicalShopName: "志高切肉机旗舰店",
        rawShopName: "志高切肉机旗舰店（志高迈德豪）",
        rawChannel: "京东-志高切肉机旗舰店（志高迈德豪）",
      },
      {
        platform: "京东",
        canonicalShopName: "志高商用洗碗机旗舰店",
        rawShopName: "志高商用洗碗机旗舰店（志高炊之王）",
        rawChannel: "京东-志高商用洗碗机旗舰店（志高炊之王）",
      },
    ],
  );

  const sqlite = createProductCatalogDatabase();
  const batch = sqlite.prepare(`INSERT INTO netshop_import_batches VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const product = sqlite.prepare(`INSERT INTO netshop_rows VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const sale = sqlite.prepare(`INSERT INTO sales_order_lines VALUES
    (?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const controlledStores = [
    {
      key: "device",
      canonicalShopName: "志高商用设备旗舰店",
      rawShopName: "志高商用设备旗舰店（亿用）",
      rawChannel: "京东-志高商用设备旗舰店（亿用）",
      amount: 110,
      date: "2026-08-01",
    },
    {
      key: "meat",
      canonicalShopName: "志高切肉机旗舰店",
      rawShopName: "志高切肉机旗舰店（志高迈德豪）",
      rawChannel: "京东-志高切肉机旗舰店（志高迈德豪）",
      amount: 220,
      date: "2026-08-02",
    },
    {
      key: "dishwasher",
      canonicalShopName: "志高商用洗碗机旗舰店",
      rawShopName: "志高商用洗碗机旗舰店（志高炊之王）",
      rawChannel: "京东-志高商用洗碗机旗舰店（志高炊之王）",
      amount: 330,
      date: "2026-08-03",
    },
  ] as const;
  for (const [index, store] of controlledStores.entries()) {
    const batchId = `controlled-${store.key}-batch`;
    batch.run(
      batchId, "jd_product_master", "product_master", "京东", store.canonicalShopName,
      `${batchId}.xlsx`, 1, `${batchId}-hash`, "sheet", "completed", 1, 1, 0, 0,
      null, null, "2026-08-01", "[]", "{}", "", "2026-08-04 08:00:00", "2026-08-04 08:01:00",
    );
    product.run(
      200 + index, `controlled-row-${index}`, `controlled-hash-${index}`, batchId, batchId, index + 1,
      "jd_product_master", "product_master", "京东", store.canonicalShopName, null, "2026-08-01",
      `ALIAS-PRODUCT-${store.key}`, `受控别名商品-${store.key}`, `ALIAS-SKU-${store.key}`,
      `ALIAS-SPU-${store.key}`, "", "{}", JSON.stringify({ "商家SKU": "SHARED-ERP-SKU", "商品状态": "上架" }),
      "2026-08-04 08:00:00", "2026-08-04 08:00:00",
    );
    sale.run(
      200 + index, `${store.date} 12:00:00`, "主仓", `ERP-${store.key}`,
      `受控销售-${store.key}`, "京东", store.rawChannel, store.rawShopName, "SHARED-ERP-SKU",
      store.amount, Math.round(store.amount / 5), 1, Math.round(store.amount * 4 / 5),
    );
  }

  // Same SKU and a later date must not cross from an unknown suffix or a
  // mismatched shop/channel pair into the device store's KPI or cutoff.
  sale.run(
    210, "2026-08-20 12:00:00", "主仓", "ERP-UNKNOWN", "未知主体", "京东",
    "京东-志高商用设备旗舰店（未知主体）", "志高商用设备旗舰店（未知主体）",
    "SHARED-ERP-SKU", 9_000, 1_800, 1, 7_200,
  );
  sale.run(
    211, "2026-08-19 12:00:00", "主仓", "ERP-CROSS", "错配渠道", "京东",
    "京东-志高切肉机旗舰店（志高迈德豪）", "志高商用设备旗舰店（亿用）",
    "SHARED-ERP-SKU", 8_000, 1_600, 1, 6_400,
  );

  const allChannels = [
    ...controlledStores.map((store) => store.rawChannel),
    "京东-志高商用设备旗舰店（未知主体）",
  ];
  const reads: string[] = [];
  const bindCounts: number[] = [];
  const allStores = await getNetshopProductCatalog(adapter(sqlite, reads, bindCounts) as never, {
    query: "ALIAS-PRODUCT-",
    pageSize: 100,
    platformNames: ["京东"],
    outlets: controlledStores.map((store) => ({ platform: "京东", shopName: store.canonicalShopName })),
    salesChannels: allChannels,
    salesStartDate: "2026-08-01",
    salesEndDate: "2026-08-20",
  });
  assert.deepEqual(
    new Map(allStores.items.map((item) => [item.shopName, item.netSalesCents])),
    new Map(controlledStores.map((store) => [store.canonicalShopName, store.amount])),
  );
  assert.equal(allStores.sales.dataCutoffDate, "2026-08-03");

  const deviceOnly = await getNetshopProductCatalog(adapter(sqlite, reads, bindCounts) as never, {
    query: "ALIAS-PRODUCT-device",
    pageSize: 100,
    outlets: [{ platform: "京东", shopName: "志高商用设备旗舰店" }],
    salesChannels: allChannels,
    salesStartDate: "2026-08-01",
    salesEndDate: "2026-08-20",
  });
  assert.equal(deviceOnly.items[0]?.netSalesCents, 110);
  assert.equal(deviceOnly.sales.dataCutoffDate, "2026-08-01");

  const wrongChannelScope = await getNetshopProductCatalog(adapter(sqlite, reads, bindCounts) as never, {
    query: "ALIAS-PRODUCT-device",
    pageSize: 100,
    outlets: [{ platform: "京东", shopName: "志高商用设备旗舰店" }],
    salesChannels: ["京东-志高切肉机旗舰店（志高迈德豪）"],
    salesStartDate: "2026-08-01",
    salesEndDate: "2026-08-20",
  });
  assert.equal(wrongChannelScope.items[0]?.salesMatched, false);
  assert.equal(wrongChannelScope.items[0]?.netSalesCents, null);
  assert.equal(wrongChannelScope.sales.dataCutoffDate, null);
  assert.ok(Math.max(...bindCounts) <= 100, `maximum bind count was ${Math.max(...bindCounts)}`);
  assert.ok(reads.every((sql) => !/sales_order_lines/i.test(sql)));
  sqlite.close();
});

test("a failed legacy-index PRAGMA probe is never treated or cached as schema-ready", async () => {
  let probes = 0;
  const failure = new Error("injected pragma failure");
  const db = {
    prepare(sql: string) {
      if (/PRAGMA index_list/i.test(sql)) {
        return { async all() { probes += 1; throw failure; } };
      }
      throw new Error(`unexpected SQL after failed probe: ${sql}`);
    },
  };
  await assert.rejects(ensureNetshopSchema(db as never), failure);
  await assert.rejects(ensureNetshopSchema(db as never), failure);
  assert.equal(probes, 2, "failed readiness promises must be evicted so the next request retries the probe");
});
