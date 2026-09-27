import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test, { type TestContext } from "node:test";
import { build } from "esbuild";

import type { AppPrincipal } from "../lib/auth/authorization";
import { createMarketGatewayAuthHeaders, salesGatewayBodySha256 } from "../lib/django/sales-gateway";
import { MARKET_ANALYSIS_OPTIONS_PATH, requestDjangoMarketService } from "../lib/django/market-service";

const admin: AppPrincipal = { email: "analysis-options@example.test", displayName: "Synthetic", role: "admin", scope: null };
const secret = "analysis-options-synthetic-secret-at-least-32-bytes";
const config = { readerBaseUrl: "http://127.0.0.1:18131", writerBaseUrl: "http://127.0.0.1:18132", internalSecret: secret };
const revision = { "x-market-data-revision": "7:abcdef123456" };
const state = globalThis as typeof globalThis & { __marketAnalysisOptionsPrincipal?: AppPrincipal | null };

// Bundle the actual route, real service/signature/analysis permission helpers.
// Only the session boundary is synthetic; HTTP is intercepted per test below.
const bundled = await build({ entryPoints: ["app/api/market/analysis-options/route.ts"], bundle: true,
  write: false, platform: "node", format: "esm", external: ["cloudflare:workers"], plugins: [{
    name: "analysis-options-session", setup(builder) {
      builder.onResolve({ filter: /^@\/lib\/auth\/authorization$/ }, () => ({ path: "session", namespace: "fixture" }));
      builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ loader: "js", contents: `
        export async function requireAppPrincipal() {
          const principal = globalThis.__marketAnalysisOptionsPrincipal;
          if (!principal) throw Object.assign(new Error('Authentication required'), {status:401});
          return principal;
        }
        export function authorizationErrorResponse(error) {
          return error.status === 401 ? Response.json({error:'Authentication required'}, {status:401}) : null;
        }` }));
    },
  }] });
const route = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`) as {
  GET(request: Request): Promise<Response>; POST?: unknown;
};

function setup(t: TestContext, fetcher: typeof fetch) {
  const environment = { TERUISI_DJANGO_MARKET_READER_BASE_URL: config.readerBaseUrl,
    TERUISI_DJANGO_MARKET_WRITER_BASE_URL: config.writerBaseUrl, TERUISI_DJANGO_INTERNAL_SECRET: secret };
  const old = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  const oldFetch = globalThis.fetch;
  state.__marketAnalysisOptionsPrincipal = admin;
  Object.assign(process.env, environment);
  globalThis.fetch = fetcher;
  t.after(() => {
    globalThis.fetch = oldFetch;
    delete state.__marketAnalysisOptionsPrincipal;
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  });
}

function verify(input: RequestInfo | URL, init: RequestInit | undefined, query: URLSearchParams) {
  const request = new Request(input, init), url = new URL(request.url);
  assert.equal(url.origin, config.readerBaseUrl);
  assert.equal(url.pathname, MARKET_ANALYSIS_OPTIONS_PATH);
  assert.equal(url.search.slice(1), query.toString());
  assert.equal(request.method, "GET");
  assert.equal(init?.cache, "no-store");
  assert.equal(request.body, null);
  const headers = request.headers;
  const canonical = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"),
    "GET", MARKET_ANALYSIS_OPTIONS_PATH, query.toString(), headers.get("x-teruisi-content-sha256"),
    headers.get("x-teruisi-principal")].join("\n");
  assert.equal(headers.get("x-teruisi-signature"), `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`);
}

test("analysis options route signs the complete query on the reader and preserves the complete protocol", async t => {
  const query = new URLSearchParams({ platform: "京东", category: "合成 类目", rankingDimension: "SKU", priceBandFilter: "全部", limit: "20" });
  // Synthetic transport fixture: the backend's own contract and digest are
  // independently covered by PostgreSQL tests; this route must not rewrite it.
  const identity = {platform: "京东", category: "合成 类目", scope: "全部", rankingDimension: "SKU", priceBandFilter: "全部"};
  const payload = { schemaVersion: "business-analysis-options-v1", domain: "market", authorityVerified: true,
    revision: "7:abcdef123456", directoryGeneration: "c".repeat(32), directoryDigest: "d".repeat(64),
    query: { platform: "京东", category: "合成 类目", rankingDimension: "SKU", priceBandFilter: "全部" }, queryDigest: "a".repeat(64),
    items: [{optionKey:"e".repeat(64), identity, source: "market_daily_top", sourceDataset: "market_daily_top",
      dateMetadata: {kind:"published_import_envelope",firstDate:"2026-09-01",lastDate:"2026-09-03",snapshotDate:null,coverageVerified:false},
      provenance: {kind:"completed_import_metadata",revision:"7:abcdef123456",generation:"c".repeat(32),directoryDigest:"d".repeat(64),meaning:"historically_published_not_current_fact_coverage"}}],
    pagination: {returned:1,limit:20,hasMore:false,nextCursor:null},pageDigest:"b".repeat(64),limitations:["synthetic transport fixture"]};
  let count = 0;
  setup(t, async (input, init) => { count++; verify(input, init, query); return Response.json(payload, { headers: revision }); });
  const response = await route.GET(new Request(`https://test.invalid/api/market/analysis-options?${query}`));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), payload);
  assert.equal(count, 1);
  assert.equal(route.POST, undefined);
});

