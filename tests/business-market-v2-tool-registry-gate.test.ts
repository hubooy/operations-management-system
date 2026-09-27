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
const { marketV2ToolArguments, MARKET_V2_SURFACE, MARKET_V2_TOOL } = await import("../lib/ai/business-market-v2-tool-candidate");
const { MARKET_V2_BASE_NAMES } = await import("../lib/ai/business-market-v2-base-tool-candidate");
const { validateToolRegistry } = await import("../lib/ai/tool-registry-contract");
const { aiHeaders } = await import("../lib/django/ai-service");
const { handleAiEdge, canonicalAiEdge } = await import("../lib/ai/django-edge");

const admin: AppPrincipal = { email: "market-registry@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const oldSurface = "business_agent_screening_promotion_v1" as const;
const oldNames = ["get_business_promotion_screening_package_v1", "get_business_promotion_screening_analysis_v1",
  "get_business_promotion_screening_budget_v1", "get_business_promotion_keyword_sku_v1"];
const secret = "market-v2-candidate-isolated-secret-32-bytes";
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191",
  TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192", TERUISI_DJANGO_INTERNAL_SECRET: secret };
const args = { reportId: "admitted-1", marketContextDigest: "a".repeat(64), marketManifestDigest: "b".repeat(64),
  role: "market_b2b", mode: "summary" };
const context = { principal: admin, surface: MARKET_V2_SURFACE, requestId: "preview-123", providerCallId: "model-call-1" };
const sha = (value: string) => createHash("sha256").update(value).digest("hex");

function isolated(t: TestContext) {
  const previous = Object.fromEntries([...Object.keys(environment), "AI_MARKET_V2_AGENT_RUNTIME_ENABLED"].map(key => [key, process.env[key]]));
  const originalFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = originalFetch; for (const [key, value] of Object.entries(previous)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
}
function payload(extra = {}) {
  return { schemaVersion: "business-market-v2-tool-result-candidate-v1", surface: MARKET_V2_SURFACE,
    profile: "business-agent-screening-promotion-market-admitted-v2", toolName: MARKET_V2_TOOL,
    reportId: args.reportId, sourceReportId: "source-1", role: args.role, mode: args.mode,
    marketManifestDigest: args.marketManifestDigest,
    jobIdClaim: "market-preview-job-preview-123", providerDispatchIdClaim: "market-preview-provider-preview-123",
    providerCallIdClaim: "model-call-1", serverFullMarketMaterialVerified: true,
    sameJobProviderPersisted: false, persistedRead: false, registeredTool: false,
    authorityVerified: false, resultDigest: "c".repeat(64), payload: { tables: [], ...extra }, citationBases: [] };
}

test("market entry is opt-in and v1 four-tool catalog stays byte-stable", () => {
  const old = registry.getToolsForPrincipal(admin, oldSurface).map(entry => entry.name);
  assert.deepEqual(old, oldNames);
  assert.strictEqual(registry.getMarketV2EnabledRegistry(), registry.aiToolRegistry);
  assert.deepEqual(registry.getToolsForPrincipal(admin, MARKET_V2_SURFACE), []);
  const enabled = registry.getMarketV2EnabledRegistry(true);
  validateToolRegistry(enabled);
  assert.deepEqual(registry.getToolsForPrincipal(admin, oldSurface, enabled).map(entry => entry.name), oldNames);
  assert.deepEqual(registry.getToolsForPrincipal(admin, MARKET_V2_SURFACE, enabled).map(entry => entry.name),
    [...MARKET_V2_BASE_NAMES, MARKET_V2_TOOL]);
  assert.deepEqual(registry.getToolsForPrincipal({ ...admin, role: "viewer" }, MARKET_V2_SURFACE, enabled), []);
  assert.deepEqual(registry.getToolsForPrincipal({ ...admin, scope: { warehouses: [], channels: [], platforms: [] } }, MARKET_V2_SURFACE, enabled), []);
  const entry = enabled.find(item => item.name === MARKET_V2_TOOL)!;
  assert.equal(entry.risk, "read_only"); assert.equal(entry.execution.timeoutMs, 12000);
  assert.equal(entry.execution.maxResultCharacters, 38000);
  assert.deepEqual(entry.execution.allowedSurfaces, [MARKET_V2_SURFACE]);
});

test("flag off and exclusive mode/role checks reject before any transport", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; throw Error("unexpected transport"); };
  delete process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED;
  await assert.rejects(registry.marketV2CandidateTool.handler(args, context));
  process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED = "true";
  for (const bad of [{ ...args, offset: 0 }, { ...args, role: "commerce" },
    { ...args, mode: "page", view: "price_band", offset: 0, limit: 10 },
    { ...args, mode: "row", view: "rank_entry_exit", rowIndex: 0, rowId: "d".repeat(64), offset: 0 }]) {
    assert.throws(() => marketV2ToolArguments(bad));
    await assert.rejects(registry.marketV2CandidateTool.handler(bad, context));
  }
  await assert.rejects(registry.marketV2CandidateTool.handler(args, { ...context, surface: oldSurface }));
  assert.equal(calls, 0);
});

