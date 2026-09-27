import { env } from "cloudflare:workers";
import { requireUnrestrictedDataScope, type AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
} from "@/lib/django/sales-consumer-reader";
import {
  importReservationCommitFence,
  rethrowImportPublishError,
  type ImportReservationFence,
} from "@/lib/imports/content-fingerprint";
import { PublicApiError } from "@/lib/http/api-error";
import type {
  FinanceImportBatch,
  FinanceImportIssue,
  FinanceLineInput,
  FinanceTarget,
  FinanceTargetInput,
  ParsedFinanceWorkbook,
} from "./types";

export const FINANCE_IMPORT_SOURCE = "月度财报 · 志高事业部";
export type FinanceDatabase = NonNullable<typeof env.DB>;

type FinanceImportBatchRow = {
  id: string;
  source: string;
  file_name: string;
  file_size_bytes: number;
  file_hash: string;
  status: string;
  row_count: number;
  inserted_count: number;
  duplicate_count: number;
  warning_count: number;
  parsed_month_count: number;
  imported_month_count: number;
  skipped_month_count: number;
  subject_count: number;
  months_json: string;
  warnings_json: string;
  created_at: string;
  completed_at: string | null;
};

type FinanceTargetRow = {
  id: string;
  period_type: FinanceTarget["periodType"];
  period_key: string;
  platform: string;
  shop_name: string;
  category: string;
  manager: string;
  sales_target_cents: number;
  profit_target_cents: number;
  small_margin_bps: number;
  inventory_cleanup_target_cents: number;
  promotion_fee_ratio_bps: number;
  stagnant_inventory_target_cents: number;
  version: number;
  created_at: string;
  updated_at: string;
};

