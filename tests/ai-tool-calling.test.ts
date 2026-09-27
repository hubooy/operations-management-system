import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  executeToolCallWithRegistry,
  getAnthropicTools,
  getOpenAiTools,
  getVisibleToolCatalog,
  parseToolArguments,
  validateToolRegistry,
  type AiToolEntry,
} from "../lib/ai/tool-registry-contract";
import { AI_TOOL_RUNTIME_LIMITS, createAiToolExecutionRuntime } from "../lib/ai/tool-execution-runtime";
import { AI_MODEL_TOOL_BUDGET_LIMITS } from "../lib/ai/model-tool-budget";
import {
  AI_TOOL_SYSTEM_PROMPT,
  ModelProtocolError,
  ToolLoopLimitError,
  runAnthropicToolLoop,
  runOpenAiCompatibleToolLoop,
} from "../lib/ai/tool-loop";
import { runSequentialBatchWithinBudget } from "../lib/ai/mcp-execution-budget";
import { BoundedFetchError, fetchBoundedJson } from "../lib/ai/bounded-fetch";
import { resolveModelToolLoopLimits } from "../lib/ai/model-gateway";

const viewer: AppPrincipal = {
  email: "viewer@example.com",
  displayName: "Viewer",
  role: "viewer",
  scope: null,
};
const admin: AppPrincipal = { ...viewer, email: "admin@example.com", role: "admin" };
const annotations = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
} as const;
const schema = { type: "object", properties: {}, additionalProperties: false } as const;
const execution = {
  environment: "worker_inline",
  mode: "direct",
  allowedSurfaces: ["ai_chat", "codex_mcp", "test"],
  timeoutMs: 1_000,
  maxResultCharacters: 4_000,
  maxCallsPerRequest: 4,
} as const;
const fakeEntries = [
  {
    name: "public_lookup",
    title: "Public lookup",
    description: "Returns a bounded public result with returned and truncated fields.",
    inputSchema: schema,
    annotations,
  risk: "read_only",
  allowedRoles: ["viewer", "admin"],
  scopePolicy: "metadata_safe",
  execution,
    handler: async () => ({ returned: 1, truncated: false }),
  },
  {
    name: "admin_lookup",
    title: "Admin lookup",
    description: "Returns a bounded admin result with returned and truncated fields.",
    inputSchema: schema,
    annotations,
  risk: "read_only",
  allowedRoles: ["admin"],
  scopePolicy: "metadata_safe",
  execution,
    handler: async () => ({ returned: 1, truncated: false }),
  },
] satisfies readonly AiToolEntry[];

test("registry validation rejects duplicates and incomplete schema contracts", () => {
  validateToolRegistry(fakeEntries);
  assert.throws(
    () => validateToolRegistry([fakeEntries[0], { ...fakeEntries[1], name: "public_lookup" }]),
    /重复/,
  );
  assert.throws(
    () => validateToolRegistry([{ ...fakeEntries[0], inputSchema: { ...schema, additionalProperties: true } } as unknown as AiToolEntry]),
    /额外参数/,
  );
  assert.throws(
    () => validateToolRegistry([{ ...fakeEntries[0], handler: undefined } as unknown as AiToolEntry]),
    /handler/,
  );
  assert.throws(
    () => validateToolRegistry([{ ...fakeEntries[0], execution: undefined } as unknown as AiToolEntry]),
    /执行环境/,
  );
  assert.throws(
    () => validateToolRegistry([{ ...fakeEntries[0], execution: { ...execution, allowedSurfaces: [] } }]),
    /入口范围/,
  );
  assert.throws(
    () => validateToolRegistry([{
      ...fakeEntries[0],
      risk: "write",
      annotations: { ...annotations, readOnlyHint: false, idempotentHint: false },
    }]),
    /不能以内联直接模式/,
  );
});

