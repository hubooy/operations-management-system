export type ProductSummaryRange = "last30" | "last90" | "halfYear" | "custom";
export type ProductSummarySort = "netSalesCents" | "grossProfitCents" | "grossMarginRate" | "refundRate" | "stockValueCents" | "netQuantity";
export type ProductSummaryDirection = "asc" | "desc";
export type ProductMarginBand = "below35" | "35to40" | "40to45" | "atLeast45" | "unavailable";
export const PRODUCT_SUMMARY_QUERY_MAX_LENGTH = 1000;

export type ProductSummaryQueryOptions = {
  range?: ProductSummaryRange;
  startDate?: string | null;
  endDate?: string | null;
  days?: number;
  page?: number;
  pageSize?: number;
  query?: string;
  categories?: string[];
  marginBands?: ProductMarginBand[];
  sortBy?: ProductSummarySort;
  direction?: ProductSummaryDirection;
};

export class ProductSummaryContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProductSummaryContractError";
  }
}

export function normalizeProductSummaryQuery(value: string) {
  const query = value.trim();
  if (query.length > PRODUCT_SUMMARY_QUERY_MAX_LENGTH) {
    throw new ProductSummaryContractError(`搜索词不能超过 ${PRODUCT_SUMMARY_QUERY_MAX_LENGTH} 个字符`);
  }
  return query;
}

export function normalizeProductSummarySelections(
  values: readonly string[],
  options: { maximum: number; allowed?: readonly string[]; label: string; maximumLength?: number },
) {
  const normalized = [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  const maximumLength = options.maximumLength ?? 120;
  if (normalized.length > options.maximum || normalized.some((value) => value.length > maximumLength)) {
    throw new ProductSummaryContractError(`${options.label}筛选项数量或长度超出限制`);
  }
  const allowed = options.allowed;
  if (allowed && normalized.some((value) => !allowed.includes(value))) {
    throw new ProductSummaryContractError(`${options.label}筛选项不在允许清单中`);
  }
  return normalized;
}

export function parseProductSummaryPaginationParameter(value: string | null, field: "page" | "pageSize") {
  if (value === null) return undefined;
  if (!/^[1-9]\d*$/.test(value)) {
    throw new ProductSummaryContractError(`${field} 必须是十进制正整数`);
  }
  const parsed = Number(value);
  const maximum = field === "page" ? 10_000 : 100;
  if (!Number.isSafeInteger(parsed) || parsed > maximum) {
    throw new ProductSummaryContractError(`${field} 必须是 1 到 ${maximum} 的整数`);
  }
  return parsed;
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function dayDifference(start: string, end: string) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000);
}

function isIsoDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

export function normalizeProductSummaryPagination(options: { page?: number; pageSize?: number }) {
  const page = options.page ?? 1;
  const pageSize = options.pageSize ?? 50;
  if (!Number.isSafeInteger(page) || page < 1 || page > 10_000) {
    throw new ProductSummaryContractError("page 必须是 1 到 10000 的整数");
  }
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) {
    throw new ProductSummaryContractError("pageSize 必须是 1 到 100 的整数");
  }
  return { page, pageSize, offset: (page - 1) * pageSize };
}

export function resolveProductSummaryPeriod(
  bounds: { startDate: string; endDate: string },
  options: ProductSummaryQueryOptions,
) {
  let range = options.range ?? "last30";
  let requestedStartDate: string;
  let requestedEndDate: string;
  if (range === "custom") {
    requestedStartDate = options.startDate?.trim() ?? "";
    requestedEndDate = options.endDate?.trim() ?? "";
    if (!isIsoDate(requestedStartDate) || !isIsoDate(requestedEndDate)) {
      throw new ProductSummaryContractError("自定义时间需要填写有效的开始和结束日期");
    }
    if (requestedStartDate > requestedEndDate) {
      throw new ProductSummaryContractError("自定义时间的开始日期不能晚于结束日期");
    }
    if (dayDifference(requestedStartDate, requestedEndDate) + 1 > 730) {
      throw new ProductSummaryContractError("商品统计周期最多支持 730 天");
    }
  } else {
    const requestedDays = !options.range && Number.isFinite(options.days)
      ? Math.max(7, Math.min(365, Math.trunc(options.days ?? 30)))
      : range === "halfYear" ? 183 : range === "last90" ? 90 : 30;
    range = requestedDays >= 180 ? "halfYear" : requestedDays >= 90 ? "last90" : "last30";
    requestedStartDate = addDays(bounds.endDate, -(requestedDays - 1));
    requestedEndDate = bounds.endDate;
  }
  const startDate = requestedStartDate > bounds.startDate ? requestedStartDate : bounds.startDate;
  const endDate = requestedEndDate < bounds.endDate ? requestedEndDate : bounds.endDate;
  return {
    range,
    requestedStartDate,
    requestedEndDate,
    startDate: startDate <= endDate ? startDate : null,
    endDate: startDate <= endDate ? endDate : null,
  };
}

export function addProductSummaryDays(value: string, days: number) {
  return addDays(value, days);
}
