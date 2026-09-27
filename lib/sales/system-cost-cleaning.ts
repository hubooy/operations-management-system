import { isZeroCostProductName } from "./import-policy";
import type { SalesLineInput } from "./import-contract";

export type SystemCostRecord = {
  productCode: string;
  warehouse: string;
  unitCostCents: number;
};

export type SystemCostCleaningUnresolvedReason =
  | "MISSING_SYSTEM_COST"
  | "AMBIGUOUS_SYSTEM_COST"
  | "INVALID_CLEANED_AMOUNT";

export type SystemCostCleaningUnresolvedRow = {
  sourceRowNumber: number;
  productCode: string;
  productName: string;
  warehouse: string;
  reason: SystemCostCleaningUnresolvedReason;
};

export type SystemCostCleaningResult = {
  rows: SalesLineInput[];
  cleanedRowNumbers: number[];
  matchedByWarehouseRows: number;
  matchedByProductFallbackRows: number;
  skippedPriceAdjustmentRows: number;
  unresolvedRows: SystemCostCleaningUnresolvedRow[];
};

type CostResolution =
  | { kind: "warehouse" | "product"; unitCostCents: number }
  | { kind: "missing" | "ambiguous" };

function normalizedCode(value: string) {
  return value.trim().toUpperCase();
}

function normalizedWarehouse(value: string) {
  return value.trim();
}

function productWarehouseKey(productCode: string, warehouse: string) {
  return `${normalizedCode(productCode)}\u001f${normalizedWarehouse(warehouse)}`;
}

function isPriceAdjustment(row: SalesLineInput) {
  return row.productCode === "ERP_PRICE_ADJUSTMENT" || isZeroCostProductName(row.productName);
}

function addCost(costsByKey: Map<string, Set<number>>, key: string, unitCostCents: number) {
  const current = costsByKey.get(key) ?? new Set<number>();
  current.add(unitCostCents);
  costsByKey.set(key, current);
}

function resolveCost(
  row: SalesLineInput,
  costsByProductWarehouse: Map<string, Set<number>>,
  costsByProduct: Map<string, Set<number>>,
): CostResolution {
  const warehouseCosts = costsByProductWarehouse.get(productWarehouseKey(row.productCode, row.warehouse));
  if (warehouseCosts?.size === 1) return { kind: "warehouse", unitCostCents: [...warehouseCosts][0] };
  if (warehouseCosts && warehouseCosts.size > 1) return { kind: "ambiguous" };

  const productCosts = costsByProduct.get(normalizedCode(row.productCode));
  if (!productCosts || productCosts.size === 0) return { kind: "missing" };
  if (productCosts.size > 1) return { kind: "ambiguous" };
  return { kind: "product", unitCostCents: [...productCosts][0] };
}

function safeSubtract(left: number, right: number) {
  const result = left - right;
  return Number.isSafeInteger(result) ? result : null;
}

function grossMarginBps(grossProfitCents: number, allocatedAmountCents: number) {
  if (allocatedAmountCents === 0) return 0;
  const result = Math.round(grossProfitCents / allocatedAmountCents * 10_000);
  return Number.isSafeInteger(result) ? result : null;
}

/**
 * Replaces only physical sales lines whose source cost is exactly zero. Cost
 * resolution is deterministic: same warehouse first, then a product-wide
 * fallback only when every eligible system record agrees on the unit cost.
 */
export function cleanZeroCostSalesRows(
  rows: readonly SalesLineInput[],
  systemCosts: readonly SystemCostRecord[],
): SystemCostCleaningResult {
  const costsByProductWarehouse = new Map<string, Set<number>>();
  const costsByProduct = new Map<string, Set<number>>();
  for (const cost of systemCosts) {
    const productCode = normalizedCode(cost.productCode);
    const unitCostCents = Number(cost.unitCostCents);
    if (!productCode || !Number.isSafeInteger(unitCostCents) || unitCostCents <= 0) continue;
    addCost(costsByProductWarehouse, productWarehouseKey(productCode, cost.warehouse), unitCostCents);
    addCost(costsByProduct, productCode, unitCostCents);
  }

  const cleanedRows: SalesLineInput[] = [];
  const cleanedRowNumbers: number[] = [];
  const unresolvedRows: SystemCostCleaningUnresolvedRow[] = [];
  let matchedByWarehouseRows = 0;
  let matchedByProductFallbackRows = 0;
  let skippedPriceAdjustmentRows = 0;

  for (const row of rows) {
    if (row.costAmountCents !== 0) {
      cleanedRows.push(row);
      continue;
    }
    // "补差价专用" is a virtual amount adjustment rather than a merchandise
    // sale, so it deliberately retains its zero cost.
    if (isPriceAdjustment(row)) {
      skippedPriceAdjustmentRows += 1;
      cleanedRows.push(row);
      continue;
    }

    const resolution = resolveCost(row, costsByProductWarehouse, costsByProduct);
    if (resolution.kind !== "warehouse" && resolution.kind !== "product") {
      unresolvedRows.push({
        sourceRowNumber: row.sourceRowNumber,
        productCode: row.productCode,
        productName: row.productName,
        warehouse: row.warehouse,
        reason: resolution.kind === "missing" ? "MISSING_SYSTEM_COST" : "AMBIGUOUS_SYSTEM_COST",
      });
      cleanedRows.push(row);
      continue;
    }

    const costAmountCents = resolution.unitCostCents * row.quantity;
    const grossAfterCost = safeSubtract(row.allocatedAmountCents, costAmountCents);
    const grossProfitCents = grossAfterCost === null ? null : safeSubtract(grossAfterCost, row.feeAllocationCents);
    const recomputedGrossMarginBps = grossProfitCents === null ? null : grossMarginBps(grossProfitCents, row.allocatedAmountCents);
    if (!Number.isSafeInteger(costAmountCents)
      || grossProfitCents === null
      || recomputedGrossMarginBps === null) {
      unresolvedRows.push({
        sourceRowNumber: row.sourceRowNumber,
        productCode: row.productCode,
        productName: row.productName,
        warehouse: row.warehouse,
        reason: "INVALID_CLEANED_AMOUNT",
      });
      cleanedRows.push(row);
      continue;
    }

    cleanedRows.push({
      ...row,
      costAmountCents,
      grossProfitCents,
      grossMarginBps: recomputedGrossMarginBps,
      // The sales export has no tax-exclusive revenue denominator. Keep the
      // normalized tax-exclusive fields aligned with the cleaned gross result.
      untaxedGrossProfitCents: grossProfitCents,
      untaxedGrossMarginBps: recomputedGrossMarginBps,
    });
    cleanedRowNumbers.push(row.sourceRowNumber);
    if (resolution.kind === "warehouse") matchedByWarehouseRows += 1;
    else matchedByProductFallbackRows += 1;
  }

  return {
    rows: cleanedRows,
    cleanedRowNumbers,
    matchedByWarehouseRows,
    matchedByProductFallbackRows,
    skippedPriceAdjustmentRows,
    unresolvedRows,
  };
}
