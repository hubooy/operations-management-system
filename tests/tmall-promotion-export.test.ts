import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertPromotionCoveragePayload,
  assertNoPendingPromotionForSkip,
  assertPromotionImportPayload,
  buildTmallPromotionItemReportUrl,
  buildTmallPromotionCoverageUrl,
  clickCalendarMonthArrowWithFallback,
  chooseTmallPromotionDownloadTask,
  chooseTmallPromotionGeneratedTask,
  chooseTmallPromotionEntryPageIndex,
  fetchTmallPromotionCoverage,
  isPromotionMetricSelectionState,
  isPromotionDateRangeControlText,
  isPromotionDownloadDialogText,
  isPromotionDownloadActionAligned,
  isPromotionDownloadActionOwnedByRowBand,
  isPromotionReportSuccessNavigation,
  isSafePromotionDismissLabel,
  isTmallPromotionDimensionSelection,
  isTmallPromotionItemReportUrl,
  isTmallPromotionMarketingSceneSelection,
  parsePromotionTaskDateRange,
  parsePromotionTaskRowIdentity,
  parsePromotionSelectedCount,
  openPromotionDialogWithRetry,
  planTmallPromotionDailyReports,
  planTmallPromotionDateRange,
  promotionDateRangeControlMatches,
  promotionLabeledControlSemanticScore,
  promotionNativeDialogAction,
  sanitizePromotionNativeDialogMessage,
  promotionDatePickerRole,
  promotionAuditProtocolDisposition,
  promotionSuccessNavigationMissingMessage,
  reacquireTmallPromotionDownloadTask,
  retryStablePromotionDownloadTask,
  runPromotionDailyPlansSequentially,
  runTmallPromotionStage,
  sanitizePromotionDiagnosticUrl,
  shouldRedownloadUnverifiedPromotionFile,
  shouldRecoverSubmittedPromotionTask,
  TMALL_PROMOTION_DOWNLOAD_LIST_URL,
  TMALL_PROMOTION_ENTRY_URL,
  TMALL_PROMOTION_REPORT_PROTOCOL,
  verifyTmallPromotionCoverageAfterImport,
  waitForPromotionReportReadinessWithRecovery,
} from "../tools/tmall-promotion-export";

function requestedPeriodFromFetchInput(input: Parameters<typeof fetch>[0]) {
  const url = new URL(typeof input === "string" || input instanceof URL ? input.toString() : input.url);
  return {
    startDate: url.searchParams.get("startDate")!,
    endDate: url.searchParams.get("endDate")!,
  };
}

test("推广入口严格选择阿里妈妈商品报表页并排除相似登录域名", () => {
  assert.equal(chooseTmallPromotionEntryPageIndex([
    "https://loginmyseller.taobao.com/?redirect_url=on_sale",
    "https://one.alimama.com/index.html#!/report/download-list",
    "https://one.alimama.com/index.html#!/report/item_promotion?rptType=item_promotion",
  ]), 2);
  assert.equal(chooseTmallPromotionEntryPageIndex([
    "https://loginmyseller.taobao.com/?redirect_url=on_sale",
    "https://one.alimama.com/index.html",
  ]), 1);
  assert.equal(chooseTmallPromotionEntryPageIndex([
    "https://loginmyseller.taobao.com/?redirect_url=on_sale",
  ]), -1);
});

test("推广目标日期排除已有覆盖，并为每个缺失业务日生成独立报表", () => {
  const productDailyDates = [
    "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
    "2026-08-02", "2026-08-03",
  ];
  const plans = planTmallPromotionDailyReports({
    requestedStartDate: "2026-07-28",
    requestedEndDate: "2026-08-04",
    productDailyDates,
    promotionDates: ["2026-07-28"],
  });
  assert.deepEqual(plans, [
    { startDate: "2026-07-29", endDate: "2026-07-29", dates: ["2026-07-29"] },
    { startDate: "2026-07-30", endDate: "2026-07-30", dates: ["2026-07-30"] },
    { startDate: "2026-07-31", endDate: "2026-07-31", dates: ["2026-07-31"] },
    { startDate: "2026-08-02", endDate: "2026-08-02", dates: ["2026-08-02"] },
    { startDate: "2026-08-03", endDate: "2026-08-03", dates: ["2026-08-03"] },
  ]);
  assert.deepEqual(planTmallPromotionDateRange({
    requestedStartDate: "2026-07-28",
    requestedEndDate: "2026-08-04",
    productDailyDates,
    promotionDates: ["2026-07-28"],
  }), plans[0]);

  const longDates = Array.from({ length: 40 }, (_, index) => {
    const date = new Date("2026-01-01T00:00:00Z");
    date.setUTCDate(date.getUTCDate() + index);
    return date.toISOString().slice(0, 10);
  });
  assert.equal(planTmallPromotionDailyReports({
    requestedStartDate: longDates[0]!,
    requestedEndDate: longDates[longDates.length - 1]!,
    productDailyDates: longDates,
    promotionDates: [],
  }).length, 30);
  assert.deepEqual(planTmallPromotionDailyReports({
    requestedStartDate: "2026-07-28",
    requestedEndDate: "2026-08-04",
    productDailyDates,
    promotionDates: productDailyDates,
  }), []);
});

test("推广显式日期只有明确强制才重复执行且仍要求商品日先覆盖", () => {
  const input = {
    requestedStartDate: "2026-08-01",
    requestedEndDate: "2026-08-05",
    productDailyDates: ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"],
    promotionDates: ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"],
  } as const;
  assert.deepEqual(planTmallPromotionDailyReports({ ...input, requestedDates: ["2026-08-01"] }), []);
  assert.deepEqual(planTmallPromotionDailyReports({
    ...input,
    requestedDates: ["2026-08-05", "2026-08-01", "2026-08-03", "2026-08-01"],
    forceExistingDates: true,
  }), [
    { startDate: "2026-08-01", endDate: "2026-08-01", dates: ["2026-08-01"] },
    { startDate: "2026-08-03", endDate: "2026-08-03", dates: ["2026-08-03"] },
    { startDate: "2026-08-05", endDate: "2026-08-05", dates: ["2026-08-05"] },
  ]);
  assert.deepEqual(planTmallPromotionDailyReports({
    ...input,
    forceExistingDates: true,
  }), input.productDailyDates.map((date) => ({ startDate: date, endDate: date, dates: [date] })));
  assert.throws(() => planTmallPromotionDailyReports({
    ...input,
    requestedDates: ["2026-08-03", "2026-08-04"],
    productDailyDates: ["2026-08-03"],
    forceExistingDates: true,
  }), /缺少商品日覆盖：2026-08-04/);
  assert.throws(() => planTmallPromotionDailyReports({
    ...input,
    requestedDates: ["2026-08-01", "2026-08-02"],
    forceExistingDates: true,
    maximumDays: 1,
  }), /超过单轮 1 天上限/);
});

