import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import { validateToolArguments, validateToolRegistry } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") return { url: "data:text/javascript,export const env={};", shortCircuit: true };
  return nextResolve(specifier, context);
} });
const { aiToolRegistry, getOpenAiTools, getAnthropicTools, getToolsForPrincipal } = await import("../lib/ai/tool-registry");
const entry = aiToolRegistry.find(item => item.name === "get_business_netshop_continuation_page")!;
const admin = { email: "continuation@example.test", displayName: "Synthetic", role: "admin" as const, scope: null };
const context = { principal: admin, surface: "business_collection" as const, requestId: "continuation-test" };
const args = { platform: "京东", shop: "店铺 & A", dataset: "promotion", startDate: "2026-09-01", endDate: "2026-09-03",
  window: "yearAgo", limit: 100, cursor: "signed:+/old=cursor", expectedSourceRef: "a".repeat(64),
  expectedRevision: "7:abcdefabcdef", expectedLastId: Number.MAX_SAFE_INTEGER };
const secret = "continuation-fixture-internal-secret-at-least-32-bytes";
const binding = { sourceRevision: args.expectedRevision, sourceRef: args.expectedSourceRef };
function environment(t: TestContext) {
  const values = { TERUISI_DJANGO_NETSHOP_READER_BASE_URL: "http://127.0.0.1:18021",
    TERUISI_DJANGO_NETSHOP_WRITER_BASE_URL: "http://127.0.0.1:18022", TERUISI_DJANGO_INTERNAL_SECRET: secret };
  const before = Object.fromEntries(Object.keys(values).map(key => [key, process.env[key]]));
  const fetch = globalThis.fetch;
  Object.assign(process.env, values);
  t.after(() => { globalThis.fetch = fetch; for (const [key, value] of Object.entries(before)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
}
function response(value: unknown) {
  return Response.json(value, { headers: { "x-netshop-data-revision": "7:abcdefabcdef" } });
}

test("continuation schema requires every fixed checkpoint field and rejects extras/non-safe integers", () => {
  validateToolRegistry(aiToolRegistry);
  validateToolArguments(args, entry.inputSchema);
  for (const key of Object.keys(args)) {
    const missing: Record<string, unknown> = { ...args }; delete missing[key];
    assert.throws(() => validateToolArguments(missing, entry.inputSchema), key);
  }
  for (const changed of [{ limit: 99 }, { limit: 101 }, { expectedLastId: 0 }, { expectedLastId: -1 },
    { expectedLastId: 1.5 }, { expectedLastId: true }, { expectedLastId: Number.MAX_SAFE_INTEGER + 1 },
    { cursor: "" }, { cursor: "x".repeat(1601) }, { expectedSourceRef: "A".repeat(64) },
    { expectedRevision: "x".repeat(129) }, { sql: "SELECT 1" }, { authorityVerified: true }]) {
    assert.throws(() => validateToolArguments({ ...args, ...changed }, entry.inputSchema));
  }
});

test("continuation is collection-only unscoped admin and invisible to both provider catalogs", () => {
  assert.equal(entry.risk, "read_only");
  assert.equal(entry.scopePolicy, "unscoped_only");
  assert.deepEqual(entry.execution.allowedSurfaces, ["business_collection"]);
  assert.equal(entry.execution.maxResultCharacters, 131_072);
  assert.ok(getToolsForPrincipal(admin, "business_collection").some(item => item.name === entry.name));
  for (const surface of ["business_collection", "ai_chat", "ai_agent", "dingtalk_chat"] as const) {
    assert.ok(!getOpenAiTools(admin, surface).some(item => item.function.name === entry.name));
    assert.ok(!getAnthropicTools(admin, surface).some(item => item.name === entry.name));
  }
  for (const role of ["viewer", "analyst", "operator"] as const)
    assert.ok(!getToolsForPrincipal({ ...admin, role }, "business_collection").some(item => item.name === entry.name));
  const scoped = { ...admin, scope: { platforms: ["京东"], warehouses: [], channels: [] } };
  assert.ok(!getToolsForPrincipal(scoped, "business_collection").some(item => item.name === entry.name));
  const chat = aiToolRegistry.find(item => item.name === "get_netshop_analysis_records")!;
  assert.ok(chat.execution.maxResultCharacters < 131_072);
  assert.throws(() => validateToolRegistry([{ ...entry,
    execution: { ...entry.execution, allowedSurfaces: ["ai_chat"] } }]));
  assert.throws(() => validateToolArguments({ platform: "京东", shop: "A", dataset: "promotion",
    startDate: args.startDate, endDate: args.endDate, limit: 100 }, chat.inputSchema));
});

test("continuation signs exact reader GET path, empty body and all encoded checkpoint arguments", async t => {
  environment(t);
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input));
    assert.equal(url.origin, "http://127.0.0.1:18021");
    assert.equal(url.pathname, "/api/netshop/analysis-records/continuation");
    assert.equal(init?.method, "GET");
    assert.ok(init?.body == null);
    assert.equal(init?.redirect, "manual");
    assert.equal(init?.cache, "no-store");
    assert.deepEqual(Object.fromEntries(url.searchParams), Object.fromEntries(Object.entries(args).map(([key,value]) => [key,String(value)])));
    const headers = new Headers(init?.headers);
    const empty = createHash("sha256").update("").digest("hex");
    assert.equal(headers.get("x-teruisi-content-sha256"), empty);
    const canonical = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"),
      "GET", url.pathname, url.search.slice(1), empty, headers.get("x-teruisi-principal")].join("\n");
    assert.equal(headers.get("x-teruisi-signature"), "v1=" + createHmac("sha256", secret).update(canonical).digest("hex"));
    return response({ ...binding, items: [{ rowId: "row-1" }], pagination: { nextCursor: "next" } });
  };
  const result = await entry.handler(args, context);
  assert.deepEqual(result, { ...binding, items: [{ rowId: "row-1" }], pagination: { nextCursor: "next" } });
});

