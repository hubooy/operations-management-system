import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { buildMarketOverviewAnalyticsSql, buildMarketOverviewEnrichedSql } from "../lib/market/overview-sql";
import { ensureAnnotationSchema } from "../lib/market/annotation-schema";
import {
  ensureMarketSchemaCore,
  marketBaseSchemaStatements,
  marketNetshopProjectionStatements,
  marketSystemKpiCacheControlTableStatement,
  marketSystemKpiCacheTriggerStatements,
  marketSystemKpiCacheTriggerDropStatements,
} from "../lib/market/schema-core";
import { refreshMarketMasterIdentities } from "../lib/market/master-identity";
import {
  canonicalMarketOverviewCacheIdentity,
  ensureMarketSystemKpiCacheSchema,
  getMarketOverviewCacheRevision,
  getCachedMarketFilterOptions as getCachedMarketFilterOptionsRaw,
  getCachedMarketOverview as getCachedMarketOverviewRaw,
  getCachedMarketSystemKpis as getCachedMarketSystemKpisRaw,
  type MarketOverviewResponseCacheDatabase,
} from "../lib/market/overview-response-cache";
import {
  validateMarketFilterOptionsCachePayload,
  validateMarketOverviewCachePayload,
  validateMarketSystemKpiCachePayload,
} from "../lib/market/cache-payload-validators";
import { PublicApiError } from "../lib/http/api-error";
import { prefetchMarketRankingOverview, requestMarketOverview } from "../app/market-view";

class AsyncSqliteStatement {
  constructor(
    private readonly statement: ReturnType<DatabaseSync["prepare"]>,
    private readonly values: SQLInputValue[] = [],
  ) {}

  bind(...values: unknown[]) {
    return new AsyncSqliteStatement(this.statement, values as SQLInputValue[]);
  }

  async first<T>() {
    return (this.statement.get(...this.values) as T | undefined) ?? null;
  }

  async all<T>() {
    return { results: this.statement.all(...this.values) as T[] };
  }

  async run() {
    return this.statement.run(...this.values);
  }
}

