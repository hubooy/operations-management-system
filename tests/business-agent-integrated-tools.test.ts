import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import { aiToolSurfaces, validateToolArguments, validateToolRegistry, type AiToolEntry, type AiToolSurface } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) { return specifier === "cloudflare:workers" ? { url: "data:text/javascript,export const env={};", shortCircuit: true } : nextResolve(specifier, context); } });
const { aiToolRegistry, getToolsForPrincipal, getOpenAiTools, getAnthropicTools, executeRegisteredToolCall } = await import("../lib/ai/tool-registry");
const { canonicalAiEdge, handleAiEdge } = await import("../lib/ai/django-edge");
const { aiHeaders } = await import("../lib/django/ai-service");
const admin: AppPrincipal = { email: "synthetic@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const surface = "business_agent_integrated_v1" as const;
const names = ["get_business_integrated_directory_v1", "get_business_integrated_analysis_table_v1", "get_business_integrated_budget_v1"];
const continuationNames = ["get_business_netshop_continuation_page", "get_business_sales_continuation_page", "get_business_market_continuation_page", "get_business_finance_source_page"];
const [directory, table, budget] = names.map(name => aiToolRegistry.find(entry => entry.name === name)!);
const strip = ({ handler, ...entry }: AiToolEntry) => { void handler; return entry; };
const sha = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");
const context = { principal: admin, surface, requestId: "integrated-fixture" };
const base = { reportId: "report-1", runId: "run-1" }, pairKey = "a".repeat(64), baselinePairKey = "b".repeat(64);
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192", TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-integrated-secret-at-least-32-bytes" };
function isolated(t: TestContext) {
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]])), oldFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
}
const response = (data: object, status = 200) => Response.json(data, { status, headers: { "x-ai-revision": "1" } });
const boundPayload = (name: string) => ({
  schemaVersion: name === names[0] ? "business-integrated-directory-v1" : name === names[1] ? "business-integrated-analysis-v1" : "business-integrated-budget-v1",
  reference: { evidenceRunId: base.runId, reportId: base.reportId },
});

test("44 legacy entries and every old surface/role/scope catalog remain byte-identical", async () => {
  const baseline = JSON.parse(await readFile(new URL("./fixtures/business-agent-integrated-legacy-catalog.json", import.meta.url), "utf8"));
  assert.deepEqual(aiToolSurfaces.slice(0, baseline.legacySurfaces.length), baseline.legacySurfaces);
  assert.ok(aiToolSurfaces.includes(surface));
  const old = aiToolRegistry.filter(entry => baseline.legacyToolNames.includes(entry.name));
  assert.deepEqual(old.map(entry => entry.name), baseline.legacyToolNames);
  assert.deepEqual({ count: old.length, sha256: sha(canonicalAiEdge(old.map(strip))) }, baseline.registry);
  const catalogs: Record<string, unknown> = {};
  for (const oldSurface of baseline.legacySurfaces as AiToolSurface[]) for (const role of ["viewer", "analyst", "operator", "admin"] as const) for (const scoped of [false, true]) {
    const entries = getToolsForPrincipal({ ...admin, role, scope: scoped ? { warehouses: [], channels: [], platforms: [] } : null }, oldSurface)
      .filter(entry => !continuationNames.includes(entry.name)).map(strip);
    catalogs[`${oldSurface}/${role}/${scoped ? "scoped" : "unscoped"}`] = { count: entries.length, sha256: sha(canonicalAiEdge(entries)) };
  }
  assert.deepEqual(catalogs, baseline.catalogs);
  await mkdir(".runtime/business-agent-integrated-catalog", { recursive: true });
  await writeFile(".runtime/business-agent-integrated-catalog/after.json", JSON.stringify({ passed: true, syntheticOnly: true, registry: baseline.registry, catalogs }, null, 2));
});

