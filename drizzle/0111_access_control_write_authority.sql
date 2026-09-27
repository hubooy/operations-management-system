-- Operator-only. Intentionally excluded from the Drizzle journal.
-- Freezes the D1 app_users authority before PostgreSQL activation.
CREATE TABLE IF NOT EXISTS `access_control_write_authority` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id`=1),
  `owner` text NOT NULL CHECK (`owner` IN ('legacy','pending','postgresql')),
  `epoch` integer NOT NULL DEFAULT 1 CHECK (`epoch`>=1),
  `cutover_id` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT INTO `access_control_write_authority` (`id`,`owner`,`epoch`,`cutover_id`)
SELECT 1,'legacy',1,'' WHERE NOT EXISTS (
  SELECT 1 FROM `access_control_write_authority` WHERE `id`=1
);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `access_control_authority_no_recreate`
BEFORE INSERT ON `access_control_write_authority`
WHEN EXISTS (SELECT 1 FROM `access_control_write_authority` WHERE `id`=1)
BEGIN SELECT RAISE(ABORT,'access_control_authority_recreate_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `access_control_authority_no_delete`
BEFORE DELETE ON `access_control_write_authority`
BEGIN SELECT RAISE(ABORT,'access_control_authority_delete_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `access_control_authority_transition_guard`
BEFORE UPDATE ON `access_control_write_authority`
WHEN NEW.`id`<>1 OR OLD.`id`<>1 OR NEW.`epoch`<>OLD.`epoch`+1 OR NEW.`cutover_id`=''
  OR NOT ((OLD.`owner`='legacy' AND NEW.`owner`='pending')
    OR (OLD.`owner`='pending' AND NEW.`owner`='legacy' AND NEW.`cutover_id`=OLD.`cutover_id`)
    OR (OLD.`owner`='pending' AND NEW.`owner`='postgresql' AND NEW.`cutover_id`=OLD.`cutover_id`))
BEGIN SELECT RAISE(ABORT,'access_control_authority_invalid_transition'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `access_control_users_insert_guard` BEFORE INSERT ON `app_users`
WHEN COALESCE((SELECT `owner` FROM `access_control_write_authority` WHERE `id`=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'access_control_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `access_control_users_update_guard` BEFORE UPDATE ON `app_users`
WHEN COALESCE((SELECT `owner` FROM `access_control_write_authority` WHERE `id`=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'access_control_authority_not_legacy'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `access_control_users_delete_guard` BEFORE DELETE ON `app_users`
WHEN COALESCE((SELECT `owner` FROM `access_control_write_authority` WHERE `id`=1),'')<>'legacy'
BEGIN SELECT RAISE(ABORT,'access_control_authority_not_legacy'); END;
