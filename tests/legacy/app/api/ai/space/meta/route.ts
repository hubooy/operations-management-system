import { requireAppPrincipal } from "@/lib/auth/authorization";
import { getAiSpaceMeta } from "@/tests/legacy/ai/space";
import { getD1Database } from "@/lib/database/d1";
import { aiJsonResponse, aiRouteErrorResponse } from "@/app/api/ai/route-helpers";

export async function GET() {
  try {
    const principal = await requireAppPrincipal();
    return aiJsonResponse(await getAiSpaceMeta(principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 空间配置失败");
  }
}
