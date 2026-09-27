import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoSalesServiceResponseError,
  SALES_IMPORTS_PATH,
  SALES_RAW_UPLOADS_PATH,
  SALES_STAGED_IMPORTS_PATH,
  requestDjangoSalesService,
  type DjangoSalesServiceConfig,
} from "../lib/django/sales-writer";
import { PublicApiError } from "../lib/http/api-error";

const secret = "test-only-django-sales-writer-secret-minimum";
const principal: AppPrincipal = {
  email: "admin@example.com",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const config: DjangoSalesServiceConfig = {
  readerBaseUrl: "http://127.0.0.1:8001",
  writerBaseUrl: "http://127.0.0.1:8002",
  internalSecret: secret,
  timeoutMs: 1_000,
  maxRequestBytes: 64 * 1024,
  maxResponseBytes: 64 * 1024,
};

function jsonResponse(value: unknown, init: ResponseInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(value), { ...init, headers });
}

test("sales writer sends the signed JSON body only to the isolated writer service", async () => {
  const payload = { action: "complete", sessionId: "session-1" };
  let capturedUrl = "";
  let capturedInit: RequestInit | undefined;
  const result = await requestDjangoSalesService<{ ok: boolean }>(principal, {
    method: "POST",
    path: SALES_STAGED_IMPORTS_PATH,
    payload,
    service: "writer",
  }, {
    config,
    now: () => 1_788_000_000_000,
    requestId: () => "writer-request-1",
    fetchImpl: async (input, init) => {
      capturedUrl = String(input);
      capturedInit = init;
      return jsonResponse({ ok: true }, { status: 201, headers: { "x-teruisi-write-replay": "1" } });
    },
  });

  assert.equal(capturedUrl, `http://127.0.0.1:8002${SALES_STAGED_IMPORTS_PATH}`);
  assert.equal(capturedInit?.method, "POST");
  assert.equal(capturedInit?.redirect, "manual");
  const body = Buffer.from(capturedInit?.body as Uint8Array).toString("utf8");
  assert.equal(body, JSON.stringify(payload));
  const headers = new Headers(capturedInit?.headers);
  const bodyDigest = headers.get("x-teruisi-content-sha256");
  const encodedPrincipal = headers.get("x-teruisi-principal");
  assert.ok(bodyDigest);
  assert.ok(encodedPrincipal);
  const canonical = [
    "v1",
    "1788000000",
    "writer-request-1",
    "POST",
    SALES_STAGED_IMPORTS_PATH,
    "",
    bodyDigest,
    encodedPrincipal,
  ].join("\n");
  assert.equal(
    headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
  assert.equal(result.status, 201);
  assert.equal(result.replayed, true);
});

test("sales import reads use the read-only service and preserve the exact query", async () => {
  let capturedUrl = "";
  await requestDjangoSalesService(principal, {
    method: "GET",
    path: SALES_IMPORTS_PATH,
    query: new URLSearchParams({ page: "2", pageSize: "20" }),
    service: "reader",
  }, {
    config,
    requestId: () => "reader-request-1",
    fetchImpl: async (input) => {
      capturedUrl = String(input);
      return jsonResponse({ items: [] });
    },
  });
  assert.equal(capturedUrl, "http://127.0.0.1:8001/api/sales/imports?page=2&pageSize=20");
});

test("sales upload coordination reads use the isolated writer service", async () => {
  const uploadId = "11111111-1111-4111-8111-111111111111";
  let capturedUrl = "";
  await requestDjangoSalesService(principal, {
    method: "GET",
    path: SALES_RAW_UPLOADS_PATH,
    query: new URLSearchParams({ uploadId }),
    service: "writer",
  }, {
    config,
    requestId: () => "upload-read-request-1",
    fetchImpl: async (input) => {
      capturedUrl = String(input);
      return jsonResponse({ id: uploadId, status: "uploading" });
    },
  });
  assert.equal(
    capturedUrl,
    `http://127.0.0.1:8002${SALES_RAW_UPLOADS_PATH}?uploadId=${uploadId}`,
  );
});

test("writer-owned sales coordination paths fail closed on the reader service", async () => {
  let fetched = false;
  for (const path of [SALES_RAW_UPLOADS_PATH, SALES_STAGED_IMPORTS_PATH]) {
    await assert.rejects(
      requestDjangoSalesService(principal, {
        method: "GET",
        path,
        query: new URLSearchParams({ uploadId: "blocked" }),
        service: "reader",
      }, {
        config,
        fetchImpl: async () => {
          fetched = true;
          return jsonResponse({ ok: true });
        },
      }),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable",
      path,
    );
  }
  assert.equal(fetched, false);
});

test("controlled Django import rejections retain their structured payload", async () => {
  await assert.rejects(
    requestDjangoSalesService(principal, {
      method: "POST",
      path: SALES_STAGED_IMPORTS_PATH,
      payload: { action: "complete", sessionId: "missing" },
      service: "writer",
    }, {
      config,
      fetchImpl: async () => jsonResponse({
        ok: false,
        status: "rejected",
        message: "会话不存在",
        code: "not_found",
        errors: [{ code: "not_found", message: "会话不存在" }],
      }, { status: 404 }),
    }),
    (error: unknown) => error instanceof DjangoSalesServiceResponseError
      && error.status === 404
      && error.payload.status === "rejected",
  );
});

test("writer configuration and request sizes fail closed before fetch", async () => {
  for (const writerBaseUrl of [
    "http://sales.example.com",
    "http://127.0.0.1.evil.example.com:8002",
    "https://user:password@sales.example.com",
    "https://sales.example.com/subpath",
  ]) {
    let fetched = false;
    await assert.rejects(
      requestDjangoSalesService(principal, {
        method: "POST",
        path: SALES_STAGED_IMPORTS_PATH,
        payload: { action: "complete", sessionId: "session-1" },
        service: "writer",
      }, {
        config: { ...config, writerBaseUrl },
        fetchImpl: async () => {
          fetched = true;
          return jsonResponse({ ok: true });
        },
      }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
      writerBaseUrl,
    );
    assert.equal(fetched, false);
  }

  let fetched = false;
  await assert.rejects(
    requestDjangoSalesService(principal, {
      method: "PUT",
      path: SALES_STAGED_IMPORTS_PATH,
      payload: { rows: ["x".repeat(2_000)] },
      service: "writer",
    }, {
      config: { ...config, maxRequestBytes: 128 },
      fetchImpl: async () => {
        fetched = true;
        return jsonResponse({ ok: true });
      },
    }),
    (error: unknown) => error instanceof PublicApiError
      && error.status === 413
      && error.code === "payload_too_large",
  );
  assert.equal(fetched, false);
});
