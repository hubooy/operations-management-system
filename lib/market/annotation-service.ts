import { randomBytes, randomUUID } from "node:crypto";

import { ensureAnnotationSchema } from "@/lib/market/annotation-schema";
import { AnnotationAgentError } from "@/lib/market/annotation-agent-errors";
import { resolveAnnotationImageCandidates } from "@/lib/market/annotation-image";
import {
  defaultMarketAnnotationConcurrency,
  normalizeMarketAnnotationConcurrency,
  normalizeMarketAnnotationJobLimit,
  type MarketAnnotationExecutor,
} from "@/lib/market/annotation-limits";
import { listAnnotationModels, listPromptTextModels, priceOnlyAnnotationPrompt, runPromptTextCompletion, runVisionAnnotation, visionAnnotationTiming } from "../../tests/legacy/market/annotation-model";
import { AnnotationRunRetryController, annotationRetryDelayMs, type AnnotationRunRetrySnapshot } from "@/lib/market/annotation-retry";
import { systemPriceRecognitionPrompt } from "@/lib/market/default-taxonomy";
import { listMarketSubcategoryTaxonomy } from "@/lib/market/subcategory-taxonomy";
import {
  activationGate, digest, normalizeImagePriceCents, normalizeSegments, parseVisionAnnotation,
  stableStratifiedSample, validationMetrics,
} from "@/lib/market/annotation-types";
import type { MarketDatabase } from "@/lib/market/database";
import { ensureMarketMasterIdentities } from "@/lib/market/master-identity";
import { inheritConfirmedStandardSkuImagePrices } from "@/lib/market/schema-core";

type Actor = { email: string; role: string };
type PromptRow = { id: string; category: string; version: number; parent_id: string | null; source: string; status: string; segments_json: string; prompt_body: string; change_note: string; metrics_json: string; created_by: string; created_at: string; activated_by: string | null; activated_at: string | null };
type JobRow = { id: string; category: string; prompt_version_id: string; executor: string; model_id: string | null; local_model_name: string; work_key: string; reuse_status: string; reuse_started_at: string | null; status: string; total_count: number; completed_count: number; failed_count: number; reviewed_count: number; committed_count: number; created_by: string; created_at: string; started_at: string | null; completed_at: string | null; updated_at: string; commit_token_hash: string; commit_started_at: string | null; remaining_inference_count?: number };
type ItemRow = { id: string; job_id: string; category: string; scope: string; sku_code: string; ranking_dimension: string; month: string; image_content_sha256: string; product_name: string; brand: string; source_image_url: string; resolved_image_url: string; image_source: string; status: string; ai_segment: string; ai_image_price_cents: number | null; ai_price_type: string; ai_price_low_cents: number | null; ai_price_high_cents: number | null; ai_confidence_bps: number | null; ai_reason: string; model_input_bytes: number; image_load_ms: number; image_prepare_ms: number; model_call_ms: number; total_inference_ms: number; reviewed_segment: string; reviewed_image_price_cents: number | null; reviewed_price_type: string; reviewed_price_low_cents: number | null; reviewed_price_high_cents: number | null; selected: number; reviewed_by: string; reviewed_at: string | null; lease_token_hash: string; lease_agent_id: string; lease_expires_at: string | null; attempt_count: number; error_message: string; version: number; created_at: string; updated_at: string };
type ValidationSampleRow = { id: string; category: string; sku_code: string; product_name: string; brand: string; image_url: string; gold_segment: string; gold_image_price_cents: number | null };
type ValidationSnapshot = { id: string; skuCode: string; productName: string; brand: string; imageUrl: string; goldSegment: string; goldImagePriceCents: number | null };
type ValidationResultRow = { id: string; run_id: string; sample_id: string; prompt_version_id: string; status: string; predicted_segment: string; predicted_image_price_cents: number | null; confidence_bps: number | null; is_correct: number; error_message: string; sample_snapshot_json: string; claim_token_hash: string; lease_expires_at: string | null; attempt_count: number; updated_at: string };
type ReusableAnnotationRow = { id: string; category: string; scope: string; sku_code: string; ranking_dimension: string; month: string; image_content_sha256: string; ai_segment: string; ai_image_price_cents: number | null; ai_price_type: string; ai_price_low_cents: number | null; ai_price_high_cents: number | null; ai_confidence_bps: number | null; ai_reason: string; ai_raw_digest: string; resolved_image_url: string; image_source: string };
type ConcurrencySettingRow = { category: string; executor: MarketAnnotationExecutor; concurrency: number; updated_by: string; updated_at: string };
type CloudRunRow = { job_id: string; state: "running" | "paused" | "completed"; retry_state_json: string; next_run_at: string | null; lease_token_hash: string; lease_expires_at: string | null; last_failure_code: string; last_failure_message: string; last_started_at: string | null; last_heartbeat_at: string | null; completed_at: string | null; updated_at: string };

const promptColumns = "id, category, version, parent_id, source, status, segments_json, prompt_body, change_note, metrics_json, created_by, created_at, activated_by, activated_at";
const jobColumns = "id, category, prompt_version_id, executor, model_id, local_model_name, work_key, reuse_status, reuse_started_at, status, total_count, completed_count, failed_count, reviewed_count, committed_count, created_by, created_at, started_at, completed_at, updated_at, commit_token_hash, commit_started_at";
const itemColumns = "id, job_id, category, scope, sku_code, ranking_dimension, month, image_content_sha256, product_name, brand, source_image_url, resolved_image_url, image_source, status, ai_segment, ai_image_price_cents, ai_price_type, ai_price_low_cents, ai_price_high_cents, ai_confidence_bps, ai_reason, model_input_bytes, image_load_ms, image_prepare_ms, model_call_ms, total_inference_ms, reviewed_segment, reviewed_image_price_cents, reviewed_price_type, reviewed_price_low_cents, reviewed_price_high_cents, selected, reviewed_by, reviewed_at, lease_token_hash, lease_agent_id, lease_expires_at, attempt_count, error_message, version, created_at, updated_at";
// This is first-paint metadata. Drive from the unique snapshot identity and probe the
// current market set instead of sorting every ranking row into a window result.
export const annotationCandidateCountsSql = `WITH candidate_snapshots AS NOT MATERIALIZED (
  SELECT snapshot.category, snapshot.scope, snapshot.sku_code, snapshot.ranking_dimension, snapshot.month,
    COALESCE(NULLIF(snapshot.image_content_sha256,''), image_cache.content_sha256, '') image_content_sha256
  FROM market_price_snapshots snapshot
  LEFT JOIN market_image_cache image_cache
    ON image_cache.source_url=COALESCE(NULLIF(snapshot.image_url,''), (
      SELECT fallback.image_url
      FROM market_ranking_entries fallback INDEXED BY market_entries_representative_idx
      WHERE fallback.category=snapshot.category AND fallback.scope=snapshot.scope
        AND fallback.sku_code=snapshot.sku_code AND fallback.ranking_dimension=snapshot.ranking_dimension
        AND substr(fallback.period_end,1,7)=snapshot.month
      ORDER BY fallback.period_end DESC, fallback.updated_at DESC, fallback.id DESC
      LIMIT 1
    ))
    AND image_cache.status='ready' AND image_cache.content_sha256<>''
  WHERE snapshot.category<>'' AND snapshot.confirmed_market_price_cents IS NULL
    AND snapshot.ranking_dimension='SKU'
)
SELECT candidate.category value, COUNT(*) candidateCount
FROM candidate_snapshots candidate
WHERE candidate.image_content_sha256<>''
  AND NOT EXISTS (
    SELECT 1 FROM market_annotation_items existing_item
    WHERE existing_item.category=candidate.category AND existing_item.scope=candidate.scope
      AND existing_item.sku_code=candidate.sku_code AND existing_item.ranking_dimension=candidate.ranking_dimension
      AND existing_item.month=candidate.month AND existing_item.image_content_sha256=candidate.image_content_sha256
      AND (existing_item.status IN ('queued','claimed','inferencing','review_pending','approved','rejected','committed')
        OR existing_item.status='failed')
  )
  AND NOT EXISTS (
    SELECT 1 FROM market_price_snapshots standard
    WHERE standard.category=candidate.category AND standard.scope=candidate.scope
      AND standard.sku_code=candidate.sku_code AND standard.ranking_dimension=candidate.ranking_dimension
      AND standard.image_content_sha256=candidate.image_content_sha256
      AND standard.confirmed_market_price_cents IS NOT NULL AND standard.ai_price_type='标准售价'
  )
  AND EXISTS (
    SELECT 1 FROM market_ranking_entries current_market INDEXED BY market_entries_representative_idx
    WHERE current_market.category=candidate.category AND current_market.scope=candidate.scope
      AND current_market.sku_code=candidate.sku_code AND current_market.ranking_dimension=candidate.ranking_dimension
      AND substr(current_market.period_end,1,7)=candidate.month
  )
GROUP BY candidate.category
ORDER BY candidateCount DESC, value
LIMIT 200`;
const HISTORY_SAME_IMAGE_REVIEWER = "system:history_same_image";
const HISTORY_SAME_SKU_SEGMENT_REVIEWER = "system:history_same_sku_segment";

function json<T>(value: string, fallback: T): T { try { return JSON.parse(value) as T; } catch { return fallback; } }
async function assertPromptTaxonomyCurrent(db: MarketDatabase, category: string, segments: string[], message: string) {
  let taxonomy: string[];
  try { taxonomy = await listMarketSubcategoryTaxonomy(db, category); }
  catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    if (/no such table|does not exist/i.test(text)) return;
    throw error;
  }
  if (!taxonomy.length) return;
  const current = [...new Set(taxonomy)].sort();
  const prompt = [...new Set(segments)].sort();
  if (current.length !== prompt.length || current.some((value, index) => value !== prompt[index])) throw new Error(message);
}
function strictInteger(value: number | undefined, fallback: number, minimum: number, maximum: number, name: string) {
  const resolved = value === undefined ? fallback : value;
  if (!Number.isSafeInteger(resolved) || resolved < minimum || resolved > maximum) throw new Error(`${name} 必须是 ${minimum} 到 ${maximum} 的整数`);
  return resolved;
}
function snapshotView(value: string) {
  const snapshot = json<Partial<ValidationSnapshot>>(value, {});
  return { skuCode: snapshot.skuCode ?? "", productName: snapshot.productName ?? "", brand: snapshot.brand ?? "", goldSegment: snapshot.goldSegment ?? "", goldImagePriceCents: snapshot.goldImagePriceCents ?? null };
}
function safeOperationalError(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : fallback;
  if (/API Key|模型调用|模型接口|模型响应|图片|候选|快照|重建|Prompt|没有返回|枚举|confidence|价格/.test(message)) return message.slice(0, 300);
  return fallback;
}
async function ensureMarketSchemaLazy(db: MarketDatabase) {
  try {
    const { ensureMarketSchema } = await import("@/lib/market/database");
    await ensureMarketSchema(db);
  } catch (error) {
    if (!(error instanceof Error) || !/cloudflare:/.test(error.message)) throw error;
    const { ensureMarketSchemaCore } = await import("@/lib/market/schema-core");
    await ensureMarketSchemaCore(db);
  }
}
function promptValue(row: PromptRow) { return { id: row.id, category: row.category, version: row.version, parentId: row.parent_id, source: row.source, status: row.status, segments: json<string[]>(row.segments_json, []), promptBody: row.prompt_body, changeNote: row.change_note, metrics: json(row.metrics_json, {}), createdBy: row.created_by, createdAt: row.created_at, activatedBy: row.activated_by, activatedAt: row.activated_at }; }
function jobValue(row: JobRow) { return { id: row.id, category: row.category, promptVersionId: row.prompt_version_id, executor: row.executor, modelId: row.model_id, localModelName: row.local_model_name, status: row.status, totalCount: row.total_count, completedCount: row.completed_count, failedCount: row.failed_count, reviewedCount: row.reviewed_count, committedCount: row.committed_count, remainingInferenceCount: Number(row.remaining_inference_count ?? 0), createdBy: row.created_by, createdAt: row.created_at, startedAt: row.started_at, completedAt: row.completed_at, updatedAt: row.updated_at }; }
function itemValue(row: ItemRow) { return { id: row.id, candidateId: row.id, jobId: row.job_id, category: row.category, skuCode: row.sku_code, rankingDimension: row.ranking_dimension, month: row.month, imageContentSha256: row.image_content_sha256, productName: row.product_name, brand: row.brand, sourceImageUrl: row.source_image_url, resolvedImageUrl: row.resolved_image_url, imageSource: row.image_source, status: row.status, aiSegment: row.ai_segment, aiImagePriceCents: row.ai_image_price_cents, aiPriceType: row.ai_price_type, aiPriceLowCents: row.ai_price_low_cents, aiPriceHighCents: row.ai_price_high_cents, aiConfidenceBps: row.ai_confidence_bps, aiReason: row.ai_reason, modelInputBytes: row.model_input_bytes, imageLoadMs: row.image_load_ms, imagePrepareMs: row.image_prepare_ms, modelCallMs: row.model_call_ms, totalInferenceMs: row.total_inference_ms, reviewedSegment: row.reviewed_segment, reviewedImagePriceCents: row.reviewed_image_price_cents, reviewedPriceType: row.reviewed_price_type, reviewedPriceLowCents: row.reviewed_price_low_cents, reviewedPriceHighCents: row.reviewed_price_high_cents, reviewPriceSource: row.reviewed_by === HISTORY_SAME_IMAGE_REVIEWER ? "history_same_image" : (row.ai_segment || row.ai_image_price_cents !== null || row.ai_confidence_bps !== null || row.ai_reason) ? "ai" : "manual", selected: Boolean(row.selected), reviewedBy: row.reviewed_by, reviewedAt: row.reviewed_at, attemptCount: row.attempt_count, errorMessage: row.error_message, version: row.version, createdAt: row.created_at, updatedAt: row.updated_at }; }

const currentAnnotationSnapshotExistsSql = (itemAlias: string) => `EXISTS (
  SELECT 1 FROM market_price_snapshots snapshot
  LEFT JOIN market_image_cache current_image ON current_image.source_url=snapshot.image_url
    AND current_image.status='ready' AND current_image.content_sha256<>''
  WHERE snapshot.category=${itemAlias}.category AND snapshot.scope=${itemAlias}.scope
    AND snapshot.sku_code=${itemAlias}.sku_code AND snapshot.ranking_dimension=${itemAlias}.ranking_dimension
    AND snapshot.month=${itemAlias}.month
    AND COALESCE(NULLIF(current_image.content_sha256,''),snapshot.image_content_sha256)=${itemAlias}.image_content_sha256
    AND EXISTS (SELECT 1 FROM market_ranking_entries ranking
      WHERE ranking.category=snapshot.category AND ranking.scope=snapshot.scope
        AND ranking.sku_code=snapshot.sku_code AND ranking.ranking_dimension=snapshot.ranking_dimension
        AND (${itemAlias}.month='' OR substr(ranking.period_end,1,7)=snapshot.month))
)`;
const aiRecognitionClause = "(COALESCE(ai_segment,'')<>'' OR ai_image_price_cents IS NOT NULL OR ai_confidence_bps IS NOT NULL OR COALESCE(ai_reason,'')<>'')";
const MAX_FILTERED_SELECTION = 50_000;
const COMMIT_SELECTION_BATCH_SIZE = 500;
const STALE_REBUILD_BATCH_SIZE = 50;
const CLOUD_ANNOTATION_BATCH_MAX = 8;
const D1_BOUND_LIST_CHUNK = 80;
const CLOUD_REUSE_BATCH_SIZE = 40;

function chunks<T>(values: T[], size = D1_BOUND_LIST_CHUNK) {
  const result: T[][] = [];
  for (let offset = 0; offset < values.length; offset += size) result.push(values.slice(offset, offset + size));
  return result;
}

function inferenceUnitMatch(left: string, right: string) {
  return `${left}.category=${right}.category AND ${left}.scope=${right}.scope
    AND ${left}.sku_code=${right}.sku_code AND ${left}.ranking_dimension=${right}.ranking_dimension
    AND ${left}.image_content_sha256=${right}.image_content_sha256`;
}

function inferenceUnitLeaderClause(alias: string) {
  return `${alias}.id=(SELECT MIN(unit.id) FROM market_annotation_items unit
    WHERE unit.job_id=${alias}.job_id AND ${inferenceUnitMatch("unit", alias)})`;
}

function annotationJobWorkKey(input: { category: string; promptVersionId: string; executor: string; modelId?: string; localModelName?: string }) {
  return digest(JSON.stringify({
    category: input.category,
    promptVersionId: input.promptVersionId,
    executor: input.executor,
    modelId: input.executor === "cloud" ? input.modelId ?? "" : "",
    localModelName: input.executor === "local" ? input.localModelName?.trim().slice(0, 160) ?? "" : "",
  }));
}

function annotationExecutor(value: string): MarketAnnotationExecutor {
  if (value !== "cloud" && value !== "local") throw new Error("执行器必须是 cloud 或 local");
  return value;
}

async function annotationConcurrency(db: MarketDatabase, category: string, executor: MarketAnnotationExecutor) {
  const setting = await db.prepare("SELECT concurrency FROM market_annotation_concurrency_settings WHERE category=? AND executor=? LIMIT 1")
    .bind(category, executor).first<{ concurrency: number }>();
  return normalizeMarketAnnotationConcurrency(setting?.concurrency, executor);
}

function retrySnapshot(value: string) {
  return json<Partial<AnnotationRunRetrySnapshot>>(value, {});
}

function cloudRunValue(row: CloudRunRow, configuredConcurrency: number) {
  const retry = new AnnotationRunRetryController(configuredConcurrency, retrySnapshot(row.retry_state_json));
  return {
    jobId: row.job_id,
    state: row.state,
    runConcurrency: retry.workerLimit,
    targetConcurrency: retry.targetConcurrency,
    recovering: retry.recovering,
    nextRunAt: row.next_run_at,
    lastFailureCode: row.last_failure_code,
    lastFailureMessage: row.last_failure_message,
    lastStartedAt: row.last_started_at,
    lastHeartbeatAt: row.last_heartbeat_at,
    completedAt: row.completed_at,
    updatedAt: row.updated_at,
  };
}

async function ensureCloudRunControl(db: MarketDatabase, jobId: string, configuredConcurrency: number) {
  const retry = new AnnotationRunRetryController(configuredConcurrency);
  await db.prepare(`INSERT OR IGNORE INTO market_annotation_cloud_runs
      (job_id,state,retry_state_json,updated_at) VALUES (?,'paused',?,CURRENT_TIMESTAMP)`)
    .bind(jobId, JSON.stringify(retry.snapshot())).run();
}

async function getCloudRunControl(db: MarketDatabase, jobId: string, configuredConcurrency?: number) {
  const row = await db.prepare(`SELECT job_id,state,retry_state_json,next_run_at,lease_token_hash,lease_expires_at,
      last_failure_code,last_failure_message,last_started_at,last_heartbeat_at,completed_at,updated_at
    FROM market_annotation_cloud_runs WHERE job_id=? LIMIT 1`).bind(jobId).first<CloudRunRow>();
  if (!row) return null;
  const target = configuredConcurrency ?? await db.prepare(`SELECT COALESCE(setting.concurrency, ?) concurrency
      FROM market_annotation_jobs job LEFT JOIN market_annotation_concurrency_settings setting
        ON setting.category=job.category AND setting.executor='cloud'
      WHERE job.id=? LIMIT 1`).bind(defaultMarketAnnotationConcurrency("cloud"), jobId).first<{ concurrency: number }>();
  return cloudRunValue(row, normalizeMarketAnnotationConcurrency(typeof target === "number" ? target : target?.concurrency, "cloud"));
}

export async function setCloudAnnotationRunState(
  db: MarketDatabase,
  input: { jobId: string; state: "running" | "paused" },
  actor: Actor,
) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const jobId = input.jobId.trim();
  let job = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs WHERE id=? LIMIT 1`).bind(jobId).first<JobRow>();
  if (!job || job.executor !== "cloud") throw new Error("云端标注任务不存在");
  if (job.status === "committing") throw new Error("该任务正在入库，不能调整后台运行状态");
  if (input.state === "running") {
    await refreshJob(db, jobId);
    job = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs WHERE id=? LIMIT 1`).bind(jobId).first<JobRow>();
    if (!job) throw new Error("云端标注任务不存在");
  }
  if (["cancelled", "committed", "deleted"].includes(job.status)) throw new Error("该任务已经结束，不能调整后台运行状态");
  if (input.state === "running") {
    const retryable = job.reuse_status !== "ready" || Boolean(await db.prepare(`SELECT 1 ok FROM market_annotation_items
      WHERE job_id=? AND (status IN ('queued','claimed','inferencing') OR (status='failed' AND attempt_count<3)) LIMIT 1`)
      .bind(jobId).first<{ ok: number }>());
    if (!retryable) throw new Error("该任务没有可重试的 AI 推理项；如该类目仍显示可新建候选，请创建下一批任务");
  }
  const configured = await annotationConcurrency(db, job.category, "cloud");
  const before = await getCloudRunControl(db, jobId, configured);
  const retryJson = input.state === "running"
    ? JSON.stringify(new AnnotationRunRetryController(configured).snapshot())
    : JSON.stringify(retrySnapshot((await db.prepare("SELECT retry_state_json FROM market_annotation_cloud_runs WHERE job_id=?").bind(jobId).first<{ retry_state_json: string }>())?.retry_state_json ?? "{}"));
  await db.batch([
    db.prepare(`INSERT INTO market_annotation_cloud_runs
        (job_id,state,retry_state_json,next_run_at,lease_token_hash,lease_expires_at,last_failure_code,last_failure_message,completed_at,updated_at)
      VALUES (?,?,?,CASE WHEN ?='running' THEN CURRENT_TIMESTAMP ELSE NULL END,'',NULL,'','',NULL,CURRENT_TIMESTAMP)
      ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,
        retry_state_json=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.retry_state_json ELSE excluded.retry_state_json END,
        next_run_at=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.next_run_at ELSE excluded.next_run_at END,
        lease_token_hash=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.lease_token_hash ELSE '' END,
        lease_expires_at=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.lease_expires_at ELSE NULL END,
        last_failure_code=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.last_failure_code WHEN excluded.state='running' THEN '' ELSE market_annotation_cloud_runs.last_failure_code END,
        last_failure_message=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.last_failure_message WHEN excluded.state='running' THEN '' ELSE market_annotation_cloud_runs.last_failure_message END,
        completed_at=CASE WHEN excluded.state='running' AND market_annotation_cloud_runs.state='running'
          THEN market_annotation_cloud_runs.completed_at ELSE NULL END,updated_at=CURRENT_TIMESTAMP`)
      .bind(jobId, input.state, retryJson, input.state),
    db.prepare(`INSERT INTO market_master_audit_logs
        (id,actor_email,actor_role,action,entity_type,entity_id,before_json,after_json)
      VALUES (?,?,?,'set_market_annotation_cloud_run_state','market_annotation_cloud_run',?,?,?)`)
      .bind(`market-audit-${randomUUID()}`, actor.email, actor.role, jobId, JSON.stringify(before), JSON.stringify({ state: input.state, configuredConcurrency: configured })),
  ]);
  return getCloudRunControl(db, jobId, configured);
}

function annotationImportableClause(alias = "market_annotation_items", jobStatuses = ["review_ready"]) {
  const statuses = jobStatuses.map((status) => `'${status}'`).join(",");
  return `${alias}.status IN ('review_pending','approved','rejected') AND EXISTS (
    SELECT 1 FROM market_annotation_jobs import_job
    JOIN market_annotation_prompt_versions import_prompt ON import_prompt.id=import_job.prompt_version_id
    JOIN json_each(import_prompt.segments_json) import_segment
    WHERE import_job.id=${alias}.job_id AND import_job.status IN (${statuses})
      AND CAST(import_segment.value AS TEXT)=COALESCE(NULLIF(${alias}.reviewed_segment,''),${alias}.ai_segment)
  )`;
}

