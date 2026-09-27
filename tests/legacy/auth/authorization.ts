import { getChatGPTUser } from "@/app/chatgpt-auth";
import { env } from "cloudflare:workers";
import { headers } from "next/headers";
import {
  getD1Database,
  type D1Database,
} from "@/lib/database/d1";
import {
  ACCESS_CONTROL_RESOLVE_PATH,
  AccessControlServiceError,
  createDjangoAccessControlService,
} from "@/lib/django/access-control-service";
import {
  decideLocalDirectAccess,
  isLoopbackRequestHost,
} from "@/lib/auth/local-direct-access";

export const BOOTSTRAP_ADMIN_EMAIL = "dengweizhang321@gmail.com";

const LOCAL_DIRECT_ACCESS_PRINCIPAL: AppPrincipal = {
  email: "local-admin@teruisi.local",
  displayName: "本地管理员",
  role: "admin",
  scope: null,
};

export const appRoles = ["viewer", "analyst", "operator", "admin"] as const;
export type AppRole = (typeof appRoles)[number];

export type AppDataScope = {
  warehouses: string[];
  channels: string[];
  platforms: string[];
} | null;

export type AppPrincipal = {
  email: string;
  displayName: string;
  role: AppRole;
  scope: AppDataScope;
};

export class AuthorizationError extends Error {
  readonly status: 401 | 403 | 503;
  readonly code: "authentication_required" | "access_denied" | "insufficient_role" | "service_unavailable";

