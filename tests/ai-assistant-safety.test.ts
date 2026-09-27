import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __aiAssistantSafetyEnv?: typeof testEnvironment }).__aiAssistantSafetyEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__aiAssistantSafetyEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const {
  AI_CHAT_DISPATCH_LIMITS,
  AI_MESSAGE_PAGE_CONTENT_BYTES_MAX,
  AI_MESSAGE_RESPONSE_BYTES_MAX,
  appendRuntimeUserContext,
  appendConversationMessage,
  claimAiChatRequest,
  completeAiChatRequest,
  createConversation,
  deleteAiModel,
  deleteAiConversation,
  digestAiChatRequestPayload,
  ensureAiAssistantSchema,
  markAiChatRequestDispatched,
  markAiChatRequestUnknown,
  listAiConversations,
  listConversationMessages,
  listAiModels,
  requireConversationAccess,
  reserveAiChatInitialProviderDispatch,
  reserveAiChatProviderDispatch,
  resolveChatModel,
  sendAiChannelText,
  upsertAiChannel,
  upsertAiModel,
} = await import("./legacy/ai/assistant-service");
const {
  ensureAiArtifactSchema,
  getAiArtifactDownload,
  listAiArtifactsForConversation,
  persistAiTableArtifacts,
} = await import("./legacy/ai/artifacts");
const {
  aiRouteErrorResponse,
  parseAiPositiveInteger,
  readAiJsonObject,
} = await import("./legacy/app/api/ai/route-helpers");
const { readBoundedJsonObject } = await import("../lib/http/bounded-json");
const { answerAiQuestion } = await import("./legacy/ai/question-workflow");
const { ensureAiAgentExecutorSchema } = await import("./legacy/ai/agent-executor-schema");

test("global memory stays request-local and is appended only to the latest user message", () => {
  const storedMessages = [
    { role: "user" as const, content: "第一轮问题" },
    { role: "assistant" as const, content: "第一轮回答" },
    { role: "user" as const, content: "本轮问题" },
  ];
  const runtimeMessages = appendRuntimeUserContext(
    storedMessages,
    '<personal_memory>{"trust":"untrusted_memory_data"}</personal_memory>',
  );
  assert.deepEqual(storedMessages.map((message) => message.content), ["第一轮问题", "第一轮回答", "本轮问题"]);
  assert.equal(runtimeMessages[0]?.content, "第一轮问题");
  assert.equal(runtimeMessages[1]?.content, "第一轮回答");
  assert.match(runtimeMessages[2]?.content ?? "", /^本轮问题\n\n<personal_memory>/);
});

type QueryRecord = { sql: string; values: SQLInputValue[]; returned?: number };

function sqliteAdapter(sqlite: DatabaseSync, queries: QueryRecord[] = []) {
  let batchTail: Promise<unknown> = Promise.resolve();
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) {
          values = nextValues as SQLInputValue[];
          return this;
        },
        async first<T>(column?: string) {
          queries.push({ sql, values: [...values] });
          const row = sqlite.prepare(sql).get(...values) as Record<string, unknown> | undefined;
          return (column ? row?.[column] : row ?? null) as T | null;
        },
        async all<T>() {
          const results = sqlite.prepare(sql).all(...values) as T[];
          queries.push({ sql, values: [...values], returned: results.length });
          return { results };
        },
        async run() {
          queries.push({ sql, values: [...values] });
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes), last_row_id: Number(result.lastInsertRowid) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      const execute = async () => {
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
      };
      const current = batchTail.then(execute, execute);
      batchTail = current.catch(() => undefined);
      return current;
    },
  };
}

const owner = { email: "owner@example.com", displayName: "Owner", role: "analyst" as const, scope: null };
const other = { email: "other@example.com", displayName: "Other", role: "analyst" as const, scope: null };
const admin = { email: "admin@example.com", displayName: "Admin", role: "admin" as const, scope: null };
const wideOwner = {
  ...owner,
  scope: { warehouses: ["华东仓"], channels: ["线上"], platforms: ["京东", "天猫"] },
};
const narrowedOwner = {
  ...owner,
  scope: { warehouses: ["华东仓"], channels: ["线上"], platforms: ["京东"] },
};
const expandedOwner = {
  ...owner,
  scope: { warehouses: ["华东仓", "华南仓"], channels: ["线上"], platforms: ["京东", "天猫", "拼多多"] },
};

async function createReadyDatabase(queries: QueryRecord[] = []) {
  const sqlite = new DatabaseSync(":memory:");
  const database = sqliteAdapter(sqlite, queries);
  await ensureAiAssistantSchema(database as never);
  return { sqlite, database };
}

function seedAiChatProviderDispatches(sqlite: DatabaseSync, input: {
  count: number;
  ownerAt: (index: number) => string;
  modelAt: (index: number) => string;
  receiptAt?: (index: number) => string;
  ordinalAt?: (index: number) => number;
  status?: "dispatched" | "succeeded" | "unknown";
  reservedAt?: string;
  prefix: string;
}) {
  const insertReceipt = sqlite.prepare(`INSERT OR IGNORE INTO ai_chat_request_receipts (
    id, owner_email, client_request_id, request_digest, status, model_id, admitted_at, provider_started_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`);
  const insertDispatch = sqlite.prepare(`INSERT INTO ai_chat_provider_dispatches (
    id, receipt_id, owner_email, model_id, dispatch_ordinal, reserved_at, provider_called_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?)`);
  for (let index = 0; index < input.count; index += 1) {
    const receiptSuffix = input.receiptAt?.(index) ?? String(index);
    const receiptId = `${input.prefix}-receipt-${receiptSuffix}`;
    const ownerEmail = input.ownerAt(index);
    const modelId = input.modelAt(index);
    const reservedAt = input.reservedAt ?? "2026-08-26 04:00:00";
    insertReceipt.run(
      receiptId,
      ownerEmail,
      `${input.prefix}-request-${receiptSuffix}`,
      `${input.prefix}-${receiptSuffix}`.padStart(64, "0").slice(-64),
      input.status ?? "succeeded",
      modelId,
      reservedAt,
      reservedAt,
    );
    insertDispatch.run(
      `${input.prefix}-dispatch-${index}`,
      receiptId,
      ownerEmail,
      modelId,
      input.ordinalAt?.(index) ?? 1,
      reservedAt,
      reservedAt,
    );
  }
}

function seedAiAgentProviderDispatches(sqlite: DatabaseSync, input: {
  count: number;
  ownerEmail: string;
  modelId: string;
  reservedAt: string;
  prefix: string;
}) {
  const maximumDispatchesPerJob = 20;
  const insertJob = sqlite.prepare(`INSERT INTO ai_agent_jobs (
    id, owner_email, client_request_id, request_digest, scope_json, task,
    input_json, state_json, output_json, model_id, model_version,
    allowed_tools_json, tool_policy_digest, provider_round_count,
    status, phase, step_index, lease_epoch, completed_at
  ) VALUES (?, ?, ?, ?, 'null', ?, '{}', '{}', '{}', ?, 1,
    '[]', ?, ?, 'completed', 'completed', ?, 1, ?)`);
  const insertDispatch = sqlite.prepare(`INSERT INTO ai_agent_provider_dispatches (
    id, job_id, dispatch_ordinal, owner_email, actor_role, model_id, model_version,
    tool_policy_digest, request_digest, state, lease_epoch,
    reserved_at, provider_called_at, completed_at
  ) VALUES (?, ?, ?, ?, 'analyst', ?, 1, ?, ?, 'succeeded', 1, ?, ?, ?)`);
  const insertResult = sqlite.prepare(`INSERT INTO ai_agent_provider_results (
    dispatch_id, response_json, response_digest, usage_json, provider_request_id, completed_at
  ) VALUES (?, ?, ?, '{}', ?, ?)`);
  const jobCount = Math.ceil(input.count / maximumDispatchesPerJob);
  let seeded = 0;
  for (let jobIndex = 0; jobIndex < jobCount; jobIndex += 1) {
    const jobId = `${input.prefix}-job-${jobIndex}`;
    const rounds = Math.min(maximumDispatchesPerJob, input.count - seeded);
    insertJob.run(
      jobId,
      input.ownerEmail,
      `${input.prefix}-request-${jobIndex}`,
      `${input.prefix}-job-digest-${jobIndex}`.padStart(64, "0").slice(-64),
      "已完成的合法 Agent 配额种子",
      input.modelId,
      "c".repeat(64),
      rounds,
      rounds,
      input.reservedAt,
    );
    for (let ordinal = 1; ordinal <= rounds; ordinal += 1) {
      const dispatchId = `${input.prefix}-dispatch-${jobIndex}-${ordinal}`;
      insertDispatch.run(
        dispatchId,
        jobId,
        ordinal,
        input.ownerEmail,
        input.modelId,
        "c".repeat(64),
        `${input.prefix}-dispatch-digest-${jobIndex}-${ordinal}`.padStart(64, "0").slice(-64),
        input.reservedAt,
        input.reservedAt,
        input.reservedAt,
      );
      insertResult.run(
        dispatchId,
        JSON.stringify({ protocol: "openai_compatible", kind: "final", text: "seed" }),
        `${input.prefix}-result-digest-${jobIndex}-${ordinal}`.padStart(64, "0").slice(-64),
        `${input.prefix}-provider-${jobIndex}-${ordinal}`,
        input.reservedAt,
      );
      seeded += 1;
    }
  }
  assert.equal(seeded, input.count);
}

