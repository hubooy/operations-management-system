import type { AppPrincipal } from "@/lib/auth/authorization";
import type { D1Database } from "@/lib/database/d1";

export type AiConversationAccessRecord = {
  createdBy: string;
};

export function assertAiConversationAccess(
  conversation: AiConversationAccessRecord,
  principal: AppPrincipal,
): void {
  if (principal.role !== "admin" && conversation.createdBy !== principal.email) {
    throw new Error("无权访问该对话");
  }
}

export function isAiChatCapableModelType(value: string): value is "text" | "vision" {
  return value === "text" || value === "vision";
}

export async function deleteAiConversationData(
  conversationId: string,
  db: D1Database,
): Promise<boolean> {
  const results = await db.batch([
    db.prepare("DELETE FROM ai_artifacts WHERE conversation_id = ?").bind(conversationId),
    db.prepare("DELETE FROM ai_conversation_messages WHERE conversation_id = ?").bind(conversationId),
    db.prepare("DELETE FROM ai_conversations WHERE id = ?").bind(conversationId),
  ]);
  return Number(results[2]?.meta.changes ?? 0) > 0;
}
