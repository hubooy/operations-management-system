import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoWorkflowService,
  getWorkflowBackendMode,
  WORKFLOW_NEW_PRODUCT_WEEKLY_REPORT_CONFIG_PATH,
} from "@/lib/django/workflow-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品销售跟进");
    await getWorkflowBackendMode();
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_NEW_PRODUCT_WEEKLY_REPORT_CONFIG_PATH, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取新品周报配置失败。");
  }
}

export async function PATCH(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品销售跟进", "修改周报配置");
    const payload = await request.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new PublicApiError(400, "invalid_request", "新品周报配置无效。");
    }
    await getWorkflowBackendMode();
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PATCH", path: WORKFLOW_NEW_PRODUCT_WEEKLY_REPORT_CONFIG_PATH, service: "writer", payload: payload as Record<string, unknown> },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-workflow-data-revision": result.revision } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "更新新品周报配置失败。");
  }
}
