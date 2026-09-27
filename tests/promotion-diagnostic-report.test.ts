import assert from "node:assert/strict";
import test from "node:test";
import { Script } from "node:vm";
import { strFromU8, unzipSync } from "fflate";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";
import {
  buildPromotionDiagnosticReport,
  promotionDiagnosticHtml,
  promotionDiagnosticXlsx,
  type DiagnosticPeriod,
} from "../lib/jd/promotion-diagnostic-report";

function days(start: string, count: number) {
  const first = Date.parse(`${start}T00:00:00Z`);
  return Array.from({ length: count }, (_, index) => new Date(first + index * 86_400_000).toISOString().slice(0, 10));
}

function period(start: string, { count = 6, shopName = "志高商用设备旗舰店", missing = false }:
  { count?: number; shopName?: string; missing?: boolean } = {}): DiagnosticPeriod {
  const requested = days(start, count);
  const present = missing ? requested.slice(0, -1) : requested;
  const rowCount = present.length;
  const metrics = {
    spendCents: rowCount * 1000,
    impressions: rowCount * 100,
    clicks: rowCount * 20,
    reportedOrderLines: rowCount * 2,
    reportedGmvCents: rowCount * 9000,
  };
  const metricAvailability = Object.fromEntries(Object.keys(metrics).map((key) => [key, {
    presentRows: rowCount, totalRows: rowCount, complete: rowCount > 0,
  }])) as DiagnosticPeriod["metricAvailability"];
  const oneGroup = (key: string) => [{ key, rowCount, name: "计划甲", planId: "P1", id: "P1", metrics }];
  return {
    schemaVersion: "jd-promotion-diagnostic-v1",
    identity: { platform: "京东", shopName },
    period: { startDate: requested[0]!, endDate: requested.at(-1)! },
    sourceRevision: "12:abcdef",
    coverage: { requestedDates: requested, presentDates: present, missingDates: missing ? requested.slice(-1) : [], complete: !missing, rowCount, aggregateReconciled: true, batchOwnershipReconciled: true },
    metricAvailability,
    sourceBatches: present.map((date) => ({ date, batchIds: [`batch-${date}`], accountNicknames: ["测试账户"], accountPresentRows: 1, rowCount: 1, aggregateBatchId: `batch-${date}`,
      ownership: [{ batchId: `batch-${date}`, status: "completed", source: "jd_promotion", dataset: "ad", platform: "京东", shopName,
        dateMin: date, dateMax: date, rowCount: 1, warningCount: 0 }],
      aggregateOwnership: { batchId: `batch-${date}`, status: "completed", source: "jd_promotion", dataset: "ad", platform: "京东", shopName,
        dateMin: date, dateMax: date, rowCount: 1, warningCount: 0 } })),
    summary: metrics,
    daily: requested.map((date) => ({ date, rowCount: present.includes(date) ? 1 : 0,
      metrics: present.includes(date) ? { spendCents: 1000, impressions: 100, clicks: 20, reportedOrderLines: 2, reportedGmvCents: 9000 }
        : { spendCents: null, impressions: null, clicks: null, reportedOrderLines: null, reportedGmvCents: null } })),
    groups: { plans: oneGroup('["P1","计划甲"]'), products: [{ ...oneGroup('["S1"]')[0]!, skuId: "S1" }],
      keywords: [{ ...oneGroup('["词甲"]')[0]!, keyword: "词甲" }],
      searchTerms: [{ ...oneGroup('["搜索甲"]')[0]!, searchTerm: "搜索甲" }], keywordSku: oneGroup('["词甲","S1"]') },
    limitations: ["归因窗口未独立核实"],
  };
}

