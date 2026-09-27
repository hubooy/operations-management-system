import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { registerHooks } from "node:module";
import test from "node:test";
registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") return { url: "data:text/javascript,export const env={};", shortCircuit: true };
  return nextResolve(specifier, context);
} });
const { requestSalesAnalysisContinuation, createMarketGatewayAuthHeaders, EMPTY_SHA256 } = await import("../lib/django/sales-gateway");
const { requestMarketAnalysisContinuation } = await import("../lib/django/market-service");
const { readBusinessSalesContinuation, readBusinessMarketContinuation } = await import("../lib/ai/business-other-continuation");
const admin = { email: "continuation@example.test", displayName: "Synthetic", role: "admin" as const, scope: null };
const secret = "other-continuation-test-secret-at-least-32-bytes";

for (const domain of ["sales", "market"] as const) {
  const revision = domain === "sales" ? "7:8" : "7:abcdefabcdef";
  const fields = { platform: "京东", startDate: "2026-09-01", endDate: "2026-09-03", window: "yearAgo", limit: 100,
    cursor: "signed:+/expired=cursor", expectedSourceRef: "a".repeat(64), expectedRevision: revision, expectedLastId: Number.MAX_SAFE_INTEGER,
    ...(domain === "sales" ? { shop: "样例店 & A", channel: "B端" } : { category: "商用 & A", scope: "POP", rankingDimension: "SKU", priceBandFilter: "全部" }) };
  const query = () => new URLSearchParams(Object.entries(fields).map(([key,value]) => [key,String(value)]));
  const binding = { sourceRevision: revision, sourceRef: fields.expectedSourceRef };
  const headers = () => ({ [`x-${domain}-data-revision`]: revision, ...(domain === "sales" ? { "x-sales-source-revision": revision } : {}) });
  const request = (params: URLSearchParams, fetchImpl: typeof fetch, signal?: AbortSignal, timeoutMs = 1000) => domain === "sales"
    ? requestSalesAnalysisContinuation(admin, params, { config: { djangoBaseUrl: "http://127.0.0.1:18011", internalSecret: secret, timeoutMs }, fetchImpl, signal })
    : requestMarketAnalysisContinuation(admin, params, { config: { readerBaseUrl: "http://127.0.0.1:18011", writerBaseUrl: "http://127.0.0.1:18012", internalSecret: secret, timeoutMs }, fetchImpl, signal });

  test(`${domain} continuation snapshots all query fields and signs only exact reader GET`, async () => {
    const params = query(), fixed = params.toString();
    const pending = request(params, async (input, init) => {
      const url = new URL(String(input));
      assert.equal(url.origin, "http://127.0.0.1:18011");
      assert.equal(url.pathname, `/api/${domain}/analysis-records/continuation`);
      assert.equal(url.search.slice(1), fixed);
      assert.equal(init?.method, "GET"); assert.ok(init?.body == null);
      assert.equal(init?.cache, "no-store"); assert.equal(init?.redirect, "manual");
      const h = new Headers(init?.headers), empty = createHash("sha256").update("").digest("hex");
      assert.equal(h.get("x-teruisi-content-sha256"), empty);
      const canonical = ["v1", h.get("x-teruisi-timestamp"), h.get("x-teruisi-request-id"), "GET", url.pathname,
        fixed, empty, h.get("x-teruisi-principal")].join("\n");
      assert.equal(h.get("x-teruisi-signature"), "v1=" + createHmac("sha256", secret).update(canonical).digest("hex"));
      return Response.json(binding, { headers: headers() });
    });
    params.set("expectedRevision", "changed");
    assert.deepEqual(await pending, binding);
  });

  test(`${domain} continuation checks complete UTF-8 bytes and never truncates`, async () => {
    const left = 131_072 - new TextEncoder().encode(JSON.stringify({ ...binding, text: "" })).length;
    const exact = { ...binding, text: "中".repeat(Math.floor(left/3)) + "a".repeat(left%3) };
    assert.equal(new TextEncoder().encode(JSON.stringify(exact)).length, 131_072);
    assert.deepEqual(await request(query(), async () => Response.json(exact, { headers: headers() })), exact);
    await assert.rejects(request(query(), async () => Response.json({ ...exact, text: exact.text + "a" }, { headers: headers() })), { status: 413 });
  });

  test(`${domain} continuation rejects revision/identity mismatches and malformed/redirect responses`, async () => {
    for (const [body, changed] of [[binding, { [`x-${domain}-data-revision`]: "9:abcdefabcdef" }],
      [{ ...binding, sourceRevision: "changed" }, {}], [{ ...binding, sourceRef: "b".repeat(64) }, {}],
      ...(domain === "sales" ? [[binding, { "x-sales-source-revision": "7:9" }]] : [])] as const) {
      await assert.rejects(request(query(), async () => Response.json(body, { headers: { ...headers(), ...changed } })), { status: 409, code: "conflict" });
    }
    for (const value of [null, [], "not-an-object"])
      await assert.rejects(request(query(), async () => Response.json(value, { headers: headers() })), { status: 503 });
    await assert.rejects(request(query(), async () => new Response(null, { status: 302, headers: { location: "http://127.0.0.1:18012" } })), { status: 503 });
    await assert.rejects(request(query(), async () => Response.json({ error: "changed", code: "analysis_revision_changed" }, { status: 409 })), { status: 409 });
  });

  test(`${domain} continuation deadline and cancellation abort transport`, async () => {
    for (const cancellation of [false, true]) {
      const controller = new AbortController();
      let aborted = false;
      const result = request(query(), async (_url, init) => new Promise((_resolve, reject) => {
        assert.ok(init?.signal);
        init.signal.addEventListener("abort", () => { aborted = true; reject(new DOMException("aborted", "AbortError")); }, { once: true });
        if (cancellation) controller.abort();
      }), controller.signal, 10);
      await assert.rejects(result, { status: 503 });
      assert.equal(aborted, true);
    }
    const cancelled = new AbortController(); cancelled.abort();
    await assert.rejects(request(query(), async () => { assert.fail("pre-cancelled transport"); }, cancelled.signal));
  });

  test(`${domain} thin handler forbids non-admin, scoped and model surfaces before transport`, async () => {
    const handler = domain === "sales" ? readBusinessSalesContinuation : readBusinessMarketContinuation;
    const base = { principal: admin, surface: "business_collection" as const, requestId: "test" };
    for (const role of ["viewer", "analyst", "operator"] as const)
      await assert.rejects(handler(fields, { ...base, principal: { ...admin, role } }), { status: 403 });
    await assert.rejects(handler(fields, { ...base, principal: { ...admin, scope: { platforms: [], channels: [], warehouses: [] } } }), { status: 403 });
    for (const surface of ["ai_chat", "ai_agent", "dingtalk_chat"] as const)
      await assert.rejects(handler(fields, { ...base, surface }), { status: 403 });
  });
}

test("market GET signer permits only exact fixed metadata/continuation paths and empty bodies", async () => {
  const input = { secret, principal: admin, method: "GET", rawQuery: "a=1", timestamp: 1, requestId: "test", bodySha256: EMPTY_SHA256 };
  for (const path of ["/api/market/analysis-options", "/api/market/analysis-records/continuation"])
    assert.ok((await createMarketGatewayAuthHeaders({ ...input, path })).has("x-teruisi-signature"));
  for (const path of ["/api/market/queries", "/api/market/analysis-records/continuation/", "/api/sales/analysis-records/continuation"])
    await assert.rejects(createMarketGatewayAuthHeaders({ ...input, path }));
  await assert.rejects(createMarketGatewayAuthHeaders({ ...input, path: "/api/market/analysis-records/continuation", bodySha256: "a".repeat(64) }));
});
