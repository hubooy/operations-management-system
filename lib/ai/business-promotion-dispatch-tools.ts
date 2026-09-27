/** Internal dispatch-bound bridge for the four promotion-screening tools. */
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { requestDjangoAi } from "@/lib/django/ai-service";
import { PublicApiError } from "@/lib/http/api-error";

const names = ["get_business_promotion_screening_package_v1", "get_business_promotion_screening_analysis_v1",
  "get_business_promotion_screening_budget_v1", "get_business_promotion_keyword_sku_v1"] as const;
type Name = typeof names[number];
const dimensions = new Set(["shop", "category", "spu", "sku", "keyword", "searchTerm", "daily", "brand"]);
const roles = new Set(["commerce", "promotion", "market_b2b", "independent_review", "report"]);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const integer = (value: unknown, maximum: number) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= maximum;
const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "词货模型工具参数或派发身份无效。"); };

function copy(name: Name, raw: unknown): Record<string, unknown> {
  if (!record(raw)) invalid();
  const value = raw as Record<string, unknown>;
  const common = ["runId", "reportId", "screeningId"];
  const extra = name === names[0] ? ["role", "offset"] : name === names[1]
    ? ["mode", "dimension", "sourceKey", "baselineKey", "pairKey", "baselinePairKey", "offset"]
    : name === names[2] ? ["offset"] : ["sourceKey", "view", "baselineKey", "offset", "limit", "rowIndex", "rowId"];
  const allowed = name === names[3] ? ["reportId", ...extra] : [...common, ...extra];
  if (Object.keys(value).some(key => !allowed.includes(key)) || !id(value.reportId)) invalid();
  if (name !== names[3] && (!id(value.runId) || !id(value.screeningId))) invalid();
  if (name === names[0] && (!roles.has(String(value.role))
    || (value.offset !== undefined && !integer(value.offset, 9999)))) invalid();
  if (name === names[1]) {
    if (!dimensions.has(String(value.dimension)) || !["native", "mapped"].includes(String(value.mode))
      || (value.offset !== undefined && !integer(value.offset, 250000))) invalid();
    if (value.mode === "native") {
      if (!id(value.sourceKey) || value.pairKey !== undefined || value.baselinePairKey !== undefined
        || (value.baselineKey !== undefined && !id(value.baselineKey))) invalid();
    } else if (!sha(value.pairKey) || !["sku", "spu"].includes(String(value.dimension))
      || value.sourceKey !== undefined || value.baselineKey !== undefined
      || (value.baselinePairKey !== undefined && !sha(value.baselinePairKey))) invalid();
  }
  if (name === names[2] && value.offset !== undefined && !integer(value.offset, 99)) invalid();
  if (name === names[3]) {
    if (!id(value.sourceKey) || !["keyword_sku", "keyword_sku_context"].includes(String(value.view))
      || (value.baselineKey !== undefined && !id(value.baselineKey))) invalid();
    const row = Object.hasOwn(value, "rowIndex") || Object.hasOwn(value, "rowId");
    if (row ? (!integer(value.rowIndex, 249999) || !sha(value.rowId)
      || Object.hasOwn(value, "offset") || Object.hasOwn(value, "limit"))
      : ((value.offset !== undefined && !integer(value.offset, 250000))
        || (value.limit !== undefined && value.limit !== 20))) invalid();
  }
  return Object.fromEntries(allowed.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
}

export async function readBusinessPromotionDispatchTool(name: Name, raw: unknown, context: AiToolExecutionContext) {
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== "business_agent_screening_promotion_v1" || !/^[A-Za-z0-9_-]{1,128}$/.test(context.requestId)
    || typeof context.providerCallId !== "string" || !context.providerCallId.length || context.providerCallId.length > 200)
    invalid();
  const argumentsCopy = copy(name, raw);
  const dispatchId = context.requestId;
  const value = (await requestDjangoAi<Record<string, unknown>>(context.principal, {
    path: `/api/ai/promotion-tool-dispatch/${dispatchId}`, method: "POST", service: "reader",
    payload: { name, arguments: argumentsCopy, providerCallId: context.providerCallId },
  }, { requestId: dispatchId, signal: context.signal })).data;
  if (!record(value))
    throw new PublicApiError(409, "conflict", "词货工具完整回执无效。");
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 38000)
    throw new PublicApiError(413, "payload_too_large", "词货工具完整回执超过字节容量。");
  const expected = name === names[0] ? "business-screening-role-package-v1" : name === names[1]
    ? "business-screening-analysis-v1" : name === names[2]
    ? "business-screening-budget-v1" : "business-promotion-keyword-sku-response-v1";
  if (value.schemaVersion !== expected) throw new PublicApiError(409, "conflict", "词货工具回执协议不一致。");
  const bound = name === names[0] ? value : record(value.reference) ? value.reference : null;
  if (name !== names[3] && (bound?.reportId !== argumentsCopy.reportId
    || bound?.evidenceRunId !== argumentsCopy.runId
    || (name === names[0] ? value.role !== argumentsCopy.role
      : !record(bound?.screeningIntent) || bound.screeningIntent.id !== argumentsCopy.screeningId)))
    throw new PublicApiError(409, "conflict", "词货筛查工具回执范围不一致。");
  if (name === names[3]) {
    const binding = record(value.binding) ? value.binding : null;
    const report = record(binding?.reportBinding) ? binding.reportBinding : null;
    const authority = record(value.authority) ? value.authority : null;
    const rowMode = Object.hasOwn(argumentsCopy, "rowIndex");
    const row = record(value.row) ? value.row : null;
    const table = record(value.table) ? value.table : null;
    const pagination = record(table?.pagination) ? table.pagination : null;
    if (!sha(value.responseDigest) || !sha(value.bindingDigest)
      || binding?.schemaVersion !== "business-promotion-keyword-sku-binding-v1"
      || report?.reportId !== argumentsCopy.reportId
      || binding?.sourceKey !== argumentsCopy.sourceKey || binding?.view !== argumentsCopy.view
      || binding?.baselineKey !== (argumentsCopy.baselineKey ?? null)
      || authority?.reportBindingVerified !== true
      || authority?.completeSourceTraversalForSelectedSources !== true
      || authority?.entityDailyCoverageVerified !== false
      || authority?.productMasterIdentityVerified !== false
      || authority?.scopeMeaning !== "selected_source_view_not_registered_report_coverage"
      || (rowMode ? !row || "table" in value || row.rowIndex !== argumentsCopy.rowIndex
        || row.id !== argumentsCopy.rowId
        : !table || "row" in value || pagination?.offset !== (argumentsCopy.offset ?? 0)))
      throw new PublicApiError(409, "conflict", "词货明细回执与持久派发选择不一致。");
  }
  return value;
}
