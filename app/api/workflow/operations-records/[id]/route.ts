import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode, WORKFLOW_OPERATION_RECORDS_PATH } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { encodedWorkflowResource, requireWorkflowJsonObject, workflowServiceResponse } from "@/lib/workflow/django-api";

type Context = { params: Promise<{ id: string }> };
const routeError = (error: unknown, fallback: string) => authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);
const pathFor = (id: string) => `${WORKFLOW_OPERATION_RECORDS_PATH}/${encodedWorkflowResource(id)}`;
export async function GET(request: Request, context: Context) {
  try { const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); await getWorkflowBackendMode(); const { id } = await context.params;
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path: pathFor(id), service: "reader" }, { signal: request.signal }));
  } catch (error) { return routeError(error, "读取运营记录失败。"); }
}
export async function PATCH(request: Request, context: Context) {
  try { const principal = await requireAppPrincipal(["operator", "admin"]); await getWorkflowBackendMode(); const { id } = await context.params; const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "缺少可更新的运营记录字段。");
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "PATCH", path: pathFor(id), service: "writer", payload }, { signal: request.signal }));
  } catch (error) { return routeError(error, "更新运营记录失败。"); }
}
export async function DELETE(request: Request, context: Context) {
  try { const principal = await requireAppPrincipal(["operator", "admin"]); await getWorkflowBackendMode(); const { id } = await context.params;
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "DELETE", path: pathFor(id), service: "writer", rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal }));
  } catch (error) { return routeError(error, "删除运营记录失败。"); }
}
