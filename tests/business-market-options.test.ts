import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { scopeOptionsHash } from "../lib/ai/business-scope-options";
import { marketOptionsPrincipalKey, marketOptionsQuery, validateMarketOptionsResponse, marketOptionSelection, mergeMarketOption,
  type MarketOptionIdentity, type MarketOptionsPage, type MarketOptionsQuery } from "../lib/ai/business-market-options";

const principalKey = "a".repeat(64), gen = "b".repeat(32), directory = "c".repeat(64), rev = "3:abcdef012345";
const base: MarketOptionIdentity = { platform: "京东", category: "切肉机", scope: "商品热销榜", rankingDimension: "SKU", priceBandFilter: "全部" };
async function page(identities: MarketOptionIdentity[] = [base], query: MarketOptionsQuery = {}, more = false): Promise<MarketOptionsPage> {
  const result: MarketOptionsPage = { schemaVersion: "business-analysis-options-v1", domain: "market", authorityVerified: true,
    revision: rev, directoryGeneration: gen, directoryDigest: directory, query, queryDigest: await scopeOptionsHash(query),
    items: await Promise.all(identities.map(async identity => ({ optionKey: await scopeOptionsHash({ domain: "market", identity }), identity: { ...identity },
      source: "market_daily_top" as const, sourceDataset: "market_daily_top" as const,
      dateMetadata: { kind: "published_import_envelope" as const, firstDate: "2024-02-29", lastDate: "2026-09-01", snapshotDate: null, coverageVerified: false as const },
      provenance: { kind: "completed_import_metadata" as const, revision: rev, generation: gen, directoryDigest: directory,
        meaning: "historically_published_not_current_fact_coverage" as const } }))),
    pagination: { returned: identities.length, limit: 20, hasMore: more, nextCursor: more ? ".data:ABC:signature" : null },
    limitations: ["历史包络不证明完整日期覆盖。"], pageDigest: "" };
  return sign(result);
}
async function sign(value: MarketOptionsPage) {
  const material: Partial<MarketOptionsPage> = { ...value }; delete material.pageDigest;
  value.pageDigest = await scopeOptionsHash(material); return value;
}
function envelope(page: MarketOptionsPage) { return { schemaVersion: "business-market-options-response-v1", principalKey, page }; }
function expected(query: MarketOptionsQuery = {}) { return { principalKey, query }; }
async function invalid(change: (value: MarketOptionsPage) => void, query: MarketOptionsQuery = {}) {
  const value = await page([base], query); change(value); await sign(value);
  await assert.rejects(validateMarketOptionsResponse(envelope(value), expected(query)));
}

test("owning market page and current-account envelope validate without changing old principal codec", async () => {
  const value = await page(); const result = await validateMarketOptionsResponse(envelope(value), expected());
  assert.deepEqual(result, value); assert.notEqual(result, value);
  result.items[0].identity.category = "copy changed"; assert.equal(value.items[0].identity.category, "切肉机");
  assert.equal(await marketOptionsPrincipalKey("Admin@example.test"), await scopeOptionsHash(["business-workbench", "admin@example.test"]));
});

test("actual Python contract hashes and Unicode casefold are accepted by the shared codec", async () => {
  const script = String.raw`
import sys,json
sys.path.insert(0,"backend")
from market import analysis_options_contract as c
inputs=json.loads(sys.stdin.buffer.read().decode("utf-8"))
pages=[]
for category,q in inputs:
    identity={"platform":"京东","category":category,"scope":"商品热销榜","rankingDimension":"SKU","priceBandFilter":"全部"}
    p=c.make_page([{"identity":identity,"firstDate":"2024-02-29","lastDate":"2026-09-01"}],revision="3:abcdef012345",generation="b"*32,directory_digest="c"*64,query={"q":q},has_more=False,next_cursor=None)
    p["authorityVerified"]=True
    p.pop("pageDigest")
    p["pageDigest"]=c._digest(p)
    pages.append(p)
print(json.dumps(pages,ensure_ascii=True))
`;
  const inputs = [["Straße", "STRASSE"], ["İstanbul", "i\u0307"], ["ﬃ", "FFI"], ["ΟΣ", "οσ"], ["\uFEFF原始", "原始"], ["𠮷商品", "𠮷"]];
  const process = spawnSync(path.resolve(".runtime/test-venv/Scripts/python.exe"), ["-c", script], {
    input: JSON.stringify(inputs), encoding: "utf8", timeout: 15000 });
  assert.equal(process.status, 0, process.stderr);
  const pages = JSON.parse(process.stdout) as MarketOptionsPage[];
  for (let i = 0; i < pages.length; i++) assert.deepEqual(await validateMarketOptionsResponse(envelope(pages[i]), expected({ q: inputs[i][1] })), pages[i]);
});

