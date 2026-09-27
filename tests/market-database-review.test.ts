import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";

import { saveMarketImportCore, type MarketEntryForImport } from "../lib/market/import-core";
import { marketNaturalKey } from "../lib/market/import-identity";
import { ensureAnnotationSchema } from "../lib/market/annotation-schema";
import { ensureMarketSystemKpiCacheSchema } from "../lib/market/overview-response-cache";
import { acquireMarketImageCacheJobLease, createOrResumeMarketImageCacheJob, discoverMarketImageCacheJobItems } from "../lib/market/image-cache-job";
import { claimMarketImageCache, completeMarketImageCacheClaim, failMarketImageCacheClaim, propagateMarketImageCacheBatch } from "../lib/market/image-cache-state";
import { buildMarketAdminComparisonSql, buildMarketAdminItemTrendLiteSql, buildMarketCachedOverviewAnalyticsSql, buildMarketItemTrendSql, buildMarketMonthlySummaryRefreshSql, buildMarketOverviewAnalyticsSql, buildMarketRankingCtes, marketEffectiveFactsCtes, marketMonthlyCoverageCtes, marketOverviewFilterOptionsSql } from "../lib/market/overview-sql";
import { ensureMarketSchemaCore, marketImportIdentityRefreshKeysTableStatement, officialPriceBandSql, type MarketSchemaDatabase } from "../lib/market/schema-core";

