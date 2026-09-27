import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoWorkflowService,
  getWorkflowBackendMode,
  WORKFLOW_LAUNCH_PROJECTS_PATH,
} from "@/lib/django/workflow-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

function requireObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicApiError(400, "invalid_request", "新品项目必须是有效的 JSON 对象。");
  }
  return value as Record<string, unknown>;
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品上新");
    await getWorkflowBackendMode();
    const params = new URL(request.url).searchParams;
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "reader", rawQuery: params.toString() },
      { signal: request.signal },
    );
    return Response.json(
      { ...result.data, structured: true, backendMode: "django" },
      { headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } },
    );
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取新品项目失败。");
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品上新", "修改");
    await getWorkflowBackendMode();
    const payload = requireObject(await request.json().catch(() => null));
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "POST", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "writer", payload },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      status: result.status,
      headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "创建新品项目失败。");
  }
}
