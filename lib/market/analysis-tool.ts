import type { AppPrincipal } from "@/lib/auth/authorization";
import { createDjangoMarketService, MARKET_CONSUMER_QUERY_PATH } from "@/lib/django/market-service";
import { PublicApiError } from "@/lib/http/api-error";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";

export async function getMarketAnalysisRecords(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const result = await createDjangoMarketService().request<Record<string, unknown>>(principal,
    { path: MARKET_CONSUMER_QUERY_PATH, service: "reader", payload: { limit: 10, ...(raw as Record<string, unknown>), operation: "analysis_records" } }, { signal });
  if (JSON.stringify(result.data).length > 38_000) throw new PublicApiError(413, "payload_too_large", "市场分析页过大，请减小页长重新收集，不得截断");
  return result.data;
}