function sqliteAdapter(sqlite: DatabaseSync, hooks: {
  afterRun?: (sql: string) => Promise<void>;
  beforeBatch?: () => Promise<void>;
  reportTriggerChanges?: boolean;
} = {}): MarketSchemaDatabase {
  return {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (statement.get(...values) ?? null) as T | null; },
        async all<T>() { return { results: statement.all(...values) as T[] }; },
        async run() {
          const before = hooks.reportTriggerChanges
            ? Number((sqlite.prepare("SELECT total_changes() changes").get() as { changes: number }).changes)
            : 0;
          const result = statement.run(...values);
          await hooks.afterRun?.(sql);
          const reportedChanges = hooks.reportTriggerChanges
            ? Number((sqlite.prepare("SELECT total_changes() changes").get() as { changes: number }).changes) - before
            : Number(result.changes);
          return { meta: { changes: reportedChanges } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      await hooks.beforeBatch?.();
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

test("market import accepts D1 change counts that include batch revision triggers", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite, { reportTriggerChanges: true });
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE trigger_revision (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL);
    INSERT INTO trigger_revision (id,revision) VALUES (1,0);
    CREATE TRIGGER market_batch_insert_revision AFTER INSERT ON market_import_batches
    BEGIN UPDATE trigger_revision SET revision=revision+1 WHERE id=1; END;
    CREATE TRIGGER market_batch_update_revision AFTER UPDATE ON market_import_batches
    BEGIN UPDATE trigger_revision SET revision=revision+1 WHERE id=1; END;`);

  const result = await saveMarketImportCore({
    db, batchId: "trigger-count-batch", sourceType: "jd", fileName: "trigger.csv",
    fileSizeBytes: 10, fileHash: "trigger-count-hash", sheetName: "CSV",
    rows: [entry({ category: "trigger-count-category" })], warnings: [],
  });

  assert.equal(result.status, "completed");
  assert.equal((sqlite.prepare("SELECT revision FROM trigger_revision WHERE id=1").get() as { revision: number }).revision, 3);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='trigger-count-category'").get() as { count: number }).count, 1);
  sqlite.close();
});

type OverviewAggregateTestRow = {
  section: string; row_key: string; text_1: string | null; text_2: string | null;
  number_1: number | null; number_2: number | null; number_3: number | null; number_4: number | null;
  number_5: number | null; number_6: number | null; number_7: number | null; number_8: number | null;
  number_9: number | null; number_10: number | null;
};

function readOverviewAnalytics(
  sqlite: DatabaseSync,
  options: Parameters<typeof buildMarketOverviewAnalyticsSql>[0] = {},
  baseValues: Array<string | number> = [],
  priceBandValues: Array<string | number> = [],
) {
  const bindings = [...baseValues, ...priceBandValues];
  const rows = sqlite.prepare(buildMarketOverviewAnalyticsSql(options)).all(...bindings) as OverviewAggregateTestRow[];
  const summary = rows.find((row) => row.section === "summary");
  const prices = rows.filter((row) => row.section === "price_value")
    .sort((left, right) => Number(left.number_1) - Number(right.number_1));
  const priceCount = prices.reduce((sum, row) => sum + Number(row.number_2 ?? 0), 0);
  const medianPosition = Math.floor((priceCount + 1) / 2);
  let seen = 0;
  let median: number | null = null;
  for (const row of prices) {
    seen += Number(row.number_2 ?? 0);
    if (medianPosition > 0 && seen >= medianPosition) { median = Number(row.number_1); break; }
  }
  const weightedDenominator = prices.reduce((sum, row) => sum + Number(row.number_3 ?? 0), 0);
  const weighted = weightedDenominator > 0
    ? Math.round(prices.reduce((sum, row) => sum + Number(row.number_1 ?? 0) * Number(row.number_3 ?? 0), 0) / weightedDenominator)
    : null;
  const trends = rows.filter((row) => row.section === "trend").map((row) => ({
    period: row.row_key, gmv_cents: Number(row.number_1 ?? 0), quantity: Number(row.number_2 ?? 0),
    visitors: Number(row.number_3 ?? 0), product_count: Number(row.number_4 ?? 0), brand_count: Number(row.number_5 ?? 0),
    pop_gmv_cents: Number(row.number_6 ?? 0), self_gmv_cents: Number(row.number_7 ?? 0),
    average_transaction_price_cents: Number(row.number_2 ?? 0) > 0 ? Math.round(Number(row.number_1 ?? 0) / Number(row.number_2 ?? 0)) : null,
    weighted_market_price_cents: Number(row.number_9 ?? 0) > 0 ? Math.round(Number(row.number_8 ?? 0) / Number(row.number_9 ?? 0)) : null,
  })).sort((left, right) => left.period.localeCompare(right.period));
  const bands = rows.filter((row) => row.section === "price_band").map((row) => ({
    price_band: row.row_key, row_count: Number(row.number_1 ?? 0), gmv_cents: Number(row.number_2 ?? 0),
    quantity: Number(row.number_3 ?? 0), sku_count: Number(row.number_4 ?? 0), pop_gmv_cents: Number(row.number_5 ?? 0),
    self_gmv_cents: Number(row.number_6 ?? 0), brands: row.text_1 ?? "",
  }));
  const bandOrder = (value: string) => value === "未确认价格" ? 9 : value === "3000+" ? 8 : 1;
  const brands = rows.filter((row) => row.section === "brand").map((row) => ({
    brand: row.row_key, gmv_cents: Number(row.number_1 ?? 0), quantity: Number(row.number_2 ?? 0),
    sku_count: Number(row.number_3 ?? 0), best_rank: row.number_4, price_bands: row.text_1 ?? "", subcategories: row.text_2 ?? "",
  })).sort((left, right) => right.gmv_cents - left.gmv_cents);
  return {
    summary_json: JSON.stringify({
      product_count: Number(summary?.number_1 ?? 0), category_count: Number(summary?.number_2 ?? 0),
      brand_count: Number(summary?.number_3 ?? 0), gmv_cents: Number(summary?.number_4 ?? 0),
      quantity: Number(summary?.number_5 ?? 0), page_views: Number(summary?.number_6 ?? 0), visitors: Number(summary?.number_7 ?? 0),
      own_product_count: Number(summary?.number_8 ?? 0), self_operated_gmv_cents: Number(summary?.number_9 ?? 0),
      pending_ai_count: Number(summary?.number_10 ?? 0), median_market_price_cents: median, weighted_market_price_cents: weighted,
    }),
    trend_json: JSON.stringify(trends.slice(-60)),
    trend_total: trends.length,
    price_bands_json: JSON.stringify([...bands]
      .sort((left, right) => bandOrder(left.price_band) - bandOrder(right.price_band) || left.price_band.localeCompare(right.price_band))
      .map((row) => ({ value: row.price_band, count: row.row_count }))),
    price_band_summary_json: JSON.stringify([...bands].sort((left, right) => right.gmv_cents - left.gmv_cents)),
    brand_rows_json: JSON.stringify(brands),
    date_min: summary?.text_1 ?? null,
    date_max: summary?.text_2 ?? null,
  };
}

test("production market analytics SQL compiles at the D1 expression-depth limit", async () => {
  const directory = await mkdtemp(join(tmpdir(), "market-d1-depth-"));
  const databasePath = join(directory, "market.sqlite");
  try {
    const sqlite = new DatabaseSync(databasePath);
    await ensureMarketSchemaCore(sqliteAdapter(sqlite));
    sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
      CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);`);
    sqlite.close();
    const python = String.raw`
import base64, json, sqlite3, sys
payload = json.loads(base64.b64decode(sys.stdin.read()).decode('utf-8'))
db = sqlite3.connect('file:' + payload['database'] + '?mode=ro', uri=True)
if not hasattr(db, 'setlimit'):
    raise RuntimeError('Python sqlite3.setlimit is required for the D1 depth gate')
db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
negative = 'SELECT ' + ('abs(' * 101) + '1' + (')' * 101)
try:
    db.execute(negative)
except sqlite3.OperationalError as error:
    if 'Expression tree is too large' not in str(error):
        raise
else:
    raise RuntimeError('expression-depth negative control did not fail')
db.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 5)
compound_negative = ' UNION ALL '.join(['SELECT 1'] * 6)
try:
    db.execute(compound_negative)
except sqlite3.OperationalError as error:
    if 'too many terms in compound SELECT' not in str(error):
        raise
else:
    raise RuntimeError('compound-select negative control did not fail')
for query in payload['queries']:
    try:
        db.execute('EXPLAIN QUERY PLAN ' + query['sql'], query['bindings']).fetchone()
    except sqlite3.OperationalError as error:
        raise RuntimeError(query['name'] + ': ' + str(error)) from error
print('depth100 ok')
`;
    const productionQueries = [
      { name: "overview", sql: buildMarketOverviewAnalyticsSql() },
      { name: "cached overview", sql: buildMarketCachedOverviewAnalyticsSql() },
      { name: "monthly summary refresh", sql: buildMarketMonthlySummaryRefreshSql(), bindings: [1] },
      { name: "item trend", sql: buildMarketItemTrendSql() },
      { name: "admin comparison", sql: buildMarketAdminComparisonSql({
        factWhere: "WHERE (m.sku_code,m.category,m.scope,m.ranking_dimension) IN ((?,?,?,?),(?,?,?,?)) AND m.category IN (?) AND m.scope IN (?) AND m.ranking_dimension IN (?) AND m.operation_mode IN (?) AND m.brand IN (?) AND m.subcategory IN (?) AND (m.sku_code LIKE ? OR m.product_name LIKE ? OR m.brand LIKE ?) AND m.period_end>=? AND m.period_start<=?",
        priceBandWhere: "WHERE m.price_band IN (?,?)",
        exactIdentity: true,
      }) },
      { name: "admin item trend lite", sql: buildMarketAdminItemTrendLiteSql({
        factWhere: "WHERE m.sku_code=? AND m.category IN (?) AND m.scope IN (?) AND m.ranking_dimension IN (?) AND m.operation_mode IN (?) AND m.brand IN (?) AND m.subcategory IN (?) AND (m.sku_code LIKE ? OR m.product_name LIKE ? OR m.brand LIKE ?) AND m.period_end>=? AND m.period_start<=?",
        priceBandWhere: "WHERE m.price_band IN (?,?)",
      }) },
    ];
    const result = spawnSync("python", ["-c", python], {
      input: Buffer.from(JSON.stringify({
        database: databasePath.replaceAll("\\", "/"),
        queries: productionQueries.map((query) => ({
          ...query,
          bindings: "bindings" in query ? query.bindings : Array.from({ length: (query.sql.match(/\?(?!\d)/g) ?? []).length }, () => "value"),
        })),
      }), "utf8").toString("base64"),
      encoding: "utf8",
      maxBuffer: 2_000_000,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.match(result.stdout, /depth100 ok/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

function pausingAdapter(base: MarketSchemaDatabase, pause: (sql: string) => Promise<void>): MarketSchemaDatabase {
  return {
    prepare(sql: string) {
      const prepared = base.prepare(sql);
      return {
        bind(...values: unknown[]) {
          const bound = prepared.bind(...values);
          return {
            first: async <T>() => { await pause(sql); return bound.first<T>(); },
            all: async <T>() => { await pause(sql); return bound.all<T>(); },
            run: async () => { await pause(sql); return bound.run(); },
          };
        },
        first: async <T>() => { await pause(sql); return prepared.first<T>(); },
        all: async <T>() => { await pause(sql); return prepared.all<T>(); },
        run: async () => { await pause(sql); return prepared.run(); },
      };
    },
    batch(statements) { return base.batch(statements); },
  };
}

function commitThenThrowAdapter(base: MarketSchemaDatabase): MarketSchemaDatabase {
  let threw = false;
  return {
    prepare: (sql) => base.prepare(sql),
    async batch(statements) {
      const result = await base.batch(statements);
      if (!threw) {
        threw = true;
        throw new Error("forced lost batch response");
      }
      return result;
    },
  };
}

test("image cache claims are fenced and first success backfills only empty snapshot hashes", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const hashA = "a".repeat(64);
  const hashB = "b".repeat(64);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,image_url,raw_json,last_import_batch_id)
    VALUES
      ('cache-a',1,'2026-06-01','2026-06-30','缓存类目','POP','SKU','POP','CACHE-A','商品A','https://img.example/a.jpg','{}','batch'),
      ('cache-b',2,'2026-06-01','2026-06-30','缓存类目','POP','SKU','POP','CACHE-B','商品B','https://img.example/b.jpg','{}','batch');
    INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,image_url,ai_price_type,confirmed_market_price_cents,confirmation_status)
    VALUES
      ('cache-snapshot-url','缓存类目','POP','CACHE-A','SKU','2026-06','','https://img.example/a.jpg','',NULL,'missing'),
      ('cache-snapshot-legacy','缓存类目','POP','CACHE-B','SKU','2026-06','','','',NULL,'missing'),
      ('cache-snapshot-existing','缓存类目','POP','CACHE-A','SKU','2026-05','historical-hash','https://img.example/a.jpg','',NULL,'missing');`);
  sqlite.prepare(`INSERT INTO market_price_snapshots
      (id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,image_url,ai_price_type,confirmed_market_price_cents,confirmation_status)
    VALUES ('cache-snapshot-standard','缓存类目','POP','CACHE-A','SKU','2026-04',?,'https://img.example/a.jpg','标准售价',188800,'confirmed')`).run(hashA);

  const job = await createOrResumeMarketImageCacheJob(db, { batchId: "batch", requestedBy: "test" });
  const lease = await acquireMarketImageCacheJobLease(db, { jobId: job.id });
  assert.ok(lease);
  assert.equal((await discoverMarketImageCacheJobItems(db, lease)).discovered, 2);
  const fence = { jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch };
  const claimA = await claimMarketImageCache(db, { ...fence, sourceUrl: "https://img.example/a.jpg" });
  assert.equal(claimA?.attemptCount, 1);
  assert.equal(await claimMarketImageCache(db, { ...fence, sourceUrl: "https://img.example/a.jpg" }), null);
  const completedA = await completeMarketImageCacheClaim(db, {
    ...fence, ...claimA!, sourceUrl: "https://img.example/a.jpg", objectKey: "market/a.jpg",
    contentHash: hashA, mimeType: "image/jpeg", sizeBytes: 10, imageSource: "test",
  });
  assert.deepEqual(completedA, { completed: true });
  assert.equal((sqlite.prepare("SELECT image_content_sha256 hash FROM market_price_snapshots WHERE id='cache-snapshot-url'").get() as { hash: string }).hash, "");
  assert.equal((sqlite.prepare("SELECT image_content_sha256 hash FROM market_price_snapshots WHERE id='cache-snapshot-existing'").get() as { hash: string }).hash, "historical-hash");

  const staleClaim = await claimMarketImageCache(db, { ...fence, sourceUrl: "https://img.example/b.jpg" });
  assert.equal(staleClaim?.attemptCount, 1);
  sqlite.prepare("UPDATE market_image_cache SET status='failed' WHERE source_url='https://img.example/b.jpg'").run();
  sqlite.prepare("UPDATE market_image_cache_claims SET lease_expires_at=datetime('now','-1 second') WHERE source_url='https://img.example/b.jpg'").run();
  const replacementClaim = await claimMarketImageCache(db, { ...fence, sourceUrl: "https://img.example/b.jpg" });
  assert.equal(replacementClaim?.attemptCount, 2);
  const completedB = await completeMarketImageCacheClaim(db, {
    ...fence, ...replacementClaim!, sourceUrl: "https://img.example/b.jpg", objectKey: "market/b.jpg",
    contentHash: hashB, mimeType: "image/jpeg", sizeBytes: 20, imageSource: "test",
  });
  assert.deepEqual(completedB, { completed: true });
  assert.equal(await failMarketImageCacheClaim(db, {
    ...fence, ...staleClaim!, sourceUrl: "https://img.example/b.jpg", errorCode: "late_failure", errorMessage: "late",
  }), false);
  assert.deepEqual(await propagateMarketImageCacheBatch(db, {
    ...fence,
    images: [
      { sourceUrl: "https://img.example/a.jpg", contentHash: hashA },
      { sourceUrl: "https://img.example/b.jpg", contentHash: hashB },
    ],
  }), { snapshotsUpdated: 2, pricesInherited: 1 });
  assert.deepEqual({ ...(sqlite.prepare("SELECT image_content_sha256 hash,image_url imageUrl,confirmed_market_price_cents price,confirmation_status status FROM market_price_snapshots WHERE id='cache-snapshot-url'").get() as Record<string, unknown>) },
    { hash: hashA, imageUrl: "https://img.example/a.jpg", price: 188800, status: "confirmed" });
  assert.deepEqual({ ...(sqlite.prepare("SELECT status,content_sha256 hash,attempt_count attempts FROM market_image_cache WHERE source_url='https://img.example/b.jpg'").get() as Record<string, unknown>) },
    { status: "ready", hash: hashB, attempts: 2 });
  assert.deepEqual({ ...(sqlite.prepare("SELECT image_content_sha256 hash,image_url imageUrl FROM market_price_snapshots WHERE id='cache-snapshot-legacy'").get() as Record<string, unknown>) },
    { hash: hashB, imageUrl: "https://img.example/b.jpg" });
  sqlite.close();
});

async function oldMarketDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  for (const migration of ["../drizzle/0015_market_analysis.sql", "../drizzle/0020_market_image_cache.sql"]) {
    sqlite.exec(await readFile(new URL(migration, import.meta.url), "utf8"));
  }
  sqlite.exec(`
    INSERT INTO market_import_batches
      (id, source_type, file_name, file_size_bytes, file_hash, sheet_name, status, row_count)
    VALUES
      ('batch-sku','jd','market_POP_SKU_2026-06.csv',100,'old-hash-sku','CSV','completed',1),
      ('batch-spu','jd','market_self_SPU_2026-06.csv',100,'old-hash-spu','CSV','completed',1);
    INSERT INTO market_image_cache
      (source_url, status, object_key, content_sha256, mime_type, size_bytes)
    VALUES
      ('https://img10.360buyimg.com/imgzone/a.jpg','ready','market/a.jpg','hash-a','image/jpeg',10),
      ('https://img10.360buyimg.com/imgzone/b.jpg','ready','market/b.jpg','hash-b','image/jpeg',10);
    INSERT INTO market_ranking_entries
      (natural_key, source_row_number, period_start, period_end, category, scope, rank, sku_code, product_name, brand, price_cents, gmv_cents, quantity, visitors, image_url, raw_json, last_import_batch_id)
    VALUES
      ('2026-06-01|2026-06-30|净水|pop|SKU-1',1,'2026-06-01','2026-06-30','净水','pop',1,'SKU-1','商品1','品牌1',199900,1000000,5,100,'https://img10.360buyimg.com/imgzone/a.jpg','{"dimension":"SKU"}','batch-sku'),
      ('2026-06-01|2026-06-30|净水|自营|SPU-1',2,'2026-06-01','2026-06-30','净水','自营',2,'SPU-1','商品2','品牌2',299900,3000000,10,200,'https://img10.360buyimg.com/imgzone/b.jpg','{"dimension":"SPU"}','batch-spu');
  `);
  sqlite.exec(`
    INSERT INTO market_ranking_entries
      (natural_key, source_row_number, period_start, period_end, category, scope, rank, sku_code, product_name, brand, price_cents, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES
      ('legacy-duplicate-a',10,'2026-06-01','2026-06-30','category-duplicate','pop',10,'DUP-1','duplicate old A','brand',100,1000,1,1,'{"dimension":"SKU"}','batch-sku'),
      ('legacy-duplicate-b',11,'2026-06-01','2026-06-30','category-duplicate','pop',11,'DUP-1','duplicate old B','brand',200,2000,2,2,'{"dimension":"SKU"}','batch-sku'),
      ('legacy-scope-pop',12,'2026-06-01','2026-06-30','category-scope','pop',1,'SCOPE-1','scope pop','brand',300,3000,3,3,'{"dimension":"SKU"}','batch-sku'),
      ('legacy-scope-self',13,'2026-06-01','2026-06-30','category-scope','自营',1,'SCOPE-1','scope self','brand',400,4000,4,4,'{"dimension":"SKU"}','batch-spu');
  `);
  return sqlite;
}

test("0020 old market database upgrades columns, indexes, snapshots, and backfill deterministically", async () => {
  const sqlite = await oldMarketDatabase();
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const changesAfterUpgrade = (sqlite.prepare("SELECT total_changes() changes").get() as { changes: number }).changes;
  await ensureMarketSchemaCore(db);
  assert.equal((sqlite.prepare("SELECT total_changes() changes").get() as { changes: number }).changes, changesAfterUpgrade);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_audit_logs WHERE entity_type='runtime_schema' AND entity_id='market-runtime-schema-v13'").get() as { count: number }).count, 1);
  const columnNames = (table: string) => new Set((sqlite.prepare(`PRAGMA table_info("${table}")`).all() as Array<{ name: string }>).map((row) => row.name));
  assert.ok(columnNames("market_brand_suggestions").has("ai_brand"));
  assert.ok(columnNames("market_brand_recognition_jobs").has("processed_count"));
  assert.ok(columnNames("market_brand_seeds").has("normalized_seed"));
  for (const column of ["ranking_dimension", "operation_mode", "subcategory", "price_band_filter", "price_low_cents", "price_high_cents", "price_estimated", "gmv_raw", "gmv_low_cents", "gmv_high_cents"]) {
    assert.ok(columnNames("market_ranking_entries").has(column), column);
  }
  for (const column of ["scope", "image_content_sha256", "source_import_batch_id", "average_transaction_price_cents"]) {
    assert.ok(columnNames("market_price_snapshots").has(column), column);
  }

  const rows = sqlite.prepare("SELECT sku_code sku, operation_mode mode, ranking_dimension dimension, natural_key naturalKey FROM market_ranking_entries ORDER BY sku_code").all() as Array<{ sku: string; mode: string; dimension: string; naturalKey: string }>;
  assert.deepEqual(rows.filter((row) => ["SKU-1", "SPU-1"].includes(row.sku)).map((row) => [row.sku, row.mode, row.dimension]), [["SKU-1", "POP", "SKU"], ["SPU-1", "自营", "SPU"]]);
  assert.ok(rows.every((row) => row.naturalKey.length > 0));

  const snapshot = sqlite.prepare("SELECT source_price_cents sourcePrice, average_transaction_price_cents avgPrice, image_content_sha256 hash, image_url imageUrl, source_import_batch_id batchId FROM market_price_snapshots WHERE sku_code='SKU-1'").get() as { sourcePrice: number; avgPrice: number; hash: string; imageUrl: string; batchId: string };
  assert.deepEqual({ ...snapshot }, {
    sourcePrice: 199900,
    avgPrice: 200000,
    hash: "hash-a",
    imageUrl: "https://img10.360buyimg.com/imgzone/a.jpg",
    batchId: "batch-sku",
  });
  const duplicate = sqlite.prepare("SELECT COUNT(*) count, SUM(gmv_cents) gmv FROM market_ranking_entries WHERE sku_code='DUP-1'").get() as { count: number; gmv: number };
  assert.deepEqual({ ...duplicate }, { count: 1, gmv: 2000 });
  const scopeSnapshots = sqlite.prepare("SELECT scope, source_price_cents sourcePrice FROM market_price_snapshots WHERE category='category-scope' ORDER BY scope").all() as Array<{ scope: string; sourcePrice: number }>;
  assert.deepEqual(scopeSnapshots.map((row) => ({ ...row })), [{ scope: "pop", sourcePrice: 300 }, { scope: "自营", sourcePrice: 400 }]);

  const indexes = new Set((sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index'").all() as Array<{ name: string }>).map((row) => row.name));
  for (const index of ["market_entries_dimension_idx", "market_entries_subcategory_idx", "market_entries_canonical_price_band_uq", "market_price_snapshots_sku_month_uq"]) {
    assert.ok(indexes.has(index), index);
  }
  sqlite.close();
});

test("0026 and 0044 forward migrations preserve scope and install import safety", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const migrationFiles = [
    "0015_market_analysis.sql", "0016_market_sku_annotations.sql", "0017_market_annotation_reliability.sql",
    "0019_young_ozymandias.sql", "0020_market_image_cache.sql",
    "0021_market_analysis_2.sql", "0022_market_analysis_review_fixes.sql", "0023_market_annotation_monthly_price.sql",
    "0024_market_master_workflow.sql", "0025_market_scope_and_executor.sql", "0026_market_mapping_and_download_scope.sql",
    "0027_market_brand_suggestions.sql", "0028_market_brand_recognition_jobs.sql", "0029_market_brand_seeds.sql",
    "0031_market_rank_gmv_ranges.sql", "0032_market_sku_gmv_totals.sql", "0033_market_representative_index.sql",
    "0034_market_annotation_catalog_index.sql", "0035_market_subcategory_taxonomy.sql",
    "0036_backfill_market_subcategory_taxonomy.sql", "0037_market_annotation_reuse_index.sql", "0038_market_master_identities.sql",
  ];
  for (const file of migrationFiles) {
    const sql = await readFile(new URL(`../drizzle/${file}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) if (statement.trim()) sqlite.exec(statement);
    if (file === "0021_market_analysis_2.sql") {
      sqlite.exec(`
      INSERT INTO market_ranking_entries
        (natural_key, source_row_number, period_start, period_end, category, scope, sku_code, raw_json, last_import_batch_id, gmv_cents, quantity, price_cents)
      VALUES
        ('legacy-a',1,'2026-07-01','2026-07-31','migration-category','pop','MIG-1','{"dimension":"SKU"}','batch-a',1000,1,100),
        ('legacy-b',2,'2026-07-01','2026-07-31','migration-category','pop','MIG-1','{"dimension":"SKU"}','batch-a',2000,2,200),
        ('legacy-c',3,'2026-07-02','2026-07-30','migration-category','pop','MIG-1','{"dimension":"SKU"}','batch-a',1500,1,150),
        ('legacy-self',4,'2026-07-01','2026-07-31','migration-category','自营','MIG-1','{"dimension":"SKU"}','batch-a',3000,3,300),
        ('separator-a',5,'2026-07-01','2026-07-31','a','pop','b-SKU-c','{"dimension":"SKU"}','batch-a',1000,1,100),
        ('separator-b',6,'2026-07-01','2026-07-31','a-SKU-b','pop','c','{"dimension":"SKU"}','batch-a',1000,1,100);
      `);
    }
  }
  const safetyMigration = await readFile(new URL("../drizzle/0044_market_import_safety.sql", import.meta.url), "utf8");
  for (const statement of safetyMigration.split("--> statement-breakpoint")) if (statement.trim()) sqlite.exec(statement);
  const facts = sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='migration-category' AND sku_code='MIG-1'").get() as { count: number };
  const snapshots = sqlite.prepare("SELECT scope, source_price_cents sourcePrice FROM market_price_snapshots WHERE category='migration-category' AND sku_code='MIG-1' ORDER BY scope").all() as Array<{ scope: string; sourcePrice: number }>;
  assert.equal(facts.count, 3);
  assert.deepEqual(snapshots.map((row) => ({ ...row })), [{ scope: "pop", sourcePrice: 200 }, { scope: "自营", sourcePrice: 300 }]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_price_snapshots WHERE sku_code IN ('b-SKU-c','c')").get() as { count: number }).count, 2);
  assert.ok((sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='market_price_snapshots_sku_month_uq'").get() as { name: string } | undefined)?.name);
  const source = sqlite.prepare("SELECT source_brand sourceBrand, source_operation_mode sourceMode FROM market_ranking_entries WHERE category='migration-category' LIMIT 1").get() as { sourceBrand: string; sourceMode: string };
  assert.equal(typeof source.sourceBrand, "string");
  assert.equal(typeof source.sourceMode, "string");
  const taskIndex = String((sqlite.prepare("SELECT sql FROM sqlite_master WHERE type='index' AND name='market_download_tasks_unique_uq'").get() as { sql: string }).sql);
  assert.match(taskIndex, /category, scope, month, ranking_dimension/i);
  assert.ok(sqlite.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='market_import_range_claims'").get());
  assert.ok(sqlite.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='market_import_staging_rows'").get());
  assert.ok((sqlite.prepare("PRAGMA table_info(market_download_tasks)").all() as Array<{ name: string }>).some((column) => column.name === "execution_token"));
  assert.ok((sqlite.prepare("PRAGMA table_info(market_import_batches)").all() as Array<{ name: string }>).some((column) => column.name === "owner_token"));
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE natural_key LIKE 'market-key-v2|%'").get() as { count: number }).count, 0);
  sqlite.close();
});

test("0076 and runtime schema share the bounded import identity refresh-key contract", async () => {
  const [migration, importCore, masterIdentity] = await Promise.all([
    readFile(new URL("../drizzle/0077_market_import_identity_refresh_keys.sql", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/import-core.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/master-identity.ts", import.meta.url), "utf8"),
  ]);
  for (const source of [migration, marketImportIdentityRefreshKeysTableStatement]) {
    assert.match(source, /market_import_identity_refresh_keys_v2/);
    for (const column of ["batch_id", "owner_token", "category", "scope", "ranking_dimension", "sku_code"]) {
      assert.match(source, new RegExp(column));
    }
    assert.match(source, /PRIMARY KEY/);
  }
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(migration);
  sqlite.exec(`INSERT INTO market_import_identity_refresh_keys_v2
    (batch_id,owner_token,category,scope,ranking_dimension,sku_code)
    VALUES ('batch','owner','category','scope','SKU','sku')`);
  assert.throws(() => sqlite.exec(`INSERT INTO market_import_identity_refresh_keys_v2
    (batch_id,owner_token,category,scope,ranking_dimension,sku_code)
    VALUES ('batch','owner','category','scope','SKU','sku')`), /UNIQUE constraint failed/);
  sqlite.exec(`CREATE TABLE market_master_identities (
    category TEXT NOT NULL,scope TEXT NOT NULL,ranking_dimension TEXT NOT NULL,sku_code TEXT NOT NULL,
    latest_entry_id INTEGER NOT NULL,PRIMARY KEY(category,scope,ranking_dimension,sku_code));`);
  const plan = sqlite.prepare(`EXPLAIN QUERY PLAN DELETE FROM market_master_identities
    WHERE (category,scope,ranking_dimension,sku_code) IN (
      SELECT category,scope,ranking_dimension,sku_code FROM market_import_identity_refresh_keys_v2
      WHERE batch_id=? AND owner_token=?
    )`).all("batch", "owner").map((row) => String(row.detail));
  assert.ok(plan.some((detail) => /SEARCH market_master_identities USING/.test(detail)), plan.join("\n"));
  assert.ok(plan.some((detail) => /SEARCH market_import_identity_refresh_keys_v2 USING/.test(detail)), plan.join("\n"));
  assert.match(importCore, /MAX_MARKET_IDENTITY_REFRESH_KEYS = 10_000/);
  assert.equal((importCore.match(/LIMIT \$\{MAX_MARKET_IDENTITY_REFRESH_KEYS \+ 1\}/g) ?? []).length, 2);
  assert.match(importCore, /SELECT COUNT\(\*\) FROM market_import_identity_refresh_keys_v2 WHERE batch_id=\? AND owner_token=\?/);
  assert.match(masterIdentity, /WHERE \(category, scope, ranking_dimension, sku_code\) IN \(/);
  assert.doesNotMatch(masterIdentity.slice(
    masterIdentity.indexOf("const deleteScope"), masterIdentity.indexOf("const sourceFilter"),
  ), /WHERE EXISTS/);
  sqlite.close();
});

test("runtime upgrade does not rewrite valid dimensions and swaps the canonical index safely", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const migrationFiles = [
    "0015_market_analysis.sql", "0016_market_sku_annotations.sql", "0017_market_annotation_reliability.sql",
    "0019_young_ozymandias.sql", "0020_market_image_cache.sql",
    "0021_market_analysis_2.sql", "0022_market_analysis_review_fixes.sql", "0023_market_annotation_monthly_price.sql",
    "0024_market_master_workflow.sql", "0025_market_scope_and_executor.sql", "0026_market_mapping_and_download_scope.sql",
  ];
  for (const file of migrationFiles) {
    const sql = await readFile(new URL(`../drizzle/${file}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) if (statement.trim()) sqlite.exec(statement);
  }
  sqlite.exec(`
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('v4-key',1,'2026-07-01','2026-07-31','净水','POP','SKU','POP','SKU-V5','商品','{}','batch');
    CREATE TRIGGER reject_valid_dimension_rewrite
    BEFORE UPDATE OF ranking_dimension ON market_ranking_entries
    WHEN OLD.ranking_dimension IN ('SKU','SPU')
    BEGIN SELECT RAISE(ABORT, 'valid dimension must not be rewritten'); END;
  `);
  assert.ok(sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='market_entries_canonical_uq'").get());

  await ensureMarketSchemaCore(sqliteAdapter(sqlite));

  assert.ok(sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='market_entries_canonical_price_band_uq'").get());
  assert.equal(sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='market_entries_canonical_uq'").get(), undefined);
  assert.ok(sqlite.prepare("SELECT id FROM market_master_audit_logs WHERE entity_type='runtime_schema' AND entity_id='market-runtime-schema-v13'").get());
  sqlite.close();
});

function entry(overrides: Partial<MarketEntryForImport> = {}): MarketEntryForImport {
  const result: MarketEntryForImport = {
    naturalKey: "",
    sourceRowNumber: 1,
    periodStart: "2026-06-01",
    periodEnd: "2026-06-30",
    category: "净水",
    scope: "pop",
    priceBandFilter: "全部",
    rankingDimension: "SKU",
    operationMode: "POP",
    subcategory: "台式",
    rank: 1,
    skuCode: "SKU-1",
    productName: "商品1",
    brand: "品牌1",
    priceCents: 199900,
    priceLowCents: 199900,
    priceHighCents: 199900,
    priceEstimated: false,
    priceRaw: "1999",
    gmvCents: 1000000,
    gmvLowCents: 900000,
    gmvHighCents: 1100000,
    gmvRaw: "9000~11000",
    quantity: 5,
    quantityLow: 1,
    quantityHigh: 10,
    quantityRaw: "1~10",
    pageViews: 1000,
    pageViewsRaw: "1000",
    visitors: 100,
    visitorsLow: 50,
    visitorsHigh: 150,
    visitorsRaw: "50~150",
    conversionBps: 500,
    conversionLowBps: 100,
    conversionHighBps: 1000,
    conversionRaw: "1%~10%",
    cartCustomers: 20,
    cartCustomersRaw: "20",
    searchClicks: 30,
    searchClicksRaw: "30",
    imageUrl: "https://img10.360buyimg.com/imgzone/a.jpg",
    productUrl: "https://item.jd.com/1.html",
    raw: { dimension: "SKU" },
    ...overrides,
  };
  return { ...result, naturalKey: marketNaturalKey(result) };
}

test("market import replaces the complete claimed range and removes rows absent from changed content", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await saveMarketImportCore({
    db,
    batchId: "market-content-a",
    sourceType: "jd-top",
    fileName: "market-a.xlsx",
    fileSizeBytes: 1,
    fileHash: "a".repeat(64),
    sheetName: "Sheet1",
    rows: [
      entry({ sourceRowNumber: 1, skuCode: "SKU-1" }),
      entry({ sourceRowNumber: 2, skuCode: "SKU-2", rank: 2 }),
    ],
    warnings: [],
  });
  assert.equal((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision, 2);
  assert.deepEqual((sqlite.prepare("SELECT sku_code skuCode FROM market_master_identities ORDER BY sku_code").all() as Array<Record<string, unknown>>).map((row) => ({ ...row })), [
    { skuCode: "SKU-1" },
    { skuCode: "SKU-2" },
  ]);
  await saveMarketImportCore({
    db,
    batchId: "market-content-b",
    sourceType: "jd-top",
    fileName: "market-b.xlsx",
    fileSizeBytes: 1,
    fileHash: "b".repeat(64),
    sheetName: "Sheet1",
    rows: [entry({ sourceRowNumber: 1, skuCode: "SKU-1", productName: "更新商品" })],
    warnings: [],
  });
  const rows = sqlite.prepare("SELECT sku_code skuCode, product_name productName, last_import_batch_id batchId FROM market_ranking_entries ORDER BY sku_code").all();
  assert.deepEqual(rows.map((row) => ({ ...row })), [{ skuCode: "SKU-1", productName: "更新商品", batchId: "market-content-b" }]);
  assert.deepEqual((sqlite.prepare("SELECT sku_code skuCode FROM market_master_identities ORDER BY sku_code").all() as Array<Record<string, unknown>>).map((row) => ({ ...row })), [
    { skuCode: "SKU-1" },
  ]);
  assert.equal((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision, 3);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2").get() as { count: number }).count, 0);
  sqlite.close();
});

test("market import identity refresh preserves the latest fact and every unrelated range", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await saveMarketImportCore({
    db, batchId: "identity-june-old", sourceType: "jd", fileName: "june-old.csv", fileSizeBytes: 10,
    fileHash: "identity-june-old-hash", sheetName: "CSV", warnings: [],
    rows: [
      entry({ sourceRowNumber: 1, skuCode: "SKU-KEEP" }),
      entry({ sourceRowNumber: 2, skuCode: "SKU-LATEST-SURVIVES" }),
      entry({ sourceRowNumber: 3, skuCode: "SKU-GONE" }),
    ],
  });
  await saveMarketImportCore({
    db, batchId: "identity-july", sourceType: "jd", fileName: "july.csv", fileSizeBytes: 10,
    fileHash: "identity-july-hash", sheetName: "CSV", warnings: [],
    rows: [entry({
      sourceRowNumber: 1, periodStart: "2026-07-01", periodEnd: "2026-07-31",
      skuCode: "SKU-LATEST-SURVIVES", productName: "July survivor",
    })],
  });
  await saveMarketImportCore({
    db, batchId: "identity-unrelated", sourceType: "jd", fileName: "unrelated.csv", fileSizeBytes: 10,
    fileHash: "identity-unrelated-hash", sheetName: "CSV", warnings: [],
    rows: [entry({ sourceRowNumber: 1, category: "unrelated-category", skuCode: "SKU-UNRELATED" })],
  });
  await saveMarketImportCore({
    db, batchId: "identity-june-new", sourceType: "jd", fileName: "june-new.csv", fileSizeBytes: 10,
    fileHash: "identity-june-new-hash", sheetName: "CSV", warnings: [],
    rows: [entry({ sourceRowNumber: 1, skuCode: "SKU-KEEP", productName: "June replacement" })],
  });

  assert.deepEqual((sqlite.prepare(`SELECT identity.sku_code skuCode, fact.period_end periodEnd, fact.product_name productName
      FROM market_master_identities identity
      JOIN market_ranking_entries fact ON fact.id=identity.latest_entry_id
      ORDER BY identity.sku_code`).all() as Array<Record<string, unknown>>).map((row) => ({ ...row })), [
    { skuCode: "SKU-KEEP", periodEnd: "2026-06-30", productName: "June replacement" },
    { skuCode: "SKU-LATEST-SURVIVES", periodEnd: "2026-07-31", productName: "July survivor" },
    { skuCode: "SKU-UNRELATED", periodEnd: "2026-06-30", productName: "商品1" },
  ]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_identities WHERE sku_code='SKU-GONE'").get() as { count: number }).count, 0);
  assert.deepEqual((sqlite.prepare(`SELECT sku_code skuCode,gmv_total_cents gmv
      FROM market_sku_gmv_totals WHERE sku_code IN ('SKU-GONE','SKU-LATEST-SURVIVES','SKU-UNRELATED')
      ORDER BY sku_code`).all() as Array<Record<string, unknown>>).map((row) => ({ ...row })), [
    { skuCode: "SKU-LATEST-SURVIVES", gmv: 1_000_000 },
    { skuCode: "SKU-UNRELATED", gmv: 1_000_000 },
  ]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2").get() as { count: number }).count, 0);
  sqlite.close();
});

test("bulk market import publishes exact lifecycle revisions while direct writes remain observable", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  await ensureMarketSystemKpiCacheSchema(db as never);
  const revision = () => Number((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision);
  const beforeImport = revision();
  await saveMarketImportCore({
    db, batchId: "bulk-revision", sourceType: "jd", fileName: "bulk.csv", fileSizeBytes: 10,
    fileHash: "bulk-revision-hash", sheetName: "CSV", warnings: [],
    rows: Array.from({ length: 1_000 }, (_, index) => entry({
      sourceRowNumber: index + 1, category: "bulk-revision-category", skuCode: `SKU-BULK-${index}`,
    })),
  });
  assert.equal(revision(), beforeImport + 3,
    "batch reservation, atomic fact publication, and batch completion must each publish one observable revision");
  assert.deepEqual({ ...(sqlite.prepare(`SELECT suppress_all_revision suppressAll,
      suppress_identity_revision suppressIdentity,owner_token owner
      FROM market_system_kpi_cache_control WHERE id=1`).get() as Record<string, unknown>) }, {
    suppressAll: 0, suppressIdentity: 0, owner: "",
  });

  const beforeDirectWrite = revision();
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('direct-after-bulk',2000,'2026-07-01','2026-07-31','direct-category','POP','SKU','POP','DIRECT-AFTER-BULK','Direct','{}','direct')`);
  assert.equal(revision(), beforeDirectWrite + 1);
  sqlite.close();
});

test("market import refuses an unbounded identity refresh and rolls the owner fence back", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const insert = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,
      operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES (?,?,'2026-06-01','2026-06-30','overflow-category','pop','全部','SKU','POP',?,'Overflow','{}','old')`);
  sqlite.exec("BEGIN");
  for (let index = 0; index < 10_001; index += 1) insert.run(`overflow-${index}`, index + 1, `SKU-OVERFLOW-${index}`);
  sqlite.exec("COMMIT");

  await assert.rejects(() => saveMarketImportCore({
    db, batchId: "overflow-refresh", sourceType: "jd", fileName: "overflow.csv", fileSizeBytes: 10,
    fileHash: "overflow-refresh-hash", sheetName: "CSV", warnings: [],
    rows: [entry({ sourceRowNumber: 1, category: "overflow-category", skuCode: "SKU-NEW" })],
  }), /NOT NULL constraint failed/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='overflow-category'").get() as { count: number }).count, 10_001);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2").get() as { count: number }).count, 0);
  assert.deepEqual({ ...(sqlite.prepare(`SELECT suppress_all_revision suppressAll,
      suppress_identity_revision suppressIdentity,owner_token owner
      FROM market_system_kpi_cache_control WHERE id=1`).get() as Record<string, unknown>) }, {
    suppressAll: 0, suppressIdentity: 0, owner: "",
  });
  sqlite.close();
});

test("market import refresh keys are isolated from a stale owner with the same batch id", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('stale-owner-fact',1,'2026-05-01','2026-05-31','stale-owner-category','POP','SKU','POP','SKU-STALE-OWNER','Stale','{}','old');
    INSERT INTO market_master_identities (category,scope,ranking_dimension,sku_code,latest_entry_id)
    SELECT category,scope,ranking_dimension,sku_code,id FROM market_ranking_entries WHERE natural_key='stale-owner-fact';
    INSERT INTO market_import_identity_refresh_keys_v2
      (batch_id,owner_token,category,scope,ranking_dimension,sku_code)
    VALUES ('same-batch-id','stale-owner','stale-owner-category','POP','SKU','SKU-STALE-OWNER');
    CREATE TRIGGER reject_stale_owner_identity_delete BEFORE DELETE ON market_master_identities
      WHEN OLD.sku_code='SKU-STALE-OWNER' BEGIN SELECT RAISE(ABORT,'stale owner key was consumed'); END;`);

  await saveMarketImportCore({
    db, batchId: "same-batch-id", sourceType: "jd", fileName: "new-owner.csv", fileSizeBytes: 10,
    fileHash: "new-owner-hash", sheetName: "CSV", warnings: [],
    rows: [entry({ sourceRowNumber: 1, category: "new-owner-category", skuCode: "SKU-NEW-OWNER" })],
  });
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_identities WHERE sku_code='SKU-STALE-OWNER'").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2 WHERE batch_id='same-batch-id' AND owner_token='stale-owner'").get() as { count: number }).count, 1);
  sqlite.close();
});

test("market publish aborts inside SQL when its batch owner disappears before commit", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let removeBatchBeforePublish = false;
  const db = sqliteAdapter(sqlite, { beforeBatch: async () => {
    if (!removeBatchBeforePublish) return;
    removeBatchBeforePublish = false;
    sqlite.exec("DELETE FROM market_import_batches WHERE id='missing-owner-batch'");
  } });
  await ensureMarketSchemaCore(db);
  const beforeRevision = Number((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision);
  removeBatchBeforePublish = true;

  await assert.rejects(() => saveMarketImportCore({
    db, batchId: "missing-owner-batch", sourceType: "jd", fileName: "missing-owner.csv", fileSizeBytes: 10,
    fileHash: "missing-owner-hash", sheetName: "CSV", warnings: [],
    rows: [entry({ sourceRowNumber: 1, category: "missing-owner-category", skuCode: "SKU-MISSING-OWNER" })],
  }), /CHECK constraint failed/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='missing-owner-category'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_staging_rows").get() as { count: number }).count, 1,
    "ownerless staging is deliberately retained when the batch row vanished, so cleanup cannot erase a replacement owner's rows");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_range_claims").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision, beforeRevision);
  assert.deepEqual({ ...(sqlite.prepare(`SELECT suppress_all_revision suppressAll,
      suppress_identity_revision suppressIdentity,owner_token owner
      FROM market_system_kpi_cache_control WHERE id=1`).get() as Record<string, unknown>) }, {
    suppressAll: 0, suppressIdentity: 0, owner: "",
  });
  sqlite.close();
});

test("daily market backfill replaces only its exact day and preserves other days in the same month", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await saveMarketImportCore({
    db, batchId: "daily-a", sourceType: "jd", fileName: "daily-a.csv", fileSizeBytes: 10,
    fileHash: "daily-a-hash", sheetName: "CSV", warnings: [],
    rows: [
      entry({ sourceRowNumber: 1, periodStart: "2026-07-01", periodEnd: "2026-07-01", skuCode: "SKU-A" }),
      entry({ sourceRowNumber: 2, periodStart: "2026-07-02", periodEnd: "2026-07-02", skuCode: "SKU-B" }),
    ],
  });
  await saveMarketImportCore({
    db, batchId: "daily-b", sourceType: "jd", fileName: "daily-b.csv", fileSizeBytes: 10,
    fileHash: "daily-b-hash", sheetName: "CSV", warnings: [],
    rows: [entry({ sourceRowNumber: 1, periodStart: "2026-07-02", periodEnd: "2026-07-02", skuCode: "SKU-C" })],
  });
  assert.deepEqual(sqlite.prepare(`SELECT period_end day, sku_code sku, last_import_batch_id batchId
    FROM market_ranking_entries ORDER BY period_end, sku_code`).all().map((row) => ({ ...row })), [
    { day: "2026-07-01", sku: "SKU-A", batchId: "daily-a" },
    { day: "2026-07-02", sku: "SKU-C", batchId: "daily-b" },
  ]);
  sqlite.close();
});

test("daily market replacement materializes distinct scopes before deleting facts", async () => {
  const source = await readFile(new URL("../lib/market/import-core.ts", import.meta.url), "utf8");
  const replacement = source.slice(source.indexOf("const replaceClaimedMarketFactsSql"), source.indexOf("const captureOldMarketIdentityRefreshKeysSql"));
  assert.match(replacement, /SELECT DISTINCT/);
  assert.match(replacement, /\) IN \(/);
  assert.doesNotMatch(replacement, /WHERE EXISTS \(/);
});

test("monthly snapshot backfill selects one fact when a SKU has multiple date ranges in one month", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, price_cents, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES
      ('partial',1,'2026-06-01','2026-06-15','category-month','pop','SKU','POP','SKU-MONTH','Partial','Brand',100,1000,1,1,'{}','batch'),
      ('full',2,'2026-06-01','2026-06-30','category-month','pop','SKU','POP','SKU-MONTH','Full','Brand',200,2000,2,2,'{}','batch')`);
  sqlite.prepare("DELETE FROM market_master_audit_logs WHERE entity_type='runtime_schema'").run();
  await ensureMarketSchemaCore(db);
  const snapshots = sqlite.prepare("SELECT id, source_price_cents sourcePrice FROM market_price_snapshots WHERE category='category-month' AND sku_code='SKU-MONTH'").all() as Array<{ id: string; sourcePrice: number }>;
  assert.equal(snapshots.length, 1);
  assert.equal(snapshots[0]?.sourcePrice, 200);
  assert.match(snapshots[0]?.id ?? "", /^market-price-backfill-v2-/);
  sqlite.close();
});

test("snapshot ids cannot collide when business-key components contain separators", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const rows = [
    entry({ naturalKey: "key-a", category: "a-b", scope: "c", skuCode: "X" }),
    entry({ naturalKey: "key-b", category: "a", scope: "b-c", skuCode: "X", sourceRowNumber: 2 }),
  ];
  await saveMarketImportCore({ db, batchId: "separator-batch", sourceType: "jd", fileName: "separator.csv", fileSizeBytes: 10, fileHash: "separator-hash", sheetName: "CSV", rows, warnings: [] });
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_price_snapshots WHERE sku_code='X'").get() as { count: number }).count, 2);
  sqlite.close();
});

test("same market fact with different file hashes updates the canonical fact instead of duplicating sales", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.prepare("INSERT INTO market_image_cache (source_url,status,content_sha256) VALUES ('https://img10.360buyimg.com/imgzone/a.jpg','ready','hash-a')").run();

  const first = await saveMarketImportCore({ db, batchId: "batch-1", sourceType: "jd", fileName: "a.csv", fileSizeBytes: 10, fileHash: "hash-1", sheetName: "CSV", rows: [entry()], warnings: [] });
  const second = await saveMarketImportCore({ db, batchId: "batch-2", sourceType: "jd", fileName: "b.csv", fileSizeBytes: 11, fileHash: "hash-2", sheetName: "CSV", rows: [entry({ gmvCents: 1200000, naturalKey: "2026-06-01|2026-06-30|净水|pop|全部|SKU|SKU-1" })], warnings: [] });

  assert.equal(first.insertedCount, 1);
  assert.equal(second.updatedCount, 1);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count, SUM(gmv_cents) gmv FROM market_ranking_entries").get() as { count: number; gmv: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT SUM(gmv_cents) gmv FROM market_ranking_entries").get() as { gmv: number }).gmv, 1200000);
  assert.match((sqlite.prepare("SELECT natural_key naturalKey FROM market_ranking_entries WHERE sku_code='SKU-1'").get() as { naturalKey: string }).naturalKey, /^market-key-v2\|/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_batches").get() as { count: number }).count, 2);
  assert.equal((sqlite.prepare("SELECT image_content_sha256 hash FROM market_price_snapshots WHERE sku_code='SKU-1'").get() as { hash: string }).hash, "hash-a");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_subcategory_taxonomy WHERE category='净水' AND subcategory='台式' AND status='active'").get() as { count: number }).count, 1);
  sqlite.close();
});

test("changing a ranking image URL invalidates the old snapshot hash and image-derived confirmation", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.prepare("INSERT INTO market_image_cache (source_url,status,content_sha256) VALUES ('https://img10.360buyimg.com/imgzone/a.jpg','ready','old-hash')").run();
  await saveMarketImportCore({
    db, batchId: "image-change-old", sourceType: "jd", fileName: "old.csv", fileSizeBytes: 10,
    fileHash: "image-change-old-file", sheetName: "CSV", rows: [entry()], warnings: [],
  });
  sqlite.prepare(`UPDATE market_price_snapshots SET ai_image_price_cents=188800,ai_price_type='标准售价',
    ai_confidence_bps=9900,ai_reason='旧图识别',confirmed_market_price_cents=188800,
    confirmation_status='confirmed',confirmed_by='admin@test',confirmed_at=CURRENT_TIMESTAMP,
    source_job_item_id='old-item',prompt_version_id='old-prompt' WHERE sku_code='SKU-1'`).run();

  await saveMarketImportCore({
    db, batchId: "image-change-new", sourceType: "jd", fileName: "new.csv", fileSizeBytes: 11,
    fileHash: "image-change-new-file", sheetName: "CSV",
    rows: [entry({ imageUrl: "https://img10.360buyimg.com/imgzone/b.jpg" })], warnings: [],
  });
  assert.deepEqual({ ...(sqlite.prepare(`SELECT image_url imageUrl,image_content_sha256 hash,
      ai_image_price_cents aiPrice,confirmed_market_price_cents confirmedPrice,confirmation_status status,
      confirmed_by confirmedBy,source_job_item_id sourceItem,prompt_version_id promptId
    FROM market_price_snapshots WHERE sku_code='SKU-1'`).get() as Record<string, unknown>) }, {
    imageUrl: "https://img10.360buyimg.com/imgzone/b.jpg", hash: "", aiPrice: null,
    confirmedPrice: null, status: "source_table", confirmedBy: "", sourceItem: "", promptId: "",
  });

  const newHash = "c".repeat(64);
  const job = await createOrResumeMarketImageCacheJob(db, { requestedBy: "test" });
  const lease = await acquireMarketImageCacheJobLease(db, { jobId: job.id });
  assert.ok(lease);
  assert.equal((await discoverMarketImageCacheJobItems(db, lease)).discovered, 1);
  const fence = { jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch };
  const claim = await claimMarketImageCache(db, { ...fence, sourceUrl: "https://img10.360buyimg.com/imgzone/b.jpg" });
  assert.equal(claim?.attemptCount, 1);
  await completeMarketImageCacheClaim(db, {
    ...fence, ...claim!, sourceUrl: "https://img10.360buyimg.com/imgzone/b.jpg", objectKey: "market/new.jpg",
    contentHash: newHash, mimeType: "image/jpeg", sizeBytes: 20, imageSource: "test",
  });
  await propagateMarketImageCacheBatch(db, {
    ...fence,
    images: [{ sourceUrl: "https://img10.360buyimg.com/imgzone/b.jpg", contentHash: newHash }],
  });
  assert.equal((sqlite.prepare("SELECT image_content_sha256 hash FROM market_price_snapshots WHERE sku_code='SKU-1'").get() as { hash: string }).hash, newHash);
  sqlite.close();
});

test("market import directly inherits a confirmed standard price only for the same SKU image", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    INSERT INTO market_image_cache (source_url,status,content_sha256) VALUES
      ('https://img10.360buyimg.com/imgzone/a.jpg','ready','hash-a'),
      ('https://img10.360buyimg.com/imgzone/b.jpg','ready','hash-b');
    INSERT INTO market_price_snapshots
      (id,category,scope,sku_code,ranking_dimension,month,ai_price_type,confirmed_market_price_cents,
       price_low_cents,price_high_cents,image_content_sha256,confirmation_status,confirmed_by,confirmed_at)
    VALUES ('history-standard','净水','pop','SKU-1','SKU','2026-06','标准售价',259900,
      259900,259900,'hash-a','confirmed','admin@test','2026-07-01 00:00:00');
  `);

  await saveMarketImportCore({
    db, batchId: "same-image-batch", sourceType: "jd", fileName: "same-image.csv", fileSizeBytes: 10,
    fileHash: "same-image-file-hash", sheetName: "CSV",
    rows: [entry({ periodStart: "2026-07-01", periodEnd: "2026-07-31", imageUrl: "https://img10.360buyimg.com/imgzone/a.jpg" })], warnings: [],
  });
  await saveMarketImportCore({
    db, batchId: "changed-image-batch", sourceType: "jd", fileName: "changed-image.csv", fileSizeBytes: 10,
    fileHash: "changed-image-file-hash", sheetName: "CSV",
    rows: [entry({ periodStart: "2026-08-01", periodEnd: "2026-08-31", imageUrl: "https://img10.360buyimg.com/imgzone/b.jpg" })], warnings: [],
  });

  const rows = sqlite.prepare(`SELECT month,image_content_sha256 hash,confirmed_market_price_cents price,
    confirmation_status status,confirmed_by confirmedBy FROM market_price_snapshots WHERE sku_code='SKU-1' ORDER BY month`).all() as Array<Record<string, unknown>>;
  assert.deepEqual(rows.map((row) => ({ ...row })), [
    { month: "2026-06", hash: "hash-a", price: 259900, status: "confirmed", confirmedBy: "admin@test" },
    { month: "2026-07", hash: "hash-a", price: 259900, status: "confirmed", confirmedBy: "system:history_same_image" },
    { month: "2026-08", hash: "hash-b", price: null, status: "source_table", confirmedBy: "" },
  ]);
  sqlite.close();
});

test("0051 backfills only pending SKU snapshots with the same confirmed standard image", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_price_snapshots
    (id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,ai_price_type,
     confirmed_market_price_cents,confirmation_status,confirmed_by,confirmed_at)
    VALUES
      ('migration-source','净水','pop','SKU-51','SKU','2026-01','hash-same','标准售价',319900,'confirmed','admin@test','2026-02-01 00:00:00'),
      ('migration-same','净水','pop','SKU-51','SKU','2026-02','hash-same','',NULL,'missing','',NULL),
      ('migration-changed','净水','pop','SKU-51','SKU','2026-03','hash-changed','',NULL,'missing','',NULL),
      ('migration-spu','净水','pop','SKU-51','SPU','2026-02','hash-same','',NULL,'missing','',NULL);`);
  const migration = await readFile(new URL("../drizzle/0051_market_standard_sku_image_price_inheritance.sql", import.meta.url), "utf8");
  sqlite.exec(migration);
  const rows = sqlite.prepare(`SELECT id,confirmed_market_price_cents price,confirmation_status status
    FROM market_price_snapshots WHERE id<>'migration-source' ORDER BY id`).all() as Array<Record<string, unknown>>;
  assert.deepEqual(rows.map((row) => ({ ...row })), [
    { id: "migration-changed", price: null, status: "missing" },
    { id: "migration-same", price: 319900, status: "confirmed" },
    { id: "migration-spu", price: null, status: "missing" },
  ]);
  sqlite.close();
});

test("failed market publish rolls back every fact, snapshot, and taxonomy row", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TRIGGER reject_market_import_row
    BEFORE INSERT ON market_ranking_entries
    WHEN NEW.sku_code='SKU-FAIL'
    BEGIN SELECT RAISE(ABORT, 'forced publish failure'); END;`);
  const rows = Array.from({ length: 81 }, (_, index) => entry({
    sourceRowNumber: index + 1,
    category: "atomic-category",
    subcategory: "atomic-subcategory",
    skuCode: index === 80 ? "SKU-FAIL" : `SKU-${index + 1}`,
    productName: `商品 ${index + 1}`,
  }));

  await assert.rejects(saveMarketImportCore({
    db, batchId: "atomic-failure", sourceType: "jd", fileName: "atomic.csv",
    fileSizeBytes: 100, fileHash: "atomic-failure-hash", sheetName: "CSV", rows, warnings: [],
  }), /forced publish failure/);

  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='atomic-category'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_price_snapshots WHERE category='atomic-category'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_subcategory_taxonomy WHERE category='atomic-category'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_staging_rows").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_range_claims").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT suppress_identity_revision value FROM market_system_kpi_cache_control WHERE id=1").get() as { value: number }).value, 0);
  assert.equal((sqlite.prepare("SELECT status FROM market_import_batches WHERE id='atomic-failure'").get() as { status: string }).status, "failed");
  sqlite.close();
});