function annotationSelectedActionableClause(alias = "market_annotation_items") {
  return `((${annotationImportableClause(alias)}) OR (
    ${alias}.status='approved' AND ${alias}.selected=1
    AND EXISTS (SELECT 1 FROM market_annotation_jobs repair_job
      WHERE repair_job.id=${alias}.job_id AND repair_job.status IN ('running','review_ready'))
    AND NOT (${currentAnnotationSnapshotExistsSql(alias)})
  ))`;
}

function annotationCategoryList(values: string[] | undefined, legacy?: string) {
  const categories = [...new Set([...(values ?? []), legacy ?? ""].map((value) => value.trim().slice(0, 120)).filter(Boolean))];
  if (categories.length > 50) throw new Error("三级类目一次最多选择 50 个");
  return categories;
}

function annotationFilterList(values: string[] | undefined, legacy?: string, max = 50) {
  return [...new Set([...(values ?? []), legacy ?? ""].map((value) => value.trim().slice(0, 120)).filter(Boolean))].slice(0, max);
}

function addAnnotationReviewFilters(
  clauses: string[],
  bindings: unknown[],
  input: Pick<AnnotationWorkspaceInput, "itemSegment" | "itemSegments" | "storageStatus" | "storageStatuses" | "recognitionSource" | "recognitionSources">,
) {
  const segments = annotationFilterList(input.itemSegments, input.itemSegment);
  if (segments.length) {
    clauses.push(`COALESCE(NULLIF(reviewed_segment,''), ai_segment) IN (${segments.map(() => "?").join(",")})`);
    bindings.push(...segments);
  }
  const storageStatuses = annotationFilterList(input.storageStatuses, input.storageStatus).filter((value) => value === "pending" || value === "committed");
  if (storageStatuses.length === 1) clauses.push(storageStatuses[0] === "committed" ? "status='committed'" : "status<>'committed'");
  const recognitionSources = annotationFilterList(input.recognitionSources, input.recognitionSource).filter((value) => value === "ai" || value === "non_ai");
  if (recognitionSources.length === 1) clauses.push(recognitionSources[0] === "ai" ? aiRecognitionClause : `NOT ${aiRecognitionClause}`);
}

function annotationReviewScope(input: { jobId?: string; aggregateJobs?: boolean; itemCategory?: string; itemCategories?: string[] }) {
  const categories = annotationCategoryList(input.itemCategories, input.itemCategory);
  const visibleJobClause = "market_annotation_items.status<>'superseded' AND EXISTS (SELECT 1 FROM market_annotation_jobs visible_job WHERE visible_job.id=market_annotation_items.job_id AND visible_job.status<>'deleted')";
  if (input.aggregateJobs) return categories.length
    ? { clause: `${visibleJobClause} AND category IN (${categories.map(() => "?").join(",")})`, bindings: categories as unknown[] }
    : { clause: visibleJobClause, bindings: [] as unknown[] };
  return { clause: `job_id=? AND ${visibleJobClause}`, bindings: [input.jobId ?? ""] as unknown[] };
}

type AnnotationWorkspaceInput = {
  jobId?: string; q?: string; page?: number; pageSize?: number; itemPage?: number; itemPageSize?: number;
  aggregateJobs?: boolean; itemCategory?: string; itemCategories?: string[]; itemSegment?: string; itemSegments?: string[]; storageStatus?: "pending" | "committed"; storageStatuses?: string[]; recognitionSource?: "ai" | "non_ai"; recognitionSources?: string[]; includeAgents?: boolean; includeCatalog?: boolean; includeCandidateCounts?: boolean;
};

async function queryAnnotationReviewWorkspace(db: MarketDatabase, input: AnnotationWorkspaceInput = {}) {
  const itemPage = strictInteger(input.itemPage, 1, 1, 50_000, "itemPage");
  const itemPageSize = strictInteger(input.itemPageSize, 20, 10, 200, "itemPageSize");
  const reviewScope = annotationReviewScope(input);
  const itemClauses = [reviewScope.clause];
  const itemBindings: unknown[] = [...reviewScope.bindings];
  addAnnotationReviewFilters(itemClauses, itemBindings, input);
  const itemWhere = itemClauses.join(" AND ");
  const hasReviewScope = Boolean(input.aggregateJobs || input.jobId);
  const [items, itemCount, reviewSummary, selection] = await Promise.all([
    hasReviewScope ? db.prepare(`SELECT ${itemColumns} FROM market_annotation_items WHERE ${itemWhere} ORDER BY created_at DESC, id LIMIT ? OFFSET ?`).bind(...itemBindings, itemPageSize, (itemPage - 1) * itemPageSize).all<ItemRow>() : Promise.resolve({ results: [] as ItemRow[] }),
    hasReviewScope ? db.prepare(`SELECT COUNT(*) count FROM market_annotation_items WHERE ${itemWhere}`).bind(...itemBindings).first<{ count: number }>() : Promise.resolve({ count: 0 }),
    hasReviewScope ? db.prepare(`SELECT COUNT(DISTINCT job_id) jobCount, COUNT(*) recordCount,
      COUNT(DISTINCT category || char(31) || scope || char(31) || sku_code || char(31) || ranking_dimension || char(31) || month || char(31) || image_content_sha256) uniqueCandidateCount
      FROM market_annotation_items WHERE ${reviewScope.clause}`).bind(...reviewScope.bindings).first<{ jobCount: number; recordCount: number; uniqueCandidateCount: number }>() : Promise.resolve({ jobCount: 0, recordCount: 0, uniqueCandidateCount: 0 }),
    hasReviewScope ? db.prepare(`SELECT
      SUM(CASE WHEN ${annotationImportableClause()} THEN 1 ELSE 0 END) filteredReviewableCount,
      SUM(CASE WHEN selected=1 AND ${annotationSelectedActionableClause()} THEN 1 ELSE 0 END) filteredSelectedCount,
      (SELECT COUNT(*) FROM market_annotation_items WHERE ${reviewScope.clause} AND selected=1 AND ${annotationSelectedActionableClause()}) scopeSelectedCount
      FROM market_annotation_items WHERE ${itemWhere}`).bind(...reviewScope.bindings, ...itemBindings).first<{ filteredReviewableCount: number | null; filteredSelectedCount: number | null; scopeSelectedCount: number }>() : Promise.resolve({ filteredReviewableCount: 0, filteredSelectedCount: 0, scopeSelectedCount: 0 }),
  ]);
  return {
    items: (items.results ?? []).map(itemValue), itemPagination: { page: itemPage, pageSize: itemPageSize, total: Number(itemCount?.count ?? 0), pageCount: Math.max(1, Math.ceil(Number(itemCount?.count ?? 0) / itemPageSize)) },
    reviewSummary: { jobCount: Number(reviewSummary?.jobCount ?? 0), recordCount: Number(reviewSummary?.recordCount ?? 0), uniqueCandidateCount: Number(reviewSummary?.uniqueCandidateCount ?? 0) },
    selection: { filteredReviewableCount: Number(selection?.filteredReviewableCount ?? 0), filteredSelectedCount: Number(selection?.filteredSelectedCount ?? 0), scopeSelectedCount: Number(selection?.scopeSelectedCount ?? 0) },
  };
}

export async function getAnnotationReviewWorkspace(db: MarketDatabase, input: AnnotationWorkspaceInput = {}) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  return queryAnnotationReviewWorkspace(db, input);
}

export async function getAnnotationCatalogWorkspace(db: MarketDatabase, input: { q?: string; page?: number; pageSize?: number } = {}) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  await ensureMarketMasterIdentities(db);
  return searchAnnotationCatalog(db, input);
}

async function queryAnnotationCandidateCounts(db: MarketDatabase) {
  const rows = await db.prepare(annotationCandidateCountsSql).all<{ value: string; candidateCount: number }>();
  return (rows.results ?? []).map((row) => ({ value: row.value, candidateCount: Number(row.candidateCount ?? 0) }));
}

export async function getAnnotationCandidateCounts(db: MarketDatabase) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  return { categories: await queryAnnotationCandidateCounts(db) };
}

export async function setAnnotationConcurrency(db: MarketDatabase, input: { category: string; executor: string; concurrency?: number }, actor: Actor) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const category = input.category.trim().slice(0, 120);
  if (!category) throw new Error("请选择需要记忆并发数的三级类目");
  const executor = annotationExecutor(input.executor);
  const concurrency = normalizeMarketAnnotationConcurrency(input.concurrency, executor);
  const categoryExists = await db.prepare(`SELECT 1 ok WHERE
      EXISTS (SELECT 1 FROM market_ranking_entries WHERE category=? LIMIT 1)
      OR EXISTS (SELECT 1 FROM market_annotation_jobs WHERE category=? LIMIT 1)
      OR EXISTS (SELECT 1 FROM market_subcategory_taxonomy WHERE category=? LIMIT 1)`)
    .bind(category, category, category).first<{ ok: number }>();
  if (!categoryExists) throw new Error("所选三级类目不存在或尚未导入数据");
  const before = await db.prepare("SELECT category, executor, concurrency, updated_by, updated_at FROM market_annotation_concurrency_settings WHERE category=? AND executor=? LIMIT 1")
    .bind(category, executor).first<ConcurrencySettingRow>();
  const after = { category, executor, concurrency, updatedBy: actor.email };
  if (before?.concurrency === concurrency) return { ...after, updatedBy: before.updated_by, updatedAt: before.updated_at, unchanged: true };
  await db.batch([
    db.prepare(`INSERT INTO market_annotation_concurrency_settings
        (category,executor,concurrency,updated_by,updated_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)
      ON CONFLICT(category,executor) DO UPDATE SET
        concurrency=excluded.concurrency,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP`)
      .bind(category, executor, concurrency, actor.email),
    db.prepare(`INSERT INTO market_master_audit_logs
        (id,actor_email,actor_role,action,entity_type,entity_id,before_json,after_json)
      VALUES (?,?,?,'set_market_annotation_concurrency','market_annotation_concurrency',?,?,?)`)
      .bind(`market-audit-${randomUUID()}`, actor.email, actor.role, `${category}|${executor}`, JSON.stringify(before ?? null), JSON.stringify(after)),
  ]);
  return after;
}

export async function getAnnotationWorkspace(db: MarketDatabase, input: AnnotationWorkspaceInput = {}) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  if (input.includeCatalog !== false) await ensureMarketMasterIdentities(db);
  const page = Math.max(1, Math.trunc(input.page ?? 1));
  const pageSize = Math.max(10, Math.min(100, Math.trunc(input.pageSize ?? 30)));
  const q = input.q?.trim().slice(0, 120) ?? "";
  const [review, categoryRows, candidateCounts, reviewCategoryRows, taxonomyRows, promptRows, jobRows, concurrencyRows, cloudRunRows, models, textModels, catalog, runRows, agentRows, validationRows] = await Promise.all([
    queryAnnotationReviewWorkspace(db, input),
    db.prepare("SELECT category value, COUNT(DISTINCT sku_code) count FROM market_ranking_entries WHERE category <> '' GROUP BY category ORDER BY count DESC, value LIMIT 200").all<{ value: string; count: number }>(),
    input.includeCandidateCounts === false ? Promise.resolve(null) : queryAnnotationCandidateCounts(db),
    db.prepare("SELECT item.category value, COUNT(DISTINCT item.job_id) jobCount, COUNT(*) recordCount FROM market_annotation_items item JOIN market_annotation_jobs job ON job.id=item.job_id WHERE item.category<>'' AND job.status<>'deleted' GROUP BY item.category ORDER BY jobCount DESC, recordCount DESC, value LIMIT 200").all<{ value: string; jobCount: number; recordCount: number }>(),
    db.prepare("SELECT category, subcategory value FROM market_subcategory_taxonomy WHERE status='active' ORDER BY category, sort_order, subcategory LIMIT 2000").all<{ category: string; value: string }>(),
    db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE status<>'deleted' ORDER BY category, version DESC LIMIT 300`).all<PromptRow>(),
    db.prepare(`SELECT ${jobColumns}, CASE WHEN job.reuse_status<>'ready' THEN 1 ELSE (
        SELECT COUNT(*) FROM market_annotation_items remaining
        WHERE remaining.job_id=job.id AND (remaining.status IN ('queued','claimed','inferencing')
          OR (remaining.status='failed' AND remaining.attempt_count<3))
      ) END remaining_inference_count
      FROM market_annotation_jobs job WHERE job.status<>'deleted' ORDER BY job.created_at DESC LIMIT 50`).all<JobRow>(),
    db.prepare("SELECT category, executor, concurrency, updated_by, updated_at FROM market_annotation_concurrency_settings ORDER BY category, executor LIMIT 400").all<ConcurrencySettingRow>(),
    db.prepare(`SELECT run.job_id,run.state,run.retry_state_json,run.next_run_at,run.lease_token_hash,run.lease_expires_at,
        run.last_failure_code,run.last_failure_message,run.last_started_at,run.last_heartbeat_at,run.completed_at,run.updated_at,
        COALESCE(setting.concurrency,?) configured_concurrency
      FROM market_annotation_cloud_runs run JOIN market_annotation_jobs job ON job.id=run.job_id
      LEFT JOIN market_annotation_concurrency_settings setting ON setting.category=job.category AND setting.executor='cloud'
      WHERE job.status<>'deleted'
      ORDER BY datetime(run.updated_at) DESC LIMIT 100`)
      .bind(defaultMarketAnnotationConcurrency("cloud")).all<CloudRunRow & { configured_concurrency: number }>(),
    listAnnotationModels(db), listPromptTextModels(db), input.includeCatalog === false
      ? Promise.resolve({ items: [], page, pageSize, total: 0, pageCount: 1, query: q })
      : searchAnnotationCatalog(db, { q, page, pageSize }),
    db.prepare("SELECT id, category, baseline_prompt_id baselinePromptId, candidate_prompt_id candidatePromptId, model_id modelId, status, seed, requested_sample_count requestedSampleCount, sample_count sampleCount, sample_hash sampleHash, metrics_json metricsJson, gate_json gateJson, created_by createdBy, created_at createdAt, completed_at completedAt FROM market_annotation_validation_runs ORDER BY created_at DESC LIMIT 30").all<Record<string, unknown>>(),
    input.includeAgents ? db.prepare("SELECT id, name, status, capabilities_json capabilitiesJson, created_by createdBy, created_at createdAt, last_seen_at lastSeenAt, revoked_at revokedAt FROM market_annotation_local_agents ORDER BY created_at DESC LIMIT 50").all<Record<string, unknown>>() : Promise.resolve({ results: [] as Record<string, unknown>[] }),
    db.prepare("SELECT id, run_id runId, prompt_version_id promptVersionId, status, predicted_segment predictedSegment, predicted_image_price_cents predictedImagePriceCents, confidence_bps confidenceBps, is_correct isCorrect, error_message errorMessage, sample_snapshot_json sampleSnapshotJson FROM market_annotation_validation_results ORDER BY created_at DESC LIMIT 500").all<Record<string, unknown>>(),
  ]);
  const candidateCountByCategory = new Map((candidateCounts ?? []).map((row) => [row.value, row.candidateCount]));
  return {
    categories: (categoryRows.results ?? []).map((row) => ({ ...row, candidateCount: candidateCounts === null ? null : candidateCountByCategory.get(row.value) ?? 0 })), reviewCategories: reviewCategoryRows.results ?? [], taxonomy: taxonomyRows.results ?? [], prompts: (promptRows.results ?? []).map(promptValue), jobs: (jobRows.results ?? []).map(jobValue),
    concurrencySettings: (concurrencyRows.results ?? []).map((row) => ({ category: row.category, executor: row.executor, concurrency: row.concurrency, updatedBy: row.updated_by, updatedAt: row.updated_at })),
    cloudRuns: (cloudRunRows.results ?? []).map((row) => cloudRunValue(row, normalizeMarketAnnotationConcurrency(row.configured_concurrency, "cloud"))),
    ...review,
    models, textModels, catalog,
    validationRuns: (runRows.results ?? []).map((row) => ({ ...row, metrics: json(String(row.metricsJson ?? "{}"), {}), gate: json(String(row.gateJson ?? "{}"), {}) })),
    validationResults: (validationRows.results ?? []).map((row) => ({ ...row, ...snapshotView(String(row.sampleSnapshotJson ?? "{}")) })),
    agents: (agentRows.results ?? []).map((row) => ({ ...row, capabilities: json(String(row.capabilitiesJson ?? "{}"), {}) })),
  };
}

export async function setFilteredAnnotationSelection(db: MarketDatabase, input: {
  jobId?: string; aggregateJobs?: boolean; category?: string; categories?: string[]; selected: boolean; itemSegment?: string; itemSegments?: string[]; storageStatus?: "pending" | "committed"; storageStatuses?: string[]; recognitionSource?: "ai" | "non_ai"; recognitionSources?: string[];
}, actor: Actor) {
  await ensureAnnotationSchema(db);
  const jobId = input.jobId?.trim() ?? "";
  const categories = annotationCategoryList(input.categories, input.category);
  const clauses = ["status IN ('review_pending','approved','rejected')"];
  const bindings: unknown[] = [];
  if (input.aggregateJobs) {
    const allowedStatuses = input.selected ? "'review_ready'" : "'running','review_ready'";
    clauses.unshift(`job_id IN (SELECT id FROM market_annotation_jobs WHERE status IN (${allowedStatuses})${categories.length ? ` AND category IN (${categories.map(() => "?").join(",")})` : ""})`);
    bindings.push(...categories);
  } else {
    const job = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs WHERE id=? LIMIT 1`).bind(jobId).first<JobRow>();
    const allowedStatuses = input.selected ? ["review_ready"] : ["running", "review_ready"];
    if (!job || !allowedStatuses.includes(job.status)) throw new Error(input.selected ? "任务尚未完成识别，暂不可批量入库" : "任务当前不可清空选择");
    clauses.unshift("job_id=?");
    bindings.push(jobId);
  }
  if (input.selected) clauses.push(annotationImportableClause());
  addAnnotationReviewFilters(clauses, bindings, input);
  const where = clauses.join(" AND ");
  const count = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items WHERE ${where}`).bind(...bindings).first<{ count: number }>();
  const total = Number(count?.count ?? 0);
  if (input.selected && total > MAX_FILTERED_SELECTION) throw new Error(`当前筛选结果超过 ${MAX_FILTERED_SELECTION} 条，请缩小筛选范围后再全选`);
  if (!total) return { ok: true, changed: 0, selected: input.selected };
  const affectedJobs = await db.prepare(`SELECT DISTINCT job_id jobId FROM market_annotation_items WHERE ${where}`).bind(...bindings).all<{ jobId: string }>();
  const result = await db.prepare(`UPDATE market_annotation_items SET selected=?, status=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE ${where}`)
    .bind(input.selected ? 1 : 0, input.selected ? "approved" : "review_pending", actor.email, ...bindings).run();
  await Promise.all((affectedJobs.results ?? []).map((row) => refreshJob(db, row.jobId)));
  return { ok: true, changed: Number(result.meta.changes ?? 0), selected: input.selected };
}

export async function commitSelectedAnnotationItems(db: MarketDatabase, input: { jobId?: string; aggregateJobs?: boolean; category?: string; categories?: string[]; idempotencyKey: string }, actor: Actor) {
  await ensureAnnotationSchema(db);
  if (input.aggregateJobs) {
    const categories = annotationCategoryList(input.categories, input.category);
    const categorySql = categories.length ? `AND i.category IN (${categories.map(() => "?").join(",")})` : "";
    const stale = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items i
      JOIN market_annotation_jobs j ON j.id=i.job_id
      JOIN market_annotation_prompt_versions p ON p.id=j.prompt_version_id
      WHERE i.status='approved' AND i.selected=1 AND j.status IN ('running','review_ready','committing')
        AND EXISTS (SELECT 1 FROM json_each(p.segments_json) segment WHERE CAST(segment.value AS TEXT)=i.reviewed_segment)
        AND NOT (${currentAnnotationSnapshotExistsSql("i")}) ${categorySql}`).bind(...categories).first<{ count: number }>();
    const staleSelected = Number(stale?.count ?? 0);
    const rows = await db.prepare(`SELECT i.id, i.job_id jobId FROM market_annotation_items i
      JOIN market_annotation_jobs j ON j.id=i.job_id
      JOIN market_annotation_prompt_versions p ON p.id=j.prompt_version_id
      WHERE i.status='approved' AND i.selected=1 AND j.status IN ('review_ready','committing')
        AND EXISTS (SELECT 1 FROM json_each(p.segments_json) segment WHERE CAST(segment.value AS TEXT)=i.reviewed_segment)
        AND ${currentAnnotationSnapshotExistsSql("i")} ${categorySql}
      ORDER BY j.created_at ASC, i.created_at, i.id LIMIT ${COMMIT_SELECTION_BATCH_SIZE}`).bind(...categories).all<{ id: string; jobId: string }>();
    const selected = rows.results ?? [];
    if (!selected.length) return { ok: true, committed: 0, duplicates: 0, jobs: 0, remainingSelected: 0, staleSelected, hasMore: false };
    const groups = new Map<string, string[]>();
    for (const row of selected) groups.set(row.jobId, [...(groups.get(row.jobId) ?? []), row.id]);
    let committed = 0;
    let duplicates = 0;
    let completedJobs = 0;
    for (const [selectedJobId, ids] of groups) {
      try {
        const result = await commitAnnotationItems(db, { jobId: selectedJobId, candidateIds: ids, idempotencyKey: input.idempotencyKey }, actor);
        committed += Number(result.committed ?? 0);
        duplicates += Number(result.duplicates ?? 0);
        completedJobs += 1;
        if (!result.ok) return { ...result, partial: committed > 0 || Boolean(result.partial), committed, duplicates, jobs: completedJobs, hasMore: true };
      } catch (error) {
        if (!committed && !duplicates) throw error;
        return { ok: false, partial: true, committed, duplicates, jobs: completedJobs, error: safeOperationalError(error, "跨任务入库部分成功，请刷新后继续处理剩余项目"), hasMore: true };
      }
    }
    const remaining = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items i
      JOIN market_annotation_jobs j ON j.id=i.job_id
      JOIN market_annotation_prompt_versions p ON p.id=j.prompt_version_id
      WHERE i.status='approved' AND i.selected=1 AND j.status='review_ready'
        AND EXISTS (SELECT 1 FROM json_each(p.segments_json) segment WHERE CAST(segment.value AS TEXT)=i.reviewed_segment)
        AND ${currentAnnotationSnapshotExistsSql("i")} ${categorySql}`).bind(...categories).first<{ count: number }>();
    const remainingSelected = Number(remaining?.count ?? 0);
    return { ok: true, committed, duplicates, jobs: completedJobs, remainingSelected, staleSelected, hasMore: remainingSelected > 0 };
  }
  const jobId = input.jobId?.trim() ?? "";
  const stale = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items i
    JOIN market_annotation_jobs j ON j.id=i.job_id
    JOIN market_annotation_prompt_versions p ON p.id=j.prompt_version_id
    WHERE i.job_id=? AND i.status='approved' AND i.selected=1 AND j.status IN ('running','review_ready','committing')
      AND EXISTS (SELECT 1 FROM json_each(p.segments_json) segment WHERE CAST(segment.value AS TEXT)=i.reviewed_segment)
      AND NOT (${currentAnnotationSnapshotExistsSql("i")})`).bind(jobId).first<{ count: number }>();
  const staleSelected = Number(stale?.count ?? 0);
  const rows = await db.prepare(`SELECT i.id FROM market_annotation_items i
    JOIN market_annotation_jobs j ON j.id=i.job_id
    JOIN market_annotation_prompt_versions p ON p.id=j.prompt_version_id
    WHERE i.job_id=? AND i.status='approved' AND i.selected=1 AND j.status IN ('review_ready','committing')
      AND EXISTS (SELECT 1 FROM json_each(p.segments_json) segment WHERE CAST(segment.value AS TEXT)=i.reviewed_segment)
      AND ${currentAnnotationSnapshotExistsSql("i")}
    ORDER BY i.created_at,i.id LIMIT ${COMMIT_SELECTION_BATCH_SIZE}`)
    .bind(jobId).all<{ id: string }>();
  const ids = (rows.results ?? []).map((row) => row.id);
  if (!ids.length) return { ok: true, committed: 0, duplicates: 0, remainingSelected: 0, staleSelected, hasMore: false };
  const result = await commitAnnotationItems(db, { jobId, candidateIds: ids, idempotencyKey: input.idempotencyKey }, actor);
  const remaining = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items i
    JOIN market_annotation_jobs j ON j.id=i.job_id
    JOIN market_annotation_prompt_versions p ON p.id=j.prompt_version_id
    WHERE i.job_id=? AND i.status='approved' AND i.selected=1 AND j.status='review_ready'
      AND EXISTS (SELECT 1 FROM json_each(p.segments_json) segment WHERE CAST(segment.value AS TEXT)=i.reviewed_segment)
      AND ${currentAnnotationSnapshotExistsSql("i")}`)
    .bind(jobId).first<{ count: number }>();
  const remainingSelected = Number(remaining?.count ?? 0);
  return { ...result, remainingSelected, staleSelected, hasMore: remainingSelected > 0 };
}

export async function searchAnnotationCatalog(db: MarketDatabase, input: { q?: string; page?: number; pageSize?: number }) {
  const page = strictInteger(input.page, 1, 1, 50_000, "page");
  const pageSize = strictInteger(input.pageSize, 30, 10, 100, "pageSize");
  const q = input.q?.trim().slice(0, 120) ?? "";
  const like = `%${q.replace(/[\\%_]/g, "\\$&")}%`;
  const base = `WITH identity_market AS MATERIALIZED (
    SELECT m.* FROM market_master_identities identity
    JOIN market_ranking_entries m ON m.id=identity.latest_entry_id
  ), latest_market AS MATERIALIZED (
    SELECT * FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY m.category, m.sku_code ORDER BY m.period_end DESC, m.updated_at DESC, m.id DESC) rn FROM identity_market m) WHERE rn = 1
  ), latest_review AS (
    SELECT * FROM (SELECT i.*, j.category item_category, ROW_NUMBER() OVER (PARTITION BY j.category, i.sku_code ORDER BY i.updated_at DESC, i.id DESC) rn FROM market_annotation_items i JOIN market_annotation_jobs j ON j.id = i.job_id) WHERE rn = 1
  )`;
  const where = q ? "WHERE (m.sku_code LIKE ? ESCAPE '\\' OR m.product_name LIKE ? ESCAPE '\\' OR m.brand LIKE ? ESCAPE '\\' OR m.category LIKE ? ESCAPE '\\' OR a.segment LIKE ? ESCAPE '\\')" : "";
  const bindings = q ? [like, like, like, like, like] : [];
  const rows = await db.prepare(`${base} SELECT m.sku_code skuCode, m.product_name productName, m.brand, m.category,
      CASE WHEN mic.status='ready' THEN '/api/market/images/' || mic.content_sha256 ELSE m.image_url END imageUrl,
      COALESCE(mic.status, CASE WHEN m.image_url='' THEN 'missing' ELSE 'pending' END) imageCacheStatus,
      m.price_cents rankingPriceCents, a.id annotationId, a.segment finalSegment, a.image_price_cents finalImagePriceCents,
      COALESCE(r.status, CASE WHEN a.id IS NOT NULL THEN 'committed' ELSE 'unreviewed' END) reviewStatus,
      COUNT(*) OVER() fullCount
      FROM latest_market m LEFT JOIN market_image_cache mic ON mic.source_url=m.image_url
      LEFT JOIN market_sku_annotations a ON a.category=m.category AND a.sku_code=m.sku_code
      LEFT JOIN latest_review r ON r.item_category=m.category AND r.sku_code=m.sku_code ${where}
      ORDER BY m.category, m.sku_code LIMIT ? OFFSET ?`).bind(...bindings, pageSize, (page - 1) * pageSize).all<Record<string, unknown>>();
  const rawItems = rows.results ?? [];
  let total = Number(rawItems[0]?.fullCount ?? 0);
  if (!rawItems.length && page > 1) {
    const countRow = await db.prepare(`${base} SELECT COUNT(*) total FROM latest_market m
      LEFT JOIN market_sku_annotations a ON a.category=m.category AND a.sku_code=m.sku_code ${where}`).bind(...bindings).first<{ total: number }>();
    total = Number(countRow?.total ?? 0);
  }
  const items = rawItems.map((row) => {
    const item = { ...row };
    delete item.fullCount;
    return item;
  });
  return { items, page, pageSize, total, pageCount: Math.max(1, Math.ceil(total / pageSize)), query: q };
}

export async function createPromptVersion(db: MarketDatabase, input: { category: string; segments: unknown; promptBody: string; parentId?: string; source?: string; changeNote?: string }, actor: Actor) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const category = input.category.trim().slice(0, 120);
  const requestedSegments = normalizeSegments(input.segments);
  const segments = await listMarketSubcategoryTaxonomy(db, category);
  if (segments.length < 2) throw new Error("该三级类目尚未配置细分品类，请先到细分品类设置中维护");
  if (requestedSegments.join("\u0000") !== segments.join("\u0000")) throw new Error("细分品类字典已更新，请刷新后再创建 Prompt");
  const promptBody = input.promptBody.trim();
  if (!category || promptBody.length < 40 || promptBody.length > 12_000) throw new Error("类目不能为空，Prompt 正文需在 40 到 12000 字符之间");
  let parent: PromptRow | null = null;
  if (input.parentId) {
    parent = await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE id = ? LIMIT 1`).bind(input.parentId).first<PromptRow>();
    if (!parent || parent.category !== category || parent.status === "deleted") throw new Error("父 Prompt 版本无效");
  }
  const last = await db.prepare("SELECT MAX(version) version FROM market_annotation_prompt_versions WHERE category = ?").bind(category).first<{ version: number | null }>();
  const id = `market-prompt-${randomUUID()}`;
  await db.prepare("INSERT INTO market_annotation_prompt_versions (id, category, version, parent_id, source, status, segments_json, prompt_body, change_note, created_by) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)")
    .bind(id, category, Number(last?.version ?? 0) + 1, parent?.id ?? null, input.source ?? "manual", JSON.stringify(segments), promptBody, input.changeNote?.trim().slice(0, 500) ?? "", actor.email).run();
  const row = await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE id = ?`).bind(id).first<PromptRow>();
  if (!row) throw new Error("Prompt 版本创建失败");
  return promptValue(row);
}

export async function generatePromptVersion(db: MarketDatabase, input: { textModelId: string; category: string; segments: unknown; parentId?: string; mode: "generate" | "evolve"; changeNote?: string }, actor: Actor) {
  await ensureMarketSchemaLazy(db);
  const requestedSegments = normalizeSegments(input.segments);
  const segments = await listMarketSubcategoryTaxonomy(db, input.category);
  if (segments.length < 2) throw new Error("该三级类目尚未配置细分品类，请先到细分品类设置中维护");
  if (requestedSegments.join("\u0000") !== segments.join("\u0000")) throw new Error("细分品类字典已更新，请刷新后再生成 Prompt");
  const parent = input.parentId ? await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE id = ?`).bind(input.parentId).first<PromptRow>() : null;
  const instruction = input.mode === "evolve"
    ? `你是视觉分类 Prompt 工程师。请仅根据通用品类规则改进下面的 Prompt，保持可审计、可复用，只输出完整新 Prompt 正文，不要代码围栏。不得请求、推断或复述冻结 holdout 的金标、预测或错误信息。\n三级类目：${input.category}\n固定枚举：${segments.join("、")}\n旧 Prompt：\n${parent?.prompt_body ?? ""}`
    : `你是电商视觉分类 Prompt 工程师。为三级类目“${input.category}”编写完整 Prompt。固定枚举：${segments.join("、")}。要求结合商品名与京东大图判断、提取主图明确展示的人民币价格、输出 segment/image_price_cents/confidence/reason 严格 JSON，只输出 Prompt 正文。`;
  const body = await runPromptTextCompletion(db, input.textModelId, instruction);
  if (!body) throw new Error("文本模型没有生成 Prompt");
  return createPromptVersion(db, { category: input.category, segments, promptBody: body, parentId: parent?.id, source: input.mode === "evolve" ? "evolved" : "ai_generated", changeNote: input.changeNote || (input.mode === "evolve" ? "AI 在不读取 holdout 的前提下生成候选" : "AI 生成初始候选") }, actor);
}

