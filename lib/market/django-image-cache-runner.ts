import { env } from "cloudflare:workers";

import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  MARKET_COMMANDS_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { fetchAnnotationImage } from "@/lib/market/annotation-image";
import { optimizeAnnotationImageWithRuntime } from "@/lib/market/annotation-image-runtime";
import { annotationModelImageObjectKey } from "@/lib/market/annotation-model-image";

const INTERNAL_IMAGE_PRINCIPAL: AppPrincipal = {
  email: "market-image-runner@teruisi.internal",
  displayName: "市场图片缓存执行器",
  role: "admin",
  scope: null,
};
const MAX_BATCH = 8;
const MAX_BYTES = 6 * 1024 * 1024;
const FETCH_TIMEOUT_MS = 8_000;

type JsonRecord = Record<string, unknown>;
type Claim = { sourceUrl: string; claimToken: string; attemptCount: number };
type JobLease = {
  id: string;
  leaseToken: string;
  leaseEpoch: number;
};

async function command<T extends JsonRecord>(commandPayload: JsonRecord, signal?: AbortSignal) {
  const response = await requestDjangoMarketService<{ ok: boolean; result: T }>(
    INTERNAL_IMAGE_PRINCIPAL,
    {
      path: MARKET_COMMANDS_PATH,
      service: "writer",
      payload: {
        contractVersion: "market-command-v1",
        domain: "images",
        command: commandPayload,
      },
    },
    { signal },
  );
  return response.data.result;
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const digest = await crypto.subtle.digest("SHA-256", input);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function extension(mimeType: string) {
  if (mimeType === "image/png") return "png";
  if (mimeType === "image/webp") return "webp";
  return "jpg";
}

async function cacheClaim(
  bucket: R2Bucket,
  job: JobLease,
  claim: Claim,
  signal?: AbortSignal,
) {
  const fence = {
    jobId: job.id,
    jobLeaseToken: job.leaseToken,
    jobEpoch: job.leaseEpoch,
    sourceUrl: claim.sourceUrl,
    claimToken: claim.claimToken,
  };
  try {
    const image = await fetchAnnotationImage(claim.sourceUrl, {
      maxBytes: MAX_BYTES,
      timeoutMs: FETCH_TIMEOUT_MS,
    });
    if (image.kind !== "image") {
      await command({
        action: "complete_image_cache_claim",
        ...fence,
        errorCode: image.reason,
        errorMessage: image.message,
      }, signal);
      return { cached: false, failed: true };
    }
    const contentSha256 = await sha256(image.bytes);
    const objectKey = `market-images/v1/${contentSha256}.${extension(image.mimeType)}`;
    if (!await bucket.head(objectKey)) {
      await bucket.put(objectKey, image.bytes, {
        httpMetadata: {
          contentType: image.mimeType,
          cacheControl: "private, max-age=31536000, immutable",
        },
        customMetadata: { source: "jd-market-ranking", sha256: contentSha256 },
      });
    }
    await (async () => {
      const optimized = await optimizeAnnotationImageWithRuntime(image);
      if (!optimized.optimizedForModel) return;
      const modelKey = annotationModelImageObjectKey(contentSha256);
      if (await bucket.head(modelKey)) return;
      await bucket.put(modelKey, optimized.bytes, {
        httpMetadata: {
          contentType: optimized.mimeType,
          cacheControl: "private, max-age=31536000, immutable",
        },
        customMetadata: { source: "market-annotation-model-input", sha256: contentSha256 },
      });
    })().catch(() => undefined);
    await command({
      action: "complete_image_cache_claim",
      ...fence,
      contentSha256,
      objectKey,
      mimeType: image.mimeType,
      sizeBytes: image.bytes.byteLength,
      imageSource: image.source,
      errorCode: "",
      errorMessage: "",
    }, signal);
    return { cached: true, failed: false };
  } catch (error) {
    const message = (error instanceof Error ? error.message : "图片缓存失败").slice(0, 500);
    await command({
      action: "complete_image_cache_claim",
      ...fence,
      errorCode: "cache_failed",
      errorMessage: message,
    }, signal).catch(() => undefined);
    return { cached: false, failed: true };
  }
}

export async function runDjangoMarketImageCacheBatch(input: {
  jobId?: string;
  limit?: number;
  bucket?: R2Bucket;
  signal?: AbortSignal;
} = {}) {
  const bucket = input.bucket ?? env.SALES_IMPORT_FILES;
  if (!bucket) throw new Error("R2 图片缓存未配置");
  const limit = Math.max(1, Math.min(MAX_BATCH, Math.trunc(input.limit ?? MAX_BATCH)));
  const claimed = await command<{
    job: (JobLease & JsonRecord) | null;
    claims: Claim[];
  }>({
    action: "claim_image_cache",
    jobId: input.jobId ?? "",
    limit,
  }, input.signal);
  if (!claimed.job) {
    return { status: "idle" as const, processed: 0, cachedThisRun: 0, failedThisRun: 0 };
  }
  const claims = Array.isArray(claimed.claims) ? claimed.claims.slice(0, limit) : [];
  const results = await Promise.all(
    claims.map((item) => cacheClaim(bucket, claimed.job!, item, input.signal)),
  );
  const finished = await command<{ ok: boolean; job: JsonRecord }>({
    action: "finish_image_cache_job",
    jobId: claimed.job.id,
    jobLeaseToken: claimed.job.leaseToken,
    jobEpoch: claimed.job.leaseEpoch,
  }, input.signal);
  return {
    status: String(finished.job.status ?? "queued"),
    jobId: claimed.job.id,
    processed: results.length,
    cachedThisRun: results.filter((item) => item.cached).length,
    failedThisRun: results.filter((item) => item.failed).length,
  };
}
