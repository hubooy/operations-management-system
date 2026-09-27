import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import {
  deleteAiModel,
  ensureAiAssistantSchema,
  listAiModels,
  testAiModelConnection,
  upsertAiModel,
  type AiModelInput,
} from "@/tests/legacy/ai/assistant-service";
import { getD1Database } from "@/lib/database/d1";
import { PublicApiError } from "@/lib/http/api-error";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  optionalAiId,
  optionalAiPositiveInteger,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

type JsonRecord = Record<string, unknown>;

function stringValue(payload: JsonRecord, key: string): string | undefined {
  const value = payload[key];
  return typeof value === "string" ? value : undefined;
}

function numberValue(payload: JsonRecord, key: string): number | undefined {
  const value = payload[key];
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new PublicApiError(400, "invalid_request", `${key}必须为 JSON 安全整数。`);
  }
  return value;
}

function modelInputFromPayload(payload: JsonRecord): AiModelInput | null {
  const id = optionalAiId(payload.id, "id");
  const name = stringValue(payload, "name");
  const protocol = stringValue(payload, "protocol");
  const modelType = stringValue(payload, "modelType");
  const modelName = stringValue(payload, "modelName");
  if (Object.hasOwn(payload, "baseUrl") && typeof payload.baseUrl !== "string") {
    throw new PublicApiError(400, "invalid_request", "baseUrl必须为字符串。");
  }
  const baseUrl = stringValue(payload, "baseUrl");
  if (!name || !protocol || !modelType || !modelName || (!id && !baseUrl)) return null;
  return {
    id,
    expectedVersion: numberValue(payload, "expectedVersion"),
    name,
    protocol: protocol as AiModelInput["protocol"],
    modelType: modelType as AiModelInput["modelType"],
    modelName,
    baseUrl,
    apiKey: stringValue(payload, "apiKey"),
    status: (stringValue(payload, "status") ?? "enabled") as AiModelInput["status"],
    isDefaultTextModel: payload.isDefaultTextModel === true,
    timeoutMs: numberValue(payload, "timeoutMs"),
    maxTokens: numberValue(payload, "maxTokens"),
    reasoningMode: stringValue(payload, "reasoningMode") as AiModelInput["reasoningMode"],
    temperatureMilli: numberValue(payload, "temperatureMilli"),
    maxToolRounds: numberValue(payload, "maxToolRounds"),
    maxTotalToolCalls: numberValue(payload, "maxTotalToolCalls"),
  };
}

export async function GET() {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "AI 模型配置");
    const db = getD1Database();
    await ensureAiAssistantSchema(db);
    return aiJsonResponse({ items: await listAiModels(db), principal });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取模型配置失败");
  }
}

export async function DELETE(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "AI 模型配置", "删除");
    const ids = new URL(request.url).searchParams.getAll("id");
    const id = requireAiId(ids.length === 1 ? ids[0] : undefined, "id");
    const expectedVersion = optionalAiPositiveInteger(new URL(request.url).searchParams, "expectedVersion");
    if (expectedVersion === null) {
      throw new PublicApiError(400, "invalid_request", "缺少 expectedVersion。");
    }
    const deleted = await deleteAiModel(id, expectedVersion, getD1Database());
    return aiJsonResponse({ ok: true, deleted });
  } catch (error) {
    return aiRouteErrorResponse(error, "删除模型失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "AI 模型配置", "修改");
    const parsed = await readAiJsonObject(request);
    const action = stringValue(parsed, "action");
    if (action !== undefined && action !== "test") {
      throw new PublicApiError(400, "invalid_request", "action 无效。");
    }
    if (action === "test") {
      const id = requireAiId(parsed.id, "id");
      return aiJsonResponse(await testAiModelConnection(id, getD1Database()));
    }
    const input = modelInputFromPayload(parsed);
    if (!input) throw new PublicApiError(400, "invalid_request", "模型信息不完整。");
    const item = await upsertAiModel(input, getD1Database());
    return aiJsonResponse({ item });
  } catch (error) {
    return aiRouteErrorResponse(error, "保存模型配置失败");
  }
}
