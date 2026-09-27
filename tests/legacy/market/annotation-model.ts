import { decryptSecret } from "@/lib/ai/crypto";
import {
  loadAiEndpointSecurityContext,
  resolveAiModelEndpointUrl,
} from "@/lib/ai/endpoint-security";
import { completeText, type AiTextModelRuntimeConfig } from "@/lib/ai/model-gateway";
import { AI_MODEL_TOOL_BUDGET_LIMITS } from "@/lib/ai/model-tool-budget";
import { fetchAnnotationImage } from "@/lib/market/annotation-image";
import type { MarketDatabase } from "@/lib/market/database";
import { digest, parseVisionAnnotation, type VisionAnnotation } from "@/lib/market/annotation-types";

export type AnnotationModelConfig = {
  id: string; name: string; protocol: string; model_type: string; model_name: string;
  base_url: string; api_key_encrypted: string; status: string;
  timeout_ms?: number; max_tokens?: number; reasoning_mode?: string; temperature_milli?: number;
  max_tool_rounds?: number; max_total_tool_calls?: number;
};
type ModelRow = AnnotationModelConfig;
const DEFAULT_MODEL_TIMEOUT_MS = 60_000;
const VISION_ANNOTATION_TIMEOUT_MAX_MS = 90_000;
const MODEL_RESPONSE_MAX_BYTES = 2 * 1024 * 1024;
const VISION_ANNOTATION_OUTPUT_TOKEN_MAX = 600;
const VISION_PRICE_ONLY_OUTPUT_TOKEN_MAX = 320;
const VISION_PROBE_IMAGE_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAAvSURBVFhH7c6hAQAACMOw/f/08BwAJqKmKmnSz7LHdQAAAAAAAAAAAAAAAAAAAANUDfhqnpuFxwAAAABJRU5ErkJggg==";

export async function listAnnotationModels(db: MarketDatabase) {
  const rows = await db.prepare("SELECT * FROM ai_models WHERE status = 'enabled' AND model_type IN ('vision','image') ORDER BY updated_at DESC").all<ModelRow>();
  return (rows.results ?? []).map(({ id, name, protocol, model_name }) => ({ id, name, protocol, modelName: model_name }));
}

export async function listPromptTextModels(db: MarketDatabase) {
  const rows = await db.prepare("SELECT * FROM ai_models WHERE status = 'enabled' AND model_type = 'text' ORDER BY is_default_text_model DESC, updated_at DESC").all<ModelRow>();
  return (rows.results ?? []).map(({ id, name, protocol, model_name }) => ({ id, name, protocol, modelName: model_name }));
}

async function getModel(db: MarketDatabase, id: string, type: "vision" | "text") {
  const row = type === "vision"
    ? await db.prepare("SELECT * FROM ai_models WHERE id = ? AND status = 'enabled' AND model_type IN ('vision','image') LIMIT 1").bind(id).first<ModelRow>()
    : await db.prepare("SELECT * FROM ai_models WHERE id = ? AND status = 'enabled' AND model_type = ? LIMIT 1").bind(id, type).first<ModelRow>();
  if (!row) throw new Error(`所选 ${type === "vision" ? "视觉" : "文本"} 模型不存在或未启用`);
  return row;
}

export async function probeVisionModelConnection(model: AnnotationModelConfig): Promise<string> {
  const segments = ["红色", "其他"] as const;
  const image: LoadedImage = {
    kind: "image",
    source: "imgzone",
    url: "inline://vision-capability-probe",
    mimeType: "image/png",
    bytes: new Uint8Array(),
    base64: VISION_PROBE_IMAGE_BASE64,
  };
  const prompt = "请观察随请求发送的测试图片主色。segment 只能返回“红色”或“其他”；图片中没有商品价格，因此价格和价格区间返回 null，price_type 返回“无法判断”，并给出置信度和简短依据。不要根据文字猜测颜色。";
  const raw = model.protocol === "anthropic"
    ? await callAnthropicVision(model, prompt, segments, image)
    : await callOpenAiVision(model, prompt, segments, image);
  const parsed = parseVisionAnnotation(raw, segments);
  if (parsed.segment !== "红色") throw new Error("模型接口可连接，但未能识别测试图片；请确认模型标识支持图片输入");
  return "图片识别成功";
}

