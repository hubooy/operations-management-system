import type { AppPrincipal } from "@/tests/legacy/auth/authorization";
import { resolveAiBackgroundPrincipal } from "@/lib/ai/background-principal";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { decryptSecret, encryptSecret } from "@/lib/ai/crypto";
import {
  aiScopeSnapshotAccessSql,
  serializeAiConversationScope,
} from "@/tests/legacy/ai/conversation-scope";
import {
  normalizeAiModelEndpointForStorage,
  redactAiModelEndpointUrl,
  resolveAiImageGenerationEndpointUrl,
  loadAiEndpointSecurityContext,
} from "@/lib/ai/endpoint-security";
import { PublicApiError } from "@/lib/http/api-error";
import { getD1Database, type D1Database } from "@/lib/database/d1";

export const aiSpaceScenes = ["product_main", "product_detail", "promotion"] as const;
export type AiSpaceScene = (typeof aiSpaceScenes)[number];
export const aiSpaceSizes = ["1024x1024", "1024x1536", "1536x1024"] as const;
export type AiSpaceSize = (typeof aiSpaceSizes)[number];
export const aiSpaceProfileStatuses = ["enabled", "disabled"] as const;
export type AiSpaceProfileStatus = (typeof aiSpaceProfileStatuses)[number];
export const aiSpaceJobStatuses = ["queued", "running", "succeeded", "partial", "failed", "cancelled"] as const;
export type AiSpaceJobStatus = (typeof aiSpaceJobStatuses)[number];

export const AI_SPACE_LIMITS = {
  minimumImages: 1,
  maximumImages: 4,
  maximumActiveJobsPerOwner: 5,
  maximumActiveJobsGlobal: 20,
  maximumDailyImagesPerOwner: 40,
  maximumJobsPageSize: 50,
  maximumAssetsPageSize: 60,
  maximumPromptCharacters: 4_000,
  maximumImageBytes: 6 * 1024 * 1024,
  maximumProviderResponseBytes: 9 * 1024 * 1024,
  maximumImagePixels: 1024 * 1536,
  maximumDailyDispatchesGlobal: 200,
  maximumDailyDispatchesPerProfile: 100,
  leaseSeconds: 360,
} as const;

export const AI_SPACE_SCENE_META = [
  { id: "product_main", label: "商品主图", description: "纯净商品主体与电商摄影构图", defaultSize: "1024x1024" },
  { id: "product_detail", label: "卖点详情", description: "突出可核验卖点与产品细节", defaultSize: "1024x1536" },
  { id: "promotion", label: "活动视觉", description: "保留商品主体与活动文案安全留白", defaultSize: "1536x1024" },
] as const satisfies ReadonlyArray<{ id: AiSpaceScene; label: string; description: string; defaultSize: AiSpaceSize }>;

