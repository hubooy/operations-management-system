import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import { PublicApiError } from "../lib/http/api-error";
import {
  createSalesGatewayAuthHeaders,
  routeDjangoSalesReadRequest,
  salesGatewayBodySha256,
  salesGatewayConfigFromEnvironment,
} from "../lib/django/sales-gateway";

const secret = "test-only-django-sales-secret-32-bytes-minimum";
const revision = "17:9";
const principal: AppPrincipal = {
  email: "analyst@example.com",
  displayName: "分析员",
  role: "analyst",
  scope: {
    warehouses: ["主仓"],
    channels: ["直营网"],
    platforms: ["京东"],
  },
};

const config = {
  djangoBaseUrl: "http://127.0.0.1:8001",
  internalSecret: secret,
  timeoutMs: 1_000,
  maxResponseBytes: 64 * 1024,
};

function jsonResponse(value: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  if (!headers.has("content-type")) {
    headers.set("content-type", "application/json; charset=utf-8");
  }
  return new Response(JSON.stringify(value), { ...init, headers });
}

function request(rawQuery = "startDate=2026-08-01&endDate=2026-08-02"): Request {
  return new Request(`http://localhost/api/sales/category-analysis?${rawQuery}`, {
    headers: {
      authorization: "Bearer must-not-leave-the-bff",
      cookie: "session=must-not-leave-the-bff",
      "x-untrusted": "client-value",
    },
  });
}

test("sales gateway signature uses the exact v1 canonical contract", async () => {
  const timestamp = 1_788_000_000;
  const requestId = "request-fixed-1";
  const rawQuery = "category=%E9%A5%AE%E6%B0%B4&outlet=A%2FB";
  const headers = await createSalesGatewayAuthHeaders({
    secret,
    principal,
    method: "get",
    path: "/api/sales/category-analysis",
    rawQuery,
    timestamp,
    requestId,
  });

  const encodedPrincipal = headers.get("x-teruisi-principal");
  assert.ok(encodedPrincipal);
  assert.equal(encodedPrincipal.includes("="), false);
  assert.deepEqual(
    JSON.parse(Buffer.from(encodedPrincipal, "base64url").toString("utf8")),
    principal,
  );
  const bodyHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
  const canonical = [
    "v1",
    String(timestamp),
    requestId,
    "GET",
    "/api/sales/category-analysis",
    rawQuery,
    bodyHash,
    encodedPrincipal,
  ].join("\n");
  const expected = createHmac("sha256", secret).update(canonical).digest("hex");
  assert.equal(headers.get("x-teruisi-signature"), `v1=${expected}`);
  assert.equal(headers.get("x-teruisi-content-sha256"), bodyHash);
});

test("write signatures require an exact body digest", async () => {
  const body = new TextEncoder().encode(JSON.stringify({ action: "complete" }));
  const bodySha256 = await salesGatewayBodySha256(body);
  const headers = await createSalesGatewayAuthHeaders({
    secret,
    principal: { ...principal, role: "admin", scope: null },
    method: "POST",
    path: "/api/sales/imports/staged",
    rawQuery: "",
    bodySha256,
    timestamp: 1_788_000_001,
    requestId: "request-write-1",
  });
  assert.equal(headers.get("x-teruisi-content-sha256"), bodySha256);

  await assert.rejects(
    createSalesGatewayAuthHeaders({
      secret,
      principal,
      method: "POST",
      path: "/api/sales/imports/staged",
      rawQuery: "",
      timestamp: 1_788_000_001,
      requestId: "request-write-2",
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("reader configuration has no legacy or shadow mode", () => {
  const parsed = salesGatewayConfigFromEnvironment({
    TERUISI_DJANGO_SALES_READER_BASE_URL: "http://127.0.0.1:8001",
    TERUISI_DJANGO_INTERNAL_SECRET: secret,
  });
  assert.equal(parsed.djangoBaseUrl, "http://127.0.0.1:8001");
  assert.equal("mode" in parsed, false);
  assert.equal(salesGatewayConfigFromEnvironment({}).djangoBaseUrl, undefined);
});

test("Django-only reads sign the exact query and strip browser credentials", async () => {
  let upstreamRequest: Request | undefined;
  const response = await routeDjangoSalesReadRequest({
    request: request("category=%E9%A5%AE%E6%B0%B4&outlet=A%2FB"),
    principal,
    config,
    now: () => 1_788_000_000_000,
    requestId: () => "request-fixed-2",
    fetchImpl: async (input, init) => {
      upstreamRequest = new Request(input, init);
      return jsonResponse({ source: "postgresql" }, {
        headers: {
          "x-sales-data-revision": revision,
          "x-sales-source-revision": revision,
          "set-cookie": "must-not-propagate=1",
          "x-internal": "must-not-propagate",
        },
      });
    },
  });

  assert.ok(upstreamRequest);
  assert.equal(upstreamRequest.url, "http://127.0.0.1:8001/api/sales/category-analysis?category=%E9%A5%AE%E6%B0%B4&outlet=A%2FB");
  assert.equal(upstreamRequest.headers.has("authorization"), false);
  assert.equal(upstreamRequest.headers.has("cookie"), false);
  assert.equal(response.headers.has("set-cookie"), false);
  assert.equal(response.headers.has("x-internal"), false);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { source: "postgresql" });
});

test("Django-only reads fail closed on inconsistent revision or upstream errors", async () => {
  for (const fetchImpl of [
    async () => jsonResponse({ source: "postgresql" }, {
      headers: {
        "x-sales-data-revision": revision,
        "x-sales-source-revision": "stale",
      },
    }),
    async () => jsonResponse({ code: "unavailable" }, { status: 503 }),
    async () => new Response("not-json", { headers: { "content-type": "text/plain" } }),
  ]) {
    await assert.rejects(
      routeDjangoSalesReadRequest({ request: request(), principal, config, fetchImpl }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
});

test("reader rejects non-loopback HTTP and non-GET requests", async () => {
  await assert.rejects(
    routeDjangoSalesReadRequest({
      request: request(),
      principal,
      config: { ...config, djangoBaseUrl: "http://example.com" },
      fetchImpl: async () => jsonResponse({}),
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    routeDjangoSalesReadRequest({
      request: new Request("http://localhost/api/sales/summary", { method: "POST" }),
      principal,
      config,
      fetchImpl: async () => jsonResponse({}),
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});
