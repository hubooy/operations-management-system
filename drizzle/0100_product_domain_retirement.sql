-- Operator-only terminal retirement for the D1 product-operations domain.
-- A controlled operator must install one exact approved receipt in the same
-- BEGIN IMMEDIATE transaction. This file is absent from the Drizzle journal.
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
  (SELECT COUNT(*) FROM `product_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='products'
      AND `version`='products-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `product_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
  AND (SELECT COUNT(*) FROM `product_shipping_rate_import_batches` WHERE `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints`
    WHERE `domain`='product-shipping-rates' AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts`
    WHERE `domain`='product-shipping-rates' AND `outcome`='processing')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads`
    WHERE `domain`='product-shipping-rates'
      AND (`status`<>'ready' OR COALESCE(`owner_token`,'')<>''))=0
  -- Chunk rows contain the only durable R2 object keys. They must be removed
  -- by the cutover operator before the borrowed upload namespace is retired.
  AND (SELECT COUNT(*) FROM `inventory_import_upload_chunks` c
    JOIN `inventory_import_uploads` u ON u.`id`=c.`upload_id`
    WHERE u.`fingerprint` LIKE 'sku-shipping-rates:%')=0
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `product_authority_singleton_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_singleton_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_batches_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_batches_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_batches_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_rates_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_rates_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_rates_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_fingerprints_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_fingerprints_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_fingerprints_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_attempts_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_attempts_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_attempts_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_heads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_heads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_heads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_uploads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_uploads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_uploads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_upload_chunks_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_upload_chunks_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_upload_chunks_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_upload_results_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_upload_results_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `product_authority_upload_results_delete`;--> statement-breakpoint

DELETE FROM `inventory_import_upload_results`
WHERE `upload_id` IN (SELECT `id` FROM `inventory_import_uploads` WHERE `fingerprint` LIKE 'sku-shipping-rates:%');--> statement-breakpoint
DELETE FROM `inventory_import_uploads` WHERE `fingerprint` LIKE 'sku-shipping-rates:%';--> statement-breakpoint
DELETE FROM `import_content_fingerprints` WHERE `domain`='product-shipping-rates';--> statement-breakpoint
DELETE FROM `import_content_attempts` WHERE `domain`='product-shipping-rates';--> statement-breakpoint
DELETE FROM `import_scope_heads` WHERE `domain`='product-shipping-rates';--> statement-breakpoint

CREATE TRIGGER `product_retired_fingerprints_insert_guard` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_fingerprints_update_guard` BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain`='product-shipping-rates' OR NEW.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_fingerprints_delete_guard` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_attempts_insert_guard` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_attempts_update_guard` BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain`='product-shipping-rates' OR NEW.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_attempts_delete_guard` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_heads_insert_guard` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_heads_update_guard` BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain`='product-shipping-rates' OR NEW.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_heads_delete_guard` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain`='product-shipping-rates'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `product_retired_uploads_insert_guard` BEFORE INSERT ON `inventory_import_uploads`
WHEN NEW.`fingerprint` LIKE 'sku-shipping-rates:%'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_uploads_update_guard` BEFORE UPDATE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'sku-shipping-rates:%' OR NEW.`fingerprint` LIKE 'sku-shipping-rates:%'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_uploads_delete_guard` BEFORE DELETE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'sku-shipping-rates:%'
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_upload_chunks_insert_guard` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'sku-shipping-rates:%')
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_upload_chunks_update_guard` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'sku-shipping-rates:%')
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_upload_chunks_delete_guard` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'sku-shipping-rates:%')
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_upload_results_insert_guard` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'sku-shipping-rates:%')
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_upload_results_update_guard` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'sku-shipping-rates:%')
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `product_retired_upload_results_delete_guard` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'sku-shipping-rates:%')
BEGIN SELECT RAISE(ABORT,'product_domain_retired'); END;--> statement-breakpoint

DROP TABLE IF EXISTS `product_shipping_rates`;--> statement-breakpoint
DROP TABLE IF EXISTS `product_shipping_rate_import_batches`;--> statement-breakpoint
DROP TABLE IF EXISTS `product_write_authority`;--> statement-breakpoint
CREATE VIEW `product_shipping_rates` AS SELECT 'products-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `product_shipping_rate_import_batches` AS SELECT 'products-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint
CREATE VIEW `product_write_authority` AS SELECT 'products-domain-retired-v1' AS `retirement_tombstone` WHERE 0;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed',`completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='products' AND `version`='products-domain-retirement-receipt-v1'
  AND `status`='approved' AND `completed_at` IS NULL;
