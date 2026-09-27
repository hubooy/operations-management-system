import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import type { DiagnosticPeriod } from "../lib/jd/promotion-diagnostic-report";
import type { DiagnosticWithRelations } from "../lib/jd/promotion-diagnostic-relations";
import type { AiToolExecutionContext } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") return { url: "data:text/javascript,export const env={};", shortCircuit: true };
  if (specifier === "next/headers") return { url: "data:text/javascript,export async function headers(){return new Headers()};export async function cookies(){return {get(){return undefined}}}", shortCircuit: true };
  return nextResolve(specifier, context);
} });

const { getJdPromotionDiagnosticForChat } = await import("../lib/ai/promotion-diagnostic-tool");
const { getToolsForPrincipal } = await import("../lib/ai/tool-registry");
const SHOP = "志高商用设备旗舰店";
const principal = { email: "promotion-chat@example.invalid", displayName: "测试管理员", role: "admin" as const, scope: null };
const context: AiToolExecutionContext = { principal, surface: "test", requestId: "promotion-chat-fixture" };
const metrics = { spendCents: 1000, impressions: 100, clicks: 20, reportedOrderLines: 2, reportedGmvCents: 8000 };
const group = (key: string, rowCount: number, more: Record<string, string | null> = {}) => ({ key, rowCount, metrics, ...more });

function days(start: string, count: number) {
  const first = Date.parse(`${start}T00:00:00Z`);
  return Array.from({ length: count }, (_, index) => new Date(first + index * 86_400_000).toISOString().slice(0, 10));
}

function source(start: string, count = 6, missing = false): DiagnosticWithRelations {
  const requested = days(start, count);
  const present = missing ? requested.slice(0, -1) : requested;
  const rowCount = present.length;
  const total = { spendCents: 1000 * rowCount, impressions: 100 * rowCount, clicks: 20 * rowCount,
    reportedOrderLines: 2 * rowCount, reportedGmvCents: 8000 * rowCount };
  const owner = (date: string) => ({ batchId: `batch-${date}`, status: "completed", source: "jd_promotion", dataset: "ad",
    platform: "京东", shopName: SHOP, dateMin: date, dateMax: date, rowCount: 1, warningCount: 0 });
  return {
    schemaVersion: "jd-promotion-diagnostic-v1", identity: { platform: "京东", shopName: SHOP },
    period: { startDate: requested[0]!, endDate: requested.at(-1)! }, sourceRevision: "42:revision",
    coverage: { requestedDates: requested, presentDates: present, missingDates: missing ? requested.slice(-1) : [],
      complete: !missing, rowCount, aggregateReconciled: true, batchOwnershipReconciled: true },
    metricAvailability: Object.fromEntries(Object.keys(total).map((key) => [key, {
      presentRows: rowCount, totalRows: rowCount, complete: rowCount > 0,
    }])) as DiagnosticPeriod["metricAvailability"],
    sourceBatches: present.map((date) => ({ date, batchIds: [`batch-${date}`], accountNicknames: ["测试账号"],
      accountPresentRows: 1, rowCount: 1, aggregateBatchId: `batch-${date}`,
      ownership: [owner(date)], aggregateOwnership: owner(date) })),
    summary: total, daily: requested.map((date) => ({ date, rowCount: present.includes(date) ? 1 : 0,
      metrics: present.includes(date) ? metrics : { spendCents: null, impressions: null, clicks: null,
        reportedOrderLines: null, reportedGmvCents: null } })), limitations: [],
    groups: {
      plans: [group('["P1"]', rowCount, { planId: "P1", name: "计划甲" })],
      products: [group('["SKU1"]', rowCount, { skuId: "SKU1", name: "商品甲" })],
      keywords: [group('["关键词甲"]', rowCount, { keyword: "关键词甲", name: "关键词甲" })],
      searchTerms: [group('["搜索词甲"]', rowCount, { searchTerm: "搜索词甲", name: "搜索词甲" })],
      keywordSku: [group('["关键词甲","SKU1"]', rowCount, { keyword: "关键词甲", skuId: "SKU1" })],
      planSku: [group('[["P1"],"SKU1"]', rowCount, { planKey: '["P1"]', planId: "P1", skuId: "SKU1" })],
      planKeyword: [group('[["P1"],"关键词甲"]', rowCount, { planKey: '["P1"]', planId: "P1", keyword: "关键词甲" })],
      searchTermSku: [group('["搜索词甲","SKU1"]', rowCount, { searchTerm: "搜索词甲", skuId: "SKU1" })],
    },
  };
}

