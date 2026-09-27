import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { AppPrincipal } from "../lib/auth/authorization";
import type { D1Database as SalesDatabase } from "../lib/database/d1";
import { installDjangoAccessControlFixture } from "./access-control-service-fixture";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __aiAgentWorkflowTestEnv?: typeof testEnvironment }).__aiAgentWorkflowTestEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__aiAgentWorkflowTestEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const {
  cancelAiAgentJob,
  cancelAiWorkflowRun,
  createExecutorAdmission,
  createAiAgentJob,
  createAiWorkflowRun,
  ensureAiAgentWorkflowSchema,
  getAiAgentJob,
  getAiWorkflowRun,
  requireAiExpectedVersionBody,
  resumeAiAgentJob,
  resumeAiWorkflowRun,
  reviewAiWorkflowNode,
  runNextAiAgentMicrostep,
  runNextAiWorkflowMicrostep,
  validateAiWorkflowGraph,
} = await import("./legacy/ai/agent-workflows").then(async (service) => ({
  ...service,
  ensureAiAgentWorkflowSchema: (await import("./legacy/ai/agent-workflow-schema")).ensureAiAgentWorkflowSchema,
}));
const { ensureAuthorizationSchema } = await import("./legacy/auth/authorization");
const {
  AI_FORMAL_WORKFLOW_INACTIVE_TTL_DAYS,
  selectNextWorkflowExecutorAdmission,
} = await import("./legacy/ai/agent-executor-admission");

const owner: AppPrincipal = {
  email: "owner@example.com",
  displayName: "Owner",
  role: "operator",
  scope: null,
};

const otherOwner: AppPrincipal = {
  email: "other@example.com",
  displayName: "Other",
  role: "operator",
  scope: null,
};

const executorAdmission = createExecutorAdmission({
  modelId: "test-provider-model",
  modelVersion: 7,
  allowedTools: ["get_sales_summary", "search_personal_memory"],
  toolPolicyDigest: "a".repeat(64),
});

const otherExecutorAdmission = createExecutorAdmission({
  modelId: "test-provider-model",
  modelVersion: 8,
  allowedTools: ["get_sales_summary"],
  toolPolicyDigest: "b".repeat(64),
});

test("0086 migration and runtime schema are mutually upgrade-safe", async () => {
  const migration = await readFile(new URL("../drizzle/0086_ai_agent_workflows.sql", import.meta.url), "utf8");
  for (const order of ["migration-first", "runtime-first"] as const) {
    const sqlite = new DatabaseSync(":memory:");
    sqlite.exec("PRAGMA foreign_keys = ON");
    const db = sqliteAdapter(sqlite);
    try {
      if (order === "migration-first") {
        applyMigration(sqlite, migration);
        await ensureAiAgentWorkflowSchema(db);
      } else {
        await ensureAiAgentWorkflowSchema(db);
        applyMigration(sqlite, migration);
      }
      for (const table of [
        "ai_agent_jobs",
        "ai_agent_checkpoints",
        "ai_agent_events",
        "ai_workflow_runs",
        "ai_workflow_node_runs",
        "ai_workflow_events",
      ]) {
        assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(table), table);
      }
      for (const index of [
        "ai_agent_jobs_runnable_idx",
        "ai_agent_jobs_workflow_node_uq",
        "ai_workflow_runs_runnable_idx",
        "ai_workflow_node_runs_agent_job_uq",
      ]) {
        assert.ok(sqlite.prepare("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?").get(index), index);
      }
      for (const table of ["ai_agent_jobs", "ai_workflow_runs"] as const) {
        const columns = sqlite.prepare(`PRAGMA table_info(${table})`).all() as Array<{
          name: string;
          type: string;
          dflt_value: string | number | null;
        }>;
        const byName = new Map(columns.map((column) => [column.name, column]));
        assert.equal(byName.get("model_id")?.type, "TEXT");
        assert.equal(byName.get("model_version")?.type, "INTEGER");
        assert.equal(Number(byName.get("model_version")?.dflt_value), 0);
        assert.equal(byName.get("allowed_tools_json")?.type, "TEXT");
        assert.equal(byName.get("tool_policy_digest")?.type, "TEXT");
        assert.equal(byName.get("provider_round_count")?.type, "INTEGER");
        assert.equal(byName.get("tool_call_count")?.type, "INTEGER");
        assert.equal(byName.get("provider_dispatch_started_at")?.type, "TEXT");
      }
    } finally {
      sqlite.close();
    }
  }
});

