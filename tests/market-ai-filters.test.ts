import assert from "node:assert/strict";
import test from "node:test";
import { marketAiFilters, marketAiOverview } from "../lib/market/ai-tools";

test("market AI's registered singular inputs reach the Django array filters", () => {
  assert.deepEqual(marketAiFilters({
    category: "商用切菜机", scope: "成交榜", rankingDimension: "SKU",
    operationMode: "POP", brand: "测试品牌", subcategory: "切菜机",
    startDate: "2026-08-01", endDate: "2026-08-31", query: "切菜",
  }), {
    categories: ["商用切菜机"], scopes: ["成交榜"], rankingDimensions: ["SKU"],
    operationModes: ["POP"], brands: ["测试品牌"], subcategories: ["切菜机"],
    startDate: "2026-08-01", endDate: "2026-08-31", query: "切菜", priceBands: [],
  });
});

test("market AI keeps explicit legacy array filters and deduplicates equal values", () => {
  const filters = marketAiFilters({categories: ["A", "B", "A"], scopes: ["榜单"], rankingDimensions: ["SPU"]});
  assert.deepEqual(filters.categories, ["A", "B"]);
  assert.deepEqual(filters.scopes, ["榜单"]);
  assert.deepEqual(filters.rankingDimensions, ["SPU"]);
  assert.deepEqual(marketAiFilters({category: "A", categories: ["A"]}).categories, ["A"]);
});

test("market AI never widens conflicting, invalid or over-limit filters", () => {
  for (const input of [
    {category: "A", categories: ["B"]}, {category: "A", categories: ["A", "B"]},
    {category: 1}, {rankingDimension: ["SKU"]}, {scope: "x".repeat(201)},
    {categories: Array.from({length: 51}, (_, index) => String(index))},
  ]) assert.throws(() => marketAiFilters(input));
  assert.deepEqual(marketAiFilters({category: "  A  "}).categories, ["A"]);
  assert.deepEqual(marketAiFilters({}).categories, []);
});

test("market AI returns bounded analytics instead of the entire dashboard", () => {
  const summary = {gmvCents: 10001, quantity: 12, productCount: 3};
  const source = {
    summary, dataRange: {startDate: "2026-08-01", endDate: "2026-08-31"},
    salesRevision: "1:abc", trend: Array.from({length: 30}, (_, index) => ({period: String(index)})),
    trendTotal: 30, trendTruncated: false,
    brandAnalysis: {items: Array.from({length: 30}, (_, index) => ({brand: String(index)})), cr3Bps: 1000, cr5Bps: 2000},
    priceBandSummary: Array(20).fill({gmvCents: 100}), subcategorySummary: Array(20).fill({quantity: 1}),
    items: Array(200).fill({title: "large product details".repeat(100)}),
    industryReport: "x".repeat(100_000), filters: {brands: Array(5000).fill("unrelated brand")},
  };
  const result = marketAiOverview(source);
  assert.deepEqual(result.summary, summary);
  assert.deepEqual(result.dataRange, source.dataRange);
  assert.equal(result.trend.length, 24);
  assert.equal(result.brandAnalysis.items.length, 10);
  assert.equal(result.priceBandSummary.length, 10);
  assert.equal(result.subcategorySummary.length, 10);
  assert.equal(result.trendTruncated, true);
  assert.equal(result.limits.brandItemsTruncated, true);
  assert.ok(!("items" in result) && !("industryReport" in result) && !("filters" in result));
  assert.ok(JSON.stringify(result).length < 40_000);
  assert.equal(source.brandAnalysis.items.length, 30);
  assert.equal(marketAiOverview({}).limits.brandItemsTruncated, false);
});
