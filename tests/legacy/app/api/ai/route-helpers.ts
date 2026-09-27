import { authorizationErrorResponse } from "@/lib/auth/authorization";
import {
  parsePositiveIntegerQuery,
  PublicApiError,
  safeApiErrorResponse,
} from "@/lib/http/api-error";

export const AI_ROUTE_NO_STORE_HEADERS = { "cache-control": "no-store" } as const;
export const AI_JSON_BODY_BYTES_MAX = 64 * 1024;

export type AiJsonRecord = Record<string, unknown>;

export function aiJsonResponse(body: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set("cache-control", "no-store");
  return Response.json(body, { ...init, headers });
}

export function aiRouteErrorResponse(error: unknown, fallback: string, extraHeaders: HeadersInit = {}): Response {
  const requestedHeaders = new Headers(extraHeaders);
  const auth = authorizationErrorResponse(error);
  if (auth) {
    const headers = new Headers(auth.headers);
    requestedHeaders.forEach((value, key) => headers.set(key, value));
    if (!headers.has("cache-control")) headers.set("cache-control", "no-store");
    return new Response(auth.body, {
      status: auth.status,
      statusText: auth.statusText,
      headers,
    });
  }
  if (!requestedHeaders.has("cache-control")) requestedHeaders.set("cache-control", "no-store");
  return safeApiErrorResponse(error, fallback, { headers: requestedHeaders });
}

export async function readAiJsonObject(
  request: Request,
  maximumBytes = AI_JSON_BODY_BYTES_MAX,
): Promise<AiJsonRecord> {
  requireAiSameOriginWrite(request);
  const mediaType = request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType !== "application/json") {
    throw new PublicApiError(415, "unsupported_media_type", "AI 写入接口只接受 application/json 请求。");
  }
  const text = await readAiBoundedText(request, maximumBytes);
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new PublicApiError(400, "invalid_request", "请求数据格式无效。");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new PublicApiError(400, "invalid_request", "请求数据格式无效。");
  }
  return parsed as AiJsonRecord;
}

/**
 * Browser write protection for authenticated and local-direct AI surfaces.
 * Cross-origin webhooks deliberately do not call this helper; they use their
 * own signature/decryption contract instead.
 */
export function requireAiSameOriginWrite(request: Request): void {
  const fetchSite = request.headers.get("sec-fetch-site")?.trim().toLowerCase();
  if (fetchSite && fetchSite !== "same-origin") {
    throw new PublicApiError(403, "cross_site_request_rejected", "已拒绝跨站 AI 写入请求。");
  }
  const origin = request.headers.get("origin");
  if (!origin) {
    if (fetchSite === "same-origin") return;
    throw new PublicApiError(403, "cross_site_request_rejected", "AI 写入请求缺少同源证明。");
  }
  let requestOrigin: string;
  let suppliedOrigin: string;
  try {
    requestOrigin = new URL(request.url).origin;
    suppliedOrigin = new URL(origin).origin;
  } catch {
    throw new PublicApiError(403, "cross_site_request_rejected", "AI 写入请求 Origin 无效。");
  }
  if (origin === "null" || origin !== suppliedOrigin || suppliedOrigin !== requestOrigin) {
    throw new PublicApiError(403, "cross_site_request_rejected", "已拒绝跨站 AI 写入请求。");
  }
}

export async function readAiBoundedText(request: Request, maximumBytes: number): Promise<string> {
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null) {
    if (!/^\d+$/.test(contentLength)) {
      throw new PublicApiError(400, "invalid_request", "Content-Length 格式无效。");
    }
    const declaredBytes = Number(contentLength);
    if (!Number.isSafeInteger(declaredBytes) || declaredBytes > maximumBytes) {
      throw new PublicApiError(413, "payload_too_large", "请求数据超过允许大小。");
    }
  }
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    totalBytes += value.byteLength;
    if (totalBytes > maximumBytes) {
      await reader.cancel().catch(() => undefined);
      throw new PublicApiError(413, "payload_too_large", "请求数据超过允许大小。");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new PublicApiError(400, "invalid_request", "请求数据必须使用 UTF-8 编码。");
  }
}

export function requireAiId(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^[a-zA-Z0-9_-]{1,160}$/.test(value)) {
    throw new PublicApiError(400, "invalid_request", `${field}格式无效。`);
  }
  return value;
}

export function optionalAiId(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  return requireAiId(value, field);
}

export function requireAiString(
  value: unknown,
  field: string,
  options: { maximumCharacters: number; maximumBytes?: number; allowEmpty?: boolean },
): string {
  if (typeof value !== "string") {
    throw new PublicApiError(400, "invalid_request", `${field}必须为字符串。`);
  }
  const normalized = value.trim();
  if (!options.allowEmpty && !normalized) {
    throw new PublicApiError(400, "invalid_request", `${field}不能为空。`);
  }
  if (normalized.length > options.maximumCharacters
    || new TextEncoder().encode(normalized).byteLength > (options.maximumBytes ?? options.maximumCharacters * 4)) {
    throw new PublicApiError(413, "payload_too_large", `${field}超过允许大小。`);
  }
  return normalized;
}

export function parseAiPositiveInteger(
  params: URLSearchParams,
  field: string,
  fallback: number,
  maximum: number,
): number {
  if (params.getAll(field).length > 1) {
    throw new PublicApiError(400, "invalid_request", `${field}不能重复。`);
  }
  return parsePositiveIntegerQuery(params.get(field), fallback, field, maximum);
}

export function optionalAiPositiveInteger(
  params: URLSearchParams,
  field: string,
  maximum = Number.MAX_SAFE_INTEGER,
): number | null {
  if (params.getAll(field).length > 1) {
    throw new PublicApiError(400, "invalid_request", `${field}不能重复。`);
  }
  const value = params.get(field);
  return value === null ? null : parsePositiveIntegerQuery(value, 1, field, maximum);
}
