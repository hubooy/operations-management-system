import { randomUUID } from "node:crypto";

import {
  ensureAuthorizationSchema,
  type AppPrincipal,
} from "@/tests/legacy/auth/authorization";
import { decryptSecret, encryptSecret } from "@/lib/ai/crypto";
import { createDingTalkSignature } from "@/lib/ai/channel-callbacks";
import { isAiRequestCancelled } from "@/lib/ai/cancellation";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  aiConversationScopeAccessSql,
  ensureAiConversationScopeSchema,
  serializeAiConversationScope,
} from "@/tests/legacy/ai/conversation-scope";
import {
  isAiChatCapableModelType,
} from "@/tests/legacy/ai/conversation-management";
import {
  loadAiEndpointSecurityContext,
  maskWebhookUrl,
  normalizeAiEndpointUrl,
  normalizeAiModelEndpointForStorage,
  redactAiModelEndpointUrl,
  type AiEndpointSecurityContext,
} from "@/lib/ai/endpoint-security";
import {
  extractAiTableArtifactCandidates,
  AI_ARTIFACT_LIMITS,
  ensureAiArtifactSchema,
  listAiArtifactsForConversation,
  persistAiTableArtifacts,
  type AiTableArtifact,
  type AiTableArtifactCandidate,
} from "@/tests/legacy/ai/artifacts";
import { probeVisionModelConnection } from "@/lib/market/annotation-model";
import {
  createRegisteredToolExecutionRuntime,
  type AiToolExecutionContext,
} from "@/lib/ai/tool-registry";
import { recordAiToolAudit } from "@/tests/legacy/ai/tool-audit";
import { AI_TOOL_SYSTEM_PROMPT } from "@/lib/ai/tool-loop";
import {
  completeText,
  completeTextWithTools,
  type AiTextModelRuntimeConfig,
} from "@/lib/ai/model-gateway";
import {
  AI_MODEL_TOOL_BUDGET_LIMITS,
  AI_MODEL_TOOL_BUDGET_MIGRATION_KEY,
} from "@/lib/ai/model-tool-budget";
import type { AiPageContext } from "@/lib/ai/page-context";
import type { ProviderToolCallMetadata } from "@/lib/ai/tool-loop";
import { PublicApiError } from "@/lib/http/api-error";

export {
  runAnthropicToolLoop,
  runOpenAiCompatibleToolLoop,
  ToolLoopLimitError,
} from "@/lib/ai/tool-loop";
import { getD1Database, type D1Database } from "@/lib/database/d1";

export { maskWebhookUrl, normalizeAiEndpointUrl } from "@/lib/ai/endpoint-security";

export const aiModelProtocols = ["openai_compatible", "anthropic"] as const;
export type AiModelProtocol = (typeof aiModelProtocols)[number];
export const aiModelTypes = ["text", "vision"] as const;
export type AiModelType = (typeof aiModelTypes)[number];
export const aiModelStatuses = ["enabled", "disabled"] as const;
export type AiModelStatus = (typeof aiModelStatuses)[number];
export const aiModelReasoningModes = ["auto", "disabled"] as const;
export type AiModelReasoningMode = (typeof aiModelReasoningModes)[number];
export const aiChannelKinds = ["dingtalk_group_bot", "dingtalk_app", "wechat_work_group_bot", "wechat_work_app"] as const;
export type AiChannelKind = (typeof aiChannelKinds)[number];
export type AiChannelStatus = "enabled" | "disabled";