test("one registry entry set derives role-filtered provider schemas and catalog", () => {
  assert.deepEqual(getOpenAiTools(viewer, "ai_chat", fakeEntries).map((item) => item.function.name), ["public_lookup"]);
  assert.deepEqual(getAnthropicTools(viewer, "ai_chat", fakeEntries).map((item) => item.name), ["public_lookup"]);
  assert.deepEqual(getVisibleToolCatalog(viewer, "ai_chat", fakeEntries).map((item) => item.name), ["public_lookup"]);
  assert.deepEqual(getOpenAiTools(admin, "ai_chat", fakeEntries).map((item) => item.function.name), ["public_lookup", "admin_lookup"]);
  assert.deepEqual(getOpenAiTools(admin, "market_ai", fakeEntries), []);
  assert.equal(getOpenAiTools(viewer, "ai_chat", fakeEntries)[0].function.parameters, fakeEntries[0].inputSchema);
  assert.equal(getAnthropicTools(viewer, "ai_chat", fakeEntries)[0].input_schema, fakeEntries[0].inputSchema);
  const scopedViewer = { ...viewer, scope: { warehouses: ["华东仓"], channels: [], platforms: [] } };
  const unsafeForScope: AiToolEntry = { ...fakeEntries[0], name: "broad_lookup", scopePolicy: "unscoped_only" };
  assert.deepEqual(getOpenAiTools(scopedViewer, "ai_chat", [unsafeForScope, fakeEntries[0]]).map((item) => item.function.name), ["public_lookup"]);
  const webOnly: AiToolEntry = { ...fakeEntries[0], name: "web_only", execution: { ...execution, allowedSurfaces: ["ai_chat"] } };
  assert.deepEqual(getOpenAiTools(admin, "codex_mcp", [webOnly, fakeEntries[0]]).map((item) => item.function.name), ["public_lookup"]);
  const pendingWrite: AiToolEntry = {
    ...fakeEntries[1],
    name: "pending_write",
    risk: "write",
    annotations: { ...annotations, readOnlyHint: false, idempotentHint: false },
    execution: { ...execution, mode: "confirmation_required" },
  };
  assert.deepEqual(getOpenAiTools(admin, "ai_chat", [pendingWrite, fakeEntries[0]]).map((item) => item.function.name), ["public_lookup"]);
});

test("surface and risk gates are enforced again immediately before execution", async () => {
  let called = false;
  const webOnly: AiToolEntry = {
    ...fakeEntries[0],
    execution: { ...execution, allowedSurfaces: ["ai_chat"] },
    handler: async () => { called = true; return {}; },
  };
  const deniedSurface = await executeToolCallWithRegistry(webOnly.name, {}, {
    principal: viewer, surface: "codex_mcp", requestId: "surface-gate",
  }, { entries: [webOnly], audit: async () => undefined });
  assert.equal(deniedSurface.ok, false);
  if (!deniedSurface.ok) assert.equal(deniedSurface.error.code, "forbidden");

  const invalidDirectWrite: AiToolEntry = {
    ...fakeEntries[0],
    risk: "write",
    annotations: { ...annotations, readOnlyHint: false, idempotentHint: false },
    handler: async () => { called = true; return {}; },
  };
  const deniedRisk = await executeToolCallWithRegistry(invalidDirectWrite.name, {}, {
    principal: viewer, surface: "test", requestId: "risk-gate",
  }, { entries: [invalidDirectWrite], audit: async () => undefined });
  assert.equal(deniedRisk.ok, false);
  if (!deniedRisk.ok) assert.equal(deniedRisk.error.code, "forbidden");
  assert.equal(called, false);
});

test("discovery and execution both reject an unsupported forged environment", async () => {
  let called = false;
  const forgedEnvironment = {
    ...fakeEntries[0],
    execution: { ...execution, environment: "external_sandbox" },
    handler: async () => { called = true; return {}; },
  } as unknown as AiToolEntry;
  assert.deepEqual(getOpenAiTools(viewer, "test", [forgedEnvironment]), []);
  const result = await executeToolCallWithRegistry(forgedEnvironment.name, {}, {
    principal: viewer, surface: "test", requestId: "environment-gate",
  }, { entries: [forgedEnvironment], audit: async () => undefined });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "unsupported_execution_environment");
  assert.equal(called, false);
});

