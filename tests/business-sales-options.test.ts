import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { scopeOptionsHash } from "../lib/ai/business-scope-options";
import { salesOptionsQuery, validateSalesOptionsResponse, salesOptionSelection, mergeSalesOption, mergeSalesOptionRequest,
  type SalesOptionIdentity, type SalesOptionsPage, type SalesOptionsQuery } from "../lib/ai/business-sales-options";

const principalKey = "a".repeat(64), gen = "b".repeat(32), directory = "c".repeat(64), rev = "7:9";
const base: SalesOptionIdentity = { platform: "京东", shop: "商用店", channel: "B端直营" };
async function sign(value: SalesOptionsPage) {
  const material: Partial<SalesOptionsPage> = { ...value }; delete material.pageDigest;
  value.pageDigest = await scopeOptionsHash(material); return value;
}
async function page(identities: SalesOptionIdentity[] = [base], query: SalesOptionsQuery = {}, more = false): Promise<SalesOptionsPage> {
  return sign({ schemaVersion: "business-analysis-options-v1", domain: "sales", authorityVerified: true,
    revision: rev, directoryGeneration: gen, directoryDigest: directory, query, queryDigest: await scopeOptionsHash(query),
    items: await Promise.all(identities.map(async identity => ({ optionKey: await scopeOptionsHash({ domain: "sales", identity }), identity: { ...identity },
      source: "erp_sales" as const, sourceDataset: "sales_order_lines" as const,
      dateMetadata: { kind: "current_fact_business_date_envelope" as const, firstDate: "2024-02-29", lastDate: "2026-09-01", snapshotDate: null, coverageVerified: false as const },
      provenance: { kind: "current_exact_business_projection" as const, revision: rev, meaning: "observed_current_identity_not_complete_period_coverage" as const } }))),
    pagination: { returned: identities.length, limit: 20, hasMore: more, nextCursor: more ? ".data:ABC:signature" : null },
    limitations: ["当前业务日包络不代表连续覆盖。"], pageDigest: "" });
}
function envelope(page: SalesOptionsPage) { return { schemaVersion: "business-sales-options-response-v1", principalKey, page }; }
function expected(query: SalesOptionsQuery = {}) { return { principalKey, query }; }
async function invalid(change: (value: SalesOptionsPage) => void) {
  const value = await page(); change(value); await sign(value);
  await assert.rejects(validateSalesOptionsResponse(envelope(value), expected()));
}
async function choice(identity = base) {
  const valid = await validateSalesOptionsResponse(envelope(await page([identity])), expected());
  return salesOptionSelection(valid, valid.items[0], principalKey);
}