async function createChatDispatchCandidate(input: {
  clientRequestId: string;
  message: string;
  ownerEmail?: string;
  database: ReturnType<typeof sqliteAdapter>;
}) {
  const principal = { ...owner, email: input.ownerEmail ?? owner.email };
  const requestDigest = await digestAiChatRequestPayload({
    conversationId: null,
    modelId: null,
    message: input.message,
    title: null,
  });
  const receipt = await claimAiChatRequest({
    clientRequestId: input.clientRequestId,
    requestDigest,
    principal,
  }, input.database as never);
  if (receipt.kind !== "claimed") throw new Error("expected a dispatch candidate claim");
  return { principal, conversationId: null, requestDigest, receipt };
}

async function admitAndReserveFirstProviderTurn(
  candidate: Awaited<ReturnType<typeof createChatDispatchCandidate>>,
  modelId: string,
  now: Date,
  database: ReturnType<typeof sqliteAdapter>,
) {
  await markAiChatRequestDispatched({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    conversationId: null,
    modelId,
    now,
  }, database as never);
  await reserveAiChatInitialProviderDispatch({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    modelId,
    now,
  }, database as never);
}

async function activateFirstProviderTurn(
  candidate: Awaited<ReturnType<typeof createChatDispatchCandidate>>,
  modelId: string,
  now: Date,
  database: ReturnType<typeof sqliteAdapter>,
) {
  await reserveAiChatProviderDispatch({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    modelId,
    ordinal: 1,
    now,
  }, database as never);
}

test("AI chat request receipts replay success and fail closed after provider dispatch", async () => {
  const { sqlite, database } = await createReadyDatabase();
  const payload = {
    conversationId: null,
    modelId: "model-1",
    message: "查询昨日销售额",
    title: "小特对话",
  };
  const requestDigest = await digestAiChatRequestPayload(payload);
  const first = await claimAiChatRequest({
    clientRequestId: "chat-request-1",
    requestDigest,
    principal: owner,
  }, database as never);
  assert.equal(first.kind, "claimed");
  if (first.kind !== "claimed") throw new Error("expected a claimed request");
  const conversationId = await createConversation(payload.title, owner, payload.modelId, database as never);
  await appendConversationMessage(conversationId, "user", payload.message, "message", database as never);
  await markAiChatRequestDispatched({
    receiptId: first.receiptId,
    requestDigest,
    ownerEmail: owner.email,
    conversationId,
    modelId: payload.modelId,
  }, database as never);
  await reserveAiChatInitialProviderDispatch({
    receiptId: first.receiptId,
    requestDigest,
    ownerEmail: owner.email,
    modelId: payload.modelId,
  }, database as never);
  await reserveAiChatProviderDispatch({
    receiptId: first.receiptId,
    requestDigest,
    ownerEmail: owner.email,
    modelId: payload.modelId,
    ordinal: 1,
  }, database as never);

  let providerCalls = 1;
  await assert.rejects(
    () => claimAiChatRequest({
      clientRequestId: "chat-request-1",
      requestDigest,
      principal: owner,
    }, database as never),
    /避免重复计费/,
  );
  assert.equal(providerCalls, 1, "a replay while dispatched must not enter the provider path");

  await completeAiChatRequest({
    receiptId: first.receiptId,
    requestDigest,
    ownerEmail: owner.email,
    expectedStatus: "dispatched",
    conversationId,
    assistantMessage: { id: "ai-msg-idempotent-answer", content: "昨日销售额为 100 元。", messageKind: "message" },
    result: {
      conversationId,
      reply: "昨日销售额为 100 元。",
      modelId: payload.modelId,
      outcome: "answered",
    },
  }, database as never);
  const replay = await claimAiChatRequest({
    clientRequestId: "chat-request-1",
    requestDigest,
    principal: owner,
  }, database as never);
  assert.equal(replay.kind, "replayed");
  if (replay.kind !== "replayed") throw new Error("expected a replayed request");
  assert.deepEqual(replay.result, {
    conversationId,
    reply: "昨日销售额为 100 元。",
    modelId: payload.modelId,
    outcome: "answered",
    assistantMessageId: "ai-msg-idempotent-answer",
  });
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages WHERE role='assistant'").get()?.total, 1);
  const mismatchedDigest = await digestAiChatRequestPayload({ ...payload, message: "另一个问题" });
  await assert.rejects(
    () => claimAiChatRequest({
      clientRequestId: "chat-request-1",
      requestDigest: mismatchedDigest,
      principal: owner,
    }, database as never),
    /不同的聊天请求/,
  );

  const unknownDigest = await digestAiChatRequestPayload({ ...payload, message: "可能已计费" });
  const unknown = await claimAiChatRequest({
    clientRequestId: "chat-request-unknown",
    requestDigest: unknownDigest,
    principal: owner,
  }, database as never);
  if (unknown.kind !== "claimed") throw new Error("expected an unknown-state claim");
  await markAiChatRequestDispatched({
    receiptId: unknown.receiptId,
    requestDigest: unknownDigest,
    ownerEmail: owner.email,
    conversationId,
    modelId: payload.modelId,
  }, database as never);
  await reserveAiChatInitialProviderDispatch({
    receiptId: unknown.receiptId,
    requestDigest: unknownDigest,
    ownerEmail: owner.email,
    modelId: payload.modelId,
  }, database as never);
  await reserveAiChatProviderDispatch({
    receiptId: unknown.receiptId,
    requestDigest: unknownDigest,
    ownerEmail: owner.email,
    modelId: payload.modelId,
    ordinal: 1,
  }, database as never);
  providerCalls += 1;
  await markAiChatRequestUnknown({
    receiptId: unknown.receiptId,
    requestDigest: unknownDigest,
    ownerEmail: owner.email,
    errorCode: "response_lost",
  }, database as never);
  await assert.rejects(
    () => claimAiChatRequest({
      clientRequestId: "chat-request-unknown",
      requestDigest: unknownDigest,
      principal: owner,
    }, database as never),
    /避免重复计费/,
  );
  assert.equal(providerCalls, 2);
  sqlite.close();
});

test("AI chat admission enforces the owner Shanghai-day request quota before provider entry", async () => {
  const { sqlite, database } = await createReadyDatabase();
  seedAiChatProviderDispatches(sqlite, {
    count: AI_CHAT_DISPATCH_LIMITS.maximumDailyRequestsPerOwner,
    ownerAt: () => owner.email,
    modelAt: (index) => `owner-seed-model-${index}`,
    prefix: "owner-daily",
  });
  const candidate = await createChatDispatchCandidate({
    clientRequestId: "owner-daily-candidate",
    message: "owner daily quota",
    database,
  });
  let providerCalls = 0;
  await assert.rejects(() => markAiChatRequestDispatched({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    conversationId: null,
    modelId: "owner-target-model",
    now: new Date("2026-08-26T05:00:00.000Z"),
  }, database as never).then(() => { providerCalls += 1; }), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 429);
    assert.match((error as Error).message, /上海自然日/);
    return true;
  });
  assert.equal(providerCalls, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations").get()?.total, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages").get()?.total, 0);
  sqlite.close();
});

