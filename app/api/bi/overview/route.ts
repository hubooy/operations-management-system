import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { requestDjangoBiOverview } from "@/lib/django/bi-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { SalesReadRequestError } from "@/lib/sales/read-contract";


const allowedRanges = new Set(["today", "yesterday", "last7", "last15", "last30", "month", "quarter", "custom"]);

function parseIsoDate(value: string | null, label: string) {
  if (value === null) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new SalesReadRequestError(`${label}日期无效`);
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    throw new SalesReadRequestError(`${label}日期无效`);
  }
  return { value, epochDay: Math.floor(date.getTime() / 86_400_000) };
}

function validate(params: URLSearchParams) {
  if ([...params.keys()].some((key) => !["range", "startDate", "endDate"].includes(key))) {
    throw new SalesReadRequestError("BI 看板包含未知查询参数");
  }
  for (const key of ["range", "startDate", "endDate"]) {
    if (params.getAll(key).length > 1) throw new SalesReadRequestError(`${key} 参数不能重复`);
  }
  const range = params.get("range") ?? "month";
  if (!allowedRanges.has(range)) throw new SalesReadRequestError("BI 看板统计周期无效");
  const start = parseIsoDate(params.get("startDate"), "开始");
  const end = parseIsoDate(params.get("endDate"), "结束");
  if (range === "custom") {
    if (!start || !end) throw new SalesReadRequestError("自定义周期必须提供开始和结束日期");
    if (start.epochDay > end.epochDay || end.epochDay - start.epochDay > 366) {
      throw new SalesReadRequestError("BI 自定义周期超出允许范围");
    }
  } else if (start || end) {
    throw new SalesReadRequestError("只有自定义周期可以提供开始和结束日期");
  }
}
export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "BI 经营看板");
    const params = new URL(request.url).searchParams;
    validate(params);
    const result = await requestDjangoBiOverview<Record<string, unknown>>(
      principal,
      params.toString(),
      { signal: request.signal },
    );
    return Response.json(result.data, {
      headers: { "cache-control": "no-store", "x-bi-data-revision": result.revision },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof SalesReadRequestError) {
      return Response.json(
        { error: error.message, code: "invalid_request" },
        { status: 400, headers: { "cache-control": "no-store" } },
      );
    }
    return safeApiErrorResponse(error, "读取 BI 经营看板失败。", {
      headers: { "cache-control": "no-store" },
    });
  }
}
