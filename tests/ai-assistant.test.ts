import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";

import {
  isSensitiveAiModelQueryKey,
  maskWebhookUrl,
  normalizeAiEndpointUrl,
  normalizeAiModelEndpointForStorage,
  redactAiModelEndpointUrl,
  resolveAiModelEndpointUrl,
} from "../lib/ai/endpoint-security";
import {
  createDingTalkSignature,
  createWeComSignature,
  verifyDingTalkSignature,
  verifyWeComSignature,
} from "../lib/ai/channel-callbacks";
import { buildOpenAiChatRequestBody, completeText, resolveModelToolLoopLimits } from "../lib/ai/model-gateway";
import { probeVisionModelConnection } from "../lib/market/annotation-model";

test("AI endpoint validation rejects insecure and private targets", () => {
  assert.equal(normalizeAiEndpointUrl("https://api.example.com/v1/", "model"), "https://api.example.com/v1");
  assert.equal(normalizeAiEndpointUrl("https://oapi.dingtalk.com/robot/send?access_token=secret", "channel"), "https://oapi.dingtalk.com/robot/send?access_token=secret");
  assert.throws(() => normalizeAiEndpointUrl("http://api.example.com/v1", "model"), /HTTPS/);
  assert.throws(() => normalizeAiEndpointUrl("https://127.0.0.1/private", "channel"), /内网|localhost/);
  assert.throws(() => normalizeAiEndpointUrl("https://localhost./private", "model"), /内网|localhost/);
  assert.throws(() => normalizeAiEndpointUrl("https://foo.localhost./private", "model"), /内网|localhost/);
  assert.throws(() => normalizeAiEndpointUrl("https://[fe90::1]/private", "model"), /内网|localhost/);
  assert.throws(() => normalizeAiEndpointUrl("https://[::ffff:127.0.0.1]/private", "model"), /内网|localhost/);
  assert.throws(() => normalizeAiEndpointUrl("https://user:pass@example.com/v1", "model"), /用户名/);
});

test("production AI model endpoints require an exact hostname allowlist", () => {
  const mutableEnvironment = process.env as Record<string, string | undefined>;
  const previousNodeEnvironment = mutableEnvironment.NODE_ENV;
  const previousAllowlist = mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST;
  try {
    mutableEnvironment.NODE_ENV = "production";
    mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST = "models.example.com";
    assert.equal(normalizeAiEndpointUrl("https://models.example.com/v1/", "model"), "https://models.example.com/v1");
    assert.equal(normalizeAiEndpointUrl("https://api.openai.com/v1", "model"), "https://api.openai.com/v1");
    assert.throws(() => normalizeAiEndpointUrl("https://api.example.com/v1", "model"), /白名单/);
    assert.throws(() => normalizeAiEndpointUrl("https://api.openai.com:8443/v1", "model"), /白名单/);
    assert.equal(normalizeAiEndpointUrl("https://models.example.com./v1", "model"), "https://models.example.com/v1");
  } finally {
    if (previousNodeEnvironment === undefined) delete mutableEnvironment.NODE_ENV;
    else mutableEnvironment.NODE_ENV = previousNodeEnvironment;
    if (previousAllowlist === undefined) delete mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST;
    else mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST = previousAllowlist;
  }
});

test("AI model endpoint accepts either a provider root or a complete request URL", () => {
  assert.equal(resolveAiModelEndpointUrl("https://api.example.com/v1", "openai_compatible"), "https://api.example.com/v1/chat/completions");
  assert.equal(resolveAiModelEndpointUrl("https://api.example.com/v1/chat/completions", "openai_compatible"), "https://api.example.com/v1/chat/completions");
  assert.equal(resolveAiModelEndpointUrl("https://api.example.com/v1", "anthropic"), "https://api.example.com/v1/messages");
});

