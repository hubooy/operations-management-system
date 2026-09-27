import { assertMarketPeriod, marketImportRangeKey, marketNaturalKey, MAX_MARKET_IMPORT_ROWS, normalizeMarketSkuCode } from "@/lib/market/import-identity";
import { marketSkuGmvRefreshStatements } from "@/lib/market/gmv-total";
import {
  marketMasterIdentityRefreshStatements,
  marketSystemKpiBulkRevisionFinishStatements,
  marketSystemKpiBulkRevisionStartStatements,
} from "@/lib/market/master-identity";
import { marketStandardSkuImagePriceInheritanceSql, type MarketSchemaDatabase } from "@/lib/market/schema-core";
import {
  importReservationCommitFence,
  type ImportReservationFence,
} from "@/lib/imports/content-fingerprint";

export type MarketEntryForImport = {
  naturalKey: string;
  sourceRowNumber: number;
  periodStart: string;
  periodEnd: string;
  category: string;
  scope: string;
  priceBandFilter: string;
  rankingDimension: "SKU" | "SPU";
  operationMode: "POP" | "自营" | "未知";
  subcategory: string;
  rank: number | null;
  skuCode: string;
  productName: string;
  brand: string;
  priceCents: number | null;
  priceLowCents: number | null;
  priceHighCents: number | null;
  priceEstimated: boolean;
  priceRaw: string;
  gmvCents: number;
  gmvLowCents: number | null;
  gmvHighCents: number | null;
  gmvRaw: string;
  quantity: number;
  quantityLow: number | null;
  quantityHigh: number | null;
  quantityRaw: string;
  pageViews: number;
  pageViewsRaw: string;
  visitors: number;
  visitorsLow: number | null;
  visitorsHigh: number | null;
  visitorsRaw: string;
  conversionBps: number | null;
  conversionLowBps: number | null;
  conversionHighBps: number | null;
  conversionRaw: string;
  cartCustomers: number;
  cartCustomersRaw: string;
  searchClicks: number;
  searchClicksRaw: string;
  imageUrl: string;
  productUrl: string;
  raw: Record<string, string | number | boolean | null>;
};

export type MarketImportIssueForCore = {
  row?: number;
  field?: string;
  message: string;
};

export type MarketImportBatchForCore = {
  id: string;
  sourceType: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  sheetName: string;
  status: string;
  rowCount: number;
  insertedCount: number;
  updatedCount: number;
  warningCount: number;
  periodStart: string | null;
  periodEnd: string | null;
  warnings: MarketImportIssueForCore[];
  createdAt: string;
  completedAt: string | null;
  created?: boolean;
};

type BatchRow = {
  id: string; source_type: string; file_name: string; file_size_bytes: number; file_hash: string;
  sheet_name: string; status: string; row_count: number; inserted_count: number; updated_count: number;
  warning_count: number; period_start: string | null; period_end: string | null; warnings_json: string;
  created_at: string; completed_at: string | null;
};

type RunResult = { meta?: { changes?: number } };

type StagingRow = MarketEntryForImport & { importRangeKey: string };

const MAX_STAGING_PAYLOAD_BYTES = 750_000;
const MAX_STAGING_ROW_BYTES = 500_000;
const MAX_STAGING_PAYLOADS = 100;
const MAX_CLAIM_PAYLOAD_BYTES = 500_000;
const MAX_MARKET_IDENTITY_REFRESH_KEYS = 10_000;
const CLAIM_LEASE_MINUTES = 30;

export const marketBatchColumns = `id, source_type, file_name, file_size_bytes, file_hash, sheet_name, status,
  row_count, inserted_count, updated_count, warning_count, period_start, period_end,
  warnings_json, created_at, completed_at`;

function parseJson<T>(value: string, fallback: T): T {
  try { return JSON.parse(value) as T; } catch { return fallback; }
}

function changes(result: unknown) {
  return Number((result as RunResult | undefined)?.meta?.changes ?? 0);
}