test("runtime old-schema upgrade fails closed for formal work without an admission snapshot", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const migration = await readFile(new URL("../drizzle/0086_ai_agent_workflows.sql", import.meta.url), "utf8");
    applyMigration(sqlite, migration);
    sqlite.prepare(`INSERT INTO ai_agent_jobs (
        id, owner_email, client_request_id, request_digest, scope_json, task
      ) VALUES ('legacy-agent', 'owner@example.com', 'legacy-agent-request', 'digest', '{}', 'legacy')`).run();
    sqlite.prepare(`INSERT INTO ai_workflow_runs (
        id, owner_email, client_request_id, request_digest, scope_json, name, graph_json, graph_digest
      ) VALUES ('legacy-workflow', 'owner@example.com', 'legacy-workflow-request', 'digest', '{}',
        'legacy', '{"nodes":[]}', 'graph-digest')`).run();

    await ensureAiAgentWorkflowSchema(db);

    const legacyAgent = sqlite.prepare(
      "SELECT status, error_code, model_version FROM ai_agent_jobs WHERE id = 'legacy-agent'",
    ).get() as { status: string; error_code: string; model_version: number };
    assert.equal(legacyAgent.status, "failed");
    assert.equal(legacyAgent.error_code, "executor_admission_missing");
    assert.equal(legacyAgent.model_version, 0);
    const legacyWorkflow = sqlite.prepare(
      "SELECT status, error_code, model_version FROM ai_workflow_runs WHERE id = 'legacy-workflow'",
    ).get() as { status: string; error_code: string; model_version: number };
    assert.equal(legacyWorkflow.status, "failed");
    assert.equal(legacyWorkflow.error_code, "executor_admission_missing");
    assert.equal(legacyWorkflow.model_version, 0);
  } finally {
    sqlite.close();
  }
});

test("Production admission requires a server-issued immutable model and tool snapshot", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    await assert.rejects(
      createAiAgentJob({
        clientRequestId: "agent-disabled-1",
        task: "不应进入永久队列",
        input: {},
      }, owner, db),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "service_unavailable",
    );
    await assert.rejects(
      createAiWorkflowRun({
        clientRequestId: "workflow-disabled-1",
        name: "不应进入永久队列",
        graph: { nodes: [{ key: "agent", type: "agent", instruction: "执行", dependsOn: [] }] },
      }, owner, db),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "service_unavailable",
    );
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_agent_jobs").get() as { count: number }).count, 0);
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_workflow_runs").get() as { count: number }).count, 0);

    await assert.rejects(
      createAiAgentJob({
        clientRequestId: "agent-spoofed-admission-1",
        task: "不能使用结构相同的请求对象伪造准入",
        input: {},
      }, owner, db, { executorAdmission: {
        modelId: executorAdmission.modelId,
        modelVersion: executorAdmission.modelVersion,
        allowedTools: executorAdmission.allowedTools,
        toolPolicyDigest: executorAdmission.toolPolicyDigest,
      } }),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "service_unavailable",
    );

    const admitted = await createAiWorkflowRun({
      clientRequestId: "workflow-admitted-formal-1",
      name: "已固定执行准入的正式工作流",
      graph: { nodes: [{ key: "agent", type: "agent", instruction: "执行", dependsOn: [] }] },
    }, owner, db, { executorAdmission });
    assert.equal(admitted.item.modelId, executorAdmission.modelId);
    assert.equal(admitted.item.modelVersion, executorAdmission.modelVersion);
    assert.deepEqual(admitted.item.allowedTools, executorAdmission.allowedTools);
    assert.equal(admitted.item.toolPolicyDigest, executorAdmission.toolPolicyDigest);
    assert.equal((await runNextAiWorkflowMicrostep({ db })).status, "idle");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission: otherExecutorAdmission })).status, "idle");
    const childCreated = await runNextAiWorkflowMicrostep({ db, executorAdmission });
    assert.equal(childCreated.status, "child_created");
    assert.ok(childCreated.childJobId);
    const child = await getAiAgentJob(childCreated.childJobId!, owner, db);
    assert.equal(child.modelId, executorAdmission.modelId);
    assert.equal(child.modelVersion, executorAdmission.modelVersion);
    assert.deepEqual(child.allowedTools, executorAdmission.allowedTools);
    assert.equal(child.toolPolicyDigest, executorAdmission.toolPolicyDigest);
  } finally {
    sqlite.close();
  }
});