const financeSchemaStatements = [
  `CREATE TABLE IF NOT EXISTS finance_import_batches (
    id TEXT PRIMARY KEY NOT NULL,
    source TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    parsed_month_count INTEGER NOT NULL DEFAULT 0,
    imported_month_count INTEGER NOT NULL DEFAULT 0,
    skipped_month_count INTEGER NOT NULL DEFAULT 0,
    subject_count INTEGER NOT NULL DEFAULT 0,
    months_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
  )`,
  `CREATE INDEX IF NOT EXISTS finance_import_batches_created_idx
    ON finance_import_batches (created_at)`,
  `CREATE TABLE IF NOT EXISTS finance_months (
    month TEXT PRIMARY KEY NOT NULL,
    batch_id TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    business_name TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    shop_count INTEGER NOT NULL DEFAULT 0,
    subject_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS finance_months_status_month_idx
    ON finance_months (status, month)`,
  `CREATE INDEX IF NOT EXISTS finance_months_status_batch_idx
    ON finance_months (status, batch_id)`,
  `CREATE TABLE IF NOT EXISTS finance_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    section TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_name TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    value_type TEXT NOT NULL,
    amount_cents INTEGER,
    rate_bps INTEGER,
    raw_value TEXT NOT NULL DEFAULT '',
    source_row_count INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_total INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (month, section, scope_key, subject_name)
  )`,
  `CREATE INDEX IF NOT EXISTS finance_lines_month_section_scope_idx
    ON finance_lines (month, section, scope_type, scope_name)`,
  `CREATE INDEX IF NOT EXISTS finance_lines_metric_month_idx
    ON finance_lines (metric_key, month)`,
  `CREATE INDEX IF NOT EXISTS finance_lines_subject_month_idx
    ON finance_lines (subject_name, month)`,
  `CREATE TABLE IF NOT EXISTS finance_targets (
    id TEXT PRIMARY KEY NOT NULL,
    period_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    shop_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    manager TEXT NOT NULL DEFAULT '',
    sales_target_cents INTEGER NOT NULL DEFAULT 0,
    profit_target_cents INTEGER NOT NULL DEFAULT 0,
    small_margin_bps INTEGER NOT NULL DEFAULT 0,
    inventory_cleanup_target_cents INTEGER NOT NULL DEFAULT 0,
    promotion_fee_ratio_bps INTEGER NOT NULL DEFAULT 0,
    stagnant_inventory_target_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (period_type, period_key, shop_name, category)
  )`,
  `CREATE TABLE IF NOT EXISTS finance_target_versions (
    target_id TEXT PRIMARY KEY NOT NULL REFERENCES finance_targets(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS finance_target_deletion_audits (
    audit_id TEXT PRIMARY KEY NOT NULL,
    target_id TEXT NOT NULL,
    period_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    category TEXT NOT NULL,
    actor TEXT NOT NULL,
    old_version INTEGER NOT NULL CHECK (old_version > 0),
    expected_version INTEGER NOT NULL CHECK (expected_version > 0),
    reason TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT OR IGNORE INTO finance_target_versions (target_id, version, updated_at)
    SELECT id, 1, CURRENT_TIMESTAMP FROM finance_targets`,
  `CREATE TRIGGER IF NOT EXISTS finance_target_version_insert
    AFTER INSERT ON finance_targets
    BEGIN
      INSERT OR IGNORE INTO finance_target_versions (target_id, version, updated_at)
      VALUES (NEW.id, 1, CURRENT_TIMESTAMP);
    END`,
  `CREATE TRIGGER IF NOT EXISTS finance_target_version_update
    BEFORE UPDATE ON finance_targets
    WHEN EXISTS (SELECT 1 FROM finance_target_versions WHERE target_id = OLD.id)
    BEGIN
      UPDATE finance_target_versions
      SET version = version + 1, updated_at = CURRENT_TIMESTAMP
      WHERE target_id = OLD.id;
    END`,
  `CREATE TABLE IF NOT EXISTS finance_targets_scoped (
    id TEXT PRIMARY KEY NOT NULL,
    period_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    manager TEXT NOT NULL DEFAULT '',
    sales_target_cents INTEGER NOT NULL DEFAULT 0,
    profit_target_cents INTEGER NOT NULL DEFAULT 0,
    small_margin_bps INTEGER NOT NULL DEFAULT 0,
    inventory_cleanup_target_cents INTEGER NOT NULL DEFAULT 0,
    promotion_fee_ratio_bps INTEGER NOT NULL DEFAULT 0,
    stagnant_inventory_target_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (period_type, period_key, platform, shop_name, category)
  )`,
  `CREATE TABLE IF NOT EXISTS finance_target_legacy_migrations (
    target_id TEXT PRIMARY KEY NOT NULL,
    migrated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT OR IGNORE INTO finance_targets_scoped (
      id, period_type, period_key, platform, shop_name, category, manager,
      sales_target_cents, profit_target_cents, small_margin_bps,
      inventory_cleanup_target_cents, promotion_fee_ratio_bps,
      stagnant_inventory_target_cents, created_at, updated_at
    )
    SELECT id, period_type, period_key, '', shop_name, category, manager,
      sales_target_cents, profit_target_cents, small_margin_bps,
      inventory_cleanup_target_cents, promotion_fee_ratio_bps,
      stagnant_inventory_target_cents, created_at, updated_at
    FROM finance_targets legacy
    WHERE NOT EXISTS (
      SELECT 1 FROM finance_target_legacy_migrations migration WHERE migration.target_id = legacy.id
    )`,
  `INSERT OR IGNORE INTO finance_target_legacy_migrations (target_id, migrated_at)
    SELECT id, CURRENT_TIMESTAMP FROM finance_targets`,
  `CREATE TABLE IF NOT EXISTS finance_target_scoped_versions (
    target_id TEXT PRIMARY KEY NOT NULL REFERENCES finance_targets_scoped(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS finance_target_scoped_deletion_audits (
    audit_id TEXT PRIMARY KEY NOT NULL,
    target_id TEXT NOT NULL,
    period_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    platform TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    category TEXT NOT NULL,
    actor TEXT NOT NULL,
    old_version INTEGER NOT NULL CHECK (old_version > 0),
    expected_version INTEGER NOT NULL CHECK (expected_version > 0),
    reason TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT OR IGNORE INTO finance_target_scoped_versions (target_id, version, updated_at)
    SELECT scoped.id, COALESCE(legacy_version.version, 1), CURRENT_TIMESTAMP
    FROM finance_targets_scoped scoped
    LEFT JOIN finance_target_versions legacy_version ON legacy_version.target_id = scoped.id`,
  `CREATE TRIGGER IF NOT EXISTS finance_target_scoped_version_insert
    AFTER INSERT ON finance_targets_scoped
    BEGIN
      INSERT OR IGNORE INTO finance_target_scoped_versions (target_id, version, updated_at)
      VALUES (NEW.id, 1, CURRENT_TIMESTAMP);
    END`,
  `CREATE TRIGGER IF NOT EXISTS finance_target_scoped_version_update
    BEFORE UPDATE ON finance_targets_scoped
    WHEN EXISTS (SELECT 1 FROM finance_target_scoped_versions WHERE target_id = OLD.id)
    BEGIN
      UPDATE finance_target_scoped_versions
      SET version = version + 1, updated_at = CURRENT_TIMESTAMP
      WHERE target_id = OLD.id;
    END`,
  `CREATE INDEX IF NOT EXISTS finance_targets_scoped_period_idx
    ON finance_targets_scoped (period_type, period_key)`,
  `CREATE INDEX IF NOT EXISTS finance_targets_scoped_shop_idx
    ON finance_targets_scoped (platform, shop_name, period_type, period_key)`,
  `CREATE INDEX IF NOT EXISTS finance_targets_period_idx
    ON finance_targets (period_type, period_key)`,
  `UPDATE finance_lines
   SET amount_cents = COALESCE((
     SELECT SUM(child.amount_cents)
     FROM finance_lines AS child
     WHERE child.month = finance_lines.month
       AND child.section = 'kingdee'
       AND child.scope_key = finance_lines.scope_key
       AND child.subject_name GLOB '销售费用_*'
       AND child.is_total = 0
   ), amount_cents)
   WHERE section = 'kingdee' AND subject_name = '销售费用'`,
] as const;

