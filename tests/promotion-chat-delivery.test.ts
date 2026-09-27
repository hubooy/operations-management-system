import assert from "node:assert/strict";
import test from "node:test";
import { attachPromotionChatInterpretation, interpretationFromChatMessage, locatorFromChatMessage,
  parsePromotionChatLocator, previousPromotionChatPeriod, buildPromotionChatReport } from "../lib/jd/promotion-chat-delivery";
import { promotionDiagnosticHtml, promotionDiagnosticXlsx, type DiagnosticPeriod, type PromotionDiagnosticReport } from "../lib/jd/promotion-diagnostic-report";

const locator = { shopName: "志高商用设备旗舰店", startDate: "2026-09-20", endDate: "2026-09-25", sourceRevision: "799:9af9045ada91" };

test("download locator is tied to the exact authorized assistant message", () => {
  const payload = { items: [{ id: "msg-1", conversationId: "conv-1", role: "assistant", execution: { promotionReport: locator } }] };
  assert.deepEqual(locatorFromChatMessage(payload, "conv-1", "msg-1"), locator);
  assert.throws(() => locatorFromChatMessage(payload, "conv-other", "msg-1"), /不存在/);
  assert.throws(() => locatorFromChatMessage({ items: [{ ...payload.items[0], role: "user" }] }, "conv-1", "msg-1"), /不存在/);
  assert.throws(() => locatorFromChatMessage({ items: [{ ...payload.items[0], execution: {} }] }, "conv-1", "msg-1"), /没有推广诊断/);
});

test("locator rejects unsupported stores and invalid or excessive dates", () => {
  assert.deepEqual(previousPromotionChatPeriod(locator), { startDate: "2026-09-14", endDate: "2026-09-19" });
  assert.throws(() => parsePromotionChatLocator({ ...locator, shopName: "其他店" }), /范围无效/);
  assert.throws(() => parsePromotionChatLocator({ ...locator, endDate: "2026-09-31" }), /范围无效/);
  assert.throws(() => parsePromotionChatLocator({ ...locator, endDate: "2026-09-28" }), /日期无效/);
  assert.throws(() => parsePromotionChatLocator({ ...locator, sourceRevision: "800:changed" }), /范围无效/);
});

test("a source revision change invalidates both chat download formats before rendering", () => {
  const stub = (startDate: string, endDate: string, sourceRevision: string) => ({
    schemaVersion: "jd-promotion-diagnostic-v1", identity: { platform: "京东", shopName: locator.shopName },
    period: { startDate, endDate }, sourceRevision,
  }) as DiagnosticPeriod;
  assert.throws(() => buildPromotionChatReport(locator,
    stub("2026-09-20", "2026-09-25", "800:aaaaaaaaaaaa"),
    stub("2026-09-14", "2026-09-19", locator.sourceRevision)), /来源修订/);
});

test("both files retain the exact assistant reply as a separately labeled interpretation", () => {
  const content = "计划点击增长但归因转化下降；请人工核对归因窗口与对应 SKU。";
  const payload = { items: [{ id: "msg-1", conversationId: "conv-1", role: "assistant", content,
    contentTruncated: false, execution: { promotionReport: locator } }] };
  assert.deepEqual(interpretationFromChatMessage(payload, "conv-1", "msg-1"), { locator, text: content });
  const report = { schemaVersion: "jd-promotion-report-v1", analysisType: "deterministic_review_draft",
    sourceRevision: locator.sourceRevision, shopName: locator.shopName,
    period: { startDate: locator.startDate, endDate: locator.endDate }, previousPeriod: previousPromotionChatPeriod(locator),
    complete: true, comparisonAvailable: true, metrics: {}, previousMetrics: {}, coverage: {},
    findings: [], actions: [], tables: [], limitations: ["规则诊断仅列人工复核候选；未调用模型，也未自动修改投放。"] } as unknown as PromotionDiagnosticReport;
  const attached = attachPromotionChatInterpretation(report, content, "msg-1");
  assert.deepEqual(attached.tables.at(-1)?.rows[0], ["未逐条核验数字/日期/引用；待人工复核", locator.sourceRevision, "msg-1", content]);
  assert.ok(attached.limitations.every((item) => !item.includes("未调用模型")));
  assert.ok(promotionDiagnosticHtml(attached).includes("文字未逐项核验"));
  assert.ok(promotionDiagnosticHtml(attached).includes(content));
  assert.ok(promotionDiagnosticXlsx(attached).byteLength > 1000);
  assert.throws(() => interpretationFromChatMessage({ items: [{ ...payload.items[0], contentTruncated: true }] }, "conv-1", "msg-1"), /过长或不完整/);
  const unsupported = attachPromotionChatInterpretation(report, "2026-10-31 花费999999元，未给来源键。", "msg-1");
  assert.match(unsupported.tables.at(-1)!.note, /未逐条核验其中的数字、日期与引用/);
  assert.ok(unsupported.limitations.some((item) => item.includes("不能替代来源表")));
  assert.deepEqual(unsupported.metrics, report.metrics);
});
