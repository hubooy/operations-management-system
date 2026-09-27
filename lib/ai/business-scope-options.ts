export type NetshopDataset = "promotion" | "sku" | "spu" | "b2b" | "master";
export type NetshopOptionIdentity = { platform: string; shop: string; dataset: NetshopDataset };
export type NetshopOptionsQuery = { platform?: string; shop?: string; dataset?: NetshopDataset; q?: string };
export type NetshopOption = {
  optionKey: string; identity: NetshopOptionIdentity; source: string; sourceDataset: string;
  dateMetadata: { kind: "published_import_envelope"; firstDate: string | null; lastDate: string | null; snapshotDate: string | null; coverageVerified: false };
  provenance: { kind: "completed_import_metadata"; revision: string; meaning: "historically_published_not_current_fact_coverage" };
};
export type NetshopOptionsPage = {
  schemaVersion: "business-analysis-options-v1"; domain: "netshop"; revision: string;
  query: NetshopOptionsQuery; queryDigest: string; items: NetshopOption[];
  pagination: { returned: number; limit: 20; hasMore: boolean; nextCursor: string | null };
  pageDigest: string; limitations: string[];
};
export type NetshopOptionSelection = { principalKey: string; revision: string; queryDigest: string; optionKey: string; identity: NetshopOptionIdentity };
export type ScopeShop = { platform: string; shop: string; datasets: string[]; salesChannels: string[] };
const encoder = new TextEncoder(), sha = /^[a-f0-9]{64}$/, revisionPattern = /^(0|[1-9]\d*):[a-f0-9]{12}$/;
const datasets: NetshopDataset[] = ["promotion", "sku", "spu", "b2b", "master"];
const sources: Record<string, Partial<Record<NetshopDataset, [string, string]>>> = {
  "京东": { promotion: ["jd_promotion", "ad"], sku: ["jd_sku_daily", "sku_daily"], spu: ["jd_sku_daily", "spu_daily"], b2b: ["jd_b2b", "b2b"], master: ["jd_product_master", "product_master"] },
  "天猫": { promotion: ["tmall_promotion", "promotion_daily"], spu: ["tmall_product_daily", "spu_daily"], master: ["tmall_product_master", "product_master"] },
};
function check(value: unknown, message = "来源选项范围或完整性无效，请重新读取。"): asserts value { if (!value) throw new Error(message); }
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value)); }
function fields(value: unknown, names: string[]): asserts value is Record<string, unknown> { check(object(value) && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name))); }
function text(value: unknown, maximum = 100): asserts value is string { check(typeof value === "string" && value.length > 0 && [...value].length <= maximum && value === value.trim() && !/[\p{Cc}\p{Cs}]/u.test(value)); }
export function scopeOptionsCanonical(value: unknown): string {
  let count = 0;
  function encode(item: unknown, depth: number): string {
    check(++count <= 4000 && depth <= 8, "来源选项结构超过边界。");
    if (item === null || typeof item === "boolean") return JSON.stringify(item);
    if (typeof item === "number") { check(Number.isSafeInteger(item)); return JSON.stringify(item); }
    if (typeof item === "string") { check(item.length <= 4096 && !/\p{Cs}/u.test(item)); return JSON.stringify(item); }
    if (Array.isArray(item)) { check(item.length <= 30); return "["+item.map(v => encode(v, depth+1)).join(",")+"]"; }
    check(object(item) && Object.keys(item).length <= 20);
    return "{"+Object.keys(item).sort().map(key => {
      check(/^[A-Za-z][A-Za-z0-9]*$/.test(key) && !["constructor", "prototype"].includes(key));
      const descriptor = Object.getOwnPropertyDescriptor(item, key); check(descriptor && "value" in descriptor);
      return JSON.stringify(key)+":"+encode(descriptor.value, depth+1);
    }).join(",")+"}";
  }
  const result = encode(value, 0); check(encoder.encode(result).length <= 65536, "来源选项响应超过容量。"); return result;
}
export async function scopeOptionsHash(value: unknown): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(scopeOptionsCanonical(value)))), n => n.toString(16).padStart(2, "0")).join("");
}
export async function scopeOptionsPrincipalKey(email: string): Promise<string> { text(email, 320); return scopeOptionsHash(["business-workbench", email.toLowerCase()]); }
export function scopeOptionsQuery(raw: unknown): NetshopOptionsQuery {
  check(object(raw) && Object.keys(raw).every(key => ["platform", "shop", "dataset", "q"].includes(key)));
  for (const value of Object.values(raw)) text(value);
  if (raw.platform !== undefined) check(["京东", "天猫"].includes(raw.platform as string));
  if (raw.shop !== undefined) check(raw.platform !== undefined);
  if (raw.dataset !== undefined) {
    check(datasets.includes(raw.dataset as NetshopDataset));
    if (raw.platform !== undefined) check(sources[raw.platform as string][raw.dataset as NetshopDataset]);
  }
  return { ...raw } as NetshopOptionsQuery;
}
function identity(raw: unknown): NetshopOptionIdentity {
  fields(raw, ["platform", "shop", "dataset"]); const value = scopeOptionsQuery(raw);
  check(value.platform && value.shop && value.dataset); return value as NetshopOptionIdentity;
}
function validDate(value: unknown) {
  if (value === null) return;
  check(typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && value >= "0001-01-01" && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value);
}
function cursor(value: unknown): asserts value is string { check(typeof value === "string" && value.length <= 4096 && /^\.?[A-Za-z0-9_-]+:[0-9A-Za-z]+:[A-Za-z0-9_-]+$/.test(value)); }
function compareText(left: string, right: string): number {
  const a = [...left], b = [...right];
  for (let i = 0; i < Math.min(a.length, b.length); i++) { const difference = a[i].codePointAt(0)!-b[i].codePointAt(0)!; if (difference) return difference; }
  return a.length-b.length;
}
function compareItems(a: NetshopOption, b: NetshopOption) {
  const left = [a.identity.platform, a.identity.shop, a.source, a.sourceDataset], right = [b.identity.platform, b.identity.shop, b.source, b.sourceDataset];
  for (let i = 0; i < 4; i++) { const difference = compareText(left[i], right[i]); if (difference) return difference; } return 0;
}
export async function validateNetshopOptionsResponse(raw: unknown, expected: {
  principalKey: string; query: NetshopOptionsQuery; cursor?: string | null; revision?: string; previousItem?: NetshopOption;
}): Promise<NetshopOptionsPage> {
  // Freeze untrusted pages and caller-owned context before asynchronous hashing.
  const copy = JSON.parse(scopeOptionsCanonical(raw)) as Record<string, unknown>;
  const fixed = { principalKey: expected.principalKey, query: scopeOptionsQuery(expected.query), cursor: expected.cursor ?? null,
    revision: expected.revision, previousItem: expected.previousItem ? JSON.parse(scopeOptionsCanonical(expected.previousItem)) as NetshopOption : undefined };
  check(sha.test(fixed.principalKey)); if (fixed.cursor !== null) cursor(fixed.cursor);
  if (fixed.revision !== undefined) check(revisionPattern.test(fixed.revision));
  fields(copy, ["schemaVersion", "principalKey", "page"]);
  check(copy.schemaVersion === "business-netshop-options-response-v1" && copy.principalKey === fixed.principalKey, "账号已变化，请刷新当前账号后重新选择来源。");
  const page = copy.page;
  fields(page, ["schemaVersion", "domain", "revision", "query", "queryDigest", "items", "pagination", "pageDigest", "limitations"]);
  check(encoder.encode(scopeOptionsCanonical(page)).length <= 38000, "完整来源选项页超过容量。");
  check(page.schemaVersion === "business-analysis-options-v1" && page.domain === "netshop" && typeof page.revision === "string" && revisionPattern.test(page.revision));
  check(fixed.revision === undefined || fixed.revision === page.revision, "来源目录版本已变化，请从首页重读。");
  check(scopeOptionsCanonical(scopeOptionsQuery(page.query)) === scopeOptionsCanonical(fixed.query));
  check(typeof page.queryDigest === "string" && sha.test(page.queryDigest) && await scopeOptionsHash(page.query) === page.queryDigest);
  check(typeof page.pageDigest === "string" && sha.test(page.pageDigest));
  const material = { ...page }; delete material.pageDigest; check(await scopeOptionsHash(material) === page.pageDigest);
  check(Array.isArray(page.items) && page.items.length <= 20);
  fields(page.pagination, ["returned", "limit", "hasMore", "nextCursor"]);
  const pagination = page.pagination;
  check(pagination.returned === page.items.length && pagination.limit === 20 && typeof pagination.hasMore === "boolean");
  if (pagination.hasMore) { check(page.items.length === 20); cursor(pagination.nextCursor); check(pagination.nextCursor !== fixed.cursor); }
  else check(pagination.nextCursor === null);
  check(Array.isArray(page.limitations) && page.limitations.length >= 1 && page.limitations.length <= 10);
  for (const item of page.limitations) text(item, 500);
  let previous = fixed.previousItem; const seen = new Set<string>();
  for (const rawItem of page.items) {
    fields(rawItem, ["optionKey", "identity", "source", "sourceDataset", "dateMetadata", "provenance"]);
    const itemIdentity = identity(rawItem.identity), pair = sources[itemIdentity.platform][itemIdentity.dataset]!;
    check(rawItem.source === pair[0] && rawItem.sourceDataset === pair[1]);
    for (const key of ["platform", "shop", "dataset"] as const) check(fixed.query[key] === undefined || fixed.query[key] === itemIdentity[key]);
    check(typeof rawItem.optionKey === "string" && sha.test(rawItem.optionKey) && !seen.has(rawItem.optionKey));
    check(await scopeOptionsHash({ domain: "netshop", identity: itemIdentity }) === rawItem.optionKey); seen.add(rawItem.optionKey);
    fields(rawItem.dateMetadata, ["kind", "firstDate", "lastDate", "snapshotDate", "coverageVerified"]);
    const dates = rawItem.dateMetadata;
    check(dates.kind === "published_import_envelope" && dates.coverageVerified === false);
    for (const value of [dates.firstDate, dates.lastDate, dates.snapshotDate]) validDate(value);
    check(dates.firstDate === null || dates.lastDate === null || String(dates.firstDate) <= String(dates.lastDate));
    if (itemIdentity.dataset === "master") check(dates.firstDate === null && dates.lastDate === null);
    fields(rawItem.provenance, ["kind", "revision", "meaning"]);
    check(rawItem.provenance.kind === "completed_import_metadata" && rawItem.provenance.revision === page.revision && rawItem.provenance.meaning === "historically_published_not_current_fact_coverage");
    const item = rawItem as NetshopOption; if (previous) check(compareItems(previous, item) < 0); previous = item;
  }
  return page as NetshopOptionsPage;
}
export function scopeOptionSelection(page: NetshopOptionsPage, item: NetshopOption, principalKey: string): NetshopOptionSelection {
  check(sha.test(principalKey) && revisionPattern.test(page.revision) && sha.test(page.queryDigest));
  const stored = page.items.find(value => value.optionKey === item.optionKey);
  check(stored && scopeOptionsCanonical(stored) === scopeOptionsCanonical(item));
  return { principalKey, revision: page.revision, queryDigest: page.queryDigest, optionKey: item.optionKey, identity: identity(item.identity) };
}
export function mergeNetshopOption(shops: ScopeShop[], rawIdentity: NetshopOptionIdentity): ScopeShop[] {
  const selected = identity(rawIdentity);
  check(Array.isArray(shops) && shops.length <= 4);
  const next = shops.map(shop => {
    fields(shop, ["platform", "shop", "datasets", "salesChannels"]);
    check(typeof shop.platform === "string" && typeof shop.shop === "string" && [...shop.platform].length <= 100 && [...shop.shop].length <= 100);
    check(Array.isArray(shop.datasets) && shop.datasets.length <= 5 && shop.datasets.every(d => datasets.includes(d as NetshopDataset)) && new Set(shop.datasets).size === shop.datasets.length);
    check(Array.isArray(shop.salesChannels) && shop.salesChannels.length <= 10 && shop.salesChannels.every(c => typeof c === "string" && [...c].length <= 100));
    return { ...shop, datasets: [...shop.datasets], salesChannels: [...shop.salesChannels] } as ScopeShop;
  });
  const matches = next.filter(shop => shop.platform === selected.platform && shop.shop === selected.shop); check(matches.length <= 1);
  if (matches.length) { if (!matches[0].datasets.includes(selected.dataset)) matches[0].datasets.push(selected.dataset); return next; }
  const added = { platform: selected.platform, shop: selected.shop, datasets: [selected.dataset], salesChannels: [] };
  if (next.length === 1 && next[0].platform === "京东" && next[0].shop === "" && next[0].salesChannels.length === 0 && scopeOptionsCanonical(next[0].datasets) === '["promotion","master"]') return [added];
  check(next.length < 4, "最多选择4家店铺，请明确调整原范围后再添加。"); return [...next, added];
}
