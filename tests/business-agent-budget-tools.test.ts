import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import { aiToolSurfaces, validateToolArguments, validateToolRegistry, type AiToolEntry, type AiToolSurface } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) {
  return specifier === "cloudflare:workers" ? { url: "data:text/javascript,export const env={};", shortCircuit: true } : nextResolve(specifier, context);
} });
const { aiToolRegistry, getToolsForPrincipal, getOpenAiTools, getAnthropicTools, executeRegisteredToolCall } = await import("../lib/ai/tool-registry");
const { canonicalAiEdge, handleAiEdge } = await import("../lib/ai/django-edge");
const { aiHeaders } = await import("../lib/django/ai-service");
const admin: AppPrincipal = { email: "synthetic@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const names = ["get_business_budget_directory_v1", "get_business_budget_analysis_table_v1", "get_business_budget_scenarios_v1"];
const continuationNames = ["get_business_netshop_continuation_page", "get_business_sales_continuation_page", "get_business_market_continuation_page", "get_business_finance_source_page"];
const directory = aiToolRegistry.find(entry => entry.name === names[0])!;
const table = aiToolRegistry.find(entry => entry.name === names[1])!;
const strip = ({ handler, ...entry }: AiToolEntry) => { void handler; return entry; };
const sha = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");
const context = { principal: admin, surface: "business_agent_budget_v1" as const, requestId: "synthetic-v2" };
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192", TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-v2-internal-secret-at-least-32-bytes" };
function isolated(t: TestContext) {
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]])), oldFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
}
const response = (data: object) => Response.json(data, { headers: { "x-ai-revision": "1" } });

test("all preexisting catalog canonical hashes and entry metadata remain exactly unchanged", async () => {
  const baseline = JSON.parse(await readFile(new URL("./fixtures/business-agent-budget-legacy-catalog.json", import.meta.url), "utf8"));
  assert.deepEqual(aiToolSurfaces.filter(surface => baseline.legacySurfaces.includes(surface)), baseline.legacySurfaces);
  assert.deepEqual(aiToolSurfaces.slice(0, baseline.legacySurfaces.length), baseline.legacySurfaces);
  assert.ok(aiToolSurfaces.includes(context.surface));
  const oldEntries = aiToolRegistry.filter(entry => !continuationNames.includes(entry.name)
    && entry.execution.allowedSurfaces.some(surface => baseline.legacySurfaces.includes(surface)));
  assert.deepEqual({ count: oldEntries.length, sha256: sha(canonicalAiEdge(oldEntries.map(strip))) }, baseline.registry);
  const actual: Record<string, { count: number; sha256: string }> = {};
  for (const surface of baseline.legacySurfaces as AiToolSurface[]) for (const role of ["viewer", "analyst", "operator", "admin"] as const) for (const scoped of [false, true]) {
    const entries = getToolsForPrincipal({ ...admin, role, scope: scoped ? { warehouses: [], channels: [], platforms: [] } : null }, surface)
      .filter(entry => !continuationNames.includes(entry.name)).map(strip);
    actual[`${surface}/${role}/${scoped ? "scoped" : "unscoped"}`] = { count: entries.length, sha256: sha(canonicalAiEdge(entries)) };
  }
  assert.deepEqual(actual, baseline.catalogs);
  await mkdir(".runtime/business-agent-budget-catalog", { recursive: true });
  await writeFile(".runtime/business-agent-budget-catalog/after.json", JSON.stringify({ passed: true, syntheticOnly: true, registry: baseline.registry, catalogs: actual }, null, 2));
});

