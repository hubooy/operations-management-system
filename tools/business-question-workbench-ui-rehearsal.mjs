// The real workbench and directory picker against a synthetic loopback API.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-question-workbench-ui");
await fs.mkdir(directory, { recursive: true });
await fs.writeFile(path.join(directory, "entry.tsx"), `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root, "app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')).render(<Workbench onReportCreated={()=>{}}/>);`);
await build({ entryPoints: [path.join(directory, "entry.tsx")], bundle: true, outfile: path.join(directory, "app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root, "tsconfig.json") });
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`
    : JSON.stringify(value);
const sha = value => createHash("sha256").update(canonical(value)).digest("hex");
const principalA = "a".repeat(64), principalB = "b".repeat(64);
let principal = principalA, delayOptions = 0, dropEvidence = false;
const requests = [], created = [], checks = [], errors = [], external = [];
const identity = { platform: "京东", shop: "合成范围店", dataset: "promotion" };
function optionPage(query) {
  const revision = "7:abcdef012345", option = { optionKey: sha({ domain: "netshop", identity }), identity,
    source: "jd_promotion", sourceDataset: "ad", dateMetadata: { kind: "published_import_envelope", firstDate: "2026-07-01", lastDate: "2026-09-20", snapshotDate: null, coverageVerified: false },
    provenance: { kind: "completed_import_metadata", revision, meaning: "historically_published_not_current_fact_coverage" } };
  const items = !query.platform || query.platform === identity.platform ? [option] : [];
  const page = { schemaVersion: "business-analysis-options-v1", domain: "netshop", revision, query, queryDigest: sha(query), items,
    pagination: { returned: items.length, limit: 20, hasMore: false, nextCursor: null }, limitations: ["合成历史包络不证明完整覆盖"] };
  return { ...page, pageDigest: sha(page) };
}
function preview(body) {
  const coverage = [];
  for (const shop of body.shops) for (const dataset of shop.datasets) for (const window of dataset === "master" ? ["current"] : body.windows)
    coverage.push({ domain: "netshop", query: { platform: shop.platform, shop: shop.shop, dataset, window, startDate: body.startDate, endDate: body.endDate },
      status: "planned", availability: "not_collected", reason: "合成计划，未采集", sourceKey: `source-${coverage.length}` });
  const sources = coverage.map(item => ({ key: item.sourceKey, domain: item.domain, query: item.query })), fits = sources.length > 0 && sources.length <= 48;
  return { schemaVersion: "business-plan-preview-v2", principalKey: principal, canCollect: fits, planDigest: fits ? sha(body) : null,
    catalogDigest: fits ? sha(sources) : null, request: body, coverage,
    capacity: { sourceCount: sources.length, maxSources: 48, planBytes: fits ? 4000 : null, maxPlanBytes: 16000,
      workflowBytes: fits ? 5000 : null, maxWorkflowBytes: 8000, queryBytes: 500, maxQueryBytes: 4096,
      directoryQueryBytes: sources.length * 500, maxDirectoryQueryBytes: 131072, factBytes: 67108864, factPages: 2000 },
    limitations: ["合成预览"], evidenceRequest: { schemaVersion: "business-evidence-v2", sources, collectionMode: "bulk", autoCollect: true,
      analysisRequest: { schemaVersion: "business-analysis-request-v1", question: body.question, requestedDimensions: ["shop"], requestedWindows: body.windows } } };
}
const server = http.createServer(async (req, res) => { try {
  const url = new URL(req.url, "http://localhost");
  const send = (value, status = 200) => { res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" }); res.end(JSON.stringify(value)); };
  if (url.pathname.startsWith("/api/")) {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString(), body = raw ? JSON.parse(raw) : null;
    requests.push({ path: url.pathname, query: url.search, method: req.method, body, raw, principal });
    if (url.pathname === "/api/ai/business-plan/netshop-options") {
      if (url.searchParams.get("expectedPrincipalKey") !== principal) return send({ error: "账号已变化" }, 403);
      const query = Object.fromEntries([...url.searchParams].filter(([key]) => !["expectedPrincipalKey", "cursor", "limit"].includes(key)));
      const owned = principal; const answer = { schemaVersion: "business-netshop-options-response-v1", principalKey: owned, page: optionPage(query) };
      const delay = delayOptions; delayOptions = 0; if (delay) await new Promise(resolve => setTimeout(resolve, delay)); return send(answer);
    }
    if (url.pathname === "/api/ai/business-plan/preview") return send(preview(body));
    if (url.pathname === "/api/ai/business-evidence" && req.method === "GET") return send({ principalKey: principal, items: [], total: 0 });
    if (url.pathname === "/api/ai/business-evidence" && req.method === "POST") {
      assert.equal(body.expectedPrincipalKey, principal);
      const prior = created.find(item => item.body.clientRequestId === body.clientRequestId);
      if (prior) assert.equal(prior.raw, raw); else created.push({ body, raw });
      if (dropEvidence) { dropEvidence = false; return send({ error: "合成已接收但回执未知" }, 503); }
      return send({ item: { id: "fixture-evidence" } });
    }
    if (url.pathname === "/api/ai/business-evidence/fixture-evidence") return send({ error: "合成测试不启动采集" }, 404);
    return send({ error: "unexpected synthetic route" }, 404);
  }
  if (["/app.js", "/app.css"].includes(url.pathname)) {
    res.setHeader("content-type", url.pathname.endsWith("css") ? "text/css" : "text/javascript");
    return res.end(await fs.readFile(path.join(directory, url.pathname.slice(1))));
  }
  res.setHeader("content-type", "text/html;charset=utf-8");
  res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><div id="root"></div><script src="/app.js"></script></body></html>');
} catch (error) { errors.push(String(error)); res.statusCode = 500; res.end(JSON.stringify({ error: String(error) })); } });
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", error => errors.push(String(error)));
await page.route("**/*", route => { if (new URL(route.request().url()).origin !== origin) { external.push(route.request().url()); return route.abort(); } return route.continue(); });
const check = async (name, fn) => { await fn(); checks.push(name); console.log(JSON.stringify({ name, passed: true })); };
const button = name => page.getByRole("button", { name, exact: true });
const question = () => page.getByLabel("分析问题", { exact: true });
const card = () => page.getByRole("region", { name: "问题范围建议", exact: true });
const generate = async value => { await question().fill(value); await button("从问题生成范围建议").click(); await card().waitFor(); };
const reset = async () => { principal = principalA; delayOptions = 0; dropEvidence = false; await page.evaluate(() => sessionStorage.clear()); await page.reload(); await button("从问题生成范围建议").waitFor(); };
try {
  await page.goto(origin); await button("从问题生成范围建议").waitFor();
  await check("manual_text_never_claims_verified_directory", async () => {
    await page.getByLabel("店铺 1 精确名称", { exact: true }).fill(identity.shop);
    await generate("店铺：合成范围店，近30天推广同比环比");
    assert.match(await card().innerText(), /缺少精确来源/);
    assert.match(await card().innerText(), /店铺候选：无/);
    assert.equal(requests.filter(item => item.path.endsWith("options") || item.path.endsWith("preview") || item.method === "POST").length, 0);
  });
  await check("apply_requires_click_and_invalidates_old_preview", async () => {
    assert.equal(await page.getByLabel("开始日期", { exact: true }).inputValue(), "");
    await button("应用日期与比较窗口").click();
    assert.ok(await page.getByLabel("开始日期", { exact: true }).inputValue());
    assert.equal(await page.getByRole("checkbox", { name: "同比", exact: true }).isChecked(), true);
    await button("预览完整来源计划").click(); await page.getByRole("region", { name: "来源范围预览" }).waitFor();
    await generate("店铺：合成范围店，2026-09-05至2026-09-20近30天推广环比");
    assert.match(await card().innerText(), /同时写了明确日期和近30天/);
    assert.match(await card().innerText(), /2026-08-20 至 2026-09-04/);
    assert.equal(await page.getByLabel("开始日期", { exact: true }).inputValue() !== "2026-09-05", true);
    await button("应用日期与比较窗口").click();
    assert.equal(await page.getByLabel("开始日期", { exact: true }).inputValue(), "2026-09-05");
    assert.equal(await page.getByRole("region", { name: "来源范围预览" }).count(), 0);
  });
  await check("today_with_near30_is_flagged_and_never_claims_today_imported", async () => {
    await generate("近30天到今天的推广");
    assert.match(await card().innerText(), /今日来源覆盖尚未核验/);
    assert.match(await card().innerText(), /近30天截至昨天/);
  });
  await check("verified_picker_choice_only_and_manual_edit_revokes_it", async () => {
    await reset(); await button("从历史导入选择网店来源").click();
    await button("添加来源 京东 合成范围店 promotion").waitFor();
    await button("添加来源 京东 合成范围店 promotion").click();
    await generate("店铺：合成范围店，2026-09-05至2026-09-20推广");
    assert.match(await card().innerText(), /店铺候选：京东 · 合成范围店/);
    await page.getByLabel("店铺 1 精确名称", { exact: true }).fill("临时手填名称");
    await page.getByLabel("店铺 1 精确名称", { exact: true }).fill(identity.shop);
    await button("从问题生成范围建议").click(); await card().waitFor();
    assert.match(await card().innerText(), /店铺候选：无/);
  });
  await check("principal_change_and_late_directory_result_do_not_reuse_old_identity", async () => {
    await reset(); delayOptions = 500; await button("从历史导入选择网店来源").click();
    await page.waitForFunction(() => document.querySelector('button[disabled]') !== null);
    principal = principalB; await button("刷新任务列表").click();
    await page.getByText("账号已变化", { exact: false }).first().waitFor();
    await page.waitForTimeout(650);
    await page.getByLabel("店铺 1 精确名称", { exact: true }).fill(identity.shop);
    await generate("店铺：合成范围店，近30天推广");
    assert.match(await card().innerText(), /店铺候选：无/);
    assert.equal(await button("收起网店来源选择").count(), 0);
  });
  await check("unknown_submission_retries_original_frozen_body", async () => {
    await reset(); await page.getByLabel("店铺 1 精确名称", { exact: true }).fill(identity.shop);
    await generate("2026-09-05至2026-09-20推广环比"); await button("应用日期与比较窗口").click();
    await button("预览完整来源计划").click(); await page.getByRole("region", { name: "来源范围预览" }).waitFor();
    await page.getByRole("checkbox", { name: /我已核对精确范围/ }).check();
    dropEvidence = true; await button("确认范围并开始后台采集").click();
    await page.getByText("有一次提交等待确认", { exact: true }).waitFor();
    await page.getByText("合成已接收但回执未知", { exact: false }).waitFor();
    const first = requests.filter(item => item.path === "/api/ai/business-evidence" && item.method === "POST").at(-1).raw;
    await page.reload(); await button("确认并重试同一次提交").click();
    await page.getByText("证据任务已保存", { exact: false }).waitFor();
    assert.equal(requests.filter(item => item.path === "/api/ai/business-evidence" && item.method === "POST").at(-1).raw, first);
    assert.equal(created.length, 1);
  });
  await check("mobile_390px_suggestion_has_no_overflow", async () => {
    await reset(); await page.setViewportSize({ width: 390, height: 844 });
    await generate("店铺：合成范围店，2026-09-05至2026-09-20近30天推广关键词SKU同比环比");
    await page.screenshot({ path: path.join(directory, "mobile.png"), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  });
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  await fs.writeFile(path.join(directory, "evidence.json"), JSON.stringify({ passed: checks.length, checks, pageErrors: errors, externalRequests: external,
    operations: { production: false, modelCalls: false } }, null, 2));
} catch (error) {
  await fs.writeFile(path.join(directory, "failure.json"), JSON.stringify({ error: String(error), checks, errors,
    dom: await page.locator("body").innerText() }, null, 2)); throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