async function findCompatibleActiveAnnotationJob(db: MarketDatabase, input: {
  category: string; promptVersionId: string; executor: string; modelId?: string; localModelName?: string; workKey: string;
}) {
  return db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs job
    WHERE job.status IN ('queued','running','failed')
      AND (job.work_key=? OR (job.work_key='' AND job.category=? AND job.prompt_version_id=? AND job.executor=?
        AND COALESCE(job.model_id,'')=? AND job.local_model_name=?))
      AND (job.reuse_status<>'ready'
        OR EXISTS (SELECT 1 FROM market_annotation_items item WHERE item.job_id=job.id
          AND (item.status IN ('queued','claimed','inferencing') OR (item.status='failed' AND item.attempt_count<3)))
        OR (job.status='queued' AND job.total_count>0 AND datetime(job.updated_at)>=datetime('now','-5 minutes')))
    ORDER BY CASE WHEN job.work_key=? THEN 0 ELSE 1 END, datetime(job.updated_at) DESC, job.id DESC LIMIT 1`)
    .bind(input.workKey, input.category, input.promptVersionId, input.executor,
      input.executor === "cloud" ? input.modelId ?? "" : "",
      input.executor === "local" ? input.localModelName?.trim().slice(0, 160) ?? "" : "",
      input.workKey).first<JobRow>();
}

async function settleDormantCompatibleAnnotationJobs(db: MarketDatabase, input: {
  category: string; promptVersionId: string; executor: string; modelId?: string; localModelName?: string; workKey: string;
}) {
  const rows = await db.prepare(`SELECT job.id FROM market_annotation_jobs job
    WHERE ((job.status IN ('running','failed'))
        OR (job.status='queued' AND datetime(job.updated_at)<datetime('now','-5 minutes')))
      AND (job.work_key=? OR (job.work_key='' AND job.category=? AND job.prompt_version_id=? AND job.executor=?
        AND COALESCE(job.model_id,'')=? AND job.local_model_name=?))
      AND job.reuse_status='ready'
      AND NOT EXISTS (SELECT 1 FROM market_annotation_items item WHERE item.job_id=job.id
        AND (item.status IN ('queued','claimed','inferencing') OR (item.status='failed' AND item.attempt_count<3)))
    ORDER BY datetime(job.updated_at), job.id LIMIT 50`)
    .bind(input.workKey, input.category, input.promptVersionId, input.executor,
      input.executor === "cloud" ? input.modelId ?? "" : "",
      input.executor === "local" ? input.localModelName?.trim().slice(0, 160) ?? "" : "")
    .all<{ id: string }>();
  for (const row of rows.results ?? []) await refreshJob(db, row.id);
}

export async function createAnnotationJob(db: MarketDatabase, input: { category: string; promptVersionId: string; executor?: string; modelId?: string; localModelName?: string; limit?: number; allowInactivePrompt?: boolean }, actor: Actor) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const category = input.category.trim().slice(0, 120);
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=? LIMIT 1").bind(input.promptVersionId).first<PromptRow>();
  if (!prompt || prompt.category !== category || (!input.allowInactivePrompt && prompt.status !== "active")) throw new Error("只能使用该类目当前已激活的 Prompt 创建正式任务");
  await assertPromptTaxonomyCurrent(db, category, json<string[]>(prompt.segments_json, []), "Prompt 的细分品类枚举已过期，请使用当前字典创建并激活新版本");
  const executor = input.executor === "local" ? "local" : "cloud";
  if (executor === "cloud") {
    if (!input.modelId) throw new Error("云端任务必须选择视觉模型");
    const model = await db.prepare("SELECT id FROM ai_models WHERE id=? AND status='enabled' AND model_type IN ('vision','image')").bind(input.modelId).first<{ id: string }>();
    if (!model) throw new Error("所选云端视觉模型不存在或未启用");
  } else if (!input.localModelName?.trim()) throw new Error("本地任务必须填写 Ollama 模型名");
  const workKey = annotationJobWorkKey({ category, promptVersionId: prompt.id, executor, modelId: input.modelId, localModelName: input.localModelName });
  const compatibility = { category, promptVersionId: prompt.id, executor, modelId: input.modelId, localModelName: input.localModelName, workKey };
  let compatible = await findCompatibleActiveAnnotationJob(db, compatibility);
  if (compatible) {
    if (executor === "cloud") await ensureCloudRunControl(db, compatible.id, await annotationConcurrency(db, category, "cloud"));
    return jobValue(compatible);
  }
  await settleDormantCompatibleAnnotationJobs(db, compatibility);
  compatible = await findCompatibleActiveAnnotationJob(db, compatibility);
  if (compatible) {
    if (executor === "cloud") await ensureCloudRunControl(db, compatible.id, await annotationConcurrency(db, category, "cloud"));
    return jobValue(compatible);
  }
  await inheritConfirmedStandardSkuImagePrices(db, "target.category=?", [category]);
  const limit = normalizeMarketAnnotationJobLimit(input.limit);
  const promptSegments = json<string[]>(prompt.segments_json, []);
  const rows = await db.prepare(`
    WITH latest_market AS (
      SELECT * FROM (
        SELECT m.*, ROW_NUMBER() OVER (
          PARTITION BY m.category, m.scope, m.sku_code, m.ranking_dimension, substr(m.period_end, 1, 7)
          ORDER BY m.period_end DESC, m.updated_at DESC, m.id DESC
        ) rn
        FROM market_ranking_entries m
        WHERE m.category = ?
      ) WHERE rn = 1
    ), latest_standard_history AS (
      SELECT * FROM (
        SELECT history.category, history.scope, history.sku_code, history.ranking_dimension,
          history.image_content_sha256, history.confirmed_market_price_cents historical_price_cents,
          COALESCE(history.price_low_cents, history.confirmed_market_price_cents) historical_price_low_cents,
          COALESCE(history.price_high_cents, history.confirmed_market_price_cents) historical_price_high_cents,
          history.source_job_item_id,
          ROW_NUMBER() OVER (
            PARTITION BY history.category, history.scope, history.sku_code, history.ranking_dimension, history.image_content_sha256
            ORDER BY datetime(history.confirmed_at) DESC, datetime(history.updated_at) DESC, history.id DESC
          ) rn
        FROM market_price_snapshots history
        WHERE history.category=? AND history.confirmed_market_price_cents IS NOT NULL
          AND history.ai_price_type='标准售价'
          AND history.image_content_sha256<>''
      ) WHERE rn=1
    ), latest_ai_history AS (
      SELECT * FROM (
        SELECT history.category, history.scope, history.sku_code, history.ranking_dimension,
          history.image_content_sha256, history.ai_segment, history.ai_image_price_cents,
          history.ai_price_type, history.ai_price_low_cents, history.ai_price_high_cents,
          history.ai_confidence_bps, history.ai_reason, history.ai_raw_digest,
          history.resolved_image_url, history.image_source,
          history_job.prompt_version_id, history_job.model_id,
          ROW_NUMBER() OVER (
            PARTITION BY history.category, history.scope, history.sku_code, history.ranking_dimension,
              history.image_content_sha256, history_job.prompt_version_id, history_job.model_id
            ORDER BY datetime(history.updated_at) DESC, history.id DESC
          ) rn
        FROM market_annotation_items history
        JOIN market_annotation_jobs history_job ON history_job.id=history.job_id
        WHERE history.category=? AND history.status IN ('review_pending','approved','committed')
          AND history.ai_segment<>''
          AND history.image_content_sha256<>''
          AND history_job.executor='cloud'
      ) WHERE rn=1
    ), latest_segment_history AS (
      SELECT * FROM (
        SELECT history.category, history.scope, history.sku_code, history.ranking_dimension,
          history.reviewed_segment historical_sku_segment,
          ROW_NUMBER() OVER (
            PARTITION BY history.category, history.scope, history.sku_code, history.ranking_dimension
            ORDER BY datetime(history.reviewed_at) DESC, datetime(history.updated_at) DESC, history.id DESC
          ) rn
        FROM market_annotation_items history
        WHERE history.category=? AND history.status='committed' AND history.reviewed_segment<>''
      ) WHERE rn=1
    )
    SELECT ps.category, ps.scope, ps.sku_code, ps.ranking_dimension, ps.month,
      COALESCE(NULLIF(ps.image_content_sha256, ''), mic.content_sha256, '') image_content_sha256,
      lm.product_name, lm.brand, COALESCE(NULLIF(ps.image_url, ''), lm.image_url) image_url,
      history.historical_price_cents, history.historical_price_low_cents, history.historical_price_high_cents,
      history_item.category historical_item_category, history_item.reviewed_segment historical_segment,
      history_item.image_source historical_image_source,
      ai_history.ai_segment historical_ai_segment, ai_history.ai_image_price_cents historical_ai_image_price_cents,
      ai_history.ai_price_type historical_ai_price_type, ai_history.ai_price_low_cents historical_ai_price_low_cents,
      ai_history.ai_price_high_cents historical_ai_price_high_cents, ai_history.ai_confidence_bps historical_ai_confidence_bps,
      ai_history.ai_reason historical_ai_reason, ai_history.ai_raw_digest historical_ai_raw_digest,
      ai_history.resolved_image_url historical_ai_resolved_image_url, ai_history.image_source historical_ai_image_source,
      segment_history.historical_sku_segment
    FROM market_price_snapshots ps
    JOIN latest_market lm ON lm.category=ps.category AND lm.scope=ps.scope AND lm.sku_code=ps.sku_code
      AND lm.ranking_dimension=ps.ranking_dimension AND substr(lm.period_end, 1, 7)=ps.month
    LEFT JOIN market_image_cache mic ON mic.source_url=COALESCE(NULLIF(ps.image_url, ''), lm.image_url) AND mic.status='ready'
    LEFT JOIN latest_standard_history history ON history.category=ps.category AND history.scope=ps.scope
      AND history.sku_code=ps.sku_code AND history.ranking_dimension=ps.ranking_dimension
      AND history.image_content_sha256=COALESCE(NULLIF(ps.image_content_sha256, ''), mic.content_sha256, '')
    LEFT JOIN market_annotation_items history_item ON history_item.id=history.source_job_item_id
    LEFT JOIN latest_ai_history ai_history ON ai_history.category=ps.category AND ai_history.scope=ps.scope
      AND ai_history.sku_code=ps.sku_code AND ai_history.ranking_dimension=ps.ranking_dimension
      AND ai_history.image_content_sha256=COALESCE(NULLIF(ps.image_content_sha256, ''), mic.content_sha256, '')
      AND ai_history.prompt_version_id=? AND ai_history.model_id=?
    LEFT JOIN latest_segment_history segment_history ON segment_history.category=ps.category AND segment_history.scope=ps.scope
      AND segment_history.sku_code=ps.sku_code AND segment_history.ranking_dimension=ps.ranking_dimension
    WHERE ps.category=?
      AND ps.ranking_dimension='SKU'
      AND ps.confirmed_market_price_cents IS NULL
      AND COALESCE(NULLIF(ps.image_content_sha256, ''), mic.content_sha256, '') <> ''
      AND NOT EXISTS (
        SELECT 1 FROM market_annotation_items existing_item
        WHERE existing_item.category=ps.category AND existing_item.scope=ps.scope
          AND existing_item.sku_code=ps.sku_code AND existing_item.ranking_dimension=ps.ranking_dimension
          AND existing_item.month=ps.month
          AND existing_item.image_content_sha256=COALESCE(NULLIF(ps.image_content_sha256, ''), mic.content_sha256, '')
          AND (existing_item.status IN ('queued','claimed','inferencing','review_pending','approved','rejected','committed')
            OR existing_item.status='failed')
      )
    ORDER BY ps.month, ps.ranking_dimension, ps.sku_code
    LIMIT ?`)
    .bind(category, category, category, category, prompt.id, executor === "cloud" ? input.modelId : null, category, limit).all<{ category: string; scope: string; sku_code: string; ranking_dimension: string; month: string; image_content_sha256: string; product_name: string; brand: string; image_url: string; historical_price_cents: number | null; historical_price_low_cents: number | null; historical_price_high_cents: number | null; historical_item_category: string | null; historical_segment: string | null; historical_image_source: string | null; historical_ai_segment: string | null; historical_ai_image_price_cents: number | null; historical_ai_price_type: string | null; historical_ai_price_low_cents: number | null; historical_ai_price_high_cents: number | null; historical_ai_confidence_bps: number | null; historical_ai_reason: string | null; historical_ai_raw_digest: string | null; historical_ai_resolved_image_url: string | null; historical_ai_image_source: string | null; historical_sku_segment: string | null }>();
  if (!rows.results.length) throw new Error("该三级类目当前可新建候选为 0：待 AI 总量可能包含无图、非 SKU、失败封顶或已由现有任务覆盖的快照");
  const id = "market-job-" + randomUUID();
  let insertedJob: { meta?: { changes?: number } };
  try {
    insertedJob = await db.prepare(`INSERT INTO market_annotation_jobs
      (id, category, prompt_version_id, executor, model_id, local_model_name, work_key, reuse_status, status, total_count, created_by)
    SELECT ?, ?, current_prompt.id, ?, ?, ?, ?, 'ready', 'queued', ?, ?
    FROM market_annotation_prompt_versions current_prompt
    WHERE current_prompt.id=? AND current_prompt.category=? AND current_prompt.status ${input.allowInactivePrompt ? "<>'deleted'" : "='active'"}
      AND (NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy WHERE taxonomy.category=? AND taxonomy.status='active') OR (
        NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy WHERE taxonomy.category=? AND taxonomy.status='active'
          AND NOT EXISTS (SELECT 1 FROM json_each(current_prompt.segments_json) segment WHERE CAST(segment.value AS TEXT)=taxonomy.subcategory))
        AND NOT EXISTS (SELECT 1 FROM json_each(current_prompt.segments_json) segment
          WHERE NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy
            WHERE taxonomy.category=? AND taxonomy.status='active' AND taxonomy.subcategory=CAST(segment.value AS TEXT)))
      ))`)
      .bind(id, category, executor, executor === "cloud" ? input.modelId : null,
        executor === "local" ? input.localModelName!.trim().slice(0, 160) : "", workKey, rows.results.length, actor.email,
        prompt.id, category, category, category, category).run() as { meta?: { changes?: number } };
  } catch (error) {
    const winner = await findCompatibleActiveAnnotationJob(db, compatibility);
    if (winner) {
      if (executor === "cloud") await ensureCloudRunControl(db, winner.id, await annotationConcurrency(db, category, "cloud"));
      return jobValue(winner);
    }
    throw error;
  }
  if (!Number(insertedJob.meta?.changes ?? 0)) throw new Error("Prompt 或细分品类字典已变化，请刷新后重建任务");
  let insertedItems = 0;
  for (let offset = 0; offset < rows.results.length; offset += 80) {
    const inserted = await db.batch(rows.results.slice(offset, offset + 80).map((row) => {
      const inheritedPrice = row.historical_price_cents;
      const inheritedSegment = row.historical_item_category === row.category && row.historical_segment && promptSegments.includes(row.historical_segment) ? row.historical_segment : "";
      const reusedAi = inheritedPrice === null && Boolean(row.historical_ai_segment) && promptSegments.includes(row.historical_ai_segment!);
      const priceOnlySegment = inheritedPrice === null && !reusedAi && row.historical_sku_segment && promptSegments.includes(row.historical_sku_segment) ? row.historical_sku_segment : "";
      const historyReviewer = inheritedPrice !== null ? HISTORY_SAME_IMAGE_REVIEWER : priceOnlySegment ? HISTORY_SAME_SKU_SEGMENT_REVIEWER : "";
      return db.prepare(`INSERT INTO market_annotation_items
        (id, job_id, category, scope, sku_code, ranking_dimension, month, image_content_sha256, product_name, brand,
          source_image_url, resolved_image_url, image_source, status, ai_segment, ai_image_price_cents, ai_price_type,
          ai_price_low_cents, ai_price_high_cents, ai_confidence_bps, ai_reason, ai_raw_digest,
          reviewed_segment, reviewed_image_price_cents, reviewed_price_type, reviewed_price_low_cents,
          reviewed_price_high_cents, reviewed_by, reviewed_at)
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?, ?,
          CASE WHEN ?='' THEN NULL ELSE CURRENT_TIMESTAMP END
        WHERE EXISTS (SELECT 1 FROM market_price_snapshots snapshot
          JOIN market_ranking_entries ranking ON ranking.category=snapshot.category AND ranking.scope=snapshot.scope
            AND ranking.sku_code=snapshot.sku_code AND ranking.ranking_dimension=snapshot.ranking_dimension
            AND substr(ranking.period_end,1,7)=snapshot.month
          WHERE snapshot.category=? AND snapshot.scope=? AND snapshot.sku_code=? AND snapshot.ranking_dimension=?
            AND snapshot.month=? AND snapshot.image_content_sha256=? AND ranking.category=?)`)
        .bind("market-item-" + randomUUID(), id, row.category, row.scope, row.sku_code, row.ranking_dimension, row.month, row.image_content_sha256, row.product_name, row.brand,
          row.image_url,
          inheritedPrice !== null ? row.image_url : reusedAi ? (row.historical_ai_resolved_image_url || row.image_url) : "",
          inheritedPrice !== null ? (row.historical_image_source || "history_same_image") : reusedAi ? (row.historical_ai_image_source || "history_same_image") : "none",
          inheritedPrice !== null || reusedAi ? "review_pending" : "queued",
          reusedAi ? row.historical_ai_segment : "", reusedAi ? row.historical_ai_image_price_cents : null,
          reusedAi ? row.historical_ai_price_type : "", reusedAi ? row.historical_ai_price_low_cents : null,
          reusedAi ? row.historical_ai_price_high_cents : null, reusedAi ? row.historical_ai_confidence_bps : null,
          reusedAi ? row.historical_ai_reason : "", reusedAi ? row.historical_ai_raw_digest : "",
          inheritedPrice !== null ? inheritedSegment : reusedAi ? row.historical_ai_segment : priceOnlySegment,
          inheritedPrice !== null ? inheritedPrice : reusedAi ? row.historical_ai_image_price_cents : null,
          inheritedPrice !== null ? "标准售价" : reusedAi ? row.historical_ai_price_type : "",
          inheritedPrice !== null ? row.historical_price_low_cents : reusedAi ? row.historical_ai_price_low_cents : null,
          inheritedPrice !== null ? row.historical_price_high_cents : reusedAi ? row.historical_ai_price_high_cents : null,
          historyReviewer, historyReviewer,
          row.category, row.scope, row.sku_code, row.ranking_dimension, row.month, row.image_content_sha256, row.category);
    })) as Array<{ meta?: { changes?: number } }>;
    insertedItems += inserted.reduce((sum, result) => sum + Number(result.meta?.changes ?? 0), 0);
  }
  if (!insertedItems) {
    await db.prepare("DELETE FROM market_annotation_jobs WHERE id=? AND status='queued'").bind(id).run();
    throw new Error("候选价格快照已变化，请刷新后重建任务");
  }
  await refreshJob(db, id);
  if (executor === "cloud") await ensureCloudRunControl(db, id, await annotationConcurrency(db, category, "cloud"));
  const job = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=?").bind(id).first<JobRow>();
  if (!job) throw new Error("标注任务创建失败");
  return jobValue(job);
}

export async function createPriceRecognitionJob(db: MarketDatabase, input: { category: string; modelId: string; limit?: number }, actor: Actor) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const category = input.category.trim().slice(0, 120);
  if (!category) throw new Error("请选择需要识别价格的类目");
  const categoryExists = await db.prepare("SELECT category FROM market_ranking_entries WHERE category=? LIMIT 1").bind(category).first<{ category: string }>();
  if (!categoryExists) throw new Error("所选类目不存在或尚未导入商品数据");

  let prompt = await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE category=? AND status='active' ORDER BY version DESC LIMIT 1`).bind(category).first<PromptRow>();
  if (!prompt) {
    prompt = await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE category=? AND source='system_price' ORDER BY version DESC LIMIT 1`).bind(category).first<PromptRow>();
  }
  if (!prompt) {
    const taxonomySegments = await listMarketSubcategoryTaxonomy(db, category);
    if (taxonomySegments.length < 2) throw new Error("该三级类目尚未配置细分品类，请先到细分品类设置中维护");
    const created = await createPromptVersion(db, {
      category,
      segments: taxonomySegments,
      promptBody: systemPriceRecognitionPrompt(category),
      source: "system_price",
      changeNote: "SKU 数据库一键识别价格使用的系统 Prompt；不替代人工验证后的细分类目 Prompt",
    }, actor);
    prompt = await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE id=?`).bind(created.id).first<PromptRow>();
  }
  if (!prompt) throw new Error("系统价格识别 Prompt 创建失败");
  const existing = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs job
    WHERE job.category=? AND job.prompt_version_id=? AND job.executor='cloud' AND job.model_id=?
      AND job.status IN ('queued','running','failed')
      AND (job.reuse_status<>'ready' OR EXISTS (
        SELECT 1 FROM market_annotation_items item WHERE item.job_id=job.id
          AND (item.status IN ('queued','claimed','inferencing')
            OR (item.status='failed' AND item.attempt_count<3))
      ))
    ORDER BY datetime(job.updated_at) DESC, job.id DESC LIMIT 1`)
    .bind(category, prompt.id, input.modelId).first<JobRow>();
  if (existing) {
    await refreshJob(db, existing.id);
    await ensureCloudRunControl(db, existing.id, await annotationConcurrency(db, category, "cloud"));
    return await getJob(db, existing.id) ?? jobValue(existing);
  }
  return createAnnotationJob(db, {
    category,
    promptVersionId: prompt.id,
    executor: "cloud",
    modelId: input.modelId,
    limit: input.limit,
    allowInactivePrompt: true,
  }, actor);
}

async function reuseAnnotationHistory(db: MarketDatabase, job: JobRow, limit = 40) {
  const rows = await db.prepare(`
    SELECT * FROM (
      SELECT current.id, current.category, current.scope, current.sku_code, current.ranking_dimension,
        current.month, current.image_content_sha256,
        history.ai_segment, history.ai_image_price_cents, history.ai_price_type,
        history.ai_price_low_cents, history.ai_price_high_cents, history.ai_confidence_bps,
        history.ai_reason, history.ai_raw_digest, history.resolved_image_url, history.image_source,
        ROW_NUMBER() OVER (PARTITION BY current.id ORDER BY datetime(history.updated_at) DESC, history.id DESC) rn
      FROM market_annotation_items current
      JOIN market_annotation_items history ON history.id<>current.id
        AND history.category=current.category AND history.scope=current.scope
        AND history.sku_code=current.sku_code AND history.ranking_dimension=current.ranking_dimension
        AND history.image_content_sha256=current.image_content_sha256
      JOIN market_annotation_jobs history_job ON history_job.id=history.job_id
      WHERE current.job_id=? AND current.status IN ('queued','failed') AND current.attempt_count<3
        AND current.image_content_sha256<>''
        AND history.status IN ('review_pending','approved','committed')
        AND history.ai_segment<>''
        AND history_job.executor=? AND history_job.prompt_version_id=?
        AND ((?='cloud' AND history_job.model_id=?) OR (?='local' AND history_job.local_model_name=?))
    ) WHERE rn=1 LIMIT ?`)
    .bind(job.id, job.executor, job.prompt_version_id,
      job.executor, job.model_id ?? "", job.executor, job.local_model_name, limit)
    .all<ReusableAnnotationRow & { rn: number }>();
  if (!rows.results.length) return 0;
  const statements = rows.results.flatMap((row) => [
    db.prepare(`UPDATE market_annotation_items SET
        status='review_pending', ai_segment=?, ai_image_price_cents=?, ai_price_type=?,
        ai_price_low_cents=?, ai_price_high_cents=?, ai_confidence_bps=?, ai_reason=?, ai_raw_digest=?,
        reviewed_segment=?, reviewed_image_price_cents=?, reviewed_price_type=?,
        reviewed_price_low_cents=?, reviewed_price_high_cents=?, resolved_image_url=?, image_source=?,
        error_message='', lease_token_hash='', lease_agent_id='', lease_expires_at=NULL,
        version=version+1, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND job_id=? AND status IN ('queued','failed') AND attempt_count<3`)
      .bind(row.ai_segment, row.ai_image_price_cents, row.ai_price_type,
        row.ai_price_low_cents, row.ai_price_high_cents, row.ai_confidence_bps, row.ai_reason, row.ai_raw_digest,
        row.ai_segment, row.ai_image_price_cents, row.ai_price_type,
        row.ai_price_low_cents, row.ai_price_high_cents, row.resolved_image_url, row.image_source,
        row.id, job.id),
    db.prepare(`UPDATE market_price_snapshots SET
        ai_image_price_cents=?, ai_price_type=?, ai_confidence_bps=?, ai_reason=?,
        price_low_cents=COALESCE(?, price_low_cents), price_high_cents=COALESCE(?, price_high_cents),
        confirmation_status='ai_pending', source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?
        AND image_content_sha256=? AND confirmed_market_price_cents IS NULL`)
      .bind(row.ai_image_price_cents, row.ai_price_type, row.ai_confidence_bps, row.ai_reason,
        row.ai_price_low_cents, row.ai_price_high_cents, row.id, job.prompt_version_id,
        row.category, row.scope, row.sku_code, row.ranking_dimension, row.month, row.image_content_sha256),
  ]);
  const results = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
  return rows.results.reduce((sum, _row, index) => sum + Number(results[index * 2]?.meta?.changes ?? 0), 0);
}

async function recoverExpiredInferenceFollowers(db: MarketDatabase, job: JobRow, limit = CLOUD_REUSE_BATCH_SIZE) {
  const rows = await db.prepare(`
    SELECT * FROM (
      SELECT current.id, current.category, current.scope, current.sku_code, current.ranking_dimension,
        current.month, current.image_content_sha256,
        history.ai_segment, history.ai_image_price_cents, history.ai_price_type,
        history.ai_price_low_cents, history.ai_price_high_cents, history.ai_confidence_bps,
        history.ai_reason, history.ai_raw_digest, history.resolved_image_url, history.image_source,
        ROW_NUMBER() OVER (PARTITION BY current.id ORDER BY datetime(history.updated_at) DESC, history.id DESC) rn
      FROM market_annotation_items current
      JOIN market_annotation_items history ON history.job_id=current.job_id AND history.id<>current.id
        AND ${inferenceUnitMatch("history", "current")}
      WHERE current.job_id=? AND current.status='inferencing' AND current.attempt_count<3
        AND current.lease_expires_at IS NOT NULL AND datetime(current.lease_expires_at)<=datetime('now')
        AND history.status IN ('review_pending','approved','committed') AND history.ai_segment<>''
    ) WHERE rn=1 LIMIT ?`)
    .bind(job.id, limit).all<ReusableAnnotationRow & { rn: number }>();
  if (!rows.results.length) return 0;
  const statements = rows.results.flatMap((row) => [
    db.prepare(`UPDATE market_annotation_items SET
        status='review_pending', ai_segment=?, ai_image_price_cents=?, ai_price_type=?,
        ai_price_low_cents=?, ai_price_high_cents=?, ai_confidence_bps=?, ai_reason=?, ai_raw_digest=?,
        reviewed_segment=?, reviewed_image_price_cents=?, reviewed_price_type=?,
        reviewed_price_low_cents=?, reviewed_price_high_cents=?, resolved_image_url=?, image_source=?,
        error_message='', lease_token_hash='', lease_agent_id='', lease_expires_at=NULL,
        version=version+1, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND job_id=? AND status='inferencing' AND attempt_count<3
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now')`)
      .bind(row.ai_segment, row.ai_image_price_cents, row.ai_price_type,
        row.ai_price_low_cents, row.ai_price_high_cents, row.ai_confidence_bps, row.ai_reason, row.ai_raw_digest,
        row.ai_segment, row.ai_image_price_cents, row.ai_price_type,
        row.ai_price_low_cents, row.ai_price_high_cents, row.resolved_image_url, row.image_source,
        row.id, job.id),
    db.prepare(`UPDATE market_price_snapshots SET
        ai_image_price_cents=?, ai_price_type=?, ai_confidence_bps=?, ai_reason=?,
        price_low_cents=COALESCE(?, price_low_cents), price_high_cents=COALESCE(?, price_high_cents),
        confirmation_status='ai_pending', source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?
        AND image_content_sha256=? AND confirmed_market_price_cents IS NULL`)
      .bind(row.ai_image_price_cents, row.ai_price_type, row.ai_confidence_bps, row.ai_reason,
        row.ai_price_low_cents, row.ai_price_high_cents, row.id, job.prompt_version_id,
        row.category, row.scope, row.sku_code, row.ranking_dimension, row.month, row.image_content_sha256),
  ]);
  const results = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
  return rows.results.reduce((sum, _row, index) => sum + Number(results[index * 2]?.meta?.changes ?? 0), 0);
}

