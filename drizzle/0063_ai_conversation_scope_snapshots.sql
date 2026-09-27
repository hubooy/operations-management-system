CREATE TABLE IF NOT EXISTS `ai_conversation_scopes` (
  `conversation_id` text PRIMARY KEY NOT NULL REFERENCES `ai_conversations`(`id`) ON DELETE CASCADE,
  `scope_json` text NOT NULL,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_conversation_scopes_scope_created_idx`
  ON `ai_conversation_scopes` (`scope_json`, `created_at`);
