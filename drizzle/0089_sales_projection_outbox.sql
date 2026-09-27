CREATE TABLE IF NOT EXISTS `sales_projection_source_state` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `source_epoch` text NOT NULL,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `sales_projection_source_state`
  (`id`, `source_epoch`, `created_at`, `updated_at`)
  VALUES (1, lower(hex(randomblob(16))), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `sales_projection_outbox` (
  `event_sequence` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `event_id` text NOT NULL,
  `source_epoch` text NOT NULL,
  `domain` text NOT NULL CHECK (`domain` IN ('sales', 'erp')),
  `operation` text NOT NULL CHECK (`operation` IN ('replace_scope', 'replace_all')),
  `scope_json` text NOT NULL,
  `source_batch_id` text NOT NULL,
  `sales_revision` integer NOT NULL CHECK (`sales_revision` >= 1),
  `erp_revision` integer NOT NULL CHECK (`erp_revision` >= 1),
  `row_count` integer NOT NULL CHECK (`row_count` >= 0),
  `content_hash` text NOT NULL,
  `canonical_format_version` text NOT NULL,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `sales_projection_outbox_event_id_uq`
  ON `sales_projection_outbox` (`event_id`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `sales_projection_outbox_domain_sequence_idx`
  ON `sales_projection_outbox` (`domain`, `event_sequence`);
