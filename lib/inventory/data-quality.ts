import type { InventoryStockRow } from "@/lib/imports/inventory-stock";

export const MIN_RELIABLE_SALES_MATCH_RATE = 0.6;

const MAX_ABSOLUTE_ROW_QUANTITY = 10_000_000;
const MAX_INVENTORY_AGE_DAYS = 3_650;
const MAX_UNIT_COST_CENTS = 100_000_000;
const MAX_ROW_STOCK_VALUE_CENTS = 100_000_000_000;
const SUMMARY_LABEL = /^(?:合计|总计|小计|汇总|grand\s*total|total)$/i;

export type InventoryDataQualityIssue = {
  code: string;
  severity: "warning" | "blocking";
  message: string;
  affectedCount: number;
};

export type InventoryDataQuality = {
  status: "reliable" | "degraded" | "blocked";
  salesMatchThreshold: number;
  salesDemandMatchRate: number;
  recommendationsSuppressed: boolean;
  issues: InventoryDataQualityIssue[];
};

export type InventoryImportQualityIssue = {
  row: number;
  field: string;
  code: string;
  message: string;
};

function summaryLabel(value: string) {
  return SUMMARY_LABEL.test(value.trim());
}

export function validateInventoryImportRows(
  rows: readonly InventoryStockRow[],
  options: { allowNegativeInventory: boolean },
): InventoryImportQualityIssue[] {
  const issues: InventoryImportQualityIssue[] = [];
  for (const row of rows) {
    const identityFields = [row.productCode, row.productName, row.warehouse];
    if (identityFields.some(summaryLabel)) {
      issues.push({
        row: row.sourceRowNumber,
        field: "productCode",
        code: "AGGREGATE_ROW_NOT_ALLOWED",
        message: "检测到合计、总计或汇总行；库存导入只接受仓库 × 货品明细",
      });
      continue;
    }

    if (!options.allowNegativeInventory && (row.onHandQuantity < 0 || row.availableQuantity < 0)) {
      issues.push({
        row: row.sourceRowNumber,
        field: row.availableQuantity < 0 ? "availableQuantity" : "onHandQuantity",
        code: "NEGATIVE_INVENTORY_NOT_ALLOWED",
        message: "系统设置未允许负库存，本行实盘或可用库存为负数",
      });
    }

    const quantities = [
      ["onHandQuantity", row.onHandQuantity],
      ["availableQuantity", row.availableQuantity],
      ["lockedQuantity", row.lockedQuantity],
      ["inTransitQuantity", row.inTransitQuantity],
    ] as const;
    const extremeQuantity = quantities.find(([, value]) => Math.abs(value) > MAX_ABSOLUTE_ROW_QUANTITY);
    if (extremeQuantity) {
      issues.push({
        row: row.sourceRowNumber,
        field: extremeQuantity[0],
        code: "IMPLAUSIBLE_INVENTORY_QUANTITY",
        message: `单行数量绝对值超过 ${MAX_ABSOLUTE_ROW_QUANTITY.toLocaleString("zh-CN")}，请确认是否误读了合计行或数字单位`,
      });
    }

    if (row.inventoryAgeDays !== null && (row.inventoryAgeDays < 0 || row.inventoryAgeDays > MAX_INVENTORY_AGE_DAYS)) {
      issues.push({
        row: row.sourceRowNumber,
        field: "inventoryAgeDays",
        code: "IMPLAUSIBLE_INVENTORY_AGE",
        message: `库龄必须在 0 到 ${MAX_INVENTORY_AGE_DAYS.toLocaleString("zh-CN")} 天之间；请确认是否把 Excel 日期序号当成了库龄`,
      });
    }

    if (row.unitCostCents > MAX_UNIT_COST_CENTS) {
      issues.push({
        row: row.sourceRowNumber,
        field: "unitCostCents",
        code: "IMPLAUSIBLE_UNIT_COST",
        message: "单件成本超过 100 万元，请确认成本列与金额单位",
      });
    }

    const rowStockValue = Math.max(0, row.availableQuantity) * row.unitCostCents;
    if (!Number.isSafeInteger(rowStockValue) || rowStockValue > MAX_ROW_STOCK_VALUE_CENTS) {
      issues.push({
        row: row.sourceRowNumber,
        field: "stockValueCents",
        code: "IMPLAUSIBLE_ROW_STOCK_VALUE",
        message: "单个仓库货品的库存货值超过 10 亿元，请确认数量与成本单位",
      });
    }
  }
  return issues;
}

export function assessInventoryOverviewQuality(input: {
  hasInventory: boolean;
  salesDemandMatchRate: number;
  skuWarehouseCount: number;
  totalAvailableQuantity: number;
  knownStockValueCents: number;
  inventoryStale: boolean;
  autoReplenishment: boolean;
}): InventoryDataQuality {
  const issues: InventoryDataQualityIssue[] = [];
  const matchRate = Number.isFinite(input.salesDemandMatchRate)
    ? Math.max(0, Math.min(1, input.salesDemandMatchRate))
    : 0;

  if (input.hasInventory && matchRate < MIN_RELIABLE_SALES_MATCH_RATE) {
    const unmatchedCount = Math.max(0, Math.round(input.skuWarehouseCount * (1 - matchRate)));
    issues.push({
      code: "LOW_SALES_MAPPING_COVERAGE",
      severity: "blocking",
      affectedCount: unmatchedCount,
      message: `只有 ${(matchRate * 100).toFixed(1)}% 的库存行匹配到同货品、同仓库销量，低于 ${(MIN_RELIABLE_SALES_MATCH_RATE * 100).toFixed(0)}% 门槛`,
    });
  }
  if (input.inventoryStale) {
    issues.push({ code: "STALE_INVENTORY_SNAPSHOT", severity: "warning", affectedCount: input.skuWarehouseCount, message: "库存快照已超过 3 天，请先同步最新库存" });
  }
  if (input.hasInventory && !input.autoReplenishment) {
    issues.push({ code: "AUTO_REPLENISHMENT_DISABLED", severity: "blocking", affectedCount: 0, message: "系统设置已关闭自动补货建议" });
  }

  const averageQuantity = input.skuWarehouseCount > 0
    ? input.totalAvailableQuantity / input.skuWarehouseCount
    : 0;
  if (averageQuantity > 1_000_000) {
    issues.push({ code: "IMPLAUSIBLE_AVERAGE_QUANTITY", severity: "blocking", affectedCount: input.skuWarehouseCount, message: "平均每个仓库货品的可用库存超过 100 万，需复核数量单位或汇总行" });
  }
  const averageValueCents = input.skuWarehouseCount > 0
    ? input.knownStockValueCents / input.skuWarehouseCount
    : 0;
  if (averageValueCents > 5_000_000_000) {
    issues.push({ code: "IMPLAUSIBLE_AVERAGE_STOCK_VALUE", severity: "blocking", affectedCount: input.skuWarehouseCount, message: "平均每个仓库货品的库存货值超过 5,000 万元，需复核数量与成本单位" });
  }

  const blocked = issues.some((issue) => issue.severity === "blocking");
  return {
    status: blocked ? "blocked" : issues.length > 0 ? "degraded" : "reliable",
    salesMatchThreshold: MIN_RELIABLE_SALES_MATCH_RATE,
    salesDemandMatchRate: matchRate,
    recommendationsSuppressed: blocked,
    issues,
  };
}
