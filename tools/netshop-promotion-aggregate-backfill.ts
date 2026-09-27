import { existsSync, realpathSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import {
  beginPromotionAggregateMaintenance,
  canUsePromotionAggregates,
  rebuildPromotionAggregates,
  type PromotionAggregateDatabase,
  type PromotionAggregateMaintenanceFence,
  type PromotionAggregateScope,
} from "../lib/netshop/promotion-aggregate";

const PROJECT_ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const MIGRATION_URLS = [
  new URL("../drizzle/0067_netshop_promotion_daily_aggregates.sql", import.meta.url),
  new URL("../drizzle/0070_netshop_promotion_aggregate_manifest.sql", import.meta.url),
  new URL("../drizzle/0071_netshop_promotion_snapshot_fence.sql", import.meta.url),
  new URL("../drizzle/0074_netshop_promotion_maintenance_fence.sql", import.meta.url),
] as const;
const MAX_CHUNK_DAYS = 31;

type Platform = "京东" | "天猫";

export type PromotionBackfillOptions = {
  database: string;
  apply: boolean;
  platform?: Platform;
  shop?: string;
  startDate?: string;
  endDate?: string;
};

export type PromotionBackfillChunkResult = {
  mode: "dry-run" | "apply";
  platform: Platform;
  shop: string;
  startDate: string;
  endDate: string;
  rawRows: number;
  productRows: number;
  readyDays: number | null;
  canUse: boolean | null;
  elapsedMs: number;
};

export type PromotionBackfillResult = {
  mode: "dry-run" | "apply";
  scopeCount: number;
  chunkCount: number;
  rawRows: number;
  productRows: number;
  readyDays: number | null;
  elapsedMs: number;
  manifestPlatforms: Platform[];
  chunks: PromotionBackfillChunkResult[];
};

type ScopeDiscoveryRow = {
  platform: Platform;
  shop_name: string;
  start_date: string;
  end_date: string;
};

type CountRow = { count: number };
type StateCountRow = {
  state_days: number;
  ready_days: number;
  raw_row_count: number;
  product_row_count: number;
};

type PlatformManifestCounts = {
  source_shop_count: number;
  historical_data_cutoff: string | null;
  raw_row_count: number;
  product_row_count: number;
  shop_day_count: number;
  state_day_count: number;
  ready_state_day_count: number;
  state_raw_row_count: number;
  state_product_row_count: number;
};

class SqliteAggregateStatement {
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
      return new SqliteAggregateStatement(sqlite, sql);
    },
    async batch(statements: SqliteAggregateStatement[]) {
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

function isIsoDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

function requiredValue(args: readonly string[], index: number, option: string) {
  const value = args[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`${option} 必须提供值`);
  return value;
}

export function parsePromotionBackfillArgs(args: readonly string[]): PromotionBackfillOptions {
  let database = "";
  let apply = false;
  let explicitDryRun = false;
  let platform: Platform | undefined;
  let shop: string | undefined;
  let startDate: string | undefined;
  let endDate: string | undefined;

  const seen = new Set<string>();
  for (let index = 0; index < args.length; index += 1) {
    const option = args[index];
    if (seen.has(option)) throw new Error(`参数重复: ${option}`);
    seen.add(option);
    if (option === "--apply") {
      apply = true;
      continue;
    }
    if (option === "--dry-run") {
      explicitDryRun = true;
      continue;
    }
    if (!["--database", "--platform", "--shop", "--start-date", "--end-date"].includes(option)) {
      throw new Error(`不支持的参数: ${option}`);
    }
    const value = requiredValue(args, index, option);
    index += 1;
    if (option === "--database") database = value;
    if (option === "--platform") {
      if (value !== "京东" && value !== "天猫") throw new Error("--platform 只接受 京东 或 天猫");
      platform = value;
    }
    if (option === "--shop") {
      shop = value.trim();
      if (!shop) throw new Error("--shop 不能为空");
    }
    if (option === "--start-date") startDate = value;
    if (option === "--end-date") endDate = value;
  }

  if (!database) throw new Error("必须显式提供 --database 绝对路径");
  if (apply && explicitDryRun) throw new Error("--apply 与 --dry-run 不能同时使用");
  if (!isAbsolute(database)) throw new Error("--database 必须是绝对路径");
  if (startDate && !isIsoDate(startDate)) throw new Error("--start-date 必须是有效的 YYYY-MM-DD 日期");
  if (endDate && !isIsoDate(endDate)) throw new Error("--end-date 必须是有效的 YYYY-MM-DD 日期");
  if (startDate && endDate && startDate > endDate) throw new Error("开始日期不能晚于结束日期");
  return { database, apply, platform, shop, startDate, endDate };
}

function validateDatabasePath(databasePath: string) {
  if (!existsSync(databasePath)) throw new Error("指定的数据库文件不存在");
  if (!statSync(databasePath).isFile()) throw new Error("指定的数据库路径不是文件");
  const projectRoot = realpathSync(PROJECT_ROOT);
  const realDatabasePath = realpathSync(databasePath);
  const projectRelative = relative(projectRoot, realDatabasePath);
  if (!projectRelative || projectRelative === ".." || projectRelative.startsWith(`..\\`) || projectRelative.startsWith("../") || isAbsolute(projectRelative)) {
    throw new Error("拒绝操作当前项目目录之外的数据库文件");
  }
  return realDatabasePath;
}

const REQUIRED_RAW_COLUMNS = [
  "source", "dataset", "platform", "shop_name", "business_date", "sku_id", "spu_id",
  "product_name", "metrics_json", "raw_json", "last_import_batch_id",
] as const;

function validateProjectDatabase(sqlite: DatabaseSync) {
  const table = sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get("netshop_rows");
  if (!table) throw new Error("指定文件不是本项目的网店数据库：缺少 netshop_rows");
  const columns = new Set((sqlite.prepare("PRAGMA table_info(netshop_rows)").all() as Array<{ name: string }>).map((row) => row.name));
  const missing = REQUIRED_RAW_COLUMNS.filter((column) => !columns.has(column));
  if (missing.length) throw new Error("指定文件不是兼容的本项目网店数据库：netshop_rows 结构不完整");
}

function idempotentMigrationStatement(statement: string) {
  return statement
    .replace(/^CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)/i, "CREATE TABLE IF NOT EXISTS ")
    .replace(/^CREATE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS)/i, "CREATE INDEX IF NOT EXISTS ")
    .replace(/^CREATE\s+TRIGGER\s+(?!IF\s+NOT\s+EXISTS)/i, "CREATE TRIGGER IF NOT EXISTS ");
}