test("new surface contains exactly three admin-only tools and never widens old surfaces", () => {
  validateToolRegistry(aiToolRegistry);
  assert.deepEqual(getToolsForPrincipal(admin, "business_agent_budget_v1").map(entry => entry.name), names);
  assert.deepEqual(getOpenAiTools(admin, "business_agent_budget_v1").map(entry => entry.function.name), names);
  assert.deepEqual(getAnthropicTools(admin, "business_agent_budget_v1").map(entry => entry.name), names);
  for (const surface of aiToolSurfaces.filter(value => value !== "business_agent_budget_v1")) assert.equal(getToolsForPrincipal(admin, surface).some(entry => names.includes(entry.name)), false);
  for (const role of ["viewer", "analyst", "operator"] as const) assert.deepEqual(getToolsForPrincipal({ ...admin, role }, "business_agent_budget_v1"), []);
  assert.deepEqual(getToolsForPrincipal({ ...admin, scope: { warehouses: [], channels: [], platforms: [] } }, "business_agent_budget_v1"), []);
  for (const entry of [directory, table, aiToolRegistry.find(entry => entry.name === names[2])!]) assert.deepEqual(entry.execution.allowedSurfaces, ["business_agent_budget_v1"]);
  assert.deepEqual(table.inputSchema, aiToolRegistry.find(entry => entry.name === "get_business_analysis_table")!.inputSchema);
});

test("directory has fixed page length and rejects model-supplied binding or arbitrary requests", () => {
  validateToolArguments({ runId: "run-1" }, directory.inputSchema);
  validateToolArguments({ runId: "run-1", offset: 47 }, directory.inputSchema);
  for (const bad of [{ runId: "../private" }, { runId: "run-1", offset: -1 }, { runId: "run-1", offset: 48 }, { runId: "run-1", offset: true }, { runId: "run-1", offset: 0.5 }, { runId: "run-1", limit: 1 }, { runId: "run-1", evidenceVersion: 1 }, { runId: "run-1", catalogDigest: "a".repeat(64) }]) assert.throws(() => validateToolArguments(bad, directory.inputSchema));
  for (const bad of [{ runId: "run-1", sourceKey: "a", dimension: "customer" }, { runId: "run-1", sourceKey: "a", dimension: "shop", limit: 21 }]) assert.throws(() => validateToolArguments(bad, table.inputSchema));
});

test("directory uses signed owning reader, fixed limit and exact UTF8 byte bound", async t => {
  isolated(t);
  let payload: Record<string, unknown> = { schemaVersion: "business-evidence-directory-page-v2", runId: "run-1", evidenceVersion: 9, catalogDigest: "d".repeat(64), items: [], nextOffset: null };
  const calls: URL[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); calls.push(target);
    assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(target.pathname, "/api/ai/business-evidence/run-1/sources");
    assert.equal(target.searchParams.get("limit"), "20"); assert.equal(init?.method, "GET");
    assert.ok(new Headers(init?.headers).has("x-teruisi-signature"));
    return response(payload);
  };
  assert.deepEqual(await directory.handler({ runId: "run-1" }, context), payload);
  assert.equal(calls[0].searchParams.get("offset"), "0");
  await directory.handler({ runId: "run-1", offset: 20 }, context); assert.equal(calls[1].searchParams.get("offset"), "20");
  payload = { text: "a".repeat(37_989) }; assert.equal(Buffer.byteLength(JSON.stringify(payload)), 38_000);
  assert.deepEqual(await directory.handler({ runId: "run-1" }, context), payload);
  payload = { text: "a".repeat(37_990) }; await assert.rejects(directory.handler({ runId: "run-1" }, context), /不得截断/);
  payload = { text: "中".repeat(13_000) }; assert.ok(JSON.stringify(payload).length < 38_000);
  await assert.rejects(directory.handler({ runId: "run-1" }, context), /不得截断/);
});

test("table reuses exact old forwarding and result bounds without accepting new filters", async t => {
  isolated(t);
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); assert.equal(target.pathname, "/api/ai/business-evidence/run-1/analysis");
    assert.deepEqual(Object.fromEntries(target.searchParams), { limit: "10", sourceKey: "sales", dimension: "shop", baselineKey: "previous", offset: "7" });
    assert.equal(init?.method, "GET"); return response({ rows: [], total: 0 });
  };
  assert.deepEqual(await table.handler({ runId: "run-1", sourceKey: "sales", dimension: "shop", baselineKey: "previous", offset: 7 }, context), { rows: [], total: 0 });
  globalThis.fetch = async () => response({ text: "a".repeat(38_001) });
  await assert.rejects(table.handler({ runId: "run-1", sourceKey: "sales", dimension: "shop" }, context), /不得截断/);
});

