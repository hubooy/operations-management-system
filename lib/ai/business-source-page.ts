import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { createDjangoNetshopService, NETSHOP_ANALYSIS_RECORDS_PATH } from "@/lib/django/netshop-service";
import { createDjangoMarketService, MARKET_CONSUMER_QUERY_PATH } from "@/lib/django/market-service";
import { readDjangoSalesConsumer, type SalesConsumerRequestMap } from "@/lib/django/sales-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";

/** Background collection only; never attached to a model provider or chat. */
export async function readBusinessSourcePage(args: Record<string, unknown>, context: AiToolExecutionContext) {
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== "business_collection") throw new PublicApiError(403, "access_denied", "仅限持久证据后台采集");
  const { domain, ...query } = args;
  let data: Record<string, unknown>;
  if (domain === "netshop") {
    const search = new URLSearchParams({ limit: "100" });
    for (const [key, value] of Object.entries(query)) search.set(key, String(value));
    data = (await createDjangoNetshopService().request<Record<string, unknown>>(context.principal,
      { method: "GET", path: NETSHOP_ANALYSIS_RECORDS_PATH, service: "reader", query: search }, { signal: context.signal })).data;
  } else if (domain === "market") {
    data = (await createDjangoMarketService().request<Record<string, unknown>>(context.principal,
      { path: MARKET_CONSUMER_QUERY_PATH, service: "reader", payload: { limit: 100, ...query, operation: "analysis_records" } }, { signal: context.signal })).data;
  } else if (domain === "sales") {
    data = (await readDjangoSalesConsumer(context.principal,
      { limit: 100, ...query, operation: "analysis_records" } as SalesConsumerRequestMap["analysis_records"], { signal: context.signal })).data;
  } else throw new PublicApiError(422, "invalid_request", "来源域无效");
  if (new TextEncoder().encode(JSON.stringify(data)).byteLength > 131_072) {
    throw new PublicApiError(413, "payload_too_large", "证据页超过持久容量，保留检查点；不得截断");
  }
  return data;
}
