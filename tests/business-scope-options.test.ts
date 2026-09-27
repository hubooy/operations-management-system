import test from "node:test";
import assert from "node:assert/strict";
import { scopeOptionsCanonical, scopeOptionsHash, scopeOptionsPrincipalKey, scopeOptionsQuery,
  validateNetshopOptionsResponse, scopeOptionSelection, mergeNetshopOption, type NetshopOptionsPage, type NetshopOption, type ScopeShop } from "../lib/ai/business-scope-options";
const key = "a".repeat(64), revision = "7:abcdef123456";
async function option(shop = "精确店", dataset: "promotion" | "master" = "promotion"): Promise<NetshopOption> {
  const identity = { platform: "京东", shop, dataset };
  return { optionKey: await scopeOptionsHash({ domain: "netshop", identity }), identity,
    source: dataset === "master" ? "jd_product_master" : "jd_promotion", sourceDataset: dataset === "master" ? "product_master" : "ad",
    dateMetadata: { kind: "published_import_envelope", firstDate: dataset === "master" ? null : "2026-07-01", lastDate: dataset === "master" ? null : "2026-09-01", snapshotDate: null, coverageVerified: false },
    provenance: { kind: "completed_import_metadata", revision, meaning: "historically_published_not_current_fact_coverage" } };
}
async function wrapper(items?: NetshopOption[]) {
  items ??= [await option()];
  const page: NetshopOptionsPage = { schemaVersion: "business-analysis-options-v1", domain: "netshop", revision, query: {}, queryDigest: await scopeOptionsHash({}), items,
    pagination: { returned: items.length, limit: 20, hasMore: false, nextCursor: null }, pageDigest: "", limitations: ["仅历史元数据，不证明完整覆盖。"] };
  await resign(page); return { schemaVersion: "business-netshop-options-response-v1", principalKey: key, page };
}
async function resign(page: NetshopOptionsPage) { const data: Record<string, unknown> = { ...page }; delete data.pageDigest; page.pageDigest = await scopeOptionsHash(data); }
const expected = { principalKey: key, query: {} };

