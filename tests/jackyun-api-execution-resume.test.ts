import assert from "node:assert/strict";
import test from "node:test";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { rollingJackyunSalesStartDate } from "../lib/jackyun/sales-period";
import { claimJackyunApiResumePermit, inspectJackyunApiResumePermit, publishJackyunApiResumePermit } from "../lib/jackyun/api-execution-resume";
import type { JackyunExportTaskBinding } from "../lib/jackyun/export-task";
import { jackyunWorkflowId, recoverySha, type PreflightEvidence } from "../lib/jackyun/preflight-recovery";
import { runJackyunExportFirstAction, type ExportFirstDependencies } from "../tools/jackyun-export-first-pipeline";

const at = (second: number) => new Date(Date.UTC(2026, 8, 6, 13, 0, second)).toISOString();
const evidence: PreflightEvidence = { executionId: "2285", workflowId: jackyunWorkflowId, status: "error", startedAt: at(0), stoppedAt: at(7),
  lastNode: "B·接口校验与五表下载", runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"],
  error: "导出任务绑定条件无效。", httpCode: "500", requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
  executionDataSha256: "a".repeat(64), activeExecutions: 0, retrySuccessId: null };
const task: JackyunExportTaskBinding = { version: 1, taskId: "sys-123", label: "【导出任务-密文】分仓库存查询(25871条)",
  createdAt: at(6), observedAt: at(8), module: "inventory", sourceRows: 25871, sourceUrlHash: "b".repeat(64) };

async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-api-resume-"));
  const runId = "n8n-export-first-2285";
  const directory = path.join(root, "outputs/jackyun-import-runs", runId);
  const pipeline = path.join(root, "outputs/jackyun-export-first");
  await mkdir(directory, { recursive: true });
  await mkdir(pipeline);
  await mkdir(path.join(root, "config"));
  const plan = { version: 1, protocol: "2026-09-06.export-first.1", executionId: "2285", runId, runDate: "2026-09-06",
    asOfDate: "2026-09-05", baseUrl: "http://localhost:3000", createdAt: at(1), phase: "exporting", exports: {},
    exportTransport: "session_api_v1", exportIntent: "inventory" };
  const controller = { version: 1, runId, transport: "session_api_v1", runDate: plan.runDate, asOfDate: plan.asOfDate,
    templateSha256: "c".repeat(64), tenantId: "fixture", modules: { inventory: { status: "submitted",
      serverClock: { requestStartedAt: at(4), receivedAt: at(5), serverDate: at(6) }, preflightStartedAt: at(2), queryIntentAt: at(2),
      queryCompletedAt: at(3), sourceRows: 25871, payloadSha256: "d".repeat(64), querySha256: "e".repeat(64),
      permissionSha256: "f".repeat(64), templateSha256: "c".repeat(64), baselineIds: ["sys-100"], exportIntentAt: at(6) } } };
  const planPath = path.join(pipeline, `${runId}.json`);
  const statePath = path.join(directory, "api-controller-state.json");
  await writeFile(planPath, JSON.stringify(plan));
  await writeFile(statePath, JSON.stringify(controller));
  await writeFile(path.join(pipeline, "active.json"), JSON.stringify({ runId, executionId: "2285" }));
  await writeFile(path.join(root, "config/jackyun-export-first-policy.json"), JSON.stringify({ version: plan.protocol,
    browser: { downloadDirectory: path.join(root, "downloads"), allowedDownloadHosts: ["oss.example.invalid"],
      controller: { profileDirectory: path.join(root, "profile") } } }));
  let passedBinding: JackyunExportTaskBinding | undefined;
  const deps: ExportFirstDependencies = { root, lockDirectory: path.join(root, "lock"), now: () => new Date(at(9)),
    runApi: async options => { passedBinding = options.resumeTaskBinding; throw new Error("resumed API adapter reached"); } };
  return { root, runId, directory, pipeline, plan, controller, planPath, statePath, deps, passedBinding: () => passedBinding };
}

async function publish(f: Awaited<ReturnType<typeof fixture>>) {
  const permit = await inspectJackyunApiResumePermit(f.root, "2285", evidence, task, at(8));
  await publishJackyunApiResumePermit(permit, evidence, recoverySha(JSON.stringify(permit)));
  return permit;
}

test("rolling API resume retains its frozen range and rejects a monthly controller", async () => {
  const f = await fixture();
  const start = rollingJackyunSalesStartDate(f.plan.asOfDate);
  Object.assign(f.plan, { version: 2, salesStartDate: start });
  await writeFile(f.planPath, JSON.stringify(f.plan));
  await assert.rejects(inspectJackyunApiResumePermit(f.root, "2285", evidence, task, at(8)));
  Object.assign(f.controller, { salesStartDate: start });
  await writeFile(f.statePath, JSON.stringify(f.controller));
  await publish(f);
  const result = await runJackyunExportFirstAction("plan-api", "2300", f.deps);
  assert.equal(result.runId, f.runId);
  assert.equal(result.salesStartDate, start);
  await assert.rejects(runJackyunExportFirstAction("export-all", "2300", f.deps), /resumed API adapter reached/);
  assert.deepEqual(f.passedBinding(), task);
});