async function prepareAnnotationReuse(db: MarketDatabase, job: JobRow) {
  if (job.reuse_status === "ready") return { ready: true, reusedCount: 0 };
  await db.prepare(`UPDATE market_annotation_jobs SET reuse_status='pending', reuse_started_at=NULL, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND reuse_status='running' AND datetime(reuse_started_at)<=datetime('now','-3 minutes')`).bind(job.id).run();
  const claimed = await db.prepare(`UPDATE market_annotation_jobs SET reuse_status='running', reuse_started_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND reuse_status='pending'`).bind(job.id).run();
  if (!Number(claimed.meta.changes ?? 0)) return { ready: false, waiting: true, reusedCount: 0 };
  try {
    const reusedCount = await reuseAnnotationHistory(db, job, CLOUD_REUSE_BATCH_SIZE);
    const ready = reusedCount < CLOUD_REUSE_BATCH_SIZE;
    await db.prepare("UPDATE market_annotation_jobs SET reuse_status=?, reuse_started_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND reuse_status='running'")
      .bind(ready ? "ready" : "pending", job.id).run();
    return { ready, reusedCount };
  } catch (error) {
    await db.prepare("UPDATE market_annotation_jobs SET reuse_status='pending', reuse_started_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND reuse_status='running'")
      .bind(job.id).run().catch(() => undefined);
    throw error;
  }
}

async function scheduleAnnotationReuseRepair(db: MarketDatabase, jobId: string) {
  await db.prepare(`UPDATE market_annotation_jobs SET reuse_status='pending', reuse_started_at=NULL, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status IN ('queued','running','failed','review_ready')`)
    .bind(jobId).run().catch(() => undefined);
}

type AnnotationResultFields = {
  segment: string; imagePriceCents: number | null; priceType: string; priceLowCents: number | null;
  priceHighCents: number | null; confidenceBps: number; reason: string; rawDigest: string;
  resolvedImageUrl: string; imageSource: string;
};

async function fanOutInferenceUnitResult(db: MarketDatabase, job: JobRow, item: ItemRow, result: AnnotationResultFields) {
  const statements = [
    db.prepare(`UPDATE market_price_snapshots SET
        ai_image_price_cents=?, ai_price_type=?, ai_confidence_bps=?, ai_reason=?,
        price_low_cents=COALESCE(?, price_low_cents), price_high_cents=COALESCE(?, price_high_cents),
        confirmation_status='ai_pending', source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=?
        AND image_content_sha256=? AND confirmed_market_price_cents IS NULL`)
      .bind(result.imagePriceCents, result.priceType, result.confidenceBps, result.reason,
        result.priceLowCents, result.priceHighCents, item.id, job.prompt_version_id,
        item.category, item.scope, item.sku_code, item.ranking_dimension, item.image_content_sha256),
    db.prepare(`UPDATE market_annotation_items SET
      status='review_pending', ai_segment=?, ai_image_price_cents=?, ai_price_type=?,
      ai_price_low_cents=?, ai_price_high_cents=?, ai_confidence_bps=?, ai_reason=?, ai_raw_digest=?,
      reviewed_segment=?, reviewed_image_price_cents=?, reviewed_price_type=?,
      reviewed_price_low_cents=?, reviewed_price_high_cents=?, resolved_image_url=?, image_source=?,
      error_message='', lease_token_hash='', lease_agent_id='', lease_expires_at=NULL,
      version=version+1, updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND id<>? AND category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND image_content_sha256=?
      AND attempt_count<3 AND (status IN ('queued','failed') OR (status='inferencing'
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now')))`)
    .bind(result.segment, result.imagePriceCents, result.priceType,
      result.priceLowCents, result.priceHighCents, result.confidenceBps, result.reason, result.rawDigest,
      result.segment, result.imagePriceCents, result.priceType, result.priceLowCents, result.priceHighCents,
      result.resolvedImageUrl, result.imageSource, job.id, item.id,
      item.category, item.scope, item.sku_code, item.ranking_dimension, item.image_content_sha256),
  ];
  const results = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
  return Number(results[1]?.meta?.changes ?? 0);
}

async function fanOutInferenceUnitTerminalFailure(db: MarketDatabase, jobId: string, item: ItemRow, message: string) {
  const result = await db.prepare(`UPDATE market_annotation_items SET status='failed', attempt_count=3, error_message=?,
      lease_token_hash='', lease_agent_id='', lease_expires_at=NULL, version=version+1, updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND id<>? AND category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND image_content_sha256=?
      AND attempt_count<3 AND (status IN ('queued','failed') OR (status='inferencing'
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now')))`)
    .bind(message, jobId, item.id, item.category, item.scope, item.sku_code, item.ranking_dimension, item.image_content_sha256).run();
  return Number(result.meta.changes ?? 0);
}

export type CloudAnnotationFailureCode = "provider_rate_limit" | "model_timeout" | "model_network" | "image_fetch" | "model_configuration" | "model_response" | "annotation_failed";

export function classifyCloudAnnotationFailure(error: unknown) {
  const message = error instanceof Error ? error.message : "";
  const failureMessage = safeOperationalError(error, "识别失败");
  if (/状态码\s*429|rate limit|限流|额度不足/i.test(message)) return { failureKind: "rate_limit", failureCode: "provider_rate_limit", failureMessage, retryAfterMs: 60_000 } as const;
  if (/主图获取失败|图片/i.test(message)) return { failureKind: "permanent", failureCode: "image_fetch", failureMessage, retryAfterMs: 0 } as const;
  if (/模型调用超时|调用超时|timeout/i.test(message)) return { failureKind: "transient", failureCode: "model_timeout", failureMessage, retryAfterMs: 5_000 } as const;
  if (/模型接口网络错误|网络错误|fetch failed/i.test(message)) return { failureKind: "transient", failureCode: "model_network", failureMessage, retryAfterMs: 5_000 } as const;
  if (/API Key|不存在或未启用|模型配置/i.test(message)) return { failureKind: "permanent", failureCode: "model_configuration", failureMessage, retryAfterMs: 0 } as const;
  if (/模型响应|没有返回|枚举|confidence|价格/i.test(message)) return { failureKind: "permanent", failureCode: "model_response", failureMessage, retryAfterMs: 0 } as const;
  return { failureKind: "permanent", failureCode: "annotation_failed", failureMessage, retryAfterMs: 0 } as const;
}

async function runNextCloudAnnotationInternal(db: MarketDatabase, jobId: string, refreshState: boolean) {
  await ensureAnnotationSchema(db);
  const job = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=? LIMIT 1").bind(jobId).first<JobRow>();
  if (!job || job.executor !== "cloud" || !job.model_id) throw new Error("云端标注任务不存在");
  if (["cancelled", "committed", "deleted"].includes(job.status)) throw new Error("该任务当前不能继续执行");
  const recoveredCount = await recoverExpiredInferenceFollowers(db, job);
  if (recoveredCount) {
    if (refreshState) await refreshJob(db, jobId);
    return { done: false, reusedCount: recoveredCount, job: refreshState ? await getJob(db, jobId) : null };
  }
  const reusePreparation = await prepareAnnotationReuse(db, job);
  if (reusePreparation.reusedCount) {
    if (refreshState) await refreshJob(db, jobId);
    return { done: false, reusedCount: reusePreparation.reusedCount, job: refreshState ? await getJob(db, jobId) : null };
  }
  if (reusePreparation.waiting) return { done: false, waiting: true, retryAfterMs: 500 };
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(job.prompt_version_id).first<PromptRow>();
  if (!prompt) throw new Error("任务绑定的 Prompt 版本不存在");
  const concurrency = await annotationConcurrency(db, job.category, "cloud");
  const candidate = await db.prepare(`SELECT ${itemColumns} FROM market_annotation_items candidate
    WHERE candidate.job_id=? AND candidate.attempt_count<3
      AND (candidate.status IN ('queued','failed') OR (candidate.status='inferencing'
        AND candidate.lease_expires_at IS NOT NULL AND datetime(candidate.lease_expires_at)<=datetime('now')))
      AND ${inferenceUnitLeaderClause("candidate")}
      AND NOT EXISTS (SELECT 1 FROM market_annotation_items active_unit
        WHERE active_unit.job_id=candidate.job_id AND ${inferenceUnitMatch("active_unit", "candidate")}
          AND active_unit.status='inferencing' AND active_unit.lease_expires_at IS NOT NULL
          AND datetime(active_unit.lease_expires_at)>datetime('now'))
    ORDER BY CASE candidate.status WHEN 'queued' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, candidate.updated_at LIMIT 1`)
    .bind(jobId).first<ItemRow>();
  if (!candidate) {
    await db.prepare(`UPDATE market_annotation_items SET status='failed',
        error_message='推理租约连续超时，已达到最大尝试次数', lease_token_hash='', lease_agent_id='', lease_expires_at=NULL,
        version=version+1, updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND status='inferencing' AND attempt_count>=3
        AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now')`)
      .bind(jobId).run();
    await db.prepare(`UPDATE market_annotation_items SET status='failed', attempt_count=3,
        error_message='推理租约连续超时，已达到最大尝试次数', lease_token_hash='', lease_agent_id='', lease_expires_at=NULL,
        version=version+1, updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND status IN ('queued','failed') AND attempt_count<3
        AND EXISTS (SELECT 1 FROM market_annotation_items leader
          WHERE leader.job_id=market_annotation_items.job_id AND ${inferenceUnitMatch("leader", "market_annotation_items")}
            AND ${inferenceUnitLeaderClause("leader")} AND leader.status='failed' AND leader.attempt_count>=3)`)
      .bind(jobId).run();
    if (refreshState) await refreshJob(db, jobId);
    const active = await db.prepare("SELECT COUNT(*) count FROM market_annotation_items WHERE job_id=? AND status='inferencing' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)>datetime('now')").bind(jobId).first<{ count: number }>();
    return { done: Number(active?.count ?? 0) === 0, waiting: Number(active?.count ?? 0) > 0, job: refreshState ? await getJob(db, jobId) : null };
  }
  const claimToken = randomBytes(24).toString("hex");
  const claimHash = digest(claimToken);
  const claimed = await db.prepare("UPDATE market_annotation_items SET status='inferencing', lease_token_hash=?, lease_agent_id='cloud', lease_expires_at=datetime('now','+3 minutes'), attempt_count=attempt_count+1, error_message='', version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=? AND attempt_count<3 AND (status IN ('queued','failed') OR (status='inferencing' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now'))) AND (SELECT COUNT(*) FROM market_annotation_items active WHERE active.job_id=? AND active.status='inferencing' AND active.lease_expires_at IS NOT NULL AND datetime(active.lease_expires_at)>datetime('now'))<?")
    .bind(claimHash, candidate.id, candidate.version, jobId, concurrency).run();
  if (!Number(claimed.meta.changes ?? 0)) {
    const active = await db.prepare("SELECT COUNT(*) count FROM market_annotation_items WHERE job_id=? AND status='inferencing' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)>datetime('now')").bind(jobId).first<{ count: number }>();
    return Number(active?.count ?? 0) >= concurrency ? { done: false, waiting: true } : { done: false, raced: true };
  }
  if (job.status !== "running") await db.prepare("UPDATE market_annotation_jobs SET status='running', started_at=COALESCE(started_at,CURRENT_TIMESTAMP), completed_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','failed','review_ready')").bind(jobId).run();
  const promptSegments = json<string[]>(prompt.segments_json, []);
  const fixedSegment = candidate.reviewed_by === HISTORY_SAME_SKU_SEGMENT_REVIEWER && promptSegments.includes(candidate.reviewed_segment) ? candidate.reviewed_segment : undefined;
  let result: Awaited<ReturnType<typeof runVisionAnnotation>>;
  try {
    result = await runVisionAnnotation({ db, modelId: job.model_id, promptBody: prompt.prompt_body, segments: promptSegments, skuCode: candidate.sku_code, productName: candidate.product_name, brand: candidate.brand, imageUrl: candidate.source_image_url, fixedSegment });
  } catch (error) {
    const failure = classifyCloudAnnotationFailure(error);
    const message = safeOperationalError(error, "识别失败");
    const timing = visionAnnotationTiming(error);
    const failed = await db.prepare("UPDATE market_annotation_items SET status='failed', error_message=?, model_input_bytes=?, image_load_ms=?, image_prepare_ms=?, model_call_ms=?, total_inference_ms=?, lease_token_hash='', lease_agent_id='', lease_expires_at=NULL, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='inferencing' AND lease_token_hash=? AND datetime(lease_expires_at)>datetime('now')")
      .bind(message, timing.inputBytes, timing.imageLoadMs, timing.imagePrepareMs, timing.modelCallMs, timing.totalMs, candidate.id, claimHash).run();
    const reusedCount = Number(failed.meta.changes ?? 0) && candidate.attempt_count + 1 >= 3
      ? await fanOutInferenceUnitTerminalFailure(db, jobId, candidate, message)
      : 0;
    if (refreshState) await refreshJob(db, jobId);
    return { done: false, itemId: candidate.id, reusedCount, ...failure, job: refreshState ? await getJob(db, jobId) : null };
  }
  const completed = await db.batch([
    db.prepare("UPDATE market_annotation_items SET status='review_pending', ai_segment=?, ai_image_price_cents=?, ai_price_type=?, ai_price_low_cents=?, ai_price_high_cents=?, ai_confidence_bps=?, ai_reason=?, ai_raw_digest=?, model_input_bytes=?, image_load_ms=?, image_prepare_ms=?, model_call_ms=?, total_inference_ms=?, reviewed_segment=?, reviewed_image_price_cents=?, reviewed_price_type=?, reviewed_price_low_cents=?, reviewed_price_high_cents=?, resolved_image_url=?, image_source=?, lease_token_hash='', lease_agent_id='', lease_expires_at=NULL, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='inferencing' AND lease_token_hash=? AND datetime(lease_expires_at)>datetime('now')")
      .bind(result.segment, result.imagePriceCents, result.priceType, result.priceLowCents, result.priceHighCents, result.confidenceBps, result.reason, result.rawDigest, result.timing.inputBytes, result.timing.imageLoadMs, result.timing.imagePrepareMs, result.timing.modelCallMs, result.timing.totalMs, result.segment, result.imagePriceCents, result.priceType, result.priceLowCents, result.priceHighCents, result.resolvedImageUrl, result.imageSource, candidate.id, claimHash),
    db.prepare(`UPDATE market_price_snapshots SET
        ai_image_price_cents=?, ai_price_type=?, ai_confidence_bps=?, ai_reason=?,
        price_low_cents=COALESCE(?, price_low_cents), price_high_cents=COALESCE(?, price_high_cents),
        confirmation_status='ai_pending', source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?
        AND image_content_sha256=? AND confirmed_market_price_cents IS NULL
        AND EXISTS (SELECT 1 FROM market_annotation_items completed
          WHERE completed.id=? AND completed.status='review_pending' AND completed.version=? AND completed.ai_raw_digest=?)`)
      .bind(result.imagePriceCents, result.priceType, result.confidenceBps, result.reason,
        result.priceLowCents, result.priceHighCents, candidate.id, job.prompt_version_id,
        candidate.category, candidate.scope, candidate.sku_code, candidate.ranking_dimension, candidate.month,
        candidate.image_content_sha256, candidate.id, candidate.version + 2, result.rawDigest),
  ]) as Array<{ meta?: { changes?: number } }>;
  if (!Number(completed[0]?.meta?.changes ?? 0)) return { done: false, raced: true };
  let reusedCount: number;
  try {
    reusedCount = await fanOutInferenceUnitResult(db, job, candidate, result);
  } catch (error) {
    await scheduleAnnotationReuseRepair(db, jobId);
    throw error;
  }
  if (refreshState) await refreshJob(db, jobId);
  return { done: false, itemId: candidate.id, reusedCount, job: refreshState ? await getJob(db, jobId) : null };
}