  constructor(
    status: 401 | 403 | 503,
    code: AuthorizationError["code"],
    message: string,
  ) {
    super(message);
    this.name = "AuthorizationError";
    this.status = status;
    this.code = code;
  }
}

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS ai_tool_audit_logs (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL,
    invocation_id TEXT NOT NULL DEFAULT '',
    provider_call_id TEXT,
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    surface TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    row_count INTEGER,
    duration_ms INTEGER,
    response_digest TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_tool_audit_logs_actor_created_idx
    ON ai_tool_audit_logs (actor_email, created_at)`,
  `CREATE INDEX IF NOT EXISTS ai_tool_audit_logs_tool_created_idx
    ON ai_tool_audit_logs (tool_name, created_at)`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export async function ensureAuthorizationSchema(
  db: D1Database = getD1Database(),
): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;

  const setup = db
    .batch(
      schemaStatements.map((statement) => db.prepare(statement)),
    )
    .then(() => ensureAiToolAuditExecutionIndex(db))
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });

  schemaReadyByDatabase.set(key, setup);
  return setup;
}

async function ensureAiToolAuditExecutionIndex(db: D1Database): Promise<void> {
  const info = await db.prepare("PRAGMA table_info(ai_tool_audit_logs)").all<{ name: string }>();
  const names = new Set((info.results ?? []).map((column) => column.name));
  if (!names.has("invocation_id")) return;
  await db.prepare(`CREATE INDEX IF NOT EXISTS ai_tool_audit_logs_invocation_created_idx
    ON ai_tool_audit_logs (invocation_id, created_at)`).run();
}

export async function requireAppPrincipal(
  allowedRoles: readonly AppRole[] = appRoles,
): Promise<AppPrincipal> {
  const viteEnvironment = (
    import.meta as ImportMeta & {
      readonly env?: {
        readonly DEV?: boolean;
        readonly PROD?: boolean;
        readonly VITE_TERUISI_LOCAL_BUILD?: string;
      };
    }
  ).env;
  const localAccess = decideLocalDirectAccess(allowedRoles, {
    enabled:
      typeof env.TERUISI_LOCAL_DIRECT_ACCESS === "string"
        ? env.TERUISI_LOCAL_DIRECT_ACCESS
        : undefined,
    runtimeEnvironment:
      typeof env.TERUISI_RUNTIME_ENV === "string"
        ? env.TERUISI_RUNTIME_ENV
        : undefined,
    viteDevelopment: viteEnvironment?.DEV === true,
    viteProduction: viteEnvironment?.PROD === true,
    nodeEnvironment: process.env.NODE_ENV,
    localBuild:
      viteEnvironment?.VITE_TERUISI_LOCAL_BUILD?.trim().toLowerCase() ===
        "true" ||
      (typeof env.VITE_TERUISI_LOCAL_BUILD === "string" &&
        env.VITE_TERUISI_LOCAL_BUILD.trim().toLowerCase() === "true"),
  });

  if (localAccess === "allowed") {
    const requestHeaders = await headers();
    if (!isLoopbackRequestHost(requestHeaders.get("host"))) {
      throw new AuthorizationError(
        403,
        "access_denied",
        "本地直连仅允许通过回环地址访问",
      );
    }
    return LOCAL_DIRECT_ACCESS_PRINCIPAL;
  }
  if (localAccess === "role_denied") {
    throw new AuthorizationError(
      403,
      "insufficient_role",
      "本地直连身份没有执行此操作的权限",
    );
  }

  const identity = await getChatGPTUser();
  if (!identity) {
    throw new AuthorizationError(
      401,
      "authentication_required",
      "请先使用 ChatGPT 账号登录",
    );
  }

  const normalizedEmail = identity.email.trim().toLowerCase();
  const identityDisplayName = identity.fullName ?? identity.displayName ?? normalizedEmail;
  const edgeIdentity: AppPrincipal = {
    email: normalizedEmail,
    displayName: identityDisplayName,
    role: "viewer",
    scope: null,
  };
  let row: { email: string; displayName: string; role: string; status: string; scope: AppDataScope };
  try {
    const result = await createDjangoAccessControlService().request<{ user?: unknown }>(
      edgeIdentity,
      {
        method: "POST",
        path: ACCESS_CONTROL_RESOLVE_PATH,
        service: "reader",
        payload: { email: normalizedEmail, displayName: identityDisplayName },
      },
    );
    row = parseResolvedUser(result.data.user, normalizedEmail);
  } catch (error) {
    if (error instanceof AccessControlServiceError && error.status === 403) {
      throw new AuthorizationError(403, "access_denied", error.message);
    }
    throw new AuthorizationError(503, "service_unavailable", "用户权限服务暂时不可用，请稍后重试");
  }

  if (row.status !== "active" || !isAppRole(row.role)) {
    throw new AuthorizationError(403, "access_denied", "当前账号未获得运营管理系统访问权限");
  }

  if (!allowedRoles.includes(row.role)) {
    throw new AuthorizationError(
      403,
      "insufficient_role",
      "当前账号没有执行此操作的权限",
    );
  }

  return {
    email: row.email,
    displayName: row.displayName,
    role: row.role,
    scope: row.scope,
  };
}

/**
 * Legacy aggregate endpoints must not silently ignore a restricted principal.
 * Keep them fail-closed until their domain query accepts and applies AppDataScope.
 */
export function requireUnrestrictedDataScope(
  principal: AppPrincipal,
  resourceLabel: string,
  operationLabel = "读取",
): void {
  if (principal.scope === null) return;
  throw new AuthorizationError(
    403,
    "access_denied",
    `当前账号的数据范围暂不支持${operationLabel}${resourceLabel}`,
  );
}

export function authorizationErrorResponse(error: unknown): Response | null {
  if (!(error instanceof AuthorizationError)) return null;
  return Response.json(
    { error: error.message, code: error.code },
    { status: error.status, headers: { "cache-control": "no-store" } },
  );
}

function isAppRole(value: string): value is AppRole {
  return appRoles.includes(value as AppRole);
}

function parseResolvedUser(
  value: unknown,
  expectedEmail: string,
): { email: string; displayName: string; role: string; status: string; scope: AppDataScope } {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid access-control principal response");
  }
  const row = value as Record<string, unknown>;
  if (row.email !== expectedEmail || typeof row.displayName !== "string"
    || typeof row.role !== "string" || row.status !== "active") {
    throw new Error("invalid access-control principal response");
  }
  return {
    email: expectedEmail,
    displayName: row.displayName,
    role: row.role,
    status: row.status,
    scope: parseScope(row.scope),
  };
}

function parseScope(value: unknown): AppDataScope {
  if (value === null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid access-control scope response");
  }
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== 3
    || !("warehouses" in record) || !("channels" in record) || !("platforms" in record)) {
    throw new Error("invalid access-control scope response");
  }
  return {
    warehouses: strictStringArray(record.warehouses),
    channels: strictStringArray(record.channels),
    platforms: strictStringArray(record.platforms),
  };
}

function strictStringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 500
    || !value.every((item) => typeof item === "string" && item.trim() && item.length <= 100)) {
    throw new Error("invalid access-control scope response");
  }
  return [...new Set(value.map((item) => item.trim()).filter(Boolean))];
}