test("AI chat charges every provider turn against the owner Shanghai-day dispatch quota", async () => {
  const { sqlite, database } = await createReadyDatabase();
  seedAiChatProviderDispatches(sqlite, {
    count: AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner - 1,
    ownerAt: () => owner.email,
    modelAt: (index) => `owner-provider-model-${Math.floor(index / 60)}`,
    receiptAt: (index) => String(Math.floor(index / 60)),
    ordinalAt: (index) => (index % 60) + 1,
    prefix: "owner-provider-daily",
  });
  const candidate = await createChatDispatchCandidate({ clientRequestId: "owner-provider-candidate", message: "owner provider quota", database });
  const modelId = "owner-provider-target";
  const now = new Date("2026-08-26T05:00:00.000Z");
  await admitAndReserveFirstProviderTurn(candidate, modelId, now, database);
  let providerCalls = 0;
  await activateFirstProviderTurn(candidate, modelId, now, database);
  providerCalls += 1;
  await assert.rejects(() => reserveAiChatProviderDispatch({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    modelId,
    ordinal: 2,
    now,
  }, database as never).then(() => { providerCalls += 1; }), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 429);
    return true;
  });
  assert.equal(providerCalls, 1);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_chat_provider_dispatches WHERE owner_email=?")
    .get(owner.email)?.total, AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner);
  sqlite.close();
});

test("AI chat initial dispatch counts formal Agent dispatches against the shared owner Shanghai-day quota", async () => {
  const { sqlite, database } = await createReadyDatabase();
  await ensureAiAgentExecutorSchema(database as never);
  sqlite.exec("PRAGMA foreign_keys = ON");
  const modelId = "shared-agent-chat-owner-model";
  const now = new Date("2026-08-26T05:00:00.000Z");
  seedAiAgentProviderDispatches(sqlite, {
    count: AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner,
    ownerEmail: owner.email,
    modelId,
    reservedAt: "2026-08-26 04:00:00",
    prefix: "shared-agent-owner-daily",
  });
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_agent_jobs").get()?.total, 6);
  assert.equal(
    sqlite.prepare("SELECT MAX(dispatch_ordinal) maximum FROM ai_agent_provider_dispatches").get()?.maximum,
    20,
  );

  const candidate = await createChatDispatchCandidate({
    clientRequestId: "shared-agent-owner-chat-candidate",
    message: "shared Agent and Chat owner provider quota",
    database,
  });
  await markAiChatRequestDispatched({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    conversationId: null,
    modelId,
    now,
  }, database as never);
  const chatDispatchesBefore = Number(
    sqlite.prepare("SELECT COUNT(*) total FROM ai_chat_provider_dispatches").get()?.total ?? 0,
  );
  await assert.rejects(() => reserveAiChatInitialProviderDispatch({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    modelId,
    now,
  }, database as never), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 429);
    assert.equal((error as { code?: string }).code, "rate_limited");
    assert.match((error as Error).message, /本轮未调用模型/);
    return true;
  });
  assert.equal(
    sqlite.prepare("SELECT COUNT(*) total FROM ai_chat_provider_dispatches").get()?.total,
    chatDispatchesBefore,
  );
  assert.equal(chatDispatchesBefore, 0);
  assert.equal(
    sqlite.prepare("SELECT COUNT(*) total FROM ai_agent_provider_dispatches").get()?.total,
    AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner,
  );
  assert.equal(sqlite.prepare("PRAGMA foreign_key_check").all().length, 0);
  sqlite.close();
});

test("AI chat charges every provider turn against the global Shanghai-day dispatch quota", async () => {
  const { sqlite, database } = await createReadyDatabase();
  seedAiChatProviderDispatches(sqlite, {
    count: AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesGlobal - 1,
    ownerAt: (index) => `global-owner-${index}@example.com`,
    modelAt: (index) => `global-model-${index}`,
    prefix: "global-provider-daily",
  });
  const candidate = await createChatDispatchCandidate({ clientRequestId: "global-provider-candidate", message: "global provider quota", database });
  const modelId = "global-provider-target";
  const now = new Date("2026-08-26T05:00:00.000Z");
  await admitAndReserveFirstProviderTurn(candidate, modelId, now, database);
  let providerCalls = 0;
  await activateFirstProviderTurn(candidate, modelId, now, database);
  providerCalls += 1;
  await assert.rejects(() => reserveAiChatProviderDispatch({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    modelId,
    ordinal: 2,
    now,
  }, database as never).then(() => { providerCalls += 1; }), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 429);
    return true;
  });
  assert.equal(providerCalls, 1);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_chat_provider_dispatches").get()?.total,
    AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesGlobal);
  sqlite.close();
});

test("AI chat charges every provider turn against the model Shanghai-day dispatch quota", async () => {
  const { sqlite, database } = await createReadyDatabase();
  const modelId = "shared-provider-quota-model";
  seedAiChatProviderDispatches(sqlite, {
    count: AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerModel - 1,
    ownerAt: (index) => `model-owner-${index}@example.com`,
    modelAt: () => modelId,
    prefix: "model-provider-daily",
  });
  const candidate = await createChatDispatchCandidate({ clientRequestId: "model-provider-candidate", message: "model provider quota", database });
  const now = new Date("2026-08-26T05:00:00.000Z");
  await admitAndReserveFirstProviderTurn(candidate, modelId, now, database);
  let providerCalls = 0;
  await activateFirstProviderTurn(candidate, modelId, now, database);
  providerCalls += 1;
  await assert.rejects(() => reserveAiChatProviderDispatch({
    receiptId: candidate.receipt.receiptId,
    requestDigest: candidate.requestDigest,
    ownerEmail: candidate.principal.email,
    modelId,
    ordinal: 2,
    now,
  }, database as never).then(() => { providerCalls += 1; }), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 429);
    return true;
  });
  assert.equal(providerCalls, 1);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_chat_provider_dispatches WHERE model_id=?")
    .get(modelId)?.total, AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerModel);
  sqlite.close();
});

test("concurrent AI chat dispatches atomically respect the owner active-request cap", async () => {
  const { sqlite, database } = await createReadyDatabase();
  seedAiChatProviderDispatches(sqlite, {
    count: AI_CHAT_DISPATCH_LIMITS.maximumActivePerOwner - 1,
    ownerAt: () => owner.email,
    modelAt: () => "existing-active-model",
    status: "dispatched",
    reservedAt: "2026-08-26 04:59:00",
    prefix: "owner-active",
  });
  const [left, right] = await Promise.all([
    createChatDispatchCandidate({ clientRequestId: "active-left", message: "left", database }),
    createChatDispatchCandidate({ clientRequestId: "active-right", message: "right", database }),
  ]);
  const results = await Promise.allSettled([
    markAiChatRequestDispatched({
      receiptId: left.receipt.receiptId,
      requestDigest: left.requestDigest,
      ownerEmail: left.principal.email,
      conversationId: left.conversationId,
      modelId: "active-left-model",
      now: new Date("2026-08-26T05:00:00.000Z"),
    }, database as never),
    markAiChatRequestDispatched({
      receiptId: right.receipt.receiptId,
      requestDigest: right.requestDigest,
      ownerEmail: right.principal.email,
      conversationId: right.conversationId,
      modelId: "active-right-model",
      now: new Date("2026-08-26T05:00:00.000Z"),
    }, database as never),
  ]);
  assert.equal(results.filter((result) => result.status === "fulfilled").length, 1);
  const rejected = results.find((result): result is PromiseRejectedResult => result.status === "rejected");
  assert.equal((rejected?.reason as { status?: number }).status, 409);
  assert.match(String(rejected?.reason?.message), /并发请求/);
  assert.equal(sqlite.prepare(`SELECT COUNT(*) total FROM ai_chat_request_receipts
    WHERE owner_email=? AND status='dispatched'`).get(owner.email)?.total, AI_CHAT_DISPATCH_LIMITS.maximumActivePerOwner);
  sqlite.close();
});

test("AI question workflow replays a completed shortcut without duplicating messages", async () => {
  const { sqlite, database } = await createReadyDatabase();
  testEnvironment.DB = database;
  try {
    const input = {
      entry: {
        principal: owner,
        source: "web_chat" as const,
        surface: "ai_chat" as const,
        requestId: "workflow-idempotency-test",
      },
      clientRequestId: "chat-workflow-help-1",
      title: "帮助测试",
      message: "帮助",
    };
    const first = await answerAiQuestion(input, database as never);
    const replay = await answerAiQuestion(input, database as never);
    assert.deepEqual(replay, first);
    assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations").get()?.total, 1);
    assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages").get()?.total, 2);
    assert.equal(sqlite.prepare("SELECT status FROM ai_chat_request_receipts").get()?.status, "succeeded");
  } finally {
    testEnvironment.DB = undefined;
    sqlite.close();
  }
});

