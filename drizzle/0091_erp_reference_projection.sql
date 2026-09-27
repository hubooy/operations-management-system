CREATE TABLE IF NOT EXISTS `erp_reference_projection_source_state` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `source_epoch` text NOT NULL CHECK (
    length(`source_epoch`) = 32
    AND `source_epoch` = lower(`source_epoch`)
    AND `source_epoch` NOT GLOB '*[^0-9a-f]*'
  ),
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `erp_reference_projection_source_state`
  (`id`, `source_epoch`, `created_at`, `updated_at`)
  VALUES (1, lower(hex(randomblob(16))), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `erp_product_projection_state` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `erp_revision` integer DEFAULT 1 NOT NULL CHECK (`erp_revision` >= 1),
  `source_batch_id` text DEFAULT '' NOT NULL,
  `row_count` integer DEFAULT 0 NOT NULL CHECK (`row_count` >= 0),
  `content_hash` text NOT NULL CHECK (
    length(`content_hash`) = 64
    AND `content_hash` = lower(`content_hash`)
    AND `content_hash` NOT GLOB '*[^0-9a-f]*'
  ),
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `erp_product_projection_state` (
  `id`, `erp_revision`, `source_batch_id`, `row_count`, `content_hash`, `updated_at`
)
SELECT
  1,
  revision.erp_product_revision,
  COALESCE((
    SELECT batch.id
    FROM erp_reference_import_batches AS batch
    WHERE batch.source_key = 'products' AND batch.status = 'completed'
    ORDER BY batch.completed_at DESC, batch.created_at DESC, batch.id DESC
    LIMIT 1
  ), ''),
  (SELECT COUNT(*) FROM erp_product_master),
  CASE
    WHEN (SELECT COUNT(*) FROM erp_product_master) = 0
      THEN 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    ELSE (
      SELECT lower(CAST(json_extract(batch.totals_json, '$.contentHash') AS text))
      FROM erp_reference_import_batches AS batch
      WHERE batch.source_key = 'products' AND batch.status = 'completed'
      ORDER BY batch.completed_at DESC, batch.created_at DESC, batch.id DESC
      LIMIT 1
    )
  END,
  CURRENT_TIMESTAMP
FROM sales_overview_cache_state AS revision
WHERE revision.id = 1;--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `erp_reference_projection_outbox` (
  `event_sequence` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `event_id` text NOT NULL,
  `source_epoch` text NOT NULL,
  `domain` text DEFAULT 'erp' NOT NULL CHECK (`domain` = 'erp'),
  `operation` text DEFAULT 'replace_all' NOT NULL CHECK (`operation` = 'replace_all'),
  `scope_json` text NOT NULL CHECK (`scope_json` = '{"source":"products"}'),
  `source_batch_id` text NOT NULL,
  `erp_revision` integer NOT NULL CHECK (`erp_revision` >= 2),
  `row_count` integer NOT NULL CHECK (`row_count` >= 0),
  `content_hash` text NOT NULL CHECK (
    length(`content_hash`) = 64
    AND `content_hash` = lower(`content_hash`)
    AND `content_hash` NOT GLOB '*[^0-9a-f]*'
  ),
  `canonical_format_version` text NOT NULL CHECK (
    `canonical_format_version` = 'erp-reference-projection-v1'
  ),
  `created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `erp_reference_projection_outbox_event_id_uq`
  ON `erp_reference_projection_outbox` (`event_id`);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_projection_source_no_update`
  BEFORE UPDATE ON `erp_reference_projection_source_state`
  BEGIN
    SELECT RAISE(ABORT, 'ERP projection source epoch is immutable');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_projection_source_no_delete`
  BEFORE DELETE ON `erp_reference_projection_source_state`
  BEGIN
    SELECT RAISE(ABORT, 'ERP projection source epoch is immutable');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_product_projection_state_guard`
  BEFORE UPDATE ON `erp_product_projection_state`
  WHEN NEW.erp_revision <> OLD.erp_revision + 1
    OR NEW.source_batch_id = ''
    OR NOT EXISTS (
      SELECT 1 FROM erp_reference_import_batches AS batch
      WHERE batch.id = NEW.source_batch_id
        AND batch.source_key = 'products'
        AND batch.status = 'processing'
        AND batch.row_count = NEW.row_count
        AND json_extract(batch.totals_json, '$.contentHash') = NEW.content_hash
    )
  BEGIN
    SELECT RAISE(ABORT, 'invalid ERP projection state transition');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_product_projection_state_no_delete`
  BEFORE DELETE ON `erp_product_projection_state`
  BEGIN
    SELECT RAISE(ABORT, 'ERP projection state is immutable');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_projection_outbox_guard`
  BEFORE INSERT ON `erp_reference_projection_outbox`
  WHEN NEW.source_epoch <> (
      SELECT source_epoch FROM erp_reference_projection_source_state WHERE id = 1
    )
    OR NEW.event_id <> NEW.source_epoch || ':erp:' || NEW.source_batch_id
    OR NOT EXISTS (
      SELECT 1 FROM erp_product_projection_state AS state
      WHERE state.id = 1
        AND state.erp_revision = NEW.erp_revision
        AND state.source_batch_id = NEW.source_batch_id
        AND state.row_count = NEW.row_count
        AND state.content_hash = NEW.content_hash
    )
  BEGIN
    SELECT RAISE(ABORT, 'invalid ERP projection outbox event');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_projection_outbox_no_update`
  BEFORE UPDATE ON `erp_reference_projection_outbox`
  BEGIN
    SELECT RAISE(ABORT, 'ERP projection outbox is append-only');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_reference_projection_outbox_no_delete`
  BEFORE DELETE ON `erp_reference_projection_outbox`
  BEGIN
    SELECT RAISE(ABORT, 'ERP projection outbox is append-only');
  END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `erp_product_import_requires_projection_event`
  BEFORE UPDATE OF `status` ON `erp_reference_import_batches`
  WHEN OLD.source_key = 'products'
    AND OLD.status = 'processing'
    AND NEW.status = 'completed'
    AND NOT EXISTS (
      SELECT 1 FROM `erp_reference_projection_outbox` AS event
      WHERE event.source_batch_id = NEW.id
        AND event.domain = 'erp'
        AND event.operation = 'replace_all'
    )
  BEGIN
    SELECT RAISE(ABORT, 'completed ERP products import requires projection event');
  END;
