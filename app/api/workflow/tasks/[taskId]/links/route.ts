import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { encodedWorkflowResource, requireWorkflowJsonObject, workflowServiceResponse } from "@/lib/workflow/django-api";

type Context = { params: Promise<{ taskId: string }> };
const pathFor = (taskId: string) => `/api/workflow/tasks/${encodedWorkflowResource(taskId)}/links`;
const routeError = (error: unknown, fallback: string) => authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);

export async function GET(request: Request, context: Context) {
  try { const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项业务关联"); await getWorkflowBackendMode(); const { taskId } = await context.params;
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path: pathFor(taskId), service: "reader" }, { signal: request.signal }));
  } catch (error) { return routeError(error, "读取业务关联失败。"); }
}
export async function POST(request: Request, context: Context) {
  try { const principal = await requireAppPrincipal(["operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项业务关联", "修改"); await getWorkflowBackendMode(); const { taskId } = await context.params;
    const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "业务关联必须是有效的 JSON 对象。");
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "POST", path: pathFor(taskId), service: "writer", payload }, { signal: request.signal }));
  } catch (error) { return routeError(error, "保存业务关联失败。"); }
}
export async function DELETE(request: Request, context: Context) {
  try { const principal = await requireAppPrincipal(["operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项业务关联", "修改"); await getWorkflowBackendMode(); const { taskId } = await context.params;
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "DELETE", path: pathFor(taskId), service: "writer", rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal }));
  } catch (error) { return routeError(error, "删除业务关联失败。"); }
}
