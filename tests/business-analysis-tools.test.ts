import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import { validateToolArguments, validateToolRegistry } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") return { url: "data:text/javascript,export const env={};", shortCircuit: true };
  return nextResolve(specifier, context);
} });
const { aiToolRegistry, getOpenAiTools, getAnthropicTools, getToolsForPrincipal, executeRegisteredToolCall } = await import("../lib/ai/tool-registry");
const { requireAnalysisPrincipal } = await import("../lib/netshop/analysis-tool");
const admin = { email: "analysis@example.test", displayName: "Fixture", role: "admin" as const, scope: null };
const entry = aiToolRegistry.find(e => e.name === "get_netshop_analysis_records")!;
const args = { platform: "京东", shop: "样例店A", dataset: "promotion", startDate: "2026-09-01", endDate: "2026-09-03" };

test("sales and market continuations preserve exact schema and collector-only permissions", () => {
  const checkpoint = { startDate: args.startDate, endDate: args.endDate, window: "current", limit: 100,
    cursor: "original-signed-cursor", expectedSourceRef: "a".repeat(64), expectedLastId: 101 };
  for (const [name, query] of [
    ["get_business_sales_continuation_page", { ...checkpoint, platform: "京东", shop: "样例店A", channel: "批发", expectedRevision: "7:9" }],
    ["get_business_market_continuation_page", { ...checkpoint, platform: "京东", category: "饮水机", scope: "POP", rankingDimension: "SKU", priceBandFilter: "全部", expectedRevision: "7:abcdefabcdef" }],
  ] as const) {
    const tool = aiToolRegistry.find(item => item.name === name)!;
    validateToolArguments(query, tool.inputSchema);
    for (const bad of [{ ...query, limit: 99 }, { ...query, domain: "netshop" }, { ...query, expectedLastId: Number.MAX_SAFE_INTEGER + 1 }])
      assert.throws(() => validateToolArguments(bad, tool.inputSchema));
    const { expectedSourceRef: omitted, ...withoutBinding } = query;
    assert.ok(omitted); assert.throws(() => validateToolArguments(withoutBinding, tool.inputSchema));
    assert.deepEqual(tool.execution.allowedSurfaces, ["business_collection"]);
    for (const surface of ["ai_chat", "ai_agent", "dingtalk_chat", "codex_mcp", "test"] as const)
      assert.ok(!getToolsForPrincipal(admin, surface).some(item => item.name === name));
    assert.ok(!getToolsForPrincipal({ ...admin, role: "operator" }, "business_collection").some(item => item.name === name));
    assert.ok(!getToolsForPrincipal({ ...admin, scope: { platforms: ["京东"], channels: [], warehouses: [] } }, "business_collection").some(item => item.name === name));
  }
  assert.deepEqual(getOpenAiTools(admin, "business_collection"), []);
  assert.deepEqual(getAnthropicTools(admin, "business_collection"), []);
});

test("budget tool binds report and evidence on the reader without accepting model assumptions", async t => {
  const budget = aiToolRegistry.find(e => e.name === "get_business_budget_scenarios")!;
  const query = { runId: "sealed", reportId: "report", offset: 1, limit: 1 };
  validateToolArguments(query, budget.inputSchema);
  for (const bad of [{ ...query, budgetPlan: {} }, { ...query, limit: 21 }, { ...query, reportId: "../secret" }]) assert.throws(() => validateToolArguments(bad, budget.inputSchema));
  for (const surface of ["dingtalk_chat", "business_collection"] as const) assert.ok(!getToolsForPrincipal(admin, surface).some(e => e.name === budget.name));
  for (const role of ["viewer", "analyst", "operator"] as const) assert.ok(!getToolsForPrincipal({ ...admin, role }, "ai_agent").some(e => e.name === budget.name));
  assert.ok(getOpenAiTools(admin, "ai_agent").some(e => e.function.name === budget.name));
  assert.ok(getAnthropicTools(admin, "ai_agent").some(e => e.name === budget.name));
  const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18111", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18112", TERUISI_DJANGO_INTERNAL_SECRET: "budget-fixture-internal-secret-at-least-32-bytes" };
  const saved = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]])), oldFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  globalThis.fetch = async (url, init) => {
    const u = new URL(String(url));
    assert.equal(u.origin, "http://127.0.0.1:18111");
    assert.equal(u.pathname, "/api/ai/reports/report/budget");
    assert.equal(u.searchParams.get("runId"), "sealed");
    assert.equal(u.searchParams.get("offset"), "1");
    assert.equal(init?.method, "GET");
    return Response.json({ schemaVersion: "business-budget-v1" }, { headers: { "x-ai-revision": "1" } });
  };
  await budget.handler(query, { principal: admin, surface: "ai_agent", requestId: "budget-fixture" });
  globalThis.fetch = async () => Response.json({ text: "x".repeat(38001) }, { headers: { "x-ai-revision": "1" } });
  await assert.rejects(budget.handler(query, { principal: admin, surface: "ai_agent", requestId: "budget-large" }), /不得截断/);
});

