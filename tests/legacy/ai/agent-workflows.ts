import type { AppPrincipal } from "@/tests/legacy/auth/authorization";
import {
  aiScopeSnapshotAccessSql,
  serializeAiConversationScope,
} from "@/tests/legacy/ai/conversation-scope";
import { ensureAiAgentWorkflowSchema } from "@/tests/legacy/ai/agent-workflow-schema";
import { PublicApiError } from "@/lib/http/api-error";
import { getD1Database, type D1Database } from "@/lib/database/d1";

export const AI_AGENT_WORKFLOW_LIMITS = {
  maximumActiveAgentJobsPerOwner: 8,
  maximumActiveAgentJobsGlobal: 64,
  maximumActiveWorkflowRunsPerOwner: 4,
  maximumActiveWorkflowRunsGlobal: 24,
  maximumTaskCharacters: 8_000,
  maximumInstructionCharacters: 4_000,
  maximumInputBytes: 24 * 1024,
  maximumStateBytes: 24 * 1024,
  maximumOutputBytes: 48 * 1024,
  maximumWorkflowOutputBytes: 96 * 1024,
  maximumGraphBytes: 48 * 1024,
  maximumWorkflowNodes: 24,
  maximumDependenciesPerNode: 8,
  maximumGraphDepth: 16,
  maximumAllowedTools: 64,
  maximumMicrosteps: 64,
  maximumResumeCount: 16,
  leaseSeconds: 240,
  defaultMicrostepTimeoutMs: 15_000,
} as const;

export type AiPassiveJson =
  | null
  | boolean
  | number
  | string
  | AiPassiveJson[]
  | { [key: string]: AiPassiveJson };

export type ExecutorAdmission = Readonly<{
  modelId: string;
  modelVersion: number;
  allowedTools: readonly string[];
  toolPolicyDigest: string;
}>;

const issuedExecutorAdmissions = new WeakSet<object>();

export type AiAgentJobStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type AiAgentCheckpoint = {
  ordinal: number;
  kind: "checkpoint" | "completed" | "paused" | "failed";
  state: AiPassiveJson;
  outputDigest: string;
  createdAt: string;
};

