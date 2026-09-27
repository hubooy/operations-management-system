CREATE TABLE IF NOT EXISTS `sales_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('d1', 'pending', 'postgresql')),
  `epoch` integer NOT NULL CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `sales_write_authority`
  (`id`, `owner`, `epoch`, `cutover_id`, `updated_at`)
  SELECT 1, 'd1', 1, '', CURRENT_TIMESTAMP
  WHERE NOT EXISTS (SELECT 1 FROM `sales_write_authority` WHERE `id` = 1);--> statement-breakpoint

-- Keep the authority row fail-closed even when a caller bypasses the operator
-- tool.  Entering `pending` is the point of no return: only the same cutover
-- may advance it to the terminal `postgresql` state, which must be recorded on
-- D1 before the PostgreSQL authority is activated.
CREATE TRIGGER IF NOT EXISTS `sales_authority_singleton_insert_guard`
BEFORE INSERT ON `sales_write_authority`
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_singleton_delete_guard`
BEFORE DELETE ON `sales_write_authority`
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_transition_guard`
BEFORE UPDATE ON `sales_write_authority`
WHEN NOT (
  NEW.`id` = OLD.`id`
  AND NEW.`epoch` = OLD.`epoch` + 1
  AND length(NEW.`cutover_id`) BETWEEN 8 AND 128
  AND (
    (OLD.`owner` = 'd1' AND NEW.`owner` = 'pending')
    OR (
      OLD.`owner` = 'pending'
      AND NEW.`owner` = 'postgresql'
      AND NEW.`cutover_id` = OLD.`cutover_id`
    )
  )
)
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_order_lines_insert`
BEFORE INSERT ON `sales_order_lines`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_order_lines_update`
BEFORE UPDATE ON `sales_order_lines`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_order_lines_delete`
BEFORE DELETE ON `sales_order_lines`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_batches_insert`
BEFORE INSERT ON `sales_import_batches`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_batches_update`
BEFORE UPDATE ON `sales_import_batches`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_batches_delete`
BEFORE DELETE ON `sales_import_batches`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_uploads_insert`
BEFORE INSERT ON `sales_import_uploads`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_uploads_update`
BEFORE UPDATE ON `sales_import_uploads`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_uploads_delete`
BEFORE DELETE ON `sales_import_uploads`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_upload_chunks_insert`
BEFORE INSERT ON `sales_import_upload_chunks`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_upload_chunks_update`
BEFORE UPDATE ON `sales_import_upload_chunks`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_upload_chunks_delete`
BEFORE DELETE ON `sales_import_upload_chunks`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_cache_insert`
BEFORE INSERT ON `sales_overview_response_cache`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_cache_update`
BEFORE UPDATE ON `sales_overview_response_cache`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_cache_delete`
BEFORE DELETE ON `sales_overview_response_cache`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_revision_update`
BEFORE UPDATE OF `sales_revision` ON `sales_overview_cache_state`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_revision_insert`
BEFORE INSERT ON `sales_overview_cache_state`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
 AND NOT EXISTS (SELECT 1 FROM `sales_overview_cache_state` WHERE `id` = 1)
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_revision_delete`
BEFORE DELETE ON `sales_overview_cache_state`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

-- This singleton is shared by sales and ERP outbox events.  ERP only reads it
-- and may continue issuing its harmless INSERT OR IGNORE during schema setup;
-- changing or recreating the epoch after the sales fence closes is forbidden.
CREATE TRIGGER IF NOT EXISTS `sales_authority_source_state_insert`
BEFORE INSERT ON `sales_projection_source_state`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
 AND NOT EXISTS (SELECT 1 FROM `sales_projection_source_state` WHERE `id` = 1)
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_source_state_update`
BEFORE UPDATE ON `sales_projection_source_state`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_source_state_delete`
BEFORE DELETE ON `sales_projection_source_state`
WHEN COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_outbox_insert`
BEFORE INSERT ON `sales_projection_outbox`
WHEN NEW.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_outbox_update`
BEFORE UPDATE ON `sales_projection_outbox`
WHEN (OLD.`domain` = 'sales' OR NEW.`domain` = 'sales')
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_outbox_delete`
BEFORE DELETE ON `sales_projection_outbox`
WHEN OLD.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_fingerprints_insert`
BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_fingerprints_update`
BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.`domain` = 'sales' OR NEW.`domain` = 'sales')
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_fingerprints_delete`
BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_attempts_insert`
BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_attempts_update`
BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.`domain` = 'sales' OR NEW.`domain` = 'sales')
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_attempts_delete`
BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `sales_authority_scope_heads_insert`
BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_scope_heads_update`
BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.`domain` = 'sales' OR NEW.`domain` = 'sales')
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `sales_authority_scope_heads_delete`
BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain` = 'sales'
 AND COALESCE((SELECT `owner` FROM `sales_write_authority` WHERE `id` = 1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'sales_write_authority_not_d1'); END;
