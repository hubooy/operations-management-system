import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoFinanceService,
  FINANCE_CONSUMER_QUERY_PATH,
  type DjangoFinanceServiceConfig,
  type DjangoFinanceServiceOptions,
} from "@/lib/django/finance-service";
import { PublicApiError } from "@/lib/http/api-error";

export const financeConsumerOperations = [
  "line_search",
  "target_search",
  "import_batch_search",
] as const;

export type FinanceConsumerOperation = (typeof financeConsumerOperations)[number];

type FinanceSearchItem = {
  id: string;
  title: string;
  subtitle: string;
  detail: string;
  updatedAt: string;
  amountCents: number | null;
};

export type FinanceConsumerRequestMap = {
  line_search: {
    operation: "line_search";
    query: string;
    offset: number;
    limit: number;
  };
  target_search: {
    operation: "target_search";
    query: string;
    offset: number;
    limit: number;
  };
  import_batch_search: {
    operation: "import_batch_search";
    query: string;
    offset: number;
    limit: number;
  };
};

export type FinanceConsumerResponseMap = {
  line_search: { items: FinanceSearchItem[]; total: number; truncated: boolean };
  target_search: { items: FinanceSearchItem[]; total: number; truncated: boolean };
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

export type FinanceConsumerRequest = FinanceConsumerRequestMap[FinanceConsumerOperation];

export type FinanceConsumerReaderResult<R extends FinanceConsumerRequest> = {
  revision: string;
  data: FinanceConsumerResponseMap[R["operation"]];
};

export type FinanceConsumerReaderOptions = Omit<DjangoFinanceServiceOptions, "config">;

function unavailable(): PublicApiError {
  return new PublicApiError(503, "service_unavailable", "Django 财务读取服务暂时不可用，请稍后重试。");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function assertRequest(request: FinanceConsumerRequest): void {
  if (!isRecord(request)
    || !financeConsumerOperations.some((operation) => operation === request.operation)
    || Object.keys(request).length !== 4
    || !Object.hasOwn(request, "query") || !Object.hasOwn(request, "offset") || !Object.hasOwn(request, "limit")
    || typeof request.query !== "string" || Array.from(request.query.trim()).length < 2
    || Array.from(request.query.trim()).length > 80
    || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 80_000
    || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) {
    throw unavailable();
  }
}

export async function readDjangoFinanceConsumer<R extends FinanceConsumerRequest>(
  principal: AppPrincipal,
  request: R,
  options: FinanceConsumerReaderOptions & { config?: DjangoFinanceServiceConfig } = {},
): Promise<FinanceConsumerReaderResult<R>> {
  assertRequest(request);
  const { config, ...serviceOptions } = options;
  const result = await createDjangoFinanceService(config).request<Record<string, unknown>>(
    principal,
    {
      method: "POST",
      path: FINANCE_CONSUMER_QUERY_PATH,
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
    data: result.data.data as FinanceConsumerResponseMap[R["operation"]],
  };
}

export type FinanceConsumerReader = {
  read<R extends FinanceConsumerRequest>(
    principal: AppPrincipal,
    request: R,
    options?: FinanceConsumerReaderOptions,
  ): Promise<FinanceConsumerReaderResult<R>>;
};

export function createDjangoFinanceConsumerReader(
  config?: DjangoFinanceServiceConfig,
): FinanceConsumerReader {
  return {
    read: (principal, request, options = {}) => readDjangoFinanceConsumer(
      principal,
      request,
      { ...options, config },
    ),
  };
}