export type AiModelRecord = {
  id: string;
  version: number;
  name: string;
  protocol: AiModelProtocol;
  modelType: AiModelType;
  modelName: string;
  baseUrl: string;
  apiKeySuffix: string;
  isDefaultTextModel: boolean;
  status: AiModelStatus;
  timeoutMs: number;
  maxTokens: number;
  reasoningMode: AiModelReasoningMode;
  temperatureMilli: number;
  maxToolRounds: number;
  maxTotalToolCalls: number;
  lastTestResult: string | null;
  lastTestedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AiChannelRecord = {
  id: string;
  name: string;
  kind: AiChannelKind;
  status: AiChannelStatus;
  sendEnabled: boolean;
  callbackEnabled: boolean;
  /** Never expose the usable webhook URL to the browser. */
  webhookUrlMasked: string;
  callbackTokenMasked: string;
  aesKeyMasked: string;
  receiverId: string;
  lastTestResult: string | null;
  lastTestedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AiConversationRecord = {
  id: string;
  title: string;
  modelId: string | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
};

export type AiConversationMessage = {
  id: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  messageKind: "message" | "context_reset" | "help";
  createdAt: string;
  artifacts: AiTableArtifact[];
  contentBytes: number;
  contentTruncated: boolean;
};

export type AiConversationPage = {
  items: AiConversationRecord[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    returned: number;
    truncated: boolean;
    hasMore: boolean;
  };
};

export type AiConversationMessagePage = {
  items: AiConversationMessage[];
  pagination: {
    pageSize: number;
    total: number;
    returned: number;
    truncated: boolean;
    hasMore: boolean;
    nextBefore: number | null;
  };
  limits: {
    maximumPageSize: number;
    maximumMessageBytes: number;
    maximumPageContentBytes: number;
  };
};

export type AiAssistantReply = {
  reply: string;
  messageId: string;
  artifacts: AiTableArtifact[];
};

export type AiChatRequestReceiptResult = {
  conversationId: string;
  reply: string;
  modelId: string | null;
  outcome: "answered" | "context_reset" | "help";
  assistantMessageId: string;
};

export type AiChatRequestClaim =
  | { kind: "claimed"; receiptId: string; requestDigest: string }
  | { kind: "replayed"; receiptId: string; requestDigest: string; result: AiChatRequestReceiptResult };

export type AiAvailableChatModel = {
  id: string;
  name: string;
  protocol: AiModelProtocol;
  modelType: AiModelType;
  modelName: string;
  isDefault: boolean;
};

export type AiModelInput = {
  id?: string;
  /** Required for edits; omitted for newly generated model IDs. */
  expectedVersion?: number;
  name: string;
  protocol: AiModelProtocol;
  modelType: AiModelType;
  modelName: string;
  /** Required when creating; omit during an edit to preserve the stored runtime URL. */
  baseUrl?: string;
  apiKey?: string;
  status: AiModelStatus;
  isDefaultTextModel?: boolean;
  timeoutMs?: number;
  maxTokens?: number;
  reasoningMode?: AiModelReasoningMode;
  temperatureMilli?: number;
  maxToolRounds?: number;
  maxTotalToolCalls?: number;
};

export type AiChannelInput = {
  id?: string;
  name: string;
  kind: AiChannelKind;
  status: AiChannelStatus;
  sendEnabled: boolean;
  callbackEnabled: boolean;
  /** Required for a new channel; blank on an edit preserves the existing value. */
  webhookUrl?: string;
  /** DingTalk signing secret or the provider callback token. */
  callbackToken?: string;
  /** Enterprise WeChat EncodingAESKey when an inbound callback is enabled. */
  aesKey?: string;
  /** Enterprise WeChat CorpID / suite ID used to validate the decrypted envelope. */
  receiverId?: string;
};

export type AiChannelSecret = {
  id: string;
  kind: AiChannelKind;
  status: AiChannelStatus;
  sendEnabled: boolean;
  callbackEnabled: boolean;
  webhookUrl: string;
  callbackTokenEncrypted: string;
  callbackTokenSuffix: string;
  aesKeyEncrypted: string;
  aesKeySuffix: string;
  receiverId: string;
};

type AiModelRow = {
  id: string;
  version: number;
  name: string;
  protocol: string;
  model_type: string;
  model_name: string;
  base_url: string;
  api_key_encrypted: string;
  api_key_suffix: string;
  is_default_text_model: number;
  status: string;
  timeout_ms: number;
  max_tokens: number;
  reasoning_mode: string;
  temperature_milli: number;
  max_tool_rounds: number;
  max_total_tool_calls: number;
  last_test_result: string | null;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string;
};

type AiChannelRow = {
  id: string;
  name: string;
  kind: string;
  status: string;
  send_enabled: number;
  callback_enabled: number;
  webhook_url: string;
  callback_token_encrypted: string;
  callback_token_suffix: string;
  aes_key_encrypted: string;
  aes_key_suffix: string;
  receiver_id: string;
  last_test_result: string | null;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string;
};

type AiConversationRow = {
  id: string;
  title: string;
  model_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
};

type AiConversationMessageRow = {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  message_kind: string;
  created_at: string;
  message_rowid?: number;
  original_content_bytes?: number;
};

type AiChatRequestReceiptRow = {
  id: string;
  owner_email: string;
  client_request_id: string;
  request_digest: string;
  status: string;
  model_id: string | null;
  conversation_id: string | null;
  assistant_message_id: string | null;
  result_json: string | null;
  error_code: string | null;
  admitted_at: string | null;
  provider_started_at: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

type CountRow = { total: number };

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS ai_models (
    id TEXT PRIMARY KEY NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK (protocol IN ('openai_compatible', 'anthropic')),
    model_type TEXT NOT NULL CHECK (model_type IN ('text', 'image', 'vision')),
    model_name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    api_key_encrypted TEXT NOT NULL DEFAULT '',
    api_key_suffix TEXT NOT NULL DEFAULT '',
    is_default_text_model INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('enabled', 'disabled')),
    timeout_ms INTEGER NOT NULL DEFAULT 60000,
    max_tokens INTEGER NOT NULL DEFAULT 4096,
    reasoning_mode TEXT NOT NULL DEFAULT 'auto' CHECK (reasoning_mode IN ('auto', 'disabled')),
    temperature_milli INTEGER NOT NULL DEFAULT 200,
    max_tool_rounds INTEGER NOT NULL DEFAULT 6,
    max_total_tool_calls INTEGER NOT NULL DEFAULT 12,
    last_test_result TEXT,
    last_tested_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_models_default_text_uq
    ON ai_models (is_default_text_model)
    WHERE is_default_text_model = 1 AND status = 'enabled' AND model_type = 'text'`,
  `CREATE INDEX IF NOT EXISTS ai_models_status_idx
    ON ai_models (status, model_type, updated_at)`,
  `UPDATE ai_models
    SET model_type = 'vision',
        is_default_text_model = 0,
        last_test_result = '需重新测试：历史“图片”类型已升级为视觉识别，请验证真实图片输入',
        last_tested_at = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE model_type = 'image'`,
  `UPDATE ai_models
    SET last_test_result = '需重新测试：此前只验证了文本连接，请验证真实图片输入',
        last_tested_at = NULL,
        updated_at = CURRENT_TIMESTAMP
    WHERE model_type = 'vision'
      AND last_test_result LIKE '连接成功：OK%'`,
  `CREATE TABLE IF NOT EXISTS ai_channels (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('dingtalk_group_bot', 'dingtalk_app', 'wechat_work_group_bot', 'wechat_work_app')),
    status TEXT NOT NULL CHECK (status IN ('enabled', 'disabled')),
    send_enabled INTEGER NOT NULL DEFAULT 0,
    callback_enabled INTEGER NOT NULL DEFAULT 0,
    webhook_url TEXT NOT NULL DEFAULT '',
    callback_token_encrypted TEXT NOT NULL DEFAULT '',
    callback_token_suffix TEXT NOT NULL DEFAULT '',
    aes_key_encrypted TEXT NOT NULL DEFAULT '',
    aes_key_suffix TEXT NOT NULL DEFAULT '',
    receiver_id TEXT NOT NULL DEFAULT '',
    last_test_result TEXT,
    last_tested_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_channels_status_idx
    ON ai_channels (status, kind, updated_at)`,
  `CREATE TABLE IF NOT EXISTS ai_conversations (
    id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    model_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_conversations_creator_updated_idx
    ON ai_conversations (created_by, updated_at)`,
  `CREATE INDEX IF NOT EXISTS ai_conversations_updated_idx
    ON ai_conversations (updated_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_conversation_messages (
    id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    message_kind TEXT NOT NULL DEFAULT 'message',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_conversation_messages_conversation_idx
    ON ai_conversation_messages (conversation_id, created_at)`,
  `CREATE TABLE IF NOT EXISTS ai_chat_request_receipts (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'dispatched', 'succeeded', 'failed', 'unknown')),
    model_id TEXT,
    conversation_id TEXT,
    assistant_message_id TEXT,
    result_json TEXT,
    error_code TEXT,
    admitted_at TEXT,
    provider_started_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id) ON DELETE SET NULL
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_chat_request_receipts_owner_client_uq
    ON ai_chat_request_receipts (owner_email, client_request_id)`,
  `CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_status_updated_idx
    ON ai_chat_request_receipts (status, updated_at)`,
  `CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_conversation_idx
    ON ai_chat_request_receipts (conversation_id, created_at)`,
  `CREATE TABLE IF NOT EXISTS ai_chat_provider_dispatches (
    id TEXT PRIMARY KEY NOT NULL,
    receipt_id TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dispatch_ordinal INTEGER NOT NULL CHECK (dispatch_ordinal > 0),
    reserved_at TEXT NOT NULL,
    provider_called_at TEXT,
    FOREIGN KEY (receipt_id) REFERENCES ai_chat_request_receipts(id) ON DELETE CASCADE
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_chat_provider_dispatches_receipt_ordinal_uq
    ON ai_chat_provider_dispatches (receipt_id, dispatch_ordinal)`,
  `CREATE INDEX IF NOT EXISTS ai_chat_provider_dispatches_owner_reserved_idx
    ON ai_chat_provider_dispatches (owner_email, reserved_at)`,
  `CREATE INDEX IF NOT EXISTS ai_chat_provider_dispatches_model_reserved_idx
    ON ai_chat_provider_dispatches (model_id, reserved_at)`,
  `CREATE INDEX IF NOT EXISTS ai_chat_provider_dispatches_reserved_idx
    ON ai_chat_provider_dispatches (reserved_at)`,
  // Shared Chat + durable Agent dispatch budgets query this ledger. SQLite
  // permits the forward foreign-key declaration before the Agent schema is
  // materialized; Agent inserts only occur after its own schema ensure.
  `CREATE TABLE IF NOT EXISTS ai_agent_provider_dispatches (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE RESTRICT,
    dispatch_ordinal INTEGER NOT NULL CHECK (dispatch_ordinal BETWEEN 1 AND 20),
    owner_email TEXT NOT NULL COLLATE NOCASE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('analyst','operator','admin')),
    model_id TEXT NOT NULL,
    model_version INTEGER NOT NULL CHECK (model_version >= 1),
    tool_policy_digest TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'calling' CHECK (state IN ('calling','succeeded','failed','unknown')),
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider_called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', completed_at TEXT
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_agent_provider_dispatches_job_ordinal_uq
    ON ai_agent_provider_dispatches (job_id, dispatch_ordinal)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_provider_dispatches_owner_reserved_idx
    ON ai_agent_provider_dispatches (owner_email, reserved_at)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_provider_dispatches_model_reserved_idx
    ON ai_agent_provider_dispatches (model_id, reserved_at)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_provider_dispatches_state_reserved_idx
    ON ai_agent_provider_dispatches (state, reserved_at)`,
  `CREATE TABLE IF NOT EXISTS ai_conversation_deletion_audits (
    audit_id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL UNIQUE,
    conversation_owner TEXT NOT NULL,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('viewer', 'analyst', 'operator', 'admin')),
    reason TEXT NOT NULL,
    deleted_message_count INTEGER NOT NULL DEFAULT 0,
    deleted_artifact_count INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_conversation_deletion_audits_actor_deleted_idx
    ON ai_conversation_deletion_audits (actor_email, deleted_at)`,
  `CREATE TABLE IF NOT EXISTS ai_system_settings (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS ai_channel_callback_events (
    id TEXT PRIMARY KEY NOT NULL,
    channel_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, event_key)
  )`,
  `CREATE INDEX IF NOT EXISTS ai_channel_callback_events_received_idx
    ON ai_channel_callback_events (channel_id, received_at)`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();
const CHANNEL_REQUEST_TIMEOUT_MS = 15_000;
const CHANNEL_RESPONSE_LIMIT_BYTES = 64 * 1024;
const MAX_NAME_LENGTH = 100;
const MAX_MESSAGE_LENGTH = 40_000;
export const AI_CONVERSATION_PAGE_SIZE_MAX = 100;
export const AI_CONVERSATION_PAGE_MAX = 10_000;
export const AI_MESSAGE_PAGE_SIZE_DEFAULT = 30;
export const AI_MESSAGE_PAGE_SIZE_MAX = 100;
export const AI_MESSAGE_RESPONSE_BYTES_MAX = 24 * 1024;
export const AI_MESSAGE_PAGE_CONTENT_BYTES_MAX = 256 * 1024;
const AI_MESSAGE_QUERY_CHARACTER_MAX = Math.floor(AI_MESSAGE_RESPONSE_BYTES_MAX / 4);
const AI_CONVERSATION_DELETE_REASON_MAX = 200;
const DEFAULT_MODEL_TIMEOUT_MS = 60_000;
const DEFAULT_MODEL_MAX_TOKENS = 4_096;
const DEFAULT_MODEL_REASONING_MODE: AiModelReasoningMode = "auto";
const DEFAULT_MODEL_TEMPERATURE_MILLI = 200;
const DEFAULT_MODEL_MAX_TOOL_ROUNDS = AI_MODEL_TOOL_BUDGET_LIMITS.defaultRounds;
const DEFAULT_MODEL_MAX_TOTAL_TOOL_CALLS = AI_MODEL_TOOL_BUDGET_LIMITS.defaultTotalCalls;

/**
 * Admission limits bound top-level requests. Provider-dispatch limits are charged independently
 * before every model HTTP request, including every tool-loop round.
 */
export const AI_CHAT_DISPATCH_LIMITS = {
  maximumDailyRequestsPerOwner: 40,
  maximumDailyProviderDispatchesPerOwner: 120,
  maximumDailyProviderDispatchesGlobal: 1_000,
  maximumDailyProviderDispatchesPerModel: 500,
  maximumActivePerOwner: 2,
  maximumActiveGlobal: 24,
  maximumActivePerModel: 8,
  activeWindowMinutes: 240,
} as const;

const modelSelectColumns = `id, version, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix,
  is_default_text_model, status, timeout_ms, max_tokens, reasoning_mode, temperature_milli, max_tool_rounds, max_total_tool_calls,
  last_test_result, last_tested_at, created_at, updated_at`;

async function applyAiModelToolBudgetIncrease(db: D1Database): Promise<void> {
  await db.batch([
    db.prepare(`UPDATE ai_models
      SET max_tool_rounds = MIN(max_tool_rounds + ?, ?),
          max_total_tool_calls = MIN(max_total_tool_calls + ?, ?),
          version = version + 1,
          updated_at = CURRENT_TIMESTAMP
      WHERE model_type = 'text'
        AND NOT EXISTS (
          SELECT 1 FROM ai_system_settings WHERE key = ?
        )`).bind(
      AI_MODEL_TOOL_BUDGET_LIMITS.increaseBy,
      AI_MODEL_TOOL_BUDGET_LIMITS.maximumRounds,
      AI_MODEL_TOOL_BUDGET_LIMITS.increaseBy,
      AI_MODEL_TOOL_BUDGET_LIMITS.maximumTotalCalls,
      AI_MODEL_TOOL_BUDGET_MIGRATION_KEY,
    ),
    db.prepare(`INSERT INTO ai_system_settings (key, value_json, updated_by, updated_at)
      VALUES (?, ?, 'system_runtime', CURRENT_TIMESTAMP)
      ON CONFLICT(key) DO NOTHING`).bind(
      AI_MODEL_TOOL_BUDGET_MIGRATION_KEY,
      JSON.stringify(AI_MODEL_TOOL_BUDGET_LIMITS),
    ),
  ]);
}

export async function ensureAiAssistantSchema(db: D1Database = getD1Database()): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;

  const setup = ensureAuthorizationSchema(db)
    .then(() => db.batch(schemaStatements.map((statement) => db.prepare(statement))))
    .then(() => ensureAiConversationScopeSchema(db))
    .then(async () => {
      await addMissingColumns(db, "ai_channels", [["receiver_id", "TEXT NOT NULL DEFAULT ''"]]);
      await addMissingColumns(db, "ai_models", [
        ["version", "INTEGER NOT NULL DEFAULT 1"],
        ["timeout_ms", `INTEGER NOT NULL DEFAULT ${DEFAULT_MODEL_TIMEOUT_MS}`],
        ["max_tokens", `INTEGER NOT NULL DEFAULT ${DEFAULT_MODEL_MAX_TOKENS}`],
        ["reasoning_mode", `TEXT NOT NULL DEFAULT '${DEFAULT_MODEL_REASONING_MODE}'`],
        ["temperature_milli", `INTEGER NOT NULL DEFAULT ${DEFAULT_MODEL_TEMPERATURE_MILLI}`],
        ["max_tool_rounds", `INTEGER NOT NULL DEFAULT ${DEFAULT_MODEL_MAX_TOOL_ROUNDS}`],
        ["max_total_tool_calls", `INTEGER NOT NULL DEFAULT ${DEFAULT_MODEL_MAX_TOTAL_TOOL_CALLS}`],
      ]);
      await applyAiModelToolBudgetIncrease(db);
      await addMissingColumns(db, "ai_conversation_messages", [["message_kind", "TEXT NOT NULL DEFAULT 'message'"]]);
      // Runtime schema creation may have happened before migration 0083 was applied. Add the
      // receipt admission columns before creating indexes that depend on them so either upgrade
      // order remains valid.
      await addMissingColumns(db, "ai_chat_request_receipts", [
        ["model_id", "TEXT"],
        ["admitted_at", "TEXT"],
        ["provider_started_at", "TEXT"],
      ]);
      await db.batch([
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_provider_started_idx
          ON ai_chat_request_receipts (provider_started_at)`),
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_owner_dispatch_idx
          ON ai_chat_request_receipts (owner_email, provider_started_at)`),
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_model_dispatch_idx
          ON ai_chat_request_receipts (model_id, provider_started_at)`),
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_status_owner_model_idx
          ON ai_chat_request_receipts (status, owner_email, model_id, provider_started_at)`),
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_owner_admitted_idx
          ON ai_chat_request_receipts (owner_email, admitted_at)`),
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_status_admitted_idx
          ON ai_chat_request_receipts (status, admitted_at)`),
        db.prepare(`CREATE INDEX IF NOT EXISTS ai_chat_request_receipts_status_model_admitted_idx
          ON ai_chat_request_receipts (status, model_id, admitted_at)`),
      ]);
      await db.prepare(`CREATE INDEX IF NOT EXISTS ai_conversation_messages_context_idx
        ON ai_conversation_messages (conversation_id, message_kind, created_at)`).run();
    })
    .then(() => undefined)
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });

  schemaReadyByDatabase.set(key, setup);
  return setup;
}

export async function listAiModels(db: D1Database = getD1Database()): Promise<AiModelRecord[]> {
  await ensureAiAssistantSchema(db);
  const rows = await db.prepare(
    `SELECT ${modelSelectColumns}
     FROM ai_models
     ORDER BY is_default_text_model DESC, status DESC, updated_at DESC`,
  ).all<AiModelRow>();
  return (rows.results ?? []).map(mapAiModelRecord);
}

export async function listAvailableChatModels(db: D1Database = getD1Database()): Promise<AiAvailableChatModel[]> {
  await ensureAiAssistantSchema(db);
  const rows = await db.prepare(
    `SELECT ${modelSelectColumns}
     FROM ai_models
     WHERE model_type IN ('text', 'vision') AND status = 'enabled'
     ORDER BY is_default_text_model DESC, CASE model_type WHEN 'text' THEN 0 ELSE 1 END, updated_at DESC`,
  ).all<AiModelRow>();
  return (rows.results ?? []).map((row) => ({
    id: row.id,
    name: row.name,
    protocol: asModelProtocol(row.protocol),
    modelType: asModelType(row.model_type),
    modelName: row.model_name,
    isDefault: Boolean(row.is_default_text_model),
  }));
}

export async function upsertAiModel(input: AiModelInput, db: D1Database = getD1Database()): Promise<AiModelRecord> {
  await ensureAiAssistantSchema(db);
  const endpointSecurityContext = await loadAiEndpointSecurityContext();
  const normalized = normalizeAiModelInput(input, endpointSecurityContext);
  const id = normalized.id ?? `ai-model-${randomUUID()}`;
  const existing = normalized.id ? await getAiModelSecretById(id, db) : null;
  if (normalized.id && !existing) {
    throw new PublicApiError(409, "version_conflict", "模型配置已被删除或更新，请刷新后重试。");
  }
  if (existing && existing.version !== normalized.expectedVersion) {
    throw new PublicApiError(409, "version_conflict", "模型配置已被其他管理员更新，请刷新后重试。");
  }
  const configuredBaseUrl = normalized.baseUrl ?? existing?.base_url;
  if (!configuredBaseUrl) {
    throw new PublicApiError(400, "invalid_request", "模型地址不能为空");
  }
  // Revalidate retained legacy values as well as newly submitted addresses so
  // an unrelated profile edit cannot preserve a production-disallowed origin.
  const baseUrl = normalizeAiModelEndpointForStorage(configuredBaseUrl, endpointSecurityContext);
  if (existing && !normalized.apiKey) {
    const previousCredentialIdentity = `${existing.protocol}:${new URL(existing.base_url).origin}`;
    const nextCredentialIdentity = `${normalized.protocol}:${new URL(baseUrl).origin}`;
    if (previousCredentialIdentity !== nextCredentialIdentity) {
      throw new PublicApiError(
        400,
        "invalid_request",
        "更换模型协议或服务 origin 时必须同时填写该服务的新 API Key，不能把原密钥转发到新来源。",
      );
    }
  }
  const apiKeyEncrypted = normalized.apiKey ? await encryptSecret(normalized.apiKey) : existing?.api_key_encrypted ?? "";
  const apiKeySuffix = normalized.apiKey ? maskSuffix(normalized.apiKey) : existing?.api_key_suffix ?? "";
  const testStillApplies = Boolean(existing)
    && !normalized.apiKey
    && existing?.protocol === normalized.protocol
    && asModelType(existing?.model_type) === normalized.modelType
    && existing?.model_name === normalized.modelName
    && existing?.base_url === baseUrl
    && asModelReasoningMode(existing?.reasoning_mode) === normalized.reasoningMode;
  const lastTestResult = testStillApplies ? existing?.last_test_result ?? null : null;
  const lastTestedAt = testStillApplies ? existing?.last_tested_at ?? null : null;
  const values = [
    normalized.name,
    normalized.protocol,
    normalized.modelType,
    normalized.modelName,
    baseUrl,
    apiKeyEncrypted,
    apiKeySuffix,
    normalized.isDefaultTextModel ? 1 : 0,
    normalized.status,
    normalized.timeoutMs,
    normalized.maxTokens,
    normalized.reasoningMode,
    normalized.temperatureMilli,
    normalized.maxToolRounds,
    normalized.maxTotalToolCalls,
    lastTestResult,
    lastTestedAt,
  ] as const;
  const becomesDefault = normalized.isDefaultTextModel
    && normalized.modelType === "text"
    && normalized.status === "enabled";

  if (existing) {
    const expectedVersion = normalized.expectedVersion!;
    const updateStatement = db.prepare(
      `UPDATE ai_models SET
         name = ?, protocol = ?, model_type = ?, model_name = ?, base_url = ?,
         api_key_encrypted = ?, api_key_suffix = ?, is_default_text_model = ?, status = ?,
         timeout_ms = ?, max_tokens = ?, reasoning_mode = ?, temperature_milli = ?,
         max_tool_rounds = ?, max_total_tool_calls = ?, last_test_result = ?, last_tested_at = ?,
         version = version + 1, updated_at = CURRENT_TIMESTAMP
       WHERE id = ? AND version = ?`,
    ).bind(...values, id, expectedVersion);
    let updatedChanges: number;
    if (becomesDefault) {
      const results = await db.batch([
        db.prepare(`UPDATE ai_models
          SET is_default_text_model = 0, version = version + 1, updated_at = CURRENT_TIMESTAMP
          WHERE model_type = 'text' AND is_default_text_model = 1 AND id <> ?
            AND EXISTS (SELECT 1 FROM ai_models target WHERE target.id = ? AND target.version = ?)`)
          .bind(id, id, expectedVersion),
        updateStatement,
      ]);
      updatedChanges = Number(results[1]?.meta.changes ?? 0);
    } else {
      const result = await updateStatement.run();
      updatedChanges = Number(result.meta.changes ?? 0);
    }
    if (updatedChanges !== 1) {
      throw new PublicApiError(409, "version_conflict", "模型配置已被其他管理员更新，请刷新后重试。");
    }
  } else {
    const insertStatement = db.prepare(
      `INSERT INTO ai_models (id, name, protocol, model_type, model_name, base_url, api_key_encrypted, api_key_suffix,
         is_default_text_model, status, timeout_ms, max_tokens, reasoning_mode, temperature_milli, max_tool_rounds, max_total_tool_calls,
         last_test_result, last_tested_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
    ).bind(id, ...values);
    if (becomesDefault) {
      await db.batch([
        db.prepare(`UPDATE ai_models
          SET is_default_text_model = 0, version = version + 1, updated_at = CURRENT_TIMESTAMP
          WHERE model_type = 'text' AND is_default_text_model = 1`),
        insertStatement,
      ]);
    } else {
      await insertStatement.run();
    }
  }
  const row = await getAiModelSecretById(id, db);
  if (!row) throw new Error("模型配置保存后无法读取");
  return mapAiModelRecord(row);
}

