import assert from "node:assert/strict";
import test from "node:test";
import { readMarketJson } from "../lib/market/read-request";

test("market GET preserves range errors without retrying", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    calls++;
    assert.equal(init.method, undefined);
    return Response.json({ error: "市场分析范围过大，请缩小日期或筛选范围" }, { status: 413 });
  });
  await assert.rejects(readMarketJson("/api/market/overview"), /范围过大/);
  assert.equal(calls, 1);
});

test("market GET deadline includes a stalled response body and clears the request", async (t) => {
  let aborted = false;
  t.mock.method(globalThis, "fetch", async (_url, init) => ({
    ok: true,
    json: () => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => {
      aborted = true;
      reject(new DOMException("Aborted", "AbortError"));
    })),
  }));
  await assert.rejects(readMarketJson("/api/market/overview", undefined, 15), /读取超时/);
  assert.equal(aborted, true);
});

test("a cancelled filter read cannot accept a late successful body", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, json: async () => {
    controller.abort();
    return { items: ["stale"] };
  } }));
  await assert.rejects(readMarketJson("/api/market/overview", controller.signal), { name: "AbortError" });
});