test("Agent jobs are owner-only, scope-fenced, and idempotent by exact payload", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const first = await createAiAgentJob({
      clientRequestId: "agent-idempotency-1",
      task: "汇总已验证的经营数据",
      input: { period: "2026-08" },
    }, owner, db, { executorAdmission });
    assert.equal(first.replayed, false);
    const replay = await createAiAgentJob({
      clientRequestId: "agent-idempotency-1",
      task: "汇总已验证的经营数据",
      input: { period: "2026-08" },
    }, owner, db, { executorAdmission });
    assert.equal(replay.replayed, true);
    assert.equal(replay.item.id, first.item.id);
    await assert.rejects(
      createAiAgentJob({
        clientRequestId: "agent-idempotency-1",
        task: "汇总已验证的经营数据",
        input: { period: "2026-08" },
      }, owner, db, { executorAdmission: otherExecutorAdmission }),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "conflict",
    );
    await assert.rejects(
      createAiAgentJob({
        clientRequestId: "agent-idempotency-1",
        task: "另一个任务",
        input: { period: "2026-08" },
      }, owner, db, { executorAdmission }),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "conflict",
    );
    await assert.rejects(
      getAiAgentJob(first.item.id, otherOwner, db),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "not_found",
    );

    const scoped: AppPrincipal = {
      ...owner,
      scope: { warehouses: ["上海仓"], channels: ["京东"], platforms: ["京东"] },
    };
    const scopedJob = await createAiAgentJob({
      clientRequestId: "agent-scope-1",
      task: "范围内任务",
      input: {},
    }, scoped, db, { executorAdmission });
    const narrowed: AppPrincipal = {
      ...scoped,
      scope: { warehouses: [], channels: ["京东"], platforms: ["京东"] },
    };
    await assert.rejects(
      getAiAgentJob(scopedJob.item.id, narrowed, db),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "not_found",
    );
  } finally {
    sqlite.close();
  }
});

test("Agent microsteps checkpoint, pause/resume with CAS, complete, and reject active HTML", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const created = await createAiAgentJob({
      clientRequestId: "agent-microsteps-1",
      task: "分两步形成结构化结果",
      input: { value: 7 },
    }, owner, db, { executorAdmission });
    convertToDeterministicJob(sqlite, created.item.id);
    const first = await runNextAiAgentMicrostep((context) => {
      assert.equal(context.stepIndex, 0);
      return { kind: "checkpoint", state: { stage: "prepared" } };
    }, { db });
    assert.equal(first.status, "checkpointed");
    const second = await runNextAiAgentMicrostep((context) => {
      assert.deepEqual(context.state, { stage: "prepared" });
      return { kind: "complete", output: { answer: 7 } };
    }, { db });
    assert.equal(second.status, "completed");
    const completed = await getAiAgentJob(created.item.id, owner, db);
    assert.equal(completed.status, "completed");
    assert.deepEqual(completed.output, { answer: 7 });
    assert.equal(completed.checkpoints.length, 2);

    const pausedCreated = await createAiAgentJob({
      clientRequestId: "agent-pause-1",
      task: "需要人工恢复",
      input: {},
    }, owner, db, { executorAdmission });
    convertToDeterministicJob(sqlite, pausedCreated.item.id);
    assert.equal((await runNextAiAgentMicrostep(() => ({
      kind: "pause",
      code: "source_not_ready",
      message: "来源尚未就绪",
      state: { cursor: 1 },
    }), { db })).status, "paused");
    const paused = await getAiAgentJob(pausedCreated.item.id, owner, db);
    assert.equal(paused.status, "paused");
    const resumed = await resumeAiAgentJob(paused.id, paused.version, owner, db);
    assert.equal(resumed.status, "queued");
    await assert.rejects(
      resumeAiAgentJob(paused.id, paused.version, owner, db),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "version_conflict",
    );
    assert.equal((await runNextAiAgentMicrostep(() => ({ kind: "complete", output: { resumed: true } }), { db })).status, "completed");

    const unsafeCreated = await createAiAgentJob({
      clientRequestId: "agent-unsafe-output-1",
      task: "只允许被动 JSON",
      input: {},
    }, owner, db, { executorAdmission });
    convertToDeterministicJob(sqlite, unsafeCreated.item.id);
    const unsafe = await runNextAiAgentMicrostep(() => ({
      kind: "complete",
      output: { html: "<script>alert(1)</script>" },
    }), { db });
    assert.equal(unsafe.status, "failed");
    const unsafeJob = await getAiAgentJob(unsafeCreated.item.id, owner, db);
    assert.equal(unsafeJob.errorCode, "invalid_microstep_result");
    assert.equal(unsafeJob.output, null);
  } finally {
    sqlite.close();
  }
});

