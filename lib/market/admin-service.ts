import type { AppPrincipal } from "@/lib/auth/authorization";
import { runPromptTextCompletion } from "../../tests/legacy/market/annotation-model";
import { ensureAnnotationSchema } from "@/lib/market/annotation-schema";
import type { MarketDatabase } from "@/lib/market/database";
import { mapMarketBatch, marketBatchColumns } from "@/lib/market/import-core";
import { ensureMarketSchemaCached, officialPriceBandSql } from "@/lib/market/schema-core";
import { buildMarketAdminComparisonSql, buildMarketAdminItemTrendLiteSql } from "@/lib/market/overview-sql";
import { ensureMarketSkuGmvTotals } from "@/lib/market/gmv-total";
import { ensureMarketMasterIdentities, marketMasterIdentityRefreshKeyStatements } from "@/lib/market/master-identity";
import { marketNaturalKeySql, normalizeMarketSkuCode } from "@/lib/market/import-identity";
import { getCachedMarketMasterDatabaseFilters, getCachedMarketSystemKpis } from "@/lib/market/overview-response-cache";
import { validateMarketSystemKpiCachePayload } from "@/lib/market/cache-payload-validators";
import {
  applyManualBrandSeedToIdentity,
  listMarketBrandSeeds,
  listUnknownMarketBrands,
  matchExistingUnknownMarketBrands,
  refreshSystemMarketBrandSeeds,
  upsertManualMarketBrandSeed,
} from "@/lib/market/brand-seeds";

export type MarketPrincipal = Pick<AppPrincipal, "email" | "role">;
export type MarketDimension = "SKU" | "SPU";
export type MappingKind = "subcategory" | "brand_alias" | "brand_override" | "operation_mode";
type MarketOverviewFilters = {
  query?: string;
  categories?: string[];
  scopes?: string[];
  rankingDimensions?: string[];
  operationModes?: string[];
  brands?: string[];
  subcategories?: string[];
  priceBands?: string[];
  startDate?: string;
  endDate?: string;
};

const selfOperated = "\u81ea\u8425";
const unknownMode = "\u672a\u77e5";
const unknownPriceBand = "\u672a\u786e\u8ba4\u4ef7\u683c";
const validDimensions = new Set(["SKU", "SPU"]);
const validMappingKinds = new Set(["subcategory", "brand_alias", "brand_override", "operation_mode"]);
const validMappingStatuses = new Set(["draft", "published", "archived"]);
const validOfficialPriceTypes = new Set(["标准售价", "到手价", "券后价", "起售价", "价格区间", "最低规格价格"]);

function replaceTaxonomyLabels(body: string, renames: Array<{ source: string; target: string }>) {
  const replacements = renames.map((rename) => ({ ...rename, placeholder: `[[MARKET_TAXONOMY_${crypto.randomUUID()}]]` }));
  let replaced = body;
  for (const rename of [...replacements].sort((left, right) => right.source.length - left.source.length)) {
    replaced = replaced.split(rename.source).join(rename.placeholder);
  }
  for (const rename of replacements) replaced = replaced.split(rename.placeholder).join(rename.target);
  return replaced;
}

type CountRow = { count: number };

export async function ensureMarketAdminSchema(db: MarketDatabase) {
  await ensureMarketSchemaCached(db);
}

export async function getMarketBrandSeedWorkspace(db: MarketDatabase, input: {
  q?: string; category?: string; page?: number; pageSize?: number;
} = {}) {
  await ensureMarketAdminSchema(db);
  const [dictionary, unknown] = await Promise.all([
    listMarketBrandSeeds(db, { q: input.q }),
    listUnknownMarketBrands(db, input),
  ]);
  return { dictionary, unknown };
}

export async function refreshMarketBrandSeeds(db: MarketDatabase, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const result = await refreshSystemMarketBrandSeeds(db, actor.email);
  await audit(db, actor, "refresh_market_brand_seeds", "market_brand_seed", "system", null, result);
  return result;
}