test("AI chat receipt success and assistant message publish atomically", async () => {
  const { sqlite, database } = await createReadyDatabase();
  const conversationId = await createConversation("原子发布", owner, null, database as never);
  const requestDigest = await digestAiChatRequestPayload({
    conversationId,
    modelId: null,
    message: "原子请求",
    title: null,
  });
  const receipt = await claimAiChatRequest({
    clientRequestId: "chat-request-atomic",
    requestDigest,
    principal: owner,
  }, database as never);
  if (receipt.kind !== "claimed") throw new Error("expected an atomic claim");
  await markAiChatRequestDispatched({
    receiptId: receipt.receiptId,
    requestDigest,
    ownerEmail: owner.email,
    conversationId,
    modelId: "model-atomic",
  }, database as never);
  sqlite.exec(`CREATE TRIGGER reject_chat_receipt_success
    BEFORE UPDATE OF status ON ai_chat_request_receipts
    WHEN NEW.status = 'succeeded'
    BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END`);
  await assert.rejects(() => completeAiChatRequest({
    receiptId: receipt.receiptId,
    requestDigest,
    ownerEmail: owner.email,
    expectedStatus: "dispatched",
    conversationId,
    assistantMessage: { id: "ai-msg-must-rollback", content: "不得残留", messageKind: "message" },
    result: { conversationId, reply: "不得残留", modelId: null, outcome: "answered" },
  }, database as never), /receipt unavailable/);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages WHERE id='ai-msg-must-rollback'").get()?.total, 0);
  assert.equal(sqlite.prepare("SELECT status FROM ai_chat_request_receipts WHERE id=?").get(receipt.receiptId)?.status, "dispatched");
  sqlite.close();
});

test("AI chat idempotency migration upgrades the existing assistant schema", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8"));
  const migration = await readFile(new URL("../drizzle/0083_ai_chat_idempotency.sql", import.meta.url), "utf8");
  sqlite.exec(migration);
  sqlite.prepare(`INSERT INTO ai_chat_request_receipts
    (id, owner_email, client_request_id, request_digest, status)
    VALUES ('receipt-1', 'owner@example.com', 'request-1', ?, 'processing')`).run("a".repeat(64));
  assert.throws(() => sqlite.prepare(`INSERT INTO ai_chat_request_receipts
    (id, owner_email, client_request_id, request_digest, status)
    VALUES ('receipt-2', 'owner@example.com', 'request-1', ?, 'processing')`).run("b".repeat(64)), /UNIQUE/);
  const indexes = sqlite.prepare(`SELECT name FROM sqlite_master
    WHERE type='index' AND tbl_name='ai_chat_request_receipts' ORDER BY name`).all() as Array<{ name: string }>;
  assert.deepEqual(indexes.map((row) => row.name), [
    "ai_chat_request_receipts_conversation_idx",
    "ai_chat_request_receipts_owner_client_uq",
    "ai_chat_request_receipts_status_updated_idx",
    "sqlite_autoindex_ai_chat_request_receipts_1",
  ]);
  const dispatchIndexes = sqlite.prepare(`SELECT name FROM sqlite_master
    WHERE type='index' AND tbl_name='ai_chat_provider_dispatches' ORDER BY name`).all() as Array<{ name: string }>;
  assert.deepEqual(dispatchIndexes.map((row) => row.name), [
    "ai_chat_provider_dispatches_model_reserved_idx",
    "ai_chat_provider_dispatches_owner_reserved_idx",
    "ai_chat_provider_dispatches_receipt_ordinal_uq",
    "ai_chat_provider_dispatches_reserved_idx",
    "sqlite_autoindex_ai_chat_provider_dispatches_1",
  ]);
  sqlite.close();
});

test("AI chat idempotency runtime-first and migration-first upgrades are idempotent and equivalent", async () => {
  const assistantMigration = await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8");
  const receiptMigration = await readFile(new URL("../drizzle/0083_ai_chat_idempotency.sql", import.meta.url), "utf8");

  const runtimeFirst = new DatabaseSync(":memory:");
  await ensureAiAssistantSchema(sqliteAdapter(runtimeFirst) as never);
  runtimeFirst.exec(receiptMigration);

  const migrationFirst = new DatabaseSync(":memory:");
  migrationFirst.exec(assistantMigration);
  migrationFirst.exec(receiptMigration);
  await ensureAiAssistantSchema(sqliteAdapter(migrationFirst) as never);

  const snapshot = (sqlite: DatabaseSync) => ({
    receipt: {
      columns: sqlite.prepare("PRAGMA table_info(ai_chat_request_receipts)").all().map((row) => ({ ...row })),
      indexes: sqlite.prepare("PRAGMA index_list(ai_chat_request_receipts)").all()
        .map((row) => ({ ...row })).sort((left, right) => String(left.name).localeCompare(String(right.name))),
      foreignKeys: sqlite.prepare("PRAGMA foreign_key_list(ai_chat_request_receipts)").all().map((row) => ({ ...row })),
    },
    dispatch: {
      columns: sqlite.prepare("PRAGMA table_info(ai_chat_provider_dispatches)").all().map((row) => ({ ...row })),
      indexes: sqlite.prepare("PRAGMA index_list(ai_chat_provider_dispatches)").all()
        .map((row) => ({ ...row })).sort((left, right) => String(left.name).localeCompare(String(right.name))),
      foreignKeys: sqlite.prepare("PRAGMA foreign_key_list(ai_chat_provider_dispatches)").all().map((row) => ({ ...row })),
    },
  });
  assert.deepEqual(snapshot(runtimeFirst), snapshot(migrationFirst));
  runtimeFirst.close();
  migrationFirst.close();
});

test("0083 tolerates a legacy runtime receipt before the new runtime adds dispatch columns", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8"));
  sqlite.exec(`CREATE TABLE ai_chat_request_receipts (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    conversation_id TEXT,
    assistant_message_id TEXT,
    result_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
  )`);
  sqlite.exec(await readFile(new URL("../drizzle/0083_ai_chat_idempotency.sql", import.meta.url), "utf8"));
  await ensureAiAssistantSchema(sqliteAdapter(sqlite) as never);
  const columns = new Set((sqlite.prepare("PRAGMA table_info(ai_chat_request_receipts)").all() as Array<{ name: string }>)
    .map((row) => row.name));
  assert.equal(columns.has("model_id"), true);
  assert.equal(columns.has("admitted_at"), true);
  assert.equal(columns.has("provider_started_at"), true);
  const indexes = new Set((sqlite.prepare("PRAGMA index_list(ai_chat_request_receipts)").all() as Array<{ name: string }>)
    .map((row) => row.name));
  assert.equal(indexes.has("ai_chat_request_receipts_owner_admitted_idx"), true);
  assert.equal(indexes.has("ai_chat_request_receipts_status_model_admitted_idx"), true);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM sqlite_master WHERE type='table' AND name='ai_chat_provider_dispatches'").get()?.total, 1);
  sqlite.close();
});

