import {
  createDjangoNetshopService,
  NETSHOP_PRODUCT_PERFORMANCE_PATH,
} from "@/lib/django/netshop-service";
import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { netshopOutletsForPrincipal, netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import {
  NETSHOP_QUERY_MAX_PAGE,
  NETSHOP_QUERY_MAX_PAGE_SIZE,
  NetshopQueryError,
  netshopQueryErrorPayload,
  readNetshopOutletFilters,
  readNetshopProductPerformanceView,
  readNetshopQueryInteger,
  readNetshopSnapshotToken,
  resolveNetshopQueryPeriod,
} from "@/lib/netshop/query-contract";

function readDimension(values: readonly string[]): "sku" | "spu" {
  if (values.length === 0) return "sku";
  if (values.length !== 1 || (values[0] !== "sku" && values[0] !== "spu")) {
    throw new NetshopQueryError("invalid_dimension", "dimension 必须且只能是 sku 或 spu");
  }
  return values[0];
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const params = new URL(request.url).searchParams;
    readDimension(params.getAll("dimension"));
    const view = readNetshopProductPerformanceView(params.getAll("view"));
    readNetshopSnapshotToken(params.getAll("snapshotToken"), view === "page");
    readNetshopQueryInteger(params.get("page"), "page", 1, 1, NETSHOP_QUERY_MAX_PAGE);
    readNetshopQueryInteger(params.get("pageSize"), "pageSize", 50, 1, NETSHOP_QUERY_MAX_PAGE_SIZE);
    resolveNetshopQueryPeriod(params.get("startDate"), params.get("endDate"));
    if (params.has("shop")) {
      throw new NetshopQueryError("invalid_outlet_filter", "店铺筛选必须使用 outlet 平台与店铺复合键");
    }
    const requestedPlatforms = params.getAll("platform");
    netshopPlatformsForPrincipal(principal, requestedPlatforms);
    netshopOutletsForPrincipal(
      principal,
      readNetshopOutletFilters(params.getAll("outlet")),
      requestedPlatforms,
    );
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_PRODUCT_PERFORMANCE_PATH, query: params, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    const failure = netshopQueryErrorPayload(error, "读取网店商品日数据失败");
    return Response.json(failure.body, { status: failure.status, headers: { "cache-control": "no-store" } });
  }
}
