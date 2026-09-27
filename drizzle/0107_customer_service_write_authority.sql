-- Operator-only. This migration is intentionally excluded from the normal
-- Drizzle journal. It freezes every remaining D1 customer-service write path
-- before PostgreSQL activation.

CREATE TABLE IF NOT EXISTS `customer_service_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('legacy','pending','postgresql')),
  `epoch` integer NOT NULL DEFAULT 1 CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `customer_service_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'legacy',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `customer_service_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `customer_service_authority_no_recreate`
BEFORE INSERT ON `customer_service_write_authority`
WHEN EXISTS (SELECT 1 FROM `customer_service_write_authority` WHERE `id`=1)
BEGIN SELECT RAISE(ABORT,'customer_service_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_authority_no_delete`
BEFORE DELETE ON `customer_service_write_authority`
BEGIN SELECT RAISE(ABORT,'customer_service_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_authority_transition_guard`
BEFORE UPDATE ON `customer_service_write_authority`
WHEN NEW.id<>1 OR OLD.id<>1 OR NEW.epoch<>OLD.epoch+1 OR NEW.cutover_id=''
  OR NOT (
    (OLD.owner='legacy' AND NEW.owner='pending')
    OR (OLD.owner='pending' AND NEW.owner='legacy' AND NEW.cutover_id=OLD.cutover_id)
    OR (OLD.owner='pending' AND NEW.owner='postgresql' AND NEW.cutover_id=OLD.cutover_id)
  )
BEGIN SELECT RAISE(ABORT,'customer_service_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `customer_service_batches_insert_guard` BEFORE INSERT ON `customer_service_import_batches`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_batches_update_guard` BEFORE UPDATE ON `customer_service_import_batches`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_batches_delete_guard` BEFORE DELETE ON `customer_service_import_batches`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_conversations_insert_guard` BEFORE INSERT ON `customer_service_conversations`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_conversations_update_guard` BEFORE UPDATE ON `customer_service_conversations`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_conversations_delete_guard` BEFORE DELETE ON `customer_service_conversations`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_versions_insert_guard` BEFORE INSERT ON `customer_service_conversation_versions`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_versions_update_guard` BEFORE UPDATE ON `customer_service_conversation_versions`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_versions_delete_guard` BEFORE DELETE ON `customer_service_conversation_versions`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_audits_insert_guard` BEFORE INSERT ON `customer_service_deletion_audits`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_audits_update_guard` BEFORE UPDATE ON `customer_service_deletion_audits`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_audits_delete_guard` BEFORE DELETE ON `customer_service_deletion_audits`
WHEN COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `customer_service_fingerprints_insert_guard` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain`='customer-service' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_fingerprints_update_guard` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.`domain`='customer-service' OR NEW.`domain`='customer-service') AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_fingerprints_delete_guard` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain`='customer-service' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_attempts_insert_guard` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain`='customer-service' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_attempts_update_guard` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.`domain`='customer-service' OR NEW.`domain`='customer-service') AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_attempts_delete_guard` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain`='customer-service' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_heads_insert_guard` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain`='customer-service' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_heads_update_guard` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.`domain`='customer-service' OR NEW.`domain`='customer-service') AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_heads_delete_guard` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain`='customer-service' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint

-- Legacy customer-service paired uploads reused the shared inventory uploader.
-- Fence that exact fingerprint namespace so stale Workers cannot recreate R2
-- objects after prepare/activate while unrelated shared-upload rows remain valid.
CREATE TRIGGER IF NOT EXISTS `customer_service_uploads_insert_guard` BEFORE INSERT ON `inventory_import_uploads`
WHEN NEW.`fingerprint` LIKE 'customer-service:%' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_uploads_update_guard` BEFORE UPDATE ON `inventory_import_uploads`
WHEN (OLD.`fingerprint` LIKE 'customer-service:%' OR NEW.`fingerprint` LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_uploads_delete_guard` BEFORE DELETE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'customer-service:%' AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_upload_chunks_insert_guard` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_upload_chunks_update_guard` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_upload_chunks_delete_guard` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_upload_results_insert_guard` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_upload_results_update_guard` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_upload_results_delete_guard` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'customer-service:%')
  AND COALESCE((SELECT owner FROM customer_service_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'customer_service_authority_not_legacy'); END;
