import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";

const principal: AppPrincipal = { email: "mapping-reader@example.invalid", displayName: "Synthetic mapping", role: "admin", scope: null };
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18321", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18322", TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-mapping-v2-reader-signing-32-bytes" };
const base = "/api/ai/business-evidence/run-1/", origin = "https://fixture.invalid";
const json = (data: object, status = 200) => Response.json(data, { status, headers: { "x-ai-revision": "10" } });
let sequence = 0;
async function harness(t: TestContext) {
  const originalFetch = globalThis.fetch, previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = originalFetch; for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  const output = await build({ stdin: { contents: `
    export * as mappingV2 from './app/api/ai/business-evidence/[id]/mapping-v2/route.ts';
    export * as legacy from './app/api/ai/business-evidence/[id]/mapping/route.ts';
    export {setPrincipal} from '@/lib/auth/authorization';
    export {isPublicAiPath} from '@/lib/django/ai-service';
    export const sequence=${sequence++};`, resolveDir: process.cwd(), loader: "ts" },
    bundle: true, write: false, platform: "node", format: "esm", plugins: [{ name: "mapping-reader-boundaries", setup(builder) {
      builder.onResolve({ filter: /^(?:@\/lib\/auth\/authorization|cloudflare:workers|@\/lib\/django\/ai-stream|@\/lib\/ai\/page-context)$/ }, args => ({ path: args.path, namespace: "boundary" }));
      builder.onLoad({ filter: /.*/, namespace: "boundary" }, args => {
        let contents: string;
        if (args.path.includes("authorization")) contents = `
          import {PublicApiError} from '@/lib/http/api-error';let actor=${JSON.stringify(principal)};
          export const setPrincipal=value=>{actor=value};
          export const requireAppPrincipal=async roles=>{if(roles&&!roles.includes(actor.role))throw new PublicApiError(403,'access_denied','role');return actor};
          export const requireUnrestrictedDataScope=value=>{if(value.scope!==null)throw new PublicApiError(403,'access_denied','scope')};
          export const authorizationErrorResponse=()=>null;`;
        else if (args.path === "cloudflare:workers") contents = "export const env={};";
        else if (args.path.includes("ai-stream")) contents = "export const requestDjangoAiStream=()=>{throw Error('unexpected model stream')};";
        else contents = "export const normalizeAiPageContext=value=>value;";
        return { contents, loader: "js", resolveDir: process.cwd() };
      });
    } }] });
  return await import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}`) as {
    mappingV2: { GET(request: Request): Promise<Response> }; legacy: { GET(request: Request): Promise<Response> };
    setPrincipal(actor: AppPrincipal): void; isPublicAiPath(path: string): boolean;
  };
}

test("mapping v2 and legacy routes export only GET without changing old route", async () => {
  for (const suffix of ["mapping", "mapping-v2"]) {
    const code = await readFile(`app/api/ai/business-evidence/[id]/${suffix}/route.ts`, "utf8");
    assert.deepEqual([...code.matchAll(/export const (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*=/g)].map(match => match[1]), ["GET"]);
    assert.match(code, /forwardAiRequest/);
  }
});

test("mapping old and v2 finite paths both remain on the signed owning reader", async t => {
  const app = await harness(t), calls: Request[] = [];
  globalThis.fetch = async (url, init) => { calls.push(new Request(url, init)); return json({ syntheticOnly: true }); };
  assert.deepEqual(Object.keys(app.mappingV2), ["GET"]); assert.deepEqual(Object.keys(app.legacy), ["GET"]);
  for (const [suffix, route, query] of [["mapping", app.legacy, "sales=erp&master=products"], ["mapping-v2", app.mappingV2, "salesKey=erp&masterKey=products&offset=20&limit=20"]] as const) {
    assert.equal(app.isPublicAiPath(base+suffix), true);
    for (const extra of ["/", "/extra", "-write"]) assert.equal(app.isPublicAiPath(base+suffix+extra), false);
    const response = await route.GET(new Request(origin+base+suffix+"?"+query));
    assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
    const request = calls.at(-1)!, url = new URL(request.url);
    assert.equal(url.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL); assert.equal(url.pathname, base+suffix);
    assert.equal(url.search.slice(1), query); assert.equal(request.method, "GET"); assert.equal(await request.text(), "");
    const header = (name: string) => request.headers.get(name)!;
    assert.deepEqual(JSON.parse(Buffer.from(header("x-teruisi-principal"), "base64url").toString()), principal);
    const canonical = ["v1", header("x-teruisi-timestamp"), header("x-teruisi-request-id"), "GET", url.pathname, query,
      createHash("sha256").update("").digest("hex"), header("x-teruisi-principal")].join("\n");
    assert.equal(header("x-teruisi-signature"), "v1="+createHmac("sha256", environment.TERUISI_DJANGO_INTERNAL_SECRET).update(canonical).digest("hex"));
  }
  assert.equal(calls.length, 2);
});

test("mapping v2 admin and unrestricted checks run before forwarding", async t => {
  const app = await harness(t); let calls = 0;
  globalThis.fetch = async () => { calls++; return json({}); };
  for (const actor of ["viewer", "operator", "analyst"].map(role => ({ ...principal, role: role as AppPrincipal["role"] })).concat([{ ...principal, scope: { warehouses: [], channels: [], platforms: [] } }])) {
    app.setPrincipal(actor);
    assert.equal((await app.mappingV2.GET(new Request(origin+base+"mapping-v2?salesKey=erp&masterKey=products"))).status, 403);
  }
  assert.equal(calls, 0);
});

test("mapping v2 retains every query value for owning validation and returns owner rejection without retry", async t => {
  const app = await harness(t); const calls: Request[] = [];
  globalThis.fetch = async (url, init) => { calls.push(new Request(url, init)); return json({ error: "synthetic owning rejection", code: "invalid_request" }, 400); };
  for (const query of ["salesKey=a&salesKey=b&masterKey=m&offset=0&limit=20", "salesKey=erp&masterKey=products&offset=01&limit=1&extra=value", "salesKey=..%2Fprivate&masterKey=products"]) {
    const result = await app.mappingV2.GET(new Request(origin+base+"mapping-v2?"+query));
    assert.equal(result.status, 400); assert.equal(new URL(calls.at(-1)!.url).search.slice(1), query);
    assert.deepEqual(await result.json(), { error: "synthetic owning rejection", code: "invalid_request" });
  }
  assert.equal(calls.length, 3);
});

test("mapping v2 binding, result and page digests are preserved as JSON without attachment conversion", async t => {
  const app = await harness(t);
  const data = { schemaVersion: "business-product-mapping-response-v2", binding: { evidenceRunId: "run-1", sales: { sourceKey: "erp" }, master: { sourceKey: "products" } }, bindingDigest: "b".repeat(64),
    mapping: { schemaVersion: "business-product-mapping-v2", algorithmVersion: "exact-product-partition-v1", resultDigest: "c".repeat(64), pageDigest: "d".repeat(64),
      rows: [{ rowIndex: 0, id: "e".repeat(64), status: "unmatched", metrics: { netSalesCents: 123 } }], pagination: { offset: 0, limit: 20, total: 1, nextOffset: null }, content: "<script>synthetic source text</script>" } };
  assert.ok(Buffer.byteLength(JSON.stringify(data)) <= 65536);
  globalThis.fetch = async () => json(data);
  const result = await app.mappingV2.GET(new Request(origin+base+"mapping-v2?salesKey=erp&masterKey=products&offset=0&limit=20"));
  assert.equal(result.status, 200); assert.deepEqual(await result.json(), data);
  assert.match(result.headers.get("content-type")!, /application\/json/);
  assert.equal(result.headers.has("content-disposition"), false); assert.equal(result.headers.has("x-ai-generated"), false);
});

test("mapping v2 abort before dispatch never contacts a backend", async t => {
  const app = await harness(t); let calls = 0;
  globalThis.fetch = async () => { calls++; return json({}); };
  const controller = new AbortController(); controller.abort();
  assert.equal((await app.mappingV2.GET(new Request(origin+base+"mapping-v2", { signal: controller.signal }))).status, 503);
  assert.equal(calls, 0);
});