export type AiAgentJob = {
  id: string;
  clientRequestId: string;
  task: string;
  input: AiPassiveJson;
  state: AiPassiveJson;
  output: AiPassiveJson | null;
  status: AiAgentJobStatus;
  phase: "queued" | "executing" | "paused" | "completed" | "failed" | "cancelled";
  stepIndex: number;
  version: number;
  retryable: boolean;
  resumeCount: number;
  attemptCount: number;
  modelId: string;
  modelVersion: number;
  allowedTools: string[];
  toolPolicyDigest: string;
  providerRoundCount: number;
  toolCallCount: number;
  providerDispatchStartedAt: string | null;
  workflowRunId: string | null;
  workflowNodeKey: string | null;
  errorCode: string;
  errorMessage: string;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AiAgentJobDetail = AiAgentJob & {
  checkpoints: AiAgentCheckpoint[];
};

export type AiWorkflowNodeType = "agent" | "human_review";
export type AiWorkflowNodeStatus =
  | "pending"
  | "running"
  | "waiting_review"
  | "completed"
  | "rejected"
  | "skipped"
  | "failed"
  | "cancelled";
export type AiWorkflowRunStatus =
  | "queued"
  | "running"
  | "waiting_review"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type AiWorkflowNodeDefinition = {
  key: string;
  type: AiWorkflowNodeType;
  dependsOn: string[];
  instruction: string;
};

export type AiWorkflowGraph = { nodes: AiWorkflowNodeDefinition[] };

export type AiWorkflowNodeRun = AiWorkflowNodeDefinition & {
  id: string;
  position: number;
  input: AiPassiveJson;
  output: AiPassiveJson | null;
  status: AiWorkflowNodeStatus;
  version: number;
  agentJobId: string | null;
  reviewerEmail: string | null;
  reviewedAt: string | null;
  errorCode: string;
  errorMessage: string;
  startedAt: string | null;
  completedAt: string | null;
};

export type AiWorkflowRun = {
  id: string;
  clientRequestId: string;
  name: string;
  graph: AiWorkflowGraph;
  input: AiPassiveJson;
  output: AiPassiveJson | null;
  dryRun: boolean;
  status: AiWorkflowRunStatus;
  currentNodeKey: string | null;
  version: number;
  retryable: boolean;
  resumeCount: number;
  attemptCount: number;
  modelId: string;
  modelVersion: number;
  allowedTools: string[];
  toolPolicyDigest: string;
  providerRoundCount: number;
  toolCallCount: number;
  providerDispatchStartedAt: string | null;
  errorCode: string;
  errorMessage: string;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AiWorkflowRunDetail = AiWorkflowRun & { nodes: AiWorkflowNodeRun[] };

type AgentJobRow = {
  id: string;
  owner_email: string;
  client_request_id: string;
  request_digest: string;
  scope_json: string;
  task: string;
  input_json: string;
  state_json: string;
  output_json: string | null;
  model_id: string;
  model_version: number;
  allowed_tools_json: string;
  tool_policy_digest: string;
  provider_round_count: number;
  tool_call_count: number;
  provider_dispatch_started_at: string | null;
  status: string;
  phase: string;
  step_index: number;
  version: number;
  mutation_token: string;
  cancel_requested: number;
  retryable: number;
  resume_count: number;
  attempt_count: number;
  lease_token: string;
  lease_epoch: number;
  lease_expires_at: string | null;
  next_run_at: string;
  workflow_run_id: string | null;
  workflow_node_key: string | null;
  error_code: string;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

type AgentCheckpointRow = {
  ordinal: number;
  kind: AiAgentCheckpoint["kind"];
  state_json: string;
  output_digest: string;
  created_at: string;
};

type WorkflowRunRow = {
  id: string;
  owner_email: string;
  client_request_id: string;
  request_digest: string;
  scope_json: string;
  name: string;
  graph_json: string;
  graph_digest: string;
  input_json: string;
  output_json: string | null;
  model_id: string;
  model_version: number;
  allowed_tools_json: string;
  tool_policy_digest: string;
  provider_round_count: number;
  tool_call_count: number;
  provider_dispatch_started_at: string | null;
  dry_run: number;
  status: string;
  current_node_key: string | null;
  version: number;
  mutation_token: string;
  cancel_requested: number;
  retryable: number;
  resume_count: number;
  attempt_count: number;
  lease_token: string;
  lease_epoch: number;
  lease_expires_at: string | null;
  next_run_at: string;
  error_code: string;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

type WorkflowNodeRow = {
  id: string;
  run_id: string;
  node_key: string;
  position: number;
  node_type: string;
  depends_on_json: string;
  instruction: string;
  input_json: string;
  output_json: string | null;
  status: string;
  version: number;
  mutation_token: string;
  agent_job_id: string | null;
  reviewer_email: string | null;
  reviewed_at: string | null;
  error_code: string;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

const AGENT_COLUMNS = `id, owner_email, client_request_id, request_digest, scope_json, task,
  input_json, state_json, output_json, model_id, model_version, allowed_tools_json,
  tool_policy_digest, provider_round_count, tool_call_count, provider_dispatch_started_at,
  status, phase, step_index, version, mutation_token,
  cancel_requested, retryable, resume_count, attempt_count, lease_token, lease_epoch,
  lease_expires_at, next_run_at, workflow_run_id, workflow_node_key,
  error_code, error_message, started_at, completed_at, created_at, updated_at`;
const WORKFLOW_COLUMNS = `id, owner_email, client_request_id, request_digest, scope_json, name,
  graph_json, graph_digest, input_json, output_json, model_id, model_version, allowed_tools_json,
  tool_policy_digest, provider_round_count, tool_call_count, provider_dispatch_started_at,
  dry_run, status, current_node_key,
  version, mutation_token, cancel_requested, retryable, resume_count, attempt_count,
  lease_token, lease_epoch, lease_expires_at, next_run_at, error_code, error_message,
  started_at, completed_at, created_at, updated_at`;
const NODE_COLUMNS = `id, run_id, node_key, position, node_type, depends_on_json, instruction,
  input_json, output_json, status, version, mutation_token, agent_job_id, reviewer_email,
  reviewed_at, error_code, error_message, started_at, completed_at, created_at, updated_at`;

function changes(result: unknown): number {
  if (!result || typeof result !== "object") return 0;
  const meta = (result as { meta?: { changes?: unknown } }).meta;
  return typeof meta?.changes === "number" ? meta.changes : 0;
}

function exactRecord(value: unknown, allowedKeys: readonly string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicApiError(400, "invalid_request", `${label}必须为对象。`);
  }
  const record = value as Record<string, unknown>;
  const allowed = new Set(allowedKeys);
  const unknown = Object.keys(record).filter((key) => !allowed.has(key));
  if (unknown.length > 0) {
    throw new PublicApiError(400, "invalid_request", `${label}包含未允许字段：${unknown.join("、")}。`);
  }
  return record;
}

function boundedText(
  value: unknown,
  field: string,
  maximumCharacters: number,
  allowEmpty = false,
): string {
  if (typeof value !== "string") {
    throw new PublicApiError(400, "invalid_request", `${field}必须为字符串。`);
  }
  const normalized = value.trim();
  if (!allowEmpty && !normalized) {
    throw new PublicApiError(400, "invalid_request", `${field}不能为空。`);
  }
  if (normalized.includes("\u0000") || Array.from(normalized).length > maximumCharacters) {
    throw new PublicApiError(413, "payload_too_large", `${field}超过允许大小。`);
  }
  return normalized;
}

function safeId(value: unknown, field: string, maximum = 160): string {
  if (typeof value !== "string" || value.length > maximum || !/^[a-zA-Z0-9_-]+$/.test(value)) {
    throw new PublicApiError(400, "invalid_request", `${field}格式无效。`);
  }
  return value;
}

/**
 * Issues the immutable execution snapshot consumed by durable Agent creation.
 * The returned object is registered by identity; a structurally similar JSON
 * object supplied by a request is not a valid admission.
 */
export function createExecutorAdmission(inputValue: unknown): ExecutorAdmission {
  const input = exactRecord(
    inputValue,
    ["modelId", "modelVersion", "allowedTools", "toolPolicyDigest"],
    "Executor admission",
  );
  const modelId = boundedText(input.modelId, "modelId", 160);
  if (typeof input.modelVersion !== "number"
    || !Number.isSafeInteger(input.modelVersion)
    || input.modelVersion < 1) {
    throw new PublicApiError(400, "invalid_request", "modelVersion必须为 JSON 安全正整数。");
  }
  if (!Array.isArray(input.allowedTools)
    || input.allowedTools.length > AI_AGENT_WORKFLOW_LIMITS.maximumAllowedTools) {
    throw new PublicApiError(400, "invalid_request", "allowedTools必须为有界工具名称数组。");
  }
  const allowedTools = input.allowedTools.map((tool, index) => {
    if (typeof tool !== "string" || !/^[a-z][a-z0-9_]{0,127}$/.test(tool)) {
      throw new PublicApiError(400, "invalid_request", `allowedTools[${index}]格式无效。`);
    }
    return tool;
  });
  if (new Set(allowedTools).size !== allowedTools.length) {
    throw new PublicApiError(400, "invalid_request", "allowedTools不能包含重复工具。" );
  }
  allowedTools.sort();
  if (typeof input.toolPolicyDigest !== "string"
    || !/^[a-f0-9]{64}$/.test(input.toolPolicyDigest)) {
    throw new PublicApiError(400, "invalid_request", "toolPolicyDigest必须为小写 SHA-256 摘要。" );
  }
  const admission = Object.freeze({
    modelId,
    modelVersion: input.modelVersion,
    allowedTools: Object.freeze(allowedTools),
    toolPolicyDigest: input.toolPolicyDigest,
  });
  issuedExecutorAdmissions.add(admission);
  return admission;
}

function requireExecutorAdmission(value: unknown): ExecutorAdmission {
  if (!value || typeof value !== "object" || !issuedExecutorAdmissions.has(value as object)) {
    throw new PublicApiError(503, "service_unavailable", AI_AGENT_EXECUTOR_UNAVAILABLE_MESSAGE);
  }
  return value as ExecutorAdmission;
}

function parseStoredAllowedTools(value: string): string[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch (error) {
    throw new Error("AI Agent 工具白名单持久化内容无效", { cause: error });
  }
  if (!Array.isArray(parsed)
    || parsed.length > AI_AGENT_WORKFLOW_LIMITS.maximumAllowedTools
    || !parsed.every((tool) => typeof tool === "string" && /^[a-z][a-z0-9_]{0,127}$/.test(tool))
    || new Set(parsed).size !== parsed.length) {
    throw new Error("AI Agent 工具白名单持久化内容无效");
  }
  return [...parsed];
}

function expectedVersion(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new PublicApiError(400, "invalid_request", "expectedVersion必须为 JSON 安全正整数。");
  }
  return value;
}

export function requireAiExpectedVersionBody(value: unknown): number {
  const body = exactRecord(value, ["expectedVersion"], "状态变更请求");
  return expectedVersion(body.expectedVersion);
}

function normalizeOwner(principal: AppPrincipal): string {
  return principal.email.trim().toLowerCase();
}

function requireAgentMutationRole(principal: AppPrincipal): void {
  if (principal.role !== "admin" && principal.role !== "operator" && principal.role !== "analyst") {
    throw new PublicApiError(404, "not_found", "AI 任务不存在或当前身份不可访问。");
  }
}

function safeErrorCode(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim().toLowerCase();
  return /^[a-z][a-z0-9_]{0,63}$/.test(normalized) ? normalized : fallback;
}

function normalizePassiveJson(
  value: unknown,
  label: string,
  maximumBytes: number,
  rejectActiveMarkup = false,
): { value: AiPassiveJson; json: string } {
  let visited = 0;
  const visit = (candidate: unknown, depth: number): AiPassiveJson => {
    visited += 1;
    if (visited > 1_500 || depth > 12) {
      throw new PublicApiError(413, "payload_too_large", `${label}结构过深或项目过多。`);
    }
    if (candidate === null || typeof candidate === "boolean") return candidate;
    if (typeof candidate === "number") {
      if (!Number.isFinite(candidate)) {
        throw new PublicApiError(400, "invalid_request", `${label}不能包含非有限数字。`);
      }
      return candidate;
    }
    if (typeof candidate === "string") {
      if (candidate.includes("\u0000") || Array.from(candidate).length > 12_000) {
        throw new PublicApiError(413, "payload_too_large", `${label}中的字符串超过允许大小。`);
      }
      if (rejectActiveMarkup && (/<\s*(?:script|iframe|object|embed|style|link|meta|html|body)\b/i.test(candidate)
        || /javascript\s*:/i.test(candidate) || /data\s*:\s*text\/html/i.test(candidate))) {
        throw new PublicApiError(422, "invalid_request", `${label}不能包含可执行 HTML 内容。`);
      }
      return candidate;
    }
    if (Array.isArray(candidate)) return candidate.map((item) => visit(item, depth + 1));
    if (!candidate || typeof candidate !== "object") {
      throw new PublicApiError(400, "invalid_request", `${label}只能包含 JSON 值。`);
    }
    const record = candidate as Record<string, unknown>;
    const output: Record<string, AiPassiveJson> = {};
    for (const key of Object.keys(record).sort()) {
      if (!key || key.length > 128 || key === "__proto__" || key === "prototype" || key === "constructor") {
        throw new PublicApiError(400, "invalid_request", `${label}包含不安全字段名。`);
      }
      output[key] = visit(record[key], depth + 1);
    }
    return output;
  };
  const normalized = visit(value, 0);
  const json = JSON.stringify(normalized);
  if (new TextEncoder().encode(json).byteLength > maximumBytes) {
    throw new PublicApiError(413, "payload_too_large", `${label}超过允许大小。`);
  }
  return { value: normalized, json };
}

function parseStoredJson(value: string, label: string): AiPassiveJson {
  try {
    return normalizePassiveJson(JSON.parse(value), label, AI_AGENT_WORKFLOW_LIMITS.maximumWorkflowOutputBytes).value;
  } catch (error) {
    if (error instanceof PublicApiError) throw new Error(`${label}持久化内容无效`, { cause: error });
    throw new Error(`${label}持久化内容无效`, { cause: error });
  }
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function asAgentStatus(value: string): AiAgentJobStatus {
  if (value === "queued" || value === "running" || value === "paused"
    || value === "completed" || value === "failed" || value === "cancelled") return value;
  throw new Error("AI Agent 任务状态无效");
}

function asAgentPhase(value: string): AiAgentJob["phase"] {
  if (value === "queued" || value === "executing" || value === "paused"
    || value === "completed" || value === "failed" || value === "cancelled") return value;
  throw new Error("AI Agent 任务阶段无效");
}

function asWorkflowStatus(value: string): AiWorkflowRunStatus {
  if (value === "queued" || value === "running" || value === "waiting_review"
    || value === "paused" || value === "completed" || value === "failed" || value === "cancelled") return value;
  throw new Error("AI 工作流状态无效");
}

function asNodeStatus(value: string): AiWorkflowNodeStatus {
  if (value === "pending" || value === "running" || value === "waiting_review"
    || value === "completed" || value === "rejected" || value === "skipped"
    || value === "failed" || value === "cancelled") return value;
  throw new Error("AI 工作流节点状态无效");
}

function mapAgentJob(row: AgentJobRow): AiAgentJob {
  return {
    id: row.id,
    clientRequestId: row.client_request_id,
    task: row.task,
    input: parseStoredJson(row.input_json, "AI Agent 输入"),
    state: parseStoredJson(row.state_json, "AI Agent 状态"),
    output: row.output_json === null ? null : parseStoredJson(row.output_json, "AI Agent 输出"),
    status: asAgentStatus(row.status),
    phase: asAgentPhase(row.phase),
    stepIndex: Number(row.step_index),
    version: Number(row.version),
    retryable: Boolean(row.retryable),
    resumeCount: Number(row.resume_count),
    attemptCount: Number(row.attempt_count),
    modelId: row.model_id,
    modelVersion: Number(row.model_version),
    allowedTools: parseStoredAllowedTools(row.allowed_tools_json),
    toolPolicyDigest: row.tool_policy_digest,
    providerRoundCount: Number(row.provider_round_count),
    toolCallCount: Number(row.tool_call_count),
    providerDispatchStartedAt: row.provider_dispatch_started_at,
    workflowRunId: row.workflow_run_id,
    workflowNodeKey: row.workflow_node_key,
    errorCode: row.error_code,
    errorMessage: row.error_message,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapWorkflow(row: WorkflowRunRow): AiWorkflowRun {
  return {
    id: row.id,
    clientRequestId: row.client_request_id,
    name: row.name,
    graph: parseStoredJson(row.graph_json, "AI 工作流图") as unknown as AiWorkflowGraph,
    input: parseStoredJson(row.input_json, "AI 工作流输入"),
    output: row.output_json === null ? null : parseStoredJson(row.output_json, "AI 工作流输出"),
    dryRun: Boolean(row.dry_run),
    status: asWorkflowStatus(row.status),
    currentNodeKey: row.current_node_key,
    version: Number(row.version),
    retryable: Boolean(row.retryable),
    resumeCount: Number(row.resume_count),
    attemptCount: Number(row.attempt_count),
    modelId: row.model_id,
    modelVersion: Number(row.model_version),
    allowedTools: parseStoredAllowedTools(row.allowed_tools_json),
    toolPolicyDigest: row.tool_policy_digest,
    providerRoundCount: Number(row.provider_round_count),
    toolCallCount: Number(row.tool_call_count),
    providerDispatchStartedAt: row.provider_dispatch_started_at,
    errorCode: row.error_code,
    errorMessage: row.error_message,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapNode(row: WorkflowNodeRow): AiWorkflowNodeRun {
  const type = row.node_type;
  if (type !== "agent" && type !== "human_review") throw new Error("AI 工作流节点类型无效");
  const dependencies = parseStoredJson(row.depends_on_json, "AI 工作流依赖");
  if (!Array.isArray(dependencies) || !dependencies.every((item) => typeof item === "string")) {
    throw new Error("AI 工作流依赖持久化内容无效");
  }
  return {
    id: row.id,
    key: row.node_key,
    type,
    dependsOn: dependencies,
    instruction: row.instruction,
    position: Number(row.position),
    input: parseStoredJson(row.input_json, "AI 工作流节点输入"),
    output: row.output_json === null ? null : parseStoredJson(row.output_json, "AI 工作流节点输出"),
    status: asNodeStatus(row.status),
    version: Number(row.version),
    agentJobId: row.agent_job_id,
    reviewerEmail: row.reviewer_email,
    reviewedAt: row.reviewed_at,
    errorCode: row.error_code,
    errorMessage: row.error_message,
    startedAt: row.started_at,
    completedAt: row.completed_at,
  };
}

async function accessibleAgentRow(
  id: string,
  principal: AppPrincipal,
  db: D1Database,
): Promise<AgentJobRow> {
  const access = aiScopeSnapshotAccessSql(principal.scope, "j.scope_json");
  const row = await db.prepare(`SELECT ${AGENT_COLUMNS.split(",").map((column) => `j.${column.trim()}`).join(", ")}
    FROM ai_agent_jobs j WHERE j.id = ? AND j.owner_email = ?${access.clause} LIMIT 1`)
    .bind(id, normalizeOwner(principal), ...access.values).first<AgentJobRow>();
  if (!row) throw new PublicApiError(404, "not_found", "AI Agent 任务不存在或当前数据范围不可访问。");
  return row;
}

async function accessibleWorkflowRow(
  id: string,
  principal: AppPrincipal,
  db: D1Database,
): Promise<WorkflowRunRow> {
  const access = aiScopeSnapshotAccessSql(principal.scope, "w.scope_json");
  const row = await db.prepare(`SELECT ${WORKFLOW_COLUMNS.split(",").map((column) => `w.${column.trim()}`).join(", ")}
    FROM ai_workflow_runs w WHERE w.id = ? AND w.owner_email = ?${access.clause} LIMIT 1`)
    .bind(id, normalizeOwner(principal), ...access.values).first<WorkflowRunRow>();
  if (!row) throw new PublicApiError(404, "not_found", "AI 工作流不存在或当前数据范围不可访问。");
  return row;
}

export async function listAiAgentJobs(
  input: { page?: number; pageSize?: number },
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
) {
  await ensureAiAgentWorkflowSchema(db);
  const page = Number.isSafeInteger(input.page) && Number(input.page) > 0 ? Number(input.page) : 1;
  const pageSize = Number.isSafeInteger(input.pageSize) && Number(input.pageSize) > 0
    ? Math.min(Number(input.pageSize), 50) : 20;
  const owner = normalizeOwner(principal);
  const access = aiScopeSnapshotAccessSql(principal.scope, "j.scope_json");
  const totalRow = await db.prepare(`SELECT COUNT(*) count FROM ai_agent_jobs j
    WHERE j.owner_email = ?${access.clause}`).bind(owner, ...access.values).first<{ count: number }>();
  const rows = await db.prepare(`SELECT ${AGENT_COLUMNS.split(",").map((column) => `j.${column.trim()}`).join(", ")}
    FROM ai_agent_jobs j WHERE j.owner_email = ?${access.clause}
    ORDER BY j.created_at DESC, j.id DESC LIMIT ? OFFSET ?`)
    .bind(owner, ...access.values, pageSize, (page - 1) * pageSize).all<AgentJobRow>();
  const total = Number(totalRow?.count ?? 0);
  return { items: (rows.results ?? []).map(mapAgentJob), page, pageSize, total, returned: rows.results?.length ?? 0, truncated: page * pageSize < total };
}

export async function getAiAgentJob(
  idInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiAgentJobDetail> {
  await ensureAiAgentWorkflowSchema(db);
  const id = safeId(idInput, "jobId");
  const row = await accessibleAgentRow(id, principal, db);
  const checkpoints = await db.prepare(`SELECT ordinal, kind, state_json, output_digest, created_at
    FROM ai_agent_checkpoints WHERE job_id = ? ORDER BY ordinal LIMIT ?`)
    .bind(id, AI_AGENT_WORKFLOW_LIMITS.maximumMicrosteps).all<AgentCheckpointRow>();
  return {
    ...mapAgentJob(row),
    checkpoints: (checkpoints.results ?? []).map((checkpoint) => ({
      ordinal: Number(checkpoint.ordinal),
      kind: checkpoint.kind,
      state: parseStoredJson(checkpoint.state_json, "AI Agent 检查点"),
      outputDigest: checkpoint.output_digest,
      createdAt: checkpoint.created_at,
    })),
  };
}

export async function createAiAgentJob(
  inputValue: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
  options: { executorAdmission?: ExecutorAdmission } = {},
): Promise<{ item: AiAgentJobDetail; replayed: boolean }> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const admission = requireExecutorAdmission(options.executorAdmission);
  const input = exactRecord(inputValue, ["clientRequestId", "task", "input"], "AI Agent 请求");
  const clientRequestId = safeId(input.clientRequestId, "clientRequestId", 128);
  const task = boundedText(input.task, "task", AI_AGENT_WORKFLOW_LIMITS.maximumTaskCharacters);
  const normalizedInput = normalizePassiveJson(input.input ?? {}, "input", AI_AGENT_WORKFLOW_LIMITS.maximumInputBytes);
  const requestDigest = await sha256(JSON.stringify({
    task,
    input: normalizedInput.value,
    modelId: admission.modelId,
    modelVersion: admission.modelVersion,
    allowedTools: admission.allowedTools,
    toolPolicyDigest: admission.toolPolicyDigest,
  }));
  const owner = normalizeOwner(principal);
  const access = aiScopeSnapshotAccessSql(principal.scope, "j.scope_json");
  const replay = await db.prepare(`SELECT ${AGENT_COLUMNS.split(",").map((column) => `j.${column.trim()}`).join(", ")}
    FROM ai_agent_jobs j WHERE j.owner_email = ? AND j.client_request_id = ?${access.clause} LIMIT 1`)
    .bind(owner, clientRequestId, ...access.values).first<AgentJobRow>();
  if (replay) {
    if (replay.request_digest !== requestDigest) {
      throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的 AI Agent 请求。");
    }
    return { item: await getAiAgentJob(replay.id, principal, db), replayed: true };
  }
  const inaccessible = await db.prepare(`SELECT 1 present FROM ai_agent_jobs
    WHERE owner_email = ? AND client_request_id = ? LIMIT 1`)
    .bind(owner, clientRequestId).first<{ present: number }>();
  if (inaccessible) throw new PublicApiError(404, "not_found", "AI Agent 任务不存在或当前数据范围不可访问。");

  const id = `ai-agent-${crypto.randomUUID()}`;
  const mutationToken = crypto.randomUUID();
  const scopeJson = serializeAiConversationScope(principal.scope);
  const eventId = `ai-agent-event-${crypto.randomUUID()}`;
  const writes = await db.batch([
    db.prepare(`INSERT OR IGNORE INTO ai_agent_jobs (
        id, owner_email, client_request_id, request_digest, scope_json, task, input_json,
        model_id, model_version, allowed_tools_json, tool_policy_digest, mutation_token
      ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
      WHERE (SELECT COUNT(*) FROM ai_agent_jobs
        WHERE owner_email = ? AND status IN ('queued','running')) < ?
        AND (SELECT COUNT(*) FROM ai_agent_jobs WHERE status IN ('queued','running')) < ?`)
      .bind(id, owner, clientRequestId, requestDigest, scopeJson, task, normalizedInput.json,
        admission.modelId, admission.modelVersion, JSON.stringify(admission.allowedTools),
        admission.toolPolicyDigest, mutationToken,
        owner, AI_AGENT_WORKFLOW_LIMITS.maximumActiveAgentJobsPerOwner,
        AI_AGENT_WORKFLOW_LIMITS.maximumActiveAgentJobsGlobal),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, ?, 'created', status, version, '{}'
      FROM ai_agent_jobs WHERE id = ? AND mutation_token = ?`)
      .bind(eventId, owner, id, mutationToken),
  ]);
  if (changes(writes[0]) !== 1) {
    const raced = await db.prepare(`SELECT ${AGENT_COLUMNS.split(",").map((column) => `j.${column.trim()}`).join(", ")}
      FROM ai_agent_jobs j WHERE j.owner_email = ? AND j.client_request_id = ?${access.clause} LIMIT 1`)
      .bind(owner, clientRequestId, ...access.values).first<AgentJobRow>();
    if (raced) {
      if (raced.request_digest !== requestDigest) {
        throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的 AI Agent 请求。");
      }
      return { item: await getAiAgentJob(raced.id, principal, db), replayed: true };
    }
    throw new PublicApiError(429, "rate_limited", "当前 AI Agent 活动任务已达上限，请稍后再试。");
  }
  return { item: await getAiAgentJob(id, principal, db), replayed: false };
}

async function versionConflictOrNotFound(
  kind: "agent" | "workflow",
  id: string,
  principal: AppPrincipal,
  db: D1Database,
): Promise<never> {
  if (kind === "agent") await accessibleAgentRow(id, principal, db);
  else await accessibleWorkflowRow(id, principal, db);
  throw new PublicApiError(409, "version_conflict", "任务已被其他执行器或请求更新，请刷新后重试。");
}

export async function cancelAiAgentJob(
  idInput: unknown,
  expectedVersionInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiAgentJobDetail> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const id = safeId(idInput, "jobId");
  const version = expectedVersion(expectedVersionInput);
  const row = await accessibleAgentRow(id, principal, db);
  if (row.workflow_run_id) throw new PublicApiError(409, "conflict", "工作流子任务只能通过所属工作流取消。");
  if (Number(row.version) !== version) return versionConflictOrNotFound("agent", id, principal, db);
  if (row.status === "completed" || row.status === "failed" || row.status === "cancelled") {
    throw new PublicApiError(409, "conflict", "当前 AI Agent 任务状态不能取消。");
  }
  const mutationToken = crypto.randomUUID();
  const nextVersion = version + 1;
  const writes = await db.batch([
    db.prepare(`UPDATE ai_agent_jobs SET status = 'cancelled', phase = 'cancelled',
        cancel_requested = 1, retryable = 0, lease_token = '', lease_expires_at = NULL,
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND owner_email = ? AND version = ?
        AND status IN ('queued','running','paused')`)
      .bind(mutationToken, id, normalizeOwner(principal), version),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, ?, 'cancelled', ?, status, version, '{}'
      FROM ai_agent_jobs WHERE id = ? AND mutation_token = ? AND version = ?`)
      .bind(`ai-agent-event-${crypto.randomUUID()}`, normalizeOwner(principal), row.status, id, mutationToken, nextVersion),
  ]);
  if (changes(writes[0]) !== 1) return versionConflictOrNotFound("agent", id, principal, db);
  return getAiAgentJob(id, principal, db);
}

export async function resumeAiAgentJob(
  idInput: unknown,
  expectedVersionInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiAgentJobDetail> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const id = safeId(idInput, "jobId");
  const version = expectedVersion(expectedVersionInput);
  const row = await accessibleAgentRow(id, principal, db);
  if (row.workflow_run_id) throw new PublicApiError(409, "conflict", "工作流子任务只能通过所属工作流恢复。");
  if (Number(row.version) !== version) return versionConflictOrNotFound("agent", id, principal, db);
  if ((row.status !== "paused" && row.status !== "failed") || !Boolean(row.retryable)) {
    throw new PublicApiError(409, "conflict", "当前 AI Agent 任务不可安全恢复。");
  }
  if (Number(row.resume_count) >= AI_AGENT_WORKFLOW_LIMITS.maximumResumeCount) {
    throw new PublicApiError(409, "conflict", "AI Agent 任务恢复次数已达上限。");
  }
  const mutationToken = crypto.randomUUID();
  const nextVersion = version + 1;
  const writes = await db.batch([
    db.prepare(`UPDATE ai_agent_jobs SET status = 'queued', phase = 'queued',
        cancel_requested = 0, retryable = 0, resume_count = resume_count + 1,
        lease_token = '', lease_expires_at = NULL, provider_dispatch_started_at = NULL,
        next_run_at = CURRENT_TIMESTAMP,
        error_code = '', error_message = '', completed_at = NULL,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND owner_email = ? AND version = ?
        AND status IN ('paused','failed') AND retryable = 1 AND resume_count < ?`)
      .bind(mutationToken, id, normalizeOwner(principal), version, AI_AGENT_WORKFLOW_LIMITS.maximumResumeCount),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, ?, 'resumed', ?, status, version, '{}'
      FROM ai_agent_jobs WHERE id = ? AND mutation_token = ? AND version = ?`)
      .bind(`ai-agent-event-${crypto.randomUUID()}`, normalizeOwner(principal), row.status, id, mutationToken, nextVersion),
  ]);
  if (changes(writes[0]) !== 1) return versionConflictOrNotFound("agent", id, principal, db);
  return getAiAgentJob(id, principal, db);
}

type AiAgentLease = {
  jobId: string;
  leaseToken: string;
  leaseEpoch: number;
};

export type AiAgentMicrostepContext = {
  jobId: string;
  task: string;
  input: AiPassiveJson;
  state: AiPassiveJson;
  stepIndex: number;
  modelId: string;
  modelVersion: number;
  allowedTools: readonly string[];
  toolPolicyDigest: string;
  providerRoundCount: number;
  toolCallCount: number;
  signal: AbortSignal;
};

export type AiAgentMicrostepResult =
  | { kind: "checkpoint"; state: AiPassiveJson }
  | { kind: "complete"; output: AiPassiveJson }
  | { kind: "pause"; state?: AiPassiveJson; code?: string; message?: string }
  | { kind: "fail"; code?: string; message?: string; retryable?: boolean };

export type AiAgentMicrostepExecutor = (
  context: Readonly<AiAgentMicrostepContext>,
) => Promise<AiAgentMicrostepResult> | AiAgentMicrostepResult;

export type AiAgentMicrostepOutcome = {
  status: "idle" | "checkpointed" | "completed" | "paused" | "failed" | "lost";
  jobId?: string;
  stepIndex?: number;
  code?: string;
};

async function acquireAgentLease(db: D1Database): Promise<AiAgentLease | null> {
  await db.prepare(`UPDATE ai_agent_jobs SET status = 'failed', phase = 'failed',
      retryable = 0, lease_token = '', lease_expires_at = NULL,
      error_code = 'provider_dispatch_unknown',
      error_message = '供应商派发后的租约已过期，结果未知；任务已失败关闭且不会自动重排',
      completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
      updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ''
    WHERE model_id = '' AND status = 'running' AND lease_expires_at IS NOT NULL
      AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP AND cancel_requested = 0
      AND provider_dispatch_started_at IS NOT NULL`).run();
  await db.prepare(`UPDATE ai_agent_jobs SET status = 'queued', phase = 'queued',
      lease_token = '', lease_expires_at = NULL, next_run_at = CURRENT_TIMESTAMP,
      error_code = 'lease_expired_requeued',
      error_message = '确定性微步骤租约过期，已由 fencing 安全重新排队',
      updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ''
    WHERE model_id = '' AND status = 'running' AND lease_expires_at IS NOT NULL
      AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP AND cancel_requested = 0
      AND provider_dispatch_started_at IS NULL`).run();

  const leaseToken = crypto.randomUUID();
  const mutationToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_agent_jobs SET status = 'running', phase = 'executing',
        attempt_count = attempt_count + 1, lease_token = ?, lease_epoch = lease_epoch + 1,
        lease_expires_at = datetime('now', '+${AI_AGENT_WORKFLOW_LIMITS.leaseSeconds} seconds'),
        started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = (
        SELECT id FROM ai_agent_jobs
        WHERE status = 'queued' AND cancel_requested = 0 AND model_id = ''
          AND datetime(next_run_at) <= CURRENT_TIMESTAMP
        ORDER BY created_at, id LIMIT 1
      ) AND status = 'queued' AND cancel_requested = 0`)
      .bind(leaseToken, mutationToken),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, 'system:ai-agent-runner', 'lease_acquired', 'queued', status, version,
        json_object('leaseEpoch', lease_epoch)
      FROM ai_agent_jobs WHERE mutation_token = ? AND lease_token = ? AND status = 'running'`)
      .bind(`ai-agent-event-${crypto.randomUUID()}`, mutationToken, leaseToken),
  ]);
  if (changes(writes[0]) !== 1) return null;
  const row = await db.prepare(`SELECT id, lease_token, lease_epoch FROM ai_agent_jobs
    WHERE mutation_token = ? AND lease_token = ? AND status = 'running' LIMIT 1`)
    .bind(mutationToken, leaseToken).first<{ id: string; lease_token: string; lease_epoch: number }>();
  return row ? { jobId: row.id, leaseToken: row.lease_token, leaseEpoch: Number(row.lease_epoch) } : null;
}

async function agentLeaseContext(lease: AiAgentLease, db: D1Database): Promise<AgentJobRow | null> {
  return db.prepare(`SELECT ${AGENT_COLUMNS} FROM ai_agent_jobs
    WHERE id = ? AND status = 'running' AND cancel_requested = 0
      AND lease_token = ? AND lease_epoch = ?
      AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP
    LIMIT 1`).bind(lease.jobId, lease.leaseToken, lease.leaseEpoch).first<AgentJobRow>();
}

function normalizedMicrostepResult(
  resultValue: unknown,
  currentState: AiPassiveJson,
): {
  kind: "checkpoint" | "completed" | "paused" | "failed";
  stateJson: string;
  outputJson: string | null;
  errorCode: string;
  errorMessage: string;
  retryable: boolean;
} {
  const candidate = exactRecord(resultValue, ["kind", "state", "output", "code", "message", "retryable"], "微步骤结果");
  const kind = candidate.kind;
  const result = kind === "checkpoint"
    ? exactRecord(resultValue, ["kind", "state"], "checkpoint 微步骤结果")
    : kind === "complete"
      ? exactRecord(resultValue, ["kind", "output"], "complete 微步骤结果")
      : kind === "pause"
        ? exactRecord(resultValue, ["kind", "state", "code", "message"], "pause 微步骤结果")
        : kind === "fail"
          ? exactRecord(resultValue, ["kind", "code", "message", "retryable"], "fail 微步骤结果")
          : candidate;
  if (result.kind === "checkpoint") {
    const state = normalizePassiveJson(result.state, "微步骤状态", AI_AGENT_WORKFLOW_LIMITS.maximumStateBytes, true);
    return { kind: "checkpoint", stateJson: state.json, outputJson: null, errorCode: "", errorMessage: "", retryable: false };
  }
  if (result.kind === "complete") {
    const output = normalizePassiveJson(result.output, "微步骤输出", AI_AGENT_WORKFLOW_LIMITS.maximumOutputBytes, true);
    const state = normalizePassiveJson(currentState, "微步骤状态", AI_AGENT_WORKFLOW_LIMITS.maximumStateBytes, true);
    return { kind: "completed", stateJson: state.json, outputJson: output.json, errorCode: "", errorMessage: "", retryable: false };
  }
  if (result.kind === "pause") {
    const state = normalizePassiveJson(result.state ?? currentState, "微步骤状态", AI_AGENT_WORKFLOW_LIMITS.maximumStateBytes, true);
    return {
      kind: "paused",
      stateJson: state.json,
      outputJson: null,
      errorCode: safeErrorCode(result.code, "manual_resume_required"),
      errorMessage: boundedText(result.message ?? "任务已安全暂停，等待显式恢复。", "message", 1_000),
      retryable: true,
    };
  }
  if (result.kind === "fail") {
    const state = normalizePassiveJson(currentState, "微步骤状态", AI_AGENT_WORKFLOW_LIMITS.maximumStateBytes, true);
    if (result.retryable !== undefined && typeof result.retryable !== "boolean") {
      throw new PublicApiError(400, "invalid_request", "retryable必须为布尔值。");
    }
    return {
      kind: "failed",
      stateJson: state.json,
      outputJson: null,
      errorCode: safeErrorCode(result.code, "deterministic_step_failed"),
      errorMessage: boundedText(result.message ?? "确定性微步骤执行失败。", "message", 1_000),
      retryable: result.retryable === true,
    };
  }
  throw new PublicApiError(400, "invalid_request", "微步骤结果 kind 无效。");
}

async function commitAgentLease(
  lease: AiAgentLease,
  row: AgentJobRow,
  result: ReturnType<typeof normalizedMicrostepResult>,
  db: D1Database,
): Promise<AiAgentMicrostepOutcome> {
  const nextOrdinal = Number(row.step_index) + 1;
  if (nextOrdinal > AI_AGENT_WORKFLOW_LIMITS.maximumMicrosteps) {
    result = {
      kind: "failed",
      stateJson: row.state_json,
      outputJson: null,
      errorCode: "microstep_limit_exceeded",
      errorMessage: "AI Agent 微步骤数量已达上限。",
      retryable: false,
    };
  }
  const outputDigest = result.outputJson === null ? "" : await sha256(result.outputJson);
  const mutationToken = crypto.randomUUID();
  const nextStatus = result.kind === "checkpoint" ? "queued" : result.kind;
  const nextPhase = result.kind === "checkpoint" ? "queued" : result.kind === "completed" ? "completed" : result.kind;
  const checkpointKind = result.kind === "completed" ? "completed" : result.kind;
  const completed = result.kind === "checkpoint" ? null : "CURRENT_TIMESTAMP";
  const nextVersion = Number(row.version) + 1;
  const update = db.prepare(`UPDATE ai_agent_jobs SET
      status = ?, phase = ?, step_index = ?, state_json = ?, output_json = ?,
      retryable = ?, error_code = ?, error_message = ?, lease_token = '', lease_expires_at = NULL,
      provider_dispatch_started_at = NULL,
      next_run_at = CURRENT_TIMESTAMP, completed_at = ${completed ?? "NULL"},
      updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
    WHERE id = ? AND status = 'running' AND cancel_requested = 0
      AND lease_token = ? AND lease_epoch = ?
      AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP`)
    .bind(nextStatus, nextPhase, nextOrdinal, result.stateJson, result.outputJson,
      result.retryable ? 1 : 0, result.errorCode, result.errorMessage, mutationToken,
      lease.jobId, lease.leaseToken, lease.leaseEpoch);
  const writes = await db.batch([
    update,
    db.prepare(`INSERT INTO ai_agent_checkpoints (
        id, job_id, ordinal, kind, state_json, output_digest
      ) SELECT ?, id, ?, ?, state_json, ? FROM ai_agent_jobs
      WHERE id = ? AND mutation_token = ? AND version = ?`)
      .bind(`ai-agent-checkpoint-${crypto.randomUUID()}`, nextOrdinal, checkpointKind, outputDigest,
        lease.jobId, mutationToken, nextVersion),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, 'system:ai-agent-runner', ?, 'running', status, version,
        json_object('stepIndex', step_index, 'outputDigest', ?)
      FROM ai_agent_jobs WHERE id = ? AND mutation_token = ? AND version = ?`)
      .bind(`ai-agent-event-${crypto.randomUUID()}`, result.kind, outputDigest,
        lease.jobId, mutationToken, nextVersion),
  ]);
  if (changes(writes[0]) !== 1) return { status: "lost", jobId: lease.jobId };
  const status = result.kind === "checkpoint" ? "checkpointed" : result.kind;
  return { status, jobId: lease.jobId, stepIndex: nextOrdinal, ...(result.errorCode ? { code: result.errorCode } : {}) };
}

/**
 * Runs one side-effect-free, deterministic microstep. The callback can only
 * return passive JSON state/output; it cannot request tools, spawn jobs,
 * execute code, publish HTML, or write operational data through this API.
 */
export async function runNextAiAgentMicrostep(
  executeDeterministicStep: AiAgentMicrostepExecutor,
  options: { db?: D1Database; timeoutMs?: number } = {},
): Promise<AiAgentMicrostepOutcome> {
  const db = options.db ?? getD1Database();
  await ensureAiAgentWorkflowSchema(db);
  const lease = await acquireAgentLease(db);
  if (!lease) return { status: "idle" };
  const row = await agentLeaseContext(lease, db);
  if (!row) return { status: "lost", jobId: lease.jobId };
  if (Number(row.step_index) >= AI_AGENT_WORKFLOW_LIMITS.maximumMicrosteps) {
    return commitAgentLease(lease, row, {
      kind: "failed",
      stateJson: row.state_json,
      outputJson: null,
      errorCode: "microstep_limit_exceeded",
      errorMessage: "AI Agent 微步骤数量已达上限。",
      retryable: false,
    }, db);
  }

  const timeoutMs = options.timeoutMs === undefined
    ? AI_AGENT_WORKFLOW_LIMITS.defaultMicrostepTimeoutMs
    : Math.max(100, Math.min(Math.trunc(options.timeoutMs), 30_000));
  const controller = new AbortController();
  let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
  let rawResult: unknown;
  try {
    const timeout = new Promise<never>((_resolve, reject) => {
      timeoutHandle = setTimeout(() => {
        controller.abort();
        reject(new Error("deterministic microstep timed out"));
      }, timeoutMs);
    });
    rawResult = await Promise.race([
      Promise.resolve(executeDeterministicStep(Object.freeze({
        jobId: row.id,
        task: row.task,
        input: parseStoredJson(row.input_json, "AI Agent 输入"),
        state: parseStoredJson(row.state_json, "AI Agent 状态"),
        stepIndex: Number(row.step_index),
        modelId: row.model_id,
        modelVersion: Number(row.model_version),
        allowedTools: Object.freeze(parseStoredAllowedTools(row.allowed_tools_json)),
        toolPolicyDigest: row.tool_policy_digest,
        providerRoundCount: Number(row.provider_round_count),
        toolCallCount: Number(row.tool_call_count),
        signal: controller.signal,
      }))),
      timeout,
    ]);
  } catch {
    rawResult = {
      kind: "pause",
      code: "deterministic_step_interrupted",
      message: "确定性微步骤中断，已暂停并等待显式恢复。",
    };
  } finally {
    if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
  }

  let result: ReturnType<typeof normalizedMicrostepResult>;
  try {
    result = normalizedMicrostepResult(rawResult, parseStoredJson(row.state_json, "AI Agent 状态"));
  } catch {
    result = {
      kind: "failed",
      stateJson: row.state_json,
      outputJson: null,
      errorCode: "invalid_microstep_result",
      errorMessage: "微步骤返回了无效或不安全的结构化结果。",
      retryable: false,
    };
  }
  return commitAgentLease(lease, row, result, db);
}

export function validateAiWorkflowGraph(value: unknown): AiWorkflowGraph {
  const graphRecord = exactRecord(value, ["nodes"], "workflow.graph");
  if (!Array.isArray(graphRecord.nodes) || graphRecord.nodes.length < 1
    || graphRecord.nodes.length > AI_AGENT_WORKFLOW_LIMITS.maximumWorkflowNodes) {
    throw new PublicApiError(400, "invalid_request", `workflow.graph.nodes 数量必须为 1–${AI_AGENT_WORKFLOW_LIMITS.maximumWorkflowNodes}。`);
  }
  const sourceNodes = graphRecord.nodes.map((candidate, index) => {
    const node = exactRecord(candidate, ["key", "type", "dependsOn", "instruction"], `workflow.graph.nodes[${index}]`);
    const key = safeId(node.key, `workflow.graph.nodes[${index}].key`, 64);
    if (node.type !== "agent" && node.type !== "human_review") {
      throw new PublicApiError(400, "invalid_request", `节点 ${key} 仅允许 agent 或 human_review。`);
    }
    if (node.dependsOn !== undefined && !Array.isArray(node.dependsOn)) {
      throw new PublicApiError(400, "invalid_request", `节点 ${key} 的 dependsOn 必须为数组。`);
    }
    const dependencies = (node.dependsOn ?? []).map((dependency) => safeId(dependency, `节点 ${key} 依赖`, 64));
    if (dependencies.length > AI_AGENT_WORKFLOW_LIMITS.maximumDependenciesPerNode
      || new Set(dependencies).size !== dependencies.length || dependencies.includes(key)) {
      throw new PublicApiError(400, "invalid_request", `节点 ${key} 的依赖重复、包含自身或超过上限。`);
    }
    return {
      key,
      type: node.type,
      dependsOn: [...dependencies].sort(),
      instruction: boundedText(node.instruction, `节点 ${key} instruction`, AI_AGENT_WORKFLOW_LIMITS.maximumInstructionCharacters),
      sourceIndex: index,
    } satisfies AiWorkflowNodeDefinition & { sourceIndex: number };
  });
  const keySet = new Set<string>();
  for (const node of sourceNodes) {
    if (keySet.has(node.key)) throw new PublicApiError(400, "invalid_request", `工作流节点 key 重复：${node.key}。`);
    keySet.add(node.key);
  }
  for (const node of sourceNodes) {
    for (const dependency of node.dependsOn) {
      if (!keySet.has(dependency)) throw new PublicApiError(400, "invalid_request", `节点 ${node.key} 引用了不存在的依赖 ${dependency}。`);
    }
  }

  const indegree = new Map(sourceNodes.map((node) => [node.key, node.dependsOn.length]));
  const dependents = new Map<string, string[]>();
  const byKey = new Map(sourceNodes.map((node) => [node.key, node]));
  for (const node of sourceNodes) {
    for (const dependency of node.dependsOn) {
      const values = dependents.get(dependency) ?? [];
      values.push(node.key);
      dependents.set(dependency, values);
    }
  }
  const ready = sourceNodes.filter((node) => node.dependsOn.length === 0)
    .sort((left, right) => left.sourceIndex - right.sourceIndex);
  const ordered: Array<AiWorkflowNodeDefinition & { sourceIndex: number }> = [];
  const depth = new Map<string, number>();
  while (ready.length > 0) {
    const node = ready.shift()!;
    const nodeDepth = node.dependsOn.length === 0
      ? 1
      : Math.max(...node.dependsOn.map((dependency) => depth.get(dependency) ?? 0)) + 1;
    if (nodeDepth > AI_AGENT_WORKFLOW_LIMITS.maximumGraphDepth) {
      throw new PublicApiError(400, "invalid_request", `工作流深度不能超过 ${AI_AGENT_WORKFLOW_LIMITS.maximumGraphDepth}。`);
    }
    depth.set(node.key, nodeDepth);
    ordered.push(node);
    for (const dependentKey of dependents.get(node.key) ?? []) {
      const remaining = (indegree.get(dependentKey) ?? 0) - 1;
      indegree.set(dependentKey, remaining);
      if (remaining === 0) {
        ready.push(byKey.get(dependentKey)!);
        ready.sort((left, right) => left.sourceIndex - right.sourceIndex);
      }
    }
  }
  if (ordered.length !== sourceNodes.length) {
    throw new PublicApiError(400, "invalid_request", "工作流必须是无环 DAG。");
  }
  const graph: AiWorkflowGraph = {
    nodes: ordered.map((node) => ({
      key: node.key,
      type: node.type,
      dependsOn: node.dependsOn,
      instruction: node.instruction,
    })),
  };
  normalizePassiveJson(graph, "workflow.graph", AI_AGENT_WORKFLOW_LIMITS.maximumGraphBytes, true);
  return graph;
}

export async function listAiWorkflowRuns(
  input: { page?: number; pageSize?: number },
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
) {
  await ensureAiAgentWorkflowSchema(db);
  const page = Number.isSafeInteger(input.page) && Number(input.page) > 0 ? Number(input.page) : 1;
  const pageSize = Number.isSafeInteger(input.pageSize) && Number(input.pageSize) > 0
    ? Math.min(Number(input.pageSize), 50) : 20;
  const owner = normalizeOwner(principal);
  const access = aiScopeSnapshotAccessSql(principal.scope, "w.scope_json");
  const totalRow = await db.prepare(`SELECT COUNT(*) count FROM ai_workflow_runs w
    WHERE w.owner_email = ?${access.clause}`).bind(owner, ...access.values).first<{ count: number }>();
  const rows = await db.prepare(`SELECT ${WORKFLOW_COLUMNS.split(",").map((column) => `w.${column.trim()}`).join(", ")}
    FROM ai_workflow_runs w WHERE w.owner_email = ?${access.clause}
    ORDER BY w.created_at DESC, w.id DESC LIMIT ? OFFSET ?`)
    .bind(owner, ...access.values, pageSize, (page - 1) * pageSize).all<WorkflowRunRow>();
  const total = Number(totalRow?.count ?? 0);
  return { items: (rows.results ?? []).map(mapWorkflow), page, pageSize, total, returned: rows.results?.length ?? 0, truncated: page * pageSize < total };
}

export async function getAiWorkflowRun(
  idInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiWorkflowRunDetail> {
  await ensureAiAgentWorkflowSchema(db);
  const id = safeId(idInput, "runId");
  const row = await accessibleWorkflowRow(id, principal, db);
  const nodes = await db.prepare(`SELECT ${NODE_COLUMNS} FROM ai_workflow_node_runs
    WHERE run_id = ? ORDER BY position`).bind(id).all<WorkflowNodeRow>();
  return { ...mapWorkflow(row), nodes: (nodes.results ?? []).map(mapNode) };
}

export async function createAiWorkflowRun(
  inputValue: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
  options: { executorAdmission?: ExecutorAdmission } = {},
): Promise<{ item: AiWorkflowRunDetail; replayed: boolean }> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const input = exactRecord(inputValue, ["clientRequestId", "name", "graph", "input", "dryRun"], "AI 工作流请求");
  const clientRequestId = safeId(input.clientRequestId, "clientRequestId", 128);
  const name = boundedText(input.name, "name", 120);
  const graph = validateAiWorkflowGraph(input.graph);
  const graphValue = normalizePassiveJson(graph, "workflow.graph", AI_AGENT_WORKFLOW_LIMITS.maximumGraphBytes, true);
  const workflowInput = normalizePassiveJson(input.input ?? {}, "input", AI_AGENT_WORKFLOW_LIMITS.maximumInputBytes);
  if (input.dryRun !== undefined && typeof input.dryRun !== "boolean") {
    throw new PublicApiError(400, "invalid_request", "dryRun必须为布尔值。");
  }
  const dryRun = input.dryRun === true;
  const admission = dryRun ? null : requireExecutorAdmission(options.executorAdmission);
  const graphDigest = await sha256(graphValue.json);
  const requestDigest = await sha256(JSON.stringify({
    name,
    graph,
    input: workflowInput.value,
    dryRun,
    ...(admission ? {
      modelId: admission.modelId,
      modelVersion: admission.modelVersion,
      allowedTools: admission.allowedTools,
      toolPolicyDigest: admission.toolPolicyDigest,
    } : {}),
  }));
  const owner = normalizeOwner(principal);
  const access = aiScopeSnapshotAccessSql(principal.scope, "w.scope_json");
  const replay = await db.prepare(`SELECT ${WORKFLOW_COLUMNS.split(",").map((column) => `w.${column.trim()}`).join(", ")}
    FROM ai_workflow_runs w WHERE w.owner_email = ? AND w.client_request_id = ?${access.clause} LIMIT 1`)
    .bind(owner, clientRequestId, ...access.values).first<WorkflowRunRow>();
  if (replay) {
    if (replay.request_digest !== requestDigest) {
      throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的 AI 工作流请求。");
    }
    return { item: await getAiWorkflowRun(replay.id, principal, db), replayed: true };
  }
  const inaccessible = await db.prepare(`SELECT 1 present FROM ai_workflow_runs
    WHERE owner_email = ? AND client_request_id = ? LIMIT 1`)
    .bind(owner, clientRequestId).first<{ present: number }>();
  if (inaccessible) throw new PublicApiError(404, "not_found", "AI 工作流不存在或当前数据范围不可访问。");

  const id = `ai-workflow-${crypto.randomUUID()}`;
  const mutationToken = crypto.randomUUID();
  const scopeJson = serializeAiConversationScope(principal.scope);
  const insertRun = db.prepare(`INSERT OR IGNORE INTO ai_workflow_runs (
      id, owner_email, client_request_id, request_digest, scope_json, name,
      graph_json, graph_digest, input_json, model_id, model_version, allowed_tools_json,
      tool_policy_digest, dry_run, mutation_token
    ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE (SELECT COUNT(*) FROM ai_workflow_runs
      WHERE owner_email = ? AND status IN ('queued','running','waiting_review','paused')) < ?
      AND (SELECT COUNT(*) FROM ai_workflow_runs
        WHERE status IN ('queued','running','waiting_review','paused')) < ?`)
    .bind(id, owner, clientRequestId, requestDigest, scopeJson, name,
      graphValue.json, graphDigest, workflowInput.json,
      admission?.modelId ?? "", admission?.modelVersion ?? 0,
      JSON.stringify(admission?.allowedTools ?? []), admission?.toolPolicyDigest ?? "",
      dryRun ? 1 : 0, mutationToken,
      owner, AI_AGENT_WORKFLOW_LIMITS.maximumActiveWorkflowRunsPerOwner,
      AI_AGENT_WORKFLOW_LIMITS.maximumActiveWorkflowRunsGlobal);
  const insertNodes = graph.nodes.map((node, position) => db.prepare(`INSERT INTO ai_workflow_node_runs (
      id, run_id, node_key, position, node_type, depends_on_json, instruction
    ) SELECT ?, id, ?, ?, ?, ?, ? FROM ai_workflow_runs
    WHERE id = ? AND mutation_token = ?`)
    .bind(`ai-workflow-node-${crypto.randomUUID()}`, node.key, position, node.type,
      JSON.stringify(node.dependsOn), node.instruction, id, mutationToken));
  const writes = await db.batch([
    insertRun,
    ...insertNodes,
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, ?, 'created', status, version,
        json_object('graphDigest', graph_digest, 'dryRun', dry_run)
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, owner, id, mutationToken),
  ]);
  if (changes(writes[0]) !== 1) {
    const raced = await db.prepare(`SELECT ${WORKFLOW_COLUMNS.split(",").map((column) => `w.${column.trim()}`).join(", ")}
      FROM ai_workflow_runs w WHERE w.owner_email = ? AND w.client_request_id = ?${access.clause} LIMIT 1`)
      .bind(owner, clientRequestId, ...access.values).first<WorkflowRunRow>();
    if (raced) {
      if (raced.request_digest !== requestDigest) {
        throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的 AI 工作流请求。");
      }
      return { item: await getAiWorkflowRun(raced.id, principal, db), replayed: true };
    }
    throw new PublicApiError(429, "rate_limited", "当前 AI 工作流活动任务已达上限，请稍后再试。");
  }
  return { item: await getAiWorkflowRun(id, principal, db), replayed: false };
}

export async function cancelAiWorkflowRun(
  idInput: unknown,
  expectedVersionInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiWorkflowRunDetail> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const id = safeId(idInput, "runId");
  const version = expectedVersion(expectedVersionInput);
  const row = await accessibleWorkflowRow(id, principal, db);
  if (Number(row.version) !== version) return versionConflictOrNotFound("workflow", id, principal, db);
  if (row.status === "completed" || row.status === "failed" || row.status === "cancelled") {
    throw new PublicApiError(409, "conflict", "当前 AI 工作流状态不能取消。");
  }
  const mutationToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'cancelled', cancel_requested = 1,
        retryable = 0, lease_token = '', lease_expires_at = NULL,
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND owner_email = ? AND version = ?
        AND status IN ('queued','running','waiting_review','paused')`)
      .bind(mutationToken, id, normalizeOwner(principal), version),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'cancelled',
        error_code = 'workflow_cancelled', error_message = '所属工作流已取消',
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE run_id = ? AND status IN ('pending','running','waiting_review')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id AND w.mutation_token = ? AND w.status = 'cancelled')`)
      .bind(mutationToken, id, mutationToken),
    db.prepare(`UPDATE ai_agent_jobs SET status = 'cancelled', phase = 'cancelled',
        cancel_requested = 1, retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = 'workflow_cancelled', error_message = '所属工作流已取消',
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE workflow_run_id = ? AND status IN ('queued','running','paused')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_agent_jobs.workflow_run_id AND w.mutation_token = ? AND w.status = 'cancelled')`)
      .bind(mutationToken, id, mutationToken),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT 'ai-agent-event-' || lower(hex(randomblob(16))), id, owner_email, ?,
        'workflow_cancelled', 'active', status, version, json_object('workflowRunId', workflow_run_id)
      FROM ai_agent_jobs WHERE workflow_run_id = ? AND mutation_token = ? AND status = 'cancelled'`)
      .bind(normalizeOwner(principal), id, mutationToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, ?, 'cancelled', ?, status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, normalizeOwner(principal), row.status, id, mutationToken),
  ]);
  if (changes(writes[0]) !== 1) return versionConflictOrNotFound("workflow", id, principal, db);
  return getAiWorkflowRun(id, principal, db);
}

export async function resumeAiWorkflowRun(
  idInput: unknown,
  expectedVersionInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiWorkflowRunDetail> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const id = safeId(idInput, "runId");
  const version = expectedVersion(expectedVersionInput);
  const row = await accessibleWorkflowRow(id, principal, db);
  if (Number(row.version) !== version) return versionConflictOrNotFound("workflow", id, principal, db);
  if ((row.status !== "paused" && row.status !== "failed") || !Boolean(row.retryable)) {
    throw new PublicApiError(409, "conflict", "当前 AI 工作流不可安全恢复。");
  }
  if (Number(row.resume_count) >= AI_AGENT_WORKFLOW_LIMITS.maximumResumeCount || !row.current_node_key) {
    throw new PublicApiError(409, "conflict", "AI 工作流无法继续恢复。");
  }
  const node = await db.prepare(`SELECT ${NODE_COLUMNS} FROM ai_workflow_node_runs
    WHERE run_id = ? AND node_key = ? LIMIT 1`)
    .bind(id, row.current_node_key).first<WorkflowNodeRow>();
  if (!node?.agent_job_id || (node.status !== "running" && node.status !== "failed")) {
    throw new PublicApiError(409, "conflict", "当前失败节点不是可恢复的 Agent 节点。");
  }
  const child = await accessibleAgentRow(node.agent_job_id, principal, db);
  if ((child.status !== "paused" && child.status !== "failed") || !Boolean(child.retryable)
    || Number(child.resume_count) >= AI_AGENT_WORKFLOW_LIMITS.maximumResumeCount) {
    throw new PublicApiError(409, "conflict", "工作流子 Agent 任务不可安全恢复。");
  }
  const workflowToken = crypto.randomUUID();
  const childToken = crypto.randomUUID();
  const nodeToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'queued', retryable = 0,
        resume_count = resume_count + 1, lease_token = '', lease_expires_at = NULL,
        next_run_at = CURRENT_TIMESTAMP, error_code = '', error_message = '', completed_at = NULL,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND owner_email = ? AND version = ?
        AND status IN ('paused','failed') AND retryable = 1 AND resume_count < ?
        AND EXISTS (SELECT 1 FROM ai_agent_jobs j
          WHERE j.id = ? AND j.workflow_run_id = ai_workflow_runs.id
            AND j.version = ? AND j.status IN ('paused','failed') AND j.retryable = 1)`)
      .bind(workflowToken, id, normalizeOwner(principal), version,
        AI_AGENT_WORKFLOW_LIMITS.maximumResumeCount, child.id, child.version),
    db.prepare(`UPDATE ai_agent_jobs SET status = 'queued', phase = 'queued',
        cancel_requested = 0, retryable = 0, resume_count = resume_count + 1,
        lease_token = '', lease_expires_at = NULL, next_run_at = CURRENT_TIMESTAMP,
        error_code = '', error_message = '', completed_at = NULL,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND version = ? AND status IN ('paused','failed') AND retryable = 1
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_agent_jobs.workflow_run_id AND w.mutation_token = ? AND w.status = 'queued')`)
      .bind(childToken, child.id, child.version, workflowToken),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'running',
        error_code = '', error_message = '', completed_at = NULL,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND version = ? AND status IN ('running','failed')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id AND w.mutation_token = ? AND w.status = 'queued')`)
      .bind(nodeToken, node.id, node.version, workflowToken),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, ?, 'workflow_resumed', ?, status, version,
        json_object('workflowRunId', workflow_run_id)
      FROM ai_agent_jobs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-agent-event-${crypto.randomUUID()}`, normalizeOwner(principal), child.status, child.id, childToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, current_node_key, owner_email, ?, 'resumed', ?, status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, normalizeOwner(principal), row.status, id, workflowToken),
  ]);
  if (changes(writes[0]) !== 1 || changes(writes[1]) !== 1 || changes(writes[2]) !== 1) {
    return versionConflictOrNotFound("workflow", id, principal, db);
  }
  return getAiWorkflowRun(id, principal, db);
}