async function applyAggregateMigration(sqlite: DatabaseSync) {
  const migrations = await Promise.all(MIGRATION_URLS.map((url) => readFile(url, "utf8")));
  const statements = migrations.flatMap((migration) => migration
    .split(/--> statement-breakpoint\s*/).map((sql) => sql.trim()).filter(Boolean));
  if (!statements.length) throw new Error("推广聚合迁移为空");
  sqlite.exec("BEGIN IMMEDIATE");
  try {
    for (const statement of statements) sqlite.exec(idempotentMigrationStatement(statement));
    sqlite.exec("COMMIT");
  } catch (error) {
    sqlite.exec("ROLLBACK");
    throw error;
  }
}

function discoverScopes(sqlite: DatabaseSync, options: PromotionBackfillOptions) {
  const predicates = [`(
    (platform = '京东' AND source = 'jd_promotion' AND dataset = 'ad' AND sku_id <> '')
    OR (platform = '天猫' AND source = 'tmall_promotion' AND dataset = 'promotion_daily' AND spu_id <> '')
  )`, "business_date IS NOT NULL", "business_date <> ''"];
  const bindings: SQLInputValue[] = [];
  if (options.platform) {
    predicates.push("platform = ?");
    bindings.push(options.platform);
  }
  if (options.shop) {
    predicates.push("shop_name = ?");
    bindings.push(options.shop);
  }
  if (options.startDate) {
    predicates.push("business_date >= ?");
    bindings.push(options.startDate);
  }
  if (options.endDate) {
    predicates.push("business_date <= ?");
    bindings.push(options.endDate);
  }
  const rows = sqlite.prepare(`SELECT platform, shop_name, MIN(business_date) AS start_date, MAX(business_date) AS end_date
    FROM netshop_rows
    WHERE ${predicates.join(" AND ")}
    GROUP BY platform, shop_name
    ORDER BY platform, shop_name`).all(...bindings) as ScopeDiscoveryRow[];
  for (const row of rows) {
    if ((row.platform !== "京东" && row.platform !== "天猫") || !row.shop_name || !isIsoDate(row.start_date) || !isIsoDate(row.end_date)) {
      throw new Error("推广原始数据包含无效的平台、店铺或业务日期范围");
    }
  }
  return rows;
}