test("derived-cache failure rolls back the same market publish transaction", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TRIGGER reject_market_identity_refresh
    BEFORE INSERT ON market_master_identities
    BEGIN SELECT RAISE(ABORT, 'forced identity refresh failure'); END;`);

  await assert.rejects(saveMarketImportCore({
    db, batchId: "cache-failure", sourceType: "jd", fileName: "cache.csv",
    fileSizeBytes: 10, fileHash: "cache-failure-hash", sheetName: "CSV",
    rows: [entry({ category: "cache-failure-category" })], warnings: [],
  }), /forced identity refresh failure/);

  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='cache-failure-category'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_sku_gmv_totals").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_identities").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_identity_refresh_keys_v2").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT suppress_identity_revision value FROM market_system_kpi_cache_control WHERE id=1").get() as { value: number }).value, 0);
  assert.equal((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision, 1);
  assert.equal((sqlite.prepare("SELECT status FROM market_import_batches WHERE id='cache-failure'").get() as { status: string }).status, "failed");
  sqlite.close();
});

test("post-commit batch read failure returns the already completed import", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(base);
  let failed = false;
  const db = pausingAdapter(base, async (sql) => {
    if (!failed && sql.includes("FROM market_import_batches WHERE id=? LIMIT 1")) {
      failed = true;
      throw new Error("forced post-commit read failure");
    }
  });

  const result = await saveMarketImportCore({
    db, batchId: "post-commit-read", sourceType: "jd", fileName: "post-commit.csv",
    fileSizeBytes: 10, fileHash: "post-commit-hash", sheetName: "CSV",
    rows: [entry({ category: "post-commit-category" })], warnings: [],
  });

  assert.equal(result.status, "completed");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='post-commit-category'").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT status FROM market_import_batches WHERE id='post-commit-read'").get() as { status: string }).status, "completed");
  sqlite.close();
});

test("lost final batch response reconciles the committed market import", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(base);
  const result = await saveMarketImportCore({
    db: commitThenThrowAdapter(base), batchId: "lost-batch-response", sourceType: "jd", fileName: "lost.csv",
    fileSizeBytes: 10, fileHash: "lost-batch-response-hash", sheetName: "CSV",
    rows: [entry({ category: "lost-response-category" })], warnings: [],
  });
  assert.equal(result.status, "completed");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='lost-response-category'").get() as { count: number }).count, 1);
  sqlite.close();
});

test("lost initial batch-insert response cleans up only its persisted owner token", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let injectLoss = false;
  let lost = false;
  const db = sqliteAdapter(sqlite, {
    afterRun: async (sql) => {
      if (injectLoss && !lost && sql.includes("INSERT OR IGNORE INTO market_import_batches")) {
        lost = true;
        throw new Error("forced lost initial insert response");
      }
    },
  });
  await ensureMarketSchemaCore(db);
  injectLoss = true;

  await assert.rejects(saveMarketImportCore({
    db, batchId: "lost-initial-response", sourceType: "jd", fileName: "lost-initial.csv",
    fileSizeBytes: 10, fileHash: "lost-initial-response-hash", sheetName: "CSV",
    rows: [entry({ category: "lost-initial-category" })], warnings: [],
  }), /forced lost initial insert response/);

  const batch = sqlite.prepare("SELECT status,owner_token owner FROM market_import_batches WHERE id='lost-initial-response'").get() as { status: string; owner: string };
  assert.equal(batch.status, "failed");
  assert.ok(batch.owner);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_staging_rows WHERE batch_id='lost-initial-response'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_range_claims WHERE batch_id='lost-initial-response'").get() as { count: number }).count, 0);
  sqlite.close();
});

test("market publish refreshes only caches affected by the staged SKU", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_sku_gmv_totals (sku_code,gmv_total_cents) VALUES ('SKU-UNRELATED',777);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,sku_code,product_name,gmv_cents,quantity,raw_json,last_import_batch_id)
    VALUES ('unrelated-key',1,'2026-06-01','2026-06-30','unrelated','pop','全部','SKU','POP','SKU-UNRELATED','Unrelated',999,1,'{}','old-batch');`);
  await saveMarketImportCore({
    db, batchId: "incremental-cache", sourceType: "jd", fileName: "incremental.csv",
    fileSizeBytes: 10, fileHash: "incremental-cache-hash", sheetName: "CSV", rows: [entry()], warnings: [],
  });
  assert.equal((sqlite.prepare("SELECT gmv_total_cents gmv FROM market_sku_gmv_totals WHERE sku_code='SKU-UNRELATED'").get() as { gmv: number }).gmv, 777);
  sqlite.close();
});

