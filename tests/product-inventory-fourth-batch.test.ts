import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import {
  normalizeProductSummarySelections,
  normalizeProductSummaryPagination,
  parseProductSummaryPaginationParameter,
  resolveProductSummaryPeriod,
} from "../lib/products/query-contract";
import {
  calculateInventoryCostValuation,
  classifyInventoryAge,
  normalizeInventorySelections,
  normalizeInventoryPagination,
  parseInventoryPaginationParameter,
} from "../lib/inventory/query-contract";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("product summary pagination and actual period are bounded", () => {
  for (const options of [
    { page: -10 }, { page: 1.5 }, { page: Number.POSITIVE_INFINITY }, { page: 10_001 },
    { pageSize: 0 }, { pageSize: 1.5 }, { pageSize: 101 }, { pageSize: Number.POSITIVE_INFINITY },
  ]) assert.throws(() => normalizeProductSummaryPagination(options), /page|pageSize/);
  for (const raw of ["1e2", "+1", "1.5", "0", "101", "Infinity"]) {
    assert.throws(() => parseProductSummaryPaginationParameter(raw, "pageSize"), /pageSize/);
  }
  assert.deepEqual(normalizeProductSummaryPagination({ page: 3, pageSize: 25 }), {
    page: 3,
    pageSize: 25,
    offset: 50,
  });
  assert.throws(
    () => normalizeProductSummarySelections(["below35", "typo"], {
      maximum: 5,
      allowed: ["below35", "35to40"],
      label: "毛利率",
    }),
    /允许清单/,
  );
  assert.throws(
    () => normalizeProductSummarySelections(Array.from({ length: 11 }, (_, index) => `类目${index}`), {
      maximum: 10,
      label: "类目",
    }),
    /数量或长度/,
  );
  assert.deepEqual(resolveProductSummaryPeriod(
    { startDate: "2026-01-01", endDate: "2026-08-18" },
    { range: "custom", startDate: "2026-08-01", endDate: "2026-08-20" },
  ), {
    range: "custom",
    requestedStartDate: "2026-08-01",
    requestedEndDate: "2026-08-20",
    startDate: "2026-08-01",
    endDate: "2026-08-18",
  });
  assert.throws(
    () => resolveProductSummaryPeriod(
      { startDate: "2024-01-01", endDate: "2026-08-18" },
      { range: "custom", startDate: "2024-01-01", endDate: "2026-08-18" },
    ),
    /最多支持 730 天/,
  );
});

test("inventory pagination and partial cost coverage remain honest", () => {
  for (const options of [
    { page: -1 }, { page: 0 }, { page: 1.5 }, { page: Number.POSITIVE_INFINITY }, { page: 10_001 },
    { pageSize: 0 }, { pageSize: 1.5 }, { pageSize: 101 }, { pageSize: Number.POSITIVE_INFINITY },
  ]) assert.throws(() => normalizeInventoryPagination(options), /page|pageSize/);
  for (const raw of ["1e2", "+1", "1.5", "0", "101", "Infinity"]) {
    assert.throws(() => parseInventoryPaginationParameter(raw, "pageSize"), /pageSize/);
  }
  assert.deepEqual(calculateInventoryCostValuation({
    availableQuantity: 100,
    importedValueCents: 1_000,
    importedPricedQuantity: 1,
    fallbackUnitCostCents: 0,
  }), {
    coveredQuantity: 1,
    coverageRate: 0.01,
    knownStockValueCents: 1_000,
    completeStockValueCents: null,
    unitCostCents: 1_000,
  });
  assert.throws(
    () => normalizeInventorySelections(["healthy", "unknown"], {
      maximum: 5,
      allowed: ["healthy", "aged", "slow", "stagnant", "no_stock"],
      label: "库龄状态",
    }),
    /允许清单/,
  );
  assert.throws(
    () => normalizeInventorySelections(Array.from({ length: 11 }, (_, index) => `仓库${index}`), {
      maximum: 10,
      label: "仓库",
    }),
    /数量或长度/,
  );
  assert.deepEqual(calculateInventoryCostValuation({
    availableQuantity: 100,
    importedValueCents: 1_000,
    importedPricedQuantity: 1,
    fallbackUnitCostCents: 50,
  }), {
    coveredQuantity: 100,
    coverageRate: 1,
    knownStockValueCents: 5_950,
    completeStockValueCents: 5_950,
    unitCostCents: 60,
  });
});

