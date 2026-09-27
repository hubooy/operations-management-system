import assert from "node:assert/strict";
import test from "node:test";
import {
  newestCompletedJdWareExportTask,
  newestUnseenJdWareExportTask,
  assertJdWareExportRecoveryIdentity,
  isJdWareExportTaskCreatedNear,
  parseJdWareExportTaskRows,
  selectRecoverableJdWareExportTask,
  selectExistingJdWareExportTask,
  decideJdWareExportBaselineRecoveryAbandonment,
  unseenJdWareExportTasks,
} from "../lib/jd/ware-export";
import { advanceWareExportAudit, captureJdWareInitialProductQuery, clickJdWareProductQueryControl, createJdWareBrowserDownloadSession, createJdWareQueryBootstrapState, createWareExportAudit, handleJdWareDownloadPromise, hasStableJdWareTaskSnapshot, hasStableUniqueVisibleJdExportEntry, importSkuFile, isConfirmedJdWareTaskListEmptyState, isJdWareCreateExportRequest, isJdWareDownloadPathInsideStaging, isJdWareProductQueryRequest, isJdWareProductTargetPage, isLikelyJdLoginPage, isTransientJdExportEntryRepaint, JdWareCreateExportRejectedError, jdWareBatchOperationsLabelPattern, jdWareBatchOperationsTriggerSelector, jdWareExportEntryBootstrapDecision, jdWareInitialProductQueryTimeoutMs, jdWareNormalizedExportDrawerSelector, jdWareProductQueryBootstrapDecision, jdWareSkuExportDrawerDecision, jdWareTargetNavigationTimeoutMs, openExportEntryWithRepaintRetry, parseJdWareProductTotalText, prepareJdWareExportEntry, reopenJdWareTargetAfterAutomatedLogin, revealJdWareExportEntry, selectJdWareTaskDownloadTarget, shouldCloseJdWareBrowserConnection, shouldDismissJdMenuUpdateNotice, validateJdWareBrowserDownloadBegin, validateJdWareCreateExportResponse, validateJdWareDownloadProgress, validateJdWareMasterWorkbook, validateJdWareProductQueryResponse, waitForJdWareLoginNavigation, waitForJdWareLoginRedirect, waitForJdWareProductQueryBootstrap, waitForJdWareQueryOrAutomatedLoginRedirect, waitForJdWareQueryOrInteractiveRedirect, wareActiveTaskPath, withJdWareDownloadStaging } from "../tools/jackyun-ware-export";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import * as XLSX from "xlsx";

test("parses the JD export record table without confusing product rows for tasks", () => {
  const tasks = parseJdWareExportTaskRows([
    "志高开水器\n商品编码：14577459490\n2026-07-18 10:03:17\n上架中",
    "2026-07-19 20:00:09\n9371817\n已完成\n成功：1453\n下载",
    "2026-07-19 20:03:11\n9371818\n处理中\n-",
  ]);

  assert.deepEqual(tasks.map((task) => ({
    taskId: task.taskId,
    status: task.status,
    successRows: task.successRows,
  })), [
    { taskId: "9371818", status: "pending", successRows: null },
    { taskId: "9371817", status: "completed", successRows: 1453 },
  ]);
});

test("chooses the newly created task rather than an older completed download", () => {
  const tasks = parseJdWareExportTaskRows([
    "2026-07-19 20:03:11\n9371818\n处理中\n-",
    "2026-07-19 20:00:09\n9371817\n已完成\n成功：1453\n下载",
  ]);

  assert.equal(newestUnseenJdWareExportTask(tasks, new Set(["9371817"]))?.taskId, "9371818");
  assert.equal(newestCompletedJdWareExportTask(tasks)?.taskId, "9371817");
  assert.deepEqual(unseenJdWareExportTasks(tasks, new Set()).map((task) => task.taskId), ["9371818", "9371817"]);
});

test("refuses to adopt even one pending task without a persisted manifest", () => {
  const tasks = [
    { taskId: "9371818", status: "pending" as const, createdAt: "2026-07-19 20:03:11", resultText: null, successRows: null, rowText: "pending" },
    { taskId: "9371817", status: "completed" as const, createdAt: "2026-07-19 20:00:09", resultText: null, successRows: 1453, rowText: "completed" },
  ];
  const selection = selectExistingJdWareExportTask(tasks, true);
  assert.equal(selection.kind, "unowned_pending");
  if (selection.kind === "unowned_pending") assert.deepEqual(selection.tasks.map((task) => task.taskId), ["9371818"]);
});

test("does not create or reuse when multiple pending export tasks exist", () => {
  const tasks = [
    { taskId: "9371819", status: "pending" as const, createdAt: "2026-07-19 20:04:11", resultText: null, successRows: null, rowText: "pending" },
    { taskId: "9371818", status: "pending" as const, createdAt: "2026-07-19 20:03:11", resultText: null, successRows: null, rowText: "pending" },
    { taskId: "9371817", status: "completed" as const, createdAt: "2026-07-19 20:00:09", resultText: null, successRows: 1453, rowText: "completed" },
  ];
  const selection = selectExistingJdWareExportTask(tasks, false);
  assert.equal(selection.kind, "ambiguous_pending");
  if (selection.kind === "ambiguous_pending") assert.deepEqual(selection.tasks.map((task) => task.taskId), ["9371819", "9371818"]);
});

test("recovers the exact timed-out task after it becomes completed", () => {
  const tasks = [
    { taskId: "9371818", status: "completed" as const, createdAt: "2026-07-19 20:03:11", resultText: "成功：1453", successRows: 1453, rowText: "completed" },
    { taskId: "9371817", status: "completed" as const, createdAt: "2026-07-19 20:00:09", resultText: "成功：1450", successRows: 1450, rowText: "older" },
  ];
  const selection = selectRecoverableJdWareExportTask(tasks, {
    version: 1,
    baselineTaskIds: ["9371817"],
    taskId: "9371818",
    createdAt: "2026-07-19T12:03:10.000Z",
  });
  assert.equal(selection.kind, "task");
  if (selection.kind === "task") assert.equal(selection.task.taskId, "9371818");
});

test("production recovery manifests bind storeKey, shopId, and shopName", () => {
  const expected = { storeKey: "jd-yiyong-director", shopId: "701455", shopName: "志高商用设备旗舰店" };
  const recovery = {
    version: 2 as const,
    ...expected,
    baselineTaskIds: ["9371817"],
    taskId: "9371818",
    createdAt: "2026-07-19T12:03:10.000Z",
  };
  assert.equal(assertJdWareExportRecoveryIdentity(recovery, expected), recovery);
  assert.throws(() => assertJdWareExportRecoveryIdentity({ ...recovery, shopId: "711743" }, expected), /跨店接管/);
  assert.throws(() => assertJdWareExportRecoveryIdentity({ ...recovery, version: 1 }, expected), /完整身份/);
});

