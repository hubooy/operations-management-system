import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { ACCESS_CONTROL_ROLES_PATH, createDjangoAccessControlService } from "@/lib/django/access-control-service";
import { accessControlErrorResponse } from "../route-helpers";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "角色目录");
    const result = await createDjangoAccessControlService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: ACCESS_CONTROL_ROLES_PATH, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-access-control-revision": result.revision } });
  } catch (error) {
    return accessControlErrorResponse(error, "角色目录读取失败");
  }
}
