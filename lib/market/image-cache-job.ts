import { ensureMarketSchemaCached, type MarketSchemaDatabase } from "@/lib/market/schema-core";

export type MarketImageCacheStats = {
  total: number;
  cached: number;
  failed: number;
  pending: number;
};

export type MarketImageCacheJobStatus = "queued" | "running" | "completed" | "failed";

export type MarketImageCacheJob = MarketImageCacheStats & {
  id: string;
  scopeKey: string;
  batchId: string;
  status: MarketImageCacheJobStatus;
  requestedBy: string;
  discoveryCursor: string;
  discoveryComplete: boolean;
  discoveredCount: number;
  processedCount: number;
  propagationPending: number;
  runCount: number;
  failureCount: number;
  leaseEpoch: number;
  leaseExpiresAt: string | null;
  nextRunAt: string | null;
  errorCode: string;
  errorMessage: string;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type MarketImageCacheJobLease = MarketImageCacheJob & {
  leaseToken: string;
};

export type MarketImageCacheJobItem = {
  sourceUrl: string;
  status: "queued" | "ready";
  contentHash: string;
  attemptCount: number;
  cacheStatus: string;
  cacheContentHash: string;
  cacheAttemptCount: number;
};

const MAX_DISCOVERY_ROWS = 64;
const MAX_WORK_ITEMS = 8;

const jobColumns = `id, scope_key scopeKey, batch_id batchId, status, requested_by requestedBy,
  discovery_cursor discoveryCursor, discovery_complete discoveryComplete,
  discovered_count discoveredCount, discovered_count total, completed_count cached,
  failed_count failed, pending_count pending, propagation_pending_count propagationPending,
  processed_count processedCount, run_count runCount, failure_count failureCount,
  lease_token leaseToken, lease_epoch leaseEpoch, lease_expires_at leaseExpiresAt,
  next_run_at nextRunAt, error_code errorCode, error_message errorMessage,
  started_at startedAt, completed_at completedAt, created_at createdAt, updated_at updatedAt`;

type NumericJobFields = "total" | "cached" | "failed" | "pending" | "propagationPending"
  | "processedCount" | "runCount" | "failureCount" | "leaseEpoch" | "discoveredCount";

type MarketImageCacheJobRow = Omit<MarketImageCacheJobLease, NumericJobFields | "discoveryComplete"> &
  Record<NumericJobFields | "discoveryComplete", number | string>;

function mapJob(row: MarketImageCacheJobRow): MarketImageCacheJobLease {
  return {
    ...row,
    total: Number(row.total ?? 0),
    cached: Number(row.cached ?? 0),
    failed: Number(row.failed ?? 0),
    pending: Number(row.pending ?? 0),
    propagationPending: Number(row.propagationPending ?? 0),
    processedCount: Number(row.processedCount ?? 0),
    runCount: Number(row.runCount ?? 0),
    failureCount: Number(row.failureCount ?? 0),
    leaseEpoch: Number(row.leaseEpoch ?? 0),
    discoveredCount: Number(row.discoveredCount ?? 0),
    discoveryComplete: Number(row.discoveryComplete ?? 0) === 1,
  };
}

function publicJob(job: MarketImageCacheJobLease): MarketImageCacheJob {
  const { leaseToken: _leaseToken, ...safe } = job;
  void _leaseToken;
  return safe;
}

function normalizeBatchId(batchId?: string) {
  const value = batchId?.trim() ?? "";
  if (value.length > 120) throw new Error("batchId 不能超过 120 个字符");
  return value;
}

function scopeKey(batchId: string) {
  return batchId ? `batch:${batchId}` : "global";
}

function changes(result: unknown) {
  return Number((result as { meta?: { changes?: number } })?.meta?.changes ?? 0);
}

async function readJobRow(db: MarketSchemaDatabase, input: { jobId?: string; batchId?: string }) {
  const jobId = input.jobId?.trim() ?? "";
  const normalizedBatchId = normalizeBatchId(input.batchId);
  if (!jobId && input.jobId !== undefined) return null;
  let row: MarketImageCacheJobRow | null;
  if (jobId && input.batchId !== undefined) {
    row = await db.prepare(`SELECT ${jobColumns} FROM market_image_cache_jobs
      WHERE id=? AND scope_key=? LIMIT 1`).bind(jobId, scopeKey(normalizedBatchId)).first<MarketImageCacheJobRow>();
  } else if (jobId) {
    row = await db.prepare(`SELECT ${jobColumns} FROM market_image_cache_jobs WHERE id=? LIMIT 1`)
      .bind(jobId).first<MarketImageCacheJobRow>();
  } else {
    row = await db.prepare(`SELECT ${jobColumns} FROM market_image_cache_jobs WHERE scope_key=? LIMIT 1`)
      .bind(scopeKey(normalizedBatchId)).first<MarketImageCacheJobRow>();
  }
  return row ? mapJob(row) : null;
}

export async function getMarketImageCacheJob(
  db: MarketSchemaDatabase,
  input: { jobId?: string; batchId?: string } = {},
): Promise<MarketImageCacheJob | null> {
  const row = await readJobRow(db, input);
  return row ? publicJob(row) : null;
}

/** O(1): creates or resumes a cursor-backed job without inspecting ranking/cache facts. */
export async function createOrResumeMarketImageCacheJob(
  db: MarketSchemaDatabase,
  input: { batchId?: string; requestedBy?: string } = {},
): Promise<MarketImageCacheJob> {
  await ensureMarketSchemaCached(db);
  const batchId = normalizeBatchId(input.batchId);
  const requestedBy = input.requestedBy?.trim().slice(0, 160) || "system";
  const id = `market-image-cache-${crypto.randomUUID()}`;
  await db.prepare(`INSERT INTO market_image_cache_jobs (
      id, scope_key, batch_id, status, requested_by, created_at, updated_at
    ) VALUES (?, ?, ?, 'queued', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT(scope_key) DO UPDATE SET
      requested_by=excluded.requested_by,
      status=CASE WHEN market_image_cache_jobs.status='failed'
          OR (market_image_cache_jobs.scope_key='global' AND market_image_cache_jobs.status='completed')
        THEN 'queued' ELSE market_image_cache_jobs.status END,
      discovery_cursor=CASE WHEN market_image_cache_jobs.scope_key='global'
          AND market_image_cache_jobs.status='completed' THEN ''
        ELSE market_image_cache_jobs.discovery_cursor END,
      discovery_complete=CASE WHEN market_image_cache_jobs.scope_key='global'
          AND market_image_cache_jobs.status='completed' THEN 0
        ELSE market_image_cache_jobs.discovery_complete END,
      lease_token=CASE WHEN market_image_cache_jobs.status='running'
        THEN market_image_cache_jobs.lease_token ELSE '' END,
      lease_expires_at=CASE WHEN market_image_cache_jobs.status='running'
        THEN market_image_cache_jobs.lease_expires_at ELSE NULL END,
      next_run_at=CASE WHEN market_image_cache_jobs.status='failed'
          OR (market_image_cache_jobs.scope_key='global' AND market_image_cache_jobs.status='completed')
        THEN NULL ELSE market_image_cache_jobs.next_run_at END,
      failure_count=CASE WHEN market_image_cache_jobs.status='failed'
          OR (market_image_cache_jobs.scope_key='global' AND market_image_cache_jobs.status='completed')
        THEN 0 ELSE market_image_cache_jobs.failure_count END,
      error_code=CASE WHEN market_image_cache_jobs.status='failed'
          OR (market_image_cache_jobs.scope_key='global' AND market_image_cache_jobs.status='completed')
        THEN '' ELSE market_image_cache_jobs.error_code END,
      error_message=CASE WHEN market_image_cache_jobs.status='failed'
          OR (market_image_cache_jobs.scope_key='global' AND market_image_cache_jobs.status='completed')
        THEN '' ELSE market_image_cache_jobs.error_message END,
      completed_at=CASE WHEN market_image_cache_jobs.status='failed'
          OR (market_image_cache_jobs.scope_key='global' AND market_image_cache_jobs.status='completed')
        THEN NULL ELSE market_image_cache_jobs.completed_at END,
      updated_at=CURRENT_TIMESTAMP`)
    .bind(id, scopeKey(batchId), batchId, requestedBy).run();
  const job = await readJobRow(db, { batchId });
  if (!job) throw new Error("图片缓存任务创建后无法回读");
  return publicJob(job);
}

export async function acquireMarketImageCacheJobLease(
  db: MarketSchemaDatabase,
  input: { jobId?: string } = {},
): Promise<MarketImageCacheJobLease | null> {
  await ensureMarketSchemaCached(db);
  const requestedJobId = input.jobId?.trim() ?? "";
  if (input.jobId !== undefined && !requestedJobId) return null;
  const token = crypto.randomUUID();
  const selector = requestedJobId
    ? "id=?"
    : `id=(SELECT id FROM market_image_cache_jobs candidate
        WHERE (candidate.discovery_complete=0 OR candidate.pending_count>0
          OR candidate.propagation_pending_count>0)
          AND (candidate.status='queued' OR (candidate.status='running'
            AND datetime(candidate.lease_expires_at)<=CURRENT_TIMESTAMP))
          AND (candidate.next_run_at IS NULL OR datetime(candidate.next_run_at)<=CURRENT_TIMESTAMP)
        ORDER BY CASE candidate.status WHEN 'running' THEN 0 ELSE 1 END,
          candidate.updated_at, candidate.id LIMIT 1)`;
  const row = await db.prepare(`UPDATE market_image_cache_jobs SET
      status='running', lease_token=?, lease_epoch=lease_epoch+1,
      lease_expires_at=datetime('now','+2 minutes'), next_run_at=NULL,
      run_count=run_count+1, started_at=COALESCE(started_at, CURRENT_TIMESTAMP),
      completed_at=NULL, error_code='', error_message='', updated_at=CURRENT_TIMESTAMP
    WHERE ${selector}
      AND (discovery_complete=0 OR pending_count>0 OR propagation_pending_count>0)
      AND (status='queued' OR (status='running' AND datetime(lease_expires_at)<=CURRENT_TIMESTAMP))
      AND (lease_token='' OR lease_expires_at IS NULL OR datetime(lease_expires_at)<=CURRENT_TIMESTAMP)
      AND NOT EXISTS (
        SELECT 1 FROM market_image_cache_jobs blocker
        WHERE blocker.id<>market_image_cache_jobs.id AND blocker.status='running'
          AND blocker.lease_token<>'' AND datetime(blocker.lease_expires_at)>CURRENT_TIMESTAMP
      )
    RETURNING ${jobColumns}`)
    .bind(...(requestedJobId ? [token, requestedJobId] : [token]))
    .first<MarketImageCacheJobRow>();
  return row ? mapJob(row) : null;
}

export async function heartbeatMarketImageCacheJobLease(db: MarketSchemaDatabase, lease: MarketImageCacheJobLease) {
  const result = await db.prepare(`UPDATE market_image_cache_jobs SET lease_expires_at=datetime('now','+2 minutes'),
      updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status='running' AND lease_token=? AND lease_epoch=?
      AND datetime(lease_expires_at)>CURRENT_TIMESTAMP`)
    .bind(lease.id, lease.leaseToken, lease.leaseEpoch).run();
  return changes(result) === 1;
}

/**
 * Revokes a timed-out runner immediately while keeping one live global blocker.
 * Late promises lose their D1 fence, and another image batch cannot start until
 * the independent quarantine window expires.
 */
export async function quarantineTimedOutMarketImageCacheJobLease(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
) {
  const quarantineToken = crypto.randomUUID();
  const result = await db.prepare(`UPDATE market_image_cache_jobs SET
      status='running', lease_token=?, lease_epoch=lease_epoch+1,
      lease_expires_at=datetime('now','+2 minutes'),
      next_run_at=datetime('now','+2 minutes'), failure_count=failure_count+1,
      error_code='cache_batch_timeout',
      error_message='图片缓存外部阶段超时；旧执行器已撤权并进入隔离窗口',
      updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status='running' AND lease_token=? AND lease_epoch=?
      AND datetime(lease_expires_at)>CURRENT_TIMESTAMP`)
    .bind(quarantineToken, lease.id, lease.leaseToken, lease.leaseEpoch).run();
  return changes(result) === 1;
}

export async function terminateTimedOutMarketImageCacheJobLease(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
) {
  const result = await db.prepare(`UPDATE market_image_cache_jobs SET
      status='failed', failure_count=MAX(failure_count, 3),
      lease_token='', lease_expires_at=NULL, next_run_at=NULL,
      error_code='cache_batch_timeout',
      error_message='图片缓存外部阶段连续超时，任务已停止；请检查图片源或 R2 状态后重试',
      completed_at=NULL, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status='running' AND lease_token=? AND lease_epoch=?
      AND datetime(lease_expires_at)>CURRENT_TIMESTAMP`)
    .bind(lease.id, lease.leaseToken, lease.leaseEpoch).run();
  return changes(result) === 1;
}

export async function discoverMarketImageCacheJobItems(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
  requestedLimit = MAX_DISCOVERY_ROWS,
) {
  const limit = Math.max(1, Math.min(MAX_DISCOVERY_ROWS, Math.trunc(requestedLimit)));
  const rows = lease.batchId
    ? await db.prepare(`SELECT image_url sourceUrl
        FROM market_ranking_entries INDEXED BY market_entries_batch_image_idx
        WHERE last_import_batch_id=? AND image_url<>'' AND image_url>?
        ORDER BY image_url LIMIT ?`).bind(lease.batchId, lease.discoveryCursor, limit).all<{ sourceUrl: string }>()
    : await db.prepare(`SELECT image_url sourceUrl
        FROM market_ranking_entries INDEXED BY market_entries_image_url_idx
        WHERE image_url<>'' AND image_url>?
        ORDER BY image_url LIMIT ?`).bind(lease.discoveryCursor, limit).all<{ sourceUrl: string }>();
  const sourceUrls = (rows.results ?? []).map((row) => row.sourceUrl).filter(Boolean);
  const nextCursor = sourceUrls.at(-1) ?? lease.discoveryCursor;
  const discoveryComplete = sourceUrls.length < limit;
  const writes = await db.batch([
    db.prepare(`WITH discovered AS (
        SELECT CAST(value AS TEXT) source_url FROM json_each(?)
      )
      INSERT INTO market_image_cache_job_items (job_id, source_url, status, created_at, updated_at)
      SELECT job.id, discovered.source_url, 'queued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM market_image_cache_jobs job CROSS JOIN discovered
      WHERE job.id=? AND job.status='running' AND job.lease_token=? AND job.lease_epoch=?
        AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP AND discovered.source_url<>''
      ON CONFLICT(job_id, source_url) DO NOTHING`)
      .bind(JSON.stringify(sourceUrls), lease.id, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE market_image_cache_jobs SET discovery_cursor=?, discovery_complete=?,
        updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND status='running' AND lease_token=? AND lease_epoch=?
        AND datetime(lease_expires_at)>CURRENT_TIMESTAMP`)
      .bind(nextCursor, discoveryComplete ? 1 : 0, lease.id, lease.leaseToken, lease.leaseEpoch),
  ]) as Array<unknown>;
  return {
    discovered: changes(writes[0]),
    scanned: sourceUrls.length,
    discoveryComplete,
    cursor: nextCursor,
    lostLease: changes(writes[1]) !== 1,
  };
}

export async function listMarketImageCacheJobItems(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
  requestedLimit = MAX_WORK_ITEMS,
): Promise<MarketImageCacheJobItem[]> {
  const limit = Math.max(1, Math.min(MAX_WORK_ITEMS, Math.trunc(requestedLimit)));
  const rows = await db.prepare(`SELECT item.source_url sourceUrl, item.status,
      item.content_sha256 contentHash, item.attempt_count attemptCount,
      COALESCE(cache.status, '') cacheStatus,
      COALESCE(cache.content_sha256, '') cacheContentHash,
      COALESCE(cache.attempt_count, 0) cacheAttemptCount
    FROM market_image_cache_job_items item
    LEFT JOIN market_image_cache cache ON cache.source_url=item.source_url
    WHERE item.job_id=? AND item.status IN ('queued','ready')
      AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
        WHERE job.id=item.job_id AND job.status='running' AND job.lease_token=?
          AND job.lease_epoch=? AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP)
    ORDER BY CASE item.status WHEN 'ready' THEN 0 ELSE 1 END, item.source_url
    LIMIT ?`).bind(lease.id, lease.leaseToken, lease.leaseEpoch, limit).all<{
      sourceUrl: string;
      status: "queued" | "ready";
      contentHash: string;
      attemptCount: number | string;
      cacheStatus: string;
      cacheContentHash: string;
      cacheAttemptCount: number | string;
    }>();
  return (rows.results ?? []).map((row) => ({
    ...row,
    attemptCount: Number(row.attemptCount ?? 0),
    cacheAttemptCount: Number(row.cacheAttemptCount ?? 0),
  }));
}

export async function markMarketImageCacheJobItemReady(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
  sourceUrl: string,
  contentHash: string,
) {
  if (!/^[a-f0-9]{64}$/.test(contentHash)) return false;
  const result = await db.prepare(`UPDATE market_image_cache_job_items SET status='ready',
      content_sha256=?, error_code='', error_message='', updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND source_url=? AND status='queued'
      AND EXISTS (SELECT 1 FROM market_image_cache cache
        WHERE cache.source_url=market_image_cache_job_items.source_url
          AND cache.status='ready' AND cache.content_sha256=?)
      AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
        WHERE job.id=market_image_cache_job_items.job_id AND job.status='running'
          AND job.lease_token=? AND job.lease_epoch=?
          AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP)`)
    .bind(contentHash, lease.id, sourceUrl, contentHash, lease.leaseToken, lease.leaseEpoch).run();
  return changes(result) === 1;
}

export async function markMarketImageCacheJobItemTerminalFailure(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
  sourceUrl: string,
) {
  const result = await db.prepare(`UPDATE market_image_cache_job_items SET status='failed',
      attempt_count=(SELECT cache.attempt_count FROM market_image_cache cache
        WHERE cache.source_url=market_image_cache_job_items.source_url),
      error_code=COALESCE((SELECT cache.error_code FROM market_image_cache cache
        WHERE cache.source_url=market_image_cache_job_items.source_url), 'cache_failed'),
      error_message=COALESCE((SELECT cache.error_message FROM market_image_cache cache
        WHERE cache.source_url=market_image_cache_job_items.source_url), '图片缓存重试已封顶'),
      completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE job_id=? AND source_url=? AND status='queued'
      AND EXISTS (SELECT 1 FROM market_image_cache cache
        WHERE cache.source_url=market_image_cache_job_items.source_url
          AND cache.status='failed' AND cache.attempt_count>=3)
      AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
        WHERE job.id=market_image_cache_job_items.job_id AND job.status='running'
          AND job.lease_token=? AND job.lease_epoch=?
          AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP)`)
    .bind(lease.id, sourceUrl, lease.leaseToken, lease.leaseEpoch).run();
  return changes(result) === 1;
}

export async function finishMarketImageCacheJobLease(
  db: MarketSchemaDatabase,
  lease: MarketImageCacheJobLease,
): Promise<MarketImageCacheJob | null> {
  const row = await db.prepare(`UPDATE market_image_cache_jobs SET
      status=CASE WHEN discovery_complete=1 AND pending_count=0
        AND propagation_pending_count=0 THEN 'completed' ELSE 'queued' END,
      lease_token='', lease_expires_at=NULL,
      next_run_at=CASE WHEN discovery_complete=0 OR pending_count>0
        OR propagation_pending_count>0 THEN datetime('now','+5 seconds') ELSE NULL END,
      completed_at=CASE WHEN discovery_complete=1 AND pending_count=0
        AND propagation_pending_count=0 THEN CURRENT_TIMESTAMP ELSE NULL END,
      failure_count=0, error_code='', error_message='', updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status='running' AND lease_token=? AND lease_epoch=?
      AND datetime(lease_expires_at)>CURRENT_TIMESTAMP
    RETURNING ${jobColumns}`)
    .bind(lease.id, lease.leaseToken, lease.leaseEpoch).first<MarketImageCacheJobRow>();
  return row ? publicJob(mapJob(row)) : null;
}

export async function failMarketImageCacheJobLease(
  db: MarketSchemaDatabase,
  input: { lease: MarketImageCacheJobLease; errorCode: string; errorMessage: string },
) {
  const errorCode = input.errorCode.slice(0, 80);
  const errorMessage = input.errorMessage.slice(0, 300);
  const lease = input.lease;
  const writes = await db.batch([
    db.prepare(`INSERT INTO market_image_cache (
        source_url, status, attempt_count, error_code, error_message, created_at, updated_at
      )
      SELECT claim.source_url, 'failed', claim.attempt_count, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM market_image_cache_claims claim
      JOIN market_image_cache_jobs job ON job.id=claim.job_id
      WHERE claim.job_id=? AND claim.job_lease_token=? AND claim.job_epoch=?
        AND job.status='running' AND job.lease_token=? AND job.lease_epoch=?
        AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP
      ON CONFLICT(source_url) DO UPDATE SET status='failed',
        attempt_count=excluded.attempt_count, error_code=excluded.error_code,
        error_message=excluded.error_message, updated_at=CURRENT_TIMESTAMP
      WHERE market_image_cache.status<>'ready'`)
      .bind(errorCode, errorMessage, lease.id, lease.leaseToken, lease.leaseEpoch,
        lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE market_image_cache_job_items SET
        status=CASE WHEN COALESCE((SELECT claim.attempt_count FROM market_image_cache_claims claim
          WHERE claim.source_url=market_image_cache_job_items.source_url
            AND claim.job_id=? AND claim.job_lease_token=? AND claim.job_epoch=?), attempt_count)>=3
          THEN 'failed' ELSE 'queued' END,
        attempt_count=COALESCE((SELECT claim.attempt_count FROM market_image_cache_claims claim
          WHERE claim.source_url=market_image_cache_job_items.source_url
            AND claim.job_id=? AND claim.job_lease_token=? AND claim.job_epoch=?), attempt_count),
        error_code=?, error_message=?,
        completed_at=CASE WHEN COALESCE((SELECT claim.attempt_count FROM market_image_cache_claims claim
          WHERE claim.source_url=market_image_cache_job_items.source_url
            AND claim.job_id=? AND claim.job_lease_token=? AND claim.job_epoch=?), attempt_count)>=3
          THEN CURRENT_TIMESTAMP ELSE NULL END,
        updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND status='queued'
        AND EXISTS (SELECT 1 FROM market_image_cache_claims claim
          WHERE claim.source_url=market_image_cache_job_items.source_url
            AND claim.job_id=? AND claim.job_lease_token=? AND claim.job_epoch=?)
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
          WHERE job.id=market_image_cache_job_items.job_id AND job.status='running'
            AND job.lease_token=? AND job.lease_epoch=?
            AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP)`)
      .bind(lease.id, lease.leaseToken, lease.leaseEpoch,
        lease.id, lease.leaseToken, lease.leaseEpoch, errorCode, errorMessage,
        lease.id, lease.leaseToken, lease.leaseEpoch,
        lease.id, lease.id, lease.leaseToken, lease.leaseEpoch,
        lease.leaseToken, lease.leaseEpoch),
    db.prepare(`DELETE FROM market_image_cache_claims
      WHERE job_id=? AND job_lease_token=? AND job_epoch=?
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
          WHERE job.id=market_image_cache_claims.job_id AND job.status='running'
            AND job.lease_token=? AND job.lease_epoch=?
            AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP)`)
      .bind(lease.id, lease.leaseToken, lease.leaseEpoch, lease.leaseToken, lease.leaseEpoch),
    db.prepare(`UPDATE market_image_cache_jobs SET
        status=CASE WHEN failure_count+1>=3 THEN 'failed' ELSE 'queued' END,
        failure_count=failure_count+1, lease_token='', lease_expires_at=NULL,
        next_run_at=CASE WHEN failure_count+1>=3 THEN NULL ELSE datetime('now','+15 seconds') END,
        error_code=?, error_message=?, completed_at=NULL, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND status='running' AND lease_token=? AND lease_epoch=?
        AND datetime(lease_expires_at)>CURRENT_TIMESTAMP`)
      .bind(errorCode, errorMessage, lease.id, lease.leaseToken, lease.leaseEpoch),
  ]) as Array<unknown>;
  return changes(writes[3]) === 1;
}
