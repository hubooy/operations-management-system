import { env } from "cloudflare:workers";
import { buildNetshopImportBatchListQuery, type NetshopImportBatchListFilters } from "./import-batch-list-query";
import { netshopBatchId, sameNetshopBatchIdentity } from "@/lib/netshop/batch-identity";
import {
  DAILY_ROW_NATURAL_IDENTITY_INDEX_SQL,
  ensureDailyRowNaturalKeys,
} from "@/lib/netshop/daily-row-migration";
import {
  netshopPromotionMetrics,
  netshopPromotionPaymentSourceSql,
  netshopPromotionProductIdSql,
  netshopPromotionSourceSql,
} from "@/lib/netshop/promotion-query";
import {
  buildPromotionAggregatePublishStatements,
  ensurePromotionAggregateSchema,
  PROMOTION_AGGREGATE_PRODUCT_FIELDS,
  PROMOTION_AGGREGATE_PRODUCT_READY_JOIN_SQL,
  PROMOTION_AGGREGATE_SHOP_FIELDS,
  PROMOTION_AGGREGATE_SHOP_READY_JOIN_SQL,
  promotionAggregateScopeForSource,
  readPromotionAggregateVersions,
} from "@/lib/netshop/promotion-aggregate";
import {
  importReservationCommitFence,
  rethrowImportPublishError,
  type ImportReservationFence,
} from "@/lib/imports/content-fingerprint";
import {
  boundedNetshopInteger,
  isNetshopIsoDate,
  NETSHOP_OUTLET_MAX_ITEMS,
  NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE,
  NETSHOP_QUERY_MAX_DAYS,
  NETSHOP_QUERY_MAX_PAGE,
  NETSHOP_QUERY_MAX_PAGE_SIZE,
  NetshopQueryError,
  normalizeNetshopOutletFilters,
  resolveNetshopQueryPeriod,
  type NetshopOutletFilter,
  type NetshopQueryPeriod,
} from "@/lib/netshop/query-contract";
import { resolveNetshopSalesOutletMatches } from "@/lib/netshop/sales-shop-aliases";
import { PublicApiError } from "@/lib/http/api-error";
import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
} from "@/lib/django/sales-consumer-reader";
import {
  netshopProductImageUrl,
  storedNetshopProductImage,
  type StoredNetshopProductImage,
} from "@/lib/netshop/product-image-assets";

export type NetshopDatabase = NonNullable<typeof env.DB>;
export const NETSHOP_DAILY_SERIES_LIMIT = NETSHOP_QUERY_MAX_DAYS;

export type NetshopImportIssue = {
  row?: number;
  field?: string;
  code?: string;
  message: string;
};

export type NetshopSource =
  | "jd_shop_overview"
  | "jd_sku_daily"
  | "jd_promotion"
  | "jd_b2b"
  | "jd_product_master"
  | "jd_cs"
  | "jd_yimei_sku"
  | "tmall_product_master"
  | "tmall_product_assets"
  | "tmall_product_daily"
  | "tmall_promotion"
  | "inv_selfop";

export type NetshopRowInput = {
  sourceRowNumber: number;
  sourceRowKey: string;
  sourceRowHash: string;
  source: NetshopSource;
  dataset: string;
  platform: string;
  shopName: string;
  businessDate: string;
  snapshotDate: string;
  productCode: string;
  productName: string;
  skuId: string;
  spuId: string;
  warehouseType: string;
  metrics: Record<string, number | string | null>;
  raw: Record<string, string | number | boolean | null>;
};

type NetshopBatchRow = {
  id: string;
  source: NetshopSource;
  dataset: string;
  platform: string;
  shop_name: string;
  file_name: string;
  file_size_bytes: number;
  file_hash: string;
  sheet_name: string;
  status: string;
  row_count: number;
  inserted_count: number;
  duplicate_count: number;
  warning_count: number;
  date_min: string | null;
  date_max: string | null;
  snapshot_date: string | null;
  warnings_json: string;
  totals_json: string;
  note: string;
  created_at: string;
  completed_at: string | null;
};

export type NetshopImportBatch = {
  id: string;
  source: NetshopSource;
  dataset: string;
  platform: string;
  shopName: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  sheetName: string;
  status: string;
  rowCount: number;
  insertedCount: number;
  duplicateCount: number;
  warningCount: number;
  dateMin: string | null;
  dateMax: string | null;
  snapshotDate: string | null;
  warnings: NetshopImportIssue[];
  totals: unknown;
  note: string;
  createdAt: string;
  completedAt: string | null;
};

export type NetshopOverviewDataset = {
  source: string;
  dataset: string;
  dateMin: string | null;
  dateMax: string | null;
  snapshotDate: string | null;
  rowCount: number;
  latestBatchId: string | null;
  latestFileName: string | null;
  completedAt: string | null;
};

export type NetshopProductCatalogItem = {
  platform: string;
  shopName: string;
  spuId: string;
  skuId: string;
  productCode: string;
  productName: string;
  imageUrl: string;
  saleAttribute: string;
  category: string;
  brand: string;
  price: number | null;
  priceCents: number | null;
  totalInventory: number | null;
  availableInventory: number | null;
  status: string;
  productUrl: string;
  createdAt: string;
  snapshotDate: string | null;
  costPriceCents: number | null;
  netSalesCents: number | null;
  grossMarginRate: number | null;
  refundRate: number | null;
  salesMatched: boolean;
};

export type NetshopProductCatalog = {
  snapshotToken: string;
  batch: NetshopImportBatch | null;
  summary: {
    totalSkus: number;
    onSaleSkus: number;
    totalInventory: number;
    availableInventory: number;
  };
  shops: Array<{
    shopName: string;
    platform: string;
    snapshotDate: string | null;
    completedAt: string | null;
  }>;
  sales: {
    periodStart: string | null;
    periodEnd: string | null;
    dataCutoffDate: string | null;
    platform: string;
  };
  items: NetshopProductCatalogItem[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    returned: number;
    truncated: boolean;
  };
};

export type NetshopProductCatalogPage = Pick<
  NetshopProductCatalog,
  "snapshotToken" | "items" | "pagination"
>;

export type NetshopProductPerformanceDimension = "sku" | "spu";

export type NetshopProductPerformanceItem = {
  id: string;
  platform: string;
  skuId: string;
  spuId: string;
  productCode: string;
  productName: string;
  imageUrl: string;
  productUrl: string;
  category: string;
  shopNames: string[];
  dateMin: string | null;
  dateMax: string | null;
  dataDays: number;
  pageViews: number;
  visitors: number;
  searchImpressions: number;
  searchClicks: number;
  searchClickRate: number | null;
  addCartCustomers: number;
  addCartQuantity: number;
  orderCustomers: number;
  orderQuantity: number;
  orderAmount: number;
  orderAmountCents: number;
  transactionOrders: number;
  transactionAmount: number;
  transactionAmountCents: number;
  transactionQuantity: number;
  transactionCustomers: number;
  favorites: number;
  refundAmountCents: number;
  searchVisitors: number;
  searchTransactionCustomers: number;
  uvValue: number | null;
  conversionRate: number | null;
};

export type NetshopProductPerformance = {
  snapshotToken: string;
  dimension: NetshopProductPerformanceDimension;
  dataset: "sku_daily" | "spu_daily";
  requestedPeriod: { startDate: string | null; endDate: string | null };
  dateMin: string | null;
  dataCutoffDate: string | null;
  monetaryUnit: "cents";
  visitorAggregation: "product_day_sum";
  coverage: {
    actualDates: string[];
    missingDates: string[];
    availableDateMin: string | null;
    availableDateMax: string | null;
    total: number;
    returned: number;
    truncated: boolean;
  };
  platforms: string[];
  shops: Array<{ shopName: string; platform: string; productCount: number }>;
  summary: {
    productCount: number;
    pageViews: number;
    visitors: number;
    searchImpressions: number;
    searchClicks: number;
    searchClickRate: number | null;
    addCartCustomers: number;
    addCartQuantity: number;
    orderCustomers: number;
    orderQuantity: number;
    orderAmount: number;
    orderAmountCents: number;
    transactionOrders: number;
    transactionAmount: number;
    transactionAmountCents: number;
    transactionQuantity: number;
    transactionCustomers: number;
    favorites: number;
    refundAmountCents: number;
    searchVisitors: number;
    searchTransactionCustomers: number;
    uvValue: number | null;
    conversionRate: number | null;
  };
  daily: Array<{
    date: string;
    pageViews: number;
    visitors: number;
    transactionCustomers: number;
    transactionQuantity: number;
    transactionAmountCents: number;
    refundAmountCents: number;
    favorites: number;
    addCartCustomers: number;
    addCartQuantity: number;
  }>;
  dailyPagination: { total: number; returned: number; truncated: boolean };
  items: NetshopProductPerformanceItem[];
  pagination: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
};

export type NetshopProductPerformanceSummary = Pick<
  NetshopProductPerformance,
  | "snapshotToken"
  | "dimension"
  | "dataset"
  | "requestedPeriod"
  | "dateMin"
  | "dataCutoffDate"
  | "monetaryUnit"
  | "visitorAggregation"
  | "summary"
>;

export type NetshopProductPerformancePage = Pick<
  NetshopProductPerformance,
  "snapshotToken" | "items" | "pagination"
>;

export type NetshopPromotionPerformance = {
  monetaryUnit: "cents";
  requestedPeriod: { startDate: string | null; endDate: string | null };
  dateMin: string | null;
  dataCutoffDate: string | null;
  coverage: {
    promotionDates: string[];
    productDailyDates: string[];
    intersectionDates: string[];
    missingProductDailyDates: string[];
    missingPromotionDates: string[];
    promotionDatesPagination: { total: number; returned: number; truncated: boolean };
    productDailyDatesPagination: { total: number; returned: number; truncated: boolean };
    intersectionTruncated: boolean;
  };
  summary: {
    productCount: number;
    spendCents: number;
    netTransactionAmountCents: number;
    grossTransactionAmountCents: number;
    platformPaymentAmountCents: number;
    impressions: number;
    clicks: number;
    netOrders: number;
    favorites: number;
    cartQuantity: number;
    clickThroughRate: number | null;
    averageClickCostCents: number | null;
    roas: number | null;
    spendRate: number | null;
    promotionTransactionShare: number | null;
  };
  daily: Array<{
    date: string;
    spendCents: number;
    netTransactionAmountCents: number;
    platformPaymentAmountCents: number | null;
    impressions: number;
    clicks: number;
    netOrders: number;
    roas: number | null;
    spendRate: number | null;
    promotionTransactionShare: number | null;
  }>;
  filterOptions: {
    shops: Array<{ platform: string; shopName: string }>;
    pagination: { total: number; returned: number; truncated: boolean };
  };
  items: Array<{
    id: string;
    platform: string;
    productName: string;
    shopName: string;
    dateMin: string | null;
    dateMax: string | null;
    dates: string[];
    datesTruncated: boolean;
    dataDays: number;
    spendCents: number;
    netTransactionAmountCents: number;
    grossTransactionAmountCents: number;
    impressions: number;
    clicks: number;
    netOrders: number;
    favorites: number;
    cartQuantity: number;
    clickThroughRate: number | null;
    averageClickCostCents: number | null;
    roas: number | null;
  }>;
  dailyPagination: { total: number; returned: number; truncated: boolean };
  pagination: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
};

export type NetshopPromotionOverview = Pick<
  NetshopPromotionPerformance,
  | "monetaryUnit"
  | "requestedPeriod"
  | "dataCutoffDate"
  | "summary"
  | "coverage"
  | "daily"
  | "dailyPagination"
  | "filterOptions"
> & { snapshotToken: string };

export type NetshopPromotionItems = Pick<
  NetshopPromotionPerformance,
  "monetaryUnit" | "requestedPeriod" | "dataCutoffDate" | "items" | "pagination"
> & { snapshotToken: string };

const batchColumns = `
  id, source, dataset, platform, shop_name, file_name, file_size_bytes, file_hash,
  sheet_name, status, row_count, inserted_count, duplicate_count, warning_count,
  date_min, date_max, snapshot_date, warnings_json, totals_json, note,
  created_at, completed_at
`;

const batchTableSql = `CREATE TABLE IF NOT EXISTS netshop_import_batches (
    id TEXT PRIMARY KEY NOT NULL,
    source TEXT NOT NULL,
    dataset TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    sheet_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    date_min TEXT,
    date_max TEXT,
    snapshot_date TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    totals_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(source, platform, shop_name, file_hash)
  )`;

const productDailyRevisionTableSql = `CREATE TABLE IF NOT EXISTS netshop_product_daily_revisions (
    platform TEXT PRIMARY KEY NOT NULL,
    data_version INTEGER NOT NULL DEFAULT 0 CHECK (data_version >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`;

const productDailyScopeRevisionTableSql = `CREATE TABLE IF NOT EXISTS netshop_product_daily_scope_revisions (
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    data_version INTEGER NOT NULL DEFAULT 0 CHECK (data_version >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, shop_name)
  )`;

