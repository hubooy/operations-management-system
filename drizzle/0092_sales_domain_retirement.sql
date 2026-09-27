-- Operator-only migration.  This file is deliberately absent from the normal
-- Drizzle journal.  A non-empty live D1 can pass only with the exact one-time
-- approved ticket installed by tools/sales-d1-retirement.ts in the same
-- BEGIN IMMEDIATE transaction.  The completed row remains as the immutable
-- response-loss recovery receipt.  A pristine bootstrap may replay the file
-- without inventing a cutover.
CREATE TABLE IF NOT EXISTS domain_retirement_receipts (
  domain TEXT PRIMARY KEY NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('approved', 'completed')),
  cutover_id TEXT NOT NULL,
  plan_id TEXT NOT NULL,
  attestation_sha256 TEXT NOT NULL,
  smoke_receipt_sha256 TEXT NOT NULL,
  preflight_evidence_sha256 TEXT NOT NULL,
  migration_sha256 TEXT NOT NULL,
  audit_id TEXT NOT NULL,
  preserved_evidence_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  CHECK (
    (status = 'approved' AND completed_at IS NULL)
    OR (status = 'completed' AND completed_at IS NOT NULL)
  )
);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS domain_retirement_receipts_insert_guard
BEFORE INSERT ON domain_retirement_receipts
WHEN NEW.status <> 'approved'
  OR NEW.completed_at IS NOT NULL
  OR EXISTS (
    SELECT 1 FROM domain_retirement_receipts WHERE domain = NEW.domain
  )
