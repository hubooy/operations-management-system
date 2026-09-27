import type { MarketSchemaDatabase } from "@/lib/market/schema-core";

const readyByDatabase = new WeakMap<object, Promise<void>>();

function buildGmvTotalsSelectSql(sourceFilter = "") {
  return `WITH eligible_gmv_rows AS MATERIALIZED (
    SELECT source.*,
      substr(source.period_end,1,7) gmv_month,
      CASE WHEN COALESCE(source.price_band_filter,'') IN ('','\u5168\u90e8') THEN 0 ELSE 1 END band_priority,
      MAX(CASE WHEN COALESCE(source.price_band_filter,'') IN ('','\u5168\u90e8') THEN 1 ELSE 0 END)
        OVER (PARTITION BY source.sku_code, substr(source.period_end,1,7)) month_has_basis,
      CASE
        WHEN source.period_start=source.period_end THEN 'daily'
        WHEN source.period_start=date(source.period_start,'start of month')
          AND source.period_end=date(source.period_start,'start of month','+1 month','-1 day') THEN 'monthly'
        ELSE 'rolling'
      END period_kind
    FROM market_ranking_entries source ${sourceFilter}
  ), monthly_ranked AS MATERIALIZED (
    SELECT source.*,
      ROW_NUMBER() OVER (PARTITION BY sku_code, gmv_month ORDER BY band_priority, period_end DESC, updated_at DESC, id DESC) pick_rank
    FROM eligible_gmv_rows source WHERE period_kind='monthly' AND (band_priority=0 OR month_has_basis=0)
  ), daily_ranked AS MATERIALIZED (
    SELECT source.*,
      ROW_NUMBER() OVER (PARTITION BY sku_code, gmv_month, period_end ORDER BY band_priority, updated_at DESC, id DESC) pick_rank
    FROM eligible_gmv_rows source WHERE period_kind='daily' AND (band_priority=0 OR month_has_basis=0)
  ), month_candidates AS MATERIALIZED (
    SELECT sku_code, gmv_month, gmv_cents, CAST(julianday(period_end)-julianday(period_start)+1 AS INTEGER) coverage_days, 0 source_priority
    FROM monthly_ranked WHERE pick_rank=1
    UNION ALL
    SELECT sku_code, gmv_month, SUM(gmv_cents), COUNT(*), 1
    FROM daily_ranked WHERE pick_rank=1 GROUP BY sku_code, gmv_month
  ), month_picks AS MATERIALIZED (
    SELECT source.*, ROW_NUMBER() OVER (
      PARTITION BY sku_code, gmv_month ORDER BY coverage_days DESC, source_priority, gmv_cents DESC
    ) month_pick_rank
    FROM month_candidates source
  )
  SELECT sku_code, SUM(gmv_cents) gmv_total_cents
  FROM month_picks WHERE month_pick_rank=1 GROUP BY sku_code`;
}

export function marketSkuGmvRefreshStatements(
  db: MarketSchemaDatabase,
  batch?: { batchId: string; ownerToken: string },
) {
  if (batch) {
    const affectedSkuSql = `SELECT DISTINCT sku_code
      FROM market_import_identity_refresh_keys_v2 WHERE batch_id=? AND owner_token=?`;
    return [
      db.prepare(`DELETE FROM market_sku_gmv_totals WHERE sku_code IN (${affectedSkuSql})`)
        .bind(batch.batchId, batch.ownerToken),
      db.prepare(`INSERT INTO market_sku_gmv_totals (sku_code, gmv_total_cents, updated_at)
        SELECT sku_code, gmv_total_cents, CURRENT_TIMESTAMP FROM (${buildGmvTotalsSelectSql(`WHERE source.sku_code IN (${affectedSkuSql})`)})`)
        .bind(batch.batchId, batch.ownerToken),
    ];
  }
  return [
    db.prepare("DELETE FROM market_sku_gmv_totals"),
    db.prepare(`INSERT INTO market_sku_gmv_totals (sku_code, gmv_total_cents, updated_at)
      SELECT sku_code, gmv_total_cents, CURRENT_TIMESTAMP FROM (${buildGmvTotalsSelectSql()})`),
  ];
}

export async function refreshMarketSkuGmvTotals(db: MarketSchemaDatabase) {
  await db.batch(marketSkuGmvRefreshStatements(db));
}

export async function ensureMarketSkuGmvTotals(db: MarketSchemaDatabase) {
  const key = db as object;
  let ready = readyByDatabase.get(key);
  if (!ready) {
    ready = (async () => {
      const state = await db.prepare(`SELECT
        EXISTS(SELECT 1 FROM market_ranking_entries LIMIT 1) has_source,
        EXISTS(SELECT 1 FROM market_sku_gmv_totals LIMIT 1) has_cache`).first<{ has_source: number; has_cache: number }>();
      if (Number(state?.has_source ?? 0) === 1 && Number(state?.has_cache ?? 0) === 0) {
        await refreshMarketSkuGmvTotals(db);
      }
    })();
    readyByDatabase.set(key, ready);
    ready.catch(() => readyByDatabase.delete(key));
  }
  await ready;
}
