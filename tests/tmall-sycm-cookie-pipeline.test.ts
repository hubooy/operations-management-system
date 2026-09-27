import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import type { TmallStore } from "../lib/netshop/tmall-store-registry";
import {
  assertTmallBrowserProcessOwnership,
  assertCookieMatchesStore,
  buildSycmExportUrl,
  classifySycmInspectionErrors,
  closeOneShotServer,
  closeTmallWorkflowBrowser,
  createHelperInactivityReaper,
  createInitialDownloadManifest,
  decodeArtifactPath,
  encodeArtifactPath,
  getCookieSourceStatus,
  getTmallProfileStatus,
  getTmallProfilesStatus,
  getTmallPromotionStageOptions,
  helperInactivityTimeoutMs,
  helperHealthCorsHeaders,
  helperRequestError,
  jdSilentNoWindowHeader,
  isLegacyXls,
  maximumDaysPerRun,
  maximumWorkflowCoordinationAttempts,
  normalizeN8nExecutionId,
  normalizeTmallStoreKey,
  parseWorkflowCoordinationAttempt,
  parseWorkflowCoordinationKey,
  parseTmallPlanDateRangeHeaders,
  parseCookieHeader,
  runTmallPromotionStageWithTimeout,
  saveDownload,
  shouldLoadCookieForPlan,
  sycmCookieHeaderFromChromeStorage,
  tmallStageAfterRoute,
  tmallCookiePointerFile,
  tmallStoreContextError,
  tmallStoreKeyHeader,
  tmallPlanStartDateHeader,
  tmallPlanEndDateHeader,
  workflowClaimDecision,
  workflowCoordinationWaitExpired,
  workflowCoordinationAttemptHeader,
  workflowCoordinationKeyHeader,
} from "../tools/tmall-sycm-cookie-pipeline";
import {
  tmallDirectProductMasterRoute,
  tmallDirectPromotionRoute,
} from "../tools/tmall-yijiu-direct-pm-contract";

const ownedTmallStore: TmallStore = {
  storeKey: "tmall-test",
  platform: "天猫",
  shopName: "天猫-测试专卖店",
  enabled: true,
  initialStartDate: "2026-08-01",
  portalUrl: "https://example.invalid",
  browser: {
    executablePath: "C:\\Chromium\\chrome.exe",
    userDataDir: "C:\\Tmall\\Test",
    profileName: "Default",
    profileDir: "C:\\Tmall\\Test\\Default",
    debugPort: 9334,
    downloadDir: "C:\\Tmall\\Downloads",
  },
};

test("JD silent copy uses one bounded non-secret no-window header", () => {
  assert.equal(jdSilentNoWindowHeader, "x-teruisi-jd-silent-no-window");
});

