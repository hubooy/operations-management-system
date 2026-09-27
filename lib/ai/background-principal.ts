import { env } from "cloudflare:workers";

import {
  type AppDataScope,
  type AppPrincipal,
  type AppRole,
} from "@/lib/auth/authorization";
import { decideLocalDirectAccess } from "@/lib/auth/local-direct-access";
import {
  ACCESS_CONTROL_BACKGROUND_PATH,
  AccessControlServiceError,
  createDjangoAccessControlService,
} from "@/lib/django/access-control-service";

const BACKGROUND_AGENT_ROLES = ["analyst", "operator", "admin"] as const;
const LOCAL_BACKGROUND_OWNER = "local-admin@teruisi.local";

export type AiBackgroundPrincipalResult =
  | { ok: true; principal: AppPrincipal; actorRole: AppRole; localDirect: boolean }
  | { ok: false; code: "authorization_revoked" | "scope_invalid"; message: string };

export async function resolveAiBackgroundPrincipal(
  ownerEmailInput: string,
  scopeSnapshotJson: string,
): Promise<AiBackgroundPrincipalResult> {
  const ownerEmail = ownerEmailInput.trim().toLowerCase();
  const snapshot = parseStoredScope(scopeSnapshotJson, false);
  if (snapshot === undefined) {
    return { ok: false, code: "scope_invalid", message: "AI 任务的数据范围快照无效。" };
  }

  if (ownerEmail === LOCAL_BACKGROUND_OWNER) {
    if (snapshot !== null || !await localDirectBackgroundEnabled()) {
      return { ok: false, code: "authorization_revoked", message: "本地直连身份已失效或任务范围无效。" };
    }
    return {
      ok: true,
      principal: { email: ownerEmail, displayName: "本地管理员", role: "admin", scope: null },
      actorRole: "admin",
      localDirect: true,
    };
  }

  const signedIdentity: AppPrincipal = {
    email: ownerEmail,
    displayName: ownerEmail,
    role: "viewer",
    scope: null,
  };
  let row: Record<string, unknown>;
  try {
    const result = await createDjangoAccessControlService().request<{ user?: unknown }>(
      signedIdentity,
      {
        method: "POST",
        path: ACCESS_CONTROL_BACKGROUND_PATH,
        service: "reader",
        payload: { ownerEmail, scope: snapshot },
      },
    );
    if (!result.data.user || typeof result.data.user !== "object" || Array.isArray(result.data.user)) {
      return { ok: false, code: "authorization_revoked", message: "权限服务未返回有效任务身份。" };
    }
    row = result.data.user as Record<string, unknown>;
  } catch (error) {
    const message = error instanceof AccessControlServiceError && error.status === 403
      ? error.message
      : "权限服务不可用，后台任务已失败关闭。";
    return { ok: false, code: "authorization_revoked", message };
  }
  if (row.email !== ownerEmail || typeof row.displayName !== "string"
    || typeof row.role !== "string" || !isBackgroundRole(row.role)
    || row.status !== "active") {
    return { ok: false, code: "authorization_revoked", message: "任务发起账号已停用或不再具备 Agent 执行角色。" };
  }
  const returnedScope = parseStoredScope(JSON.stringify(row.scope), false);
  if (returnedScope === undefined || !scopeCoversSnapshot(returnedScope, snapshot)
    || !scopeCoversSnapshot(snapshot, returnedScope)) {
    return { ok: false, code: "authorization_revoked", message: "权限服务返回的数据范围与任务快照不一致。" };
  }
  return {
    ok: true,
    principal: {
      email: ownerEmail,
      displayName: row.displayName,
      role: row.role,
      // Expansion never broadens an existing job: tools always receive the immutable snapshot.
      scope: snapshot,
    },
    actorRole: row.role,
    localDirect: false,
  };
}

function isBackgroundRole(value: string): value is (typeof BACKGROUND_AGENT_ROLES)[number] {
  return BACKGROUND_AGENT_ROLES.includes(value as (typeof BACKGROUND_AGENT_ROLES)[number]);
}

function parseStoredScope(value: string | null, sqlNullIsUnrestricted: boolean): AppDataScope | undefined {
  if (value === null) return sqlNullIsUnrestricted ? null : undefined;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (parsed === null) return sqlNullIsUnrestricted ? undefined : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
    const record = parsed as Record<string, unknown>;
    const dimensions = [record.warehouses, record.channels, record.platforms];
    if (dimensions.some((items) => !Array.isArray(items)
      || !items.every((item) => typeof item === "string" && item.trim().length > 0))) return undefined;
    return {
      warehouses: normalizeScopeValues(record.warehouses as string[]),
      channels: normalizeScopeValues(record.channels as string[]),
      platforms: normalizeScopeValues(record.platforms as string[]),
    };
  } catch {
    return undefined;
  }
}

function normalizeScopeValues(values: readonly string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
}

function scopeCoversSnapshot(current: AppDataScope, snapshot: AppDataScope): boolean {
  if (current === null) return true;
  if (snapshot === null) return false;
  return snapshot.warehouses.every((value) => current.warehouses.includes(value))
    && snapshot.channels.every((value) => current.channels.includes(value))
    && snapshot.platforms.every((value) => current.platforms.includes(value));
}

async function localDirectBackgroundEnabled(): Promise<boolean> {
  try {
    const viteEnvironment = (
      import.meta as ImportMeta & {
        readonly env?: {
          readonly DEV?: boolean;
          readonly PROD?: boolean;
          readonly VITE_TERUISI_LOCAL_BUILD?: string;
        };
      }
    ).env;
    return decideLocalDirectAccess(BACKGROUND_AGENT_ROLES, {
      enabled: typeof env.TERUISI_LOCAL_DIRECT_ACCESS === "string"
        ? env.TERUISI_LOCAL_DIRECT_ACCESS
        : undefined,
      runtimeEnvironment: typeof env.TERUISI_RUNTIME_ENV === "string"
        ? env.TERUISI_RUNTIME_ENV
        : undefined,
      viteDevelopment: viteEnvironment?.DEV === true,
      viteProduction: viteEnvironment?.PROD === true,
      nodeEnvironment: globalThis.process?.env?.NODE_ENV,
      localBuild: viteEnvironment?.VITE_TERUISI_LOCAL_BUILD?.trim().toLowerCase() === "true",
    }) === "allowed";
  } catch {
    return false;
  }
}