export async function runNextCloudAnnotation(db: MarketDatabase, jobId: string) {
  return runNextCloudAnnotationInternal(db, jobId, true);
}

export async function runCloudAnnotationBatch(db: MarketDatabase, jobId: string, requestedLimit = 4) {
  await ensureAnnotationSchema(db);
  const limit = strictInteger(requestedLimit, 4, 1, CLOUD_ANNOTATION_BATCH_MAX, "limit");
  const control = await db.prepare("SELECT state FROM market_annotation_cloud_runs WHERE job_id=? LIMIT 1").bind(jobId).first<{ state: string }>();
  if (control?.state === "paused") return { done: false, waiting: true, paused: true, retryAfterMs: 0, processedCount: 0, reusedCount: 0, failedCount: 0, job: await getJob(db, jobId) };
  if (control?.state === "completed") return { done: true, waiting: false, processedCount: 0, reusedCount: 0, failedCount: 0, job: await getJob(db, jobId) };
  let processedCount = 0;
  let reusedCount = 0;
  let failedCount = 0;
  let done = false;
  let waiting = false;
  let failureKind = "";
  let failureCode: CloudAnnotationFailureCode | "" = "";
  let failureMessage = "";
  let retryAfterMs = 0;
  for (let index = 0; index < limit; index += 1) {
      const result = await runNextCloudAnnotationInternal(db, jobId, false) as {
        done?: boolean; waiting?: boolean; raced?: boolean; reusedCount?: number; itemId?: string;
        failureKind?: "rate_limit" | "transient" | "permanent"; failureCode?: CloudAnnotationFailureCode; failureMessage?: string; retryAfterMs?: number;
      };
      if (result.done) { done = true; break; }
      if (result.waiting) { waiting = true; break; }
      if (result.raced) continue;
      const reused = Math.max(0, Number(result.reusedCount ?? 0));
      reusedCount += reused;
      processedCount += reused + (result.itemId ? 1 : 0);
      if (result.failureKind) {
        failedCount += 1;
        failureKind = result.failureKind;
        failureCode = result.failureCode ?? "annotation_failed";
        failureMessage = String(result.failureMessage ?? "识别失败").slice(0, 300);
        retryAfterMs = Math.max(retryAfterMs, Number(result.retryAfterMs ?? 0));
        if (result.failureKind === "rate_limit" || result.failureKind === "transient") break;
      }
  }
  if (done) await refreshJob(db, jobId);
  return {
    done, waiting, processedCount, reusedCount, failedCount,
    ...(failureKind ? { failureKind, failureCode, failureMessage, retryAfterMs } : {}),
    job: done ? await getJob(db, jobId) : null,
  };
}

/**
 * 挑一个还有待推理内容的云端任务。reuse 尚未准备好的任务也要选中，
 * 因为复用扩散由 runNextCloudAnnotationInternal 自己在开头完成。
 */
async function pickRunnableCloudJob(db: MarketDatabase) {
  return db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs job
    WHERE job.executor='cloud' AND job.status IN ('queued','running','failed')
      AND (job.reuse_status<>'ready' OR EXISTS (SELECT 1 FROM market_annotation_items item
        WHERE item.job_id=job.id AND item.attempt_count<3
          AND (item.status IN ('queued','failed')
            OR (item.status='inferencing' AND item.lease_expires_at IS NOT NULL
              AND datetime(item.lease_expires_at)<=datetime('now')))))
    ORDER BY datetime(job.created_at), job.id LIMIT 1`).first<JobRow>();
}

/**
 * 后台泵的单次推进：识别本身早已在服务端执行，浏览器只是反复调用 run_batch。
 * 这里把「选任务 + 读当前并发 + 跑一批」合成一次调用，让常驻 runner 或将来的
 * scheduled() 处理器共用同一个入口，续跑仍然完全依赖既有租约与 attempt_count。
 */
export async function runCloudAnnotationPump(db: MarketDatabase, input: { jobId?: string } = {}) {
  await ensureAnnotationSchema(db);
  const job = input.jobId
    ? await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=? LIMIT 1").bind(input.jobId).first<JobRow>()
    : await pickRunnableCloudJob(db);
  if (!job) return { idle: true, jobId: "", category: "", concurrency: 0 };
  if (job.executor !== "cloud") throw new Error("只有云端标注任务可以由后台泵推进");
  // 已收尾的任务只走一次对账：runCloudAnnotationBatch 会立刻返回 done，
  // 随后的 refreshJob 把计数校正回真实值，不会再触发任何模型调用。
  if (["cancelled", "committed", "deleted"].includes(job.status)) return { idle: true, jobId: job.id, category: job.category, concurrency: 0 };
  const concurrency = await annotationConcurrency(db, job.category, "cloud");
  const batch = await runCloudAnnotationBatch(db, job.id, 1);
  // 后台泵没有浏览器那份 loadJobProgress 轮询，这里顺手刷新任务计数，
  // 页面上的进度才会随后台推进而前进，而不是一直停在 0/N。
  await refreshJob(db, job.id);
  return { idle: false, jobId: job.id, category: job.category, concurrency, ...batch, job: await getJob(db, job.id) };
}

const CLOUD_RUN_LEASE_MINUTES = 12;
const CLOUD_RUN_DEFAULT_RUNTIME_MS = 8 * 60_000;

type CloudPumpBatchResult = Awaited<ReturnType<typeof runCloudAnnotationBatch>> & {
  failureKind?: "rate_limit" | "transient" | "permanent";
  failureCode?: string;
  failureMessage?: string;
  retryAfterMs?: number;
  processedCount?: number;
  reusedCount?: number;
  waiting?: boolean;
  done?: boolean;
};

async function claimCloudRun(db: MarketDatabase, requestedJobId?: string) {
  const candidate = requestedJobId
    ? await db.prepare(`SELECT run.job_id FROM market_annotation_cloud_runs run
        JOIN market_annotation_jobs job ON job.id=run.job_id
        WHERE run.job_id=? AND run.state='running' AND job.executor='cloud'
          AND job.status IN ('queued','running','failed')
          AND (run.lease_token_hash='' OR run.lease_expires_at IS NULL OR datetime(run.lease_expires_at)<=datetime('now'))
          AND (run.next_run_at IS NULL OR datetime(run.next_run_at)<=datetime('now')) LIMIT 1`)
      .bind(requestedJobId).first<{ job_id: string }>()
    : await db.prepare(`SELECT run.job_id FROM market_annotation_cloud_runs run
        JOIN market_annotation_jobs job ON job.id=run.job_id
        WHERE run.state='running' AND job.executor='cloud' AND job.status IN ('queued','running','failed')
          AND (run.lease_token_hash='' OR run.lease_expires_at IS NULL OR datetime(run.lease_expires_at)<=datetime('now'))
          AND (run.next_run_at IS NULL OR datetime(run.next_run_at)<=datetime('now'))
        ORDER BY COALESCE(datetime(run.next_run_at),datetime('1970-01-01')),datetime(run.updated_at),run.job_id LIMIT 1`)
      .first<{ job_id: string }>();
  if (!candidate) return null;
  const tokenHash = digest(randomBytes(24).toString("hex"));
  const claimed = await db.prepare(`UPDATE market_annotation_cloud_runs SET lease_token_hash=?,
      lease_expires_at=datetime('now','+${CLOUD_RUN_LEASE_MINUTES} minutes'),last_started_at=CURRENT_TIMESTAMP,
      last_heartbeat_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND state='running'
      AND (lease_token_hash='' OR lease_expires_at IS NULL OR datetime(lease_expires_at)<=datetime('now'))
      AND (next_run_at IS NULL OR datetime(next_run_at)<=datetime('now'))`)
    .bind(tokenHash, candidate.job_id).run();
  return Number(claimed.meta.changes ?? 0) ? { jobId: candidate.job_id, tokenHash } : null;
}

async function persistCloudRun(
  db: MarketDatabase,
  claim: { jobId: string; tokenHash: string },
  retry: AnnotationRunRetryController,
  input: { nextRunAt?: number; failureCode?: string; failureMessage?: string } = {},
) {
  const nextRunAt = input.nextRunAt && input.nextRunAt > Date.now() ? new Date(input.nextRunAt).toISOString() : null;
  const result = await db.prepare(`UPDATE market_annotation_cloud_runs SET retry_state_json=?,next_run_at=?,
      last_failure_code=CASE WHEN ?<>'' THEN ? ELSE last_failure_code END,
      last_failure_message=CASE WHEN ?<>'' THEN ? ELSE last_failure_message END,
      lease_expires_at=datetime('now','+${CLOUD_RUN_LEASE_MINUTES} minutes'),last_heartbeat_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND state='running' AND lease_token_hash=?`)
    .bind(JSON.stringify(retry.snapshot()), nextRunAt,
      input.failureCode ?? "", (input.failureCode ?? "").slice(0, 80),
      input.failureMessage ?? "", (input.failureMessage ?? "").slice(0, 300),
      claim.jobId, claim.tokenHash).run();
  return Number(result.meta.changes ?? 0) > 0;
}

async function releaseCloudRun(db: MarketDatabase, claim: { jobId: string; tokenHash: string }, nextRunAt?: number) {
  const value = nextRunAt && nextRunAt > Date.now() ? new Date(nextRunAt).toISOString() : null;
  await db.prepare(`UPDATE market_annotation_cloud_runs SET lease_token_hash='',lease_expires_at=NULL,next_run_at=?,updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND lease_token_hash=?`).bind(value, claim.jobId, claim.tokenHash).run();
}

async function finishCloudRun(db: MarketDatabase, claim: { jobId: string; tokenHash: string }) {
  await db.prepare(`UPDATE market_annotation_cloud_runs SET state='completed',lease_token_hash='',lease_expires_at=NULL,
      next_run_at=NULL,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND lease_token_hash=?`).bind(claim.jobId, claim.tokenHash).run();
}

async function autoPauseCloudRun(db: MarketDatabase, claim: { jobId: string; tokenHash: string }, retry: AnnotationRunRetryController, code: string, message: string) {
  await db.prepare(`UPDATE market_annotation_cloud_runs SET state='paused',retry_state_json=?,lease_token_hash='',lease_expires_at=NULL,
      next_run_at=NULL,last_failure_code=?,last_failure_message=?,updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND lease_token_hash=?`)
    .bind(JSON.stringify(retry.snapshot()), code.slice(0, 80), message.slice(0, 300), claim.jobId, claim.tokenHash).run();
}

/**
 * Cloudflare scheduled() 与页面“开始/恢复”共用的持久后台执行器。
 * 每个任务只有一个协调租约；单图租约仍由 runNextCloudAnnotationInternal 原子限制。
 */
export async function runScheduledCloudAnnotations(
  db: MarketDatabase,
  input: { jobId?: string; maxRuntimeMs?: number; maxWaves?: number } = {},
) {
  await ensureAnnotationSchema(db);
  const claim = await claimCloudRun(db, input.jobId?.trim() || undefined);
  if (!claim) return { idle: true, jobId: input.jobId?.trim() || "" };
  const deadline = Date.now() + Math.max(5_000, Math.min(10 * 60_000, Math.trunc(input.maxRuntimeMs ?? CLOUD_RUN_DEFAULT_RUNTIME_MS)));
  const maxWaves = Math.max(1, Math.min(1_000, Math.trunc(input.maxWaves ?? 1_000)));
  let waves = 0;
  let processedCount = 0;
  let reusedCount = 0;
  let failedCount = 0;
  let lastFailureCode = "";
  let lastFailureMessage = "";
  try {
    const job = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs WHERE id=? LIMIT 1`).bind(claim.jobId).first<JobRow>();
    if (!job || job.executor !== "cloud") {
      await releaseCloudRun(db, claim);
      return { idle: true, jobId: claim.jobId };
    }
    const configured = await annotationConcurrency(db, job.category, "cloud");
    const controlRow = await db.prepare("SELECT retry_state_json FROM market_annotation_cloud_runs WHERE job_id=? AND lease_token_hash=?")
      .bind(claim.jobId, claim.tokenHash).first<{ retry_state_json: string }>();
    const retry = new AnnotationRunRetryController(configured, retrySnapshot(controlRow?.retry_state_json ?? "{}"));
    retry.updateTarget(configured);

    while (Date.now() < deadline && waves < maxWaves) {
      const recovered = await recoverExpiredInferenceFollowers(db, job);
      if (!recovered) break;
      waves += 1;
      processedCount += recovered;
      reusedCount += recovered;
      await refreshJob(db, claim.jobId);
      const recoveredJob = await getJob(db, claim.jobId);
      if (recoveredJob && ["review_ready", "committed", "cancelled"].includes(recoveredJob.status)) {
        await finishCloudRun(db, claim);
        return { idle: false, done: true, jobId: claim.jobId, waves, processedCount, reusedCount, failedCount };
      }
    }

    while (Date.now() < deadline && waves < maxWaves) {
      const latestConfigured = await annotationConcurrency(db, job.category, "cloud");
      if (latestConfigured !== retry.targetConcurrency) retry.updateTarget(latestConfigured);
      const now = Date.now();
      const workerIndexes = Array.from({ length: retry.workerLimit }, (_, index) => index)
        .filter((index) => retry.blockedUntil(index) <= now);
      if (!workerIndexes.length) {
        const nextRunAt = Math.min(...Array.from({ length: retry.workerLimit }, (_, index) => retry.blockedUntil(index)).filter((value) => value > now));
        if (nextRunAt - now > 2_000) {
          await persistCloudRun(db, claim, retry, { nextRunAt, failureCode: lastFailureCode, failureMessage: lastFailureMessage });
          await releaseCloudRun(db, claim, nextRunAt);
          return { idle: false, jobId: claim.jobId, waiting: true, waves, processedCount, reusedCount, failedCount, runConcurrency: retry.workerLimit };
        }
        await cloudRunDelay(Math.max(100, Math.min(1_000, nextRunAt - now)));
        continue;
      }

      const settled = await Promise.allSettled(workerIndexes.map(() => runCloudAnnotationBatch(db, claim.jobId, 1)));
      waves += 1;
      let successfulImages = 0;
      let shouldPause = false;
      let waiting = true;
      for (let index = 0; index < settled.length; index += 1) {
        const entry = settled[index]!;
        if (entry.status === "rejected") throw entry.reason;
        const result = entry.value as CloudPumpBatchResult;
        if (result.done) {
          await refreshJob(db, claim.jobId);
          await finishCloudRun(db, claim);
          return { idle: false, done: true, jobId: claim.jobId, waves, processedCount, reusedCount, failedCount };
        }
        const processed = Math.max(0, Number(result.processedCount ?? 0));
        const reused = Math.max(0, Number(result.reusedCount ?? 0));
        processedCount += processed;
        reusedCount += reused;
        if (processed > 0) waiting = false;
        const failureKind = result.failureKind;
        if (failureKind) {
          failedCount += Math.max(1, Number(result.failedCount ?? 0));
          lastFailureCode = String(result.failureCode ?? "annotation_failed").slice(0, 80);
          lastFailureMessage = String(result.failureMessage ?? "识别失败").slice(0, 300);
          if (failureKind === "rate_limit" || failureKind === "transient") {
            const decision = retry.schedule(failureKind, workerIndexes[index]!, Number(result.retryAfterMs ?? 0));
            if (decision.shouldPause) shouldPause = true;
          }
        } else if (processed > reused) {
          successfulImages += processed - reused;
        }
      }
      if (successfulImages > 0) {
        retry.recordSuccess(successfulImages);
        shouldPause = false;
      }
      if (shouldPause) {
        const reason = `运行并发降至 1 后又连续出现 3 个独立失败窗口，最近原因：${lastFailureMessage || lastFailureCode || "未返回具体原因"}`;
        await autoPauseCloudRun(db, claim, retry, lastFailureCode || "annotation_failed", reason);
        return { idle: false, paused: true, jobId: claim.jobId, waves, processedCount, reusedCount, failedCount, failureCode: lastFailureCode, failureMessage: reason };
      }
      await refreshJob(db, claim.jobId);
      const latestJob = await getJob(db, claim.jobId);
      if (latestJob && ["review_ready", "committed", "cancelled"].includes(latestJob.status)) {
        await finishCloudRun(db, claim);
        return { idle: false, done: true, jobId: claim.jobId, waves, processedCount, reusedCount, failedCount };
      }
      if (!await persistCloudRun(db, claim, retry, { failureCode: lastFailureCode, failureMessage: lastFailureMessage })) {
        return { idle: false, paused: true, jobId: claim.jobId, waves, processedCount, reusedCount, failedCount };
      }
      if (waiting) await cloudRunDelay(annotationRetryDelayMs("waiting", 0));
    }
    await releaseCloudRun(db, claim);
    return { idle: false, jobId: claim.jobId, waves, processedCount, reusedCount, failedCount, runConcurrency: retry.workerLimit };
  } catch (error) {
    await releaseCloudRun(db, claim, Date.now() + 60_000).catch(() => undefined);
    throw error;
  }
}

function cloudRunDelay(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, Math.max(1, Math.trunc(ms))));
}

export async function updateAnnotationItems(db: MarketDatabase, jobId: string, updates: Array<{ id: string; version: number; segment: string; imagePriceCents: unknown; priceType?: string; priceLowCents?: unknown; priceHighCents?: unknown; selected: boolean }>, actor: Actor) {
  if (!updates.length || updates.length > 500) throw new Error("每次必须更新 1 到 500 个明确候选项");
  const job = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=?").bind(jobId).first<JobRow>();
  if (!job) throw new Error("任务不存在");
  if (!["running", "review_ready"].includes(job.status)) throw new Error("任务当前不可复核");
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(job.prompt_version_id).first<PromptRow>();
  if (!prompt) throw new Error("Prompt 版本不存在");
  const segments = json<string[]>(prompt.segments_json, []);
  const seen = new Set<string>();
  const normalized = updates.map((update) => {
    if (!update.id || seen.has(update.id) || !Number.isSafeInteger(update.version)) throw new Error("候选项 ID 为空、重复或版本无效");
    seen.add(update.id);
    const segment = update.segment.trim();
    if (!segments.includes(segment)) throw new Error("细分品类不在 Prompt 枚举中：" + segment);
    return {
      ...update,
      segment,
      price: normalizeImagePriceCents(update.imagePriceCents),
      priceType: typeof update.priceType === "string" && update.priceType.trim() ? update.priceType.trim().slice(0, 40) : "",
      priceLow: normalizeImagePriceCents(update.priceLowCents),
      priceHigh: normalizeImagePriceCents(update.priceHighCents),
    };
  });
  const mutex = await acquireJobMutex(db, jobId, false);
  try {
    const currentById = new Map<string, { status: string; version: number; effectiveSegment: string; reviewedImagePriceCents: number | null }>();
    for (const group of chunks(normalized.map((update) => update.id))) {
      const rows = await db.prepare(`SELECT id, status, version,
          COALESCE(NULLIF(reviewed_segment,''), ai_segment) effectiveSegment,
          reviewed_image_price_cents reviewedImagePriceCents
        FROM market_annotation_items WHERE job_id=? AND id IN (${group.map(() => "?").join(",")})`)
        .bind(jobId, ...group).all<{ id: string; status: string; version: number; effectiveSegment: string; reviewedImagePriceCents: number | null }>();
      for (const row of rows.results ?? []) currentById.set(row.id, row);
    }
    const statements = [];
    for (const update of normalized) {
      const current = currentById.get(update.id);
      if (!current || !["review_pending", "approved", "rejected"].includes(current.status)) throw new Error("候选项 " + update.id + " 当前不可复核，请刷新后重试");
      const reviewContentUnchanged = current.effectiveSegment === update.segment && current.reviewedImagePriceCents === update.price;
      if (current.version !== update.version && !reviewContentUnchanged) throw new Error("候选项 " + update.id + " 的复核内容已被他人修改，系统已停止覆盖，请刷新后核对");
      const effectiveVersion = current.version;
      statements.push(db.prepare("UPDATE market_annotation_items SET reviewed_segment=?, reviewed_image_price_cents=?, reviewed_price_type=COALESCE(NULLIF(?, ''), reviewed_price_type), reviewed_price_low_cents=COALESCE(?, reviewed_price_low_cents), reviewed_price_high_cents=COALESCE(?, reviewed_price_high_cents), selected=?, status=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND job_id=? AND version=? AND status IN ('review_pending','approved','rejected')")
        .bind(update.segment, update.price, update.priceType, update.priceLow, update.priceHigh, update.selected ? 1 : 0, update.selected ? "approved" : "review_pending", actor.email, update.id, jobId, effectiveVersion));
    }
    await db.batch(statements);
    await releaseJobMutex(db, jobId, mutex, false);
    await refreshJob(db, jobId);
    return { ok: true, changed: updates.length };
  } catch (error) {
    await releaseJobMutex(db, jobId, mutex, false).catch(() => undefined);
    throw error;
  }
}