test("same market month cannot publish concurrently across import batches", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(base);
  let reachedStagingResolve!: () => void;
  let releaseStagingResolve!: () => void;
  const reachedStaging = new Promise<void>((resolve) => { reachedStagingResolve = resolve; });
  const releaseStaging = new Promise<void>((resolve) => { releaseStagingResolve = resolve; });
  let paused = false;
  const firstDb = pausingAdapter(base, async (sql) => {
    if (!paused && sql.includes("INSERT INTO market_import_staging_rows")) {
      paused = true;
      reachedStagingResolve();
      await releaseStaging;
    }
  });
  const firstSave = saveMarketImportCore({
    db: firstDb, batchId: "concurrent-a", sourceType: "jd", fileName: "a.csv",
    fileSizeBytes: 10, fileHash: "concurrent-hash-a", sheetName: "CSV",
    rows: [entry({ category: "concurrent-category", skuCode: "SKU-A" })], warnings: [],
  });
  await reachedStaging;
  await assert.rejects(saveMarketImportCore({
    db: base, batchId: "concurrent-b", sourceType: "jd", fileName: "b.csv",
    fileSizeBytes: 10, fileHash: "concurrent-hash-b", sheetName: "CSV",
    rows: [entry({ category: "concurrent-category", skuCode: "SKU-B" })], warnings: [],
  }), /已有市场分析导入正在发布/);
  releaseStagingResolve();
  const first = await firstSave;

  assert.equal(first.status, "completed");
  assert.deepEqual(sqlite.prepare("SELECT sku_code sku, last_import_batch_id batchId FROM market_ranking_entries WHERE category='concurrent-category'").all().map((row) => ({ ...row })), [
    { sku: "SKU-A", batchId: "concurrent-a" },
  ]);
  assert.equal((sqlite.prepare("SELECT status FROM market_import_batches WHERE id='concurrent-b'").get() as { status: string }).status, "failed");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_staging_rows").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_range_claims").get() as { count: number }).count, 0);
  sqlite.close();
});

