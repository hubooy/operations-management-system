import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { encodedWorkflowResource, requireWorkflowJsonObject, workflowServiceResponse } from "@/lib/workflow/django-api";

type Context = { params: Promise<{ taskId: string }> };
const pathFor = (taskId: string) => `/api/workflow/tasks/${encodedWorkflowResource(taskId)}/comments`;
const errorResponse = (error: unknown, fallback: string) => authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);

export async function GET(request: Request, context: Context) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项评论");
    await getWorkflowBackendMode(); const { taskId } = await context.params;
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path: pathFor(taskId), service: "reader" }, { signal: request.signal });
    return workflowServiceResponse(result);
  } catch (error) { return errorResponse(error, "读取评论失败。"); }
}

export async function POST(request: Request, context: Context) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项评论", "修改");
    await getWorkflowBackendMode(); const { taskId } = await context.params;
    const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "评论内容必须是有效的 JSON 对象。");
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "POST", path: pathFor(taskId), service: "writer", payload }, { signal: request.signal });
    return workflowServiceResponse(result);
  } catch (error) { return errorResponse(error, "保存评论失败。"); }
}
