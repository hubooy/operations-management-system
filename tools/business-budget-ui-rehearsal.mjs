import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = resolve(import.meta.dirname, ".."), output = resolve(root, ".runtime/business-budget-ui");
await mkdir(output, { recursive: true });
const calculate = (file) => JSON.parse(execFileSync(resolve(root, ".runtime/test-venv/Scripts/python.exe"), ["-X", "utf8", resolve(root, "tools/business-budget-rehearsal.py"), ...(file ? [file] : [])], { encoding: "utf8", timeout: 15000 }));
const original = calculate();
await writeFile(resolve(output, "budget.json"), JSON.stringify(original));
await writeFile(resolve(output, "main.tsx"), `import React,{StrictMode} from 'react'; import {createRoot} from 'react-dom/client'; import View from '../../app/ai-business-budget'; import '../../app/ai-report-workbench.css'; import budget from './budget.json'; createRoot(document.getElementById('root')).render(<StrictMode><main style={{padding:20}}><p>预算试算合成演练</p><View reportId="report_1" question="合成预算分析" budget={budget} onCreated={id=>window.created=id}/></main></StrictMode>);`);
await writeFile(resolve(output, "index.html"), '<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/bundle.css"><style>*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;color:#163c2c}button{padding:10px}p{overflow-wrap:anywhere}</style><div id="root"></div><script src="/bundle.js"></script></html>');
await build({ entryPoints: [resolve(output, "main.tsx")], outfile: resolve(output, "bundle.js"), bundle: true, jsx: "automatic", platform: "browser", alias: { "@": root } });
const server = createServer(async (request, response) => { const name = ["/bundle.js", "/bundle.css"].includes(request.url) ? request.url.slice(1) : "index.html"; response.setHeader("content-type", name.endsWith("js") ? "text/javascript" : name.endsWith("css") ? "text/css" : "text/html"); response.end(await readFile(resolve(output, name))); });
let browser;
const errors = [], saves = [], checks = [];
try {
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(3120, "127.0.0.1", resolve); });
  browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.setDefaultTimeout(15000);
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, "http://127.0.0.1:3120");
    if (!url.pathname.startsWith("/api/")) return route.continue();
    assert.equal(route.request().method(), "POST");
    const body = route.request().postDataJSON();
    if (url.pathname.endsWith("/budget-preview")) {
      assert.equal(body.budgetPlan.totalBudgetCents, 15050);
      assert.equal(body.budgetPlan.scenarios[0].contributionMarginBps, null);
      const path = resolve(output, "preview-plan.json"); await writeFile(path, JSON.stringify(body.budgetPlan));
      return route.fulfill({ json: { previewOnly: true, reportId: "report_1", originalPlanDigest: original.planDigest, budget: calculate(path) } });
    }
    assert.equal(url.pathname, "/api/ai/business-reports");
    saves.push(body);
    if (saves.length === 1) return route.abort("failed");
    assert.deepEqual(saves[0], saves[1]);
    return route.fulfill({ json: { item: { id: "report_2" } } });
  });
  await page.goto("http://127.0.0.1:3120");
  await page.getByLabel("预算上限（元）", { exact: true }).fill("150.50");
  await page.getByLabel("假设贡献率（%，未知留空）", { exact: true }).first().fill("");
  await page.getByRole("button", { name: "按新参数试算", exact: true }).click();
  await page.getByRole("heading", { name: "新参数试算（尚未保存）" }).waitFor();
  assert.ok((await page.locator("body").innerText()).includes("已分配 140.50 元"));
  assert.ok((await page.locator("body").innerText()).includes("702.50"));
  checks.push("editable_cents_and_unknown_margin_use_server_calculation");
  await page.screenshot({ path: resolve(output, "budget.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: resolve(output, "mobile.png"), fullPage: true });
  checks.push("mobile_no_horizontal_overflow");
  await page.getByRole("button", { name: "保存新版本并重新分析", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "提交结果尚未确认" }).waitFor();
  assert.equal(await page.getByLabel("预算上限（元）", { exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "重试确认同一提交", exact: true }).click();
  await page.waitForFunction(() => window.created === "report_2");
  assert.equal(saves[0].previousReportId, "report_1");
  assert.equal(saves[0].dryRun, false);
  checks.push("unknown_save_reuses_exact_id_and_inputs_and_keeps_prior_report");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", checks, productionWrites: false, paidModelCalls: false }));
} finally { await browser?.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