test("工作流协调领取使用受控键并在 A 前原子授予唯一 execution", () => {
  assert.equal(workflowCoordinationKeyHeader, "x-teruisi-workflow-key");
  assert.equal(workflowCoordinationAttemptHeader, "x-teruisi-coordination-attempt");
  assert.equal(tmallStoreKeyHeader, "x-teruisi-tmall-store-key");
  assert.equal(maximumWorkflowCoordinationAttempts, 72);
  assert.equal(parseWorkflowCoordinationAttempt(undefined), 0);
  assert.equal(parseWorkflowCoordinationAttempt("0"), 0);
  assert.equal(parseWorkflowCoordinationAttempt("72"), 72);
  assert.equal(parseWorkflowCoordinationAttempt("-1"), null);
  assert.equal(parseWorkflowCoordinationAttempt("bad"), null);
  assert.equal(workflowCoordinationWaitExpired(71, "waiting"), false);
  assert.equal(workflowCoordinationWaitExpired(72, "waiting"), true);
  assert.equal(workflowCoordinationWaitExpired(72, "granted"), false);
  assert.equal(parseWorkflowCoordinationKey("tmall"), "tmall");
  assert.equal(parseWorkflowCoordinationKey("jd-market"), "jd-market");
  assert.equal(parseWorkflowCoordinationKey("unknown"), null);
  assert.equal(parseWorkflowCoordinationKey(["jd"]), null);

  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: null, requestedWorkflow: "jd",
    requestExecutionId: "execution-jd", claimedExecutionId: null,
  }), { coordinationStatus: "granted", shouldClaim: true });
  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: "jd", requestedWorkflow: "jd",
    requestExecutionId: "execution-jd", claimedExecutionId: "execution-jd",
  }), { coordinationStatus: "granted", shouldClaim: false });
  assert.deepEqual(workflowClaimDecision({
    stage: "running", busy: true, activeWorkflow: "jd", requestedWorkflow: "tmall",
    requestExecutionId: "execution-tmall", claimedExecutionId: null,
    requestedTmallStoreKey: "tmall-lili", claimedTmallStoreKey: null,
  }), { coordinationStatus: "waiting", reason: "active_workflow", activeWorkflow: "jd" });
  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: "jd", requestedWorkflow: "jd",
    requestExecutionId: "execution-new", claimedExecutionId: "execution-old",
  }), { coordinationStatus: "waiting", reason: "active_workflow", activeWorkflow: "jd" });
  assert.deepEqual(workflowClaimDecision({
    stage: "failed", busy: false, activeWorkflow: null, requestedWorkflow: "jd-market",
    requestExecutionId: "execution-market", claimedExecutionId: null,
  }), { coordinationStatus: "waiting", reason: "helper_not_ready", activeWorkflow: null });
  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: null, requestedWorkflow: null,
    requestExecutionId: "execution", claimedExecutionId: null,
  }), { error: "missing_or_invalid_workflow_key" });
  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: null, requestedWorkflow: "tmall",
    requestExecutionId: null, claimedExecutionId: null,
  }), { error: "missing_or_invalid_execution_id" });
  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: null, requestedWorkflow: "tmall",
    requestExecutionId: "execution-tmall", claimedExecutionId: null,
  }), { error: "missing_or_invalid_tmall_store_key" });
  assert.deepEqual(workflowClaimDecision({
    stage: "running", busy: true, activeWorkflow: "tmall", requestedWorkflow: "tmall",
    requestExecutionId: "execution-lili", claimedExecutionId: "execution-yijiu",
    requestedTmallStoreKey: "tmall-lili", claimedTmallStoreKey: "tmall-yijiu",
  }), { coordinationStatus: "waiting", reason: "active_workflow", activeWorkflow: "tmall" });
  assert.deepEqual(workflowClaimDecision({
    stage: "ready", busy: false, activeWorkflow: "tmall", requestedWorkflow: "tmall",
    requestExecutionId: "execution-tmall", claimedExecutionId: "execution-tmall",
    requestedTmallStoreKey: "tmall-lili", claimedTmallStoreKey: "tmall-yijiu",
  }), { error: "tmall_store_context_mismatch" });
});

test("天猫 n8n 可为恢复灰度显式绑定完整历史日期且默认仍使用昨天", () => {
  assert.equal(tmallPlanStartDateHeader, "x-teruisi-tmall-plan-start-date");
  assert.equal(tmallPlanEndDateHeader, "x-teruisi-tmall-plan-end-date");
  assert.equal(parseTmallPlanDateRangeHeaders(undefined, undefined), null);
  assert.equal(parseTmallPlanDateRangeHeaders("", ""), null);
  assert.deepEqual(parseTmallPlanDateRangeHeaders("2026-08-25", "2026-08-25"), {
    startDate: "2026-08-25",
    endDate: "2026-08-25",
  });
  assert.throws(() => parseTmallPlanDateRangeHeaders("2026-08-25", undefined), /必须同时提供/);
  assert.throws(() => parseTmallPlanDateRangeHeaders("2026-08-26", "2026-08-25"), /范围无效/);
  assert.throws(() => parseTmallPlanDateRangeHeaders("bad", "2026-08-25"), /请求头无效/);
});

