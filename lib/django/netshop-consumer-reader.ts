import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoNetshopService,
  NETSHOP_CONSUMER_QUERY_PATH,
  type DjangoNetshopServiceConfig,
  type DjangoNetshopServiceOptions,
} from "@/lib/django/netshop-service";
import { PublicApiError } from "@/lib/http/api-error";

type SearchItem = {
  id: string;
  title: string;
  subtitle: string;
  detail: string;
  updatedAt: string;
  amountCents: number | null;
};

type BatchItem = {
  id: string;
  source: string;
  dataset: string;
  platform: string;
  shopName: string;
  fileName: string;
  status: string;
  rowCount: number;
  createdAt: string;
  completedAt: string | null;
};

type ProductMasterLookupRow = {
  skuId: string;
  spuId: string;
  productCode: string;
  onlineSpecCode: string;
  raw: Record<string, unknown>;
};

export type MarketNetshopProjectionRow = {
  projectionKey: string;
  kind: "metric" | "identity" | "brand";
  source: string;
  dataset: string;
  platform: string;
  shopName: string;
  businessDate: string;
  skuId: string;
  spuId: string;
  productCode: string;
  transactionAmountCents: number;
  brand: string;
};

export type NetshopConsumerRequestMap = {
  row_search: { operation: "row_search"; query: string; offset: number; limit: number };
  import_batch_search: { operation: "import_batch_search"; query: string; offset: number; limit: number };
  brand_options: { operation: "brand_options"; query: string; limit: number };
  product_master_lookup: {
    operation: "product_master_lookup";
    lookupCodes: string[];
    spuIds: string[];
    limit: number;
  };
  market_projection_page: {
    operation: "market_projection_page";
    offset: number;
    limit: number;
    expectedRevision: string | null;
  };
};

export type NetshopConsumerResponseMap = {
  row_search: { items: SearchItem[]; total: number; truncated: boolean };
  import_batch_search: { items: BatchItem[]; total: number; truncated: boolean };
  brand_options: { items: string[]; truncated: boolean };
  product_master_lookup: { rows: ProductMasterLookupRow[]; truncated: boolean };
  market_projection_page: {
    rows: MarketNetshopProjectionRow[];
    total: number;
    truncated: boolean;
  };
};

export type NetshopConsumerRequest = NetshopConsumerRequestMap[keyof NetshopConsumerRequestMap];
export type NetshopConsumerReaderOptions = Omit<DjangoNetshopServiceOptions, "config">;

function unavailable() {
  return new PublicApiError(503, "service_unavailable", "Django 网店读取服务暂时不可用，请稍后重试。");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function assertRequest(request: NetshopConsumerRequest): void {
  if (!isRecord(request) || typeof request.operation !== "string"
    || !["row_search", "import_batch_search", "brand_options", "product_master_lookup", "market_projection_page"].includes(request.operation)
    || !Number.isSafeInteger(request.limit) || request.limit < 1
    || request.limit > (request.operation === "product_master_lookup" ? 10_000 : request.operation === "market_projection_page" ? 1_000 : request.operation === "brand_options" ? 500 : 100)) {
    throw unavailable();
  }
  if (request.operation === "market_projection_page") {
    if (Object.keys(request).length !== 4
      || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 1_000_000
      || (request.expectedRevision !== null
        && (typeof request.expectedRevision !== "string" || !request.expectedRevision || request.expectedRevision.length > 96))) {
      throw unavailable();
    }
    return;
  }
  if (request.operation === "product_master_lookup") {
    if (Object.keys(request).length !== 4) throw unavailable();
    for (const values of [request.lookupCodes, request.spuIds]) {
      if (!Array.isArray(values) || values.length > 5_000
        || values.some((value) => typeof value !== "string" || !value.trim() || value.trim().length > 200)) {
        throw unavailable();
      }
    }
    return;
  }
  if (typeof request.query !== "string" || Array.from(request.query.trim()).length > 80) {
    throw unavailable();
  }
  if (request.operation === "row_search" && Array.from(request.query.trim()).length < 2) {
    throw unavailable();
  }
  if (request.operation === "brand_options") {
    if (Object.keys(request).length !== 3) throw unavailable();
    return;
  }
  if (Object.keys(request).length !== 4
    || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 80_000) {
    throw unavailable();
  }
}

export async function readDjangoNetshopConsumer<R extends NetshopConsumerRequest>(
  principal: AppPrincipal,
  request: R,
  options: NetshopConsumerReaderOptions & { config?: DjangoNetshopServiceConfig } = {},
): Promise<{ revision: string; data: NetshopConsumerResponseMap[R["operation"]] }> {
  assertRequest(request);
  const { config, ...serviceOptions } = options;
  const result = await createDjangoNetshopService(config).request<Record<string, unknown>>(
    principal,
    {
      method: "POST",
      path: NETSHOP_CONSUMER_QUERY_PATH,
      payload: request as unknown as Record<string, unknown>,
      service: "reader",
    },
    serviceOptions,
  );
  if (!result.revision || !/^\d+:[a-f0-9]{12}$/.test(result.revision)
    || result.data.operation !== request.operation || !isRecord(result.data.data)) {
    throw unavailable();
  }
  return {
    revision: result.revision,
    data: result.data.data as NetshopConsumerResponseMap[R["operation"]],
  };
}

export type NetshopConsumerReader = {
  read<R extends NetshopConsumerRequest>(
    principal: AppPrincipal,
    request: R,
    options?: NetshopConsumerReaderOptions,
  ): Promise<{ revision: string; data: NetshopConsumerResponseMap[R["operation"]] }>;
};

export function createDjangoNetshopConsumerReader(config?: DjangoNetshopServiceConfig): NetshopConsumerReader {
  return {
    read: (principal, request, options = {}) => readDjangoNetshopConsumer(
      principal,
      request,
      { ...options, config },
    ),
  };
}
