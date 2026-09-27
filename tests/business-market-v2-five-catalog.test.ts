import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";

registerHooks({ resolve(specifier, context, nextResolve) {
  return specifier === "cloudflare:workers" ? { url: "data:text/javascript,export const env={};", shortCircuit: true }
    : nextResolve(specifier, context);
} });
const registry = await import("../lib/ai/tool-registry");
const { MARKET_V2_BASE_NAMES, marketV2BaseArguments } = await import("../lib/ai/business-market-v2-base-tool-candidate");
const { MARKET_V2_TOOL, MARKET_V2_SURFACE } = await import("../lib/ai/business-market-v2-tool-candidate");
const { aiHeaders, aiSha256 } = await import("../lib/django/ai-service");
const { handleAiEdge, canonicalAiEdge } = await import("../lib/ai/django-edge");
const admin: AppPrincipal = { email: "market-five@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const secret = "market-five-tool-catalog-test-secret-32-bytes";
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191",
  TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192", TERUISI_DJANGO_INTERNAL_SECRET: secret };
const oldSurface = "business_agent_screening_promotion_v1" as const;
const oldNames = ["get_business_promotion_screening_package_v1", "get_business_promotion_screening_analysis_v1",
  "get_business_promotion_screening_budget_v1", "get_business_promotion_keyword_sku_v1"];
const identity = { reportId: "admitted-1", runId: "run-1", screeningId: "screen-1" };
const requests = [
  { ...identity, role: "market_b2b", offset: 0 },
  { ...identity, role: "commerce", mode: "native", dimension: "keyword", sourceKey: "ads", offset: 0 },
  { ...identity, role: "promotion", offset: 0 },
  { reportId: "admitted-1", role: "promotion", sourceKey: "ads", view: "keyword_sku", offset: 0, limit: 20 },
];
const context = { principal: admin, surface: MARKET_V2_SURFACE, requestId: "market-candidate-1", providerCallId: "call-1" };
const sha = (value: string) => createHash("sha256").update(value).digest("hex");
function isolated(t: TestContext) {
  const previous = Object.fromEntries([...Object.keys(environment), "AI_MARKET_V2_AGENT_RUNTIME_ENABLED"].map(key => [key, process.env[key]]));
  const originalFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = originalFetch; for (const [key, value] of Object.entries(previous)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
}
function response(name: string) {
  const budget = name === MARKET_V2_BASE_NAMES[2];
  return { schemaVersion: "business-market-v2-base-tool-candidate-v1",
    toolName: name, reportId: "admitted-1", sourceReportId: "source-1",
    roleClaim: requests[MARKET_V2_BASE_NAMES.indexOf(name as typeof MARKET_V2_BASE_NAMES[number])].role,
    status: budget ? "unavailable_no_fixed_budget" : "available",
    marketManifestDigest: "a".repeat(64), payload: budget ? null : { rows: [] },
    sourceResultDigest: budget ? null : "b".repeat(64),
    serverSourceVerified: true, sameJobProviderPersisted: false,
    persistedRead: false, registeredAgentTool: false, authorityVerified: false,
    resultDigest: "c".repeat(64) };
}

test("five callable market entries are isolated and v1 catalog bytes unchanged", () => {
  const previous = canonicalAiEdge(registry.getVisibleToolCatalog(admin, oldSurface));
  const enabled = registry.getMarketV2EnabledRegistry(true);
  const names = registry.getToolsForPrincipal(admin, MARKET_V2_SURFACE, enabled).map(entry => entry.name);
  assert.deepEqual(names, [...MARKET_V2_BASE_NAMES, MARKET_V2_TOOL]);
  assert.deepEqual(registry.getToolsForPrincipal(admin, MARKET_V2_SURFACE), []);
  assert.deepEqual(registry.getToolsForPrincipal(admin, oldSurface, enabled).map(entry => entry.name), oldNames);
  assert.equal(canonicalAiEdge(registry.getVisibleToolCatalog(admin, oldSurface, enabled)), previous);
  for (const entry of registry.getToolsForPrincipal(admin, MARKET_V2_SURFACE, enabled)) {
    assert.equal(entry.risk, "read_only"); assert.deepEqual(entry.execution.allowedSurfaces, [MARKET_V2_SURFACE]);
    assert.equal(entry.execution.timeoutMs, 12000); assert.ok(entry.execution.maxResultCharacters <= 40000);
  }
});

test("four aliases enforce exclusive modes, roles and disabled flag before transport", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; throw Error("unexpected fetch"); };
  delete process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED;
  for (const [index] of MARKET_V2_BASE_NAMES.entries())
    await assert.rejects(registry.marketV2BaseTools[index].handler(requests[index], context));
  process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED = "true";
  const negatives = [
    { name: MARKET_V2_BASE_NAMES[0], args: { ...requests[0], pairKey: "d".repeat(64) } },
    { name: MARKET_V2_BASE_NAMES[1], args: { ...requests[1], pairKey: "d".repeat(64) } },
    { name: MARKET_V2_BASE_NAMES[2], args: { ...requests[2], role: "market_b2b" } },
    { name: MARKET_V2_BASE_NAMES[3], args: { ...requests[3], role: "commerce" } },
    { name: MARKET_V2_BASE_NAMES[3], args: { ...requests[3], rowIndex: 0, rowId: "e".repeat(64) } },
  ];
  for (const { name, args } of negatives) {
    assert.throws(() => marketV2BaseArguments(name, args));
    await assert.rejects(registry.marketV2BaseTools[MARKET_V2_BASE_NAMES.indexOf(name)].handler(args, context));
  }
  assert.equal(calls, 0);
});

