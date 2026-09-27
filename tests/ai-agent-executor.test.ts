import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { AppPrincipal } from "../lib/auth/authorization";
import type { ModelProviderTranscriptFrame, ModelProviderTurnResult } from "../lib/ai/model-gateway";
import type { D1Database as SalesDatabase } from "../lib/database/d1";
import { installDjangoAccessControlFixture } from "./access-control-service-fixture";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __aiAgentExecutorTestEnv?: typeof testEnvironment }).__aiAgentExecutorTestEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__aiAgentExecutorTestEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const {
  createCurrentAgentExecutorAdmission,
} = await import("./legacy/ai/agent-executor-admission");
const {
  AI_CHAT_DISPATCH_LIMITS,
} = await import("./legacy/ai/assistant-service");
const {
  runNextFormalAiAgentMicrostep,
} = await import("./legacy/ai/agent-executor");
const {
  ensureAiAgentExecutorSchema,
} = await import("./legacy/ai/agent-executor-schema");
const {
  createAiAgentJob,
  createAiWorkflowRun,
  getAiAgentJob,
  getAiWorkflowRun,
  runNextAiWorkflowMicrostep,
} = await import("./legacy/ai/agent-workflows");

const owner: AppPrincipal = {
  email: "agent-owner@example.com",
  displayName: "Agent Owner",
  role: "operator",
  scope: null,
};

test("formal Agent persists one provider dispatch/result and completes without replay", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-final-1");
    let calls = 0;
    const outcome = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async (input) => {
        calls += 1;
        await input.beforeProviderRequest?.();
        return finalTurn("已完成正式 Agent 查询。", "provider-final-1");
      },
    });
    assert.equal(outcome.status, "completed");
    assert.equal(calls, 1);
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 1);
    assert.equal(count(sqlite, "ai_agent_provider_results"), 1);
    const row = sqlite.prepare(`SELECT status, provider_round_count, tool_call_count, output_json
      FROM ai_agent_jobs WHERE id = ?`).get(jobId) as {
        status: string; provider_round_count: number; tool_call_count: number; output_json: string;
      };
    assert.equal(row.status, "completed");
    assert.equal(row.provider_round_count, 1);
    assert.equal(row.tool_call_count, 0);
    assert.equal((JSON.parse(row.output_json) as { answer: string }).answer, "已完成正式 Agent 查询。");
    assert.equal((await runNextFormalAiAgentMicrostep({ db })).status, "idle");
  });
});

test("formal Agent resumes a provider/tool/provider loop from durable transcript frames", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-tools-1");
    const seenTranscripts: ModelProviderTranscriptFrame[][] = [];
    let providerCalls = 0;
    let toolCalls = 0;
    const providerTurn = async (input: {
      transcript: readonly ModelProviderTranscriptFrame[];
      beforeProviderRequest?: () => Promise<void>;
    }): Promise<ModelProviderTurnResult> => {
      providerCalls += 1;
      seenTranscripts.push([...input.transcript]);
      await input.beforeProviderRequest?.();
      return providerCalls === 1 ? toolTurn() : finalTurn("已依据工具事实完成。", "provider-final-2");
    };
    const first = await runNextFormalAiAgentMicrostep({ db, providerTurn: providerTurn as never });
    assert.equal(first.status, "provider_checkpointed");
    const second = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: providerTurn as never,
      executeTool: async (name, rawArguments) => {
        toolCalls += 1;
        assert.equal(name, "search_system_knowledge");
        assert.equal(rawArguments, JSON.stringify({ query: "库存口径" }));
        return { ok: true, toolName: name, data: { items: [{ title: "库存口径" }], returned: 1 } };
      },
    });
    assert.equal(second.status, "tool_checkpointed");
    const third = await runNextFormalAiAgentMicrostep({ db, providerTurn: providerTurn as never });
    assert.equal(third.status, "completed");
    assert.equal(providerCalls, 2);
    assert.equal(toolCalls, 1);
    assert.equal(seenTranscripts[0]?.length, 1);
    assert.equal(seenTranscripts[1]?.length, 3);
    assert.equal((seenTranscripts[1]?.[2] as { role?: string }).role, "tool");
    const job = await getAiAgentJob(jobId, owner, db);
    assert.equal(job.providerRoundCount, 2);
    assert.equal(job.toolCallCount, 1);
    assert.equal(job.status, "completed");
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 2);
    assert.equal(count(sqlite, "ai_agent_tool_dispatches"), 1);
  });
});