function addDays(date: string, days: number) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

function chunkRange(startDate: string, endDate: string) {
  const chunks: Array<{ startDate: string; endDate: string }> = [];
  for (let start = startDate; start <= endDate;) {
    const candidateEnd = addDays(start, MAX_CHUNK_DAYS - 1);
    const chunkEnd = candidateEnd < endDate ? candidateEnd : endDate;
    chunks.push({ startDate: start, endDate: chunkEnd });
    start = addDays(chunkEnd, 1);
  }
  return chunks;
}

function sourceScope(platform: Platform, shopName: string, startDate: string, endDate: string): PromotionAggregateScope {
  return platform === "京东"
    ? { source: "jd_promotion", dataset: "ad", platform, shopName, startDate, endDate }
    : { source: "tmall_promotion", dataset: "promotion_daily", platform, shopName, startDate, endDate };
}

function rawPredicate(platform: Platform, alias = "") {
  const prefix = alias ? `${alias}.` : "";
  return platform === "京东"
    ? `${prefix}source = 'jd_promotion' AND ${prefix}dataset = 'ad' AND ${prefix}sku_id <> ''`
    : `${prefix}source = 'tmall_promotion' AND ${prefix}dataset = 'promotion_daily' AND ${prefix}spu_id <> ''`;
}

function scopeBindings(scope: PromotionAggregateScope): SQLInputValue[] {
  return [scope.platform, scope.shopName, scope.startDate, scope.endDate];
}

function rawCounts(sqlite: DatabaseSync, scope: PromotionAggregateScope) {
  const productId = scope.platform === "京东" ? "sku_id" : "spu_id";
  const where = `${rawPredicate(scope.platform as Platform)}
    AND platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?`;
  const bindings = scopeBindings(scope);
  const rawRows = (sqlite.prepare(`SELECT COUNT(*) AS count FROM netshop_rows WHERE ${where}`).get(...bindings) as CountRow).count;
  const rawDays = (sqlite.prepare(`SELECT COUNT(DISTINCT business_date) AS count FROM netshop_rows WHERE ${where}`).get(...bindings) as CountRow).count;
  const productRows = (sqlite.prepare(`SELECT COUNT(*) AS count FROM (
    SELECT business_date, ${productId} FROM netshop_rows WHERE ${where} GROUP BY business_date, ${productId}
  )`).get(...bindings) as CountRow).count;
  return { rawRows, rawDays, productRows };
}

