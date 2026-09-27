import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoInventoryServiceResponseError,
  INVENTORY_CONSUMER_QUERY_PATH,
  INVENTORY_GUANGDONG_PATH,
  INVENTORY_IMPORTS_PATH,
  INVENTORY_OVERVIEW_PATH,
  INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH,
  INVENTORY_REPLENISHMENT_DINGTALK_PATH,
  INVENTORY_REPLENISHMENT_PATH,
  INVENTORY_WAREHOUSE_MAPPINGS_PATH,
  requestDjangoInventoryBytes,
  requestDjangoInventoryJson,
} from "../lib/django/inventory-service";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "inventory-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8051",
  writerBaseUrl: "http://127.0.0.1:8052",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 64 * 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};

test("DingTalk authorization guidance reaches the caller without replaying the write", async () => {
  const message = "钉钉登录授权已失效，请管理员为系统绑定的钉钉账号重新授权后，再重试原备货计划。";
  let calls = 0;
  await assert.rejects(
    requestDjangoInventoryJson(
      principal,
      { method: "POST", path: INVENTORY_REPLENISHMENT_DINGTALK_PATH, service: "writer", payload: { planId: "plan-1" } },
      {
        config,
        fetchImpl: async () => {
          calls += 1;
          return Response.json({ error: message, code: "service_unavailable" }, { status: 503 });
        },
      },
    ),
    (error: unknown) => {
      assert.ok(error instanceof DjangoInventoryServiceResponseError);
      assert.equal(error.message, message);
      assert.equal(error.status, 503);
      return true;
    },
  );
  assert.equal(calls, 1);
});

function verifySignature(request: Request, path: string, query = "") {
  const canonical = [
    "v1",
    request.headers.get("x-teruisi-timestamp")!,
    request.headers.get("x-teruisi-request-id")!,
    request.method,
    path,
    query,
    request.headers.get("x-teruisi-content-sha256")!,
    request.headers.get("x-teruisi-principal")!,
  ].join("\n");
  assert.equal(
    request.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
}

test("inventory reader signs exact queries and requires a bounded revision", async () => {
  let observed: Request | undefined;
  const rawQuery = "view=overview&page=1&pageSize=50";
  const result = await requestDjangoInventoryJson<Record<string, unknown>>(
    principal,
    { method: "GET", path: INVENTORY_OVERVIEW_PATH, rawQuery, service: "reader" },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "inventory-reader-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json(
          { items: [] },
          { headers: { "x-inventory-data-revision": "9:abcdef123456" } },
        );
      },
    },
  );
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed.url).search.slice(1), rawQuery);
  verifySignature(observed, INVENTORY_OVERVIEW_PATH, rawQuery);
  assert.equal(result.revision, "9:abcdef123456");
});

test("inventory consumers stay reader-only while imports and plans use writer", async () => {
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    observed.push(new Request(input, init));
    return Response.json(
      { ok: true },
      { headers: { "x-inventory-data-revision": "10:abcdef123456", "x-teruisi-write-replay": "1" } },
    );
  };
  await requestDjangoInventoryJson(
    principal,
    { method: "POST", path: INVENTORY_CONSUMER_QUERY_PATH, service: "reader", payload: { operation: "freshness" } },
    { config, fetchImpl },
  );
  const imported = await requestDjangoInventoryJson(
    principal,
    { method: "POST", path: INVENTORY_IMPORTS_PATH, service: "writer", payload: { dataset: "stock" } },
    { config, fetchImpl },
  );
  await requestDjangoInventoryJson(
    principal,
    { method: "PATCH", path: INVENTORY_REPLENISHMENT_PATH, service: "writer", payload: { id: "plan-1", status: "confirmed" } },
    { config, fetchImpl },
  );
  await requestDjangoInventoryJson(
    principal,
    { method: "POST", path: INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH, service: "writer", payload: { action: "preview", planIds: ["plan-1"] } },
    { config, fetchImpl },
  );
  assert.equal(new URL(observed[0]!.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed[1]!.url).origin, config.writerBaseUrl);
  assert.equal(new URL(observed[2]!.url).origin, config.writerBaseUrl);
  assert.equal(new URL(observed[3]!.url).origin, config.writerBaseUrl);
  assert.equal(imported.replayed, true);
  verifySignature(observed[1]!, INVENTORY_IMPORTS_PATH);
});

