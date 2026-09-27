import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { inspectPreflightClosure, publishPreflightClosure, assertClosedPreflight, recoverySha, preflightClosurePath,
  jackyunWorkflowId, type PreflightEvidence } from "../lib/jackyun/preflight-recovery";
import { runJackyunExportFirstAction } from "../tools/jackyun-export-first-pipeline";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";

const proof: PreflightEvidence = {
  executionId: "4098", workflowId: jackyunWorkflowId, status: "error",
  startedAt: "2026-09-22T16:10:00.032Z", stoppedAt: "2026-09-22T16:10:04.434Z", retrySuccessId: null,
  lastNode: "B·接口校验与五表下载",
  runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"],
  error: "API_LOGIN_PAGE_NOT_UNIQUE", httpCode: "500",
  requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
  executionDataSha256: "b44a28839e5d2ea0741c9d0420bb3d2aefa54ac3d29ea7309501a420a352dafb", activeExecutions: 0,
};
const closedAt = "2026-09-22T16:40:00Z";
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-4098-test-"));
  const pipeline = path.join(root, "outputs/jackyun-export-first"), download = path.join(root, "downloads");
  await mkdir(pipeline, { recursive: true }); await mkdir(path.join(root, "config")); await mkdir(download);
  const runId = "n8n-export-first-4098", planPath = path.join(pipeline, `${runId}.json`), activePath = path.join(pipeline, "active.json");
  const plan = { version: 1, protocol: "2026-09-06.export-first.1", executionId: "4098", runId,
    runDate: "2026-09-23", asOfDate: "2026-09-22", baseUrl: "http://localhost:3000", createdAt: "2026-09-22T16:10:01.899Z",
    phase: "exporting", exports: {}, exportTransport: "session_api_v1" };
  await writeFile(planPath, JSON.stringify(plan, null, 2) + "\n");
  await writeFile(activePath, JSON.stringify({ runId, executionId: "4098" }));
  await writeFile(path.join(root, "config/jackyun-export-first-policy.json"), JSON.stringify({ version: plan.protocol,
    browser: { downloadDirectory: download, allowedDownloadHosts: [], controller: { profileDirectory: path.join(root, "profile") } } }));
  const deps = { root, lockDirectory: path.join(root, "lock"), profileReady: async () => true,
    now: () => new Date("2026-09-23T16:10:00Z"), request: (async () => new Response("{}")) as typeof fetch };
  return { root, pipeline, download, planPath, activePath, runId, deps };
}

test("4098 closure preserves failed-run bytes and admits only a fresh complete API execution with fresh dates", async () => {
  const f = await fixture(), original = await readFile(f.planPath), active = await readFile(f.activePath);
  await assert.rejects(runJackyunExportFirstAction("plan-api", "4100", f.deps), /尚未闭合/);
  const proposal = await inspectPreflightClosure(f.root, "4098", proof, closedAt);
  assert.equal(proposal.status, "closed_before_business");
  assert.equal(proposal.reason, "audited_4098_page_selection_before_api_exports");
  assert.equal(proposal.absentPaths.length, 4);
  await publishPreflightClosure(f.root, proposal, proof, recoverySha(JSON.stringify(proposal)));
  await assertClosedPreflight(f.root, "4098");
  assert.deepEqual(await readFile(f.planPath), original); assert.deepEqual(await readFile(f.activePath), active);
  await assert.rejects(publishPreflightClosure(f.root, proposal, proof, recoverySha(JSON.stringify(proposal))), /EEXIST/);
  await assert.rejects(runJackyunExportFirstAction("export-all", "4098", f.deps), /已经闭合/);
  await assert.rejects(runJackyunExportFirstAction("export-all", "4100", f.deps), /缺少/);
  const next = await runJackyunExportFirstAction("plan-api", "4100", f.deps);
  assert.equal(next.exportTransport, "session_api_v1");
  assert.equal(next.snapshotDate, "2026-09-24"); assert.equal(next.salesEndDate, "2026-09-23");
  assert.deepEqual(await readFile(f.planPath), original);
});

