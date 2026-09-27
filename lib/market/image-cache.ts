import { env } from "cloudflare:workers";

import { encodeAnnotationImageBase64, fetchAnnotationImage, resolveAnnotationImageCandidates, type AnnotationImageSource } from "@/lib/market/annotation-image";
import { optimizeAnnotationImageWithRuntime } from "@/lib/market/annotation-image-runtime";
import { annotationModelImageObjectKey, cachedAnnotationModelImage, repairAnnotationModelImageVariant } from "@/lib/market/annotation-model-image";
import { ensureMarketSchema, getMarketDatabase, type MarketDatabase } from "@/lib/market/database";
import {
  acquireMarketImageCacheJobLease,
  discoverMarketImageCacheJobItems,
  failMarketImageCacheJobLease,
  finishMarketImageCacheJobLease,
  heartbeatMarketImageCacheJobLease,
  listMarketImageCacheJobItems,
  markMarketImageCacheJobItemReady,
  markMarketImageCacheJobItemTerminalFailure,
  quarantineTimedOutMarketImageCacheJobLease,
  terminateTimedOutMarketImageCacheJobLease,
  type MarketImageCacheJobLease,
} from "@/lib/market/image-cache-job";
import {
  claimMarketImageCache,
  completeMarketImageCacheClaim,
  failMarketImageCacheClaim,
  propagateMarketImageCacheBatch,
  recoverExpiredMarketImageCacheClaims,
} from "@/lib/market/image-cache-state";

const MAX_CACHE_BATCH = 8;
const CACHE_CONCURRENCY = 4;
const CACHE_MAX_BYTES = 6 * 1024 * 1024;
const CACHE_TIMEOUT_MS = 8_000;
const CACHE_EXTERNAL_DEADLINE_MS = 30_000;

type CacheResult = { sourceUrl: string; cached: boolean; skipped?: boolean; reason?: string; contentHash?: string };

class MarketImageCacheDeadlineError extends Error {
  constructor() {
    super("图片缓存外部阶段超过 30 秒时间片，任务已安全释放并等待重试");
    this.name = "MarketImageCacheDeadlineError";
  }
}

async function withinCacheExternalDeadline<T>(promise: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new MarketImageCacheDeadlineError()), CACHE_EXTERNAL_DEADLINE_MS);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function bucket() {
  if (!env.SALES_IMPORT_FILES) throw new Error("R2 图片缓存未配置");
  return env.SALES_IMPORT_FILES;
}

