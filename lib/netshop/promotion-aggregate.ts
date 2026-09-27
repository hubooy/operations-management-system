import type { NetshopRowInput, NetshopSource } from "@/lib/netshop/database";
import { netshopPromotionMetrics } from "@/lib/netshop/promotion-query";

type PromotionAggregateStatement = {
  bind(...values: unknown[]): PromotionAggregateStatement;
  first<T = unknown>(): Promise<T | null>;
  all<T = unknown>(): Promise<{ results?: T[] }>;
  run(): Promise<unknown>;
};

export type PromotionAggregateDatabase = {
  prepare(sql: string): PromotionAggregateStatement;
  batch(statements: PromotionAggregateStatement[]): Promise<unknown>;
};

export type PromotionAggregateScope = {
  source: "jd_promotion" | "tmall_promotion";
  dataset: "ad" | "promotion_daily";
  platform: string;
  shopName: string;
  startDate: string;
  endDate: string;
};

export type PromotionProductDailyAggregate = {
  source: PromotionAggregateScope["source"];
  platform: string;
  shopName: string;
  businessDate: string;
  productId: string;
  productName: string;
  productLine: string;
  spendCents: number;
  netTransactionAmountCents: number;
  grossTransactionAmountCents: number;
  impressions: number;
  clicks: number;
  netOrders: number;
  favorites: number;
  cartQuantity: number;
  sourceRowCount: number;
  sourceBatchId: string;
  sourceBatchCount: number;
};

export type PromotionShopDailyAggregate = Omit<
  PromotionProductDailyAggregate,
  "productId" | "productName" | "productLine"
> & { productCount: number };

export type PromotionAggregateSet = {
  products: PromotionProductDailyAggregate[];
  shops: PromotionShopDailyAggregate[];
};

export const PROMOTION_AGGREGATE_REBUILD_MAX_DAYS = 31;

export const PROMOTION_AGGREGATE_PRODUCT_FIELDS = {
  spendCents: "p.spend_cents",
  netTransactionAmountCents: "p.net_transaction_amount_cents",
  grossTransactionAmountCents: "p.gross_transaction_amount_cents",
  impressions: "p.impressions",
  clicks: "p.clicks",
  netOrders: "p.net_orders",
  favorites: "p.favorites",
  cartQuantity: "p.cart_quantity",
} as const;

export const PROMOTION_AGGREGATE_SHOP_FIELDS = {
  productCount: "s.product_count",
  spendCents: "s.spend_cents",
  netTransactionAmountCents: "s.net_transaction_amount_cents",
  grossTransactionAmountCents: "s.gross_transaction_amount_cents",
  impressions: "s.impressions",
  clicks: "s.clicks",
  netOrders: "s.net_orders",
  favorites: "s.favorites",
  cartQuantity: "s.cart_quantity",
} as const;

export const PROMOTION_AGGREGATE_PRODUCT_READY_JOIN_SQL = `INNER JOIN netshop_promotion_aggregate_state aggregate_state
  ON aggregate_state.platform = p.platform
 AND aggregate_state.shop_name = p.shop_name
 AND aggregate_state.business_date = p.business_date
 AND aggregate_state.ready = 1`;

export const PROMOTION_AGGREGATE_SHOP_READY_JOIN_SQL = `INNER JOIN netshop_promotion_aggregate_state aggregate_state
  ON aggregate_state.platform = s.platform
 AND aggregate_state.shop_name = s.shop_name
 AND aggregate_state.business_date = s.business_date
 AND aggregate_state.ready = 1`;

const productTableSql = `CREATE TABLE IF NOT EXISTS netshop_promotion_product_daily (
  platform TEXT NOT NULL,
  shop_name TEXT NOT NULL,
  business_date TEXT NOT NULL,
  product_id TEXT NOT NULL,
  source TEXT NOT NULL,
  product_name TEXT NOT NULL DEFAULT '',
  product_line TEXT NOT NULL DEFAULT '',
  spend_cents INTEGER NOT NULL DEFAULT 0,
  net_transaction_amount_cents INTEGER NOT NULL DEFAULT 0,
  gross_transaction_amount_cents INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  clicks INTEGER NOT NULL DEFAULT 0,
  net_orders INTEGER NOT NULL DEFAULT 0,
  favorites INTEGER NOT NULL DEFAULT 0,
  cart_quantity INTEGER NOT NULL DEFAULT 0,
  source_row_count INTEGER NOT NULL DEFAULT 0,
  source_batch_id TEXT NOT NULL DEFAULT '',
  source_batch_count INTEGER NOT NULL DEFAULT 0,
  rebuilt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (platform, shop_name, business_date, product_id)
)`;

const shopTableSql = `CREATE TABLE IF NOT EXISTS netshop_promotion_shop_daily (
  platform TEXT NOT NULL,
  shop_name TEXT NOT NULL,
  business_date TEXT NOT NULL,
  source TEXT NOT NULL,
  product_count INTEGER NOT NULL DEFAULT 0,
  spend_cents INTEGER NOT NULL DEFAULT 0,
  net_transaction_amount_cents INTEGER NOT NULL DEFAULT 0,
  gross_transaction_amount_cents INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  clicks INTEGER NOT NULL DEFAULT 0,
  net_orders INTEGER NOT NULL DEFAULT 0,
  favorites INTEGER NOT NULL DEFAULT 0,
  cart_quantity INTEGER NOT NULL DEFAULT 0,
  source_row_count INTEGER NOT NULL DEFAULT 0,
  source_batch_id TEXT NOT NULL DEFAULT '',
  source_batch_count INTEGER NOT NULL DEFAULT 0,
  rebuilt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (platform, shop_name, business_date)
)`;

const stateTableSql = `CREATE TABLE IF NOT EXISTS netshop_promotion_aggregate_state (
  platform TEXT NOT NULL,
  shop_name TEXT NOT NULL,
  business_date TEXT NOT NULL,
  source TEXT NOT NULL,
  ready INTEGER NOT NULL DEFAULT 0 CHECK (ready IN (0, 1)),
  raw_row_count INTEGER NOT NULL DEFAULT 0,
  product_row_count INTEGER NOT NULL DEFAULT 0,
  source_batch_id TEXT NOT NULL DEFAULT '',
  source_batch_count INTEGER NOT NULL DEFAULT 0,
  rebuilt_at TEXT,
  invalidated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (platform, shop_name, business_date)
)`;