export async function runVisionAnnotation(input: {
  db: MarketDatabase; modelId: string; promptBody: string; segments: readonly string[];
  skuCode: string; productName: string; brand: string; imageUrl: string; fixedSegment?: string;
  skipMarketCache?: boolean;
}): Promise<VisionAnnotation & { imageSource: "imgzone" | "n5" | "none"; resolvedImageUrl: string; rawDigest: string; timing: VisionAnnotationTiming }> {
  const startedAt = Date.now();
  const timing: VisionAnnotationTiming = { imageLoadMs: 0, imagePrepareMs: 0, modelCallMs: 0, totalMs: 0, inputBytes: 0 };
  try {
    const model = await getModel(input.db, input.modelId, "vision");
    const imageLoadStartedAt = Date.now();
    const cachedImage = input.imageUrl && !input.skipMarketCache
      ? await loadCachedAnnotationImage(input.db, input.imageUrl)
      : null;
    const sourceImage = cachedImage ?? (input.imageUrl ? await fetchAnnotationImage(input.imageUrl) : { kind: "no-image" as const, reason: "invalid_url" as const, message: "没有图片地址" });
    timing.imageLoadMs = Date.now() - imageLoadStartedAt;
    if (sourceImage.kind !== "image") throw new Error(`主图获取失败：${sourceImage.message}`);
    const imagePrepareStartedAt = Date.now();
    const image = await prepareAnnotationModelImage(sourceImage);
    timing.imagePrepareMs = Date.now() - imagePrepareStartedAt;
    timing.inputBytes = image.bytes.byteLength;
    const fixedSegment = input.fixedSegment?.trim() ?? "";
    if (fixedSegment && !input.segments.includes(fixedSegment)) throw new Error("历史细分品类已失效，不能执行价格专用识别");
    const outputSegments = fixedSegment ? [fixedSegment] : input.segments;
    const text = `${fixedSegment ? priceOnlyAnnotationPrompt(fixedSegment) : `${input.promptBody}\n\n允许的细分品类：${input.segments.join("、")}`}\nSKU：${input.skuCode}\n商品名称：${input.productName}\n品牌：${input.brand || "未知"}\n必须返回主图中清晰可见且可作为完整商品售价的价格（人民币元，可带两位小数；没有则 null）、价格类型、价格区间最低/最高值（同样使用人民币元）、0到1置信度和简短证据。忽略销量、优惠券面额、补贴金额、划线原价及赠品价格；分期每期金额、定金、起售价和最低规格价必须如实标记，不能冒充完整商品售价。价格类型只能是：标准售价、到手价、券后价、起售价、价格区间、定金、分期金额、最低规格价格、无法判断。`;
    const modelCallStartedAt = Date.now();
    let raw: unknown;
    try {
      raw = model.protocol === "anthropic"
        ? await callAnthropicVision(model, text, outputSegments, image, fixedSegment ? VISION_PRICE_ONLY_OUTPUT_TOKEN_MAX : VISION_ANNOTATION_OUTPUT_TOKEN_MAX)
        : await callOpenAiVision(model, text, outputSegments, image, fixedSegment ? VISION_PRICE_ONLY_OUTPUT_TOKEN_MAX : VISION_ANNOTATION_OUTPUT_TOKEN_MAX);
    } finally {
      timing.modelCallMs = Date.now() - modelCallStartedAt;
    }
    const parsed = parseVisionAnnotation(raw, outputSegments);
    timing.totalMs = Date.now() - startedAt;
    return {
      ...parsed,
      imageSource: image.source,
      resolvedImageUrl: image.url,
      rawDigest: digest(parsed.rawText),
      timing,
    };
  } catch (error) {
    timing.totalMs = Date.now() - startedAt;
    throw new VisionAnnotationExecutionError(error instanceof Error ? error.message : "视觉识别失败", timing);
  }
}

