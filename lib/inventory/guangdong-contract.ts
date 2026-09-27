export type GuangdongWatchRow = { productCode: string; active: boolean; notes: string };
export type GuangdongIdentity = { productCode: string; productName: string; specification: string; brand: string; category: string; supplier: string; supplierSource: string };
export type GuangdongRisk = "no_stock" | "urgent" | "warning" | "stale" | "unknown" | "healthy";
export type GuangdongItem = GuangdongIdentity & {
  warehouse: string; notes: string; availableQuantity: number | null; inTransitQuantity: number | null;
  inventoryAgeDays: number | null; unitCostCents: number | null; knownStockValueCents: number; costMissing: boolean;
  outbound7dQuantity: number | null; outbound15dQuantity: number | null; outbound30dQuantity: number | null;
  leadDays: number | null; bufferDays: number; supplierLeadDays: number | null; supplierBufferDays: number;
  leadDaysOverride: number | null; bufferDaysOverride: number | null;
  cycleSource: "型号设置" | "供应商设置" | "待设置"; inventoryStale: boolean;
  replenishmentQuantity: number | null; latestReplenishmentOrderDate: string | null;
  replenishmentStockIncreaseQuantity: number | null; replenishmentRemainingQuantity: number | null;
  replenishmentRemainingReason: string;
  operatorName: string; operatorNameOverride: string | null; planOperatorName: string; operatorNameSource: "型号设置" | "最新备货计划" | "待设置";
  buyer: string; buyerOverride: string | null; planBuyer: string; buyerSource: "型号设置" | "最新备货计划" | "待设置";
  turnoverDays: number | null; latestOrderDate: string | null; risk: GuangdongRisk; riskLabel: string; riskReasons: string[];
  autoRisk: GuangdongRisk; autoRiskLabel: string; autoRiskReasons: string[];
  riskOverride: GuangdongRisk | null; riskReasonOverride: string | null; riskSource: "型号设置" | "系统判定" | "备货跟进";
};
export type GuangdongMonitor = {
  version: string; hasInventory: boolean; watchCount: number;
  sync: { inventoryAsOf: string | null; inventoryAgeAsOf: string | null; salesThrough: string | null; latestInventoryBatchId: string | null; inventoryStale: boolean };
  filters: { brands: string[]; categories: string[]; suppliers: string[] };
  metrics: { itemCount: number; availableQuantity: number; inTransitQuantity: number; knownStockValueCents: number; missingCostCount: number; missingStockCount: number };
  distribution: Array<{ risk: GuangdongRisk; label: string; itemCount: number; quantity: number; knownStockValueCents: number; itemRate: number; quantityRate: number; valueRate: number }>;
  pagination: { page: number; pageSize: number; total: number; totalPages: number }; items: GuangdongItem[]; disclosures: string[];
};
export type GuangdongPreview = {
  version: string; contentHash: string; valid: boolean;
  counts: { added: number; updated: number; unchanged: number };
  errors: Array<{ row?: number; productCode?: string; error: string }>;
  items: Array<GuangdongWatchRow & { change: "added" | "updated" | "unchanged" }>;
};
