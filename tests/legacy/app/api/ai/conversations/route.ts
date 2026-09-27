import { requireAppPrincipal } from "@/lib/auth/authorization";
import {
  AI_CONVERSATION_PAGE_MAX,
  AI_CONVERSATION_PAGE_SIZE_MAX,
  deleteAiConversation,
  ensureAiAssistantSchema,
  listAiConversations,
  listAvailableChatModels,
  selectConversationModel,
} from "@/tests/legacy/ai/assistant-service";
import { getD1Database } from "@/lib/database/d1";
import {
  aiJsonResponse,
  aiRouteErrorResponse,
  parseAiPositiveInteger,
  readAiJsonObject,
  requireAiId,
  requireAiSameOriginWrite,
} from "@/app/api/ai/route-helpers";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal();
    const db = getD1Database();
    await ensureAiAssistantSchema(db);
    const params = new URL(request.url).searchParams;
    const page = parseAiPositiveInteger(params, "page", 1, AI_CONVERSATION_PAGE_MAX);
    const pageSize = parseAiPositiveInteger(params, "pageSize", 30, AI_CONVERSATION_PAGE_SIZE_MAX);
    const [conversationPage, models] = await Promise.all([
      listAiConversations(principal, { page, pageSize }, db),
      listAvailableChatModels(db),
    ]);
    return aiJsonResponse({ ...conversationPage, models });
  } catch (error) {
    return aiRouteErrorResponse(error, "读取对话列表失败");
  }
}

export async function PATCH(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const payload = await readAiJsonObject(request);
    const conversationId = requireAiId(payload.conversationId, "conversationId");
    const modelId = requireAiId(payload.modelId, "modelId");
    const item = await selectConversationModel(conversationId, modelId, principal, getD1Database());
    return aiJsonResponse({ item });
  } catch (error) {
    return aiRouteErrorResponse(error, "切换对话模型失败");
  }
}

export async function DELETE(request: Request) {
  try {
    requireAiSameOriginWrite(request);
    const principal = await requireAppPrincipal(["admin", "operator", "analyst"]);
    const ids = new URL(request.url).searchParams.getAll("id");
    const conversationId = requireAiId(ids.length === 1 ? ids[0] : undefined, "id");
    const deleted = await deleteAiConversation(conversationId, principal, "用户通过 AI 助理页面删除", getD1Database());
    if (!deleted) return aiJsonResponse({ error: "对话不存在或已删除", code: "not_found" }, { status: 404 });
    return aiJsonResponse({ ok: true, deleted: true });
  } catch (error) {
    return aiRouteErrorResponse(error, "删除对话失败");
  }
}
