import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import {
  assertAiConversationAccess,
  deleteAiConversationData,
  isAiChatCapableModelType,
} from "./legacy/ai/conversation-management";
import {
  attachPendingAiChatResponse,
  markPendingAiChatSynchronized,
  requestAiAssistantConfiguration,
  requestAiAssistantConfigurationWithIdentityRecovery,
  requestAiConversationPage,
  requestAiConversationPageWithIdentityRecovery,
  resolvePendingAiChatRequest,
  shouldReleasePendingAiChatRequest,
} from "../app/ai-assistant-view";
import type { AppPrincipal } from "../lib/auth/authorization";
import type { D1Database } from "../lib/database/d1";

const owner: AppPrincipal = { email: "owner@example.com", displayName: "Owner", role: "analyst", scope: null };
const otherUser: AppPrincipal = { email: "other@example.com", displayName: "Other", role: "analyst", scope: null };

test("AI assistant chat bootstrap avoids admin configuration fetches until configuration is requested", async () => {
  const calls: string[] = [];
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    if (input.startsWith("/api/ai/conversations?")) {
      return Response.json({
        items: [],
        models: [{ id: "chat-model", name: "Chat", protocol: "openai_compatible", modelType: "text", modelName: "chat", isDefault: true }],
        pagination: { page: 1, pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false },
      });
    }
    if (input === "/api/ai/models") return Response.json({ items: [] });
    if (input === "/api/ai/channels") return Response.json({ items: [] });
    return Response.json({ error: "unexpected request" }, { status: 500 });
  };

  const chat = await requestAiConversationPage({}, fetcher);
  assert.equal(chat.models?.[0]?.id, "chat-model");
  assert.deepEqual(calls, ["/api/ai/conversations?page=1&pageSize=30"]);

  const configuration = await requestAiAssistantConfiguration(undefined, fetcher);
  assert.deepEqual(configuration, { models: [], channels: [] });
  assert.equal(calls.filter((url) => url === "/api/ai/models").length, 1);
  assert.equal(calls.filter((url) => url === "/api/ai/channels").length, 1);
});

test("AI chat bootstrap revalidates an existing sidebar identity and retries one transient 401 once", async () => {
  const calls: string[] = [];
  let conversationAttempts = 0;
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    if (input.startsWith("/api/ai/conversations?")) {
      conversationAttempts += 1;
      if (conversationAttempts === 1) {
        return Response.json(
          { error: "请先使用 ChatGPT 账号登录", code: "authentication_required" },
          { status: 401 },
        );
      }
      return Response.json({
        items: [],
        models: [{ id: "chat-model", name: "Chat", protocol: "openai_compatible", modelType: "text", modelName: "chat", isDefault: true }],
        pagination: { page: 1, pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false },
      });
    }
    if (input === "/api/auth/me") {
      return Response.json({
        user: {
          email: "local-admin@teruisi.local",
          displayName: "本地管理员",
          role: "admin",
          roleLabel: "管理员",
          scopeRestricted: false,
        },
      });
    }
    return Response.json({ error: "unexpected request" }, { status: 500 });
  };

  const payload = await requestAiConversationPageWithIdentityRecovery({
    currentUser: {
      email: "local-admin@teruisi.local",
      displayName: "本地管理员",
      role: "admin",
      roleLabel: "管理员",
      scopeRestricted: false,
    },
  }, fetcher);

  assert.equal(payload.models?.[0]?.id, "chat-model");
  assert.deepEqual(calls, [
    "/api/ai/conversations?page=1&pageSize=30",
    "/api/auth/me",
    "/api/ai/conversations?page=1&pageSize=30",
  ]);
});

test("AI chat bootstrap preserves a real unauthenticated 401 without retrying", async () => {
  const calls: string[] = [];
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    return Response.json(
      { error: "请先使用 ChatGPT 账号登录", code: "authentication_required" },
      { status: 401 },
    );
  };

  await assert.rejects(
    requestAiConversationPageWithIdentityRecovery({ currentUser: null }, fetcher),
    /请先使用 ChatGPT 账号登录/,
  );
  assert.deepEqual(calls, ["/api/ai/conversations?page=1&pageSize=30"]);
});

test("AI chat bootstrap with a stale sidebar user stops when auth revalidation is genuinely unauthenticated", async () => {
  const calls: string[] = [];
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    return Response.json(
      { error: "请先使用 ChatGPT 账号登录", code: "authentication_required" },
      { status: 401 },
    );
  };

  await assert.rejects(
    requestAiConversationPageWithIdentityRecovery({
      currentUser: {
        email: "owner@example.com",
        displayName: "Owner",
        role: "analyst",
        roleLabel: "分析员",
        scopeRestricted: false,
      },
    }, fetcher),
    /请先使用 ChatGPT 账号登录/,
  );
  assert.deepEqual(calls, [
    "/api/ai/conversations?page=1&pageSize=30",
    "/api/auth/me",
  ]);
});