export async function deleteAiModel(id: string, expectedVersion: number, db: D1Database = getD1Database()): Promise<boolean> {
  await ensureAiAssistantSchema(db);
  const version = requireAiModelExpectedVersion(expectedVersion);
  const result = await db.prepare("DELETE FROM ai_models WHERE id = ? AND version = ?").bind(id, version).run();
  if (Number(result.meta.changes ?? 0) !== 1) {
    throw new PublicApiError(409, "version_conflict", "模型配置已被删除或更新，请刷新后重试。");
  }
  return true;
}

export async function listAiChannels(db: D1Database = getD1Database()): Promise<AiChannelRecord[]> {
  await ensureAiAssistantSchema(db);
  const rows = await db.prepare(
    `SELECT id, name, kind, status, send_enabled, callback_enabled, webhook_url, callback_token_encrypted, callback_token_suffix,
            aes_key_encrypted, aes_key_suffix, receiver_id, last_test_result, last_tested_at, created_at, updated_at
     FROM ai_channels
     ORDER BY updated_at DESC`,
  ).all<AiChannelRow>();
  return (rows.results ?? []).map(mapAiChannelRecord);
}

export async function upsertAiChannel(input: AiChannelInput, db: D1Database = getD1Database()): Promise<AiChannelRecord> {
  await ensureAiAssistantSchema(db);
  const normalized = normalizeAiChannelInput(input);
  const id = normalized.id ?? `ai-channel-${randomUUID()}`;
  const existing = normalized.id ? await getAiChannelSecretById(id, db) : null;
  if (normalized.id && !existing) throw new Error("渠道配置不存在");
  if (normalized.callbackEnabled && !normalized.receiverId && !existing?.receiverId) {
    throw new PublicApiError(400, "invalid_request", "启用企业微信回调时必须填写接收方 ID。");
  }
  const webhookUrl = normalized.webhookUrl ? normalizeAiEndpointUrl(normalized.webhookUrl, "channel") : existing?.webhookUrl ?? "";
  const requiresWebhook = normalized.kind === "dingtalk_group_bot" || normalized.kind === "wechat_work_group_bot" || normalized.sendEnabled;
  if (!webhookUrl && requiresWebhook) throw new Error("启用发送或配置群机器人时必须填写 Webhook 地址");
  const callbackTokenEncrypted = normalized.callbackToken ? await encryptSecret(normalized.callbackToken) : existing?.callbackTokenEncrypted ?? "";
  const aesKeyEncrypted = normalized.aesKey ? await encryptSecret(normalized.aesKey) : existing?.aesKeyEncrypted ?? "";
  const callbackTokenSuffix = normalized.callbackToken ? maskSuffix(normalized.callbackToken) : existing?.callbackTokenSuffix ?? "";
  const aesKeySuffix = normalized.aesKey ? maskSuffix(normalized.aesKey) : existing?.aesKeySuffix ?? "";
  const receiverId = normalized.receiverId || existing?.receiverId || "";
  if (normalized.callbackEnabled && (!callbackTokenEncrypted || !aesKeyEncrypted || !receiverId)) {
    throw new PublicApiError(400, "invalid_request", "启用企业微信回调时必须配置 Token、EncodingAESKey 和接收方 ID。");
  }
  await db.prepare(
    `INSERT INTO ai_channels (id, name, kind, status, send_enabled, callback_enabled, webhook_url, callback_token_encrypted, callback_token_suffix, aes_key_encrypted, aes_key_suffix, receiver_id, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
     ON CONFLICT(id) DO UPDATE SET
       name = excluded.name,
       kind = excluded.kind,
       status = excluded.status,
       send_enabled = excluded.send_enabled,
       callback_enabled = excluded.callback_enabled,
       webhook_url = excluded.webhook_url,
       callback_token_encrypted = excluded.callback_token_encrypted,
       callback_token_suffix = excluded.callback_token_suffix,
       aes_key_encrypted = excluded.aes_key_encrypted,
       aes_key_suffix = excluded.aes_key_suffix,
       receiver_id = excluded.receiver_id,
       updated_at = CURRENT_TIMESTAMP`,
  ).bind(id, normalized.name, normalized.kind, normalized.status, normalized.sendEnabled ? 1 : 0, normalized.callbackEnabled ? 1 : 0, webhookUrl, callbackTokenEncrypted, callbackTokenSuffix, aesKeyEncrypted, aesKeySuffix, receiverId).run();
  const row = await getAiChannelRowById(id, db);
  if (!row) throw new Error("渠道配置保存后无法读取");
  return mapAiChannelRecord(row);
}

export async function deleteAiChannel(id: string, db: D1Database = getD1Database()): Promise<boolean> {
  await ensureAiAssistantSchema(db);
  const result = await db.prepare("DELETE FROM ai_channels WHERE id = ?").bind(id).run();
  return Number(result.meta.changes ?? 0) > 0;
}

export async function getAiChannelSecretById(id: string, db: D1Database = getD1Database()): Promise<AiChannelSecret | null> {
  const row = await getAiChannelRowById(id, db);
  if (!row) return null;
  return {
    id: row.id,
    kind: asChannelKind(row.kind),
    status: asChannelStatus(row.status),
    sendEnabled: Boolean(row.send_enabled),
    callbackEnabled: Boolean(row.callback_enabled),
    webhookUrl: row.webhook_url,
    callbackTokenEncrypted: row.callback_token_encrypted,
    callbackTokenSuffix: row.callback_token_suffix,
    aesKeyEncrypted: row.aes_key_encrypted,
    aesKeySuffix: row.aes_key_suffix,
    receiverId: row.receiver_id,
  };
}

export async function listAiConversations(
  principal: AppPrincipal,
  inputOrDb: { page?: number; pageSize?: number } | D1Database = {},
  database: D1Database = getD1Database(),
): Promise<AiConversationPage> {
  const input = isD1Database(inputOrDb) ? {} : inputOrDb;
  const db = isD1Database(inputOrDb) ? inputOrDb : database;
  await ensureAiAssistantSchema(db);
  const page = requireBoundedPositiveInteger(input.page, 1, AI_CONVERSATION_PAGE_MAX, "page");
  const pageSize = requireBoundedPositiveInteger(input.pageSize, 30, AI_CONVERSATION_PAGE_SIZE_MAX, "pageSize");
  const offset = (page - 1) * pageSize;
  const ownerFilter = principal.role === "admin" ? "" : " AND c.created_by = ?";
  const ownerBindings = principal.role === "admin" ? [] : [principal.email];
  const scopeAccess = aiConversationScopeAccessSql(principal.scope);
  const [countResult, rows] = await Promise.all([
    db.prepare(`SELECT COUNT(*) total
      FROM ai_conversations c
      ${scopeAccess.join}
      WHERE 1 = 1${ownerFilter}${scopeAccess.clause}`)
      .bind(...ownerBindings, ...scopeAccess.values).first<CountRow>(),
    db.prepare(`SELECT c.id, c.title, c.model_id, c.created_by, c.created_at, c.updated_at
      FROM ai_conversations c
      ${scopeAccess.join}
      WHERE 1 = 1${ownerFilter}${scopeAccess.clause}
      ORDER BY c.updated_at DESC, c.id DESC
      LIMIT ? OFFSET ?`)
      .bind(...ownerBindings, ...scopeAccess.values, pageSize, offset).all<AiConversationRow>(),
  ]);
  const items = (rows.results ?? []).map(mapConversationRecord);
  const total = Number(countResult?.total ?? 0);
  return {
    items,
    pagination: {
      page,
      pageSize,
      total,
      returned: items.length,
      truncated: total > items.length,
      hasMore: offset + items.length < total,
    },
  };
}

export async function createConversation(
  title: string,
  principal: Pick<AppPrincipal, "email" | "scope">,
  modelId: string | null,
  db: D1Database = getD1Database(),
): Promise<string> {
  await ensureAiAssistantSchema(db);
  const id = `ai-conv-${randomUUID()}`;
  const results = await db.batch([
    db.prepare("INSERT INTO ai_conversations (id, title, model_id, created_by) VALUES (?, ?, ?, ?)")
      .bind(id, normalizeText(title, "新对话", 120), modelId, principal.email),
    db.prepare(`INSERT INTO ai_conversation_scopes (conversation_id, scope_json)
      VALUES ((SELECT id FROM ai_conversations WHERE id = ? AND created_by = ?), ?)`)
      .bind(id, principal.email, serializeAiConversationScope(principal.scope)),
  ]);
  if (Number(results[0]?.meta.changes ?? 0) !== 1 || Number(results[1]?.meta.changes ?? 0) !== 1) {
    throw new Error("AI conversation scope snapshot could not be persisted");
  }
  return id;
}

export async function appendConversationMessage(
  conversationId: string,
  role: "user" | "assistant",
  content: string,
  kindOrDb: AiConversationMessage["messageKind"] | D1Database = "message",
  database: D1Database = getD1Database(),
): Promise<string> {
  const messageKind = typeof kindOrDb === "string" ? kindOrDb : "message";
  const db = typeof kindOrDb === "string" ? database : kindOrDb;
  await ensureAiAssistantSchema(db);
  const normalizedContent = normalizeMessageContent(content, MAX_MESSAGE_LENGTH);
  if (!normalizedContent) throw new Error("消息不能为空");
  const id = `ai-msg-${randomUUID()}`;
  await appendConversationMessageWithId({ id, conversationId, role, content: normalizedContent, messageKind }, db);
  return id;
}

async function appendConversationMessageWithId(input: {
  id: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  messageKind: AiConversationMessage["messageKind"];
}, db: D1Database): Promise<void> {
  const results = await db.batch([
    db.prepare(`INSERT INTO ai_conversation_messages (id, conversation_id, role, content, message_kind)
      SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM ai_conversations WHERE id = ?)`)
      .bind(input.id, input.conversationId, input.role, input.content, input.messageKind, input.conversationId),
    db.prepare("UPDATE ai_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?").bind(input.conversationId),
  ]);
  if (Number(results[0]?.meta.changes ?? 0) === 0) throw new Error("对话不存在或已删除");
}

