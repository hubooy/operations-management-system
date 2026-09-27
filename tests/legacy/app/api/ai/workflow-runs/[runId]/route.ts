import { requireAppPrincipal } from "@/lib/auth/authorization";
import { getAiWorkflowRun } from "@/tests/legacy/ai/agent-workflows";
import { getD1Database } from "@/lib/database/d1";
import { aiJsonResponse, aiRouteErrorResponse, requireAiId } from "@/app/api/ai/route-helpers";

export async function GET(_request: Request, context: { params: Promise<{ runId: string }> }) {
  try {
    const principal = await requireAppPrincipal();
    const params = await context.params;
    const runId = requireAiId(params.runId, "runId");
    return aiJsonResponse({ item: await getAiWorkflowRun(runId, principal, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 工作流失败");
  }
}