test("server-admitted formal workflow creates a formal child and completes after its provider final", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const { admission } = await createCurrentAgentExecutorAdmission(owner, null, db);
    const workflow = await createAiWorkflowRun({
      clientRequestId: "formal-workflow-final-1",
      name: "正式单节点 Agent 工作流",
      graph: {
        nodes: [{
          key: "research",
          type: "agent",
          instruction: "查询允许范围内的数据并形成最终结论",
          dependsOn: [],
        }],
      },
      input: { range: "2026-08" },
    }, owner, db, { executorAdmission: admission });

    assert.equal(workflow.item.dryRun, false);
    assert.equal(workflow.item.modelId, admission.modelId);
    assert.equal(workflow.item.modelVersion, admission.modelVersion);
    assert.deepEqual(workflow.item.allowedTools, admission.allowedTools);
    assert.equal(workflow.item.toolPolicyDigest, admission.toolPolicyDigest);

    const childCreated = await runNextAiWorkflowMicrostep({ db, executorAdmission: admission });
    assert.equal(childCreated.status, "child_created");
    assert.ok(childCreated.childJobId);
    const child = await getAiAgentJob(childCreated.childJobId!, owner, db);
    assert.equal(child.workflowRunId, workflow.item.id);
    assert.equal(child.workflowNodeKey, "research");
    assert.equal(child.modelId, admission.modelId);
    assert.equal(child.modelVersion, admission.modelVersion);
    assert.deepEqual(child.allowedTools, admission.allowedTools);
    assert.equal(child.toolPolicyDigest, admission.toolPolicyDigest);

    let providerCalls = 0;
    const childFinal = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async (input) => {
        providerCalls += 1;
        await input.beforeProviderRequest?.();
        return finalTurn("正式工作流子 Agent 已完成。", "provider-workflow-final-1");
      },
    });
    assert.equal(childFinal.status, "completed");
    assert.equal(childFinal.jobId, childCreated.childJobId);
    assert.equal(providerCalls, 1);
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 1);
    assert.equal(count(sqlite, "ai_agent_provider_results"), 1);

    const workflowFinal = await runNextAiWorkflowMicrostep({ db, executorAdmission: admission });
    assert.equal(workflowFinal.status, "completed");
    assert.equal(workflowFinal.runId, workflow.item.id);
    const completed = await getAiWorkflowRun(workflow.item.id, owner, db);
    assert.equal(completed.status, "completed");
    assert.deepEqual(completed.nodes.map((node) => node.status), ["completed"]);
    assert.equal(completed.nodes[0]?.agentJobId, childCreated.childJobId);
    assert.equal(
      (completed.nodes[0]?.output as { answer?: string } | null)?.answer,
      "正式工作流子 Agent 已完成。",
    );
  });
});

test("a crash after provider dispatch fails unknown and never automatically calls again", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-unknown-1");
    let calls = 0;
    const first = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async (input) => {
        calls += 1;
        await input.beforeProviderRequest?.();
        throw new Error("connection ended after request bytes were sent");
      },
    });
    assert.equal(first.status, "failed");
    assert.equal(first.code, "provider_result_unknown");
    assert.equal(calls, 1);
    assert.equal((sqlite.prepare("SELECT state FROM ai_agent_provider_dispatches WHERE job_id = ?").get(jobId) as { state: string }).state, "unknown");
    assert.equal((await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => { calls += 1; return finalTurn("不应执行", "unexpected"); },
    })).status, "idle");
    assert.equal(calls, 1);
  });
});

test("an oversized multibyte final answer fails terminally after one provider call", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-output-bytes-1");
    let calls = 0;
    const outcome = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async (input) => {
        calls += 1;
        await input.beforeProviderRequest?.();
        return finalTurn("汉".repeat(20_000), "provider-output-too-large");
      },
    });
    assert.equal(outcome.status, "failed");
    assert.equal(outcome.code, "provider_output_invalid");
    assert.equal(calls, 1);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT status, error_code FROM ai_agent_jobs WHERE id = ?").get(jobId) },
      { status: "failed", error_code: "provider_output_invalid" },
    );
    assert.equal(
      (sqlite.prepare("SELECT state FROM ai_agent_provider_dispatches WHERE job_id = ?").get(jobId) as { state: string }).state,
      "failed",
    );
    assert.equal(count(sqlite, "ai_agent_provider_results"), 0);
    assert.equal((await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => {
        calls += 1;
        return finalTurn("不应再次调用", "unexpected-output-retry");
      },
    })).status, "idle");
    assert.equal(calls, 1);
  });
});

