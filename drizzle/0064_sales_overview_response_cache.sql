CREATE TABLE IF NOT EXISTS `sales_overview_cache_state` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `sales_revision` integer DEFAULT 1 NOT NULL,
  `erp_product_revision` integer DEFAULT 1 NOT NULL,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `sales_overview_cache_state`
  (`id`, `sales_revision`, `erp_product_revision`, `updated_at`)
  VALUES (1, 1, 1, CURRENT_TIMESTAMP);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `sales_overview_response_cache` (
  `cache_key` text PRIMARY KEY NOT NULL,
  `revision_key` text NOT NULL,
  `payload_json` text NOT NULL,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `sales_overview_response_cache_updated_idx`
  ON `sales_overview_response_cache` (`updated_at`);