test("AI conversation pagination applies owner scope before LIMIT and remains bounded", async () => {
  const queries: QueryRecord[] = [];
  const { sqlite, database } = await createReadyDatabase(queries);
  const insert = sqlite.prepare(`INSERT INTO ai_conversations
    (id, title, model_id, created_by, created_at, updated_at) VALUES (?, ?, NULL, ?, ?, ?)`);
  for (let index = 0; index < 205; index += 1) {
    const timestamp = `2026-08-${String((index % 20) + 1).padStart(2, "0")} 00:${String(index % 60).padStart(2, "0")}:00`;
    insert.run(`owner-${String(index).padStart(3, "0")}`, `owner ${index}`, owner.email, timestamp, timestamp);
  }
  for (let index = 0; index < 50; index += 1) {
    insert.run(`other-${String(index).padStart(3, "0")}`, `other ${index}`, other.email, "2026-08-20 12:00:00", "2026-08-20 12:00:00");
  }

  queries.length = 0;
  const secondPage = await listAiConversations(owner, { page: 2, pageSize: 100 }, database as never);
  assert.equal(secondPage.items.length, 100);
  assert.equal(secondPage.pagination.total, 205);
  assert.equal(secondPage.pagination.returned, 100);
  assert.equal(secondPage.pagination.hasMore, true);
  assert.equal(secondPage.items.every((item) => item.createdBy === owner.email), true);
  const listQuery = queries.find((query) => /FROM ai_conversations c/i.test(query.sql)
    && /WHERE 1 = 1 AND c\.created_by = \?/i.test(query.sql)
    && /LIMIT \? OFFSET \?/i.test(query.sql));
  assert.ok(listQuery, "owner predicate must be inside the paginated SQL query");
  assert.deepEqual(listQuery.values.slice(-2), [100, 100]);

  const finalPage = await listAiConversations(owner, { page: 3, pageSize: 100 }, database as never);
  assert.equal(finalPage.items.length, 5);
  assert.equal(finalPage.pagination.hasMore, false);
  const adminPage = await listAiConversations(admin, { page: 1, pageSize: 100 }, database as never);
  assert.equal(adminPage.pagination.total, 255);
  await assert.rejects(() => listAiConversations(owner, { page: 1, pageSize: 101 }, database as never), /pageSize/);
  sqlite.close();
});

test("AI conversation and artifacts keep immutable scope snapshots across scope tightening", async () => {
  const { sqlite, database } = await createReadyDatabase();
  sqlite.exec(`CREATE TRIGGER reject_ai_conversation_scope BEFORE INSERT ON ai_conversation_scopes
    BEGIN SELECT RAISE(ABORT, 'scope storage unavailable'); END`);
  await assert.rejects(
    () => createConversation("must roll back", wideOwner, null, database as never),
    /scope storage unavailable/,
  );
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations WHERE title='must roll back'").get()?.total, 0);
  sqlite.exec("DROP TRIGGER reject_ai_conversation_scope");
  const conversationId = await createConversation("跨平台分析", wideOwner, null, database as never);
  const messageId = await appendConversationMessage(conversationId, "assistant", "分析结果", "message", database as never);
  const [artifact] = await persistAiTableArtifacts({
    conversationId,
    messageId,
    principal: wideOwner,
    candidates: [{
      kind: "table",
      title: "跨平台结果",
      sourceTool: "get_sales_summary",
      columns: ["platform"],
      rows: [["京东"], ["天猫"]],
      rowCount: 2,
      truncated: false,
    }],
    database: database as never,
  });

  assert.equal((await requireConversationAccess(conversationId, expandedOwner, database as never)).id, conversationId);
  await assert.rejects(
    () => requireConversationAccess(conversationId, narrowedOwner, database as never),
    /不存在或无权访问/,
  );
  const restrictedAdmin = { ...admin, scope: narrowedOwner.scope };
  await assert.rejects(
    () => requireConversationAccess(conversationId, restrictedAdmin, database as never),
    /不存在或无权访问/,
  );
  const coveringAdmin = { ...admin, scope: expandedOwner.scope };
  assert.equal((await requireConversationAccess(conversationId, coveringAdmin, database as never)).id, conversationId);
  await assert.rejects(
    () => persistAiTableArtifacts({
      conversationId,
      messageId,
      principal: narrowedOwner,
      candidates: [{
        kind: "table",
        title: "迟到写入",
        sourceTool: "get_sales_summary",
        columns: ["platform"],
        rows: [["不得写入"]],
        rowCount: 1,
        truncated: false,
      }],
      database: database as never,
    }),
    /target is unavailable/,
  );
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_artifacts WHERE conversation_id=?").get(conversationId)?.total, 1);
  assert.equal((await listAiArtifactsForConversation(conversationId, narrowedOwner, database as never)).size, 0);
  assert.equal(
    (await listAiArtifactsForConversation(conversationId, expandedOwner, database as never)).get(messageId)?.[0]?.id,
    artifact.id,
  );
  assert.equal(await getAiArtifactDownload(artifact.id, narrowedOwner, database as never), null);
  assert.ok(await getAiArtifactDownload(artifact.id, expandedOwner, database as never));

  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('legacy-without-scope', 'legacy', NULL, ?)` ).run(owner.email);
  assert.equal((await requireConversationAccess("legacy-without-scope", owner, database as never)).id, "legacy-without-scope");
  await assert.rejects(
    () => requireConversationAccess("legacy-without-scope", narrowedOwner, database as never),
    /不存在或无权访问/,
  );
  const malformedId = "malformed-scope";
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES (?, 'malformed', NULL, ?)` ).run(malformedId, owner.email);
  sqlite.prepare(`INSERT INTO ai_conversation_scopes (conversation_id, scope_json)
    VALUES (?, '{"platforms":["京东"]}')`).run(malformedId);
  await assert.rejects(
    () => requireConversationAccess(malformedId, narrowedOwner, database as never),
    /不存在或无权访问/,
  );
  const invalidJsonId = "invalid-json-scope";
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES (?, 'invalid json', NULL, ?)` ).run(invalidJsonId, owner.email);
  sqlite.prepare(`INSERT INTO ai_conversation_scopes (conversation_id, scope_json)
    VALUES (?, '{')`).run(invalidJsonId);
  await assert.rejects(
    () => requireConversationAccess(invalidJsonId, narrowedOwner, database as never),
    /不存在或无权访问/,
  );
  sqlite.close();
});

test("AI message cursor returns recent bounded UTF-8 content and loads older pages without overlap", async () => {
  const queries: QueryRecord[] = [];
  const { sqlite, database } = await createReadyDatabase(queries);
  await ensureAiArtifactSchema(database as never);
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('conversation-owner', '安全分页', NULL, ?)` ).run(owner.email);
  const insert = sqlite.prepare(`INSERT INTO ai_conversation_messages
    (id, conversation_id, role, content, message_kind, created_at)
    VALUES (?, 'conversation-owner', 'assistant', ?, 'message', ?)`);
  const largeContent = "😀".repeat(10_000);
  for (let index = 0; index < 135; index += 1) {
    insert.run(`message-${String(index).padStart(3, "0")}`, `${index}:${largeContent}`, `2026-08-20 12:${String(index % 60).padStart(2, "0")}:00`);
  }
  const insertArtifact = sqlite.prepare(`INSERT INTO ai_artifacts (
    id, conversation_id, message_id, owner_email, kind, title, file_name, mime_type,
    source_tool, columns_json, rows_json, row_count, truncated, content_digest, created_at
  ) VALUES (?, 'conversation-owner', ?, ?, 'table', 'table', 'table.csv',
    'text/csv; charset=utf-8', 'test_tool', '["value"]', '[[1]]', 1, 0, 'digest', ?)`);
  for (let messageIndex = 0; messageIndex < 135; messageIndex += 1) {
    const messageId = `message-${String(messageIndex).padStart(3, "0")}`;
    for (let artifactIndex = 0; artifactIndex < 3; artifactIndex += 1) {
      insertArtifact.run(`artifact-${messageIndex}-${artifactIndex}`, messageId, owner.email, `2026-08-20 12:${String(messageIndex % 60).padStart(2, "0")}:0${artifactIndex}`);
    }
  }

  queries.length = 0;
  const recent = await listConversationMessages("conversation-owner", owner, { pageSize: 30 }, database as never);
  assert.equal(recent.pagination.total, 135);
  assert.equal(recent.pagination.returned, 30);
  assert.equal(recent.pagination.hasMore, true);
  assert.ok(recent.pagination.nextBefore);
  assert.equal(recent.items[0]?.id, "message-105");
  assert.equal(recent.items.at(-1)?.id, "message-134");
  assert.equal(recent.items.every((item) => item.contentTruncated), true);
  assert.equal(recent.items.every((item) => item.contentBytes <= AI_MESSAGE_RESPONSE_BYTES_MAX), true);
  assert.ok(recent.items.reduce((total, item) => total + item.contentBytes, 0) <= AI_MESSAGE_PAGE_CONTENT_BYTES_MAX);
  assert.equal(recent.items.every((item) => !item.content.includes("�")), true);
  assert.equal(recent.items.every((item) => item.artifacts.length === 3), true);
  const artifactQuery = queries.find((query) => /FROM ai_artifacts/i.test(query.sql) && /json_each\(\?\)/i.test(query.sql));
  assert.ok(artifactQuery, "artifacts must be filtered to the current message page in SQL");
  assert.equal(JSON.parse(String(artifactQuery.values[3])).length, 30);
  assert.equal(artifactQuery.values.at(-1), 90);

  const older = await listConversationMessages("conversation-owner", owner, {
    pageSize: 30,
    before: recent.pagination.nextBefore,
  }, database as never);
  assert.equal(older.items[0]?.id, "message-075");
  assert.equal(older.items.at(-1)?.id, "message-104");
  assert.equal(new Set([...recent.items, ...older.items].map((item) => item.id)).size, 60);
  await assert.rejects(
    () => listConversationMessages("conversation-owner", other, { pageSize: 30 }, database as never),
    /不存在或无权访问/,
  );
  sqlite.close();
});

