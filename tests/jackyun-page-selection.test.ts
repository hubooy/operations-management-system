import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { waitForUniqueJackyunLoginPage } from "../tools/jackyun-api-export";
import { classifyHourlyRetryFailure } from "../tools/n8n-hourly-retry-policy.mjs";
import { assertClosedPreflight, inspectPreflightClosure, publishPreflightClosure, recoverySha, jackyunWorkflowId, type PreflightEvidence } from "../lib/jackyun/preflight-recovery";

test("login page selection waits for two stable observations", async () => {
  const page = { url: () => "https://web.jackyun.com/home/mainframe_web_horizontal.html" };
  let calls = 0;
  const selected = await waitForUniqueJackyunLoginPage({ pages: () => calls++ === 0 ? [] : [page] }, { timeoutMs: 2_000, settleMs: 100 });
  assert.equal(selected, page);
  assert.ok(calls >= 3);
});

test("login page selection reports a bounded, observable ambiguity", async () => {
  const pages = [
    { url: () => "https://web.jackyun.com/home/mainframe_web_horizontal.html" },
    { url: () => "https://web.jackyun.com/home/mainframe_web_horizontal.html" },
  ];
  await assert.rejects(waitForUniqueJackyunLoginPage({ pages: () => pages }, { timeoutMs: 1_000, settleMs: 100 }), /API_LOGIN_PAGE_NOT_UNIQUE: eligible=2; total=2; blank=0; jackyun=2; other=0/);
});

test("page ambiguity and an unclosed prior run stop the hourly retry chain", () => {
  const result = classifyHourlyRetryFailure({ workflow: { id: jackyunWorkflowId }, execution: { mode: "webhook", error: "API_LOGIN_PAGE_NOT_UNIQUE" } });
  assert.deepEqual(result, { retry: false, reason: "manual_intervention_required" });
  const blocked = classifyHourlyRetryFailure({ workflow: { id: jackyunWorkflowId }, execution: { mode: "webhook", error: "原运行 n8n-export-first-4102 尚未闭合" } });
  assert.deepEqual(blocked, { retry: false, reason: "manual_intervention_required" });
});