test("continuation enforces complete UTF-8 JSON boundary without truncation", async t => {
  environment(t);
  const remaining = 131_072 - new TextEncoder().encode(JSON.stringify({ ...binding, text: "" })).length;
  const exact = { ...binding, text: "中".repeat(Math.floor(remaining / 3)) + "a".repeat(remaining % 3) };
  assert.equal(new TextEncoder().encode(JSON.stringify(exact)).length, 131_072);
  globalThis.fetch = async () => response(exact);
  assert.deepEqual(await entry.handler(args, context), exact);
  globalThis.fetch = async () => response({ ...exact, text: exact.text + "a" });
  await assert.rejects(entry.handler(args, context), /超过容量/);
});

test("continuation rejects header, body revision or source reference checkpoint mismatch", async t => {
  environment(t);
  for (const [body, revision] of [
    [binding, "8:abcdefabcdef"],
    [{ ...binding, sourceRevision: "8:abcdefabcdef" }, args.expectedRevision],
    [{ ...binding, sourceRef: "b".repeat(64) }, args.expectedRevision],
  ] as const) {
    globalThis.fetch = async () => Response.json(body, { headers: { "x-netshop-data-revision": revision } });
    await assert.rejects(entry.handler(args, context), { status: 409, code: "conflict" });
  }
});

test("continuation rejects role, scope or surface before transport", async t => {
  environment(t);
  let calls = 0;
  globalThis.fetch = async () => { calls++; return response({}); };
  for (const role of ["viewer", "analyst", "operator"] as const)
    await assert.rejects(entry.handler(args, { ...context, principal: { ...admin, role } }));
  await assert.rejects(entry.handler(args, { ...context, principal: { ...admin, scope: { platforms: ["京东"], warehouses: [], channels: [] } } }));
  for (const surface of ["ai_chat", "ai_agent", "dingtalk_chat"] as const)
    await assert.rejects(entry.handler(args, { ...context, surface }));
  assert.equal(calls, 0);
});

test("continuation forwards cancellation to the reader transport", async t => {
  environment(t);
  const controller = new AbortController();
  let notified: () => void = () => {};
  const started = new Promise<void>(resolve => { notified = resolve; });
  let aborted = false;
  globalThis.fetch = async (_url, init) => new Promise((_resolve, reject) => {
    assert.ok(init?.signal);
    init.signal.addEventListener("abort", () => { aborted = true; reject(new DOMException("cancelled", "AbortError")); }, { once: true });
    notified();
  });
  const pending = entry.handler(args, { ...context, signal: controller.signal });
  await started;
  controller.abort();
  await assert.rejects(pending);
  assert.equal(aborted, true);
});
