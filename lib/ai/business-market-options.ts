/** Market metadata DTO checks. This module never grants source/data authority.
 * Python casefold search remains the owning backend's responsibility: JavaScript
 * lowercasing is not equivalent (ß, İ, ligatures, final sigma). We validate the
 * exact q/query digest, response digest and all exact identity filters instead.
 */
import { scopeOptionsCanonical, scopeOptionsHash, scopeOptionsPrincipalKey } from "./business-scope-options";

export { scopeOptionsPrincipalKey as marketOptionsPrincipalKey };
export type MarketOptionIdentity = { platform: "京东"; category: string; scope: string; rankingDimension: "SKU" | "SPU"; priceBandFilter: string };
export type MarketOptionsQuery = Partial<MarketOptionIdentity> & { q?: string };
export type MarketOption = {
  optionKey: string; identity: MarketOptionIdentity; source: "market_daily_top"; sourceDataset: "market_daily_top";
  dateMetadata: { kind: "published_import_envelope"; firstDate: string; lastDate: string; snapshotDate: null; coverageVerified: false };
  provenance: { kind: "completed_import_metadata"; revision: string; generation: string; directoryDigest: string; meaning: "historically_published_not_current_fact_coverage" };
};
export type MarketOptionsPage = {
  schemaVersion: "business-analysis-options-v1"; domain: "market"; authorityVerified: true;
  revision: string; directoryGeneration: string; directoryDigest: string;
  query: MarketOptionsQuery; queryDigest: string; items: MarketOption[];
  pagination: { returned: number; limit: 20; hasMore: boolean; nextCursor: string | null };
  limitations: string[]; pageDigest: string;
};
export type MarketOptionsExpected = {
  principalKey: string; query: MarketOptionsQuery; cursor?: string | null;
  revision?: string; directoryGeneration?: string; directoryDigest?: string; previousItem?: MarketOption;
};
export type MarketOptionSelection = {
  principalKey: string; revision: string; directoryGeneration: string; directoryDigest: string;
  queryDigest: string; optionKey: string; identity: MarketOptionIdentity;
};
export type MarketCondition = { [K in keyof MarketOptionIdentity]: string };
const validatedPages = new WeakMap<MarketOptionsPage, { principalKey: string; canonical: string }>();
const encoder = new TextEncoder(), sha = /^[a-f0-9]{64}$/, generation = /^[a-f0-9]{32}$/;
const revision = /^(0|[1-9]\d*):[a-f0-9]{12}$/;
const identityKeys = ["platform", "category", "scope", "rankingDimension", "priceBandFilter"] as const;
function check(value: unknown, message = "市场来源选项范围或完整性无效，请重新读取。"): asserts value { if (!value) throw new Error(message); }
function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}
function fields(value: unknown, names: readonly string[]): asserts value is Record<string, unknown> {
  check(object(value) && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name)));
}
function text(value: unknown, maximum = 200): asserts value is string {
  // Python str.strip excludes U+FEFF, unlike JavaScript trim. Cc is forbidden
  // separately, leaving Zs/line/paragraph separators as boundary whitespace.
  check(typeof value === "string" && [...value].length > 0 && [...value].length <= maximum
    && !/[\p{Cc}\p{Cs}]/u.test(value) && !/^[\p{Zs}\u2028\u2029]|[\p{Zs}\u2028\u2029]$/u.test(value));
}
function date(value: unknown): asserts value is string {
  check(typeof value === "string" && /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)
    && value >= "0001-01-01" && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value);
}
function cursor(value: unknown): asserts value is string {
  text(value, 1600); check(/^\.?[A-Za-z0-9_-]+:[0-9A-Za-z]+:[A-Za-z0-9_-]+$/.test(value));
}
function compareText(a: string, b: string) {
  const left = [...a], right = [...b];
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const diff = left[i].codePointAt(0)! - right[i].codePointAt(0)!; if (diff) return diff;
  }
  return left.length - right.length;
}
function compareIdentity(a: MarketOptionIdentity, b: MarketOptionIdentity) {
  for (const key of identityKeys) { const diff = compareText(a[key], b[key]); if (diff) return diff; } return 0;
}
export function marketOptionsQuery(raw: unknown): MarketOptionsQuery {
  // The shared bounded codec rejects getters, unknown types and deep structures
  // before any property is accessed or asynchronous digest begins.
  const copy: unknown = JSON.parse(scopeOptionsCanonical(raw));
  check(object(copy) && Object.keys(copy).every(key => [...identityKeys, "q"].includes(key)));
  for (const [key, value] of Object.entries(copy)) text(value, key === "q" ? 100 : 200);
  if (copy.platform !== undefined) check(copy.platform === "京东");
  if (copy.rankingDimension !== undefined) check(copy.rankingDimension === "SKU" || copy.rankingDimension === "SPU");
  return copy as MarketOptionsQuery;
}
function identity(raw: unknown): MarketOptionIdentity {
  fields(raw, identityKeys); return marketOptionsQuery(raw) as MarketOptionIdentity;
}
async function item(raw: unknown, binding: { revision: string; generation: string; digest: string; query: MarketOptionsQuery }): Promise<MarketOption> {
  fields(raw, ["optionKey", "identity", "source", "sourceDataset", "dateMetadata", "provenance"]);
  const chosen = identity(raw.identity);
  check(raw.source === "market_daily_top" && raw.sourceDataset === "market_daily_top");
  for (const key of identityKeys) check(binding.query[key] === undefined || chosen[key] === binding.query[key]);
  check(typeof raw.optionKey === "string" && sha.test(raw.optionKey)
    && raw.optionKey === await scopeOptionsHash({ domain: "market", identity: chosen }));
  fields(raw.dateMetadata, ["kind", "firstDate", "lastDate", "snapshotDate", "coverageVerified"]);
  const dates = raw.dateMetadata;
  check(dates.kind === "published_import_envelope" && dates.coverageVerified === false && dates.snapshotDate === null);
  date(dates.firstDate); date(dates.lastDate); check(dates.firstDate <= dates.lastDate);
  fields(raw.provenance, ["kind", "revision", "generation", "directoryDigest", "meaning"]);
  check(raw.provenance.kind === "completed_import_metadata" && raw.provenance.revision === binding.revision
    && raw.provenance.generation === binding.generation && raw.provenance.directoryDigest === binding.digest
    && raw.provenance.meaning === "historically_published_not_current_fact_coverage");
  return raw as MarketOption;
}
export async function validateMarketOptionsResponse(raw: unknown, expected: MarketOptionsExpected): Promise<MarketOptionsPage> {
  const copy: unknown = JSON.parse(scopeOptionsCanonical(raw));
  const fixed = { principalKey: expected.principalKey, query: marketOptionsQuery(expected.query), cursor: expected.cursor ?? null,
    revision: expected.revision, generation: expected.directoryGeneration, digest: expected.directoryDigest,
    previous: expected.previousItem ? JSON.parse(scopeOptionsCanonical(expected.previousItem)) : undefined };
  check(typeof fixed.principalKey === "string" && sha.test(fixed.principalKey));
  if (fixed.cursor !== null) {
    cursor(fixed.cursor);
    check(fixed.revision !== undefined && fixed.generation !== undefined && fixed.digest !== undefined && fixed.previous,
      "下一页必须绑定先前完整页的版本、目录及末项。");
  } else check(fixed.previous === undefined);
  if (fixed.revision !== undefined) check(typeof fixed.revision === "string" && revision.test(fixed.revision));
  if (fixed.generation !== undefined) check(typeof fixed.generation === "string" && generation.test(fixed.generation));
  if (fixed.digest !== undefined) check(typeof fixed.digest === "string" && sha.test(fixed.digest));
  fields(copy, ["schemaVersion", "principalKey", "page"]);
  check(copy.schemaVersion === "business-market-options-response-v1" && copy.principalKey === fixed.principalKey,
    "账号已变化，请刷新当前账号后重新选择市场来源。");
  const page = copy.page;
  fields(page, ["schemaVersion", "domain", "authorityVerified", "revision", "directoryGeneration", "directoryDigest",
    "query", "queryDigest", "items", "pagination", "limitations", "pageDigest"]);
  check(encoder.encode(scopeOptionsCanonical(page)).length <= 38000, "完整市场来源页超过容量。");
  check(page.schemaVersion === "business-analysis-options-v1" && page.domain === "market" && page.authorityVerified === true);
  check(typeof page.revision === "string" && revision.test(page.revision) && [...page.revision].length <= 160);
  check(typeof page.directoryGeneration === "string" && generation.test(page.directoryGeneration));
  check(typeof page.directoryDigest === "string" && sha.test(page.directoryDigest));
  check((fixed.revision === undefined || fixed.revision === page.revision)
    && (fixed.generation === undefined || fixed.generation === page.directoryGeneration)
    && (fixed.digest === undefined || fixed.digest === page.directoryDigest), "市场目录版本已变化，请从首页重读。");
  check(scopeOptionsCanonical(marketOptionsQuery(page.query)) === scopeOptionsCanonical(fixed.query));
  check(typeof page.queryDigest === "string" && sha.test(page.queryDigest) && await scopeOptionsHash(page.query) === page.queryDigest);
  const material = { ...page }; delete material.pageDigest;
  check(typeof page.pageDigest === "string" && sha.test(page.pageDigest) && await scopeOptionsHash(material) === page.pageDigest);
  check(Array.isArray(page.items) && page.items.length <= 20);
  fields(page.pagination, ["returned", "limit", "hasMore", "nextCursor"]);
  check(page.pagination.returned === page.items.length && page.pagination.limit === 20 && typeof page.pagination.hasMore === "boolean");
  if (page.pagination.hasMore) { check(page.items.length === 20); cursor(page.pagination.nextCursor); check(page.pagination.nextCursor !== fixed.cursor); }
  else check(page.pagination.nextCursor === null);
  check(Array.isArray(page.limitations) && page.limitations.length >= 1 && page.limitations.length <= 10);
  for (const value of page.limitations) text(value, 500);
  const binding = { revision: page.revision, generation: page.directoryGeneration, digest: page.directoryDigest, query: fixed.query };
  let previous = fixed.previous ? await item(fixed.previous, binding) : undefined;
  for (const candidate of page.items) {
    const current = await item(candidate, binding);
    if (previous) check(compareIdentity(previous.identity, current.identity) < 0);
    previous = current;
  }
  const result = page as MarketOptionsPage;
  validatedPages.set(result, { principalKey: fixed.principalKey, canonical: scopeOptionsCanonical(result) });
  return result;
}