test("owner revocation and model version changes are revalidated before provider dispatch", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const revokedId = await createFormalJob(db, "formal-revoked-1");
    sqlite.prepare("UPDATE app_users SET status = 'disabled' WHERE email = ?").run(owner.email);
    let providerCalls = 0;
    const revoked = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => { providerCalls += 1; return finalTurn("不应调用", "unexpected"); },
    });
    assert.equal(revoked.status, "failed");
    assert.equal(revoked.code, "authorization_revoked");
    assert.equal(providerCalls, 0);
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 0);
    assert.equal((sqlite.prepare("SELECT status FROM ai_agent_jobs WHERE id = ?").get(revokedId) as { status: string }).status, "failed");

    sqlite.prepare("UPDATE app_users SET status = 'active' WHERE email = ?").run(owner.email);
    const changedId = await createFormalJob(db, "formal-version-1");
    sqlite.prepare("UPDATE ai_models SET version = version + 1 WHERE id = 'text-default'").run();
    const changed = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => { providerCalls += 1; return finalTurn("不应调用", "unexpected"); },
    });
    assert.equal(changed.status, "failed");
    assert.equal(changed.code, "model_profile_changed");
    assert.equal(providerCalls, 0);
    assert.equal((sqlite.prepare("SELECT status FROM ai_agent_jobs WHERE id = ?").get(changedId) as { status: string }).status, "failed");
  });
});

test("tampered tool policy and narrowed owner scope both fail before any provider dispatch", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const tamperedId = await createFormalJob(db, "formal-policy-tamper-1");
    sqlite.prepare("UPDATE ai_agent_jobs SET tool_policy_digest = ? WHERE id = ?")
      .run("f".repeat(64), tamperedId);
    let providerCalls = 0;
    const tampered = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => {
        providerCalls += 1;
        return finalTurn("不应调用", "unexpected-policy-call");
      },
    });
    assert.equal(tampered.status, "failed");
    assert.equal(tampered.code, "tool_policy_changed");
    assert.equal(providerCalls, 0);
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 0);
  });

  await withAgentDatabase(async ({ sqlite, db }) => {
    const scopedOwner: AppPrincipal = {
      ...owner,
      scope: {
        warehouses: ["上海仓", "北京仓"],
        channels: ["京东"],
        platforms: ["京东"],
      },
    };
    sqlite.prepare("UPDATE app_users SET scope_json = ? WHERE email = ?")
      .run(JSON.stringify(scopedOwner.scope), owner.email);
    const { admission } = await createCurrentAgentExecutorAdmission(scopedOwner, null, db);
    const created = await createAiAgentJob({
      clientRequestId: "formal-scope-contract-1",
      task: "只能查询任务创建时固定的数据范围",
      input: {},
    }, scopedOwner, db, { executorAdmission: admission });
    sqlite.prepare("UPDATE app_users SET scope_json = ? WHERE email = ?").run(JSON.stringify({
      warehouses: ["上海仓"],
      channels: ["京东"],
      platforms: ["京东"],
    }), owner.email);

    let providerCalls = 0;
    const narrowed = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => {
        providerCalls += 1;
        return finalTurn("不应调用", "unexpected-scope-call");
      },
    });
    assert.equal(narrowed.status, "failed");
    assert.equal(narrowed.jobId, created.item.id);
    assert.equal(narrowed.code, "authorization_revoked");
    assert.equal(providerCalls, 0);
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 0);
  });
});

