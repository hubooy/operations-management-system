import { requireAppPrincipal } from "@/lib/auth/authorization";
import {
  archiveAiMemory,
  getAiMemory,
  updateAiMemory,
} from "@/tests/legacy/ai/memory";
import { PublicApiError } from "@/lib/http/api-error";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

const UPDATE_BODY_FIELDS = new Set(["confirmed", "expectedVersion", "key", "content"]);
const ARCHIVE_BODY_FIELDS = new Set(["confirmed", "expectedVersion"]);

function rejectUnknownBody(body: Record<string, unknown>, allowed: ReadonlySet<string>): void {
  const unexpected = Object.keys(body).find((field) => !allowed.has(field));
  if (unexpected) {
    throw new PublicApiError(400, "invalid_request", `请求字段 ${unexpected} 不受支持。`);
  }
}

async function memoryIdFrom(context: { params: Promise<{ memoryId: string }> }): Promise<string> {
  const params = await context.params;
  return requireAiId(params.memoryId, "memoryId");
}

function rejectQuery(request: Request): void {
  if ([...new URL(request.url).searchParams.keys()].length > 0) {
    throw new PublicApiError(400, "invalid_request", "该接口不接受查询参数。");
  }
}

export async function GET(
  request: Request,
  context: { params: Promise<{ memoryId: string }> },
) {
  try {
    rejectQuery(request);
    const principal = await requireAppPrincipal();
    const memoryId = await memoryIdFrom(context);
    return aiJsonResponse({ item: await getAiMemory(memoryId, principal, getD1Database()) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取全局记忆失败");
  }
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ memoryId: string }> },
) {
  try {
    requireAiSameOriginWrite(request);
    rejectQuery(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const memoryId = await memoryIdFrom(context);
    const body = await readAiJsonObject(request);
    rejectUnknownBody(body, UPDATE_BODY_FIELDS);
    return aiJsonResponse(await updateAiMemory(memoryId, {
      confirmed: body.confirmed,
      expectedVersion: body.expectedVersion,
      key: body.key,
      content: body.content,
      surface: "management_ui",
      requestId: request.headers.get("x-request-id"),
    }, principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "更新全局记忆失败");
  }
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ memoryId: string }> },
) {
  try {
    requireAiSameOriginWrite(request);
    rejectQuery(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const memoryId = await memoryIdFrom(context);
    const body = await readAiJsonObject(request);
    rejectUnknownBody(body, ARCHIVE_BODY_FIELDS);
    return aiJsonResponse(await archiveAiMemory(memoryId, {
      confirmed: body.confirmed,
      expectedVersion: body.expectedVersion,
      surface: "management_ui",
      requestId: request.headers.get("x-request-id"),
    }, principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "归档全局记忆失败");
  }
}