test("market analysis preserves the exact sample identity on its owning reader", async t => {
  const entry = aiToolRegistry.find(e => e.name === "get_market_analysis_records")!;
  const query = { platform: "京东", category: "饮水机", scope: "POP", rankingDimension: "SKU", priceBandFilter: "全部", startDate: args.startDate, endDate: args.endDate };
  validateToolArguments(query, entry.inputSchema);
  assert.throws(() => validateToolArguments({ ...query, shop: "样例店A" }, entry.inputSchema));
  assert.ok(!getToolsForPrincipal(admin, "dingtalk_chat").some(e => e.name === entry.name));
  const oldFetch = globalThis.fetch;
  const environment = { TERUISI_DJANGO_MARKET_READER_BASE_URL: "http://127.0.0.1:18031", TERUISI_DJANGO_MARKET_WRITER_BASE_URL: "http://127.0.0.1:18032", TERUISI_DJANGO_INTERNAL_SECRET: "analysis-fixture-internal-secret-at-least-32-bytes" };
  const saved = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "http://127.0.0.1:18031/api/market/consumers/query");
    const body = JSON.parse(new TextDecoder().decode(init?.body as Uint8Array));
    assert.deepEqual(body, { ...query, limit: 10, operation: "analysis_records" });
    assert.ok(new Headers(init?.headers).get("X-Teruisi-Signature"));
    return Response.json({ schemaVersion: "business-analysis-v1" }, { headers: { "x-market-data-revision": "7:aaaaaaaaaaaa" } });
  };
  const result = await entry.handler(query, { principal: admin, surface: "ai_agent", requestId: "market-test" });
  assert.equal((result as Record<string, unknown>).schemaVersion, "business-analysis-v1");
});

test("ERP analysis tool uses the owning signed reader and retains exact three-field identity", async t => {
  const sales = aiToolRegistry.find(e => e.name === "get_sales_analysis_records")!;
  const oldFetch = globalThis.fetch;
  const priorUrl = process.env.TERUISI_DJANGO_SALES_READER_BASE_URL;
  const priorSecret = process.env.TERUISI_DJANGO_INTERNAL_SECRET;
  process.env.TERUISI_DJANGO_SALES_READER_BASE_URL = "http://127.0.0.1:18011";
  process.env.TERUISI_DJANGO_INTERNAL_SECRET = "analysis-fixture-internal-secret-at-least-32-bytes";
  t.after(() => { globalThis.fetch = oldFetch;
    if (priorUrl === undefined) delete process.env.TERUISI_DJANGO_SALES_READER_BASE_URL; else process.env.TERUISI_DJANGO_SALES_READER_BASE_URL = priorUrl;
    if (priorSecret === undefined) delete process.env.TERUISI_DJANGO_INTERNAL_SECRET; else process.env.TERUISI_DJANGO_INTERNAL_SECRET = priorSecret;
  });
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "http://127.0.0.1:18011/api/sales/consumers/query");
    const body = JSON.parse(new TextDecoder().decode(init?.body as Uint8Array));
    assert.equal(body.operation, "analysis_records");
    assert.equal(body.channel, "京东-样例店A");
    assert.equal(body.shop, "样例店A");
    assert.equal(body.platform, "京东");
    assert.equal(body.limit, 10);
    assert.ok(new Headers(init?.headers).get("X-Teruisi-Signature"));
    return Response.json({ operation: "analysis_records", data: { schemaVersion: "business-analysis-v1" } },
      { headers: { "x-sales-data-revision": "7:3", "x-sales-source-revision": "7:3" } });
  };
  const query = { platform: args.platform, shop: args.shop, channel: "京东-样例店A", startDate: args.startDate, endDate: args.endDate };
  validateToolArguments(query, sales.inputSchema);
  await sales.handler(query, { principal: admin, surface: "ai_agent", requestId: "erp-test" });
  assert.ok(!getToolsForPrincipal(admin, "dingtalk_chat").some(e => e.name === sales.name));
});

test("evidence paths remain finite and use separate reader/writer routes", async () => {
  const { isPublicAiPath, requestDjangoAi } = await import("../lib/django/ai-service");
  for (const suffix of ["", "/run", "/run/collect", "/run/finish", "/run/control", "/run/mapping", "/run/analysis", "/run/chunks/sales"]) assert.ok(isPublicAiPath("/api/ai/business-evidence" + suffix));
  assert.equal(isPublicAiPath("/api/ai/business-evidence/run/exec"), false);
  for (const [method, suffix, port] of [["GET", "/run", "18001"], ["POST", "/run/collect", "18002"]] as const) {
    await requestDjangoAi(admin, { path: "/api/ai/business-evidence" + suffix, method }, {
      environment: { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18001", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18002", TERUISI_DJANGO_INTERNAL_SECRET: "analysis-fixture-internal-secret-at-least-32-bytes" },
      fetchImpl: async url => { assert.equal(new URL(String(url)).port, port); return Response.json({}, { headers: { "x-ai-revision": "1" } }); },
    });
  }
});