test("exact query filters and raw price labels never infer aliases", async () => {
  const query = { platform: "京东" as const, category: "切肉机", scope: "商品热销榜", rankingDimension: "SKU" as const, priceBandFilter: "全部" };
  await validateMarketOptionsResponse(envelope(await page([base], query)), expected(query));
  await assert.rejects(validateMarketOptionsResponse(envelope(await page([base], query)), expected({ ...query, priceBandFilter: "全价格带" })));
  for (const bad of [{ platform: "天猫" }, { rankingDimension: "sku" }, { category: " x" }, { scope: "" }, { q: "\n" }, { shop: "x" }, { q: "x".repeat(101) }])
    assert.throws(() => marketOptionsQuery(bad));
  assert.deepEqual(marketOptionsQuery({ category: "\uFEFF原始" }), { category: "\uFEFF原始" });
});

test("unknown fields and false owning authority are rejected even after fresh page SHA", async () => {
  await invalid(value => { Object.assign(value, { unexpected: true }); });
  await invalid(value => { Object.assign(value.items[0].identity, { shop: "x" }); });
  await invalid(value => { Object.assign(value.items[0].provenance, { extra: "x" }); });
  await invalid(value => { Object.assign(value, { authorityVerified: false }); });
  await invalid(value => { Object.assign(value.items[0], { source: "jd_promotion" }); });
  const value = envelope(await page()); Object.assign(value, { extra: "x" });
  await assert.rejects(validateMarketOptionsResponse(value, expected()));
});

test("full page SHA, option key and query digest are independently checked", async () => {
  const value = await page(); value.items[0].dateMetadata.lastDate = "2026-09-02";
  await assert.rejects(validateMarketOptionsResponse(envelope(value), expected()));
  await invalid(item => { item.items[0].optionKey = "d".repeat(64); });
  await invalid(item => { item.queryDigest = "d".repeat(64); });
  await invalid(item => { item.items[0].provenance.directoryDigest = "d".repeat(64); });
  await invalid(item => { item.items[0].provenance.generation = "d".repeat(32); });
});

test("date envelopes stay valid historical bounds with no snapshot or coverage claim", async () => {
  for (const mutate of [
    (p: MarketOptionsPage) => { p.items[0].dateMetadata.firstDate = "2023-02-29"; },
    (p: MarketOptionsPage) => { p.items[0].dateMetadata.lastDate = "2020-01-01"; },
    (p: MarketOptionsPage) => { Object.assign(p.items[0].dateMetadata, { firstDate: null }); },
    (p: MarketOptionsPage) => { Object.assign(p.items[0].dateMetadata, { coverageVerified: true }); },
    (p: MarketOptionsPage) => { Object.assign(p.items[0].dateMetadata, { snapshotDate: "2026-09-01" }); },
  ]) await invalid(mutate);
});

test("Python code-point identity order rejects duplicates and UTF16 order mistakes", async () => {
  const identities = ["\uE000", "𠀀"].map(category => ({ ...base, category }));
  await validateMarketOptionsResponse(envelope(await page(identities)), expected());
  await assert.rejects(validateMarketOptionsResponse(envelope(await page([...identities].reverse())), expected()));
  await assert.rejects(validateMarketOptionsResponse(envelope(await page([base, base])), expected()));
});

test("continuation pins account, query, revision, generation, digest and strict previous identity", async () => {
  const first = await page(Array.from({ length: 20 }, (_, i) => ({ ...base, category: `C${String(i).padStart(3, "0")}` })), {}, true);
  const next = await page([{ ...base, category: "C020" }]);
  const fixed = { ...expected(), cursor: first.pagination.nextCursor, revision: first.revision, directoryGeneration: first.directoryGeneration,
    directoryDigest: first.directoryDigest, previousItem: first.items.at(-1)! };
  await validateMarketOptionsResponse(envelope(next), fixed);
  for (const change of [{ principalKey: "d".repeat(64) }, { revision: "4:abcdef012345" }, { directoryGeneration: "d".repeat(32) },
    { directoryDigest: "d".repeat(64) }, { previousItem: next.items[0] }, { previousItem: undefined }])
    await assert.rejects(validateMarketOptionsResponse(envelope(next), { ...fixed, ...change }));
  await assert.rejects(validateMarketOptionsResponse(envelope(next), { ...expected(), cursor: first.pagination.nextCursor }));
});

