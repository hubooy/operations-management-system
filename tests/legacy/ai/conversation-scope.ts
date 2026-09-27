import type { AppDataScope } from "@/tests/legacy/auth/authorization";
import type { D1Database } from "@/lib/database/d1";

type RestrictedScope = Exclude<AppDataScope, null>;

export const AI_CONVERSATION_SCOPE_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS ai_conversation_scopes (
    conversation_id TEXT PRIMARY KEY NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    scope_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_conversation_scopes_scope_created_idx
    ON ai_conversation_scopes (scope_json, created_at)`,
] as const;

const scopeReadyByDatabase = new WeakMap<object, Promise<void>>();

export async function ensureAiConversationScopeSchema(db: D1Database): Promise<void> {
  const key = db as unknown as object;
  const existing = scopeReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(AI_CONVERSATION_SCOPE_SCHEMA_STATEMENTS.map((statement) => db.prepare(statement)))
    .then(() => undefined)
    .catch((error: unknown) => {
      scopeReadyByDatabase.delete(key);
      throw error;
    });
  scopeReadyByDatabase.set(key, setup);
  return setup;
}

function normalizeScopeValues(values: readonly string[]) {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
}

/**
 * Canonical scope snapshot stored with an AI conversation. A missing row is a
 * legacy unrestricted snapshot and therefore remains visible only to a
 * currently unrestricted principal.
 */
export function serializeAiConversationScope(scope: AppDataScope): string {
  if (scope === null) return "null";
  return JSON.stringify({
    warehouses: normalizeScopeValues(scope.warehouses),
    channels: normalizeScopeValues(scope.channels),
    platforms: normalizeScopeValues(scope.platforms),
  } satisfies RestrictedScope);
}

/**
 * Returns a SQL fragment proving that the current principal covers every
 * dimension in the immutable conversation snapshot. An unrestricted current
 * principal covers all historical snapshots; a restricted principal never
 * covers a missing, malformed, or legacy-unrestricted snapshot.
 */
export function aiConversationScopeAccessSql(
  scope: AppDataScope,
  alias = "s",
): { join: string; clause: string; values: string[] } {
  if (scope === null) return { join: "", clause: "", values: [] };
  const access = aiScopeSnapshotAccessSql(scope, `${alias}.scope_json`);
  return {
    join: ` LEFT JOIN ai_conversation_scopes ${alias} ON ${alias}.conversation_id = c.id`,
    clause: access.clause,
    values: access.values,
  };
}

/**
 * Generic immutable-scope SQL used by AI resources outside conversations.
 * The expression must be a trusted column expression chosen by server code.
 */
export function aiScopeSnapshotAccessSql(
  scope: AppDataScope,
  scopeJsonExpression: string,
): { clause: string; values: string[] } {
  if (scope === null) return { clause: "", values: [] };
  const serialized = serializeAiConversationScope(scope);
  const safeSnapshot = `CASE WHEN ${scopeJsonExpression} IS NOT NULL AND json_valid(${scopeJsonExpression})
        THEN ${scopeJsonExpression} ELSE 'null' END`;
  return {
    clause: ` AND ${scopeJsonExpression} IS NOT NULL
      AND ${scopeJsonExpression} <> 'null'
      AND json_valid(${scopeJsonExpression})
      AND json_type(${safeSnapshot}) = 'object'
      AND json_type(${safeSnapshot}, '$.warehouses') = 'array'
      AND json_type(${safeSnapshot}, '$.channels') = 'array'
      AND json_type(${safeSnapshot}, '$.platforms') = 'array'
      AND NOT EXISTS (
        SELECT 1 FROM json_each(${safeSnapshot}, '$.warehouses') stored
        WHERE stored.type <> 'text'
      )
      AND NOT EXISTS (
        SELECT 1 FROM json_each(${safeSnapshot}, '$.channels') stored
        WHERE stored.type <> 'text'
      )
      AND NOT EXISTS (
        SELECT 1 FROM json_each(${safeSnapshot}, '$.platforms') stored
        WHERE stored.type <> 'text'
      )
      AND NOT EXISTS (
        SELECT 1 FROM json_each(${safeSnapshot}, '$.warehouses') stored
        WHERE CAST(stored.value AS TEXT) NOT IN (
          SELECT CAST(current.value AS TEXT) FROM json_each(?, '$.warehouses') current
        )
      )
      AND NOT EXISTS (
        SELECT 1 FROM json_each(${safeSnapshot}, '$.channels') stored
        WHERE CAST(stored.value AS TEXT) NOT IN (
          SELECT CAST(current.value AS TEXT) FROM json_each(?, '$.channels') current
        )
      )
      AND NOT EXISTS (
        SELECT 1 FROM json_each(${safeSnapshot}, '$.platforms') stored
        WHERE CAST(stored.value AS TEXT) NOT IN (
          SELECT CAST(current.value AS TEXT) FROM json_each(?, '$.platforms') current
        )
      )`,
    values: [serialized, serialized, serialized],
  };
}