test("missing row-level age sales never becomes zero sales", () => {
  assert.equal(classifyInventoryAge({
    availableQuantity: 10,
    inventoryAgeDays: 120,
    sales30dQuantity: null,
  }).status, "aged");
  assert.equal(classifyInventoryAge({
    availableQuantity: 10,
    inventoryAgeDays: 120,
    sales30dQuantity: 0,
  }).status, "stagnant");
});

test("product and inventory APIs expose real page contracts", async () => {
  const [product, productBackend, productRoute, inventory, age, erpDatabase, plans] = await Promise.all([
    source("../lib/products/summary.ts"),
    source("../backend/products/query.py"),
    source("../app/api/products/summary/route.ts"),
    source("../lib/inventory/overview.ts"),
    source("../lib/inventory/age-analysis.ts"),
    source("../lib/erp-reference/database.ts"),
    source("../lib/inventory/database.ts"),
  ]);
  assert.match(product, /PRODUCTS_SUMMARY_PATH/);
  assert.match(product, /rawQuery: summaryQuery\(options\)/);
  assert.match(productBackend, /selected = filtered\[offset : offset \+ page_size\]/);
  assert.match(productBackend, /"pagination": _pagination/);
  assert.match(productBackend, /facet_rows/);
  assert.match(productBackend, /known_stock_value_cents/);
  assert.match(productBackend, /priced_available_quantity/);
  assert.match(productBackend, /available <= priced/);
  assert.match(productBackend, /"operation": "product_performance"/);
  assert.match(productRoute, /searchParams\.get\("page"\)/);
  assert.match(productRoute, /searchParams\.get\("pageSize"\)/);
  assert.match(productRoute, /parseProductSummaryPaginationParameter/);
  assert.match(productRoute, /searchParams\.get\("q"\)/);
  assert.match(inventory, /page_rows AS MATERIALIZED[\s\S]*?ORDER BY \$\{INVENTORY_PAGE_ORDER_SQL\}[\s\S]*?LIMIT \? OFFSET \?/);
  assert.match(inventory, /returned:/);
  assert.match(inventory, /stockValueComplete/);
  assert.match(inventory, /operation: "inventory_demand"/);
  assert.match(age, /findLatestCompletedErpReferenceBatch/);
  assert.match(erpDatabase, /status = 'completed'/);
  assert.match(age, /LIMIT \? OFFSET \?/);
  assert.match(plans, /total:/);
  assert.match(plans, /includeCancelled/);
});

