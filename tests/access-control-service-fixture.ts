import { DatabaseSync } from "node:sqlite";

type Scope = { warehouses: string[]; channels: string[]; platforms: string[] } | null;

const readerOrigin = "http://127.0.0.1:18101";
const writerOrigin = "http://127.0.0.1:18102";
const testSecret = "access-control-test-transport-secret-32-bytes";
const originalFetch = globalThis.fetch.bind(globalThis);
let activeDatabase: DatabaseSync | undefined;
let installed = false;

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "x-access-control-revision": "1:aaaaaaaaaaaa",
      "cache-control": "no-store",
    },
  });
}

function parseScope(value: unknown): Scope | undefined {
  if (value === null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== 3) return undefined;
  const dimensions = [row.warehouses, row.channels, row.platforms];
  if (dimensions.some((items) => !Array.isArray(items)
    || !items.every((item) => typeof item === "string" && item.trim().length > 0))) return undefined;
  return {
    warehouses: [...new Set(row.warehouses as string[])].sort(),
    channels: [...new Set(row.channels as string[])].sort(),
    platforms: [...new Set(row.platforms as string[])].sort(),
  };
}

function covers(current: Scope, snapshot: Scope): boolean {
  if (current === null) return true;
  if (snapshot === null) return false;
  return snapshot.warehouses.every((value) => current.warehouses.includes(value))
    && snapshot.channels.every((value) => current.channels.includes(value))
    && snapshot.platforms.every((value) => current.platforms.includes(value));
}

async function accessControlFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(input, init);
  const url = new URL(request.url);
  if (![readerOrigin, writerOrigin].includes(url.origin)) return originalFetch(input, init);
  const database = activeDatabase;
  if (!database) return json({ error: "test access-control database unavailable", code: "service_unavailable" }, 503);
  if (request.method !== "POST" || ![
    "/api/access-control/principal/resolve",
    "/api/access-control/principal/authorize-background",
  ].includes(url.pathname)) {
    return json({ error: "test access-control path rejected", code: "invalid_request" }, 400);
  }
  const payload = await request.json() as Record<string, unknown>;
  const email = String(payload.email ?? payload.ownerEmail ?? "").trim().toLowerCase();
  const row = database.prepare(`SELECT email,display_name,role,status,scope_json
    FROM app_users WHERE email=? COLLATE NOCASE`).get(email) as {
      email: string; display_name: string; role: string; status: string; scope_json: string | null;
    } | undefined;
  if (!row || row.status !== "active") {
    return json({ error: "当前账号未获得运营管理系统访问权限", code: "access_denied" }, 403);
  }
  let currentScope: Scope | undefined;
  try {
    currentScope = row.scope_json === null ? null : parseScope(JSON.parse(row.scope_json));
  } catch {
    currentScope = undefined;
  }
  if (currentScope === undefined) return json({ error: "用户数据范围无效", code: "access_denied" }, 403);
  const user = {
    email: row.email.toLowerCase(),
    displayName: row.display_name,
    role: row.role,
    status: row.status,
    scope: currentScope,
    version: 1,
  };
  if (url.pathname.endsWith("authorize-background")) {
    const snapshot = parseScope(payload.scope);
    if (snapshot === undefined || !["analyst", "operator", "admin"].includes(row.role) || !covers(currentScope, snapshot)) {
      return json({ error: "任务发起账号当前权限不再覆盖任务快照", code: "access_denied" }, 403);
    }
    return json({ user: { ...user, scope: snapshot } });
  }
  return json({ user });
}

export function installDjangoAccessControlFixture(database: DatabaseSync): void {
  database.exec(`CREATE TABLE IF NOT EXISTS app_users (
    email TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    scope_json TEXT
  )`);
  activeDatabase = database;
  process.env.TERUISI_DJANGO_ACCESS_CONTROL_READER_BASE_URL = readerOrigin;
  process.env.TERUISI_DJANGO_ACCESS_CONTROL_WRITER_BASE_URL = writerOrigin;
  process.env.TERUISI_DJANGO_INTERNAL_SECRET = testSecret;
  if (!installed) {
    installed = true;
    globalThis.fetch = accessControlFetch;
  }
}
