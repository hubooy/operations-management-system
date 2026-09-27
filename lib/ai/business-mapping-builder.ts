import type { BudgetRun } from "./business-budget-builder";
export type { BudgetRun };
export type MappingPair = { salesKey: string; masterKey: string };
export type MappingSelection = { bindingKey: string; pairs: MappingPair[]; ready: boolean; reason: "loading" | "verified" | "selected" | "cleared" | "failed" };
export type MappingSource = { key: string; domain: "sales" | "netshop" | "market"; query: Record<string, string>; queryDigest: string; ordinal: number };
export type MappingContext = { bindingKey: string; runId: string; version: number; catalogDigest: string; header: Record<string, unknown>; sourceKeys: string[] };
const encoder = new TextEncoder(), shaPattern = /^[a-f0-9]{64}$/, idPattern = /^[A-Za-z0-9_-]{1,160}$/;
function requireValue(value: unknown, message = "来源目录或固定范围无效，未启用关联选择。"): asserts value { if (!value) throw new Error(message); }
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function keys(value: unknown, names: string[]): asserts value is Record<string, unknown> { requireValue(object(value) && Object.keys(value).length === names.length && names.every(key => Object.hasOwn(value, key))); }
export function mappingCanonical(value: unknown): string {
  let nodes = 0;
  function encode(item: unknown, depth: number): string {
    requireValue(++nodes <= 6000 && depth <= 8, "来源目录结构超过安全边界。");
    if (item === null || typeof item === "boolean") return JSON.stringify(item);
    if (typeof item === "number") { requireValue(Number.isSafeInteger(item)); return JSON.stringify(item); }
    if (typeof item === "string") { requireValue(item.length <= 16000); return JSON.stringify(item); }
    if (Array.isArray(item)) { requireValue(item.length <= 48); return "["+item.map(v => encode(v, depth+1)).join(",")+"]"; }
    requireValue(object(item) && Object.keys(item).length <= 100);
    return "{"+Object.keys(item).sort().map(key => { requireValue(key.length <= 100 && !["__proto__", "constructor", "prototype"].includes(key)); return JSON.stringify(key)+":"+encode(item[key], depth+1); }).join(",")+"}";
  }
  return encode(value, 0);
}
export async function mappingHash(value: unknown) { return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(mappingCanonical(value)))), n => n.toString(16).padStart(2, "0")).join(""); }
export function mappingTextCompare(a: string, b: string) {
  const left = Array.from(a), right = Array.from(b);
  for (let i = 0; i < Math.min(left.length, right.length); i++) { const difference = left[i].codePointAt(0)!-right[i].codePointAt(0)!; if (difference) return difference; }
  return left.length-right.length;
}
export function mappingBindingKey(run: BudgetRun, principalKey: string) { return JSON.stringify([principalKey, run.id, run.version, run.plan.catalogDigest]); }
export function mappingContext(run: BudgetRun, principalKey: string): MappingContext {
  requireValue(typeof principalKey === "string" && principalKey.length > 0 && principalKey.length <= 200 && idPattern.test(run.id)
    && run.status === "sealed" && Number.isSafeInteger(run.version) && run.version > 0, "商品关联需要当前账号的已封存 v2 任务。");
  const raw = mappingCanonical(run.plan); requireValue(encoder.encode(raw).length <= 16000);
  const header = JSON.parse(raw) as Record<string, unknown>;
  keys(header, ["schemaVersion", "sourceCount", "catalogDigest", "capacityProfile", "collector", "limits", ...(Object.hasOwn(header, "analysisRequest") ? ["analysisRequest"] : [])]);
  requireValue(header.schemaVersion === "business-evidence-v2" && Number.isSafeInteger(header.sourceCount) && Number(header.sourceCount) >= 1 && Number(header.sourceCount) <= 48
    && typeof header.catalogDigest === "string" && shaPattern.test(header.catalogDigest) && header.capacityProfile === "catalog-48-facts-v1"
    && mappingCanonical(header.collector) === '{"pageSize":100,"surface":"business_collection","version":1}'
    && mappingCanonical(header.limits) === '{"factBytes":67108864,"factPages":2000}', "需要完整 v2 任务头信息，请刷新任务详情。");
  requireValue(object(run.sources));
  const sourceKeys = Object.keys(run.sources).sort();
  requireValue(sourceKeys.length === header.sourceCount && sourceKeys.every(key => idPattern.test(key) && run.sources[key]?.complete === true), "封存来源目录不完整，请刷新任务详情。");
  return { bindingKey: mappingBindingKey(run, principalKey), runId: run.id, version: run.version, catalogDigest: header.catalogDigest, header, sourceKeys };
}
function query(value: unknown, domain: string): Record<string, string> {
  requireValue(object(value));
  const names = ["platform", "startDate", "endDate", "window", ...(domain === "market" ? ["category", "scope", "rankingDimension", "priceBandFilter"] : ["shop", domain === "sales" ? "channel" : "dataset"])];
  keys(value, names);
  for (const [key, item] of Object.entries(value)) requireValue(typeof item === "string" && item.length > 0 && [...item].length <= (["category", "scope", "priceBandFilter"].includes(key) ? 200 : 100) && item === item.trim() && !/[\p{Cc}\p{Cs}]/u.test(item));
  const result = value as Record<string, string>;
  requireValue(["current", "previous", "yearAgo"].includes(result.window));
  for (const date of [result.startDate, result.endDate]) requireValue(/^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(Date.parse(date)) && new Date(date).toISOString().slice(0, 10) === date);
  const days = (Date.parse(result.endDate)-Date.parse(result.startDate))/86400000;
  requireValue(days >= 0 && days < 93);
  if (domain === "netshop") {
    requireValue(["京东", "天猫"].includes(result.platform) && ["promotion", "sku", "spu", "b2b", "master"].includes(result.dataset));
    requireValue(result.dataset !== "b2b" || result.platform === "京东");
    requireValue(result.dataset !== "master" || result.window === "current");
  }
  if (domain === "market") requireValue(result.platform === "京东" && ["SKU", "SPU"].includes(result.rankingDimension));
  requireValue(encoder.encode(mappingCanonical(result)).length <= 4096);
  return { ...result };
}
export async function readMappingDirectory(context: MappingContext, fetchPage: (offset: number) => Promise<unknown>): Promise<MappingSource[]> {
  // Freeze every trusted input before the first await or supplied callback.
  const fixed = JSON.parse(mappingCanonical(context)) as MappingContext, entries: MappingSource[] = [], seen = new Set<string>();
  const planDigest = await mappingHash(fixed.header); let offset = 0, pages = 0, queryBytes = 0;
  while (true) {
    requireValue(++pages <= 48 && offset < fixed.sourceKeys.length);
    const page = JSON.parse(mappingCanonical(await fetchPage(offset))) as Record<string, unknown>;
    keys(page, ["schemaVersion", "runId", "evidenceVersion", "planDigest", "catalogDigest", "offset", "requestedLimit", "total", "returned", "nextOffset", "items", "pageDigest"]);
    requireValue(page.schemaVersion === "business-evidence-directory-page-v2" && page.runId === fixed.runId && page.evidenceVersion === fixed.version && page.planDigest === planDigest
      && page.catalogDigest === fixed.catalogDigest && page.offset === offset && page.requestedLimit === 20 && page.total === fixed.sourceKeys.length && Array.isArray(page.items)
      && page.items.length >= 1 && page.items.length <= 20 && page.returned === page.items.length && offset+page.items.length <= page.total
      && page.nextOffset === (offset+page.items.length < page.total ? offset+page.items.length : null) && typeof page.pageDigest === "string" && shaPattern.test(page.pageDigest));
    const copied: MappingSource[] = [];
    for (const [i, item] of (page.items as unknown[]).entries()) {
      keys(item, ["key", "domain", "query", "queryDigest", "ordinal"]);
      requireValue(typeof item.key === "string" && fixed.sourceKeys.includes(item.key) && !seen.has(item.key) && item.ordinal === offset+i+1
        && typeof item.domain === "string" && ["sales", "netshop", "market"].includes(item.domain) && typeof item.queryDigest === "string" && shaPattern.test(item.queryDigest));
      const checkedQuery = query(item.query, item.domain as string);
      requireValue(await mappingHash(checkedQuery) === item.queryDigest);
      queryBytes += encoder.encode(mappingCanonical(checkedQuery)).length; requireValue(queryBytes <= 128*1024);
      copied.push({ key: item.key, domain: item.domain as MappingSource["domain"], ordinal: item.ordinal as number, query: checkedQuery, queryDigest: item.queryDigest }); seen.add(item.key);
    }
    const body: Record<string, unknown> = { ...page, items: copied }; delete body.pageDigest;
    requireValue(encoder.encode(mappingCanonical(page)).length <= 38000 && await mappingHash(body) === page.pageDigest, "目录页摘要校验失败，原选择已失效。");
    entries.push(...copied);
    if (page.nextOffset === null) break;
    offset = page.nextOffset as number;
  }
  requireValue(entries.length === fixed.sourceKeys.length && seen.size === fixed.sourceKeys.length);
  const order = entries.map(entry => mappingCanonical({ domain: entry.domain, query: entry.query, key: entry.key }));
  const identities = entries.map(entry => mappingCanonical({ domain: entry.domain, query: entry.query }));
  requireValue(new Set(identities).size === entries.length && order.every((item, i) => !i || mappingTextCompare(order[i-1], item) < 0)
    && new Set(entries.map(entry => JSON.stringify([entry.query.startDate, entry.query.endDate]))).size === 1);
  requireValue(await mappingHash({ schemaVersion: "business-evidence-directory-v2", entries }) === fixed.catalogDigest, "完整目录摘要不匹配，未保留关联选择。");
  return entries;
}
export function matchingMasters(sales: MappingSource, sources: MappingSource[]) {
  return sources.filter(source => sales.domain === "sales" && source.domain === "netshop" && source.query.dataset === "master" && source.query.window === "current"
    && source.query.platform === sales.query.platform && source.query.shop === sales.query.shop);
}
export function mappingPairs(sources: MappingSource[], raw: MappingPair[]): MappingPair[] {
  requireValue(Array.isArray(raw) && raw.length <= 47, "最多选择 47 组关联，不得截断。");
  const seen = new Set<string>();
  const pairs = raw.map(pair => {
    keys(pair, ["salesKey", "masterKey"]); requireValue(typeof pair.salesKey === "string" && typeof pair.masterKey === "string" && !seen.has(pair.salesKey));
    const sales = sources.find(source => source.key === pair.salesKey);
    requireValue(sales && matchingMasters(sales, sources).some(master => master.key === pair.masterKey), "关联必须使用同一精确平台、店铺的销售和本期主数据。");
    seen.add(pair.salesKey); return { salesKey: pair.salesKey, masterKey: pair.masterKey };
  }).sort((a, b) => a.salesKey < b.salesKey ? -1 : a.salesKey > b.salesKey ? 1 : a.masterKey < b.masterKey ? -1 : 1);
  const plan = { schemaVersion: "business-mapping-plan-v1", algorithmVersion: "exact-product-partition-v1", pairs: pairs.map(pair => ({ ...pair, pairKey: "0".repeat(64) })) };
  requireValue(encoder.encode(mappingCanonical(plan)).length <= 16000, "完整关联计划超过 16000 字节，不能截断选择。");
  return pairs;
}
