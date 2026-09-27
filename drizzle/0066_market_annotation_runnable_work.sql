DROP INDEX IF EXISTS market_annotation_jobs_active_work_uq;
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS market_annotation_jobs_active_work_uq
ON market_annotation_jobs(work_key)
WHERE work_key<>'' AND status IN ('queued','running','failed');
