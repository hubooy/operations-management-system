import assert from "node:assert/strict";
import test from "node:test";
import { buildPromotionDiagnosticReport, type DiagnosticPeriod } from "../lib/jd/promotion-diagnostic-report";
import { attachPromotionInterpretation, preparePromotionInterpretation, validatePromotionInterpretationReply } from "../lib/jd/promotion-diagnostic-interpret";

function period(start: string): DiagnosticPeriod {
  const dates = Array.from({ length: 6 }, (_, index) => new Date(Date.parse(`${start}T00:00:00Z`) + index * 86_400_000).toISOString().slice(0, 10));
  const metrics = { spendCents: 6000, impressions: 600, clicks: 120, reportedOrderLines: 12, reportedGmvCents: 54000 };
  const metricAvailability = Object.fromEntries(Object.keys(metrics).map((key) => [key, { presentRows: 6, totalRows: 6, complete: true }])) as DiagnosticPeriod["metricAvailability"];
  const group = (key: string) => [{ key, rowCount: 6, name: "计划甲", planId: "P1", skuId: "S1", keyword: "词甲", searchTerm: "搜索甲", metrics }];
  return {
    schemaVersion: "jd-promotion-diagnostic-v1",
    identity: { platform: "京东", shopName: "志高商用设备旗舰店" },
    period: { startDate: dates[0]!, endDate: dates.at(-1)! }, sourceRevision: "12:abcdef",
    coverage: { requestedDates: dates, presentDates: dates, missingDates: [], complete: true, rowCount: 6, aggregateReconciled: true, batchOwnershipReconciled: true },
    metricAvailability,
    sourceBatches: dates.map((date) => ({ date, batchIds: [`batch-${date}`], accountNicknames: ["测试账户"], accountPresentRows: 1, rowCount: 1, aggregateBatchId: `batch-${date}`,
      ownership: [{ batchId: `batch-${date}`, status: "completed", source: "jd_promotion", dataset: "ad", platform: "京东", shopName: "志高商用设备旗舰店",
        dateMin: date, dateMax: date, rowCount: 1, warningCount: 0 }],
      aggregateOwnership: { batchId: `batch-${date}`, status: "completed", source: "jd_promotion", dataset: "ad", platform: "京东", shopName: "志高商用设备旗舰店",
        dateMin: date, dateMax: date, rowCount: 1, warningCount: 0 } })),
    summary: metrics,
    daily: dates.map((date) => ({ date, rowCount: 1, metrics: { spendCents: 1000, impressions: 100, clicks: 20, reportedOrderLines: 2, reportedGmvCents: 9000 } })),
    groups: { plans: group('["P1","计划甲"]'), products: group('["S1"]'), keywords: group('["词甲"]'),
      searchTerms: group('["搜索甲"]'), keywordSku: group('["词甲","S1"]') },
    limitations: ["归因窗口未独立核实"],
  };
}

test("prepares stable bounded evidence for one exact object and both comparable periods", () => {
  const report = buildPromotionDiagnosticReport(period("2026-09-20"), period("2026-09-14"));
  const target = { tableKey: "plans", groupKey: '["P1","计划甲"]' };
  const first = preparePromotionInterpretation(report, target);
  const second = preparePromotionInterpretation(report, target);
  assert.deepEqual(first, second);
  assert.equal(first.sourceRevision, "12:abcdef");
  assert.deepEqual(first.previousPeriod, { startDate: "2026-09-14", endDate: "2026-09-19" });
  assert.ok(first.evidence.some((item) => item.id === "T01" && item.values.groupKey === target.groupKey));
  assert.ok(first.evidence.some((item) => item.kind === "summary" && item.values.previous !== null));
  assert.ok(first.evidence.length <= 18);
  assert.ok(new TextEncoder().encode(first.prompt).length <= 12_000);
  assert.doesNotMatch(first.prompt, /batch-2026/); // Full source rows and batch payloads stay out of the model input.
});

test("only exact unique target and complete reconciled report can be prepared", () => {
  const report = buildPromotionDiagnosticReport(period("2026-09-20"));
  assert.throws(() => preparePromotionInterpretation(report, { tableKey: "plans", groupKey: "other" }), /不存在/);
  assert.throws(() => preparePromotionInterpretation(report, { tableKey: "coverage", groupKey: "x" }), /对象身份/);
  report.complete = false;
  assert.throws(() => preparePromotionInterpretation(report, { tableKey: "plans", groupKey: '["P1","计划甲"]' }), /完整且已对账/);
});

test("accepts cited qualitative JSON, rejects invented IDs, numbers and unbounded shape", () => {
  const prepared = preparePromotionInterpretation(buildPromotionDiagnosticReport(period("2026-09-20")),
    { tableKey: "plans", groupKey: '["P1","计划甲"]' });
  const context = { sourceRevision: prepared.sourceRevision, startDate: prepared.period.startDate,
    endDate: prepared.period.endDate, tableKey: prepared.target.tableKey, groupKey: prepared.target.groupKey };
  const good = { context, items: [{ text: "该计划值得核查搜索词和归因成熟度。", evidenceIds: ["T01", "L1"] }] };
  assert.deepEqual(validatePromotionInterpretationReply(JSON.stringify(good), prepared), good);
  assert.throws(() => validatePromotionInterpretationReply({ ...good, context: { ...context, sourceRevision: "stale" } }, prepared), /不匹配/);
  assert.throws(() => validatePromotionInterpretationReply({ context, items: [{ text: "结论", evidenceIds: ["T01", "X99"] }] }, prepared), /证据引用/);
  assert.throws(() => validatePromotionInterpretationReply({ context, items: [{ text: "花费提高 30%", evidenceIds: ["T01"] }] }, prepared), /数字/);
  assert.throws(() => validatePromotionInterpretationReply({ context, items: [{ text: "增长三倍", evidenceIds: ["T01"] }] }, prepared), /数字/);
  assert.throws(() => validatePromotionInterpretationReply({ context, items: [{ text: "应立即停投", evidenceIds: ["T01"] }] }, prepared), /直接投放指令/);
  assert.throws(() => validatePromotionInterpretationReply({ context, items: [{ text: "结论", evidenceIds: ["S01"] }] }, prepared), /证据引用/);
  assert.throws(() => validatePromotionInterpretationReply({ ...good, extra: true }, prepared), /结构/);
  assert.throws(() => validatePromotionInterpretationReply("```json\n{}\n```", prepared), /不是 JSON/);
});

test("model text stays in a separate cited report table and keeps calculated cells", () => {
  const report = buildPromotionDiagnosticReport(period("2026-09-20"));
  const prepared = preparePromotionInterpretation(report, { tableKey: "plans", groupKey: '["P1","计划甲"]' });
  const reply = { context: { sourceRevision: prepared.sourceRevision, startDate: prepared.period.startDate,
    endDate: prepared.period.endDate, tableKey: "plans", groupKey: '["P1","计划甲"]' },
  items: [{ text: "该计划需要复核定向变化。", evidenceIds: ["T01"] }] };
  const withModel = attachPromotionInterpretation(report, prepared, reply);
  assert.equal(withModel.tables.at(-1)?.key, "modelInterpretation");
  assert.equal(withModel.tables.at(-1)?.rows[0]?.[1], reply.items[0]!.text);
  assert.deepEqual(withModel.tables[0]?.rows, report.tables[0]?.rows);
  assert.equal(report.tables.some((table) => table.key === "modelInterpretation"), false);
});
