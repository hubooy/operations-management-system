import {
  ensureAuthorizationSchema,
  type AppPrincipal,
} from "@/tests/legacy/auth/authorization";
import {
  createExecutorAdmission,
  type ExecutorAdmission,
} from "@/tests/legacy/ai/agent-workflows";
import { ensureAiAgentWorkflowSchema } from "@/tests/legacy/ai/agent-workflow-schema";
import {
  resolveAiBackgroundPrincipal,
} from "@/lib/ai/background-principal";
import { resolveChatModel } from "@/tests/legacy/ai/assistant-service";
import {
  getToolsForPrincipal,
  getVisibleToolCatalog,
} from "@/lib/ai/tool-registry";
import type { AiToolEntry } from "@/lib/ai/tool-registry-contract";
import { PublicApiError } from "@/lib/http/api-error";
import { getD1Database, type D1Database } from "@/lib/database/d1";

export const AI_AGENT_EXECUTOR_PROMPT_VERSION = "teruisi-agent-v1";
export const AI_FORMAL_WORKFLOW_INACTIVE_TTL_DAYS = 30;

export type CurrentAgentExecutorAdmission = {
  admission: ExecutorAdmission;
  entries: readonly AiToolEntry[];
};

/**
 * Builds the immutable model/tool snapshot used at task admission. Request
 * JSON can select a model id, but can never supply its version or tool policy.
 */
export async function createCurrentAgentExecutorAdmission(
  principal: AppPrincipal,
  modelId?: string | null,
  db: D1Database = getD1Database(),
): Promise<CurrentAgentExecutorAdmission> {
  const normalizedModelId = normalizeOptionalModelId(modelId);
  const model = await resolveChatModel({
    ...(normalizedModelId ? { modelId: normalizedModelId } : {}),
    allowFallback: !normalizedModelId,
  }, db);
  if (!model) {
    throw new PublicApiError(
      503,
      "service_unavailable",
      normalizedModelId ? "所选 AI 模型不存在、已停用或不可用于 Agent。" : "尚未配置可用的文本 AI 模型。",
    );
  }
  const entries = [...getToolsForPrincipal(principal, "ai_agent")]
    .sort((left, right) => left.name.localeCompare(right.name));
  const catalog = getVisibleToolCatalog(principal, "ai_agent", entries);
  const allowedTools = entries.map((entry) => entry.name).sort();
  const toolPolicyDigest = await digestCanonicalJson({
    executorPromptVersion: AI_AGENT_EXECUTOR_PROMPT_VERSION,
    surface: "ai_agent",
    catalog,
  });
  return {
    admission: createExecutorAdmission({
      modelId: model.id,
      modelVersion: model.version,
      allowedTools,
      toolPolicyDigest,
    }),
    entries,
  };
}

/** Recomputes the current catalog and requires an exact safe snapshot match. */
export async function validateCurrentAgentToolPolicy(
  principal: AppPrincipal,
  expected: Pick<ExecutorAdmission, "allowedTools" | "toolPolicyDigest">,
): Promise<{ ok: true; entries: readonly AiToolEntry[] } | { ok: false }> {
  const visible = getToolsForPrincipal(principal, "ai_agent");
  const expectedNames = [...expected.allowedTools].sort();
  const currentEntries = visible
    .filter((entry) => expectedNames.includes(entry.name))
    .sort((left, right) => left.name.localeCompare(right.name));
  if (currentEntries.length !== expectedNames.length
    || currentEntries.some((entry, index) => entry.name !== expectedNames[index])) {
    return { ok: false };
  }
  const catalog = getVisibleToolCatalog(principal, "ai_agent", currentEntries);
  const digest = await digestCanonicalJson({
    executorPromptVersion: AI_AGENT_EXECUTOR_PROMPT_VERSION,
    surface: "ai_agent",
    catalog,
  });
  return digest === expected.toolPolicyDigest
    ? { ok: true, entries: currentEntries }
    : { ok: false };
}

/**
 * Selects the oldest runnable workflow's persisted capability. Orchestration
 * itself is deterministic and does not call a provider; the child Agent still
 * revalidates current owner/model/tool policy before every external action.
 * `null` means either the selected run is a dry-run or no run is ready.
 */