test("execution rejects model-claimed privilege and malformed arguments", async () => {
  const context = { principal: viewer, surface: "test", requestId: "request-role" } as const;
  const audits: Array<Record<string, unknown>> = [];
  const forbidden = await executeToolCallWithRegistry("admin_lookup", {}, context, {
    entries: fakeEntries,
    audit: async (input) => { audits.push(input); },
  });
  assert.equal(forbidden.ok, false);
  if (!forbidden.ok) assert.equal(forbidden.error.code, "forbidden");
  assert.equal(audits[0].actorEmail, viewer.email);
  assert.equal(audits[0].actorRole, "viewer");

  const malformed = await executeToolCallWithRegistry("public_lookup", "{bad json", context, {
    entries: fakeEntries,
    audit: async () => undefined,
  });
  assert.equal(malformed.ok, false);
  if (!malformed.ok) assert.equal(malformed.error.code, "invalid_arguments");
  assert.throws(() => parseToolArguments("[]"), /JSON 对象/);
});

test("invalid or oversized requested tool names are bounded before runtime state and audit", async () => {
  const oversizedName = `bad-${"x".repeat(10_000)}`;
  const audits: Array<Record<string, unknown>> = [];
  const runtime = createAiToolExecutionRuntime({
    context: { principal: viewer, surface: "test", requestId: "bounded-tool-name" },
    entries: fakeEntries,
    audit: async (input) => { audits.push(input); },
  });
  const result = await runtime.execute(oversizedName, {});
  assert.equal(result.ok, false);
  assert.equal(result.toolName, "[invalid_tool_name]");
  assert.deepEqual(Object.keys(runtime.snapshot().callsByTool), ["[invalid_tool_name]"]);
  assert.deepEqual(audits.map((item) => item.toolName), ["[invalid_tool_name]"]);
});

test("every tool fails closed before its handler when audit storage is unavailable", async () => {
  let called = false;
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    handler: async () => { called = true; return { returned: 1, truncated: false }; },
  };
  const result = await executeToolCallWithRegistry("public_lookup", {}, {
    principal: viewer,
    surface: "test",
    requestId: "request-audit-failure",
  }, {
    entries: [entry],
    audit: async () => { throw new Error("audit unavailable"); },
  });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.error.code, "audit_unavailable");
    assert.equal(result.auditStatus, "unavailable");
  }
  assert.equal(called, false);
});

test("a missing completion audit never returns handler data", async () => {
  let auditCalls = 0;
  const result = await executeToolCallWithRegistry("public_lookup", {}, {
    principal: viewer,
    surface: "test",
    requestId: "request-completion-audit-failure",
  }, {
    entries: fakeEntries,
    audit: async () => {
      auditCalls += 1;
      if (auditCalls > 1) throw new Error("completion audit unavailable");
    },
  });
  assert.equal(auditCalls, 2);
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.error.code, "audit_unavailable");
    assert.equal(Object.hasOwn(result, "data"), false);
  }
});

test("runtime validates the complete registered JSON schema before invoking a handler", async () => {
  let called = false;
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", minLength: 2, maxLength: 5 },
        limit: { type: "integer", minimum: 1, maximum: 8 },
      },
      required: ["query"],
      additionalProperties: false,
    },
    handler: async () => { called = true; return {}; },
  };
  const result = await executeToolCallWithRegistry("public_lookup", { query: "x", limit: 99 }, {
    principal: viewer, surface: "test", requestId: "schema-runtime",
  }, { entries: [entry], audit: async () => undefined });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "invalid_arguments");
  assert.equal(called, false);
});

test("write and dangerous tools fail closed when audit preflight is unavailable", async () => {
  let called = false;
  const entry: AiToolEntry = {
    ...fakeEntries[1],
    name: "sensitive_action",
    risk: "write",
    annotations: { ...annotations, readOnlyHint: false, idempotentHint: false },
    execution: { ...execution, mode: "confirmation_required" },
    handler: async () => { called = true; return {}; },
  };
  const result = await executeToolCallWithRegistry(entry.name, {}, {
    principal: admin, surface: "test", requestId: "audit-preflight",
  }, { entries: [entry], audit: async () => { throw new Error("down"); } });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "audit_unavailable");
  assert.equal(called, false);
});

