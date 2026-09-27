import assert from "node:assert/strict";
import { build } from "esbuild";
import test, { type TestContext } from "node:test";
import { readFile } from "node:fs/promises";
import type { AppPrincipal } from "../lib/auth/authorization";

const actor: AppPrincipal = { email: "budget-route@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18221", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18222", TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-budget-builder-signature-secret-32-bytes" };
const origin = "https://fixture.invalid";
const base = "/api/ai/business-evidence/run-1/";
let serial = 0;
async function harness(t: TestContext) {
  const originalFetch = globalThis.fetch;
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = originalFetch; for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  const bundle = await build({ stdin: { contents: `
    export * as targets from './app/api/ai/business-evidence/[id]/budget-targets/route.ts';
    export * as preview from './app/api/ai/business-evidence/[id]/budget-preview/route.ts';
    export {setPrincipal} from '@/lib/auth/authorization';
    export {isPublicAiPath} from '@/lib/django/ai-service';
    export const serial=${serial++};`, resolveDir: process.cwd(), loader: "ts" },
    bundle: true, write: false, platform: "node", format: "esm", plugins: [{ name: "budget-builder-boundaries", setup(builder) {
      builder.onResolve({ filter: /^(?:@\/lib\/auth\/authorization|cloudflare:workers|@\/lib\/django\/ai-stream|@\/lib\/ai\/page-context)$/ }, args => ({ path: args.path, namespace: "boundary" }));
      builder.onLoad({ filter: /.*/, namespace: "boundary" }, args => {
        let contents: string;
        if (args.path.includes("authorization")) contents = `
          import {PublicApiError} from '@/lib/http/api-error';
          let principal=${JSON.stringify(actor)};
          export const setPrincipal=value=>{principal=value};
          export const requireAppPrincipal=async roles=>{if(roles&&!roles.includes(principal.role))throw new PublicApiError(403,'access_denied','role');return principal};
          export const requireUnrestrictedDataScope=value=>{if(value.scope!==null)throw new PublicApiError(403,'access_denied','scope')};
          export const authorizationErrorResponse=()=>null;`;
        else if (args.path === "cloudflare:workers") contents = "export const env={};";
        else if (args.path.includes("ai-stream")) contents = "export const requestDjangoAiStream=()=>{throw Error('unexpected model stream')};";
        else contents = "export const normalizeAiPageContext=value=>value;";
        return { contents, loader: "js", resolveDir: process.cwd() };
      });
    } }] });
  return await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`) as {
    targets: { GET(request: Request): Promise<Response> }; preview: { POST(request: Request): Promise<Response> };
    setPrincipal(principal: AppPrincipal): void; isPublicAiPath(path: string): boolean;
  };
}
const json = (data: object, status = 200) => Response.json(data, { status, headers: { "x-ai-revision": "9" } });
const post = (body: unknown, suffix = "budget-preview") => new Request(origin+base+suffix, { method: "POST", headers: { origin, "content-type": "application/json" }, body: JSON.stringify(body) });

test("budget builder has only GET targets and POST preview exports under the existing id segment", async () => {
  for (const [suffix, method] of [["budget-targets", "GET"], ["budget-preview", "POST"]]) {
    const code = await readFile(`app/api/ai/business-evidence/[id]/${suffix}/route.ts`, "utf8");
    const methods = [...code.matchAll(/export const (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*=/g)].map(match => match[1]);
    assert.deepEqual(methods, [method]);
    assert.match(code, /forwardAiRequest/);
  }
});

test("budget builder finite paths and exact query and JSON body use signed owning reader", async t => {
  const app = await harness(t); const calls: Request[] = [];
  for (const suffix of ["budget-targets", "budget-preview"]) {
    assert.equal(app.isPublicAiPath(base+suffix), true);
    for (const extra of ["/", "/extra", "-write"]) assert.equal(app.isPublicAiPath(base+suffix+extra), false);
  }
  globalThis.fetch = async (url, init) => { const request = new Request(url, init); calls.push(request); return json({ schemaVersion: "synthetic-only", items: [] }); };
  const query = "sourceKey=exact_source&dimension=sku&offset=20";
  const targets = await app.targets.GET(new Request(origin+base+"budget-targets?"+query));
  assert.equal(targets.status, 200); assert.equal(targets.headers.get("cache-control"), "no-store");
  const body = { evidenceBinding: { evidenceRunId: "run-1", evidenceVersion: 3, catalogDigest: "c".repeat(64) }, budgetPlan: { targets: [{ rowId: "r1", ownerRole: "中文\n\"逐项\"\\精确" }], totalBudgetCents: 10000 } };
  const preview = await app.preview.POST(post(body)); assert.equal(preview.status, 200);
  assert.equal(calls.length, 2);
  assert.equal(new URL(calls[0].url).search.slice(1), query);
  assert.equal(await calls[0].text(), ""); assert.equal(calls[0].method, "GET");
  assert.deepEqual(await calls[1].json(), body); assert.equal(calls[1].method, "POST");
  for (const request of calls) {
    assert.equal(new URL(request.url).origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.ok(request.headers.get("x-teruisi-signature"));
    const encoded = request.headers.get("x-teruisi-principal")!;
    assert.deepEqual(JSON.parse(Buffer.from(encoded, "base64url").toString()), actor);
  }
});

test("budget builder rejects unauthorized scope, cross-origin, malformed and oversized bodies before forwarding", async t => {
  const app = await harness(t); let requests = 0;
  globalThis.fetch = async () => { requests++; return json({}); };
  for (const principal of [{ ...actor, role: "viewer" as const }, { ...actor, role: "operator" as const }, { ...actor, role: "analyst" as const }, { ...actor, scope: { warehouses: [], channels: [], platforms: [] } }]) {
    app.setPrincipal(principal);
    assert.equal((await app.targets.GET(new Request(origin+base+"budget-targets"))).status, 403);
    assert.equal((await app.preview.POST(post({}))).status, 403);
  }
  app.setPrincipal(actor);
  assert.equal((await app.preview.POST(new Request(origin+base+"budget-preview", { method: "POST", headers: { origin: "https://other.invalid", "content-type": "application/json" }, body: "{}" }))).status, 403);
  for (const [body, contentType, expected] of [["[]", "application/json", 400], ["{", "application/json", 400], ["{}", "text/plain", 415], [JSON.stringify({ text: "中".repeat(23000) }), "application/json", 413]] as const) {
    assert.equal((await app.preview.POST(new Request(origin+base+"budget-preview", { method: "POST", headers: { origin, "content-type": contentType }, body }))).status, expected);
  }
  assert.equal(requests, 0);
});

test("budget builder preserves duplicate query and unknown body fields for owning validation and never retries conflict", async t => {
  const app = await harness(t); const calls: Request[] = [];
  globalThis.fetch = async (url, init) => { calls.push(new Request(url, init)); return json({ error: "synthetic owning rejection", code: "conflict" }, 409); };
  const query = "sourceKey=a&sourceKey=b&dimension=sku&offset=01&extra=1";
  assert.equal((await app.targets.GET(new Request(origin+base+"budget-targets?"+query))).status, 409);
  assert.equal(new URL(calls[0].url).search.slice(1), query);
  const body = { evidenceBinding: {}, budgetPlan: {}, extra: "must-reject-owning-side" };
  assert.equal((await app.preview.POST(post(body))).status, 409);
  assert.deepEqual(await calls[1].json(), body);
  assert.equal(calls.length, 2);
});

test("budget builder abort before dispatch cannot start a reader request", async t => {
  const app = await harness(t); let calls = 0;
  globalThis.fetch = async () => { calls++; return json({}); };
  const controller = new AbortController(); controller.abort();
  const response = await app.targets.GET(new Request(origin+base+"budget-targets", { signal: controller.signal }));
  assert.equal(response.status, 503); assert.equal(calls, 0);
});


test("budget builder result remains no-store JSON and owning permission denial remains visible", async t => {
  const app = await harness(t); let status = 200; let calls = 0;
  const data = { content: "<script>synthetic untrusted text</script>", budgetPlan: { totalBudgetCents: 10000 }, limitations: ["条件试算"] };
  globalThis.fetch = async () => { calls++; return json(status === 200 ? data : { error: "synthetic owner changed", code: "access_denied" }, status); };
  const result = await app.preview.POST(post({ evidenceBinding: {}, budgetPlan: {} }));
  assert.equal(result.status, 200); assert.deepEqual(await result.json(), data);
  assert.match(result.headers.get("content-type")!, /application\/json/);
  assert.equal(result.headers.get("cache-control"), "no-store");
  assert.equal(result.headers.has("content-disposition"), false); assert.equal(result.headers.has("x-ai-generated"), false);
  status = 403;
  const denied = await app.targets.GET(new Request(origin+base+"budget-targets?sourceKey=ads&dimension=sku&offset=0"));
  assert.equal(denied.status, 403); assert.deepEqual(await denied.json(), { error: "synthetic owner changed", code: "access_denied" });
  assert.equal(calls, 2);
});
