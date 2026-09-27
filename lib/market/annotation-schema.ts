import type { MarketDatabase } from "@/lib/market/database";

const foundationStatements = [
  `CREATE TABLE IF NOT EXISTS market_annotation_prompt_versions (id TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL, version INTEGER NOT NULL, parent_id TEXT, source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', segments_json TEXT NOT NULL, prompt_body TEXT NOT NULL, change_note TEXT NOT NULL DEFAULT '', metrics_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, activated_by TEXT, activated_at TEXT)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_prompts_category_version_uq ON market_annotation_prompt_versions(category, version)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_prompts_active_uq ON market_annotation_prompt_versions(category) WHERE status = 'active'`,
  `CREATE TABLE IF NOT EXISTS market_annotation_jobs (id TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL, prompt_version_id TEXT NOT NULL, executor TEXT NOT NULL, model_id TEXT, local_model_name TEXT NOT NULL DEFAULT '', work_key TEXT NOT NULL DEFAULT '', reuse_status TEXT NOT NULL DEFAULT 'pending', reuse_started_at TEXT, status TEXT NOT NULL DEFAULT 'queued', total_count INTEGER NOT NULL DEFAULT 0, completed_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0, reviewed_count INTEGER NOT NULL DEFAULT 0, committed_count INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, commit_token_hash TEXT NOT NULL DEFAULT '', commit_started_at TEXT)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_jobs_category_created_idx ON market_annotation_jobs(category, created_at)`,
  `DROP INDEX IF EXISTS market_annotation_jobs_active_work_uq`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_jobs_active_work_uq ON market_annotation_jobs(work_key) WHERE work_key<>'' AND status IN ('queued','running','failed')`,
  `CREATE TABLE IF NOT EXISTS market_annotation_items (id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '', sku_code TEXT NOT NULL, ranking_dimension TEXT NOT NULL DEFAULT 'SKU', month TEXT NOT NULL DEFAULT '', image_content_sha256 TEXT NOT NULL DEFAULT '', product_name TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '', source_image_url TEXT NOT NULL DEFAULT '', resolved_image_url TEXT NOT NULL DEFAULT '', image_source TEXT NOT NULL DEFAULT 'none', status TEXT NOT NULL DEFAULT 'queued', ai_segment TEXT NOT NULL DEFAULT '', ai_image_price_cents INTEGER, ai_price_type TEXT NOT NULL DEFAULT '', ai_price_low_cents INTEGER, ai_price_high_cents INTEGER, ai_confidence_bps INTEGER, ai_reason TEXT NOT NULL DEFAULT '', ai_raw_digest TEXT NOT NULL DEFAULT '', model_input_bytes INTEGER NOT NULL DEFAULT 0, image_load_ms INTEGER NOT NULL DEFAULT 0, image_prepare_ms INTEGER NOT NULL DEFAULT 0, model_call_ms INTEGER NOT NULL DEFAULT 0, total_inference_ms INTEGER NOT NULL DEFAULT 0, reviewed_segment TEXT NOT NULL DEFAULT '', reviewed_image_price_cents INTEGER, reviewed_price_type TEXT NOT NULL DEFAULT '', reviewed_price_low_cents INTEGER, reviewed_price_high_cents INTEGER, selected INTEGER NOT NULL DEFAULT 0, reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT, lease_token_hash TEXT NOT NULL DEFAULT '', lease_agent_id TEXT NOT NULL DEFAULT '', lease_expires_at TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `DROP INDEX IF EXISTS market_annotation_items_job_sku_uq`,
  `DROP INDEX IF EXISTS market_annotation_items_job_snapshot_uq`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_items_job_snapshot_uq ON market_annotation_items(job_id, category, scope, sku_code, ranking_dimension, month, image_content_sha256)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_items_job_status_idx ON market_annotation_items(job_id, status, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_items_reuse_idx ON market_annotation_items(category, scope, sku_code, ranking_dimension, image_content_sha256, status, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_items_segment_reuse_idx ON market_annotation_items(category, scope, sku_code, ranking_dimension, status, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_items_job_inference_unit_idx ON market_annotation_items(job_id, category, scope, sku_code, ranking_dimension, image_content_sha256, status, id)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_items_lease_idx ON market_annotation_items(lease_expires_at, status)`,
  `CREATE TABLE IF NOT EXISTS market_sku_annotations (id TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL, sku_code TEXT NOT NULL, segment TEXT NOT NULL, image_price_cents INTEGER, image_url TEXT NOT NULL DEFAULT '', image_source TEXT NOT NULL DEFAULT 'none', confidence_bps INTEGER, source_job_item_id TEXT NOT NULL, prompt_version_id TEXT NOT NULL, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_sku_annotations_category_sku_uq ON market_sku_annotations(category, sku_code)`,
  `CREATE INDEX IF NOT EXISTS market_sku_annotations_segment_idx ON market_sku_annotations(category, segment, updated_at)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_commit_receipts (id TEXT PRIMARY KEY NOT NULL, job_item_id TEXT NOT NULL, annotation_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL, committed_by TEXT NOT NULL, committed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, batch_id TEXT NOT NULL DEFAULT '', request_digest TEXT NOT NULL DEFAULT '')`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_commits_item_uq ON market_annotation_commit_receipts(job_item_id)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_commits_idempotency_uq ON market_annotation_commit_receipts(idempotency_key)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_validation_samples (id TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL, sku_code TEXT NOT NULL, product_name TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '', gold_segment TEXT NOT NULL, gold_image_price_cents INTEGER, source_annotation_id TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_samples_category_sku_uq ON market_annotation_validation_samples(category, sku_code)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_validation_runs (id TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL, baseline_prompt_id TEXT, candidate_prompt_id TEXT NOT NULL, model_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', seed TEXT NOT NULL, requested_sample_count INTEGER NOT NULL DEFAULT 50, sample_count INTEGER NOT NULL DEFAULT 0, sample_hash TEXT NOT NULL DEFAULT '', metrics_json TEXT NOT NULL DEFAULT '{}', gate_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_validation_runs_prompt_idx ON market_annotation_validation_runs(candidate_prompt_id, created_at)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_validation_results (id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL, sample_id TEXT NOT NULL, prompt_version_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', predicted_segment TEXT NOT NULL DEFAULT '', predicted_image_price_cents INTEGER, confidence_bps INTEGER, is_correct INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sample_snapshot_json TEXT NOT NULL DEFAULT '{}', claim_token_hash TEXT NOT NULL DEFAULT '', lease_expires_at TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_validation_result_uq ON market_annotation_validation_results(run_id, sample_id, prompt_version_id)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_prompt_audits (id TEXT PRIMARY KEY NOT NULL, prompt_id TEXT NOT NULL, category TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_prompt_audits_prompt_idx ON market_annotation_prompt_audits(prompt_id, created_at)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_local_agents (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'enabled', capabilities_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen_at TEXT, revoked_at TEXT)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_agents_token_uq ON market_annotation_local_agents(token_hash)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_concurrency_settings (category TEXT NOT NULL, executor TEXT NOT NULL CHECK (executor IN ('cloud','local')), concurrency INTEGER NOT NULL CHECK (concurrency BETWEEN 1 AND 50), updated_by TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (category,executor))`,
  `CREATE INDEX IF NOT EXISTS market_annotation_concurrency_settings_updated_idx ON market_annotation_concurrency_settings(updated_at)`,
  `CREATE TABLE IF NOT EXISTS market_annotation_cloud_runs (job_id TEXT PRIMARY KEY NOT NULL, state TEXT NOT NULL DEFAULT 'running' CHECK (state IN ('running','paused','completed')), retry_state_json TEXT NOT NULL DEFAULT '{}', next_run_at TEXT, lease_token_hash TEXT NOT NULL DEFAULT '', lease_expires_at TEXT, last_failure_code TEXT NOT NULL DEFAULT '', last_failure_message TEXT NOT NULL DEFAULT '', last_started_at TEXT, last_heartbeat_at TEXT, completed_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_cloud_runs_ready_idx ON market_annotation_cloud_runs(state, next_run_at, lease_expires_at, updated_at)`,
] as const;

const runtimeColumnUpgrades = {
  market_annotation_jobs: {
    commit_token_hash: "TEXT NOT NULL DEFAULT ''",
    commit_started_at: "TEXT",
    work_key: "TEXT NOT NULL DEFAULT ''",
    reuse_status: "TEXT NOT NULL DEFAULT 'pending'",
    reuse_started_at: "TEXT",
  },
  market_annotation_commit_receipts: {
    batch_id: "TEXT NOT NULL DEFAULT ''",
    request_digest: "TEXT NOT NULL DEFAULT ''",
  },
  market_annotation_validation_results: {
    sample_snapshot_json: "TEXT NOT NULL DEFAULT '{}'",
    claim_token_hash: "TEXT NOT NULL DEFAULT ''",
    lease_expires_at: "TEXT",
    attempt_count: "INTEGER NOT NULL DEFAULT 0",
    updated_at: "TEXT NOT NULL DEFAULT ''",
  },
  market_annotation_items: {
    category: "TEXT NOT NULL DEFAULT ''",
    scope: "TEXT NOT NULL DEFAULT ''",
    ranking_dimension: "TEXT NOT NULL DEFAULT 'SKU'",
    month: "TEXT NOT NULL DEFAULT ''",
    image_content_sha256: "TEXT NOT NULL DEFAULT ''",
    ai_price_type: "TEXT NOT NULL DEFAULT ''",
    ai_price_low_cents: "INTEGER",
    ai_price_high_cents: "INTEGER",
    reviewed_price_type: "TEXT NOT NULL DEFAULT ''",
    reviewed_price_low_cents: "INTEGER",
    reviewed_price_high_cents: "INTEGER",
    model_input_bytes: "INTEGER NOT NULL DEFAULT 0",
    image_load_ms: "INTEGER NOT NULL DEFAULT 0",
    image_prepare_ms: "INTEGER NOT NULL DEFAULT 0",
    model_call_ms: "INTEGER NOT NULL DEFAULT 0",
    total_inference_ms: "INTEGER NOT NULL DEFAULT 0",
  },
} as const;

const upgradedIndexStatements = [
  `CREATE INDEX IF NOT EXISTS market_annotation_commits_batch_idx ON market_annotation_commit_receipts(batch_id)`,
  `CREATE INDEX IF NOT EXISTS market_annotation_validation_result_lease_idx ON market_annotation_validation_results(run_id, status, lease_expires_at)`,
] as const;

const tableStatements = foundationStatements.filter((sql) => sql.startsWith("CREATE TABLE"));
const existingColumnIndexStatements = foundationStatements.filter((sql) => sql.startsWith("CREATE INDEX") || sql.startsWith("CREATE UNIQUE INDEX") || sql.startsWith("DROP INDEX"));

const ready = new WeakMap<object, Promise<void>>();

export async function ensureAnnotationSchema(db: MarketDatabase) {
  const key = db as unknown as object;
  const previous = ready.get(key);
  if (previous) return previous;
  const setup = initializeAnnotationSchema(db).catch((error: unknown) => { ready.delete(key); throw error; });
  ready.set(key, setup);
  return setup;
}

async function initializeAnnotationSchema(db: MarketDatabase) {
  await db.batch(tableStatements.map((sql) => db.prepare(sql)));
  for (const [table, columns] of Object.entries(runtimeColumnUpgrades)) {
    for (const [column, definition] of Object.entries(columns)) await ensureColumn(db, table, column, definition);
  }
  await db.batch([...existingColumnIndexStatements, ...upgradedIndexStatements].map((sql) => db.prepare(sql)));
}

async function ensureColumn(db: MarketDatabase, table: string, column: string, definition: string) {
  if ((await listColumns(db, table)).has(column)) return;
  try {
    await db.prepare(`ALTER TABLE "${table}" ADD COLUMN "${column}" ${definition}`).run();
  } catch (error) {
    // Another isolate may have upgraded the same D1 database after our PRAGMA.
    if (!(await listColumns(db, table)).has(column)) throw error;
  }
}

async function listColumns(db: MarketDatabase, table: string) {
  const rows = await db.prepare(`PRAGMA table_info("${table}")`).all<{ name: string }>();
  return new Set((rows.results ?? []).map((row) => row.name));
}
