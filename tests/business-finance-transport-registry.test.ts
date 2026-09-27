import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";

registerHooks({ resolve(specifier, context, nextResolve) { return specifier === "cloudflare:workers"
  ? { url: "data:text/javascript,export const env={};", shortCircuit: true } : nextResolve(specifier, context); } });
const { aiToolRegistry, getToolsForPrincipal, getOpenAiTools, getAnthropicTools, executeRegisteredToolCall } =
  await import("../lib/ai/tool-registry");
const { validateToolArguments, validateToolRegistry } = await import("../lib/ai/tool-registry-contract");

const admin: AppPrincipal = { email: "finance-admin@example.test", displayName: "Synthetic", role: "admin", scope: null };
const tool = aiToolRegistry.find((entry) => entry.name === "get_business_finance_source_page")!;
const query = { months: ["2026-08"], scope: { scope_key: "business", scope_type: "business",
  scope_name: "志高事业部", group_name: "" }, analysisPeriod: { startDate: "2026-08-01", endDate: "2026-08-31" } };
const args = { query, offset: 0, afterId: 0 };
const secret = "finance-v3-collector-tool-secret-at-least-32-bytes";

test("finance page tool is exclusive to admin unscoped internal collection and absent from models", () => {
  validateToolRegistry(aiToolRegistry);
  assert.equal(tool.risk, "read_only");
  assert.deepEqual(tool.allowedRoles, ["admin"]);
  assert.equal(tool.scopePolicy, "unscoped_only");
  assert.deepEqual(tool.execution.allowedSurfaces, ["business_collection"]);
  assert.equal(tool.execution.mode, "direct");
  assert.equal(tool.execution.maxResultCharacters, 40_000);
  assert.ok(getToolsForPrincipal(admin, "business_collection").some((item) => item.name === tool.name));
  for (const principal of [{ ...admin, role: "viewer" } as AppPrincipal,
                           { ...admin, scope: { warehouses: [], channels: [], platforms: [] } } as AppPrincipal]) {
    assert.ok(!getToolsForPrincipal(principal, "business_collection").some((item) => item.name === tool.name));
  }
  for (const surface of ["ai_chat", "ai_agent", "business_agent_screening_v1", "business_agent_screening_promotion_v1"] as const) {
    assert.ok(!getToolsForPrincipal(admin, surface).some((item) => item.name === tool.name));
  }
  assert.deepEqual(getOpenAiTools(admin, "business_collection"), []);
  assert.deepEqual(getAnthropicTools(admin, "business_collection"), []);
});

test("caller-supplied page fails schema before any fetch", async (t) => {
  validateToolArguments(args, tool.inputSchema);
  for (const bad of [{ ...args, page: { rows: [] } }, { ...args, offset: 1, afterId: 1 },
                     { ...args, expectedSourceRef: "wrong" },
                     { ...args, query: { ...query, scope: { ...query.scope, scope_key: "" } } }]) {
    if ("page" in bad || "expectedSourceRef" in bad || bad.query.scope.scope_key === "") {
      assert.throws(() => validateToolArguments(bad, tool.inputSchema));
    }
  }
  let calls = 0;
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => assert.fail("forged page must not reach finance reader");
  t.after(() => { globalThis.fetch = oldFetch; });
  const context = { principal: admin, surface: "business_collection" as const, requestId: "finance-v3-collector" };
  const result = await executeRegisteredToolCall(tool.name, { ...args, page: {} }, context,
    { entries: [tool], audit: async () => { calls++; } });
  assert.equal(result.ok, false);
  assert.equal(calls, 1); // The denial is audited without invoking the reader.
});

test("registered handler uses signed finance reader and audited internal execution", async (t) => {
  const keys = ["TERUISI_DJANGO_FINANCE_READER_BASE_URL", "TERUISI_DJANGO_FINANCE_WRITER_BASE_URL", "TERUISI_DJANGO_INTERNAL_SECRET"] as const;
  const prior = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  const priorFetch = globalThis.fetch;
  process.env.TERUISI_DJANGO_FINANCE_READER_BASE_URL = "http://127.0.0.1:8011";
  process.env.TERUISI_DJANGO_FINANCE_WRITER_BASE_URL = "http://127.0.0.1:8012";
  process.env.TERUISI_DJANGO_INTERNAL_SECRET = secret;
  t.after(() => { globalThis.fetch = priorFetch; for (const key of keys) {
    const value = prior[key]; if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
  const full = `2:${"b".repeat(64)}`;
  const page = { schemaVersion: "business-finance-owned-page-v1", sourceRef: "a".repeat(64), sourceRevision: full,
    query, rows: [], pageEvidence: { rowCount: 0, sha256: "c".repeat(64) },
    pagination: { offset: 0, returned: 0, total: 0, nextOffset: null, nextLastId: null },
    sourceAuthorityVerified: false, persistentEvidenceVerified: false, pageDigest: "d".repeat(64) };
  let observed: Request | undefined;
  globalThis.fetch = async (input, init) => {
    observed = new Request(input, init);
    return Response.json(page, { headers: { "x-finance-data-revision": `2:${"b".repeat(12)}` } });
  };
  const audits: string[] = [];
  const result = await executeRegisteredToolCall(tool.name, args,
    { principal: admin, surface: "business_collection", requestId: "finance-v3-collector" },
    { entries: [tool], audit: async (entry) => { audits.push(entry.status); } });
  assert.equal(result.ok, true);
  assert.deepEqual(audits, ["started", "succeeded"]);
  assert.ok(observed);
  assert.equal(new URL(observed.url).origin, "http://127.0.0.1:8011");
  assert.equal(new URL(observed.url).pathname, "/api/finance/business-evidence/page");
  assert.equal(observed.method, "POST");
  assert.ok(observed.headers.has("x-teruisi-signature"));
  assert.deepEqual(await observed.json(), args);
});
