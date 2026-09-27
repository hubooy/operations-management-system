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

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场日覆盖");
    const params = new URL(request.url).searchParams;
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: {
          operation: "daily_coverage",
          category: params.get("category") ?? "",
          scope: params.get("scope") ?? "",
          rankingDimension: (params.get("rankingDimension") ?? "").toUpperCase(),
          priceBandFilter: params.get("priceBandFilter") ?? "全部",
          startDate: params.get("startDate") ?? "",
          endDate: params.get("endDate") ?? "",
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
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取市场日覆盖失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
