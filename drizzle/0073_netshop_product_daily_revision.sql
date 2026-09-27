CREATE TABLE IF NOT EXISTS netshop_product_daily_revisions (
  platform TEXT PRIMARY KEY NOT NULL,
  data_version INTEGER NOT NULL DEFAULT 0 CHECK (data_version >= 0),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
