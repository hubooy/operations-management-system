import { requireAppPrincipal } from "@/lib/auth/authorization";
import { reviewAiWorkflowNode } from "@/tests/legacy/ai/agent-workflows";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

export async function POST(
  request: Request,
  context: { params: Promise<{ runId: string; nodeKey: string }> },
) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const params = await context.params;
    const runId = requireAiId(params.runId, "runId");
    const nodeKey = requireAiId(params.nodeKey, "nodeKey");
    const body = await readAiJsonObject(request);
    return aiJsonResponse({ item: await reviewAiWorkflowNode(runId, nodeKey, body, principal, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "提交 AI 工作流人工复核失败");
  }
}
