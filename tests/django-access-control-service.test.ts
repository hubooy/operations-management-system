import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  ACCESS_CONTROL_BACKGROUND_PATH,
  ACCESS_CONTROL_RESOLVE_PATH,
  ACCESS_CONTROL_USERS_PATH,
  AccessControlServiceError,
  requestDjangoAccessControl,
} from "../lib/django/access-control-service";

const secret = "access-control-service-test-secret-32-bytes-minimum";
const principal: AppPrincipal = {
  email: "admin@example.com",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const config = {
  readerBaseUrl: "http://127.0.0.1:8101",
  writerBaseUrl: "http://127.0.0.1:8102",
  internalSecret: secret,
  timeoutMs: 1_000,
  maxRequestBytes: 64 * 1024,
  maxResponseBytes: 64 * 1024,
};

function response(payload: unknown, init: ResponseInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  if (!headers.has("x-access-control-revision")) headers.set("x-access-control-revision", "7:abcdef012345");
  return new Response(JSON.stringify(payload), { ...init, headers });
}

test("access-control reader signs exact identity and requires a revision", async () => {
  let upstream: Request | undefined;
  const result = await requestDjangoAccessControl<{ user: { email: string } }>(
    principal,
    {
      method: "POST",
      path: ACCESS_CONTROL_RESOLVE_PATH,
      service: "reader",
      payload: { email: principal.email, displayName: principal.displayName },
    },
    {
      config,
      now: () => 1_788_000_000_000,
      requestId: () => "access-control-request-1",
      fetchImpl: async (input, init) => {
        upstream = new Request(input, init);
        return response({ user: { email: principal.email } });
      },
    },
  );
  assert.ok(upstream);
  assert.equal(upstream.url, `http://127.0.0.1:8101${ACCESS_CONTROL_RESOLVE_PATH}`);
  const encodedPrincipal = upstream.headers.get("x-teruisi-principal")!;
  const bodyHash = upstream.headers.get("x-teruisi-content-sha256")!;
  const canonical = [
    "v1", "1788000000", "access-control-request-1", "POST",
    ACCESS_CONTROL_RESOLVE_PATH, "", bodyHash, encodedPrincipal,
  ].join("\n");
  assert.equal(
    upstream.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
  assert.equal(result.revision, "7:abcdef012345");
  assert.equal(result.data.user.email, principal.email);
});

test("access-control writer is isolated and preserves replay evidence", async () => {
  let url = "";
  const result = await requestDjangoAccessControl<{ ok: boolean }>(
    principal,
    {
      method: "PUT",
      path: ACCESS_CONTROL_USERS_PATH,
      service: "writer",
      payload: {
        email: principal.email, displayName: principal.displayName, role: "admin",
        status: "active", scope: null, expectedVersion: 1, reason: "test",
      },
    },
    {
      config,
      fetchImpl: async (input) => {
        url = String(input);
        return response({ ok: true }, { headers: { "x-teruisi-write-replay": "1" } });
      },
    },
  );
  assert.equal(url, `http://127.0.0.1:8102${ACCESS_CONTROL_USERS_PATH}`);
  assert.equal(result.replayed, true);
});

test("access-control transport fails closed on topology, allowlist, and response drift", async () => {
  await assert.rejects(
    requestDjangoAccessControl(principal, {
      method: "POST", path: ACCESS_CONTROL_BACKGROUND_PATH, service: "reader",
      payload: { ownerEmail: principal.email, scope: null },
    }, { config: { ...config, writerBaseUrl: config.readerBaseUrl } }),
    (error: unknown) => error instanceof AccessControlServiceError && error.status === 503,
  );
  await assert.rejects(
    requestDjangoAccessControl(principal, {
      method: "GET", path: "/api/access-control/unbounded", service: "reader",
    }, { config }),
    (error: unknown) => error instanceof AccessControlServiceError && error.status === 503,
  );
  await assert.rejects(
    requestDjangoAccessControl(principal, {
      method: "POST", path: ACCESS_CONTROL_RESOLVE_PATH, service: "reader",
      payload: { email: principal.email, displayName: principal.displayName },
    }, { config, fetchImpl: async () => response({ user: {} }, { headers: { "x-access-control-revision": "" } }) }),
    (error: unknown) => error instanceof AccessControlServiceError && error.status === 503,
  );
});

test("runtime authorization has no reachable D1 user fallback", async () => {
  const [authorization, background, schema, settings, health, ui, retirement, runtime, baseRuntime, workerRuntime] = await Promise.all([
    readFile(new URL("../lib/auth/authorization.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/background-principal.ts", import.meta.url), "utf8"),
    readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/teruisi_backend/settings.py", import.meta.url), "utf8"),
    readFile(new URL("../backend/teruisi_backend/health.py", import.meta.url), "utf8"),
    readFile(new URL("../app/access-control-management.tsx", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0112_access_control_domain_retirement.sql", import.meta.url), "utf8"),
    readFile(new URL("../tools/django-access-control.ps1", import.meta.url), "utf8"),
    readFile(new URL("../tools/django-local-service.ps1", import.meta.url), "utf8"),
    readFile(new URL("../tools/worker-local-service.ps1", import.meta.url), "utf8"),
  ]);
  for (const source of [authorization, background]) assert.doesNotMatch(source, /FROM\s+app_users|JOIN\s+app_users/i);
  assert.doesNotMatch(schema, /sqliteTable\(\s*["']app_users["']/);
  assert.match(settings, /access_control_reader/);
  assert.match(settings, /access_control_writer/);
  assert.match(health, /ACCESS_CONTROL_ROLE_CONTRACT/);
  assert.match(health, /has_table_privilege\(current_user,c\.oid,'SELECT'\)/);
  assert.match(health, /rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin/);
  assert.match(ui, /权限审计/);
  assert.match(retirement, /access-control-domain-retired-v1/);
  assert.equal((retirement.match(/CREATE TRIGGER `access_control_retired_[^`]+_guard`/g) ?? []).length, 6);
  assert.match(runtime, /--listen=127\.0\.0\.1:8101/);
  assert.match(runtime, /--listen=127\.0\.0\.1:8102/);
  assert.match(runtime, /ALTER ROLE teruisi_access_control_reader SET default_transaction_read_only=on/);
  assert.match(runtime, /"access_control_users": \("SELECT", "INSERT", "UPDATE"\)/);
  assert.match(runtime, /access_control_permission_audits','sequence/);
  assert.match(baseRuntime, /tools\\django-access-control\.ps1/);
  assert.match(baseRuntime, /TERUISI_DJANGO_ACCESS_CONTROL_AUTHORITY_EPOCH/);
  assert.match(baseRuntime, /AccessControlStartupEnabledPath/);
  assert.match(workerRuntime, /accessControl = Test-DjangoDomainReady/);
});
