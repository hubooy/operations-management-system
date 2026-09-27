"use client";

import { SearchableMultiSelect, SearchableSelect, type SearchableSelectOption } from "./ui/searchable-select";
import type { InventoryAgeStatus, InventoryHealthStatus, ReplenishmentPlanItem } from "./module-view-shared";

export type InventoryFilterTab = "overview" | "age" | "plan" | "stale" | "inbound" | "guangdong";
export type InventoryWarehouseType = "owned" | "jd_rdc" | "other";
export type InventoryPlanStatus = "" | ReplenishmentPlanItem["status"];

export type InventorySharedFilters = {
  productQuery: string;
  warehouses: string[];
  brands: string[];
  categories: string[];
  warehouseTypes: InventoryWarehouseType[];
  healthStatuses: InventoryHealthStatus[];
  ageStatuses: InventoryAgeStatus[];
  ageBuckets: string[];
  suppliers: string[];
  planStatus: InventoryPlanStatus;
};

export type InventorySharedFilterOptions = {
  warehouses: string[];
  brands: string[];
  categories: string[];
  suppliers: string[];
  ageBuckets: Array<{ value: string; label: string }>;
};

const inventoryWarehouseTypes = ["owned", "jd_rdc", "other"] as const;
const inventoryHealthStatuses = ["no_stock", "urgent", "warning", "stale", "slow", "healthy"] as const;
const inventoryAgeStatuses = ["healthy", "aged", "slow", "stagnant", "no_stock"] as const;
const inventoryPlanStatuses = ["draft", "confirmed", "completed", "cancelled"] as const;
const defaultAgeBucketOptions = [
  { value: "0-7", label: "0–7 天" },
  { value: "8-15", label: "8–15 天" },
  { value: "16-30", label: "16–30 天" },
  { value: "31-60", label: "31–60 天" },
  { value: "61-90", label: "61–90 天" },
  { value: "91-120", label: "91–120 天" },
  { value: "121-150", label: "121–150 天" },
  { value: "151-180", label: "151–180 天" },
  { value: "181-360", label: "181–360 天" },
  { value: "361+", label: "1 年以上" },
] as const;
const inventoryAgeBucketKeys = defaultAgeBucketOptions.map((option) => option.value);
const cleanupAgeStatuses = new Set<InventoryAgeStatus>(["aged", "slow", "stagnant"]);