test("MCP timeout waits for settlement and never starts later batch tools", async () => {
  let active = 0;
  let peakActive = 0;
  const started: string[] = [];
  const results = await runSequentialBatchWithinBudget({
    items: ["slow", "must-not-start", "also-not-start"],
    totalBudgetMs: 100,
    perItemTimeoutMs: 5,
    operation: async (item, signal) => {
      started.push(item);
      active += 1;
      peakActive = Math.max(peakActive, active);
      assert.equal(signal.aborted, false);
      await new Promise((resolve) => setTimeout(resolve, 25));
      assert.equal(signal.aborted, true);
      active -= 1;
      return `completed:${item}`;
    },
    notStarted: (item, reason) => `${reason}:${item}`,
  });
  assert.deepEqual(started, ["slow"]);
  assert.equal(active, 0);
  assert.equal(peakActive, 1);
  assert.deepEqual(results, [
    "item_timeout:slow",
    "prior_timeout:must-not-start",
    "prior_timeout:also-not-start",
  ]);
});

test("registry propagates cooperative abort state before handler execution", async () => {
  const controller = new AbortController();
  controller.abort();
  let called = false;
  const entry: AiToolEntry = { ...fakeEntries[0], handler: async () => { called = true; return {}; } };
  const result = await executeToolCallWithRegistry(entry.name, {}, {
    principal: viewer, surface: "test", requestId: "already-aborted", signal: controller.signal,
  }, { entries: [entry], audit: async () => undefined });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "tool_cancelled");
  assert.equal(called, false);
});

test("cancellation during the started audit prevents the handler from starting", async () => {
  const controller = new AbortController();
  let called = false;
  const statuses: string[] = [];
  const runtime = createAiToolExecutionRuntime({
    context: {
      principal: viewer,
      surface: "test",
      requestId: "cancel-during-audit",
      signal: controller.signal,
    },
    entries: [{
      ...fakeEntries[0],
      handler: async () => { called = true; return {}; },
    }],
    audit: async (input) => {
      statuses.push(input.status);
      if (input.status === "started") controller.abort();
    },
  });
  const result = await runtime.execute(fakeEntries[0].name, {});
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "tool_cancelled");
  assert.equal(called, false);
  assert.deepEqual(statuses, ["started", "failed"]);
});

test("request runtime enforces call budgets and correlates provider calls", async () => {
  let called = 0;
  const audits: Array<Record<string, unknown>> = [];
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    handler: async () => { called += 1; return { returned: 1 }; },
  };
  const runtime = createAiToolExecutionRuntime({
    context: { principal: viewer, surface: "test", requestId: "runtime-budget" },
    entries: [entry],
    audit: async (input) => { audits.push(input); },
    limits: { maxTotalCalls: 1, maxCumulativeDurationMs: 1_000 },
  });
  const first = await runtime.execute(entry.name, {}, { providerCallId: "provider-call-1" });
  const second = await runtime.execute(entry.name, {}, { providerCallId: "provider-call-2" });
  assert.equal(first.ok, true);
  assert.equal(second.ok, false);
  if (!second.ok) assert.equal(second.error.code, "tool_call_budget_exceeded");
  assert.equal(called, 1);
  assert.equal(runtime.snapshot().totalCalls, 2);
  const firstAudits = audits.filter((item) => item.providerCallId === "provider-call-1");
  assert.equal(firstAudits.length, 2);
  assert.equal(firstAudits[0].invocationId, firstAudits[1].invocationId);
  assert.notEqual(firstAudits[0].invocationId, "provider-call-1");
});

