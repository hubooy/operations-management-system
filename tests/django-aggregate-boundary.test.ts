import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { auditProductionBoundary } from "../tools/check-django-production-boundary.mjs";
import { DJANGO_READINESS_SERVICES, probeDjangoBackendReadiness } from "../lib/django/backend-readiness";
import { createDjangoMarketConsumerReader } from "../lib/django/market-consumer-reader";
import type { AppPrincipal } from "../lib/auth/authorization";

test("every production entry and transitive dynamic import is free of D1 access", async () => {
  const result = await auditProductionBoundary();
  assert.ok(result.checkedModules > 250);
  assert.ok(result.operationalEntrypoints.includes("tools/tmall-sycm-cookie-pipeline.ts"));
  assert.ok(result.operationalEntrypoints.includes("tools/jd-market-image-repair.ts"));
  assert.deepEqual(result.violations, []);
  const hosting = JSON.parse(await readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"));
  assert.equal(hosting.d1, undefined);
  assert.equal(hosting.r2, "SALES_IMPORT_FILES");
});

function readinessFixture() {
  const environment: Record<string, string> = {};
  const payloads = new Map<string, Record<string, unknown>>();
  DJANGO_READINESS_SERVICES.forEach((service, index) => {
    const endpoint = `http://127.0.0.1:${22000 + index}`;
    environment[service.variable] = endpoint;
    payloads.set(`${endpoint}/health/ready`, { status: "ready", service: "teruisi-django", database: "ready",
      ...(service.aiRole ? { processRole: service.aiRole, authority: "postgres" } : { [service.field]: "ready" }),
    });
  });
  return { environment, payloads };
}

test("readiness uses the deployed ERP adapter's ERP environment names", async () => {
  const requested: string[] = [];
  const result = await probeDjangoBackendReadiness({
    TERUISI_DJANGO_ERP_READER_BASE_URL: "http://127.0.0.1:22901",
    TERUISI_DJANGO_ERP_WRITER_BASE_URL: "http://127.0.0.1:22902",
  }, { fetchImpl: async (url) => {
    requested.push(String(url));
    return Response.json({ status: "ready", service: "teruisi-django", database: "ready",
      [String(url).includes(":22901/") ? "erpReferenceReader" : "erpReferenceWriter"]: "ready" });
  } });
  assert.equal(requested.length, 2);
  assert.equal(result.unavailableServices.length, 21);
  assert.ok(result.unavailableServices.every((service) => !service.startsWith("erp_reference.")));
});
test("readiness verifies all 23 reader/writer identities with at most six requests in flight", async () => {
  const { environment, payloads } = readinessFixture();
  let active = 0, peak = 0, calls = 0;
  const result = await probeDjangoBackendReadiness(environment, { fetchImpl: async (url, init) => {
    assert.equal(init?.method, "GET");
    assert.equal(init?.redirect, "manual");
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.headers, undefined);
    calls++; active++; peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 1)); active--;
    return Response.json(payloads.get(String(url)));
  } });
  assert.equal(result.ok, true);
  assert.equal(calls, 23);
  assert.ok(peak <= 6);
  assert.deepEqual(result.unavailableServices, []);
});

test("missing configuration, wrong role and failed backend are disclosed without private response details", async () => {
  const { environment, payloads } = readinessFixture();
  delete environment.TERUISI_DJANGO_FINANCE_READER_BASE_URL;
  payloads.set(`${environment.TERUISI_DJANGO_MARKET_WRITER_BASE_URL}/health/ready`, { status: "ready", service: "teruisi-django", database: "ready", marketReader: "ready" });
  const result = await probeDjangoBackendReadiness(environment, { fetchImpl: async (url) =>
    String(url).startsWith(environment.TERUISI_DJANGO_AI_WRITER_BASE_URL)
      ? Response.json({ error: "private server path" }, { status: 503 }) : Response.json(payloads.get(String(url))),
  });
  assert.equal(result.ok, false);
  assert.deepEqual(result.unavailableServices, ["finance.reader", "market.writer", "ai.writer"]);
  assert.doesNotMatch(JSON.stringify(result), /private|127\.0\.0\.1/);
});