test("three new tools are exclusive to the integrated admin-unscoped surface", () => {
  validateToolRegistry(aiToolRegistry);
  assert.deepEqual(getToolsForPrincipal(admin, surface).map(entry => entry.name), names);
  assert.deepEqual(getOpenAiTools(admin, surface).map(entry => entry.function.name), names);
  assert.deepEqual(getAnthropicTools(admin, surface).map(entry => entry.name), names);
  for (const old of aiToolSurfaces.filter(value => value !== surface)) assert.equal(getToolsForPrincipal(admin, old).some(entry => names.includes(entry.name)), false);
  for (const role of ["viewer", "analyst", "operator"] as const) assert.deepEqual(getToolsForPrincipal({ ...admin, role }, surface), []);
  assert.deepEqual(getToolsForPrincipal({ ...admin, scope: { warehouses: [], channels: [], platforms: [] } }, surface), []);
  for (const tool of [directory, table, budget]) {
    assert.deepEqual(tool.execution.allowedSurfaces, [surface]);
    assert.equal(tool.execution.maxResultCharacters, 40000); assert.equal(tool.execution.maxCallsPerRequest, 8);
    assert.equal("limit" in tool.inputSchema.properties, false);
  }
  assert.match(table.description, /table\.pagination\.nextOffset/);
  assert.match(budget.description, /budget\.pagination\.nextOffset/);
});

test("fixed report/run IDs, integer bounds, no digests or page-size parameters", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; return response({ ok: true }); };
  for (const [tool, max] of [[directory, 47], [budget, 99]] as const) {
    validateToolArguments(base, tool.inputSchema); validateToolArguments({ ...base, offset: max }, tool.inputSchema);
    for (const extra of [{ offset: max+1 }, { offset: -1 }, { offset: true }, { offset: null }, { offset: 0.5 }, { limit: 20 }, { catalogDigest: pairKey }, { budgetRef: {} }]) {
      assert.throws(() => validateToolArguments({ ...base, ...extra }, tool.inputSchema));
      await assert.rejects(tool.handler({ ...base, ...extra }, context), /参数无效/);
    }
    for (const args of [{ runId: "run-1" }, { reportId: "report-1" }, { ...base, reportId: "../private" }, { ...base, runId: "x".repeat(161) }]) await assert.rejects(tool.handler(args, context), /参数无效/);
  }
  assert.equal(calls, 0);
});

test("native and mapped selectors are mutually exclusive even when handler called directly", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; return response({ ok: true }); };
  const native = { ...base, mode: "native", dimension: "shop", sourceKey: "sales" }, mapped = { ...base, mode: "mapped", dimension: "sku", pairKey };
  validateToolArguments(native, table.inputSchema); validateToolArguments(mapped, table.inputSchema);
  for (const args of [{ ...base, mode: "native", dimension: "shop" }, { ...native, pairKey }, { ...native, baselinePairKey }, { ...mapped, sourceKey: "sales" }, { ...mapped, baselineKey: "before" }, { ...mapped, dimension: "keyword" }, { ...mapped, pairKey: "BAD" }, { ...mapped, baselinePairKey: "B".repeat(64) }, { ...mapped, offset: true }, { ...mapped, offset: null }, { ...mapped, offset: 250001 }, { ...native, dimension: "customer" }, { ...native, limit: 20 }, { ...native, mode: "auto" }]) await assert.rejects(table.handler(args, context), /参数无效/);
  assert.equal(calls, 0);
});

test("three signed owning-reader paths forward exact IDs and actual offsets without limit", async t => {
  isolated(t); const observed: { path: string; query: Record<string, string> }[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(init?.method, "GET"); assert.ok(new Headers(init?.headers).has("x-teruisi-signature"));
    observed.push({ path: target.pathname, query: Object.fromEntries(target.searchParams) });
    return response(boundPayload(target.pathname.endsWith("integrated-directory") ? directory.name : target.pathname.endsWith("integrated-budget") ? budget.name : table.name));
  };
  assert.deepEqual(await directory.handler(base, context), boundPayload(directory.name));
  await budget.handler({ ...base, offset: 1 }, context);
  await table.handler({ ...base, mode: "native", dimension: "category", sourceKey: "sales", baselineKey: "before", offset: 7 }, context);
  await table.handler({ ...base, mode: "mapped", dimension: "spu", pairKey, baselinePairKey }, context);
  assert.deepEqual(observed, [
    { path: "/api/ai/reports/report-1/integrated-directory", query: { runId: "run-1", offset: "0" } },
    { path: "/api/ai/reports/report-1/integrated-budget", query: { runId: "run-1", offset: "1" } },
    { path: "/api/ai/reports/report-1/integrated-analysis-table", query: { runId: "run-1", offset: "7", mode: "native", dimension: "category", sourceKey: "sales", baselineKey: "before" } },
    { path: "/api/ai/reports/report-1/integrated-analysis-table", query: { runId: "run-1", offset: "0", mode: "mapped", dimension: "spu", pairKey, baselinePairKey } },
  ]);
});