export async function rebuildStaleAnnotationItem(
  db: MarketDatabase,
  input: { candidateId: string },
  actor: Actor,
) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const candidateId = input.candidateId.trim();
  if (!/^market-item-[0-9a-f-]{36}$/i.test(candidateId)) throw new Error("失效候选项 ID 无效");
  const item = await db.prepare(`SELECT item.*, job.prompt_version_id promptVersionId, job.executor,
      CASE WHEN ${currentAnnotationSnapshotExistsSql("item")} THEN 1 ELSE 0 END snapshotValid
    FROM market_annotation_items item JOIN market_annotation_jobs job ON job.id=item.job_id
    WHERE item.id=? LIMIT 1`).bind(candidateId).first<ItemRow & {
      promptVersionId: string; executor: string; snapshotValid: number;
    }>();
  if (!item) throw new Error("失效候选项不存在");
  if (item.status === "committed" || await db.prepare("SELECT 1 ok FROM market_annotation_commit_receipts WHERE job_item_id=? LIMIT 1").bind(candidateId).first()) {
    throw new Error("候选项已经正式入库，不能重建");
  }
  if (item.snapshotValid) throw new Error("候选项当前快照仍然有效，请刷新页面后直接入库");
  const prompt = await db.prepare(`SELECT ${promptColumns} FROM market_annotation_prompt_versions WHERE id=? LIMIT 1`)
    .bind(item.promptVersionId).first<PromptRow>();
  if (!prompt) throw new Error("候选项绑定的 Prompt 已不存在，无法安全重建");
  const promptSegments = json<string[]>(prompt.segments_json, []);
  await assertPromptTaxonomyCurrent(db, item.category, promptSegments, "候选项绑定的 Prompt 枚举已不是当前细分品类字典，无法安全重建");
  const replacement = await db.prepare(`SELECT snapshot.category,snapshot.scope,snapshot.sku_code,snapshot.ranking_dimension,snapshot.month,
      COALESCE(NULLIF(current_image.content_sha256,''),snapshot.image_content_sha256,'') imageContentSha256,
      COALESCE(NULLIF(snapshot.image_url,''),ranking.image_url,'') imageUrl,
      ranking.product_name productName,ranking.brand
    FROM market_price_snapshots snapshot
    JOIN market_ranking_entries ranking ON ranking.category=snapshot.category AND ranking.scope=snapshot.scope
      AND ranking.sku_code=snapshot.sku_code AND ranking.ranking_dimension=snapshot.ranking_dimension
      AND substr(ranking.period_end,1,7)=snapshot.month
    LEFT JOIN market_image_cache current_image ON current_image.source_url=COALESCE(NULLIF(snapshot.image_url,''),ranking.image_url)
      AND current_image.status='ready' AND current_image.content_sha256<>''
    WHERE snapshot.category=? AND snapshot.scope=? AND snapshot.sku_code=? AND snapshot.ranking_dimension=? AND snapshot.month=?
    ORDER BY ranking.period_end DESC,ranking.updated_at DESC,ranking.id DESC LIMIT 1`)
    .bind(item.category, item.scope, item.sku_code, item.ranking_dimension, item.month).first<{
      category: string; scope: string; sku_code: string; ranking_dimension: string; month: string;
      imageContentSha256: string; imageUrl: string; productName: string; brand: string;
    }>();
  if (!replacement) throw new Error("该候选对应月份的当前榜单身份或价格快照已不存在；请先恢复该月份榜单数据，再重建候选");
  if (!replacement.imageContentSha256 || !replacement.imageUrl) throw new Error("当前主图尚未完成安全缓存，暂不能重建候选；请等待图片缓存完成后重试");
  if (replacement.imageContentSha256 === item.image_content_sha256) {
    throw new Error("当前图片哈希与原候选相同，但榜单身份不完整；请刷新或重新导入该月份榜单后重试");
  }
  const reusableSegment = promptSegments.includes(item.reviewed_segment) ? item.reviewed_segment : "";
  const replacementId = "market-item-" + randomUUID();
  const auditId = "market-audit-" + randomUUID();
  const statements = [
    db.prepare(`UPDATE market_price_snapshots SET image_content_sha256=?,
        ai_image_price_cents=NULL,ai_price_type='',ai_confidence_bps=NULL,ai_reason='',
        confirmed_market_price_cents=NULL,confirmed_by='',confirmed_at=NULL,
        source_job_item_id='',prompt_version_id='',
        confirmation_status=CASE WHEN source_price_cents IS NOT NULL THEN 'source_table' ELSE 'missing' END,
        updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?
        AND image_content_sha256<>?`)
      .bind(replacement.imageContentSha256, item.category, item.scope, item.sku_code, item.ranking_dimension, item.month, replacement.imageContentSha256),
    db.prepare(`INSERT INTO market_annotation_items
      (id,job_id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,product_name,brand,
        source_image_url,status,reviewed_segment,reviewed_by,reviewed_at)
      SELECT ?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,CASE WHEN ?='' THEN NULL ELSE CURRENT_TIMESTAMP END
      WHERE EXISTS (SELECT 1 FROM market_price_snapshots snapshot
        JOIN market_ranking_entries ranking ON ranking.category=snapshot.category AND ranking.scope=snapshot.scope
          AND ranking.sku_code=snapshot.sku_code AND ranking.ranking_dimension=snapshot.ranking_dimension
          AND substr(ranking.period_end,1,7)=snapshot.month
        WHERE snapshot.category=? AND snapshot.scope=? AND snapshot.sku_code=? AND snapshot.ranking_dimension=?
          AND snapshot.month=? AND snapshot.image_url=? AND snapshot.image_content_sha256=?
          AND COALESCE(NULLIF((SELECT cache.content_sha256 FROM market_image_cache cache
            WHERE cache.source_url=snapshot.image_url AND cache.status='ready' AND cache.content_sha256<>'' LIMIT 1),''),
            snapshot.image_content_sha256)=?)`)
      .bind(replacementId, item.job_id, item.category, item.scope, item.sku_code, item.ranking_dimension, item.month,
        replacement.imageContentSha256, replacement.productName, replacement.brand, replacement.imageUrl,
        reusableSegment, reusableSegment ? HISTORY_SAME_SKU_SEGMENT_REVIEWER : "", reusableSegment,
        item.category, item.scope, item.sku_code, item.ranking_dimension, item.month, replacement.imageUrl,
        replacement.imageContentSha256, replacement.imageContentSha256),
    db.prepare(`UPDATE market_annotation_items SET status='superseded',selected=0,
        error_message='候选图片版本已变化，已重建为 ' || ?,lease_token_hash='',lease_agent_id='',lease_expires_at=NULL,
        version=version+1,updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND status<>'committed' AND EXISTS (SELECT 1 FROM market_annotation_items replacement WHERE replacement.id=?)`)
      .bind(replacementId, candidateId, replacementId),
    db.prepare(`INSERT INTO market_master_audit_logs
      (id,actor_email,actor_role,action,entity_type,entity_id,before_json,after_json)
      SELECT ?,?,?,'rebuild_stale_market_annotation_item','market_annotation_item',?,?,?
      FROM market_annotation_items replacement WHERE replacement.id=?`)
      .bind(auditId, actor.email, actor.role, candidateId,
        JSON.stringify({ candidateId, imageContentSha256: item.image_content_sha256, status: item.status }),
        JSON.stringify({ replacementCandidateId: replacementId, imageContentSha256: replacement.imageContentSha256,
          recognitionMode: reusableSegment ? "price_only" : "full" }), replacementId),
  ];
  if (item.executor === "cloud") {
    statements.push(db.prepare(`UPDATE market_annotation_cloud_runs SET state='paused',next_run_at=NULL,
      lease_token_hash='',lease_expires_at=NULL,completed_at=NULL,updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND EXISTS (SELECT 1 FROM market_annotation_items replacement WHERE replacement.id=?)`)
      .bind(item.job_id, replacementId));
  }
  const mutex = await acquireJobMutex(db, item.job_id, false);
  try {
    const latest = await db.prepare(`SELECT current_item.status,
        CASE WHEN ${currentAnnotationSnapshotExistsSql("current_item")} THEN 1 ELSE 0 END snapshotValid,
        EXISTS (SELECT 1 FROM market_annotation_commit_receipts receipt WHERE receipt.job_item_id=current_item.id) committed
      FROM market_annotation_items current_item WHERE current_item.id=? LIMIT 1`)
      .bind(candidateId).first<{ status: string; snapshotValid: number; committed: number }>();
    if (!latest || latest.status === "committed" || latest.committed) throw new Error("候选项已经正式入库，不能重建");
    if (latest.snapshotValid) throw new Error("候选项当前快照仍然有效，请刷新页面后直接入库");
    const results = await db.batch(statements) as Array<{ meta?: { changes?: number } }>;
    if (!Number(results[1]?.meta?.changes ?? 0)) throw new Error("重建期间当前快照再次变化，请刷新后重试");
    await releaseJobMutex(db, item.job_id, mutex, false);
    await refreshJob(db, item.job_id);
    return {
      ok: true,
      jobId: item.job_id,
      supersededCandidateId: candidateId,
      replacementCandidateId: replacementId,
      recognitionMode: reusableSegment ? "price_only" : "full",
    };
  } catch (error) {
    await releaseJobMutex(db, item.job_id, mutex, false).catch(() => undefined);
    throw error;
  }
}

