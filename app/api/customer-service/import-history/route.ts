import { listCustomerServiceBatches } from "@/lib/customer-service/database";
import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { parsePositiveIntegerQuery, safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "客服导入历史");
    const params = new URL(request.url).searchParams;
    const paged = params.has("page") || params.has("pageSize");
    const page = parsePositiveIntegerQuery(paged ? params.get("page") : null, 1, "page", 10_000);
    const pageSize = parsePositiveIntegerQuery(paged ? params.get("pageSize") : params.get("limit"), 20, paged ? "pageSize" : "limit", 100);
    return Response.json(
      await listCustomerServiceBatches({ page, pageSize }, principal),
      { headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取客服导入历史失败。", { headers: { "cache-control": "no-store" } });
  }
}
