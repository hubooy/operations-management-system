import {
  AuthorizationError,
  type AppPrincipal,
} from "@/tests/legacy/auth/authorization";
import {
  aiScopeSnapshotAccessSql,
  serializeAiConversationScope,
} from "@/tests/legacy/ai/conversation-scope";
import { PublicApiError } from "@/lib/http/api-error";
import {
  getD1Database,
  type D1Database,
} from "@/lib/database/d1";

export const AI_MEMORY_POLICY_VERSION = "ai-memory-v1";

export const AI_MEMORY_LIMITS = {
  keyCharacters: 80,
  keyBytes: 320,
  contentCharacters: 2_000,
  contentBytes: 8_000,
  queryCharacters: 200,
  pageSize: 50,
  page: 10_000,
  similarityCandidates: 100,
  retrievalItems: 8,
  retrievalCharacters: 4_000,
} as const;

export const AI_MEMORY_KINDS = [
  "preference",
  "glossary",
  "business_context",
] as const;

export type AiMemoryKind = (typeof AI_MEMORY_KINDS)[number];
export type AiMemoryStatus = "active" | "archived";
export type AiMemoryScopeMode = "owner" | "data_scope";
export type AiMemoryWriteSurface = "management_ui";

export type AiMemoryItem = {
  id: string;
  kind: AiMemoryKind;
  key: string;
  content: string;
  scopeMode: AiMemoryScopeMode;
  status: AiMemoryStatus;
  version: number;
  source: "management_ui" | "web_chat";
  createdAt: string;
  updatedAt: string;
  archivedAt: string | null;
};

export type AiMemoryPage = {
  items: AiMemoryItem[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    returned: number;
    truncated: boolean;
  };
};

export type AiMemoryContextResult = {
  trust: "untrusted_memory_data";
  items: Array<Pick<AiMemoryItem, "id" | "kind" | "key" | "content" | "version" | "updatedAt">>;
  totalMatched: number;
  returned: number;
  truncated: boolean;
};

