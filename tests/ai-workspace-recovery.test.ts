import assert from "node:assert/strict";
import test from "node:test";
import { recoverAiWorkspaceRequest } from "../lib/ai/workspace-recovery";
import { AI_PAGE_CONTEXT_CATALOG, createAiPageContext, normalizeAiPageContext, normalizeAiPageFilters } from "../lib/ai/page-context";
import { requestAiConversationPageWithIdentityRecovery, resolvePendingAiChatRequest } from "../app/ai-assistant-view";

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
const receipt = { request: { status: "succeeded", conversationId: "saved", assistantMessageId: "reply" } };
const saved = { conversation: { id: "saved" }, items: [{ id: "reply", conversationId: "saved" }] };
const input = () => ({ clientRequestId: "request-one", module: "sales" as const, signal: new AbortController().signal, isCurrent: () => true });

test("refresh recovers a saved reply using only two scoped reads, including replies outside recent page", async () => {
  const calls: URL[] = [];
  const result = await recoverAiWorkspaceRequest({ ...input(), fetcher: (async (url, options) => {
    assert.equal(options?.method, undefined);
    const parsed = new URL(String(url), "https://fixture.invalid"); calls.push(parsed);
    assert.equal(parsed.searchParams.get("workspaceModule"), "sales");
    return json(calls.length === 1 ? receipt : saved);
  }) as typeof fetch });
  assert.deepEqual(result, { conversationId: "saved" });
  assert.equal(calls[0].searchParams.get("clientRequestId"), "request-one");
  assert.equal(calls[1].searchParams.get("messageId"), "reply");
});

test("pending, missing and failed receipts never dispatch or release the pending request", async () => {
  for (const status of ["processing", "indeterminate", "failed", undefined]) {
    let calls = 0;
    assert.equal(await recoverAiWorkspaceRequest({ ...input(), fetcher: (async () => {
      calls++; return json({ request: { status } });
    }) as typeof fetch }), null);
    assert.equal(calls, 1);
  }
});

test("a mismatched conversation or exact reply cannot release recovery", async () => {
  for (const wrong of [
    { ...saved, conversation: { id: "other" } },
    { ...saved, items: [{ id: "other-reply", conversationId: "saved" }] },
    { ...saved, items: [{ id: "reply", conversationId: "other" }] },
    { ...saved, items: [] },
  ]) {
    let count = 0;
    await assert.rejects(recoverAiWorkspaceRequest({ ...input(), fetcher: (async () => json(++count === 1 ? receipt : wrong)) as typeof fetch }), /尚未完整读回/);
  }
});

test("navigation or account invalidation discards late receipt and late message responses", async () => {
  for (const invalidatedAt of [1, 2]) {
    let count = 0; let current = true;
    assert.equal(await recoverAiWorkspaceRequest({ ...input(), isCurrent: () => current, fetcher: (async () => {
      count++; if (count === invalidatedAt) current = false;
      return json(count === 1 ? receipt : saved);
    }) as typeof fetch }), null);
    assert.equal(count, invalidatedAt);
  }
});

test("workspace list keeps module isolation through identity recovery", async () => {
  const urls: string[] = [];
  const payload = await requestAiConversationPageWithIdentityRecovery({
    currentUser: { email: "fixture@example.invalid", role: "analyst", displayName: "测试分析员", roleLabel: "分析员" },
    workspaceModule: "inventory", page: 2,
  }, (async (url) => {
      urls.push(String(url));
      if (urls.length === 1) return new Response(JSON.stringify({ code: "unauthenticated" }), { status: 401 });
      if (String(url) === "/api/auth/me") return json({ user: { email: "fixture@example.invalid" } });
      return json({ items: [] });
    }) as typeof fetch);
  assert.deepEqual(payload.items, []);
  assert.equal(urls.filter(url => url.includes("workspaceModule=inventory")).length, 2);
});

test("page scopes reject invalid dates, unbounded selections and customer text without broadening", () => {
  assert.equal(normalizeAiPageFilters({ shops: Array(21).fill("a") }), null);
  assert.equal(normalizeAiPageFilters({ shops: Array(15).fill("中".repeat(150)) }), null);
  assert.equal(normalizeAiPageFilters({ customerAlias: "private" }), null);
  assert.equal(normalizeAiPageContext({ module: "customer_service", view: "conversations", filters: { query: "private" } }), null);
  assert.equal(normalizeAiPageContext({ module: "sales", view: "overview", period: { startDate: "2026-02-30", endDate: "2026-03-01" } }), null);
  for (const [module, value] of Object.entries(AI_PAGE_CONTEXT_CATALOG)) {
    for (const view of value.views) {
      const context = createAiPageContext({ module, view, filters: { shops: ["店铺甲"], selectedIds: ["record-1"] } });
      assert.equal(context.module, module);
      assert.equal(context.view, view);
      assert.ok(new TextEncoder().encode(JSON.stringify(context)).byteLength < 4000);
    }
  }
});

test("changing page filters after response loss preserves the exact admitted workspace payload", () => {
  const original = resolvePendingAiChatRequest(null, { workspaceModule: "sales", message: "分析销售", title: "分析销售", pageContext: createAiPageContext({ module: "sales", view: "overview", filters: { shops: ["店铺甲"] } }) }, () => "original");
  const retry = resolvePendingAiChatRequest(original, { ...original.requestPayload, pageContext: createAiPageContext({ module: "sales", view: "channel", filters: { shops: ["店铺乙"] } }) }, () => { throw new Error("must keep original request"); });
  assert.equal(retry, original);
  assert.deepEqual(retry.requestPayload.pageContext?.filters?.shops, ["店铺甲"]);
  const next = resolvePendingAiChatRequest(original, { ...original.requestPayload, workspaceModule: "inventory", pageContext: null }, () => "next");
  assert.notEqual(next.clientRequestId, original.clientRequestId);
});
