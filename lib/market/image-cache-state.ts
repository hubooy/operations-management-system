import { marketStandardSkuImagePriceInheritanceSql, type MarketSchemaDatabase } from "@/lib/market/schema-core";

export type MarketImageCacheLeaseFence = {
  jobId: string;
  leaseToken: string;
  jobEpoch: number;
};

export type MarketImageCacheClaim = {
  attemptCount: number;
  claimToken: string;
};

function changes(result: unknown) {
  return Number((result as { meta?: { changes?: number } })?.meta?.changes ?? 0);
}

const MAX_EXPIRED_CLAIM_RECOVERY = 8;
const MAX_LEGACY_FETCHING_RECOVERY = 8;

/** Recovers only claims owned by an older epoch of the currently fenced job. */
export async function recoverExpiredMarketImageCacheClaims(
  db: MarketSchemaDatabase,
  fence: MarketImageCacheLeaseFence,
) {
  const writes = await db.batch([
    db.prepare(`WITH expired_claims AS MATERIALIZED (
        SELECT claim.source_url, claim.attempt_count
        FROM market_image_cache_claims claim INDEXED BY market_image_cache_claims_job_expiry_idx
        WHERE claim.job_id=? AND claim.job_epoch<?
          AND claim.lease_expires_at<=CURRENT_TIMESTAMP
        ORDER BY claim.lease_expires_at, claim.source_url
        LIMIT ${MAX_EXPIRED_CLAIM_RECOVERY}
      )
      INSERT INTO market_image_cache (
        source_url, status, attempt_count, error_code, error_message, created_at, updated_at
      )
      SELECT expired.source_url, 'failed', expired.attempt_count, 'stale_fetch',
        '缓存任务租约超时，可安全重试', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM expired_claims expired CROSS JOIN market_image_cache_jobs job
      WHERE job.id=? AND job.status='running' AND job.lease_token=? AND job.lease_epoch=?
        AND job.lease_expires_at>CURRENT_TIMESTAMP
      ON CONFLICT(source_url) DO UPDATE SET status='failed',
        attempt_count=excluded.attempt_count, error_code=excluded.error_code,
        error_message=excluded.error_message, updated_at=CURRENT_TIMESTAMP
      WHERE market_image_cache.status<>'ready'`)
      .bind(fence.jobId, fence.jobEpoch, fence.jobId, fence.leaseToken, fence.jobEpoch),
    db.prepare(`WITH expired_claims AS MATERIALIZED (
        SELECT claim.source_url, claim.attempt_count
        FROM market_image_cache_claims claim INDEXED BY market_image_cache_claims_job_expiry_idx
        WHERE claim.job_id=? AND claim.job_epoch<?
          AND claim.lease_expires_at<=CURRENT_TIMESTAMP
        ORDER BY claim.lease_expires_at, claim.source_url
        LIMIT ${MAX_EXPIRED_CLAIM_RECOVERY}
      )
      UPDATE market_image_cache_job_items SET
        status=CASE WHEN COALESCE((SELECT expired.attempt_count FROM expired_claims expired
          WHERE expired.source_url=market_image_cache_job_items.source_url), attempt_count)>=3
          THEN 'failed' ELSE 'queued' END,
        attempt_count=COALESCE((SELECT expired.attempt_count FROM expired_claims expired
          WHERE expired.source_url=market_image_cache_job_items.source_url), attempt_count),
        error_code='stale_fetch', error_message='缓存任务租约超时，可安全重试',
        completed_at=CASE WHEN COALESCE((SELECT expired.attempt_count FROM expired_claims expired
          WHERE expired.source_url=market_image_cache_job_items.source_url), attempt_count)>=3
          THEN CURRENT_TIMESTAMP ELSE NULL END,
        updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND status='queued'
        AND source_url IN (SELECT source_url FROM expired_claims)
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
          WHERE job.id=market_image_cache_job_items.job_id AND job.status='running'
            AND job.lease_token=? AND job.lease_epoch=?
            AND job.lease_expires_at>CURRENT_TIMESTAMP)`)
      .bind(fence.jobId, fence.jobEpoch, fence.jobId, fence.leaseToken, fence.jobEpoch),
    db.prepare(`WITH expired_claims AS MATERIALIZED (
        SELECT claim.source_url
        FROM market_image_cache_claims claim INDEXED BY market_image_cache_claims_job_expiry_idx
        WHERE claim.job_id=? AND claim.job_epoch<?
          AND claim.lease_expires_at<=CURRENT_TIMESTAMP
        ORDER BY claim.lease_expires_at, claim.source_url
        LIMIT ${MAX_EXPIRED_CLAIM_RECOVERY}
      )
      DELETE FROM market_image_cache_claims
      WHERE job_id=? AND source_url IN (SELECT source_url FROM expired_claims)
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
          WHERE job.id=? AND job.status='running' AND job.lease_token=?
            AND job.lease_epoch=? AND job.lease_expires_at>CURRENT_TIMESTAMP)`)
      .bind(fence.jobId, fence.jobEpoch, fence.jobId, fence.jobId, fence.leaseToken, fence.jobEpoch),
    db.prepare(`WITH stale_legacy AS MATERIALIZED (
        SELECT cache.source_url
        FROM market_image_cache cache INDEXED BY market_image_cache_fetching_recovery_idx
        WHERE cache.status='fetching' AND cache.updated_at<datetime('now','-10 minutes')
          AND NOT EXISTS (SELECT 1 FROM market_image_cache_claims claim
            WHERE claim.source_url=cache.source_url)
        ORDER BY cache.updated_at, cache.source_url
        LIMIT ${MAX_LEGACY_FETCHING_RECOVERY}
      )
      UPDATE market_image_cache SET status='failed', error_code='stale_fetch',
        error_message='旧版缓存领取超时，可安全重试', updated_at=CURRENT_TIMESTAMP
      WHERE source_url IN (SELECT source_url FROM stale_legacy)
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
          WHERE job.id=? AND job.status='running' AND job.lease_token=?
            AND job.lease_epoch=? AND job.lease_expires_at>CURRENT_TIMESTAMP)`)
      .bind(fence.jobId, fence.leaseToken, fence.jobEpoch),
  ]) as Array<unknown>;
  return changes(writes[2]);
}