test("formal Agent defers before provider HTTP when Chat already exhausted the shared owner daily dispatch budget", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-shared-chat-quota-1");
    const now = new Date("2026-08-27T04:00:00.000Z");
    const reservedAt = "2026-08-27 04:00:00";
    const receipt = sqlite.prepare(`INSERT INTO ai_chat_request_receipts (
      id, owner_email, client_request_id, request_digest, status, model_id,
      admitted_at, provider_started_at, completed_at
    ) VALUES (?, ?, ?, ?, 'succeeded', 'text-default', ?, ?, ?)`);
    const dispatch = sqlite.prepare(`INSERT INTO ai_chat_provider_dispatches (
      id, receipt_id, owner_email, model_id, dispatch_ordinal, reserved_at, provider_called_at
    ) VALUES (?, ?, ?, 'text-default', 1, ?, ?)`);
    sqlite.exec("BEGIN");
    try {
      for (let index = 0;
        index < AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner;
        index += 1) {
        const receiptId = `chat-quota-receipt-${index}`;
        receipt.run(
          receiptId,
          owner.email,
          `chat-quota-request-${index}`,
          `chat-quota-digest-${index}`,
          reservedAt,
          reservedAt,
          reservedAt,
        );
        dispatch.run(
          `chat-quota-dispatch-${index}`,
          receiptId,
          owner.email,
          reservedAt,
          reservedAt,
        );
      }
      sqlite.exec("COMMIT");
    } catch (error) {
      sqlite.exec("ROLLBACK");
      throw error;
    }

    let providerHttpCalls = 0;
    const outcome = await runNextFormalAiAgentMicrostep({
      db,
      now,
      providerTurn: async (input) => {
        // The primitive invokes this boundary immediately before provider HTTP.
        await input.beforeProviderRequest?.();
        providerHttpCalls += 1;
        return finalTurn("不应调用", "unexpected-shared-quota-call");
      },
    });
    assert.equal(outcome.status, "deferred");
    assert.equal(outcome.jobId, jobId);
    assert.equal(outcome.code, "provider_dispatch_rate_limited");
    assert.equal(providerHttpCalls, 0);
    assert.equal(count(sqlite, "ai_agent_provider_dispatches"), 0);
    assert.equal(
      count(sqlite, "ai_chat_provider_dispatches"),
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner,
    );
  });
});

test("stored provider results are consumed after response loss without a second provider call", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-result-recovery-1");
    const job = sqlite.prepare(`SELECT owner_email, model_id, model_version, tool_policy_digest
      FROM ai_agent_jobs WHERE id = ?`).get(jobId) as {
        owner_email: string; model_id: string; model_version: number; tool_policy_digest: string;
      };
    const result = finalTurn("从持久结果恢复完成。", "provider-recovered");
    const responseJson = JSON.stringify(result);
    const digest = await sha256(responseJson);
    sqlite.prepare(`INSERT INTO ai_agent_provider_dispatches (
      id, job_id, dispatch_ordinal, owner_email, actor_role, model_id, model_version,
      tool_policy_digest, request_digest, state, lease_epoch
    ) VALUES ('dispatch-recovered', ?, 1, ?, 'operator', ?, ?, ?, ?, 'succeeded', 1)`).run(
      jobId, job.owner_email, job.model_id, job.model_version, job.tool_policy_digest, "a".repeat(64),
    );
    sqlite.prepare(`INSERT INTO ai_agent_provider_results (
      dispatch_id, response_json, response_digest, usage_json, provider_request_id
    ) VALUES ('dispatch-recovered', ?, ?, '{}', 'provider-recovered')`).run(responseJson, digest);
    let calls = 0;
    const outcome = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => { calls += 1; return finalTurn("不应调用", "unexpected"); },
    });
    assert.equal(outcome.status, "completed");
    assert.equal(calls, 0);
    assert.equal((sqlite.prepare("SELECT provider_round_count FROM ai_agent_jobs WHERE id = ?").get(jobId) as { provider_round_count: number }).provider_round_count, 1);
  });
});