type AiMemoryRow = {
  id: string;
  owner_email: string;
  kind: string;
  memory_key: string;
  memory_key_normalized: string;
  content: string;
  content_digest: string;
  scope_mode: string;
  scope_json: string;
  scope_digest: string;
  status: string;
  version: number;
  source: string;
  last_operation_id: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

type MemoryGateResults = {
  policyVersion: typeof AI_MEMORY_POLICY_VERSION;
  l0Source: "passed";
  l1Content: "passed";
  l2ExactKey: "unique" | "duplicate" | "same_record";
  l3Similarity: "not_run" | "low";
};

type PreparedMemory = {
  kind: AiMemoryKind;
  key: string;
  keyNormalized: string;
  content: string;
  contentDigest: string;
  scopeMode: AiMemoryScopeMode;
  scopeJson: string;
  scopeDigest: string;
};

const MEMORY_SELECT_COLUMNS = `
  id, owner_email, kind, memory_key, memory_key_normalized, content,
  content_digest, scope_mode, scope_json, scope_digest, status, version,
  source, last_operation_id, created_at, updated_at, archived_at
`;

export const AI_MEMORY_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS ai_memory_entries (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE,
    kind TEXT NOT NULL CHECK (kind IN ('preference', 'glossary', 'business_context')),
    memory_key TEXT NOT NULL,
    memory_key_normalized TEXT NOT NULL,
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    scope_mode TEXT NOT NULL CHECK (scope_mode IN ('owner', 'data_scope')),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    source TEXT NOT NULL CHECK (source IN ('management_ui', 'web_chat')),
    source_conversation_id TEXT,
    source_message_id TEXT,
    last_operation_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_memory_entries_active_key_uq
    ON ai_memory_entries (owner_email, kind, memory_key_normalized, scope_digest)
    WHERE status = 'active'`,
  `CREATE INDEX IF NOT EXISTS ai_memory_entries_owner_status_updated_idx
    ON ai_memory_entries (owner_email, status, updated_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_memory_audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('viewer', 'analyst', 'operator', 'admin')),
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'archive', 'duplicate')),
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'duplicate')),
    scope_digest TEXT NOT NULL,
    before_digest TEXT,
    after_digest TEXT,
    result_version INTEGER NOT NULL CHECK (result_version > 0),
    policy_version TEXT NOT NULL,
    gate_results_json TEXT NOT NULL CHECK (json_valid(gate_results_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_memory_audit_logs_owner_created_idx
    ON ai_memory_audit_logs (owner_email, created_at, id)`,
  `CREATE INDEX IF NOT EXISTS ai_memory_audit_logs_memory_created_idx
    ON ai_memory_audit_logs (memory_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_memory_commit_guards (
    operation_id TEXT PRIMARY KEY NOT NULL,
    audit_present INTEGER NOT NULL CHECK (audit_present = 1)
  )`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export async function ensureAiMemorySchema(
  db: D1Database = getD1Database(),
): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db
    .batch(AI_MEMORY_SCHEMA_STATEMENTS.map((statement) => db.prepare(statement)))
    .then(() => undefined)
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

function normalizeOwnerEmail(principal: AppPrincipal): string {
  return principal.email.trim().toLowerCase();
}

function requireConfirmed(value: unknown): void {
  if (value !== true) {
    throw new PublicApiError(
      400,
      "invalid_request",
      "写入全局记忆前必须由当前用户显式确认。",
    );
  }
}

function requireManagementSurface(value: unknown): AiMemoryWriteSurface {
  if (value !== "management_ui") {
    throw new PublicApiError(
      400,
      "invalid_request",
      "当前入口不允许自动、定时、机器人或后台任务写入全局记忆。",
    );
  }
  return value;
}

function requireMemoryWriter(principal: AppPrincipal): void {
  if (principal.role === "viewer") {
    throw new AuthorizationError(403, "insufficient_role", "当前角色不能写入全局记忆。");
  }
}

function requireKind(value: unknown): AiMemoryKind {
  if (typeof value !== "string" || !AI_MEMORY_KINDS.includes(value as AiMemoryKind)) {
    throw new PublicApiError(400, "invalid_request", "记忆类型无效。");
  }
  return value as AiMemoryKind;
}

function utf8Bytes(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function normalizeMemoryKey(value: unknown): { key: string; normalized: string } {
  if (typeof value !== "string") {
    throw new PublicApiError(400, "invalid_request", "记忆键必须为字符串。");
  }
  const key = value.normalize("NFKC").trim().replace(/\s+/gu, " ");
  if (!key) {
    throw new PublicApiError(400, "invalid_request", "记忆键不能为空。");
  }
  if (key.length > AI_MEMORY_LIMITS.keyCharacters || utf8Bytes(key) > AI_MEMORY_LIMITS.keyBytes) {
    throw new PublicApiError(413, "payload_too_large", "记忆键超过允许大小。");
  }
  if (!/^[\p{L}\p{N}][\p{L}\p{N}_.:：\- ]*$/u.test(key)) {
    throw new PublicApiError(400, "invalid_request", "记忆键只能包含文字、数字、空格和安全分隔符。");
  }
  return { key, normalized: key.toLocaleLowerCase("zh-CN") };
}

function normalizeMemoryContent(value: unknown): string {
  if (typeof value !== "string") {
    throw new PublicApiError(400, "invalid_request", "记忆内容必须为字符串。");
  }
  const content = value
    .normalize("NFKC")
    .replace(/\r\n?/gu, "\n")
    .replace(/\0/gu, "")
    .trim();
  if (!content) {
    throw new PublicApiError(400, "invalid_request", "记忆内容不能为空。");
  }
  if (
    content.length > AI_MEMORY_LIMITS.contentCharacters
    || utf8Bytes(content) > AI_MEMORY_LIMITS.contentBytes
  ) {
    throw new PublicApiError(413, "payload_too_large", "记忆内容超过允许大小。");
  }
  return content;
}

function requireExpectedVersion(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new PublicApiError(400, "invalid_request", "expectedVersion 必须为 JSON 安全正整数。");
  }
  return value;
}

function normalizeRequestId(value: unknown): string {
  if (typeof value === "string") {
    const normalized = value.trim();
    if (/^[a-zA-Z0-9_.:-]{1,160}$/.test(normalized)) return normalized;
  }
  return `ai-memory-request-${crypto.randomUUID()}`;
}

const SECRET_ASSIGNMENT_PATTERN = /(?:password|passwd|pwd|api[ _-]?key|access[ _-]?token|refresh[ _-]?token|authorization|cookie|client[ _-]?secret|webhook|密码|口令|密钥|令牌|访问令牌|刷新令牌|授权头|浏览器凭据|机器人地址)\s*(?:=|:|：)\s*\S+/iu;
const SECRET_VALUE_PATTERN = /(?:\bBearer\s+[A-Za-z0-9._~+\/-]{12,}|\bsk-[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}|https?:\/\/[^\s]+(?:token|webhook|secret)[^\s]*)/iu;
const RAW_CUSTOMER_DATA_PATTERN = /(?:客户聊天原文|完整聊天记录|原始客服会话|客户手机号|客户电话|收货地址|身份证号|银行卡号)/iu;
const PROMPT_OVERRIDE_PATTERN = /(?:忽略|覆盖|绕过|取消|跳过).{0,24}(?:系统提示|既有指令|安全规则|权限|数据范围|人工确认|审计|工具限制)|(?:ignore|override|bypass|disable|skip).{0,48}(?:system prompt|previous instructions?|security|permission|scope|confirmation|audit|tool restriction)/iu;
const TIME_BOUND_PATTERN = /(?:20\d{2}[-/.年](?:0?[1-9]|1[0-2])(?:[-/.月](?:0?[1-9]|[12]\d|3[01])日?)?|今天|今日|昨天|昨日|本周|上周|本月|上月|截至|截止|as of|today|yesterday|this (?:week|month)|last (?:week|month))/iu;
const METRIC_PATTERN = /(?:gmv|销售额|销售量|销量|订单量|订单数|库存量|库存|金额|收入|利润|毛利率|转化率|访客数|成交量|\d+(?:\.\d+)?\s*(?:%|元|万元|件|单))/iu;

function enforceContentGate(key: string, content: string): void {
  const candidate = `${key}\n${content}`;
  if (SECRET_ASSIGNMENT_PATTERN.test(candidate) || SECRET_VALUE_PATTERN.test(candidate)) {
    throw new PublicApiError(400, "invalid_request", "记忆内容可能包含密钥、令牌或登录凭据，已拒绝保存。");
  }
  if (RAW_CUSTOMER_DATA_PATTERN.test(candidate)) {
    throw new PublicApiError(400, "invalid_request", "记忆内容不得保存原始客户会话或客户敏感信息。");
  }
  if (PROMPT_OVERRIDE_PATTERN.test(candidate)) {
    throw new PublicApiError(400, "invalid_request", "记忆内容不得包含改变系统权限或安全规则的指令。");
  }
  if (TIME_BOUND_PATTERN.test(candidate) && METRIC_PATTERN.test(candidate)) {
    throw new PublicApiError(400, "invalid_request", "带时间边界的经营指标应实时查询，不得固化为全局记忆。");
  }
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function scopeForKind(kind: AiMemoryKind, principal: AppPrincipal): {
  mode: AiMemoryScopeMode;
  json: string;
} {
  if (kind === "business_context") {
    return { mode: "data_scope", json: serializeAiConversationScope(principal.scope) };
  }
  return { mode: "owner", json: "null" };
}

async function prepareNewMemory(
  input: { kind: unknown; key: unknown; content: unknown },
  principal: AppPrincipal,
): Promise<PreparedMemory> {
  const kind = requireKind(input.kind);
  const { key, normalized: keyNormalized } = normalizeMemoryKey(input.key);
  const content = normalizeMemoryContent(input.content);
  enforceContentGate(key, content);
  const scope = scopeForKind(kind, principal);
  const scopeDigest = scope.mode === "owner" ? "owner:v1" : await sha256(scope.json);
  const contentDigest = await sha256(JSON.stringify({
    kind,
    key: keyNormalized,
    content,
    scopeMode: scope.mode,
    scopeJson: scope.json,
  }));
  return {
    kind,
    key,
    keyNormalized,
    content,
    contentDigest,
    scopeMode: scope.mode,
    scopeJson: scope.json,
    scopeDigest,
  };
}

async function prepareUpdatedMemory(
  row: AiMemoryRow,
  input: { key?: unknown; content?: unknown },
): Promise<PreparedMemory> {
  const { key, normalized: keyNormalized } = normalizeMemoryKey(
    input.key === undefined ? row.memory_key : input.key,
  );
  const content = normalizeMemoryContent(
    input.content === undefined ? row.content : input.content,
  );
  enforceContentGate(key, content);
  const kind = requireKind(row.kind);
  const scopeMode = row.scope_mode as AiMemoryScopeMode;
  const contentDigest = await sha256(JSON.stringify({
    kind,
    key: keyNormalized,
    content,
    scopeMode,
    scopeJson: row.scope_json,
  }));
  return {
    kind,
    key,
    keyNormalized,
    content,
    contentDigest,
    scopeMode,
    scopeJson: row.scope_json,
    scopeDigest: row.scope_digest,
  };
}

function mapMemory(row: AiMemoryRow): AiMemoryItem {
  return {
    id: row.id,
    kind: requireKind(row.kind),
    key: row.memory_key,
    content: row.content,
    scopeMode: row.scope_mode as AiMemoryScopeMode,
    status: row.status as AiMemoryStatus,
    version: Number(row.version),
    source: row.source as AiMemoryItem["source"],
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    archivedAt: row.archived_at,
  };
}

function activeAccessSql(
  principal: AppPrincipal,
  alias = "m",
): { clause: string; values: unknown[] } {
  const scope = aiScopeSnapshotAccessSql(principal.scope, `${alias}.scope_json`);
  return {
    clause: `${alias}.owner_email = ? COLLATE NOCASE
      AND ${alias}.status = 'active'
      AND (
        ${alias}.scope_mode = 'owner'
        OR (${alias}.scope_mode = 'data_scope'${scope.clause})
      )`,
    values: [normalizeOwnerEmail(principal), ...scope.values],
  };
}

async function findActiveMemory(
  id: string,
  principal: AppPrincipal,
  db: D1Database,
): Promise<AiMemoryRow | null> {
  const access = activeAccessSql(principal);
  return db
    .prepare(`SELECT ${MEMORY_SELECT_COLUMNS} FROM ai_memory_entries m
      WHERE m.id = ? AND ${access.clause} LIMIT 1`)
    .bind(id, ...access.values)
    .first<AiMemoryRow>();
}

async function findExactKey(
  prepared: PreparedMemory,
  principal: AppPrincipal,
  db: D1Database,
  excludedId?: string,
): Promise<AiMemoryRow | null> {
  const exclusion = excludedId ? " AND id <> ?" : "";
  const values: unknown[] = [
    normalizeOwnerEmail(principal),
    prepared.kind,
    prepared.keyNormalized,
    prepared.scopeDigest,
  ];
  if (excludedId) values.push(excludedId);
  return db
    .prepare(`SELECT ${MEMORY_SELECT_COLUMNS} FROM ai_memory_entries
      WHERE owner_email = ? COLLATE NOCASE AND status = 'active'
        AND kind = ? AND memory_key_normalized = ? AND scope_digest = ?${exclusion}
      LIMIT 1`)
    .bind(...values)
    .first<AiMemoryRow>();
}

function similarityText(key: string, content: string): string {
  return `${key}\n${content}`
    .normalize("NFKC")
    .toLocaleLowerCase("zh-CN")
    .replace(/[\p{P}\p{S}\s]+/gu, "");
}

function ngrams(value: string): Set<string> {
  if (value.length < 2) return new Set(value ? [value] : []);
  const result = new Set<string>();
  for (let index = 0; index < value.length - 1; index += 1) {
    result.add(value.slice(index, index + 2));
  }
  return result;
}

function jaccard(left: Set<string>, right: Set<string>): number {
  if (left.size === 0 || right.size === 0) return 0;
  let intersection = 0;
  for (const item of left) {
    if (right.has(item)) intersection += 1;
  }
  return intersection / (left.size + right.size - intersection);
}

async function enforceSimilarityGate(
  prepared: PreparedMemory,
  principal: AppPrincipal,
  db: D1Database,
  excludedId?: string,
): Promise<void> {
  const access = activeAccessSql(principal);
  const exclusion = excludedId ? " AND m.id <> ?" : "";
  const values: unknown[] = [...access.values, prepared.kind, prepared.scopeDigest];
  if (excludedId) values.push(excludedId);
  values.push(AI_MEMORY_LIMITS.similarityCandidates);
  const result = await db
    .prepare(`SELECT id, memory_key, content FROM ai_memory_entries m
      WHERE ${access.clause} AND m.kind = ? AND m.scope_digest = ?${exclusion}
      ORDER BY m.updated_at DESC, m.id DESC LIMIT ?`)
    .bind(...values)
    .all<Pick<AiMemoryRow, "id" | "memory_key" | "content">>();
  const candidate = ngrams(similarityText(prepared.key, prepared.content));
  const candidateContent = ngrams(similarityText("", prepared.content));
  let highest = 0;
  for (const row of result.results ?? []) {
    highest = Math.max(
      highest,
      jaccard(candidate, ngrams(similarityText(row.memory_key, row.content))),
      jaccard(candidateContent, ngrams(similarityText("", row.content))),
    );
  }
  if (highest >= 0.72) {
    const level = highest >= 0.92 ? "高度相似" : "可能重复";
    throw new PublicApiError(
      409,
      "conflict",
      `检测到${level}的既有记忆，请编辑或合并原记录。`,
    );
  }
}

function gates(l2ExactKey: MemoryGateResults["l2ExactKey"], l3Similarity: MemoryGateResults["l3Similarity"]): MemoryGateResults {
  return {
    policyVersion: AI_MEMORY_POLICY_VERSION,
    l0Source: "passed",
    l1Content: "passed",
    l2ExactKey,
    l3Similarity,
  };
}

type AuditInput = {
  operationId: string;
  requestId: string;
  memoryId: string;
  principal: AppPrincipal;
  operation: "create" | "update" | "archive" | "duplicate";
  status: "succeeded" | "duplicate";
  scopeDigest: string;
  beforeDigest: string | null;
  afterDigest: string | null;
  resultVersion: number;
  gateResults: MemoryGateResults;
};

function auditInsertStatement(
  db: D1Database,
  input: AuditInput,
  conditional: { sql: string; values: unknown[] } = { sql: "", values: [] },
) {
  return db
    .prepare(`INSERT INTO ai_memory_audit_logs (
      id, operation_id, request_id, memory_id, owner_email, actor_role,
      operation, status, scope_digest, before_digest, after_digest,
      result_version, policy_version, gate_results_json
    ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?${conditional.sql}`)
    .bind(
      `ai-memory-audit-${crypto.randomUUID()}`,
      input.operationId,
      input.requestId,
      input.memoryId,
      normalizeOwnerEmail(input.principal),
      input.principal.role,
      input.operation,
      input.status,
      input.scopeDigest,
      input.beforeDigest,
      input.afterDigest,
      input.resultVersion,
      AI_MEMORY_POLICY_VERSION,
      JSON.stringify(input.gateResults),
      ...conditional.values,
    );
}

function auditGuardStatements(db: D1Database, operationId: string) {
  return [
    db.prepare(`INSERT INTO ai_memory_commit_guards (operation_id, audit_present)
      VALUES (?, CASE WHEN EXISTS (
        SELECT 1 FROM ai_memory_audit_logs WHERE operation_id = ?
      ) THEN 1 ELSE 0 END)`).bind(operationId, operationId),
    db.prepare("DELETE FROM ai_memory_commit_guards WHERE operation_id = ?").bind(operationId),
  ];
}

async function auditDuplicate(
  row: AiMemoryRow,
  principal: AppPrincipal,
  requestId: string,
  db: D1Database,
): Promise<void> {
  const operationId = `ai-memory-operation-${crypto.randomUUID()}`;
  const audit = auditInsertStatement(db, {
    operationId,
    requestId,
    memoryId: row.id,
    principal,
    operation: "duplicate",
    status: "duplicate",
    scopeDigest: row.scope_digest,
    beforeDigest: row.content_digest,
    afterDigest: row.content_digest,
    resultVersion: Number(row.version),
    gateResults: gates("duplicate", "not_run"),
  });
  try {
    await db.batch([audit, ...auditGuardStatements(db, operationId)]);
  } catch {
    throw new PublicApiError(503, "service_unavailable", "记忆审计不可用，操作未完成。");
  }
}

export async function createAiMemory(
  input: {
    confirmed: unknown;
    kind: unknown;
    key: unknown;
    content: unknown;
    surface?: unknown;
    requestId?: unknown;
  },
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<{ item: AiMemoryItem; created: boolean; duplicate: boolean }> {
  requireMemoryWriter(principal);
  requireConfirmed(input.confirmed);
  requireManagementSurface(input.surface);
  const prepared = await prepareNewMemory(input, principal);
  const requestId = normalizeRequestId(input.requestId);
  await ensureAiMemorySchema(db);

  const exact = await findExactKey(prepared, principal, db);
  if (exact) {
    if (exact.content_digest !== prepared.contentDigest) {
      throw new PublicApiError(409, "conflict", "同一记忆键已存在，请使用版本号编辑原记录。");
    }
    await auditDuplicate(exact, principal, requestId, db);
    return { item: mapMemory(exact), created: false, duplicate: true };
  }
  await enforceSimilarityGate(prepared, principal, db);

  const memoryId = `ai-memory-${crypto.randomUUID()}`;
  const operationId = `ai-memory-operation-${crypto.randomUUID()}`;
  const ownerEmail = normalizeOwnerEmail(principal);
  const audit = auditInsertStatement(db, {
    operationId,
    requestId,
    memoryId,
    principal,
    operation: "create",
    status: "succeeded",
    scopeDigest: prepared.scopeDigest,
    beforeDigest: null,
    afterDigest: prepared.contentDigest,
    resultVersion: 1,
    gateResults: gates("unique", "low"),
  }, {
    sql: ` FROM ai_memory_entries m
      WHERE m.id = ? AND m.owner_email = ? COLLATE NOCASE AND m.last_operation_id = ?`,
    values: [memoryId, ownerEmail, operationId],
  });
  try {
    await db.batch([
      db.prepare(`INSERT INTO ai_memory_entries (
        id, owner_email, kind, memory_key, memory_key_normalized, content,
        content_digest, scope_mode, scope_json, scope_digest, status, version,
        source, last_operation_id
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, 'management_ui', ?)`)
        .bind(
          memoryId,
          ownerEmail,
          prepared.kind,
          prepared.key,
          prepared.keyNormalized,
          prepared.content,
          prepared.contentDigest,
          prepared.scopeMode,
          prepared.scopeJson,
          prepared.scopeDigest,
          operationId,
        ),
      audit,
      ...auditGuardStatements(db, operationId),
    ]);
  } catch {
    const concurrent = await findExactKey(prepared, principal, db).catch(() => null);
    if (concurrent) {
      if (concurrent.last_operation_id === operationId) {
        return { item: mapMemory(concurrent), created: true, duplicate: false };
      }
      if (concurrent.content_digest !== prepared.contentDigest) {
        throw new PublicApiError(409, "conflict", "同一记忆键已存在，请使用版本号编辑原记录。");
      }
      await auditDuplicate(concurrent, principal, requestId, db);
      return { item: mapMemory(concurrent), created: false, duplicate: true };
    }
    throw new PublicApiError(503, "service_unavailable", "记忆审计不可用，操作未完成。");
  }
  const created = await findActiveMemory(memoryId, principal, db);
  if (!created) {
    throw new PublicApiError(503, "service_unavailable", "记忆写入后回查失败。");
  }
  return { item: mapMemory(created), created: true, duplicate: false };
}

export async function getAiMemory(
  memoryId: string,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiMemoryItem> {
  await ensureAiMemorySchema(db);
  const row = await findActiveMemory(memoryId, principal, db);
  if (!row) throw new PublicApiError(404, "not_found", "记忆不存在。");
  return mapMemory(row);
}

function requirePageValue(value: unknown, fallback: number, maximum: number, field: string): number {
  const resolved = value === undefined ? fallback : value;
  if (typeof resolved !== "number" || !Number.isSafeInteger(resolved) || resolved < 1 || resolved > maximum) {
    throw new PublicApiError(400, "invalid_request", `${field}超出允许范围。`);
  }
  return resolved;
}

function optionalQuery(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string") {
    throw new PublicApiError(400, "invalid_request", "q 必须为字符串。");
  }
  const query = value.normalize("NFKC").trim().replace(/\s+/gu, " ");
  if (!query) return null;
  if (query.length > AI_MEMORY_LIMITS.queryCharacters || utf8Bytes(query) > AI_MEMORY_LIMITS.queryCharacters * 4) {
    throw new PublicApiError(413, "payload_too_large", "q 超过允许大小。");
  }
  return query;
}

export async function listAiMemories(
  input: { page?: unknown; pageSize?: unknown; q?: unknown; kind?: unknown } = {},
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiMemoryPage> {
  const page = requirePageValue(input.page, 1, AI_MEMORY_LIMITS.page, "page");
  const pageSize = requirePageValue(input.pageSize, 20, AI_MEMORY_LIMITS.pageSize, "pageSize");
  const query = optionalQuery(input.q);
  const kind = input.kind === undefined || input.kind === null || input.kind === ""
    ? null
    : requireKind(input.kind);
  await ensureAiMemorySchema(db);
  const access = activeAccessSql(principal);
  const clauses = [access.clause];
  const values: unknown[] = [...access.values];
  if (kind) {
    clauses.push("m.kind = ?");
    values.push(kind);
  }
  if (query) {
    clauses.push("(instr(lower(m.memory_key), lower(?)) > 0 OR instr(lower(m.content), lower(?)) > 0)");
    values.push(query, query);
  }
  const where = clauses.join(" AND ");
  const offset = (page - 1) * pageSize;
  const [rows, count] = await Promise.all([
    db.prepare(`SELECT ${MEMORY_SELECT_COLUMNS} FROM ai_memory_entries m
      WHERE ${where} ORDER BY m.updated_at DESC, m.id DESC LIMIT ? OFFSET ?`)
      .bind(...values, pageSize, offset)
      .all<AiMemoryRow>(),
    db.prepare(`SELECT COUNT(*) AS total FROM ai_memory_entries m WHERE ${where}`)
      .bind(...values)
      .first<{ total: number }>(),
  ]);
  const items = (rows.results ?? []).map(mapMemory);
  const total = Number(count?.total ?? 0);
  return {
    items,
    pagination: {
      page,
      pageSize,
      total,
      returned: items.length,
      truncated: offset + items.length < total,
    },
  };
}

export async function updateAiMemory(
  memoryId: string,
  input: {
    confirmed: unknown;
    expectedVersion: unknown;
    key?: unknown;
    content?: unknown;
    surface?: unknown;
    requestId?: unknown;
  },
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<{ item: AiMemoryItem; updated: boolean; duplicate: boolean }> {
  requireMemoryWriter(principal);
  requireConfirmed(input.confirmed);
  requireManagementSurface(input.surface);
  const expectedVersion = requireExpectedVersion(input.expectedVersion);
  if (input.key === undefined && input.content === undefined) {
    throw new PublicApiError(400, "invalid_request", "至少提供 key 或 content 中的一项。");
  }
  await ensureAiMemorySchema(db);
  const before = await findActiveMemory(memoryId, principal, db);
  if (!before) throw new PublicApiError(404, "not_found", "记忆不存在。");
  if (Number(before.version) !== expectedVersion) {
    throw new PublicApiError(409, "version_conflict", "记忆版本已变化，请刷新后重试。");
  }
  const prepared = await prepareUpdatedMemory(before, input);
  const requestId = normalizeRequestId(input.requestId);
  if (prepared.contentDigest === before.content_digest) {
    await auditDuplicate(before, principal, requestId, db);
    return { item: mapMemory(before), updated: false, duplicate: true };
  }
  const exact = await findExactKey(prepared, principal, db, memoryId);
  if (exact) {
    throw new PublicApiError(409, "conflict", "同一记忆键已存在，请编辑或合并原记录。");
  }
  await enforceSimilarityGate(prepared, principal, db, memoryId);

  const operationId = `ai-memory-operation-${crypto.randomUUID()}`;
  const newVersion = expectedVersion + 1;
  const access = activeAccessSql(principal);
  const audit = auditInsertStatement(db, {
    operationId,
    requestId,
    memoryId,
    principal,
    operation: "update",
    status: "succeeded",
    scopeDigest: before.scope_digest,
    beforeDigest: before.content_digest,
    afterDigest: prepared.contentDigest,
    resultVersion: newVersion,
    gateResults: gates("unique", "low"),
  }, {
    sql: ` FROM ai_memory_entries m
      WHERE m.id = ? AND m.owner_email = ? COLLATE NOCASE
        AND m.last_operation_id = ? AND m.version = ?`,
    values: [memoryId, normalizeOwnerEmail(principal), operationId, newVersion],
  });
  try {
    await db.batch([
      db.prepare(`UPDATE ai_memory_entries AS m SET
        memory_key = ?, memory_key_normalized = ?, content = ?, content_digest = ?,
        version = version + 1, last_operation_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE m.id = ? AND m.version = ? AND ${access.clause}`)
        .bind(
          prepared.key,
          prepared.keyNormalized,
          prepared.content,
          prepared.contentDigest,
          operationId,
          memoryId,
          expectedVersion,
          ...access.values,
        ),
      audit,
      ...auditGuardStatements(db, operationId),
    ]);
  } catch {
    const current = await findActiveMemory(memoryId, principal, db).catch(() => null);
    if (!current) throw new PublicApiError(404, "not_found", "记忆不存在。");
    if (current.last_operation_id === operationId && Number(current.version) === newVersion) {
      return { item: mapMemory(current), updated: true, duplicate: false };
    }
    if (Number(current.version) !== expectedVersion) {
      throw new PublicApiError(409, "version_conflict", "记忆版本已变化，请刷新后重试。");
    }
    const conflicting = await findExactKey(prepared, principal, db, memoryId).catch(() => null);
    if (conflicting) {
      throw new PublicApiError(409, "conflict", "同一记忆键已存在，请编辑或合并原记录。");
    }
    throw new PublicApiError(503, "service_unavailable", "记忆审计不可用，操作未完成。");
  }
  const updated = await findActiveMemory(memoryId, principal, db);
  if (!updated || Number(updated.version) !== newVersion) {
    throw new PublicApiError(503, "service_unavailable", "记忆更新后回查失败。");
  }
  return { item: mapMemory(updated), updated: true, duplicate: false };
}

export async function archiveAiMemory(
  memoryId: string,
  input: {
    confirmed: unknown;
    expectedVersion: unknown;
    surface?: unknown;
    requestId?: unknown;
  },
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<{ id: string; archived: true; version: number }> {
  requireMemoryWriter(principal);
  requireConfirmed(input.confirmed);
  requireManagementSurface(input.surface);
  const expectedVersion = requireExpectedVersion(input.expectedVersion);
  await ensureAiMemorySchema(db);
  const before = await findActiveMemory(memoryId, principal, db);
  if (!before) throw new PublicApiError(404, "not_found", "记忆不存在。");
  if (Number(before.version) !== expectedVersion) {
    throw new PublicApiError(409, "version_conflict", "记忆版本已变化，请刷新后重试。");
  }

  const operationId = `ai-memory-operation-${crypto.randomUUID()}`;
  const requestId = normalizeRequestId(input.requestId);
  const newVersion = expectedVersion + 1;
  const access = activeAccessSql(principal);
  const audit = auditInsertStatement(db, {
    operationId,
    requestId,
    memoryId,
    principal,
    operation: "archive",
    status: "succeeded",
    scopeDigest: before.scope_digest,
    beforeDigest: before.content_digest,
    afterDigest: null,
    resultVersion: newVersion,
    gateResults: gates("same_record", "not_run"),
  }, {
    sql: ` FROM ai_memory_entries m
      WHERE m.id = ? AND m.owner_email = ? COLLATE NOCASE
        AND m.last_operation_id = ? AND m.status = 'archived' AND m.version = ?`,
    values: [memoryId, normalizeOwnerEmail(principal), operationId, newVersion],
  });
  try {
    await db.batch([
      db.prepare(`UPDATE ai_memory_entries AS m SET status = 'archived', archived_at = CURRENT_TIMESTAMP,
        version = version + 1, last_operation_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE m.id = ? AND m.version = ? AND ${access.clause}`)
        .bind(operationId, memoryId, expectedVersion, ...access.values),
      audit,
      ...auditGuardStatements(db, operationId),
    ]);
  } catch {
    const current = await findActiveMemory(memoryId, principal, db).catch(() => null);
    if (!current) {
      const ownerRow = await db.prepare(`SELECT version, status, last_operation_id FROM ai_memory_entries
        WHERE id = ? AND owner_email = ? COLLATE NOCASE LIMIT 1`)
        .bind(memoryId, normalizeOwnerEmail(principal))
        .first<{ version: number; status: string; last_operation_id: string }>()
        .catch(() => null);
      if (ownerRow?.status === "archived"
        && Number(ownerRow.version) === newVersion
        && ownerRow.last_operation_id === operationId) {
        return { id: memoryId, archived: true, version: newVersion };
      }
      if (ownerRow && Number(ownerRow.version) !== expectedVersion) {
        throw new PublicApiError(409, "version_conflict", "记忆版本已变化，请刷新后重试。");
      }
      throw new PublicApiError(404, "not_found", "记忆不存在。");
    }
    if (Number(current.version) !== expectedVersion) {
      throw new PublicApiError(409, "version_conflict", "记忆版本已变化，请刷新后重试。");
    }
    throw new PublicApiError(503, "service_unavailable", "记忆审计不可用，操作未完成。");
  }
  return { id: memoryId, archived: true, version: newVersion };
}

export async function retrieveAiMemoriesForContext(
  queryValue: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiMemoryContextResult> {
  const query = optionalQuery(queryValue);
  if (!query) {
    return {
      trust: "untrusted_memory_data",
      items: [],
      totalMatched: 0,
      returned: 0,
      truncated: false,
    };
  }
  const page = await listAiMemories({ page: 1, pageSize: AI_MEMORY_LIMITS.retrievalItems, q: query }, principal, db);
  const items: AiMemoryContextResult["items"] = [];
  let characters = 0;
  for (const item of page.items) {
    const length = item.key.length + item.content.length;
    if (characters + length > AI_MEMORY_LIMITS.retrievalCharacters) break;
    characters += length;
    items.push({
      id: item.id,
      kind: item.kind,
      key: item.key,
      content: item.content,
      version: item.version,
      updatedAt: item.updatedAt,
    });
  }
  return {
    trust: "untrusted_memory_data",
    items,
    totalMatched: page.pagination.total,
    returned: items.length,
    truncated: items.length < page.pagination.total,
  };
}
