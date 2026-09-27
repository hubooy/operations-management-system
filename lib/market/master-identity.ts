import type { MarketSchemaDatabase } from "@/lib/market/schema-core";

const readyByDatabase = new WeakMap<object, Promise<void>>();

export type MarketMasterIdentityKey = {
  category: string;
  scope: string;
  rankingDimension: string;
  skuCode: string;
};

function buildLatestIdentitySelectSql(sourceFilter = "") {
  return `WITH ranked AS MATERIALIZED (
    SELECT source.id, source.category, source.scope, source.ranking_dimension, source.sku_code,
      ROW_NUMBER() OVER (
        PARTITION BY source.category, source.scope, source.ranking_dimension, source.sku_code
        ORDER BY source.period_end DESC, source.period_start DESC, source.id DESC
      ) identity_rank
    FROM market_ranking_entries source ${sourceFilter}
  )
  SELECT category, scope, ranking_dimension, sku_code, id latest_entry_id
  FROM ranked WHERE identity_rank=1`;
}

function revisionControlGuardStatement(
  db: MarketSchemaDatabase,
  predicate: string,
  bindings: unknown[],
) {
  return db.prepare(`INSERT INTO market_system_kpi_cache_control
      (id, suppress_all_revision, suppress_identity_revision, owner_token, updated_at)
    SELECT CASE WHEN EXISTS (
      SELECT 1 FROM market_system_kpi_cache_control control
      WHERE control.id=1 AND ${predicate}
        AND EXISTS (SELECT 1 FROM market_system_kpi_cache_state WHERE id=1)
    ) THEN 1 ELSE 2 END, 0, 0, '', CURRENT_TIMESTAMP
    ON CONFLICT(id) DO UPDATE SET updated_at=market_system_kpi_cache_control.updated_at`)
    .bind(...bindings);
}

function revisionControlStartStatements(
  db: MarketSchemaDatabase,
  ownerToken: string,
  mode: "all" | "identity",
) {
  const column = mode === "all" ? "suppress_all_revision" : "suppress_identity_revision";
  return [
    db.prepare(`UPDATE market_system_kpi_cache_control
      SET ${column}=1, owner_token=?, updated_at=CURRENT_TIMESTAMP
      WHERE id=1 AND suppress_all_revision=0 AND suppress_identity_revision=0 AND owner_token=''`)
      .bind(ownerToken),
    revisionControlGuardStatement(db, `${column}=1 AND owner_token=?`, [ownerToken]),
  ];
}

function revisionControlFinishStatements(
  db: MarketSchemaDatabase,
  ownerToken: string,
  mode: "all" | "identity",
) {
  const column = mode === "all" ? "suppress_all_revision" : "suppress_identity_revision";
  return [
    revisionControlGuardStatement(db, `${column}=1 AND owner_token=?`, [ownerToken]),
    db.prepare(`UPDATE market_system_kpi_cache_state
      SET source_revision=source_revision+1, updated_at=CURRENT_TIMESTAMP
      WHERE id=1 AND EXISTS (SELECT 1 FROM market_system_kpi_cache_control
        WHERE id=1 AND ${column}=1 AND owner_token=?)`).bind(ownerToken),
    db.prepare(`UPDATE market_system_kpi_cache_control
      SET ${column}=0, owner_token='', updated_at=CURRENT_TIMESTAMP
      WHERE id=1 AND ${column}=1 AND owner_token=?`).bind(ownerToken),
    revisionControlGuardStatement(db,
      "suppress_all_revision=0 AND suppress_identity_revision=0 AND owner_token=''", []),
  ];
}

export function marketSystemKpiBulkRevisionStartStatements(db: MarketSchemaDatabase, ownerToken: string) {
  return revisionControlStartStatements(db, ownerToken, "all");
}

export function marketSystemKpiBulkRevisionFinishStatements(db: MarketSchemaDatabase, ownerToken: string) {
  return revisionControlFinishStatements(db, ownerToken, "all");
}

