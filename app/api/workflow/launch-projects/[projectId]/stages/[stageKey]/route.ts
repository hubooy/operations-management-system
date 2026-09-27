import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

type RouteContext = { params: Promise<{ projectId: string; stageKey: string }> };
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const STAGES = new Set(["modeling", "pricing", "image", "video", "listing", "stocking", "review"]);

export async function PATCH(request: Request, context: RouteContext) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品上新", "修改");
    await getWorkflowBackendMode();
    const { projectId, stageKey } = await context.params;
    if (!UUID_RE.test(projectId) || !STAGES.has(stageKey)) {
      throw new PublicApiError(400, "invalid_request", "新品项目或阶段标识无效。");
    }
    const payload = await request.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new PublicApiError(400, "invalid_request", "阶段更新内容必须是 JSON 对象。");
    }
    const path = `/api/workflow/launch-projects/${projectId.toLowerCase()}/stages/${stageKey}`;
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PATCH", path, service: "writer", payload: payload as Record<string, unknown> },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "更新新品阶段失败。");
  }
}
