import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { createDjangoNetshopService, NETSHOP_ANALYSIS_OPTIONS_PATH } from "@/lib/django/netshop-service";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { netshopQueryErrorPayload } from "@/lib/netshop/query-contract";
import { scopeOptionsPrincipalKey, scopeOptionsQuery, validateNetshopOptionsResponse } from "@/lib/ai/business-scope-options";
import { PublicApiError } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(); requireAnalysisPrincipal(principal);
    const query = new URL(request.url).searchParams, expected = query.getAll("expectedPrincipalKey");
    if (expected.length !== 1 || !/^[a-f0-9]{64}$/.test(expected[0])) throw new PublicApiError(400, "invalid_request", "必须提供唯一的当前账号绑定。");
    const principalKey = await scopeOptionsPrincipalKey(principal.email);
    if (principalKey !== expected[0]) return Response.json({ error: "账号已变化，请刷新当前账号后重新选择来源。", code: "principal_mismatch" }, { status: 403, headers: { "cache-control": "no-store" } });
    query.delete("expectedPrincipalKey");
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal, { method: "GET", path: NETSHOP_ANALYSIS_OPTIONS_PATH, query, service: "reader" }, { signal: request.signal },
    );
    const wrapper = { schemaVersion: "business-netshop-options-response-v1", principalKey, page: result.data };
    try {
      // Preserve the original transport query; only validate a successful reply.
      if ([...query.keys()].some(key => query.getAll(key).length !== 1) || query.has("limit") && query.get("limit") !== "20") throw new Error("Unexpected successful query");
      const filters = new URLSearchParams(query); filters.delete("cursor"); filters.delete("limit");
      await validateNetshopOptionsResponse(wrapper, { principalKey, query: scopeOptionsQuery(Object.fromEntries(filters)), cursor: query.get("cursor") });
      if (result.revision !== result.data.revision) throw new Error("Revision header differs");
    } catch { throw new PublicApiError(503, "service_unavailable", "来源选项响应未通过完整核验，请重新读取。"); }
    return Response.json(wrapper, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    const failure = netshopQueryErrorPayload(error, "经营分析选项读取失败");
    return Response.json(failure.body, { status: failure.status, headers: { "cache-control": "no-store" } });
  }
}
