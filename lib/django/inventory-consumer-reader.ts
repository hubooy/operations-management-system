import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_CONSUMER_QUERY_PATH,
  type DjangoInventoryServiceConfig,
} from "@/lib/django/inventory-service";
import { PublicApiError } from "@/lib/http/api-error";

export type InventoryConsumerRequestMap = {
  freshness: { operation: "freshness" };
  inventory_health: {
    operation: "inventory_health";
    warehouse: string | null;
    category: string | null;
    status: "no_stock" | "urgent" | "warning" | "stale" | "slow" | "healthy" | null;
    query: string | null;
    limit: number;
  };
  import_batch_search: {
    operation: "import_batch_search";
    dataset: "stock" | "age" | null;
    query: string;
    offset: number;
    limit: number;
  };
  stock_projection: { operation: "stock_projection"; offset: number; limit: number };
  system_cost_snapshot: { operation: "system_cost_snapshot" };
  inventory_search: { operation: "inventory_search"; query: string; offset: number; limit: number };
  age_search: { operation: "age_search"; query: string; offset: number; limit: number };
  replenishment_search: {
    operation: "replenishment_search";
    query: string;
    status: "draft" | "confirmed" | "completed" | "cancelled" | null;
    warehouse: string | null;
    offset: number;
    limit: number;
  };
  work_item_reference: {
    operation: "work_item_reference";
    kind: "procurement" | "stale_cleanup";
    referenceId: string;
  };
};

export type InventoryConsumerRequest = InventoryConsumerRequestMap[keyof InventoryConsumerRequestMap];

export type InventoryConsumerResponseMap = {
  freshness: {
    stock: { id: string; snapshotDate: string; fileName: string; completedAt: string | null; rowCount: number } | null;
    age: { id: string; snapshotDate: string; fileName: string; completedAt: string | null; rowCount: number } | null;
  };
  inventory_health: Record<string, unknown>;
  import_batch_search: { items: Array<Record<string, unknown>>; total: number; truncated: boolean };
  stock_projection: {
    batchId: string | null;
    snapshotDate: string | null;
    total: number;
    offset: number;
    rows: Array<{
      productCode: string;
      brand: string;
      availableQuantity: number;
      knownStockValueCents: number;
      pricedAvailableQuantity: number;
    }>;
    truncated: boolean;
  };
  system_cost_snapshot: {
    snapshot: {
      batchId: string;
      snapshotDate: string;
      costs: Array<{ productCode: string; warehouse: string; unitCostCents: number }>;
    } | null;
  };
  inventory_search: { items: Array<Record<string, unknown>>; total: number; truncated: boolean };
  age_search: { items: Array<Record<string, unknown>>; total: number; truncated: boolean };
  replenishment_search: { items: Array<Record<string, unknown>>; total: number; truncated: boolean };
  work_item_reference: Record<string, unknown>;
};

function invalid() {
  return new PublicApiError(400, "invalid_request", "库存消费查询参数无效");
}

function validate(request: InventoryConsumerRequest) {
  if (request.operation === "freshness" || request.operation === "system_cost_snapshot") return;
  if (request.operation === "stock_projection") {
    if (!Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 100_000
      || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 2_000) throw invalid();
    return;
  }
  if (request.operation === "inventory_health") {
    if ((request.warehouse !== null && (!request.warehouse || request.warehouse.length > 120))
      || (request.category !== null && (!request.category || request.category.length > 120))
      || (request.query !== null && (!request.query || request.query.length > 100))
      || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) throw invalid();
    return;
  }
  if (request.operation === "work_item_reference") {
    if (!request.referenceId || request.referenceId.length > 240) throw invalid();
    return;
  }
  if (request.operation === "replenishment_search") {
    if ((request.warehouse !== null && (!request.warehouse || request.warehouse.length > 120))
      || (request.status !== null && !["draft", "confirmed", "completed", "cancelled"].includes(request.status))
      || typeof request.query !== "string" || request.query.length > 100
      || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 100_000
      || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) throw invalid();
    return;
  }
  if (typeof request.query !== "string" || request.query.length > 120
    || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 100_000
    || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) throw invalid();
}

export async function readDjangoInventoryConsumer<R extends InventoryConsumerRequest>(
  principal: AppPrincipal,
  request: R,
  options: {
    config?: DjangoInventoryServiceConfig;
    fetchImpl?: typeof fetch;
    now?: () => number;
    requestId?: () => string;
    signal?: AbortSignal;
  } = {},
): Promise<{ revision: string; data: InventoryConsumerResponseMap[R["operation"]] }> {
  validate(request);
  const result = await createDjangoInventoryService(options.config).requestJson<{
    operation: R["operation"];
    data: InventoryConsumerResponseMap[R["operation"]];
  }>(principal, {
    method: "POST",
    path: INVENTORY_CONSUMER_QUERY_PATH,
    service: "reader",
    payload: request,
  }, options);
  if (result.data.operation !== request.operation || !result.revision) {
    throw new PublicApiError(503, "service_unavailable", "库存消费查询响应无效");
  }
  return { revision: result.revision, data: result.data.data };
}

export type InventoryConsumerReader = {
  read<R extends InventoryConsumerRequest>(
    principal: AppPrincipal,
    request: R,
    options?: { signal?: AbortSignal },
  ): Promise<{ revision: string; data: InventoryConsumerResponseMap[R["operation"]] }>;
};

export function createDjangoInventoryConsumerReader(config?: DjangoInventoryServiceConfig): InventoryConsumerReader {
  return {
    read: (principal, request, options = {}) => readDjangoInventoryConsumer(principal, request, { ...options, config }),
  };
}