test("request runtime timeout settles the handler and terminates later calls", async () => {
  let settled = false;
  let laterCalled = false;
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    execution: { ...execution, timeoutMs: 100 },
    handler: async (_args, context) => {
      assert.equal(Object.hasOwn(context, "completeExecution"), false);
      await new Promise((resolve) => setTimeout(resolve, 120));
      assert.equal(context.signal?.aborted, true);
      settled = true;
      return { returned: 1 };
    },
  };
  const laterEntry: AiToolEntry = {
    ...fakeEntries[1],
    name: "later_lookup",
    allowedRoles: ["viewer", "admin"],
    handler: async () => { laterCalled = true; return { returned: 1 }; },
  };
  const runtime = createAiToolExecutionRuntime({
    context: { principal: viewer, surface: "test", requestId: "runtime-timeout" },
    entries: [entry, laterEntry],
    audit: async () => undefined,
    limits: { maxCumulativeDurationMs: 1_000 },
  });
  const startedAt = performance.now();
  const result = await runtime.execute(entry.name, {});
  assert.equal(settled, true);
  assert.ok(performance.now() - startedAt >= 110);
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "tool_timeout");
  const laterResult = await runtime.execute(laterEntry.name, {});
  assert.equal(laterResult.ok, false);
  if (!laterResult.ok) assert.equal(laterResult.error.code, "tool_runtime_terminated");
  assert.equal(laterCalled, false);
  assert.equal(runtime.snapshot().active, false);
  assert.equal(runtime.snapshot().terminalErrorCode, "tool_runtime_terminated");
});

test("handler deadline stops after a bounded result while completion audit remains fail-closed", async () => {
  const statuses: string[] = [];
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    execution: { ...execution, timeoutMs: 100 },
    handler: async () => ({ returned: 1 }),
  };
  const runtime = createAiToolExecutionRuntime({
    context: { principal: viewer, surface: "test", requestId: "slow-completion-audit" },
    entries: [entry],
    audit: async (input) => {
      statuses.push(input.status);
      if (input.status === "succeeded") await new Promise((resolve) => setTimeout(resolve, 120));
    },
    limits: { maxCumulativeDurationMs: 1_000 },
  });
  const startedAt = performance.now();
  const result = await runtime.execute(entry.name, {});
  assert.equal(result.ok, true);
  assert.ok(performance.now() - startedAt >= 110);
  assert.deepEqual(statuses, ["started", "succeeded"]);
});

test("oversized tool results fail before success audit and never expose full data", async () => {
  const audits: Array<Record<string, unknown>> = [];
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    execution: { ...execution, maxResultCharacters: 1_000 },
    handler: async () => ({ secretLargeResult: "x".repeat(1_100) }),
  };
  const result = await executeToolCallWithRegistry(entry.name, {}, {
    principal: viewer, surface: "test", requestId: "large-result",
  }, { entries: [entry], audit: async (input) => { audits.push(input); } });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "tool_result_too_large");
  assert.deepEqual(audits.map((item) => item.status), ["started", "failed"]);
  assert.equal(audits.some((item) => Object.hasOwn(item, "result")), false);
});

test("the registry bounds the final provider result envelope before success audit", async () => {
  const audits: string[] = [];
  const payload = "x".repeat(980);
  assert.ok(JSON.stringify({ payload }).length < 1_000);
  assert.ok(JSON.stringify({ ok: true, toolName: "public_lookup", data: { payload } }).length > 1_000);
  const entry: AiToolEntry = {
    ...fakeEntries[0],
    execution: { ...execution, maxResultCharacters: 1_000 },
    handler: async () => ({ payload }),
  };
  const result = await executeToolCallWithRegistry(entry.name, {}, {
    principal: viewer, surface: "test", requestId: "envelope-limit",
  }, { entries: [entry], audit: async (input) => { audits.push(input.status); } });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.error.code, "tool_result_too_large");
  assert.deepEqual(audits, ["started", "failed"]);
});

test("model JSON fetch limits declared and streamed response bytes", async () => {
  await assert.rejects(
    fetchBoundedJson({
      url: "https://model.example/v1/chat/completions",
      init: {}, timeoutMs: 100, maxBytes: 8,
      fetcher: async () => new Response("123456789", { headers: { "content-length": "9" } }),
    }),
    (error: unknown) => error instanceof BoundedFetchError && error.code === "response_too_large",
  );
  await assert.rejects(
    fetchBoundedJson({
      url: "https://model.example/v1/chat/completions",
      init: {}, timeoutMs: 100, maxBytes: 8,
      fetcher: async () => new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode("1234"));
          controller.enqueue(new TextEncoder().encode("56789"));
          controller.close();
        },
      })),
    }),
    (error: unknown) => error instanceof BoundedFetchError && error.code === "response_too_large",
  );
});

