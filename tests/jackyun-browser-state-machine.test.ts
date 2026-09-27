import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import type { BrowserAutomationClient } from "../lib/jackyun/cdp-client";
import { JackyunBrowserStateMachine, isSafePreExportBlockedResume } from "../lib/jackyun/browser-state-machine";
import { jackyunModuleOrder } from "../lib/jackyun/post-download";
import {
  assertBoundDownloadUrl,
  assertHistoricalDateReadback,
  armQueryRefreshTracking,
  autoLoginWithSavedBrowserCredentials,
  classifyJackyunSession,
  extractStockAgeOwnerId,
  isModuleQueryRefreshRequest,
  productModeState,
  readRowCountTextState,
  readStockAgeOwnerIdFromPage,
  retryOnceAfterAmbiguousBrowserResult,
  shouldIssueModuleQuery,
  shouldRetryZeroRowQuery,
  stableRowCount,
  type QueryRefreshTracking,
  waitForNestedControls,
  withOwnedControllerChromeCleanup,
} from "../tools/jackyun-browser-controller";

test("row-count readback requests the exact total before accepting a page-sized grid count", () => {
  assert.deepEqual(readRowCountTextState("共 50+ 条 查看总数"), { approximate: true, exactCounts: [] });
  assert.deepEqual(readRowCountTextState("共 5,556 条"), { approximate: false, exactCounts: [5556] });
  assert.deepEqual(readRowCountTextState("共 0 条"), { approximate: false, exactCounts: [0] });
});

test("historical inventory rejects an unrelated MiniUI grid completion without date-bound network evidence", async () => {
  const queryIntentMs = Date.now() - 1_000;
  const tracking: QueryRefreshTracking = {
    token: "query-inventory-no-refresh",
    module: "inventory",
    queryIntentAt: new Date(queryIntentMs).toISOString(),
    requiredDate: "2026-08-05",
    pageProbeArmed: true,
  };
  const completedProbe = {
    token: tracking.token,
    startedAt: queryIntentMs + 100,
    completedAt: queryIntentMs + 200,
    failedAt: null,
  };
  let reads = 0;
  const client = {
    async send(method: string) {
      assert.equal(method, "Runtime.evaluate");
      reads += 1;
      return {
        result: {
          value: {
            text: "共 20,000 条",
            gridTotals: [20_000],
            anyGridLoading: false,
            probes: [completedProbe],
          },
        },
      };
    },
    on() { return () => undefined; },
    close() {},
  } as BrowserAutomationClient;
  const policy = {
    version: "test",
    browser: {
      pageTimeoutMs: 250,
      pollIntervalMs: 100,
      fastPollIntervalMs: 100,
      tableStableTimeoutMs: 250,
      stableSamples: 2,
      downloadDirectory: "unused",
      eventTimeoutMs: 250,
      allowedDownloadHosts: [],
    },
    modules: {
      products: { pageName: "products", requiresQuery: false },
      inventory: { pageName: "inventory", requiresQuery: true },
      inventory_age: { pageName: "inventory_age", requiresQuery: true },
      sales: { pageName: "sales", requiresQuery: true },
      combos: { pageName: "combos", requiresQuery: false },
    },
  };

  await assert.rejects(
    stableRowCount(client, policy, ["branch_stock"], tracking),
    (error: unknown) => {
      assert.equal((error as { code?: unknown }).code, "TABLE_TIMEOUT");
      assert.equal((error as { stage?: unknown }).stage, "query_refresh");
      return true;
    },
  );
  assert.ok(reads >= 2);
});

