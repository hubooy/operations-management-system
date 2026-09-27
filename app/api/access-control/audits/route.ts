import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { ACCESS_CONTROL_AUDITS_PATH, createDjangoAccessControlService } from "@/lib/django/access-control-service";
import { accessControlErrorResponse, boundedAccessQuery } from "../route-helpers";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "权限审计");
    const query = boundedAccessQuery(request, ["page", "pageSize", "targetEmail", "action"]);
    const result = await createDjangoAccessControlService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: ACCESS_CONTROL_AUDITS_PATH, query, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-access-control-revision": result.revision } });
  } catch (error) {
    return accessControlErrorResponse(error, "权限审计读取失败");
  }
}
