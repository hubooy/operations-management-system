export const DEFAULT_MODEL_RESPONSE_LIMIT_BYTES = 2 * 1024 * 1024;

export class BoundedFetchError extends Error {
  constructor(
    readonly code: "timeout" | "redirect" | "response_too_large" | "cancelled",
    message: string,
  ) {
    super(message);
    this.name = "BoundedFetchError";
  }
}

/** Fetches and consumes JSON while one timer covers headers and the full body. */
export async function fetchBoundedJson(input: {
  url: string;
  init: RequestInit;
  timeoutMs: number;
  maxBytes?: number;
  fetcher?: typeof fetch;
  signal?: AbortSignal;
}): Promise<{ response: Response; data: unknown; responseBytes: number }> {
  const controller = new AbortController();
  const externalSignal = input.signal ?? input.init.signal ?? undefined;
  const timeoutMs = Math.max(1, input.timeoutMs);
  const maxBytes = input.maxBytes ?? DEFAULT_MODEL_RESPONSE_LIMIT_BYTES;
  let timedOut = false;
  const abortFromExternal = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) abortFromExternal();
  else externalSignal?.addEventListener("abort", abortFromExternal, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new Error("response_timeout"));
  }, timeoutMs);
  try {
    const response = await (input.fetcher ?? fetch)(input.url, {
      ...input.init,
      redirect: "manual",
      signal: controller.signal,
    });
    if (response.status >= 300 && response.status < 400) {
      throw new BoundedFetchError("redirect", "接口地址返回了重定向，请填写最终的 HTTPS 接口地址");
    }
    const declaredLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
      throw new BoundedFetchError("response_too_large", `模型响应超过 ${maxBytes} 字节上限`);
    }
    const bytes = await readBoundedBody(response, maxBytes);
    if (bytes.byteLength === 0) return { response, data: null, responseBytes: 0 };
    try {
      return { response, data: JSON.parse(new TextDecoder().decode(bytes)) as unknown, responseBytes: bytes.byteLength };
    } catch {
      return { response, data: null, responseBytes: bytes.byteLength };
    }
  } catch (error) {
    if (error instanceof BoundedFetchError) throw error;
    if (controller.signal.aborted && !timedOut) throw new BoundedFetchError("cancelled", "模型请求已取消");
    if (controller.signal.aborted) throw new BoundedFetchError("timeout", "连接超时，请检查平台地址和网络");
    throw error;
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener("abort", abortFromExternal);
  }
}

async function readBoundedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel("response_too_large").catch(() => undefined);
        throw new BoundedFetchError("response_too_large", `模型响应超过 ${maxBytes} 字节上限`);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}
