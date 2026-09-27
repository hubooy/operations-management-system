import type { AppPrincipal } from "@/lib/auth/authorization";
import { createDjangoMarketService, MARKET_CONSUMER_QUERY_PATH, type DjangoMarketServiceOptions } from "./market-service";
import { PublicApiError } from "@/lib/http/api-error";

export type MarketSearchItem = {
  id: string; title: string; subtitle: string; detail: string;
  updatedAt: string; amountCents: number | null;
};
type Page<T> = { items: T[]; total: number; truncated: boolean };
export type MarketConsumerResponseMap = {
  sku_search: Page<MarketSearchItem>;
  annotation_search: Page<MarketSearchItem>;
  import_batch_search: Page<{
    id: string; sourceType: string; fileName: string; status: string; rowCount: number;
    periodStart: string; periodEnd: string; createdAt: string; completedAt: string | null;
  }>;
};
export type MarketConsumerRequest = {
  operation: keyof MarketConsumerResponseMap; query: string; offset: number; limit: number;
};
export type MarketConsumerReader = {
  read<R extends MarketConsumerRequest>(principal: AppPrincipal, request: R, options?: { signal?: AbortSignal }):
    Promise<{ revision: string; data: MarketConsumerResponseMap[R["operation"]] }>;
};
export function createDjangoMarketConsumerReader(options: DjangoMarketServiceOptions = {}): MarketConsumerReader {
  return {
    async read<R extends MarketConsumerRequest>(principal: AppPrincipal, request: R, execution: { signal?: AbortSignal } = {}) {
      if (!["sku_search", "annotation_search", "import_batch_search"].includes(request.operation)
        || Object.keys(request).sort().join(",") !== "limit,offset,operation,query"
        || typeof request.query !== "string" || request.query.length > 120
        || !Number.isSafeInteger(request.offset) || request.offset < 0 || request.offset > 100_000
        || !Number.isSafeInteger(request.limit) || request.limit < 1 || request.limit > 100) {
        throw new PublicApiError(400, "invalid_request", "市场搜索参数无效");
      }
      if (principal.scope !== null || (request.operation === "import_batch_search"
        && !["operator", "admin"].includes(principal.role))) {
        throw new PublicApiError(403, "access_denied", "当前账号无权读取市场搜索数据");
      }
      const result = await createDjangoMarketService(options.config).request<MarketConsumerResponseMap[R["operation"]]>(principal, {
        path: MARKET_CONSUMER_QUERY_PATH, service: "reader", payload: request,
      }, { ...options, ...execution });
      if (!result.revision || !Array.isArray(result.data.items)
        || !Number.isSafeInteger(result.data.total) || result.data.total < 0
        || typeof result.data.truncated !== "boolean"
        || result.data.items.length > request.limit) {
        throw new PublicApiError(503, "service_unavailable", "市场搜索响应无效");
      }
      return { revision: result.revision, data: result.data };
    },
  };
}
