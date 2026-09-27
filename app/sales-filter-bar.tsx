"use client";

import { SearchableMultiSelect } from "./ui/searchable-select";
import { parseProductQueriesStrict, SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH } from "@/lib/sales/read-contract";

export type SalesSharedFilters = {
  platforms: string[];
  outletKeys: string[];
  categories: string[];
  channels: string[];
  productQuery: string;
};

export type SalesSharedFilterOptions = {
  platforms: string[];
  shops: Array<{ key: string; name: string; platform: string }>;
  categories: string[];
  channels?: string[];
};

export const MAX_SALES_SHARED_FILTER_SELECTIONS = 50;

export type FinanceDimensionFilterOptions = {
  platforms: string[];
  shops: Array<{ key: string; name: string; platform: string }>;
};

export type FinanceDimensionFilterIssues = {
  invalidPlatforms: string[];
  invalidShops: Array<{ name: string; platform: string }>;
  incompatibleShops: Array<{ name: string; platform: string }>;
};

export type FinanceDimensionFilterReconciliation = {
  platforms: string[];
  outletKeys: string[];
  removedPlatforms: string[];
  removedShops: string[];
  changed: boolean;
  canReconcile: boolean;
};

export type FinanceDimensionReconciliationDecision = "reject" | "auto_apply" | "require_confirmation";

export type ScopedFinanceAnalysisResult<T> = {
  requestSignature: string;
  payload: T;
};

export function parseFinanceDimensionFilterIssues(value: unknown): FinanceDimensionFilterIssues | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const invalidPlatforms = record.invalidPlatforms;
  const invalidShops = record.invalidShops;
  const incompatibleShops = record.incompatibleShops;
  const validPlatforms = Array.isArray(invalidPlatforms)
    && invalidPlatforms.length <= MAX_SALES_SHARED_FILTER_SELECTIONS
    && invalidPlatforms.every((platform) => typeof platform === "string" && platform.length > 0 && platform.length <= 100);
  const validShopList = (items: unknown): items is Array<{ name: string; platform: string }> => Array.isArray(items)
    && items.length <= MAX_SALES_SHARED_FILTER_SELECTIONS
    && items.every((item) => {
      if (!item || typeof item !== "object") return false;
      const shop = item as Record<string, unknown>;
      return typeof shop.name === "string" && shop.name.length > 0 && shop.name.length <= 100
        && typeof shop.platform === "string" && shop.platform.length > 0 && shop.platform.length <= 100;
    });
  if (!validPlatforms || !validShopList(invalidShops) || !validShopList(incompatibleShops)) return null;
  return { invalidPlatforms, invalidShops, incompatibleShops };
}

export type SalesFilterCapabilities = {
  categories?: boolean;
  channels?: boolean;
  product?: boolean;
};

export const emptySalesSharedFilters = (): SalesSharedFilters => ({
  platforms: [],
  outletKeys: [],
  categories: [],
  channels: [],
  productQuery: "",
});

const filterUrlKeys = [
  "salesPlatform",
  "salesOutlet",
  "salesCategory",
  "salesChannel",
  "salesProductQuery",
] as const;

export function salesOutletIdentityKey(platform: string, name: string) {
  return `${platform}\u001f${name}`;
}

export function salesOutletKeyToFinanceKey(value: string) {
  const [platform, name, ...rest] = value.split("\u001f");
  return platform && name && rest.length === 0 ? JSON.stringify([platform, name]) : null;
}

export function financeDimensionOptionsToSalesOptions(options: FinanceDimensionFilterOptions): SalesSharedFilterOptions {
  return {
    platforms: options.platforms,
    shops: options.shops.map((shop) => ({
      key: salesOutletIdentityKey(shop.platform, shop.name),
      name: shop.name,
      platform: shop.platform,
    })),
    categories: [],
  };
}