test("Agent lease token and epoch fence reject a late completion after expiry", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const created = await createAiAgentJob({
      clientRequestId: "agent-fence-1",
      task: "验证迟到写 fencing",
      input: {},
    }, owner, db, { executorAdmission });
    convertToDeterministicJob(sqlite, created.item.id);
    const late = await runNextAiAgentMicrostep((context) => {
      sqlite.prepare("UPDATE ai_agent_jobs SET lease_expires_at = '2000-01-01 00:00:00' WHERE id = ?").run(context.jobId);
      return { kind: "complete", output: { stale: true } };
    }, { db });
    assert.equal(late.status, "lost");
    assert.equal((await getAiAgentJob(created.item.id, owner, db)).output, null);

    const recovered = await runNextAiAgentMicrostep(() => ({ kind: "complete", output: { stale: false } }), { db });
    assert.equal(recovered.status, "completed");
    const final = await getAiAgentJob(created.item.id, owner, db);
    assert.deepEqual(final.output, { stale: false });
    assert.equal(final.attemptCount, 2);

    const dispatched = await createAiAgentJob({
      clientRequestId: "agent-provider-boundary-1",
      task: "验证供应商派发边界不重放",
      input: {},
    }, owner, db, { executorAdmission });
    convertToDeterministicJob(sqlite, dispatched.item.id);
    const unknown = await runNextAiAgentMicrostep((context) => {
      sqlite.prepare(`UPDATE ai_agent_jobs SET lease_expires_at = '2000-01-01 00:00:00',
        provider_dispatch_started_at = CURRENT_TIMESTAMP WHERE id = ?`).run(context.jobId);
      return { kind: "complete", output: { mustNotCommit: true } };
    }, { db });
    assert.equal(unknown.status, "lost");
    assert.equal((await runNextAiAgentMicrostep(() => ({
      kind: "complete",
      output: { mustNotReplay: true },
    }), { db })).status, "idle");
    const failedUnknown = await getAiAgentJob(dispatched.item.id, owner, db);
    assert.equal(failedUnknown.status, "failed");
    assert.equal(failedUnknown.retryable, false);
    assert.equal(failedUnknown.errorCode, "provider_dispatch_unknown");
    assert.equal(failedUnknown.output, null);
  } finally {
    sqlite.close();
  }
});

test("Agent and workflow cancel mutations require exact CAS versions", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    assert.equal(requireAiExpectedVersionBody({ expectedVersion: 1 }), 1);
    assert.throws(() => requireAiExpectedVersionBody({ expectedVersion: 1, force: true }), /未允许字段/);
    const agent = await createAiAgentJob({
      clientRequestId: "agent-cancel-1",
      task: "待取消",
      input: {},
    }, owner, db, { executorAdmission });
    const cancelledAgent = await cancelAiAgentJob(agent.item.id, agent.item.version, owner, db);
    assert.equal(cancelledAgent.status, "cancelled");
    await assert.rejects(cancelAiAgentJob(agent.item.id, agent.item.version, owner, db), /刷新后重试/);

    const workflow = await createAiWorkflowRun({
      clientRequestId: "workflow-cancel-1",
      name: "待取消工作流",
      dryRun: true,
      graph: { nodes: [{ key: "only", type: "human_review", instruction: "确认", dependsOn: [] }] },
    }, owner, db, { executorAdmission });
    const cancelledWorkflow = await cancelAiWorkflowRun(workflow.item.id, workflow.item.version, owner, db);
    assert.equal(cancelledWorkflow.status, "cancelled");
    assert.equal(cancelledWorkflow.nodes[0]!.status, "cancelled");
    await assert.rejects(cancelAiWorkflowRun(workflow.item.id, workflow.item.version, owner, db), /刷新后重试/);
  } finally {
    sqlite.close();
  }
});

test("Workflow graph validation is strict, bounded, and rejects cycles or executable node types", () => {
  const graph = validateAiWorkflowGraph({ nodes: [
    { key: "finish", type: "agent", instruction: "汇总", dependsOn: ["start"] },
    { key: "start", type: "agent", instruction: "分析", dependsOn: [] },
  ] });
  assert.deepEqual(graph.nodes.map((node) => node.key), ["start", "finish"]);
  assert.throws(() => validateAiWorkflowGraph({ nodes: [
    { key: "a", type: "agent", instruction: "A", dependsOn: ["b"] },
    { key: "b", type: "agent", instruction: "B", dependsOn: ["a"] },
  ] }), /无环 DAG/);
  assert.throws(() => validateAiWorkflowGraph({ nodes: [
    { key: "sandbox", type: "tool", instruction: "执行代码", dependsOn: [] },
  ] }), /仅允许 agent 或 human_review/);
  assert.throws(() => validateAiWorkflowGraph({ nodes: [
    { key: "unsafe", type: "agent", instruction: "执行", dependsOn: [], code: "eval('x')" },
  ] }), /未允许字段/);
  assert.throws(() => validateAiWorkflowGraph({ nodes: [
    { key: "unsafe_html", type: "agent", instruction: "<script>alert(1)</script>", dependsOn: [] },
  ] }), /可执行 HTML/);
});