test("text and vision dispatch validate the runtime origin before touching model credentials", async () => {
  const mutableEnvironment = process.env as Record<string, string | undefined>;
  const previousNodeEnvironment = mutableEnvironment.NODE_ENV;
  const previousAllowlist = mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST;
  try {
    mutableEnvironment.NODE_ENV = "production";
    mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST = "models.example.com";
    await assert.rejects(() => completeText({
      model: {
        id: "blocked-text",
        name: "blocked text",
        protocol: "openai_compatible",
        modelName: "test-model",
        baseUrl: "https://blocked.example/v1",
        apiKeyEncrypted: "",
        timeoutMs: 3_000,
        maxTokens: 128,
        reasoningMode: "auto",
        temperature: 0,
        maxToolRounds: 1,
        maxTotalToolCalls: 1,
      },
      messages: [{ role: "user", content: "test" }],
    }), /白名单/);
    await assert.rejects(() => probeVisionModelConnection({
      id: "blocked-vision",
      name: "blocked vision",
      protocol: "anthropic",
      model_type: "vision",
      model_name: "test-model",
      base_url: "https://blocked.example/v1",
      api_key_encrypted: "",
      status: "enabled",
    }), /白名单/);
  } finally {
    if (previousNodeEnvironment === undefined) delete mutableEnvironment.NODE_ENV;
    else mutableEnvironment.NODE_ENV = previousNodeEnvironment;
    if (previousAllowlist === undefined) delete mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST;
    else mutableEnvironment.AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST = previousAllowlist;
  }
});

test("AI model endpoints reject sensitive query keys and redact legacy values without changing runtime resolution", () => {
  for (const key of [
    "api_key",
    "API-Key",
    "api.Key",
    "access_token",
    "x-amz-signature",
    "credential",
    "Authorization",
    "subscription-key",
    "subscriptionKey",
    "Ocp-Apim-Subscription-Key",
    "x-functions-key",
    "code",
    "密钥",
  ]) {
    assert.equal(isSensitiveAiModelQueryKey(key), true, key);
    assert.throws(
      () => normalizeAiModelEndpointForStorage(`https://api.example.com/v1?${encodeURIComponent(key)}=DO_NOT_EXPOSE`),
      /敏感查询参数/,
      key,
    );
  }
  for (const key of ["api-version", "monkey", "hockey", "postal-code", "tenant", "deployment"]) {
    assert.equal(isSensitiveAiModelQueryKey(key), false, key);
  }
  const legacy = "https://api.example.com/v1?api-version=2026-08-01&API_KEY=TOP_SECRET&tenant=tenant-a&x-amz-signature=AWS_SECRET";
  const redacted = redactAiModelEndpointUrl(legacy);
  assert.equal(redacted, "https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a");
  assert.doesNotMatch(redacted, /TOP_SECRET|AWS_SECRET|API_KEY|signature/i);
  assert.equal(
    resolveAiModelEndpointUrl(legacy, "openai_compatible"),
    "https://api.example.com/v1/chat/completions?api-version=2026-08-01&API_KEY=TOP_SECRET&tenant=tenant-a&x-amz-signature=AWS_SECRET",
  );
  assert.equal(
    normalizeAiModelEndpointForStorage("https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a"),
    "https://api.example.com/v1?api-version=2026-08-01&tenant=tenant-a",
  );
});

test("OpenAI-compatible reasoning mode is explicit and fail-closed", () => {
  const base = { modelName: "glm-5.2", maxTokens: 4_096, temperature: 0.2 };
  const automatic = buildOpenAiChatRequestBody({ ...base, reasoningMode: "auto" }, { messages: [] });
  assert.equal(Object.hasOwn(automatic, "thinking"), false);
  assert.equal(automatic.max_tokens, 4_096);

  const disabled = buildOpenAiChatRequestBody({ ...base, reasoningMode: "disabled" }, { messages: [] });
  assert.deepEqual(disabled.thinking, { type: "disabled" });
  assert.equal(disabled.max_tokens, 4_096);
});