test("recovers one post-baseline task when the process stopped before learning its id", () => {
  const tasks = [
    { taskId: "9371818", status: "pending" as const, createdAt: "2026-07-19 20:03:11", resultText: null, successRows: null, rowText: "pending" },
    { taskId: "9371817", status: "completed" as const, createdAt: "2026-07-19 20:00:09", resultText: "成功：1450", successRows: 1450, rowText: "older" },
  ];
  const selection = selectRecoverableJdWareExportTask(tasks, {
    version: 1,
    baselineTaskIds: ["9371817"],
    createdAt: "2026-07-19T12:03:10.000Z",
  });
  assert.equal(selection.kind, "task");
  if (selection.kind === "task") assert.equal(selection.task.taskId, "9371818");
});

test("filters post-baseline recovery candidates by the confirmation-time window", () => {
  const tasks = [
    { taskId: "9371819", status: "completed" as const, createdAt: "2026-07-19 20:10:11", resultText: "success", successRows: 10, rowText: "unrelated" },
    { taskId: "9371818", status: "completed" as const, createdAt: "2026-07-19 20:03:11", resultText: "success", successRows: 1453, rowText: "target" },
    { taskId: "9371817", status: "completed" as const, createdAt: "2026-07-19 20:00:09", resultText: "success", successRows: 1450, rowText: "baseline" },
  ];
  const selection = selectRecoverableJdWareExportTask(tasks, {
    version: 1,
    baselineTaskIds: ["9371817"],
    createdAt: "2026-07-19T12:03:10.000Z",
  });
  assert.equal(selection.kind, "task");
  if (selection.kind === "task") assert.equal(selection.task.taskId, "9371818");
});

test("converts JD Shanghai task timestamps before applying the recovery window", () => {
  assert.equal(isJdWareExportTaskCreatedNear("2026-07-19T12:03:10.000Z", "2026-07-19 20:03:11"), true);
  assert.equal(isJdWareExportTaskCreatedNear("2026-07-19T12:03:10.000Z", "2026-07-19 20:10:11"), false);
});

test("stops when a baseline-only recovery has multiple candidate tasks", () => {
  const tasks = [
    { taskId: "9371819", status: "pending" as const, createdAt: "2026-07-19 20:04:11", resultText: null, successRows: null, rowText: "pending" },
    { taskId: "9371818", status: "completed" as const, createdAt: "2026-07-19 20:03:11", resultText: "成功：1453", successRows: 1453, rowText: "completed" },
    { taskId: "9371817", status: "completed" as const, createdAt: "2026-07-19 20:00:09", resultText: "成功：1450", successRows: 1450, rowText: "older" },
  ];
  const selection = selectRecoverableJdWareExportTask(tasks, {
    version: 1,
    baselineTaskIds: ["9371817"],
    createdAt: "2026-07-19T12:03:10.000Z",
  });
  assert.equal(selection.kind, "ambiguous");
});

test("abandons only a stale baseline-only recovery with a confirmed snapshot and zero nearby candidates", () => {
  const recovery = { version: 1 as const, baselineTaskIds: ["old"], createdAt: "2026-08-07T01:33:00.000Z" };
  assert.equal(decideJdWareExportBaselineRecoveryAbandonment(recovery, [], true, Date.parse("2026-08-07T02:03:00.000Z")).kind, "abandon");
  assert.equal(decideJdWareExportBaselineRecoveryAbandonment({ ...recovery, taskId: "known" }, [], true, Date.parse("2026-08-07T03:00:00.000Z")).kind, "keep");
  assert.equal(decideJdWareExportBaselineRecoveryAbandonment(recovery, [], true, Date.parse("2026-08-07T01:50:00.000Z")).kind, "keep");
  assert.equal(decideJdWareExportBaselineRecoveryAbandonment(recovery, [], false, Date.parse("2026-08-07T03:00:00.000Z")).kind, "keep");
  assert.equal(decideJdWareExportBaselineRecoveryAbandonment(recovery, [{ taskId: "near", status: "pending", createdAt: "2026-08-07 09:33:10", resultText: null, successRows: null, rowText: "near" }], true, Date.parse("2026-08-07T03:00:00.000Z")).kind, "keep");
});

test("task-list baseline requires two stable nonempty snapshots or two explicit empty states", () => {
  const task = { taskId: "1", status: "completed" as const, createdAt: "2026-08-07 09:33:10", resultText: null, successRows: 1, rowText: "row" };
  assert.equal(hasStableJdWareTaskSnapshot([{ tasks: [], emptyConfirmed: false }, { tasks: [], emptyConfirmed: false }]), false);
  assert.equal(hasStableJdWareTaskSnapshot([{ tasks: [], emptyConfirmed: true }, { tasks: [], emptyConfirmed: true }]), true);
  assert.equal(hasStableJdWareTaskSnapshot([{ tasks: [task], emptyConfirmed: false }, { tasks: [task], emptyConfirmed: false }]), true);
  assert.equal(hasStableJdWareTaskSnapshot([{ tasks: [task], emptyConfirmed: false }, { tasks: [], emptyConfirmed: true }]), false);
  assert.equal(hasStableJdWareTaskSnapshot([{ tasks: [{ ...task, rowText: "first frame" }], emptyConfirmed: false }, { tasks: [{ ...task, rowText: "changed frame" }], emptyConfirmed: false }]), false);
});

test("confirms an empty task list only from the uniquely bound export container", () => {
  assert.equal(isConfirmedJdWareTaskListEmptyState({ uniqueRefresh: true, boundToExportContainer: true, containerText: "暂无记录" }), true);
  assert.equal(isConfirmedJdWareTaskListEmptyState({ uniqueRefresh: false, boundToExportContainer: true, containerText: "暂无记录" }), false);
  assert.equal(isConfirmedJdWareTaskListEmptyState({ uniqueRefresh: true, boundToExportContainer: false, containerText: "暂无记录" }), false);
  // A separate page section may say “暂无数据”; it cannot certify this export container.
  assert.equal(isConfirmedJdWareTaskListEmptyState({ uniqueRefresh: true, boundToExportContainer: true, containerText: "导出记录加载中" }), false);
});

test("menu-update overlay is dismissed only for one matching layer with a single exact 知道了 button", () => {
  assert.equal(shouldDismissJdMenuUpdateNotice([{ text: "京麦菜单更新调整将于明日生效", buttons: ["知道了"] }]), true);
  assert.equal(shouldDismissJdMenuUpdateNotice([{ text: "京麦菜单更新调整将于明日生效", buttons: ["立即查看"] }]), false);
  assert.equal(shouldDismissJdMenuUpdateNotice([{ text: "京麦菜单更新调整将于明日生效", buttons: ["知道了", "立即查看"] }]), false);
  assert.equal(shouldDismissJdMenuUpdateNotice([{ text: "京麦菜单更新调整将于明日生效", buttons: ["知道了"] }, { text: "导出查询商品", buttons: ["确认"] }]), true);
  assert.equal(shouldDismissJdMenuUpdateNotice([{ text: "京麦菜单更新调整", buttons: ["知道了"] }]), false);
  assert.equal(shouldDismissJdMenuUpdateNotice([{ text: "一级菜单更新调整已生效", buttons: ["知道了"] }, { text: "一级菜单更新调整已生效", buttons: ["知道了"] }]), false);
});

const jdWareTaskId = "9940846";
const jdWareDownloadUrl = `https://storage.360buyimg.com/ware-common/cpop-export-sku/%E5%AF%BC%E5%87%BA%E5%95%86%E5%93%81%E6%99%AE%E9%80%9APOP-SKU%E4%BF%A1%E6%81%AF_${jdWareTaskId}_fixture.xlsx?Expires=1&AccessKey=fixture&Signature=fixture`;