test("4098 exception rejects changed execution identity, evidence, active owner and plan", async () => {
  const f = await fixture();
  const variations: Partial<PreflightEvidence>[] = [
    { executionId: "4100" }, { workflowId: "other" }, { status: "success" }, { activeExecutions: 1 },
    { retrySuccessId: "4100" }, { executionDataSha256: "f".repeat(64) }, { error: "API_LOGIN_PAGE_NOT_UNIQUE other" },
    { httpCode: "502" }, { requestUrl: "http://127.0.0.1:5791/jackyun/export-first/import" },
    { lastNode: "D·统一导入运营管理系统" }, { runNodes: [...proof.runNodes, "D·统一导入运营管理系统"] },
    { stoppedAt: "2026-09-22T16:10:05.434Z" }, { startedAt: "2026-09-22T16:10:01.032Z" },
  ];
  for (const change of variations) await assert.rejects(inspectPreflightClosure(f.root, "4098", { ...proof, ...change }, closedAt));
  await assert.rejects(inspectPreflightClosure(f.root, "4098", proof, "2026-09-22T16:00:00Z"));
  await writeFile(f.activePath, JSON.stringify({ runId: "n8n-export-first-4100", executionId: "4100" }));
  await assert.rejects(inspectPreflightClosure(f.root, "4098", proof, closedAt));
  const changed = await fixture();
  await writeFile(changed.planPath, (await readFile(changed.planPath, "utf8")) + " ");
  await assert.rejects(inspectPreflightClosure(changed.root, "4098", proof, closedAt));
});

test("4098 closure rejects every effect before publication and late artifacts before the next execution", async () => {
  for (const afterPublish of [false, true]) for (const index of [0, 1, 2, 3]) {
    const f = await fixture(), proposal = await inspectPreflightClosure(f.root, "4098", proof, closedAt);
    if (afterPublish) await publishPreflightClosure(f.root, proposal, proof, recoverySha(JSON.stringify(proposal)));
    await mkdir(proposal.absentPaths[index], { recursive: true });
    if (afterPublish) await assert.rejects(runJackyunExportFirstAction("plan-api", "4100", f.deps), /尚未闭合/);
    else await assert.rejects(publishPreflightClosure(f.root, proposal, proof, recoverySha(JSON.stringify(proposal))), /已存在/);
  }
});

test("4098 publication fences stale evidence, policy, approved digest and receipt tampering", async () => {
  for (const fault of ["proof", "policy", "digest", "receipt"]) {
    const f = await fixture(), proposal = await inspectPreflightClosure(f.root, "4098", proof, closedAt);
    const digest = recoverySha(JSON.stringify(proposal));
    if (fault === "receipt") {
      await publishPreflightClosure(f.root, proposal, proof, digest);
      await writeFile(preflightClosurePath(f.root, "4098"), JSON.stringify({ ...proposal, reason: "audited_3134_dpapi_before_api_exports" }));
      await assert.rejects(runJackyunExportFirstAction("plan-api", "4100", f.deps), /尚未闭合/);
    } else {
      if (fault === "policy") await writeFile(path.join(f.root, "config/jackyun-export-first-policy.json"), "{}");
      await assert.rejects(publishPreflightClosure(f.root, proposal, fault === "proof" ? { ...proof, activeExecutions: 1 } : proof,
        fault === "digest" ? "f".repeat(64) : digest));
    }
  }
});

test("4098 recovery keeps the original global lock and cannot release another execution", async () => {
  const f = await fixture();
  await withJackyunRunLock({ runId: "operator-4098", purpose: "preflight_recovery", lockDirectory: f.deps.lockDirectory }, async () => {
    await assert.rejects(runJackyunExportFirstAction("plan-api", "4100", f.deps), /锁|运行|执行|占用/);
  });
  await assert.rejects(inspectPreflightClosure(f.root, "4100", { ...proof, executionId: "4100" }, closedAt));
});
