import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import os, { tmpdir } from "node:os";
import { syncBuiltinESMExports } from "node:module";
import { DatabaseSync } from "node:sqlite";
import { recoverPreviousJackyunPreflight } from "../lib/jackyun/automatic-preflight-recovery";
import { readN8nReplacementEvidence } from "../lib/jackyun/n8n-preflight-evidence";
import { classifyJackyunPreflightFailure } from "../lib/jackyun/preflight-failure";
import { assertClosedPreflight, preflightClosurePath, jackyunWorkflowId } from "../lib/jackyun/preflight-recovery";
import { runJackyunExportFirstAction } from "../tools/jackyun-export-first-pipeline";

const missing = "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（missing）。";
const challenge = "waiting_login：吉客云登录已停止（challenge_present）。";
const at = "2026-09-28T01:00:02.000Z";
const nodes = ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"];
async function fixture(t: TestContext, error = missing, mode = "trigger") {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-auto-preflight-"));
  t.after(async () => { assert(path.resolve(root).startsWith(path.resolve(tmpdir()) + path.sep)); assert(path.basename(root).startsWith("jackyun-auto-preflight-")); await rm(root, { recursive: true, force: true }); });
  const pipeline = path.join(root, "outputs/jackyun-export-first"), download = path.join(root, "downloads");
  await mkdir(pipeline, { recursive: true }); await mkdir(path.join(root, "config")); await mkdir(download);
  const plan = { version: 2, protocol: "2026-09-06.export-first.1", executionId: "9000", runId: "n8n-export-first-9000",
    runDate: "2026-09-27", asOfDate: "2026-09-26", salesStartDate: "2026-08-13", baseUrl: "http://localhost:3000",
    createdAt: "2026-09-27T02:00:02.000Z", phase: "exporting", exports: {}, exportTransport: "session_api_v1" };
  const planPath = path.join(pipeline, plan.runId + ".json"), activePath = path.join(pipeline, "active.json");
  await writeFile(planPath, JSON.stringify(plan)); await writeFile(activePath, JSON.stringify({ runId: plan.runId, executionId: "9000" }));
  await writeFile(path.join(root, "config/jackyun-export-first-policy.json"), JSON.stringify({ version: plan.protocol,
    browser: { downloadDirectory: download, allowedDownloadHosts: [], controller: { profileDirectory: path.join(root, "profile") } } }));
  const n8nUserRoot = path.join(root, "user"); await mkdir(path.join(n8nUserRoot, ".n8n"), { recursive: true });
  const database = path.join(n8nUserRoot, ".n8n/database.sqlite"), db = new DatabaseSync(database);
  db.exec("CREATE TABLE execution_entity(id INTEGER PRIMARY KEY,workflowId TEXT,status TEXT,mode TEXT,startedAt TEXT,stoppedAt TEXT,retrySuccessId TEXT,deletedAt TEXT); CREATE TABLE execution_data(executionId INTEGER,data TEXT)");
  const insert = db.prepare("INSERT INTO execution_entity VALUES(?,?,?,?,?,?,?,?)");
  insert.run(9000, jackyunWorkflowId, "error", "trigger", "2026-09-27 02:00:00.000", "2026-09-27 02:00:10.000", null, null);
  insert.run(9001, jackyunWorkflowId, "running", mode, "2026-09-28 01:00:00.000", null, null, null);
  const data = JSON.stringify([{ resultData: "1", unrelatedSecret: "synthetic-secret" }, { error: "2", runData: "3", lastNodeExecuted: "4" },
    { description: "5", httpCode: "6", node: "7" }, Object.fromEntries(nodes.map(n => [n, []])), nodes[4], error, "500", { parameters: "8" },
    { url: "9" }, "http://127.0.0.1:5791/jackyun/export-first/export-all"]);
  db.prepare("INSERT INTO execution_data VALUES(?,?)").run(9000, data); db.close();
  const readEvidence = (old: string, next: string) => {
    // Synchronous adapter mocks only this isolated test process, without
    // changing the user's environment or adding a production path override.
    const mocked = t.mock.method(os, "homedir", () => n8nUserRoot); syncBuiltinESMExports();
    try { return readN8nReplacementEvidence(old, next); }
    finally { mocked.mock.restore(); syncBuiltinESMExports(); }
  };
  const recovery = { readEvidence, credentialReady: async () => true };
  const recover = () => recoverPreviousJackyunPreflight(root, "9000", "9001", at, recovery);
  const deps = { root, lockDirectory: path.join(root, "lock"), now: () => new Date(at), profileReady: async () => true,
    request: (async () => new Response("{}")) as typeof fetch,
    recoverPreviousPreflight: (old: string, next: string, time: string) => recoverPreviousJackyunPreflight(root, old, next, time, recovery) };
  const changeDb = (sql: string) => { const writer = new DatabaseSync(database); try { writer.exec(sql); } finally { writer.close(); } };
  return { root, pipeline, download, plan, planPath, activePath, database, recovery, recover, deps, changeDb };
}