export async function digestAiChatRequestPayload(input: {
  conversationId: string | null;
  modelId: string | null;
  message: string;
  title: string | null;
  pageContext?: AiPageContext | null;
}): Promise<string> {
  const canonicalParts: unknown[] = [
    input.conversationId,
    input.modelId,
    input.message,
    input.title,
  ];
  // Preserve historical no-context digests so in-flight response-loss replays
  // from an older build remain recoverable after this optional field ships.
  if (input.pageContext) canonicalParts.push(input.pageContext);
  const canonical = JSON.stringify(canonicalParts);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function claimAiChatRequest(input: {
  clientRequestId: string;
  requestDigest: string;
  principal: Pick<AppPrincipal, "email">;
}, db: D1Database = getD1Database()): Promise<AiChatRequestClaim> {
  await ensureAiAssistantSchema(db);
  const clientRequestId = requireAiEntityId(input.clientRequestId, "clientRequestId");
  const requestDigest = input.requestDigest.trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(requestDigest)) {
    throw new PublicApiError(400, "invalid_request", "聊天请求摘要格式无效。");
  }
  const receiptId = `ai-chat-receipt-${randomUUID()}`;
  const inserted = await db.prepare(`INSERT OR IGNORE INTO ai_chat_request_receipts (
      id, owner_email, client_request_id, request_digest, status
    ) VALUES (?, ?, ?, ?, 'processing')`)
    .bind(receiptId, input.principal.email, clientRequestId, requestDigest).run();
  if (Number(inserted.meta.changes ?? 0) === 1) {
    return { kind: "claimed", receiptId, requestDigest };
  }

  const existing = await db.prepare(`SELECT id, owner_email, client_request_id, request_digest, status, model_id,
      conversation_id, assistant_message_id, result_json, error_code, provider_started_at,
      admitted_at,
      created_at, updated_at, completed_at
    FROM ai_chat_request_receipts
    WHERE owner_email = ? AND client_request_id = ? LIMIT 1`)
    .bind(input.principal.email, clientRequestId).first<AiChatRequestReceiptRow>();
  if (!existing) {
    throw new PublicApiError(409, "conflict", "聊天请求回执冲突，请刷新后重试。");
  }
  if (existing.request_digest !== requestDigest) {
    throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的聊天请求。");
  }
  if (existing.status === "succeeded") {
    return {
      kind: "replayed",
      receiptId: existing.id,
      requestDigest,
      result: parseAiChatReceiptResult(existing),
    };
  }
  if (existing.status === "processing") {
    throw new PublicApiError(409, "conflict", "该消息正在处理，已阻止重复提交，请稍后刷新对话记录。");
  }
  if (existing.status === "dispatched" || existing.status === "unknown") {
    throw new PublicApiError(409, "ai_chat_result_unknown", "该消息可能已发送给模型，但结果尚未确认。为避免重复计费，已阻止再次提交，请刷新对话记录。");
  }
  throw new PublicApiError(409, "ai_chat_not_dispatched", "该消息此前已确认未发送给模型。请重新发送，系统将使用新的请求号。");
}

export async function markAiChatRequestDispatched(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  conversationId: string | null;
  modelId: string;
  now?: Date;
}, db: D1Database = getD1Database()): Promise<void> {
  await ensureAiAssistantSchema(db);
  const modelId = requireAiEntityId(input.modelId, "modelId");
  const now = input.now ?? new Date();
  const { dayStart, dayEnd, activeCutoff, dispatchedAt } = aiChatDispatchTimeBounds(now);
  const admitted = await db.prepare(`UPDATE ai_chat_request_receipts
    SET status = 'dispatched', conversation_id = ?, model_id = ?, admitted_at = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ? AND owner_email = ? AND request_digest = ? AND status = 'processing'
      AND (SELECT COUNT(*) FROM ai_chat_request_receipts daily
        WHERE daily.owner_email = ? AND daily.admitted_at >= ? AND daily.admitted_at < ?) < ?
      AND (SELECT COUNT(*) FROM ai_chat_request_receipts active
        WHERE active.status = 'dispatched' AND active.owner_email = ?
          AND active.admitted_at >= ? AND active.admitted_at <= ?) < ?
      AND (SELECT COUNT(*) FROM ai_chat_request_receipts active
        WHERE active.status = 'dispatched'
          AND active.admitted_at >= ? AND active.admitted_at <= ?) < ?
      AND (SELECT COUNT(*) FROM ai_chat_request_receipts active
        WHERE active.status = 'dispatched' AND active.model_id = ?
          AND active.admitted_at >= ? AND active.admitted_at <= ?) < ?`)
    .bind(
      input.conversationId,
      modelId,
      dispatchedAt,
      input.receiptId,
      input.ownerEmail,
      input.requestDigest,
      input.ownerEmail,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyRequestsPerOwner,
      input.ownerEmail,
      activeCutoff,
      dispatchedAt,
      AI_CHAT_DISPATCH_LIMITS.maximumActivePerOwner,
      activeCutoff,
      dispatchedAt,
      AI_CHAT_DISPATCH_LIMITS.maximumActiveGlobal,
      modelId,
      activeCutoff,
      dispatchedAt,
      AI_CHAT_DISPATCH_LIMITS.maximumActivePerModel,
    ).run();
  if (Number(admitted.meta.changes ?? 0) !== 1) {
    const current = await db.prepare(`SELECT status FROM ai_chat_request_receipts
      WHERE id = ? AND owner_email = ? AND request_digest = ? LIMIT 1`)
      .bind(input.receiptId, input.ownerEmail, input.requestDigest).first<{ status: string }>();
    if (!current || current.status !== "processing") {
      throw new PublicApiError(409, "conflict", "聊天请求派发权已失效，未调用模型。");
    }
    const counts = await readAiChatDispatchCounts({
      ownerEmail: input.ownerEmail,
      modelId,
      dayStart,
      dayEnd,
      activeCutoff,
      dispatchedAt,
    }, db);
    if (counts.ownerRequestsDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyRequestsPerOwner) {
      throw new PublicApiError(429, "rate_limited", "今日 AI 对话请求额度已满，未调用模型，请于下一个上海自然日再试。");
    }
    if (
      counts.ownerActive >= AI_CHAT_DISPATCH_LIMITS.maximumActivePerOwner
      || counts.globalActive >= AI_CHAT_DISPATCH_LIMITS.maximumActiveGlobal
      || counts.modelActive >= AI_CHAT_DISPATCH_LIMITS.maximumActivePerModel
    ) {
      throw new PublicApiError(409, "conflict", "AI 对话并发请求已达上限，未调用模型，请等待当前请求完成。");
    }
    throw new PublicApiError(409, "conflict", "聊天请求派发权已失效，未调用模型。");
  }
}

/**
 * Pre-reserve the first provider turn before persisting a new chat. The reservation is refunded
 * only when failAiChatRequestBeforeDispatch proves that no provider request was attempted.
 */
export async function reserveAiChatInitialProviderDispatch(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  modelId: string;
  now?: Date;
}, db: D1Database = getD1Database()): Promise<void> {
  await ensureAiAssistantSchema(db);
  const modelId = requireAiEntityId(input.modelId, "modelId");
  const now = input.now ?? new Date();
  const { dayStart, dayEnd, activeCutoff, dispatchedAt } = aiChatDispatchTimeBounds(now);
  const dispatchId = `ai-chat-dispatch-${randomUUID()}`;
  const reserved = await db.prepare(`INSERT OR IGNORE INTO ai_chat_provider_dispatches (
      id, receipt_id, owner_email, model_id, dispatch_ordinal, reserved_at, provider_called_at
    )
    SELECT ?, r.id, r.owner_email, ?, 1, ?, NULL
    FROM ai_chat_request_receipts r
    WHERE r.id = ? AND r.owner_email = ? AND r.request_digest = ?
      AND r.status = 'dispatched' AND r.model_id = ?
      AND ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.owner_email = ? AND d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.owner_email = ? AND d.reserved_at >= ? AND d.reserved_at < ?)) < ?
      AND ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.reserved_at >= ? AND d.reserved_at < ?)) < ?
      AND ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.model_id = ? AND d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.model_id = ? AND d.reserved_at >= ? AND d.reserved_at < ?)) < ?`)
    .bind(
      dispatchId,
      modelId,
      dispatchedAt,
      input.receiptId,
      input.ownerEmail,
      input.requestDigest,
      modelId,
      input.ownerEmail,
      dayStart,
      dayEnd,
      input.ownerEmail,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner,
      dayStart,
      dayEnd,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesGlobal,
      modelId,
      dayStart,
      dayEnd,
      modelId,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerModel,
    ).run();
  if (Number(reserved.meta.changes ?? 0) === 1) return;
  const current = await db.prepare(`SELECT r.status,
      EXISTS (SELECT 1 FROM ai_chat_provider_dispatches d
        WHERE d.receipt_id = r.id AND d.dispatch_ordinal = 1) ordinal_exists
    FROM ai_chat_request_receipts r
    WHERE r.id = ? AND r.owner_email = ? AND r.request_digest = ? AND r.model_id = ?
    LIMIT 1`)
    .bind(input.receiptId, input.ownerEmail, input.requestDigest, modelId)
    .first<{ status: string; ordinal_exists: number }>();
  if (!current || current.status !== "dispatched" || Boolean(current.ordinal_exists)) {
    throw new PublicApiError(409, "conflict", "首轮模型派发凭证已失效，未调用模型。");
  }
  const counts = await readAiChatDispatchCounts({
    ownerEmail: input.ownerEmail,
    modelId,
    dayStart,
    dayEnd,
    activeCutoff,
    dispatchedAt,
  }, db);
  if (
    counts.ownerProviderDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner
    || counts.globalProviderDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesGlobal
    || counts.modelProviderDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerModel
  ) {
    throw new PublicApiError(429, "rate_limited", "今日模型派发额度已满，本轮未调用模型，请于下一个上海自然日再试。");
  }
  throw new PublicApiError(409, "conflict", "首轮模型派发凭证已失效，未调用模型。");
}

export async function attachAiChatRequestConversation(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  conversationId: string;
}, db: D1Database = getD1Database()): Promise<void> {
  const conversationId = requireAiEntityId(input.conversationId, "conversationId");
  const attached = await db.prepare(`UPDATE ai_chat_request_receipts
    SET conversation_id = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ? AND owner_email = ? AND request_digest = ? AND status = 'dispatched'
      AND EXISTS (SELECT 1 FROM ai_conversations c WHERE c.id = ?)`)
    .bind(conversationId, input.receiptId, input.ownerEmail, input.requestDigest, conversationId).run();
  if (Number(attached.meta.changes ?? 0) !== 1) {
    throw new PublicApiError(409, "conflict", "聊天请求与对话绑定失败，未调用模型。");
  }
}

type AiChatDispatchCounts = {
  ownerRequestsDaily: number;
  ownerProviderDaily: number;
  globalProviderDaily: number;
  modelProviderDaily: number;
  ownerActive: number;
  globalActive: number;
  modelActive: number;
};

function aiChatDispatchTimeBounds(now: Date): {
  dayStart: string;
  dayEnd: string;
  activeCutoff: string;
  dispatchedAt: string;
} {
  if (!Number.isFinite(now.getTime())) throw new Error("AI chat dispatch time is invalid");
  const shanghaiOffsetMs = 8 * 60 * 60 * 1_000;
  const shifted = new Date(now.getTime() + shanghaiOffsetMs);
  const dayStartMs = Date.UTC(shifted.getUTCFullYear(), shifted.getUTCMonth(), shifted.getUTCDate()) - shanghaiOffsetMs;
  return {
    dayStart: sqliteUtcTimestamp(new Date(dayStartMs)),
    dayEnd: sqliteUtcTimestamp(new Date(dayStartMs + 24 * 60 * 60 * 1_000)),
    activeCutoff: sqliteUtcTimestamp(new Date(now.getTime() - AI_CHAT_DISPATCH_LIMITS.activeWindowMinutes * 60 * 1_000)),
    dispatchedAt: sqliteUtcTimestamp(now),
  };
}

function sqliteUtcTimestamp(value: Date): string {
  return value.toISOString().slice(0, 19).replace("T", " ");
}