test("shared evidence tool is read-only, bounded and cannot accept a partial chunk locator", async () => {
  const shared = aiToolRegistry.find(e => e.name === "get_business_analysis_evidence")!;
  assert.equal(shared.risk, "read_only");
  validateToolArguments({ runId: "evidence-example", sourceKey: "sales", sequence: 1 }, shared.inputSchema);
  assert.throws(() => validateToolArguments({ runId: "../secret" }, shared.inputSchema));
  await assert.rejects(() => shared.handler({ runId: "example", sourceKey: "sales" }, { principal: admin, surface: "ai_agent", requestId: "partial" }), /同时指定/);
  assert.ok(!getToolsForPrincipal({ ...admin, role: "viewer" }, "ai_chat").some(e => e.name === shared.name));
});

test("analysis tool has a single schema, two provider projections and bounded surfaces", () => {
  validateToolRegistry(aiToolRegistry);
  assert.equal(aiToolRegistry.filter(e => e.name === entry.name).length, 1);
  assert.ok(getOpenAiTools(admin, "ai_agent").some(e => e.function.name === entry.name));
  assert.ok(getAnthropicTools(admin, "ai_chat").some(e => e.name === entry.name));
  assert.ok(!getToolsForPrincipal(admin, "dingtalk_chat").some(e => e.name === entry.name));
  for (const role of ["viewer", "analyst", "operator"] as const) {
    assert.ok(!getToolsForPrincipal({ ...admin, role }, "ai_chat").some(e => e.name === entry.name));
    assert.throws(() => requireAnalysisPrincipal({ ...admin, role }));
  }
  assert.throws(() => requireAnalysisPrincipal({ ...admin, scope: { platforms: ["京东"], warehouses: [], channels: [] } }));
  assert.doesNotThrow(() => validateToolArguments(args, entry.inputSchema));
  for (const bad of [{ ...args, sql: "select *" }, { ...args, limit: 21 }, { ...args, dataset: "private" }, { ...args, window: "latest" }]) {
    assert.throws(() => validateToolArguments(bad, entry.inputSchema));
  }
});

test("analysis handler preserves exact identity, date and cursor on the signed reader", async t => {
  const oldFetch = globalThis.fetch;
  const environment = { TERUISI_DJANGO_NETSHOP_READER_BASE_URL: "http://127.0.0.1:18021",
    TERUISI_DJANGO_NETSHOP_WRITER_BASE_URL: "http://127.0.0.1:18022",
    TERUISI_DJANGO_INTERNAL_SECRET: "analysis-fixture-internal-secret-at-least-32-bytes" };
  const saved = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
  globalThis.fetch = async (url, init) => {
    const u = new URL(String(url));
    assert.equal(u.origin, "http://127.0.0.1:18021");
    assert.equal(u.pathname, "/api/netshop/analysis-records");
    assert.equal(u.searchParams.get("shop"), args.shop);
    assert.equal(u.searchParams.get("window"), "yearAgo");
    assert.equal(u.searchParams.get("cursor"), "bound-cursor");
    assert.equal(u.searchParams.get("limit"), "10");
    assert.equal(init?.method, "GET");
    assert.ok(new Headers(init?.headers).has("X-Teruisi-Signature"));
    return Response.json({ schemaVersion: "business-analysis-v1", items: [], pagination: { hasMore: false, nextCursor: null } },
      { headers: { "X-Netshop-Data-Revision": "7:aaaaaaaaaaaa" } });
  };
  const result = await entry.handler({ ...args, window: "yearAgo", cursor: "bound-cursor" },
    { principal: admin, surface: "ai_agent", requestId: "analysis-fixture" });
  assert.equal((result as Record<string, unknown>).schemaVersion, "business-analysis-v1");
  globalThis.fetch = async () => Response.json({ items: [{ productName: "长".repeat(38_001) }] },
    { headers: { "X-Netshop-Data-Revision": "7:aaaaaaaaaaaa" } });
  await assert.rejects(() => entry.handler(args,
    { principal: admin, surface: "ai_agent", requestId: "analysis-overflow" }), /不得截断/);
});

