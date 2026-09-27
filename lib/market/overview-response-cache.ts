import type { MarketOverviewFilters } from "@/lib/market/database";
import { PublicApiError } from "@/lib/http/api-error";
import {
  marketSystemKpiCacheControlSeedStatement,
  marketSystemKpiCacheControlTableStatement,
  marketSystemKpiCacheTriggerDropStatements,
  marketSystemKpiCacheTriggerNames,
  marketSystemKpiCacheTriggerStatements,
} from "@/lib/market/schema-core";

type CacheStatement = {
  bind(...values: unknown[]): CacheStatement;
  first<T = unknown>(): Promise<T | null>;
  run(): Promise<unknown>;
};

export type MarketOverviewResponseCacheDatabase = {
  prepare(sql: string): CacheStatement;
  batch(statements: CacheStatement[]): Promise<unknown>;
};

type CacheIdentity = {
  view: "ranking" | "full";
  filters: MarketOverviewFilters;
  pagination?: { page: number; pageSize: number };
  salesRevision: string;
};

type CacheRevisionRow = {
  revision_key: string;
};

type CachePayloadRow = {
  payload_json: string;
};

export type MarketOverviewCacheResult<T> = {
  payload: T;
  status: "hit" | "miss" | "coalesced";
};

export type MarketCachePayloadValidator = (value: unknown) => boolean;

const CACHE_MAX_ROWS = 40;
const CACHE_FORMAT_VERSION = 5;
const FILTER_OPTIONS_FORMAT_VERSION = 2;
const MASTER_DATABASE_FILTERS_FORMAT_VERSION = 1;
const SYSTEM_KPI_FORMAT_VERSION = 1;
type CacheFlightMap = Map<string, Promise<MarketOverviewCacheResult<unknown>>>;
const overviewInFlightByDatabase = new WeakMap<object, CacheFlightMap>();
const filterOptionsInFlightByDatabase = new WeakMap<object, CacheFlightMap>();
const masterDatabaseFiltersInFlightByDatabase = new WeakMap<object, CacheFlightMap>();
const systemKpiInFlightByDatabase = new WeakMap<object, Map<string, Promise<MarketOverviewCacheResult<unknown>>>>();
const systemKpiSchemaByDatabase = new WeakMap<object, Promise<void>>();
const masterDatabaseFiltersSchemaByDatabase = new WeakMap<object, Promise<void>>();

export const marketMasterDatabaseFiltersCacheStateStatement = `CREATE TABLE IF NOT EXISTS market_master_database_filters_cache_state (
  id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
  source_revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)`;

export const marketMasterDatabaseFiltersCacheSeedStatement = `INSERT OR IGNORE INTO market_master_database_filters_cache_state
  (id, source_revision) VALUES (1, 1)`;

export const marketMasterDatabaseFiltersCacheTriggerStatements = [
  `CREATE TRIGGER IF NOT EXISTS market_master_filters_v1_ranking_insert
    AFTER INSERT ON market_ranking_entries BEGIN
      UPDATE market_master_database_filters_cache_state
      SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1;
    END`,
  `CREATE TRIGGER IF NOT EXISTS market_master_filters_v1_ranking_update
    AFTER UPDATE OF category,subcategory,sku_code ON market_ranking_entries
    WHEN OLD.category IS NOT NEW.category OR OLD.subcategory IS NOT NEW.subcategory OR OLD.sku_code IS NOT NEW.sku_code
    BEGIN
      UPDATE market_master_database_filters_cache_state
      SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1;
    END`,
  `CREATE TRIGGER IF NOT EXISTS market_master_filters_v1_ranking_delete
    AFTER DELETE ON market_ranking_entries BEGIN
      UPDATE market_master_database_filters_cache_state
      SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1;
    END`,
  `CREATE TRIGGER IF NOT EXISTS market_master_filters_v1_taxonomy_insert
    AFTER INSERT ON market_subcategory_taxonomy BEGIN
      UPDATE market_master_database_filters_cache_state
      SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1;
    END`,
  `CREATE TRIGGER IF NOT EXISTS market_master_filters_v1_taxonomy_update
    AFTER UPDATE OF category,subcategory,status ON market_subcategory_taxonomy
    WHEN OLD.category IS NOT NEW.category OR OLD.subcategory IS NOT NEW.subcategory OR OLD.status IS NOT NEW.status
    BEGIN
      UPDATE market_master_database_filters_cache_state
      SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1;
    END`,
  `CREATE TRIGGER IF NOT EXISTS market_master_filters_v1_taxonomy_delete
    AFTER DELETE ON market_subcategory_taxonomy BEGIN
      UPDATE market_master_database_filters_cache_state
      SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1;
    END`,
] as const;