test("all four handlers sign exact preview-only reader requests and preserve unavailable budget", async t => {
  isolated(t); process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED = "true";
  const seen: string[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(target.pathname, "/api/ai/market-v2-base-tool-candidate/market-candidate-1");
    assert.equal(init?.method, "POST"); assert.equal(target.search, "");
    const sent = JSON.parse(String(init?.body)) as { name: string; arguments: Record<string, unknown>; providerCallId: string };
    assert.equal(sent.providerCallId, "call-1");
    assert.deepEqual(sent.arguments, requests[MARKET_V2_BASE_NAMES.indexOf(sent.name as typeof MARKET_V2_BASE_NAMES[number])]);
    const headers = new Headers(init?.headers);
    const signed = ["v1", headers.get("x-teruisi-timestamp"), "market-candidate-1", "POST", target.pathname,
      "", sha(String(init?.body)), headers.get("x-teruisi-principal")].join("\n");
    assert.equal(headers.get("x-teruisi-signature"), "v1=" + createHmac("sha256", secret).update(signed).digest("hex"));
    return Response.json(response(sent.name), { headers: { "x-ai-revision": "1" } });
  };
  for (const [index, name] of MARKET_V2_BASE_NAMES.entries()) {
    const value = await registry.marketV2BaseTools[index].handler(requests[index], context);
    assert.equal(value.persistedRead, false);
    if (name === MARKET_V2_BASE_NAMES[2]) {
      assert.equal(value.status, "unavailable_no_fixed_budget"); assert.equal(value.payload, null);
    }
  }
  assert.deepEqual(seen, Array(4).fill("/api/ai/market-v2-base-tool-candidate/market-candidate-1"));
});

test("signed central edge dispatch resolves alias only under explicit flag, without model job", async t => {
  isolated(t);
  const bodyFor = async () => {
    const visible = registry.getToolsForPrincipal(admin, MARKET_V2_SURFACE, registry.getMarketV2EnabledRegistry(true))
      .map(({ handler: _handler, ...entry }) => { void _handler; return entry; });
    return { action: "execute", name: MARKET_V2_BASE_NAMES[0], arguments: requests[0],
      surface: MARKET_V2_SURFACE, requestId: "market-candidate-1", providerCallId: "call-1",
      policyDigest: await aiSha256(canonicalAiEdge(visible)) };
  };
  const invoke = async (body: object) => {
    const raw = JSON.stringify(body), path = "/api/ai/internal/edge";
    const headers = await aiHeaders({ secret, principal: admin, method: "POST", path, query: "", body: raw, requestId: "edge-call-1" });
    return handleAiEdge(new Request("https://fixture.invalid" + path, { method: "POST", headers, body: raw }));
  };
  let fetches = 0;
  globalThis.fetch = async (url) => { fetches++; const path = new URL(String(url)).pathname;
    return Response.json(path === "/api/ai/consumer" ? {} : response(MARKET_V2_BASE_NAMES[0]),
      { headers: { "x-ai-revision": "1" } }); };
  const request = await bodyFor();
  assert.equal((await invoke(request)).status, 403); assert.equal(fetches, 0);
  process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED = "true";
  const result = await invoke(request);
  assert.equal(result.status, 200);
  const value = await result.json() as { ok: boolean; data: { persistedRead: boolean } };
  assert.equal(value.ok, true); assert.equal(value.data.persistedRead, false);
  assert.ok(fetches >= 3); // started audit, owning preview, succeeded audit.
});