export async function claimMarketImageCache(
  db: MarketSchemaDatabase,
  input: MarketImageCacheLeaseFence & { sourceUrl: string },
): Promise<MarketImageCacheClaim | null> {
  const claimToken = crypto.randomUUID();
  const claim = await db.prepare(`INSERT INTO market_image_cache_claims (
      source_url, job_id, claim_token, job_lease_token, job_epoch,
      attempt_count, lease_expires_at, created_at, updated_at
    )
    SELECT ?, job.id, ?, job.lease_token, job.lease_epoch,
      MAX(COALESCE(cache.attempt_count, 0), item.attempt_count)+1,
      datetime('now','+2 minutes'), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    FROM market_image_cache_jobs job
    JOIN market_image_cache_job_items item ON item.job_id=job.id AND item.source_url=?
    LEFT JOIN market_image_cache cache ON cache.source_url=item.source_url
    WHERE job.id=? AND job.status='running' AND job.lease_token=? AND job.lease_epoch=?
      AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP AND item.status='queued'
      AND (cache.source_url IS NULL OR cache.status='pending'
        OR (cache.status='failed' AND cache.attempt_count<3))
    ON CONFLICT(source_url) DO UPDATE SET
      job_id=excluded.job_id, claim_token=excluded.claim_token,
      job_lease_token=excluded.job_lease_token, job_epoch=excluded.job_epoch,
      attempt_count=MAX(market_image_cache_claims.attempt_count, excluded.attempt_count-1)+1,
      lease_expires_at=excluded.lease_expires_at,
      updated_at=CURRENT_TIMESTAMP
    WHERE datetime(market_image_cache_claims.lease_expires_at)<=CURRENT_TIMESTAMP
    RETURNING attempt_count attemptCount, claim_token claimToken`)
    .bind(input.sourceUrl, claimToken, input.sourceUrl, input.jobId, input.leaseToken, input.jobEpoch)
    .first<{ attemptCount: number | string; claimToken: string }>();
  return claim ? { attemptCount: Number(claim.attemptCount), claimToken: claim.claimToken } : null;
}

