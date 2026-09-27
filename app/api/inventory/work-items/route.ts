import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  createInventoryWorkItem,
  InventoryWorkItemError,
  type InventoryWorkItemInput,
} from "@/lib/inventory/work-items";

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "库存执行事项", "创建");
    const body = await request.json().catch(() => null) as InventoryWorkItemInput | null;
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return Response.json({ ok: false, code: "invalid_request", message: "请求内容必须是 JSON 对象" }, {
        status: 400,
        headers: { "cache-control": "no-store" },
      });
    }
    const result = await createInventoryWorkItem(body, principal);
    return Response.json({ ok: true, ...result }, {
      status: result.created ? 201 : 200,
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof InventoryWorkItemError) {
      return Response.json({ ok: false, code: error.code, message: error.message }, {
        status: error.status,
        headers: { "cache-control": "no-store" },
      });
    }
    return safeApiErrorResponse(error, "创建库存执行事项失败。", { headers: { "cache-control": "no-store" } });
  }
}
