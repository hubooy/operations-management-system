import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  createSubmittingTaskManifest,
  clickWithJdNpsSurveyRecovery,
  dismissJdNoticeWithBoundedRetry,
  importJdProductDetailFile,
  isJdCalendarEndSelected,
  isJdProductOverviewNpsSurveyText,
  isJdNpsSurveyPointerInterception,
  isRealtimeSummaryDownloadDialog,
  isSafeJdNpsSurveySkipLabel,
  isSafeJdNoticeCloseLabel,
  isStaticCurrentTimestamp,
  isVerifiedJdDateRangeEcho,
  jdCalendarCellState,
  jdCalendarDateDispatchDecision,
  jdCalendarEndSelectionDecision,
  jdDateRangeSelectionPlan,
  taskManifestPath,
  waitForStableTaskBaseline,
} from "../tools/jdsz-product-detail-export";
import { assertJdProductDetailStoreIdentity, parseJdProductDetailStoreIdentity } from "../lib/jd/product-detail-store-identity";

test("single-day ranges retain the second endpoint click", () => {
  assert.deepEqual(jdDateRangeSelectionPlan("2026-07-21", "2026-07-21"), ["2026-07-21", "2026-07-21"]);
  assert.deepEqual(jdDateRangeSelectionPlan("2026-07-20", "2026-07-21"), ["2026-07-20", "2026-07-21"]);
});

test("a static current-time echo never verifies a custom range", () => {
  const echo = "当前：2026-07-22 09:51:31";
  assert.equal(isStaticCurrentTimestamp(echo), true);
  assert.equal(isVerifiedJdDateRangeEcho(echo, "2026-07-21", "2026-07-21"), false);
});

test("a disabled current-day calendar cell fails before any endpoint dispatch", () => {
  const hashedDisabledToday = "jmt-date-picker-calendar-cell-disabled__9a4 cell-now__f01";
  let dispatches = 0;
  if (jdCalendarDateDispatchDecision(hashedDisabledToday) === "dispatch") dispatches += 1;

  assert.equal(dispatches, 0);
  assert.deepEqual(jdCalendarCellState(hashedDisabledToday), {
    disabled: true,
    now: true,
    start: false,
    end: false,
    selected: false,
  });
  assert.equal(jdCalendarEndSelectionDecision({
    className: hashedDisabledToday,
    echoText: "",
    startDate: "2026-08-01",
    endDate: "2026-08-07",
  }), "blocked_disabled");
});

test("current-day marker never counts as an endpoint", () => {
  assert.equal(isJdCalendarEndSelected(jdCalendarCellState("cell-now__a cell-end__b cell-selected__c")), false);
  assert.equal(jdCalendarEndSelectionDecision({
    className: "cell-now__a cell-end__b cell-selected__c",
    echoText: "",
    startDate: "2026-08-01",
    endDate: "2026-08-07",
  }), "unconfirmed");
});

test("end plus selected is diagnostic only until the strict date echo matches", () => {
  const hoverSecondDate = "cell-end__hash cell-selected__hash";
  assert.equal(isJdCalendarEndSelected(jdCalendarCellState(hoverSecondDate)), true);
  assert.equal(jdCalendarEndSelectionDecision({
    className: hoverSecondDate,
    echoText: "\u5f53\u524d\uff1a2026-08-01 09:30:00",
    startDate: "2026-08-01",
    endDate: "2026-08-07",
  }), "end_selected_without_echo");
});

test("a strict range echo succeeds without a second end dispatch", () => {
  const decision = jdCalendarEndSelectionDecision({
    className: "cell-now__hash",
    echoText: "\u5f53\u524d\uff1a2026-08-01 ~ 08-07",
    startDate: "2026-08-01",
    endDate: "2026-08-07",
  });
  let secondEndDispatches = 0;
  if (decision !== "confirmed_echo") secondEndDispatches += 1;
  assert.equal(decision, "confirmed_echo");
  assert.equal(secondEndDispatches, 0);
});

test("realtime download-settings dialogs are rejected before task submission", () => {
  assert.equal(isRealtimeSummaryDownloadDialog("下载设置\n最多 1000 行\n取消\n确定"), true);
  assert.equal(isRealtimeSummaryDownloadDialog("下载类型\n分天下载\n不包含对比时间\n确定"), false);
});

test("JD notice dismissal only accepts explicit harmless close labels", () => {
  assert.equal(isSafeJdNoticeCloseLabel("Close"), true);
  assert.equal(isSafeJdNoticeCloseLabel("关闭"), true);
  assert.equal(isSafeJdNoticeCloseLabel("立即查看"), false);
});