export async function failMarketImageCacheClaim(
  db: MarketSchemaDatabase,
  input: MarketImageCacheLeaseFence & MarketImageCacheClaim & {
    sourceUrl: string;
    errorCode: string;
    errorMessage: string;
  },
) {
  const errorCode = input.errorCode.slice(0, 80);
  const errorMessage = input.errorMessage.slice(0, 300);
  const claimFilter = `claim.source_url=? AND claim.job_id=? AND claim.claim_token=?
    AND claim.job_lease_token=? AND claim.job_epoch=?`;
  const liveJobFilter = `job.id=? AND job.status='running' AND job.lease_token=?
    AND job.lease_epoch=? AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP`;
  const writes = await db.batch([
    db.prepare(`INSERT INTO market_image_cache (
        source_url, status, attempt_count, error_code, error_message, created_at, updated_at
      )
      SELECT claim.source_url, 'failed', claim.attempt_count, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM market_image_cache_claims claim JOIN market_image_cache_jobs job ON job.id=claim.job_id
      WHERE ${claimFilter} AND ${liveJobFilter}
      ON CONFLICT(source_url) DO UPDATE SET status='failed', attempt_count=excluded.attempt_count,
        error_code=excluded.error_code, error_message=excluded.error_message, updated_at=CURRENT_TIMESTAMP`)
      .bind(errorCode, errorMessage, input.sourceUrl, input.jobId, input.claimToken,
        input.leaseToken, input.jobEpoch, input.jobId, input.leaseToken, input.jobEpoch),
    db.prepare(`UPDATE market_image_cache_job_items SET
        status=CASE WHEN ? >= 3 THEN 'failed' ELSE 'queued' END,
        attempt_count=?, error_code=?, error_message=?,
        completed_at=CASE WHEN ? >= 3 THEN CURRENT_TIMESTAMP ELSE NULL END,
        updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND source_url=? AND status='queued'
        AND EXISTS (SELECT 1 FROM market_image_cache_claims claim
          WHERE ${claimFilter})
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job WHERE ${liveJobFilter})`)
      .bind(input.attemptCount, input.attemptCount, errorCode, errorMessage, input.attemptCount,
        input.jobId, input.sourceUrl, input.sourceUrl, input.jobId, input.claimToken,
        input.leaseToken, input.jobEpoch, input.jobId, input.leaseToken, input.jobEpoch),
    db.prepare(`DELETE FROM market_image_cache_claims
      WHERE source_url=? AND job_id=? AND claim_token=? AND job_lease_token=? AND job_epoch=?
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job WHERE ${liveJobFilter})`)
      .bind(input.sourceUrl, input.jobId, input.claimToken, input.leaseToken, input.jobEpoch,
        input.jobId, input.leaseToken, input.jobEpoch),
  ]) as Array<unknown>;
  return changes(writes[0]) === 1 && changes(writes[1]) === 1 && changes(writes[2]) === 1;
}

