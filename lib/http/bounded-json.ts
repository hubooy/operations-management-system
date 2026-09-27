import { PublicApiError } from "@/lib/http/api-error";

export const DEFAULT_JSON_BODY_BYTES_MAX = 256 * 1024;

/**
 * Reads one UTF-8 JSON object without trusting Content-Length. The streaming
 * byte counter remains authoritative when the header is missing or too small.
 */
export async function readBoundedJsonObject(
  request: Request,
  maximumBytes = DEFAULT_JSON_BODY_BYTES_MAX,
): Promise<Record<string, unknown>> {
  const text = await readBoundedUtf8Text(request, maximumBytes);
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new PublicApiError(400, "invalid_request", "请求数据格式无效。");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new PublicApiError(400, "invalid_request", "请求体必须是 JSON 对象。");
  }
  return parsed as Record<string, unknown>;
}

export async function readBoundedUtf8Text(request: Request, maximumBytes: number): Promise<string> {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) {
    throw new TypeError("maximumBytes must be a positive safe integer");
  }
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
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      totalBytes += value.byteLength;
      if (totalBytes > maximumBytes) {
        await reader.cancel("payload_too_large").catch(() => undefined);
        throw new PublicApiError(413, "payload_too_large", "请求数据超过允许大小。");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
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
