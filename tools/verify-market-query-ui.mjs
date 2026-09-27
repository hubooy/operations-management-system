import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import { chromium } from "playwright-core";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, ".runtime/market-query-ui");
await mkdir(output, { recursive: true });
await writeFile(resolve(output, "index.html"), '<html lang="zh-CN"><meta charset="utf-8"><div id="root"></div><script type="module" src="/.runtime/market-query-ui/main.tsx"></script></html>');
await writeFile(resolve(output, "main.tsx"), `import React,{useState} from 'react';
import {createRoot} from 'react-dom/client'; import View from '/app/market-view'; import '/app/globals.css';
function App(){const [view,setView]=useState('ranking'); const [period,setPeriod]=useState(['2025-01-01','2026-09-15']);
return <main style={{padding:24}}><p>演示预览 · 合成数据 · 仅查询</p><View customStartDate={period[0]} customEndDate={period[1]} currentUser={{email:'fixture@example.invalid',role:'analyst'}} moduleView={view} onModuleViewChange={setView} onApplyPeriod={(s,e)=>setPeriod([s,e])}/></main>}
createRoot(document.getElementById('root')).render(<App/>);`);
const server = await createServer({ configFile: false, root, plugins: [react()],
  cacheDir: resolve(output, "vite-cache"),
  optimizeDeps: { include: ["react", "react-dom", "react-dom/client"] },
  resolve: { alias: { "@": root } }, server: { host: "127.0.0.1", port: 3108, strictPort: true } });
const filters = { categories: [{ value: "合成类目", count: 250002 }], scopes: [{ value: "全部", count: 250002 }],
  brands: [], rankingDimensions: [{ value: "SKU", count: 250002 }], operationModes: [], subcategories: [], priceBands: [] };
