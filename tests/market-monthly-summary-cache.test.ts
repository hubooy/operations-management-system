import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import {
  ensureMarketMonthlySummaryCache,
  isMarketMonthlySummaryCacheEligible,
  type MonthlySummaryCacheDatabase,
} from "../lib/market/monthly-summary-cache";
import {
  buildMarketCachedOverviewAnalyticsSql,
  buildMarketMonthlySummaryRefreshSql,
  buildMarketOverviewAnalyticsSql,
} from "../lib/market/overview-sql";
import { getCachedMarketOverview } from "../lib/market/overview-response-cache";
import { ensureMarketSchemaCore, type MarketSchemaDatabase } from "../lib/market/schema-core";

function sqliteAdapter(sqlite: DatabaseSync): MonthlySummaryCacheDatabase {
  return {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (statement.get(...values) ?? null) as T | null; },
        async all<T>() { return { results: statement.all(...values) as T[] }; },
        async run() { const result = statement.run(...values); return { meta: { changes: Number(result.changes) } }; },
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
  } as unknown as MonthlySummaryCacheDatabase;
}

function createSourceSchema(sqlite: DatabaseSync) {
  sqlite.exec(`
    CREATE TABLE market_ranking_entries (
      id INTEGER PRIMARY KEY, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      period_start TEXT NOT NULL, period_end TEXT NOT NULL, category TEXT NOT NULL,
      scope TEXT NOT NULL, price_band_filter TEXT NOT NULL DEFAULT '', ranking_dimension TEXT NOT NULL,
      operation_mode TEXT NOT NULL, subcategory TEXT NOT NULL, rank INTEGER, sku_code TEXT NOT NULL,
      product_name TEXT NOT NULL, brand TEXT NOT NULL, price_cents INTEGER,
      page_views INTEGER NOT NULL DEFAULT 0, visitors INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE market_effective_metrics_cache (
      market_entry_id INTEGER PRIMARY KEY, effective_gmv_cents INTEGER, real_gmv_cents INTEGER,
      gmv_out_of_band INTEGER, effective_quantity INTEGER,
      effective_average_transaction_price_cents INTEGER, effective_conversion_bps INTEGER
    );
    CREATE TABLE market_price_snapshots (
      id TEXT PRIMARY KEY, category TEXT NOT NULL, scope TEXT NOT NULL, sku_code TEXT NOT NULL,
      ranking_dimension TEXT NOT NULL, month TEXT NOT NULL, confirmed_market_price_cents INTEGER,
      confirmation_status TEXT, ai_price_type TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE market_price_band_versions (
      id TEXT PRIMARY KEY, category TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
      effective_from TEXT NOT NULL
    );
    CREATE TABLE market_price_band_items (
      id TEXT PRIMARY KEY, version_id TEXT NOT NULL, label TEXT NOT NULL,
      min_cents INTEGER, max_cents INTEGER, sort_order INTEGER NOT NULL
    );
    CREATE TABLE netshop_rows (
      id INTEGER PRIMARY KEY, sku_id TEXT NOT NULL DEFAULT '', spu_id TEXT NOT NULL DEFAULT '',
      product_code TEXT NOT NULL DEFAULT '', source_row_key TEXT NOT NULL DEFAULT '',
      source TEXT NOT NULL DEFAULT 'jd_sku_daily', dataset TEXT NOT NULL DEFAULT 'sku_daily',
      platform TEXT NOT NULL DEFAULT '京东', shop_name TEXT NOT NULL DEFAULT '', business_date TEXT,
      last_import_batch_id TEXT,
      metrics_json TEXT NOT NULL DEFAULT '{}',
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE netshop_import_batches (
      id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
      platform TEXT NOT NULL DEFAULT '', shop_name TEXT NOT NULL DEFAULT '',
      completed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE sales_order_lines (
      id INTEGER PRIMARY KEY, product_code TEXT NOT NULL DEFAULT '', allocated_amount_cents INTEGER NOT NULL DEFAULT 0,
      sales_time TEXT NOT NULL DEFAULT '', ship_time TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE market_overview_response_cache (
      cache_key TEXT PRIMARY KEY, revision_key TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE market_image_cache (
      status TEXT NOT NULL, attempt_count INTEGER NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE market_import_batches (
      id TEXT NOT NULL, status TEXT NOT NULL, file_name TEXT NOT NULL,
      created_at TEXT NOT NULL, completed_at TEXT, row_count INTEGER NOT NULL,
      inserted_count INTEGER NOT NULL, updated_count INTEGER NOT NULL, warning_count INTEGER NOT NULL
    );
    CREATE TABLE market_subcategory_taxonomy (
      status TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_items (id INTEGER PRIMARY KEY);
    CREATE TABLE market_master_identities (id INTEGER PRIMARY KEY);
    INSERT INTO market_price_band_versions VALUES ('default-band','*',1,'published','2020-01-01');
    INSERT INTO market_price_band_items VALUES
      ('low','default-band','0-499',0,50000,1),('high','default-band','500+',50000,NULL,2);
  `);
}

