CREATE TABLE IF NOT EXISTS `ai_chat_request_receipts` (
  `id` text PRIMARY KEY NOT NULL,
  `owner_email` text NOT NULL,
  `client_request_id` text NOT NULL,
  `request_digest` text NOT NULL,
  `status` text NOT NULL CHECK (`status` IN ('processing', 'dispatched', 'succeeded', 'failed', 'unknown')),
  `model_id` text,
  `conversation_id` text,
  `assistant_message_id` text,
  `result_json` text,
  `error_code` text,
  `admitted_at` text,
  `provider_started_at` text,
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `completed_at` text,
  FOREIGN KEY (`conversation_id`) REFERENCES `ai_conversations`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `ai_chat_request_receipts_owner_client_uq`
  ON `ai_chat_request_receipts` (`owner_email`, `client_request_id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_chat_request_receipts_status_updated_idx`
  ON `ai_chat_request_receipts` (`status`, `updated_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_chat_request_receipts_conversation_idx`
  ON `ai_chat_request_receipts` (`conversation_id`, `created_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `ai_chat_provider_dispatches` (
  `id` text PRIMARY KEY NOT NULL,
  `receipt_id` text NOT NULL,
  `owner_email` text NOT NULL,
  `model_id` text NOT NULL,
  `dispatch_ordinal` integer NOT NULL CHECK (`dispatch_ordinal` > 0),
  `reserved_at` text NOT NULL,
  `provider_called_at` text,
  FOREIGN KEY (`receipt_id`) REFERENCES `ai_chat_request_receipts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `ai_chat_provider_dispatches_receipt_ordinal_uq`
  ON `ai_chat_provider_dispatches` (`receipt_id`, `dispatch_ordinal`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_chat_provider_dispatches_owner_reserved_idx`
  ON `ai_chat_provider_dispatches` (`owner_email`, `reserved_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_chat_provider_dispatches_model_reserved_idx`
  ON `ai_chat_provider_dispatches` (`model_id`, `reserved_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `ai_chat_provider_dispatches_reserved_idx`
  ON `ai_chat_provider_dispatches` (`reserved_at`);
