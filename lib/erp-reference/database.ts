import {
  getD1Database,
  type D1Database,
} from "@/lib/database/d1";
import {
  ERP_REFERENCE_SOURCE_LABELS,
  type ComboItemImportRow,
  type ErpReferenceIssue,
  type ErpReferenceSourceKey,
  type InventoryAgeImportRow,
  type ProductMasterRow,
} from "@/lib/imports/erp-reference";
import {
  importReservationCommitFence,
  rethrowImportPublishError,
  type ImportReservationFence,
} from "@/lib/imports/content-fingerprint";
import { PublicApiError } from "@/lib/http/api-error";
import {
  ERP_REFERENCE_PROJECTION_CANONICAL_FORMAT_VERSION,
  ERP_PRODUCT_PROJECTION_SCOPE_JSON,
  assertErpProjectionContentHash,
  bumpErpProductProjectionRevisionSql,
  erpReferenceProjectionSchemaStatements,
  insertErpReferenceProjectionOutboxEventSql,
} from "@/lib/erp-reference/projection-outbox";

export type ErpReferenceDatabase = D1Database;

export type ErpReferenceImportBatch = {
  id: string;
  sourceKey: ErpReferenceSourceKey;
  sourceLabel: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  sheetName: string;
  snapshotDate: string | null;
  status: string;
  rowCount: number;
  insertedCount: number;
  updatedCount: number;
  excludedCount: number;
  warningCount: number;
  warnings: ErpReferenceIssue[];
  totals: unknown;
  createdAt: string;
  completedAt: string | null;
};

type BatchRow = {
  id: string;
  source_key: ErpReferenceSourceKey;
  source_label: string;
  file_name: string;
  file_size_bytes: number;
  file_hash: string;
  sheet_name: string;
  snapshot_date: string | null;
  status: string;
  row_count: number;
  inserted_count: number;
  updated_count: number;
  excluded_count: number;
  warning_count: number;
  warnings_json: string;
  totals_json: string;
  created_at: string;
  completed_at: string | null;
};

const WRITE_CHUNK_SIZE = 200;
const LOOKUP_CHUNK_SIZE = 50;

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS erp_reference_import_batches (
    id TEXT PRIMARY KEY NOT NULL,
    source_key TEXT NOT NULL,
    source_label TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    snapshot_date TEXT,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    excluded_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    totals_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE (source_key, file_hash)
  )`,
  `CREATE INDEX IF NOT EXISTS erp_reference_import_batches_source_created_idx
    ON erp_reference_import_batches (source_key, created_at)`,
  `CREATE TABLE IF NOT EXISTS erp_product_master (
    product_code TEXT PRIMARY KEY NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    specification TEXT NOT NULL DEFAULT '',
    barcode TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    supplier TEXT NOT NULL DEFAULT '',
    product_status TEXT NOT NULL DEFAULT '',
    source_row_number INTEGER NOT NULL,
    last_import_batch_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS erp_product_master_name_idx ON erp_product_master (product_name)`,
  `CREATE INDEX IF NOT EXISTS erp_product_master_barcode_idx ON erp_product_master (barcode)`,
  `CREATE INDEX IF NOT EXISTS erp_product_master_last_batch_idx ON erp_product_master (last_import_batch_id)`,
  `CREATE TABLE IF NOT EXISTS erp_combo_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_code TEXT NOT NULL,
    parent_name TEXT NOT NULL DEFAULT '',
    child_code TEXT NOT NULL,
    child_name TEXT NOT NULL DEFAULT '',
    child_quantity_milli INTEGER NOT NULL,
    source_row_number INTEGER NOT NULL,
    last_import_batch_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (parent_code, child_code)
  )`,
  `CREATE INDEX IF NOT EXISTS erp_combo_items_parent_idx ON erp_combo_items (parent_code)`,
  `CREATE INDEX IF NOT EXISTS erp_combo_items_child_idx ON erp_combo_items (child_code)`,
  `CREATE INDEX IF NOT EXISTS erp_combo_items_last_batch_idx ON erp_combo_items (last_import_batch_id)`,
  ...erpReferenceProjectionSchemaStatements,
] as const;

const schemaReady = new WeakMap<object, Promise<void>>();

export function getErpReferenceDatabase(): ErpReferenceDatabase {
  return getD1Database();
}

export async function ensureErpReferenceSchema(db = getErpReferenceDatabase()) {
  const key = db as unknown as object;
  const existing = schemaReady.get(key);
  if (existing) return existing;
  const setup = db.batch(schemaStatements.map((sql) => db.prepare(sql))).then(() => undefined).catch((error) => {
    schemaReady.delete(key);
    throw error;
  });
  schemaReady.set(key, setup);
  return setup;
}

function parseJson<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function mapBatch(row: BatchRow): ErpReferenceImportBatch {
  return {
    id: row.id,
    sourceKey: row.source_key,
    sourceLabel: row.source_label,
    fileName: row.file_name,
    fileSizeBytes: Number(row.file_size_bytes),
    fileHash: row.file_hash,
    sheetName: row.sheet_name,
    snapshotDate: row.snapshot_date,
    status: row.status,
    rowCount: Number(row.row_count),
    insertedCount: Number(row.inserted_count),
    updatedCount: Number(row.updated_count),
    excludedCount: Number(row.excluded_count),
    warningCount: Number(row.warning_count),
    warnings: parseJson(row.warnings_json, []),
    totals: parseJson(row.totals_json, {}),
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}

const batchColumns = `
  id, source_key, source_label, file_name, file_size_bytes, file_hash, sheet_name,
  snapshot_date, status, row_count, inserted_count, updated_count, excluded_count,
  warning_count, warnings_json, totals_json, created_at, completed_at