export const marketMasterDatabaseFiltersCacheTriggerNames = marketMasterDatabaseFiltersCacheTriggerStatements
  .map((statement) => statement.match(/TRIGGER IF NOT EXISTS ([a-z0-9_]+)/)?.[1] ?? "");

export const marketMasterDatabaseFiltersCacheTriggerDropStatements = marketMasterDatabaseFiltersCacheTriggerNames
  .map((name) => `DROP TRIGGER IF EXISTS ${name}`);

function cacheFlightsForDatabase(
  flightsByDatabase: WeakMap<object, CacheFlightMap>,
  db: MarketOverviewResponseCacheDatabase,
) {
  const key = db as object;
  const existing = flightsByDatabase.get(key);
  if (existing) return existing;
  const flights: CacheFlightMap = new Map();
  flightsByDatabase.set(key, flights);
  return flights;
}

function normalizedList(values: string[] | undefined) {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
}

function normalizedSalesRevision(value: string) {
  const revision = value.trim();
  if (!revision || revision.length > 128 || /[\u0000-\u001f\u007f]/.test(revision)) {
    throw new Error("INVALID_DJANGO_SALES_REVISION");
  }
  return revision;
}

export function canonicalMarketOverviewCacheIdentity(identity: CacheIdentity) {
  const filters = identity.filters;
  return JSON.stringify({
    formatVersion: CACHE_FORMAT_VERSION,
    salesRevision: normalizedSalesRevision(identity.salesRevision),
    view: identity.view,
    query: filters.query?.trim() || "",
    categories: normalizedList(filters.categories),
    scopes: normalizedList(filters.scopes),
    brands: normalizedList(filters.brands),
    rankingDimensions: normalizedList(filters.rankingDimensions),
    operationModes: normalizedList(filters.operationModes),
    subcategories: normalizedList(filters.subcategories),
    priceBands: normalizedList(filters.priceBands),
    startDate: filters.startDate ?? "",
    endDate: filters.endDate ?? "",
    page: identity.view === "ranking" ? identity.pagination?.page ?? 1 : 1,
    pageSize: identity.view === "ranking" ? identity.pagination?.pageSize ?? 20 : 200,
  });
}

async function sha256(value: string) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function getMarketOverviewCacheRevision(
  db: MarketOverviewResponseCacheDatabase,
  salesRevision: string,
) {
  const row = await db.prepare(`SELECT
    CAST(COALESCE((SELECT source_revision FROM market_monthly_summary_cache_state WHERE id=1),0) AS TEXT)
      || ':' || CAST(COALESCE((SELECT source_revision FROM market_system_kpi_cache_state WHERE id=1),0) AS TEXT)
      AS revision_key`)
    .first<CacheRevisionRow>();
  return `${row?.revision_key ?? "0"}:django-sales:${normalizedSalesRevision(salesRevision)}`;
}

async function getMarketFilterOptionsRevision(db: MarketOverviewResponseCacheDatabase) {
  return getMarketSystemKpiCacheRevision(db);
}

async function getMarketMasterDatabaseFiltersRevision(db: MarketOverviewResponseCacheDatabase) {
  const row = await db.prepare(`SELECT CAST(source_revision AS TEXT) revision_key
    FROM market_master_database_filters_cache_state WHERE id=1`)
    .first<CacheRevisionRow>();
  return row?.revision_key ?? "0";
}

