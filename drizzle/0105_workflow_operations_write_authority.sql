-- Operator-only. This migration is intentionally excluded from the normal Drizzle journal.
-- It freezes every remaining D1 workflow write path before PostgreSQL activation.

CREATE TABLE IF NOT EXISTS `workflow_operations_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `owner` text NOT NULL CHECK (`owner` IN ('legacy','pending','postgresql')),
  `epoch` integer NOT NULL DEFAULT 1 CHECK (`epoch` >= 1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `workflow_operations_write_authority` (`id`,`owner`,`epoch`,`cutover_id`,`updated_at`)
SELECT 1,'legacy',1,'',CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM `workflow_operations_write_authority` WHERE `id`=1);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `workflow_operations_authority_no_recreate`
BEFORE INSERT ON `workflow_operations_write_authority`
WHEN EXISTS (SELECT 1 FROM `workflow_operations_write_authority` WHERE `id`=1)
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_authority_no_delete`
BEFORE DELETE ON `workflow_operations_write_authority`
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_authority_transition_guard`
BEFORE UPDATE ON `workflow_operations_write_authority`
WHEN NEW.id<>1 OR OLD.id<>1 OR NEW.epoch<>OLD.epoch+1 OR NEW.cutover_id=''
  OR NOT (
    (OLD.owner='legacy' AND NEW.owner='pending')
    OR (OLD.owner='pending' AND NEW.owner='legacy' AND NEW.cutover_id=OLD.cutover_id)
    OR (OLD.owner='pending' AND NEW.owner='postgresql' AND NEW.cutover_id=OLD.cutover_id)
  )
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_invalid_transition'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `workflow_operations_tasks_insert_guard`
BEFORE INSERT ON `workflow_tasks`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_tasks_update_guard`
BEFORE UPDATE ON `workflow_tasks`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_tasks_delete_guard`
BEFORE DELETE ON `workflow_tasks`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_bootstrap_insert_guard`
BEFORE INSERT ON `workflow_task_bootstrap`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_bootstrap_update_guard`
BEFORE UPDATE ON `workflow_task_bootstrap`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_bootstrap_delete_guard`
BEFORE DELETE ON `workflow_task_bootstrap`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_task_states_insert_guard`
BEFORE INSERT ON `workflow_task_states`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_task_states_update_guard`
BEFORE UPDATE ON `workflow_task_states`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_task_states_delete_guard`
BEFORE DELETE ON `workflow_task_states`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_comments_insert_guard`
BEFORE INSERT ON `workflow_task_comments`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_comments_update_guard`
BEFORE UPDATE ON `workflow_task_comments`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_comments_delete_guard`
BEFORE DELETE ON `workflow_task_comments`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_activity_logs_insert_guard`
BEFORE INSERT ON `workflow_task_activity_logs`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_activity_logs_update_guard`
BEFORE UPDATE ON `workflow_task_activity_logs`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_activity_logs_delete_guard`
BEFORE DELETE ON `workflow_task_activity_logs`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_reminders_insert_guard`
BEFORE INSERT ON `workflow_task_reminders`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_reminders_update_guard`
BEFORE UPDATE ON `workflow_task_reminders`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_reminders_delete_guard`
BEFORE DELETE ON `workflow_task_reminders`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_templates_insert_guard`
BEFORE INSERT ON `workflow_task_templates`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_templates_update_guard`
BEFORE UPDATE ON `workflow_task_templates`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_templates_delete_guard`
BEFORE DELETE ON `workflow_task_templates`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_template_states_insert_guard`
BEFORE INSERT ON `workflow_task_template_states`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_template_states_update_guard`
BEFORE UPDATE ON `workflow_task_template_states`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_template_states_delete_guard`
BEFORE DELETE ON `workflow_task_template_states`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_entity_links_insert_guard`
BEFORE INSERT ON `workflow_task_entity_links`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_entity_links_update_guard`
BEFORE UPDATE ON `workflow_task_entity_links`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_entity_links_delete_guard`
BEFORE DELETE ON `workflow_task_entity_links`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_attachments_insert_guard`
BEFORE INSERT ON `workflow_task_attachments`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_attachments_update_guard`
BEFORE UPDATE ON `workflow_task_attachments`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_attachments_delete_guard`
BEFORE DELETE ON `workflow_task_attachments`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_cleanup_queue_insert_guard`
BEFORE INSERT ON `workflow_attachment_cleanup_queue`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_cleanup_queue_update_guard`
BEFORE UPDATE ON `workflow_attachment_cleanup_queue`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_cleanup_queue_delete_guard`
BEFORE DELETE ON `workflow_attachment_cleanup_queue`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_records_insert_guard`
BEFORE INSERT ON `workflow_operation_records`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_records_update_guard`
BEFORE UPDATE ON `workflow_operation_records`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_records_delete_guard`
BEFORE DELETE ON `workflow_operation_records`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_record_activities_insert_guard`
BEFORE INSERT ON `workflow_operation_activities`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_record_activities_update_guard`
BEFORE UPDATE ON `workflow_operation_activities`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `workflow_operations_record_activities_delete_guard`
BEFORE DELETE ON `workflow_operation_activities`
WHEN COALESCE((SELECT owner FROM workflow_operations_write_authority WHERE id=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'workflow_operations_authority_not_legacy'); END;
