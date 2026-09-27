import { requireAppPrincipal, type AppPrincipal, type AppRole } from "@/lib/auth/authorization";
import { aiConsumer } from "@/lib/django/ai-service";
export type AiToolAuditInput = {
  requestId: string;
  invocationId?: string;
  providerCallId?: string;
  actorEmail: string;
  actorRole: AppRole;
  surface: string;
  toolName: string;
  arguments: unknown;
  status: "started" | "succeeded" | "failed";
  durationMs: number;
  result?: Record<string, unknown>;
  errorCode?: string;
};

export async function recordAiToolAudit(input: AiToolAuditInput, principal?: AppPrincipal) {
  const actor = principal ?? await requireAppPrincipal();
  await aiConsumer(actor, { operation: "tool-audit", entry: input });
}
export function summarizeToolArguments(value: unknown): unknown {
  return summarize(value, 0);
}

function summarize(value: unknown, depth: number): unknown {
  if (depth > 3) return "[depth-limited]";
  if (typeof value === "string") return value.length > 240 ? `${value.slice(0, 240)}…` : value;
  if (typeof value !== "object" || value === null) return value;
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => summarize(item, depth + 1));
  const output: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value).slice(0, 40)) {
    output[key] = /secret|password|token|api.?key|authorization|^code$|^inputsJson$/i.test(key)
      ? "[redacted]"
      : summarize(item, depth + 1);
  }
  return output;
}