test("a legacy stored oversized final is consumed once and closed instead of requeueing forever", async () => {
  await withAgentDatabase(async ({ sqlite, db }) => {
    const jobId = await createFormalJob(db, "formal-stored-output-bytes-1");
    const job = sqlite.prepare(`SELECT owner_email, model_id, model_version, tool_policy_digest
      FROM ai_agent_jobs WHERE id = ?`).get(jobId) as {
        owner_email: string; model_id: string; model_version: number; tool_policy_digest: string;
      };
    const result = finalTurn("汉".repeat(20_000), "provider-stored-output-too-large");
    const responseJson = JSON.stringify(result);
    sqlite.prepare(`INSERT INTO ai_agent_provider_dispatches (
      id, job_id, dispatch_ordinal, owner_email, actor_role, model_id, model_version,
      tool_policy_digest, request_digest, state, lease_epoch
    ) VALUES ('dispatch-stored-output-too-large', ?, 1, ?, 'operator', ?, ?, ?, ?, 'succeeded', 1)`).run(
      jobId,
      job.owner_email,
      job.model_id,
      job.model_version,
      job.tool_policy_digest,
      "c".repeat(64),
    );
    sqlite.prepare(`INSERT INTO ai_agent_provider_results (
      dispatch_id, response_json, response_digest, usage_json, provider_request_id
    ) VALUES ('dispatch-stored-output-too-large', ?, ?, '{}', 'provider-stored-output-too-large')`)
      .run(responseJson, await sha256(responseJson));

    let calls = 0;
    const outcome = await runNextFormalAiAgentMicrostep({
      db,
      providerTurn: async () => {
        calls += 1;
        return finalTurn("不应调用", "unexpected-stored-output-call");
      },
    });
    assert.equal(outcome.status, "failed");
    assert.equal(outcome.code, "provider_output_invalid");
    assert.equal(calls, 0);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT status, error_code, provider_round_count FROM ai_agent_jobs WHERE id = ?").get(jobId) },
      { status: "failed", error_code: "provider_output_invalid", provider_round_count: 1 },
    );
  });
});

function finalTurn(text: string, providerRequestId: string): ModelProviderTurnResult {
  return {
    protocol: "openai_compatible",
    kind: "final",
    text,
    assistantFrame: { protocol: "openai_compatible", role: "assistant", content: text },
    providerRequestId,
    usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 },
    finishReason: "stop",
  };
}

function toolTurn(): ModelProviderTurnResult {
  const arguments_ = JSON.stringify({ query: "库存口径" });
  return {
    protocol: "openai_compatible",
    kind: "tool_calls",
    toolCalls: [{
      providerCallId: "call-1",
      name: "search_system_knowledge",
      arguments: arguments_,
    }],
    assistantFrame: {
      protocol: "openai_compatible",
      role: "assistant",
      content: null,
      tool_calls: [{ id: "call-1", type: "function", function: { name: "search_system_knowledge", arguments: arguments_ } }],
    },
    providerRequestId: "provider-tools-1",
    usage: { inputTokens: 10, outputTokens: 5, totalTokens: 15 },
    finishReason: "tool_calls",
  };
}

async function createFormalJob(db: SalesDatabase, clientRequestId: string): Promise<string> {
  const { admission } = await createCurrentAgentExecutorAdmission(owner, null, db);
  const created = await createAiAgentJob({
    clientRequestId,
    task: "读取允许范围内的数据并给出有来源的结论",
    input: { range: "2026-08" },
  }, owner, db, { executorAdmission: admission });
  return created.item.id;
}

async function withAgentDatabase(
  callback: (input: { sqlite: DatabaseSync; db: SalesDatabase }) => Promise<void>,
) {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys = ON");
  const db = sqliteAdapter(sqlite);
  testEnvironment.DB = db;
  try {
    installDjangoAccessControlFixture(sqlite);
    await ensureAiAgentExecutorSchema(db);
    sqlite.prepare(`INSERT OR REPLACE INTO app_users (email, display_name, role, status, scope_json)
      VALUES (?, ?, 'operator', 'active', NULL)`).run(owner.email, owner.displayName);
    sqlite.prepare(`INSERT INTO ai_models (
      id, version, name, protocol, model_type, model_name, base_url,
      api_key_encrypted, api_key_suffix, is_default_text_model, status,
      timeout_ms, max_tokens, reasoning_mode, temperature_milli, max_tool_rounds, max_total_tool_calls
    ) VALUES ('text-default', 1, 'Default', 'openai_compatible', 'text', 'gpt-test',
      'https://api.openai.com/v1/chat/completions', 'unused', '', 1, 'enabled',
      20000, 4096, 'auto', 200, 20, 40)`).run();
    await callback({ sqlite, db });
  } finally {
    delete testEnvironment.DB;
    sqlite.close();
  }
}

function count(sqlite: DatabaseSync, table: string): number {
  return Number((sqlite.prepare(`SELECT COUNT(*) count FROM ${table}`).get() as { count: number }).count);
}

async function sha256(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
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
