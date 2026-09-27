/** New-surface aliases of four owning v1 reads; never an Agent receipt. */
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
import { aiEnvironment, requestDjangoAi } from "@/lib/django/ai-service";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { PublicApiError } from "@/lib/http/api-error";
import { MARKET_V2_SURFACE } from "@/lib/ai/business-market-v2-tool-candidate";

export const MARKET_V2_BASE_NAMES = [
  "get_business_market_v2_screening_package", "get_business_market_v2_screening_analysis",
  "get_business_market_v2_screening_budget", "get_business_market_v2_keyword_sku",
] as const;
export type MarketV2BaseName = typeof MARKET_V2_BASE_NAMES[number];
const roles = new Set(["commerce", "promotion", "market_b2b", "independent_review", "report"]);
const budgetRoles = new Set(["promotion", "independent_review", "report"]);
const promotionRoles = new Set(["promotion", "independent_review", "report"]);
const views = new Set(["keyword_sku", "keyword_sku_context"]);
const dimensions = new Set(["shop", "category", "spu", "sku", "keyword", "searchTerm", "daily", "brand"]);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const integer = (value: unknown, max: number) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "市场v2基础工具别名参数无效。"); };

export function marketV2BaseArguments(name: MarketV2BaseName, raw: unknown) {
  if (!MARKET_V2_BASE_NAMES.includes(name) || !record(raw)) invalid();
  const value = raw as Record<string, unknown>;
  const role = String(value.role);
  if (!id(value.reportId) || !roles.has(role)) invalid();
  const common = ["reportId", "role"];
  let allowed: string[];
  if (name !== MARKET_V2_BASE_NAMES[3]) {
    if (!id(value.runId) || !id(value.screeningId)) invalid();
    allowed = [...common, "runId", "screeningId", "offset"];
    const maximum = name === MARKET_V2_BASE_NAMES[0] ? 9999 : name === MARKET_V2_BASE_NAMES[2] ? 99 : 250000;
    if (value.offset !== undefined && !integer(value.offset, maximum)) invalid();
    if (name === MARKET_V2_BASE_NAMES[2] && !budgetRoles.has(role)) invalid();
    if (name === MARKET_V2_BASE_NAMES[1]) {
      allowed.push("mode", "dimension", "sourceKey", "baselineKey", "pairKey", "baselinePairKey");
      if (!dimensions.has(String(value.dimension))) invalid();
      if (value.mode === "native") {
        if (!id(value.sourceKey) || value.pairKey !== undefined || value.baselinePairKey !== undefined
          || (value.baselineKey !== undefined && !id(value.baselineKey))) invalid();
      } else if (value.mode === "mapped") {
        if (!sha(value.pairKey) || !["sku", "spu"].includes(String(value.dimension))
          || value.sourceKey !== undefined || value.baselineKey !== undefined
          || (value.baselinePairKey !== undefined && !sha(value.baselinePairKey))) invalid();
      } else invalid();
    }
  } else {
    if (!promotionRoles.has(role) || !id(value.sourceKey) || !views.has(String(value.view))
      || (value.baselineKey !== undefined && !id(value.baselineKey))) invalid();
    allowed = [...common, "sourceKey", "view", "baselineKey", "offset", "limit", "rowIndex", "rowId"];
    const row = Object.hasOwn(value, "rowIndex") || Object.hasOwn(value, "rowId");
    if (row ? (!integer(value.rowIndex, 249999) || !sha(value.rowId)
      || value.offset !== undefined || value.limit !== undefined)
      : ((value.offset !== undefined && !integer(value.offset, 250000))
        || (value.limit !== undefined && value.limit !== 20))) invalid();
  }
  if (Object.keys(value).some(key => !allowed.includes(key))) invalid();
  return Object.fromEntries(allowed.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
}

export async function readBusinessMarketV2BaseToolCandidate(name: MarketV2BaseName, raw: unknown, context: AiToolExecutionContext) {
  const environment = await aiEnvironment();
  if (environment.AI_MARKET_V2_AGENT_RUNTIME_ENABLED !== "true")
    throw new PublicApiError(409, "conflict", "市场v2基础工具别名仍处于默认关闭状态。");
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== MARKET_V2_SURFACE || !/^[A-Za-z0-9_-]{1,128}$/.test(context.requestId)
    || typeof context.providerCallId !== "string" || !context.providerCallId.length
    || context.providerCallId.length > 160 || /[\x00-\x1f]/.test(context.providerCallId)) invalid();
  const args = marketV2BaseArguments(name, raw);
  const value = (await requestDjangoAi<Record<string, unknown>>(context.principal, {
    path: `/api/ai/market-v2-base-tool-candidate/${context.requestId}`, method: "POST", service: "reader",
    payload: { name, arguments: args, providerCallId: context.providerCallId },
  }, { requestId: context.requestId, signal: context.signal })).data;
  if (!record(value) || value.schemaVersion !== "business-market-v2-base-tool-candidate-v1"
    || value.toolName !== name || value.reportId !== args.reportId
    || value.roleClaim !== args.role || value.persistedRead !== false
    || value.sameJobProviderPersisted !== false || value.registeredAgentTool !== false
    || value.authorityVerified !== false || value.serverSourceVerified !== true
    || !sha(value.resultDigest) || !sha(value.marketManifestDigest)
    || !["available", "unavailable_no_fixed_budget"].includes(String(value.status)))
    throw new PublicApiError(409, "conflict", "市场v2基础工具别名不是同源未持久候选。");
  if (value.status === "unavailable_no_fixed_budget" && (name !== MARKET_V2_BASE_NAMES[2]
    || value.payload !== null || value.sourceResultDigest !== null))
    throw new PublicApiError(409, "conflict", "市场v2预算缺源状态不一致。");
  const maximum = name === MARKET_V2_BASE_NAMES[3] ? 38000 : 40000;
  const envelope = JSON.stringify({ ok: true, toolName: name, data: value });
  if (envelope.length > maximum || new TextEncoder().encode(envelope).length > maximum)
    throw new PublicApiError(413, "payload_too_large", "市场v2基础工具完整结果超出固定容量，不截断。");
  return value;
}
