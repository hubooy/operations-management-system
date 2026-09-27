import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { routeDjangoSalesReadRequest } from "@/lib/django/sales-gateway";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  parseProductQueriesStrict,
  SalesReadRequestError,
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
    const category = (params.get("category") ?? "").trim();
    if (!isoDatePattern.test(startDate) || !isoDatePattern.test(endDate)) {
      throw new SalesReadRequestError("startDate 和 endDate 必须使用 YYYY-MM-DD");
    }
    if (!category || category.length > 100) {
      throw new SalesReadRequestError("category 不能为空且不能超过 100 字");
    }
    const outlets = selections(params, "outlet", "outlets").map(parseShopFilterKey);
    if (outlets.some((value) => value === null)) {
      throw new SalesReadRequestError("outlet 必须使用有效的平台与店铺复合键。");
    }
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
    return safeApiErrorResponse(error, "读取品类平台店铺明细失败。", { headers: { "cache-control": "no-store" } });
  }
}
