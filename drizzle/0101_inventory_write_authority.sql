-- Operator-only preparation for the inventory single-write cutover.
-- Behavior-neutral while owner=d1; intentionally absent from the journal.
CREATE TABLE IF NOT EXISTS `inventory_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id`=1),
  `owner` text NOT NULL CHECK (`owner` IN ('d1','pending','postgresql')),
  `epoch` integer NOT NULL CHECK (`epoch`>=1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `inventory_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'d1',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `inventory_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `inventory_authority_singleton_insert_guard`
BEFORE INSERT ON `inventory_write_authority`
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_singleton_delete_guard`
BEFORE DELETE ON `inventory_write_authority`
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_transition_guard`
BEFORE UPDATE ON `inventory_write_authority`
WHEN NOT (
  NEW.`id`=OLD.`id` AND NEW.`epoch`=OLD.`epoch`+1
  AND length(NEW.`cutover_id`) BETWEEN 8 AND 128
  AND (
    (OLD.`owner`='d1' AND NEW.`owner`='pending')
    OR (OLD.`owner`='pending' AND NEW.`owner`='d1' AND NEW.`cutover_id`=OLD.`cutover_id`)
    OR (OLD.`owner`='pending' AND NEW.`owner`='postgresql' AND NEW.`cutover_id`=OLD.`cutover_id`)
  )
)
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `inventory_authority_batches_insert` BEFORE INSERT ON `inventory_import_batches`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_batches_update` BEFORE UPDATE ON `inventory_import_batches`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_batches_delete` BEFORE DELETE ON `inventory_import_batches`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_stock_insert` BEFORE INSERT ON `inventory_stock_lines`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_stock_update` BEFORE UPDATE ON `inventory_stock_lines`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_stock_delete` BEFORE DELETE ON `inventory_stock_lines`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_metrics_insert` BEFORE INSERT ON `inventory_age_metrics`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_metrics_update` BEFORE UPDATE ON `inventory_age_metrics`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_metrics_delete` BEFORE DELETE ON `inventory_age_metrics`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_plans_insert` BEFORE INSERT ON `replenishment_plan_items`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_plans_update` BEFORE UPDATE ON `replenishment_plan_items`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_plans_delete` BEFORE DELETE ON `replenishment_plan_items`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `inventory_authority_age_batches_insert` BEFORE INSERT ON `erp_reference_import_batches`
WHEN NEW.`source_key`='inventory_age' AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_age_batches_update` BEFORE UPDATE ON `erp_reference_import_batches`
WHEN (OLD.`source_key`='inventory_age' OR NEW.`source_key`='inventory_age') AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_age_batches_delete` BEFORE DELETE ON `erp_reference_import_batches`
WHEN OLD.`source_key`='inventory_age' AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_age_insert` BEFORE INSERT ON `erp_inventory_age_lines`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_age_update` BEFORE UPDATE ON `erp_inventory_age_lines`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_age_delete` BEFORE DELETE ON `erp_inventory_age_lines`
WHEN COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `inventory_authority_settings_insert` BEFORE INSERT ON `system_settings`
WHEN NEW.`key`='operating' AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_settings_update` BEFORE UPDATE ON `system_settings`
WHEN (OLD.`key`='operating' OR NEW.`key`='operating') AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_settings_delete` BEFORE DELETE ON `system_settings`
WHEN OLD.`key`='operating' AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `inventory_authority_fingerprints_insert` BEFORE INSERT ON `import_content_fingerprints`
WHEN (NEW.`domain`='inventory-stock' OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_fingerprints_update` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.`domain`='inventory-stock' OR NEW.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age')
  OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_fingerprints_delete` BEFORE DELETE ON `import_content_fingerprints`
WHEN (OLD.`domain`='inventory-stock' OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_attempts_insert` BEFORE INSERT ON `import_content_attempts`
WHEN (NEW.`domain`='inventory-stock' OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_attempts_update` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.`domain`='inventory-stock' OR NEW.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age')
  OR (NEW.`domain`='erp-reference' AND json_extract(NEW.`scope_json`,'$.source')='inventory_age'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_attempts_delete` BEFORE DELETE ON `import_content_attempts`
WHEN (OLD.`domain`='inventory-stock' OR (OLD.`domain`='erp-reference' AND json_extract(OLD.`scope_json`,'$.source')='inventory_age'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_heads_insert` BEFORE INSERT ON `import_scope_heads`
WHEN (NEW.`domain`='inventory-stock' OR (NEW.`domain`='erp-reference' AND (
    NEW.`scope_key` IN (
      'ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d',
      'c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4'
    )
    OR COALESCE(NEW.`current_batch_id`,'') LIKE 'inventory_age:%'
    OR EXISTS (SELECT 1 FROM `erp_reference_import_batches` b
      WHERE b.`id`=NEW.`current_batch_id` AND b.`source_key`='inventory_age')
  )))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_heads_update` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.`domain`='inventory-stock' OR NEW.`domain`='inventory-stock'
  OR (OLD.`domain`='erp-reference' AND (
    OLD.`scope_key` IN ('ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d','c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4')
    OR COALESCE(OLD.`current_batch_id`,'') LIKE 'inventory_age:%'
    OR EXISTS (SELECT 1 FROM `erp_reference_import_batches` b WHERE b.`id`=OLD.`current_batch_id` AND b.`source_key`='inventory_age')
  ))
  OR (NEW.`domain`='erp-reference' AND (
    NEW.`scope_key` IN ('ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d','c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4')
    OR COALESCE(NEW.`current_batch_id`,'') LIKE 'inventory_age:%'
    OR EXISTS (SELECT 1 FROM `erp_reference_import_batches` b WHERE b.`id`=NEW.`current_batch_id` AND b.`source_key`='inventory_age')
  )))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_heads_delete` BEFORE DELETE ON `import_scope_heads`
WHEN (OLD.`domain`='inventory-stock' OR (OLD.`domain`='erp-reference' AND (
    OLD.`scope_key` IN (
      'ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d',
      'c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4'
    )
    OR COALESCE(OLD.`current_batch_id`,'') LIKE 'inventory_age:%'
    OR EXISTS (SELECT 1 FROM `erp_reference_import_batches` b
      WHERE b.`id`=OLD.`current_batch_id` AND b.`source_key`='inventory_age')
  )))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `inventory_authority_uploads_insert` BEFORE INSERT ON `inventory_import_uploads`
WHEN (NEW.`fingerprint` LIKE 'inventory-v1:%' OR NEW.`fingerprint` LIKE 'erp:inventory_age:%')
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_uploads_update` BEFORE UPDATE ON `inventory_import_uploads`
WHEN (OLD.`fingerprint` LIKE 'inventory-v1:%' OR NEW.`fingerprint` LIKE 'inventory-v1:%'
  OR OLD.`fingerprint` LIKE 'erp:inventory_age:%' OR NEW.`fingerprint` LIKE 'erp:inventory_age:%')
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_uploads_delete` BEFORE DELETE ON `inventory_import_uploads`
WHEN (OLD.`fingerprint` LIKE 'inventory-v1:%' OR OLD.`fingerprint` LIKE 'erp:inventory_age:%')
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_upload_chunks_insert` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_upload_chunks_update` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_upload_chunks_delete` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_upload_results_insert` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_upload_results_update` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `inventory_authority_upload_results_delete` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'))
  AND COALESCE((SELECT owner FROM inventory_write_authority WHERE id=1),'')<>'d1'
BEGIN SELECT RAISE(ABORT,'inventory_write_authority_not_d1'); END;
