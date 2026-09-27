import { ensureMarketSubcategoryTaxonomyData } from "@/lib/market/subcategory-taxonomy";

export type MarketSchemaDatabase = {
  prepare(sql: string): {
    bind(...values: unknown[]): {
      first<T = unknown>(): Promise<T | null>;
      all<T = unknown>(): Promise<{ results?: T[] }>;
      run(): Promise<unknown>;
    };
    first<T = unknown>(): Promise<T | null>;
    all<T = unknown>(): Promise<{ results?: T[] }>;
    run(): Promise<unknown>;
  };
  batch(statements: Array<{ run(): Promise<unknown> }>): Promise<unknown>;
};

const selfOperated = "\u81ea\u8425";
const unknownMode = "\u672a\u77e5";
const unknownPriceBand = "\u672a\u786e\u8ba4\u4ef7\u683c";

export const marketNetshopProjectionStatements = [
  `CREATE TABLE IF NOT EXISTS market_netshop_projection_control (
    id INTEGER PRIMARY KEY NOT NULL CHECK (id=1),
    active_revision TEXT NOT NULL DEFAULT '',
    active_total INTEGER NOT NULL DEFAULT 0,
    syncing_revision TEXT NOT NULL DEFAULT '',
    owner_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT OR IGNORE INTO market_netshop_projection_control
    (id,active_revision,active_total,syncing_revision,owner_token)
    VALUES (1,'',0,'','')`,
  `CREATE TABLE IF NOT EXISTS market_netshop_projection (
    projection_revision TEXT NOT NULL,
    projection_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('metric','identity','brand')),
    source TEXT NOT NULL DEFAULT '',
    dataset TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    business_date TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    spu_id TEXT NOT NULL DEFAULT '',
    product_code TEXT NOT NULL DEFAULT '',
    transaction_amount_cents INTEGER NOT NULL DEFAULT 0,
    brand TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (projection_revision,projection_key)
  )`,
  `CREATE INDEX IF NOT EXISTS market_netshop_projection_metric_idx
    ON market_netshop_projection
      (projection_revision,kind,source,dataset,business_date,sku_id,spu_id)`,
  `CREATE INDEX IF NOT EXISTS market_netshop_projection_identity_idx
    ON market_netshop_projection
      (projection_revision,kind,sku_id,spu_id,product_code)`,
  `CREATE INDEX IF NOT EXISTS market_netshop_projection_brand_idx
    ON market_netshop_projection (projection_revision,kind,brand)`,
  `CREATE VIEW IF NOT EXISTS market_netshop_active_projection AS
    SELECT projection.* FROM market_netshop_projection projection
    JOIN market_netshop_projection_control control
      ON control.id=1 AND control.active_revision=projection.projection_revision`,
] as const;