export async function selectNextWorkflowExecutorAdmission(
  db: D1Database = getD1Database(),
): Promise<ExecutorAdmission | null> {
  // This selector runs before the deterministic workflow runner. Upgrade and
  // fail-close legacy rows first so an old/default admission snapshot cannot
  // throw here forever and starve every workflow behind it.
  await ensureAiAgentWorkflowSchema(db);
  await ensureAuthorizationSchema(db);
  await failRevokedActiveFormalWorkflows(db);
  const row = await db.prepare(`SELECT id, status, dry_run, model_id, model_version,
      allowed_tools_json, tool_policy_digest
    FROM ai_workflow_runs
    WHERE cancel_requested = 0 AND (
      (status = 'queued' AND datetime(next_run_at) <= CURRENT_TIMESTAMP)
      OR (status = 'running' AND lease_expires_at IS NOT NULL
        AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP)
    )
    ORDER BY created_at, id LIMIT 1`)
    .first<{
      id: string;
      status: "queued" | "running";
      dry_run: number;
      model_id: string;
      model_version: number;
      allowed_tools_json: string;
      tool_policy_digest: string;
    }>();
  if (!row || Boolean(row.dry_run)) return null;
  let allowedTools: unknown;
  try {
    allowedTools = JSON.parse(row.allowed_tools_json);
  } catch {
    allowedTools = null;
  }
  try {
    return createExecutorAdmission({
      modelId: row.model_id,
      modelVersion: Number(row.model_version),
      allowedTools,
      toolPolicyDigest: row.tool_policy_digest,
    });
  } catch {
    await failInvalidWorkflowAdmission(row, db);
    return null;
  }
}

type ActiveFormalWorkflowAuthorizationRow = {
  id: string;
  owner_email: string;
  scope_json: string;
  status: "queued" | "running" | "waiting_review" | "paused";
  version: number;
  inactive_expired: number;
};

/**
 * Waiting-review and paused workflows are not otherwise selected by the
 * orchestrator. Revalidate every bounded active formal run here so an owner
 * revocation or scope narrowing cannot strand an invisible run in the global
 * active quota forever.
 */
async function failRevokedActiveFormalWorkflows(db: D1Database): Promise<void> {
  const rows = await db.prepare(`SELECT id, owner_email, scope_json, status, version,
      CASE WHEN status IN ('waiting_review','paused')
        AND datetime(updated_at) <= datetime('now', '-${AI_FORMAL_WORKFLOW_INACTIVE_TTL_DAYS} days')
        THEN 1 ELSE 0 END AS inactive_expired
    FROM ai_workflow_runs
    WHERE dry_run = 0 AND cancel_requested = 0
      AND status IN ('queued','running','waiting_review','paused')
    ORDER BY created_at, id
    LIMIT 64`).all<ActiveFormalWorkflowAuthorizationRow>();
  for (const row of rows.results ?? []) {
    const authorization = await resolveAiBackgroundPrincipal(row.owner_email, row.scope_json);
    if (!authorization.ok) {
      await failWorkflowForRevokedAuthorization(row, authorization, db);
      continue;
    }
    if (Number(row.inactive_expired) === 1) {
      await failWorkflowIfInactiveExpired(row, db);
    }
  }
}

async function failWorkflowForRevokedAuthorization(
  row: ActiveFormalWorkflowAuthorizationRow,
  authorization: Extract<Awaited<ReturnType<typeof resolveAiBackgroundPrincipal>>, { ok: false }>,
  db: D1Database,
): Promise<void> {
  const errorCode = authorization.code;
  const errorMessage = authorization.message.slice(0, 800);
  // The current authorization was resolved from the PostgreSQL authority.
  // The fenced update below still binds the exact immutable owner, scope,
  // version and state snapshot; D1 no longer contains a second user authority.
  const authorizationStillRevoked = "1 = 1";
  await failActiveFormalWorkflow(row, {
    actor: "system:workflow-authorization-maintenance",
    code: errorCode,
    message: errorMessage,
    updateGuardSql: authorizationStillRevoked,
  }, db);
}

async function failWorkflowIfInactiveExpired(
  row: ActiveFormalWorkflowAuthorizationRow,
  db: D1Database,
): Promise<void> {
  await failActiveFormalWorkflow(row, {
    actor: "system:workflow-lifecycle-maintenance",
    code: "workflow_inactive_expired",
    message: `工作流待处理状态连续 ${AI_FORMAL_WORKFLOW_INACTIVE_TTL_DAYS} 天无活动，已终止并释放执行配额。`,
    updateGuardSql: `datetime(updated_at) <= datetime('now', '-${AI_FORMAL_WORKFLOW_INACTIVE_TTL_DAYS} days')`,
  }, db);
}