test("v2 table enforces serialized UTF8 bytes for Chinese and escaped text without changing the legacy handler", async t => {
  isolated(t);
  let payload = { text: "中".repeat(12_663) };
  let calls = 0;
  globalThis.fetch = async () => { calls++; return response(payload); };
  const args = { runId: "run-1", sourceKey: "sales", dimension: "shop" };
  assert.equal(Buffer.byteLength(JSON.stringify(payload)), 38_000);
  assert.deepEqual(await table.handler(args, context), payload);
  for (const text of ["中".repeat(13_000), '中\n"'.repeat(6000)]) {
    payload = { text };
    assert.ok(JSON.stringify(payload).length < 38_000);
    assert.ok(Buffer.byteLength(JSON.stringify(payload)) > 38_000);
    await assert.rejects(table.handler(args, context), (error: unknown) => {
      const value = error as { status: number; code: string };
      return value.status === 413 && value.code === "payload_too_large";
    });
  }
  const legacy = aiToolRegistry.find(entry => entry.name === "get_business_analysis_table")!;
  assert.deepEqual(await legacy.handler(args, { ...context, surface: "ai_agent" }), payload);
  payload = { text: '中文\n"精确渠道"\\保留原文' };
  assert.deepEqual(await table.handler(args, context), payload);
  assert.equal(calls, 5);
});

test("new tools retain registry audit denial, scope denial, cancellation and audit sequencing", async () => {
  let calls = 0; const audits: string[] = [];
  const entries = [{ ...directory, handler: async () => { calls++; return { items: [] }; } }];
  const options = { entries, audit: async (value: { status: string }) => { audits.push(value.status); } };
  const call = (ctx = context) => executeRegisteredToolCall(directory.name, { runId: "run-1" }, ctx, options);
  assert.equal((await call()).ok, true); assert.deepEqual(audits, ["started", "succeeded"]);
  for (const surface of ["ai_chat", "ai_agent", "business_collection"] as const) assert.equal((await executeRegisteredToolCall(directory.name, { runId: "run-1" }, { ...context, surface }, options)).ok, false);
  assert.equal((await executeRegisteredToolCall(directory.name, { runId: "run-1" }, { ...context, principal: { ...admin, role: "operator" } }, options)).ok, false);
  const ctl = new AbortController(); ctl.abort();
  assert.equal((await executeRegisteredToolCall(directory.name, { runId: "run-1" }, { ...context, signal: ctl.signal }, options)).ok, false);
  assert.equal((await executeRegisteredToolCall(directory.name, { runId: "run-1" }, context, { entries, audit: async () => { throw Error("offline"); } })).ok, false);
  assert.equal(calls, 1);
});