const batchColumns = `
  id, source, file_name, file_size_bytes, file_hash, status, row_count,
  inserted_count, duplicate_count, warning_count, parsed_month_count,
  imported_month_count, skipped_month_count, subject_count, months_json,
  warnings_json, created_at, completed_at
`;

const targetColumns = `
  id, period_type, period_key, platform, shop_name, category, manager,
  sales_target_cents, profit_target_cents, small_margin_bps,
  inventory_cleanup_target_cents, promotion_fee_ratio_bps,
  stagnant_inventory_target_cents,
  COALESCE((SELECT version FROM finance_target_scoped_versions version_state
    WHERE version_state.target_id = finance_targets_scoped.id), 1) AS version,
  created_at, updated_at
`;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export function getFinanceDatabase(): FinanceDatabase {
  if (!env.DB) throw new Error("Cloudflare D1 binding `DB` is unavailable.");
  return env.DB;
}

export async function ensureFinanceSchema(db = getFinanceDatabase()): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  // After the operator closes D1 finance writes, new Worker isolates must not
  // replay legacy backfills or no-op UPDATE statements merely to serve a read.
  // The authority migration is applied only after this schema is complete.
  const authority = await db.prepare(
    "SELECT owner FROM finance_write_authority WHERE id = 1 LIMIT 1",
  ).first<{ owner: string }>().catch(() => null);
  if (authority && authority.owner !== "d1") return;
  const setup = db.batch(financeSchemaStatements.map((statement) => db.prepare(statement)))
    .then(async () => {
      const targetColumnsResult = await db.prepare("PRAGMA table_info(finance_targets)").all<{ name: string }>();
      if (targetColumnsResult.results.some((column) => column.name === "version")) {
        await db.prepare(`UPDATE finance_target_versions
          SET version = MAX(version, COALESCE((
            SELECT legacy.version FROM finance_targets legacy WHERE legacy.id = finance_target_versions.target_id
          ), version))`).run();
      }
      await db.prepare(`UPDATE finance_target_scoped_versions
        SET version = MAX(version, COALESCE((
          SELECT legacy_version.version FROM finance_target_versions legacy_version
          WHERE legacy_version.target_id = finance_target_scoped_versions.target_id
        ), version))`).run();
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

function mapBatch(row: FinanceImportBatchRow): FinanceImportBatch {
  return {
    id: row.id,
    source: row.source,
    fileName: row.file_name,
    fileSizeBytes: Number(row.file_size_bytes),
    fileHash: row.file_hash,
    status: row.status,
    rowCount: Number(row.row_count),
    insertedCount: Number(row.inserted_count),
    duplicateCount: Number(row.duplicate_count),
    warningCount: Number(row.warning_count),
    parsedMonthCount: Number(row.parsed_month_count),
    importedMonthCount: Number(row.imported_month_count),
    skippedMonthCount: Number(row.skipped_month_count),
    subjectCount: Number(row.subject_count),
    months: parseJson<string[]>(row.months_json, []),
    warnings: parseJson<FinanceImportIssue[]>(row.warnings_json, []),
    createdAt: row.created_at,
    completedAt: row.completed_at,
  };
}

function mapTarget(row: FinanceTargetRow): FinanceTarget {
  return {
    id: row.id,
    periodType: row.period_type,
    periodKey: row.period_key,
    platform: row.platform,
    shopName: row.shop_name,
    category: row.category,
    manager: row.manager,
    salesTargetCents: Number(row.sales_target_cents),
    profitTargetCents: Number(row.profit_target_cents),
    grossMarginBps: 0,
    smallMarginBps: Number(row.small_margin_bps),
    inventoryCleanupTargetCents: Number(row.inventory_cleanup_target_cents),
    promotionFeeRatioBps: Number(row.promotion_fee_ratio_bps),
    stagnantInventoryTargetCents: Number(row.stagnant_inventory_target_cents),
    version: Number(row.version || 1),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function findFinanceImportBatchByHash(db: FinanceDatabase, fileHash: string) {
  const row = await db.prepare(`SELECT ${batchColumns} FROM finance_import_batches WHERE file_hash = ? LIMIT 1`)
    .bind(fileHash)
    .first<FinanceImportBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function findFinanceImportBatchById(db: FinanceDatabase, id: string) {
  const row = await db.prepare(
    `SELECT ${batchColumns} FROM finance_import_batches WHERE id = ? LIMIT 1`,
  ).bind(id).first<FinanceImportBatchRow>();
  return row ? mapBatch(row) : null;
}

export async function listFinanceImportBatches(
  db: FinanceDatabase,
  input: { page?: number; pageSize?: number } = {},
) {
  const page = input.page ?? 1;
  const pageSize = input.pageSize ?? 20;
  if (!Number.isSafeInteger(page) || page < 1 || page > 10_000) throw new PublicApiError(400, "invalid_request", "page 必须为 1 到 10000 的整数");
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new PublicApiError(400, "invalid_request", "pageSize 必须为 1 到 100 的整数");
  const offset = (page - 1) * pageSize;
  const [result, count] = await Promise.all([db.prepare(
    `SELECT ${batchColumns} FROM finance_import_batches
     ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?`,
  ).bind(pageSize, offset).all<FinanceImportBatchRow>(), db.prepare(
    "SELECT COUNT(*) AS total FROM finance_import_batches",
  ).first<{ total: number }>()]);
  const items = result.results.map(mapBatch);
  const total = Number(count?.total ?? 0);
  return { items, pagination: { page, pageSize, total, returned: items.length, truncated: offset + items.length < total } };
}

const insertFinanceLinesSql = `
  INSERT INTO finance_lines (
    month, section, metric_key, subject_name, scope_key, scope_type,
    scope_name, group_name, value_type, amount_cents, rate_bps, raw_value,
    source_row_count, sort_order, is_total
  )
  SELECT
    json_extract(item.value, '$.month'),
    json_extract(item.value, '$.section'),
    json_extract(item.value, '$.metricKey'),
    json_extract(item.value, '$.subjectName'),
    json_extract(item.value, '$.scopeKey'),
    json_extract(item.value, '$.scopeType'),
    json_extract(item.value, '$.scopeName'),
    json_extract(item.value, '$.groupName'),
    json_extract(item.value, '$.valueType'),
    CAST(json_extract(item.value, '$.amountCents') AS INTEGER),
    CAST(json_extract(item.value, '$.rateBps') AS INTEGER),
    json_extract(item.value, '$.rawValue'),
    CAST(json_extract(item.value, '$.sourceRowCount') AS INTEGER),
    CAST(json_extract(item.value, '$.sortOrder') AS INTEGER),
    CASE WHEN json_extract(item.value, '$.isTotal') THEN 1 ELSE 0 END
  FROM json_each(?) AS item
  WHERE 1
  ON CONFLICT(month, section, scope_key, subject_name) DO UPDATE SET
    metric_key = excluded.metric_key,
    scope_type = excluded.scope_type,
    scope_name = excluded.scope_name,
    group_name = excluded.group_name,
    value_type = excluded.value_type,
    amount_cents = excluded.amount_cents,
    rate_bps = excluded.rate_bps,
    raw_value = excluded.raw_value,
    source_row_count = excluded.source_row_count,
    sort_order = excluded.sort_order,
    is_total = excluded.is_total
`;

function lineChunks(lines: FinanceLineInput[], size = 300) {
  const chunks: FinanceLineInput[][] = [];
  for (let index = 0; index < lines.length; index += size) chunks.push(lines.slice(index, index + size));
  return chunks;
}

export async function saveFinanceImport(db: FinanceDatabase, input: {
  fileHash: string;
  fileName: string;
  fileSizeBytes: number;
  parsed: ParsedFinanceWorkbook;
  reservationFence?: ImportReservationFence;
}): Promise<{ batch: FinanceImportBatch; created: boolean; importedMonths: string[]; skippedMonths: string[] }> {
  let existingBatch = await findFinanceImportBatchByHash(db, input.fileHash);
  if (existingBatch?.status === "completed") {
    return { batch: existingBatch, created: false, importedMonths: [], skippedMonths: existingBatch.months };
  }
  if (existingBatch?.status === "processing"
    && Date.now() - Date.parse(existingBatch.createdAt) < 30 * 60 * 1000) {
    throw new Error("相同财务资料正在导入，请稍后重试");
  }
  if (existingBatch) {
    await db.prepare("DELETE FROM finance_import_batches WHERE id = ? AND status <> 'completed'")
      .bind(existingBatch.id).run();
    existingBatch = null;
  }

  const allMonths = input.parsed.months.map((item) => item.month);
  const warnings = input.parsed.warnings.slice(0, 300);
  const rowCount = input.parsed.months.reduce((sum, item) => sum + item.lines.length, 0);
  const subjectCount = new Set(input.parsed.months.flatMap((item) =>
    item.lines.filter((line) => line.section === "kingdee").map((line) => line.subjectName),
  )).size;
  const inserted = await db.prepare(
    `INSERT INTO finance_import_batches (
      id, source, file_name, file_size_bytes, file_hash, status, row_count,
      warning_count, parsed_month_count, subject_count, months_json, warnings_json
    ) VALUES (?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)
    ON CONFLICT(file_hash) DO NOTHING`,
  ).bind(
    input.fileHash,
    FINANCE_IMPORT_SOURCE,
    input.fileName,
    input.fileSizeBytes,
    input.fileHash,
    rowCount,
    warnings.length,
    input.parsed.months.length,
    subjectCount,
    JSON.stringify(allMonths),
    JSON.stringify(warnings),
  ).run();
  if (Number(inserted.meta?.changes ?? 0) === 0) {
    const raced = await findFinanceImportBatchByHash(db, input.fileHash);
    if (!raced) throw new Error("财报导入任务创建失败");
    return { batch: raced, created: false, importedMonths: [], skippedMonths: raced.months };
  }

  const publishStatements: D1PreparedStatement[] = [];
  for (const month of input.parsed.months) {
    publishStatements.push(
      db.prepare("DELETE FROM finance_lines WHERE month = ?").bind(month.month),
      db.prepare(
        `INSERT INTO finance_months (
          month, batch_id, sheet_name, business_name, source_file_name,
          status, shop_count, subject_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, 'processing', ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(month) DO UPDATE SET
          batch_id = excluded.batch_id,
          sheet_name = excluded.sheet_name,
          business_name = excluded.business_name,
          source_file_name = excluded.source_file_name,
          status = 'processing',
          shop_count = excluded.shop_count,
          subject_count = excluded.subject_count,
          imported_at = CURRENT_TIMESTAMP`,
      ).bind(
        month.month,
        input.fileHash,
        month.sheetName,
        month.businessName,
        input.fileName,
        month.shopCount,
        month.subjectCount,
      ),
      ...lineChunks(month.lines).map((chunk) =>
        db.prepare(insertFinanceLinesSql).bind(JSON.stringify(chunk)),
      ),
      db.prepare(
        `UPDATE finance_months
         SET status = 'completed', imported_at = CURRENT_TIMESTAMP
         WHERE month = ? AND batch_id = ?`,
      ).bind(month.month, input.fileHash),
    );
  }
  publishStatements.push(
    db.prepare(
      `UPDATE finance_import_batches
       SET status = 'completed', inserted_count = ?, duplicate_count = 0,
           imported_month_count = ?, skipped_month_count = 0,
           completed_at = CURRENT_TIMESTAMP
       WHERE id = ? AND status = 'processing'`,
    ).bind(rowCount, allMonths.length, input.fileHash),
  );
  if (input.reservationFence) publishStatements.push(importReservationCommitFence(db, input.reservationFence));
  try {
    await db.batch(publishStatements);
  } catch (error) {
    if (input.reservationFence) {
      try {
        await rethrowImportPublishError(db, input.reservationFence, error);
      } catch (translated) {
        if (translated instanceof PublicApiError) throw translated;
      }
    }
    const ownerGuard = input.reservationFence
      ? ` AND EXISTS (
          SELECT 1 FROM import_scope_heads
          WHERE domain = ? AND scope_key = ? AND status = 'processing'
            AND owner_token = ? AND current_batch_id = ?
        )`
      : "";
    await db.prepare(
      `UPDATE finance_import_batches
       SET status = 'failed', inserted_count = 0, imported_month_count = 0,
           skipped_month_count = 0, completed_at = CURRENT_TIMESTAMP
       WHERE id = ? AND status = 'processing'${ownerGuard}`,
    ).bind(
      input.fileHash,
      ...(input.reservationFence
        ? [
          input.reservationFence.domain,
          input.reservationFence.scopeKey,
          input.reservationFence.attemptId,
          input.reservationFence.batchId,
        ]
        : []),
    ).run().catch(() => undefined);
    throw error;
  }

  const batch = await findFinanceImportBatchByHash(db, input.fileHash);
  if (!batch || batch.status !== "completed" || batch.importedMonthCount !== allMonths.length) {
    throw new Error("财报导入完成后批次回查不一致");
  }
  return { batch, created: true, importedMonths: allMonths, skippedMonths: [] };
}

export async function listFinanceTargets(
  db: FinanceDatabase,
  input: { page?: number; pageSize?: number } = {},
) {
  const page = input.page ?? 1;
  const pageSize = input.pageSize ?? 50;
  if (!Number.isSafeInteger(page) || page < 1 || page > 10_000) {
    throw new PublicApiError(400, "invalid_request", "page 必须为 1 到 10000 的整数");
  }
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) {
    throw new PublicApiError(400, "invalid_request", "pageSize 必须为 1 到 100 的整数");
  }
  const [result, count] = await Promise.all([
    db.prepare(
      `SELECT ${targetColumns} FROM finance_targets_scoped
       ORDER BY CASE period_type WHEN 'month' THEN 1 WHEN 'year' THEN 2 ELSE 3 END,
                period_key DESC, platform, shop_name, category
       LIMIT ? OFFSET ?`,
    ).bind(pageSize, (page - 1) * pageSize).all<FinanceTargetRow>(),
    db.prepare("SELECT COUNT(*) AS total FROM finance_targets_scoped").first<{ total: number }>(),
  ]);
  const items = result.results.map(mapTarget);
  const total = Number(count?.total ?? 0);
  return {
    items,
    pagination: {
      page,
      pageSize,
      total,
      returned: items.length,
      truncated: (page - 1) * pageSize + items.length < total,
    },
  };
}

export async function upsertFinanceTarget(db: FinanceDatabase, input: FinanceTargetInput & { id: string }) {
  const platform = input.platform?.trim() ?? "";
  const shopName = input.shopName?.trim() ?? "";
  const category = input.category?.trim() ?? "";
  if (input.periodType !== "project" && !platform) {
    throw new PublicApiError(400, "invalid_request", "月度或年度目标必须绑定平台与店铺复合身份");
  }
  const values = [
    input.periodType,
    input.periodKey,
    platform,
    shopName,
    category,
    input.manager?.trim() ?? "",
    Math.trunc(input.salesTargetCents ?? 0),
    Math.trunc(input.profitTargetCents ?? 0),
    Math.trunc(input.smallMarginBps ?? 0),
    Math.trunc(input.inventoryCleanupTargetCents ?? 0),
    Math.trunc(input.promotionFeeRatioBps ?? 0),
    Math.trunc(input.stagnantInventoryTargetCents ?? 0),
  ] as const;
  const existingById = await db.prepare(`SELECT id FROM finance_targets_scoped WHERE id = ? LIMIT 1`)
    .bind(input.id)
    .first<{ id: string }>();
  if (existingById) {
    if (!Number.isSafeInteger(input.expectedVersion) || Number(input.expectedVersion) < 1) {
      throw new PublicApiError(400, "invalid_request", "编辑经营目标必须提供有效的 expectedVersion");
    }
    let updated: FinanceTargetRow | null;
    try {
      updated = await db.prepare(
        `UPDATE finance_targets_scoped SET
          period_type = ?, period_key = ?, platform = ?, shop_name = ?, category = ?, manager = ?,
          sales_target_cents = ?, profit_target_cents = ?, small_margin_bps = ?,
          inventory_cleanup_target_cents = ?, promotion_fee_ratio_bps = ?,
          stagnant_inventory_target_cents = ?,
          updated_at = CURRENT_TIMESTAMP
         WHERE id = ?
           AND EXISTS (
             SELECT 1 FROM finance_target_scoped_versions version_state
             WHERE version_state.target_id = finance_targets_scoped.id
               AND version_state.version = ?
           )
         RETURNING ${targetColumns}`,
      ).bind(...values, input.id, input.expectedVersion).first<FinanceTargetRow>();
    } catch (error) {
      if (error instanceof Error && /unique constraint|constraint failed/i.test(error.message)) {
        throw new PublicApiError(409, "version_conflict", "同周期、平台、店铺和品类的经营目标已存在，请刷新后编辑");
      }
      throw error;
    }
    if (!updated) {
      const current = await db.prepare(`SELECT version_state.version
        FROM finance_targets_scoped target
        LEFT JOIN finance_target_scoped_versions version_state ON version_state.target_id = target.id
        WHERE target.id = ? LIMIT 1`).bind(input.id).first<{ version: number | null }>();
      if (!current) throw new PublicApiError(404, "not_found", "经营目标不存在或已被删除");
      throw new PublicApiError(409, "version_conflict", "经营目标已被其他人更新，请刷新后重试");
    }
    return mapTarget(updated);
  }
  if (input.expectedVersion !== undefined) {
    throw new PublicApiError(404, "not_found", "经营目标不存在或已被删除");
  }
  let inserted: FinanceTargetRow | null;
  try {
    inserted = await db.prepare(
      `INSERT INTO finance_targets_scoped (
        id, period_type, period_key, platform, shop_name, category, manager,
        sales_target_cents, profit_target_cents, small_margin_bps,
        inventory_cleanup_target_cents, promotion_fee_ratio_bps,
        stagnant_inventory_target_cents
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(period_type, period_key, platform, shop_name, category) DO NOTHING
      RETURNING ${targetColumns}`,
    ).bind(
      input.id,
      ...values,
    ).first<FinanceTargetRow>();
  } catch (error) {
    if (error instanceof Error && /unique constraint|constraint failed/i.test(error.message)) {
      throw new PublicApiError(409, "version_conflict", "经营目标 ID 或同周期、平台、店铺和品类已存在，请刷新后编辑");
    }
    throw error;
  }
  if (!inserted) throw new PublicApiError(409, "version_conflict", "同周期、平台、店铺和品类的经营目标已存在，请刷新后编辑");
  return mapTarget(inserted);
}

export async function deleteFinanceTarget(db: FinanceDatabase, id: string, expectedVersion: number, actor: string, reason: string) {
  if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 1) {
    throw new PublicApiError(400, "invalid_request", "删除经营目标必须提供有效的 expectedVersion");
  }
  const normalizedActor = actor.trim().toLowerCase();
  const normalizedReason = reason.trim();
  if (!normalizedActor || normalizedActor.length > 254) throw new PublicApiError(400, "invalid_request", "删除操作缺少有效执行人");
  if (!normalizedReason || normalizedReason.length > 200) throw new PublicApiError(400, "invalid_request", "删除原因必须为 1 到 200 字");
  const auditId = crypto.randomUUID();
  const [auditResult, deleteResult] = await db.batch([
    db.prepare(`INSERT INTO finance_target_scoped_deletion_audits (
        audit_id, target_id, period_type, period_key, platform, shop_name, category,
        actor, old_version, expected_version, reason
      )
      SELECT ?, target.id, target.period_type, target.period_key, target.platform, target.shop_name, target.category,
        ?, version_state.version, ?, ?
      FROM finance_targets_scoped target
      JOIN finance_target_scoped_versions version_state ON version_state.target_id = target.id
      WHERE target.id = ? AND version_state.version = ?`)
      .bind(auditId, normalizedActor, expectedVersion, normalizedReason, id, expectedVersion),
    db.prepare(`DELETE FROM finance_targets_scoped
      WHERE id = ? AND EXISTS (
        SELECT 1 FROM finance_target_scoped_deletion_audits audit
        WHERE audit.audit_id = ? AND audit.target_id = finance_targets_scoped.id
          AND audit.expected_version = ?
      )`).bind(id, auditId, expectedVersion),
    db.prepare(`UPDATE finance_target_scoped_deletion_audits
      SET old_version = CASE WHEN changes() = 1 THEN old_version ELSE 0 END
      WHERE audit_id = ?`).bind(auditId),
  ]);
  if (Number(auditResult.meta?.changes ?? 0) === 1 && Number(deleteResult.meta?.changes ?? 0) === 1) return { deleted: true, auditId };
  const current = await db.prepare(`SELECT version_state.version
    FROM finance_targets_scoped target
    LEFT JOIN finance_target_scoped_versions version_state ON version_state.target_id = target.id
    WHERE target.id = ? LIMIT 1`).bind(id).first<{ version: number | null }>();
  if (!current) throw new PublicApiError(404, "not_found", "经营目标不存在或已被删除");
  throw new PublicApiError(409, "version_conflict", "经营目标已被其他人更新，请刷新后重试");
}

export async function getFinanceTargetOptions(
  db: FinanceDatabase,
  principal: AppPrincipal,
  options: { salesReader?: SalesConsumerReader; signal?: AbortSignal } = {},
) {
  requireUnrestrictedDataScope(principal, "经营目标");
  const salesReader = options.salesReader ?? createDjangoSalesConsumerReader();
  const [shopResult, shopCount, salesCategories] = await Promise.all([
    db.prepare(
      `SELECT DISTINCT COALESCE(NULLIF(group_name, ''), '未分组') AS platform, scope_name AS name
       FROM finance_lines
       WHERE scope_type = 'shop' AND TRIM(scope_name) <> ''
       ORDER BY platform, name LIMIT 300`,
    ).all<{ platform: string; name: string }>(),
    db.prepare(`SELECT COUNT(*) AS total FROM (
      SELECT COALESCE(NULLIF(group_name, ''), '未分组'), scope_name
      FROM finance_lines WHERE scope_type = 'shop' AND TRIM(scope_name) <> ''
      GROUP BY COALESCE(NULLIF(group_name, ''), '未分组'), scope_name
    )`).first<{ total: number }>(),
    salesReader.read(
      principal,
      { operation: "category_options", limit: 300 },
      { signal: options.signal },
    ),
  ]);
  if (!salesCategories || typeof salesCategories.revision !== "string" || !salesCategories.revision
    || !salesCategories.data || !Array.isArray(salesCategories.data.categories)
    || typeof salesCategories.data.truncated !== "boolean"
    || salesCategories.data.categories.some((item) => typeof item !== "string" || item.length > 200)) {
    throw new PublicApiError(503, "service_unavailable", "Django 销售读取服务暂时不可用，请稍后重试。");
  }
  return {
    shops: shopResult.results.map((item) => ({
      key: JSON.stringify([item.platform, item.name]),
      platform: item.platform,
      name: item.name,
    })),
    categories: salesCategories.data.categories,
    projects: ["8系列"],
    pagination: {
      shops: {
        total: Number(shopCount?.total ?? 0),
        returned: shopResult.results.length,
        truncated: shopResult.results.length < Number(shopCount?.total ?? 0),
      },
    },
  };
}
