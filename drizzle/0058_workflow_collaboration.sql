CREATE TABLE IF NOT EXISTS `workflow_task_states` (
	`task_id` text PRIMARY KEY NOT NULL,
	`version` integer NOT NULL DEFAULT 1 CHECK (`version` >= 1),
	`mutation_token` text NOT NULL DEFAULT '',
	`deleted_at` text,
	`deleted_by` text,
	FOREIGN KEY (`task_id`) REFERENCES `workflow_tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_states_deleted_idx`
	ON `workflow_task_states` (`deleted_at`,`task_id`);
--> statement-breakpoint
INSERT OR IGNORE INTO `workflow_task_states` (`task_id`)
	SELECT `id` FROM `workflow_tasks`;
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_comments` (
	`id` text PRIMARY KEY NOT NULL,
	`task_id` text NOT NULL,
	`content` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY (`task_id`) REFERENCES `workflow_tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_comments_task_created_idx`
	ON `workflow_task_comments` (`task_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_activity_logs` (
	`id` text PRIMARY KEY NOT NULL,
	`task_id` text NOT NULL,
	`action` text NOT NULL,
	`summary` text NOT NULL,
	`metadata_json` text NOT NULL DEFAULT '{}',
	`actor_email` text NOT NULL,
	`created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY (`task_id`) REFERENCES `workflow_tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_activity_task_created_idx`
	ON `workflow_task_activity_logs` (`task_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_reminders` (
	`id` text PRIMARY KEY NOT NULL,
	`task_id` text NOT NULL,
	`remind_at` text NOT NULL,
	`note` text NOT NULL DEFAULT '',
	`status` text NOT NULL DEFAULT 'pending' CHECK (`status` IN ('pending','dismissed','sent')),
	`created_by` text NOT NULL,
	`created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY (`task_id`) REFERENCES `workflow_tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_reminders_task_status_time_idx`
	ON `workflow_task_reminders` (`task_id`,`status`,`remind_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_templates` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`description` text NOT NULL DEFAULT '',
	`title` text NOT NULL DEFAULT '',
	`work_content` text NOT NULL DEFAULT '',
	`category` text NOT NULL DEFAULT '工作计划',
	`owner` text NOT NULL DEFAULT '',
	`shop_name` text NOT NULL DEFAULT '',
	`start_offset_days` integer NOT NULL DEFAULT 0,
	`due_offset_days` integer NOT NULL DEFAULT 0,
	`priority` text NOT NULL DEFAULT 'normal' CHECK (`priority` IN ('high','normal','low')),
	`active` integer NOT NULL DEFAULT 1 CHECK (`active` IN (0,1)),
	`created_by` text NOT NULL,
	`updated_by` text NOT NULL,
	`created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_templates_active_updated_idx`
	ON `workflow_task_templates` (`active`,`updated_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_template_states` (
	`template_id` text PRIMARY KEY NOT NULL,
	`version` integer NOT NULL DEFAULT 1 CHECK (`version` >= 1),
	`mutation_token` text NOT NULL DEFAULT '',
	FOREIGN KEY (`template_id`) REFERENCES `workflow_task_templates`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
INSERT OR IGNORE INTO `workflow_task_template_states` (`template_id`)
	SELECT `id` FROM `workflow_task_templates`;
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_entity_links` (
	`id` text PRIMARY KEY NOT NULL,
	`task_id` text NOT NULL,
	`entity_type` text NOT NULL CHECK (`entity_type` IN ('shop','product','campaign','order','report','url')),
	`entity_id` text NOT NULL,
	`label` text NOT NULL,
	`url` text NOT NULL DEFAULT '',
	`created_by` text NOT NULL,
	`created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY (`task_id`) REFERENCES `workflow_tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `workflow_task_entity_links_task_entity_uq`
	ON `workflow_task_entity_links` (`task_id`,`entity_type`,`entity_id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_entity_links_task_created_idx`
	ON `workflow_task_entity_links` (`task_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_task_attachments` (
	`id` text PRIMARY KEY NOT NULL,
	`task_id` text NOT NULL,
	`file_name` text NOT NULL,
	`mime_type` text NOT NULL,
	`size_bytes` integer NOT NULL,
	`sha256` text NOT NULL,
	`object_key` text NOT NULL UNIQUE,
	`created_by` text NOT NULL,
	`created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY (`task_id`) REFERENCES `workflow_tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_task_attachments_task_created_idx`
	ON `workflow_task_attachments` (`task_id`,`created_at`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `workflow_attachment_cleanup_queue` (
	`object_key` text PRIMARY KEY NOT NULL,
	`attempts` integer NOT NULL DEFAULT 0,
	`last_error` text NOT NULL DEFAULT '',
	`enqueued_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `workflow_attachment_cleanup_queue_updated_idx`
	ON `workflow_attachment_cleanup_queue` (`updated_at`,`object_key`);
--> statement-breakpoint
PRAGMA optimize;
