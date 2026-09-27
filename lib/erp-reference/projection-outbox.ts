export const ERP_REFERENCE_PROJECTION_CANONICAL_FORMAT_VERSION =
  "erp-reference-projection-v1";

export const ERP_PRODUCT_PROJECTION_SCOPE_JSON = JSON.stringify({ source: "products" });

const EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

export const erpReferenceProjectionSchemaStatements = [
  `CREATE TABLE IF NOT EXISTS erp_reference_projection_source_state (
    id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
    source_epoch TEXT NOT NULL CHECK (
      length(source_epoch) = 32
      AND source_epoch = lower(source_epoch)
      AND source_epoch NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT OR IGNORE INTO erp_reference_projection_source_state
    (id, source_epoch, created_at, updated_at)
    VALUES (1, lower(hex(randomblob(16))), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`,
  `CREATE TABLE IF NOT EXISTS erp_product_projection_state (
    id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
    erp_revision INTEGER NOT NULL DEFAULT 1 CHECK (erp_revision >= 1),
    source_batch_id TEXT NOT NULL DEFAULT '',
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    content_hash TEXT NOT NULL DEFAULT '${EMPTY_SHA256}' CHECK (
      length(content_hash) = 64
      AND content_hash = lower(content_hash)
      AND content_hash NOT GLOB '*[^0-9a-f]*'
    ),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT OR IGNORE INTO erp_product_projection_state
    (id, erp_revision, source_batch_id, row_count, content_hash, updated_at)
    SELECT 1, 1, '', 0, '${EMPTY_SHA256}', CURRENT_TIMESTAMP
    WHERE NOT EXISTS (SELECT 1 FROM erp_product_master LIMIT 1)`,
  `CREATE TABLE IF NOT EXISTS erp_reference_projection_outbox (
    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    source_epoch TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'erp' CHECK (domain = 'erp'),
    operation TEXT NOT NULL DEFAULT 'replace_all' CHECK (operation = 'replace_all'),
    scope_json TEXT NOT NULL CHECK (scope_json = '${ERP_PRODUCT_PROJECTION_SCOPE_JSON}'),
    source_batch_id TEXT NOT NULL,
    erp_revision INTEGER NOT NULL CHECK (erp_revision >= 2),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    content_hash TEXT NOT NULL CHECK (
      length(content_hash) = 64
      AND content_hash = lower(content_hash)
      AND content_hash NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_format_version TEXT NOT NULL CHECK (
      canonical_format_version = '${ERP_REFERENCE_PROJECTION_CANONICAL_FORMAT_VERSION}'
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS erp_reference_projection_outbox_event_id_uq
    ON erp_reference_projection_outbox (event_id)`,
  `CREATE TRIGGER IF NOT EXISTS erp_reference_projection_source_no_update
    BEFORE UPDATE ON erp_reference_projection_source_state
    BEGIN
      SELECT RAISE(ABORT, 'ERP projection source epoch is immutable');
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_reference_projection_source_no_delete
    BEFORE DELETE ON erp_reference_projection_source_state
    BEGIN
      SELECT RAISE(ABORT, 'ERP projection source epoch is immutable');
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_product_projection_state_guard
    BEFORE UPDATE ON erp_product_projection_state
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
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_product_projection_state_no_delete
    BEFORE DELETE ON erp_product_projection_state
    BEGIN
      SELECT RAISE(ABORT, 'ERP projection state is immutable');
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_reference_projection_outbox_guard
    BEFORE INSERT ON erp_reference_projection_outbox
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
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_reference_projection_outbox_no_update
    BEFORE UPDATE ON erp_reference_projection_outbox
    BEGIN
      SELECT RAISE(ABORT, 'ERP projection outbox is append-only');
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_reference_projection_outbox_no_delete
    BEFORE DELETE ON erp_reference_projection_outbox
    BEGIN
      SELECT RAISE(ABORT, 'ERP projection outbox is append-only');
    END`,
  `CREATE TRIGGER IF NOT EXISTS erp_product_import_requires_projection_event
    BEFORE UPDATE OF status ON erp_reference_import_batches
    WHEN OLD.source_key = 'products'
      AND OLD.status = 'processing'
      AND NEW.status = 'completed'
      AND NOT EXISTS (
        SELECT 1 FROM erp_reference_projection_outbox AS event
        WHERE event.source_batch_id = NEW.id
          AND event.domain = 'erp'
          AND event.operation = 'replace_all'
      )
    BEGIN
      SELECT RAISE(ABORT, 'completed ERP products import requires projection event');
    END`,
] as const;

const SHA256_HEX_PATTERN = /^[0-9a-f]{64}$/;

export function assertErpProjectionContentHash(contentHash: string): string {
  if (!SHA256_HEX_PATTERN.test(contentHash)) {
    throw new Error("ERP 投影 outbox 的规范内容摘要必须是 64 位小写 SHA-256");
  }
  return contentHash;
}

export const bumpErpProductProjectionRevisionSql = `UPDATE erp_product_projection_state
  SET erp_revision = erp_revision + 1,
      source_batch_id = ?,
      row_count = ?,
      content_hash = ?,
      updated_at = CURRENT_TIMESTAMP
  WHERE id = 1
    AND EXISTS (
      SELECT 1 FROM erp_reference_import_batches
      WHERE id = ?
        AND source_key = 'products'
        AND status = 'processing'
        AND row_count = ?
        AND json_extract(totals_json, '$.contentHash') = ?
    )`;

export const insertErpReferenceProjectionOutboxEventSql = `
  INSERT INTO erp_reference_projection_outbox (
    event_id, source_epoch, domain, operation, scope_json, source_batch_id,
    erp_revision, row_count, content_hash, canonical_format_version, created_at
  )
  SELECT
    source.source_epoch || ':erp:' || state.source_batch_id,
    source.source_epoch,
    'erp',
    'replace_all',
    ?,
    state.source_batch_id,
    state.erp_revision,
    state.row_count,
    state.content_hash,
    ?,
    CURRENT_TIMESTAMP
  FROM erp_product_projection_state AS state
  JOIN erp_reference_projection_source_state AS source ON source.id = 1
  WHERE state.id = 1
    AND state.source_batch_id = ?
    AND EXISTS (
      SELECT 1 FROM erp_reference_import_batches
      WHERE id = state.source_batch_id
        AND source_key = 'products'
        AND status = 'processing'
    )
  ON CONFLICT(event_id) DO NOTHING
`;