async function failActiveFormalWorkflow(
  row: ActiveFormalWorkflowAuthorizationRow,
  failure: {
    actor: "system:workflow-authorization-maintenance" | "system:workflow-lifecycle-maintenance";
    code: string;
    message: string;
    updateGuardSql: string;
  },
  db: D1Database,
): Promise<void> {
  const mutationToken = crypto.randomUUID();
  await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'failed', current_node_key = NULL,
        cancel_requested = 1, retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = ?, error_message = ?, completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
        updated_at = CURRENT_TIMESTAMP, version = version + 1, mutation_token = ?
      WHERE id = ? AND owner_email = ? COLLATE NOCASE AND scope_json = ?
        AND version = ? AND status = ? AND dry_run = 0 AND cancel_requested = 0
        AND ${failure.updateGuardSql}`)
      .bind(failure.code, failure.message, mutationToken, row.id, row.owner_email, row.scope_json,
        Number(row.version), row.status),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'failed',
        error_code = ?, error_message = ?,
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE run_id = ? AND status IN ('pending','running','waiting_review')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id AND w.mutation_token = ?
            AND w.status = 'failed' AND w.error_code = ?)`)
      .bind(failure.code, failure.message, mutationToken, row.id, mutationToken, failure.code),
    db.prepare(`UPDATE ai_agent_jobs SET status = 'failed', phase = 'failed', cancel_requested = 1,
        retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = ?, error_message = ?,
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE workflow_run_id = ? AND status IN ('queued','running','paused')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_agent_jobs.workflow_run_id AND w.mutation_token = ?
            AND w.status = 'failed' AND w.error_code = ?)`)
      .bind(failure.code, failure.message, mutationToken, row.id, mutationToken, failure.code),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT 'ai-agent-event-' || lower(hex(randomblob(16))), id, owner_email,
        ?, ?, 'active', status, version,
        json_object('workflowRunId', workflow_run_id)
      FROM ai_agent_jobs WHERE workflow_run_id = ? AND mutation_token = ? AND status = 'failed'`)
      .bind(failure.actor, failure.code, row.id, mutationToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, ?,
        ?, ?, status, version, json_object('failureCode', ?)
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, failure.actor, failure.code,
        row.status, failure.code, row.id, mutationToken),
  ]);
}

async function failInvalidWorkflowAdmission(
  row: {
    id: string;
    status: "queued" | "running";
    model_id: string;
    model_version: number;
    allowed_tools_json: string;
    tool_policy_digest: string;
  },
  db: D1Database,
): Promise<void> {
  const mutationToken = crypto.randomUUID();
  await db.batch([
    db.prepare(`UPDATE ai_workflow_runs SET status = 'failed', current_node_key = NULL,
        cancel_requested = 1, retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = 'executor_admission_invalid',
        error_message = '工作流执行准入快照无效，已阻止模型与工具派发',
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE id = ? AND cancel_requested = 0
        AND model_id = ? AND model_version = ? AND allowed_tools_json = ? AND tool_policy_digest = ?
        AND ((status = 'queued' AND datetime(next_run_at) <= CURRENT_TIMESTAMP)
          OR (status = 'running' AND lease_expires_at IS NOT NULL
            AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP))`)
      .bind(mutationToken, row.id, row.model_id, Number(row.model_version),
        row.allowed_tools_json, row.tool_policy_digest),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'failed',
        error_code = 'executor_admission_invalid',
        error_message = '所属工作流执行准入快照无效',
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE run_id = ? AND status IN ('pending','running','waiting_review')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id AND w.mutation_token = ?
            AND w.status = 'failed' AND w.error_code = 'executor_admission_invalid')`)
      .bind(mutationToken, row.id, mutationToken),
    db.prepare(`UPDATE ai_agent_jobs SET status = 'failed', phase = 'failed', cancel_requested = 1,
        retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = 'executor_admission_invalid',
        error_message = '所属工作流执行准入快照无效',
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ?
      WHERE workflow_run_id = ? AND status IN ('queued','running','paused')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_agent_jobs.workflow_run_id AND w.mutation_token = ?
            AND w.status = 'failed' AND w.error_code = 'executor_admission_invalid')`)
      .bind(mutationToken, row.id, mutationToken),
    db.prepare(`INSERT INTO ai_agent_events (
        id, job_id, owner_email, actor_email, event_type, from_status, to_status, job_version, details_json
      ) SELECT 'ai-agent-event-' || lower(hex(randomblob(16))), id, owner_email,
        'system:workflow-admission-selector', 'executor_admission_invalid', 'active', status, version,
        json_object('workflowRunId', workflow_run_id)
      FROM ai_agent_jobs WHERE workflow_run_id = ? AND mutation_token = ? AND status = 'failed'`)
      .bind(row.id, mutationToken),
    db.prepare(`INSERT INTO ai_workflow_events (
        id, run_id, owner_email, actor_email, event_type, from_status, to_status, run_version, details_json
      ) SELECT ?, id, owner_email, 'system:workflow-admission-selector',
        'executor_admission_invalid', ?, status, version, '{}'
      FROM ai_workflow_runs WHERE id = ? AND mutation_token = ?`)
      .bind(`ai-workflow-event-${crypto.randomUUID()}`, row.status, row.id, mutationToken),
  ]);
}

export async function digestCanonicalJson(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, sortJson(item)]),
  );
}

function normalizeOptionalModelId(value: string | null | undefined): string | null {
  if (value === undefined || value === null || value.trim() === "") return null;
  const normalized = value.trim();
  if (!/^[a-zA-Z0-9_-]{1,160}$/.test(normalized)) {
    throw new PublicApiError(400, "invalid_request", "modelId 格式无效。");
  }
  return normalized;
}