export async function completeMarketImageCacheClaim(
  db: MarketSchemaDatabase,
  input: MarketImageCacheLeaseFence & MarketImageCacheClaim & {
    sourceUrl: string;
    objectKey: string;
    contentHash: string;
    mimeType: string;
    sizeBytes: number;
    imageSource: string;
  },
) {
  if (!/^[a-f0-9]{64}$/.test(input.contentHash)) return { completed: false };
  const claimFilter = `claim.source_url=? AND claim.job_id=? AND claim.claim_token=?
    AND claim.job_lease_token=? AND claim.job_epoch=?`;
  const liveJobFilter = `job.id=? AND job.status='running' AND job.lease_token=?
    AND job.lease_epoch=? AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP`;
  const claimBindings = [input.sourceUrl, input.jobId, input.claimToken, input.leaseToken, input.jobEpoch];
  const liveBindings = [input.jobId, input.leaseToken, input.jobEpoch];
  const writes = await db.batch([
    db.prepare(`INSERT INTO market_image_cache (
        source_url, status, object_key, content_sha256, mime_type, size_bytes, image_source,
        attempt_count, error_code, error_message, created_at, updated_at
      )
      SELECT claim.source_url, 'ready', ?, ?, ?, ?, ?, claim.attempt_count,
        '', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
      FROM market_image_cache_claims claim JOIN market_image_cache_jobs job ON job.id=claim.job_id
      WHERE ${claimFilter} AND ${liveJobFilter}
      ON CONFLICT(source_url) DO UPDATE SET status='ready', object_key=excluded.object_key,
        content_sha256=excluded.content_sha256, mime_type=excluded.mime_type,
        size_bytes=excluded.size_bytes, image_source=excluded.image_source,
        attempt_count=excluded.attempt_count, error_code='', error_message='', updated_at=CURRENT_TIMESTAMP`)
      .bind(input.objectKey, input.contentHash, input.mimeType, input.sizeBytes, input.imageSource,
        ...claimBindings, ...liveBindings),
    db.prepare(`UPDATE market_image_cache_job_items SET status='ready', content_sha256=?,
        attempt_count=?, error_code='', error_message='', completed_at=NULL, updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND source_url=? AND status='queued'
        AND EXISTS (SELECT 1 FROM market_image_cache_claims claim WHERE ${claimFilter})
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job WHERE ${liveJobFilter})`)
      .bind(input.contentHash, input.attemptCount, input.jobId, input.sourceUrl,
        ...claimBindings, ...liveBindings),
    db.prepare(`DELETE FROM market_image_cache_claims
      WHERE source_url=? AND job_id=? AND claim_token=? AND job_lease_token=? AND job_epoch=?
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job WHERE ${liveJobFilter})`)
      .bind(...claimBindings, ...liveBindings),
  ]) as Array<unknown>;
  return { completed: changes(writes[0]) === 1 && changes(writes[1]) === 1 && changes(writes[2]) === 1 };
}

