-- Operator-only terminal retirement for the remaining D1 workflow domain.
-- The operator must install one exact approved receipt in the same
-- BEGIN IMMEDIATE transaction. Intentionally absent from the journal.
CREATE TABLE IF NOT EXISTS `domain_retirement_receipts` (
  `domain` text PRIMARY KEY NOT NULL,
  `version` text NOT NULL,
  `status` text NOT NULL CHECK (`status` IN ('approved','completed')),
  `cutover_id` text NOT NULL,
  `plan_id` text NOT NULL,
  `attestation_sha256` text NOT NULL,
  `smoke_receipt_sha256` text NOT NULL,
  `preflight_evidence_sha256` text NOT NULL,
  `migration_sha256` text NOT NULL,
  `audit_id` text NOT NULL,
  `preserved_evidence_sha256` text NOT NULL,
  `created_at` text NOT NULL,
  `completed_at` text,
  CHECK ((`status`='approved' AND `completed_at` IS NULL)
    OR (`status`='completed' AND `completed_at` IS NOT NULL))
);--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `domain_retirement_receipts_insert_guard`
BEFORE INSERT ON `domain_retirement_receipts`
WHEN NEW.`status`<>'approved' OR NEW.`completed_at` IS NOT NULL
  OR EXISTS (SELECT 1 FROM `domain_retirement_receipts` WHERE `domain`=NEW.`domain`)