test("推广无缺口不能跳过活动清单，跨店隔离且原证据不变", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-gap-"));
  try {
    const file = path.join(directory, "active-tmall-lili.json");
    const original = JSON.stringify({ stage: "export_submitted", date: "2026-08-01" });
    await writeFile(file, original);
    await assert.rejects(assertNoPendingPromotionForSkip("tmall-lili", [directory]), /活动清单/);
    await assertNoPendingPromotionForSkip("tmall-tuofeng", [directory]);
    assert.equal(await readFile(file, "utf8"), original);

    const completed = JSON.stringify({
      version: 2,
      runId: "completed-run",
      storeKey: "tmall-lili",
      stage: "completed",
      startDate: "2026-08-01",
      endDate: "2026-08-01",
      dates: ["2026-08-01"],
      batchId: "tmall_promotion:completed",
      importStatus: "imported",
      warningCount: 0,
      file: {
        fileName: "completed.zip",
        filePath: "D:\\controlled\\completed.zip",
        size: 128,
        sha256: "a".repeat(64),
        rowCount: 10,
        dateMin: "2026-08-01",
        dateMax: "2026-08-01",
      },
    });
    await writeFile(file, completed);
    await assertNoPendingPromotionForSkip("tmall-lili", [directory]);
    assert.equal(await readFile(file, "utf8"), completed);

    await writeFile(file, JSON.stringify({ ...JSON.parse(completed), batchId: undefined }));
    await assert.rejects(assertNoPendingPromotionForSkip("tmall-lili", [directory]), /活动清单/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("推广日报严格串行执行且任意一天失败后不再处理后续日期", async () => {
  const plans = ["2026-08-01", "2026-08-02", "2026-08-03"].map((date) => ({
    startDate: date,
    endDate: date,
    dates: [date],
  }));
  const visited: string[] = [];
  await assert.rejects(runPromotionDailyPlansSequentially(plans, async (plan) => {
    visited.push(plan.startDate);
    if (plan.startDate === "2026-08-02") throw new Error("day_failed");
    return plan.startDate;
  }), /day_failed/);
  assert.deepEqual(visited, ["2026-08-01", "2026-08-02"]);

  await assert.rejects(runPromotionDailyPlansSequentially([{
    startDate: "2026-08-01",
    endDate: "2026-08-02",
    dates: ["2026-08-01", "2026-08-02"],
  }], async () => "should_not_run"), /必须按单个业务日下载/);
});

test("下载任务日期同时兼容同日起止范围和带标签的单日展示", () => {
  assert.deepEqual(parsePromotionTaskDateRange("日期范围 2026-08-05至2026-08-05 创建日期 2026-08-06"), {
    startDate: "2026-08-05",
    endDate: "2026-08-05",
  });
  assert.deepEqual(parsePromotionTaskDateRange("数据日期：2026-08-05 创建日期 2026-08-06"), {
    startDate: "2026-08-05",
    endDate: "2026-08-05",
  });
  assert.equal(parsePromotionTaskDateRange("创建日期 2026-08-06"), null);
});

test("下载任务行必须只有一个文件名和一个业务日期范围", () => {
  const identity = parsePromotionTaskRowIdentity(
    "商品报表_20260827_044500 2026-08-25 至 2026-08-25 生成成功 下载",
  );
  assert.equal(identity?.fileName, "商品报表_20260827_044500");
  assert.equal(identity?.startDate, "2026-08-25");
  assert.equal(identity?.endDate, "2026-08-25");
  assert.equal(parsePromotionTaskRowIdentity(
    "商品报表_20260827_044500 2026-08-25 至 2026-08-25 下载 商品报表_20260822_120830 2026-08-21 至 2026-08-21 下载",
  ), null);
});

test("已提交推广任务只允许对纯日期错配文件受控重下发一次", () => {
  const mismatch = new Error("推广 ZIP 内容、日期或店铺校验失败：MISSING_EXPECTED_DATES, OUT_OF_RANGE_DATES");
  assert.equal(shouldRedownloadUnverifiedPromotionFile(mismatch, 1), true);
  assert.equal(shouldRedownloadUnverifiedPromotionFile(mismatch, 2), false);
  assert.equal(shouldRedownloadUnverifiedPromotionFile(
    new Error("推广 ZIP 内容、日期或店铺校验失败：UNSUPPORTED_SUBJECT_TYPE, MISSING_EXPECTED_DATES, OUT_OF_RANGE_DATES"),
    1,
  ), false);
  assert.equal(shouldRedownloadUnverifiedPromotionFile(new Error("恢复清单中的未校验商品推广文件已变化"), 1), false);
});

test("下载中心只选择本轮日期、创建时间、成功状态和唯一下载动作一致的任务", () => {
  const runStartedAt = new Date(Date.now() - 30_000).toISOString();
  const createdAt = new Date(Date.now() - 5_000).toISOString();
  const common = {
    fileName: "商品报表_20260805_230918",
    reportName: "商品报表",
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    createdAt,
    downloadReady: true,
  };
  const candidates = [
    { ...common, signature: "old-range", startDate: "2026-07-28", status: "生成成功" },
    { ...common, signature: "pending", status: "生成中" },
    { ...common, signature: "current", status: "生成成功" },
  ];
  assert.equal(chooseTmallPromotionDownloadTask(candidates, {
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    runStartedAt,
  }), "current");

  assert.equal(chooseTmallPromotionDownloadTask([
    { ...common, signature: "same-time-a", status: "生成成功" },
    { ...common, signature: "same-time-b", status: "生成成功" },
  ], {
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    runStartedAt,
  }), null);
  assert.equal(chooseTmallPromotionDownloadTask([
    { ...common, signature: "newer", status: "生成成功" },
    { ...common, signature: "older", status: "生成成功", createdAt: new Date(Date.now() - 10_000).toISOString() },
  ], {
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    runStartedAt,
  }), null);
  assert.equal(chooseTmallPromotionDownloadTask([{
    ...common,
    signature: "legacy-full-site-report",
    fileName: "报表_20260805_230918",
    reportName: "未知报表",
    status: "生成成功",
  }], {
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    runStartedAt,
  }), null);
  assert.match(TMALL_PROMOTION_DOWNLOAD_LIST_URL, /#!\/report\/download-list$/);
});

test("生成完成但悬浮下载按钮尚未绑定时只允许定位任务行而不允许直接下载", () => {
  const runStartedAt = new Date(Date.now() - 30_000).toISOString();
  const task = {
    signature: "generated-without-action",
    fileName: "商品报表_20260827_044452",
    reportName: "商品报表",
    status: "生成成功",
    startDate: "2026-08-25",
    endDate: "2026-08-25",
    createdAt: new Date(Date.now() - 5_000).toISOString(),
    downloadReady: false,
  };
  const expected = { startDate: task.startDate, endDate: task.endDate, runStartedAt };
  assert.equal(chooseTmallPromotionGeneratedTask([task], expected), task.signature);
  assert.equal(chooseTmallPromotionDownloadTask([task], expected), null);
});

test("悬浮下载按钮只绑定垂直对齐且位于任务行右侧的唯一动作", () => {
  const row = { x: 100, y: 200, width: 1_000, height: 52 };
  assert.equal(isPromotionDownloadActionAligned(row, { x: 1_000, y: 210, width: 60, height: 28 }), true);
  assert.equal(isPromotionDownloadActionAligned(row, { x: 1_000, y: 270, width: 60, height: 28 }), false);
  assert.equal(isPromotionDownloadActionAligned(row, { x: 150, y: 210, width: 60, height: 28 }), false);
});

test("虚拟表格展开操作行按当前任务至下一任务的独占纵向区间绑定", () => {
  const firstRow = { x: -367, y: 319, width: 1_290, height: 41 };
  const secondRow = { x: -367, y: 401, width: 1_290, height: 41 };
  const expandedAction = { x: -343, y: 369, width: 49, height: 24 };
  assert.equal(isPromotionDownloadActionOwnedByRowBand(firstRow, secondRow.y - 2, expandedAction), true);
  assert.equal(isPromotionDownloadActionOwnedByRowBand(secondRow, 480, expandedAction), false);
  assert.equal(isPromotionDownloadActionOwnedByRowBand(firstRow, secondRow.y - 2, {
    ...expandedAction,
    x: 1_000,
  }), false);
});

test("下载点击前只重新绑定同一个稳定任务且拒绝消失或重复候选", () => {
  const task = { signature: "current", downloadReady: true, marker: "fresh" };
  assert.equal(reacquireTmallPromotionDownloadTask([task], "current"), task);
  assert.equal(reacquireTmallPromotionDownloadTask([{ ...task, downloadReady: false }], "current"), null);
  assert.equal(reacquireTmallPromotionDownloadTask([task, { ...task, marker: "duplicate" }], "current"), null);
  assert.equal(reacquireTmallPromotionDownloadTask([task], "other"), null);
});

test("虚拟下载任务行刷新时有限重绑同一任务且绝不接受身份不一致候选", async () => {
  let acquisitions = 0;
  const waits: number[] = [];
  const recovered = await retryStablePromotionDownloadTask({
    acquire: async () => {
      acquisitions += 1;
      return acquisitions < 3 ? null : { signature: "stable-task", identity: "expected" };
    },
    verify: async (candidate) => candidate.signature === "stable-task" && candidate.identity === "expected",
    attempts: 4,
    delayMs: 25,
    wait: async (delayMs) => { waits.push(delayMs); },
  });
  assert.deepEqual(recovered, { signature: "stable-task", identity: "expected" });
  assert.equal(acquisitions, 3);
  assert.deepEqual(waits, [25, 25]);

  acquisitions = 0;
  const rejected = await retryStablePromotionDownloadTask({
    acquire: async () => {
      acquisitions += 1;
      return { signature: "stable-task", identity: "other" };
    },
    verify: async (candidate) => candidate.identity === "expected",
    attempts: 3,
    delayMs: 0,
    wait: async () => undefined,
  });
  assert.equal(rejected, null);
  assert.equal(acquisitions, 3);
});

test("平台消息和广告弹窗只允许无业务副作用的明确关闭动作", () => {
  for (const label of ["关闭", "忽略", "暂不", "稍后", "以后再说", "我知道了", "×", "close"]) {
    assert.equal(isSafePromotionDismissLabel(label), true, label);
  }
  for (const label of ["去优化", "立即处理", "立即报名", "查看详情", "前往下载", "开通"]) {
    assert.equal(isSafePromotionDismissLabel(label), false, label);
  }
});

test("下载报表弹窗同时兼容旧版和新版语义结构但拒绝不完整页面", () => {
  assert.equal(isPromotionDownloadDialogText("下载报表 日期范围 数据指标 确定"), true);
  assert.equal(isPromotionDownloadDialogText("报表下载 统计日期 报表指标 确认生成"), true);
  assert.equal(isPromotionDownloadDialogText("生成报表 开始日期 结束日期 全部数据指标 生成报表"), true);
  assert.equal(isPromotionDownloadDialogText("下载报表 历史任务 下载"), false);
  assert.equal(isPromotionDownloadDialogText("日期范围 数据指标 确定"), false);
  assert.equal(isPromotionDownloadDialogText("生成报表 日期范围 数据指标"), false);
});

test("新版商品报表日期控件兼容绝对日期到昨日且拒绝无关日期文本", () => {
  assert.equal(isPromotionDateRangeControlText("2026-08-24 至 昨日"), true);
  assert.equal(isPromotionDateRangeControlText("日期范围：2026-08-24 至 2026-08-25"), true);
  assert.equal(isPromotionDateRangeControlText("2026-08-25"), true);
  assert.equal(isPromotionDateRangeControlText("过去 7 天"), true);
  assert.equal(isPromotionDateRangeControlText("商品数据范围 2026-08-24"), false);
  assert.equal(isPromotionDateRangeControlText("2026-08-24 商品数据明细"), false);
});

test("下载报表日期确认后必须精确读回目标单日", () => {
  const expected = { startDate: "2026-08-25", endDate: "2026-08-25", yesterday: "2026-08-26" };
  assert.equal(promotionDateRangeControlMatches("2026-08-25", expected), true);
  assert.equal(promotionDateRangeControlMatches("日期范围：2026-08-25 至 2026-08-25", expected), true);
  assert.equal(promotionDateRangeControlMatches("昨日", {
    startDate: "2026-08-26",
    endDate: "2026-08-26",
    yesterday: "2026-08-26",
  }), true);
  assert.equal(promotionDateRangeControlMatches("昨日", expected), false);
  assert.equal(promotionDateRangeControlMatches("昨日", {
    startDate: "2026-08-25",
    endDate: "2026-08-26",
    yesterday: "2026-08-26",
  }), false);
  assert.equal(promotionDateRangeControlMatches("2026-08-25 至 昨日", expected), false);
  assert.equal(promotionDateRangeControlMatches("2026-08-21 至 2026-08-21", expected), false);
});

test("下载报表弹窗握手只允许一次安全重试且不会重复已有弹窗", async () => {
  const events: string[] = [];
  let waitCount = 0;
  const recovered = await openPromotionDialogWithRetry({
    findDialog: async () => null,
    click: async () => { events.push("click"); },
    waitForDialog: async (attempt) => {
      events.push(`wait-${attempt}`);
      waitCount += 1;
      return waitCount === 2 ? { id: "dialog" } : null;
    },
    beforeRetry: async () => { events.push("identity-check"); },
    onAttempt: async (attempt) => { events.push(`attempt-${attempt}`); },
  });
  assert.deepEqual(recovered, { dialog: { id: "dialog" }, attempts: 2 });
  assert.deepEqual(events, [
    "attempt-1", "click", "wait-1", "identity-check",
    "attempt-2", "click", "wait-2",
  ]);

  let clicks = 0;
  const existing = await openPromotionDialogWithRetry({
    findDialog: async () => ({ id: "existing" }),
    click: async () => { clicks += 1; },
    waitForDialog: async () => null,
    beforeRetry: async () => undefined,
  });
  assert.deepEqual(existing, { dialog: { id: "existing" }, attempts: 0 });
  assert.equal(clicks, 0);
});

test("下载报表弹窗连续两次无响应后失败关闭且诊断地址移除查询参数", async () => {
  let clicks = 0;
  let retries = 0;
  await assert.rejects(openPromotionDialogWithRetry({
    findDialog: async () => null,
    click: async () => { clicks += 1; },
    waitForDialog: async () => null,
    beforeRetry: async () => { retries += 1; },
  }), /连续两次未出现.*未提交报表任务/);
  assert.equal(clicks, 2);
  assert.equal(retries, 1);
  assert.equal(
    sanitizePromotionDiagnosticUrl("https://one.alimama.com/index.html?spm=secret#!/report/download-list?token=secret"),
    "https://one.alimama.com/index.html#!/report/download-list",
  );
  assert.equal(sanitizePromotionDiagnosticUrl("not a url"), "invalid-url");
});

test("原生对话框只自动处理明确的无数据信息，未知文案必须停止", () => {
  assert.equal(promotionNativeDialogAction({ type: "alert", message: "提示：暂无数据。" }), "dismiss");
  assert.equal(promotionNativeDialogAction({ type: "alert", message: "暂无可下载数据" }), "dismiss");
  assert.equal(promotionNativeDialogAction({ type: "confirm", message: "是否创建报表？" }), "stop");
  assert.equal(promotionNativeDialogAction({ type: "alert", message: "请完成验证码" }), "stop");
  assert.equal(
    sanitizePromotionNativeDialogMessage("是否离开 https://example.com/path?token=secret abcdefghijklmnopqrstuvwxyz123456 13800138000 test@example.com"),
    "是否离开 [链接] [标识已脱敏] [手机号已脱敏] [邮箱已脱敏]",
  );
});

test("全部数据指标必须能从 checked 或 selected 状态得到确认", () => {
  assert.equal(isPromotionMetricSelectionState({ checked: true }), true);
  assert.equal(isPromotionMetricSelectionState({ attributeValues: ["false", "true"] }), true);
  assert.equal(isPromotionMetricSelectionState({ classNames: ["ant-checkbox-wrapper ant-checkbox-wrapper-checked"] }), true);
  assert.equal(isPromotionMetricSelectionState({ checked: false, attributeValues: ["false"], classNames: ["ant-checkbox-wrapper"] }), false);
  assert.equal(isPromotionMetricSelectionState({ classNames: ["unchecked unselected"] }), false);
});

test("确认报表后只点击生成成功提示中的前往下载动作", () => {
  const successNotice = "离线数据生成成功！您可以在下载任务管理中将报表内容保存到本地，文案仅为示意 立即前往";
  assert.equal(isPromotionReportSuccessNavigation({ label: "立即前往", context: successNotice }), true);
  assert.equal(isPromotionReportSuccessNavigation({ label: "前往下载", context: successNotice }), true);
  assert.equal(isPromotionReportSuccessNavigation({ label: "立即前往", context: "全站流量打爆，立即前往" }), false);
  assert.equal(isPromotionReportSuccessNavigation({ label: "查看详情", context: successNotice }), false);
  assert.equal(isSafePromotionDismissLabel("立即前往"), false);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error(promotionSuccessNavigationMissingMessage)), true);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error("点击前往下载后出现多个下载任务页面，为防止接管错误页面已停止")), true);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error("点击前往下载后未进入下载任务管理页面")), true);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error("locator.click: Timeout 10000ms exceeded. element was detached from the DOM while clicking 立即前往")), true);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error("报表生成成功提示中存在多个前往下载操作，为防止误点已停止")), true);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error("下载任务存在多个相同日期候选，为防止误点已停止")), false);
  assert.equal(shouldRecoverSubmittedPromotionTask(new Error("阿里妈妈登录身份与受控店铺不一致")), false);
});

