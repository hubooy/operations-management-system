// Synthetic loopback-only component acceptance. No production or model calls.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { createHash } from "node:crypto";
import { build } from "esbuild";
import { chromium } from "playwright-core";
const root = process.cwd(), out = path.resolve(process.argv[2] || ".runtime/batch24-budget-builder-ui");
await fs.mkdir(out, { recursive: true });
const hash = "a".repeat(64), account = "b".repeat(64);
const run = { id: "evidence-builder", version: 4, status: "sealed", plan: { schemaVersion: "business-evidence-v2", sourceCount: 2, catalogDigest: hash }, sources: { promotion: { complete: true }, sales: { complete: true } } };
const source = { key: "promotion", ordinal: 1, domain: "netshop", query: { platform: "京东", shop: "合成精确店铺", dataset: "promotion", window: "current", startDate: "2026-08-01", endDate: "2026-08-31" } };
const other = { key: "sales", ordinal: 2, domain: "sales", query: { platform: "京东", shop: "合成精确店铺", channel: "精确销售渠道", window: "current", startDate: "2026-08-01", endDate: "2026-08-31" } };
const canonical = value => Array.isArray(value) ? "["+value.map(canonical).join(",")+"]" : value !== null && typeof value === "object" ? "{"+Object.keys(value).sort().map(k => JSON.stringify(k)+":"+canonical(value[k])).join(",")+"}" : JSON.stringify(value);
const rows = [0, 1].map(i => ({ id: String(i+1).repeat(64), rowIndex: i, entity: { sku: "合成SKU"+(i+1) }, dimensionMissing: false, metrics: Object.fromEntries(Object.entries({ spendCents: 10000, clicks: 100, reportedOrderLines: 10, reportedGmvCents: 50000 }).map(([key, value]) => [key, { value, missingRows: 0 }])) }));
let delayPreview = false, corruptBinding = false;
const requests = [], results = [], pageErrors = [];
const entry = path.join(out, "entry.tsx");
await fs.writeFile(entry, `import React,{useState}from'react';import{createRoot}from'react-dom/client';import Builder from ${JSON.stringify(path.join(root, "app/ai-business-budget-builder.tsx"))};import ${JSON.stringify(path.join(root,"app/ai-business-workbench.css"))};function Harness(){const[run,setRun]=useState(${JSON.stringify(run)}),[actor,setActor]=useState(${JSON.stringify(account)});window.switchBudgetActor=()=>setActor('c'.repeat(64));window.switchBudgetRun=()=>setRun({...run,id:'evidence-builder-two'});return <div className="business-workbench"><Builder run={run} principalKey={actor} onSubmit={(plan,dryRun)=>{window.submitted={plan,dryRun};document.getElementById('result').textContent=JSON.stringify(window.submitted)}}/></div>}createRoot(document.getElementById('root')).render(<Harness/>);`);
await build({ entryPoints: [entry], bundle: true, outfile: path.join(out, "app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root, "tsconfig.json") });
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost"), send = data => { res.setHeader("content-type", "application/json"); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith("/api/")) {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
      requests.push({ path: url.pathname, query: url.search, method: req.method, body });
      const match = url.pathname.match(/^\/api\/ai\/business-evidence\/(evidence-builder(?:-two)?)\/(sources|budget-targets|budget-preview)$/);
      assert.ok(match, "No unexpected API or model request");
      const binding = { evidenceRunId: match[1], evidenceVersion: 4, evidencePlanDigest: hash, catalogDigest: hash, sealedDigest: hash };
      if (match[2] === "sources") return send({ schemaVersion: "business-evidence-directory-page-v2", runId: match[1], evidenceVersion: 4, catalogDigest: hash, offset: 0, total: 2, returned: 2, nextOffset: null, items: [source, other] });
      if (match[2] === "budget-targets") {
        const offset = Number(url.searchParams.get("offset"));
        if (corruptBinding) binding.sealedDigest = "d".repeat(64);
        return send({ schemaVersion: "business-budget-targets-v1", evidenceBinding: binding, sourceKey: "promotion", dimension: url.searchParams.get("dimension"), sourceMetadata: { coverage: { status: "dates_present" } }, rows: rows.slice(offset, offset+1), pagination: { offset, limit: 20, total: 2, hasMore: offset === 0, nextOffset: offset === 0 ? 1 : null } });
      }
      assert.deepEqual(body.evidenceBinding, binding);
      const plan = body.budgetPlan, each = Math.floor((plan.totalBudgetCents-plan.reserveCents)/plan.targets.length);
      const budget = { schemaVersion: "business-budget-v1", plan, planDigest: createHash("sha256").update(canonical(plan)).digest("hex"), evidenceRunId: match[1], evidenceVersion: 4, evidencePlanDigest: hash,
        allocation: { totalBudgetCents: plan.totalBudgetCents, reservedCents: plan.reserveCents, allocatedCents: each*plan.targets.length, unallocatedCents: plan.totalBudgetCents-plan.reserveCents-each*plan.targets.length },
        scenarios: plan.scenarios.map(assumptions => ({ assumptions, summary: { projectedAttributedGmvCents: 50000, assumedContributionAfterAdCents: null, unavailableTargets: 0, mixedReportingBases: false, byReportingBasis: [] }, rows: plan.targets.map(target => ({ ...target, entity: rows[target.rowIndex].entity, budgetCents: each, status: "assumption_scenario", reviewAfterSpendCents: 1000, ownerRole: target.ownerRole })) })) };
      if (delayPreview) { delayPreview = false; await new Promise(resolve => setTimeout(resolve, 700)); }
      return send({ schemaVersion: "business-budget-preview-v1", previewOnly: true, evidenceBinding: binding, budget });
    }
    if (["/app.js", "/app.css"].includes(url.pathname)) { res.setHeader("content-type", url.pathname.endsWith("css") ? "text/css" : "text/javascript"); res.end(await fs.readFile(path.join(out, url.pathname.slice(1)))); return; }
    res.setHeader("content-type", "text/html;charset=utf-8"); res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px"><main id="root"></main><pre id="result" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre><script src="/app.js"></script></body></html>');
  } catch (error) { res.statusCode = 500; res.end(JSON.stringify({ error: String(error) })); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1200, height: 950 } });