export type VisionAnnotationTiming = {
  imageLoadMs: number;
  imagePrepareMs: number;
  modelCallMs: number;
  totalMs: number;
  inputBytes: number;
};

export class VisionAnnotationExecutionError extends Error {
  constructor(message: string, readonly timing: VisionAnnotationTiming) {
    super(message);
    this.name = "VisionAnnotationExecutionError";
  }
}

export function visionAnnotationTiming(error: unknown): VisionAnnotationTiming {
  return error instanceof VisionAnnotationExecutionError
    ? error.timing
    : { imageLoadMs: 0, imagePrepareMs: 0, modelCallMs: 0, totalMs: 0, inputBytes: 0 };
}

export function priceOnlyAnnotationPrompt(segment: string) {
  return `该 SKU 的细分品类已经人工复核并正式入库，固定为“${segment}”。不要重新分类，只识别当前新主图价格；segment 必须原样返回“${segment}”。`;
}

async function loadCachedAnnotationImage(db: MarketDatabase, sourceUrl: string) {
  try {
    const { getCachedMarketImageForAnnotation } = await import("@/lib/market/image-cache");
    return await getCachedMarketImageForAnnotation(sourceUrl, db);
  } catch {
    return null;
  }
}

async function prepareAnnotationModelImage<T extends LoadedImage>(image: T): Promise<T> {
  if ("optimizedForModel" in image && image.optimizedForModel) return image;
  try {
    const { optimizeAnnotationImageWithRuntime } = await import("@/lib/market/annotation-image-runtime");
    return await optimizeAnnotationImageWithRuntime(image);
  } catch {
    return image;
  }
}

export async function runPromptTextCompletion(db: MarketDatabase, modelId: string, instruction: string) {
  const model = await getModel(db, modelId, "text");
  return completeText({
    model: textRuntimeModel(model),
    messages: [{ role: "user", content: instruction }],
  });
}

function textRuntimeModel(model: ModelRow): AiTextModelRuntimeConfig {
  return {
    id: model.id,
    name: model.name,
    protocol: model.protocol === "anthropic" ? "anthropic" : "openai_compatible",
    modelName: model.model_name,
    baseUrl: model.base_url,
    apiKeyEncrypted: model.api_key_encrypted,
    timeoutMs: boundedModelSetting(model.timeout_ms, 20_000, 3_000, 120_000),
    maxTokens: boundedModelSetting(model.max_tokens, 4_096, 128, 8_192),
    reasoningMode: model.protocol !== "anthropic" && model.reasoning_mode === "disabled" ? "disabled" : "auto",
    temperature: boundedModelSetting(model.temperature_milli, 200, 0, 1_000) / 1_000,
    maxToolRounds: boundedModelSetting(model.max_tool_rounds, AI_MODEL_TOOL_BUDGET_LIMITS.defaultRounds, 1, AI_MODEL_TOOL_BUDGET_LIMITS.maximumRounds),
    maxTotalToolCalls: boundedModelSetting(model.max_total_tool_calls, AI_MODEL_TOOL_BUDGET_LIMITS.defaultTotalCalls, 1, AI_MODEL_TOOL_BUDGET_LIMITS.maximumTotalCalls),
  };
}

function boundedModelSetting(value: number | undefined, fallback: number, minimum: number, maximum: number) {
  return Number.isInteger(value) && Number(value) >= minimum && Number(value) <= maximum ? Number(value) : fallback;
}

type LoadedImage = Extract<Awaited<ReturnType<typeof fetchAnnotationImage>>, { kind: "image" }>;
type ModelErrorEnvelope = { error?: string | { message?: unknown; code?: unknown; type?: unknown }; message?: unknown };

