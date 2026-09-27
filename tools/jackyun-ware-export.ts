import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readFile, rename, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { Locator, Page, Response } from "playwright-core";
import {
  parseJdWareExportTaskRows,
  assertJdWareExportRecoveryIdentity,
  selectExistingJdWareExportTask,
  selectRecoverableJdWareExportTask,
  decideJdWareExportBaselineRecoveryAbandonment,
  unseenJdWareExportTasks,
  type JdWareExportRecovery,
  type JdWareExportTask,
} from "../lib/jd/ware-export";
import { closeChromeBrowser, waitForChrome } from "../lib/jackyun/cdp-client";
import { connectPlaywrightBrowser, connectPlaywrightJackyunTarget } from "../lib/jackyun/playwright-client";
import { readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { getJdStore } from "../lib/jd/store-registry";
import { withJdChromiumRunLock } from "../lib/jd/chromium-run-lock";
import { hasJdInteractivePageGate, isJdInteractiveBrowserFailure, launchJdWareBrowser, revealJdBrowserForInteractiveFailure } from "../lib/jd/browser-mode";
import { parseXlsxFirstSheet } from "../lib/imports/xlsx";
import { ensureJdStoreAuthenticatedSession } from "./jd-saved-login";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const targetUrl = "https://wares-jdm.jd.com/ware/wareList?activeTab=OnsaleWare&businessModel=0";
const artifactDir = path.join(projectRoot, "outputs", "jd-ware-export");
const legacyActiveTaskPath = path.join(artifactDir, "active-task.json");
const pollIntervalMs = 700;
const refreshIntervalMs = 3_000;
const maximumJdWareWorkbookBytes = 25 * 1024 * 1024;
const jdWareCreateExportApi = "dsm.product.manage.view.batchJobService.createExportJob";
const jdWareProductQueryApi = "dsm.product.manage.ProductInfoReadViewService.queryValidProductList";
export const jdWareTargetNavigationTimeoutMs = 30_000;
export const jdWareInitialProductQueryTimeoutMs = 60_000;

async function withJdWareExportRunLock<T>(task: () => Promise<T>) {
  return withJdChromiumRunLock(
    "product-master-script",
    task,
    path.join(artifactDir, "jd-ware-export.run-lock"),
  );
}

export function wareActiveTaskPath(storeKey: string) {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(storeKey)) throw new Error("店铺键不能用于活动任务清单路径。");
  return path.join(artifactDir, `active-task-${storeKey}.json`);
}

export function isLikelyJdLoginPage(url: string, pageText: string, hasPasswordInput = false) {
  if (/passport|login/i.test(url)) return true;
  const hasLoginSignal = (hasPasswordInput || /密码/.test(pageText)) && /登录/.test(pageText) && /账号|手机|用户名/.test(pageText);
  const hasMerchantUi = /导出查询商品|批量操作|商品管理|查询/.test(pageText);
  return hasLoginSignal && !hasMerchantUi;
}

export function isJdWareProductTargetPage(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:"
      && parsed.hostname === "wares-jdm.jd.com"
      && parsed.pathname === "/ware/wareList";
  } catch {
    return false;
  }
}

export async function reopenJdWareTargetAfterAutomatedLogin(input: {
  authentication: "existing_session" | "windows_dpapi_credentials";
  currentUrl: string;
  gotoTarget: () => Promise<void>;
  verifyPostNavigation: () => Promise<void>;
}) {
  if (input.authentication !== "windows_dpapi_credentials" || isJdWareProductTargetPage(input.currentUrl)) return false;
  // JD sometimes completes password login on the merchant home page instead
  // of restoring the originally requested WareList URL. The first navigation
  // never reached the product page or dispatched its query in that case, so a
  // single post-login navigation is required while the original response
  // listener remains armed. Never submit credentials again on this path.
  await input.gotoTarget();
  await input.verifyPostNavigation();
  return true;
}

export function shouldCloseJdWareBrowserConnection(interactiveLogin: boolean) {
  return !interactiveLogin;
}

export type CliOptions = {
  storeKey: string;
  shopName: string;
  shopId: string;
  loginMode?: "manual" | "windows_dpapi_credentials";
  executablePath: string;
  userDataDirectory: string;
  profileName: string;
  port: number;
  downloadDirectory: string;
  reuseLatest: boolean;
  taskTimeoutMs: number;
  debug: boolean;
  interactiveLogin: boolean;
  visibleRecovery: boolean;
  autoImport: boolean;
  baseUrl: string;
};

export type ScriptResult = {
  status: "completed" | "download_triggered_unverified";
  targetUrl: string;
  reusedLatest: boolean;
  task: JdWareExportTask;
  downloadSavedPath?: string;
  importResult?: { status: "imported" | "duplicate"; message: string; batchId: string; rowCount: number; source: "jd_product_master"; dataset: "product_master"; platform: "京东"; shopName: string; batchStatus: "completed"; warningCount: 0 };
  notes: string[];
  elapsedMs: number;
};

export type WareExportAudit = {
  status: "running" | "completed" | "failed";
  stage: string;
  startedAt: string;
  updatedAt: string;
  targetUrl: string;
  baseUrl: string;
  intent: "create" | "reuse_latest";
  storeKey?: string;
  shopName?: string;
  shopId?: string;
  querySource?: "initial_navigation" | "trusted_click";
  queryHttpStatus?: number;
  queryBusinessCode?: number;
  queryTotal?: number;
  queryObservedAt?: string;
  baselineTaskIds?: string[];
  taskId?: string;
  taskStatus?: JdWareExportTask["status"];
  recoveryArchivePath?: string;
  recoveryCreatedAt?: string;
  savedPath?: string;
  result?: ScriptResult;
  error?: string;
};
type StoreWareExportRecovery = JdWareExportRecovery;

export function createWareExportAudit(
  options: Pick<CliOptions, "baseUrl" | "reuseLatest"> & Partial<Pick<CliOptions, "storeKey" | "shopName" | "shopId">>,
): WareExportAudit {
  const now = new Date().toISOString();
  return {
    status: "running",
    stage: "starting",
    startedAt: now,
    updatedAt: now,
    targetUrl,
    baseUrl: options.baseUrl,
    intent: options.reuseLatest ? "reuse_latest" : "create",
    ...(options.storeKey ? { storeKey: options.storeKey } : {}),
    ...(options.shopName ? { shopName: options.shopName } : {}),
    ...(options.shopId ? { shopId: options.shopId } : {}),
  };
}

export function advanceWareExportAudit(audit: WareExportAudit, patch: Partial<WareExportAudit>): WareExportAudit {
  return { ...audit, ...patch, updatedAt: new Date().toISOString() };
}

export function isJdWareCreateExportRequest(url: string, method: string) {
  try {
    const parsed = new URL(url);
    return method.toUpperCase() === "POST"
      && parsed.protocol === "https:"
      && parsed.hostname === "sff.jd.com"
      && parsed.pathname === "/api"
      && parsed.searchParams.get("api") === jdWareCreateExportApi;
  } catch {
    return false;
  }
}

export function isJdWareProductQueryRequest(url: string, method: string) {
  try {
    const parsed = new URL(url);
    return method.toUpperCase() === "POST"
      && parsed.protocol === "https:"
      && parsed.hostname === "sff.jd.com"
      && parsed.pathname === "/api"
      && parsed.searchParams.get("api") === jdWareProductQueryApi;
  } catch {
    return false;
  }
}

export function validateJdWareProductQueryResponse(input: { status: number; payload: unknown }) {
  const payload = input.payload && typeof input.payload === "object"
    ? input.payload as { code?: unknown; msg?: unknown; data?: unknown }
    : null;
  const data = payload?.data && typeof payload.data === "object"
    ? payload.data as { total?: unknown; totalCount?: unknown }
    : null;
  const code = typeof payload?.code === "number" ? payload.code : null;
  const legacyTotal = typeof data?.total === "number" ? data.total : null;
  const currentTotal = typeof data?.totalCount === "number" ? data.totalCount : null;
  if (legacyTotal !== null && currentTotal !== null && legacyTotal !== currentTotal) {
    throw new Error(`京东商品查询总行数字段冲突（total=${legacyTotal}，totalCount=${currentTotal}）。`);
  }
  const total = currentTotal ?? legacyTotal;
  const message = typeof payload?.msg === "string" ? payload.msg.trim().slice(0, 300) : "";
  if (input.status !== 200 || code !== 200 || !Number.isInteger(total) || total === null || total <= 0) {
    throw new Error(`京东商品查询未返回正数总行数（HTTP ${input.status}，业务码 ${code ?? "missing"}，总行数 ${total ?? "missing"}）${message ? `：${message}` : ""}`);
  }
  return { code: 200 as const, total, message };
}

export function parseJdWareProductTotalText(value: string) {
  const match = /^\s*共\s*(\d+)\s*条\s*$/.exec(value);
  return match ? Number(match[1]) : null;
}

