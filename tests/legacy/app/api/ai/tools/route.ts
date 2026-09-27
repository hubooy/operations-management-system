import { requireAppPrincipal } from "@/lib/auth/authorization";
import { getVisibleToolCatalog } from "@/lib/ai/tool-registry";
import { aiJsonResponse, aiRouteErrorResponse } from "@/app/api/ai/route-helpers";

export async function GET() {
  try {
    const principal = await requireAppPrincipal();
    const items = getVisibleToolCatalog(principal, "ai_chat");
    return aiJsonResponse({ items, count: items.length });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 工具清单失败");
  }
}
