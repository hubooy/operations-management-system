/** Disabled-by-default market-v2 fifth tool bridge; no persisted Agent read. */
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
import { aiEnvironment, requestDjangoAi } from "@/lib/django/ai-service";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { PublicApiError } from "@/lib/http/api-error";

export const MARKET_V2_TOOL = "get_business_promotion_market_v2";
export const MARKET_V2_SURFACE = "business_agent_screening_promotion_market_v2";
export const MARKET_V2_PROFILE = "business-agent-screening-promotion-market-admitted-v2";
const roles = new Set(["market_b2b", "independent_review", "report"]);
const views = new Set(["price_band", "rank_entry_exit"]);
const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "市场v2第五工具参数或注入身份无效。"); };

export function marketV2ToolArguments(raw: unknown) {
  if (!record(raw)) invalid();
  const args = raw as Record<string, unknown>;
  const common = ["reportId", "marketContextDigest", "marketManifestDigest", "role", "mode"];
  if (!id(args.reportId) || !sha(args.marketContextDigest) || !sha(args.marketManifestDigest)
    || !roles.has(String(args.role))) invalid();
  let allowed: string[];
  if (args.mode === "summary") allowed = common;
  else if (args.mode === "page") {
    allowed = [...common, "view", "offset", "limit"];
    if (!views.has(String(args.view)) || !Number.isSafeInteger(args.offset)
      || Number(args.offset) < 0 || Number(args.offset) > 200000 || args.limit !== 20) invalid();
  } else if (args.mode === "row") {
    allowed = [...common, "view", "rowIndex", "rowId"];
    if (!views.has(String(args.view)) || !Number.isSafeInteger(args.rowIndex)
      || Number(args.rowIndex) < 0 || Number(args.rowIndex) >= 200000 || !sha(args.rowId)) invalid();
  } else invalid();
  if (Object.keys(args).length !== allowed.length || allowed.some(key => !Object.hasOwn(args, key))) invalid();
  return Object.fromEntries(allowed.map(key => [key, args[key]]));
}

export async function readBusinessMarketV2ToolCandidate(raw: unknown, context: AiToolExecutionContext) {
  const environment = await aiEnvironment();
  if (environment.AI_MARKET_V2_AGENT_RUNTIME_ENABLED !== "true")
    throw new PublicApiError(409, "conflict", "市场v2第五工具仍处于默认关闭状态。");
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== MARKET_V2_SURFACE || !/^[A-Za-z0-9_-]{1,128}$/.test(context.requestId)
    || typeof context.providerCallId !== "string" || !context.providerCallId.length
    || context.providerCallId.length > 160 || /[\x00-\x1f]/.test(context.providerCallId)) invalid();
  const args = marketV2ToolArguments(raw);
  const value = (await requestDjangoAi<Record<string, unknown>>(context.principal, {
    path: `/api/ai/market-v2-tool-candidate/${context.requestId}`, method: "POST", service: "reader",
    payload: { arguments: args, providerCallId: context.providerCallId },
  }, { requestId: context.requestId, signal: context.signal })).data;
  if (!record(value) || value.schemaVersion !== "business-market-v2-tool-result-candidate-v1"
    || value.surface !== MARKET_V2_SURFACE || value.profile !== MARKET_V2_PROFILE
    || value.toolName !== MARKET_V2_TOOL || value.reportId !== args.reportId
    || value.role !== args.role || value.mode !== args.mode
    || value.marketManifestDigest !== args.marketManifestDigest
    || value.jobIdClaim !== `market-preview-job-${context.requestId}`
    || value.providerDispatchIdClaim !== `market-preview-provider-${context.requestId}`
    || value.providerCallIdClaim !== context.providerCallId
    || value.serverFullMarketMaterialVerified !== true
    || value.sameJobProviderPersisted !== false || value.persistedRead !== false
    || value.registeredTool !== false || value.authorityVerified !== false
    || !sha(value.resultDigest) || !record(value.payload) || !Array.isArray(value.citationBases))
    throw new PublicApiError(409, "conflict", "市场v2第五工具候选结果与固定范围不一致。");
  const envelope = JSON.stringify({ ok: true, toolName: MARKET_V2_TOOL, data: value });
  if (envelope.length > 38000 || new TextEncoder().encode(envelope).length > 38000)
    throw new PublicApiError(413, "payload_too_large", "市场v2工具完整结果超过38k，不截断返回。");
  return value;
}
