import { PublicApiError } from "@/lib/http/api-error";

export const marketMasterViews = [
  "workspace",
  "system_kpis",
  "settings_status",
  "database_primary",
  "database_filters",
  "database_secondary",
  "master",
  "pending_prices",
  "compare",
  "brand_job",
  "brand_seeds",
  "subcategories",
] as const;

export type MarketMasterView = (typeof marketMasterViews)[number];

export function parseMarketMasterView(params: URLSearchParams): MarketMasterView {
  const requestedViews = params.getAll("view");
  if (requestedViews.length > 1) {
    throw new PublicApiError(400, "invalid_request", "view 参数不能重复");
  }
  const view = requestedViews[0] ?? "workspace";
  if (!marketMasterViews.some((candidate) => candidate === view)) {
    throw new PublicApiError(400, "invalid_request", "不支持的市场主数据 view");
  }
  return view as MarketMasterView;
}
