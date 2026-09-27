import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { inspectJackyunResumePermit, publishJackyunResumePermit, claimJackyunResumePermit } from "../lib/jackyun/execution-resume";
import { recoverySha, jackyunWorkflowId, type PreflightEvidence } from "../lib/jackyun/preflight-recovery";
import { runJackyunExportFirstAction, type ExportFirstDependencies } from "../tools/jackyun-export-first-pipeline";
import type { JackyunExportTaskBinding } from "../lib/jackyun/export-task";

const at = (second: number) => new Date(Date.UTC(2026, 8, 6, 13, 0, second)).toISOString();
const nodeName = "1·分仓库存：筛选并导出所有页";
const evidence: PreflightEvidence = { executionId: "844", workflowId: jackyunWorkflowId, status: "error", startedAt: at(0), stoppedAt: at(50),
  lastNode: nodeName, runNodes: ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", nodeName],
  error: "FILE_BINDING_FAILED [download_binding]: synthetic", httpCode: "500", requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export/inventory",
  executionDataSha256: "a".repeat(64), activeExecutions: 0, retrySuccessId: null };
const task: JackyunExportTaskBinding = { version: 1, taskId: "sys-123", label: "【导出任务-密文】分仓库存查询(25000条)",
  createdAt: at(5), observedAt: at(55), module: "inventory", sourceRows: 25000, sourceUrlHash: "b".repeat(64) };
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-resume-")), runId = "n8n-export-first-844";
  const directory = path.join(root, "outputs/jackyun-import-runs", runId), pipeline = path.join(root, "outputs/jackyun-export-first");
  await mkdir(directory, { recursive: true }); await mkdir(pipeline); await mkdir(path.join(root, "config"));
  const plan = { version: 1, protocol: "2026-09-06.export-first.1", runId, executionId: "844", createdAt: at(1), runDate: "2026-09-06",
    asOfDate: "2026-09-05", baseUrl: "http://localhost:3000", phase: "exporting", exports: {}, exportIntent: "inventory" };
  const controller = { version: 1, runId, policyVersion: plan.protocol, modules: { inventory: { status: "export_armed", navigationIntentAt: at(2),
    queryIntentAt: at(3), tableStableAt: at(4), exportIntentAt: at(5), expectedSourceRows: 25000 } } };
  const planPath = path.join(pipeline, `${runId}.json`), statePath = path.join(directory, "browser-controller-state.json");
  await writeFile(planPath, JSON.stringify(plan)); await writeFile(statePath, JSON.stringify(controller));
  await writeFile(path.join(pipeline, "active.json"), JSON.stringify({ runId, executionId: "844" }));
  await writeFile(path.join(root, "config/jackyun-export-first-policy.json"), JSON.stringify({ version: plan.protocol,
    browser: { downloadDirectory: path.join(root, "downloads"), allowedDownloadHosts: [], controller: { profileDirectory: path.join(root, "profile") } } }));
  const deps: ExportFirstDependencies = { root, lockDirectory: path.join(root, "lock"), now: () => new Date(at(61)),
    runBrowser: async options => { assert.equal(options.runId, runId); assert.deepEqual(options.resumeTaskBinding, task); throw new Error("bound controller reached"); } };
  return { root, runId, directory, pipeline, plan, controller, planPath, statePath, deps };
}
async function publish(f: Awaited<ReturnType<typeof fixture>>) {
  const permit = await inspectJackyunResumePermit(f.root, "844", evidence, task, at(60));
  await publishJackyunResumePermit(permit, evidence, recoverySha(JSON.stringify(permit)));
  return permit;
}
test("approved resume keeps original bytes, requires full plan, binds one execution and preserves the five-file barrier", async () => {
  const f = await fixture(), original = await readFile(f.planPath), state = await readFile(f.statePath);
  await assert.rejects(runJackyunExportFirstAction("plan", "845", f.deps), /尚未闭合/);
  await publish(f);
  await assert.rejects(runJackyunExportFirstAction("export/inventory", "845", f.deps), /尚未闭合/);
  const plan = await runJackyunExportFirstAction("plan", "845", f.deps);
  assert.equal(plan.runId, f.runId); assert.deepEqual(await readFile(f.planPath), original); assert.deepEqual(await readFile(f.statePath), state);
  await assert.rejects(runJackyunExportFirstAction("plan", "846", f.deps), /尚未闭合/);
  await assert.rejects(runJackyunExportFirstAction("import", "845", f.deps));
  await assert.rejects(runJackyunExportFirstAction("export/combos", "845", f.deps));
  await assert.rejects(runJackyunExportFirstAction("export/inventory", "845", f.deps), /bound controller reached/);
  assert.deepEqual(await claimJackyunResumePermit(f.root, "844", "845", "plan", at(62)), task);
  await assert.rejects(readFile(path.join(f.pipeline, "n8n-export-first-845.json")));
});
test("permit rejects changed original artifacts, later effects, invalid times and mismatched tasks", async () => {
  for (const fault of ["controller", "plan", "active", "policy", "extra", "download", "proof", "time", "task", "digest"]) {
    const f = await fixture(), permit = await inspectJackyunResumePermit(f.root, "844", evidence, task, at(60));
    if (fault === "controller") await writeFile(f.statePath, JSON.stringify({ ...f.controller, changed: true }));
    if (fault === "plan") await writeFile(f.planPath, JSON.stringify({ ...f.plan, phase: "importing" }));
    if (fault === "active") await writeFile(path.join(f.pipeline, "active.json"), "{}");
    if (fault === "policy") await writeFile(path.join(f.root, "config/jackyun-export-first-policy.json"), "{}");
    if (fault === "extra") await writeFile(path.join(f.directory, "export.xlsx"), "unexpected");
    if (fault === "download") await mkdir(path.join(f.root, "downloads/jackyun", f.runId), { recursive: true });
    if (fault === "time") permit.createdAt = "invalid";
    if (fault === "task") permit.task = { ...task, sourceRows: 24999 };
    await assert.rejects(publishJackyunResumePermit(permit, fault === "proof" ? { ...evidence, activeExecutions: 1 } : evidence,
      fault === "digest" ? "0".repeat(64) : recoverySha(JSON.stringify(permit))));
  }
});
test("permit cannot be reused after expiration, cross-day, artifact changes or consumption tampering", async () => {
  for (const fault of ["expired", "cross-day", "changed", "receipt"]) {
    const f = await fixture(); await publish(f);
    if (fault === "changed") await writeFile(f.statePath, JSON.stringify({ ...f.controller, changed: true }));
    if (fault === "receipt") {
      await claimJackyunResumePermit(f.root, "844", "845", "plan", at(61));
      await writeFile(path.join(f.pipeline, "resumptions", `${f.runId}.json`), "{}");
    }
    await assert.rejects(claimJackyunResumePermit(f.root, "844", "845", "plan", fault === "expired" ? at(1900) : fault === "cross-day" ? "2026-09-06T16:00:00Z" : at(61)));
  }
});
