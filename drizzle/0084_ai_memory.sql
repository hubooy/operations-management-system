CREATE TABLE IF NOT EXISTS `ai_memory_entries` (
  `id` text PRIMARY KEY NOT NULL,
  `owner_email` text NOT NULL COLLATE NOCASE,
  `kind` text NOT NULL CHECK (`kind` IN ('preference', 'glossary', 'business_context')),
  `memory_key` text NOT NULL,
  `memory_key_normalized` text NOT NULL,
  `content` text NOT NULL,
  `content_digest` text NOT NULL,
  `scope_mode` text NOT NULL CHECK (`scope_mode` IN ('owner', 'data_scope')),
  `scope_json` text NOT NULL CHECK (json_valid(`scope_json`)),
  `scope_digest` text NOT NULL,
  `status` text NOT NULL DEFAULT 'active' CHECK (`status` IN ('active', 'archived')),
  `version` integer NOT NULL DEFAULT 1 CHECK (`version` > 0),
  `source` text NOT NULL CHECK (`source` IN ('management_ui', 'web_chat')),
  `source_conversation_id` text,
  `source_message_id` text,
  `last_operation_id` text NOT NULL,
  `created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `archived_at` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `ai_memory_entries_active_key_uq`
  ON `ai_memory_entries` (`owner_email`, `kind`, `memory_key_normalized`, `scope_digest`)
  WHERE `status` = 'active';
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_memory_entries_owner_status_updated_idx`
  ON `ai_memory_entries` (`owner_email`, `status`, `updated_at`, `id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `ai_memory_audit_logs` (
  `id` text PRIMARY KEY NOT NULL,
  `operation_id` text NOT NULL UNIQUE,
  `request_id` text NOT NULL,
  `memory_id` text NOT NULL,
  `owner_email` text NOT NULL COLLATE NOCASE,
  `actor_role` text NOT NULL CHECK (`actor_role` IN ('viewer', 'analyst', 'operator', 'admin')),
  `operation` text NOT NULL CHECK (`operation` IN ('create', 'update', 'archive', 'duplicate')),
  `status` text NOT NULL CHECK (`status` IN ('succeeded', 'duplicate')),
  `scope_digest` text NOT NULL,
  `before_digest` text,
  `after_digest` text,
  `result_version` integer NOT NULL CHECK (`result_version` > 0),
  `policy_version` text NOT NULL,
  `gate_results_json` text NOT NULL CHECK (json_valid(`gate_results_json`)),
  `created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_memory_audit_logs_owner_created_idx`
  ON `ai_memory_audit_logs` (`owner_email`, `created_at`, `id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_memory_audit_logs_memory_created_idx`
  ON `ai_memory_audit_logs` (`memory_id`, `created_at`, `id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `ai_memory_commit_guards` (
  `operation_id` text PRIMARY KEY NOT NULL,
  `audit_present` integer NOT NULL CHECK (`audit_present` = 1)
);
