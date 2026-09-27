import type { AiPageModule } from "./page-context";

/** Recovery only reads durable receipts and the exact saved reply. It never dispatches. */
export async function recoverAiWorkspaceRequest(input: {
  clientRequestId: string; module: AiPageModule; signal: AbortSignal;
  isCurrent: () => boolean;
  fetcher?: typeof fetch;
}): Promise<{ conversationId: string } | null> {
  const fetcher = input.fetcher ?? fetch;
  const current = () => !input.signal.aborted && input.isCurrent();
  const params = new URLSearchParams({ clientRequestId: input.clientRequestId, workspaceModule: input.module });
  const receiptResponse = await fetcher(`/api/ai/chat?${params}`, { cache: "no-store", signal: input.signal });
  const receipt = await receiptResponse.json() as { request?: { status?: string; conversationId?: string; assistantMessageId?: string } };
  if (!current()) return null;
  if (!receiptResponse.ok || receipt.request?.status !== "succeeded") return null;
  const { conversationId, assistantMessageId } = receipt.request;
  if (!conversationId || !assistantMessageId) throw new Error("上次回复的回执不完整，请稍后刷新。");
  const target = new URLSearchParams({ conversationId, messageId: assistantMessageId, workspaceModule: input.module });
  const savedResponse = await fetcher(`/api/ai/chat?${target}`, { cache: "no-store", signal: input.signal });
  const saved = await savedResponse.json() as { conversation?: { id?: string }; items?: { id?: string; conversationId?: string }[] };
  if (!current()) return null;
  if (!savedResponse.ok || saved.conversation?.id !== conversationId
    || saved.items?.length !== 1 || saved.items[0].id !== assistantMessageId
    || saved.items[0].conversationId !== conversationId) throw new Error("尚未完整读回上次回复，已保留恢复记录，请稍后刷新。");
  return { conversationId };
}
