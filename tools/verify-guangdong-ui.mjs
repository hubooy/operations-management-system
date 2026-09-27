import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import { chromium } from "playwright-core";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, ".runtime/guangdong-ui");
await mkdir(output, { recursive: true });
await writeFile(resolve(output, "index.html"), '<html lang="zh-CN"><meta charset="utf-8"><div id="root"></div><script type="module" src="/.runtime/guangdong-ui/main.tsx"></script></html>');
await writeFile(resolve(output, "main.tsx"), `import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import View from '/app/inventory-guangdong-view';
import {emptyInventorySharedFilters} from '/app/inventory-filter-bar';
import '/app/globals.css';
function App(){const [filters,setFilters]=useState({...emptyInventorySharedFilters(),warehouses:['京东仓']}); return <main style={{padding:24}}><View canManage={!location.search.includes('viewer')} filters={filters} onFiltersChange={setFilters} onAskAi={()=>{}} /></main>}
createRoot(document.getElementById('root')).render(<App/>);`);
const server = await createServer({ configFile: false, root, plugins: [react()], resolve: { alias: { "@": root } }, server: { host: "127.0.0.1", port: 3108, strictPort: true } });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const errors = []; page.on("pageerror", (error) => errors.push(error.message));
  let version = "1:012345abcdef/sales:1/erp:1";
  const identity = { productCode: "00123", productName: "志高循环风扇", specification: "ZG-18", brand: "志高", category: "电风扇", supplier: "测试供应商", supplierSource: "库存快照" };
  let watch = [{ ...identity, active: true, notes: "关注补货" }];
  let cycle = { supplier: "测试供应商", leadDays: 10, bufferDays: 7 };
  const base = { ...identity, warehouse: "广东仓", notes: "关注补货", availableQuantity: 100, inTransitQuantity: 200, inventoryAgeDays: 20, unitCostCents: 5000, knownStockValueCents: 500000, costMissing: false, outbound7dQuantity: 70, outbound15dQuantity: 150, outbound30dQuantity: 300, turnoverDays: 10, latestOrderDate: "2026-09-09", replenishmentQuantity: 35, replenishmentRemainingQuantity: 20, replenishmentStockIncreaseQuantity: 15, replenishmentRemainingReason: "", latestReplenishmentOrderDate: "2026-09-08", supplierLeadDays: 10, supplierBufferDays: 7, planOperatorName: "运营甲", planBuyer: "采购甲", autoRisk: "urgent", autoRiskLabel: "紧急补货", autoRiskReasons: ["销售周转不超过生产周期"], inventoryStale: false };
  let itemSettings = { leadDays: 10, bufferDays: 7, leadDaysOverride: null, bufferDaysOverride: null, cycleSource: "供应商设置", operatorName: "运营甲", operatorNameOverride: null, operatorNameSource: "最新备货计划", buyer: "采购甲", buyerOverride: null, buyerSource: "最新备货计划", risk: "urgent", riskLabel: "紧急补货", riskReasons: ["销售周转不超过生产周期"], riskOverride: null, riskReasonOverride: null, riskSource: "系统判定" };
  const labels = { no_stock: "无可用库存", urgent: "紧急补货", warning: "补货预警", stale: "积压风险", unknown: "待完善/待观察", healthy: "健康" };
  const requests = [];
  await page.route("**/api/inventory/guangdong-monitor**", async (route) => {
    const request = route.request(); const url = new URL(request.url()); const suffix = url.pathname.split("guangdong-monitor")[1];
    requests.push({ suffix, search: url.search, method: request.method() });
    const body = request.postData() && request.headers()["content-type"]?.includes("json") ? request.postDataJSON() : null;
    let result;
    if (!suffix) {
      assert.equal(url.searchParams.has("warehouse"), false);
      const items = watch.filter((row) => row.active).map((row) => ({ ...base, ...row, ...itemSettings }));
      result = { version, hasInventory: true, watchCount: items.length, sync: { inventoryAsOf: "2026-09-08", inventoryAgeAsOf: "2026-09-08", salesThrough: "2026-09-07", inventoryStale: false }, filters: { brands: ["志高"], categories: ["电风扇"], suppliers: ["测试供应商"] }, metrics: { itemCount: items.length, availableQuantity: 100, inTransitQuantity: 200, knownStockValueCents: 500000, missingCostCount: 0, missingStockCount: 0 }, pagination: { page: 1, pageSize: 50, total: items.length, totalPages: 1 }, items, distribution: Object.entries(labels).map(([risk,label]) => ({ risk, label, itemCount: risk === "urgent" ? items.length : 0, quantity: risk === "urgent" ? 100 : 0, knownStockValueCents: risk === "urgent" ? 500000 : 0, itemRate: risk === "urgent" ? 1 : 0, quantityRate: risk === "urgent" ? 1 : 0, valueRate: risk === "urgent" ? 1 : 0 })), disclosures: [] };
    } else if (suffix === "/watchlist") result = { version, items: watch };
    else if (suffix === "/preview") result = { version, contentHash: "a".repeat(64), valid: true, counts: { added: 0, updated: body.rows.length, unchanged: 0 }, errors: [], items: body.rows.map((r) => ({ ...r, change: "updated" })) };
    else if (suffix === "/import") { watch = body.rows.map((r) => ({ ...identity, ...r })); version = "2:012345abcdef/sales:1/erp:1"; result = { status: "saved", version }; }
    else if (suffix === "/suppliers") {
      if (request.method() === "PATCH") { cycle = { ...cycle, leadDays: body.leadDays, bufferDays: body.bufferDays }; version = "3:012345abcdef/sales:1/erp:1"; }
      result = { version, items: [cycle] };
    } else if (suffix === "/items") {
      assert.equal(request.method(), "PATCH");
      itemSettings = {
        leadDays: body.leadDays ?? cycle.leadDays, bufferDays: body.bufferDays ?? cycle.bufferDays,
        leadDaysOverride: body.leadDays, bufferDaysOverride: body.bufferDays,
        cycleSource: body.leadDays === null ? "供应商设置" : "型号设置",
        operatorName: body.operatorName || "运营甲", operatorNameOverride: body.operatorName || null,
        operatorNameSource: body.operatorName ? "型号设置" : "最新备货计划",
        buyer: body.buyer || "采购甲", buyerOverride: body.buyer || null,
        buyerSource: body.buyer ? "型号设置" : "最新备货计划",
        risk: body.risk || "urgent", riskLabel: body.risk ? labels[body.risk] : "紧急补货",
        riskReasons: body.risk ? [`人工设置：${body.riskReason}`, "系统原判：紧急补货（销售周转不超过生产周期）"] : ["销售周转不超过生产周期"],
        riskOverride: body.risk || null, riskReasonOverride: body.riskReason || null, riskSource: body.risk ? "型号设置" : "系统判定",
      };
      version = "4:012345abcdef/sales:1/erp:1"; result = { status: "saved", version };
    } else if (suffix === "/products") result = { items: [identity] };
    else if (suffix === "/export" || suffix === "/template") {
      if (suffix === "/export") assert.equal(url.searchParams.get("version"), version);
      await route.fulfill({ status: 200, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: Buffer.from("ui-download-fixture") }); return;
    } else throw new Error("Unexpected API: " + suffix);
    await route.fulfill({ status: 200, json: result });
  });
  await page.goto("http://127.0.0.1:3108/.runtime/guangdong-ui/index.html");
  await page.getByRole("heading", { name: "风险库存健康分布" }).waitFor();
  assert.equal(await page.getByLabel("广东监控固定仓库").inputValue(), "广东仓");
  await page.getByRole("columnheader", { name: "规格编码" }).waitFor();
  await page.getByRole("columnheader", { name: "7 / 15 / 30日出库" }).waitFor();
  await page.getByRole("columnheader", { name: "销售周转 / 库龄" }).waitFor();
  await page.getByRole("columnheader", { name: "生产周期 / 安全天数" }).waitFor();
  await page.getByRole("columnheader", { name: "备货数量 / 下单剩余库存" }).waitFor();
  await page.getByText("剩余 20", { exact: true }).waitFor();
  assert.equal(await page.getByRole("columnheader", { name: "备货数量 / 最新下单时间" }).count(), 0);
  await page.getByRole("columnheader", { name: "运营负责人" }).waitFor();
  await page.getByRole("columnheader", { name: "采购负责人" }).waitFor();
  await page.locator("tbody td").filter({ hasText: "运营甲" }).first().waitFor();
  await page.locator("tbody td").filter({ hasText: "采购甲" }).first().waitFor();
  await page.locator("tbody td").filter({ hasText: "供应商设置" }).first().waitFor();
  assert.equal(await page.getByRole("columnheader", { name: "成本 / 货值" }).count(), 0);
  for (const label of ["广东仓可用库存", "在途库存", "已覆盖库存货值"]) {
    assert.equal(await page.getByText(label, { exact: true }).count(), 0);
  }
  const urgentCardText = await page.getByRole("button", { name: /^紧急补货/ }).innerText();
  assert.equal(urgentCardText.includes("100 件"), false);
  assert.equal(urgentCardText.includes("¥5,000"), false);
  await page.getByRole("button", { name: "00123编辑型号设置" }).click();
  assert.equal(await page.getByLabel("型号生产周期").inputValue(), "");
  assert.equal(await page.getByLabel("型号运营负责人").inputValue(), "");
  await page.getByLabel("型号生产周期").fill("22");
  await page.getByLabel("型号安全天数").fill("6");
  await page.getByLabel("型号运营负责人").fill("运营乙");
  await page.getByLabel("型号采购负责人").fill("采购乙");
  await page.getByLabel("型号风险判定").selectOption("healthy");
  await page.getByLabel("型号风险原因说明").fill("人工核实库存健康");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText("型号设置已保存并回查。", { exact: true }).waitFor();
  assert.equal(itemSettings.leadDays, 22); assert.equal(itemSettings.operatorName, "运营乙"); assert.equal(itemSettings.risk, "healthy");
  await page.getByRole("button", { name: "00123编辑型号设置" }).click();
  await page.getByLabel("型号生产周期").fill(""); await page.getByLabel("型号安全天数").fill("");
  await page.getByLabel("型号运营负责人").fill(""); await page.getByLabel("型号采购负责人").fill("");
  await page.getByLabel("型号风险判定").selectOption(""); await page.getByLabel("型号风险原因说明").fill("");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText("型号设置已保存并回查。", { exact: true }).waitFor();
  assert.equal(itemSettings.leadDays, 10); assert.equal(itemSettings.operatorName, "运营甲"); assert.equal(itemSettings.risk, "urgent");
  await page.screenshot({ path: resolve(output, "desktop.png"), fullPage: true });
  await page.getByRole("button", { name: /^紧急补货/ }).click();
  await page.waitForFunction(() => location.search.includes("inventoryGuangdongRisk=urgent"));
  await page.getByRole("button", { name: "监控清单", exact: true }).click();
  await page.getByRole("button", { name: "暂停", exact: true }).waitFor();
  await page.getByLabel("批量粘贴监控型号").fill("00123\t暂停\t休季");
  await page.getByRole("button", { name: "预览批量变更" }).click();
  await page.getByRole("button", { name: "确认保存清单" }).click();
  await page.getByText("监控清单已保存并回查。", { exact: true }).waitFor();
  assert.equal(watch[0].active, false); assert.equal(watch[0].productCode, "00123");
  await page.getByRole("button", { name: "供应商备货周期", exact: true }).click();
  await page.getByLabel("测试供应商生产周期").fill("21");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText("供应商周期已保存并回查。", { exact: true }).waitFor();
  assert.equal(cycle.leadDays, 21);
  await page.getByRole("button", { name: "监控清单", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出完整清单" }).click();
  assert.equal((await download).suggestedFilename(), "广东监控清单.xlsx");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(output, "mobile.png"), fullPage: true });
  await page.goto("http://127.0.0.1:3108/.runtime/guangdong-ui/index.html?viewer");
  await page.getByRole("button", { name: "监控清单", exact: true }).click();
  await page.getByLabel("筛选监控清单").waitFor();
  assert.equal(await page.getByRole("button", { name: "导入Excel" }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "预览批量变更" }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "00123编辑型号设置" }).count(), 0);
  assert.deepEqual(errors, []);
  await writeFile(resolve(output, "result.json"), JSON.stringify({ status: "passed", mockedApi: true, requests: requests.length, checks: ["fixed-warehouse", "risk-url", "item-overrides-risk-reason-and-fallback", "paste-preview-commit", "supplier-cycle", "versioned-export", "viewer-read-only", "desktop-mobile-render"] }, null, 2));
  console.log("Guangdong UI checks passed: " + output);
} finally {
  await browser?.close();
  await server.close();
}