test("direct market save rejects invalid or reversed periods before creating a batch", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await assert.rejects(saveMarketImportCore({
    db, batchId: "invalid-date", sourceType: "jd", fileName: "invalid.csv",
    fileSizeBytes: 10, fileHash: "invalid-date-hash", sheetName: "CSV",
    rows: [entry({ periodStart: "2026-02-30", periodEnd: "2026-02-28" })], warnings: [],
  }), /周期无效/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_batches").get() as { count: number }).count, 0);
  sqlite.close();
});

test("direct market save rejects more than 5000 rows before creating a batch", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const rows = Array.from({ length: 5_001 }, (_, index) => entry({ sourceRowNumber: index + 1, skuCode: `SKU-${index + 1}` }));
  await assert.rejects(saveMarketImportCore({
    db, batchId: "too-many", sourceType: "jd", fileName: "too-many.csv",
    fileSizeBytes: 10, fileHash: "too-many-hash", sheetName: "CSV", rows, warnings: [],
  }), /不能超过 5000 行/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_batches").get() as { count: number }).count, 0);
  sqlite.close();
});

test("official price band and market price statistics ignore unconfirmed source, transaction, deposit, and installment prices", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    INSERT INTO market_ranking_entries
      (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES
      ('a',1,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-A','A','A',1000000,10,10,'{}','b'),
      ('b',2,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-B','B','B',2000000,20,20,'{}','b'),
      ('c',3,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-C','C','C',3000000,30,30,'{}','b');
    INSERT INTO market_price_snapshots
      (id, category, sku_code, ranking_dimension, month, source_price_cents, ai_image_price_cents, ai_price_type, average_transaction_price_cents, confirmed_market_price_cents, confirmation_status)
    VALUES
      ('ps-a','净水','SKU-A','SKU','2026-06',199900,159900,'标准售价',100000,NULL,'source_table'),
      ('ps-b','净水','SKU-B','SKU','2026-06',NULL,9900,'定金',100000,NULL,'ai_pending'),
      ('ps-c','净水','SKU-C','SKU','2026-06',NULL,12000,'分期金额',100000,259900,'confirmed');
  `);
  const rows = sqlite.prepare(`
    SELECT m.sku_code sku, ${officialPriceBandSql("ps.confirmed_market_price_cents")} band
    FROM market_ranking_entries m
    JOIN market_price_snapshots ps ON ps.category=m.category AND ps.sku_code=m.sku_code AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
    ORDER BY sku
  `).all() as Array<{ sku: string; band: string }>;
  assert.deepEqual(rows.map((row) => ({ ...row })), [
    { sku: "SKU-A", band: "未确认价格" },
    { sku: "SKU-B", band: "未确认价格" },
    { sku: "SKU-C", band: "未确认价格" },
  ]);
  const official = sqlite.prepare("SELECT COUNT(*) count, AVG(confirmed_market_price_cents) avgPrice FROM market_price_snapshots WHERE confirmed_market_price_cents IS NOT NULL AND confirmation_status='confirmed' AND ai_price_type NOT IN (char(23450,37329), char(20998,26399,37329,39069), char(26080,27861,21028,26029))").get() as { count: number; avgPrice: number | null };
  assert.deepEqual({ ...official }, { count: 0, avgPrice: null });
  sqlite.close();
});

test("official price band SQL is alias-safe and D1-compatible inside the overview CTE", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES
      ('overview-alias',1,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-ALIAS','别名测试商品','测试品牌',1000000,5,10,'{}','batch');
    INSERT INTO market_price_snapshots
      (id, category, scope, sku_code, ranking_dimension, month, ai_price_type, confirmed_market_price_cents, confirmation_status)
    VALUES
      ('ps-overview-alias','净水','pop','SKU-ALIAS','SKU','2026-06','标准售价',159900,'confirmed');
  `);
  const priceBandSql = officialPriceBandSql("snapshot.confirmed_market_price_cents", {
    confirmationStatusSql: "snapshot.confirmation_status",
    aiPriceTypeSql: "snapshot.ai_price_type",
    categorySql: "entry.category",
    periodEndSql: "entry.period_end",
  });
  assert.doesNotMatch(priceBandSql, /ORDER BY[^\n]*entry\.category/);
  const row = sqlite.prepare(`WITH enriched AS (
    SELECT entry.sku_code, ${priceBandSql} AS price_band
    FROM market_ranking_entries entry
    LEFT JOIN market_price_snapshots snapshot
      ON snapshot.category=entry.category AND snapshot.scope=entry.scope
      AND snapshot.sku_code=entry.sku_code AND snapshot.ranking_dimension=entry.ranking_dimension
      AND snapshot.month=substr(entry.period_end,1,7)
  ), filtered AS (SELECT * FROM enriched)
  SELECT sku_code sku, price_band band FROM filtered`).get() as { sku: string; band: string };
  assert.deepEqual({ ...row }, { sku: "SKU-ALIAS", band: "1000-1999" });
  const analytics = readOverviewAnalytics(sqlite, { where: "WHERE m.ranking_dimension=?" }, ["SKU"]);
  assert.deepEqual(JSON.parse(analytics.summary_json), {
    product_count: 1, category_count: 1, brand_count: 1, gmv_cents: 1000000, quantity: 5,
    page_views: 0, visitors: 10, own_product_count: 0, self_operated_gmv_cents: 0, pending_ai_count: 0,
    median_market_price_cents: 159900, weighted_market_price_cents: 159900,
  });
  assert.deepEqual(JSON.parse(analytics.trend_json), [{
    period: "2026-06", gmv_cents: 1000000, quantity: 5, visitors: 10, product_count: 1, brand_count: 1,
    pop_gmv_cents: 1000000, self_gmv_cents: 0, average_transaction_price_cents: 200000,
    weighted_market_price_cents: 159900,
  }]);
  assert.deepEqual(JSON.parse(analytics.price_bands_json), [{ value: "1000-1999", count: 1 }]);
  assert.deepEqual({ start: analytics.date_min, end: analytics.date_max }, { start: "2026-06-01", end: "2026-06-30" });
  const filterOptions = sqlite.prepare(marketOverviewFilterOptionsSql).get() as { categories_json: string; dimensions_json: string };
  assert.deepEqual(JSON.parse(filterOptions.categories_json), [{ value: "净水", count: 1 }]);
  assert.deepEqual(JSON.parse(filterOptions.dimensions_json), [{ value: "SKU", count: 1 }]);
  sqlite.close();
});

test("overview price bands fall back to the imported price-range midpoint", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,price_cents,price_low_cents,price_high_cents,price_estimated,gmv_cents,quantity,raw_json,last_import_batch_id)
    VALUES ('midpoint-fallback',1,'2026-06-01','2026-06-30','净水','POP','SKU','POP','SKU-MID','中位数兜底商品',89900,79900,99900,1,1000000,10,'{}','batch');`);
  const analytics = readOverviewAnalytics(sqlite);
  assert.deepEqual(JSON.parse(analytics.price_bands_json), [{ value: "500-999", count: 1 }]);
  assert.equal(JSON.parse(analytics.price_band_summary_json)[0]?.price_band, "500-999");
  const confirmedOnly = readOverviewAnalytics(sqlite, { confirmedOnlyPriceBands: true });
  assert.deepEqual(JSON.parse(confirmedOnly.price_bands_json), [{ value: "未确认价格", count: 1 }]);
  assert.equal(JSON.parse(confirmedOnly.price_band_summary_json)[0]?.price_band, "未确认价格");
  const adminSource = await readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8");
  assert.match(adminSource, /getMarketPriceBandAnalysisForAi[\s\S]*priceBandBasis: "confirmed_only"/);
  sqlite.close();
});

test("industry report analytics keeps lifecycle, identity, channel, and opportunity cells server-side", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,rank,sku_code,product_name,brand,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES
      ('apr-d',1,'2026-04-01','2026-04-30','商用净饮水设备','整体SKU','SKU','POP','商用直饮机',3,'D','商用直饮机D','丁',25000,2,50,'{}','b'),
      ('jun-a',1,'2026-06-01','2026-06-30','商用净饮水设备','整体SKU','SKU','POP','校园饮水机',1,'A','校园直饮机A','甲',100000,10,100,'{}','b'),
      ('jun-c',2,'2026-06-01','2026-06-30','商用净饮水设备','整体SKU','SKU','自营','工厂饮水机',2,'C','工厂直饮机C','乙',50000,5,100,'{}','b'),
      ('jul-a',3,'2026-07-01','2026-07-31','商用净饮水设备','整体SKU','SKU','POP','校园饮水机',1,'A','校园直饮机A','甲',150000,15,100,'{}','b'),
      ('jul-b',4,'2026-07-01','2026-07-31','商用净饮水设备','整体SKU','SKU','自营','校园饮水机',2,'B','校园直饮机B','丙',75000,5,100,'{}','b');
    INSERT INTO market_price_snapshots
      (id,category,scope,sku_code,ranking_dimension,month,ai_price_type,confirmed_market_price_cents,confirmation_status)
    VALUES
      ('p-ja','商用净饮水设备','整体SKU','A','SKU','2026-06','标准售价',399900,'confirmed'),
      ('p-jc','商用净饮水设备','整体SKU','C','SKU','2026-06','标准售价',699900,'confirmed'),
      ('p-la','商用净饮水设备','整体SKU','A','SKU','2026-07','标准售价',399900,'confirmed'),
      ('p-lb','商用净饮水设备','整体SKU','B','SKU','2026-07','标准售价',499900,'confirmed');
  `);
  const rows = sqlite.prepare(buildMarketOverviewAnalyticsSql()).all() as OverviewAggregateTestRow[];
  const identity = rows.find((row) => row.section === "identity");
  assert.deepEqual([identity?.number_1, identity?.number_2, identity?.number_3], [1, 1, 1]);
  const lifecycle = rows.filter((row) => row.section === "lifecycle").sort((left, right) => left.row_key.localeCompare(right.row_key));
  assert.deepEqual(lifecycle.map((row) => [row.row_key, row.number_1, row.number_2]), [
    ["2026-04", null, null],
    ["2026-06", null, 1],
    ["2026-07", 1, null],
  ]);
  const modes = rows.filter((row) => row.section === "operation_mode");
  assert.deepEqual(new Set(modes.map((row) => row.row_key)), new Set(["POP", "自营"]));
  const campusOpportunity = rows.find((row) => row.section === "opportunity_cell" && row.row_key === "校园饮水机");
  assert.equal(campusOpportunity?.number_8, 225000);
  assert.equal(campusOpportunity?.number_9, 100000);
  assert.ok(rows.some((row) => row.section === "traffic_quadrant"));
  sqlite.close();
});

