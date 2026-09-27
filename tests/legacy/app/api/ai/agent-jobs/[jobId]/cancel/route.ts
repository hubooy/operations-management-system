import { requireAppPrincipal } from "@/lib/auth/authorization";
import { cancelAiAgentJob, requireAiExpectedVersionBody } from "@/tests/legacy/ai/agent-workflows";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

export async function POST(request: Request, context: { params: Promise<{ jobId: string }> }) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const params = await context.params;
    const jobId = requireAiId(params.jobId, "jobId");
    const expectedVersion = requireAiExpectedVersionBody(await readAiJsonObject(request));
    return aiJsonResponse({ item: await cancelAiAgentJob(jobId, expectedVersion, principal, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "取消 AI Agent 任务失败");
  }
}