test("principal SHA matches independently computed Python canonical fixtures", async () => {
  for (const [email, digest] of [["Admin@Example.TEST", "e32fbcc319df8959427256eceee63dba3c30f825248dee75b1e331ff321adaee"], ["混合User@example.test", "66766d737226cc4066dd143bfd3c7d9a6f4e1b54a62105d97e8ca1b24c46bd92"], ["İTEST@example.test", "ba322b90be0bffb3301260b03905292fe65a162150fd76ea5127d2200b21e59c"]]) assert.equal(await scopeOptionsPrincipalKey(email), digest);
});
test("complete valid page copied; explicit selection is independent and retains historical-only metadata", async () => {
  const raw = await wrapper(), page = await validateNetshopOptionsResponse(raw, expected);
  const selection = scopeOptionSelection(page, page.items[0], key);
  selection.identity.shop = "changed"; raw.page.items[0].identity.shop = "also changed";
  assert.equal(page.items[0].identity.shop, "精确店"); assert.equal(page.items[0].dateMetadata.coverageVerified, false);
  assert.throws(() => scopeOptionSelection(page, { ...page.items[0], source: "fake" }, key));
});
test("bad account, query, digest, version or extra fields fail closed", async () => {
  const value = await wrapper();
  for (const changed of [{ ...value, principalKey: "b".repeat(64) }, { ...value, extra: true }, { ...value, page: { ...value.page, pageDigest: "b".repeat(64) } }]) await assert.rejects(validateNetshopOptionsResponse(changed, expected));
  await assert.rejects(validateNetshopOptionsResponse(value, { ...expected, query: { platform: "京东" } }));
  await assert.rejects(validateNetshopOptionsResponse(value, { ...expected, revision: "8:abcdef123456" }));
  value.page.query = { platform: "京东" }; await resign(value.page);
  await assert.rejects(validateNetshopOptionsResponse(value, { ...expected, query: value.page.query }));
});
test("rehashed lies in source, identity, dates and coverage remain invalid", async () => {
  const mutations: ((item: NetshopOption) => void)[] = [i => { i.source = "secret"; }, i => { i.identity.shop = "other"; },
    i => { i.dateMetadata.firstDate = "2026-02-30"; }, i => { i.dateMetadata.firstDate = "2027-01-01"; },
    i => { (i.dateMetadata as unknown as Record<string, unknown>).coverageVerified = true; }, i => { i.provenance.revision = "9:abcdef123456"; }];
  for (const mutate of mutations) { const raw = await wrapper(); mutate(raw.page.items[0]); await resign(raw.page); await assert.rejects(validateNetshopOptionsResponse(raw, expected)); }
  const master = await wrapper([await option("店", "master")]); master.page.items[0].dateMetadata.firstDate = "2026-01-01"; await resign(master.page); await assert.rejects(validateNetshopOptionsResponse(master, expected));
});
test("20 rows continuation, strict cursor, unique ordering, and preceding-page boundary", async () => {
  const raw = await wrapper(await Promise.all(Array.from({ length: 20 }, (_, i) => option(`店${String(i).padStart(2,"0")}`))));
  raw.page.pagination.hasMore = true; raw.page.pagination.nextCursor = "abc:1abc:signature"; await resign(raw.page);
  await validateNetshopOptionsResponse(raw, expected);
  await assert.rejects(validateNetshopOptionsResponse(raw, { ...expected, cursor: raw.page.pagination.nextCursor }));
  const next = await wrapper([await option("店20")]); await validateNetshopOptionsResponse(next, { ...expected, revision, cursor: raw.page.pagination.nextCursor, previousItem: raw.page.items[19] });
  await assert.rejects(validateNetshopOptionsResponse(next, { ...expected, previousItem: next.page.items[0] }));
  for (const token of ["", "../path", "a".repeat(4097)]) { raw.page.pagination.nextCursor = token; if (token.length <= 4096) await resign(raw.page); await assert.rejects(validateNetshopOptionsResponse(raw, expected)); }
  const duplicate = await wrapper([await option(), await option()]); await assert.rejects(validateNetshopOptionsResponse(duplicate, expected));
  const reverse = await wrapper([await option("𐀀店"), await option("\ue000店")]); await assert.rejects(validateNetshopOptionsResponse(reverse, expected));
  reverse.page.items.reverse(); await resign(reverse.page); await validateNetshopOptionsResponse(reverse, expected);
});
test("snapshot before asynchronous hashes prevents input aliases changing returned scope", async () => {
  const raw = await wrapper(), context = { principalKey: key, query: {} };
  const promise = validateNetshopOptionsResponse(raw, context); raw.page.items[0].identity.shop = "mutated"; context.principalKey = "b".repeat(64);
  assert.equal((await promise).items[0].identity.shop, "精确店");
});
test("malicious depth, cycles, prototype, surrogates and UTF8 bytes are bounded", async () => {
  const cycle: unknown[] = []; cycle.push(cycle); assert.throws(() => scopeOptionsCanonical(cycle));
  assert.throws(() => scopeOptionsCanonical(Object.create({ custom: true })));
  assert.throws(() => scopeOptionsCanonical({ text: "\ud800" }));
  const raw = await wrapper(await Promise.all(Array.from({ length: 20 }, (_, i) => option("中".repeat(98)+String(i).padStart(2,"0")))));
  raw.page.limitations = Array.from({ length: 10 }, () => "中".repeat(1000)); await resign(raw.page);
  assert.ok(new TextEncoder().encode(scopeOptionsCanonical(raw.page)).length > 38000);
  assert.ok(scopeOptionsCanonical(raw.page).length < 38000);
  await assert.rejects(validateNetshopOptionsResponse(raw, expected), /完整来源选项页超过容量/);
});
test("queries retain exact identities, reject unsupported datasets and controls", () => {
  const query = { platform: "京东", shop: "精确店", dataset: "b2b" as const }; assert.deepEqual(scopeOptionsQuery(query), query);
  for (const raw of [{ shop: "店" }, { platform: "天猫", dataset: "sku" }, { q: " 店" }, { q: "店\x7f" }, { q: "店\u0085" }, { unknown: "x" }, { q: "" }]) assert.throws(() => scopeOptionsQuery(raw));
});
test("pure merge appends only selected dataset, preserves ERP channels, and replaces only untouched placeholder", () => {
  const selected = { platform: "京东", shop: "精确店", dataset: "sku" as const };
  assert.deepEqual(mergeNetshopOption([{ platform: "京东", shop: "", datasets: ["promotion", "master"], salesChannels: [] }], selected), [{ platform: "京东", shop: "精确店", datasets: ["sku"], salesChannels: [] }]);
  const shops: ScopeShop[] = [{ platform: "京东", shop: "精确店", datasets: ["promotion"], salesChannels: ["ERP原渠道"] }];
  const merged = mergeNetshopOption(shops, selected); assert.deepEqual(merged[0].datasets, ["promotion", "sku"]); assert.deepEqual(merged[0].salesChannels, ["ERP原渠道"]); assert.deepEqual(shops[0].datasets, ["promotion"]);
  assert.deepEqual(mergeNetshopOption(merged, selected), merged);
  assert.equal(mergeNetshopOption(shops, { ...selected, platform: "天猫", dataset: "spu" }).length, 2);
  assert.equal(mergeNetshopOption([{ platform: "京东", shop: "", datasets: ["sku"], salesChannels: [] }], selected).length, 2);
  assert.throws(() => mergeNetshopOption(Array.from({ length: 4 }, (_, i) => ({ platform: "京东", shop: `店${i}`, datasets: [], salesChannels: [] })), selected));
});
test("100 supplementary Unicode characters remain mergeable without UTF16 length confusion", () => {
  const selected = { platform: "京东", shop: "𐀀".repeat(100), dataset: "sku" as const };
  const first = mergeNetshopOption([], selected); first[0].salesChannels = ["𐀀".repeat(100)];
  const second = mergeNetshopOption(first, { ...selected, dataset: "spu" });
  assert.deepEqual(second[0].datasets, ["sku", "spu"]); assert.equal(second[0].salesChannels[0], "𐀀".repeat(100));
  assert.throws(() => mergeNetshopOption(first, { ...selected, shop: "𐀀".repeat(101) }));
});
