-- Operator-only. This migration is intentionally excluded from the normal Drizzle journal.
-- It installs a behavior-neutral authority row plus database-level guards for legacy launch records.

CREATE TABLE IF NOT EXISTS `workflow_launch_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('legacy','pending','postgresql')),
  `epoch` integer NOT NULL DEFAULT 1 CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `workflow_launch_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'legacy',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `workflow_launch_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `workflow_launch_authority_no_recreate`
BEFORE INSERT ON `workflow_launch_write_authority`
WHEN EXISTS (SELECT 1 FROM `workflow_launch_write_authority` WHERE `id`=1)
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_launch_authority_no_delete`
BEFORE DELETE ON `workflow_launch_write_authority`
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_launch_authority_transition_guard`
BEFORE UPDATE ON `workflow_launch_write_authority`
WHEN NEW.id<>1 OR OLD.id<>1 OR NEW.epoch<>OLD.epoch+1 OR NEW.cutover_id=''
  OR NOT (
    (OLD.owner='legacy' AND NEW.owner='pending')
    OR (OLD.owner='pending' AND NEW.owner='legacy' AND NEW.cutover_id=OLD.cutover_id)
    OR (OLD.owner='pending' AND NEW.owner='postgresql' AND NEW.cutover_id=OLD.cutover_id)
  )
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `workflow_launch_records_insert_guard`
BEFORE INSERT ON `workflow_operation_records`
WHEN NEW.record_type='launch'
  AND COALESCE((SELECT owner FROM workflow_launch_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_launch_records_update_guard`
BEFORE UPDATE ON `workflow_operation_records`
WHEN (OLD.record_type='launch' OR NEW.record_type='launch')
  AND COALESCE((SELECT owner FROM workflow_launch_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_launch_records_delete_guard`
BEFORE DELETE ON `workflow_operation_records`
WHEN OLD.record_type='launch'
  AND COALESCE((SELECT owner FROM workflow_launch_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_not_legacy'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `workflow_launch_activities_insert_guard`
BEFORE INSERT ON `workflow_operation_activities`
WHEN EXISTS (SELECT 1 FROM workflow_operation_records WHERE id=NEW.record_id AND record_type='launch')
  AND COALESCE((SELECT owner FROM workflow_launch_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_launch_activities_update_guard`
BEFORE UPDATE ON `workflow_operation_activities`
WHEN EXISTS (
  SELECT 1 FROM workflow_operation_records
  WHERE id IN (OLD.record_id,NEW.record_id) AND record_type='launch'
) AND COALESCE((SELECT owner FROM workflow_launch_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_launch_activities_delete_guard`
BEFORE DELETE ON `workflow_operation_activities`
WHEN EXISTS (SELECT 1 FROM workflow_operation_records WHERE id=OLD.record_id AND record_type='launch')
  AND COALESCE((SELECT owner FROM workflow_launch_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_launch_authority_not_legacy'); END;