async function readAiChatDispatchCounts(input: {
  ownerEmail: string;
  modelId: string;
  dayStart: string;
  dayEnd: string;
  activeCutoff: string;
  dispatchedAt: string;
}, db: D1Database): Promise<AiChatDispatchCounts> {
  const row = await db.prepare(`SELECT
      (SELECT COUNT(*) FROM ai_chat_request_receipts admitted
        WHERE admitted.owner_email = ?
          AND admitted.admitted_at >= ? AND admitted.admitted_at < ?) owner_requests_daily,
      ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.owner_email = ? AND d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.owner_email = ? AND d.reserved_at >= ? AND d.reserved_at < ?)) owner_provider_daily,
      ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.reserved_at >= ? AND d.reserved_at < ?)) global_provider_daily,
      ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.model_id = ? AND d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.model_id = ? AND d.reserved_at >= ? AND d.reserved_at < ?)) model_provider_daily,
      (SELECT COUNT(*) FROM ai_chat_request_receipts active
        WHERE active.status = 'dispatched' AND active.owner_email = ?
          AND active.admitted_at >= ? AND active.admitted_at <= ?) owner_active,
      (SELECT COUNT(*) FROM ai_chat_request_receipts active
        WHERE active.status = 'dispatched'
          AND active.admitted_at >= ? AND active.admitted_at <= ?) global_active,
      (SELECT COUNT(*) FROM ai_chat_request_receipts active
        WHERE active.status = 'dispatched' AND active.model_id = ?
          AND active.admitted_at >= ? AND active.admitted_at <= ?) model_active`)
    .bind(
      input.ownerEmail,
      input.dayStart,
      input.dayEnd,
      input.ownerEmail,
      input.dayStart,
      input.dayEnd,
      input.ownerEmail,
      input.dayStart,
      input.dayEnd,
      input.dayStart,
      input.dayEnd,
      input.dayStart,
      input.dayEnd,
      input.modelId,
      input.dayStart,
      input.dayEnd,
      input.modelId,
      input.dayStart,
      input.dayEnd,
      input.ownerEmail,
      input.activeCutoff,
      input.dispatchedAt,
      input.activeCutoff,
      input.dispatchedAt,
      input.modelId,
      input.activeCutoff,
      input.dispatchedAt,
    ).first<{
      owner_requests_daily: number;
      owner_provider_daily: number;
      global_provider_daily: number;
      model_provider_daily: number;
      owner_active: number;
      global_active: number;
      model_active: number;
    }>();
  return {
    ownerRequestsDaily: Number(row?.owner_requests_daily ?? 0),
    ownerProviderDaily: Number(row?.owner_provider_daily ?? 0),
    globalProviderDaily: Number(row?.global_provider_daily ?? 0),
    modelProviderDaily: Number(row?.model_provider_daily ?? 0),
    ownerActive: Number(row?.owner_active ?? 0),
    globalActive: Number(row?.global_active ?? 0),
    modelActive: Number(row?.model_active ?? 0),
  };
}

export async function reserveAiChatProviderDispatch(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  modelId: string;
  ordinal: number;
  now?: Date;
}, db: D1Database = getD1Database()): Promise<void> {
  await ensureAiAssistantSchema(db);
  const modelId = requireAiEntityId(input.modelId, "modelId");
  if (!Number.isSafeInteger(input.ordinal) || input.ordinal < 1 || input.ordinal > AI_MODEL_TOOL_BUDGET_LIMITS.maximumRounds) {
    throw new PublicApiError(409, "conflict", "模型派发轮次超出安全上限，未调用模型。");
  }
  const now = input.now ?? new Date();
  const { dayStart, dayEnd, activeCutoff, dispatchedAt } = aiChatDispatchTimeBounds(now);
  if (input.ordinal === 1) {
    const [activated, receiptStarted] = await db.batch([
      db.prepare(`UPDATE ai_chat_provider_dispatches
        SET provider_called_at = ?
        WHERE receipt_id = ? AND owner_email = ? AND model_id = ? AND dispatch_ordinal = 1
          AND provider_called_at IS NULL
          AND EXISTS (SELECT 1 FROM ai_chat_request_receipts r
            WHERE r.id = ai_chat_provider_dispatches.receipt_id AND r.request_digest = ?
              AND r.status = 'dispatched' AND r.model_id = ?)`)
        .bind(dispatchedAt, input.receiptId, input.ownerEmail, modelId, input.requestDigest, modelId),
      db.prepare(`UPDATE ai_chat_request_receipts
        SET provider_started_at = COALESCE(provider_started_at, ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND owner_email = ? AND request_digest = ? AND status = 'dispatched'
          AND EXISTS (SELECT 1 FROM ai_chat_provider_dispatches d
            WHERE d.receipt_id = ai_chat_request_receipts.id AND d.dispatch_ordinal = 1
              AND d.provider_called_at = ?)`)
        .bind(dispatchedAt, input.receiptId, input.ownerEmail, input.requestDigest, dispatchedAt),
    ]);
    if (Number(activated?.meta.changes ?? 0) !== 1 || Number(receiptStarted?.meta.changes ?? 0) !== 1) {
      throw new PublicApiError(409, "conflict", "首轮模型派发凭证已失效，未调用模型。");
    }
    return;
  }

  const dispatchId = `ai-chat-dispatch-${randomUUID()}`;
  const reserved = await db.prepare(`INSERT OR IGNORE INTO ai_chat_provider_dispatches (
      id, receipt_id, owner_email, model_id, dispatch_ordinal, reserved_at, provider_called_at
    )
    SELECT ?, r.id, r.owner_email, ?, ?, ?, ?
    FROM ai_chat_request_receipts r
    WHERE r.id = ? AND r.owner_email = ? AND r.request_digest = ?
      AND r.status = 'dispatched' AND r.model_id = ?
      AND EXISTS (SELECT 1 FROM ai_chat_provider_dispatches previous
        WHERE previous.receipt_id = r.id AND previous.dispatch_ordinal = ?
          AND previous.provider_called_at IS NOT NULL)
      AND ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.owner_email = ? AND d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.owner_email = ? AND d.reserved_at >= ? AND d.reserved_at < ?)) < ?
      AND ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.reserved_at >= ? AND d.reserved_at < ?)) < ?
      AND ((SELECT COUNT(*) FROM ai_chat_provider_dispatches d
          WHERE d.model_id = ? AND d.reserved_at >= ? AND d.reserved_at < ?)
        + (SELECT COUNT(*) FROM ai_agent_provider_dispatches d
          WHERE d.model_id = ? AND d.reserved_at >= ? AND d.reserved_at < ?)) < ?`)
    .bind(
      dispatchId,
      modelId,
      input.ordinal,
      dispatchedAt,
      dispatchedAt,
      input.receiptId,
      input.ownerEmail,
      input.requestDigest,
      modelId,
      input.ordinal - 1,
      input.ownerEmail,
      dayStart,
      dayEnd,
      input.ownerEmail,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner,
      dayStart,
      dayEnd,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesGlobal,
      modelId,
      dayStart,
      dayEnd,
      modelId,
      dayStart,
      dayEnd,
      AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerModel,
    ).run();
  if (Number(reserved.meta.changes ?? 0) === 1) return;
  const current = await db.prepare(`SELECT r.status,
      EXISTS (SELECT 1 FROM ai_chat_provider_dispatches d
        WHERE d.receipt_id = r.id AND d.dispatch_ordinal = ?) ordinal_exists,
      EXISTS (SELECT 1 FROM ai_chat_provider_dispatches previous
        WHERE previous.receipt_id = r.id AND previous.dispatch_ordinal = ?
          AND previous.provider_called_at IS NOT NULL) previous_called
    FROM ai_chat_request_receipts r
    WHERE r.id = ? AND r.owner_email = ? AND r.request_digest = ? AND r.model_id = ?
    LIMIT 1`)
    .bind(input.ordinal, input.ordinal - 1, input.receiptId, input.ownerEmail, input.requestDigest, modelId)
    .first<{ status: string; ordinal_exists: number; previous_called: number }>();
  if (!current || current.status !== "dispatched" || Boolean(current.ordinal_exists) || !Boolean(current.previous_called)) {
    throw new PublicApiError(409, "conflict", "模型派发凭证已失效，本轮未调用模型。");
  }
  const counts = await readAiChatDispatchCounts({
    ownerEmail: input.ownerEmail,
    modelId,
    dayStart,
    dayEnd,
    activeCutoff,
    dispatchedAt,
  }, db);
  if (
    counts.ownerProviderDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerOwner
    || counts.globalProviderDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesGlobal
    || counts.modelProviderDaily >= AI_CHAT_DISPATCH_LIMITS.maximumDailyProviderDispatchesPerModel
  ) {
    throw new PublicApiError(429, "rate_limited", "今日模型派发额度已满，本轮未调用模型，请于下一个上海自然日再试。");
  }
  throw new PublicApiError(409, "conflict", "模型派发凭证已失效，本轮未调用模型。");
}

export async function completeAiChatRequest(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  expectedStatus: "processing" | "dispatched";
  conversationId: string;
  userMessage?: { content: string; messageKind: AiConversationMessage["messageKind"] };
  assistantMessage: { id: string; content: string; messageKind: AiConversationMessage["messageKind"] };
  result: Omit<AiChatRequestReceiptResult, "assistantMessageId">;
}, db: D1Database = getD1Database()): Promise<void> {
  await ensureAiAssistantSchema(db);
  const assistantContent = normalizeMessageContent(input.assistantMessage.content, MAX_MESSAGE_LENGTH);
  if (!assistantContent) throw new Error("消息不能为空");
  const userMessage = input.userMessage
    ? {
      id: `ai-msg-${randomUUID()}`,
      content: normalizeMessageContent(input.userMessage.content, MAX_MESSAGE_LENGTH),
      messageKind: input.userMessage.messageKind,
    }
    : null;
  if (input.userMessage && !userMessage?.content) throw new Error("消息不能为空");
  const receiptResult: AiChatRequestReceiptResult = {
    ...input.result,
    conversationId: input.conversationId,
    assistantMessageId: input.assistantMessage.id,
  };
  const receiptFence = `EXISTS (
    SELECT 1 FROM ai_chat_request_receipts r
    WHERE r.id = ? AND r.owner_email = ? AND r.request_digest = ? AND r.status = ?
  )`;
  const statements = [];
  if (userMessage) {
    statements.push(db.prepare(`INSERT INTO ai_conversation_messages (id, conversation_id, role, content, message_kind)
      SELECT ?, ?, 'user', ?, ? WHERE EXISTS (SELECT 1 FROM ai_conversations WHERE id = ?) AND ${receiptFence}`)
      .bind(
        userMessage.id,
        input.conversationId,
        userMessage.content,
        userMessage.messageKind,
        input.conversationId,
        input.receiptId,
        input.ownerEmail,
        input.requestDigest,
        input.expectedStatus,
      ));
  }
  statements.push(
    db.prepare(`INSERT INTO ai_conversation_messages (id, conversation_id, role, content, message_kind)
      SELECT ?, ?, 'assistant', ?, ? WHERE EXISTS (SELECT 1 FROM ai_conversations WHERE id = ?) AND ${receiptFence}`)
      .bind(
        input.assistantMessage.id,
        input.conversationId,
        assistantContent,
        input.assistantMessage.messageKind,
        input.conversationId,
        input.receiptId,
        input.ownerEmail,
        input.requestDigest,
        input.expectedStatus,
      ),
    db.prepare(`UPDATE ai_conversations SET updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND EXISTS (
        SELECT 1 FROM ai_conversation_messages WHERE id = ? AND conversation_id = ?
      )`).bind(input.conversationId, input.assistantMessage.id, input.conversationId),
    db.prepare(`UPDATE ai_chat_request_receipts
      SET status = 'succeeded', conversation_id = ?, assistant_message_id = ?, result_json = ?,
          error_code = NULL, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND owner_email = ? AND request_digest = ? AND status = ?
        AND EXISTS (
          SELECT 1 FROM ai_conversation_messages WHERE id = ? AND conversation_id = ?
        )`).bind(
      input.conversationId,
      input.assistantMessage.id,
      JSON.stringify(receiptResult),
      input.receiptId,
      input.ownerEmail,
      input.requestDigest,
      input.expectedStatus,
      input.assistantMessage.id,
      input.conversationId,
    ),
  );
  const results = await db.batch(statements);
  if (results.some((result) => Number(result?.meta.changes ?? 0) !== 1)) {
    throw new PublicApiError(409, "conflict", "聊天请求完成权已失效，结果未发布。");
  }
}

export async function failAiChatRequestBeforeDispatch(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  errorCode: string;
}, db: D1Database = getD1Database()): Promise<boolean> {
  const errorCode = normalizeReceiptErrorCode(input.errorCode);
  const [failed] = await db.batch([
    db.prepare(`UPDATE ai_chat_request_receipts
      SET status = 'failed', error_code = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND owner_email = ? AND request_digest = ?
        AND status IN ('processing', 'dispatched')
        AND NOT EXISTS (SELECT 1 FROM ai_chat_provider_dispatches d
          WHERE d.receipt_id = ai_chat_request_receipts.id AND d.provider_called_at IS NOT NULL)`)
      .bind(errorCode, input.receiptId, input.ownerEmail, input.requestDigest),
    db.prepare(`DELETE FROM ai_chat_provider_dispatches
      WHERE receipt_id = ? AND provider_called_at IS NULL
        AND EXISTS (SELECT 1 FROM ai_chat_request_receipts r
          WHERE r.id = ai_chat_provider_dispatches.receipt_id AND r.owner_email = ?
            AND r.request_digest = ? AND r.status = 'failed' AND r.error_code = ?)`)
      .bind(input.receiptId, input.ownerEmail, input.requestDigest, errorCode),
  ]);
  return Number(failed?.meta.changes ?? 0) === 1;
}

