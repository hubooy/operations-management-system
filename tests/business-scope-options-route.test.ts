import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { createHmac } from "node:crypto";
import { build } from "esbuild";
import type { AppPrincipal } from "../lib/auth/authorization";
import { scopeOptionsHash, scopeOptionsPrincipalKey } from "../lib/ai/business-scope-options";

const admin: AppPrincipal = { email: "Admin@Example.TEST", displayName: "Synthetic", role: "admin", scope: null };
const state = globalThis as typeof globalThis & { __scopeOptionsPrincipal?: AppPrincipal | null };
const secret = "scope-options-bridge-synthetic-secret-0123456789", reader = "http://127.0.0.1:18131", revision = "7:abcdef123456";
const routePath = "/api/ai/business-plan/netshop-options", domainPath = "/api/netshop/analysis-options";
const key = await scopeOptionsPrincipalKey(admin.email);
const bundled = await build({ entryPoints: ["app/api/ai/business-plan/netshop-options/route.ts"], bundle: true, write: false,
  platform: "node", format: "esm", external: ["cloudflare:workers"], plugins: [{ name: "scope-options-session", setup(builder) {
    builder.onResolve({ filter: /^@\/lib\/auth\/authorization$/ }, () => ({ path: "session", namespace: "fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ loader: "js", contents: `
      export async function requireAppPrincipal(){const p=globalThis.__scopeOptionsPrincipal;if(!p)throw Object.assign(new Error('Authentication required'),{status:401});return p;}
      export function authorizationErrorResponse(e){return e.status===401?Response.json({error:'Authentication required'},{status:401}):null;}` }));
  } }] });
const route = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`) as { GET(request: Request): Promise<Response>; POST?: unknown };
function setup(t: TestContext, fetcher: typeof fetch) {
  const env = { TERUISI_DJANGO_NETSHOP_READER_BASE_URL: reader, TERUISI_DJANGO_NETSHOP_WRITER_BASE_URL: "http://127.0.0.1:18132", TERUISI_DJANGO_INTERNAL_SECRET: secret };
  const old = Object.fromEntries(Object.keys(env).map(name => [name, process.env[name]])), previousFetch = globalThis.fetch;
  Object.assign(process.env, env); state.__scopeOptionsPrincipal = { ...admin }; globalThis.fetch = fetcher;
  t.after(() => { globalThis.fetch = previousFetch; delete state.__scopeOptionsPrincipal; for (const [name, value] of Object.entries(old)) { if (value === undefined) delete process.env[name]; else process.env[name] = value; } });
}
async function page(query: Record<string, string> = {}) {
  const result = { schemaVersion: "business-analysis-options-v1", domain: "netshop", revision, query, queryDigest: await scopeOptionsHash(query), items: [],
    pagination: { returned: 0, limit: 20, hasMore: false, nextCursor: null }, limitations: ["仅历史导入元数据，不表示日期完整。"] };
  return { ...result, pageDigest: await scopeOptionsHash(result) };
}
function request(query = "", binding = key) { return new Request(`https://test.invalid${routePath}?expectedPrincipalKey=${binding}${query ? "&"+query : ""}`); }
function signature(input: RequestInfo | URL, init: RequestInit | undefined, expectedQuery: URLSearchParams) {
  const req = new Request(input, init), url = new URL(req.url), headers = req.headers;
  assert.equal(url.origin, reader); assert.equal(url.pathname, domainPath); assert.equal(url.search.slice(1), expectedQuery.toString());
  assert.equal(req.method, "GET"); assert.equal(req.body, null); assert.equal(init?.cache, "no-store");
  const canonical = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"), "GET", domainPath, expectedQuery.toString(), headers.get("x-teruisi-content-sha256"), headers.get("x-teruisi-principal")].join("\n");
  assert.equal(headers.get("x-teruisi-signature"), `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`);
  const principal = JSON.parse(Buffer.from(headers.get("x-teruisi-principal")!, "base64url").toString()); assert.equal(principal.email, admin.email);
}

test("same authenticated principal signs exact domain query and envelope preserves original page", async t => {
  const query = new URLSearchParams({ platform: "京东", shop: "精确 店", dataset: "sku", limit: "20" });
  const payload = await page({ platform: "京东", shop: "精确 店", dataset: "sku" }); let count = 0;
  setup(t, async (input, init) => { count++; signature(input, init, query); return Response.json(payload, { headers: { "x-netshop-data-revision": revision } }); });
  const response = await route.GET(request(query.toString())); assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { schemaVersion: "business-netshop-options-response-v1", principalKey: key, page: payload });
  assert.equal(response.headers.get("cache-control"), "no-store"); assert.equal(count, 1); assert.equal(route.POST, undefined);
});
test("missing duplicate malformed and stale expected account cause zero domain requests", async t => {
  let calls = 0; setup(t, async () => { calls++; throw new Error("must not call"); });
  for (const query of ["", "expectedPrincipalKey=", "expectedPrincipalKey=bad", `expectedPrincipalKey=${key}&expectedPrincipalKey=${key}`]) {
    assert.equal((await route.GET(new Request(`https://test.invalid${routePath}?${query}`))).status, 400);
  }
  state.__scopeOptionsPrincipal = { ...admin, email: "switched@example.test" };
  const response = await route.GET(request()); assert.equal(response.status, 403); assert.equal((await response.json()).code, "principal_mismatch"); assert.equal(calls, 0);
});
test("anonymous role and scope denial before fetching metadata", async t => {
  setup(t, async () => { throw new Error("must not dispatch"); }); state.__scopeOptionsPrincipal = null;
  assert.equal((await route.GET(request())).status, 401);
  for (const role of ["viewer", "analyst", "operator"] as const) { state.__scopeOptionsPrincipal = { ...admin, role }; assert.equal((await route.GET(request())).status, 403); }
  state.__scopeOptionsPrincipal = { ...admin, scope: { platforms: ["京东"], channels: [], warehouses: [] } }; assert.equal((await route.GET(request())).status, 403);
});
test("duplicate unknown and cursor parameters reach owning reader unchanged then retain its rejection", async t => {
  let actual = new URLSearchParams(); let count = 0;
  setup(t, async (input, init) => { count++; signature(input, init, actual); return Response.json({ error: "synthetic rejected query", code: "invalid_request" }, { status: 400 }); });
  for (const raw of ["platform=京东&platform=天猫", "unknown=literal", "cursor=abc%3Aone%3Asig&limit=999"]) {
    actual = new URLSearchParams(raw); const response = await route.GET(request(raw)); assert.equal(response.status, 400); assert.match(JSON.stringify(await response.json()), /synthetic rejected query/);
  }
  assert.equal(count, 3);
});
test("broken successful page and revision do not get an authenticated-looking success envelope", async t => {
  const good = await page(); let payload: Record<string, unknown> = good, header: string | null = revision;
  setup(t, async () => Response.json(payload, { headers: header ? { "x-netshop-data-revision": header } : {} }));
  for (const bad of [{ ...good, pageDigest: "a".repeat(64) }, { ...good, items: [{ secret: "customer" }] }]) { payload = bad; assert.equal((await route.GET(request())).status, 503); }
  payload = good; header = "8:abcdef123456"; assert.equal((await route.GET(request())).status, 503);
  header = null; assert.equal((await route.GET(request())).status, 503);
});
test("owning reader permission failure is never retried or changed to empty results", async t => {
  let count = 0; setup(t, async () => { count++; return Response.json({ error: "revoked", code: "access_denied" }, { status: 403 }); });
  const response = await route.GET(request()); assert.equal(response.status, 403); assert.match(JSON.stringify(await response.json()), /revoked/); assert.equal(count, 1);
});
test("request cancellation propagates to the existing bounded transport", async t => {
  let started!: () => void; const begin = new Promise<void>(resolve => { started = resolve; }); let aborted = false;
  setup(t, async (_input, init) => new Promise<Response>((_resolve, reject) => {
    started(); init!.signal!.addEventListener("abort", () => { aborted = true; reject(new DOMException("aborted", "AbortError")); }, { once: true });
  }));
  const controller = new AbortController(), pending = route.GET(new Request(request().url, { signal: controller.signal }));
  await begin; controller.abort(); const response = await pending; assert.ok(response.status >= 400); assert.equal(aborted, true);
});