function asyncDatabase(sqlite: DatabaseSync) {
  return {
    prepare: (sql: string) => new AsyncSqliteStatement(sqlite.prepare(sql)),
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

const objectPayload = (value: unknown) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
type OverviewCacheIdentity = Parameters<typeof canonicalMarketOverviewCacheIdentity>[0];

function getCachedMarketOverview<T>(
  db: MarketOverviewResponseCacheDatabase,
  identity: OverviewCacheIdentity,
  load: () => Promise<T>,
  validate: (value: unknown) => boolean = objectPayload,
) {
  return getCachedMarketOverviewRaw(db, identity, load, validate);
}

function getCachedMarketFilterOptions<T>(
  db: MarketOverviewResponseCacheDatabase,
  load: () => Promise<T>,
  validate: (value: unknown) => boolean = objectPayload,
) {
  return getCachedMarketFilterOptionsRaw(db, load, validate);
}

function getCachedMarketSystemKpis<T>(
  db: MarketOverviewResponseCacheDatabase,
  load: () => Promise<T>,
  validate: (value: unknown) => boolean = objectPayload,
) {
  return getCachedMarketSystemKpisRaw(db, load, validate);
}

function installMarketCacheRevisionDependencyTables(sqlite: DatabaseSync) {
  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS market_ranking_entries (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_price_snapshots (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_image_cache (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_annotation_items (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_subcategory_taxonomy (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_master_identities (id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_import_batches (id INTEGER PRIMARY KEY);
  `);
}

function createMarketResponseCacheFixture() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_monthly_summary_cache_state (id INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL);
    INSERT INTO market_monthly_summary_cache_state VALUES (1, 7);
    CREATE TABLE market_image_cache (
      id INTEGER PRIMARY KEY, source_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
      attempt_count INTEGER NOT NULL, content_sha256 TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
    );
    CREATE TABLE market_import_batches (
      id TEXT NOT NULL, status TEXT NOT NULL, file_name TEXT NOT NULL,
      file_size_bytes INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, completed_at TEXT, row_count INTEGER NOT NULL,
      inserted_count INTEGER NOT NULL, updated_count INTEGER NOT NULL, warning_count INTEGER NOT NULL
    );
    CREATE TABLE market_subcategory_taxonomy (
      id INTEGER PRIMARY KEY, subcategory TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, updated_at TEXT NOT NULL
    );
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  for (const statement of marketBaseSchemaStatements.filter((sql) => sql.includes("market_overview_response_cache"))) {
    sqlite.exec(statement);
  }
  return { sqlite, db: asyncDatabase(sqlite) };
}

function validFilterOptionsPayload() {
  const emptyOptions = "[]";
  return {
    categories_json: emptyOptions,
    scopes_json: emptyOptions,
    brands_json: emptyOptions,
    dimensions_json: emptyOptions,
    modes_json: emptyOptions,
    subcategories_json: emptyOptions,
  };
}

function validSystemKpiPayload(seed = 0) {
  return {
    marketIdentityTotal: seed,
    pendingPriceCount: 0,
    pendingAiCount: 0,
    completedAiCount: 0,
    sameImageReuseCount: 0,
    priceOnlyRecognitionCount: 0,
    fullRecognitionCount: 0,
    blockedRecognitionCount: 0,
  };
}

function validMarketOverviewPayload(view: "ranking" | "full" = "ranking", seed = 0) {
  return {
    view,
    salesRevision: "sales:test:1",
    summary: {
      productCount: seed, categoryCount: 0, brandCount: 0, gmvCents: 0, quantity: 0,
      pageViews: 0, visitors: 0, ownProductCount: 0, activeSkuCount: 0, pendingAiCount: 0,
      selfOperatedGmvCents: 0, selfOperatedShareBps: null, medianMarketPriceCents: null,
      weightedMarketPriceCents: null, averageTransactionPriceCents: null,
    },
    items: [],
    pagination: { page: 1, pageSize: 20, total: 0, pageCount: 1 },
    trend: [],
    trendTotal: 0,
    trendTruncated: false,
    priceBands: [],
    priceBandSummary: [],
    priceBandTrend: [],
    brandAnalysis: { items: [], cr3Bps: 0, cr5Bps: 0, concentration: "低" },
    subcategorySummary: [],
    industryReport: {
      definition: {
        title: "测试", metricScope: "当前 TOP 榜单覆盖市场",
        profile: { category: "测试", coreSubcategories: [], adjacentSubcategories: [], adjacentCategories: [] },
        selectedCategories: [], selectedScopes: [], selectedRankingDimensions: [],
      },
      period: {
        coverageMonths: 0, latestPeriod: null, latestGmvCents: 0, monthOverMonthBps: null,
        yearOverYearBps: null, peak: null, trough: null, latestEntryCount: null,
        latestExitCount: null, latestExitPeriod: null,
      },
      lifecycle: [],
      operationModes: [],
      brandConcentrationTrend: [],
      trafficQuadrants: [],
      productSignals: { sampleSize: 0, source: "测试", signals: [] },
      opportunities: [],
      dataQuality: {
        categoryCount: 0, scopeCount: 0, rankingDimensionCount: 0, operationModeCount: 0,
        unknownBrandSkuCount: 0, unclassifiedSkuCount: 0, pendingPriceSkuCount: 0,
        identityReady: false, coverageReady: false, comparisonReady: false, warnings: [],
      },
      externalDataGaps: [],
    },
    filters: {
      categories: [], scopes: [], brands: [], rankingDimensions: [], operationModes: [],
      subcategories: [], priceBands: [],
    },
    dataRange: { startDate: null, endDate: null },
    batches: [],
    imageCache: { total: 0, cached: 0, failed: 0, pending: 0 },
  };
}

function installDeferredMarketOverviewFetch() {
  const originalFetch = globalThis.fetch;
  let completeResponse: ((payload: unknown) => void) | undefined;
  let resolveStarted: ((signal: AbortSignal) => void) | undefined;
  let fetchCount = 0;
  const started = new Promise<AbortSignal>((resolve) => { resolveStarted = resolve; });
  globalThis.fetch = (async (_input, init) => {
    fetchCount += 1;
    const signal = init?.signal;
    assert.ok(signal, "shared market request must own an abort signal");
    resolveStarted?.(signal);
    return await new Promise<Response>((resolve, reject) => {
      completeResponse = (payload) => resolve(new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      }));
      signal.addEventListener("abort", () => {
        const error = new Error("internal request aborted");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
    });
  }) as typeof fetch;
  return {
    started,
    complete(payload: unknown) {
      assert.ok(completeResponse, "fetch must start before it can complete");
      completeResponse(payload);
    },
    fetchCount: () => fetchCount,
    restore: () => { globalThis.fetch = originalFetch; },
  };
}

test("market overview can read precomputed effective metrics without rebuilding anchor windows", () => {
  const enriched = buildMarketOverviewEnrichedSql({ useEffectiveMetricsCache: true });
  const analytics = buildMarketOverviewAnalyticsSql({ useEffectiveMetricsCache: true });
  for (const sql of [enriched, analytics]) {
    assert.match(sql, /JOIN market_effective_metrics_cache cached ON cached\.market_entry_id=m\.id/);
    assert.doesNotMatch(sql, /anchor_segments AS MATERIALIZED/);
    assert.doesNotMatch(sql, /parsed_ranges AS MATERIALIZED/);
  }
});

test("market schema and migration persist cache rows and their source revision", async () => {
  const schema = marketBaseSchemaStatements.join("\n");
  const migration = await readFile(new URL("../drizzle/0046_market_effective_metrics_cache.sql", import.meta.url), "utf8");
  for (const sql of [schema, migration]) {
    assert.match(sql, /market_effective_metrics_cache/);
    assert.match(sql, /market_effective_metrics_cache_state/);
    assert.match(sql, /netshop_updated_at/);
  }
});

test("effective-metrics refresh updates the cache without deleting every persisted row first", async () => {
  const database = await readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8");
  const refresh = database.slice(
    database.indexOf("async function refreshEffectiveMetricsCache"),
    database.indexOf("export function ensureMarketEffectiveMetricsCache"),
  );
  assert.match(refresh, /ON CONFLICT\(market_entry_id\) DO UPDATE SET/);
  assert.match(refresh, /IS NOT excluded\.effective_gmv_cents/);
  assert.match(refresh, /DELETE FROM market_effective_metrics_cache[\s\S]*price_band_preference=1/);
  assert.doesNotMatch(refresh, /prepare\("DELETE FROM market_effective_metrics_cache"\)/);
  assert.match(refresh, /SELECT DISTINCT category FROM market_ranking_entries ORDER BY category/);
  assert.match(refresh, /marketEffectiveFactsCtes\("WHERE m\.category=\?"\)/);
  assert.match(refresh, /MARKET_EFFECTIVE_METRICS_SOURCE_CHANGED/);
  assert.match(database, /const marketEffectiveMetricsNetshopRevisionSql = `SELECT active_total row_count, active_revision updated_at\s+FROM market_netshop_projection_control WHERE id=1`/);
  assert.equal(
    (refresh.match(/db\.prepare\(marketEffectiveMetricsNetshopRevisionSql\)/g) ?? []).length,
    2,
    "both the opening and closing fences must read the constant-time active projection receipt",
  );
  assert.doesNotMatch(refresh, /FROM netshop_rows/);
  const upsertPosition = refresh.indexOf("ON CONFLICT(market_entry_id)");
  assert.ok(
    upsertPosition >= 0
      && upsertPosition < refresh.indexOf("market_effective_metrics_cache_state", upsertPosition),
    "the source revision must only be published after the cache rows are refreshed",
  );
});

test("market and netshop mutations invalidate the persisted effective-metrics revision", async () => {
  const migration = await readFile(new URL("../drizzle/0046_market_effective_metrics_cache.sql", import.meta.url), "utf8");
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY)");
  sqlite.exec("CREATE TABLE netshop_rows (id INTEGER PRIMARY KEY)");
  for (const statement of migration.split("--> statement-breakpoint").map((item) => item.trim()).filter(Boolean)) {
    sqlite.exec(statement);
  }
  const restoreState = () => sqlite.prepare(`INSERT OR REPLACE INTO market_effective_metrics_cache_state
    (id, market_row_count, market_updated_at, netshop_row_count, netshop_updated_at)
    VALUES (1, 0, '', 0, '')`).run();
  restoreState();
  sqlite.exec("INSERT INTO market_ranking_entries (id) VALUES (1)");
  assert.equal(sqlite.prepare("SELECT id FROM market_effective_metrics_cache_state WHERE id=1").get(), undefined);
  restoreState();
  sqlite.exec("INSERT INTO netshop_rows (id) VALUES (1)");
  assert.equal(sqlite.prepare("SELECT id FROM market_effective_metrics_cache_state WHERE id=1").get(), undefined);
  sqlite.close();
});

test("effective-metrics netshop invalidation is scoped identically in runtime and migration", async () => {
  const [baseMigration, projectionMigration, databaseSource] = await Promise.all([
    readFile(new URL("../drizzle/0046_market_effective_metrics_cache.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0095_market_netshop_projection.sql", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
  ]);
  const splitMigration = (source: string) => source
    .split("--> statement-breakpoint")
    .map((statement) => statement.trim())
    .filter(Boolean);
  const extractRuntimeStatements = (constantName: string) => {
    const marker = `const ${constantName} = [`;
    const start = databaseSource.indexOf(marker);
    assert.ok(start >= 0, `${constantName} must exist`);
    const end = databaseSource.indexOf("];", start);
    assert.ok(end > start, `${constantName} must be a bounded SQL array`);
    return [...databaseSource.slice(start, end).matchAll(/`([\s\S]*?)`/g)]
      .map((match) => match[1]!.trim());
  };
  const runtimeDrops = extractRuntimeStatements("marketEffectiveMetricsNetshopTriggerDropStatements");
  const runtimeCreates = extractRuntimeStatements("marketEffectiveMetricsNetshopTriggerStatements");
  assert.equal(runtimeDrops.length, 4);
  assert.equal(runtimeCreates.length, 1);
  const runtimeSetup = databaseSource.slice(
    databaseSource.indexOf("function ensureEffectiveMetricsInvalidationTriggers"),
    databaseSource.indexOf("function sameEffectiveMetricsRevision"),
  );
  assert.match(runtimeSetup, /marketEffectiveMetricsNetshopTriggerDropStatements\.map\(\(statement\) => db\.prepare\(statement\)\)/);
  assert.match(runtimeSetup, /marketEffectiveMetricsNetshopTriggerStatements\.map\(\(statement\) => db\.prepare\(statement\)\)/);

  const createFixture = () => {
    const sqlite = new DatabaseSync(":memory:");
    sqlite.exec(`
      CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY);
      CREATE TABLE netshop_rows (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        dataset TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE market_monthly_summary_cache_state (
        id INTEGER PRIMARY KEY,
        source_revision INTEGER NOT NULL,
        status TEXT NOT NULL
      );
      INSERT INTO market_monthly_summary_cache_state VALUES (1,1,'ready');
      CREATE TABLE market_monthly_summary_dirty_scopes (
        category TEXT PRIMARY KEY,
        dirty_revision INTEGER NOT NULL
      );
    `);
    for (const statement of splitMigration(baseMigration)) sqlite.exec(statement);
    return sqlite;
  };
  const restoreState = (sqlite: DatabaseSync) => sqlite.prepare(`INSERT OR REPLACE INTO market_effective_metrics_cache_state
    (id, market_row_count, market_updated_at, netshop_row_count, netshop_updated_at)
    VALUES (1, 0, '', 0, '')`).run();
  const hasState = (sqlite: DatabaseSync) => Boolean(
    sqlite.prepare("SELECT id FROM market_effective_metrics_cache_state WHERE id=1").get(),
  );
  const assertProjectionBehavior = (sqlite: DatabaseSync, surface: string) => {
    restoreState(sqlite);
    sqlite.exec("INSERT INTO netshop_rows (id,source,dataset) VALUES (1,'tmall_product_daily','spu_daily')");
    assert.equal(hasState(sqlite), true, `${surface}: retired D1 fact insert must preserve the cache`);
    sqlite.exec("UPDATE netshop_rows SET source='jd_sku_daily',dataset='sku_daily' WHERE id=1");
    assert.equal(hasState(sqlite), true, `${surface}: retired D1 fact update must preserve the cache`);
    sqlite.exec("DELETE FROM netshop_rows WHERE id=1");
    assert.equal(hasState(sqlite), true, `${surface}: retired D1 fact delete must preserve the cache`);

    sqlite.exec(`INSERT INTO market_netshop_projection
      (projection_revision,projection_key,kind,source,dataset,business_date,sku_id,transaction_amount_cents)
      VALUES ('1:aaaaaaaaaaaa','metric:SKU-1','metric','jd_sku_daily','sku_daily','2026-08-25','SKU-1',100)`);
    assert.equal(hasState(sqlite), true, `${surface}: staging must preserve the active cache`);
    sqlite.exec(`UPDATE market_netshop_projection_control
      SET active_revision='1:aaaaaaaaaaaa',active_total=1 WHERE id=1`);
    assert.equal(hasState(sqlite), false, `${surface}: activation must invalidate`);
    restoreState(sqlite);
    sqlite.exec("UPDATE market_netshop_projection_control SET active_revision=active_revision WHERE id=1");
    assert.equal(hasState(sqlite), true, `${surface}: an unchanged revision must not invalidate`);
    sqlite.exec("UPDATE market_netshop_projection_control SET active_revision='2:bbbbbbbbbbbb',active_total=0 WHERE id=1");
    assert.equal(hasState(sqlite), false, `${surface}: every successor activation must invalidate`);
  };
  const triggerDefinitions = (sqlite: DatabaseSync) => sqlite.prepare(`SELECT name,sql FROM sqlite_master
    WHERE type='trigger' AND name LIKE 'market_effective_cache_netshop_%' ORDER BY name`).all()
    .map((row) => ({
      name: String(row.name),
      sql: String(row.sql).replace(/`/g, "").replace(/\s+/g, " ").trim(),
    }));

  const runtimeSqlite = createFixture();
  for (const statement of marketNetshopProjectionStatements) runtimeSqlite.exec(statement);
  for (const statement of [...runtimeDrops, ...runtimeCreates]) runtimeSqlite.exec(statement);
  assertProjectionBehavior(runtimeSqlite, "runtime");
  const runtimeDefinitions = triggerDefinitions(runtimeSqlite);

  const migrationSqlite = createFixture();
  restoreState(migrationSqlite);
  for (const statement of splitMigration(projectionMigration)) migrationSqlite.exec(statement);
  assert.equal(hasState(migrationSqlite), false, "the forward migration must fail closed and force one rebuild");
  assertProjectionBehavior(migrationSqlite, "migration");
  const migrationDefinitions = triggerDefinitions(migrationSqlite);

  assert.deepEqual(migrationDefinitions, runtimeDefinitions);
  assert.match(projectionMigration, /AFTER UPDATE OF `active_revision` ON `market_netshop_projection_control`/);
  assert.match(projectionMigration, /deliberately absent from the normal Drizzle\s+-- journal/);
  runtimeSqlite.close();
  migrationSqlite.close();
});

test("market UI requests lightweight ranking data and aborts superseded requests", async () => {
  const [view, route, database] = await Promise.all([
    readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
  ]);
  assert.match(view, /requestedView = activeSection === "overview" \? "full" : "ranking"/);
  assert.match(view, /controller\.abort\(\)/);
  assert.match(view, /signal: controller\.signal|load\(controller\.signal\)/);
  assert.match(route, /parseMarketOverviewQuery\([\s\S]*new URL\(request\.url\)\.searchParams/);
  assert.match(route, /requestDjangoMarketService/);
  assert.doesNotMatch(route, /getCachedMarketOverview|getD1Database|ensureMarketSchema/);
  assert.match(view, /prefetchMarketRankingOverview/);
  assert.match(view, /requestMarketOverview\(requestKey, signal, MARKET_OVERVIEW_RECENT_PREFETCH_MS\)/);
  assert.match(view, /requestMarketOverview\(params\.toString\(\), signal, maximumCacheAgeMs\)/);
  assert.match(view, /marketOverviewRequests\.get\(requestKey\)/);
  assert.match(view, /signal: controller\.signal/);
  assert.match(view, /request\.subscribers === 0 && !request\.settled/);
  assert.match(view, /rememberMarketOverview\(requestKey, payload\)/);
  assert.match(view, /const delay = isInitialLoad \? 0 : 350/);
  assert.match(view, /isInitialLoad \? MARKET_OVERVIEW_RECENT_PREFETCH_MS : 0/);
  assert.match(view, /MARKET_RANKING_PAGE_SIZE = 20/);
  assert.match(view, /params\.set\("page", String\(page\)\)/);
  assert.match(view, /params\.set\("pageSize", String\(MARKET_RANKING_PAGE_SIZE\)\)/);
  assert.match(view, /上一页/);
  assert.match(view, /下一页/);
  assert.doesNotMatch(view, /items: \[\.\.\.current\.items/);
  assert.match(view, /loadMoreController\.current\?\.abort\(\)/);
  assert.match(route, /page: pagination\.page/);
  assert.match(route, /pageSize: pagination\.pageSize/);
  assert.match(database, /rankingOffset: \(rankingPage - 1\) \* rankingPageSize/);
  assert.match(database, /COUNT\(\*\) item_count/);
  assert.match(database, /pagination: \{/);
  assert.match(database, /await ensureMarketEffectiveMetricsCache\(db\)/);
  assert.match(database, /getCachedMarketFilterOptions/);
  assert.match(database, /WITH sources AS MATERIALIZED[\s\S]*SELECT DISTINCT image_url source_url/);
  assert.doesNotMatch(database, /COUNT\(DISTINCT CASE WHEN mic\.status='ready'/);
});

test("aborting the prefetch subscriber does not cancel a joined page load", async (context) => {
  const deferred = installDeferredMarketOverviewFetch();
  context.after(deferred.restore);
  const startDate = "2026-08-03";
  const endDate = "2026-08-04";
  const requestKey = `view=ranking&includeFilterOptions=false&page=1&pageSize=20&dimension=SKU&startDate=${startDate}&endDate=${endDate}`;
  const prefetchController = new AbortController();
  const pageController = new AbortController();
  const prefetch = prefetchMarketRankingOverview(startDate, endDate, prefetchController.signal);
  const internalSignal = await deferred.started;
  const page = requestMarketOverview(requestKey, pageController.signal);
  const cancelledPrefetch = assert.rejects(prefetch, { name: "AbortError" });
  prefetchController.abort();
  await cancelledPrefetch;
  assert.equal(internalSignal.aborted, false);
  deferred.complete({ marker: "page-success" });
  assert.equal(((await page) as unknown as { marker: string }).marker, "page-success");
  assert.equal(deferred.fetchCount(), 1);
});

test("aborting the page subscriber does not cancel a joined prefetch", async (context) => {
  const deferred = installDeferredMarketOverviewFetch();
  context.after(deferred.restore);
  const startDate = "2026-08-05";
  const endDate = "2026-08-06";
  const requestKey = `view=ranking&includeFilterOptions=false&page=1&pageSize=20&dimension=SKU&startDate=${startDate}&endDate=${endDate}`;
  const pageController = new AbortController();
  const prefetchController = new AbortController();
  const page = requestMarketOverview(requestKey, pageController.signal);
  const internalSignal = await deferred.started;
  const prefetch = prefetchMarketRankingOverview(startDate, endDate, prefetchController.signal);
  const cancelledPage = assert.rejects(page, { name: "AbortError" });
  pageController.abort();
  await cancelledPage;
  assert.equal(internalSignal.aborted, false);
  deferred.complete({ marker: "prefetch-success" });
  await prefetch;
  assert.equal(deferred.fetchCount(), 1);
});

test("the shared market request aborts only after its last subscriber leaves", async (context) => {
  const deferred = installDeferredMarketOverviewFetch();
  context.after(deferred.restore);
  const requestKey = "view=ranking&includeFilterOptions=false&page=1&pageSize=20&dimension=SKU&startDate=2026-08-07&endDate=2026-08-08";
  const firstController = new AbortController();
  const secondController = new AbortController();
  const first = requestMarketOverview(requestKey, firstController.signal);
  const internalSignal = await deferred.started;
  const second = requestMarketOverview(requestKey, secondController.signal);
  const firstCancelled = assert.rejects(first, { name: "AbortError" });
  firstController.abort();
  await firstCancelled;
  assert.equal(internalSignal.aborted, false);
  const secondCancelled = assert.rejects(second, { name: "AbortError" });
  secondController.abort();
  await secondCancelled;
  assert.equal(internalSignal.aborted, true);
  assert.equal(deferred.fetchCount(), 1);
});

test("market overview response cache is canonical, version-invalidated, and coalesces duplicate loads", async () => {
  assert.match(canonicalMarketOverviewCacheIdentity({ view: "full", filters: {}, salesRevision: "test:1" }), /"formatVersion":5/);
  assert.notEqual(
    canonicalMarketOverviewCacheIdentity({ view: "ranking", filters: {}, pagination: { page: 1, pageSize: 20 }, salesRevision: "test:1" }),
    canonicalMarketOverviewCacheIdentity({ view: "ranking", filters: {}, pagination: { page: 2, pageSize: 20 }, salesRevision: "test:1" }),
  );
  assert.equal(
    canonicalMarketOverviewCacheIdentity({
      view: "ranking",
      filters: { categories: ["B", "A", "A"], rankingDimensions: ["SKU"] },
      salesRevision: "test:1",
    }),
    canonicalMarketOverviewCacheIdentity({
      view: "ranking",
      filters: { categories: ["A", "B"], rankingDimensions: ["SKU"] },
      salesRevision: "test:1",
    }),
  );

  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_monthly_summary_cache_state (id INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL);
    INSERT INTO market_monthly_summary_cache_state VALUES (1, 7);
    CREATE TABLE market_image_cache (status TEXT NOT NULL, attempt_count INTEGER NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE market_import_batches (
      id TEXT NOT NULL, status TEXT NOT NULL, file_name TEXT NOT NULL,
      created_at TEXT NOT NULL, completed_at TEXT, row_count INTEGER NOT NULL,
      inserted_count INTEGER NOT NULL, updated_count INTEGER NOT NULL, warning_count INTEGER NOT NULL
    );
    CREATE TABLE market_subcategory_taxonomy (status TEXT NOT NULL, updated_at TEXT NOT NULL);
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  for (const statement of marketBaseSchemaStatements.filter((sql) => sql.includes("market_overview_response_cache"))) {
    sqlite.exec(statement);
  }
  const db = asyncDatabase(sqlite);
  const identity = { view: "ranking" as const, filters: { rankingDimensions: ["SKU"] }, salesRevision: "test:1" };
  let loads = 0;
  const load = async () => {
    loads += 1;
    await new Promise((resolve) => setTimeout(resolve, 10));
    return { revision: loads };
  };

  const [first, duplicate] = await Promise.all([
    getCachedMarketOverview(db, identity, load),
    getCachedMarketOverview(db, identity, load),
  ]);
  assert.equal(loads, 1);
  assert.deepEqual(new Set([first.status, duplicate.status]), new Set(["miss", "coalesced"]));
  assert.deepEqual((await getCachedMarketOverview(db, identity, load)).payload, { revision: 1 });
  assert.equal(loads, 1);

  sqlite.exec("UPDATE market_overview_response_cache SET updated_at='2000-01-01 00:00:00'");
  assert.deepEqual((await getCachedMarketOverview(db, identity, load)).payload, { revision: 1 });
  assert.equal(loads, 1, "版本未变化的持久缓存不应仅因时间经过而重建");

  sqlite.exec("UPDATE market_monthly_summary_cache_state SET source_revision=8 WHERE id=1");
  const invalidated = await getCachedMarketOverview(db, identity, load);
  assert.equal(invalidated.status, "miss");
  assert.deepEqual(invalidated.payload, { revision: 2 });
  sqlite.close();
});

test("exact market revisions invalidate same-second batch, image, and taxonomy changes", async () => {
  const { sqlite, db } = createMarketResponseCacheFixture();
  sqlite.exec(`
    INSERT INTO market_import_batches
      (id,status,file_name,file_size_bytes,created_at,completed_at,row_count,inserted_count,updated_count,warning_count)
      VALUES ('batch-1','completed','market.xlsx',100,'2026-08-25 01:02:03','2026-08-25 01:02:03',1,1,0,0);
    INSERT INTO market_image_cache
      (id,source_url,status,attempt_count,content_sha256,updated_at)
      VALUES (1,'https://example.test/image.jpg','ready',1,'old-hash','2026-08-25 01:02:03');
    INSERT INTO market_subcategory_taxonomy
      (id,subcategory,status,updated_at) VALUES (1,'旧分类','active','2026-08-25 01:02:03');
  `);
  const identity = { view: "ranking" as const, filters: { rankingDimensions: ["SKU"] }, salesRevision: "test:1" };
  const validateOverview = (value: unknown) => validateMarketOverviewCachePayload(value, "ranking");
  let overviewLoads = 0;
  const loadOverview = async () => validMarketOverviewPayload("ranking", ++overviewLoads);

  assert.equal((await getCachedMarketOverviewRaw(db, identity, loadOverview, validateOverview)).status, "miss");
  const initialRevision = await getMarketOverviewCacheRevision(db, "test:1");
  sqlite.exec(`UPDATE market_import_batches SET file_size_bytes=101
    WHERE id='batch-1' AND created_at='2026-08-25 01:02:03'`);
  const batchRevision = await getMarketOverviewCacheRevision(db, "test:1");
  assert.notEqual(batchRevision, initialRevision);
  assert.equal((await getCachedMarketOverviewRaw(db, identity, loadOverview, validateOverview)).status, "miss");

  sqlite.exec(`UPDATE market_image_cache SET content_sha256='new-hash'
    WHERE id=1 AND updated_at='2026-08-25 01:02:03'`);
  const imageRevision = await getMarketOverviewCacheRevision(db, "test:1");
  assert.notEqual(imageRevision, batchRevision);
  assert.equal((await getCachedMarketOverviewRaw(db, identity, loadOverview, validateOverview)).status, "miss");
  assert.equal(overviewLoads, 3);

  let filterLoads = 0;
  const loadFilters = async () => {
    filterLoads += 1;
    return validFilterOptionsPayload();
  };
  await getCachedMarketFilterOptionsRaw(db, loadFilters, validateMarketFilterOptionsCachePayload);
  sqlite.exec(`UPDATE market_subcategory_taxonomy SET subcategory='新分类'
    WHERE id=1 AND updated_at='2026-08-25 01:02:03'`);
  await getCachedMarketFilterOptionsRaw(db, loadFilters, validateMarketFilterOptionsCachePayload);
  assert.equal(filterLoads, 2);
  sqlite.close();
});

test("market cache validators fail closed on valid JSON with the wrong surface shape", async () => {
  const { sqlite, db } = createMarketResponseCacheFixture();
  const identity = { view: "ranking" as const, filters: { categories: ["shape-check"] }, salesRevision: "test:1" };
  const overviewValidator = (value: unknown) => validateMarketOverviewCachePayload(value, "ranking");
  assert.equal(overviewValidator(validMarketOverviewPayload()), true);
  assert.equal(overviewValidator([]), false);
  assert.equal(validateMarketFilterOptionsCachePayload(validFilterOptionsPayload()), true);
  assert.equal(validateMarketFilterOptionsCachePayload({ categories_json: "[]" }), false);
  assert.equal(validateMarketSystemKpiCachePayload(validSystemKpiPayload()), true);
  assert.equal(validateMarketSystemKpiCachePayload({ marketIdentityTotal: 1 }), false);

  let overviewLoads = 0;
  const loadOverview = async () => validMarketOverviewPayload("ranking", ++overviewLoads);
  await getCachedMarketOverviewRaw(db, identity, loadOverview, overviewValidator);
  sqlite.exec("UPDATE market_overview_response_cache SET payload_json='[]'");
  assert.equal((await getCachedMarketOverviewRaw(db, identity, loadOverview, overviewValidator)).status, "miss");
  assert.equal(overviewLoads, 2);

  sqlite.exec("DELETE FROM market_overview_response_cache");
  let filterLoads = 0;
  const loadFilters = async () => {
    filterLoads += 1;
    return validFilterOptionsPayload();
  };
  await getCachedMarketFilterOptionsRaw(db, loadFilters, validateMarketFilterOptionsCachePayload);
  sqlite.exec("UPDATE market_overview_response_cache SET payload_json='{}'");
  await getCachedMarketFilterOptionsRaw(db, loadFilters, validateMarketFilterOptionsCachePayload);
  assert.equal(filterLoads, 2);

  sqlite.exec("DELETE FROM market_overview_response_cache");
  let systemLoads = 0;
  const loadSystem = async () => validSystemKpiPayload(++systemLoads);
  await getCachedMarketSystemKpisRaw(db, loadSystem, validateMarketSystemKpiCachePayload);
  sqlite.exec("UPDATE market_overview_response_cache SET payload_json='{broken-json'");
  assert.equal((await getCachedMarketSystemKpisRaw(db, loadSystem, validateMarketSystemKpiCachePayload)).status, "miss");
  assert.equal(systemLoads, 2);

  sqlite.exec("DELETE FROM market_overview_response_cache");
  await assert.rejects(
    () => getCachedMarketSystemKpisRaw(db, async () => ({ marketIdentityTotal: 1 }), validateMarketSystemKpiCachePayload),
    (error) => error instanceof PublicApiError && error.status === 503 && error.code === "service_unavailable",
  );
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_overview_response_cache").get()!.count, 0);
  sqlite.close();
});

test("shared market cache capacity remains bounded and usable across mixed surfaces", async () => {
  const { sqlite, db } = createMarketResponseCacheFixture();
  const overviewValidator = (value: unknown) => validateMarketOverviewCachePayload(value, "ranking");
  for (let index = 0; index < 40; index += 1) {
    await getCachedMarketOverviewRaw(
      db,
      { view: "ranking", filters: { categories: [`capacity-${index}`] }, salesRevision: "test:1" },
      async () => validMarketOverviewPayload("ranking", index),
      overviewValidator,
    );
  }
  sqlite.exec("UPDATE market_overview_response_cache SET updated_at='2000-01-01 00:00:00'");
  let filterLoads = 0;
  await getCachedMarketFilterOptionsRaw(db, async () => {
    filterLoads += 1;
    return validFilterOptionsPayload();
  }, validateMarketFilterOptionsCachePayload);
  let systemLoads = 0;
  await getCachedMarketSystemKpisRaw(db, async () => validSystemKpiPayload(++systemLoads), validateMarketSystemKpiCachePayload);

  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_overview_response_cache").get()!.count, 40);
  await getCachedMarketFilterOptionsRaw(db, async () => {
    filterLoads += 1;
    return validFilterOptionsPayload();
  }, validateMarketFilterOptionsCachePayload);
  assert.equal((await getCachedMarketSystemKpisRaw(
    db,
    async () => validSystemKpiPayload(++systemLoads),
    validateMarketSystemKpiCachePayload,
  )).status, "hit");
  assert.equal(filterLoads, 1);
  assert.equal(systemLoads, 1);
  sqlite.close();
});

test("market overview and filter single-flight streams are isolated by database", async () => {
  const left = createMarketResponseCacheFixture();
  const right = createMarketResponseCacheFixture();
  const identity = { view: "ranking" as const, filters: { rankingDimensions: ["SKU"] }, salesRevision: "test:1" };

  let leftOverviewLoads = 0;
  let rightOverviewLoads = 0;
  let resolveOverviewStarted: (() => void) | undefined;
  let releaseOverview: (() => void) | undefined;
  const overviewStarted = new Promise<void>((resolve) => { resolveOverviewStarted = resolve; });
  const overviewRelease = new Promise<void>((resolve) => { releaseOverview = resolve; });
  const leftOverview = getCachedMarketOverview(left.db, identity, async () => {
    leftOverviewLoads += 1;
    resolveOverviewStarted?.();
    await overviewRelease;
    return { database: "left-overview" };
  });
  await overviewStarted;
  const rightOverview = getCachedMarketOverview(right.db, identity, async () => {
    rightOverviewLoads += 1;
    return { database: "right-overview" };
  });
  await new Promise<void>((resolve) => setImmediate(resolve));
  releaseOverview?.();
  const [leftOverviewResult, rightOverviewResult] = await Promise.all([leftOverview, rightOverview]);
  assert.deepEqual(leftOverviewResult, { payload: { database: "left-overview" }, status: "miss" });
  assert.deepEqual(rightOverviewResult, { payload: { database: "right-overview" }, status: "miss" });
  assert.deepEqual([leftOverviewLoads, rightOverviewLoads], [1, 1]);

  let leftFilterLoads = 0;
  let rightFilterLoads = 0;
  let resolveFilterStarted: (() => void) | undefined;
  let releaseFilter: (() => void) | undefined;
  const filterStarted = new Promise<void>((resolve) => { resolveFilterStarted = resolve; });
  const filterRelease = new Promise<void>((resolve) => { releaseFilter = resolve; });
  const leftFilter = getCachedMarketFilterOptions(left.db, async () => {
    leftFilterLoads += 1;
    resolveFilterStarted?.();
    await filterRelease;
    return { database: "left-filter" };
  });
  await filterStarted;
  const rightFilter = getCachedMarketFilterOptions(right.db, async () => {
    rightFilterLoads += 1;
    return { database: "right-filter" };
  });
  await new Promise<void>((resolve) => setImmediate(resolve));
  releaseFilter?.();
  assert.deepEqual(await Promise.all([leftFilter, rightFilter]), [
    { database: "left-filter" },
    { database: "right-filter" },
  ]);
  assert.deepEqual([leftFilterLoads, rightFilterLoads], [1, 1]);
  left.sqlite.close();
  right.sqlite.close();
});

test("market overview and filter single-flight entries are removed after loader errors", async () => {
  const { sqlite, db } = createMarketResponseCacheFixture();
  const identity = { view: "ranking" as const, filters: { categories: ["error-cleanup"] }, salesRevision: "test:1" };
  let overviewLoads = 0;
  await assert.rejects(() => getCachedMarketOverview(db, identity, async () => {
    overviewLoads += 1;
    throw new Error("forced overview loader failure");
  }), /forced overview loader failure/);
  const overviewRetry = await getCachedMarketOverview(db, identity, async () => {
    overviewLoads += 1;
    return { recovered: true };
  });
  assert.deepEqual(overviewRetry, { payload: { recovered: true }, status: "miss" });
  assert.equal(overviewLoads, 2);

  let filterLoads = 0;
  await assert.rejects(() => getCachedMarketFilterOptions(db, async () => {
    filterLoads += 1;
    throw new Error("forced filter loader failure");
  }), /forced filter loader failure/);
  assert.deepEqual(await getCachedMarketFilterOptions(db, async () => {
    filterLoads += 1;
    return { recovered: true };
  }), { recovered: true });
  assert.equal(filterLoads, 2);
  sqlite.close();
});

test("market overview and filter cache misses close their revision fence and retry only once", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_monthly_summary_cache_state (id INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL);
    INSERT INTO market_monthly_summary_cache_state VALUES (1, 1);
    CREATE TABLE market_image_cache (status TEXT NOT NULL, attempt_count INTEGER NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE market_import_batches (
      id TEXT NOT NULL, status TEXT NOT NULL, file_name TEXT NOT NULL,
      created_at TEXT NOT NULL, completed_at TEXT, row_count INTEGER NOT NULL,
      inserted_count INTEGER NOT NULL, updated_count INTEGER NOT NULL, warning_count INTEGER NOT NULL
    );
    CREATE TABLE market_subcategory_taxonomy (status TEXT NOT NULL, updated_at TEXT NOT NULL);
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  for (const statement of marketBaseSchemaStatements.filter((sql) => sql.includes("market_overview_response_cache"))) {
    sqlite.exec(statement);
  }
  const db = asyncDatabase(sqlite);
  const identity = { view: "ranking" as const, filters: { rankingDimensions: ["SKU"] }, salesRevision: "test:1" };
  let overviewLoads = 0;
  const overview = await getCachedMarketOverview(db, identity, async () => {
    overviewLoads += 1;
    if (overviewLoads === 1) sqlite.exec("UPDATE market_monthly_summary_cache_state SET source_revision=2 WHERE id=1");
    return { revision: overviewLoads };
  });
  assert.equal(overviewLoads, 2);
  assert.deepEqual(overview.payload, { revision: 2 });
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_overview_response_cache").get()!.count, 1);

  sqlite.exec("DELETE FROM market_overview_response_cache; UPDATE market_monthly_summary_cache_state SET source_revision=10 WHERE id=1");
  let unstableLoads = 0;
  await assert.rejects(
    () => getCachedMarketOverview(db, identity, async () => {
      unstableLoads += 1;
      sqlite.exec("UPDATE market_monthly_summary_cache_state SET source_revision=source_revision+1 WHERE id=1");
      return { revision: unstableLoads };
    }),
    (error) => error instanceof PublicApiError && error.status === 503 && error.code === "service_unavailable",
  );
  assert.equal(unstableLoads, 2);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_overview_response_cache").get()!.count, 0);

  sqlite.exec("UPDATE market_monthly_summary_cache_state SET source_revision=20 WHERE id=1");
  let filterLoads = 0;
  const filters = await getCachedMarketFilterOptions(db, async () => {
    filterLoads += 1;
    if (filterLoads === 1) sqlite.exec("UPDATE market_system_kpi_cache_state SET source_revision=source_revision+1 WHERE id=1");
    return { revision: filterLoads };
  });
  assert.equal(filterLoads, 2);
  assert.deepEqual(filters, { revision: 2 });
  sqlite.close();
});

test("market filter options use a separate revision-aware cache", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_monthly_summary_cache_state (id INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL);
    INSERT INTO market_monthly_summary_cache_state VALUES (1, 3);
    CREATE TABLE market_subcategory_taxonomy (status TEXT NOT NULL, updated_at TEXT NOT NULL);
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  for (const statement of marketBaseSchemaStatements.filter((sql) => sql.includes("market_overview_response_cache"))) {
    sqlite.exec(statement);
  }
  const db = asyncDatabase(sqlite);
  let loads = 0;
  const load = async () => ({ categories_json: JSON.stringify([{ value: "净水", count: ++loads }]) });

  const first = await getCachedMarketFilterOptions(db, load);
  const cached = await getCachedMarketFilterOptions(db, load);
  assert.deepEqual(cached, first);
  assert.equal(loads, 1);

  sqlite.exec("UPDATE market_system_kpi_cache_state SET source_revision=source_revision+1 WHERE id=1");
  const refreshed = await getCachedMarketFilterOptions(db, load);
  assert.equal(loads, 2);
  assert.notDeepEqual(refreshed, first);
  sqlite.close();
});

test("legacy market response cache schema remains migratable while the active route uses Django", async () => {
  const schema = marketBaseSchemaStatements.join("\n");
  const [migration, schemaCore, cacheSource, overviewRoute, databaseSource, adminSource] = await Promise.all([
    readFile(new URL("../drizzle/0050_market_overview_response_cache.sql", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/schema-core.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/overview-response-cache.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8"),
  ]);
  for (const sql of [schema, migration, schemaCore]) {
    assert.match(sql, /market_overview_response_cache/);
  }
  for (const sql of [migration, schemaCore]) assert.match(sql, /market_image_cache_updated_idx/);
  assert.match(cacheSource, /DELETE FROM market_overview_response_cache WHERE cache_key=\? AND revision_key=\?/);
  assert.match(cacheSource, /ORDER BY updated_at DESC, cache_key DESC LIMIT \?/);
  assert.match(overviewRoute, /requestDjangoMarketService/);
  assert.match(overviewRoute, /service: "reader"/);
  assert.doesNotMatch(overviewRoute, /getCachedMarketOverview|validateMarketOverviewCachePayload|getD1Database/);
  assert.match(databaseSource, /getCachedMarketFilterOptions\([\s\S]*validateMarketFilterOptionsCachePayload/);
  assert.match(adminSource, /getCachedMarketSystemKpis\([\s\S]*validateMarketSystemKpiCachePayload/);
});

test("system KPI cache is persistent, single-flight, and invalidated by every KPI dependency", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_overview_response_cache (
      cache_key TEXT PRIMARY KEY NOT NULL, revision_key TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY);
    CREATE TABLE market_price_snapshots (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
    CREATE TABLE market_image_cache (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_items (id INTEGER PRIMARY KEY);
    CREATE TABLE market_subcategory_taxonomy (id INTEGER PRIMARY KEY);
    CREATE TABLE market_master_identities (id INTEGER PRIMARY KEY);
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  const db = asyncDatabase(sqlite);
  await ensureMarketSystemKpiCacheSchema(db);
  let loads = 0;
  const load = async () => {
    loads += 1;
    await new Promise((resolve) => setTimeout(resolve, 10));
    return { load: loads };
  };
  const [first, joined] = await Promise.all([
    getCachedMarketSystemKpis(db, load),
    getCachedMarketSystemKpis(db, load),
  ]);
  assert.equal(loads, 1);
  assert.deepEqual(new Set([first.status, joined.status]), new Set(["miss", "coalesced"]));
  assert.equal((await getCachedMarketSystemKpis(db, load)).status, "hit");

  let expectedRevision = Number(sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get()!.source_revision);
  for (const table of [
    "market_ranking_entries",
    "market_price_snapshots",
    "market_annotation_prompt_versions",
    "market_image_cache",
    "market_annotation_items",
    "market_subcategory_taxonomy",
    "market_master_identities",
    "market_import_batches",
  ]) {
    sqlite.exec(`INSERT INTO ${table} (id) VALUES (1)`);
    expectedRevision += 1;
    assert.equal(sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get()!.source_revision, expectedRevision);
    sqlite.exec(`UPDATE ${table} SET id=id WHERE id=1`);
    expectedRevision += 1;
    assert.equal(sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get()!.source_revision, expectedRevision);
    sqlite.exec(`DELETE FROM ${table} WHERE id=1`);
    expectedRevision += 1;
    assert.equal(sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get()!.source_revision, expectedRevision);
  }
  const invalidated = await getCachedMarketSystemKpis(db, load);
  assert.equal(invalidated.status, "miss");
  assert.equal(loads, 2);
  sqlite.close();
});

test("market identity updates invalidate a previously cached system KPI payload", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_overview_response_cache (
      cache_key TEXT PRIMARY KEY NOT NULL, revision_key TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY);
    CREATE TABLE market_price_snapshots (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
    CREATE TABLE market_image_cache (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_items (id INTEGER PRIMARY KEY);
    CREATE TABLE market_subcategory_taxonomy (id INTEGER PRIMARY KEY);
    CREATE TABLE market_master_identities (id INTEGER PRIMARY KEY, identity_value TEXT NOT NULL);
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  const db = asyncDatabase(sqlite);
  await ensureMarketSystemKpiCacheSchema(db);
  sqlite.exec("INSERT INTO market_master_identities (id, identity_value) VALUES (1, 'old')");
  let loads = 0;
  const load = async () => {
    loads += 1;
    return {
      identity: sqlite.prepare("SELECT identity_value FROM market_master_identities WHERE id=1").get()!.identity_value,
    };
  };

  assert.deepEqual(await getCachedMarketSystemKpis(db, load), {
    payload: { identity: "old" },
    status: "miss",
  });
  assert.equal((await getCachedMarketSystemKpis(db, load)).status, "hit");
  sqlite.exec("UPDATE market_master_identities SET identity_value='new' WHERE id=1");
  assert.deepEqual(await getCachedMarketSystemKpis(db, load), {
    payload: { identity: "new" },
    status: "miss",
  });
  assert.equal(loads, 2);
  sqlite.close();
});

test("system KPI single-flight is isolated by database identity", async () => {
  const createFixture = () => {
    const sqlite = new DatabaseSync(":memory:");
    sqlite.exec(`
      CREATE TABLE market_overview_response_cache (
        cache_key TEXT PRIMARY KEY NOT NULL, revision_key TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY);
      CREATE TABLE market_price_snapshots (id INTEGER PRIMARY KEY);
      CREATE TABLE market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
      CREATE TABLE market_image_cache (id INTEGER PRIMARY KEY);
      CREATE TABLE market_annotation_items (id INTEGER PRIMARY KEY);
      CREATE TABLE market_subcategory_taxonomy (id INTEGER PRIMARY KEY);
      CREATE TABLE market_master_identities (id INTEGER PRIMARY KEY);
    `);
    installMarketCacheRevisionDependencyTables(sqlite);
    return { sqlite, db: asyncDatabase(sqlite) };
  };
  const left = createFixture();
  const right = createFixture();
  await Promise.all([
    ensureMarketSystemKpiCacheSchema(left.db),
    ensureMarketSystemKpiCacheSchema(right.db),
  ]);
  let leftLoads = 0;
  let rightLoads = 0;
  let resolveLeftStarted: (() => void) | undefined;
  let releaseLeft: (() => void) | undefined;
  const leftStarted = new Promise<void>((resolve) => { resolveLeftStarted = resolve; });
  const leftRelease = new Promise<void>((resolve) => { releaseLeft = resolve; });
  const leftRequest = getCachedMarketSystemKpis(left.db, async () => {
    leftLoads += 1;
    resolveLeftStarted?.();
    await leftRelease;
    return { database: "left" };
  });
  await leftStarted;
  const rightRequest = getCachedMarketSystemKpis(right.db, async () => {
    rightLoads += 1;
    return { database: "right" };
  });
  await new Promise<void>((resolve) => setImmediate(resolve));
  releaseLeft?.();
  const [leftResult, rightResult] = await Promise.all([leftRequest, rightRequest]);

  assert.deepEqual(leftResult, { payload: { database: "left" }, status: "miss" });
  assert.deepEqual(rightResult, { payload: { database: "right" }, status: "miss" });
  assert.equal(leftLoads, 1);
  assert.equal(rightLoads, 1);
  left.sqlite.close();
  right.sqlite.close();
});

test("system KPI cache never persists a payload while its revision keeps moving", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_overview_response_cache (
      cache_key TEXT PRIMARY KEY NOT NULL, revision_key TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY);
    CREATE TABLE market_price_snapshots (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
    CREATE TABLE market_image_cache (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_items (id INTEGER PRIMARY KEY);
    CREATE TABLE market_subcategory_taxonomy (id INTEGER PRIMARY KEY);
    CREATE TABLE market_master_identities (id INTEGER PRIMARY KEY);
  `);
  installMarketCacheRevisionDependencyTables(sqlite);
  const db = asyncDatabase(sqlite);
  let loads = 0;
  await assert.rejects(
    () => getCachedMarketSystemKpis(db, async () => {
      loads += 1;
      sqlite.exec(`INSERT INTO market_annotation_items (id) VALUES (${loads})`);
      return { load: loads };
    }),
    (error) => error instanceof PublicApiError && error.status === 503 && error.code === "service_unavailable",
  );
  assert.equal(loads, 2);
  assert.equal(sqlite.prepare("SELECT COUNT(*) count FROM market_overview_response_cache").get()!.count, 0);
  sqlite.close();
});

test("controlled master-identity refresh bumps once, rolls back cleanly, and leaves direct writes observable", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = asyncDatabase(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  await ensureMarketSystemKpiCacheSchema(db);
  const insertRanking = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,
      operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES (?,?, '2026-06-01','2026-06-30','bulk-category','POP','SKU','POP',?,?, '{}','bulk-batch')`);
  for (let index = 0; index < 1_000; index += 1) {
    insertRanking.run(`bulk-${index}`, index + 1, `SKU-BULK-${index}`, `Bulk ${index}`);
  }
  const revision = () => Number((sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get() as { source_revision: number }).source_revision);

  const beforeFirstRefresh = revision();
  await refreshMarketMasterIdentities(db);
  assert.equal(revision(), beforeFirstRefresh + 1, "1,000 controlled inserts must publish one revision");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_identities").get() as { count: number }).count, 1_000);

  const beforeSecondRefresh = revision();
  await refreshMarketMasterIdentities(db);
  assert.equal(revision(), beforeSecondRefresh + 1, "1,000 deletes plus inserts must still publish one revision");

  const beforeFailedRefresh = revision();
  const failingDb = {
    prepare: db.prepare,
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (let index = 0; index < statements.length; index += 1) {
          results.push(await statements[index]!.run());
          if (index === 2) throw new Error("forced controlled refresh failure");
        }
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  };
  await assert.rejects(() => refreshMarketMasterIdentities(failingDb), /forced controlled refresh failure/);
  assert.equal(revision(), beforeFailedRefresh);
  assert.equal((sqlite.prepare("SELECT suppress_identity_revision value FROM market_system_kpi_cache_control WHERE id=1").get() as { value: number }).value, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_identities").get() as { count: number }).count, 1_000);

  const directEntryId = Number((sqlite.prepare("SELECT MAX(id) max_id FROM market_ranking_entries").get() as { max_id: number }).max_id);
  let expectedRevision = revision();
  sqlite.prepare(`INSERT INTO market_master_identities
    (category,scope,ranking_dimension,sku_code,latest_entry_id) VALUES ('direct','POP','SKU','DIRECT-SKU',?)`).run(directEntryId + 1);
  expectedRevision += 1;
  assert.equal(revision(), expectedRevision);
  sqlite.exec("UPDATE market_master_identities SET sku_code='DIRECT-SKU-UPDATED' WHERE category='direct'");
  expectedRevision += 1;
  assert.equal(revision(), expectedRevision);
  sqlite.exec("DELETE FROM market_master_identities WHERE category='direct'");
  expectedRevision += 1;
  assert.equal(revision(), expectedRevision);

  sqlite.exec(`UPDATE market_system_kpi_cache_control
    SET suppress_identity_revision=1,owner_token='stale-owner' WHERE id=1`);
  const beforeStaleOwnerAttempt = revision();
  await assert.rejects(() => refreshMarketMasterIdentities(db), /CHECK constraint failed/);
  assert.equal(revision(), beforeStaleOwnerAttempt);
  assert.deepEqual({ ...(sqlite.prepare(`SELECT suppress_identity_revision suppressIdentity,owner_token owner
      FROM market_system_kpi_cache_control WHERE id=1`).get() as Record<string, unknown>) }, {
    suppressIdentity: 1, owner: "stale-owner",
  });
  sqlite.exec(`UPDATE market_system_kpi_cache_control
    SET suppress_identity_revision=0,owner_token='' WHERE id=1`);
  sqlite.close();
});

test("system KPI cache runtime schema and forward migration keep the same dependency triggers", async () => {
  const [migration, exactRevisionMigration] = await Promise.all([
    readFile(new URL("../drizzle/0075_market_system_kpi_cache.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0079_market_overview_exact_revision.sql", import.meta.url), "utf8"),
  ]);
  const forwardMigrations = `${migration}\n${exactRevisionMigration}`;
  const runtimeTriggers = marketSystemKpiCacheTriggerStatements.join("\n");
  const runtimeUpgrade = [
    marketSystemKpiCacheControlTableStatement,
    ...marketSystemKpiCacheTriggerDropStatements,
    runtimeTriggers,
  ].join("\n");
  assert.match(migration, /market_system_kpi_cache_state/);
  for (const source of [runtimeUpgrade, forwardMigrations]) {
    assert.match(source, /market_system_kpi_cache_control/);
    assert.match(source, /suppress_all_revision/);
    assert.match(source, /suppress_identity_revision/);
    assert.match(source, /owner_token/);
    for (const dependency of ["ranking", "price", "prompt", "image", "annotation", "taxonomy", "identity", "batch"]) {
      for (const operation of ["insert", "update", "delete"]) {
        assert.match(source, new RegExp(`market_system_kpi_cache_${dependency}_${operation}`));
      }
    }
  }
  assert.equal((runtimeTriggers.match(/suppress_all_revision=1/g) ?? []).length, 24);
  assert.equal((forwardMigrations.match(/`suppress_all_revision`=1/g) ?? []).length, 24);
  assert.match(runtimeTriggers, /market_master_identities[\s\S]*WHEN NOT EXISTS[\s\S]*suppress_identity_revision=1/);
  assert.match(migration, /DROP TRIGGER IF EXISTS `market_system_kpi_cache_identity_insert`[\s\S]*WHEN NOT EXISTS[\s\S]*suppress_identity_revision`=1/);

  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY);
    CREATE TABLE market_price_snapshots (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_prompt_versions (id INTEGER PRIMARY KEY);
    CREATE TABLE market_image_cache (id INTEGER PRIMARY KEY);
    CREATE TABLE market_annotation_items (id INTEGER PRIMARY KEY);
    CREATE TABLE market_subcategory_taxonomy (id INTEGER PRIMARY KEY);
    CREATE TABLE market_master_identities (id INTEGER PRIMARY KEY);
    CREATE TABLE market_import_batches (id INTEGER PRIMARY KEY);
  `);
  for (const statement of migration.split("--> statement-breakpoint").map((sql) => sql.trim()).filter(Boolean)) {
    sqlite.exec(statement);
  }
  for (const statement of exactRevisionMigration.split("--> statement-breakpoint").map((sql) => sql.trim()).filter(Boolean)) {
    sqlite.exec(statement);
  }
  sqlite.exec("INSERT INTO market_ranking_entries (id) VALUES (1)");
  sqlite.exec("INSERT INTO market_import_batches (id) VALUES (1)");
  assert.equal(sqlite.prepare("SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1").get()!.source_revision, 4);
  assert.equal(sqlite.prepare("SELECT suppress_identity_revision FROM market_system_kpi_cache_control WHERE id=1").get()!.suppress_identity_revision, 0);
  sqlite.close();

  const upgradeSqlite = new DatabaseSync(":memory:");
  const upgradeDb = asyncDatabase(upgradeSqlite);
  await ensureMarketSchemaCore(upgradeDb);
  await ensureAnnotationSchema(upgradeDb as never);
  upgradeSqlite.exec(`CREATE TRIGGER market_system_kpi_cache_ranking_insert
    AFTER INSERT ON market_ranking_entries BEGIN
      UPDATE market_system_kpi_cache_state SET source_revision=source_revision+1 WHERE id=1;
    END;`);
  await ensureMarketSystemKpiCacheSchema(upgradeDb);
  const upgradedTrigger = String((upgradeSqlite.prepare(`SELECT sql FROM sqlite_master
    WHERE type='trigger' AND name='market_system_kpi_cache_ranking_insert'`).get() as { sql: string }).sql);
  assert.match(upgradedTrigger, /WHEN NOT EXISTS[\s\S]*suppress_all_revision=1/);
  upgradeSqlite.close();
});

test("pending-price history starts from snapshots and uses the representative identity-month index", async () => {
  const service = await readFile(new URL("../lib/market/admin-service.ts", import.meta.url), "utf8");
  assert.match(service, /SELECT source\.\* FROM market_price_snapshots snapshot[\s\S]*candidate\.category=snapshot\.category[\s\S]*candidate\.period_end>=snapshot\.month\|\|'-01'[\s\S]*candidate\.period_end<date\(snapshot\.month\|\|'-01','\+1 month'\)/);
  const historyBranch = service.slice(service.indexOf("function masterBaseSql"), service.indexOf("async function getMarketItemTrendLite"));
  assert.doesNotMatch(historyBranch, /ROW_NUMBER\(\) OVER/);
});
