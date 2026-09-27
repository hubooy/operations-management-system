import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import { requestDjangoBiOverview } from "../lib/django/bi-service";
import { PublicApiError } from "../lib/http/api-error";


const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const secret = "bi-service-contract-secret-at-least-32-bytes";
const config = {
  readerBaseUrl: "http://127.0.0.1:8081",
  internalSecret: secret,
  timeoutMs: 2_000,
  maxResponseBytes: 1024 * 1024,
};

test("BI reader signs the exact bounded query and requires a composite revision", async () => {
  let observed: Request | undefined;
  const rawQuery = "range=custom&startDate=2026-08-01&endDate=2026-08-02";
  const revision = "7:3|4:bbbbbbbbbbbb";
  const result = await requestDjangoBiOverview<Record<string, unknown>>(
    principal,
    rawQuery,
    {
      config,
      now: () => 1_800_000_000_000,
      requestId: () => "bi-reader-1",
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json(
          { contractVersion: "bi-dashboard-read-model-v1", revision, projection: "dashboard" },
          { headers: { "x-bi-data-revision": revision } },
        );
      },
    },
  );
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, config.readerBaseUrl);
  assert.equal(new URL(observed.url).search.slice(1), rawQuery);
  const canonical = [
    "v1",
    observed.headers.get("x-teruisi-timestamp"),
    observed.headers.get("x-teruisi-request-id"),
    "GET",
    "/api/bi/overview",
    rawQuery,
    observed.headers.get("x-teruisi-content-sha256"),
    observed.headers.get("x-teruisi-principal"),
  ].join("\n");
  assert.equal(
    observed.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
  assert.equal(result.revision, revision);
});

test("BI reader fails closed for unsafe origins, oversized queries, and invalid revisions", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("request must fail before fetch");
  for (const unsafe of [
    { ...config, readerBaseUrl: "http://example.com" },
    { ...config, internalSecret: "short" },
  ]) {
    await assert.rejects(
      requestDjangoBiOverview(principal, "range=month", { config: unsafe, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError && error.status === 503,
    );
  }
  await assert.rejects(
    requestDjangoBiOverview(principal, `range=${"x".repeat(3_000)}`, { config, fetchImpl: neverFetch }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    requestDjangoBiOverview(principal, "range=month", {
      config,
      fetchImpl: async () => Response.json(
        { contractVersion: "bi-dashboard-read-model-v1", revision: "4:bad" },
        { headers: { "x-bi-data-revision": "4:bad" } },
      ),
    }),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});