function jdWareWorkbook(rows: unknown[][]) {
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(rows), "SKU");
  return new Uint8Array(XLSX.write(workbook, { type: "buffer", bookType: "xlsx" }));
}

test("captures only one signed task-specific JD browser download target", () => {
  assert.equal(selectJdWareTaskDownloadTarget({ taskId: jdWareTaskId, sourceUrl: "https://wares-jdm.jd.com/ware/wareList", openedUrls: [jdWareDownloadUrl] }).kind, "target");
  assert.equal(selectJdWareTaskDownloadTarget({ taskId: jdWareTaskId, sourceUrl: "https://wares-jdm.jd.com/ware/wareList", openedUrls: [] }).kind, "reject");
  assert.equal(selectJdWareTaskDownloadTarget({ taskId: jdWareTaskId, sourceUrl: "https://wares-jdm.jd.com/ware/wareList", openedUrls: ["https://storage.360buyimg.com/ware-common/cpop-export-sku/not-a-workbook.csv?Expires=1&AccessKey=x&Signature=x"] }).kind, "reject");
  assert.equal(selectJdWareTaskDownloadTarget({ taskId: jdWareTaskId, sourceUrl: jdWareDownloadUrl, openedUrls: [jdWareDownloadUrl] }).kind, "reject");
  assert.equal(selectJdWareTaskDownloadTarget({ taskId: jdWareTaskId, sourceUrl: "https://wares-jdm.jd.com/ware/wareList", openedUrls: [jdWareDownloadUrl, jdWareDownloadUrl] }).kind, "reject");
  assert.equal(validateJdWareBrowserDownloadBegin({ url: jdWareDownloadUrl, suggestedFilename: `商品_${jdWareTaskId}.xlsx` }, jdWareTaskId).kind, "target");
  assert.equal(validateJdWareBrowserDownloadBegin({ url: jdWareDownloadUrl, suggestedFilename: `商品_${jdWareTaskId}.csv` }, jdWareTaskId).kind, "reject");
});

test("fails closed for canceled, wrong-guid, stale-cross-store paths, and malformed JD workbooks", () => {
  assert.deepEqual(validateJdWareDownloadProgress("expected", { guid: "expected", state: "canceled" }), { kind: "reject", reason: "download_canceled" });
  assert.deepEqual(validateJdWareDownloadProgress("expected", { guid: "other", state: "completed" }), { kind: "reject", reason: "unexpected_download_guid" });
  assert.equal(isJdWareDownloadPathInsideStaging("D:\\downloads\\shop-a\\.jd-ware-export-1", "D:\\downloads\\shop-a\\.jd-ware-export-1\\guid"), true);
  assert.equal(isJdWareDownloadPathInsideStaging("D:\\downloads\\shop-a\\.jd-ware-export-1", "D:\\downloads\\shop-b\\old.xlsx"), false);
  assert.throws(() => validateJdWareMasterWorkbook(new Uint8Array([0x50, 0x4b, 0x03, 0x04])), /workbook|工作簿|ZIP|解压/i);
  assert.throws(() => validateJdWareMasterWorkbook(jdWareWorkbook([["商品编码", "SKU ID"], ["A", "1"]]), 2), /行数/);
});

test("requires the JD master SKUID and 商品编码 headers plus the exact task row count", () => {
  const workbook = jdWareWorkbook([["商品编码", "SKU ID", "商品名称"], ["A", "1", "商品A"], ["B", "2", "商品B"]]);
  assert.deepEqual(validateJdWareMasterWorkbook(workbook, 2), { headerRowNumber: 1, rowCount: 2, columnCount: 3 });
  assert.throws(() => validateJdWareMasterWorkbook(jdWareWorkbook([["商品编码", "商品名称"], ["A", "商品A"]]), 1), /SKUID/);
});

test("uses the Chrome root CDP session for JD downloads", async () => {
  let browserSessionCalls = 0;
  let pageSessionCalls = 0;
  const expectedSession = {};
  const page = {
    context: () => ({
      browser: () => ({ newBrowserCDPSession: async () => { browserSessionCalls += 1; return expectedSession; } }),
      newCDPSession: async () => { pageSessionCalls += 1; return expectedSession; },
    }),
  } as unknown as Parameters<typeof createJdWareBrowserDownloadSession>[0];
  assert.equal(await createJdWareBrowserDownloadSession(page), expectedSession);
  assert.equal(browserSessionCalls, 1);
  assert.equal(pageSessionCalls, 0);
});

test("handles an invalid download-start rejection before the click await can observe it", async () => {
  const observed: unknown[] = [];
  const listener = (reason: unknown) => observed.push(reason);
  process.on("unhandledRejection", listener);
  try {
    const rejected = handleJdWareDownloadPromise(Promise.reject(new Error("invalid_download_begin")));
    await assert.rejects(rejected, /invalid_download_begin/);
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.deepEqual(observed, []);
  } finally {
    process.off("unhandledRejection", listener);
  }
});

test("cleans the scoped staging directory when root CDP session setup fails", async () => {
  const downloadDirectory = await mkdtemp(path.join(tmpdir(), "jd-ware-staging-root-"));
  let stagingDirectory = "";
  try {
    await assert.rejects(withJdWareDownloadStaging(downloadDirectory, async (created) => {
      stagingDirectory = created;
      throw new Error("root_cdp_unavailable");
    }), /root_cdp_unavailable/);
    await assert.rejects(stat(stagingDirectory));
  } finally {
    await rm(downloadDirectory, { recursive: true, force: true });
  }
});

test("preserves the task id in a timeout failure audit", () => {
  const running = advanceWareExportAudit(createWareExportAudit({ baseUrl: "http://localhost:3000", reuseLatest: false }), {
    stage: "wait_new_task", baselineTaskIds: ["9371817"], taskId: "9371818", taskStatus: "pending",
  });
  const failed = advanceWareExportAudit(running, { status: "failed", error: "等待京东导出任务 9371818 完成超时" });
  assert.deepEqual(
    { status: failed.status, stage: failed.stage, taskId: failed.taskId, baselineTaskIds: failed.baselineTaskIds },
    { status: "failed", stage: "wait_new_task", taskId: "9371818", baselineTaskIds: ["9371817"] },
  );
});

test("binds a JD ware audit to the controlled store identity when supplied", () => {
  const audit = createWareExportAudit({
    baseUrl: "http://localhost:3000",
    reuseLatest: false,
    storeKey: "jd-yiyong-director",
    shopName: "志高商用设备旗舰店",
  });
  assert.equal(audit.storeKey, "jd-yiyong-director");
  assert.equal(audit.shopName, "志高商用设备旗舰店");
});

test("keeps the abandoned recovery evidence in the audit after later failure", () => {
  const abandoned = advanceWareExportAudit(createWareExportAudit({ baseUrl: "http://localhost:3000", reuseLatest: false }), {
    stage: "recovery_abandoned",
    recoveryArchivePath: "active-task-jd-yiyong-director.abandoned-1.json",
    recoveryCreatedAt: "2026-08-07T01:33:00.000Z",
  });
  const failed = advanceWareExportAudit(abandoned, { status: "failed", error: "audit write retry failed" });
  assert.deepEqual(
    { stage: failed.stage, recoveryArchivePath: failed.recoveryArchivePath, recoveryCreatedAt: failed.recoveryCreatedAt },
    { stage: "recovery_abandoned", recoveryArchivePath: "active-task-jd-yiyong-director.abandoned-1.json", recoveryCreatedAt: "2026-08-07T01:33:00.000Z" },
  );
});