test("AI chat bootstrap recovers one transient network failure after identity revalidation", async () => {
  const calls: string[] = [];
  let conversationAttempts = 0;
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    if (input.startsWith("/api/ai/conversations?")) {
      conversationAttempts += 1;
      if (conversationAttempts === 1) throw new TypeError("connection reset");
      return Response.json({
        items: [],
        models: [],
        pagination: { page: 1, pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false },
      });
    }
    if (input === "/api/auth/me") {
      return Response.json({ user: { email: "local-admin@teruisi.local" } });
    }
    return Response.json({ error: "unexpected request" }, { status: 500 });
  };

  const payload = await requestAiConversationPageWithIdentityRecovery({
    currentUser: {
      email: "local-admin@teruisi.local",
      displayName: "本地管理员",
      role: "admin",
      roleLabel: "管理员",
      scopeRestricted: false,
    },
  }, fetcher);

  assert.equal(payload.pagination?.total, 0);
  assert.deepEqual(calls, [
    "/api/ai/conversations?page=1&pageSize=30",
    "/api/auth/me",
    "/api/ai/conversations?page=1&pageSize=30",
  ]);
});

test("AI chat bootstrap stops after the single recovered retry also fails", async () => {
  const calls: string[] = [];
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    if (input === "/api/auth/me") {
      return Response.json({ user: { email: "local-admin@teruisi.local" } });
    }
    return Response.json(
      { error: "服务正在重载", code: "internal_error" },
      { status: 503 },
    );
  };

  await assert.rejects(
    requestAiConversationPageWithIdentityRecovery({
      currentUser: {
        email: "local-admin@teruisi.local",
        displayName: "本地管理员",
        role: "admin",
        roleLabel: "管理员",
        scopeRestricted: false,
      },
    }, fetcher),
    /服务正在重载/,
  );
  assert.deepEqual(calls, [
    "/api/ai/conversations?page=1&pageSize=30",
    "/api/auth/me",
    "/api/ai/conversations?page=1&pageSize=30",
  ]);
});

test("AI management GET bootstrap revalidates identity and retries transient model configuration failure once", async () => {
  const calls: string[] = [];
  let modelAttempts = 0;
  const fetcher = async (input: string): Promise<Response> => {
    calls.push(input);
    if (input === "/api/ai/models") {
      modelAttempts += 1;
      return modelAttempts === 1
        ? Response.json({ error: "Worker 正在重载", code: "internal_error" }, { status: 503 })
        : Response.json({ items: [] });
    }
    if (input === "/api/ai/channels") return Response.json({ items: [] });
    if (input === "/api/auth/me") {
      return Response.json({ user: { email: "local-admin@teruisi.local" } });
    }
    return Response.json({ error: "unexpected request" }, { status: 500 });
  };

  const result = await requestAiAssistantConfigurationWithIdentityRecovery({
    currentUser: {
      email: "local-admin@teruisi.local",
      displayName: "本地管理员",
      role: "admin",
      roleLabel: "管理员",
      scopeRestricted: false,
    },
  }, fetcher);

  assert.deepEqual(result, { models: [], channels: [] });
  assert.deepEqual(calls, [
    "/api/ai/models",
    "/api/ai/channels",
    "/api/auth/me",
    "/api/ai/models",
    "/api/ai/channels",
  ]);
});

test("new-conversation response loss reuses the exact original chat payload and client request id", () => {
  const original = resolvePendingAiChatRequest(null, {
    conversationId: undefined,
    modelId: "model-default",
    message: "查询昨日销售额",
    title: "小特对话",
  }, () => "stable-client-request-id");
  const afterRefresh = resolvePendingAiChatRequest(original, {
    conversationId: "ai-conv-created-after-response-loss",
    modelId: "model-refreshed",
    message: "查询昨日销售额",
    title: "小特对话",
  }, () => "must-not-be-created");

  assert.equal(afterRefresh, original);
  assert.equal(afterRefresh.clientRequestId, "stable-client-request-id");
  assert.deepEqual(afterRefresh.requestPayload, {
    conversationId: undefined,
    modelId: "model-default",
    message: "查询昨日销售额",
    title: "小特对话",
  });
});