test("readiness timeout cancels in-flight probes and skips queued services", async () => {
  const { environment } = readinessFixture();
  let calls = 0, aborted = 0;
  const result = await probeDjangoBackendReadiness(environment, { timeoutMs: 10, fetchImpl: async (_url, init) => {
    calls++;
    return new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => {
      aborted++; reject(new Error("cancelled"));
    }, { once: true }));
  } });
  assert.equal(result.ok, false);
  assert.equal(result.unavailableServices.length, 23);
  assert.equal(calls, 6);
  assert.equal(aborted, 6);
});

test("readiness rejects unsafe endpoints and oversized/redirected responses", async () => {
  const { environment } = readinessFixture();
  environment.TERUISI_DJANGO_SALES_READER_BASE_URL = "http://untrusted.example";
  environment.TERUISI_DJANGO_FINANCE_READER_BASE_URL = "http://user:secret@127.0.0.1";
  const urls: string[] = [];
  const result = await probeDjangoBackendReadiness(environment, { fetchImpl: async (url) => {
    urls.push(String(url));
    return urls.length % 2 ? new Response(null, { status: 302, headers: { location: "http://untrusted.example" } })
      : new Response("", { headers: { "content-length": "20000" } });
  } });
  assert.equal(result.ok, false);
  assert.equal(result.unavailableServices.length, 23);
  assert.ok(urls.every((url) => !/untrusted|secret/.test(url)));
});

const principal: AppPrincipal = { email: "viewer@example.com", displayName: "Viewer", role: "viewer", scope: null };
const config = { readerBaseUrl: "http://127.0.0.1:22301", writerBaseUrl: "http://127.0.0.1:22302", internalSecret: "isolated-test-secret-01234567890123456789" };
test("market consumer signs bounded reads and never sends them to the writer", async () => {
  const abort = new AbortController();
  const reader = createDjangoMarketConsumerReader({ config, fetchImpl: async (url, init) => {
    assert.equal(String(url), `${config.readerBaseUrl}/api/market/consumers/query`);
    assert.equal(init?.method, "POST");
    const headers = new Headers(init?.headers);
    assert.ok([...headers.keys()].some((key) => /signature/.test(key)));
    const body = JSON.parse(new TextDecoder().decode(init?.body as Uint8Array));
    assert.deepEqual(body, { operation: "sku_search", query: "100%_", offset: 8, limit: 4 });
    return Response.json({ items: [], total: 0, truncated: false }, { headers: { "x-market-data-revision": "8:abcdef123456" } });
  } });
  const result = await reader.read(principal, { operation: "sku_search", query: "100%_", offset: 8, limit: 4 }, { signal: abort.signal });
  assert.equal(result.revision, "8:abcdef123456");
});

test("market consumer denies scope, unauthorized batch reads, and invalid windows before dispatch", async () => {
  let calls = 0;
  const reader = createDjangoMarketConsumerReader({ config, fetchImpl: async () => { calls++; throw new Error("unexpected"); } });
  const query = { operation: "sku_search" as const, query: "SKU", offset: 0, limit: 1 };
  await assert.rejects(reader.read({ ...principal, scope: { warehouses: [], channels: [], platforms: [] } }, query), /无权/);
  await assert.rejects(reader.read(principal, { ...query, operation: "import_batch_search" }), /无权/);
  for (const override of [{ offset: -1 }, { limit: 101 }, { limit: 1.5 }, { query: "x".repeat(121) }]) {
    await assert.rejects(reader.read(principal, { ...query, ...override }), /参数无效/);
  }
  assert.equal(calls, 0);
});

test("market consumer rejects mismatched operation and missing revision", async () => {
  for (const response of [
    Response.json({ operation: "annotation_search", data: {} }, { headers: { "x-market-data-revision": "8:abcdef123456" } }),
    Response.json({ operation: "sku_search", data: {} }),
  ]) {
    const reader = createDjangoMarketConsumerReader({ config, fetchImpl: async () => response });
    await assert.rejects(reader.read(principal, { operation: "sku_search", query: "sku", offset: 0, limit: 1 }));
  }
});
