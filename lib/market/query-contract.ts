import { PublicApiError } from "@/lib/http/api-error";

const MAX_MARKET_FACET_VALUES = 100;

function optionalNaturalDate(params: URLSearchParams, name: "startDate" | "endDate") {
  const values = params.getAll(name);
  if (values.length > 1) throw new PublicApiError(400, "invalid_request", `${name} 只能提供一次。`);
  const value = values[0]?.trim() ?? "";
  if (!value) return undefined;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) throw new PublicApiError(400, "invalid_request", `${name} 必须是有效的 YYYY-MM-DD 自然日。`);
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (year < 1900 || year > 2199
    || parsed.getUTCFullYear() !== year
    || parsed.getUTCMonth() !== month - 1
    || parsed.getUTCDate() !== day) {
    throw new PublicApiError(400, "invalid_request", `${name} 必须是有效的 YYYY-MM-DD 自然日。`);
  }
  return value;
}

function boundedInteger(value: string | null, fallback: number, minimum: number, maximum: number, name: string) {
  if (value === null) return fallback;
  if (!/^[1-9]\d*$/.test(value)) {
    throw new PublicApiError(400, "invalid_request", `${name} 必须为十进制正整数。`);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new PublicApiError(400, "invalid_request", `${name} 必须在 ${minimum} 到 ${maximum} 之间。`);
  }
  return parsed;
}

function facetValues(params: URLSearchParams, name: string, maximum: number) {
  const values = [...new Set(params.getAll(name).map((value) => value.trim()).filter(Boolean))];
  if (values.length > maximum || values.some((value) => value.length > 120)) {
    throw new PublicApiError(400, "invalid_request", `${name} 筛选项数量或长度超出限制。`);
  }
  return values;
}

export function parseMarketOverviewQuery(params: URLSearchParams) {
  const requestedView = params.get("view") ?? "full";
  if (requestedView !== "full" && requestedView !== "ranking") {
    throw new PublicApiError(400, "invalid_request", "view 必须为 full 或 ranking。");
  }
  const view: "full" | "ranking" = requestedView;
  const pagination = view === "ranking" ? {
    page: boundedInteger(params.get("page"), 1, 1, 1_000_000, "page"),
    pageSize: boundedInteger(params.get("pageSize"), 20, 10, 50, "pageSize"),
  } : { page: 1, pageSize: 50 };
  const query = params.get("q")?.trim() || undefined;
  if (query && query.length > 100) throw new PublicApiError(400, "invalid_request", "q 不能超过 100 个字符。");
  const categories = facetValues(params, "category", 30);
  const scopes = facetValues(params, "scope", 30);
  const brands = facetValues(params, "brand", 30);
  const rankingDimensions = facetValues(params, "dimension", 30);
  const operationModes = facetValues(params, "operationMode", 30);
  const subcategories = facetValues(params, "subcategory", 30);
  const priceBands = facetValues(params, "priceBand", 20);
  const facetCount = categories.length + scopes.length + brands.length + rankingDimensions.length
    + operationModes.length + subcategories.length + priceBands.length;
  if (facetCount > MAX_MARKET_FACET_VALUES) {
    throw new PublicApiError(400, "invalid_request", `筛选项合计不能超过 ${MAX_MARKET_FACET_VALUES} 个。`);
  }
  const startDate = optionalNaturalDate(params, "startDate");
  const endDate = optionalNaturalDate(params, "endDate");
  if (Boolean(startDate) !== Boolean(endDate)) {
    throw new PublicApiError(400, "invalid_request", "startDate 和 endDate 必须同时提供。");
  }
  if (startDate && endDate && startDate > endDate) {
    throw new PublicApiError(400, "invalid_request", "startDate 不能晚于 endDate。");
  }
  return {
    view,
    pagination,
    filters: {
      query,
      categories,
      scopes,
      brands,
      rankingDimensions,
      operationModes,
      subcategories,
      priceBands,
      startDate,
      endDate,
    },
  };
}
