import type { AppPrincipal } from "@/lib/auth/authorization";
import { aiConsumer } from "@/lib/django/ai-service";
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
export const describeAiAnalysisDatasets = (principal: AppPrincipal) => aiConsumer<Record<string, unknown>>(principal, { operation: "analysis-describe" });
export const runAndRecordAiAnalysisPlan = (input: unknown, principal: AppPrincipal, requestId: string) => aiConsumer<Record<string, unknown>>(principal, { operation: "analysis-plan", input }, { requestId });

export function runPandasAnalysis(args: Record<string, unknown>, context: AiToolExecutionContext) {
  return aiConsumer<Record<string, unknown>>(context.principal, {
    operation: "pandas-analysis", ...args,
    surface: context.surface === "dingtalk_chat" ? "dingtalk_chat" : "ai_chat",
  }, { signal: context.signal, requestId: context.invocationId ?? context.requestId });
}