export type AiSpaceModelProfile = {
  id: string;
  name: string;
  protocol: "openai_images";
  modelName: string;
  baseUrl: string;
  apiKeySuffix: string;
  status: AiSpaceProfileStatus;
  version: number;
  timeoutMs: number;
  lastSuccessResult: string | null;
  lastSuccessAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AiSpaceTemplate = {
  id: string;
  scene: AiSpaceScene;
  name: string;
  promptTemplate: string;
  size: AiSpaceSize;
  modelProfileId: string | null;
  version: number;
  isEnabled: boolean;
  isDefault: boolean;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

export type AiSpaceAsset = {
  id: string;
  jobId: string;
  itemId: string;
  scene: AiSpaceScene;
  productName: string;
  brand: string;
  sku: string;
  mimeType: "image/png" | "image/jpeg" | "image/webp";
  byteSize: number;
  width: number;
  height: number;
  favorite: boolean;
  generatedByAi: true;
  reviewRequired: true;
  contentUrl: string;
  createdAt: string;
};

export type AiSpaceJobItem = {
  id: string;
  ordinal: number;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  errorCode: string;
  errorMessage: string;
  durationMs: number | null;
  asset: AiSpaceAsset | null;
};

export type AiSpaceJob = {
  id: string;
  clientRequestId: string;
  scene: AiSpaceScene;
  templateId: string;
  templateName: string;
  templateVersion: number;
  modelProfileId: string;
  modelProfileName: string;
  modelProfileVersion: number;
  modelName: string;
  productName: string;
  brand: string;
  sku: string;
  sellingPoints: string;
  finalPrompt: string;
  size: AiSpaceSize;
  requestedCount: number;
  succeededCount: number;
  failedCount: number;
  cancelledCount: number;
  status: AiSpaceJobStatus;
  cancelRequested: boolean;
  errorCode: string;
  errorMessage: string;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
  items: AiSpaceJobItem[];
};

type ProfileRow = {
  id: string;
  name: string;
  protocol: string;
  model_name: string;
  base_url: string;
  api_key_encrypted: string;
  api_key_suffix: string;
  status: string;
  version: number;
  timeout_ms: number;
  last_success_result: string | null;
  last_success_at: string | null;
  created_at: string;
  updated_at: string;
};

type TemplateRow = {
  id: string;
  scene: string;
  name: string;
  prompt_template: string;
  size: string;
  model_profile_id: string | null;
  version: number;
  is_enabled: number;
  is_default: number;
  updated_by: string;
  created_at: string;
  updated_at: string;
};

type JobRow = {
  id: string;
  client_request_id: string;
  request_digest: string;
  owner_email: string;
  scope_json: string;
  scene: string;
  template_id: string;
  template_name: string;
  template_version: number;
  model_profile_id: string;
  model_profile_name: string;
  model_profile_version: number;
  model_name: string;
  product_name: string;
  brand: string;
  sku: string;
  selling_points: string;
  final_prompt: string;
  prompt_digest: string;
  size: string;
  requested_count: number;
  succeeded_count: number;
  failed_count: number;
  cancelled_count: number;
  status: string;
  cancel_requested: number;
  error_code: string;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

type ItemRow = {
  id: string;
  job_id: string;
  ordinal: number;
  status: string;
  lease_token?: string;
  lease_epoch?: number;
  error_code: string;
  error_message: string;
  duration_ms: number | null;
};

type AssetRow = {
  id: string;
  job_id: string;
  item_id: string;
  owner_email: string;
  scope_json: string;
  scene: string;
  object_key: string;
  content_sha256: string;
  mime_type: string;
  byte_size: number;
  width: number;
  height: number;
  product_name?: string;
  brand?: string;
  sku?: string;
  favorite?: number;
  created_at: string;
};

const profileColumns = `id, name, protocol, model_name, base_url, api_key_encrypted, api_key_suffix,
  status, version, timeout_ms, last_success_result, last_success_at, created_at, updated_at`;
const templateColumns = `id, scene, name, prompt_template, size, model_profile_id, version,
  is_enabled, is_default, updated_by, created_at, updated_at`;
const jobColumns = `id, client_request_id, request_digest, owner_email, scope_json, scene,
  template_id, template_name, template_version, model_profile_id, model_profile_name, model_profile_version, model_name,
  product_name, brand, sku, selling_points, final_prompt, prompt_digest, size, requested_count,
  succeeded_count, failed_count, cancelled_count, status, cancel_requested, error_code, error_message,
  started_at, completed_at, created_at, updated_at`;
const qualifiedJobColumns = jobColumns.split(",").map((column) => `j.${column.trim()}`).join(", ");

const AI_SPACE_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS ai_space_model_profiles (
    id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'openai_images' CHECK (protocol = 'openai_images'),
    model_name TEXT NOT NULL, base_url TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL DEFAULT '', api_key_suffix TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'enabled' CHECK (status IN ('enabled', 'disabled')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    timeout_ms INTEGER NOT NULL DEFAULT 90000 CHECK (timeout_ms BETWEEN 3000 AND 120000),
    last_success_result TEXT, last_success_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_model_profiles_status_updated_idx
    ON ai_space_model_profiles (status, updated_at DESC, id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_templates (
    id TEXT PRIMARY KEY NOT NULL,
    scene TEXT NOT NULL CHECK (scene IN ('product_main', 'product_detail', 'promotion')),
    name TEXT NOT NULL, prompt_template TEXT NOT NULL,
    size TEXT NOT NULL DEFAULT '1024x1024' CHECK (size IN ('1024x1024', '1024x1536', '1536x1024')),
    model_profile_id TEXT REFERENCES ai_space_model_profiles(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    updated_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (is_default = 0 OR is_enabled = 1)
  )`,
  `CREATE UNIQUE INDEX IF NOT EXISTS ai_space_templates_default_scene_uq
    ON ai_space_templates (scene) WHERE is_default = 1 AND is_enabled = 1`,
  `CREATE INDEX IF NOT EXISTS ai_space_templates_scene_enabled_idx
    ON ai_space_templates (scene, is_enabled, is_default DESC, updated_at DESC, id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_jobs (
    id TEXT PRIMARY KEY NOT NULL, client_request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE, scope_json TEXT NOT NULL,
    scene TEXT NOT NULL CHECK (scene IN ('product_main', 'product_detail', 'promotion')),
    template_id TEXT NOT NULL REFERENCES ai_space_templates(id) ON DELETE RESTRICT,
    template_name TEXT NOT NULL, template_version INTEGER NOT NULL,
    model_profile_id TEXT NOT NULL REFERENCES ai_space_model_profiles(id) ON DELETE RESTRICT,
    model_profile_name TEXT NOT NULL, model_profile_version INTEGER NOT NULL, model_name TEXT NOT NULL,
    product_name TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', sku TEXT NOT NULL DEFAULT '',
    selling_points TEXT NOT NULL DEFAULT '', final_prompt TEXT NOT NULL, prompt_digest TEXT NOT NULL,
    size TEXT NOT NULL CHECK (size IN ('1024x1024', '1024x1536', '1536x1024')),
    requested_count INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 4),
    succeeded_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
    cancelled_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','partial','failed','cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_email, client_request_id)
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_jobs_owner_created_idx ON ai_space_jobs (owner_email, created_at DESC, id)`,
  `CREATE INDEX IF NOT EXISTS ai_space_jobs_status_created_idx ON ai_space_jobs (status, cancel_requested, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_job_items (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_space_jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 4),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0, lease_token TEXT NOT NULL DEFAULT '', lease_epoch INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT, dispatch_started_at TEXT, pending_object_key TEXT NOT NULL DEFAULT '',
    provider_request_id TEXT NOT NULL DEFAULT '', asset_id TEXT,
    error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', duration_ms INTEGER,
    started_at TEXT, completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, ordinal)
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_job_items_runnable_idx ON ai_space_job_items (status, created_at, job_id, ordinal)`,
  `CREATE INDEX IF NOT EXISTS ai_space_job_items_job_idx ON ai_space_job_items (job_id, ordinal)`,
  `CREATE TABLE IF NOT EXISTS ai_space_assets (
    id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES ai_space_jobs(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL UNIQUE REFERENCES ai_space_job_items(id) ON DELETE CASCADE,
    owner_email TEXT NOT NULL COLLATE NOCASE, scope_json TEXT NOT NULL, scene TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE, content_sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp')),
    byte_size INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_assets_owner_created_idx ON ai_space_assets (owner_email, created_at DESC, id)`,
  `CREATE INDEX IF NOT EXISTS ai_space_assets_job_idx ON ai_space_assets (job_id, created_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_asset_favorites (
    asset_id TEXT NOT NULL REFERENCES ai_space_assets(id) ON DELETE CASCADE,
    actor_email TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(asset_id, actor_email)
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_asset_favorites_actor_created_idx
    ON ai_space_asset_favorites (actor_email, created_at DESC, asset_id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_asset_cleanup_queue (
    object_key TEXT PRIMARY KEY NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS ai_space_schema_upgrades (
    id TEXT PRIMARY KEY NOT NULL, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS ai_space_admin_audits (
    id TEXT PRIMARY KEY NOT NULL, actor_email TEXT NOT NULL COLLATE NOCASE, actor_role TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('upsert_profile','delete_profile','upsert_template','delete_template')),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('model_profile','template')), entity_id TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_admin_audits_entity_created_idx
    ON ai_space_admin_audits (entity_type, entity_id, created_at DESC, id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_dispatch_receipts (
    id TEXT PRIMARY KEY NOT NULL, item_id TEXT NOT NULL UNIQUE, job_id TEXT NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE, actor_role TEXT NOT NULL,
    model_profile_id TEXT NOT NULL, model_profile_version INTEGER NOT NULL, model_name TEXT NOT NULL,
    scene TEXT NOT NULL, size TEXT NOT NULL, prompt_digest TEXT NOT NULL,
    dispatched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_space_dispatch_receipts_owner_day_idx
    ON ai_space_dispatch_receipts (owner_email, dispatched_at, id)`,
  `CREATE INDEX IF NOT EXISTS ai_space_dispatch_receipts_profile_day_idx
    ON ai_space_dispatch_receipts (model_profile_id, dispatched_at, id)`,
  `CREATE TABLE IF NOT EXISTS ai_space_dispatch_results (
    dispatch_id TEXT PRIMARY KEY NOT NULL REFERENCES ai_space_dispatch_receipts(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('succeeded','failed')), provider_request_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '', usage_json TEXT NOT NULL DEFAULT '{}',
    estimated_cost_cents INTEGER, price_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `INSERT INTO ai_space_templates (id, scene, name, prompt_template, size, version, is_enabled, is_default, updated_by)
   VALUES
    ('ai-space-template-product-main','product_main','电商商品主图','为{brand}{product_name}（SKU：{sku}）生成专业电商商品主图。核心卖点：{selling_points}。主体完整清晰、真实材质、纯净浅色背景、商业摄影光线，不添加价格、销量、认证、平台标识或无法核验的文字。','1024x1024',1,1,1,'system_seed'),
    ('ai-space-template-product-detail','product_detail','商品卖点详情图','为{brand}{product_name}（SKU：{sku}）生成电商详情页视觉。突出这些可核验卖点：{selling_points}。采用结构清晰的产品场景与细节特写，不伪造参数、认证、人物背书、价格或销量，不生成难以辨认的文字。','1024x1536',1,1,1,'system_seed'),
    ('ai-space-template-promotion','promotion','活动推广视觉','为{brand}{product_name}（SKU：{sku}）生成有节奏感的电商活动推广视觉。围绕卖点：{selling_points}。保留清晰的商品主体与安全留白，不添加未经提供的折扣、价格、销量、认证、平台标识或真人代言。','1536x1024',1,1,1,'system_seed')
   ON CONFLICT DO NOTHING`,
] as const;

const AI_SPACE_LEGACY_COLUMN_UPGRADES = [
  {
    table: "ai_space_model_profiles",
    column: "version",
    definition: "version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)",
  },
  {
    table: "ai_space_jobs",
    column: "model_profile_version",
    definition: "model_profile_version INTEGER NOT NULL DEFAULT 1",
  },
  {
    table: "ai_space_job_items",
    column: "dispatch_started_at",
    definition: "dispatch_started_at TEXT",
  },
  {
    table: "ai_space_job_items",
    column: "pending_object_key",
    definition: "pending_object_key TEXT NOT NULL DEFAULT ''",
  },
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();
let bucketOverride: R2Bucket | undefined;

export function setAiSpaceBucketForTest(bucket?: R2Bucket) {
  bucketOverride = bucket;
}

async function aiSpaceTableColumns(table: (typeof AI_SPACE_LEGACY_COLUMN_UPGRADES)[number]["table"], db: D1Database) {
  const rows = await db.prepare(`PRAGMA table_info(${table})`).all<{ name: string }>();
  return new Set((rows.results ?? []).map((row) => row.name));
}

async function ensureAiSpaceLegacyColumns(db: D1Database): Promise<void> {
  const migrationId = "legacy_provider_snapshot_v2";
  const completed = await db.prepare("SELECT 1 present FROM ai_space_schema_upgrades WHERE id = ? LIMIT 1")
    .bind(migrationId).first<{ present: number }>();
  if (!completed) {
    // This runs before ALTER. If the process stops after a column was added but
    // before the marker commit, the next isolate repeats the fail-closed step.
    // Thus a DEFAULT 1 cannot accidentally authorize an unversioned old task.
    await db.batch([
      db.prepare(`UPDATE ai_space_job_items SET status = 'failed',
          error_code = 'legacy_profile_snapshot_missing',
          error_message = '旧任务缺少图片模型版本快照，已阻止付费派发',
          lease_token = '', lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP,
          updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('queued','running') AND EXISTS (
          SELECT 1 FROM ai_space_jobs job WHERE job.id = ai_space_job_items.job_id
            AND job.status IN ('queued','running')
        )`),
      db.prepare(`UPDATE ai_space_jobs SET
          succeeded_count = (SELECT COUNT(*) FROM ai_space_job_items item WHERE item.job_id = ai_space_jobs.id AND item.status = 'succeeded'),
          failed_count = (SELECT COUNT(*) FROM ai_space_job_items item WHERE item.job_id = ai_space_jobs.id AND item.status = 'failed'),
          cancelled_count = (SELECT COUNT(*) FROM ai_space_job_items item WHERE item.job_id = ai_space_jobs.id AND item.status = 'cancelled'),
          status = CASE
            WHEN EXISTS (SELECT 1 FROM ai_space_job_items item WHERE item.job_id = ai_space_jobs.id AND item.status = 'succeeded') THEN 'partial'
            ELSE 'failed'
          END,
          error_code = 'legacy_profile_snapshot_missing',
          error_message = '旧任务缺少图片模型版本快照，已阻止付费派发',
          completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('queued','running')`),
    ]);
  }
  for (const upgrade of AI_SPACE_LEGACY_COLUMN_UPGRADES) {
    const columns = await aiSpaceTableColumns(upgrade.table, db);
    if (columns.has(upgrade.column)) continue;
    try {
      await db.prepare(`ALTER TABLE ${upgrade.table} ADD COLUMN ${upgrade.definition}`).run();
    } catch (error) {
      // Another isolate may have completed the same idempotent compatibility
      // upgrade after our PRAGMA read. Re-read before deciding it is an error.
      const refreshed = await aiSpaceTableColumns(upgrade.table, db);
      if (!refreshed.has(upgrade.column)) throw error;
    }
  }
  await db.prepare("INSERT OR IGNORE INTO ai_space_schema_upgrades (id) VALUES (?)").bind(migrationId).run();
}

export async function ensureAiSpaceSchema(db: D1Database = getD1Database()): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(AI_SPACE_SCHEMA_STATEMENTS.map((statement) => db.prepare(statement)))
    .then(() => ensureAiSpaceLegacyColumns(db))
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

async function aiSpaceBucket(): Promise<R2Bucket> {
  if (bucketOverride) return bucketOverride;
  const { env } = await import("cloudflare:workers");
  if (!env.SALES_IMPORT_FILES) throw new PublicApiError(503, "service_unavailable", "AI 空间图片存储暂不可用。");
  return env.SALES_IMPORT_FILES;
}

function asScene(value: unknown): AiSpaceScene {
  if (aiSpaceScenes.includes(value as AiSpaceScene)) return value as AiSpaceScene;
  throw new PublicApiError(400, "invalid_request", "AI 空间场景无效。");
}

function asSize(value: unknown): AiSpaceSize {
  if (aiSpaceSizes.includes(value as AiSpaceSize)) return value as AiSpaceSize;
  throw new PublicApiError(400, "invalid_request", "图片尺寸无效。");
}

function asProfileStatus(value: unknown): AiSpaceProfileStatus {
  if (aiSpaceProfileStatuses.includes(value as AiSpaceProfileStatus)) return value as AiSpaceProfileStatus;
  throw new PublicApiError(400, "invalid_request", "模型配置状态无效。");
}

function asJobStatus(value: unknown): AiSpaceJobStatus {
  if (aiSpaceJobStatuses.includes(value as AiSpaceJobStatus)) return value as AiSpaceJobStatus;
  throw new Error("AI 空间任务状态无效");
}

function boundedText(value: unknown, field: string, maximumCharacters: number, allowEmpty = false): string {
  if (typeof value !== "string") throw new PublicApiError(400, "invalid_request", `${field}必须为字符串。`);
  const normalized = value.replace(/\r\n?/g, "\n").replace(/\0/g, "").trim();
  if (!allowEmpty && !normalized) throw new PublicApiError(400, "invalid_request", `${field}不能为空。`);
  if (Array.from(normalized).length > maximumCharacters || new TextEncoder().encode(normalized).byteLength > maximumCharacters * 4) {
    throw new PublicApiError(413, "payload_too_large", `${field}超过允许大小。`);
  }
  return normalized;
}

function optionalBoundedText(value: unknown, field: string, maximumCharacters: number): string {
  if (value === undefined || value === null || value === "") return "";
  return boundedText(value, field, maximumCharacters, true);
}

function assertSafeAiSpaceIntent(input: {
  productName: string;
  brand: string;
  sku: string;
  sellingPoints: string;
  additionalInstructions: string;
}) {
  const allText = `${input.productName}\n${input.brand}\n${input.sku}\n${input.sellingPoints}\n${input.additionalInstructions}`;
  if (/忽略.{0,20}(?:规则|要求|指令|安全)|system\s*prompt|developer\s*message|以上.{0,12}(?:无效|作废)/iu.test(allText)) {
    throw new PublicApiError(400, "invalid_request", "图片生成内容包含绕过安全约束的指令，请删除后重试。");
  }
  const riskyRequest = `${input.sellingPoints}\n${input.additionalInstructions}`;
  if (/买家秀|真人.{0,8}(?:代言|出镜|模特)|(?:伪造|虚构).{0,12}(?:认证|参数|功效|销量)|(?:添加|写上|标注|展示|突出|生成).{0,16}(?:价格|折扣|销量|认证|功效|平台标识)/u.test(riskyRequest)) {
    throw new PublicApiError(400, "invalid_request", "首版 AI 空间禁止请求真人背书、虚构认证、价格、折扣、销量或功效内容。");
  }
}

function safeId(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^[a-zA-Z0-9_-]{1,160}$/.test(value)) {
    throw new PublicApiError(400, "invalid_request", `${field}格式无效。`);
  }
  return value;
}

function integerInRange(value: unknown, field: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new PublicApiError(400, "invalid_request", `${field}必须在 ${minimum} 到 ${maximum} 之间。`);
  }
  return value;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return value === undefined ? fallback : value === true;
}

function changes(result: unknown): number {
  return Number((result as { meta?: { changes?: number } } | null)?.meta?.changes ?? 0);
}

async function sha256(value: string | Uint8Array): Promise<string> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function mapProfile(row: ProfileRow): AiSpaceModelProfile {
  return {
    id: row.id,
    name: row.name,
    protocol: "openai_images",
    modelName: row.model_name,
    baseUrl: redactAiModelEndpointUrl(row.base_url),
    apiKeySuffix: row.api_key_suffix,
    status: asProfileStatus(row.status),
    version: Number(row.version),
    timeoutMs: Number(row.timeout_ms),
    lastSuccessResult: row.last_success_result,
    lastSuccessAt: row.last_success_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapTemplate(row: TemplateRow): AiSpaceTemplate {
  return {
    id: row.id,
    scene: asScene(row.scene),
    name: row.name,
    promptTemplate: row.prompt_template,
    size: asSize(row.size),
    modelProfileId: row.model_profile_id,
    version: Number(row.version),
    isEnabled: Boolean(row.is_enabled),
    isDefault: Boolean(row.is_default),
    updatedBy: row.updated_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function profileAuditSummary(row: ProfileRow | null) {
  if (!row) return null;
  return {
    id: row.id,
    name: row.name,
    protocol: "openai_images",
    modelName: row.model_name,
    baseUrl: redactAiModelEndpointUrl(row.base_url),
    apiKeySuffix: row.api_key_suffix,
    status: row.status,
    version: Number(row.version),
    timeoutMs: Number(row.timeout_ms),
  };
}

async function templateAuditSummary(row: TemplateRow | null) {
  if (!row) return null;
  return {
    id: row.id,
    scene: row.scene,
    name: row.name,
    promptDigest: await sha256(row.prompt_template),
    promptCharacters: Array.from(row.prompt_template).length,
    size: row.size,
    modelProfileId: row.model_profile_id,
    version: Number(row.version),
    isEnabled: Boolean(row.is_enabled),
    isDefault: Boolean(row.is_default),
  };
}

function aiSpaceAdminAuditStatement(input: {
  principal: AppPrincipal;
  action: "upsert_profile" | "delete_profile" | "upsert_template" | "delete_template";
  entityType: "model_profile" | "template";
  entityId: string;
  before: unknown;
  after: unknown;
  requirePreviousChange?: boolean;
}, db: D1Database) {
  return db.prepare(`INSERT INTO ai_space_admin_audits
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?${input.requirePreviousChange ? " WHERE changes() = 1" : ""}`)
    .bind(
      `ai-space-audit-${crypto.randomUUID()}`,
      input.principal.email,
      input.principal.role,
      input.action,
      input.entityType,
      input.entityId,
      JSON.stringify(input.before ?? {}),
      JSON.stringify(input.after ?? {}),
    );
}

export async function listAiSpaceModelProfiles(
  input: { enabledOnly?: boolean } = {},
  db: D1Database = getD1Database(),
): Promise<AiSpaceModelProfile[]> {
  await ensureAiSpaceSchema(db);
  const rows = await db.prepare(`SELECT ${profileColumns} FROM ai_space_model_profiles
    ${input.enabledOnly ? "WHERE status = 'enabled'" : ""}
    ORDER BY status DESC, updated_at DESC, id`).all<ProfileRow>();
  return (rows.results ?? []).map(mapProfile);
}

export async function upsertAiSpaceModelProfile(input: {
  id?: unknown;
  name: unknown;
  modelName: unknown;
  baseUrl?: unknown;
  apiKey?: unknown;
  status?: unknown;
  timeoutMs?: unknown;
  expectedVersion?: unknown;
}, principal: AppPrincipal, db: D1Database = getD1Database()): Promise<AiSpaceModelProfile> {
  await ensureAiSpaceSchema(db);
  const id = input.id === undefined ? `ai-space-profile-${crypto.randomUUID()}` : safeId(input.id, "id");
  const name = boundedText(input.name, "配置名称", 100);
  const modelName = boundedText(input.modelName, "模型标识", 120);
  const status = asProfileStatus(input.status ?? "enabled");
  const timeoutMs = integerInRange(input.timeoutMs ?? 90_000, "请求超时", 3_000, 120_000);
  const existing = await db.prepare(`SELECT ${profileColumns} FROM ai_space_model_profiles WHERE id = ? LIMIT 1`)
    .bind(id).first<ProfileRow>();
  if (input.id !== undefined && !existing) throw new PublicApiError(404, "not_found", "图片生成模型配置不存在。");
  const expectedVersion = existing
    ? integerInRange(input.expectedVersion, "expectedVersion", 1, 1_000_000_000)
    : 0;
  if (existing && expectedVersion !== Number(existing.version)) {
    throw new PublicApiError(409, "conflict", "图片生成模型已被其他管理员更新，请刷新后重试。");
  }
  if (existing) {
    const active = await db.prepare(`SELECT COUNT(*) total FROM ai_space_jobs
      WHERE model_profile_id = ? AND status IN ('queued','running')`).bind(id).first<{ total: number }>();
    if (Number(active?.total ?? 0) > 0) {
      throw new PublicApiError(409, "conflict", "该模型仍有排队或运行中的图片任务，请先取消并等待任务终止后再修改。");
    }
  }
  const baseUrlInput = input.baseUrl === undefined ? undefined : boundedText(input.baseUrl, "API 地址", 1_000);
  const endpointSecurity = await loadAiEndpointSecurityContext();
  const baseUrl = baseUrlInput ? normalizeAiModelEndpointForStorage(baseUrlInput, endpointSecurity) : existing?.base_url;
  if (!baseUrl) throw new PublicApiError(400, "invalid_request", "API 地址不能为空。");
  const apiKey = input.apiKey === undefined ? "" : optionalBoundedText(input.apiKey, "API Key", 2_000);
  if (existing && !apiKey && new URL(existing.base_url).origin !== new URL(baseUrl).origin) {
    throw new PublicApiError(
      400,
      "invalid_request",
      "更换图片生成服务 origin 时必须同时填写该服务的新 API Key，不能把原密钥转发到新来源。",
    );
  }
  const encryptedKey = apiKey ? await encryptSecret(apiKey) : existing?.api_key_encrypted ?? "";
  const keySuffix = apiKey ? apiKey.slice(-4) : existing?.api_key_suffix ?? "";
  if (!encryptedKey) throw new PublicApiError(400, "invalid_request", "首次创建图片生成模型时必须填写 API Key。");
  const successStillApplies = Boolean(existing)
    && !apiKey
    && existing?.model_name === modelName
    && existing?.base_url === baseUrl;
  const nextVersion = existing ? Number(existing.version) + 1 : 1;
  const write = db.prepare(`INSERT INTO ai_space_model_profiles (
      id, name, protocol, model_name, base_url, api_key_encrypted, api_key_suffix, status, timeout_ms,
      last_success_result, last_success_at, updated_at
    ) VALUES (?, ?, 'openai_images', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name, model_name = excluded.model_name, base_url = excluded.base_url,
      api_key_encrypted = excluded.api_key_encrypted, api_key_suffix = excluded.api_key_suffix,
      status = excluded.status, version = ai_space_model_profiles.version + 1, timeout_ms = excluded.timeout_ms,
      last_success_result = excluded.last_success_result, last_success_at = excluded.last_success_at,
      updated_at = CURRENT_TIMESTAMP
    WHERE NOT EXISTS (
      SELECT 1 FROM ai_space_jobs active
      WHERE active.model_profile_id = excluded.id AND active.status IN ('queued','running')
    ) AND ai_space_model_profiles.version = ?`)
    .bind(
      id, name, modelName, baseUrl, encryptedKey, keySuffix, status, timeoutMs,
      successStillApplies ? existing?.last_success_result ?? null : null,
      successStillApplies ? existing?.last_success_at ?? null : null,
      expectedVersion,
    );
  const writes = await db.batch([
    write,
    aiSpaceAdminAuditStatement({
      principal,
      action: "upsert_profile",
      entityType: "model_profile",
      entityId: id,
      before: profileAuditSummary(existing),
      after: {
        id, name, protocol: "openai_images", modelName, baseUrl: redactAiModelEndpointUrl(baseUrl),
        apiKeySuffix: keySuffix, apiKeyRotated: Boolean(apiKey), status, version: nextVersion, timeoutMs,
      },
      requirePreviousChange: true,
    }, db),
  ]) as Array<unknown>;
  if (changes(writes[0]) !== 1) {
    throw new PublicApiError(409, "conflict", "该模型刚被新的图片任务引用，本次配置修改未生效。");
  }
  if (changes(writes[1]) !== 1) throw new Error("图片生成模型变更审计未写入");
  const row = await db.prepare(`SELECT ${profileColumns} FROM ai_space_model_profiles WHERE id = ? LIMIT 1`)
    .bind(id).first<ProfileRow>();
  if (!row) throw new Error("图片生成模型保存后无法读取");
  return mapProfile(row);
}

export async function deleteAiSpaceModelProfile(
  idInput: unknown,
  expectedVersionInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<void> {
  await ensureAiSpaceSchema(db);
  const id = safeId(idInput, "id");
  const existing = await db.prepare(`SELECT ${profileColumns} FROM ai_space_model_profiles WHERE id = ? LIMIT 1`)
    .bind(id).first<ProfileRow>();
  if (!existing) throw new PublicApiError(404, "not_found", "图片生成模型配置不存在。");
  const expectedVersion = integerInRange(expectedVersionInput, "expectedVersion", 1, 1_000_000_000);
  if (expectedVersion !== Number(existing.version)) {
    throw new PublicApiError(409, "conflict", "图片生成模型已被其他管理员更新，请刷新后再删除。");
  }
  const usage = await db.prepare(`SELECT
      (SELECT COUNT(*) FROM ai_space_templates WHERE model_profile_id = ?) template_count,
      (SELECT COUNT(*) FROM ai_space_jobs WHERE model_profile_id = ?) job_count`)
    .bind(id, id).first<{ template_count: number; job_count: number }>();
  if (Number(usage?.template_count ?? 0) > 0 || Number(usage?.job_count ?? 0) > 0) {
    throw new PublicApiError(409, "conflict", "该模型已被模板或历史任务引用，请先停用而不要删除。");
  }
  const results = await db.batch([
    db.prepare(`DELETE FROM ai_space_model_profiles WHERE id = ? AND version = ?
      AND NOT EXISTS (SELECT 1 FROM ai_space_templates WHERE model_profile_id = ?)
      AND NOT EXISTS (SELECT 1 FROM ai_space_jobs WHERE model_profile_id = ?)`)
      .bind(id, expectedVersion, id, id),
    aiSpaceAdminAuditStatement({
      principal, action: "delete_profile", entityType: "model_profile", entityId: id,
      before: profileAuditSummary(existing), after: { deleted: true }, requirePreviousChange: true,
    }, db),
  ]) as Array<unknown>;
  if (changes(results[0]) !== 1) throw new PublicApiError(409, "conflict", "该模型已被模板或历史任务引用，请停用而不要删除。");
  if (changes(results[1]) !== 1) throw new Error("图片生成模型删除审计未写入");
}

export async function listAiSpaceTemplates(
  input: { enabledOnly?: boolean } = {},
  db: D1Database = getD1Database(),
): Promise<AiSpaceTemplate[]> {
  await ensureAiSpaceSchema(db);
  const rows = await db.prepare(`SELECT ${templateColumns} FROM ai_space_templates
    ${input.enabledOnly ? "WHERE is_enabled = 1" : ""}
    ORDER BY scene, is_default DESC, is_enabled DESC, updated_at DESC, id`).all<TemplateRow>();
  return (rows.results ?? []).map(mapTemplate);
}

const AI_SPACE_TEMPLATE_PLACEHOLDERS = new Set(["product_name", "brand", "sku", "selling_points", "scene"]);
const AI_SPACE_SEED_TEMPLATE_IDS = new Set([
  "ai-space-template-product-main",
  "ai-space-template-product-detail",
  "ai-space-template-promotion",
]);

function validatePromptTemplate(value: unknown): string {
  const prompt = boundedText(value, "提示词模板", 3_000);
  const placeholders = [...prompt.matchAll(/\{([^{}]+)\}/g)].map((match) => match[1] ?? "");
  const invalid = placeholders.filter((placeholder) => !AI_SPACE_TEMPLATE_PLACEHOLDERS.has(placeholder));
  if (invalid.length > 0) throw new PublicApiError(400, "invalid_request", `提示词包含不支持的占位符：${invalid[0]}`);
  if (!placeholders.includes("product_name")) {
    throw new PublicApiError(400, "invalid_request", "提示词模板必须包含 {product_name} 占位符。");
  }
  if (/\{[^{}]*$|^[^{}]*\}/.test(prompt)) throw new PublicApiError(400, "invalid_request", "提示词模板花括号不完整。");
  return prompt;
}

export async function upsertAiSpaceTemplate(input: {
  id?: unknown;
  scene: unknown;
  name: unknown;
  promptTemplate: unknown;
  size: unknown;
  modelProfileId?: unknown;
  isEnabled?: unknown;
  isDefault?: unknown;
  expectedVersion?: unknown;
}, principal: AppPrincipal, db: D1Database = getD1Database()): Promise<AiSpaceTemplate> {
  await ensureAiSpaceSchema(db);
  const id = input.id === undefined ? `ai-space-template-${crypto.randomUUID()}` : safeId(input.id, "id");
  const existing = await db.prepare(`SELECT ${templateColumns} FROM ai_space_templates WHERE id = ? LIMIT 1`)
    .bind(id).first<TemplateRow>();
  if (input.id !== undefined && !existing) throw new PublicApiError(404, "not_found", "AI 空间模板不存在。");
  const expectedVersion = existing
    ? integerInRange(input.expectedVersion, "expectedVersion", 1, 1_000_000_000)
    : 0;
  if (existing && expectedVersion !== Number(existing.version)) {
    throw new PublicApiError(409, "conflict", "AI 空间模板已被其他管理员更新，请刷新后重试。");
  }
  const scene = asScene(input.scene);
  const name = boundedText(input.name, "模板名称", 100);
  const promptTemplate = validatePromptTemplate(input.promptTemplate);
  const size = asSize(input.size);
  const isEnabled = booleanValue(input.isEnabled, true);
  const isDefault = booleanValue(input.isDefault, false);
  if (isDefault && !isEnabled) throw new PublicApiError(400, "invalid_request", "默认模板必须同时启用。");
  const modelProfileId = input.modelProfileId === undefined || input.modelProfileId === null || input.modelProfileId === ""
    ? null
    : safeId(input.modelProfileId, "modelProfileId");
  if (modelProfileId) {
    const profile = await db.prepare("SELECT id FROM ai_space_model_profiles WHERE id = ? LIMIT 1")
      .bind(modelProfileId).first<{ id: string }>();
    if (!profile) throw new PublicApiError(400, "invalid_request", "模板指定的图片生成模型不存在。");
  }
  const write = db.prepare(`INSERT INTO ai_space_templates (
      id, scene, name, prompt_template, size, model_profile_id, version, is_enabled, is_default, updated_by, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET
      scene = excluded.scene, name = excluded.name, prompt_template = excluded.prompt_template,
      size = excluded.size, model_profile_id = excluded.model_profile_id,
      version = ai_space_templates.version + 1, is_enabled = excluded.is_enabled,
      is_default = excluded.is_default, updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP
    WHERE ai_space_templates.version = ?`)
    .bind(id, scene, name, promptTemplate, size, modelProfileId, isEnabled ? 1 : 0, isDefault ? 1 : 0, principal.email, expectedVersion);
  const clearedDefaults = isDefault
    ? await db.prepare(`SELECT id FROM ai_space_templates WHERE scene = ? AND id <> ? AND is_default = 1`)
      .bind(scene, id).all<{ id: string }>()
    : { results: [] as Array<{ id: string }> };
  const writes = await db.batch([
    db.prepare(`UPDATE ai_space_templates SET is_default = 0, updated_at = CURRENT_TIMESTAMP
      WHERE scene = ? AND id <> ? AND is_default = 1 AND ? = 1
        AND (? = 1 OR EXISTS (SELECT 1 FROM ai_space_templates target WHERE target.id = ? AND target.version = ?))`)
      .bind(scene, id, isDefault ? 1 : 0, existing ? 0 : 1, id, expectedVersion),
    write,
    aiSpaceAdminAuditStatement({
      principal,
      action: "upsert_template",
      entityType: "template",
      entityId: id,
      before: await templateAuditSummary(existing),
      after: {
        id, scene, name, promptDigest: await sha256(promptTemplate), promptCharacters: Array.from(promptTemplate).length,
        size, modelProfileId, version: existing ? Number(existing.version) + 1 : 1,
        isEnabled, isDefault, clearedDefaultTemplateIds: (clearedDefaults.results ?? []).map((row) => row.id),
      },
      requirePreviousChange: true,
    }, db),
  ]) as Array<unknown>;
  if (changes(writes[1]) !== 1) {
    throw new PublicApiError(409, "conflict", "AI 空间模板已被其他管理员更新，请刷新后重试。");
  }
  if (changes(writes[2]) !== 1) throw new Error("AI 空间模板变更审计未写入");
  const row = await db.prepare(`SELECT ${templateColumns} FROM ai_space_templates WHERE id = ? LIMIT 1`)
    .bind(id).first<TemplateRow>();
  if (!row) throw new Error("AI 空间模板保存后无法读取");
  return mapTemplate(row);
}

export async function deleteAiSpaceTemplate(
  idInput: unknown,
  expectedVersionInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<void> {
  await ensureAiSpaceSchema(db);
  const id = safeId(idInput, "id");
  const existing = await db.prepare(`SELECT ${templateColumns} FROM ai_space_templates WHERE id = ? LIMIT 1`)
    .bind(id).first<TemplateRow>();
  if (!existing) throw new PublicApiError(404, "not_found", "AI 空间模板不存在。");
  const expectedVersion = integerInRange(expectedVersionInput, "expectedVersion", 1, 1_000_000_000);
  if (expectedVersion !== Number(existing.version)) {
    throw new PublicApiError(409, "conflict", "AI 空间模板已被其他管理员更新，请刷新后再删除。");
  }
  if (AI_SPACE_SEED_TEMPLATE_IDS.has(id)) {
    throw new PublicApiError(409, "conflict", "系统内置模板不可删除，可以复制后定制或切换其他默认模板。");
  }
  const history = await db.prepare("SELECT COUNT(*) total FROM ai_space_jobs WHERE template_id = ?")
    .bind(id).first<{ total: number }>();
  if (Number(history?.total ?? 0) > 0) throw new PublicApiError(409, "conflict", "该模板已被历史任务引用，请停用而不要删除。");
  const results = await db.batch([
    db.prepare(`DELETE FROM ai_space_templates WHERE id = ? AND version = ?
      AND NOT EXISTS (SELECT 1 FROM ai_space_jobs WHERE template_id = ?)`)
      .bind(id, expectedVersion, id),
    aiSpaceAdminAuditStatement({
      principal, action: "delete_template", entityType: "template", entityId: id,
      before: await templateAuditSummary(existing), after: { deleted: true }, requirePreviousChange: true,
    }, db),
  ]) as Array<unknown>;
  if (changes(results[0]) !== 1) throw new PublicApiError(409, "conflict", "该模板已被历史任务引用，请停用而不要删除。");
  if (changes(results[1]) !== 1) throw new Error("AI 空间模板删除审计未写入");
}

export async function getAiSpaceMeta(principal: AppPrincipal, db: D1Database = getD1Database()) {
  const [templates, profiles] = await Promise.all([
    listAiSpaceTemplates({ enabledOnly: true }, db),
    listAiSpaceModelProfiles({ enabledOnly: true }, db),
  ]);
  return {
    scenes: AI_SPACE_SCENE_META,
    templates: templates.map((template) => ({
      id: template.id,
      scene: template.scene,
      name: template.name,
      size: template.size,
      version: template.version,
      isDefault: template.isDefault,
      modelProfileId: template.modelProfileId,
    })),
    profiles: profiles.map((profile) => ({
      id: profile.id,
      name: profile.name,
      modelName: profile.modelName,
      lastSuccessAt: profile.lastSuccessAt,
    })),
    limits: AI_SPACE_LIMITS,
    permissions: {
      canGenerate: principal.role !== "viewer",
      canManage: principal.role === "admin" && principal.scope === null,
    },
    safetyNotice: "首版仅接受商品主图、卖点详情和活动视觉草稿；禁止请求买家秀、真人背书、虚构认证、价格或销量，所有生成物发布前必须人工复核。",
  };
}

function renderAiSpacePrompt(template: AiSpaceTemplate, input: {
  productName: string;
  brand: string;
  sku: string;
  sellingPoints: string;
  additionalInstructions: string;
}): string {
  const values: Record<string, string> = {
    product_name: input.productName,
    brand: input.brand ? `${input.brand} ` : "",
    sku: input.sku || "未提供",
    selling_points: input.sellingPoints || "保持真实商品特征，不补充未提供的参数",
    scene: AI_SPACE_SCENE_META.find((item) => item.id === template.scene)?.label ?? template.scene,
  };
  let prompt = template.promptTemplate;
  for (const [key, value] of Object.entries(values)) prompt = prompt.replaceAll(`{${key}}`, value);
  if (/\{[^{}]+\}/.test(prompt)) throw new PublicApiError(400, "invalid_request", "模板仍包含未解析占位符。");
  if (input.additionalInstructions) prompt += `\n补充构图要求：${input.additionalInstructions}`;
  prompt += "\n安全约束：不生成真人买家秀或代言，不伪造平台认证、销量、价格、折扣、功效或未提供的商品参数；尽量避免不可核验文字。";
  if (Array.from(prompt).length > AI_SPACE_LIMITS.maximumPromptCharacters) {
    throw new PublicApiError(413, "payload_too_large", "渲染后的提示词超过安全上限。");
  }
  return prompt;
}

function mapAsset(row: AssetRow): AiSpaceAsset {
  const mimeType = row.mime_type;
  if (mimeType !== "image/png" && mimeType !== "image/jpeg" && mimeType !== "image/webp") {
    throw new Error("AI 空间图片类型无效");
  }
  return {
    id: row.id,
    jobId: row.job_id,
    itemId: row.item_id,
    scene: asScene(row.scene),
    productName: row.product_name ?? "",
    brand: row.brand ?? "",
    sku: row.sku ?? "",
    mimeType,
    byteSize: Number(row.byte_size),
    width: Number(row.width),
    height: Number(row.height),
    favorite: Boolean(row.favorite),
    generatedByAi: true,
    reviewRequired: true,
    contentUrl: `/api/ai/space/assets/${encodeURIComponent(row.id)}/content`,
    createdAt: row.created_at,
  };
}

function mapJob(row: JobRow, items: AiSpaceJobItem[]): AiSpaceJob {
  return {
    id: row.id,
    clientRequestId: row.client_request_id,
    scene: asScene(row.scene),
    templateId: row.template_id,
    templateName: row.template_name,
    templateVersion: Number(row.template_version),
    modelProfileId: row.model_profile_id,
    modelProfileName: row.model_profile_name,
    modelProfileVersion: Number(row.model_profile_version),
    modelName: row.model_name,
    productName: row.product_name,
    brand: row.brand,
    sku: row.sku,
    sellingPoints: row.selling_points,
    finalPrompt: row.final_prompt,
    size: asSize(row.size),
    requestedCount: Number(row.requested_count),
    succeededCount: Number(row.succeeded_count),
    failedCount: Number(row.failed_count),
    cancelledCount: Number(row.cancelled_count),
    status: asJobStatus(row.status),
    cancelRequested: Boolean(row.cancel_requested),
    errorCode: row.error_code,
    errorMessage: row.error_message,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    items,
  };
}

async function hydrateAiSpaceJobs(rows: JobRow[], principal: AppPrincipal, db: D1Database): Promise<AiSpaceJob[]> {
  if (rows.length === 0) return [];
  const ids = rows.map((row) => row.id);
  const itemRows = await db.prepare(`SELECT id, job_id, ordinal, status, error_code, error_message, duration_ms
    FROM ai_space_job_items WHERE job_id IN (SELECT CAST(value AS TEXT) FROM json_each(?))
    ORDER BY job_id, ordinal`).bind(JSON.stringify(ids)).all<ItemRow>();
  const itemIds = (itemRows.results ?? []).map((row) => row.id);
  const assets = itemIds.length === 0 ? [] : (await db.prepare(`SELECT a.*, j.product_name, j.brand, j.sku,
      EXISTS(SELECT 1 FROM ai_space_asset_favorites f WHERE f.asset_id = a.id AND f.actor_email = ?) favorite
    FROM ai_space_assets a JOIN ai_space_jobs j ON j.id = a.job_id
    WHERE a.item_id IN (SELECT CAST(value AS TEXT) FROM json_each(?))`)
    .bind(principal.email, JSON.stringify(itemIds)).all<AssetRow>()).results ?? [];
  const assetByItem = new Map(assets.map((row) => [row.item_id, mapAsset(row)]));
  const itemsByJob = new Map<string, AiSpaceJobItem[]>();
  for (const row of itemRows.results ?? []) {
    const status = row.status === "running" || row.status === "succeeded" || row.status === "failed" || row.status === "cancelled"
      ? row.status
      : "queued";
    const item: AiSpaceJobItem = {
      id: row.id,
      ordinal: Number(row.ordinal),
      status,
      errorCode: row.error_code,
      errorMessage: row.error_message,
      durationMs: row.duration_ms === null ? null : Number(row.duration_ms),
      asset: assetByItem.get(row.id) ?? null,
    };
    const bucket = itemsByJob.get(row.job_id) ?? [];
    bucket.push(item);
    itemsByJob.set(row.job_id, bucket);
  }
  return rows.map((row) => mapJob(row, itemsByJob.get(row.id) ?? []));
}

export async function listAiSpaceJobs(input: {
  page?: number;
  pageSize?: number;
}, principal: AppPrincipal, db: D1Database = getD1Database()) {
  await ensureAiSpaceSchema(db);
  const page = integerInRange(input.page ?? 1, "page", 1, 10_000);
  const pageSize = integerInRange(input.pageSize ?? 20, "pageSize", 1, AI_SPACE_LIMITS.maximumJobsPageSize);
  const offset = (page - 1) * pageSize;
  const access = aiScopeSnapshotAccessSql(principal.scope, "j.scope_json");
  const where = `WHERE j.owner_email = ?${access.clause}`;
  const values = [principal.email, ...access.values];
  const [count, rows] = await Promise.all([
    db.prepare(`SELECT COUNT(*) total FROM ai_space_jobs j ${where}`).bind(...values).first<{ total: number }>(),
    db.prepare(`SELECT ${qualifiedJobColumns}
      FROM ai_space_jobs j ${where} ORDER BY j.created_at DESC, j.id DESC LIMIT ? OFFSET ?`)
      .bind(...values, pageSize, offset).all<JobRow>(),
  ]);
  const items = await hydrateAiSpaceJobs(rows.results ?? [], principal, db);
  const total = Number(count?.total ?? 0);
  return {
    items,
    pagination: {
      page,
      pageSize,
      total,
      returned: items.length,
      hasMore: offset + items.length < total,
      truncated: offset + items.length < total,
    },
  };
}

async function getAiSpaceJobRow(id: string, principal: AppPrincipal, db: D1Database): Promise<JobRow> {
  const access = aiScopeSnapshotAccessSql(principal.scope, "j.scope_json");
  const row = await db.prepare(`SELECT ${qualifiedJobColumns}
    FROM ai_space_jobs j WHERE j.id = ? AND j.owner_email = ?${access.clause} LIMIT 1`)
    .bind(id, principal.email, ...access.values).first<JobRow>();
  if (!row) throw new PublicApiError(404, "not_found", "AI 空间任务不存在。");
  return row;
}

export async function getAiSpaceJob(idInput: unknown, principal: AppPrincipal, db: D1Database = getD1Database()): Promise<AiSpaceJob> {
  await ensureAiSpaceSchema(db);
  const id = safeId(idInput, "id");
  const row = await getAiSpaceJobRow(id, principal, db);
  return (await hydrateAiSpaceJobs([row], principal, db))[0]!;
}

export async function createAiSpaceJob(input: {
  clientRequestId: unknown;
  scene: unknown;
  templateId: unknown;
  modelProfileId?: unknown;
  productName: unknown;
  brand?: unknown;
  sku?: unknown;
  sellingPoints?: unknown;
  additionalInstructions?: unknown;
  count: unknown;
}, principal: AppPrincipal, db: D1Database = getD1Database()): Promise<{ item: AiSpaceJob; replayed: boolean }> {
  await ensureAiSpaceSchema(db);
  const clientRequestId = safeId(input.clientRequestId, "clientRequestId");
  const scene = asScene(input.scene);
  const templateId = safeId(input.templateId, "templateId");
  const productName = boundedText(input.productName, "商品名称", 200);
  const brand = optionalBoundedText(input.brand, "品牌", 100);
  const sku = optionalBoundedText(input.sku, "SKU", 120);
  const sellingPoints = optionalBoundedText(input.sellingPoints, "卖点", 800);
  const additionalInstructions = optionalBoundedText(input.additionalInstructions, "补充要求", 800);
  assertSafeAiSpaceIntent({ productName, brand, sku, sellingPoints, additionalInstructions });
  const count = integerInRange(input.count, "生成数量", AI_SPACE_LIMITS.minimumImages, AI_SPACE_LIMITS.maximumImages);
  const requestedProfileId = input.modelProfileId === undefined || input.modelProfileId === null || input.modelProfileId === ""
    ? null
    : safeId(input.modelProfileId, "modelProfileId");
  // The idempotency receipt is derived only from the normalized client payload. Runtime template/model
  // snapshots remain immutable on the job, but later configuration changes must not break replay.
  const requestDigest = await sha256(JSON.stringify({
    scene, templateId, requestedProfileId, productName, brand, sku, sellingPoints, additionalInstructions, count,
  }));
  const receiptAccess = aiScopeSnapshotAccessSql(principal.scope, "j.scope_json");
  const existing = await db.prepare(`SELECT ${qualifiedJobColumns} FROM ai_space_jobs j
    WHERE j.owner_email = ? AND j.client_request_id = ?${receiptAccess.clause} LIMIT 1`)
    .bind(principal.email, clientRequestId, ...receiptAccess.values).first<JobRow>();
  if (existing) {
    if (existing.request_digest !== requestDigest) {
      throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的生成请求。");
    }
    return { item: (await hydrateAiSpaceJobs([existing], principal, db))[0]!, replayed: true };
  }
  const inaccessibleReceipt = await db.prepare(`SELECT 1 present FROM ai_space_jobs
    WHERE owner_email = ? AND client_request_id = ? LIMIT 1`)
    .bind(principal.email, clientRequestId).first<{ present: number }>();
  if (inaccessibleReceipt) {
    throw new PublicApiError(404, "not_found", "AI 空间任务不存在或当前数据范围不可访问。");
  }
  const templateRow = await db.prepare(`SELECT ${templateColumns} FROM ai_space_templates
    WHERE id = ? AND scene = ? AND is_enabled = 1 LIMIT 1`).bind(templateId, scene).first<TemplateRow>();
  if (!templateRow) throw new PublicApiError(400, "invalid_request", "所选模板不可用或不属于当前场景。");
  const template = mapTemplate(templateRow);
  const profileId = requestedProfileId ?? template.modelProfileId;
  if (!profileId) throw new PublicApiError(400, "invalid_request", "请选择图片生成模型，或为模板配置默认模型。");
  const profile = await db.prepare(`SELECT ${profileColumns} FROM ai_space_model_profiles
    WHERE id = ? AND status = 'enabled' LIMIT 1`).bind(profileId).first<ProfileRow>();
  if (!profile || !profile.api_key_encrypted) throw new PublicApiError(400, "invalid_request", "所选图片生成模型当前不可用。");
  const finalPrompt = renderAiSpacePrompt(template, { productName, brand, sku, sellingPoints, additionalInstructions });
  const promptDigest = await sha256(finalPrompt);
  const id = `ai-space-job-${crypto.randomUUID()}`;
  const scopeJson = serializeAiConversationScope(principal.scope);
  const insertJob = db.prepare(`INSERT OR IGNORE INTO ai_space_jobs (
      id, client_request_id, request_digest, owner_email, scope_json, scene,
      template_id, template_name, template_version, model_profile_id, model_profile_name, model_profile_version, model_name,
      product_name, brand, sku, selling_points, final_prompt, prompt_digest, size, requested_count
    ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE
      (SELECT COUNT(*) FROM ai_space_jobs WHERE status IN ('queued','running')) < ?
      AND (SELECT COUNT(*) FROM ai_space_jobs WHERE owner_email = ? AND status IN ('queued','running')) < ?
      AND COALESCE((SELECT SUM(requested_count) FROM ai_space_jobs
        WHERE owner_email = ? AND date(created_at, '+8 hours') = date('now', '+8 hours')), 0) + ? <= ?`)
    .bind(
      id, clientRequestId, requestDigest, principal.email, scopeJson, scene,
      template.id, template.name, template.version, profile.id, profile.name, profile.version, profile.model_name,
      productName, brand, sku, sellingPoints, finalPrompt, promptDigest, template.size, count,
      AI_SPACE_LIMITS.maximumActiveJobsGlobal,
      principal.email, AI_SPACE_LIMITS.maximumActiveJobsPerOwner,
      principal.email, count, AI_SPACE_LIMITS.maximumDailyImagesPerOwner,
    );
  const itemStatements = Array.from({ length: count }, (_, index) => db.prepare(`INSERT INTO ai_space_job_items (id, job_id, ordinal)
    SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM ai_space_jobs WHERE id = ?)`)
    .bind(`ai-space-item-${crypto.randomUUID()}`, id, index + 1, id));
  const writes = await db.batch([insertJob, ...itemStatements]) as Array<unknown>;
  if (changes(writes[0]) !== 1) {
    const raced = await db.prepare(`SELECT ${qualifiedJobColumns} FROM ai_space_jobs j
      WHERE j.owner_email = ? AND j.client_request_id = ?${receiptAccess.clause} LIMIT 1`)
      .bind(principal.email, clientRequestId, ...receiptAccess.values).first<JobRow>();
    if (raced) {
      if (raced.request_digest !== requestDigest) throw new PublicApiError(409, "conflict", "同一个 clientRequestId 已用于不同的生成请求。");
      return { item: (await hydrateAiSpaceJobs([raced], principal, db))[0]!, replayed: true };
    }
    const racedOutsideScope = await db.prepare(`SELECT 1 present FROM ai_space_jobs
      WHERE owner_email = ? AND client_request_id = ? LIMIT 1`)
      .bind(principal.email, clientRequestId).first<{ present: number }>();
    if (racedOutsideScope) {
      throw new PublicApiError(404, "not_found", "AI 空间任务不存在或当前数据范围不可访问。");
    }
    throw new PublicApiError(409, "conflict", "当前生成队列或今日额度已满，请等待现有任务完成后再试。");
  }
  return { item: await getAiSpaceJob(id, principal, db), replayed: false };
}

async function refreshAiSpaceJobAggregate(jobId: string, db: D1Database): Promise<void> {
  await db.prepare(`UPDATE ai_space_jobs SET
      succeeded_count = (SELECT COUNT(*) FROM ai_space_job_items WHERE job_id = ? AND status = 'succeeded'),
      failed_count = (SELECT COUNT(*) FROM ai_space_job_items WHERE job_id = ? AND status = 'failed'),
      cancelled_count = (SELECT COUNT(*) FROM ai_space_job_items WHERE job_id = ? AND status = 'cancelled'),
      status = CASE
        WHEN EXISTS (SELECT 1 FROM ai_space_job_items WHERE job_id = ? AND status IN ('queued','running'))
          THEN CASE WHEN EXISTS (SELECT 1 FROM ai_space_job_items WHERE job_id = ? AND status = 'running') THEN 'running' ELSE 'queued' END
        WHEN (SELECT COUNT(*) FROM ai_space_job_items WHERE job_id = ? AND status = 'succeeded') = requested_count THEN 'succeeded'
        WHEN (SELECT COUNT(*) FROM ai_space_job_items WHERE job_id = ? AND status = 'succeeded') > 0 THEN 'partial'
        WHEN (SELECT COUNT(*) FROM ai_space_job_items WHERE job_id = ? AND status = 'cancelled') = requested_count THEN 'cancelled'
        ELSE 'failed'
      END,
      completed_at = CASE
        WHEN EXISTS (SELECT 1 FROM ai_space_job_items WHERE job_id = ? AND status IN ('queued','running')) THEN NULL
        ELSE COALESCE(completed_at, CURRENT_TIMESTAMP)
      END,
      updated_at = CURRENT_TIMESTAMP
    WHERE id = ?`)
    .bind(jobId, jobId, jobId, jobId, jobId, jobId, jobId, jobId, jobId, jobId).run();
}

export async function cancelAiSpaceJob(idInput: unknown, principal: AppPrincipal, db: D1Database = getD1Database()): Promise<AiSpaceJob> {
  await ensureAiSpaceSchema(db);
  const id = safeId(idInput, "id");
  const row = await getAiSpaceJobRow(id, principal, db);
  if (row.status === "succeeded" || row.status === "partial" || row.status === "failed" || row.status === "cancelled") {
    return (await hydrateAiSpaceJobs([row], principal, db))[0]!;
  }
  await db.batch([
    db.prepare(`UPDATE ai_space_jobs SET cancel_requested = 1, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND status IN ('queued','running')`).bind(id),
    db.prepare(`UPDATE ai_space_job_items SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP,
      error_code = 'cancelled_by_user', error_message = '用户已取消尚未派发的图片', updated_at = CURRENT_TIMESTAMP
      WHERE job_id = ? AND status = 'queued'`).bind(id),
  ]);
  await refreshAiSpaceJobAggregate(id, db);
  return getAiSpaceJob(id, principal, db);
}

export async function listAiSpaceAssets(input: {
  page?: number;
  pageSize?: number;
  favoritesOnly?: boolean;
}, principal: AppPrincipal, db: D1Database = getD1Database()) {
  await ensureAiSpaceSchema(db);
  const page = integerInRange(input.page ?? 1, "page", 1, 10_000);
  const pageSize = integerInRange(input.pageSize ?? 24, "pageSize", 1, AI_SPACE_LIMITS.maximumAssetsPageSize);
  const offset = (page - 1) * pageSize;
  const access = aiScopeSnapshotAccessSql(principal.scope, "a.scope_json");
  const favoriteClause = input.favoritesOnly
    ? " AND EXISTS(SELECT 1 FROM ai_space_asset_favorites filter_f WHERE filter_f.asset_id = a.id AND filter_f.actor_email = ?)"
    : "";
  const values = [principal.email, ...access.values, ...(input.favoritesOnly ? [principal.email] : [])];
  const where = `WHERE a.owner_email = ?${access.clause}${favoriteClause}`;
  const [count, rows] = await Promise.all([
    db.prepare(`SELECT COUNT(*) total FROM ai_space_assets a ${where}`).bind(...values).first<{ total: number }>(),
    db.prepare(`SELECT a.*, j.product_name, j.brand, j.sku,
        EXISTS(SELECT 1 FROM ai_space_asset_favorites f WHERE f.asset_id = a.id AND f.actor_email = ?) favorite
      FROM ai_space_assets a JOIN ai_space_jobs j ON j.id = a.job_id
      ${where} ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?`)
      .bind(principal.email, ...values, pageSize, offset).all<AssetRow>(),
  ]);
  const items = (rows.results ?? []).map(mapAsset);
  const total = Number(count?.total ?? 0);
  return {
    items,
    pagination: { page, pageSize, total, returned: items.length, hasMore: offset + items.length < total, truncated: offset + items.length < total },
  };
}

async function getAiSpaceAssetRow(id: string, principal: AppPrincipal, db: D1Database): Promise<AssetRow> {
  const access = aiScopeSnapshotAccessSql(principal.scope, "a.scope_json");
  const row = await db.prepare(`SELECT a.*, j.product_name, j.brand, j.sku,
      EXISTS(SELECT 1 FROM ai_space_asset_favorites f WHERE f.asset_id = a.id AND f.actor_email = ?) favorite
    FROM ai_space_assets a JOIN ai_space_jobs j ON j.id = a.job_id
    WHERE a.id = ? AND a.owner_email = ?${access.clause} LIMIT 1`)
    .bind(principal.email, id, principal.email, ...access.values).first<AssetRow>();
  if (!row) throw new PublicApiError(404, "not_found", "AI 空间图片不存在。");
  return row;
}

export async function setAiSpaceAssetFavorite(
  idInput: unknown,
  favorite: boolean,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
): Promise<AiSpaceAsset> {
  await ensureAiSpaceSchema(db);
  const id = safeId(idInput, "id");
  await getAiSpaceAssetRow(id, principal, db);
  if (favorite) {
    await db.prepare(`INSERT INTO ai_space_asset_favorites (asset_id, actor_email)
      VALUES (?, ?) ON CONFLICT(asset_id, actor_email) DO NOTHING`).bind(id, principal.email).run();
  } else {
    await db.prepare("DELETE FROM ai_space_asset_favorites WHERE asset_id = ? AND actor_email = ?")
      .bind(id, principal.email).run();
  }
  return mapAsset(await getAiSpaceAssetRow(id, principal, db));
}

export async function getAiSpaceAssetDownload(
  idInput: unknown,
  principal: AppPrincipal,
  db: D1Database = getD1Database(),
  bucket?: R2Bucket,
) {
  await ensureAiSpaceSchema(db);
  const id = safeId(idInput, "id");
  const row = await getAiSpaceAssetRow(id, principal, db);
  const object = await (bucket ?? await aiSpaceBucket()).get(row.object_key);
  if (!object || Number(object.size) !== Number(row.byte_size)
    || object.httpMetadata?.contentType !== row.mime_type
    || object.customMetadata?.sha256 !== row.content_sha256
    || object.customMetadata?.source !== "ai-space"
    || object.customMetadata?.jobId !== row.job_id
    || object.customMetadata?.itemId !== row.item_id) {
    throw new PublicApiError(503, "service_unavailable", "AI 空间图片存储回查失败，请稍后重试。");
  }
  let bytes: Uint8Array;
  try {
    const reader = object.body.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      total += chunk.value.byteLength;
      if (total > Number(row.byte_size) || total > AI_SPACE_LIMITS.maximumImageBytes) {
        await reader.cancel("AI Space R2 object exceeded its bounded size").catch(() => undefined);
        throw new Error("R2 对象内容超过已审计大小");
      }
      chunks.push(chunk.value);
    }
    if (total !== Number(row.byte_size)) throw new Error("R2 对象内容大小不一致");
    bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    if (await sha256(bytes) !== row.content_sha256) throw new Error("R2 对象内容摘要不一致");
    const decoded = row.mime_type === "image/png"
      ? await validateAiSpaceImageBytesDeep(bytes)
      : validateAiSpaceImageBytes(bytes);
    if (decoded.mimeType !== row.mime_type || decoded.width !== Number(row.width) || decoded.height !== Number(row.height)) {
      throw new Error("R2 对象图片身份不一致");
    }
  } catch {
    throw new PublicApiError(503, "service_unavailable", "AI 空间图片存储回查失败，请稍后重试。");
  }
  const extension = row.mime_type === "image/png" ? "png" : row.mime_type === "image/webp" ? "webp" : "jpg";
  return {
    body: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer,
    mimeType: row.mime_type,
    byteSize: Number(row.byte_size),
    fileName: `ai-space-${row.id}.${extension}`,
    contentSha256: row.content_sha256,
  };
}

type AiSpaceItemLease = {
  itemId: string;
  jobId: string;
  ordinal: number;
  leaseToken: string;
  leaseEpoch: number;
};

type RunnerContextRow = JobRow & {
  profile_base_url: string;
  profile_api_key_encrypted: string;
  profile_status: string;
  profile_version: number;
  profile_timeout_ms: number;
};

type ValidatedGeneratedImage = {
  bytes: Uint8Array;
  mimeType: "image/png" | "image/jpeg" | "image/webp";
  extension: "png" | "jpg" | "webp";
  width: number;
  height: number;
  providerRequestId: string;
  usageJson: string;
};

function imageDimensionsSafe(width: number, height: number) {
  return Number.isSafeInteger(width) && Number.isSafeInteger(height)
    && width > 0 && height > 0 && width <= 16_384 && height <= 16_384
    && width * height <= AI_SPACE_LIMITS.maximumImagePixels;
}

function uint32BigEndian(bytes: Uint8Array, offset: number) {
  return (bytes[offset]! * 0x1000000) + (bytes[offset + 1]! << 16) + (bytes[offset + 2]! << 8) + bytes[offset + 3]!;
}

function uint32LittleEndian(bytes: Uint8Array, offset: number) {
  return (bytes[offset]! | (bytes[offset + 1]! << 8) | (bytes[offset + 2]! << 16) | (bytes[offset + 3]! << 24)) >>> 0;
}

function ascii(bytes: Uint8Array, start: number, end: number) {
  return String.fromCharCode(...bytes.subarray(start, end));
}

function jpegDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.byteLength < 32 || bytes[0] !== 0xff || bytes[1] !== 0xd8
    || bytes.at(-2) !== 0xff || bytes.at(-1) !== 0xd9) return null;
  const frames = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  let offset = 2;
  let dimensions: { width: number; height: number } | null = null;
  let scan = false;
  while (offset < bytes.byteLength - 2) {
    if (bytes[offset] !== 0xff) return null;
    while (offset < bytes.byteLength && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset++]!;
    if (marker === 0xd9) break;
    if (marker === 0x00 || marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.byteLength) return null;
    const segmentLength = (bytes[offset]! << 8) | bytes[offset + 1]!;
    if (segmentLength < 2 || offset + segmentLength > bytes.byteLength) return null;
    if (frames.has(marker)) {
      if (segmentLength < 8) return null;
      dimensions = {
        height: (bytes[offset + 3]! << 8) | bytes[offset + 4]!,
        width: (bytes[offset + 5]! << 8) | bytes[offset + 6]!,
      };
    }
    if (marker === 0xda) { scan = true; break; }
    offset += segmentLength;
  }
  return dimensions && scan && imageDimensionsSafe(dimensions.width, dimensions.height) ? dimensions : null;
}

function pngDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.byteLength < 45 || signature.some((byte, index) => bytes[index] !== byte)) return null;
  let offset = 8;
  let dimensions: { width: number; height: number } | null = null;
  let foundData = false;
  let foundEnd = false;
  while (offset + 12 <= bytes.byteLength) {
    const length = uint32BigEndian(bytes, offset);
    const type = ascii(bytes, offset + 4, offset + 8);
    const next = offset + 12 + length;
    if (length > bytes.byteLength || next > bytes.byteLength) return null;
    if (!dimensions) {
      if (type !== "IHDR" || length !== 13) return null;
      dimensions = { width: uint32BigEndian(bytes, offset + 8), height: uint32BigEndian(bytes, offset + 12) };
    } else if (type === "IHDR") return null;
    if (type === "IDAT") foundData = true;
    if (type === "IEND") {
      if (length !== 0 || next !== bytes.byteLength) return null;
      foundEnd = true;
      break;
    }
    offset = next;
  }
  return dimensions && foundData && foundEnd && imageDimensionsSafe(dimensions.width, dimensions.height) ? dimensions : null;
}

function webpDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.byteLength < 30 || ascii(bytes, 0, 4) !== "RIFF" || ascii(bytes, 8, 12) !== "WEBP"
    || uint32LittleEndian(bytes, 4) !== bytes.byteLength - 8) return null;
  let offset = 12;
  let dimensions: { width: number; height: number } | null = null;
  while (offset + 8 <= bytes.byteLength) {
    const type = ascii(bytes, offset, offset + 4);
    const length = uint32LittleEndian(bytes, offset + 4);
    const data = offset + 8;
    const next = data + length + (length & 1);
    if (length > bytes.byteLength || next > bytes.byteLength) return null;
    if (type === "VP8X" && length >= 10) {
      dimensions = {
        width: 1 + bytes[data + 4]! + (bytes[data + 5]! << 8) + (bytes[data + 6]! << 16),
        height: 1 + bytes[data + 7]! + (bytes[data + 8]! << 8) + (bytes[data + 9]! << 16),
      };
    } else if (type === "VP8L" && length >= 5 && bytes[data] === 0x2f) {
      dimensions = {
        width: 1 + bytes[data + 1]! + ((bytes[data + 2]! & 0x3f) << 8),
        height: 1 + ((bytes[data + 2]! & 0xc0) >> 6) + (bytes[data + 3]! << 2) + ((bytes[data + 4]! & 0x0f) << 10),
      };
    } else if (type === "VP8 " && length >= 10
      && bytes[data + 3] === 0x9d && bytes[data + 4] === 0x01 && bytes[data + 5] === 0x2a) {
      dimensions = {
        width: (bytes[data + 6]! | (bytes[data + 7]! << 8)) & 0x3fff,
        height: (bytes[data + 8]! | (bytes[data + 9]! << 8)) & 0x3fff,
      };
    }
    offset = next;
  }
  return dimensions && offset === bytes.byteLength && imageDimensionsSafe(dimensions.width, dimensions.height) ? dimensions : null;
}

export function validateAiSpaceImageBytes(bytes: Uint8Array): Omit<ValidatedGeneratedImage, "bytes" | "providerRequestId" | "usageJson"> {
  if (bytes.byteLength === 0 || bytes.byteLength > AI_SPACE_LIMITS.maximumImageBytes) {
    throw new Error("生成图片为空或超过 6 MiB 上限");
  }
  const png = pngDimensions(bytes);
  if (png) return { mimeType: "image/png", extension: "png", ...png };
  const jpeg = jpegDimensions(bytes);
  if (jpeg) return { mimeType: "image/jpeg", extension: "jpg", ...jpeg };
  const webp = webpDimensions(bytes);
  if (webp) return { mimeType: "image/webp", extension: "webp", ...webp };
  throw new Error("生成结果不是结构完整、像素安全的 JPEG、PNG 或 WebP 图片");
}

let crc32Table: Uint32Array | undefined;

function pngCrc32(bytes: Uint8Array): number {
  if (!crc32Table) {
    crc32Table = Uint32Array.from({ length: 256 }, (_, value) => {
      let crc = value;
      for (let index = 0; index < 8; index += 1) crc = (crc & 1) ? (0xedb88320 ^ (crc >>> 1)) : (crc >>> 1);
      return crc >>> 0;
    });
  }
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crc32Table[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

async function validateDecodablePng(bytes: Uint8Array, width: number, height: number): Promise<void> {
  let offset = 8;
  let bitDepth = 0;
  let colorType = -1;
  let compression = -1;
  let filterMethod = -1;
  let interlace = -1;
  const compressedParts: Uint8Array[] = [];
  while (offset + 12 <= bytes.byteLength) {
    const length = uint32BigEndian(bytes, offset);
    const dataStart = offset + 8;
    const crcOffset = dataStart + length;
    const next = crcOffset + 4;
    if (length > bytes.byteLength || next > bytes.byteLength) throw new Error("PNG 数据块越界");
    const expectedCrc = uint32BigEndian(bytes, crcOffset);
    if (pngCrc32(bytes.subarray(offset + 4, crcOffset)) !== expectedCrc) throw new Error("PNG 数据块 CRC 校验失败");
    const type = ascii(bytes, offset + 4, offset + 8);
    if (type === "IHDR") {
      bitDepth = bytes[dataStart + 8]!;
      colorType = bytes[dataStart + 9]!;
      compression = bytes[dataStart + 10]!;
      filterMethod = bytes[dataStart + 11]!;
      interlace = bytes[dataStart + 12]!;
    } else if (type === "IDAT") {
      compressedParts.push(bytes.subarray(dataStart, crcOffset));
    }
    offset = next;
  }
  const channels = colorType === 0 ? 1 : colorType === 2 ? 3 : colorType === 4 ? 2 : colorType === 6 ? 4 : 0;
  if (bitDepth !== 8 || channels === 0 || compression !== 0 || filterMethod !== 0 || interlace !== 0) {
    throw new Error("AI 空间首版仅接收 8 位、非隔行的标准 PNG 图片");
  }
  if (compressedParts.length === 0) throw new Error("PNG 缺少像素数据");
  const compressedLength = compressedParts.reduce((total, part) => total + part.byteLength, 0);
  const compressed = new Uint8Array(compressedLength);
  let compressedOffset = 0;
  for (const part of compressedParts) {
    compressed.set(part, compressedOffset);
    compressedOffset += part.byteLength;
  }
  const rowBytes = width * channels;
  const expectedBytes = height * (rowBytes + 1);
  let decodedBytes = 0;
  let rowOffset = 0;
  try {
    const reader = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      for (const byte of chunk.value) {
        if (rowOffset === 0 && byte > 4) throw new Error("PNG 扫描线过滤器无效");
        rowOffset = (rowOffset + 1) % (rowBytes + 1);
        decodedBytes += 1;
        if (decodedBytes > expectedBytes) throw new Error("PNG 解压数据超过声明尺寸");
      }
    }
  } catch (error) {
    throw new Error(error instanceof Error ? `PNG 像素解码失败：${error.message}` : "PNG 像素解码失败");
  }
  if (decodedBytes !== expectedBytes || rowOffset !== 0) throw new Error("PNG 解压数据与声明尺寸不一致");
}

export async function validateAiSpaceImageBytesDeep(bytes: Uint8Array) {
  const image = validateAiSpaceImageBytes(bytes);
  if (image.mimeType !== "image/png") throw new Error("AI 空间首版仅接收经过完整解码校验的 PNG 图片");
  await validateDecodablePng(bytes, image.width, image.height);
  return image;
}

function decodeProviderBase64(value: unknown): Uint8Array {
  if (typeof value !== "string" || !value || value.length > Math.ceil(AI_SPACE_LIMITS.maximumImageBytes * 4 / 3) + 16) {
    throw new Error("图片生成服务未返回安全的 base64 图片");
  }
  let binary: string;
  try {
    binary = atob(value);
  } catch {
    throw new Error("图片生成服务返回的 base64 无效");
  }
  if (binary.length > AI_SPACE_LIMITS.maximumImageBytes) throw new Error("生成图片超过 6 MiB 上限");
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function boundedProviderUsageJson(value: unknown): string {
  const usage = recordValue(value);
  if (!usage) return "{}";
  const summary: Record<string, number | Record<string, number>> = {};
  for (const key of ["input_tokens", "output_tokens", "total_tokens"]) {
    const count = usage[key];
    if (typeof count === "number" && Number.isSafeInteger(count) && count >= 0) summary[key] = count;
  }
  const details = recordValue(usage.input_tokens_details);
  if (details) {
    const detailSummary: Record<string, number> = {};
    for (const key of ["text_tokens", "image_tokens"]) {
      const count = details[key];
      if (typeof count === "number" && Number.isSafeInteger(count) && count >= 0) detailSummary[key] = count;
    }
    if (Object.keys(detailSummary).length > 0) summary.input_tokens_details = detailSummary;
  }
  return JSON.stringify(summary);
}

type PreparedAiSpaceProviderRequest = {
  endpoint: string;
  apiKey: string;
  requestBody: string;
  timeoutMs: number;
};

async function prepareAiSpaceProviderRequest(context: RunnerContextRow): Promise<PreparedAiSpaceProviderRequest> {
  const apiKey = await decryptSecret(context.profile_api_key_encrypted);
  if (!apiKey) throw new Error("图片生成模型密钥不可用");
  const endpoint = resolveAiImageGenerationEndpointUrl(context.profile_base_url, await loadAiEndpointSecurityContext());
  const requestBody: Record<string, unknown> = {
    model: context.model_name,
    prompt: context.final_prompt,
    n: 1,
    size: context.size,
  };
  if (/^dall-e-(?:2|3)$/i.test(context.model_name)) requestBody.response_format = "b64_json";
  return {
    endpoint,
    apiKey,
    requestBody: JSON.stringify(requestBody),
    timeoutMs: integerInRange(Number(context.profile_timeout_ms), "模型超时", 3_000, 120_000),
  };
}

async function callAiSpaceImageProvider(
  context: RunnerContextRow,
  prepared: PreparedAiSpaceProviderRequest,
  fetcher: typeof fetch,
): Promise<ValidatedGeneratedImage> {
  const bounded = await fetchBoundedJson({
    url: prepared.endpoint,
    init: {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${prepared.apiKey}` },
      body: prepared.requestBody,
    },
    timeoutMs: prepared.timeoutMs,
    maxBytes: AI_SPACE_LIMITS.maximumProviderResponseBytes,
    fetcher,
  });
  if (!bounded.response.ok) throw new Error(`图片生成服务返回 HTTP ${bounded.response.status}`);
  const payload = recordValue(bounded.data);
  const data = Array.isArray(payload?.data) ? payload?.data : [];
  const first = recordValue(data[0]);
  const bytes = decodeProviderBase64(first?.b64_json);
  const structure = validateAiSpaceImageBytes(bytes);
  const [expectedWidth, expectedHeight] = context.size.split("x").map(Number);
  if (structure.width !== expectedWidth || structure.height !== expectedHeight) {
    throw new Error(`图片尺寸与任务要求不一致：期望 ${context.size}`);
  }
  const image = await validateAiSpaceImageBytesDeep(bytes);
  const rawRequestId = bounded.response.headers.get("x-request-id")
    ?? bounded.response.headers.get("request-id")
    ?? bounded.response.headers.get("openai-request-id")
    ?? "";
  const providerRequestId = /^[a-zA-Z0-9._:-]{1,160}$/.test(rawRequestId) ? rawRequestId : "";
  return { bytes, providerRequestId, usageJson: boundedProviderUsageJson(payload?.usage), ...image };
}

async function acquireAiSpaceItemLease(db: D1Database): Promise<AiSpaceItemLease | null> {
  const expired = await db.prepare(`SELECT id, job_id, dispatch_started_at, pending_object_key FROM ai_space_job_items
    WHERE status = 'running' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP
    LIMIT 100`).all<{ id: string; job_id: string; dispatch_started_at: string | null; pending_object_key: string }>();
  const pendingCleanup = (expired.results ?? [])
    .filter((row) => row.pending_object_key.startsWith("ai-space/v1/"))
    .map((row) => db.prepare(`INSERT INTO ai_space_asset_cleanup_queue (object_key, attempt_count, last_error, updated_at)
      VALUES (?, 0, 'expired_dispatch_pending_object', CURRENT_TIMESTAMP)
      ON CONFLICT(object_key) DO UPDATE SET last_error = excluded.last_error, updated_at = CURRENT_TIMESTAMP`)
      .bind(row.pending_object_key));
  await db.batch([
    ...pendingCleanup,
    db.prepare(`UPDATE ai_space_job_items SET status = 'failed',
        error_code = 'dispatch_state_unknown',
        error_message = '上次图片生成派发状态不确定，为避免重复付费未自动重试',
        lease_token = '', lease_expires_at = NULL, pending_object_key = '', completed_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
      WHERE status = 'running' AND dispatch_started_at IS NOT NULL
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP`),
    db.prepare(`UPDATE ai_space_job_items SET status = 'queued',
        error_code = 'claim_expired_before_dispatch', error_message = '派发前租约失效，已安全重新排队',
        lease_token = '', lease_expires_at = NULL, pending_object_key = '', dispatch_started_at = NULL,
        updated_at = CURRENT_TIMESTAMP
      WHERE status = 'running' AND dispatch_started_at IS NULL
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) <= CURRENT_TIMESTAMP`),
  ]);
  const cancelled = await db.prepare(`SELECT DISTINCT item.job_id FROM ai_space_job_items item
    JOIN ai_space_jobs job ON job.id = item.job_id
    WHERE item.status = 'queued' AND job.cancel_requested = 1 LIMIT 100`).all<{ job_id: string }>();
  await db.prepare(`UPDATE ai_space_job_items SET status = 'cancelled',
      error_code = 'cancelled_by_user', error_message = '用户已取消尚未派发的图片',
      completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE status = 'queued' AND EXISTS (
      SELECT 1 FROM ai_space_jobs job WHERE job.id = ai_space_job_items.job_id AND job.cancel_requested = 1
    )`).run();
  for (const jobId of new Set([...(expired.results ?? []).map((row) => row.job_id), ...(cancelled.results ?? []).map((row) => row.job_id)])) {
    await refreshAiSpaceJobAggregate(jobId, db);
  }
  const leaseToken = crypto.randomUUID();
  const row = await db.prepare(`UPDATE ai_space_job_items SET
      status = 'running', attempt_count = attempt_count + 1, lease_token = ?, lease_epoch = lease_epoch + 1,
      lease_expires_at = datetime('now', '+${AI_SPACE_LIMITS.leaseSeconds} seconds'),
      started_at = COALESCE(started_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
    WHERE id = (
      SELECT item.id FROM ai_space_job_items item JOIN ai_space_jobs job ON job.id = item.job_id
      WHERE item.status = 'queued' AND job.status IN ('queued','running') AND job.cancel_requested = 0
        AND NOT EXISTS (
          SELECT 1 FROM ai_space_job_items sibling WHERE sibling.job_id = item.job_id AND sibling.status = 'running'
        )
      ORDER BY job.created_at, item.ordinal LIMIT 1
    ) AND status = 'queued'
    RETURNING id, job_id, ordinal, lease_token, lease_epoch`)
    .bind(leaseToken).first<{ id: string; job_id: string; ordinal: number; lease_token: string; lease_epoch: number }>();
  if (!row) return null;
  await db.prepare(`UPDATE ai_space_jobs SET status = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
    updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('queued','running')`).bind(row.job_id).run();
  return { itemId: row.id, jobId: row.job_id, ordinal: Number(row.ordinal), leaseToken: row.lease_token, leaseEpoch: Number(row.lease_epoch) };
}

async function runnerContext(lease: AiSpaceItemLease, db: D1Database): Promise<RunnerContextRow | null> {
  return db.prepare(`SELECT ${qualifiedJobColumns},
      p.base_url profile_base_url, p.api_key_encrypted profile_api_key_encrypted,
      p.status profile_status, p.version profile_version, p.timeout_ms profile_timeout_ms
    FROM ai_space_jobs j JOIN ai_space_model_profiles p ON p.id = j.model_profile_id
    WHERE j.id = ? AND EXISTS (
      SELECT 1 FROM ai_space_job_items item WHERE item.id = ? AND item.job_id = j.id
        AND item.status = 'running' AND item.lease_token = ? AND item.lease_epoch = ?
        AND datetime(item.lease_expires_at) > CURRENT_TIMESTAMP
    ) LIMIT 1`).bind(lease.jobId, lease.itemId, lease.leaseToken, lease.leaseEpoch).first<RunnerContextRow>();
}

async function authorizeAiSpaceDispatch(context: RunnerContextRow): Promise<{ role: string } | null> {
  const authorization = await resolveAiBackgroundPrincipal(context.owner_email, context.scope_json);
  return authorization.ok ? { role: authorization.actorRole } : null;
}

async function beginAiSpaceDispatch(
  lease: AiSpaceItemLease,
  context: RunnerContextRow,
  actorRole: string,
  db: D1Database,
): Promise<{ dispatchId: string } | { errorCode: "dispatch_quota_exceeded" | "dispatch_state_unknown" }> {
  const dispatchId = `ai-space-dispatch-${lease.itemId}`;
  const results = await db.batch([
    db.prepare(`INSERT OR IGNORE INTO ai_space_dispatch_receipts (
        id, item_id, job_id, owner_email, actor_role, model_profile_id, model_profile_version,
        model_name, scene, size, prompt_digest
      ) SELECT ?, item.id, job.id, job.owner_email, ?, job.model_profile_id, job.model_profile_version,
          job.model_name, job.scene, job.size, job.prompt_digest
        FROM ai_space_job_items item JOIN ai_space_jobs job ON job.id = item.job_id
      WHERE item.id = ? AND item.job_id = ? AND item.status = 'running'
        AND item.lease_token = ? AND item.lease_epoch = ? AND item.dispatch_started_at IS NULL
        AND datetime(item.lease_expires_at) > CURRENT_TIMESTAMP
        AND (SELECT COUNT(*) FROM ai_space_dispatch_receipts
          WHERE owner_email = job.owner_email AND date(dispatched_at, '+8 hours') = date('now', '+8 hours')) < ?
        AND (SELECT COUNT(*) FROM ai_space_dispatch_receipts
          WHERE date(dispatched_at, '+8 hours') = date('now', '+8 hours')) < ?
        AND (SELECT COUNT(*) FROM ai_space_dispatch_receipts
          WHERE model_profile_id = job.model_profile_id AND date(dispatched_at, '+8 hours') = date('now', '+8 hours')) < ?`)
      .bind(
        dispatchId, actorRole, lease.itemId, lease.jobId, lease.leaseToken, lease.leaseEpoch,
        AI_SPACE_LIMITS.maximumDailyImagesPerOwner,
        AI_SPACE_LIMITS.maximumDailyDispatchesGlobal,
        AI_SPACE_LIMITS.maximumDailyDispatchesPerProfile,
      ),
    db.prepare(`UPDATE ai_space_job_items SET dispatch_started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND job_id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
        AND dispatch_started_at IS NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP
        AND EXISTS (SELECT 1 FROM ai_space_dispatch_receipts WHERE id = ? AND item_id = ai_space_job_items.id)`)
      .bind(lease.itemId, lease.jobId, lease.leaseToken, lease.leaseEpoch, dispatchId),
  ]) as Array<unknown>;
  if (changes(results[0]) === 1 && changes(results[1]) === 1) return { dispatchId };
  const receipt = await db.prepare("SELECT id FROM ai_space_dispatch_receipts WHERE item_id = ? LIMIT 1")
    .bind(lease.itemId).first<{ id: string }>();
  return { errorCode: receipt ? "dispatch_state_unknown" : "dispatch_quota_exceeded" };
}

async function recordAiSpaceDispatchResult(input: {
  dispatchId: string;
  status: "succeeded" | "failed";
  providerRequestId?: string;
  errorCode?: string;
  usageJson?: string;
}, db: D1Database): Promise<void> {
  const result = await db.prepare(`INSERT OR IGNORE INTO ai_space_dispatch_results
      (dispatch_id, status, provider_request_id, error_code, usage_json)
    SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM ai_space_dispatch_receipts WHERE id = ?)`)
    .bind(
      input.dispatchId,
      input.status,
      input.providerRequestId?.slice(0, 160) ?? "",
      input.errorCode?.slice(0, 80) ?? "",
      input.usageJson ?? "{}",
      input.dispatchId,
    ).run();
  if (changes(result) !== 1) throw new Error("AI 空间派发结果审计写入失败");
}

async function failAiSpaceItemLease(
  lease: AiSpaceItemLease,
  code: string,
  message: string,
  startedAt: number,
  db: D1Database,
) {
  const result = await db.prepare(`UPDATE ai_space_job_items SET status = 'failed',
      error_code = ?, error_message = ?, duration_ms = ?, lease_token = '', lease_expires_at = NULL,
      pending_object_key = '', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
    WHERE id = ? AND job_id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
      AND datetime(lease_expires_at) > CURRENT_TIMESTAMP`)
    .bind(code.slice(0, 80), message.slice(0, 300), Math.max(0, Date.now() - startedAt),
      lease.itemId, lease.jobId, lease.leaseToken, lease.leaseEpoch).run();
  await refreshAiSpaceJobAggregate(lease.jobId, db);
  return changes(result) === 1;
}

function providerFailure(error: unknown): { code: string; message: string } {
  if (error instanceof BoundedFetchError) {
    if (error.code === "timeout") return { code: "provider_timeout_unknown", message: "图片生成请求超时；为避免重复付费未自动重试" };
    if (error.code === "response_too_large") return { code: "provider_response_too_large", message: "图片生成服务响应超过安全上限" };
    if (error.code === "redirect") return { code: "provider_redirect_rejected", message: "图片生成接口发生重定向，已安全拒绝" };
    return { code: "provider_request_cancelled", message: "图片生成请求已取消" };
  }
  const message = error instanceof Error ? error.message : "图片生成失败";
  if (message.includes("密钥")) return { code: "provider_credentials_unavailable", message: "图片生成模型凭据不可用" };
  if (message.includes("base64") || message.includes("JPEG") || message.includes("PNG") || message.includes("WebP")
    || message.includes("6 MiB") || message.includes("图片尺寸")) {
    return { code: "provider_image_invalid", message: "图片生成服务返回的文件未通过安全校验" };
  }
  if (/HTTP \d{3}/.test(message)) return { code: "provider_http_error", message: message.slice(0, 120) };
  return { code: "provider_failed", message: "图片生成服务调用失败" };
}

async function queueAiSpaceObjectCleanup(db: D1Database, objectKey: string, error: unknown) {
  if (!objectKey.startsWith("ai-space/v1/")) return;
  const errorName = error instanceof Error ? error.name : "cleanup_failed";
  await db.prepare(`INSERT INTO ai_space_asset_cleanup_queue (object_key, attempt_count, last_error)
    VALUES (?, 1, ?) ON CONFLICT(object_key) DO UPDATE SET
      attempt_count = ai_space_asset_cleanup_queue.attempt_count + 1,
      last_error = excluded.last_error, updated_at = CURRENT_TIMESTAMP`)
    .bind(objectKey, errorName.slice(0, 120)).run().catch(() => undefined);
}

async function deleteAiSpaceObjectOrQueue(db: D1Database, bucket: R2Bucket, objectKey: string) {
  try {
    await bucket.delete(objectKey);
  } catch (error) {
    await queueAiSpaceObjectCleanup(db, objectKey, error);
  }
}

async function drainAiSpaceObjectCleanup(db: D1Database, bucket: R2Bucket, limit = 10) {
  const rows = await db.prepare("SELECT object_key FROM ai_space_asset_cleanup_queue ORDER BY updated_at LIMIT ?")
    .bind(limit).all<{ object_key: string }>();
  let deleted = 0;
  for (const row of rows.results ?? []) {
    if (!row.object_key.startsWith("ai-space/v1/")) continue;
    try {
      await bucket.delete(row.object_key);
      await db.prepare("DELETE FROM ai_space_asset_cleanup_queue WHERE object_key = ?").bind(row.object_key).run();
      deleted += 1;
    } catch (error) {
      await queueAiSpaceObjectCleanup(db, row.object_key, error);
    }
  }
  return deleted;
}

async function publishAiSpaceImage(
  lease: AiSpaceItemLease,
  context: RunnerContextRow,
  generated: ValidatedGeneratedImage,
  startedAt: number,
  db: D1Database,
  bucket: R2Bucket,
) {
  const [expectedWidth, expectedHeight] = context.size.split("x").map(Number);
  if (generated.width !== expectedWidth || generated.height !== expectedHeight) {
    throw new Error(`图片尺寸与任务要求不一致：期望 ${context.size}`);
  }
  const contentHash = await sha256(generated.bytes);
  const assetId = `ai-space-asset-${crypto.randomUUID()}`;
  const tokenSegment = lease.leaseToken.replace(/-/g, "").slice(0, 12);
  const objectKey = `ai-space/v1/${lease.jobId}/${lease.ordinal}-${tokenSegment}-${contentHash.slice(0, 16)}.${generated.extension}`;
  const reservation = await db.prepare(`UPDATE ai_space_job_items SET pending_object_key = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ? AND job_id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
      AND dispatch_started_at IS NOT NULL AND datetime(lease_expires_at) > CURRENT_TIMESTAMP`)
    .bind(objectKey, lease.itemId, lease.jobId, lease.leaseToken, lease.leaseEpoch).run();
  if (changes(reservation) !== 1) return false;
  let stored: R2Object | null;
  try {
    await bucket.put(objectKey, generated.bytes, {
      httpMetadata: { contentType: generated.mimeType, cacheControl: "private, max-age=31536000, immutable" },
      customMetadata: { source: "ai-space", sha256: contentHash, jobId: lease.jobId, itemId: lease.itemId },
    });
    stored = await bucket.head(objectKey);
  } catch (error) {
    await deleteAiSpaceObjectOrQueue(db, bucket, objectKey);
    throw error;
  }
  if (!stored || Number(stored.size) !== generated.bytes.byteLength
    || stored.httpMetadata?.contentType !== generated.mimeType
    || stored.customMetadata?.sha256 !== contentHash) {
    await deleteAiSpaceObjectOrQueue(db, bucket, objectKey);
    throw new Error("AI 空间图片写入后回查失败");
  }
  let writes: Array<unknown>;
  try {
    writes = await db.batch([
      db.prepare(`INSERT INTO ai_space_assets (
          id, job_id, item_id, owner_email, scope_json, scene, object_key, content_sha256,
          mime_type, byte_size, width, height
        ) SELECT ?, job.id, item.id, job.owner_email, job.scope_json, job.scene, ?, ?, ?, ?, ?, ?
        FROM ai_space_job_items item JOIN ai_space_jobs job ON job.id = item.job_id
        WHERE item.id = ? AND item.job_id = ? AND item.status = 'running'
          AND item.lease_token = ? AND item.lease_epoch = ? AND datetime(item.lease_expires_at) > CURRENT_TIMESTAMP`)
        .bind(assetId, objectKey, contentHash, generated.mimeType, generated.bytes.byteLength,
          generated.width, generated.height, lease.itemId, lease.jobId, lease.leaseToken, lease.leaseEpoch),
      db.prepare(`UPDATE ai_space_job_items SET status = 'succeeded', asset_id = ?, provider_request_id = ?,
          duration_ms = ?, error_code = '', error_message = '', lease_token = '', lease_expires_at = NULL,
          pending_object_key = '', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND job_id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?
          AND EXISTS (SELECT 1 FROM ai_space_assets asset WHERE asset.id = ? AND asset.item_id = ai_space_job_items.id)`)
        .bind(assetId, generated.providerRequestId, Math.max(0, Date.now() - startedAt),
          lease.itemId, lease.jobId, lease.leaseToken, lease.leaseEpoch, assetId),
    ]) as Array<unknown>;
  } catch (error) {
    await deleteAiSpaceObjectOrQueue(db, bucket, objectKey);
    throw error;
  }
  if (changes(writes[0]) !== 1 || changes(writes[1]) !== 1) {
    await db.prepare("DELETE FROM ai_space_assets WHERE id = ? AND item_id = ? AND object_key = ?")
      .bind(assetId, lease.itemId, objectKey).run().catch(() => undefined);
    await deleteAiSpaceObjectOrQueue(db, bucket, objectKey);
    return false;
  }
  await db.prepare(`UPDATE ai_space_model_profiles SET last_success_result = '真实图片生成成功',
    last_success_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND version = ?`)
    .bind(context.model_profile_id, context.model_profile_version).run();
  await refreshAiSpaceJobAggregate(lease.jobId, db);
  return true;
}

export async function runScheduledAiSpace(input: {
  db?: D1Database;
  bucket?: R2Bucket;
  fetcher?: typeof fetch;
} = {}) {
  const db = input.db ?? getD1Database();
  await ensureAiSpaceSchema(db);
  const bucket = input.bucket ?? await aiSpaceBucket();
  const cleaned = await drainAiSpaceObjectCleanup(db, bucket);
  const lease = await acquireAiSpaceItemLease(db);
  if (!lease) return { status: "idle" as const, cleaned };
  const startedAt = Date.now();
  const context = await runnerContext(lease, db);
  if (!context) {
    await failAiSpaceItemLease(lease, "lease_context_lost", "任务租约上下文已失效", startedAt, db);
    return { status: "lost" as const, jobId: lease.jobId, itemId: lease.itemId, cleaned };
  }
  if (context.profile_status !== "enabled") {
    await failAiSpaceItemLease(lease, "profile_disabled", "图片生成模型已停用", startedAt, db);
    return { status: "failed" as const, jobId: lease.jobId, itemId: lease.itemId, code: "profile_disabled", cleaned };
  }
  if (Number(context.profile_version) !== Number(context.model_profile_version)) {
    await failAiSpaceItemLease(lease, "profile_changed", "图片生成模型配置已变更，旧任务未派发", startedAt, db);
    return { status: "failed" as const, jobId: lease.jobId, itemId: lease.itemId, code: "profile_changed", cleaned };
  }
  const cancellation = await db.prepare("SELECT cancel_requested FROM ai_space_jobs WHERE id = ?")
    .bind(lease.jobId).first<{ cancel_requested: number }>();
  if (Boolean(cancellation?.cancel_requested)) {
    const result = await db.prepare(`UPDATE ai_space_job_items SET status = 'cancelled',
      error_code = 'cancelled_by_user', error_message = '用户在派发前取消任务',
      lease_token = '', lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_epoch = ?`)
      .bind(lease.itemId, lease.leaseToken, lease.leaseEpoch).run();
    await refreshAiSpaceJobAggregate(lease.jobId, db);
    return { status: changes(result) === 1 ? "cancelled" as const : "lost" as const, jobId: lease.jobId, itemId: lease.itemId, cleaned };
  }
  const authorization = await authorizeAiSpaceDispatch(context);
  if (!authorization) {
    await failAiSpaceItemLease(lease, "authorization_revoked", "任务所有者的账号、角色或数据范围已失效，未派发图片生成", startedAt, db);
    return { status: "failed" as const, jobId: lease.jobId, itemId: lease.itemId, code: "authorization_revoked", cleaned };
  }
  let prepared: PreparedAiSpaceProviderRequest;
  try {
    prepared = await prepareAiSpaceProviderRequest(context);
  } catch (error) {
    const failure = providerFailure(error);
    await failAiSpaceItemLease(lease, failure.code, failure.message, startedAt, db);
    return { status: "failed" as const, jobId: lease.jobId, itemId: lease.itemId, code: failure.code, cleaned };
  }
  const dispatch = await beginAiSpaceDispatch(lease, context, authorization.role, db);
  if ("errorCode" in dispatch) {
    const message = dispatch.errorCode === "dispatch_quota_exceeded"
      ? "今日图片生成实际派发额度已满，任务未调用供应商"
      : "图片生成派发状态不确定，为避免重复付费未自动重试";
    await failAiSpaceItemLease(lease, dispatch.errorCode, message, startedAt, db);
    return { status: "failed" as const, jobId: lease.jobId, itemId: lease.itemId, code: dispatch.errorCode, cleaned };
  }
  let dispatchResultRecorded = false;
  try {
    const generated = await callAiSpaceImageProvider(context, prepared, input.fetcher ?? fetch);
    await recordAiSpaceDispatchResult({
      dispatchId: dispatch.dispatchId,
      status: "succeeded",
      providerRequestId: generated.providerRequestId,
      usageJson: generated.usageJson,
    }, db);
    dispatchResultRecorded = true;
    const published = await publishAiSpaceImage(lease, context, generated, startedAt, db, bucket);
    return { status: published ? "succeeded" as const : "lost" as const, jobId: lease.jobId, itemId: lease.itemId, cleaned };
  } catch (error) {
    const failure = providerFailure(error);
    if (!dispatchResultRecorded) {
      try {
        await recordAiSpaceDispatchResult({ dispatchId: dispatch.dispatchId, status: "failed", errorCode: failure.code }, db);
      } catch {
        failure.code = "dispatch_audit_failed";
        failure.message = "图片生成派发结果审计失败，任务已安全终止";
      }
    }
    const recorded = await failAiSpaceItemLease(lease, failure.code, failure.message, startedAt, db);
    return { status: recorded ? "failed" as const : "lost" as const, jobId: lease.jobId, itemId: lease.itemId, code: failure.code, cleaned };
  }
}
