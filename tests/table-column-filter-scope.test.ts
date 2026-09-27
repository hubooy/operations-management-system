import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import {
  scoreTableFilterControl,
  tableSummaryShowsPartialDataset,
} from "../lib/ui/table-column-filter";

test("full-scope controls match table headers by business dimension", () => {
  assert.ok(scoreTableFilterControl("健康状态", "健康状态筛选") > scoreTableFilterControl("健康状态", "搜索商品名称"));
  assert.ok(scoreTableFilterControl("库存类型 / 仓库", "仓库") >= 30);
  assert.equal(scoreTableFilterControl("库存类型 / 仓库", "库存类型"), 120);
  assert.ok(scoreTableFilterControl("品牌", "搜索货品、品牌、品类或仓库") >= 30);
  assert.ok(scoreTableFilterControl("榜单口径", "榜单范围") >= 30);
  assert.ok(scoreTableFilterControl("覆盖日期", "统计周期") >= 30);
  assert.equal(scoreTableFilterControl("库龄", "库龄仓库"), 0);
  assert.equal(scoreTableFilterControl("销售净额", "统计周期"), 0);
  assert.equal(scoreTableFilterControl("操作", "刷新"), 0);
  assert.equal(scoreTableFilterControl("SKU图", "按平台筛选货品"), 0);
  assert.equal(scoreTableFilterControl("主图", "搜索商品名称"), 0);
});

test("paging and server-truncated table summaries never use page-local values", () => {
  assert.equal(tableSummaryShowsPartialDataset("显示 50 / 22,386 行", 50), true);
  assert.equal(tableSummaryShowsPartialDataset("优先处理 4,195 项货品仓库", 50), true);
  assert.equal(tableSummaryShowsPartialDataset("当前页 20 条", 20), true);
  assert.equal(tableSummaryShowsPartialDataset("共 24 家店铺", 24), false);
});

test("global table filters route partial tables to full-scope business controls", async () => {
  const source = await readFile(new URL("../app/ui/table-column-filters.tsx", import.meta.url), "utf8");
  assert.match(source, /tableUsesPartialDataset/);
  assert.match(source, /table\.closest<HTMLElement>\("\.table-panel, article, section"\)/);
  assert.match(source, /externalFilterControls/);
  assert.match(source, /tableSummaryShowsPartialDataset/);
  assert.match(source, /不会再使用当前页临时选项/);
  assert.match(source, /打开\$\{partialDataset \? "全量" : ""\}列筛选/);
});

test("full-scope routing only activates explicit filter controls and fails closed", async () => {
  const source = await readFile(new URL("../app/ui/table-column-filters.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /"button\[aria-label\]"/);
  assert.doesNotMatch(source, /"button\[aria-expanded\]"/);
  assert.match(source, /button\.searchable-select-trigger\[aria-label\]/);
  assert.match(source, /button\.multi-filter-trigger\[aria-label\]/);
  assert.match(source, /\[data-column-filter-control='true'\]/);
  assert.match(source, /header\.dataset\.columnFilterHeader !== "true"/);
  assert.match(source, /table\.dataset\.columnFilterScope === "none"/);
  assert.match(source, /"data-column-filter-scope", "data-column-filter-total"/);
  assert.match(source, /prepareTable\(table\);[\s\S]{0,120}header\.dataset\.columnFilterHeader !== "true"/);
  assert.match(source, /role="radiogroup"/);
});

test("known bounded result tables declare an explicit safe column-filter scope", async () => {
  const [assistant, imports, market, marketAdmin, salesCategory, sales, shops, shared] = await Promise.all([
    "ai-assistant-view.tsx", "import-run-records-view.tsx", "market-view.tsx",
    "market-master-admin-panel.tsx", "sales-category-view.tsx", "sales-module-view.tsx", "shop-module-view.tsx", "module-view-shared.tsx",
  ].map((file) => readFile(new URL(`../app/${file}`, import.meta.url), "utf8")));
  assert.match(assistant, /data-column-filter-scope=\{artifact\.truncated \? "none" : "full"\}/);
  assert.match(imports, /import-history-panel[\s\S]{0,1800}data-column-filter-scope="none"/);
  assert.ok((market.match(/data-column-filter-scope="none"/g) ?? []).length >= 2);
  assert.match(market, /data-column-filter-scope=\{data\.truncated \? "none" : "full"\} data-column-filter-total=\{data\.totalMonths\}/);
  assert.ok((marketAdmin.match(/data-column-filter-scope="none"/g) ?? []).length >= 4);
  assert.match(salesCategory, /data-column-filter-scope=\{data\.pagination\.truncated \? "none" : "full"\}/);
  assert.match(sales, /dimensionPagination\?\.truncated === false \? "full" : "none"/);
  assert.match(sales, /data\.expensePagination\?\.truncated === false \? "full" : "none"/);
  const annual = await readFile(new URL("../app/finance-annual-progress-view.tsx", import.meta.url), "utf8");
  assert.match(annual, /page === 1 && !data\.pagination\.truncated \? "full" : "none"/);
  assert.match(shops, /dimensionPagination\?\.truncated === false \? "full" : "none"/);
  assert.match(shops, /summary\.trendTruncated === false[\s\S]{0,120}promotion\?\.dailyPagination\.truncated === false/);
  assert.match(shared, /groupPagination\?:/);
  assert.match(shared, /expensePagination\?:/);
  assert.match(shared, /shopPagination\?:/);
  assert.match(shared, /trendTruncated\?: boolean/);
});