test("a resolved export-button click is not reported as a submitted JD task", () => {
  const audit = advanceWareExportAudit(createWareExportAudit({ baseUrl: "http://localhost:3000", reuseLatest: false }), {
    stage: "task_click_invoked", baselineTaskIds: ["9371817"],
  });
  assert.deepEqual(
    { stage: audit.stage, taskId: audit.taskId, baselineTaskIds: audit.baselineTaskIds },
    { stage: "task_click_invoked", taskId: undefined, baselineTaskIds: ["9371817"] },
  );
});

test("matches only the exact JD create-export POST request", () => {
  const target = "https://sff.jd.com/api?v=1.0&appId=fixture&api=dsm.product.manage.view.batchJobService.createExportJob";
  assert.equal(isJdWareCreateExportRequest(target, "POST"), true);
  assert.equal(isJdWareCreateExportRequest(target, "GET"), false);
  assert.equal(isJdWareCreateExportRequest(target.replace("createExportJob", "queryByPage"), "POST"), false);
  assert.equal(isJdWareCreateExportRequest(target.replace("sff.jd.com", "example.com"), "POST"), false);
  assert.equal(isJdWareCreateExportRequest("not-a-url", "POST"), false);
});

test("requires both HTTP and JD business success before polling export records", () => {
  assert.deepEqual(validateJdWareCreateExportResponse({ status: 200, payload: { code: 200, msg: "成功" } }), { code: 200, message: "成功" });
  assert.throws(
    () => validateJdWareCreateExportResponse({ status: 200, payload: { code: 5001, msg: "操作频繁" } }),
    /业务码 5001.*操作频繁/,
  );
  assert.throws(() => validateJdWareCreateExportResponse({ status: 503, payload: { code: 200, msg: "成功" } }), /HTTP 503/);
  assert.throws(() => validateJdWareCreateExportResponse({ status: 200, payload: "not-json" }), /业务码 missing/);
  assert.throws(
    () => validateJdWareCreateExportResponse({ status: 200, payload: { code: 201, msg: "[总行数必须大于0],创建导出任务失败" } }),
    (error: unknown) => error instanceof JdWareCreateExportRejectedError && error.definitiveNoTask,
  );
  assert.throws(
    () => validateJdWareCreateExportResponse({ status: 200, payload: { code: 201, msg: "系统繁忙" } }),
    (error: unknown) => error instanceof JdWareCreateExportRejectedError && !error.definitiveNoTask,
  );
});

test("requires a fresh positive JD product query before opening the export drawer", () => {
  const target = "https://sff.jd.com/api?v=1.0&appId=fixture&api=dsm.product.manage.ProductInfoReadViewService.queryValidProductList";
  assert.equal(isJdWareProductQueryRequest(target, "POST"), true);
  assert.equal(isJdWareProductQueryRequest(target, "GET"), false);
  assert.equal(isJdWareProductQueryRequest(target.replace("queryValidProductList", "createExportJob"), "POST"), false);
  assert.deepEqual(
    validateJdWareProductQueryResponse({ status: 200, payload: { code: 200, msg: "成功", data: { total: 83 } } }),
    { code: 200, total: 83, message: "成功" },
  );
  assert.deepEqual(
    validateJdWareProductQueryResponse({ status: 200, payload: { code: 200, msg: "成功", data: { totalCount: 83, data: [{ productId: 1 }] } } }),
    { code: 200, total: 83, message: "成功" },
  );
  assert.throws(
    () => validateJdWareProductQueryResponse({ status: 200, payload: { code: 200, msg: "成功", data: { total: 82, totalCount: 83 } } }),
    /字段冲突/,
  );
  assert.throws(
    () => validateJdWareProductQueryResponse({ status: 200, payload: { code: 200, msg: "成功", data: { total: 0 } } }),
    /总行数 0/,
  );
  assert.throws(
    () => validateJdWareProductQueryResponse({ status: 200, payload: { code: 201, msg: "查询失败", data: { total: 83 } } }),
    /业务码 201/,
  );
  assert.throws(
    () => validateJdWareProductQueryResponse({ status: 200, payload: { code: 601, msg: "未经京东授权的软件操作", data: {} } }),
    /业务码 601.*未经京东授权的软件操作/,
  );
  assert.equal(parseJdWareProductTotalText("共83条"), 83);
  assert.equal(parseJdWareProductTotalText(" 共 83 条 "), 83);
  assert.equal(parseJdWareProductTotalText("共 2 页"), null);
});