async function applyMonthlyCacheMigration(sqlite: DatabaseSync) {
  for (const file of [
    "0048_market_monthly_summary_cache.sql",
    "0049_market_cache_invalidation_fix.sql",
    "0060_market_netshop_query_safety.sql",
  ]) {
    const migration = await readFile(new URL(`../drizzle/${file}`, import.meta.url), "utf8");
    for (const statement of migration.split("--> statement-breakpoint").map((item) => item.trim()).filter(Boolean)) sqlite.exec(statement);
  }
}

async function applyOriginalMonthlyCacheMigration(sqlite: DatabaseSync) {
  const migration = await readFile(new URL("../drizzle/0048_market_monthly_summary_cache.sql", import.meta.url), "utf8");
  for (const statement of migration.split("--> statement-breakpoint").map((item) => item.trim()).filter(Boolean)) sqlite.exec(statement);
}

test("runtime schema fast path creates monthly cache tables and indexes for an existing database", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as unknown as MarketSchemaDatabase;
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    DROP TABLE market_monthly_summary_cache;
    DROP TABLE market_monthly_summary_cache_state;
    DROP TABLE market_monthly_summary_dirty_keys;
    DROP TABLE market_monthly_summary_dirty_scopes;
    DROP TABLE market_monthly_summary_dirty_products;
  `);
  await ensureMarketSchemaCore(db);
  assert.ok(sqlite.prepare("SELECT 1 FROM market_monthly_summary_cache_state LIMIT 1").get() === undefined);
  assert.ok(sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='market_monthly_summary_month_idx'").get());
  sqlite.close();
});

function insertMarketRow(sqlite: DatabaseSync, id: number, sku: string, gmv: number) {
  sqlite.prepare(`INSERT INTO market_ranking_entries
    (id,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,rank,sku_code,product_name,brand,price_cents,page_views,visitors)
    VALUES (?, '2026-06-01','2026-06-30','家电','POP','SKU','POP','厨房',?,? ,?,'品牌甲',39900,1000,500)`)
    .run(id, id, sku, `商品${sku}`);
  sqlite.prepare(`INSERT INTO market_effective_metrics_cache
    (market_entry_id,effective_gmv_cents,real_gmv_cents,gmv_out_of_band,effective_quantity,effective_average_transaction_price_cents,effective_conversion_bps)
    VALUES (?,?,0,0,10,?,200)`).run(id, gmv, Math.round(gmv / 10));
}

test("monthly summary invalidation follows atomic netshop projection and Django sales revisions", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSourceSchema(sqlite);
  await applyMonthlyCacheMigration(sqlite);
  await ensureMarketMonthlySummaryCache(sqliteAdapter(sqlite));
  const revision = () => Number((sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision);
  const initial = revision();
  insertMarketRow(sqlite, 1, "SKU-1", 100000);
  assert.ok(revision() > initial);
  assert.equal((sqlite.prepare("SELECT month FROM market_monthly_summary_dirty_keys WHERE sku_code='SKU-1'").get() as { month: string }).month, "2026-06");
  sqlite.exec("INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month) VALUES ('p','家电','POP','SKU-1','SKU','2026-06')");
  sqlite.exec("INSERT INTO netshop_rows (id,sku_id) VALUES (1,'SKU-1')");
  const afterLegacyNetshopInsert = revision();
  sqlite.exec("UPDATE netshop_rows SET source_row_key='natural-key',updated_at='2026-07-30 12:00:00' WHERE id=1");
  assert.equal(revision(), afterLegacyNetshopInsert);
  sqlite.exec(`INSERT INTO market_netshop_projection
    (projection_revision,projection_key,kind,source,dataset,business_date,sku_id,transaction_amount_cents)
    VALUES ('1:aaaaaaaaaaaa','metric:SKU-1','metric','jd_sku_daily','sku_daily','2026-06-02','SKU-1',200)`);
  assert.equal(revision(), afterLegacyNetshopInsert, "staging must not invalidate the active market view");
  sqlite.exec(`UPDATE market_netshop_projection_control
    SET active_revision='1:aaaaaaaaaaaa',active_total=1 WHERE id=1`);
  assert.ok(revision() > afterLegacyNetshopInsert);
  const afterProjectionActivation = revision();
  sqlite.exec(`UPDATE market_netshop_projection_control
    SET active_revision=active_revision WHERE id=1`);
  assert.equal(revision(), afterProjectionActivation);
  const beforeSalesInsert = revision();
  sqlite.exec("INSERT INTO sales_order_lines (id,product_code,allocated_amount_cents,sales_time) VALUES (1,'SKU-1',100,'2026-06-01')");
  assert.equal(revision(), beforeSalesInsert);
  const responseCacheDb = sqliteAdapter(sqlite);
  const responseIdentity = { view: "ranking" as const, filters: { rankingDimensions: ["SKU"] }, salesRevision: "sales:1" };
  let responseLoads = 0;
  const loadResponse = async () => ({ load: ++responseLoads });
  const validateResponse = (value: unknown) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  assert.equal((await getCachedMarketOverview(responseCacheDb, responseIdentity, loadResponse, validateResponse)).status, "miss");
  assert.equal((await getCachedMarketOverview(responseCacheDb, responseIdentity, loadResponse, validateResponse)).status, "hit");
  sqlite.exec("UPDATE sales_order_lines SET product_code=product_code WHERE id=1");
  assert.equal(revision(), beforeSalesInsert);
  sqlite.exec("UPDATE sales_order_lines SET allocated_amount_cents=200,sales_time='2026-06-02' WHERE id=1");
  assert.equal(revision(), beforeSalesInsert);
  assert.equal((await getCachedMarketOverview(responseCacheDb, { ...responseIdentity, salesRevision: "sales:2" }, loadResponse, validateResponse)).status, "miss");
  assert.equal(responseLoads, 2);
  sqlite.exec("UPDATE sales_order_lines SET product_code='SKU-2' WHERE id=1");
  assert.equal(revision(), beforeSalesInsert);
  sqlite.exec("UPDATE market_price_band_versions SET version=2 WHERE id='default-band'");
  assert.ok(revision() >= initial + 4);
  assert.ok(sqlite.prepare("SELECT 1 FROM market_monthly_summary_dirty_scopes WHERE category='*'").get());
  sqlite.close();
});

test("runtime replaces legacy netshop-row triggers with one projection activation trigger", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSourceSchema(sqlite);
  await applyOriginalMonthlyCacheMigration(sqlite);
  const db = sqliteAdapter(sqlite);
  await ensureMarketMonthlySummaryCache(db);
  sqlite.exec("INSERT INTO netshop_rows (id,sku_id) VALUES (1,'SKU-1')");
  const before = (sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision;
  sqlite.exec("UPDATE netshop_rows SET source_row_key='natural-key',updated_at='2026-07-30 12:00:00' WHERE id=1");
  const after = (sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision;
  assert.equal(after, before);
  sqlite.exec(`UPDATE netshop_rows SET metrics_json='{"transactionAmountCents":300}' WHERE id=1`);
  const afterLegacyMetrics = (sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision;
  assert.equal(afterLegacyMetrics, after);
  sqlite.exec(`UPDATE market_netshop_projection_control
    SET active_revision='2:bbbbbbbbbbbb',active_total=0 WHERE id=1`);
  const afterActivation = (sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision;
  assert.ok(afterActivation > afterLegacyMetrics);
  sqlite.exec("INSERT INTO sales_order_lines (id,product_code,allocated_amount_cents,sales_time) VALUES (1,'SKU-1',100,'2026-06-01')");
  const beforeSalesCorrection = (sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision;
  sqlite.exec("UPDATE sales_order_lines SET allocated_amount_cents=999,sales_time='2026-06-02' WHERE id=1");
  const afterSalesCorrection = (sqlite.prepare("SELECT source_revision revision FROM market_monthly_summary_cache_state WHERE id=1").get() as { revision: number }).revision;
  assert.equal(afterSalesCorrection, beforeSalesCorrection);
  assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM sqlite_master
    WHERE type='trigger' AND name LIKE 'market_monthly_summary_sales_%'`).get()?.count, 0);
  assert.equal(sqlite.prepare(`SELECT COUNT(*) count FROM sqlite_master
    WHERE type='trigger' AND name LIKE 'market_monthly_summary_netshop_%'`).get()?.count, 1);
  sqlite.close();
});