test("historical inventory accepts unchanged data only after a date-bound module network request completes", async () => {
  const queryIntentMs = Date.now() - 1_000;
  const tracking: QueryRefreshTracking = {
    token: "query-inventory-refreshed",
    module: "inventory",
    queryIntentAt: new Date(queryIntentMs).toISOString(),
    requiredDate: "2026-08-05",
    pageProbeArmed: false,
    networkStartedAt: new Date(queryIntentMs + 100).toISOString(),
    networkCompletedAt: new Date(queryIntentMs + 200).toISOString(),
  };
  const snapshots = [
    { text: "共 20,000 条", gridTotals: [20_000], anyGridLoading: false, probes: [] },
    { text: "共 20,000 条", gridTotals: [20_000], anyGridLoading: false, probes: [] },
    { text: "共 20,000 条", gridTotals: [20_000], anyGridLoading: false, probes: [] },
    { text: "共 20,000 条", gridTotals: [20_000], anyGridLoading: false, probes: [] },
  ];
  let reads = 0;
  const client = {
    async send(method: string) {
      assert.equal(method, "Runtime.evaluate");
      const value = snapshots[Math.min(reads, snapshots.length - 1)];
      reads += 1;
      return { result: { value } };
    },
    on() { return () => undefined; },
    close() {},
  } as BrowserAutomationClient;
  const policy = {
    version: "test",
    browser: {
      pageTimeoutMs: 1_000,
      pollIntervalMs: 100,
      fastPollIntervalMs: 100,
      tableStableTimeoutMs: 1_000,
      stableSamples: 2,
      downloadDirectory: "unused",
      eventTimeoutMs: 1_000,
      allowedDownloadHosts: [],
    },
    modules: {
      products: { pageName: "products", requiresQuery: false },
      inventory: { pageName: "inventory", requiresQuery: true },
      inventory_age: { pageName: "inventory_age", requiresQuery: true },
      sales: { pageName: "sales", requiresQuery: true },
      combos: { pageName: "combos", requiresQuery: false },
    },
  };

  assert.equal(await stableRowCount(client, policy, ["branch_stock"], tracking), 20_000);
  assert.ok(reads >= 2);
});

test("query network refresh evidence is limited to the active module's XHR/fetch", () => {
  assert.equal(isModuleQueryRefreshRequest({
    type: "XHR",
    documentURL: "https://web.jackyun.com/erp/branch_stock_main.html",
    request: { url: "https://web.jackyun.com/api/grid/query" },
  }, "inventory", "2026-08-05"), false);
  assert.equal(isModuleQueryRefreshRequest({
    type: "XHR",
    documentURL: "https://web.jackyun.com/home/dashboard.html",
    request: {
      url: "https://web.jackyun.com/api/branch_stock/query",
      postData: "snapshotDate=2026-08-05",
    },
  }, "inventory", "2026-08-05"), true);
  assert.equal(isModuleQueryRefreshRequest({
    type: "XHR",
    documentURL: "https://web.jackyun.com/erp/branch_stock_main.html",
    request: { url: "https://web.jackyun.com/api/branch_stock/query" },
  }, "inventory", "2026-08-05"), false);
  assert.equal(isModuleQueryRefreshRequest({
    type: "Image",
    documentURL: "https://web.jackyun.com/erp/branch_stock_main.html",
    request: { url: "https://web.jackyun.com/assets/icon.png" },
  }, "inventory"), false);
  assert.equal(isModuleQueryRefreshRequest({
    type: "Fetch",
    documentURL: "https://web.jackyun.com/home/dashboard.html",
    request: { url: "https://web.jackyun.com/api/notifications" },
  }, "inventory"), false);
});

test("observed stockSkuList route counts as inventory refresh without relaxing historical dates or adjacent endpoints", () => {
  const url = "https://web.jackyun.com/jkyun/erp-stock/warehouseStock/stockSkuList";
  const request = { type: "XHR", request: { url, postData: "pageIndex=0&pageSize=100&warehouseId=synthetic" } };
  assert.equal(isModuleQueryRefreshRequest(request, "inventory"), true);
  assert.equal(isModuleQueryRefreshRequest(request, "inventory", "2026-09-05"), false);
  assert.equal(isModuleQueryRefreshRequest({ ...request, request: { url, postData: "snapshotDate=2026-09-05" } }, "inventory", "2026-09-05"), true);
  assert.equal(isModuleQueryRefreshRequest(request, "inventory_age"), false);
  assert.equal(isModuleQueryRefreshRequest({ ...request, type: "Image" }, "inventory"), false);
  for (const ending of ["stockSkuListTotal", "stockSkuListExport", "purchaseList"]) {
    assert.equal(isModuleQueryRefreshRequest({ ...request, request: { url: url.replace("stockSkuList", ending) } }, "inventory"), false);
  }
});

