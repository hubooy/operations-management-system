/** Candidate tool bridge only: no provider/profile dispatch or read-receipt authority. */
import type { AppPrincipal } from "@/lib/auth/authorization";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { requestDjangoAi } from "@/lib/django/ai-service";
import { PublicApiError } from "@/lib/http/api-error";

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const integer = (value: unknown, maximum: number) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= maximum;
const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "词货工具参数无效，页模式与行引用不能混合。"); };

export async function readBusinessPromotionKeywordSkuV1(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  if (!record(raw)) invalid();
  const input = raw as Record<string, unknown>;
  const rowMode = Object.hasOwn(input, "rowIndex") || Object.hasOwn(input, "rowId");
  const allowed = ["reportId", "sourceKey", "view", "baselineKey", ...(rowMode ? ["rowIndex", "rowId"] : ["offset", "limit"])];
  if (Object.keys(input).some(key => !allowed.includes(key)) || !id(input.reportId) || !id(input.sourceKey)
    || typeof input.view !== "string" || !["keyword_sku", "keyword_sku_context"].includes(input.view)
    || (Object.hasOwn(input, "baselineKey") && !id(input.baselineKey))) invalid();
  if (rowMode) {
    if (!integer(input.rowIndex, 249999) || !sha(input.rowId)) invalid();
  } else if ((Object.hasOwn(input, "offset") && !integer(input.offset, 250000))
    || (Object.hasOwn(input, "limit") && input.limit !== 20)) invalid();
  // Copy primitive selectors before the first asynchronous operation.
  const fixed = { ...input };
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(fixed)) if (key !== "reportId") query.set(key, String(value));
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/reports/${fixed.reportId}/promotion-keyword-sku`, method: "GET", query,
  }, { signal });
  const value = result.data;
  if (!record(value)) throw new PublicApiError(409, "conflict", "词货回执格式无效。");
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 38000)
    throw new PublicApiError(413, "payload_too_large", "完整词货页超过字节容量，不得截断。");
  const binding = record(value.binding) ? value.binding : null;
  const report = record(binding?.reportBinding) ? binding.reportBinding : null;
  const authority = record(value.authority) ? value.authority : null;
  const row = record(value.row) ? value.row : null;
  const table = record(value.table) ? value.table : null;
  const pagination = record(table?.pagination) ? table.pagination : null;
  if (value.schemaVersion !== "business-promotion-keyword-sku-response-v1"
    || !sha(value.responseDigest) || !sha(value.bindingDigest)
    || binding?.schemaVersion !== "business-promotion-keyword-sku-binding-v1"
    || report?.reportId !== fixed.reportId || binding?.sourceKey !== fixed.sourceKey
    || binding?.view !== fixed.view || binding?.baselineKey !== (fixed.baselineKey ?? null)
    || authority?.reportBindingVerified !== true || authority?.completeSourceTraversalForSelectedSources !== true
    || authority?.entityDailyCoverageVerified !== false || authority?.productMasterIdentityVerified !== false
    || authority?.scopeMeaning !== "selected_source_view_not_registered_report_coverage"
    || (rowMode ? !row || "table" in value || row.id !== fixed.rowId || row.rowIndex !== fixed.rowIndex
      : !table || "row" in value || pagination?.offset !== (fixed.offset ?? 0)))
    throw new PublicApiError(409, "conflict", "词货回执协议或固定报告、来源、行身份不一致。");
  return value;
}
