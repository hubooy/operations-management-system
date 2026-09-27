import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";

registerHooks({ resolve(specifier, context, nextResolve) { return specifier === "cloudflare:workers"
  ? { url: "data:text/javascript,export const env={};", shortCircuit: true } : nextResolve(specifier, context); } });
const { readBusinessMarketObservationV2 } = await import("../lib/ai/business-market-observation");
const { isPublicAiPath, isInternalMarketObservationPath, requestDjangoAi } = await import("../lib/django/ai-service");
const admin: AppPrincipal = { email: "market@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const base = { reportId: "report-1", currentSourceKey: "market-current", baselineSourceKey: "market-previous",
  currentObservationDate: "2026-08-03", baselineObservationDate: "2026-07-31" };
const path = "/api/ai/reports/report-1/market-observation";
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191",
  TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192",
  TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-market-observation-secret-32-bytes" };
const sha = (raw: string) => createHash("sha256").update(raw).digest("hex");
function isolated(t: TestContext) {
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  const original = globalThis.fetch; Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = original; for (const [key, value] of Object.entries(previous)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  } });
}
function payload(row = false) {
  return { schemaVersion: "business-market-observation-response-v2", bindingDigest: "a".repeat(64),
    responseDigest: "b".repeat(64),
    binding: { schemaVersion: "business-market-observation-binding-v2",
      reportBinding: { reportId: base.reportId }, currentSourceKey: base.currentSourceKey,
      baselineSourceKey: base.baselineSourceKey, currentObservationDate: base.currentObservationDate,
      baselineObservationDate: base.baselineObservationDate,
      algorithmVersion: "market-daily-observation-v2", tableBindingDigest: "c".repeat(64) },
    authority: { completeSourceTraversalForSelectedSources: true, reportBindingVerified: true,
      wholeMarketCoverageVerified: false, ownProductIdentityVerified: false,
      scopeMeaning: "two_selected_days_of_top_sample_not_whole_market" },
    ...(row ? { row: { rowIndex: 7, rowId: "d".repeat(64),
      status: "entered_observed_top_sample", baseline: { metrics: null } } }
      : { table: { algorithmVersion: "market-daily-observation-v2", view: "rank_entry_exit",
        observationDates: { current: base.currentObservationDate, baseline: base.baselineObservationDate },
        pagination: { offset: 0, returned: 1, total: 1, nextOffset: null },
        rows: [{ rowIndex: 0, rowId: "e".repeat(64), status: "entered_observed_top_sample" }] } }) };
}
const reply = (data: object, status = 200) => Response.json(data, { status, headers: { "x-ai-revision": "2" } });

test("internal market observation signs exact reader GET and preserves TOP absence", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(target.pathname, path); assert.equal(init?.method, "GET");
    assert.equal(init?.body, undefined); assert.equal(init?.cache, "no-store");
    const headers = new Headers(init?.headers);
    const canonical = ["v1", headers.get("x-teruisi-timestamp"), headers.get("x-teruisi-request-id"),
      "GET", target.pathname, target.search.slice(1), sha(""), headers.get("x-teruisi-principal")].join("\n");
    assert.equal(headers.get("x-teruisi-signature"), "v1="+createHmac("sha256",
      environment.TERUISI_DJANGO_INTERNAL_SECRET).update(canonical).digest("hex"));
    assert.equal(target.searchParams.get("currentSourceKey"), base.currentSourceKey);
    assert.equal(target.searchParams.get("baselineSourceKey"), base.baselineSourceKey);
    return reply(target.searchParams.has("rowIndex") ? payload(true) : payload());
  };
  const page = await readBusinessMarketObservationV2(base, admin);
  assert.deepEqual(page, payload());
  const row = await readBusinessMarketObservationV2({ ...base, rowIndex: 7, rowId: "d".repeat(64) }, admin);
  assert.deepEqual(row, payload(true));
  assert.equal(((row as Record<string, unknown>).row as { baseline: { metrics: null } }).baseline.metrics, null);
  assert.deepEqual(seen, [path, path]);
});

test("invalid actor, source, date, row modes and external paths never reach transport", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; return reply(payload()); };
  for (const invalid of [{ ...base, reportId: "../other" }, { ...base, currentSourceKey: base.baselineSourceKey },
    { ...base, currentObservationDate: "2026-02-30" }, { ...base, baselineObservationDate: "2026-07-30", offset: -1 },
    { ...base, rowIndex: 1 }, { ...base, rowId: "d".repeat(64) },
    { ...base, rowIndex: 7, rowId: "d".repeat(64), offset: 0 }, { ...base, limit: 10 },
    { ...base, extra: "x" }]) await assert.rejects(readBusinessMarketObservationV2(invalid, admin));
  await assert.rejects(readBusinessMarketObservationV2(base, { ...admin, role: "viewer" }));
  await assert.rejects(readBusinessMarketObservationV2(base, { ...admin,
    scope: { warehouses: [], channels: [], platforms: [] } }));
  assert.equal(isPublicAiPath(path), false);
  assert.equal(isInternalMarketObservationPath(path), true);
  for (const bad of [path+"/extra", path+"-fake", "/api/ai/reports/../market-observation"])
    assert.equal(isInternalMarketObservationPath(bad), false);
  const query = new URLSearchParams({ currentSourceKey: base.currentSourceKey });
  await assert.rejects(requestDjangoAi(admin, { path, method: "POST", service: "reader", query, payload: {} }));
  await assert.rejects(requestDjangoAi(admin, { path, method: "GET", service: "writer", query }));
  await assert.rejects(requestDjangoAi(admin, { path, method: "GET", query }));
  await assert.rejects(requestDjangoAi(admin, { path, method: "GET", service: "reader", query, payload: {} }));
  assert.equal(calls, 0);
});

test("adapter rejects false authority, wrong binding, malformed row and oversized response", async t => {
  isolated(t); let value: object = payload(); globalThis.fetch = async () => reply(value);
  for (const changed of [{ ...payload(), schemaVersion: "old" },
    { ...payload(), authority: { ...payload().authority, ownProductIdentityVerified: true } },
    { ...payload(), binding: { ...payload().binding, currentSourceKey: "other" } },
    { ...payload(), table: { ...(payload() as { table: Record<string, unknown> }).table, pagination: { offset: 1 } } },
    { ...payload(), padding: "界".repeat(15000) }]) {
    value = changed; await assert.rejects(readBusinessMarketObservationV2(base, admin));
  }
  value = payload(true);
  await assert.rejects(readBusinessMarketObservationV2({ ...base, rowIndex: 8, rowId: "d".repeat(64) }, admin));
});