function ranking(page, query) {
  return { view: "ranking", salesRevision: "1:1", summary: { activeSkuCount: 250002, pendingAiCount: 250002 },
    items: Array.from({ length: 20 }, (_, i) => ({ id: (page-1)*20+i+1, periodStart: "2026-08-01", periodEnd: "2026-08-31",
      category: "合成类目", scope: "全部", rankingDimension: "SKU", rank: i+1, skuCode: `fixture-${(page-1)*20+i+1}`,
      productName: `${query || '合成商品'}-${(page-1)*20+i+1}`, brand: "合成品牌", operationMode: "POP", periodCount: 3,
      subcategory: "合成细分类目", priceCents: 10000, marketPriceCents: null, candidatePriceCents: 10000,
      averageTransactionPriceCents: 10000, quantity: 2, gmvCents: 20000, ownSalesCents: 0, imageUrl: "", productUrl: "https://item.jd.com/10000.html",
      rankChange: null, previousRank: null, discountBps: null, marketPriceSource: "missing" })),
    filters, pagination: { page, pageSize: 20, total: 250002, pageCount: 12501 },
    dataRange: { startDate: "2025-01-01", endDate: "2026-08-31" }, imageCache: { total: 0, cached: 0, pending: 0, failed: 0 },
    industryReport: { definition: { selectedRankingDimensions: ["SKU"] }, dataQuality: { rankingDimensionCount: 1 } } };
}
let browser;
const requests = [], errors = [], checks = [];
try {
  await server.listen();
  browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", (error) => errors.push(error.message));
  let failRanking = true, failFilters = false;
  // Every request remains on this synthetic server; no production/network API.
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url.pathname + url.search);
    assert.equal(route.request().method(), "GET");
    if (url.pathname === "/api/market/filters") return route.fulfill({ status: failFilters ? 503 : 200, json: failFilters ? { error: "合成筛选服务中断" } : { filters } });
    if (url.pathname === "/api/market/overview") {
      assert.equal(url.searchParams.get("includeFilterOptions"), "false", "ranking must not repeat global facet aggregation");
      if (url.searchParams.get("view") === "full" || failRanking) return route.fulfill({ status: 413, json: { error: "市场分析范围过大，请缩小日期或筛选范围" } });
      if (url.searchParams.get("q") === "旧查询") await new Promise((resolve) => setTimeout(resolve, 800));
      return route.fulfill({ json: ranking(Number(url.searchParams.get("page") || 1), url.searchParams.get("q")) }).catch(() => {});
    }
    return route.fulfill({ status: 503, json: { error: "合成设置接口未就绪" } });
  });
  await page.goto("http://127.0.0.1:3108/.runtime/market-query-ui/index.html");
  await page.getByRole("alert").filter({ hasText: "范围过大" }).waitFor();
  assert.equal(await page.getByRole("tab").count(), 4);
  assert.equal(await page.getByLabel("市场开始日期").isVisible(), true);
  assert.equal(await page.getByLabel("搜索商品标题或 SKU").isVisible(), true);
  checks.push("initial_413_preserves_tabs_and_filters");
  await page.screenshot({ path: resolve(output, "recovery.png"), fullPage: true });
  await page.getByRole("tab", { name: /系统和 AI 设置/ }).click();
  await page.locator("#market-panel-settings").waitFor();
  checks.push("settings_accessible_after_ranking_failure");
  failRanking = false;
  await page.getByRole("tab", { name: /商品榜单/ }).click();
  await page.getByRole("link", { name: "合成商品-1", exact: true }).waitFor();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.getByRole("link", { name: "合成商品-21", exact: true }).waitFor();
  assert.equal(await page.locator(".market-ranking-table tbody tr").count(), 20);
  assert.equal(await page.getByRole("link", { name: "合成商品-1", exact: true }).count(), 0);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.getByRole("link", { name: "合成商品-1", exact: true }).waitFor();
  checks.push("pagination_replaces_rows_and_supports_previous");
  const search = page.getByLabel("搜索商品标题或 SKU");
  const oldRequest = page.waitForRequest((request) => new URL(request.url()).searchParams.get("q") === "旧查询");
  await search.fill("旧查询"); await oldRequest;
  await search.fill("新查询");
  assert.equal(await page.locator(".market-ranking-table").count(), 0);
  await page.getByRole("link", { name: "新查询-1", exact: true }).waitFor();
  await page.waitForTimeout(900);
  assert.equal(await page.getByRole("link", { name: "旧查询-1", exact: true }).count(), 0);
  checks.push("new_filter_hides_old_rows_and_rejects_late_response");
  await page.getByLabel("市场开始日期").fill("2026-08-01");
  await page.getByLabel("市场结束日期").fill("2026-08-31");
  const periodRequest = page.waitForRequest((request) => new URL(request.url()).searchParams.get("startDate") === "2026-08-01");
  await page.getByRole("button", { name: "应用日期" }).click(); await periodRequest;
  await page.getByRole("link", { name: "新查询-1", exact: true }).waitFor();
  checks.push("inline_period_recovery_applies_global_period");
  await page.getByRole("tab", { name: /行业汇报/ }).click();
  await page.getByRole("alert").filter({ hasText: "范围过大" }).waitFor();
  assert.equal(await page.locator(".market-ranking-table").count(), 0);
  await page.getByRole("tab", { name: /商品榜单/ }).click();
  await page.getByRole("link", { name: "新查询-1", exact: true }).waitFor();
  checks.push("report_failure_does_not_block_ranking");
  failFilters = true;
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "合成筛选服务中断" }).waitFor();
  assert.equal(await search.isVisible(), true);
  failFilters = false;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.getByRole("alert").filter({ hasText: "合成筛选服务中断" }).waitFor({ state: "detached" });
  checks.push("independent_filter_failure_and_online_read_recovery");
  await page.screenshot({ path: resolve(output, "ranking.png"), fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile(resolve(output, "result.json"), JSON.stringify({ status: "passed", checks, requests: requests.length, pageErrors: errors }, null, 2));
  console.log(JSON.stringify({ status: "passed", checks, output }));
} finally {
  await browser?.close();
  await server.close();
}