page.on("pageerror", error => pageErrors.push(String(error)));
const simulate = () => page.getByRole("button", { name: "创建预算模拟分析（不调用模型）", exact: true });
const calc = () => page.getByRole("button", { name: "免费试算预算", exact: true });
const check = async (name, fn) => { await fn(); results.push({ name, passed: true }); console.log(JSON.stringify(results.at(-1))); };
const choose = async (two = true) => {
  await page.getByRole("button", { name: "选择来源 promotion", exact: true }).click(); await page.getByLabel("预算聚合维度", { exact: true }).selectOption("sku");
  await page.getByRole("button", { name: "选择目标 1", exact: true }).click();
  if (two) { await page.getByRole("button", { name: "预算目标下一页", exact: true }).click(); await page.getByRole("button", { name: "选择目标 2", exact: true }).click(); }
};
const fill = async (count = 2) => {
  const values = { "总预算（元）": "100", "预留预算（元）": "10", "规划天数（1–93）": "30", "观察天数": "7", "消耗复核比例（%）": "20", "最低点击数": "50", "最低订单行数": "3", "情景1 情景名称": "明确假设", "情景1 点击成本系数（10–300%）": "100", "情景1 订单行率系数（10–300%）": "110", "情景1 订单行价值系数（10–300%）": "100" };
  for (let i = 1; i <= count; i++) Object.assign(values, { [`目标${i} 分配权重（1–10000）`]: "1", [`目标${i} 最低预算（元）`]: "0", [`目标${i} 最高预算（元）`]: "100", [`目标${i} 负责人角色`]: "运营", [`目标${i} 最低产出比`]: "2" });
  for (const [name, value] of Object.entries(values)) await page.getByLabel(name, { exact: true }).fill(value);
};
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await check("all_business_values_blank_and_other_sources_explained", async () => {
    assert.equal(await page.locator("input").evaluateAll(nodes => nodes.every(node => node.value === "")), true);
    assert.equal(await page.getByRole("button", { name: "选择来源 sales", exact: true }).isDisabled(), true);
    await choose(); await calc().click(); await page.getByRole("alert").filter({ hasText: "总预算" }).waitFor();
    assert.equal(requests.filter(r => r.path.endsWith("budget-preview")).length, 0); assert.equal(await simulate().isDisabled(), true);
  });
  await check("short_page_preserves_two_exact_ids_and_explicit_plan", async () => {
    await fill(); await calc().click(); await page.getByRole("region", { name: "当前预算试算" }).waitFor();
    assert.match(await page.getByRole("region", { name: "当前预算试算" }).innerText(), /按输入假设测算，不是业绩承诺/);
    await simulate().click(); const result = await page.evaluate(() => window.submitted);
    assert.deepEqual(result.plan.targets.map(t => t.rowId), rows.map(r => r.id)); assert.equal(result.plan.scenarios[0].contributionMarginBps, null); assert.equal(result.dryRun, true);
    assert.equal(requests.some(r => r.query.includes("offset=1")), true);
  });
  await check("editing_invalidates_preview_and_delayed_success_cannot_unlock", async () => {
    await page.getByLabel("总预算（元）", { exact: true }).fill("101"); assert.equal(await simulate().isDisabled(), true);
    delayPreview = true; await calc().click(); await page.getByLabel("总预算（元）", { exact: true }).fill("102");
    await page.waitForTimeout(850); assert.equal(await simulate().isDisabled(), true); assert.equal(await page.getByRole("region", { name: "当前预算试算" }).count(), 0);
  });
  await check("changed_seal_on_later_page_rejected", async () => {
    corruptBinding = true; await page.getByRole("button", { name: "预算目标上一页", exact: true }).click(); await page.getByRole("alert").filter({ hasText: "封存证据绑定已变化" }).waitFor();
    assert.equal(await page.getByRole("button", { name: "选择目标 1", exact: true }).count(), 0); corruptBinding = false;
  });
  await check("account_switch_during_preview_clears_form_and_ignores_late_result", async () => {
    delayPreview = true; await calc().click(); await page.evaluate(() => window.switchBudgetActor()); await page.waitForTimeout(850);
    assert.equal(await page.getByLabel("总预算（元）", { exact: true }).inputValue(), ""); assert.equal(await simulate().isDisabled(), true); assert.equal(await page.getByRole("region", { name: "当前预算试算" }).count(), 0);
  });
  await check("run_switch_during_preview_isolates_old_success", async () => {
    await choose(false); await fill(1); delayPreview = true; await calc().click(); await page.evaluate(() => window.switchBudgetRun()); await page.waitForTimeout(850);
    assert.equal(await page.getByLabel("总预算（元）", { exact: true }).inputValue(), ""); assert.equal(await simulate().isDisabled(), true);
  });
  await check("mobile_input_and_target_table_do_not_overflow", async () => {
    await choose(false); await fill(1); await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: path.join(out, "builder-mobile.png"), fullPage: true });
  });
  assert.deepEqual(pageErrors, []);
  await fs.writeFile(path.join(out, "evidence.json"), JSON.stringify({ passed: true, syntheticOnly: true, cases: results, pageErrors, requestCount: requests.length }, null, 2));
} catch (error) {
  await fs.writeFile(path.join(out, "failure.json"), JSON.stringify({ error: String(error), cases: results, pageErrors, requests, dom: await page.locator("body").innerText() }, null, 2)); throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