async function getMarketSystemKpiCacheRevision(db: MarketOverviewResponseCacheDatabase) {
  const row = await db.prepare(`SELECT CAST(source_revision AS TEXT) revision_key
    FROM market_system_kpi_cache_state WHERE id=1`)
    .first<CacheRevisionRow>();
  return row?.revision_key ?? "0";
}

export function ensureMarketSystemKpiCacheSchema(db: MarketOverviewResponseCacheDatabase): Promise<void> {
  const key = db as object;
  const existing = systemKpiSchemaByDatabase.get(key);
  if (existing) return existing;
  const setup = (async () => {
    await db.prepare(`CREATE TABLE IF NOT EXISTS market_system_kpi_cache_state (
      id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
      source_revision INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`).run();
    await db.prepare(`INSERT OR IGNORE INTO market_system_kpi_cache_state (id, source_revision)
      VALUES (1, 1)`).run();
    await db.prepare(marketSystemKpiCacheControlTableStatement).run();
    await db.prepare(marketSystemKpiCacheControlSeedStatement).run();
    const triggerNamesSql = marketSystemKpiCacheTriggerNames.map((name) => `'${name}'`).join(",");
    const triggerState = await db.prepare(`SELECT COUNT(*) trigger_count FROM sqlite_master
      WHERE type='trigger' AND name IN (${triggerNamesSql})`).first<{ trigger_count: number }>();
    const needsTriggerUpgrade = Number(triggerState?.trigger_count ?? 0) !== marketSystemKpiCacheTriggerNames.length;
    if (needsTriggerUpgrade) {
      await db.batch([
        ...marketSystemKpiCacheTriggerDropStatements.map((statement) => db.prepare(statement)),
        ...marketSystemKpiCacheTriggerStatements.map((statement) => db.prepare(statement)),
        db.prepare(`UPDATE market_system_kpi_cache_state
          SET source_revision=source_revision+1, updated_at=CURRENT_TIMESTAMP WHERE id=1`),
      ]);
    }
  })().catch((error: unknown) => {
    systemKpiSchemaByDatabase.delete(key);
    throw error;
  });
  systemKpiSchemaByDatabase.set(key, setup);
  return setup;
}

export function ensureMarketMasterDatabaseFiltersCacheSchema(db: MarketOverviewResponseCacheDatabase): Promise<void> {
  const key = db as object;
  const existing = masterDatabaseFiltersSchemaByDatabase.get(key);
  if (existing) return existing;
  const setup = (async () => {
    await db.prepare(marketMasterDatabaseFiltersCacheStateStatement).run();
    await db.prepare(marketMasterDatabaseFiltersCacheSeedStatement).run();
    const triggerNamesSql = marketMasterDatabaseFiltersCacheTriggerNames.map((name) => `'${name}'`).join(",");
    const triggerState = await db.prepare(`SELECT COUNT(*) trigger_count FROM sqlite_master
      WHERE type='trigger' AND name IN (${triggerNamesSql})`).first<{ trigger_count: number }>();
    if (Number(triggerState?.trigger_count ?? 0) !== marketMasterDatabaseFiltersCacheTriggerNames.length) {
      await db.batch([
        ...marketMasterDatabaseFiltersCacheTriggerDropStatements.map((statement) => db.prepare(statement)),
        ...marketMasterDatabaseFiltersCacheTriggerStatements.map((statement) => db.prepare(statement)),
        db.prepare(`UPDATE market_master_database_filters_cache_state
          SET source_revision=source_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1`),
      ]);
    }
  })().catch((error: unknown) => {
    masterDatabaseFiltersSchemaByDatabase.delete(key);
    throw error;
  });
  masterDatabaseFiltersSchemaByDatabase.set(key, setup);
  return setup;
}