export async function rebuildSelectedStaleAnnotationItems(
  db: MarketDatabase,
  input: { jobId?: string; aggregateJobs?: boolean; category?: string; categories?: string[] },
  actor: Actor,
) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const categories = annotationCategoryList(input.categories, input.category);
  const clauses = [
    "item.status='approved'",
    "item.selected=1",
    "job.status IN ('running','review_ready')",
    `NOT (${currentAnnotationSnapshotExistsSql("item")})`,
  ];
  const bindings: string[] = [];
  if (input.aggregateJobs) {
    if (categories.length) {
      clauses.push(`item.category IN (${categories.map(() => "?").join(",")})`);
      bindings.push(...categories);
    }
  } else {
    const jobId = input.jobId?.trim() ?? "";
    if (!jobId) throw new Error("任务 ID 不能为空");
    clauses.push("item.job_id=?");
    bindings.push(jobId);
  }
  const where = clauses.join(" AND ");
  const rows = await db.prepare(`SELECT item.id FROM market_annotation_items item
    JOIN market_annotation_jobs job ON job.id=item.job_id
    WHERE ${where}
    ORDER BY job.created_at,item.created_at,item.id LIMIT ${STALE_REBUILD_BATCH_SIZE}`)
    .bind(...bindings).all<{ id: string }>();
  let rebuilt = 0;
  let priceOnly = 0;
  let fullRecognition = 0;
  const affectedCloudJobs = new Set<string>();
  let partialError = "";
  for (const row of rows.results ?? []) {
    try {
      const result = await rebuildStaleAnnotationItem(db, { candidateId: row.id }, actor);
      rebuilt += 1;
      if (result.recognitionMode === "price_only") priceOnly += 1;
      else fullRecognition += 1;
      const job = await db.prepare("SELECT executor FROM market_annotation_jobs WHERE id=? LIMIT 1")
        .bind(result.jobId).first<{ executor: string }>();
      if (job?.executor === "cloud") affectedCloudJobs.add(result.jobId);
    } catch (error) {
      if (!rebuilt) throw error;
      partialError = safeOperationalError(error, "部分过期候选重建失败，请刷新后继续处理");
      break;
    }
  }
  const remaining = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items item
    JOIN market_annotation_jobs job ON job.id=item.job_id WHERE ${where}`)
    .bind(...bindings).first<{ count: number }>();
  const remainingStale = Number(remaining?.count ?? 0);
  const resumedJobIds: string[] = [];
  for (const jobId of affectedCloudJobs) {
    const jobRemaining = await db.prepare(`SELECT COUNT(*) count FROM market_annotation_items item
      WHERE item.job_id=? AND item.status='approved' AND item.selected=1
        AND NOT (${currentAnnotationSnapshotExistsSql("item")})`).bind(jobId).first<{ count: number }>();
    if (!Number(jobRemaining?.count ?? 0)) {
      await setCloudAnnotationRunState(db, { jobId, state: "running" }, actor);
      resumedJobIds.push(jobId);
    }
  }
  return {
    ok: !partialError,
    partial: Boolean(partialError),
    error: partialError || undefined,
    rebuilt,
    priceOnly,
    fullRecognition,
    remainingStale,
    hasMore: remainingStale > 0,
    resumedJobIds,
  };
}

export async function commitAnnotationItems(db: MarketDatabase, input: { jobId: string; candidateIds: string[]; idempotencyKey: string }, actor: Actor) {
  await ensureMarketSchemaLazy(db);
  await ensureAnnotationSchema(db);
  const ids = [...new Set(input.candidateIds.map((id) => id.trim()).filter(Boolean))];
  if (!ids.length || ids.length > 500 || ids.length !== input.candidateIds.length) throw new Error("必须提交 1 到 500 个不重复的明确 candidate/item ID");
  const idempotencyKey = input.idempotencyKey.trim();
  if (!/^[A-Za-z0-9:_-]{12,160}$/.test(idempotencyKey)) throw new Error("批量入库幂等键无效");
  const job = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=?").bind(input.jobId).first<JobRow>();
  if (!job) throw new Error("任务不存在");
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(job.prompt_version_id).first<PromptRow>();
  const segments = prompt ? json<string[]>(prompt.segments_json, []) : [];
  await assertPromptTaxonomyCurrent(db, job.category, segments, "任务绑定的 Prompt 枚举已不是当前细分品类字典，禁止继续入库");
  const requestDigest = digest(JSON.stringify({ jobId: job.id, ids: [...ids].sort(), idempotencyKey }));
  const batchId = "market-commit-batch-" + digest(`${job.id}:${idempotencyKey}`).slice(0, 32);
  const priorBatch = await db.prepare("SELECT request_digest requestDigest, COUNT(*) count FROM market_annotation_commit_receipts WHERE batch_id=? GROUP BY request_digest LIMIT 1").bind(batchId).first<{ requestDigest: string; count: number }>();
  if (priorBatch && priorBatch.requestDigest !== requestDigest) throw new Error("幂等键已用于不同的提交请求");
  if (priorBatch && Number(priorBatch.count) === ids.length) return { ok: true, committed: 0, duplicates: ids.length, batchId, job: await getJob(db, job.id) };
  if (!["review_ready", "committing"].includes(job.status)) throw new Error("任务必须完成识别并进入待复核状态后才能入库");

  const mutex = await acquireJobMutex(db, job.id, true);
  try {
    const prepared: Array<{ item: ItemRow; old: Record<string, unknown> | null; annotationId: string; after: Record<string, unknown>; receiptKey: string }> = [];
    const committedAt = new Date().toISOString();
    let duplicates = 0;
    const receiptByItemId = new Map<string, { requestDigest: string }>();
    for (const group of chunks(ids)) {
      const receipts = await db.prepare(`SELECT job_item_id jobItemId, request_digest requestDigest
        FROM market_annotation_commit_receipts WHERE job_item_id IN (${group.map(() => "?").join(",")})`)
        .bind(...group).all<{ jobItemId: string; requestDigest: string }>();
      for (const receipt of receipts.results ?? []) receiptByItemId.set(receipt.jobItemId, receipt);
    }
    const itemById = new Map<string, ItemRow & { snapshot_valid: number }>();
    for (const group of chunks(ids)) {
      const items = await db.prepare(`SELECT item.*,
          CASE WHEN ${currentAnnotationSnapshotExistsSql("item")} THEN 1 ELSE 0 END snapshot_valid
        FROM market_annotation_items item WHERE item.job_id=? AND item.id IN (${group.map(() => "?").join(",")})`)
        .bind(job.id, ...group).all<ItemRow & { snapshot_valid: number }>();
      for (const item of items.results ?? []) itemById.set(item.id, item);
    }
    const oldBySku = new Map<string, Record<string, unknown>>();
    for (const group of chunks([...new Set([...itemById.values()].map((item) => item.sku_code))])) {
      const oldRows = await db.prepare(`SELECT id, category, sku_code skuCode, segment, image_price_cents imagePriceCents,
          image_url imageUrl, image_source imageSource, confidence_bps confidenceBps, source_job_item_id sourceJobItemId,
          prompt_version_id promptVersionId, reviewed_by reviewedBy, reviewed_at reviewedAt, version,
          created_at createdAt, updated_at updatedAt
        FROM market_sku_annotations WHERE category=? AND sku_code IN (${group.map(() => "?").join(",")})`)
        .bind(job.category, ...group).all<Record<string, unknown>>();
      for (const old of oldRows.results ?? []) oldBySku.set(String(old.skuCode), old);
    }
    for (const itemId of ids) {
      const receiptKey = idempotencyKey + ":" + itemId;
      const receipt = receiptByItemId.get(itemId);
      if (receipt) {
        if (receipt.requestDigest !== requestDigest) throw new Error("候选项已由不同请求入库：" + itemId);
        duplicates += 1;
        continue;
      }
      const item = itemById.get(itemId);
      if (!item) throw new Error("候选项不存在：" + itemId);
      if (item.status !== "approved" || !item.selected || !segments.includes(item.reviewed_segment)) throw new Error("候选项 " + itemId + " 未经勾选批准或品类无效");
      if (!item.snapshot_valid) throw new Error("候选项 " + itemId + " 对应的榜单身份、价格快照或图片版本已变化，请重建任务后再入库");
      const old = oldBySku.get(item.sku_code) ?? null;
      const annotationId = String(old?.id ?? ("market-annotation-" + randomUUID()));
      const after = { id: annotationId, category: job.category, skuCode: item.sku_code, rankingDimension: item.ranking_dimension, month: item.month, imageContentSha256: item.image_content_sha256, segment: item.reviewed_segment, imagePriceCents: item.reviewed_image_price_cents, priceType: item.reviewed_price_type || item.ai_price_type, priceLowCents: item.reviewed_price_low_cents, priceHighCents: item.reviewed_price_high_cents, imageUrl: item.resolved_image_url || item.source_image_url, imageSource: item.image_source, confidenceBps: item.ai_confidence_bps, sourceJobItemId: item.id, promptVersionId: job.prompt_version_id, reviewedBy: actor.email, reviewedAt: committedAt, version: Number(old?.version ?? 0) + 1, createdAt: old?.createdAt ?? committedAt, updatedAt: committedAt, batchId, requestDigest };
      prepared.push({ item, old, annotationId, after, receiptKey });
    }
    let committed = 0;
    for (let offset = 0; offset < prepared.length; offset += 25) {
      const chunk = prepared.slice(offset, offset + 25);
      const statements = chunk.flatMap(({ item, old, annotationId, after, receiptKey }) => {
        const priceType = item.reviewed_price_type || item.ai_price_type || "无法判断";
        const formalPrice = ["定金", "分期金额", "无法判断"].includes(priceType) ? null : item.reviewed_image_price_cents;
        const reusableStandardPrice = item.ranking_dimension === "SKU" && priceType === "标准售价" ? formalPrice : null;
        const status = formalPrice === null ? "review_pending" : "confirmed";
        const snapshotGuardId = "market-snapshot-guard-" + randomUUID();
        return [
          db.prepare(`INSERT INTO market_master_audit_logs
            (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
            SELECT CASE WHEN ${currentAnnotationSnapshotExistsSql("guard_item")} THEN ? ELSE NULL END,
              ?, ?, 'market_annotation_snapshot_guard', 'market_price_snapshot', ?, '{}', '{}'
            FROM market_annotation_items guard_item WHERE guard_item.id=?`)
            .bind(snapshotGuardId, actor.email, actor.role,
              `${item.category || job.category}|${item.scope}|${item.ranking_dimension}|${item.sku_code}|${item.month}`, item.id),
          db.prepare("INSERT INTO market_sku_annotations (id, category, sku_code, segment, image_price_cents, image_url, image_source, confidence_bps, source_job_item_id, prompt_version_id, reviewed_by, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(category, sku_code) DO UPDATE SET segment=excluded.segment, image_price_cents=excluded.image_price_cents, image_url=excluded.image_url, image_source=excluded.image_source, confidence_bps=excluded.confidence_bps, source_job_item_id=excluded.source_job_item_id, prompt_version_id=excluded.prompt_version_id, reviewed_by=excluded.reviewed_by, reviewed_at=CURRENT_TIMESTAMP, version=market_sku_annotations.version+1, updated_at=CURRENT_TIMESTAMP")
            .bind(annotationId, job.category, item.sku_code, item.reviewed_segment, item.reviewed_image_price_cents, after.imageUrl, item.image_source, item.ai_confidence_bps, item.id, job.prompt_version_id, actor.email),
          db.prepare(`UPDATE market_price_snapshots SET
            ai_image_price_cents=?, ai_price_type=?, ai_confidence_bps=?, ai_reason=?,
            price_low_cents=COALESCE(?, price_low_cents), price_high_cents=COALESCE(?, price_high_cents),
            confirmed_market_price_cents=?, image_content_sha256=?,
            image_url=CASE WHEN ? <> '' THEN ? ELSE image_url END,
            confirmation_status=?, confirmed_by=?, confirmed_at=CASE WHEN ? IS NULL THEN confirmed_at ELSE CURRENT_TIMESTAMP END,
            source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?
              AND image_content_sha256=?`)
            .bind(item.ai_image_price_cents, priceType, item.ai_confidence_bps, item.ai_reason, item.reviewed_price_low_cents, item.reviewed_price_high_cents, formalPrice, item.image_content_sha256, after.imageUrl, after.imageUrl, status, actor.email, formalPrice, item.id, job.prompt_version_id, item.category || job.category, item.scope, item.sku_code, item.ranking_dimension, item.month, item.image_content_sha256),
          db.prepare(`UPDATE market_price_snapshots SET
            ai_image_price_cents=?, ai_price_type='标准售价',
            confirmed_market_price_cents=?, confirmation_status='confirmed', confirmed_by='system:history_same_image', confirmed_at=CURRENT_TIMESTAMP,
            source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension='SKU' AND image_content_sha256=?
              AND confirmed_market_price_cents IS NULL AND ? IS NOT NULL`)
            .bind(reusableStandardPrice, reusableStandardPrice, item.id, job.prompt_version_id,
              item.category || job.category, item.scope, item.sku_code, item.image_content_sha256, reusableStandardPrice),
          db.prepare("UPDATE market_ranking_entries SET subcategory=?, source_subcategory=?, updated_at=CURRENT_TIMESTAMP WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=?")
            .bind(item.reviewed_segment, item.reviewed_segment, item.category || job.category, item.scope, item.sku_code, item.ranking_dimension),
          db.prepare("INSERT INTO market_annotation_commit_receipts (id, job_item_id, annotation_id, idempotency_key, before_json, after_json, committed_by, batch_id, request_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
            .bind("market-commit-" + randomUUID(), item.id, annotationId, receiptKey, JSON.stringify(old ?? {}), JSON.stringify(after), actor.email, batchId, requestDigest),
          db.prepare("UPDATE market_annotation_items SET status='committed', selected=0, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='approved' AND selected=1 AND version=?").bind(item.id, item.version),
          db.prepare("DELETE FROM market_master_audit_logs WHERE id=? AND action='market_annotation_snapshot_guard'").bind(snapshotGuardId),
        ];
      });
      try { await db.batch(statements); committed += chunk.length; }
      catch (error) {
        await releaseJobMutex(db, job.id, mutex, true);
        await refreshJob(db, job.id);
        return { ok: false, partial: committed > 0, committed, duplicates, failed: prepared.length - committed, batchId, requestDigest, error: safeOperationalError(error, "入库批次执行失败，可使用相同幂等键安全续跑"), job: await getJob(db, job.id) };
      }
    }
    await releaseJobMutex(db, job.id, mutex, true);
    await refreshJob(db, job.id);
    return { ok: true, committed, duplicates, batchId, requestDigest, job: await getJob(db, job.id) };
  } catch (error) {
    await releaseJobMutex(db, job.id, mutex, true).catch(() => undefined);
    throw error;
  }
}

export async function markAnnotationsAsGold(db: MarketDatabase, annotationIds: string[], actor: Actor) {
  const ids = [...new Set(annotationIds.map((id) => id.trim()).filter(Boolean))];
  if (!ids.length || ids.length > 500) throw new Error("请选择 1 到 500 条已复核细分品类数据");
  let saved = 0;
  for (const id of ids) {
    const row = await db.prepare("SELECT id, category, sku_code, segment, image_price_cents, image_url FROM market_sku_annotations WHERE id=?").bind(id).first<{ id: string; category: string; sku_code: string; segment: string; image_price_cents: number | null; image_url: string }>();
    if (!row) throw new Error("细分品类记录不存在：" + id);
    const market = await db.prepare("SELECT product_name, brand FROM market_ranking_entries WHERE category=? AND sku_code=? ORDER BY period_end DESC, updated_at DESC LIMIT 1").bind(row.category, row.sku_code).first<{ product_name: string; brand: string }>();
    await db.prepare("INSERT INTO market_annotation_validation_samples (id, category, sku_code, product_name, brand, image_url, gold_segment, gold_image_price_cents, source_annotation_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(category, sku_code) DO UPDATE SET product_name=excluded.product_name, brand=excluded.brand, image_url=excluded.image_url, gold_segment=excluded.gold_segment, gold_image_price_cents=excluded.gold_image_price_cents, source_annotation_id=excluded.source_annotation_id, created_by=excluded.created_by, created_at=CURRENT_TIMESTAMP")
      .bind("market-gold-" + randomUUID(), row.category, row.sku_code, market?.product_name ?? "", market?.brand ?? "", row.image_url, row.segment, row.image_price_cents, row.id, actor.email).run();
    saved += 1;
  }
  return { ok: true, saved };
}

async function acquireJobMutex(db: MarketDatabase, jobId: string, forCommit: boolean) {
  await db.prepare("UPDATE market_annotation_jobs SET status=CASE WHEN status='committing' THEN 'review_ready' ELSE status END, commit_token_hash='', commit_started_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND commit_token_hash<>'' AND datetime(commit_started_at)<=datetime('now','-5 minutes')")
    .bind(jobId).run();
  const token = randomBytes(24).toString("hex");
  const tokenHash = digest(token);
  const result = forCommit
    ? await db.prepare("UPDATE market_annotation_jobs SET status='committing', commit_token_hash=?, commit_started_at=datetime('now'), updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='review_ready' AND commit_token_hash=''").bind(tokenHash, jobId).run()
    : await db.prepare("UPDATE market_annotation_jobs SET commit_token_hash=?, commit_started_at=datetime('now'), updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('running','review_ready') AND commit_token_hash=''").bind(tokenHash, jobId).run();
  if (!Number(result.meta.changes ?? 0)) throw new Error(forCommit ? "任务正在复核或入库，请稍后重试" : "任务正在执行入库，请刷新后重试");
  return tokenHash;
}

async function releaseJobMutex(db: MarketDatabase, jobId: string, tokenHash: string, forCommit: boolean) {
  await db.prepare(`UPDATE market_annotation_jobs SET status=CASE WHEN ?=1 AND status='committing' THEN 'review_ready' ELSE status END, commit_token_hash='', commit_started_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND commit_token_hash=?`)
    .bind(forCommit ? 1 : 0, jobId, tokenHash).run();
}

async function getJob(db: MarketDatabase, id: string) {
  const row = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=?").bind(id).first<JobRow>();
  return row ? jobValue(row) : null;
}

export async function getAnnotationJobProgress(db: MarketDatabase, jobId: string) {
  await ensureAnnotationSchema(db);
  const id = jobId.trim();
  if (!id) throw new Error("任务 ID 不能为空");
  await refreshJob(db, id);
  const job = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=? LIMIT 1").bind(id).first<JobRow>();
  if (!job) throw new Error("任务不存在");
  const [metrics, performance, cloudRun] = await Promise.all([db.prepare(`SELECT
      (SELECT COUNT(*) FROM market_annotation_items active WHERE active.job_id=?
        AND active.status IN ('claimed','inferencing') AND active.lease_expires_at IS NOT NULL
        AND datetime(active.lease_expires_at)>datetime('now')) active_claims,
      (SELECT COUNT(*) FROM (SELECT 1 FROM market_annotation_items unit WHERE unit.job_id=?
        GROUP BY unit.category, unit.scope, unit.sku_code, unit.ranking_dimension, unit.image_content_sha256)) unique_inference_units,
      (SELECT COUNT(*) FROM (SELECT 1 FROM market_annotation_items remaining WHERE remaining.job_id=?
        AND (remaining.status IN ('queued','claimed','inferencing') OR (remaining.status='failed' AND remaining.attempt_count<3))
        GROUP BY remaining.category, remaining.scope, remaining.sku_code, remaining.ranking_dimension, remaining.image_content_sha256)) remaining_inference_units`)
    .bind(id, id, id).first<{ active_claims: number; unique_inference_units: number; remaining_inference_units: number }>(),
    db.prepare(`SELECT COUNT(*) measured_count,AVG(image_load_ms) image_load_ms,AVG(image_prepare_ms) image_prepare_ms,
        AVG(model_call_ms) model_call_ms,AVG(total_inference_ms) total_inference_ms,AVG(model_input_bytes) model_input_bytes
      FROM (SELECT image_load_ms,image_prepare_ms,model_call_ms,total_inference_ms,model_input_bytes
        FROM market_annotation_items WHERE job_id=? AND total_inference_ms>0
        ORDER BY datetime(updated_at) DESC LIMIT 100)`).bind(id).first<Record<string, number>>(),
    job.executor === "cloud" ? getCloudRunControl(db, id, await annotationConcurrency(db, job.category, "cloud")) : Promise.resolve(null),
  ]);
  return {
    job: jobValue({ ...job, remaining_inference_count: Number(metrics?.remaining_inference_units ?? 0) }),
    activeClaims: Number(metrics?.active_claims ?? 0),
    uniqueInferenceUnits: Number(metrics?.unique_inference_units ?? 0),
    remainingInferenceUnits: Number(metrics?.remaining_inference_units ?? 0),
    cloudRun,
    performance: {
      measuredCount: Number(performance?.measured_count ?? 0),
      averageImageLoadMs: Math.round(Number(performance?.image_load_ms ?? 0)),
      averageImagePrepareMs: Math.round(Number(performance?.image_prepare_ms ?? 0)),
      averageModelCallMs: Math.round(Number(performance?.model_call_ms ?? 0)),
      averageTotalInferenceMs: Math.round(Number(performance?.total_inference_ms ?? 0)),
      averageModelInputBytes: Math.round(Number(performance?.model_input_bytes ?? 0)),
    },
  };
}

async function refreshJob(db: MarketDatabase, jobId: string) {
  const counts = await db.prepare("SELECT SUM(CASE WHEN status<>'superseded' THEN 1 ELSE 0 END) total, SUM(CASE WHEN status IN ('review_pending','approved','rejected','committed') THEN 1 ELSE 0 END) completed, SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed, SUM(CASE WHEN status IN ('approved','rejected','committed') THEN 1 ELSE 0 END) reviewed, SUM(CASE WHEN status='committed' THEN 1 ELSE 0 END) committed, SUM(CASE WHEN status IN ('queued','claimed','inferencing') OR (status='failed' AND attempt_count<3) THEN 1 ELSE 0 END) remaining, SUM(CASE WHEN status='superseded' THEN 1 ELSE 0 END) superseded FROM market_annotation_items WHERE job_id=?")
    .bind(jobId).first<Record<string, number>>();
  if (!counts) return;
  const current = await db.prepare("SELECT status FROM market_annotation_jobs WHERE id=?").bind(jobId).first<{ status: string }>();
  let status = current?.status ?? "running";
  if (!["cancelled", "committed", "deleted"].includes(status)) {
    if (Number(counts.total) === 0) status = Number(counts.superseded) > 0 ? "cancelled" : "review_ready";
    else if (Number(counts.committed) === Number(counts.total)) status = "committed";
    else if (Number(counts.remaining) === 0) status = "review_ready";
    else status = "running";
  }
  await db.prepare("UPDATE market_annotation_jobs SET status=?, total_count=?, completed_count=?, failed_count=?, reviewed_count=?, committed_count=?, completed_at=CASE WHEN ? IN ('review_ready','committed') THEN CURRENT_TIMESTAMP ELSE completed_at END, updated_at=CURRENT_TIMESTAMP WHERE id=?")
    .bind(status, Number(counts.total), Number(counts.completed), Number(counts.failed), Number(counts.reviewed), Number(counts.committed), status, jobId).run();
}

export async function createValidationRun(db: MarketDatabase, input: { candidatePromptId: string; modelId: string; sampleCount?: number; seed?: string }, actor: Actor) {
  await ensureAnnotationSchema(db);
  await ensureMarketSchemaLazy(db);
  const candidate = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(input.candidatePromptId).first<PromptRow>();
  if (!candidate) throw new Error("候选 Prompt 不存在");
  await assertPromptTaxonomyCurrent(db, candidate.category, json<string[]>(candidate.segments_json, []), "候选 Prompt 的细分品类枚举已过期，不能创建冻结验证");
  const baseline = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE category=? AND status='active' LIMIT 1").bind(candidate.category).first<PromptRow>();
  if (baseline) await assertPromptTaxonomyCurrent(db, baseline.category, json<string[]>(baseline.segments_json, []), "基线 Prompt 的细分品类枚举已过期，不能创建冻结验证");
  const model = await db.prepare("SELECT id FROM ai_models WHERE id=? AND status='enabled' AND model_type IN ('vision','image')").bind(input.modelId).first<{ id: string }>();
  if (!model) throw new Error("冻结验证必须选择已启用的视觉模型");
  const samples = await db.prepare("SELECT id, category, sku_code, product_name, brand, image_url, gold_segment, gold_image_price_cents FROM market_annotation_validation_samples WHERE category=? ORDER BY id").bind(candidate.category).all<ValidationSampleRow>();
  const requestedCount = strictInteger(input.sampleCount, 50, 50, 500, "sampleCount");
  if (samples.results.length < requestedCount) throw new Error(`冻结验证至少需要 ${requestedCount} 条金标，当前只有 ${samples.results.length} 条`);
  const seed = (input.seed?.trim() || "market-annotation-v1").slice(0, 120);
  const selected = stableStratifiedSample(samples.results.map((row) => ({ ...row, goldSegment: row.gold_segment })), requestedCount, seed);
  if (selected.length !== requestedCount) throw new Error(`冻结验证未能生成 ${requestedCount} 条完整样本`);
  const snapshots: ValidationSnapshot[] = selected.map((row) => ({ id: row.id, skuCode: row.sku_code, productName: row.product_name, brand: row.brand, imageUrl: row.image_url, goldSegment: row.gold_segment, goldImagePriceCents: row.gold_image_price_cents }));
  const runId = "market-validation-" + randomUUID();
  const sampleHash = digest(JSON.stringify({ seed, modelId: input.modelId, samples: [...snapshots].sort((a, b) => a.id.localeCompare(b.id)) }));
  const insertedRun = await db.prepare(`INSERT INTO market_annotation_validation_runs
      (id, category, baseline_prompt_id, candidate_prompt_id, model_id, status, seed, requested_sample_count, sample_count, sample_hash, created_by)
    SELECT ?, current_prompt.category, ?, current_prompt.id, ?, 'queued', ?, ?, ?, ?, ?
    FROM market_annotation_prompt_versions current_prompt
    WHERE current_prompt.id=? AND (NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy
      WHERE taxonomy.category=current_prompt.category AND taxonomy.status='active') OR (
        NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy
          WHERE taxonomy.category=current_prompt.category AND taxonomy.status='active'
            AND NOT EXISTS (SELECT 1 FROM json_each(current_prompt.segments_json) segment
              WHERE CAST(segment.value AS TEXT)=taxonomy.subcategory))
        AND NOT EXISTS (SELECT 1 FROM json_each(current_prompt.segments_json) segment WHERE NOT EXISTS (
          SELECT 1 FROM market_subcategory_taxonomy taxonomy WHERE taxonomy.category=current_prompt.category
            AND taxonomy.status='active' AND taxonomy.subcategory=CAST(segment.value AS TEXT)))
      ))`)
    .bind(runId, baseline && baseline.id !== candidate.id ? baseline.id : null, input.modelId, seed, requestedCount,
      selected.length, sampleHash, actor.email, candidate.id).run() as { meta?: { changes?: number } };
  if (!Number(insertedRun.meta?.changes ?? 0)) throw new Error("候选 Prompt 或细分品类字典已变化，请刷新后重建冻结验证");
  const promptIds = [...new Set([candidate.id, baseline && baseline.id !== candidate.id ? baseline.id : null].filter(Boolean) as string[])];
  for (let offset = 0; offset < snapshots.length; offset += 50) {
    const statements = snapshots.slice(offset, offset + 50).flatMap((sample) => promptIds.map((promptId) =>
      db.prepare("INSERT INTO market_annotation_validation_results (id, run_id, sample_id, prompt_version_id, status, sample_snapshot_json, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, CURRENT_TIMESTAMP)")
        .bind("market-validation-result-" + randomUUID(), runId, sample.id, promptId, JSON.stringify(sample))));
    await db.batch(statements);
  }
  return { id: runId, category: candidate.category, candidatePromptId: candidate.id, baselinePromptId: baseline && baseline.id !== candidate.id ? baseline.id : null, modelId: input.modelId, status: "queued", seed, sampleCount: selected.length, sampleHash };
}

export async function runNextValidation(db: MarketDatabase, runId: string) {
  const run = await db.prepare("SELECT id, category, baseline_prompt_id, candidate_prompt_id, model_id, status, metrics_json, gate_json FROM market_annotation_validation_runs WHERE id=?").bind(runId).first<{ id: string; category: string; baseline_prompt_id: string | null; candidate_prompt_id: string; model_id: string; status: string; metrics_json: string; gate_json: string }>();
  if (!run) throw new Error("冻结验证运行不存在");
  if (run.status === "completed") return { done: true, runId, metrics: json(run.metrics_json, {}), gate: json(run.gate_json, {}) };
  await db.prepare("UPDATE market_annotation_validation_results SET status=CASE WHEN attempt_count>=3 THEN 'failed' ELSE 'queued' END, claim_token_hash='', lease_expires_at=NULL, error_message=CASE WHEN attempt_count>=3 THEN '验证租约连续超时，已达到最大尝试次数' ELSE error_message END, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='inferencing' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now')")
    .bind(runId).run();
  const result = await db.prepare("SELECT id, run_id, sample_id, prompt_version_id, status, predicted_segment, predicted_image_price_cents, confidence_bps, is_correct, error_message, sample_snapshot_json, claim_token_hash, lease_expires_at, attempt_count, updated_at FROM market_annotation_validation_results WHERE run_id=? AND status IN ('queued','failed') AND attempt_count<3 ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, sample_id, prompt_version_id LIMIT 1").bind(runId).first<ValidationResultRow>();
  if (!result) {
    const pending = await db.prepare("SELECT COUNT(*) count FROM market_annotation_validation_results WHERE run_id=? AND status IN ('queued','inferencing')").bind(runId).first<{ count: number }>();
    if (!Number(pending?.count ?? 0)) return { done: true, ...(await finalizeValidationRun(db, runId)) };
    return { done: false, waiting: true };
  }
  const claimHash = digest(randomBytes(24).toString("hex"));
  const claimed = await db.prepare("UPDATE market_annotation_validation_results SET status='inferencing', claim_token_hash=?, lease_expires_at=datetime('now','+2 minutes'), attempt_count=attempt_count+1, error_message='', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','failed') AND attempt_count<3")
    .bind(claimHash, result.id).run();
  if (!Number(claimed.meta.changes ?? 0)) return { done: false, raced: true };
  await db.prepare("UPDATE market_annotation_validation_runs SET status='running' WHERE id=? AND status='queued'").bind(runId).run();
  const snapshot = json<ValidationSnapshot | null>(result.sample_snapshot_json, null);
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(result.prompt_version_id).first<PromptRow>();
  if (!snapshot?.skuCode || !snapshot.goldSegment || !prompt) throw new Error("冻结样本快照或 Prompt 版本已丢失");
  try {
    const prediction = await runVisionAnnotation({ db, modelId: run.model_id, promptBody: prompt.prompt_body, segments: json(prompt.segments_json, []), skuCode: snapshot.skuCode, productName: snapshot.productName, brand: snapshot.brand, imageUrl: snapshot.imageUrl });
    await db.prepare("UPDATE market_annotation_validation_results SET status='completed', predicted_segment=?, predicted_image_price_cents=?, confidence_bps=?, is_correct=?, error_message='', claim_token_hash='', lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='inferencing' AND claim_token_hash=? AND datetime(lease_expires_at)>datetime('now')")
      .bind(prediction.segment, prediction.imagePriceCents, prediction.confidenceBps, prediction.segment === snapshot.goldSegment ? 1 : 0, result.id, claimHash).run();
  } catch (error) {
    await db.prepare("UPDATE market_annotation_validation_results SET status='failed', error_message=?, claim_token_hash='', lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='inferencing' AND claim_token_hash=? AND datetime(lease_expires_at)>datetime('now')")
      .bind(safeOperationalError(error, "验证识别失败"), result.id, claimHash).run();
  }
  return { done: false, resultId: result.id };
}

async function finalizeValidationRun(db: MarketDatabase, runId: string) {
  const run = await db.prepare("SELECT candidate_prompt_id candidatePromptId, baseline_prompt_id baselinePromptId, status, metrics_json metricsJson, gate_json gateJson FROM market_annotation_validation_runs WHERE id=?").bind(runId).first<{ candidatePromptId: string; baselinePromptId: string | null; status: string; metricsJson: string; gateJson: string }>();
  if (!run) throw new Error("冻结验证运行不存在");
  if (run.status === "completed") return { runId, metrics: json(run.metricsJson, {}), gate: json(run.gateJson, {}) };
  const pending = await db.prepare("SELECT COUNT(*) count FROM market_annotation_validation_results WHERE run_id=? AND status IN ('queued','inferencing')").bind(runId).first<{ count: number }>();
  if (Number(pending?.count ?? 0)) throw new Error("冻结验证仍有未完成结果，不能结算");
  const resultRows = await db.prepare("SELECT prompt_version_id promptVersionId, predicted_segment predictedSegment, predicted_image_price_cents predictedImagePriceCents, sample_snapshot_json sampleSnapshotJson FROM market_annotation_validation_results WHERE run_id=?").bind(runId).all<Record<string, string | number | null>>();
  const rows = (resultRows.results ?? []).map((row) => ({
    promptVersionId: String(row.promptVersionId ?? ""), predictedSegment: String(row.predictedSegment ?? ""),
    predictedImagePriceCents: row.predictedImagePriceCents === null ? null : Number(row.predictedImagePriceCents),
    snapshot: json<ValidationSnapshot>(String(row.sampleSnapshotJson ?? "{}"), { id: "", skuCode: "", productName: "", brand: "", imageUrl: "", goldSegment: "", goldImagePriceCents: null }),
  }));
  const metricsFor = (promptId: string | null) => promptId ? validationMetrics(rows.filter((row) => row.promptVersionId === promptId).map((row) => ({
    goldSegment: row.snapshot.goldSegment, predictedSegment: String(row.predictedSegment ?? ""), goldImagePriceCents: row.snapshot.goldImagePriceCents, predictedImagePriceCents: row.predictedImagePriceCents === null ? null : Number(row.predictedImagePriceCents),
  }))) : null;
  const candidate = metricsFor(run.candidatePromptId)!;
  const baseline = metricsFor(run.baselinePromptId);
  const gate = activationGate(baseline, candidate, 50);
  await db.batch([
    db.prepare("UPDATE market_annotation_validation_runs SET status='completed', metrics_json=?, gate_json=?, completed_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','running')").bind(JSON.stringify({ candidate, baseline }), JSON.stringify(gate), runId),
    db.prepare("UPDATE market_annotation_prompt_versions SET metrics_json=? WHERE id=?").bind(JSON.stringify({ runId, candidate, baseline, gate }), run.candidatePromptId),
  ]);
  return { runId, metrics: { candidate, baseline }, gate };
}

export async function activatePromptVersion(db: MarketDatabase, input: { promptId: string; explicitOverride?: boolean; reason?: string; rollback?: boolean }, actor: Actor) {
  await ensureAnnotationSchema(db);
  await ensureMarketSchemaLazy(db);
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(input.promptId).first<PromptRow>();
  if (!prompt) throw new Error("Prompt 版本不存在");
  await assertPromptTaxonomyCurrent(db, prompt.category, json<string[]>(prompt.segments_json, []), "该 Prompt 的细分品类枚举已过期，不能激活或回滚");
  if (input.rollback && prompt.status !== "archived") throw new Error("只能回滚到曾经激活后归档的历史版本");
  if (!input.rollback && !["draft", "archived"].includes(prompt.status)) throw new Error("该 Prompt 当前不能激活");
  const run = await db.prepare("SELECT status, gate_json FROM market_annotation_validation_runs WHERE candidate_prompt_id=? ORDER BY created_at DESC LIMIT 1").bind(prompt.id).first<{ status: string; gate_json: string }>();
  const gate = run ? json<{ passed?: boolean; reasons?: string[] }>(run.gate_json, {}) : {};
  if ((!run || run.status !== "completed" || !gate.passed) && !input.explicitOverride) throw new Error("该 Prompt 尚未通过冻结样本门禁；管理员可填写原因后显式确认");
  if ((input.explicitOverride || input.rollback) && (input.reason?.trim().length ?? 0) < 6) throw new Error("显式确认或回滚必须填写至少 6 个字符的审计原因");
  const activationGuardId = "market-prompt-activation-guard-" + randomUUID();
  await db.batch([
    db.prepare(`INSERT INTO market_master_audit_logs
      (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
      SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM market_annotation_prompt_versions WHERE id=? AND status=?) THEN NULL
      WHEN NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy
          WHERE taxonomy.category=? AND taxonomy.status='active') OR (
        NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy taxonomy WHERE taxonomy.category=? AND taxonomy.status='active'
          AND NOT EXISTS (SELECT 1 FROM json_each(?) segment WHERE CAST(segment.value AS TEXT)=taxonomy.subcategory))
        AND NOT EXISTS (SELECT 1 FROM json_each(?) segment WHERE NOT EXISTS (
          SELECT 1 FROM market_subcategory_taxonomy taxonomy WHERE taxonomy.category=? AND taxonomy.status='active'
            AND taxonomy.subcategory=CAST(segment.value AS TEXT)))
      ) THEN ? ELSE NULL END, ?, ?, 'market_prompt_activation_guard', 'market_annotation_prompt', ?, '{}', '{}'`)
      .bind(prompt.id, prompt.status, prompt.category, prompt.category, prompt.segments_json, prompt.segments_json, prompt.category,
        activationGuardId, actor.email, actor.role, prompt.id),
    db.prepare("UPDATE market_annotation_prompt_versions SET status='archived' WHERE category=? AND status='active' AND id<>?").bind(prompt.category, prompt.id),
    db.prepare("UPDATE market_annotation_prompt_versions SET status='active', activated_by=?, activated_at=CURRENT_TIMESTAMP, change_note=CASE WHEN ?<>'' THEN change_note || ' | 激活说明：' || ? ELSE change_note END WHERE id=?").bind(actor.email, input.reason?.trim() ?? "", input.reason?.trim() ?? "", prompt.id),
    db.prepare("INSERT INTO market_annotation_prompt_audits (id, prompt_id, category, action, reason, actor) VALUES (?, ?, ?, ?, ?, ?)").bind("market-prompt-audit-" + randomUUID(), prompt.id, prompt.category, input.rollback ? "rollback" : input.explicitOverride ? "activate_override" : "activate", input.reason?.trim() || "validation_gate_passed", actor.email),
    db.prepare("DELETE FROM market_master_audit_logs WHERE id=? AND action='market_prompt_activation_guard'").bind(activationGuardId),
  ]);
  const activated = await db.prepare("SELECT status FROM market_annotation_prompt_versions WHERE id=?").bind(prompt.id).first<{ status: string }>();
  if (activated?.status !== "active") throw new Error("Prompt 激活写入未生效，请刷新后重试");
  return { ok: true, promptId: prompt.id, category: prompt.category, gate, explicitOverride: Boolean(input.explicitOverride), rollback: Boolean(input.rollback) };
}

export async function deletePromptVersion(db: MarketDatabase, promptIdValue: string, actor: Actor) {
  const promptId = promptIdValue.trim();
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(promptId).first<PromptRow>();
  if (!prompt) throw new Error("Prompt 版本不存在");
  if (prompt.status !== "draft") throw new Error("只能删除尚未激活的草稿版本；激活或归档版本必须保留审计记录");
  const references = await db.prepare(`SELECT
      (SELECT COUNT(*) FROM market_annotation_jobs WHERE prompt_version_id=?) job_count,
      (SELECT COUNT(*) FROM market_annotation_validation_runs WHERE candidate_prompt_id=? OR baseline_prompt_id=?) validation_count,
      (SELECT COUNT(*) FROM market_annotation_validation_results WHERE prompt_version_id=?) validation_result_count,
      (SELECT COUNT(*) FROM market_sku_annotations WHERE prompt_version_id=?) annotation_count`)
    .bind(promptId, promptId, promptId, promptId, promptId)
    .first<{ job_count: number; validation_count: number; validation_result_count: number; annotation_count: number }>();
  const referenceCount = Number(references?.job_count ?? 0) + Number(references?.validation_count ?? 0)
    + Number(references?.validation_result_count ?? 0) + Number(references?.annotation_count ?? 0);
  if (referenceCount > 0) throw new Error("该草稿已被任务、冻结验证或正式标注引用，不能删除");
  const reason = "管理员删除未使用的 Prompt 草稿";
  await db.batch([
    db.prepare("UPDATE market_annotation_prompt_versions SET status='deleted', change_note=CASE WHEN change_note='' THEN ? ELSE change_note || ' | ' || ? END WHERE id=? AND status='draft'").bind(reason, reason, promptId),
    db.prepare("INSERT INTO market_annotation_prompt_audits (id, prompt_id, category, action, reason, actor) VALUES (?, ?, ?, 'delete_draft', ?, ?)").bind("market-prompt-audit-" + randomUUID(), promptId, prompt.category, reason, actor.email),
  ]);
  const deleted = await db.prepare("SELECT status FROM market_annotation_prompt_versions WHERE id=?").bind(promptId).first<{ status: string }>();
  if (deleted?.status !== "deleted") throw new Error("Prompt 草稿删除未生效，请刷新后重试");
  return { ok: true, promptId, category: prompt.category, version: prompt.version };
}

export async function deleteSettledAnnotationJob(db: MarketDatabase, jobIdValue: string, actor: Actor) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const jobId = jobIdValue.trim();
  if (!/^[A-Za-z0-9:_-]{12,160}$/.test(jobId)) throw new Error("标注任务 ID 无效");
  await db.prepare("UPDATE market_annotation_jobs SET status=CASE WHEN status='committing' THEN 'review_ready' ELSE status END, commit_token_hash='', commit_started_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND commit_token_hash<>'' AND datetime(commit_started_at)<=datetime('now','-5 minutes')")
    .bind(jobId).run();
  const tokenHash = digest(randomBytes(24).toString("hex"));
  const acquired = await db.prepare("UPDATE market_annotation_jobs SET commit_token_hash=?, commit_started_at=datetime('now'), updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('review_ready','committed') AND commit_token_hash='' ")
    .bind(tokenHash, jobId).run();
  if (!Number(acquired.meta.changes ?? 0)) {
    const current = await db.prepare("SELECT status FROM market_annotation_jobs WHERE id=? LIMIT 1").bind(jobId).first<{ status: string }>();
    if (!current) throw new Error("标注任务不存在");
    if (!["review_ready", "committed"].includes(current.status)) throw new Error("只能归档推理已结束或已经全部入库的任务记录；运行中的任务请等待完成");
    throw new Error("任务正在复核或入库，请稍后重试");
  }
  try {
    const job = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs WHERE id=? AND commit_token_hash=? LIMIT 1`).bind(jobId, tokenHash).first<JobRow>();
    if (!job) throw new Error("任务状态已变化，归档未生效，请刷新后重试");
    const facts = await db.prepare(`SELECT COUNT(*) itemCount,
        SUM(CASE WHEN status='committed' THEN 1 ELSE 0 END) committedCount,
        SUM(CASE WHEN status IN ('review_pending','approved','rejected') THEN 1 ELSE 0 END) pendingReviewCount,
        SUM(CASE WHEN status='failed' AND attempt_count>=3 THEN 1 ELSE 0 END) cappedFailedCount,
        SUM(CASE WHEN status IN ('queued','claimed','inferencing') OR (status='failed' AND attempt_count<3) THEN 1 ELSE 0 END) retryableCount,
        SUM(CASE WHEN status='superseded' THEN 1 ELSE 0 END) supersededCount,
        (SELECT COUNT(*) FROM market_annotation_commit_receipts WHERE job_item_id IN
          (SELECT id FROM market_annotation_items WHERE job_id=?)) receiptCount
      FROM market_annotation_items WHERE job_id=?`).bind(jobId, jobId)
      .first<{ itemCount: number; committedCount: number | null; pendingReviewCount: number | null; cappedFailedCount: number | null; retryableCount: number | null; supersededCount: number | null; receiptCount: number }>();
    const itemCount = Number(facts?.itemCount ?? 0);
    const committedCount = Number(facts?.committedCount ?? 0);
    const pendingReviewCount = Number(facts?.pendingReviewCount ?? 0);
    const cappedFailedCount = Number(facts?.cappedFailedCount ?? 0);
    const retryableCount = Number(facts?.retryableCount ?? 0);
    const supersededCount = Number(facts?.supersededCount ?? 0);
    if (!itemCount) throw new Error("任务没有可归档的明细");
    if (retryableCount > 0) throw new Error(`任务仍有 ${retryableCount} 条可继续识别，禁止归档`);
    if (committedCount + pendingReviewCount + cappedFailedCount + supersededCount !== itemCount) throw new Error("任务含有无法安全归档的明细状态，请刷新后检查");
    if (job.status === "committed" && committedCount + supersededCount !== itemCount) throw new Error("已入库任务的明细状态不完整，禁止删除任务记录");
    const before = jobValue(job);
    const after = {
      status: "deleted",
      previousStatus: job.status,
      preservedItems: itemCount,
      preservedReceipts: Number(facts?.receiptCount ?? 0),
      preservedCommittedItems: committedCount,
      archivedPendingItems: pendingReviewCount,
      cappedFailedItems: cappedFailedCount,
      formalAnnotationsPreserved: true,
    };
    const auditAction = job.status === "committed" ? "delete_committed_market_annotation_job" : "archive_review_ready_market_annotation_job";
    await db.batch([
      db.prepare(`UPDATE market_annotation_jobs SET status='deleted', commit_token_hash='', commit_started_at=NULL,
          updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND commit_token_hash=? AND status IN ('review_ready','committed')
          AND EXISTS (SELECT 1 FROM market_annotation_items WHERE job_id=?)
          AND NOT EXISTS (SELECT 1 FROM market_annotation_items WHERE job_id=?
            AND (status IN ('queued','claimed','inferencing') OR (status='failed' AND attempt_count<3)))`)
        .bind(jobId, tokenHash, jobId, jobId),
      db.prepare(`UPDATE market_annotation_items SET status='superseded',selected=0,
          lease_token_hash='',lease_agent_id='',lease_expires_at=NULL,version=version+1,updated_at=CURRENT_TIMESTAMP
        WHERE job_id=? AND status IN ('review_pending','approved','rejected')
          AND EXISTS (SELECT 1 FROM market_annotation_jobs WHERE id=? AND status='deleted')`).bind(jobId, jobId),
      db.prepare(`UPDATE market_annotation_cloud_runs SET state='completed', lease_token_hash='', lease_expires_at=NULL,
          next_run_at=NULL, completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP
        WHERE job_id=? AND EXISTS (SELECT 1 FROM market_annotation_jobs WHERE id=? AND status='deleted')`).bind(jobId, jobId),
      db.prepare(`INSERT INTO market_master_audit_logs
          (id,actor_email,actor_role,action,entity_type,entity_id,before_json,after_json)
        SELECT ?,?,?, ?, 'market_annotation_job', ?, ?, ?
        WHERE EXISTS (SELECT 1 FROM market_annotation_jobs WHERE id=? AND status='deleted')`)
        .bind(`market-audit-${randomUUID()}`, actor.email, actor.role, auditAction, jobId, JSON.stringify(before), JSON.stringify(after), jobId),
    ]);
    const deleted = await db.prepare("SELECT status FROM market_annotation_jobs WHERE id=? LIMIT 1").bind(jobId).first<{ status: string }>();
    if (deleted?.status !== "deleted") throw new Error("任务状态已变化，归档未生效，请刷新后重试");
    return { ok: true, jobId, ...after };
  } catch (error) {
    await releaseJobMutex(db, jobId, tokenHash, false).catch(() => undefined);
    throw error;
  }
}