test("analysis options does not fill defaults or erase duplicate and unknown parameters before backend rejection", async t => {
  let current = new URLSearchParams(), count = 0;
  setup(t, async (input, init) => {
    count++; verify(input, init, current);
    return Response.json({ code: "invalid_request", message: "unknown or duplicate query" }, { status: 400 });
  });
  for (const raw of ["", "unknown=preserve", "category=甲&category=乙", "offset=01&limit=999",
    "platform=京东&priceBandFilter=%20原样%20&rankingDimension=sku"] ) {
    current = new URLSearchParams(raw);
    const result = await route.GET(new Request(`https://test.invalid/api/market/analysis-options?${current}`));
    assert.equal(result.status, 400);
    assert.equal(result.headers.get("cache-control"), "no-store");
    assert.match(JSON.stringify(await result.json()), /unknown or duplicate query/);
  }
  assert.equal(count, 5);
});

test("analysis options requires authenticated unscoped admin before any transport", async t => {
  setup(t, async () => { throw new Error("must not dispatch"); });
  state.__marketAnalysisOptionsPrincipal = null;
  assert.equal((await route.GET(new Request("https://test.invalid/api/market/analysis-options"))).status, 401);
  for (const role of ["viewer", "analyst", "operator"] as const) {
    state.__marketAnalysisOptionsPrincipal = { ...admin, role };
    assert.equal((await route.GET(new Request("https://test.invalid/api/market/analysis-options"))).status, 403);
  }
  state.__marketAnalysisOptionsPrincipal = { ...admin, scope: { platforms: ["京东"], warehouses: [], channels: [] } };
  assert.equal((await route.GET(new Request("https://test.invalid/api/market/analysis-options"))).status, 403);
});

test("analysis options writer and POST are denied without reader fallback", async () => {
  let count = 0;
  for (const input of [{ method: "GET", service: "writer" }, { method: "POST", service: "reader" }, { method: "POST", service: "writer" }] as const) {
    await assert.rejects(requestDjangoMarketService(admin, { service: input.service, path: MARKET_ANALYSIS_OPTIONS_PATH, payload: {} }, { config, fetchImpl: async () => {
      count++; return Response.json({}, { headers: revision });
    } }));
  }
  assert.equal(count, 0);
});

test("analysis options preserves upstream denial and does not retry on another endpoint", async t => {
  let count = 0;
  setup(t, async (input, init) => {
    count++; verify(input, init, new URLSearchParams());
    return Response.json({ code: "access_denied", message: "reader denied" }, { status: 403 });
  });
  const response = await route.GET(new Request("https://test.invalid/api/market/analysis-options"));
  assert.equal(response.status, 403);
  assert.match(JSON.stringify(await response.json()), /reader denied/);
  assert.equal(count, 1);
});

test("analysis options abort reaches the in-flight reader without retry", async t => {
  const controller = new AbortController();
  let count = 0, aborted = false;
  setup(t, async (_input, init) => {
    count++;
    return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => { aborted = true; reject(new DOMException("Aborted", "AbortError")); }, { once: true });
      controller.abort();
    });
  });
  const response = await route.GET(new Request("https://test.invalid/api/market/analysis-options", { signal: controller.signal }));
  assert.equal(response.status, 503);
  assert.equal(aborted, true);
  assert.equal(count, 1);
});

test("analysis options rejects success without revision rather than returning unbound options", async t => {
  setup(t, async () => Response.json({ items: [] }));
  assert.equal((await route.GET(new Request("https://test.invalid/api/market/analysis-options"))).status, 503);
});


test("market options rejects revision mismatch and bounded response overflow", async t => {
  let count = 0;
  setup(t, async () => { count++; return Response.json({ revision: count === 1 ? "8:abcdef123456" : "7:abcdef123456", items: count === 1 ? [] : ["中".repeat(13000)] }, {headers:revision}); });
  for (let i=0;i<2;i++) assert.equal((await route.GET(new Request("https://test.invalid/api/market/analysis-options"))).status,503);
  assert.equal(count,2);
});


test("market signature expands GET only for exact options path and empty body", async () => {
  const input = {secret, principal:admin, timestamp:1800000000, requestId:"synthetic",method:"GET",path:MARKET_ANALYSIS_OPTIONS_PATH,rawQuery:"q=a%26b",bodySha256:await salesGatewayBodySha256(new Uint8Array())};
  await createMarketGatewayAuthHeaders(input);
  for (const change of [{path:"/api/market/commands"},{path:MARKET_ANALYSIS_OPTIONS_PATH+"/"},{method:"DELETE"},{bodySha256:"1".repeat(64)}]) await assert.rejects(createMarketGatewayAuthHeaders({...input,...change}));
});
