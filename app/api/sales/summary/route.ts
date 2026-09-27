import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { routeDjangoSalesReadRequest } from "@/lib/django/sales-gateway";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  isSalesRange,
  parseProductQueriesStrict,
  salesRanges,
  SalesReadRequestError,
} from "@/lib/sales/read-contract";
import { parseShopFilterKey } from "@/lib/sales/shop-identity";

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
    requireUnrestrictedDataScope(principal, "销售汇总");
    const url = new URL(request.url);
    const params = url.searchParams;
    const requested = params.get("range") ?? "month";
    if (!isSalesRange(requested)) {
      throw new SalesReadRequestError(`range 必须是 ${salesRanges.join(", ")} 之一`);
    }
    const productQueries = parseProductQueriesStrict([
      ...params.getAll("productQuery"),
      params.get("productCodes") ?? "",
    ]);
    selections(params, "categories", "category");
    selections(params, "platforms", "platform");
    const outlets = selections(params, "outlet", "outlets").map(parseShopFilterKey);
    if (outlets.some((value) => value === null)) {
      throw new SalesReadRequestError("outlet 必须使用有效的平台与店铺复合键。");
    }
    // Keep repeated client values within Django's total query-field limit.
    params.delete("productQuery");
    params.delete("productCodes");
    if (productQueries.length > 0) params.set("productQuery", productQueries.join(","));
    request = new Request(url, request);
    return routeDjangoSalesReadRequest({ request, principal });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof SalesReadRequestError) {
      return Response.json(
        { error: error.message, code: "invalid_request" },
        { status: 400, headers: { "cache-control": "no-store" } },
      );
    }
    return safeApiErrorResponse(error, "读取销售汇总失败。", { headers: { "cache-control": "no-store" } });
  }
}