test("configured total tool budget also permits provider parallel calls in one round", () => {
  assert.deepEqual(resolveModelToolLoopLimits({ maxToolRounds: 12, maxTotalToolCalls: 24 }), {
    maxRounds: 12,
    maxCallsPerRound: 24,
    maxTotalCalls: 24,
  });
});

test("masked webhook never exposes route credentials", () => {
  const raw = "https://hooks.example.com/services/team/bot/a-very-secret-token?access_token=a-very-secret-token";
  const masked = maskWebhookUrl(raw);
  assert.match(masked, /^https:\/\/hooks\.example\.com\/•••\?…/);
  assert.doesNotMatch(masked, /services|a-very-secret-token/);
});

test("DingTalk signatures are verifiable and reject tampering", async () => {
  const timestamp = "1720000000000";
  const secret = "SECexample";
  const signature = await createDingTalkSignature(timestamp, secret);
  assert.equal(await verifyDingTalkSignature({ timestamp, signature, secret }), true);
  assert.equal(await verifyDingTalkSignature({ timestamp: `${timestamp}1`, signature, secret }), false);
  assert.equal(await verifyDingTalkSignature({ timestamp, signature: `${signature}x`, secret }), false);
});

test("Enterprise WeChat signatures are verifiable and reject tampering", async () => {
  const payload = { token: "callback-token", timestamp: "1720000000", nonce: "nonce", encrypt: "encrypted-payload" };
  const signature = await createWeComSignature(payload);
  assert.equal(await verifyWeComSignature({ ...payload, signature }), true);
  assert.equal(await verifyWeComSignature({ ...payload, signature: "0000000000000000000000000000000000000000" }), false);
});

test("legacy image model type is migrated to the canonical vision capability", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8"));
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status, last_test_result, last_tested_at)
    VALUES ('legacy-image', '旧图片模型', 'openai_compatible', 'image', 'vision-model', 'https://api.example.com/v1', 'encrypted', '1234', 'enabled', '连接成功：OK', '2026-07-26 03:16:47')`).run();
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status, last_test_result, last_tested_at)
    VALUES ('legacy-vision-test', '旧视觉测试', 'openai_compatible', 'vision', 'vision-model', 'https://api.example.com/v1', 'encrypted', '1234', 'enabled', '连接成功：OK', '2026-07-26 03:16:47')`).run();

  sqlite.exec(await readFile(new URL("../drizzle/0030_ai_vision_model_capability.sql", import.meta.url), "utf8"));

  const row = sqlite.prepare("SELECT model_type modelType, is_default_text_model isDefaultTextModel, last_test_result lastTestResult, last_tested_at lastTestedAt FROM ai_models WHERE id='legacy-image'").get() as {
    modelType: string;
    isDefaultTextModel: number;
    lastTestResult: string;
    lastTestedAt: string | null;
  };
  assert.equal(row.modelType, "vision");
  assert.equal(row.isDefaultTextModel, 0);
  assert.match(row.lastTestResult, /验证真实图片输入/);
  assert.equal(row.lastTestedAt, null);
  const legacyVision = sqlite.prepare("SELECT last_test_result lastTestResult, last_tested_at lastTestedAt FROM ai_models WHERE id='legacy-vision-test'").get() as { lastTestResult: string; lastTestedAt: string | null };
  assert.match(legacyVision.lastTestResult, /此前只验证了文本连接/);
  assert.equal(legacyVision.lastTestedAt, null);
  sqlite.close();
});