test("overview analytics applies rank bounds and geometric interpolation between JD daily anchors", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`
    CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,brand,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES
      ('r1',1,'2026-06-01','2026-06-30','净水','pop','全部','SKU','POP',1,'A','A','甲',1000000,100,1000,'{"成交金额":"￥9000~￥1.1万"}','b'),
      ('r2',2,'2026-06-01','2026-06-30','净水','pop','全部','SKU','POP',2,'B','B','乙',600000,60,1000,'{"成交金额":"￥1000~￥9000"}','b'),
      ('r3',3,'2026-06-01','2026-06-30','净水','pop','全部','SKU','POP',3,'C','C','丙',100000,10,1000,'{"成交金额":"￥900~￥1100"}','b');
    INSERT INTO market_netshop_projection
      (projection_revision,projection_key,kind,source,dataset,business_date,sku_id,transaction_amount_cents)
    VALUES
      ('1:aaaaaaaaaaaa','metric:A','metric','jd_sku_daily','sku_daily','2026-06-15','A',1000000),
      ('1:aaaaaaaaaaaa','metric:C','metric','jd_sku_daily','sku_daily','2026-06-15','C',100000);
    UPDATE market_netshop_projection_control
      SET active_revision='1:aaaaaaaaaaaa',active_total=2 WHERE id=1;
  `);
  const analytics = readOverviewAnalytics(sqlite);
  const summary = JSON.parse(analytics.summary_json) as { gmv_cents: number };
  assert.equal(summary.gmv_cents, 1_416_228);
  const brands = JSON.parse(analytics.brand_rows_json) as Array<{ brand: string; gmv_cents: number }>;
  assert.equal(brands.find((row) => row.brand === "乙")?.gmv_cents, 316_228);
  sqlite.close();
});