test("Dry-run preserves DAG shape, skips human review, and creates no Agent children", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const created = await createAiWorkflowRun({
      clientRequestId: "workflow-dry-run-1",
      name: "三节点 dry-run",
      dryRun: true,
      graph: { nodes: [
        { key: "research", type: "agent", instruction: "研究", dependsOn: [] },
        { key: "approval", type: "human_review", instruction: "审批", dependsOn: ["research"] },
        { key: "report", type: "agent", instruction: "报告", dependsOn: ["approval"] },
      ] },
    }, owner, db);
    for (let index = 0; index < 3; index += 1) {
      assert.equal((await runNextAiWorkflowMicrostep({ db })).status, "dry_run_node");
    }
    const final = await getAiWorkflowRun(created.item.id, owner, db);
    assert.equal(final.status, "completed");
    assert.deepEqual(final.nodes.map((node) => node.status), ["skipped", "skipped", "skipped"]);
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_agent_jobs").get() as { count: number }).count, 0);
  } finally {
    sqlite.close();
  }
});

test("Workflow lease recovery requeues only before the provider dispatch boundary", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const unknown = await createAiWorkflowRun({
      clientRequestId: "workflow-provider-boundary-1",
      name: "派发结果未知",
      dryRun: true,
      graph: { nodes: [{ key: "only", type: "agent", instruction: "检查", dependsOn: [] }] },
    }, owner, db);
    sqlite.prepare(`UPDATE ai_workflow_runs SET status = 'running',
      lease_token = 'expired', lease_epoch = 1, lease_expires_at = '2000-01-01 00:00:00',
      provider_dispatch_started_at = CURRENT_TIMESTAMP WHERE id = ?`).run(unknown.item.id);
    assert.equal((await runNextAiWorkflowMicrostep({ db })).status, "idle");
    const failed = await getAiWorkflowRun(unknown.item.id, owner, db);
    assert.equal(failed.status, "failed");
    assert.equal(failed.retryable, false);
    assert.equal(failed.errorCode, "provider_dispatch_unknown");
    assert.deepEqual(failed.nodes.map((node) => node.status), ["failed"]);

    const safe = await createAiWorkflowRun({
      clientRequestId: "workflow-pre-dispatch-expiry-1",
      name: "派发前可恢复",
      dryRun: true,
      graph: { nodes: [{ key: "only", type: "agent", instruction: "检查", dependsOn: [] }] },
    }, owner, db);
    sqlite.prepare(`UPDATE ai_workflow_runs SET status = 'running',
      lease_token = 'expired', lease_epoch = 1, lease_expires_at = '2000-01-01 00:00:00',
      provider_dispatch_started_at = NULL WHERE id = ?`).run(safe.item.id);
    assert.equal((await runNextAiWorkflowMicrostep({ db })).status, "dry_run_node");
    const recovered = await getAiWorkflowRun(safe.item.id, owner, db);
    assert.equal(recovered.status, "completed");
    assert.equal(recovered.errorCode, "lease_expired_requeued");
  } finally {
    sqlite.close();
  }
});