test("clicks the uniquely verified JD product query once with trusted browser input", async () => {
  const calls: Array<Record<string, unknown>> = [];
  await clickJdWareProductQueryControl({
    click: async (options: Record<string, unknown>) => { calls.push(options); },
  } as never);
  assert.deepEqual(calls, [{ force: true, timeout: 10_000 }]);
  const source = await readFile("tools/jackyun-ware-export.ts", "utf8");
  assert.doesNotMatch(source, /queryButton\.dispatchEvent\(/);
});

test("captures the initial JD navigation query before loading the target and fences any replay", async () => {
  const state = createJdWareQueryBootstrapState();
  const calls: string[] = [];
  const response = await captureJdWareInitialProductQuery(state, {
    gotoBlank: async () => { calls.push("blank"); },
    waitForQuery: async () => { calls.push("listen"); return { code: 200 }; },
    gotoTarget: async () => { calls.push("target"); },
  });
  assert.deepEqual(calls, ["blank", "listen", "target"]);
  assert.deepEqual(response, { code: 200 });
  assert.equal(state.queryTriggered, true);
  await assert.rejects(
    captureJdWareInitialProductQuery(state, {
      gotoBlank: async () => { throw new Error("must not navigate again"); },
      waitForQuery: async () => ({ code: 200 }),
      gotoTarget: async () => undefined,
    }),
    /拒绝重复导航或查询/,
  );
});

test("keeps the initial JD query listener alive beyond the complete navigation budget", () => {
  assert.ok(jdWareInitialProductQueryTimeoutMs > jdWareTargetNavigationTimeoutMs);
  assert.equal(jdWareInitialProductQueryTimeoutMs, 60_000);
});

test("reports a login redirect immediately when navigation completes without a product query", async () => {
  const state = createJdWareQueryBootstrapState();
  const calls: string[] = [];
  await assert.rejects(
    captureJdWareInitialProductQuery(state, {
      gotoBlank: async () => { calls.push("blank"); },
      waitForQuery: async () => new Promise<never>(() => undefined),
      gotoTarget: async () => { calls.push("target"); },
      verifyAfterNavigation: async () => { calls.push("verify"); throw new Error("京东商家后台尚未登录。"); },
    }),
    /尚未登录/,
  );
  assert.deepEqual(calls, ["blank", "target", "verify"]);
  assert.equal(state.queryTriggered, true);
});

test("races a delayed JD login redirect against the still-pending product query", async () => {
  const pendingQuery = new Promise<never>(() => undefined);
  await assert.rejects(
    waitForJdWareQueryOrInteractiveRedirect(
      pendingQuery,
      Promise.reject(new Error("京东商家后台尚未登录。")),
    ),
    /尚未登录/,
  );
  assert.equal(
    await waitForJdWareQueryOrInteractiveRedirect(Promise.resolve("query"), new Promise<never>(() => undefined)),
    "query",
  );
});

test("reopens WareList once when DPAPI login lands on the merchant home page", async () => {
  const calls: string[] = [];
  assert.equal(isJdWareProductTargetPage("https://wares-jdm.jd.com/ware/wareList?activeTab=OnsaleWare"), true);
  assert.equal(isJdWareProductTargetPage("https://shop.jd.com/home"), false);
  assert.equal(await reopenJdWareTargetAfterAutomatedLogin({
    authentication: "windows_dpapi_credentials",
    currentUrl: "https://shop.jd.com/home",
    gotoTarget: async () => { calls.push("target"); },
    verifyPostNavigation: async () => { calls.push("verify"); },
  }), true);
  assert.deepEqual(calls, ["target", "verify"]);
});

test("does not reopen or resubmit when the session already reached WareList", async () => {
  let calls = 0;
  const dependencies = {
    gotoTarget: async () => { calls += 1; },
    verifyPostNavigation: async () => { calls += 1; },
  };
  assert.equal(await reopenJdWareTargetAfterAutomatedLogin({
    authentication: "existing_session",
    currentUrl: "https://shop.jd.com/home",
    ...dependencies,
  }), false);
  assert.equal(await reopenJdWareTargetAfterAutomatedLogin({
    authentication: "windows_dpapi_credentials",
    currentUrl: "https://wares-jdm.jd.com/ware/wareList?activeTab=OnsaleWare",
    ...dependencies,
  }), false);
  assert.equal(calls, 0);
});

test("handles a delayed login redirect once and keeps the original product query listener", async () => {
  let authenticated = 0;
  let resolveQuery!: (value: string) => void;
  const query = new Promise<string>((resolve) => { resolveQuery = resolve; });
  const result = waitForJdWareQueryOrAutomatedLoginRedirect(
    query,
    Promise.resolve("login"),
    async () => { authenticated += 1; resolveQuery("query-after-login"); },
  );
  assert.equal(await result, "query-after-login");
  assert.equal(authenticated, 1);
});

test("a product query that wins first suppresses a later automated login submission", async () => {
  let resolveLogin!: () => void;
  let authenticated = 0;
  const login = new Promise<void>((resolve) => { resolveLogin = resolve; });
  assert.equal(await waitForJdWareQueryOrAutomatedLoginRedirect(
    Promise.resolve("query"),
    login,
    async () => { authenticated += 1; },
  ), "query");
  resolveLogin();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(authenticated, 0);
});

test("does not let a detached non-login URL observer preempt the initial product query", async () => {
  const query = new Promise<string>((resolve) => setTimeout(() => resolve("query"), 5));
  const redirect = waitForJdWareLoginRedirect(
    () => Promise.reject(new Error("page.waitForURL: net::ERR_ABORTED; maybe frame was detached?")),
    () => "https://wares-jdm.jd.com/ware/wareList?activeTab=OnsaleWare",
  );
  assert.equal(await waitForJdWareQueryOrInteractiveRedirect(query, redirect), "query");
});

test("still reports login when the URL observer resolves or the current URL is a login page", async () => {
  await assert.rejects(
    waitForJdWareLoginRedirect(() => Promise.resolve(), () => "https://passport.jd.com/new/login.aspx"),
    /尚未登录/,
  );
  await assert.rejects(
    waitForJdWareLoginRedirect(
      () => Promise.reject(new Error("page.waitForURL: net::ERR_ABORTED; maybe frame was detached?")),
      () => "https://passport.jd.com/new/login.aspx",
    ),
    /尚未登录/,
  );
});

test("re-arms the login observer after a detached merchant frame", async () => {
  let attempts = 0;
  await assert.rejects(
    waitForJdWareLoginRedirect(
      () => attempts++ === 0
        ? Promise.reject(new Error("page.waitForURL: net::ERR_ABORTED; maybe frame was detached?"))
        : Promise.resolve(),
      () => "https://wares-jdm.jd.com/ware/wareList?activeTab=OnsaleWare",
    ),
    /尚未登录/,
  );
  assert.equal(attempts, 2);
});

test("returns a delayed login navigation to the DPAPI guard without inventing a second observer", async () => {
  let attempts = 0;
  assert.equal(await waitForJdWareLoginNavigation(
    async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("detached merchant frame");
    },
    () => "https://wares-jdm.jd.com/ware/wareList",
  ), "login");
  assert.equal(attempts, 2);
});

test("waits for the JD product query controls to become uniquely ready without clicking", async () => {
  const samples = [
    { productSearchContainerCount: 0, scopedQueryButtonCount: 0, pageQueryButtonCount: 0 },
    { productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1 },
  ];
  let pauses = 0;
  await waitForJdWareProductQueryBootstrap(async () => samples.shift()!, async () => { pauses += 1; }, 2);
  assert.equal(pauses, 1);
  await assert.rejects(
    waitForJdWareProductQueryBootstrap(
      async () => ({ productSearchContainerCount: 0, scopedQueryButtonCount: 0, pageQueryButtonCount: 0 }),
      async () => undefined,
      2,
    ),
    /有界等待/,
  );
  await assert.rejects(
    waitForJdWareProductQueryBootstrap(
      async () => ({ productSearchContainerCount: 2, scopedQueryButtonCount: 1, pageQueryButtonCount: 1 }),
      async () => undefined,
      2,
    ),
    /筛选容器不唯一/,
  );
});

test("retries the reversible export drawer action only for a JD repaint", () => {
  assert.equal(isTransientJdExportEntryRepaint(new Error("element is not stable; detached from the DOM")), true);
  assert.equal(isTransientJdExportEntryRepaint(new Error("intercepted by overlay")), false);
});

test("requires two consecutive unique visible export-entry samples", () => {
  assert.equal(hasStableUniqueVisibleJdExportEntry([1]), false);
  assert.equal(hasStableUniqueVisibleJdExportEntry([1, 0, 1]), false);
  assert.equal(hasStableUniqueVisibleJdExportEntry([0, 1, 1]), true);
});

