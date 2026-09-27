const MAX_FINANCE_ANALYSIS_MONTHS = 24;
import {
  createDjangoFinanceService,
  DjangoFinanceServiceResponseError,
  FINANCE_ANALYSIS_PATH,
} from "@/lib/django/finance-service";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "财报分析");
    const searchParams = new URL(request.url).searchParams;
    const monthValues = searchParams.getAll("month").flatMap((value) => value.split(",")).filter(Boolean);
    const allMonths = monthValues.includes("*");
    const requestedMonths = [...new Set(monthValues.filter((month) => month !== "*"))];
    const fallbackValues = searchParams.getAll("initialMonthFallback");
    if (fallbackValues.length > 1 || (fallbackValues.length === 1 && fallbackValues[0] !== "latest_completed")) {
      throw new PublicApiError(400, "invalid_request", "initialMonthFallback 只能是 latest_completed");
    }
    const fallbackToLatestCompletedMonth = fallbackValues[0] === "latest_completed";
    if (allMonths && requestedMonths.length) {
      throw new PublicApiError(400, "invalid_request", "month=* 不能与指定月份同时使用。");
    }
    if (allMonths && fallbackToLatestCompletedMonth) {
      throw new PublicApiError(400, "invalid_request", "month=* 不需要初始月份回退。");
    }
    if (fallbackToLatestCompletedMonth && requestedMonths.length === 0) {
      throw new PublicApiError(400, "invalid_request", "初始月份回退必须与指定月份同时使用。");
    }
    if (requestedMonths.some((month) => !/^\d{4}-(0[1-9]|1[0-2])$/.test(month))) {
      throw new PublicApiError(400, "invalid_request", "月份格式应为真实的 YYYY-MM。");
    }
    if (requestedMonths.length > MAX_FINANCE_ANALYSIS_MONTHS) {
      throw new PublicApiError(400, "invalid_request", `单次最多分析 ${MAX_FINANCE_ANALYSIS_MONTHS} 个财务月份。`);
    }
    const djangoRead = () => createDjangoFinanceService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: FINANCE_ANALYSIS_PATH, query: searchParams, service: "reader" },
      { signal: request.signal },
    ).then((result) => result.data);
    return Response.json(await djangoRead(), { headers: { "cache-control": "no-store" } });
  } catch (error) {
    if (error instanceof DjangoFinanceServiceResponseError
      && error.upstreamCode === "finance_dimension_filter_out_of_scope") {
      return Response.json(error.payload, { status: error.status, headers: { "cache-control": "no-store" } });
    }
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "财报分析读取失败。", { headers: { "cache-control": "no-store" } });
  }
}
