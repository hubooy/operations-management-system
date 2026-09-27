import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  DjangoFinanceServiceResponseError,
  financeBackendModeFromEnvironment,
  requestDjangoFinanceService,
  requestDjangoFinanceRawWorkbookAttestation,
} from "../lib/django/finance-service";
import { createSalesGatewayAuthHeaders } from "../lib/django/sales-gateway";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "finance-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8011",
  writerBaseUrl: "http://127.0.0.1:8012",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxRequestBytes: 1024 * 1024,
  maxResponseBytes: 1024 * 1024,
};

function verifySignature(request: Request, expectedPath: string, expectedQuery: string) {
  const principalEnvelope = request.headers.get("x-teruisi-principal")!;
  const timestamp = request.headers.get("x-teruisi-timestamp")!;
  const requestId = request.headers.get("x-teruisi-request-id")!;
  const bodyHash = request.headers.get("x-teruisi-content-sha256")!;
  const canonical = [
    "v1",
    timestamp,
    requestId,
    request.method,
    expectedPath,
    expectedQuery,
    bodyHash,
    principalEnvelope,
  ].join("\n");
  assert.equal(
    request.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
}

test("finance mode defaults to legacy and accepts only the staged cutover states", () => {
  assert.equal(financeBackendModeFromEnvironment({}), "django");
  for (const mode of ["legacy", "shadow"]) assert.throws(() => financeBackendModeFromEnvironment({ TERUISI_DJANGO_FINANCE_MODE: mode }));
  assert.equal(financeBackendModeFromEnvironment({ TERUISI_DJANGO_FINANCE_MODE: "django" }), "django");
  assert.throws(
    () => financeBackendModeFromEnvironment({ TERUISI_DJANGO_FINANCE_MODE: "fallback" }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("finance reader signs the exact path/query and cannot use the writer URL", async () => {
  const observed: Request[] = [];
  const result = await requestDjangoFinanceService<Record<string, unknown>>(
    principal,
    {
      method: "GET",
      path: "/api/finance/analysis",
      query: new URLSearchParams("month=2026-08&platform=%E4%BA%AC%E4%B8%9C"),
      service: "reader",
    },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "finance-read-1",
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json({ hasData: false }, {
          headers: { "x-finance-data-revision": "1:abcdef123456" },
        });
      },
    },
  );
  const request = observed[0];
  assert.ok(request);
  assert.equal(new URL(request.url).origin, "http://127.0.0.1:8011");
  assert.equal(request.method, "GET");
  verifySignature(request, "/api/finance/analysis", "month=2026-08&platform=%E4%BA%AC%E4%B8%9C");
  assert.equal(result.revision, "1:abcdef123456");
  assert.deepEqual(result.data, { hasData: false });
});

test("finance writer sends normalized JSON only to the writer and preserves replay status", async () => {
  const observed: Request[] = [];
  const payload = {
    schemaVersion: "finance-normalized-v1",
    disposition: "rejected",
    fileName: "bad.xlsx",
  };
  const result = await requestDjangoFinanceService<Record<string, unknown>>(
    principal,
    {
      method: "POST",
      path: "/api/finance/imports",
      payload,
      service: "writer",
      acceptedErrorStatuses: [422],
    },
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "finance-write-1",
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json(
          { ok: false, status: "rejected", message: "解析失败" },
          { status: 422, headers: { "x-teruisi-write-replay": "1" } },
        );
      },
    },
  );
  const request = observed[0];
  assert.ok(request);
  assert.equal(new URL(request.url).origin, "http://127.0.0.1:8012");
  assert.equal(request.method, "POST");
  assert.deepEqual(await request.json(), payload);
  verifySignature(request, "/api/finance/imports", "");
  assert.equal(result.status, 422);
  assert.equal(result.replayed, true);
});

test("raw workbook follow-up signs the exact bytes and only the private writer path", async () => {
  const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2, 3]);
  const batchId = "a".repeat(64);
  const observed: Request[] = [];
  const result = await requestDjangoFinanceRawWorkbookAttestation(
    principal, { bytes, month: "2026-01", batchId }, {
      config, requestId: () => "finance-byte-proof-1",
      now: () => 1_800_000_000_000,
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json({ ok: true, backendRawBytesObserved: true,
          reportAuthorityVerified: false,
          stableNetshopShopIdentityVerified: false }, { status: 201 });
      },
    },
  );
  const request = observed[0];
  assert.equal(new URL(request.url).origin, "http://127.0.0.1:8012");
  assert.equal(request.headers.get("content-type"), "application/octet-stream");
  assert.equal(request.headers.get("x-teruisi-content-sha256"),
    createHash("sha256").update(bytes).digest("hex"));
  assert.deepEqual(new Uint8Array(await request.arrayBuffer()), bytes);
  verifySignature(request, "/api/finance/imports/raw-attest",
    `month=2026-01&batchId=${batchId}`);
  assert.equal(result.status, 201);
  await assert.rejects(requestDjangoFinanceRawWorkbookAttestation(
    principal, { bytes: new Uint8Array(8 * 1024 * 1024 + 1),
      month: "2026-01", batchId }, { config,
      fetchImpl: async () => assert.fail("oversize source reached backend") }),
    (error: unknown) => error instanceof PublicApiError && error.status === 422);
});

