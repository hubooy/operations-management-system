import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode, WORKFLOW_OPERATION_RECORDS_PATH } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { encodedWorkflowResource, workflowServiceResponse } from "@/lib/workflow/django-api";

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  try { const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); await getWorkflowBackendMode(); const { id } = await context.params;
    const path = `${WORKFLOW_OPERATION_RECORDS_PATH}/${encodedWorkflowResource(id)}/activity`;
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path, service: "reader", rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal }));
  } catch (error) { return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "读取运营记录活动失败。"); }
}
