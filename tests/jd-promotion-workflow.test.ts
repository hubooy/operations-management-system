import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { zipSync } from "fflate";

import {
  inspectJdPromotionCsv,
  jdPromotionReportPrefix,
  selectJdPromotionDownloadTask,
  validateJdPromotionImportProof,
  type JdPromotionDownloadTask,
} from "../lib/jd/promotion-report";
import type { JdStore } from "../lib/jd/store-registry";
import { parseNetshopCsv } from "../lib/netshop/import-service";
import {
  assertJdPromotionAccount,
  dismissJdPromotionMigrationGuide,
  importJdPromotionFile,
  isJdPromotionMigrationGuideInterception,
  jdPromotionReportListUrl,
  jdPromotionReportName,
  normalizeJdPromotionDownloadFile,
  openJdPromotionReport,
  parseJdPromotionArgs,
  type JdPromotionExportResult,
} from "../tools/jd-promotion-export";
import {
  assertJdPromotionCoveragePayload,
  buildJdPromotionCoverageUrl,
  jdPromotionHelperRequestError,
  planJdPromotionMissingDates,
  parseJdPromotionStoreKeyHeader,
  planJdPromotionN8nRun,
  runJdPromotionN8nPlan,
  verifyJdPromotionN8nPlan,
} from "../tools/jd-promotion-n8n-pipeline";

const csvText = [
  "日期,跟单SKU ID,产品线,账户昵称,展现数,点击数,花费,总订单行,总订单金额",
  "20260813,1001,商智,志高亿用-小燕,10,2,3.25,1,998.75",
  "20260814,1002,商智,志高亿用-小燕,20,3,4.50,0,0",
].join("\r\n");

function bytes(text = csvText) {
  return new TextEncoder().encode(text);
}

test("京准通 CSV 字段内的制表符不能被网店导入器误当成分隔符", () => {
  const text = [
    "日期,搜索词,跟单SKU ID,展现数,点击数",
    "20260709,绞肉机\t22s型\\功率2000w\t\t\t志高,10085446559278,6,3",
  ].join("\r\n");
  const rows = parseNetshopCsv(text);
  assert.equal(rows[1]?.values.length, 5);
  assert.equal(rows[1]?.values[1], "绞肉机\t22s型\\功率2000w\t\t\t志高");
  assert.equal(rows[1]?.values[2], "10085446559278");
  assert.equal(rows[1]?.values[3], "6");
  assert.equal(rows[1]?.values[4], "3");
});

