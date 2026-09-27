import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoProductsService,
  PRODUCTS_SUMMARY_PATH,
  type DjangoProductsServiceOptions,
} from "@/lib/django/products-service";
import {
  type ProductMarginBand,
  type ProductSummaryDirection,
  type ProductSummaryQueryOptions,
  type ProductSummaryRange,
  type ProductSummarySort,
} from "@/lib/products/query-contract";

export type { ProductSummaryRange } from "@/lib/products/query-contract";
export { ProductSummaryContractError as ProductSummaryRequestError } from "@/lib/products/query-contract";

export type ProductSummaryItem = {
  productCode: string;
  productName: string;
  brand: string;
  supplierName: string;
  specification: string;
  category: string;
  outlets: Array<{ platform: string; shop: string }>;
  netQuantity: number;
  grossSalesCents: number;
  refundAmountCents: number;
  netSalesCents: number;
  costCents: number;
  feeCents: number;
  grossProfitCents: number;
  grossMarginRate: number | null;
  refundRate: number;
  shippingRate: number | null;
  averageSalePriceCents: number | null;
  averageCostCents: number | null;
  observedFeeRate: number | null;
  availableQuantity: number | null;
  stockValueCents: number | null;
  knownStockValueCents: number | null;
  costCoverageRate: number | null;
};

export type ProductSummaryOptions = ProductSummaryQueryOptions & {
  platforms?: string[];
  shopKeys?: string[];
  projection?: ProductSummaryProjection;
  expectedSnapshotToken?: string;
  signal?: AbortSignal;
};

export type ProductSummaryProjection = "full" | "page";

export type ProductSummaryPagination = {
  page: number;
  pageSize: number;
  total: number;
  returned: number;
  totalPages: number;
  truncated: boolean;
};

export type ProductSummaryPageResponse = {
  projection: "page";
  snapshotToken: string;
  sort: { by: ProductSummarySort; direction: ProductSummaryDirection };
  pagination: ProductSummaryPagination;
  items: ProductSummaryItem[];
};

export type ProductSummaryMetrics = {
  skuCount: number;
  grossSalesCents: number;
  netSalesCents: number;
  grossProfitCents: number;
  grossMarginRate: number | null;
  lossSkuCount: number;
  stockedSkuCount: number;
  marginBuckets: {
    below35Count: number;
    between35And40Count: number;
    between40And45Count: number;
    atLeast45Count: number;
  };
};

export type ProductSummaryFullResponse = {
  projection: "full";
  snapshotToken: string;
  hasSales: boolean;
  range: ProductSummaryRange;
  sync: {
    salesThrough: string | null;
    salesWindowStart: string | null;
    requestedStartDate: string | null;
    requestedEndDate: string | null;
    dataStartDate: string | null;
    dataCutoffDate: string | null;
    inventoryAsOf: string | null;
    latestSalesFile: string | null;
  };
  filters: {
    platforms: string[];
    shops: Array<{ key: string; platform: string; shop: string }>;
    categories: string[];
  };
  filtersApplied: {
    platforms: string[];
    shops: Array<{ key: string; platform: string; shop: string }>;
    query: string;
    categories: string[];
    marginBands: ProductMarginBand[];
  };
  sort: { by: ProductSummarySort; direction: ProductSummaryDirection };
  metrics: ProductSummaryMetrics;
  pagination: ProductSummaryPagination;
  items: ProductSummaryItem[];
};

export type ProductsSummaryReader = {
  requestJson<T>(
    principal: AppPrincipal,
    input: {
      method: "GET";
      path: typeof PRODUCTS_SUMMARY_PATH;
      service: "reader";
      rawQuery: string;
    },
    options?: Omit<DjangoProductsServiceOptions, "config">,
  ): Promise<{ data: T }>;
};

function appendList(params: URLSearchParams, name: string, values: readonly string[] | undefined) {
  for (const value of values ?? []) params.append(name, value);
}

function summaryQuery(options: ProductSummaryOptions) {
  const params = new URLSearchParams();
  if (options.range) params.set("range", options.range);
  if (options.startDate) params.set("startDate", options.startDate);
  if (options.endDate) params.set("endDate", options.endDate);
  if (options.days !== undefined) params.set("days", String(options.days));
  appendList(params, "platform", options.platforms);
  appendList(params, "shop", options.shopKeys);
  if (options.page !== undefined) params.set("page", String(options.page));
  if (options.pageSize !== undefined) params.set("pageSize", String(options.pageSize));
  if (options.query) params.set("q", options.query);
  appendList(params, "category", options.categories);
  appendList(params, "marginBand", options.marginBands);
  if (options.sortBy) params.set("sortBy", options.sortBy);
  if (options.direction) params.set("direction", options.direction);
  if (options.projection === "page") {
    params.set("view", "page");
    if (options.expectedSnapshotToken) params.set("snapshotToken", options.expectedSnapshotToken);
  }
  return params.toString();
}

export function getProductSummary(
  principal: AppPrincipal,
  options: Omit<ProductSummaryOptions, "projection"> & { projection: "page" },
  reader?: ProductsSummaryReader,
): Promise<ProductSummaryPageResponse>;
export function getProductSummary(
  principal: AppPrincipal,
  options?: Omit<ProductSummaryOptions, "projection"> & { projection?: "full" },
  reader?: ProductsSummaryReader,
): Promise<ProductSummaryFullResponse>;
export function getProductSummary(
  principal: AppPrincipal,
  options: ProductSummaryOptions,
  reader?: ProductsSummaryReader,
): Promise<ProductSummaryFullResponse | ProductSummaryPageResponse>;
export async function getProductSummary(
  principal: AppPrincipal,
  options: ProductSummaryOptions = {},
  reader: ProductsSummaryReader = createDjangoProductsService(),
): Promise<ProductSummaryFullResponse | ProductSummaryPageResponse> {
  const result = await reader.requestJson<ProductSummaryFullResponse | ProductSummaryPageResponse>(
    principal,
    {
      method: "GET",
      path: PRODUCTS_SUMMARY_PATH,
      service: "reader",
      rawQuery: summaryQuery(options),
    },
    { signal: options.signal },
  );
  return result.data;
}
