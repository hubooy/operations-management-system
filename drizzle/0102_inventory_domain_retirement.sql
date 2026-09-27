-- Operator-only terminal retirement for the D1 inventory domain.
-- The operator must install one exact approved receipt in the same
-- BEGIN IMMEDIATE transaction. Intentionally absent from the journal.
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
  (SELECT COUNT(*) FROM `inventory_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='inventory'
      AND `version`='inventory-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `inventory_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
  AND (SELECT COUNT(*) FROM `inventory_import_batches` WHERE `status`='processing')=0
  AND (SELECT COUNT(*) FROM `erp_reference_import_batches`
    WHERE `source_key`='inventory_age' AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints`
    WHERE (`domain`='inventory-stock'
      OR (`domain`='erp-reference' AND json_extract(`scope_json`,'$.source')='inventory_age'))
      AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts`
    WHERE (`domain`='inventory-stock'
      OR (`domain`='erp-reference' AND json_extract(`scope_json`,'$.source')='inventory_age'))
      AND `outcome`='processing')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads`
    WHERE (`domain`='inventory-stock'
      OR (`domain`='erp-reference' AND (
        `scope_key` IN (
          'ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d',
          'c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4'
        )
        OR COALESCE(`current_batch_id`,'') LIKE 'inventory_age:%'
        OR EXISTS (SELECT 1 FROM `erp_reference_import_batches` b
          WHERE b.`id`=`import_scope_heads`.`current_batch_id` AND b.`source_key`='inventory_age')
      )))
      AND (`status`<>'ready' OR COALESCE(`owner_token`,'')<>''))=0
  AND (SELECT COUNT(*) FROM `inventory_import_upload_chunks` c
    JOIN `inventory_import_uploads` u ON u.`id`=c.`upload_id`
    WHERE u.`fingerprint` LIKE 'inventory-v1:%'
      OR u.`fingerprint` LIKE 'erp:inventory_age:%')=0
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `inventory_authority_singleton_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_singleton_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_batches_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_batches_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_batches_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_stock_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_stock_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_stock_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_metrics_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_metrics_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_metrics_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_plans_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_plans_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_plans_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_age_batches_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_age_batches_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_age_batches_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_age_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_age_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_age_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_settings_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_settings_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_settings_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_fingerprints_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_fingerprints_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_fingerprints_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_attempts_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_attempts_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_attempts_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_heads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_heads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_heads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_uploads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_uploads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_uploads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_upload_chunks_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_upload_chunks_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_upload_chunks_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_upload_results_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_upload_results_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `inventory_authority_upload_results_delete`;--> statement-breakpoint

DELETE FROM `inventory_import_upload_results`
WHERE `upload_id` IN (
  SELECT `id` FROM `inventory_import_uploads`
  WHERE `fingerprint` LIKE 'inventory-v1:%' OR `fingerprint` LIKE 'erp:inventory_age:%'
);--> statement-breakpoint
DELETE FROM `inventory_import_uploads`
WHERE `fingerprint` LIKE 'inventory-v1:%' OR `fingerprint` LIKE 'erp:inventory_age:%';--> statement-breakpoint
DELETE FROM `import_content_fingerprints`
WHERE `domain`='inventory-stock'
  OR (`domain`='erp-reference' AND json_extract(`scope_json`,'$.source')='inventory_age');--> statement-breakpoint
DELETE FROM `import_content_attempts`
WHERE `domain`='inventory-stock'
  OR (`domain`='erp-reference' AND json_extract(`scope_json`,'$.source')='inventory_age');--> statement-breakpoint
DELETE FROM `import_scope_heads`
WHERE `domain`='inventory-stock'
  OR (`domain`='erp-reference' AND (
    `scope_key` IN (
      'ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d',
      'c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4'
    )
    OR COALESCE(`current_batch_id`,'') LIKE 'inventory_age:%'
    OR EXISTS (SELECT 1 FROM `erp_reference_import_batches` b
      WHERE b.`id`=`import_scope_heads`.`current_batch_id` AND b.`source_key`='inventory_age')
  ));--> statement-breakpoint
DELETE FROM `erp_reference_import_batches` WHERE `source_key`='inventory_age';--> statement-breakpoint
DELETE FROM `system_settings` WHERE `key`='operating';--> statement-breakpoint

CREATE TRIGGER `inventory_retired_age_batches_insert_guard` BEFORE INSERT ON `erp_reference_import_batches`
WHEN NEW.`source_key`='inventory_age'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_age_batches_update_guard` BEFORE UPDATE ON `erp_reference_import_batches`
WHEN OLD.`source_key`='inventory_age' OR NEW.`source_key`='inventory_age'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_age_batches_delete_guard` BEFORE DELETE ON `erp_reference_import_batches`
WHEN OLD.`source_key`='inventory_age'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_settings_insert_guard` BEFORE INSERT ON `system_settings`
WHEN NEW.`key`='operating'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_settings_update_guard` BEFORE UPDATE ON `system_settings`
WHEN OLD.`key`='operating' OR NEW.`key`='operating'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_settings_delete_guard` BEFORE DELETE ON `system_settings`
WHEN OLD.`key`='operating'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `inventory_retired_fingerprints_insert_guard` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain`='inventory-stock'
  OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age')
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_fingerprints_update_guard` BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain`='inventory-stock' OR NEW.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age')
  OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age')
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_fingerprints_delete_guard` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age')
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_attempts_insert_guard` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain`='inventory-stock'
  OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age')
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_attempts_update_guard` BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain`='inventory-stock' OR NEW.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age')
  OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age')
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_attempts_delete_guard` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age')
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_heads_insert_guard` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain`='inventory-stock'
  OR (NEW.`domain`='erp-reference' AND (
    NEW.`scope_key` IN ('ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d','c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4')
    OR COALESCE(NEW.`current_batch_id`,'') LIKE 'inventory_age:%'
  ))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_heads_update_guard` BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain`='inventory-stock' OR NEW.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND (
    OLD.`scope_key` IN ('ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d','c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4')
    OR COALESCE(OLD.`current_batch_id`,'') LIKE 'inventory_age:%'
  ))
  OR (NEW.`domain`='erp-reference' AND (
    NEW.`scope_key` IN ('ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d','c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4')
    OR COALESCE(NEW.`current_batch_id`,'') LIKE 'inventory_age:%'
  ))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_heads_delete_guard` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND (
    OLD.`scope_key` IN ('ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d','c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4')
    OR COALESCE(OLD.`current_batch_id`,'') LIKE 'inventory_age:%'
  ))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `inventory_retired_uploads_insert_guard` BEFORE INSERT ON `inventory_import_uploads`
WHEN NEW.`fingerprint` LIKE 'inventory-v1:%' OR NEW.`fingerprint` LIKE 'erp:inventory_age:%'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_uploads_update_guard` BEFORE UPDATE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'inventory-v1:%' OR NEW.`fingerprint` LIKE 'inventory-v1:%'
  OR OLD.`fingerprint` LIKE 'erp:inventory_age:%' OR NEW.`fingerprint` LIKE 'erp:inventory_age:%'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_uploads_delete_guard` BEFORE DELETE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'inventory-v1:%' OR OLD.`fingerprint` LIKE 'erp:inventory_age:%'
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_upload_chunks_insert_guard` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_upload_chunks_update_guard` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_upload_chunks_delete_guard` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_upload_results_insert_guard` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_upload_results_update_guard` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `inventory_retired_upload_results_delete_guard` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
BEGIN SELECT RAISE(ABORT,'inventory_domain_retired'); END;--> statement-breakpoint

DROP TABLE IF EXISTS `erp_inventory_age_lines`;--> statement-breakpoint
DROP TABLE IF EXISTS `inventory_age_metrics`;--> statement-breakpoint
DROP TABLE IF EXISTS `inventory_stock_lines`;--> statement-breakpoint
DROP TABLE IF EXISTS `replenishment_plan_items`;--> statement-breakpoint
DROP TABLE IF EXISTS `inventory_import_batches`;--> statement-breakpoint
DROP TABLE IF EXISTS `inventory_write_authority`;--> statement-breakpoint
CREATE VIEW `erp_inventory_age_lines` AS SELECT 'inventory-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `inventory_age_metrics` AS SELECT 'inventory-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `inventory_stock_lines` AS SELECT 'inventory-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `replenishment_plan_items` AS SELECT 'inventory-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `inventory_import_batches` AS SELECT 'inventory-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `inventory_write_authority` AS SELECT 'inventory-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed',`completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='inventory' AND `version`='inventory-domain-retirement-receipt-v1'
  AND `status`='approved' AND `completed_at` IS NULL;
