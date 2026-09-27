import {
  ensureAuthorizationSchema,
  type AppRole,
} from "@/tests/legacy/auth/authorization";
import { getD1Database, type D1Database } from "@/lib/database/d1";

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

export async function recordAiToolAudit(input: AiToolAuditInput) {
  const db = getD1Database();
  await ensureAuthorizationSchema(db);
  const resultJson = input.result ? JSON.stringify(input.result) : "";
  const responseDigest = resultJson ? await sha256Hex(resultJson) : null;
  const id = crypto.randomUUID();
  const argumentsJson = safeJson(input.arguments, 4_000);
  const rowCount = inferRowCount(input.result);
  const durationMs = Math.max(0, Math.trunc(input.durationMs));
  if (await supportsInvocationCorrelation(db)) {
    await db.prepare(
      `INSERT INTO ai_tool_audit_logs (
        id, request_id, invocation_id, provider_call_id, actor_email, actor_role, surface, tool_name,
        arguments_json, status, row_count, duration_ms, response_digest, error_code
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      id,
      input.requestId,
      (input.invocationId || input.requestId).slice(0, 160),
      input.providerCallId?.slice(0, 200) ?? null,
      input.actorEmail,
      input.actorRole,
      input.surface.slice(0, 80),
      input.toolName,
      argumentsJson,
      input.status,
      rowCount,
      durationMs,
      responseDigest,
      input.errorCode ?? null,
    ).run();
    return;
  }
  await db.prepare(
      `INSERT INTO ai_tool_audit_logs (
        id, request_id, actor_email, actor_role, surface, tool_name,
        arguments_json, status, row_count, duration_ms, response_digest, error_code
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      id,
      input.requestId,
      input.actorEmail,
      input.actorRole,
      input.surface.slice(0, 80),
      input.toolName,
      argumentsJson,
      input.status,
      rowCount,
      durationMs,
      responseDigest,
      input.errorCode ?? null,
    ).run();
}

const invocationCorrelationByDatabase = new WeakMap<object, Promise<boolean>>();

async function supportsInvocationCorrelation(db: D1Database): Promise<boolean> {
  const key = db as unknown as object;
  const existing = invocationCorrelationByDatabase.get(key);
  if (existing) return existing;
  const check = db.prepare("PRAGMA table_info(ai_tool_audit_logs)").all<{ name: string }>()
    .then((info) => {
      const columns = new Set((info.results ?? []).map((column) => column.name));
      const supported = columns.has("invocation_id") && columns.has("provider_call_id");
      if (!supported) invocationCorrelationByDatabase.delete(key);
      return supported;
    })
    .catch((error: unknown) => {
      invocationCorrelationByDatabase.delete(key);
      throw error;
    });
  invocationCorrelationByDatabase.set(key, check);
  return check;
}

export function summarizeToolArguments(value: unknown): unknown {
  return summarize(value, 0);
}

function inferRowCount(result: Record<string, unknown> | undefined): number | null {
  if (!result) return null;
  if (result.data && typeof result.data === "object" && !Array.isArray(result.data)) {
    return inferRowCount(result.data as Record<string, unknown>);
  }
  if (typeof result.returned === "number") return Math.max(0, Math.trunc(result.returned));
  if (Array.isArray(result.items)) return result.items.length;
  if (Array.isArray(result.daily)) return result.daily.length;
  return null;
}

function summarize(value: unknown, depth: number): unknown {
  if (depth > 3) return "[depth-limited]";
  if (typeof value === "string") return value.length > 240 ? `${value.slice(0, 240)}…` : value;
  if (typeof value !== "object" || value === null) return value;
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => summarize(item, depth + 1));
  const output: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value).slice(0, 40)) {
    output[key] = /secret|password|token|api.?key|authorization/i.test(key)
      ? "[redacted]"
      : summarize(item, depth + 1);
  }
  return output;
}

function safeJson(value: unknown, limit: number) {
  try {
    return JSON.stringify(value ?? {}).slice(0, limit);
  } catch {
    return "{}";
  }
}

async function sha256Hex(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