function installCompletePlatformManifest(
  sqlite: DatabaseSync,
  platform: Platform,
  fence: PromotionAggregateMaintenanceFence,
) {
  const rawWhere = rawPredicate(platform);
  const aliasedRawWhere = rawPredicate(platform, "r");
  sqlite.exec("BEGIN IMMEDIATE");
  try {
    const raw = sqlite.prepare(`SELECT COUNT(*) AS raw_row_count,
        COUNT(DISTINCT shop_name) AS source_shop_count,
        MAX(business_date) AS historical_data_cutoff
      FROM netshop_rows
      WHERE ${rawWhere} AND platform = ?`).get(platform) as Pick<PlatformManifestCounts,
        "raw_row_count" | "source_shop_count" | "historical_data_cutoff">;
    const product = sqlite.prepare(`SELECT COUNT(*) AS product_row_count
      FROM netshop_promotion_product_daily WHERE platform = ?`).get(platform) as Pick<PlatformManifestCounts, "product_row_count">;
    const shop = sqlite.prepare(`SELECT COUNT(*) AS shop_day_count
      FROM netshop_promotion_shop_daily WHERE platform = ?`).get(platform) as Pick<PlatformManifestCounts, "shop_day_count">;
    const state = sqlite.prepare(`SELECT COUNT(*) AS state_day_count,
        COALESCE(SUM(CASE WHEN ready = 1 THEN 1 ELSE 0 END), 0) AS ready_state_day_count,
        COALESCE(SUM(raw_row_count), 0) AS state_raw_row_count,
        COALESCE(SUM(product_row_count), 0) AS state_product_row_count
      FROM netshop_promotion_aggregate_state WHERE platform = ?`).get(platform) as Pick<PlatformManifestCounts,
        "state_day_count" | "ready_state_day_count" | "state_raw_row_count" | "state_product_row_count">;
    const rawStateMismatch = sqlite.prepare(`SELECT 1
      FROM (
        SELECT r.shop_name,r.business_date,COUNT(*) AS raw_row_count
        FROM netshop_rows r
        WHERE ${aliasedRawWhere} AND r.platform = ?
        GROUP BY r.shop_name,r.business_date
      ) raw
      LEFT JOIN netshop_promotion_aggregate_state state
        ON state.platform = ? AND state.shop_name = raw.shop_name AND state.business_date = raw.business_date
      WHERE COALESCE(state.ready,0) <> 1 OR state.raw_row_count <> raw.raw_row_count
      LIMIT 1`).get(platform, platform);
    const stateRawMismatch = sqlite.prepare(`SELECT 1
      FROM netshop_promotion_aggregate_state state
      LEFT JOIN (
        SELECT r.shop_name,r.business_date,COUNT(*) AS raw_row_count
        FROM netshop_rows r
        WHERE ${aliasedRawWhere} AND r.platform = ?
        GROUP BY r.shop_name,r.business_date
      ) raw
        ON raw.shop_name = state.shop_name AND raw.business_date = state.business_date
      WHERE state.platform = ? AND state.ready = 1
        AND (raw.shop_name IS NULL OR raw.raw_row_count <> state.raw_row_count)
      LIMIT 1`).get(platform, platform);
    const stateAggregateMismatch = sqlite.prepare(`SELECT 1
      FROM netshop_promotion_aggregate_state state
      LEFT JOIN (
        SELECT shop_name,business_date,COUNT(*) AS product_row_count
        FROM netshop_promotion_product_daily
        WHERE platform = ?
        GROUP BY shop_name,business_date
      ) product
        ON product.shop_name = state.shop_name AND product.business_date = state.business_date
      LEFT JOIN netshop_promotion_shop_daily shop
        ON shop.platform = state.platform AND shop.shop_name = state.shop_name AND shop.business_date = state.business_date
      WHERE state.platform = ? AND state.ready = 1 AND (
        COALESCE(product.product_row_count,-1) <> state.product_row_count OR
        shop.platform IS NULL OR shop.product_count <> state.product_row_count
      )
      LIMIT 1`).get(platform, platform);
    const aggregateOrphan = sqlite.prepare(`SELECT 1 FROM (
        SELECT p.shop_name,p.business_date
        FROM netshop_promotion_product_daily p
        LEFT JOIN netshop_promotion_aggregate_state state
          ON state.platform=p.platform AND state.shop_name=p.shop_name
         AND state.business_date=p.business_date AND state.ready=1
        WHERE p.platform=? AND state.platform IS NULL
        UNION ALL
        SELECT s.shop_name,s.business_date
        FROM netshop_promotion_shop_daily s
        LEFT JOIN netshop_promotion_aggregate_state state
          ON state.platform=s.platform AND state.shop_name=s.shop_name
         AND state.business_date=s.business_date AND state.ready=1
        WHERE s.platform=? AND state.platform IS NULL
      ) orphan LIMIT 1`).get(platform, platform);
    const counts: PlatformManifestCounts = { ...raw, ...product, ...shop, ...state };
    const complete = counts.raw_row_count > 0
      && counts.source_shop_count > 0
      && counts.state_day_count === counts.ready_state_day_count
      && counts.state_day_count === counts.shop_day_count
      && counts.state_raw_row_count === counts.raw_row_count
      && counts.state_product_row_count === counts.product_row_count
      && !rawStateMismatch
      && !stateRawMismatch
      && !stateAggregateMismatch
      && !aggregateOrphan;
    if (!complete) throw new Error(`推广聚合平台完整性验证失败: ${platform}`);
    const publication = sqlite.prepare(`UPDATE netshop_promotion_aggregate_manifest
      SET ready=1,
        historical_data_cutoff=?,
        source_shop_count=?,
        raw_row_count=?,
        product_row_count=?,
        shop_day_count=?,
        state_day_count=?,
        completed_at=CURRENT_TIMESTAMP,
        invalidated_at=CURRENT_TIMESTAMP
      WHERE platform=? AND ready=0 AND data_version=?
        AND EXISTS (
          SELECT 1 FROM netshop_promotion_aggregate_control control
          WHERE control.platform=? AND control.maintenance_token=?
            AND control.maintenance_version=?
        )`).run(
      counts.historical_data_cutoff,
      counts.source_shop_count,
      counts.raw_row_count,
      counts.product_row_count,
      counts.shop_day_count,
      counts.state_day_count,
      platform,
      fence.dataVersion,
      platform,
      fence.token,
      fence.dataVersion,
    );
    if (Number(publication.changes) !== 1) throw new Error(`推广聚合平台维护栅栏已失效: ${platform}`);
    const release = sqlite.prepare(`UPDATE netshop_promotion_aggregate_control
      SET maintenance_token='', maintenance_version=0,
        maintenance_previous_ready=0, maintenance_started_at=NULL,
        updated_at=CURRENT_TIMESTAMP
      WHERE platform=? AND maintenance_token=? AND maintenance_version=?`).run(
      platform,
      fence.token,
      fence.dataVersion,
    );
    if (Number(release.changes) !== 1) throw new Error(`推广聚合平台维护栅栏释放失败: ${platform}`);
    sqlite.exec("COMMIT");
  } catch (error) {
    sqlite.exec("ROLLBACK");
    throw error;
  }
}