export async function upsertMarketBrandSeed(db: MarketDatabase, input: {
  canonicalBrand: string; seedText: string; category?: string; scope?: string;
  rankingDimension?: string; skuCode?: string;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const canonicalBrand = requiredText(input.canonicalBrand, 120);
  const seedText = requiredText(input.seedText, 120);
  const saved = await upsertManualMarketBrandSeed(db, { canonicalBrand, seedText, actorEmail: actor.email });
  let appliedRows = 0;
  if (input.category && input.scope && input.rankingDimension && input.skuCode) {
    appliedRows = await applyManualBrandSeedToIdentity(db, {
      category: requiredText(input.category, 120),
      scope: requiredText(input.scope, 120),
      rankingDimension: dimension(input.rankingDimension),
      skuCode: requiredText(input.skuCode, 80),
      brand: canonicalBrand,
    });
  }
  const result = { seed: saved.after, appliedRows };
  await audit(db, actor, "upsert_market_brand_seed", "market_brand_seed", saved.id, saved.before, result);
  return result;
}

export async function matchMarketBrandSeeds(db: MarketDatabase, input: { category?: string } = {}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const category = optionalText(input.category, 120) ?? "";
  const result = await matchExistingUnknownMarketBrands(db, { category });
  await audit(db, actor, "match_market_brand_seeds", "market_ranking_entries", category || "*", null, result);
  return result;
}

type MarketMasterListInput = {
  q?: string; category?: string; categories?: string[]; rankingDimension?: string; rankingDimensions?: string[]; operationMode?: string; operationModes?: string[]; brand?: string; brands?: string[]; subcategory?: string; subcategories?: string[];
  priceStatus?: "confirmed" | "pending" | "missing"; priceStatuses?: string[]; candidatePriceSource?: "ai" | "non_ai"; candidatePriceSources?: string[];
  annotationStatus?: "committed" | "pending"; annotationStatuses?: string[]; page?: number; pageSize?: number; includeHistory?: boolean;
};

function validateMarketMasterDatabaseFiltersCachePayload(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const payload = value as { categories?: unknown; subcategories?: unknown };
  const validOptions = (options: unknown) => Array.isArray(options) && options.length <= 200 && options.every((option) => {
    if (!option || typeof option !== "object" || Array.isArray(option)) return false;
    const row = option as { value?: unknown; count?: unknown };
    return typeof row.value === "string" && row.value.length <= 120
      && Number.isSafeInteger(Number(row.count)) && Number(row.count) >= 0;
  });
  return validOptions(payload.categories) && validOptions(payload.subcategories)
    && JSON.stringify(value).length <= 64 * 1024;
}

export async function listMarketMasterData(db: MarketDatabase, input: MarketMasterListInput = {}) {
  await Promise.all([ensureMarketAdminSchema(db), ensureAnnotationSchema(db)]);
  await ensureMarketSkuGmvTotals(db);
  await ensureMarketMasterIdentities(db);
  const pageSize = integer(input.pageSize, 30, 1, 100);
  const requestedPage = integer(input.page, 1, 1, 10_000);
  const { where, values } = masterWhere(input);
  const includeHistory = Boolean(input.includeHistory);
  const rows = await db.prepare(includeHistory ? `WITH filtered AS MATERIALIZED (${masterBaseSql(true)} WHERE ${where}),
    meta AS MATERIALIZED (
      SELECT total, page_count, MIN(?, page_count) safe_page FROM (
        SELECT COUNT(*) total, MAX(1, CAST((COUNT(*) + ? - 1) / ? AS INTEGER)) page_count FROM filtered
      )
    ), paged AS MATERIALIZED (
      SELECT filtered.* FROM filtered
      ORDER BY gmv_total_cents DESC, period_end DESC,
        CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, id ASC
      LIMIT ? OFFSET (SELECT (safe_page - 1) * ? FROM meta)
    )
    SELECT paged.*, meta.total full_count, meta.page_count resolved_page_count, meta.safe_page resolved_page,
      CASE WHEN paged.id IS NULL THEN 1 ELSE 0 END pagination_sentinel
    FROM meta LEFT JOIN paged ON 1=1
    ORDER BY gmv_total_cents DESC, period_end DESC,
      CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, paged.id ASC`
    : `WITH candidates AS MATERIALIZED (
      SELECT m.id, m.period_end, m.rank, COALESCE(gt.gmv_total_cents,0) gmv_total_cents
      FROM market_master_identities identity
      JOIN market_ranking_entries m ON m.id=identity.latest_entry_id
      LEFT JOIN market_sku_gmv_totals gt ON gt.sku_code=m.sku_code
      LEFT JOIN market_price_snapshots ps ON ps.category=m.category AND ps.scope=m.scope AND ps.sku_code=m.sku_code
        AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
      LEFT JOIN market_sku_annotations a ON a.category=m.category AND a.sku_code=m.sku_code
      WHERE ${where}
    ), meta AS MATERIALIZED (
      SELECT total, page_count, MIN(?, page_count) safe_page FROM (
        SELECT COUNT(*) total, MAX(1, CAST((COUNT(*) + ? - 1) / ? AS INTEGER)) page_count FROM candidates
      )
    ), page_ids AS MATERIALIZED (
      SELECT id, period_end, rank, gmv_total_cents FROM candidates
      ORDER BY gmv_total_cents DESC, period_end DESC,
        CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, id ASC
      LIMIT ? OFFSET (SELECT (safe_page - 1) * ? FROM meta)
    )
    SELECT ${masterDetailColumnsSql()},
      meta.total full_count, meta.page_count resolved_page_count, meta.safe_page resolved_page,
      CASE WHEN page_ids.id IS NULL THEN 1 ELSE 0 END pagination_sentinel
    FROM meta
    LEFT JOIN page_ids ON 1=1
    LEFT JOIN market_ranking_entries m ON m.id=page_ids.id
    ${masterDetailJoinsSql()}
    ORDER BY page_ids.gmv_total_cents DESC, page_ids.period_end DESC,
      CASE WHEN page_ids.rank IS NULL THEN 1 ELSE 0 END, page_ids.rank, page_ids.id ASC`)
    .bind(...values, requestedPage, pageSize, pageSize, pageSize, pageSize)
    .all<Record<string, string | number | null>>();
  const resultRows = rows.results ?? [];
  const meta = resultRows[0];
  const total = Number(meta?.full_count ?? 0);
  const pageCount = Number(meta?.resolved_page_count ?? 1);
  const page = Number(meta?.resolved_page ?? 1);
  return {
    items: resultRows.filter((row) => Number(row.pagination_sentinel ?? 0) === 0).map(mapMasterRow),
    pagination: { page, pageSize, total, pageCount },
  };
}

export async function listPendingMarketPrices(db: MarketDatabase, input: {
  q?: string; category?: string; categories?: string[]; candidatePriceSource?: "ai" | "non_ai"; candidatePriceSources?: string[]; page?: number; pageSize?: number;
} = {}) {
  return listMarketMasterData(db, { ...input, priceStatus: "pending", includeHistory: true });
}

export async function getMarketMasterDatabasePrimary(db: MarketDatabase, input: MarketMasterListInput = {}) {
  return { masterData: await listMarketMasterData(db, input) };
}

export async function getMarketMasterDatabaseFilters(db: MarketDatabase, input: Pick<MarketMasterListInput, "category" | "categories"> = {}) {
  await Promise.all([ensureMarketAdminSchema(db), ensureAnnotationSchema(db)]);
  const selectedCategories = [...new Set([...(input.categories ?? []), input.category ?? ""]
    .map((value) => value.trim().slice(0, 120)).filter(Boolean))].slice(0, 50);
  const categorySql = selectedCategories.length ? ` IN (${selectedCategories.map(() => "?").join(",")})` : "";
  return getCachedMarketMasterDatabaseFilters(db, selectedCategories, async () => {
    const [categories, subcategories] = await Promise.all([
      db.prepare("SELECT category value, COUNT(DISTINCT sku_code) count FROM market_ranking_entries GROUP BY category ORDER BY count DESC, category LIMIT 200")
        .all<{ value: string; count: number }>(),
      db.prepare(`SELECT t.subcategory value, COUNT(DISTINCT m.sku_code) count
        FROM market_subcategory_taxonomy t
        LEFT JOIN market_ranking_entries m ON m.category=t.category AND m.subcategory=t.subcategory
        WHERE t.status='active'${categorySql ? ` AND t.category${categorySql}` : ""}
        GROUP BY t.subcategory ORDER BY count DESC, t.subcategory LIMIT 200`)
        .bind(...selectedCategories).all<{ value: string; count: number }>(),
    ]);
    return {
      categories: categories.results ?? [],
      subcategories: subcategories.results ?? [],
    };
  }, validateMarketMasterDatabaseFiltersCachePayload);
}

export async function getMarketMasterDatabaseSecondary(db: MarketDatabase, input: {
  categories?: string[]; candidatePriceSources?: string[]; page?: number; pageSize?: number;
} = {}) {
  await Promise.all([ensureMarketAdminSchema(db), ensureAnnotationSchema(db)]);
  const selectedCategories = [...new Set((input.categories ?? [])
    .map((value) => value.trim().slice(0, 120)).filter(Boolean))].slice(0, 50);
  const categorySql = selectedCategories.length ? ` IN (${selectedCategories.map(() => "?").join(",")})` : "";
  const [pendingPrices, imageCache, pricePrompts, statusCounts] = await Promise.all([
    listPendingMarketPrices(db, {
      categories: selectedCategories,
      candidatePriceSources: input.candidatePriceSources,
      page: input.page,
      pageSize: input.pageSize,
    }),
    db.prepare(`SELECT COUNT(*) total, SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) cached,
      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
      SUM(CASE WHEN status NOT IN ('ready','failed') THEN 1 ELSE 0 END) pending FROM market_image_cache`)
      .first<Record<string, number | null>>(),
    db.prepare(`SELECT c.category,
        COALESCE((SELECT p.id FROM market_annotation_prompt_versions p WHERE p.category=c.category AND p.status='active' ORDER BY p.version DESC LIMIT 1), '') prompt_id,
        (SELECT COUNT(*) FROM market_price_snapshots ps
          WHERE ps.category=c.category AND ps.confirmed_market_price_cents IS NULL
            AND (ps.image_content_sha256<>'' OR EXISTS (
              SELECT 1 FROM market_image_cache mic WHERE mic.source_url=ps.image_url AND mic.status='ready' AND mic.content_sha256<>''
            ))) pending_count
      FROM (SELECT DISTINCT category FROM market_ranking_entries WHERE category<>'') c ORDER BY c.category`)
      .all<Record<string, unknown>>(),
    db.prepare(`SELECT COUNT(*) total,
      SUM(CASE WHEN confirmed_market_price_cents IS NULL THEN 1 ELSE 0 END) pending_prices,
      SUM(CASE WHEN confirmed_market_price_cents IS NOT NULL THEN 1 ELSE 0 END) confirmed_prices
      FROM market_price_snapshots${categorySql ? ` WHERE category${categorySql}` : ""}`)
      .bind(...selectedCategories).first<Record<string, number | null>>(),
  ]);
  return {
    pendingPrices,
    imageCache: {
      total: Number(imageCache?.total ?? 0),
      cached: Number(imageCache?.cached ?? 0),
      failed: Number(imageCache?.failed ?? 0),
      pending: Number(imageCache?.pending ?? 0),
    },
    priceRecognition: { prompts: pricePrompts.results ?? [] },
    statusCounts: {
      total: Number(statusCounts?.total ?? 0),
      pendingPrices: Number(statusCounts?.pending_prices ?? 0),
      confirmedPrices: Number(statusCounts?.confirmed_prices ?? 0),
    },
  };
}

export async function getMarketSettingsStatus(db: MarketDatabase) {
  await ensureMarketAdminSchema(db);
  const [range, batches, imageCache] = await Promise.all([
    db.prepare(`SELECT MIN(period_start) start_date, MAX(period_end) end_date
      FROM market_ranking_entries`).first<Record<string, string | null>>(),
    db.prepare(`SELECT ${marketBatchColumns}
      FROM market_import_batches ORDER BY created_at DESC LIMIT 8`)
      .all<Parameters<typeof mapMarketBatch>[0]>(),
    db.prepare(`SELECT COUNT(*) total,
      SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) cached,
      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
      SUM(CASE WHEN status NOT IN ('ready','failed') THEN 1 ELSE 0 END) pending
      FROM market_image_cache`).first<Record<string, number | null>>(),
  ]);
  return {
    dataRange: {
      startDate: range?.start_date ?? null,
      endDate: range?.end_date ?? null,
    },
    batches: (batches.results ?? []).map((row) => mapMarketBatch(row)),
    imageCache: {
      total: Number(imageCache?.total ?? 0),
      cached: Number(imageCache?.cached ?? 0),
      failed: Number(imageCache?.failed ?? 0),
      pending: Number(imageCache?.pending ?? 0),
    },
  };
}

export async function confirmMarketPrice(db: MarketDatabase, input: {
  category: string; scope?: string; skuCode: string; rankingDimension: string; month: string; imageContentSha256?: string;
  priceCents: unknown; priceType?: string; priceLowCents?: unknown; priceHighCents?: unknown; note?: string;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const category = requiredText(input.category, 120);
  const skuCode = requiredText(input.skuCode, 80);
  const rankingDimension = dimension(input.rankingDimension);
  const month = requiredMonth(input.month);
  const priceCents = nullableInteger(input.priceCents, 0, 100_000_000);
  if (priceCents === null) throw new Error("人工确认市场定位价不能为空");
  const hash = optionalText(input.imageContentSha256, 128);
  const requestedScope = optionalText(input.scope, 120);
  const beforeRows = await db.prepare(`SELECT * FROM market_price_snapshots WHERE category=? AND sku_code=? AND ranking_dimension=? AND month=? ${requestedScope ? "AND scope=?" : ""} ORDER BY scope`)
    .bind(...[category, skuCode, rankingDimension, month, ...(requestedScope ? [requestedScope] : [])]).all<Record<string, unknown>>();
  if (!requestedScope && (beforeRows.results ?? []).length !== 1) throw new Error("scope is required when multiple snapshots match");
  const before = (beforeRows.results ?? [])[0] ?? null;
  if (!before) throw new Error("未找到对应月份价格快照");
  const storedHash = String(before.image_content_sha256 ?? "");
  if (storedHash && hash !== storedHash) throw new Error("图片哈希不匹配，不能跨图片确认价格");
  const priceType = optionalText(input.priceType, 40) ?? String(before.ai_price_type ?? "");
  if (!validOfficialPriceTypes.has(priceType)) throw new Error("正式市场定位价必须选择有效的完整售价类型");
  await db.prepare(`UPDATE market_price_snapshots
    SET confirmed_market_price_cents=?, price_low_cents=?, price_high_cents=?,
      ai_price_type=COALESCE(NULLIF(?, ''), ai_price_type), confirmation_status='confirmed',
      confirmed_by=?, confirmed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?`)
    .bind(priceCents, nullableInteger(input.priceLowCents, 0, 100_000_000), nullableInteger(input.priceHighCents, 0, 100_000_000),
      priceType, actor.email, category, String(before.scope ?? ""), skuCode, rankingDimension, month).run();
  const after = await db.prepare(`SELECT * FROM market_price_snapshots WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=? LIMIT 1`)
    .bind(category, String(before.scope ?? ""), skuCode, rankingDimension, month).first<Record<string, unknown>>();
  await audit(db, actor, "confirm_market_price", "market_price_snapshot", `${category}|${String(before.scope ?? "")}|${rankingDimension}|${skuCode}|${month}`, before, { ...after, note: optionalText(input.note, 300) ?? "" });
  return { ok: true, snapshot: after };
}

export async function updateMarketSkuMasterData(db: MarketDatabase, input: {
  originalCategory: string; category: string; scope: string; rankingDimension: string; skuCode: string; month: string;
  productName: string; brand: string; operationMode: string; subcategory: string;
  priceCents: unknown; priceType?: string;
}, actor: MarketPrincipal) {
  await Promise.all([ensureMarketAdminSchema(db), ensureAnnotationSchema(db)]);
  const originalCategory = requiredText(input.originalCategory, 120);
  const category = requiredText(input.category, 120);
  const scope = requiredText(input.scope, 120);
  const rankingDimension = dimension(input.rankingDimension);
  const skuCode = requiredText(input.skuCode, 80);
  const month = requiredMonth(input.month);
  const productName = requiredText(input.productName, 500);
  const brand = optionalText(input.brand, 120) ?? "";
  const operationMode = normalizeOperationMode(input.operationMode);
  const subcategory = optionalText(input.subcategory, 120) ?? "";
  const priceCents = nullableInteger(input.priceCents, 0, 100_000_000);
  const priceType = optionalText(input.priceType, 40) ?? "标准售价";
  if (priceCents !== null && !validOfficialPriceTypes.has(priceType)) throw new Error("确认价格必须选择有效的完整售价类型");
  if (category !== originalCategory) await ensureMarketMasterIdentities(db);
  if (subcategory) {
    const taxonomy = await db.prepare(`SELECT id FROM market_subcategory_taxonomy
      WHERE category=? AND subcategory=? AND status='active' LIMIT 1`).bind(category, subcategory).first<{ id: string }>();
    if (!taxonomy) throw new Error("细分品类不在当前三级类目的细分品类设置中");
  }

  const before = await db.prepare(`SELECT id, category, scope, ranking_dimension, sku_code, product_name, brand, operation_mode, subcategory
    FROM market_ranking_entries WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?
    ORDER BY period_end DESC, period_start DESC, id DESC LIMIT 1`)
    .bind(originalCategory, scope, rankingDimension, skuCode).first<Record<string, unknown>>();
  if (!before) throw new Error("未找到要编辑的 SKU 主数据");
  if (category !== originalCategory) {
    const otherIdentity = await db.prepare(`SELECT id FROM market_ranking_entries
      WHERE category=? AND sku_code=? AND (scope<>? OR ranking_dimension<>?) LIMIT 1`)
      .bind(originalCategory, skuCode, scope, rankingDimension).first<{ id: number }>();
    if (otherIdentity) throw new Error("同一 SKU 在原三级类目仍有其他店铺范围或榜单维度，不能只迁移其中一个身份");
    const unfinishedAnnotation = await db.prepare(`SELECT item.id FROM market_annotation_items item
      JOIN market_annotation_jobs job ON job.id=item.job_id
      WHERE job.category=? AND item.sku_code=?
        AND ((item.category=? AND item.scope=? AND item.ranking_dimension=?)
          OR item.category='' OR item.scope='' OR item.image_content_sha256='')
        AND item.status IN ('queued','claimed','inferencing','failed','review_pending','approved','rejected')
        AND job.status NOT IN ('cancelled','committed') LIMIT 1`)
      .bind(originalCategory, skuCode, originalCategory, scope, rankingDimension).first<{ id: string }>();
    if (unfinishedAnnotation) throw new Error("该 SKU 仍有未完成的 AI 标注候选，请先完成或作废相关任务后再迁移三级类目");
    const conflict = await db.prepare(`SELECT id FROM market_ranking_entries
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=? LIMIT 1`)
      .bind(category, scope, rankingDimension, skuCode).first<{ id: number }>();
    if (conflict) throw new Error("目标三级类目已经存在同一 SKU，请先合并重复数据后再修改类目");
    const annotationConflict = await db.prepare("SELECT id FROM market_sku_annotations WHERE category=? AND sku_code=? LIMIT 1")
      .bind(category, skuCode).first<{ id: string }>();
    if (annotationConflict) throw new Error("目标三级类目已经存在该 SKU 的入库标注，不能直接迁移类目");
    const priceConflict = await db.prepare(`SELECT id FROM market_price_snapshots
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=? LIMIT 1`)
      .bind(category, scope, rankingDimension, skuCode).first<{ id: string }>();
    if (priceConflict) throw new Error("目标三级类目已经存在该 SKU 的价格快照，不能直接迁移类目");
  }

  const statements = [
    db.prepare(`UPDATE market_ranking_entries SET
      category=?, product_name=?, brand=?, operation_mode=?, subcategory=?,
      updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
      .bind(category, productName, brand, operationMode, subcategory, originalCategory, scope, rankingDimension, skuCode),
    db.prepare(`UPDATE market_ranking_entries SET natural_key=${marketNaturalKeySql()}, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
      .bind(category, scope, rankingDimension, skuCode),
    db.prepare(`UPDATE market_price_snapshots SET category=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
      .bind(category, originalCategory, scope, rankingDimension, skuCode),
    db.prepare(`UPDATE market_sku_annotations SET category=?, segment=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND sku_code=?`)
      .bind(category, subcategory, originalCategory, skuCode),
    db.prepare(`UPDATE market_brand_suggestions SET category=?, product_name=?, current_brand=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
      .bind(category, productName, brand, originalCategory, scope, rankingDimension, skuCode),
    db.prepare(`UPDATE market_price_snapshots SET confirmed_market_price_cents=?, ai_price_type=?,
      confirmation_status=?, confirmed_by=?, confirmed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=? AND month=?`)
      .bind(priceCents, priceCents === null ? "" : priceType, priceCents === null ? "missing" : "confirmed",
        actor.email, category, scope, rankingDimension, skuCode, month),
  ];
  const domainStatementCount = statements.length;
  if (category !== originalCategory) {
    const guardId = `market-category-migration-guard-${crypto.randomUUID()}`;
    statements.unshift(db.prepare(`INSERT INTO market_master_audit_logs
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
      SELECT CASE WHEN EXISTS (
        SELECT 1 FROM market_annotation_items item JOIN market_annotation_jobs job ON job.id=item.job_id
        WHERE job.category=? AND item.sku_code=?
          AND ((item.category=? AND item.scope=? AND item.ranking_dimension=?)
            OR item.category='' OR item.scope='' OR item.image_content_sha256='')
          AND item.status IN ('queued','claimed','inferencing','failed','review_pending','approved','rejected')
          AND job.status NOT IN ('cancelled','committed')
      ) OR EXISTS (SELECT 1 FROM market_ranking_entries
        WHERE category=? AND sku_code=? AND (scope<>? OR ranking_dimension<>?))
      OR EXISTS (SELECT 1 FROM market_ranking_entries
        WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?)
      OR EXISTS (SELECT 1 FROM market_sku_annotations WHERE category=? AND sku_code=?)
      OR EXISTS (SELECT 1 FROM market_price_snapshots
        WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?)
      THEN NULL ELSE ? END, ?, ?, 'market_category_migration_guard', 'market_sku', ?, '{}', '{}'
    `).bind(originalCategory, skuCode, originalCategory, scope, rankingDimension,
      originalCategory, skuCode, scope, rankingDimension,
      category, scope, rankingDimension, skuCode, category, skuCode, category, scope, rankingDimension, skuCode,
      guardId, actor.email, actor.role,
      `${originalCategory}|${scope}|${rankingDimension}|${skuCode}`));
    statements.push(...(marketMasterIdentityRefreshKeyStatements(db, [
      { category: originalCategory, scope, rankingDimension, skuCode },
      { category, scope, rankingDimension, skuCode },
    ]) as unknown as typeof statements));
    statements.push(db.prepare("DELETE FROM market_master_audit_logs WHERE id=? AND action='market_category_migration_guard'").bind(guardId));
  }
  const results = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
  const after = await db.prepare(`SELECT id, category, scope, ranking_dimension, sku_code, product_name, brand, operation_mode, subcategory
    FROM market_ranking_entries WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?
    ORDER BY period_end DESC, period_start DESC, id DESC LIMIT 1`)
    .bind(category, scope, rankingDimension, skuCode).first<Record<string, unknown>>();
  const domainResults = category !== originalCategory ? results.slice(1, 1 + domainStatementCount) : results;
  const changedRows = domainResults.reduce((sum, result) => sum + Number(result?.meta?.changes ?? 0), 0);
  await audit(db, actor, "update_market_sku_master", "market_sku", `${originalCategory}|${scope}|${rankingDimension}|${skuCode}`, before, { ...after, month, priceCents, priceType, changedRows });
  return { ok: true, changedRows, item: after };
}

export async function getMarketSubcategoryWorkspace(db: MarketDatabase, category = "") {
  await Promise.all([ensureMarketAdminSchema(db), ensureAnnotationSchema(db)]);
  const normalizedCategory = optionalText(category, 120) ?? "";
  const [categories, items] = await Promise.all([
    db.prepare("SELECT category value, COUNT(DISTINCT sku_code) count FROM market_ranking_entries WHERE category<>'' GROUP BY category ORDER BY count DESC, category LIMIT 200").all<{ value: string; count: number }>(),
    normalizedCategory
      ? db.prepare(`SELECT t.subcategory,
          (SELECT COUNT(DISTINCT sku_code) FROM market_ranking_entries r WHERE r.category=t.category AND r.subcategory=t.subcategory) sku_count,
          (SELECT COUNT(*) FROM market_sku_annotations a WHERE a.category=t.category AND a.segment=t.subcategory) annotation_count,
          t.status, t.sort_order
        FROM market_subcategory_taxonomy t
        WHERE t.category=? AND t.status='active'
        ORDER BY t.sort_order, sku_count DESC, t.subcategory`)
        .bind(normalizedCategory)
        .all<Record<string, string | number>>()
      : Promise.resolve({ results: [] as Record<string, string | number>[] }),
  ]);
  return { category: normalizedCategory, categories: categories.results ?? [], items: items.results ?? [] };
}

export async function saveMarketSubcategorySettings(db: MarketDatabase, input: {
  category: string; renames?: Array<{ source: string; target: string }>; additions?: string[];
}, actor: MarketPrincipal) {
  await Promise.all([ensureMarketAdminSchema(db), ensureAnnotationSchema(db)]);
  const category = requiredText(input.category, 120);
  const requestedRenames = (input.renames ?? []).slice(0, 100).map((item) => ({ source: requiredText(item.source, 120), target: requiredText(item.target, 120) }));
  const renames = requestedRenames.filter((item) => item.source !== item.target);
  const additions = [...new Set((input.additions ?? []).map((item) => requiredText(item, 120)))].slice(0, 100);
  if (!renames.length && !additions.length) throw new Error("请至少修改或新增一个细分品类");
  const sources = renames.map((item) => item.source);
  if (new Set(sources).size !== sources.length) throw new Error("同一个细分品类不能在一次保存中重复重命名");
  if (additions.some((item) => sources.includes(item))) throw new Error("待重命名的旧细分品类不能在同一次保存中重新新增");
  const sourceSet = new Set(sources);
  if (renames.some((item) => sourceSet.has(item.target))) throw new Error("一次保存不支持链式或循环重命名，请直接填写最终细分品类名称");

  const taxonomyRows = await db.prepare(`SELECT subcategory, sort_order sortOrder FROM market_subcategory_taxonomy
    WHERE category=? AND status='active' ORDER BY sort_order, subcategory`).bind(category).all<{ subcategory: string; sortOrder: number }>();
  const currentTaxonomy = (taxonomyRows.results ?? []).map((row) => row.subcategory);
  if (sources.some((source) => !currentTaxonomy.includes(source))) throw new Error("要重命名的细分品类已不存在，请刷新后重试");
  const renameMap = new Map(renames.map((item) => [item.source, item.target]));
  const finalTaxonomy = [...new Set([...currentTaxonomy.map((value) => renameMap.get(value) ?? value), ...additions])];
  if (finalTaxonomy.length < 2) throw new Error("每个三级类目至少需要保留 2 个细分品类");
  if (finalTaxonomy.length > 80 || finalTaxonomy.some((value) => value.length > 40)) throw new Error("细分品类字典最多 80 项，且每项不能超过 40 个字符");

  if (renames.length || additions.length) {
    const activeJob = await db.prepare(`SELECT id FROM market_annotation_jobs
      WHERE category=? AND status NOT IN ('cancelled','committed') LIMIT 1`).bind(category).first<{ id: string }>();
    if (activeJob) throw new Error("仍有任务引用当前细分品类字典，请先完成或作废任务后再保存");
    const activeValidation = await db.prepare(`SELECT id FROM market_annotation_validation_runs
      WHERE category=? AND status IN ('queued','running') LIMIT 1`).bind(category).first<{ id: string }>();
    if (activeValidation) throw new Error("仍有冻结验证引用当前细分品类字典，请先等待验证完成后再保存");
  }

  const activePrompt = await db.prepare(`SELECT id, prompt_body promptBody FROM market_annotation_prompt_versions
    WHERE category=? AND status='active' ORDER BY version DESC LIMIT 1`).bind(category).first<{ id: string; promptBody: string }>();
  const lastPrompt = await db.prepare("SELECT MAX(version) version FROM market_annotation_prompt_versions WHERE category=?")
    .bind(category).first<{ version: number | null }>();
  const renameJson = JSON.stringify(renames);
  const ruleRows = renames.length ? await db.prepare(`SELECT id, source_value sourceValue FROM market_master_mapping_rules
    WHERE kind='subcategory' AND category=? AND status='published'
      AND source_value IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)
    ORDER BY version DESC, updated_at DESC`).bind(category, renameJson).all<{ id: string; sourceValue: string }>() : { results: [] };
  const existingRules = new Map<string, string>();
  for (const row of ruleRows.results ?? []) if (!existingRules.has(row.sourceValue)) existingRules.set(row.sourceValue, row.id);

  const guardId = `market-taxonomy-guard-${crypto.randomUUID()}`;
  const statements = [db.prepare(`INSERT INTO market_master_audit_logs
    (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
    SELECT CASE WHEN EXISTS (SELECT 1 FROM market_annotation_jobs WHERE category=? AND status NOT IN ('cancelled','committed'))
      OR EXISTS (SELECT 1 FROM market_annotation_validation_runs WHERE category=? AND status IN ('queued','running'))
      OR EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy WHERE taxonomy.category=? AND taxonomy.status='active'
        AND NOT EXISTS (SELECT 1 FROM json_each(?) expected WHERE CAST(expected.value AS TEXT)=taxonomy.subcategory))
      OR EXISTS (SELECT 1 FROM json_each(?) expected
        WHERE NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy
          WHERE taxonomy.category=? AND taxonomy.status='active' AND taxonomy.subcategory=CAST(expected.value AS TEXT)))
      OR (?='' AND EXISTS (SELECT 1 FROM market_annotation_prompt_versions WHERE category=? AND status='active'))
      OR (?<>'' AND NOT EXISTS (SELECT 1 FROM market_annotation_prompt_versions WHERE id=? AND category=? AND status='active'))
      THEN NULL ELSE ? END, ?, ?, 'market_subcategory_mutation_guard', 'market_subcategory_taxonomy', ?, '{}', '{}'
  `).bind(category, category, category, JSON.stringify(currentTaxonomy), JSON.stringify(currentTaxonomy), category,
    activePrompt?.id ?? "", category, activePrompt?.id ?? "", activePrompt?.id ?? "", category,
    guardId, actor.email, actor.role, category)];
  if (renames.length) {
    statements.push(
      db.prepare(`UPDATE market_ranking_entries SET subcategory=COALESCE((SELECT json_extract(mapping.value,'$.target')
          FROM json_each(?) mapping WHERE json_extract(mapping.value,'$.source')=subcategory LIMIT 1), subcategory), updated_at=CURRENT_TIMESTAMP
        WHERE category=? AND subcategory IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)`)
        .bind(renameJson, category, renameJson),
      db.prepare(`UPDATE market_sku_annotations SET segment=COALESCE((SELECT json_extract(mapping.value,'$.target')
          FROM json_each(?) mapping WHERE json_extract(mapping.value,'$.source')=segment LIMIT 1), segment), updated_at=CURRENT_TIMESTAMP
        WHERE category=? AND segment IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)`)
        .bind(renameJson, category, renameJson),
      db.prepare(`UPDATE market_annotation_validation_samples SET gold_segment=COALESCE((SELECT json_extract(mapping.value,'$.target')
          FROM json_each(?) mapping WHERE json_extract(mapping.value,'$.source')=gold_segment LIMIT 1), gold_segment)
        WHERE category=? AND gold_segment IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)`)
        .bind(renameJson, category, renameJson),
      db.prepare(`UPDATE market_subcategory_taxonomy SET status='archived', updated_by=?, updated_at=CURRENT_TIMESTAMP
        WHERE category=? AND subcategory IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)`)
        .bind(actor.email, category, renameJson),
      db.prepare(`UPDATE market_master_mapping_rules SET target_value=COALESCE((SELECT json_extract(mapping.value,'$.target')
          FROM json_each(?) mapping WHERE json_extract(mapping.value,'$.source')=target_value LIMIT 1), target_value),
        version=version+1, updated_at=CURRENT_TIMESTAMP
        WHERE kind='subcategory' AND category=? AND status='published'
          AND target_value IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)`)
        .bind(renameJson, category, renameJson),
      db.prepare(`UPDATE market_master_mapping_rules SET target_value=COALESCE((SELECT json_extract(mapping.value,'$.target')
          FROM json_each(?) mapping WHERE json_extract(mapping.value,'$.source')=source_value LIMIT 1), target_value),
        version=version+1, updated_at=CURRENT_TIMESTAMP
        WHERE kind='subcategory' AND category=? AND status='published'
          AND source_value IN (SELECT json_extract(mapping.value,'$.source') FROM json_each(?) mapping)`)
        .bind(renameJson, category, renameJson),
    );
  }
  for (const [sortOrder, subcategory] of finalTaxonomy.entries()) {
    statements.push(db.prepare(`INSERT INTO market_subcategory_taxonomy (id, category, subcategory, status, sort_order, created_by, updated_by)
      VALUES (?, ?, ?, 'active', ?, ?, ?) ON CONFLICT(category, subcategory) DO UPDATE SET
        status='active', sort_order=excluded.sort_order, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP`)
      .bind(`market-subcategory-${crypto.randomUUID()}`, category, subcategory, sortOrder, actor.email, actor.email));
  }
  for (const rename of renames) {
    if (existingRules.has(rename.source)) continue;
    statements.push(db.prepare(`INSERT INTO market_master_mapping_rules (id, kind, category, source_value, target_value, status, version, effective_from, created_by)
      VALUES (?, 'subcategory', ?, ?, ?, 'published', 1, '1970-01-01', ?)
      ON CONFLICT(id) DO UPDATE SET target_value=excluded.target_value, status='published',
        version=market_master_mapping_rules.version+1, updated_at=CURRENT_TIMESTAMP`)
      .bind(`market-subcategory-map-${crypto.randomUUID()}`, category, rename.source, rename.target, actor.email));
  }
  let successorPromptId = "";
  if (activePrompt && (renames.length || additions.length)) {
    successorPromptId = `market-prompt-${crypto.randomUUID()}`;
    const renamedBody = replaceTaxonomyLabels(activePrompt.promptBody, renames);
    const taxonomySuffix = `\n细分品类固定枚举：${finalTaxonomy.join("、")}。`;
    const promptBody = `${renamedBody.slice(0, Math.max(0, 12_000 - taxonomySuffix.length))}${taxonomySuffix}`;
    const changeSummary = [
      renames.length ? `重命名：${renames.map((item) => `${item.source}→${item.target}`).join("；")}` : "",
      additions.length ? `新增：${additions.join("、")}` : "",
    ].filter(Boolean).join("；");
    statements.push(
      db.prepare("UPDATE market_annotation_prompt_versions SET status='archived' WHERE category=? AND status='active'").bind(category),
      db.prepare(`INSERT INTO market_annotation_prompt_versions
        (id, category, version, parent_id, source, status, segments_json, prompt_body, change_note, created_by, activated_by, activated_at)
        VALUES (?, ?, ?, ?, 'taxonomy_rename', 'active', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`)
        .bind(successorPromptId, category, Number(lastPrompt?.version ?? 0) + 1, activePrompt.id, JSON.stringify(finalTaxonomy), promptBody,
          `细分品类字典变更：${changeSummary}`.slice(0, 500), actor.email, actor.email),
      db.prepare(`INSERT INTO market_annotation_prompt_audits (id, prompt_id, category, action, reason, actor)
        VALUES (?, ?, ?, 'taxonomy_rename_successor', ?, ?)`)
        .bind(`market-prompt-audit-${crypto.randomUUID()}`, successorPromptId, category, "细分品类字典重命名后自动创建不可变后继版本", actor.email),
    );
  }
  const result = { category, renamed: renames.length, added: additions.length, changedRows: 0, successorPromptId,
    summary: renames.map((item) => ({ ...item, changed: 0 })) };
  statements.push(db.prepare(`INSERT INTO market_master_audit_logs
    (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
    VALUES (?, ?, ?, 'save_market_subcategory_settings', 'market_subcategory_taxonomy', ?, ?, ?)`)
    .bind(`market-audit-${crypto.randomUUID()}`, actor.email, actor.role, category, JSON.stringify({ taxonomy: currentTaxonomy }), JSON.stringify(result)));
  statements.push(db.prepare("DELETE FROM market_master_audit_logs WHERE id=? AND action='market_subcategory_mutation_guard'").bind(guardId));
  const writes = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
  const changedRows = writes.slice(1, renames.length ? 4 : 1).reduce((sum, write) => sum + Number(write.meta?.changes ?? 0), 0);
  return { ...result, changedRows, summary: renames.map((item) => ({ ...item, changed: changedRows })) };
}

export async function listMarketMappings(db: MarketDatabase, input: { kind?: string; category?: string; status?: string } = {}) {
  await ensureMarketAdminSchema(db);
  const clauses = ["1=1"];
  const values: unknown[] = [];
  if (input.kind && validMappingKinds.has(input.kind)) { clauses.push("kind=?"); values.push(input.kind); }
  if (input.category?.trim()) { clauses.push("category=?"); values.push(input.category.trim().slice(0, 120)); }
  if (input.status?.trim()) { clauses.push("status=?"); values.push(input.status.trim().slice(0, 30)); }
  const rows = await db.prepare(`SELECT id, kind, category, source_value, target_value, status, version, effective_from, created_by, created_at, updated_at
    FROM market_master_mapping_rules WHERE ${clauses.join(" AND ")} ORDER BY kind, category, source_value LIMIT 300`).bind(...values).all<Record<string, string | number>>();
  return { items: rows.results ?? [] };
}

export async function upsertMarketMapping(db: MarketDatabase, input: {
  id?: string; kind: string; category?: string; sourceValue: string; targetValue: string; status?: string; effectiveFrom?: string;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const kind = validMappingKinds.has(input.kind) ? input.kind : "";
  if (!kind) throw new Error("映射类型无效");
  const id = optionalText(input.id, 120) ?? `market-map-${crypto.randomUUID()}`;
  const before = await db.prepare("SELECT * FROM market_master_mapping_rules WHERE id=?").bind(id).first<Record<string, unknown>>();
  const version = before ? Number(before.version ?? 1) + 1 : 1;
  const status = validMappingStatuses.has(input.status ?? "") ? String(input.status) : "published";
  const after = {
    id, kind,
    category: optionalText(input.category, 120) ?? "",
    sourceValue: requiredText(input.sourceValue, 200),
    targetValue: normalizeMappingTarget(kind, input.targetValue),
    status,
    effectiveFrom: date(input.effectiveFrom) ?? "1970-01-01",
    version,
  };
  if (after.status === "published") {
    const conflict = await db.prepare(`SELECT id FROM market_master_mapping_rules
      WHERE id<>? AND kind=? AND category=? AND source_value=? AND effective_from=? AND status='published' LIMIT 1`)
      .bind(id, after.kind, after.category, after.sourceValue, after.effectiveFrom).first<{ id: string }>();
    if (conflict) throw new Error("同一来源值和生效日期只能有一条已发布规则");
  }
  await db.prepare(`INSERT INTO market_master_mapping_rules
    (id, kind, category, source_value, target_value, status, version, effective_from, created_by, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET category=excluded.category, source_value=excluded.source_value,
      target_value=excluded.target_value, status=excluded.status, version=excluded.version,
      effective_from=excluded.effective_from, updated_at=CURRENT_TIMESTAMP`)
    .bind(after.id, after.kind, after.category, after.sourceValue, after.targetValue, after.status, after.version, after.effectiveFrom, actor.email).run();
  await audit(db, actor, "upsert_mapping", "market_mapping_rule", id, before, after);
  return after;
}

export async function applyPublishedMarketMappings(db: MarketDatabase, input: { category?: string } = {}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const category = optionalText(input.category, 120) ?? "";
  await db.prepare(`UPDATE market_ranking_entries SET
      source_brand=brand,
      source_operation_mode=operation_mode,
      source_subcategory=subcategory
    WHERE source_brand='' AND source_operation_mode='' AND source_subcategory=''
      AND (?='' OR category=?)`).bind(category, category).run();
  await db.prepare(`UPDATE market_ranking_entries SET
      brand=source_brand,
      operation_mode=source_operation_mode,
      subcategory=source_subcategory,
      updated_at=CURRENT_TIMESTAMP
    WHERE (?='' OR category=?)
      AND (brand<>source_brand OR operation_mode<>source_operation_mode OR subcategory<>source_subcategory)`).bind(category, category).run();
  const rules = await db.prepare(`SELECT id, kind, category, source_value, target_value, effective_from
    FROM market_master_mapping_rules
    WHERE status='published' AND (?='' OR category='' OR category=?)
    ORDER BY CASE WHEN category='' THEN 0 ELSE 1 END, effective_from ASC, version ASC, id`).bind(category, category).all<{
      id: string; kind: string; category: string; source_value: string; target_value: string; effective_from: string;
    }>();
  let changed = 0;
  const applied: Array<{ id: string; kind: string; changes: number }> = [];
  for (const rule of rules.results ?? []) {
    const categoryClause = rule.category ? " AND category=?" : "";
    const categoryValues = rule.category ? [rule.category] : [];
    let result: { meta?: { changes?: number } };
    if (rule.kind === "brand_alias") {
      result = await db.prepare(`UPDATE market_ranking_entries SET brand=?, updated_at=CURRENT_TIMESTAMP
        WHERE source_brand=? AND period_end>=?${categoryClause}`)
        .bind(rule.target_value, rule.source_value, rule.effective_from, ...categoryValues).run() as { meta?: { changes?: number } };
    } else if (rule.kind === "brand_override") {
      const identity = parseBrandOverrideIdentity(rule.source_value);
      if (!identity) { applied.push({ id: rule.id, kind: rule.kind, changes: 0 }); continue; }
      result = await db.prepare(`UPDATE market_ranking_entries SET brand=?, updated_at=CURRENT_TIMESTAMP
        WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=? AND period_end>=?`)
        .bind(rule.target_value, identity.category, identity.scope, identity.rankingDimension, identity.skuCode, rule.effective_from).run() as { meta?: { changes?: number } };
    } else if (rule.kind === "operation_mode") {
      result = await db.prepare(`UPDATE market_ranking_entries SET operation_mode=?, updated_at=CURRENT_TIMESTAMP
        WHERE (source_operation_mode=? OR scope=?) AND period_end>=?${categoryClause}`)
        .bind(rule.target_value, rule.source_value, rule.source_value, rule.effective_from, ...categoryValues).run() as { meta?: { changes?: number } };
    } else {
      const like = `%${rule.source_value.replace(/[\\%_]/g, (value) => `\\${value}`)}%`;
      result = await db.prepare(`UPDATE market_ranking_entries SET subcategory=?, updated_at=CURRENT_TIMESTAMP
        WHERE (source_subcategory=? OR subcategory=? OR sku_code=? OR product_name LIKE ? ESCAPE '\\') AND period_end>=?${categoryClause}`)
        .bind(rule.target_value, rule.source_value, rule.source_value, rule.source_value, like, rule.effective_from, ...categoryValues).run() as { meta?: { changes?: number } };
    }
    const changes = Number(result.meta?.changes ?? 0);
    changed += changes;
    applied.push({ id: rule.id, kind: rule.kind, changes });
  }
  await audit(db, actor, "apply_published_mappings", "market_ranking_entries", category || "*", null, { category, changed, applied });
  return { category, changed, applied };
}

export async function suggestMarketBrand(db: MarketDatabase, input: { modelId: string; productName: string }) {
  await ensureMarketAdminSchema(db);
  const modelId = requiredText(input.modelId, 120);
  const productName = requiredText(input.productName, 500);
  const raw = await runPromptTextCompletion(db, modelId, [
    "你是电商商品品牌识别助手。只根据商品标题识别品牌，不要猜测制造商、店铺名或品类名。",
    "无法可靠识别时 brand 必须为空字符串。只返回严格 JSON：{\"brand\":\"\"}。",
    `商品标题：${JSON.stringify(productName)}`,
  ].join("\n"));
  const unfenced = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
  let parsed: unknown;
  try { parsed = JSON.parse(unfenced); } catch { throw new Error("AI 品牌识别没有返回有效 JSON"); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("AI 品牌识别结果格式无效");
  const brand = typeof (parsed as Record<string, unknown>).brand === "string"
    ? (parsed as Record<string, unknown>).brand as string
    : "";
  return { brand: brand.trim().slice(0, 120), modelId };
}

export async function recognizeNextMarketBrandBatch(db: MarketDatabase, input: {
  modelId: string; q?: string; category?: string; batchSize?: number;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const modelId = requiredText(input.modelId, 120);
  const batchSize = integer(input.batchSize, 40, 1, 50);
  const category = optionalText(input.category, 120) ?? "";
  const q = optionalText(input.q, 100) ?? "";
  const clauses = ["r.rn=1", "(s.id IS NULL OR s.status='failed')"];
  const values: unknown[] = [];
  if (category) { clauses.push("r.category=?"); values.push(category); }
  if (q) { clauses.push("(r.sku_code LIKE ? OR r.product_name LIKE ? OR r.brand LIKE ?)"); values.push(`%${q}%`, `%${q}%`, `%${q}%`); }
  const rows = await db.prepare(`WITH ranked AS (
      SELECT m.category, m.scope, m.ranking_dimension, m.sku_code, m.product_name, m.brand,
        ROW_NUMBER() OVER (PARTITION BY m.category, m.scope, m.ranking_dimension, m.sku_code ORDER BY m.period_end DESC, m.id DESC) rn
      FROM market_ranking_entries m
    )
    SELECT r.category, r.scope, r.ranking_dimension, r.sku_code, r.product_name, r.brand
    FROM ranked r LEFT JOIN market_brand_suggestions s
      ON s.category=r.category AND s.scope=r.scope AND s.ranking_dimension=r.ranking_dimension AND s.sku_code=r.sku_code
    WHERE ${clauses.join(" AND ")}
    ORDER BY r.category, r.scope, r.ranking_dimension, r.sku_code LIMIT ?`)
    .bind(...values, batchSize).all<{ category: string; scope: string; ranking_dimension: string; sku_code: string; product_name: string; brand: string }>();
  const candidates = rows.results ?? [];
  if (!candidates.length) return { processed: 0, recognized: 0, empty: 0, done: true };
  const payload = candidates.map((row, index) => ({ key: String(index), title: row.product_name }));
  const raw = await runPromptTextCompletion(db, modelId, [
    "你是电商商品品牌识别助手。只根据标题识别明确出现或可可靠确定的品牌；不要把品类、店铺、规格或制造商当成品牌。",
    "无法可靠识别时 brand 必须为空字符串。每个输入 key 必须原样返回且只返回严格 JSON：{\"items\":[{\"key\":\"0\",\"brand\":\"\"}]}。",
    `输入：${JSON.stringify(payload)}`,
  ].join("\n"));
  const unfenced = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
  let parsed: unknown;
  try { parsed = JSON.parse(unfenced); } catch { throw new Error("AI 批量品牌识别没有返回有效 JSON"); }
  const items = parsed && typeof parsed === "object" && !Array.isArray(parsed) && Array.isArray((parsed as { items?: unknown }).items)
    ? (parsed as { items: unknown[] }).items : [];
  const byKey = new Map<string, string>();
  for (const item of items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const value = item as Record<string, unknown>;
    if (typeof value.key === "string" && typeof value.brand === "string") byKey.set(value.key, value.brand.trim().slice(0, 120));
  }
  let recognized = 0;
  const statements = candidates.map((row, index) => {
    const brand = byKey.get(String(index)) ?? "";
    if (brand) recognized += 1;
    return db.prepare(`INSERT INTO market_brand_suggestions
      (id, category, scope, ranking_dimension, sku_code, product_name, current_brand, ai_brand, status, model_id, error_message, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', CURRENT_TIMESTAMP)
      ON CONFLICT(category, scope, ranking_dimension, sku_code) DO UPDATE SET
        product_name=excluded.product_name, current_brand=excluded.current_brand, ai_brand=excluded.ai_brand,
        status=excluded.status, model_id=excluded.model_id, error_message='', updated_at=CURRENT_TIMESTAMP`)
      .bind(`market-brand-${crypto.randomUUID()}`, row.category, row.scope, row.ranking_dimension, row.sku_code,
        row.product_name, row.brand, brand, brand ? "ai_pending" : "ai_empty", modelId);
  });
  await db.batch(statements);
  await audit(db, actor, "recognize_market_brand_batch", "market_brand_suggestion", category || "*", null, {
    query: q, modelId, processed: candidates.length, recognized, empty: candidates.length - recognized,
  });
  return { processed: candidates.length, recognized, empty: candidates.length - recognized, done: candidates.length < batchSize };
}

type MarketBrandRecognitionJobRow = {
  id: string; model_id: string; query_text: string; category: string; status: string;
  total_count: number; processed_count: number; recognized_count: number; empty_count: number; batch_size: number;
  created_by: string; created_at: string; started_at: string | null; updated_at: string; completed_at: string | null;
  last_error: string; lease_token: string; lease_expires_at: string | null;
};

function marketBrandRecognitionJobValue(row: MarketBrandRecognitionJobRow) {
  const totalCount = Number(row.total_count ?? 0);
  const processedCount = Math.min(totalCount, Number(row.processed_count ?? 0));
  return {
    id: row.id,
    modelId: row.model_id,
    query: row.query_text,
    category: row.category,
    status: row.status,
    totalCount,
    processedCount,
    remainingCount: Math.max(0, totalCount - processedCount),
    recognizedCount: Number(row.recognized_count ?? 0),
    emptyCount: Number(row.empty_count ?? 0),
    batchSize: Number(row.batch_size ?? 40),
    progressBps: totalCount ? Math.min(10_000, Math.round(processedCount * 10_000 / totalCount)) : 10_000,
    createdBy: row.created_by,
    createdAt: row.created_at,
    startedAt: row.started_at,
    updatedAt: row.updated_at,
    completedAt: row.completed_at,
    lastError: row.last_error,
  };
}

async function readMarketBrandRecognitionJob(db: MarketDatabase, id: string) {
  const row = await db.prepare("SELECT * FROM market_brand_recognition_jobs WHERE id=? LIMIT 1")
    .bind(id).first<MarketBrandRecognitionJobRow>();
  return row ? marketBrandRecognitionJobValue(row) : null;
}

async function countRemainingMarketBrandCandidates(db: MarketDatabase, input: { q?: string; category?: string }) {
  const category = optionalText(input.category, 120) ?? "";
  const q = optionalText(input.q, 100) ?? "";
  const clauses = ["r.rn=1", "(s.id IS NULL OR s.status='failed')"];
  const values: unknown[] = [];
  if (category) { clauses.push("r.category=?"); values.push(category); }
  if (q) { clauses.push("(r.sku_code LIKE ? OR r.product_name LIKE ? OR r.brand LIKE ?)"); values.push(`%${q}%`, `%${q}%`, `%${q}%`); }
  const row = await db.prepare(`WITH ranked AS (
      SELECT m.category, m.scope, m.ranking_dimension, m.sku_code, m.product_name, m.brand,
        ROW_NUMBER() OVER (PARTITION BY m.category, m.scope, m.ranking_dimension, m.sku_code ORDER BY m.period_end DESC, m.id DESC) rn
      FROM market_ranking_entries m
    )
    SELECT COUNT(*) count FROM ranked r LEFT JOIN market_brand_suggestions s
      ON s.category=r.category AND s.scope=r.scope AND s.ranking_dimension=r.ranking_dimension AND s.sku_code=r.sku_code
    WHERE ${clauses.join(" AND ")}`).bind(...values).first<CountRow>();
  return Number(row?.count ?? 0);
}

export async function getMarketBrandRecognitionJob(db: MarketDatabase, input: { q?: string; category?: string } = {}) {
  await ensureMarketAdminSchema(db);
  const q = optionalText(input.q, 100) ?? "";
  const category = optionalText(input.category, 120) ?? "";
  const row = await db.prepare(`SELECT * FROM market_brand_recognition_jobs
    WHERE query_text=? AND category=? ORDER BY created_at DESC LIMIT 1`)
    .bind(q, category).first<MarketBrandRecognitionJobRow>();
  return row ? marketBrandRecognitionJobValue(row) : null;
}

export async function createMarketBrandRecognitionJob(db: MarketDatabase, input: {
  modelId: string; q?: string; category?: string; batchSize?: number;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const modelId = requiredText(input.modelId, 120);
  const q = optionalText(input.q, 100) ?? "";
  const category = optionalText(input.category, 120) ?? "";
  const batchSize = integer(input.batchSize, 40, 1, 50);
  const existing = await db.prepare(`SELECT * FROM market_brand_recognition_jobs
    WHERE query_text=? AND category=? AND status IN ('queued','running','paused','failed')
    ORDER BY created_at DESC LIMIT 1`).bind(q, category).first<MarketBrandRecognitionJobRow>();
  if (existing) return { ...marketBrandRecognitionJobValue(existing), reused: true };
  const totalCount = await countRemainingMarketBrandCandidates(db, { q, category });
  const id = `market-brand-job-${crypto.randomUUID()}`;
  const status = totalCount ? "queued" : "completed";
  await db.prepare(`INSERT INTO market_brand_recognition_jobs
    (id, model_id, query_text, category, status, total_count, batch_size, created_by, completed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE NULL END)`)
    .bind(id, modelId, q, category, status, totalCount, batchSize, actor.email, status).run();
  await audit(db, actor, "create_market_brand_recognition_job", "market_brand_recognition_job", id, null, { modelId, q, category, totalCount, batchSize });
  return { ...(await readMarketBrandRecognitionJob(db, id))!, reused: false };
}

export async function runMarketBrandRecognitionJobBatch(db: MarketDatabase, id: string, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const jobId = requiredText(id, 160);
  const job = await db.prepare("SELECT * FROM market_brand_recognition_jobs WHERE id=? LIMIT 1")
    .bind(jobId).first<MarketBrandRecognitionJobRow>();
  if (!job) throw new Error("品牌识别任务不存在");
  if (job.status === "completed") return { job: marketBrandRecognitionJobValue(job), done: true };
  if (job.status === "paused") return { job: marketBrandRecognitionJobValue(job), done: false, paused: true };
  const leaseToken = crypto.randomUUID();
  const claim = await db.prepare(`UPDATE market_brand_recognition_jobs SET
      status='running', started_at=COALESCE(started_at,CURRENT_TIMESTAMP), lease_token=?,
      lease_expires_at=datetime('now','+3 minutes'), last_error='', updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status IN ('queued','running','failed')
      AND (lease_token='' OR lease_expires_at IS NULL OR datetime(lease_expires_at)<=datetime('now'))`)
    .bind(leaseToken, jobId).run() as { meta?: { changes?: number } };
  if (Number(claim.meta?.changes ?? 0) !== 1) return { job: await readMarketBrandRecognitionJob(db, jobId), done: false, waiting: true };
  try {
    const result = await recognizeNextMarketBrandBatch(db, {
      modelId: job.model_id, q: job.query_text, category: job.category, batchSize: job.batch_size,
    }, actor);
    const remainingCount = await countRemainingMarketBrandCandidates(db, { q: job.query_text, category: job.category });
    const minimumTotal = Number(job.processed_count ?? 0) + Number(result.processed ?? 0) + remainingCount;
    const totalCount = Math.max(Number(job.total_count ?? 0), minimumTotal);
    const processedCount = Math.max(Number(job.processed_count ?? 0) + Number(result.processed ?? 0), totalCount - remainingCount);
    const completed = remainingCount === 0;
    await db.prepare(`UPDATE market_brand_recognition_jobs SET
        total_count=?, processed_count=?, recognized_count=recognized_count+?, empty_count=empty_count+?,
        status=CASE WHEN status='paused' THEN 'paused' WHEN ?=1 THEN 'completed' ELSE 'running' END,
        completed_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE completed_at END,
        lease_token='', lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND lease_token=?`)
      .bind(totalCount, processedCount, result.recognized, result.empty, completed ? 1 : 0, completed ? 1 : 0, jobId, leaseToken).run();
    return { job: await readMarketBrandRecognitionJob(db, jobId), done: completed, processed: result.processed };
  } catch (error) {
    const message = (error instanceof Error ? error.message : "品牌识别失败").slice(0, 300);
    await db.prepare(`UPDATE market_brand_recognition_jobs SET status='failed', last_error=?, lease_token='', lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND lease_token=?`).bind(message, jobId, leaseToken).run();
    throw error;
  }
}

export async function setMarketBrandRecognitionJobStatus(db: MarketDatabase, input: { id: string; status: "paused" | "queued" }, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const id = requiredText(input.id, 160);
  if (input.status !== "paused" && input.status !== "queued") throw new Error("品牌识别任务状态无效");
  const result = await db.prepare(`UPDATE market_brand_recognition_jobs SET status=?, last_error='', updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status IN ('queued','running','paused','failed')`).bind(input.status, id).run() as { meta?: { changes?: number } };
  if (Number(result.meta?.changes ?? 0) !== 1) throw new Error("品牌识别任务已经完成或不存在");
  await audit(db, actor, input.status === "paused" ? "pause_market_brand_recognition_job" : "resume_market_brand_recognition_job", "market_brand_recognition_job", id, null, { status: input.status });
  return await readMarketBrandRecognitionJob(db, id);
}

export async function confirmMarketBrandSuggestionsBatch(db: MarketDatabase, input: {
  q?: string; category?: string; batchSize?: number;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const batchSize = integer(input.batchSize, 25, 1, 50);
  const category = optionalText(input.category, 120) ?? "";
  const q = optionalText(input.q, 100) ?? "";
  const clauses = ["status='ai_pending'", "ai_brand<>''"];
  const values: unknown[] = [];
  if (category) { clauses.push("category=?"); values.push(category); }
  if (q) { clauses.push("(sku_code LIKE ? OR product_name LIKE ? OR current_brand LIKE ? OR ai_brand LIKE ?)"); values.push(`%${q}%`, `%${q}%`, `%${q}%`, `%${q}%`); }
  const rows = await db.prepare(`SELECT id, category, scope, ranking_dimension, sku_code, ai_brand
    FROM market_brand_suggestions WHERE ${clauses.join(" AND ")} ORDER BY updated_at, id LIMIT ?`)
    .bind(...values, batchSize).all<{ id: string; category: string; scope: string; ranking_dimension: string; sku_code: string; ai_brand: string }>();
  let confirmed = 0;
  for (const row of rows.results ?? []) {
    await confirmMarketBrand(db, { category: row.category, scope: row.scope, rankingDimension: row.ranking_dimension, skuCode: row.sku_code, brand: row.ai_brand }, actor);
    await db.prepare("UPDATE market_brand_suggestions SET status='confirmed', confirmed_by=?, confirmed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='ai_pending'")
      .bind(actor.email, row.id).run();
    confirmed += 1;
  }
  return { confirmed, done: confirmed < batchSize };
}

export async function confirmMarketBrand(db: MarketDatabase, input: {
  category: string; scope: string; rankingDimension: string; skuCode: string; brand: string;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const identity = {
    category: requiredText(input.category, 120),
    scope: requiredText(input.scope, 120),
    rankingDimension: dimension(input.rankingDimension),
    skuCode: requiredText(input.skuCode, 80),
  };
  const brand = requiredText(input.brand, 120);
  const sourceValue = JSON.stringify(identity);
  const before = await db.prepare(`SELECT COUNT(*) count, MIN(brand) sample_brand FROM market_ranking_entries
    WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
    .bind(identity.category, identity.scope, identity.rankingDimension, identity.skuCode).first<Record<string, unknown>>();
  if (!Number(before?.count ?? 0)) throw new Error("未找到需要确认品牌的商品");
  const existing = await db.prepare(`SELECT id FROM market_master_mapping_rules
    WHERE kind='brand_override' AND category=? AND source_value=? LIMIT 1`)
    .bind(identity.category, sourceValue).first<{ id: string }>();
  const rule = await upsertMarketMapping(db, {
    id: existing?.id,
    kind: "brand_override",
    category: identity.category,
    sourceValue,
    targetValue: brand,
    status: "published",
  }, actor);
  await db.prepare(`UPDATE market_ranking_entries SET brand=?, updated_at=CURRENT_TIMESTAMP
    WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
    .bind(brand, identity.category, identity.scope, identity.rankingDimension, identity.skuCode).run();
  await db.prepare(`UPDATE market_brand_suggestions SET ai_brand=?, status='confirmed', confirmed_by=?, confirmed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=?`)
    .bind(brand, actor.email, identity.category, identity.scope, identity.rankingDimension, identity.skuCode).run();
  await audit(db, actor, "confirm_market_brand", "market_product_brand", sourceValue, before, { ...identity, brand, mappingId: rule.id });
  return { ok: true, ...identity, brand, mappingId: rule.id };
}

export async function listMarketPriceBandVersions(db: MarketDatabase, category = "") {
  await ensureMarketAdminSchema(db);
  const rows = await db.prepare(`SELECT v.id, v.category, v.version, v.status, v.effective_from, v.created_by, v.created_at,
      v.published_by, v.published_at, v.rolled_back_from_id, v.note,
      COALESCE((SELECT json_group_array(json_object('id', i.id, 'label', i.label, 'minCents', i.min_cents, 'maxCents', i.max_cents, 'sortOrder', i.sort_order))
        FROM market_price_band_items i WHERE i.version_id=v.id ORDER BY i.sort_order), '[]') items_json
    FROM market_price_band_versions v
    WHERE (?='' OR v.category IN ('*', ?))
    ORDER BY v.category, v.version DESC LIMIT 100`).bind(category.trim(), category.trim()).all<Record<string, string | number | null>>();
  return { items: (rows.results ?? []).map((row) => ({ ...row, items: parseJsonArray(row.items_json) })) };
}

export async function createMarketPriceBandVersion(db: MarketDatabase, input: {
  category?: string; effectiveFrom?: string; note?: string; items: Array<{ label: string; minCents?: number | null; maxCents?: number | null }>;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const category = optionalText(input.category, 120) || "*";
  const next = await db.prepare("SELECT COALESCE(MAX(version), 0)+1 version FROM market_price_band_versions WHERE category=?").bind(category).first<{ version: number }>();
  const version = Number(next?.version ?? 1);
  const id = `market-price-band-${category.replace(/[^\w-]+/g, "_")}-v${version}-${crypto.randomUUID().slice(0, 8)}`;
  const items = normalizePriceBandItems(input.items);
  await db.prepare(`INSERT INTO market_price_band_versions (id, category, version, status, effective_from, created_by, note)
    VALUES (?, ?, ?, 'draft', ?, ?, ?)`).bind(id, category, version, date(input.effectiveFrom) ?? "1970-01-01", actor.email, optionalText(input.note, 300) ?? "").run();
  await db.batch(items.map((item, index) => db.prepare(`INSERT INTO market_price_band_items (id, version_id, label, min_cents, max_cents, sort_order)
    VALUES (?, ?, ?, ?, ?, ?)`).bind(`${id}-${index + 1}`, id, item.label, item.minCents, item.maxCents, (index + 1) * 10)));
  await audit(db, actor, "create_price_band_version", "market_price_band_version", id, null, { id, category, version, items });
  return { id, category, version, status: "draft", items };
}

async function readPriceBandMutationReceipt(
  db: MarketDatabase,
  input: { auditId: string; action: "publish_price_band_version" | "rollback_price_band_version"; entityId: string },
) {
  const receipt = await db.prepare(`SELECT after_json FROM market_master_audit_logs
    WHERE id=? AND action=? AND entity_type='market_price_band_version' AND entity_id=? LIMIT 1`)
    .bind(input.auditId, input.action, input.entityId).first<{ after_json: string }>();
  if (!receipt) throw new Error("价格带版本事务回执缺失");
  let snapshot: unknown;
  try {
    snapshot = JSON.parse(receipt.after_json);
  } catch {
    throw new Error("价格带版本事务回执不可解析");
  }
  if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
    throw new Error("价格带版本事务回执不可解析");
  }
  return snapshot as Record<string, unknown>;
}

export async function publishMarketPriceBandVersion(db: MarketDatabase, id: string, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const before = await db.prepare("SELECT * FROM market_price_band_versions WHERE id=? LIMIT 1").bind(id).first<Record<string, unknown>>();
  if (!before) throw new Error("价格带版本不存在");
  const category = String(before.category);
  const expectedStatus = String(before.status);
  if (!new Set(["draft", "archived"]).has(expectedStatus)) throw new Error("该价格带版本当前不能发布");
  const guardId = `market-price-band-publish-guard-${crypto.randomUUID()}`;
  const auditId = `market-audit-${crypto.randomUUID()}`;
  await db.batch([
    db.prepare(`INSERT INTO market_master_audit_logs
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
      SELECT CASE WHEN EXISTS (SELECT 1 FROM market_price_band_versions WHERE id=? AND category=? AND status=?)
        THEN ? ELSE NULL END, ?, ?, 'market_price_band_publish_guard', 'market_price_band_version', ?, '{}', '{}'`)
      .bind(id, category, expectedStatus, guardId, actor.email, actor.role, id),
    db.prepare("UPDATE market_price_band_versions SET status='archived' WHERE category=? AND status='published' AND id<>?")
      .bind(category, id),
    db.prepare(`UPDATE market_price_band_versions
      SET status='published', published_by=?, published_at=CURRENT_TIMESTAMP
      WHERE id=? AND category=? AND status=?`).bind(actor.email, id, category, expectedStatus),
    db.prepare(`INSERT INTO market_master_audit_logs
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
      SELECT ?, ?, ?, 'publish_price_band_version', 'market_price_band_version', id, ?,
        json_object('id', id, 'category', category, 'version', version, 'status', status,
          'effective_from', effective_from, 'created_by', created_by, 'created_at', created_at,
          'published_by', published_by, 'published_at', published_at,
          'rolled_back_from_id', rolled_back_from_id, 'note', note)
      FROM market_price_band_versions WHERE id=? AND category=? AND status='published'`)
      .bind(auditId, actor.email, actor.role, JSON.stringify(before), id, category),
    db.prepare(`UPDATE market_price_band_versions
      SET category=CASE WHEN status='published' AND EXISTS (
        SELECT 1 FROM market_master_audit_logs
        WHERE id=? AND action='publish_price_band_version' AND entity_id=?
      ) THEN category ELSE NULL END
      WHERE id=? AND category=?`)
      .bind(auditId, id, id, category),
    db.prepare("DELETE FROM market_master_audit_logs WHERE id=? AND action='market_price_band_publish_guard'").bind(guardId),
  ]);
  return readPriceBandMutationReceipt(db, {
    auditId,
    action: "publish_price_band_version",
    entityId: id,
  });
}

export async function rollbackMarketPriceBandVersion(db: MarketDatabase, input: { category?: string; targetVersionId: string }, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const target = await db.prepare("SELECT * FROM market_price_band_versions WHERE id=? LIMIT 1").bind(input.targetVersionId).first<Record<string, unknown>>();
  if (!target) throw new Error("回滚目标价格带版本不存在");
  const category = String(target.category);
  const expectedStatus = String(target.status);
  if (expectedStatus !== "archived") throw new Error("只能回滚到已归档的价格带版本");
  const guardId = `market-price-band-rollback-guard-${crypto.randomUUID()}`;
  const auditId = `market-audit-${crypto.randomUUID()}`;
  await db.batch([
    db.prepare(`INSERT INTO market_master_audit_logs
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
      SELECT CASE WHEN EXISTS (SELECT 1 FROM market_price_band_versions WHERE id=? AND category=? AND status=?)
        THEN ? ELSE NULL END, ?, ?, 'market_price_band_rollback_guard', 'market_price_band_version', ?, '{}', '{}'`)
      .bind(input.targetVersionId, category, expectedStatus, guardId, actor.email, actor.role, input.targetVersionId),
    db.prepare(`UPDATE market_price_band_versions
      SET rolled_back_from_id=COALESCE((SELECT current.id FROM market_price_band_versions current
        WHERE current.category=? AND current.status='published' AND current.id<>?
        ORDER BY current.effective_from DESC, current.version DESC, COALESCE(current.published_at, '') DESC, current.id DESC
        LIMIT 1), '')
      WHERE id=? AND category=? AND status=?`)
      .bind(category, input.targetVersionId, input.targetVersionId, category, expectedStatus),
    db.prepare("UPDATE market_price_band_versions SET status='archived' WHERE category=? AND status='published' AND id<>?")
      .bind(category, input.targetVersionId),
    db.prepare(`UPDATE market_price_band_versions
      SET status='published', published_by=?, published_at=CURRENT_TIMESTAMP
      WHERE id=? AND category=? AND status=?`).bind(actor.email, input.targetVersionId, category, expectedStatus),
    db.prepare(`INSERT INTO market_master_audit_logs
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
      SELECT ?, ?, ?, 'rollback_price_band_version', 'market_price_band_version', id, ?,
        json_object('id', id, 'category', category, 'version', version, 'status', status,
          'effective_from', effective_from, 'created_by', created_by, 'created_at', created_at,
          'published_by', published_by, 'published_at', published_at,
          'rolled_back_from_id', rolled_back_from_id, 'note', note)
      FROM market_price_band_versions WHERE id=? AND category=? AND status='published'`)
      .bind(auditId, actor.email, actor.role, JSON.stringify(target), input.targetVersionId, category),
    db.prepare(`UPDATE market_price_band_versions
      SET category=CASE WHEN status='published' AND EXISTS (
        SELECT 1 FROM market_master_audit_logs
        WHERE id=? AND action='rollback_price_band_version' AND entity_id=?
      ) THEN category ELSE NULL END
      WHERE id=? AND category=?`)
      .bind(auditId, input.targetVersionId, input.targetVersionId, category),
    db.prepare("DELETE FROM market_master_audit_logs WHERE id=? AND action='market_price_band_rollback_guard'").bind(guardId),
  ]);
  return readPriceBandMutationReceipt(db, {
    auditId,
    action: "rollback_price_band_version",
    entityId: input.targetVersionId,
  });
}

export async function upsertMarketDownloadConfig(db: MarketDatabase, input: { category: string; scope?: string; rankingDimension: string; monthStart: string; monthEnd: string; status?: string }, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const config = {
    id: `market-download-config-${crypto.randomUUID()}`,
    category: requiredText(input.category, 120),
    scope: optionalText(input.scope, 120) || "全部",
    rankingDimension: dimension(input.rankingDimension),
    monthStart: requiredMonth(input.monthStart),
    monthEnd: requiredMonth(input.monthEnd),
    status: optionalText(input.status, 30) || "enabled",
  };
  if (config.monthStart > config.monthEnd) throw new Error("起始月份不能晚于结束月份");
  const existing = await db.prepare(`SELECT * FROM market_download_configs WHERE category=? AND scope=? AND ranking_dimension=? AND month_start=? AND month_end=? LIMIT 1`)
    .bind(config.category, config.scope, config.rankingDimension, config.monthStart, config.monthEnd).first<Record<string, unknown>>();
  const id = String(existing?.id ?? config.id);
  await db.prepare(`INSERT INTO market_download_configs (id, category, scope, ranking_dimension, month_start, month_end, status, created_by, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(category, scope, ranking_dimension, month_start, month_end)
    DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP`)
    .bind(id, config.category, config.scope, config.rankingDimension, config.monthStart, config.monthEnd, config.status, actor.email).run();
  await audit(db, actor, "upsert_download_config", "market_download_config", id, existing, { ...config, id });
  return { ...config, id };
}

export async function planMissingMarketDownloads(db: MarketDatabase, input: { category?: string; scope?: string; rankingDimension?: string } = {}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const configs = await db.prepare(`SELECT * FROM market_download_configs
    WHERE status='enabled' AND (?='' OR category=?) AND (?='' OR scope=?) AND (?='' OR ranking_dimension=?)
    ORDER BY category, scope, ranking_dimension, month_start`).bind(input.category ?? "", input.category ?? "", input.scope ?? "", input.scope ?? "", input.rankingDimension ?? "", input.rankingDimension ?? "")
    .all<Record<string, string>>();
  let created = 0;
  let reused = 0;
  for (const config of configs.results ?? []) {
    for (const month of monthsBetween(config.month_start, config.month_end)) {
      const verified = await db.prepare(`SELECT 1 FROM market_download_tasks
        WHERE category=? AND scope=? AND ranking_dimension=? AND month=?
          AND status IN ('imported','published') AND header_valid=1 AND period_valid=1
          AND category_valid=1 AND dimension_valid=1 AND import_batch_id<>'' LIMIT 1`)
        .bind(config.category, config.scope, config.ranking_dimension, month).first();
      if (verified) continue;
      const existingTask = await db.prepare(`SELECT id, attempt_count FROM market_download_tasks
        WHERE category=? AND scope=? AND month=? AND ranking_dimension=? LIMIT 1`)
        .bind(config.category, config.scope, month, config.ranking_dimension).first<{ id: string; attempt_count: number }>();
      if (existingTask) {
        await db.prepare(`UPDATE market_download_tasks SET
          status=CASE WHEN status IN ('failed','planned','waiting_login') AND attempt_count < 3 THEN 'planned' ELSE status END,
          next_retry_at=CASE WHEN attempt_count < 3 THEN NULL ELSE next_retry_at END,
          updated_at=CURRENT_TIMESTAMP WHERE id=?`).bind(existingTask.id).run();
        reused += 1;
      } else {
        await db.prepare(`INSERT INTO market_download_tasks (id, category, scope, month, ranking_dimension, status, updated_at)
          VALUES (?, ?, ?, ?, ?, 'planned', CURRENT_TIMESTAMP)`)
          .bind(`market-download-${crypto.randomUUID()}`, config.category, config.scope, month, config.ranking_dimension).run();
        created += 1;
      }
    }
  }
  await audit(db, actor, "plan_missing_downloads", "market_download_task", "*", null, { created, reused, filters: input });
  return { created, reused };
}

export async function recordMarketDownloadAttempt(db: MarketDatabase, input: {
  taskId: string; status: "waiting_login" | "failed"; errorCode?: string; errorMessage?: string;
}, actor: MarketPrincipal) {
  await ensureMarketAdminSchema(db);
  const before = await db.prepare("SELECT * FROM market_download_tasks WHERE id=? LIMIT 1").bind(input.taskId).first<Record<string, unknown>>();
  if (!before) throw new Error("下载任务不存在");
  if (input.status !== "waiting_login" && input.status !== "failed") throw new Error("客户端只能记录等待登录或失败状态");
  const nextAttempt = input.status === "failed" ? Number(before.attempt_count ?? 0) + 1 : Number(before.attempt_count ?? 0);
  const terminalFailed = input.status === "failed" && nextAttempt >= 3;
  const status = terminalFailed ? "failed" : input.status;
  const nextRetryAt = input.status === "failed" && !terminalFailed ? new Date(Date.now() + 15 * 60_000).toISOString() : null;
  const result = await db.prepare(`UPDATE market_download_tasks SET status=?, attempt_count=?, error_code=?, error_message=?,
      next_retry_at=?, last_attempt_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status IN ('planned','created','failed','waiting_login')`)
    .bind(status, nextAttempt, input.errorCode ?? "", input.errorMessage ?? "", nextRetryAt, input.taskId).run() as { meta?: { changes?: number } };
  if (Number(result.meta?.changes ?? 0) !== 1) throw new Error("任务已被执行器领取或已完成，不能从客户端改写状态");
  const after = await db.prepare("SELECT * FROM market_download_tasks WHERE id=? LIMIT 1").bind(input.taskId).first<Record<string, unknown>>();
  await audit(db, actor, "record_download_attempt", "market_download_task", input.taskId, before, after);
  return after;
}

export type MarketSystemKpis = {
  marketIdentityTotal: number;
  pendingPriceCount: number;
  pendingAiCount: number;
  completedAiCount: number;
  sameImageReuseCount: number;
  priceOnlyRecognitionCount: number;
  fullRecognitionCount: number;
  blockedRecognitionCount: number;
};

export async function getMarketSystemKpis(db: MarketDatabase): Promise<MarketSystemKpis> {
  await ensureMarketAdminSchema(db);
  await ensureAnnotationSchema(db);
  await ensureMarketMasterIdentities(db);
  const result = await getCachedMarketSystemKpis(db, async () => {
    const row = await db.prepare(`WITH market_identities AS MATERIALIZED (
      SELECT category, scope, ranking_dimension, sku_code
      FROM market_master_identities
    ), price_state AS MATERIALIZED (
      SELECT category, scope, ranking_dimension, sku_code,
        MAX(CASE WHEN confirmed_market_price_cents IS NULL THEN 1 ELSE 0 END) AS has_pending
      FROM market_price_snapshots
      GROUP BY category, scope, ranking_dimension, sku_code
    ), active_prompts AS MATERIALIZED (
      SELECT prompt.category, prompt.segments_json,
        CASE WHEN NOT EXISTS (
          SELECT 1 FROM market_subcategory_taxonomy taxonomy
          WHERE taxonomy.category=prompt.category AND taxonomy.status='active'
        ) OR (
          NOT EXISTS (
            SELECT 1 FROM market_subcategory_taxonomy taxonomy
            WHERE taxonomy.category=prompt.category AND taxonomy.status='active'
              AND NOT EXISTS (
                SELECT 1 FROM json_each(prompt.segments_json) segment
                WHERE CAST(segment.value AS TEXT)=taxonomy.subcategory
              )
          )
          AND NOT EXISTS (
            SELECT 1 FROM json_each(prompt.segments_json) segment
            WHERE NOT EXISTS (
              SELECT 1 FROM market_subcategory_taxonomy taxonomy
              WHERE taxonomy.category=prompt.category AND taxonomy.status='active'
                AND taxonomy.subcategory=CAST(segment.value AS TEXT)
            )
          )
        ) THEN 1 ELSE 0 END AS ready
      FROM market_annotation_prompt_versions prompt
      WHERE prompt.status='active'
    ), pending_snapshots AS MATERIALIZED (
      SELECT snapshot.category, snapshot.scope, snapshot.ranking_dimension, snapshot.sku_code, snapshot.month,
        COALESCE(NULLIF(snapshot.image_content_sha256,''), image_cache.content_sha256, '') image_content_sha256
      FROM market_price_snapshots snapshot
      LEFT JOIN market_image_cache image_cache
        ON image_cache.source_url=snapshot.image_url
        AND image_cache.status='ready' AND image_cache.content_sha256<>''
      WHERE snapshot.confirmed_market_price_cents IS NULL
    ), pending_ai_snapshots AS MATERIALIZED (
      SELECT snapshot.*
      FROM pending_snapshots snapshot
      WHERE NOT EXISTS (
        SELECT 1 FROM market_annotation_items result
        WHERE result.category=snapshot.category AND result.scope=snapshot.scope
          AND result.ranking_dimension=snapshot.ranking_dimension AND result.sku_code=snapshot.sku_code
          AND result.month=snapshot.month AND result.image_content_sha256=snapshot.image_content_sha256
          AND result.status IN ('review_pending','approved','rejected','committed')
          AND (COALESCE(result.ai_segment,'')<>'' OR result.ai_image_price_cents IS NOT NULL
            OR result.ai_confidence_bps IS NOT NULL OR COALESCE(result.ai_reason,'')<>'')
      )
    ), pending_ai_identities AS MATERIALIZED (
      SELECT snapshot.category, snapshot.scope, snapshot.ranking_dimension, snapshot.sku_code
      FROM pending_ai_snapshots snapshot
      GROUP BY snapshot.category, snapshot.scope, snapshot.ranking_dimension, snapshot.sku_code
    ), same_image_standard AS MATERIALIZED (
      SELECT category, scope, ranking_dimension, sku_code, image_content_sha256
      FROM market_price_snapshots
      WHERE confirmed_market_price_cents IS NOT NULL AND ai_price_type='标准售价' AND image_content_sha256<>''
      GROUP BY category, scope, ranking_dimension, sku_code, image_content_sha256
    ), valid_segment_history AS MATERIALIZED (
      SELECT history.category, history.scope, history.ranking_dimension, history.sku_code
      FROM market_annotation_items history
      JOIN active_prompts prompt ON prompt.category=history.category AND prompt.ready=1
      JOIN json_each(prompt.segments_json) segment ON CAST(segment.value AS TEXT)=history.reviewed_segment
      WHERE history.status='committed' AND history.reviewed_segment<>''
      GROUP BY history.category, history.scope, history.ranking_dimension, history.sku_code
    ), terminal_failures AS MATERIALIZED (
      SELECT failed.category, failed.scope, failed.ranking_dimension, failed.sku_code, failed.month, failed.image_content_sha256
      FROM market_annotation_items failed
      WHERE failed.status='failed' AND failed.attempt_count>=3
        AND NOT EXISTS (
          SELECT 1 FROM market_annotation_items replacement
          WHERE replacement.id<>failed.id AND replacement.category=failed.category AND replacement.scope=failed.scope
            AND replacement.ranking_dimension=failed.ranking_dimension AND replacement.sku_code=failed.sku_code
            AND replacement.month=failed.month AND replacement.image_content_sha256=failed.image_content_sha256
            AND (replacement.status IN ('queued','claimed','inferencing','review_pending','approved','rejected','committed')
              OR (replacement.status='failed' AND replacement.attempt_count<3))
        )
      GROUP BY failed.category, failed.scope, failed.ranking_dimension, failed.sku_code, failed.month, failed.image_content_sha256
    ), route_flags AS MATERIALIZED (
      SELECT identity.category, identity.scope, identity.ranking_dimension, identity.sku_code,
        MAX(CASE WHEN identity.ranking_dimension='SKU' AND prompt.ready=1
          AND snapshot.image_content_sha256<>'' AND failure.sku_code IS NULL
          AND standard.sku_code IS NOT NULL THEN 1 ELSE 0 END) same_image_reuse,
        MAX(CASE WHEN identity.ranking_dimension='SKU' AND prompt.ready=1
          AND snapshot.image_content_sha256<>'' AND failure.sku_code IS NULL
          AND standard.sku_code IS NULL AND segment.sku_code IS NOT NULL THEN 1 ELSE 0 END) price_only_recognition,
        MAX(CASE WHEN identity.ranking_dimension='SKU' AND prompt.ready=1
          AND snapshot.image_content_sha256<>'' AND failure.sku_code IS NULL
          AND standard.sku_code IS NULL AND segment.sku_code IS NULL THEN 1 ELSE 0 END) full_recognition
      FROM pending_ai_identities identity
      LEFT JOIN pending_ai_snapshots snapshot ON snapshot.category=identity.category AND snapshot.scope=identity.scope
        AND snapshot.ranking_dimension=identity.ranking_dimension AND snapshot.sku_code=identity.sku_code
      LEFT JOIN active_prompts prompt ON prompt.category=identity.category
      LEFT JOIN same_image_standard standard ON standard.category=snapshot.category AND standard.scope=snapshot.scope
        AND standard.ranking_dimension=snapshot.ranking_dimension AND standard.sku_code=snapshot.sku_code
        AND standard.image_content_sha256=snapshot.image_content_sha256
      LEFT JOIN valid_segment_history segment ON segment.category=identity.category AND segment.scope=identity.scope
        AND segment.ranking_dimension=identity.ranking_dimension AND segment.sku_code=identity.sku_code
      LEFT JOIN terminal_failures failure ON failure.category=snapshot.category AND failure.scope=snapshot.scope
        AND failure.ranking_dimension=snapshot.ranking_dimension AND failure.sku_code=snapshot.sku_code
        AND failure.month=snapshot.month AND failure.image_content_sha256=snapshot.image_content_sha256
      GROUP BY identity.category, identity.scope, identity.ranking_dimension, identity.sku_code
    ), classified_routes AS MATERIALIZED (
      SELECT *, CASE
        WHEN full_recognition=1 THEN 'full_recognition'
        WHEN price_only_recognition=1 THEN 'price_only_recognition'
        WHEN same_image_reuse=1 THEN 'same_image_reuse'
        ELSE 'blocked'
      END route
      FROM route_flags
    )
    SELECT (SELECT COUNT(*) FROM market_identities) AS market_identity_total,
      COALESCE(SUM(CASE WHEN COALESCE(price_state.has_pending, 1) = 1 THEN 1 ELSE 0 END), 0) AS pending_price_count,
      (SELECT COUNT(*) FROM classified_routes) AS pending_ai_count,
      (SELECT COUNT(*) FROM market_identities)-(SELECT COUNT(*) FROM classified_routes) AS completed_ai_count,
      (SELECT COUNT(*) FROM classified_routes WHERE route='same_image_reuse') AS same_image_reuse_count,
      (SELECT COUNT(*) FROM classified_routes WHERE route='price_only_recognition') AS price_only_recognition_count,
      (SELECT COUNT(*) FROM classified_routes WHERE route='full_recognition') AS full_recognition_count,
      (SELECT COUNT(*) FROM classified_routes WHERE route='blocked') AS blocked_recognition_count
    FROM market_identities identity
    LEFT JOIN price_state ON price_state.category=identity.category AND price_state.scope=identity.scope
      AND price_state.ranking_dimension=identity.ranking_dimension AND price_state.sku_code=identity.sku_code`)
      .first<Record<string, number | null>>();
    return {
      marketIdentityTotal: Number(row?.market_identity_total ?? 0),
      pendingPriceCount: Number(row?.pending_price_count ?? 0),
      pendingAiCount: Number(row?.pending_ai_count ?? 0),
      completedAiCount: Number(row?.completed_ai_count ?? 0),
      sameImageReuseCount: Number(row?.same_image_reuse_count ?? 0),
      priceOnlyRecognitionCount: Number(row?.price_only_recognition_count ?? 0),
      fullRecognitionCount: Number(row?.full_recognition_count ?? 0),
      blockedRecognitionCount: Number(row?.blocked_recognition_count ?? 0),
    };
  }, validateMarketSystemKpiCachePayload);
  return result.payload;
}

export async function getMarketMasterWorkspace(db: MarketDatabase, input: {
  mode?: "all" | "database" | "brand" | "mapping" | "subcategory" | "data";
  q?: string; category?: string; categories?: string[]; rankingDimension?: string; rankingDimensions?: string[]; operationMode?: string; operationModes?: string[]; subcategory?: string; subcategories?: string[];
  priceStatus?: "confirmed" | "pending" | "missing"; priceStatuses?: string[]; candidatePriceSource?: "ai" | "non_ai"; candidatePriceSources?: string[];
  annotationStatus?: "committed" | "pending"; annotationStatuses?: string[]; page?: number; pageSize?: number;
  pendingPriceCategory?: string; pendingPriceCategories?: string[]; pendingPriceSource?: "ai" | "non_ai"; pendingPriceSources?: string[];
  pendingPricePage?: number; pendingPricePageSize?: number;
} = {}) {
  await ensureMarketAdminSchema(db);
  await ensureAnnotationSchema(db);
  const mode = input.mode ?? "all";
  const wantsMaster = mode === "all" || mode === "database" || mode === "brand";
  const wantsDatabase = mode === "all" || mode === "database";
  const wantsBrand = mode === "all" || mode === "brand";
  const wantsMapping = mode === "all" || mode === "mapping";
  const wantsData = mode === "all" || mode === "data";
  const wantsSubcategory = mode === "all" || mode === "subcategory";
  const emptyPage = { items: [] as Array<Record<string, string | number | null>>, pagination: { total: 0, page: 1, pageSize: input.pageSize ?? 30, pageCount: 1 } };
  const emptyRows = { results: [] as Array<Record<string, unknown>> };
  const selectedMasterCategories = [...new Set([...(input.categories ?? []), input.category ?? ""].map((value) => value.trim().slice(0, 120)).filter(Boolean))].slice(0, 50);
  const selectedCategorySql = selectedMasterCategories.length ? ` IN (${selectedMasterCategories.map(() => "?").join(",")})` : "";
  const [masterData, pendingPrices, mappings, priceBands, tasks, configs, coverage, imageCache, audits, categories, subcategories, pricePrompts, brandRecognitionJob, brandSeeds, statusCounts, subcategorySettings] = await Promise.all([
    wantsMaster ? listMarketMasterData(db, input) : Promise.resolve(emptyPage),
    wantsDatabase ? listPendingMarketPrices(db, {
      category: input.pendingPriceCategory,
      categories: input.pendingPriceCategories,
      candidatePriceSource: input.pendingPriceSource,
      candidatePriceSources: input.pendingPriceSources,
      page: input.pendingPricePage,
      pageSize: input.pendingPricePageSize,
    }) : Promise.resolve(emptyPage),
    wantsMapping ? listMarketMappings(db) : Promise.resolve({ items: [] as Array<Record<string, string | number>> }),
    wantsMapping ? listMarketPriceBandVersions(db) : Promise.resolve({ items: [] as Array<Record<string, unknown>> }),
    wantsData ? db.prepare("SELECT * FROM market_download_tasks ORDER BY updated_at DESC LIMIT 100").all<Record<string, unknown>>() : Promise.resolve(emptyRows),
    wantsData ? db.prepare("SELECT * FROM market_download_configs ORDER BY updated_at DESC LIMIT 100").all<Record<string, unknown>>() : Promise.resolve(emptyRows),
    wantsData ? db.prepare(`SELECT category, scope, ranking_dimension, MIN(substr(period_end,1,7)) month_min, MAX(substr(period_end,1,7)) month_max, COUNT(DISTINCT substr(period_end,1,7)) month_count, COUNT(DISTINCT sku_code) sku_count
      FROM market_ranking_entries GROUP BY category, scope, ranking_dimension ORDER BY category, scope, ranking_dimension LIMIT 200`).all<Record<string, unknown>>() : Promise.resolve(emptyRows),
    (wantsDatabase || wantsData) ? db.prepare(`SELECT COUNT(*) total, SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) cached, SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
      SUM(CASE WHEN status NOT IN ('ready','failed') THEN 1 ELSE 0 END) pending FROM market_image_cache`).first<Record<string, number | null>>() : Promise.resolve(null),
    wantsData ? db.prepare("SELECT * FROM market_master_audit_logs ORDER BY created_at DESC LIMIT 100").all<Record<string, unknown>>() : Promise.resolve(emptyRows),
    db.prepare("SELECT category value, COUNT(DISTINCT sku_code) count FROM market_ranking_entries GROUP BY category ORDER BY count DESC, category LIMIT 200").all<{ value: string; count: number }>(),
    wantsDatabase ? db.prepare(`SELECT t.subcategory value, COUNT(DISTINCT m.sku_code) count
      FROM market_subcategory_taxonomy t
      LEFT JOIN market_ranking_entries m ON m.category=t.category AND m.subcategory=t.subcategory
      WHERE t.status='active'${selectedCategorySql ? ` AND t.category${selectedCategorySql}` : ""}
      GROUP BY t.subcategory ORDER BY count DESC, t.subcategory LIMIT 200`).bind(...selectedMasterCategories).all<{ value: string; count: number }>() : Promise.resolve({ results: [] as Array<{ value: string; count: number }> }),
    wantsDatabase ? db.prepare(`SELECT c.category,
        COALESCE((SELECT p.id FROM market_annotation_prompt_versions p WHERE p.category=c.category AND p.status='active' ORDER BY p.version DESC LIMIT 1), '') prompt_id,
        (SELECT COUNT(*) FROM market_price_snapshots ps
          WHERE ps.category=c.category AND ps.confirmed_market_price_cents IS NULL
            AND (ps.image_content_sha256<>'' OR EXISTS (
              SELECT 1 FROM market_image_cache mic WHERE mic.source_url=ps.image_url AND mic.status='ready' AND mic.content_sha256<>''
            ))) pending_count
      FROM (SELECT DISTINCT category FROM market_ranking_entries WHERE category<>'') c ORDER BY c.category`).all<Record<string, unknown>>() : Promise.resolve(emptyRows),
    wantsBrand ? getMarketBrandRecognitionJob(db, { q: input.q, category: input.category }) : Promise.resolve(null),
    wantsBrand ? getMarketBrandSeedWorkspace(db, input) : Promise.resolve({ dictionary: { items: [], counts: { total: 0, enabled: 0, system: 0, manual: 0 } }, unknown: { items: [], pagination: { total: 0, page: 1, pageCount: 1 } } }),
    wantsDatabase ? db.prepare(`SELECT
      COUNT(*) total,
      SUM(CASE WHEN confirmed_market_price_cents IS NULL THEN 1 ELSE 0 END) pending_prices,
      SUM(CASE WHEN confirmed_market_price_cents IS NOT NULL THEN 1 ELSE 0 END) confirmed_prices
      FROM market_price_snapshots${selectedCategorySql ? ` WHERE category${selectedCategorySql}` : ""}`).bind(...selectedMasterCategories).first<Record<string, number | null>>() : Promise.resolve(null),
    wantsSubcategory ? getMarketSubcategoryWorkspace(db, input.category ?? "") : Promise.resolve({ category: "", categories: [], items: [] }),
  ]);
  return {
    masterData,
    pendingPrices,
    mappings,
    priceBands,
    downloadTasks: tasks.results ?? [],
    downloadConfigs: configs.results ?? [],
    coverage: coverage.results ?? [],
    imageCache: {
      total: Number(imageCache?.total ?? 0),
      cached: Number(imageCache?.cached ?? 0),
      failed: Number(imageCache?.failed ?? 0),
      pending: Number(imageCache?.pending ?? 0),
    },
    categories: categories.results ?? [],
    subcategories: subcategories.results ?? [],
    priceRecognition: { prompts: pricePrompts.results ?? [] },
    brandRecognitionJob,
    brandSeeds,
    statusCounts: {
      total: Number(statusCounts?.total ?? 0),
      pendingPrices: Number(statusCounts?.pending_prices ?? 0),
      confirmedPrices: Number(statusCounts?.confirmed_prices ?? 0),
    },
    subcategorySettings,
    audits: audits.results ?? [],
  };
}

export type MarketComparisonSelection = {
  skuCode: string;
  category: string;
  scope: string;
  rankingDimension: MarketDimension;
};

function normalizeMarketComparisonSelection(selection: MarketComparisonSelection): MarketComparisonSelection {
  return {
    skuCode: normalizeMarketSkuCode(selection.skuCode),
    category: requiredText(selection.category, 120),
    scope: requiredText(selection.scope, 120),
    rankingDimension: dimension(selection.rankingDimension),
  };
}

function marketComparisonSelectionKey(selection: MarketComparisonSelection) {
  return JSON.stringify([selection.category, selection.scope, selection.rankingDimension, selection.skuCode]);
}

export async function getMarketSkuComparison(db: MarketDatabase, input: {
  skuCodes?: string[]; selections?: MarketComparisonSelection[]; q?: string; category?: string; rankingDimension?: string;
  categories?: string[]; scopes?: string[]; rankingDimensions?: string[]; operationModes?: string[];
  brands?: string[]; subcategories?: string[]; priceBands?: string[]; startDate?: string; endDate?: string;
}) {
  await ensureMarketAdminSchema(db);
  const hasExactSelections = input.selections !== undefined;
  const selections = [...new Map((input.selections ?? [])
    .map(normalizeMarketComparisonSelection)
    .filter((selection) => selection.skuCode)
    .map((selection) => [marketComparisonSelectionKey(selection), selection])).values()];
  const skuCodes = [...new Set((input.skuCodes ?? []).map(normalizeMarketSkuCode).filter(Boolean))];
  const selectionCount = hasExactSelections ? selections.length : skuCodes.length;
  if (selectionCount < 2 || selectionCount > 5) throw new Error("商品对比必须选择 2 到 5 个 SKU");
  const filters: MarketOverviewFilters = {
    query: optionalText(input.q, 100),
    categories: input.categories?.length ? input.categories : input.category ? [input.category] : undefined,
    scopes: input.scopes,
    rankingDimensions: input.rankingDimensions?.length ? input.rankingDimensions.map(dimension) : input.rankingDimension ? [dimension(input.rankingDimension)] : undefined,
    operationModes: input.operationModes,
    brands: input.brands,
    subcategories: input.subcategories,
    priceBands: input.priceBands,
    startDate: date(input.startDate),
    endDate: date(input.endDate),
  };
  const clauses = hasExactSelections
    ? [`(m.sku_code,m.category,m.scope,m.ranking_dimension) IN (${selections.map(() => "(?,?,?,?)").join(",")})`]
    : [`m.sku_code IN (${skuCodes.map(() => "?").join(",")})`];
  const values: unknown[] = hasExactSelections
    ? selections.flatMap((selection) => [selection.skuCode, selection.category, selection.scope, selection.rankingDimension])
    : [...skuCodes];
  const list = (column: string, entries?: string[]) => {
    const normalized = [...new Set((entries ?? []).map((entry) => entry.trim()).filter(Boolean))].slice(0, 30);
    if (!normalized.length) return;
    clauses.push(`${column} IN (${normalized.map(() => "?").join(",")})`);
    values.push(...normalized);
  };
  list("m.category", filters.categories);
  list("m.scope", filters.scopes);
  list("m.ranking_dimension", filters.rankingDimensions);
  list("m.operation_mode", filters.operationModes);
  list("m.brand", filters.brands);
  list("m.subcategory", filters.subcategories);
  if (filters.query) {
    const query = `%${filters.query}%`;
    clauses.push("(m.sku_code LIKE ? OR m.product_name LIKE ? OR m.brand LIKE ?)");
    values.push(query, query, query);
  }
  const priceBandValues = [...new Set((filters.priceBands ?? []).map((entry) => entry.trim()).filter(Boolean))].slice(0, 30);
  const priceBandWhere = priceBandValues.length ? `WHERE m.price_band IN (${priceBandValues.map(() => "?").join(",")})` : "";
  if (filters.startDate) { clauses.push("m.period_end>=?"); values.push(filters.startDate); }
  if (filters.endDate) { clauses.push("m.period_start<=?"); values.push(filters.endDate); }
  const rows = await db.prepare(buildMarketAdminComparisonSql({
    factWhere: `WHERE ${clauses.join(" AND ")}`,
    priceBandWhere,
    exactIdentity: hasExactSelections,
  })).bind(...values, ...priceBandValues).all<Record<string, string | number | null>>();
  const resultRows = rows.results ?? [];
  const trendEntries = await Promise.all(resultRows.map(async (row) => {
    const rowSelection: MarketComparisonSelection = {
      skuCode: String(row.sku_code ?? ""),
      category: String(row.category ?? ""),
      scope: String(row.scope ?? ""),
      rankingDimension: dimension(row.ranking_dimension ?? "SKU"),
    };
    const trend = await getMarketItemTrendLite(db, {
      skuCode: rowSelection.skuCode,
      filters: hasExactSelections ? {
        ...filters,
        categories: [rowSelection.category],
        scopes: [rowSelection.scope],
        rankingDimensions: [rowSelection.rankingDimension],
      } : filters,
    });
    return [hasExactSelections ? marketComparisonSelectionKey(rowSelection) : rowSelection.skuCode, trend] as const;
  }));
  const trendByIdentity = new Map(trendEntries);
  const returnedSkuCodes = new Set(resultRows.map((row) => String(row.sku_code ?? "")));
  const returnedSelectionKeys = new Set(resultRows.map((row) => marketComparisonSelectionKey({
    skuCode: String(row.sku_code ?? ""),
    category: String(row.category ?? ""),
    scope: String(row.scope ?? ""),
    rankingDimension: dimension(row.ranking_dimension ?? "SKU"),
  })));
  return {
    items: resultRows.map((row) => {
      const rowSelection: MarketComparisonSelection = {
        skuCode: String(row.sku_code ?? ""),
        category: String(row.category ?? ""),
        scope: String(row.scope ?? ""),
        rankingDimension: dimension(row.ranking_dimension ?? "SKU"),
      };
      const itemTrend = trendByIdentity.get(hasExactSelections ? marketComparisonSelectionKey(rowSelection) : rowSelection.skuCode);
      return {
        skuCode: rowSelection.skuCode,
        productName: String(row.product_name ?? ""),
        brand: String(row.brand ?? ""),
        category: rowSelection.category,
        scope: rowSelection.scope,
        rankingDimension: rowSelection.rankingDimension,
        gmvCents: Number(row.gmv_cents ?? 0),
        quantity: Number(row.quantity ?? 0),
        visitors: Number(row.visitors ?? 0),
        conversionBps: row.conversion_bps === null ? null : Number(row.conversion_bps),
        bestRank: row.best_rank === null ? null : Number(row.best_rank),
        marketPriceCents: row.market_price_cents === null ? null : Number(row.market_price_cents),
        averageTransactionPriceCents: row.average_transaction_price_cents === null ? null : Number(row.average_transaction_price_cents),
        trend: itemTrend?.items ?? [],
        trendTotalMonths: itemTrend?.totalMonths ?? 0,
        trendTruncated: itemTrend?.truncated ?? false,
      };
    }),
    missingSkuCodes: skuCodes.filter((skuCode) => !returnedSkuCodes.has(skuCode)),
    missingSelections: hasExactSelections
      ? selections.filter((selection) => !returnedSelectionKeys.has(marketComparisonSelectionKey(selection)))
      : [],
  };
}

export async function getMarketOverviewForAi(
  db: MarketDatabase,
  args: Record<string, unknown>,
  principal: AppPrincipal,
) {
  const { getMarketOverview } = await import("@/lib/market/database");
  const filtersApplied = aiFilters(args);
  const overview = await getMarketOverview(db, principal, filtersApplied);
  const industryReport = overview.industryReport;
  return {
    filtersApplied,
    dataRange: overview.dataRange,
    summary: overview.summary,
    industryReport: {
      definition: industryReport.definition,
      period: industryReport.period,
      lifecycle: industryReport.lifecycle.slice(-24),
      operationModes: industryReport.operationModes.slice(0, 4),
      brandConcentrationTrend: industryReport.brandConcentrationTrend.slice(-24),
      trafficQuadrants: industryReport.trafficQuadrants.slice(0, 4).map((item) => ({
        ...item,
        examples: item.examples.slice(0, 3),
      })),
      productSignals: {
        sampleSize: industryReport.productSignals.sampleSize,
        signals: industryReport.productSignals.signals.slice(0, 20),
      },
      opportunities: industryReport.opportunities.slice(0, 20),
      dataQuality: industryReport.dataQuality,
      externalDataGaps: industryReport.externalDataGaps,
    },
    returned: 1,
    truncated: industryReport.opportunities.length > 20,
    currency: "CNY",
    monetaryUnit: "cents",
    basis: "current_top_ranking_coverage",
  };
}

export async function getMarketBrandAnalysisForAi(
  db: MarketDatabase,
  args: Record<string, unknown>,
  principal: AppPrincipal,
) {
  const { getMarketOverview } = await import("@/lib/market/database");
  const overview = await getMarketOverview(db, principal, aiFilters(args));
  return {
    filtersApplied: aiFilters(args),
    dataRange: overview.dataRange,
    brandAnalysis: overview.brandAnalysis,
    returned: overview.brandAnalysis.items.length,
    truncated: overview.brandAnalysis.items.length >= 30,
    currency: "CNY",
    monetaryUnit: "cents",
    basis: "current_top_ranking_coverage",
  };
}

export async function getMarketPriceBandAnalysisForAi(
  db: MarketDatabase,
  args: Record<string, unknown>,
  principal: AppPrincipal,
) {
  const { getMarketOverview } = await import("@/lib/market/database");
  const overview = await getMarketOverview(db, principal, aiFilters(args), { priceBandBasis: "confirmed_only" });
  return {
    filtersApplied: aiFilters(args),
    dataRange: overview.dataRange,
    priceBands: overview.priceBandSummary.slice(0, integer(args.limit, 20, 1, 50)),
    returned: Math.min(overview.priceBandSummary.length, integer(args.limit, 20, 1, 50)),
    truncated: overview.priceBandSummary.length > integer(args.limit, 20, 1, 50),
    currency: "CNY",
    monetaryUnit: "cents",
    basis: "confirmed_market_position_price_only",
  };
}

export async function getMarketPendingReviewSummaryForAi(db: MarketDatabase, args: Record<string, unknown>) {
  const pending = await listPendingMarketPrices(db, { category: optionalText(args.category, 120), page: 1, pageSize: integer(args.limit, 20, 1, 50) });
  return {
    filtersApplied: { category: optionalText(args.category, 120) ?? null },
    totalMatched: pending.pagination.total,
    returned: pending.items.length,
    truncated: pending.pagination.total > pending.items.length,
    items: pending.items.map((item) => ({
      category: item.category, skuCode: item.skuCode, rankingDimension: item.rankingDimension, month: item.month,
      productName: item.productName, brand: item.brand, candidatePriceCents: item.candidatePriceCents,
      candidatePriceSource: item.candidatePriceSource, imageContentSha256: item.imageContentSha256,
    })),
  };
}

function masterBaseSql(includeHistory = false) {
  return `WITH representatives AS MATERIALIZED (
      ${includeHistory
        ? `SELECT source.* FROM market_price_snapshots snapshot
          JOIN market_ranking_entries source ON source.id=(
            SELECT candidate.id FROM market_ranking_entries candidate
            WHERE candidate.category=snapshot.category AND candidate.scope=snapshot.scope
              AND candidate.ranking_dimension=snapshot.ranking_dimension AND candidate.sku_code=snapshot.sku_code
              AND candidate.period_end>=snapshot.month||'-01'
              AND candidate.period_end<date(snapshot.month||'-01','+1 month')
            ORDER BY candidate.period_end DESC, candidate.period_start DESC, candidate.id DESC
            LIMIT 1
          )`
        : `SELECT source.* FROM market_master_identities identity
          JOIN market_ranking_entries source ON source.id=identity.latest_entry_id`}
    )
    SELECT ${masterDetailColumnsSql()}
    FROM representatives m
    ${masterDetailJoinsSql()}`;
}

function masterDetailColumnsSql() {
  return `m.id, m.period_start, m.period_end, substr(m.period_end,1,7) month, m.category, m.scope, m.ranking_dimension, m.operation_mode,
    m.subcategory, m.rank, m.sku_code, m.product_name, m.brand, m.gmv_cents, m.quantity, m.visitors, m.conversion_bps,
    COALESCE(gt.gmv_total_cents,0) gmv_total_cents,
    m.image_url, m.product_url, COALESCE(c.status, CASE WHEN m.image_url='' THEN 'missing' ELSE 'pending' END) image_cache_status,
    COALESCE(c.content_sha256, ps.image_content_sha256, '') image_content_sha256,
    ps.source_price_cents, ps.ai_image_price_cents, ps.ai_price_type, ps.ai_confidence_bps, ps.ai_reason,
    ps.confirmed_market_price_cents official_market_price_cents,
    CASE WHEN ps.confirmation_status='ai_pending' AND ps.ai_image_price_cents IS NOT NULL THEN ps.ai_image_price_cents
      ELSE COALESCE(ps.source_price_cents, ps.average_transaction_price_cents, ps.ai_image_price_cents) END candidate_price_cents,
    CASE WHEN ps.confirmation_status='ai_pending' AND ps.ai_image_price_cents IS NOT NULL THEN 'ai_suggestion'
      WHEN ps.source_price_cents IS NOT NULL THEN 'source_table'
      WHEN ps.average_transaction_price_cents IS NOT NULL THEN 'average_transaction'
      WHEN ps.ai_image_price_cents IS NOT NULL THEN 'ai_suggestion'
      ELSE 'missing' END candidate_price_source,
    ps.average_transaction_price_cents, ps.price_low_cents, ps.price_high_cents, COALESCE(ps.confirmation_status,'missing') confirmation_status,
    bs.ai_brand suggested_brand, COALESCE(bs.status, '') brand_suggestion_status,
    CASE WHEN a.id IS NULL THEN 'pending' ELSE 'committed' END annotation_status,
    ${officialPriceBandSql("ps.confirmed_market_price_cents", {
      confirmationStatusSql: "ps.confirmation_status",
      aiPriceTypeSql: "ps.ai_price_type",
      categorySql: "m.category",
      periodEndSql: "m.period_end",
    })} price_band`;
}

function masterDetailJoinsSql() {
  return `LEFT JOIN market_sku_gmv_totals gt ON gt.sku_code=m.sku_code
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category AND ps.scope=m.scope AND ps.sku_code=m.sku_code AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
    LEFT JOIN market_brand_suggestions bs ON bs.category=m.category AND bs.scope=m.scope AND bs.ranking_dimension=m.ranking_dimension AND bs.sku_code=m.sku_code
    LEFT JOIN market_sku_annotations a ON a.category=m.category AND a.sku_code=m.sku_code
    LEFT JOIN market_image_cache c ON c.source_url=m.image_url`;
}

async function getMarketItemTrendLite(db: MarketDatabase, input: { skuCode: string; filters?: MarketOverviewFilters }) {
  const skuCode = normalizeMarketSkuCode(input.skuCode);
  const clauses = ["m.sku_code = ?"];
  const values: unknown[] = [skuCode];
  const list = (column: string, entries?: string[]) => {
    const normalized = [...new Set((entries ?? []).map((entry) => entry.trim()).filter(Boolean))].slice(0, 30);
    if (!normalized.length) return;
    clauses.push(`${column} IN (${normalized.map(() => "?").join(",")})`);
    values.push(...normalized);
  };
  list("m.category", input.filters?.categories);
  list("m.scope", input.filters?.scopes);
  list("m.ranking_dimension", input.filters?.rankingDimensions);
  list("m.operation_mode", input.filters?.operationModes);
  list("m.brand", input.filters?.brands);
  list("m.subcategory", input.filters?.subcategories);
  if (input.filters?.query) {
    const query = `%${input.filters.query}%`;
    clauses.push("(m.sku_code LIKE ? OR m.product_name LIKE ? OR m.brand LIKE ?)");
    values.push(query, query, query);
  }
  const priceBandValues = [...new Set((input.filters?.priceBands ?? []).map((entry) => entry.trim()).filter(Boolean))].slice(0, 30);
  const priceBandWhere = priceBandValues.length ? `WHERE m.price_band IN (${priceBandValues.map(() => "?").join(",")})` : "";
  if (input.filters?.startDate) { clauses.push("m.period_end>=?"); values.push(input.filters.startDate); }
  if (input.filters?.endDate) { clauses.push("m.period_start<=?"); values.push(input.filters.endDate); }
  const rows = await db.prepare(buildMarketAdminItemTrendLiteSql({
    factWhere: `WHERE ${clauses.join(" AND ")}`,
    priceBandWhere,
  })).bind(...values, ...priceBandValues).all<Record<string, string | number | null>>();
  const trendRows = rows.results ?? [];
  const totalMonths = Number(trendRows[0]?.total_months ?? 0);
  return {
    skuCode,
    totalMonths,
    truncated: totalMonths > trendRows.length,
    items: trendRows.map((row) => ({
      month: String(row.month ?? ""),
      periodStart: String(row.period_start ?? ""),
      periodEnd: String(row.period_end ?? ""),
      rank: row.rank === null ? null : Number(row.rank),
      operationMode: String(row.operation_mode ?? unknownMode),
      gmvCents: Number(row.gmv_cents ?? 0),
      quantity: Number(row.quantity ?? 0),
      visitors: Number(row.visitors ?? 0),
      conversionBps: row.conversion_bps === null ? null : Number(row.conversion_bps),
      marketPriceCents: row.market_price_cents === null ? null : Number(row.market_price_cents),
      averageTransactionPriceCents: row.average_transaction_price_cents === null ? null : Number(row.average_transaction_price_cents),
      confirmationStatus: String(row.confirmation_status ?? "missing"),
    })),
  };
}

function masterWhere(input: {
  q?: string; category?: string; categories?: string[]; rankingDimension?: string; rankingDimensions?: string[]; operationMode?: string; operationModes?: string[]; brand?: string; brands?: string[]; subcategory?: string; subcategories?: string[]; priceStatus?: string; priceStatuses?: string[];
  candidatePriceSource?: string; candidatePriceSources?: string[]; annotationStatus?: string; annotationStatuses?: string[];
}) {
  const clauses = ["1=1"];
  const values: unknown[] = [];
  const q = optionalText(input.q, 100);
  if (q) { clauses.push("(m.sku_code LIKE ? OR m.product_name LIKE ? OR m.brand LIKE ?)"); values.push(`%${q}%`, `%${q}%`, `%${q}%`); }
  const list = (multiple: string[] | undefined, legacy: string | undefined, maxLength: number) => [...new Set([...(multiple ?? []), legacy ?? ""].map((value) => value.trim().slice(0, maxLength)).filter(Boolean))].slice(0, 50);
  const addIn = (column: string, selected: string[]) => {
    if (!selected.length) return;
    clauses.push(`${column} IN (${selected.map(() => "?").join(",")})`);
    values.push(...selected);
  };
  addIn("m.category", list(input.categories, input.category, 120));
  addIn("m.ranking_dimension", list(input.rankingDimensions, input.rankingDimension, 10).filter((value) => validDimensions.has(value)));
  addIn("m.operation_mode", list(input.operationModes, input.operationMode, 20));
  addIn("m.brand", list(input.brands, input.brand, 120));
  addIn("m.subcategory", list(input.subcategories, input.subcategory, 120));
  const priceStatusClauses: Record<string, string> = {
    confirmed: "ps.confirmed_market_price_cents IS NOT NULL",
    pending: "ps.confirmed_market_price_cents IS NULL AND (ps.source_price_cents IS NOT NULL OR ps.ai_image_price_cents IS NOT NULL OR ps.average_transaction_price_cents IS NOT NULL OR ps.confirmation_status IN ('missing','ai_pending','review_pending','source_table'))",
    missing: "ps.confirmed_market_price_cents IS NULL AND ps.source_price_cents IS NULL AND ps.ai_image_price_cents IS NULL AND ps.average_transaction_price_cents IS NULL",
  };
  const priceStatuses = list(input.priceStatuses, input.priceStatus, 20).filter((value) => priceStatusClauses[value]);
  if (priceStatuses.length > 0 && priceStatuses.length < 3) clauses.push(`(${priceStatuses.map((value) => `(${priceStatusClauses[value]})`).join(" OR ")})`);
  const aiCandidateClause = `(
    (ps.confirmation_status='ai_pending' AND ps.ai_image_price_cents IS NOT NULL)
    OR (ps.source_price_cents IS NULL AND ps.average_transaction_price_cents IS NULL AND ps.ai_image_price_cents IS NOT NULL)
  )`;
  const nonAiCandidateClause = `(
    NOT (COALESCE(ps.confirmation_status, '')='ai_pending' AND ps.ai_image_price_cents IS NOT NULL)
    AND (ps.source_price_cents IS NOT NULL OR (ps.source_price_cents IS NULL AND ps.average_transaction_price_cents IS NOT NULL))
  )`;
  const candidateSources = list(input.candidatePriceSources, input.candidatePriceSource, 20).filter((value) => value === "ai" || value === "non_ai");
  if (candidateSources.length === 1) clauses.push(candidateSources[0] === "ai" ? aiCandidateClause : nonAiCandidateClause);
  if (candidateSources.length === 2) clauses.push(`(${aiCandidateClause} OR ${nonAiCandidateClause})`);
  const annotationStatuses = list(input.annotationStatuses, input.annotationStatus, 20).filter((value) => value === "committed" || value === "pending");
  if (annotationStatuses.length === 1) clauses.push(annotationStatuses[0] === "committed" ? "a.id IS NOT NULL" : "a.id IS NULL");
  return { where: clauses.join(" AND "), values };
}

function mapMasterRow(row: Record<string, string | number | null>) {
  return {
    id: Number(row.id),
    periodStart: String(row.period_start ?? ""),
    periodEnd: String(row.period_end ?? ""),
    month: String(row.month ?? ""),
    category: String(row.category ?? ""),
    scope: String(row.scope ?? ""),
    rankingDimension: String(row.ranking_dimension ?? "SKU"),
    operationMode: String(row.operation_mode ?? unknownMode),
    subcategory: String(row.subcategory ?? ""),
    rank: row.rank === null ? null : Number(row.rank),
    skuCode: String(row.sku_code ?? ""),
    productName: String(row.product_name ?? ""),
    brand: String(row.brand ?? ""),
    gmvCents: Number(row.gmv_cents ?? 0),
    gmvTotalCents: Number(row.gmv_total_cents ?? 0),
    quantity: Number(row.quantity ?? 0),
    visitors: Number(row.visitors ?? 0),
    conversionBps: row.conversion_bps === null ? null : Number(row.conversion_bps),
    imageUrl: String(row.image_url ?? ""),
    displayImageUrl: row.image_cache_status === "ready" && row.image_content_sha256
      ? `/api/market/images/${String(row.image_content_sha256)}`
      : String(row.image_url ?? ""),
    productUrl: String(row.product_url ?? ""),
    imageCacheStatus: String(row.image_cache_status ?? "missing"),
    imageContentSha256: String(row.image_content_sha256 ?? ""),
    officialMarketPriceCents: row.official_market_price_cents === null ? null : Number(row.official_market_price_cents),
    candidatePriceCents: row.candidate_price_cents === null ? null : Number(row.candidate_price_cents),
    candidatePriceSource: String(row.candidate_price_source ?? "missing"),
    averageTransactionPriceCents: row.average_transaction_price_cents === null ? null : Number(row.average_transaction_price_cents),
    priceLowCents: row.price_low_cents === null ? null : Number(row.price_low_cents),
    priceHighCents: row.price_high_cents === null ? null : Number(row.price_high_cents),
    aiImagePriceCents: row.ai_image_price_cents === null ? null : Number(row.ai_image_price_cents),
    aiPriceType: String(row.ai_price_type ?? ""),
    aiConfidenceBps: row.ai_confidence_bps === null ? null : Number(row.ai_confidence_bps),
    aiReason: String(row.ai_reason ?? ""),
    confirmationStatus: String(row.confirmation_status ?? "missing"),
    suggestedBrand: String(row.suggested_brand ?? ""),
    brandSuggestionStatus: String(row.brand_suggestion_status ?? ""),
    annotationStatus: String(row.annotation_status ?? "pending"),
    priceBand: String(row.price_band ?? unknownPriceBand),
  };
}

function aiFilters(args: Record<string, unknown>): MarketOverviewFilters {
  return {
    query: optionalText(args.query, 100),
    categories: optionalText(args.category, 120) ? [String(args.category).trim()] : undefined,
    scopes: optionalText(args.scope, 120) ? [String(args.scope).trim()] : undefined,
    rankingDimensions: optionalText(args.rankingDimension, 10) ? [dimension(String(args.rankingDimension))] : undefined,
    operationModes: optionalText(args.operationMode, 20) ? [normalizeOperationMode(String(args.operationMode))] : undefined,
    brands: optionalText(args.brand, 120) ? [String(args.brand).trim()] : undefined,
    subcategories: optionalText(args.subcategory, 120) ? [String(args.subcategory).trim()] : undefined,
    startDate: date(args.startDate),
    endDate: date(args.endDate),
  };
}

async function audit(db: MarketDatabase, actor: MarketPrincipal, action: string, entityType: string, entityId: string, before: unknown, after: unknown) {
  await db.prepare(`INSERT INTO market_master_audit_logs (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(`market-audit-${crypto.randomUUID()}`, actor.email, actor.role, action, entityType, entityId, JSON.stringify(before ?? null), JSON.stringify(after ?? null)).run();
}

function normalizeMappingTarget(kind: string, value: string) {
  const normalized = requiredText(value, 200);
  if (kind === "operation_mode") return normalizeOperationMode(normalized);
  return normalized;
}

function parseBrandOverrideIdentity(value: string): { category: string; scope: string; rankingDimension: MarketDimension; skuCode: string } | null {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    const category = typeof parsed.category === "string" ? parsed.category : "";
    const scope = typeof parsed.scope === "string" ? parsed.scope : "";
    const rankingDimension = typeof parsed.rankingDimension === "string" && validDimensions.has(parsed.rankingDimension)
      ? parsed.rankingDimension as MarketDimension
      : null;
    const skuCode = typeof parsed.skuCode === "string" ? parsed.skuCode : "";
    return category && scope && rankingDimension && skuCode ? { category, scope, rankingDimension, skuCode } : null;
  } catch { return null; }
}

function normalizeOperationMode(value: string) {
  if (/^pop$/i.test(value.trim())) return "POP";
  if (value.includes(selfOperated)) return selfOperated;
  return unknownMode;
}

function normalizePriceBandItems(items: Array<{ label: string; minCents?: number | null; maxCents?: number | null }>) {
  if (!Array.isArray(items) || items.length === 0 || items.length > 30) throw new Error("价格带配置必须包含 1 到 30 条");
  const normalized = items.map((item) => ({
    label: requiredText(item.label, 60),
    minCents: nullableInteger(item.minCents ?? null, 0, 100_000_000),
    maxCents: nullableInteger(item.maxCents ?? null, 0, 100_000_000),
  }));
  for (const item of normalized) if (item.minCents !== null && item.maxCents !== null && item.minCents >= item.maxCents) throw new Error("价格带下限必须小于上限");
  return normalized;
}

function monthsBetween(start: string, end: string) {
  const result: string[] = [];
  let year = Number(start.slice(0, 4));
  let month = Number(start.slice(5, 7));
  const endKey = Number(end.slice(0, 4)) * 12 + Number(end.slice(5, 7));
  for (let key = year * 12 + month; key <= endKey && result.length < 120; key += 1) {
    result.push(`${year.toString().padStart(4, "0")}-${month.toString().padStart(2, "0")}`);
    month += 1;
    if (month === 13) { month = 1; year += 1; }
  }
  return result;
}

function requiredText(value: unknown, maxLength: number) {
  const normalized = optionalText(value, maxLength);
  if (!normalized) throw new Error("必填字段不能为空");
  return normalized;
}

function optionalText(value: unknown, maxLength: number) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new Error("文本字段格式无效");
  const normalized = value.trim();
  if (!normalized) return undefined;
  if (Array.from(normalized).length > maxLength) throw new Error("文本字段过长");
  return normalized;
}

function date(value: unknown) {
  const text = optionalText(value, 10);
  if (!text) return undefined;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) throw new Error("日期格式必须为 YYYY-MM-DD");
  return text;
}

function requiredMonth(value: unknown) {
  const text = requiredText(value, 7);
  if (!/^\d{4}-\d{2}$/.test(text)) throw new Error("月份格式必须为 YYYY-MM");
  return text;
}

function dimension(value: unknown): MarketDimension {
  const text = requiredText(value, 3);
  if (!validDimensions.has(text)) throw new Error("榜单维度必须是 SKU 或 SPU");
  return text as MarketDimension;
}

function integer(value: unknown, fallback: number, min: number, max: number) {
  if (value === undefined || value === null || value === "") return fallback;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < min || number > max) throw new Error(`整数必须在 ${min} 到 ${max} 之间`);
  return number;
}

function nullableInteger(value: unknown, min: number, max: number) {
  if (value === undefined || value === null || value === "") return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < min || number > max) throw new Error(`整数必须在 ${min} 到 ${max} 之间`);
  return number;
}

function parseJsonArray(value: unknown) {
  try {
    const parsed = JSON.parse(String(value ?? "[]")) as unknown;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
