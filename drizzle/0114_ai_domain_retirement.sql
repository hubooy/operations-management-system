-- Operator-only terminal retirement for the D1 ai-assistant domain.
-- Install an exact approved domain_retirement_receipts row in the same
-- BEGIN IMMEDIATE transaction before applying this script.
-- Intentionally absent from the Drizzle journal.
CREATE TABLE IF NOT EXISTS `domain_retirement_receipts` (
  `domain` text PRIMARY KEY NOT NULL,
  `version` text NOT NULL,
  `status` text NOT NULL CHECK (`status` IN ('approved','completed')),
  `cutover_id` text NOT NULL,
  `plan_id` text NOT NULL,
  `attestation_sha256` text NOT NULL,
  `smoke_receipt_sha256` text NOT NULL,
  `preflight_evidence_sha256` text NOT NULL,
  `migration_sha256` text NOT NULL,
  `audit_id` text NOT NULL,
  `preserved_evidence_sha256` text NOT NULL,
  `created_at` text NOT NULL,
  `completed_at` text,
  CHECK ((`status`='approved' AND `completed_at` IS NULL)
    OR (`status`='completed' AND `completed_at` IS NOT NULL))
);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `domain_retirement_receipts_insert_guard`
BEFORE INSERT ON `domain_retirement_receipts`
WHEN NEW.`status`<>'approved' OR NEW.`completed_at` IS NOT NULL
  OR EXISTS (SELECT 1 FROM `domain_retirement_receipts` WHERE `domain`=NEW.`domain`)
