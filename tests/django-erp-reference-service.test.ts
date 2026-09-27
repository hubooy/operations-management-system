import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  ERP_REFERENCE_CONSUMER_QUERY_PATH,
  ERP_REFERENCE_IMPORTS_PATH,
  ERP_REFERENCE_UPLOAD_CHUNK_PATH,
  requestDjangoErpReferenceBytes,
  requestDjangoErpReferenceJson,
} from "../lib/django/erp-reference-service";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test", displayName: "管理员", role: "admin", scope: null,
};
const secret = "erp-reference-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8091",
  writerBaseUrl: "http://127.0.0.1:8092",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 64 * 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};

function verifySignature(request: Request, path: string, query = "") {
  const canonical = [
    "v1", request.headers.get("x-teruisi-timestamp")!,
    request.headers.get("x-teruisi-request-id")!, request.method, path, query,
    request.headers.get("x-teruisi-content-sha256")!,
    request.headers.get("x-teruisi-principal")!,
  ].join("\n");
  assert.equal(
    request.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
}

test("ERP reader and writer use isolated signed endpoints with revision fencing", async () => {
  const observed: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    observed.push(request);
    return Response.json(
      request.method === "GET" ? { items: [], pagination: { total: 0 } } : { ok: true },
      { headers: {
        "x-erp-reference-data-revision": "7:abcdef123456",
        "x-teruisi-write-replay": request.method === "POST" ? "1" : "0",
      } },
    );
  };
  const query = "source=products&page=1&pageSize=50";
  const read = await requestDjangoErpReferenceJson(
    principal,
    { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader", rawQuery: query },
    { config, fetchImpl, now: () => 1_800_000_000_000, requestId: () => "erp-reader-1" },
  );
  const write = await requestDjangoErpReferenceJson(
    principal,
    { method: "POST", path: ERP_REFERENCE_IMPORTS_PATH, service: "writer", payload: { version: "erp-reference-normalized-v1" } },
    { config, fetchImpl, now: () => 1_800_000_000_000, requestId: () => "erp-writer-1" },
  );
  assert.equal(new URL(observed[0]!.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed[1]!.url).origin, config.writerBaseUrl);
  verifySignature(observed[0]!, ERP_REFERENCE_IMPORTS_PATH, query);
  verifySignature(observed[1]!, ERP_REFERENCE_IMPORTS_PATH);
  assert.equal(read.revision, "7:abcdef123456");
  assert.equal(write.replayed, true);
});

test("ERP allowlist, mode, topology, upstream response and network failures fail closed", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const input of [
    { method: "POST", path: ERP_REFERENCE_CONSUMER_QUERY_PATH, service: "writer", payload: {} },
    { method: "POST", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader", payload: {} },
    { method: "GET", path: "/api/erp-reference/unknown", service: "reader" },
  ] as const) {
    await assert.rejects(
      requestDjangoErpReferenceJson(principal, input, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  for (const unsafe of [
    { ...config, writerBaseUrl: config.readerBaseUrl },
    { ...config, readerBaseUrl: "http://example.com" },
    { ...config, internalSecret: "short" },
  ]) {
    await assert.rejects(
      requestDjangoErpReferenceJson(
        principal,
        { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader" },
        { config: unsafe, fetchImpl: neverFetch },
      ),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  for (const fetchImpl of [
    async () => Response.json({ items: [] }),
    async () => new Response("not-json", { status: 502 }),
    async () => { throw new Error("connection refused"); },
  ] satisfies typeof fetch[]) {
    await assert.rejects(
      requestDjangoErpReferenceJson(
        principal,
        { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader" },
        { config, fetchImpl },
      ),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
});

test("ERP chunk read is byte-bounded and digest-bound", async () => {
  const bytes = new TextEncoder().encode("ERP chunk");
  const result = await requestDjangoErpReferenceBytes(principal, {
    "x-upload-id": "8b603366-3dfd-4c85-9a97-9d5b73de4bc5",
    "x-chunk-index": "0",
    "x-upload-owner-token": "bounded-owner-token",
  }, {
    config,
    fetchImpl: async () => new Response(bytes, { headers: {
      "content-type": "application/octet-stream",
      "x-chunk-sha256": createHash("sha256").update(bytes).digest("hex"),
    } }),
  });
  assert.deepEqual(result.bytes, bytes);
});