function modelErrorDetail(data: unknown) {
  if (!data || typeof data !== "object") return "";
  const envelope = data as ModelErrorEnvelope;
  const error = envelope.error;
  const message = typeof error === "string" ? error : typeof error?.message === "string" ? error.message : typeof envelope.message === "string" ? envelope.message : "";
  const code = typeof error === "object" && error && typeof error.code === "string" ? error.code : "";
  const detail = [code, message].filter(Boolean).join(" · ").replace(/\s+/g, " ").trim();
  return detail
    .replace(/\b(sk-|key-)[A-Za-z0-9_-]{8,}\b/gi, "$1…")
    .replace(/(authorization\s*[:=]?\s*bearer\s+)\S+/gi, "$1…")
    .replace(/(api[_ -]?key\s*[:=]\s*)\S+/gi, "$1…")
    .slice(0, 180);
}

function modelCallError(kind: "文本" | "视觉", status: number, data: unknown) {
  const detail = modelErrorDetail(data);
  const hint = status === 429
    ? "模型供应商限流或额度不足，请稍后重试并检查额度"
    : status === 400
      ? "请求被接口拒绝，请核对模型标识、图片输入及结构化输出兼容性"
      : "请检查模型服务状态和接口配置";
  return new Error(`${kind}模型调用失败（状态码 ${status}：${detail || hint}）`);
}

