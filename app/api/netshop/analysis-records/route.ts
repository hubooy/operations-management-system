import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { createDjangoNetshopService, NETSHOP_ANALYSIS_RECORDS_PATH } from "@/lib/django/netshop-service";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { netshopQueryErrorPayload } from "@/lib/netshop/query-contract";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    requireAnalysisPrincipal(principal);
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_ANALYSIS_RECORDS_PATH, query: new URL(request.url).searchParams, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    const failure = netshopQueryErrorPayload(error, "经营分析数据读取失败");
    return Response.json(failure.body, { status: failure.status, headers: { "cache-control": "no-store" } });
  }
}
