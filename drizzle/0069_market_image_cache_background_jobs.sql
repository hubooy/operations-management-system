CREATE TABLE IF NOT EXISTS market_image_cache_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  scope_key TEXT NOT NULL UNIQUE,
  batch_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed')),
  requested_by TEXT NOT NULL DEFAULT '',
  discovery_cursor TEXT NOT NULL DEFAULT '',
  discovery_complete INTEGER NOT NULL DEFAULT 0 CHECK (discovery_complete IN (0,1)),
  discovered_count INTEGER NOT NULL DEFAULT 0,
  completed_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  pending_count INTEGER NOT NULL DEFAULT 0,
  propagation_pending_count INTEGER NOT NULL DEFAULT 0,
  processed_count INTEGER NOT NULL DEFAULT 0,
  run_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  lease_token TEXT NOT NULL DEFAULT '',
  lease_epoch INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  next_run_at TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS market_image_cache_job_items (
  job_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','ready','completed','failed')),
  content_sha256 TEXT NOT NULL DEFAULT '',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  PRIMARY KEY (job_id, source_url)
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS market_image_cache_claims (
  source_url TEXT PRIMARY KEY NOT NULL,
  job_id TEXT NOT NULL,
  claim_token TEXT NOT NULL,
  job_lease_token TEXT NOT NULL,
  job_epoch INTEGER NOT NULL,
  attempt_count INTEGER NOT NULL,
  lease_expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS market_image_cache_item_insert_counts
AFTER INSERT ON market_image_cache_job_items
BEGIN
  UPDATE market_image_cache_jobs SET
    discovered_count=discovered_count+1,
    pending_count=pending_count+CASE WHEN NEW.status='queued' THEN 1 ELSE 0 END,
    propagation_pending_count=propagation_pending_count+CASE WHEN NEW.status='ready' THEN 1 ELSE 0 END,
    completed_count=completed_count+CASE WHEN NEW.status='completed' THEN 1 ELSE 0 END,
    failed_count=failed_count+CASE WHEN NEW.status='failed' THEN 1 ELSE 0 END,
    processed_count=processed_count+CASE WHEN NEW.status IN ('completed','failed') THEN 1 ELSE 0 END,
    updated_at=CURRENT_TIMESTAMP
  WHERE id=NEW.job_id;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS market_image_cache_item_status_counts
AFTER UPDATE OF status ON market_image_cache_job_items
WHEN OLD.status<>NEW.status
BEGIN
  UPDATE market_image_cache_jobs SET
    pending_count=MAX(0, pending_count
      + CASE WHEN NEW.status='queued' THEN 1 ELSE 0 END
      - CASE WHEN OLD.status='queued' THEN 1 ELSE 0 END),
    propagation_pending_count=MAX(0, propagation_pending_count
      + CASE WHEN NEW.status='ready' THEN 1 ELSE 0 END
      - CASE WHEN OLD.status='ready' THEN 1 ELSE 0 END),
    completed_count=MAX(0, completed_count
      + CASE WHEN NEW.status='completed' THEN 1 ELSE 0 END
      - CASE WHEN OLD.status='completed' THEN 1 ELSE 0 END),
    failed_count=MAX(0, failed_count
      + CASE WHEN NEW.status='failed' THEN 1 ELSE 0 END
      - CASE WHEN OLD.status='failed' THEN 1 ELSE 0 END),
    processed_count=MAX(0, processed_count
      + CASE WHEN NEW.status IN ('completed','failed') THEN 1 ELSE 0 END
      - CASE WHEN OLD.status IN ('completed','failed') THEN 1 ELSE 0 END),
    updated_at=CURRENT_TIMESTAMP
  WHERE id=NEW.job_id;
END;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_jobs_runnable_idx
ON market_image_cache_jobs(status, next_run_at, lease_expires_at, updated_at);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_jobs_batch_idx
ON market_image_cache_jobs(batch_id, updated_at);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_job_items_work_idx
ON market_image_cache_job_items(job_id, status, source_url);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_claims_job_expiry_idx
ON market_image_cache_claims(job_id, lease_expires_at, source_url);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_fetching_recovery_idx
ON market_image_cache(status, updated_at, source_url)
WHERE status='fetching';
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_entries_batch_image_idx
ON market_ranking_entries(last_import_batch_id, image_url)
WHERE image_url<>'';
--> statement-breakpoint
PRAGMA optimize;
