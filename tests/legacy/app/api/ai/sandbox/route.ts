import { requireAppPrincipal } from "@/lib/auth/authorization";
import {
  describeAiAnalysisDatasets,
  listAiAnalysisRuns,
} from "@/tests/legacy/ai/analysis-sandbox";
import { executeRegisteredToolCall } from "@/lib/ai/tool-registry";
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
    const page = parseAiPositiveInteger(params, "page", 1, 10_000);
    const pageSize = parseAiPositiveInteger(params, "pageSize", 20, 50);
    const history = await listAiAnalysisRuns(principal, { page, pageSize });
    return aiJsonResponse({ ...describeAiAnalysisDatasets(), history });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 分析沙箱失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const body = await readAiJsonObject(request);
    const requestId = normalizeRequestId(request.headers.get("x-request-id"));
    const result = await executeRegisteredToolCall("run_analysis_plan", body, {
      principal,
      requestId,
      surface: "ai_sandbox",
      signal: request.signal,
    });
    if (result.ok) return aiJsonResponse(result.data, { status: 201 });
    return aiJsonResponse({ error: result.error.message, code: result.error.code }, {
      status: analysisErrorStatus(result.error.code),
    });
  } catch (error) {
    return aiRouteErrorResponse(error, "运行 AI 分析计划失败");
  }
}

function normalizeRequestId(value: string | null) {
  return value && /^[a-zA-Z0-9_.:-]{1,160}$/.test(value)
    ? value
    : `ai-sandbox-${crypto.randomUUID()}`;
}

function analysisErrorStatus(code: string): 400 | 403 | 409 | 422 | 503 {
  if (code === "invalid_arguments" || code === "invalid_analysis_plan" || code === "invalid_analysis_query") return 400;
  if (code === "forbidden") return 403;
  if (code === "analysis_audit_unavailable" || code === "audit_unavailable") return 503;
  if (code === "tool_cancelled" || code === "tool_timeout") return 409;
  return 422;
}
