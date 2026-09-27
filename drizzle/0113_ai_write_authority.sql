-- Operator-only. Intentionally excluded from the Drizzle journal.
-- Freezes the D1 39 AI domain tables authority before PostgreSQL activation.
CREATE TABLE IF NOT EXISTS `ai_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id`=1),
  `owner` text NOT NULL CHECK (`owner` IN ('legacy','pending','postgresql')),
  `epoch` integer NOT NULL DEFAULT 1 CHECK (`epoch`>=1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `ai_write_authority` (`id`,`owner`,`epoch`,`cutover_id`)
SELECT 1,'legacy',1,'' WHERE NOT EXISTS (
  SELECT 1 FROM `ai_write_authority` WHERE `id`=1
);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_authority_no_recreate`
BEFORE INSERT ON `ai_write_authority`
WHEN EXISTS (SELECT 1 FROM `ai_write_authority` WHERE `id`=1)
BEGIN SELECT RAISE(ABORT,'ai_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_authority_no_delete`
BEFORE DELETE ON `ai_write_authority`
BEGIN SELECT RAISE(ABORT,'ai_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_authority_transition_guard`
BEFORE UPDATE ON `ai_write_authority`
WHEN NEW.`id`<>1 OR OLD.`id`<>1 OR NEW.`epoch`<>OLD.`epoch`+1 OR NEW.`cutover_id`=''
  OR NOT ((OLD.`owner`='legacy' AND NEW.`owner`='pending')
    OR (OLD.`owner`='pending' AND NEW.`owner`='postgresql' AND NEW.`cutover_id`=OLD.`cutover_id`))
BEGIN SELECT RAISE(ABORT,'ai_authority_invalid_transition'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_checkpoints_authority_insert_guard` BEFORE INSERT ON `ai_agent_checkpoints`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_checkpoints_authority_update_guard` BEFORE UPDATE ON `ai_agent_checkpoints`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_checkpoints_authority_delete_guard` BEFORE DELETE ON `ai_agent_checkpoints`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_events_authority_insert_guard` BEFORE INSERT ON `ai_agent_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_events_authority_update_guard` BEFORE UPDATE ON `ai_agent_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_events_authority_delete_guard` BEFORE DELETE ON `ai_agent_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_jobs_authority_insert_guard` BEFORE INSERT ON `ai_agent_jobs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_jobs_authority_update_guard` BEFORE UPDATE ON `ai_agent_jobs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_jobs_authority_delete_guard` BEFORE DELETE ON `ai_agent_jobs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_provider_dispatches_authority_insert_guard` BEFORE INSERT ON `ai_agent_provider_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_provider_dispatches_authority_update_guard` BEFORE UPDATE ON `ai_agent_provider_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_provider_dispatches_authority_delete_guard` BEFORE DELETE ON `ai_agent_provider_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_provider_results_authority_insert_guard` BEFORE INSERT ON `ai_agent_provider_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_provider_results_authority_update_guard` BEFORE UPDATE ON `ai_agent_provider_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_provider_results_authority_delete_guard` BEFORE DELETE ON `ai_agent_provider_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_tool_dispatches_authority_insert_guard` BEFORE INSERT ON `ai_agent_tool_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_tool_dispatches_authority_update_guard` BEFORE UPDATE ON `ai_agent_tool_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_tool_dispatches_authority_delete_guard` BEFORE DELETE ON `ai_agent_tool_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_tool_results_authority_insert_guard` BEFORE INSERT ON `ai_agent_tool_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_tool_results_authority_update_guard` BEFORE UPDATE ON `ai_agent_tool_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_agent_tool_results_authority_delete_guard` BEFORE DELETE ON `ai_agent_tool_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_analysis_runs_authority_insert_guard` BEFORE INSERT ON `ai_analysis_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_analysis_runs_authority_update_guard` BEFORE UPDATE ON `ai_analysis_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_analysis_runs_authority_delete_guard` BEFORE DELETE ON `ai_analysis_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_artifact_deliveries_authority_insert_guard` BEFORE INSERT ON `ai_artifact_deliveries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_artifact_deliveries_authority_update_guard` BEFORE UPDATE ON `ai_artifact_deliveries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_artifact_deliveries_authority_delete_guard` BEFORE DELETE ON `ai_artifact_deliveries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_artifacts_authority_insert_guard` BEFORE INSERT ON `ai_artifacts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_artifacts_authority_update_guard` BEFORE UPDATE ON `ai_artifacts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_artifacts_authority_delete_guard` BEFORE DELETE ON `ai_artifacts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_channel_callback_events_authority_insert_guard` BEFORE INSERT ON `ai_channel_callback_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_channel_callback_events_authority_update_guard` BEFORE UPDATE ON `ai_channel_callback_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_channel_callback_events_authority_delete_guard` BEFORE DELETE ON `ai_channel_callback_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_channels_authority_insert_guard` BEFORE INSERT ON `ai_channels`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_channels_authority_update_guard` BEFORE UPDATE ON `ai_channels`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_channels_authority_delete_guard` BEFORE DELETE ON `ai_channels`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_chat_provider_dispatches_authority_insert_guard` BEFORE INSERT ON `ai_chat_provider_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_chat_provider_dispatches_authority_update_guard` BEFORE UPDATE ON `ai_chat_provider_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_chat_provider_dispatches_authority_delete_guard` BEFORE DELETE ON `ai_chat_provider_dispatches`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_chat_request_receipts_authority_insert_guard` BEFORE INSERT ON `ai_chat_request_receipts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_chat_request_receipts_authority_update_guard` BEFORE UPDATE ON `ai_chat_request_receipts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_chat_request_receipts_authority_delete_guard` BEFORE DELETE ON `ai_chat_request_receipts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_deletion_audits_authority_insert_guard` BEFORE INSERT ON `ai_conversation_deletion_audits`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_deletion_audits_authority_update_guard` BEFORE UPDATE ON `ai_conversation_deletion_audits`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_deletion_audits_authority_delete_guard` BEFORE DELETE ON `ai_conversation_deletion_audits`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_messages_authority_insert_guard` BEFORE INSERT ON `ai_conversation_messages`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_messages_authority_update_guard` BEFORE UPDATE ON `ai_conversation_messages`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_messages_authority_delete_guard` BEFORE DELETE ON `ai_conversation_messages`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_scopes_authority_insert_guard` BEFORE INSERT ON `ai_conversation_scopes`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_scopes_authority_update_guard` BEFORE UPDATE ON `ai_conversation_scopes`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversation_scopes_authority_delete_guard` BEFORE DELETE ON `ai_conversation_scopes`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversations_authority_insert_guard` BEFORE INSERT ON `ai_conversations`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversations_authority_update_guard` BEFORE UPDATE ON `ai_conversations`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_conversations_authority_delete_guard` BEFORE DELETE ON `ai_conversations`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_knowledge_entries_authority_insert_guard` BEFORE INSERT ON `ai_knowledge_entries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_knowledge_entries_authority_update_guard` BEFORE UPDATE ON `ai_knowledge_entries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_knowledge_entries_authority_delete_guard` BEFORE DELETE ON `ai_knowledge_entries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_audit_logs_authority_insert_guard` BEFORE INSERT ON `ai_memory_audit_logs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_audit_logs_authority_update_guard` BEFORE UPDATE ON `ai_memory_audit_logs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_audit_logs_authority_delete_guard` BEFORE DELETE ON `ai_memory_audit_logs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_commit_guards_authority_insert_guard` BEFORE INSERT ON `ai_memory_commit_guards`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_commit_guards_authority_update_guard` BEFORE UPDATE ON `ai_memory_commit_guards`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_commit_guards_authority_delete_guard` BEFORE DELETE ON `ai_memory_commit_guards`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_entries_authority_insert_guard` BEFORE INSERT ON `ai_memory_entries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_entries_authority_update_guard` BEFORE UPDATE ON `ai_memory_entries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_memory_entries_authority_delete_guard` BEFORE DELETE ON `ai_memory_entries`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_models_authority_insert_guard` BEFORE INSERT ON `ai_models`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_models_authority_update_guard` BEFORE UPDATE ON `ai_models`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_models_authority_delete_guard` BEFORE DELETE ON `ai_models`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_admin_audits_authority_insert_guard` BEFORE INSERT ON `ai_space_admin_audits`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_admin_audits_authority_update_guard` BEFORE UPDATE ON `ai_space_admin_audits`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_admin_audits_authority_delete_guard` BEFORE DELETE ON `ai_space_admin_audits`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_asset_cleanup_queue_authority_insert_guard` BEFORE INSERT ON `ai_space_asset_cleanup_queue`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_asset_cleanup_queue_authority_update_guard` BEFORE UPDATE ON `ai_space_asset_cleanup_queue`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_asset_cleanup_queue_authority_delete_guard` BEFORE DELETE ON `ai_space_asset_cleanup_queue`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_asset_favorites_authority_insert_guard` BEFORE INSERT ON `ai_space_asset_favorites`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_asset_favorites_authority_update_guard` BEFORE UPDATE ON `ai_space_asset_favorites`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_asset_favorites_authority_delete_guard` BEFORE DELETE ON `ai_space_asset_favorites`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_assets_authority_insert_guard` BEFORE INSERT ON `ai_space_assets`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_assets_authority_update_guard` BEFORE UPDATE ON `ai_space_assets`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_assets_authority_delete_guard` BEFORE DELETE ON `ai_space_assets`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_dispatch_receipts_authority_insert_guard` BEFORE INSERT ON `ai_space_dispatch_receipts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_dispatch_receipts_authority_update_guard` BEFORE UPDATE ON `ai_space_dispatch_receipts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_dispatch_receipts_authority_delete_guard` BEFORE DELETE ON `ai_space_dispatch_receipts`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_dispatch_results_authority_insert_guard` BEFORE INSERT ON `ai_space_dispatch_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_dispatch_results_authority_update_guard` BEFORE UPDATE ON `ai_space_dispatch_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_dispatch_results_authority_delete_guard` BEFORE DELETE ON `ai_space_dispatch_results`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_job_items_authority_insert_guard` BEFORE INSERT ON `ai_space_job_items`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_job_items_authority_update_guard` BEFORE UPDATE ON `ai_space_job_items`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_job_items_authority_delete_guard` BEFORE DELETE ON `ai_space_job_items`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_jobs_authority_insert_guard` BEFORE INSERT ON `ai_space_jobs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_jobs_authority_update_guard` BEFORE UPDATE ON `ai_space_jobs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_jobs_authority_delete_guard` BEFORE DELETE ON `ai_space_jobs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_model_profiles_authority_insert_guard` BEFORE INSERT ON `ai_space_model_profiles`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_model_profiles_authority_update_guard` BEFORE UPDATE ON `ai_space_model_profiles`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_model_profiles_authority_delete_guard` BEFORE DELETE ON `ai_space_model_profiles`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_schema_upgrades_authority_insert_guard` BEFORE INSERT ON `ai_space_schema_upgrades`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_schema_upgrades_authority_update_guard` BEFORE UPDATE ON `ai_space_schema_upgrades`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_schema_upgrades_authority_delete_guard` BEFORE DELETE ON `ai_space_schema_upgrades`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_templates_authority_insert_guard` BEFORE INSERT ON `ai_space_templates`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_templates_authority_update_guard` BEFORE UPDATE ON `ai_space_templates`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_space_templates_authority_delete_guard` BEFORE DELETE ON `ai_space_templates`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_system_settings_authority_insert_guard` BEFORE INSERT ON `ai_system_settings`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_system_settings_authority_update_guard` BEFORE UPDATE ON `ai_system_settings`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_system_settings_authority_delete_guard` BEFORE DELETE ON `ai_system_settings`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_tool_audit_logs_authority_insert_guard` BEFORE INSERT ON `ai_tool_audit_logs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_tool_audit_logs_authority_update_guard` BEFORE UPDATE ON `ai_tool_audit_logs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_tool_audit_logs_authority_delete_guard` BEFORE DELETE ON `ai_tool_audit_logs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_events_authority_insert_guard` BEFORE INSERT ON `ai_workflow_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_events_authority_update_guard` BEFORE UPDATE ON `ai_workflow_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_events_authority_delete_guard` BEFORE DELETE ON `ai_workflow_events`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_node_runs_authority_insert_guard` BEFORE INSERT ON `ai_workflow_node_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_node_runs_authority_update_guard` BEFORE UPDATE ON `ai_workflow_node_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_node_runs_authority_delete_guard` BEFORE DELETE ON `ai_workflow_node_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_runs_authority_insert_guard` BEFORE INSERT ON `ai_workflow_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_runs_authority_update_guard` BEFORE UPDATE ON `ai_workflow_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `ai_workflow_runs_authority_delete_guard` BEFORE DELETE ON `ai_workflow_runs`
WHEN COALESCE((SELECT owner FROM ai_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'ai_authority_not_legacy'); END;--> statement-breakpoint
