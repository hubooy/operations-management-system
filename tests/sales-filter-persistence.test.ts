import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { readSalesSharedFilters } from "../app/sales-filter-bar";
import { updateModuleViewLocation } from "../app/shell/navigation-contract";
import { parseProductQueriesStrict, SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH } from "../lib/sales/read-contract";
import { normalizeProductSummaryQuery } from "../lib/products/query-contract";

test("商品经营搜索接受1000字符且不会截断超限输入", () => {
  assert.equal(normalizeProductSummaryQuery("码".repeat(1000)), "码".repeat(1000));
  assert.throws(() => normalizeProductSummaryQuery("码".repeat(1001)), /1000/);
});

test("销售多代码换行与逗号输入去重且刷新后不丢失第500字符之后的代码", () => {
  const codes = Array.from({ length: 100 }, (_, index) => `SKU-${String(index).padStart(3, "0")}`);
  const raw = codes.join(",\n");
  const url = new URL("https://example.test/");
  url.searchParams.set("salesProductQuery", raw);
  const restored = readSalesSharedFilters(url.href).productQuery;
  assert.equal(restored, raw);
  assert.deepEqual(parseProductQueriesStrict(restored), codes);
  assert.deepEqual(parseProductQueriesStrict("SKU-1\r\nSKU-2，SKU-1;SKU-3"), ["SKU-1", "SKU-2", "SKU-3"]);
  assert.throws(() => parseProductQueriesStrict([...codes, "SKU-101"]), /最多 100 项/);
  assert.throws(() => parseProductQueriesStrict("X".repeat(201)), /200/);
  assert.equal(SALES_PRODUCT_QUERY_TEXT_MAX_LENGTH, 1000);
  // Use ten long codes so per-code and item-count bounds remain valid.
  const boundary = Array.from({ length: 10 }, (_, i) => String(i).repeat(i === 9 ? 91 : 100)).join(",");
  assert.equal(boundary.length, 1000);
  assert.equal(parseProductQueriesStrict(boundary).length, 10);
  assert.throws(() => parseProductQueriesStrict(`${boundary}Z`), /1000/);
});

test("销售公共筛选从 URL 去重、去空并恢复所有跨页签维度", () => {
  const filters = readSalesSharedFilters("https://example.test/?module=sales&salesPlatform=%E4%BA%AC%E4%B8%9C&salesPlatform=%E4%BA%AC%E4%B8%9C&salesOutlet=%E4%BA%AC%E4%B8%9C%1F%E6%97%97%E8%88%B0%E5%BA%97&salesCategory=%E5%95%86%E7%94%A8%E5%87%80%E6%B0%B4&salesChannel=%E7%BA%BF%E4%B8%8A&salesProductQuery=SKU-1");

  assert.deepEqual(filters, {
    platforms: ["京东"],
    outletKeys: ["京东\u001f旗舰店"],
    categories: ["商用净水"],
    channels: ["线上"],
    productQuery: "SKU-1",
  });
});

test("销售 tab 切换保留公共筛选查询参数", () => {
  const next = updateModuleViewLocation(
    "/?module=sales&salesPlatform=%E4%BA%AC%E4%B8%9C&salesCategory=%E5%95%86%E7%94%A8%E5%87%80%E6%B0%B4",
    "sales",
    "category",
  );

  const url = new URL(next, "https://example.test");
  assert.equal(url.searchParams.get("view"), "category");
  assert.deepEqual(url.searchParams.getAll("salesPlatform"), ["京东"]);
  assert.deepEqual(url.searchParams.getAll("salesCategory"), ["商用净水"]);
});

test("销售各分析页由同一父级筛选状态驱动，品类页不再拥有公共 URL 字段", async () => {
  const [sales, category] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/sales-category-view.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(sales, /<SalesFilterBar filters=\{filters\}/);
  assert.match(sales, /SalesCategoryView[\s\S]*filters=\{filters\}[\s\S]*onFiltersChange=\{updateFilters\}/);
  assert.match(sales, /FinanceAnalysisView[\s\S]*selectedPlatforms=\{filters\.platforms\}[\s\S]*selectedShopKeys=\{filters\.outletKeys\}/);
  assert.doesNotMatch(category.slice(category.indexOf("const categoryOwnedUrlKeys"), category.indexOf("] as const;")), /salesPlatform|salesOutlet|salesCategory"|salesProductQuery/);
});
