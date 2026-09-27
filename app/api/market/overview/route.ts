import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { parseMarketOverviewQuery } from "@/lib/market/query-contract";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "市场分析概览");
    const { view, pagination, filters } = parseMarketOverviewQuery(
      new URL(request.url).searchParams,
    );
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: {
          operation: "overview",
          view,
          page: pagination.page,
          pageSize: pagination.pageSize,
          filters,
          ...(new URL(request.url).searchParams.get("includeFilterOptions") === "false" ? { includeFilterOptions: false } : {}),
        },
      },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      headers: {
        "cache-control": "no-store",
        "x-market-data-revision": result.revision,
      },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "市场分析数据读取失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
