import { requireAppPrincipal } from "@/lib/auth/authorization";
import { createAiSpaceJob, listAiSpaceJobs } from "@/tests/legacy/ai/space";
import { getD1Database } from "@/lib/database/d1";
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
    return aiJsonResponse(await listAiSpaceJobs({ page, pageSize }, principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "读取 AI 空间任务失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const body = await readAiJsonObject(request);
    const result = await createAiSpaceJob({
      clientRequestId: body.clientRequestId,
      scene: body.scene,
      templateId: body.templateId,
      modelProfileId: body.modelProfileId,
      productName: body.productName,
      brand: body.brand,
      sku: body.sku,
      sellingPoints: body.sellingPoints,
      additionalInstructions: body.additionalInstructions,
      count: body.count,
    }, principal, getD1Database());
    return aiJsonResponse(result, { status: result.replayed ? 200 : 201 });
  } catch (error) {
    return aiRouteErrorResponse(error, "创建 AI 空间任务失败");
  }
}
