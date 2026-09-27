import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_SETTINGS_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import type { OperatingSettings } from "@/lib/settings/service";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "系统设置");
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_SETTINGS_PATH, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取系统设置失败", { headers: { "cache-control": "no-store" } });
  }
}
export async function PUT(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "系统设置", "修改");
    const payload = await request.json().catch(() => null) as Partial<OperatingSettings> | null;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return Response.json({ error: "设置内容不能为空" }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PUT", path: INVENTORY_SETTINGS_PATH, service: "writer", payload },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "保存系统设置失败", { headers: { "cache-control": "no-store" } });
  }
}
