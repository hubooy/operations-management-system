import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode, WORKFLOW_OPERATION_RECORDS_PATH } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { requireWorkflowJsonObject, workflowServiceResponse } from "@/lib/workflow/django-api";

const routeError = (error: unknown, fallback: string) => authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);
export async function GET(request: Request) {
  try { const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); await getWorkflowBackendMode();
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path: WORKFLOW_OPERATION_RECORDS_PATH, service: "reader", rawQuery: new URL(request.url).searchParams.toString() }, { signal: request.signal }));
  } catch (error) { return routeError(error, "读取运营记录失败。"); }
}
export async function POST(request: Request) {
  try { const principal = await requireAppPrincipal(["operator", "admin"]); await getWorkflowBackendMode(); const payload = requireWorkflowJsonObject(await request.json().catch(() => null), "运营记录必须是有效的 JSON 对象。");
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "POST", path: WORKFLOW_OPERATION_RECORDS_PATH, service: "writer", payload }, { signal: request.signal }));
  } catch (error) { return routeError(error, "保存运营记录失败。"); }
}
