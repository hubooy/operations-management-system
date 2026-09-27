import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import { validateToolArguments, validateToolRegistry, type AiToolEntry } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") return { url: "data:text/javascript,export const env={};", shortCircuit: true };
  return nextResolve(specifier, context);
} });
const { aiToolRegistry, getOpenAiTools, getAnthropicTools, getToolsForPrincipal, executeRegisteredToolCall } = await import("../lib/ai/tool-registry");
const { summarizeToolArguments } = await import("../lib/ai/tool-audit");
const admin = { email: "pandas@example.invalid", displayName: "Fixture", role: "admin" as const, scope: null };
const entry: AiToolEntry = aiToolRegistry.find(e => e.name === "run_pandas_analysis")!;

test("pandas tool is declared once, visible in both protocols, hidden from viewer and generic MCP", () => {
  validateToolRegistry(aiToolRegistry);
  assert.equal(aiToolRegistry.filter(e => e.name === entry.name).length, 1);
  assert.equal(entry.execution.environment, "isolated_container");
  assert.equal(entry.execution.maxCallsPerRequest, 1);
  assert.equal(entry.annotations.idempotentHint, false);
  assert.ok(getOpenAiTools(admin, "ai_chat").some(t => t.function.name === entry.name));
  assert.ok(getAnthropicTools(admin, "dingtalk_chat").some(t => t.name === entry.name));
  assert.ok(!getToolsForPrincipal({ ...admin, role: "viewer" }, "ai_chat").some(t => t.name === entry.name));
  assert.ok(!getToolsForPrincipal(admin, "codex_mcp").some(t => t.name === entry.name));
  const args = { inputsJson: "[]", code: "result = frames['sales']" };
  assert.doesNotThrow(() => validateToolArguments(args, entry.inputSchema));
  assert.throws(() => validateToolArguments({ ...args, dockerHost: "tcp://host" }, entry.inputSchema));
  assert.throws(() => validateToolArguments({ ...args, code: "x".repeat(16001) }, entry.inputSchema));
});

test("central handler uses writer, server invocation identity and fixed surface; audit does not reveal code", async t => {
  const originalFetch = globalThis.fetch;
  const env = { TERUISI_DJANGO_INTERNAL_SECRET: "isolated-pandas-transport-secret-abcdefghijklmnopqrstuvwxyz",
    TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18111", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18112" };
  const previous = Object.fromEntries(Object.keys(env).map(k => [k, process.env[k]]));
  Object.assign(process.env, env);
  t.after(() => { globalThis.fetch = originalFetch; for (const [k, v] of Object.entries(previous)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; } });
  let dispatched = 0;
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "http://127.0.0.1:18112/api/ai/consumer");
    const payload = JSON.parse(String(init?.body));
    assert.equal(payload.operation, "pandas-analysis");
    assert.equal(payload.surface, "dingtalk_chat");
    assert.equal(new Headers(init?.headers).get("x-teruisi-request-id"), "fixture-invocation");
    dispatched++;
    return Response.json({ items: [{ amount: 800 }], returned: 1, truncated: false }, { headers: { "x-ai-revision": "1" } });
  };
  const result = await entry.handler({ inputsJson: "[]", code: "result = frames['sales']" },
    { principal: admin, surface: "dingtalk_chat", requestId: "fixture-request", invocationId: "fixture-invocation" });
  assert.equal(dispatched, 1);
  assert.equal(result.returned, 1);
  const summary = JSON.stringify(summarizeToolArguments({ code: "sensitive literal", inputsJson: "query literal" }));
  assert.ok(!summary.includes("sensitive literal"));
  assert.ok(!summary.includes("query literal"));
});

test("audit failure blocks isolated container dispatch", async () => {
  let calls = 0;
  const result = await executeRegisteredToolCall(entry.name, { inputsJson: "[]", code: "result = frames['sales']" },
    { principal: admin, surface: "ai_chat", requestId: "fixture-audit" },
    { entries: [{ ...entry, handler: async () => { calls++; return {}; } }], audit: async () => { throw new Error("unavailable"); } });
  assert.equal(result.ok, false);
  assert.equal(calls, 0);
});
