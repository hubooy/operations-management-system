import { requireAppPrincipal } from "@/lib/auth/authorization";
import {
  createAiMemory,
  listAiMemories,
} from "@/tests/legacy/ai/memory";
import { PublicApiError } from "@/lib/http/api-error";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  parseAiPositiveInteger,
  readAiJsonObject,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

const LIST_QUERY_FIELDS = new Set(["page", "pageSize", "q", "kind"]);
const CREATE_BODY_FIELDS = new Set(["confirmed", "kind", "key", "content"]);

function rejectUnknownQuery(params: URLSearchParams): void {
  for (const field of params.keys()) {
    if (!LIST_QUERY_FIELDS.has(field)) {
      throw new PublicApiError(400, "invalid_request", `不支持查询参数 ${field}。`);
    }
    if (params.getAll(field).length > 1) {
      throw new PublicApiError(400, "invalid_request", `${field}不能重复。`);
    }
  }
}

function rejectUnknownBody(body: Record<string, unknown>, allowed: ReadonlySet<string>): void {
  const unexpected = Object.keys(body).find((field) => !allowed.has(field));
  if (unexpected) {
    throw new PublicApiError(400, "invalid_request", `请求字段 ${unexpected} 不受支持。`);
  }
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const params = new URL(request.url).searchParams;
    rejectUnknownQuery(params);
    return aiJsonResponse(await listAiMemories({
      page: parseAiPositiveInteger(params, "page", 1, 10_000),
      pageSize: parseAiPositiveInteger(params, "pageSize", 20, 50),
      q: params.get("q") ?? undefined,
      kind: params.get("kind") ?? undefined,
    }, principal, getD1Database()));
  } catch (error) {
    return aiRouteErrorResponse(error, "读取全局记忆失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const body = await readAiJsonObject(request);
    rejectUnknownBody(body, CREATE_BODY_FIELDS);
    const result = await createAiMemory({
      confirmed: body.confirmed,
      kind: body.kind,
      key: body.key,
      content: body.content,
      surface: "management_ui",
      requestId: request.headers.get("x-request-id"),
    }, principal, getD1Database());
    return aiJsonResponse(result, { status: result.created ? 201 : 200 });
  } catch (error) {
    return aiRouteErrorResponse(error, "保存全局记忆失败");
  }
}