test("model timeout remains active while the response body is pending", async () => {
  await assert.rejects(
    fetchBoundedJson({
      url: "https://model.example/v1/chat/completions",
      init: {}, timeoutMs: 5, maxBytes: 100,
      fetcher: async (_url, init) => new Response(new ReadableStream({
        start(controller) {
          init?.signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
        },
      })),
    }),
    (error: unknown) => error instanceof BoundedFetchError && error.code === "timeout",
  );
});

test("external cancellation aborts the bounded model response without reporting a timeout", async () => {
  const controller = new AbortController();
  await assert.rejects(
    fetchBoundedJson({
      url: "https://model.example/v1/chat/completions",
      init: {},
      timeoutMs: 1_000,
      signal: controller.signal,
      fetcher: async (_url, init) => new Response(new ReadableStream({
        start(stream) {
          init?.signal?.addEventListener("abort", () => stream.error(new DOMException("aborted", "AbortError")));
          controller.abort();
        },
      })),
    }),
    (error: unknown) => error instanceof BoundedFetchError && error.code === "cancelled",
  );
});

test("OpenAI-compatible loop preserves tool call id and feeds role=tool result back", async () => {
  const requests: Array<Record<string, unknown>> = [];
  let executed = 0;
  const reply = await runOpenAiCompatibleToolLoop({
    messages: [{ role: "user", content: "查库存" }],
    tools: [{ type: "function", function: { name: "public_lookup" } }],
    request: async (body) => {
      requests.push(structuredClone(body));
      return requests.length === 1
        ? { choices: [{ message: { content: null, reasoning_content: "先查询库存再回答", tool_calls: [{ id: "call-1", type: "function", function: { name: "public_lookup", arguments: "{}" } }] } }] }
        : { choices: [{ message: { content: "已完成" } }] };
    },
    executeTool: async (name, rawArguments, metadata) => {
      executed += 1;
      assert.equal(name, "public_lookup");
      assert.deepEqual(parseToolArguments(rawArguments), {});
      assert.equal(metadata.providerCallId, "call-1");
      return { ok: true, toolName: name, data: { returned: 1 } };
    },
  });
  assert.equal(reply, "已完成");
  assert.equal(executed, 1);
  const secondMessages = requests[1].messages as Array<Record<string, unknown>>;
  const assistantMessage = secondMessages.find((message) => message.role === "assistant");
  const toolMessage = secondMessages.find((message) => message.role === "tool");
  assert.equal(assistantMessage?.reasoning_content, "先查询库存再回答");
  assert.equal(toolMessage?.tool_call_id, "call-1");
  assert.match(String(toolMessage?.content), /"returned":1/);
});

test("OpenAI-compatible loop reports empty final content without exposing reasoning text", async () => {
  const privateReasoning = "不应显示给用户的推理过程";
  await assert.rejects(
    runOpenAiCompatibleToolLoop({
      messages: [{ role: "user", content: "查数据" }],
      tools: [],
      request: async () => ({
        choices: [{
          message: { content: null, reasoning_content: privateReasoning },
          finish_reason: "length",
        }],
        usage: {
          completion_tokens: 1024,
          completion_tokens_details: { reasoning_tokens: 1024 },
        },
      }),
      executeTool: async () => { throw new Error("must not execute"); },
    }),
    (error: unknown) => {
      assert.ok(error instanceof ModelProtocolError);
      assert.match(error.message, /模型未返回最终正文/);
      assert.match(error.message, /finish_reason=length/);
      assert.match(error.message, /completion_tokens=1024/);
      assert.match(error.message, /reasoning_tokens=1024/);
      assert.match(error.message, /仅返回推理内容 12 字符/);
      assert.doesNotMatch(error.message, new RegExp(privateReasoning));
      return true;
    },
  );
});