export function reconcileFinanceDimensionFilters(
  selectedPlatforms: readonly string[],
  selectedOutletKeys: readonly string[],
  issues: FinanceDimensionFilterIssues,
): FinanceDimensionFilterReconciliation {
  const selectedPlatformSet = new Set(selectedPlatforms);
  const selectedOutletKeySet = new Set(selectedOutletKeys);
  const invalidPlatformSet = new Set(issues.invalidPlatforms);
  const invalidShopOptions = [...issues.invalidShops, ...issues.incompatibleShops];
  const invalidShopKeySet = new Set(invalidShopOptions.map((shop) => salesOutletIdentityKey(shop.platform, shop.name)));
  const issuesBelongToRequest = issues.invalidPlatforms.every((platform) => selectedPlatformSet.has(platform))
    && [...invalidShopKeySet].every((shopKey) => selectedOutletKeySet.has(shopKey));
  const platforms = selectedPlatforms.filter((platform) => !invalidPlatformSet.has(platform));
  const outletKeys = selectedOutletKeys.filter((outletKey) => !invalidShopKeySet.has(outletKey));
  const removedPlatforms = selectedPlatforms.filter((platform) => invalidPlatformSet.has(platform));
  const removedShops = selectedOutletKeys
    .filter((outletKey) => invalidShopKeySet.has(outletKey))
    .map((outletKey) => {
      const shop = invalidShopOptions.find((item) => salesOutletIdentityKey(item.platform, item.name) === outletKey);
      return shop ? `${shop.platform} · ${shop.name}` : outletKey;
    });
  return {
    platforms,
    outletKeys,
    removedPlatforms,
    removedShops,
    changed: removedPlatforms.length > 0 || removedShops.length > 0,
    canReconcile: issuesBelongToRequest,
  };
}

export function decideFinanceDimensionReconciliation(
  reconciliation: FinanceDimensionFilterReconciliation,
): FinanceDimensionReconciliationDecision {
  if (!reconciliation.changed || !reconciliation.canReconcile) return "reject";
  return reconciliation.platforms.length > 0 || reconciliation.outletKeys.length > 0
    ? "auto_apply"
    : "require_confirmation";
}

export function financeAnalysisPayloadForRequest<T>(
  result: ScopedFinanceAnalysisResult<T> | null,
  requestSignature: string,
) {
  return result?.requestSignature === requestSignature ? result.payload : null;
}

function boundedSelections(params: URLSearchParams, key: string) {
  return [...new Set(params.getAll(key).map((value) => value.trim()).filter(Boolean))]
    .slice(0, MAX_SALES_SHARED_FILTER_SELECTIONS);
}

export function readSalesSharedFilters(input?: string | URL): SalesSharedFilters {
  const params = typeof window === "undefined" && !input
    ? new URLSearchParams()
    : new URL(input ?? window.location.href, "https://teruisi-sales.invalid").searchParams;
  return {
    platforms: boundedSelections(params, "salesPlatform"),
    outletKeys: boundedSelections(params, "salesOutlet"),
    categories: boundedSelections(params, "salesCategory"),
    channels: boundedSelections(params, "salesChannel"),
    productQuery: (params.get("salesProductQuery") ?? "").slice(0, SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH),
  };
}

export function writeSalesSharedFilters(filters: SalesSharedFilters, mode: "push" | "replace" = "replace") {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  for (const key of filterUrlKeys) url.searchParams.delete(key);
  filters.platforms.forEach((value) => url.searchParams.append("salesPlatform", value));
  filters.outletKeys.forEach((value) => url.searchParams.append("salesOutlet", value));
  filters.categories.forEach((value) => url.searchParams.append("salesCategory", value));
  filters.channels.forEach((value) => url.searchParams.append("salesChannel", value));
  if (filters.productQuery.trim()) url.searchParams.set("salesProductQuery", filters.productQuery.trim());
  const next = `${url.pathname}${url.search}${url.hash}`;
  window.history[mode === "push" ? "pushState" : "replaceState"](null, "", next);
}

function countLabel(count: number, label: string, fallback: string) {
  return count ? `${count} 个${label}` : fallback;
}

