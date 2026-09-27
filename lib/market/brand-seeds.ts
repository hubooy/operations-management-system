import type { MarketEntryForImport } from "@/lib/market/import-core";
import type { MarketSchemaDatabase } from "@/lib/market/schema-core";

export type MarketBrandMatchPolicy = "title_prefix" | "title_anywhere";
export type MarketBrandSeed = {
  id: string;
  canonicalBrand: string;
  seedText: string;
  normalizedSeed: string;
  source: "system" | "manual";
  sourceRef: string;
  status: "enabled" | "disabled";
};

type SeedRow = {
  id: string; canonical_brand: string; seed_text: string; normalized_seed: string;
  source: string; source_ref: string; status: string;
};

type UnknownBrandRow = {
  category: string; scope: string; ranking_dimension: string; sku_code: string;
  product_name: string; product_url: string; raw_json: string;
};

const ignoredSystemBrands = new Set(["配件", "赠品", "包材", "邮费", "其他", "无品牌", "暂无", "不适用"]);
const punctuation = /[\s\u00a0·•・|｜/\\()[\]{}（）【】《》〈〉“”‘’"'，,。.：:；;！!？?+_—–-]+/gu;

export function normalizeMarketBrandSeed(value: unknown) {
  return String(value ?? "").normalize("NFKC").toLocaleLowerCase("zh-CN").replace(punctuation, " ").trim().replace(/\s+/g, " ");
}

function compact(value: string) { return value.replace(/\s+/g, ""); }

function seedIndex(title: string, seed: string) {
  const asciiSeed = /^[a-z0-9 ]+$/i.test(seed);
  let offset = 0;
  while (offset <= title.length - seed.length) {
    const index = title.indexOf(seed, offset);
    if (index < 0) return -1;
    const previous = title[index - 1] ?? "";
    const next = title[index + seed.length] ?? "";
    if (!asciiSeed || (!/[a-z0-9]/i.test(previous) && !/[a-z0-9]/i.test(next))) return index;
    offset = index + 1;
  }
  return -1;
}

function rawText(raw: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function parseRaw(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== "string" || !value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch { return {}; }
}

export function marketBrandMatchPolicy(input: { raw?: unknown; scope?: string; operationMode?: string }): MarketBrandMatchPolicy {
  const raw = parseRaw(input.raw);
  const source = normalizeMarketBrandSeed([
    rawText(raw, "店铺规则", "店铺类型", "店铺等级", "经营模式"),
    input.scope ?? "",
    input.operationMode ?? "",
  ].join(" "));
  if (/(^|\s)c\s*店($|\s)|c\s*(?:shop|store)|\bpop\b/i.test(source)) return "title_anywhere";
  if (/(^|\s)b\s*店($|\s)|b\s*(?:shop|store)|京东自营|自营|\bself\b/i.test(source)) return "title_prefix";
  return "title_prefix";
}

export function matchMarketBrandTitle(title: string, seeds: MarketBrandSeed[], policy: MarketBrandMatchPolicy) {
  const normalizedTitle = normalizeMarketBrandSeed(title);
  const compactTitle = compact(normalizedTitle);
  const candidates: Array<{ seed: MarketBrandSeed; index: number; length: number }> = [];
  for (const seed of seeds) {
    const normalizedSeed = seed.normalizedSeed || normalizeMarketBrandSeed(seed.seedText);
    if (!normalizedSeed) continue;
    const compactSeed = compact(normalizedSeed);
    const normalizedIndex = seedIndex(normalizedTitle, normalizedSeed);
    const compactIndex = seedIndex(compactTitle, compactSeed);
    const index = normalizedIndex >= 0 ? normalizedIndex : compactIndex;
    if (index < 0 || (policy === "title_prefix" && index !== 0)) continue;
    candidates.push({ seed, index, length: compactSeed.length });
  }
  candidates.sort((left, right) => left.index - right.index || right.length - left.length || left.seed.normalizedSeed.localeCompare(right.seed.normalizedSeed, "zh-CN"));
  const best = candidates[0]?.seed;
  return best ? { brand: best.canonicalBrand, seedText: best.seedText, policy } : null;
}

export async function loadEnabledMarketBrandSeeds(db: MarketSchemaDatabase): Promise<MarketBrandSeed[]> {
  const result = await db.prepare(`SELECT id, canonical_brand, seed_text, normalized_seed, source, source_ref, status
    FROM market_brand_seeds WHERE status='enabled'
    ORDER BY length(normalized_seed) DESC, normalized_seed, id`).all<SeedRow>();
  return (result.results ?? []).map((row) => ({
    id: row.id,
    canonicalBrand: row.canonical_brand,
    seedText: row.seed_text,
    normalizedSeed: row.normalized_seed,
    source: row.source === "system" ? "system" : "manual",
    sourceRef: row.source_ref,
    status: row.status === "disabled" ? "disabled" : "enabled",
  }));
}

export async function matchImportedMarketBrands(db: MarketSchemaDatabase, rows: MarketEntryForImport[]) {
  const seeds = await loadMarketBrandSeedsForImport(db);
  let matched = 0;
  let prefixMatched = 0;
  let anywhereMatched = 0;
  const nextRows = rows.map((row) => {
    if (row.brand.trim()) return row;
    const policy = marketBrandMatchPolicy({ raw: row.raw, scope: row.scope, operationMode: row.operationMode });
    const result = matchMarketBrandTitle(row.productName, seeds, policy);
    if (!result) return row;
    matched += 1;
    if (policy === "title_prefix") prefixMatched += 1;
    else anywhereMatched += 1;
    return { ...row, brand: result.brand };
  });
  return {
    rows: nextRows,
    systemSeedSnapshot: seeds.filter((seed) => seed.source === "system" && seed.status === "enabled"),
    summary: { seedCount: seeds.length, matched, prefixMatched, anywhereMatched, unmatched: nextRows.filter((row) => !row.brand.trim()).length },
  };
}

async function tableExists(db: MarketSchemaDatabase, table: string) {
  const row = await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1").bind(table).first<{ name: string }>();
  return Boolean(row?.name);
}

async function discoverSystemMarketBrandSeeds(db: MarketSchemaDatabase) {
  const discovered = new Map<string, { canonicalBrand: string; refs: Set<string> }>();
  const sources: Array<{ table: string; sql: string; ref: string }> = [
    { table: "erp_product_master", sql: "SELECT DISTINCT trim(brand) brand FROM erp_product_master WHERE trim(brand)<>''", ref: "erp_product_master" },
    { table: "inventory_stock_lines", sql: "SELECT DISTINCT trim(brand) brand FROM inventory_stock_lines WHERE trim(brand)<>''", ref: "inventory_stock_lines" },
    { table: "market_ranking_entries", sql: "SELECT DISTINCT trim(brand) brand FROM market_ranking_entries WHERE trim(brand)<>''", ref: "market_confirmed_brand" },
    { table: "market_master_mapping_rules", sql: "SELECT DISTINCT trim(target_value) brand FROM market_master_mapping_rules WHERE kind IN ('brand_alias','brand_override') AND status='published' AND trim(target_value)<>''", ref: "market_brand_mapping" },
    { table: "market_netshop_projection", sql: `SELECT DISTINCT trim(brand) brand
      FROM market_netshop_active_projection WHERE kind='brand' AND trim(brand)<>''`, ref: "netshop_product_master" },
  ];
  for (const source of sources) {
    if (!await tableExists(db, source.table)) continue;
    const rows = await db.prepare(source.sql).all<{ brand: string }>();
    for (const row of rows.results ?? []) {
      const canonicalBrand = String(row.brand ?? "").trim().slice(0, 120);
      const normalized = normalizeMarketBrandSeed(canonicalBrand);
      if (!normalized || ignoredSystemBrands.has(canonicalBrand)) continue;
      const current = discovered.get(normalized) ?? { canonicalBrand, refs: new Set<string>() };
      current.refs.add(source.ref);
      discovered.set(normalized, current);
    }
  }
  return discovered;
}

export async function loadMarketBrandSeedsForImport(db: MarketSchemaDatabase): Promise<MarketBrandSeed[]> {
  const [enabled, discovered, manualRows] = await Promise.all([
    loadEnabledMarketBrandSeeds(db),
    discoverSystemMarketBrandSeeds(db),
    db.prepare("SELECT normalized_seed FROM market_brand_seeds WHERE source='manual'").all<{ normalized_seed: string }>(),
  ]);
  const blockedByManual = new Set((manualRows.results ?? []).map((row) => row.normalized_seed));
  const merged = new Map(enabled.filter((seed) => seed.source === "manual")
    .map((seed) => [seed.normalizedSeed, seed]));
  for (const [normalizedSeed, value] of discovered) {
    if (merged.has(normalizedSeed) || blockedByManual.has(normalizedSeed)) continue;
    merged.set(normalizedSeed, {
      id: `market-brand-seed-discovered-${normalizedSeed}`,
      canonicalBrand: value.canonicalBrand,
      seedText: value.canonicalBrand,
      normalizedSeed,
      source: "system",
      sourceRef: [...value.refs].sort().join(","),
      status: "enabled",
    });
  }
  return [...merged.values()].sort((left, right) => right.normalizedSeed.length - left.normalizedSeed.length
    || left.normalizedSeed.localeCompare(right.normalizedSeed, "zh-CN") || left.id.localeCompare(right.id));
}

function discoveredFromSystemSeedSnapshot(seeds: readonly MarketBrandSeed[]) {
  const discovered = new Map<string, { canonicalBrand: string; refs: Set<string> }>();
  for (const seed of seeds) {
    if (seed.source !== "system" || seed.status !== "enabled" || !seed.normalizedSeed) continue;
    discovered.set(seed.normalizedSeed, {
      canonicalBrand: seed.canonicalBrand,
      refs: new Set(seed.sourceRef.split(",").map((value) => value.trim()).filter(Boolean)),
    });
  }
  return discovered;
}

export async function refreshSystemMarketBrandSeeds(
  db: MarketSchemaDatabase,
  actorEmail: string,
  options: { systemSeedSnapshot?: readonly MarketBrandSeed[] } = {},
) {
  const discovered = options.systemSeedSnapshot
    ? discoveredFromSystemSeedSnapshot(options.systemSeedSnapshot)
    : await discoverSystemMarketBrandSeeds(db);
  const existingRows = await db.prepare("SELECT id, canonical_brand, normalized_seed, source FROM market_brand_seeds").all<{
    id: string; canonical_brand: string; normalized_seed: string; source: string;
  }>();
  const existing = new Map((existingRows.results ?? []).map((row) => [row.normalized_seed, row]));
  let inserted = 0;
  let refreshed = 0;
  let manualPreserved = 0;
  let disabled = 0;
  const incoming: Array<{ id: string; canonicalBrand: string; normalizedSeed: string; sourceRef: string }> = [];
  for (const [normalized, value] of [...discovered.entries()].sort(([left], [right]) => left.localeCompare(right, "zh-CN"))) {
    const current = existing.get(normalized);
    if (current?.source === "manual") { manualPreserved += 1; continue; }
    if (current) refreshed += 1;
    else inserted += 1;
    incoming.push({
      id: current?.id ?? `market-brand-seed-${crypto.randomUUID()}`,
      canonicalBrand: value.canonicalBrand,
      normalizedSeed: normalized,
      sourceRef: [...value.refs].sort().join(","),
    });
  }
  for (const current of existingRows.results ?? []) {
    if (current.source !== "system" || discovered.has(current.normalized_seed)) continue;
    disabled += 1;
  }
  const payload = JSON.stringify(incoming);
  if (new TextEncoder().encode(payload).length > 1_500_000) throw new Error("系统品牌种子过多，无法在单次事务内安全刷新");
  await db.batch([
    db.prepare(`INSERT INTO market_brand_seeds
      (id, canonical_brand, seed_text, normalized_seed, source, source_ref, status, created_by, last_refreshed_at, updated_at)
      SELECT json_extract(value, '$.id'), json_extract(value, '$.canonicalBrand'),
        json_extract(value, '$.canonicalBrand'), json_extract(value, '$.normalizedSeed'),
        'system', json_extract(value, '$.sourceRef'), 'enabled', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM json_each(?) WHERE 1=1
      ON CONFLICT(normalized_seed) DO UPDATE SET canonical_brand=excluded.canonical_brand,
        seed_text=excluded.seed_text, source_ref=excluded.source_ref, status='enabled',
        last_refreshed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
      WHERE market_brand_seeds.source='system'`).bind(actorEmail, payload),
    db.prepare(`UPDATE market_brand_seeds
      SET status='disabled', last_refreshed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
      WHERE source='system' AND NOT EXISTS (
        SELECT 1 FROM json_each(?) incoming
        WHERE json_extract(incoming.value, '$.normalizedSeed')=market_brand_seeds.normalized_seed
      )`).bind(payload),
  ]);
  return { discovered: discovered.size, inserted, refreshed, disabled, manualPreserved };
}

export async function upsertManualMarketBrandSeed(db: MarketSchemaDatabase, input: {
  canonicalBrand: string; seedText: string; actorEmail: string;
}) {
  const canonicalBrand = input.canonicalBrand.trim().slice(0, 120);
  const seedText = input.seedText.trim().slice(0, 120);
  const normalizedSeed = normalizeMarketBrandSeed(seedText);
  if (!canonicalBrand) throw new Error("品牌名称不能为空");
  if (!normalizedSeed) throw new Error("品牌种子不能为空");
  const before = await db.prepare("SELECT * FROM market_brand_seeds WHERE normalized_seed=? LIMIT 1").bind(normalizedSeed).first<Record<string, unknown>>();
  const id = String(before?.id ?? `market-brand-seed-${crypto.randomUUID()}`);
  await db.prepare(`INSERT INTO market_brand_seeds
    (id, canonical_brand, seed_text, normalized_seed, source, source_ref, status, created_by, updated_at)
    VALUES (?, ?, ?, ?, 'manual', 'manual_entry', 'enabled', ?, CURRENT_TIMESTAMP)
    ON CONFLICT(normalized_seed) DO UPDATE SET canonical_brand=excluded.canonical_brand,
      seed_text=excluded.seed_text, source='manual', source_ref='manual_entry', status='enabled',
      created_by=excluded.created_by, updated_at=CURRENT_TIMESTAMP`)
    .bind(id, canonicalBrand, seedText, normalizedSeed, input.actorEmail).run();
  const after = await db.prepare("SELECT * FROM market_brand_seeds WHERE id=? LIMIT 1").bind(id).first<Record<string, unknown>>();
  return { id, before, after };
}

export async function listMarketBrandSeeds(db: MarketSchemaDatabase, input: { q?: string } = {}) {
  const q = input.q?.trim().slice(0, 100) ?? "";
  const like = `%${q}%`;
  const rows = await db.prepare(`SELECT id, canonical_brand, seed_text, normalized_seed, source, source_ref, status,
      created_by, created_at, updated_at, last_refreshed_at
    FROM market_brand_seeds
    WHERE (?='' OR canonical_brand LIKE ? OR seed_text LIKE ?)
    ORDER BY status='enabled' DESC, source='manual' DESC, canonical_brand, seed_text LIMIT 500`)
    .bind(q, like, like).all<Record<string, string | number | null>>();
  const counts = await db.prepare(`SELECT COUNT(*) total,
      SUM(CASE WHEN status='enabled' THEN 1 ELSE 0 END) enabled,
      SUM(CASE WHEN source='system' THEN 1 ELSE 0 END) system,
      SUM(CASE WHEN source='manual' THEN 1 ELSE 0 END) manual
    FROM market_brand_seeds`).first<Record<string, number | null>>();
  return { items: rows.results ?? [], counts: {
    total: Number(counts?.total ?? 0), enabled: Number(counts?.enabled ?? 0),
    system: Number(counts?.system ?? 0), manual: Number(counts?.manual ?? 0),
  } };
}

export async function listUnknownMarketBrands(db: MarketSchemaDatabase, input: {
  q?: string; category?: string; page?: number; pageSize?: number;
} = {}) {
  const q = input.q?.trim().slice(0, 100) ?? "";
  const category = input.category?.trim().slice(0, 120) ?? "";
  const page = Math.max(1, Math.min(10_000, Math.trunc(input.page ?? 1)));
  const pageSize = Math.max(1, Math.min(100, Math.trunc(input.pageSize ?? 30)));
  const like = `%${q}%`;
  const base = `WITH ranked AS (
      SELECT m.category, m.scope, m.ranking_dimension, m.sku_code, m.product_name, m.product_url, m.raw_json,
        ROW_NUMBER() OVER (PARTITION BY m.category, m.scope, m.ranking_dimension, m.sku_code ORDER BY m.period_end DESC, m.id DESC) rn
      FROM market_ranking_entries m WHERE trim(m.brand)=''
    )`;
  const where = `rn=1 AND (?='' OR category=?) AND (?='' OR sku_code LIKE ? OR product_name LIKE ?)`;
  const values = [category, category, q, like, like];
  const total = await db.prepare(`${base} SELECT COUNT(*) count FROM ranked WHERE ${where}`).bind(...values).first<{ count: number }>();
  const rows = await db.prepare(`${base} SELECT category, scope, ranking_dimension, sku_code, product_name, product_url, raw_json
    FROM ranked WHERE ${where} ORDER BY category, scope, ranking_dimension, sku_code LIMIT ? OFFSET ?`)
    .bind(...values, pageSize, (page - 1) * pageSize).all<UnknownBrandRow>();
  return {
    items: (rows.results ?? []).map((row) => {
      const raw = parseRaw(row.raw_json);
      return {
        category: row.category, scope: row.scope, rankingDimension: row.ranking_dimension,
        skuCode: row.sku_code, productName: row.product_name, productUrl: row.product_url,
        storeName: rawText(raw, "店铺名称", "所属店铺", "店铺"),
        storeType: rawText(raw, "店铺规则", "店铺类型", "店铺等级") || row.scope,
        matchPolicy: marketBrandMatchPolicy({ raw, scope: row.scope }),
      };
    }),
    pagination: { page, pageSize, total: Number(total?.count ?? 0), pageCount: Math.max(1, Math.ceil(Number(total?.count ?? 0) / pageSize)) },
  };
}

export async function matchExistingUnknownMarketBrands(db: MarketSchemaDatabase, input: { category?: string } = {}) {
  const category = input.category?.trim().slice(0, 120) ?? "";
  const seeds = await loadEnabledMarketBrandSeeds(db);
  const rows = await db.prepare(`WITH ranked AS (
      SELECT m.category, m.scope, m.ranking_dimension, m.sku_code, m.product_name, m.raw_json,
        ROW_NUMBER() OVER (PARTITION BY m.category, m.scope, m.ranking_dimension, m.sku_code ORDER BY m.period_end DESC, m.id DESC) rn
      FROM market_ranking_entries m WHERE trim(m.brand)=''
    )
    SELECT category, scope, ranking_dimension, sku_code, product_name, raw_json
    FROM ranked WHERE rn=1 AND (?='' OR category=?)
    ORDER BY category, scope, ranking_dimension, sku_code LIMIT 100000`)
    .bind(category, category).all<UnknownBrandRow>();
  const candidates = rows.results ?? [];
  const matched = candidates.flatMap((row) => {
    const raw = parseRaw(row.raw_json);
    const policy = marketBrandMatchPolicy({ raw, scope: row.scope });
    const result = matchMarketBrandTitle(row.product_name, seeds, policy);
    return result ? [{ ...row, ...result }] : [];
  });
  let changedRows = 0;
  for (let offset = 0; offset < matched.length; offset += 80) {
    const result = await db.batch(matched.slice(offset, offset + 80).map((row) => db.prepare(`UPDATE market_ranking_entries
      SET brand=?, source_brand=CASE WHEN source_brand='' THEN ? ELSE source_brand END, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=? AND trim(brand)=''`)
      .bind(row.brand, row.brand, row.category, row.scope, row.ranking_dimension, row.sku_code))) as Array<{ meta?: { changes?: number } }>;
    changedRows += result.reduce((sum, item) => sum + Number(item.meta?.changes ?? 0), 0);
  }
  return {
    seedCount: seeds.length,
    scanned: candidates.length,
    matchedSkuCount: matched.length,
    changedRows,
    remainingSkuCount: candidates.length - matched.length,
    prefixMatched: matched.filter((row) => row.policy === "title_prefix").length,
    anywhereMatched: matched.filter((row) => row.policy === "title_anywhere").length,
  };
}

export async function applyManualBrandSeedToIdentity(db: MarketSchemaDatabase, input: {
  category: string; scope: string; rankingDimension: string; skuCode: string; brand: string;
}) {
  const result = await db.prepare(`UPDATE market_ranking_entries
    SET brand=?, source_brand=CASE WHEN source_brand='' THEN ? ELSE source_brand END, updated_at=CURRENT_TIMESTAMP
    WHERE category=? AND scope=? AND ranking_dimension=? AND sku_code=? AND trim(brand)=''`)
    .bind(input.brand, input.brand, input.category, input.scope, input.rankingDimension, input.skuCode).run() as { meta?: { changes?: number } };
  return Number(result.meta?.changes ?? 0);
}
