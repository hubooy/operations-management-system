import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoProductsServiceResponseError,
  PRODUCTS_CONSUMER_QUERY_PATH,
  PRODUCTS_IMPORTS_PATH,
  PRODUCTS_INVENTORY_PROJECTION_PATH,
  PRODUCTS_SUMMARY_PATH,
  requestDjangoProductsJson,
} from "../lib/django/products-service";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "products-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8041",
  writerBaseUrl: "http://127.0.0.1:8042",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};

function verifySignature(request: Request, path: string, query: string) {
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

test("products reader signs the exact GET query and requires a product revision", async () => {
  let observed: Request | undefined;
  const rawQuery = "range=last30&page=1&pageSize=50";
  const result = await requestDjangoProductsJson<Record<string, unknown>>(
    principal,
    { method: "GET", path: PRODUCTS_SUMMARY_PATH, rawQuery, service: "reader" },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "products-reader-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json(
          { projection: "full", items: [] },
          { headers: { "x-product-data-revision": "9:abcdef123456" } },
        );
      },
    },
  );
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed.url).search.slice(1), rawQuery);
  verifySignature(observed, PRODUCTS_SUMMARY_PATH, rawQuery);
  assert.equal(result.revision, "9:abcdef123456");
});

test("products consumer stays reader-only while imports and projection use writer", async () => {
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    observed.push(new Request(input, init));
    return Response.json(
      { ok: true },
      { headers: { "x-product-data-revision": "10:abcdef123456", "x-teruisi-write-replay": "1" } },
    );
  };
  await requestDjangoProductsJson(
    principal,
    { method: "POST", path: PRODUCTS_CONSUMER_QUERY_PATH, service: "reader", payload: { operation: "import_batch_search", query: "", offset: 0, limit: 10 } },
    { config, fetchImpl },
  );
  const imported = await requestDjangoProductsJson(
    principal,
    { method: "POST", path: PRODUCTS_IMPORTS_PATH, service: "writer", payload: { kind: "import" } },
    { config, fetchImpl },
  );
  await requestDjangoProductsJson(
    principal,
    { method: "POST", path: PRODUCTS_INVENTORY_PROJECTION_PATH, service: "writer", payload: { action: "begin_sync" } },
    { config, fetchImpl },
  );
  assert.equal(new URL(observed[0]!.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed[1]!.url).origin, config.writerBaseUrl);
  assert.equal(new URL(observed[2]!.url).origin, config.writerBaseUrl);
  assert.equal(imported.replayed, true);
});

test("products read/write allowlists and loopback configuration fail closed", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const input of [
    { method: "GET", path: PRODUCTS_SUMMARY_PATH, service: "writer" },
    { method: "POST", path: PRODUCTS_IMPORTS_PATH, payload: {}, service: "reader" },
    { method: "GET", path: "/api/products/unknown", service: "reader" },
  ] as const) {
    await assert.rejects(
      requestDjangoProductsJson(principal, input, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  for (const unsafe of [
    { ...config, writerBaseUrl: config.readerBaseUrl },
    { ...config, readerBaseUrl: "http://example.com" },
    { ...config, internalSecret: "short" },
  ]) {
    await assert.rejects(
      requestDjangoProductsJson(
        principal,
        { method: "GET", path: PRODUCTS_SUMMARY_PATH, service: "reader" },
        { config: unsafe, fetchImpl: neverFetch },
      ),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
});

test("products upstream failures never fall back to D1", async () => {
  await assert.rejects(
    requestDjangoProductsJson(
      principal,
      { method: "GET", path: PRODUCTS_SUMMARY_PATH, service: "reader" },
      {
        config,
        fetchImpl: async () => Response.json(
          { error: "筛选项无效", code: "invalid_request" },
          { status: 400 },
        ),
      },
    ),
    (error: unknown) => error instanceof DjangoProductsServiceResponseError
      && error.status === 400
      && error.upstreamCode === "invalid_request",
  );
  for (const response of [
    () => Response.json({ items: [] }),
    () => Response.json({ items: [] }, { headers: { "x-product-data-revision": "9:3" } }),
    () => new Response("not-json", { status: 502 }),
  ]) {
    await assert.rejects(
      requestDjangoProductsJson(
        principal,
        { method: "GET", path: PRODUCTS_SUMMARY_PATH, service: "reader" },
        { config, fetchImpl: async () => response() },
      ),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable",
    );
  }
});