async function closureFixture() {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-4102-closure-"));
  const pipeline = path.join(root, "outputs/jackyun-export-first"), download = path.join(root, "downloads");
  await mkdir(pipeline, { recursive: true }); await mkdir(path.join(root, "config")); await mkdir(download);
  const runId = "n8n-export-first-4102";
  const plan = { version: 1, protocol: "2026-09-06.export-first.1", executionId: "4102", runId,
    runDate: "2026-09-23", asOfDate: "2026-09-22", baseUrl: "http://localhost:3000", createdAt: "2026-09-22T18:10:25.593Z",
    phase: "exporting", exports: {}, exportTransport: "session_api_v1" };
  await writeFile(path.join(pipeline, `${runId}.json`), JSON.stringify(plan, null, 2) + "\n");
  await writeFile(path.join(pipeline, "active.json"), JSON.stringify({ runId, executionId: "4102" }));
  await writeFile(path.join(root, "config/jackyun-export-first-policy.json"), JSON.stringify({ version: plan.protocol,
    browser: { downloadDirectory: download, allowedDownloadHosts: [], controller: { profileDirectory: path.join(root, "profile") } } }));
  const evidence: PreflightEvidence = { executionId: "4102", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-22T18:10:24.726Z", stoppedAt: "2026-09-22T18:10:28.119Z", retrySuccessId: null,
    lastNode: "B·接口校验与五表下载", runNodes: ["失败后每小时安全重试入口", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"],
    error: "API_LOGIN_PAGE_NOT_UNIQUE", httpCode: "500", requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "a".repeat(64), activeExecutions: 0 };
  return { root, pipeline, runId, evidence, plan };
}

test("4102 zero-effect page selection closure is generic and create-only", async () => {
  const {root,pipeline,runId,evidence} = await closureFixture();
  const proposal = await inspectPreflightClosure(root, "4102", evidence, "2026-09-23T09:00:00Z");
  assert.equal(proposal.reason, "verified_api_page_selection_without_business_effects");
  await publishPreflightClosure(root, proposal, evidence, recoverySha(JSON.stringify(proposal)));
  await assertClosedPreflight(root, "4102");
  assert.deepEqual(JSON.parse(await readFile(path.join(pipeline, "active.json"), "utf8")), { runId, executionId: "4102" });
  await assert.rejects(publishPreflightClosure(root, proposal, evidence, recoverySha(JSON.stringify(proposal))), /EEXIST/);
});

test("login diagnostics exclude URLs, fragments, queries and foreign origins", async () => {
  const pages = [
    { url: () => "https://web.jackyun.com/?token=synthetic-secret#private" },
    { url: () => "about:blank" },
    { url: () => "https://private.invalid/customer" },
  ];
  await assert.rejects(waitForUniqueJackyunLoginPage({pages:()=>pages},{timeoutMs:1000,settleMs:100}), error => {
    assert.match(String(error), /eligible=2; total=3; blank=1; jackyun=1; other=1/);
    assert.doesNotMatch(String(error), /https|synthetic-secret|private|customer/);return true;
  });
});

test("zero or changing page identities cannot satisfy stable unique selection", async () => {
  for (const pages of [()=>[],()=>[{url:()=>"about:blank"}]]) {
    await assert.rejects(waitForUniqueJackyunLoginPage({pages},{timeoutMs:1000,settleMs:100}),/API_LOGIN_PAGE_NOT_UNIQUE/);
  }
});

test("generic page closure requires exact pre-export scope and rejects stale or foreign evidence", async () => {
  const f=await closureFixture();
  for (const bad of [{error:"API_LOGIN_PAGE_NOT_UNIQUE: export submitted"},{error:"download timeout"},
    {status:"running"},{workflowId:"other"},{executionId:"4114"},{activeExecutions:1},{retrySuccessId:"4114"},
    {lastNode:"A·固定采集日和销售日期"},{runNodes:[...f.evidence.runNodes,"D·统一导入运营管理系统"]},
    {httpCode:"502"},{requestUrl:"http://127.0.0.1:5791/jackyun/export-first/import"},
    {executionDataSha256:"bad"},{stoppedAt:"2026-09-22T18:00:00Z"}]) {
    await assert.rejects(inspectPreflightClosure(f.root,"4102",{...f.evidence,...bad},"2026-09-23T09:00:00Z"));
  }
  for (const mutation of [{exportIntent:"inventory"},{exports:1},{exports:{inventory:{}}},{phase:"importing"},{exportTransport:"direct_http_v1"},{extra:true}]) {
    await writeFile(path.join(f.pipeline,`${f.runId}.json`),JSON.stringify({...f.plan,...mutation}));
    await assert.rejects(inspectPreflightClosure(f.root,"4102",f.evidence,"2026-09-23T09:00:00Z"));
  }
});

test("generic page closure fences all effects and stale approval, and rechecks late effects", async () => {
  for(const late of [false,true]) for(const index of [0,1,2,3]) {
    const f=await closureFixture();const p=await inspectPreflightClosure(f.root,"4102",f.evidence,"2026-09-23T09:00:00Z");
    if(late)await publishPreflightClosure(f.root,p,f.evidence,recoverySha(JSON.stringify(p)));
    await mkdir(p.absentPaths[index],{recursive:true});
    if(late)await assert.rejects(assertClosedPreflight(f.root,"4102"));
    else await assert.rejects(publishPreflightClosure(f.root,p,f.evidence,recoverySha(JSON.stringify(p))));
  }
  const f=await closureFixture();const p=await inspectPreflightClosure(f.root,"4102",f.evidence,"2026-09-23T09:00:00Z");
  await assert.rejects(publishPreflightClosure(f.root,p,{...f.evidence,executionDataSha256:"b".repeat(64)},recoverySha(JSON.stringify(p))));
  await assert.rejects(publishPreflightClosure(f.root,p,f.evidence,"0".repeat(64)));
  await writeFile(path.join(f.pipeline,"active.json"),JSON.stringify({runId:"n8n-export-first-4114",executionId:"4114"}));
  await assert.rejects(publishPreflightClosure(f.root,p,f.evidence,recoverySha(JSON.stringify(p))));
});
