import assert from "node:assert/strict";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import { readDjangoFinanceConsumer } from "../lib/django/finance-consumer-reader";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = {
  email: "admin@example.test",
  displayName: "管理员",
  role: "admin",
  scope: null,
};
const config = {
  readerBaseUrl: "http://127.0.0.1:8011",
  writerBaseUrl: "http://127.0.0.1:8012",
  internalSecret: "finance-consumer-reader-secret-at-least-32-bytes",
  timeoutMs: 2_000,
  maxRequestBytes: 64 * 1024,
  maxResponseBytes: 1024 * 1024,
};

test("finance consumer uses only the fixed reader POST endpoint and verifies revision", async () => {
  let observed: Request | null = null;
  const result = await readDjangoFinanceConsumer(
    principal,
    { operation: "line_search", query: "销售费用", offset: 0, limit: 2 },
    {
      config,
      requestId: () => "finance-consumer-1",
      now: () => 1_800_000_000_000,
      fetchImpl: async (input, init) => {
        observed = new Request(input, init);
        return Response.json({
          operation: "line_search",
          data: { items: [], total: 0, truncated: false },
        }, { headers: { "x-finance-data-revision": "2:abcdef123456" } });
      },
    },
  );
  const request = observed as Request | null;
  assert.ok(request);
  assert.equal(request.method, "POST");
  assert.equal(new URL(request.url).pathname, "/api/finance/consumers/query");
  assert.equal(new URL(request.url).origin, "http://127.0.0.1:8011");
  assert.deepEqual(await request.json(), {
    operation: "line_search", query: "销售费用", offset: 0, limit: 2,
  });
  assert.equal(result.revision, "2:abcdef123456");
  assert.deepEqual(result.data, { items: [], total: 0, truncated: false });
});

test("finance consumer rejects unbounded input and mismatched response before use", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("invalid input must fail before fetch");
  await assert.rejects(
    readDjangoFinanceConsumer(
      principal,
      { operation: "line_search", query: "A", offset: 0, limit: 2 },
      { config, fetchImpl: neverFetch },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
  await assert.rejects(
    readDjangoFinanceConsumer(
      principal,
      { operation: "target_search", query: "目标", offset: 0, limit: 2 },
      {
        config,
        fetchImpl: async () => Response.json({
          operation: "line_search",
          data: { items: [], total: 0, truncated: false },
        }, { headers: { "x-finance-data-revision": "2:abcdef123456" } }),
      },
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});
