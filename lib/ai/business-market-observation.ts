/** Internal read-only market TOP observation bridge. No public route or Agent registration. */
import type { AppPrincipal } from "@/lib/auth/authorization";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { requestDjangoAi } from "@/lib/django/ai-service";
import { PublicApiError } from "@/lib/http/api-error";

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
const sha = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const integer = (value: unknown, max: number) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
const date = (value: unknown): value is string => typeof value === "string" && /^20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/.test(value)
  && !Number.isNaN(Date.parse(value + "T00:00:00Z")) && new Date(value + "T00:00:00Z").toISOString().slice(0, 10) === value;
const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "市场观察来源、日期或行选择无效。"); };
const conflict = (): never => { throw new PublicApiError(409, "conflict", "市场观察回执与固定报告、来源或行不一致。"); };

export async function readBusinessMarketObservationV2(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  if (!record(raw)) invalid();
  const input = raw as Record<string, unknown>;
  const rowMode = Object.hasOwn(input, "rowIndex") || Object.hasOwn(input, "rowId");
  const common = ["reportId", "currentSourceKey", "baselineSourceKey", "currentObservationDate", "baselineObservationDate"];
  const allowed = [...common, ...(rowMode ? ["rowIndex", "rowId"] : ["offset", "limit"])];
  if (Object.keys(input).some(key => !allowed.includes(key)) || common.some(key => !Object.hasOwn(input, key))
    || !id(input.reportId) || !id(input.currentSourceKey) || !id(input.baselineSourceKey)
    || input.currentSourceKey === input.baselineSourceKey
    || !date(input.currentObservationDate) || !date(input.baselineObservationDate)) invalid();
  if (rowMode ? (!integer(input.rowIndex, 199999) || !sha(input.rowId))
    : ((Object.hasOwn(input, "offset") && !integer(input.offset, 200000))
      || (Object.hasOwn(input, "limit") && input.limit !== 20))) invalid();
  const fixed = Object.fromEntries(allowed.filter(key => Object.hasOwn(input, key)).map(key => [key, input[key]]));
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(fixed)) if (key !== "reportId") query.set(key, String(value));
  const value = (await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/reports/${fixed.reportId}/market-observation`, method: "GET", service: "reader", query,
  }, { signal })).data;
  if (!record(value) || new TextEncoder().encode(JSON.stringify(value)).byteLength > 38000) conflict();
  const binding = record(value.binding) ? value.binding : null;
  const report = record(binding?.reportBinding) ? binding.reportBinding : null;
  const authority = record(value.authority) ? value.authority : null;
  const row = record(value.row) ? value.row : null;
  const table = record(value.table) ? value.table : null;
  const pagination = record(table?.pagination) ? table.pagination : null;
  const dates = record(table?.observationDates) ? table.observationDates : null;
  if (value.schemaVersion !== "business-market-observation-response-v2" || !sha(value.bindingDigest)
    || !sha(value.responseDigest) || binding?.schemaVersion !== "business-market-observation-binding-v2"
    || report?.reportId !== fixed.reportId || binding?.currentSourceKey !== fixed.currentSourceKey
    || binding?.baselineSourceKey !== fixed.baselineSourceKey
    || binding?.currentObservationDate !== fixed.currentObservationDate
    || binding?.baselineObservationDate !== fixed.baselineObservationDate
    || binding?.algorithmVersion !== "market-daily-observation-v2"
    || !sha(binding?.tableBindingDigest)
    || authority?.completeSourceTraversalForSelectedSources !== true
    || authority?.reportBindingVerified !== true || authority?.wholeMarketCoverageVerified !== false
    || authority?.ownProductIdentityVerified !== false
    || authority?.scopeMeaning !== "two_selected_days_of_top_sample_not_whole_market"
    || (rowMode ? !row || "table" in value || row.rowIndex !== fixed.rowIndex || row.rowId !== fixed.rowId
      : !table || "row" in value || !Array.isArray(table.rows) || table.algorithmVersion !== "market-daily-observation-v2"
        || table.view !== "rank_entry_exit" || dates?.current !== fixed.currentObservationDate
        || dates?.baseline !== fixed.baselineObservationDate || pagination?.offset !== (fixed.offset ?? 0))) conflict();
  return value;
}
