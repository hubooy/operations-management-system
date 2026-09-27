import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";
import { installDjangoAccessControlFixture } from "./access-control-service-fixture";

const testEnvironment: {
  DB?: unknown;
  AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST?: string;
  TERUISI_RUNTIME_ENV?: string;
} = {
  AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST: "https://api.example.com",
  TERUISI_RUNTIME_ENV: "production",
};
(globalThis as typeof globalThis & { __aiApiRouteEnv?: typeof testEnvironment }).__aiApiRouteEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__aiApiRouteEnv;",
        shortCircuit: true,
      };
    }
    if (specifier === "next/headers") {
      return {
        url: "data:text/javascript,export async function headers(){return new Headers({'oai-authenticated-user-email':'owner@example.com'});}",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

function sqliteAdapter(sqlite: DatabaseSync) {
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
          const row = sqlite.prepare(sql).get(...values) as Record<string, unknown> | undefined;
          return (column ? row?.[column] : row ?? null) as T | null;
        },
        async all<T>() {
          return { results: sqlite.prepare(sql).all(...values) as T[] };
        },
        async run() {
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

test("AI routes enforce strict pagination, SQL owner scope, bounded bodies, audit delete, and safe errors", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const database = sqliteAdapter(sqlite);
  testEnvironment.DB = database;
  installDjangoAccessControlFixture(sqlite);
  const { ensureAiAssistantSchema, resolveChatModel } = await import("./legacy/ai/assistant-service");
  const { resolveAiModelEndpointUrl } = await import("../lib/ai/endpoint-security");
  await ensureAiAssistantSchema(database as never);
  sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
    VALUES ('owner@example.com', 'Owner', 'analyst', 'active', NULL)`).run();
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by, updated_at)
    VALUES ('owner-one', 'Owner 1', NULL, 'owner@example.com', '2026-08-20 12:00:00')`).run();
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by, updated_at)
    VALUES ('owner-two', 'Owner 2', NULL, 'owner@example.com', '2026-08-20 11:00:00')`).run();
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by, updated_at)
    VALUES ('other-one', 'Other', NULL, 'other@example.com', '2026-08-20 13:00:00')`).run();
  sqlite.prepare(`INSERT INTO ai_conversation_messages
    (id, conversation_id, role, content, message_kind)
    VALUES ('message-one', 'owner-one', 'user', 'hello', 'message')`).run();

  const conversationsRoute = await import("./legacy/app/api/ai/conversations/route");
  const chatRoute = await import("./legacy/app/api/ai/chat/route");
  const modelsRoute = await import("./legacy/app/api/ai/models/route");
  const channelsRoute = await import("./legacy/app/api/ai/channels/route");

  const listResponse = await conversationsRoute.GET(new Request("https://example.test/api/ai/conversations?page=1&pageSize=1"));
  assert.equal(listResponse.status, 200);
  assert.equal(listResponse.headers.get("cache-control"), "no-store");
  const listPayload = await listResponse.json() as { items: Array<{ id: string }>; pagination: { total: number; returned: number; hasMore: boolean } };
  assert.deepEqual(listPayload.items.map((item) => item.id), ["owner-one"]);
  assert.deepEqual(listPayload.pagination, { page: 1, pageSize: 1, total: 2, returned: 1, truncated: true, hasMore: true });

  for (const query of ["page=1e2", "page=1&page=2", "pageSize=101", "page=+1"]) {
    const response = await conversationsRoute.GET(new Request(`https://example.test/api/ai/conversations?${query}`));
    assert.equal(response.status, 400, query);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }

  const messageResponse = await chatRoute.GET(new Request("https://example.test/api/ai/chat?conversationId=owner-one&pageSize=1"));
  assert.equal(messageResponse.status, 200);
  const messagePayload = await messageResponse.json() as { items: Array<{ id: string }>; pagination: { total: number } };
  assert.deepEqual(messagePayload.items.map((item) => item.id), ["message-one"]);
  assert.equal(messagePayload.pagination.total, 1);
  for (const query of [
    "conversationId=owner-one&pageSize=1e2",
    "conversationId=owner-one&before=+1",
    "conversationId=owner-one&before=1&before=2",
    "conversationId=other-one&pageSize=1",
  ]) {
    const response = await chatRoute.GET(new Request(`https://example.test/api/ai/chat?${query}`));
    assert.equal(response.status, query.includes("other-one") ? 404 : 400, query);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }

  const oversized = await chatRoute.POST(new Request("https://example.test/api/ai/chat", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({ clientRequestId: "oversized-chat-request", message: "中".repeat(20_000) }),
  }));
  assert.equal(oversized.status, 413);
  assert.equal(oversized.headers.get("cache-control"), "no-store");

  const deniedDelete = await conversationsRoute.DELETE(new Request("https://example.test/api/ai/conversations?id=other-one", {
    method: "DELETE",
    headers: { "sec-fetch-site": "same-origin" },
  }));
  assert.equal(deniedDelete.status, 404);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations WHERE id='other-one'").get()?.total, 1);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_deletion_audits WHERE conversation_id='other-one'").get()?.total, 0);
  const ownerDelete = await conversationsRoute.DELETE(new Request("https://example.test/api/ai/conversations?id=owner-one", {
    method: "DELETE",
    headers: { "sec-fetch-site": "same-origin" },
  }));
  assert.equal(ownerDelete.status, 200);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversations WHERE id='owner-one'").get()?.total, 0);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_conversation_deletion_audits WHERE conversation_id='owner-one'").get()?.total, 1);

  const adminOnly = await modelsRoute.GET();
  assert.equal(adminOnly.status, 403);
  assert.equal(adminOnly.headers.get("cache-control"), "no-store");
  sqlite.prepare("UPDATE app_users SET role='admin', scope_json=NULL WHERE email='owner@example.com'").run();
  const legacyBaseUrl = "https://api.example.com/v1?api-version=2026-08-01&Ocp-Apim-Subscription-Key=LEGACY_SUBSCRIPTION&x-functions-key=LEGACY_FUNCTION&code=LEGACY_CODE&tenant=tenant-a";
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status)
    VALUES ('legacy-query-secret', '旧模型', 'openai_compatible', 'text', 'legacy-model', ?, 'encrypted', '1234', 'enabled')`)
    .run(legacyBaseUrl);
  const modelsResponse = await modelsRoute.GET();
  assert.equal(modelsResponse.status, 200);
  const modelsPayload = await modelsResponse.json() as { items: Array<{ id: string; version: number; baseUrl: string }> };
  const legacyDto = modelsPayload.items.find((item) => item.id === "legacy-query-secret");
  assert.equal(legacyDto?.baseUrl, "https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a");
  assert.equal(legacyDto?.version, 1);
  assert.doesNotMatch(JSON.stringify(modelsPayload), /LEGACY_SUBSCRIPTION|LEGACY_FUNCTION|LEGACY_CODE|subscription-key|functions-key|[?&]code=/i);

  const runtimeModel = await resolveChatModel({ modelId: "legacy-query-secret", allowFallback: false }, database as never);
  assert.equal(runtimeModel?.baseUrl, legacyBaseUrl);
  assert.equal(
    runtimeModel ? resolveAiModelEndpointUrl(runtimeModel.baseUrl, runtimeModel.protocol) : "",
    "https://api.example.com/v1/chat/completions?api-version=2026-08-01&Ocp-Apim-Subscription-Key=LEGACY_SUBSCRIPTION&x-functions-key=LEGACY_FUNCTION&code=LEGACY_CODE&tenant=tenant-a",
  );

  const updateWithoutBaseUrl = await modelsRoute.POST(new Request("https://example.test/api/ai/models", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({
      id: "legacy-query-secret",
      expectedVersion: legacyDto?.version,
      name: "旧模型已改名",
      protocol: "openai_compatible",
      modelType: "text",
      modelName: "legacy-model",
      status: "enabled",
    }),
  }));
  assert.equal(updateWithoutBaseUrl.status, 400);
  assert.equal(sqlite.prepare("SELECT base_url FROM ai_models WHERE id='legacy-query-secret'").get()?.base_url, legacyBaseUrl);
  assert.equal(sqlite.prepare("SELECT name FROM ai_models WHERE id='legacy-query-secret'").get()?.name, "旧模型");
  assert.doesNotMatch(JSON.stringify(await updateWithoutBaseUrl.json()), /LEGACY_SUBSCRIPTION|LEGACY_FUNCTION|LEGACY_CODE|subscription-key|functions-key|[?&]code=/i);

  const regularQuery = await modelsRoute.POST(new Request("https://example.test/api/ai/models", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({
      name: "普通查询参数模型",
      protocol: "openai_compatible",
      modelType: "text",
      modelName: "regular-model",
      baseUrl: "https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a",
      status: "disabled",
    }),
  }));
  assert.equal(regularQuery.status, 200);
  const regularPayload = await regularQuery.json() as { item: { id: string; version: number; baseUrl: string } };
  assert.equal(regularPayload.item.baseUrl, "https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a");
  assert.equal(regularPayload.item.version, 1);
  assert.equal(
    sqlite.prepare("SELECT base_url FROM ai_models WHERE model_name='regular-model'").get()?.base_url,
    "https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a",
  );

  const editedRegular = await modelsRoute.POST(new Request("https://example.test/api/ai/models", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({
      id: regularPayload.item.id,
      expectedVersion: regularPayload.item.version,
      name: "普通查询参数模型（已编辑）",
      protocol: "openai_compatible",
      modelType: "text",
      modelName: "regular-model",
      status: "disabled",
    }),
  }));
  assert.equal(editedRegular.status, 200);
  const editedRegularPayload = await editedRegular.json() as { item: { version: number } };
  assert.equal(editedRegularPayload.item.version, 2);
  const staleRegular = await modelsRoute.POST(new Request("https://example.test/api/ai/models", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({
      id: regularPayload.item.id,
      expectedVersion: regularPayload.item.version,
      name: "过期覆盖",
      protocol: "openai_compatible",
      modelType: "text",
      modelName: "regular-model",
      status: "disabled",
    }),
  }));
  assert.equal(staleRegular.status, 409);
  assert.equal((await staleRegular.json() as { code?: string }).code, "version_conflict");
  const missingDeleteVersion = await modelsRoute.DELETE(new Request(
    `https://example.test/api/ai/models?id=${encodeURIComponent(regularPayload.item.id)}`,
    { method: "DELETE", headers: { "sec-fetch-site": "same-origin" } },
  ));
  assert.equal(missingDeleteVersion.status, 400);
  const staleDelete = await modelsRoute.DELETE(new Request(
    `https://example.test/api/ai/models?id=${encodeURIComponent(regularPayload.item.id)}&expectedVersion=1`,
    { method: "DELETE", headers: { "sec-fetch-site": "same-origin" } },
  ));
  assert.equal(staleDelete.status, 409);
  const currentDelete = await modelsRoute.DELETE(new Request(
    `https://example.test/api/ai/models?id=${encodeURIComponent(regularPayload.item.id)}&expectedVersion=2`,
    { method: "DELETE", headers: { "sec-fetch-site": "same-origin" } },
  ));
  assert.equal(currentDelete.status, 200);

  const rejectedSecret = await modelsRoute.POST(new Request("https://example.test/api/ai/models", {
    method: "POST",
    headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
    body: JSON.stringify({
      name: "错误模型",
      protocol: "openai_compatible",
      modelType: "text",
      modelName: "bad-model",
      baseUrl: "https://api.example.com/v1?api-Key=NEW_SECRET",
      status: "enabled",
    }),
  }));
  assert.equal(rejectedSecret.status, 400);
  assert.equal(rejectedSecret.headers.get("cache-control"), "no-store");
  assert.doesNotMatch(JSON.stringify(await rejectedSecret.json()), /NEW_SECRET/);
  assert.equal(sqlite.prepare("SELECT COUNT(*) total FROM ai_models WHERE model_name='bad-model'").get()?.total, 0);

  for (const [index, key] of [
    "subscription-key",
    "Ocp-Apim-Subscription-Key",
    "x-functions-key",
    "code",
  ].entries()) {
    const secretValue = `ROUTE_SECRET_${index}`;
    const response = await modelsRoute.POST(new Request("https://example.test/api/ai/models", {
      method: "POST",
      headers: { "content-type": "application/json", "sec-fetch-site": "same-origin" },
      body: JSON.stringify({
        name: `敏感查询参数-${index}`,
        protocol: "openai_compatible",
        modelType: "text",
        modelName: `sensitive-query-${index}`,
        baseUrl: `https://api.example.com/v1?${encodeURIComponent(key)}=${secretValue}`,
        status: "enabled",
      }),
    }));
    assert.equal(response.status, 400, key);
    assert.doesNotMatch(JSON.stringify(await response.json()), new RegExp(secretValue), key);
    assert.equal(
      sqlite.prepare("SELECT COUNT(*) total FROM ai_models WHERE model_name=?").get(`sensitive-query-${index}`)?.total,
      0,
      key,
    );
  }

  sqlite.prepare(`UPDATE app_users SET role='admin', scope_json=? WHERE email='owner@example.com'`)
    .run(JSON.stringify({ warehouses: [], channels: [], platforms: ["京东"] }));
  for (const response of [await modelsRoute.GET(), await channelsRoute.GET()]) {
    assert.equal(response.status, 403, "restricted admin must not read global AI configuration");
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal((await response.json() as { code?: string }).code, "access_denied");
  }

  testEnvironment.DB = {
    prepare() { throw new Error("SECRET_DATABASE_TABLE"); },
    async batch() { throw new Error("SECRET_DATABASE_TABLE"); },
  };
  const failed = await conversationsRoute.GET(new Request("https://example.test/api/ai/conversations?page=1"));
  assert.equal(failed.status, 500);
  assert.equal(failed.headers.get("cache-control"), "no-store");
  const failedBody = JSON.stringify(await failed.json());
  assert.equal(failedBody.includes("SECRET_DATABASE_TABLE"), false);
  sqlite.close();
});