test("ERP exact page validates as an isolated copy with owning-only provenance", async () => {
  const value = await page(); const result = await validateSalesOptionsResponse(envelope(value), expected());
  assert.deepEqual(result, value); assert.notEqual(result, value);
  result.items[0].identity.shop = "changed";
  assert.equal(value.items[0].identity.shop, base.shop);
});
test("actual Python contract SHA and Unicode exact identities match TS", async () => {
  const script = String.raw`
import sys,json
sys.path.insert(0,"backend")
from sales import analysis_options_contract as c
identities=json.loads(sys.stdin.buffer.read().decode("utf-8"))
pages=[]
for identity in identities:
    group={"platform":identity["platform"],"platform_key":identity["platform"],"shop_name":identity["shop"],"shop_key":identity["shop"],"channel":identity["channel"],"channel_key":identity["channel"],"firstDate":"2024-02-29","lastDate":"2026-09-01","rowCount":1}
    p=c.make_page([group],query=identity,revision="7:9",has_more=False,next_cursor=None)
    p.update(authorityVerified=True,directoryGeneration="b"*32,directoryDigest="c"*64)
    p.pop("pageDigest")
    p["pageDigest"]=c.digest(p)
    pages.append(p)
print(json.dumps(pages,ensure_ascii=True))
`;
  const inputs = [base, { platform: "线下", shop: "𠮷商品", channel: "\uFEFF原始" }, { platform: "自建", shop: "İstanbul", channel: "Straße" }];
  const run = spawnSync(path.resolve(".runtime/test-venv/Scripts/python.exe"), ["-c", script], { input: JSON.stringify(inputs), encoding: "utf8", timeout: 15000 });
  assert.equal(run.status, 0, run.stderr);
  const pages = JSON.parse(run.stdout) as SalesOptionsPage[];
  for (let i = 0; i < inputs.length; i++) assert.deepEqual(await validateSalesOptionsResponse(envelope(pages[i]), expected(inputs[i])), pages[i]);
});
test("queries retain arbitrary exact platforms but reject aliases empty fields q and shop without platform", async () => {
  const query = { ...base, platform: "任意精确平台" };
  await validateSalesOptionsResponse(envelope(await page([query], query)), expected(query));
  assert.deepEqual(salesOptionsQuery({ channel: "仅渠道" }), { channel: "仅渠道" });
  for (const bad of [{ shop: "独立店铺" }, { q: "查询" }, { platform: " x" }, { channel: "" }, { channel: "\n" }, { channel: "x".repeat(201) }, { platform: 1 }])
    assert.throws(() => salesOptionsQuery(bad));
  await assert.rejects(validateSalesOptionsResponse(envelope(await page()), expected({ platform: "JD" })));
  const filtered = await page([base], { platform: "JD" });
  await assert.rejects(validateSalesOptionsResponse(envelope(filtered), expected({ platform: "JD" })));
});
test("schema source authority and provenance are strict even after recomputing SHA", async () => {
  for (const change of [
    (p: SalesOptionsPage) => Object.assign(p, { authorityVerified: false }),
    (p: SalesOptionsPage) => Object.assign(p, { domain: "market" }),
    (p: SalesOptionsPage) => Object.assign(p, { extra: true }),
    (p: SalesOptionsPage) => Object.assign(p.items[0], { source: "jd_b2b" }),
    (p: SalesOptionsPage) => Object.assign(p.items[0].provenance, { directoryDigest: directory }),
    (p: SalesOptionsPage) => Object.assign(p.items[0].provenance, { meaning: "historically_published_not_current_fact_coverage" }),
  ]) await invalid(change);
});
test("independent page query option and revision bindings reject tampering", async () => {
  const value = await page(); value.items[0].dateMetadata.lastDate = "2026-09-02";
  await assert.rejects(validateSalesOptionsResponse(envelope(value), expected()));
  await invalid(p => { p.queryDigest = "d".repeat(64); });
  await invalid(p => { p.items[0].optionKey = "d".repeat(64); });
  await invalid(p => { p.items[0].provenance.revision = "7:8"; });
  for (const revision of ["07:9", "7:abcdef123456", "9007199254740992:9", "7:-1", "7:9:1"]) await invalid(p => { p.revision = revision; });
});
test("business dates cannot claim snapshot or complete period coverage", async () => {
  for (const change of [
    (p: SalesOptionsPage) => { p.items[0].dateMetadata.firstDate = "2023-02-29"; },
    (p: SalesOptionsPage) => { p.items[0].dateMetadata.lastDate = "2020-01-01"; },
    (p: SalesOptionsPage) => Object.assign(p.items[0].dateMetadata, { coverageVerified: true }),
    (p: SalesOptionsPage) => Object.assign(p.items[0].dateMetadata, { snapshotDate: "2026-09-01" }),
    (p: SalesOptionsPage) => Object.assign(p.items[0].dateMetadata, { kind: "published_import_envelope" }),
  ]) await invalid(change);
});
test("C collation uses Unicode code points and rejects duplicate or backwards keys", async () => {
  const identities = ["\uE000", "𠀀"].map(shop => ({ ...base, shop }));
  await validateSalesOptionsResponse(envelope(await page(identities)), expected());
  for (const identities of [[base, base], [{ ...base, channel: "B" }, { ...base, channel: "A" }]])
    await assert.rejects(validateSalesOptionsResponse(envelope(await page(identities)), expected()));
  await assert.rejects(validateSalesOptionsResponse(envelope(await page([...identities].reverse())), expected()));
});
test("continuation pins account query revision directory generation and previous identity", async () => {
  const first = await page(Array.from({ length: 20 }, (_, i) => ({ ...base, shop: `S${String(i).padStart(3, "0")}` })), {}, true);
  const next = await page([{ ...base, shop: "S020" }]);
  const fixed = { ...expected(), cursor: first.pagination.nextCursor, revision: rev, directoryGeneration: gen, directoryDigest: directory, previousItem: first.items.at(-1)! };
  await validateSalesOptionsResponse(envelope(next), fixed);
  for (const change of [{ principalKey: "d".repeat(64) }, { revision: "7:10" }, { directoryGeneration: "d".repeat(32) }, { directoryDigest: "d".repeat(64) },
    { previousItem: next.items[0] }, { previousItem: undefined }, { query: { channel: "other" } }])
    await assert.rejects(validateSalesOptionsResponse(envelope(next), { ...fixed, ...change }));
  await assert.rejects(validateSalesOptionsResponse(envelope(next), { ...expected(), cursor: first.pagination.nextCursor }));
});
test("pagination and full UTF8 capacity are checked without trimming records", async () => {
  await invalid(p => { p.pagination.hasMore = true; p.pagination.nextCursor = "data:ABC:sig"; });
  await invalid(p => { p.pagination.nextCursor = "data:ABC:sig"; });
  await invalid(p => Object.assign(p.pagination, { returned: true }));
  await invalid(p => Object.assign(p.pagination, { limit: 21 }));
  await validateSalesOptionsResponse(envelope(await page([])), expected());
  const identities = Array.from({ length: 21 }, (_, i) => ({ ...base, channel: `C${String(i).padStart(3, "0")}` }));
  await assert.rejects(validateSalesOptionsResponse(envelope(await page(identities)), expected()));
  const long = await page(Array.from({ length: 20 }, (_, i) => ({ platform: "𠀀".repeat(200), shop: "𠀀".repeat(200), channel: "𠀀".repeat(197) + String(i).padStart(3, "0") })));
  assert.ok(new TextEncoder().encode(JSON.stringify(long)).length > 38000);
  await assert.rejects(validateSalesOptionsResponse(envelope(long), expected()));
});
test("getters and asynchronous mutation cannot alter validated snapshots", async () => {
  const getter = Object.defineProperty({}, "platform", { enumerable: true, get() { throw new Error("getter executed"); } });
  assert.throws(() => salesOptionsQuery(getter), error => error instanceof Error && !error.message.includes("getter executed"));
  const value = await page(), fixed = expected();
  const pending = validateSalesOptionsResponse(envelope(value), fixed);
  value.items[0].identity.shop = "late"; fixed.query.channel = "late";
  const valid = await pending;
  assert.equal(valid.items[0].identity.shop, base.shop); assert.deepEqual(valid.query, {});
});
test("selection and merge require validated unchanged page and exact current-account choice", async () => {
  const raw = await page();
  assert.throws(() => salesOptionSelection(raw, raw.items[0], principalKey));
  const valid = await validateSalesOptionsResponse(envelope(raw), expected());
  const selection = salesOptionSelection(valid, valid.items[0], principalKey);
  assert.throws(() => salesOptionSelection(valid, valid.items[0], "d".repeat(64)));
  assert.throws(() => mergeSalesOption([], structuredClone(selection), principalKey));
  assert.throws(() => mergeSalesOption([], selection, "d".repeat(64)));
  valid.items[0].identity.channel = "changed";
  assert.throws(() => salesOptionSelection(valid, valid.items[0], principalKey));
  // Selection retains a detached identity even if the original page later changes.
  assert.equal(selection.identity.channel, base.channel);
  selection.identity.channel = "changed";
  assert.throws(() => mergeSalesOption([], selection, principalKey));
});
test("ERP merge adds only explicit channel and preserves other form data including budgets", async () => {
  const selection = await choice();
  const original = { question: "问题", startDate: "2026-09-01", endDate: "2026-09-17", windows: ["current", "yearAgo"],
    markets: [{ category: "原有" }], budget: { money: 1000 }, shops: [{ platform: base.platform, shop: base.shop, datasets: ["sku", "master"], salesChannels: ["原有", ""] }] };
  const before = structuredClone(original);
  const result = mergeSalesOptionRequest(original, selection, principalKey);
  assert.deepEqual(original, before);
  assert.deepEqual(result, { ...before, shops: [{ ...before.shops[0], salesChannels: ["原有", "", base.channel] }] });
  assert.equal(result.budget, original.budget);
  assert.deepEqual(mergeSalesOption(result.shops, selection, principalKey), result.shops);
  assert.deepEqual(mergeSalesOption([], selection, principalKey), [{ platform: base.platform, shop: base.shop, datasets: [], salesChannels: [base.channel] }]);
});
test("pristine placeholder may be replaced but unfinished manual shops stay intact", async () => {
  const selection = await choice();
  const pristine = [{ platform: "京东", shop: "", datasets: ["promotion", "master"], salesChannels: [] }];
  assert.equal(mergeSalesOption(pristine, selection, principalKey).length, 1);
  const manual = [{ ...pristine[0], salesChannels: ["未完成"] }];
  assert.deepEqual(mergeSalesOption(manual, selection, principalKey)[0], manual[0]);
});
test("100-character planner limit rejects complete 101-200 character source identity without truncation", async () => {
  for (const key of ["platform", "shop", "channel"] as const) {
    const selection = await choice({ ...base, [key]: "𠀀".repeat(101) });
    assert.throws(() => mergeSalesOption([], selection, principalKey), /100字符/);
    assert.equal([...selection.identity[key]].length, 101);
    assert.equal(mergeSalesOption([], await choice({ ...base, [key]: "𠀀".repeat(100) }), principalKey).length, 1);
  }
});
test("merge refuses shop channel bounds and ambiguous duplicates without shrinking scope", async () => {
  const selection = await choice();
  const shops = Array.from({ length: 4 }, (_, i) => ({ platform: "京东", shop: `S${i}`, datasets: [], salesChannels: [] }));
  assert.throws(() => mergeSalesOption(shops, selection, principalKey), /4家/);
  const full = [{ platform: base.platform, shop: base.shop, datasets: [], salesChannels: Array.from({ length: 10 }, (_, i) => `C${i}`) }];
  assert.throws(() => mergeSalesOption(full, selection, principalKey), /10个/);
  assert.throws(() => mergeSalesOption([shops[0], shops[0]], selection, principalKey), /重复/);
  assert.throws(() => mergeSalesOption([{ ...full[0], salesChannels: ["重复", "重复"] }], selection, principalKey));
});
