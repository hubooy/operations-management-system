import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode, WORKFLOW_TEMPLATES_PATH } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { requireWorkflowJsonObject, workflowServiceResponse } from "@/lib/workflow/django-api";

const routeError = (error: unknown, fallback: string) => authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);
async function authorizedReadPrincipal() {
  const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
  requireUnrestrictedDataScope(principal, "工作事项模板", "查看"); await getWorkflowBackendMode(); return principal;
}
async function authorizedWritePrincipal() {
  const principal = await requireAppPrincipal(["operator", "admin"]);
  requireUnrestrictedDataScope(principal, "工作事项模板", "修改"); await getWorkflowBackendMode(); return principal;
}
export async function GET(request: Request) {
  try { const actor = await authorizedReadPrincipal(); return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(actor, { method: "GET", path: WORKFLOW_TEMPLATES_PATH, service: "reader", rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal })); }
  catch (error) { return routeError(error, "读取模板失败。"); }
}
export async function POST(request: Request) {
  try { const actor = await authorizedWritePrincipal(); const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "模板必须是有效的 JSON 对象。"); return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(actor, { method: "POST", path: WORKFLOW_TEMPLATES_PATH, service: "writer", payload }, { signal: request.signal })); }
  catch (error) { return routeError(error, "保存模板失败。"); }
}
export async function PATCH(request: Request) {
  try { const actor = await authorizedWritePrincipal(); const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "模板必须是有效的 JSON 对象。"); return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(actor, { method: "PATCH", path: WORKFLOW_TEMPLATES_PATH, service: "writer", payload, rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal })); }
  catch (error) { return routeError(error, "更新模板失败。"); }
}
export async function DELETE(request: Request) {
  try { const actor = await authorizedWritePrincipal(); return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(actor, { method: "DELETE", path: WORKFLOW_TEMPLATES_PATH, service: "writer", rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal })); }
  catch (error) { return routeError(error, "删除模板失败。"); }
}