export async function propagateMarketImageCacheBatch(
  db: MarketSchemaDatabase,
  input: MarketImageCacheLeaseFence & { images: Array<{ sourceUrl: string; contentHash: string }> },
) {
  const images = input.images.filter((image, index, all) =>
    Boolean(image.sourceUrl) && /^[a-f0-9]{64}$/.test(image.contentHash)
      && all.findIndex((candidate) => candidate.sourceUrl === image.sourceUrl) === index).slice(0, 8);
  if (!images.length) return { snapshotsUpdated: 0, pricesInherited: 0 };
  const imagesJson = JSON.stringify(images);
  const fencedItemHashes = `SELECT item.content_sha256
    FROM json_each(?) submitted
    JOIN market_image_cache_job_items item
      ON item.job_id=? AND item.source_url=json_extract(submitted.value, '$.sourceUrl')
      AND item.content_sha256=json_extract(submitted.value, '$.contentHash')
    JOIN market_image_cache_jobs job ON job.id=item.job_id
    WHERE item.status='ready' AND job.status='running' AND job.lease_token=?
      AND job.lease_epoch=? AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP`;
  const sourceHashFilter = `source.image_content_sha256 IN (${fencedItemHashes})`;
  const targetHashFilter = `target.image_content_sha256 IN (${fencedItemHashes})`;
  const writes = await db.batch([
    db.prepare(`WITH submitted AS (
        SELECT json_extract(value, '$.sourceUrl') source_url,
          json_extract(value, '$.contentHash') content_hash
        FROM json_each(?)
      ), completed AS MATERIALIZED (
        SELECT item.source_url, item.content_sha256 content_hash
        FROM submitted
        JOIN market_image_cache_job_items item ON item.job_id=?
          AND item.source_url=submitted.source_url AND item.content_sha256=submitted.content_hash
        JOIN market_image_cache_jobs job ON job.id=item.job_id
        WHERE item.status='ready' AND job.status='running' AND job.lease_token=?
          AND job.lease_epoch=? AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP
      ), direct_candidates AS MATERIALIZED (
        SELECT target.id snapshot_id, completed.source_url, completed.content_hash,
          0 match_priority, '' period_end, 0 ranking_id
        FROM completed
        CROSS JOIN market_price_snapshots AS target INDEXED BY market_price_snapshots_pending_image_url_idx
        WHERE target.image_url=completed.source_url
          AND target.image_content_sha256='' AND target.image_url<>''
      ), legacy_candidates AS MATERIALIZED (
        SELECT target.id snapshot_id, completed.source_url, completed.content_hash,
          1 match_priority, ranking.period_end, ranking.id ranking_id
        FROM completed
        CROSS JOIN market_ranking_entries AS ranking INDEXED BY market_entries_image_url_idx
        CROSS JOIN market_price_snapshots AS target INDEXED BY market_price_snapshots_sku_month_uq
        WHERE ranking.image_url=completed.source_url AND ranking.image_url<>''
          AND target.category=ranking.category AND target.scope=ranking.scope
          AND target.sku_code=ranking.sku_code
          AND target.ranking_dimension=ranking.ranking_dimension
          AND target.month=substr(ranking.period_end,1,7)
          AND target.image_content_sha256='' AND target.image_url=''
      ), candidates AS (
        SELECT * FROM direct_candidates
        UNION ALL
        SELECT * FROM legacy_candidates
      ), resolved AS (
        SELECT snapshot_id, source_url, content_hash,
          ROW_NUMBER() OVER (PARTITION BY snapshot_id
            ORDER BY match_priority, period_end DESC, ranking_id DESC, source_url) match_rank
        FROM candidates
      )
      UPDATE market_price_snapshots AS target SET
        image_content_sha256=(SELECT resolved.content_hash FROM resolved
          WHERE resolved.snapshot_id=target.id AND resolved.match_rank=1),
        image_url=CASE WHEN target.image_url='' THEN (SELECT resolved.source_url FROM resolved
          WHERE resolved.snapshot_id=target.id AND resolved.match_rank=1) ELSE target.image_url END,
        updated_at=CURRENT_TIMESTAMP
      WHERE target.image_content_sha256=''
        AND target.id IN (SELECT snapshot_id FROM resolved WHERE match_rank=1)`)
      .bind(imagesJson, input.jobId, input.leaseToken, input.jobEpoch),
    db.prepare(marketStandardSkuImagePriceInheritanceSql(targetHashFilter, sourceHashFilter, true))
      .bind(imagesJson, input.jobId, input.leaseToken, input.jobEpoch,
        imagesJson, input.jobId, input.leaseToken, input.jobEpoch),
    db.prepare(`UPDATE market_image_cache_job_items SET status='completed',
        completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
      WHERE job_id=? AND status='ready'
        AND EXISTS (SELECT 1 FROM json_each(?) submitted
          WHERE json_extract(submitted.value, '$.sourceUrl')=market_image_cache_job_items.source_url
            AND json_extract(submitted.value, '$.contentHash')=market_image_cache_job_items.content_sha256)
        AND EXISTS (SELECT 1 FROM market_image_cache_jobs job
          WHERE job.id=market_image_cache_job_items.job_id AND job.status='running'
            AND job.lease_token=? AND job.lease_epoch=?
            AND datetime(job.lease_expires_at)>CURRENT_TIMESTAMP)`)
      .bind(input.jobId, imagesJson, input.leaseToken, input.jobEpoch),
  ]) as Array<unknown>;
  return {
    snapshotsUpdated: changes(writes[0]),
    pricesInherited: changes(writes[1]),
  };
}