export async function markAiChatRequestUnknown(input: {
  receiptId: string;
  requestDigest: string;
  ownerEmail: string;
  errorCode: string;
}, db: D1Database = getD1Database()): Promise<void> {
  await db.prepare(`UPDATE ai_chat_request_receipts
    SET status = 'unknown', error_code = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE id = ? AND owner_email = ? AND request_digest = ? AND status = 'dispatched'`)
    .bind(normalizeReceiptErrorCode(input.errorCode), input.receiptId, input.ownerEmail, input.requestDigest).run();
}

export async function updateConversationModel(
  conversationId: string,
  modelId: string,
  db: D1Database = getD1Database(),
): Promise<void> {
  await ensureAiAssistantSchema(db);
  await db.prepare("UPDATE ai_conversations SET model_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?")
    .bind(modelId, conversationId).run();
}

export async function selectConversationModel(
  conversationId: string,
  modelId: string,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiConversationRecord> {
  const conversation = await requireConversationAccess(conversationId, principal, db);
  const model = await resolveChatModel({ modelId, allowFallback: false }, db);
  if (!model) throw new Error("指定对话模型不存在、已停用或不支持对话");
  if (conversation.modelId !== model.id) await updateConversationModel(conversation.id, model.id, db);
  return requireConversationAccess(conversation.id, principal, db);
}

export async function deleteAiConversation(
  conversationId: string,
  principal: AppPrincipal,
  reasonOrDb: string | D1Database = "用户删除对话",
  db: D1Database = getD1Database(),
): Promise<boolean> {
  const reason = typeof reasonOrDb === "string" ? reasonOrDb : "用户删除对话";
  const database = typeof reasonOrDb === "string" ? db : reasonOrDb;
  await ensureAiAssistantSchema(database);
  await ensureAiArtifactSchema(database);
  const normalizedId = requireAiEntityId(conversationId, "conversationId");
  const normalizedReason = normalizeDeletionReason(reason);
  const auditId = `ai-conversation-delete-${randomUUID()}`;
  const scopeAccess = aiConversationScopeAccessSql(principal.scope);
  const [auditResult] = await database.batch([
    database.prepare(`INSERT INTO ai_conversation_deletion_audits (
        audit_id, conversation_id, conversation_owner, actor_email, actor_role, reason,
        deleted_message_count, deleted_artifact_count, deleted_at
      )
      SELECT ?, c.id, c.created_by, ?, ?, ?,
        (SELECT COUNT(*) FROM ai_conversation_messages m WHERE m.conversation_id = c.id),
        (SELECT COUNT(*) FROM ai_artifacts a WHERE a.conversation_id = c.id),
        CURRENT_TIMESTAMP
      FROM ai_conversations c
      ${scopeAccess.join}
      WHERE c.id = ? AND (? = 'admin' OR c.created_by = ?)
        ${scopeAccess.clause}`)
      .bind(
        auditId,
        principal.email,
        principal.role,
        normalizedReason,
        normalizedId,
        principal.role,
        principal.email,
        ...scopeAccess.values,
      ),
    database.prepare(`DELETE FROM ai_artifacts
      WHERE conversation_id = ?
        AND EXISTS (SELECT 1 FROM ai_conversation_deletion_audits WHERE audit_id = ? AND conversation_id = ?)`)
      .bind(normalizedId, auditId, normalizedId),
    database.prepare(`DELETE FROM ai_conversation_messages
      WHERE conversation_id = ?
        AND EXISTS (SELECT 1 FROM ai_conversation_deletion_audits WHERE audit_id = ? AND conversation_id = ?)`)
      .bind(normalizedId, auditId, normalizedId),
    database.prepare(`DELETE FROM ai_conversation_scopes
      WHERE conversation_id = ?
        AND EXISTS (SELECT 1 FROM ai_conversation_deletion_audits WHERE audit_id = ? AND conversation_id = ?)`)
      .bind(normalizedId, auditId, normalizedId),
    database.prepare(`DELETE FROM ai_conversations
      WHERE id = ?
        AND (? = 'admin' OR created_by = ?)
        AND EXISTS (SELECT 1 FROM ai_conversation_deletion_audits WHERE audit_id = ? AND conversation_id = ?)`)
      .bind(normalizedId, principal.role, principal.email, auditId, normalizedId),
  ]);
  return Number(auditResult?.meta.changes ?? 0) === 1;
}

export async function requireConversationAccess(conversationId: string, principal: AppPrincipal, db: D1Database = getD1Database()): Promise<AiConversationRecord> {
  await ensureAiAssistantSchema(db);
  const normalizedId = requireAiEntityId(conversationId, "conversationId");
  const scopeAccess = aiConversationScopeAccessSql(principal.scope);
  const row = await db.prepare(`SELECT c.id, c.title, c.model_id, c.created_by, c.created_at, c.updated_at
    FROM ai_conversations c
    ${scopeAccess.join}
    WHERE c.id = ? AND (? = 'admin' OR c.created_by = ?)
      ${scopeAccess.clause}
    LIMIT 1`)
    .bind(normalizedId, principal.role, principal.email, ...scopeAccess.values).first<AiConversationRow>();
  if (!row) throw new PublicApiError(404, "not_found", "对话不存在或无权访问。");
  return mapConversationRecord(row);
}

export async function listConversationMessages(
  conversationId: string,
  principal: AppPrincipal,
  inputOrDb: { pageSize?: number; before?: number | null } | D1Database = {},
  database: D1Database = getD1Database(),
): Promise<AiConversationMessagePage> {
  const input = isD1Database(inputOrDb) ? {} : inputOrDb;
  const db = isD1Database(inputOrDb) ? inputOrDb : database;
  const conversation = await requireConversationAccess(conversationId, principal, db);
  const pageSize = requireBoundedPositiveInteger(input.pageSize, AI_MESSAGE_PAGE_SIZE_DEFAULT, AI_MESSAGE_PAGE_SIZE_MAX, "pageSize");
  const before = input.before === null || input.before === undefined
    ? null
    : requireBoundedPositiveInteger(input.before, 1, Number.MAX_SAFE_INTEGER, "before");
  const beforeClause = before === null ? "" : " AND m.rowid < ?";
  const beforeBindings = before === null ? [] : [before];
  const ownerClause = principal.role === "admin" ? "" : " AND c.created_by = ?";
  const ownerBindings = principal.role === "admin" ? [] : [principal.email];
  const scopeAccess = aiConversationScopeAccessSql(principal.scope);
  const [countRow, rows] = await Promise.all([
    db.prepare(`SELECT COUNT(*) total
      FROM ai_conversation_messages m
      INNER JOIN ai_conversations c ON c.id = m.conversation_id
      ${scopeAccess.join}
      WHERE m.conversation_id = ?${ownerClause}${scopeAccess.clause}`)
      .bind(conversation.id, ...ownerBindings, ...scopeAccess.values).first<CountRow>(),
    db.prepare(`SELECT m.rowid message_rowid, m.id, m.conversation_id, m.role,
        substr(m.content, 1, ?) content,
        length(CAST(m.content AS BLOB)) original_content_bytes,
        m.message_kind, m.created_at
      FROM ai_conversation_messages m
      INNER JOIN ai_conversations c ON c.id = m.conversation_id
      ${scopeAccess.join}
      WHERE m.conversation_id = ?${ownerClause}${scopeAccess.clause}${beforeClause}
      ORDER BY m.rowid DESC
      LIMIT ?`)
      .bind(AI_MESSAGE_QUERY_CHARACTER_MAX, conversation.id, ...ownerBindings, ...scopeAccess.values, ...beforeBindings, pageSize + 1)
      .all<AiConversationMessageRow>(),
  ]);
  const fetched = rows.results ?? [];
  const hasMore = fetched.length > pageSize;
  const selectedRows = fetched.slice(0, pageSize).reverse();
  const messages = mapConversationMessagePage(selectedRows);
  const artifactsByMessage = await listAiArtifactsForConversation(
    conversation.id,
    principal,
    db,
    selectedRows.map((row) => row.id),
  );
  const items = messages.map((message) => ({
    ...message,
    artifacts: artifactsByMessage.get(message.id) ?? [],
  }));
  const total = Number(countRow?.total ?? 0);
  const oldestRowId = selectedRows.length > 0
    ? Math.min(...selectedRows.map((row) => Number(row.message_rowid)))
    : null;
  return {
    items,
    pagination: {
      pageSize,
      total,
      returned: items.length,
      truncated: total > items.length,
      hasMore,
      nextBefore: hasMore && oldestRowId !== null && Number.isSafeInteger(oldestRowId) ? oldestRowId : null,
    },
    limits: {
      maximumPageSize: AI_MESSAGE_PAGE_SIZE_MAX,
      maximumMessageBytes: AI_MESSAGE_RESPONSE_BYTES_MAX,
      maximumPageContentBytes: AI_MESSAGE_PAGE_CONTENT_BYTES_MAX,
    },
  };
}

export async function listConversationContextMessages(
  conversationId: string,
  db: D1Database = getD1Database(),
  limit = 24,
): Promise<AiConversationMessage[]> {
  await ensureAiAssistantSchema(db);
  const boundedLimit = Math.max(1, Math.min(100, Math.trunc(limit)));
  const rows = await db.prepare(
    `SELECT id, conversation_id, role, content, message_kind, created_at
     FROM (
       SELECT rowid message_rowid, id, conversation_id, role, content, message_kind, created_at
       FROM ai_conversation_messages
       WHERE conversation_id = ?
         AND message_kind = 'message'
         AND rowid > COALESCE((
           SELECT rowid FROM ai_conversation_messages
           WHERE conversation_id = ? AND message_kind = 'context_reset'
           ORDER BY rowid DESC LIMIT 1
         ), 0)
       ORDER BY rowid DESC
       LIMIT ?
     )
     ORDER BY message_rowid ASC`,
  ).bind(conversationId, conversationId, boundedLimit).all<AiConversationMessageRow>();
  return (rows.results ?? []).map(mapConversationMessage);
}

export async function recordAiChannelCallbackEvent(input: { channelId: string; eventKey: string; payloadDigest: string }, db: D1Database = getD1Database()): Promise<boolean> {
  await ensureAiAssistantSchema(db);
  const result = await db.prepare(
    `INSERT INTO ai_channel_callback_events (id, channel_id, event_key, payload_digest)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(channel_id, event_key) DO NOTHING`,
  ).bind(`ai-event-${randomUUID()}`, input.channelId, input.eventKey.slice(0, 300), input.payloadDigest.slice(0, 128)).run();
  return Number(result.meta.changes ?? 0) > 0;
}

export async function resolveChatModel(
  input?: { modelId?: string | null; allowFallback?: boolean } | D1Database,
  database: D1Database = getD1Database(),
): Promise<VersionedAiTextModelRuntimeConfig | null> {
  const db = isD1Database(input) ? input : database;
  const options = isD1Database(input) ? undefined : input;
  await ensureAiAssistantSchema(db);
  const modelId = options?.modelId?.trim();
  if (modelId) {
    const selected = await db.prepare(
      `SELECT ${modelSelectColumns} FROM ai_models
       WHERE id = ? AND model_type IN ('text', 'vision') AND status = 'enabled' LIMIT 1`,
    ).bind(modelId).first<AiModelRow>();
    if (selected) return mapAiTextModelRuntime(selected);
    if (options?.allowFallback === false) return null;
  }
  const defaultModel = await db.prepare(
    `SELECT ${modelSelectColumns} FROM ai_models
     WHERE model_type = 'text' AND status = 'enabled' AND is_default_text_model = 1 LIMIT 1`,
  ).first<AiModelRow>();
  if (defaultModel) return mapAiTextModelRuntime(defaultModel);
  const fallback = await db.prepare(
    `SELECT ${modelSelectColumns} FROM ai_models
     WHERE model_type = 'text' AND status = 'enabled' ORDER BY updated_at DESC LIMIT 1`,
  ).first<AiModelRow>();
  return fallback ? mapAiTextModelRuntime(fallback) : null;
}

export async function testAiModelConnection(modelId: string, db: D1Database = getD1Database()): Promise<{ ok: true; message: string }> {
  const model = await getAiModelSecretById(modelId, db);
  if (!model) throw new Error("模型不存在");
  try {
    if (!model.base_url || !model.api_key_encrypted) throw new Error("模型地址或 API Key 未配置");
    if (model.model_type === "vision" || model.model_type === "image") {
      await probeVisionModelConnection(model);
    } else {
      await completeText({ model: mapAiTextModelRuntime(model), messages: [{ role: "user", content: "仅回复 OK" }] });
    }
    await setModelTestResult(modelId, "连接成功", db);
    return { ok: true, message: model.model_type === "vision" || model.model_type === "image" ? "视觉模型图片识别验证成功" : "文本模型连接成功" };
  } catch (error) {
    await setModelTestResult(modelId, `连接失败：${safeErrorMessage(error)}`, db);
    throw error;
  }
}

export async function sendAiChannelText(channelId: string, content: string, db: D1Database = getD1Database()): Promise<{ ok: true; message: string }> {
  const channel = await getAiChannelSecretById(channelId, db);
  if (!channel) throw new Error("渠道不存在");
  if (channel.status !== "enabled") throw new Error("渠道已停用");
  if (!channel.sendEnabled) throw new Error("渠道未启用消息发送");
  if (channel.kind !== "dingtalk_group_bot" && channel.kind !== "wechat_work_group_bot") {
    throw new Error("当前仅支持钉钉和企业微信的群机器人 Webhook 发送；应用型渠道需补充应用凭据后启用");
  }

  let endpoint = normalizeAiEndpointUrl(channel.webhookUrl, "channel");
  const text = normalizeText(content, "", 4_000);
  if (!text) throw new Error("发送内容不能为空");
  if (channel.kind === "dingtalk_group_bot" && channel.callbackTokenEncrypted) {
    const secret = await decryptSecret(channel.callbackTokenEncrypted);
    if (secret) {
      const timestamp = Date.now().toString();
      const signedUrl = new URL(endpoint);
      signedUrl.searchParams.set("timestamp", timestamp);
      signedUrl.searchParams.set("sign", await createDingTalkSignature(timestamp, secret));
      endpoint = signedUrl.toString();
    }
  }

  let response: Response;
  let providerResult: Record<string, unknown> | null;
  try {
    const bounded = await fetchBoundedJson({
      url: endpoint,
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ msgtype: "text", text: { content: text } }),
      },
      timeoutMs: CHANNEL_REQUEST_TIMEOUT_MS,
      maxBytes: CHANNEL_RESPONSE_LIMIT_BYTES,
    });
    response = bounded.response;
    providerResult = isRecord(bounded.data) ? bounded.data : null;
  } catch (error) {
    if (error instanceof BoundedFetchError && error.code === "response_too_large") {
      throw new Error("渠道响应超过 64 KiB 安全上限");
    }
    throw new Error("渠道调用失败：网络、超时或响应格式异常");
  }
  if (!response.ok || !isSuccessfulChannelResponse(providerResult)) {
    throw new Error(`渠道调用失败${providerErrorSuffix(response.status, providerResult)}`);
  }
  return { ok: true, message: "渠道消息已发送" };
}

