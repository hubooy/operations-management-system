import assert from "node:assert/strict";
import test from "node:test";
import type { SalesLineInput } from "../lib/sales/import-contract";
import { cleanZeroCostSalesRows } from "../lib/sales/system-cost-cleaning";

function salesRow(overrides: Partial<SalesLineInput> = {}): SalesLineInput {
  return {
    sourceRowNumber: 2,
    sourceLineKey: "gky:test:1",
    sourceRowHash: "source-row-hash",
    orderNo: "ORDER-1",
    onlineOrderNo: "ONLINE-1",
    channel: "京东-志高切肉机旗舰店（志高迈德豪）",
    platform: "京东",
    shopName: "志高切肉机旗舰店（志高迈德豪）",
    logisticsCompany: "",
    warehouse: "主仓",
    productCode: "SKU-1",
    onlineSpecCode: "JD-SKU-1",
    productName: "测试货品",
    specification: "",
    barcode: "",
    supplier: "",
    category: "测试",
    quantity: 2,
    listUnitPriceCents: 5_000,
    costAmountCents: 0,
    allocatedUnitPriceCents: 5_000,
    allocatedAmountCents: 10_000,
    feeAllocationCents: 100,
    grossProfitCents: 9_900,
    grossMarginBps: 9_900,
    untaxedGrossProfitCents: 9_000,
    untaxedGrossMarginBps: 9_000,
    orderTime: "2026-07-01 10:00:00",
    salesTime: "2026-07-01 10:00:00",
    shipTime: "2026-07-01 10:00:00",
    lineShipTime: "",
    businessType: "sale",
    ...overrides,
  };
}

test("zero source costs use the same-warehouse system cost and recompute profit", () => {
  const result = cleanZeroCostSalesRows([
    salesRow(),
  ], [
    { productCode: "sku-1", warehouse: "主仓", unitCostCents: 3_000 },
    { productCode: "SKU-1", warehouse: "异地仓", unitCostCents: 4_000 },
  ]);

  assert.deepEqual(result.unresolvedRows, []);
  assert.equal(result.matchedByWarehouseRows, 1);
  assert.equal(result.matchedByProductFallbackRows, 0);
  assert.equal(result.rows[0].costAmountCents, 6_000);
  assert.equal(result.rows[0].grossProfitCents, 3_900);
  assert.equal(result.rows[0].grossMarginBps, 3_900);
  assert.equal(result.rows[0].untaxedGrossProfitCents, 3_900);
  assert.equal(result.rows[0].untaxedGrossMarginBps, 3_900);
});

test("a unique product-level cost can clean a return when its warehouse is absent", () => {
  const result = cleanZeroCostSalesRows([
    salesRow({
      warehouse: "发货仓",
      quantity: -1,
      allocatedAmountCents: -10_000,
      feeAllocationCents: -100,
      grossProfitCents: -9_900,
      untaxedGrossProfitCents: -9_000,
      businessType: "return",
    }),
  ], [
    { productCode: "SKU-1", warehouse: "主仓", unitCostCents: 700 },
    { productCode: "SKU-1", warehouse: "异地仓", unitCostCents: 700 },
  ]);

  assert.deepEqual(result.unresolvedRows, []);
  assert.equal(result.matchedByProductFallbackRows, 1);
  assert.equal(result.rows[0].costAmountCents, -700);
  assert.equal(result.rows[0].grossProfitCents, -9_200);
  assert.equal(result.rows[0].grossMarginBps, 9_200);
});

test("ambiguous costs block automatic cleanup while price adjustments remain zero-cost", () => {
  const result = cleanZeroCostSalesRows([
    salesRow(),
    salesRow({
      sourceRowNumber: 3,
      sourceLineKey: "gky:test:2",
      productCode: "ERP_PRICE_ADJUSTMENT",
      productName: "补差价专用",
    }),
  ], [
    { productCode: "SKU-1", warehouse: "A仓", unitCostCents: 100 },
    { productCode: "SKU-1", warehouse: "B仓", unitCostCents: 200 },
  ]);

  assert.equal(result.unresolvedRows.length, 1);
  assert.equal(result.unresolvedRows[0].reason, "AMBIGUOUS_SYSTEM_COST");
  assert.equal(result.skippedPriceAdjustmentRows, 1);
  assert.equal(result.rows[1].costAmountCents, 0);
});