test("classifies the WareList query control only when it is uniquely bound to its product filter", () => {
  assert.equal(jdWareBatchOperationsLabelPattern.test("批量操作 "), true);
  assert.equal(jdWareBatchOperationsLabelPattern.test(" 更多批量工具 "), false);
  assert.equal(jdWareExportEntryBootstrapDecision({ exportEntryCount: 1, batchOperationsCount: 1 }), "ready");
  assert.equal(jdWareExportEntryBootstrapDecision({ exportEntryCount: 0, batchOperationsCount: 1 }), "open_batch_operations");
  assert.equal(jdWareExportEntryBootstrapDecision({ exportEntryCount: 0, batchOperationsCount: 0 }), "wait");
  assert.throws(() => jdWareExportEntryBootstrapDecision({ exportEntryCount: 2, batchOperationsCount: 1 }), /不唯一/);
  assert.throws(() => jdWareExportEntryBootstrapDecision({ exportEntryCount: 0, batchOperationsCount: 2 }), /不唯一/);
  assert.throws(() => jdWareExportEntryBootstrapDecision({ exportEntryCount: -1, batchOperationsCount: 1 }), /计数无效/);
  assert.equal(jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1 }), "query");
  assert.equal(jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 1, scopedQueryButtonCount: 0, pageQueryButtonCount: 0 }), "wait");
  assert.throws(() => jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 2, scopedQueryButtonCount: 1, pageQueryButtonCount: 1 }), /筛选容器不唯一/);
  assert.equal(jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 0, scopedQueryButtonCount: 0, pageQueryButtonCount: 1 }), "wait");
  assert.throws(() => jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 0, scopedQueryButtonCount: 0, pageQueryButtonCount: 2 }), /按钮不唯一/);
  assert.throws(() => jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 2 }), /不唯一对应/);
  assert.throws(() => jdWareProductQueryBootstrapDecision({ productSearchContainerCount: 1, scopedQueryButtonCount: 2, pageQueryButtonCount: 2 }), /查询按钮不唯一/);
  assert.equal(jdWareSkuExportDrawerDecision({ exportDrawerCount: 1, scopedSkuTabCount: 1, pageSkuTabCount: 1 }), "already_open");
  assert.equal(jdWareSkuExportDrawerDecision({ exportDrawerCount: 0, scopedSkuTabCount: 0, pageSkuTabCount: 0 }), "bootstrap");
  assert.throws(() => jdWareSkuExportDrawerDecision({ exportDrawerCount: 2, scopedSkuTabCount: 1, pageSkuTabCount: 1 }), /抽屉不唯一/);
  assert.throws(() => jdWareSkuExportDrawerDecision({ exportDrawerCount: 1, scopedSkuTabCount: 2, pageSkuTabCount: 2 }), /页签不唯一/);
  assert.throws(() => jdWareSkuExportDrawerDecision({ exportDrawerCount: 0, scopedSkuTabCount: 0, pageSkuTabCount: 1 }), /不在唯一导出条件抽屉/);
});

function createWareListEntryPageFixture(input: { productSearchContainerCount: number; scopedQueryButtonCount: number; pageQueryButtonCount: number; nestedDrawerDom?: boolean; jdOverlayDom?: "single" | "multiple" | "hidden_clone"; exportDrawerCount?: number; scopedSkuTabCount?: number; pageSkuTabCount?: number; initialExportEntryCount?: number; exportEntryCountSequence?: readonly number[]; revealAfterWaits?: number; hideInitialExportAfterWaits?: number; hideExportAfterWaits?: number; queryClickFailures?: number; exportClickFailures?: number; hideExportEntryOnClickFailure?: boolean; batchOperationsCount?: number; batchRevealMethod?: "click" | "enter" | "none"; batchAriaExpanded?: "true" | "false"; deferBatchRevealUntilAriaRead?: boolean }) {
  let exportEntryCount = input.initialExportEntryCount ?? 0;
  let exportEntryCountReads = 0;
  let waitCount = 0;
  let batchAriaRead = false;
  let requiredBatchClicks = 1;
  const clicks = { scopedQuery: 0, batchOperations: 0, batchOperationsEnter: 0, exportEntry: 0 };
  const selectors: string[] = [];
  const chain = <T extends object>(locator: T) => Object.assign(locator, { filter: () => locator });
  const scopedQuery = chain({
    count: async () => input.scopedQueryButtonCount,
    click: async () => {
      clicks.scopedQuery += 1;
      if (clicks.scopedQuery <= (input.queryClickFailures ?? 0)) throw new Error("element is detached from the DOM");
    },
  });
  const productSearchContainer = chain({
    count: async () => input.productSearchContainerCount,
    getByRole: () => scopedQuery,
  });
  const pageQuery = chain({ count: async () => input.pageQueryButtonCount });
  const scopedSkuTab = chain({ count: async () => input.scopedSkuTabCount ?? 0 });
  // Models the normal nested Ant/JDM DOM: an outer .ant-drawer wrapping an
  // inner [role=dialog]. The normalized locator keeps only the inner root.
  const nestedDrawerNodes = input.nestedDrawerDom
    ? [{ id: "outer", parentId: null, matchesDrawer: true }, { id: "inner", parentId: "outer", matchesDrawer: true }]
    : [];
  // Captures JD's real shape: .jd-overlay > .jd-drawer__body > title("导出条件").
  // A hidden duplicate is deliberately excluded by the production visible filter.
  const jdOverlay = (visible: boolean) => ({ visible, className: "jd-overlay", body: { className: "jd-drawer__body", title: "导出条件", skuTab: "SKU导出" } });
  const jdOverlayNodes = input.jdOverlayDom === "single"
    ? [jdOverlay(true)]
    : input.jdOverlayDom === "multiple"
      ? [jdOverlay(true), jdOverlay(true)]
      : input.jdOverlayDom === "hidden_clone"
        ? [jdOverlay(true), jdOverlay(false)]
        : [];
  const rawDrawerCandidate = chain({ count: async () => nestedDrawerNodes.length || jdOverlayNodes.length || (input.exportDrawerCount ?? 0) });
  const exportDrawer = chain({
    count: async () => nestedDrawerNodes.filter((node) => !nestedDrawerNodes.some((other) => other.parentId === node.id && other.matchesDrawer)).length || jdOverlayNodes.filter((node) => node.visible).length || (input.exportDrawerCount ?? 0),
    getByRole: () => scopedSkuTab,
  });
  const pageSkuTab = chain({ count: async () => input.pageSkuTabCount ?? 0 });
  const exportEntry = chain({
    count: async () => input.exportEntryCountSequence?.[exportEntryCountReads++] ?? exportEntryCount,
    click: async () => {
      clicks.exportEntry += 1;
      if (clicks.exportEntry <= (input.exportClickFailures ?? 0)) {
        if (input.hideExportEntryOnClickFailure) {
          exportEntryCount = 0;
          requiredBatchClicks = clicks.batchOperations + 1;
        }
        throw new Error("element is detached from the DOM");
      }
    },
  });
  const batchOperationsButton = chain({ count: async () => input.batchOperationsCount ?? 0 });
  const batchOperations = chain({
    count: async () => input.batchOperationsCount ?? 0,
    click: async () => { clicks.batchOperations += 1; },
    press: async (key: string) => { if (key === "Enter") clicks.batchOperationsEnter += 1; },
    getAttribute: async () => {
      batchAriaRead = true;
      return input.batchAriaExpanded ?? "false";
    },
  });
  const page = {
    locator: (selector: string) => {
      if (selector === "button") return batchOperationsButton;
      if (selector === jdWareBatchOperationsTriggerSelector) return batchOperations;
      if (selector.includes("jdm-drawer") || selector.includes(".jd-overlay")) return selector.includes(":not(:has(") ? exportDrawer : rawDrawerCandidate;
      selectors.push(selector);
      return productSearchContainer;
    },
    getByRole: (_role: string, options: { name: string }) => options.name === "导出查询商品" ? exportEntry : options.name === "SKU导出" ? pageSkuTab : pageQuery,
    waitForTimeout: async () => {
      waitCount += 1;
      const batchRevealed = (input.batchRevealMethod ?? "click") === "click"
        ? clicks.batchOperations >= requiredBatchClicks && (!input.deferBatchRevealUntilAriaRead || batchAriaRead)
        : input.batchRevealMethod === "enter"
          ? clicks.batchOperationsEnter === 1
          : false;
      if (input.hideInitialExportAfterWaits !== undefined && clicks.batchOperations === 0 && clicks.scopedQuery === 0 && waitCount >= input.hideInitialExportAfterWaits) exportEntryCount = 0;
      if ((clicks.scopedQuery === 1 || batchRevealed) && waitCount >= (input.revealAfterWaits ?? 1)) exportEntryCount = 1;
      if (input.hideExportAfterWaits !== undefined && waitCount >= input.hideExportAfterWaits) exportEntryCount = 0;
    },
  };
  return { page, clicks, selectors, jdOverlayNodes, rawDrawerCandidate, resetExportEntry: () => { exportEntryCount = 0; } };
}

