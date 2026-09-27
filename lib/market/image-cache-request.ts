export type MarketImageCacheRequestValidation<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

export function parseMarketImageCachePostBody(
  body: unknown,
): MarketImageCacheRequestValidation<{ batchId?: string }> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { ok: false, error: "请求正文必须是 JSON 对象" };
  }
  const record = body as Record<string, unknown>;
  if (Object.hasOwn(record, "batchId") && typeof record.batchId !== "string") {
    return { ok: false, error: "batchId 必须是字符串" };
  }
  const batchId = typeof record.batchId === "string" ? record.batchId.trim() : undefined;
  if ((batchId?.length ?? 0) > 120) return { ok: false, error: "batchId 不能超过 120 个字符" };
  return { ok: true, value: { batchId: batchId || undefined } };
}

export function parseMarketImageCacheGetQuery(
  searchParams: URLSearchParams,
): MarketImageCacheRequestValidation<{ jobId?: string; batchId?: string }> {
  if (searchParams.has("jobId") && searchParams.has("batchId")) {
    return { ok: false, error: "jobId 与 batchId 不能同时提供" };
  }
  const jobId = searchParams.get("jobId")?.trim() || undefined;
  const batchId = searchParams.get("batchId")?.trim() || undefined;
  if ((jobId?.length ?? 0) > 180 || (batchId?.length ?? 0) > 120) {
    return { ok: false, error: "任务参数过长" };
  }
  return { ok: true, value: { jobId, batchId } };
}