test("天猫 execution owner 同时绑定规范店铺键并拒绝缺失或跨店请求", () => {
  assert.equal(normalizeTmallStoreKey("TMALL-LILI"), "tmall-lili");
  assert.equal(normalizeTmallStoreKey("bad store"), null);
  assert.equal(normalizeTmallStoreKey(["tmall-lili"]), null);
  assert.deepEqual(tmallStoreContextError(null, "tmall-lili"), { error: "missing_or_invalid_tmall_store_key" });
  assert.deepEqual(tmallStoreContextError("tmall-lili", null), { error: "tmall_store_not_claimed" });
  assert.deepEqual(tmallStoreContextError("tmall-lili", "tmall-yijiu"), { error: "tmall_store_context_mismatch" });
  assert.equal(tmallStoreContextError("tmall-lili", "tmall-lili"), null);
  assert.notEqual(tmallCookiePointerFile("tmall-lili"), tmallCookiePointerFile("tmall-yijiu"));
  assert.equal(path.basename(tmallCookiePointerFile("tmall-lili")), "tmall-lili-sycm-cookie-path.txt");
});

test("不同执行可保存同一目标日的不同源文件，交由导入接口比较业务内容", async () => {
  const downloadDir = await mkdtemp(path.join(tmpdir(), "tmall-sycm-run-download-"));
  const store = { browser: { profileDir: "unused", debugPort: 9222, downloadDir } };
  const fileName = "【生意参谋平台】商品_全部_2026-08-05_2026-08-05.xls";
  try {
    const first = await saveDownload(store, "run-one", "2026-08-05", new Uint8Array([1, 2, 3]), fileName);
    const repeated = await saveDownload(store, "run-one", "2026-08-05", new Uint8Array([1, 2, 3]), fileName);
    const changed = await saveDownload(store, "run-two", "2026-08-05", new Uint8Array([1, 2, 4]), fileName);

    assert.equal(repeated.filePath, first.filePath);
    assert.equal(repeated.reusedExistingFile, true);
    assert.notEqual(changed.filePath, first.filePath);
    assert.equal(changed.reusedExistingFile, false);
  } finally {
    await rm(downloadDir, { recursive: true, force: true });
  }
});

test("one-shot helper closes both its listener and retained keep-alive connections", () => {
  let closeCalls = 0;
  let closeAllCalls = 0;
  closeOneShotServer({
    close() { closeCalls += 1; return undefined as never; },
    closeAllConnections() { closeAllCalls += 1; },
  });
  assert.equal(closeCalls, 1);
  assert.equal(closeAllCalls, 1);
});

test("天猫工作流终态只关闭注册端口对应的独立 Chromium", async () => {
  const ports: number[] = [];
  assert.deepEqual(await closeTmallWorkflowBrowser(9334, async (port) => {
    ports.push(port);
    return true;
  }), { ok: true, status: "closed" });
  assert.deepEqual(await closeTmallWorkflowBrowser(9334, async (port) => {
    ports.push(port);
    return false;
  }), { ok: true, status: "already_closed" });
  assert.deepEqual(ports, [9334, 9334]);
  await assert.rejects(() => closeTmallWorkflowBrowser(0, async () => true), /调试端口无效/);
});

test("天猫受控 Chromium 只在端口、可执行文件、用户目录和 Profile 全部匹配时允许进程级关闭", () => {
  const identity = {
    processId: 4321,
    executablePath: "C:\\Chromium\\chrome.exe",
    commandLine: '"C:\\Chromium\\chrome.exe" --remote-debugging-port=9334 "--user-data-dir=C:\\Tmall\\Test" --profile-directory=Default',
  };
  assert.equal(assertTmallBrowserProcessOwnership(ownedTmallStore, identity), 4321);
  assert.throws(() => assertTmallBrowserProcessOwnership(ownedTmallStore, {
    ...identity,
    commandLine: identity.commandLine.replace("9334", "9335"),
  }), /端口、用户目录或 Profile/);
  assert.throws(() => assertTmallBrowserProcessOwnership(ownedTmallStore, {
    ...identity,
    executablePath: "C:\\Other\\chrome.exe",
  }), /可执行文件.*不一致/);
});