test("推广报表固定使用阿里妈妈商品报表协议和精确日期路由", () => {
  assert.match(TMALL_PROMOTION_ENTRY_URL, /^https:\/\/one\.alimama\.com\/index\.html#!\/report\/item_promotion/);
  assert.match(TMALL_PROMOTION_DOWNLOAD_LIST_URL, /^https:\/\/one\.alimama\.com\//);
  const target = buildTmallPromotionItemReportUrl("2026-08-25", "2026-08-25");
  assert.equal(isTmallPromotionItemReportUrl(target, { startDate: "2026-08-25", endDate: "2026-08-25" }), true);
  assert.equal(isTmallPromotionItemReportUrl(target, { startDate: "2026-08-24", endDate: "2026-08-24" }), false);
  assert.equal(isTmallPromotionItemReportUrl(TMALL_PROMOTION_DOWNLOAD_LIST_URL), false);
  assert.match(target, /rptType=item_promotion/);
  assert.match(target, /startTime=2026-08-25/);
  assert.throws(() => buildTmallPromotionItemReportUrl("2026-08-26", "2026-08-25"), /日期范围无效/);
});

test("商品报表已就绪时不执行路由恢复", async () => {
  const events: string[] = [];
  const result = await waitForPromotionReportReadinessWithRecovery({
    waitForReady: async (phase) => {
      events.push(`wait:${phase}`);
      return true;
    },
    recoverRoute: async () => { events.push("recover"); },
    verifyIdentity: async () => { events.push("identity"); },
    dismissPopups: async () => {
      events.push("dismiss");
      return 1;
    },
    failureMessage: () => "not ready",
  });
  assert.deepEqual(result, { recovered: false, dismissedPopups: 0 });
  assert.deepEqual(events, ["wait:initial"]);
});

test("商品报表路由漂移后只恢复一次并重新核验身份再操作页面", async () => {
  const events: string[] = [];
  const result = await waitForPromotionReportReadinessWithRecovery({
    waitForReady: async (phase) => {
      events.push(`wait:${phase}`);
      return phase === "recovered";
    },
    recoverRoute: async () => { events.push("recover"); },
    verifyIdentity: async () => { events.push("identity"); },
    dismissPopups: async () => {
      events.push("dismiss");
      return 2;
    },
    failureMessage: () => "not ready",
  });
  assert.deepEqual(result, { recovered: true, dismissedPopups: 2 });
  assert.deepEqual(events, ["wait:initial", "recover", "identity", "dismiss", "wait:recovered"]);
});

test("商品报表受控恢复后仍未就绪则失败关闭且不重复恢复", async () => {
  const events: string[] = [];
  await assert.rejects(waitForPromotionReportReadinessWithRecovery({
    waitForReady: async (phase) => {
      events.push(`wait:${phase}`);
      return false;
    },
    recoverRoute: async () => { events.push("recover"); },
    verifyIdentity: async () => { events.push("identity"); },
    dismissPopups: async () => {
      events.push("dismiss");
      return 0;
    },
    failureMessage: () => "一次受控恢复后仍未加载完成，未开始报表业务操作",
  }), /一次受控恢复后仍未加载完成.*未开始报表业务操作/);
  assert.deepEqual(events, ["wait:initial", "recover", "identity", "dismiss", "wait:recovered"]);
});

test("商品报表恢复后的身份核验失败时不继续页面等待", async () => {
  const events: string[] = [];
  await assert.rejects(waitForPromotionReportReadinessWithRecovery({
    waitForReady: async (phase) => {
      events.push(`wait:${phase}`);
      return false;
    },
    recoverRoute: async () => { events.push("recover"); },
    verifyIdentity: async () => {
      events.push("identity");
      throw new Error("shop_identity_mismatch");
    },
    dismissPopups: async () => {
      events.push("dismiss");
      return 0;
    },
    failureMessage: () => "not ready",
  }), /shop_identity_mismatch/);
  assert.deepEqual(events, ["wait:initial", "recover", "identity"]);
});

test("商品报表必须精确选择全部营销场景与商品计划两个维度", () => {
  assert.equal(isTmallPromotionMarketingSceneSelection(["人群推广", "货品全站推广", "店铺直达", "关键词推广"]), true);
  assert.equal(isTmallPromotionMarketingSceneSelection(["货品全站推广", "关键词推广", "人群推广"]), false);
  assert.equal(isTmallPromotionMarketingSceneSelection(["货品全站推广", "关键词推广", "人群推广", "店铺直达", "内容营销"]), false);
  assert.equal(isTmallPromotionDimensionSelection(["计划", "商品"]), true);
  assert.equal(isTmallPromotionDimensionSelection(["商品"]), false);
});

test("营销场景全选以页面已选数量作为四类精确回读证据", () => {
  assert.equal(parsePromotionSelectedCount("全选 已选：4 货品全站推广 关键词推广 人群推广 店铺直达"), 4);
  assert.equal(parsePromotionSelectedCount("已选 4"), 4);
  assert.equal(parsePromotionSelectedCount("已选：3"), 3);
  assert.equal(parsePromotionSelectedCount("已选：4 已选：3"), null);
  assert.equal(parsePromotionSelectedCount("没有选择数量"), null);
});

test("营销场景真实多选框必须优先于同一行的箭头图标", () => {
  const iconScore = promotionLabeledControlSemanticScore(68.3, "");
  const selectorScore = promotionLabeledControlSemanticScore(
    68,
    "货品全站推广 关键词推广 人群推广 店铺直达",
  );
  assert.ok(selectorScore > iconScore);
});

test("新版商品报表不会接管旧全站推已提交或已下载的活动清单", () => {
  assert.equal(promotionAuditProtocolDisposition({
    version: 2,
    reportProtocol: TMALL_PROMOTION_REPORT_PROTOCOL,
    stage: "report_submitted",
  }), "reuse");
  assert.equal(promotionAuditProtocolDisposition({ version: 1, stage: "completed" }), "replace_pre_submit");
  assert.equal(promotionAuditProtocolDisposition({ version: 1, stage: "report_configured" }), "replace_pre_submit");
  assert.equal(promotionAuditProtocolDisposition({ version: 1, stage: "report_submitted" }), "block_existing_business_action");
  assert.equal(promotionAuditProtocolDisposition({ version: 1, stage: "failed", resumeStage: "downloaded" }), "block_existing_business_action");
});

test("推广日期弹层能识别自定义组件的起止控件", () => {
  assert.equal(promotionDatePickerRole("mx_output_x\u001emagix-portsaH({trigger:'start'})"), "start");
  assert.equal(promotionDatePickerRole('mx_output_x\u001emagix-portsaH({trigger:"end"})'), "end");
  assert.equal(promotionDatePickerRole("mx_output_x\u001emagix-portsaH()"), null);
});

test("日期月份主点击超时但已经生效时不重复点击，未生效时才使用受控后备点击", async () => {
  let month = "2026-07";
  let fallbackClicks = 0;
  const changed = await clickCalendarMonthArrowWithFallback({
    beforeMonth: month,
    targetMonth: "2026-08",
    click: async () => {
      month = "2026-08";
      throw new Error("actionability timeout after click");
    },
    fallbackClick: async () => { fallbackClicks += 1; },
    readMonth: async () => month,
    primaryWaitMs: 5,
    finalWaitMs: 5,
  });
  assert.equal(changed, "2026-08");
  assert.equal(fallbackClicks, 0);

  month = "2026-07";
  const recovered = await clickCalendarMonthArrowWithFallback({
    beforeMonth: month,
    targetMonth: "2026-08",
    click: async () => { throw new Error("element is unstable"); },
    fallbackClick: async () => {
      fallbackClicks += 1;
      month = "2026-08";
    },
    readMonth: async () => month,
    primaryWaitMs: 5,
    finalWaitMs: 5,
  });
  assert.equal(recovered, "2026-08");
  assert.equal(fallbackClicks, 1);
});

test("推广导入结果必须同时匹配来源、店铺、日期、行数和落库回查", () => {
  const payload = {
    ok: true,
    status: "imported",
    batch: {
      id: "batch-1",
      source: "tmall_promotion",
      dataset: "promotion_daily",
      platform: "天猫",
      shopName: "天猫-志高亿玖专卖店",
      status: "completed",
      rowCount: 12,
      warningCount: 1,
      dateMin: "2026-07-29",
      dateMax: "2026-08-04",
    },
    verification: {
      verified: true,
      parsedRowCount: 12,
      readbackRowCount: 12,
      dataset: "promotion_daily",
      platform: "天猫",
      shopName: "天猫-志高亿玖专卖店",
      dateMin: "2026-07-29",
      dateMax: "2026-08-04",
    },
  };
  const expected = {
    shopName: "天猫-志高亿玖专卖店",
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    rowCount: 12,
  };
  assert.deepEqual(assertPromotionImportPayload(payload, 201, expected), {
    batchId: "batch-1",
    status: "imported",
    warningCount: 1,
  });
  assert.deepEqual(assertPromotionImportPayload({ ...payload, status: "duplicate" }, 200, expected), {
    batchId: "batch-1",
    status: "duplicate",
    warningCount: 1,
  });
  const djangoPayload = {
    ...payload,
    verification: {
      verified: true,
      rowCount: 12,
      dataset: "promotion_daily",
      platform: "天猫",
      shopName: "天猫-志高亿玖专卖店",
      dateMin: "2026-07-29",
      dateMax: "2026-08-04",
    },
  };
  assert.deepEqual(assertPromotionImportPayload(djangoPayload, 201, expected), {
    batchId: "batch-1",
    status: "imported",
    warningCount: 1,
  });
  assert.deepEqual(assertPromotionImportPayload({
    ...djangoPayload,
    verification: { ...djangoPayload.verification, readbackRowCount: 12 },
  }, 201, expected), {
    batchId: "batch-1",
    status: "imported",
    warningCount: 1,
  });
  assert.deepEqual(assertPromotionImportPayload({
    ...djangoPayload,
    status: "duplicate",
    verification: { verified: true, rowCount: 12 },
  }, 200, expected), {
    batchId: "batch-1",
    status: "duplicate",
    warningCount: 1,
  });
  assert.throws(() => assertPromotionImportPayload(payload, 200, expected), /不一致/);
  assert.throws(() => assertPromotionImportPayload({
    ...payload,
    verification: { ...payload.verification, parsedRowCount: 11 },
  }, 201, expected), /不一致/);
  assert.throws(() => assertPromotionImportPayload({
    ...djangoPayload,
    verification: { ...djangoPayload.verification, rowCount: 11 },
  }, 201, expected), /不一致/);
  assert.throws(() => assertPromotionImportPayload({
    ...payload,
    batch: { ...payload.batch, shopName: "天猫-志高丽力专卖店" },
  }, 201, expected), /不一致/);
});

test("推广覆盖响应必须精确匹配请求周期并拒绝非法日期", () => {
  const expected = { startDate: "2026-07-28", endDate: "2026-08-04" };
  assert.deepEqual(assertPromotionCoveragePayload({
    requestedPeriod: expected,
    coverage: {
      productDailyDates: ["2026-07-29", "2026-07-28", "2026-07-29"],
      promotionDates: ["2026-07-28"],
    },
  }, expected), {
    productDailyDates: ["2026-07-28", "2026-07-29"],
    promotionDates: ["2026-07-28"],
  });
  assert.throws(() => assertPromotionCoveragePayload({
    requestedPeriod: { startDate: "2026-07-29", endDate: "2026-08-04" },
    coverage: { productDailyDates: [], promotionDates: [] },
  }, expected), /requestedPeriod/);
  assert.throws(() => assertPromotionCoveragePayload({
    requestedPeriod: expected,
    coverage: { productDailyDates: ["2026-02-30"], promotionDates: [] },
  }, expected), /非法日期/);
  assert.throws(() => assertPromotionCoveragePayload({
    requestedPeriod: expected,
    coverage: { productDailyDates: ["2026-07-27"], promotionDates: [] },
  }, expected), /请求区间外日期/);
});

test("推广覆盖回查使用轻量 overview 与平台店铺复合 outlet", () => {
  const url = new URL(buildTmallPromotionCoverageUrl(
    "http://localhost:3000",
    { shopName: "天猫-志高亿玖专卖店" },
    "2026-08-20",
    "2026-08-20",
  ));

  assert.equal(url.pathname, "/api/netshop/promotion-performance/overview");
  assert.deepEqual(url.searchParams.getAll("platform"), ["天猫"]);
  assert.equal(url.searchParams.get("outlet"), "天猫\u001f天猫-志高亿玖专卖店");
  assert.equal(url.searchParams.has("shop"), false);
  assert.equal(url.searchParams.has("page"), false);
  assert.equal(url.searchParams.has("pageSize"), false);
  assert.equal(url.searchParams.get("startDate"), "2026-08-20");
  assert.equal(url.searchParams.get("endDate"), "2026-08-20");
});

test("导入后推广覆盖回查只对瞬时超时做一次有界重试并仍要求覆盖证据", async () => {
  let requestCount = 0;
  const waits: number[] = [];
  const request = (async (input: Parameters<typeof fetch>[0]) => {
    requestCount += 1;
    if (requestCount === 1) {
      throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
    }
    const requestedPeriod = requestedPeriodFromFetchInput(input);
    return Response.json({
      requestedPeriod,
      coverage: {
        productDailyDates: [requestedPeriod.startDate],
        promotionDates: [requestedPeriod.startDate],
      },
    });
  }) as typeof fetch;

  const coverage = await verifyTmallPromotionCoverageAfterImport({
    baseUrl: "http://localhost:3000",
    store: { shopName: "天猫-志高亿玖专卖店" },
    startDate: "2026-08-26",
    endDate: "2026-08-26",
    dates: ["2026-08-26"],
    request,
    timeoutRetryDelaysMs: [1_000],
    wait: async (delayMs) => { waits.push(delayMs); },
  });

  assert.equal(requestCount, 2);
  assert.deepEqual(waits, [1_000]);
  assert.deepEqual(coverage.promotionDates, ["2026-08-26"]);
});

test("推广覆盖回查重试耗尽或仍缺日期时失败关闭，不能绕过落库回查", async () => {
  let timeoutRequestCount = 0;
  const timeoutRequest = (async () => {
    timeoutRequestCount += 1;
    throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
  }) as typeof fetch;
  await assert.rejects(verifyTmallPromotionCoverageAfterImport({
    baseUrl: "http://localhost:3000",
    store: { shopName: "天猫-志高亿玖专卖店" },
    startDate: "2026-08-26",
    endDate: "2026-08-26",
    dates: ["2026-08-26"],
    request: timeoutRequest,
    timeoutRetryDelaysMs: [0],
    wait: async () => undefined,
  }), /aborted due to timeout/);
  assert.equal(timeoutRequestCount, 2);

  let invalidCoverageRequestCount = 0;
  const invalidCoverageRequest = (async (input: Parameters<typeof fetch>[0]) => {
    invalidCoverageRequestCount += 1;
    return Response.json({
      requestedPeriod: requestedPeriodFromFetchInput(input),
      coverage: { productDailyDates: ["2026-08-26"], promotionDates: [] },
    });
  }) as typeof fetch;
  await assert.rejects(verifyTmallPromotionCoverageAfterImport({
    baseUrl: "http://localhost:3000",
    store: { shopName: "天猫-志高亿玖专卖店" },
    startDate: "2026-08-26",
    endDate: "2026-08-26",
    dates: ["2026-08-26"],
    request: invalidCoverageRequest,
    timeoutRetryDelaysMs: [0],
    wait: async () => undefined,
  }), /日期覆盖回查缺少：2026-08-26/);
  assert.equal(invalidCoverageRequestCount, 1);
});

test("推广计划前覆盖检查仅对瞬时超时做一次有界重试", async () => {
  let timeoutRequestCount = 0;
  const waits: number[] = [];
  const timeoutThenSuccessRequest = (async (input: Parameters<typeof fetch>[0]) => {
    timeoutRequestCount += 1;
    if (timeoutRequestCount === 1) {
      throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
    }
    const requestedPeriod = requestedPeriodFromFetchInput(input);
    return Response.json({
      requestedPeriod,
      coverage: {
        productDailyDates: [requestedPeriod.startDate],
        promotionDates: [requestedPeriod.startDate],
      },
    });
  }) as typeof fetch;
  const coverage = await fetchTmallPromotionCoverage({
    baseUrl: "http://localhost:3000",
    store: { shopName: "天猫-志高亿玖专卖店" },
    startDate: "2026-08-26",
    endDate: "2026-08-26",
    request: timeoutThenSuccessRequest,
    timeoutRetryDelaysMs: [1_000],
    wait: async (delayMs) => { waits.push(delayMs); },
  });
  assert.equal(timeoutRequestCount, 2);
  assert.deepEqual(waits, [1_000]);
  assert.deepEqual(coverage.productDailyDates, ["2026-08-26"]);

  let requestCount = 0;
  const request = (async () => {
    requestCount += 1;
    return new Response("unavailable", { status: 503 });
  }) as typeof fetch;
  await assert.rejects(fetchTmallPromotionCoverage({
    baseUrl: "http://localhost:3000",
    store: { shopName: "天猫-志高亿玖专卖店" },
    startDate: "2026-08-26",
    endDate: "2026-08-26",
    request,
  }), /HTTP 503/);
  assert.equal(requestCount, 1);
});

test("没有商品日覆盖时推广阶段明确等待并通过失败状态让调用链重试", async () => {
  const request = (async (input: Parameters<typeof fetch>[0]) => Response.json({
    requestedPeriod: requestedPeriodFromFetchInput(input),
    coverage: { productDailyDates: [], promotionDates: [] },
  })) as typeof fetch;
  await assert.rejects(runTmallPromotionStage({
    storeKey: "tmall-yijiu",
    baseUrl: "http://localhost:3000",
    request,
  }), /waiting_product_daily/);
});

test("已覆盖的推广日在下载前返回跳过，绝不调用导出；空计划覆盖变化失败关闭", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-promotion-covered-"));
  let exported = false;
  try {
    const request = (async (input: Parameters<typeof fetch>[0]) => Response.json({
      requestedPeriod: requestedPeriodFromFetchInput(input),
      coverage: { productDailyDates: ["2026-07-28"], promotionDates: ["2026-07-28"] },
    })) as typeof fetch;
    const result = await runTmallPromotionStage({ storeKey: "tmall-yijiu", request,
      auditDirectory: directory, dates: ["2026-07-28"],
      executeDate: async () => { exported = true; throw new Error("must_not_export"); } });
    assert.equal(result.status, "skipped");
    assert.equal(result.reason, "already_covered");
    assert.equal(result.reportCount, 0);
    assert.equal(exported, false);
    await assert.rejects(runTmallPromotionStage({ storeKey: "tmall-yijiu", request,
      auditDirectory: directory, dates: [],
      executeDate: async () => { exported = true; throw new Error("must_not_export"); } }), /覆盖已变化/);
    assert.equal(exported, false);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("推广阶段 maximumDays 能把单轮日任务限制为一个且默认规划器上限不变", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-promotion-limit-"));
  let requestCount = 0;
  const executedDates: string[] = [];
  try {
    const request = (async (input: Parameters<typeof fetch>[0]) => {
      requestCount += 1;
      const requestedPeriod = requestedPeriodFromFetchInput(input);
      const singleDay = requestedPeriod.startDate === requestedPeriod.endDate;
      return Response.json({
        requestedPeriod,
        coverage: singleDay
          ? { productDailyDates: [requestedPeriod.startDate], promotionDates: [] }
          : { productDailyDates: ["2026-07-28", "2026-07-29"], promotionDates: [] },
      });
    }) as typeof fetch;
    const result = await runTmallPromotionStage({
      storeKey: "tmall-yijiu",
      baseUrl: "http://localhost:3000",
      request,
      auditDirectory: directory,
      dates: ["2026-07-28"],
      maximumDays: 1,
      executeDate: async ({ plan }) => {
        executedDates.push(plan.startDate);
        return {
          ok: true,
          stage: "promotion_day" as const,
          status: "duplicate" as const,
          storeKey: "tmall-yijiu",
          shopName: "天猫-志高亿玖专卖店",
          date: plan.startDate,
          startDate: plan.startDate,
          endDate: plan.endDate,
          dates: plan.dates,
          metrics: "全部数据指标" as const,
          fileName: "promotion.zip",
          sha256: "a".repeat(64),
          rowCount: 1,
          warningCount: 0,
          batchId: "batch-1",
          coverageConfirmed: true,
        };
      },
    });
    assert.deepEqual(result.plannedDates, ["2026-07-28"]);
    assert.deepEqual(result.completedDates, ["2026-07-28"]);
    assert.equal(result.coverageConfirmed, true);
    assert.deepEqual(executedDates, ["2026-07-28"]);
    assert.equal(requestCount, 1);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("损坏的推广恢复清单必须失败关闭且测试不读取真实活动清单", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-promotion-audit-"));
  try {
    await writeFile(path.join(directory, "active-tmall-yijiu.json"), "{broken", "utf8");
    const request = (async (input: Parameters<typeof fetch>[0]) => Response.json({
      requestedPeriod: requestedPeriodFromFetchInput(input),
      coverage: { productDailyDates: ["2026-07-28"], promotionDates: [] },
    })) as typeof fetch;
    await assert.rejects(runTmallPromotionStage({
      storeKey: "tmall-yijiu",
      baseUrl: "http://localhost:3000",
      request,
      auditDirectory: directory,
      dates: ["2026-07-28"],
    }), /JSON|Unexpected|结构无效/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("旧全站推已提交活动清单保持原样并阻止商品报表重复提交", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-promotion-legacy-audit-"));
  const auditPath = path.join(directory, "active-tmall-yijiu.json");
  const legacy = {
    version: 1,
    runId: "legacy-run",
    storeKey: "tmall-yijiu",
    shopName: "天猫-志高亿玖专卖店",
    baseUrl: "http://localhost:3000",
    startedAt: "2026-08-26T03:00:00.000Z",
    updatedAt: "2026-08-26T03:01:00.000Z",
    stage: "failed",
    resumeStage: "report_submitted",
    startDate: "2026-07-28",
    endDate: "2026-07-28",
    dates: ["2026-07-28"],
    metrics: "全部数据指标",
    downloadListUrl: TMALL_PROMOTION_DOWNLOAD_LIST_URL,
    dismissedPopups: 0,
    error: "legacy failure",
  };
  try {
    await writeFile(auditPath, JSON.stringify(legacy), "utf8");
    const request = (async (input: Parameters<typeof fetch>[0]) => Response.json({
      requestedPeriod: requestedPeriodFromFetchInput(input),
      coverage: { productDailyDates: ["2026-07-28"], promotionDates: ["2026-07-28"] },
    })) as typeof fetch;
    await assert.rejects(runTmallPromotionStage({
      storeKey: "tmall-yijiu",
      baseUrl: "http://localhost:3000",
      request,
      auditDirectory: directory,
      dates: ["2026-07-28"],
    }), /活动清单.*禁止按无缺口跳过/);
    assert.deepEqual(JSON.parse(await readFile(auditPath, "utf8")), legacy);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