test("brand share denominator uses all brands and display limiting happens after CR calculations", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const insert = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, rank, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES (?, ?, '2026-06-01', '2026-06-30', '净水', 'pop', 'SKU', 'POP', ?, ?, ?, ?, ?, 1, 1, '{}', 'batch')`);
  for (let index = 1; index <= 31; index += 1) {
    const brand = `品牌${index.toString().padStart(2, "0")}`;
    const gmv = index === 31 ? 7000 : 1000;
    insert.run(`key-${index}`, index, index, `SKU-${index}`, `商品${index}`, brand, gmv);
  }
  const allBrands = sqlite.prepare("SELECT brand, SUM(gmv_cents) gmv FROM market_ranking_entries WHERE brand<>'' GROUP BY brand ORDER BY gmv DESC").all() as Array<{ brand: string; gmv: number }>;
  const brandTotal = allBrands.reduce((sum, row) => sum + row.gmv, 0);
  const displayed = allBrands.slice(0, 30);
  const cr3 = Math.round(allBrands.slice(0, 3).reduce((sum, row) => sum + row.gmv, 0) / brandTotal * 10000);
  assert.equal(brandTotal, 37000);
  assert.equal(displayed.length, 30);
  assert.equal(Math.round(displayed[0]!.gmv / brandTotal * 10000), 1892);
  assert.equal(cr3, 2432);
  sqlite.close();
});

test("market ranking limits candidates before previous-rank lookup and pins the selective index", async () => {
  const database = await readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8");
  const rankingSql = buildMarketRankingCtes();
  const topRanked = rankingSql.indexOf("top_ranked AS MATERIALIZED");
  const boundedIds = rankingSql.lastIndexOf("LIMIT 200", topRanked);
  assert.ok(boundedIds >= 0 && topRanked > boundedIds);
  assert.ok(database.indexOf("buildMarketRankingCtes") < database.indexOf("previous_rank"));
  assert.match(database, /market_ranking_entries p INDEXED BY market_entries_sku_idx/);
});

test("market ranking reuses only a ready image from the same exact product identity", async () => {
  const sqlite = new DatabaseSync(":memory:");
  await ensureMarketSchemaCore(sqliteAdapter(sqlite));
  sqlite.exec(`
    CREATE TABLE netshop_rows (sku_id TEXT, spu_id TEXT, product_code TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,rank,sku_code,product_name,image_url,raw_json,last_import_batch_id)
    VALUES
      ('historical',1,'2026-07-01','2026-07-31','净水','POP','SKU','POP',1,'SKU-IMAGE','旧榜单','https://img10.360buyimg.com/n5/same.jpg','{}','old'),
      ('foreign-scope',2,'2026-08-01','2026-08-11','净水','自营','SKU','自营',1,'SKU-IMAGE','其他范围','https://img10.360buyimg.com/n5/foreign.jpg','{}','foreign'),
      ('current',3,'2026-08-12','2026-08-12','净水','POP','SKU','POP',1,'SKU-IMAGE','今日榜单','','{}','current');
    INSERT INTO market_image_cache (source_url,status,content_sha256,object_key)
    VALUES
      ('https://img10.360buyimg.com/n5/same.jpg','ready','same-hash','market-images/same.jpg'),
      ('https://img10.360buyimg.com/n5/foreign.jpg','ready','foreign-hash','market-images/foreign.jpg');
    INSERT INTO market_effective_metrics_cache
      (market_entry_id,effective_gmv_cents,real_gmv_cents,gmv_out_of_band,effective_quantity,effective_average_transaction_price_cents,effective_conversion_bps)
    SELECT id,1000,NULL,0,1,1000,100 FROM market_ranking_entries WHERE natural_key='current';
  `);
  const sql = `${buildMarketRankingCtes({ factWhere: "WHERE m.natural_key='current'" })}
    SELECT resolved_image_url imageUrl,image_cache_status_raw cacheStatus,image_content_sha256 hash FROM top_ranked`;
  // D1 rejects outer-query references inside a scalar subquery's ORDER BY even though local SQLite accepts them.
  assert.doesNotMatch(sql, /ORDER BY CASE WHEN historical\.id=m\.id/);
  assert.deepEqual({ ...(sqlite.prepare(sql).get() as Record<string, unknown>) }, {
    imageUrl: "https://img10.360buyimg.com/n5/same.jpg",
    cacheStatus: "ready",
    hash: "same-hash",
  });
  sqlite.close();
});

test("shared market month coverage prefers a full month, deduplicates daily rows, and excludes rolling windows", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,gmv_cents,quantity,page_views,visitors,raw_json,last_import_batch_id)
    VALUES
      ('month',1,'2026-06-01','2026-06-30','净水','POP','全部','SKU','POP',1,'SKU-M','整月',1000,10,100,20,'{}','b'),
      ('june-day',2,'2026-06-01','2026-06-01','净水','POP','全部','SKU','POP',1,'SKU-M','日',100,1,10,2,'{}','b'),
      ('june-day-child',3,'2026-06-01','2026-06-01','净水','POP','0-500','SKU','POP',1,'SKU-M','日价格带重复',9999,99,99,99,'{}','b'),
      ('july-day-1',4,'2026-07-01','2026-07-01','净水','POP','全部','SKU','POP',1,'SKU-M','日1',10,1,5,2,'{}','b'),
      ('july-day-2',5,'2026-07-02','2026-07-02','净水','POP','全部','SKU','POP',1,'SKU-M','日2',20,2,6,3,'{}','b'),
      ('july-child',6,'2026-07-03','2026-07-03','净水','POP','0-500','SKU','POP',1,'SKU-M','月内子价格带',9999,99,99,99,'{}','b'),
      ('july-rolling',7,'2026-07-02','2026-07-31','净水','POP','全部','SKU','POP',1,'SKU-M','滚动',999999,999,999,999,'{}','b');`);
  const rows = sqlite.prepare(`WITH ${marketEffectiveFactsCtes()}, trend_source AS MATERIALIZED (
      SELECT * FROM market_effective_rows WHERE sku_code='SKU-M'
    ), ${marketMonthlyCoverageCtes({ source: "trend_source" })}
    SELECT coverage_month month, monthly_gmv_cents gmv, monthly_quantity quantity,
      monthly_page_views page_views, monthly_visitors visitors, coverage_days days, source_priority priority
    FROM market_monthly_rows ORDER BY coverage_month`).all() as Array<Record<string, number | string>>;
  assert.deepEqual(rows.map((row) => ({ ...row })), [
    { month: "2026-06", gmv: 1000, quantity: 10, page_views: 100, visitors: 20, days: 30, priority: 0 },
    { month: "2026-07", gmv: 30, quantity: 3, page_views: 11, visitors: 5, days: 2, priority: 1 },
  ]);
  const analytics = readOverviewAnalytics(sqlite);
  assert.equal(JSON.parse(analytics.summary_json).gmv_cents, 1030);
  assert.deepEqual((JSON.parse(analytics.trend_json) as Array<{ period: string; gmv_cents: number }>).map(({ period, gmv_cents }) => ({ period, gmv_cents })), [
    { period: "2026-06", gmv_cents: 1000 },
    { period: "2026-07", gmv_cents: 30 },
  ]);
  sqlite.close();
});