async function readCachedPayload<T>(
  db: MarketOverviewResponseCacheDatabase,
  cacheKey: string,
  revisionKey: string,
  validate: MarketCachePayloadValidator,
): Promise<T | null> {
  const row = await db.prepare(`SELECT payload_json FROM market_overview_response_cache
    WHERE cache_key=? AND revision_key=?`)
    .bind(cacheKey, revisionKey)
    .first<CachePayloadRow>();
  if (!row) return null;
  try {
    const payload: unknown = JSON.parse(row.payload_json);
    if (!validate(payload)) throw new Error("invalid_cache_payload");
    return payload as T;
  } catch {
    await db.prepare("DELETE FROM market_overview_response_cache WHERE cache_key=? AND revision_key=?")
      .bind(cacheKey, revisionKey).run().catch(() => undefined);
    return null;
  }
}

async function writeCachedPayload<T>(
  db: MarketOverviewResponseCacheDatabase,
  cacheKey: string,
  revisionKey: string,
  payload: T,
) {
  await db.prepare(`INSERT INTO market_overview_response_cache
    (cache_key,revision_key,payload_json,created_at,updated_at)
    VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
    ON CONFLICT(cache_key) DO UPDATE SET
      revision_key=excluded.revision_key,
      payload_json=excluded.payload_json,
      updated_at=CURRENT_TIMESTAMP`)
    .bind(cacheKey, revisionKey, JSON.stringify(payload)).run();
  await db.prepare(`DELETE FROM market_overview_response_cache
    WHERE cache_key NOT IN (
      SELECT cache_key FROM market_overview_response_cache ORDER BY updated_at DESC, cache_key DESC LIMIT ?
    )`).bind(CACHE_MAX_ROWS).run().catch(() => undefined);
}

async function loadRevisionFencedCache<T>(input: {
  db: MarketOverviewResponseCacheDatabase;
  cacheKey: string;
  revisionKey: string;
  readRevision: () => Promise<string>;
  load: () => Promise<T>;
  validate: MarketCachePayloadValidator;
  flights: Map<string, Promise<MarketOverviewCacheResult<unknown>>>;
  driftMessage: string;
  invalidPayloadMessage: string;
  attempt?: number;
}): Promise<MarketOverviewCacheResult<T>> {
  const attempt = input.attempt ?? 0;
  const flightKey = `${input.cacheKey}:${input.revisionKey}`;
  const running = input.flights.get(flightKey);
  if (running) {
    const result = await running as MarketOverviewCacheResult<T>;
    return { payload: result.payload, status: "coalesced" };
  }

  const retryAfterDrift = async (nextRevision: string) => {
    if (attempt >= 1) {
      throw new PublicApiError(503, "service_unavailable", input.driftMessage);
    }
    return loadRevisionFencedCache({
      ...input,
      revisionKey: nextRevision,
      attempt: attempt + 1,
    });
  };

  const task = (async (): Promise<MarketOverviewCacheResult<T>> => {
    const cached = await readCachedPayload<T>(input.db, input.cacheKey, input.revisionKey, input.validate);
    if (cached !== null) {
      const closingRevision = await input.readRevision();
      if (closingRevision !== input.revisionKey) return retryAfterDrift(closingRevision);
      return { payload: cached, status: "hit" };
    }
    const payload = await input.load();
    if (!input.validate(payload)) {
      throw new PublicApiError(503, "service_unavailable", input.invalidPayloadMessage);
    }
    const closingRevision = await input.readRevision();
    if (closingRevision !== input.revisionKey) return retryAfterDrift(closingRevision);
    await writeCachedPayload(input.db, input.cacheKey, input.revisionKey, payload);
    return { payload, status: "miss" };
  })();
  input.flights.set(flightKey, task as Promise<MarketOverviewCacheResult<unknown>>);
  try {
    return await task;
  } finally {
    input.flights.delete(flightKey);
  }
}

