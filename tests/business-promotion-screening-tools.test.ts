import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import { aiToolSurfaces, validateToolArguments, validateToolRegistry, type AiToolEntry } from "../lib/ai/tool-registry-contract";
registerHooks({ resolve(specifier, context, nextResolve) { return specifier === "cloudflare:workers" ? { url: "data:text/javascript,export const env={};", shortCircuit: true } : nextResolve(specifier, context); } });
const { aiToolRegistry, getToolsForPrincipal, getOpenAiTools, getAnthropicTools, executeRegisteredToolCall } = await import("../lib/ai/tool-registry");
const { aiHeaders, isPublicAiPath, requestDjangoAi } = await import("../lib/django/ai-service");
const { canonicalAiEdge, handleAiEdge } = await import("../lib/ai/django-edge");
const surface = "business_agent_screening_promotion_v1" as const;
const names = ["get_business_promotion_screening_package_v1", "get_business_promotion_screening_analysis_v1", "get_business_promotion_screening_budget_v1", "get_business_promotion_keyword_sku_v1"];
const entries = names.map(name => aiToolRegistry.find(entry => entry.name === name)!);
const keyword = entries[3];
const admin: AppPrincipal = { email: "promotion@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const context = { principal: admin, surface, requestId: "promotion-dispatch-1", providerCallId: "provider-call-1" };
const base = { reportId: "report-1", sourceKey: "ads", view: "keyword_sku" };
const standard = { reportId: "report-1", runId: "run-1", screeningId: "screen-1" };
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192", TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-promotion-secret-at-least-32-bytes" };
function isolated(t: TestContext) {
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]])), originalFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = originalFetch; for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
}
const response = (data: object, status = 200) => Response.json(data, { status, headers: { "x-ai-revision": "2" } });
const sha = (value: string) => createHash("sha256").update(value).digest("hex");
function payload(row = false) {
  return { schemaVersion: "business-promotion-keyword-sku-response-v1", bindingDigest: "a".repeat(64), responseDigest: "b".repeat(64),
    binding: { schemaVersion: "business-promotion-keyword-sku-binding-v1", reportBinding: { reportId: "report-1" }, sourceKey: "ads", view: "keyword_sku", baselineKey: null },
    authority: { reportBindingVerified: true, completeSourceTraversalForSelectedSources: true, entityDailyCoverageVerified: false,
      productMasterIdentityVerified: false, scopeMeaning: "selected_source_view_not_registered_report_coverage" },
    ...(row ? { row: { id: "c".repeat(64), rowIndex: 7 } } : { table: { pagination: { offset: 0 }, rows: [{ note: "来源文本仅为数据" }] } }) };
}

test("four fixed tools are isolated from all legacy surfaces and both providers agree", () => {
  validateToolRegistry(aiToolRegistry);
  assert.deepEqual(getToolsForPrincipal(admin, surface).map(e => e.name), names);
  assert.deepEqual(getOpenAiTools(admin, surface).map(e => e.function.name), names);
  assert.deepEqual(getAnthropicTools(admin, surface).map(e => e.name), names);
  for (const old of aiToolSurfaces.filter(s => s !== surface)) assert.equal(getToolsForPrincipal(admin, old).some(e => names.includes(e.name)), false);
  for (const role of ["viewer", "analyst", "operator"] as const) assert.deepEqual(getToolsForPrincipal({ ...admin, role }, surface), []);
  assert.deepEqual(getToolsForPrincipal({ ...admin, scope: { warehouses: [], channels: [], platforms: [] } }, surface), []);
  const oldNames = ["get_business_screening_package_v1", "get_business_screening_analysis_table_v1", "get_business_screening_budget_v1"];
  for (let index = 0; index < 3; index++) assert.deepEqual(entries[index].inputSchema, aiToolRegistry.find(e => e.name === oldNames[index])!.inputSchema);
  for (const entry of entries) { assert.equal(entry.risk, "read_only"); assert.equal(entry.execution.mode, "direct"); assert.deepEqual(entry.execution.allowedSurfaces, [surface]); }
});