function identityRefreshDmlStatements(
  db: MarketSchemaDatabase,
  affectedSql = "",
  affectedBindings: unknown[] = [],
) {
  const identityMatch = `source.category=affected.category AND source.scope=affected.scope
    AND source.ranking_dimension=affected.ranking_dimension AND source.sku_code=affected.sku_code`;
  const deleteScope = affectedSql
    ? ` WHERE (category, scope, ranking_dimension, sku_code) IN (
        SELECT category, scope, ranking_dimension, sku_code FROM (${affectedSql})
      )`
    : "";
  const sourceFilter = affectedSql ? `JOIN (${affectedSql}) affected ON ${identityMatch}` : "";
  return [
    db.prepare(`DELETE FROM market_master_identities${deleteScope}`).bind(...affectedBindings),
    db.prepare(`INSERT INTO market_master_identities
      (category, scope, ranking_dimension, sku_code, latest_entry_id, updated_at)
      SELECT category, scope, ranking_dimension, sku_code, latest_entry_id, CURRENT_TIMESTAMP
      FROM (${buildLatestIdentitySelectSql(sourceFilter)})`).bind(...affectedBindings),
  ];
}

function controlledIdentityRefreshStatements(
  db: MarketSchemaDatabase,
  affectedSql = "",
  affectedBindings: unknown[] = [],
) {
  const ownerToken = crypto.randomUUID();
  return [
    ...revisionControlStartStatements(db, ownerToken, "identity"),
    ...identityRefreshDmlStatements(db, affectedSql, affectedBindings),
    ...revisionControlFinishStatements(db, ownerToken, "identity"),
  ];
}

export function marketMasterIdentityRefreshStatements(
  db: MarketSchemaDatabase,
  batch?: { batchId: string; ownerToken: string; revisionControl: "external" },
) {
  if (batch) {
    const affectedSql = `SELECT DISTINCT
        category, scope, ranking_dimension, sku_code
      FROM market_import_identity_refresh_keys_v2 WHERE batch_id=? AND owner_token=?`;
    return identityRefreshDmlStatements(db, affectedSql, [batch.batchId, batch.ownerToken]);
  }
  return controlledIdentityRefreshStatements(db);
}

export function marketMasterIdentityRefreshKeyStatements(
  db: MarketSchemaDatabase,
  keys: readonly MarketMasterIdentityKey[],
) {
  const normalized = [...new Map(keys.map((key) => ({
    category: key.category.trim(),
    scope: key.scope.trim(),
    rankingDimension: key.rankingDimension.trim(),
    skuCode: key.skuCode.trim(),
  })).filter((key) => key.category && key.scope && key.rankingDimension && key.skuCode)
    .map((key) => [JSON.stringify(key), key])).values()];
  if (normalized.length === 0) return [];
  const affectedSql = `SELECT DISTINCT
      CAST(json_extract(value, '$.category') AS TEXT) category,
      CAST(json_extract(value, '$.scope') AS TEXT) scope,
      CAST(json_extract(value, '$.rankingDimension') AS TEXT) ranking_dimension,
      CAST(json_extract(value, '$.skuCode') AS TEXT) sku_code
    FROM json_each(?)`;
  return controlledIdentityRefreshStatements(db, affectedSql, [JSON.stringify(normalized)]);
}

export async function refreshMarketMasterIdentities(db: MarketSchemaDatabase) {
  await db.batch(marketMasterIdentityRefreshStatements(db));
}

export async function ensureMarketMasterIdentities(db: MarketSchemaDatabase) {
  const key = db as object;
  let ready = readyByDatabase.get(key);
  if (!ready) {
    ready = (async () => {
      const state = await db.prepare(`SELECT
        EXISTS(SELECT 1 FROM market_ranking_entries LIMIT 1) has_source,
        EXISTS(SELECT 1 FROM market_master_identities LIMIT 1) has_cache`).first<{ has_source: number; has_cache: number }>();
      if (Number(state?.has_source ?? 0) === 1 && Number(state?.has_cache ?? 0) === 0) {
        await refreshMarketMasterIdentities(db);
      }
    })();
    readyByDatabase.set(key, ready);
    ready.catch(() => readyByDatabase.delete(key));
  }
  await ready;
}
