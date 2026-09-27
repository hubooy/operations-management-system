import assert from "node:assert/strict";
import test from "node:test";
import { isInternalV3SourceReadPath, isPublicAiPath, requestDjangoAi } from "../lib/django/ai-service";
import { readBusinessV3DirectoryInternal, readBusinessV3PageInternal } from "../lib/ai/business-evidence";
import { PublicApiError } from "../lib/http/api-error";

const admin = { email: "v3-reader@example.test", displayName: "Reader", role: "admin" as const, scope: null };
const environment = {
  TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:55481/",
  TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:55482/",
  TERUISI_DJANGO_INTERNAL_SECRET: "v3-reader-contract-secret-at-least-32-bytes",
};
const intentId = "business-v3-intent-1";
const directoryPath = `/api/ai/business-v3-source-read/${intentId}/directory`;
const pagePath = `/api/ai/business-v3-source-read/${intentId}/pages/sales`;
const handle = `${"a".repeat(45)}.${"b".repeat(64)}`;

test("v3 signed source paths are internal writer POST only, never public proxy routes", async () => {
  assert.equal(isInternalV3SourceReadPath(directoryPath), true);
  assert.equal(isInternalV3SourceReadPath(pagePath), true);
  assert.equal(isPublicAiPath(directoryPath), false);
  assert.equal(isPublicAiPath(pagePath), false);
  for (const path of [directoryPath, pagePath]) {
    await assert.rejects(requestDjangoAi(admin, { path, method: "GET", service: "reader" }, { environment }));
    await assert.rejects(requestDjangoAi(admin, { path, method: "POST", service: "reader", payload: {} }, { environment }));
    await assert.rejects(requestDjangoAi(admin, { path, method: "POST", service: "writer", payload: {}, query: new URLSearchParams({ x: "1" }) }, { environment }));
  }
  assert.equal(isInternalV3SourceReadPath(`${pagePath}/extra`), false);
});

test("unregistered v3 adapter forwards signed bounded requests without public route or model", async () => {
  const prior = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  const previousFetch = globalThis.fetch;
  const calls: Request[] = [];
  Object.assign(process.env, environment);
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    calls.push(request);
    const sourceKey = request.url.includes("/pages/") ? "sales" : undefined;
    return Response.json({ schemaVersion: sourceKey ? "business-v3-source-slice-v1" : "business-v3-source-directory-v1",
      intentId, ...(sourceKey ? { sourceKey } : {}), readOnlyOperationOnWriterProcess: true,
      agentReadReceiptRecorded: false }, { headers: { "x-ai-revision": "0" } });
  };
  try {
    const directory = await readBusinessV3DirectoryInternal({ intentId, offset: 0 }, admin);
    const page = await readBusinessV3PageInternal({ intentId, sourceKey: "sales", sequence: 1, handle }, admin);
    assert.equal(directory.schemaVersion, "business-v3-source-directory-v1");
    assert.equal(page.schemaVersion, "business-v3-source-slice-v1");
    assert.equal(calls.length, 2);
    for (const call of calls) {
      assert.equal(call.method, "POST");
      assert.equal(new URL(call.url).port, "55482");
      assert.ok(call.headers.get("x-teruisi-signature")?.startsWith("v1="));
    }
    await assert.rejects(readBusinessV3PageInternal({ intentId, sourceKey: "finance", sequence: 1, handle: "forged" }, admin), PublicApiError);
    await assert.rejects(readBusinessV3DirectoryInternal({ intentId, offset: 1 }, admin), PublicApiError);
    await assert.rejects(readBusinessV3DirectoryInternal({ intentId, offset: 0, extra: "smuggle" }, admin), PublicApiError);
    assert.equal(calls.length, 2);
  } finally {
    globalThis.fetch = previousFetch;
    for (const [key, value] of Object.entries(prior)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});