test("AI conversation deletion is owner-fenced, audited, and rolls back when audit storage fails", async () => {
  const { sqlite, database } = await createReadyDatabase();
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('conversation-delete', '不要进入审计的消息', NULL, ?)` ).run(owner.email);
  sqlite.prepare(`INSERT INTO ai_conversation_messages
    (id, conversation_id, role, content, message_kind)
    VALUES ('message-delete', 'conversation-delete', 'user', 'raw customer message', 'message')`).run();

  assert.equal(await deleteAiConversation("conversation-delete", other, "越权删除", database as never), false);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations").get()?.total, 1);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_deletion_audits").get()?.total, 0);

  assert.equal(await deleteAiConversation("conversation-delete", owner, "用户确认删除", database as never), true);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations").get()?.total, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages").get()?.total, 0);
  const audit = sqlite.prepare(`SELECT conversation_owner conversationOwner, actor_email actorEmail,
    actor_role actorRole, reason, deleted_message_count deletedMessageCount
    FROM ai_conversation_deletion_audits WHERE conversation_id='conversation-delete'`).get() as Record<string, unknown>;
  assert.deepEqual({ ...audit }, {
    conversationOwner: owner.email,
    actorEmail: owner.email,
    actorRole: owner.role,
    reason: "用户确认删除",
    deletedMessageCount: 1,
  });
  assert.equal(JSON.stringify(audit).includes("raw customer message"), false);

  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('conversation-rollback', 'rollback', NULL, ?)` ).run(owner.email);
  sqlite.prepare(`INSERT INTO ai_conversation_messages
    (id, conversation_id, role, content, message_kind)
    VALUES ('message-rollback', 'conversation-rollback', 'user', 'must survive', 'message')`).run();
  sqlite.exec(`CREATE TRIGGER reject_ai_conversation_deletion_audit
    BEFORE INSERT ON ai_conversation_deletion_audits
    BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END`);
  await assert.rejects(
    () => deleteAiConversation("conversation-rollback", owner, "用户确认删除", database as never),
    /audit unavailable/,
  );
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations WHERE id='conversation-rollback'").get()?.total, 1);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages WHERE conversation_id='conversation-rollback'").get()?.total, 1);
  sqlite.close();
});

test("concurrent append and delete never leave an orphan AI message", async () => {
  const { sqlite, database } = await createReadyDatabase();
  for (let index = 0; index < 12; index += 1) {
    const conversationId = `conversation-race-${index}`;
    sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
      VALUES (?, 'race', NULL, ?)` ).run(conversationId, owner.email);
    await Promise.allSettled([
      appendConversationMessage(conversationId, "user", `message ${index}`, database as never),
      deleteAiConversation(conversationId, owner, "并发删除", database as never),
    ]);
    assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations WHERE id=?").get(conversationId)?.total, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_messages WHERE conversation_id=?").get(conversationId)?.total, 0);
    assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_deletion_audits WHERE conversation_id=?").get(conversationId)?.total, 1);
  }
  sqlite.close();
});

test("late AI artifact persistence cannot recreate downloadable data after conversation deletion", async () => {
  const { sqlite, database } = await createReadyDatabase();
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('conversation-artifact-race', 'artifact race', NULL, ?)` ).run(owner.email);
  sqlite.prepare(`INSERT INTO ai_conversation_messages
    (id, conversation_id, role, content, message_kind)
    VALUES ('message-artifact-race', 'conversation-artifact-race', 'assistant', 'table ready', 'message')`).run();

  assert.equal(
    await deleteAiConversation("conversation-artifact-race", owner, "并发删除", database as never),
    true,
  );
  await assert.rejects(
    () => persistAiTableArtifacts({
      conversationId: "conversation-artifact-race",
      messageId: "message-artifact-race",
      principal: owner,
      candidates: [{
        kind: "table",
        title: "迟到表格",
        sourceTool: "get_sales_summary",
        columns: ["name"],
        rows: [["must not survive"]],
        rowCount: 1,
        truncated: false,
      }],
      database: database as never,
    }),
    /target is unavailable/,
  );
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_artifacts").get()?.total, 0);
  sqlite.prepare(`INSERT INTO ai_artifacts (
      id, conversation_id, message_id, owner_email, kind, title, file_name, mime_type,
      source_tool, columns_json, rows_json, row_count, truncated, content_digest
    ) VALUES (
      'ai-artifact-00000000-0000-4000-8000-000000000000',
      'conversation-artifact-race', 'message-artifact-race', ?, 'table', 'legacy orphan',
      'legacy.csv', 'text/csv; charset=utf-8', 'get_sales_summary', '["name"]',
      '[["must not download"]]', 1, 0, 'legacy-digest'
    )`).run(owner.email);
  assert.equal(
    await getAiArtifactDownload("ai-artifact-00000000-0000-4000-8000-000000000000", owner, database as never),
    null,
  );
  const audit = sqlite.prepare(`SELECT deleted_artifact_count deletedArtifactCount
    FROM ai_conversation_deletion_audits
    WHERE conversation_id='conversation-artifact-race'`).get() as { deletedArtifactCount: number };
  assert.equal(audit.deletedArtifactCount, 0);
  sqlite.close();
});

test("0062 forward migration and runtime upgrade are idempotent in both orders", async () => {
  const baseMigration = await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8");
  const auditMigration = await readFile(new URL("../drizzle/0062_ai_conversation_deletion_audit.sql", import.meta.url), "utf8");

  const migrationFirst = new DatabaseSync(":memory:");
  migrationFirst.exec(baseMigration);
  migrationFirst.exec(auditMigration);
  migrationFirst.exec(auditMigration);
  const migrationFirstDb = sqliteAdapter(migrationFirst);
  await ensureAiAssistantSchema(migrationFirstDb as never);
  assert.equal(migrationFirst.prepare("SELECT COUNT(*) total FROM ai_conversation_deletion_audits").get()?.total, 0);
  migrationFirst.close();

  const runtimeFirst = new DatabaseSync(":memory:");
  const runtimeFirstDb = sqliteAdapter(runtimeFirst);
  await ensureAiAssistantSchema(runtimeFirstDb as never);
  runtimeFirst.exec(auditMigration);
  runtimeFirst.exec(auditMigration);
  assert.equal(runtimeFirst.prepare(`SELECT COUNT(*) total FROM sqlite_master
    WHERE type='table' AND name='ai_conversation_deletion_audits'`).get()?.total, 1);
  runtimeFirst.close();
});

