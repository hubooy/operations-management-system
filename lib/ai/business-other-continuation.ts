import type { AiToolExecutionContext } from "./tool-registry-contract";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { requestSalesAnalysisContinuation } from "@/lib/django/sales-gateway";
import { requestMarketAnalysisContinuation } from "@/lib/django/market-service";
import { PublicApiError } from "@/lib/http/api-error";

function query(args: Record<string, unknown>, context: AiToolExecutionContext) {
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== "business_collection") throw new PublicApiError(403, "access_denied", "仅限持久证据后台续读");
  return new URLSearchParams(Object.entries(args).map(([key, value]) => [key, String(value)]));
}

export async function readBusinessSalesContinuation(args: Record<string, unknown>, context: AiToolExecutionContext) {
  return requestSalesAnalysisContinuation(context.principal, query(args, context), { signal: context.signal });
}

export async function readBusinessMarketContinuation(args: Record<string, unknown>, context: AiToolExecutionContext) {
  return requestMarketAnalysisContinuation(context.principal, query(args, context), { signal: context.signal });
}
