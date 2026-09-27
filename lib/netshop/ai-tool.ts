import type { AppPrincipal } from "@/lib/auth/authorization";
import { netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import {
  createDjangoNetshopService,
  NETSHOP_PRODUCT_PERFORMANCE_PATH,
  NETSHOP_PROMOTION_PERFORMANCE_PATH,
} from "@/lib/django/netshop-service";
import {
  NetshopQueryError,
  netshopOutletKey,
  resolveNetshopQueryPeriod,
} from "@/lib/netshop/query-contract";

type NetshopAiArgs = {
  dataset?: "product_daily" | "promotion";
  startDate?: string;
  endDate?: string;
  platform?: string;
  shop?: string;
  query?: string;
  limit?: number;
};

type NetshopAiPerformancePayload = {
  monetaryUnit: unknown;
  requestedPeriod: unknown;
  dataCutoffDate: unknown;
  coverage: unknown;
  summary: unknown;
  items: unknown[];
  pagination: { truncated: boolean };
  visitorAggregation?: unknown;
};

export async function getNetshopPerformanceForAi(rawArgs: unknown, principal: AppPrincipal) {
  const args = (rawArgs && typeof rawArgs === "object" ? rawArgs : {}) as NetshopAiArgs;
  const platformNames = netshopPlatformsForPrincipal(principal, args.platform ? [args.platform] : []);
  const shopName = typeof args.shop === "string" ? args.shop.trim() : "";
  if (shopName && (typeof args.platform !== "string" || !args.platform.trim())) {
    throw new NetshopQueryError("invalid_outlet_filter", "按店铺查询时必须同时提供平台");
  }
  resolveNetshopQueryPeriod(args.startDate, args.endDate);
  const limit = Math.max(1, Math.min(20, Math.trunc(Number(args.limit ?? 10)) || 10));
  const query = new URLSearchParams({ page: "1", pageSize: String(limit) });
  for (const platform of platformNames) query.append("platform", platform);
  if (shopName) query.append("outlet", netshopOutletKey(args.platform!.trim(), shopName));
  if (args.startDate) query.set("startDate", args.startDate);
  if (args.endDate) query.set("endDate", args.endDate);
  if (args.query) query.set("q", args.query);
  if (args.dataset === "promotion") {
    const { data: result } = await createDjangoNetshopService().request<NetshopAiPerformancePayload>(
      principal,
      { method: "GET", path: NETSHOP_PROMOTION_PERFORMANCE_PATH, query, service: "reader" },
    );
    return {
      dataset: "promotion",
      monetaryUnit: result.monetaryUnit,
      requestedPeriod: result.requestedPeriod,
      dataCutoffDate: result.dataCutoffDate,
      coverage: result.coverage,
      summary: result.summary,
      returned: result.items.length,
      truncated: result.pagination.truncated,
      items: result.items,
    };
  }
  query.set("dimension", "spu");
  query.set("view", "full");
  const { data: result } = await createDjangoNetshopService().request<NetshopAiPerformancePayload>(
    principal,
    { method: "GET", path: NETSHOP_PRODUCT_PERFORMANCE_PATH, query, service: "reader" },
  );
  return {
    dataset: "product_daily",
    monetaryUnit: result.monetaryUnit,
    requestedPeriod: result.requestedPeriod,
    dataCutoffDate: result.dataCutoffDate,
    visitorAggregation: result.visitorAggregation,
    coverage: result.coverage,
    summary: result.summary,
    returned: result.items.length,
    truncated: result.pagination.truncated,
    items: result.items,
  };
}
