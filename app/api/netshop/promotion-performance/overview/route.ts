import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { netshopOutletsForPrincipal, netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import {
  createDjangoNetshopService,
  NETSHOP_PROMOTION_OVERVIEW_PATH,
} from "@/lib/django/netshop-service";
import {
  NetshopQueryError,
  netshopQueryErrorPayload,
  readNetshopOutletFilters,
  resolveNetshopQueryPeriod,
} from "@/lib/netshop/query-contract";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const params = new URL(request.url).searchParams;
    const period = resolveNetshopQueryPeriod(params.get("startDate"), params.get("endDate"));
    if (!period) throw new NetshopQueryError("invalid_date_range", "推广概览必须显式提供 startDate 和 endDate");
    if (params.has("shop")) {
      throw new NetshopQueryError("invalid_outlet_filter", "店铺筛选必须使用 outlet 平台与店铺复合键");
    }
    const requestedPlatforms = params.getAll("platform");
    if (!requestedPlatforms.length) throw new NetshopQueryError("invalid_platform_filter", "推广概览必须显式提供 platform");
    const snapshotToken = params.get("snapshotToken") ?? undefined;
    if (snapshotToken !== undefined && !/^[a-f0-9]{64}$/.test(snapshotToken)) {
      throw new NetshopQueryError("invalid_snapshot_token", "推广概览必须使用商品排行返回的有效数据版本");
    }
    netshopPlatformsForPrincipal(principal, requestedPlatforms);
    netshopOutletsForPrincipal(
      principal,
      readNetshopOutletFilters(params.getAll("outlet")),
      requestedPlatforms,
    );
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_PROMOTION_OVERVIEW_PATH, query: params, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    const failure = netshopQueryErrorPayload(error, "读取网店推广概览失败");
    return Response.json(failure.body, { status: failure.status, headers: { "cache-control": "no-store" } });
  }
}