async function verifyAppliedChunk(sqlite: DatabaseSync, db: PromotionAggregateDatabase, scope: PromotionAggregateScope) {
  const expected = rawCounts(sqlite, scope);
  const bindings = scopeBindings(scope);
  const actualProductRows = (sqlite.prepare(`SELECT COUNT(*) AS count FROM netshop_promotion_product_daily
    WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?`).get(...bindings) as CountRow).count;
  const shopDays = (sqlite.prepare(`SELECT COUNT(*) AS count FROM netshop_promotion_shop_daily
    WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?`).get(...bindings) as CountRow).count;
  const state = sqlite.prepare(`SELECT COUNT(*) AS state_days,
      COALESCE(SUM(CASE WHEN ready = 1 THEN 1 ELSE 0 END), 0) AS ready_days,
      COALESCE(SUM(raw_row_count), 0) AS raw_row_count,
      COALESCE(SUM(product_row_count), 0) AS product_row_count
    FROM netshop_promotion_aggregate_state
    WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?`).get(...bindings) as StateCountRow;
  const canUse = await canUsePromotionAggregates(db, {
    platform: scope.platform,
    shopNames: [scope.shopName],
    startDate: scope.startDate,
    endDate: scope.endDate,
  });
  const valid = actualProductRows === expected.productRows
    && state.product_row_count === expected.productRows
    && state.raw_row_count === expected.rawRows
    && state.state_days === expected.rawDays
    && state.ready_days === expected.rawDays
    && shopDays === expected.rawDays;
  if (!valid) throw new Error("推广聚合回填验证失败：计数、ready 状态或完整性门禁不一致");
  return { ...expected, actualProductRows, readyDays: state.ready_days, canUse };
}

