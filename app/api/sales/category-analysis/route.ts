import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { routeDjangoSalesReadRequest } from "@/lib/django/sales-gateway";
import { parsePositiveIntegerQuery, safeApiErrorResponse } from "@/lib/http/api-error";
import {
  parseProductQueriesStrict,
  salesCategoryGranularities,
  salesCategorySortKeys,
  SalesReadRequestError,
  type SalesCategoryGranularity,
  type SalesCategorySortKey,
} from "@/lib/sales/read-contract";
import { parseShopFilterKey } from "@/lib/sales/shop-identity";

const isoDatePattern = /^\d{4}-\d{2}-\d{2}$/;

function selections(params: URLSearchParams, ...keys: string[]) {
  const values = [...new Set(keys.flatMap((key) => params.getAll(key))
    .flatMap((value) => value.split(/[，,;；]+/))
    .map((value) => value.trim())
    .filter(Boolean))];
  if (values.length > 50 || values.some((value) => value.length > 100)) {
    throw new SalesReadRequestError(`${keys[0]} 筛选最多 50 项，且每项不能超过 100 字。`);
  }
  return values;
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    const params = new URL(request.url).searchParams;
    const startDate = params.get("startDate") ?? "";
    const endDate = params.get("endDate") ?? "";
    if (!isoDatePattern.test(startDate) || !isoDatePattern.test(endDate)) {
      throw new SalesReadRequestError("startDate 和 endDate 必须使用 YYYY-MM-DD");
    }
    const granularity = params.get("granularity") ?? "day";
    if (!salesCategoryGranularities.includes(granularity as SalesCategoryGranularity)) {
      throw new SalesReadRequestError(`granularity 必须是 ${salesCategoryGranularities.join(", ")} 之一`);
    }
    const sortBy = params.get("sortBy") ?? "netSalesCents";
    if (!salesCategorySortKeys.includes(sortBy as SalesCategorySortKey)) {
      throw new SalesReadRequestError(`sortBy 必须是 ${salesCategorySortKeys.join(", ")} 之一`);
    }
    const direction = params.get("direction") ?? "desc";
    if (direction !== "asc" && direction !== "desc") {
      throw new SalesReadRequestError("direction 必须是 asc 或 desc");
    }
    parsePositiveIntegerQuery(params.get("level"), 1, "level", 3);
    parsePositiveIntegerQuery(params.get("page"), 1, "page", 10_000);
    parsePositiveIntegerQuery(params.get("pageSize"), 20, "pageSize", 100);
    const outlets = selections(params, "outlet", "outlets").map(parseShopFilterKey);
    if (outlets.some((value) => value === null)) {
      throw new SalesReadRequestError("outlet 必须使用有效的平台与店铺复合键。");
    }
    selections(params, "category", "categories");
    selections(params, "channel", "channels");
    selections(params, "platform", "platforms");
    parseProductQueriesStrict([...params.getAll("productQuery"), ...params.getAll("productQueries")]);
    return routeDjangoSalesReadRequest({ request, principal });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    if (error instanceof SalesReadRequestError) {
      return Response.json(
        { error: error.message, code: "invalid_request" },
        { status: 400, headers: { "cache-control": "no-store" } },
      );
    }
    return safeApiErrorResponse(error, "读取品类分析失败。", { headers: { "cache-control": "no-store" } });
  }
}
