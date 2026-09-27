CREATE TABLE IF NOT EXISTS `ai_analysis_runs` (
  `id` text PRIMARY KEY NOT NULL,
  `owner_email` text NOT NULL COLLATE NOCASE,
  `actor_role` text NOT NULL CHECK (`actor_role` IN ('viewer', 'analyst', 'operator', 'admin')),
  `scope_json` text NOT NULL CHECK (json_valid(`scope_json`)),
  `dataset` text NOT NULL CHECK (`dataset` IN ('sales_category', 'netshop_product_daily', 'netshop_promotion')),
  `query_digest` text NOT NULL,
  `plan_digest` text NOT NULL,
  `operations_json` text NOT NULL CHECK (json_valid(`operations_json`)),
  `data_cutoff_date` text,
  `source_rows` integer NOT NULL CHECK (`source_rows` >= 0),
  `returned_rows` integer NOT NULL CHECK (`returned_rows` >= 0),
  `truncated` integer NOT NULL DEFAULT 0 CHECK (`truncated` IN (0, 1)),
  `result_digest` text NOT NULL,
  `request_id` text NOT NULL,
  `created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_analysis_runs_owner_created_idx`
  ON `ai_analysis_runs` (`owner_email`, `created_at`, `id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_analysis_runs_dataset_created_idx`
  ON `ai_analysis_runs` (`dataset`, `created_at`, `id`);