test("daily-only overview data range uses the actual covered days", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,price_cents,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES
      ('range-day-1',1,'2026-07-05','2026-07-05','range-category','POP','','SKU','POP',1,'SKU-RANGE','Range',129900,100,1,1,'{}','b'),
      ('range-day-2',2,'2026-07-20','2026-07-20','range-category','POP','','SKU','POP',1,'SKU-RANGE','Range',129900,200,2,2,'{}','b');`);

  const analytics = readOverviewAnalytics(sqlite);
  assert.equal(JSON.parse(analytics.summary_json).gmv_cents, 300);
  assert.deepEqual({ start: analytics.date_min, end: analytics.date_max }, {
    start: "2026-07-05",
    end: "2026-07-20",
  });
  sqlite.close();
});

test("daily price-band filtering follows the representative month after coverage aggregation", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,brand,price_cents,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES
      ('band-day-low',1,'2026-07-05','2026-07-05','band-category','POP','','SKU','POP',1,'SKU-BAND','Band','Band Brand',89900,100,1,1,'{}','b'),
      ('band-day-high',2,'2026-07-20','2026-07-20','band-category','POP','','SKU','POP',1,'SKU-BAND','Band','Band Brand',129900,200,2,2,'{}','b');`);

  const unfiltered = readOverviewAnalytics(sqlite);
  const selected = readOverviewAnalytics(sqlite, {
    factWhere: "WHERE m.category=?",
    where: "WHERE m.brand=?",
    priceBandWhere: "WHERE price_band IN (?3)",
  }, ["band-category", "Band Brand"], ["1000-1999"]);
  assert.deepEqual(JSON.parse(unfiltered.price_band_summary_json).map((row: { price_band: string; gmv_cents: number }) => ({
    priceBand: row.price_band,
    gmvCents: row.gmv_cents,
  })), [{ priceBand: "1000-1999", gmvCents: 300 }]);
  assert.equal(JSON.parse(unfiltered.summary_json).gmv_cents, 300);
  assert.equal(JSON.parse(selected.summary_json).gmv_cents, 300);
  assert.equal(JSON.parse(selected.price_band_summary_json)[0]?.price_band, "1000-1999");
  sqlite.close();
});

test("monthly trend boundaries keep the newest 120 item months and newest 60 overview months", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    CREATE TABLE sales_order_lines (product_code TEXT, allocated_amount_cents INTEGER, sales_time TEXT, ship_time TEXT);`);
  const insert = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,brand,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES (?, ?, ?, ?, '净水', 'POP', '全部', 'SKU', 'POP', 1, 'SKU-LONG', '长期商品', '品牌', ?, 1, 1, '{}', 'b')`);
  const months: string[] = [];
  for (let index = 0; index < 121; index += 1) {
    const first = new Date(Date.UTC(2016, index, 1));
    const last = new Date(Date.UTC(2016, index + 1, 0));
    const periodStart = first.toISOString().slice(0, 10);
    const periodEnd = last.toISOString().slice(0, 10);
    months.push(periodEnd.slice(0, 7));
    insert.run(`long-${index}`, index + 1, periodStart, periodEnd, index + 1);
  }
  const itemMonths = sqlite.prepare(`WITH ${marketEffectiveFactsCtes()}, trend_source AS MATERIALIZED (
      SELECT * FROM market_effective_rows WHERE sku_code='SKU-LONG'
    ), ${marketMonthlyCoverageCtes({ source: "trend_source" })}, recent AS MATERIALIZED (
      SELECT coverage_month, COUNT(*) OVER () total_months FROM market_monthly_rows ORDER BY coverage_month DESC LIMIT 120
    ) SELECT coverage_month month, total_months FROM recent ORDER BY coverage_month ASC`).all() as Array<{ month: string; total_months: number }>;
  assert.equal(itemMonths.length, 120);
  assert.equal(itemMonths[0]!.total_months, 121);
  assert.equal(itemMonths[0]!.month, months[1]);
  assert.equal(itemMonths.at(-1)!.month, months.at(-1));

  const analytics = readOverviewAnalytics(sqlite);
  const overviewMonths = (JSON.parse(analytics.trend_json) as Array<{ period: string }>).map((row) => row.period);
  assert.equal(analytics.trend_total, 121);
  assert.equal(overviewMonths.length, 60);
  assert.equal(overviewMonths[0], months[61]);
  assert.equal(overviewMonths.at(-1), months.at(-1));
  assert.equal(analytics.date_min, "2016-01-01");
  assert.equal(analytics.date_max, new Date(Date.UTC(2016, 121, 0)).toISOString().slice(0, 10));
  sqlite.close();
});

test("item trend exposes the 120-month boundary and the UI reports the total month count", async () => {
  const [database, overviewSql, view] = await Promise.all([
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/overview-sql.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(overviewSql, /COUNT\(\*\) OVER \(\) total_months[\s\S]*LIMIT 120/);
  assert.match(database, /totalMonths,\s*truncated: totalMonths > trendRows\.length/);
  assert.match(view, /totalMonths: number; truncated: boolean/);
  assert.match(view, /展示最近 \$\{count\(data\.items\.length\)\} \/ 共 \$\{count\(data\.totalMonths\)\} 个月/);
  assert.doesNotMatch(view, /读取全量月度趋势/);
});

test("market trend identity is exact in UI, API, service, and the central AI schema", async () => {
  const [view, route, overviewSql, registry, aiTools] = await Promise.all([
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/trend/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/overview-sql.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/ai-tools.ts", import.meta.url), "utf8"),
  ]);
  assert.match(view, /scope: item\.scope/);
  assert.match(route, /scope: params\.get\("scope"\)/);
  assert.match(overviewSql, /m\.sku_code=\? AND m\.category=\? AND m\.scope=\? AND m\.ranking_dimension=\?/);
  const trendTool = registry.slice(registry.indexOf('name: "get_market_sku_trend"'), registry.indexOf('name: "get_market_brand_analysis"'));
  assert.match(trendTool, /scope: \{ type: "string", minLength: 1, maxLength: 120 \}/);
  assert.match(trendTool, /required: \["skuCode", "category", "scope", "rankingDimension"\]/);
  assert.match(aiTools, /scope = stringArg\(args\.scope, "scope", 120\)/);
  assert.match(aiTools, /path: MARKET_CONSUMER_QUERY_PATH/);
  assert.match(aiTools, /operation: "sku_trend"[\s\S]*category,[\s\S]*scope,[\s\S]*rankingDimension/);
});
