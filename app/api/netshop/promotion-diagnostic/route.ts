import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { netshopOutletsForPrincipal, netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import {
  createDjangoNetshopService,
  NETSHOP_PROMOTION_DIAGNOSTIC_PATH,
} from "@/lib/django/netshop-service";
import {
  NetshopQueryError,
  netshopQueryErrorPayload,
  readNetshopOutletFilters,
  resolveNetshopQueryPeriod,
} from "@/lib/netshop/query-contract";

const ALLOWED = new Set(["platform", "outlet", "startDate", "endDate"]);

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const params = new URL(request.url).searchParams;
    for (const key of params.keys()) {
      if (!ALLOWED.has(key) || params.getAll(key).length !== 1) {
        throw new NetshopQueryError("invalid_diagnostic_filter", "推广诊断筛选包含未知或重复参数");
      }
    }
    const period = resolveNetshopQueryPeriod(params.get("startDate"), params.get("endDate"), 31);
    if (!period) throw new NetshopQueryError("invalid_date_range", "推广诊断必须选择明确的自然日范围");
    const platforms = params.getAll("platform");
    const outlets = readNetshopOutletFilters(params.getAll("outlet"));
    if (platforms.length !== 1 || platforms[0] !== "京东" || outlets.length !== 1
      || outlets[0]?.platform !== "京东") {
      throw new NetshopQueryError("invalid_diagnostic_scope", "推广诊断一次只允许一个精确京东店铺");
    }
    netshopPlatformsForPrincipal(principal, platforms);
    netshopOutletsForPrincipal(principal, outlets, platforms);
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_PROMOTION_DIAGNOSTIC_PATH, query: params, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    const failure = netshopQueryErrorPayload(error, "读取京东推广诊断数据失败");
    return Response.json(failure.body, { status: failure.status, headers: { "cache-control": "no-store" } });
  }
}