export async function reviewAiWorkflowNode(
  runIdInput: unknown,
  nodeKeyInput: unknown,
  inputValue: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiWorkflowRunDetail> {
  await ensureAiAgentWorkflowSchema(db);
  requireAgentMutationRole(principal);
  const runId = safeId(runIdInput, "runId");
  const nodeKey = safeId(nodeKeyInput, "nodeKey", 64);
  const input = exactRecord(inputValue, ["decision", "comment", "expectedVersion"], "人工复核请求");
  if (input.decision !== "approve" && input.decision !== "reject") {
    throw new PublicApiError(400, "invalid_request", "decision 仅允许 approve 或 reject。");
  }
  const nodeVersion = expectedVersion(input.expectedVersion);
  const comment = boundedText(input.comment ?? "", "comment", 2_000, true);
  const run = await accessibleWorkflowRow(runId, principal, db);
  const node = await db.prepare(`SELECT ${NODE_COLUMNS} FROM ai_workflow_node_runs
    WHERE run_id = ? AND node_key = ? LIMIT 1`).bind(runId, nodeKey).first<WorkflowNodeRow>();
  if (!node || node.node_type !== "human_review") {
    throw new PublicApiError(404, "not_found", "人工复核节点不存在。");
  }
  if (run.status !== "waiting_review" || run.current_node_key !== nodeKey
    || node.status !== "waiting_review") {
    throw new PublicApiError(409, "conflict", "当前节点不在等待人工复核状态。");
  }
  if (Number(node.version) !== nodeVersion) {
    throw new PublicApiError(409, "version_conflict", "人工复核节点已被更新，请刷新后重试。");
  }
  const decisionOutput = normalizePassiveJson({ decision: input.decision, comment }, "人工复核结果",
    AI_AGENT_WORKFLOW_LIMITS.maximumOutputBytes, true);
  const approved = input.decision === "approve";
  const runToken = crypto.randomUUID();
  const nodeToken = crypto.randomUUID();
  const nextRunStatus = approved ? "queued" : "failed";
  const nextNodeStatus = approved ? "completed" : "rejected";
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = ?, current_node_key = ?,
        retryable = 0, lease_token = '', lease_expires_at = NULL,
        next_run_at = CURRENT_TIMESTAMP, error_code = ?, error_message = ?,
        completed_at = ${approved ? "NULL" : "CURRENT_TIMESTAMP"},
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND owner_email = ? AND status = 'waiting_review' AND current_node_key = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_node_runs n
          WHERE n.run_id = ai_workflow_runs.id AND n.node_key = ?
            AND n.node_type = 'human_review' AND n.status = 'waiting_review' AND n.version = ?)`)
      .bind(nextRunStatus, approved ? null : nodeKey,
        approved ? "" : "human_review_rejected", approved ? "" : "人工复核未通过",
        runToken, runId, normalizeOwner(principal), nodeKey, nodeKey, nodeVersion),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = ?, output_json = ?,
        reviewer_email = ?, reviewed_at = CURRENT_TIMESTAMP,
        error_code = ?, error_message = ?, completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND version = ? AND status = 'waiting_review'
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id AND w.mutation_token = ? AND w.status = ?)`)
      .bind(nextNodeStatus, decisionOutput.json, normalizeOwner(principal),
        approved ? "" : "human_review_rejected", approved ? "" : "人工复核未通过",
        nodeToken, node.id, nodeVersion, runToken, nextRunStatus),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, ?, owner_email, ?, ?, 'waiting_review', status, version,
        json_object('decision', ?)
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, nodeKey, normalizeOwner(principal),
        approved ? "review_approved" : "review_rejected", input.decision, runId, runToken),
  ]);
  if (changes(writes[0]) !== 1 || changes(writes[1]) !== 1) {
    throw new PublicApiError(409, "version_conflict", "人工复核节点已被其他请求更新。");
  }
  return getAiWorkflowRun(runId, principal, db);
}

