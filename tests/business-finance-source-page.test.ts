import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import { readBusinessFinanceSourcePage } from "../lib/ai/business-finance-source-page";
import type { AiToolExecutionContext } from "../lib/ai/tool-registry-contract";
import { PublicApiError } from "../lib/http/api-error";

const principal: AppPrincipal = { email: "finance-admin@example.test", displayName: "管理员", role: "admin", scope: null };
const context: AiToolExecutionContext = { principal, surface: "business_collection", requestId: "finance-bridge" };
const secret = "finance-evidence-bridge-secret-at-least-32-bytes";
const config = { readerBaseUrl: "http://127.0.0.1:8011", writerBaseUrl: "http://127.0.0.1:8012",
  internalSecret: secret, timeoutMs: 2_000, maxRequestBytes: 8192, maxResponseBytes: 38_000 };
const args = { query: { months: ["2026-08"], scope: { scope_key: "business", scope_type: "business",
  scope_name: "志高事业部", group_name: "" }, analysisPeriod: { startDate: "2026-08-01", endDate: "2026-08-31" } },
  offset: 0, afterId: 0 };
const revision = `2:${"b".repeat(64)}`;
const page = { schemaVersion: "business-finance-owned-page-v1", sourceRef: "a".repeat(64),
  sourceRevision: revision, query: args.query, rows: [], pageEvidence: { rowCount: 0, sha256: "c".repeat(64) },
  pagination: { offset: 0, returned: 0, total: 0, nextOffset: null, nextLastId: null },
  sourceAuthorityVerified: false, persistentEvidenceVerified: false, pageDigest: "d".repeat(64) };

test("internal finance bridge signs only its fixed reader POST and preserves full revision", async () => {
  let observed: Request | undefined;
  const actual = await readBusinessFinanceSourcePage(args, context, { config, now: () => 1_800_000_000_000,
    requestId: () => "finance-source-page", fetchImpl: async (input, init) => {
      observed = new Request(input, init);
      return Response.json(page, { headers: { "x-finance-data-revision": `2:${"b".repeat(12)}` } });
    } });
  assert.deepEqual(actual, page);
  assert.ok(observed);
  assert.equal(observed.method, "POST");
  assert.equal(new URL(observed.url).pathname, "/api/finance/business-evidence/page");
  assert.equal(new URL(observed.url).origin, "http://127.0.0.1:8011");
  assert.deepEqual(await observed.json(), args);
  const envelope = observed.headers.get("x-teruisi-principal")!;
  const bodyHash = observed.headers.get("x-teruisi-content-sha256")!;
  const signed = ["v1", "1800000000", "finance-source-page", "POST", "/api/finance/business-evidence/page",
    "", bodyHash, envelope].join("\n");
  assert.equal(observed.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(signed).digest("hex")}`);
});

test("finance bridge rejects scoped identity, wrong surface and malformed inputs before transport", async () => {
  const neverFetch: typeof fetch = async () => assert.fail("transport must not run");
  for (const [input, ctx] of [
    [args, { ...context, principal: { ...principal, scope: {} } }],
    [args, { ...context, surface: "ai_agent" }],
    [{ ...args, extra: true }, context],
    [{ ...args, offset: 1, afterId: 1 }, context],
    [{ ...args, query: { ...args.query, financeAuthority: true } }, context],
  ] as Array<[unknown, AiToolExecutionContext]>) {
    await assert.rejects(readBusinessFinanceSourcePage(input, ctx, { config, fetchImpl: neverFetch }),
      (error: unknown) => error instanceof PublicApiError);
  }
});

test("finance bridge refuses revision, scope and response-capacity mismatches", async () => {
  const options = { config, fetchImpl: async () => Response.json({ ...page, sourceRevision: `3:${"b".repeat(64)}` },
    { headers: { "x-finance-data-revision": `2:${"b".repeat(12)}` } }) };
  await assert.rejects(readBusinessFinanceSourcePage(args, context, options),
    (error: unknown) => error instanceof PublicApiError && error.status === 409);
  await assert.rejects(readBusinessFinanceSourcePage(args, context, { config,
    fetchImpl: async () => Response.json({ ...page, query: { ...args.query, scope: { ...args.query.scope, group_name: "另一组" } } },
      { headers: { "x-finance-data-revision": `2:${"b".repeat(12)}` } }),
  }), (error: unknown) => error instanceof PublicApiError && error.status === 409);
  const tiny = { ...config, maxResponseBytes: 100 };
  await assert.rejects(readBusinessFinanceSourcePage(args, context, { config: tiny,
    fetchImpl: async () => Response.json(page, { headers: { "x-finance-data-revision": `2:${"b".repeat(12)}` } }),
  }), (error: unknown) => error instanceof PublicApiError && error.status === 503);
});
