import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { requestSalesAnalysisOptions } from "@/lib/django/sales-gateway";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { salesOptionsPrincipalKey, salesOptionsQuery, validateSalesOptionsResponse } from "@/lib/ai/business-sales-options";
import { PublicApiError } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(); requireAnalysisPrincipal(principal);
    const query = new URL(request.url).searchParams, expected = query.getAll("expectedPrincipalKey");
    if (expected.length !== 1 || !/^[a-f0-9]{64}$/.test(expected[0]))
      throw new PublicApiError(400, "invalid_request", "必须提供唯一的当前账号绑定。");
    const principalKey = await salesOptionsPrincipalKey(principal.email);
    if (principalKey !== expected[0])
      return Response.json({ code: "principal_mismatch", error: "账号已变化，请刷新当前账号后重新选择ERP来源。" },
        { status: 403, headers: { "cache-control": "no-store" } });
    query.delete("expectedPrincipalKey");
    if (request.signal.aborted) throw new PublicApiError(503, "service_unavailable", "来源选项读取已取消。");
    // Forward the original query including duplicates/unknown fields. Only the
    // local account envelope field is consumed here; Django rejects bad queries.
    const result = await requestSalesAnalysisOptions(principal, query, { signal: request.signal });
    let page;
    try {
      if ([...query.keys()].some(key => query.getAll(key).length !== 1)
        || query.has("limit") && query.get("limit") !== "20") throw new Error("Unexpected successful query");
      if (query.has("cursor") && (query.get("cursor")!.length > 1600
        || !/^\.?[A-Za-z0-9_-]+:[0-9A-Za-z]+:[A-Za-z0-9_-]+$/.test(query.get("cursor")!))) throw new Error("Invalid successful cursor");
      const filters = new URLSearchParams(query); filters.delete("cursor"); filters.delete("limit");
      // Validate this owning page in full. Django verifies its signed cursor.
      // Previous-item continuity is deliberately NOT claimed here: the frontend
      // validator pins its own previously accepted page, never client authority.
      page = await validateSalesOptionsResponse({ schemaVersion: "business-sales-options-response-v1", principalKey, page: result },
        { principalKey, query: salesOptionsQuery(Object.fromEntries(filters)) });
    } catch {
      throw new PublicApiError(503, "service_unavailable", "ERP来源选项响应未通过完整核验，请重新读取。");
    }
    if (request.signal.aborted) throw new PublicApiError(503, "service_unavailable", "来源选项读取已取消。");
    return Response.json({ schemaVersion: "business-sales-options-response-v1", principalKey, page },
      { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) { auth.headers.set("cache-control", "no-store"); return auth; }
    return Response.json({ code: error instanceof PublicApiError ? error.code : "service_unavailable",
      error: error instanceof PublicApiError ? error.message : "ERP来源选项读取失败" },
    { status: error instanceof PublicApiError ? error.status : 503, headers: { "cache-control": "no-store" } });
  }
}
