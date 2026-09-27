import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_WAREHOUSE_MAPPINGS_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "系统设置");
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_WAREHOUSE_MAPPINGS_PATH, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取仓库映射失败", { headers: { "cache-control": "no-store" } });
  }
}

export async function PUT(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "系统设置", "修改仓库映射");
    const payload = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!payload || Array.isArray(payload)) {
      return Response.json({ error: "仓库映射更新内容不能为空" }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PUT", path: INVENTORY_WAREHOUSE_MAPPINGS_PATH, service: "writer", payload },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      status: result.status,
      headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "保存仓库映射失败", { headers: { "cache-control": "no-store" } });
  }
}
