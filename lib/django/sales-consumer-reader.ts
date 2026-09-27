import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createSalesGatewayAuthHeaders,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

/**
 * Fixed internal contract for non-sales domains that need bounded sales facts.
 * This is deliberately not an SQL proxy: every operation has an explicit
 * request and response shape that Django must authorize and implement.
 */
export const SALES_CONSUMER_QUERY_PATH = "/api/sales/consumers/query";
export const salesConsumerOperations = [
  "analysis_records",
  "freshness",
  "summary",
  "inventory_demand",
  "inventory_inbound_windows",
  "product_performance",
  "customer_service_products",
  "netshop_product_metrics",
  "market_product_metrics",
  "order_search",
  "import_batch_search",
  "category_options",
] as const;

export type SalesConsumerOperation = (typeof salesConsumerOperations)[number];

export type SalesOutletIdentity = {
  platform: string;
  shopName: string;
  channel?: string | null;
};

export type SalesResolvedOutletMatch = {
  platform: string;
  canonicalShopName: string;
  rawShopName: string;
  rawChannel: string | null;
};

export type SalesSummaryMetric = {
  grossSalesCents: number;
  netSalesCents: number;
  netSalesExcludingAccessoriesCents: number;
  costAmountCents: number;
  grossProfitCents: number;
  refundAmountCents: number;
  orderCount: number;
  lineCount: number;
  netQuantity: number;
  averageOrderValueCents: number;
  grossMarginRate: number;
  refundRate: number;
};

export type SalesSummaryGroup = SalesSummaryMetric & {
  groupKey: string;
  name: string;
  platform: string;
  shareRate: number;
  yearAgoNetSalesCents: number;
  salesYearOverYearRate: number | null;
};

export type SalesSummaryConsumerData = {
  range: "today" | "yesterday" | "last7" | "last15" | "last30" | "month" | "quarter" | "custom" | "all";
  startDate: string;
  endDate: string;
  previousStartDate?: string;
  previousEndDate?: string;
  requestedStartDate: string;
  requestedEndDate: string;
  dataCutoffDate: string | null;
  periodAdjustedToDataCutoff: boolean;
  comparisonDayCount: number;
  current: SalesSummaryMetric;
  previous?: SalesSummaryMetric;
  yearAgo: SalesSummaryMetric;
  yearAgoStartDate: string;
  yearAgoEndDate: string;
  channels: SalesSummaryGroup[];
  outlets: SalesSummaryGroup[];
  shops: SalesSummaryGroup[];
  platforms: SalesSummaryGroup[];
  daily: Array<SalesSummaryMetric & { date: string }>;
  previousDaily: Array<SalesSummaryMetric & { date: string }>;
  yearAgoDaily: Array<SalesSummaryMetric & { date: string }>;
  trendStartDate: string;
  trendEndDate: string;
  trendReturned: number;
  trendTruncated: boolean;
};

export type SalesProductAggregate = {
  productCode: string;
  productName: string;
  specification: string;
  category: string;
  supplier: string;
  netQuantity: number;
  grossSalesCents: number;
  refundAmountCents: number;
  netSalesCents: number;
  costCents: number;
  feeCents: number;
  grossProfitCents: number;
  absoluteQuantity: number;
  absoluteCostCents: number;
  outlets: SalesOutletIdentity[];
};

export type SalesConsumerRequestMap = {
  analysis_records: { operation: "analysis_records"; platform: string; shop: string; channel: string; startDate: string; endDate: string; window?: "current" | "previous" | "yearAgo"; limit?: number; cursor?: string };
  freshness: {
    operation: "freshness";
  };
  summary: {
    operation: "summary";
    range: SalesSummaryConsumerData["range"];
    startDate?: string;
    endDate?: string;
    productQueries?: string[];
    platforms?: string[];
    outlets?: SalesOutletIdentity[];
    categories?: string[];
  };
  inventory_demand: {
    operation: "inventory_demand";
    startDate?: string | null;
    endDate?: string | null;
    productCodes?: string[];
    limit?: number;
  };
  inventory_inbound_windows: {
    operation: "inventory_inbound_windows";
    asOfDate?: string | null;
    productCodes: string[];
    limit?: number;
  };
  product_performance: {
    operation: "product_performance";
    startDate: string;
    endDate: string;
    platforms?: string[];
    outlets?: SalesOutletIdentity[];
    productCodes?: string[];
    limit?: number;
  };
  customer_service_products: {
    operation: "customer_service_products";
    onlineSpecCodes?: string[];
    categories?: string[];
    limit?: number;
  };
  netshop_product_metrics: {
    operation: "netshop_product_metrics";
    identities: Array<SalesResolvedOutletMatch & { salesProductCode: string }>;
    outletScopes: SalesResolvedOutletMatch[];
    startDate?: string | null;
    endDate?: string | null;
    allowedChannels?: string[] | null;
  };
  market_product_metrics: {
    operation: "market_product_metrics";
    productCodes: string[];
    startDate?: string | null;
    endDate?: string | null;
  };
  order_search: {
    operation: "order_search";
    query: string;
    page: number;
    pageSize: number;
  };
  import_batch_search: {
    operation: "import_batch_search";
    query: string;
    page: number;
    pageSize: number;
  };
  category_options: {
    operation: "category_options";
    limit?: number;
  };
};