test("revealJdWareExportEntry scopes query clicks to the unique WareList product filter and never repeats after repaint", async () => {
  const target = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, revealAfterWaits: 2 });
  await revealJdWareExportEntry(target.page as never);
  assert.equal(target.clicks.scopedQuery, 1);
  assert.match(target.selectors[0]!, /商品名称/);
  assert.match(target.selectors[0]!, /商品编码/);

  const targetAndExternal = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 2 });
  await assert.rejects(revealJdWareExportEntry(targetAndExternal.page as never), /不唯一对应/);
  assert.equal(targetAndExternal.clicks.scopedQuery, 0);
});

test("revealJdWareExportEntry opens the exact batch dropdown wrapper instead of its inert inner button", async () => {
  const target = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, batchOperationsCount: 1, revealAfterWaits: 2 });
  await revealJdWareExportEntry(target.page as never);
  assert.equal(target.clicks.batchOperations, 1);
  assert.equal(target.clicks.scopedQuery, 0);
  assert.equal(jdWareBatchOperationsTriggerSelector, "span.batch-dropdown-link");

  const ambiguous = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, batchOperationsCount: 2 });
  await assert.rejects(revealJdWareExportEntry(ambiguous.page as never), /批量操作入口不唯一/);
  assert.equal(ambiguous.clicks.batchOperations, 0);
});

test("revealJdWareExportEntry falls back once to Enter when the same unique dropdown ignores a pointer click", async () => {
  const target = createWareListEntryPageFixture({
    productSearchContainerCount: 1,
    scopedQueryButtonCount: 1,
    pageQueryButtonCount: 1,
    batchOperationsCount: 1,
    batchRevealMethod: "enter",
    revealAfterWaits: 14,
  });
  await revealJdWareExportEntry(target.page as never);
  assert.equal(target.clicks.batchOperations, 1);
  assert.equal(target.clicks.batchOperationsEnter, 1);
  assert.equal(target.clicks.scopedQuery, 0);
});

test("revealJdWareExportEntry waits for an aria-expanded pointer menu without replaying input", async () => {
  const target = createWareListEntryPageFixture({
    productSearchContainerCount: 1,
    scopedQueryButtonCount: 1,
    pageQueryButtonCount: 1,
    batchOperationsCount: 1,
    batchAriaExpanded: "true",
    deferBatchRevealUntilAriaRead: true,
  });

  await revealJdWareExportEntry(target.page as never);

  assert.equal(target.clicks.batchOperations, 1);
  assert.equal(target.clicks.batchOperationsEnter, 0);
});

test("revealJdWareExportEntry ignores an early transient export clone and waits for the hydrated batch trigger", async () => {
  const target = createWareListEntryPageFixture({
    productSearchContainerCount: 1,
    scopedQueryButtonCount: 1,
    pageQueryButtonCount: 1,
    initialExportEntryCount: 1,
    hideInitialExportAfterWaits: 1,
    batchOperationsCount: 1,
  });

  await revealJdWareExportEntry(target.page as never);

  assert.equal(target.clicks.batchOperations, 1);
  assert.equal(target.clicks.scopedQuery, 0);
});

test("revealJdWareExportEntry keeps waiting when JD removes a stable-looking entry during verification", async () => {
  const target = createWareListEntryPageFixture({
    productSearchContainerCount: 1,
    scopedQueryButtonCount: 1,
    pageQueryButtonCount: 1,
    initialExportEntryCount: 1,
    exportEntryCountSequence: [1, 1, 1, 0, 0, 1, 1, 1],
  });

  await revealJdWareExportEntry(target.page as never);

  assert.equal(target.clicks.scopedQuery, 0);
});

test("prepareJdWareExportEntry validates drawer identity without opening the short-lived export menu", async () => {
  const alreadyOpen = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, exportDrawerCount: 1, scopedSkuTabCount: 1, pageSkuTabCount: 1 });
  assert.equal(await prepareJdWareExportEntry(alreadyOpen.page as never), "already_open");
  assert.equal(alreadyOpen.clicks.scopedQuery, 0);
  assert.equal(alreadyOpen.clicks.exportEntry, 0);

  const closed = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1 });
  assert.equal(await prepareJdWareExportEntry(closed.page as never), "ready_to_open");
  assert.equal(closed.clicks.scopedQuery, 0);
  assert.equal(closed.clicks.batchOperations, 0);

  const wrongOverlay = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, jdOverlayDom: "single", scopedSkuTabCount: 0, pageSkuTabCount: 0 });
  await assert.rejects(prepareJdWareExportEntry(wrongOverlay.page as never), /页签不唯一或身份不匹配/);
  assert.equal(wrongOverlay.clicks.scopedQuery, 0);

  const ambiguousTabs = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, exportDrawerCount: 1, scopedSkuTabCount: 2, pageSkuTabCount: 2 });
  await assert.rejects(prepareJdWareExportEntry(ambiguousTabs.page as never), /页签不唯一/);
  assert.equal(ambiguousTabs.clicks.scopedQuery, 0);

  const nestedDrawer = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, nestedDrawerDom: true, scopedSkuTabCount: 1, pageSkuTabCount: 1 });
  assert.equal(await prepareJdWareExportEntry(nestedDrawer.page as never), "already_open");
  assert.equal(await nestedDrawer.rawDrawerCandidate.count(), 2);
  assert.match(jdWareNormalizedExportDrawerSelector, /:not\(:has\(/);
  assert.equal(nestedDrawer.clicks.scopedQuery, 0);

  const jdOverlay = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, jdOverlayDom: "single", scopedSkuTabCount: 1, pageSkuTabCount: 1 });
  assert.equal(await prepareJdWareExportEntry(jdOverlay.page as never), "already_open");
  assert.match(jdWareNormalizedExportDrawerSelector, /\.jd-overlay/);
  assert.deepEqual(jdOverlay.jdOverlayNodes[0]?.body, { className: "jd-drawer__body", title: "导出条件", skuTab: "SKU导出" });
  assert.equal(jdOverlay.clicks.scopedQuery, 0);

  const independentOverlays = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 2, jdOverlayDom: "multiple", scopedSkuTabCount: 2, pageSkuTabCount: 2 });
  await assert.rejects(prepareJdWareExportEntry(independentOverlays.page as never), /抽屉不唯一/);
  assert.equal(independentOverlays.clicks.scopedQuery, 0);

  const hiddenClone = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, jdOverlayDom: "hidden_clone", scopedSkuTabCount: 1, pageSkuTabCount: 1 });
  assert.equal(await prepareJdWareExportEntry(hiddenClone.page as never), "already_open");
  assert.equal(await hiddenClone.rawDrawerCandidate.count(), 2);
  assert.equal(hiddenClone.clicks.scopedQuery, 0);
});

