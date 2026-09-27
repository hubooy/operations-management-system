import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoNetshopServiceResponseError,
  NETSHOP_CONSUMER_QUERY_PATH,
  NETSHOP_IMPORTS_PATH,
  NETSHOP_OVERVIEW_PATH,
  requestDjangoNetshopService,
} from "../lib/django/netshop-service";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "netshop-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8021",
  writerBaseUrl: "http://127.0.0.1:8022",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};

function verifySignature(request: Request, path: string, query: string) {
  const envelope = request.headers.get("x-teruisi-principal")!;
  const timestamp = request.headers.get("x-teruisi-timestamp")!;
  const requestId = request.headers.get("x-teruisi-request-id")!;
  const bodyHash = request.headers.get("x-teruisi-content-sha256")!;
  const canonical = [
    "v1",
    timestamp,
    requestId,
    request.method,
    path,
    query,
    bodyHash,
    envelope,
  ].join("\n");
  assert.equal(
    request.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
}

test("netshop reader signs the exact query and requires the revisioned reader response", async () => {
  const observed: Request[] = [];
  const query = new URLSearchParams("platform=%E4%BA%AC%E4%B8%9C&shop=%E6%B5%8B%E8%AF%95%E5%BA%97");
  const result = await requestDjangoNetshopService<Record<string, unknown>>(
    principal,
    { method: "GET", path: NETSHOP_OVERVIEW_PATH, query, service: "reader" },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "netshop-read-1",
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json(
          { datasets: [], filters: {} },
          { headers: { "x-netshop-data-revision": "9:abcdef123456" } },
        );
      },
    },
  );
  const request = observed[0];
  assert.ok(request);
  assert.equal(new URL(request.url).origin, config.readerBaseUrl);
  verifySignature(request, NETSHOP_OVERVIEW_PATH, query.toString());
  assert.equal(result.revision, "9:abcdef123456");
  assert.deepEqual(result.data, { datasets: [], filters: {} });
});

test("netshop consumer POST stays on the reader while normalized imports stay on the writer", async () => {
  const observed: Request[] = [];
  const consumerPayload = {
    operation: "market_projection_page",
    offset: 0,
    limit: 1_000,
    expectedRevision: null,
  };
  await requestDjangoNetshopService(
    principal,
    {
      method: "POST",
      path: NETSHOP_CONSUMER_QUERY_PATH,
      payload: consumerPayload,
      service: "reader",
    },
    {
      config,
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json(
          { operation: consumerPayload.operation, data: { rows: [], total: 0, truncated: false } },
          { headers: { "x-netshop-data-revision": "9:abcdef123456" } },
        );
      },
    },
  );
  const importPayload = { schemaVersion: "netshop-normalized-v1", disposition: "rejected" };
  const imported = await requestDjangoNetshopService<Record<string, unknown>>(
    principal,
    {
      method: "POST",
      path: NETSHOP_IMPORTS_PATH,
      payload: importPayload,
      service: "writer",
      acceptedErrorStatuses: [422],
    },
    {
      config,
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json(
          { ok: false, status: "rejected" },
          { status: 422, headers: { "x-teruisi-write-replay": "1" } },
        );
      },
    },
  );
  assert.equal(new URL(observed[0]!.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed[1]!.url).origin, config.writerBaseUrl);
  assert.deepEqual(await observed[0]!.json(), consumerPayload);
  assert.deepEqual(await observed[1]!.json(), importPayload);
  assert.equal(imported.replayed, true);
  assert.equal(imported.status, 422);
});

test("netshop reader and writer surfaces are disjoint and reject unsafe configuration before fetch", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const input of [
    { method: "POST", path: NETSHOP_OVERVIEW_PATH, payload: {}, service: "writer" },
    { method: "GET", path: NETSHOP_IMPORTS_PATH, service: "writer" },
    { method: "GET", path: "/api/netshop/unknown", service: "reader" },
  ] as const) {
    await assert.rejects(
      requestDjangoNetshopService(principal, input, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  await assert.rejects(
    requestDjangoNetshopService(
      principal,
      { method: "GET", path: NETSHOP_OVERVIEW_PATH, service: "reader" },
      { config: { ...config, writerBaseUrl: config.readerBaseUrl }, fetchImpl: neverFetch },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    requestDjangoNetshopService(
      principal,
      { method: "GET", path: NETSHOP_OVERVIEW_PATH, service: "reader" },
      { config: { ...config, readerBaseUrl: "http://example.com" }, fetchImpl: neverFetch },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("netshop upstream failures never fall back to D1", async () => {
  await assert.rejects(
    requestDjangoNetshopService(
      principal,
      { method: "GET", path: NETSHOP_OVERVIEW_PATH, service: "reader" },
      {
        config,
        fetchImpl: async () => Response.json(
          { error: "筛选项无效", code: "invalid_request", details: ["bad"] },
          { status: 400 },
        ),
      },
    ),
    (error: unknown) => error instanceof DjangoNetshopServiceResponseError
      && error.status === 400
      && error.upstreamCode === "invalid_request",
  );
  for (const response of [
    () => Response.json({ datasets: [] }),
    () => Response.json({ datasets: [] }, { headers: { "x-netshop-data-revision": "9:3" } }),
    () => new Response("not-json", { status: 502 }),
  ]) {
    await assert.rejects(
      requestDjangoNetshopService(
        principal,
        { method: "GET", path: NETSHOP_OVERVIEW_PATH, service: "reader" },
        { config, fetchImpl: async () => response() },
      ),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable",
    );
  }
});
