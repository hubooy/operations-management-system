-- Operator-only terminal retirement for the D1-owned netshop domain.  This
-- migration is deliberately absent from the ordinary Drizzle journal.  The
-- controlled operator must install one exact approved receipt in the same
-- BEGIN IMMEDIATE transaction before executing this file.
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
  CHECK (
    (`status`='approved' AND `completed_at` IS NULL)
    OR (`status`='completed' AND `completed_at` IS NOT NULL)
  )
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

-- The PostgreSQL handoff is terminal, all D1 write owners must be quiet, and
-- the D1-owned market consumer must already expose one complete revision.
-- Integer overflow is a read-only assertion because SQLite RAISE() is legal
-- only inside a trigger body.
SELECT CASE WHEN ((
  (SELECT COUNT(*) FROM `netshop_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='netshop'
      AND `version`='netshop-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `netshop_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
  AND (SELECT COUNT(*) FROM `netshop_import_batches` WHERE `status`='processing')=0
  AND (SELECT COUNT(*) FROM `netshop_asset_uploads` WHERE `status`<>'completed')=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints`
    WHERE `domain`='netshop' AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts`
    WHERE `domain`='netshop' AND `outcome`='processing')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads`
    WHERE `domain`='netshop' AND (`status`<>'ready' OR COALESCE(`owner_token`,'')<>''))=0
  AND (SELECT COUNT(*) FROM `market_netshop_projection_control`
    WHERE `id`=1 AND `active_revision`<>'' AND `active_total`>0
      AND `syncing_revision`='' AND `owner_token`='')=1
  AND (SELECT `active_total` FROM `market_netshop_projection_control` WHERE `id`=1)
    =(SELECT COUNT(*) FROM `market_netshop_projection`
      WHERE `projection_revision`=(SELECT `active_revision`
        FROM `market_netshop_projection_control` WHERE `id`=1))
) OR (
  -- A pristine bootstrap may replay the whole migration directory without
  -- inventing cutover evidence.  Any non-empty netshop-owned object or shared
  -- import row closes this path.
  (SELECT COUNT(*) FROM `netshop_write_authority`
    WHERE `id`=1 AND `owner`='d1' AND `cutover_id`='')=1
  AND (SELECT COUNT(*) FROM `netshop_import_batches`)=0
  AND (SELECT COUNT(*) FROM `netshop_rows`)=0
  AND (SELECT COUNT(*) FROM `netshop_product_daily_revisions`)=0
  AND (SELECT COUNT(*) FROM `netshop_product_daily_scope_revisions`)=0
  AND (SELECT COUNT(*) FROM `netshop_promotion_product_daily`)=0
  AND (SELECT COUNT(*) FROM `netshop_promotion_shop_daily`)=0
  AND (SELECT COUNT(*) FROM `netshop_promotion_aggregate_state`)=0
  AND (SELECT COUNT(*) FROM `netshop_promotion_aggregate_manifest`)=0
  AND (SELECT COUNT(*) FROM `netshop_promotion_aggregate_control`)=0
  AND (SELECT COUNT(*) FROM `netshop_promotion_scope_revisions`)=0
  AND (SELECT COUNT(*) FROM `netshop_asset_uploads`)=0
  AND (SELECT COUNT(*) FROM `netshop_asset_upload_chunks`)=0
  AND (SELECT COUNT(*) FROM `netshop_asset_upload_results`)=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints` WHERE `domain`='netshop')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts` WHERE `domain`='netshop')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads` WHERE `domain`='netshop')=0
  AND (SELECT COUNT(*) FROM `market_netshop_projection`)=0
  AND (SELECT COUNT(*) FROM `market_netshop_projection_control`
    WHERE `id`=1 AND `active_revision`='' AND `active_total`=0
      AND `syncing_revision`='' AND `owner_token`='')=1
)) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

-- Remove all old authority and derived-maintenance triggers before deleting
-- shared rows or owned tables.
DROP TRIGGER IF EXISTS `netshop_authority_singleton_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_singleton_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_batches_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_batches_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_batches_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_rows_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_rows_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_rows_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_product_revisions_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_product_revisions_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_product_revisions_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_product_scope_revisions_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_product_scope_revisions_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_product_scope_revisions_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_products_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_products_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_products_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_shops_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_shops_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_shops_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_state_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_state_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_state_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_manifest_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_manifest_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_manifest_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_control_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_control_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_control_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_revisions_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_revisions_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_promotion_revisions_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_uploads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_uploads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_uploads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_upload_chunks_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_upload_chunks_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_upload_chunks_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_upload_results_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_upload_results_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_upload_results_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_fingerprints_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_fingerprints_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_fingerprints_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_attempts_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_attempts_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_attempts_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_heads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_heads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_authority_heads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_bootstrap_after_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_update_new`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_update_old`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_delete`;--> statement-breakpoint

DELETE FROM `import_content_fingerprints` WHERE `domain`='netshop';--> statement-breakpoint
DELETE FROM `import_content_attempts` WHERE `domain`='netshop';--> statement-breakpoint
DELETE FROM `import_scope_heads` WHERE `domain`='netshop';--> statement-breakpoint

-- Shared import tables remain available to other domains.  These permanent
-- guards prevent insertion, reclassification, mutation, or deletion of the
-- retired exact domain without querying a now-retired authority table.
CREATE TRIGGER `netshop_retired_fingerprints_insert_guard`
BEFORE INSERT ON `import_content_fingerprints` WHEN NEW.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_fingerprints_update_guard`
BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain`='netshop' OR NEW.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_fingerprints_delete_guard`
BEFORE DELETE ON `import_content_fingerprints` WHEN OLD.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_attempts_insert_guard`
BEFORE INSERT ON `import_content_attempts` WHEN NEW.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_attempts_update_guard`
BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain`='netshop' OR NEW.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_attempts_delete_guard`
BEFORE DELETE ON `import_content_attempts` WHEN OLD.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_scope_heads_insert_guard`
BEFORE INSERT ON `import_scope_heads` WHEN NEW.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_scope_heads_update_guard`
BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain`='netshop' OR NEW.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `netshop_retired_scope_heads_delete_guard`
BEFORE DELETE ON `import_scope_heads` WHEN OLD.`domain`='netshop'
BEGIN SELECT RAISE(ABORT,'netshop_domain_retired'); END;--> statement-breakpoint

DROP TABLE IF EXISTS `netshop_asset_upload_chunks`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_asset_upload_results`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_asset_uploads`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_rows`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_import_batches`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_product_daily_revisions`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_product_daily_scope_revisions`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_promotion_product_daily`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_promotion_shop_daily`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_promotion_aggregate_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_promotion_aggregate_manifest`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_promotion_aggregate_control`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_promotion_scope_revisions`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_schema_migrations`;--> statement-breakpoint
DROP TABLE IF EXISTS `netshop_write_authority`;--> statement-breakpoint

CREATE VIEW `netshop_asset_upload_chunks` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_asset_upload_results` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_asset_uploads` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_rows` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_import_batches` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_product_daily_revisions` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_product_daily_scope_revisions` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_promotion_product_daily` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_promotion_shop_daily` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_promotion_aggregate_state` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_promotion_aggregate_manifest` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_promotion_aggregate_control` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_promotion_scope_revisions` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_schema_migrations` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `netshop_write_authority` AS SELECT 'netshop-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed',`completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='netshop' AND `version`='netshop-domain-retirement-receipt-v1'
  AND `status`='approved' AND `completed_at` IS NULL;
