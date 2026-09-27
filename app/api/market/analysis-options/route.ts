import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { requestMarketAnalysisOptions } from "@/lib/django/market-service";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { PublicApiError } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    requireAnalysisPrincipal(principal);
    const result = await requestMarketAnalysisOptions(principal, new URL(request.url).searchParams, { signal: request.signal });
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return Response.json({ code: error instanceof PublicApiError ? error.code : "service_unavailable",
      error: error instanceof PublicApiError ? error.message : "市场来源选项读取失败" },
    { status: error instanceof PublicApiError ? error.status : 503, headers: { "cache-control": "no-store" } });
  }
}