test("derived table tools have finite dimensions and cannot ask for arbitrary computations", () => {
  const table = aiToolRegistry.find(e => e.name === "get_business_analysis_table")!;
  const query = { runId: "sealed-run", sourceKey: "promotion", dimension: "keyword", baselineKey: "previous" };
  validateToolArguments(query, table.inputSchema);
  for (const bad of [{ ...query, dimension: "customer" }, { ...query, sql: "SELECT" }, { ...query, limit: 21 }]) {
    assert.throws(() => validateToolArguments(bad, table.inputSchema));
  }
  assert.equal(table.risk, "read_only");
  assert.ok(!getToolsForPrincipal(admin, "dingtalk_chat").some(e => e.name === table.name));
  assert.ok(!getToolsForPrincipal({ ...admin, role: "viewer" }, "ai_chat").some(e => e.name === table.name));
});

test("audit failure prevents analysis data access", async () => {
  let calls = 0;
  const result = await executeRegisteredToolCall(entry.name, args,
    { principal: admin, surface: "ai_agent", requestId: "analysis-fixture" },
    { entries: [{ ...entry, handler: async () => { calls++; return {}; } }],
      audit: async () => { throw new Error("audit unavailable"); } });
  assert.equal(result.ok, false);
  assert.equal(calls, 0);
});

test("bulk collector is absent from providers and cannot widen a chat tool budget", async () => {
  const bulk = aiToolRegistry.find(e => e.name === "get_business_source_page")!;
  assert.deepEqual(getToolsForPrincipal(admin, "business_collection").map(e => e.name).sort(), ["get_business_finance_source_page", "get_business_market_continuation_page", "get_business_netshop_continuation_page", "get_business_sales_continuation_page", "get_business_source_page", "get_data_freshness"]);
  assert.deepEqual(getOpenAiTools(admin, "business_collection"), []);
  assert.deepEqual(getAnthropicTools(admin, "business_collection"), []);
  for (const surface of ["ai_chat", "ai_agent", "ai_sandbox", "dingtalk_chat", "codex_mcp", "test"] as const) {
    assert.ok(!getToolsForPrincipal(admin, surface).some(e => e.name === bulk.name));
  }
  assert.throws(() => validateToolRegistry([{ ...bulk, execution: { ...bulk.execution, allowedSurfaces: ["ai_chat"] } }]));
  assert.throws(() => validateToolRegistry([{ ...entry, execution: { ...entry.execution, maxResultCharacters: 131072 } }]));
  const query = { ...args, domain: "netshop", limit: 100 };
  validateToolArguments(query, bulk.inputSchema);
  assert.throws(() => validateToolArguments({ ...query, limit: 101 }, bulk.inputSchema));
  let calls = 0;
  const entries = [{ ...bulk, handler: async () => { calls++; return { text: "a".repeat(45000) }; } }];
  const audits: string[] = [];
  const options = { entries, audit: async (value: { status: string }) => { audits.push(value.status); } };
  const result = await executeRegisteredToolCall(bulk.name, query, { principal: admin, surface: "business_collection", requestId: "bulk" }, options);
  assert.equal(result.ok, true);
  assert.equal(calls, 1);
  assert.deepEqual(audits, ["started", "succeeded"]);
  const rejected = await executeRegisteredToolCall(bulk.name, query, { principal: admin, surface: "ai_chat", requestId: "wrong-surface" }, options);
  assert.equal(rejected.ok, false);
  assert.equal(calls, 1);
});

test("bulk handler signs the owning request and enforces UTF8 byte capacity", async t => {
  const bulk = aiToolRegistry.find(e => e.name === "get_business_source_page")!;
  const environment = { TERUISI_DJANGO_NETSHOP_READER_BASE_URL: "http://127.0.0.1:18021",
    TERUISI_DJANGO_NETSHOP_WRITER_BASE_URL: "http://127.0.0.1:18022",
    TERUISI_DJANGO_INTERNAL_SECRET: "analysis-fixture-internal-secret-at-least-32-bytes" };
  const saved = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  const oldFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
  const payload = { items: [{ text: "a".repeat(45000) }] };
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url));
    assert.equal(target.pathname, "/api/netshop/analysis-records");
    assert.equal(target.searchParams.get("limit"), "100");
    assert.equal(target.searchParams.get("shop"), args.shop);
    assert.equal(target.searchParams.has("domain"), false);
    assert.ok(new Headers(init?.headers).has("x-teruisi-signature"));
    return Response.json(payload, { headers: { "X-Netshop-Data-Revision": "7:aaaaaaaaaaaa" } });
  };
  const context = { principal: admin, surface: "business_collection" as const, requestId: "bulk-reader" };
  assert.deepEqual(await bulk.handler({ ...args, domain: "netshop" }, context), payload);
  payload.items[0].text = "汉".repeat(45000);
  await assert.rejects(() => bulk.handler({ ...args, domain: "netshop" }, context), /不得截断/);
  await assert.rejects(() => bulk.handler({ ...args, domain: "netshop" }, { ...context, surface: "ai_agent" }), /仅限/);
});