export async function clickJdWareProductQueryControl(queryButton: Locator) {
  // The sticky JD merchant header can cover the button's pointer coordinates
  // after closing the export drawer. This is a reversible query action and is
  // still fenced by the unique locator plus the exact response/total checks.
  // Use Chromium input instead of an untrusted synthetic DOM event. JD may
  // still reject the request independently through its business risk controls.
  await queryButton.click({ force: true, timeout: 10_000 });
}

export async function captureJdWareInitialProductQuery<T>(
  queryBootstrapState: JdWareQueryBootstrapState,
  dependencies: {
    gotoBlank: () => Promise<void>;
    waitForQuery: () => Promise<T>;
    gotoTarget: () => Promise<void>;
    verifyAfterNavigation?: () => Promise<void>;
  },
) {
  if (queryBootstrapState.queryTriggered) throw new Error("本轮京东商品查询已触发，拒绝重复导航或查询。");
  await dependencies.gotoBlank();
  const responsePromise = handleJdWareDownloadPromise(dependencies.waitForQuery());
  // Fence before navigation: the target page performs its own initial query,
  // and a failed/ambiguous navigation must never be followed by another click.
  queryBootstrapState.queryTriggered = true;
  await dependencies.gotoTarget();
  // A login/security redirect can complete without ever dispatching the
  // product query. Classify that state immediately instead of hiding the real
  // interactive gate behind the query-listener timeout.
  await dependencies.verifyAfterNavigation?.();
  return responsePromise;
}

export async function waitForJdWareProductQueryBootstrap(
  probe: () => Promise<Parameters<typeof jdWareProductQueryBootstrapDecision>[0]>,
  pause: () => Promise<void>,
  maxAttempts = 50,
) {
  if (!Number.isInteger(maxAttempts) || maxAttempts <= 0) throw new Error("京东商品查询等待次数无效。");
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const decision = jdWareProductQueryBootstrapDecision(await probe());
    if (decision === "query") return;
    if (attempt + 1 < maxAttempts) await pause();
  }
  throw new Error("京东商品列表查询入口在有界等待后仍未达到唯一可用状态。");
}

export class JdWareCreateExportRejectedError extends Error {
  readonly definitiveNoTask: boolean;

  constructor(message: string, definitiveNoTask: boolean) {
    super(message);
    this.name = "JdWareCreateExportRejectedError";
    this.definitiveNoTask = definitiveNoTask;
  }
}

export function validateJdWareCreateExportResponse(input: { status: number; payload: unknown }) {
  const payload = input.payload && typeof input.payload === "object"
    ? input.payload as { code?: unknown; msg?: unknown }
    : null;
  const code = typeof payload?.code === "number" ? payload.code : null;
  const message = typeof payload?.msg === "string" ? payload.msg.trim().slice(0, 300) : "";
  if (input.status !== 200 || code !== 200) {
    const definitiveNoTask = input.status === 200
      && code === 201
      && message.includes("创建导出任务失败");
    throw new JdWareCreateExportRejectedError(
      `京东 SKU 导出任务创建被拒绝（HTTP ${input.status}，业务码 ${code ?? "missing"}）${message ? `：${message}` : ""}`,
      definitiveNoTask,
    );
  }
  return { code: 200 as const, message };
}

async function clickAndConfirmJdWareExportSubmission(
  page: Page,
  confirm: Locator,
  onClickInvoked: () => Promise<void>,
) {
  // JD's export component catches every application error and renders no
  // durable failure state. Observe the exact request before the only click,
  // and do not refresh the record table while that request is still in flight.
  const responsePromise = handleJdWareDownloadPromise(page.waitForResponse(
    (response) => isJdWareCreateExportRequest(response.url(), response.request().method()),
    { timeout: 15_000 },
  ));
  await confirm.click();
  await onClickInvoked();
  const response = await responsePromise;
  const responseText = (await response.text()).slice(0, 8_192);
  let payload: unknown;
  try {
    payload = JSON.parse(responseText);
  } catch {
    payload = null;
  }
  return validateJdWareCreateExportResponse({ status: response.status(), payload });
}

async function parseCliOptions(): Promise<CliOptions> {
  const args = process.argv.slice(2);
  let reuseLatest = false;
  let debug = false;
  let interactiveLogin = false;
  let visibleRecovery = true;
  let autoImport = true;
  let baseUrl = (process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000").replace(/\/$/, "");
  // 京东导出任务通常需要数分钟，默认给足等待时间，避免任务已完成但脚本提前超时。
  let taskTimeoutMs = 300_000;
  let storeKey = "jd-yiyong-director";

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--reuse-latest") {
      reuseLatest = true;
      continue;
    }
    if (argument === "--debug") {
      debug = true;
      continue;
    }
    if (argument === "--interactive-login") {
      interactiveLogin = true;
      continue;
    }
    if (argument === "--no-visible-recovery") {
      visibleRecovery = false;
      continue;
    }
    if (argument === "--store-key") {
      const value = args[index + 1];
      if (!value || value.startsWith("--")) throw new Error("--store-key 必须提供店铺注册键");
      storeKey = value;
      index += 1;
      continue;
    }
    if (argument === "--no-auto-import") {
      autoImport = false;
      continue;
    }
    if (argument === "--base-url") {
      const value = args[index + 1];
      if (!value || value.startsWith("--")) throw new Error("--base-url 必须提供运营管理系统地址");
      baseUrl = value.replace(/\/$/, "");
      index += 1;
      continue;
    }
    if (argument === "--task-timeout-seconds") {
      const value = Number(args[index + 1]);
      if (!Number.isFinite(value) || value <= 0) throw new Error("--task-timeout-seconds 必须是正数。");
      taskTimeoutMs = Math.round(value * 1_000);
      index += 1;
      continue;
    }
    throw new Error(`不支持的参数：${argument}`);
  }

  if (interactiveLogin && !visibleRecovery) throw new Error("--interactive-login 不能与 --no-visible-recovery 同时使用。");
  const store = await getJdStore(storeKey);
  return {
    storeKey: store.storeKey,
    shopName: store.shopName,
    shopId: store.shopId,
    loginMode: store.loginMode,
    executablePath: store.browser.executablePath,
    userDataDirectory: store.browser.userDataDir,
    profileName: store.browser.profileName,
    port: store.browser.debugPort,
    downloadDirectory: store.browser.downloadDir,
    reuseLatest,
    taskTimeoutMs,
    debug,
    interactiveLogin,
    visibleRecovery,
    autoImport,
    baseUrl,
  };
}

async function ensureDir(directory: string) {
  await mkdir(directory, { recursive: true });
}

async function exactlyOne(locator: Locator, description: string) {
  const count = await locator.count();
  if (count !== 1) throw new Error(`${description} 应匹配 1 个元素，实际匹配 ${count} 个。`);
  return locator;
}

async function waitForExportEntry(page: Page, timeoutMs = 90_000) {
  // JD keeps another same-named button inside a hidden batch-tools menu.
  // Bind only the visible entry so locator waiting cannot attach to that copy.
  const entry = page.getByRole("button", { name: "导出查询商品", exact: true }).filter({ visible: true });
  const deadline = Date.now() + timeoutMs;
  const recentVisibleCounts: number[] = [];
  while (Date.now() < deadline) {
    recentVisibleCounts.push(await entry.count());
    if (recentVisibleCounts.length > 2) recentVisibleCounts.shift();
    if (hasStableUniqueVisibleJdExportEntry(recentVisibleCounts)) {
      // Re-read immediately before returning because JD can remove the
      // short-lived menu between the two stable samples and this hand-off.
      // A vanished entry is transient and must stay inside this bounded wait;
      // an extra visible entry remains an identity ambiguity and fails closed.
      const verificationCount = await entry.count();
      if (verificationCount === 1) return entry;
      if (verificationCount > 1) throw new Error("导出查询商品入口不唯一。");
      recentVisibleCounts.push(verificationCount);
      if (recentVisibleCounts.length > 2) recentVisibleCounts.shift();
    }
    await page.waitForTimeout(250);
  }
  throw new Error("导出查询商品按钮未达到连续可见稳定状态。");
}

export function hasStableUniqueVisibleJdExportEntry(samples: readonly number[]) {
  return samples.length >= 2 && samples.at(-1) === 1 && samples.at(-2) === 1;
}

export type JdWareExportEntryBootstrapDecision = "ready" | "open_batch_operations" | "wait";
export const jdWareBatchOperationsLabelPattern = /^\s*批量操作\s*$/;
export const jdWareBatchOperationsTriggerSelector = "span.batch-dropdown-link";

export function jdWareExportEntryBootstrapDecision(input: { exportEntryCount: number; batchOperationsCount: number }): JdWareExportEntryBootstrapDecision {
  if (!Number.isInteger(input.exportEntryCount) || input.exportEntryCount < 0 || !Number.isInteger(input.batchOperationsCount) || input.batchOperationsCount < 0) {
    throw new Error("京东商品导出入口计数无效。");
  }
  if (input.exportEntryCount > 1) throw new Error("导出查询商品入口不唯一。");
  if (input.exportEntryCount === 1) return "ready";
  if (input.batchOperationsCount > 1) throw new Error("批量操作入口不唯一。");
  return input.batchOperationsCount === 1 ? "open_batch_operations" : "wait";
}

