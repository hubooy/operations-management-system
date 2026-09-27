import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { isPublicAiPath, requestDjangoAi } from "@/lib/django/ai-service";
import { aiJsonResponse, aiRouteErrorResponse, readAiJsonObject, requireAiSameOriginWrite } from "@/app/api/ai/route-helpers";
import { PublicApiError } from "@/lib/http/api-error";
import { normalizeAiPageContext } from "@/lib/ai/page-context";
import { requestDjangoAiStream } from "@/lib/django/ai-stream";

export async function forwardAiRequest(request: Request) {
  try {
    const url = new URL(request.url);
    if (!isPublicAiPath(url.pathname)) throw new PublicApiError(404, "not_found", "AI 接口不存在。");
    const read = request.method === "GET";
    const datasetQuery = request.method === "POST" && /^\/api\/ai\/datasets\/[a-z][a-z0-9_]{0,63}\/query$/.test(url.pathname);
    if (!read) requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(read || datasetQuery ? undefined : ["admin", "operator", "analyst"]);
    if (/^\/api\/ai\/(?:business-plan|business-evidence|business-reports|market-v2-parked-reports|market-v2-cap-approvals)(?:\/|$)/.test(url.pathname)) {
      if (principal.role !== "admin") throw new PublicApiError(403, "access_denied", "经营分析仅允许管理员。");
      requireUnrestrictedDataScope(principal, "经营分析");
    }
    if (/^\/api\/ai\/market-v2-parked-reports(?:\/|$)/.test(url.pathname)) {
      const service = await import("@/lib/django/ai-service");
      if ((await service.aiEnvironment()).AI_MARKET_V2_PREVIEW_ENABLED !== "true")
        throw new PublicApiError(409, "conflict", "市场样本预览尚未启用。");
    }
    if (/^\/api\/ai\/market-v2-cap-approvals(?:\/|$)/.test(url.pathname)) {
      const service = await import("@/lib/django/ai-service");
      if ((await service.aiEnvironment()).AI_MARKET_V2_HUMAN_CAP_ENABLED !== "true")
        throw new PublicApiError(409, "conflict", "市场单报告费用上限审批尚未启用。");
    }
    if (/^\/api\/ai\/(?:report-library|prompt-settings|dingtalk-settings|dingtalk-schedules(?:\/run)?|models|channels|space\/(?:profiles|templates))$/.test(url.pathname)) {
      if (principal.role !== "admin") throw new PublicApiError(403, "access_denied", "AI 管理仅允许管理员。");
      requireUnrestrictedDataScope(principal, "AI 管理");
    }
    const payload = read || request.method === "DELETE" && !request.headers.get("content-type") ? undefined : await readAiJsonObject(request);
    if (url.pathname === "/api/ai/chat" && payload?.pageContext != null) {
      const context = normalizeAiPageContext(payload.pageContext);
      if (!context) throw new PublicApiError(400, "invalid_request", "AI 页面上下文无效。");
      payload.pageContext = context;
    }
    if (url.pathname === "/api/ai/chat" && request.method === "POST" && request.headers.get("accept")?.includes("text/event-stream")) {
      if (url.search || !payload || typeof payload.clientRequestId !== "string") throw new PublicApiError(400, "invalid_request", "AI 流式请求无效。");
      return await requestDjangoAiStream(principal, payload, { signal: request.signal, onCancel: () => {
        void requestDjangoAi(principal, { path: "/api/ai/chat/cancel", method: "POST", payload: { clientRequestId: payload.clientRequestId } }).catch(() => undefined);
      } });
    }
    let cancellation: Promise<unknown> | undefined;
    const abort = () => {
      if (url.pathname === "/api/ai/chat" && request.method === "POST" && typeof payload?.clientRequestId === "string") {
        cancellation = requestDjangoAi(principal, { path: "/api/ai/chat/cancel", method: "POST", payload: { clientRequestId: payload.clientRequestId } }).catch(() => undefined);
      }
    };
    request.signal.addEventListener("abort", abort, { once: true });
    try {
      const result = await requestDjangoAi<Record<string, unknown>>(principal, { path: url.pathname, method: request.method as "GET" | "POST" | "PUT" | "PATCH" | "DELETE", query: url.searchParams, payload,
        ...(url.pathname === "/api/ai/models" && request.method === "GET"
          ? { service: "writer" as const }
          : (url.pathname === "/api/ai/business-plan/preview" || /^\/api\/ai\/(?:reports|business-evidence)\/[A-Za-z0-9_-]{1,160}\/budget-preview$/.test(url.pathname)) && request.method === "POST"
            ? { service: "reader" as const }
            : {}) }, { signal: request.signal });
      const generatedContent = /^\/api\/ai\/(?:reports|space\/assets)\/[A-Za-z0-9_-]{1,160}\/content$/.test(url.pathname);
      if (url.pathname.startsWith("/api/ai/artifacts/") || generatedContent) {
        const file = result.data as { base64?: string; content?: string; mimeType: string; fileName: string };
        const bytes = file.base64 ? Uint8Array.from(atob(file.base64), c => c.charCodeAt(0)) : new TextEncoder().encode(file.content ?? "");
        return new Response(bytes, { headers: { "content-type": file.mimeType, "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(file.fileName)}`, "cache-control": "private, no-store", "x-content-type-options": "nosniff", "content-security-policy": "default-src 'none'; sandbox", ...(generatedContent ? { "x-ai-generated": "true", "x-ai-review-required": "true" } : {}) } });
      }
      return aiJsonResponse(result.data, { status: result.status, headers: { "x-ai-revision": result.revision } });
    } finally { request.signal.removeEventListener("abort", abort); if (cancellation) await cancellation; }
  } catch (error) { return aiRouteErrorResponse(error, "AI 服务请求失败"); }
}