test("AI assistant routes, callbacks, knowledge, artifacts, UI, and migrations are wired", async () => {
  const [page, chatRoute, conversationsRoute, modelsRoute, channelsRoute, webhookRoute, artifactRoute, service, boundedFetch, entryContext, workflow, knowledge, artifacts, gateway, toolBudget, toolRuntime, toolAudit, authorization, visionModel, callbackMigration, visionMigration, pipelineMigration, reasoningMigration, executionMigration, knowledgeArtifactMigration, budgetMigration, guide, rolloutGuide] = await Promise.all([
    readFile(new URL("../app/ai-assistant-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/chat/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/conversations/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/models/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/channels/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/webhooks/[channelId]/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/artifacts/[artifactId]/route.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/assistant-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/bounded-fetch.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/entry-context.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/question-workflow.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/data-knowledge.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/artifacts.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/model-gateway.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/model-tool-budget.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-execution-runtime.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/tool-audit.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/auth/authorization.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/annotation-model.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0014_ai_channel_callbacks.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0030_ai_vision_model_capability.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0039_ai_question_pipeline.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0040_ai_model_reasoning_mode.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0041_ai_tool_execution_runtime.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0042_ai_knowledge_and_artifacts.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0043_ai_model_tool_budget_increase.sql", import.meta.url), "utf8"),
    readFile(new URL("../docs/AI_ASSISTANT_SETUP.md", import.meta.url), "utf8"),
    readFile(new URL("../docs/AI能力重构6-7层推进文档.md", import.meta.url), "utf8"),
  ]);

  assert.match(page, /新增模型配置/);
  assert.match(page, /视觉识别（读取图片）/);
  assert.match(page, /测试图片识别/);
  assert.doesNotMatch(page, /\{ value: "image"/);
  assert.match(page, /新增聊天渠道/);
  assert.match(page, /停止生成/);
  const workbench = await readFile(new URL("../app/ai-chat-workbench.tsx", import.meta.url), "utf8");
  assert.match(workbench, /本对话模型/);
  assert.match(workbench, /model\.modelType === "vision"/);
  assert.match(page, /onModel=\{id => void changeConversationModel\(id\)\}/);
  assert.match(page, /deleteConversation/);
  assert.match(page, /AiMessageArtifacts/);
  assert.match(page, /下载 CSV/);
  assert.match(page, /maxToolRounds/);
  assert.match(page, /AI_MODEL_TOOL_BUDGET_LIMITS\.maximumRounds/);
  assert.match(page, /reasoningMode/);
  assert.match(page, /关闭推理（端点须支持）/);
  assert.match(page, /modelBaseUrlDirty/);
  assert.match(page, /modelDraft\.id && !modelBaseUrlDirty/);
  assert.match(page, /webhookUrlMasked/);
  for (const route of [chatRoute, conversationsRoute, modelsRoute, channelsRoute, artifactRoute]) assert.match(route, /forwardAiRequest/);
  assert.match(webhookRoute, /requestDjangoAi/);
  const edgeGate = await readFile(new URL("../lib/ai/django-route.ts", import.meta.url), "utf8");
  assert.match(edgeGate, /requireAppPrincipal/);
  assert.match(edgeGate, /requireAiSameOriginWrite/);
  assert.match(edgeGate, /private, no-store/);
  assert.match(edgeGate, /request.signal/);
  assert.match(boundedFetch, /redirect: "manual"/);
  assert.match(boundedFetch, /response\.status >= 300 && response\.status < 400/);
  assert.match(boundedFetch, /readBoundedBody\(response, maxBytes\)/);
  assert.match(service, /callback_token_encrypted/);
  assert.match(service, /addMissingColumns\(db, "ai_channels"/);
  assert.match(service, /applyAiModelToolBudgetIncrease\(db\)/);
  assert.match(service, /probeVisionModelConnection\(model\)/);
  assert.match(service, /listConversationContextMessages/);
  assert.match(service, /ALTER TABLE \$\{table\} ADD COLUMN/);
  assert.match(service, /WHERE model_type = 'image'/);
  assert.match(service, /const testStillApplies = Boolean\(existing\)/);
  assert.match(visionModel, /VISION_PROBE_IMAGE_BASE64/);
  assert.match(visionModel, /未能识别测试图片/);
  assert.match(callbackMigration, /ai_channel_callback_events/);
  assert.match(visionMigration, /SET\s+`model_type` = 'vision'/);
  assert.match(entryContext, /principal: input\.principal/);
  assert.doesNotMatch(entryContext, /payload.*principal/i);
  assert.match(workflow, /RESET_COMMANDS/);
  assert.match(workflow, /getVisibleToolCatalog/);
  assert.doesNotMatch(workflow, /已有对话已固定模型/);
  assert.match(workflow, /selectConversationModel/);
  assert.match(gateway, /completeTextWithTools/);
  assert.match(gateway, /loadAiEndpointSecurityContext\(\)/);
  assert.ok(gateway.indexOf("resolveRuntimeModelEndpoint(input.model)") < gateway.indexOf("requireModelApiKey(input.model)"));
  assert.match(service, /normalizeAiModelInput\(input, endpointSecurityContext\)/);
  assert.match(visionModel, /resolveAiModelEndpointUrl\(model\.base_url, "openai_compatible", endpointSecurityContext\)/);
  assert.match(visionModel, /resolveAiModelEndpointUrl\(model\.base_url, "anthropic", endpointSecurityContext\)/);
  assert.match(gateway, /max_tokens: model\.maxTokens/);
  assert.match(gateway, /thinking: \{ type: "disabled" \}/);
  assert.match(gateway, /signal/);
  assert.match(toolBudget, /maximumRounds: 62/);
  assert.match(toolBudget, /maximumTotalCalls: 300/);
  assert.match(service, /DEFAULT_MODEL_TIMEOUT_MS = 60_000/);
  assert.match(service, /createRegisteredToolExecutionRuntime/);
  assert.match(service, /listAiArtifactsForConversation/);
  assert.match(service, /persistAiTableArtifacts/);
  assert.match(workflow, /retrieveKnowledgeForPrompt/);
  assert.match(workflow, /不是指令/);
  assert.match(knowledge, /deterministic_lexical/);
  assert.match(knowledge, /allowed_roles_json/);
  assert.match(artifacts, /rowsPerTable: 50/);
  assert.match(artifacts, /recordAiArtifactDelivery/);
  assert.match(artifacts, /\[=\+\\-@\]/);
  assert.match(toolRuntime, /maxCumulativeDurationMs/);
  assert.match(toolRuntime, /tool_timeout/);
  assert.match(toolRuntime, /crypto\.randomUUID/);
  assert.doesNotMatch(authorization, /ensureAiToolAuditExecutionIndex/);
  assert.doesNotMatch(authorization, /ALTER TABLE ai_tool_audit_logs ADD COLUMN/);
  assert.match(toolAudit, /supportsInvocationCorrelation/);
  assert.match(toolAudit, /request_id, actor_email, actor_role, surface, tool_name/);
  assert.doesNotMatch(page, /单轮请求超时|modelDraft\.timeoutMs/);
  assert.match(page, /maxTokens: 65536/);
  assert.match(pipelineMigration, /message_kind/);
  assert.match(pipelineMigration, /max_total_tool_calls/);
  assert.match(reasoningMigration, /reasoning_mode/);
  assert.match(reasoningMigration, /'auto', 'disabled'/);
  assert.match(executionMigration, /invocation_id/);
  assert.match(executionMigration, /provider_call_id/);
  assert.match(knowledgeArtifactMigration, /ai_knowledge_entries/);
  assert.match(knowledgeArtifactMigration, /ai_artifacts/);
  assert.match(knowledgeArtifactMigration, /ai_artifact_deliveries/);
  assert.match(knowledgeArtifactMigration, /request_id/);
  assert.match(budgetMigration, /max_tool_rounds[\s\S]*\+ 50/);
  assert.match(budgetMigration, /max_total_tool_calls[\s\S]*\+ 50/);
  assert.match(toolBudget, /ai-model-tool-budget-increase-2026-07-30/);
  assert.match(budgetMigration, /ai-model-tool-budget-increase-2026-07-30/);
  assert.match(guide, /AI_SECRET_ENCRYPTION_KEY/);
  assert.match(guide, /不应盲目继承上一模型的思考参数/);
  assert.match(guide, /模型派发审计记录失败类型及单轮耗时/);
  assert.match(guide, /仅文本请求成功不能证明模型支持主图识别/);
  assert.match(rolloutGuide, /数据与知识层/);
  assert.match(rolloutGuide, /产物与投递层/);
});

test("AI chat POST and UI carry a stable client request idempotency key", async () => {
  const [page, route, workflow, service, migration] = await Promise.all([
    readFile(new URL("../app/ai-assistant-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/chat/route.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/question-workflow.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/assistant-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0083_ai_chat_idempotency.sql", import.meta.url), "utf8"),
  ]);
  assert.match(page, /pendingChatRequestRef/);
  assert.match(page, /resolvePendingAiChatRequest\(pendingChatRequestRef\.current, requestPayload\)/);
  assert.match(page, /JSON\.stringify\(\{ \.\.\.pendingRequest\.requestPayload, clientRequestId \}\)/);
  assert.match(page, /attachPendingAiChatResponse\(pendingRequest/);
  assert.match(page, /markPendingAiChatSynchronized/);
  assert.match(page, /服务端同步尚未完整确认；原请求号已保留/);
  assert.match(route, /forwardAiRequest/);
  const domain = await readFile(new URL("../backend/ai_assistant/chat.py", import.meta.url), "utf8");
  assert.match(domain, /identifier\(body\["clientRequestId"\], "clientRequestId"\)/);
  assert.match(domain, /existing.request_digest != request_digest/);
  assert.match(domain, /ai_chat_result_unknown/);
  assert.match(workflow, /claimAiChatRequest/);
  assert.match(workflow, /markAiChatRequestDispatched/);
  assert.match(workflow, /markAiChatRequestUnknown/);
  assert.match(service, /status IN \('processing', 'dispatched', 'succeeded', 'failed', 'unknown'\)/);
  assert.match(service, /同一个 clientRequestId 已用于不同的聊天请求/);
  assert.match(migration, /ai_chat_request_receipts_owner_client_uq/);
});

test("AI tool execution migration preserves audit rows and adds invocation correlation", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0005_slow_tyrannus.sql", import.meta.url), "utf8"));
  sqlite.prepare(`INSERT INTO ai_tool_audit_logs (
    id, request_id, actor_email, actor_role, surface, tool_name, arguments_json, status, duration_ms
  ) VALUES ('audit-1', 'request-1', 'analyst@example.com', 'analyst', 'ai_chat', 'get_sales_summary', '{}', 'succeeded', 10)`).run();
  sqlite.exec(await readFile(new URL("../drizzle/0041_ai_tool_execution_runtime.sql", import.meta.url), "utf8"));
  const row = sqlite.prepare(`SELECT request_id requestId, invocation_id invocationId,
    provider_call_id providerCallId FROM ai_tool_audit_logs WHERE id='audit-1'`).get() as Record<string, string | null>;
  assert.deepEqual({ ...row }, { requestId: "request-1", invocationId: "", providerCallId: null });
  const indexes = sqlite.prepare(`SELECT name FROM sqlite_master
    WHERE type='index' AND name='ai_tool_audit_logs_invocation_created_idx'`).all();
  assert.equal(indexes.length, 1);
  sqlite.close();
});

test("AI question-pipeline and reasoning migrations upgrade the 0013 schema without rewriting existing records", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8"));
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status)
    VALUES ('text-1', '文本模型', 'openai_compatible', 'text', 'model-1', 'https://api.example.com/v1', 'encrypted', '1234', 'enabled')`).run();
  sqlite.prepare(`INSERT INTO ai_conversations (id, title, model_id, created_by)
    VALUES ('conversation-1', '旧对话', 'text-1', 'user@example.com')`).run();
  sqlite.prepare(`INSERT INTO ai_conversation_messages (id, conversation_id, role, content)
    VALUES ('message-1', 'conversation-1', 'user', '历史消息')`).run();

  sqlite.exec(await readFile(new URL("../drizzle/0039_ai_question_pipeline.sql", import.meta.url), "utf8"));
  sqlite.exec(await readFile(new URL("../drizzle/0040_ai_model_reasoning_mode.sql", import.meta.url), "utf8"));
  const model = sqlite.prepare(`SELECT timeout_ms timeoutMs, max_tokens maxTokens,
    reasoning_mode reasoningMode, temperature_milli temperatureMilli, max_tool_rounds maxToolRounds,
    max_total_tool_calls maxTotalToolCalls FROM ai_models WHERE id='text-1'`).get() as Record<string, number | string>;
  assert.deepEqual({ ...model }, {
    timeoutMs: 20_000,
    maxTokens: 1_024,
    reasoningMode: "auto",
    temperatureMilli: 200,
    maxToolRounds: 6,
    maxTotalToolCalls: 12,
  });
  const message = sqlite.prepare("SELECT message_kind messageKind, content FROM ai_conversation_messages WHERE id='message-1'").get() as { messageKind: string; content: string };
  assert.deepEqual({ ...message }, { messageKind: "message", content: "历史消息" });
  const indexes = sqlite.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='ai_conversation_messages_context_idx'").all();
  assert.equal(indexes.length, 1);
  sqlite.close();
});

test("AI model tool budget migration adds 50 once within the coordinated hard caps", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0013_ai_assistant.sql", import.meta.url), "utf8"));
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status)
    VALUES ('text-default', '默认预算', 'openai_compatible', 'text', 'model-1', 'https://api.example.com/v1', 'encrypted', '1234', 'enabled')`).run();
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status)
    VALUES ('text-high', '旧上限', 'openai_compatible', 'text', 'model-2', 'https://api.example.com/v1', 'encrypted', '1234', 'disabled')`).run();
  sqlite.prepare(`INSERT INTO ai_models
    (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix, status)
    VALUES ('vision-1', '视觉模型', 'openai_compatible', 'vision', 'vision-1', 'https://api.example.com/v1', 'encrypted', '1234', 'enabled')`).run();
  sqlite.exec(await readFile(new URL("../drizzle/0039_ai_question_pipeline.sql", import.meta.url), "utf8"));
  sqlite.prepare("UPDATE ai_models SET max_tool_rounds=12, max_total_tool_calls=24 WHERE id='text-high'").run();
  const migration = await readFile(new URL("../drizzle/0043_ai_model_tool_budget_increase.sql", import.meta.url), "utf8");
  sqlite.exec(migration);
  sqlite.exec(migration);
  const rows = sqlite.prepare(`SELECT id, max_tool_rounds maxToolRounds,
    max_total_tool_calls maxTotalToolCalls FROM ai_models ORDER BY id`).all() as Array<Record<string, number | string>>;
  assert.deepEqual(rows.map((row) => ({ ...row })), [
    { id: "text-default", maxToolRounds: 56, maxTotalToolCalls: 62 },
    { id: "text-high", maxToolRounds: 62, maxTotalToolCalls: 74 },
    { id: "vision-1", maxToolRounds: 6, maxTotalToolCalls: 12 },
  ]);
  const markers = sqlite.prepare(`SELECT key, updated_by updatedBy FROM ai_system_settings
    WHERE key='ai-model-tool-budget-increase-2026-07-30'`).all() as Array<Record<string, string>>;
  assert.deepEqual(markers.map((row) => ({ ...row })), [
    { key: "ai-model-tool-budget-increase-2026-07-30", updatedBy: "system_migration" },
  ]);
  sqlite.close();
});
