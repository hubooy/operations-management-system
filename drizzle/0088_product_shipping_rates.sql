CREATE TABLE IF NOT EXISTS product_shipping_rate_import_batches (
  id TEXT PRIMARY KEY NOT NULL,
  source TEXT NOT NULL DEFAULT 'sku_cumulative',
  file_name TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  file_hash TEXT NOT NULL,
  raw_file_hash TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'processing',
  source_row_count INTEGER NOT NULL DEFAULT 0,
  row_count INTEGER NOT NULL DEFAULT 0,
  inserted_count INTEGER NOT NULL DEFAULT 0,
  updated_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  warning_count INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  totals_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS product_shipping_rate_batches_file_hash_uq
  ON product_shipping_rate_import_batches (file_hash);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS product_shipping_rate_batches_completed_idx
  ON product_shipping_rate_import_batches (completed_at DESC, created_at DESC);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS product_shipping_rates (
  product_code TEXT PRIMARY KEY NOT NULL,
  shipping_rate REAL NOT NULL,
  source_row_number INTEGER NOT NULL,
  last_import_batch_id TEXT NOT NULL
    REFERENCES product_shipping_rate_import_batches(id) ON DELETE RESTRICT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS product_shipping_rates_batch_idx
  ON product_shipping_rates (last_import_batch_id, product_code);
