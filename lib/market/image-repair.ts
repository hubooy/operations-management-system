import { randomUUID } from "node:crypto";

import type { MarketDatabase } from "@/lib/market/database";
import { ensureAnnotationSchema } from "@/lib/market/annotation-schema";
import { ensureMarketSchemaCached, marketStandardSkuImagePriceInheritanceSql } from "@/lib/market/schema-core";

import { normalizeJdMarketRepairImageUrl, type MarketImageRepairMapping } from "./image-repair-contract";
export { normalizeJdMarketRepairImageUrl } from "./image-repair-contract";
export type { MarketImageRepairIdentity, MarketImageRepairCandidate, MarketImageRepairMapping } from "./image-repair-contract";

type MarketImageRepairActor = { email: string; role: string };

const MAX_REPAIR_PAGE_SIZE = 200;
const MAX_REPAIR_BATCH_SIZE = 20;

function boundedText(value: unknown, label: string, maximum: number) {
  const text = String(value ?? "").trim();
  if (!text || text.length > maximum || /[\u0000-\u001f\u007f]/.test(text)) throw new Error(`${label}无效`);
  return text;
}

export async function listMarketImageRepairCandidates(
  db: MarketDatabase,
  input: { page?: number; pageSize?: number } = {},
) {
  await Promise.all([ensureMarketSchemaCached(db), ensureAnnotationSchema(db)]);
  const pageSize = Math.max(1, Math.min(MAX_REPAIR_PAGE_SIZE, Math.trunc(input.pageSize ?? 100)));
  const page = Math.max(1, Math.min(10_000, Math.trunc(input.page ?? 1)));
  const rows = await db.prepare(`WITH pending_snapshots AS MATERIALIZED (
      SELECT snapshot.category,snapshot.scope,snapshot.ranking_dimension,snapshot.sku_code,snapshot.month,
        snapshot.image_url,cache.status image_cache_status,cache.attempt_count image_cache_attempt_count,
        COALESCE(NULLIF(snapshot.image_content_sha256,''),
          CASE WHEN cache.status='ready' THEN cache.content_sha256 ELSE '' END,'') image_content_sha256
      FROM market_price_snapshots snapshot
      LEFT JOIN market_image_cache cache ON cache.source_url=snapshot.image_url
      WHERE snapshot.confirmed_market_price_cents IS NULL
    ), pending_ai_snapshots AS MATERIALIZED (
      SELECT snapshot.* FROM pending_snapshots snapshot
      WHERE NOT EXISTS (
        SELECT 1 FROM market_annotation_items result
        WHERE result.category=snapshot.category AND result.scope=snapshot.scope
          AND result.ranking_dimension=snapshot.ranking_dimension AND result.sku_code=snapshot.sku_code
          AND result.month=snapshot.month AND result.image_content_sha256=snapshot.image_content_sha256
          AND result.status IN ('review_pending','approved','rejected','committed')
          AND (COALESCE(result.ai_segment,'')<>'' OR result.ai_image_price_cents IS NOT NULL
            OR result.ai_confidence_bps IS NOT NULL OR COALESCE(result.ai_reason,'')<>'')
      )
    ), missing_identities AS MATERIALIZED (
      SELECT snapshot.category,snapshot.scope,snapshot.ranking_dimension,snapshot.sku_code
      FROM pending_ai_snapshots snapshot
      WHERE snapshot.ranking_dimension='SKU'
      GROUP BY snapshot.category,snapshot.scope,snapshot.ranking_dimension,snapshot.sku_code
      HAVING MAX(CASE WHEN snapshot.image_content_sha256<>'' THEN 1 ELSE 0 END)=0
        AND MAX(CASE WHEN snapshot.image_url<>'' AND (
          snapshot.image_cache_status IS NULL OR snapshot.image_cache_status IN ('pending','fetching')
          OR (snapshot.image_cache_status='failed' AND snapshot.image_cache_attempt_count<3)
        ) AND EXISTS (
          SELECT 1 FROM market_ranking_entries cache_source
          WHERE cache_source.category=snapshot.category AND cache_source.scope=snapshot.scope
            AND cache_source.ranking_dimension=snapshot.ranking_dimension AND cache_source.sku_code=snapshot.sku_code
            AND cache_source.image_url=snapshot.image_url
        ) THEN 1 ELSE 0 END)=0
    )
    SELECT identity.category,identity.scope,identity.ranking_dimension,identity.sku_code,
      COALESCE((SELECT historical.image_url
        FROM market_ranking_entries historical
        JOIN market_image_cache ready_cache ON ready_cache.source_url=historical.image_url
          AND ready_cache.status='ready' AND ready_cache.content_sha256<>''
        WHERE historical.category=identity.category AND historical.scope=identity.scope
          AND historical.ranking_dimension=identity.ranking_dimension AND historical.sku_code=identity.sku_code
          AND historical.image_url<>''
        ORDER BY historical.period_end DESC,historical.period_start DESC,historical.id DESC LIMIT 1),'') reusable_image_url,
      COALESCE((SELECT historical.product_url FROM market_ranking_entries historical
        WHERE historical.category=identity.category AND historical.scope=identity.scope
          AND historical.ranking_dimension=identity.ranking_dimension AND historical.sku_code=identity.sku_code
          AND historical.product_url<>''
        ORDER BY historical.period_end DESC,historical.period_start DESC,historical.id DESC LIMIT 1),'') product_url,
      COUNT(*) OVER() total_count
    FROM missing_identities identity
    ORDER BY identity.category,identity.scope,identity.ranking_dimension,identity.sku_code
    LIMIT ? OFFSET ?`).bind(pageSize, (page - 1) * pageSize).all<{
      category: string; scope: string; ranking_dimension: "SKU"; sku_code: string;
      reusable_image_url: string; product_url: string; total_count: number;
    }>();
  const results = rows.results ?? [];
  const total = Number(results[0]?.total_count ?? 0);
  return {
    items: results.map((row) => ({
      category: row.category,
      scope: row.scope,
      rankingDimension: row.ranking_dimension,
      skuCode: row.sku_code,
      productUrl: row.product_url,
      reusableImageUrl: row.reusable_image_url,
    })),
    pagination: { page, pageSize, total, pageCount: Math.max(1, Math.ceil(total / pageSize)) },
  };
}

