-- Operator-applied preparation for the finance single-write cutover. Applying
-- this file is behavior-neutral while owner=d1; the controlled cutover tool is
-- the only component allowed to advance the singleton.
CREATE TABLE IF NOT EXISTS `finance_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('d1', 'pending', 'postgresql')),
  `epoch` integer NOT NULL CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `finance_write_authority` (`id`, `owner`, `epoch`, `cutover_id`, `updated_at`)
SELECT 1, 'd1', 1, '', CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `finance_write_authority` WHERE `id` = 1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_singleton_insert_guard`
BEFORE INSERT ON `finance_write_authority`
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_singleton_delete_guard`
BEFORE DELETE ON `finance_write_authority`
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_transition_guard`
BEFORE UPDATE ON `finance_write_authority`
WHEN NOT (
  NEW.`id` = OLD.`id`
  AND NEW.`epoch` = OLD.`epoch` + 1
  AND length(NEW.`cutover_id`) BETWEEN 8 AND 128
  AND (
    (OLD.`owner` = 'd1' AND NEW.`owner` = 'pending')
    OR (OLD.`owner` = 'pending' AND NEW.`owner` = 'd1' AND NEW.`cutover_id` = OLD.`cutover_id`)
    OR (OLD.`owner` = 'pending' AND NEW.`owner` = 'postgresql' AND NEW.`cutover_id` = OLD.`cutover_id`)
  )
)
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_batches_insert` BEFORE INSERT ON `finance_import_batches`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_batches_update` BEFORE UPDATE ON `finance_import_batches`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_batches_delete` BEFORE DELETE ON `finance_import_batches`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_months_insert` BEFORE INSERT ON `finance_months`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_months_update` BEFORE UPDATE ON `finance_months`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_months_delete` BEFORE DELETE ON `finance_months`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_lines_insert` BEFORE INSERT ON `finance_lines`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_lines_update` BEFORE UPDATE ON `finance_lines`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_lines_delete` BEFORE DELETE ON `finance_lines`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_targets_insert` BEFORE INSERT ON `finance_targets`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_targets_update` BEFORE UPDATE ON `finance_targets`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_targets_delete` BEFORE DELETE ON `finance_targets`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_target_versions_insert` BEFORE INSERT ON `finance_target_versions`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_target_versions_update` BEFORE UPDATE ON `finance_target_versions`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_target_versions_delete` BEFORE DELETE ON `finance_target_versions`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_target_audits_insert` BEFORE INSERT ON `finance_target_deletion_audits`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_target_audits_update` BEFORE UPDATE ON `finance_target_deletion_audits`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_target_audits_delete` BEFORE DELETE ON `finance_target_deletion_audits`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_targets_insert` BEFORE INSERT ON `finance_targets_scoped`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_targets_update` BEFORE UPDATE ON `finance_targets_scoped`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_targets_delete` BEFORE DELETE ON `finance_targets_scoped`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_versions_insert` BEFORE INSERT ON `finance_target_scoped_versions`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_versions_update` BEFORE UPDATE ON `finance_target_scoped_versions`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_versions_delete` BEFORE DELETE ON `finance_target_scoped_versions`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_audits_insert` BEFORE INSERT ON `finance_target_scoped_deletion_audits`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_audits_update` BEFORE UPDATE ON `finance_target_scoped_deletion_audits`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_scoped_audits_delete` BEFORE DELETE ON `finance_target_scoped_deletion_audits`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_legacy_migrations_insert` BEFORE INSERT ON `finance_target_legacy_migrations`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_legacy_migrations_update` BEFORE UPDATE ON `finance_target_legacy_migrations`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_legacy_migrations_delete` BEFORE DELETE ON `finance_target_legacy_migrations`
WHEN COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_fingerprints_insert` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.domain='finance' AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_fingerprints_update` BEFORE UPDATE ON `import_content_fingerprints`
WHEN (OLD.domain='finance' OR NEW.domain='finance') AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_fingerprints_delete` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.domain='finance' AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_attempts_insert` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.domain='finance' AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_attempts_update` BEFORE UPDATE ON `import_content_attempts`
WHEN (OLD.domain='finance' OR NEW.domain='finance') AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_attempts_delete` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.domain='finance' AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `finance_authority_heads_insert` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.domain='finance' AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_heads_update` BEFORE UPDATE ON `import_scope_heads`
WHEN (OLD.domain='finance' OR NEW.domain='finance') AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_authority_heads_delete` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.domain='finance' AND COALESCE((SELECT owner FROM finance_write_authority WHERE id=1), '') <> 'd1'
BEGIN SELECT RAISE(ABORT, 'finance_write_authority_not_d1'); END;
