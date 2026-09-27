import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test, { type TestContext } from "node:test";
import { build } from "esbuild";
import type { AppPrincipal } from "../lib/auth/authorization";
import { requestSalesAnalysisOptions, SALES_ANALYSIS_OPTIONS_PATH, EMPTY_SHA256 } from "../lib/django/sales-gateway";

const admin: AppPrincipal = { email: "erp-options@example.test", displayName: "Synthetic", role: "admin", scope: null };
const secret = "synthetic-erp-options-secret-at-least-32-bytes";
const config = { djangoBaseUrl: "http://127.0.0.1:18101", internalSecret: secret };
const revisions = { "x-sales-data-revision": "7:9", "x-sales-source-revision": "7:9" };
const payload = { revision: "7:9", items: [], pagination: { returned: 0, hasMore: false, nextCursor: null } };
const state = globalThis as typeof globalThis & { __salesOptionsPrincipal?: AppPrincipal | null };
const bundled = await build({ entryPoints: ["app/api/sales/analysis-options/route.ts"], bundle: true,
  write: false, platform: "node", format: "esm", external: ["cloudflare:workers"], plugins: [{
    name: "sales-options-session", setup(builder) {
      builder.onResolve({ filter: /^@\/lib\/auth\/authorization$/ }, () => ({ path: "session", namespace: "fixture" }));
      builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ loader: "js", contents: `
        export async function requireAppPrincipal() {
          const principal = globalThis.__salesOptionsPrincipal;
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
  const environment = { TERUISI_DJANGO_SALES_READER_BASE_URL: config.djangoBaseUrl,
    TERUISI_DJANGO_SALES_BASE_URL: "http://127.0.0.1:18102", TERUISI_DJANGO_INTERNAL_SECRET: secret,
    TERUISI_DJANGO_SALES_TIMEOUT_MS: "1000", TERUISI_DJANGO_SALES_MAX_RESPONSE_BYTES: "65536" };
  const old = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  const oldFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  state.__salesOptionsPrincipal = admin;
  globalThis.fetch = fetcher;
  t.after(() => {
    globalThis.fetch = oldFetch;
    delete state.__salesOptionsPrincipal;
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  });
}
const request = (query = "") => new Request(`https://test.invalid${SALES_ANALYSIS_OPTIONS_PATH}?${query}`);
function verify(input: RequestInfo | URL, init: RequestInit | undefined, query: URLSearchParams) {
  const outgoing = new Request(input, init), url = new URL(outgoing.url), headers = outgoing.headers;
  assert.equal(url.origin, config.djangoBaseUrl);
  assert.equal(url.pathname, SALES_ANALYSIS_OPTIONS_PATH);
  assert.equal(url.search.slice(1), query.toString());
  assert.equal(outgoing.method, "GET");
  assert.equal(outgoing.body, null);
  assert.equal(init?.cache, "no-store");
  assert.equal(init?.redirect, "manual");
  assert.equal(headers.get("x-teruisi-content-sha256"), EMPTY_SHA256);
  assert.deepEqual(JSON.parse(Buffer.from(headers.get("x-teruisi-principal")!, "base64url").toString()), admin);
  const canonical = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"), "GET",
    SALES_ANALYSIS_OPTIONS_PATH, query.toString(), EMPTY_SHA256, headers.get("x-teruisi-principal")].join("\n");
  assert.equal(headers.get("x-teruisi-signature"), `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`);
}
test("ERP options actual route signs the complete GET for the reader and preserves payload", async t => {
  const query = new URLSearchParams({ platform: "京东", shop: "合成 店铺", channel: "B端&直营", limit: "20", cursor: "x+y/z=" });
  let calls = 0;
  setup(t, async (input, init) => { calls++; verify(input, init, query); return Response.json(payload, { headers: revisions }); });
  const response = await route.GET(request(query.toString()));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), payload);
  assert.equal(calls, 1);
  assert.equal(route.POST, undefined);
});
test("ERP options preserves empty duplicate and unknown query for owning validation", async t => {
  let query = new URLSearchParams();
  setup(t, async (input, init) => { verify(input, init, query); return Response.json({ error: "invalid owning query" }, { status: 400 }); });
  for (const raw of ["", "shop=甲&shop=乙", "unknown=1&limit=999", "channel=%20原样%20"]) {
    query = new URLSearchParams(raw);
    const response = await route.GET(request(query.toString()));
    assert.equal(response.status, 400);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.match(JSON.stringify(await response.json()), /invalid owning query/);
  }
});
test("ERP options rejects unauthenticated nonadmin and scoped admin before transport", async t => {
  let calls = 0;
  setup(t, async () => { calls++; throw new Error("must not dispatch"); });
  for (const principal of [null, ...(["viewer", "analyst", "operator"] as const).map(role => ({ ...admin, role })),
    { ...admin, scope: { platforms: ["京东"], channels: [], warehouses: [] } }]) {
    state.__salesOptionsPrincipal = principal;
    const response = await route.GET(request());
    assert.equal(response.status, principal ? 403 : 401);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
  assert.equal(calls, 0);
});
test("ERP options preserves controlled upstream errors without retries", async t => {
  let status = 400, calls = 0;
  setup(t, async () => { calls++; return Response.json({ error: "owning denial" }, { status }); });
  for (status of [400, 403, 409, 413, 422, 503]) {
    const response = await route.GET(request());
    assert.equal(response.status, status);
    assert.match(JSON.stringify(await response.json()), /owning denial/);
  }
  assert.equal(calls, 6);
});
test("ERP options fails closed on absent mismatched or malformed dual revisions", async t => {
  let headers: Record<string, string> = {};
  setup(t, async () => Response.json(payload, { headers }));
  for (headers of [{}, { "x-sales-data-revision": "7:9" }, { ...revisions, "x-sales-source-revision": "7:8" },
    { "x-sales-data-revision": "8:9", "x-sales-source-revision": "8:9" },
    { "x-sales-data-revision": "07:9", "x-sales-source-revision": "07:9" }]) {
    assert.equal((await route.GET(request())).status, 503);
  }
});
test("ERP options rejects nonobjects nonJSON redirects and unexpected upstream failures", async t => {
  let response: Response;
  setup(t, async () => response);
  for (response of [Response.json([], { headers: revisions }), new Response("not json"),
    new Response(JSON.stringify(payload), { headers: { ...revisions, "content-type": "text/html" } }),
    new Response(null, { status: 302, headers: { location: "https://other.invalid" } }),
    Response.json({ error: "private trace" }, { status: 500 })]) {
    const result = await route.GET(request());
    assert.equal(result.status, 503);
    assert.doesNotMatch(await result.text(), /private trace/);
  }
});
test("ERP options enforces 38000 UTF8 bytes including streamed bodies and smaller config", async () => {
  const data = { ...payload, label: "中".repeat(13000) };
  assert.ok(JSON.stringify(data).length < 38000);
  const bytes = new TextEncoder().encode(JSON.stringify(data));
  assert.ok(bytes.length > 38000);
  for (const fetchImpl of [async () => Response.json(data, { headers: revisions }), async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(bytes.subarray(0, 20000)); controller.enqueue(bytes.subarray(20000)); controller.close(); },
  }), { headers: { ...revisions, "content-type": "application/json" } })]) {
    await assert.rejects(requestSalesAnalysisOptions(admin, new URLSearchParams(), { config, fetchImpl }), { status: 503 });
  }
  await assert.rejects(requestSalesAnalysisOptions(admin, new URLSearchParams(), {
    config: { ...config, maxResponseBytes: 20 }, fetchImpl: async () => Response.json(payload, { headers: revisions }),
  }), { status: 503 });
});
test("ERP options cancellation reaches pending headers without retry and preabort does not dispatch", async () => {
  const controller = new AbortController();
  let calls = 0;
  const fetchImpl: typeof fetch = async (_input, init) => {
    calls++;
    return new Promise((_resolve, reject) => {
      init!.signal!.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      controller.abort();
    });
  };
  for (let i = 0; i < 2; i++) await assert.rejects(requestSalesAnalysisOptions(admin, new URLSearchParams(), {
    config, fetchImpl, signal: controller.signal,
  }), { status: 503 });
  assert.equal(calls, 1);
});
test("ERP options timeout covers response body consumption", async () => {
  let aborted = false;
  await assert.rejects(requestSalesAnalysisOptions(admin, new URLSearchParams(), {
    config: { ...config, timeoutMs: 20 }, fetchImpl: async (_input, init) => new Response(new ReadableStream({
      start(controller) { init!.signal!.addEventListener("abort", () => { aborted = true; controller.error(new Error("timeout")); }, { once: true }); },
    }), { headers: { ...revisions, "content-type": "application/json" } }),
  }), { status: 503 });
  assert.equal(aborted, true);
});
test("ERP options snapshots mutable query before asynchronous runtime config", async t => {
  const query = new URLSearchParams({ shop: "original" }), expected = new URLSearchParams(query);
  setup(t, async (input, init) => { verify(input, init, expected); return Response.json(payload, { headers: revisions }); });
  const pending = requestSalesAnalysisOptions(admin, query);
  query.set("shop", "mutated");
  assert.deepEqual(await pending, payload);
});
