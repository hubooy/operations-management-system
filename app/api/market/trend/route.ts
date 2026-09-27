import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "市场单品趋势");
    const params = new URL(request.url).searchParams;
    const dimension = params.get("dimension");
    if (dimension !== "SKU" && dimension !== "SPU") {
      throw new PublicApiError(400, "invalid_request", "榜单维度必须为 SKU 或 SPU");
    }
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: {
          operation: "trend",
          skuCode: params.get("skuCode") ?? "",
          category: params.get("category") ?? "",
          scope: params.get("scope") ?? "",
          rankingDimension: dimension,
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
    return safeApiErrorResponse(error, "读取市场单品趋势失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
