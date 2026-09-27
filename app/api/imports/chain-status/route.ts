import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, WORKFLOW_IMPORT_CHAIN_STATUS_PATH } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "自动导入运行状态");
    if (new URL(request.url).search) return Response.json({ error: "此接口不接受查询参数。" }, { status: 400, headers: { "cache-control": "no-store" } });
    const result = await createDjangoWorkflowService().requestJson(principal,
      { method: "GET", path: WORKFLOW_IMPORT_CHAIN_STATUS_PATH, service: "reader" }, { signal: request.signal });
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "无法核实今天的工作流状态。");
  }
}
