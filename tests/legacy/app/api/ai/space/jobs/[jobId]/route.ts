import { requireAppPrincipal } from "@/lib/auth/authorization";
import { getAiSpaceJob } from "@/tests/legacy/ai/space";
import { getD1Database } from "@/lib/database/d1";
import { aiJsonResponse, aiRouteErrorResponse, requireAiId } from "@/app/api/ai/route-helpers";

export async function GET(_request: Request, context: { params: Promise<{ jobId: string }> }) {
  try {
    const principal = await requireAppPrincipal();
    const params = await context.params;
    const id = requireAiId(params.jobId, "jobId");
    return aiJsonResponse({ item: await getAiSpaceJob(id, principal, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 空间任务失败");
  }
}
