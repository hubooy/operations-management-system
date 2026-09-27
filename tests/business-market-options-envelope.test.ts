import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { createHmac } from "node:crypto";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { build } from "esbuild";
import type { AppPrincipal } from "../lib/auth/authorization";
import { marketOptionsPrincipalKey } from "../lib/ai/business-market-options";
import { scopeOptionsHash } from "../lib/ai/business-scope-options";

const admin: AppPrincipal = { email: "Admin@Example.TEST", displayName: "Synthetic", role: "admin", scope: null };
const state = globalThis as typeof globalThis & { __marketEnvelopePrincipal?: AppPrincipal | null };
const secret = "market-envelope-synthetic-secret-0123456789", reader = "http://127.0.0.1:18131", revision = "7:abcdef123456";
const routePath = "/api/ai/business-plan/market-options", domainPath = "/api/market/analysis-options";
const key = await marketOptionsPrincipalKey(admin.email);
const bundled = await build({ entryPoints: ["app/api/ai/business-plan/market-options/route.ts"], bundle: true, write: false,
  platform: "node", format: "esm", external: ["cloudflare:workers"], plugins: [{ name: "market-envelope-session", setup(builder) {
    builder.onResolve({ filter: /^@\/lib\/auth\/authorization$/ }, () => ({ path: "session", namespace: "fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ loader: "js", contents: `
      export async function requireAppPrincipal(){const p=globalThis.__marketEnvelopePrincipal;if(!p)throw Object.assign(new Error('Authentication required'),{status:401});return p;}
      export function authorizationErrorResponse(e){return e.status===401?Response.json({error:'Authentication required'},{status:401}):null;}` }));
  } }] });
const route = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`) as { GET(request: Request): Promise<Response>; POST?: unknown };
function setup(t: TestContext, fetcher: typeof fetch) {
  const env = { TERUISI_DJANGO_MARKET_READER_BASE_URL: reader, TERUISI_DJANGO_MARKET_WRITER_BASE_URL: "http://127.0.0.1:18132", TERUISI_DJANGO_INTERNAL_SECRET: secret };
  const old = Object.fromEntries(Object.keys(env).map(name => [name, process.env[name]])), previousFetch = globalThis.fetch;
  Object.assign(process.env, env); state.__marketEnvelopePrincipal = { ...admin }; globalThis.fetch = fetcher;
  t.after(() => { globalThis.fetch = previousFetch; delete state.__marketEnvelopePrincipal; for (const [name, value] of Object.entries(old)) { if (value === undefined) delete process.env[name]; else process.env[name] = value; } });
}
function request(query = "", binding = key) { return new Request(`https://test.invalid${routePath}?expectedPrincipalKey=${binding}${query ? "&"+query : ""}`); }
function signature(input: RequestInfo | URL, init: RequestInit | undefined, expectedQuery: URLSearchParams) {
  const req = new Request(input, init), url = new URL(req.url), headers = req.headers;
  assert.equal(url.origin, reader); assert.equal(url.pathname, domainPath); assert.equal(url.search.slice(1), expectedQuery.toString());
  assert.equal(req.method, "GET"); assert.equal(req.body, null); assert.equal(init?.cache, "no-store");
  const canonical = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"), "GET", domainPath, expectedQuery.toString(), headers.get("x-teruisi-content-sha256"), headers.get("x-teruisi-principal")].join("\n");
  assert.equal(headers.get("x-teruisi-signature"), `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`);
  assert.equal(JSON.parse(Buffer.from(headers.get("x-teruisi-principal")!, "base64url").toString()).email, admin.email);
}
// Actual Python DTO builder, with the same owning authority change/re-sign used
// by analysis_options.read_page. No DB/API/production request in this fixture.
const python = spawnSync(path.resolve(".runtime/test-venv/Scripts/python.exe"), ["-c", String.raw`
import sys,json
sys.path.insert(0,"backend")
from market import analysis_options_contract as c
identity={"platform":"京东","category":"Straße","scope":"商品热销榜","rankingDimension":"SKU","priceBandFilter":"全部"}
p=c.make_page([{"identity":identity,"firstDate":"2024-02-29","lastDate":"2026-09-01"}],revision="7:abcdef123456",generation="b"*32,directory_digest="c"*64,query={"q":"STRASSE"},has_more=False,next_cursor=None)
p["authorityVerified"]=True
p.pop("pageDigest")
p["pageDigest"]=c._digest(p)
print(json.dumps(p,ensure_ascii=True))
`], { encoding: "utf8", timeout: 15000 });
assert.equal(python.status, 0, python.stderr);
const pythonPage = JSON.parse(python.stdout);

test("actual Python complete page is signed to reader and wrapped without rewriting its identity or digest", async t => {
  const query = new URLSearchParams({ q: "STRASSE", limit: "20" }); let count = 0;
  setup(t, async (input, init) => { count++; signature(input, init, query); return Response.json(pythonPage, { headers: { "x-market-data-revision": revision } }); });
  const response = await route.GET(request(query.toString())); assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { schemaVersion: "business-market-options-response-v1", principalKey: key, page: pythonPage });
  assert.equal(response.headers.get("cache-control"), "no-store"); assert.equal(count, 1); assert.equal(route.POST, undefined);
});

test("missing duplicate malformed and changed account bindings make zero owning requests", async t => {
  let count = 0; setup(t, async () => { count++; throw new Error("must not fetch"); });
  for (const query of ["", "expectedPrincipalKey=bad", `expectedPrincipalKey=${key}&expectedPrincipalKey=${key}`])
    assert.equal((await route.GET(new Request(`https://test.invalid${routePath}?${query}`))).status, 400);
  state.__marketEnvelopePrincipal = { ...admin, email: "other@example.test" };
  assert.equal((await route.GET(request())).status, 403); assert.equal(count, 0);
});

test("anonymous, non-admin and scoped accounts fail closed with no-store", async t => {
  setup(t, async () => { throw new Error("must not dispatch"); }); state.__marketEnvelopePrincipal = null;
  const response = await route.GET(request()); assert.equal(response.status, 401); assert.equal(response.headers.get("cache-control"), "no-store");
  for (const role of ["viewer", "operator", "analyst"] as const) {
    state.__marketEnvelopePrincipal = { ...admin, role }; assert.equal((await route.GET(request())).status, 403);
  }
  state.__marketEnvelopePrincipal = { ...admin, scope: { platforms: ["京东"], channels: [], warehouses: [] } };
  assert.equal((await route.GET(request())).status, 403);
});

test("unknown and duplicate fields including cursor reach owning reader unchanged", async t => {
  let current = new URLSearchParams(); let count = 0;
  setup(t, async (input, init) => { count++; signature(input, init, current); return Response.json({ code: "invalid_request", error: "owning query rejected" }, { status: 400 }); });
  for (const raw of ["category=甲&category=乙", "unknown=keep", "cursor=old%3Aabc%3Asig&cursor=other", "limit=999", "previousItem=%7B%7D"]) {
    current = new URLSearchParams(raw); const response = await route.GET(request(raw)); assert.equal(response.status, 400);
    assert.match(JSON.stringify(await response.json()), /owning query rejected/);
  }
  assert.equal(count, 5);
});

test("server validates a successful continuation page but does not invent previous-item authority", async t => {
  const query = new URLSearchParams({ q: "STRASSE", cursor: "signed:ABC:cursor" });
  setup(t, async (input, init) => { signature(input, init, query); return Response.json(pythonPage, { headers: { "x-market-data-revision": revision } }); });
  const response = await route.GET(request(query.toString())); assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).page, pythonPage);
});

test("bad successful digests, identity structure, generation or header never receive a success envelope", async t => {
  let payload = pythonPage, header = revision;
  setup(t, async () => Response.json(payload, { headers: { "x-market-data-revision": header } }));
  for (const change of [
    (v: typeof pythonPage) => { v.pageDigest = "a".repeat(64); },
    (v: typeof pythonPage) => { v.items[0].identity.shop = "invented"; },
    (v: typeof pythonPage) => { v.items[0].dateMetadata.coverageVerified = true; },
    (v: typeof pythonPage) => { v.directoryGeneration = "not-hex"; },
  ]) {
    payload = structuredClone(pythonPage); change(payload);
    if (payload.pageDigest !== "a".repeat(64)) { const material = { ...payload }; delete material.pageDigest; payload.pageDigest = await scopeOptionsHash(material); }
    assert.equal((await route.GET(request("q=STRASSE"))).status, 503);
  }
  payload = pythonPage; header = "8:abcdef123456";
  assert.equal((await route.GET(request("q=STRASSE"))).status, 503);
});

test("unexpected upstream success cannot erase duplicate/unknown filters", async t => {
  setup(t, async () => Response.json(pythonPage, { headers: { "x-market-data-revision": revision } }));
  for (const raw of ["q=STRASSE&q=STRASSE", "q=STRASSE&unknown=x", "q=STRASSE&limit=21", "q=STRASSE&cursor=bad"]) {
    assert.equal((await route.GET(request(raw))).status, 503);
  }
});

test("revocation and initialization failure retain owning error and are never retried", async t => {
  let count = 0, status = 403;
  setup(t, async () => { count++; return Response.json({ error: "owning refusal", code: status === 403 ? "access_denied" : "options_not_ready" }, { status }); });
  for (status of [403, 503]) { const response = await route.GET(request()); assert.equal(response.status, status); assert.match(JSON.stringify(await response.json()), /owning refusal/); }
  assert.equal(count, 2);
});

test("abort propagates to reader and does not retry or return a success page", async t => {
  let start!: () => void; const begun = new Promise<void>(resolve => { start = resolve; }); let count = 0, aborted = false;
  setup(t, async (_input, init) => new Promise<Response>((_resolve, reject) => {
    count++; start(); init!.signal!.addEventListener("abort", () => { aborted = true; reject(new DOMException("aborted", "AbortError")); }, { once: true });
  }));
  const controller = new AbortController(); const pending = route.GET(new Request(request().url, { signal: controller.signal }));
  await begun; controller.abort(); assert.equal((await pending).status, 503); assert.equal(count, 1); assert.equal(aborted, true);
  assert.equal((await route.GET(new Request(request().url, { signal: controller.signal }))).status, 503); assert.equal(count, 1);
});
