-- Operator-applied preparation for the netshop single-write cutover. This is
-- behavior-neutral while owner=d1. The controlled cutover advances the
-- singleton to pending before the immutable migration snapshot is captured.
CREATE TABLE IF NOT EXISTS `netshop_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('d1', 'pending', 'postgresql')),
  `epoch` integer NOT NULL CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `netshop_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'d1',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `netshop_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_singleton_insert_guard`
BEFORE INSERT ON `netshop_write_authority`
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_singleton_delete_guard`
BEFORE DELETE ON `netshop_write_authority`
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_transition_guard`
BEFORE UPDATE ON `netshop_write_authority`
WHEN NOT (
  NEW.`id`=OLD.`id` AND NEW.`epoch`=OLD.`epoch`+1
  AND length(NEW.`cutover_id`) BETWEEN 8 AND 128
  AND (
    (OLD.`owner`='d1' AND NEW.`owner`='pending')
    OR (OLD.`owner`='pending' AND NEW.`owner`='d1' AND NEW.`cutover_id`=OLD.`cutover_id`)
    OR (OLD.`owner`='pending' AND NEW.`owner`='postgresql' AND NEW.`cutover_id`=OLD.`cutover_id`)
  )
)
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_batches_insert` BEFORE INSERT ON `netshop_import_batches`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_batches_update` BEFORE UPDATE ON `netshop_import_batches`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_batches_delete` BEFORE DELETE ON `netshop_import_batches`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_rows_insert` BEFORE INSERT ON `netshop_rows`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_rows_update` BEFORE UPDATE ON `netshop_rows`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_rows_delete` BEFORE DELETE ON `netshop_rows`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_product_revisions_insert` BEFORE INSERT ON `netshop_product_daily_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_product_revisions_update` BEFORE UPDATE ON `netshop_product_daily_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_product_revisions_delete` BEFORE DELETE ON `netshop_product_daily_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_product_scope_revisions_insert` BEFORE INSERT ON `netshop_product_daily_scope_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_product_scope_revisions_update` BEFORE UPDATE ON `netshop_product_daily_scope_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_product_scope_revisions_delete` BEFORE DELETE ON `netshop_product_daily_scope_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_products_insert` BEFORE INSERT ON `netshop_promotion_product_daily`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_products_update` BEFORE UPDATE ON `netshop_promotion_product_daily`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_products_delete` BEFORE DELETE ON `netshop_promotion_product_daily`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_shops_insert` BEFORE INSERT ON `netshop_promotion_shop_daily`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_shops_update` BEFORE UPDATE ON `netshop_promotion_shop_daily`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_shops_delete` BEFORE DELETE ON `netshop_promotion_shop_daily`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_state_insert` BEFORE INSERT ON `netshop_promotion_aggregate_state`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_state_update` BEFORE UPDATE ON `netshop_promotion_aggregate_state`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_state_delete` BEFORE DELETE ON `netshop_promotion_aggregate_state`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_manifest_insert` BEFORE INSERT ON `netshop_promotion_aggregate_manifest`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_manifest_update` BEFORE UPDATE ON `netshop_promotion_aggregate_manifest`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_manifest_delete` BEFORE DELETE ON `netshop_promotion_aggregate_manifest`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_control_insert` BEFORE INSERT ON `netshop_promotion_aggregate_control`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_control_update` BEFORE UPDATE ON `netshop_promotion_aggregate_control`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_control_delete` BEFORE DELETE ON `netshop_promotion_aggregate_control`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_revisions_insert` BEFORE INSERT ON `netshop_promotion_scope_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_revisions_update` BEFORE UPDATE ON `netshop_promotion_scope_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_promotion_revisions_delete` BEFORE DELETE ON `netshop_promotion_scope_revisions`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_uploads_insert` BEFORE INSERT ON `netshop_asset_uploads`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_uploads_update` BEFORE UPDATE ON `netshop_asset_uploads`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_uploads_delete` BEFORE DELETE ON `netshop_asset_uploads`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_upload_chunks_insert` BEFORE INSERT ON `netshop_asset_upload_chunks`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_upload_chunks_update` BEFORE UPDATE ON `netshop_asset_upload_chunks`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_upload_chunks_delete` BEFORE DELETE ON `netshop_asset_upload_chunks`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_upload_results_insert` BEFORE INSERT ON `netshop_asset_upload_results`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_upload_results_update` BEFORE UPDATE ON `netshop_asset_upload_results`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_upload_results_delete` BEFORE DELETE ON `netshop_asset_upload_results`
WHEN COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_fingerprints_insert` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.domain='netshop' AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_fingerprints_update` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.domain='netshop' OR NEW.domain='netshop') AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_fingerprints_delete` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.domain='netshop' AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_attempts_insert` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.domain='netshop' AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_attempts_update` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.domain='netshop' OR NEW.domain='netshop') AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_attempts_delete` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.domain='netshop' AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `netshop_authority_heads_insert` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.domain='netshop' AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_heads_update` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.domain='netshop' OR NEW.domain='netshop') AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_authority_heads_delete` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.domain='netshop' AND COALESCE((SELECT owner FROM netshop_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'netshop_write_authority_not_d1'); END;
