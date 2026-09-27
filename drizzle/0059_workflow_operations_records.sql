CREATE TABLE IF NOT EXISTS `workflow_operation_records` (
  `id` text PRIMARY KEY NOT NULL,
  `record_type` text NOT NULL CHECK (`record_type` IN ('inspection', 'review', 'launch')),
  `title` text NOT NULL,
  `status` text NOT NULL,
  `priority` text DEFAULT 'normal' NOT NULL CHECK (`priority` IN ('high', 'normal', 'low')),
  `platform` text DEFAULT '' NOT NULL,
  `channel` text DEFAULT '' NOT NULL,
  `shop_name` text NOT NULL,
  `owner` text DEFAULT '' NOT NULL,
  `occurred_at` text NOT NULL,
  `due_at` text,
  `content` text DEFAULT '' NOT NULL,
  `source` text DEFAULT 'manual' NOT NULL CHECK (`source` IN ('manual', 'system', 'import', 'integration')),
  `source_ref` text DEFAULT '' NOT NULL,
  `reference_code` text DEFAULT '' NOT NULL,
  `version` integer DEFAULT 1 NOT NULL CHECK (`version` >= 1),
  `mutation_token` text DEFAULT '' NOT NULL,
  `created_by` text NOT NULL,
  `updated_by` text NOT NULL,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `deleted_at` text,
  `deleted_by` text
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_operation_records_type_status_time_idx`
  ON `workflow_operation_records` (`record_type`,`status`,`occurred_at`,`id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_operation_records_shop_type_time_idx`
  ON `workflow_operation_records` (`shop_name`,`record_type`,`occurred_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_operation_activities` (
  `id` text PRIMARY KEY NOT NULL,
  `record_id` text NOT NULL,
  `action` text NOT NULL CHECK (`action` IN ('created', 'updated', 'status_changed', 'deleted')),
  `actor_email` text NOT NULL,
  `actor_role` text NOT NULL,
  `from_version` integer,
  `to_version` integer NOT NULL,
  `detail_json` text DEFAULT '{}' NOT NULL,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_operation_activities_record_created_idx`
  ON `workflow_operation_activities` (`record_id`,`to_version`,`id`);