export async function applyMarketImageRepairs(
  db: MarketDatabase,
  input: { repairs: MarketImageRepairMapping[] },
  actor: MarketImageRepairActor,
) {
  await Promise.all([ensureMarketSchemaCached(db), ensureAnnotationSchema(db)]);
  if (!Array.isArray(input.repairs) || input.repairs.length < 1 || input.repairs.length > MAX_REPAIR_BATCH_SIZE) {
    throw new Error(`单次图片修复必须包含 1–${MAX_REPAIR_BATCH_SIZE} 个商品身份`);
  }
  const seen = new Set<string>();
  const repairs = input.repairs.map((repair) => {
    const normalized = {
      category: boundedText(repair.category, "三级类目", 120),
      scope: boundedText(repair.scope, "榜单范围", 80),
      rankingDimension: repair.rankingDimension,
      skuCode: boundedText(repair.skuCode, "商品编码", 80),
      imageUrl: normalizeJdMarketRepairImageUrl(repair.imageUrl),
    };
    if (normalized.rankingDimension !== "SKU") throw new Error("图片修复只允许 SKU 维度");
    if (!normalized.imageUrl) throw new Error("图片修复只接受受控京东主图地址");
    const key = [normalized.category, normalized.scope, normalized.rankingDimension, normalized.skuCode].join("\u0000");
    if (seen.has(key)) throw new Error("图片修复批次包含重复商品身份");
    seen.add(key);
    return normalized;
  });

  const statements = repairs.flatMap((repair) => [
    db.prepare(`UPDATE market_ranking_entries SET image_url=?,updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?
        AND (image_url='' OR NOT EXISTS (
          SELECT 1 FROM market_image_cache usable_cache
          WHERE usable_cache.source_url=market_ranking_entries.image_url
            AND usable_cache.status='ready' AND usable_cache.content_sha256<>''
        ))`)
      .bind(repair.imageUrl, repair.category, repair.scope, repair.rankingDimension, repair.skuCode),
    db.prepare(`INSERT INTO market_image_cache
        (source_url,status,attempt_count,error_code,error_message,updated_at)
      VALUES (?,'pending',0,'','',CURRENT_TIMESTAMP)
      ON CONFLICT(source_url) DO UPDATE SET
        status=CASE WHEN market_image_cache.status='ready' THEN 'ready' ELSE 'pending' END,
        attempt_count=CASE WHEN market_image_cache.status='ready' THEN market_image_cache.attempt_count ELSE 0 END,
        error_code=CASE WHEN market_image_cache.status='ready' THEN market_image_cache.error_code ELSE '' END,
        error_message=CASE WHEN market_image_cache.status='ready' THEN market_image_cache.error_message ELSE '' END,
        updated_at=CURRENT_TIMESTAMP`)
      .bind(repair.imageUrl),
    db.prepare(`UPDATE market_price_snapshots SET image_url=?,
        image_content_sha256=COALESCE((SELECT content_sha256 FROM market_image_cache
          WHERE source_url=? AND status='ready' AND content_sha256<>'' LIMIT 1),''),
        updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?
        AND confirmed_market_price_cents IS NULL AND image_content_sha256=''`)
      .bind(repair.imageUrl, repair.imageUrl, repair.category, repair.scope, repair.rankingDimension, repair.skuCode),
    db.prepare(marketStandardSkuImagePriceInheritanceSql(
      "target.category=? AND target.scope=? AND target.ranking_dimension=? AND target.sku_code=? AND target.image_url=?",
    )).bind(repair.category, repair.scope, repair.rankingDimension, repair.skuCode, repair.imageUrl),
  ]);
  statements.push(db.prepare(`INSERT INTO market_master_audit_logs
      (id,actor_email,actor_role,action,entity_type,entity_id,before_json,after_json)
    VALUES (?,?,?,'repair_market_identity_images','market_image_repair',?,'{}',?)`)
    .bind(`market-audit-${randomUUID()}`, actor.email, actor.role, `batch-${randomUUID()}`,
      JSON.stringify({ repairCount: repairs.length, categories: [...new Set(repairs.map((repair) => repair.category))].sort() })));
  const results = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
  let rankingRowsUpdated = 0;
  let snapshotsUpdated = 0;
  let inheritedPrices = 0;
  for (let index = 0; index < repairs.length; index += 1) {
    rankingRowsUpdated += Number(results[index * 4]?.meta?.changes ?? 0);
    snapshotsUpdated += Number(results[index * 4 + 2]?.meta?.changes ?? 0);
    inheritedPrices += Number(results[index * 4 + 3]?.meta?.changes ?? 0);
  }
  return { repairCount: repairs.length, rankingRowsUpdated, snapshotsUpdated, inheritedPrices };
}