export async function testAiChannelConnection(channelId: string, db: D1Database = getD1Database()): Promise<{ ok: true; message: string }> {
  try {
    const result = await sendAiChannelText(channelId, "TERUISI AI 助理渠道连接测试", db);
    await setChannelTestResult(channelId, "连接成功", db);
    return result;
  } catch (error) {
    await setChannelTestResult(channelId, `连接失败：${safeErrorMessage(error)}`, db);
    throw error;
  }
}

export async function generateAssistantReply(input: {
  prompt: string;
  principal: AppPrincipal;
  conversationId: string;
  model: AiTextModelRuntimeConfig;
  requestId?: string;
  surface?: AiToolExecutionContext["surface"];
  signal?: AbortSignal;
  systemPrompt?: string;
  /** Untrusted, request-local data appended to the latest user message only. Never persisted. */
  runtimeUserContext?: string;
  beforeProviderRequest?: () => Promise<void>;
  persistAssistantMessage?: (message: { id: string; content: string }) => Promise<void>;
}, db: D1Database = getD1Database()): Promise<AiAssistantReply> {
  const startedAt = Date.now();
  const requestId = input.requestId ?? `ai-chat-${randomUUID()}`;
  const surface = input.surface ?? "ai_chat";
  try {
    const messages = appendRuntimeUserContext(
      await listConversationContextMessages(input.conversationId, db, 24),
      input.runtimeUserContext,
    );
    const toolContext: AiToolExecutionContext = {
      principal: input.principal,
      requestId,
      surface,
      signal: input.signal,
    };
    const toolRuntime = createRegisteredToolExecutionRuntime(toolContext, {
      maxTotalCalls: input.model.maxTotalToolCalls,
    });
    const tools = input.model.protocol === "anthropic"
      ? toolRuntime.getAnthropicTools()
      : toolRuntime.getOpenAiTools();
    const toolTitles = new Map(toolRuntime.getVisibleToolCatalog().map((tool) => [tool.name, tool.title]));
    const artifactCandidates: AiTableArtifactCandidate[] = [];
    const executeTool = async (name: string, args: unknown, metadata: ProviderToolCallMetadata) => {
      const result = await toolRuntime.execute(name, args, metadata);
      if (result.ok && artifactCandidates.length < AI_ARTIFACT_LIMITS.artifactsPerMessage) {
        artifactCandidates.push(...extractAiTableArtifactCandidates({
          toolName: name,
          toolTitle: toolTitles.get(name) ?? name,
          data: result.data,
        }).slice(0, AI_ARTIFACT_LIMITS.artifactsPerMessage - artifactCandidates.length));
      }
      return result;
    };
    const reply = await completeTextWithTools({
      model: input.model,
      messages,
      systemPrompt: input.systemPrompt ?? AI_TOOL_SYSTEM_PROMPT,
      tools,
      executeTool,
      signal: input.signal,
      beforeProviderRequest: input.beforeProviderRequest,
    });
    if (!reply) throw new Error("模型未返回内容");
    const persistedReply = normalizeMessageContent(reply, MAX_MESSAGE_LENGTH);
    if (!persistedReply) throw new Error("模型未返回内容");
    const messageId = `ai-msg-${randomUUID()}`;
    if (input.persistAssistantMessage) {
      await input.persistAssistantMessage({ id: messageId, content: persistedReply });
    } else {
      await appendConversationMessageWithId({
        id: messageId,
        conversationId: input.conversationId,
        role: "assistant",
        content: persistedReply,
        messageKind: "message",
      }, db);
    }
    let artifacts: AiTableArtifact[] = [];
    try {
      artifacts = await persistAiTableArtifacts({
        conversationId: input.conversationId,
        messageId,
        principal: input.principal,
        candidates: artifactCandidates,
        database: db,
      });
    } catch {
      await recordAiToolAudit({
        requestId,
        actorEmail: input.principal.email,
        actorRole: input.principal.role,
        surface,
        toolName: "persist_ai_artifacts",
        arguments: { conversationId: input.conversationId, messageId, candidateCount: artifactCandidates.length },
        status: "failed",
        durationMs: Date.now() - startedAt,
        errorCode: "artifact_persist_failed",
      }).catch(() => undefined);
    }
    await recordAiToolAudit({
      requestId,
      actorEmail: input.principal.email,
      actorRole: input.principal.role,
      surface,
      toolName: "chat_message",
      arguments: { conversationId: input.conversationId, promptCharacters: input.prompt.length },
      status: "succeeded",
      durationMs: Date.now() - startedAt,
      result: { reply: persistedReply, artifactCount: artifacts.length },
    });
    return { reply: persistedReply, messageId, artifacts };
  } catch (error) {
    await recordAiToolAudit({
      requestId,
      actorEmail: input.principal.email,
      actorRole: input.principal.role,
      surface,
      toolName: "chat_message",
      arguments: { conversationId: input.conversationId, promptCharacters: input.prompt.length },
      status: "failed",
      durationMs: Date.now() - startedAt,
      errorCode: isAiRequestCancelled(error, input.signal) ? "ai_request_cancelled" : "ai_model_error",
    });
    throw error;
  }
}

export function appendRuntimeUserContext(
  messages: Array<{ role: "user" | "assistant"; content: string }>,
  runtimeUserContext: string | undefined,
) {
  const context = runtimeUserContext?.trim().slice(0, 6_000) ?? "";
  if (!context) return messages.map((message) => ({ role: message.role, content: message.content }));
  const output = messages.map((message) => ({ role: message.role, content: message.content }));
  for (let index = output.length - 1; index >= 0; index -= 1) {
    if (output[index]?.role !== "user") continue;
    output[index] = { ...output[index]!, content: `${output[index]!.content}\n\n${context}` };
    return output;
  }
  return [...output, { role: "user" as const, content: context }];
}

export async function generateConfiguredAnalysisReply(input: {
  prompt: string;
  principal: AppPrincipal;
  requestId: string;
  auditArguments: Record<string, unknown>;
}, db: D1Database = getD1Database()): Promise<string> {
  const startedAt = Date.now();
  const model = await resolveChatModel(db);
  if (!model) throw new Error("尚未配置可用的文本模型。请由管理员先在“AI 助理”中新增、启用并测试文本模型；客服数据导入不受影响。");
  const auditBase = {
    requestId: input.requestId,
    actorEmail: input.principal.email,
    actorRole: input.principal.role,
    surface: "customer_service_ai",
    toolName: "analyze_customer_service_conversations",
    arguments: input.auditArguments,
  } as const;
  await recordAiToolAudit({ ...auditBase, status: "started", durationMs: 0 });
  try {
    const reply = await completeText({
      model,
      messages: [{ role: "user", content: input.prompt }],
    });
    if (!reply) throw new Error("模型未返回分析结果");
    await recordAiToolAudit({ ...auditBase, status: "succeeded", durationMs: Date.now() - startedAt, result: { returned: 1, modelId: model.id, responseCharacters: reply.length } });
    return reply;
  } catch (error) {
    await recordAiToolAudit({ ...auditBase, status: "failed", durationMs: Date.now() - startedAt, errorCode: "customer_service_analysis_failed" });
    throw error;
  }
}

async function getAiModelSecretById(id: string, db: D1Database): Promise<AiModelRow | null> {
  await ensureAiAssistantSchema(db);
  return db.prepare(
    `SELECT ${modelSelectColumns} FROM ai_models WHERE id = ? LIMIT 1`,
  ).bind(id).first<AiModelRow>();
}

async function getAiChannelRowById(id: string, db: D1Database): Promise<AiChannelRow | null> {
  await ensureAiAssistantSchema(db);
  return db.prepare(
    "SELECT id, name, kind, status, send_enabled, callback_enabled, webhook_url, callback_token_encrypted, callback_token_suffix, aes_key_encrypted, aes_key_suffix, receiver_id, last_test_result, last_tested_at, created_at, updated_at FROM ai_channels WHERE id = ? LIMIT 1",
  ).bind(id).first<AiChannelRow>();
}

function normalizeAiModelInput(input: AiModelInput, endpointSecurityContext: AiEndpointSecurityContext) {
  const id = optionalId(input.id);
  const expectedVersion = id
    ? requireAiModelExpectedVersion(input.expectedVersion)
    : undefined;
  if (!id && input.expectedVersion !== undefined) {
    throw new PublicApiError(400, "invalid_request", "新增模型不能携带 expectedVersion。");
  }
  const protocol = asModelProtocol(input.protocol);
  const modelType = asModelType(input.modelType);
  const status = asModelStatus(input.status);
  const reasoningMode = asModelReasoningMode(input.reasoningMode ?? DEFAULT_MODEL_REASONING_MODE);
  if (protocol !== "openai_compatible" && reasoningMode !== "auto") {
    throw new Error("当前仅 OpenAI 兼容协议支持配置关闭推理模式");
  }
  const name = normalizeText(input.name, "", MAX_NAME_LENGTH);
  const modelName = normalizeText(input.modelName, "", MAX_NAME_LENGTH);
  if (!name || !modelName) throw new Error("模型名称和模型标识不能为空");
  const apiKey = input.apiKey?.trim() || undefined;
  return {
    id,
    expectedVersion,
    name,
    protocol,
    modelType,
    modelName,
    baseUrl: input.baseUrl === undefined
      ? undefined
      : normalizeAiModelEndpointForStorage(input.baseUrl, endpointSecurityContext),
    apiKey,
    status,
    isDefaultTextModel: Boolean(input.isDefaultTextModel),
    timeoutMs: boundedInteger(input.timeoutMs, DEFAULT_MODEL_TIMEOUT_MS, 3_000, 120_000, "模型超时"),
    maxTokens: boundedInteger(input.maxTokens, DEFAULT_MODEL_MAX_TOKENS, 128, 8_192, "最大输出 token"),
    reasoningMode,
    temperatureMilli: boundedInteger(input.temperatureMilli, DEFAULT_MODEL_TEMPERATURE_MILLI, 0, 1_000, "温度"),
    maxToolRounds: boundedInteger(input.maxToolRounds, DEFAULT_MODEL_MAX_TOOL_ROUNDS, 1, AI_MODEL_TOOL_BUDGET_LIMITS.maximumRounds, "工具轮数"),
    maxTotalToolCalls: boundedInteger(input.maxTotalToolCalls, DEFAULT_MODEL_MAX_TOTAL_TOOL_CALLS, 1, AI_MODEL_TOOL_BUDGET_LIMITS.maximumTotalCalls, "工具调用总数"),
  };
}

function normalizeAiChannelInput(input: AiChannelInput): Required<Omit<AiChannelInput, "id" | "webhookUrl" | "callbackToken" | "aesKey" | "receiverId">> & Pick<AiChannelInput, "id" | "webhookUrl" | "callbackToken" | "aesKey" | "receiverId"> {
  const name = normalizeText(input.name, "", MAX_NAME_LENGTH);
  if (!name) throw new Error("渠道名称不能为空");
  const kind = asChannelKind(input.kind);
  const status = asChannelStatus(input.status);
  const supportsWebhookSend = kind === "dingtalk_group_bot" || kind === "wechat_work_group_bot";
  if (input.sendEnabled && !supportsWebhookSend) throw new Error("当前应用型渠道仅支持安全回调，不支持通过 Webhook 主动发送");
  if (input.callbackEnabled && kind !== "wechat_work_app") throw new Error("当前仅支持企业微信应用回调；钉钉和企业微信机器人用于主动发送消息");
  return {
    id: optionalId(input.id),
    name,
    kind,
    status,
    sendEnabled: Boolean(input.sendEnabled),
    callbackEnabled: Boolean(input.callbackEnabled),
    webhookUrl: input.webhookUrl?.trim() || undefined,
    callbackToken: input.callbackToken?.trim() || undefined,
    aesKey: input.aesKey?.trim() || undefined,
    receiverId: normalizeText(input.receiverId ?? "", "", 160) || undefined,
  };
}

