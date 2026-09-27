-- Operator-only terminal retirement for the D1 workflow launch subdomain.
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
  (SELECT COUNT(*) FROM `workflow_launch_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='workflow-launch'
      AND `version`='workflow-launch-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `workflow_launch_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `workflow_launch_authority_no_recreate`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_authority_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_records_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_records_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_records_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_activities_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_activities_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `workflow_launch_activities_delete_guard`;--> statement-breakpoint

DELETE FROM `workflow_operation_activities`
WHERE `record_id` IN (
  SELECT `id` FROM `workflow_operation_records` WHERE `record_type`='launch'
);--> statement-breakpoint
DELETE FROM `workflow_operation_records` WHERE `record_type`='launch';--> statement-breakpoint

DROP TABLE `workflow_launch_write_authority`;--> statement-breakpoint
CREATE VIEW `workflow_launch_write_authority` AS
SELECT
  'workflow-launch-domain-retired-v1' AS `retirement_tombstone`,
  CAST(NULL AS integer) AS `id`,
  CAST(NULL AS text) AS `owner`,
  CAST(NULL AS integer) AS `epoch`,
  CAST(NULL AS text) AS `cutover_id`,
  CAST(NULL AS text) AS `updated_at`
WHERE 0;--> statement-breakpoint

CREATE TRIGGER `workflow_launch_retired_records_insert_guard`
BEFORE INSERT ON `workflow_operation_records`
WHEN NEW.`record_type`='launch'
BEGIN SELECT RAISE(ABORT,'workflow_launch_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `workflow_launch_retired_records_update_guard`
BEFORE UPDATE ON `workflow_operation_records`
WHEN OLD.`record_type`='launch' OR NEW.`record_type`='launch'
BEGIN SELECT RAISE(ABORT,'workflow_launch_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `workflow_launch_retired_records_delete_guard`
BEFORE DELETE ON `workflow_operation_records`
WHEN OLD.`record_type`='launch'
BEGIN SELECT RAISE(ABORT,'workflow_launch_domain_retired'); END;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed',`completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='workflow-launch' AND `status`='approved';