test("0063 scope migration and runtime upgrade are idempotent in both orders and preserve legacy rows", async () => {
  const baseMigration = await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8");
  const scopeMigration = await readFile(new URL("../drizzle/0063_ai_conversation_scope_snapshots.sql", import.meta.url), "utf8");

  const migrationFirst = new DatabaseSync(":memory:");
  migrationFirst.exec("PRAGMA foreign_keys = ON");
  migrationFirst.exec(baseMigration);
  migrationFirst.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('legacy-before-0063', 'legacy', NULL, 'legacy@example.com')`).run();
  migrationFirst.exec(scopeMigration);
  migrationFirst.exec(scopeMigration);
  const migrationFirstDb = sqliteAdapter(migrationFirst);
  await ensureAiAssistantSchema(migrationFirstDb as never);
  assert.equal(migrationFirst.prepare("SELECT COUNT(*) total FROM ai_conversations WHERE id='legacy-before-0063'").get()?.total, 1);
  assert.equal(migrationFirst.prepare("SELECT COUNT(*) total FROM ai_conversation_scopes WHERE conversation_id='legacy-before-0063'").get()?.total, 0);
  const createdId = await createConversation("scoped", wideOwner, null, migrationFirstDb as never);
  assert.equal(migrationFirst.prepare("SELECT COUNT(*) total FROM ai_conversation_scopes WHERE conversation_id=?").get(createdId)?.total, 1);
  migrationFirst.prepare("DELETE FROM ai_conversations WHERE id=?").run(createdId);
  assert.equal(migrationFirst.prepare("SELECT COUNT(*) total FROM ai_conversation_scopes WHERE conversation_id=?").get(createdId)?.total, 0);
  migrationFirst.close();

  const runtimeFirst = new DatabaseSync(":memory:");
  runtimeFirst.exec("PRAGMA foreign_keys = ON");
  const runtimeFirstDb = sqliteAdapter(runtimeFirst);
  await ensureAiAssistantSchema(runtimeFirstDb as never);
  runtimeFirst.exec(scopeMigration);
  runtimeFirst.exec(scopeMigration);
  assert.equal(runtimeFirst.prepare(`SELECT COUNT(*) total FROM sqlite_master
    WHERE type='table' AND name='ai_conversation_scopes'`).get()?.total, 1);
  const foreignKey = runtimeFirst.prepare("PRAGMA foreign_key_list(ai_conversation_scopes)").get() as Record<string, unknown>;
  assert.equal(foreignKey.table, "ai_conversations");
  assert.equal(String(foreignKey.on_delete).toUpperCase(), "CASCADE");
  runtimeFirst.close();

  const journal = JSON.parse(await readFile(new URL("../drizzle/meta/_journal.json", import.meta.url), "utf8")) as {
    entries: Array<{ idx: number; tag: string }>;
  };
  const scopeEntry = journal.entries.find((entry) => entry.tag === "0063_ai_conversation_scope_snapshots");
  assert.equal(scopeEntry?.idx, 63);
  assert.equal(scopeEntry?.tag, "0063_ai_conversation_scope_snapshots");
});

test("default AI model clear and upsert are one atomic D1 batch", async () => {
  const { sqlite, database } = await createReadyDatabase();
  const modelInput = (name: string) => ({
    name,
    protocol: "openai_compatible" as const,
    modelType: "text" as const,
    modelName: `provider-${name.toLowerCase()}`,
    baseUrl: "https://api.example.com/v1",
    status: "enabled" as const,
    isDefaultTextModel: true,
  });
  const modelA = await upsertAiModel(modelInput("A"), database as never);
  sqlite.exec(`CREATE TRIGGER reject_model_b BEFORE INSERT ON ai_models
    WHEN NEW.name = 'B'
    BEGIN SELECT RAISE(ABORT, 'model insert unavailable'); END`);
  await assert.rejects(
    () => upsertAiModel(modelInput("B"), database as never),
    /model insert unavailable/,
  );
  assert.equal(sqlite.prepare("SELECT is_default_text_model value FROM ai_models WHERE id=?").get(modelA.id)?.value, 1);
  sqlite.exec("DROP TRIGGER reject_model_b");

  const created = await Promise.all([
    upsertAiModel(modelInput("B"), database as never),
    upsertAiModel(modelInput("C"), database as never),
  ]);
  const defaults = sqlite.prepare(`SELECT id FROM ai_models
    WHERE model_type='text' AND status='enabled' AND is_default_text_model=1`).all();
  assert.equal(defaults.length, 1);
  assert.ok(created.map((item) => item.id).includes(String(defaults[0]?.id)));
  sqlite.close();
});

test("AI model versions fence edits, default changes, runtime resolution, and deletes", async () => {
  const { sqlite, database } = await createReadyDatabase();
  const first = await upsertAiModel({
    name: "模型 A",
    protocol: "openai_compatible",
    modelType: "text",
    modelName: "provider-a",
    baseUrl: "https://api.example.com/v1",
    status: "enabled",
    isDefaultTextModel: true,
  }, database as never);
  assert.equal(first.version, 1);

  const edited = await upsertAiModel({
    id: first.id,
    expectedVersion: first.version,
    name: "模型 A 已更新",
    protocol: first.protocol,
    modelType: first.modelType,
    modelName: first.modelName,
    status: first.status,
    isDefaultTextModel: true,
  }, database as never);
  assert.equal(edited.version, 2);
  await assert.rejects(() => upsertAiModel({
    id: first.id,
    expectedVersion: first.version,
    name: "过期覆盖",
    protocol: first.protocol,
    modelType: first.modelType,
    modelName: first.modelName,
    status: first.status,
    isDefaultTextModel: true,
  }, database as never), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 409);
    assert.equal((error as { code?: string }).code, "version_conflict");
    return true;
  });

  const second = await upsertAiModel({
    name: "模型 B",
    protocol: "openai_compatible",
    modelType: "text",
    modelName: "provider-b",
    baseUrl: "https://api.example.com/v1",
    status: "enabled",
    isDefaultTextModel: true,
  }, database as never);
  assert.equal(second.version, 1);
  const records = await listAiModels(database as never);
  const demoted = records.find((item) => item.id === first.id);
  assert.equal(demoted?.isDefaultTextModel, false);
  assert.equal(demoted?.version, 3);
  assert.equal((await resolveChatModel({ modelId: first.id, allowFallback: false }, database as never))?.version, 3);

  await assert.rejects(() => deleteAiModel(second.id, second.version + 1, database as never), (error: unknown) => {
    assert.equal((error as { status?: number }).status, 409);
    return true;
  });
  assert.equal(await deleteAiModel(second.id, second.version, database as never), true);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_models WHERE id=?").get(second.id)?.total, 0);
  sqlite.close();
});

test("AI model runtime schema adds version 1 to legacy databases", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8"));
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status)
    VALUES ('legacy-version-model', '旧视觉模型', 'openai_compatible', 'image', 'vision-model',
      'https://api.example.com/v1', 'encrypted', '1234', 'enabled')`).run();
  const database = sqliteAdapter(sqlite);
  await ensureAiAssistantSchema(database as never);
  assert.equal(sqlite.prepare("SELECT version FROM ai_models WHERE id='legacy-version-model'").get()?.version, 1);
  assert.equal((await listAiModels(database as never))[0]?.version, 1);
  sqlite.close();
});

test("AI model credentials never follow a protocol or origin change", async () => {
  const previousKey = process.env.AI_SECRET_ENCRYPTION_KEY;
  process.env.AI_SECRET_ENCRYPTION_KEY = "assistant-origin-boundary-test";
  const { sqlite, database } = await createReadyDatabase();
  try {
    const created = await upsertAiModel({
      name: "Origin A",
      protocol: "openai_compatible",
      modelType: "text",
      modelName: "provider-model",
      baseUrl: "https://api.example.com/v1",
      apiKey: "secret-origin-a",
      status: "enabled",
    }, database as never);
    await assert.rejects(upsertAiModel({
      id: created.id,
      expectedVersion: created.version,
      name: created.name,
      protocol: created.protocol,
      modelType: created.modelType,
      modelName: created.modelName,
      baseUrl: "https://api.other-provider.example/v1",
      status: created.status,
    }, database as never), /必须同时填写.*新 API Key/);
    const stored = sqlite.prepare("SELECT base_url, api_key_encrypted FROM ai_models WHERE id = ?")
      .get(created.id) as { base_url: string; api_key_encrypted: string };
    assert.equal(stored.base_url, "https://api.example.com/v1");
    assert.notEqual(stored.api_key_encrypted, "secret-origin-a");
  } finally {
    sqlite.close();
    if (previousKey === undefined) delete process.env.AI_SECRET_ENCRYPTION_KEY;
    else process.env.AI_SECRET_ENCRYPTION_KEY = previousKey;
  }
});

test("AI channel consumes streaming bodies under a 64 KiB cap and never exposes provider text", async () => {
  const { sqlite, database } = await createReadyDatabase();
  const channel = await upsertAiChannel({
    name: "企业微信群机器人",
    kind: "wechat_work_group_bot",
    status: "enabled",
    sendEnabled: true,
    callbackEnabled: false,
    webhookUrl: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=masked",
  }, database as never);
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = (async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(40 * 1024));
        controller.enqueue(new Uint8Array(40 * 1024));
        controller.close();
      },
    }), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;
    await assert.rejects(
      () => sendAiChannelText(channel.id, "hello", database as never),
      /64 KiB 安全上限/,
    );

    globalThis.fetch = (async () => Response.json({
      errcode: 40001,
      errmsg: "token=TOP_SECRET provider internal stack",
    }, { status: 500 })) as typeof fetch;
    let message = "";
    try {
      await sendAiChannelText(channel.id, "hello", database as never);
    } catch (error) {
      message = error instanceof Error ? error.message : String(error);
    }
    assert.match(message, /HTTP 500 · code 40001/);
    assert.equal(message.includes("TOP_SECRET"), false);
    assert.equal(message.includes("provider internal stack"), false);
  } finally {
    globalThis.fetch = originalFetch;
    sqlite.close();
  }
});

