import { AI_CHAT_RELAY_TIMEOUT_MS } from "@/lib/ai/model-generation";
import type { AppPrincipal } from "@/lib/auth/authorization";
import { aiEnvironment, aiHeaders } from "./ai-service";
import { PublicApiError } from "@/lib/http/api-error";

export async function requestDjangoAiStream(principal: AppPrincipal, payload: Record<string, unknown>, options: {
  signal?: AbortSignal; environment?: Record<string, string | undefined>; fetchImpl?: typeof fetch; onCancel?: () => void;
} = {}): Promise<Response> {
  const unavailable = () => new PublicApiError(503, "service_unavailable", "AI 流式连接中断，请核对原请求结果。");
  const environment = options.environment ?? await aiEnvironment();
  let base: URL;
  try { base = new URL(environment.TERUISI_DJANGO_AI_WRITER_BASE_URL ?? ""); } catch { throw unavailable(); }
  if (!/^https?:$/.test(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/"
    || base.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(base.hostname)
    || environment.TERUISI_DJANGO_AI_READER_BASE_URL === environment.TERUISI_DJANGO_AI_WRITER_BASE_URL) throw unavailable();
  const body = JSON.stringify(payload);
  if (new TextEncoder().encode(body).length > 1024 * 1024) throw new PublicApiError(413, "payload_too_large", "AI 请求超过传输上限。");
  const headers = await aiHeaders({ secret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "", principal, method: "POST", path: "/api/ai/chat", query: "", body, requestId: crypto.randomUUID() });
  headers.set("accept", "text/event-stream");
  const controller = new AbortController(); let cancelled = false;
  const abort = () => { controller.abort(); if (!cancelled) { cancelled = true; options.onCancel?.(); } };
  const timer = setTimeout(abort, AI_CHAT_RELAY_TIMEOUT_MS);
  const cleanup = () => { clearTimeout(timer); options.signal?.removeEventListener("abort", abort); };
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) { abort(); cleanup(); throw unavailable(); }
  let upstream: Response;
  try {
    upstream = await (options.fetchImpl ?? fetch)(new URL("/api/ai/chat", base), { method: "POST", body, headers, cache: "no-store", redirect: "manual", signal: controller.signal });
    if (!upstream.ok && upstream.body && /application\/json/i.test(upstream.headers.get("content-type") ?? "") && [400, 401, 403, 404, 409, 413, 429, 499, 503].includes(upstream.status)) {
      const errorReader = upstream.body.getReader(); const decoder = new TextDecoder(); let text = ""; let size = 0;
      try {
        while (true) {
          const part = await errorReader.read(); if (part.done) break;
          size += part.value.byteLength; if (size > 16384) throw unavailable();
          text += decoder.decode(part.value, { stream: true });
        }
        text += decoder.decode();
        const parsed = JSON.parse(text) as { error?: string; code?: string };
        const codes = new Set(["invalid_request", "access_denied", "not_found", "conflict", "rate_limited", "ai_chat_not_dispatched", "ai_chat_result_unknown", "ai_request_cancelled", "service_unavailable"]);
        if (!codes.has(parsed.code ?? "") || typeof parsed.error !== "string") throw unavailable();
        throw new PublicApiError((upstream.status === 401 ? 403 : upstream.status) as PublicApiError["status"], parsed.code as PublicApiError["code"], parsed.error);
      } finally { await errorReader.cancel().catch(() => undefined); errorReader.releaseLock(); }
    }
    if (!upstream.ok || !upstream.body || !/^text\/event-stream\b/i.test(upstream.headers.get("content-type") ?? "") || !/^(?:0|[1-9]\d{0,18})$/.test(upstream.headers.get("x-ai-revision") ?? "")) {
      await upstream.body?.cancel();
      throw unavailable();
    }
  } catch (error) { cleanup(); controller.abort(); if (error instanceof PublicApiError) throw error; throw unavailable(); }
  const reader = upstream.body!.getReader(); let bytes = 0;
  const bodyStream = new ReadableStream<Uint8Array>({
    async pull(output) {
      try {
        const part = await reader.read();
        if (part.done) { cleanup(); reader.releaseLock(); output.close(); return; }
        bytes += part.value.byteLength;
        if (bytes > 32 * 1024 * 1024) throw unavailable();
        output.enqueue(part.value);
      } catch { abort(); cleanup(); await reader.cancel().catch(() => undefined); output.error(unavailable()); }
    },
    async cancel() { abort(); cleanup(); await reader.cancel().catch(() => undefined); },
  });
  return new Response(bodyStream, { headers: { "content-type": "text/event-stream; charset=utf-8", "cache-control": "private, no-store, no-transform", "x-content-type-options": "nosniff", "x-accel-buffering": "no", "x-ai-revision": upstream.headers.get("x-ai-revision")! } });
}