test("pagination is exact twenty and a progressing bounded signed cursor", async () => {
  await invalid(value => { value.pagination.hasMore = true; value.pagination.nextCursor = "data:ABC:sig"; });
  await invalid(value => { value.pagination.nextCursor = "data:ABC:sig"; });
  await invalid(value => { Object.assign(value.pagination, { limit: true }); });
  await invalid(value => { Object.assign(value.pagination, { returned: true }); });
  const identities = Array.from({ length: 21 }, (_, i) => ({ ...base, category: `C${String(i).padStart(3, "0")}` }));
  await assert.rejects(validateMarketOptionsResponse(envelope(await page(identities)), expected()));
  const empty = await page([]); await validateMarketOptionsResponse(envelope(empty), expected());
  const largeCursor = await page(identities.slice(0, 20), {}, true); largeCursor.pagination.nextCursor = "x".repeat(1601); await sign(largeCursor);
  await assert.rejects(validateMarketOptionsResponse(envelope(largeCursor), expected()));
});

test("UTF8 38000 byte limit counts complete multibyte identities, never slices entries", async () => {
  const identities = Array.from({ length: 20 }, (_, i) => ({ ...base, category: "𠀀".repeat(197) + String(i).padStart(3, "0"),
    scope: "𠀀".repeat(200), priceBandFilter: "𠀀".repeat(200) }));
  const value = await page(identities);
  assert.ok(new TextEncoder().encode(JSON.stringify(value)).length > 38000);
  await assert.rejects(validateMarketOptionsResponse(envelope(value), expected()));
});

test("untrusted getters/deep types and async caller mutation cannot change the validated snapshot", async () => {
  const getter = Object.defineProperty({}, "platform", { enumerable: true, get() { throw new Error("getter executed"); } });
  assert.throws(() => marketOptionsQuery(getter), error => error instanceof Error && !error.message.includes("getter executed"));
  const cycle: Record<string, unknown> = {}; cycle.self = cycle;
  await assert.rejects(validateMarketOptionsResponse(cycle, expected()));
  const value = await page(); const fixed = expected(); const validation = validateMarketOptionsResponse(envelope(value), fixed);
  value.items[0].identity.category = "late changed"; fixed.query.q = "late";
  const validated = await validation; assert.equal(validated.items[0].identity.category, "切肉机"); assert.deepEqual(validated.query, {});
});

test("selection requires an unchanged current-account validated page and exact member", async () => {
  const raw = await page();
  assert.throws(() => marketOptionSelection(raw, raw.items[0], principalKey));
  const valid = await validateMarketOptionsResponse(envelope(raw), expected());
  const selection = marketOptionSelection(valid, valid.items[0], principalKey);
  assert.deepEqual(Object.keys(selection).sort(), ["principalKey", "revision", "directoryGeneration", "directoryDigest", "queryDigest", "optionKey", "identity"].sort());
  assert.throws(() => marketOptionSelection(valid, valid.items[0], "d".repeat(64)));
  const changed = structuredClone(valid.items[0]); changed.identity.category = "different";
  assert.throws(() => marketOptionSelection(valid, changed, principalKey));
  selection.identity.category = "independent copy"; assert.equal(valid.items[0].identity.category, "切肉机");
  valid.items[0].dateMetadata.lastDate = "2026-09-02";
  assert.throws(() => marketOptionSelection(valid, valid.items[0], principalKey));
});

test("market merge preserves exact manual fields, deduplicates only whole identity and never exceeds seven", async () => {
  const valid = await validateMarketOptionsResponse(envelope(await page()), expected());
  const selection = marketOptionSelection(valid, valid.items[0], principalKey);
  const incomplete = { platform: "", category: " 未完成类目 ", scope: "", rankingDimension: "", priceBandFilter: "" };
  const first = mergeMarketOption([incomplete], selection, principalKey);
  assert.deepEqual(first, [incomplete, base]); assert.notEqual(first[0], incomplete);
  assert.deepEqual(mergeMarketOption(first, selection, principalKey), first);
  const otherBand = { ...base, priceBandFilter: "全价格带" };
  assert.deepEqual(mergeMarketOption([otherBand], selection, principalKey), [otherBand, base]);
  const seven = Array.from({ length: 7 }, (_, i) => ({ ...base, category: `C${i}` }));
  assert.throws(() => mergeMarketOption(seven, selection, principalKey), /最多选择7项/);
  assert.throws(() => mergeMarketOption([base, base], selection, principalKey), /重复/);
  assert.throws(() => mergeMarketOption([], selection, "d".repeat(64)), /账号/);
  assert.throws(() => mergeMarketOption([], { ...selection, optionKey: "invalid" }, principalKey));
  assert.throws(() => mergeMarketOption([], { ...selection, identity: { ...base, scope: "" } }, principalKey));
});
