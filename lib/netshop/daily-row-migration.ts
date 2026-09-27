import { dailyRowKey } from "@/lib/netshop/daily-contract";
import { PublicApiError } from "@/lib/http/api-error";

type MigrationStatement = {
  bind(...values: unknown[]): MigrationStatement;
  first<T = unknown>(): Promise<T | null>;
  all<T = unknown>(): Promise<{ results?: T[] }>;
  run(): Promise<unknown>;
};

export type DailyRowMigrationDatabase = {
  prepare(sql: string): MigrationStatement;
  batch(statements: MigrationStatement[]): Promise<unknown>;
};

type DailyMigrationRow = {
  id: number;
  source_row_key: string;
  dataset: string;
  platform: string;
  shop_name: string;
  business_date: string | null;
  sku_id: string;
  spu_id: string;
};

export const DAILY_ROW_NATURAL_KEY_MIGRATION = "jd-daily-natural-key-v1";
export const DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE = 100;
export const DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES = 4;
export const DAILY_ROW_NATURAL_IDENTITY_INDEX_NAME = "netshop_rows_daily_natural_identity_idx";
export const DAILY_ROW_NATURAL_IDENTITY_INDEX_SQL = `CREATE INDEX IF NOT EXISTS ${DAILY_ROW_NATURAL_IDENTITY_INDEX_NAME}
  ON netshop_rows (
    dataset, platform, shop_name, business_date,
    (CASE WHEN dataset = 'sku_daily' THEN sku_id ELSE spu_id END),
    last_import_batch_id, id
  )
  WHERE source = 'jd_sku_daily'
    AND dataset IN ('sku_daily', 'spu_daily')
    AND business_date IS NOT NULL
    AND business_date <> ''
    AND ((dataset = 'sku_daily' AND sku_id <> '') OR (dataset = 'spu_daily' AND spu_id <> ''))`;

export class NetshopSchemaUpgradePendingError extends PublicApiError {
  constructor() {
    super(503, "service_unavailable", "网店数据正在升级，请稍后重试");
    this.name = "NetshopSchemaUpgradePendingError";
  }
}

function dailyNaturalKey(row: DailyMigrationRow) {
  const dimensionId = row.dataset === "sku_daily" ? row.sku_id : row.spu_id;
  return dailyRowKey(row.dataset, row.platform, row.shop_name, row.business_date ?? "", dimensionId);
}

function eligibleDailyRowsSql(selectSql: string) {
  return `${selectSql}
    FROM netshop_rows r
    JOIN netshop_import_batches b ON b.id = r.last_import_batch_id
    WHERE r.source = 'jd_sku_daily'
      AND r.dataset IN ('sku_daily', 'spu_daily')
      AND b.status = 'completed'
      AND r.business_date IS NOT NULL
      AND r.business_date <> ''
      AND ((r.dataset = 'sku_daily' AND r.sku_id <> '') OR (r.dataset = 'spu_daily' AND r.spu_id <> ''))`;
}

const dailyDimensionIdSql = (alias: string) =>
  `CASE WHEN ${alias}.dataset = 'sku_daily' THEN ${alias}.sku_id ELSE ${alias}.spu_id END`;

export const DAILY_ROW_NATURAL_KEY_LOSER_PROBE_SQL = `SELECT r.id
  FROM netshop_rows r
  JOIN netshop_import_batches b ON b.id = r.last_import_batch_id
  WHERE r.source = 'jd_sku_daily'
    AND r.dataset IN ('sku_daily', 'spu_daily')
    AND b.status = 'completed'
    AND r.business_date IS NOT NULL
    AND r.business_date <> ''
    AND ((r.dataset = 'sku_daily' AND r.sku_id <> '') OR (r.dataset = 'spu_daily' AND r.spu_id <> ''))
    AND EXISTS (
      SELECT 1
      FROM netshop_rows newer
      JOIN netshop_import_batches newer_batch ON newer_batch.id = newer.last_import_batch_id
      WHERE newer.source = 'jd_sku_daily'
        AND newer.dataset IN ('sku_daily', 'spu_daily')
        AND newer.business_date IS NOT NULL
        AND newer.business_date <> ''
        AND ((newer.dataset = 'sku_daily' AND newer.sku_id <> '') OR (newer.dataset = 'spu_daily' AND newer.spu_id <> ''))
        AND newer.dataset = r.dataset
        AND newer.platform = r.platform
        AND newer.shop_name = r.shop_name
        AND newer.business_date = r.business_date
        AND ${dailyDimensionIdSql("newer")} = ${dailyDimensionIdSql("r")}
        AND newer_batch.status = 'completed'
        AND (
          COALESCE(newer_batch.completed_at, '') > COALESCE(b.completed_at, '')
          OR (COALESCE(newer_batch.completed_at, '') = COALESCE(b.completed_at, '') AND newer_batch.created_at > b.created_at)
          OR (COALESCE(newer_batch.completed_at, '') = COALESCE(b.completed_at, '') AND newer_batch.created_at = b.created_at AND newer.id > r.id)
        )
    )
  ORDER BY r.id
  LIMIT ?`;