type AiWorkflowLease = {
  runId: string;
  leaseToken: string;
  leaseEpoch: number;
};

export type AiWorkflowMicrostepOutcome = {
  status:
    | "idle"
    | "dry_run_node"
    | "child_created"
    | "waiting_child"
    | "node_completed"
    | "waiting_review"
    | "completed"
    | "paused"
    | "failed"
    | "lost";
  runId?: string;
  nodeKey?: string;
  childJobId?: string;
  code?: string;
};

export const AI_AGENT_EXECUTOR_UNAVAILABLE_MESSAGE =
  "后台 AI Agent 执行器尚未启用；当前只开放可完整推进的 dry-run 工作流。";

async function acquireWorkflowLease(
  db: D1Database,
  admission: ExecutorAdmission | null,
): Promise<AiWorkflowLease | null> {
  const admissionClause = admission
    ? "dry_run = 0 AND model_id = ? AND model_version = ? AND allowed_tools_json = ? AND tool_policy_digest = ?"
    : "dry_run = 1 AND model_id = ''";
  const admissionValues = admission
    ? [admission.modelId, admission.modelVersion, JSON.stringify(admission.allowedTools), admission.toolPolicyDigest]
    : [];
  await db.prepare(`UPDATE ai_workflow_runs SET status = 'failed', current_node_key = NULL,
      retryable = 0, lease_token = '', lease_expires_at = NULL,
      error_code = 'provider_dispatch_unknown',
      error_message = '供应商派发后的编排租约已过期，结果未知；工作流已失败关闭且不会自动重排',
      completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
      updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ''
    WHERE ${admissionClause} AND status = 'running' AND lease_expires_at IS NOT NULL
      AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP AND cancel_requested = 0
      AND provider_dispatch_started_at IS NOT NULL`).bind(...admissionValues).run();
  await db.prepare(`UPDATE ai_workflow_node_runs SET status = 'failed',
      error_code = 'provider_dispatch_unknown',
      error_message = '所属工作流的供应商派发结果未知，已阻止自动重排',
      completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
      version = version + 1, mutation_token = ''
    WHERE status IN ('pending','running','waiting_review')
      AND EXISTS (SELECT 1 FROM ai_workflow_runs w
        WHERE w.id = ai_workflow_node_runs.run_id
          AND w.status = 'failed' AND w.error_code = 'provider_dispatch_unknown')`).run();
  await db.prepare(`UPDATE ai_workflow_runs SET status = 'queued',
      lease_token = '', lease_expires_at = NULL, next_run_at = CURRENT_TIMESTAMP,
      error_code = 'lease_expired_requeued',
      error_message = '确定性编排租约过期，已由 fencing 安全重新排队',
      updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ''
    WHERE ${admissionClause} AND status = 'running' AND lease_expires_at IS NOT NULL
      AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP AND cancel_requested = 0
      AND provider_dispatch_started_at IS NULL`).bind(...admissionValues).run();
  const leaseToken = crypto.randomUUID();
  const mutationToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'running',
        attempt_count = attempt_count + 1, lease_token = ?, lease_epoch = lease_epoch + 1,
        lease_expires_at = datetime('now', '+${AI_AGENT_WORKFLOW_LIMITS.leaseSeconds} seconds'),
        started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = (
        SELECT id FROM ai_workflow_runs
        WHERE status = 'queued' AND cancel_requested = 0 AND ${admissionClause}
          AND datetime(next_run_at) <= CURRENT_TIMESTAMP
        ORDER BY created_at, id LIMIT 1
      ) AND status = 'queued' AND cancel_requested = 0`)
      .bind(leaseToken, mutationToken, ...admissionValues),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, 'system:ai-workflow-runner', 'lease_acquired', 'queued', status, version,
        json_object('leaseEpoch', lease_epoch)
      FROM ai_workflow_runs WHERE mutation_token = ? AND lease_token = ? AND status = 'running'`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, mutationToken, leaseToken),
  ]);
  if (changes(writes[0]) !== 1) return null;
  const row = await db.prepare(`SELECT id, lease_token, lease_epoch FROM ai_workflow_runs
    WHERE mutation_token = ? AND lease_token = ? AND status = 'running' LIMIT 1`)
    .bind(mutationToken, leaseToken).first<{ id: string; lease_token: string; lease_epoch: number }>();
  return row ? { runId: row.id, leaseToken: row.lease_token, leaseEpoch: Number(row.lease_epoch) } : null;
}