test("all full-run modes recover an arbitrary proven zero-effect credential failure without exporting", async t => {
  for (const mode of ["trigger", "webhook", "manual"]) {
    const f = await fixture(t, missing, mode), original = await readFile(f.planPath), dbBefore = await readFile(f.database);
    let businessCalls = 0;
    await runJackyunExportFirstAction("plan-api", "9001", { ...f.deps, runApi: (async () => { businessCalls++; throw new Error("must not export in A"); }) });
    const active = JSON.parse(await readFile(f.activePath, "utf8")), receipt = JSON.parse(await readFile(preflightClosurePath(f.root, "9000"), "utf8"));
    assert.equal(active.executionId, "9001"); assert.equal(receipt.version, 3); assert.equal(receipt.replacementExecutionId, "9001");
    assert.equal(receipt.credentialReadinessVerified, true); assert.equal(businessCalls, 0);
    assert.deepEqual(await readFile(f.planPath), original); assert.deepEqual(await readFile(f.database), dbBefore);
    assert.doesNotMatch(JSON.stringify(receipt), /synthetic-secret/);
    const fresh = JSON.parse(await readFile(path.join(f.pipeline, "n8n-export-first-9001.json"), "utf8"));
    assert.equal(fresh.runDate, "2026-09-28"); assert.equal(fresh.asOfDate, "2026-09-27"); assert.equal(fresh.salesStartDate, "2026-08-14");
    await assert.rejects(runJackyunExportFirstAction("export-all", "9000", f.deps));
  }
});

test("replacement authorization is one execution only, create-only and rechecks live n8n state", async t => {
  const f = await fixture(t); await f.recover();
  const receiptPath = preflightClosurePath(f.root, "9000"), original = await readFile(receiptPath);
  await f.recover(); assert.deepEqual(await readFile(receiptPath), original);
  await assertClosedPreflight(f.root, "9000", "9001");
  await assert.rejects(assertClosedPreflight(f.root, "9000", "9002"));
  f.changeDb("UPDATE execution_entity SET status='success', stoppedAt='2026-09-28 01:00:01.000' WHERE id=9001");
  await assert.rejects(f.recover(), /需要人工/);
  assert.deepEqual(await readFile(receiptPath), original);
});

test("captcha and rejected-login failures require a manual full run even with no business effects", async t => {
  for (const message of [challenge, "waiting_login：吉客云登录已停止（credential_rejected）。", "waiting_login：吉客云登录已停止（tenant_mismatch）。", "API_LOGIN_PAGE_NOT_UNIQUE: eligible=0; total=1; blank=0; jackyun=0; other=1"]) {
    for (const mode of ["trigger", "webhook"]) {
      const f = await fixture(t, message, mode); await assert.rejects(f.recover(), /需要人工/);
      await assert.rejects(readFile(preflightClosurePath(f.root, "9000")), /ENOENT/);
    }
    const f = await fixture(t, message, "manual"); await f.recover(); await assertClosedPreflight(f.root, "9000", "9001");
  }
});

test("missing, malformed or failed readiness probes leave the old task closed to recovery and redact errors", async t => {
  for (const failure of [false, "throws"] as const) {
    const f = await fixture(t); f.recovery.credentialReady = async () => { if (failure === "throws") throw new Error("synthetic-secret"); return false; };
    await assert.rejects(f.recover(), e => e instanceof Error && /凭据仍未就绪/.test(e.message) && !e.message.includes("synthetic-secret"));
    await assert.rejects(readFile(preflightClosurePath(f.root, "9000")), /ENOENT/);
  }
});

