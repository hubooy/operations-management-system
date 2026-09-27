import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { ACCESS_CONTROL_USERS_PATH, createDjangoAccessControlService } from "@/lib/django/access-control-service";
import { accessControlErrorResponse, boundedAccessQuery, readAccessControlJson, requireSameOriginWrite } from "../route-helpers";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "用户权限");
    const query = boundedAccessQuery(request, ["page", "pageSize", "query", "status", "role"]);
    const result = await createDjangoAccessControlService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: ACCESS_CONTROL_USERS_PATH, query, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-access-control-revision": result.revision } });
  } catch (error) {
    return accessControlErrorResponse(error, "用户权限读取失败");
  }
}

export async function POST(request: Request) {
  try {
    requireSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "用户权限", "修改");
    const payload = await readAccessControlJson(request);
    const result = await createDjangoAccessControlService().request<Record<string, unknown>>(
      principal,
      { method: "POST", path: ACCESS_CONTROL_USERS_PATH, payload, service: "writer" },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-access-control-revision": result.revision } });
  } catch (error) {
    return accessControlErrorResponse(error, "用户权限创建失败");
  }
}