test("Sequential multi-Agent workflow waits for review and never overlaps child jobs", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const created = await createAiWorkflowRun({
      clientRequestId: "workflow-sequential-1",
      name: "研究、复核、汇总",
      graph: { nodes: [
        { key: "research", type: "agent", instruction: "形成研究数据", dependsOn: [] },
        { key: "approval", type: "human_review", instruction: "复核研究数据", dependsOn: ["research"] },
        { key: "report", type: "agent", instruction: "形成最终结构化结果", dependsOn: ["approval"] },
      ] },
      input: { period: "2026-08" },
    }, owner, db, { executorAdmission });

    const childOne = await runNextAiWorkflowMicrostep({ db, executorAdmission });
    assert.equal(childOne.status, "child_created");
    const admittedChildOne = await getAiAgentJob(childOne.childJobId!, owner, db);
    assert.equal(admittedChildOne.modelId, executorAdmission.modelId);
    assert.equal(admittedChildOne.modelVersion, executorAdmission.modelVersion);
    assert.deepEqual(admittedChildOne.allowedTools, executorAdmission.allowedTools);
    assert.equal(admittedChildOne.toolPolicyDigest, executorAdmission.toolPolicyDigest);
    convertToDeterministicJob(sqlite, childOne.childJobId!);
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_agent_jobs WHERE status IN ('queued','running')").get() as { count: number }).count, 1);
    assert.equal((await runNextAiAgentMicrostep((context) => {
      assert.deepEqual(context.input, { dependencies: {}, workflowInput: { period: "2026-08" } });
      return { kind: "complete", output: { finding: 42 } };
    }, { db })).status, "completed");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "node_completed");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "waiting_review");

    const waiting = await getAiWorkflowRun(created.item.id, owner, db);
    const approval = waiting.nodes.find((node) => node.key === "approval")!;
    assert.equal(waiting.status, "waiting_review");
    const reviewed = await reviewAiWorkflowNode(waiting.id, approval.key, {
      decision: "approve",
      comment: "数据口径已核对",
      expectedVersion: approval.version,
    }, owner, db);
    assert.equal(reviewed.status, "queued");

    const childTwo = await runNextAiWorkflowMicrostep({ db, executorAdmission });
    assert.equal(childTwo.status, "child_created");
    convertToDeterministicJob(sqlite, childTwo.childJobId!);
    assert.notEqual(childTwo.childJobId, childOne.childJobId);
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_agent_jobs WHERE status IN ('queued','running')").get() as { count: number }).count, 1);
    assert.equal((await runNextAiAgentMicrostep((context) => {
      assert.deepEqual(context.input, {
        dependencies: { approval: { comment: "数据口径已核对", decision: "approve" } },
        workflowInput: { period: "2026-08" },
      });
      return { kind: "complete", output: { report: "ready" } };
    }, { db })).status, "completed");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "completed");

    const final = await getAiWorkflowRun(created.item.id, owner, db);
    assert.equal(final.status, "completed");
    assert.deepEqual(final.nodes.map((node) => node.status), ["completed", "completed", "completed"]);
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_agent_jobs WHERE workflow_run_id = ?").get(final.id) as { count: number }).count, 2);
  } finally {
    sqlite.close();
  }
});

test("scheduled workflow maintenance fails waiting review runs after owner revocation and releases quota", async () => {
  const originalScope = {
    warehouses: ["华东仓", "华南仓"],
    channels: ["京东"],
    platforms: ["京东"],
  };
  const scopedOwner: AppPrincipal = { ...owner, scope: originalScope };
  const cases = [
    {
      name: "disabled",
      invalidate(sqlite: DatabaseSync) {
        sqlite.prepare("UPDATE app_users SET status = 'disabled' WHERE email = ?").run(scopedOwner.email);
      },
    },
    {
      name: "viewer",
      invalidate(sqlite: DatabaseSync) {
        sqlite.prepare("UPDATE app_users SET role = 'viewer' WHERE email = ?").run(scopedOwner.email);
      },
    },
    {
      name: "scope-narrowed",
      invalidate(sqlite: DatabaseSync) {
        sqlite.prepare("UPDATE app_users SET scope_json = ? WHERE email = ?").run(JSON.stringify({
          warehouses: ["华东仓"],
          channels: ["京东"],
          platforms: ["京东"],
        }), scopedOwner.email);
      },
    },
  ] as const;

  for (const scenario of cases) {
    const sqlite = new DatabaseSync(":memory:");
    sqlite.exec("PRAGMA foreign_keys = ON");
    const db = sqliteAdapter(sqlite);
    try {
      installDjangoAccessControlFixture(sqlite);
      await ensureAuthorizationSchema(db);
      sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
        VALUES (?, ?, 'operator', 'active', ?)`)
        .run(scopedOwner.email, scopedOwner.displayName, JSON.stringify(originalScope));
      const runIds: string[] = [];
      for (let index = 0; index < 4; index += 1) {
        const created = await createAiWorkflowRun({
          clientRequestId: `workflow-auth-maintenance-${scenario.name}-${index}`,
          name: `等待复核 ${scenario.name} ${index}`,
          graph: { nodes: [{
            key: "approval",
            type: "human_review",
            instruction: "确认是否继续",
            dependsOn: [],
          }] },
        }, scopedOwner, db, { executorAdmission });
        runIds.push(created.item.id);
        assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "waiting_review");
      }

      scenario.invalidate(sqlite);
      assert.equal(await selectNextWorkflowExecutorAdmission(db), null);
      for (const runId of runIds) {
        const failed = await getAiWorkflowRun(runId, scopedOwner, db);
        assert.equal(failed.status, "failed", scenario.name);
        assert.equal(failed.errorCode, "authorization_revoked", scenario.name);
        assert.deepEqual(failed.nodes.map((node) => node.status), ["failed"], scenario.name);
      }
      assert.equal((sqlite.prepare(`SELECT COUNT(*) AS count FROM ai_workflow_runs
        WHERE owner_email = ? AND status IN ('queued','running','waiting_review','paused')`)
        .get(scopedOwner.email) as { count: number }).count, 0);
      assert.equal((sqlite.prepare(`SELECT COUNT(*) AS count FROM ai_workflow_events
        WHERE actor_email = 'system:workflow-authorization-maintenance'
          AND event_type = 'authorization_revoked'`).get() as { count: number }).count, 4);

      sqlite.prepare(`UPDATE app_users SET role = 'operator', status = 'active', scope_json = ?
        WHERE email = ?`).run(JSON.stringify(originalScope), scopedOwner.email);
      const replacement = await createAiWorkflowRun({
        clientRequestId: `workflow-auth-maintenance-${scenario.name}-replacement`,
        name: `撤权恢复后的新任务 ${scenario.name}`,
        graph: { nodes: [{
          key: "approval",
          type: "human_review",
          instruction: "确认是否继续",
          dependsOn: [],
        }] },
      }, scopedOwner, db, { executorAdmission });
      assert.equal(replacement.item.status, "queued");
    } finally {
      sqlite.close();
    }
  }
});

test("scheduled workflow maintenance fails a malformed immutable scope even for an unrestricted owner", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys = ON");
  const db = sqliteAdapter(sqlite);
  try {
    installDjangoAccessControlFixture(sqlite);
    await ensureAuthorizationSchema(db);
    sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
      VALUES (?, ?, 'operator', 'active', NULL)`).run(owner.email, owner.displayName);
    const created = await createAiWorkflowRun({
      clientRequestId: "workflow-invalid-scope-maintenance",
      name: "损坏范围快照",
      graph: { nodes: [{
        key: "approval",
        type: "human_review",
        instruction: "确认是否继续",
        dependsOn: [],
      }] },
    }, owner, db, { executorAdmission });
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "waiting_review");
    sqlite.prepare("UPDATE ai_workflow_runs SET scope_json = '{}' WHERE id = ?").run(created.item.id);

    assert.equal(await selectNextWorkflowExecutorAdmission(db), null);
    const failed = await getAiWorkflowRun(created.item.id, owner, db);
    assert.equal(failed.status, "failed");
    assert.equal(failed.errorCode, "scope_invalid");
    assert.deepEqual(failed.nodes.map((node) => node.status), ["failed"]);
  } finally {
    sqlite.close();
  }
});