test("warehouse mappings use the inventory reader for reads and writer for edits", async () => {
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    observed.push(new Request(input, init));
    return Response.json(
      { rows: [], mappingRevision: "a".repeat(64), updatedAt: null, updatedBy: null },
      { headers: { "x-inventory-data-revision": "10:abcdef123456" } },
    );
  };
  await requestDjangoInventoryJson(
    principal,
    { method: "GET", path: INVENTORY_WAREHOUSE_MAPPINGS_PATH, service: "reader" },
    { config, fetchImpl },
  );
  await requestDjangoInventoryJson(
    principal,
    {
      method: "PUT",
      path: INVENTORY_WAREHOUSE_MAPPINGS_PATH,
      service: "writer",
      payload: { expectedMappingRevision: "a".repeat(64), mappings: [] },
    },
    { config, fetchImpl },
  );
  assert.equal(new URL(observed[0]!.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed[1]!.url).origin, config.writerBaseUrl);
  verifySignature(observed[1]!, INVENTORY_WAREHOUSE_MAPPINGS_PATH);
});

test("Guangdong item edits are allowlisted on the inventory writer", async () => {
  let observed: Request | undefined;
  await requestDjangoInventoryJson(
    principal,
    {
      method: "PATCH",
      path: `${INVENTORY_GUANGDONG_PATH}/items`,
      service: "writer",
      payload: { action: "item", productCode: "SKU-1", version: "1:abcdef123456/sales:1" },
    },
    {
      config,
      requestId: () => "guangdong-item-edit-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json(
          { status: "saved" },
          { headers: { "x-inventory-data-revision": "11:abcdef123456" } },
        );
      },
    },
  );
  assert.ok(observed);
  assert.equal(observed.method, "PATCH");
  assert.equal(new URL(observed.url).origin, config.writerBaseUrl);
  verifySignature(observed, `${INVENTORY_GUANGDONG_PATH}/items`);
});

test("inventory allowlists and loopback-only configuration fail closed", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const input of [
    { method: "GET", path: INVENTORY_OVERVIEW_PATH, service: "writer" },
    { method: "POST", path: INVENTORY_IMPORTS_PATH, payload: {}, service: "reader" },
    { method: "GET", path: "/api/inventory/unknown", service: "reader" },
  ] as const) {
    await assert.rejects(
      requestDjangoInventoryJson(principal, input, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  for (const unsafe of [
    { ...config, writerBaseUrl: config.readerBaseUrl },
    { ...config, readerBaseUrl: "http://example.com" },
    { ...config, internalSecret: "short" },
  ]) {
    await assert.rejects(
      requestDjangoInventoryJson(
        principal,
        { method: "GET", path: INVENTORY_OVERVIEW_PATH, service: "reader" },
        { config: unsafe, fetchImpl: neverFetch },
      ),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
});

test("inventory upstream failures never fall back to D1", async () => {
  await assert.rejects(
    requestDjangoInventoryJson(
      principal,
      { method: "GET", path: INVENTORY_OVERVIEW_PATH, service: "reader" },
      {
        config,
        fetchImpl: async () => Response.json(
          { error: "筛选项无效", code: "invalid_request" },
          { status: 400 },
        ),
      },
    ),
    (error: unknown) => error instanceof DjangoInventoryServiceResponseError
      && error.status === 400
      && error.upstreamCode === "invalid_request",
  );
  for (const response of [
    () => Response.json({ items: [] }),
    () => Response.json({ items: [] }, { headers: { "x-inventory-data-revision": "9:3" } }),
    () => new Response("not-json", { status: 502 }),
  ]) {
    await assert.rejects(
      requestDjangoInventoryJson(
        principal,
        { method: "GET", path: INVENTORY_OVERVIEW_PATH, service: "reader" },
        { config, fetchImpl: async () => response() },
      ),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable",
    );
  }
});

test("inventory chunk reads are bounded by the internal service timeout", async () => {
  const headers = {
    "x-upload-id": "8b603366-3dfd-4c85-9a97-9d5b73de4bc5",
    "x-chunk-index": "0",
    "x-upload-owner-token": "bounded-owner-token",
  };
  await assert.rejects(
    requestDjangoInventoryBytes(principal, headers, {
      config: { ...config, timeoutMs: 10 },
      fetchImpl: async (_input, init) => new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        assert.ok(signal);
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      }),
    }),
    (error: unknown) => error instanceof PublicApiError
      && error.status === 503
      && error.code === "service_unavailable",
  );

  const bytes = new TextEncoder().encode("bounded inventory chunk");
  const result = await requestDjangoInventoryBytes(principal, headers, {
    config,
    fetchImpl: async () => new Response(bytes, {
      headers: {
        "content-type": "application/octet-stream",
        "x-chunk-sha256": createHash("sha256").update(bytes).digest("hex"),
      },
    }),
  });
  assert.deepEqual(result.bytes, bytes);
});