test("monthly summary refresh is revisioned, incremental, and analytics-equivalent", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSourceSchema(sqlite);
  await applyMonthlyCacheMigration(sqlite);
  insertMarketRow(sqlite, 1, "SKU-1", 100000);
  insertMarketRow(sqlite, 2, "SKU-2", 200000);
  const db = sqliteAdapter(sqlite);
  assert.equal(await ensureMarketMonthlySummaryCache(db), true);
  const firstRows = sqlite.prepare(`SELECT sku_code sku,gmv_cents gmv,refreshed_revision revision
    FROM market_monthly_summary_cache ORDER BY sku_code`).all() as Array<{ sku: string; gmv: number; revision: number }>;
  assert.deepEqual(firstRows.map((row) => [row.sku, row.gmv]), [["SKU-1", 100000], ["SKU-2", 200000]]);

  const live = sqlite.prepare(buildMarketOverviewAnalyticsSql({ useEffectiveMetricsCache: true })).all();
  const cached = sqlite.prepare(buildMarketCachedOverviewAnalyticsSql()).all();
  assert.deepEqual(cached, live);

  sqlite.exec("UPDATE market_ranking_entries SET updated_at='2026-07-01 00:00:00' WHERE id=1");
  sqlite.exec("UPDATE market_effective_metrics_cache SET effective_gmv_cents=300000 WHERE market_entry_id=1");
  assert.equal(await ensureMarketMonthlySummaryCache(db), true);
  const secondRows = sqlite.prepare(`SELECT sku_code sku,gmv_cents gmv,refreshed_revision revision
    FROM market_monthly_summary_cache ORDER BY sku_code`).all() as Array<{ sku: string; gmv: number; revision: number }>;
  assert.equal(secondRows[0].gmv, 300000);
  assert.ok(secondRows[0].revision > firstRows[0].revision);
  assert.equal(secondRows[1].revision, firstRows[1].revision);
  const state = sqlite.prepare("SELECT source_revision source,built_revision built,status FROM market_monthly_summary_cache_state WHERE id=1").get() as { source: number; built: number; status: string };
  assert.deepEqual({ ...state }, { source: state.source, built: state.source, status: "ready" });
  sqlite.close();
});

test("monthly cache only serves whole-month date boundaries", () => {
  assert.equal(isMarketMonthlySummaryCacheEligible({}), true);
  assert.equal(isMarketMonthlySummaryCacheEligible({ startDate: "2026-06-01", endDate: "2026-06-30" }), true);
  assert.equal(isMarketMonthlySummaryCacheEligible({ startDate: "2026-06-02" }), false);
  assert.equal(isMarketMonthlySummaryCacheEligible({ endDate: "2026-06-29" }), false);
  assert.match(buildMarketMonthlySummaryRefreshSql(), /dirty_revision<=\?1/);
  assert.match(buildMarketCachedOverviewAnalyticsSql(), /source_revision=built_revision/);
});