test("fixed six-day periods produce dynamic offline HTML and same-table XLSX", () => {
  const current = period("2026-09-20");
  const previous = period("2026-09-14");
  const report = buildPromotionDiagnosticReport(current, previous);
  assert.equal(report.complete, true);
  assert.equal(report.comparisonAvailable, true);
  assert.equal(report.period.endDate, "2026-09-25");
  assert.equal(report.previousPeriod?.endDate, "2026-09-19");
  assert.deepEqual(report.tables.map((item) => item.key), ["summary", "daily", "plans", "products", "keywords", "searchTerms", "keywordSku", "actions", "coverage"]);
  const html = promotionDiagnosticHtml(report);
  assert.match(html, /2026-09-20 至 2026-09-25/);
  assert.match(html, /rows\.length-1/);
  assert.doesNotMatch(html, /\/29|30天|2026-08-16/);
  assert.match(html, /搜索当前表/);
  assert.match(html, /导出当前表CSV/);
  assert.match(html, /createElementNS\('http:\/\/www\.w3\.org\/2000\/svg'/);
  const offlineScript = html.match(/<script>([\s\S]*?)<\/script><\/body>/)?.[1];
  assert.ok(offlineScript);
  assert.doesNotThrow(() => new Script(offlineScript));
  const files = unzipSync(promotionDiagnosticXlsx(report));
  const workbook = strFromU8(files["xl/workbook.xml"]!);
  assert.equal((workbook.match(/<sheet /g) ?? []).length, report.tables.length);
  const summary = strFromU8(files["xl/worksheets/sheet1.xml"]!);
  const styles = strFromU8(files["xl/styles.xml"]!);
  assert.match(summary, /推广花费/);
  assert.match(summary, /60<\/v>/); // 6 × 10 yuan, same source cells as HTML.
  assert.match(summary, /state="frozen"/);
  assert.match(summary, /<autoFilter ref="A1:E10"\/>/);
  assert.match(styles, /formatCode="0\.00&quot;%&quot;"/);
});

test("missing source day stays partial, and a mismatched shop or revision cannot compare", () => {
  const current = period("2026-09-20", { missing: true });
  const report = buildPromotionDiagnosticReport(current, period("2026-09-14"));
  assert.equal(report.complete, false);
  assert.equal(report.comparisonAvailable, false);
  assert.equal(report.actions[0]?.priority, "先补源");
  assert.match(promotionDiagnosticHtml(report), /数据待补/);
  assert.throws(() => buildPromotionDiagnosticReport(period("2026-09-20"), period("2026-09-14", { shopName: "其他店" })), /店铺身份/);
  const stale = period("2026-09-14"); stale.sourceRevision = "13:changed";
  assert.equal(buildPromotionDiagnosticReport(period("2026-09-20"), stale).comparisonAvailable, false);
});

test("unreconciled or incomplete dimension facts cannot become a report", () => {
  const bad = period("2026-09-20");
  bad.coverage.aggregateReconciled = false;
  assert.throws(() => buildPromotionDiagnosticReport(bad), /来源身份、修订或逐日对账/);
  const lost = period("2026-09-20");
  lost.groups.keywordSku[0]!.rowCount -= 1;
  assert.throws(() => buildPromotionDiagnosticReport(lost), /词货|keywordSku/);
});

test("anonymous plan spend is disclosed without a fabricated plan concentration", () => {
  const current = period("2026-09-20");
  current.groups.plans = [{ key: "[null,null]", rowCount: 6, name: "未提供计划名称", planId: null, metrics: current.summary }];
  const report = buildPromotionDiagnosticReport(current);
  assert.ok(report.findings.some((finding) => finding.title === "计划身份缺失"));
  assert.ok(!report.findings.some((finding) => finding.title === "可识别计划花费"));
  assert.equal(report.actions[0]?.priority, "先核身份");
});

test("XLSX inline text removes XML-forbidden controls from source labels", () => {
  const workbook = unzipSync(createXlsxWorkbookBytes([{ name: "来源", rows: [["对象"], ["计划\u000B名称"]] }]));
  const worksheet = strFromU8(workbook["xl/worksheets/sheet1.xml"]!);
  assert.match(worksheet, /计划名称/);
  assert.doesNotMatch(worksheet, /\u000B/);
});

function cell(report: ReturnType<typeof buildPromotionDiagnosticReport>, tableKey: string, row: number, columnKey: string) {
  const table = report.tables.find((item) => item.key === tableKey)!;
  return table.rows[row]![table.columns.findIndex((column) => column.key === columnKey)];
}

test("plans match by ID across a rename; other objects use their stable source identities", () => {
  const current = period("2026-09-20"), previous = period("2026-09-14");
  current.groups.plans[0]!.name = "计划乙";
  current.groups.plans[0]!.key = '["P1","计划乙"]';
  current.groups.products[0]!.name = "商品新标题";
  current.groups.products[0]!.key = '["S1","商品新标题"]';
  current.groups.keywords[0]!.name = "新显示标签";
  current.groups.searchTerms[0]!.name = "新搜索标签";
  const report = buildPromotionDiagnosticReport(current, previous);
  for (const key of ["plans", "products", "keywords", "searchTerms"]) {
    assert.equal(cell(report, key, 0, "state"), "可比");
    assert.equal(cell(report, key, 0, "spendCurrent"), 60);
    assert.equal(cell(report, key, 0, "spendPrevious"), 60);
    assert.equal(cell(report, key, 0, "spendChange"), 0);
  }
  assert.equal(cell(report, "plans", 0, "name"), "计划乙");
  assert.equal(cell(report, "plans", 0, "groupKey"), '["P1","计划乙"]');
});

test("new, disappeared, unknown, and no-baseline states retain missing values", () => {
  const current = period("2026-09-20"), previous = period("2026-09-14");
  const group = (id: string, rowCount: number) => ({ key: JSON.stringify([id, id]), rowCount, planId: id,
    name: id, metrics: { spendCents: rowCount * 1000, impressions: rowCount * 100, clicks: rowCount * 20,
      reportedOrderLines: rowCount * 2, reportedGmvCents: rowCount * 9000 } });
  current.groups.plans = [group("P1", 3), group("P3", 3)];
  previous.groups.plans = [group("P1", 3), group("P2", 3)];
  const report = buildPromotionDiagnosticReport(current, previous);
  assert.deepEqual(report.tables.find((table) => table.key === "plans")!.rows.map((row) => row[1]), ["可比", "新增", "消失"]);
  assert.equal(cell(report, "plans", 1, "spendPrevious"), null);
  assert.equal(cell(report, "plans", 1, "spendChange"), null);
  assert.equal(cell(report, "plans", 2, "spendCurrent"), null);
  assert.equal(cell(report, "plans", 2, "spendPrevious"), 30);
  assert.equal(cell(report, "plans", 2, "spendChange"), null);
  assert.ok(report.findings.some((finding) => finding.target?.groupKey === '["P2","P2"]'));
  assert.ok(report.actions.some((action) => action.target?.groupKey === '["P3","P3"]'));

  current.groups.plans = [{ ...group("P1", 6), planId: null, name: "未提供计划名称", key: "[null,null]" }];
  const unknown = buildPromotionDiagnosticReport(current, previous);
  assert.equal(cell(unknown, "plans", 0, "state"), "身份未知");
  assert.equal(cell(unknown, "plans", 0, "spendPrevious"), null);

  const noBaseline = buildPromotionDiagnosticReport(period("2026-09-20"));
  assert.equal(cell(noBaseline, "products", 0, "state"), "无前期基线");
  assert.equal(cell(noBaseline, "products", 0, "clicksPrevious"), null);
  assert.equal(cell(noBaseline, "products", 0, "orderRatePointChange"), null);
});

test("material object conversion decline produces targeted findings with attribution caveat", () => {
  const current = period("2026-09-20"), previous = period("2026-09-14");
  for (const key of ["plans", "products", "keywords", "searchTerms"] as const) {
    current.groups[key][0]!.metrics = { spendCents: 6000, impressions: 1000, clicks: 100, reportedOrderLines: 4, reportedGmvCents: 36000 };
    previous.groups[key][0]!.metrics = { spendCents: 6000, impressions: 1000, clicks: 100, reportedOrderLines: 10, reportedGmvCents: 90000 };
  }
  current.summary = { spendCents: 6000, impressions: 1000, clicks: 100, reportedOrderLines: 4, reportedGmvCents: 36000 };
  previous.summary = { spendCents: 6000, impressions: 1000, clicks: 100, reportedOrderLines: 10, reportedGmvCents: 90000 };
  const report = buildPromotionDiagnosticReport(current, previous);
  for (const key of ["plans", "products", "keywords", "searchTerms"]) {
    assert.equal(cell(report, key, 0, "orderRateCurrent"), 4);
    assert.equal(cell(report, key, 0, "orderRatePrevious"), 10);
    assert.equal(cell(report, key, 0, "orderRatePointChange"), -6);
    assert.ok(report.findings.some((finding) => finding.target?.tableKey === key && /不证明|不能据此证明/.test(finding.text)));
    assert.ok(report.actions.some((action) => action.target?.tableKey === key));
  }
});

test("name fallback is unique, while duplicate plan identities and mismatched revisions remain unpaired", () => {
  const current = period("2026-09-20"), previous = period("2026-09-14");
  current.groups.plans[0]!.planId = null;
  previous.groups.plans[0]!.planId = null;
  current.groups.plans[0]!.key = '[null,"计划甲"]';
  previous.groups.plans[0]!.key = '[null,"计划甲"]';
  assert.equal(cell(buildPromotionDiagnosticReport(current, previous), "plans", 0, "state"), "可比");

  const duplicate = { ...current.groups.plans[0]!, key: '[null,"计划甲",2]', rowCount: 3,
    metrics: { spendCents: 3000, impressions: 300, clicks: 60, reportedOrderLines: 6, reportedGmvCents: 27000 } };
  current.groups.plans[0] = { ...duplicate, key: '[null,"计划甲",1]' };
  current.groups.plans.push(duplicate);
  assert.deepEqual(buildPromotionDiagnosticReport(current, previous).tables.find((table) => table.key === "plans")!.rows
    .slice(0, 2).map((row) => row[1]), ["身份未知", "身份未知"]);

  const stale = period("2026-09-14");
  stale.sourceRevision = "13:changed";
  const noComparison = buildPromotionDiagnosticReport(period("2026-09-20"), stale);
  assert.equal(cell(noComparison, "plans", 0, "state"), "无前期基线");
  assert.equal(cell(noComparison, "plans", 0, "spendPrevious"), null);
});

test("identifiable keyword and search-term subsets get exact observation targets", () => {
  const report = buildPromotionDiagnosticReport(period("2026-09-20"), period("2026-09-14"));
  for (const tableKey of ["keywords", "searchTerms"]) {
    const observation = report.actions.find((item) => item.tableKey === tableKey && item.priority === "观察：对象复核");
    assert.ok(observation?.target?.groupKey);
    assert.equal(observation.target.tableKey, tableKey);
    assert.match(observation.change, /核对/);
  }
});