export type SalesConsumerResponseMap = {
  analysis_records: Record<string, unknown>;
  freshness: {
    dataStartDate: string | null;
    dataCutoffDate: string | null;
    latestBatch: {
      id: string;
      fileName: string;
      completedAt: string | null;
      rowCount: number;
    } | null;
  };
  summary: SalesSummaryConsumerData;
  inventory_demand: {
    dataStartDate: string | null;
    dataCutoffDate: string | null;
    rows: Array<{
      productCode: string;
      warehouseKey: string;
      productName: string;
      salesQuantity: number;
      absoluteQuantity: number;
      absoluteCostCents: number;
    }>;
    truncated: boolean;
  };
  inventory_inbound_windows: {
    asOfDate: string | null;
    dataStartDate: string | null;
    dataCutoffDate: string | null;
    rows: Array<{
      productCode: string;
      warehouseKey: string;
      productName: string;
      sales7dQuantity: number;
      sales15dQuantity: number;
      sales30dQuantity: number;
      sales90dQuantity: number;
    }>;
    truncated: boolean;
  };
  product_performance: {
    dataStartDate: string | null;
    dataCutoffDate: string | null;
    latestBatch: SalesConsumerResponseMap["freshness"]["latestBatch"];
    rows: SalesProductAggregate[];
    outletOptions: SalesOutletIdentity[];
    truncated: boolean;
  };
  customer_service_products: {
    rows: Array<{
      onlineSpecCode: string;
      productCode: string;
      category: string;
      latestAt: string | null;
    }>;
    truncated: boolean;
  };
  netshop_product_metrics: {
    dataCutoffDate: string | null;
    platform: string;
    rows: Array<{
      platform: string;
      shopName: string;
      salesProductCode: string;
      grossSalesCents: number;
      refundAmountCents: number;
      netSalesCents: number;
      grossProfitCents: number;
      absoluteQuantity: number;
      absoluteCostCents: number;
    }>;
  };
  market_product_metrics: {
    rows: Array<{
      productCode: string;
      owned: boolean;
      ownSalesCents: number;
    }>;
  };
  order_search: {
    items: Array<{
      id: string;
      title: string;
      subtitle: string;
      detail: string;
      updatedAt: string;
      amountCents: number;
    }>;
    total: number;
    truncated: boolean;
  };
  import_batch_search: {
    items: Array<{
      id: string;
      source: string;
      fileName: string;
      status: string;
      rowCount: number;
      createdAt: string;
      completedAt: string | null;
    }>;
    total: number;
    truncated: boolean;
  };
  category_options: {
    categories: string[];
    truncated: boolean;
  };
};

export type SalesConsumerRequest = SalesConsumerRequestMap[SalesConsumerOperation];

export type SalesConsumerReaderResult<R extends SalesConsumerRequest> = {
  revision: string;
  data: SalesConsumerResponseMap[R["operation"]];
};

export type SalesConsumerReaderConfig = {
  djangoBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type SalesConsumerReaderOptions = {
  config?: SalesConsumerReaderConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

const DEFAULT_TIMEOUT_MS = 12_000;
const MAX_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_REQUEST_BYTES = 512 * 1024;
const MAX_REQUEST_BYTES = 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const encoder = new TextEncoder();

type RuntimeEnvironment = Record<string, string | undefined>;

function unavailable(): PublicApiError {
  return new PublicApiError(503, "service_unavailable", "Django 销售读取服务暂时不可用，请稍后重试。");
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) throw unavailable();
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable();
  return boundedInteger(Number(value), fallback, maximum);
}

async function loadConfig(): Promise<SalesConsumerReaderConfig> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Unit tests and non-Worker tools may inject config or use process.env.
  }
  const processEnvironment = globalThis.process?.env as RuntimeEnvironment | undefined;
  const value = (key: string) => workerEnvironment[key] ?? processEnvironment?.[key];
  const djangoBaseUrl = value("TERUISI_DJANGO_SALES_READER_BASE_URL")
    ?? value("TERUISI_DJANGO_SALES_BASE_URL")
    ?? "";
  const internalSecret = value("TERUISI_DJANGO_INTERNAL_SECRET") ?? "";
  return {
    djangoBaseUrl,
    internalSecret,
    timeoutMs: environmentInteger(
      value("TERUISI_DJANGO_SALES_READER_TIMEOUT_MS") ?? value("TERUISI_DJANGO_SALES_TIMEOUT_MS"),
      DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ),
    maxRequestBytes: environmentInteger(
      value("TERUISI_DJANGO_SALES_READER_MAX_REQUEST_BYTES"),
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: environmentInteger(
      value("TERUISI_DJANGO_SALES_READER_MAX_RESPONSE_BYTES") ?? value("TERUISI_DJANGO_SALES_MAX_RESPONSE_BYTES"),
      DEFAULT_MAX_RESPONSE_BYTES,
      MAX_RESPONSE_BYTES,
    ),
  };
}