function mapAiModelRecord(row: AiModelRow): AiModelRecord {
  return {
    id: row.id,
    version: row.version,
    name: row.name,
    protocol: asModelProtocol(row.protocol),
    modelType: asModelType(row.model_type),
    modelName: row.model_name,
    baseUrl: redactAiModelEndpointUrl(row.base_url),
    apiKeySuffix: row.api_key_suffix,
    isDefaultTextModel: Boolean(row.is_default_text_model),
    status: asModelStatus(row.status),
    timeoutMs: row.timeout_ms,
    maxTokens: row.max_tokens,
    reasoningMode: asModelReasoningMode(row.reasoning_mode),
    temperatureMilli: row.temperature_milli,
    maxToolRounds: row.max_tool_rounds,
    maxTotalToolCalls: row.max_total_tool_calls,
    lastTestResult: row.last_test_result,
    lastTestedAt: row.last_tested_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapAiChannelRecord(row: AiChannelRow): AiChannelRecord {
  return {
    id: row.id,
    name: row.name,
    kind: asChannelKind(row.kind),
    status: asChannelStatus(row.status),
    sendEnabled: Boolean(row.send_enabled),
    callbackEnabled: Boolean(row.callback_enabled),
    webhookUrlMasked: maskWebhookUrl(row.webhook_url),
    callbackTokenMasked: row.callback_token_suffix || "未配置",
    aesKeyMasked: row.aes_key_suffix || "未配置",
    receiverId: row.receiver_id,
    lastTestResult: row.last_test_result,
    lastTestedAt: row.last_tested_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapConversationRecord(row: AiConversationRow): AiConversationRecord {
  return { id: row.id, title: row.title, modelId: row.model_id, createdBy: row.created_by, createdAt: row.created_at, updatedAt: row.updated_at };
}

function parseAiChatReceiptResult(row: AiChatRequestReceiptRow): AiChatRequestReceiptResult {
  let parsed: unknown;
  try {
    parsed = row.result_json ? JSON.parse(row.result_json) : null;
  } catch {
    throw new PublicApiError(503, "service_unavailable", "聊天请求回执损坏，已阻止重复调用模型。");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new PublicApiError(503, "service_unavailable", "聊天请求回执不完整，已阻止重复调用模型。");
  }
  const value = parsed as Record<string, unknown>;
  const outcome = value.outcome;
  const modelId = value.modelId;
  if (
    typeof value.conversationId !== "string"
    || value.conversationId !== row.conversation_id
    || typeof value.reply !== "string"
    || value.reply.length > MAX_MESSAGE_LENGTH
    || (modelId !== null && typeof modelId !== "string")
    || (outcome !== "answered" && outcome !== "context_reset" && outcome !== "help")
    || typeof value.assistantMessageId !== "string"
    || value.assistantMessageId !== row.assistant_message_id
  ) {
    throw new PublicApiError(503, "service_unavailable", "聊天请求回执不完整，已阻止重复调用模型。");
  }
  return {
    conversationId: value.conversationId,
    reply: value.reply,
    modelId,
    outcome,
    assistantMessageId: value.assistantMessageId,
  };
}

function normalizeReceiptErrorCode(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").slice(0, 80);
  return normalized || "unknown_error";
}

function mapConversationMessage(row: AiConversationMessageRow): AiConversationMessage {
  const contentBytes = new TextEncoder().encode(row.content).byteLength;
  return {
    id: row.id,
    conversationId: row.conversation_id,
    role: row.role === "assistant" ? "assistant" : "user",
    content: row.content,
    messageKind: row.message_kind === "context_reset" ? "context_reset" : row.message_kind === "help" ? "help" : "message",
    createdAt: row.created_at,
    artifacts: [],
    contentBytes,
    contentTruncated: Number(row.original_content_bytes ?? contentBytes) > contentBytes,
  };
}

function mapConversationMessagePage(rows: AiConversationMessageRow[]): AiConversationMessage[] {
  let remainingBytes = AI_MESSAGE_PAGE_CONTENT_BYTES_MAX;
  return rows.map((row, index) => {
    const remainingMessages = rows.length - index;
    const fairShare = Math.floor(remainingBytes / Math.max(1, remainingMessages));
    const byteLimit = Math.min(AI_MESSAGE_RESPONSE_BYTES_MAX, fairShare);
    const limited = truncateUtf8(row.content, byteLimit);
    remainingBytes -= limited.bytes;
    return mapConversationMessage({
      ...row,
      content: limited.text,
      original_content_bytes: Math.max(Number(row.original_content_bytes ?? 0), limited.originalBytes),
    });
  });
}

function truncateUtf8(value: string, maximumBytes: number): {
  text: string;
  bytes: number;
  originalBytes: number;
} {
  const encoded = new TextEncoder().encode(value);
  if (encoded.byteLength <= maximumBytes) {
    return { text: value, bytes: encoded.byteLength, originalBytes: encoded.byteLength };
  }
  let end = Math.max(0, maximumBytes);
  let text = "";
  while (end > 0) {
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(encoded.slice(0, end));
      break;
    } catch {
      end -= 1;
    }
  }
  return {
    text,
    bytes: new TextEncoder().encode(text).byteLength,
    originalBytes: encoded.byteLength,
  };
}

export type VersionedAiTextModelRuntimeConfig = AiTextModelRuntimeConfig & { version: number };

function mapAiTextModelRuntime(row: AiModelRow): VersionedAiTextModelRuntimeConfig {
  return {
    id: row.id,
    version: row.version,
    name: row.name,
    protocol: asModelProtocol(row.protocol),
    modelName: row.model_name,
    baseUrl: row.base_url,
    apiKeyEncrypted: row.api_key_encrypted,
    timeoutMs: boundedInteger(row.timeout_ms, DEFAULT_MODEL_TIMEOUT_MS, 3_000, 120_000, "模型超时"),
    maxTokens: boundedInteger(row.max_tokens, DEFAULT_MODEL_MAX_TOKENS, 128, 8_192, "最大输出 token"),
    reasoningMode: asModelReasoningMode(row.reasoning_mode),
    temperature: boundedInteger(row.temperature_milli, DEFAULT_MODEL_TEMPERATURE_MILLI, 0, 1_000, "温度") / 1_000,
    maxToolRounds: boundedInteger(row.max_tool_rounds, DEFAULT_MODEL_MAX_TOOL_ROUNDS, 1, AI_MODEL_TOOL_BUDGET_LIMITS.maximumRounds, "工具轮数"),
    maxTotalToolCalls: boundedInteger(row.max_total_tool_calls, DEFAULT_MODEL_MAX_TOTAL_TOOL_CALLS, 1, AI_MODEL_TOOL_BUDGET_LIMITS.maximumTotalCalls, "工具调用总数"),
  };
}

function asModelProtocol(value: unknown): AiModelProtocol {
  if (aiModelProtocols.includes(value as AiModelProtocol)) return value as AiModelProtocol;
  throw new Error("模型协议无效");
}

function asModelType(value: unknown): AiModelType {
  if (value === "image") return "vision";
  if (typeof value === "string" && isAiChatCapableModelType(value)) return value;
  throw new Error("模型类型无效");
}

function asModelStatus(value: unknown): AiModelStatus {
  if (aiModelStatuses.includes(value as AiModelStatus)) return value as AiModelStatus;
  throw new Error("模型状态无效");
}

function asModelReasoningMode(value: unknown): AiModelReasoningMode {
  if (aiModelReasoningModes.includes(value as AiModelReasoningMode)) return value as AiModelReasoningMode;
  throw new Error("模型推理模式无效");
}

function asChannelKind(value: unknown): AiChannelKind {
  if (aiChannelKinds.includes(value as AiChannelKind)) return value as AiChannelKind;
  throw new Error("渠道类型无效");
}

function asChannelStatus(value: unknown): AiChannelStatus {
  if (value === "enabled" || value === "disabled") return value;
  throw new Error("渠道状态无效");
}

function normalizeText(value: string, fallback: string, limit: number): string {
  const text = value.trim().replace(/\s+/g, " ").slice(0, limit);
  return text || fallback;
}

function normalizeMessageContent(value: string, limit: number): string {
  const normalized = value.replace(/\r\n?/g, "\n").replace(/\0/g, "").trim();
  if (normalized.length <= limit) return normalized;
  const marker = "\n\n[内容已按会话存储上限截断]";
  return `${normalized.slice(0, Math.max(0, limit - marker.length))}${marker}`;
}

function requireBoundedPositiveInteger(
  value: number | undefined,
  fallback: number,
  maximum: number,
  field: string,
): number {
  const normalized = value === undefined ? fallback : value;
  if (!Number.isSafeInteger(normalized) || normalized <= 0 || normalized > maximum) {
    throw new PublicApiError(400, "invalid_request", `${field}超出允许范围。`);
  }
  return normalized;
}

function requireAiEntityId(value: string, field: string): string {
  const normalized = value.trim();
  if (!/^[a-zA-Z0-9_-]{1,160}$/.test(normalized)) {
    throw new PublicApiError(400, "invalid_request", `${field}格式无效。`);
  }
  return normalized;
}

function normalizeDeletionReason(value: string): string {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized || new TextEncoder().encode(normalized).byteLength > AI_CONVERSATION_DELETE_REASON_MAX * 4) {
    throw new PublicApiError(400, "invalid_request", "删除原因必须为 1—200 个字符。");
  }
  return normalized.slice(0, AI_CONVERSATION_DELETE_REASON_MAX);
}

function boundedInteger(value: number | undefined, fallback: number, minimum: number, maximum: number, label: string): number {
  const normalized = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(normalized) || normalized < minimum || normalized > maximum) {
    throw new Error(`${label}必须是 ${minimum}—${maximum} 的整数`);
  }
  return normalized;
}

function isD1Database(value: unknown): value is D1Database {
  return Boolean(value && typeof value === "object" && "prepare" in value && typeof value.prepare === "function");
}

async function addMissingColumns(
  db: D1Database,
  table: string,
  columns: ReadonlyArray<readonly [name: string, definition: string]>,
): Promise<void> {
  const info = await db.prepare(`PRAGMA table_info(${table})`).all<{ name: string }>();
  const names = new Set((info.results ?? []).map((column) => column.name));
  for (const [name, definition] of columns) {
    if (!names.has(name)) await db.prepare(`ALTER TABLE ${table} ADD COLUMN ${name} ${definition}`).run();
  }
}

function optionalId(value: string | undefined): string | undefined {
  const id = value?.trim();
  if (!id) return undefined;
  if (!/^[a-zA-Z0-9_-]{1,160}$/.test(id)) throw new Error("配置 ID 格式无效");
  return id;
}

function requireAiModelExpectedVersion(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new PublicApiError(400, "invalid_request", "expectedVersion 必须为 JSON 安全正整数。");
  }
  return value;
}

function maskSuffix(value: string): string {
  return value ? `••••${value.slice(-4)}` : "";
}

async function setModelTestResult(id: string, result: string, db: D1Database): Promise<void> {
  await db.prepare("UPDATE ai_models SET last_test_result = ?, last_tested_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?")
    .bind(result.slice(0, 300), id).run();
}

async function setChannelTestResult(id: string, result: string, db: D1Database): Promise<void> {
  await db.prepare("UPDATE ai_channels SET last_test_result = ?, last_tested_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?")
    .bind(result.slice(0, 300), id).run();
}

function isSuccessfulChannelResponse(result: Record<string, unknown> | null): boolean {
  if (!result) return true;
  if (typeof result.errcode === "number") return result.errcode === 0;
  if (typeof result.code === "number") return result.code === 0 || result.code === 200;
  if (typeof result.success === "boolean") return result.success;
  return true;
}

function providerErrorSuffix(status: number, result: Record<string, unknown> | null): string {
  const providerCode = result && typeof result.errcode === "number" && Number.isSafeInteger(result.errcode)
    ? String(result.errcode).slice(0, 32)
    : result && typeof result.code === "number" && Number.isSafeInteger(result.code)
      ? String(result.code).slice(0, 32)
      : "";
  return `（HTTP ${status}${providerCode ? ` · code ${providerCode}` : ""}）`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function safeErrorMessage(error: unknown): string {
  return redactSensitiveErrorText(error instanceof Error ? error.message : "未知错误")
    .replace(/\s+/g, " ")
    .slice(0, 220);
}

function redactSensitiveErrorText(value: string): string {
  return value
    .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi, "Bearer [redacted]")
    .replace(/\b(api[_-]?key|access[_-]?token|authorization|password|secret|token)\s*[:=]\s*[^\s,;&]+/gi, "$1=[redacted]")
    .replace(/(https?:\/\/[^\s?#]+)\?[^\s#]*/gi, "$1?[redacted]");
}