test("current stockSkuList query requires its own successful response and loading completion", async () => {
  for (const status of [200, 500]) {
    const handlers = new Map<string, (params: Record<string, unknown>) => void>();
    const client = { send: async () => ({ result: { value: 0 } }),
      on(method: string, handler: (params: Record<string, unknown>) => void) { handlers.set(method, handler); return () => handlers.delete(method); },
      close() {} } as BrowserAutomationClient;
    const tracking = await armQueryRefreshTracking(client, "inventory", new Date().toISOString(), ["branch_stock"], undefined, true);
    handlers.get("Network.requestWillBeSent")!({ requestId: "current", type: "XHR",
      request: { url: "https://web.jackyun.com/jkyun/erp-stock/warehouseStock/stockSkuList" } });
    handlers.get("Network.responseReceived")!({ requestId: "other", response: { status: 200 } });
    handlers.get("Network.loadingFinished")!({ requestId: "other" });
    assert.equal(tracking.networkCompletedAt, undefined);
    handlers.get("Network.responseReceived")!({ requestId: "current", response: { status } });
    assert.equal(tracking.networkCompletedAt, undefined);
    handlers.get("Network.loadingFinished")!({ requestId: "current" });
    assert.equal(Boolean(tracking.networkCompletedAt), status === 200);
    assert.equal(Boolean(tracking.networkFailedAt), status !== 200);
    tracking.dispose?.();
  }
});

test("historical query refresh rejects a matching request whose HTTP response is not successful", async () => {
  const handlers = new Map<string, Set<(params: Record<string, unknown>) => void>>();
  const client = {
    async send(method: string) {
      assert.equal(method, "Runtime.evaluate");
      return { result: { value: 0 } };
    },
    on(method: string, handler: (params: Record<string, unknown>) => void) {
      const current = handlers.get(method) ?? new Set();
      current.add(handler);
      handlers.set(method, current);
      return () => current.delete(handler);
    },
    close() {},
  } as BrowserAutomationClient;
  const emit = (method: string, params: Record<string, unknown>) => {
    for (const handler of handlers.get(method) ?? []) handler(params);
  };
  const queryIntentAt = new Date(Date.now() - 1_000).toISOString();
  const tracking = await armQueryRefreshTracking(
    client,
    "inventory",
    queryIntentAt,
    ["branch_stock"],
    "2026-08-05",
  );
  emit("Network.requestWillBeSent", {
    requestId: "failed-query",
    type: "XHR",
    request: {
      url: "https://web.jackyun.com/api/branch_stock/query",
      postData: "snapshotDate=2026-08-05",
    },
  });
  emit("Network.responseReceived", {
    requestId: "failed-query",
    response: { status: 500 },
  });
  emit("Network.loadingFinished", { requestId: "failed-query" });

  assert.equal(tracking.networkCompletedAt, undefined);
  assert.ok(tracking.networkFailedAt);
  tracking.dispose?.();
});

test("dedicated Chrome session classification distinguishes login from authenticated menus", () => {
  assert.equal(classifyJackyunSession("忘记密码 为企业注册吉客号 忘记吉客号 登录"), "login_required");
  assert.equal(classifyJackyunSession("主菜单 货品查询 分仓库存查询"), "authenticated");
  assert.equal(classifyJackyunSession("页面加载中"), "unknown");
});

