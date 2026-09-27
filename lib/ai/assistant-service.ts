import type { AppPrincipal } from "@/lib/auth/authorization";
import { aiConsumer } from "@/lib/django/ai-service";
export async function resolveChatModel(principal: AppPrincipal) {
  const result = await aiConsumer<{ items: Array<{ id: string; modelType: string }> }>(principal, { operation: "model-list", modelType: "text" });
  return result.items[0] ?? null;
}
export async function generateConfiguredAnalysisReply(input: { prompt: string; principal: AppPrincipal; requestId: string; auditArguments?: Record<string, unknown>; signal?: AbortSignal }) {
  return (await aiConsumer<{ reply: string }>(input.principal, { operation: "analysis-reply", prompt: input.prompt, systemPrompt: "你是客服分析助手。仅依据提供的会话事实分析，不编造缺失信息。", surface: "customer_service_ai" }, { signal: input.signal, requestId: input.requestId })).reply;
}