function reader(missing = false) {
  const calls: string[] = [];
  return { calls, read: async (_context: AiToolExecutionContext, startDate: string, endDate: string) => {
    calls.push(`${startDate}/${endDate}`);
    return source(startDate, days(startDate, Math.round((Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / 86_400_000) + 1).length, missing && calls.length === 2);
  } };
}

const base = { shopName: SHOP, startDate: "2026-09-20", endDate: "2026-09-25" };

test("admin chat tool computes a dynamic equal-day baseline and a stable report locator", async () => {
  const fixture = reader();
  const result = await getJdPromotionDiagnosticForChat(base, context, fixture.read);
  assert.equal(result.status, "complete");
  assert.equal(result.mode, "overview");
  assert.deepEqual(result.reportLocator, { ...base, sourceRevision: "42:revision" });
  assert.deepEqual(fixture.calls, ["2026-09-20/2026-09-25", "2026-09-14/2026-09-19"]);
  assert.equal((result.metrics as { spendCents: number }).spendCents, 6000);
  assert.ok((result.summary as Array<{ metric: string; current: number }>).some((row) => row.metric === "平均点击花费" && row.current === 0.5));
  assert.ok((result.tables as Array<{ key: string }>).some((table) => table.key === "searchTermSku"));
  assert.ok(JSON.stringify(result).length < 40_000);
});

test("model receives a bounded object page and precise same-source relation evidence", async () => {
  const fixture = reader();
  const table = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "table", tableKey: "searchTerms", pageSize: 1 }, context, fixture.read);
  assert.equal(table.totalRows, 1);
  assert.equal((table.rows as Array<Record<string, unknown>>)[0]?.groupKey, '["搜索词甲"]');
  const relation = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "relations", tableKey: "searchTerms",
    groupKey: '["搜索词甲"]' }, context, fixture.read);
  assert.equal((relation.relations as Array<{ tableKey: string }>)[0]?.tableKey, "searchTermSku");
  assert.ok(JSON.stringify(relation).includes("SKU1"));
  assert.ok(JSON.stringify(relation).length < 40_000);
  const stale = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "41:old", mode: "table", tableKey: "plans" }, context, fixture.read);
  assert.equal(stale.status, "source_changed");
});

test("large object dimensions stay paged rather than entering model context wholesale", async () => {
  const fixture = reader();
  const split = async (ctx: AiToolExecutionContext, startDate: string, endDate: string) => {
    const data = await fixture.read(ctx, startDate, endDate);
    data.groups.searchTerms = Array.from({ length: 6 }, (_, index) => group(`["搜索词${index}"]`, 1,
      { searchTerm: `搜索词${index}`, name: `搜索词${index}` }));
    data.groups.searchTermSku = Array.from({ length: 6 }, (_, index) => group(`["搜索词${index}","SKU1"]`, 1,
      { searchTerm: `搜索词${index}`, skuId: "SKU1" }));
    return data;
  };
  const result = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "table",
    tableKey: "searchTerms", pageSize: 3, page: 2 }, context, split);
  assert.equal(result.totalRows, 6);
  assert.equal((result.rows as unknown[]).length, 3);
  assert.equal(result.hasMore, false);
  await assert.rejects(getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "table",
    tableKey: "searchTerms", pageSize: 21 }, context, split), /最多20/);
});

test("numeric sorting finds largest changed objects without treating nulls as zero", async () => {
  const fixture = reader();
  const split = async (ctx: AiToolExecutionContext, startDate: string, endDate: string) => {
    const data = await fixture.read(ctx, startDate, endDate);
    const spends = [500, 1500, 1000, 1000, 1000, 1000];
    data.groups.searchTerms = spends.map((spendCents, index) => ({
      ...group(`["搜索词${index}"]`, 1, { searchTerm: `搜索词${index}`, name: `搜索词${index}` }),
      metrics: { ...metrics, spendCents },
    }));
    data.groups.searchTermSku = spends.map((spendCents, index) => ({
      ...group(`["搜索词${index}","SKU1"]`, 1, { searchTerm: `搜索词${index}`, skuId: "SKU1" }),
      metrics: { ...metrics, spendCents },
    }));
    return data;
  };
  const result = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "table",
    tableKey: "searchTerms", sortColumn: "spendCurrent", sortDirection: "desc", pageSize: 2 }, context, split);
  assert.equal((result.rows as Array<Record<string, unknown>>)[0]?.groupKey, '["搜索词1"]');
  assert.deepEqual(result.appliedSort, { column: "spendCurrent", direction: "desc" });
  await assert.rejects(getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "table",
    tableKey: "searchTerms", sortColumn: "name" }, context, split), /数值指标/);
});