test("keyword validates both exclusive selectors before any transport", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; return response(payload()); };
  validateToolArguments(base, keyword.inputSchema);
  validateToolArguments({ ...base, rowIndex: 7, rowId: "c".repeat(64) }, keyword.inputSchema);
  for (const extra of [{ rowIndex: 1 }, { rowId: "c".repeat(64) }, { rowIndex: 7, rowId: "c".repeat(64), offset: 0 },
    { rowIndex: 7, rowId: "c".repeat(64), limit: 20 }, { offset: true }, { offset: "0" }, { offset: -1 }, { offset: 250001 },
    { limit: 10 }, { baselineKey: null }, { reportId: "../a" }, { view: "sku" }, { runId: "run-1" }]) await assert.rejects(keyword.handler({ ...base, ...extra }, context));
  for (const principal of [{ ...admin, role: "viewer" as const }, { ...admin, scope: { warehouses: [], channels: [], platforms: [] } }]) await assert.rejects(keyword.handler(base, { ...context, principal }));
  for (const changed of [{ providerCallId: undefined }, { providerCallId: "" }, { requestId: "not valid" },
    { surface: "business_agent_screening_v1" as const }]) await assert.rejects(keyword.handler(base, { ...context, ...changed }));
  assert.equal(calls, 0);
});

test("keyword signs exact dispatch-bound reader POST and preserves owning page and row", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(init?.method, "POST"); assert.equal(init?.cache, "no-store");
    assert.equal(target.search, "");
    const sent = JSON.parse(String(init?.body)) as { name: string; arguments: Record<string, unknown>; providerCallId: string };
    assert.equal(sent.name, keyword.name); assert.equal(sent.providerCallId, context.providerCallId);
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("x-teruisi-request-id"), context.requestId);
    const message = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"), "POST", target.pathname,
      "", sha(String(init?.body)), headers.get("x-teruisi-principal")].join("\n");
    assert.equal(headers.get("x-teruisi-signature"), "v1="+createHmac("sha256", environment.TERUISI_DJANGO_INTERNAL_SECRET).update(message).digest("hex"));
    assert.equal(sent.arguments.sourceKey, "ads");
    return response(payload(Object.hasOwn(sent.arguments, "rowIndex")));
  };
  assert.deepEqual(await keyword.handler(base, context), payload());
  assert.deepEqual(await keyword.handler({ ...base, rowIndex: 7, rowId: "c".repeat(64) }, context), payload(true));
  assert.deepEqual(seen, Array(2).fill("/api/ai/promotion-tool-dispatch/promotion-dispatch-1"));
});

test("new first-three handlers use dispatch POST while old screening names keep GET routes", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); seen.push(`${init?.method} ${target.pathname}`);
    const sent = init?.method === "POST" ? JSON.parse(String(init.body)) as { name: string } : null;
    const kind = sent?.name.includes("package") || target.pathname.endsWith("/package") ? "package"
      : sent?.name.includes("analysis") || target.pathname.endsWith("/analysis") ? "analysis" : "budget";
    return response(kind === "package" ? { schemaVersion: "business-screening-role-package-v1", reportId: "report-1", evidenceRunId: "run-1", role: "promotion" }
      : { schemaVersion: `business-screening-${kind}-v1`, reference: { reportId: "report-1", evidenceRunId: "run-1", screeningIntent: { id: "screen-1" } } });
  };
  await entries[0].handler({ ...standard, role: "promotion" }, context);
  await entries[1].handler({ ...standard, mode: "native", dimension: "keyword", sourceKey: "ads" }, context);
  await entries[2].handler(standard, context);
  assert.deepEqual(seen, Array(3).fill("POST /api/ai/promotion-tool-dispatch/promotion-dispatch-1"));
  seen.length = 0;
  const oldNames = ["get_business_screening_package_v1", "get_business_screening_analysis_table_v1", "get_business_screening_budget_v1"];
  const old = oldNames.map(name => aiToolRegistry.find(entry => entry.name === name)!);
  const oldContext = { ...context, surface: "business_agent_screening_v1" as const };
  await old[0].handler({ ...standard, role: "promotion" }, oldContext);
  await old[1].handler({ ...standard, mode: "native", dimension: "keyword", sourceKey: "ads" }, oldContext);
  await old[2].handler(standard, oldContext);
  assert.deepEqual(seen, ["package", "analysis", "budget"].map(kind => `GET /api/ai/reports/report-1/screening/${kind}`));
});

test("keyword rejects mismatched bindings, false authority, oversized UTF8 and upstream errors", async t => {
  isolated(t); let data: object = payload(); globalThis.fetch = async () => response(data);
  for (const changed of [{ ...payload(), schemaVersion: "old" }, { ...payload(), authority: {} },
    { ...payload(), binding: { ...payload().binding, sourceKey: "other" } }, { ...payload(), binding: { ...payload().binding, baselineKey: "other" } },
    { ...payload(), table: { pagination: { offset: 1 } } }, { ...payload(), extra: "界".repeat(15000) }]) {
    data = changed; await assert.rejects(keyword.handler(base, context));
  }
  for (const status of [403, 409, 413, 503]) { globalThis.fetch = async () => response({ error: "blocked", code: "conflict" }, status); await assert.rejects(keyword.handler(base, context)); }
  let calls = 0; globalThis.fetch = async () => { calls++; return response(payload()); };
  const ctl = new AbortController(); ctl.abort(); await assert.rejects(keyword.handler(base, { ...context, signal: ctl.signal })); assert.equal(calls, 0);
});