function stagingPayloads(rows: StagingRow[]) {
  const encoder = new TextEncoder();
  const payloads: string[] = [];
  let current: string[] = [];
  let currentBytes = 2;
  for (const row of rows) {
    const serialized = JSON.stringify(row);
    const rowBytes = encoder.encode(serialized).length;
    if (rowBytes > MAX_STAGING_ROW_BYTES) {
      throw new Error(`市场分析导入第 ${row.sourceRowNumber} 行数据过大，无法安全暂存`);
    }
    if (current.length && currentBytes + rowBytes + 1 > MAX_STAGING_PAYLOAD_BYTES) {
      payloads.push(`[${current.join(",")}]`);
      current = [];
      currentBytes = 2;
    }
    current.push(serialized);
    currentBytes += rowBytes + (current.length > 1 ? 1 : 0);
  }
  if (current.length) payloads.push(`[${current.join(",")}]`);
  if (payloads.length > MAX_STAGING_PAYLOADS) throw new Error("市场分析导入数据总量过大，无法在单次请求内安全暂存");
  return payloads;
}

function stringArrayPayloads(values: string[]) {
  const encoder = new TextEncoder();
  const payloads: Array<{ json: string; count: number }> = [];
  let current: string[] = [];
  let currentBytes = 2;
  for (const value of values) {
    const serialized = JSON.stringify(value);
    const valueBytes = encoder.encode(serialized).length;
    if (valueBytes > MAX_CLAIM_PAYLOAD_BYTES) throw new Error("市场分析导入范围标识过大");
    if (current.length && currentBytes + valueBytes + 1 > MAX_CLAIM_PAYLOAD_BYTES) {
      payloads.push({ json: `[${current.join(",")}]`, count: current.length });
      current = [];
      currentBytes = 2;
    }
    current.push(serialized);
    currentBytes += valueBytes + (current.length > 1 ? 1 : 0);
  }
  if (current.length) payloads.push({ json: `[${current.join(",")}]`, count: current.length });
  return payloads;
}

function normalizeRows(rows: MarketEntryForImport[]): StagingRow[] {
  if (!rows.length) throw new Error("市场分析导入没有可保存的数据行");
  if (rows.length > MAX_MARKET_IMPORT_ROWS) throw new Error(`单次市场分析导入不能超过 ${MAX_MARKET_IMPORT_ROWS} 行`);
  const sourceRows = new Set<number>();
  const naturalKeys = new Set<string>();
  return rows.map((input) => {
    assertMarketPeriod(input.periodStart, input.periodEnd);
    if (!Number.isInteger(input.sourceRowNumber) || input.sourceRowNumber < 1 || sourceRows.has(input.sourceRowNumber)) {
      throw new Error(`市场分析导入包含无效或重复的源行号：${input.sourceRowNumber}`);
    }
    sourceRows.add(input.sourceRowNumber);
    const skuCode = normalizeMarketSkuCode(input.skuCode);
    if (!skuCode) throw new Error(`市场分析导入第 ${input.sourceRowNumber} 行缺少商品编码`);
    const normalized = { ...input, skuCode };
    const naturalKey = marketNaturalKey(normalized);
    if (naturalKeys.has(naturalKey)) {
      throw new Error(`市场分析导入包含重复业务行：第 ${input.sourceRowNumber} 行`);
    }
    naturalKeys.add(naturalKey);
    return {
      ...normalized,
      naturalKey,
      importRangeKey: marketImportRangeKey({
        category: normalized.category,
        scope: normalized.scope,
        rankingDimension: normalized.rankingDimension,
        month: normalized.periodEnd.slice(0, 7),
      }),
    };
  });
}