test("approved API resume preserves the submitted run and binds one full n8n execution", async () => {
  const f = await fixture();
  const planBytes = await readFile(f.planPath);
  const stateBytes = await readFile(f.statePath);
  await assert.rejects(runJackyunExportFirstAction("plan-api", "2300", f.deps), /尚未闭合/);
  await publish(f);
  const plan = await runJackyunExportFirstAction("plan-api", "2300", f.deps);
  assert.equal(plan.runId, f.runId);
  assert.deepEqual(await readFile(f.planPath), planBytes);
  assert.deepEqual(await readFile(f.statePath), stateBytes);
  await assert.rejects(runJackyunExportFirstAction("plan-api", "2302", f.deps), /尚未闭合/);
  await assert.rejects(runJackyunExportFirstAction("export-all", "2300", f.deps), /resumed API adapter reached/);
  assert.deepEqual(f.passedBinding(), task);
  assert.deepEqual(await claimJackyunApiResumePermit(f.root, "2285", "2300", "plan-api", at(9)), task);
});

test("approved API resume reuses an already-bound task after download fetch failed before file creation", async () => {
  const f = await fixture();
  const inventory = f.controller.modules.inventory as typeof f.controller.modules.inventory & { pendingTaskId?: string; binding?: JackyunExportTaskBinding };
  inventory.pendingTaskId = task.taskId;
  inventory.binding = task;
  await writeFile(f.statePath, JSON.stringify(f.controller));
  await mkdir(path.join(f.root, "downloads/jackyun", f.runId, "inventory"), { recursive: true });
  const fetchEvidence = { ...evidence, error: "fetch failed" };
  const permit = await inspectJackyunApiResumePermit(f.root, "2285", fetchEvidence, task, at(8));
  await publishJackyunApiResumePermit(permit, fetchEvidence, recoverySha(JSON.stringify(permit)));
  await runJackyunExportFirstAction("plan-api", "2300", f.deps);
  await assert.rejects(runJackyunExportFirstAction("export-all", "2300", f.deps), /resumed API adapter reached/);
  assert.deepEqual(f.passedBinding(), task);
});

test("bound-task fetch recovery rejects an unbound task, changed failure or any partial download", async () => {
  for (const fault of ["pending", "binding", "failure", "file"]) {
    const f = await fixture();
    const inventory = f.controller.modules.inventory as typeof f.controller.modules.inventory & { pendingTaskId?: string; binding?: JackyunExportTaskBinding };
    inventory.pendingTaskId = fault === "pending" ? "sys-999" : task.taskId;
    inventory.binding = fault === "binding" ? { ...task, sourceRows: task.sourceRows - 1 } : task;
    await writeFile(f.statePath, JSON.stringify(f.controller));
    const directory = path.join(f.root, "downloads/jackyun", f.runId, "inventory");
    await mkdir(directory, { recursive: true });
    if (fault === "file") await writeFile(path.join(directory, "partial.xlsx"), "partial");
    await assert.rejects(inspectJackyunApiResumePermit(f.root, "2285",
      { ...evidence, error: fault === "failure" ? "network timeout" : "fetch failed" }, task, at(8)));
  }
});

test("API resume rejects changed evidence, extra effects and mismatched task identity", async () => {
  for (const fault of ["controller", "plan", "active", "policy", "extra", "download", "proof", "task", "digest"]) {
    const f = await fixture();
    const permit = await inspectJackyunApiResumePermit(f.root, "2285", evidence, task, at(8));
    if (fault === "controller") await writeFile(f.statePath, JSON.stringify({ ...f.controller, changed: true }));
    if (fault === "plan") await writeFile(f.planPath, JSON.stringify({ ...f.plan, phase: "importing" }));
    if (fault === "active") await writeFile(path.join(f.pipeline, "active.json"), "{}");
    if (fault === "policy") await writeFile(path.join(f.root, "config/jackyun-export-first-policy.json"), "{}");
    if (fault === "extra") await writeFile(path.join(f.directory, "unexpected.xlsx"), "x");
    if (fault === "download") await mkdir(path.join(f.root, "downloads/jackyun", f.runId), { recursive: true });
    if (fault === "task") permit.task = { ...task, sourceRows: 25870 };
    await assert.rejects(publishJackyunApiResumePermit(permit, fault === "proof" ? { ...evidence, activeExecutions: 1 } : evidence,
      fault === "digest" ? "0".repeat(64) : recoverySha(JSON.stringify(permit))));
  }
});

test("API resume permit expires, stays single-consumer and detects later artifact changes", async () => {
  for (const fault of ["expired", "cross-day", "changed", "receipt"]) {
    const f = await fixture();
    await publish(f);
    if (fault === "changed") await writeFile(f.statePath, JSON.stringify({ ...f.controller, changed: true }));
    if (fault === "receipt") {
      await claimJackyunApiResumePermit(f.root, "2285", "2300", "plan-api", at(9));
      await writeFile(path.join(f.pipeline, "api-resumptions", f.runId + ".json"), "{}");
    }
    await assert.rejects(claimJackyunApiResumePermit(f.root, "2285", "2300", "plan-api",
      fault === "expired" ? at(1900) : fault === "cross-day" ? "2026-09-06T16:00:00Z" : at(9)));
  }
});
