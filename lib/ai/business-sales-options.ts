/** ERP exact-source metadata checks; a verified DTO does not grant business-data authority. */
import { scopeOptionsCanonical, scopeOptionsHash, scopeOptionsPrincipalKey } from "./business-scope-options";

export { scopeOptionsPrincipalKey as salesOptionsPrincipalKey };
export type SalesOptionIdentity = { platform: string; shop: string; channel: string };
export type SalesOptionsQuery = Partial<SalesOptionIdentity>;
export type SalesOption = {
  optionKey: string; identity: SalesOptionIdentity; source: "erp_sales"; sourceDataset: "sales_order_lines";
  dateMetadata: { kind: "current_fact_business_date_envelope"; firstDate: string; lastDate: string; snapshotDate: null; coverageVerified: false };
  provenance: { kind: "current_exact_business_projection"; revision: string; meaning: "observed_current_identity_not_complete_period_coverage" };
};
export type SalesOptionsPage = {
  schemaVersion: "business-analysis-options-v1"; domain: "sales"; authorityVerified: true;
  revision: string; directoryGeneration: string; directoryDigest: string;
  query: SalesOptionsQuery; queryDigest: string; items: SalesOption[];
  pagination: { returned: number; limit: 20; hasMore: boolean; nextCursor: string | null };
  limitations: string[]; pageDigest: string;
};
export type SalesOptionsExpected = {
  principalKey: string; query: SalesOptionsQuery; cursor?: string | null;
  revision?: string; directoryGeneration?: string; directoryDigest?: string; previousItem?: SalesOption;
};
export type SalesOptionSelection = {
  principalKey: string; revision: string; directoryGeneration: string; directoryDigest: string;
  queryDigest: string; optionKey: string; identity: SalesOptionIdentity;
};
export type SalesScopeShop = { platform: string; shop: string; datasets: string[]; salesChannels: string[] };
const validatedPages = new WeakMap<SalesOptionsPage, { principalKey: string; canonical: string }>();
const validatedSelections = new WeakMap<SalesOptionSelection, string>();
const encoder = new TextEncoder(), sha = /^[a-f0-9]{64}$/, generation = /^[a-f0-9]{32}$/;
const revision = /^(0|[1-9]\d*):(0|[1-9]\d*)$/;
function validRevision(value: unknown): value is string {
  return typeof value === "string" && value.length <= 64 && revision.test(value)
    && value.split(":").every(part => Number.isSafeInteger(Number(part)));
}
const identityKeys = ["platform", "shop", "channel"] as const;
function check(value: unknown, message = "ERP来源选项范围或完整性无效，请重新读取。"): asserts value { if (!value) throw new Error(message); }
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
function compareIdentity(a: SalesOptionIdentity, b: SalesOptionIdentity) {
  for (const key of identityKeys) { const diff = compareText(a[key], b[key]); if (diff) return diff; } return 0;
}
export function salesOptionsQuery(raw: unknown): SalesOptionsQuery {
  // The shared bounded codec rejects getters, unknown types and deep structures
  // before any property is accessed or asynchronous digest begins.
  const copy: unknown = JSON.parse(scopeOptionsCanonical(raw));
  check(object(copy) && Object.keys(copy).every(key => identityKeys.includes(key as typeof identityKeys[number])));
  for (const value of Object.values(copy)) text(value);
  if (copy.shop !== undefined) check(copy.platform !== undefined);
  return copy as SalesOptionsQuery;
}
function identity(raw: unknown): SalesOptionIdentity {
  fields(raw, identityKeys); return salesOptionsQuery(raw) as SalesOptionIdentity;
}
async function item(raw: unknown, binding: { revision: string; generation: string; digest: string; query: SalesOptionsQuery }): Promise<SalesOption> {
  fields(raw, ["optionKey", "identity", "source", "sourceDataset", "dateMetadata", "provenance"]);
  const chosen = identity(raw.identity);
  check(raw.source === "erp_sales" && raw.sourceDataset === "sales_order_lines");
  for (const key of identityKeys) check(binding.query[key] === undefined || chosen[key] === binding.query[key]);
  check(typeof raw.optionKey === "string" && sha.test(raw.optionKey)
    && raw.optionKey === await scopeOptionsHash({ domain: "sales", identity: chosen }));
  fields(raw.dateMetadata, ["kind", "firstDate", "lastDate", "snapshotDate", "coverageVerified"]);
  const dates = raw.dateMetadata;
  check(dates.kind === "current_fact_business_date_envelope" && dates.coverageVerified === false && dates.snapshotDate === null);
  date(dates.firstDate); date(dates.lastDate); check(dates.firstDate <= dates.lastDate);
  fields(raw.provenance, ["kind", "revision", "meaning"]);
  check(raw.provenance.kind === "current_exact_business_projection" && raw.provenance.revision === binding.revision
    && raw.provenance.meaning === "observed_current_identity_not_complete_period_coverage");
  return raw as SalesOption;
}
export async function validateSalesOptionsResponse(raw: unknown, expected: SalesOptionsExpected): Promise<SalesOptionsPage> {
  const copy: unknown = JSON.parse(scopeOptionsCanonical(raw));
  const fixed = { principalKey: expected.principalKey, query: salesOptionsQuery(expected.query), cursor: expected.cursor ?? null,
    revision: expected.revision, generation: expected.directoryGeneration, digest: expected.directoryDigest,
    previous: expected.previousItem ? JSON.parse(scopeOptionsCanonical(expected.previousItem)) : undefined };
  check(typeof fixed.principalKey === "string" && sha.test(fixed.principalKey));
  if (fixed.cursor !== null) {
    cursor(fixed.cursor);
    check(fixed.revision !== undefined && fixed.generation !== undefined && fixed.digest !== undefined && fixed.previous,
      "下一页必须绑定先前完整页的版本、目录及末项。");
  } else check(fixed.previous === undefined);
  if (fixed.revision !== undefined) check(validRevision(fixed.revision));
  if (fixed.generation !== undefined) check(typeof fixed.generation === "string" && generation.test(fixed.generation));
  if (fixed.digest !== undefined) check(typeof fixed.digest === "string" && sha.test(fixed.digest));
  fields(copy, ["schemaVersion", "principalKey", "page"]);
  check(copy.schemaVersion === "business-sales-options-response-v1" && copy.principalKey === fixed.principalKey,
    "账号已变化，请刷新当前账号后重新选择ERP来源。");
  const page = copy.page;
  fields(page, ["schemaVersion", "domain", "authorityVerified", "revision", "directoryGeneration", "directoryDigest",
    "query", "queryDigest", "items", "pagination", "limitations", "pageDigest"]);
  check(encoder.encode(scopeOptionsCanonical(page)).length <= 38000, "完整ERP来源页超过容量。");
  check(page.schemaVersion === "business-analysis-options-v1" && page.domain === "sales" && page.authorityVerified === true);
  check(validRevision(page.revision));
  check(typeof page.directoryGeneration === "string" && generation.test(page.directoryGeneration));
  check(typeof page.directoryDigest === "string" && sha.test(page.directoryDigest));
  check((fixed.revision === undefined || fixed.revision === page.revision)
    && (fixed.generation === undefined || fixed.generation === page.directoryGeneration)
    && (fixed.digest === undefined || fixed.digest === page.directoryDigest), "ERP目录版本已变化，请从首页重读。");
  check(scopeOptionsCanonical(salesOptionsQuery(page.query)) === scopeOptionsCanonical(fixed.query));
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
  const result = page as SalesOptionsPage;
  validatedPages.set(result, { principalKey: fixed.principalKey, canonical: scopeOptionsCanonical(result) });
  return result;
}

