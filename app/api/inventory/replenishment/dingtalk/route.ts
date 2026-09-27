import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoInventoryService, INVENTORY_REPLENISHMENT_DINGTALK_PATH } from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

function errorResponse(status: number, message: string) {
  return Response.json({ ok: false, message }, { status, headers: { "cache-control": "no-store" } });
}


export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划", "同步到钉钉");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body || Object.keys(body).length !== 1 || typeof body.id !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(body.id.trim())) {
      return errorResponse(400, "缺少有效的备货计划 ID");
    }
    const id = body.id.trim();
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "POST", path: INVENTORY_REPLENISHMENT_DINGTALK_PATH, service: "writer", payload: { id } },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      status: result.status,
      headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "创建钉钉备货计划失败。", { headers: { "cache-control": "no-store" } });
  }
}