async function callOpenAiVision(model: ModelRow, text: string, segments: readonly string[], image: LoadedImage | null, outputTokenCap?: number) {
  const endpointSecurityContext = await loadAiEndpointSecurityContext();
  const endpointUrl = resolveAiModelEndpointUrl(model.base_url, "openai_compatible", endpointSecurityContext);
  const key = await decryptSecret(model.api_key_encrypted);
  if (!key) throw new Error("视觉模型 API Key 未配置");
  const content: Array<Record<string, unknown>> = [{ type: "text", text }];
  if (image) content.push({ type: "image_url", image_url: { url: `data:${image.mimeType};base64,${image.base64}`, detail: "high" } });
  const { response, data } = await fetchJsonLimited<{ choices?: Array<{ message?: { content?: string | Array<{ text?: string }> } }> }>(endpointUrl, {
    method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${key}` },
    body: JSON.stringify({
      model: model.model_name, temperature: 0,
      max_tokens: Math.min(boundedModelSetting(model.max_tokens, VISION_ANNOTATION_OUTPUT_TOKEN_MAX, 128, 1_600), outputTokenCap ?? VISION_ANNOTATION_OUTPUT_TOKEN_MAX),
      ...(disableVisionThinking(model) ? { thinking: { type: "disabled" } } : {}),
      messages: [{ role: "user", content }],
      response_format: { type: "json_schema", json_schema: { name: "market_sku_annotation", strict: true, schema: annotationJsonSchema(segments) } },
    }),
  }, Math.min(boundedModelSetting(model.timeout_ms, DEFAULT_MODEL_TIMEOUT_MS, 3_000, 120_000), VISION_ANNOTATION_TIMEOUT_MAX_MS));
  if (!response.ok) throw modelCallError("视觉", response.status, data);
  const contentValue = data?.choices?.[0]?.message?.content;
  return typeof contentValue === "string" ? contentValue : contentValue?.map((part) => part.text ?? "").join("") || "";
}

async function callAnthropicVision(model: ModelRow, text: string, segments: readonly string[], image: LoadedImage | null, outputTokenCap?: number) {
  const endpointSecurityContext = await loadAiEndpointSecurityContext();
  const endpointUrl = resolveAiModelEndpointUrl(model.base_url, "anthropic", endpointSecurityContext);
  const key = await decryptSecret(model.api_key_encrypted);
  if (!key) throw new Error("视觉模型 API Key 未配置");
  const content: Array<Record<string, unknown>> = [];
  if (image) content.push({ type: "image", source: { type: "base64", media_type: image.mimeType, data: image.base64 } });
  content.push({ type: "text", text });
  const { response, data } = await fetchJsonLimited<{ content?: Array<{ type?: string; name?: string; input?: unknown }> }>(endpointUrl, {
    method: "POST", headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01" },
    body: JSON.stringify({
      model: model.model_name, max_tokens: Math.min(boundedModelSetting(model.max_tokens, VISION_ANNOTATION_OUTPUT_TOKEN_MAX, 128, 1_600), outputTokenCap ?? VISION_ANNOTATION_OUTPUT_TOKEN_MAX), messages: [{ role: "user", content }],
      tools: [{ name: "submit_market_sku_annotation", description: "提交结构化识别结果", input_schema: annotationJsonSchema(segments) }],
      tool_choice: { type: "tool", name: "submit_market_sku_annotation" },
    }),
  }, Math.min(boundedModelSetting(model.timeout_ms, DEFAULT_MODEL_TIMEOUT_MS, 3_000, 120_000), VISION_ANNOTATION_TIMEOUT_MAX_MS));
  if (!response.ok) throw modelCallError("视觉", response.status, data);
  const tool = data?.content?.find((part) => part.type === "tool_use" && part.name === "submit_market_sku_annotation");
  if (!tool?.input) throw new Error("Anthropic 视觉模型没有返回结构化工具结果");
  return tool.input;
}

function disableVisionThinking(model: ModelRow) {
  // 标注是严格 JSON 的分类/抽取任务。豆包 Seed 的默认思考会明显增加首 token
  // 与完整响应时间，但不会改变可用枚举；显式关闭后仍由 schema 和人工复核兜底。
  return model.reasoning_mode === "disabled" || /^doubao-seed-/i.test(model.model_name.trim());
}

function annotationJsonSchema(segments: readonly string[]) {
  return { type: "object", additionalProperties: false, properties: {
    segment: { type: "string", enum: segments }, image_price_yuan: { anyOf: [{ type: "number", minimum: 0, maximum: 1_000_000 }, { type: "null" }] },
    price_type: { type: "string", enum: ["标准售价", "到手价", "券后价", "起售价", "价格区间", "定金", "分期金额", "最低规格价格", "无法判断"] },
    price_low_yuan: { anyOf: [{ type: "number", minimum: 0, maximum: 1_000_000 }, { type: "null" }] },
    price_high_yuan: { anyOf: [{ type: "number", minimum: 0, maximum: 1_000_000 }, { type: "null" }] },
    confidence: { type: "number", minimum: 0, maximum: 1 }, reason: { type: "string" },
  }, required: ["segment", "image_price_yuan", "price_type", "price_low_yuan", "price_high_yuan", "confidence", "reason"] };
}

async function fetchJsonLimited<T>(url: string, init: RequestInit, timeoutMs: number): Promise<{ response: Response; data: T | null }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, redirect: "manual", signal: controller.signal });
    if (response.status >= 300 && response.status < 400) throw new Error("模型接口禁止重定向");
    const declared = response.headers.get("content-length");
    if (declared && (!/^\d+$/.test(declared.trim()) || Number(declared) > MODEL_RESPONSE_MAX_BYTES)) throw new Error("模型响应超过安全上限");
    const bytes = await readBodyLimited(response, MODEL_RESPONSE_MAX_BYTES);
    let data: T | null = null;
    try { data = JSON.parse(new TextDecoder().decode(bytes)) as T; } catch {
      if (response.ok) throw new Error("模型响应不是有效 JSON");
    }
    return { response, data };
  } catch (error) {
    if (controller.signal.aborted) throw new Error("模型调用超时");
    if (error instanceof Error && /^(模型接口禁止重定向|模型响应超过安全上限|模型响应不是有效 JSON)/.test(error.message)) throw error;
    throw new Error("模型接口网络错误");
  } finally { clearTimeout(timer); }
}

async function readBodyLimited(response: Response, maxBytes: number): Promise<Uint8Array> {
  if (!response.body) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > maxBytes) throw new Error("模型响应超过安全上限");
    return bytes;
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    size += value.byteLength;
    if (size > maxBytes) { await reader.cancel().catch(() => undefined); throw new Error("模型响应超过安全上限"); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
