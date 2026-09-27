import {
  getInventoryDatabase,
  type InventoryDatabase,
} from "@/lib/inventory/database";
import {
  importReservationCommitFence,
  rethrowImportPublishError,
  type ImportReservationFence,
} from "@/lib/imports/content-fingerprint";
import type {
  ProductShippingRateImportRow,
  ProductShippingRateIssue,
} from "@/lib/products/shipping-rate-xlsx";

const WRITE_CHUNK_SIZE = 400;

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS product_shipping_rate_import_batches (
    id TEXT PRIMARY KEY NOT NULL,
    source TEXT NOT NULL DEFAULT 'sku_cumulative',
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    raw_file_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'processing',
    source_row_count INTEGER NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    totals_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS product_shipping_rate_batches_file_hash_uq
    ON product_shipping_rate_import_batches (file_hash)`,
  `CREATE INDEX IF NOT EXISTS product_shipping_rate_batches_completed_idx
    ON product_shipping_rate_import_batches (completed_at DESC, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS product_shipping_rates (
    product_code TEXT PRIMARY KEY NOT NULL,
    shipping_rate REAL NOT NULL,
    source_row_number INTEGER NOT NULL,
    last_import_batch_id TEXT NOT NULL
      REFERENCES product_shipping_rate_import_batches(id) ON DELETE RESTRICT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS product_shipping_rates_batch_idx
    ON product_shipping_rates (last_import_batch_id, product_code)`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export function getProductShippingRateDatabase() {
  return getInventoryDatabase();
}

export async function ensureProductShippingRateSchema(db: InventoryDatabase = getProductShippingRateDatabase()) {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(schemaStatements.map((statement) => db.prepare(statement)))
    .then(() => undefined)
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

type ProductShippingRateBatchRow = {
  id: string;
  source: string;
  file_name: string;
  file_size_bytes: number;
  file_hash: string;
  raw_file_hash: string;
  content_hash: string;
  sheet_name: string;
  actor: string;
  status: string;
  source_row_count: number;
  row_count: number;
  inserted_count: number;
  updated_count: number;
  duplicate_count: number;
  warning_count: number;
  warnings_json: string;
  totals_json: string;
  created_at: string;
  completed_at: string | null;
};

export type ProductShippingRateImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  fileHash: string;
  rawFileHash: string;
  contentHash: string;
  sheetName: string;
  actor: string;
  status: string;
  sourceRowCount: number;
  rowCount: number;
  insertedCount: number;
  updatedCount: number;
  duplicateCount: number;
  warningCount: number;
  warnings: ProductShippingRateIssue[];
  totals: Record<string, unknown>;
  createdAt: string;
  completedAt: string | null;
};

function parseJson<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function mapBatch(row: ProductShippingRateBatchRow): ProductShippingRateImportBatch {
  return {
    id: row.id,
    source: row.source,
    fileName: row.file_name,
    fileSizeBytes: Number(row.file_size_bytes),
    fileHash: row.file_hash,
    rawFileHash: row.raw_file_hash,
    contentHash: row.content_hash,
    sheetName: row.sheet_name,
    actor: row.actor,
    status: row.status,
    sourceRowCount: Number(row.source_row_count),
    rowCount: Number(row.row_count),
    insertedCount: Number(row.inserted_count),
    updatedCount: Number(row.updated_count),
    duplicateCount: Number(row.duplicate_count),
    warningCount: Number(row.warning_count),
    warnings: parseJson<ProductShippingRateIssue[]>(row.warnings_json, []),
    totals: parseJson<Record<string, unknown>>(row.totals_json, {}),
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}

const batchSelect = `SELECT id, source, file_name, file_size_bytes, file_hash, raw_file_hash,
  content_hash, sheet_name, actor, status, source_row_count, row_count, inserted_count,
  updated_count, duplicate_count, warning_count, warnings_json, totals_json, created_at, completed_at
  FROM product_shipping_rate_import_batches`;

export async function findProductShippingRateBatchById(db: InventoryDatabase, id: string) {
  const row = await db.prepare(`${batchSelect} WHERE id = ? LIMIT 1`)
    .bind(id)
    .first<ProductShippingRateBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function findLatestProductShippingRateBatch(db: InventoryDatabase) {
  const row = await db.prepare(`${batchSelect}
    WHERE status = 'completed'
    ORDER BY completed_at DESC, created_at DESC, id DESC
    LIMIT 1`).first<ProductShippingRateBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function findLatestProductShippingRateBatchId(db: InventoryDatabase) {
  const row = await db.prepare(`SELECT id FROM product_shipping_rate_import_batches
    WHERE status = 'completed'
    ORDER BY completed_at DESC, created_at DESC, id DESC
    LIMIT 1`).first<{ id: string }>();
  return row?.id ?? null;
}

export async function listProductShippingRateBatches(
  db: InventoryDatabase,
  options: { page: number; pageSize: number },
) {
  const page = Number.isSafeInteger(options.page) && options.page > 0 ? options.page : 1;
  const pageSize = Number.isSafeInteger(options.pageSize) ? Math.min(100, Math.max(1, options.pageSize)) : 50;
  const offset = (page - 1) * pageSize;
  const [countRow, rows] = await Promise.all([
    db.prepare("SELECT COUNT(*) AS count FROM product_shipping_rate_import_batches")
      .first<{ count: number }>(),
    db.prepare(`${batchSelect}
      ORDER BY created_at DESC, id DESC
      LIMIT ? OFFSET ?`).bind(pageSize, offset).all<ProductShippingRateBatchRow>(),
  ]);
  const total = Number(countRow?.count ?? 0);
  return {
    items: rows.results.map(mapBatch),
    pagination: {
      page,
      pageSize,
      total,
      returned: rows.results.length,
      truncated: offset + rows.results.length < total,
    },
  };
}

export async function countProductShippingRatesOwnedByBatch(db: InventoryDatabase, batchId: string) {
  const row = await db.prepare(`SELECT COUNT(*) AS count FROM product_shipping_rates
    WHERE last_import_batch_id = ?`).bind(batchId).first<{ count: number }>();
  return Number(row?.count ?? 0);
}

export async function readProductShippingRateOwnership(db: InventoryDatabase) {
  const result = await db.prepare(`SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
    FROM product_shipping_rates
    GROUP BY last_import_batch_id
    ORDER BY last_import_batch_id`).all<{ batch_id: string; row_count: number }>();
  return result.results.map((row) => ({ batchId: row.batch_id, rowCount: Number(row.row_count) }));
}

export async function saveProductShippingRateImport(
  db: InventoryDatabase,
  input: {
    id: string;
    fileName: string;
    fileSizeBytes: number;
    fileHash: string;
    rawFileHash: string;
    contentHash: string;
    sheetName: string;
    actor: string;
    sourceRowCount: number;
    duplicateCount: number;
    rows: ProductShippingRateImportRow[];
    warnings: ProductShippingRateIssue[];
    totals: Record<string, unknown>;
    reservationFence: ImportReservationFence;
  },
) {
  const codeJson = JSON.stringify(input.rows.map((row) => row.productCode));
  const overlap = await db.prepare(`SELECT COUNT(*) AS count
    FROM product_shipping_rates
    WHERE product_code IN (SELECT CAST(value AS TEXT) FROM json_each(?))`)
    .bind(codeJson)
    .first<{ count: number }>();
  const updatedCount = Number(overlap?.count ?? 0);
  const insertedCount = Math.max(0, input.rows.length - updatedCount);
  const statements = [
    db.prepare(`INSERT INTO product_shipping_rate_import_batches (
      id, source, file_name, file_size_bytes, file_hash, raw_file_hash, content_hash,
      sheet_name, actor, status, source_row_count, row_count, duplicate_count,
      warning_count, warnings_json, totals_json
    ) VALUES (?, 'sku_cumulative', ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO NOTHING`).bind(
      input.id,
      input.fileName,
      input.fileSizeBytes,
      input.fileHash,
      input.rawFileHash,
      input.contentHash,
      input.sheetName,
      input.actor,
      input.sourceRowCount,
      input.rows.length,
      input.duplicateCount,
      input.warnings.length,
      JSON.stringify(input.warnings),
      JSON.stringify(input.totals),
    ),
  ];
  const insertSql = `INSERT INTO product_shipping_rates (
    product_code, shipping_rate, source_row_number, last_import_batch_id
  ) SELECT
    CAST(json_extract(item.value, '$.productCode') AS TEXT),
    CAST(json_extract(item.value, '$.shippingRate') AS REAL),
    CAST(json_extract(item.value, '$.sourceRowNumber') AS INTEGER),
    ?
  FROM json_each(?) item
  WHERE EXISTS (
    SELECT 1 FROM product_shipping_rate_import_batches WHERE id = ? AND status = 'processing'
  )
  ON CONFLICT(product_code) DO UPDATE SET
    shipping_rate = excluded.shipping_rate,
    source_row_number = excluded.source_row_number,
    last_import_batch_id = excluded.last_import_batch_id,
    updated_at = CURRENT_TIMESTAMP`;
  for (let offset = 0; offset < input.rows.length; offset += WRITE_CHUNK_SIZE) {
    statements.push(
      db.prepare(insertSql).bind(input.id, JSON.stringify(input.rows.slice(offset, offset + WRITE_CHUNK_SIZE)), input.id),
    );
  }
  statements.push(
    db.prepare(`DELETE FROM product_shipping_rates
      WHERE last_import_batch_id <> ?
        AND EXISTS (
          SELECT 1 FROM product_shipping_rate_import_batches WHERE id = ? AND status = 'processing'
        )`).bind(input.id, input.id),
    db.prepare(`UPDATE product_shipping_rate_import_batches
      SET status = 'completed', inserted_count = ?, updated_count = ?, completed_at = CURRENT_TIMESTAMP
      WHERE id = ? AND status = 'processing'`).bind(insertedCount, updatedCount, input.id),
    importReservationCommitFence(db, input.reservationFence),
  );
  let results;
  try {
    results = await db.batch(statements);
  } catch (error) {
    return rethrowImportPublishError(db, input.reservationFence, error);
  }
  const created = Number(results[0]?.meta?.changes ?? 0) === 1;
  const batch = await findProductShippingRateBatchById(db, input.id);
  if (!batch) throw new Error("快递费率导入批次写入后无法读取");
  return { batch, created };
}