const manifestTableSql = `CREATE TABLE IF NOT EXISTS netshop_promotion_aggregate_manifest (
  platform TEXT NOT NULL PRIMARY KEY,
  ready INTEGER NOT NULL DEFAULT 0 CHECK (ready IN (0, 1)),
  historical_data_cutoff TEXT,
  source_shop_count INTEGER NOT NULL DEFAULT 0,
  raw_row_count INTEGER NOT NULL DEFAULT 0,
  product_row_count INTEGER NOT NULL DEFAULT 0,
  shop_day_count INTEGER NOT NULL DEFAULT 0,
  state_day_count INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT,
  invalidated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  data_version INTEGER NOT NULL DEFAULT 0
)`;

const scopeRevisionTableSql = `CREATE TABLE IF NOT EXISTS netshop_promotion_scope_revisions (
  platform TEXT NOT NULL,
  shop_name TEXT NOT NULL,
  data_version INTEGER NOT NULL DEFAULT 0 CHECK (data_version >= 0),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (platform, shop_name)
)`;

const aggregateControlTableSql = `CREATE TABLE IF NOT EXISTS netshop_promotion_aggregate_control (
  platform TEXT NOT NULL PRIMARY KEY,
  bootstrap_batch_id TEXT NOT NULL DEFAULT '',
  bootstrap_raw_row_count INTEGER NOT NULL DEFAULT 0 CHECK (bootstrap_raw_row_count >= 0),
  bootstrap_product_row_count INTEGER NOT NULL DEFAULT 0 CHECK (bootstrap_product_row_count >= 0),
  bootstrap_shop_day_count INTEGER NOT NULL DEFAULT 0 CHECK (bootstrap_shop_day_count >= 0),
  bootstrap_data_cutoff TEXT,
  maintenance_token TEXT NOT NULL DEFAULT '',
  maintenance_version INTEGER NOT NULL DEFAULT 0 CHECK (maintenance_version >= 0),
  maintenance_previous_ready INTEGER NOT NULL DEFAULT 0 CHECK (maintenance_previous_ready IN (0, 1)),
  maintenance_started_at TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)`;

const eligiblePromotionRawSql = (alias: string) => `
  ${alias}.business_date IS NOT NULL AND ${alias}.business_date <> '' AND (
    (${alias}.source = 'jd_promotion' AND ${alias}.dataset = 'ad' AND ${alias}.sku_id <> '') OR
    (${alias}.source = 'tmall_promotion' AND ${alias}.dataset = 'promotion_daily' AND ${alias}.spu_id <> '')
  )`;

const shouldBumpRevisionSql = (rowAlias: "OLD" | "NEW") => `(
  NOT EXISTS (
    SELECT 1 FROM netshop_promotion_aggregate_state
    WHERE platform = ${rowAlias}.platform
      AND shop_name = ${rowAlias}.shop_name
      AND business_date = ${rowAlias}.business_date
  ) OR EXISTS (
    SELECT 1 FROM netshop_promotion_aggregate_state
    WHERE platform = ${rowAlias}.platform
      AND shop_name = ${rowAlias}.shop_name
      AND business_date = ${rowAlias}.business_date
      AND ready = 1
  )
)`;

const bumpManifestVersionSql = (rowAlias: "OLD" | "NEW") => `
  UPDATE netshop_promotion_aggregate_manifest
  SET data_version = data_version + 1,
    invalidated_at = CURRENT_TIMESTAMP
  WHERE platform = ${rowAlias}.platform AND ${shouldBumpRevisionSql(rowAlias)};`;

const bumpScopeRevisionSql = (rowAlias: "OLD" | "NEW") => `
  INSERT INTO netshop_promotion_scope_revisions (platform, shop_name, data_version, updated_at)
  SELECT ${rowAlias}.platform, ${rowAlias}.shop_name, 1, CURRENT_TIMESTAMP
  WHERE ${shouldBumpRevisionSql(rowAlias)}
  ON CONFLICT(platform, shop_name) DO UPDATE SET
    data_version = netshop_promotion_scope_revisions.data_version + 1,
    updated_at = CURRENT_TIMESTAMP;`;

const invalidateStateSql = (rowAlias: "OLD" | "NEW") => `
  INSERT INTO netshop_promotion_aggregate_state (
    platform, shop_name, business_date, source, ready, invalidated_at
  ) VALUES (
    ${rowAlias}.platform, ${rowAlias}.shop_name, ${rowAlias}.business_date,
    ${rowAlias}.source, 0, CURRENT_TIMESTAMP
  )
  ON CONFLICT(platform, shop_name, business_date) DO UPDATE SET
    source = excluded.source,
    ready = 0,
    invalidated_at = CURRENT_TIMESTAMP;`;

