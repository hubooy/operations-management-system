import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { getSalesDataHealth } from "@/lib/sales/data-health";

export async function GET() {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "销售数据健康状态");
    const data = await getSalesDataHealth(principal);
    return Response.json(data, {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取销售数据健康状态失败。", {
      headers: { "cache-control": "no-store" },
    });
  }
}
