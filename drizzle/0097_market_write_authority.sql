-- Operator-applied preparation for the market single-write cutover. This is
-- behavior-neutral while owner=d1. The controlled cutover advances the
-- singleton to pending before the immutable migration snapshot is captured.
CREATE TABLE IF NOT EXISTS `market_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('d1', 'pending', 'postgresql')),
  `epoch` integer NOT NULL CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `market_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'d1',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `market_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_singleton_insert_guard`
BEFORE INSERT ON `market_write_authority`
BEGIN SELECT RAISE(ABORT,'market_write_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_singleton_delete_guard`
BEFORE DELETE ON `market_write_authority`
BEGIN SELECT RAISE(ABORT,'market_write_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_transition_guard`
BEFORE UPDATE ON `market_write_authority`
WHEN NOT (
  NEW.`id`=OLD.`id` AND NEW.`epoch`=OLD.`epoch`+1
  AND length(NEW.`cutover_id`) BETWEEN 8 AND 128
  AND (
    (OLD.`owner`='d1' AND NEW.`owner`='pending')
    OR (OLD.`owner`='pending' AND NEW.`owner`='d1' AND NEW.`cutover_id`=OLD.`cutover_id`)
    OR (OLD.`owner`='pending' AND NEW.`owner`='postgresql' AND NEW.`cutover_id`=OLD.`cutover_id`)
  )
)
BEGIN SELECT RAISE(ABORT,'market_write_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_cloud_runs_insert` BEFORE INSERT ON `market_annotation_cloud_runs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_cloud_runs_update` BEFORE UPDATE ON `market_annotation_cloud_runs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_cloud_runs_delete` BEFORE DELETE ON `market_annotation_cloud_runs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_commit_receipts_insert` BEFORE INSERT ON `market_annotation_commit_receipts`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_commit_receipts_update` BEFORE UPDATE ON `market_annotation_commit_receipts`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_commit_receipts_delete` BEFORE DELETE ON `market_annotation_commit_receipts`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_concurrency_settings_insert` BEFORE INSERT ON `market_annotation_concurrency_settings`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_concurrency_settings_update` BEFORE UPDATE ON `market_annotation_concurrency_settings`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_concurrency_settings_delete` BEFORE DELETE ON `market_annotation_concurrency_settings`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_items_insert` BEFORE INSERT ON `market_annotation_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_items_update` BEFORE UPDATE ON `market_annotation_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_items_delete` BEFORE DELETE ON `market_annotation_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_jobs_insert` BEFORE INSERT ON `market_annotation_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_jobs_update` BEFORE UPDATE ON `market_annotation_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_jobs_delete` BEFORE DELETE ON `market_annotation_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_local_agents_insert` BEFORE INSERT ON `market_annotation_local_agents`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_local_agents_update` BEFORE UPDATE ON `market_annotation_local_agents`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_local_agents_delete` BEFORE DELETE ON `market_annotation_local_agents`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_prompt_audits_insert` BEFORE INSERT ON `market_annotation_prompt_audits`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_prompt_audits_update` BEFORE UPDATE ON `market_annotation_prompt_audits`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_prompt_audits_delete` BEFORE DELETE ON `market_annotation_prompt_audits`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_prompt_versions_insert` BEFORE INSERT ON `market_annotation_prompt_versions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_prompt_versions_update` BEFORE UPDATE ON `market_annotation_prompt_versions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_prompt_versions_delete` BEFORE DELETE ON `market_annotation_prompt_versions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_results_insert` BEFORE INSERT ON `market_annotation_validation_results`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_results_update` BEFORE UPDATE ON `market_annotation_validation_results`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_results_delete` BEFORE DELETE ON `market_annotation_validation_results`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_runs_insert` BEFORE INSERT ON `market_annotation_validation_runs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_runs_update` BEFORE UPDATE ON `market_annotation_validation_runs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_runs_delete` BEFORE DELETE ON `market_annotation_validation_runs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_samples_insert` BEFORE INSERT ON `market_annotation_validation_samples`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_samples_update` BEFORE UPDATE ON `market_annotation_validation_samples`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_annotation_validation_samples_delete` BEFORE DELETE ON `market_annotation_validation_samples`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_recognition_jobs_insert` BEFORE INSERT ON `market_brand_recognition_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_recognition_jobs_update` BEFORE UPDATE ON `market_brand_recognition_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_recognition_jobs_delete` BEFORE DELETE ON `market_brand_recognition_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_seeds_insert` BEFORE INSERT ON `market_brand_seeds`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_seeds_update` BEFORE UPDATE ON `market_brand_seeds`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_seeds_delete` BEFORE DELETE ON `market_brand_seeds`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_suggestions_insert` BEFORE INSERT ON `market_brand_suggestions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_suggestions_update` BEFORE UPDATE ON `market_brand_suggestions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_brand_suggestions_delete` BEFORE DELETE ON `market_brand_suggestions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_configs_insert` BEFORE INSERT ON `market_download_configs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_configs_update` BEFORE UPDATE ON `market_download_configs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_configs_delete` BEFORE DELETE ON `market_download_configs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_staging_rows_insert` BEFORE INSERT ON `market_download_staging_rows`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_staging_rows_update` BEFORE UPDATE ON `market_download_staging_rows`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_staging_rows_delete` BEFORE DELETE ON `market_download_staging_rows`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_tasks_insert` BEFORE INSERT ON `market_download_tasks`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_tasks_update` BEFORE UPDATE ON `market_download_tasks`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_download_tasks_delete` BEFORE DELETE ON `market_download_tasks`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_effective_metrics_cache_insert` BEFORE INSERT ON `market_effective_metrics_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_effective_metrics_cache_update` BEFORE UPDATE ON `market_effective_metrics_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_effective_metrics_cache_delete` BEFORE DELETE ON `market_effective_metrics_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_effective_metrics_cache_state_insert` BEFORE INSERT ON `market_effective_metrics_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_effective_metrics_cache_state_update` BEFORE UPDATE ON `market_effective_metrics_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_effective_metrics_cache_state_delete` BEFORE DELETE ON `market_effective_metrics_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_insert` BEFORE INSERT ON `market_image_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_update` BEFORE UPDATE ON `market_image_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_delete` BEFORE DELETE ON `market_image_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_claims_insert` BEFORE INSERT ON `market_image_cache_claims`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_claims_update` BEFORE UPDATE ON `market_image_cache_claims`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_claims_delete` BEFORE DELETE ON `market_image_cache_claims`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_job_items_insert` BEFORE INSERT ON `market_image_cache_job_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_job_items_update` BEFORE UPDATE ON `market_image_cache_job_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_job_items_delete` BEFORE DELETE ON `market_image_cache_job_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_jobs_insert` BEFORE INSERT ON `market_image_cache_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_jobs_update` BEFORE UPDATE ON `market_image_cache_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_image_cache_jobs_delete` BEFORE DELETE ON `market_image_cache_jobs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_batches_insert` BEFORE INSERT ON `market_import_batches`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_batches_update` BEFORE UPDATE ON `market_import_batches`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_batches_delete` BEFORE DELETE ON `market_import_batches`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_identity_refresh_keys_v2_insert` BEFORE INSERT ON `market_import_identity_refresh_keys_v2`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_identity_refresh_keys_v2_update` BEFORE UPDATE ON `market_import_identity_refresh_keys_v2`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_identity_refresh_keys_v2_delete` BEFORE DELETE ON `market_import_identity_refresh_keys_v2`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_range_claims_insert` BEFORE INSERT ON `market_import_range_claims`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_range_claims_update` BEFORE UPDATE ON `market_import_range_claims`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_range_claims_delete` BEFORE DELETE ON `market_import_range_claims`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_staging_rows_insert` BEFORE INSERT ON `market_import_staging_rows`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_staging_rows_update` BEFORE UPDATE ON `market_import_staging_rows`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_import_staging_rows_delete` BEFORE DELETE ON `market_import_staging_rows`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_audit_logs_insert` BEFORE INSERT ON `market_master_audit_logs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_audit_logs_update` BEFORE UPDATE ON `market_master_audit_logs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_audit_logs_delete` BEFORE DELETE ON `market_master_audit_logs`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_database_filters_cache_state_insert` BEFORE INSERT ON `market_master_database_filters_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_database_filters_cache_state_update` BEFORE UPDATE ON `market_master_database_filters_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_database_filters_cache_state_delete` BEFORE DELETE ON `market_master_database_filters_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_identities_insert` BEFORE INSERT ON `market_master_identities`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_identities_update` BEFORE UPDATE ON `market_master_identities`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_identities_delete` BEFORE DELETE ON `market_master_identities`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_mapping_rules_insert` BEFORE INSERT ON `market_master_mapping_rules`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_mapping_rules_update` BEFORE UPDATE ON `market_master_mapping_rules`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_master_mapping_rules_delete` BEFORE DELETE ON `market_master_mapping_rules`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_cache_insert` BEFORE INSERT ON `market_monthly_summary_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_cache_update` BEFORE UPDATE ON `market_monthly_summary_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_cache_delete` BEFORE DELETE ON `market_monthly_summary_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_cache_state_insert` BEFORE INSERT ON `market_monthly_summary_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_cache_state_update` BEFORE UPDATE ON `market_monthly_summary_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_cache_state_delete` BEFORE DELETE ON `market_monthly_summary_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_keys_insert` BEFORE INSERT ON `market_monthly_summary_dirty_keys`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_keys_update` BEFORE UPDATE ON `market_monthly_summary_dirty_keys`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_keys_delete` BEFORE DELETE ON `market_monthly_summary_dirty_keys`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_products_insert` BEFORE INSERT ON `market_monthly_summary_dirty_products`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_products_update` BEFORE UPDATE ON `market_monthly_summary_dirty_products`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_products_delete` BEFORE DELETE ON `market_monthly_summary_dirty_products`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_scopes_insert` BEFORE INSERT ON `market_monthly_summary_dirty_scopes`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_scopes_update` BEFORE UPDATE ON `market_monthly_summary_dirty_scopes`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_monthly_summary_dirty_scopes_delete` BEFORE DELETE ON `market_monthly_summary_dirty_scopes`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_netshop_projection_insert` BEFORE INSERT ON `market_netshop_projection`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_netshop_projection_update` BEFORE UPDATE ON `market_netshop_projection`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_netshop_projection_delete` BEFORE DELETE ON `market_netshop_projection`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_netshop_projection_control_insert` BEFORE INSERT ON `market_netshop_projection_control`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_netshop_projection_control_update` BEFORE UPDATE ON `market_netshop_projection_control`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_netshop_projection_control_delete` BEFORE DELETE ON `market_netshop_projection_control`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_overview_response_cache_insert` BEFORE INSERT ON `market_overview_response_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_overview_response_cache_update` BEFORE UPDATE ON `market_overview_response_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_overview_response_cache_delete` BEFORE DELETE ON `market_overview_response_cache`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_band_items_insert` BEFORE INSERT ON `market_price_band_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_band_items_update` BEFORE UPDATE ON `market_price_band_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_band_items_delete` BEFORE DELETE ON `market_price_band_items`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_band_versions_insert` BEFORE INSERT ON `market_price_band_versions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_band_versions_update` BEFORE UPDATE ON `market_price_band_versions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_band_versions_delete` BEFORE DELETE ON `market_price_band_versions`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_snapshots_insert` BEFORE INSERT ON `market_price_snapshots`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_snapshots_update` BEFORE UPDATE ON `market_price_snapshots`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_price_snapshots_delete` BEFORE DELETE ON `market_price_snapshots`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_ranking_entries_insert` BEFORE INSERT ON `market_ranking_entries`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_ranking_entries_update` BEFORE UPDATE ON `market_ranking_entries`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_ranking_entries_delete` BEFORE DELETE ON `market_ranking_entries`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_sku_annotations_insert` BEFORE INSERT ON `market_sku_annotations`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_sku_annotations_update` BEFORE UPDATE ON `market_sku_annotations`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_sku_annotations_delete` BEFORE DELETE ON `market_sku_annotations`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_sku_gmv_totals_insert` BEFORE INSERT ON `market_sku_gmv_totals`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_sku_gmv_totals_update` BEFORE UPDATE ON `market_sku_gmv_totals`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_sku_gmv_totals_delete` BEFORE DELETE ON `market_sku_gmv_totals`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_subcategory_taxonomy_insert` BEFORE INSERT ON `market_subcategory_taxonomy`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_subcategory_taxonomy_update` BEFORE UPDATE ON `market_subcategory_taxonomy`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_subcategory_taxonomy_delete` BEFORE DELETE ON `market_subcategory_taxonomy`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_system_kpi_cache_control_insert` BEFORE INSERT ON `market_system_kpi_cache_control`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_system_kpi_cache_control_update` BEFORE UPDATE ON `market_system_kpi_cache_control`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_system_kpi_cache_control_delete` BEFORE DELETE ON `market_system_kpi_cache_control`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_system_kpi_cache_state_insert` BEFORE INSERT ON `market_system_kpi_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_system_kpi_cache_state_update` BEFORE UPDATE ON `market_system_kpi_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_system_kpi_cache_state_delete` BEFORE DELETE ON `market_system_kpi_cache_state`
WHEN COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_fingerprints_insert` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.domain='market' AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_fingerprints_update` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.domain='market' OR NEW.domain='market') AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_fingerprints_delete` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.domain='market' AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_attempts_insert` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.domain='market' AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_attempts_update` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.domain='market' OR NEW.domain='market') AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_attempts_delete` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.domain='market' AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `market_authority_heads_insert` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.domain='market' AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_heads_update` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.domain='market' OR NEW.domain='market') AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_authority_heads_delete` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.domain='market' AND COALESCE((SELECT owner FROM market_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'market_write_authority_not_d1'); END;--> statement-breakpoint
