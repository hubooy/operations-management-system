-- Operator-only preparation for the product-operations single-write cutover.
-- This is behavior-neutral while owner=d1 and is intentionally absent from
-- the ordinary Drizzle journal.
CREATE TABLE IF NOT EXISTS `product_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id`=1),
  `owner` text NOT NULL CHECK (`owner` IN ('d1','pending','postgresql')),
  `epoch` integer NOT NULL CHECK (`epoch`>=1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `product_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'d1',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `product_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `product_authority_singleton_insert_guard`
BEFORE INSERT ON `product_write_authority`
BEGIN SELECT RAISE(ABORT,'product_write_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_singleton_delete_guard`
BEFORE DELETE ON `product_write_authority`
BEGIN SELECT RAISE(ABORT,'product_write_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_transition_guard`
BEFORE UPDATE ON `product_write_authority`
WHEN NOT (
  NEW.`id`=OLD.`id` AND NEW.`epoch`=OLD.`epoch`+1
  AND length(NEW.`cutover_id`) BETWEEN 8 AND 128
  AND (
    (OLD.`owner`='d1' AND NEW.`owner`='pending')
    OR (OLD.`owner`='pending' AND NEW.`owner`='d1' AND NEW.`cutover_id`=OLD.`cutover_id`)
    OR (OLD.`owner`='pending' AND NEW.`owner`='postgresql' AND NEW.`cutover_id`=OLD.`cutover_id`)
  )
)
BEGIN SELECT RAISE(ABORT,'product_write_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `product_authority_batches_insert` BEFORE INSERT ON `product_shipping_rate_import_batches`
WHEN COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_batches_update` BEFORE UPDATE ON `product_shipping_rate_import_batches`
WHEN COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_batches_delete` BEFORE DELETE ON `product_shipping_rate_import_batches`
WHEN COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_rates_insert` BEFORE INSERT ON `product_shipping_rates`
WHEN COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_rates_update` BEFORE UPDATE ON `product_shipping_rates`
WHEN COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_rates_delete` BEFORE DELETE ON `product_shipping_rates`
WHEN COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `product_authority_fingerprints_insert` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.domain='product-shipping-rates' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_fingerprints_update` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.domain='product-shipping-rates' OR NEW.domain='product-shipping-rates') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_fingerprints_delete` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.domain='product-shipping-rates' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_attempts_insert` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.domain='product-shipping-rates' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_attempts_update` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.domain='product-shipping-rates' OR NEW.domain='product-shipping-rates') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_attempts_delete` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.domain='product-shipping-rates' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_heads_insert` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.domain='product-shipping-rates' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_heads_update` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.domain='product-shipping-rates' OR NEW.domain='product-shipping-rates') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_heads_delete` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.domain='product-shipping-rates' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint

-- The retired product upload endpoint formerly borrowed inventory upload
-- tables. Fence only its exact fingerprint namespace; inventory stays D1-owned.
CREATE TRIGGER IF NOT EXISTS `product_authority_uploads_insert` BEFORE INSERT ON `inventory_import_uploads`
WHEN NEW.fingerprint LIKE 'sku-shipping-rates:%' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_uploads_update` BEFORE UPDATE ON `inventory_import_uploads`
WHEN (OLD.fingerprint LIKE 'sku-shipping-rates:%' OR NEW.fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_uploads_delete` BEFORE DELETE ON `inventory_import_uploads`
WHEN OLD.fingerprint LIKE 'sku-shipping-rates:%' AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_upload_chunks_insert` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_upload_chunks_update` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_upload_chunks_delete` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_upload_results_insert` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_upload_results_update` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `product_authority_upload_results_delete` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'sku-shipping-rates:%') AND COALESCE((SELECT owner FROM product_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'product_write_authority_not_d1'); END;