test("JD product-overview NPS dismissal accepts only the exact opt-out on the identified survey", () => {
  const survey = "请您对商品概览整体使用感受打分\n不满意 0 1 2 3 4 5 6 7 8 9 10 满意\n您在使用商品概览时有什么建议？\n我不愿作答\n提交";
  assert.equal(isJdProductOverviewNpsSurveyText(survey), true);
  assert.equal(isJdProductOverviewNpsSurveyText("商品概览\n我不愿作答\n提交"), false);
  assert.equal(isSafeJdNpsSurveySkipLabel("我不愿作答"), true);
  assert.equal(isSafeJdNpsSurveySkipLabel("提交"), false);
  assert.equal(isSafeJdNpsSurveySkipLabel("10"), false);
});

test("a late JD NPS survey permits one exact reversible click replay", async () => {
  let clicks = 0;
  let dismissals = 0;
  const recovered = await clickWithJdNpsSurveyRecovery(async () => {
    clicks += 1;
    if (clicks === 1) throw new Error('<div id="ux-scene-research"> intercepts pointer events');
  }, async () => { dismissals += 1; });

  assert.equal(recovered, true);
  assert.equal(clicks, 2);
  assert.equal(dismissals, 1);
  assert.equal(isJdNpsSurveyPointerInterception(new Error("another modal intercepts pointer events")), false);
});

test("late-survey recovery never swallows unrelated or repeated click failures", async () => {
  let dismissals = 0;
  await assert.rejects(
    clickWithJdNpsSurveyRecovery(
      async () => { throw new Error("header intercepts pointer events"); },
      async () => { dismissals += 1; },
    ),
    /header intercepts/,
  );
  assert.equal(dismissals, 0);

  let clicks = 0;
  await assert.rejects(
    clickWithJdNpsSurveyRecovery(
      async () => { clicks += 1; throw new Error('<div id="ux-scene-research"> intercepts pointer events'); },
      async () => { dismissals += 1; },
    ),
    /ux-scene-research/,
  );
  assert.equal(clicks, 2);
  assert.equal(dismissals, 1);
});

test("JD notice waits for a stable hydrated close control before clicking", async () => {
  const snapshots = [
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 0 },
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 0, closeControlCount: 0 },
  ];
  let reads = 0;
  let clicks = 0;
  const result = await dismissJdNoticeWithBoundedRetry(
    async () => snapshots[Math.min(reads++, snapshots.length - 1)]!,
    async () => { clicks += 1; },
    async () => undefined,
  );
  assert.equal(result, 1);
  assert.equal(clicks, 1);
});

test("JD notice permits one revalidated retry for the same harmless announcement", async () => {
  const snapshots = [
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    ...Array.from({ length: 20 }, () => ({ noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 })),
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 0, closeControlCount: 0 },
  ];
  let reads = 0;
  let clicks = 0;
  const result = await dismissJdNoticeWithBoundedRetry(
    async () => snapshots[Math.min(reads++, snapshots.length - 1)]!,
    async () => { clicks += 1; },
    async () => undefined,
  );
  assert.equal(result, 2);
  assert.equal(clicks, 2);
});

test("JD notice never auto-closes a different follow-up announcement", async () => {
  const snapshots = [
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 1, noticeKey: "notice-a", closeControlCount: 1 },
    { noticeCount: 1, noticeKey: "notice-b", closeControlCount: 1 },
  ];
  let reads = 0;
  let clicks = 0;
  await assert.rejects(
    dismissJdNoticeWithBoundedRetry(
      async () => snapshots[Math.min(reads++, snapshots.length - 1)]!,
      async () => { clicks += 1; },
      async () => undefined,
    ),
    /发生变化/,
  );
  assert.equal(clicks, 1);
});

test("download-center baseline does not accept its first empty loading snapshot", async () => {
  const snapshots = [
    { rows: [], emptyConfirmed: false },
    { rows: [], emptyConfirmed: false },
    { rows: [{ fingerprint: "old" }], emptyConfirmed: false },
    { rows: [{ fingerprint: "old" }], emptyConfirmed: false },
  ];
  let read = 0;
  const baseline = await waitForStableTaskBaseline(async () => snapshots[Math.min(read++, snapshots.length - 1)]!, async () => undefined);
  assert.deepEqual(baseline, [{ fingerprint: "old" }]);
});

test("download-center baseline tolerates a prolonged non-confirmed loading state", async () => {
  const snapshots = [
    ...Array.from({ length: 12 }, () => ({ rows: [], emptyConfirmed: false })),
    { rows: [{ fingerprint: "old" }], emptyConfirmed: false },
    { rows: [{ fingerprint: "old" }], emptyConfirmed: false },
  ];
  let read = 0;
  const baseline = await waitForStableTaskBaseline(async () => snapshots[Math.min(read++, snapshots.length - 1)]!, async () => undefined);
  assert.deepEqual(baseline, [{ fingerprint: "old" }]);
});

test("download-center confirmed empty state returns quickly without a fixed delay", async () => {
  let reads = 0;
  const baseline = await waitForStableTaskBaseline(async () => ({ rows: [], emptyConfirmed: (++reads) >= 1 }), async () => undefined);
  assert.deepEqual(baseline, []);
  assert.equal(reads, 2);
});