export default function SalesFilterBar({
  filters,
  options,
  capabilities = { categories: true, channels: false, product: true },
  updating = false,
  scopeLabel = "销售分析",
  maxSelectionsPerDimension = MAX_SALES_SHARED_FILTER_SELECTIONS,
  onChange,
}: {
  filters: SalesSharedFilters;
  options: SalesSharedFilterOptions;
  capabilities?: SalesFilterCapabilities;
  updating?: boolean;
  scopeLabel?: string;
  maxSelectionsPerDimension?: number;
  onChange: (filters: SalesSharedFilters) => void;
}) {
  const visibleShops = filters.platforms.length
    ? options.shops.filter((shop) => filters.platforms.includes(shop.platform))
    : options.shops;
  let productQueryError = "";
  try { parseProductQueriesStrict(filters.productQuery); }
  catch (error) { productQueryError = error instanceof Error ? error.message : "货品筛选无效"; }
  const hasApplicableFilter = filters.platforms.length > 0
    || filters.outletKeys.length > 0
    || (capabilities.categories !== false && filters.categories.length > 0)
    || (capabilities.channels && filters.channels.length > 0)
    || (capabilities.product !== false && Boolean(filters.productQuery.trim()));
  const patch = (next: Partial<SalesSharedFilters>) => onChange({ ...filters, ...next });
  const updatePlatforms = (platforms: string[]) => {
    const allowedShopKeys = new Set(options.shops
      .filter((shop) => platforms.length === 0 || platforms.includes(shop.platform))
      .map((shop) => shop.key));
    patch({
      platforms,
      outletKeys: filters.outletKeys.filter((shopKey) => allowedShopKeys.has(shopKey)),
    });
  };
  const resetApplicable = () => patch({
    platforms: [],
    outletKeys: [],
    ...(capabilities.categories !== false ? { categories: [] } : {}),
    ...(capabilities.channels ? { channels: [] } : {}),
    ...(capabilities.product !== false ? { productQuery: "" } : {}),
  });

  return <section className="panel sales-overview-filter-panel sales-shared-filter-panel" aria-label={`${scopeLabel}公共筛选`} aria-busy={updating}>
    <div className="sales-overview-filter-heading">
      <div><span className="eyebrow">SHARED SALES SCOPE</span><h2>销售分析公共筛选</h2><p>筛选会保留在当前链接中；切换页签后，目标页支持的维度会自动继承。</p></div>
      <div className="sales-overview-filter-controls">
        <label><span>平台</span><SearchableMultiSelect values={filters.platforms} onChange={updatePlatforms} ariaLabel="销售分析平台" allLabel="全部平台" searchPlaceholder="搜索平台" maxSelections={maxSelectionsPerDimension} options={options.platforms.map((platform) => ({ value: platform, label: platform }))} /></label>
        <label><span>店铺</span><SearchableMultiSelect values={filters.outletKeys} onChange={(outletKeys) => patch({ outletKeys })} ariaLabel="销售分析店铺" allLabel="全部店铺" searchPlaceholder="搜索店铺或平台" maxSelections={maxSelectionsPerDimension} options={visibleShops.map((shop) => ({ value: shop.key, label: shop.platform === "未分类" ? shop.name : `${shop.platform} · ${shop.name}`, searchText: `${shop.platform} ${shop.name}` }))} /></label>
        {capabilities.categories !== false && <label><span>品类</span><SearchableMultiSelect values={filters.categories} onChange={(categories) => patch({ categories })} ariaLabel="销售分析品类" allLabel="全部品类" searchPlaceholder="搜索品类" maxSelections={maxSelectionsPerDimension} options={options.categories.map((category) => ({ value: category, label: category }))} /></label>}
        {capabilities.channels && <label><span>渠道</span><SearchableMultiSelect values={filters.channels} onChange={(channels) => patch({ channels })} ariaLabel="销售分析渠道" allLabel="全部渠道" searchPlaceholder="搜索渠道" maxSelections={maxSelectionsPerDimension} options={(options.channels ?? []).map((channel) => ({ value: channel, label: channel }))} /></label>}
        {capabilities.product !== false && <label className="sales-shared-product-query"><span>货品编码或名称</span><textarea rows={2} maxLength={SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH} value={filters.productQuery} onChange={(event) => patch({ productQuery: event.target.value })} placeholder="粘贴多个代码，用逗号或换行分隔（最多1000字符、100项）" aria-label="销售分析货品编码或名称" aria-invalid={Boolean(productQueryError)} />{productQueryError && <small role="alert">{productQueryError}</small>}</label>}
        {hasApplicableFilter && <button type="button" className="secondary-button sales-overview-filter-reset" onClick={resetApplicable}>清空筛选</button>}
      </div>
    </div>
    <small role={updating ? "status" : undefined} aria-live="polite">{updating
      ? `正在按公共筛选更新${scopeLabel}…`
      : `当前范围：${countLabel(filters.platforms.length, "平台", "全部平台")}、${countLabel(filters.outletKeys.length, "店铺", "全部店铺")}${capabilities.categories !== false ? `、${countLabel(filters.categories.length, "品类", "全部品类")}` : ""}${capabilities.channels ? `、${countLabel(filters.channels.length, "渠道", "全部渠道")}` : ""}${capabilities.product !== false && filters.productQuery.trim() ? "、已筛选货品" : ""}。`}</small>
  </section>;
}
