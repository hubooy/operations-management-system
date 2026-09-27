CREATE TABLE IF NOT EXISTS ai_space_model_profiles (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  protocol TEXT NOT NULL DEFAULT 'openai_images' CHECK (protocol = 'openai_images'),
  model_name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  api_key_encrypted TEXT NOT NULL DEFAULT '',
  api_key_suffix TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'enabled' CHECK (status IN ('enabled', 'disabled')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  timeout_ms INTEGER NOT NULL DEFAULT 90000 CHECK (timeout_ms BETWEEN 3000 AND 120000),
  last_success_result TEXT,
  last_success_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_model_profiles_status_updated_idx
  ON ai_space_model_profiles (status, updated_at DESC, id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_templates (
  id TEXT PRIMARY KEY NOT NULL,
  scene TEXT NOT NULL CHECK (scene IN ('product_main', 'product_detail', 'promotion')),
  name TEXT NOT NULL,
  prompt_template TEXT NOT NULL,
  size TEXT NOT NULL DEFAULT '1024x1024' CHECK (size IN ('1024x1024', '1024x1536', '1536x1024')),
  model_profile_id TEXT REFERENCES ai_space_model_profiles(id) ON DELETE RESTRICT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
  updated_by TEXT NOT NULL DEFAULT 'system',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (is_default = 0 OR is_enabled = 1)
);--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS ai_space_templates_default_scene_uq
  ON ai_space_templates (scene)
  WHERE is_default = 1 AND is_enabled = 1;--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_templates_scene_enabled_idx
  ON ai_space_templates (scene, is_enabled, is_default DESC, updated_at DESC, id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  client_request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  scope_json TEXT NOT NULL,
  scene TEXT NOT NULL CHECK (scene IN ('product_main', 'product_detail', 'promotion')),
  template_id TEXT NOT NULL REFERENCES ai_space_templates(id) ON DELETE RESTRICT,
  template_name TEXT NOT NULL,
  template_version INTEGER NOT NULL,
  model_profile_id TEXT NOT NULL REFERENCES ai_space_model_profiles(id) ON DELETE RESTRICT,
  model_profile_name TEXT NOT NULL,
  model_profile_version INTEGER NOT NULL,
  model_name TEXT NOT NULL,
  product_name TEXT NOT NULL,
  brand TEXT NOT NULL DEFAULT '',
  sku TEXT NOT NULL DEFAULT '',
  selling_points TEXT NOT NULL DEFAULT '',
  final_prompt TEXT NOT NULL,
  prompt_digest TEXT NOT NULL,
  size TEXT NOT NULL CHECK (size IN ('1024x1024', '1024x1536', '1536x1024')),
  requested_count INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 4),
  succeeded_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  cancelled_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')),
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (owner_email, client_request_id)
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_jobs_owner_created_idx
  ON ai_space_jobs (owner_email, created_at DESC, id);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_jobs_status_created_idx
  ON ai_space_jobs (status, cancel_requested, created_at, id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_job_items (
  id TEXT PRIMARY KEY NOT NULL,
  job_id TEXT NOT NULL REFERENCES ai_space_jobs(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 4),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  lease_token TEXT NOT NULL DEFAULT '',
  lease_epoch INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TEXT,
  dispatch_started_at TEXT,
  pending_object_key TEXT NOT NULL DEFAULT '',
  provider_request_id TEXT NOT NULL DEFAULT '',
  asset_id TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (job_id, ordinal)
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_job_items_runnable_idx
  ON ai_space_job_items (status, created_at, job_id, ordinal);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_job_items_job_idx
  ON ai_space_job_items (job_id, ordinal);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_assets (
  id TEXT PRIMARY KEY NOT NULL,
  job_id TEXT NOT NULL REFERENCES ai_space_jobs(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL UNIQUE REFERENCES ai_space_job_items(id) ON DELETE CASCADE,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  scope_json TEXT NOT NULL,
  scene TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL,
  mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png', 'image/jpeg', 'image/webp')),
  byte_size INTEGER NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_assets_owner_created_idx
  ON ai_space_assets (owner_email, created_at DESC, id);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_assets_job_idx
  ON ai_space_assets (job_id, created_at, id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_asset_favorites (
  asset_id TEXT NOT NULL REFERENCES ai_space_assets(id) ON DELETE CASCADE,
  actor_email TEXT NOT NULL COLLATE NOCASE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (asset_id, actor_email)
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_asset_favorites_actor_created_idx
  ON ai_space_asset_favorites (actor_email, created_at DESC, asset_id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_asset_cleanup_queue (
  object_key TEXT PRIMARY KEY NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_schema_upgrades (
  id TEXT PRIMARY KEY NOT NULL,
  completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_admin_audits (
  id TEXT PRIMARY KEY NOT NULL,
  actor_email TEXT NOT NULL COLLATE NOCASE,
  actor_role TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('upsert_profile', 'delete_profile', 'upsert_template', 'delete_template')),
  entity_type TEXT NOT NULL CHECK (entity_type IN ('model_profile', 'template')),
  entity_id TEXT NOT NULL,
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_admin_audits_entity_created_idx
  ON ai_space_admin_audits (entity_type, entity_id, created_at DESC, id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_dispatch_receipts (
  id TEXT PRIMARY KEY NOT NULL,
  item_id TEXT NOT NULL UNIQUE,
  job_id TEXT NOT NULL,
  owner_email TEXT NOT NULL COLLATE NOCASE,
  actor_role TEXT NOT NULL,
  model_profile_id TEXT NOT NULL,
  model_profile_version INTEGER NOT NULL,
  model_name TEXT NOT NULL,
  scene TEXT NOT NULL,
  size TEXT NOT NULL,
  prompt_digest TEXT NOT NULL,
  dispatched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_dispatch_receipts_owner_day_idx
  ON ai_space_dispatch_receipts (owner_email, dispatched_at, id);--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ai_space_dispatch_receipts_profile_day_idx
  ON ai_space_dispatch_receipts (model_profile_id, dispatched_at, id);--> statement-breakpoint

CREATE TABLE IF NOT EXISTS ai_space_dispatch_results (
  dispatch_id TEXT PRIMARY KEY NOT NULL REFERENCES ai_space_dispatch_receipts(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
  provider_request_id TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL DEFAULT '',
  usage_json TEXT NOT NULL DEFAULT '{}',
  estimated_cost_cents INTEGER,
  price_version TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint

INSERT INTO ai_space_templates (
  id, scene, name, prompt_template, size, version, is_enabled, is_default, updated_by
) VALUES
  ('ai-space-template-product-main', 'product_main', '电商商品主图',
   '为{brand}{product_name}（SKU：{sku}）生成专业电商商品主图。核心卖点：{selling_points}。主体完整清晰、真实材质、纯净浅色背景、商业摄影光线，不添加价格、销量、认证、平台标识或无法核验的文字。',
   '1024x1024', 1, 1, 1, 'system_seed'),
  ('ai-space-template-product-detail', 'product_detail', '商品卖点详情图',
   '为{brand}{product_name}（SKU：{sku}）生成电商详情页视觉。突出这些可核验卖点：{selling_points}。采用结构清晰的产品场景与细节特写，不伪造参数、认证、人物背书、价格或销量，不生成难以辨认的文字。',
   '1024x1536', 1, 1, 1, 'system_seed'),
  ('ai-space-template-promotion', 'promotion', '活动推广视觉',
   '为{brand}{product_name}（SKU：{sku}）生成有节奏感的电商活动推广视觉。围绕卖点：{selling_points}。保留清晰的商品主体与安全留白，不添加未经提供的折扣、价格、销量、认证、平台标识或真人代言。',
   '1536x1024', 1, 1, 1, 'system_seed')
ON CONFLICT DO NOTHING;
