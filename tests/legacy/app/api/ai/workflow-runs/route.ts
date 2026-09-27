import { requireAppPrincipal } from "@/lib/auth/authorization";
import {
  createAiWorkflowRun,
  listAiWorkflowRuns,
} from "@/tests/legacy/ai/agent-workflows";
import { createCurrentAgentExecutorAdmission } from "@/tests/legacy/ai/agent-executor-admission";
import { getD1Database } from "@/lib/database/d1";
import { PublicApiError } from "@/lib/http/api-error";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  parseAiPositiveInteger,
  readAiJsonObject,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const params = new URL(request.url).searchParams;
    rejectUnknownListParams(params);
    const page = parseAiPositiveInteger(params, "page", 1, 10_000);
    const pageSize = parseAiPositiveInteger(params, "pageSize", 20, 50);
    return aiJsonResponse(await listAiWorkflowRuns({ page, pageSize }, principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 工作流失败");
  }
}

function rejectUnknownListParams(params: URLSearchParams) {
  for (const key of params.keys()) {
    if (key !== "page" && key !== "pageSize") {
      throw new PublicApiError(400, "invalid_request", `不支持查询参数 ${key}。`);
    }
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const body = await readAiJsonObject(request);
    rejectUnknownCreateKeys(body, ["clientRequestId", "name", "graph", "input", "dryRun", "modelId"]);
    const db = getD1Database();
    const admission = body.dryRun === true
      ? undefined
      : (await createCurrentAgentExecutorAdmission(
        principal,
        typeof body.modelId === "string" ? body.modelId : null,
        db,
      )).admission;
    const result = await createAiWorkflowRun({
      clientRequestId: body.clientRequestId,
      name: body.name,
      graph: body.graph,
      input: body.input,
      dryRun: body.dryRun,
    }, principal, db, { ...(admission ? { executorAdmission: admission } : {}) });
    return aiJsonResponse(result, { status: result.replayed ? 200 : 201 });
  } catch (error) {
    return aiRouteErrorResponse(error, "创建 AI 工作流失败");
  }
}

function rejectUnknownCreateKeys(body: Record<string, unknown>, allowed: readonly string[]) {
  for (const key of Object.keys(body)) {
    if (!allowed.includes(key)) throw new PublicApiError(400, "invalid_request", `不支持字段 ${key}。`);
  }
}