test("CDP 关闭失效时只对已核验的本店 Chromium 使用进程级后备关闭", async () => {
  const calls: string[] = [];
  const result = await closeTmallWorkflowBrowser(
    ownedTmallStore,
    async (port) => {
      calls.push(`cdp:${port}`);
      throw new Error("cdp_stalled");
    },
    async (store) => {
      calls.push(`force:${store.storeKey}`);
      return true;
    },
  );
  assert.deepEqual(result, { ok: true, status: "force_closed" });
  assert.deepEqual(calls, ["cdp:9334", "force:tmall-test"]);
});

test("推广阶段硬超时会中止运行并等待受控浏览器关闭后失败返回", async () => {
  let fireTimeout!: () => void;
  let aborted = false;
  let closed = false;
  const result = runTmallPromotionStageWithTimeout((signal) => new Promise<never>((_resolve, reject) => {
    const onAbort = () => {
      aborted = true;
      reject(signal.reason);
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }), {
    timeoutMs: 100,
    schedule: (callback) => {
      fireTimeout = callback;
      return "timer";
    },
    cancel: () => undefined,
    onTimeout: async () => {
      closed = true;
    },
  });
  await Promise.resolve();
  fireTimeout();
  await assert.rejects(result, /推广阶段超过 1 分钟未完成，已失败关闭/);
  assert.equal(aborted, true);
  assert.equal(closed, true);
});

test("Cookie 只接受单行请求头并核验亿玖店登录身份", () => {
  const cookie = parseCookieHeader([
    "Cookie: _tb_token_=token-value",
    "cookie2=session-value",
    "unb=123",
    `sn=${encodeURIComponent("志高亿玖专卖店:测试账号")}`,
  ].join("; "));
  assert.equal(assertCookieMatchesStore(cookie, { shopName: "天猫-志高亿玖专卖店" }), "志高亿玖专卖店:测试账号");
  assert.throws(() => assertCookieMatchesStore(cookie, { shopName: "天猫-志高丽力专卖店" }), /跨店下载/);
  assert.throws(() => parseCookieHeader("a=1\nb=2"), /单行/);
  assert.throws(() => parseCookieHeader("a=1; a=2"), /重复键/);
  assert.throws(() => assertCookieMatchesStore(parseCookieHeader("cookie2=x; unb=1; sn=test"), { shopName: "天猫-志高亿玖专卖店" }), /必要登录键/);
});

test("生意参谋优先从店铺独立 Chrome 读取当前域 Cookie 且排除过期与跨域值", () => {
  const now = Date.parse("2026-08-06T00:00:00Z");
  const header = sycmCookieHeaderFromChromeStorage([
    { name: "cookie2", value: "root-session", domain: ".taobao.com", path: "/", expires: now / 1000 + 3600 },
    { name: "cookie2", value: "specific-session", domain: "sycm.taobao.com", path: "/cc", expires: now / 1000 + 3600 },
    { name: "sn", value: encodeURIComponent("志高亿玖专卖店:测试账号"), domain: ".taobao.com", path: "/" },
    { name: "expired", value: "old", domain: ".taobao.com", path: "/", expires: now / 1000 - 1 },
    { name: "other", value: "wrong", domain: ".example.com", path: "/" },
  ], now);
  const parsed = parseCookieHeader(header);
  assert.equal(parsed.values.get("cookie2"), "specific-session");
  assert.equal(parsed.values.has("expired"), false);
  assert.equal(parsed.values.has("other"), false);
  assert.equal(parsed.values.get("sn"), encodeURIComponent("志高亿玖专卖店:测试账号"));
});

test("生意参谋导出固定为同一单日且路径用规范 Base64 传递", () => {
  const url = new URL(buildSycmExportUrl("2026-08-02", "test-token"));
  assert.equal(url.origin, "https://sycm.taobao.com");
  assert.equal(url.pathname, "/cc/item/view/excel/top.json");
  assert.equal(url.searchParams.get("dateType"), "day");
  assert.equal(url.searchParams.get("dateRange"), "2026-08-02|2026-08-02");
  assert.equal(url.searchParams.get("token"), "test-token");
  assert.throws(() => buildSycmExportUrl("2026-08-01|2026-08-02", "test-token"), /YYYY-MM-DD/);

  const filePath = "D:\\运营管理系统\\outputs\\tmall-sycm-cookie-pipeline\\plan-test.json";
  assert.equal(decodeArtifactPath(encodeArtifactPath(filePath)), filePath);
  assert.throws(() => decodeArtifactPath("%%%"), /Base64/);
});

test("下载响应必须是老式 XLS 魔数", () => {
  assert.equal(isLegacyXls(new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 1])), true);
  assert.equal(isLegacyXls(new TextEncoder().encode('{"code":5810}')), false);
});