test("every integrated tool enforces exact serialized UTF8 size without truncation", async t => {
  isolated(t); let payload: object = {}; globalThis.fetch = async () => response(payload);
  const entries = [[directory, base], [budget, base], [table, { ...base, mode: "native", sourceKey: "sales", dimension: "shop" }]] as const;
  for (const [tool, args] of entries) {
    const empty = { ...boundPayload(tool.name), text: "" }, padding = 38000-Buffer.byteLength(JSON.stringify(empty));
    payload = { ...empty, text: "a".repeat(padding) };
    assert.equal(Buffer.byteLength(JSON.stringify(payload)), 38000);
    assert.deepEqual(await tool.handler(args, context), payload);
    for (const value of ["a".repeat(padding+1), "中".repeat(13000), '中\n"'.repeat(6000)]) {
      payload = { ...empty, text: value };
      await assert.rejects(tool.handler(args, context), /不得截断/);
    }
  }
});

test("each receipt must match the precise tool schema and both fixed identities", async t => {
  isolated(t); let payload: object = {}; globalThis.fetch = async () => response(payload);
  for (const [tool, args] of [[directory, base], [budget, base], [table, { ...base, mode: "mapped", dimension: "sku", pairKey }]] as const) {
    const good = boundPayload(tool.name);
    for (const invalid of [{}, { ...good, schemaVersion: undefined }, { ...good, schemaVersion: "business-evidence-directory-v2" },
      ...names.filter(name => name !== tool.name).map(name => ({ ...good, schemaVersion: boundPayload(name).schemaVersion })),
      ...[undefined, null, [], "reference", true, 1, {}, { reportId: base.reportId }, { evidenceRunId: base.runId },
        { ...good.reference, reportId: "other-report" }, { ...good.reference, evidenceRunId: "other-run" },
        { ...good.reference, reportId: 1 }, { ...good.reference, evidenceRunId: true }].map(reference => ({ ...good, reference }))]) {
      payload = invalid;
      await assert.rejects(tool.handler(args, context), /回执协议或报告证据身份不一致/);
    }
    payload = good; assert.deepEqual(await tool.handler(args, context), good);
  }
});

test("absent budget and cancellation propagate rather than synthesize success", async t => {
  isolated(t); globalThis.fetch = async () => response({ error: "没有固定预算", code: "not_found" }, 404);
  await assert.rejects(budget.handler(base, context));
  let calls = 0; globalThis.fetch = async () => { calls++; return response({ rows: [] }); };
  const ctl = new AbortController(); ctl.abort();
  await assert.rejects(directory.handler(base, { ...context, signal: ctl.signal }));
  assert.equal(calls, 0);
});

test("signed edge keeps policy digest, authorization, audit and old-surface denial", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async (url) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    if (target.pathname === "/api/ai/consumer") return response({ ok: true });
    if (target.pathname === "/api/ai/reports/report-1/integrated-directory") return response({ ...boundPayload(directory.name), items: [], nextOffset: null });
    throw new Error("Unexpected request "+target.pathname);
  };
  async function edge(body: object, actor = admin) {
    const raw = JSON.stringify(body), path = "/api/ai/internal/edge";
    const headers = await aiHeaders({ secret: environment.TERUISI_DJANGO_INTERNAL_SECRET, principal: actor, path, method: "POST", query: "", body: raw, requestId: "integrated-edge" });
    return handleAiEdge(new Request("https://synthetic.invalid"+path, { method: "POST", headers, body: raw }));
  }
  const catalogResponse = await edge({ action: "catalog", surface }); assert.equal(catalogResponse.status, 200);
  const catalog = (await catalogResponse.json() as { entries: AiToolEntry[] }).entries; assert.deepEqual(catalog.map(e => e.name), names);
  const request = { action: "execute", surface, name: directory.name, arguments: base, requestId: "integrated-edge", policyDigest: sha(canonicalAiEdge(catalog)) };
  assert.equal((await edge({ ...request, policyDigest: "0".repeat(64) })).status, 403); assert.deepEqual(seen, []);
  const success = await edge(request); assert.equal(success.status, 200); assert.equal((await success.json() as { ok: boolean }).ok, true);
  assert.deepEqual(seen, ["/api/ai/consumer", "/api/ai/reports/report-1/integrated-directory", "/api/ai/consumer"]);
  assert.equal((await edge({ ...request, surface: "ai_agent" })).status, 403);
  const denied = await executeRegisteredToolCall(directory.name, base, { ...context, surface: "ai_chat" }); assert.equal(denied.ok, false);
});
