import { authorizationErrorResponse } from "@/lib/auth/authorization";
import { AccessControlServiceError } from "@/lib/django/access-control-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

const MAX_BODY_BYTES = 64 * 1024;

export function accessControlErrorResponse(error: unknown, fallback: string): Response {
  const authorization = authorizationErrorResponse(error);
  if (authorization) return authorization;
  if (error instanceof AccessControlServiceError) {
    return Response.json(
      { error: error.message, code: error.code },
      { status: error.status, headers: { "cache-control": "no-store" } },
    );
  }
  return safeApiErrorResponse(error, fallback, { headers: { "cache-control": "no-store" } });
}

export function requireSameOriginWrite(request: Request): void {
  const fetchSite = request.headers.get("sec-fetch-site")?.trim().toLowerCase();
  if (fetchSite && fetchSite !== "same-origin") {
    throw new PublicApiError(403, "cross_site_request_rejected", "已拒绝跨站权限写入请求。");
  }
  const origin = request.headers.get("origin");
  if (!origin) {
    if (fetchSite === "same-origin") return;
    throw new PublicApiError(403, "cross_site_request_rejected", "权限写入请求缺少同源证明。");
  }
  try {
    const requestOrigin = new URL(request.url).origin;
    const supplied = new URL(origin);
    if (origin === "null" || origin !== supplied.origin || supplied.origin !== requestOrigin) {
      throw new Error("origin mismatch");
    }
  } catch {
    throw new PublicApiError(403, "cross_site_request_rejected", "权限写入请求 Origin 无效。");
  }
}

export async function readAccessControlJson(request: Request): Promise<Record<string, unknown>> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    throw new PublicApiError(415, "unsupported_media_type", "权限写入请求必须使用 application/json。" );
  }
  const declared = request.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_BODY_BYTES)) {
    throw new PublicApiError(413, "payload_too_large", "权限写入请求超过大小上限。" );
  }
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    if (reader) {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > MAX_BODY_BYTES) {
          await reader.cancel();
          throw new PublicApiError(413, "payload_too_large", "权限写入请求超过大小上限。");
        }
        chunks.push(next.value);
      }
    }
  } finally {
    reader?.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new PublicApiError(400, "invalid_request", "权限写入请求不是有效 JSON。" );
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new PublicApiError(400, "invalid_request", "权限写入请求必须是 JSON 对象。" );
  }
  return payload as Record<string, unknown>;
}

export function boundedAccessQuery(request: Request, allowed: readonly string[]): URLSearchParams {
  const source = new URL(request.url).searchParams;
  const result = new URLSearchParams();
  for (const [key, value] of source) {
    if (!allowed.includes(key) || value.length > 200 || result.getAll(key).length > 0) {
      throw new PublicApiError(400, "invalid_request", "权限查询参数无效。" );
    }
    result.set(key, value);
  }
  return result;
}