test("单轮只规划一个商品日，空计划不读取 Cookie 并可生成空下载清单", () => {
  assert.equal(maximumDaysPerRun, 1);
  assert.deepEqual(getTmallPromotionStageOptions(), { storeKey: "tmall-yijiu", maximumDays: 1 });
  assert.deepEqual(getTmallPromotionStageOptions("tmall-lili"), { storeKey: "tmall-lili", maximumDays: 1 });
  assert.deepEqual(getTmallPromotionStageOptions("tmall-yijiu", ["2026-08-25"]), {
    storeKey: "tmall-yijiu",
    maximumDays: 1,
    dates: ["2026-08-25"],
  });
  assert.deepEqual(getTmallPromotionStageOptions("tmall-yijiu", [], {
    startDate: "2026-08-28",
    endDate: "2026-08-28",
  }), {
    storeKey: "tmall-yijiu",
    maximumDays: 1,
    dates: [],
    planStartDate: "2026-08-28",
    planEndDate: "2026-08-28",
  });
  assert.throws(() => getTmallPromotionStageOptions("bad store"), /店铺键无效/);
  assert.equal(shouldLoadCookieForPlan([]), false);
  assert.equal(shouldLoadCookieForPlan(["2026-08-05"]), true);
  assert.deepEqual(createInitialDownloadManifest({
    runId: "run-empty",
    baseUrl: "http://localhost:3000",
    dates: [],
  }, {
    storeKey: "tmall-yijiu",
    shopName: "天猫-志高亿玖专卖店",
  }, "2026-08-06T01:00:00.000Z"), {
    version: 1,
    runId: "run-empty",
    generatedAt: "2026-08-06T01:00:00.000Z",
    status: "downloaded",
    baseUrl: "http://localhost:3000",
    storeKey: "tmall-yijiu",
    shopName: "天猫-志高亿玖专卖店",
    dates: [],
    files: [],
    errors: [],
  });
});

test("来源零行且缺少目标日明确归类为 SOURCE_NOT_READY", () => {
  assert.deepEqual(classifySycmInspectionErrors([
    { code: "NO_DATA_ROWS" },
    { code: "MISSING_EXPECTED_DATES" },
  ]), {
    code: "SOURCE_NOT_READY",
    message: "SOURCE_NOT_READY：生意参谋目标日尚未返回可导入数据 (NO_DATA_ROWS, MISSING_EXPECTED_DATES)",
  });
  assert.equal(classifySycmInspectionErrors([
    { code: "NO_DATA_ROWS" },
    { code: "MISSING_EXPECTED_DATES" },
    { code: "MISSING_REQUIRED_COLUMNS" },
  ]).code, "INVALID_SOURCE_FILE");
});

