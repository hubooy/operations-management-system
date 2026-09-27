import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoMarketServiceResponseError,
  MARKET_COMMANDS_PATH,
  MARKET_CONSUMER_QUERY_PATH,
  MARKET_IMPORTS_PATH,
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "../lib/django/market-service";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "market-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8031",
  writerBaseUrl: "http://127.0.0.1:8032",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};

function verifySignature(request: Request, path: string) {
  const envelope = request.headers.get("x-teruisi-principal")!;
  const canonical = [
    "v1",
    request.headers.get("x-teruisi-timestamp")!,
    request.headers.get("x-teruisi-request-id")!,
    "POST",
    path,
    "",
    request.headers.get("x-teruisi-content-sha256")!,
    envelope,
  ].join("\n");
  assert.equal(
    request.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
}

test("market reader signs a bounded revisioned request and preserves exact payload", async () => {
  let observed: Request | undefined;
  const payload = { operation: "overview", view: "full", page: 1, pageSize: 20, filters: null };
  const result = await requestDjangoMarketService<Record<string, unknown>>(
    principal,
    { path: MARKET_QUERIES_PATH, payload, service: "reader" },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "market-reader-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json(
          { view: "full", items: [] },
          { headers: { "x-market-data-revision": "9:abcdef123456" } },
        );
      },
    },
  );
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, config.readerBaseUrl);
  assert.deepEqual(await observed.json(), payload);
  verifySignature(observed, MARKET_QUERIES_PATH);
  assert.equal(result.revision, "9:abcdef123456");
});

test("market consumer remains reader-only while commands and imports use the writer", async () => {
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    observed.push(request);
    return Response.json(
      { ok: true },
      { headers: { "x-market-data-revision": "10:abcdef123456", "x-teruisi-write-replay": "1" } },
    );
  };
  await requestDjangoMarketService(
    principal,
    { path: MARKET_CONSUMER_QUERY_PATH, payload: { operation: "projection_status" }, service: "reader" },
    { config, fetchImpl },
  );
  const command = await requestDjangoMarketService(
    principal,
    { path: MARKET_COMMANDS_PATH, payload: { contractVersion: "market-command-v1", domain: "master", command: {} }, service: "writer" },
    { config, fetchImpl },
  );
  await requestDjangoMarketService(
    principal,
    { path: MARKET_IMPORTS_PATH, payload: { contractVersion: "market-import-v1" }, service: "writer" },
    { config, fetchImpl },
  );
  assert.equal(new URL(observed[0]!.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed[1]!.url).origin, config.writerBaseUrl);
  assert.equal(new URL(observed[2]!.url).origin, config.writerBaseUrl);
  assert.equal(command.replayed, true);
});

test("market reader/writer allowlists and loopback configuration fail closed before fetch", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const input of [
    { path: MARKET_QUERIES_PATH, payload: {}, service: "writer" },
    { path: MARKET_IMPORTS_PATH, payload: {}, service: "reader" },
    { path: "/api/market/unknown", payload: {}, service: "reader" },
  ] as const) {
    await assert.rejects(
      requestDjangoMarketService(principal, input, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  for (const unsafe of [
    { ...config, writerBaseUrl: config.readerBaseUrl },
    { ...config, readerBaseUrl: "http://example.com" },
    { ...config, internalSecret: "short" },
  ]) {
    await assert.rejects(
      requestDjangoMarketService(
        principal,
        { path: MARKET_QUERIES_PATH, payload: {}, service: "reader" },
        { config: unsafe, fetchImpl: neverFetch },
      ),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
});

test("market upstream errors and malformed success responses never fall back to D1", async () => {
  await assert.rejects(
    requestDjangoMarketService(
      principal,
      { path: MARKET_QUERIES_PATH, payload: {}, service: "reader" },
      {
        config,
        fetchImpl: async () => Response.json(
          { message: "筛选项无效", code: "invalid_request", details: ["bad"] },
          { status: 400 },
        ),
      },
    ),
    (error: unknown) => error instanceof DjangoMarketServiceResponseError
      && error.status === 400
      && error.upstreamCode === "invalid_request",
  );
  for (const response of [
    () => Response.json({ items: [] }),
    () => Response.json({ items: [] }, { headers: { "x-market-data-revision": "9:3" } }),
    () => new Response("not-json", { status: 502 }),
  ]) {
    await assert.rejects(
      requestDjangoMarketService(
        principal,
        { path: MARKET_QUERIES_PATH, payload: {}, service: "reader" },
        { config, fetchImpl: async () => response() },
      ),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable",
    );
  }
});