test("scheduled workflow maintenance expires only stale waiting or paused runs and closes active children", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys = ON");
  const db = sqliteAdapter(sqlite);
  try {
    installDjangoAccessControlFixture(sqlite);
    await ensureAuthorizationSchema(db);
    sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
      VALUES (?, ?, 'operator', 'active', NULL)`).run(owner.email, owner.displayName);

    const fresh = await createAiWorkflowRun({
      clientRequestId: "workflow-inactive-ttl-fresh",
      name: "仍在有效复核期",
      graph: { nodes: [{
        key: "approval",
        type: "human_review",
        instruction: "确认是否继续",
        dependsOn: [],
      }] },
    }, owner, db, { executorAdmission });
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "waiting_review");

    const stale = await createAiWorkflowRun({
      clientRequestId: "workflow-inactive-ttl-paused-child",
      name: "遗留暂停任务",
      graph: { nodes: [{ key: "agent", type: "agent", instruction: "等待来源", dependsOn: [] }] },
    }, owner, db, { executorAdmission });
    const child = await runNextAiWorkflowMicrostep({ db, executorAdmission });
    assert.equal(child.status, "child_created");
    convertToDeterministicJob(sqlite, child.childJobId!);
    assert.equal((await runNextAiAgentMicrostep(() => ({
      kind: "pause",
      code: "source_not_ready",
      message: "来源尚未就绪",
    }), { db })).status, "paused");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "paused");
    sqlite.prepare(`UPDATE ai_workflow_runs
      SET updated_at = datetime('now', ?)
      WHERE id = ?`).run(`-${AI_FORMAL_WORKFLOW_INACTIVE_TTL_DAYS + 1} days`, stale.item.id);

    assert.equal(await selectNextWorkflowExecutorAdmission(db), null);
    const expired = await getAiWorkflowRun(stale.item.id, owner, db);
    assert.equal(expired.status, "failed");
    assert.equal(expired.errorCode, "workflow_inactive_expired");
    assert.deepEqual(expired.nodes.map((node) => node.status), ["failed"]);
    const failedChild = await getAiAgentJob(child.childJobId!, owner, db);
    assert.equal(failedChild.status, "failed");
    assert.equal(failedChild.errorCode, "workflow_inactive_expired");
    assert.equal(failedChild.retryable, false);
    assert.equal((sqlite.prepare(`SELECT COUNT(*) AS count FROM ai_agent_events
      WHERE job_id = ? AND actor_email = 'system:workflow-lifecycle-maintenance'
        AND event_type = 'workflow_inactive_expired'`).get(child.childJobId!) as { count: number }).count, 1);

    const untouched = await getAiWorkflowRun(fresh.item.id, owner, db);
    assert.equal(untouched.status, "waiting_review");
    assert.equal(untouched.errorCode, "");
  } finally {
    sqlite.close();
  }
});

test("Workflow resume uses CAS and resumes the existing retryable child instead of deriving a new job", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  try {
    const created = await createAiWorkflowRun({
      clientRequestId: "workflow-resume-1",
      name: "可恢复工作流",
      graph: { nodes: [
        { key: "agent", type: "agent", instruction: "等待来源后完成", dependsOn: [] },
      ] },
    }, owner, db, { executorAdmission });
    const child = await runNextAiWorkflowMicrostep({ db, executorAdmission });
    assert.equal(child.status, "child_created");
    convertToDeterministicJob(sqlite, child.childJobId!);
    assert.equal((await runNextAiAgentMicrostep(() => ({
      kind: "pause",
      code: "source_not_ready",
      message: "来源尚未就绪",
    }), { db })).status, "paused");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "paused");
    const paused = await getAiWorkflowRun(created.item.id, owner, db);
    assert.equal(paused.retryable, true);
    const resumed = await resumeAiWorkflowRun(paused.id, paused.version, owner, db);
    assert.equal(resumed.status, "queued");
    await assert.rejects(
      resumeAiWorkflowRun(paused.id, paused.version, owner, db),
      (error: unknown) => error instanceof Error && "code" in error && error.code === "version_conflict",
    );
    assert.equal((await runNextAiAgentMicrostep(() => ({ kind: "complete", output: { ready: true } }), { db })).status, "completed");
    assert.equal((await runNextAiWorkflowMicrostep({ db, executorAdmission })).status, "completed");
    const final = await getAiWorkflowRun(paused.id, owner, db);
    assert.equal(final.status, "completed");
    assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM ai_agent_jobs WHERE workflow_run_id = ?").get(paused.id) as { count: number }).count, 1);
    assert.equal(final.nodes[0]!.agentJobId, child.childJobId);
  } finally {
    sqlite.close();
  }
});

test("AI Agent and workflow routes are same-origin, bounded JSON, role-gated, and owner-service backed", async () => {
  const routeFiles = [
    "../app/api/ai/agent-jobs/route.ts",
    "../app/api/ai/agent-jobs/[jobId]/cancel/route.ts",
    "../app/api/ai/agent-jobs/[jobId]/resume/route.ts",
    "../app/api/ai/workflow-runs/route.ts",
    "../app/api/ai/workflow-runs/[runId]/cancel/route.ts",
    "../app/api/ai/workflow-runs/[runId]/resume/route.ts",
    "../app/api/ai/workflow-runs/[runId]/nodes/[nodeKey]/review/route.ts",
  ];
  const sources = await Promise.all(routeFiles.map((file) => readFile(new URL(file, import.meta.url), "utf8")));
  for (const source of sources) {
    assert.match(source, /forwardAiRequest/);
    assert.doesNotMatch(source, /getD1Database|ensureAi|createAiAgentJob/);
  }
  const gate = await readFile(new URL("../lib/ai/django-route.ts", import.meta.url), "utf8");
  assert.match(gate, /requireAiSameOriginWrite\(request\)/);
  assert.match(gate, /readAiJsonObject\(request\)/);
  assert.match(gate, /requireAppPrincipal\(read \|\| datasetQuery \? undefined : \["admin", "operator", "analyst"\]\)/);
  const service = await readFile(new URL("../backend/ai_assistant/workflows.py", import.meta.url), "utf8");
  assert.match(service, /authorize_owner\(row, principal\)/);
  assert.match(service, /admission\(principal/);
  assert.match(service, /select|mutation/);
  assert.doesNotMatch(service, /eval\(|exec\(|subprocess/);
});

function applyMigration(sqlite: DatabaseSync, migration: string) {
  const statements = migration.split("--> statement-breakpoint").map((statement) => statement.trim()).filter(Boolean);
  assert.ok(statements.length >= 15, "0086 migration must remain explicitly split for D1 deployment");
  for (const statement of statements) sqlite.prepare(statement).run();
}

function convertToDeterministicJob(sqlite: DatabaseSync, jobId: string) {
  sqlite.prepare(`UPDATE ai_agent_jobs SET model_id = '', model_version = 0,
      allowed_tools_json = '[]', tool_policy_digest = '' WHERE id = ?`).run(jobId);
}

function sqliteAdapter(sqlite: DatabaseSync): SalesDatabase {
  return {
    prepare(sql: string) {
      let values: Array<string | number | bigint | Uint8Array | null> = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as typeof values; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() {
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  } as unknown as SalesDatabase;
}
