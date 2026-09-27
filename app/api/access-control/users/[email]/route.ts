import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { ACCESS_CONTROL_USERS_PATH, createDjangoAccessControlService } from "@/lib/django/access-control-service";
import { accessControlErrorResponse, readAccessControlJson, requireSameOriginWrite } from "../../route-helpers";

export async function PUT(request: Request, context: { params: Promise<{ email: string }> }) {
  try {
    requireSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "用户权限", "修改");
    const { email } = await context.params;
    const normalized = email.trim().toLowerCase();
    if (!normalized || normalized.length > 320 || normalized.includes("/")) {
      return Response.json({ error: "用户邮箱无效", code: "invalid_request" }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    const payload = await readAccessControlJson(request);
    payload.email = normalized;
    const result = await createDjangoAccessControlService().request<Record<string, unknown>>(
      principal,
      { method: "PUT", path: ACCESS_CONTROL_USERS_PATH, payload, service: "writer" },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-access-control-revision": result.revision } });
  } catch (error) {
    return accessControlErrorResponse(error, "用户权限保存失败");
  }
}