const warehouseTypeOptions: SearchableSelectOption[] = [
  { value: "owned", label: "自有仓" },
  { value: "jd_rdc", label: "京东 RDC / DC" },
  { value: "other", label: "其他仓" },
];
const healthStatusOptions: SearchableSelectOption[] = [
  { value: "no_stock", label: "无库存可用" },
  { value: "urgent", label: "紧急补货" },
  { value: "warning", label: "补货预警" },
  { value: "stale", label: "积压风险" },
  { value: "slow", label: "低周转" },
  { value: "healthy", label: "库存健康" },
];
const ageStatusOptions: SearchableSelectOption[] = [
  { value: "healthy", label: "库龄健康" },
  { value: "aged", label: "高库龄" },
  { value: "slow", label: "低动销" },
  { value: "stagnant", label: "滞销清理" },
  { value: "no_stock", label: "无可用库存" },
];
const planStatusOptions: SearchableSelectOption[] = [
  { value: "", label: "当前有效计划" },
  { value: "draft", label: "待确认" },
  { value: "confirmed", label: "已确认" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已取消" },
];

const filterUrlKeys = [
  "inventoryQuery",
  "inventoryWarehouse",
  "inventoryBrand",
  "inventoryCategory",
  "inventoryWarehouseType",
  "inventoryHealthStatus",
  "inventoryAgeStatus",
  "inventoryAgeBucket",
  "inventorySupplier",
  "inventoryPlanStatus",
] as const;

export const emptyInventorySharedFilters = (): InventorySharedFilters => ({
  productQuery: "",
  warehouses: [],
  brands: [],
  categories: [],
  warehouseTypes: [],
  healthStatuses: [],
  ageStatuses: [],
  ageBuckets: [],
  suppliers: [],
  planStatus: "",
});

function boundedSelections<T extends string>(
  params: URLSearchParams,
  key: string,
  maximum: number,
  allowed?: readonly T[],
) {
  const values = [...new Set(params.getAll(key).map((value) => value.trim()).filter(Boolean))]
    .filter((value) => value.length <= 120)
    .slice(0, maximum);
  return (allowed ? values.filter((value): value is T => allowed.includes(value as T)) : values as T[]);
}

export function readInventorySharedFilters(input?: string | URL): InventorySharedFilters {
  const params = typeof window === "undefined" && !input
    ? new URLSearchParams()
    : new URL(input ?? window.location.href, "https://teruisi-inventory.invalid").searchParams;
  const planStatus = params.get("inventoryPlanStatus")?.trim() ?? "";
  return {
    productQuery: (params.get("inventoryQuery") ?? "").slice(0, 100),
    warehouses: boundedSelections(params, "inventoryWarehouse", 10),
    brands: boundedSelections(params, "inventoryBrand", 20),
    categories: boundedSelections(params, "inventoryCategory", 20),
    warehouseTypes: boundedSelections(params, "inventoryWarehouseType", inventoryWarehouseTypes.length, inventoryWarehouseTypes),
    healthStatuses: boundedSelections(params, "inventoryHealthStatus", inventoryHealthStatuses.length, inventoryHealthStatuses),
    ageStatuses: boundedSelections(params, "inventoryAgeStatus", inventoryAgeStatuses.length, inventoryAgeStatuses),
    ageBuckets: boundedSelections(params, "inventoryAgeBucket", inventoryAgeBucketKeys.length, inventoryAgeBucketKeys),
    suppliers: boundedSelections(params, "inventorySupplier", 20),
    planStatus: inventoryPlanStatuses.includes(planStatus as ReplenishmentPlanItem["status"])
      ? planStatus as ReplenishmentPlanItem["status"]
      : "",
  };
}

export function writeInventorySharedFilters(filters: InventorySharedFilters, mode: "push" | "replace" = "replace") {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  for (const key of filterUrlKeys) url.searchParams.delete(key);
  if (filters.productQuery.trim()) url.searchParams.set("inventoryQuery", filters.productQuery.trim().slice(0, 100));
  filters.warehouses.forEach((value) => url.searchParams.append("inventoryWarehouse", value));
  filters.brands.forEach((value) => url.searchParams.append("inventoryBrand", value));
  filters.categories.forEach((value) => url.searchParams.append("inventoryCategory", value));
  filters.warehouseTypes.forEach((value) => url.searchParams.append("inventoryWarehouseType", value));
  filters.healthStatuses.forEach((value) => url.searchParams.append("inventoryHealthStatus", value));
  filters.ageStatuses.forEach((value) => url.searchParams.append("inventoryAgeStatus", value));
  filters.ageBuckets.forEach((value) => url.searchParams.append("inventoryAgeBucket", value));
  filters.suppliers.forEach((value) => url.searchParams.append("inventorySupplier", value));
  if (filters.planStatus) url.searchParams.set("inventoryPlanStatus", filters.planStatus);
  window.history[mode === "push" ? "pushState" : "replaceState"](null, "", `${url.pathname}${url.search}${url.hash}`);
}

function optionsWithSelections(options: string[], selected: string[]) {
  return [...new Set([...selected, ...options])].map((value) => ({ value, label: value }));
}

function countLabel(count: number, label: string, fallback: string) {
  return count > 0 ? `${count} 个${label}` : fallback;
}

export default function InventoryFilterBar({
  activeTab,
  filters,
  options,
  updating,
  onChange,
  extraFilterActive = false,
  onResetExtra,
}: {
  activeTab: InventoryFilterTab;
  filters: InventorySharedFilters;
  options: InventorySharedFilterOptions;
  updating: boolean;
  onChange: (filters: InventorySharedFilters) => void;
  extraFilterActive?: boolean;
  onResetExtra?: () => void;
}) {
  const patch = (next: Partial<InventorySharedFilters>) => onChange({ ...filters, ...next });
  const usesAgeFilters = activeTab === "age" || activeTab === "stale";
  const ageBucketOptions = options.ageBuckets.length > 0 ? options.ageBuckets : defaultAgeBucketOptions;
  const applicableAgeStatuses = activeTab === "stale"
    ? filters.ageStatuses.filter((status) => cleanupAgeStatuses.has(status))
    : filters.ageStatuses;
  const applicableAgeStatusOptions = activeTab === "stale"
    ? ageStatusOptions.filter((option) => cleanupAgeStatuses.has(option.value as InventoryAgeStatus))
    : ageStatusOptions;
  const changeApplicableAgeStatuses = (statuses: string[]) => {
    if (activeTab !== "stale") {
      patch({ ageStatuses: statuses as InventoryAgeStatus[] });
      return;
    }
    patch({
      ageStatuses: [
        ...filters.ageStatuses.filter((status) => !cleanupAgeStatuses.has(status)),
        ...(statuses as InventoryAgeStatus[]),
      ],
    });
  };
  const hasCommonFilter = Boolean(filters.productQuery.trim())
    || (activeTab !== "guangdong" && filters.warehouses.length > 0)
    || filters.brands.length > 0
    || filters.categories.length > 0;
  const hasApplicableFilter = extraFilterActive || hasCommonFilter
    || (activeTab === "overview" && (filters.warehouseTypes.length > 0 || filters.healthStatuses.length > 0))
    || (usesAgeFilters && (filters.ageBuckets.length > 0 || applicableAgeStatuses.length > 0))
    || ((activeTab === "inbound" || activeTab === "guangdong") && filters.suppliers.length > 0)
    || (activeTab === "plan" && Boolean(filters.planStatus));
  const resetApplicable = () => patch({
    productQuery: "",
    ...(activeTab === "guangdong" ? {} : { warehouses: [] }),
    brands: [],
    categories: [],
    ...(activeTab === "overview" ? { warehouseTypes: [], healthStatuses: [] } : {}),
    ...(usesAgeFilters ? {
      ageBuckets: [],
      ageStatuses: activeTab === "stale"
        ? filters.ageStatuses.filter((status) => !cleanupAgeStatuses.has(status))
        : [],
    } : {}),
    ...(activeTab === "inbound" || activeTab === "guangdong" ? { suppliers: [] } : {}),
    ...(activeTab === "plan" ? { planStatus: "" as const } : {}),
  });
  const scopeLabel = activeTab === "overview"
    ? "库存总览"
    : activeTab === "age"
      ? "库龄分析"
      : activeTab === "plan"
        ? "备货计划"
        : activeTab === "stale"
          ? "滞销清理"
          : activeTab === "guangdong" ? "广东入仓监控" : "京东入仓监控";

  return <section className="panel inventory-shared-filter-panel" aria-label={`${scopeLabel}公共筛选`} aria-busy={updating}>
    <div className="inventory-shared-filter-heading">
      <div><span className="eyebrow">SHARED INVENTORY SCOPE</span><h2>库存管理公共筛选</h2><p>{activeTab === "guangdong" ? "广东监控固定使用广东仓；货品、品牌和品类沿用公共筛选，风险条件独立保存。" : "货品、仓库、品牌和品类会写入当前链接，并同步应用到所有库存 Tab；各页专属条件会独立保留。"}</p></div>
      <div className="inventory-shared-filter-controls">
        <label className="inventory-shared-product-query"><span>货品编码或名称</span><input value={filters.productQuery} maxLength={100} onChange={(event) => patch({ productQuery: event.target.value.slice(0, 100) })} placeholder="支持空格、逗号或换行分隔" aria-label="库存公共货品搜索" /></label>
        {activeTab === "guangdong" ? <label><span>仓库</span><input value="广东仓" readOnly aria-label="广东监控固定仓库" /></label> : <label><span>仓库</span><SearchableMultiSelect values={filters.warehouses} onChange={(warehouses) => patch({ warehouses })} ariaLabel="库存公共仓库" allLabel="全部仓库" searchPlaceholder="搜索仓库" options={optionsWithSelections(options.warehouses, filters.warehouses)} /></label>}
        <label><span>品牌</span><SearchableMultiSelect values={filters.brands} onChange={(brands) => patch({ brands })} ariaLabel="库存公共品牌" allLabel="全部品牌" searchPlaceholder="搜索品牌" options={optionsWithSelections(options.brands, filters.brands)} /></label>
        <label><span>品类</span><SearchableMultiSelect values={filters.categories} onChange={(categories) => patch({ categories })} ariaLabel="库存公共品类" allLabel="全部品类" searchPlaceholder="搜索品类" options={optionsWithSelections(options.categories, filters.categories)} /></label>
        {activeTab === "overview" && <>
          <label><span>库存类型</span><SearchableMultiSelect values={filters.warehouseTypes} onChange={(warehouseTypes) => patch({ warehouseTypes: warehouseTypes as InventoryWarehouseType[] })} ariaLabel="库存类型" allLabel="全部类型" searchPlaceholder="搜索库存类型" options={warehouseTypeOptions} /></label>
          <label><span>健康状态</span><SearchableMultiSelect values={filters.healthStatuses} onChange={(healthStatuses) => patch({ healthStatuses: healthStatuses as InventoryHealthStatus[] })} ariaLabel="健康状态" allLabel="全部状态" searchPlaceholder="搜索健康状态" options={healthStatusOptions} /></label>
        </>}
        {usesAgeFilters && <label><span>风险状态</span><SearchableMultiSelect values={applicableAgeStatuses} onChange={changeApplicableAgeStatuses} ariaLabel="库龄风险状态" allLabel={activeTab === "stale" ? "全部待清理状态" : "全部状态"} searchPlaceholder="搜索风险状态" options={applicableAgeStatusOptions} /></label>}
        {activeTab === "inbound" && <label><span>供应商</span><SearchableMultiSelect values={filters.suppliers} onChange={(suppliers) => patch({ suppliers })} ariaLabel="京东入仓供应商" allLabel="全部供应商" searchPlaceholder="搜索供应商" options={optionsWithSelections(options.suppliers, filters.suppliers)} /></label>}
        {activeTab === "guangdong" && <label><span>供应商</span><SearchableMultiSelect values={filters.suppliers} onChange={(suppliers) => patch({ suppliers })} ariaLabel="广东入仓供应商" allLabel="全部供应商" searchPlaceholder="搜索供应商" options={optionsWithSelections(options.suppliers, filters.suppliers)} /></label>}
        {activeTab === "plan" && <label><span>计划状态</span><SearchableSelect value={filters.planStatus} onChange={(planStatus) => patch({ planStatus: planStatus as InventoryPlanStatus })} ariaLabel="备货计划状态" searchPlaceholder="搜索计划状态" options={planStatusOptions} /></label>}
        {hasApplicableFilter && <button type="button" className="secondary-button inventory-shared-filter-reset" onClick={() => { resetApplicable(); onResetExtra?.(); }}>清空当前页筛选</button>}
      </div>
    </div>
    {usesAgeFilters && <div className="inventory-shared-age-buckets" role="group" aria-label="库龄区间多选">
      {ageBucketOptions.map((bucket) => {
        const selected = filters.ageBuckets.includes(bucket.value);
        return <button type="button" key={bucket.value} className={selected ? "selected" : ""} aria-pressed={selected} onClick={() => patch({ ageBuckets: selected ? filters.ageBuckets.filter((value) => value !== bucket.value) : [...filters.ageBuckets, bucket.value] })}><i>{selected ? "✓" : ""}</i><span>{bucket.label}</span></button>;
      })}
    </div>}
    <small role={updating ? "status" : undefined} aria-live="polite">{updating
      ? `正在按公共筛选更新${scopeLabel}…`
      : `当前范围：${filters.productQuery.trim() ? "已筛选货品" : "全部货品"}、${activeTab === "guangdong" ? "广东仓" : countLabel(filters.warehouses.length, "仓库", "全部仓库")}、${countLabel(filters.brands.length, "品牌", "全部品牌")}、${countLabel(filters.categories.length, "品类", "全部品类")}。`}</small>
  </section>;
}