test("annual target imports use the dedicated finance writer path", async () => {
  const observed: Request[] = [];
  await requestDjangoFinanceService<Record<string, unknown>>(
    principal,
    {
      method: "POST",
      path: "/api/finance/targets/import",
      payload: { schemaVersion: "finance-annual-target-import-v1", year: "2026" },
      service: "writer",
    },
    {
      config,
      requestId: () => "annual-target-write",
      fetchImpl: async (input, init) => {
        observed.push(new Request(input, init));
        return Response.json({ ok: true, status: "imported" }, { status: 201 });
      },
    },
  );
  assert.equal(new URL(observed[0].url).origin, "http://127.0.0.1:8012");
  verifySignature(observed[0], "/api/finance/targets/import", "");
});

test("finance service keeps reader/writer surfaces disjoint and sales signer remains sales-only", async () => {
  const neverFetch: typeof fetch = async () => {
    assert.fail("invalid service/path combinations must fail before fetch");
  };
  await assert.rejects(
    requestDjangoFinanceService(principal, {
      method: "POST",
      path: "/api/finance/targets",
      payload: {},
      service: "reader",
    }, { config, fetchImpl: neverFetch }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    requestDjangoFinanceService(principal, {
      method: "GET",
      path: "/api/finance/analysis",
      service: "reader",
    }, {
      config: { ...config, writerBaseUrl: config.readerBaseUrl },
      fetchImpl: neverFetch,
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    requestDjangoFinanceService(principal, {
      method: "GET",
      path: "/api/sales/summary",
      service: "reader",
    }, { config, fetchImpl: neverFetch }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    createSalesGatewayAuthHeaders({
      secret,
      principal,
      method: "GET",
      path: "/api/finance/analysis",
      rawQuery: "",
      timestamp: 1_800_000_000,
      requestId: "wrong-domain",
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("finance upstream validation details are bounded errors and never silently fall back", async () => {
  await assert.rejects(
    requestDjangoFinanceService(principal, {
      method: "GET",
      path: "/api/finance/analysis",
      service: "reader",
    }, {
      config,
      fetchImpl: async () => Response.json({
        error: "筛选项不存在",
        code: "finance_dimension_filter_out_of_scope",
        invalidPlatforms: ["不存在"],
        invalidShops: [],
        incompatibleShops: [],
      }, { status: 400 }),
    }),
    (error: unknown) => error instanceof DjangoFinanceServiceResponseError
      && error.status === 400
      && error.upstreamCode === "finance_dimension_filter_out_of_scope"
      && Array.isArray(error.payload.invalidPlatforms),
  );
  await assert.rejects(
    requestDjangoFinanceService(principal, {
      method: "GET",
      path: "/api/finance/analysis",
      service: "reader",
    }, {
      config,
      fetchImpl: async () => new Response("not-json", { status: 502 }),
    }),
    (error: unknown) => error instanceof PublicApiError
      && error.status === 503
      && error.code === "service_unavailable",
  );
  await assert.rejects(
    requestDjangoFinanceService(principal, {
      method: "GET",
      path: "/api/finance/analysis",
      service: "reader",
    }, {
      config,
      fetchImpl: async () => Response.json({ hasData: false }),
    }),
    (error: unknown) => error instanceof PublicApiError
      && error.status === 503
      && error.code === "service_unavailable",
  );
});
