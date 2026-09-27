import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

type RouteContext = { params: Promise<{ projectId: string }> };
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function projectPath(projectId: string) {
  if (!UUID_RE.test(projectId)) throw new PublicApiError(400, "invalid_request", "新品项目标识无效。");
  return `/api/workflow/launch-projects/${projectId.toLowerCase()}`;
}

function requireDjangoMode() {
  return getWorkflowBackendMode();
}

function requireObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicApiError(400, "invalid_request", "新品项目更新内容必须是 JSON 对象。");
  }
  return value as Record<string, unknown>;
}

export async function GET(request: Request, context: RouteContext) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品上新");
    await requireDjangoMode();
    const path = projectPath((await context.params).projectId);
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取新品项目失败。");
  }
}

export async function PATCH(request: Request, context: RouteContext) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品上新", "修改");
    await requireDjangoMode();
    const path = projectPath((await context.params).projectId);
    const payload = requireObject(await request.json().catch(() => null));
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PATCH", path, service: "writer", payload },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "更新新品项目失败。");
  }
}

export async function DELETE(request: Request, context: RouteContext) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品上新", "修改");
    await requireDjangoMode();
    const path = projectPath((await context.params).projectId);
    const params = new URL(request.url).searchParams;
    const expectedVersion = params.get("expectedVersion");
    if (!expectedVersion || !/^[1-9]\d*$/.test(expectedVersion) || !Number.isSafeInteger(Number(expectedVersion))) {
      throw new PublicApiError(400, "invalid_request", "expectedVersion 必须为正整数。");
    }
    const query = new URLSearchParams({ expectedVersion }).toString();
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "DELETE", path, service: "writer", rawQuery: query },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "删除新品项目失败。");
  }
}