function normalizeBaseUrl(value: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw unavailable();
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable();
  }
  return parsed;
}

function normalizeConfig(config: SalesConsumerReaderConfig) {
  const secret = config.internalSecret;
  if (encoder.encode(secret).byteLength < 32) throw unavailable();
  return {
    baseUrl: normalizeBaseUrl(config.djangoBaseUrl),
    internalSecret: secret,
    timeoutMs: boundedInteger(config.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: boundedInteger(config.maxRequestBytes, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: boundedInteger(config.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isOperation(value: unknown): value is SalesConsumerOperation {
  return typeof value === "string" && salesConsumerOperations.some((operation) => operation === value);
}

function assertRequest(request: SalesConsumerRequest): void {
  if (!isRecord(request) || !isOperation(request.operation)) throw unavailable();
  const keys = new Set(Object.keys(request));
  const allowed: Record<SalesConsumerOperation, readonly string[]> = {
    analysis_records: ["operation", "platform", "shop", "channel", "startDate", "endDate", "window", "limit", "cursor"],
    freshness: ["operation"],
    summary: ["operation", "range", "startDate", "endDate", "productQueries", "platforms", "outlets", "categories"],
    inventory_demand: ["operation", "startDate", "endDate", "productCodes", "limit"],
    inventory_inbound_windows: ["operation", "asOfDate", "productCodes", "limit"],
    product_performance: ["operation", "startDate", "endDate", "platforms", "outlets", "productCodes", "limit"],
    customer_service_products: ["operation", "onlineSpecCodes", "categories", "limit"],
    netshop_product_metrics: ["operation", "identities", "outletScopes", "startDate", "endDate", "allowedChannels"],
    market_product_metrics: ["operation", "productCodes", "startDate", "endDate"],
    order_search: ["operation", "query", "page", "pageSize"],
    import_batch_search: ["operation", "query", "page", "pageSize"],
    category_options: ["operation", "limit"],
  };
  if ([...keys].some((key) => !allowed[request.operation].includes(key))) throw unavailable();
}

function contentTypeIsJson(value: string | null): boolean {
  return typeof value === "string" && /^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(value);
}

function readRevision(headers: Headers): string {
  const dataRevision = headers.get("x-sales-data-revision")?.trim() ?? "";
  const sourceRevision = headers.get("x-sales-source-revision")?.trim() ?? "";
  if (!dataRevision || dataRevision.length > 128 || dataRevision !== sourceRevision) throw unavailable();
  return dataRevision;
}

export async function readDjangoSalesConsumer<R extends SalesConsumerRequest>(
  principal: AppPrincipal,
  request: R,
  options: SalesConsumerReaderOptions = {},
): Promise<SalesConsumerReaderResult<R>> {
  assertRequest(request);
  const config = normalizeConfig(options.config ?? await loadConfig());
  const body = encoder.encode(JSON.stringify(request));
  if (body.byteLength > config.maxRequestBytes) throw unavailable();
  const bodySha256 = await salesGatewayBodySha256(body);
  const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
  const timestamp = Math.floor((options.now ?? Date.now)() / 1_000);
  const headers = await createSalesGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: "POST",
    path: SALES_CONSUMER_QUERY_PATH,
    rawQuery: "",
    bodySha256,
    timestamp,
    requestId,
  });
  headers.set("content-type", "application/json; charset=utf-8");

  try {
    const { response, data } = await fetchBoundedJson({
      url: new URL(SALES_CONSUMER_QUERY_PATH, config.baseUrl).toString(),
      init: {
        method: "POST",
        headers,
        body,
        cache: "no-store",
      },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (response.status < 200 || response.status >= 300 || !contentTypeIsJson(response.headers.get("content-type"))) {
      throw unavailable();
    }
    const revision = readRevision(response.headers);
    if (!isRecord(data) || data.operation !== request.operation || !Object.hasOwn(data, "data")) throw unavailable();
    return {
      revision,
      data: data.data as SalesConsumerResponseMap[R["operation"]],
    };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}

export type SalesConsumerReader = {
  read<R extends SalesConsumerRequest>(
    principal: AppPrincipal,
    request: R,
    options?: Omit<SalesConsumerReaderOptions, "config">,
  ): Promise<SalesConsumerReaderResult<R>>;
};

export function createDjangoSalesConsumerReader(
  config?: SalesConsumerReaderConfig,
): SalesConsumerReader {
  return {
    read: (principal, request, options = {}) => readDjangoSalesConsumer(
      principal,
      request,
      { ...options, config },
    ),
  };
}
