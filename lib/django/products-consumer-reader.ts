import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoProductsService,
  PRODUCTS_CONSUMER_QUERY_PATH,
  type DjangoProductsServiceConfig,
} from "@/lib/django/products-service";
import type {
  ProductSummaryItem,
  ProductSummaryMetrics,
} from "@/lib/products/summary";
import { PublicApiError } from "@/lib/http/api-error";

export type ProductsConsumerRequestMap = {
  product_performance: {
    operation: "product_performance";
    days: number;
    category: string | null;
    query: string | null;
    sortBy: "netSalesCents" | "grossProfitCents" | "grossMarginRate" | "stockValueCents" | "netQuantity";
    direction: "asc" | "desc";
    limit: number;
  };
  import_batch_search: {
    operation: "import_batch_search";
    query: string;
    offset: number;
    limit: number;
  };
};

export type ProductsConsumerRequest = ProductsConsumerRequestMap[keyof ProductsConsumerRequestMap];

export type ProductsConsumerResponseMap = {
  product_performance: {
    sync: Record<string, unknown>;
    metrics: ProductSummaryMetrics;
    days: number;
    filtersApplied: Record<string, unknown>;
    totalMatched: number;
    returned: number;
    truncated: boolean;
    items: ProductSummaryItem[];
    currency: "CNY";
    monetaryUnit: "cents";
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
};

type ConsumerResult<R extends ProductsConsumerRequest> = {
  revision: string;
  data: ProductsConsumerResponseMap[R["operation"]];
};

function invalid(message = "商品经营消费查询参数无效") {
  return new PublicApiError(400, "invalid_request", message);
}

function validateRequest(request: ProductsConsumerRequest): void {
  if (request.operation === "import_batch_search") {
    if (typeof request.query !== "string" || request.query.length > 120
      || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 100_000
      || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) {
      throw invalid();
    }
    return;
  }
  if (!Number.isSafeInteger(request.days) || request.days < 7 || request.days > 365
    || (request.category !== null && (typeof request.category !== "string" || !request.category || request.category.length > 120))
    || (request.query !== null && (typeof request.query !== "string" || !request.query || request.query.length > 100))
    || !["netSalesCents", "grossProfitCents", "grossMarginRate", "stockValueCents", "netQuantity"].includes(request.sortBy)
    || !["asc", "desc"].includes(request.direction)
    || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) {
    throw invalid();
  }
}

export async function readDjangoProductsConsumer<R extends ProductsConsumerRequest>(
  principal: AppPrincipal,
  request: R,
  options: {
    config?: DjangoProductsServiceConfig;
    fetchImpl?: typeof fetch;
    now?: () => number;
    requestId?: () => string;
    signal?: AbortSignal;
  } = {},
): Promise<ConsumerResult<R>> {
  validateRequest(request);
  const service = createDjangoProductsService(options.config);
  const result = await service.requestJson<{
    operation: R["operation"];
    data: ProductsConsumerResponseMap[R["operation"]];
  }>(principal, {
    method: "POST",
    path: PRODUCTS_CONSUMER_QUERY_PATH,
    service: "reader",
    payload: request,
  }, options);
  if (result.data.operation !== request.operation || !result.revision) {
    throw new PublicApiError(503, "service_unavailable", "商品经营消费查询响应无效");
  }
  return { revision: result.revision, data: result.data.data };
}

export type ProductsConsumerReader = {
  read<R extends ProductsConsumerRequest>(
    principal: AppPrincipal,
    request: R,
    options?: { signal?: AbortSignal },
  ): Promise<ConsumerResult<R>>;
};

export function createDjangoProductsConsumerReader(
  config?: DjangoProductsServiceConfig,
): ProductsConsumerReader {
  return {
    read: (principal, request, options = {}) => readDjangoProductsConsumer(
      principal, request, { ...options, config },
    ),
  };
}
