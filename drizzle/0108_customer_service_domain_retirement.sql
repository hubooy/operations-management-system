-- Operator-only terminal retirement for the D1/R2 customer-service domain.
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
  (SELECT COUNT(*) FROM `customer_service_write_authority`
    WHERE `id`=1 AND `owner`='postgresql' AND length(`cutover_id`) BETWEEN 8 AND 128)=1
  AND (SELECT COUNT(*) FROM `domain_retirement_receipts`
    WHERE `domain`='customer-service'
      AND `version`='customer-service-domain-retirement-receipt-v1'
      AND `status`='approved'
      AND `cutover_id`=(SELECT `cutover_id` FROM `customer_service_write_authority` WHERE `id`=1)
      AND length(`plan_id`)=64 AND `plan_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`attestation_sha256`)=64 AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`smoke_receipt_sha256`)=64 AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`preflight_evidence_sha256`)=64 AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`migration_sha256`)=64 AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
      AND length(`audit_id`)=64 AND `audit_id` NOT GLOB '*[^0-9a-f]*'
      AND length(`preserved_evidence_sha256`)=64 AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
      AND `completed_at` IS NULL)=1
  AND (SELECT COUNT(*) FROM `customer_service_import_batches` WHERE `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_fingerprints`
    WHERE `domain`='customer-service' AND `status`='processing')=0
  AND (SELECT COUNT(*) FROM `import_content_attempts`
    WHERE `domain`='customer-service' AND `outcome`='processing')=0
  AND (SELECT COUNT(*) FROM `import_scope_heads`
    WHERE `domain`='customer-service'
      AND (`status`<>'ready' OR COALESCE(`owner_token`,'')<>''))=0
  AND (SELECT COUNT(*) FROM `inventory_import_upload_chunks` c
    JOIN `inventory_import_uploads` u ON u.`id`=c.`upload_id`
    WHERE u.`fingerprint` LIKE 'customer-service:%')=0
  AND (SELECT COUNT(*) FROM `inventory_import_uploads`
    WHERE `fingerprint` LIKE 'customer-service:%' AND `status`<>'completed')=0
) THEN 1 ELSE abs(-9223372036854775808) END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `customer_service_authority_no_recreate`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_authority_no_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_batches_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_batches_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_batches_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_conversations_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_conversations_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_conversations_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_versions_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_versions_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_versions_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_audits_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_audits_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_audits_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_fingerprints_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_fingerprints_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_fingerprints_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_attempts_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_attempts_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_attempts_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_heads_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_heads_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_heads_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_uploads_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_uploads_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_uploads_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_upload_chunks_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_upload_chunks_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_upload_chunks_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_upload_results_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_upload_results_update_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `customer_service_upload_results_delete_guard`;--> statement-breakpoint

DELETE FROM `inventory_import_upload_results`
WHERE `upload_id` IN (
  SELECT `id` FROM `inventory_import_uploads` WHERE `fingerprint` LIKE 'customer-service:%'
);--> statement-breakpoint
DELETE FROM `inventory_import_upload_chunks`
WHERE `upload_id` IN (
  SELECT `id` FROM `inventory_import_uploads` WHERE `fingerprint` LIKE 'customer-service:%'
);--> statement-breakpoint
DELETE FROM `inventory_import_uploads` WHERE `fingerprint` LIKE 'customer-service:%';--> statement-breakpoint
DELETE FROM `import_content_fingerprints` WHERE `domain`='customer-service';--> statement-breakpoint
DELETE FROM `import_content_attempts` WHERE `domain`='customer-service';--> statement-breakpoint
DELETE FROM `import_scope_heads` WHERE `domain`='customer-service';--> statement-breakpoint

DELETE FROM `customer_service_conversation_versions`;--> statement-breakpoint
DELETE FROM `customer_service_deletion_audits`;--> statement-breakpoint
DELETE FROM `customer_service_conversations`;--> statement-breakpoint
DELETE FROM `customer_service_import_batches`;--> statement-breakpoint
DROP TABLE `customer_service_conversation_versions`;--> statement-breakpoint
DROP TABLE `customer_service_deletion_audits`;--> statement-breakpoint
DROP TABLE `customer_service_conversations`;--> statement-breakpoint
DROP TABLE `customer_service_import_batches`;--> statement-breakpoint
DROP TABLE `customer_service_write_authority`;--> statement-breakpoint

CREATE VIEW `customer_service_import_batches` AS SELECT
  /* customer-service-domain-retired-v1 */
  CAST(NULL AS TEXT) AS `id`, CAST(NULL AS TEXT) AS `shop_name`,
  CAST(NULL AS TEXT) AS `session_file_name`, CAST(NULL AS TEXT) AS `chat_file_name`,
  CAST(NULL AS TEXT) AS `file_hash`, CAST(NULL AS TEXT) AS `status`,
  CAST(NULL AS INTEGER) AS `conversation_count`, CAST(NULL AS INTEGER) AS `matched_count`,
  CAST(NULL AS INTEGER) AS `session_only_count`, CAST(NULL AS INTEGER) AS `chat_only_count`,
  CAST(NULL AS INTEGER) AS `ambiguous_count`, CAST(NULL AS TEXT) AS `warnings_json`,
  CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `completed_at`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `customer_service_conversations` AS SELECT
  /* customer-service-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS TEXT) AS `conversation_key`,
  CAST(NULL AS TEXT) AS `first_import_batch_id`, CAST(NULL AS TEXT) AS `last_import_batch_id`,
  CAST(NULL AS TEXT) AS `shop_name`, CAST(NULL AS TEXT) AS `consulted_at`,
  CAST(NULL AS TEXT) AS `customer_id`, CAST(NULL AS TEXT) AS `customer_alias`,
  CAST(NULL AS TEXT) AS `consultation_type`, CAST(NULL AS TEXT) AS `agent`,
  CAST(NULL AS TEXT) AS `transferred_agent`, CAST(NULL AS TEXT) AS `skill_group`,
  CAST(NULL AS TEXT) AS `product_sku`, CAST(NULL AS TEXT) AS `product_name`,
  CAST(NULL AS TEXT) AS `first_response_at`, CAST(NULL AS REAL) AS `response_seconds`,
  CAST(NULL AS REAL) AS `duration_minutes`, CAST(NULL AS INTEGER) AS `customer_message_count`,
  CAST(NULL AS INTEGER) AS `agent_message_count`, CAST(NULL AS TEXT) AS `satisfaction`,
  CAST(NULL AS TEXT) AS `resolved`, CAST(NULL AS TEXT) AS `conversation_id`,
  CAST(NULL AS TEXT) AS `match_status`, CAST(NULL AS TEXT) AS `match_confidence`,
  CAST(NULL AS TEXT) AS `chat_started_at`, CAST(NULL AS TEXT) AS `chat_ended_at`,
  CAST(NULL AS TEXT) AS `chat_customer_alias`, CAST(NULL AS TEXT) AS `messages_json`,
  CAST(NULL AS TEXT) AS `robot_scope`, CAST(NULL AS TEXT) AS `problem_type`,
  CAST(NULL AS TEXT) AS `conversion_status`, CAST(NULL AS TEXT) AS `service_issues`,
  CAST(NULL AS TEXT) AS `summary_text`, CAST(NULL AS TEXT) AS `analysis_source`,
  CAST(NULL AS TEXT) AS `analyzed_at`, CAST(NULL AS TEXT) AS `annotated_at`,
  CAST(NULL AS TEXT) AS `created_at`, CAST(NULL AS TEXT) AS `updated_at`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `customer_service_conversation_versions` AS SELECT
  /* customer-service-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `conversation_id`, CAST(NULL AS INTEGER) AS `version`,
  CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `customer_service_deletion_audits` AS SELECT
  /* customer-service-domain-retired-v1 */
  CAST(NULL AS TEXT) AS `audit_id`, CAST(NULL AS INTEGER) AS `conversation_id`,
  CAST(NULL AS TEXT) AS `conversation_key`, CAST(NULL AS TEXT) AS `actor`,
  CAST(NULL AS INTEGER) AS `old_version`, CAST(NULL AS INTEGER) AS `expected_version`,
  CAST(NULL AS TEXT) AS `reason`, CAST(NULL AS TEXT) AS `deleted_at` WHERE 0;--> statement-breakpoint
CREATE VIEW `customer_service_write_authority` AS SELECT
  /* customer-service-domain-retired-v1 */
  CAST(NULL AS INTEGER) AS `id`, CAST(NULL AS TEXT) AS `owner`,
  CAST(NULL AS INTEGER) AS `epoch`, CAST(NULL AS TEXT) AS `cutover_id`,
  CAST(NULL AS TEXT) AS `updated_at` WHERE 0;--> statement-breakpoint

CREATE TRIGGER `customer_service_retired_fingerprints_insert_guard` BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_fingerprints_update_guard` BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain`='customer-service' OR NEW.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_fingerprints_delete_guard` BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_attempts_insert_guard` BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_attempts_update_guard` BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain`='customer-service' OR NEW.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_attempts_delete_guard` BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_heads_insert_guard` BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_heads_update_guard` BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain`='customer-service' OR NEW.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_heads_delete_guard` BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain`='customer-service' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_uploads_insert_guard` BEFORE INSERT ON `inventory_import_uploads`
WHEN NEW.`fingerprint` LIKE 'customer-service:%' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_uploads_update_guard` BEFORE UPDATE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'customer-service:%' OR NEW.`fingerprint` LIKE 'customer-service:%'
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_uploads_delete_guard` BEFORE DELETE ON `inventory_import_uploads`
WHEN OLD.`fingerprint` LIKE 'customer-service:%' BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_upload_chunks_insert_guard` BEFORE INSERT ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'customer-service:%')
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_upload_chunks_update_guard` BEFORE UPDATE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'customer-service:%')
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_upload_chunks_delete_guard` BEFORE DELETE ON `inventory_import_upload_chunks`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'customer-service:%')
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_upload_results_insert_guard` BEFORE INSERT ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=NEW.upload_id AND fingerprint LIKE 'customer-service:%')
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_upload_results_update_guard` BEFORE UPDATE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id IN (OLD.upload_id,NEW.upload_id) AND fingerprint LIKE 'customer-service:%')
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `customer_service_retired_upload_results_delete_guard` BEFORE DELETE ON `inventory_import_upload_results`
WHEN EXISTS (SELECT 1 FROM inventory_import_uploads WHERE id=OLD.upload_id AND fingerprint LIKE 'customer-service:%')
BEGIN SELECT RAISE(ABORT,'customer_service_domain_retired'); END;--> statement-breakpoint

UPDATE `domain_retirement_receipts`
SET `status`='completed', `completed_at`=CURRENT_TIMESTAMP
WHERE `domain`='customer-service' AND `status`='approved';