BEGIN SELECT RAISE(ABORT,'domain_retirement_receipt_insert_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `domain_retirement_receipts_transition_guard`
BEFORE UPDATE ON `domain_retirement_receipts`
WHEN NOT (
  OLD.`status`='approved' AND NEW.`status`='completed'
  AND OLD.`domain`=NEW.`domain` AND OLD.`version`=NEW.`version`
  AND OLD.`cutover_id`=NEW.`cutover_id` AND OLD.`plan_id`=NEW.`plan_id`
  AND OLD.`attestation_sha256`=NEW.`attestation_sha256`
  AND OLD.`smoke_receipt_sha256`=NEW.`smoke_receipt_sha256`
  AND OLD.`preflight_evidence_sha256`=NEW.`preflight_evidence_sha256`
  AND OLD.`migration_sha256`=NEW.`migration_sha256`
  AND OLD.`audit_id`=NEW.`audit_id`
  AND OLD.`preserved_evidence_sha256`=NEW.`preserved_evidence_sha256`
  AND OLD.`created_at`=NEW.`created_at`
  AND OLD.`completed_at` IS NULL AND NEW.`completed_at` IS NOT NULL
)
BEGIN SELECT RAISE(ABORT,'domain_retirement_receipt_update_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `domain_retirement_receipts_no_delete`
BEFORE DELETE ON `domain_retirement_receipts`
BEGIN SELECT RAISE(ABORT,'domain_retirement_receipt_delete_forbidden'); END;--> statement-breakpoint
SELECT CASE WHEN (
  (SELECT COUNT(*) FROM `ai_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='ai-assistant'
      AND `version`='ai-assistant-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `ai_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_checkpoints_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_checkpoints_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_checkpoints_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_events_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_events_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_events_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_jobs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_jobs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_jobs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_provider_dispatches_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_provider_dispatches_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_provider_dispatches_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_provider_results_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_provider_results_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_provider_results_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_tool_dispatches_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_tool_dispatches_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_tool_dispatches_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_tool_results_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_tool_results_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_agent_tool_results_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_analysis_runs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_analysis_runs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_analysis_runs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_artifact_deliveries_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_artifact_deliveries_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_artifact_deliveries_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_artifacts_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_artifacts_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_artifacts_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_channel_callback_events_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_channel_callback_events_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_channel_callback_events_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_channels_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_channels_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_channels_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_chat_provider_dispatches_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_chat_provider_dispatches_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_chat_provider_dispatches_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_chat_request_receipts_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_chat_request_receipts_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_chat_request_receipts_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_deletion_audits_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_deletion_audits_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_deletion_audits_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_messages_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_messages_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_messages_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_scopes_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_scopes_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversation_scopes_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversations_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversations_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_conversations_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_knowledge_entries_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_knowledge_entries_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_knowledge_entries_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_audit_logs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_audit_logs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_audit_logs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_commit_guards_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_commit_guards_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_commit_guards_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_entries_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_entries_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_memory_entries_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_models_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_models_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_models_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_admin_audits_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_admin_audits_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_admin_audits_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_asset_cleanup_queue_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_asset_cleanup_queue_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_asset_cleanup_queue_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_asset_favorites_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_asset_favorites_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_asset_favorites_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_assets_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_assets_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_assets_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_dispatch_receipts_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_dispatch_receipts_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_dispatch_receipts_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_dispatch_results_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_dispatch_results_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_dispatch_results_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_job_items_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_job_items_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_job_items_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_jobs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_jobs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_jobs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_model_profiles_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_model_profiles_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_model_profiles_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_schema_upgrades_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_schema_upgrades_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_schema_upgrades_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_templates_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_templates_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_space_templates_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_system_settings_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_system_settings_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_system_settings_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_tool_audit_logs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_tool_audit_logs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_tool_audit_logs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_events_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_events_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_events_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_node_runs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_node_runs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_node_runs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_runs_authority_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_runs_authority_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_workflow_runs_authority_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_authority_no_recreate`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_authority_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `ai_authority_transition_guard`;--> statement-breakpoint
DELETE FROM `ai_space_asset_favorites`;--> statement-breakpoint
DROP TABLE `ai_space_asset_favorites`;--> statement-breakpoint
DELETE FROM `ai_agent_tool_results`;--> statement-breakpoint
DROP TABLE `ai_agent_tool_results`;--> statement-breakpoint
DELETE FROM `ai_space_assets`;--> statement-breakpoint
DROP TABLE `ai_space_assets`;--> statement-breakpoint
DELETE FROM `ai_chat_provider_dispatches`;--> statement-breakpoint
DROP TABLE `ai_chat_provider_dispatches`;--> statement-breakpoint
DELETE FROM `ai_agent_tool_dispatches`;--> statement-breakpoint
DROP TABLE `ai_agent_tool_dispatches`;--> statement-breakpoint
DELETE FROM `ai_agent_provider_results`;--> statement-breakpoint
DROP TABLE `ai_agent_provider_results`;--> statement-breakpoint
DELETE FROM `ai_workflow_node_runs`;--> statement-breakpoint
DROP TABLE `ai_workflow_node_runs`;--> statement-breakpoint
DELETE FROM `ai_workflow_events`;--> statement-breakpoint
DROP TABLE `ai_workflow_events`;--> statement-breakpoint
DELETE FROM `ai_space_templates`;--> statement-breakpoint
DROP TABLE `ai_space_templates`;--> statement-breakpoint
DELETE FROM `ai_space_job_items`;--> statement-breakpoint
DROP TABLE `ai_space_job_items`;--> statement-breakpoint
DELETE FROM `ai_space_dispatch_results`;--> statement-breakpoint
DROP TABLE `ai_space_dispatch_results`;--> statement-breakpoint
DELETE FROM `ai_conversation_scopes`;--> statement-breakpoint
DROP TABLE `ai_conversation_scopes`;--> statement-breakpoint
DELETE FROM `ai_chat_request_receipts`;--> statement-breakpoint
DROP TABLE `ai_chat_request_receipts`;--> statement-breakpoint
DELETE FROM `ai_agent_provider_dispatches`;--> statement-breakpoint
DROP TABLE `ai_agent_provider_dispatches`;--> statement-breakpoint
DELETE FROM `ai_agent_events`;--> statement-breakpoint
DROP TABLE `ai_agent_events`;--> statement-breakpoint
DELETE FROM `ai_agent_checkpoints`;--> statement-breakpoint
DROP TABLE `ai_agent_checkpoints`;--> statement-breakpoint
DELETE FROM `ai_workflow_runs`;--> statement-breakpoint
DROP TABLE `ai_workflow_runs`;--> statement-breakpoint
DELETE FROM `ai_tool_audit_logs`;--> statement-breakpoint
DROP TABLE `ai_tool_audit_logs`;--> statement-breakpoint
DELETE FROM `ai_system_settings`;--> statement-breakpoint
DROP TABLE `ai_system_settings`;--> statement-breakpoint
DELETE FROM `ai_space_schema_upgrades`;--> statement-breakpoint
DROP TABLE `ai_space_schema_upgrades`;--> statement-breakpoint
DELETE FROM `ai_space_model_profiles`;--> statement-breakpoint
DROP TABLE `ai_space_model_profiles`;--> statement-breakpoint
DELETE FROM `ai_space_jobs`;--> statement-breakpoint
DROP TABLE `ai_space_jobs`;--> statement-breakpoint
DELETE FROM `ai_space_dispatch_receipts`;--> statement-breakpoint
DROP TABLE `ai_space_dispatch_receipts`;--> statement-breakpoint
DELETE FROM `ai_space_asset_cleanup_queue`;--> statement-breakpoint
DROP TABLE `ai_space_asset_cleanup_queue`;--> statement-breakpoint
DELETE FROM `ai_space_admin_audits`;--> statement-breakpoint
DROP TABLE `ai_space_admin_audits`;--> statement-breakpoint
DELETE FROM `ai_models`;--> statement-breakpoint
DROP TABLE `ai_models`;--> statement-breakpoint
DELETE FROM `ai_memory_entries`;--> statement-breakpoint
DROP TABLE `ai_memory_entries`;--> statement-breakpoint
DELETE FROM `ai_memory_commit_guards`;--> statement-breakpoint
DROP TABLE `ai_memory_commit_guards`;--> statement-breakpoint
DELETE FROM `ai_memory_audit_logs`;--> statement-breakpoint
DROP TABLE `ai_memory_audit_logs`;--> statement-breakpoint
DELETE FROM `ai_knowledge_entries`;--> statement-breakpoint
DROP TABLE `ai_knowledge_entries`;--> statement-breakpoint
DELETE FROM `ai_conversations`;--> statement-breakpoint
DROP TABLE `ai_conversations`;--> statement-breakpoint
DELETE FROM `ai_conversation_messages`;--> statement-breakpoint
DROP TABLE `ai_conversation_messages`;--> statement-breakpoint
DELETE FROM `ai_conversation_deletion_audits`;--> statement-breakpoint
DROP TABLE `ai_conversation_deletion_audits`;--> statement-breakpoint
DELETE FROM `ai_channels`;--> statement-breakpoint
DROP TABLE `ai_channels`;--> statement-breakpoint
DELETE FROM `ai_channel_callback_events`;--> statement-breakpoint
DROP TABLE `ai_channel_callback_events`;--> statement-breakpoint
DELETE FROM `ai_artifacts`;--> statement-breakpoint
DROP TABLE `ai_artifacts`;--> statement-breakpoint
DELETE FROM `ai_artifact_deliveries`;--> statement-breakpoint
DROP TABLE `ai_artifact_deliveries`;--> statement-breakpoint
DELETE FROM `ai_analysis_runs`;--> statement-breakpoint
DROP TABLE `ai_analysis_runs`;--> statement-breakpoint
DELETE FROM `ai_agent_jobs`;--> statement-breakpoint
DROP TABLE `ai_agent_jobs`;--> statement-breakpoint
DROP TABLE ai_write_authority;--> statement-breakpoint
CREATE VIEW `ai_agent_checkpoints` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS INTEGER) AS `ordinal`, CAST(NULL AS TEXT) AS `kind`, CAST(NULL AS TEXT) AS `state_json`, CAST(NULL AS TEXT) AS `output_digest`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_checkpoints_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_checkpoints`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_checkpoints_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_checkpoints`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_checkpoints_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_checkpoints`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_agent_events` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `event_type`, CAST(NULL AS TEXT) AS `from_status`, CAST(NULL AS TEXT) AS `to_status`, CAST(NULL AS INTEGER) AS `job_version`, CAST(NULL AS TEXT) AS `details_json`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_events_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_events_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_events_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_agent_jobs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `client_request_id`, CAST(NULL AS TEXT) AS `request_digest`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `task`, CAST(NULL AS TEXT) AS `input_json`, CAST(NULL AS TEXT) AS `state_json`, CAST(NULL AS TEXT) AS `output_json`, CAST(NULL AS TEXT) AS `model_id`, CAST(NULL AS INTEGER) AS `model_version`, CAST(NULL AS TEXT) AS `allowed_tools_json`, CAST(NULL AS TEXT) AS `tool_policy_digest`, CAST(NULL AS INTEGER) AS `provider_round_count`, CAST(NULL AS INTEGER) AS `tool_call_count`, CAST(NULL AS TEXT) AS `provider_dispatch_started_at`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS TEXT) AS `phase`, CAST(NULL AS INTEGER) AS `step_index`, CAST(NULL AS INTEGER) AS `version`, CAST(NULL AS TEXT) AS `mutation_token`, CAST(NULL AS INTEGER) AS `cancel_requested`, CAST(NULL AS INTEGER) AS `retryable`, CAST(NULL AS INTEGER) AS `resume_count`, CAST(NULL AS INTEGER) AS `attempt_count`, CAST(NULL AS TEXT) AS `lease_token`, CAST(NULL AS INTEGER) AS `lease_epoch`, CAST(NULL AS TEXT) AS `lease_expires_at`, CAST(NULL AS TEXT) AS `next_run_at`, CAST(NULL AS TEXT) AS `workflow_run_id`, CAST(NULL AS TEXT) AS `workflow_node_key`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS TEXT) AS `started_at`, CAST(NULL AS TEXT) AS `completed_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_jobs_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_jobs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_jobs_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_jobs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_jobs_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_jobs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_agent_provider_dispatches` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS INTEGER) AS `dispatch_ordinal`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `model_id`, CAST(NULL AS INTEGER) AS `model_version`, CAST(NULL AS TEXT) AS `tool_policy_digest`, CAST(NULL AS TEXT) AS `request_digest`, CAST(NULL AS TEXT) AS `state`, CAST(NULL AS INTEGER) AS `lease_epoch`, CAST(NULL AS TEXT) AS `reserved_at`, CAST(NULL AS TEXT) AS `provider_called_at`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_provider_dispatches_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_provider_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_provider_dispatches_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_provider_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_provider_dispatches_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_provider_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_agent_provider_results` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `dispatch_id`, CAST(NULL AS TEXT) AS `response_json`, CAST(NULL AS TEXT) AS `response_digest`, CAST(NULL AS TEXT) AS `usage_json`, CAST(NULL AS TEXT) AS `provider_request_id`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_provider_results_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_provider_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_provider_results_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_provider_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_provider_results_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_provider_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_agent_tool_dispatches` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS TEXT) AS `provider_dispatch_id`, CAST(NULL AS INTEGER) AS `tool_call_ordinal`, CAST(NULL AS TEXT) AS `provider_call_id`, CAST(NULL AS TEXT) AS `tool_name`, CAST(NULL AS TEXT) AS `arguments_json`, CAST(NULL AS TEXT) AS `arguments_digest`, CAST(NULL AS TEXT) AS `invocation_id`, CAST(NULL AS TEXT) AS `state`, CAST(NULL AS INTEGER) AS `lease_epoch`, CAST(NULL AS TEXT) AS `reserved_at`, CAST(NULL AS TEXT) AS `tool_called_at`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_tool_dispatches_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_tool_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_tool_dispatches_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_tool_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_tool_dispatches_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_tool_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_agent_tool_results` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `tool_dispatch_id`, CAST(NULL AS TEXT) AS `result_json`, CAST(NULL AS TEXT) AS `result_digest`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_agent_tool_results_retired_insert_guard` INSTEAD OF INSERT ON `ai_agent_tool_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_tool_results_retired_update_guard` INSTEAD OF UPDATE ON `ai_agent_tool_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_agent_tool_results_retired_delete_guard` INSTEAD OF DELETE ON `ai_agent_tool_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_analysis_runs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `dataset`, CAST(NULL AS TEXT) AS `query_digest`, CAST(NULL AS TEXT) AS `plan_digest`, CAST(NULL AS TEXT) AS `operations_json`, CAST(NULL AS TEXT) AS `data_cutoff_date`, CAST(NULL AS INTEGER) AS `source_rows`, CAST(NULL AS INTEGER) AS `returned_rows`, CAST(NULL AS INTEGER) AS `truncated`, CAST(NULL AS TEXT) AS `result_digest`, CAST(NULL AS TEXT) AS `request_id`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_analysis_runs_retired_insert_guard` INSTEAD OF INSERT ON `ai_analysis_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_analysis_runs_retired_update_guard` INSTEAD OF UPDATE ON `ai_analysis_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_analysis_runs_retired_delete_guard` INSTEAD OF DELETE ON `ai_analysis_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_artifact_deliveries` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `artifact_id`, CAST(NULL AS TEXT) AS `request_id`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `surface`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `byte_size`, CAST(NULL AS TEXT) AS `content_digest`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_artifact_deliveries_retired_insert_guard` INSTEAD OF INSERT ON `ai_artifact_deliveries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_artifact_deliveries_retired_update_guard` INSTEAD OF UPDATE ON `ai_artifact_deliveries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_artifact_deliveries_retired_delete_guard` INSTEAD OF DELETE ON `ai_artifact_deliveries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_artifacts` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `conversation_id`, CAST(NULL AS TEXT) AS `message_id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `kind`, CAST(NULL AS TEXT) AS `title`, CAST(NULL AS TEXT) AS `file_name`, CAST(NULL AS TEXT) AS `mime_type`, CAST(NULL AS TEXT) AS `source_tool`, CAST(NULL AS TEXT) AS `columns_json`, CAST(NULL AS TEXT) AS `rows_json`, CAST(NULL AS INTEGER) AS `row_count`, CAST(NULL AS INTEGER) AS `truncated`, CAST(NULL AS TEXT) AS `content_digest`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_artifacts_retired_insert_guard` INSTEAD OF INSERT ON `ai_artifacts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_artifacts_retired_update_guard` INSTEAD OF UPDATE ON `ai_artifacts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_artifacts_retired_delete_guard` INSTEAD OF DELETE ON `ai_artifacts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_channel_callback_events` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `channel_id`, CAST(NULL AS TEXT) AS `event_key`, CAST(NULL AS TEXT) AS `payload_digest`, CAST(NULL AS TEXT) AS `received_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_channel_callback_events_retired_insert_guard` INSTEAD OF INSERT ON `ai_channel_callback_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_channel_callback_events_retired_update_guard` INSTEAD OF UPDATE ON `ai_channel_callback_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_channel_callback_events_retired_delete_guard` INSTEAD OF DELETE ON `ai_channel_callback_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_channels` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `name`, CAST(NULL AS TEXT) AS `kind`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `send_enabled`, CAST(NULL AS INTEGER) AS `callback_enabled`, CAST(NULL AS TEXT) AS `webhook_url`, CAST(NULL AS TEXT) AS `callback_token_encrypted`, CAST(NULL AS TEXT) AS `callback_token_suffix`, CAST(NULL AS TEXT) AS `aes_key_encrypted`, CAST(NULL AS TEXT) AS `aes_key_suffix`, CAST(NULL AS TEXT) AS `last_test_result`, CAST(NULL AS TEXT) AS `last_tested_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS TEXT) AS `receiver_id` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_channels_retired_insert_guard` INSTEAD OF INSERT ON `ai_channels`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_channels_retired_update_guard` INSTEAD OF UPDATE ON `ai_channels`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_channels_retired_delete_guard` INSTEAD OF DELETE ON `ai_channels`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_chat_provider_dispatches` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `receipt_id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `model_id`, CAST(NULL AS INTEGER) AS `dispatch_ordinal`, CAST(NULL AS TEXT) AS `reserved_at`, CAST(NULL AS TEXT) AS `provider_called_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_chat_provider_dispatches_retired_insert_guard` INSTEAD OF INSERT ON `ai_chat_provider_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_chat_provider_dispatches_retired_update_guard` INSTEAD OF UPDATE ON `ai_chat_provider_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_chat_provider_dispatches_retired_delete_guard` INSTEAD OF DELETE ON `ai_chat_provider_dispatches`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_chat_request_receipts` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `client_request_id`, CAST(NULL AS TEXT) AS `request_digest`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS TEXT) AS `model_id`, CAST(NULL AS TEXT) AS `conversation_id`, CAST(NULL AS TEXT) AS `assistant_message_id`, CAST(NULL AS TEXT) AS `result_json`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `admitted_at`, CAST(NULL AS TEXT) AS `provider_started_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_chat_request_receipts_retired_insert_guard` INSTEAD OF INSERT ON `ai_chat_request_receipts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_chat_request_receipts_retired_update_guard` INSTEAD OF UPDATE ON `ai_chat_request_receipts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_chat_request_receipts_retired_delete_guard` INSTEAD OF DELETE ON `ai_chat_request_receipts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_conversation_deletion_audits` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `audit_id`, CAST(NULL AS TEXT) AS `conversation_id`, CAST(NULL AS TEXT) AS `conversation_owner`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `reason`, CAST(NULL AS INTEGER) AS `deleted_message_count`, CAST(NULL AS INTEGER) AS `deleted_artifact_count`, CAST(NULL AS TEXT) AS `deleted_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_deletion_audits_retired_insert_guard` INSTEAD OF INSERT ON `ai_conversation_deletion_audits`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_deletion_audits_retired_update_guard` INSTEAD OF UPDATE ON `ai_conversation_deletion_audits`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_deletion_audits_retired_delete_guard` INSTEAD OF DELETE ON `ai_conversation_deletion_audits`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_conversation_messages` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `conversation_id`, CAST(NULL AS TEXT) AS `role`, CAST(NULL AS TEXT) AS `content`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `message_kind` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_messages_retired_insert_guard` INSTEAD OF INSERT ON `ai_conversation_messages`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_messages_retired_update_guard` INSTEAD OF UPDATE ON `ai_conversation_messages`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_messages_retired_delete_guard` INSTEAD OF DELETE ON `ai_conversation_messages`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_conversation_scopes` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `conversation_id`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_scopes_retired_insert_guard` INSTEAD OF INSERT ON `ai_conversation_scopes`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_scopes_retired_update_guard` INSTEAD OF UPDATE ON `ai_conversation_scopes`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversation_scopes_retired_delete_guard` INSTEAD OF DELETE ON `ai_conversation_scopes`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_conversations` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `title`, CAST(NULL AS TEXT) AS `model_id`, CAST(NULL AS TEXT) AS `created_by`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_conversations_retired_insert_guard` INSTEAD OF INSERT ON `ai_conversations`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversations_retired_update_guard` INSTEAD OF UPDATE ON `ai_conversations`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_conversations_retired_delete_guard` INSTEAD OF DELETE ON `ai_conversations`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_knowledge_entries` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `source_type`, CAST(NULL AS TEXT) AS `source_ref`, CAST(NULL AS TEXT) AS `title`, CAST(NULL AS TEXT) AS `content`, CAST(NULL AS TEXT) AS `tags_json`, CAST(NULL AS TEXT) AS `allowed_roles_json`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `version`, CAST(NULL AS TEXT) AS `content_digest`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_knowledge_entries_retired_insert_guard` INSTEAD OF INSERT ON `ai_knowledge_entries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_knowledge_entries_retired_update_guard` INSTEAD OF UPDATE ON `ai_knowledge_entries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_knowledge_entries_retired_delete_guard` INSTEAD OF DELETE ON `ai_knowledge_entries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_memory_audit_logs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `operation_id`, CAST(NULL AS TEXT) AS `request_id`, CAST(NULL AS TEXT) AS `memory_id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `operation`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS TEXT) AS `scope_digest`, CAST(NULL AS TEXT) AS `before_digest`, CAST(NULL AS TEXT) AS `after_digest`, CAST(NULL AS INTEGER) AS `result_version`, CAST(NULL AS TEXT) AS `policy_version`, CAST(NULL AS TEXT) AS `gate_results_json`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_memory_audit_logs_retired_insert_guard` INSTEAD OF INSERT ON `ai_memory_audit_logs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_memory_audit_logs_retired_update_guard` INSTEAD OF UPDATE ON `ai_memory_audit_logs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_memory_audit_logs_retired_delete_guard` INSTEAD OF DELETE ON `ai_memory_audit_logs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_memory_commit_guards` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `operation_id`, CAST(NULL AS INTEGER) AS `audit_present` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_memory_commit_guards_retired_insert_guard` INSTEAD OF INSERT ON `ai_memory_commit_guards`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_memory_commit_guards_retired_update_guard` INSTEAD OF UPDATE ON `ai_memory_commit_guards`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_memory_commit_guards_retired_delete_guard` INSTEAD OF DELETE ON `ai_memory_commit_guards`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_memory_entries` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `kind`, CAST(NULL AS TEXT) AS `memory_key`, CAST(NULL AS TEXT) AS `memory_key_normalized`, CAST(NULL AS TEXT) AS `content`, CAST(NULL AS TEXT) AS `content_digest`, CAST(NULL AS TEXT) AS `scope_mode`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `scope_digest`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `version`, CAST(NULL AS TEXT) AS `source`, CAST(NULL AS TEXT) AS `source_conversation_id`, CAST(NULL AS TEXT) AS `source_message_id`, CAST(NULL AS TEXT) AS `last_operation_id`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS TEXT) AS `archived_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_memory_entries_retired_insert_guard` INSTEAD OF INSERT ON `ai_memory_entries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_memory_entries_retired_update_guard` INSTEAD OF UPDATE ON `ai_memory_entries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_memory_entries_retired_delete_guard` INSTEAD OF DELETE ON `ai_memory_entries`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_models` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `name`, CAST(NULL AS TEXT) AS `protocol`, CAST(NULL AS TEXT) AS `model_type`, CAST(NULL AS TEXT) AS `model_name`, CAST(NULL AS TEXT) AS `base_url`, CAST(NULL AS TEXT) AS `api_key_encrypted`, CAST(NULL AS TEXT) AS `api_key_suffix`, CAST(NULL AS INTEGER) AS `is_default_text_model`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS TEXT) AS `last_test_result`, CAST(NULL AS TEXT) AS `last_tested_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS INTEGER) AS `timeout_ms`, CAST(NULL AS INTEGER) AS `max_tokens`, CAST(NULL AS INTEGER) AS `temperature_milli`, CAST(NULL AS INTEGER) AS `max_tool_rounds`, CAST(NULL AS INTEGER) AS `max_total_tool_calls`, CAST(NULL AS TEXT) AS `reasoning_mode`, CAST(NULL AS INTEGER) AS `version` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_models_retired_insert_guard` INSTEAD OF INSERT ON `ai_models`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_models_retired_update_guard` INSTEAD OF UPDATE ON `ai_models`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_models_retired_delete_guard` INSTEAD OF DELETE ON `ai_models`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_admin_audits` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `action`, CAST(NULL AS TEXT) AS `entity_type`, CAST(NULL AS TEXT) AS `entity_id`, CAST(NULL AS TEXT) AS `before_json`, CAST(NULL AS TEXT) AS `after_json`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_admin_audits_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_admin_audits`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_admin_audits_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_admin_audits`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_admin_audits_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_admin_audits`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_asset_cleanup_queue` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `object_key`, CAST(NULL AS INTEGER) AS `attempt_count`, CAST(NULL AS TEXT) AS `last_error`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_asset_cleanup_queue_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_asset_cleanup_queue`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_asset_cleanup_queue_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_asset_cleanup_queue`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_asset_cleanup_queue_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_asset_cleanup_queue`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_asset_favorites` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `asset_id`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_asset_favorites_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_asset_favorites`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_asset_favorites_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_asset_favorites`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_asset_favorites_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_asset_favorites`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_assets` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS TEXT) AS `item_id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `scene`, CAST(NULL AS TEXT) AS `object_key`, CAST(NULL AS TEXT) AS `content_sha256`, CAST(NULL AS TEXT) AS `mime_type`, CAST(NULL AS INTEGER) AS `byte_size`, CAST(NULL AS INTEGER) AS `width`, CAST(NULL AS INTEGER) AS `height`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_assets_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_assets`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_assets_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_assets`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_assets_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_assets`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_dispatch_receipts` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `item_id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `model_profile_id`, CAST(NULL AS INTEGER) AS `model_profile_version`, CAST(NULL AS TEXT) AS `model_name`, CAST(NULL AS TEXT) AS `scene`, CAST(NULL AS TEXT) AS `size`, CAST(NULL AS TEXT) AS `prompt_digest`, CAST(NULL AS TEXT) AS `dispatched_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_dispatch_receipts_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_dispatch_receipts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_dispatch_receipts_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_dispatch_receipts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_dispatch_receipts_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_dispatch_receipts`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_dispatch_results` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `dispatch_id`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS TEXT) AS `provider_request_id`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `usage_json`, CAST(NULL AS INTEGER) AS `estimated_cost_cents`, CAST(NULL AS TEXT) AS `price_version`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_dispatch_results_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_dispatch_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_dispatch_results_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_dispatch_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_dispatch_results_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_dispatch_results`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_job_items` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `job_id`, CAST(NULL AS INTEGER) AS `ordinal`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `attempt_count`, CAST(NULL AS TEXT) AS `lease_token`, CAST(NULL AS INTEGER) AS `lease_epoch`, CAST(NULL AS TEXT) AS `lease_expires_at`, CAST(NULL AS TEXT) AS `provider_request_id`, CAST(NULL AS TEXT) AS `asset_id`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS INTEGER) AS `duration_ms`, CAST(NULL AS TEXT) AS `started_at`, CAST(NULL AS TEXT) AS `completed_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS TEXT) AS `dispatch_started_at`, CAST(NULL AS TEXT) AS `pending_object_key` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_job_items_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_job_items`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_job_items_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_job_items`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_job_items_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_job_items`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_jobs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `client_request_id`, CAST(NULL AS TEXT) AS `request_digest`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `scene`, CAST(NULL AS TEXT) AS `template_id`, CAST(NULL AS TEXT) AS `template_name`, CAST(NULL AS INTEGER) AS `template_version`, CAST(NULL AS TEXT) AS `model_profile_id`, CAST(NULL AS TEXT) AS `model_profile_name`, CAST(NULL AS TEXT) AS `model_name`, CAST(NULL AS TEXT) AS `product_name`, CAST(NULL AS TEXT) AS `brand`, CAST(NULL AS TEXT) AS `sku`, CAST(NULL AS TEXT) AS `selling_points`, CAST(NULL AS TEXT) AS `final_prompt`, CAST(NULL AS TEXT) AS `prompt_digest`, CAST(NULL AS TEXT) AS `size`, CAST(NULL AS INTEGER) AS `requested_count`, CAST(NULL AS INTEGER) AS `succeeded_count`, CAST(NULL AS INTEGER) AS `failed_count`, CAST(NULL AS INTEGER) AS `cancelled_count`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `cancel_requested`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS TEXT) AS `started_at`, CAST(NULL AS TEXT) AS `completed_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS INTEGER) AS `model_profile_version` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_jobs_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_jobs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_jobs_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_jobs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_jobs_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_jobs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_model_profiles` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `name`, CAST(NULL AS TEXT) AS `protocol`, CAST(NULL AS TEXT) AS `model_name`, CAST(NULL AS TEXT) AS `base_url`, CAST(NULL AS TEXT) AS `api_key_encrypted`, CAST(NULL AS TEXT) AS `api_key_suffix`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `timeout_ms`, CAST(NULL AS TEXT) AS `last_success_result`, CAST(NULL AS TEXT) AS `last_success_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`, CAST(NULL AS INTEGER) AS `version` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_model_profiles_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_model_profiles`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_model_profiles_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_model_profiles`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_model_profiles_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_model_profiles`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_schema_upgrades` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_schema_upgrades_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_schema_upgrades`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_schema_upgrades_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_schema_upgrades`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_schema_upgrades_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_schema_upgrades`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_space_templates` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `scene`, CAST(NULL AS TEXT) AS `name`, CAST(NULL AS TEXT) AS `prompt_template`, CAST(NULL AS TEXT) AS `size`, CAST(NULL AS TEXT) AS `model_profile_id`, CAST(NULL AS INTEGER) AS `version`, CAST(NULL AS INTEGER) AS `is_enabled`, CAST(NULL AS INTEGER) AS `is_default`, CAST(NULL AS TEXT) AS `updated_by`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_space_templates_retired_insert_guard` INSTEAD OF INSERT ON `ai_space_templates`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_templates_retired_update_guard` INSTEAD OF UPDATE ON `ai_space_templates`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_space_templates_retired_delete_guard` INSTEAD OF DELETE ON `ai_space_templates`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_system_settings` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `key`, CAST(NULL AS TEXT) AS `value_json`, CAST(NULL AS TEXT) AS `updated_by`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_system_settings_retired_insert_guard` INSTEAD OF INSERT ON `ai_system_settings`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_system_settings_retired_update_guard` INSTEAD OF UPDATE ON `ai_system_settings`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_system_settings_retired_delete_guard` INSTEAD OF DELETE ON `ai_system_settings`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_tool_audit_logs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `request_id`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `actor_role`, CAST(NULL AS TEXT) AS `surface`, CAST(NULL AS TEXT) AS `tool_name`, CAST(NULL AS TEXT) AS `arguments_json`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `row_count`, CAST(NULL AS INTEGER) AS `duration_ms`, CAST(NULL AS TEXT) AS `response_digest`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_tool_audit_logs_retired_insert_guard` INSTEAD OF INSERT ON `ai_tool_audit_logs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_tool_audit_logs_retired_update_guard` INSTEAD OF UPDATE ON `ai_tool_audit_logs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_tool_audit_logs_retired_delete_guard` INSTEAD OF DELETE ON `ai_tool_audit_logs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_workflow_events` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `run_id`, CAST(NULL AS TEXT) AS `node_key`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `actor_email`, CAST(NULL AS TEXT) AS `event_type`, CAST(NULL AS TEXT) AS `from_status`, CAST(NULL AS TEXT) AS `to_status`, CAST(NULL AS INTEGER) AS `run_version`, CAST(NULL AS TEXT) AS `details_json`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_events_retired_insert_guard` INSTEAD OF INSERT ON `ai_workflow_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_events_retired_update_guard` INSTEAD OF UPDATE ON `ai_workflow_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_events_retired_delete_guard` INSTEAD OF DELETE ON `ai_workflow_events`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_workflow_node_runs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `run_id`, CAST(NULL AS TEXT) AS `node_key`, CAST(NULL AS INTEGER) AS `position`, CAST(NULL AS TEXT) AS `node_type`, CAST(NULL AS TEXT) AS `depends_on_json`, CAST(NULL AS TEXT) AS `instruction`, CAST(NULL AS TEXT) AS `input_json`, CAST(NULL AS TEXT) AS `output_json`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `version`, CAST(NULL AS TEXT) AS `mutation_token`, CAST(NULL AS TEXT) AS `agent_job_id`, CAST(NULL AS TEXT) AS `reviewer_email`, CAST(NULL AS TEXT) AS `reviewed_at`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS TEXT) AS `started_at`, CAST(NULL AS TEXT) AS `completed_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_node_runs_retired_insert_guard` INSTEAD OF INSERT ON `ai_workflow_node_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_node_runs_retired_update_guard` INSTEAD OF UPDATE ON `ai_workflow_node_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_node_runs_retired_delete_guard` INSTEAD OF DELETE ON `ai_workflow_node_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_workflow_runs` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `owner_email`, CAST(NULL AS TEXT) AS `client_request_id`, CAST(NULL AS TEXT) AS `request_digest`, CAST(NULL AS TEXT) AS `scope_json`, CAST(NULL AS TEXT) AS `name`, CAST(NULL AS TEXT) AS `graph_json`, CAST(NULL AS TEXT) AS `graph_digest`, CAST(NULL AS TEXT) AS `input_json`, CAST(NULL AS TEXT) AS `output_json`, CAST(NULL AS TEXT) AS `model_id`, CAST(NULL AS INTEGER) AS `model_version`, CAST(NULL AS TEXT) AS `allowed_tools_json`, CAST(NULL AS TEXT) AS `tool_policy_digest`, CAST(NULL AS INTEGER) AS `provider_round_count`, CAST(NULL AS INTEGER) AS `tool_call_count`, CAST(NULL AS TEXT) AS `provider_dispatch_started_at`, CAST(NULL AS INTEGER) AS `dry_run`, CAST(NULL AS TEXT) AS `status`, CAST(NULL AS TEXT) AS `current_node_key`, CAST(NULL AS INTEGER) AS `version`, CAST(NULL AS TEXT) AS `mutation_token`, CAST(NULL AS INTEGER) AS `cancel_requested`, CAST(NULL AS INTEGER) AS `retryable`, CAST(NULL AS INTEGER) AS `resume_count`, CAST(NULL AS INTEGER) AS `attempt_count`, CAST(NULL AS TEXT) AS `lease_token`, CAST(NULL AS INTEGER) AS `lease_epoch`, CAST(NULL AS TEXT) AS `lease_expires_at`, CAST(NULL AS TEXT) AS `next_run_at`, CAST(NULL AS TEXT) AS `error_code`, CAST(NULL AS TEXT) AS `error_message`, CAST(NULL AS TEXT) AS `started_at`, CAST(NULL AS TEXT) AS `completed_at`, CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_runs_retired_insert_guard` INSTEAD OF INSERT ON `ai_workflow_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_runs_retired_update_guard` INSTEAD OF UPDATE ON `ai_workflow_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_workflow_runs_retired_delete_guard` INSTEAD OF DELETE ON `ai_workflow_runs`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE VIEW `ai_write_authority` AS SELECT /* ai-assistant-domain-retired-v1 */ CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS TEXT) AS `owner`, CAST(NULL AS INTEGER) AS `epoch`, CAST(NULL AS TEXT) AS `cutover_id`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE TRIGGER `ai_write_authority_retired_insert_guard` INSTEAD OF INSERT ON `ai_write_authority`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_write_authority_retired_update_guard` INSTEAD OF UPDATE ON `ai_write_authority`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `ai_write_authority_retired_delete_guard` INSTEAD OF DELETE ON `ai_write_authority`
BEGIN SELECT RAISE(ABORT,'ai_domain_retired'); END;--> statement-breakpoint
UPDATE domain_retirement_receipts SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE domain='ai-assistant' AND status='approved';
