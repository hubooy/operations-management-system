import { requireAppPrincipal } from "@/lib/auth/authorization";
import { getAiAgentJob } from "@/tests/legacy/ai/agent-workflows";
import { getD1Database } from "@/lib/database/d1";
import { aiJsonResponse, aiRouteErrorResponse, requireAiId } from "@/app/api/ai/route-helpers";

export async function GET(_request: Request, context: { params: Promise<{ jobId: string }> }) {
  try {
    const principal = await requireAppPrincipal();
    const params = await context.params;
    const jobId = requireAiId(params.jobId, "jobId");
    return aiJsonResponse({ item: await getAiAgentJob(jobId, principal, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI Agent 任务失败");
  }
}
