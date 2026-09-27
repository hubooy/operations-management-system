CREATE TABLE IF NOT EXISTS ai_agent_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  client_request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
  task TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
  state_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state_json)),
  output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')),
  phase TEXT NOT NULL DEFAULT 'queued'
    CHECK (phase IN ('queued', 'executing', 'paused', 'completed', 'failed', 'cancelled')),
  step_index INTEGER NOT NULL DEFAULT 0 CHECK (step_index BETWEEN 0 AND 64),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  mutation_token TEXT NOT NULL DEFAULT '',
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
  retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
  resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 16),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token TEXT NOT NULL DEFAULT '',
  lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
  lease_expires_at TEXT,
  next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  workflow_run_id TEXT,
  workflow_node_key TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (owner_email, client_request_id),
  CHECK ((workflow_run_id IS NULL AND workflow_node_key IS NULL)
    OR (workflow_run_id IS NOT NULL AND workflow_node_key IS NOT NULL))
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_agent_jobs_owner_created_idx
  ON ai_agent_jobs (owner_email, created_at DESC, id);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_agent_jobs_runnable_idx
  ON ai_agent_jobs (status, next_run_at, created_at, id);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS ai_agent_jobs_workflow_node_uq
  ON ai_agent_jobs (workflow_run_id, workflow_node_key)
  WHERE workflow_run_id IS NOT NULL AND workflow_node_key IS NOT NULL;
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS ai_agent_checkpoints (
  id TEXT PRIMARY KEY NOT NULL,
  job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 64),
  kind TEXT NOT NULL CHECK (kind IN ('checkpoint', 'completed', 'paused', 'failed')),
  state_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state_json)),
  output_digest TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (job_id, ordinal)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_agent_checkpoints_job_idx
  ON ai_agent_checkpoints (job_id, ordinal);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS ai_agent_events (
  id TEXT PRIMARY KEY NOT NULL,
  job_id TEXT NOT NULL REFERENCES ai_agent_jobs(id) ON DELETE CASCADE,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  actor_email TEXT NOT NULL COLLATE NOCASE,
  event_type TEXT NOT NULL,
  from_status TEXT NOT NULL DEFAULT '',
  to_status TEXT NOT NULL DEFAULT '',
  job_version INTEGER NOT NULL CHECK (job_version >= 1),
  details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_agent_events_job_created_idx
  ON ai_agent_events (job_id, created_at, id);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS ai_workflow_runs (
  id TEXT PRIMARY KEY NOT NULL,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  client_request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
  name TEXT NOT NULL,
  graph_json TEXT NOT NULL CHECK (json_valid(graph_json)),
  graph_digest TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
  output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
  dry_run INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'waiting_review', 'paused', 'completed', 'failed', 'cancelled')),
  current_node_key TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  mutation_token TEXT NOT NULL DEFAULT '',
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
  retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
  resume_count INTEGER NOT NULL DEFAULT 0 CHECK (resume_count BETWEEN 0 AND 16),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  lease_token TEXT NOT NULL DEFAULT '',
  lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
  lease_expires_at TEXT,
  next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (owner_email, client_request_id)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_workflow_runs_owner_created_idx
  ON ai_workflow_runs (owner_email, created_at DESC, id);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_workflow_runs_runnable_idx
  ON ai_workflow_runs (status, next_run_at, created_at, id);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS ai_workflow_node_runs (
  id TEXT PRIMARY KEY NOT NULL,
  run_id TEXT NOT NULL REFERENCES ai_workflow_runs(id) ON DELETE CASCADE,
  node_key TEXT NOT NULL,
  position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 23),
  node_type TEXT NOT NULL CHECK (node_type IN ('agent', 'human_review')),
  depends_on_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(depends_on_json)),
  instruction TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_json)),
  output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'waiting_review', 'completed', 'rejected', 'skipped', 'failed', 'cancelled')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  mutation_token TEXT NOT NULL DEFAULT '',
  agent_job_id TEXT REFERENCES ai_agent_jobs(id) ON DELETE SET NULL,
  reviewer_email TEXT,
  reviewed_at TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (run_id, node_key),
  UNIQUE (run_id, position)
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS ai_workflow_node_runs_agent_job_uq
  ON ai_workflow_node_runs (agent_job_id) WHERE agent_job_id IS NOT NULL;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_workflow_node_runs_run_position_idx
  ON ai_workflow_node_runs (run_id, position, status);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS ai_workflow_events (
  id TEXT PRIMARY KEY NOT NULL,
  run_id TEXT NOT NULL REFERENCES ai_workflow_runs(id) ON DELETE CASCADE,
  node_key TEXT,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  actor_email TEXT NOT NULL COLLATE NOCASE,
  event_type TEXT NOT NULL,
  from_status TEXT NOT NULL DEFAULT '',
  to_status TEXT NOT NULL DEFAULT '',
  run_version INTEGER NOT NULL CHECK (run_version >= 1),
  details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS ai_workflow_events_run_created_idx
  ON ai_workflow_events (run_id, created_at, id);