/** Only a still-unchanged page returned by the validator may supply a choice. */
export function marketOptionSelection(page: MarketOptionsPage, chosen: MarketOption, principalKey: string): MarketOptionSelection {
  const fixed = validatedPages.get(page);
  check(fixed && fixed.principalKey === principalKey && fixed.canonical === scopeOptionsCanonical(page),
    "市场来源页未经当前账号核验或已变化，请重新读取。");
  const selected = page.items.find(value => value.optionKey === chosen.optionKey);
  check(selected && scopeOptionsCanonical(selected) === scopeOptionsCanonical(chosen));
  return { principalKey, revision: page.revision, directoryGeneration: page.directoryGeneration,
    directoryDigest: page.directoryDigest, queryDigest: page.queryDigest, optionKey: selected.optionKey,
    identity: identity(selected.identity) };
}

/** Preserve the user's existing exact fields, including unfinished manual rows. */
export function mergeMarketOption(markets: readonly MarketCondition[], rawSelection: MarketOptionSelection, principalKey: string): MarketCondition[] {
  const selection: unknown = JSON.parse(scopeOptionsCanonical(rawSelection));
  fields(selection, ["principalKey", "revision", "directoryGeneration", "directoryDigest", "queryDigest", "optionKey", "identity"]);
  check(typeof principalKey === "string" && sha.test(principalKey) && selection.principalKey === principalKey,
    "账号已变化，不能添加上一账号的市场来源。");
  check(typeof selection.revision === "string" && selection.revision.length <= 160 && revision.test(selection.revision));
  check(typeof selection.directoryGeneration === "string" && generation.test(selection.directoryGeneration));
  for (const key of ["directoryDigest", "queryDigest", "optionKey"]) check(typeof selection[key] === "string" && sha.test(selection[key]));
  const added = identity(selection.identity);
  check(Array.isArray(markets) && markets.length <= 7);
  const next = markets.map(raw => {
    const value: unknown = JSON.parse(scopeOptionsCanonical(raw)); fields(value, identityKeys);
    for (const key of identityKeys) check(typeof value[key] === "string" && [...value[key]].length <= 200 && !/[\p{Cc}\p{Cs}]/u.test(value[key]));
    return value as MarketCondition;
  });
  const keys = next.map(value => scopeOptionsCanonical(value));
  check(new Set(keys).size === keys.length, "已有市场条件重复，请明确调整后再添加。");
  if (keys.includes(scopeOptionsCanonical(added))) return next;
  check(next.length < 7, "最多选择7项市场条件，请明确调整原范围后再添加。");
  return [...next, added];
}
