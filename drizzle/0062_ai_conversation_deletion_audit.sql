CREATE INDEX IF NOT EXISTS `ai_conversations_updated_idx`
  ON `ai_conversations` (`updated_at`, `id`);

CREATE TABLE IF NOT EXISTS `ai_conversation_deletion_audits` (
  `audit_id` text PRIMARY KEY NOT NULL,
  `conversation_id` text NOT NULL UNIQUE,
  `conversation_owner` text NOT NULL,
  `actor_email` text NOT NULL,
  `actor_role` text NOT NULL CHECK (`actor_role` IN ('viewer', 'analyst', 'operator', 'admin')),
  `reason` text NOT NULL,
  `deleted_message_count` integer DEFAULT 0 NOT NULL,
  `deleted_artifact_count` integer DEFAULT 0 NOT NULL,
  `deleted_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS `ai_conversation_deletion_audits_actor_deleted_idx`
  ON `ai_conversation_deletion_audits` (`actor_email`, `deleted_at`);
