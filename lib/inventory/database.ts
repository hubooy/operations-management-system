import {
  getD1Database,
  type D1Database,
} from "@/lib/database/d1";
import type { InventoryStockRow } from "@/lib/imports/inventory-stock";
import {
  importReservationCommitFence,
  rethrowImportPublishError,
  type ImportReservationFence,
} from "@/lib/imports/content-fingerprint";
import { normalizeInventoryPagination } from "@/lib/inventory/query-contract";
import { PublicApiError } from "@/lib/http/api-error";

export const INVENTORY_IMPORT_SOURCE = "吉客云 ERP · 分仓库存查询";
const INVENTORY_IMPORT_CHUNK_SIZE = 300;

export type InventoryDatabase = D1Database;

export type InventoryImportIssue = {
  row?: number;
  field?: string;
  code?: string;
  message: string;
};

type InventoryBatchRow = {
  id: string;
  source: string;
  file_name: string;
  file_size_bytes: number;
  file_hash: string;
  sheet_name: string;
  snapshot_date: string;
  status: string;
  row_count: number;
  inserted_count: number;
  warning_count: number;
  warnings_json: string;
  totals_json: string;
  created_at: string;
  completed_at: string | null;
};

export type InventoryImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  sheetName: string;
  snapshotDate: string;
  status: string;
  rowCount: number;
  insertedCount: number;
  warningCount: number;
  warnings: InventoryImportIssue[];
  totals: unknown;
  createdAt: string;
  completedAt: string | null;
};