test("controller closes only an owned Chrome and still cleans up after failure", async () => {
  const closedPorts: number[] = [];
  const closeBrowser = async (port: number) => {
    closedPorts.push(port);
    return true;
  };

  assert.equal(await withOwnedControllerChromeCleanup(false, 9223, async () => "reused", closeBrowser), "reused");
  assert.deepEqual(closedPorts, []);
  assert.equal(await withOwnedControllerChromeCleanup(true, 9223, async () => "completed", closeBrowser), "completed");
  assert.deepEqual(closedPorts, [9223]);
  await assert.rejects(
    withOwnedControllerChromeCleanup(true, 9224, async () => { throw new Error("login probe failed"); }, closeBrowser),
    /login probe failed/,
  );
  assert.deepEqual(closedPorts, [9223, 9224]);

  const controllerSource = readFileSync(path.resolve("tools/jackyun-browser-controller.ts"), "utf8");
  assert.match(controllerSource, /const launchedBrowser = await launchDedicatedChrome/);
  assert.match(controllerSource, /const ownsBrowser = Boolean\(launchedBrowser\)/);
  assert.match(controllerSource, /return withOwnedControllerChromeCleanup\(ownsBrowser, port, async \(\) => \{/);
});

test("saved-password login submits only Chrome-autofilled fields and never transports secrets", async () => {
  let expression = "";
  const client = {
    async send(_method: string, params?: Record<string, unknown>) {
      expression = String(params?.expression ?? "");
      return { result: { value: { attempted: true, submitted: true, reason: "submitted" } } };
    },
    on() { return () => undefined; },
    close() {},
  } as BrowserAutomationClient;
  assert.deepEqual(await autoLoginWithSavedBrowserCredentials(client, 0), {
    attempted: true,
    submitted: true,
    reason: "submitted",
  });
  assert.match(expression, /:-webkit-autofill/);
  assert.doesNotMatch(expression, /(?:account|password)\.value\b|JACKYUN_(?:USERNAME|PASSWORD)|credentials\.json/i);

  const controllerSource = readFileSync(path.resolve("tools/jackyun-browser-controller.ts"), "utf8");
  const loginSource = readFileSync(path.resolve("tools/jackyun-browser-login.ts"), "utf8");
  assert.doesNotMatch(controllerSource, /JACKYUN_(?:USERNAME|PASSWORD)|valueLength|bodyPreview/);
  assert.doesNotMatch(loginSource, /credentials\.json|\.username\b|\.password\b/);
});

test("daily module order puts sales fourth and combos last", () => {
  assert.deepEqual(jackyunModuleOrder, ["products", "inventory", "inventory_age", "sales", "combos"]);
});

test("browser state machine rejects skipped and out-of-order module states", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jackyun-state-test-"));
  try {
    const machine = await JackyunBrowserStateMachine.create({
      statePath: path.join(directory, "state.json"),
      runId: "state-test",
      policyVersion: "test",
    });
    await assert.rejects(machine.transition("inventory", "ENTER_MODULE", {}), /当前模块是 products/);
    await machine.transition("products", "ENTER_MODULE", {});
    await assert.rejects(machine.transition("products", "EXPORT_ONCE", {}), /非法状态转换/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("combo export supports one explicit confirmation state before waiting for download", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jackyun-combo-confirm-state-test-"));
  try {
    const machine = await JackyunBrowserStateMachine.create({
      statePath: path.join(directory, "state.json"),
      runId: "combo-confirm-state-test",
      policyVersion: "test",
    });
    const snapshot = machine.snapshot();
    snapshot.currentModule = "combos";
    snapshot.currentState = "EXPORT_ONCE";
    await rm(path.join(directory, "state.json"), { force: true });
    const statePath = path.join(directory, "combo-state.json");
    await writeFile(statePath, JSON.stringify(snapshot), "utf8");
    const comboMachine = await JackyunBrowserStateMachine.load(statePath);
    await comboMachine.transition("combos", "CONFIRM_EXPORT_DIALOG", {
      prompt: "导出列中存在图片列，最多只能导出2000条，确定导出？",
      button: "确定",
    });
    await comboMachine.transition("combos", "WAIT_EVENT_AND_FILE", { confirmedAt: new Date().toISOString() });
    assert.equal(comboMachine.snapshot().currentState, "WAIT_EVENT_AND_FILE");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("blocked or interrupted state can reconcile to the manifest's next module", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jackyun-state-resume-test-"));
  try {
    const statePath = path.join(directory, "state.json");
    const machine = await JackyunBrowserStateMachine.create({ statePath, runId: "resume-test", policyVersion: "test" });
    await machine.transition("products", "ENTER_MODULE", {});
    await machine.block("CONNECTION_LOST", "test");
    await machine.reconcileForResume("inventory_age", { manifestVerified: true });
    assert.equal(machine.snapshot().status, "running");
    assert.equal(machine.snapshot().currentModule, "inventory_age");
    assert.equal(machine.snapshot().currentState, "ENTER_MODULE");
    assert.equal(machine.snapshot().failureCode, undefined);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("blocked resume is allowed only before query, export, download, or import intent", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "jackyun-safe-resume-test-"));
  try {
    const statePath = path.join(directory, "state.json");
    const machine = await JackyunBrowserStateMachine.create({ statePath, runId: "safe-resume", policyVersion: "test" });
    await machine.transition("products", "ENTER_MODULE", {});
    await machine.block("DAILY_RUNNER_FAILED", "login required");
    const state = machine.snapshot();
    assert.equal(isSafePreExportBlockedResume(state, "products", { status: "navigated", navigationIntentAt: new Date().toISOString() }), true);
    assert.equal(isSafePreExportBlockedResume(state, "products", { status: "queried", queryIntentAt: new Date().toISOString() }), false);
    const zeroRowState = {
      status: "queried" as const,
      queryIntentAt: new Date().toISOString(),
      tableReadbackFailure: { code: "zero_rows" as const, observedAt: new Date().toISOString() },
    };
    assert.equal(shouldRetryZeroRowQuery(zeroRowState), true);
    assert.equal(isSafePreExportBlockedResume(state, "products", zeroRowState), true);
    assert.equal(shouldRetryZeroRowQuery({ ...zeroRowState, queryRetryCount: 1 }), false);
    assert.equal(isSafePreExportBlockedResume(state, "products", { ...zeroRowState, queryRetryCount: 1 }), false);
    assert.equal(isSafePreExportBlockedResume(state, "products", { status: "export_armed", exportIntentAt: new Date().toISOString() }), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("browser download ownership rejects a shared-directory file without current event or URL evidence", () => {
  const exportIntentAt = "2026-08-05T18:00:00.000Z";
  const allowedHosts = ["jackyun-shortterm.oss-cn-zhangjiakou.aliyuncs.com"];
  const assertBindingFailure = (error: unknown) => {
    assert.equal((error as { code?: unknown }).code, "FILE_BINDING_FAILED");
    assert.equal((error as { stage?: unknown }).stage, "download_binding");
    return true;
  };

  assert.throws(
    () => assertBoundDownloadUrl(undefined, exportIntentAt, allowedHosts),
    assertBindingFailure,
  );
  assert.throws(
    () => assertBoundDownloadUrl({
      url: "https://jackyun-shortterm.oss-cn-zhangjiakou.aliyuncs.com/export.xlsx",
      observedAt: "2026-08-05T17:59:59.999Z",
      source: "browser_download_event",
    }, exportIntentAt, allowedHosts),
    assertBindingFailure,
  );
  assert.throws(
    () => assertBoundDownloadUrl({
      url: "https://example.invalid/export.xlsx",
      observedAt: "2026-08-05T18:00:01.000Z",
      source: "browser_download_event",
    }, exportIntentAt, allowedHosts),
    assertBindingFailure,
  );

  const boundUrl = "https://jackyun-shortterm.oss-cn-zhangjiakou.aliyuncs.com/export.xlsx";
  assert.equal(assertBoundDownloadUrl({
    url: boundUrl,
    observedAt: "2026-08-05T18:00:01.000Z",
    source: "page_download_hook",
  }, exportIntentAt, allowedHosts), boundUrl);

  const controllerSource = readFileSync(path.resolve("tools/jackyun-browser-controller.ts"), "utf8");
  assert.doesNotMatch(controllerSource, /findLocalDownloadedFile|waitForLocalDownloadedFile|readdir\(downloadDirectory/);
  assert.match(controllerSource, /downloadSignedOssExport/);
  assert.match(controllerSource, /downloadEventAt !== moduleState\.downloadProvenance\.completedAt/);
  assert.match(controllerSource, /browserClientBrowser\.close\(\)/);
});

test("inventory and inventory-age fail closed unless the historical date control reads back exactly", () => {
  assert.equal(assertHistoricalDateReadback("inventory", "2026-08-05", ["2026-08-05"]), "2026-08-05");
  assert.equal(assertHistoricalDateReadback("inventory_age", "2026-08-05", ["2026-08-05"]), "2026-08-05");
  for (const observed of [[], ["2026-08-06"], ["2026-08-05", "2026-08-05"]]) {
    assert.throws(
      () => assertHistoricalDateReadback("inventory", "2026-08-05", observed),
      (error: unknown) => {
        assert.equal((error as { code?: unknown }).code, "FIELD_MISMATCH");
        assert.equal((error as { stage?: unknown }).stage, "field_readback");
        return true;
      },
    );
  }
  assert.throws(
    () => assertHistoricalDateReadback("inventory_age", "2026-08-05", ["2026-08-04"]),
    /FIELD_MISMATCH.*inventory_age/,
  );

  const controllerSource = readFileSync(path.resolve("tools/jackyun-browser-controller.ts"), "utf8");
  assert.doesNotMatch(controllerSource, /v4实时库存|v4无日期框/);
  assert.match(controllerSource, /历史日期控件候选不唯一/);
  assert.match(controllerSource, /日期\|快照\|统计日\|库存日\|截止日\|业务日\|date/);
  assert.match(controllerSource, /source: "historical_date_control"/);
  assert.match(controllerSource, /snapshotEvidence: moduleState\.snapshotEvidence/);
});

test("product navigation waits for the nested mode control instead of accepting dashboard text", () => {
  assert.equal(productModeState("驾驶舱 货品查询 公告 系统目录"), "loading");
  assert.equal(productModeState("货品查询 货品模式"), "goods");
  assert.equal(productModeState("货品查询 规格模式(SKU) 货品编号 规格编号"), "sku");
  assert.equal(productModeState("货品查询 规格模式（SKU）"), "sku");
});

test("nested module controls are polled instead of failing on the outer wrapper", async () => {
  let attempts = 0;
  const client = {
    async send() {
      attempts += 1;
      return { result: { value: attempts >= 2 } };
    },
    on() { return () => undefined; },
    close() {},
  } as BrowserAutomationClient;

  await waitForNestedControls(
    client,
    "branch_stock_main",
    [{ controlId: "warehouseCom" }],
    100,
    0,
  );
  assert.equal(attempts, 2);

  const controllerSource = readFileSync(path.resolve("tools/jackyun-browser-controller.ts"), "utf8");
  assert.match(
    controllerSource,
    /await clickAnyTextEventually\(\s*client,\s*\["筛选", "查询"\]/,
  );
  assert.doesNotMatch(controllerSource, /for \(const grid of grids\) \{\s*const fileExport/);
  assert.match(controllerSource, /if \(missing\.length\) continue;/);
  assert.doesNotMatch(controllerSource, /ownerId:\s*"\d{6,32}"/);
});

test("an ambiguous browser result is retried once and can recover by readback", async () => {
  let attempts = 0;
  const result = await retryOnceAfterAmbiguousBrowserResult(async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("execution context changed");
    return { count: 245, already: true };
  }, 0);
  assert.deepEqual(result, { count: 245, already: true });
  assert.equal(attempts, 2);
});

test("a persistent browser error still fails after one bounded retry", async () => {
  let attempts = 0;
  await assert.rejects(
    retryOnceAfterAmbiguousBrowserResult(async () => {
      attempts += 1;
      throw new Error("still broken");
    }, 0),
    /still broken/,
  );
  assert.equal(attempts, 2);
});

test("a browser timeout is never retried while the first page action may still be running", async () => {
  let attempts = 0;
  await assert.rejects(
    retryOnceAfterAmbiguousBrowserResult(async () => {
      attempts += 1;
      const error = new Error("page action timed out");
      error.name = "JackyunBrowserTimeoutError";
      throw error;
    }, 0),
    (error: Error) => error.name === "JackyunBrowserTimeoutError",
  );
  assert.equal(attempts, 1);
});

test("stock-age owner scope can be read back and an unconfirmed query is reissued", async () => {
  let expression = "";
  const client = {
    async send(_method: string, params?: Record<string, unknown>) {
      expression = String(params?.expression ?? "");
      return { result: { value: "1639245045540225536" } };
    },
    on() { return () => undefined; },
    close() {},
  } as BrowserAutomationClient;
  assert.equal(await readStockAgeOwnerIdFromPage(client), "1639245045540225536");
  assert.ok(expression.includes("/^\\d{6,32}$/"));
  assert.equal(shouldIssueModuleQuery(true, { status: "navigated", queryIntentAt: "2026-08-04T18:18:11.952Z" }), true);
  assert.equal(shouldIssueModuleQuery(true, { status: "queried", queryIntentAt: "2026-08-04T18:18:11.952Z" }), false);
});

test("stock-age export derives the owner scope from the current query request", () => {
  assert.equal(
    extractStockAgeOwnerId('conditionJson=%7B%22ownerId%22%3A%22987654321012345678%22%7D'),
    "987654321012345678",
  );
  assert.equal(extractStockAgeOwnerId('{"ownerId":"123456"}'), "123456");
  assert.equal(extractStockAgeOwnerId('{"ownerId":""}'), undefined);
  assert.equal(extractStockAgeOwnerId('{"ownerId":"not-an-id"}'), undefined);
});