test("an oversized complete page returns no partial rows or misleading next-page offset", async () => {
  const fixture = reader();
  const split = async (ctx: AiToolExecutionContext, startDate: string, endDate: string) => {
    const data = await fixture.read(ctx, startDate, endDate);
    const terms = Array.from({ length: 6 }, (_, index) => `搜索词${index}${"长".repeat(6800)}`);
    data.groups.searchTerms = terms.map((term) => group(JSON.stringify([term]), 1, { searchTerm: term }));
    data.groups.searchTermSku = terms.map((term) => group(JSON.stringify([term, "SKU1"]), 1, { searchTerm: term, skuId: "SKU1" }));
    return data;
  };
  const large = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "table",
    tableKey: "searchTerms", pageSize: 6, page: 1 }, context, split);
  assert.equal(large.status, "page_too_large");
  assert.equal(large.rows, undefined);
  assert.equal(large.restartPage, 1);
  assert.ok((large.suggestedPageSize as number) < 6);
  assert.ok(JSON.stringify(large).length < 40_000);
});

test("a disappeared plan does not imply zero SKU relationships", async () => {
  const fixture = reader();
  const changed = async (ctx: AiToolExecutionContext, startDate: string, endDate: string) => {
    const data = await fixture.read(ctx, startDate, endDate);
    if (startDate === "2026-09-20") {
      data.groups.plans = [group('["P2"]', 6, { planId: "P2", name: "新计划" })];
      data.groups.planSku = [group('[["P2"],"SKU1"]', 6, { planKey: '["P2"]', planId: "P2", skuId: "SKU1" })];
      data.groups.planKeyword = [group('[["P2"],"关键词甲"]', 6, { planKey: '["P2"]', planId: "P2", keyword: "关键词甲" })];
    }
    return data;
  };
  const result = await getJdPromotionDiagnosticForChat({ ...base, sourceRevision: "42:revision", mode: "relations",
    tableKey: "plans", groupKey: '["P1"]' }, context, changed);
  assert.equal(result.relationCoverage, "previous_only_not_queried");
  assert.deepEqual(result.relations, []);
  assert.match(String(result.note), /前期 SKU\/词关系未/);
});

test("permission, unsupported shop, date span and missing source are explicit", async () => {
  const fixture = reader();
  for (const denied of [{ ...context, principal: { ...principal, role: "analyst" as const } },
    { ...context, principal: { ...principal, scope: { platforms: ["京东"], warehouses: [], channels: [] } } },
    { ...context, surface: "dingtalk_chat" as const }]) {
    await assert.rejects(getJdPromotionDiagnosticForChat(base, denied, fixture.read), /无权/);
  }
  assert.equal(fixture.calls.length, 0);
  await assert.rejects(getJdPromotionDiagnosticForChat({ ...base, shopName: "其他店" }, context, fixture.read), /仅支持/);
  await assert.rejects(getJdPromotionDiagnosticForChat({ ...base, endDate: "2026-09-28" }, context, fixture.read), /7|日期/);
  const missing = reader(true);
  const result = await getJdPromotionDiagnosticForChat(base, context, missing.read);
  assert.equal(result.status, "source_unavailable");
  assert.deepEqual(result.previousMissingDates, ["2026-09-19"]);
  assert.equal(result.reportLocator, undefined);
  const visible = getToolsForPrincipal(principal, "ai_chat");
  assert.ok(visible.some((entry) => entry.name === "get_jd_promotion_diagnostic"));
  assert.ok(!getToolsForPrincipal({ ...principal, role: "analyst" }, "ai_chat").some((entry) => entry.name === "get_jd_promotion_diagnostic"));
  assert.ok(!getToolsForPrincipal(principal, "dingtalk_chat").some((entry) => entry.name === "get_jd_promotion_diagnostic"));
});
