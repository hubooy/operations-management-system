// Synthetic loopback API only. Never connects to the application or a model.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-workbench-v2-ui");
await fs.mkdir(directory, { recursive: true });
const entry = path.join(directory, "entry.tsx");
await fs.writeFile(entry, `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root, "app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={id=>{document.getElementById('report-result')!.textContent=id;}}/>);`);
await build({ entryPoints: [entry], bundle: true, outfile: path.join(directory, "app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root, "tsconfig.json") });
const principalA = "a".repeat(64), principalB = "b".repeat(64), catalog = "c".repeat(64);
let principal = principalA, delayNext = false, wrongVersion = false;
const requests = [], pageErrors = [], results = [];
const entries = Array.from({ length: 17 }, (_, index) => ({ ordinal: index+1, key: `source-${index+1}`, domain: "sales", query: { platform: "京东", shop: `精确店铺${index+1}`, channel: "current", window: "previous", startDate: "2026-08-01", endDate: "2026-08-31" } }));
const v2 = { id: "evidence-v2", question: "合成 v2 采集任务", version: 1, status: "collecting", collection: { status: "paused" }, createdAt: "2026-09-17T00:00:00Z", storedBytes: 1024,
  sourceCount: 17, completedSources: 2, rowCount: 18,
  plan: { schemaVersion: "business-evidence-v2", sourceCount: 17, catalogDigest: catalog, analysisRequest: { question: "合成 v2 采集任务" }, collector: { version: 1, surface: "business_collection", pageSize: 100 } },
  sources: Object.fromEntries(entries.map((source, index) => [source.key, { pageCount: index < 2 ? 1 : 0, rowCount: index === 0 ? 18 : 0, complete: index < 2 }])) };
const v1 = { id: "evidence-v1", question: "合成旧任务", version: 1, status: "sealed", collection: { status: "sealed" }, createdAt: "2026-09-17T00:00:00Z", storedBytes: 0,
  plan: { schemaVersion: "business-evidence-v1", analysisRequest: { question: "合成旧任务" }, sources: [{ key: "old", domain: "sales", query: { shop: "旧版精确店" } }] }, sources: { old: { pageCount: 1, rowCount: 0, complete: true } } };
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");
    const send = (value, status=200) => { res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" }); res.end(JSON.stringify(value)); };
    if (url.pathname.startsWith("/api/")) {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const raw = Buffer.concat(chunks).toString(), body = raw ? JSON.parse(raw) : null;
      requests.push({ path: url.pathname, query: url.search, method: req.method, body });
      if (url.pathname === "/api/ai/business-evidence") return send({ principalKey: principal, items: principal === principalA ? [v2, v1] : [], pagination: { total: principal === principalA ? 2 : 0 } });
      if(url.pathname === "/api/ai/business-reports"){assert.equal(body.expectedPrincipalKey,principal);assert.equal(body.evidenceRunId,v2.id);assert.equal(v2.workbenchAnalysisEnabled,true);assert.equal("budgetPlan" in body,false);return send({item:{id:body.dryRun?"report-v2-dry":"report-v2-paid"}});}
      const match = url.pathname.match(/^\/api\/ai\/business-evidence\/(evidence-v[12])(?:\/(sources|control|finish))?$/);
      if (!match || principal !== principalA) return send({ error: "不存在" }, 404);
      const item = match[1] === v2.id ? v2 : v1;
      if (match[2] === "sources") {
        const offset = Number(url.searchParams.get("offset")), limit = Number(url.searchParams.get("limit"));
        const items = entries.slice(offset, offset+limit), next = offset+items.length;
        const value = { schemaVersion: "business-evidence-directory-page-v2", runId: item.id, evidenceVersion: item.version+(wrongVersion ? 1 : 0), catalogDigest: item.plan.catalogDigest, offset, requestedLimit: limit, total: entries.length, returned: items.length, nextOffset: next < entries.length ? next : null, items: structuredClone(items) };
        if (delayNext) { delayNext = false; await new Promise(resolve => setTimeout(resolve, 650)); }
        return send(value);
      }
      if (match[2]) {
        if (body.expectedVersion !== item.version) return send({ error: "版本冲突" }, 409);
        item.version++; item.collection.status = body.action === "resume" ? "queued" : body.action === "pause" ? "paused" : "cancelled";
        if (body.action === "cancel") item.status = "cancelled";
        return send({ item });
      }
      return send({ item, reports: [], reportsPagination: { hasMore: false }, principalKey: principal });
    }
    if (["/app.js", "/app.css"].includes(url.pathname)) { res.setHeader("content-type", url.pathname.endsWith("css") ? "text/css" : "text/javascript"); res.end(await fs.readFile(path.join(directory, url.pathname.slice(1)))); return; }
    res.setHeader("content-type", "text/html;charset=utf-8"); res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><main id="root"></main><div id="report-result"></div><script src="/app.js"></script></body></html>');
  } catch (error) { res.statusCode=500; res.end(JSON.stringify({ error: String(error) })); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", error => pageErrors.push(String(error)));
const detail = () => page.getByRole("region", { name: "选中任务详情" });
const directoryView = () => page.getByRole("region", { name: "精确来源目录" });
const chooseV2 = async () => { await page.getByRole("button", { name: /合成 v2 采集任务/ }).click(); await directoryView().getByText("来源 1–10 / 17", { exact: false }).waitFor(); };
const check = async (name, fn) => { await fn(); results.push({ name, passed: true }); console.log(JSON.stringify(results.at(-1))); };
try {
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await check("compact_header_actual_progress_and_precise_paging", async () => {
    await chooseV2(); assert.match(await detail().innerText(), /来源总数 17 · 分页采集完成 2 · 已保存 2 页 \/ 18 行/);
    assert.match(await directoryView().innerText(), /ERP渠道：current/); assert.match(await directoryView().innerText(), /周期：环比/);
    assert.equal(await directoryView().getByRole("cell", { name: "分页采集完成", exact: true }).count(), 2);
    await page.getByRole("button", { name: "下一页来源", exact: true }).click(); await directoryView().getByText("来源 11–17 / 17", { exact: false }).waitFor();
    assert.equal(await page.getByRole("button", { name: "下一页来源", exact: true }).isDisabled(), true);
    assert.equal(await directoryView().getByRole("cell", { name: "source-1", exact: true }).count(), 0);
    await page.getByRole("button", { name: "上一页来源", exact: true }).click(); await directoryView().getByText("来源 1–10 / 17", { exact: false }).waitFor();
  });
  await check("v2_pause_resume_cancel_preserved", async () => {
    await page.getByRole("button", { name: "恢复后台采集", exact: true }).click(); await page.getByRole("button", { name: "暂停后台采集", exact: true }).waitFor();
    await page.getByRole("button", { name: "暂停后台采集", exact: true }).click(); await page.getByRole("button", { name: "恢复后台采集", exact: true }).waitFor();
    await page.getByRole("button", { name: "取消采集任务", exact: true }).click(); await detail().getByText("已取消", { exact: false }).waitFor();
    assert.deepEqual(requests.filter(r => r.method === "POST").map(r => r.body.action), ["resume", "pause", "cancel"]);
  });
  await check("sealed_v2_blocks_both_report_actions", async () => {
    v2.status="sealed"; v2.collection.status="sealed"; v2.version++;
    await page.getByRole("button", { name: "刷新选中任务", exact: true }).click(); await detail().getByText("证据已封存", { exact: false }).waitFor();
    assert.match(await detail().innerText(), /服务端尚未开放此任务的工作台分析启动/);
    for (const name of ["模拟分析（不调用模型）", "启动多 Agent 分析（调用模型）"]) assert.equal(await page.getByRole("button", { name, exact: true }).isDisabled(), true);
    assert.equal(requests.filter(r => r.path === "/api/ai/business-reports").length, 0);
  });
  await check("server_flag_requires_sealed_question_then_manual_dry_and_paid_navigation", async () => {
    const refresh = async()=>{v2.version++;await page.getByRole("button",{name:"刷新选中任务",exact:true}).click();await detail().getByText(`版本 ${v2.version}`,{exact:false}).first().waitFor();};
    v2.workbenchAnalysisEnabled=false;await refresh();assert.equal(await page.getByRole("button",{name:"模拟分析（不调用模型）",exact:true}).isDisabled(),true);
    v2.workbenchAnalysisEnabled=true;v2.status="collecting";await refresh();assert.equal(await page.getByRole("button",{name:"模拟分析（不调用模型）",exact:true}).isDisabled(),true);
    v2.status="sealed";const question=v2.plan.analysisRequest.question;delete v2.plan.analysisRequest.question;await refresh();assert.equal(await page.getByRole("button",{name:"模拟分析（不调用模型）",exact:true}).isDisabled(),true);
    v2.plan.analysisRequest.question=question;await refresh();assert.equal(requests.filter(r=>r.path==="/api/ai/business-reports").length,0);assert.match(await detail().innerText(),/当前未开放固定预算分析/);
    for(const [name,id,dryRun]of [["模拟分析（不调用模型）","report-v2-dry",true],["启动多 Agent 分析（调用模型）","report-v2-paid",false]]){await page.getByRole("button",{name,exact:true}).click();await page.locator("#report-result").filter({hasText:id}).waitFor();const request=requests.filter(r=>r.path==="/api/ai/business-reports").at(-1);assert.equal(request.body.dryRun,dryRun);assert.equal("budgetPlan" in request.body,false);}
    assert.equal(requests.filter(r=>r.path==="/api/ai/business-reports").length,2);
  });
  await check("late_directory_isolated_after_task_switch_and_v1_still_works", async () => {
    delayNext=true; await page.getByRole("button", { name: "下一页来源", exact: true }).click();
    await page.getByRole("button", { name: /合成旧任务/ }).click(); await detail().getByRole("cell", { name: "店铺：旧版精确店", exact: true }).waitFor();
    await page.waitForTimeout(750); assert.equal(await directoryView().count(), 0);
    assert.equal(await page.getByRole("button", { name: "模拟分析（不调用模型）", exact: true }).isDisabled(), false);
  });
  await check("version_change_restarts_directory_and_discards_old_page", async () => {
    await chooseV2(); delayNext=true; await page.getByRole("button", { name: "下一页来源", exact: true }).click();
    await page.waitForTimeout(70); v2.version++; await page.getByRole("button", { name: "刷新选中任务", exact: true }).click();
    await directoryView().getByText(`任务版本 ${v2.version}`, { exact: false }).waitFor(); await page.waitForTimeout(750);
    assert.match(await directoryView().innerText(), /来源 1–10 \/ 17/); assert.doesNotMatch(await directoryView().innerText(), /来源 11–17/);
  });
  await check("mismatched_directory_version_fails_closed_and_retries", async () => {
    wrongVersion=true; await page.getByRole("button", { name: "下一页来源", exact: true }).click(); await directoryView().getByRole("alert").waitFor();
    assert.equal(await directoryView().getByRole("cell").count(), 0); assert.match(await directoryView().innerText(), /目录版本已变化/);
    wrongVersion=false; await page.getByRole("button", { name: "重试来源目录", exact: true }).click(); await directoryView().getByText("来源 11–17 / 17", { exact: false }).waitFor();
  });
  await check("mobile_directory_has_no_page_overflow", async () => {
    await page.setViewportSize({ width: 390, height: 844 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: path.join(directory, "v2-directory-mobile.png"), fullPage: true });
  });
  await check("account_change_clears_pending_directory_response", async () => {
    delayNext=true; await page.getByRole("button", { name: "上一页来源", exact: true }).click(); principal=principalB;
    await page.getByRole("button", { name: "刷新任务列表", exact: true }).click(); await page.getByTestId("write-error").filter({ hasText: "账号已变化" }).waitFor();
    await page.waitForTimeout(750); assert.equal(await directoryView().count(), 0); assert.equal(await detail().count(), 0); assert.equal(await page.getByText("精确店铺1", { exact: false }).count(), 0);
  });
  assert.deepEqual(pageErrors, []);
  await fs.writeFile(path.join(directory, "evidence.json"), JSON.stringify({ passed: true, syntheticOnly: true, cases: results, pageErrors, requests: requests.length }, null, 2));
} catch (error) {
  await fs.writeFile(path.join(directory, "failure.json"), JSON.stringify({ error: String(error), results, pageErrors, requests, dom: await page.locator("body").innerText() }, null, 2)); throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
