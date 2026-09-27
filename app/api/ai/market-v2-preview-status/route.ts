import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { aiJsonResponse, aiRouteErrorResponse } from "@/app/api/ai/route-helpers";
import { aiEnvironment } from "@/lib/django/ai-service";
import { PublicApiError } from "@/lib/http/api-error";

export async function GET() {
  try {
    const principal = await requireAppPrincipal();
    if (principal.role !== "admin") throw new PublicApiError(403, "access_denied", "经营分析仅允许管理员。");
    requireUnrestrictedDataScope(principal, "经营分析");
    const enabled = (await aiEnvironment()).AI_MARKET_V2_PREVIEW_ENABLED === "true";
    return aiJsonResponse({ schemaVersion: "business-market-v2-preview-status-v1", enabled });
  } catch (error) { return aiRouteErrorResponse(error, "市场样本预览状态读取失败"); }
}