export type JdWareProductQueryBootstrapDecision = "query" | "wait";
export type JdWareQueryBootstrapState = { queryTriggered: boolean };

export function createJdWareQueryBootstrapState(): JdWareQueryBootstrapState {
  return { queryTriggered: false };
}

export function jdWareProductQueryBootstrapDecision(input: { productSearchContainerCount: number; scopedQueryButtonCount: number; pageQueryButtonCount: number }): JdWareProductQueryBootstrapDecision {
  if (!Number.isInteger(input.productSearchContainerCount) || input.productSearchContainerCount < 0 || !Number.isInteger(input.scopedQueryButtonCount) || input.scopedQueryButtonCount < 0 || !Number.isInteger(input.pageQueryButtonCount) || input.pageQueryButtonCount < 0) {
    throw new Error("京东商品查询入口计数无效。");
  }
  if (input.productSearchContainerCount > 1) throw new Error("商品列表筛选容器不唯一。");
  if (input.productSearchContainerCount === 0) {
    // On slower stores JD can paint the unique button before its enclosing
    // filter form receives the identifying field text/classes. Never click the
    // unbound button; allow the outer bounded wait to observe the complete DOM.
    if (input.pageQueryButtonCount > 1) throw new Error("商品列表筛选容器尚未出现时查询按钮不唯一。");
    return "wait";
  }
  if (input.pageQueryButtonCount !== input.scopedQueryButtonCount) throw new Error("查询按钮与商品列表筛选容器不唯一对应。");
  if (input.scopedQueryButtonCount > 1) throw new Error("商品列表筛选容器中的查询按钮不唯一。");
  return input.scopedQueryButtonCount === 1 ? "query" : "wait";
}

export type JdWareSkuExportDrawerDecision = "already_open" | "bootstrap";
const jdWareDrawerCandidateSelector = ":is(.jd-overlay, [role='dialog'], .ant-drawer, .ant-modal, .el-dialog, .el-drawer, [class*='jdm-drawer'], [class*='jdm-dialog'])";
// JD nests Ant/JDM drawer wrappers. Count only the innermost matching root so
// one logical drawer cannot be mistaken for multiple overlays.
export const jdWareNormalizedExportDrawerSelector = `${jdWareDrawerCandidateSelector}:has-text('导出条件'):not(:has(${jdWareDrawerCandidateSelector}:has-text('导出条件')))`;

export function jdWareSkuExportDrawerDecision(input: { exportDrawerCount: number; scopedSkuTabCount: number; pageSkuTabCount: number }): JdWareSkuExportDrawerDecision {
  if (!Number.isInteger(input.exportDrawerCount) || input.exportDrawerCount < 0 || !Number.isInteger(input.scopedSkuTabCount) || input.scopedSkuTabCount < 0 || !Number.isInteger(input.pageSkuTabCount) || input.pageSkuTabCount < 0) {
    throw new Error("京东 SKU 导出抽屉计数无效。");
  }
  if (input.exportDrawerCount > 1) throw new Error("导出条件抽屉不唯一。");
  if (input.exportDrawerCount === 0) {
    if (input.pageSkuTabCount > 0) throw new Error("SKU导出页签不在唯一导出条件抽屉中。");
    return "bootstrap";
  }
  if (input.scopedSkuTabCount !== 1 || input.pageSkuTabCount !== 1 || input.pageSkuTabCount !== input.scopedSkuTabCount) {
    throw new Error("导出条件抽屉中的 SKU导出页签不唯一或身份不匹配。");
  }
  return "already_open";
}

type VerifiedJdWareSkuExportDrawer = { exportDrawer: Locator; skuTab: Locator };

async function getVerifiedJdWareSkuExportDrawer(page: Page): Promise<VerifiedJdWareSkuExportDrawer | null> {
  const exportDrawer = page.locator(jdWareNormalizedExportDrawerSelector).filter({ visible: true });
  const scopedSkuTab = exportDrawer.getByRole("tab", { name: "SKU导出", exact: true }).filter({ visible: true });
  const pageSkuTab = page.getByRole("tab", { name: "SKU导出", exact: true }).filter({ visible: true });
  const decision = jdWareSkuExportDrawerDecision({
    exportDrawerCount: await exportDrawer.count(),
    scopedSkuTabCount: await scopedSkuTab.count(),
    pageSkuTabCount: await pageSkuTab.count(),
  });
  return decision === "already_open" ? { exportDrawer, skuTab: scopedSkuTab } : null;
}

async function closeExistingJdWareSkuExportDrawer(page: Page) {
  const verified = await getVerifiedJdWareSkuExportDrawer(page);
  if (!verified) return false;
  const close = verified.exportDrawer.getByRole("button", { name: "关闭此对话框", exact: true }).filter({ visible: true });
  await exactlyOne(close, "京东 SKU 导出抽屉关闭按钮");
  await close.click({ timeout: 10_000 });
  await verified.exportDrawer.waitFor({ state: "hidden", timeout: 10_000 });
  return true;
}

async function verifyJdWareProductQueryResponse(page: Page, response: Response) {
  const payload = await response.json().catch(() => null) as unknown;
  const verified = validateJdWareProductQueryResponse({ status: response.status(), payload });
  const total = page.locator(".select-count").filter({ visible: true });
  await exactlyOne(total, "京东商品查询总行数");
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (parseJdWareProductTotalText(await total.innerText()) === verified.total) return verified;
    await page.waitForTimeout(200);
  }
  throw new Error(`京东商品查询接口返回 ${verified.total} 行，但页面总行数未精确回显。`);
}

export async function prepareJdWareExportEntry(page: Page) {
  if (await getVerifiedJdWareSkuExportDrawer(page)) return "already_open" as const;
  // Do not reveal the dropdown during query verification. Some JD account
  // variants auto-close this short-lived menu before the later dialog stage.
  // Keep reveal + click as one atomic action in openSkuExportDialog instead.
  return "ready_to_open" as const;
}

export async function revealJdWareExportEntry(page: Page, queryBootstrapState: JdWareQueryBootstrapState = createJdWareQueryBootstrapState()) {
  const exportEntry = page.getByRole("button", { name: "导出查询商品", exact: true }).filter({ visible: true });
  // The inner button is the stable identity, while JD now binds the dropdown
  // expansion to its wrapping span. Require one wrapper containing that exact
  // button so “更多批量工具” and duplicate role nodes cannot be clicked.
  const batchOperationsButton = page.locator("button").filter({ hasText: jdWareBatchOperationsLabelPattern });
  const batchOperations = page.locator(jdWareBatchOperationsTriggerSelector)
    .filter({ hasText: jdWareBatchOperationsLabelPattern, has: batchOperationsButton, visible: true });
  // A page-level 查询 button can belong to a popover or an unrelated panel. Bind it
  // to the one visible WareList filter container identified by both product fields.
  const productSearchContainer = page.locator("form:has-text('商品名称'):has-text('商品编码'), [role='search']:has-text('商品名称'):has-text('商品编码'), .ant-form:has-text('商品名称'):has-text('商品编码')").filter({ visible: true });
  const queryButton = productSearchContainer.getByRole("button", { name: "查询", exact: true }).filter({ visible: true });
  const pageQueryButton = page.getByRole("button", { name: "查询", exact: true }).filter({ visible: true });
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    const decision = jdWareExportEntryBootstrapDecision({
      exportEntryCount: await exportEntry.count(),
      batchOperationsCount: await batchOperations.count(),
    });
    if (decision === "ready") {
      try {
        return await waitForExportEntry(page, 3_000);
      } catch (error) {
        if (!(error instanceof Error) || error.message !== "导出查询商品按钮未达到连续可见稳定状态。") throw error;
        // Some stores briefly paint a visible export-entry clone before the
        // hydrated batch trigger exists. Let the bounded bootstrap loop observe
        // the settled controls instead of waiting 90 seconds on that clone.
        continue;
      }
    }
    if (decision === "open_batch_operations") {
      await exactlyOne(batchOperations, "批量操作按钮");
      await batchOperations.click({ timeout: 10_000 });
      try {
        return await waitForExportEntry(page, 3_000);
      } catch (error) {
        if (!(error instanceof Error) || error.message !== "导出查询商品按钮未达到连续可见稳定状态。") throw error;
        await exactlyOne(batchOperations, "批量操作按钮");
        if (await batchOperations.getAttribute("aria-expanded") === "true") {
          // JD can mark the dropdown expanded before its lazy menu content is
          // painted. Wait for that in-flight expansion without replaying input.
          return waitForExportEntry(page, 12_000);
        }
        // Some JD account variants ignore pointer clicks on this dropdown but
        // implement its keyboard contract. This remains the same reversible
        // menu action and is allowed only after the click produced no entry.
        await batchOperations.press("Enter", { timeout: 10_000 });
        return waitForExportEntry(page);
      }
    }
    const queryDecision = jdWareProductQueryBootstrapDecision({
      productSearchContainerCount: await productSearchContainer.count(),
      scopedQueryButtonCount: await queryButton.count(),
      pageQueryButtonCount: await pageQueryButton.count(),
    });
    if (queryDecision === "query" && !queryBootstrapState.queryTriggered) {
      await exactlyOne(queryButton, "查询按钮");
      // A thrown click can still mean that JD received the browser event before
      // repainting. Fence the attempt first so later workflow stages never replay it.
      queryBootstrapState.queryTriggered = true;
      await queryButton.click({ timeout: 10_000 });
    }
    await page.waitForTimeout(250);
  }
  throw new Error("无法找到唯一的导出查询商品入口或批量操作入口。");
}

