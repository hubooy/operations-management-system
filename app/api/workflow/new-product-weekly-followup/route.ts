import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoWorkflowService,
  getWorkflowBackendMode,
  WORKFLOW_NEW_PRODUCT_WEEKLY_FOLLOWUP_PATH,
} from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品销售跟进");
    await getWorkflowBackendMode();
    const rawQuery = new URL(request.url).searchParams.toString();
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_NEW_PRODUCT_WEEKLY_FOLLOWUP_PATH, service: "reader", rawQuery },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取新品销售周报失败。");
  }
}