test("健康检查只暴露 Cookie 文件就绪状态，并仅允许运营系统本机来源跨域读取", async () => {
  assert.equal(await getCookieSourceStatus(""), "missing");
  assert.equal(await getCookieSourceStatus("relative-cookie.txt"), "invalid");
  assert.equal(await getCookieSourceStatus("C:\\definitely-missing\\cookie.txt"), "missing");

  assert.deepEqual(helperHealthCorsHeaders("https://example.com"), {});
  assert.deepEqual(helperHealthCorsHeaders(undefined), {});
  const headers = helperHealthCorsHeaders("http://localhost:3000", true);
  assert.equal(headers["Access-Control-Allow-Origin"], "http://localhost:3000");
  assert.equal(headers["Access-Control-Allow-Private-Network"], "true");
  assert.equal(JSON.stringify(headers).includes("cookie"), false);
});

test("天猫健康检查接受完整专属 user-data-dir，备用 Cookie 缺失不影响 profile 就绪", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-profile-health-"));
  const executablePath = path.join(root, "chrome.exe");
  const userDataDir = path.join(root, "User Data");
  const profileDir = path.join(userDataDir, "Default");
  try {
    await mkdir(profileDir, { recursive: true });
    await writeFile(executablePath, "test", "utf8");
    await writeFile(path.join(userDataDir, "Local State"), "{}", "utf8");
    assert.equal(await getTmallProfileStatus({ browser: {
      executablePath,
      userDataDir,
      profileName: "Default",
      profileDir,
      debugPort: 9334,
      downloadDir: path.join(root, "downloads"),
    } }), "ready");
    assert.equal(await getTmallProfilesStatus([{ browser: {
      executablePath,
      userDataDir,
      profileName: "Default",
      profileDir,
      debugPort: 9334,
      downloadDir: path.join(root, "downloads"),
    } }]), "ready");
    assert.equal(await getTmallProfileStatus({ browser: {
      profileDir,
      debugPort: 9334,
      downloadDir: path.join(root, "downloads"),
    } }), "invalid");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("一次性 HTTP 辅助进程绑定同一 n8n execution id 并拒绝旧执行、乱序和并发调用", () => {
  assert.equal(normalizeN8nExecutionId("execution-100"), "execution-100");
  assert.equal(normalizeN8nExecutionId("bad execution"), null);
  assert.equal(normalizeN8nExecutionId(undefined), null);

  assert.deepEqual(helperRequestError("ready", false, "/plan", "execution-100", null), {
    error: "execution_not_claimed",
    expected: "/coordination/claim",
  });
  assert.deepEqual(helperRequestError("ready", false, "/product-master", "execution-100", null), {
    error: "execution_not_claimed",
    expected: "/coordination/claim",
  });
  assert.deepEqual(helperRequestError("planned", false, "/product-master", "execution-100", "execution-100"), {
    error: "invalid_stage",
    expected: "promoted",
    actual: "planned",
  });
  assert.deepEqual(helperRequestError("ready", true, "/plan", "execution-100", "execution-100"), { error: "pipeline_busy" });
  assert.deepEqual(helperRequestError("ready", false, "/fetch", "execution-100", null), {
    error: "execution_not_claimed",
    expected: "/coordination/claim",
  });
  assert.deepEqual(helperRequestError("planned", false, "/plan", "execution-100", "execution-100"), {
    error: "invalid_stage",
    expected: "ready",
    actual: "planned",
  });
  assert.deepEqual(helperRequestError("planned", false, "/fetch", "execution-old", "execution-100"), {
    error: "execution_mismatch",
  });
  assert.deepEqual(helperRequestError("ready", false, "/fetch", "execution-100", "execution-100"), {
    error: "invalid_stage",
    expected: "planned",
    actual: "ready",
  });
  assert.deepEqual(helperRequestError("planned", true, "/fetch", "execution-100", "execution-100"), { error: "pipeline_busy" });
  assert.equal(helperRequestError("planned", false, "/fetch", "execution-100", "execution-100"), null);
  assert.equal(helperRequestError("fetched", false, "/import", "execution-100", "execution-100"), null);
  assert.equal(helperRequestError("imported", false, "/promotion", "execution-100", "execution-100"), null);
  assert.equal(helperRequestError("imported", false, tmallDirectPromotionRoute, "execution-100", "execution-100"), null);
  assert.deepEqual(helperRequestError("fetched", false, "/promotion", "execution-100", "execution-100"), {
    error: "invalid_stage",
    expected: "imported",
    actual: "fetched",
  });
  assert.deepEqual(helperRequestError("imported", true, "/promotion", "execution-100", "execution-100"), { error: "pipeline_busy" });
  assert.equal(helperRequestError("promoted", false, "/product-master", "execution-100", "execution-100"), null);
  assert.equal(helperRequestError("promoted", false, tmallDirectProductMasterRoute, "execution-100", "execution-100"), null);
  assert.deepEqual(helperRequestError("promoted", false, "/promotion", "execution-100", "execution-100"), {
    error: "invalid_stage",
    expected: "imported",
    actual: "promoted",
  });
  assert.deepEqual(helperRequestError("completed", false, "/product-master", "execution-100", "execution-100"), {
    error: "invalid_stage",
    expected: "promoted",
    actual: "completed",
  });
  assert.deepEqual(helperRequestError("ready", false, "/plan", null, null), { error: "missing_or_invalid_execution_id" });
  assert.match(JSON.stringify(helperRequestError("completed", false, "/import", "execution-100", "execution-100")), /invalid_stage/);
  assert.deepEqual([
    tmallStageAfterRoute("/plan"),
    tmallStageAfterRoute("/fetch"),
    tmallStageAfterRoute("/import"),
    tmallStageAfterRoute("/promotion"),
    tmallStageAfterRoute(tmallDirectPromotionRoute),
    tmallStageAfterRoute("/product-master"),
    tmallStageAfterRoute(tmallDirectProductMasterRoute),
  ], ["planned", "fetched", "imported", "promoted", "promoted", "completed", "completed"]);
});

test("A/B 等非终态在有界空闲后关闭 helper，下一段领取会取消旧回收计时", () => {
  let closed = 0;
  let cancelled = 0;
  const scheduled: Array<{ callback: () => void; delay: number; handle: object }> = [];
  const reaper = createHelperInactivityReaper({
    close: () => { closed += 1; },
    schedule: (callback, delay) => {
      const handle = {};
      scheduled.push({ callback, delay, handle });
      return handle;
    },
    cancel: (handle) => {
      assert.equal(handle, scheduled.at(-1)?.handle);
      cancelled += 1;
    },
  });

  reaper.arm();
  assert.equal(reaper.isArmed(), true);
  assert.equal(scheduled[0]?.delay, helperInactivityTimeoutMs);
  reaper.clear();
  assert.equal(cancelled, 1);
  assert.equal(reaper.isArmed(), false);

  reaper.arm();
  scheduled.at(-1)?.callback();
  assert.equal(closed, 1);
  assert.equal(reaper.isArmed(), false);
});

test("helper 空闲回收不会关闭仍在执行的活跃请求", () => {
  let busy = true;
  let closed = 0;
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const reaper = createHelperInactivityReaper({
    close: () => { closed += 1; },
    isBusy: () => busy,
    timeoutMs: 250,
    schedule: (callback, delay) => {
      scheduled.push({ callback, delay });
      return { index: scheduled.length - 1 };
    },
    cancel: () => undefined,
  });

  reaper.arm();
  scheduled[0]!.callback();
  assert.equal(closed, 0);
  assert.equal(reaper.isArmed(), true);
  assert.equal(scheduled.length, 2);
  assert.equal(scheduled[1]!.delay, 250);

  busy = false;
  scheduled[1]!.callback();
  assert.equal(closed, 1);
  assert.equal(reaper.isArmed(), false);
});