export type JdWareTaskSnapshot = { tasks: JdWareExportTask[]; emptyConfirmed: boolean };

export function hasStableJdWareTaskSnapshot(samples: readonly JdWareTaskSnapshot[]) {
  if (samples.length < 2) return false;
  const previous = samples.at(-2)!;
  const current = samples.at(-1)!;
  if (previous.tasks.length === 0 || current.tasks.length === 0) return previous.tasks.length === 0 && current.tasks.length === 0 && previous.emptyConfirmed && current.emptyConfirmed;
  const signature = (items: readonly JdWareExportTask[]) => items.map((item) => `${item.taskId}:${item.createdAt}:${item.status}:${item.resultText ?? ""}:${item.successRows ?? ""}:${item.rowText}`).sort().join("|");
  return signature(previous.tasks) === signature(current.tasks);
}

export function shouldDismissJdMenuUpdateNotice(visibleLayers: readonly { text: string; buttons: readonly string[] }[]) {
  const candidates = visibleLayers.filter((layer) => /京麦菜单更新调整|一级菜单更新调整/.test(layer.text) && /生效/.test(layer.text));
  if (candidates.length !== 1) return false;
  const layer = candidates[0]!;
  return layer.buttons.length === 1 && layer.buttons[0]?.trim() === "知道了";
}

export function isConfirmedJdWareTaskListEmptyState(input: { uniqueRefresh: boolean; boundToExportContainer: boolean; containerText: string }) {
  return input.uniqueRefresh && input.boundToExportContainer && /暂无数据|暂无记录/.test(input.containerText);
}

export type JdWareDownloadTarget = { kind: "target"; url: string } | { kind: "reject"; reason: string };

/** Accept only the signed WareList export URL captured from this exact task-row click. */
export function selectJdWareTaskDownloadTarget(input: { taskId: string; sourceUrl: string; openedUrls: readonly string[] }): JdWareDownloadTarget {
  if (input.openedUrls.length !== 1) return { kind: "reject", reason: input.openedUrls.length === 0 ? "no_window_open_target" : "multiple_window_open_targets" };
  const targetUrl = input.openedUrls[0]!;
  try {
    const target = new URL(targetUrl);
    const source = new URL(input.sourceUrl);
    const decodedPath = decodeURIComponent(target.pathname);
    if (target.protocol !== "https:" || target.hostname !== "storage.360buyimg.com" || target.href === source.href
      || !decodedPath.startsWith("/ware-common/cpop-export-sku/") || !decodedPath.endsWith(".xlsx") || !decodedPath.includes(`_${input.taskId}_`)
      || !target.searchParams.get("Expires") || !target.searchParams.get("AccessKey") || !target.searchParams.get("Signature")) {
      return { kind: "reject", reason: "unexpected_window_open_target" };
    }
    return { kind: "target", url: target.href };
  } catch {
    return { kind: "reject", reason: "invalid_window_open_target" };
  }
}

/** The local file is accepted only after it is a nonempty XLSX with the product-master identity column. */
export function validateJdWareMasterWorkbook(bytes: Uint8Array, expectedRowCount?: number) {
  if (bytes.byteLength === 0) throw new Error("京东 SKU 下载文件为空。");
  if (bytes.byteLength > maximumJdWareWorkbookBytes) throw new Error("京东 SKU 下载文件超过 25MiB 上限。");
  const sheet = parseXlsxFirstSheet(bytes);
  const headerIndex = sheet.rows.findIndex((row) => {
    const headers = row.cells.map((cell) => String(cell ?? "").trim()).filter(Boolean);
    return headers.includes("商品编码") && headers.some((header) => header.replace(/\s+/g, "").toUpperCase() === "SKUID");
  });
  if (headerIndex < 0) throw new Error("京东 SKU 下载文件缺少商品编码或 SKUID 表头，拒绝导入。");
  const dataRows = sheet.rows.slice(headerIndex + 1).filter((row) => row.cells.some((cell) => String(cell ?? "").trim()));
  if (dataRows.length === 0) throw new Error("京东 SKU 下载文件没有商品数据行，拒绝导入。");
  if (expectedRowCount !== undefined && dataRows.length !== expectedRowCount) throw new Error(`京东 SKU 下载业务行数 ${dataRows.length} 与任务成功行数 ${expectedRowCount} 不一致。`);
  return { headerRowNumber: sheet.rows[headerIndex]!.rowNumber, rowCount: dataRows.length, columnCount: sheet.maxColumns };
}

/** Only retry this reversible drawer-opening action after JD replaces its button mid-click. */
export function isTransientJdExportEntryRepaint(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /element is not stable|detached from the DOM/i.test(message);
}

export async function openExportEntryWithRepaintRetry(page: Page, queryBootstrapState: JdWareQueryBootstrapState = createJdWareQueryBootstrapState()) {
  // Querying is a one-time, reversible bootstrap action. Keep it outside the
  // click retry loop: a detached export button must never replay that query.
  let exportEntry = await revealJdWareExportEntry(page, queryBootstrapState);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    // A JD repaint can close the short-lived batch menu together with the
    // detached entry. Re-run the bounded reveal step so the same reversible
    // menu may be opened once more; queryBootstrapState still fences the
    // already-observed product query from being replayed.
    if (attempt > 0) exportEntry = await revealJdWareExportEntry(page, queryBootstrapState);
    try {
      await exportEntry.click({ timeout: 10_000 });
      return;
    } catch (error) {
      if (attempt === 0 && isTransientJdExportEntryRepaint(error)) {
        await page.waitForTimeout(250);
        continue;
      }
      throw error;
    }
  }
  throw new Error("京东导出入口在重绘后仍无法稳定打开。");
}

async function openTargetPage(
  page: Page,
  queryBootstrapState: JdWareQueryBootstrapState,
  store: Pick<CliOptions, "storeKey" | "shopName" | "loginMode">,
) {
  let authenticationPromise: Promise<void> | null = null;
  const authenticateAndRestoreTarget = () => {
    authenticationPromise ??= (async () => {
      const authenticated = await ensureJdStoreAuthenticatedSession(page, store);
      await reopenJdWareTargetAfterAutomatedLogin({
        authentication: authenticated.authentication,
        currentUrl: page.url(),
        gotoTarget: async () => { await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: jdWareTargetNavigationTimeoutMs }); },
        verifyPostNavigation: async () => {
          const postLoginText = await page.locator("body").innerText({ timeout: 2_000 }).catch(() => "");
          if (hasJdInteractivePageGate(postLoginText)) {
            throw new Error("京东商家后台自动登录后需要人工完成验证码或安全验证。");
          }
          const postLoginHasPassword = await page.locator('input[type="password"]').count().then((count) => count > 0).catch(() => false);
          if (isLikelyJdLoginPage(page.url(), postLoginText, postLoginHasPassword)) {
            throw new Error(`waiting_login：${store.shopName} 自动登录后仍停留在登录页面，需要人工检查`);
          }
        },
      });
    })();
    return authenticationPromise;
  };
  const response = await captureJdWareInitialProductQuery(queryBootstrapState, {
    gotoBlank: async () => { await page.goto("about:blank", { waitUntil: "domcontentloaded", timeout: 10_000 }); },
    waitForQuery: () => {
      const query = page.waitForResponse(
        (candidate) => isJdWareProductQueryRequest(candidate.url(), candidate.request().method()),
        // The listener is installed before navigation and must outlive the full
        // navigation budget. On a cold JD profile the page can finish its DOM
        // navigation before the application dispatches the initial query.
        { timeout: jdWareInitialProductQueryTimeoutMs },
      );
      // DPAPI login is handled by verifyAfterNavigation below. Keep the query
      // observer alive across the single login submission so the redirected
      // merchant page's first query remains the only accepted freshness proof.
      if (store.loginMode === "windows_dpapi_credentials") {
        return waitForJdWareQueryOrAutomatedLoginRedirect(
          query,
          waitForJdWareLoginNavigation(
            () => page.waitForURL(
              (url) => /passport|login/i.test(url.hostname) || /passport|login/i.test(url.pathname),
              { timeout: jdWareInitialProductQueryTimeoutMs },
            ),
            () => page.url(),
          ),
          authenticateAndRestoreTarget,
        );
      }
      return waitForJdWareQueryOrInteractiveRedirect(query, waitForJdWareLoginRedirect(
        () => page.waitForURL(
          (url) => /passport|login/i.test(url.hostname) || /passport|login/i.test(url.pathname),
          { timeout: jdWareInitialProductQueryTimeoutMs },
        ),
        () => page.url(),
      ));
    },
    gotoTarget: async () => { await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: jdWareTargetNavigationTimeoutMs }); },
    verifyAfterNavigation: async () => {
      const pageText = await page.locator("body").innerText({ timeout: 2_000 }).catch(() => "");
      if (hasJdInteractivePageGate(pageText)) {
        throw new Error("京东商家后台需要人工完成验证码或安全验证。");
      }
      const hasPasswordInput = await page.locator('input[type="password"]').count().then((count) => count > 0).catch(() => false);
      if (isLikelyJdLoginPage(page.url(), pageText, hasPasswordInput)) {
        await authenticateAndRestoreTarget();
      }
    },
  });
  // Login redirects render faster than the merchant export button. Check them
  // first so each unauthenticated store does not burn the 30-second UI wait.
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const pageText = await page.locator("body").innerText({ timeout: 1_000 }).catch(() => "");
    if (hasJdInteractivePageGate(pageText)) {
      throw new Error("京东商家后台需要人工完成验证码或安全验证。");
    }
    if (isLikelyJdLoginPage(page.url(), pageText, await page.locator('input[type="password"]').count().then((count) => count > 0))) {
      await ensureJdStoreAuthenticatedSession(page, store);
    }
    if (/导出查询商品|批量操作|商品管理/.test(pageText)) break;
    await page.waitForTimeout(150);
  }
  await dismissJdMenuUpdateNotice(page);
  // A drawer opened before the product query settled captures total=0 even
  // after the table later shows rows. Reopen it only after a fresh, positive
  // initial query response and the same total are visible in the toolbar.
  await closeExistingJdWareSkuExportDrawer(page);
  const verified = await verifyJdWareProductQueryResponse(page, response);
  await prepareJdWareExportEntry(page);
  return verified;
}

