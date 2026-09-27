import type { AppPrincipal } from "@/lib/auth/authorization";
import { PublicApiError } from "@/lib/http/api-error";
import { readDjangoSalesConsumer, type SalesConsumerRequestMap } from "@/lib/django/sales-consumer-reader";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";

export async function getSalesAnalysisRecords(args: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const result = await readDjangoSalesConsumer(principal,
    { limit: 10, ...(args as Omit<SalesConsumerRequestMap["analysis_records"], "operation">), operation: "analysis_records" }, { signal });
  if (JSON.stringify(result.data).length > 38_000) throw new PublicApiError(422, "payload_too_large", "销售分析页过大，须减小页长从头收集，不得截断");
  return result.data;
}
