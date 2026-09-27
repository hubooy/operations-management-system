export function annotationFailure(error: unknown) {
  const message = error instanceof Error ? error.message : "视觉识别失败";
  const failureMessage = message.replace(/\b(?:sk-|key-)[A-Za-z0-9_-]{8,}\b/gi, "[已隐藏]")
    .replace(/(authorization\s*[:=]?\s*bearer\s+)\S+/gi, "$1[已隐藏]")
    .replace(/(api[_ -]?key\s*[:=]\s*)\S+/gi, "$1[已隐藏]").slice(0, 300);
  const status = error && typeof error === "object" && "status" in error ? Number(error.status) : 0;
  const retryAfter = error && typeof error === "object" && "retryAfterMs" in error ? Number(error.retryAfterMs) : 0;
  const result = (failureKind: "transient" | "rate_limit" | "permanent", failureCode: string, delay = 0) => ({
    failureKind, failureCode, failureMessage,
    retryAfterMs: Math.min(300_000, Math.max(delay, Number.isFinite(retryAfter) ? retryAfter : 0)),
  });
  if (/模型调用失败.*状态码\s*(401|403)/i.test(message)) return result("permanent", "model_configuration");
  if ([401, 403].includes(status) || /账号或数据权限|权限已|身份已|未授权|access.denied/i.test(message)) return result("permanent", "authorization_revoked");
  if (status === 429 || /状态码\s*429|HTTP\s*429|rate limit|限流|额度不足/i.test(message)) return result("rate_limit", "provider_rate_limit", 60_000);
  if (/主图获取失败|图片/i.test(message)) return result("permanent", "image_fetch");
  if (/调用超时|timeout/i.test(message)) return result("transient", "model_timeout", 5_000);
  if ([408, 425, 500, 502, 503, 504].includes(status) || /网络错误|network|fetch failed/i.test(message)) return result("transient", "model_network", 5_000);
  if (/API Key|不存在或未启用|模型配置|状态码\s*(401|403)/i.test(message)) return result("permanent", "model_configuration");
  if (/模型响应|没有返回|枚举|confidence|价格/i.test(message)) return result("permanent", "model_response");
  return result("permanent", "annotation_failed");
}