async function dismissJdMenuUpdateNotice(page: Page) {
  const notices = page.locator('[role="dialog"], .ant-modal, .ant-drawer, .el-dialog, .el-drawer, [class*="jdm-modal"], [class*="jdm-dialog"]').filter({ visible: true });
  const layers = await notices.evaluateAll((elements) => elements.map((element) => ({
    text: (element as HTMLElement).innerText ?? "",
    buttons: [...element.querySelectorAll("button")].map((button) => (button as HTMLElement).innerText.trim()),
  })).filter((layer) => /京麦菜单更新调整|一级菜单更新调整/.test(layer.text) && /生效/.test(layer.text)));
  if (!shouldDismissJdMenuUpdateNotice(layers)) return;
  const notice = notices.filter({ hasText: /京麦菜单更新调整|一级菜单更新调整/ });
  await exactlyOne(notice, "京麦菜单更新调整提示");
  const acknowledged = notice.getByRole("button", { name: "知道了", exact: true });
  await exactlyOne(acknowledged, "京麦菜单更新调整提示的知道了按钮");
  await acknowledged.click();
  await notice.waitFor({ state: "hidden", timeout: 10_000 });
}

async function readExportTasks(page: Page) {
  const rows = await page.locator("tr").evaluateAll((elements) => elements.map((element) => ((element as HTMLElement).innerText ?? "").trim()));
  return parseJdWareExportTaskRows(rows);
}

async function readLoadedExportTasks(page: Page, timeoutMs = 15_000) {
  const refresh = page.getByText("刷新列表", { exact: true });
  await refresh.waitFor({ state: "visible", timeout: timeoutMs });
  const uniqueRefresh = await refresh.count() === 1;

  const deadline = Date.now() + timeoutMs;
  const samples: JdWareTaskSnapshot[] = [];
  while (Date.now() < deadline) {
    const tasks = await readExportTasks(page);
    const emptyConfirmed = await refresh.evaluate((element) => {
      const container = element.closest('[role="dialog"], .ant-modal, .ant-drawer, .el-dialog, .el-drawer, [class*="jdm-modal"], [class*="jdm-dialog"]');
      return container ? { boundToExportContainer: true, containerText: (container as HTMLElement).innerText ?? "" } : { boundToExportContainer: false, containerText: "" };
    }).catch(() => ({ boundToExportContainer: false, containerText: "" }));
    samples.push({ tasks, emptyConfirmed: isConfirmedJdWareTaskListEmptyState({ uniqueRefresh, ...emptyConfirmed }) });
    if (samples.length > 2) samples.shift();
    if (hasStableJdWareTaskSnapshot(samples)) return tasks;
    await page.waitForTimeout(500);
  }
  throw new Error("导出任务列表未达到两次一致的非空快照或两次明确空态，已停止且不会创建任务。");
}

async function refreshExportRecords(page: Page) {
  const refresh = page.getByText("刷新列表", { exact: true });
  if (await refresh.count() === 1) await refresh.click();
}

async function openSkuExportDialog(page: Page, queryBootstrapState: JdWareQueryBootstrapState) {
  await dismissJdMenuUpdateNotice(page);
  let verifiedDrawer = await getVerifiedJdWareSkuExportDrawer(page);
  if (!verifiedDrawer) {
    await openExportEntryWithRepaintRetry(page, queryBootstrapState);
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      verifiedDrawer = await getVerifiedJdWareSkuExportDrawer(page);
      if (verifiedDrawer) break;
      await page.waitForTimeout(250);
    }
    if (!verifiedDrawer) throw new Error("京东 SKU 导出抽屉未达到唯一可验证状态。");
  }
  const skuTab = verifiedDrawer.skuTab;
  await exactlyOne(skuTab, "导出条件抽屉中的 SKU导出页签");
  const skuTabHandle = await skuTab.elementHandle();
  if (!skuTabHandle) throw new Error("导出条件抽屉中的 SKU导出页签已在操作前消失。");

  // JD normally opens this tab by default.  Clicking only when needed saves a
  // UI round trip while still making the intended export dimension explicit.
  if (await skuTabHandle.getAttribute("aria-selected") !== "true") {
    if (!await getVerifiedJdWareSkuExportDrawer(page) || !await skuTabHandle.evaluate((element) => element.isConnected && (element as HTMLElement).offsetParent !== null)) {
      throw new Error("导出条件抽屉中的 SKU导出页签在点击前已变化，已停止。");
    }
    await skuTabHandle.click();
    if (await skuTabHandle.getAttribute("aria-selected") !== "true") {
      throw new Error("SKU导出页签未实际选中，已停止且不会点击确定导出。");
    }
  }

  const confirm = page.getByRole("button", { name: "确定导出", exact: true });
  await confirm.waitFor({ state: "visible", timeout: 15_000 });
  const uniqueConfirm = await exactlyOne(confirm, "确定导出按钮");
  if (!await uniqueConfirm.isEnabled()) throw new Error("确定导出按钮当前不可用，已停止且不会创建任务。");
  return uniqueConfirm;
}

async function waitForTask(
  page: Page,
  previousTaskIds: ReadonlySet<string>,
  timeoutMs: number,
  onTaskObserved?: (task: JdWareExportTask) => Promise<void>,
) {
  const deadline = Date.now() + timeoutMs;
  let lastRefreshAt = 0;

  while (Date.now() < deadline) {
    const tasks = await readExportTasks(page);
    const unseenTasks = unseenJdWareExportTasks(tasks, previousTaskIds);
    if (unseenTasks.length > 1) {
      throw new Error(`检测到多个新导出任务（${unseenTasks.map((task) => task.taskId).join("、")}），无法安全关联本次导出，已停止自动下载。`);
    }
    const task = unseenTasks[0];
    if (task) await onTaskObserved?.(task);
    if (task?.status === "completed") return task;
    if (task?.status === "failed") throw new Error(`京东导出任务 ${task.taskId} 失败：${task.resultText ?? task.rowText}`);

    if (Date.now() - lastRefreshAt >= refreshIntervalMs) {
      await refreshExportRecords(page);
      lastRefreshAt = Date.now();
    }
    await page.waitForTimeout(pollIntervalMs);
  }

  throw new Error(`等待新的京东导出任务完成超时（${Math.round(timeoutMs / 1_000)} 秒）。`);
}

async function waitForKnownTask(
  page: Page,
  taskId: string,
  timeoutMs: number,
  onTaskObserved?: (task: JdWareExportTask) => Promise<void>,
) {
  const deadline = Date.now() + timeoutMs;
  let lastRefreshAt = 0;
  while (Date.now() < deadline) {
    const task = (await readExportTasks(page)).find((item) => item.taskId === taskId);
    if (!task) throw new Error(`京东导出任务 ${taskId} 已不在导出记录中，无法安全继续。`);
    await onTaskObserved?.(task);
    if (task.status === "completed") return task;
    if (task.status === "failed") throw new Error(`京东导出任务 ${task.taskId} 失败：${task.resultText ?? task.rowText}`);
    if (Date.now() - lastRefreshAt >= refreshIntervalMs) {
      await refreshExportRecords(page);
      lastRefreshAt = Date.now();
    }
    await page.waitForTimeout(pollIntervalMs);
  }
  throw new Error(`等待京东导出任务 ${taskId} 完成超时（${Math.round(timeoutMs / 1_000)} 秒）。`);
}