test("signed edge accepts only the new isolated catalog and matching policy digest", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    if (target.pathname === "/api/ai/consumer") { const body = JSON.parse(String(init?.body)); assert.equal(body.operation, "tool-audit"); assert.equal(body.entry.surface, "business_agent_budget_v1"); return response({ ok: true }); }
    assert.equal(target.pathname, "/api/ai/business-evidence/run-1/sources"); return response({ items: [], nextOffset: null });
  };
  async function edge(body: object, actor = admin) {
    const raw = JSON.stringify(body), pathname = "/api/ai/internal/edge";
    const headers = await aiHeaders({ secret: environment.TERUISI_DJANGO_INTERNAL_SECRET, principal: actor, method: "POST", path: pathname, query: "", body: raw, requestId: "fixture-edge" });
    return handleAiEdge(new Request("https://synthetic.invalid"+pathname, { method: "POST", headers, body: raw }));
  }
  const catalogResponse = await edge({ action: "catalog", surface: "business_agent_budget_v1" }); assert.equal(catalogResponse.status, 200);
  const catalog = (await catalogResponse.json() as { entries: AiToolEntry[] }).entries;
  assert.deepEqual(catalog.map(entry => entry.name), names);
  const body = { action: "execute", name: directory.name, arguments: { runId: "run-1" }, surface: "business_agent_budget_v1", requestId: "fixture-v2", policyDigest: sha(canonicalAiEdge(catalog)) };
  assert.equal((await edge({ ...body, policyDigest: "0".repeat(64) })).status, 403); assert.equal(seen.length, 0);
  const success = await edge(body); assert.equal(success.status, 200); assert.equal((await success.json() as { ok: boolean }).ok, true);
  assert.deepEqual(seen, ["/api/ai/consumer", "/api/ai/business-evidence/run-1/sources", "/api/ai/consumer"]);
  assert.equal((await edge({ ...body, surface: "ai_agent" })).status, 403);
  assert.equal((await edge({ action: "catalog", surface: "unknown_v2" })).status, 403);
  const scoped = await edge({ action: "catalog", surface: "business_agent_budget_v1" }, { ...admin, scope: { warehouses: [], channels: [], platforms: [] } });
  assert.deepEqual(await scoped.json(), { entries: [] });
});

const budget = aiToolRegistry.find(entry => entry.name === names[2])!;
test("fixed budget schema only accepts bound IDs and next offset", async () => {
  validateToolArguments({ reportId: "report-1", runId: "run-1" }, budget.inputSchema);
  validateToolArguments({ reportId: "report-1", runId: "run-1", offset: 99 }, budget.inputSchema);
  for (const extra of [{ limit: 20 }, { offset: 100 }, { offset: null }, { offset: true }, { offset: 0.5 }, { budgetRef: {} }, { bindingDigest: "a".repeat(64) }, { plan: {} }]) {
    const args = { reportId: "report-1", runId: "run-1", ...extra };
    assert.throws(() => validateToolArguments(args, budget.inputSchema));
    await assert.rejects(budget.handler(args, context), /参数无效/);
  }
  for (const reportId of ["../private", "", "x".repeat(161)]) assert.throws(() => validateToolArguments({ reportId, runId: "run-1" }, budget.inputSchema));
});

test("fixed budget forwards signed owning-reader query and preserves complete bound page", async t => {
  isolated(t);
  let payload: Record<string, unknown> = { schemaVersion: "business-budget-page-v1", reportId: "report-1", budgetRef: { id: "parameter-1" }, binding: { evidenceRunId: "run-1" }, rows: [], pagination: { nextOffset: null } };
  const calls: URL[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); calls.push(target);
    assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(target.pathname, "/api/ai/reports/report-1/budget-reference");
    assert.equal(target.searchParams.get("limit"), "20");
    assert.equal(target.searchParams.get("runId"), "run-1");
    assert.equal(init?.method, "GET"); assert.ok(new Headers(init?.headers).has("x-teruisi-signature"));
    return response(payload);
  };
  const args = { reportId: "report-1", runId: "run-1" };
  assert.deepEqual(await budget.handler(args, context), payload);
  assert.equal(calls[0].searchParams.get("offset"), "0");
  await budget.handler({ ...args, offset: 20 }, context); assert.equal(calls[1].searchParams.get("offset"), "20");
  payload = { schemaVersion: "business-budget-page-v1", text: "" };
  payload.text = "a".repeat(38_000-Buffer.byteLength(JSON.stringify(payload)));
  assert.equal(Buffer.byteLength(JSON.stringify(payload)), 38_000);
  assert.deepEqual(await budget.handler(args, context), payload);
  for (const text of [String(payload.text)+"a", "中".repeat(13_000), '中\n"'.repeat(6000)]) {
    payload = { schemaVersion: "business-budget-page-v1", text };
    await assert.rejects(budget.handler(args, context), /不得截断/);
  }
  payload = { schemaVersion: "business-budget-v1", rows: [] };
  await assert.rejects(budget.handler(args, context), /协议不匹配/);
});
