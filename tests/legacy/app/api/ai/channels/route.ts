import { requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import {
  deleteAiChannel,
  ensureAiAssistantSchema,
  listAiChannels,
  sendAiChannelText,
  testAiChannelConnection,
  upsertAiChannel,
  type AiChannelInput,
} from "@/tests/legacy/ai/assistant-service";
import { getD1Database } from "@/lib/database/d1";
import { PublicApiError } from "@/lib/http/api-error";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  optionalAiId,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
  requireAiString,
} from "@/app/api/ai/route-helpers";

type JsonRecord = Record<string, unknown>;

function stringValue(payload: JsonRecord, key: string): string | undefined {
  const value = payload[key];
  return typeof value === "string" ? value : undefined;
}

function channelInputFromPayload(payload: JsonRecord): AiChannelInput | null {
  const name = stringValue(payload, "name");
  const kind = stringValue(payload, "kind");
  if (!name || !kind) return null;
  return {
    id: optionalAiId(payload.id, "id"),
    name,
    kind: kind as AiChannelInput["kind"],
    status: (stringValue(payload, "status") ?? "enabled") as AiChannelInput["status"],
    sendEnabled: payload.sendEnabled === true,
    callbackEnabled: payload.callbackEnabled === true,
    webhookUrl: stringValue(payload, "webhookUrl"),
    callbackToken: stringValue(payload, "callbackToken"),
    aesKey: stringValue(payload, "aesKey"),
    receiverId: stringValue(payload, "receiverId"),
  };
}

export async function GET() {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "AI 渠道配置");
    const db = getD1Database();
    await ensureAiAssistantSchema(db);
    return aiJsonResponse({ items: await listAiChannels(db) });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取渠道配置失败");
  }
}

export async function DELETE(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "AI 渠道配置", "删除");
    const ids = new URL(request.url).searchParams.getAll("id");
    const id = requireAiId(ids.length === 1 ? ids[0] : undefined, "id");
    const deleted = await deleteAiChannel(id, getD1Database());
    return aiJsonResponse({ ok: true, deleted });
  } catch (error) {
    return aiRouteErrorResponse(error, "删除渠道失败");
  }
}

export async function POST(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "AI 渠道配置", "修改");
    const parsed = await readAiJsonObject(request);
    const action = stringValue(parsed, "action");
    if (action !== undefined && action !== "test" && action !== "send") {
      throw new PublicApiError(400, "invalid_request", "action 无效。");
    }
    const db = getD1Database();
    if (action === "test") {
      const id = requireAiId(parsed.id, "id");
      return aiJsonResponse(await testAiChannelConnection(id, db));
    }
    if (action === "send") {
      const id = requireAiId(parsed.id, "id");
      const text = requireAiString(parsed.text, "消息内容", { maximumCharacters: 4_000, maximumBytes: 16_000 });
      return aiJsonResponse(await sendAiChannelText(id, text, db));
    }
    const input = channelInputFromPayload(parsed);
    if (!input) throw new PublicApiError(400, "invalid_request", "渠道信息不完整。");
    const item = await upsertAiChannel(input, db);
    return aiJsonResponse({ item });
  } catch (error) {
    return aiRouteErrorResponse(error, "保存渠道配置失败");
  }
}
