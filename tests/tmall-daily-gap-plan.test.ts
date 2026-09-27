import assert from "node:assert/strict";
import test from "node:test";
import { planTmallDailyGaps } from "../tools/tmall-daily-gap-plan";

const scope = { startDate: "2026-08-30", endDate: "2026-09-02", maximumDays: 1 };
test("分别规划非连续跨月缺口，商品已覆盖只补推广，保持有界串行", () => {
  const result = planTmallDailyGaps({ ...scope,
    productDailyDates: ["2026-08-30", "2026-08-31", "2026-09-02"],
    promotionDates: ["2026-08-31", "2026-09-02"] });
  assert.deepEqual(result.selectedDates, ["2026-08-30"]);
  assert.deepEqual(result.productDownloadDates, []);
  assert.deepEqual(result.missingProductDates, ["2026-09-01"]);
  assert.deepEqual(result.missingPromotionDates, ["2026-08-30", "2026-09-01"]);
  assert.deepEqual(result.remainingDates, ["2026-09-01"]);
  assert.equal(result.truncated, true);
});
test("无数据从起始日补，重跑同覆盖不推进，成功后才选择下一日", () => {
  const first = { ...scope, productDailyDates: [], promotionDates: [] };
  assert.deepEqual(planTmallDailyGaps(first), planTmallDailyGaps(first));
  assert.deepEqual(planTmallDailyGaps(first).productDownloadDates, ["2026-08-30"]);
  assert.deepEqual(planTmallDailyGaps({ ...first, productDailyDates: ["2026-08-30"],
    promotionDates: ["2026-08-30"] }).selectedDates, ["2026-08-31"]);
});
test("全部覆盖不下载；推广已有但商品缺失只补商品", () => {
  const all = ["2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02"];
  assert.deepEqual(planTmallDailyGaps({ ...scope, productDailyDates: all, promotionDates: all }).selectedDates, []);
  assert.deepEqual(planTmallDailyGaps({ ...scope, productDailyDates: all.slice(1),
    promotionDates: all }).productDownloadDates, ["2026-08-30"]);
});
test("拒绝非法日期、倒置范围、无效上限与损坏覆盖", () => {
  const base = { ...scope, productDailyDates: [], promotionDates: [] };
  for (const change of [{ startDate: "2026-02-30" }, { endDate: "2026-01-01" },
    { maximumDays: 0 }, { maximumDays: 31 }, { promotionDates: ["bad"] }]) {
    assert.throws(() => planTmallDailyGaps({ ...base, ...change }));
  }
});
