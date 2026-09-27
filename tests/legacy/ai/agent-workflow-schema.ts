import { getD1Database, type D1Database } from "@/lib/database/d1";

export const AI_AGENT_WORKFLOW_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS ai_agent_jobs (
    id TEXT PRIMARY KEY NOT NULL, owner_email TEXT NOT NULL COLLATE NOCASE,
    client_request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)), task TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
    state_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state_json)),
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    model_id TEXT NOT NULL DEFAULT '',
    model_version INTEGER NOT NULL DEFAULT 0 CHECK (model_version >= 0),
    allowed_tools_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(allowed_tools_json)),
    tool_policy_digest TEXT NOT NULL DEFAULT '',
    provider_round_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_round_count >= 0),
    tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_count >= 0),
    provider_dispatch_started_at TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','paused','completed','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT 'queued' CHECK (phase IN ('queued','executing','paused','completed','failed','cancelled')),
    step_index INTEGER NOT NULL DEFAULT 0 CHECK (step_index BETWEEN 0 AND 64),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1), mutation_token TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0,1)),
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 16),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_token TEXT NOT NULL DEFAULT '', lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at TEXT, next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workflow_run_id TEXT, workflow_node_key TEXT,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_email, client_request_id),
    CHECK ((workflow_run_id IS NULL AND workflow_node_key IS NULL)
      OR (workflow_run_id IS NOT NULL AND workflow_node_key IS NOT NULL))
  )`,
  `CREATE INDEX IF NOT EXISTS ai_agent_jobs_owner_created_idx
    ON ai_agent_jobs (owner_email, created_at DESC, id)`,
  `CREATE INDEX IF NOT EXISTS ai_agent_jobs_runnable_idx
    ON ai_agent_jobs (status, next_run_at, created_at, id)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_agent_jobs_workflow_node_uq
    ON ai_agent_jobs (workflow_run_id, workflow_node_key)
    WHERE workflow_run_id IS NOT NULL AND workflow_node_key IS NOT NULL`,
  `CREATE TABLE IF NOT EXISTS ai_agent_checkpoints (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 64),
    kind TEXT NOT NULL CHECK (kind IN ('checkpoint','completed','paused','failed')),
    state_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state_json)),
    output_digest TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, ordinal)
  )`,
  `CREATE INDEX IF NOT EXISTS ai_agent_checkpoints_job_idx
    ON ai_agent_checkpoints (job_id, ordinal)`,
  `CREATE TABLE IF NOT EXISTS ai_agent_events (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE CASCADE,
    owner_email TEXT NOT NULL COLLATE NOCASE, actor_email TEXT NOT NULL COLLATE NOCASE,
    event_type TEXT NOT NULL, from_status TEXT NOT NULL DEFAULT '', to_status TEXT NOT NULL DEFAULT '',
    job_version INTEGER NOT NULL CHECK (job_version >= 1),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_agent_events_job_created_idx
    ON ai_agent_events (job_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_workflow_runs (
    id TEXT PRIMARY KEY NOT NULL, owner_email TEXT NOT NULL COLLATE NOCASE,
    client_request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)), name TEXT NOT NULL,
    graph_json TEXT NOT NULL CHECK (json_valid(graph_json)), graph_digest TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    model_id TEXT NOT NULL DEFAULT '',
    model_version INTEGER NOT NULL DEFAULT 0 CHECK (model_version >= 0),
    allowed_tools_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(allowed_tools_json)),
    tool_policy_digest TEXT NOT NULL DEFAULT '',
    provider_round_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_round_count >= 0),
    tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_count >= 0),
    provider_dispatch_started_at TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0,1)),
    status TEXT NOT NULL DEFAULT 'queued'
      CHECK (status IN ('queued','running','waiting_review','paused','completed','failed','cancelled')),
    current_node_key TEXT, version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    mutation_token TEXT NOT NULL DEFAULT '', cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0,1)),
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 16),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_token TEXT NOT NULL DEFAULT '', lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at TEXT, next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_email, client_request_id)
  )`,
  `CREATE INDEX IF NOT EXISTS ai_workflow_runs_owner_created_idx
    ON ai_workflow_runs (owner_email, created_at DESC, id)`,
  `CREATE INDEX IF NOT EXISTS ai_workflow_runs_runnable_idx
    ON ai_workflow_runs (status, next_run_at, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_workflow_node_runs (
    id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES ai_workflow_runs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL, position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 23),
    node_type TEXT NOT NULL CHECK (node_type IN ('agent','human_review')),
    depends_on_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(depends_on_json)), instruction TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','running','waiting_review','completed','rejected','skipped','failed','cancelled')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1), mutation_token TEXT NOT NULL DEFAULT '',
    agent_job_id TEXT REFERENCES ai_agent_jobs(id) ON DELETE SET NULL,
    reviewer_email TEXT, reviewed_at TEXT,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, node_key), UNIQUE(run_id, position)
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_workflow_node_runs_agent_job_uq
    ON ai_workflow_node_runs (agent_job_id) WHERE agent_job_id IS NOT NULL`,
  `CREATE INDEX IF NOT EXISTS ai_workflow_node_runs_run_position_idx
    ON ai_workflow_node_runs (run_id, position, status)`,
  `CREATE TABLE IF NOT EXISTS ai_workflow_events (
    id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES ai_workflow_runs(id) ON DELETE CASCADE,
    node_key TEXT, owner_email TEXT NOT NULL COLLATE NOCASE, actor_email TEXT NOT NULL COLLATE NOCASE,
    event_type TEXT NOT NULL, from_status TEXT NOT NULL DEFAULT '', to_status TEXT NOT NULL DEFAULT '',
    run_version INTEGER NOT NULL CHECK (run_version >= 1),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_workflow_events_run_created_idx
    ON ai_workflow_events (run_id, created_at, id)`,
] as const;

const AI_AGENT_WORKFLOW_LEGACY_COLUMN_UPGRADES = [
  { table: "ai_agent_jobs", column: "model_id", definition: "model_id TEXT NOT NULL DEFAULT ''" },
  { table: "ai_agent_jobs", column: "model_version", definition: "model_version INTEGER NOT NULL DEFAULT 0 CHECK (model_version >= 0)" },
  { table: "ai_agent_jobs", column: "allowed_tools_json", definition: "allowed_tools_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(allowed_tools_json))" },
  { table: "ai_agent_jobs", column: "tool_policy_digest", definition: "tool_policy_digest TEXT NOT NULL DEFAULT ''" },
  { table: "ai_agent_jobs", column: "provider_round_count", definition: "provider_round_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_round_count >= 0)" },
  { table: "ai_agent_jobs", column: "tool_call_count", definition: "tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_count >= 0)" },
  { table: "ai_agent_jobs", column: "provider_dispatch_started_at", definition: "provider_dispatch_started_at TEXT" },
  { table: "ai_workflow_runs", column: "model_id", definition: "model_id TEXT NOT NULL DEFAULT ''" },
  { table: "ai_workflow_runs", column: "model_version", definition: "model_version INTEGER NOT NULL DEFAULT 0 CHECK (model_version >= 0)" },
  { table: "ai_workflow_runs", column: "allowed_tools_json", definition: "allowed_tools_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(allowed_tools_json))" },
  { table: "ai_workflow_runs", column: "tool_policy_digest", definition: "tool_policy_digest TEXT NOT NULL DEFAULT ''" },
  { table: "ai_workflow_runs", column: "provider_round_count", definition: "provider_round_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_round_count >= 0)" },
  { table: "ai_workflow_runs", column: "tool_call_count", definition: "tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_count >= 0)" },
  { table: "ai_workflow_runs", column: "provider_dispatch_started_at", definition: "provider_dispatch_started_at TEXT" },
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

async function tableColumns(table: "ai_agent_jobs" | "ai_workflow_runs", db: D1Database) {
  const rows = await db.prepare(`PRAGMA table_info(${table})`).all<{ name: string }>();
  return new Set((rows.results ?? []).map((row) => row.name));
}

async function ensureLegacyAdmissionColumns(db: D1Database): Promise<void> {
  for (const upgrade of AI_AGENT_WORKFLOW_LEGACY_COLUMN_UPGRADES) {
    const columns = await tableColumns(upgrade.table, db);
    if (columns.has(upgrade.column)) continue;
    try {
      await db.prepare(`ALTER TABLE ${upgrade.table} ADD COLUMN ${upgrade.definition}`).run();
    } catch (error) {
      // A second isolate may have installed the same compatibility column after
      // our PRAGMA read. Re-read before treating the upgrade as failed.
      const refreshed = await tableColumns(upgrade.table, db);
      if (!refreshed.has(upgrade.column)) throw error;
    }
  }

  // The legacy schema had only a boolean executor gate and therefore contains
  // no immutable model/tool admission snapshot. Such formal work must never be
  // dispatched after the upgrade. Dry-runs remain safe and do not need one.
  await db.batch([
    db.prepare(`UPDATE ai_agent_jobs SET status = 'failed', phase = 'failed', retryable = 0,
        lease_token = '', lease_expires_at = NULL, error_code = 'executor_admission_missing',
        error_message = '历史任务缺少服务端执行准入快照，已阻止供应商派发',
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ''
      WHERE status IN ('queued','running','paused')
        AND (model_id = '' OR model_version < 1 OR tool_policy_digest = '')`),
    db.prepare(`UPDATE ai_workflow_runs SET status = 'failed', current_node_key = NULL,
        retryable = 0, lease_token = '', lease_expires_at = NULL,
        error_code = 'executor_admission_missing',
        error_message = '历史正式工作流缺少服务端执行准入快照，已阻止供应商派发',
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ''
      WHERE dry_run = 0 AND status IN ('queued','running','waiting_review','paused')
        AND (model_id = '' OR model_version < 1 OR tool_policy_digest = '')`),
    db.prepare(`UPDATE ai_workflow_node_runs SET status = 'failed',
        error_code = 'executor_admission_missing',
        error_message = '所属正式工作流缺少服务端执行准入快照',
        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP,
        version = version + 1, mutation_token = ''
      WHERE status IN ('pending','running','waiting_review')
        AND EXISTS (SELECT 1 FROM ai_workflow_runs w
          WHERE w.id = ai_workflow_node_runs.run_id
            AND w.status = 'failed' AND w.error_code = 'executor_admission_missing')`),
  ]);
}

export async function ensureAiAgentWorkflowSchema(
  db: D1Database = getD1Database(),
): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(
    AI_AGENT_WORKFLOW_SCHEMA_STATEMENTS.map((statement) => db.prepare(statement)),
  ).then(() => ensureLegacyAdmissionColumns(db)).catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}