test("bounded JSON reader enforces the actual streamed bytes and strict UTF-8", async () => {
  const oversized = new Request("https://example.test", {
    method: "POST",
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`{"value":"${"x".repeat(80)}"}`));
        controller.close();
      },
    }),
    duplex: "half",
  } as RequestInit & { duplex: "half" });
  await assert.rejects(
    () => readBoundedJsonObject(oversized, 32),
    (error: unknown) => error instanceof Error && "status" in error && error.status === 413,
  );

  const malformedUtf8 = new Request("https://example.test", {
    method: "POST",
    body: new Uint8Array([0x7b, 0x22, 0x78, 0x22, 0x3a, 0x22, 0xc3, 0x28, 0x22, 0x7d]),
  });
  await assert.rejects(
    () => readBoundedJsonObject(malformedUtf8),
    (error: unknown) => error instanceof Error && "status" in error && error.status === 400,
  );
});

test("AI route contract rejects ambiguous integers and masks unknown failures", async () => {
  assert.throws(
    () => parseAiPositiveInteger(new URLSearchParams("page=1e2"), "page", 1, 10_000),
    /十进制正整数/,
  );
  assert.throws(
    () => parseAiPositiveInteger(new URLSearchParams("page=1&page=2"), "page", 1, 10_000),
    /不能重复/,
  );
  await assert.rejects(
    () => readAiJsonObject(new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify({ message: "x".repeat(70_000) }),
      headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    })),
    (error: unknown) => error instanceof Error && error.name === "PublicApiError" && "status" in error && error.status === 413,
  );
  await assert.rejects(
    () => readAiJsonObject(new Request("http://127.0.0.1:3000/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message: "blind paid request" }),
      headers: { "content-type": "text/plain", origin: "https://evil.example", "sec-fetch-site": "cross-site" },
    })),
    (error: unknown) => error instanceof Error && "status" in error && error.status === 403,
  );
  await assert.rejects(
    () => readAiJsonObject(new Request("http://127.0.0.1:3000/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message: "same-site subdomain request without an origin" }),
      headers: { "content-type": "application/json", "sec-fetch-site": "same-site" },
    })),
    (error: unknown) => error instanceof Error && "status" in error && error.status === 403,
  );
  await assert.rejects(
    () => readAiJsonObject(new Request("http://127.0.0.1:3000/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message: "request without browser origin proof" }),
      headers: { "content-type": "application/json" },
    })),
    (error: unknown) => error instanceof Error && "status" in error && error.status === 403,
  );
  await assert.rejects(
    () => readAiJsonObject(new Request("http://127.0.0.1:3000/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message: "wrong media type" }),
      headers: { "content-type": "text/plain", origin: "http://127.0.0.1:3000" },
    })),
    (error: unknown) => error instanceof Error && "status" in error && error.status === 415,
  );
  assert.deepEqual(
    await readAiJsonObject(new Request("http://127.0.0.1:3000/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message: "same origin" }),
      headers: { "content-type": "application/json; charset=utf-8", origin: "http://127.0.0.1:3000" },
    })),
    { message: "same origin" },
  );
  const response = aiRouteErrorResponse(new Error("secret SQL table name"), "AI 请求失败");
  assert.equal(response.status, 500);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { error: "AI 请求失败", code: "internal_error" });

  const routeFiles = await Promise.all([
    "chat/route.ts",
    "conversations/route.ts",
    "models/route.ts",
    "channels/route.ts",
    "tools/route.ts",
    "artifacts/[artifactId]/route.ts",
    "webhooks/[channelId]/route.ts",
  ].map((path) => readFile(new URL(`./legacy/app/api/ai/${path}`, import.meta.url), "utf8")));
  assert.equal(routeFiles.some((source) => /error instanceof Error \? error\.message/.test(source)), false);
  assert.equal(routeFiles.some((source) => /request\.json\(/.test(source)), false);
  assert.match(routeFiles[0], /parseAiPositiveInteger/);
  assert.match(routeFiles[1], /parseAiPositiveInteger/);

  const protectedWriteRoutes = await Promise.all([
    "chat/route.ts",
    "conversations/route.ts",
    "models/route.ts",
    "channels/route.ts",
    "space/jobs/route.ts",
    "space/jobs/[jobId]/cancel/route.ts",
    "space/profiles/route.ts",
    "space/templates/route.ts",
    "space/assets/[assetId]/route.ts",
  ].map((path) => readFile(new URL(`./legacy/app/api/ai/${path}`, import.meta.url), "utf8")));
  for (const source of protectedWriteRoutes) assert.match(source, /requireAiSameOriginWrite\(request\)/);
  assert.doesNotMatch(routeFiles[6]!, /requireAiSameOriginWrite/);

  const workerSource = await readFile(new URL("../worker/index.ts", import.meta.url), "utf8");
  assert.match(workerSource, /localDirectAccessRequested\(env\)\s*&&\s*!allowsLoopbackDevelopmentRequest\(request, env\)/);
});

test("enabled WeCom callbacks require an exact receiver identity at save and verification time", async () => {
  const { sqlite, database } = await createReadyDatabase();
  await assert.rejects(
    () => upsertAiChannel({
      name: "企业微信回调",
      kind: "wechat_work_app",
      status: "enabled",
      sendEnabled: false,
      callbackEnabled: true,
      callbackToken: "callback-token",
      aesKey: "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
      receiverId: "",
    }, database as never),
    /接收方 ID/,
  );
  const webhookRoute = await readFile(
    new URL("./legacy/app/api/ai/webhooks/[channelId]/route.ts", import.meta.url),
    "utf8",
  );
  assert.match(webhookRoute, /!channel\.receiverId/);
  assert.match(webhookRoute, /expectedReceiverId: channel\.receiverId/);
  assert.doesNotMatch(webhookRoute, /channel\.receiverId \|\| undefined/);
  sqlite.close();
});

test("AI assistant UI aborts stale list and message requests and exposes incremental loading", async () => {
  const page = await readFile(new URL("../app/ai-assistant-view.tsx", import.meta.url), "utf8");
  const workbench = await readFile(new URL("../app/ai-chat-workbench.tsx", import.meta.url), "utf8");
  assert.match(page, /conversationGenerationRef/);
  assert.match(page, /messageGenerationRef/);
  assert.match(page, /conversationControllerRef\.current\?\.abort\(\)/);
  assert.match(page, /messageControllerRef\.current\?\.abort\(\)/);
  assert.match(page, /generation !== conversationGenerationRef\.current/);
  assert.match(page, /generation !== messageGenerationRef\.current/);
  assert.match(page, /controller\.signal\.aborted \|\| generation !== messageGenerationRef\.current\) return;\s*throw reason;/);
  assert.match(page, /return \(\) => \{\s*messageControllerRef\.current\?\.abort\(\);\s*messageGenerationRef\.current \+= 1;/);
  assert.match(workbench, /加载更多对话/);
  assert.match(workbench, /加载更早消息/);
  assert.match(page, /onMore=\{\(\) => void loadMoreConversations\(\)\}/);
  assert.match(page, /onOlder=\{\(\) => void loadOlderMessages\(\)\}/);
  assert.match(page, /contentTruncated/);
  assert.match(page, /expectedVersion: item\.version/);
  assert.match(page, /deleteConfiguration\("model", item\.id, item\.name, item\.version\)/);
});

test("AI assistant loads full admin configuration only after the manager is opened", async () => {
  const page = await readFile(new URL("../app/ai-assistant-view.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(page, /Promise\.all\(\[loadConfiguration\([^\]]*loadConversations/);
  assert.match(page, /await loadConversations\(\{ signal: controller\.signal \}\);/);
  assert.match(page, /if \(!showManagement \|\| !isAdmin \|\| configurationState !== "idle"\) return;/);
  assert.match(page, /configurationState === "ready"/);
  assert.match(page, /setConfigurationError/);
  assert.match(page, /正在加载脱敏后的管理配置/);
});
