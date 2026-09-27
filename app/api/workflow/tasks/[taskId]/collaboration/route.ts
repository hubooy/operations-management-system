import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { encodedWorkflowResource, workflowServiceResponse } from "@/lib/workflow/django-api";

export async function GET(request: Request, context: { params: Promise<{ taskId: string }> }) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项协作信息");
    await getWorkflowBackendMode(); const { taskId } = await context.params;
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, {
      method: "GET", path: `/api/workflow/tasks/${encodedWorkflowResource(taskId)}/collaboration`, service: "reader",
    }, { signal: request.signal });
    return workflowServiceResponse(result);
  } catch (error) { return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "读取工作事项协作信息失败。"); }
}