BEGIN SELECT RAISE(ABORT, 'domain_retirement_receipt_insert_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS domain_retirement_receipts_transition_guard
BEFORE UPDATE ON domain_retirement_receipts
WHEN NOT (
  OLD.status = 'approved'
  AND NEW.status = 'completed'
  AND OLD.domain = NEW.domain
  AND OLD.version = NEW.version
  AND OLD.cutover_id = NEW.cutover_id
  AND OLD.plan_id = NEW.plan_id
  AND OLD.attestation_sha256 = NEW.attestation_sha256
  AND OLD.smoke_receipt_sha256 = NEW.smoke_receipt_sha256
  AND OLD.preflight_evidence_sha256 = NEW.preflight_evidence_sha256
  AND OLD.migration_sha256 = NEW.migration_sha256
  AND OLD.audit_id = NEW.audit_id
  AND OLD.preserved_evidence_sha256 = NEW.preserved_evidence_sha256
  AND OLD.created_at = NEW.created_at
  AND OLD.completed_at IS NULL
  AND NEW.completed_at IS NOT NULL
)
BEGIN SELECT RAISE(ABORT, 'domain_retirement_receipt_update_forbidden'); END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS domain_retirement_receipts_no_delete
BEFORE DELETE ON domain_retirement_receipts
BEGIN SELECT RAISE(ABORT, 'domain_retirement_receipt_delete_forbidden'); END;--> statement-breakpoint

-- Retire the D1-owned sales domain after either the terminal PostgreSQL
-- authority handoff plus quiescence, or a pristine bootstrap where D1 still
-- has its default authority row but has never stored any sales facts, batches,
-- uploads, projection events, response caches or import audit state.  The
-- bootstrap path permits a brand-new database to replay 0001 -> 0092 without
-- inventing a cutover; every non-empty D1-owned sales domain remains
-- fail-closed. Integer overflow is used as a read-only assertion because
-- SQLite's RAISE() function is only legal inside triggers.
SELECT CASE
  WHEN (
    (SELECT COUNT(*) FROM `sales_write_authority` WHERE `id` = 1 AND `owner` = 'postgresql') = 1
      AND (
        SELECT COUNT(*) FROM `domain_retirement_receipts`
        WHERE `domain` = 'sales'
          AND `version` = 'sales-domain-retirement-receipt-v1'
          AND `status` = 'approved'
          AND `cutover_id` = (
            SELECT `cutover_id` FROM `sales_write_authority` WHERE `id` = 1
          )
          AND length(`cutover_id`) BETWEEN 8 AND 128
          AND length(`plan_id`) = 64
          AND `plan_id` NOT GLOB '*[^0-9a-f]*'
          AND length(`attestation_sha256`) = 64
          AND `attestation_sha256` NOT GLOB '*[^0-9a-f]*'
          AND length(`smoke_receipt_sha256`) = 64
          AND `smoke_receipt_sha256` NOT GLOB '*[^0-9a-f]*'
          AND length(`preflight_evidence_sha256`) = 64
          AND `preflight_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
          AND length(`migration_sha256`) = 64
          AND `migration_sha256` NOT GLOB '*[^0-9a-f]*'
          AND length(`audit_id`) = 64
          AND `audit_id` NOT GLOB '*[^0-9a-f]*'
          AND length(`preserved_evidence_sha256`) = 64
          AND `preserved_evidence_sha256` NOT GLOB '*[^0-9a-f]*'
          AND `completed_at` IS NULL
      ) = 1
      AND (SELECT COUNT(*) FROM `sales_import_batches` WHERE `status` = 'processing') = 0
      AND (
        SELECT COUNT(*)
        FROM `sales_import_uploads`
        WHERE `status` IN ('uploading', 'ready', 'processing')
          AND (datetime(`expires_at`) IS NULL OR datetime(`expires_at`) > CURRENT_TIMESTAMP)
      ) = 0
      -- Chunk rows retain the only durable R2 object keys.  They must be
      -- cleaned by the controlled cutover operation before this migration is
      -- allowed to drop the upload tables, including for expired sessions.
      AND (SELECT COUNT(*) FROM `sales_import_upload_chunks`) = 0
      AND (
        SELECT COUNT(*) FROM `import_content_fingerprints`
        WHERE `domain` = 'sales' AND `status` = 'processing'
      ) = 0
      AND (
        SELECT COUNT(*) FROM `import_content_attempts`
        WHERE `domain` = 'sales' AND `outcome` = 'processing'
      ) = 0
      AND (
        SELECT COUNT(*) FROM `import_scope_heads`
        WHERE `domain` = 'sales' AND `status` = 'processing'
      ) = 0
  ) OR (
    (SELECT COUNT(*) FROM `sales_write_authority` WHERE `id` = 1 AND `owner` = 'd1') = 1
      AND (SELECT COUNT(*) FROM `sales_order_lines`) = 0
      AND (SELECT COUNT(*) FROM `sales_import_batches`) = 0
      AND (SELECT COUNT(*) FROM `sales_import_uploads`) = 0
      AND (SELECT COUNT(*) FROM `sales_import_upload_chunks`) = 0
      AND (SELECT COUNT(*) FROM `sales_overview_response_cache`) = 0
      AND (SELECT COUNT(*) FROM `sales_projection_outbox`) = 0
      AND (
        SELECT COUNT(*) FROM `sales_overview_cache_state`
        WHERE `id` = 1 AND `sales_revision` = 1
      ) = 1
      AND (SELECT COUNT(*) FROM `import_content_fingerprints` WHERE `domain` = 'sales') = 0
      AND (SELECT COUNT(*) FROM `import_content_attempts` WHERE `domain` = 'sales') = 0
      AND (SELECT COUNT(*) FROM `import_scope_heads` WHERE `domain` = 'sales') = 0
  )
  THEN 1
  ELSE abs(-9223372036854775808)
END;--> statement-breakpoint

-- Remove every 0090 authority trigger before deleting sales-owned rows or
-- tables.  The market cache triggers must also go before sales_order_lines.
DROP TRIGGER IF EXISTS `sales_authority_singleton_insert_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_singleton_delete_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_transition_guard`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_order_lines_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_order_lines_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_order_lines_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_batches_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_batches_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_batches_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_uploads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_uploads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_uploads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_upload_chunks_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_upload_chunks_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_upload_chunks_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_cache_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_cache_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_cache_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_revision_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_revision_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_revision_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_source_state_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_source_state_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_source_state_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_outbox_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_outbox_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_outbox_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_fingerprints_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_fingerprints_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_fingerprints_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_attempts_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_attempts_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_attempts_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_scope_heads_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_scope_heads_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `sales_authority_scope_heads_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_sales_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_sales_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_sales_delete`;--> statement-breakpoint

-- Shared import-control tables remain for every other domain.  Only sales
-- rows are retired, after the processing checks above have passed.
DELETE FROM `import_content_fingerprints` WHERE `domain` = 'sales';--> statement-breakpoint
DELETE FROM `import_content_attempts` WHERE `domain` = 'sales';--> statement-breakpoint
DELETE FROM `import_scope_heads` WHERE `domain` = 'sales';--> statement-breakpoint

-- The three import-control tables remain shared by active D1 domains.  Replace
-- the old authority-dependent guards with permanent sales-retirement guards
-- that do not query the retired sales_write_authority schema.  Non-sales rows
-- remain fully writable; no statement may create, reclassify, mutate or remove
-- a row whose exact domain identity is `sales`.
CREATE TRIGGER `sales_retired_fingerprints_insert_guard`
BEFORE INSERT ON `import_content_fingerprints`
WHEN NEW.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_fingerprints_update_guard`
BEFORE UPDATE ON `import_content_fingerprints`
WHEN OLD.`domain` = 'sales' OR NEW.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_fingerprints_delete_guard`
BEFORE DELETE ON `import_content_fingerprints`
WHEN OLD.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_attempts_insert_guard`
BEFORE INSERT ON `import_content_attempts`
WHEN NEW.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_attempts_update_guard`
BEFORE UPDATE ON `import_content_attempts`
WHEN OLD.`domain` = 'sales' OR NEW.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_attempts_delete_guard`
BEFORE DELETE ON `import_content_attempts`
WHEN OLD.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_scope_heads_insert_guard`
BEFORE INSERT ON `import_scope_heads`
WHEN NEW.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_scope_heads_update_guard`
BEFORE UPDATE ON `import_scope_heads`
WHEN OLD.`domain` = 'sales' OR NEW.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint
CREATE TRIGGER `sales_retired_scope_heads_delete_guard`
BEFORE DELETE ON `import_scope_heads`
WHEN OLD.`domain` = 'sales'
BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END;--> statement-breakpoint

DROP TABLE IF EXISTS `sales_import_upload_chunks`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_import_uploads`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_order_lines`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_import_batches`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_overview_response_cache`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_overview_cache_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_projection_outbox`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_projection_source_state`;--> statement-breakpoint
DROP TABLE IF EXISTS `sales_write_authority`;--> statement-breakpoint

-- Reserve every retired sales table name with an empty read-only view.  These
-- tombstones contain no facts and are not a write source; they exist solely so
-- a stale Worker cannot resurrect the retired domain with CREATE TABLE IF NOT
-- EXISTS.  SQLite views are non-writable without INSTEAD OF triggers, cannot be
-- indexed, and reject DROP TABLE, so legacy schema and DML paths fail closed.
CREATE VIEW `sales_import_upload_chunks` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_import_uploads` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_order_lines` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_import_batches` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_overview_response_cache` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_overview_cache_state` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_projection_outbox` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_projection_source_state` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;--> statement-breakpoint
CREATE VIEW `sales_write_authority` AS
  SELECT 'sales-domain-retired-v1' AS `retirement_tombstone`
  WHERE 0;