function extension(mimeType: string) {
  if (mimeType === "image/png") return "png";
  if (mimeType === "image/webp") return "webp";
  return "jpg";
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function cacheOne(db: MarketDatabase, sourceUrl: string, lease: MarketImageCacheJobLease): Promise<CacheResult> {
  const fence = { jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch };
  const claim = await claimMarketImageCache(db, { ...fence, sourceUrl });
  if (claim === null) return { sourceUrl, cached: false, skipped: true, reason: "already_claimed" };
  try {
    const result = await fetchAnnotationImage(sourceUrl, { maxBytes: CACHE_MAX_BYTES, timeoutMs: CACHE_TIMEOUT_MS });
    if (result.kind !== "image") {
      const failed = await failMarketImageCacheClaim(db, { ...fence, ...claim, sourceUrl, errorCode: result.reason, errorMessage: result.message });
      if (!failed) return { sourceUrl, cached: false, skipped: true, reason: "lost_claim" };
      return { sourceUrl, cached: false, reason: result.reason };
    }
    const contentHash = await sha256(result.bytes);
    const objectKey = `market-images/v1/${contentHash}.${extension(result.mimeType)}`;
    const existing = await bucket().head(objectKey);
    if (!existing) {
      await bucket().put(objectKey, result.bytes, {
        httpMetadata: { contentType: result.mimeType, cacheControl: "private, max-age=31536000, immutable" },
        customMetadata: { source: "jd-market-ranking", sha256: contentHash },
      });
    }
    await cacheAnnotationModelVariant(contentHash, result).catch(() => undefined);
    const completed = await completeMarketImageCacheClaim(db, {
      ...fence, ...claim, sourceUrl, objectKey, contentHash, mimeType: result.mimeType,
      sizeBytes: result.bytes.byteLength, imageSource: result.source,
    });
    if (!completed.completed) return { sourceUrl, cached: false, skipped: true, reason: "lost_claim" };
    return { sourceUrl, cached: true, contentHash };
  } catch (error) {
    const message = error instanceof Error ? error.message : "图片缓存失败";
    const failed = await failMarketImageCacheClaim(db, {
      ...fence, ...claim, sourceUrl, errorCode: "cache_failed", errorMessage: message,
    }).catch(() => false);
    if (!failed) return { sourceUrl, cached: false, skipped: true, reason: "lost_claim" };
    return { sourceUrl, cached: false, reason: "cache_failed" };
  }
}

async function cacheAnnotationModelVariant(contentHash: string, image: Extract<Awaited<ReturnType<typeof fetchAnnotationImage>>, { kind: "image" }>) {
  const objectKey = annotationModelImageObjectKey(contentHash);
  if (await bucket().head(objectKey)) return;
  const optimized = await optimizeAnnotationImageWithRuntime(image);
  if (!optimized.optimizedForModel) return;
  await bucket().put(objectKey, optimized.bytes, {
    httpMetadata: { contentType: optimized.mimeType, cacheControl: "private, max-age=31536000, immutable" },
    customMetadata: { source: "market-annotation-model-input", sha256: contentHash },
  });
}

export async function runScheduledMarketImageCacheBatch(
  input: { db?: MarketDatabase; jobId?: string; limit?: number } = {},
) {
  const db = input.db ?? getMarketDatabase();
  await ensureMarketSchema(db);
  const lease = await acquireMarketImageCacheJobLease(db, { jobId: input.jobId });
  if (!lease) return { status: "idle" as const, processed: 0, cachedThisRun: 0, failedThisRun: 0, skippedThisRun: 0 };
  const fence = { jobId: lease.id, leaseToken: lease.leaseToken, jobEpoch: lease.leaseEpoch };
  const limit = Math.max(1, Math.min(MAX_CACHE_BATCH, Math.trunc(input.limit ?? MAX_CACHE_BATCH)));
  try {
    await recoverExpiredMarketImageCacheClaims(db, fence);
    if (lease.failureCount >= 3) {
      await terminateTimedOutMarketImageCacheJobLease(db, lease);
      return { status: "failed" as const, jobId: lease.id, processed: 0, cachedThisRun: 0, failedThisRun: 0, skippedThisRun: 0 };
    }
    const discovery = await discoverMarketImageCacheJobItems(db, lease);
    if (discovery.lostLease) {
      return { status: "lost_lease" as const, jobId: lease.id, processed: 0, cachedThisRun: 0, failedThisRun: 0, skippedThisRun: 0 };
    }
    const items = await listMarketImageCacheJobItems(db, lease, limit);
    const readyForPropagation: Array<{ sourceUrl: string; contentHash: string }> = [];
    const fetchQueue: string[] = [];
    let terminalThisRun = 0;
    for (const item of items) {
      if (item.status === "ready" && /^[a-f0-9]{64}$/.test(item.contentHash)) {
        readyForPropagation.push({ sourceUrl: item.sourceUrl, contentHash: item.contentHash });
      } else if (item.cacheStatus === "ready" && /^[a-f0-9]{64}$/.test(item.cacheContentHash)) {
        if (await markMarketImageCacheJobItemReady(db, lease, item.sourceUrl, item.cacheContentHash)) {
          readyForPropagation.push({ sourceUrl: item.sourceUrl, contentHash: item.cacheContentHash });
        }
      } else if (item.cacheStatus === "failed" && item.cacheAttemptCount >= 3) {
        if (await markMarketImageCacheJobItemTerminalFailure(db, lease, item.sourceUrl)) terminalThisRun += 1;
      } else {
        fetchQueue.push(item.sourceUrl);
      }
    }
    const results: CacheResult[] = [];
    const workers = Array.from({ length: Math.min(CACHE_CONCURRENCY, fetchQueue.length) }, async () => {
      while (fetchQueue.length) {
        const sourceUrl = fetchQueue.shift();
        if (sourceUrl) results.push(await cacheOne(db, sourceUrl, lease));
      }
    });
    await withinCacheExternalDeadline(Promise.all(workers));
    if (!await heartbeatMarketImageCacheJobLease(db, lease)) {
      return { status: "lost_lease" as const, jobId: lease.id, processed: 0, cachedThisRun: 0, failedThisRun: 0, skippedThisRun: results.length };
    }
    const newlyCached = results.filter((item): item is CacheResult & { contentHash: string } => item.cached && Boolean(item.contentHash));
    const images = [...readyForPropagation, ...newlyCached.map((item) => ({ sourceUrl: item.sourceUrl, contentHash: item.contentHash }))];
    const propagation = await propagateMarketImageCacheBatch(db, { ...fence, images });
    const processed = results.filter((item) => !item.skipped).length;
    const finished = await finishMarketImageCacheJobLease(db, lease);
    return {
      status: finished ? finished.status : "lost_lease" as const,
      jobId: lease.id,
      processed,
      cachedThisRun: newlyCached.length,
      failedThisRun: terminalThisRun + results.filter((item) => !item.cached && !item.skipped).length,
      skippedThisRun: results.filter((item) => item.skipped).length,
      discovery,
      propagation,
      ...(finished ?? {}),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "图片缓存后台批次失败";
    if (error instanceof MarketImageCacheDeadlineError) {
      await quarantineTimedOutMarketImageCacheJobLease(db, lease).catch(() => false);
    } else {
      await failMarketImageCacheJobLease(db, {
        lease,
        errorCode: "cache_batch_failed",
        errorMessage: message,
      }).catch(() => false);
    }
    throw error;
  }
}

export async function getCachedMarketImage(contentHash: string, db: MarketDatabase = getMarketDatabase()) {
  if (!/^[a-f0-9]{64}$/.test(contentHash)) return null;
  await ensureMarketSchema(db);
  const row = await db.prepare("SELECT object_key objectKey, mime_type mimeType, size_bytes sizeBytes FROM market_image_cache WHERE content_sha256=? AND status='ready' LIMIT 1")
    .bind(contentHash).first<{ objectKey: string; mimeType: string; sizeBytes: number }>();
  if (!row?.objectKey) return null;
  const object = await bucket().get(row.objectKey);
  return object ? { object, ...row } : null;
}

export async function getCachedMarketImageForAnnotation(sourceUrl: string, db: MarketDatabase) {
  if (!sourceUrl) return null;
  const row = await db.prepare(`SELECT object_key objectKey, content_sha256 contentHash, mime_type mimeType, size_bytes sizeBytes, image_source imageSource
    FROM market_image_cache WHERE source_url=? AND status='ready' AND object_key<>'' LIMIT 1`)
    .bind(sourceUrl).first<{ objectKey: string; contentHash: string; mimeType: string; sizeBytes: number; imageSource: string }>();
  if (!row?.objectKey || !["image/jpeg", "image/png", "image/webp"].includes(row.mimeType)) return null;
  const source = (row.imageSource === "n5" ? "n5" : "imgzone") as AnnotationImageSource;
  const resolvedUrl = resolveAnnotationImageCandidates(sourceUrl).find((candidate) => candidate.source === source)?.url;
  if (!resolvedUrl) return null;
  const modelObject = row.contentHash
    ? await bucket().get(annotationModelImageObjectKey(row.contentHash)).catch(() => null)
    : null;
  if (modelObject) {
    const modelBytes = new Uint8Array(await modelObject.arrayBuffer());
    if (modelBytes.byteLength > 0 && modelBytes.byteLength < Number(row.sizeBytes) && modelBytes.byteLength <= CACHE_MAX_BYTES) {
      const optimized = cachedAnnotationModelImage({ kind: "image", source, url: resolvedUrl }, modelBytes);
      if (optimized) return optimized;
    }
  }
  const object = await bucket().get(row.objectKey);
  if (!object) return null;
  const bytes = new Uint8Array(await object.arrayBuffer());
  if (!bytes.byteLength || bytes.byteLength !== Number(row.sizeBytes) || bytes.byteLength > CACHE_MAX_BYTES) return null;
  const sourceImage = {
    kind: "image" as const,
    source,
    url: resolvedUrl,
    mimeType: row.mimeType,
    bytes,
    base64: encodeAnnotationImageBase64(bytes),
  };
  if (!row.contentHash) return sourceImage;
  return repairAnnotationModelImageVariant(
    sourceImage,
    null,
    optimizeAnnotationImageWithRuntime,
    async (modelBytes) => {
      await bucket().put(annotationModelImageObjectKey(row.contentHash), modelBytes, {
        httpMetadata: { contentType: "image/webp", cacheControl: "private, max-age=31536000, immutable" },
        customMetadata: { source: "market-annotation-model-input", sha256: row.contentHash },
      });
    },
  );
}