test("replenishment, AI and frontend do not reuse truncated pages", async () => {
  const [route, backend, ai, inventoryView, productView] = await Promise.all([
    source("../app/api/inventory/replenishment/route.ts"),
    source("../backend/inventory/views.py"),
    source("../lib/ai/operations-tools.ts"),
    source("../app/inventory-module-view.tsx"),
    source("../app/product-module-view.tsx"),
  ]);
  const frontend = `${inventoryView}\n${productView}`;
  assert.match(route, /path: INVENTORY_REPLENISHMENT_PATH, service: "writer", payload: body/);
  assert.match(route, /const parts = body\.key\.split\("\\u001f"\)/);
  assert.match(backend, /"exactKey": key/);
  assert.match(backend, /"startDate": body\.get\("startDate"\)/);
  assert.doesNotMatch(ai, /const overview = await getInventoryOverview\(db\);[\s\S]*?overview\.items\.filter/);
  assert.match(inventoryView, /useState\(shanghaiIsoToday\)/);
  assert.match(inventoryView, /currentUser\?\.role === "admin"/);
  assert.match(inventoryView, /overviewGenerationRef/);
  assert.match(productView, /productSummaryGenerationRef/);
  assert.doesNotMatch(inventoryView, /InventoryKpiCard label=\{overview\.metrics\.stockValueComplete \? "库存货值"/);
  assert.match(inventoryView, /stockValueComplete/);
  assert.doesNotMatch(frontend, /filtered\.slice\(0, 300\)/);
  assert.doesNotMatch(frontend, /filteredItems\.slice\(0, 300\)/);
  assert.doesNotMatch(frontend, /cleanupItems\.slice\(/);
});

test("netshop requests reject late filter responses", async () => {
  const page = await source("../app/shop-module-view.tsx");
  for (const gate of [
    "productPerformanceGenerationRef",
    "skuCatalogGenerationRef",
    "promotionItemsGenerationRef",
    "promotionOverviewGenerationRef",
  ]) {
    assert.match(page, new RegExp(`${gate}\\.current`));
  }
  assert.match(page, /signal: controller\.signal/);
  assert.match(page, /generation === productPerformanceGenerationRef\.current/);
  assert.match(page, /generation === skuCatalogGenerationRef\.current/);
  assert.match(page, /generation === promotionItemsGenerationRef\.current/);
  assert.match(page, /generation === promotionOverviewGenerationRef\.current/);
});

test("page consumes customer and finance bounded detail, pagination and CAS contracts", async () => {
  const [sales, customer] = await Promise.all([
    source("../app/sales-module-view.tsx"),
    source("../app/customer-service-view.tsx"),
  ]);
  assert.match(customer, /listControllerRef\.current\?\.abort\(\)/);
  assert.match(customer, /listGenerationRef\.current === generation/);
  assert.match(customer, /listRequestKeyRef\.current === requestKey/);
  assert.match(customer, /signal: controller\.signal/);
  assert.match(customer, /customer-service\/conversations\?id=\$\{id\}/);
  assert.match(customer, /detailGenerationRef/);
  assert.match(customer, /messageTotalCount/);
  assert.match(customer, /messagesTruncated/);
  assert.match(customer, /expectedVersion: item\.version/);
  assert.match(customer, /payload\.incomplete === true/);
  assert.match(customer, /冲突 \$\{conflictCount\}、失败 \$\{failedCount\}/);
  assert.match(sales, /finance\/targets\?view=items&page=\$\{targetPage\}&pageSize=100/);
  assert.match(sales, /targetRequestGenerationRef/);
  assert.match(sales, /expectedVersion: form\.id \? form\.expectedVersion/);
  assert.match(sales, /const query = new URLSearchParams\(\{[\s\S]*?expectedVersion: String\(item\.version\),[\s\S]*?reason: reasonResult\.reason,/);
  assert.match(sales, /finance\/targets\?\$\{query\.toString\(\)\}/);
  assert.match(sales, /targetPagination\.truncated/);
  assert.match(sales, /data\.selection\?\.truncated/);
});

test("customer detail uses the shared dialog and saves both draft fields through one CAS", async () => {
  const page = await source("../app/customer-service-view.tsx");
  assert.match(page, /import Dialog from "\.\/ui\/dialog"/);
  assert.match(page, /dialogId="customer-service-conversation-detail"/);
  assert.match(page, /initialFocusRef=\{customerDialogCloseRef\}/);
  assert.match(page, /if \(selected && busyId === selected\.id\) return/);
  assert.match(page, /expectedVersion: item\.version,[\s\S]*?serviceIssues: draft\.serviceIssues,[\s\S]*?summaryText: draft\.summaryText/);
  assert.match(page, /服务端版本已刷新，你的两项草稿均已保留/);
  assert.match(page, /"保存详情标注"/);
  assert.doesNotMatch(page, /\{selected && <div className="modal-backdrop"/);
  assert.doesNotMatch(page, /onBlur=\{\(\) => void saveAnnotation\(selected/);
});
