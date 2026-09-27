import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  MARKET_COMMANDS_PATH,
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { PublicApiError } from "@/lib/http/api-error";
import { annotationFailure } from "@/lib/market/annotation-failure";
import { executeAnnotationAttempt } from "@/lib/market/annotation-attempt";
import {
  runVisionAnnotation,
  visionAnnotationTiming,
} from "@/lib/market/annotation-model";

type JsonRecord = Record<string, unknown>;

type ClaimedTask = {
  itemId: string;
  jobId: string;
  skuCode: string;
  productName: string;
  brand: string;
  sourceImageUrl: string;
  promptBody: string;
  segments: string[];
  fixedSegment: string | null;
  modelId: string;
  leaseToken: string;
  ownerEmail: string;
  leaseExpiresAt: string;
};

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function claimedTask(value: unknown): ClaimedTask | null {
  if (value === null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicApiError(503, "service_unavailable", "Django 市场标注任务返回无效。");
  }
  const task = value as JsonRecord;
  const segments = Array.isArray(task.segments)
    ? task.segments.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
  const normalized = {
    itemId: text(task.itemId),
    jobId: text(task.jobId),
    skuCode: text(task.skuCode),
    productName: text(task.productName),
    brand: text(task.brand),
    sourceImageUrl: text(task.sourceImageUrl),
    promptBody: text(task.promptBody),
    segments,
    fixedSegment: text(task.fixedSegment) || null,
    modelId: text(task.modelId),
    leaseToken: text(task.leaseToken),
    ownerEmail: text(task.ownerEmail),
    leaseExpiresAt: text(task.leaseExpiresAt),
  };
  if (!normalized.itemId || !normalized.jobId || !normalized.skuCode
    || !normalized.promptBody || !normalized.segments.length
    || !normalized.modelId || !normalized.leaseToken || !normalized.ownerEmail
    || !Number.isFinite(Date.parse(normalized.leaseExpiresAt))) {
    throw new PublicApiError(503, "service_unavailable", "Django 市场标注任务返回不完整。");
  }
  return normalized;
}

export async function annotationQuery<T extends JsonRecord>(
  principal: AppPrincipal,
  view: string,
  params: JsonRecord,
  signal?: AbortSignal,
) {
  return requestDjangoMarketService<T>(
    principal,
    {
      path: MARKET_QUERIES_PATH,
      service: "reader",
      payload: { operation: "annotations", view, params },
    },
    { signal },
  );
}

export async function annotationCommand<T extends JsonRecord>(
  principal: AppPrincipal,
  value: JsonRecord,
  signal?: AbortSignal,
  requestId?: string,
) {
  return requestDjangoMarketService<{ ok: boolean; result: T }>(
    principal,
    {
      path: MARKET_COMMANDS_PATH,
      service: "writer",
      payload: {
        contractVersion: "market-command-v1",
        domain: "annotations",
        command: value,
      },
    },
    { signal, ...(requestId ? { requestId: () => requestId } : {}) },
  );
}

export async function runClaimedDjangoMarketVisionTask(input: {
  principal: AppPrincipal;
  jobId?: string;
  signal?: AbortSignal;
  coordinatorToken?: string;
}) {
  const claimed = await annotationCommand<{ task: unknown }>(
    input.principal,
    { action: "claim_task", executor: "cloud", jobId: text(input.jobId), ...(input.coordinatorToken ? { coordinatorToken: input.coordinatorToken } : {}) },
    input.signal,
  );
  const task = claimedTask(claimed.data.result.task);
  if (!task) {
    const progress = input.jobId
      ? await annotationQuery<JsonRecord>(input.principal, "progress", { jobId: input.jobId }, input.signal)
      : null;
    const remaining = Number(progress?.data.remainingInferenceUnits ?? 0);
    return {
      status: 200,
      revision: progress?.revision ?? claimed.revision,
      replayed: false,
      data: {
        ok: true,
        result: {
          done: Boolean(progress) && remaining === 0,
          waiting: true,
          processedCount: 0,
          job: progress?.data.job ?? null,
        },
      },
    };
  }
  // One idempotent completion envelope per lease. Retrying storage never reruns inference.
  const completionRequestId = crypto.randomUUID();
  const inferenceDeadline = Math.min(Date.now() + 140_000, Date.parse(task.leaseExpiresAt) - 15_000);
  const complete = async (value: JsonRecord) => {
    try { return await annotationCommand<JsonRecord>(input.principal, value, AbortSignal.timeout(5_000), completionRequestId); }
    catch (error) {
      const status = error && typeof error === "object" && "status" in error ? Number(error.status) : 0;
      if (status && status !== 503) throw error;
      return annotationCommand<JsonRecord>(input.principal, value, AbortSignal.timeout(5_000), completionRequestId);
    }
  };
  return executeAnnotationAttempt({
    infer: async () => {
      const { resolveAiBackgroundPrincipal } = await import("@/lib/ai/background-principal");
      const owner = await resolveAiBackgroundPrincipal(task.ownerEmail, "null");
      if (!owner.ok || !["operator", "admin"].includes(owner.principal.role) || owner.principal.scope !== null) {
        throw new PublicApiError(403, "access_denied", "标注任务发起账号或数据权限已变化");
      }
      return runVisionAnnotation({
      principal: owner.principal,
      modelId: task.modelId,
      promptBody: task.promptBody,
      segments: task.segments,
      skuCode: task.skuCode,
      productName: task.productName,
      brand: task.brand,
      imageUrl: task.sourceImageUrl,
      fixedSegment: task.fixedSegment ?? undefined,
      deadlineAt: inferenceDeadline,
      });
    },
    complete: async (prediction) => {
      const completed = await complete({
        action: "complete_task",
        itemId: task.itemId,
        leaseToken: task.leaseToken,
        result: {
          segment: prediction.segment,
          imagePriceCents: prediction.imagePriceCents,
          priceType: prediction.priceType,
          priceLowCents: prediction.priceLowCents,
          priceHighCents: prediction.priceHighCents,
          confidenceBps: prediction.confidenceBps,
          reason: prediction.reason,
          rawDigest: prediction.rawDigest,
          resolvedImageUrl: prediction.resolvedImageUrl,
          imageSource: prediction.imageSource,
          timing: prediction.timing,
        },
      });
    // Background lanes do not re-aggregate a 10,000-row job after every image.
    const progress = input.coordinatorToken ? null : await annotationQuery<JsonRecord>(
      input.principal, "progress", { jobId: task.jobId }, input.signal,
    );
    return {
      status: 200,
      revision: progress?.revision ?? completed.revision,
      replayed: completed.replayed,
      data: {
        ok: true,
        result: {
          ...completed.data.result,
          done: progress ? Number(progress.data.remainingInferenceUnits ?? 0) === 0 : completed.data.result.done === true,
          waiting: false,
          processedCount: 1,
          job: progress?.data.job ?? null,
        },
      },
    };
    },
    fail: async (error) => {
    const failure = annotationFailure(error);
    const completed = await complete({
        action: "complete_task",
        itemId: task.itemId,
        leaseToken: task.leaseToken,
        error: failure.failureMessage,
        failureCode: failure.failureCode,
        failureKind: failure.failureKind,
        retryAfterMs: failure.retryAfterMs,
        timing: visionAnnotationTiming(error),
      });
    return {
      status: 200,
      revision: completed.revision,
      replayed: false,
      data: {
        ok: true,
        result: {
          done: false,
          waiting: false,
          processedCount: 0,
          failedCount: 1,
          ...failure,
        },
      },
    };
    },
  });
}
