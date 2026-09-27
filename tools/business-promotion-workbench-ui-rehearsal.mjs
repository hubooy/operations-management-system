// Real workbench against a synthetic loopback API. No model or production call.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-promotion-workbench-ui");
await fs.mkdir(directory, { recursive: true });
await fs.writeFile(path.join(directory, "entry.tsx"), `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root, "app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')).render(<Workbench onReportCreated={id=>{document.getElementById('report-result').textContent=id}}/>);`);
await build({ entryPoints: [path.join(directory, "entry.tsx")], bundle: true, outfile: path.join(directory, "app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root, "tsconfig.json") });
const principalA = "a".repeat(64), principalB = "b".repeat(64), digest = "c".repeat(64);
let principal = principalA, enabled = false, version = 4, malformed = false, dropReply = false, delayDetail = 0;
const requests = [], created = [], cases = [], pageErrors = [], outside = [];
const base = { platform: "京东", shop: "合成精确店", dataset: "promotion", startDate: "2026-08-01", endDate: "2026-08-31" };
const entries = [{ ordinal: 1, key: "ads", domain: "netshop", query: { ...base, window: "current" } },
  { ordinal: 2, key: "ads-previous", domain: "netshop", query: { ...base, window: "previous" } }];
const status = { id: "evidence-promotion", question: "合成词货诊断", status: "sealed", version: 4,
  collection: { status: "sealed" }, createdAt: "2026-09-24T00:00:00Z", storedBytes: 2024,
  sourceCount: 2, completedSources: 2, rowCount: 10 };
function detail() {
  const choice = { sourceKey: "ads", platform: "京东", shop: base.shop, startDate: base.startDate, endDate: base.endDate,
    baselineChoices: [{ sourceKey: "ads-previous", window: "previous" }] };
  if (malformed) choice.baselineChoices[0].window = "current";
  return { ...status, version, workbenchAnalysisEnabled: true, screeningSupported: true, mappingSupported: false, budgetSupported: false,
    promotionSupported: enabled, promotionChoices: enabled ? [choice] : [],
    plan: { schemaVersion: "business-evidence-v2", sourceCount: 2, catalogDigest: digest,
      analysisRequest: { question: status.question }, collector: { version: 1, surface: "business_collection", pageSize: 100 } },
    sources: { ads: { pageCount: 1, rowCount: 5, complete: true }, "ads-previous": { pageCount: 1, rowCount: 5, complete: true } } };
}
const server = http.createServer(async (req, res) => { try {
  const url = new URL(req.url, "http://localhost");
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json", "cache-control": "no-store" }); res.end(JSON.stringify(value)); };
  if (url.pathname.startsWith("/api/")) {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString(), body = raw ? JSON.parse(raw) : null;
    requests.push({ path: url.pathname, method: req.method, body, raw, principal });
    if (url.pathname === "/api/ai/business-evidence" && req.method === "GET")
      return send({ principalKey: principal, items: principal === principalA ? [{ ...status, version }] : [], pagination: { total: principal === principalA ? 1 : 0 } });
    if (url.pathname === "/api/ai/business-evidence/evidence-promotion" && req.method === "GET") {
      if (principal !== principalA) return send({ error: "不存在" }, 404);
      const value = { item: detail(), reports: created.map(item => ({ id: item.id, workflowId: item.id, status: "queued", createdAt: status.createdAt })),
        reportsPagination: { hasMore: false }, principalKey: principal };
      const delay = delayDetail; delayDetail = 0; if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      return send(value);
    }
    if (url.pathname === "/api/ai/business-evidence/evidence-promotion/sources") {
      const offset = Number(url.searchParams.get("offset")), limit = Number(url.searchParams.get("limit"));
      return send({ schemaVersion: "business-evidence-directory-page-v2", runId: status.id, evidenceVersion: version,
        catalogDigest: digest, offset, total: 2, returned: entries.slice(offset, offset+limit).length, nextOffset: null,
        items: entries.slice(offset, offset+limit) });
    }
    if (url.pathname === "/api/ai/business-reports" && req.method === "POST") {
      assert.equal(body.expectedPrincipalKey, principal);
      if (body.analysisMode === "screening-promotion-v1" && !enabled) return send({ error: "词货五角色分析尚未启用" }, 409);
      const prior = created.find(item => item.body.clientRequestId === body.clientRequestId);
      if (prior) assert.equal(prior.raw, raw);
      else created.push({ id: body.analysisMode === "screening-promotion-v1" ? "report-promotion" : `report-${created.length+1}`, body, raw });
      if (dropReply) { dropReply = false; return send({ error: "合成已接收但回执未知" }, 503); }
      return send({ item: { id: created.find(item => item.body.clientRequestId === body.clientRequestId).id } });
    }
    return send({ error: "unexpected synthetic route" }, 404);
  }
  if (["/app.js", "/app.css"].includes(url.pathname)) {
    res.setHeader("content-type", url.pathname.endsWith("css") ? "text/css" : "text/javascript");
    return res.end(await fs.readFile(path.join(directory, url.pathname.slice(1))));
  }
  res.setHeader("content-type", "text/html;charset=utf-8");
  res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><div id="root"></div><div id="report-result"></div><script src="/app.js"></script></body></html>');
} catch (error) { pageErrors.push(String(error)); res.statusCode = 500; res.end(JSON.stringify({ error: String(error) })); } });
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", error => pageErrors.push(String(error)));
await page.route("**/*", route => { if (new URL(route.request().url()).origin !== origin) { outside.push(route.request().url()); return route.abort(); } return route.continue(); });
const button = name => page.getByRole("button", { name, exact: true });
const region = () => page.getByRole("region", { name: "选中任务详情" });
const check = async (name, fn) => { await fn(); cases.push(name); console.log(JSON.stringify({ name, passed: true })); };
const runButton = () => page.getByRole("button", { name: /合成词货诊断/ });
const selectRun = async () => { await runButton().click(); await region().getByText("来源总数 2", { exact: false }).waitFor(); };
const chooseMode = async () => { await page.getByLabel("分析方式", { exact: true }).selectOption("screening-promotion-v1"); };
const chooseSource = async (baseline = true) => {
  await page.getByLabel("本期京东推广来源", { exact: true }).selectOption("ads");
  if (baseline) await page.getByLabel("可选比较基期", { exact: true }).selectOption("ads-previous");
};
const reset = async () => { principal = principalA; enabled = false; version = 4; malformed = false; dropReply = false; delayDetail = 0;
  await page.evaluate(() => sessionStorage.clear()); await page.reload(); await runButton().waitFor(); };
try {
  await page.goto(origin); await runButton().waitFor();
  await check("rollout_off_disables_exact_mode_without_post", async () => {
    await selectRun(); assert.equal(await page.getByRole("option", { name: "京东推广关键词 × SKU 专项筛查" }).evaluate(option => option.disabled), true);
    assert.match(await region().innerText(), /京东推广专项尚未开放/);
    assert.equal(requests.filter(item => item.path === "/api/ai/business-reports").length, 0);
  });
  await check("sealed_server_choices_bind_exact_main_and_baseline_to_existing_create", async () => {
    enabled = true; version++; await button("刷新选中任务").click();
    await page.waitForFunction(() => document.querySelector('select[aria-label="分析方式"] option[value="screening-promotion-v1"]')?.disabled === false);
    await chooseMode();
    assert.equal(await button("启动词货五角色专项分析（调用模型）").isDisabled(), true);
    await chooseSource();
    assert.match(await region().innerText(), /创建成功仅表示任务排队/);
    await button("启动词货五角色专项分析（调用模型）").click();
    await page.locator("#report-result").filter({ hasText: "report-promotion" }).waitFor();
    const body = requests.filter(item => item.path === "/api/ai/business-reports" && item.method === "POST").at(-1).body;
    assert.equal(body.analysisMode, "screening-promotion-v1"); assert.equal(body.sourceKey, "ads");
    assert.equal(body.baselineKey, "ads-previous"); assert.equal(body.dryRun, false);
    assert.equal(body.expectedPrincipalKey, principalA);
    await button("刷新选中任务").click(); await page.getByRole("button", { name: /打开报告 · 待执行/ }).waitFor();
  });
  await check("version_change_invalidates_previous_choice", async () => {
    version++; await button("刷新选中任务").click();
    await region().getByText(`版本 ${version}`, { exact: false }).first().waitFor();
    await chooseMode();
    assert.equal(await page.getByLabel("本期京东推广来源", { exact: true }).inputValue(), "");
    assert.equal(await button("启动词货五角色专项分析（调用模型）").isDisabled(), true);
  });
  await check("malformed_source_detail_blocks_launch_and_keeps_legacy_available", async () => {
    malformed = true; await button("刷新选中任务").click();
    await region().getByRole("alert").filter({ hasText: "推广专项来源详情无效" }).waitFor();
    assert.equal(await button("启动词货五角色专项分析（调用模型）").isDisabled(), true);
    malformed = false; await button("刷新选中任务").click(); await region().getByText("来源总数 2", { exact: false }).waitFor();
    await page.getByLabel("分析方式", { exact: true }).selectOption("screening-v1");
    await button("启动完整筛查与多 Agent 分析（调用模型）").click();
    await page.locator("#report-result").filter({ hasText: "report-2" }).waitFor();
    assert.equal(requests.filter(item => item.path === "/api/ai/business-reports").at(-1).body.analysisMode, "screening-v1");
    await page.getByLabel("分析方式", { exact: true }).selectOption("legacy");
    await button("模拟分析（不调用模型）").click();
    await page.locator("#report-result").filter({ hasText: "report-3" }).waitFor();
    assert.equal("analysisMode" in requests.filter(item => item.path === "/api/ai/business-reports").at(-1).body, false);
  });
  await check("unknown_reply_retries_same_frozen_promotion_body", async () => {
    await reset(); enabled = true; await selectRun(); await chooseMode(); await chooseSource(false);
    dropReply = true; await button("启动词货五角色专项分析（调用模型）").click();
    await page.getByText("合成已接收但回执未知", { exact: false }).waitFor();
    const first = requests.filter(item => item.path === "/api/ai/business-reports" && item.method === "POST").at(-1).raw;
    await page.reload(); await button("确认并重试同一次提交").click();
    await page.locator("#report-result").filter({ hasText: "report-promotion" }).waitFor();
    assert.equal(requests.filter(item => item.path === "/api/ai/business-reports" && item.method === "POST").at(-1).raw, first);
  });
  await check("principal_switch_and_late_detail_clear_mode_and_choice", async () => {
    await reset(); enabled = true; await selectRun(); await chooseMode(); await chooseSource();
    delayDetail = 500; await button("刷新选中任务").click(); principal = principalB;
    await button("刷新任务列表").click(); await page.getByTestId("write-error").filter({ hasText: "账号已变化" }).waitFor();
    await page.waitForTimeout(600); assert.equal(await region().count(), 0);
    assert.equal(requests.filter(item => item.path === "/api/ai/business-reports" && item.principal === principalB).length, 0);
  });
  await check("mobile_390px_exact_selection_no_overflow", async () => {
    await reset(); enabled = true; await page.setViewportSize({ width: 390, height: 844 });
    await selectRun(); await chooseMode(); await chooseSource();
    await page.screenshot({ path: path.join(directory, "mobile.png"), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  });
  assert.deepEqual(pageErrors, []); assert.deepEqual(outside, []);
  await fs.writeFile(path.join(directory, "evidence.json"), JSON.stringify({ passed: cases.length, cases, pageErrors, externalRequests: outside,
    operations: { production: false, modelCalls: false } }, null, 2));
} catch (error) { await fs.writeFile(path.join(directory, "failure.json"), JSON.stringify({ error: String(error), cases,
  pageErrors, dom: await page.locator("body").innerText() }, null, 2)); throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
