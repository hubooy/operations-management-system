// Real report viewer against a synthetic loopback API; no model or production.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-promotion-report-view-ui");
await fs.mkdir(directory, { recursive: true });
await fs.writeFile(path.join(directory, "entry.tsx"), `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root, "app/ai-report-workbench-view.tsx"))};createRoot(document.getElementById('root')).render(<Workbench kind="pipelines"/>);`);
await build({ entryPoints: [path.join(directory, "entry.tsx")], bundle: true, outfile: path.join(directory, "app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root, "tsconfig.json") });
const profile = "business-agent-screening-promotion-reference-v1", principal = "a".repeat(64);
const roles = ["commerce", "promotion", "market_b2b", "independent_review", "report"];
let stage = "running", malformed = false;
const requests = [], cases = [], errors = [], external = [];
const item = { id: "promotion-report", name: "合成词货报告", status: "running", dryRun: false, workflowId: "flow", version: 0,
  createdAt: "2026-09-24T00:00:00Z", template: "词货", skills: [], scope: { platform: "京东", shop: "精确店铺", startDate: "2026-08-01", endDate: "2026-08-31" } };
function detail() {
  const complete = stage !== "running";
  const nodes = [...roles.map((key, index) => ({ id: `node-${key}`, key, type: "agent",
    status: complete ? "completed" : index === 0 ? "running" : "pending", version: 1,
    output: { answer: "INTERNAL_RAW_OUTPUT" }, instruction: "internal" })),
    { id: "human", key: "human_review", type: "human_review", status: stage === "waiting_review" ? "waiting_review" : stage === "completed" ? "completed" : "pending", version: 1 }];
  const screening = { schemaVersion: malformed ? "wrong-schema" : "business-promotion-completed-screening-v1",
    coverage: [{ kind: "source" }, { kind: "partition" }], readProofs: Object.fromEntries(roles.map(role => [role, { role, jobId: `job-${role}` }])),
    limitations: ["推广归因不等于ERP净销售"], candidateDisclosure: { fullCandidatesIncluded: false, crossPartitionAmountsAdditive: false } };
  return { item: { ...item, status: stage }, snapshot: { schemaVersion: "business-report-v1", executionProfile: profile,
    evidenceProtocol: "reference-v2", question: "合成词货诊断" },
    workflow: { id: "flow", status: stage, version: 1, retryable: false, nodes }, delivery: null,
    ...(complete ? { sections: [{ title: "合成结论", body: "保留来源缺口" }], screening,
      professionalAnalyses: Object.fromEntries(roles.slice(0, 3).map(role => [role, { role, summary: `${role}已验证摘要` }])) }
      : { contentError: "词货五角色诊断尚未完成" }) };
}
const server = http.createServer(async (req, res) => { try {
  const url = new URL(req.url, "http://localhost");
  const send = (value, code = 200) => { res.writeHead(code, { "content-type": "application/json", "cache-control": "no-store" }); res.end(JSON.stringify(value)); };
  if (url.pathname.startsWith("/api/")) {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString();
    requests.push({ path: url.pathname, method: req.method, body: raw ? JSON.parse(raw) : null });
    if (url.pathname === "/api/ai/report-library") return send({ item: { version: 0, config: { templates: [], skills: [], pipelines: [] } }, history: [], hasMore: false });
    if (url.pathname === "/api/ai/reports") return send({ items: [{ ...item, status: stage }], total: 1 });
    if (url.pathname === "/api/ai/reports/promotion-report") return send(detail());
    if (url.pathname === "/api/ai/business-evidence") return send({ principalKey: principal, items: [], total: 0 });
    if (url.pathname === "/api/ai/reports/promotion-report/files") return send({ items: [] });
    return send({ error: "unexpected synthetic API" }, 404);
  }
  if (["/app.js", "/app.css"].includes(url.pathname)) { res.setHeader("content-type", url.pathname.endsWith("css") ? "text/css" : "text/javascript"); return res.end(await fs.readFile(path.join(directory, url.pathname.slice(1)))); }
  res.setHeader("content-type", "text/html;charset=utf-8");
  res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><div id="root"></div><script src="/app.js"></script></body></html>');
} catch (error) { errors.push(String(error)); res.statusCode = 500; res.end(JSON.stringify({ error: String(error) })); } });
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", error => errors.push(String(error)));
await page.route("**/*", route => { if (new URL(route.request().url()).origin !== origin) { external.push(route.request().url()); return route.abort(); } return route.continue(); });
const check = async (name, fn) => { await fn(); cases.push(name); console.log(JSON.stringify({ name, passed: true })); };
const panel = () => page.getByRole("region", { name: "词货专项进度" });
const refresh = async () => { await page.getByRole("button", { name: "刷新详情", exact: true }).click(); await panel().waitFor(); };
try {
  await page.goto(origin);
  await page.getByRole("button", { name: "查看与复核", exact: true }).click(); await panel().waitFor();
  await check("running_nodes_do_not_claim_scan_or_file_ready", async () => {
    assert.match(await panel().innerText(), /尚未返回独立的筛查准备回执或完整筛查内容/);
    assert.match(await panel().innerText(), /店铺与商品：运行中/);
    assert.equal(await page.getByRole("region", { name: "完整经营报告文件" }).count(), 0);
    assert.equal(await page.getByText("INTERNAL_RAW_OUTPUT", { exact: false }).count(), 0);
  });
  await check("waiting_review_content_shows_coverage_and_proofs_without_file_ready", async () => {
    stage = "waiting_review"; await refresh();
    assert.match(await panel().innerText(), /2 条覆盖记录与五角色独立读取证明/);
    assert.match(await panel().innerText(), /推广归因不等于ERP净销售/);
    assert.match(await panel().innerText(), /人工复核：待人工复核/);
    assert.equal(await page.getByRole("button", { name: "通过复核", exact: true }).isDisabled(), false);
    const files = page.getByRole("region", { name: "完整经营报告文件" }); await files.waitFor();
    assert.equal(await files.getByRole("button", { name: /生成已复核/ }).count(), 0);
    assert.equal(await files.getByRole("button", { name: /下载 HTML|下载 Excel/ }).count(), 0);
  });
  await check("completed_still_requires_file_task_before_download", async () => {
    stage = "completed"; await refresh();
    const files = page.getByRole("region", { name: "完整经营报告文件" }); await files.waitFor();
    assert.equal(await files.getByRole("button", { name: "生成已复核多卷文件", exact: true }).count(), 1);
    assert.equal(await files.getByRole("button", { name: /下载 HTML|下载 Excel/ }).count(), 0);
  });
  await check("malformed_promotion_content_fails_closed", async () => {
    malformed = true; await refresh();
    assert.match(await panel().innerText(), /详情无法核验/);
    assert.equal(await panel().getByText("2 条覆盖记录", { exact: false }).count(), 0);
    assert.equal(await page.getByRole("region", { name: "完整经营报告文件" }).count(), 0);
    malformed = false;
  });
  await check("mobile_390px_panel_has_no_overflow", async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await refresh();
    await page.screenshot({ path: path.join(directory, "mobile.png"), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  });
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  assert.equal(requests.some(item => item.method === "POST"), false);
  await fs.writeFile(path.join(directory, "evidence.json"), JSON.stringify({ passed: cases.length, cases,
    pageErrors: errors, externalRequests: external, production: false, modelCalls: false }, null, 2));
} catch (error) { await fs.writeFile(path.join(directory, "failure.json"), JSON.stringify({ error: String(error), cases, errors,
  dom: await page.locator("body").innerText() }, null, 2)); throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