/** Only a still-unchanged page returned by the validator may supply a choice. */
export function salesOptionSelection(page: SalesOptionsPage, chosen: SalesOption, principalKey: string): SalesOptionSelection {
  const fixed = validatedPages.get(page);
  check(fixed && fixed.principalKey === principalKey && fixed.canonical === scopeOptionsCanonical(page),
    "ERP来源页未经当前账号核验或已变化，请重新读取。");
  const selected = page.items.find(value => value.optionKey === chosen.optionKey);
  check(selected && scopeOptionsCanonical(selected) === scopeOptionsCanonical(chosen));
  const result = { principalKey, revision: page.revision, directoryGeneration: page.directoryGeneration,
    directoryDigest: page.directoryDigest, queryDigest: page.queryDigest, optionKey: selected.optionKey,
    identity: identity(selected.identity) };
  validatedSelections.set(result, scopeOptionsCanonical(result));
  return result;
}

/** Merge only shops; the parent retains dates, other scopes, question and budget.
 * New ERP shops get no inferred netshop datasets. Source identities can be 200
 * code points, while planning_v2 accepts only 100: reject explicitly, never slice.
 */
export function mergeSalesOption(shops: readonly SalesScopeShop[], selection: SalesOptionSelection, principalKey: string): SalesScopeShop[] {
  check(validatedSelections.get(selection) === scopeOptionsCanonical(selection), "ERP选择未经核验或已修改，请重新选择。");
  check(typeof principalKey === "string" && sha.test(principalKey) && selection.principalKey === principalKey,
    "账号已变化，不能添加上一账号的ERP来源。");
  const added = identity(selection.identity);
  for (const value of Object.values(added)) {
    check([...value].length <= 100, "来源身份超过当前分析计划100字符上限；请保留完整身份，不能截断或替换后分析。");
  }
  const copy: unknown = JSON.parse(scopeOptionsCanonical(shops));
  check(Array.isArray(copy) && copy.length <= 4);
  const next = copy.map(raw => {
    fields(raw, ["platform", "shop", "datasets", "salesChannels"]);
    for (const key of ["platform", "shop"] as const) check(typeof raw[key] === "string" && [...raw[key]].length <= 100 && !/[\p{Cc}\p{Cs}]/u.test(raw[key]));
    check(Array.isArray(raw.datasets) && raw.datasets.length <= 5
      && raw.datasets.every(value => typeof value === "string" && ["promotion", "sku", "spu", "b2b", "master"].includes(value))
      && new Set(raw.datasets).size === raw.datasets.length);
    check(Array.isArray(raw.salesChannels) && raw.salesChannels.length <= 10
      && raw.salesChannels.every(value => typeof value === "string" && [...value].length <= 100 && !/[\p{Cc}\p{Cs}]/u.test(value))
      && new Set(raw.salesChannels).size === raw.salesChannels.length);
    return raw as SalesScopeShop;
  });
  const identities = next.map(value => scopeOptionsCanonical([value.platform, value.shop]));
  check(new Set(identities).size === identities.length, "已有店铺身份重复，请明确调整后再添加。");
  const matching = next.find(value => value.platform === added.platform && value.shop === added.shop);
  if (matching) {
    if (matching.salesChannels.includes(added.channel)) return next;
    check(matching.salesChannels.length < 10, "每家店铺最多10个ERP渠道，请明确调整原范围后再添加。");
    matching.salesChannels.push(added.channel);
    return next;
  }
  const newShop = { platform: added.platform, shop: added.shop, datasets: [], salesChannels: [added.channel] };
  // Only the pristine initial placeholder is expendable; unfinished user rows stay.
  if (next.length === 1 && next[0].platform === "京东" && next[0].shop === "" && next[0].salesChannels.length === 0
    && scopeOptionsCanonical(next[0].datasets) === '["promotion","master"]') return [newShop];
  check(next.length < 4, "最多选择4家店铺，请明确调整原范围后再添加。");
  return [...next, newShop];
}

/** Shallow-copy the form so fields outside shops (including budget) stay exact. */
export function mergeSalesOptionRequest<T extends { shops: SalesScopeShop[] }>(request: T, selection: SalesOptionSelection, principalKey: string): T {
  return { ...request, shops: mergeSalesOption(request.shops, selection, principalKey) };
}