test("the open-target to export-dialog path reuses one query bootstrap across a repaint and click retry", async () => {
  const retry = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, exportClickFailures: 1 });
  const queryBootstrapState = createJdWareQueryBootstrapState();
  await revealJdWareExportEntry(retry.page as never, queryBootstrapState);
  retry.resetExportEntry();
  await openExportEntryWithRepaintRetry(retry.page as never, queryBootstrapState);
  assert.equal(retry.clicks.scopedQuery, 1);
  assert.equal(retry.clicks.exportEntry, 2);

  const detachedQuery = createWareListEntryPageFixture({ productSearchContainerCount: 1, scopedQueryButtonCount: 1, pageQueryButtonCount: 1, queryClickFailures: 1 });
  const detachedQueryState = createJdWareQueryBootstrapState();
  await assert.rejects(revealJdWareExportEntry(detachedQuery.page as never, detachedQueryState), /detached/);
  await revealJdWareExportEntry(detachedQuery.page as never, detachedQueryState);
  assert.equal(detachedQuery.clicks.scopedQuery, 1);
});

test("clicks a briefly stable JD export entry without waiting for the same menu twice", async () => {
  const briefMenu = createWareListEntryPageFixture({
    productSearchContainerCount: 1,
    scopedQueryButtonCount: 1,
    pageQueryButtonCount: 1,
    batchOperationsCount: 1,
    revealAfterWaits: 1,
    hideExportAfterWaits: 3,
  });

  await openExportEntryWithRepaintRetry(briefMenu.page as never);

  assert.equal(briefMenu.clicks.batchOperations, 1);
  assert.equal(briefMenu.clicks.exportEntry, 1);
});

test("reopens the same reversible batch menu when a JD repaint removes the first export entry", async () => {
  const repaint = createWareListEntryPageFixture({
    productSearchContainerCount: 1,
    scopedQueryButtonCount: 1,
    pageQueryButtonCount: 1,
    batchOperationsCount: 1,
    exportClickFailures: 1,
    hideExportEntryOnClickFailure: true,
  });
  const queryBootstrapState = createJdWareQueryBootstrapState();
  queryBootstrapState.queryTriggered = true;

  await openExportEntryWithRepaintRetry(repaint.page as never, queryBootstrapState);

  assert.equal(repaint.clicks.batchOperations, 2);
  assert.equal(repaint.clicks.scopedQuery, 0);
  assert.equal(repaint.clicks.exportEntry, 2);
});

test("records an auto-import failure audit after a rejected connection without browser startup", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jd-ware-export-"));
  const workbook = path.join(directory, "task.xlsx");
  await writeFile(workbook, "not-a-real-workbook");
  const initial = createWareExportAudit({ baseUrl: "http://127.0.0.1:3000", reuseLatest: false });
  const ready = advanceWareExportAudit(initial, {
    stage: "auto_import",
    taskId: "9371818",
    taskStatus: "completed",
    savedPath: workbook,
  });
  await assert.rejects(
    importSkuFile("http://127.0.0.1:3000", workbook, "A店", async () => { throw new Error("connect ECONNREFUSED"); }),
    /ECONNREFUSED/,
  );
  const failed = advanceWareExportAudit(ready, { status: "failed", error: "connect ECONNREFUSED" });
  assert.deepEqual(
    { status: failed.status, stage: failed.stage, taskId: failed.taskId, savedPath: failed.savedPath, baseUrl: failed.baseUrl },
    { status: "failed", stage: "auto_import", taskId: "9371818", savedPath: workbook, baseUrl: "http://127.0.0.1:3000" },
  );
});

test("records an auto-import HTTP 500 failure audit without browser startup", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jd-ware-export-"));
  const workbook = path.join(directory, "task.xlsx");
  await writeFile(workbook, "not-a-real-workbook");
  await assert.rejects(
    importSkuFile("http://127.0.0.1:3000", workbook, "A店", async () => new Response(JSON.stringify({ message: "server failed" }), { status: 500 })),
    /server failed/,
  );
  const failed = advanceWareExportAudit(createWareExportAudit({ baseUrl: "http://127.0.0.1:3000", reuseLatest: false }), {
    status: "failed", stage: "auto_import", taskId: "9371818", savedPath: workbook, error: "server failed",
  });
  assert.equal(failed.stage, "auto_import");
  assert.equal(failed.error, "server failed");
});

test("master auto-import rejects a successful HTTP response with another shop's batch", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jd-ware-export-"));
  const workbook = path.join(directory, "task.xlsx");
  await writeFile(workbook, "not-a-real-workbook");
  await assert.rejects(
    importSkuFile("http://127.0.0.1:3000", workbook, "A店", async () => Response.json({ ok: true, status: "duplicate", batch: { id: "b", source: "jd_product_master", dataset: "product_master", platform: "京东", shopName: "B店", status: "completed", warningCount: 0, rowCount: 1 } })),
    /SKU 导入失败/,
  );
});

test("master import requires imported=201 and duplicate=200 exactly", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jd-ware-export-"));
  const workbook = path.join(directory, "task.xlsx");
  await writeFile(workbook, "not-a-real-workbook");
  const payload = { ok: true, status: "duplicate", batch: { id: "b", source: "jd_product_master", dataset: "product_master", platform: "京东", shopName: "A店", status: "completed", warningCount: 0, rowCount: 1 } };
  await assert.rejects(importSkuFile("http://127.0.0.1:3000", workbook, "A店", async () => Response.json(payload, { status: 201 })), /SKU 导入失败/);
  await assert.rejects(importSkuFile("http://127.0.0.1:3000", workbook, "A店", async () => Response.json({ ...payload, batch: { ...payload.batch, rowCount: 0 } }, { status: 200 })), /SKU 导入失败/);
});

test("per-store recovery manifests cannot collide and login redirects fail before export UI wait", () => {
  assert.notEqual(wareActiveTaskPath("store-a"), wareActiveTaskPath("store-b"));
  assert.equal(isLikelyJdLoginPage("https://passport.jd.com/login", "账号 登录"), true);
  assert.equal(isLikelyJdLoginPage("https://wares-jdm.jd.com/ware", "登录 批量操作 导出查询商品"), false);
  assert.equal(isLikelyJdLoginPage("https://wares-jdm.jd.com/ware", "账号中心 页面加载中"), false);
  assert.equal(isLikelyJdLoginPage("https://wares-jdm.jd.com/ware", "账号 密码 登录", true), true);
});

test("keeps the actual Chromium process open for interactive login only", () => {
  assert.equal(shouldCloseJdWareBrowserConnection(true), false);
  assert.equal(shouldCloseJdWareBrowserConnection(false), true);
});