test("tool loop observes cancellation after a model round and before starting a tool", async () => {
  const controller = new AbortController();
  let executed = false;
  await assert.rejects(
    runOpenAiCompatibleToolLoop({
      messages: [{ role: "user", content: "查数据" }],
      tools: [{ type: "function", function: { name: "public_lookup" } }],
      signal: controller.signal,
      request: async () => {
        controller.abort();
        return { choices: [{ message: { tool_calls: [{ id: "cancelled-call", function: { name: "public_lookup", arguments: "{}" } }] } }] };
      },
      executeTool: async (name) => { executed = true; return { ok: true, toolName: name, data: {} }; },
    }),
    /取消/,
  );
  assert.equal(executed, false);
});

test("Anthropic loop preserves tool_use id and feeds a user tool_result block back", async () => {
  const requests: Array<Record<string, unknown>> = [];
  const reply = await runAnthropicToolLoop({
    messages: [{ role: "user", content: "查商品" }],
    tools: [{ name: "public_lookup", input_schema: schema }],
    request: async (body) => {
      requests.push(structuredClone(body));
      return requests.length === 1
        ? { content: [{ type: "tool_use", id: "use-1", name: "public_lookup", input: {} }], stop_reason: "tool_use" }
        : { content: [{ type: "text", text: "查询完成" }], stop_reason: "end_turn" };
    },
    executeTool: async (name, _args, metadata) => {
      assert.equal(metadata.providerCallId, "use-1");
      return { ok: true, toolName: name, data: { returned: 2 } };
    },
  });
  assert.equal(reply, "查询完成");
  const secondMessages = requests[1].messages as Array<Record<string, unknown>>;
  const resultMessage = secondMessages.at(-1);
  const blocks = resultMessage?.content as Array<Record<string, unknown>>;
  assert.equal(resultMessage?.role, "user");
  assert.equal(blocks[0].type, "tool_result");
  assert.equal(blocks[0].tool_use_id, "use-1");
});

test("tool loop enforces per-round limits and keeps no-tool legacy answers", async () => {
  await assert.rejects(
    runOpenAiCompatibleToolLoop({
      messages: [{ role: "user", content: "loop" }],
      tools: [{ type: "function" }],
      request: async () => ({
        choices: [{
          message: {
            tool_calls: Array.from({ length: 5 }, (_, index) => ({
              id: `call-${index}`,
              function: { name: "public_lookup", arguments: "{}" },
            })),
          },
        }],
      }),
      executeTool: async (name) => ({ ok: true, toolName: name, data: {} }),
    }),
    ToolLoopLimitError,
  );

  let requests = 0;
  const plain = await runOpenAiCompatibleToolLoop({
    messages: [{ role: "user", content: "hello" }],
    tools: [],
    request: async (body) => {
      requests += 1;
      assert.equal(Object.hasOwn(body, "tools"), false);
      return { choices: [{ message: { content: "普通回答" } }] };
    },
    executeTool: async () => { throw new Error("must not execute"); },
  });
  assert.equal(plain, "普通回答");
  assert.equal(requests, 1);
});

test("model-configured total budget allows more than four parallel calls in one round", async () => {
  let requests = 0;
  let executions = 0;
  const reply = await runOpenAiCompatibleToolLoop({
    messages: [{ role: "user", content: "并行查询多个模块" }],
    tools: [{ type: "function", function: { name: "public_lookup" } }],
    limits: resolveModelToolLoopLimits({ maxToolRounds: 6, maxTotalToolCalls: 12 }),
    request: async () => {
      requests += 1;
      return requests === 1
        ? { choices: [{ message: { tool_calls: Array.from({ length: 5 }, (_, index) => ({ id: `parallel-${index}`, function: { name: "public_lookup", arguments: "{}" } })) } }] }
        : { choices: [{ message: { content: "五项查询完成" } }] };
    },
    executeTool: async (name) => { executions += 1; return { ok: true, toolName: name, data: {} }; },
  });
  assert.equal(reply, "五项查询完成");
  assert.equal(executions, 5);
});

