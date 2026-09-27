-- Operator-only terminal retirement for the D1-owned market domain. This
-- migration is deliberately absent from the ordinary Drizzle journal. The
-- controlled operator installs one exact approved receipt in the same
-- BEGIN IMMEDIATE transaction before executing the destructive statements.
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
  AND OLD.`domain`=NEW.`domain`
  AND OLD.`version`=NEW.`version`
  AND OLD.`cutover_id`=NEW.`cutover_id`
  AND OLD.`plan_id`=NEW.`plan_id`
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

-- The handoff is terminal and every D1 market execution owner must be quiet.
SELECT CASE WHEN (
  (SELECT COUNT(*) FROM `market_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='market'
      AND `version`='market-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `market_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
  AND (SELECT COUNT(*) FROM `market_import_batches` WHERE `status`='processing')=0
  AND (SELECT COUNT(*) FROM `market_import_range_claims`)=0
  AND (SELECT COUNT(*) FROM `market_import_staging_rows`)=0
  AND (SELECT COUNT(*) FROM `market_download_staging_rows`)=0
  AND (SELECT COUNT(*) FROM `market_image_cache_claims`)=0
  AND (SELECT COUNT(*) FROM `market_annotation_items` WHERE `status` IN ('claimed','inferencing'))=0
  AND (SELECT COUNT(*) FROM `market_annotation_validation_results` WHERE COALESCE(`claim_token_hash`,'')<>'')=0
  AND (SELECT COUNT(*) FROM `market_annotation_cloud_runs` WHERE COALESCE(`lease_token_hash`,'')<>'')=0
  AND (SELECT COUNT(*) FROM `market_brand_recognition_jobs` WHERE COALESCE(`lease_token`,'')<>'')=0
  AND (SELECT COUNT(*) FROM `market_image_cache_jobs` WHERE COALESCE(`lease_token`,'')<>'')=0
  AND (SELECT COUNT(*) FROM `market_download_tasks` WHERE COALESCE(`execution_token`,'')<>'')=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints` WHERE `domain`='market' AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts` WHERE `domain`='market' AND `outcome`='processing')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads` WHERE `domain`='market'
    AND (`status`<>'ready' OR COALESCE(`owner_token`,'')<>''))=0
  AND (SELECT COUNT(*) FROM `market_netshop_projection_control` WHERE `id`=1
    AND `syncing_revision`='' AND `owner_token`='')=1
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

-- Remove the temporary authority fences before deleting D1-owned state.
DROP TRIGGER IF EXISTS `market_authority_singleton_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_singleton_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_cloud_runs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_cloud_runs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_cloud_runs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_commit_receipts_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_commit_receipts_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_commit_receipts_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_concurrency_settings_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_concurrency_settings_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_concurrency_settings_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_items_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_items_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_items_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_jobs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_jobs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_jobs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_local_agents_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_local_agents_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_local_agents_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_prompt_audits_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_prompt_audits_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_prompt_audits_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_prompt_versions_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_prompt_versions_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_prompt_versions_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_results_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_results_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_results_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_runs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_runs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_runs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_samples_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_samples_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_annotation_validation_samples_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_recognition_jobs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_recognition_jobs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_recognition_jobs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_seeds_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_seeds_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_seeds_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_suggestions_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_suggestions_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_brand_suggestions_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_configs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_configs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_configs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_staging_rows_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_staging_rows_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_staging_rows_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_tasks_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_tasks_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_download_tasks_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_effective_metrics_cache_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_effective_metrics_cache_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_effective_metrics_cache_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_effective_metrics_cache_state_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_effective_metrics_cache_state_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_effective_metrics_cache_state_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_claims_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_claims_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_claims_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_job_items_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_job_items_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_job_items_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_jobs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_jobs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_image_cache_jobs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_batches_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_batches_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_batches_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_identity_refresh_keys_v2_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_identity_refresh_keys_v2_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_identity_refresh_keys_v2_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_range_claims_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_range_claims_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_range_claims_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_staging_rows_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_staging_rows_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_import_staging_rows_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_audit_logs_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_audit_logs_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_audit_logs_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_database_filters_cache_state_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_database_filters_cache_state_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_database_filters_cache_state_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_identities_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_identities_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_identities_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_mapping_rules_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_mapping_rules_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_master_mapping_rules_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_cache_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_cache_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_cache_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_cache_state_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_cache_state_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_cache_state_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_keys_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_keys_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_keys_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_products_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_products_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_products_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_scopes_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_scopes_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_monthly_summary_dirty_scopes_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_netshop_projection_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_netshop_projection_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_netshop_projection_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_netshop_projection_control_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_netshop_projection_control_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_netshop_projection_control_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_overview_response_cache_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_overview_response_cache_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_overview_response_cache_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_band_items_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_band_items_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_band_items_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_band_versions_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_band_versions_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_band_versions_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_snapshots_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_snapshots_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_price_snapshots_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_ranking_entries_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_ranking_entries_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_ranking_entries_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_sku_annotations_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_sku_annotations_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_sku_annotations_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_sku_gmv_totals_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_sku_gmv_totals_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_sku_gmv_totals_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_subcategory_taxonomy_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_subcategory_taxonomy_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_subcategory_taxonomy_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_system_kpi_cache_control_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_system_kpi_cache_control_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_system_kpi_cache_control_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_system_kpi_cache_state_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_system_kpi_cache_state_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_system_kpi_cache_state_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_fingerprints_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_fingerprints_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_fingerprints_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_attempts_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_attempts_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_attempts_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_heads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_heads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_authority_heads_delete`;--> statement-breakpoint
DROP VIEW IF EXISTS `market_netshop_active_projection`;--> statement-breakpoint

DELETE FROM `import_content_fingerprints` WHERE `domain`='market';--> statement-breakpoint
DELETE FROM `import_content_attempts` WHERE `domain`='market';--> statement-breakpoint
DELETE FROM `import_scope_heads` WHERE `domain`='market';--> statement-breakpoint

-- Permanent shared-table guards make the retired domain impossible to revive.
CREATE TRIGGER `market_retired_fingerprints_insert_guard`
BEFORE INSERT ON `import_content_fingerprints` WHEN NEW.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_fingerprints_update_guard`
BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain`='market' OR NEW.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_fingerprints_delete_guard`
BEFORE DELETE ON `import_content_fingerprints` WHEN OLD.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_attempts_insert_guard`
BEFORE INSERT ON `import_content_attempts` WHEN NEW.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_attempts_update_guard`
BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain`='market' OR NEW.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_attempts_delete_guard`
BEFORE DELETE ON `import_content_attempts` WHEN OLD.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_scope_heads_insert_guard`
BEFORE INSERT ON `import_scope_heads` WHEN NEW.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_scope_heads_update_guard`
BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain`='market' OR NEW.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `market_retired_scope_heads_delete_guard`
BEFORE DELETE ON `import_scope_heads` WHEN OLD.`domain`='market'
BEGIN SELECT RAISE(ABORT,'market_domain_retired'); END;--> statement-breakpoint

DROP TABLE IF EXISTS `market_annotation_cloud_runs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_commit_receipts`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_concurrency_settings`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_items`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_jobs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_local_agents`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_prompt_audits`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_prompt_versions`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_validation_results`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_validation_runs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_annotation_validation_samples`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_brand_recognition_jobs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_brand_seeds`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_brand_suggestions`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_download_configs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_download_staging_rows`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_download_tasks`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_effective_metrics_cache`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_effective_metrics_cache_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_image_cache`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_image_cache_claims`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_image_cache_job_items`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_image_cache_jobs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_import_batches`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_import_identity_refresh_keys_v2`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_import_range_claims`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_import_staging_rows`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_master_audit_logs`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_master_database_filters_cache_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_master_identities`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_master_mapping_rules`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_monthly_summary_cache`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_monthly_summary_cache_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_monthly_summary_dirty_keys`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_monthly_summary_dirty_products`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_monthly_summary_dirty_scopes`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_netshop_projection`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_netshop_projection_control`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_overview_response_cache`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_price_band_items`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_price_band_versions`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_price_snapshots`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_ranking_entries`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_sku_annotations`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_sku_gmv_totals`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_subcategory_taxonomy`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_system_kpi_cache_control`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_system_kpi_cache_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `market_write_authority`;--> statement-breakpoint
CREATE VIEW `market_annotation_cloud_runs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_commit_receipts` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_concurrency_settings` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_items` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_jobs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_local_agents` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_prompt_audits` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_prompt_versions` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_validation_results` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_validation_runs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_annotation_validation_samples` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_brand_recognition_jobs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_brand_seeds` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_brand_suggestions` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_download_configs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_download_staging_rows` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_download_tasks` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_effective_metrics_cache` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_effective_metrics_cache_state` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_image_cache` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_image_cache_claims` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_image_cache_job_items` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_image_cache_jobs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_import_batches` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_import_identity_refresh_keys_v2` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_import_range_claims` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_import_staging_rows` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_master_audit_logs` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_master_database_filters_cache_state` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_master_identities` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_master_mapping_rules` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_monthly_summary_cache` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_monthly_summary_cache_state` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_monthly_summary_dirty_keys` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_monthly_summary_dirty_products` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_monthly_summary_dirty_scopes` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_netshop_projection` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_netshop_projection_control` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_overview_response_cache` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_price_band_items` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_price_band_versions` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_price_snapshots` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_ranking_entries` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_sku_annotations` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_sku_gmv_totals` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_subcategory_taxonomy` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_system_kpi_cache_control` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_system_kpi_cache_state` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `market_write_authority` AS SELECT 'market-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed',`completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='market' AND `version`='market-domain-retirement-receipt-v1'
  AND `status`='approved' AND `completed_at` IS NULL;