function safeDownloadedFilename(value: string, taskId: string) {
  const filename = value.replace(/[<>:"/\\|?*]/g, "_").trim();
  return /\.xlsx$/i.test(filename) ? filename : `jd-ware-${taskId}.xlsx`;
}

function insideDirectory(directory: string, candidatePath: string) {
  const relative = path.relative(path.resolve(directory), path.resolve(candidatePath));
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

export function validateJdWareDownloadProgress(activeGuid: string, event: { guid?: unknown; state?: unknown }) {
  if (event.guid !== activeGuid) return { kind: "reject" as const, reason: "unexpected_download_guid" };
  if (event.state === "completed") return { kind: "completed" as const };
  if (event.state === "canceled") return { kind: "reject" as const, reason: "download_canceled" };
  return { kind: "waiting" as const };
}

export function isJdWareDownloadPathInsideStaging(stagingDirectory: string, filePath: string) {
  return insideDirectory(stagingDirectory, filePath);
}

export function validateJdWareBrowserDownloadBegin(event: { url?: unknown; suggestedFilename?: unknown }, taskId: string) {
  const target = selectJdWareTaskDownloadTarget({
    taskId,
    sourceUrl: targetUrl,
    openedUrls: typeof event.url === "string" ? [event.url] : [],
  });
  if (target.kind === "reject") return target;
  const filename = typeof event.suggestedFilename === "string" ? event.suggestedFilename : "";
  return /\.xlsx$/i.test(filename) ? target : { kind: "reject" as const, reason: "non_xlsx_suggested_filename" };
}

export async function createJdWareBrowserDownloadSession(page: Page) {
  const browser = page.context().browser();
  if (!browser) throw new Error("无法取得 Chrome 浏览器根会话，已停止京东 SKU 下载。");
  return browser.newBrowserCDPSession();
}

/** Attach a rejection observer immediately; callers still await the original promise for the failure result. */
export function handleJdWareDownloadPromise<T>(promise: Promise<T>) {
  void promise.catch(() => undefined);
  return promise;
}

export function waitForJdWareQueryOrInteractiveRedirect<T>(queryPromise: Promise<T>, interactiveRedirectPromise: Promise<never>) {
  // Both observers are installed before navigation. The loser is rejection-
  // observed because Playwright will settle it later when its timeout expires
  // or the owned page closes.
  return handleJdWareDownloadPromise(Promise.race([
    handleJdWareDownloadPromise(queryPromise),
    handleJdWareDownloadPromise(interactiveRedirectPromise),
  ]));
}

export function waitForJdWareQueryOrAutomatedLoginRedirect<T>(
  queryPromise: Promise<T>,
  loginNavigationPromise: Promise<unknown>,
  authenticateAndRestoreTarget: () => Promise<void>,
) {
  let querySettled = false;
  const observedQuery = handleJdWareDownloadPromise(queryPromise.then(
    (value) => { querySettled = true; return value; },
    (error) => { querySettled = true; throw error; },
  ));
  const loginBranch = handleJdWareDownloadPromise(loginNavigationPromise.then(async () => {
    // The losing URL observer can settle after a valid query during later page
    // lifecycle changes. Never start a credential submission once the fresh
    // product response has already won the race.
    if (querySettled) return observedQuery;
    await authenticateAndRestoreTarget();
    return observedQuery;
  }));
  return handleJdWareDownloadPromise(Promise.race([observedQuery, loginBranch]));
}

export async function waitForJdWareLoginNavigation(
  waitForRedirect: () => Promise<unknown>,
  currentUrl: () => string,
  maxAttempts = 3,
): Promise<"login"> {
  if (!Number.isInteger(maxAttempts) || maxAttempts <= 0) throw new Error("京东登录跳转监听次数无效。");
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      await waitForRedirect();
      return "login";
    } catch {
      if (isLikelyJdLoginPage(currentUrl(), "")) return "login";
      // JD can replace the merchant frame while staying on WareList. Re-arm
      // the auxiliary login observer so a later real passport redirect is
      // still classified, while the authoritative query listener remains the
      // race winner when the product response arrives first.
      if (attempt + 1 < maxAttempts) continue;
    }
  }
  // The product-query promise owns the overall 60-second bound. Keeping this
  // exhausted auxiliary observer pending prevents a transient frame error
  // from preempting its final response or timeout.
  return await new Promise<never>(() => undefined);
}

export async function waitForJdWareLoginRedirect(
  waitForRedirect: () => Promise<unknown>,
  currentUrl: () => string,
  maxAttempts = 3,
): Promise<never> {
  await waitForJdWareLoginNavigation(waitForRedirect, currentUrl, maxAttempts);
  throw new Error("京东商家后台尚未登录。请在专用浏览器中完成登录后重新运行。");
}

export async function withJdWareDownloadStaging<T>(downloadDirectory: string, operation: (stagingDirectory: string) => Promise<T>) {
  await ensureDir(downloadDirectory);
  const stagingDirectory = await mkdtemp(path.join(downloadDirectory, ".jd-ware-export-"));
  try {
    if (!insideDirectory(downloadDirectory, stagingDirectory)) throw new Error("京东 SKU 下载暂存目录越过当前店铺下载目录。");
    return await operation(stagingDirectory);
  } finally {
    await rm(stagingDirectory, { recursive: true, force: true }).catch(() => undefined);
  }
}

async function withDeadline<T>(promise: Promise<T>, timeoutMs: number, message: string) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function saveTaskDownload(page: Page, task: JdWareExportTask, downloadDirectory: string) {
  const taskRow = page.locator("tr").filter({ hasText: task.taskId });
  await exactlyOne(taskRow, `导出任务 ${task.taskId} 的记录行`);
  const downloadButton = taskRow.getByRole("button", { name: "下载", exact: true });
  await exactlyOne(downloadButton, `导出任务 ${task.taskId} 的下载按钮`);
  if (!Number.isInteger(task.successRows) || task.successRows === null || task.successRows <= 0) {
    return { savedPath: undefined, verified: false, error: "京东已完成任务缺少正数成功行数，拒绝下载。" };
  }
  return withJdWareDownloadStaging(downloadDirectory, async (stagingDirectory) => {
    let session: Awaited<ReturnType<typeof createJdWareBrowserDownloadSession>> | null = null;
    let activeGuid: string | undefined;
    let suggestedFilename = "";
    let resolveStarted!: () => void;
    let rejectStarted!: (error: Error) => void;
    let resolveCompleted!: (event: { filePath?: string }) => void;
    let rejectCompleted!: (error: Error) => void;
    const started = handleJdWareDownloadPromise(new Promise<void>((resolve, reject) => { resolveStarted = resolve; rejectStarted = reject; }));
    const completed = handleJdWareDownloadPromise(new Promise<{ filePath?: string }>((resolve, reject) => { resolveCompleted = resolve; rejectCompleted = reject; }));
    try {
      session = await createJdWareBrowserDownloadSession(page);
      session.on("Browser.downloadWillBegin", (event) => {
    if (activeGuid) {
      const error = new Error("京东 SKU 单次下载出现多个开始事件。");
      rejectCompleted(error);
      return;
    }
    const valid = validateJdWareBrowserDownloadBegin(event, task.taskId);
    if (valid.kind === "reject" || typeof event.guid !== "string" || !event.guid) {
      const error = new Error(`京东 SKU 下载开始事件不受信任：${valid.kind === "reject" ? valid.reason : "missing_guid"}`);
      rejectStarted(error);
      rejectCompleted(error);
      return;
    }
    activeGuid = event.guid;
    suggestedFilename = String(event.suggestedFilename);
    resolveStarted();
      });
      session.on("Browser.downloadProgress", (event) => {
    if (!activeGuid) return;
    const progress = validateJdWareDownloadProgress(activeGuid, event);
    if (progress.kind === "completed") resolveCompleted({ filePath: typeof event.filePath === "string" ? event.filePath : undefined });
    if (progress.kind === "reject") rejectCompleted(new Error(`Chrome 京东 SKU 下载失败：${progress.reason}`));
      });
      await session.send("Browser.setDownloadBehavior", { behavior: "allowAndName", downloadPath: stagingDirectory, eventsEnabled: true });
    // This is the only task-row click. WareList opens jobExecFileRst in a new target;
    // root Browser events retain the exact GUID without replaying that signed URL.
    await downloadButton.click({ timeout: 15_000 });
    await withDeadline(started, 60_000, "点击京东导出任务下载后 Chrome 未开始浏览器级下载。");
    const finish = await withDeadline(completed, 120_000, "Chrome 京东 SKU XLSX 下载未在两分钟内完成。");
    const stagedPath = path.resolve(finish.filePath || path.join(stagingDirectory, activeGuid!));
    if (!insideDirectory(stagingDirectory, stagedPath)) throw new Error("Chrome 京东 SKU 下载结果越过本轮暂存目录。");
    const fileInfo = await stat(stagedPath);
    if (!fileInfo.isFile() || fileInfo.size < 4) throw new Error("Chrome 京东 SKU 下载文件为空或不完整。");
    const bytes = await readFile(stagedPath);
    if (bytes[0] !== 0x50 || bytes[1] !== 0x4b || bytes[2] !== 0x03 || bytes[3] !== 0x04) throw new Error("Chrome 京东 SKU 下载文件不是 XLSX ZIP 工作簿。");
    validateJdWareMasterWorkbook(bytes, task.successRows ?? undefined);
    const savedPath = path.join(downloadDirectory, `jd-ware-${task.taskId}-${randomUUID()}-${safeDownloadedFilename(suggestedFilename, task.taskId)}`);
    await rename(stagedPath, savedPath);
    await stat(savedPath);
      return { savedPath, verified: true };
    } catch (error) {
    // The task-row action was sent exactly once; never retry it automatically.
      return { savedPath: undefined, verified: false, error: String(error) };
    } finally {
      if (session) {
        await session.send("Browser.setDownloadBehavior", { behavior: "default" }).catch(() => undefined);
        await session.detach().catch(() => undefined);
      }
    }
  });
}

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const read = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${read("year")}-${read("month")}-${read("day")}`;
}

export async function importSkuFile(baseUrl: string, filePath: string, shopName: string, request: typeof fetch = fetch) {
  if (!shopName.trim()) throw new Error("京东 SKU 导入必须显式绑定受控店铺名称。");
  const bytes = await readFile(filePath);
  const form = new FormData();
  form.append("source", "jd_product_master");
  form.append("platform", "京东");
  form.set("shop_name", shopName);
  form.append("snapshot_date", shanghaiToday());
  form.append("note", "京东 SKU 自动下载后导入");
  form.append(
    "file",
    new File([bytes], path.basename(filePath), {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
  );

  const response = await request(`${baseUrl}/api/netshop/import`, {
    method: "POST",
    body: form,
    signal: AbortSignal.timeout(120_000),
  });
  const payload = await response.json().catch(() => null) as {
    ok?: boolean;
    status?: string;
    message?: string;
    batch?: { id?: string; source?: string; dataset?: string; platform?: string; shopName?: string; status?: string; warningCount?: number; rowCount?: number };
  } | null;
  const batch = payload?.batch;
  const expectedHttpStatus = payload?.status === "imported" ? 201 : payload?.status === "duplicate" ? 200 : 0;
  if (response.status !== expectedHttpStatus || !payload?.ok || (payload.status !== "imported" && payload.status !== "duplicate")
    || !batch?.id || !Number.isSafeInteger(batch.rowCount) || batch.rowCount! <= 0
    || batch.source !== "jd_product_master" || batch.dataset !== "product_master" || batch.platform !== "京东"
    || batch.shopName !== shopName || batch.status !== "completed" || batch.warningCount !== 0) {
    throw new Error(payload?.message ?? `运营管理系统 SKU 导入失败（HTTP ${response.status}）`);
  }
  return {
    status: payload.status as "imported" | "duplicate",
    message: payload.message ?? "京东 SKU 已自动导入运营管理系统",
    batchId: batch.id,
    rowCount: batch.rowCount!,
    source: "jd_product_master" as const,
    dataset: "product_master" as const,
    platform: "京东" as const,
    shopName,
    batchStatus: "completed" as const,
    warningCount: 0 as const,
  };
}

async function maybeCaptureDebug(page: Page, label: string, enabled: boolean) {
  if (!enabled) return;
  await ensureDir(artifactDir);
  await page.screenshot({ path: path.join(artifactDir, `${label}.png`), fullPage: false }).catch(() => undefined);
}

export async function runShopSkuExport(
  page: Page,
  options: CliOptions,
  checkpoint: (patch: Partial<WareExportAudit>) => Promise<void> = async () => undefined,
  recovery: JdWareExportRecovery | null = null,
  abandonRecovery: (() => Promise<void>) | null = null,
  queryBootstrapState: JdWareQueryBootstrapState = createJdWareQueryBootstrapState(),
): Promise<ScriptResult> {
  const startedAt = Date.now();
  const notes: string[] = [];
  await maybeCaptureDebug(page, "before-export", options.debug);

  const confirm = await openSkuExportDialog(page, queryBootstrapState);
  const existingTasks = await readLoadedExportTasks(page);
  await checkpoint({
    stage: "select_task",
    baselineTaskIds: existingTasks.map((task) => task.taskId),
  });

  let task: JdWareExportTask;
  let reusedLatest = false;
  const recovered = recovery ? selectRecoverableJdWareExportTask(existingTasks, recovery) : null;
  if (recovered?.kind === "ambiguous") {
    throw new Error(`活动任务清单匹配到多个 SKU 导出任务（${recovered.tasks.map((item) => item.taskId).join("、")}），已停止且不会创建新任务。`);
  }
  if (recovered?.kind === "missing") {
    const decision = decideJdWareExportBaselineRecoveryAbandonment(recovery!, existingTasks, true);
    if (decision.kind !== "abandon") {
      throw new Error(`活动任务清单对应的 SKU 导出任务尚未唯一出现（${decision.reason}）；已保留清单且不会创建新任务。`);
    }
    if (!abandonRecovery) throw new Error("活动任务清单可放弃但未提供原子归档处理，已停止且不会创建新任务。");
    await abandonRecovery();
    recovery = null;
  }
  if (recovered?.kind === "task") {
    if (recovered.task.status === "failed") {
      await checkpoint({ stage: "recovered_task_failed", taskId: recovered.task.taskId, taskStatus: recovered.task.status });
      throw new Error(`活动任务 ${recovered.task.taskId} 已失败：${recovered.task.resultText ?? recovered.task.rowText}`);
    }
    await checkpoint({ stage: "take_over_recovered_task", taskId: recovered.task.taskId, taskStatus: recovered.task.status });
    task = recovered.task.status === "completed"
      ? recovered.task
      : await waitForKnownTask(page, recovered.task.taskId, options.taskTimeoutMs, async (observed) => {
        await checkpoint({ stage: "wait_recovered_task", taskId: observed.taskId, taskStatus: observed.status });
      });
    reusedLatest = true;
    notes.push(`按活动任务清单接管导出任务 ${task.taskId}，跳过创建新任务。`);
  } else {
    const selection = selectExistingJdWareExportTask(existingTasks, options.reuseLatest);
    if (selection.kind === "unowned_pending") {
      throw new Error(`检测到没有活动任务清单归属的待处理 SKU 导出任务（${selection.tasks.map((item) => item.taskId).join("、")}），已停止且不会猜测接管。`);
    }
    if (selection.kind === "ambiguous_pending") {
      throw new Error(`检测到多个待处理 SKU 导出任务（${selection.tasks.map((item) => item.taskId).join("、")}），无法安全接管，已停止且不会创建新任务。`);
    }
    if (selection.kind === "completed") {
      task = selection.task;
      reusedLatest = true;
      await checkpoint({ stage: "reuse_completed_task", taskId: task.taskId, taskStatus: task.status });
      notes.push(`复用已完成导出任务 ${task.taskId}，跳过创建新任务。`);
    } else {
      if (options.reuseLatest) throw new Error("没有可复用的已完成 SKU 导出记录，也没有待处理任务，请不带 --reuse-latest 重新运行。");
      const previousTaskIds = new Set(existingTasks.map((item) => item.taskId));
      // Persist the baseline before the irreversible remote click. If the
      // process stops before learning the task id, the next run can still
      // associate exactly one post-baseline row.
      await checkpoint({ stage: "task_submitting", baselineTaskIds: [...previousTaskIds] });
      const submission = await clickAndConfirmJdWareExportSubmission(
        page,
        confirm,
        () => checkpoint({ stage: "task_click_invoked", baselineTaskIds: [...previousTaskIds] }),
      );
      // A resolved Playwright click only proves the UI event was invoked.  JD
      // can ignore it or delay creation, so do not report a submitted task
      // until the export-record table exposes exactly one post-baseline row.
      await checkpoint({ stage: "task_submission_accepted", baselineTaskIds: [...previousTaskIds] });
      task = await waitForTask(page, previousTaskIds, options.taskTimeoutMs, async (observed) => {
        await checkpoint({ stage: "task_observed", taskId: observed.taskId, taskStatus: observed.status });
      });
      if (submission.message) notes.push(`京东创建接口：${submission.message}`);
      notes.push(`已确认新的 SKU 导出任务 ${task.taskId}。`);
    }
  }

  await maybeCaptureDebug(page, "task-completed", options.debug);
  const download = await saveTaskDownload(page, task, options.downloadDirectory);
  if (download.savedPath) await checkpoint({ stage: "downloaded", taskId: task.taskId, taskStatus: task.status, savedPath: download.savedPath });
  let importResult: ScriptResult["importResult"];
  if (download.verified && download.savedPath && options.autoImport) {
    await checkpoint({ stage: "auto_import", taskId: task.taskId, taskStatus: task.status, savedPath: download.savedPath });
    const completedImport = await importSkuFile(options.baseUrl, download.savedPath, options.shopName);
    importResult = completedImport;
    notes.push(`auto-imported SKU file: ${completedImport.message}`);
  } else if (download.verified && !options.autoImport) {
    notes.push("auto-import skipped by --no-auto-import");
  }
  if (download.verified) notes.push(`已保存下载文件：${download.savedPath}`);
  else notes.push(`已触发下载，但未收到浏览器下载事件：${download.error}`);

  return {
    status: download.verified ? "completed" : "download_triggered_unverified",
    targetUrl,
    reusedLatest,
    task,
    downloadSavedPath: download.savedPath,
    importResult,
    notes,
    elapsedMs: Date.now() - startedAt,
  };
}

async function main() {
  const options = await parseCliOptions();
  await ensureDir(artifactDir);
  const activeTaskPath = wareActiveTaskPath(options.storeKey);
  const auditPath = path.join(artifactDir, `run-${Date.now()}.json`);
  let audit = createWareExportAudit(options);
  let recovery: StoreWareExportRecovery | null = null;
  const persistAudit = async (patch: Partial<WareExportAudit>) => {
    audit = advanceWareExportAudit(audit, patch);
    await writeJsonAtomic(auditPath, audit);
    if (patch.stage === "task_submitting" && patch.baselineTaskIds) {
      recovery = { version: 2, storeKey: options.storeKey, shopId: options.shopId, shopName: options.shopName, baselineTaskIds: patch.baselineTaskIds, createdAt: new Date().toISOString() };
      await writeJsonAtomic(activeTaskPath, recovery);
    } else if (patch.taskId) {
      recovery = {
        version: 2, storeKey: options.storeKey, shopId: options.shopId, shopName: options.shopName,
        baselineTaskIds: recovery?.baselineTaskIds ?? audit.baselineTaskIds ?? [],
        taskId: patch.taskId,
        createdAt: recovery?.createdAt ?? new Date().toISOString(),
      };
      if (patch.taskStatus === "failed") {
        await rm(activeTaskPath, { force: true });
        recovery = null;
      } else {
        await writeJsonAtomic(activeTaskPath, recovery);
      }
    }
  };
  await writeJsonAtomic(auditPath, audit);
  let browser: Awaited<ReturnType<typeof connectPlaywrightBrowser>> | undefined;
  let ownsBrowser = false;
  let revealInteractiveBrowser = false;
  try {
    const legacyRecovery = await readJsonFileOr<JdWareExportRecovery | null>(legacyActiveTaskPath, null);
    if (legacyRecovery) {
      throw new Error(`发现旧版跨店活动任务清单，无法安全判断所属店铺；请人工迁移或确认后删除：${legacyActiveTaskPath}`);
    }
    recovery = await readJsonFileOr<StoreWareExportRecovery | null>(activeTaskPath, null);
    if (recovery) assertJdWareExportRecoveryIdentity(recovery, options);
    await persistAudit({ stage: "launch_browser" });
    const launchResult = await launchJdWareBrowser({
      executablePath: options.executablePath,
      profileDirectory: options.userDataDirectory,
      profileName: options.profileName,
      port: options.port,
      // The uniquely named Playwright page performs the only JD navigation.
      // Starting Chrome at the target URL created an unobserved first page and
      // a second same-store query before the controlled page was attached.
      startUrl: options.interactiveLogin ? targetUrl : "about:blank",
      keepWindowHidden: !options.interactiveLogin && !options.visibleRecovery,
    }, options.interactiveLogin);
    ownsBrowser = Boolean(launchResult.launched);
    await waitForChrome(options.port);
    browser = await connectPlaywrightBrowser(options.port);
    const { page, client } = await connectPlaywrightJackyunTarget(browser, {
      workerName: "codex-jd-ware-export",
      targetUrlPattern: /wares-jdm\.jd\.com/i,
      requireMini: false,
    });
    try {
      const queryBootstrapState = createJdWareQueryBootstrapState();
      await persistAudit({ stage: "verify_product_query", querySource: "initial_navigation" });
      const verifiedQuery = await openTargetPage(page, queryBootstrapState, options);
      await persistAudit({
        stage: "product_query_verified",
        querySource: "initial_navigation",
        queryHttpStatus: 200,
        queryBusinessCode: verifiedQuery.code,
        queryTotal: verifiedQuery.total,
        queryObservedAt: new Date().toISOString(),
      });
      const abandonRecovery = async () => {
        if (!recovery || recovery.taskId) throw new Error("活动任务清单不满足无 taskId 的放弃条件。");
        const archivedPath = path.join(artifactDir, `active-task-${options.storeKey}.abandoned-${Date.now()}.json`);
        const archivedFileName = path.basename(archivedPath);
        const recoveryCreatedAt = recovery.createdAt;
        await rename(activeTaskPath, archivedPath);
        recovery = null;
        await persistAudit({ stage: "recovery_abandoned", recoveryArchivePath: archivedFileName, recoveryCreatedAt });
      };
      const result = await runShopSkuExport(page, options, persistAudit, recovery, abandonRecovery, queryBootstrapState);
      if (result.status !== "completed") {
        const message = "京东 SKU 下载点击已发送，但未验证本地文件；活动任务清单已保留，禁止自动新建任务。";
        await persistAudit({ status: "failed", stage: "download_unverified", taskId: result.task.taskId, taskStatus: result.task.status, result, error: message });
        console.error(message);
        console.log(`@@JD_PIPELINE_RESULT@@${JSON.stringify({ ...result, auditPath })}`);
        console.log(JSON.stringify({ ...result, auditPath }, null, 2));
        process.exitCode = 1;
        return;
      }
      await persistAudit({ status: "completed", stage: "completed", taskId: result.task.taskId, taskStatus: result.task.status, savedPath: result.downloadSavedPath, result });
      await rm(activeTaskPath, { force: true });
      recovery = null;
      console.log(`@@JD_PIPELINE_RESULT@@${JSON.stringify({ ...result, auditPath })}`);
      console.log(JSON.stringify({ ...result, auditPath }, null, 2));
    } finally {
      client.close();
    }
  } catch (error) {
    const message = error instanceof Error ? error.stack ?? error.message : String(error);
    const interactiveAttentionRequired = !options.interactiveLogin && isJdInteractiveBrowserFailure(error);
    revealInteractiveBrowser = options.visibleRecovery && interactiveAttentionRequired;
    if (error instanceof JdWareCreateExportRejectedError && error.definitiveNoTask && recovery && !recovery.taskId) {
      // JD explicitly confirmed that createExportJob failed, so this manifest
      // cannot own a remote task. Preserve it as evidence and allow an
      // immediate corrected retry instead of imposing the ambiguous 30-minute
      // recovery quarantine.
      const archivedPath = path.join(artifactDir, `active-task-${options.storeKey}.rejected-${Date.now()}.json`);
      const recoveryCreatedAt = recovery.createdAt;
      await rename(activeTaskPath, archivedPath);
      recovery = null;
      await persistAudit({
        status: "failed",
        stage: "task_submission_rejected",
        recoveryArchivePath: path.basename(archivedPath),
        recoveryCreatedAt,
        error: message,
      });
    } else {
      await persistAudit({
        status: "failed",
        ...(interactiveAttentionRequired ? { stage: "interactive_attention_required" } : {}),
        error: message,
      });
    }
    console.error(message);
    process.exitCode = 1;
  } finally {
    // Closing a Playwright CDP Browser terminates the actual Chromium process.
    // Interactive login must leave the visible profile open after this command
    // exits so the operator can complete authentication manually.
    if (shouldCloseJdWareBrowserConnection(options.interactiveLogin)) {
      await browser?.close().catch(() => undefined);
    }
    if (ownsBrowser && !options.interactiveLogin) await closeChromeBrowser(options.port);
  }
  if (revealInteractiveBrowser) {
    try {
      await revealJdBrowserForInteractiveFailure({
        executablePath: options.executablePath,
        profileDirectory: options.userDataDirectory,
        profileName: options.profileName,
        port: options.port,
        startUrl: targetUrl,
      });
      console.error(`京东交互异常：已打开 ${options.shopName} 对应的 Chromium profile，请完成人工验证后从原审计续跑。`);
    } catch (revealError) {
      const bounded = revealError instanceof Error ? revealError.message.slice(0, 500) : String(revealError).slice(0, 500);
      await persistAudit({ stage: "interactive_attention_open_failed" });
      console.error(`京东交互异常，但可见 Chromium 打开失败：${bounded}`);
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void withJdChromiumRunLock("product-master", () => withJdWareExportRunLock(main)).catch((error: unknown) => {
    console.error(error instanceof Error ? error.stack ?? error.message : String(error));
    process.exitCode = 1;
  });
}