test("京准通误标为 CSV 的单文件 ZIP 会保留原件并发布 UTF-8 CSV", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-zip-"));
  try {
    const target = path.join(root, "report.csv");
    const archive = zipSync({ "report-inner.csv": bytes() });
    await writeFile(target, archive);
    assert.equal(await normalizeJdPromotionDownloadFile(target), target);
    assert.equal(new TextDecoder().decode(await readFile(target)), csvText);
    assert.ok((await stat(`${target}.source.zip`)).size > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("京准通 ZIP 的多个同表头 CSV 分片会按名称合并", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-zip-multiple-"));
  try {
    const target = path.join(root, "report.csv");
    await writeFile(target, zipSync({ "one.csv": bytes(), "two.csv": bytes() }));
    await normalizeJdPromotionDownloadFile(target);
    const matrix = parseNetshopCsv(new TextDecoder().decode(await readFile(target)));
    assert.equal(matrix.length, 5);
    assert.equal(matrix[0]?.values[0], "日期");
    assert.equal(matrix[1]?.values[0], "20260813");
    assert.equal(matrix[3]?.values[0], "20260813");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("京准通 ZIP 的 CSV 分片表头不一致时拒绝合并", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-zip-mismatch-"));
  try {
    const target = path.join(root, "report.csv");
    await writeFile(target, zipSync({ "one.csv": bytes(), "two.csv": new TextEncoder().encode("日期,错误列\r\n20260813,1") }));
    await assert.rejects(() => normalizeJdPromotionDownloadFile(target), /分片表头不一致/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function store(storeKey: "jd-yiyong-director" | "jd-maidehao-operator1" = "jd-yiyong-director"): JdStore {
  const cutMeat = storeKey === "jd-maidehao-operator1";
  return {
    storeKey,
    accountLabel: cutMeat ? "志高迈德豪-运营1" : "志高亿用-总监",
    platform: "京东",
    shopName: cutMeat ? "志高切肉机旗舰店" : "志高商用设备旗舰店",
    shopId: cutMeat ? "745866" : "701455",
    enabled: true,
    promotionInitialStartDate: "2026-07-01",
    browser: {
      executablePath: "unused/chromium.exe",
      userDataDir: "unused/user-data",
      profileName: cutMeat ? "Profile 2" : "Default",
      profileDir: cutMeat ? "unused/user-data/Profile 2" : "unused/user-data/Default",
      debugPort: cutMeat ? 9226 : 9224,
      downloadDir: cutMeat ? "unused/cut-meat-downloads" : "unused/downloads",
    },
  };
}

function importPayload(hash: string, options: {
  dateMin?: string;
  dateMax?: string;
  rowCount?: number;
  shopName?: string;
  batchId?: string;
} = {}) {
  const dateMin = options.dateMin ?? "2026-08-13";
  const dateMax = options.dateMax ?? "2026-08-14";
  const rowCount = options.rowCount ?? 2;
  const shopName = options.shopName ?? "志高商用设备旗舰店";
  return {
    ok: true,
    status: "imported",
    batch: {
      id: options.batchId ?? "batch-13-14",
      status: "completed",
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName,
      rowCount,
      warningCount: 0,
      dateMin,
      dateMax,
      totals: { rawFileHash: hash },
    },
    verification: {
      verified: true,
      readbackRowCount: rowCount,
      dateMin,
      dateMax,
      shopName,
    },
  };
}

function coverageResponse(startDate: string, endDate: string, promotionDates: string[]) {
  return Response.json({
    requestedPeriod: { startDate, endDate },
    coverage: {
      promotionDates,
      promotionDatesPagination: { total: promotionDates.length, returned: promotionDates.length, truncated: false },
    },
  });
}

function singleDayBytes(date: string, skuId: string) {
  return bytes([
    "日期,跟单SKU ID,产品线,账户昵称,展现数,点击数,花费,总订单行,总订单金额",
    `${date.replaceAll("-", "")},${skuId},商智,志高亿用-小燕,10,2,3.25,1,998.75`,
  ].join("\r\n"));
}

test("京准通 CSV 必须完整覆盖精确日期范围并重算关键汇总", () => {
  const inspection = inspectJdPromotionCsv(bytes(), "2026-08-13", "2026-08-14");
  assert.deepEqual({
    rowCount: inspection.rowCount,
    columnCount: inspection.columnCount,
    dateMin: inspection.dateMin,
    dateMax: inspection.dateMax,
    accountNicknames: inspection.accountNicknames,
    uniqueSkuCount: inspection.uniqueSkuCount,
    impressions: inspection.impressions,
    clicks: inspection.clicks,
    spendYuan: inspection.spendYuan,
    totalOrders: inspection.totalOrders,
    totalOrderAmountYuan: inspection.totalOrderAmountYuan,
  }, {
    rowCount: 2,
    columnCount: 9,
    dateMin: "2026-08-13",
    dateMax: "2026-08-14",
    accountNicknames: ["志高亿用-小燕"],
    uniqueSkuCount: 2,
    impressions: 30,
    clicks: 5,
    spendYuan: 7.75,
    totalOrders: 1,
    totalOrderAmountYuan: 998.75,
  });
  assert.rejects(Promise.resolve().then(() => inspectJdPromotionCsv(bytes(csvText.split("\r\n").slice(0, 2).join("\r\n")), "2026-08-13", "2026-08-14")), /必须完整覆盖/);
  assert.throws(() => inspectJdPromotionCsv(bytes(), "2026-08-14", "2026-08-14"), /必须完整覆盖/);
});

test("京准通下载中心只接管唯一、精确范围且不在 baseline 中的任务", () => {
  const prefix = jdPromotionReportPrefix("志高亿用-总监", "2026-08-13", "2026-08-14");
  assert.equal(prefix, "志高亿用-总监_AI推广数据自动下载_20260813_20260814");
  const task: JdPromotionDownloadTask = {
    fingerprint: "task-1",
    reportName: `${prefix}_2026年08月15日18时07分23秒下载`,
    status: "报表已生成",
    startDate: "2026-08-13",
    endDate: "2026-08-14",
    createdAt: "2026-08-15 18:07:23",
  };
  assert.equal(selectJdPromotionDownloadTask([task], prefix, task.startDate, task.endDate), task);
  assert.equal(selectJdPromotionDownloadTask([task], prefix, task.startDate, task.endDate, new Set([task.fingerprint])), null);
  assert.throws(() => selectJdPromotionDownloadTask([task, { ...task, fingerprint: "task-2" }], prefix, task.startDate, task.endDate), /多个本轮候选任务/);
});

test("京准通没有恢复清单时只把历史任务纳入 baseline，不直接接管", async () => {
  const source = await readFile(new URL("../tools/jd-promotion-export.ts", import.meta.url), "utf8");
  const taskSelection = source.slice(source.indexOf("async function createOrResumeDownloadTask"), source.indexOf("async function waitAndDownload"));
  assert.doesNotMatch(taskSelection, /if \(!manifest && existing\)/);
  assert.match(taskSelection, /tasks\.map\(\(task\) => task\.fingerprint\)/);
  assert.match(taskSelection, /new Set\(baseline\)/);
  assert.match(taskSelection, /manifest\?\.status === "planned"/);
  assert.match(taskSelection, /manifest\.status = "submitting";\s+await persistManifest\(manifest\);\s+await startButton\.click\(\)/);
});

test("京准通日期面板按精确左右面板有界翻月并读回目标月份", async () => {
  const source = await readFile(new URL("../tools/jd-promotion-export.ts", import.meta.url), "utf8");
  const dateSelection = source.slice(source.indexOf("async function setJdPromotionDateRange"), source.indexOf("async function createOrResumeDownloadTask"));
  assert.match(dateSelection, /jad-date-picker-content-left/);
  assert.match(dateSelection, /jad-date-picker-content-right/);
  assert.match(dateSelection, /attempt <= 24/);
  assert.match(dateSelection, /jad-date-picker-\$\{direction\}-btn-arrow/);
  assert.match(dateSelection, /targetPanel/);
  assert.match(dateSelection, /无法在有界翻页后精确显示目标月份/);
});

test("京准通已完成清单用原文件修正重导后回写当前批次证明", async () => {
  const source = await readFile(new URL("../tools/jd-promotion-export.ts", import.meta.url), "utf8");
  const completedResume = source.slice(source.indexOf('if (manifest?.status === "completed"'), source.indexOf("let browser:"));
  assert.match(completedResume, /manifest\.sha256 = inspection\.sha256/);
  assert.match(completedResume, /manifest\.rowCount = inspection\.rowCount/);
  assert.match(completedResume, /manifest\.batchId = proof\.batchId/);
  assert.match(completedResume, /await persistManifest\(manifest\)/);
});

test("京准通导入证明必须绑定精确店铺、日期、行数、原文件哈希和零告警", () => {
  const inspection = inspectJdPromotionCsv(bytes(), "2026-08-13", "2026-08-14");
  const proof = validateJdPromotionImportProof({
    payload: importPayload(inspection.sha256),
    shopName: store().shopName,
    startDate: inspection.dateMin,
    endDate: inspection.dateMax,
    rowCount: inspection.rowCount,
    rawFileHash: inspection.sha256,
  });
  assert.equal(proof.batchId, "batch-13-14");
  assert.throws(() => validateJdPromotionImportProof({
    payload: importPayload("wrong-hash"),
    shopName: store().shopName,
    startDate: inspection.dateMin,
    endDate: inspection.dateMax,
    rowCount: inspection.rowCount,
    rawFileHash: inspection.sha256,
  }), /缺少精确批次/);
});

test("京准通导入严格区分 imported 201 与 duplicate 200", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-import-status-"));
  const filePath = path.join(root, "promotion.csv");
  await writeFile(filePath, bytes());
  const inspection = inspectJdPromotionCsv(bytes(), "2026-08-13", "2026-08-14");
  const options = parseJdPromotionArgs([
    "--start-date", "2026-08-13",
    "--end-date", "2026-08-14",
    "--base-url", "http://127.0.0.1:3000",
    "--run-id", "status-contract",
  ]);
  const payload = importPayload(inspection.sha256);
  const wrongStatus: typeof fetch = async () => Response.json(payload, { status: 200 });
  await assert.rejects(() => importJdPromotionFile(options, store(), filePath, wrongStatus), /HTTP 200/);
  const imported: typeof fetch = async () => Response.json(payload, { status: 201 });
  const result = await importJdPromotionFile(options, store(), filePath, imported);
  assert.equal(result.proof.status, "imported");

  const duplicatePayload = { ...payload, status: "duplicate" };
  const wrongDuplicateStatus: typeof fetch = async () => Response.json(duplicatePayload, { status: 201 });
  await assert.rejects(() => importJdPromotionFile(options, store(), filePath, wrongDuplicateStatus), /HTTP 201/);
  const duplicate: typeof fetch = async () => Response.json(duplicatePayload, { status: 200 });
  assert.equal((await importJdPromotionFile(options, store(), filePath, duplicate)).proof.status, "duplicate");
});

test("京准通命令行默认使用上海昨天，并接受同月显式范围", () => {
  const now = new Date("2026-08-15T03:00:00+08:00");
  const daily = parseJdPromotionArgs([], now);
  assert.deepEqual([daily.storeKey, daily.startDate, daily.endDate], ["jd-yiyong-director", "2026-08-14", "2026-08-14"]);
  const range = parseJdPromotionArgs(["--start-date", "2026-08-13", "--end-date", "2026-08-14", "--run-id", "range-13-14"], now);
  assert.deepEqual([range.startDate, range.endDate, range.runId], ["2026-08-13", "2026-08-14", "range-13-14"]);
  assert.throws(() => parseJdPromotionArgs(["--start-date", "2026-07-31", "--end-date", "2026-08-01"], now), /同一自然月/);
});

test("京准通推广从当前店铺报表列表按名称进入，不能复用另一店的报表 id", () => {
  assert.equal(jdPromotionReportListUrl, "https://jzt.jd.com/custom-report/#/list");
  assert.equal(jdPromotionReportName, "AI推广数据自动下载");
});

test("京准通只关闭唯一且文案匹配的迁移引导，不点击跳转按钮", async () => {
  let visible = true;
  const clicks: string[] = [];
  const modal = {
    filter: () => modal,
    count: async () => visible ? 1 : 0,
    innerText: async () => "自定义报表已迁移至「报表 - 报表工具」；当前入口仍可正常使用。立即前往 知道了",
    getByRole: (_role: string, options: { name: string }) => ({
      count: async () => 1,
      click: async () => { clicks.push(options.name); visible = false; },
    }),
    waitFor: async ({ state }: { state: string }) => assert.equal(state, "hidden"),
  };
  const page = { locator: (selector: string) => { assert.equal(selector, ".migration-guide-modal"); return modal; } };
  assert.equal(await dismissJdPromotionMigrationGuide(page as never), true);
  assert.deepEqual(clicks, ["知道了"]);
  assert.equal(await dismissJdPromotionMigrationGuide(page as never), false);
});

test("京准通未知或不唯一弹窗失败关闭，不能猜测关闭控件", async () => {
  let count = 2;
  let text = "自定义报表已迁移至「报表 - 报表工具」；当前入口仍可正常使用";
  let buttonCount = 1;
  let clicks = 0;
  const modal = {
    filter: () => modal,
    count: async () => count,
    innerText: async () => text,
    getByRole: () => ({ count: async () => buttonCount, click: async () => { clicks += 1; } }),
  };
  const page = { locator: () => modal };
  await assert.rejects(() => dismissJdPromotionMigrationGuide(page as never), /弹窗不唯一/);
  count = 1;
  text = "请进行安全验证";
  await assert.rejects(() => dismissJdPromotionMigrationGuide(page as never), /未知弹窗/);
  text = "自定义报表已迁移至「报表 - 报表工具」；当前入口仍可正常使用";
  buttonCount = 2;
  await assert.rejects(() => dismissJdPromotionMigrationGuide(page as never), /控件不唯一/);
  assert.equal(clicks, 0);
});

test("京准通迁移引导延迟挂载时仅在精确遮挡错误后补一次可逆报表入口点击", async () => {
  let modalVisible = false;
  let reportClicks = 0;
  let guideClicks = 0;
  const modal = {
    filter: () => modal,
    count: async () => modalVisible ? 1 : 0,
    innerText: async () => "自定义报表已迁移至「报表 - 报表工具」；当前入口仍可正常使用",
    getByRole: (_role: string, options: { name: string }) => ({
      count: async () => 1,
      click: async () => { assert.equal(options.name, "知道了"); guideClicks += 1; modalVisible = false; },
    }),
    waitFor: async () => undefined,
  };
  const report = {
    filter: () => report,
    first: () => report,
    waitFor: async () => undefined,
    count: async () => 1,
    click: async () => {
      reportClicks += 1;
      if (reportClicks === 1) {
        modalVisible = true;
        throw new Error("migration-guide-modal intercepts pointer events");
      }
    },
  };
  const page = {
    waitForFunction: async () => undefined,
    locator: (selector: string) => selector === "body" ? { innerText: async () => "志高亿用-总监" } : modal,
    getByText: () => report,
    getByRole: () => ({ waitFor: async () => undefined }),
  };
  await openJdPromotionReport(page as never, store());
  assert.deepEqual([reportClicks, guideClicks], [2, 1]);
  assert.equal(isJdPromotionMigrationGuideInterception(new Error("unknown overlay intercepts pointer events")), false);
  assert.equal(isJdPromotionMigrationGuideInterception(new Error("migration-guide-modal timeout")), false);

  let unknownClicks = 0;
  const unknownReport = {
    ...report,
    filter: () => unknownReport,
    first: () => unknownReport,
    click: async () => { unknownClicks += 1; throw new Error("unknown overlay intercepts pointer events"); },
  };
  await assert.rejects(() => openJdPromotionReport({ ...page, getByText: () => unknownReport } as never, store()), /unknown overlay/);
  assert.equal(unknownClicks, 1);
  assert.equal(guideClicks, 1);
});

test("京准通推广等待列表页异步渲染受控账号，并继续拒绝缺失身份", async () => {
  let visible = false;
  const page = {
    waitForFunction: async () => { visible = true; },
    locator: () => ({ innerText: async () => visible ? "自定义报表\n志高迈德豪-运营1" : "自定义报表" }),
  };
  await assertJdPromotionAccount(page as never, store("jd-maidehao-operator1"));
  assert.equal(visible, true);

  const missingPage = {
    waitForFunction: async () => { throw new Error("timeout"); },
    locator: () => ({ innerText: async () => "自定义报表" }),
  };
  await assert.rejects(() => assertJdPromotionAccount(missingPage as never, store("jd-maidehao-operator1")), /登录身份不一致/);
});

test("京准通 helper 绑定同一 execution 并拒绝空、跨执行、并发和乱序请求", () => {
  assert.equal(jdPromotionHelperRequestError("ready", false, "/jd-promotion/plan", "execution-1", null), null);
  assert.deepEqual(jdPromotionHelperRequestError("ready", false, "/jd-promotion/run", "execution-1", null), { error: "execution_not_claimed", expected: "/jd-promotion/plan" });
  assert.deepEqual(jdPromotionHelperRequestError("planned", false, "/jd-promotion/run", "other", "execution-1"), { error: "execution_mismatch" });
  assert.deepEqual(jdPromotionHelperRequestError("planned", true, "/jd-promotion/run", "execution-1", "execution-1"), { error: "pipeline_busy" });
  assert.deepEqual(jdPromotionHelperRequestError("planned", false, "/jd-promotion/verify", "execution-1", "execution-1"), { error: "invalid_stage", expected: "executed|completed", actual: "planned" });
  assert.deepEqual(jdPromotionHelperRequestError("ready", false, "/jd-promotion/plan", null, null), { error: "missing_or_invalid_execution_id" });
  assert.equal(jdPromotionHelperRequestError("executed", false, "/jd-promotion/plan", "execution-1", "execution-1"), null);
  assert.equal(jdPromotionHelperRequestError("executed", false, "/jd-promotion/run", "execution-1", "execution-1"), null);
  assert.equal(jdPromotionHelperRequestError("completed", false, "/jd-promotion/verify", "execution-1", "execution-1"), null);
});

test("京准通 n8n 店铺请求头只接受两条显式推广白名单", () => {
  assert.throws(() => parseJdPromotionStoreKeyHeader(undefined), /店铺请求头无效/);
  assert.equal(parseJdPromotionStoreKeyHeader("jd-yiyong-director"), "jd-yiyong-director");
  assert.equal(parseJdPromotionStoreKeyHeader("jd-maidehao-operator1"), "jd-maidehao-operator1");
  assert.throws(() => parseJdPromotionStoreKeyHeader("jd-chudian-weizhang"), /不在推广工作流白名单/);
  assert.throws(() => parseJdPromotionStoreKeyHeader(["jd-maidehao-operator1"]), /店铺请求头无效/);
});

test("京准通下载前按系统覆盖计算缺口并把跨月缺口限制为逐日计划", () => {
  assert.equal(
    buildJdPromotionCoverageUrl("http://localhost:3000", store(), "2026-07-31", "2026-08-02"),
    `http://localhost:3000/api/netshop/promotion-performance/overview?platform=${encodeURIComponent("京东")}&outlet=${encodeURIComponent(`京东\u001f${store().shopName}`)}&startDate=2026-07-31&endDate=2026-08-02`,
  );
  assert.deepEqual(planJdPromotionMissingDates({
    startDate: "2026-07-31",
    endDate: "2026-08-03",
    coveredDates: ["2026-07-31", "2026-08-02"],
  }), {
    dates: ["2026-08-01", "2026-08-03"],
    missingDateCount: 2,
    deferredDateCount: 0,
  });
  const capped = planJdPromotionMissingDates({
    startDate: "2026-07-01",
    endDate: "2026-08-10",
    coveredDates: [],
  });
  assert.equal(capped.dates.length, 31);
  assert.equal(capped.dates[0], "2026-07-01");
  assert.equal(capped.dates.at(-1), "2026-07-31");
  assert.equal(capped.deferredDateCount, 10);
});

test("京准通缺口扫描拒绝截断、计数异常和区间外日期", () => {
  assert.throws(() => assertJdPromotionCoveragePayload({
    requestedPeriod: { startDate: "2026-08-13", endDate: "2026-08-14" },
    coverage: {
      promotionDates: ["2026-08-13"],
      promotionDatesPagination: { total: 2, returned: 1, truncated: true },
    },
  }, { startDate: "2026-08-13", endDate: "2026-08-14" }), /截断或分页计数不一致/);
  assert.throws(() => assertJdPromotionCoveragePayload({
    requestedPeriod: { startDate: "2026-08-13", endDate: "2026-08-14" },
    coverage: {
      promotionDates: ["2026-08-15"],
      promotionDatesPagination: { total: 1, returned: 1, truncated: false },
    },
  }, { startDate: "2026-08-13", endDate: "2026-08-14" }), /区间外日期/);
});

test("京准通 n8n 计划拒绝店铺对象与请求头跨店错配", async () => {
  await assert.rejects(() => planJdPromotionN8nRun({
    executionId: "mismatched-store",
    storeKey: "jd-yiyong-director",
    store: store("jd-maidehao-operator1"),
  }), /店铺对象与受控请求头不一致/);
});

test("京准通 n8n 计划先读取切肉机店覆盖，再只固化缺失日期", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-cut-meat-plan-"));
  const targetStore = store("jd-maidehao-operator1");
  const plan = await planJdPromotionN8nRun({
    root,
    baseUrl: "http://localhost:3000",
    executionId: "cut-meat-execution",
    storeKey: targetStore.storeKey,
    startDate: "2026-08-13",
    endDate: "2026-08-14",
    store: targetStore,
    request: async () => coverageResponse("2026-08-13", "2026-08-14", ["2026-08-13"]),
    profileStatus: async (stores) => {
      assert.deepEqual(stores.map((item) => [item.storeKey, item.browser.profileName]), [["jd-maidehao-operator1", "Profile 2"]]);
      return "ready";
    },
    runIdFactory: () => "jd-promotion-cut-meat-13-14",
  });
  assert.deepEqual(plan.store, {
    storeKey: "jd-maidehao-operator1",
    shopId: "745866",
    shopName: "志高切肉机旗舰店",
    accountLabel: "志高迈德豪-运营1",
  });
  assert.deepEqual([plan.scannedStartDate, plan.scannedEndDate], ["2026-08-13", "2026-08-14"]);
  assert.deepEqual([plan.startDate, plan.endDate, plan.dates, plan.stage], ["2026-08-14", "2026-08-14", ["2026-08-14"], "planned"]);
});

test("京准通 n8n A/B/C 按缺口逐日串行并独立复验文件、批次和最终覆盖", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-n8n-"));
  const targetStore = store();
  const daily = await Promise.all(["2026-08-13", "2026-08-14"].map(async (date, index) => {
    const fileBytes = singleDayBytes(date, `100${index + 1}`);
    const savedPath = path.join(root, `promotion-${date}.csv`);
    await writeFile(savedPath, fileBytes);
    const inspection = inspectJdPromotionCsv(fileBytes, date, date);
    const importResult = validateJdPromotionImportProof({
      payload: importPayload(inspection.sha256, {
        dateMin: date,
        dateMax: date,
        rowCount: inspection.rowCount,
        batchId: `batch-${date}`,
      }),
      shopName: targetStore.shopName,
      startDate: date,
      endDate: date,
      rowCount: inspection.rowCount,
      rawFileHash: inspection.sha256,
    });
    return { date, savedPath, inspection, importResult };
  }));
  const plan = await planJdPromotionN8nRun({
    root,
    now: new Date("2026-08-15T03:00:00+08:00"),
    baseUrl: "http://localhost:3000",
    executionId: "execution-13-14",
    startDate: "2026-08-13",
    endDate: "2026-08-14",
    store: targetStore,
    request: async () => coverageResponse("2026-08-13", "2026-08-14", []),
    profileStatus: async () => "ready",
    runIdFactory: () => "jd-promotion-13-14",
  });
  assert.deepEqual([plan.store.storeKey, plan.dates, plan.stage], ["jd-yiyong-director", ["2026-08-13", "2026-08-14"], "planned"]);
  let active = 0;
  let maximumActive = 0;
  const calls: string[] = [];
  await runJdPromotionN8nPlan(plan, { root, store: targetStore, run: async (options) => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    calls.push(options.startDate);
    await Promise.resolve();
    const item = daily.find((candidate) => candidate.date === options.startDate)!;
    active -= 1;
    return {
      ok: true,
      runId: options.runId,
      storeKey: targetStore.storeKey,
      shopName: targetStore.shopName,
      startDate: item.date,
      endDate: item.date,
      reportName: `受控报表-${item.date}`,
      taskCreatedAt: "2026-08-15 18:07:23",
      savedPath: item.savedPath,
      fileSizeBytes: (await stat(item.savedPath)).size,
      sha256: item.inspection.sha256,
      rowCount: item.inspection.rowCount,
      accountNicknames: item.inspection.accountNicknames,
      productLines: item.inspection.productLines,
      impressions: item.inspection.impressions,
      clicks: item.inspection.clicks,
      spendYuan: item.inspection.spendYuan,
      totalOrders: item.inspection.totalOrders,
      totalOrderAmountYuan: item.inspection.totalOrderAmountYuan,
      importResult: item.importResult,
    } satisfies JdPromotionExportResult;
  } });
  assert.equal(plan.stage, "executed");
  assert.deepEqual(calls, ["2026-08-13", "2026-08-14"]);
  assert.equal(maximumActive, 1);
  const request: typeof fetch = async (input) => {
    const url = String(input);
    if (url.includes("promotion-performance/overview")) return coverageResponse("2026-08-13", "2026-08-14", ["2026-08-13", "2026-08-14"]);
    const item = daily.find((candidate) => url.includes(encodeURIComponent(candidate.importResult.batchId)))!;
    return Response.json({ items: [{
      id: item.importResult.batchId,
      status: "completed",
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName: targetStore.shopName,
      warningCount: 0,
      rowCount: item.inspection.rowCount,
      dateMin: item.date,
      dateMax: item.date,
      totals: { rawFileHash: item.inspection.sha256 },
    }] });
  };
  const verified = await verifyJdPromotionN8nPlan(plan, { root, store: targetStore, request });
  assert.deepEqual([verified.stage, verified.rowCount, verified.completedDates, plan.stage], ["verify", 2, ["2026-08-13", "2026-08-14"], "completed"]);
});

test("京准通 n8n 无缺口时不调用浏览器 runner，只完成覆盖复核", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-no-gap-"));
  const targetStore = store();
  const request: typeof fetch = async () => coverageResponse("2026-08-13", "2026-08-14", ["2026-08-13", "2026-08-14"]);
  const plan = await planJdPromotionN8nRun({
    root,
    now: new Date("2026-08-15T03:00:00+08:00"),
    baseUrl: "http://localhost:3000",
    executionId: "no-gap",
    startDate: "2026-08-13",
    endDate: "2026-08-14",
    store: targetStore,
    request,
    profileStatus: async () => { throw new Error("无缺口时不得检查浏览器 Profile"); },
    runIdFactory: () => "jd-promotion-no-gap",
  });
  assert.deepEqual([plan.dates, plan.stage], [[], "executed"]);
  const run = await runJdPromotionN8nPlan(plan, { root, store: targetStore, run: async () => { throw new Error("不得调用"); } });
  assert.equal(run.status, "no_gap");
  const verified = await verifyJdPromotionN8nPlan(plan, { root, store: targetStore, request });
  assert.deepEqual([verified.status, verified.rowCount, plan.stage], ["no_gap", 0, "completed"]);
});

test("京准通 C 节点失败后保留逐日证据并只重试核验", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-promotion-verify-recovery-"));
  const targetStore = store();
  const date = "2026-08-13";
  const fileBytes = singleDayBytes(date, "1001");
  const savedPath = path.join(root, `promotion-${date}.csv`);
  await writeFile(savedPath, fileBytes);
  const inspection = inspectJdPromotionCsv(fileBytes, date, date);
  const importResult = validateJdPromotionImportProof({
    payload: importPayload(inspection.sha256, { dateMin: date, dateMax: date, rowCount: 1, batchId: "batch-recovery" }),
    shopName: targetStore.shopName,
    startDate: date,
    endDate: date,
    rowCount: 1,
    rawFileHash: inspection.sha256,
  });
  const plan = await planJdPromotionN8nRun({
    root,
    now: new Date("2026-08-15T03:00:00+08:00"),
    baseUrl: "http://localhost:3000",
    executionId: "verify-recovery-1",
    startDate: date,
    endDate: date,
    store: targetStore,
    request: async () => coverageResponse(date, date, []),
    profileStatus: async () => "ready",
    runIdFactory: () => "jd-promotion-verify-recovery",
  });
  await runJdPromotionN8nPlan(plan, { root, store: targetStore, run: async (options) => ({
    ok: true,
    runId: options.runId,
    storeKey: targetStore.storeKey,
    shopName: targetStore.shopName,
    startDate: date,
    endDate: date,
    reportName: "受控报表",
    taskCreatedAt: "2026-08-15 18:07:23",
    savedPath,
    fileSizeBytes: fileBytes.byteLength,
    sha256: inspection.sha256,
    rowCount: inspection.rowCount,
    accountNicknames: inspection.accountNicknames,
    productLines: inspection.productLines,
    impressions: inspection.impressions,
    clicks: inspection.clicks,
    spendYuan: inspection.spendYuan,
    totalOrders: inspection.totalOrders,
    totalOrderAmountYuan: inspection.totalOrderAmountYuan,
    importResult,
  }) });
  await assert.rejects(() => verifyJdPromotionN8nPlan(plan, {
    root,
    store: targetStore,
    request: async () => Response.json({ items: [] }),
  }), /已发布批次与下载、日期或导入证明不一致/);
  assert.deepEqual([plan.stage, plan.failure?.stage, plan.results.length], ["failed", "verify", 1]);

  const resumed = await planJdPromotionN8nRun({
    root,
    now: new Date("2026-08-15T03:05:00+08:00"),
    baseUrl: "http://localhost:3000",
    executionId: "verify-recovery-2",
    startDate: date,
    endDate: date,
    store: targetStore,
    request: async () => coverageResponse(date, date, [date]),
    profileStatus: async () => "ready",
  });
  assert.deepEqual([resumed.runId, resumed.stage, resumed.dates, resumed.results.length], [plan.runId, "executed", [date], 1]);
  const recoveredRun = await runJdPromotionN8nPlan(resumed, {
    root,
    store: targetStore,
    run: async () => { throw new Error("不得重复下载"); },
  });
  assert.equal(recoveredRun.verificationOnly, true);
  const request: typeof fetch = async (input) => String(input).includes("promotion-performance/overview")
    ? coverageResponse(date, date, [date])
    : Response.json({ items: [{
      id: importResult.batchId,
      status: "completed",
      source: "jd_promotion",
      dataset: "ad",
      platform: "京东",
      shopName: targetStore.shopName,
      warningCount: 0,
      rowCount: 1,
      dateMin: date,
      dateMax: date,
      totals: { rawFileHash: inspection.sha256 },
    }] });
  assert.equal((await verifyJdPromotionN8nPlan(resumed, { root, store: targetStore, request })).status, "completed");
});

test("京准通 n8n 模板保持未激活、先原子领取 helper 再以同一 execution ID 串联三段请求", async () => {
  const workflow = JSON.parse(await readFile(new URL("../automation/n8n/jd-promotion-daily.workflow.json", import.meta.url), "utf8")) as {
    active: boolean;
    settings?: { timezone?: string };
    connections: Record<string, { main?: Array<Array<{ node?: string }>> }>;
    nodes: Array<{ name: string; type: string; parameters?: { url?: string; rule?: { interval?: Array<{ expression?: string }> }; assignments?: { assignments?: Array<{ name?: string; value?: string }> }; headerParameters?: { parameters?: Array<{ name?: string; value?: string }> } } }>;
  };
  assert.equal(workflow.active, false);
  assert.equal(workflow.settings?.timezone, "Asia/Shanghai");
  assert.equal(workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger")?.parameters?.rule?.interval?.[0]?.expression, "40 10 * * *");
  const requests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest");
  assert.deepEqual(requests.map((node) => node.parameters?.url), [
    "http://127.0.0.1:5791/coordination/claim",
    "http://127.0.0.1:5791/jd-promotion/plan",
    "http://127.0.0.1:5791/jd-promotion/run",
    "http://127.0.0.1:5791/jd-promotion/verify",
  ]);
  for (const request of requests) {
    assert.deepEqual(request.parameters?.headerParameters?.parameters?.[0], { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" });
  }
  assert.deepEqual(requests[0]?.parameters?.headerParameters?.parameters?.slice(1), [
    { name: "X-TERUISI-COORDINATION-ATTEMPT", value: "={{ $runIndex }}" },
    { name: "X-TERUISI-WORKFLOW-KEY", value: "jd-promotion" },
  ]);
  assert.deepEqual(requests[1]?.parameters?.headerParameters?.parameters?.[1], { name: "X-TERUISI-JD-PROMOTION-STORE-KEY", value: "jd-yiyong-director" });
  assert.deepEqual(requests[1]?.parameters?.headerParameters?.parameters?.slice(2), [
    { name: "X-TERUISI-JD-PROMOTION-START-DATE", value: "={{ $execution.mode === 'manual' ? $('手动补跑日期').first().json.startDate : '' }}" },
    { name: "X-TERUISI-JD-PROMOTION-END-DATE", value: "={{ $execution.mode === 'manual' ? $('手动补跑日期').first().json.endDate : '' }}" },
  ]);
  assert.deepEqual(workflow.nodes.find((node) => node.name === "手动补跑日期")?.parameters?.assignments?.assignments?.map((item) => [item.name, item.value]), [
    ["startDate", "2026-08-20"],
    ["endDate", "2026-08-20"],
  ]);
  assert.equal(workflow.connections["手动补跑日期"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["每天 10:40 执行"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["等待前序流程释放 helper"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["helper 领取成功？"]?.main?.[0]?.[0]?.node, "A·固化京准通目标日期与店铺");
  assert.equal(workflow.connections["helper 领取成功？"]?.main?.[1]?.[0]?.node, "等待前序流程释放 helper");
});

test("切肉机京准通 n8n 模板固定 Profile 2、每天 10:50 扫描缺口并先领取 helper", async () => {
  const workflow = JSON.parse(await readFile(new URL("../automation/n8n/jd-promotion-cut-meat-20260813-14.workflow.json", import.meta.url), "utf8")) as {
    id: string;
    active: boolean;
    settings?: { timezone?: string };
    connections: Record<string, { main?: Array<Array<{ node?: string }>> }>;
    nodes: Array<{ name: string; type: string; parameters?: { url?: string; rule?: { interval?: Array<{ expression?: string }> }; assignments?: { assignments?: Array<{ name?: string; value?: string }> }; headerParameters?: { parameters?: Array<{ name?: string; value?: string }> } } }>;
  };
  assert.equal(workflow.id, "JdPromotionCutMeat2026");
  assert.equal(workflow.active, false);
  assert.equal(workflow.settings?.timezone, "Asia/Shanghai");
  assert.equal(workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger")?.parameters?.rule?.interval?.[0]?.expression, "50 10 * * *");
  const dates = workflow.nodes.find((node) => node.type === "n8n-nodes-base.set")?.parameters?.assignments?.assignments;
  assert.deepEqual(dates?.map((item) => [item.name, item.value]), [["startDate", "2026-08-20"], ["endDate", "2026-08-20"]]);
  const requests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest");
  assert.deepEqual(requests.map((node) => node.parameters?.url), [
    "http://127.0.0.1:5791/coordination/claim",
    "http://127.0.0.1:5791/jd-promotion-cut-meat/plan",
    "http://127.0.0.1:5791/jd-promotion/run",
    "http://127.0.0.1:5791/jd-promotion/verify",
  ]);
  assert.deepEqual(requests[0]?.parameters?.headerParameters?.parameters, [
    { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
    { name: "X-TERUISI-COORDINATION-ATTEMPT", value: "={{ $runIndex }}" },
    { name: "X-TERUISI-WORKFLOW-KEY", value: "jd-promotion" },
  ]);
  assert.deepEqual(requests[1]?.parameters?.headerParameters?.parameters, [
    { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
    { name: "X-TERUISI-JD-PROMOTION-STORE-KEY", value: "jd-maidehao-operator1" },
    { name: "X-TERUISI-JD-PROMOTION-START-DATE", value: "={{ $execution.mode === 'manual' ? $('固定补跑日期').first().json.startDate : '' }}" },
    { name: "X-TERUISI-JD-PROMOTION-END-DATE", value: "={{ $execution.mode === 'manual' ? $('固定补跑日期').first().json.endDate : '' }}" },
  ]);
  assert.equal(workflow.connections["固定补跑日期"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["每天 10:50 执行"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["等待前序流程释放 helper"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["helper 领取成功？"]?.main?.[0]?.[0]?.node, "A·固化切肉机店铺与目标日期");
  assert.equal(workflow.connections["helper 领取成功？"]?.main?.[1]?.[0]?.node, "等待前序流程释放 helper");
});