const schemaStatements = [
  batchTableSql,
  productDailyRevisionTableSql,
  productDailyScopeRevisionTableSql,
  `CREATE TABLE IF NOT EXISTS netshop_schema_migrations (
    migration_key TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS netshop_import_batches_source_created_idx
    ON netshop_import_batches (source, created_at)`,
  `CREATE INDEX IF NOT EXISTS netshop_import_batches_shop_dataset_idx
    ON netshop_import_batches (shop_name, dataset, completed_at)`,
  `CREATE INDEX IF NOT EXISTS netshop_import_batches_latest_product_idx
    ON netshop_import_batches (source, status, platform, shop_name, completed_at, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS netshop_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_row_key TEXT NOT NULL UNIQUE,
    source_row_hash TEXT NOT NULL,
    first_import_batch_id TEXT NOT NULL,
    last_import_batch_id TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    source TEXT NOT NULL,
    dataset TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    business_date TEXT,
    snapshot_date TEXT,
    product_code TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    spu_id TEXT NOT NULL DEFAULT '',
    warehouse_type TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_shop_dataset_date_idx
    ON netshop_rows (shop_name, dataset, business_date)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_source_date_idx
    ON netshop_rows (source, business_date)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_source_dataset_scope_date_idx
    ON netshop_rows (source, dataset, platform, shop_name, business_date)`,
  DAILY_ROW_NATURAL_IDENTITY_INDEX_SQL,
  `CREATE INDEX IF NOT EXISTS netshop_rows_snapshot_idx
    ON netshop_rows (source, snapshot_date, warehouse_type)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_source_sku_idx
    ON netshop_rows (source, sku_id, product_code)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_jd_master_online_spec_idx
    ON netshop_rows (
      CAST(json_extract(raw_json, '$."商家SKU"') AS TEXT),
      sku_id
    )
    WHERE source = 'jd_product_master'
      AND dataset = 'product_master'
      AND json_valid(raw_json)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_scope_date_product_idx
    ON netshop_rows (dataset, platform, shop_name, business_date, spu_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_master_snapshot_idx
    ON netshop_rows (source, platform, shop_name, snapshot_date)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_sku_id_idx
    ON netshop_rows (sku_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_spu_id_idx
    ON netshop_rows (spu_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_product_code_idx
    ON netshop_rows (product_code)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_lock_ownership_idx
    ON netshop_rows (source, dataset, platform, shop_name, last_import_batch_id)`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_product_batch_page_idx
    ON netshop_rows (last_import_batch_id, shop_name, product_name, sku_id, platform, id)
    WHERE source IN ('jd_product_master', 'tmall_product_master') AND dataset = 'product_master'`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_product_assets_identity_idx
    ON netshop_rows (platform, shop_name, spu_id, snapshot_date DESC)
    WHERE source = 'tmall_product_assets' AND dataset = 'spu_assets'`,
  `CREATE INDEX IF NOT EXISTS netshop_rows_product_assets_hash_idx
    ON netshop_rows (json_extract(raw_json, '$."图片内容SHA256"'))
    WHERE source = 'tmall_product_assets' AND dataset = 'spu_assets' AND json_valid(raw_json)`,
  `CREATE TABLE IF NOT EXISTS netshop_asset_uploads (
    id TEXT PRIMARY KEY NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    shop_name TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    chunk_size_bytes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    received_chunk_count INTEGER NOT NULL DEFAULT 0,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'uploading',
    processing_owner TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS netshop_asset_uploads_expiry_idx
    ON netshop_asset_uploads (expires_at, status)`,
  `CREATE TABLE IF NOT EXISTS netshop_asset_upload_chunks (
    upload_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    object_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (upload_id, chunk_index),
    FOREIGN KEY (upload_id) REFERENCES netshop_asset_uploads(id) ON DELETE CASCADE
  )`,
  `CREATE INDEX IF NOT EXISTS netshop_asset_upload_chunks_upload_idx
    ON netshop_asset_upload_chunks (upload_id, chunk_index)`,
  `CREATE TABLE IF NOT EXISTS netshop_asset_upload_results (
    upload_id TEXT PRIMARY KEY NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (upload_id) REFERENCES netshop_asset_uploads(id) ON DELETE CASCADE
  )`,
  `UPDATE netshop_rows
    SET platform = '京东', shop_name = '志高商用设备旗舰店', updated_at = CURRENT_TIMESTAMP
    WHERE source = 'jd_product_master'
      AND last_import_batch_id IN (
        SELECT id FROM netshop_import_batches
        WHERE source = 'jd_product_master'
          AND file_name LIKE '%POP%'
          AND shop_name = '特睿思（TERUISI）京东自营旗舰店'
      )`,
  `UPDATE netshop_import_batches
    SET platform = '京东', shop_name = '志高商用设备旗舰店'
    WHERE source = 'jd_product_master'
      AND file_name LIKE '%POP%'
      AND shop_name = '特睿思（TERUISI）京东自营旗舰店'`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

/** Upgrade the historical source+file-hash constraint without altering file_hash itself. */
async function migrateBatchIdentityConstraint(db: NetshopDatabase) {
  type IndexRow = { name: string; unique: number };
  type IndexColumn = { name: string };
  const indexes = await db.prepare("PRAGMA index_list('netshop_import_batches')").all<IndexRow>();
  let legacyConstraint = false;
  for (const index of (indexes.results ?? []).filter((item) => Number(item.unique) === 1)) {
    const columns = await db.prepare(`PRAGMA index_info('${index.name.replace(/'/g, "''")}')`).all<IndexColumn>();
    if ((columns.results ?? []).map((column) => column.name).join(",") === "source,file_hash") legacyConstraint = true;
  }
  if (!legacyConstraint) return;
  const legacy = "netshop_import_batches_legacy_scope";
  const replacement = batchTableSql.replace("netshop_import_batches", "netshop_import_batches_scoped_tmp");
  await db.batch([
    db.prepare(`ALTER TABLE netshop_import_batches RENAME TO ${legacy}`),
    db.prepare(replacement),
    db.prepare(`INSERT INTO netshop_import_batches_scoped_tmp (${batchColumns}) SELECT ${batchColumns} FROM ${legacy}`),
    db.prepare(`DROP TABLE ${legacy}`),
    db.prepare("ALTER TABLE netshop_import_batches_scoped_tmp RENAME TO netshop_import_batches"),
  ]);
}

export function getNetshopDatabase(): NetshopDatabase {
  if (!env.DB) {
    throw new Error("Cloudflare D1 binding `DB` is unavailable.");
  }
  return env.DB;
}

export async function ensureNetshopSchema(db = getNetshopDatabase()) {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = migrateBatchIdentityConstraint(db)
    .then(() => db.batch(schemaStatements.map((statement) => db.prepare(statement))))
    .then(() => ensureDailyRowNaturalKeys(db))
    .then(() => ensurePromotionAggregateSchema(db))
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

function parseJson<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export async function getNetshopProductImageMetadata(
  db: NetshopDatabase,
  contentHash: string,
  platformNames: readonly string[],
): Promise<StoredNetshopProductImage | null> {
  const normalizedHash = contentHash.trim().toLowerCase();
  const platforms = [...new Set(platformNames.map((value) => value.trim()).filter(Boolean))].slice(0, 20);
  if (!/^[a-f0-9]{64}$/.test(normalizedHash) || platforms.length === 0) return null;
  const row = await db.prepare(
    `SELECT asset.raw_json
     FROM netshop_rows asset
     JOIN netshop_import_batches batch
      ON batch.id = asset.last_import_batch_id
      AND batch.status = 'completed'
      AND batch.source = asset.source
      AND batch.dataset = asset.dataset
      AND batch.platform = asset.platform
      AND batch.shop_name = asset.shop_name
     WHERE asset.source = 'tmall_product_assets'
       AND asset.dataset = 'spu_assets'
       AND json_valid(asset.raw_json)
       AND asset.platform IN (${platforms.map(() => "?").join(", ")})
       AND json_extract(asset.raw_json, '$."图片内容SHA256"') = ?
     ORDER BY asset.snapshot_date DESC, batch.completed_at DESC, batch.created_at DESC, asset.id DESC
     LIMIT 1`,
  ).bind(...platforms, normalizedHash).first<{ raw_json: string }>();
  return row ? storedNetshopProductImage(parseJson<Record<string, unknown>>(row.raw_json, {})) : null;
}

function mapBatch(row: NetshopBatchRow): NetshopImportBatch {
  return {
    id: row.id,
    source: row.source,
    dataset: row.dataset,
    platform: row.platform,
    shopName: row.shop_name,
    fileName: row.file_name,
    fileSizeBytes: Number(row.file_size_bytes),
    fileHash: row.file_hash,
    sheetName: row.sheet_name,
    status: row.status,
    rowCount: Number(row.row_count),
    insertedCount: Number(row.inserted_count),
    duplicateCount: Number(row.duplicate_count),
    warningCount: Number(row.warning_count),
    dateMin: row.date_min,
    dateMax: row.date_max,
    snapshotDate: row.snapshot_date,
    warnings: parseJson<NetshopImportIssue[]>(row.warnings_json, []),
    totals: parseJson<unknown>(row.totals_json, {}),
    note: row.note,
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}

export function sanitizeNetshopIssues(issues: readonly unknown[]): NetshopImportIssue[] {
  return issues.slice(0, 200).map((issue) => {
    if (typeof issue === "string") return { message: issue.slice(0, 500) };
    if (!issue || typeof issue !== "object") return { message: String(issue).slice(0, 500) };
    const record = issue as Record<string, unknown>;
    const row = Number(record.row ?? record.rowNumber ?? record.sourceRowNumber);
    const safe: NetshopImportIssue = {
      message: String(record.message ?? record.reason ?? record.code ?? "Import warning").slice(0, 500),
    };
    if (Number.isInteger(row) && row > 0) safe.row = row;
    if (typeof record.field === "string") safe.field = record.field.slice(0, 100);
    if (typeof record.code === "string") safe.code = record.code.slice(0, 100);
    return safe;
  });
}

export async function findNetshopImportBatchByHash(
  db: NetshopDatabase,
  source: NetshopSource,
  fileHash: string,
  identity?: { platform: string; shopName: string },
) {
  if (identity) {
    const scoped = await db
      .prepare(`SELECT ${batchColumns} FROM netshop_import_batches WHERE source = ? AND platform = ? AND shop_name = ? AND file_hash = ? LIMIT 1`)
      .bind(source, identity.platform, identity.shopName, fileHash)
      .first<NetshopBatchRow>();
    if (scoped) return mapBatch(scoped);
  }
  const row = await db
    .prepare(`SELECT ${batchColumns} FROM netshop_import_batches WHERE source = ? AND file_hash = ? LIMIT 1`)
    .bind(source, fileHash)
    .first<NetshopBatchRow>();
  const batch = row ? mapBatch(row) : null;
  // Legacy batches were keyed only by source/hash. They remain idempotent only
  // for their recorded owner and must never suppress another shop's import.
  return batch && (!identity || sameNetshopBatchIdentity(batch, { source, ...identity })) ? batch : null;
}

export async function findNetshopImportBatchById(
  db: NetshopDatabase,
  id: string,
) {
  const row = await db
    .prepare(`SELECT ${batchColumns} FROM netshop_import_batches WHERE id = ? LIMIT 1`)
    .bind(id)
    .first<NetshopBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function readNetshopScopeOwnership(
  db: NetshopDatabase,
  input: {
    source: NetshopSource;
    dataset: string;
    platform: string;
    shopName: string;
    startDate?: string | null;
    endDate?: string | null;
    snapshotDate?: string | null;
    fullScope?: boolean;
  },
) {
  if (input.startDate && input.endDate) {
    const result = await db.prepare(
      `SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
       FROM netshop_rows
       WHERE source = ? AND dataset = ? AND platform = ? AND shop_name = ?
         AND business_date >= ? AND business_date <= ?
       GROUP BY last_import_batch_id
       ORDER BY last_import_batch_id`,
    ).bind(
      input.source,
      input.dataset,
      input.platform,
      input.shopName,
      input.startDate,
      input.endDate,
    ).all<{ batch_id: string; row_count: number }>();
    return result.results.map((row) => ({ batchId: row.batch_id, rowCount: Number(row.row_count) }));
  }
  const snapshotClause = input.snapshotDate ? " AND snapshot_date = ?" : "";
  const bindings: string[] = [input.source, input.dataset, input.platform, input.shopName];
  if (input.snapshotDate) bindings.push(input.snapshotDate);
  const result = await db.prepare(
    `SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
     FROM netshop_rows
     WHERE source = ? AND dataset = ? AND platform = ? AND shop_name = ?${snapshotClause}
     GROUP BY last_import_batch_id
     ORDER BY last_import_batch_id`,
  ).bind(...bindings).all<{ batch_id: string; row_count: number }>();
  return result.results.map((row) => ({ batchId: row.batch_id, rowCount: Number(row.row_count) }));
}

type NetshopStoredContentRow = {
  source_row_number: number;
  source_row_key: string;
  source_row_hash: string;
  source: NetshopSource;
  dataset: string;
  platform: string;
  shop_name: string;
  business_date: string | null;
  snapshot_date: string | null;
  product_code: string;
  product_name: string;
  sku_id: string;
  spu_id: string;
  warehouse_type: string;
  metrics_json: string;
  raw_json: string;
};

/**
 * Reads the currently published business facts for an exact import scope.
 * This is used only for a duplicate candidate so the caller can recompute the
 * normalized content fingerprint instead of trusting batch ownership/counts.
 */
export async function readNetshopScopeRows(
  db: NetshopDatabase,
  input: {
    source: NetshopSource;
    dataset: string;
    platform: string;
    shopName: string;
    startDate?: string | null;
    endDate?: string | null;
    snapshotDate?: string | null;
    fullScope?: boolean;
  },
): Promise<NetshopRowInput[]> {
  const where = ["source = ?", "dataset = ?", "platform = ?", "shop_name = ?"];
  const bindings: string[] = [input.source, input.dataset, input.platform, input.shopName];
  if (input.startDate && input.endDate) {
    where.push("business_date >= ?", "business_date <= ?");
    bindings.push(input.startDate, input.endDate);
  } else if (input.snapshotDate) {
    where.push("snapshot_date = ?");
    bindings.push(input.snapshotDate);
  }
  const result = await db.prepare(
    `SELECT source_row_number, source_row_key, source_row_hash, source, dataset,
            platform, shop_name, business_date, snapshot_date, product_code,
            product_name, sku_id, spu_id, warehouse_type, metrics_json, raw_json
     FROM netshop_rows
     WHERE ${where.join(" AND ")}
     ORDER BY id`,
  ).bind(...bindings).all<NetshopStoredContentRow>();
  return result.results.map((row) => ({
    sourceRowNumber: Number(row.source_row_number),
    sourceRowKey: row.source_row_key,
    sourceRowHash: row.source_row_hash,
    source: row.source,
    dataset: row.dataset,
    platform: row.platform,
    shopName: row.shop_name,
    businessDate: row.business_date ?? "",
    snapshotDate: row.snapshot_date ?? "",
    productCode: row.product_code,
    productName: row.product_name,
    skuId: row.sku_id,
    spuId: row.spu_id,
    warehouseType: row.warehouse_type,
    metrics: parseJson<Record<string, number | string | null>>(
      row.metrics_json,
      { __invalidStoredJson: row.metrics_json },
    ),
    raw: parseJson<Record<string, string | number | boolean | null>>(
      row.raw_json,
      { __invalidStoredJson: row.raw_json },
    ),
  }));
}

export async function listNetshopImportBatches(
  db: NetshopDatabase,
  input: NetshopImportBatchListFilters = {},
) {
  const { page, pageSize, offset, whereSql, bindings } = buildNetshopImportBatchListQuery(input);
  const where = whereSql ? `WHERE ${whereSql}` : "";
  const [result, count] = await Promise.all([
    db.prepare(
      `SELECT ${batchColumns}
       FROM netshop_import_batches
       ${where}
       ORDER BY created_at DESC, id DESC
       LIMIT ? OFFSET ?`,
    )
      .bind(...bindings, pageSize, offset)
      .all<NetshopBatchRow>(),
    db.prepare(`SELECT COUNT(*) AS total FROM netshop_import_batches ${where}`)
      .bind(...bindings)
      .first<{ total: number }>(),
  ]);
  const items = result.results.map(mapBatch);
  const total = Number(count?.total ?? 0);
  return {
    items,
    pagination: {
      page,
      pageSize,
      total,
      returned: items.length,
      truncated: offset + items.length < total,
    },
  };
}

const upsertRowsSql = `
  INSERT INTO netshop_rows (
    source_row_key, source_row_hash, first_import_batch_id, last_import_batch_id,
    source_row_number, source, dataset, platform, shop_name, business_date,
    snapshot_date, product_code, product_name, sku_id, spu_id, warehouse_type,
    metrics_json, raw_json
  )
  SELECT
    json_extract(item.value, '$.sourceRowKey'),
    json_extract(item.value, '$.sourceRowHash'),
    ?, ?,
    CAST(json_extract(item.value, '$.sourceRowNumber') AS INTEGER),
    json_extract(item.value, '$.source'),
    json_extract(item.value, '$.dataset'),
    json_extract(item.value, '$.platform'),
    json_extract(item.value, '$.shopName'),
    NULLIF(json_extract(item.value, '$.businessDate'), ''),
    NULLIF(json_extract(item.value, '$.snapshotDate'), ''),
    json_extract(item.value, '$.productCode'),
    json_extract(item.value, '$.productName'),
    json_extract(item.value, '$.skuId'),
    json_extract(item.value, '$.spuId'),
    json_extract(item.value, '$.warehouseType'),
    json(json_extract(item.value, '$.metrics')),
    json(json_extract(item.value, '$.raw'))
  FROM json_each(?) AS item
  WHERE EXISTS (
    SELECT 1 FROM netshop_import_batches
    WHERE id = ? AND status = 'processing'
  )
  ON CONFLICT(source_row_key) DO UPDATE SET
    source_row_hash = excluded.source_row_hash,
    last_import_batch_id = excluded.last_import_batch_id,
    source_row_number = excluded.source_row_number,
    source = excluded.source,
    dataset = excluded.dataset,
    platform = excluded.platform,
    shop_name = excluded.shop_name,
    business_date = excluded.business_date,
    snapshot_date = excluded.snapshot_date,
    product_code = excluded.product_code,
    product_name = excluded.product_name,
    sku_id = excluded.sku_id,
    spu_id = excluded.spu_id,
    warehouse_type = excluded.warehouse_type,
    metrics_json = excluded.metrics_json,
    raw_json = excluded.raw_json,
    updated_at = CURRENT_TIMESTAMP
`;

export async function saveNetshopImport(
  db: NetshopDatabase,
  input: {
    source: NetshopSource;
    dataset: string;
    platform: string;
    shopName: string;
    fileHash: string;
    fileName: string;
    fileSizeBytes: number;
    sheetName: string;
    rows: NetshopRowInput[];
    warnings: NetshopImportIssue[];
    totals: unknown;
    note: string;
    replaceScope?: { startDate: string; endDate: string } | { snapshotDate: string } | { fullScope: true };
    reservationFence?: ImportReservationFence;
  },
): Promise<{ batch: NetshopImportBatch; created: boolean }> {
  const batchId = netshopBatchId(input);
  const dates = input.rows.map((row) => row.businessDate).filter(Boolean).sort();
  const snapshots = input.rows.map((row) => row.snapshotDate).filter(Boolean).sort();
  const dateMin = dates[0] ?? null;
  const dateMax = dates[dates.length - 1] ?? null;
  const snapshotDate = snapshots[snapshots.length - 1] ?? null;
  const statements = [
    db
      .prepare(
        `INSERT INTO netshop_import_batches (
          id, source, dataset, platform, shop_name, file_name, file_size_bytes,
          file_hash, sheet_name, status, row_count, warning_count,
          date_min, date_max, snapshot_date, warnings_json, totals_json, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, platform, shop_name, file_hash) DO NOTHING`,
      )
      .bind(
        batchId,
        input.source,
        input.dataset,
        input.platform,
        input.shopName,
        input.fileName,
        input.fileSizeBytes,
        input.fileHash,
        input.sheetName,
        input.rows.length,
        input.warnings.length,
        dateMin,
        dateMax,
        snapshotDate,
        JSON.stringify(input.warnings),
        JSON.stringify(input.totals ?? {}),
        input.note,
      ),
  ];

  if (input.replaceScope && "startDate" in input.replaceScope) {
    statements.push(
      db.prepare(
        `DELETE FROM netshop_rows
         WHERE source = ? AND dataset = ? AND platform = ? AND shop_name = ?
           AND business_date >= ? AND business_date <= ?
           AND EXISTS (
             SELECT 1 FROM netshop_import_batches
             WHERE id = ? AND status = 'processing'
           )`,
      ).bind(
        input.source,
        input.dataset,
        input.platform,
        input.shopName,
        input.replaceScope.startDate,
        input.replaceScope.endDate,
        batchId,
      ),
    );
  } else if (input.replaceScope && "snapshotDate" in input.replaceScope) {
    statements.push(
      db.prepare(
        `DELETE FROM netshop_rows
         WHERE source = ? AND dataset = ? AND platform = ? AND shop_name = ?
           AND snapshot_date = ?
           AND EXISTS (
             SELECT 1 FROM netshop_import_batches
             WHERE id = ? AND status = 'processing'
           )`,
      ).bind(
        input.source,
        input.dataset,
        input.platform,
        input.shopName,
        input.replaceScope.snapshotDate,
        batchId,
      ),
    );
  } else if (input.replaceScope?.fullScope) {
    statements.push(
      db.prepare(
        `DELETE FROM netshop_rows
         WHERE source = ? AND dataset = ? AND platform = ? AND shop_name = ?
           AND EXISTS (
             SELECT 1 FROM netshop_import_batches
             WHERE id = ? AND status = 'processing'
           )`,
      ).bind(input.source, input.dataset, input.platform, input.shopName, batchId),
    );
  }

  for (let offset = 0; offset < input.rows.length; offset += 300) {
    const chunk = input.rows.slice(offset, offset + 300);
    statements.push(db.prepare(upsertRowsSql).bind(batchId, batchId, JSON.stringify(chunk), batchId));
  }

  if (input.source === "jd_product_master") {
    statements.push(db.prepare(
      `UPDATE netshop_rows
       SET product_code = COALESCE(NULLIF(CAST(json_extract(raw_json, '$."商品编码"') AS TEXT), ''), product_code),
           spu_id = COALESCE(NULLIF(CAST(json_extract(raw_json, '$."商品编码"') AS TEXT), ''), spu_id),
           updated_at = CURRENT_TIMESTAMP
       WHERE source = 'jd_product_master' AND last_import_batch_id = ?
         AND EXISTS (
           SELECT 1 FROM netshop_import_batches
           WHERE id = ? AND status = 'processing'
         )`,
    ).bind(batchId, batchId));
  }

  if (input.replaceScope && "startDate" in input.replaceScope) {
    const promotionScope = promotionAggregateScopeForSource({
      source: input.source,
      platform: input.platform,
      shopName: input.shopName,
      startDate: input.replaceScope.startDate,
      endDate: input.replaceScope.endDate,
    });
    if (promotionScope) {
      const aggregateStatements = buildPromotionAggregatePublishStatements(db, {
        ...promotionScope,
        batchId,
        rows: input.rows,
      }) as Array<(typeof statements)[number]>;
      statements.push(...aggregateStatements);
    }
  }

  const updatesProductDailyFacts = (
    input.source === "jd_sku_daily"
      && (input.dataset === "sku_daily" || input.dataset === "spu_daily")
      && input.platform === "京东"
  ) || (
    input.source === "tmall_product_daily"
      && input.dataset === "spu_daily"
      && input.platform === "天猫"
  );
  if (updatesProductDailyFacts) {
    statements.push(db.prepare(
      `INSERT INTO netshop_product_daily_revisions (platform, data_version, updated_at)
       SELECT ?, 1, CURRENT_TIMESTAMP
       WHERE EXISTS (
         SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
       )
       ON CONFLICT(platform) DO UPDATE SET
         data_version = netshop_product_daily_revisions.data_version + 1,
         updated_at = CURRENT_TIMESTAMP`,
    ).bind(input.platform, batchId));
    statements.push(db.prepare(
      `INSERT INTO netshop_product_daily_scope_revisions (
         platform, shop_name, data_version, updated_at
       )
       SELECT ?, ?, 1, CURRENT_TIMESTAMP
       WHERE EXISTS (
         SELECT 1 FROM netshop_import_batches WHERE id = ? AND status = 'processing'
       )
       ON CONFLICT(platform, shop_name) DO UPDATE SET
         data_version = netshop_product_daily_scope_revisions.data_version + 1,
         updated_at = CURRENT_TIMESTAMP`,
    ).bind(input.platform, input.shopName, batchId));
  }

  statements.push(
    db
      .prepare(
        `UPDATE netshop_import_batches
         SET status = 'completed',
             inserted_count = (
               SELECT COUNT(*) FROM netshop_rows WHERE first_import_batch_id = ?
             ),
             duplicate_count = row_count - (
               SELECT COUNT(*) FROM netshop_rows WHERE first_import_batch_id = ?
             ),
             completed_at = CURRENT_TIMESTAMP
         WHERE id = ? AND status = 'processing'`,
      )
      .bind(batchId, batchId, batchId),
  );
  if (input.reservationFence) statements.push(importReservationCommitFence(db, input.reservationFence));

  let result;
  try {
    result = await db.batch(statements);
  } catch (error) {
    if (input.reservationFence) {
      await rethrowImportPublishError(db, input.reservationFence, error);
    }
    throw error;
  }
  const created = Number(result[0]?.meta?.changes ?? 0) > 0;
  const batch = await findNetshopImportBatchByHash(db, input.source, input.fileHash, input);
  if (!batch) throw new Error("Netshop import batch was not readable after save.");
  return { batch, created };
}

export async function verifyNetshopImportBatch(
  db: NetshopDatabase,
  batch: NetshopImportBatch,
  expected: { rowCount: number; dataset: string; platform: string; shopName: string; dateMin: string | null; dateMax: string | null },
) {
  const row = await db.prepare(
    `SELECT COUNT(*) AS row_count, MIN(business_date) AS date_min, MAX(business_date) AS date_max
     FROM netshop_rows WHERE last_import_batch_id = ?`,
  ).bind(batch.id).first<{ row_count: number | null; date_min: string | null; date_max: string | null }>();
  const readbackRowCount = Number(row?.row_count ?? 0);
  const scoped = expected.dateMin && expected.dateMax
    ? await db.prepare(
      `SELECT COUNT(*) AS row_count
       FROM netshop_rows
       WHERE source = ? AND dataset = ? AND platform = ? AND shop_name = ?
         AND business_date >= ? AND business_date <= ?`,
    ).bind(
      batch.source,
      expected.dataset,
      expected.platform,
      expected.shopName,
      expected.dateMin,
      expected.dateMax,
    ).first<{ row_count: number | null }>()
    : null;
  const currentScopeRowCount = scoped ? Number(scoped.row_count ?? 0) : readbackRowCount;
  const verified = batch.status === "completed"
    && batch.dataset === expected.dataset
    && batch.platform === expected.platform
    && batch.shopName === expected.shopName
    && batch.rowCount === expected.rowCount
    && readbackRowCount === expected.rowCount
    && currentScopeRowCount === expected.rowCount
    && (row?.date_min ?? null) === expected.dateMin
    && (row?.date_max ?? null) === expected.dateMax;
  return {
    verified,
    parsedRowCount: expected.rowCount,
    readbackRowCount,
    currentScopeRowCount,
    dateMin: row?.date_min ?? null,
    dateMax: row?.date_max ?? null,
    dataset: batch.dataset,
    platform: batch.platform,
    shopName: batch.shopName,
  };
}

export async function reconcileNetshopMasterProducts(
  db: NetshopDatabase,
  input: { platform: string; shopName: string; productIds: string[] },
) {
  const productIds = [...new Set(input.productIds.map((value) => value.trim()).filter(Boolean))].slice(0, 20_000);
  const latest = await db.prepare(
    `SELECT id FROM netshop_import_batches
     WHERE source = 'tmall_product_master' AND platform = ? AND shop_name = ? AND status = 'completed'
     ORDER BY completed_at DESC, created_at DESC, id DESC LIMIT 1`,
  ).bind(input.platform, input.shopName).first<{ id: string }>();
  if (!latest?.id) return { masterAvailable: false, unmatchedCount: productIds.length, unmatchedSample: productIds.slice(0, 20) };
  const result = await db.prepare(
    `WITH requested(id) AS (SELECT CAST(value AS TEXT) FROM json_each(?))
     SELECT requested.id
     FROM requested
     LEFT JOIN netshop_rows master
       ON master.last_import_batch_id = ? AND master.spu_id = requested.id
     WHERE master.id IS NULL
     LIMIT 21`,
  ).bind(JSON.stringify(productIds), latest.id).all<{ id: string }>();
  const countRow = await db.prepare(
    `WITH requested(id) AS (SELECT CAST(value AS TEXT) FROM json_each(?))
     SELECT COUNT(*) AS count
     FROM requested
     WHERE NOT EXISTS (
       SELECT 1 FROM netshop_rows master
       WHERE master.last_import_batch_id = ? AND master.spu_id = requested.id
     )`,
  ).bind(JSON.stringify(productIds), latest.id).first<{ count: number }>();
  return {
    masterAvailable: true,
    unmatchedCount: Number(countRow?.count ?? 0),
    unmatchedSample: result.results.slice(0, 20).map((row) => row.id),
  };
}

export async function normalizeJdProductMasterRows(
  db: NetshopDatabase,
  batchId: string,
) {
  await db
    .prepare(
      `UPDATE netshop_rows
       SET product_code = COALESCE(NULLIF(CAST(json_extract(raw_json, '$."商品编码"') AS TEXT), ''), product_code),
           spu_id = COALESCE(NULLIF(CAST(json_extract(raw_json, '$."商品编码"') AS TEXT), ''), spu_id),
           updated_at = CURRENT_TIMESTAMP
       WHERE source = 'jd_product_master' AND last_import_batch_id = ?`,
    )
    .bind(batchId)
    .run();
}

export async function getNetshopOverview(db: NetshopDatabase, shop: string | null, platformNames: string[] = []) {
  const platforms = [...new Set(platformNames.map((value) => value.trim()).filter(Boolean))].slice(0, 20);
  const platformClause = platforms.length ? ` AND r.platform IN (${platforms.map(() => "?").join(", ")})` : "";
  const rows = await db
    .prepare(
      `SELECT
         dataset,
         source,
         MIN(business_date) AS date_min,
         MAX(business_date) AS date_max,
         MAX(snapshot_date) AS snapshot_date,
         COUNT(*) AS row_count,
         (
           SELECT b.id
           FROM netshop_import_batches b
            WHERE b.dataset = r.dataset AND b.source = r.source AND b.platform = r.platform
             AND (? = '' OR b.shop_name = ? OR b.source = 'inv_selfop')
             AND b.status = 'completed'
           ORDER BY b.completed_at DESC, b.created_at DESC
           LIMIT 1
         ) AS latest_batch_id,
         (
           SELECT b.file_name
           FROM netshop_import_batches b
            WHERE b.dataset = r.dataset AND b.source = r.source AND b.platform = r.platform
             AND (? = '' OR b.shop_name = ? OR b.source = 'inv_selfop')
             AND b.status = 'completed'
           ORDER BY b.completed_at DESC, b.created_at DESC
           LIMIT 1
         ) AS latest_file_name,
         (
           SELECT b.completed_at
           FROM netshop_import_batches b
            WHERE b.dataset = r.dataset AND b.source = r.source AND b.platform = r.platform
             AND (? = '' OR b.shop_name = ? OR b.source = 'inv_selfop')
             AND b.status = 'completed'
           ORDER BY b.completed_at DESC, b.created_at DESC
           LIMIT 1
         ) AS completed_at
       FROM netshop_rows r
        WHERE (? = '' OR r.shop_name = ? OR r.source = 'inv_selfop')${platformClause}
        GROUP BY dataset, source, platform
       ORDER BY dataset`,
    )
    .bind(shop ?? "", shop ?? "", shop ?? "", shop ?? "", shop ?? "", shop ?? "", shop ?? "", shop ?? "", ...platforms)
    .all<{
      dataset: string;
      source: string;
      date_min: string | null;
      date_max: string | null;
      snapshot_date: string | null;
      row_count: number;
      latest_batch_id: string | null;
      latest_file_name: string | null;
      completed_at: string | null;
    }>();

  const datasets: Record<string, NetshopOverviewDataset> = {};
  for (const row of rows.results) {
    datasets[row.dataset] = {
      source: row.source,
      dataset: row.dataset,
      dateMin: row.date_min,
      dateMax: row.date_max,
      snapshotDate: row.snapshot_date,
      rowCount: Number(row.row_count),
      latestBatchId: row.latest_batch_id,
      latestFileName: row.latest_file_name,
      completedAt: row.completed_at,
    };
  }
  return {
    shop,
    filters: { shop },
    datasets,
    date_max: Object.fromEntries(Object.entries(datasets).map(([key, value]) => [key, value.dateMax])),
  };
}

type NetshopProductRow = {
  source: NetshopSource;
  platform: string;
  shop_name: string;
  snapshot_date: string | null;
  spu_id: string;
  sku_id: string;
  product_code: string;
  product_name: string;
  metrics_json: string;
  raw_json: string;
  image_raw_json: string | null;
};

type NetshopProductSummaryRow = {
  total_skus: number;
  on_sale_skus: number | null;
  total_inventory: number | null;
  available_inventory: number | null;
};

type NetshopProductSalesMetrics = Pick<
  NetshopProductCatalogItem,
  "costPriceCents" | "netSalesCents" | "grossMarginRate" | "refundRate" | "salesMatched"
>;

type NetshopProductCatalogInternalItem = NetshopProductCatalogItem & {
  salesProductCode: string;
};

type NetshopProductSalesIdentity = {
  platform: string;
  shopName: string;
  salesProductCode: string;
};

type NetshopProductSalesMatch = {
  platform: string;
  canonicalShopName: string;
  rawShopName: string;
  rawChannel: string | null;
  salesProductCode: string;
};

function rawText(raw: Record<string, unknown>, key: string) {
  const value = raw[key];
  return value === null || value === undefined ? "" : String(value).trim();
}

function rawNumber(raw: Record<string, unknown>, key: string) {
  const value = raw[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const parsed = Number(String(value ?? "").replace(/[￥¥,]/g, "").trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeImageUrl(value: string) {
  const match = value.trim().match(/(?:https?:)?\/\/[^\s"'<>]+/i);
  if (!match) return "";
  return match[0].startsWith("//") ? `https:${match[0]}` : match[0];
}

function productImageUrl(...rawSources: Array<Record<string, unknown>>) {
  const preferredKeys = [
    "主图链接", "商品主图链接", "SKU主图链接", "图片链接", "商品图片链接",
    "主图", "商品主图", "SKU主图", "图片", "商品图片", "imageUrl", "image_url", "image", "img", "pic",
  ];
  for (const raw of rawSources) {
    for (const key of preferredKeys) {
      const imageUrl = normalizeImageUrl(rawText(raw, key));
      if (imageUrl) return imageUrl;
    }
    for (const [key, value] of Object.entries(raw)) {
      if (!/(主图|图片|image|img|pic)/i.test(key)) continue;
      const imageUrl = normalizeImageUrl(String(value ?? ""));
      if (imageUrl) return imageUrl;
    }
  }
  return "";
}

const isIsoDate = isNetshopIsoDate;

function addIsoDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function dailyDateCoverageForQuery(startDate: string, endDate: string, actualValues: readonly string[]) {
  const actualDates = [...new Set(actualValues.filter(Boolean))].sort();
  const actual = new Set(actualDates);
  const missingDates: string[] = [];
  for (let date = startDate; date <= endDate; date = addIsoDays(date, 1)) {
    if (!actual.has(date)) missingDates.push(date);
  }
  return { actualDates, missingDates };
}

async function latestProductBatches(db: NetshopDatabase) {
  const rows = await db
    .prepare(
      `WITH ranked AS (
         SELECT ${batchColumns},
           ROW_NUMBER() OVER (
             PARTITION BY platform,shop_name
             ORDER BY completed_at DESC,created_at DESC,id DESC
           ) AS scope_rank
         FROM netshop_import_batches
         WHERE source IN ('jd_product_master','tmall_product_master') AND status='completed'
       )
       SELECT ${batchColumns} FROM ranked WHERE scope_rank=1
       ORDER BY completed_at DESC,created_at DESC,id DESC`,
    )
    .all<NetshopBatchRow>();
  return rows.results.map(mapBatch);
}

type NetshopAssetHeadRow = {
  source: string;
  dataset: string;
  platform: string;
  shop_name: string;
  batch_id: string;
  snapshot_date: string | null;
};

async function sha256SnapshotPayload(value: unknown) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(value)),
  );
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function readLatestProductAssetHeads(
  db: NetshopDatabase,
  scopes: readonly { platform: string; shopName: string }[],
  includeJd: boolean,
  includeTmall: boolean,
) {
  const requested = [...new Map(scopes
    .map((scope) => ({ platform: scope.platform.trim(), shopName: scope.shopName.trim() }))
    .filter((scope) => scope.platform && scope.shopName)
    .map((scope) => [`${scope.platform}\u001f${scope.shopName}`, scope])).values()]
    .sort((left, right) => left.platform.localeCompare(right.platform) || left.shopName.localeCompare(right.shopName));
  if (requested.length === 0 || (!includeJd && !includeTmall)) return [] as NetshopAssetHeadRow[];
  const sourcePredicates = [
    includeJd ? "(batch.source = 'jd_yimei_sku' AND batch.platform = '京东')" : "",
    includeTmall ? "(batch.source = 'tmall_product_assets' AND batch.dataset = 'spu_assets' AND batch.platform = '天猫')" : "",
  ].filter(Boolean);
  const rows = await db.prepare(
    `WITH requested AS (
       SELECT DISTINCT
         CAST(json_extract(value, '$.platform') AS TEXT) AS platform,
         CAST(json_extract(value, '$.shopName') AS TEXT) AS shop_name
       FROM json_each(?)
     ), ranked AS (
       SELECT batch.source, batch.dataset, batch.platform, batch.shop_name,
         batch.id AS batch_id, batch.snapshot_date,
         ROW_NUMBER() OVER (
           PARTITION BY batch.source, batch.dataset, batch.platform, batch.shop_name
           ORDER BY batch.snapshot_date DESC, batch.completed_at DESC, batch.created_at DESC, batch.id DESC
         ) AS batch_rank
       FROM netshop_import_batches batch
       WHERE batch.status = 'completed'
         AND (${sourcePredicates.join(" OR ")})
         AND EXISTS (
           SELECT 1 FROM requested
           WHERE requested.platform = batch.platform
             AND (
               requested.shop_name = batch.shop_name
               OR (batch.source = 'jd_yimei_sku' AND batch.shop_name = '')
             )
         )
     )
     SELECT source, dataset, platform, shop_name, batch_id, snapshot_date
     FROM ranked
     WHERE batch_rank = 1
     ORDER BY source, dataset, platform, shop_name`,
  ).bind(JSON.stringify(requested)).all<NetshopAssetHeadRow>();
  return rows.results;
}

type ProductCatalogSnapshotInput = {
  requestedPlatforms: readonly string[];
  requestedOutlets: readonly NetshopOutletFilter[];
  salesPeriod: NetshopQueryPeriod | null;
  salesChannels: readonly string[] | null;
  includeShopOptions: boolean;
};

async function readProductCatalogSnapshot(
  db: NetshopDatabase,
  input: ProductCatalogSnapshotInput,
  salesRevision: string,
) {
  const latestBatches = await latestProductBatches(db);
  const requestedOutletKeys = new Set(input.requestedOutlets.map((value) => `${value.platform}\u001f${value.shopName}`));
  const visibleBatches = latestBatches.filter((batch) => input.requestedPlatforms.length === 0 || input.requestedPlatforms.includes(batch.platform));
  const batches = visibleBatches.filter((batch) => requestedOutletKeys.size === 0 || requestedOutletKeys.has(`${batch.platform}\u001f${batch.shopName}`));
  const assetScopes = batches.map((batch) => ({ platform: batch.platform, shopName: batch.shopName }));
  const hasJd = batches.some((batch) => batch.platform === "京东");
  const hasTmall = batches.some((batch) => batch.platform === "天猫");
  const assetHeads = await readLatestProductAssetHeads(db, assetScopes, hasJd, hasTmall);
  const snapshotToken = await sha256SnapshotPayload({
    version: 1,
    platforms: [...input.requestedPlatforms].sort(),
    outlets: [...input.requestedOutlets]
      .map((outlet) => ({ platform: outlet.platform, shopName: outlet.shopName }))
      .sort((left, right) => left.platform.localeCompare(right.platform) || left.shopName.localeCompare(right.shopName)),
    salesPeriod: input.salesPeriod
      ? { startDate: input.salesPeriod.startDate, endDate: input.salesPeriod.endDate }
      : null,
    salesChannels: input.salesChannels === null ? null : [...input.salesChannels].sort(),
    selectedProductHeads: batches.map((batch) => ({
      id: batch.id,
      source: batch.source,
      platform: batch.platform,
      shopName: batch.shopName,
      snapshotDate: batch.snapshotDate,
    })).sort((left, right) => left.platform.localeCompare(right.platform) || left.shopName.localeCompare(right.shopName)),
    assetHeads,
    salesRevision: hasJd ? salesRevision : null,
  });
  const shopOptionsSnapshotToken = input.includeShopOptions
    ? await sha256SnapshotPayload({
      version: 1,
      visibleProductHeads: visibleBatches.map((batch) => ({
        id: batch.id,
        source: batch.source,
        platform: batch.platform,
        shopName: batch.shopName,
        snapshotDate: batch.snapshotDate,
      })).sort((left, right) => left.platform.localeCompare(right.platform) || left.shopName.localeCompare(right.shopName)),
    })
    : null;
  return { latestBatches, visibleBatches, batches, snapshotToken, shopOptionsSnapshotToken };
}

async function verifyProductCatalogSnapshot(
  db: NetshopDatabase,
  input: ProductCatalogSnapshotInput,
  expectedSnapshotToken: string,
  expectedShopOptionsSnapshotToken: string | null,
  principal: AppPrincipal,
  salesReader: SalesConsumerReader,
  expectedSalesRevision: string,
  signal?: AbortSignal,
) {
  const closingFreshness = await salesReader.read(principal, { operation: "freshness" }, { signal });
  if (!closingFreshness.revision || closingFreshness.revision !== expectedSalesRevision) {
    throw new PublicApiError(503, "service_unavailable", "货品目录在读取期间销售数据已更新，请重新加载后重试");
  }
  const closing = await readProductCatalogSnapshot(db, input, expectedSalesRevision);
  if (closing.snapshotToken !== expectedSnapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "货品目录在读取期间已更新，请重新加载后重试");
  }
  if (closing.shopOptionsSnapshotToken !== expectedShopOptionsSnapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "货品目录店铺选项在读取期间已更新，请重新加载后重试");
  }
}

function emptyNetshopProductSalesMetrics(): NetshopProductSalesMetrics {
  return {
    costPriceCents: null,
    netSalesCents: null,
    grossMarginRate: null,
    refundRate: null,
    salesMatched: false,
  };
}

async function readJdProductSalesMetrics(
  principal: AppPrincipal,
  salesReader: SalesConsumerReader,
  productIdentities: readonly NetshopProductSalesIdentity[],
  outletScopes: readonly NetshopOutletFilter[],
  salesPeriod: NetshopQueryPeriod | null,
  allowedChannels: readonly string[] | null,
  expectedRevision: string,
  includeDataCutoff = true,
  signal?: AbortSignal,
) {
  const identities = [...new Map(productIdentities
    .flatMap((identity): NetshopProductSalesMatch[] => {
      const salesProductCode = identity.salesProductCode.trim();
      if (identity.platform.trim() !== "京东" || !identity.shopName.trim()
        || !salesProductCode || salesProductCode === "--") return [];
      return resolveNetshopSalesOutletMatches(identity.platform, identity.shopName).map((match) => ({
        ...match,
        salesProductCode,
      }));
    })
    .map((identity) => [JSON.stringify([
      identity.platform,
      identity.canonicalShopName,
      identity.rawShopName,
      identity.rawChannel,
      identity.salesProductCode,
    ]), identity])).values()];
  const scopes = [...new Map(outletScopes
    .flatMap((scope) => scope.platform.trim() === "京东"
      ? resolveNetshopSalesOutletMatches(scope.platform, scope.shopName)
      : [])
    .map((scope) => [JSON.stringify([
      scope.platform,
      scope.canonicalShopName,
      scope.rawShopName,
      scope.rawChannel,
    ]), scope])).values()];
  const channelScope = allowedChannels === null
    ? null
    : [...new Set(allowedChannels.map((channel) => channel.trim()).filter(Boolean))];
  const salesScope = "京东";
  if (channelScope !== null && channelScope.length === 0) {
    return { revision: expectedRevision, metrics: new Map<string, NetshopProductSalesMetrics>(), dataCutoffDate: null, platform: salesScope };
  }
  if (identities.length === 0 && scopes.length === 0) {
    return { revision: expectedRevision, metrics: new Map<string, NetshopProductSalesMetrics>(), dataCutoffDate: null, platform: salesScope };
  }
  const result = await salesReader.read(principal, {
    operation: "netshop_product_metrics",
    identities: salesPeriod ? identities : [],
    outletScopes: scopes,
    startDate: salesPeriod?.startDate ?? null,
    endDate: salesPeriod?.endExclusive ?? null,
    allowedChannels: channelScope,
  }, { signal });
  const data = result.data;
  if (!result.revision || result.revision !== expectedRevision
    || !data || data.platform !== salesScope || !Array.isArray(data.rows)
    || (data.dataCutoffDate !== null && !isNetshopIsoDate(data.dataCutoffDate))) {
    throw new PublicApiError(503, "service_unavailable", "Django 销售读取服务返回了无效的网店指标。");
  }

  const metrics = new Map<string, NetshopProductSalesMetrics>();
  const expectedKeys = new Set((salesPeriod ? identities : []).map((identity) => JSON.stringify([
    identity.platform,
    identity.canonicalShopName,
    identity.salesProductCode,
  ])));
  for (const row of data.rows) {
    const key = JSON.stringify([row.platform, row.shopName, row.salesProductCode]);
    const values = [row.grossSalesCents, row.refundAmountCents, row.netSalesCents, row.grossProfitCents, row.absoluteQuantity, row.absoluteCostCents];
    if (typeof row.platform !== "string" || typeof row.shopName !== "string"
      || typeof row.salesProductCode !== "string" || !expectedKeys.has(key) || metrics.has(key)
      || values.some((value) => !Number.isSafeInteger(value))
      || row.grossSalesCents < 0 || row.refundAmountCents < 0
      || row.absoluteQuantity < 0 || row.absoluteCostCents < 0) {
      throw new PublicApiError(503, "service_unavailable", "Django 销售读取服务返回了无效的网店指标。");
    }
    const grossSalesCents = row.grossSalesCents;
    const netSalesCents = row.netSalesCents;
    const grossProfitCents = row.grossProfitCents;
    const absoluteQuantity = row.absoluteQuantity;
    const absoluteCostCents = row.absoluteCostCents;
    metrics.set(key, {
      costPriceCents: absoluteQuantity > 0 ? absoluteCostCents / absoluteQuantity : null,
      netSalesCents,
      grossMarginRate: netSalesCents !== 0 ? grossProfitCents / netSalesCents : null,
      refundRate: grossSalesCents > 0 ? row.refundAmountCents / grossSalesCents : null,
      salesMatched: true,
    });
  }
  return {
    revision: result.revision,
    metrics,
    dataCutoffDate: includeDataCutoff ? data.dataCutoffDate : null,
    platform: data.platform,
  };
}

function mapNetshopProductRow(row: NetshopProductRow): NetshopProductCatalogInternalItem {
  const raw = parseJson<Record<string, unknown>>(row.raw_json, {});
  const imageRaw = parseJson<Record<string, unknown>>(row.image_raw_json ?? "{}", {});
  const category = [rawText(raw, "类目名称"), ...["一级类目", "二级类目", "三级类目", "末级类目"].map((key) => rawText(raw, key))]
    .filter((value) => value && value !== "--")
    .join(" / ");
  const metrics = parseJson<Record<string, unknown>>(row.metrics_json, {});
  const priceCents = typeof metrics.skuPriceCents === "number" ? metrics.skuPriceCents : rawNumber(raw, "SKU价格") !== null ? Math.round(Number(rawNumber(raw, "SKU价格")) * 100) : null;
  const spuId = row.spu_id || rawText(raw, "商品ID") || rawText(raw, "商品编码");
  const productUrl = rawText(imageRaw, "商品链接") || rawText(raw, "商品链接")
    || (row.platform === "天猫" && spuId ? `https://detail.tmall.com/item.htm?id=${encodeURIComponent(spuId)}` : "");
  return {
    platform: row.platform,
    shopName: row.shop_name,
    spuId,
    skuId: row.sku_id || rawText(raw, "SKUID"),
    productCode: rawText(raw, "SKU商家编码") || rawText(raw, "商品编码") || row.product_code,
    productName: row.product_name || rawText(raw, "商品名称"),
    imageUrl: netshopProductImageUrl(imageRaw) || productImageUrl(raw, imageRaw),
    salesProductCode: rawText(raw, "商家SKU") || rawText(raw, "SKU商家编码"),
    saleAttribute: rawText(raw, "销售属性"),
    category,
    brand: rawText(raw, "品牌"),
    price: row.platform === "天猫" && priceCents !== null ? priceCents / 100 : rawNumber(raw, "京东价"),
    priceCents: row.platform === "天猫" ? priceCents : rawNumber(raw, "京东价") !== null ? Math.round(Number(rawNumber(raw, "京东价")) * 100) : null,
    totalInventory: rawNumber(raw, "商品总库存") ?? rawNumber(raw, "SKU库存"),
    availableInventory: rawNumber(raw, "商品可用库存") ?? rawNumber(raw, "SKU库存"),
    status: rawText(raw, "商品状态"),
    productUrl,
    createdAt: rawText(raw, "创建时间"),
    snapshotDate: row.snapshot_date,
    ...emptyNetshopProductSalesMetrics(),
  };
}

type NetshopProductCatalogQueryInput = {
  query?: string;
  page?: number;
  pageSize?: number;
  outlets?: NetshopOutletFilter[];
  platformNames?: string[];
  salesStartDate?: string;
  salesEndDate?: string;
  salesChannels?: readonly string[] | null;
  signal?: AbortSignal;
};

async function readNetshopProductCatalogProjection(
  db: NetshopDatabase,
  principal: AppPrincipal,
  input: NetshopProductCatalogQueryInput,
  view: "full" | "page",
  expectedSnapshotToken?: string,
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
): Promise<NetshopProductCatalog | NetshopProductCatalogPage> {
  const page = boundedNetshopInteger(input.page, "page", 1, 1, NETSHOP_QUERY_MAX_PAGE);
  const pageSize = boundedNetshopInteger(input.pageSize, "pageSize", 50, 1, NETSHOP_QUERY_MAX_PAGE_SIZE);
  const salesPeriod = resolveNetshopQueryPeriod(input.salesStartDate, input.salesEndDate);
  const requestedOutlets = normalizeNetshopOutletFilters(input.outlets);
  const requestedPlatforms = [...new Set((input.platformNames ?? []).map((value) => value.trim()).filter(Boolean))].sort();
  assertNetshopOutletPlatformSelection(requestedPlatforms, requestedOutlets);
  const query = (input.query ?? "").trim().slice(0, 120);
  const salesChannels = input.salesChannels === undefined ? null : input.salesChannels;
  if (view === "page" && (!expectedSnapshotToken || !/^[a-f0-9]{64}$/.test(expectedSnapshotToken))) {
    throw new NetshopQueryError("snapshot_token_required", "page 视图必须提供有效 snapshotToken");
  }
  const snapshotInput: ProductCatalogSnapshotInput = {
    requestedPlatforms,
    requestedOutlets,
    salesPeriod,
    salesChannels,
    includeShopOptions: view === "full" && requestedOutlets.length > 0,
  };
  const salesFreshness = await salesReader.read(principal, { operation: "freshness" }, { signal: input.signal });
  if (!salesFreshness.revision || salesFreshness.revision.length > 128) {
    throw new PublicApiError(503, "service_unavailable", "Django 销售读取服务返回了无效版本。");
  }
  const opening = await readProductCatalogSnapshot(db, snapshotInput, salesFreshness.revision);
  if (view === "page" && opening.snapshotToken !== expectedSnapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "货品目录版本已变化，请重新加载");
  }
  const { visibleBatches, batches } = opening;
  const batch = batches[0] ?? null;
  const shops = visibleBatches
    .map((item) => ({ shopName: item.shopName, platform: item.platform, snapshotDate: item.snapshotDate, completedAt: item.completedAt }))
    .sort((left, right) => left.platform.localeCompare(right.platform, "zh-CN") || left.shopName.localeCompare(right.shopName, "zh-CN"));
  const emptySales = {
    periodStart: salesPeriod?.startDate ?? null,
    periodEnd: salesPeriod?.endDate ?? null,
    dataCutoffDate: null,
    platform: "京东",
  };
  if (!batch) {
    const pagePayload: NetshopProductCatalogPage = {
      snapshotToken: opening.snapshotToken,
      items: [],
      pagination: { page, pageSize, total: 0, returned: 0, truncated: false },
    };
    await verifyProductCatalogSnapshot(
      db,
      snapshotInput,
      opening.snapshotToken,
      opening.shopOptionsSnapshotToken,
      principal,
      salesReader,
      salesFreshness.revision,
      input.signal,
    );
    if (view === "page") return pagePayload;
    return {
      ...pagePayload,
      batch: null,
      summary: { totalSkus: 0, onSaleSkus: 0, totalInventory: 0, availableInventory: 0 },
      shops,
      sales: emptySales,
    };
  }

  const batchIds = batches.map((item) => item.id);
  const authoritativeTotal = batches.reduce((sum, item) => sum + item.rowCount, 0);
  if (!Number.isSafeInteger(authoritativeTotal) || authoritativeTotal < 0) {
    throw new PublicApiError(503, "service_unavailable", "货品目录批次行数无效，请稍后重试");
  }
  const batchFrom = "json_each(?) requested_batch CROSS JOIN netshop_rows product";
  const batchClause = `product.source IN ('jd_product_master', 'tmall_product_master')
    AND product.dataset = 'product_master'
    AND product.last_import_batch_id = CAST(requested_batch.value AS TEXT)`;
  const batchBinding = JSON.stringify(batchIds);
  const searchClause = query
    ? " AND (product.shop_name LIKE ? OR product.spu_id LIKE ? OR product.sku_id LIKE ? OR product.product_code LIKE ? OR product.product_name LIKE ?)"
    : "";
  const searchTerm = `%${query}%`;
  const bindings = query
    ? [batchBinding, searchTerm, searchTerm, searchTerm, searchTerm, searchTerm]
    : [batchBinding];
  const offset = (page - 1) * pageSize;
  const summaryPromise = view === "full"
    ? db.prepare(
      `SELECT
         COUNT(*) AS total_skus,
         SUM(CASE WHEN json_extract(product.raw_json, '$."商品状态"') = '上架' THEN 1 ELSE 0 END) AS on_sale_skus,
         SUM(COALESCE(CAST(json_extract(product.metrics_json, '$.inventoryQuantity') AS REAL), CAST(json_extract(product.raw_json, '$."商品总库存"') AS REAL), 0)) AS total_inventory,
         SUM(COALESCE(CAST(json_extract(product.metrics_json, '$.inventoryQuantity') AS REAL), CAST(json_extract(product.raw_json, '$."商品可用库存"') AS REAL), 0)) AS available_inventory
       FROM ${batchFrom}
       WHERE ${batchClause}`,
    ).bind(batchBinding).first<NetshopProductSummaryRow>()
    : Promise.resolve(null);
  const searchedTotalPromise = query
    ? db.prepare(
      `SELECT COUNT(*) AS total
       FROM ${batchFrom}
       WHERE ${batchClause}${searchClause}`,
    ).bind(...bindings).first<{ total: number | null }>()
    : Promise.resolve(null);
  const rowsPromise = db.prepare(
    `SELECT
         product.source,
         product.platform,
         product.shop_name,
         product.snapshot_date,
         product.spu_id,
         product.sku_id,
         product.product_code,
         product.product_name,
         product.metrics_json,
         product.raw_json,
         (
           SELECT image_row.raw_json
           FROM netshop_rows image_row
           JOIN netshop_import_batches image_batch
             ON image_batch.id = image_row.last_import_batch_id
            WHERE image_batch.status = 'completed'
              AND image_batch.source = image_row.source
              AND image_batch.dataset = image_row.dataset
              AND image_row.platform = product.platform
              AND image_batch.platform = product.platform
              AND TRIM(image_batch.shop_name) = TRIM(product.shop_name)
              AND (
                (
                  product.platform = '天猫'
                  AND image_row.source = 'tmall_product_assets'
                  AND image_row.dataset = 'spu_assets'
                  AND TRIM(image_row.shop_name) = TRIM(product.shop_name)
                  AND image_row.spu_id <> ''
                  AND image_row.spu_id = product.spu_id
                  AND image_row.last_import_batch_id = (
                    SELECT latest_asset_batch.id
                    FROM netshop_import_batches latest_asset_batch
                    WHERE latest_asset_batch.source = 'tmall_product_assets'
                      AND latest_asset_batch.dataset = 'spu_assets'
                      AND latest_asset_batch.status = 'completed'
                      AND latest_asset_batch.platform = product.platform
                      AND TRIM(latest_asset_batch.shop_name) = TRIM(product.shop_name)
                    ORDER BY latest_asset_batch.snapshot_date DESC,
                      latest_asset_batch.completed_at DESC,
                      latest_asset_batch.created_at DESC,
                      latest_asset_batch.id DESC
                    LIMIT 1
                  )
                )
                OR (
                  product.platform = '京东'
                  AND image_row.source = 'jd_yimei_sku'
                  AND (TRIM(image_row.shop_name) = TRIM(product.shop_name) OR TRIM(image_row.shop_name) = '')
                  AND (
                    (image_row.sku_id <> '' AND image_row.sku_id = product.sku_id)
                    OR (image_row.product_code <> '' AND image_row.product_code = product.product_code)
                  )
                )
              )
            ORDER BY image_row.snapshot_date DESC, image_batch.completed_at DESC, image_batch.created_at DESC, image_row.id DESC
           LIMIT 1
         ) AS image_raw_json
       FROM ${batchFrom}
       WHERE ${batchClause}${searchClause}
       ORDER BY product.shop_name ASC, product.product_name ASC, product.sku_id ASC,
         product.platform ASC, product.id ASC
       LIMIT ? OFFSET ?`,
  ).bind(...bindings, pageSize, offset).all<NetshopProductRow>();
  const [summary, searchedTotal, rows] = await Promise.all([summaryPromise, searchedTotalPromise, rowsPromise]);
  if (view === "full" && Number(summary?.total_skus ?? 0) !== authoritativeTotal) {
    throw new PublicApiError(
      503,
      "service_unavailable",
      "货品目录批次元数据与已发布事实不一致，请完成数据修复后重试",
    );
  }
  const total = query ? Number(searchedTotal?.total ?? 0) : authoritativeTotal;

  const rawItems = rows.results.map(mapNetshopProductRow);
  const jdItems = rawItems.filter((item) => item.platform === "京东");
  const jdOutletScopes = batches
    .filter((item) => item.platform === "京东")
    .map((item) => ({ platform: item.platform, shopName: item.shopName }));
  const sales = view === "full" || (Boolean(salesPeriod) && jdItems.length > 0)
    ? await readJdProductSalesMetrics(
      principal,
      salesReader,
      jdItems.map((item) => ({
        platform: item.platform,
        shopName: item.shopName,
        salesProductCode: item.salesProductCode,
      })),
      jdOutletScopes,
      salesPeriod,
      salesChannels,
      salesFreshness.revision,
      view === "full",
      input.signal,
    )
    : { revision: salesFreshness.revision, metrics: new Map<string, NetshopProductSalesMetrics>(), dataCutoffDate: null, platform: "京东" };
  const pagePayload: NetshopProductCatalogPage = {
    snapshotToken: opening.snapshotToken,
    items: rawItems.map(({ salesProductCode, ...item }) => ({
      ...item,
      ...(item.platform === "京东"
        ? sales.metrics.get(JSON.stringify([item.platform, item.shopName, salesProductCode])) ?? emptyNetshopProductSalesMetrics()
        : emptyNetshopProductSalesMetrics()),
    })),
    pagination: {
      page,
      pageSize,
      total,
      returned: rawItems.length,
      truncated: offset + rawItems.length < total,
    },
  };
  await verifyProductCatalogSnapshot(
    db,
    snapshotInput,
    opening.snapshotToken,
    opening.shopOptionsSnapshotToken,
    principal,
    salesReader,
    salesFreshness.revision,
    input.signal,
  );
  if (view === "page") return pagePayload;
  return {
    ...pagePayload,
    batch,
    summary: {
      totalSkus: Number(summary?.total_skus ?? 0),
      onSaleSkus: Number(summary?.on_sale_skus ?? 0),
      totalInventory: Number(summary?.total_inventory ?? 0),
      availableInventory: Number(summary?.available_inventory ?? 0),
    },
    shops,
    sales: {
      periodStart: salesPeriod?.startDate ?? null,
      periodEnd: salesPeriod?.endDate ?? null,
      dataCutoffDate: sales.dataCutoffDate,
      platform: sales.platform,
    },
  };
}

export async function getNetshopProductCatalog(
  db: NetshopDatabase,
  principal: AppPrincipal,
  input: NetshopProductCatalogQueryInput = {},
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
): Promise<NetshopProductCatalog> {
  return readNetshopProductCatalogProjection(db, principal, input, "full", undefined, salesReader) as Promise<NetshopProductCatalog>;
}

export async function getNetshopProductCatalogPage(
  db: NetshopDatabase,
  principal: AppPrincipal,
  input: NetshopProductCatalogQueryInput & { snapshotToken: string },
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
): Promise<NetshopProductCatalogPage> {
  return readNetshopProductCatalogProjection(db, principal, input, "page", input.snapshotToken, salesReader) as Promise<NetshopProductCatalogPage>;
}

type NetshopProductPerformanceSummaryRow = {
  product_count: number | null;
  date_count: number | null;
  date_min: string | null;
  data_cutoff_date: string | null;
  page_views: number | null;
  visitors: number | null;
  search_impressions: number | null;
  search_clicks: number | null;
  add_cart_customers: number | null;
  add_cart_quantity: number | null;
  order_customers: number | null;
  order_quantity: number | null;
  order_amount: number | null;
  transaction_orders: number | null;
  transaction_amount: number | null;
  transaction_quantity: number | null;
  transaction_customers: number | null;
  favorites: number | null;
  refund_amount_cents: number | null;
  search_visitors: number | null;
  search_transaction_customers: number | null;
};

type NetshopProductPerformanceRow = {
  id: string;
  platform: string;
  shop_name: string;
  sku_id: string;
  spu_id: string;
  product_code: string;
  product_name: string;
  category: string | null;
  shop_names: string | null;
  date_min: string | null;
  date_max: string | null;
  data_days: number | null;
  page_views: number | null;
  visitors: number | null;
  search_impressions: number | null;
  search_clicks: number | null;
  add_cart_customers: number | null;
  add_cart_quantity: number | null;
  order_customers: number | null;
  order_quantity: number | null;
  order_amount: number | null;
  transaction_orders: number | null;
  transaction_amount: number | null;
  transaction_quantity: number | null;
  transaction_customers: number | null;
  favorites: number | null;
  refund_amount_cents: number | null;
  search_visitors: number | null;
  search_transaction_customers: number | null;
  total_items?: number | null;
};

type NetshopProductAssetLookupRow = {
  platform: string;
  shop_name: string;
  spu_id: string;
  raw_json: string;
};

type NetshopProductPerformanceDailyRow = {
  business_date: string;
  page_views: number | null;
  visitors: number | null;
  transaction_customers: number | null;
  transaction_quantity: number | null;
  transaction_amount_cents: number | null;
  refund_amount_cents: number | null;
  favorites: number | null;
  add_cart_customers: number | null;
  add_cart_quantity: number | null;
};

type NetshopProductPerformanceShopRow = {
  shop_name: string;
  platform: string;
  product_count: number | null;
};

type NetshopProductPerformanceAvailableCoverageRow = {
  date_min: string | null;
  date_max: string | null;
};

type NetshopProductPerformanceFullProjectionRow = NetshopProductPerformanceSummaryRow & {
  items_json: string;
  shops_json: string;
  available_date_min: string | null;
  available_date_max: string | null;
  daily_json: string;
};

const dailyPerformanceMetrics = {
  pageViews: `COALESCE(CAST(json_extract(r.metrics_json, '$.pageViews') AS REAL), CAST(json_extract(r.metrics_json, '$."商品浏览量"') AS REAL), 0)`,
  visitors: `COALESCE(CAST(json_extract(r.metrics_json, '$.visitors') AS REAL), CAST(json_extract(r.metrics_json, '$."商品访客数"') AS REAL), 0)`,
  searchImpressions: `COALESCE(CAST(json_extract(r.metrics_json, '$."搜索曝光次数"') AS REAL), 0)`,
  searchClicks: `COALESCE(CAST(json_extract(r.metrics_json, '$."搜索点击次数"') AS REAL), 0)`,
  addCartCustomers: `COALESCE(CAST(json_extract(r.metrics_json, '$.addCartCustomers') AS REAL), CAST(json_extract(r.metrics_json, '$."加购客户数"') AS REAL), 0)`,
  addCartQuantity: `COALESCE(CAST(json_extract(r.metrics_json, '$.addCartQuantity') AS REAL), CAST(json_extract(r.metrics_json, '$."加购商品件数"') AS REAL), 0)`,
  orderCustomers: `COALESCE(CAST(json_extract(r.metrics_json, '$.orderCustomers') AS REAL), CAST(json_extract(r.metrics_json, '$."下单客户数"') AS REAL), 0)`,
  orderQuantity: `COALESCE(CAST(json_extract(r.metrics_json, '$.orderQuantity') AS REAL), CAST(json_extract(r.metrics_json, '$."下单商品件数"') AS REAL), 0)`,
  orderAmountCents: `COALESCE(CAST(json_extract(r.metrics_json, '$.orderAmountCents') AS REAL), CAST(json_extract(r.metrics_json, '$."下单金额"') AS REAL) * 100, 0)`,
  transactionOrders: `COALESCE(CAST(json_extract(r.metrics_json, '$."成交单量"') AS REAL), 0)`,
  transactionAmountCents: `COALESCE(CAST(json_extract(r.metrics_json, '$.transactionAmountCents') AS REAL), CAST(json_extract(r.metrics_json, '$."成交金额"') AS REAL) * 100, 0)`,
  transactionQuantity: `COALESCE(CAST(json_extract(r.metrics_json, '$.transactionQuantity') AS REAL), CAST(json_extract(r.metrics_json, '$."成交商品件数"') AS REAL), 0)`,
  transactionCustomers: `COALESCE(CAST(json_extract(r.metrics_json, '$.transactionCustomers') AS REAL), CAST(json_extract(r.metrics_json, '$."成交客户数"') AS REAL), 0)`,
  favorites: `COALESCE(CAST(json_extract(r.metrics_json, '$.favorites') AS REAL), 0)`,
  refundAmountCents: `COALESCE(CAST(json_extract(r.metrics_json, '$.refundAmountCents') AS REAL), 0)`,
  searchVisitors: `COALESCE(CAST(json_extract(r.metrics_json, '$.searchVisitors') AS REAL), 0)`,
  searchTransactionCustomers: `COALESCE(CAST(json_extract(r.metrics_json, '$.searchTransactionCustomers') AS REAL), 0)`,
} as const;

function numberFromDailyMetric(value: number | null | undefined) {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function hasExactProjectionFields(row: Record<string, unknown>, fields: readonly string[]) {
  const keys = Object.keys(row);
  return keys.length === fields.length && fields.every((field) => Object.hasOwn(row, field));
}

function hasProjectionText(row: Record<string, unknown>, fields: readonly string[], nullable = false) {
  return fields.every((field) => typeof row[field] === "string" || (nullable && row[field] === null));
}

function hasProjectionNumber(row: Record<string, unknown>, fields: readonly string[]) {
  return fields.every((field) => row[field] === null
    || (typeof row[field] === "number" && Number.isFinite(row[field])));
}

const productPerformanceItemProjectionFields = [
  "id", "platform", "shop_name", "sku_id", "spu_id", "product_code", "product_name", "category", "shop_names",
  "date_min", "date_max", "data_days", "page_views", "visitors", "search_impressions", "search_clicks",
  "add_cart_customers", "add_cart_quantity", "order_customers", "order_quantity", "order_amount",
  "transaction_orders", "transaction_amount", "transaction_quantity", "transaction_customers", "favorites",
  "refund_amount_cents", "search_visitors", "search_transaction_customers",
] as const;
const productPerformanceItemProjectionNumbers = productPerformanceItemProjectionFields.slice(11);

function isProductPerformanceItemProjection(row: Record<string, unknown>) {
  return hasExactProjectionFields(row, productPerformanceItemProjectionFields)
    && hasProjectionText(row, ["id", "platform", "shop_name", "sku_id", "spu_id"])
    && hasProjectionText(row, ["product_code", "product_name", "category", "shop_names", "date_min", "date_max"], true)
    && hasProjectionNumber(row, productPerformanceItemProjectionNumbers);
}

const productPerformanceShopProjectionFields = ["shop_name", "platform", "product_count"] as const;

function isProductPerformanceShopProjection(row: Record<string, unknown>) {
  return hasExactProjectionFields(row, productPerformanceShopProjectionFields)
    && hasProjectionText(row, ["shop_name", "platform"])
    && hasProjectionNumber(row, ["product_count"]);
}

const productPerformanceDailyProjectionFields = [
  "business_date", "page_views", "visitors", "transaction_customers", "transaction_quantity",
  "transaction_amount_cents", "refund_amount_cents", "favorites", "add_cart_customers", "add_cart_quantity",
] as const;

function isProductPerformanceDailyProjection(row: Record<string, unknown>) {
  return hasExactProjectionFields(row, productPerformanceDailyProjectionFields)
    && hasProjectionText(row, ["business_date"])
    && hasProjectionNumber(row, productPerformanceDailyProjectionFields.slice(1));
}

function parseProductPerformanceProjectionRows<T extends Record<string, unknown>>(
  value: string,
  label: string,
  maximumRows: number | null,
  isValid: (row: Record<string, unknown>) => boolean,
) {
  let parsed: unknown;
  try {
    if (typeof value !== "string") throw new TypeError("projection is not text");
    parsed = JSON.parse(value);
  } catch {
    throw new PublicApiError(503, "service_unavailable", `${label}投影不是有效 JSON，请稍后重试`);
  }
  if (!Array.isArray(parsed)
    || (maximumRows !== null && parsed.length > maximumRows)
    || parsed.some((item) => !item
      || typeof item !== "object"
      || Array.isArray(item)
      || !isValid(item as Record<string, unknown>))) {
    throw new PublicApiError(503, "service_unavailable", `${label}投影结构无效，请稍后重试`);
  }
  return parsed as T[];
}

async function readTmallProductAssets(
  db: NetshopDatabase,
  performanceRows: readonly NetshopProductPerformanceRow[],
) {
  const identities = [...new Map(performanceRows
    .filter((row) => row.platform === "天猫" && row.shop_name.trim() && row.spu_id.trim())
    .map((row) => {
      const identity = { platform: row.platform, shopName: row.shop_name.trim(), spuId: row.spu_id.trim() };
      return [JSON.stringify([identity.platform, identity.shopName, identity.spuId]), identity] as const;
    })).values()];
  if (identities.length === 0) return new Map<string, { imageUrl: string; productUrl: string }>();

  const result = await db.prepare(
    `WITH requested AS (
       SELECT
         CAST(json_extract(value, '$.platform') AS TEXT) AS platform,
         CAST(json_extract(value, '$.shopName') AS TEXT) AS shop_name,
         CAST(json_extract(value, '$.spuId') AS TEXT) AS spu_id
       FROM json_each(?)
     ), requested_shops AS (
       SELECT DISTINCT platform, shop_name FROM requested
     ), latest_asset_batches AS (
       SELECT
         requested_shops.platform,
         requested_shops.shop_name,
         (
           SELECT batch.id
           FROM netshop_import_batches batch
           WHERE batch.source = 'tmall_product_assets'
             AND batch.dataset = 'spu_assets'
             AND batch.status = 'completed'
             AND batch.platform = requested_shops.platform
             AND batch.shop_name = requested_shops.shop_name
           ORDER BY batch.snapshot_date DESC, batch.completed_at DESC, batch.created_at DESC, batch.id DESC
           LIMIT 1
         ) AS batch_id
       FROM requested_shops
     ), ranked AS (
       SELECT
         requested.platform,
         requested.shop_name,
         requested.spu_id,
         asset.raw_json,
         ROW_NUMBER() OVER (
           PARTITION BY requested.platform, requested.shop_name, requested.spu_id
           ORDER BY asset.snapshot_date DESC, batch.completed_at DESC, batch.created_at DESC, asset.id DESC
         ) AS asset_rank
       FROM requested
       JOIN latest_asset_batches latest
         ON latest.platform = requested.platform
        AND latest.shop_name = requested.shop_name
       JOIN netshop_rows asset
         ON asset.source = 'tmall_product_assets'
        AND asset.dataset = 'spu_assets'
        AND asset.platform = requested.platform
        AND asset.shop_name = requested.shop_name
        AND asset.spu_id = requested.spu_id
        AND asset.last_import_batch_id = latest.batch_id
       JOIN netshop_import_batches batch
         ON batch.id = asset.last_import_batch_id
        AND batch.status = 'completed'
        AND batch.source = asset.source
        AND batch.dataset = asset.dataset
        AND batch.platform = requested.platform
        AND batch.shop_name = requested.shop_name
     )
     SELECT platform, shop_name, spu_id, raw_json
     FROM ranked
     WHERE asset_rank = 1`,
  ).bind(JSON.stringify(identities)).all<NetshopProductAssetLookupRow>();

  return new Map(result.results.map((row) => {
    const raw = parseJson<Record<string, unknown>>(row.raw_json, {});
    return [JSON.stringify([row.platform, row.shop_name, row.spu_id]), {
      imageUrl: netshopProductImageUrl(raw),
      productUrl: rawText(raw, "商品链接"),
    }];
  }));
}

function dailyPerformanceCategorySql() {
  return `COALESCE(
    NULLIF(json_extract(r.raw_json, '$."商品标签"'), ''),
    NULLIF(json_extract(r.raw_json, '$."类目名称"'), ''),
    NULLIF(json_extract(r.raw_json, '$."三级类目"'), ''),
    NULLIF(json_extract(r.raw_json, '$."二级类目"'), ''),
    NULLIF(json_extract(r.raw_json, '$."一级类目"'), ''),
    ''
  )`;
}

function appendNetshopOutletFilter(
  whereParts: string[],
  bindings: string[],
  outlets: NetshopOutletFilter[],
  rowAlias = "r",
) {
  if (outlets.length === 0) return;
  whereParts.push(`EXISTS (
    SELECT 1
    FROM json_each(?) AS outlet
    WHERE ${rowAlias}.platform = CAST(json_extract(outlet.value, '$.platform') AS TEXT)
      AND ${rowAlias}.shop_name = CAST(json_extract(outlet.value, '$.shopName') AS TEXT)
  )`);
  bindings.push(JSON.stringify(outlets));
}

function assertNetshopOutletPlatformSelection(
  platforms: string[],
  outlets: NetshopOutletFilter[],
) {
  if (platforms.length > 0 && outlets.some((outlet) => !platforms.includes(outlet.platform))) {
    throw new NetshopQueryError("invalid_outlet_filter", "outlet 平台必须属于当前 platform 筛选");
  }
}

function productPerformanceRevisionScopes(
  dimension: NetshopProductPerformanceDimension,
  platforms: readonly string[],
  outlets: readonly NetshopOutletFilter[],
) {
  const supportedPlatforms = dimension === "sku" ? ["京东"] : ["京东", "天猫"];
  const activePlatforms = (platforms.length > 0 ? platforms : supportedPlatforms)
    .filter((platform) => supportedPlatforms.includes(platform));
  if (outlets.length > 0) {
    return activePlatforms.flatMap((platform) => {
      const shopNames = outlets
        .filter((outlet) => outlet.platform === platform)
        .map((outlet) => outlet.shopName);
      return shopNames.length > 0 ? [{ platform, shopNames }] : [];
    });
  }
  return activePlatforms.map((platform) => ({ platform }));
}

function productPerformancePlatformRevisionScopes(
  dimension: NetshopProductPerformanceDimension,
  platforms: readonly string[],
) {
  const supportedPlatforms = dimension === "sku" ? ["京东"] : ["京东", "天猫"];
  return (platforms.length > 0 ? platforms : supportedPlatforms)
    .filter((platform) => supportedPlatforms.includes(platform))
    .map((platform) => ({ platform }));
}

async function readProductPerformanceAssetHeads(
  db: NetshopDatabase,
  platforms: readonly string[],
  outlets: readonly NetshopOutletFilter[],
) {
  const includesTmall = platforms.length === 0 || platforms.includes("天猫");
  if (!includesTmall) return [] as NetshopAssetHeadRow[];
  const tmallOutlets = outlets.filter((outlet) => outlet.platform === "天猫");
  if (outlets.length > 0) {
    return readLatestProductAssetHeads(db, tmallOutlets, false, true);
  }
  const rows = await db.prepare(
    `WITH ranked AS (
       SELECT batch.source, batch.dataset, batch.platform, batch.shop_name,
         batch.id AS batch_id, batch.snapshot_date,
         ROW_NUMBER() OVER (
           PARTITION BY batch.platform, batch.shop_name
           ORDER BY batch.snapshot_date DESC, batch.completed_at DESC, batch.created_at DESC, batch.id DESC
         ) AS batch_rank
       FROM netshop_import_batches batch
       WHERE batch.source = 'tmall_product_assets'
         AND batch.dataset = 'spu_assets'
         AND batch.status = 'completed'
         AND batch.platform = '天猫'
     )
     SELECT source, dataset, platform, shop_name, batch_id, snapshot_date
     FROM ranked WHERE batch_rank = 1
     ORDER BY platform, shop_name`,
  ).all<NetshopAssetHeadRow>();
  return rows.results;
}

type ProductPerformanceSnapshotInput = {
  dimension: NetshopProductPerformanceDimension;
  query: string;
  period: NetshopQueryPeriod | null;
  platforms: readonly string[];
  outlets: readonly NetshopOutletFilter[];
  includeAssets: boolean;
  includeShopOptions: boolean;
};

async function readProductPerformanceSnapshot(
  db: NetshopDatabase,
  input: ProductPerformanceSnapshotInput,
) {
  const revisionScopes = productPerformanceRevisionScopes(input.dimension, input.platforms, input.outlets);
  const [productDailyRevisions, assetHeads, shopOptionRevisions] = await Promise.all([
    readNetshopProductDailyRevisions(db, revisionScopes),
    input.includeAssets && input.dimension === "spu"
      ? readProductPerformanceAssetHeads(db, input.platforms, input.outlets)
      : Promise.resolve([] as NetshopAssetHeadRow[]),
    input.includeShopOptions
      ? readNetshopProductDailyRevisions(
        db,
        productPerformancePlatformRevisionScopes(input.dimension, input.platforms),
      )
      : Promise.resolve([]),
  ]);
  const snapshotToken = await sha256SnapshotPayload({
    version: 1,
    projectionFacts: input.includeAssets ? "items" : "summary",
    dimension: input.dimension,
    query: input.query,
    period: input.period ? { startDate: input.period.startDate, endDate: input.period.endDate } : null,
    platforms: [...input.platforms].sort(),
    outlets: [...input.outlets]
      .map((outlet) => ({ platform: outlet.platform, shopName: outlet.shopName }))
      .sort((left, right) => left.platform.localeCompare(right.platform) || left.shopName.localeCompare(right.shopName)),
    productDailyRevisions,
    assetHeads,
  });
  const shopOptionsSnapshotToken = input.includeShopOptions
    ? await sha256SnapshotPayload({ version: 1, shopOptionRevisions })
    : null;
  return { snapshotToken, shopOptionsSnapshotToken };
}

async function verifyProductPerformanceSnapshot(
  db: NetshopDatabase,
  input: ProductPerformanceSnapshotInput,
  expectedSnapshotToken: string,
  expectedShopOptionsSnapshotToken: string | null,
) {
  const closing = await readProductPerformanceSnapshot(db, input);
  if (closing.snapshotToken !== expectedSnapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "商品日数据在读取期间已更新，请重新加载后重试");
  }
  if (closing.shopOptionsSnapshotToken !== expectedShopOptionsSnapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "商品日店铺选项在读取期间已更新，请重新加载后重试");
  }
}

/**
 * Aggregates the imported JD Business Intelligence product-detail workbooks.
 * The data is deliberately read-only: each SKU/SPU day remains the source of
 * truth and is only summed for the selected analysis range.
 */
type NetshopProductPerformanceQueryInput = {
  dimension: NetshopProductPerformanceDimension;
  query?: string;
  page?: number;
  pageSize?: number;
  platformNames?: string[];
  outlets?: NetshopOutletFilter[];
  startDate?: string;
  endDate?: string;
};

async function readNetshopProductPerformanceProjection(
  db: NetshopDatabase,
  input: NetshopProductPerformanceQueryInput,
  view: "summary" | "full" | "page",
  expectedSnapshotToken?: string,
): Promise<NetshopProductPerformance | NetshopProductPerformanceSummary | NetshopProductPerformancePage> {
  const page = boundedNetshopInteger(input.page, "page", 1, 1, NETSHOP_QUERY_MAX_PAGE);
  const pageSize = boundedNetshopInteger(input.pageSize, "pageSize", 50, 1, NETSHOP_QUERY_MAX_PAGE_SIZE);
  const dataset = input.dimension === "sku" ? "sku_daily" : "spu_daily";
  const dimensionSql = input.dimension === "sku" ? "r.sku_id" : "r.spu_id";
  const period = resolveNetshopQueryPeriod(input.startDate, input.endDate);
  const startDate = period?.startDate ?? null;
  const endDate = period?.endDate ?? null;
  const selectedPlatforms = [...new Set((input.platformNames ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, 20);
  const selectedOutlets = normalizeNetshopOutletFilters(input.outlets);
  assertNetshopOutletPlatformSelection(selectedPlatforms, selectedOutlets);
  const query = (input.query ?? "").trim().slice(0, 120);
  if (view === "page" && (!expectedSnapshotToken || !/^[a-f0-9]{64}$/.test(expectedSnapshotToken))) {
    throw new NetshopQueryError("snapshot_token_required", "page 视图必须提供有效 snapshotToken");
  }
  const snapshotInput: ProductPerformanceSnapshotInput = {
    dimension: input.dimension,
    query,
    period,
    platforms: selectedPlatforms,
    outlets: selectedOutlets,
    includeAssets: view !== "summary",
    includeShopOptions: view === "full" && selectedOutlets.length > 0,
  };
  const opening = await readProductPerformanceSnapshot(db, snapshotInput);
  if (view === "page" && opening.snapshotToken !== expectedSnapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "商品日数据版本已变化，请重新加载");
  }
  const sourceSql = input.dimension === "sku"
    ? "r.source = 'jd_sku_daily'"
    : "r.source IN ('jd_sku_daily', 'tmall_product_daily')";
  // SPU projections aggregate JSON metrics across both JD and Tmall. Without an
  // explicit plan SQLite prefers the narrower source/date index, which turns the
  // metric reads into scattered table lookups. This composite index is installed
  // by ensureNetshopSchema and keeps source, dataset, platform and shop rows
  // together; INDEXED BY only changes the access path, never the result contract.
  // SKU projections keep the planner free to use their partial identity index.
  const factTableSql = input.dimension === "spu"
    ? "netshop_rows r INDEXED BY netshop_rows_source_dataset_scope_date_idx"
    : "netshop_rows r";
  const identitySql = `r.platform || char(31) || r.shop_name || char(31) || ${dimensionSql}`;
  const whereParts = [sourceSql, "r.dataset = ?", `${dimensionSql} <> ''`];
  const bindings: string[] = [dataset];

  if (period) {
    whereParts.push("r.business_date >= ?", "r.business_date < ?");
    bindings.push(period.startDate, period.endExclusive);
  }
  if (selectedPlatforms.length > 0) {
    whereParts.push(`r.platform IN (${selectedPlatforms.map(() => "?").join(", ")})`);
    bindings.push(...selectedPlatforms);
  }
  appendNetshopOutletFilter(whereParts, bindings, selectedOutlets);
  if (query) {
    whereParts.push(`(${dimensionSql} LIKE ? OR r.sku_id LIKE ? OR r.spu_id LIKE ? OR r.product_code LIKE ? OR r.product_name LIKE ?)`);
    const searchTerm = `%${query}%`;
    bindings.push(searchTerm, searchTerm, searchTerm, searchTerm, searchTerm);
  }
  const whereSql = whereParts.join(" AND ");
  const categorySql = dailyPerformanceCategorySql();
  const metric = dailyPerformanceMetrics;
  const summaryPromise = view === "summary"
    ? db.prepare(
      `SELECT
         COUNT(DISTINCT ${identitySql}) AS product_count,
         COUNT(DISTINCT r.business_date) AS date_count,
         MIN(r.business_date) AS date_min,
         MAX(r.business_date) AS data_cutoff_date,
         SUM(${metric.pageViews}) AS page_views,
         SUM(${metric.visitors}) AS visitors,
         SUM(${metric.searchImpressions}) AS search_impressions,
         SUM(${metric.searchClicks}) AS search_clicks,
         SUM(${metric.addCartCustomers}) AS add_cart_customers,
         SUM(${metric.addCartQuantity}) AS add_cart_quantity,
         SUM(${metric.orderCustomers}) AS order_customers,
         SUM(${metric.orderQuantity}) AS order_quantity,
          SUM(${metric.orderAmountCents}) AS order_amount,
         SUM(${metric.transactionOrders}) AS transaction_orders,
          SUM(${metric.transactionAmountCents}) AS transaction_amount,
         SUM(${metric.transactionQuantity}) AS transaction_quantity,
          SUM(${metric.transactionCustomers}) AS transaction_customers
         ,SUM(${metric.favorites}) AS favorites
         ,SUM(${metric.refundAmountCents}) AS refund_amount_cents
         ,SUM(${metric.searchVisitors}) AS search_visitors
         ,SUM(${metric.searchTransactionCustomers}) AS search_transaction_customers
       FROM ${factTableSql}
       WHERE ${whereSql}`,
    ).bind(...bindings).first<NetshopProductPerformanceSummaryRow>()
    : Promise.resolve(null);

  const offset = (page - 1) * pageSize;
  const groupedItemsSql = `SELECT
         ${dimensionSql} AS id,
         MAX(r.platform) AS platform,
         MAX(r.shop_name) AS shop_name,
         MAX(r.sku_id) AS sku_id,
         MAX(r.spu_id) AS spu_id,
         MAX(NULLIF(r.product_code, '')) AS product_code,
         MAX(NULLIF(r.product_name, '')) AS product_name,
         MAX(${categorySql}) AS category,
         GROUP_CONCAT(DISTINCT NULLIF(r.shop_name, '')) AS shop_names,
         MIN(r.business_date) AS date_min,
         MAX(r.business_date) AS date_max,
         COUNT(DISTINCT r.business_date) AS data_days,
         SUM(${metric.pageViews}) AS page_views,
         SUM(${metric.visitors}) AS visitors,
         SUM(${metric.searchImpressions}) AS search_impressions,
         SUM(${metric.searchClicks}) AS search_clicks,
         SUM(${metric.addCartCustomers}) AS add_cart_customers,
         SUM(${metric.addCartQuantity}) AS add_cart_quantity,
         SUM(${metric.orderCustomers}) AS order_customers,
         SUM(${metric.orderQuantity}) AS order_quantity,
          SUM(${metric.orderAmountCents}) AS order_amount,
         SUM(${metric.transactionOrders}) AS transaction_orders,
          SUM(${metric.transactionAmountCents}) AS transaction_amount,
         SUM(${metric.transactionQuantity}) AS transaction_quantity,
          SUM(${metric.transactionCustomers}) AS transaction_customers
         ,SUM(${metric.favorites}) AS favorites
         ,SUM(${metric.refundAmountCents}) AS refund_amount_cents
         ,SUM(${metric.searchVisitors}) AS search_visitors
         ,SUM(${metric.searchTransactionCustomers}) AS search_transaction_customers
       FROM ${factTableSql}
       WHERE ${whereSql}
       GROUP BY r.platform, r.shop_name, ${dimensionSql}`;
  const rowsSql = view === "page"
    ? `WITH grouped_items AS (${groupedItemsSql})
       SELECT grouped_items.*, COUNT(*) OVER () AS total_items
       FROM grouped_items
       ORDER BY transaction_amount DESC, visitors DESC, product_name COLLATE NOCASE ASC,
                id ASC, platform ASC, shop_name COLLATE NOCASE ASC, shop_name ASC
       LIMIT ? OFFSET ?`
    : `${groupedItemsSql}
       ORDER BY transaction_amount DESC, visitors DESC, product_name COLLATE NOCASE ASC,
                id ASC, platform ASC, shop_name COLLATE NOCASE ASC, shop_name ASC
       LIMIT ? OFFSET ?`;
  const rowsPromise = view === "page"
    ? db.prepare(rowsSql).bind(...bindings, pageSize, offset).all<NetshopProductPerformanceRow>()
    : Promise.resolve({ results: [] as NetshopProductPerformanceRow[] });

  const shopWhereParts = [sourceSql, "r.dataset = ?", `${dimensionSql} <> ''`];
  const shopBindings: string[] = [dataset];
  if (selectedPlatforms.length > 0) {
    shopWhereParts.push(`r.platform IN (${selectedPlatforms.map(() => "?").join(", ")})`);
    shopBindings.push(...selectedPlatforms);
  }
  const shopsPromise = Promise.resolve({ results: [] as NetshopProductPerformanceShopRow[] });

  const availableCoveragePromise = Promise.resolve(null as NetshopProductPerformanceAvailableCoverageRow | null);
  const dailyRowsPromise = Promise.resolve({ results: [] as NetshopProductPerformanceDailyRow[] });

  const coverageWhereParts: string[] = [];
  const coverageBindings: string[] = [];
  appendNetshopOutletFilter(coverageWhereParts, coverageBindings, selectedOutlets, "available");
  const coverageWhereSql = coverageWhereParts.length > 0 ? coverageWhereParts.join(" AND ") : "1 = 1";
  const fullProjectionPromise = view === "full"
    ? db.prepare(
      `WITH filtered AS MATERIALIZED (
         SELECT
           r.platform,
           r.shop_name,
           r.business_date,
           r.sku_id,
           r.spu_id,
           r.product_code,
           r.product_name,
           ${dimensionSql} AS dimension_id,
           ${categorySql} AS category,
           ${metric.pageViews} AS page_views,
           ${metric.visitors} AS visitors,
           ${metric.searchImpressions} AS search_impressions,
           ${metric.searchClicks} AS search_clicks,
           ${metric.addCartCustomers} AS add_cart_customers,
           ${metric.addCartQuantity} AS add_cart_quantity,
           ${metric.orderCustomers} AS order_customers,
           ${metric.orderQuantity} AS order_quantity,
           ${metric.orderAmountCents} AS order_amount,
           ${metric.transactionOrders} AS transaction_orders,
           ${metric.transactionAmountCents} AS transaction_amount,
           ${metric.transactionQuantity} AS transaction_quantity,
           ${metric.transactionCustomers} AS transaction_customers,
           ${metric.favorites} AS favorites,
           ${metric.refundAmountCents} AS refund_amount_cents,
           ${metric.searchVisitors} AS search_visitors,
           ${metric.searchTransactionCustomers} AS search_transaction_customers
         FROM ${factTableSql}
         WHERE ${whereSql}
       ), available_facts AS MATERIALIZED (
         SELECT
           r.platform,
           r.shop_name,
           r.business_date,
           ${dimensionSql} AS dimension_id
         FROM ${factTableSql}
         WHERE ${shopWhereParts.join(" AND ")}
       ), summary AS MATERIALIZED (
         SELECT
           COUNT(DISTINCT platform || char(31) || shop_name || char(31) || dimension_id) AS product_count,
           COUNT(DISTINCT business_date) AS date_count,
           MIN(business_date) AS date_min,
           MAX(business_date) AS data_cutoff_date,
           SUM(page_views) AS page_views,
           SUM(visitors) AS visitors,
           SUM(search_impressions) AS search_impressions,
           SUM(search_clicks) AS search_clicks,
           SUM(add_cart_customers) AS add_cart_customers,
           SUM(add_cart_quantity) AS add_cart_quantity,
           SUM(order_customers) AS order_customers,
           SUM(order_quantity) AS order_quantity,
           SUM(order_amount) AS order_amount,
           SUM(transaction_orders) AS transaction_orders,
           SUM(transaction_amount) AS transaction_amount,
           SUM(transaction_quantity) AS transaction_quantity,
           SUM(transaction_customers) AS transaction_customers,
           SUM(favorites) AS favorites,
           SUM(refund_amount_cents) AS refund_amount_cents,
           SUM(search_visitors) AS search_visitors,
           SUM(search_transaction_customers) AS search_transaction_customers
         FROM filtered
       ), grouped_items AS MATERIALIZED (
         SELECT
           dimension_id AS id,
           MAX(platform) AS platform,
           MAX(shop_name) AS shop_name,
           MAX(sku_id) AS sku_id,
           MAX(spu_id) AS spu_id,
           MAX(NULLIF(product_code, '')) AS product_code,
           MAX(NULLIF(product_name, '')) AS product_name,
           MAX(category) AS category,
           GROUP_CONCAT(DISTINCT NULLIF(shop_name, '')) AS shop_names,
           MIN(business_date) AS date_min,
           MAX(business_date) AS date_max,
           COUNT(DISTINCT business_date) AS data_days,
           SUM(page_views) AS page_views,
           SUM(visitors) AS visitors,
           SUM(search_impressions) AS search_impressions,
           SUM(search_clicks) AS search_clicks,
           SUM(add_cart_customers) AS add_cart_customers,
           SUM(add_cart_quantity) AS add_cart_quantity,
           SUM(order_customers) AS order_customers,
           SUM(order_quantity) AS order_quantity,
           SUM(order_amount) AS order_amount,
           SUM(transaction_orders) AS transaction_orders,
           SUM(transaction_amount) AS transaction_amount,
           SUM(transaction_quantity) AS transaction_quantity,
           SUM(transaction_customers) AS transaction_customers,
           SUM(favorites) AS favorites,
           SUM(refund_amount_cents) AS refund_amount_cents,
           SUM(search_visitors) AS search_visitors,
           SUM(search_transaction_customers) AS search_transaction_customers
         FROM filtered
         GROUP BY platform, shop_name, dimension_id
       ), shop_rows AS MATERIALIZED (
         SELECT
           shop_name,
           MAX(platform) AS platform,
           COUNT(DISTINCT platform || char(31) || shop_name || char(31) || dimension_id) AS product_count
         FROM available_facts
         WHERE shop_name <> ''
         GROUP BY platform, shop_name
       ), coverage AS MATERIALIZED (
         SELECT MIN(business_date) AS date_min, MAX(business_date) AS date_max
         FROM available_facts available
         WHERE ${coverageWhereSql}
       ), daily_series AS MATERIALIZED (
         SELECT
           business_date,
           SUM(page_views) AS page_views,
           SUM(visitors) AS visitors,
           SUM(transaction_customers) AS transaction_customers,
           SUM(transaction_quantity) AS transaction_quantity,
           SUM(transaction_amount) AS transaction_amount_cents,
           SUM(refund_amount_cents) AS refund_amount_cents,
           SUM(favorites) AS favorites,
           SUM(add_cart_customers) AS add_cart_customers,
           SUM(add_cart_quantity) AS add_cart_quantity
         FROM filtered
         GROUP BY business_date
       )
       SELECT summary.*,
         COALESCE((
           SELECT json_group_array(json_object(
             'id', page_items.id,
             'platform', page_items.platform,
             'shop_name', page_items.shop_name,
             'sku_id', page_items.sku_id,
             'spu_id', page_items.spu_id,
             'product_code', page_items.product_code,
             'product_name', page_items.product_name,
             'category', page_items.category,
             'shop_names', page_items.shop_names,
             'date_min', page_items.date_min,
             'date_max', page_items.date_max,
             'data_days', page_items.data_days,
             'page_views', page_items.page_views,
             'visitors', page_items.visitors,
             'search_impressions', page_items.search_impressions,
             'search_clicks', page_items.search_clicks,
             'add_cart_customers', page_items.add_cart_customers,
             'add_cart_quantity', page_items.add_cart_quantity,
             'order_customers', page_items.order_customers,
             'order_quantity', page_items.order_quantity,
             'order_amount', page_items.order_amount,
             'transaction_orders', page_items.transaction_orders,
             'transaction_amount', page_items.transaction_amount,
             'transaction_quantity', page_items.transaction_quantity,
             'transaction_customers', page_items.transaction_customers,
             'favorites', page_items.favorites,
             'refund_amount_cents', page_items.refund_amount_cents,
             'search_visitors', page_items.search_visitors,
             'search_transaction_customers', page_items.search_transaction_customers
           ))
           FROM (
             SELECT *
             FROM grouped_items
             ORDER BY transaction_amount DESC, visitors DESC, product_name COLLATE NOCASE ASC,
                      id ASC, platform ASC, shop_name COLLATE NOCASE ASC, shop_name ASC
             LIMIT ? OFFSET ?
           ) page_items
         ), '[]') AS items_json,
         COALESCE((
           SELECT json_group_array(json_object(
             'shop_name', shops.shop_name,
             'platform', shops.platform,
             'product_count', shops.product_count
           ))
           FROM (
             SELECT * FROM shop_rows
             ORDER BY shop_name COLLATE NOCASE ASC, shop_name ASC, platform ASC
           ) shops
         ), '[]') AS shops_json,
         coverage.date_min AS available_date_min,
         coverage.date_max AS available_date_max,
         COALESCE((
           SELECT json_group_array(json_object(
             'business_date', daily.business_date,
             'page_views', daily.page_views,
             'visitors', daily.visitors,
             'transaction_customers', daily.transaction_customers,
             'transaction_quantity', daily.transaction_quantity,
             'transaction_amount_cents', daily.transaction_amount_cents,
             'refund_amount_cents', daily.refund_amount_cents,
             'favorites', daily.favorites,
             'add_cart_customers', daily.add_cart_customers,
             'add_cart_quantity', daily.add_cart_quantity
           ))
           FROM (
             SELECT * FROM daily_series ORDER BY business_date DESC LIMIT ?
           ) daily
         ), '[]') AS daily_json
       FROM summary CROSS JOIN coverage`,
    ).bind(
      ...bindings,
      ...shopBindings,
      ...coverageBindings,
      pageSize,
      offset,
      NETSHOP_DAILY_SERIES_LIMIT,
    ).first<NetshopProductPerformanceFullProjectionRow>()
    : Promise.resolve(null);

  const [standaloneSummary, standaloneRows, standaloneShops, standaloneAvailableCoverage, standaloneDailyRows, fullProjection] = await Promise.all([
    summaryPromise,
    rowsPromise,
    shopsPromise,
    availableCoveragePromise,
    dailyRowsPromise,
    fullProjectionPromise,
  ]);
  if (view === "full" && !fullProjection) {
    throw new PublicApiError(503, "service_unavailable", "商品日首屏投影为空，请稍后重试");
  }
  const summary = fullProjection ?? standaloneSummary;
  const rows = fullProjection
    ? {
      results: parseProductPerformanceProjectionRows<NetshopProductPerformanceRow>(
        fullProjection.items_json,
        "商品明细",
        pageSize,
        isProductPerformanceItemProjection,
      ),
    }
    : standaloneRows;
  const shops = fullProjection
    ? {
      results: parseProductPerformanceProjectionRows<NetshopProductPerformanceShopRow>(
        fullProjection.shops_json,
        "店铺选项",
        null,
        isProductPerformanceShopProjection,
      ),
    }
    : standaloneShops;
  const availableCoverage = fullProjection
    ? { date_min: fullProjection.available_date_min, date_max: fullProjection.available_date_max }
    : standaloneAvailableCoverage;
  const dailyRows = fullProjection
    ? {
      results: parseProductPerformanceProjectionRows<NetshopProductPerformanceDailyRow>(
        fullProjection.daily_json,
        "商品日趋势",
        NETSHOP_DAILY_SERIES_LIMIT,
        isProductPerformanceDailyProjection,
      ),
    }
    : standaloneDailyRows;
  const productAssets = view !== "summary" && input.dimension === "spu"
    ? await readTmallProductAssets(db, rows.results)
    : new Map<string, { imageUrl: string; productUrl: string }>();

  const visitors = numberFromDailyMetric(summary?.visitors);
  const transactionCustomers = numberFromDailyMetric(summary?.transaction_customers);
  const searchImpressions = numberFromDailyMetric(summary?.search_impressions);
  const searchClicks = numberFromDailyMetric(summary?.search_clicks);
  const transactionAmountCents = numberFromDailyMetric(summary?.transaction_amount);
  const summaryPayload: NetshopProductPerformanceSummary = {
    snapshotToken: opening.snapshotToken,
    dimension: input.dimension,
    dataset,
    requestedPeriod: { startDate, endDate },
    dateMin: summary?.date_min ?? null,
    dataCutoffDate: summary?.data_cutoff_date ?? null,
    monetaryUnit: "cents",
    visitorAggregation: "product_day_sum",
    summary: {
      productCount: numberFromDailyMetric(summary?.product_count),
      pageViews: numberFromDailyMetric(summary?.page_views),
      visitors,
      searchImpressions,
      searchClicks,
      searchClickRate: searchImpressions > 0 ? searchClicks / searchImpressions : null,
      addCartCustomers: numberFromDailyMetric(summary?.add_cart_customers),
      addCartQuantity: numberFromDailyMetric(summary?.add_cart_quantity),
      orderCustomers: numberFromDailyMetric(summary?.order_customers),
      orderQuantity: numberFromDailyMetric(summary?.order_quantity),
      orderAmount: numberFromDailyMetric(summary?.order_amount) / 100,
      orderAmountCents: numberFromDailyMetric(summary?.order_amount),
      transactionOrders: numberFromDailyMetric(summary?.transaction_orders),
      transactionAmount: transactionAmountCents / 100,
      transactionAmountCents,
      transactionQuantity: numberFromDailyMetric(summary?.transaction_quantity),
      transactionCustomers,
      favorites: numberFromDailyMetric(summary?.favorites),
      refundAmountCents: numberFromDailyMetric(summary?.refund_amount_cents),
      searchVisitors: numberFromDailyMetric(summary?.search_visitors),
      searchTransactionCustomers: numberFromDailyMetric(summary?.search_transaction_customers),
      uvValue: visitors > 0 ? transactionAmountCents / 100 / visitors : null,
      conversionRate: visitors > 0 ? transactionCustomers / visitors : null,
    },
  };
  if (view === "summary") {
    await verifyProductPerformanceSnapshot(db, snapshotInput, opening.snapshotToken, opening.shopOptionsSnapshotToken);
    return summaryPayload;
  }

  const items: NetshopProductPerformanceItem[] = rows.results.map((row) => {
    const itemVisitors = numberFromDailyMetric(row.visitors);
    const itemTransactionCustomers = numberFromDailyMetric(row.transaction_customers);
    const itemSearchImpressions = numberFromDailyMetric(row.search_impressions);
    const itemSearchClicks = numberFromDailyMetric(row.search_clicks);
    const itemTransactionAmountCents = numberFromDailyMetric(row.transaction_amount);
    const productAsset = productAssets.get(JSON.stringify([row.platform, row.shop_name, row.spu_id]));
    return {
      id: row.id,
      platform: row.platform,
      skuId: row.sku_id,
      spuId: row.spu_id,
      productCode: row.product_code,
      productName: row.product_name,
      imageUrl: productAsset?.imageUrl ?? "",
      productUrl: productAsset?.productUrl ?? "",
      category: row.category || "",
      shopNames: (row.shop_names ?? "").split(",").map((value) => value.trim()).filter(Boolean),
      dateMin: row.date_min,
      dateMax: row.date_max,
      dataDays: numberFromDailyMetric(row.data_days),
      pageViews: numberFromDailyMetric(row.page_views),
      visitors: itemVisitors,
      searchImpressions: itemSearchImpressions,
      searchClicks: itemSearchClicks,
      searchClickRate: itemSearchImpressions > 0 ? itemSearchClicks / itemSearchImpressions : null,
      addCartCustomers: numberFromDailyMetric(row.add_cart_customers),
      addCartQuantity: numberFromDailyMetric(row.add_cart_quantity),
      orderCustomers: numberFromDailyMetric(row.order_customers),
      orderQuantity: numberFromDailyMetric(row.order_quantity),
      orderAmount: numberFromDailyMetric(row.order_amount) / 100,
      orderAmountCents: numberFromDailyMetric(row.order_amount),
      transactionOrders: numberFromDailyMetric(row.transaction_orders),
      transactionAmount: itemTransactionAmountCents / 100,
      transactionAmountCents: itemTransactionAmountCents,
      transactionQuantity: numberFromDailyMetric(row.transaction_quantity),
      transactionCustomers: itemTransactionCustomers,
      favorites: numberFromDailyMetric(row.favorites),
      refundAmountCents: numberFromDailyMetric(row.refund_amount_cents),
      searchVisitors: numberFromDailyMetric(row.search_visitors),
      searchTransactionCustomers: numberFromDailyMetric(row.search_transaction_customers),
      uvValue: itemVisitors > 0 ? itemTransactionAmountCents / 100 / itemVisitors : null,
      conversionRate: itemVisitors > 0 ? itemTransactionCustomers / itemVisitors : null,
    };
  });
  let pageTotal = view === "page"
    ? numberFromDailyMetric(rows.results[0]?.total_items)
    : numberFromDailyMetric(summary?.product_count);
  if (view === "page" && rows.results.length === 0 && page > 1) {
    const fallbackTotal = await db.prepare(
      `SELECT COUNT(*) AS total FROM (
         SELECT 1 FROM ${factTableSql}
         WHERE ${whereSql}
         GROUP BY r.platform, r.shop_name, ${dimensionSql}
       ) grouped_items`,
    ).bind(...bindings).first<{ total: number | null }>();
    pageTotal = numberFromDailyMetric(fallbackTotal?.total);
  }
  const pagePayload: NetshopProductPerformancePage = {
    snapshotToken: opening.snapshotToken,
    items,
    pagination: {
      page,
      pageSize,
      total: pageTotal,
      returned: items.length,
      truncated: offset + items.length < pageTotal,
    },
  };
  if (view === "page") {
    await verifyProductPerformanceSnapshot(db, snapshotInput, opening.snapshotToken, opening.shopOptionsSnapshotToken);
    return pagePayload;
  }

  const dailySeriesRows = [...dailyRows.results].sort((left, right) => left.business_date.localeCompare(right.business_date));
  const actualDates = dailySeriesRows.map((row) => row.business_date).filter(Boolean);
  const dailyTotal = numberFromDailyMetric(summary?.date_count);
  const requestedCoverage = period
    ? dailyDateCoverageForQuery(period.startDate, period.endDate, actualDates)
    : { actualDates, missingDates: [] as string[] };
  const coverage = {
    ...requestedCoverage,
    availableDateMin: availableCoverage?.date_min ?? null,
    availableDateMax: availableCoverage?.date_max ?? null,
    total: dailyTotal,
    returned: actualDates.length,
    truncated: actualDates.length < dailyTotal,
  };
  const platforms = [...new Set(shops.results.map((shop) => shop.platform.trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
  const payload: NetshopProductPerformance = {
    ...summaryPayload,
    ...pagePayload,
    coverage,
    platforms,
    shops: shops.results.map((shop) => ({
      shopName: shop.shop_name,
      platform: shop.platform || "京东",
      productCount: numberFromDailyMetric(shop.product_count),
    })),
    daily: dailySeriesRows.map((row) => ({
      date: row.business_date,
      pageViews: numberFromDailyMetric(row.page_views),
      visitors: numberFromDailyMetric(row.visitors),
      transactionCustomers: numberFromDailyMetric(row.transaction_customers),
      transactionQuantity: numberFromDailyMetric(row.transaction_quantity),
      transactionAmountCents: numberFromDailyMetric(row.transaction_amount_cents),
      refundAmountCents: numberFromDailyMetric(row.refund_amount_cents),
      favorites: numberFromDailyMetric(row.favorites),
      addCartCustomers: numberFromDailyMetric(row.add_cart_customers),
      addCartQuantity: numberFromDailyMetric(row.add_cart_quantity),
    })),
    dailyPagination: {
      total: dailyTotal,
      returned: dailySeriesRows.length,
      truncated: dailySeriesRows.length < dailyTotal,
    },
  };
  await verifyProductPerformanceSnapshot(db, snapshotInput, opening.snapshotToken, opening.shopOptionsSnapshotToken);
  return payload;
}

export async function getNetshopProductPerformance(
  db: NetshopDatabase,
  input: NetshopProductPerformanceQueryInput,
): Promise<NetshopProductPerformance> {
  return readNetshopProductPerformanceProjection(db, input, "full") as Promise<NetshopProductPerformance>;
}

export async function getNetshopProductPerformanceSummary(
  db: NetshopDatabase,
  input: NetshopProductPerformanceQueryInput,
): Promise<NetshopProductPerformanceSummary> {
  return readNetshopProductPerformanceProjection(db, input, "summary") as Promise<NetshopProductPerformanceSummary>;
}

export async function getNetshopProductPerformancePage(
  db: NetshopDatabase,
  input: NetshopProductPerformanceQueryInput & { snapshotToken: string },
): Promise<NetshopProductPerformancePage> {
  return readNetshopProductPerformanceProjection(db, input, "page", input.snapshotToken) as Promise<NetshopProductPerformancePage>;
}

type PromotionAggregateRow = {
  product_count: number | null;
  date_count: number | null;
  date_min: string | null;
  date_max: string | null;
  spend_cents: number | null;
  net_transaction_amount_cents: number | null;
  gross_transaction_amount_cents: number | null;
  impressions: number | null;
  clicks: number | null;
  net_orders: number | null;
  favorites: number | null;
  cart_quantity: number | null;
};

type PromotionDailyRow = Omit<PromotionAggregateRow, "product_count" | "date_count" | "date_min" | "date_max"> & { business_date: string };
type PromotionPaymentDailyRow = { business_date: string; payment_cents: number | null; total_dates: number | null };
type PromotionOutletOptionRow = { platform: string; shop_name: string; total: number | null };

type PromotionItemRow = PromotionAggregateRow & {
  id: string;
  platform: string;
  product_name: string | null;
  shop_name: string;
  data_days: number | null;
  coverage_dates: string | null;
};

export async function getNetshopPromotionPerformance(
  db: NetshopDatabase,
  input: {
    query?: string;
    page?: number;
    pageSize?: number;
    platformNames?: string[];
    outlets?: NetshopOutletFilter[];
    startDate?: string;
    endDate?: string;
  } = {},
): Promise<NetshopPromotionPerformance> {
  const page = boundedNetshopInteger(input.page, "page", 1, 1, NETSHOP_QUERY_MAX_PAGE);
  const pageSize = boundedNetshopInteger(input.pageSize, "pageSize", 20, 1, NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE);
  const period = resolveNetshopQueryPeriod(input.startDate, input.endDate);
  const startDate = period?.startDate ?? null;
  const endDate = period?.endDate ?? null;
  const platforms = [...new Set((input.platformNames ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, 20);
  const outlets = normalizeNetshopOutletFilters(input.outlets);
  assertNetshopOutletPlatformSelection(platforms, outlets);
  const promotionProductIdSql = netshopPromotionProductIdSql;
  const where = [netshopPromotionSourceSql];
  const bindings: string[] = [];
  if (period) { where.push("r.business_date >= ?", "r.business_date < ?"); bindings.push(period.startDate, period.endExclusive); }
  if (platforms.length) { where.push(`r.platform IN (${platforms.map(() => "?").join(", ")})`); bindings.push(...platforms); }
  appendNetshopOutletFilter(where, bindings, outlets);
  const whereSql = where.join(" AND ");
  const promotionIdentitySql = `r.platform || char(31) || r.shop_name || char(31) || ${promotionProductIdSql}`;
  const metric = netshopPromotionMetrics;
  const aggregateSelect = `
    COUNT(DISTINCT ${promotionIdentitySql}) AS product_count,
    COUNT(DISTINCT r.business_date) AS date_count,
    MIN(r.business_date) AS date_min,
    MAX(r.business_date) AS date_max,
    SUM(${metric.spendCents}) AS spend_cents,
    SUM(${metric.netTransactionAmountCents}) AS net_transaction_amount_cents,
    SUM(${metric.grossTransactionAmountCents}) AS gross_transaction_amount_cents,
    SUM(${metric.impressions}) AS impressions,
    SUM(${metric.clicks}) AS clicks,
    SUM(${metric.netOrders}) AS net_orders,
    SUM(${metric.favorites}) AS favorites,
    SUM(${metric.cartQuantity}) AS cart_quantity`;
  const summaryPromise = db.prepare(`SELECT ${aggregateSelect} FROM netshop_rows r WHERE ${whereSql}`)
    .bind(...bindings).first<PromotionAggregateRow>();
  const outletOptionWhere = [netshopPromotionSourceSql];
  const outletOptionBindings: string[] = [];
  if (platforms.length) {
    outletOptionWhere.push(`r.platform IN (${platforms.map(() => "?").join(", ")})`);
    outletOptionBindings.push(...platforms);
  }
  const outletOptionsPromise = db.prepare(
    `SELECT platform, shop_name, COUNT(*) OVER () AS total
     FROM (
       SELECT r.platform AS platform, r.shop_name AS shop_name
       FROM netshop_rows r
       WHERE ${outletOptionWhere.join(" AND ")}
       GROUP BY r.platform, r.shop_name
     ) promotion_outlets
     ORDER BY platform ASC, shop_name ASC
     LIMIT ?`,
  ).bind(...outletOptionBindings, NETSHOP_OUTLET_MAX_ITEMS).all<PromotionOutletOptionRow>();
  const dailyPromise = db.prepare(
    `WITH daily_series AS (
     SELECT r.business_date,
       SUM(${metric.spendCents}) AS spend_cents,
       SUM(${metric.netTransactionAmountCents}) AS net_transaction_amount_cents,
       SUM(${metric.grossTransactionAmountCents}) AS gross_transaction_amount_cents,
       SUM(${metric.impressions}) AS impressions,
       SUM(${metric.clicks}) AS clicks,
       SUM(${metric.netOrders}) AS net_orders,
       SUM(${metric.favorites}) AS favorites,
       SUM(${metric.cartQuantity}) AS cart_quantity
     FROM netshop_rows r WHERE ${whereSql}
     GROUP BY r.business_date
    )
    SELECT * FROM daily_series
    ORDER BY business_date DESC
    LIMIT ?`,
  ).bind(...bindings, NETSHOP_DAILY_SERIES_LIMIT).all<PromotionDailyRow>();

  const paymentWhere = [netshopPromotionPaymentSourceSql];
  const paymentBindings: string[] = [];
  if (period) { paymentWhere.push("r.business_date >= ?", "r.business_date < ?"); paymentBindings.push(period.startDate, period.endExclusive); }
  if (platforms.length) { paymentWhere.push(`r.platform IN (${platforms.map(() => "?").join(", ")})`); paymentBindings.push(...platforms); }
  appendNetshopOutletFilter(paymentWhere, paymentBindings, outlets);
  const paymentRowsPromise = db.prepare(
    `WITH daily_series AS (
       SELECT r.business_date, SUM(${dailyPerformanceMetrics.transactionAmountCents}) AS payment_cents
       FROM netshop_rows r WHERE ${paymentWhere.join(" AND ")}
       GROUP BY r.business_date
     )
     SELECT *, COUNT(*) OVER () AS total_dates
     FROM daily_series
     ORDER BY business_date DESC
     LIMIT ?`,
  ).bind(...paymentBindings, NETSHOP_DAILY_SERIES_LIMIT).all<PromotionPaymentDailyRow>();
  const query = (input.query ?? "").trim().slice(0, 120);
  const itemWhere = [...where];
  const itemBindings = [...bindings];
  if (query) {
    itemWhere.push(`(${promotionProductIdSql} LIKE ? OR r.product_name LIKE ? OR json_extract(r.raw_json, '$."产品线"') LIKE ?)`);
    const term = `%${query}%`;
    itemBindings.push(term, term, term);
  }
  const itemWhereSql = itemWhere.join(" AND ");
  const totalPromise = query
    ? db.prepare(`SELECT COUNT(DISTINCT ${promotionIdentitySql}) AS total FROM netshop_rows r WHERE ${itemWhereSql}`)
      .bind(...itemBindings).first<{ total: number | null }>()
    : Promise.resolve(null);
  const offset = (page - 1) * pageSize;
  const itemRowsPromise = db.prepare(
    `SELECT
       ${promotionProductIdSql} AS id,
       MAX(r.platform) AS platform,
       MAX(COALESCE(NULLIF(r.product_name, ''), NULLIF(CAST(json_extract(r.raw_json, '$."产品线"') AS TEXT), ''))) AS product_name,
       MAX(r.shop_name) AS shop_name,
       COUNT(DISTINCT r.business_date) AS data_days,
       ${period ? "GROUP_CONCAT(DISTINCT r.business_date)" : "NULL"} AS coverage_dates,
       ${aggregateSelect}
     FROM netshop_rows r WHERE ${itemWhereSql}
     GROUP BY r.platform, r.shop_name, ${promotionProductIdSql}
     ORDER BY net_transaction_amount_cents DESC, spend_cents DESC, id ASC
     LIMIT ? OFFSET ?`,
  ).bind(...itemBindings, pageSize, offset).all<PromotionItemRow>();

  const [summary, outletOptions, daily, paymentRows, totalRow, itemRows] = await Promise.all([
    summaryPromise,
    outletOptionsPromise,
    dailyPromise,
    paymentRowsPromise,
    totalPromise,
    itemRowsPromise,
  ]);
  const orderedDailyRows = [...daily.results].sort((left, right) => left.business_date.localeCompare(right.business_date));
  const orderedPaymentRows = [...paymentRows.results].sort((left, right) => left.business_date.localeCompare(right.business_date));
  const paymentByDate = new Map(orderedPaymentRows.map((row) => [row.business_date, numberFromDailyMetric(row.payment_cents)]));
  const dailyByDate = new Map(orderedDailyRows.map((row) => [row.business_date, row]));
  const promotionDates = [...dailyByDate.keys()].sort();
  const productDailyDates = [...paymentByDate.keys()].sort();
  const intersectionDates = promotionDates.filter((date) => paymentByDate.has(date));
  const requestedDates = period
    ? dailyDateCoverageForQuery(period.startDate, period.endDate, []).missingDates
    : [...new Set([...promotionDates, ...productDailyDates])].sort();
  const ratioSpendCents = intersectionDates.reduce((sum, date) => sum + numberFromDailyMetric(dailyByDate.get(date)?.spend_cents), 0);
  const ratioTransactionCents = intersectionDates.reduce((sum, date) => sum + numberFromDailyMetric(dailyByDate.get(date)?.net_transaction_amount_cents), 0);
  const platformPaymentAmountCents = intersectionDates.reduce((sum, date) => sum + numberFromDailyMetric(paymentByDate.get(date)), 0);

  const summarySpend = numberFromDailyMetric(summary?.spend_cents);
  const summaryNetTransaction = numberFromDailyMetric(summary?.net_transaction_amount_cents);
  const summaryImpressions = numberFromDailyMetric(summary?.impressions);
  const summaryClicks = numberFromDailyMetric(summary?.clicks);
  const promotionDateTotal = numberFromDailyMetric(summary?.date_count);
  const productDailyDateTotal = numberFromDailyMetric(paymentRows.results[0]?.total_dates);
  const total = query
    ? numberFromDailyMetric(totalRow?.total)
    : numberFromDailyMetric(summary?.product_count);
  const outletOptionTotal = numberFromDailyMetric(outletOptions.results[0]?.total);
  return {
    monetaryUnit: "cents",
    requestedPeriod: { startDate, endDate },
    dateMin: summary?.date_min ?? null,
    dataCutoffDate: summary?.date_max ?? null,
    coverage: {
      promotionDates,
      productDailyDates,
      intersectionDates,
      missingProductDailyDates: requestedDates.filter((date) => !paymentByDate.has(date)),
      missingPromotionDates: requestedDates.filter((date) => !dailyByDate.has(date)),
      promotionDatesPagination: {
        total: promotionDateTotal,
        returned: promotionDates.length,
        truncated: promotionDates.length < promotionDateTotal,
      },
      productDailyDatesPagination: {
        total: productDailyDateTotal,
        returned: productDailyDates.length,
        truncated: productDailyDates.length < productDailyDateTotal,
      },
      intersectionTruncated: promotionDates.length < promotionDateTotal || productDailyDates.length < productDailyDateTotal,
    },
    summary: {
      productCount: numberFromDailyMetric(summary?.product_count),
      spendCents: summarySpend,
      netTransactionAmountCents: summaryNetTransaction,
      grossTransactionAmountCents: numberFromDailyMetric(summary?.gross_transaction_amount_cents),
      platformPaymentAmountCents,
      impressions: summaryImpressions,
      clicks: summaryClicks,
      netOrders: numberFromDailyMetric(summary?.net_orders),
      favorites: numberFromDailyMetric(summary?.favorites),
      cartQuantity: numberFromDailyMetric(summary?.cart_quantity),
      clickThroughRate: summaryImpressions > 0 ? summaryClicks / summaryImpressions : null,
      averageClickCostCents: summaryClicks > 0 ? summarySpend / summaryClicks : null,
      roas: summarySpend > 0 ? summaryNetTransaction / summarySpend : null,
      spendRate: platformPaymentAmountCents > 0 ? ratioSpendCents / platformPaymentAmountCents : null,
      promotionTransactionShare: platformPaymentAmountCents > 0 ? ratioTransactionCents / platformPaymentAmountCents : null,
    },
    daily: orderedDailyRows.map((row) => {
      const spendCents = numberFromDailyMetric(row.spend_cents);
      const netTransactionAmountCents = numberFromDailyMetric(row.net_transaction_amount_cents);
      const payment = paymentByDate.has(row.business_date) ? numberFromDailyMetric(paymentByDate.get(row.business_date)) : null;
      return {
        date: row.business_date,
        spendCents,
        netTransactionAmountCents,
        platformPaymentAmountCents: payment,
        impressions: numberFromDailyMetric(row.impressions),
        clicks: numberFromDailyMetric(row.clicks),
        netOrders: numberFromDailyMetric(row.net_orders),
        roas: spendCents > 0 ? netTransactionAmountCents / spendCents : null,
        spendRate: payment && payment > 0 ? spendCents / payment : null,
        promotionTransactionShare: payment && payment > 0 ? netTransactionAmountCents / payment : null,
      };
    }),
    filterOptions: {
      shops: outletOptions.results.map((row) => ({ platform: row.platform, shopName: row.shop_name })),
      pagination: {
        total: outletOptionTotal,
        returned: outletOptions.results.length,
        truncated: outletOptions.results.length < outletOptionTotal,
      },
    },
    dailyPagination: {
      total: promotionDateTotal,
      returned: orderedDailyRows.length,
      truncated: orderedDailyRows.length < promotionDateTotal,
    },
    items: itemRows.results.map((row) => {
      const spendCents = numberFromDailyMetric(row.spend_cents);
      const netTransactionAmountCents = numberFromDailyMetric(row.net_transaction_amount_cents);
      const impressions = numberFromDailyMetric(row.impressions);
      const clicks = numberFromDailyMetric(row.clicks);
      return {
        id: row.id,
        platform: row.platform,
        productName: row.product_name ?? "",
        shopName: row.shop_name,
        dateMin: row.date_min,
        dateMax: row.date_max,
        dates: [...new Set((row.coverage_dates ?? "").split(",").filter(isIsoDate))].sort(),
        datesTruncated: !period && numberFromDailyMetric(row.data_days) > 0,
        dataDays: numberFromDailyMetric(row.data_days),
        spendCents,
        netTransactionAmountCents,
        grossTransactionAmountCents: numberFromDailyMetric(row.gross_transaction_amount_cents),
        impressions,
        clicks,
        netOrders: numberFromDailyMetric(row.net_orders),
        favorites: numberFromDailyMetric(row.favorites),
        cartQuantity: numberFromDailyMetric(row.cart_quantity),
        clickThroughRate: impressions > 0 ? clicks / impressions : null,
        averageClickCostCents: clicks > 0 ? spendCents / clicks : null,
        roas: spendCents > 0 ? netTransactionAmountCents / spendCents : null,
      };
    }),
    pagination: { page, pageSize, total, returned: itemRows.results.length, truncated: offset + itemRows.results.length < total },
  };
}

type PromotionScopeInput = {
  platformNames?: string[];
  outlets?: NetshopOutletFilter[];
  startDate?: string;
  endDate?: string;
  expectedSnapshotToken?: string;
};

type PromotionOverviewDailyRow = PromotionDailyRow & {
  total_dates: number | null;
  all_date_min: string | null;
  all_date_max: string | null;
  all_spend_cents: number | null;
  all_net_transaction_amount_cents: number | null;
  all_gross_transaction_amount_cents: number | null;
  all_impressions: number | null;
  all_clicks: number | null;
  all_net_orders: number | null;
  all_favorites: number | null;
  all_cart_quantity: number | null;
};

type PromotionItemsQueryRow = PromotionItemRow & {
  total_items: number | null;
  data_cutoff_date: string | null;
};

type PromotionProductDailyRevisionRow = {
  platform: string;
  shop_name?: string;
  data_version: number | null;
};

async function readNetshopProductDailyRevisions(
  db: NetshopDatabase,
  scopes: ReadonlyArray<{ platform: string; shopNames?: readonly string[] }>,
) {
  const normalized = scopes.map((scope) => ({
    platform: scope.platform,
    shopNames: [...new Set((scope.shopNames ?? []).map((shop) => shop.trim()).filter(Boolean))].sort(),
  }));
  const platformHeads = normalized.filter((scope) => scope.shopNames.length === 0).map((scope) => scope.platform);
  const requestedShops = normalized.flatMap((scope) => scope.shopNames.map((shopName) => ({
    platform: scope.platform,
    shopName,
  })));
  const [headRows, shopRows] = await Promise.all([
    platformHeads.length > 0
      ? db.prepare(`SELECT platform, data_version
          FROM netshop_product_daily_revisions
          WHERE platform IN (${platformHeads.map(() => "?").join(", ")})
          ORDER BY platform ASC`)
        .bind(...platformHeads).all<PromotionProductDailyRevisionRow>()
      : Promise.resolve({ results: [] as PromotionProductDailyRevisionRow[] }),
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
        LEFT JOIN netshop_product_daily_scope_revisions revision
          ON revision.platform = requested_shops.platform
         AND revision.shop_name = requested_shops.shop_name
        ORDER BY requested_shops.platform ASC, requested_shops.shop_name ASC`)
        .bind(JSON.stringify(requestedShops)).all<PromotionProductDailyRevisionRow>()
      : Promise.resolve({ results: [] as PromotionProductDailyRevisionRow[] }),
  ]);
  const headByPlatform = new Map((headRows.results ?? []).map((row) => [row.platform, row]));
  const shopByKey = new Map((shopRows.results ?? []).map((row) => [`${row.platform}\u001f${row.shop_name ?? ""}`, row]));
  const revisionValue = (row: PromotionProductDailyRevisionRow | undefined) => {
    const dataVersion = Number(row?.data_version ?? 0);
    if (!Number.isSafeInteger(dataVersion) || dataVersion < 0) {
      throw new PublicApiError(503, "service_unavailable", "商品日依赖版本无效，请稍后重试");
    }
    return dataVersion;
  };
  return normalized.sort((left, right) => left.platform.localeCompare(right.platform)).map((scope) => ({
    platform: scope.platform,
    dataVersion: scope.shopNames.length === 0 ? revisionValue(headByPlatform.get(scope.platform)) : null,
    shopRevisions: scope.shopNames.map((shopName) => ({
      shopName,
      dataVersion: revisionValue(shopByKey.get(`${scope.platform}\u001f${shopName}`)),
    })),
  }));
}

async function buildPromotionSnapshotToken(input: {
  period: NetshopQueryPeriod;
  snapshots: ReadonlyArray<{
    platform: string;
    shopNames?: readonly string[];
    dataVersion: number | null;
    shopRevisions: readonly { shopName: string; dataVersion: number }[];
  }>;
  productDailyRevisions: Awaited<ReturnType<typeof readNetshopProductDailyRevisions>>;
}) {
  const payload = JSON.stringify({
    version: 2,
    startDate: input.period.startDate,
    endDate: input.period.endDate,
    promotion: [...input.snapshots]
      .sort((left, right) => left.platform < right.platform ? -1 : left.platform > right.platform ? 1 : 0)
      .map((snapshot) => ({
        platform: snapshot.platform,
        shopNames: [...(snapshot.shopNames ?? [])].sort(),
        dataVersion: snapshot.dataVersion,
        shopRevisions: [...snapshot.shopRevisions]
          .sort((left, right) => left.shopName.localeCompare(right.shopName)),
      })),
    productDaily: [...input.productDailyRevisions]
      .sort((left, right) => left.platform.localeCompare(right.platform))
      .map((revision) => ({
        platform: revision.platform,
        dataVersion: revision.dataVersion,
        shopRevisions: [...revision.shopRevisions]
          .sort((left, right) => left.shopName.localeCompare(right.shopName)),
      })),
  });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function resolvePromotionScope(input: PromotionScopeInput) {
  const period = resolveNetshopQueryPeriod(input.startDate, input.endDate);
  const platforms = [...new Set((input.platformNames ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, 20);
  const outlets = normalizeNetshopOutletFilters(input.outlets);
  assertNetshopOutletPlatformSelection(platforms, outlets);
  const where = [netshopPromotionSourceSql];
  const bindings: string[] = [];
  if (period) {
    where.push("r.business_date >= ?", "r.business_date < ?");
    bindings.push(period.startDate, period.endExclusive);
  }
  if (platforms.length) {
    where.push(`r.platform IN (${platforms.map(() => "?").join(", ")})`);
    bindings.push(...platforms);
  }
  appendNetshopOutletFilter(where, bindings, outlets);
  return { period, platforms, outlets, where, bindings };
}

async function requirePromotionAggregateScope(
  db: NetshopDatabase,
  period: NetshopQueryPeriod | null,
  platforms: readonly string[],
  outlets: readonly NetshopOutletFilter[],
  expectedSnapshotToken?: string,
) {
  if (!period) throw new NetshopQueryError("invalid_date_range", "推广聚合查询必须显式提供 startDate 和 endDate");
  if (!platforms.length || platforms.some((platform) => platform !== "京东" && platform !== "天猫")) {
    throw new NetshopQueryError("invalid_platform_filter", "推广聚合查询必须显式选择京东或天猫平台");
  }
  const activePlatforms = outlets.length
    ? platforms.filter((platform) => outlets.some((outlet) => outlet.platform === platform))
    : [...platforms];
  const scopes = activePlatforms.map((platform) => {
    const shopNames = outlets.length
      ? outlets.filter((outlet) => outlet.platform === platform).map((outlet) => outlet.shopName)
      : undefined;
    return {
      platform,
      shopNames,
      startDate: period.startDate,
      endDate: period.endDate,
    };
  });
  const [versionRows, productDailyRevisions] = await Promise.all([
    readPromotionAggregateVersions(db, scopes),
    readNetshopProductDailyRevisions(db, scopes),
  ]);
  const versionsByPlatform = new Map(versionRows.map((row) => [row.platform, row]));
  if (versionsByPlatform.size !== scopes.length) {
    throw new PublicApiError(503, "service_unavailable", "所选推广聚合尚未完成回填或已失效，请完成聚合回填后重试");
  }
  const snapshots = scopes.map((scope) => {
    const version = versionsByPlatform.get(scope.platform)!;
    const isShopScoped = Boolean(scope.shopNames?.length);
    return {
      ...scope,
      dataVersion: isShopScoped ? null : version.dataVersion,
      shopRevisions: isShopScoped ? version.shopRevisions : [],
    };
  });
  const snapshotToken = await buildPromotionSnapshotToken({ period, snapshots, productDailyRevisions });
  if (expectedSnapshotToken && expectedSnapshotToken !== snapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "推广商品与概览数据版本已变化，请重新加载");
  }
  return {
    period,
    snapshots,
    productDailyRevisions,
    snapshotToken,
  };
}

async function verifyPromotionAggregateScope(
  db: NetshopDatabase,
  fence: Awaited<ReturnType<typeof requirePromotionAggregateScope>>,
) {
  const [currentVersions, productDailyRevisions] = await Promise.all([
    readPromotionAggregateVersions(db, fence.snapshots.map((snapshot) => ({
      platform: snapshot.platform,
      shopNames: snapshot.shopNames,
      startDate: fence.period.startDate,
      endDate: fence.period.endDate,
    }))),
    readNetshopProductDailyRevisions(
      db,
      fence.snapshots,
    ),
  ]);
  const currentByPlatform = new Map(currentVersions.map((row) => [row.platform, row]));
  if (currentByPlatform.size !== fence.snapshots.length) {
    throw new PublicApiError(503, "service_unavailable", "推广聚合在读取期间已失效或更新，请重试");
  }
  const currentSnapshots = fence.snapshots.map((snapshot) => {
    const version = currentByPlatform.get(snapshot.platform)!;
    const isShopScoped = Boolean(snapshot.shopNames?.length);
    return {
      ...snapshot,
      dataVersion: isShopScoped ? null : version.dataVersion,
      shopRevisions: isShopScoped ? version.shopRevisions : [],
    };
  });
  const currentSnapshotToken = await buildPromotionSnapshotToken({
    period: fence.period,
    snapshots: currentSnapshots,
    productDailyRevisions,
  });
  if (currentSnapshotToken !== fence.snapshotToken) {
    throw new PublicApiError(503, "service_unavailable", "推广依赖数据在读取期间已更新，请重试");
  }
}

function promotionAggregateWhere(
  alias: "p" | "s",
  platforms: readonly string[],
  period: NetshopQueryPeriod,
  outlets: readonly NetshopOutletFilter[],
) {
  const where = [`${alias}.platform IN (${platforms.map(() => "?").join(", ")})`, `${alias}.business_date >= ?`, `${alias}.business_date <= ?`];
  const bindings = [...platforms, period.startDate, period.endDate];
  appendNetshopOutletFilter(where, bindings, [...outlets], alias);
  return { whereSql: where.join(" AND "), bindings };
}

export async function getNetshopPromotionOverview(
  db: NetshopDatabase,
  input: PromotionScopeInput = {},
): Promise<NetshopPromotionOverview> {
  const { period, platforms, outlets } = resolvePromotionScope(input);
  const startDate = period?.startDate ?? null;
  const endDate = period?.endDate ?? null;
  const aggregateFence = await requirePromotionAggregateScope(
    db,
    period,
    platforms,
    outlets,
    input.expectedSnapshotToken,
  );
  const aggregatePeriod = aggregateFence.period;
  const aggregateShopScope = promotionAggregateWhere("s", platforms, aggregatePeriod, outlets);
  const aggregateProductScope = promotionAggregateWhere("p", platforms, aggregatePeriod, outlets);

  // The daily aggregation extracts every JSON metric once. Overall totals are
  // derived with window functions instead of scanning the raw rows again for a
  // separate summary query.
  const dailyPromise = db.prepare(
      `WITH daily_series AS (
         SELECT s.business_date,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.spendCents}) AS spend_cents,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.netTransactionAmountCents}) AS net_transaction_amount_cents,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.grossTransactionAmountCents}) AS gross_transaction_amount_cents,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.impressions}) AS impressions,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.clicks}) AS clicks,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.netOrders}) AS net_orders,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.favorites}) AS favorites,
           SUM(${PROMOTION_AGGREGATE_SHOP_FIELDS.cartQuantity}) AS cart_quantity
         FROM netshop_promotion_shop_daily s
         ${PROMOTION_AGGREGATE_SHOP_READY_JOIN_SQL}
         WHERE ${aggregateShopScope.whereSql}
         GROUP BY s.business_date
       )
       SELECT daily_series.*,
         COUNT(*) OVER () AS total_dates,
         MIN(business_date) OVER () AS all_date_min,
         MAX(business_date) OVER () AS all_date_max,
         SUM(spend_cents) OVER () AS all_spend_cents,
         SUM(net_transaction_amount_cents) OVER () AS all_net_transaction_amount_cents,
         SUM(gross_transaction_amount_cents) OVER () AS all_gross_transaction_amount_cents,
         SUM(impressions) OVER () AS all_impressions,
         SUM(clicks) OVER () AS all_clicks,
         SUM(net_orders) OVER () AS all_net_orders,
         SUM(favorites) OVER () AS all_favorites,
         SUM(cart_quantity) OVER () AS all_cart_quantity
       FROM daily_series
       ORDER BY business_date DESC
       LIMIT ?`,
    ).bind(...aggregateShopScope.bindings, NETSHOP_DAILY_SERIES_LIMIT).all<PromotionOverviewDailyRow>();

  // Product count does not need any JSON extraction and is kept separate from
  // the expensive metric aggregation.
  const productCountPromise = db.prepare(
      `SELECT COUNT(DISTINCT p.platform || char(31) || p.shop_name || char(31) || p.product_id) AS total
       FROM netshop_promotion_product_daily p
       ${PROMOTION_AGGREGATE_PRODUCT_READY_JOIN_SQL}
       WHERE ${aggregateProductScope.whereSql}`,
    ).bind(...aggregateProductScope.bindings).first<{ total: number | null }>();

  const paymentWhere = [netshopPromotionPaymentSourceSql];
  const paymentBindings: string[] = [];
  if (period) {
    paymentWhere.push("r.business_date >= ?", "r.business_date < ?");
    paymentBindings.push(period.startDate, period.endExclusive);
  }
  if (platforms.length) {
    paymentWhere.push(`r.platform IN (${platforms.map(() => "?").join(", ")})`);
    paymentBindings.push(...platforms);
  }
  appendNetshopOutletFilter(paymentWhere, paymentBindings, outlets);
  const paymentRowsPromise = db.prepare(
    `WITH daily_series AS (
       SELECT r.business_date, SUM(${dailyPerformanceMetrics.transactionAmountCents}) AS payment_cents
       FROM netshop_rows r
       WHERE ${paymentWhere.join(" AND ")}
       GROUP BY r.business_date
     )
     SELECT daily_series.*, COUNT(*) OVER () AS total_dates
     FROM daily_series
     ORDER BY business_date DESC
     LIMIT ?`,
  ).bind(...paymentBindings, NETSHOP_DAILY_SERIES_LIMIT).all<PromotionPaymentDailyRow>();

  // Options follow the principal-constrained platform scope, but deliberately
  // do not inherit the currently selected outlet filters.
  const outletOptionBindings = [...platforms];
  const outletOptionsPromise = db.prepare(
    `SELECT platform, shop_name, COUNT(*) OVER () AS total
     FROM (
       SELECT s.platform AS platform, s.shop_name AS shop_name
       FROM netshop_promotion_shop_daily s
       ${PROMOTION_AGGREGATE_SHOP_READY_JOIN_SQL}
       WHERE s.platform IN (${platforms.map(() => "?").join(", ")})
       GROUP BY s.platform, s.shop_name
     ) promotion_outlets
     ORDER BY platform ASC, shop_name ASC
     LIMIT ?`,
  ).bind(...outletOptionBindings, NETSHOP_OUTLET_MAX_ITEMS).all<PromotionOutletOptionRow>();

  const [dailyResult, productCountRow, paymentResult, outletOptions] = await Promise.all([
    dailyPromise,
    productCountPromise,
    paymentRowsPromise,
    outletOptionsPromise,
  ]);
  await verifyPromotionAggregateScope(db, aggregateFence);
  const orderedDailyRows = [...dailyResult.results].sort((left, right) => left.business_date.localeCompare(right.business_date));
  const orderedPaymentRows = [...paymentResult.results].sort((left, right) => left.business_date.localeCompare(right.business_date));
  const paymentByDate = new Map(orderedPaymentRows.map((row) => [row.business_date, numberFromDailyMetric(row.payment_cents)]));
  const dailyByDate = new Map(orderedDailyRows.map((row) => [row.business_date, row]));
  const promotionDates = [...dailyByDate.keys()].sort();
  const productDailyDates = [...paymentByDate.keys()].sort();
  const intersectionDates = promotionDates.filter((date) => paymentByDate.has(date));
  const requestedDates = period
    ? dailyDateCoverageForQuery(period.startDate, period.endDate, []).missingDates
    : [...new Set([...promotionDates, ...productDailyDates])].sort();
  const firstDaily = dailyResult.results[0];
  const summarySpend = numberFromDailyMetric(firstDaily?.all_spend_cents);
  const summaryNetTransaction = numberFromDailyMetric(firstDaily?.all_net_transaction_amount_cents);
  const summaryImpressions = numberFromDailyMetric(firstDaily?.all_impressions);
  const summaryClicks = numberFromDailyMetric(firstDaily?.all_clicks);
  const promotionDateTotal = numberFromDailyMetric(firstDaily?.total_dates);
  const productDailyDateTotal = numberFromDailyMetric(paymentResult.results[0]?.total_dates);
  const ratioSpendCents = intersectionDates.reduce((sum, date) => sum + numberFromDailyMetric(dailyByDate.get(date)?.spend_cents), 0);
  const ratioTransactionCents = intersectionDates.reduce((sum, date) => sum + numberFromDailyMetric(dailyByDate.get(date)?.net_transaction_amount_cents), 0);
  const platformPaymentAmountCents = intersectionDates.reduce((sum, date) => sum + numberFromDailyMetric(paymentByDate.get(date)), 0);
  const outletOptionTotal = numberFromDailyMetric(outletOptions.results[0]?.total);

  return {
    snapshotToken: aggregateFence.snapshotToken,
    monetaryUnit: "cents",
    requestedPeriod: { startDate, endDate },
    dataCutoffDate: firstDaily?.all_date_max ?? null,
    coverage: {
      promotionDates,
      productDailyDates,
      intersectionDates,
      missingProductDailyDates: requestedDates.filter((date) => !paymentByDate.has(date)),
      missingPromotionDates: requestedDates.filter((date) => !dailyByDate.has(date)),
      promotionDatesPagination: {
        total: promotionDateTotal,
        returned: promotionDates.length,
        truncated: promotionDates.length < promotionDateTotal,
      },
      productDailyDatesPagination: {
        total: productDailyDateTotal,
        returned: productDailyDates.length,
        truncated: productDailyDates.length < productDailyDateTotal,
      },
      intersectionTruncated: promotionDates.length < promotionDateTotal || productDailyDates.length < productDailyDateTotal,
    },
    summary: {
      productCount: numberFromDailyMetric(productCountRow?.total),
      spendCents: summarySpend,
      netTransactionAmountCents: summaryNetTransaction,
      grossTransactionAmountCents: numberFromDailyMetric(firstDaily?.all_gross_transaction_amount_cents),
      platformPaymentAmountCents,
      impressions: summaryImpressions,
      clicks: summaryClicks,
      netOrders: numberFromDailyMetric(firstDaily?.all_net_orders),
      favorites: numberFromDailyMetric(firstDaily?.all_favorites),
      cartQuantity: numberFromDailyMetric(firstDaily?.all_cart_quantity),
      clickThroughRate: summaryImpressions > 0 ? summaryClicks / summaryImpressions : null,
      averageClickCostCents: summaryClicks > 0 ? summarySpend / summaryClicks : null,
      roas: summarySpend > 0 ? summaryNetTransaction / summarySpend : null,
      spendRate: platformPaymentAmountCents > 0 ? ratioSpendCents / platformPaymentAmountCents : null,
      promotionTransactionShare: platformPaymentAmountCents > 0 ? ratioTransactionCents / platformPaymentAmountCents : null,
    },
    daily: orderedDailyRows.map((row) => {
      const spendCents = numberFromDailyMetric(row.spend_cents);
      const netTransactionAmountCents = numberFromDailyMetric(row.net_transaction_amount_cents);
      const payment = paymentByDate.has(row.business_date) ? numberFromDailyMetric(paymentByDate.get(row.business_date)) : null;
      return {
        date: row.business_date,
        spendCents,
        netTransactionAmountCents,
        platformPaymentAmountCents: payment,
        impressions: numberFromDailyMetric(row.impressions),
        clicks: numberFromDailyMetric(row.clicks),
        netOrders: numberFromDailyMetric(row.net_orders),
        roas: spendCents > 0 ? netTransactionAmountCents / spendCents : null,
        spendRate: payment && payment > 0 ? spendCents / payment : null,
        promotionTransactionShare: payment && payment > 0 ? netTransactionAmountCents / payment : null,
      };
    }),
    dailyPagination: {
      total: promotionDateTotal,
      returned: orderedDailyRows.length,
      truncated: orderedDailyRows.length < promotionDateTotal,
    },
    filterOptions: {
      shops: outletOptions.results.map((row) => ({ platform: row.platform, shopName: row.shop_name })),
      pagination: {
        total: outletOptionTotal,
        returned: outletOptions.results.length,
        truncated: outletOptions.results.length < outletOptionTotal,
      },
    },
  };
}

export async function getNetshopPromotionItems(
  db: NetshopDatabase,
  input: PromotionScopeInput & { query?: string; page?: number; pageSize?: number } = {},
): Promise<NetshopPromotionItems> {
  const page = boundedNetshopInteger(input.page, "page", 1, 1, NETSHOP_QUERY_MAX_PAGE);
  const pageSize = boundedNetshopInteger(input.pageSize, "pageSize", 20, 1, NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE);
  const { period, platforms, outlets } = resolvePromotionScope(input);
  const startDate = period?.startDate ?? null;
  const endDate = period?.endDate ?? null;
  const query = (input.query ?? "").trim().slice(0, 120);
  const aggregateFence = await requirePromotionAggregateScope(db, period, platforms, outlets);
  const aggregatePeriod = aggregateFence.period;
  const offset = (page - 1) * pageSize;
  const aggregateScope = promotionAggregateWhere("p", platforms, aggregatePeriod, outlets);
  const aggregateItemWhere = [aggregateScope.whereSql];
  const aggregateItemBindings = [...aggregateScope.bindings];
  if (query) {
    aggregateItemWhere.push("(p.product_id LIKE ? OR p.product_name LIKE ? OR p.product_line LIKE ?)");
    const term = `%${query}%`;
    aggregateItemBindings.push(term, term, term);
  }
  const result = await db.prepare(
      `WITH grouped_items AS (
         SELECT
           p.product_id AS id,
           MAX(p.platform) AS platform,
           MAX(COALESCE(NULLIF(p.product_name, ''), NULLIF(p.product_line, ''), '')) AS product_name,
           MAX(p.shop_name) AS shop_name,
           COUNT(DISTINCT p.business_date) AS data_days,
           GROUP_CONCAT(DISTINCT p.business_date) AS coverage_dates,
           MIN(p.business_date) AS date_min,
           MAX(p.business_date) AS date_max,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.spendCents}) AS spend_cents,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.netTransactionAmountCents}) AS net_transaction_amount_cents,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.grossTransactionAmountCents}) AS gross_transaction_amount_cents,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.impressions}) AS impressions,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.clicks}) AS clicks,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.netOrders}) AS net_orders,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.favorites}) AS favorites,
           SUM(${PROMOTION_AGGREGATE_PRODUCT_FIELDS.cartQuantity}) AS cart_quantity
         FROM netshop_promotion_product_daily p
         ${PROMOTION_AGGREGATE_PRODUCT_READY_JOIN_SQL}
         WHERE ${aggregateItemWhere.join(" AND ")}
         GROUP BY p.platform, p.shop_name, p.product_id
       )
       SELECT grouped_items.*,
         COUNT(*) OVER () AS total_items,
         MAX(date_max) OVER () AS data_cutoff_date
       FROM grouped_items
       ORDER BY net_transaction_amount_cents DESC, spend_cents DESC, id ASC
       LIMIT ? OFFSET ?`,
    ).bind(...aggregateItemBindings, pageSize, offset).all<PromotionItemsQueryRow>();

  let total = numberFromDailyMetric(result.results[0]?.total_items);
  let dataCutoffDate = result.results[0]?.data_cutoff_date ?? null;
  // A page beyond the final page has no window row from which to read total or
  // cutoff. Recover only for that exceptional request, without JSON metrics.
  if (result.results.length === 0 && page > 1) {
    const fallback = await db.prepare(
        `SELECT COUNT(DISTINCT p.platform || char(31) || p.shop_name || char(31) || p.product_id) AS total,
           MAX(p.business_date) AS data_cutoff_date
         FROM netshop_promotion_product_daily p
         ${PROMOTION_AGGREGATE_PRODUCT_READY_JOIN_SQL}
         WHERE ${aggregateItemWhere.join(" AND ")}`,
      ).bind(...aggregateItemBindings).first<{ total: number | null; data_cutoff_date: string | null }>();
    total = numberFromDailyMetric(fallback?.total);
    dataCutoffDate = fallback?.data_cutoff_date ?? null;
  }
  await verifyPromotionAggregateScope(db, aggregateFence);

  return {
    snapshotToken: aggregateFence.snapshotToken,
    monetaryUnit: "cents",
    requestedPeriod: { startDate, endDate },
    dataCutoffDate,
    items: result.results.map((row) => {
      const spendCents = numberFromDailyMetric(row.spend_cents);
      const netTransactionAmountCents = numberFromDailyMetric(row.net_transaction_amount_cents);
      const impressions = numberFromDailyMetric(row.impressions);
      const clicks = numberFromDailyMetric(row.clicks);
      return {
        id: row.id,
        platform: row.platform,
        productName: row.product_name ?? "",
        shopName: row.shop_name,
        dateMin: row.date_min,
        dateMax: row.date_max,
        dates: [...new Set((row.coverage_dates ?? "").split(",").filter(isNetshopIsoDate))].sort(),
        datesTruncated: !period && numberFromDailyMetric(row.data_days) > 0,
        dataDays: numberFromDailyMetric(row.data_days),
        spendCents,
        netTransactionAmountCents,
        grossTransactionAmountCents: numberFromDailyMetric(row.gross_transaction_amount_cents),
        impressions,
        clicks,
        netOrders: numberFromDailyMetric(row.net_orders),
        favorites: numberFromDailyMetric(row.favorites),
        cartQuantity: numberFromDailyMetric(row.cart_quantity),
        clickThroughRate: impressions > 0 ? clicks / impressions : null,
        averageClickCostCents: clicks > 0 ? spendCents / clicks : null,
        roas: spendCents > 0 ? netTransactionAmountCents / spendCents : null,
      };
    }),
    pagination: {
      page,
      pageSize,
      total,
      returned: result.results.length,
      truncated: offset + result.results.length < total,
    },
  };
}