test("successful chat receipt remains pending until conversation and assistant message both synchronize", () => {
  const claimed = resolvePendingAiChatRequest(null, {
    message: "生成经营摘要",
    title: "小特对话",
  }, () => "sync-client-request-id");
  const acknowledged = attachPendingAiChatResponse(claimed, {
    conversationId: "ai-conv-sync",
    assistantMessageId: "ai-msg-sync",
  });
  const conversationOnly = markPendingAiChatSynchronized(acknowledged, {
    kind: "conversation",
    conversationId: "ai-conv-sync",
  });
  assert.ok(conversationOnly, "a message synchronization failure must preserve the receipt");
  assert.deepEqual(conversationOnly.synchronized, { conversation: true, message: false });
  const unrelatedMessage = markPendingAiChatSynchronized(conversationOnly, {
    kind: "message",
    conversationId: "ai-conv-sync",
    assistantMessageId: "ai-msg-other",
  });
  assert.equal(unrelatedMessage, conversationOnly);
  assert.equal(markPendingAiChatSynchronized(conversationOnly, {
    kind: "message",
    conversationId: "ai-conv-sync",
    assistantMessageId: "ai-msg-sync",
  }), null);
});

test("chat UI rotates only receipts that the server proves were not provider-dispatched", () => {
  assert.equal(shouldReleasePendingAiChatRequest("ai_chat_not_dispatched"), true);
  assert.equal(shouldReleasePendingAiChatRequest("ai_request_cancelled"), true);
  assert.equal(shouldReleasePendingAiChatRequest("ai_chat_result_unknown"), false);
  assert.equal(shouldReleasePendingAiChatRequest("rate_limited"), false);
  assert.equal(shouldReleasePendingAiChatRequest(undefined), false);
});

test("chat capability admits text and vision models while rejecting legacy or unknown types", async () => {
  assert.equal(isAiChatCapableModelType("text"), true);
  assert.equal(isAiChatCapableModelType("vision"), true);
  assert.equal(isAiChatCapableModelType("image"), false);
  assert.equal(isAiChatCapableModelType("audio"), false);

  const [service, workflow] = await Promise.all([
    readFile(new URL("./legacy/ai/assistant-service.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/question-workflow.ts", import.meta.url), "utf8"),
  ]);
  assert.match(service, /WHERE model_type IN \('text', 'vision'\) AND status = 'enabled'/);
  assert.match(service, /WHERE id = \? AND model_type IN \('text', 'vision'\) AND status = 'enabled'/);
  assert.match(workflow, /selectConversationModel/);
  assert.doesNotMatch(workflow, /已有对话已固定模型/);
});

test("conversation deletion denies non-owners and atomically removes messages and artifacts", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE ai_conversations (id TEXT PRIMARY KEY, created_by TEXT NOT NULL);
    CREATE TABLE ai_conversation_messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL);
    CREATE TABLE ai_artifacts (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL);
    CREATE TABLE ai_artifact_deliveries (id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL);
    INSERT INTO ai_conversations VALUES ('conversation-1','owner@example.com');
    INSERT INTO ai_conversation_messages VALUES ('message-1','conversation-1');
    INSERT INTO ai_artifacts VALUES ('artifact-1','conversation-1');
    INSERT INTO ai_artifact_deliveries VALUES ('delivery-1','artifact-1');
  `);
  const db = sqliteAdapter(sqlite);

  assert.throws(() => assertAiConversationAccess({ createdBy: owner.email }, otherUser), /无权访问该对话/);
  assert.doesNotThrow(() => assertAiConversationAccess({ createdBy: owner.email }, owner));
  assert.equal(await deleteAiConversationData("conversation-1", db), true);
  assert.equal(countRows(sqlite, "ai_conversations"), 0);
  assert.equal(countRows(sqlite, "ai_conversation_messages"), 0);
  assert.equal(countRows(sqlite, "ai_artifacts"), 0);
  assert.equal(countRows(sqlite, "ai_artifact_deliveries"), 1);
  assert.equal(await deleteAiConversationData("conversation-1", db), false);
  sqlite.close();
});

function countRows(sqlite: DatabaseSync, table: string): number {
  return Number((sqlite.prepare(`SELECT COUNT(*) total FROM ${table}`).get() as { total: number }).total);
}

function sqliteAdapter(sqlite: DatabaseSync): D1Database {
  return {
    prepare(sql: string) {
      let values: Array<string | number | bigint | Uint8Array | null> = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as typeof values; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() { const result = sqlite.prepare(sql).run(...values); return { meta: { changes: Number(result.changes) } }; },
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
  } as unknown as D1Database;
}
