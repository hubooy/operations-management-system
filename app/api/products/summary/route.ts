import {
  getProductSummary,
  ProductSummaryRequestError,
  type ProductSummaryRange,
} from "@/lib/products/summary";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { parseShopFilterKey } from "@/lib/sales/shop-identity";
import {
  normalizeProductSummarySelections,
  normalizeProductSummaryQuery,
  parseProductSummaryPaginationParameter,
} from "@/lib/products/query-contract";

function readSelections(
  searchParams: URLSearchParams,
  key: string,
  options: { maximum: number; allowed?: readonly string[]; label: string },
) {
  return normalizeProductSummarySelections(searchParams.getAll(key), options);
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "商品经营汇总");
    const searchParams = new URL(request.url).searchParams;
    const requestedViews = searchParams.getAll("view");
    if (requestedViews.length > 1) {
      throw new ProductSummaryRequestError("view 参数不能重复");
    }
    const requestedView = requestedViews[0] ?? null;
    if (requestedView !== null && requestedView !== "page") {
      throw new ProductSummaryRequestError("view 必须是 page");
    }
    const requestedSnapshotTokens = searchParams.getAll("snapshotToken");
    if (requestedSnapshotTokens.length > 1) {
      throw new ProductSummaryRequestError("snapshotToken 参数不能重复");
    }
    const expectedSnapshotToken = requestedSnapshotTokens[0];
    if (requestedView === "page" && !/^[a-f0-9]{64}$/.test(expectedSnapshotToken ?? "")) {
      throw new ProductSummaryRequestError("page 视图必须使用完整汇总返回的有效数据版本");
    }
    if (requestedView === null && expectedSnapshotToken !== undefined) {
      throw new ProductSummaryRequestError("完整汇总不接受 snapshotToken");
    }
    const requestedRange = searchParams.get("range");
    const allowedRanges = new Set<ProductSummaryRange>(["last30", "last90", "halfYear", "custom"]);
    if (requestedRange && !allowedRanges.has(requestedRange as ProductSummaryRange)) {
      throw new ProductSummaryRequestError("不支持的商品统计周期");
    }
    const rawDays = searchParams.get("days");
    if (rawDays !== null && (!/^[1-9]\d*$/.test(rawDays) || Number(rawDays) < 7 || Number(rawDays) > 365)) {
      throw new ProductSummaryRequestError("days 必须是 7 到 365 的十进制整数");
    }
    const page = parseProductSummaryPaginationParameter(searchParams.get("page"), "page");
    const pageSize = parseProductSummaryPaginationParameter(searchParams.get("pageSize"), "pageSize");
    const requestedSort = searchParams.get("sortBy");
    const requestedDirection = searchParams.get("direction");
    const platforms = readSelections(searchParams, "platform", { maximum: 8, label: "平台" });
    const shopKeys = readSelections(searchParams, "shop", { maximum: 4, label: "店铺" });
    if (shopKeys.some((value) => !parseShopFilterKey(value))) {
      throw new ProductSummaryRequestError("店铺筛选标识格式无效");
    }
    const query = normalizeProductSummaryQuery(searchParams.get("q") ?? "") || undefined;
    const categories = readSelections(searchParams, "category", { maximum: 10, label: "类目" });
    const marginBands = readSelections(searchParams, "marginBand", {
      maximum: 5,
      allowed: ["below35", "35to40", "40to45", "atLeast45", "unavailable"],
      label: "毛利率",
    }) as Array<"below35" | "35to40" | "40to45" | "atLeast45" | "unavailable">;
    const allowedSorts = ["netSalesCents", "grossProfitCents", "grossMarginRate", "refundRate", "stockValueCents", "netQuantity"] as const;
    if (requestedSort !== null && !allowedSorts.includes(requestedSort as typeof allowedSorts[number])) {
      throw new ProductSummaryRequestError("商品排序字段不在允许清单中");
    }
    if (requestedDirection !== null && requestedDirection !== "asc" && requestedDirection !== "desc") {
      throw new ProductSummaryRequestError("商品排序方向必须是 asc 或 desc");
    }
    const payload = await getProductSummary(principal, {
      range: requestedRange ? requestedRange as ProductSummaryRange : undefined,
      startDate: searchParams.get("startDate"),
      endDate: searchParams.get("endDate"),
      days: rawDays === null ? undefined : Number(rawDays),
      platforms,
      shopKeys,
      page,
      pageSize,
      query,
      categories,
      marginBands,
      sortBy: requestedSort === null ? undefined : requestedSort as typeof allowedSorts[number],
      direction: requestedDirection === null ? undefined : requestedDirection,
      projection: requestedView === "page" ? "page" : "full",
      expectedSnapshotToken,
      signal: request.signal,
    });
    return Response.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof ProductSummaryRequestError) {
      return Response.json({ error: error.message }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    return safeApiErrorResponse(error, "读取商品与毛利数据失败", { headers: { "cache-control": "no-store" } });
  }
}
