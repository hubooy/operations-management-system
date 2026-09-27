import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  AI_PAGE_CONTEXT_CATALOG,
  buildAiPageContextPrompt,
  createAiPageContext,
  normalizeAiPageContext,
  serializeAiPageContextForSystemPrompt,
} from "../lib/ai/page-context";
import { moduleViewCatalog } from "../app/shell/navigation-catalog";
import { resolvePendingAiChatRequest } from "../app/ai-assistant-view";

test("AI page context covers every shell module and canonical subview", () => {
  assert.deepEqual(Object.keys(AI_PAGE_CONTEXT_CATALOG).sort(), Object.keys(moduleViewCatalog).sort());
  for (const [moduleKey, definition] of Object.entries(moduleViewCatalog)) {
    assert.deepEqual(
      [...AI_PAGE_CONTEXT_CATALOG[moduleKey as keyof typeof AI_PAGE_CONTEXT_CATALOG].views],
      [...definition.views],
      `${moduleKey} views must stay aligned`,
    );
  }
});

test("AI page context is normalized from a strict allowlist", () => {
  const context = createAiPageContext({
    module: "sales",
    view: "category",
    startDate: "2026-08-01",
    endDate: "2026-08-27",
  });
  assert.equal(context.moduleLabel, "销售分析");
  assert.deepEqual(context.period, { startDate: "2026-08-01", endDate: "2026-08-27" });
  assert.ok(context.suggestedTools.includes("get_sales_category_analysis"));
  assert.equal(normalizeAiPageContext({ module: "sales", view: "not-real" }), null);
  assert.equal(normalizeAiPageContext({ module: "sales", view: "category", period: { startDate: "bad", endDate: "2026-08-27" } }), null);
  assert.equal(normalizeAiPageContext({ module: "__proto__", view: "overview" }), null);
});

test("page context stays separate from user text and participates in retry intent", () => {
  const context = createAiPageContext({ module: "inventory", view: "age", startDate: "2026-08-01", endDate: "2026-08-27" });
  const prompt = buildAiPageContextPrompt(context);
  const serialized = serializeAiPageContextForSystemPrompt(context);
  assert.match(prompt, /库存管理 \/ age/);
  assert.equal(JSON.parse(serialized).module, "inventory");

  const first = resolvePendingAiChatRequest(null, { message: "分析异常", title: "小特对话", pageContext: context }, () => "request-1");
  const changed = resolvePendingAiChatRequest(first, {
    message: "分析异常",
    title: "小特对话",
    pageContext: createAiPageContext({ module: "inventory", view: "inbound", startDate: "2026-08-01", endDate: "2026-08-27" }),
  }, () => "request-2");
  assert.equal(first.clientRequestId, "request-1");
  assert.equal(changed.clientRequestId, "request-2");
});

test("chat route validates page context and workflow injects it outside the user message", () => {
  const route = readFileSync("lib/ai/django-route.ts", "utf8");
  const workflow = readFileSync("backend/ai_assistant/chat.py", "utf8");
  assert.match(route, /normalizeAiPageContext\(payload\.pageContext\)/);
  assert.match(workflow, /<page_context>/);
  assert.match(workflow, /prompt = text\(body\["message"\]/);
  assert.match(workflow, /shortcut/);
});