`;

export async function findErpReferenceBatch(
  db: ErpReferenceDatabase,
  source: ErpReferenceSourceKey,
  fileHash: string,
) {
  const row = await db.prepare(
    `SELECT ${batchColumns} FROM erp_reference_import_batches WHERE source_key = ? AND file_hash = ? LIMIT 1`,
  ).bind(source, fileHash).first<BatchRow>();
  return row ? mapBatch(row) : null;
}

export async function findErpReferenceBatchById(
  db: ErpReferenceDatabase,
  id: string,
) {
  const row = await db.prepare(
    `SELECT ${batchColumns} FROM erp_reference_import_batches WHERE id = ? LIMIT 1`,
  ).bind(id).first<BatchRow>();
  return row ? mapBatch(row) : null;
}

export async function countErpReferenceRowsOwnedByBatch(
  db: ErpReferenceDatabase,
  source: ErpReferenceSourceKey,
  batchId: string,
  snapshotDate?: string | null,
) {
  const query = source === "products"
    ? "SELECT COUNT(*) AS count FROM erp_product_master WHERE last_import_batch_id = ?"
    : source === "combos"
      ? "SELECT COUNT(*) AS count FROM erp_combo_items WHERE last_import_batch_id = ?"
      : "SELECT COUNT(*) AS count FROM erp_inventory_age_lines WHERE last_import_batch_id = ? AND snapshot_date = ?";
  const statement = db.prepare(query);
  const row = source === "inventory_age"
    ? await statement.bind(batchId, snapshotDate ?? "").first<{ count: number }>()
    : await statement.bind(batchId).first<{ count: number }>();
  return Number(row?.count ?? 0);
}

export async function listErpReferenceBatches(
  db: ErpReferenceDatabase,
  source?: ErpReferenceSourceKey,
  input: { page?: number; pageSize?: number } = {},
) {
  const page = input.page ?? 1;
  const pageSize = input.pageSize ?? 50;
  if (!Number.isSafeInteger(page) || page < 1 || page > 10_000) throw new PublicApiError(400, "invalid_request", "page 必须为 1 到 10000 的整数");
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new PublicApiError(400, "invalid_request", "pageSize 必须为 1 到 100 的整数");
  const offset = (page - 1) * pageSize;
  const [result, count] = await Promise.all([
    source
      ? db.prepare(
        `SELECT ${batchColumns} FROM erp_reference_import_batches WHERE source_key = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?`,
      ).bind(source, pageSize, offset).all<BatchRow>()
      : db.prepare(
        `SELECT ${batchColumns} FROM erp_reference_import_batches ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?`,
      ).bind(pageSize, offset).all<BatchRow>(),
    source
      ? db.prepare("SELECT COUNT(*) AS total FROM erp_reference_import_batches WHERE source_key = ?")
        .bind(source).first<{ total: number }>()
      : db.prepare("SELECT COUNT(*) AS total FROM erp_reference_import_batches").first<{ total: number }>(),
  ]);
  const items = result.results.map(mapBatch);
  const total = Number(count?.total ?? 0);
  return { items, pagination: { page, pageSize, total, returned: items.length, truncated: offset + items.length < total } };
}

export async function findLatestCompletedErpReferenceBatch(
  db: ErpReferenceDatabase,
  source: ErpReferenceSourceKey,
) {
  const row = await db.prepare(
    `SELECT ${batchColumns}
     FROM erp_reference_import_batches
     WHERE source_key = ? AND status = 'completed'
     ORDER BY snapshot_date DESC, completed_at DESC, created_at DESC, id DESC
     LIMIT 1`,
  ).bind(source).first<BatchRow>();
  return row ? mapBatch(row) : null;
}

async function countExistingProducts(db: ErpReferenceDatabase, rows: ProductMasterRow[]) {
  let count = 0;
  for (let offset = 0; offset < rows.length; offset += LOOKUP_CHUNK_SIZE) {
    const codes = rows.slice(offset, offset + LOOKUP_CHUNK_SIZE).map((row) => row.productCode);
    const placeholders = codes.map(() => "?").join(",");
    const result = await db.prepare(`SELECT COUNT(*) AS count FROM erp_product_master WHERE product_code IN (${placeholders})`)
      .bind(...codes).first<{ count: number }>();
    count += Number(result?.count ?? 0);
  }
  return count;
}

async function countExistingCombos(db: ErpReferenceDatabase, rows: ComboItemImportRow[]) {
  let count = 0;
  for (let offset = 0; offset < rows.length; offset += LOOKUP_CHUNK_SIZE) {
    const chunk = rows.slice(offset, offset + LOOKUP_CHUNK_SIZE);
    const clauses = chunk.map(() => "(parent_code = ? AND child_code = ?)").join(" OR ");
    const bindings = chunk.flatMap((row) => [row.parentCode, row.childCode]);
    const result = await db.prepare(`SELECT COUNT(*) AS count FROM erp_combo_items WHERE ${clauses}`)
      .bind(...bindings).first<{ count: number }>();
    count += Number(result?.count ?? 0);
  }
  return count;
}

function batchInsertStatement(
  db: ErpReferenceDatabase,
  input: {
    id: string;
    source: ErpReferenceSourceKey;
    fileName: string;
    fileSizeBytes: number;
    fileHash: string;
    sheetName: string;
    snapshotDate: string | null;
    rowCount: number;
    excludedCount: number;
    warnings: ErpReferenceIssue[];
    totals: unknown;
    reservationFence?: ImportReservationFence;
  },
) {
  return db.prepare(
    `INSERT INTO erp_reference_import_batches (
      id, source_key, source_label, file_name, file_size_bytes, file_hash, sheet_name,
      snapshot_date, status, row_count, excluded_count, warning_count, warnings_json, totals_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?)
    ON CONFLICT(source_key, file_hash) DO NOTHING`,
  ).bind(
    input.id,
    input.source,
    ERP_REFERENCE_SOURCE_LABELS[input.source],
    input.fileName,
    input.fileSizeBytes,
    input.fileHash,
    input.sheetName,
    input.snapshotDate,
    input.rowCount,
    input.excludedCount,
    input.warnings.length,
    JSON.stringify(input.warnings),
    JSON.stringify(input.totals ?? {}),
  );
}

function completeStatement(db: ErpReferenceDatabase, id: string, insertedCount: number, updatedCount: number) {
  return db.prepare(
    `UPDATE erp_reference_import_batches
     SET status = 'completed', inserted_count = ?, updated_count = ?, completed_at = CURRENT_TIMESTAMP
     WHERE id = ? AND status = 'processing'`,
  ).bind(insertedCount, updatedCount, id);
}

export async function saveProductMasterImport(
  db: ErpReferenceDatabase,
  input: {
    id: string;
    fileName: string;
    fileSizeBytes: number;
    fileHash: string;
    sheetName: string;
    rows: ProductMasterRow[];
    warnings: ErpReferenceIssue[];
    totals: unknown;
    contentHash: string;
    reservationFence?: ImportReservationFence;
  },
) {
  const existingCount = await countExistingProducts(db, input.rows);
  const contentHash = assertErpProjectionContentHash(input.contentHash);
  const totals = input.totals && typeof input.totals === "object" && !Array.isArray(input.totals)
    ? { ...input.totals, contentHash }
    : { contentHash };
  const statements = [batchInsertStatement(db, {
    ...input,
    totals,
    source: "products",
    snapshotDate: null,
    rowCount: input.rows.length,
    excludedCount: 0,
  })];
  const sql = `INSERT INTO erp_product_master (
    product_code, product_name, brand, specification, barcode, category, supplier,
    product_status, source_row_number, last_import_batch_id
  ) SELECT
    json_extract(item.value, '$.productCode'), json_extract(item.value, '$.productName'),
    json_extract(item.value, '$.brand'), json_extract(item.value, '$.specification'),
    json_extract(item.value, '$.barcode'), json_extract(item.value, '$.category'),
    json_extract(item.value, '$.supplier'), json_extract(item.value, '$.productStatus'),
    CAST(json_extract(item.value, '$.sourceRowNumber') AS INTEGER), ?
  FROM json_each(?) item
  WHERE EXISTS (SELECT 1 FROM erp_reference_import_batches WHERE id = ? AND status = 'processing')
  ON CONFLICT(product_code) DO UPDATE SET
    product_name = excluded.product_name, brand = excluded.brand,
    specification = excluded.specification, barcode = excluded.barcode,
    category = excluded.category, supplier = excluded.supplier,
    product_status = excluded.product_status, source_row_number = excluded.source_row_number,
    last_import_batch_id = excluded.last_import_batch_id, updated_at = CURRENT_TIMESTAMP`;
  for (let offset = 0; offset < input.rows.length; offset += WRITE_CHUNK_SIZE) {
    statements.push(db.prepare(sql).bind(input.id, JSON.stringify(input.rows.slice(offset, offset + WRITE_CHUNK_SIZE)), input.id));
  }
  statements.push(
    db.prepare(`DELETE FROM erp_product_master
      WHERE last_import_batch_id <> ?
        AND EXISTS (SELECT 1 FROM erp_reference_import_batches WHERE id = ? AND status = 'processing')`).bind(input.id, input.id),
    db.prepare(bumpErpProductProjectionRevisionSql).bind(
      input.id,
      input.rows.length,
      contentHash,
      input.id,
      input.rows.length,
      contentHash,
    ),
    db.prepare(insertErpReferenceProjectionOutboxEventSql).bind(
      ERP_PRODUCT_PROJECTION_SCOPE_JSON,
      ERP_REFERENCE_PROJECTION_CANONICAL_FORMAT_VERSION,
      input.id,
    ),
    completeStatement(db, input.id, input.rows.length - existingCount, existingCount),
  );
  if (input.reservationFence) statements.push(importReservationCommitFence(db, input.reservationFence));
  let results;
  try {
    results = await db.batch(statements);
  } catch (error) {
    if (input.reservationFence) return rethrowImportPublishError(db, input.reservationFence, error);
    throw error;
  }
  const created = Number(results[0]?.meta?.changes ?? 0) === 1;
  const batch = await findErpReferenceBatch(db, "products", input.fileHash);
  if (!batch) throw new Error("货品导入批次写入后无法读取");
  return { batch, created };
}

export async function saveInventoryAgeImport(
  db: ErpReferenceDatabase,
  input: {
    id: string;
    fileName: string;
    fileSizeBytes: number;
    fileHash: string;
    sheetName: string;
    snapshotDate: string;
    rows: InventoryAgeImportRow[];
    excludedCount: number;
    warnings: ErpReferenceIssue[];
    totals: unknown;
    reservationFence?: ImportReservationFence;
  },
) {
  const existing = await db.prepare(
    "SELECT COUNT(*) AS count FROM erp_inventory_age_lines WHERE snapshot_date = ?",
  ).bind(input.snapshotDate).first<{ count: number }>();
  const existingCount = Number(existing?.count ?? 0);
  const statements = [
    batchInsertStatement(db, { ...input, source: "inventory_age", rowCount: input.rows.length + input.excludedCount }),
    db.prepare(`DELETE FROM erp_inventory_age_lines
      WHERE snapshot_date = ?
        AND EXISTS (SELECT 1 FROM erp_reference_import_batches WHERE id = ? AND status = 'processing')`).bind(input.snapshotDate, input.id),
  ];
  const sql = `INSERT INTO erp_inventory_age_lines (
    snapshot_date, warehouse, warehouse_type, product_code, product_name, specification,
    category, available_quantity, inventory_age_days, sales_7d_quantity, sales_30d_quantity,
    unit_cost_cents, stock_value_cents, source_row_number, last_import_batch_id
  ) SELECT
    ?, json_extract(item.value, '$.warehouse'), json_extract(item.value, '$.warehouseType'),
    json_extract(item.value, '$.productCode'), json_extract(item.value, '$.productName'),
    json_extract(item.value, '$.specification'), json_extract(item.value, '$.category'),
    CAST(json_extract(item.value, '$.availableQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.inventoryAgeDays') AS INTEGER),
    CAST(json_extract(item.value, '$.sales7dQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.sales30dQuantity') AS INTEGER),
    CAST(json_extract(item.value, '$.unitCostCents') AS INTEGER),
    CAST(json_extract(item.value, '$.stockValueCents') AS INTEGER),
    CAST(json_extract(item.value, '$.sourceRowNumber') AS INTEGER), ?
  FROM json_each(?) item
  WHERE EXISTS (SELECT 1 FROM erp_reference_import_batches WHERE id = ? AND status = 'processing')`;
  for (let offset = 0; offset < input.rows.length; offset += WRITE_CHUNK_SIZE) {
    statements.push(db.prepare(sql).bind(input.snapshotDate, input.id, JSON.stringify(input.rows.slice(offset, offset + WRITE_CHUNK_SIZE)), input.id));
  }
  const updatedCount = Math.min(existingCount, input.rows.length);
  statements.push(completeStatement(db, input.id, input.rows.length - updatedCount, updatedCount));
  if (input.reservationFence) statements.push(importReservationCommitFence(db, input.reservationFence));
  let results;
  try {
    results = await db.batch(statements);
  } catch (error) {
    if (input.reservationFence) return rethrowImportPublishError(db, input.reservationFence, error);
    throw error;
  }
  const created = Number(results[0]?.meta?.changes ?? 0) === 1;
  const batch = await findErpReferenceBatch(db, "inventory_age", input.fileHash);
  if (!batch) throw new Error("库龄导入批次写入后无法读取");
  return { batch, created };
}

export async function saveComboImport(
  db: ErpReferenceDatabase,
  input: {
    id: string;
    fileName: string;
    fileSizeBytes: number;
    fileHash: string;
    sheetName: string;
    rows: ComboItemImportRow[];
    warnings: ErpReferenceIssue[];
    totals: unknown;
    reservationFence?: ImportReservationFence;
  },
) {
  const existingCount = await countExistingCombos(db, input.rows);
  const statements = [batchInsertStatement(db, { ...input, source: "combos", snapshotDate: null, rowCount: input.rows.length, excludedCount: 0 })];
  const sql = `INSERT INTO erp_combo_items (
    parent_code, parent_name, child_code, child_name, child_quantity_milli,
    source_row_number, last_import_batch_id
  ) SELECT
    json_extract(item.value, '$.parentCode'), json_extract(item.value, '$.parentName'),
    json_extract(item.value, '$.childCode'), json_extract(item.value, '$.childName'),
    CAST(json_extract(item.value, '$.childQuantityMilli') AS INTEGER),
    CAST(json_extract(item.value, '$.sourceRowNumber') AS INTEGER), ?
  FROM json_each(?) item
  WHERE EXISTS (SELECT 1 FROM erp_reference_import_batches WHERE id = ? AND status = 'processing')
  ON CONFLICT(parent_code, child_code) DO UPDATE SET
    parent_name = excluded.parent_name, child_name = excluded.child_name,
    child_quantity_milli = excluded.child_quantity_milli,
    source_row_number = excluded.source_row_number,
    last_import_batch_id = excluded.last_import_batch_id, updated_at = CURRENT_TIMESTAMP`;
  for (let offset = 0; offset < input.rows.length; offset += WRITE_CHUNK_SIZE) {
    statements.push(db.prepare(sql).bind(input.id, JSON.stringify(input.rows.slice(offset, offset + WRITE_CHUNK_SIZE)), input.id));
  }
  statements.push(
    db.prepare(`DELETE FROM erp_combo_items
      WHERE last_import_batch_id <> ?
        AND EXISTS (SELECT 1 FROM erp_reference_import_batches WHERE id = ? AND status = 'processing')`).bind(input.id, input.id),
    completeStatement(db, input.id, input.rows.length - existingCount, existingCount),
  );
  if (input.reservationFence) statements.push(importReservationCommitFence(db, input.reservationFence));
  let results;
  try {
    results = await db.batch(statements);
  } catch (error) {
    if (input.reservationFence) return rethrowImportPublishError(db, input.reservationFence, error);
    throw error;
  }
  const created = Number(results[0]?.meta?.changes ?? 0) === 1;
  const batch = await findErpReferenceBatch(db, "combos", input.fileHash);
  if (!batch) throw new Error("组合装导入批次写入后无法读取");
  return { batch, created };
}