const promotionAggregateBaseSchemaStatements = [
  productTableSql,
  shopTableSql,
  stateTableSql,
  manifestTableSql,
  scopeRevisionTableSql,
  aggregateControlTableSql,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_product_daily_platform_date_shop_idx
    ON netshop_promotion_product_daily (platform, business_date, shop_name)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_product_daily_scope_date_product_idx
    ON netshop_promotion_product_daily (platform, shop_name, business_date, product_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_product_daily_scope_product_date_idx
    ON netshop_promotion_product_daily (platform, shop_name, product_id, business_date)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_shop_daily_platform_date_shop_idx
    ON netshop_promotion_shop_daily (platform, business_date, shop_name)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_aggregate_state_ready_scope_date_idx
    ON netshop_promotion_aggregate_state (ready, platform, shop_name, business_date)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_aggregate_state_stale_platform_date_idx
    ON netshop_promotion_aggregate_state (ready, platform, business_date)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_raw_platform_batch_idx
    ON netshop_rows (platform, last_import_batch_id)
    WHERE business_date IS NOT NULL AND business_date <> '' AND (
      (source = 'jd_promotion' AND dataset = 'ad' AND sku_id <> '') OR
      (source = 'tmall_promotion' AND dataset = 'promotion_daily' AND spu_id <> '')
    )`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_product_daily_platform_batch_idx
    ON netshop_promotion_product_daily (platform, source_batch_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_shop_daily_platform_batch_idx
    ON netshop_promotion_shop_daily (platform, source_batch_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_promotion_aggregate_state_platform_batch_ready_idx
    ON netshop_promotion_aggregate_state (platform, source_batch_id, ready)`,
] as const;

const promotionAggregateTriggerNames = [
  "netshop_promotion_aggregate_raw_insert",
  "netshop_promotion_aggregate_raw_delete",
  "netshop_promotion_aggregate_raw_update_old",
  "netshop_promotion_aggregate_raw_update_new",
  "netshop_promotion_aggregate_bootstrap_after_insert",
] as const;

const bootstrapTriggerSql = `CREATE TRIGGER IF NOT EXISTS netshop_promotion_aggregate_bootstrap_after_insert
  AFTER INSERT ON netshop_promotion_aggregate_control
  WHEN NEW.bootstrap_batch_id <> '' AND NEW.maintenance_token = ''
  BEGIN
    UPDATE netshop_promotion_aggregate_manifest
    SET ready = 1,
      historical_data_cutoff = NEW.bootstrap_data_cutoff,
      source_shop_count = 1,
      raw_row_count = NEW.bootstrap_raw_row_count,
      product_row_count = NEW.bootstrap_product_row_count,
      shop_day_count = NEW.bootstrap_shop_day_count,
      state_day_count = NEW.bootstrap_shop_day_count,
      completed_at = CURRENT_TIMESTAMP,
      invalidated_at = CURRENT_TIMESTAMP
    WHERE platform = NEW.platform
      AND ready = 0
      AND EXISTS (
        SELECT 1 FROM netshop_import_batches
        WHERE id = NEW.bootstrap_batch_id AND status = 'processing'
      )
      AND NOT EXISTS (
        SELECT 1 FROM netshop_rows r
        WHERE r.platform = NEW.platform
          AND ${eligiblePromotionRawSql("r")}
          AND r.last_import_batch_id <> NEW.bootstrap_batch_id
        LIMIT 1
      )
      AND (
        SELECT COUNT(*) FROM netshop_rows r
        WHERE r.platform = NEW.platform
          AND ${eligiblePromotionRawSql("r")}
          AND r.last_import_batch_id = NEW.bootstrap_batch_id
      ) = NEW.bootstrap_raw_row_count
      AND NOT EXISTS (
        SELECT 1 FROM netshop_promotion_product_daily p
        WHERE p.platform = NEW.platform AND p.source_batch_id <> NEW.bootstrap_batch_id
        LIMIT 1
      )
      AND (
        SELECT COUNT(*) FROM netshop_promotion_product_daily p
        WHERE p.platform = NEW.platform AND p.source_batch_id = NEW.bootstrap_batch_id
      ) = NEW.bootstrap_product_row_count
      AND NOT EXISTS (
        SELECT 1 FROM netshop_promotion_shop_daily s
        WHERE s.platform = NEW.platform AND s.source_batch_id <> NEW.bootstrap_batch_id
        LIMIT 1
      )
      AND (
        SELECT COUNT(*) FROM netshop_promotion_shop_daily s
        WHERE s.platform = NEW.platform AND s.source_batch_id = NEW.bootstrap_batch_id
      ) = NEW.bootstrap_shop_day_count
      AND NOT EXISTS (
        SELECT 1 FROM netshop_promotion_aggregate_state state
        WHERE state.platform = NEW.platform AND (
          state.source_batch_id <> NEW.bootstrap_batch_id OR state.ready <> 1
        )
        LIMIT 1
      )
      AND (
        SELECT COUNT(*) FROM netshop_promotion_aggregate_state state
        WHERE state.platform = NEW.platform
          AND state.source_batch_id = NEW.bootstrap_batch_id AND state.ready = 1
      ) = NEW.bootstrap_shop_day_count
      AND COALESCE((
        SELECT SUM(state.raw_row_count) FROM netshop_promotion_aggregate_state state
        WHERE state.platform = NEW.platform
          AND state.source_batch_id = NEW.bootstrap_batch_id AND state.ready = 1
      ), 0) = NEW.bootstrap_raw_row_count
      AND COALESCE((
        SELECT SUM(state.product_row_count) FROM netshop_promotion_aggregate_state state
        WHERE state.platform = NEW.platform
          AND state.source_batch_id = NEW.bootstrap_batch_id AND state.ready = 1
      ), 0) = NEW.bootstrap_product_row_count;
  END`;

const promotionAggregateTriggerStatements = [
  `CREATE TRIGGER IF NOT EXISTS netshop_promotion_aggregate_raw_insert
    AFTER INSERT ON netshop_rows
    WHEN NEW.business_date IS NOT NULL AND NEW.business_date <> '' AND (
      (NEW.source = 'jd_promotion' AND NEW.dataset = 'ad' AND NEW.sku_id <> '') OR
      (NEW.source = 'tmall_promotion' AND NEW.dataset = 'promotion_daily' AND NEW.spu_id <> '')
    ) BEGIN${bumpManifestVersionSql("NEW")}${bumpScopeRevisionSql("NEW")}${invalidateStateSql("NEW")}
    END`,
  `CREATE TRIGGER IF NOT EXISTS netshop_promotion_aggregate_raw_delete
    AFTER DELETE ON netshop_rows
    WHEN OLD.business_date IS NOT NULL AND OLD.business_date <> '' AND (
      (OLD.source = 'jd_promotion' AND OLD.dataset = 'ad' AND OLD.sku_id <> '') OR
      (OLD.source = 'tmall_promotion' AND OLD.dataset = 'promotion_daily' AND OLD.spu_id <> '')
    ) BEGIN${bumpManifestVersionSql("OLD")}${bumpScopeRevisionSql("OLD")}${invalidateStateSql("OLD")}
    END`,
  `CREATE TRIGGER IF NOT EXISTS netshop_promotion_aggregate_raw_update_old
    AFTER UPDATE OF source, dataset, platform, shop_name, business_date, sku_id, spu_id, product_name, metrics_json, raw_json, last_import_batch_id ON netshop_rows
    WHEN OLD.business_date IS NOT NULL AND OLD.business_date <> '' AND (
      (OLD.source = 'jd_promotion' AND OLD.dataset = 'ad' AND OLD.sku_id <> '') OR
      (OLD.source = 'tmall_promotion' AND OLD.dataset = 'promotion_daily' AND OLD.spu_id <> '')
    ) BEGIN${bumpManifestVersionSql("OLD")}${bumpScopeRevisionSql("OLD")}${invalidateStateSql("OLD")}
    END`,
  `CREATE TRIGGER IF NOT EXISTS netshop_promotion_aggregate_raw_update_new
    AFTER UPDATE OF source, dataset, platform, shop_name, business_date, sku_id, spu_id, product_name, metrics_json, raw_json, last_import_batch_id ON netshop_rows
    WHEN NEW.business_date IS NOT NULL AND NEW.business_date <> '' AND (
      (NEW.source = 'jd_promotion' AND NEW.dataset = 'ad' AND NEW.sku_id <> '') OR
      (NEW.source = 'tmall_promotion' AND NEW.dataset = 'promotion_daily' AND NEW.spu_id <> '')
    ) BEGIN${bumpManifestVersionSql("NEW")}${bumpScopeRevisionSql("NEW")}${invalidateStateSql("NEW")}
    END`,
  bootstrapTriggerSql,
] as const;

export const PROMOTION_AGGREGATE_SCHEMA_STATEMENTS = [
  ...promotionAggregateBaseSchemaStatements,
  ...promotionAggregateTriggerStatements,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export async function ensurePromotionAggregateSchema(db: PromotionAggregateDatabase) {
  const key = db as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(promotionAggregateBaseSchemaStatements.map((sql) => db.prepare(sql)))
    .then(async () => {
      const columns = await db.prepare("PRAGMA table_info(netshop_promotion_aggregate_manifest)")
        .all<{ name: string }>();
      if (!(columns.results ?? []).some((column) => column.name === "data_version")) {
        await db.prepare(`ALTER TABLE netshop_promotion_aggregate_manifest
          ADD COLUMN data_version INTEGER NOT NULL DEFAULT 0`).run();
      }
      await db.batch([
        ...promotionAggregateTriggerNames.map((name) => db.prepare(`DROP TRIGGER IF EXISTS ${name}`)),
        ...promotionAggregateTriggerStatements.map((sql) => db.prepare(sql)),
      ]);
    })
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

function isIsoDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function rangeDayCount(startDate: string, endDate: string) {
  if (!isIsoDate(startDate) || !isIsoDate(endDate) || startDate > endDate) {
    throw new Error("推广聚合重建范围必须是有效的左闭右闭 YYYY-MM-DD 日期区间");
  }
  return Math.floor((Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / 86_400_000) + 1;
}

function assertScope(scope: PromotionAggregateScope, enforceRebuildBound = false) {
  if (!scope.platform.trim() || !scope.shopName.trim()) throw new Error("推广聚合范围必须包含平台和店铺");
  if (scope.source === "jd_promotion" && scope.dataset !== "ad") throw new Error("京东推广聚合只接受 ad 数据集");
  if (scope.source === "tmall_promotion" && scope.dataset !== "promotion_daily") throw new Error("天猫推广聚合只接受 promotion_daily 数据集");
  const days = rangeDayCount(scope.startDate, scope.endDate);
  if (enforceRebuildBound && days > PROMOTION_AGGREGATE_REBUILD_MAX_DAYS) {
    throw new Error(`单次推广聚合重建最多 ${PROMOTION_AGGREGATE_REBUILD_MAX_DAYS} 天`);
  }
}

/**
 * Constant-query readiness gate for aggregate-backed reads. The platform
 * manifest proves that every raw shop that pre-dated the invalidation triggers
 * was included in a complete historical backfill. State rows then track every
 * later scope mutation. Request paths only read manifest/state metadata and
 * never re-count raw or aggregate facts.
 */
export type PromotionAggregateVersionScope = {
  platform: string;
  shopNames?: readonly string[];
  startDate: string;
  endDate: string;
};

export async function readPromotionAggregateVersions(
  db: PromotionAggregateDatabase,
  inputs: readonly PromotionAggregateVersionScope[],
) {
  if (inputs.length === 0) return [];
  if (inputs.length > 20) throw new Error("推广聚合完整性门禁单次最多核验 20 个平台范围");
  const seenPlatforms = new Set<string>();
  const scopes = inputs.map((input) => {
    rangeDayCount(input.startDate, input.endDate);
    if (seenPlatforms.has(input.platform)) throw new Error("推广聚合完整性门禁的平台范围不能重复");
    seenPlatforms.add(input.platform);
    const shopNames = [...new Set((input.shopNames ?? []).map((shop) => shop.trim()).filter(Boolean))];
    if (shopNames.length > 50) throw new Error("推广聚合完整性门禁单次最多核验 50 个店铺");
    return { ...input, shopNames };
  });
  const requestedSql = `WITH requested AS (
    SELECT
      CAST(json_extract(value, '$.platform') AS TEXT) AS platform,
      CAST(json_extract(value, '$.startDate') AS TEXT) AS start_date,
      CAST(json_extract(value, '$.endDate') AS TEXT) AS end_date,
      json_extract(value, '$.shopNames') AS shop_names
    FROM json_each(?)
  )`;
  const platformStaleSql = `NOT EXISTS (
    SELECT 1
    FROM netshop_promotion_aggregate_state
    WHERE ready = 0
      AND platform = requested.platform
      AND business_date >= requested.start_date
      AND business_date <= requested.end_date
    LIMIT 1
  )`;
  const shopsStaleSql = `NOT EXISTS (
    SELECT 1
    FROM json_each(requested.shop_names) selected_shop
    WHERE EXISTS (
      SELECT 1
      FROM netshop_promotion_aggregate_state
      WHERE ready = 0
        AND platform = requested.platform
        AND shop_name = CAST(selected_shop.value AS TEXT)
        AND business_date >= requested.start_date
        AND business_date <= requested.end_date
      LIMIT 1
    )
    LIMIT 1
  )`;
  const staleSql = scopes.every((scope) => scope.shopNames.length === 0)
    ? platformStaleSql
    : scopes.every((scope) => scope.shopNames.length > 0)
      ? shopsStaleSql
      : `((json_array_length(requested.shop_names) = 0 AND ${platformStaleSql})
        OR (json_array_length(requested.shop_names) > 0 AND ${shopsStaleSql}))`;
  const requestedShops = scopes.flatMap((scope) => scope.shopNames.map((shopName) => ({
    platform: scope.platform,
    shopName,
  })));
  const [rows, shopRevisionRows] = await Promise.all([
    db.prepare(`${requestedSql}
      SELECT requested.platform, manifest.data_version
      FROM requested
      JOIN netshop_promotion_aggregate_manifest manifest
        ON manifest.platform = requested.platform
       AND manifest.ready = 1
      WHERE ${staleSql}`)
      .bind(JSON.stringify(scopes)).all<{ platform: string; data_version: number }>(),
    requestedShops.length > 0
      ? db.prepare(`WITH requested_shops AS (
          SELECT
            CAST(json_extract(value, '$.platform') AS TEXT) AS platform,
            CAST(json_extract(value, '$.shopName') AS TEXT) AS shop_name
          FROM json_each(?)
        )
        SELECT requested_shops.platform, requested_shops.shop_name,
          COALESCE(revision.data_version, 0) AS data_version
        FROM requested_shops
        LEFT JOIN netshop_promotion_scope_revisions revision
          ON revision.platform = requested_shops.platform
         AND revision.shop_name = requested_shops.shop_name
        ORDER BY requested_shops.platform ASC, requested_shops.shop_name ASC`)
        .bind(JSON.stringify(requestedShops))
        .all<{ platform: string; shop_name: string; data_version: number }>()
      : Promise.resolve({ results: [] as Array<{ platform: string; shop_name: string; data_version: number }> }),
  ]);
  const shopRevisionsByPlatform = new Map<string, Array<{ shopName: string; dataVersion: number }>>();
  for (const row of shopRevisionRows.results ?? []) {
    const dataVersion = Number(row.data_version);
    if (!Number.isSafeInteger(dataVersion) || dataVersion < 0) continue;
    const revisions = shopRevisionsByPlatform.get(row.platform) ?? [];
    revisions.push({ shopName: row.shop_name, dataVersion });
    shopRevisionsByPlatform.set(row.platform, revisions);
  }
  return (rows.results ?? []).flatMap((row) => {
    const dataVersion = Number(row.data_version);
    const scope = scopes.find((candidate) => candidate.platform === row.platform);
    const shopRevisions = shopRevisionsByPlatform.get(row.platform) ?? [];
    if (!Number.isSafeInteger(dataVersion) || dataVersion < 0 || !scope) return [];
    if (scope.shopNames.length > 0 && shopRevisions.length !== scope.shopNames.length) return [];
    return [{ platform: row.platform, dataVersion, shopRevisions }];
  });
}

export async function readPromotionAggregateVersion(
  db: PromotionAggregateDatabase,
  input: PromotionAggregateVersionScope,
) {
  const versions = await readPromotionAggregateVersions(db, [input]);
  return versions[0]?.dataVersion ?? null;
}

export async function canUsePromotionAggregates(
  db: PromotionAggregateDatabase,
  input: { platform: string; shopNames?: readonly string[]; startDate: string; endDate: string },
) {
  return (await readPromotionAggregateVersion(db, input)) !== null;
}

function finiteMetric(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const numeric = Number(value.replace(/[,￥¥%]/g, "").trim());
  return Number.isFinite(numeric) ? numeric : null;
}

function metric(row: NetshopRowInput, typedKey: string, legacyKey?: string, legacyMultiplier = 1) {
  const typed = finiteMetric(row.metrics[typedKey]);
  if (typed !== null) return typed;
  const legacy = legacyKey ? finiteMetric(row.metrics[legacyKey]) : null;
  return legacy === null ? 0 : legacy * legacyMultiplier;
}

function integerMetric(value: number, label: string) {
  const integer = Math.round(value);
  if (!Number.isSafeInteger(integer)) throw new Error(`推广聚合指标 ${label} 超出安全整数范围`);
  return integer;
}

function rowMetrics(row: NetshopRowInput) {
  return {
    spendCents: integerMetric(metric(row, "spendCents", "花费", 100), "spendCents"),
    netTransactionAmountCents: integerMetric(metric(row, "netTransactionAmountCents", "总订单金额", 100), "netTransactionAmountCents"),
    grossTransactionAmountCents: integerMetric(metric(row, "grossTransactionAmountCents", "总订单金额", 100), "grossTransactionAmountCents"),
    impressions: integerMetric(metric(row, "impressions", "展现数"), "impressions"),
    clicks: integerMetric(metric(row, "clicks", "点击数"), "clicks"),
    netOrders: integerMetric(metric(row, "netOrders", "总订单行"), "netOrders"),
    favorites: integerMetric(metric(row, "favorites"), "favorites"),
    cartQuantity: integerMetric(metric(row, "cartQuantity"), "cartQuantity"),
  };
}

function productId(row: NetshopRowInput) {
  return row.source === "jd_promotion" ? row.skuId.trim() : row.spuId.trim();
}

function productKey(row: NetshopRowInput, id: string) {
  return JSON.stringify([row.platform, row.shopName, row.businessDate, id]);
}

function shopKey(row: Pick<PromotionProductDailyAggregate, "platform" | "shopName" | "businessDate">) {
  return JSON.stringify([row.platform, row.shopName, row.businessDate]);
}

/** Pure aggregation used by import publication before any database mutation. */
export function aggregatePromotionRows(rows: readonly NetshopRowInput[], sourceBatchId: string): PromotionAggregateSet {
  if (!sourceBatchId.trim()) throw new Error("推广聚合必须绑定来源批次");
  const products = new Map<string, PromotionProductDailyAggregate & { productLines: Set<string> }>();
  for (const row of rows) {
    const eligible = (row.source === "jd_promotion" && row.dataset === "ad")
      || (row.source === "tmall_promotion" && row.dataset === "promotion_daily");
    if (!eligible) continue;
    if (!isIsoDate(row.businessDate)) throw new Error("推广聚合行缺少有效业务日期");
    const id = productId(row);
    if (!id) throw new Error("推广聚合行缺少商品身份");
    const values = rowMetrics(row);
    const key = productKey(row, id);
    const existing = products.get(key);
    if (!existing) {
      const line = String(row.raw["产品线"] ?? "").trim();
      products.set(key, {
        source: row.source as PromotionAggregateScope["source"],
        platform: row.platform,
        shopName: row.shopName,
        businessDate: row.businessDate,
        productId: id,
        productName: row.productName,
        productLine: line,
        productLines: new Set(line ? [line] : []),
        ...values,
        sourceRowCount: 1,
        sourceBatchId,
        sourceBatchCount: 1,
      });
      continue;
    }
    if (row.productName > existing.productName) existing.productName = row.productName;
    const line = String(row.raw["产品线"] ?? "").trim();
    if (line) existing.productLines.add(line);
    existing.spendCents += values.spendCents;
    existing.netTransactionAmountCents += values.netTransactionAmountCents;
    existing.grossTransactionAmountCents += values.grossTransactionAmountCents;
    existing.impressions += values.impressions;
    existing.clicks += values.clicks;
    existing.netOrders += values.netOrders;
    existing.favorites += values.favorites;
    existing.cartQuantity += values.cartQuantity;
    existing.sourceRowCount += 1;
  }
  const productRows = [...products.values()].map(({ productLines, ...row }) => ({
    ...row,
    productLine: [...productLines].sort().join(","),
  })).sort((a, b) => JSON.stringify([a.platform, a.shopName, a.businessDate, a.productId])
    .localeCompare(JSON.stringify([b.platform, b.shopName, b.businessDate, b.productId])));

  const shops = new Map<string, PromotionShopDailyAggregate>();
  for (const row of productRows) {
    const key = shopKey(row);
    const existing = shops.get(key);
    if (!existing) {
      shops.set(key, {
        source: row.source,
        platform: row.platform,
        shopName: row.shopName,
        businessDate: row.businessDate,
        productCount: 1,
        spendCents: row.spendCents,
        netTransactionAmountCents: row.netTransactionAmountCents,
        grossTransactionAmountCents: row.grossTransactionAmountCents,
        impressions: row.impressions,
        clicks: row.clicks,
        netOrders: row.netOrders,
        favorites: row.favorites,
        cartQuantity: row.cartQuantity,
        sourceRowCount: row.sourceRowCount,
        sourceBatchId,
        sourceBatchCount: 1,
      });
      continue;
    }
    existing.productCount += 1;
    existing.spendCents += row.spendCents;
    existing.netTransactionAmountCents += row.netTransactionAmountCents;
    existing.grossTransactionAmountCents += row.grossTransactionAmountCents;
    existing.impressions += row.impressions;
    existing.clicks += row.clicks;
    existing.netOrders += row.netOrders;
    existing.favorites += row.favorites;
    existing.cartQuantity += row.cartQuantity;
    existing.sourceRowCount += row.sourceRowCount;
  }
  return { products: productRows, shops: [...shops.values()].sort((a, b) => shopKey(a).localeCompare(shopKey(b))) };
}

const productInsertSql = `INSERT INTO netshop_promotion_product_daily (
  platform, shop_name, business_date, product_id, source, product_name, product_line,
  spend_cents, net_transaction_amount_cents, gross_transaction_amount_cents,
  impressions, clicks, net_orders, favorites, cart_quantity, source_row_count,
  source_batch_id, source_batch_count, rebuilt_at
) SELECT
  json_extract(value, '$.platform'), json_extract(value, '$.shopName'),
  json_extract(value, '$.businessDate'), json_extract(value, '$.productId'),
  json_extract(value, '$.source'), json_extract(value, '$.productName'),
  json_extract(value, '$.productLine'), CAST(json_extract(value, '$.spendCents') AS INTEGER),
  CAST(json_extract(value, '$.netTransactionAmountCents') AS INTEGER),
  CAST(json_extract(value, '$.grossTransactionAmountCents') AS INTEGER),
  CAST(json_extract(value, '$.impressions') AS INTEGER), CAST(json_extract(value, '$.clicks') AS INTEGER),
  CAST(json_extract(value, '$.netOrders') AS INTEGER), CAST(json_extract(value, '$.favorites') AS INTEGER),
  CAST(json_extract(value, '$.cartQuantity') AS INTEGER), CAST(json_extract(value, '$.sourceRowCount') AS INTEGER),
  json_extract(value, '$.sourceBatchId'), CAST(json_extract(value, '$.sourceBatchCount') AS INTEGER), CURRENT_TIMESTAMP
FROM json_each(?)
WHERE EXISTS (
  SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
)`;

const shopInsertSql = `INSERT INTO netshop_promotion_shop_daily (
  platform, shop_name, business_date, source, product_count, spend_cents,
  net_transaction_amount_cents, gross_transaction_amount_cents, impressions, clicks,
  net_orders, favorites, cart_quantity, source_row_count, source_batch_id,
  source_batch_count, rebuilt_at
) SELECT
  json_extract(value, '$.platform'), json_extract(value, '$.shopName'),
  json_extract(value, '$.businessDate'), json_extract(value, '$.source'),
  CAST(json_extract(value, '$.productCount') AS INTEGER), CAST(json_extract(value, '$.spendCents') AS INTEGER),
  CAST(json_extract(value, '$.netTransactionAmountCents') AS INTEGER),
  CAST(json_extract(value, '$.grossTransactionAmountCents') AS INTEGER),
  CAST(json_extract(value, '$.impressions') AS INTEGER), CAST(json_extract(value, '$.clicks') AS INTEGER),
  CAST(json_extract(value, '$.netOrders') AS INTEGER), CAST(json_extract(value, '$.favorites') AS INTEGER),
  CAST(json_extract(value, '$.cartQuantity') AS INTEGER), CAST(json_extract(value, '$.sourceRowCount') AS INTEGER),
  json_extract(value, '$.sourceBatchId'), CAST(json_extract(value, '$.sourceBatchCount') AS INTEGER), CURRENT_TIMESTAMP
FROM json_each(?)
WHERE EXISTS (
  SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
)`;

const stateInsertSql = `INSERT INTO netshop_promotion_aggregate_state (
  platform, shop_name, business_date, source, ready, raw_row_count, product_row_count,
  source_batch_id, source_batch_count, rebuilt_at, invalidated_at
) SELECT
  json_extract(value, '$.platform'), json_extract(value, '$.shopName'),
  json_extract(value, '$.businessDate'), json_extract(value, '$.source'), 1,
  CAST(json_extract(value, '$.sourceRowCount') AS INTEGER), CAST(json_extract(value, '$.productCount') AS INTEGER),
  json_extract(value, '$.sourceBatchId'), CAST(json_extract(value, '$.sourceBatchCount') AS INTEGER),
  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM json_each(?)
WHERE EXISTS (
  SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
)`;

const manifestSentinelSql = `INSERT OR IGNORE INTO netshop_promotion_aggregate_manifest (
  platform, ready, invalidated_at, data_version
) SELECT ?, 0, CURRENT_TIMESTAMP, 0
WHERE EXISTS (
  SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
)`;

// The expensive completeness proof lives behind an AFTER INSERT trigger on the
// one-row control table. Existing manifests and previously-blocked legacy
// platforms take INSERT-OR-IGNORE fast paths, so routine imports never execute
// any historical raw/aggregate scans.
const bootstrapCandidateSql = `INSERT OR IGNORE INTO netshop_promotion_aggregate_control (
  platform, bootstrap_batch_id, bootstrap_raw_row_count,
  bootstrap_product_row_count, bootstrap_shop_day_count,
  bootstrap_data_cutoff, updated_at
) SELECT ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
  )
  AND EXISTS (
    SELECT 1 FROM netshop_promotion_aggregate_manifest
    WHERE platform = ? AND ready = 0
  )`;

export type PromotionAggregateMaintenanceFence = {
  platform: string;
  token: string;
  dataVersion: number;
  previousReady: boolean;
};

function maintenanceGuard(fence: PromotionAggregateMaintenanceFence) {
  return {
    sql: ` AND EXISTS (
      SELECT 1
      FROM netshop_promotion_aggregate_control control
      JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform = control.platform
      WHERE control.platform = ? AND control.maintenance_token = ?
        AND control.maintenance_version = ?
        AND manifest.ready = 0 AND manifest.data_version = ?
    )`,
    bindings: [fence.platform, fence.token, fence.dataVersion, fence.dataVersion] as unknown[],
  };
}

async function maintenanceFenceIsCurrent(
  db: PromotionAggregateDatabase,
  fence: PromotionAggregateMaintenanceFence,
) {
  const row = await db.prepare(`SELECT 1 AS held
    FROM netshop_promotion_aggregate_control control
    JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform = control.platform
    WHERE control.platform = ? AND control.maintenance_token = ?
      AND control.maintenance_version = ?
      AND manifest.ready = 0 AND manifest.data_version = ?`)
    .bind(fence.platform, fence.token, fence.dataVersion, fence.dataVersion)
    .first<{ held: number }>();
  return Number(row?.held ?? 0) === 1;
}

export async function beginPromotionAggregateMaintenance(
  db: PromotionAggregateDatabase,
  platform: string,
  token = crypto.randomUUID(),
): Promise<PromotionAggregateMaintenanceFence> {
  const normalizedPlatform = platform.trim();
  if (!normalizedPlatform) throw new Error("推广聚合维护必须绑定平台");
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(token)) throw new Error("推广聚合维护令牌无效");
  await ensurePromotionAggregateSchema(db);
  await db.batch([
    db.prepare(`INSERT OR IGNORE INTO netshop_promotion_aggregate_manifest (
      platform, ready, invalidated_at, data_version
    ) VALUES (?, 0, CURRENT_TIMESTAMP, 0)`).bind(normalizedPlatform),
    db.prepare(`INSERT INTO netshop_promotion_aggregate_control (
        platform, maintenance_token, maintenance_version,
        maintenance_previous_ready, maintenance_started_at, updated_at
      )
      SELECT platform, ?, data_version + 1, ready, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM netshop_promotion_aggregate_manifest
      WHERE platform = ?
      ON CONFLICT(platform) DO UPDATE SET
        maintenance_token = excluded.maintenance_token,
        maintenance_version = excluded.maintenance_version,
        -- A normal promotion import may advance manifest.data_version while a
        -- failed maintenance remains pending. The non-empty prior owner token,
        -- not version adjacency, is the durable provenance that this platform
        -- was readable before that unfinished maintenance began.
        maintenance_previous_ready = CASE
          WHEN excluded.maintenance_previous_ready = 1 THEN 1
          WHEN netshop_promotion_aggregate_control.maintenance_token <> ''
            AND netshop_promotion_aggregate_control.maintenance_previous_ready = 1
          THEN 1
          ELSE 0
        END,
        maintenance_started_at = excluded.maintenance_started_at,
        updated_at = CURRENT_TIMESTAMP`).bind(token, normalizedPlatform),
    db.prepare(`UPDATE netshop_promotion_aggregate_manifest
      SET ready = 0,
        data_version = data_version + 1,
        invalidated_at = CURRENT_TIMESTAMP
      WHERE platform = ?`).bind(normalizedPlatform),
  ]);
  const row = await db.prepare(`SELECT control.maintenance_token, control.maintenance_version,
      control.maintenance_previous_ready, manifest.ready, manifest.data_version
    FROM netshop_promotion_aggregate_control control
    JOIN netshop_promotion_aggregate_manifest manifest ON manifest.platform = control.platform
    WHERE control.platform = ?`).bind(normalizedPlatform).first<{
      maintenance_token: string;
      maintenance_version: number;
      maintenance_previous_ready: number;
      ready: number;
      data_version: number;
    }>();
  const dataVersion = Number(row?.data_version ?? -1);
  if (row?.maintenance_token !== token || Number(row.maintenance_version) !== dataVersion
    || row.ready !== 0 || !Number.isSafeInteger(dataVersion) || dataVersion < 1) {
    throw new Error("推广聚合维护栅栏被并发任务取代");
  }
  return {
    platform: normalizedPlatform,
    token,
    dataVersion,
    previousReady: row.maintenance_previous_ready === 1,
  };
}

function scopeDeleteStatements(
  db: PromotionAggregateDatabase,
  scope: PromotionAggregateScope,
  guardInput: { processingBatchId?: string; maintenanceFence?: PromotionAggregateMaintenanceFence } = {},
) {
  const bind = [scope.platform, scope.shopName, scope.startDate, scope.endDate];
  if (guardInput.processingBatchId && guardInput.maintenanceFence) throw new Error("推广聚合写入栅栏不能混用");
  const guard = guardInput.processingBatchId
    ? {
        sql: " AND EXISTS (SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing')",
        bindings: [guardInput.processingBatchId] as unknown[],
      }
    : guardInput.maintenanceFence
      ? maintenanceGuard(guardInput.maintenanceFence)
      : { sql: "", bindings: [] as unknown[] };
  const guardedBind = [...bind, ...guard.bindings];
  return [
    db.prepare(`DELETE FROM netshop_promotion_aggregate_state
      WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?${guard.sql}`).bind(...guardedBind),
    db.prepare(`DELETE FROM netshop_promotion_product_daily
      WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?${guard.sql}`).bind(...guardedBind),
    db.prepare(`DELETE FROM netshop_promotion_shop_daily
      WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?${guard.sql}`).bind(...guardedBind),
  ];
}

/**
 * Builds statements to append to saveNetshopImport's existing db.batch.
 * Call after raw DELETE/UPSERT statements and before the reservation commit fence.
 */
export function buildPromotionAggregatePublishStatements(
  db: PromotionAggregateDatabase,
  input: PromotionAggregateScope & { batchId: string; rows: readonly NetshopRowInput[] },
) {
  assertScope(input);
  if (!input.batchId.trim()) throw new Error("推广聚合发布必须绑定导入批次");
  if (input.rows.length === 0) throw new Error("推广聚合发布拒绝空业务集合");
  for (const row of input.rows) {
    if (row.source !== input.source || row.dataset !== input.dataset || row.platform !== input.platform
      || row.shopName !== input.shopName || row.businessDate < input.startDate || row.businessDate > input.endDate) {
      throw new Error("推广聚合发布行超出精确业务范围");
    }
  }
  const aggregates = aggregatePromotionRows(input.rows, input.batchId);
  const statements = scopeDeleteStatements(db, input, { processingBatchId: input.batchId });
  for (let offset = 0; offset < aggregates.products.length; offset += 250) {
    statements.push(db.prepare(productInsertSql).bind(JSON.stringify(aggregates.products.slice(offset, offset + 250)), input.batchId));
  }
  if (aggregates.shops.length) {
    statements.push(db.prepare(shopInsertSql).bind(JSON.stringify(aggregates.shops), input.batchId));
    statements.push(db.prepare(stateInsertSql).bind(JSON.stringify(aggregates.shops), input.batchId));
    statements.push(db.prepare(manifestSentinelSql).bind(input.platform, input.batchId));
    statements.push(db.prepare(bootstrapCandidateSql).bind(
      input.platform,
      input.batchId,
      input.rows.length,
      aggregates.products.length,
      aggregates.shops.length,
      aggregates.shops.reduce<string | null>((latest, row) => latest === null || row.businessDate > latest ? row.businessDate : latest, null),
      input.batchId,
      input.platform,
    ));
  }
  return statements;
}

function rawScopeSql(scope: PromotionAggregateScope) {
  const identity = scope.source === "jd_promotion"
    ? "r.source = 'jd_promotion' AND r.dataset = 'ad' AND r.sku_id <> ''"
    : "r.source = 'tmall_promotion' AND r.dataset = 'promotion_daily' AND r.spu_id <> ''";
  return `${identity} AND r.platform = ? AND r.shop_name = ? AND r.business_date >= ? AND r.business_date <= ?`;
}

function rawScopeBindings(scope: PromotionAggregateScope) {
  return [scope.platform, scope.shopName, scope.startDate, scope.endDate];
}

/**
 * Atomically and idempotently rebuilds one bounded historical promotion range.
 * Existing readers are fenced before any aggregate can change. Standalone
 * rebuilds deliberately leave the platform unavailable; only a fully verified
 * backfill may complete the maintenance fence.
 */
export async function rebuildPromotionAggregates(
  db: PromotionAggregateDatabase,
  scope: PromotionAggregateScope,
  suppliedFence?: PromotionAggregateMaintenanceFence,
) {
  assertScope(scope, true);
  await ensurePromotionAggregateSchema(db);
  const fence = suppliedFence ?? await beginPromotionAggregateMaintenance(db, scope.platform);
  if (fence.platform !== scope.platform || !await maintenanceFenceIsCurrent(db, fence)) {
    throw new Error("推广聚合重建维护栅栏已失效");
  }
  const guard = maintenanceGuard(fence);
  const where = rawScopeSql(scope);
  const bindings = rawScopeBindings(scope);
  const productIdSql = scope.source === "jd_promotion" ? "r.sku_id" : "r.spu_id";
  const productInsert = db.prepare(`INSERT INTO netshop_promotion_product_daily (
      platform, shop_name, business_date, product_id, source, product_name, product_line,
      spend_cents, net_transaction_amount_cents, gross_transaction_amount_cents,
      impressions, clicks, net_orders, favorites, cart_quantity, source_row_count,
      source_batch_id, source_batch_count, rebuilt_at
    ) SELECT
      r.platform, r.shop_name, r.business_date, ${productIdSql}, r.source,
      MAX(r.product_name), COALESCE(GROUP_CONCAT(DISTINCT NULLIF(CAST(json_extract(r.raw_json, '$."产品线"') AS TEXT), '')), ''),
      CAST(ROUND(SUM(${netshopPromotionMetrics.spendCents})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.netTransactionAmountCents})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.grossTransactionAmountCents})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.impressions})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.clicks})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.netOrders})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.favorites})) AS INTEGER),
      CAST(ROUND(SUM(${netshopPromotionMetrics.cartQuantity})) AS INTEGER),
      COUNT(*),
      CASE WHEN COUNT(DISTINCT r.last_import_batch_id) = 1 THEN MIN(r.last_import_batch_id) ELSE '' END,
      COUNT(DISTINCT r.last_import_batch_id), CURRENT_TIMESTAMP
    FROM netshop_rows r
    WHERE ${where}${guard.sql}
    GROUP BY r.platform, r.shop_name, r.business_date, ${productIdSql}`).bind(...bindings, ...guard.bindings);
  const shopInsert = db.prepare(`INSERT INTO netshop_promotion_shop_daily (
      platform, shop_name, business_date, source, product_count, spend_cents,
      net_transaction_amount_cents, gross_transaction_amount_cents, impressions, clicks,
      net_orders, favorites, cart_quantity, source_row_count, source_batch_id,
      source_batch_count, rebuilt_at
    ) SELECT platform, shop_name, business_date, MIN(source), COUNT(*), SUM(spend_cents),
      SUM(net_transaction_amount_cents), SUM(gross_transaction_amount_cents), SUM(impressions), SUM(clicks),
      SUM(net_orders), SUM(favorites), SUM(cart_quantity), SUM(source_row_count),
      CASE WHEN MAX(source_batch_count) = 1 AND MIN(source_batch_id) = MAX(source_batch_id) THEN MIN(source_batch_id) ELSE '' END,
      CASE WHEN MAX(source_batch_count) = 1 AND MIN(source_batch_id) = MAX(source_batch_id) THEN 1 ELSE SUM(source_batch_count) END,
      CURRENT_TIMESTAMP
    FROM netshop_promotion_product_daily
    WHERE platform = ? AND shop_name = ? AND business_date >= ? AND business_date <= ?${guard.sql}
    GROUP BY platform, shop_name, business_date`).bind(...bindings, ...guard.bindings);
  const stateInsert = db.prepare(`INSERT INTO netshop_promotion_aggregate_state (
      platform, shop_name, business_date, source, ready, raw_row_count, product_row_count,
      source_batch_id, source_batch_count, rebuilt_at, invalidated_at
    ) SELECT s.platform, s.shop_name, s.business_date, s.source, 1, s.source_row_count,
      s.product_count, s.source_batch_id, s.source_batch_count, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    FROM netshop_promotion_shop_daily s
    WHERE s.platform = ? AND s.shop_name = ? AND s.business_date >= ? AND s.business_date <= ?${guard.sql}`).bind(...bindings, ...guard.bindings);
  const statements = [
    db.prepare(`INSERT INTO netshop_promotion_scope_revisions (
        platform, shop_name, data_version, updated_at
      )
      SELECT ?, ?, 1, CURRENT_TIMESTAMP
      WHERE 1 = 1${guard.sql}
      ON CONFLICT(platform, shop_name) DO UPDATE SET
        data_version = netshop_promotion_scope_revisions.data_version + 1,
        updated_at = CURRENT_TIMESTAMP`).bind(scope.platform, scope.shopName, ...guard.bindings),
    ...scopeDeleteStatements(db, scope, { maintenanceFence: fence }),
    productInsert,
    shopInsert,
    stateInsert,
  ];
  await db.batch(statements);
  if (!await maintenanceFenceIsCurrent(db, fence)) throw new Error("推广聚合重建期间维护栅栏已失效");
  return { platform: scope.platform, shopName: scope.shopName, startDate: scope.startDate, endDate: scope.endDate };
}

export function promotionAggregateScopeForSource(input: {
  source: NetshopSource;
  platform: string;
  shopName: string;
  startDate: string;
  endDate: string;
}): PromotionAggregateScope | null {
  if (input.source === "jd_promotion") return { ...input, source: input.source, dataset: "ad" };
  if (input.source === "tmall_promotion") return { ...input, source: input.source, dataset: "promotion_daily" };
  return null;
}