test("configured model budgets can continue beyond the former six-round ceiling", async () => {
  assert.deepEqual(AI_MODEL_TOOL_BUDGET_LIMITS, {
    defaultRounds: 6,
    defaultTotalCalls: 12,
    increaseBy: 50,
    maximumRounds: 62,
    maximumTotalCalls: 300,
  });
  assert.equal(AI_TOOL_RUNTIME_LIMITS.maxTotalCalls.maximum, 300);
  let rounds = 0;
  const reply = await runOpenAiCompatibleToolLoop({
    messages: [{ role: "user", content: "需要多步检索" }],
    tools: [{ type: "function", function: { name: "public_lookup" } }],
    limits: { maxRounds: 56, maxCallsPerRound: 4, maxTotalCalls: 62 },
    request: async () => {
      rounds += 1;
      return rounds <= 6
        ? { choices: [{ message: { tool_calls: [{ id: `call-${rounds}`, function: { name: "public_lookup", arguments: "{}" } }] } }] }
        : { choices: [{ message: { content: "第七轮完成" } }] };
    },
    executeTool: async (name) => ({ ok: true, toolName: name, data: { returned: 1 } }),
  });
  assert.equal(reply, "第七轮完成");
  assert.equal(rounds, 7);
});

test("production registry keeps all existing operations tools plus knowledge and global search", async () => {
  const [registry, apiRoute, assistant, operations, searchHandler, mcp, marketAiRoute] = await Promise.all([
    readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/tools/route.ts", import.meta.url), "utf8"),
    readFile(new URL("./legacy/ai/assistant-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/operations-tools.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/search/ai-tool.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/mcp/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market/ai/route.ts", import.meta.url), "utf8"),
  ]);
  for (const name of [
    "search_system_knowledge",
    "get_data_freshness",
    "get_sales_summary",
    "get_inventory_health",
    "get_product_performance",
    "list_replenishment_plans",
    "get_customer_service_conversations",
    "get_market_overview",
    "get_market_sku_trend",
    "get_market_brand_analysis",
    "get_market_price_band_analysis",
    "get_market_pending_review_summary",
    "search_system_data",
  ]) assert.match(registry, new RegExp(`name: "${name}"`));
  assert.match(registry, /validateToolRegistry\(aiToolRegistry\)/);
  assert.match(apiRoute, /getVisibleToolCatalog/);
  assert.match(mcp, /getVisibleToolCatalog/);
  assert.match(mcp, /createRegisteredToolExecutionRuntime/);
  assert.match(mcp, /MAX_BATCH_REQUESTS = 20/);
  assert.match(mcp, /MAX_BATCH_DURATION_MS/);
  assert.match(mcp, /mcp-\$\{digest\.slice/);
  assert.doesNotMatch(mcp, /BOOTSTRAP_ADMIN_EMAIL/);
  assert.match(assistant, /createRegisteredToolExecutionRuntime/);
  assert.match(assistant, /toolRuntime\.getOpenAiTools/);
  assert.match(assistant, /toolRuntime\.getAnthropicTools/);
  assert.match(assistant, /extractAiTableArtifactCandidates/);
  assert.match(assistant, /persistAiTableArtifacts/);
  assert.match(marketAiRoute, /MARKET_AI_SYSTEM_PROMPT/);
  assert.match(marketAiRoute, /systemPrompt: MARKET_AI_SYSTEM_PROMPT/);
  assert.doesNotMatch(marketAiRoute, /AI_TOOL_SYSTEM_PROMPT/);
  assert.doesNotMatch(operations, /operationsToolNames/);
  assert.doesNotMatch(searchHandler, /ToolDefinition|inputSchema/);
  assert.match(AI_TOOL_SYSTEM_PROMPT, /get_data_freshness/);
  assert.match(AI_TOOL_SYSTEM_PROMPT, /filtersApplied/);
  assert.match(AI_TOOL_SYSTEM_PROMPT, /人民币分/);
  assert.match(AI_TOOL_SYSTEM_PROMPT, /truncated/);
});