export async function getCachedMarketOverview<T>(
  db: MarketOverviewResponseCacheDatabase,
  identity: CacheIdentity,
  load: () => Promise<T>,
  validate: MarketCachePayloadValidator,
): Promise<MarketOverviewCacheResult<T>> {
  await ensureMarketSystemKpiCacheSchema(db);
  const [cacheKey, revisionKey] = await Promise.all([
    sha256(canonicalMarketOverviewCacheIdentity(identity)),
    getMarketOverviewCacheRevision(db, identity.salesRevision),
  ]);
  return loadRevisionFencedCache({
    db,
    cacheKey,
    revisionKey,
    readRevision: () => getMarketOverviewCacheRevision(db, identity.salesRevision),
    load,
    validate,
    flights: cacheFlightsForDatabase(overviewInFlightByDatabase, db),
    driftMessage: "市场分析数据在读取期间持续更新，请稍后重试",
    invalidPayloadMessage: "市场分析缓存数据结构无效，请稍后重试",
  });
}

export async function getCachedMarketFilterOptions<T>(
  db: MarketOverviewResponseCacheDatabase,
  load: () => Promise<T>,
  validate: MarketCachePayloadValidator,
): Promise<T> {
  await ensureMarketSystemKpiCacheSchema(db);
  const [cacheKey, revisionKey] = await Promise.all([
    sha256(JSON.stringify({ type: "market-filter-options", formatVersion: FILTER_OPTIONS_FORMAT_VERSION })),
    getMarketFilterOptionsRevision(db),
  ]);
  return (await loadRevisionFencedCache({
    db,
    cacheKey,
    revisionKey,
    readRevision: () => getMarketFilterOptionsRevision(db),
    load,
    validate,
    flights: cacheFlightsForDatabase(filterOptionsInFlightByDatabase, db),
    driftMessage: "市场筛选项在读取期间持续更新，请稍后重试",
    invalidPayloadMessage: "市场筛选项缓存数据结构无效，请稍后重试",
  })).payload;
}

export async function getCachedMarketMasterDatabaseFilters<T>(
  db: MarketOverviewResponseCacheDatabase,
  categories: string[],
  load: () => Promise<T>,
  validate: MarketCachePayloadValidator,
): Promise<T> {
  await ensureMarketMasterDatabaseFiltersCacheSchema(db);
  const normalizedCategories = normalizedList(categories);
  const [cacheKey, revisionKey] = await Promise.all([
    sha256(JSON.stringify({
      type: "market-master-database-filters",
      formatVersion: MASTER_DATABASE_FILTERS_FORMAT_VERSION,
      categories: normalizedCategories,
    })),
    getMarketMasterDatabaseFiltersRevision(db),
  ]);
  return (await loadRevisionFencedCache({
    db,
    cacheKey,
    revisionKey,
    readRevision: () => getMarketMasterDatabaseFiltersRevision(db),
    load,
    validate,
    flights: cacheFlightsForDatabase(masterDatabaseFiltersInFlightByDatabase, db),
    driftMessage: "市场主数据筛选项在读取期间持续更新，请稍后重试",
    invalidPayloadMessage: "市场主数据筛选项缓存结构无效，请稍后重试",
  })).payload;
}

export async function getCachedMarketSystemKpis<T>(
  db: MarketOverviewResponseCacheDatabase,
  load: () => Promise<T>,
  validate: MarketCachePayloadValidator,
): Promise<MarketOverviewCacheResult<T>> {
  await ensureMarketSystemKpiCacheSchema(db);
  const [cacheKey, revisionKey] = await Promise.all([
    sha256(JSON.stringify({ type: "market-system-kpis", formatVersion: SYSTEM_KPI_FORMAT_VERSION })),
    getMarketSystemKpiCacheRevision(db),
  ]);
  return loadRevisionFencedCache({
    db,
    cacheKey,
    revisionKey,
    readRevision: () => getMarketSystemKpiCacheRevision(db),
    load,
    validate,
    flights: cacheFlightsForDatabase(systemKpiInFlightByDatabase, db),
    driftMessage: "市场系统统计在读取期间持续更新，请稍后重试",
    invalidPayloadMessage: "市场系统统计缓存数据结构无效，请稍后重试",
  });
}