export function mapMarketBatch(row: BatchRow): MarketImportBatchForCore {
  return {
    id: row.id,
    sourceType: row.source_type,
    fileName: row.file_name,
    fileSizeBytes: row.file_size_bytes,
    fileHash: row.file_hash,
    sheetName: row.sheet_name,
    status: row.status,
    rowCount: row.row_count,
    insertedCount: row.inserted_count,
    updatedCount: row.updated_count,
    warningCount: row.warning_count,
    periodStart: row.period_start,
    periodEnd: row.period_end,
    warnings: parseJson(row.warnings_json, []),
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}

const factInsertSql = `INSERT INTO market_ranking_entries (
  natural_key, source_row_number, period_start, period_end, category, scope, price_band_filter, ranking_dimension,
  operation_mode, subcategory, rank, sku_code, product_name, brand, price_cents,
  source_brand, source_operation_mode, source_subcategory,
  price_low_cents, price_high_cents, price_estimated, price_raw,
  gmv_cents, gmv_low_cents, gmv_high_cents, gmv_raw,
  quantity, quantity_low, quantity_high, quantity_raw, page_views, page_views_raw,
  visitors, visitors_low, visitors_high, visitors_raw,
  conversion_bps, conversion_low_bps, conversion_high_bps, conversion_raw,
  cart_customers, cart_customers_raw, search_clicks, search_clicks_raw, image_url, product_url,
  raw_json, last_import_batch_id
)
SELECT
  json_extract(s.row_json, '$.naturalKey'), json_extract(s.row_json, '$.sourceRowNumber'),
  json_extract(s.row_json, '$.periodStart'), json_extract(s.row_json, '$.periodEnd'),
  json_extract(s.row_json, '$.category'), json_extract(s.row_json, '$.scope'),
  json_extract(s.row_json, '$.priceBandFilter'), json_extract(s.row_json, '$.rankingDimension'),
  json_extract(s.row_json, '$.operationMode'), json_extract(s.row_json, '$.subcategory'),
  json_extract(s.row_json, '$.rank'), json_extract(s.row_json, '$.skuCode'),
  json_extract(s.row_json, '$.productName'), json_extract(s.row_json, '$.brand'),
  json_extract(s.row_json, '$.priceCents'), json_extract(s.row_json, '$.brand'),
  json_extract(s.row_json, '$.operationMode'), json_extract(s.row_json, '$.subcategory'),
  json_extract(s.row_json, '$.priceLowCents'), json_extract(s.row_json, '$.priceHighCents'),
  json_extract(s.row_json, '$.priceEstimated'), json_extract(s.row_json, '$.priceRaw'),
  json_extract(s.row_json, '$.gmvCents'), json_extract(s.row_json, '$.gmvLowCents'),
  json_extract(s.row_json, '$.gmvHighCents'), json_extract(s.row_json, '$.gmvRaw'),
  json_extract(s.row_json, '$.quantity'), json_extract(s.row_json, '$.quantityLow'),
  json_extract(s.row_json, '$.quantityHigh'), json_extract(s.row_json, '$.quantityRaw'),
  json_extract(s.row_json, '$.pageViews'), json_extract(s.row_json, '$.pageViewsRaw'),
  json_extract(s.row_json, '$.visitors'), json_extract(s.row_json, '$.visitorsLow'),
  json_extract(s.row_json, '$.visitorsHigh'), json_extract(s.row_json, '$.visitorsRaw'),
  json_extract(s.row_json, '$.conversionBps'), json_extract(s.row_json, '$.conversionLowBps'),
  json_extract(s.row_json, '$.conversionHighBps'), json_extract(s.row_json, '$.conversionRaw'),
  json_extract(s.row_json, '$.cartCustomers'), json_extract(s.row_json, '$.cartCustomersRaw'),
  json_extract(s.row_json, '$.searchClicks'), json_extract(s.row_json, '$.searchClicksRaw'),
  json_extract(s.row_json, '$.imageUrl'), json_extract(s.row_json, '$.productUrl'),
  json_extract(s.row_json, '$.raw'), ?
FROM market_import_staging_rows s
WHERE s.batch_id=?
  AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id=? AND claim_token=?)=?
ON CONFLICT(period_start, period_end, category, scope, price_band_filter, ranking_dimension, sku_code) DO UPDATE SET
  natural_key=excluded.natural_key, source_row_number=excluded.source_row_number,
  category=excluded.category, scope=excluded.scope, price_band_filter=excluded.price_band_filter,
  ranking_dimension=excluded.ranking_dimension, operation_mode=excluded.operation_mode,
  subcategory=excluded.subcategory, rank=excluded.rank, product_name=excluded.product_name,
  brand=excluded.brand, source_brand=excluded.source_brand,
  source_operation_mode=excluded.source_operation_mode, source_subcategory=excluded.source_subcategory,
  price_cents=excluded.price_cents, price_low_cents=excluded.price_low_cents,
  price_high_cents=excluded.price_high_cents, price_estimated=excluded.price_estimated,
  price_raw=excluded.price_raw, gmv_cents=excluded.gmv_cents, gmv_low_cents=excluded.gmv_low_cents,
  gmv_high_cents=excluded.gmv_high_cents, gmv_raw=excluded.gmv_raw,
  quantity=excluded.quantity, quantity_low=excluded.quantity_low, quantity_high=excluded.quantity_high,
  quantity_raw=excluded.quantity_raw, page_views=excluded.page_views, page_views_raw=excluded.page_views_raw,
  visitors=excluded.visitors, visitors_low=excluded.visitors_low, visitors_high=excluded.visitors_high,
  visitors_raw=excluded.visitors_raw, conversion_bps=excluded.conversion_bps,
  conversion_low_bps=excluded.conversion_low_bps, conversion_high_bps=excluded.conversion_high_bps,
  conversion_raw=excluded.conversion_raw, cart_customers=excluded.cart_customers,
  cart_customers_raw=excluded.cart_customers_raw, search_clicks=excluded.search_clicks,
  search_clicks_raw=excluded.search_clicks_raw, image_url=excluded.image_url,
  product_url=excluded.product_url, raw_json=excluded.raw_json,
  last_import_batch_id=excluded.last_import_batch_id, updated_at=CURRENT_TIMESTAMP`;

const replaceClaimedMarketFactsSql = `DELETE FROM market_ranking_entries AS fact
WHERE (
  fact.period_start, fact.period_end, fact.category, fact.scope,
  fact.price_band_filter, fact.ranking_dimension
) IN (
  SELECT DISTINCT
    json_extract(staged.row_json, '$.periodStart'),
    json_extract(staged.row_json, '$.periodEnd'),
    json_extract(staged.row_json, '$.category'),
    json_extract(staged.row_json, '$.scope'),
    json_extract(staged.row_json, '$.priceBandFilter'),
    json_extract(staged.row_json, '$.rankingDimension')
  FROM market_import_staging_rows staged
  WHERE staged.batch_id=?
)
AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id = ? AND claim_token = ?) = ?`;

const captureOldMarketIdentityRefreshKeysSql = `WITH authorized AS MATERIALIZED (
  SELECT 1
  WHERE EXISTS (SELECT 1 FROM market_import_batches
    WHERE id=? AND owner_token=? AND status='processing')
    AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id=? AND claim_token=?)=?
), replacement_ranges AS MATERIALIZED (
  SELECT DISTINCT
    json_extract(staged.row_json, '$.periodStart') period_start,
    json_extract(staged.row_json, '$.periodEnd') period_end,
    json_extract(staged.row_json, '$.category') category,
    json_extract(staged.row_json, '$.scope') scope,
    json_extract(staged.row_json, '$.priceBandFilter') price_band_filter,
    json_extract(staged.row_json, '$.rankingDimension') ranking_dimension
  FROM market_import_staging_rows staged, authorized
  WHERE staged.batch_id=?
)
INSERT OR IGNORE INTO market_import_identity_refresh_keys_v2
  (batch_id, owner_token, category, scope, ranking_dimension, sku_code)
SELECT DISTINCT ?, ?, fact.category, fact.scope, fact.ranking_dimension, fact.sku_code
  FROM market_ranking_entries fact
  JOIN replacement_ranges replacement
    ON fact.period_start=replacement.period_start
    AND fact.period_end=replacement.period_end
    AND fact.category=replacement.category
    AND fact.scope=replacement.scope
    AND fact.price_band_filter=replacement.price_band_filter
    AND fact.ranking_dimension=replacement.ranking_dimension
  LIMIT ${MAX_MARKET_IDENTITY_REFRESH_KEYS + 1}`;

const captureNewMarketIdentityRefreshKeysSql = `WITH authorized AS MATERIALIZED (
  SELECT 1
  WHERE EXISTS (SELECT 1 FROM market_import_batches
    WHERE id=? AND owner_token=? AND status='processing')
    AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id=? AND claim_token=?)=?
)
INSERT OR IGNORE INTO market_import_identity_refresh_keys_v2
  (batch_id, owner_token, category, scope, ranking_dimension, sku_code)
SELECT ?, ?,
    json_extract(staged.row_json, '$.category'),
    json_extract(staged.row_json, '$.scope'),
    json_extract(staged.row_json, '$.rankingDimension'),
    json_extract(staged.row_json, '$.skuCode')
  FROM market_import_staging_rows staged, authorized
  WHERE staged.batch_id=?
  GROUP BY 3, 4, 5, 6
  LIMIT ${MAX_MARKET_IDENTITY_REFRESH_KEYS + 1}`;

const snapshotInsertSql = `WITH decoded AS (
  SELECT s.row_number,
    json_extract(s.row_json, '$.category') category,
    json_extract(s.row_json, '$.scope') scope,
    json_extract(s.row_json, '$.skuCode') sku_code,
    json_extract(s.row_json, '$.rankingDimension') ranking_dimension,
    substr(json_extract(s.row_json, '$.periodEnd'), 1, 7) month,
    json_extract(s.row_json, '$.periodStart') period_start,
    json_extract(s.row_json, '$.periodEnd') period_end,
    json_extract(s.row_json, '$.priceCents') price_cents,
    json_extract(s.row_json, '$.gmvCents') gmv_cents,
    json_extract(s.row_json, '$.quantity') quantity,
    json_extract(s.row_json, '$.priceLowCents') price_low_cents,
    json_extract(s.row_json, '$.priceHighCents') price_high_cents,
    json_extract(s.row_json, '$.imageUrl') image_url
  FROM market_import_staging_rows s
  WHERE s.batch_id=?
    AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id=? AND claim_token=?)=?
), ranked AS (
  SELECT decoded.*, ROW_NUMBER() OVER (
    PARTITION BY category, scope, sku_code, ranking_dimension, month
    ORDER BY period_end DESC, period_start DESC, row_number DESC
  ) snapshot_rn FROM decoded
)
INSERT INTO market_price_snapshots (
  id, category, scope, sku_code, ranking_dimension, month, source_price_cents,
  average_transaction_price_cents, price_low_cents, price_high_cents,
  image_content_sha256, image_url, confirmation_status, source_import_batch_id
)
SELECT
  'market-price-import-v4-' ||
    length(CAST(category AS BLOB)) || ':' || category || '|' ||
    length(CAST(scope AS BLOB)) || ':' || scope || '|' ||
    length(CAST(sku_code AS BLOB)) || ':' || sku_code || '|' ||
    length(CAST(ranking_dimension AS BLOB)) || ':' || ranking_dimension || '|' || month,
  category, scope, sku_code, ranking_dimension, month, price_cents,
  CASE WHEN quantity>0 THEN CAST(ROUND(gmv_cents * 1.0 / quantity) AS INTEGER) ELSE NULL END,
  price_low_cents, price_high_cents,
  COALESCE((SELECT content_sha256 FROM market_image_cache WHERE source_url=ranked.image_url AND status='ready' LIMIT 1), ''),
  image_url, CASE WHEN price_cents IS NULL THEN 'missing' ELSE 'source_table' END, ?
FROM ranked WHERE snapshot_rn=1
ON CONFLICT(category, scope, sku_code, ranking_dimension, month) DO UPDATE SET
  source_price_cents=excluded.source_price_cents,
  average_transaction_price_cents=excluded.average_transaction_price_cents,
  price_low_cents=excluded.price_low_cents, price_high_cents=excluded.price_high_cents,
  image_content_sha256=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN excluded.image_content_sha256
    WHEN excluded.image_content_sha256<>'' THEN excluded.image_content_sha256
    ELSE market_price_snapshots.image_content_sha256 END,
  image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE market_price_snapshots.image_url END,
  ai_image_price_cents=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN NULL
    ELSE market_price_snapshots.ai_image_price_cents END,
  ai_price_type=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN ''
    ELSE market_price_snapshots.ai_price_type END,
  ai_confidence_bps=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN NULL
    ELSE market_price_snapshots.ai_confidence_bps END,
  ai_reason=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN ''
    ELSE market_price_snapshots.ai_reason END,
  confirmed_market_price_cents=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN NULL
    ELSE market_price_snapshots.confirmed_market_price_cents END,
  confirmed_by=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN ''
    ELSE market_price_snapshots.confirmed_by END,
  confirmed_at=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN NULL
    ELSE market_price_snapshots.confirmed_at END,
  source_job_item_id=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN ''
    ELSE market_price_snapshots.source_job_item_id END,
  prompt_version_id=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url THEN ''
    ELSE market_price_snapshots.prompt_version_id END,
  confirmation_status=CASE
    WHEN excluded.image_url<>'' AND excluded.image_url<>market_price_snapshots.image_url
      THEN CASE WHEN excluded.source_price_cents IS NOT NULL THEN 'source_table' ELSE 'missing' END
    WHEN market_price_snapshots.confirmed_market_price_cents IS NOT NULL THEN market_price_snapshots.confirmation_status
    WHEN excluded.source_price_cents IS NOT NULL THEN 'source_table' ELSE 'missing' END,
  source_import_batch_id=excluded.source_import_batch_id, updated_at=CURRENT_TIMESTAMP`;

const taxonomyInsertSql = `INSERT INTO market_subcategory_taxonomy
  (id, category, subcategory, status, sort_order, created_by, updated_by)
SELECT
  'market-subcategory-v1-' ||
    length(CAST(category AS BLOB)) || ':' || category || '|' ||
    length(CAST(subcategory AS BLOB)) || ':' || subcategory,
  category, subcategory, 'active', 0, 'market-import', 'market-import'
FROM (
  SELECT DISTINCT json_extract(row_json, '$.category') category,
    json_extract(row_json, '$.subcategory') subcategory
  FROM market_import_staging_rows
  WHERE batch_id=? AND trim(COALESCE(json_extract(row_json, '$.subcategory'), ''))<>''
    AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id=? AND claim_token=?)=?
)
WHERE 1=1
ON CONFLICT(category, subcategory) DO UPDATE SET
  status='active', updated_by='market-import', updated_at=CURRENT_TIMESTAMP`;

export async function saveMarketImportCore(input: {
  db: MarketSchemaDatabase;
  batchId: string;
  sourceType: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  sheetName: string;
  rows: MarketEntryForImport[];
  warnings: MarketImportIssueForCore[];
  replaceRangeKeys?: string[];
  executionFence?: { taskId: string; token: string };
  reservationFence?: ImportReservationFence;
}): Promise<MarketImportBatchForCore> {
  const { db } = input;
  const rows = normalizeRows(input.rows);
  const payloads = stagingPayloads(rows);
  const dates = rows.flatMap((row) => [row.periodStart, row.periodEnd]).sort();
  const rowRangeKeys = [...new Set(rows.map((row) => row.importRangeKey))];
  const claimKeys = [...new Set(input.replaceRangeKeys ?? rowRangeKeys)].sort();
  if (!claimKeys.length || rowRangeKeys.some((key) => !claimKeys.includes(key))) {
    throw new Error("市场分析替换范围未完整覆盖文件中的业务范围");
  }
  const claimPayloads = stringArrayPayloads(claimKeys);
  const claimToken = crypto.randomUUID();
  let completedFallback: MarketImportBatchForCore | null = null;
  let publishAttempted = false;

  try {
    const insertedBatch = await db.prepare(
      `INSERT OR IGNORE INTO market_import_batches (
        id, source_type, file_name, file_size_bytes, file_hash, sheet_name, status,
        row_count, warning_count, period_start, period_end, warnings_json, owner_token
      ) VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)`,
    ).bind(
      input.batchId, input.sourceType, input.fileName, input.fileSizeBytes, input.fileHash,
      input.sheetName, rows.length, input.warnings.length, dates[0] ?? null,
      dates.at(-1) ?? null, JSON.stringify(input.warnings.slice(0, 100)), claimToken,
    ).run();
    // D1 reports trigger side effects in meta.changes. The batch table has
    // revision-maintenance triggers, so a successful single-row insert/update
    // can legitimately report more than one changed row. Zero still means the
    // INSERT OR IGNORE lost the idempotency race.
    if (changes(insertedBatch) < 1) {
      const existing = await db.prepare(`SELECT ${marketBatchColumns} FROM market_import_batches WHERE file_hash=? LIMIT 1`)
        .bind(input.fileHash).first<BatchRow>();
      if (existing?.status === "completed") return { ...mapMarketBatch(existing), created: false };
      throw new Error("同一市场分析文件正在导入或此前导入失败，请稍后重试或先清理失败批次");
    }
    let claimedCount = 0;
    for (const payload of claimPayloads) {
      const claimed = await db.prepare(`INSERT INTO market_import_range_claims
        (range_key, batch_id, claim_token, claimed_at, lease_expires_at)
        SELECT CAST(value AS TEXT), ?, ?, CURRENT_TIMESTAMP, datetime('now', ?)
        FROM json_each(?)
        WHERE EXISTS (SELECT 1 FROM market_import_batches
          WHERE id=? AND owner_token=? AND status='processing')
        ON CONFLICT(range_key) DO UPDATE SET
          batch_id=excluded.batch_id, claim_token=excluded.claim_token,
          claimed_at=excluded.claimed_at, lease_expires_at=excluded.lease_expires_at
        WHERE datetime(market_import_range_claims.lease_expires_at)<=CURRENT_TIMESTAMP`)
        .bind(input.batchId, claimToken, `+${CLAIM_LEASE_MINUTES} minutes`, payload.json, input.batchId, claimToken).run();
      claimedCount += changes(claimed);
      if (changes(claimed) !== payload.count) break;
    }
    if (claimedCount !== claimKeys.length) {
      await db.prepare("DELETE FROM market_import_range_claims WHERE batch_id=? AND claim_token=?")
        .bind(input.batchId, claimToken).run();
      throw new Error("相同品类、范围、维度和月份已有市场分析导入正在发布，请稍后重试");
    }

    for (const payload of payloads) {
      await db.prepare(`INSERT INTO market_import_staging_rows (batch_id, row_number, range_key, row_json)
        SELECT ?, CAST(json_extract(value, '$.sourceRowNumber') AS INTEGER),
          json_extract(value, '$.importRangeKey'), value
        FROM json_each(?)
        WHERE EXISTS (SELECT 1 FROM market_import_batches WHERE id=? AND owner_token=? AND status='processing')`)
        .bind(input.batchId, payload, input.batchId, claimToken).run();
    }
    const staged = await db.prepare("SELECT COUNT(*) total FROM market_import_staging_rows WHERE batch_id=?")
      .bind(input.batchId).first<{ total: number }>();
    if (Number(staged?.total ?? 0) !== rows.length) throw new Error("市场分析导入暂存校验失败，未发布任何数据");

    const existing = await db.prepare(`SELECT COUNT(*) total
      FROM market_import_staging_rows s
      JOIN market_ranking_entries m
        ON m.period_start=json_extract(s.row_json, '$.periodStart')
        AND m.period_end=json_extract(s.row_json, '$.periodEnd')
        AND m.category=json_extract(s.row_json, '$.category')
        AND m.scope=json_extract(s.row_json, '$.scope')
        AND m.price_band_filter=json_extract(s.row_json, '$.priceBandFilter')
        AND m.ranking_dimension=json_extract(s.row_json, '$.rankingDimension')
        AND m.sku_code=json_extract(s.row_json, '$.skuCode')
      WHERE s.batch_id=?`).bind(input.batchId).first<{ total: number }>();
    const updated = Number(existing?.total ?? 0);
    const inserted = rows.length - updated;
    const fenceStatements = input.executionFence ? [
      db.prepare(`UPDATE market_import_batches SET source_type=CASE WHEN EXISTS (
        SELECT 1 FROM market_download_tasks
        WHERE id=? AND status='downloading' AND execution_token=?
      ) THEN source_type ELSE NULL END
      WHERE id=? AND owner_token=? AND status='processing'`)
        .bind(input.executionFence.taskId, input.executionFence.token, input.batchId, claimToken),
    ] : [];

    publishAttempted = true;
    const publishStatements = [
      db.prepare(`INSERT INTO market_system_kpi_cache_control
          (id, suppress_all_revision, suppress_identity_revision, owner_token, updated_at)
        SELECT CASE WHEN EXISTS (
          SELECT 1 FROM market_import_batches batch
          WHERE batch.id=? AND batch.owner_token=? AND batch.status='processing'
            AND (SELECT COUNT(*) FROM market_import_range_claims
              WHERE batch_id=? AND claim_token=?)=?
        ) THEN 1 ELSE 2 END, 0, 0, '', CURRENT_TIMESTAMP
        ON CONFLICT(id) DO UPDATE SET updated_at=market_system_kpi_cache_control.updated_at`)
        .bind(input.batchId, claimToken, input.batchId, claimToken, claimKeys.length),
      ...fenceStatements,
      ...marketSystemKpiBulkRevisionStartStatements(db, claimToken),
      db.prepare(captureOldMarketIdentityRefreshKeysSql).bind(
        input.batchId, claimToken, input.batchId, claimToken, claimKeys.length,
        input.batchId, input.batchId, claimToken,
      ),
      db.prepare(captureNewMarketIdentityRefreshKeysSql).bind(
        input.batchId, claimToken, input.batchId, claimToken, claimKeys.length,
        input.batchId, claimToken, input.batchId,
      ),
      db.prepare(`UPDATE market_import_batches SET source_type=CASE WHEN (
        SELECT COUNT(*) FROM market_import_identity_refresh_keys_v2 WHERE batch_id=? AND owner_token=?
      )<=? THEN source_type ELSE NULL END
      WHERE id=? AND owner_token=? AND status='processing'`)
        .bind(input.batchId, claimToken, MAX_MARKET_IDENTITY_REFRESH_KEYS, input.batchId, claimToken),
      db.prepare(replaceClaimedMarketFactsSql).bind(input.batchId, input.batchId, claimToken, claimKeys.length),
      db.prepare(factInsertSql).bind(input.batchId, input.batchId, input.batchId, claimToken, claimKeys.length),
      db.prepare(snapshotInsertSql).bind(input.batchId, input.batchId, claimToken, claimKeys.length, input.batchId),
      db.prepare(marketStandardSkuImagePriceInheritanceSql("target.source_import_batch_id=?")).bind(input.batchId),
      db.prepare(taxonomyInsertSql).bind(input.batchId, input.batchId, claimToken, claimKeys.length),
      ...marketSkuGmvRefreshStatements(db, { batchId: input.batchId, ownerToken: claimToken }),
      ...marketMasterIdentityRefreshStatements(db, {
        batchId: input.batchId, ownerToken: claimToken, revisionControl: "external",
      }),
      ...marketSystemKpiBulkRevisionFinishStatements(db, claimToken),
    ];
    const completionStatementIndex = publishStatements.length;
    publishStatements.push(
      db.prepare(`UPDATE market_import_batches
        SET status='completed', inserted_count=?, updated_count=?, completed_at=CURRENT_TIMESTAMP
        WHERE id=? AND owner_token=? AND status='processing'
          AND (SELECT COUNT(*) FROM market_import_range_claims WHERE batch_id=? AND claim_token=?)=?`)
        .bind(inserted, updated, input.batchId, claimToken, input.batchId, claimToken, claimKeys.length),
      db.prepare(`DELETE FROM market_import_staging_rows WHERE batch_id=? AND EXISTS (
        SELECT 1 FROM market_import_batches WHERE id=? AND owner_token=?)`)
        .bind(input.batchId, input.batchId, claimToken),
      db.prepare(`DELETE FROM market_import_identity_refresh_keys_v2
        WHERE batch_id=? AND owner_token=? AND EXISTS (
          SELECT 1 FROM market_import_batches WHERE id=? AND owner_token=?)`)
        .bind(input.batchId, claimToken, input.batchId, claimToken),
      db.prepare("DELETE FROM market_import_range_claims WHERE batch_id=? AND claim_token=?").bind(input.batchId, claimToken),
    );
    if (input.reservationFence) publishStatements.push(importReservationCommitFence(db, input.reservationFence));
    const publish = await db.batch(publishStatements) as RunResult[];
    if (changes(publish[completionStatementIndex]) < 1) throw new Error("市场分析导入发布租约已失效，未发布任何数据");

    const completedAt = new Date().toISOString();
    completedFallback = {
      id: input.batchId, sourceType: input.sourceType, fileName: input.fileName,
      fileSizeBytes: input.fileSizeBytes, fileHash: input.fileHash, sheetName: input.sheetName,
      status: "completed", rowCount: rows.length, insertedCount: inserted, updatedCount: updated,
      warningCount: input.warnings.length, periodStart: dates[0] ?? null, periodEnd: dates.at(-1) ?? null,
      warnings: input.warnings.slice(0, 100), createdAt: completedAt, completedAt,
    };

    const row = await db.prepare(`SELECT ${marketBatchColumns} FROM market_import_batches WHERE id=? LIMIT 1`)
      .bind(input.batchId).first<BatchRow>();
    return row ? { ...mapMarketBatch(row), created: true } : { ...completedFallback, created: true };
  } catch (error) {
    if (completedFallback) return completedFallback;
    if (publishAttempted) {
      try {
        const committed = await db.prepare(`SELECT ${marketBatchColumns} FROM market_import_batches WHERE id=? LIMIT 1`)
          .bind(input.batchId).first<BatchRow>();
        if (committed?.status === "completed") return { ...mapMarketBatch(committed), created: true };
      } catch {
        // Keep the original publish error; a same-hash retry will reconcile an unknown commit outcome.
      }
    }
    await db.batch([
      db.prepare(`DELETE FROM market_import_staging_rows WHERE batch_id=? AND EXISTS (
        SELECT 1 FROM market_import_batches WHERE id=? AND owner_token=?)`)
        .bind(input.batchId, input.batchId, claimToken),
      db.prepare(`DELETE FROM market_import_identity_refresh_keys_v2
        WHERE batch_id=? AND owner_token=? AND EXISTS (
          SELECT 1 FROM market_import_batches WHERE id=? AND owner_token=?)`)
        .bind(input.batchId, claimToken, input.batchId, claimToken),
      db.prepare("DELETE FROM market_import_range_claims WHERE batch_id=? AND claim_token=?").bind(input.batchId, claimToken),
      db.prepare(`UPDATE market_import_batches SET status='failed', completed_at=CURRENT_TIMESTAMP
        WHERE id=? AND owner_token=? AND status='processing'`).bind(input.batchId, claimToken),
    ]).catch(() => undefined);
    throw error;
  }
}