const marketEffectiveMetricsCacheStatements = [
  `CREATE TABLE IF NOT EXISTS market_effective_metrics_cache (
    market_entry_id INTEGER PRIMARY KEY NOT NULL,
    effective_gmv_cents INTEGER,
    real_gmv_cents INTEGER,
    gmv_out_of_band INTEGER,
    effective_quantity INTEGER,
    effective_average_transaction_price_cents INTEGER,
    effective_conversion_bps INTEGER
  )`,
  `CREATE TABLE IF NOT EXISTS market_effective_metrics_cache_state (
    id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
    market_row_count INTEGER NOT NULL,
    market_updated_at TEXT NOT NULL,
    netshop_row_count INTEGER NOT NULL,
    netshop_updated_at TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
] as const;

const marketMonthlySummaryCacheStatements = [
  `CREATE TABLE IF NOT EXISTS market_monthly_summary_cache (
    category TEXT NOT NULL,
    scope TEXT NOT NULL,
    ranking_dimension TEXT NOT NULL,
    sku_code TEXT NOT NULL,
    month TEXT NOT NULL,
    representative_entry_id INTEGER NOT NULL,
    coverage_period_start TEXT NOT NULL,
    coverage_period_end TEXT NOT NULL,
    operation_mode TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    rank INTEGER,
    product_name TEXT NOT NULL,
    brand TEXT NOT NULL,
    gmv_cents INTEGER,
    quantity INTEGER,
    page_views INTEGER NOT NULL,
    visitors INTEGER NOT NULL,
    conversion_bps INTEGER,
    official_market_price_cents INTEGER,
    confirmation_status TEXT,
    market_price_source TEXT NOT NULL,
    display_price_band TEXT NOT NULL,
    confirmed_price_band TEXT NOT NULL,
    is_own INTEGER NOT NULL,
    refreshed_revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (category, scope, ranking_dimension, sku_code, month)
  )`,
  `CREATE TABLE IF NOT EXISTS market_monthly_summary_cache_state (
    id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
    source_revision INTEGER NOT NULL DEFAULT 1,
    built_revision INTEGER NOT NULL DEFAULT -1,
    status TEXT NOT NULL DEFAULT 'stale',
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    refreshed_at TEXT,
    error_code TEXT NOT NULL DEFAULT ''
  )`,
  `CREATE TABLE IF NOT EXISTS market_monthly_summary_dirty_keys (
    category TEXT NOT NULL,
    scope TEXT NOT NULL,
    ranking_dimension TEXT NOT NULL,
    sku_code TEXT NOT NULL,
    month TEXT NOT NULL,
    dirty_revision INTEGER NOT NULL,
    PRIMARY KEY (category, scope, ranking_dimension, sku_code, month)
  )`,
  `CREATE TABLE IF NOT EXISTS market_monthly_summary_dirty_scopes (
    category TEXT PRIMARY KEY NOT NULL,
    dirty_revision INTEGER NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS market_monthly_summary_dirty_products (
    product_code TEXT PRIMARY KEY NOT NULL,
    dirty_revision INTEGER NOT NULL
  )`,
] as const;

const marketMonthlySummaryCacheIndexStatements = [
  `CREATE INDEX IF NOT EXISTS market_monthly_summary_month_idx ON market_monthly_summary_cache (month, category, ranking_dimension)`,
  `CREATE INDEX IF NOT EXISTS market_monthly_summary_brand_idx ON market_monthly_summary_cache (brand, month)`,
  `CREATE INDEX IF NOT EXISTS market_monthly_summary_subcategory_idx ON market_monthly_summary_cache (subcategory, month)`,
  `CREATE INDEX IF NOT EXISTS market_monthly_summary_display_band_idx ON market_monthly_summary_cache (display_price_band, month)`,
  `CREATE INDEX IF NOT EXISTS market_monthly_summary_confirmed_band_idx ON market_monthly_summary_cache (confirmed_price_band, month)`,
] as const;

export const marketSystemKpiCacheControlTableStatement = `CREATE TABLE IF NOT EXISTS market_system_kpi_cache_control (
  id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
  suppress_all_revision INTEGER NOT NULL DEFAULT 0 CHECK (suppress_all_revision IN (0, 1)),
  suppress_identity_revision INTEGER NOT NULL DEFAULT 0 CHECK (suppress_identity_revision IN (0, 1)),
  owner_token TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)`;

const marketOverviewResponseCacheStatements = [
  `CREATE TABLE IF NOT EXISTS market_overview_response_cache (
    cache_key TEXT PRIMARY KEY NOT NULL,
    revision_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_system_kpi_cache_state (
    id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1),
    source_revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  marketSystemKpiCacheControlTableStatement,
] as const;

const marketSystemKpiCacheStateSeedStatement = `INSERT OR IGNORE INTO market_system_kpi_cache_state (id, source_revision)
  VALUES (1, 1)`;

export const marketSystemKpiCacheControlSeedStatement = `INSERT OR IGNORE INTO market_system_kpi_cache_control
  (id, suppress_all_revision, suppress_identity_revision, owner_token) VALUES (1, 0, 0, '')`;

const marketSystemKpiRevisionDependencies = [
  ["ranking", "market_ranking_entries"],
  ["price", "market_price_snapshots"],
  ["prompt", "market_annotation_prompt_versions"],
  ["image", "market_image_cache"],
  ["annotation", "market_annotation_items"],
  ["taxonomy", "market_subcategory_taxonomy"],
  ["identity", "market_master_identities"],
  ["batch", "market_import_batches"],
] as const;

export const marketSystemKpiCacheTriggerNames = marketSystemKpiRevisionDependencies.flatMap(([name]) =>
  (["insert", "update", "delete"] as const).map((operation) => `market_system_kpi_cache_${name}_${operation}`),
);

export const marketSystemKpiCacheTriggerStatements = marketSystemKpiRevisionDependencies.flatMap(([name, table]) =>
  (["insert", "update", "delete"] as const).map((operation) => `CREATE TRIGGER IF NOT EXISTS market_system_kpi_cache_${name}_${operation}
    AFTER ${operation.toUpperCase()} ON ${table}
    WHEN NOT EXISTS (
      SELECT 1 FROM market_system_kpi_cache_control
      WHERE id=1 AND (suppress_all_revision=1${name === "identity" ? " OR suppress_identity_revision=1" : ""})
    )
    BEGIN
      UPDATE market_system_kpi_cache_state
      SET source_revision=source_revision+1, updated_at=CURRENT_TIMESTAMP
      WHERE id=1;
    END`),
);

export const marketSystemKpiCacheTriggerDropStatements = marketSystemKpiRevisionDependencies.flatMap(([name]) =>
  (["insert", "update", "delete"] as const)
    .map((operation) => `DROP TRIGGER IF EXISTS market_system_kpi_cache_${name}_${operation}`),
);

export const marketSystemKpiIdentityTriggerDropStatements = marketSystemKpiCacheTriggerDropStatements
  .filter((statement) => statement.includes("_identity_"));

const marketImageCacheTableStatement = `CREATE TABLE IF NOT EXISTS market_image_cache (
    source_url TEXT PRIMARY KEY NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    object_key TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    image_source TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`;

const marketImageCacheJobTableStatement = `CREATE TABLE IF NOT EXISTS market_image_cache_jobs (
    id TEXT PRIMARY KEY NOT NULL,
    scope_key TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed')),
    requested_by TEXT NOT NULL DEFAULT '',
    discovery_cursor TEXT NOT NULL DEFAULT '',
    discovery_complete INTEGER NOT NULL DEFAULT 0 CHECK (discovery_complete IN (0,1)),
    discovered_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    propagation_pending_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    run_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT NOT NULL DEFAULT '',
    lease_epoch INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT,
    next_run_at TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`;

const marketImageCacheJobItemTableStatement = `CREATE TABLE IF NOT EXISTS market_image_cache_job_items (
    job_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','ready','completed','failed')),
    content_sha256 TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    PRIMARY KEY (job_id, source_url)
  )`;

const marketImageCacheClaimTableStatement = `CREATE TABLE IF NOT EXISTS market_image_cache_claims (
    source_url TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    job_lease_token TEXT NOT NULL,
    job_epoch INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL,
    lease_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`;

const marketImageCacheCounterTriggerStatements = [
  `CREATE TRIGGER IF NOT EXISTS market_image_cache_item_insert_counts
    AFTER INSERT ON market_image_cache_job_items
    BEGIN
      UPDATE market_image_cache_jobs SET
        discovered_count=discovered_count+1,
        pending_count=pending_count+CASE WHEN NEW.status='queued' THEN 1 ELSE 0 END,
        propagation_pending_count=propagation_pending_count+CASE WHEN NEW.status='ready' THEN 1 ELSE 0 END,
        completed_count=completed_count+CASE WHEN NEW.status='completed' THEN 1 ELSE 0 END,
        failed_count=failed_count+CASE WHEN NEW.status='failed' THEN 1 ELSE 0 END,
        processed_count=processed_count+CASE WHEN NEW.status IN ('completed','failed') THEN 1 ELSE 0 END,
        updated_at=CURRENT_TIMESTAMP
      WHERE id=NEW.job_id;
    END`,
  `CREATE TRIGGER IF NOT EXISTS market_image_cache_item_status_counts
    AFTER UPDATE OF status ON market_image_cache_job_items
    WHEN OLD.status<>NEW.status
    BEGIN
      UPDATE market_image_cache_jobs SET
        pending_count=MAX(0, pending_count
          + CASE WHEN NEW.status='queued' THEN 1 ELSE 0 END
          - CASE WHEN OLD.status='queued' THEN 1 ELSE 0 END),
        propagation_pending_count=MAX(0, propagation_pending_count
          + CASE WHEN NEW.status='ready' THEN 1 ELSE 0 END
          - CASE WHEN OLD.status='ready' THEN 1 ELSE 0 END),
        completed_count=MAX(0, completed_count
          + CASE WHEN NEW.status='completed' THEN 1 ELSE 0 END
          - CASE WHEN OLD.status='completed' THEN 1 ELSE 0 END),
        failed_count=MAX(0, failed_count
          + CASE WHEN NEW.status='failed' THEN 1 ELSE 0 END
          - CASE WHEN OLD.status='failed' THEN 1 ELSE 0 END),
        processed_count=MAX(0, processed_count
          + CASE WHEN NEW.status IN ('completed','failed') THEN 1 ELSE 0 END
          - CASE WHEN OLD.status IN ('completed','failed') THEN 1 ELSE 0 END),
        updated_at=CURRENT_TIMESTAMP
      WHERE id=NEW.job_id;
    END`,
] as const;

export const marketImportIdentityRefreshKeysTableStatement = `CREATE TABLE IF NOT EXISTS market_import_identity_refresh_keys_v2 (
  batch_id TEXT NOT NULL,
  owner_token TEXT NOT NULL,
  category TEXT NOT NULL,
  scope TEXT NOT NULL,
  ranking_dimension TEXT NOT NULL,
  sku_code TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (batch_id, owner_token, category, scope, ranking_dimension, sku_code)
)`;

export const marketBaseSchemaStatements = [
  ...marketOverviewResponseCacheStatements,
  ...marketNetshopProjectionStatements,
  `CREATE TABLE IF NOT EXISTS market_import_batches (
    id TEXT PRIMARY KEY NOT NULL,
    source_type TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    sheet_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    period_start TEXT,
    period_end TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    owner_token TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
  )`,
  `CREATE TABLE IF NOT EXISTS market_import_range_claims (
    range_key TEXT PRIMARY KEY NOT NULL,
    batch_id TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS market_import_staging_rows (
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    range_key TEXT NOT NULL,
    row_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (batch_id, row_number)
  )`,
  marketImportIdentityRefreshKeysTableStatement,
  `CREATE TABLE IF NOT EXISTS market_ranking_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    natural_key TEXT NOT NULL UNIQUE,
    source_row_number INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '\u5168\u90e8',
    price_band_filter TEXT NOT NULL DEFAULT '\u5168\u90e8',
    ranking_dimension TEXT NOT NULL DEFAULT 'SKU',
    operation_mode TEXT NOT NULL DEFAULT '${unknownMode}',
    subcategory TEXT NOT NULL DEFAULT '',
    source_brand TEXT NOT NULL DEFAULT '',
    source_operation_mode TEXT NOT NULL DEFAULT '',
    source_subcategory TEXT NOT NULL DEFAULT '',
    rank INTEGER,
    sku_code TEXT NOT NULL,
    product_name TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    price_cents INTEGER,
    price_low_cents INTEGER,
    price_high_cents INTEGER,
    price_estimated INTEGER NOT NULL DEFAULT 0,
    price_raw TEXT NOT NULL DEFAULT '',
    gmv_cents INTEGER NOT NULL DEFAULT 0,
    gmv_low_cents INTEGER,
    gmv_high_cents INTEGER,
    gmv_raw TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 0,
    quantity_low INTEGER,
    quantity_high INTEGER,
    quantity_raw TEXT NOT NULL DEFAULT '',
    page_views INTEGER NOT NULL DEFAULT 0,
    page_views_raw TEXT NOT NULL DEFAULT '',
    visitors INTEGER NOT NULL DEFAULT 0,
    visitors_low INTEGER,
    visitors_high INTEGER,
    visitors_raw TEXT NOT NULL DEFAULT '',
    conversion_bps INTEGER,
    conversion_low_bps INTEGER,
    conversion_high_bps INTEGER,
    conversion_raw TEXT NOT NULL DEFAULT '',
    cart_customers INTEGER NOT NULL DEFAULT 0,
    cart_customers_raw TEXT NOT NULL DEFAULT '',
    search_clicks INTEGER NOT NULL DEFAULT 0,
    search_clicks_raw TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    product_url TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    last_import_batch_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  ...marketEffectiveMetricsCacheStatements,
  ...marketMonthlySummaryCacheStatements,
  `CREATE TABLE IF NOT EXISTS market_sku_gmv_totals (
    sku_code TEXT PRIMARY KEY NOT NULL,
    gmv_total_cents INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_master_identities (
    category TEXT NOT NULL,
    scope TEXT NOT NULL,
    ranking_dimension TEXT NOT NULL,
    sku_code TEXT NOT NULL,
    latest_entry_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (category, scope, ranking_dimension, sku_code),
    UNIQUE (latest_entry_id)
  )`,
  `CREATE TABLE IF NOT EXISTS market_price_snapshots (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    sku_code TEXT NOT NULL,
    ranking_dimension TEXT NOT NULL DEFAULT 'SKU',
    month TEXT NOT NULL,
    source_price_cents INTEGER,
    ai_image_price_cents INTEGER,
    ai_price_type TEXT NOT NULL DEFAULT '',
    ai_confidence_bps INTEGER,
    ai_reason TEXT NOT NULL DEFAULT '',
    confirmed_market_price_cents INTEGER,
    average_transaction_price_cents INTEGER,
    price_low_cents INTEGER,
    price_high_cents INTEGER,
    image_content_sha256 TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    confirmation_status TEXT NOT NULL DEFAULT 'source_table',
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT,
    source_job_item_id TEXT NOT NULL DEFAULT '',
    prompt_version_id TEXT NOT NULL DEFAULT '',
    source_import_batch_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  marketImageCacheTableStatement,
  marketImageCacheJobTableStatement,
  marketImageCacheJobItemTableStatement,
  marketImageCacheClaimTableStatement,
  ...marketImageCacheCounterTriggerStatements,
  `CREATE TABLE IF NOT EXISTS market_price_band_versions (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL DEFAULT '*',
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    effective_from TEXT NOT NULL DEFAULT '1970-01-01',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_by TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    rolled_back_from_id TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
  )`,
  `CREATE TABLE IF NOT EXISTS market_price_band_items (
    id TEXT PRIMARY KEY NOT NULL,
    version_id TEXT NOT NULL,
    label TEXT NOT NULL,
    min_cents INTEGER,
    max_cents INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0
  )`,
  `CREATE TABLE IF NOT EXISTS market_master_mapping_rules (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    source_value TEXT NOT NULL,
    target_value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    effective_from TEXT NOT NULL DEFAULT '1970-01-01',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_subcategory_taxonomy (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_brand_suggestions (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL,
    scope TEXT NOT NULL,
    ranking_dimension TEXT NOT NULL DEFAULT 'SKU',
    sku_code TEXT NOT NULL,
    product_name TEXT NOT NULL DEFAULT '',
    current_brand TEXT NOT NULL DEFAULT '',
    ai_brand TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ai_pending',
    model_id TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT
  )`,
  `CREATE TABLE IF NOT EXISTS market_brand_recognition_jobs (
    id TEXT PRIMARY KEY NOT NULL,
    model_id TEXT NOT NULL,
    query_text TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    total_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    recognized_count INTEGER NOT NULL DEFAULT 0,
    empty_count INTEGER NOT NULL DEFAULT 0,
    batch_size INTEGER NOT NULL DEFAULT 40,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT
  )`,
  `CREATE TABLE IF NOT EXISTS market_brand_seeds (
    id TEXT PRIMARY KEY NOT NULL,
    canonical_brand TEXT NOT NULL,
    seed_text TEXT NOT NULL,
    normalized_seed TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'enabled',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_refreshed_at TEXT
  )`,
  `CREATE TABLE IF NOT EXISTS market_download_configs (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '全部',
    ranking_dimension TEXT NOT NULL,
    month_start TEXT NOT NULL,
    month_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'enabled',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_download_tasks (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '全部',
    month TEXT NOT NULL,
    ranking_dimension TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    jd_task_id TEXT NOT NULL DEFAULT '',
    source_file_name TEXT NOT NULL DEFAULT '',
    file_hash TEXT NOT NULL DEFAULT '',
    row_count INTEGER NOT NULL DEFAULT 0,
    header_valid INTEGER NOT NULL DEFAULT 0,
    period_valid INTEGER NOT NULL DEFAULT 0,
    category_valid INTEGER NOT NULL DEFAULT 0,
    dimension_valid INTEGER NOT NULL DEFAULT 0,
    staging_batch_id TEXT NOT NULL DEFAULT '',
    import_batch_id TEXT NOT NULL DEFAULT '',
    validation_json TEXT NOT NULL DEFAULT '{}',
    execution_token TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    next_retry_at TEXT,
    last_attempt_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_download_staging_rows (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    row_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS market_master_audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
] as const;

const rankingEntryColumns: Array<[string, string]> = [
  ["ranking_dimension", "TEXT NOT NULL DEFAULT 'SKU'"],
  ["operation_mode", `TEXT NOT NULL DEFAULT '${unknownMode}'`],
  ["subcategory", "TEXT NOT NULL DEFAULT ''"],
  ["source_brand", "TEXT NOT NULL DEFAULT ''"],
  ["source_operation_mode", "TEXT NOT NULL DEFAULT ''"],
  ["source_subcategory", "TEXT NOT NULL DEFAULT ''"],
  ["price_band_filter", "TEXT NOT NULL DEFAULT '全部'"],
  ["price_low_cents", "INTEGER"],
  ["price_high_cents", "INTEGER"],
  ["price_estimated", "INTEGER NOT NULL DEFAULT 0"],
  ["price_raw", "TEXT NOT NULL DEFAULT ''"],
  ["gmv_low_cents", "INTEGER"],
  ["gmv_high_cents", "INTEGER"],
  ["gmv_raw", "TEXT NOT NULL DEFAULT ''"],
  ["quantity_low", "INTEGER"],
  ["quantity_high", "INTEGER"],
  ["quantity_raw", "TEXT NOT NULL DEFAULT ''"],
  ["page_views_raw", "TEXT NOT NULL DEFAULT ''"],
  ["visitors_low", "INTEGER"],
  ["visitors_high", "INTEGER"],
  ["visitors_raw", "TEXT NOT NULL DEFAULT ''"],
  ["conversion_low_bps", "INTEGER"],
  ["conversion_high_bps", "INTEGER"],
  ["conversion_raw", "TEXT NOT NULL DEFAULT ''"],
  ["cart_customers_raw", "TEXT NOT NULL DEFAULT ''"],
  ["search_clicks_raw", "TEXT NOT NULL DEFAULT ''"],
];

const downloadConfigColumns: Array<[string, string]> = [
  ["scope", "TEXT NOT NULL DEFAULT '全部'"],
];

const priceSnapshotColumns: Array<[string, string]> = [
  ["scope", "TEXT NOT NULL DEFAULT ''"],
  ["ranking_dimension", "TEXT NOT NULL DEFAULT 'SKU'"],
  ["source_price_cents", "INTEGER"],
  ["ai_image_price_cents", "INTEGER"],
  ["ai_price_type", "TEXT NOT NULL DEFAULT ''"],
  ["ai_confidence_bps", "INTEGER"],
  ["ai_reason", "TEXT NOT NULL DEFAULT ''"],
  ["confirmed_market_price_cents", "INTEGER"],
  ["average_transaction_price_cents", "INTEGER"],
  ["price_low_cents", "INTEGER"],
  ["price_high_cents", "INTEGER"],
  ["image_content_sha256", "TEXT NOT NULL DEFAULT ''"],
  ["image_url", "TEXT NOT NULL DEFAULT ''"],
  ["confirmation_status", "TEXT NOT NULL DEFAULT 'source_table'"],
  ["confirmed_by", "TEXT NOT NULL DEFAULT ''"],
  ["confirmed_at", "TEXT"],
  ["source_job_item_id", "TEXT NOT NULL DEFAULT ''"],
  ["prompt_version_id", "TEXT NOT NULL DEFAULT ''"],
  ["source_import_batch_id", "TEXT NOT NULL DEFAULT ''"],
];

const downloadTaskColumns: Array<[string, string]> = [
  ["scope", "TEXT NOT NULL DEFAULT '全部'"],
  ["jd_task_id", "TEXT NOT NULL DEFAULT ''"],
  ["header_valid", "INTEGER NOT NULL DEFAULT 0"],
  ["period_valid", "INTEGER NOT NULL DEFAULT 0"],
  ["category_valid", "INTEGER NOT NULL DEFAULT 0"],
  ["dimension_valid", "INTEGER NOT NULL DEFAULT 0"],
  ["staging_batch_id", "TEXT NOT NULL DEFAULT ''"],
  ["import_batch_id", "TEXT NOT NULL DEFAULT ''"],
  ["validation_json", "TEXT NOT NULL DEFAULT '{}'"],
  ["execution_token", "TEXT NOT NULL DEFAULT ''"],
  ["last_attempt_at", "TEXT"],
  ["completed_at", "TEXT"],
];

const importBatchColumns: Array<[string, string]> = [
  ["owner_token", "TEXT NOT NULL DEFAULT ''"],
];

const systemKpiCacheControlColumns: Array<[string, string]> = [
  ["suppress_all_revision", "INTEGER NOT NULL DEFAULT 0 CHECK (suppress_all_revision IN (0, 1))"],
  ["suppress_identity_revision", "INTEGER NOT NULL DEFAULT 0 CHECK (suppress_identity_revision IN (0, 1))"],
  ["owner_token", "TEXT NOT NULL DEFAULT ''"],
];

const imageCacheJobColumns: Array<[string, string]> = [
  ["discovery_cursor", "TEXT NOT NULL DEFAULT ''"],
  ["discovery_complete", "INTEGER NOT NULL DEFAULT 0"],
  ["discovered_count", "INTEGER NOT NULL DEFAULT 0"],
  ["completed_count", "INTEGER NOT NULL DEFAULT 0"],
  ["failure_count", "INTEGER NOT NULL DEFAULT 0"],
];

const imageCacheClaimColumns: Array<[string, string]> = [
  ["job_lease_token", "TEXT NOT NULL DEFAULT ''"],
];

const marketPublishedPriceBandUniqueIndexStatement =
  `CREATE UNIQUE INDEX IF NOT EXISTS market_price_band_versions_published_category_uq ON market_price_band_versions (category) WHERE status = 'published'`;

const marketImageCachePropagationIndexStatements = [
  `CREATE INDEX IF NOT EXISTS market_price_snapshots_pending_image_url_idx
    ON market_price_snapshots (image_url, id)
    WHERE image_content_sha256='' AND image_url<>''`,
  `CREATE INDEX IF NOT EXISTS market_price_snapshots_image_hash_idx
    ON market_price_snapshots (image_content_sha256, category, scope, sku_code, ranking_dimension, month, id)
    WHERE image_content_sha256<>''`,
] as const;

export const marketPostUpgradeIndexStatements = [
  `CREATE INDEX IF NOT EXISTS market_import_batches_created_idx ON market_import_batches (created_at)`,
  `CREATE INDEX IF NOT EXISTS market_import_range_claims_batch_idx ON market_import_range_claims (batch_id, claim_token)`,
  `CREATE INDEX IF NOT EXISTS market_import_range_claims_expiry_idx ON market_import_range_claims (lease_expires_at)`,
  `CREATE INDEX IF NOT EXISTS market_import_staging_rows_range_idx ON market_import_staging_rows (batch_id, range_key)`,
  `CREATE INDEX IF NOT EXISTS market_entries_period_idx ON market_ranking_entries (period_end, period_start)`,
  `CREATE INDEX IF NOT EXISTS market_entries_category_idx ON market_ranking_entries (category, period_end)`,
  `CREATE INDEX IF NOT EXISTS market_entries_sku_idx ON market_ranking_entries (sku_code, period_end)`,
  `CREATE INDEX IF NOT EXISTS market_entries_brand_idx ON market_ranking_entries (brand, period_end)`,
  `CREATE INDEX IF NOT EXISTS market_entries_dimension_idx ON market_ranking_entries (ranking_dimension, operation_mode, period_end)`,
  `CREATE INDEX IF NOT EXISTS market_entries_subcategory_idx ON market_ranking_entries (subcategory, period_end)`,
  `CREATE INDEX IF NOT EXISTS market_entries_representative_idx ON market_ranking_entries (category, scope, ranking_dimension, sku_code, period_end DESC, period_start DESC, id DESC)`,
  `CREATE INDEX IF NOT EXISTS market_entries_annotation_catalog_idx ON market_ranking_entries (category, sku_code, period_end DESC, updated_at DESC, id DESC)`,
  `CREATE INDEX IF NOT EXISTS market_entries_rank_order_idx ON market_ranking_entries ((rank IS NULL), rank, gmv_cents DESC, id)`,
  `CREATE INDEX IF NOT EXISTS market_entries_image_url_idx ON market_ranking_entries (image_url) WHERE image_url<>''`,
  `CREATE INDEX IF NOT EXISTS market_entries_last_batch_idx ON market_ranking_entries (last_import_batch_id)`,
  `CREATE INDEX IF NOT EXISTS market_entries_batch_image_idx ON market_ranking_entries (last_import_batch_id, image_url) WHERE image_url<>''`,
  ...marketMonthlySummaryCacheIndexStatements,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_entries_canonical_price_band_uq ON market_ranking_entries (period_start, period_end, category, scope, price_band_filter, ranking_dimension, sku_code)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_price_snapshots_sku_month_uq ON market_price_snapshots (category, scope, sku_code, ranking_dimension, month)`,
  `CREATE INDEX IF NOT EXISTS market_price_snapshots_status_idx ON market_price_snapshots (confirmation_status, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_price_snapshots_hash_idx ON market_price_snapshots (sku_code, image_content_sha256, confirmed_at)`,
  ...marketImageCachePropagationIndexStatements,
  `CREATE INDEX IF NOT EXISTS market_image_cache_object_key_idx ON market_image_cache (object_key)`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_status_idx ON market_image_cache (status, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_updated_idx ON market_image_cache (updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_fetching_recovery_idx ON market_image_cache (status, updated_at, source_url) WHERE status='fetching'`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_jobs_runnable_idx ON market_image_cache_jobs (status, next_run_at, lease_expires_at, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_jobs_batch_idx ON market_image_cache_jobs (batch_id, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_job_items_work_idx ON market_image_cache_job_items (job_id, status, source_url)`,
  `CREATE INDEX IF NOT EXISTS market_image_cache_claims_job_expiry_idx ON market_image_cache_claims (job_id, lease_expires_at, source_url)`,
  `CREATE INDEX IF NOT EXISTS market_overview_response_cache_updated_idx ON market_overview_response_cache (updated_at)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_price_band_versions_category_version_uq ON market_price_band_versions (category, version)`,
  marketPublishedPriceBandUniqueIndexStatement,
  `CREATE INDEX IF NOT EXISTS market_price_band_versions_lookup_idx ON market_price_band_versions (category, status, effective_from, version)`,
  `CREATE INDEX IF NOT EXISTS market_price_band_items_version_idx ON market_price_band_items (version_id, sort_order)`,
  `CREATE INDEX IF NOT EXISTS market_master_mapping_rules_kind_idx ON market_master_mapping_rules (kind, category, status, effective_from)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_subcategory_taxonomy_category_name_uq ON market_subcategory_taxonomy (category, subcategory)`,
  `CREATE INDEX IF NOT EXISTS market_subcategory_taxonomy_lookup_idx ON market_subcategory_taxonomy (category, status, sort_order)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_brand_suggestions_identity_uq ON market_brand_suggestions (category, scope, ranking_dimension, sku_code)`,
  `CREATE INDEX IF NOT EXISTS market_brand_suggestions_status_idx ON market_brand_suggestions (status, category, updated_at)`,
  `CREATE INDEX IF NOT EXISTS market_brand_recognition_jobs_filter_idx ON market_brand_recognition_jobs (category, query_text, created_at)`,
  `CREATE INDEX IF NOT EXISTS market_brand_recognition_jobs_status_idx ON market_brand_recognition_jobs (status, updated_at)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_brand_seeds_normalized_uq ON market_brand_seeds (normalized_seed)`,
  `CREATE INDEX IF NOT EXISTS market_brand_seeds_lookup_idx ON market_brand_seeds (status, canonical_brand, source)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_download_configs_unique_uq ON market_download_configs (category, scope, ranking_dimension, month_start, month_end)`,
  `CREATE INDEX IF NOT EXISTS market_download_configs_status_idx ON market_download_configs (status, category, scope, ranking_dimension)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_download_tasks_unique_uq ON market_download_tasks (category, scope, month, ranking_dimension)`,
  `CREATE INDEX IF NOT EXISTS market_download_tasks_status_idx ON market_download_tasks (status, next_retry_at, updated_at)`,
  `CREATE UNIQUE INDEX IF NOT EXISTS market_download_staging_rows_file_row_uq ON market_download_staging_rows (task_id, file_hash, row_number)`,
  `CREATE INDEX IF NOT EXISTS market_master_audit_logs_entity_idx ON market_master_audit_logs (entity_type, entity_id, created_at)`,
] as const;

const marketPreUpgradeIndexStatements = {
  market_price_snapshots: `DROP INDEX IF EXISTS market_price_snapshots_sku_month_uq`,
  market_download_configs: `DROP INDEX IF EXISTS market_download_configs_unique_uq`,
  market_download_tasks: `DROP INDEX IF EXISTS market_download_tasks_unique_uq`,
} as const;

const defaultPriceBandItems = [
  { label: "0-499", min: 0, max: 50_000, order: 10 },
  { label: "500-999", min: 50_000, max: 100_000, order: 20 },
  { label: "1000-1999", min: 100_000, max: 200_000, order: 30 },
  { label: "2000-2999", min: 200_000, max: 300_000, order: 40 },
  { label: "3000+", min: 300_000, max: null, order: 50 },
] as const;

async function addMissingColumns(db: MarketSchemaDatabase, table: string, columns: Array<[string, string]>) {
  const info = await db.prepare(`PRAGMA table_info(${table})`).all<{ name: string }>();
  const existing = new Set((info.results ?? []).map((row) => row.name));
  const added = new Set<string>();
  for (const [name, definition] of columns) {
    if (!existing.has(name)) {
      await db.prepare(`ALTER TABLE ${table} ADD COLUMN ${name} ${definition}`).run();
      added.add(name);
    }
  }
  return added;
}

const marketRuntimeSchemaMarker = "market-runtime-schema-v13";

export function marketStandardSkuImagePriceInheritanceSql(
  targetFilter = "1=1",
  sourceFilter = "1=1",
  useImageHashIndex = false,
) {
  const sourceIndex = useImageHashIndex ? " INDEXED BY market_price_snapshots_image_hash_idx" : "";
  const targetIndex = useImageHashIndex ? " INDEXED BY market_price_snapshots_image_hash_idx" : "";
  return `WITH ranked_standard_prices AS MATERIALIZED (
      SELECT source.category, source.scope, source.sku_code, source.ranking_dimension,
        source.image_content_sha256,
        source.confirmed_market_price_cents, source.price_low_cents, source.price_high_cents,
        source.source_job_item_id, source.prompt_version_id,
        ROW_NUMBER() OVER (
          PARTITION BY source.category, source.scope, source.sku_code, source.ranking_dimension, source.image_content_sha256
          ORDER BY datetime(COALESCE(source.confirmed_at, source.updated_at)) DESC,
            source.month DESC, source.id DESC
        ) standard_rank
      FROM market_price_snapshots source${sourceIndex}
      WHERE source.ranking_dimension='SKU'
        AND source.confirmed_market_price_cents IS NOT NULL
        AND source.confirmation_status='confirmed'
        AND source.ai_price_type='标准售价'
        AND source.image_content_sha256<>''
        AND (${sourceFilter})
    ), latest_standard_prices AS MATERIALIZED (
      SELECT * FROM ranked_standard_prices WHERE standard_rank=1
    )
    UPDATE market_price_snapshots AS target${targetIndex}
    SET ai_image_price_cents=inherited.confirmed_market_price_cents,
      ai_price_type='标准售价',
      confirmed_market_price_cents=inherited.confirmed_market_price_cents,
      price_low_cents=COALESCE(inherited.price_low_cents, target.price_low_cents),
      price_high_cents=COALESCE(inherited.price_high_cents, target.price_high_cents),
      confirmation_status='confirmed', confirmed_by='system:history_same_image', confirmed_at=CURRENT_TIMESTAMP,
      source_job_item_id=COALESCE(NULLIF(inherited.source_job_item_id, ''), target.source_job_item_id),
      prompt_version_id=COALESCE(NULLIF(inherited.prompt_version_id, ''), target.prompt_version_id),
      updated_at=CURRENT_TIMESTAMP
    FROM latest_standard_prices inherited
    WHERE target.ranking_dimension='SKU' AND target.image_content_sha256<>'' AND target.confirmed_market_price_cents IS NULL
      AND (${targetFilter})
      AND inherited.category=target.category AND inherited.scope=target.scope
      AND inherited.sku_code=target.sku_code
      AND inherited.ranking_dimension=target.ranking_dimension
      AND inherited.image_content_sha256=target.image_content_sha256`;
}

export async function inheritConfirmedStandardSkuImagePrices(db: MarketSchemaDatabase, targetFilter = "1=1", bindings: unknown[] = []) {
  return db.prepare(marketStandardSkuImagePriceInheritanceSql(targetFilter)).bind(...bindings).run();
}

async function ensureMarketImageCacheJobSchema(db: MarketSchemaDatabase) {
  await db.prepare(marketImageCacheTableStatement).run();
  await db.prepare(marketImageCacheJobTableStatement).run();
  await addMissingColumns(db, "market_image_cache_jobs", imageCacheJobColumns);
  await db.prepare(marketImageCacheJobItemTableStatement).run();
  await db.prepare(marketImageCacheClaimTableStatement).run();
  await addMissingColumns(db, "market_image_cache_claims", imageCacheClaimColumns);
  for (const statement of marketImageCacheCounterTriggerStatements) await db.prepare(statement).run();
  await db.prepare("CREATE INDEX IF NOT EXISTS market_image_cache_jobs_runnable_idx ON market_image_cache_jobs (status, next_run_at, lease_expires_at, updated_at)").run();
  await db.prepare("CREATE INDEX IF NOT EXISTS market_image_cache_jobs_batch_idx ON market_image_cache_jobs (batch_id, updated_at)").run();
  await db.prepare("CREATE INDEX IF NOT EXISTS market_image_cache_job_items_work_idx ON market_image_cache_job_items (job_id, status, source_url)").run();
  await db.prepare("CREATE INDEX IF NOT EXISTS market_image_cache_claims_job_expiry_idx ON market_image_cache_claims (job_id, lease_expires_at, source_url)").run();
  await db.prepare("CREATE INDEX IF NOT EXISTS market_image_cache_fetching_recovery_idx ON market_image_cache (status, updated_at, source_url) WHERE status='fetching'").run();
}

async function hasMarketRuntimeSchemaMarker(db: MarketSchemaDatabase) {
  try {
    const marker = await db.prepare(`SELECT id FROM market_master_audit_logs
      WHERE entity_type='runtime_schema' AND entity_id=? LIMIT 1`).bind(marketRuntimeSchemaMarker).first<{ id: string }>();
    return Boolean(marker);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("no such table") || message.includes("does not exist")) return false;
    throw error;
  }
}

async function needsLegacyMarketDataUpgrade(db: MarketSchemaDatabase) {
  const row = await db.prepare(`SELECT CASE WHEN
    EXISTS (SELECT 1 FROM market_ranking_entries WHERE ranking_dimension NOT IN ('SKU','SPU') OR ranking_dimension IS NULL OR ranking_dimension='')
    OR EXISTS (
      SELECT 1 FROM market_ranking_entries
      GROUP BY period_start, period_end, category, scope, price_band_filter, ranking_dimension, sku_code
      HAVING COUNT(*)>1 LIMIT 1
    )
    OR EXISTS (
      SELECT 1 FROM market_ranking_entries m
      WHERE substr(m.period_end,1,7)<>'' AND NOT EXISTS (
        SELECT 1 FROM market_price_snapshots ps
        WHERE ps.category=m.category AND ps.scope=m.scope AND ps.sku_code=m.sku_code
          AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
      ) LIMIT 1
    )
    THEN 1 ELSE 0 END needs_upgrade`).first<{ needs_upgrade: number }>();
  return Number(row?.needs_upgrade ?? 0) === 1;
}

async function recordMarketRuntimeSchemaMarker(db: MarketSchemaDatabase) {
  await db.prepare(`INSERT OR IGNORE INTO market_master_audit_logs
    (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
    VALUES (?, 'system', 'system', 'complete_runtime_schema_upgrade', 'runtime_schema', ?, '{}', '{"version":1}')`)
    .bind(marketRuntimeSchemaMarker, marketRuntimeSchemaMarker).run();
}

async function normalizeExistingRankingRows(db: MarketSchemaDatabase, rankingDimensionAdded: boolean) {
  await db.prepare(`
    UPDATE market_ranking_entries
    SET operation_mode = CASE
      WHEN lower(COALESCE(scope, '') || ' ' || COALESCE(raw_json, '')) LIKE '%pop%'
        OR COALESCE(scope, '') || ' ' || COALESCE(raw_json, '') LIKE '%\u5e97\u94fa%'
        OR COALESCE(scope, '') || ' ' || COALESCE(raw_json, '') LIKE '%\u65d7\u8230\u5e97%' THEN 'POP'
      WHEN COALESCE(scope, '') || ' ' || COALESCE(raw_json, '') LIKE '%\u81ea\u8425%' THEN '${selfOperated}'
      ELSE '${unknownMode}'
    END
    WHERE operation_mode IS NULL OR operation_mode = '' OR operation_mode IN ('SKU','SPU','\u5168\u90e8','${unknownMode}')
  `).run();
  await db.prepare(`
    UPDATE market_ranking_entries
    SET ranking_dimension = CASE
      WHEN upper(COALESCE(scope, '') || ' ' || COALESCE(raw_json, '') || ' ' || COALESCE((SELECT file_name FROM market_import_batches b WHERE b.id = last_import_batch_id), '')) LIKE '%SPU%' THEN 'SPU'
      WHEN upper(COALESCE(scope, '') || ' ' || COALESCE(raw_json, '') || ' ' || COALESCE((SELECT file_name FROM market_import_batches b WHERE b.id = last_import_batch_id), '')) LIKE '%SKU%' THEN 'SKU'
      ELSE 'SKU'
    END
    ${rankingDimensionAdded ? "" : "WHERE ranking_dimension IS NULL OR ranking_dimension = '' OR ranking_dimension NOT IN ('SKU','SPU')"}
  `).run();
}

async function removeCanonicalDuplicates(db: MarketSchemaDatabase) {
  await db.prepare(`
    DELETE FROM market_ranking_entries
    WHERE id IN (
      SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
          PARTITION BY period_start, period_end, category, scope, price_band_filter, ranking_dimension, sku_code
          ORDER BY datetime(updated_at) DESC, id DESC
        ) AS rn
        FROM market_ranking_entries
      ) ranked
      WHERE rn > 1
    )
  `).run();
}

async function backfillPriceSnapshots(db: MarketSchemaDatabase) {
  await db.prepare(`
    UPDATE market_price_snapshots
    SET scope = COALESCE((
      SELECT m.scope FROM market_ranking_entries m
      WHERE m.category = market_price_snapshots.category
        AND m.sku_code = market_price_snapshots.sku_code
        AND m.ranking_dimension = market_price_snapshots.ranking_dimension
        AND substr(m.period_end, 1, 7) = market_price_snapshots.month
      ORDER BY m.id DESC LIMIT 1
    ), '')
    WHERE scope = ''
  `).run();
  await db.prepare(`
    INSERT OR IGNORE INTO market_price_snapshots (
      id, category, scope, sku_code, ranking_dimension, month, source_price_cents,
      average_transaction_price_cents, price_low_cents, price_high_cents,
      image_content_sha256, image_url, confirmation_status, source_import_batch_id, updated_at
    )
    SELECT
      'market-price-backfill-v2-' ||
        length(m.category) || ':' || m.category || '|' ||
        length(m.scope) || ':' || m.scope || '|' ||
        length(m.ranking_dimension) || ':' || m.ranking_dimension || '|' ||
        length(m.sku_code) || ':' || m.sku_code || '|' || substr(m.period_end, 1, 7),
      m.category,
      m.scope,
      m.sku_code,
      m.ranking_dimension,
      substr(m.period_end, 1, 7),
      m.price_cents,
      CASE WHEN m.quantity > 0 THEN CAST(ROUND(m.gmv_cents * 1.0 / m.quantity) AS INTEGER) ELSE NULL END,
      m.price_low_cents,
      m.price_high_cents,
      COALESCE((SELECT content_sha256 FROM market_image_cache mic WHERE mic.source_url = m.image_url AND mic.status = 'ready' LIMIT 1), ''),
      m.image_url,
      CASE WHEN m.price_cents IS NULL THEN 'missing' ELSE 'source_table' END,
      m.last_import_batch_id,
      CURRENT_TIMESTAMP
    FROM (
      SELECT ranked.*
      FROM (
        SELECT source.*,
          ROW_NUMBER() OVER (
            PARTITION BY source.category, source.scope, source.sku_code, source.ranking_dimension, substr(source.period_end, 1, 7)
            ORDER BY source.period_end DESC, source.period_start DESC, datetime(source.updated_at) DESC, source.id DESC
          ) AS snapshot_rn
        FROM market_ranking_entries source
        WHERE substr(source.period_end, 1, 7) <> ''
      ) ranked
      WHERE ranked.snapshot_rn = 1
    ) m
    WHERE substr(m.period_end, 1, 7) <> ''
      AND NOT EXISTS (
        SELECT 1 FROM market_price_snapshots ps
        WHERE ps.category = m.category
          AND ps.scope = m.scope
          AND ps.sku_code = m.sku_code
          AND ps.ranking_dimension = m.ranking_dimension
          AND ps.month = substr(m.period_end, 1, 7)
      )
  `).run();
  await db.prepare(`
    UPDATE market_price_snapshots
    SET
      source_price_cents = (
        SELECT m.price_cents FROM market_ranking_entries m
        WHERE m.category = market_price_snapshots.category
          AND m.scope = market_price_snapshots.scope
          AND m.sku_code = market_price_snapshots.sku_code
          AND m.ranking_dimension = market_price_snapshots.ranking_dimension
          AND substr(m.period_end, 1, 7) = market_price_snapshots.month
        ORDER BY m.period_end DESC, m.id DESC LIMIT 1
      ),
      average_transaction_price_cents = (
        SELECT CASE WHEN m.quantity > 0 THEN CAST(ROUND(m.gmv_cents * 1.0 / m.quantity) AS INTEGER) ELSE NULL END
        FROM market_ranking_entries m
        WHERE m.category = market_price_snapshots.category
          AND m.scope = market_price_snapshots.scope
          AND m.sku_code = market_price_snapshots.sku_code
          AND m.ranking_dimension = market_price_snapshots.ranking_dimension
          AND substr(m.period_end, 1, 7) = market_price_snapshots.month
        ORDER BY m.period_end DESC, m.id DESC LIMIT 1
      ),
      image_content_sha256 = COALESCE(NULLIF(image_content_sha256, ''), (
        SELECT mic.content_sha256 FROM market_ranking_entries m
        JOIN market_image_cache mic ON mic.source_url = m.image_url AND mic.status = 'ready'
        WHERE m.category = market_price_snapshots.category
          AND m.scope = market_price_snapshots.scope
          AND m.sku_code = market_price_snapshots.sku_code
          AND m.ranking_dimension = market_price_snapshots.ranking_dimension
          AND substr(m.period_end, 1, 7) = market_price_snapshots.month
        ORDER BY m.period_end DESC, m.id DESC LIMIT 1
      ), ''),
      image_url = COALESCE(NULLIF(image_url, ''), (
        SELECT m.image_url FROM market_ranking_entries m
        WHERE m.category = market_price_snapshots.category
          AND m.scope = market_price_snapshots.scope
          AND m.sku_code = market_price_snapshots.sku_code
          AND m.ranking_dimension = market_price_snapshots.ranking_dimension
          AND substr(m.period_end, 1, 7) = market_price_snapshots.month
        ORDER BY m.period_end DESC, m.id DESC LIMIT 1
      ), ''),
      confirmation_status = CASE
        WHEN confirmed_market_price_cents IS NOT NULL THEN confirmation_status
        WHEN source_price_cents IS NULL THEN 'missing'
        ELSE 'source_table'
      END,
      updated_at = CURRENT_TIMESTAMP
    WHERE EXISTS (
      SELECT 1 FROM market_ranking_entries m
      WHERE m.category = market_price_snapshots.category
        AND m.scope = market_price_snapshots.scope
        AND m.sku_code = market_price_snapshots.sku_code
        AND m.ranking_dimension = market_price_snapshots.ranking_dimension
        AND substr(m.period_end, 1, 7) = market_price_snapshots.month
    )
  `).run();
}

async function priceBandVersionHasItems(db: MarketSchemaDatabase, versionId: string) {
  const row = await db.prepare("SELECT id FROM market_price_band_items WHERE version_id=? LIMIT 1")
    .bind(versionId).first<{ id: string }>();
  return Boolean(row);
}

async function ensureDefaultPriceBandItems(db: MarketSchemaDatabase, versionId: string) {
  for (const item of defaultPriceBandItems) {
    const exact = () => db.prepare(`SELECT id FROM market_price_band_items
      WHERE version_id=? AND label=? AND min_cents IS ? AND max_cents IS ? AND sort_order=? LIMIT 1`)
      .bind(versionId, item.label, item.min, item.max, item.order).first<{ id: string }>();
    if (await exact()) continue;
    const itemId = versionId === "market-price-band-default-v1"
      ? `${versionId}-${item.order}`
      : `${versionId}-default-${item.order}`;
    await db.prepare(`INSERT OR IGNORE INTO market_price_band_items
      (id, version_id, label, min_cents, max_cents, sort_order)
      VALUES (?, ?, ?, ?, ?, ?)`)
      .bind(itemId, versionId, item.label, item.min, item.max, item.order).run();
    if (!await exact()) throw new Error(`默认价格带项恢复失败：${item.label}`);
  }
}

async function seedDefaultPriceBands(db: MarketSchemaDatabase) {
  const fixedDefaultId = "market-price-band-default-v1";
  const published = await db.prepare(`SELECT id FROM market_price_band_versions
    WHERE category='*' AND status='published'
    ORDER BY effective_from DESC, version DESC, COALESCE(published_at, '') DESC, id DESC LIMIT 1`)
    .first<{ id: string }>();
  if (published) {
    if (published.id === fixedDefaultId || !await priceBandVersionHasItems(db, published.id)) {
      await ensureDefaultPriceBandItems(db, published.id);
    }
    const usable = await db.prepare(`SELECT id FROM market_price_band_versions
      WHERE id=? AND category='*' AND status='published'
        AND EXISTS (SELECT 1 FROM market_price_band_items WHERE version_id=market_price_band_versions.id)
      LIMIT 1`).bind(published.id).first<{ id: string }>();
    if (!usable) throw new Error("全局价格带已发布版本不可用");
    return;
  }

  const fixedDefault = await db.prepare("SELECT id FROM market_price_band_versions WHERE id=? AND category='*' LIMIT 1")
    .bind(fixedDefaultId).first<{ id: string }>();
  let targetId = fixedDefault?.id ?? "";
  if (!targetId) {
    const available = await db.prepare(`SELECT id FROM market_price_band_versions
      WHERE category='*'
      ORDER BY CASE WHEN EXISTS (SELECT 1 FROM market_price_band_items i WHERE i.version_id=market_price_band_versions.id) THEN 0 ELSE 1 END,
        effective_from DESC, version DESC, COALESCE(published_at, '') DESC, id DESC LIMIT 1`)
      .first<{ id: string }>();
    targetId = available?.id ?? "";
  }
  if (!targetId) {
    const next = await db.prepare("SELECT COALESCE(MAX(version),0)+1 version FROM market_price_band_versions WHERE category='*'")
      .first<{ version: number }>();
    const version = Number(next?.version ?? 1);
    const fixedIdCollision = await db.prepare("SELECT id FROM market_price_band_versions WHERE id=? LIMIT 1")
      .bind(fixedDefaultId).first<{ id: string }>();
    targetId = fixedIdCollision ? `market-price-band-default-recovery-${crypto.randomUUID()}` : fixedDefaultId;
    await db.prepare(`INSERT INTO market_price_band_versions
      (id, category, version, status, effective_from, created_by, published_by, published_at, note)
      VALUES (?, '*', ?, 'draft', '1970-01-01', 'system', '', NULL, 'default seeded config')`)
      .bind(targetId, version).run();
  }

  if (targetId === fixedDefaultId || !await priceBandVersionHasItems(db, targetId)) {
    await ensureDefaultPriceBandItems(db, targetId);
  }
  await db.prepare(`UPDATE market_price_band_versions
    SET status='published', published_by='system', published_at=CURRENT_TIMESTAMP
    WHERE id=? AND category='*' AND status<>'published'`).bind(targetId).run();
  const restored = await db.prepare(`SELECT id FROM market_price_band_versions
    WHERE id=? AND category='*' AND status='published'
      AND EXISTS (SELECT 1 FROM market_price_band_items WHERE version_id=market_price_band_versions.id)
    LIMIT 1`).bind(targetId).first<{ id: string }>();
  if (!restored) throw new Error("全局默认价格带恢复失败");
}

async function normalizePublishedPriceBandVersions(db: MarketSchemaDatabase) {
  await db.prepare(`WITH ranked_published AS (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY category
        ORDER BY effective_from DESC, version DESC, COALESCE(published_at, '') DESC, id DESC
      ) AS published_rank
      FROM market_price_band_versions
      WHERE status='published'
    )
    UPDATE market_price_band_versions
    SET status='archived'
    WHERE id IN (SELECT id FROM ranked_published WHERE published_rank>1)`).run();
}

export async function ensureMarketSchemaCore(db: MarketSchemaDatabase): Promise<void> {
  await ensureMarketImageCacheJobSchema(db);
  const fastMarker = await hasMarketRuntimeSchemaMarker(db);
  if (fastMarker) {
    for (const statement of marketNetshopProjectionStatements) await db.prepare(statement).run();
    for (const statement of marketEffectiveMetricsCacheStatements) await db.prepare(statement).run();
    for (const statement of marketMonthlySummaryCacheStatements) await db.prepare(statement).run();
    for (const statement of marketMonthlySummaryCacheIndexStatements) await db.prepare(statement).run();
    for (const statement of marketOverviewResponseCacheStatements) await db.prepare(statement).run();
    await db.prepare(marketImportIdentityRefreshKeysTableStatement).run();
    await db.prepare(marketSystemKpiCacheStateSeedStatement).run();
    await addMissingColumns(db, "market_system_kpi_cache_control", systemKpiCacheControlColumns);
    await db.prepare(marketSystemKpiCacheControlSeedStatement).run();
    await db.prepare("CREATE INDEX IF NOT EXISTS market_overview_response_cache_updated_idx ON market_overview_response_cache (updated_at)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS market_image_cache_updated_idx ON market_image_cache (updated_at)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS market_entries_batch_image_idx ON market_ranking_entries (last_import_batch_id, image_url) WHERE image_url<>''").run();
    for (const statement of marketImageCachePropagationIndexStatements) await db.prepare(statement).run();
    await db.prepare(`CREATE TABLE IF NOT EXISTS market_master_identities (
      category TEXT NOT NULL, scope TEXT NOT NULL, ranking_dimension TEXT NOT NULL,
      sku_code TEXT NOT NULL, latest_entry_id INTEGER NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (category, scope, ranking_dimension, sku_code), UNIQUE (latest_entry_id)
    )`).run();
    await db.prepare("CREATE UNIQUE INDEX IF NOT EXISTS market_master_identities_entry_uq ON market_master_identities (latest_entry_id)").run();
    await db.prepare(`CREATE TABLE IF NOT EXISTS market_subcategory_taxonomy (
      id TEXT PRIMARY KEY NOT NULL, category TEXT NOT NULL, subcategory TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active', sort_order INTEGER NOT NULL DEFAULT 0,
      created_by TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )`).run();
    await db.prepare("CREATE UNIQUE INDEX IF NOT EXISTS market_subcategory_taxonomy_category_name_uq ON market_subcategory_taxonomy (category, subcategory)").run();
    await db.prepare("CREATE INDEX IF NOT EXISTS market_subcategory_taxonomy_lookup_idx ON market_subcategory_taxonomy (category, status, sort_order)").run();
    await ensureMarketSubcategoryTaxonomyData(db);
    await normalizePublishedPriceBandVersions(db);
    await seedDefaultPriceBands(db);
    await db.prepare(marketPublishedPriceBandUniqueIndexStatement).run();
    return;
  }
  // D1/SQLite validates views and trigger dependencies when a statement is
  // prepared. Preparing the whole fresh-schema list eagerly can therefore
  // reference a table that has not run yet. The statements are idempotent, so
  // install them in dependency order and let a later attempt safely continue
  // if an infrastructure failure interrupts initialization.
  for (const statement of marketBaseSchemaStatements) await db.prepare(statement).run();
  await db.prepare(marketSystemKpiCacheStateSeedStatement).run();
  await addMissingColumns(db, "market_system_kpi_cache_control", systemKpiCacheControlColumns);
  await db.prepare(marketSystemKpiCacheControlSeedStatement).run();
  const changedTables = new Set<string>();
  const addedColumns = new Map<string, Set<string>>();
  for (const [table, columns] of [
    ["market_import_batches", importBatchColumns],
    ["market_ranking_entries", rankingEntryColumns],
    ["market_price_snapshots", priceSnapshotColumns],
    ["market_download_configs", downloadConfigColumns],
    ["market_download_tasks", downloadTaskColumns],
  ] as const) {
    const added = await addMissingColumns(db, table, columns);
    if (added.size) {
      changedTables.add(table);
      addedColumns.set(table, added);
    }
  }
  const alreadyUpgraded = await hasMarketRuntimeSchemaMarker(db);
  const needsDataUpgrade = changedTables.size > 0 || await needsLegacyMarketDataUpgrade(db);
  if (needsDataUpgrade) {
    await db.prepare(`UPDATE market_ranking_entries SET
      source_brand=brand,
      source_operation_mode=operation_mode,
      source_subcategory=subcategory
      WHERE source_brand='' AND source_operation_mode='' AND source_subcategory=''`).run();
    await normalizeExistingRankingRows(db, Boolean(addedColumns.get("market_ranking_entries")?.has("ranking_dimension")));
    await removeCanonicalDuplicates(db);
    const preUpgradeIndexes = Object.entries(marketPreUpgradeIndexStatements)
      .filter(([table]) => changedTables.has(table))
      .map(([, statement]) => db.prepare(statement));
    if (preUpgradeIndexes.length) await db.batch(preUpgradeIndexes);
    await backfillPriceSnapshots(db);
  }
  await normalizePublishedPriceBandVersions(db);
  await seedDefaultPriceBands(db);
  for (const statement of marketPostUpgradeIndexStatements) await db.prepare(statement).run();
  await db.prepare(`DROP INDEX IF EXISTS market_entries_canonical_uq`).run();
  await ensureMarketSubcategoryTaxonomyData(db);
  if (!alreadyUpgraded) await inheritConfirmedStandardSkuImagePrices(db);
  if (!alreadyUpgraded) await recordMarketRuntimeSchemaMarker(db);
}

const marketSchemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export function ensureMarketSchemaCached(db: MarketSchemaDatabase): Promise<void> {
  const key = db as object;
  const ready = marketSchemaReadyByDatabase.get(key);
  if (ready) return ready;
  const setup = ensureMarketSchemaCore(db).catch((error: unknown) => {
    marketSchemaReadyByDatabase.delete(key);
    throw error;
  });
  marketSchemaReadyByDatabase.set(key, setup);
  return setup;
}

type OfficialPriceBandSqlContext = {
  confirmationStatusSql?: string;
  aiPriceTypeSql?: string;
  categorySql?: string;
  periodEndSql?: string;
  fallbackPriceSql?: string;
};

export function officialPriceBandSql(
  priceSql = "official_market_price_cents",
  context: OfficialPriceBandSqlContext = {},
) {
  const confirmationStatusSql = context.confirmationStatusSql ?? "ps.confirmation_status";
  const aiPriceTypeSql = context.aiPriceTypeSql ?? "ps.ai_price_type";
  const categorySql = context.categorySql ?? "m.category";
  const periodEndSql = context.periodEndSql ?? "m.period_end";
  const fallbackPriceSql = context.fallbackPriceSql;
  const effectivePriceSql = fallbackPriceSql ? `COALESCE(${priceSql}, ${fallbackPriceSql})` : priceSql;
  return `CASE
    WHEN ${effectivePriceSql} IS NULL
      OR (${priceSql} IS NOT NULL AND (COALESCE(${confirmationStatusSql}, '') <> 'confirmed'
        OR COALESCE(${aiPriceTypeSql}, '') IN (char(23450,37329), char(20998,26399,37329,39069), char(26080,27861,21028,26029)))) THEN '${unknownPriceBand}'
    ELSE COALESCE((
      SELECT pbi.label
      FROM market_price_band_versions pbv
      JOIN market_price_band_items pbi ON pbi.version_id = pbv.id
      WHERE pbv.status = 'published'
        AND pbv.category IN ('*', ${categorySql})
        AND pbv.effective_from <= ${periodEndSql}
        AND ${effectivePriceSql} >= COALESCE(pbi.min_cents, -9223372036854775808)
        AND (${effectivePriceSql} < pbi.max_cents OR pbi.max_cents IS NULL)
      ORDER BY CASE WHEN pbv.category = '*' THEN 1 ELSE 0 END, pbv.effective_from DESC, pbv.version DESC, pbi.sort_order
      LIMIT 1
    ), '${unknownPriceBand}')
  END`;
}