test("another active run, changed identity, chronology or raw failure data blocks recovery", async t => {
  for (const sql of [
    "INSERT INTO execution_entity VALUES(9002,'J8kY2mQ5vR7sT4pN','running','trigger','2026-09-28 01:00:01.000',NULL,NULL,NULL)",
    "UPDATE execution_entity SET status='running',stoppedAt=NULL WHERE id=9000",
    "UPDATE execution_entity SET retrySuccessId='8999' WHERE id=9000",
    "UPDATE execution_entity SET workflowId='foreign' WHERE id=9001",
    "UPDATE execution_entity SET deletedAt='2026-09-28 00:00:00.000' WHERE id=9001",
    "UPDATE execution_entity SET mode='cli' WHERE id=9001",
    "UPDATE execution_entity SET startedAt='2026-09-27 01:00:00.000' WHERE id=9001",
    "UPDATE execution_entity SET startedAt='2026-99-27 01:00:00.000' WHERE id=9001",
    "UPDATE execution_data SET data='synthetic-secret invalid JSON' WHERE executionId=9000",
  ]) {
    const f = await fixture(t); f.changeDb(sql); await assert.rejects(f.recover(), e => e instanceof Error && !e.message.includes("synthetic-secret"));
    await assert.rejects(readFile(preflightClosurePath(f.root, "9000")), /ENOENT/);
  }
});

test("intent, files, changed proof and late empty directories cannot be discarded", async t => {
  for (const fault of ["intent", "export", "events", "imports", "validation", "downloads", "late", "evidence-race", "receipt-tamper"]) {
    const f = await fixture(t), effects = { events: path.join(f.root, "outputs/jackyun-browser-events", f.plan.runId), imports: path.join(f.root, "outputs/jackyun-import-runs", f.plan.runId), validation: path.join(f.root, "outputs/jackyun-export-first-validation", f.plan.runId), downloads: path.join(f.download, "jackyun", f.plan.runId) };
    if (fault === "intent") await writeFile(f.planPath, JSON.stringify({ ...f.plan, exportIntent: "inventory" }));
    if (fault === "export") await writeFile(f.planPath, JSON.stringify({ ...f.plan, exports: { inventory: {} } }));
    if (fault in effects) await mkdir(effects[fault as keyof typeof effects], { recursive: true });
    if (fault === "late") f.recovery.credentialReady = async () => { await mkdir(effects.events, { recursive: true }); return true; };
    if (fault === "evidence-race") f.recovery.credentialReady = async () => { f.changeDb("UPDATE execution_entity SET mode='webhook' WHERE id=9001"); return true; };
    if (fault === "receipt-tamper") { await f.recover(); const p = preflightClosurePath(f.root, "9000"); const r = JSON.parse(await readFile(p, "utf8")); r.replacementExecutionId = "9002"; await writeFile(p, JSON.stringify(r)); }
    await assert.rejects(f.recover());
    if (fault !== "receipt-tamper") await assert.rejects(readFile(preflightClosurePath(f.root, "9000")), /ENOENT/);
  }
});

test("uncertain login submissions and remote export results never enter generic preflight recovery", async t => {
  for (const error of ["fetch failed", "waiting_login：吉客云提交后登录状态未在限定时间内确认。", "waiting_login：吉客云登录表单变化或提交结果未确定，已停止自动提交。", "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（read）。"]) {
    assert.equal(classifyJackyunPreflightFailure(error), null);
    const f = await fixture(t, error); await f.recover();
    await assert.rejects(runJackyunExportFirstAction("plan-api", "9001", f.deps), /尚未闭合/);
    await assert.rejects(readFile(preflightClosurePath(f.root, "9000")), /ENOENT/);
  }
});

test("local page failures close only after zero-effect proof and then allow the existing hourly retry", async t => {
  const f = await fixture(t, "API_LOGIN_PAGE_NOT_UNIQUE"); await runJackyunExportFirstAction("plan-api", "9001", f.deps);
  const runApi = async () => { throw new Error("API_LOGIN_PAGE_NOT_UNIQUE: eligible=0; total=0; blank=0; jackyun=0; other=0"); };
  await assert.rejects(runJackyunExportFirstAction("export-all", "9001", { ...f.deps, runApi }), /JACKYUN_PREFLIGHT_RETRY_READY/);
  const receipt = JSON.parse(await readFile(preflightClosurePath(f.root, "9001"), "utf8")); assert.equal(receipt.version, 4);
  await assertClosedPreflight(f.root, "9001");
  await mkdir(path.join(f.root, "outputs/jackyun-import-runs/n8n-export-first-9001"), { recursive: true });
  await assert.rejects(assertClosedPreflight(f.root, "9001"));
});