async function workflowLeaseContext(lease: AiWorkflowLease, db: D1Database): Promise<WorkflowRunRow | null> {
  return db.prepare(`SELECT ${WORKFLOW_COLUMNS} FROM ai_workflow_runs
    WHERE id = ? AND status = 'running' AND cancel_requested = 0
      AND lease_token = ? AND lease_epoch = ?
      AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP
    LIMIT 1`).bind(lease.runId, lease.leaseToken, lease.leaseEpoch).first<WorkflowRunRow>();
}

async function failWorkflowForUnavailableExecutor(
  lease: AiWorkflowLease,
  db: D1Database,
): Promise<boolean> {
  const mutationToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'failed', current_node_key = NULL,
        retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = 'agent_executor_unavailable', error_message = ?,
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND cancel_requested = 0
        AND lease_token = ? AND lease_epoch = ?
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP`)
      .bind(AI_AGENT_EXECUTOR_UNAVAILABLE_MESSAGE, mutationToken,
        lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'failed',
        error_code = 'agent_executor_unavailable', error_message = ?,
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE run_id = ? AND status IN ('pending','running','waiting_review')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id
            AND w.mutation_token = ? AND w.status = 'failed')`)
      .bind(AI_AGENT_EXECUTOR_UNAVAILABLE_MESSAGE, mutationToken, lease.runId, mutationToken),
    db.prepare(`UPDATE ai_agent_jobs SET status = 'failed', phase = 'failed',
        cancel_requested = 1, retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = 'agent_executor_unavailable', error_message = ?,
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE workflow_run_id = ? AND status IN ('queued','running','paused')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_agent_jobs.workflow_run_id
            AND w.mutation_token = ? AND w.status = 'failed')`)
      .bind(AI_AGENT_EXECUTOR_UNAVAILABLE_MESSAGE, mutationToken, lease.runId, mutationToken),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT 'ai-agent-event-' || lower(hex(randomblob(16))), id, owner_email,
        'system:ai-workflow-runner', 'agent_executor_unavailable', 'active', status, version,
        json_object('workflowRunId', workflow_run_id)
      FROM ai_agent_jobs
      WHERE workflow_run_id = ? AND mutation_token = ? AND status = 'failed'`)
      .bind(lease.runId, mutationToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, 'system:ai-workflow-runner',
        'agent_executor_unavailable', 'running', status, version,
        json_object(
          'failedNodes', (SELECT COUNT(*) FROM ai_workflow_node_runs n
            WHERE n.run_id = ai_workflow_runs.id AND n.mutation_token = ?),
          'failedChildren', (SELECT COUNT(*) FROM ai_agent_jobs j
            WHERE j.workflow_run_id = ai_workflow_runs.id AND j.mutation_token = ?)
        )
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, mutationToken, mutationToken,
        lease.runId, mutationToken),
  ]);
  return changes(writes[0]) === 1;
}

function workflowManifest(nodes: readonly WorkflowNodeRow[], override?: { key: string; status: AiWorkflowNodeStatus }) {
  return normalizePassiveJson({
    nodes: nodes.map((node) => ({
      key: node.node_key,
      type: node.node_type,
      status: override?.key === node.node_key ? override.status : node.status,
    })),
  }, "AI 工作流输出", AI_AGENT_WORKFLOW_LIMITS.maximumWorkflowOutputBytes, true).json;
}

async function handbackWorkflowLease(
  lease: AiWorkflowLease,
  input: {
    status: "queued" | "paused" | "failed";
    currentNodeKey: string | null;
    retryable?: boolean;
    errorCode?: string;
    errorMessage?: string;
    delaySeconds?: number;
    eventType: string;
  },
  db: D1Database,
): Promise<boolean> {
  const mutationToken = crypto.randomUUID();
  const nextRun = input.delaySeconds && input.delaySeconds > 0
    ? `datetime('now', '+${Math.min(Math.trunc(input.delaySeconds), 300)} seconds')`
    : "CURRENT_TIMESTAMP";
  const terminal = input.status === "failed";
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = ?, current_node_key = ?,
        retryable = ?, lease_token = '', lease_expires_at = NULL, next_run_at = ${nextRun},
        error_code = ?, error_message = ?, completed_at = ${terminal ? "CURRENT_TIMESTAMP" : "NULL"},
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND cancel_requested = 0
        AND lease_token = ? AND lease_epoch = ?
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP`)
      .bind(input.status, input.currentNodeKey, input.retryable ? 1 : 0,
        safeErrorCode(input.errorCode, ""), boundedText(input.errorMessage ?? "", "errorMessage", 1_000, true),
        mutationToken, lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, current_node_key, owner_email, 'system:ai-workflow-runner', ?, 'running', status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, input.eventType, lease.runId, mutationToken),
  ]);
  return changes(writes[0]) === 1;
}

