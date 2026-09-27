export const salesRanges = ["today", "yesterday", "last7", "last15", "last30", "month", "quarter", "custom", "all"] as const;
export type SalesRange = (typeof salesRanges)[number];
export const SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH = 1000;

export const salesCategoryGranularities = ["day", "week", "month"] as const;
export type SalesCategoryGranularity = (typeof salesCategoryGranularities)[number];

export const salesCategorySortKeys = [
  "netSalesCents",
  "shareRate",
  "netQuantity",
  "refundRate",
  "weekOverWeekRate",
  "yearOverYearRate",
  "positiveQuantity",
  "returnQuantity",
  "refundAmountCents",
  "grossProfitCents",
  "grossMarginRate",
  "productCount",
] as const;
export type SalesCategorySortKey = (typeof salesCategorySortKeys)[number];

export class SalesReadRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SalesReadRequestError";
  }
}

export function isSalesRange(value: string): value is SalesRange {
  return salesRanges.includes(value as SalesRange);
}

function normalizedProductQueries(values: string | string[]) {
  return (Array.isArray(values) ? values : [values])
    .flatMap((value) => value.split(/[\r\n,，;；]+/))
    .flatMap((value) => {
      const trimmed = value.trim();
      if (!trimmed) return [];
      return /[\u3400-\u9fff]/.test(trimmed) ? [trimmed] : trimmed.split(/\s+/);
    })
    .map((value) => value.trim())
    .filter(Boolean);
}

export function parseProductQueriesStrict(values: string | string[]): string[] {
  const queries = [...new Set(normalizedProductQueries(values))];
  if (queries.length > 100 || queries.some((value) => value.length > 200)) {
    throw new SalesReadRequestError("商品筛选最多 100 项，且每项不能超过 200 字。");
  }
  if ((Array.isArray(values) ? values.join("\n") : values).trim().length > SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH) {
    throw new SalesReadRequestError(`货品筛选不能超过 ${SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH} 个字符。`);
  }
  return queries;
}

/**
 * Client-side convenience parser. The server still applies the strict
 * validation above; the UI only keeps the first 100 normalized entries so a
 * pasted list cannot produce an unbounded query string.
 */
export function parseProductQueries(values: string | string[]): string[] {
  return [...new Set(normalizedProductQueries(values))].slice(0, 100);
}
