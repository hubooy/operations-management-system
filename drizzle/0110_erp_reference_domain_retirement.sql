-- Operator-only terminal retirement for the D1 ERP products/combos domain.
-- The operator installs one exact approved receipt in this same transaction.
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
  (SELECT COUNT(*) FROM `erp_reference_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='erp-reference'
      AND `version`='erp-reference-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `erp_reference_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
  AND (SELECT COUNT(*) FROM `erp_reference_import_batches` WHERE `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints`
    WHERE `domain`='erp-reference' AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts`
    WHERE `domain`='erp-reference' AND `outcome`='processing')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads`
    WHERE `domain`='erp-reference'
      AND (`status`<>'ready' OR COALESCE(`owner_token`,'')<>''))=0
  AND (SELECT COUNT(*) FROM `inventory_import_upload_chunks` c
    JOIN `inventory_import_uploads` u ON u.`id`=c.`upload_id`
    WHERE u.`fingerprint` LIKE 'erp:products:%' OR u.`fingerprint` LIKE 'erp:combos:%')=0
  AND (SELECT COUNT(*) FROM `inventory_import_uploads`
    WHERE (`fingerprint` LIKE 'erp:products:%' OR `fingerprint` LIKE 'erp:combos:%')
      AND `status`<>'completed')=0
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `erp_reference_authority_no_recreate`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_authority_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_batches_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_batches_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_batches_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_products_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_products_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_products_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_combos_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_combos_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_combos_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_source_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_source_update_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_source_delete_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_state_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_state_update_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_state_delete_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_outbox_insert_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_outbox_update_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_outbox_delete_authority_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_fingerprints_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_fingerprints_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_fingerprints_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_attempts_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_attempts_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_attempts_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_heads_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_heads_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_heads_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_uploads_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_uploads_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_uploads_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_upload_chunks_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_upload_chunks_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_upload_chunks_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_upload_results_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_upload_results_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_upload_results_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_projection_source_no_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_projection_source_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_product_projection_state_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_product_projection_state_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_projection_outbox_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_projection_outbox_no_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_reference_projection_outbox_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `erp_product_import_requires_projection_event`;--> statement-breakpoint

DELETE FROM `inventory_import_upload_results` WHERE `upload_id` IN (
  SELECT `id` FROM `inventory_import_uploads`
  WHERE `fingerprint` LIKE 'erp:products:%' OR `fingerprint` LIKE 'erp:combos:%'
);--> statement-breakpoint
DELETE FROM `inventory_import_upload_chunks` WHERE `upload_id` IN (
  SELECT `id` FROM `inventory_import_uploads`
  WHERE `fingerprint` LIKE 'erp:products:%' OR `fingerprint` LIKE 'erp:combos:%'
);--> statement-breakpoint
DELETE FROM `inventory_import_uploads`
WHERE `fingerprint` LIKE 'erp:products:%' OR `fingerprint` LIKE 'erp:combos:%';--> statement-breakpoint
DELETE FROM `import_content_fingerprints` WHERE `domain`='erp-reference';--> statement-breakpoint
DELETE FROM `import_content_attempts` WHERE `domain`='erp-reference';--> statement-breakpoint
DELETE FROM `import_scope_heads` WHERE `domain`='erp-reference';--> statement-breakpoint

DELETE FROM `erp_reference_projection_outbox`;--> statement-breakpoint
DELETE FROM `erp_combo_items`;--> statement-breakpoint
DELETE FROM `erp_product_master`;--> statement-breakpoint
DELETE FROM `erp_reference_import_batches`;--> statement-breakpoint
DROP TABLE `erp_reference_projection_outbox`;--> statement-breakpoint
DROP TABLE `erp_product_projection_state`;--> statement-breakpoint
DROP TABLE `erp_reference_projection_source_state`;--> statement-breakpoint
DROP TABLE `erp_combo_items`;--> statement-breakpoint
DROP TABLE `erp_product_master`;--> statement-breakpoint
DROP TABLE `erp_reference_import_batches`;--> statement-breakpoint
DROP TABLE `erp_reference_write_authority`;--> statement-breakpoint

CREATE VIEW `erp_reference_import_batches` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `source_key`,
  CAST(NULL AS TEXT) AS `source_label`, CAST(NULL AS TEXT) AS `file_name`,
  CAST(NULL AS INTEGER) AS `file_size_bytes`, CAST(NULL AS TEXT) AS `file_hash`,
  CAST(NULL AS TEXT) AS `sheet_name`, CAST(NULL AS TEXT) AS `snapshot_date`,
  CAST(NULL AS TEXT) AS `status`, CAST(NULL AS INTEGER) AS `row_count`,
  CAST(NULL AS INTEGER) AS `inserted_count`, CAST(NULL AS INTEGER) AS `updated_count`,
  CAST(NULL AS INTEGER) AS `excluded_count`, CAST(NULL AS INTEGER) AS `warning_count`,
  CAST(NULL AS TEXT) AS `warnings_json`, CAST(NULL AS TEXT) AS `totals_json`,
  CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `completed_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `erp_product_master` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS TEXT) AS `product_code`, CAST(NULL AS TEXT) AS `product_name`,
  CAST(NULL AS TEXT) AS `brand`, CAST(NULL AS TEXT) AS `specification`,
  CAST(NULL AS TEXT) AS `barcode`, CAST(NULL AS TEXT) AS `category`,
  CAST(NULL AS TEXT) AS `supplier`, CAST(NULL AS TEXT) AS `product_status`,
  CAST(NULL AS INTEGER) AS `source_row_number`, CAST(NULL AS TEXT) AS `last_import_batch_id`,
  CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `erp_combo_items` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS TEXT) AS `parent_code`,
  CAST(NULL AS TEXT) AS `parent_name`, CAST(NULL AS TEXT) AS `child_code`,
  CAST(NULL AS TEXT) AS `child_name`, CAST(NULL AS INTEGER) AS `child_quantity_milli`,
  CAST(NULL AS INTEGER) AS `source_row_number`, CAST(NULL AS TEXT) AS `last_import_batch_id`,
  CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `erp_reference_projection_source_state` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS TEXT) AS `source_epoch`,
  CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `erp_product_projection_state` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS INTEGER) AS `erp_revision`,
  CAST(NULL AS TEXT) AS `source_batch_id`, CAST(NULL AS INTEGER) AS `row_count`,
  CAST(NULL AS TEXT) AS `content_hash`, CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `erp_reference_projection_outbox` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `event_sequence`, CAST(NULL AS TEXT) AS `event_id`,
  CAST(NULL AS TEXT) AS `source_epoch`, CAST(NULL AS TEXT) AS `domain`,
  CAST(NULL AS TEXT) AS `operation`, CAST(NULL AS TEXT) AS `scope_json`,
  CAST(NULL AS TEXT) AS `source_batch_id`, CAST(NULL AS INTEGER) AS `erp_revision`,
  CAST(NULL AS INTEGER) AS `row_count`, CAST(NULL AS TEXT) AS `content_hash`,
  CAST(NULL AS TEXT) AS `canonical_format_version`, CAST(NULL AS TEXT) AS `created_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `erp_reference_write_authority` AS SELECT
  /* erp-reference-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS TEXT) AS `owner`,
  CAST(NULL AS INTEGER) AS `epoch`, CAST(NULL AS TEXT) AS `cutover_id`,
  CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint

CREATE TRIGGER `erp_reference_retired_fingerprints_insert_guard` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_fingerprints_update_guard` BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain`='erp-reference' OR NEW.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_fingerprints_delete_guard` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_attempts_insert_guard` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_attempts_update_guard` BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain`='erp-reference' OR NEW.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_attempts_delete_guard` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_heads_insert_guard` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_heads_update_guard` BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain`='erp-reference' OR NEW.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_heads_delete_guard` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain`='erp-reference' BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_uploads_insert_guard` BEFORE INSERT ON `inventory_import_uploads`
WHEN NEW.`fingerprint` LIKE 'erp:products:%' OR NEW.`fingerprint` LIKE 'erp:combos:%'
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_uploads_update_guard` BEFORE UPDATE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'erp:products:%' OR NEW.`fingerprint` LIKE 'erp:products:%'
  OR OLD.`fingerprint` LIKE 'erp:combos:%' OR NEW.`fingerprint` LIKE 'erp:combos:%'
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_uploads_delete_guard` BEFORE DELETE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'erp:products:%' OR OLD.`fingerprint` LIKE 'erp:combos:%'
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_upload_chunks_insert_guard` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_upload_chunks_update_guard` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_upload_chunks_delete_guard` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_upload_results_insert_guard` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_upload_results_update_guard` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `erp_reference_retired_upload_results_delete_guard` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
BEGIN SELECT RAISE(ABORT,'erp_reference_domain_retired'); END;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed', `completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='erp-reference' AND `status`='approved';