async function completeWorkflowWithoutNode(
  lease: AiWorkflowLease,
  nodes: readonly WorkflowNodeRow[],
  db: D1Database,
): Promise<boolean> {
  const mutationToken = crypto.randomUUID();
  const outputJson = workflowManifest(nodes);
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'completed', current_node_key = NULL,
        output_json = ?, retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = '', error_message = '', completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND cancel_requested = 0
        AND lease_token = ? AND lease_epoch = ?
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP
        AND NOT EXISTS (SELECT 1 FROM ai_workflow_node_runs n
          WHERE n.run_id = ai_workflow_runs.id
            AND n.status NOT IN ('completed','skipped'))`)
      .bind(outputJson, mutationToken, lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, 'system:ai-workflow-runner', 'completed', 'running', status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, lease.runId, mutationToken),
  ]);
  return changes(writes[0]) === 1;
}

async function advanceDryRunNode(
  lease: AiWorkflowLease,
  node: WorkflowNodeRow,
  nodes: readonly WorkflowNodeRow[],
  db: D1Database,
): Promise<boolean> {
  const nodeOutput = normalizePassiveJson({
    dryRun: true,
    nodeKey: node.node_key,
    nodeType: node.node_type,
    dependsOn: parseStoredJson(node.depends_on_json, "AI 工作流依赖"),
  }, "dry-run 节点输出", AI_AGENT_WORKFLOW_LIMITS.maximumOutputBytes, true);
  const isLast = nodes.every((candidate) => candidate.id === node.id
    || candidate.status === "completed" || candidate.status === "skipped");
  const nodeToken = crypto.randomUUID();
  const runToken = crypto.randomUUID();
  const outputJson = isLast ? workflowManifest(nodes, { key: node.node_key, status: "skipped" }) : null;
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'skipped', output_json = ?,
        completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'pending'
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w WHERE w.id = ai_workflow_node_runs.run_id
          AND w.id = ? AND w.status = 'running' AND w.dry_run = 1
          AND w.lease_token = ? AND w.lease_epoch = ?
          AND datetime(w.lease_expires_at) > CURRENT_TIMESTAMP)`)
      .bind(nodeOutput.json, nodeToken, node.id, lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE ai_workflow_runs SET status = ?, current_node_key = NULL,
        output_json = ?, lease_token = '', lease_expires_at = NULL,
        completed_at = ${isLast ? "CURRENT_TIMESTAMP" : "NULL"},
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_node_runs n
          WHERE n.id = ? AND n.mutation_token = ? AND n.status = 'skipped')`)
      .bind(isLast ? "completed" : "queued", outputJson, runToken,
        lease.runId, lease.leaseToken, lease.leaseEpoch, node.id, nodeToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, ?, owner_email, 'system:ai-workflow-runner', 'dry_run_node', 'running', status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, node.node_key, lease.runId, runToken),
  ]);
  return changes(writes[0]) === 1 && changes(writes[1]) === 1;
}

async function waitForHumanReview(
  lease: AiWorkflowLease,
  node: WorkflowNodeRow,
  db: D1Database,
): Promise<boolean> {
  const nodeToken = crypto.randomUUID();
  const runToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'waiting_review',
        started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'pending'
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w WHERE w.id = ai_workflow_node_runs.run_id
          AND w.id = ? AND w.status = 'running' AND w.dry_run = 0
          AND w.lease_token = ? AND w.lease_epoch = ?
          AND datetime(w.lease_expires_at) > CURRENT_TIMESTAMP)`)
      .bind(nodeToken, node.id, lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE ai_workflow_runs SET status = 'waiting_review', current_node_key = ?,
        lease_token = '', lease_expires_at = NULL, retryable = 0,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_node_runs n
          WHERE n.id = ? AND n.mutation_token = ? AND n.status = 'waiting_review')`)
      .bind(node.node_key, runToken, lease.runId, lease.leaseToken, lease.leaseEpoch, node.id, nodeToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, ?, owner_email, 'system:ai-workflow-runner', 'waiting_review', 'running', status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, node.node_key, lease.runId, runToken),
  ]);
  return changes(writes[0]) === 1 && changes(writes[1]) === 1;
}

function dependencyInput(run: WorkflowRunRow, node: WorkflowNodeRow, nodes: readonly WorkflowNodeRow[]) {
  const dependencyKeys = parseStoredJson(node.depends_on_json, "AI 工作流依赖");
  if (!Array.isArray(dependencyKeys) || !dependencyKeys.every((value) => typeof value === "string")) {
    throw new Error("AI 工作流节点依赖无效");
  }
  const dependencies: Record<string, AiPassiveJson> = {};
  for (const key of dependencyKeys) {
    const dependency = nodes.find((candidate) => candidate.node_key === key);
    if (!dependency || (dependency.status !== "completed" && dependency.status !== "skipped")
      || dependency.output_json === null) {
      throw new Error(`AI 工作流依赖 ${key} 尚未产生结构化输出`);
    }
    dependencies[key] = parseStoredJson(dependency.output_json, `AI 工作流依赖 ${key}`);
  }
  return normalizePassiveJson({
    workflowInput: parseStoredJson(run.input_json, "AI 工作流输入"),
    dependencies,
  }, "AI Agent 工作流输入", AI_AGENT_WORKFLOW_LIMITS.maximumInputBytes, true);
}

async function createWorkflowChildAgent(
  lease: AiWorkflowLease,
  run: WorkflowRunRow,
  node: WorkflowNodeRow,
  nodes: readonly WorkflowNodeRow[],
  db: D1Database,
): Promise<{ created: boolean; childJobId?: string }> {
  const childInput = dependencyInput(run, node, nodes);
  const clientRequestId = `wfnode-${(await sha256(`${run.id}:${node.node_key}`)).slice(0, 48)}`;
  const requestDigest = await sha256(JSON.stringify({
    task: node.instruction,
    input: childInput.value,
    workflowRunId: run.id,
    workflowNodeKey: node.node_key,
    modelId: run.model_id,
    modelVersion: run.model_version,
    allowedTools: parseStoredAllowedTools(run.allowed_tools_json),
    toolPolicyDigest: run.tool_policy_digest,
  }));
  const childId = `ai-agent-${crypto.randomUUID()}`;
  const childToken = crypto.randomUUID();
  const nodeToken = crypto.randomUUID();
  const runToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`INSERT OR IGNORE INTO ai_agent_jobs (
        id, owner_email, client_request_id, request_digest, scope_json, task, input_json,
        model_id, model_version, allowed_tools_json, tool_policy_digest,
        workflow_run_id, workflow_node_key, mutation_token
      ) SELECT ?, owner_email, ?, ?, scope_json, ?, ?, model_id, model_version,
        allowed_tools_json, tool_policy_digest, id, ?, ?
      FROM ai_workflow_runs WHERE id = ? AND status = 'running' AND dry_run = 0
        AND lease_token = ? AND lease_epoch = ? AND datetime(lease_expires_at) > CURRENT_TIMESTAMP
        AND (SELECT COUNT(*) FROM ai_agent_jobs
          WHERE owner_email = ai_workflow_runs.owner_email AND status IN ('queued','running')) < ?
        AND (SELECT COUNT(*) FROM ai_agent_jobs WHERE status IN ('queued','running')) < ?`)
      .bind(childId, clientRequestId, requestDigest, node.instruction, childInput.json,
        node.node_key, childToken, run.id, lease.leaseToken, lease.leaseEpoch,
        AI_AGENT_WORKFLOW_LIMITS.maximumActiveAgentJobsPerOwner,
        AI_AGENT_WORKFLOW_LIMITS.maximumActiveAgentJobsGlobal),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'running', input_json = ?,
        agent_job_id = (SELECT id FROM ai_agent_jobs
          WHERE owner_email = ? AND client_request_id = ? AND request_digest = ? LIMIT 1),
        started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'pending'
        AND EXISTS (SELECT 1 FROM ai_agent_jobs j
          WHERE j.owner_email = ? AND j.client_request_id = ?
            AND j.request_digest = ? AND j.workflow_run_id = ? AND j.workflow_node_key = ?)
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w WHERE w.id = ai_workflow_node_runs.run_id
          AND w.id = ? AND w.status = 'running' AND w.lease_token = ? AND w.lease_epoch = ?
          AND datetime(w.lease_expires_at) > CURRENT_TIMESTAMP)`)
      .bind(childInput.json, run.owner_email, clientRequestId, requestDigest, nodeToken, node.id,
        run.owner_email, clientRequestId, requestDigest, run.id, node.node_key,
        run.id, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE ai_workflow_runs SET status = 'queued', current_node_key = ?,
        lease_token = '', lease_expires_at = NULL, next_run_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_node_runs n
          WHERE n.id = ? AND n.mutation_token = ? AND n.status = 'running' AND n.agent_job_id IS NOT NULL)`)
      .bind(node.node_key, runToken, run.id, lease.leaseToken, lease.leaseEpoch, node.id, nodeToken),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, to_status, job_version, details_json
      ) SELECT ?, id, owner_email, 'system:ai-workflow-runner', 'workflow_child_created', status, version,
        json_object('workflowRunId', workflow_run_id, 'workflowNodeKey', workflow_node_key)
      FROM ai_agent_jobs WHERE owner_email = ? AND client_request_id = ? AND mutation_token = ?`)
      .bind(`ai-agent-event-${crypto.randomUUID()}`, run.owner_email, clientRequestId, childToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, ?, owner_email, 'system:ai-workflow-runner', 'child_created', 'running', status, version,
        json_object('childJobId', (SELECT agent_job_id FROM ai_workflow_node_runs WHERE id = ?))
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, node.node_key, node.id, run.id, runToken),
  ]);
  if (changes(writes[1]) !== 1 || changes(writes[2]) !== 1) return { created: false };
  const linked = await db.prepare(`SELECT agent_job_id FROM ai_workflow_node_runs WHERE id = ? LIMIT 1`)
    .bind(node.id).first<{ agent_job_id: string | null }>();
  return linked?.agent_job_id ? { created: true, childJobId: linked.agent_job_id } : { created: false };
}