export async function runPromotionAggregateBackfill(
  options: PromotionBackfillOptions,
  logger: (line: string) => void = console.log,
): Promise<PromotionBackfillResult> {
  const startedAt = Date.now();
  const databasePath = validateDatabasePath(options.database);
  const sqlite = new DatabaseSync(databasePath, { readOnly: !options.apply });
  try {
    if (!options.apply) sqlite.exec("PRAGMA query_only = ON");
    validateProjectDatabase(sqlite);
    const scopes = discoverScopes(sqlite, options);
    if (options.apply) await applyAggregateMigration(sqlite);
    const db = aggregateAdapter(sqlite);
    const targetPlatforms = [...new Set(scopes.map((scope) => scope.platform))];
    const maintenanceFences = new Map<Platform, PromotionAggregateMaintenanceFence>();
    if (options.apply) {
      for (const platform of targetPlatforms) {
        maintenanceFences.set(platform, await beginPromotionAggregateMaintenance(db, platform));
      }
    }
    const chunks: PromotionBackfillChunkResult[] = [];
    for (const discovered of scopes) {
      for (const range of chunkRange(discovered.start_date, discovered.end_date)) {
        const chunkStartedAt = Date.now();
        const scope = sourceScope(discovered.platform, discovered.shop_name, range.startDate, range.endDate);
        const before = rawCounts(sqlite, scope);
        let readyDays: number | null = null;
        let canUse: boolean | null = null;
        let productRows = before.productRows;
        if (options.apply) {
          const fence = maintenanceFences.get(discovered.platform);
          if (!fence) throw new Error(`推广聚合平台缺少维护栅栏: ${discovered.platform}`);
          await rebuildPromotionAggregates(db, scope, fence);
          const after = rawCounts(sqlite, scope);
          if (after.rawRows !== before.rawRows || after.rawDays !== before.rawDays || after.productRows !== before.productRows) {
            throw new Error("推广聚合回填期间原始数据计数发生变化，已停止后续分块");
          }
          const verified = await verifyAppliedChunk(sqlite, db, scope);
          productRows = verified.actualProductRows;
          readyDays = verified.readyDays;
          canUse = verified.canUse;
        }
        const result: PromotionBackfillChunkResult = {
          mode: options.apply ? "apply" : "dry-run",
          platform: discovered.platform,
          shop: discovered.shop_name,
          startDate: range.startDate,
          endDate: range.endDate,
          rawRows: before.rawRows,
          productRows,
          readyDays,
          canUse,
          elapsedMs: Date.now() - chunkStartedAt,
        };
        chunks.push(result);
        logger(JSON.stringify(result));
      }
    }
    const completePlatformRequested = !options.shop && !options.startDate && !options.endDate;
    const manifestPlatforms = options.apply
      ? targetPlatforms.filter((platform) => completePlatformRequested || maintenanceFences.get(platform)?.previousReady)
      : [];
    for (const platform of manifestPlatforms) {
      const fence = maintenanceFences.get(platform);
      if (!fence) throw new Error(`推广聚合平台缺少完成栅栏: ${platform}`);
      installCompletePlatformManifest(sqlite, platform, fence);
    }
    for (const chunk of chunks) {
      if (!manifestPlatforms.includes(chunk.platform)) continue;
      chunk.canUse = await canUsePromotionAggregates(db, {
        platform: chunk.platform,
        shopNames: [chunk.shop],
        startDate: chunk.startDate,
        endDate: chunk.endDate,
      });
    }
    const result: PromotionBackfillResult = {
      mode: options.apply ? "apply" : "dry-run",
      scopeCount: scopes.length,
      chunkCount: chunks.length,
      rawRows: chunks.reduce((sum, chunk) => sum + chunk.rawRows, 0),
      productRows: chunks.reduce((sum, chunk) => sum + chunk.productRows, 0),
      readyDays: options.apply ? chunks.reduce((sum, chunk) => sum + (chunk.readyDays ?? 0), 0) : null,
      elapsedMs: Date.now() - startedAt,
      manifestPlatforms,
      chunks,
    };
    logger(JSON.stringify({ ...result, chunks: undefined }));
    return result;
  } finally {
    sqlite.close();
  }
}

async function main() {
  throw new Error("D1 推广聚合维护已退役；当前业务维护必须使用 Django/PostgreSQL。该模块仅保留隔离历史审计与测试导入。");
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "未知错误";
    console.error(`推广聚合回填失败: ${message}`);
    process.exitCode = 1;
  });
}
