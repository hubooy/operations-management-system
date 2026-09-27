import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoCustomerService,
  CUSTOMER_SERVICE_CONSUMER_PATH,
  type DjangoCustomerServiceConfig,
  type DjangoCustomerServiceOptions,
} from "@/lib/django/customer-service";
import { PublicApiError } from "@/lib/http/api-error";

export type CustomerServiceConsumerRequest =
  | { operation: "search"; query: string; offset: number; limit: number; includeMessages: boolean }
  | { operation: "import_batch_search"; query: string; offset: number; limit: number };

export type CustomerServiceSearchResponse = {
  items: Array<{ resultId: string; title: string; subtitle: string; detail: string; updatedAt: string; amountCents: null }>;
  total: number;
  truncated: boolean;
};

export type CustomerServiceImportSearchResponse = {
  items: Array<{ id: string; source: string; fileName: string; status: string; rowCount: number; createdAt: string; completedAt: string | null }>;
  total: number;
  truncated: boolean;
};

export type CustomerServiceConsumerResponseMap = {
  search: CustomerServiceSearchResponse;
  import_batch_search: CustomerServiceImportSearchResponse;
};

type CustomerServiceConsumerRequestFor<K extends keyof CustomerServiceConsumerResponseMap> =
  Extract<CustomerServiceConsumerRequest, { operation: K }>;

function unavailable(): PublicApiError {
  return new PublicApiError(503, "service_unavailable", "Django 客服读取服务暂时不可用，请稍后重试。");
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validateWindow(value: unknown, limit: number) {
  return record(value) && Array.isArray(value.items) && Number.isSafeInteger(value.total)
    && Number(value.total) >= 0 && typeof value.truncated === "boolean" && value.items.length <= limit;
}

function validateResponse<K extends keyof CustomerServiceConsumerResponseMap>(operation: K, value: unknown, limit: number): value is CustomerServiceConsumerResponseMap[K] {
  if (!validateWindow(value, limit)) return false;
  const items = (value as { items: unknown[] }).items;
  if (operation === "search") return items.every((item) => record(item)
    && typeof item.resultId === "string" && typeof item.title === "string"
    && typeof item.subtitle === "string" && typeof item.detail === "string"
    && typeof item.updatedAt === "string" && item.amountCents === null);
  return items.every((item) => record(item) && typeof item.id === "string"
    && typeof item.source === "string" && typeof item.fileName === "string"
    && typeof item.status === "string" && Number.isSafeInteger(item.rowCount)
    && typeof item.createdAt === "string" && (item.completedAt === null || typeof item.completedAt === "string"));
}

export async function readDjangoCustomerServiceConsumer<K extends keyof CustomerServiceConsumerResponseMap>(
  principal: AppPrincipal,
  request: CustomerServiceConsumerRequestFor<K>,
  options: Omit<DjangoCustomerServiceOptions, "config"> & { config?: DjangoCustomerServiceConfig } = {},
): Promise<{ revision: string; data: CustomerServiceConsumerResponseMap[K] }> {
  if (!record(request) || typeof request.query !== "string" || !Number.isSafeInteger(request.offset)
    || !Number.isSafeInteger(request.limit) || request.offset < 0 || request.offset > 80_000
    || request.limit < 1 || request.limit > 100) throw unavailable();
  const { config, ...serviceOptions } = options;
  const result = await createDjangoCustomerService(config).requestJson<Record<string, unknown>>(principal, {
    method: "POST", path: CUSTOMER_SERVICE_CONSUMER_PATH, service: "reader", payload: request,
  }, serviceOptions);
  if (result.data.operation !== request.operation || !validateResponse(request.operation, result.data.data, request.limit)) throw unavailable();
  return { revision: result.revision, data: result.data.data };
}

export type CustomerServiceConsumerReader = {
  read<K extends keyof CustomerServiceConsumerResponseMap>(principal: AppPrincipal, request: CustomerServiceConsumerRequestFor<K>, options?: { signal?: AbortSignal }): Promise<{ revision: string; data: CustomerServiceConsumerResponseMap[K] }>;
};

export function createDjangoCustomerServiceConsumerReader(config?: DjangoCustomerServiceConfig): CustomerServiceConsumerReader {
  return { read: (principal, request, options = {}) => readDjangoCustomerServiceConsumer(principal, request, { ...options, config }) };
}
