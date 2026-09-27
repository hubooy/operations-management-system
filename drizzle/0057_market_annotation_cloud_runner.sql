ALTER TABLE `market_annotation_items` ADD COLUMN `model_input_bytes` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `market_annotation_items` ADD COLUMN `image_load_ms` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `market_annotation_items` ADD COLUMN `image_prepare_ms` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `market_annotation_items` ADD COLUMN `model_call_ms` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `market_annotation_items` ADD COLUMN `total_inference_ms` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `market_annotation_cloud_runs` (
	`job_id` text PRIMARY KEY NOT NULL,
	`state` text NOT NULL DEFAULT 'running' CHECK (`state` IN ('running','paused','completed')),
	`retry_state_json` text NOT NULL DEFAULT '{}',
	`next_run_at` text,
	`lease_token_hash` text NOT NULL DEFAULT '',
	`lease_expires_at` text,
	`last_failure_code` text NOT NULL DEFAULT '',
	`last_failure_message` text NOT NULL DEFAULT '',
	`last_started_at` text,
	`last_heartbeat_at` text,
	`completed_at` text,
	`updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_annotation_cloud_runs_ready_idx`
	ON `market_annotation_cloud_runs` (`state`,`next_run_at`,`lease_expires_at`,`updated_at`);
--> statement-breakpoint
PRAGMA optimize;