async function markMigrationComplete(db: DailyRowMigrationDatabase) {
  await db.prepare(`INSERT OR IGNORE INTO netshop_schema_migrations (migration_key)
    VALUES (?)`).bind(DAILY_ROW_NATURAL_KEY_MIGRATION).run();
}

/**
 * Historical daily imports used file hash + row number as their identity.
 * This migration is durable and restart-safe: already-migrated databases take
 * a marker fast path. Each call mutates at most four 100-row batches; incomplete
 * work returns a typed 503 so the schema readiness cache is cleared and the next
 * request resumes from persisted rows instead of running the full history in one
 * Worker invocation.
 */
export async function ensureDailyRowNaturalKeys(db: DailyRowMigrationDatabase) {
  const marker = await db.prepare("SELECT 1 FROM netshop_schema_migrations WHERE migration_key = ? LIMIT 1")
    .bind(DAILY_ROW_NATURAL_KEY_MIGRATION)
    .first();
  if (marker) return;

  let processedBatches = 0;
  let loserPhaseComplete = false;

  // Delete globally-ranked losers across bounded invocations. No winner may be
  // updated until a fresh global probe proves this phase is empty, otherwise the
  // existing unique source_row_key index could collide with a legacy sibling.
  while (processedBatches < DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES) {
    const losers = await db.prepare(DAILY_ROW_NATURAL_KEY_LOSER_PROBE_SQL)
      .bind(DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE)
      .all<{ id: number }>();
    const ids = (losers.results ?? []).map((row) => Number(row.id)).filter(Number.isSafeInteger);
    if (ids.length === 0) {
      loserPhaseComplete = true;
      break;
    }
    await db.batch(ids.map((id) => db.prepare("DELETE FROM netshop_rows WHERE id = ?").bind(id)));
    processedBatches += 1;
  }
  if (!loserPhaseComplete) {
    const remainingLoser = await db.prepare(DAILY_ROW_NATURAL_KEY_LOSER_PROBE_SQL).bind(1).first<{ id: number }>();
    if (remainingLoser) throw new NetshopSchemaUpgradePendingError();
  }

  const pendingWinnersSql = eligibleDailyRowsSql(`SELECT
      r.id, r.source_row_key, r.dataset, r.platform, r.shop_name, r.business_date,
      r.sku_id, r.spu_id
    `) + ` AND r.source_row_key IS NOT json_array(
      r.dataset,
      r.platform,
      r.shop_name,
      r.business_date,
      CASE WHEN r.dataset = 'sku_daily' THEN r.sku_id ELSE r.spu_id END
    ) ORDER BY r.id LIMIT ?`;
  let winnerPhaseComplete = false;
  while (processedBatches < DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES) {
    const pending = await db.prepare(pendingWinnersSql)
      .bind(DAILY_ROW_NATURAL_KEY_MIGRATION_BATCH_SIZE)
      .all<DailyMigrationRow>();
    const rows = pending.results ?? [];
    if (rows.length === 0) {
      winnerPhaseComplete = true;
      break;
    }
    await db.batch(rows.map((row) => db.prepare(
      "UPDATE netshop_rows SET source_row_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    ).bind(dailyNaturalKey(row), row.id)));
    processedBatches += 1;
  }
  if (!winnerPhaseComplete) {
    const remainingWinner = await db.prepare(pendingWinnersSql).bind(1).first<DailyMigrationRow>();
    if (remainingWinner) throw new NetshopSchemaUpgradePendingError();
  }
  await markMigrationComplete(db);
}