async function advanceCompletedChild(
  lease: AiWorkflowLease,
  node: WorkflowNodeRow,
  child: AgentJobRow,
  nodes: readonly WorkflowNodeRow[],
  db: D1Database,
): Promise<{ completed: boolean; workflowCompleted: boolean }> {
  if (child.output_json === null) throw new Error("已完成的 AI Agent 子任务缺少结构化输出");
  normalizePassiveJson(JSON.parse(child.output_json), "AI Agent 子任务输出", AI_AGENT_WORKFLOW_LIMITS.maximumOutputBytes, true);
  const isLast = nodes.every((candidate) => candidate.id === node.id
    || candidate.status === "completed" || candidate.status === "skipped");
  const nodeToken = crypto.randomUUID();
  const runToken = crypto.randomUUID();
  const outputJson = isLast ? workflowManifest(nodes, { key: node.node_key, status: "completed" }) : null;
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'completed', output_json = ?,
        error_code = '', error_message = '', completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND agent_job_id = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w WHERE w.id = ai_workflow_node_runs.run_id
          AND w.id = ? AND w.status = 'running' AND w.lease_token = ? AND w.lease_epoch = ?
          AND datetime(w.lease_expires_at) > CURRENT_TIMESTAMP)
        AND EXISTS (SELECT 1 FROM ai_agent_jobs j
          WHERE j.id = ai_workflow_node_runs.agent_job_id AND j.status = 'completed')`)
      .bind(child.output_json, nodeToken, node.id, child.id,
        lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE ai_workflow_runs SET status = ?, current_node_key = NULL,
        output_json = ?, retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = '', error_message = '', completed_at = ${isLast ? "CURRENT_TIMESTAMP" : "NULL"},
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_node_runs n
          WHERE n.id = ? AND n.mutation_token = ? AND n.status = 'completed')`)
      .bind(isLast ? "completed" : "queued", outputJson, runToken,
        lease.runId, lease.leaseToken, lease.leaseEpoch, node.id, nodeToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, ?, owner_email, 'system:ai-workflow-runner', ?, 'running', status, version,
        json_object('childJobId', ?)
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, node.node_key,
        isLast ? "completed" : "node_completed", child.id, lease.runId, runToken),
  ]);
  return { completed: changes(writes[0]) === 1 && changes(writes[1]) === 1, workflowCompleted: isLast };
}