BEGIN SELECT RAISE(ABORT,'domain_retirement_receipt_insert_forbidden'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `domain_retirement_receipts_transition_guard`
BEFORE UPDATE ON `domain_retirement_receipts`
WHEN NOT (
  OLD.`status`='approved' AND NEW.`status`='completed'
  AND OLD.`domain`=NEW.`domain` AND OLD.`version`=NEW.`version`
  AND OLD.`cutover_id`=NEW.`cutover_id` AND OLD.`plan_id`=NEW.`plan_id`
  AND OLD.`attestation_sha256`=NEW.`attestation_sha256`
  AND OLD.`smoke_receipt_sha256`=NEW.`smoke_receipt_sha256`
  AND OLD.`preflight_evidence_sha256`=NEW.`preflight_evidence_sha256`
  AND OLD.`migration_sha256`=NEW.`migration_sha256`
  AND OLD.`audit_id`=NEW.`audit_id`
  AND OLD.`preserved_evidence_sha256`=NEW.`preserved_evidence_sha256`
  AND OLD.`created_at`=NEW.`created_at`
  AND OLD.`completed_at` IS NULL AND NEW.`completed_at` IS NOT NULL
)
BEGIN SELECT RAISE(ABORT,'domain_retirement_receipt_update_forbidden'); END;--> statement-breakpoint

CREATE TRIGGER IF NOT EXISTS `domain_retirement_receipts_no_delete`
BEFORE DELETE ON `domain_retirement_receipts`
BEGIN SELECT RAISE(ABORT,'domain_retirement_receipt_delete_forbidden'); END;--> statement-breakpoint

SELECT CASE WHEN (
  (SELECT COUNT(*) FROM `workflow_operations_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='workflow-operations'
      AND `version`='workflow-operations-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `workflow_operations_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_authority_no_recreate`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_authority_no_delete`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_authority_transition_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_tasks_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_tasks_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_tasks_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_bootstrap_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_bootstrap_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_bootstrap_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_task_states_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_task_states_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_task_states_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_comments_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_comments_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_comments_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_activity_logs_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_activity_logs_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_activity_logs_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_reminders_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_reminders_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_reminders_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_templates_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_templates_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_templates_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_template_states_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_template_states_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_template_states_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_entity_links_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_entity_links_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_entity_links_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_attachments_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_attachments_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_attachments_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_cleanup_queue_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_cleanup_queue_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_cleanup_queue_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_records_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_records_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_records_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_record_activities_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_record_activities_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_operations_record_activities_delete_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_launch_retired_records_insert_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_launch_retired_records_update_guard`;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_launch_retired_records_delete_guard`;--> statement-breakpoint

DROP TABLE `workflow_operation_activities`;--> statement-breakpoint

DROP TABLE `workflow_operation_records`;--> statement-breakpoint

DROP TABLE `workflow_task_attachments`;--> statement-breakpoint

DROP TABLE `workflow_task_entity_links`;--> statement-breakpoint

DROP TABLE `workflow_task_reminders`;--> statement-breakpoint

DROP TABLE `workflow_task_comments`;--> statement-breakpoint

DROP TABLE `workflow_task_activity_logs`;--> statement-breakpoint

DROP TABLE `workflow_task_states`;--> statement-breakpoint

DROP TABLE `workflow_tasks`;--> statement-breakpoint

DROP TABLE `workflow_task_template_states`;--> statement-breakpoint

DROP TABLE `workflow_task_templates`;--> statement-breakpoint

DROP TABLE `workflow_attachment_cleanup_queue`;--> statement-breakpoint

DROP TABLE `workflow_task_bootstrap`;--> statement-breakpoint

DROP TABLE `workflow_operations_write_authority`;--> statement-breakpoint

CREATE VIEW `workflow_operation_activities` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `record_id`,
  CAST(NULL AS text) AS `action`,
  CAST(NULL AS text) AS `actor_email`,
  CAST(NULL AS text) AS `actor_role`,
  CAST(NULL AS integer) AS `from_version`,
  CAST(NULL AS integer) AS `to_version`,
  CAST(NULL AS text) AS `detail_json`,
  CAST(NULL AS text) AS `created_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_record_activities_insert_guard`
INSTEAD OF INSERT ON `workflow_operation_activities`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_record_activities_update_guard`
INSTEAD OF UPDATE ON `workflow_operation_activities`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_record_activities_delete_guard`
INSTEAD OF DELETE ON `workflow_operation_activities`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_operation_records` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `record_type`,
  CAST(NULL AS text) AS `title`,
  CAST(NULL AS text) AS `status`,
  CAST(NULL AS text) AS `priority`,
  CAST(NULL AS text) AS `platform`,
  CAST(NULL AS text) AS `channel`,
  CAST(NULL AS text) AS `shop_name`,
  CAST(NULL AS text) AS `owner`,
  CAST(NULL AS text) AS `occurred_at`,
  CAST(NULL AS text) AS `due_at`,
  CAST(NULL AS text) AS `content`,
  CAST(NULL AS text) AS `source`,
  CAST(NULL AS text) AS `source_ref`,
  CAST(NULL AS text) AS `reference_code`,
  CAST(NULL AS integer) AS `version`,
  CAST(NULL AS text) AS `mutation_token`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `updated_by`,
  CAST(NULL AS text) AS `created_at`,
  CAST(NULL AS text) AS `updated_at`,
  CAST(NULL AS text) AS `deleted_at`,
  CAST(NULL AS text) AS `deleted_by`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_records_insert_guard`
INSTEAD OF INSERT ON `workflow_operation_records`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_records_update_guard`
INSTEAD OF UPDATE ON `workflow_operation_records`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_records_delete_guard`
INSTEAD OF DELETE ON `workflow_operation_records`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_attachments` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `task_id`,
  CAST(NULL AS text) AS `file_name`,
  CAST(NULL AS text) AS `mime_type`,
  CAST(NULL AS integer) AS `size_bytes`,
  CAST(NULL AS text) AS `sha256`,
  CAST(NULL AS text) AS `object_key`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `created_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_attachments_insert_guard`
INSTEAD OF INSERT ON `workflow_task_attachments`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_attachments_update_guard`
INSTEAD OF UPDATE ON `workflow_task_attachments`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_attachments_delete_guard`
INSTEAD OF DELETE ON `workflow_task_attachments`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_entity_links` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `task_id`,
  CAST(NULL AS text) AS `entity_type`,
  CAST(NULL AS text) AS `entity_id`,
  CAST(NULL AS text) AS `label`,
  CAST(NULL AS text) AS `url`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `created_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_entity_links_insert_guard`
INSTEAD OF INSERT ON `workflow_task_entity_links`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_entity_links_update_guard`
INSTEAD OF UPDATE ON `workflow_task_entity_links`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_entity_links_delete_guard`
INSTEAD OF DELETE ON `workflow_task_entity_links`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_reminders` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `task_id`,
  CAST(NULL AS text) AS `remind_at`,
  CAST(NULL AS text) AS `note`,
  CAST(NULL AS text) AS `status`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `created_at`,
  CAST(NULL AS text) AS `updated_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_reminders_insert_guard`
INSTEAD OF INSERT ON `workflow_task_reminders`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_reminders_update_guard`
INSTEAD OF UPDATE ON `workflow_task_reminders`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_reminders_delete_guard`
INSTEAD OF DELETE ON `workflow_task_reminders`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_comments` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `task_id`,
  CAST(NULL AS text) AS `content`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `created_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_comments_insert_guard`
INSTEAD OF INSERT ON `workflow_task_comments`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_comments_update_guard`
INSTEAD OF UPDATE ON `workflow_task_comments`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_comments_delete_guard`
INSTEAD OF DELETE ON `workflow_task_comments`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_activity_logs` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `task_id`,
  CAST(NULL AS text) AS `action`,
  CAST(NULL AS text) AS `summary`,
  CAST(NULL AS text) AS `metadata_json`,
  CAST(NULL AS text) AS `actor_email`,
  CAST(NULL AS text) AS `created_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_activity_logs_insert_guard`
INSTEAD OF INSERT ON `workflow_task_activity_logs`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_activity_logs_update_guard`
INSTEAD OF UPDATE ON `workflow_task_activity_logs`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_activity_logs_delete_guard`
INSTEAD OF DELETE ON `workflow_task_activity_logs`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_states` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `task_id`,
  CAST(NULL AS integer) AS `version`,
  CAST(NULL AS text) AS `mutation_token`,
  CAST(NULL AS text) AS `deleted_at`,
  CAST(NULL AS text) AS `deleted_by`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_task_states_insert_guard`
INSTEAD OF INSERT ON `workflow_task_states`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_task_states_update_guard`
INSTEAD OF UPDATE ON `workflow_task_states`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_task_states_delete_guard`
INSTEAD OF DELETE ON `workflow_task_states`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_tasks` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `title`,
  CAST(NULL AS text) AS `work_content`,
  CAST(NULL AS text) AS `category`,
  CAST(NULL AS text) AS `owner`,
  CAST(NULL AS text) AS `shop_name`,
  CAST(NULL AS text) AS `start_date`,
  CAST(NULL AS text) AS `due_date`,
  CAST(NULL AS text) AS `status`,
  CAST(NULL AS text) AS `priority`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `updated_by`,
  CAST(NULL AS text) AS `created_at`,
  CAST(NULL AS text) AS `updated_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_tasks_insert_guard`
INSTEAD OF INSERT ON `workflow_tasks`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_tasks_update_guard`
INSTEAD OF UPDATE ON `workflow_tasks`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_tasks_delete_guard`
INSTEAD OF DELETE ON `workflow_tasks`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_template_states` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `template_id`,
  CAST(NULL AS integer) AS `version`,
  CAST(NULL AS text) AS `mutation_token`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_template_states_insert_guard`
INSTEAD OF INSERT ON `workflow_task_template_states`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_template_states_update_guard`
INSTEAD OF UPDATE ON `workflow_task_template_states`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_template_states_delete_guard`
INSTEAD OF DELETE ON `workflow_task_template_states`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_templates` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `id`,
  CAST(NULL AS text) AS `name`,
  CAST(NULL AS text) AS `description`,
  CAST(NULL AS text) AS `title`,
  CAST(NULL AS text) AS `work_content`,
  CAST(NULL AS text) AS `category`,
  CAST(NULL AS text) AS `owner`,
  CAST(NULL AS text) AS `shop_name`,
  CAST(NULL AS integer) AS `start_offset_days`,
  CAST(NULL AS integer) AS `due_offset_days`,
  CAST(NULL AS text) AS `priority`,
  CAST(NULL AS integer) AS `active`,
  CAST(NULL AS text) AS `created_by`,
  CAST(NULL AS text) AS `updated_by`,
  CAST(NULL AS text) AS `created_at`,
  CAST(NULL AS text) AS `updated_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_templates_insert_guard`
INSTEAD OF INSERT ON `workflow_task_templates`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_templates_update_guard`
INSTEAD OF UPDATE ON `workflow_task_templates`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_templates_delete_guard`
INSTEAD OF DELETE ON `workflow_task_templates`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_attachment_cleanup_queue` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `object_key`,
  CAST(NULL AS integer) AS `attempts`,
  CAST(NULL AS text) AS `last_error`,
  CAST(NULL AS text) AS `enqueued_at`,
  CAST(NULL AS text) AS `updated_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_cleanup_queue_insert_guard`
INSTEAD OF INSERT ON `workflow_attachment_cleanup_queue`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_cleanup_queue_update_guard`
INSTEAD OF UPDATE ON `workflow_attachment_cleanup_queue`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_cleanup_queue_delete_guard`
INSTEAD OF DELETE ON `workflow_attachment_cleanup_queue`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_task_bootstrap` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS text) AS `key`,
  CAST(NULL AS text) AS `seeded_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_bootstrap_insert_guard`
INSTEAD OF INSERT ON `workflow_task_bootstrap`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_bootstrap_update_guard`
INSTEAD OF UPDATE ON `workflow_task_bootstrap`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_bootstrap_delete_guard`
INSTEAD OF DELETE ON `workflow_task_bootstrap`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE VIEW `workflow_operations_write_authority` AS
SELECT
  'workflow-operations-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS integer) AS `id`,
  CAST(NULL AS text) AS `owner`,
  CAST(NULL AS integer) AS `epoch`,
  CAST(NULL AS text) AS `cutover_id`,
  CAST(NULL AS text) AS `updated_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_authority_insert_guard`
INSTEAD OF INSERT ON `workflow_operations_write_authority`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_authority_update_guard`
INSTEAD OF UPDATE ON `workflow_operations_write_authority`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

CREATE TRIGGER `workflow_operations_retired_authority_delete_guard`
INSTEAD OF DELETE ON `workflow_operations_write_authority`
BEGIN SELECT RAISE(ABORT,'workflow_operations_domain_retired'); END;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed',`completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='workflow-operations' AND `status`='approved';
