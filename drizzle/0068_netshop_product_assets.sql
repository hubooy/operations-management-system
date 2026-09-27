CREATE INDEX IF NOT EXISTS netshop_rows_product_assets_identity_idx
ON netshop_rows(platform, shop_name, spu_id, snapshot_date DESC)
WHERE source = 'tmall_product_assets' AND dataset = 'spu_assets';
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS netshop_rows_product_assets_hash_idx
ON netshop_rows(json_extract(raw_json, '$."图片内容SHA256"'))
WHERE source = 'tmall_product_assets' AND dataset = 'spu_assets' AND json_valid(raw_json);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS netshop_asset_uploads (
  id TEXT PRIMARY KEY NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  shop_name TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  chunk_size_bytes INTEGER NOT NULL,
  chunk_count INTEGER NOT NULL,
  received_chunk_count INTEGER NOT NULL DEFAULT 0,
  received_bytes INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'uploading',
  processing_owner TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS netshop_asset_uploads_expiry_idx
ON netshop_asset_uploads(expires_at, status);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS netshop_asset_upload_chunks (
  upload_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  object_key TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (upload_id, chunk_index),
  FOREIGN KEY (upload_id) REFERENCES netshop_asset_uploads(id) ON DELETE CASCADE
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS netshop_asset_upload_chunks_upload_idx
ON netshop_asset_upload_chunks(upload_id, chunk_index);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS netshop_asset_upload_results (
  upload_id TEXT PRIMARY KEY NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (upload_id) REFERENCES netshop_asset_uploads(id) ON DELETE CASCADE
);