async function failWorkflowForChild(
  lease: AiWorkflowLease,
  node: WorkflowNodeRow,
  child: AgentJobRow,
  db: D1Database,
): Promise<boolean> {
  const childStatus = asAgentStatus(child.status);
  const retryable = (childStatus === "paused" || childStatus === "failed") && Boolean(child.retryable);
  const nodeStatus = childStatus === "cancelled" ? "cancelled" : childStatus === "failed" ? "failed" : "running";
  const runStatus = childStatus === "paused" ? "paused" : "failed";
  const errorCode = childStatus === "cancelled" ? "child_cancelled" : child.error_code || "child_agent_failed";
  const errorMessage = childStatus === "cancelled" ? "工作流子 Agent 已取消" : child.error_message || "工作流子 Agent 未完成";
  const nodeToken = crypto.randomUUID();
  const runToken = crypto.randomUUID();
  const writes = await db.batch([
    db.prepare(`UPDATE ai_workflow_node_runs SET status = ?, error_code = ?, error_message = ?,
        completed_at = ${nodeStatus === "running" ? "NULL" : "CURRENT_TIMESTAMP"},
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND agent_job_id = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w WHERE w.id = ai_workflow_node_runs.run_id
          AND w.id = ? AND w.status = 'running' AND w.lease_token = ? AND w.lease_epoch = ?
          AND w.lease_expires_at IS NOT NULL AND datetime(w.lease_expires_at) > CURRENT_TIMESTAMP)`)
      .bind(nodeStatus, errorCode, errorMessage, nodeToken, node.id, child.id,
        lease.runId, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE ai_workflow_runs SET status = ?, current_node_key = ?, retryable = ?,
        lease_token = '', lease_expires_at = NULL, error_code = ?, error_message = ?,
        completed_at = ${runStatus === "failed" ? "CURRENT_TIMESTAMP" : "NULL"},
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
        AND EXISTS (SELECT 1 FROM ai_workflow_node_runs n WHERE n.id = ? AND n.mutation_token = ?)`)
      .bind(runStatus, node.node_key, retryable ? 1 : 0, errorCode, errorMessage, runToken,
        lease.runId, lease.leaseToken, lease.leaseEpoch, node.id, nodeToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, node_key, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, ?, owner_email, 'system:ai-workflow-runner', ?, 'running', status, version,
        json_object('childJobId', ?)
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, node.node_key,
        runStatus === "paused" ? "child_paused" : "child_failed", child.id, lease.runId, runToken),
  ]);
  return changes(writes[0]) === 1 && changes(writes[1]) === 1;
}

/** Advances one deterministic orchestration transition; Agent work remains in its own durable job. */
export async function runNextAiWorkflowMicrostep(
  options: { db?: D1Database; executorAdmission?: ExecutorAdmission } = {},
): Promise<AiWorkflowMicrostepOutcome> {
  const db = options.db ?? getD1Database();
  await ensureAiAgentWorkflowSchema(db);
  const admission = options.executorAdmission === undefined
    ? null
    : requireExecutorAdmission(options.executorAdmission);
  const lease = await acquireWorkflowLease(db, admission);
  if (!lease) return { status: "idle" };
  const run = await workflowLeaseContext(lease, db);
  if (!run) return { status: "lost", runId: lease.runId };
  if (!Boolean(run.dry_run) && (!admission
    || run.model_id !== admission.modelId
    || Number(run.model_version) !== admission.modelVersion
    || run.allowed_tools_json !== JSON.stringify(admission.allowedTools)
    || run.tool_policy_digest !== admission.toolPolicyDigest)) {
    const failed = await failWorkflowForUnavailableExecutor(lease, db);
    return {
      status: failed ? "failed" : "lost",
      runId: run.id,
      code: "agent_executor_unavailable",
    };
  }
  const nodeRows = await db.prepare(`SELECT ${NODE_COLUMNS} FROM ai_workflow_node_runs
    WHERE run_id = ? ORDER BY position`).bind(run.id).all<WorkflowNodeRow>();
  const nodes = nodeRows.results ?? [];
  if (nodes.length < 1 || nodes.length > AI_AGENT_WORKFLOW_LIMITS.maximumWorkflowNodes) {
    const failed = await handbackWorkflowLease(lease, {
      status: "failed", currentNodeKey: null, eventType: "invalid_graph",
      errorCode: "invalid_graph", errorMessage: "持久化工作流节点数量无效。",
    }, db);
    return { status: failed ? "failed" : "lost", runId: run.id, code: "invalid_graph" };
  }
  const active = nodes.filter((node) => node.status === "running" || node.status === "waiting_review");
  if (active.length > 1 || active.some((node) => node.status === "waiting_review")) {
    const failed = await handbackWorkflowLease(lease, {
      status: "failed", currentNodeKey: run.current_node_key, eventType: "invalid_node_state",
      errorCode: "invalid_node_state", errorMessage: "顺序工作流出现多个活动节点或未释放的人工复核节点。",
    }, db);
    return { status: failed ? "failed" : "lost", runId: run.id, code: "invalid_node_state" };
  }

  const activeNode = active[0];
  if (activeNode) {
    if (activeNode.node_type !== "agent" || !activeNode.agent_job_id) {
      const failed = await handbackWorkflowLease(lease, {
        status: "failed", currentNodeKey: activeNode.node_key, eventType: "invalid_node_state",
        errorCode: "invalid_node_state", errorMessage: "活动节点缺少唯一 Agent 子任务。",
      }, db);
      return { status: failed ? "failed" : "lost", runId: run.id, nodeKey: activeNode.node_key, code: "invalid_node_state" };
    }
    const child = await db.prepare(`SELECT ${AGENT_COLUMNS} FROM ai_agent_jobs
      WHERE id = ? AND workflow_run_id = ? AND workflow_node_key = ? LIMIT 1`)
      .bind(activeNode.agent_job_id, run.id, activeNode.node_key).first<AgentJobRow>();
    if (!child) {
      const failed = await handbackWorkflowLease(lease, {
        status: "failed", currentNodeKey: activeNode.node_key, eventType: "child_missing",
        errorCode: "child_missing", errorMessage: "工作流 Agent 子任务不存在。",
      }, db);
      return { status: failed ? "failed" : "lost", runId: run.id, nodeKey: activeNode.node_key, code: "child_missing" };
    }
    if (child.status === "completed") {
      try {
        const advanced = await advanceCompletedChild(lease, activeNode, child, nodes, db);
        if (!advanced.completed) return { status: "lost", runId: run.id, nodeKey: activeNode.node_key };
        return { status: advanced.workflowCompleted ? "completed" : "node_completed", runId: run.id, nodeKey: activeNode.node_key, childJobId: child.id };
      } catch {
        const failed = await handbackWorkflowLease(lease, {
          status: "failed", currentNodeKey: activeNode.node_key, eventType: "invalid_child_output",
          errorCode: "invalid_child_output", errorMessage: "Agent 子任务输出无效或超过边界。",
        }, db);
        return { status: failed ? "failed" : "lost", runId: run.id, nodeKey: activeNode.node_key, code: "invalid_child_output" };
      }
    }
    if (child.status === "paused" || child.status === "failed" || child.status === "cancelled") {
      const failed = await failWorkflowForChild(lease, activeNode, child, db);
      return {
        status: failed ? (child.status === "paused" ? "paused" : "failed") : "lost",
        runId: run.id,
        nodeKey: activeNode.node_key,
        childJobId: child.id,
        code: child.error_code || (child.status === "cancelled" ? "child_cancelled" : "child_agent_failed"),
      };
    }
    const handedBack = await handbackWorkflowLease(lease, {
      status: "queued", currentNodeKey: activeNode.node_key, delaySeconds: 5,
      eventType: "waiting_child",
    }, db);
    return { status: handedBack ? "waiting_child" : "lost", runId: run.id, nodeKey: activeNode.node_key, childJobId: child.id };
  }

  const pending = nodes.find((node) => node.status === "pending");
  if (!pending) {
    const completed = await completeWorkflowWithoutNode(lease, nodes, db);
    if (completed) return { status: "completed", runId: run.id };
    const failed = await handbackWorkflowLease(lease, {
      status: "failed", currentNodeKey: null, eventType: "invalid_terminal_state",
      errorCode: "invalid_terminal_state", errorMessage: "工作流没有可执行节点且尚未满足完成条件。",
    }, db);
    return { status: failed ? "failed" : "lost", runId: run.id, code: "invalid_terminal_state" };
  }
  const dependencies = parseStoredJson(pending.depends_on_json, "AI 工作流依赖");
  const dependencyKeys = Array.isArray(dependencies) ? dependencies.filter((item): item is string => typeof item === "string") : [];
  const unmet = dependencyKeys.find((key) => {
    const dependency = nodes.find((node) => node.node_key === key);
    return !dependency || (dependency.status !== "completed" && dependency.status !== "skipped");
  });
  if (unmet) {
    const failed = await handbackWorkflowLease(lease, {
      status: "failed", currentNodeKey: pending.node_key, eventType: "dependency_unavailable",
      errorCode: "dependency_unavailable", errorMessage: `节点依赖 ${unmet} 未成功完成。`,
    }, db);
    return { status: failed ? "failed" : "lost", runId: run.id, nodeKey: pending.node_key, code: "dependency_unavailable" };
  }
  if (Boolean(run.dry_run)) {
    const advanced = await advanceDryRunNode(lease, pending, nodes, db);
    return { status: advanced ? "dry_run_node" : "lost", runId: run.id, nodeKey: pending.node_key };
  }
  if (pending.node_type === "human_review") {
    const waiting = await waitForHumanReview(lease, pending, db);
    return { status: waiting ? "waiting_review" : "lost", runId: run.id, nodeKey: pending.node_key };
  }
  try {
    const created = await createWorkflowChildAgent(lease, run, pending, nodes, db);
    if (created.created) {
      return { status: "child_created", runId: run.id, nodeKey: pending.node_key, childJobId: created.childJobId };
    }
    const handedBack = await handbackWorkflowLease(lease, {
      status: "queued", currentNodeKey: null, delaySeconds: 60,
      eventType: "agent_capacity_wait",
      errorCode: "agent_capacity_wait", errorMessage: "AI Agent 活动任务已达上限，工作流稍后重试。",
    }, db);
    return { status: handedBack ? "waiting_child" : "lost", runId: run.id, nodeKey: pending.node_key, code: "agent_capacity_wait" };
  } catch {
    const failed = await handbackWorkflowLease(lease, {
      status: "failed", currentNodeKey: pending.node_key, eventType: "dependency_input_invalid",
      errorCode: "dependency_input_invalid", errorMessage: "工作流依赖输出无法安全组装为 Agent 输入。",
    }, db);
    return { status: failed ? "failed" : "lost", runId: run.id, nodeKey: pending.node_key, code: "dependency_input_invalid" };
  }
}
