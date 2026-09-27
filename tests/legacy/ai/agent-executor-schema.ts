import { ensureAiAgentWorkflowSchema } from "@/tests/legacy/ai/agent-workflow-schema";
import { ensureAiAssistantSchema } from "@/tests/legacy/ai/assistant-service";
import { getD1Database, type D1Database } from "@/lib/database/d1";

/**
 * Immutable dispatch/result ledgers for the durable Agent executor.
 *
 * A `calling` row is the irreversible boundary. If a worker disappears before
 * the matching result is durably stored, recovery must fail the job as
 * unknown; it must never repeat that provider or tool call automatically.
 */
export const AI_AGENT_EXECUTOR_SCHEMA_STATEMENTS = [
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
    state TEXT NOT NULL DEFAULT 'calling'
      CHECK (state IN ('calling','succeeded','failed','unknown')),
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider_called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    completed_at TEXT
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_agent_provider_dispatches_job_ordinal_uq
    ON ai_agent_provider_dispatches (job_id, dispatch_ordinal)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_provider_dispatches_owner_reserved_idx
    ON ai_agent_provider_dispatches (owner_email, reserved_at)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_provider_dispatches_model_reserved_idx
    ON ai_agent_provider_dispatches (model_id, reserved_at)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_provider_dispatches_state_reserved_idx
    ON ai_agent_provider_dispatches (state, reserved_at)`,
  `CREATE TABLE IF NOT EXISTS ai_agent_provider_results (
    dispatch_id TEXT PRIMARY KEY NOT NULL
      REFERENCES ai_agent_provider_dispatches(id) ON DELETE RESTRICT,
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    response_digest TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(usage_json)),
    provider_request_id TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS ai_agent_tool_dispatches (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE RESTRICT,
    provider_dispatch_id TEXT NOT NULL
      REFERENCES ai_agent_provider_dispatches(id) ON DELETE RESTRICT,
    tool_call_ordinal INTEGER NOT NULL CHECK (tool_call_ordinal BETWEEN 1 AND 40),
    provider_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL CHECK (json_valid(arguments_json)),
    arguments_digest TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'calling'
      CHECK (state IN ('calling','succeeded','failed','unknown')),
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tool_called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    completed_at TEXT
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_agent_tool_dispatches_job_ordinal_uq
    ON ai_agent_tool_dispatches (job_id, tool_call_ordinal)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_agent_tool_dispatches_provider_call_uq
    ON ai_agent_tool_dispatches (provider_dispatch_id, provider_call_id)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_tool_dispatches_job_state_idx
    ON ai_agent_tool_dispatches (job_id, state, tool_call_ordinal)`,
  `CREATE TABLE IF NOT EXISTS ai_agent_tool_results (
    tool_dispatch_id TEXT PRIMARY KEY NOT NULL
      REFERENCES ai_agent_tool_dispatches(id) ON DELETE RESTRICT,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    result_digest TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export async function ensureAiAgentExecutorSchema(
  db: D1Database = getD1Database(),
): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = ensureAiAssistantSchema(db)
    .then(() => ensureAiAgentWorkflowSchema(db))
    .then(() => db.batch(
    AI_AGENT_EXECUTOR_SCHEMA_STATEMENTS.map((statement) => db.prepare(statement)),
  )).then(() => undefined).catch((error: unknown) => {
    schemaReadyByDatabase.delete(key);
    throw error;
  });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}
