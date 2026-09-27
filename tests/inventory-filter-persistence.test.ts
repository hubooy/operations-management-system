import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { readInventorySharedFilters } from "../app/inventory-filter-bar";
import { updateModuleViewLocation } from "../app/shell/navigation-contract";

test("库存公共筛选从 URL 去重、去空并恢复公共与各 Tab 专属条件", () => {
  const filters = readInventorySharedFilters("https://example.test/?module=inventory&inventoryQuery=SKU-1&inventoryWarehouse=%E5%8D%8E%E4%B8%9C%E4%BB%93&inventoryWarehouse=%E5%8D%8E%E4%B8%9C%E4%BB%93&inventoryWarehouse=%20&inventoryBrand=%E5%93%81%E7%89%8C%E7%94%B2&inventoryCategory=%E5%87%80%E6%B0%B4&inventoryWarehouseType=owned&inventoryWarehouseType=invalid&inventoryHealthStatus=warning&inventoryAgeStatus=stagnant&inventoryAgeBucket=91-120&inventorySupplier=%E4%BE%9B%E5%BA%94%E5%95%86%E7%94%B2&inventoryPlanStatus=confirmed");

  assert.deepEqual(filters, {
    productQuery: "SKU-1",
    warehouses: ["华东仓"],
    brands: ["品牌甲"],
    categories: ["净水"],
    warehouseTypes: ["owned"],
    healthStatuses: ["warning"],
    ageStatuses: ["stagnant"],
    ageBuckets: ["91-120"],
    suppliers: ["供应商甲"],
    planStatus: "confirmed",
  });
});

test("库存 Tab 切换保留全部公共筛选查询参数", () => {
  const next = updateModuleViewLocation(
    "/?module=inventory&inventoryQuery=SKU-1&inventoryWarehouse=%E5%8D%8E%E4%B8%9C%E4%BB%93&inventoryBrand=%E5%93%81%E7%89%8C%E7%94%B2&inventoryCategory=%E5%87%80%E6%B0%B4&inventoryAgeBucket=91-120",
    "inventory",
    "inbound",
  );

  const url = new URL(next, "https://example.test");
  assert.equal(url.searchParams.get("view"), "inbound");
  assert.equal(url.searchParams.get("inventoryQuery"), "SKU-1");
  assert.deepEqual(url.searchParams.getAll("inventoryWarehouse"), ["华东仓"]);
  assert.deepEqual(url.searchParams.getAll("inventoryBrand"), ["品牌甲"]);
  assert.deepEqual(url.searchParams.getAll("inventoryCategory"), ["净水"]);
  assert.deepEqual(url.searchParams.getAll("inventoryAgeBucket"), ["91-120"]);
});

test("库存五个 Tab 由同一父级筛选状态驱动并向各接口透传公共维度", async () => {
  const [view, filterBar, overviewRoute, inboundRoute] = await Promise.all([
    readFile(new URL("../app/inventory-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/inventory-filter-bar.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/inventory/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/inventory/inbound-monitor/route.ts", import.meta.url), "utf8"),
  ]);

  assert.match(view, /useState<InventorySharedFilters>\(readInventorySharedFilters\)/);
  assert.match(view, /<InventoryFilterBar[\s\S]*filters=\{filters\}[\s\S]*onChange=\{updateFilters\}/);
  assert.match(view, /writeInventorySharedFilters\(next\)/);
  assert.match(view, /filters\.warehouses\.forEach\(\(value\) => params\.append\("warehouse", value\)\)/);
  assert.match(view, /filters\.brands\.forEach\(\(value\) => params\.append\("brand", value\)\)/);
  assert.match(view, /filters\.categories\.forEach\(\(value\) => params\.append\("category", value\)\)/);
  assert.match(view, /filters\.warehouses\.forEach\(\(warehouse\) => params\.append\("warehouse", warehouse\)\)/);
  assert.match(view, /filters\.brands\.forEach\(\(brand\) => params\.append\("brand", brand\)\)/);
  assert.match(view, /filters\.categories\.forEach\(\(category\) => params\.append\("category", category\)\)/);
  assert.match(filterBar, /inventoryWarehouse/);
  assert.match(filterBar, /inventoryBrand/);
  assert.match(filterBar, /inventoryCategory/);
  assert.match(filterBar, /库存管理公共筛选/);
  assert.match(overviewRoute, /normalizeInventorySelections\(params\.getAll\("brand"\)/);
  assert.match(overviewRoute, /normalizeInventorySelections\(params\.getAll\("category"\)/);
  assert.match(overviewRoute, /rawQuery: params\.toString\(\)/);
  assert.match(inboundRoute, /normalizeInventorySelections\(params\.getAll\("brand"\)/);
  assert.match(inboundRoute, /normalizeInventorySelections\(params\.getAll\("category"\)/);
});