export async function createLocalAgent(db: MarketDatabase, nameValue: string, actor: Actor) {
  const name = nameValue.trim().slice(0, 120);
  if (name.length < 2) throw new Error("本地 agent 名称至少需要 2 个字符");
  const id = "market-agent-" + randomUUID();
  const token = "teruisi_ma_" + randomBytes(32).toString("hex");
  await db.prepare("INSERT INTO market_annotation_local_agents (id, name, token_hash, status, capabilities_json, created_by) VALUES (?, ?, ?, 'enabled', ?, ?)")
    .bind(id, name, digest(token), JSON.stringify({ scope: "market_annotation_worker", protocols: ["ollama"] }), actor.email).run();
  return { id, name, token, status: "enabled", note: "token 只在本次响应显示，请立即复制到本机环境变量" };
}

export async function revokeLocalAgent(db: MarketDatabase, agentId: string) {
  const result = await db.prepare("UPDATE market_annotation_local_agents SET status='revoked', revoked_at=CURRENT_TIMESTAMP WHERE id=? AND status='enabled'").bind(agentId.trim()).run();
  return { ok: true, revoked: Number(result.meta.changes ?? 0) > 0 };
}

export async function authenticateLocalAgent(db: MarketDatabase, authorization: string | null) {
  const match = authorization?.match(/^Bearer\s+(.+)$/i);
  if (!match?.[1]) throw new AnnotationAgentError("authentication");
  const tokenHash = digest(match[1]);
  const agent = await db.prepare("SELECT id, name, status FROM market_annotation_local_agents WHERE token_hash=? AND status='enabled' LIMIT 1").bind(tokenHash).first<{ id: string; name: string; status: string }>();
  if (!agent) throw new AnnotationAgentError("authentication");
  await db.prepare("UPDATE market_annotation_local_agents SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?").bind(agent.id).run();
  return agent;
}

export async function claimLocalAnnotation(db: MarketDatabase, agent: { id: string }) {
  await ensureAnnotationSchema(db);
  const localDefault = defaultMarketAnnotationConcurrency("local");
  await db.prepare("UPDATE market_annotation_items SET status=CASE WHEN attempt_count>=3 THEN 'failed' ELSE 'queued' END, lease_token_hash='', lease_agent_id='', lease_expires_at=NULL, error_message=CASE WHEN attempt_count>=3 THEN '本地执行租约连续超时，已达到最大尝试次数' ELSE error_message END, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE status='claimed' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at)<=datetime('now')").run();
  const worker = await db.prepare(`SELECT MAX(COALESCE(setting.concurrency, ?)) concurrency
      FROM market_annotation_jobs job
      JOIN market_annotation_items item ON item.job_id=job.id
      LEFT JOIN market_annotation_concurrency_settings setting ON setting.category=job.category AND setting.executor='local'
      WHERE job.executor='local' AND job.status IN ('queued','running','failed')
        AND item.status IN ('queued','failed','claimed') AND item.attempt_count<3`)
    .bind(localDefault).first<{ concurrency: number | null }>();
  const workerConcurrency = normalizeMarketAnnotationConcurrency(worker?.concurrency ?? undefined, "local");
  const reuseJob = await db.prepare(`SELECT ${jobColumns} FROM market_annotation_jobs
      WHERE executor='local' AND status IN ('queued','running','failed','review_ready') AND reuse_status<>'ready'
      ORDER BY datetime(updated_at), id LIMIT 1`).first<JobRow>();
  if (reuseJob) {
    const preparation = await prepareAnnotationReuse(db, reuseJob);
    if (preparation.reusedCount) await refreshJob(db, reuseJob.id);
    if (preparation.reusedCount || preparation.waiting || !preparation.ready) {
      return { task: null, workerConcurrency, preparing: true, reusedCount: preparation.reusedCount };
    }
  }
  const item = await db.prepare(`SELECT i.id, i.job_id jobId, i.category, i.sku_code skuCode,
      i.ranking_dimension rankingDimension, i.month, i.image_content_sha256 imageContentSha256,
      i.product_name productName, i.brand, i.source_image_url sourceImageUrl,
      i.reviewed_segment reviewedSegment, i.reviewed_by reviewedBy, i.version,
      j.prompt_version_id promptVersionId, j.local_model_name localModelName,
      COALESCE(setting.concurrency, ?) inferenceConcurrency
    FROM market_annotation_items i
    JOIN market_annotation_jobs j ON j.id=i.job_id
    LEFT JOIN market_annotation_concurrency_settings setting ON setting.category=j.category AND setting.executor='local'
    WHERE j.executor='local' AND j.status IN ('queued','running','failed')
      AND i.status IN ('queued','failed') AND i.attempt_count<3
      AND ${inferenceUnitLeaderClause("i")}
      AND NOT EXISTS (SELECT 1 FROM market_annotation_items active_unit
        WHERE active_unit.job_id=i.job_id AND ${inferenceUnitMatch("active_unit", "i")}
          AND active_unit.status='claimed' AND active_unit.lease_expires_at IS NOT NULL
          AND datetime(active_unit.lease_expires_at)>datetime('now'))
      AND (SELECT COUNT(*) FROM market_annotation_items active
        WHERE active.job_id=i.job_id AND active.status='claimed'
          AND active.lease_expires_at IS NOT NULL AND datetime(active.lease_expires_at)>datetime('now')) < COALESCE(setting.concurrency, ?)
    ORDER BY j.created_at, i.updated_at LIMIT 1`)
    .bind(localDefault, localDefault)
    .first<{ id: string; jobId: string; category: string; skuCode: string; rankingDimension: string; month: string; imageContentSha256: string; productName: string; brand: string; sourceImageUrl: string; reviewedSegment: string; reviewedBy: string; version: number; promptVersionId: string; localModelName: string; inferenceConcurrency: number }>();
  if (!item) return { task: null, workerConcurrency };
  const inferenceConcurrency = normalizeMarketAnnotationConcurrency(item.inferenceConcurrency, "local");
  const leaseToken = randomBytes(24).toString("hex");
  const claimed = await db.prepare(`UPDATE market_annotation_items SET status='claimed', lease_token_hash=?, lease_agent_id=?, lease_expires_at=datetime('now','+5 minutes'), attempt_count=attempt_count+1, error_message='', version=version+1, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND version=? AND status IN ('queued','failed') AND attempt_count<3
      AND (SELECT COUNT(*) FROM market_annotation_items active
        WHERE active.job_id=? AND active.status='claimed'
          AND active.lease_expires_at IS NOT NULL AND datetime(active.lease_expires_at)>datetime('now')) < ?`)
    .bind(digest(leaseToken), agent.id, item.id, item.version, item.jobId, inferenceConcurrency).run();
  if (!Number(claimed.meta.changes ?? 0)) return { task: null, raced: true, workerConcurrency };
  const prompt = await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(item.promptVersionId).first<PromptRow>();
  if (!prompt) throw new Error("本地任务绑定的 Prompt 不存在");
  const lease = await db.prepare("SELECT lease_expires_at leaseExpiresAt FROM market_annotation_items WHERE id=?").bind(item.id).first<{ leaseExpiresAt: string }>();
  await db.prepare("UPDATE market_annotation_jobs SET status='running', started_at=COALESCE(started_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP WHERE id=?").bind(item.jobId).run();
  const promptSegments = json<string[]>(prompt.segments_json, []);
  const fixedSegment = item.reviewedBy === HISTORY_SAME_SKU_SEGMENT_REVIEWER && promptSegments.includes(item.reviewedSegment) ? item.reviewedSegment : "";
  return { workerConcurrency, task: { itemId: item.id, candidateId: item.id, jobId: item.jobId, category: item.category, skuCode: item.skuCode, rankingDimension: item.rankingDimension, month: item.month, imageContentSha256: item.imageContentSha256, productName: item.productName, brand: item.brand, sourceImageUrl: item.sourceImageUrl, imageCandidates: resolveAnnotationImageCandidates(item.sourceImageUrl), promptVersionId: prompt.id, promptBody: fixedSegment ? priceOnlyAnnotationPrompt(fixedSegment) : prompt.prompt_body, segments: fixedSegment ? [fixedSegment] : promptSegments, recognitionMode: fixedSegment ? "price_only" : "full", fixedSegment: fixedSegment || null, localModelName: item.localModelName, inferenceConcurrency, leaseToken, leaseExpiresAt: lease?.leaseExpiresAt ?? "" } };
}

export async function completeLocalAnnotation(db: MarketDatabase, agent: { id: string }, input: {
  itemId: string; leaseToken: string; result?: unknown; error?: string; imageSource?: string; resolvedImageUrl?: string;
}) {
  await Promise.all([ensureMarketSchemaLazy(db), ensureAnnotationSchema(db)]);
  const item = await db.prepare("SELECT " + itemColumns + " FROM market_annotation_items WHERE id=?").bind(input.itemId).first<ItemRow>();
  if (!item) throw new AnnotationAgentError("bad_request");
  if (item.status === "review_pending" || item.status === "approved" || item.status === "committed") return { ok: true, duplicate: true, itemId: item.id };
  if (item.status !== "claimed" || item.lease_agent_id !== agent.id || item.lease_token_hash !== digest(input.leaseToken) || !item.lease_expires_at) {
    throw new AnnotationAgentError("lease_conflict");
  }
  if (input.error) {
    const failed = await db.prepare("UPDATE market_annotation_items SET status='failed', error_message=?, lease_token_hash='', lease_agent_id='', lease_expires_at=NULL, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='claimed' AND lease_agent_id=? AND lease_token_hash=? AND datetime(lease_expires_at)>datetime('now')")
      .bind(input.error.slice(0, 800), item.id, agent.id, digest(input.leaseToken)).run();
    if (!Number(failed.meta.changes ?? 0)) throw new AnnotationAgentError("lease_conflict");
    if (item.attempt_count >= 3) await fanOutInferenceUnitTerminalFailure(db, item.job_id, item, input.error.slice(0, 800));
    await refreshJob(db, item.job_id);
    return { ok: true, failed: true };
  }
  const job = await db.prepare("SELECT " + jobColumns + " FROM market_annotation_jobs WHERE id=?").bind(item.job_id).first<JobRow>();
  const prompt = job ? await db.prepare("SELECT " + promptColumns + " FROM market_annotation_prompt_versions WHERE id=?").bind(job.prompt_version_id).first<PromptRow>() : null;
  if (!prompt) throw new Error("本地任务 Prompt 不存在");
  let prediction: ReturnType<typeof parseVisionAnnotation>;
  const promptSegments = json<string[]>(prompt.segments_json, []);
  const fixedSegment = item.reviewed_by === HISTORY_SAME_SKU_SEGMENT_REVIEWER && promptSegments.includes(item.reviewed_segment) ? item.reviewed_segment : "";
  try { prediction = parseVisionAnnotation(input.result, fixedSegment ? [fixedSegment] : promptSegments); }
  catch { throw new AnnotationAgentError("bad_request"); }
  const candidates = resolveAnnotationImageCandidates(item.source_image_url);
  const selectedImage = input.resolvedImageUrl ? candidates.find((candidate) => candidate.url === input.resolvedImageUrl && candidate.source === input.imageSource) : null;
  if (input.resolvedImageUrl && !selectedImage) throw new AnnotationAgentError("bad_request");
  const rawDigest = digest(prediction.rawText);
  const resolvedImageUrl = selectedImage?.url ?? "";
  const imageSource = selectedImage?.source ?? "none";
  const annotationResult = {
    segment: prediction.segment,
    imagePriceCents: prediction.imagePriceCents,
    priceType: prediction.priceType,
    priceLowCents: prediction.priceLowCents,
    priceHighCents: prediction.priceHighCents,
    confidenceBps: prediction.confidenceBps,
    reason: prediction.reason,
    rawDigest,
    resolvedImageUrl,
    imageSource,
  };
  const result = await db.batch([
    db.prepare("UPDATE market_annotation_items SET status='review_pending', ai_segment=?, ai_image_price_cents=?, ai_price_type=?, ai_price_low_cents=?, ai_price_high_cents=?, ai_confidence_bps=?, ai_reason=?, ai_raw_digest=?, reviewed_segment=?, reviewed_image_price_cents=?, reviewed_price_type=?, reviewed_price_low_cents=?, reviewed_price_high_cents=?, resolved_image_url=?, image_source=?, lease_token_hash='', lease_agent_id='', lease_expires_at=NULL, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='claimed' AND lease_agent_id=? AND lease_token_hash=? AND datetime(lease_expires_at)>datetime('now')")
      .bind(prediction.segment, prediction.imagePriceCents, prediction.priceType, prediction.priceLowCents, prediction.priceHighCents, prediction.confidenceBps, prediction.reason, rawDigest, prediction.segment, prediction.imagePriceCents, prediction.priceType, prediction.priceLowCents, prediction.priceHighCents, resolvedImageUrl, imageSource, item.id, agent.id, digest(input.leaseToken)),
    db.prepare(`UPDATE market_price_snapshots SET
        ai_image_price_cents=?, ai_price_type=?, ai_confidence_bps=?, ai_reason=?,
        price_low_cents=COALESCE(?, price_low_cents), price_high_cents=COALESCE(?, price_high_cents),
        confirmation_status='ai_pending', source_job_item_id=?, prompt_version_id=?, updated_at=CURRENT_TIMESTAMP
      WHERE category=? AND scope=? AND sku_code=? AND ranking_dimension=? AND month=?
        AND image_content_sha256=? AND confirmed_market_price_cents IS NULL
        AND EXISTS (SELECT 1 FROM market_annotation_items completed
          WHERE completed.id=? AND completed.status='review_pending' AND completed.version=? AND completed.ai_raw_digest=?)`)
      .bind(prediction.imagePriceCents, prediction.priceType, prediction.confidenceBps, prediction.reason,
        prediction.priceLowCents, prediction.priceHighCents, item.id, job!.prompt_version_id,
        item.category, item.scope, item.sku_code, item.ranking_dimension, item.month,
        item.image_content_sha256, item.id, item.version + 1, rawDigest),
  ]) as Array<{ meta?: { changes?: number } }>;
  if (!Number(result[0]?.meta?.changes ?? 0)) throw new AnnotationAgentError("lease_conflict");
  let reusedCount: number;
  try {
    reusedCount = await fanOutInferenceUnitResult(db, job!, item, annotationResult);
  } catch (error) {
    await scheduleAnnotationReuseRepair(db, item.job_id);
    throw error;
  }
  await refreshJob(db, item.job_id);
  return { ok: true, itemId: item.id, reusedCount };
}
