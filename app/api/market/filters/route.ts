import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { MARKET_QUERIES_PATH, requestDjangoMarketService } from "@/lib/django/market-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "市场分析筛选");
    const result = await requestDjangoMarketService<Record<string, unknown>>(principal, {
      path: MARKET_QUERIES_PATH, service: "reader", payload: { operation: "filter_options" },
    }, { signal: request.signal });
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-market-data-revision": result.revision } });
  } catch (error) {
    return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "市场筛选选项读取失败");
  }
}
