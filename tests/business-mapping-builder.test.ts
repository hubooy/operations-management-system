import test from "node:test";
import assert from "node:assert/strict";
import { mappingBindingKey, mappingCanonical, mappingContext, mappingHash, mappingPairs, mappingTextCompare, matchingMasters, readMappingDirectory,
  type BudgetRun, type MappingSource } from "../lib/ai/business-mapping-builder";

async function fixture(count = 4) {
  const query = { platform: "京东", shop: "合成精确店", startDate: "2026-08-01", endDate: "2026-08-31", window: "current" };
  const sources = Array.from({ length: count }, (_, i) => i === 0 ? { key: "master", domain: "netshop", query: { ...query, dataset: "master" } }
    : { key: "sales"+i, domain: "sales", query: { ...query, channel: "精确渠道"+i, window: ["current", "previous", "yearAgo"][(i-1)%3] } });
  sources.sort((a, b) => mappingTextCompare(mappingCanonical(a), mappingCanonical(b)));
  // Server ordering is canonical({domain, query, key}); canonical sorts keys.
  const entries = await Promise.all(sources.map(async (source, i) => ({ ...source, ordinal: i+1, queryDigest: await mappingHash(source.query) }))) as MappingSource[];
  const catalogDigest = await mappingHash({ schemaVersion: "business-evidence-directory-v2", entries });
  const header = { schemaVersion: "business-evidence-v2", sourceCount: count, catalogDigest, capacityProfile: "catalog-48-facts-v1",
    collector: { version: 1, surface: "business_collection", pageSize: 100 }, limits: { factBytes: 67108864, factPages: 2000 } };
  const run: BudgetRun = { id: "mapping-evidence", version: 8, status: "sealed", plan: header, sources: Object.fromEntries(entries.map(entry => [entry.key, { complete: true }])) };
  const context = mappingContext(run, "actor-a"), offsets: number[] = [];
  async function page(offset: number, size = 20) {
    const items = entries.slice(offset, offset+size), end = offset+items.length;
    const result = { schemaVersion: "business-evidence-directory-page-v2", runId: run.id, evidenceVersion: run.version, planDigest: await mappingHash(header), catalogDigest,
      offset, requestedLimit: 20, total: count, returned: items.length, nextOffset: end < count ? end : null, items };
    return { ...result, pageDigest: await mappingHash(result) };
  }
  return { run, context, entries, offsets, page, read: async (offset: number) => { offsets.push(offset); return page(offset); } };
}
test("full immutable header and completed sources are required; binding includes account and version", async () => {
  const f = await fixture(); assert.equal(f.context.bindingKey, mappingBindingKey(f.run, "actor-a"));
  for (const actor of ["actor-b", "actor-a:other"]) assert.notEqual(mappingBindingKey(f.run, actor), f.context.bindingKey);
  assert.notEqual(mappingBindingKey({ ...f.run, version: 9 }, "actor-a"), f.context.bindingKey);
  assert.throws(() => mappingContext({ ...f.run, plan: { schemaVersion: "business-evidence-v2", sourceCount: 4, catalogDigest: f.run.plan.catalogDigest } }, "actor-a"));
  assert.throws(() => mappingContext({ ...f.run, status: "collecting" }, "actor-a"));
  assert.throws(() => mappingContext({ ...f.run, sources: {} }, "actor-a"));
  assert.throws(() => mappingContext({ ...f.run, sources: { ...f.run.sources, sales1: { complete: false } } }, "actor-a"));
  assert.throws(() => mappingContext({ ...f.run, version: 1.1 }, "actor-a"));
});
test("all 48 sources stream in exact pages before any selection and 49 are rejected", async () => {
  const f = await fixture(48); const rows = await readMappingDirectory(f.context, f.read);
  assert.equal(rows.length, 48); assert.deepEqual(f.offsets, [0, 20, 40]); assert.deepEqual(mappingPairs(rows, []), []);
  await assert.rejects(fixture(49));
});
test("short byte-bounded pages follow actual nextOffset and late failure rejects whole catalog", async () => {
  const f = await fixture(23); const offsets: number[] = [];
  assert.equal((await readMappingDirectory(f.context, async offset => { offsets.push(offset); return f.page(offset, 7); })).length, 23);
  assert.deepEqual(offsets, [0, 7, 14, 21]);
  await assert.rejects(readMappingDirectory(f.context, async offset => { if (offset) throw Error("late source failure"); return f.page(offset); }));
});
test("page identity, requested limit, count, offsets and all hashes fail closed", async () => {
  const f = await fixture();
  for (const [key, value] of Object.entries({ runId: "other", evidenceVersion: 9, planDigest: "a".repeat(64), catalogDigest: "b".repeat(64), offset: 1,
    requestedLimit: 10, total: 3, returned: 0, nextOffset: 4, pageDigest: "c".repeat(64), extra: true })) {
    await assert.rejects(readMappingDirectory(f.context, async offset => ({ ...await f.page(offset), [key]: value })), key);
  }
});
test("rehashing duplicate, missing, reordered, cross-page and modified source rows cannot forge catalog", async () => {
  const f = await fixture(23);
  for (const mutate of [(items: MappingSource[]) => { items[0].ordinal = 2; }, (items: MappingSource[]) => { items[1] = items[0]; },
    (items: MappingSource[]) => { items[0].key = "unrequested"; }, (items: MappingSource[]) => { items[0].query.shop = "其他店"; }]) {
    await assert.rejects(readMappingDirectory(f.context, async offset => {
      const value = structuredClone(await f.page(offset)); mutate(value.items); const { pageDigest, ...body } = value; assert.equal(pageDigest.length, 64);
      return { ...body, pageDigest: await mappingHash(body) };
    }));
  }
  await assert.rejects(readMappingDirectory(f.context, async offset => {
    const value = structuredClone(await f.page(offset)); value.items[0].query.shop = "自洽新店";
    value.items[0].queryDigest = await mappingHash(value.items[0].query);
    const { pageDigest, ...body } = value; assert.equal(pageDigest.length, 64); return { ...body, pageDigest: await mappingHash(body) };
  }));
});
test("context aliases are frozen before callback and fetched source aliases are not returned", async () => {
  const f = await fixture();
  const rows = await readMappingDirectory(f.context, async offset => { f.context.catalogDigest = "f".repeat(64); f.context.sourceKeys.length = 0; return f.page(offset); });
  rows[0].query.shop = "changed"; assert.notEqual(f.entries[0].query.shop, "changed");
});
test("bounded malicious nested objects fail before unbounded hashing", async () => {
  const f = await fixture(); const cycle: Record<string, unknown> = {}; cycle.cycle = cycle;
  for (const value of [cycle, { x: "x".repeat(1000000) }, Array(10000).fill(0)]) {
    await assert.rejects(readMappingDirectory(f.context, async () => value));
  }
});
test("explicit sales windows each choose exact current same-shop master without guessing", async () => {
  const f = await fixture(); const sources = await readMappingDirectory(f.context, f.read);
  const sales = sources.filter(source => source.domain === "sales");
  assert.deepEqual(new Set(sales.map(source => source.query.window)), new Set(["current", "previous", "yearAgo"]));
  for (const source of sales) assert.deepEqual(matchingMasters(source, sources).map(row => row.key), ["master"]);
  const choices = sales.map(source => ({ salesKey: source.key, masterKey: "master" })).reverse();
  assert.deepEqual(mappingPairs(sources, choices).map(pair => pair.salesKey), ["sales1", "sales2", "sales3"]);
  assert.throws(() => mappingPairs(sources, [choices[0], choices[0]]));
  assert.throws(() => mappingPairs(sources, [{ salesKey: "master", masterKey: "sales1" }]));
  assert.throws(() => mappingPairs(sources, [{ salesKey: "sales1", masterKey: "missing" }]));
  const changed = structuredClone(sources); changed[0].query.shop += "别名";
  assert.equal(matchingMasters(sales[0], changed).length, 0);
});
test("no matching master is a visible gap and 16000 byte plan capacity never truncates", async () => {
  const f = await fixture(48); const sources = await readMappingDirectory(f.context, f.read);
  assert.equal(matchingMasters(sources[1], sources.slice(1)).length, 0);
  const choices = sources.filter(source => source.domain === "sales").map(source => ({ salesKey: source.key, masterKey: "master" }));
  assert.equal(mappingPairs(sources, choices).length, 47);
  assert.throws(() => mappingPairs(sources, [...choices, choices[0]]));
  const wide = sources.map((source, i) => ({ ...source, key: String(i).padEnd(160, "x") }));
  assert.throws(() => mappingPairs(wide, wide.slice(1).map(source => ({ salesKey: source.key, masterKey: wide[0].key }))), /16000/);
});
test("Unicode source order uses Python code points rather than UTF16 units", () => {
  assert.ok(mappingTextCompare("\ue000", "😀") < 0); assert.ok("\ue000" > "😀");
});