type PlanRow = {
  id: string;
  source_batch_id: string;
  product_code: string;
  product_name: string;
  warehouse: string;
  suggested_quantity: number;
  planned_quantity: number;
  coverage_days_tenths: number | null;
  reason: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ReplenishmentPlanItem = {
  id: string;
  sourceBatchId: string;
  productCode: string;
  productName: string;
  warehouse: string;
  suggestedQuantity: number;
  plannedQuantity: number;
  coverageDays: number | null;
  reason: string;
  status: "draft" | "confirmed" | "completed" | "cancelled";
  createdAt: string;
  updatedAt: string;
};

export type ReplenishmentPlanQuery = {
  page?: number;
  pageSize?: number;
  status?: ReplenishmentPlanItem["status"];
  includeCancelled?: boolean;
  query?: string;
  warehouse?: string;
  warehouses?: string[];
  brands?: string[];
  categories?: string[];
};

export class ReplenishmentPlanTransitionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReplenishmentPlanTransitionError";
  }
}

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS inventory_import_batches (
    id TEXT PRIMARY KEY NOT NULL,
    source TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    sheet_name TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    totals_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
  )`,
  `CREATE INDEX IF NOT EXISTS inventory_import_batches_completed_at_idx
    ON inventory_import_batches (completed_at)`,
  `CREATE TABLE IF NOT EXISTS inventory_stock_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    row_key TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    warehouse_type TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    specification TEXT NOT NULL,
    barcode TEXT NOT NULL,
    category TEXT NOT NULL,
    on_hand_quantity INTEGER NOT NULL,
    available_quantity INTEGER NOT NULL,
    locked_quantity INTEGER NOT NULL,
    in_transit_quantity INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL,
    inventory_age_days INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (batch_id, row_key)
  )`,
  `CREATE INDEX IF NOT EXISTS inventory_stock_lines_batch_idx
    ON inventory_stock_lines (batch_id)`,
  `CREATE INDEX IF NOT EXISTS inventory_stock_lines_product_idx
    ON inventory_stock_lines (product_code)`,
  `CREATE INDEX IF NOT EXISTS inventory_stock_lines_warehouse_idx
    ON inventory_stock_lines (warehouse)`,
  `CREATE TABLE IF NOT EXISTS inventory_age_metrics (
    batch_id TEXT NOT NULL,
    row_key TEXT NOT NULL,
    sales_7d_quantity INTEGER NOT NULL DEFAULT 0,
    sales_30d_quantity INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (batch_id, row_key)
  )`,
  `CREATE INDEX IF NOT EXISTS inventory_age_metrics_batch_idx
    ON inventory_age_metrics (batch_id)`,
  `CREATE TABLE IF NOT EXISTS inventory_import_uploads (
    id TEXT PRIMARY KEY NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    chunk_size_bytes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    received_chunk_count INTEGER NOT NULL DEFAULT 0,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'uploading',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
  )`,
  `CREATE INDEX IF NOT EXISTS inventory_import_uploads_expires_at_idx
    ON inventory_import_uploads (expires_at)`,
  `CREATE TABLE IF NOT EXISTS inventory_import_upload_chunks (
    upload_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    object_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (upload_id, chunk_index)
  )`,
  `CREATE INDEX IF NOT EXISTS inventory_import_upload_chunks_upload_id_idx
    ON inventory_import_upload_chunks (upload_id)`,
  `CREATE TABLE IF NOT EXISTS inventory_import_upload_results (
    upload_id TEXT PRIMARY KEY NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS replenishment_plan_items (
    id TEXT PRIMARY KEY NOT NULL,
    source_batch_id TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    suggested_quantity INTEGER NOT NULL,
    planned_quantity INTEGER NOT NULL,
    coverage_days_tenths INTEGER,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS replenishment_plan_items_draft_key_uq
    ON replenishment_plan_items (source_batch_id, product_code, warehouse)
    WHERE status = 'draft'`,
  `CREATE INDEX IF NOT EXISTS replenishment_plan_items_status_idx
    ON replenishment_plan_items (status)`,
  `CREATE INDEX IF NOT EXISTS replenishment_plan_items_product_idx
    ON replenishment_plan_items (product_code)`,
  `CREATE INDEX IF NOT EXISTS replenishment_plan_items_source_batch_idx
    ON replenishment_plan_items (source_batch_id)`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export function getInventoryDatabase(): InventoryDatabase {
  return getD1Database();
}

export async function ensureInventorySchema(db = getInventoryDatabase()): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db
    .batch(schemaStatements.map((statement) => db.prepare(statement)))
    .then(async () => {
      const columns = await db.prepare("PRAGMA table_info(inventory_stock_lines)").all<{ name: string }>();
      if (!columns.results.some((column) => column.name === "brand")) {
        await db.prepare("ALTER TABLE inventory_stock_lines ADD COLUMN brand TEXT NOT NULL DEFAULT ''").run();
      }
    })
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

function mapBatch(row: InventoryBatchRow): InventoryImportBatch {
  return {
    id: row.id,
    source: row.source,
    fileName: row.file_name,
    fileSizeBytes: Number(row.file_size_bytes),
    fileHash: row.file_hash,
    sheetName: row.sheet_name,
    snapshotDate: row.snapshot_date,
    status: row.status,
    rowCount: Number(row.row_count),
    insertedCount: Number(row.inserted_count),
    warningCount: Number(row.warning_count),
    warnings: parseJson(row.warnings_json, []),
    totals: parseJson(row.totals_json, {}),
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}

const batchColumns = `
  id, source, file_name, file_size_bytes, file_hash, sheet_name, snapshot_date,
  status, row_count, inserted_count, warning_count, warnings_json, totals_json,
  created_at, completed_at
`;

export async function findInventoryImportBatchByHash(
  db: InventoryDatabase,
  fileHash: string,
): Promise<InventoryImportBatch | null> {
  const row = await db
    .prepare(`SELECT ${batchColumns} FROM inventory_import_batches WHERE file_hash = ? LIMIT 1`)
    .bind(fileHash)
    .first<InventoryBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function countInventoryRowsOwnedByBatch(
  db: InventoryDatabase,
  batchId: string,
) {
  const row = await db.prepare(
    "SELECT COUNT(*) AS count FROM inventory_stock_lines WHERE batch_id = ?",
  ).bind(batchId).first<{ count: number }>();
  return Number(row?.count ?? 0);
}

export async function findLatestInventoryImportBatch(
  db: InventoryDatabase,
): Promise<InventoryImportBatch | null> {
  const row = await db
    .prepare(
      `SELECT ${batchColumns}
       FROM inventory_import_batches
       WHERE status = 'completed'
       ORDER BY snapshot_date DESC, rowid DESC
       LIMIT 1`,
    )
    .first<InventoryBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function findLatestInventoryImportBatchForSnapshot(
  db: InventoryDatabase,
  snapshotDate: string,
): Promise<InventoryImportBatch | null> {
  const row = await db
    .prepare(
      `SELECT ${batchColumns}
       FROM inventory_import_batches
       WHERE status = 'completed' AND snapshot_date = ?
       ORDER BY rowid DESC
       LIMIT 1`,
    )
    .bind(snapshotDate)
    .first<InventoryBatchRow>();
  return row ? mapBatch(row) : null;
}

export type SystemCostSnapshot = {
  batchId: string;
  snapshotDate: string;
  costs: Array<{
    productCode: string;
    warehouse: string;
    unitCostCents: number;
  }>;
};

/**
 * Read the latest imported inventory snapshot as the system cost source for
 * sales imports. Only positive fixed costs are eligible for automatic use.
 */
export async function findLatestSystemCostSnapshot(
  db: InventoryDatabase,
): Promise<SystemCostSnapshot | null> {
  await ensureInventorySchema(db);
  const batch = await findLatestInventoryImportBatch(db);
  if (!batch) return null;

  const result = await db.prepare(
    `SELECT product_code, warehouse, unit_cost_cents
     FROM inventory_stock_lines
     WHERE batch_id = ? AND unit_cost_cents > 0 AND TRIM(warehouse) <> '刷刷仓'`,
  ).bind(batch.id).all<{
    product_code: string;
    warehouse: string;
    unit_cost_cents: number;
  }>();

  const costs = result.results.flatMap((row) => {
    const productCode = String(row.product_code ?? "").trim();
    const warehouse = String(row.warehouse ?? "").trim();
    const unitCostCents = Number(row.unit_cost_cents);
    if (!productCode || !Number.isSafeInteger(unitCostCents) || unitCostCents <= 0) return [];
    return [{ productCode, warehouse, unitCostCents }];
  });

  return { batchId: batch.id, snapshotDate: batch.snapshotDate, costs };
}

export async function listInventoryImportBatches(
  db: InventoryDatabase,
  input: { page?: number; pageSize?: number } = {},
) {
  const page = input.page ?? 1;
  const pageSize = input.pageSize ?? 20;
  if (!Number.isSafeInteger(page) || page < 1 || page > 10_000) throw new PublicApiError(400, "invalid_request", "page 必须为 1 到 10000 的整数");
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new PublicApiError(400, "invalid_request", "pageSize 必须为 1 到 100 的整数");
  const offset = (page - 1) * pageSize;
  const [result, count] = await Promise.all([
    db.prepare(
      `SELECT ${batchColumns}
       FROM inventory_import_batches
       ORDER BY created_at DESC, id DESC
       LIMIT ? OFFSET ?`,
    ).bind(pageSize, offset).all<InventoryBatchRow>(),
    db.prepare("SELECT COUNT(*) AS total FROM inventory_import_batches").first<{ total: number }>(),
  ]);
  const items = result.results.map(mapBatch);
  const total = Number(count?.total ?? 0);
  return { items, pagination: { page, pageSize, total, returned: items.length, truncated: offset + items.length < total } };
}

const insertStockSql = `
  INSERT INTO inventory_stock_lines (
    batch_id, row_key, source_row_number, snapshot_date, warehouse, warehouse_type,
    product_code, product_name, brand, specification, barcode, category,
    on_hand_quantity, available_quantity, locked_quantity, in_transit_quantity,
    unit_cost_cents, inventory_age_days
  )
  SELECT
    ?,
    json_extract(item.value, '$.rowKey'),
    CAST(json_extract(item.value, '$.sourceRowNumber') AS INTEGER),
    COALESCE(NULLIF(json_extract(item.value, '$.snapshotDate'), ''), ?),
    json_extract(item.value, '$.warehouse'),
    json_extract(item.value, '$.warehouseType'),
    json_extract(item.value, '$.productCode'),
    json_extract(item.value, '$.productName'),
    COALESCE(json_extract(item.value, '$.brand'), ''),
    json_extract(item.value, '$.specification'),
    json_extract(item.value, '$.barcode'),
    json_extract(item.value, '$.category'),
    CAST(json_extract(item.value, '$.onHandQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.availableQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.lockedQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.inTransitQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.unitCostCents') AS INTEGER),
    CAST(json_extract(item.value, '$.inventoryAgeDays') AS INTEGER)
  FROM json_each(?) AS item
  WHERE 1
  ON CONFLICT(batch_id, row_key) DO UPDATE SET
    brand = CASE WHEN excluded.brand <> '' THEN excluded.brand ELSE inventory_stock_lines.brand END
`;

const insertAgeMetricsSql = `
  INSERT INTO inventory_age_metrics (
    batch_id, row_key, sales_7d_quantity, sales_30d_quantity
  )
  SELECT
    ?,
    json_extract(item.value, '$.rowKey'),
    CAST(json_extract(item.value, '$.sales7dQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.sales30dQuantity') AS INTEGER)
  FROM json_each(?) AS item
  WHERE 1
  ON CONFLICT(batch_id, row_key) DO NOTHING
`;

export async function syncInventoryStockDimensions(
  db: InventoryDatabase,
  input: { batchId: string; snapshotDate: string; rows: InventoryStockRow[] },
): Promise<void> {
  const statements = [];
  for (let offset = 0; offset < input.rows.length; offset += INVENTORY_IMPORT_CHUNK_SIZE) {
    const chunk = input.rows.slice(offset, offset + INVENTORY_IMPORT_CHUNK_SIZE);
    statements.push(db.prepare(insertStockSql).bind(input.batchId, input.snapshotDate, JSON.stringify(chunk)));
  }
  if (statements.length > 0) await db.batch(statements);
}

export async function saveInventoryImport(
  db: InventoryDatabase,
  input: {
    fileHash: string;
    fileName: string;
    fileSizeBytes: number;
    sheetName: string;
    snapshotDate: string;
    rows: InventoryStockRow[];
    warnings: InventoryImportIssue[];
    totals: unknown;
    reservationFence?: ImportReservationFence;
  },
): Promise<{ batch: InventoryImportBatch; created: boolean }> {
  const batchId = input.fileHash;
  const statements = [
    db
      .prepare(
        `INSERT INTO inventory_import_batches (
          id, source, file_name, file_size_bytes, file_hash, sheet_name,
          snapshot_date, status, row_count, warning_count, warnings_json, totals_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?)
        ON CONFLICT(file_hash) DO NOTHING`,
      )
      .bind(
        batchId,
        INVENTORY_IMPORT_SOURCE,
        input.fileName,
        input.fileSizeBytes,
        input.fileHash,
        input.sheetName,
        input.snapshotDate,
        input.rows.length,
        input.warnings.length,
        JSON.stringify(input.warnings),
        JSON.stringify(input.totals ?? {}),
      ),
  ];

  for (let offset = 0; offset < input.rows.length; offset += INVENTORY_IMPORT_CHUNK_SIZE) {
    const chunk = input.rows.slice(offset, offset + INVENTORY_IMPORT_CHUNK_SIZE);
    statements.push(
      db.prepare(insertStockSql).bind(batchId, input.snapshotDate, JSON.stringify(chunk)),
      db.prepare(insertAgeMetricsSql).bind(batchId, JSON.stringify(chunk)),
    );
  }

  statements.push(
    db
      .prepare(
        `UPDATE inventory_import_batches
         SET status = 'completed',
             inserted_count = (SELECT COUNT(*) FROM inventory_stock_lines WHERE batch_id = ?),
             completed_at = CURRENT_TIMESTAMP
         WHERE id = ?`,
      )
      .bind(batchId, batchId),
  );
  if (input.reservationFence) statements.push(importReservationCommitFence(db, input.reservationFence));

  let results;
  try {
    results = await db.batch(statements);
  } catch (error) {
    if (input.reservationFence) return rethrowImportPublishError(db, input.reservationFence, error);
    throw error;
  }
  const created = Number(results[0]?.meta?.changes ?? 0) > 0;
  const batch = await findInventoryImportBatchByHash(db, input.fileHash);
  if (!batch) throw new Error("库存导入批次写入后无法读取");
  return { batch, created };
}

function mapPlan(row: PlanRow): ReplenishmentPlanItem {
  const allowed = ["draft", "confirmed", "completed", "cancelled"] as const;
  const status = allowed.includes(row.status as (typeof allowed)[number])
    ? row.status as ReplenishmentPlanItem["status"]
    : "draft";
  return {
    id: row.id,
    sourceBatchId: row.source_batch_id,
    productCode: row.product_code,
    productName: row.product_name,
    warehouse: row.warehouse,
    suggestedQuantity: Number(row.suggested_quantity),
    plannedQuantity: Number(row.planned_quantity),
    coverageDays: row.coverage_days_tenths === null ? null : Number(row.coverage_days_tenths) / 10,
    reason: row.reason,
    status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

const planColumns = `
  id, source_batch_id, product_code, product_name, warehouse,
  suggested_quantity, planned_quantity, coverage_days_tenths,
  reason, status, created_at, updated_at
`;

export async function listReplenishmentPlans(
  db: InventoryDatabase,
  limit = 200,
): Promise<ReplenishmentPlanItem[]> {
  const result = await queryReplenishmentPlans(db, {
    page: 1,
    pageSize: Math.max(1, Math.min(100, Math.trunc(limit))),
  });
  return result.items;
}

export async function getReplenishmentPlanById(
  db: InventoryDatabase,
  id: string,
): Promise<ReplenishmentPlanItem | null> {
  const row = await db.prepare(
    `SELECT ${planColumns}
     FROM replenishment_plan_items
     WHERE id = ?
     LIMIT 1`,
  ).bind(id).first<PlanRow>();
  return row ? mapPlan(row) : null;
}

function uniquePlanFilterValues(values: readonly string[] | undefined, maximum: number) {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, maximum);
}

function buildReplenishmentPlanScopeFilter(
  options: Pick<ReplenishmentPlanQuery, "query" | "warehouse" | "warehouses" | "brands" | "categories">,
) {
  const clauses: string[] = [];
  const values: string[] = [];
  const query = options.query?.trim().slice(0, 100);
  if (query) {
    const keywords = uniquePlanFilterValues(query.split(/[\s,，;；]+/), 8).map((value) => value.toLowerCase());
    if (keywords.length > 0) {
      clauses.push(`(${keywords.map(() => "(INSTR(LOWER(product_code), ?) > 0 OR INSTR(LOWER(product_name), ?) > 0 OR INSTR(LOWER(warehouse), ?) > 0)").join(" OR ")})`);
      for (const keyword of keywords) values.push(keyword, keyword, keyword);
    }
  }
  const warehouses = uniquePlanFilterValues([
    ...(options.warehouses ?? []),
    ...(options.warehouse ? [options.warehouse] : []),
  ], 10);
  if (warehouses.length > 0) {
    clauses.push("warehouse IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(warehouses));
  }
  const brands = uniquePlanFilterValues(options.brands, 20);
  if (brands.length > 0) {
    clauses.push(`EXISTS (
      SELECT 1 FROM inventory_stock_lines plan_stock
      WHERE plan_stock.batch_id = replenishment_plan_items.source_batch_id
        AND plan_stock.product_code = replenishment_plan_items.product_code
        AND plan_stock.warehouse = replenishment_plan_items.warehouse
        AND COALESCE(NULLIF(TRIM(plan_stock.brand), ''), '') IN (SELECT value FROM json_each(?))
    )`);
    values.push(JSON.stringify(brands));
  }
  const categories = uniquePlanFilterValues(options.categories, 20);
  if (categories.length > 0) {
    clauses.push(`EXISTS (
      SELECT 1 FROM inventory_stock_lines plan_stock
      WHERE plan_stock.batch_id = replenishment_plan_items.source_batch_id
        AND plan_stock.product_code = replenishment_plan_items.product_code
        AND plan_stock.warehouse = replenishment_plan_items.warehouse
        AND COALESCE(NULLIF(TRIM(plan_stock.category), ''), '未分类') IN (SELECT value FROM json_each(?))
    )`);
    values.push(JSON.stringify(categories));
  }
  return { clauses, values };
}

export async function queryReplenishmentPlans(
  db: InventoryDatabase,
  options: ReplenishmentPlanQuery = {},
) {
  const { page, pageSize } = normalizeInventoryPagination(options);
  const scope = buildReplenishmentPlanScopeFilter(options);
  const clauses = [...scope.clauses];
  const values: Array<string | number> = [...scope.values];
  if (options.status) {
    clauses.push("status = ?");
    values.push(options.status);
  } else if (!options.includeCancelled) {
    clauses.push("status <> 'cancelled'");
  }
  const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
  const [result, countRow] = await Promise.all([
    db
    .prepare(
      `SELECT ${planColumns}
       FROM replenishment_plan_items
       ${where}
       ORDER BY CASE status WHEN 'draft' THEN 0 WHEN 'confirmed' THEN 1 ELSE 2 END,
                updated_at DESC
       LIMIT ? OFFSET ?`,
    )
    .bind(...values, pageSize, (page - 1) * pageSize)
    .all<PlanRow>(),
    db.prepare(`SELECT COUNT(*) AS total FROM replenishment_plan_items ${where}`)
      .bind(...values)
      .first<{ total: number }>(),
  ]);
  const total = Number(countRow?.total ?? 0);
  return {
    items: result.results.map(mapPlan),
    pagination: {
      page,
      pageSize,
      total,
      returned: result.results.length,
      totalPages: total === 0 ? 0 : Math.ceil(total / pageSize),
      truncated: (page - 1) * pageSize + result.results.length < total,
    },
  };
}

export async function getReplenishmentPlanSummary(
  db: InventoryDatabase,
  currentBatchId: string | null,
  options: Pick<ReplenishmentPlanQuery, "query" | "warehouse" | "warehouses" | "brands" | "categories"> = {},
) {
  const scope = buildReplenishmentPlanScopeFilter(options);
  const where = scope.clauses.length > 0 ? `WHERE ${scope.clauses.join(" AND ")}` : "";
  const row = await db.prepare(
    `SELECT
       COALESCE(SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END), 0) AS draft_count,
       COALESCE(SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END), 0) AS confirmed_count,
       COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_count,
       COALESCE(SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END), 0) AS cancelled_count,
       COALESCE(SUM(CASE
         WHEN status IN ('draft', 'confirmed') OR (status = 'completed' AND source_batch_id = ?)
         THEN planned_quantity ELSE 0 END), 0) AS active_quantity
     FROM replenishment_plan_items
     ${where}`,
  ).bind(currentBatchId ?? "", ...scope.values).first<{
    draft_count: number;
    confirmed_count: number;
    completed_count: number;
    cancelled_count: number;
    active_quantity: number;
  }>();
  return {
    draftCount: Number(row?.draft_count ?? 0),
    confirmedCount: Number(row?.confirmed_count ?? 0),
    completedCount: Number(row?.completed_count ?? 0),
    cancelledCount: Number(row?.cancelled_count ?? 0),
    activeQuantity: Number(row?.active_quantity ?? 0),
  };
}

export async function upsertReplenishmentPlan(
  db: InventoryDatabase,
  input: {
    sourceBatchId: string;
    productCode: string;
    productName: string;
    warehouse: string;
    suggestedQuantity: number;
    plannedQuantity: number;
    coverageDays: number | null;
    reason: string;
  },
): Promise<ReplenishmentPlanItem> {
  const id = crypto.randomUUID();
  await db
    .prepare(
      `INSERT INTO replenishment_plan_items (
        id, source_batch_id, product_code, product_name, warehouse,
        suggested_quantity, planned_quantity, coverage_days_tenths, reason, status
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
      ON CONFLICT(source_batch_id, product_code, warehouse) WHERE status = 'draft' DO UPDATE SET
        product_name = excluded.product_name,
        suggested_quantity = excluded.suggested_quantity,
        planned_quantity = excluded.planned_quantity,
        coverage_days_tenths = excluded.coverage_days_tenths,
        reason = excluded.reason,
        updated_at = CURRENT_TIMESTAMP`,
    )
    .bind(
      id,
      input.sourceBatchId,
      input.productCode,
      input.productName,
      input.warehouse,
      input.suggestedQuantity,
      input.plannedQuantity,
      input.coverageDays === null ? null : Math.round(input.coverageDays * 10),
      input.reason,
    )
    .run();
  const row = await db
    .prepare(
      `SELECT ${planColumns} FROM replenishment_plan_items
       WHERE source_batch_id = ? AND product_code = ? AND warehouse = ? AND status = 'draft'
       ORDER BY updated_at DESC LIMIT 1`,
    )
    .bind(input.sourceBatchId, input.productCode, input.warehouse)
    .first<PlanRow>();
  if (!row) throw new Error("备货计划写入后无法读取");
  return mapPlan(row);
}

export async function updateReplenishmentPlan(
  db: InventoryDatabase,
  input: {
    id: string;
    status: ReplenishmentPlanItem["status"];
    plannedQuantity?: number;
  },
): Promise<ReplenishmentPlanItem | null> {
  const current = await db
    .prepare(`SELECT ${planColumns} FROM replenishment_plan_items WHERE id = ? LIMIT 1`)
    .bind(input.id)
    .first<PlanRow>();
  if (!current) return null;

  const allowedTransitions: Record<ReplenishmentPlanItem["status"], ReplenishmentPlanItem["status"][]> = {
    draft: ["draft", "confirmed", "cancelled"],
    confirmed: ["completed", "cancelled"],
    completed: [],
    cancelled: [],
  };
  const currentPlan = mapPlan(current);
  if (!allowedTransitions[currentPlan.status].includes(input.status)) {
    throw new ReplenishmentPlanTransitionError(`不能将${currentPlan.status}状态的备货计划更新为${input.status}`);
  }
  if (input.plannedQuantity !== undefined && currentPlan.status !== "draft") {
    throw new ReplenishmentPlanTransitionError("只有备货草稿可以调整计划数量");
  }

  const update = await db
    .prepare(
      `UPDATE replenishment_plan_items
       SET status = ?,
           planned_quantity = COALESCE(?, planned_quantity),
           updated_at = CURRENT_TIMESTAMP
       WHERE id = ? AND status = ?`,
    )
    .bind(input.status, input.plannedQuantity ?? null, input.id, currentPlan.status)
    .run();
  if (Number(update.meta?.changes ?? 0) === 0) {
    throw new ReplenishmentPlanTransitionError("备货计划已被其他操作更新，请刷新后重试");
  }
  const row = await db
    .prepare(`SELECT ${planColumns} FROM replenishment_plan_items WHERE id = ? LIMIT 1`)
    .bind(input.id)
    .first<PlanRow>();
  return row ? mapPlan(row) : null;
}
