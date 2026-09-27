import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";
import { installDjangoAccessControlFixture } from "./access-control-service-fixture";

const testEnvironment: { DB?: unknown } = {};
const identity: { email: string } = { email: "restricted-admin@example.com" };
(globalThis as typeof globalThis & {
  __marketRouteSafetyEnv?: typeof testEnvironment;
  __marketRouteSafetyIdentity?: typeof identity;
}).__marketRouteSafetyEnv = testEnvironment;
(globalThis as typeof globalThis & {
  __marketRouteSafetyIdentity?: typeof identity;
}).__marketRouteSafetyIdentity = identity;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__marketRouteSafetyEnv;",
        shortCircuit: true,
      };
    }
    if (specifier === "next/headers") {
      return {
        url: "data:text/javascript,export async function headers(){return new Headers({'oai-authenticated-user-email':globalThis.__marketRouteSafetyIdentity.email});}",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

function sqliteAdapter(sqlite: DatabaseSync) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) {
          values = nextValues as SQLInputValue[];
          return this;
        },
        async first<T>(column?: string) {
          const row = sqlite.prepare(sql).get(...values) as Record<string, unknown> | undefined;
          return (column ? row?.[column] : row ?? null) as T | null;
        },
        async all<T>() {
          return { results: sqlite.prepare(sql).all(...values) as T[] };
        },
        async run() {
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  };
}

function bodyTrapRequest(url: string) {
  let bodyRead = false;
  const request = {
    url,
    method: "POST",
    headers: new Headers({ "content-type": "application/json" }),
    get body() {
      bodyRead = true;
      throw new Error("request body must not be read before authorization");
    },
  } as unknown as Request;
  return { request, wasBodyRead: () => bodyRead };
}

test("market write routes authenticate and reject restricted scope before bounded body parsing", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const database = sqliteAdapter(sqlite);
  testEnvironment.DB = database;
  installDjangoAccessControlFixture(sqlite);
  const { ensureAuthorizationSchema } = await import("./legacy/auth/authorization");
  await ensureAuthorizationSchema(database as never);
  const scopeJson = JSON.stringify({ warehouses: [], channels: [], platforms: ["京东"] });
  sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
    VALUES (?, 'Restricted admin', 'admin', 'active', ?)`)
    .run("restricted-admin@example.com", scopeJson);
  sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
    VALUES (?, 'Restricted operator', 'operator', 'active', ?)`)
    .run("restricted-operator@example.com", scopeJson);
  sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
    VALUES ('unrestricted-admin@example.com', 'Unrestricted admin', 'admin', 'active', NULL)`).run();

  const masterRoute = await import("../app/api/market/master/route");
  const annotationsRoute = await import("../app/api/market/annotations/route");

  identity.email = "restricted-admin@example.com";
  const masterTrap = bodyTrapRequest("https://example.test/api/market/master");
  const masterDenied = await masterRoute.POST(masterTrap.request);
  assert.equal(masterDenied.status, 403);
  assert.equal(masterDenied.headers.get("cache-control"), "no-store");
  assert.equal(masterTrap.wasBodyRead(), false);

  identity.email = "restricted-operator@example.com";
  const annotationTrap = bodyTrapRequest("https://example.test/api/market/annotations");
  const annotationDenied = await annotationsRoute.POST(annotationTrap.request);
  assert.equal(annotationDenied.status, 403);
  assert.equal(annotationDenied.headers.get("cache-control"), "no-store");
  assert.equal(annotationTrap.wasBodyRead(), false);

  identity.email = "unrestricted-admin@example.com";
  const oversized = new Request("https://example.test/api/market/master", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "confirm_price", note: "x".repeat(256 * 1024) }),
  });
  const oversizedResponse = await masterRoute.POST(oversized);
  assert.equal(oversizedResponse.status, 413);
  assert.equal(oversizedResponse.headers.get("cache-control"), "no-store");

  const malformed = await annotationsRoute.POST(new Request("https://example.test/api/market/annotations", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "[1,2,3]",
  }));
  assert.equal(malformed.status, 400);
  assert.equal(malformed.headers.get("cache-control"), "no-store");
  sqlite.close();
});
