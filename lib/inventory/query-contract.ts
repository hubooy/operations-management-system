export type InventoryPageOptions = { page?: number; pageSize?: number };

export class InventoryQueryContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InventoryQueryContractError";
  }
}

export const inventoryOverviewViews = ["full", "dashboard", "overview", "plan"] as const;
export type InventoryOverviewView = (typeof inventoryOverviewViews)[number];

export function parseInventoryOverviewView(params: URLSearchParams): InventoryOverviewView {
  const requestedViews = params.getAll("view");
  if (requestedViews.length === 0) return "full";
  if (requestedViews.length > 1) throw new InventoryQueryContractError("view 只能提供一次");
  const requestedView = requestedViews[0];
  if (!inventoryOverviewViews.some((view) => view === requestedView)) {
    throw new InventoryQueryContractError("view 必须是 full、dashboard、overview 或 plan");
  }
  return requestedView as InventoryOverviewView;
}

export function normalizeInventorySelections(
  values: readonly string[],
  options: { maximum: number; allowed?: readonly string[]; label: string; maximumLength?: number },
) {
  const normalized = [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  const maximumLength = options.maximumLength ?? 120;
  if (normalized.length > options.maximum || normalized.some((value) => value.length > maximumLength)) {
    throw new InventoryQueryContractError(`${options.label}筛选项数量或长度超出限制`);
  }
  const allowed = options.allowed;
  if (allowed && normalized.some((value) => !allowed.includes(value))) {
    throw new InventoryQueryContractError(`${options.label}筛选项不在允许清单中`);
  }
  return normalized;
}

export function parseInventoryPaginationParameter(value: string | null, field: "page" | "pageSize") {
  if (value === null) return undefined;
  if (!/^[1-9]\d*$/.test(value)) {
    throw new InventoryQueryContractError(`${field} 必须是十进制正整数`);
  }
  const parsed = Number(value);
  const maximum = field === "page" ? 10_000 : 100;
  if (!Number.isSafeInteger(parsed) || parsed > maximum) {
    throw new InventoryQueryContractError(`${field} 必须是 1 到 ${maximum} 的整数`);
  }
  return parsed;
}

export function normalizeInventoryPagination(options: InventoryPageOptions) {
  const page = options.page ?? 1;
  const pageSize = options.pageSize ?? 50;
  if (!Number.isSafeInteger(page) || page < 1 || page > 10_000) {
    throw new InventoryQueryContractError("page 必须是 1 到 10000 的整数");
  }
  if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) {
    throw new InventoryQueryContractError("pageSize 必须是 1 到 100 的整数");
  }
  return { page, pageSize, offset: (page - 1) * pageSize };
}

export function calculateInventoryCostValuation(input: {
  availableQuantity: number;
  importedValueCents: number;
  importedPricedQuantity: number;
  fallbackUnitCostCents: number;
}) {
  const availableQuantity = Math.max(0, input.availableQuantity);
  const importedPricedQuantity = Math.min(availableQuantity, Math.max(0, input.importedPricedQuantity));
  const missingQuantity = Math.max(0, availableQuantity - importedPricedQuantity);
  const fallbackCoveredQuantity = input.fallbackUnitCostCents > 0 ? missingQuantity : 0;
  const coveredQuantity = Math.min(availableQuantity, importedPricedQuantity + fallbackCoveredQuantity);
  const knownStockValueCents = Math.max(0, Math.round(input.importedValueCents))
    + Math.round(fallbackCoveredQuantity * Math.max(0, input.fallbackUnitCostCents));
  const coverageRate = availableQuantity > 0 ? coveredQuantity / availableQuantity : 1;
  const completeStockValueCents = coveredQuantity >= availableQuantity ? knownStockValueCents : null;
  const unitCostCents = coveredQuantity > 0 ? Math.round(knownStockValueCents / coveredQuantity) : 0;
  return { coveredQuantity, coverageRate, knownStockValueCents, completeStockValueCents, unitCostCents };
}

export type InventoryAgeStatus = "healthy" | "aged" | "slow" | "stagnant" | "no_stock";

export const inventoryAgeBuckets = [
  { key: "0-7", label: "0–7 天", minDays: 0, maxDays: 7 },
  { key: "8-15", label: "8–15 天", minDays: 8, maxDays: 15 },
  { key: "16-30", label: "16–30 天", minDays: 16, maxDays: 30 },
  { key: "31-60", label: "31–60 天", minDays: 31, maxDays: 60 },
  { key: "61-90", label: "61–90 天", minDays: 61, maxDays: 90 },
  { key: "91-120", label: "91–120 天", minDays: 91, maxDays: 120 },
  { key: "121-150", label: "121–150 天", minDays: 121, maxDays: 150 },
  { key: "151-180", label: "151–180 天", minDays: 151, maxDays: 180 },
  { key: "181-360", label: "181–360 天", minDays: 181, maxDays: 360 },
  { key: "361+", label: "1 年以上", minDays: 361, maxDays: null },
] as const;

export type InventoryAgeBucketKey = (typeof inventoryAgeBuckets)[number]["key"];

export function classifyInventoryAge(input: {
  availableQuantity: number;
  inventoryAgeDays: number | null;
  sales30dQuantity: number | null;
}) {
  if (input.availableQuantity <= 0) {
    return { status: "no_stock" as const, label: "无可用库存", recommendation: "无需纳入滞销清理，等待下一次库存快照确认。" };
  }
  if (input.inventoryAgeDays === null) {
    return { status: "healthy" as const, label: "待补库龄", recommendation: "当前报表未提供库龄，暂不参与库龄预警。" };
  }
  if (input.sales30dQuantity !== null && input.inventoryAgeDays >= 90 && input.sales30dQuantity <= 0) {
    return { status: "stagnant" as const, label: "滞销清理", recommendation: "停止补货，优先评估促销、渠道调拨或清退。" };
  }
  if (input.sales30dQuantity !== null && input.inventoryAgeDays >= 60 && input.sales30dQuantity <= 3) {
    return { status: "slow" as const, label: "低动销", recommendation: "控制补货，结合价格和渠道方案提升动销。" };
  }
  if (input.inventoryAgeDays >= 90) {
    return { status: "aged" as const, label: "高库龄", recommendation: "库龄超过 90 天，建议核查动销并制定处理计划。" };
  }
  return { status: "healthy" as const, label: "库龄健康", recommendation: "持续观察库存周转与近 30 日销量。" };
}