test("download-center accepts an empty target-range baseline after other task rows prove the table loaded", async () => {
  let reads = 0;
  const baseline = await waitForStableTaskBaseline(
    async () => ({ rows: [], emptyConfirmed: false, tableReady: (++reads) >= 1 }),
    async () => undefined,
  );
  assert.deepEqual(baseline, []);
  assert.equal(reads, 2);
});

test("daily auto-import rejects a batch whose coverage is not the requested interval", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "jdsz-import-"));
  const file = path.join(directory, "daily.xlsx");
  try {
    await writeFile(file, "workbook");
    const options = { baseUrl: "http://localhost:3000", shopName: "示例店", dimension: "SKU" as const, startDate: "2026-07-01", endDate: "2026-07-02" };
    await assert.rejects(
      importJdProductDetailFile(options, file, async () => Response.json({ ok: true, status: "imported", batch: { id: "b", source: "jd_sku_daily", dataset: "sku_daily", platform: "京东", shopName: "示例店", status: "completed", warningCount: 0, rowCount: 2, dateMin: "2026-07-01", dateMax: "2026-07-01" } }, { status: 201 })),
      /failed validation/,
    );
    const result = await importJdProductDetailFile(options, file, async () => Response.json({ ok: true, status: "duplicate", batch: { id: "b", source: "jd_sku_daily", dataset: "sku_daily", platform: "京东", shopName: "示例店", status: "completed", warningCount: 0, rowCount: 2, dateMin: "2026-07-01", dateMax: "2026-07-02" } }, { status: 200 }));
    assert.deepEqual(result, { status: "duplicate", batchId: "b", rowCount: 2, warningCount: 0, dateMin: "2026-07-01", dateMax: "2026-07-02", source: "jd_sku_daily", dataset: "sku_daily", platform: "京东", shopName: "示例店", batchStatus: "completed" });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("task manifest path rejects traversal-shaped shop ids", () => {
  assert.throws(() => taskManifestPath({ dimension: "SKU", shopId: "../outside", startDate: "2026-07-01", endDate: "2026-07-02" }), /invalid/);
});

test("daily import requires imported=201 and duplicate=200 exactly", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "jdsz-import-"));
  const file = path.join(directory, "daily.xlsx");
  try {
    await writeFile(file, "workbook");
    const options = { baseUrl: "http://localhost:3000", shopName: "示例店", dimension: "SKU" as const, startDate: "2026-07-01", endDate: "2026-07-01" };
    const payload = { ok: true, status: "imported", batch: { id: "b", source: "jd_sku_daily", dataset: "sku_daily", platform: "京东", shopName: "示例店", status: "completed", warningCount: 0, rowCount: 1, dateMin: "2026-07-01", dateMax: "2026-07-01" } };
    await assert.rejects(importJdProductDetailFile(options, file, async () => Response.json(payload, { status: 200 })), /failed validation/);
    await assert.rejects(importJdProductDetailFile(options, file, async () => Response.json({ ...payload, batch: { ...payload.batch, rowCount: 0 } }, { status: 201 })), /failed validation/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("submitting manifest records the confirmation time, not the older baseline time", () => {
  const preparedAt = new Date("2026-07-20T00:00:00.000Z");
  const confirmedAt = new Date("2026-07-20T00:03:00.000Z");
  const manifest = createSubmittingTaskManifest({ dimension: "SKU", storeKey: "jd-yiyong-director", shopId: "701455", shopName: "志高商用设备旗舰店", startDate: "2026-07-01", endDate: "2026-07-02" }, [{ fingerprint: "old" }], confirmedAt);
  assert.notEqual(manifest.createdAt, preparedAt.toISOString());
  assert.equal(manifest.createdAt, confirmedAt.toISOString());
  assert.equal(manifest.version, 2);
  assert.equal(manifest.storeKey, "jd-yiyong-director");
  assert.equal(manifest.shopName, "志高商用设备旗舰店");
  assert.deepEqual(manifest.baseline, ["old"]);
});

test("JD Business Intelligence store identity is derived from the unique visible mall link", () => {
  const identity = parseJdProductDetailStoreIdentity([
    { href: "//mall.jd.com/index-711743.html", text: "志高商用洗碗机旗舰店\nPOP" },
  ]);
  assert.deepEqual(identity, { shopId: "711743", shopName: "志高商用洗碗机旗舰店" });
  assert.throws(
    () => assertJdProductDetailStoreIdentity(identity, { shopId: "701455", shopName: "志高商用设备旗舰店" }),
    /店铺身份不一致.*701455.*711743/,
  );
});

test("JD Business Intelligence store identity fails closed when the header is absent or ambiguous", () => {
  assert.throws(() => parseJdProductDetailStoreIdentity([]), /实际识别 0 个/);
  assert.throws(() => parseJdProductDetailStoreIdentity([
    { href: "//mall.jd.com/index-701455.html", text: "志高商用设备旗舰店 POP" },
    { href: "//mall.jd.com/index-711743.html", text: "志高商用洗碗机旗舰店 POP" },
  ]), /实际识别 2 个/);
});
