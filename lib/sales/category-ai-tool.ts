import type { AppPrincipal } from "@/lib/auth/authorization";
import { routeDjangoSalesReadRequest } from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";
import {
  salesCategorySortKeys,
  type SalesCategorySortKey,
} from "@/lib/sales/read-contract";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown, maximum: number): string {
  return typeof value === "string" ? value.trim().slice(0, maximum) : "";
}

function list(value: unknown, maximumItems: number): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((item) => text(item, 120)).filter(Boolean))].slice(0, maximumItems);
}

function integer(value: unknown, fallback: number, maximum: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(maximum, Math.trunc(parsed)) : fallback;
}

function naturalDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return year >= 1900 && year <= 2199
    && parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}

export async function getSalesCategoryAnalysisForAi(
  rawArgs: unknown,
  principal: AppPrincipal,
  options: { route?: typeof routeDjangoSalesReadRequest } = {},
) {
  const args = record(rawArgs);
  const startDate = text(args.startDate, 10);
  const endDate = text(args.endDate, 10);
  const sortCandidate = text(args.sortBy, 40);
  const sortBy = salesCategorySortKeys.includes(sortCandidate as SalesCategorySortKey)
    ? sortCandidate as SalesCategorySortKey
    : "netSalesCents";
  if (!naturalDate(startDate) || !naturalDate(endDate) || startDate > endDate) {
    throw new PublicApiError(400, "invalid_request", "startDate 和 endDate 必须使用 YYYY-MM-DD。");
  }
  const query = new URLSearchParams({
    startDate,
    endDate,
    level: "1",
    granularity: "month",
    sortBy,
    direction: args.direction === "asc" ? "asc" : "desc",
    page: "1",
    pageSize: String(integer(args.limit, 20, 50)),
  });
  for (const value of list(args.categories, 20)) query.append("category", value);
  for (const value of list(args.brands, 20)) query.append("brand", value);
  for (const value of list(args.channels, 20)) query.append("channel", value);
  for (const value of list(args.platforms, 20)) query.append("platform", value);
  for (const value of list(args.productQueries, 20)) query.append("productQuery", value);
  const response = await (options.route ?? routeDjangoSalesReadRequest)({
    request: new Request(`http://sales.internal/api/sales/category-analysis?${query.toString()}`),
    principal,
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new PublicApiError(503, "service_unavailable", "Django 销售读取服务暂时不可用，请稍后重试。");
  }
  const result = await response.json().catch(() => null) as Record<string, unknown> | null;
  const details = record(result?.details);
  const pagination = record(details.pagination);
  const trend = record(result?.trend);
  const filterOptions = record(result?.filterOptions);
  const categoryTotal = record(filterOptions.totals).categories;
  if (!result || !Array.isArray(result.ranking) || !Array.isArray(trend.items)
    || !Number.isSafeInteger(trend.returned) || Number(trend.returned) !== trend.items.length
    || typeof trend.truncated !== "boolean"
    || !Array.isArray(filterOptions.categories) || filterOptions.categories.length > 200
    || !filterOptions.categories.every(value => typeof value === "string" && value.length <= 120)
    || !Number.isSafeInteger(categoryTotal) || Number(categoryTotal) < filterOptions.categories.length
    || !Array.isArray(details.items) || !Number.isFinite(Number(pagination.total))) {
    throw new PublicApiError(503, "service_unavailable", "Django 销售读取响应无效，请稍后重试。");
  }
  return {
    range: result.range,
    comparisonPeriods: result.comparisonPeriods,
    dataCutoffDate: result.dataCutoffDate,
    categoryHierarchy: result.categoryHierarchy,
    filtersApplied: result.filtersApplied,
    categoryOptions: {
      items: filterOptions.categories,
      total: categoryTotal,
      truncated: Number(categoryTotal) > filterOptions.categories.length,
    },
    summary: result.summary,
    uncategorized: result.uncategorized,
    ranking: result.ranking,
    trend: result.trend,
    items: details.items,
    totalMatched: pagination.total,
    returned: pagination.returned,
    truncated: pagination.truncated,
  };
}