test("enabled handler signs exact preview-only reader POST and rejects wide result", async t => {
  isolated(t); process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED = "true";
  const seen: string[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(target.pathname, "/api/ai/market-v2-tool-candidate/preview-123");
    assert.equal(init?.method, "POST"); assert.equal(target.search, "");
    const sent = JSON.parse(String(init?.body)) as { arguments: Record<string, unknown>; providerCallId: string };
    assert.deepEqual(sent.arguments, args); assert.equal(sent.providerCallId, "model-call-1");
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("x-teruisi-request-id"), "preview-123");
    const signed = ["v1", headers.get("x-teruisi-timestamp"), "preview-123", "POST", target.pathname,
      "", sha(String(init?.body)), headers.get("x-teruisi-principal")].join("\n");
    assert.equal(headers.get("x-teruisi-signature"), "v1=" +
      createHmac("sha256", secret).update(signed).digest("hex"));
    return Response.json(payload(), { headers: { "x-ai-revision": "1" } });
  };
  const value = await registry.marketV2CandidateTool.handler(args, context);
  assert.equal(value.persistedRead, false); assert.equal(value.registeredTool, false);
  assert.deepEqual(seen, ["/api/ai/market-v2-tool-candidate/preview-123"]);
  globalThis.fetch = async () => Response.json(payload({ text: "中".repeat(13000) }), { headers: { "x-ai-revision": "1" } });
  await assert.rejects(registry.marketV2CandidateTool.handler(args, context));
});

test("edge catalog new surface is denied by default and v1 catalog is unchanged", async t => {
  isolated(t);
  const make = async (surface: string) => {
    const body = JSON.stringify({ action: "catalog", surface });
    const path = "/api/ai/internal/edge", requestId = "market-registry-catalog";
    const headers = await aiHeaders({ secret, principal: admin, method: "POST", path, query: "", body, requestId });
    return handleAiEdge(new Request("https://fixture.invalid" + path, { method: "POST", headers, body }));
  };
  const old = await make(oldSurface);
  assert.equal(old.status, 200);
  const oldCatalog = canonicalAiEdge(await old.json());
  const hidden = await make(MARKET_V2_SURFACE);
  assert.equal(hidden.status, 403);
  process.env.AI_MARKET_V2_AGENT_RUNTIME_ENABLED = "true";
  const shown = await make(MARKET_V2_SURFACE);
  assert.equal(shown.status, 200);
  assert.deepEqual((await shown.json() as { entries: { name: string }[] }).entries.map(item => item.name),
    [...MARKET_V2_BASE_NAMES, MARKET_V2_TOOL]);
  const oldAgain = await make(oldSurface);
  assert.equal(canonicalAiEdge(await oldAgain.json()), oldCatalog);
});