test("human GET remains public; dispatch POST stays internal and requires matching signed ID", async t => {
  isolated(t); const target = "/api/ai/reports/report-1/promotion-keyword-sku";
  assert.equal(isPublicAiPath(target), true);
  const internal = "/api/ai/promotion-tool-dispatch/promotion-dispatch-1";
  assert.equal(isPublicAiPath(internal), false);
  for (const value of [target+"/extra", target+"-other", "/api/ai/reports/../promotion-keyword-sku"]) assert.equal(isPublicAiPath(value), false);
  let calls = 0; globalThis.fetch = async () => { calls++; return response(payload()); };
  await assert.rejects(requestDjangoAi(admin, { path: target, method: "POST" }));
  await assert.rejects(requestDjangoAi(admin, { path: target, method: "GET", service: "writer" }));
  await assert.rejects(requestDjangoAi(admin, { path: internal, method: "GET", service: "reader" }));
  await assert.rejects(requestDjangoAi(admin, { path: internal, method: "POST", service: "writer", payload: {} }, { requestId: context.requestId }));
  await assert.rejects(requestDjangoAi(admin, { path: internal, method: "POST", service: "reader", payload: {} }, { requestId: "wrong" }));
  assert.equal(calls, 0);
});

test("bounded HTTP whitespace margin never enlarges the complete semantic payload limit", async t => {
  isolated(t);
  const value = { ...payload(), padding: "" };
  value.padding = "x".repeat(37950 - Buffer.byteLength(JSON.stringify(value)));
  assert.equal(Buffer.byteLength(JSON.stringify(value)), 37950);
  const pretty = JSON.stringify(value, null, 2);
  assert.ok(Buffer.byteLength(pretty) > 38000 && Buffer.byteLength(pretty) <= 48000);
  globalThis.fetch = async () => new Response(pretty, { headers: { "content-type": "application/json", "x-ai-revision": "2" } });
  assert.deepEqual(await keyword.handler(base, context), value);
  const oversized = { ...value, padding: value.padding+"界".repeat(30) };
  assert.ok(Buffer.byteLength(JSON.stringify(oversized)) > 38000);
  globalThis.fetch = async () => response(oversized);
  await assert.rejects(keyword.handler(base, context), /字节容量/);
  // The parser also keeps a distinct bound on raw bytes, even pure whitespace.
  globalThis.fetch = async () => new Response(" ".repeat(48001)+JSON.stringify(payload()), { headers: { "content-type": "application/json", "x-ai-revision": "2" } });
  await assert.rejects(keyword.handler(base, context));
});

test("signed edge new catalog policy and audit work but old surface cannot dispatch", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async url => { const target = new URL(String(url)); seen.push(target.pathname); return response(target.pathname === "/api/ai/consumer" ? { ok: true } : payload()); };
  async function edge(body: object) {
    const raw = JSON.stringify(body), path = "/api/ai/internal/edge";
    const headers = await aiHeaders({ secret: environment.TERUISI_DJANGO_INTERNAL_SECRET, principal: admin, path, method: "POST", query: "", body: raw, requestId: "promotion-edge" });
    return handleAiEdge(new Request("https://synthetic.invalid"+path, { method: "POST", headers, body: raw }));
  }
  const catalog = await edge({ action: "catalog", surface }); assert.equal(catalog.status, 200);
  const listed = (await catalog.json() as { entries: AiToolEntry[] }).entries; assert.deepEqual(listed.map(e => e.name), names);
  const request = { action: "execute", surface, name: keyword.name, arguments: base,
    requestId: context.requestId, providerCallId: context.providerCallId, policyDigest: sha(canonicalAiEdge(listed)) };
  assert.equal((await edge({ ...request, policyDigest: "0".repeat(64) })).status, 403); assert.deepEqual(seen, []);
  const success = await edge(request); assert.equal(success.status, 200); assert.equal((await success.json() as { ok: boolean }).ok, true);
  assert.deepEqual(seen, ["/api/ai/consumer", "/api/ai/promotion-tool-dispatch/promotion-dispatch-1", "/api/ai/consumer"]);
  const denied = await executeRegisteredToolCall(keyword.name, base, { ...context, surface: "business_agent_screening_v1" }, { audit: async () => {} });
  assert.equal(denied.ok, false);
});
