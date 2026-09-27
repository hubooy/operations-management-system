CREATE INDEX IF NOT EXISTS market_price_snapshots_pending_image_url_idx
ON market_price_snapshots(image_url, id)
WHERE image_content_sha256='' AND image_url<>'';
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_price_snapshots_image_hash_idx
ON market_price_snapshots(image_content_sha256, category, scope, sku_code, ranking_dimension, month, id)
WHERE image_content_sha256<>'';
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_claims_job_expiry_idx
ON market_image_cache_claims(job_id, lease_expires_at, source_url);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS market_image_cache_fetching_recovery_idx
ON market_image_cache(status, updated_at, source_url)
WHERE status='fetching';
--> statement-breakpoint
PRAGMA optimize;
