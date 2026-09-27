-- Operator-only. Intentionally excluded from the Drizzle journal.
-- Freezes every remaining D1 ERP products/combos write path before PostgreSQL activation.

CREATE TABLE IF NOT EXISTS `erp_reference_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id`=1),
  `owner` text NOT NULL CHECK (`owner` IN ('legacy','pending','postgresql')),
  `epoch` integer NOT NULL DEFAULT 1 CHECK (`epoch`>=1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `erp_reference_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'legacy',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `erp_reference_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `erp_reference_authority_no_recreate`
BEFORE INSERT ON `erp_reference_write_authority`
WHEN EXISTS (SELECT 1 FROM `erp_reference_write_authority` WHERE `id`=1)
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_authority_no_delete`
BEFORE DELETE ON `erp_reference_write_authority`
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_authority_transition_guard`
BEFORE UPDATE ON `erp_reference_write_authority`
WHEN NEW.id<>1 OR OLD.id<>1 OR NEW.epoch<>OLD.epoch+1 OR NEW.cutover_id=''
  OR NOT ((OLD.owner='legacy' AND NEW.owner='pending')
    OR (OLD.owner='pending' AND NEW.owner='legacy' AND NEW.cutover_id=OLD.cutover_id)
    OR (OLD.owner='pending' AND NEW.owner='postgresql' AND NEW.cutover_id=OLD.cutover_id))
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `erp_reference_batches_insert_guard` BEFORE INSERT ON `erp_reference_import_batches`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_batches_update_guard` BEFORE UPDATE ON `erp_reference_import_batches`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_batches_delete_guard` BEFORE DELETE ON `erp_reference_import_batches`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_products_insert_guard` BEFORE INSERT ON `erp_product_master`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_products_update_guard` BEFORE UPDATE ON `erp_product_master`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_products_delete_guard` BEFORE DELETE ON `erp_product_master`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_combos_insert_guard` BEFORE INSERT ON `erp_combo_items`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_combos_update_guard` BEFORE UPDATE ON `erp_combo_items`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_combos_delete_guard` BEFORE DELETE ON `erp_combo_items`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `erp_reference_source_insert_guard` BEFORE INSERT ON `erp_reference_projection_source_state`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_source_update_authority_guard` BEFORE UPDATE ON `erp_reference_projection_source_state`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_source_delete_authority_guard` BEFORE DELETE ON `erp_reference_projection_source_state`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_state_insert_guard` BEFORE INSERT ON `erp_product_projection_state`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_state_update_authority_guard` BEFORE UPDATE ON `erp_product_projection_state`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_state_delete_authority_guard` BEFORE DELETE ON `erp_product_projection_state`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_outbox_insert_authority_guard` BEFORE INSERT ON `erp_reference_projection_outbox`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_outbox_update_authority_guard` BEFORE UPDATE ON `erp_reference_projection_outbox`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_outbox_delete_authority_guard` BEFORE DELETE ON `erp_reference_projection_outbox`
WHEN COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `erp_reference_fingerprints_insert_guard` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain`='erp-reference' AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_fingerprints_update_guard` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.`domain`='erp-reference' OR NEW.`domain`='erp-reference') AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_fingerprints_delete_guard` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain`='erp-reference' AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_attempts_insert_guard` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain`='erp-reference' AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_attempts_update_guard` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.`domain`='erp-reference' OR NEW.`domain`='erp-reference') AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_attempts_delete_guard` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain`='erp-reference' AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_heads_insert_guard` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain`='erp-reference' AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_heads_update_guard` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.`domain`='erp-reference' OR NEW.`domain`='erp-reference') AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_heads_delete_guard` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain`='erp-reference' AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `erp_reference_uploads_insert_guard` BEFORE INSERT ON `inventory_import_uploads`
WHEN (NEW.`fingerprint` LIKE 'erp:products:%' OR NEW.`fingerprint` LIKE 'erp:combos:%')
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_uploads_update_guard` BEFORE UPDATE ON `inventory_import_uploads`
WHEN (OLD.`fingerprint` LIKE 'erp:products:%' OR NEW.`fingerprint` LIKE 'erp:products:%'
  OR OLD.`fingerprint` LIKE 'erp:combos:%' OR NEW.`fingerprint` LIKE 'erp:combos:%')
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_uploads_delete_guard` BEFORE DELETE ON `inventory_import_uploads`
WHEN (OLD.`fingerprint` LIKE 'erp:products:%' OR OLD.`fingerprint` LIKE 'erp:combos:%')
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_upload_chunks_insert_guard` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_upload_chunks_update_guard` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_upload_chunks_delete_guard` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_upload_results_insert_guard` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_upload_results_update_guard` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_upload_results_delete_guard` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'))
  AND COALESCE((SELECT owner FROM erp_reference_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'erp_reference_authority_not_legacy'); END;
