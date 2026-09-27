import { mkdir, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { Page } from "playwright-core";
import { closeChromeBrowser, launchDedicatedChrome, waitForChrome } from "../lib/jackyun/cdp-client";
import { connectPlaywrightBrowser, connectPlaywrightJackyunTarget } from "../lib/jackyun/playwright-client";
import {
  acquireJdProductDetailDownload,
  finalizeJdProductDetailDownload,
  findRecentJdProductDetailDownload,
  JD_PRODUCT_DETAIL_REUSE_WINDOW_MS,
} from "../lib/jd/product-detail-download";
import {
  isVerifiedJdDateRangeEcho,
  jdCalendarCellState,
  jdCalendarDateDispatchDecision,
  jdCalendarEndSelectionDecision,
  jdDateRangeSelectionPlan,
} from "../lib/jd/calendar-range-selection";
export {
  isJdCalendarEndSelected,
  isStaticCurrentTimestamp,
  isVerifiedJdDateRangeEcho,
  jdCalendarCellState,
  jdCalendarDateDispatchDecision,
  jdCalendarEndSelectionDecision,
  jdDateRangeSelectionPlan,
} from "../lib/jd/calendar-range-selection";
import {
  assertJdProductDetailTaskManifest,
  jdProductDetailTaskFingerprint,
  selectManifestTaskRow,
  type JdProductDetailTaskManifest,
} from "../lib/jd/product-detail-task-manifest";
import { readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { getJdStore } from "../lib/jd/store-registry";
import { assertJdNaturalDateRange } from "../lib/jd/date-range";
import { withJdChromiumRunLock } from "../lib/jd/chromium-run-lock";
import { hasJdInteractivePageGate, isJdInteractiveBrowserFailure, jdBrowserLaunchMode, revealJdBrowserForInteractiveFailure } from "../lib/jd/browser-mode";
import { assertJdProductDetailStoreIdentity, parseJdProductDetailStoreIdentity } from "../lib/jd/product-detail-store-identity";
import { ensureJdStoreAuthenticatedSession } from "./jd-saved-login";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifactDir = path.join(projectRoot, "outputs", "jdsz-product-detail-export");
const targetUrl = "https://jdsz.jd.com/szweb/view/product/productDetail.html";
const downloadCenterUrl = "https://jdsz.jd.com/szweb/view/reports-center/download-center.html";

async function withJdProductDetailRunLock<T>(task: () => Promise<T>) {
  return withJdChromiumRunLock(
    "product-detail-script",
    task,
    path.join(artifactDir, "jdsz-product-detail-export.run-lock"),
  );
}

type CliOptions = {
  executablePath: string;
  userDataDirectory: string;
  profileName: string;
  port: number;
  downloadDirectory: string;
  storeKey: string;
  shopId: string;
  shopName: string;
  loginMode?: "manual" | "windows_dpapi_credentials";
  startDate: string;
  endDate: string;
  dimension: "SKU" | "SPU";
  debug: boolean;
  interactiveLogin: boolean;
  visibleRecovery: boolean;
  autoImport: boolean;
  baseUrl: string;
};

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function addDays(date: string, days: number) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

async function parseArgs(argv: string[]): Promise<CliOptions> {
  const values = new Map<string, string>();
  const flags = new Set<string>();
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key?.startsWith("--")) continue;
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) {
      values.set(key, next);
      index += 1;
    } else {
      flags.add(key);
    }
  }
  if (flags.has("--interactive-login") && flags.has("--no-visible-recovery")) {
    throw new Error("--interactive-login 不能与 --no-visible-recovery 同时使用。");
  }

  const hasStartDate = values.has("--start-date");
  const hasEndDate = values.has("--end-date");
  if (flags.has("--start-date") || flags.has("--end-date") || hasStartDate !== hasEndDate) {
    throw new Error("--start-date 与 --end-date 必须成对提供有效值。");
  }
  const yesterday = addDays(shanghaiToday(), -1);
  const range = hasStartDate
    ? assertJdNaturalDateRange(values.get("--start-date")!, values.get("--end-date")!)
    : { startDate: `${yesterday.slice(0, 8)}01`, endDate: yesterday };
  const { startDate, endDate } = range;
  const dimension = (values.get("--dimension") ?? "SKU").toUpperCase();
  if (dimension !== "SKU" && dimension !== "SPU") {
    throw new Error("--dimension 必须是 SKU 或 SPU。");
  }

  const store = await getJdStore(values.get("--store-key") ?? "jd-yiyong-director");
  const shopId = values.get("--shop-id") ?? store.shopId;
  if (!/^\d+$/.test(shopId)) throw new Error("--shop-id 必须是纯数字。");
  if (shopId !== store.shopId) throw new Error("--shop-id 与受控店铺注册表不一致。");
  const executablePath = path.resolve(values.get("--chrome-path") ?? store.browser.executablePath);
  const requestedProfileDirectory = path.resolve(values.get("--profile-dir") ?? store.browser.profileDir);
  const port = Number(values.get("--port") ?? store.browser.debugPort);
  const downloadDirectory = path.resolve(values.get("--download-dir") ?? store.browser.downloadDir);
  if (executablePath !== path.resolve(store.browser.executablePath)
    || requestedProfileDirectory !== path.resolve(store.browser.profileDir)
    || port !== store.browser.debugPort
    || downloadDirectory !== path.resolve(store.browser.downloadDir)) {
    throw new Error("京东商智 Chromium、profile、调试端口或下载目录与受控店铺注册表不一致。");
  }
  return {
    executablePath,
    userDataDirectory: store.browser.userDataDir,
    profileName: store.browser.profileName,
    port,
    downloadDirectory,
    storeKey: store.storeKey,
    shopId,
    shopName: store.shopName,
    loginMode: store.loginMode,
    startDate,
    endDate,
    dimension,
    debug: flags.has("--debug"),
    interactiveLogin: flags.has("--interactive-login"),
    visibleRecovery: !flags.has("--no-visible-recovery"),
    autoImport: !flags.has("--no-auto-import"),
    baseUrl: (values.get("--base-url") ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000").replace(/\/$/, ""),
  };
}

export function jdProductDetailDownloadPrefix(options: Pick<CliOptions, "shopId" | "startDate" | "endDate">) {
  return `${options.shopId}_商品明细_离线_不包括对比时间_分天下载_${options.startDate}_${options.endDate}`;
}

export function taskManifestPath(options: Pick<CliOptions, "dimension" | "shopId" | "startDate" | "endDate">) {
  if (!/^\d+$/.test(options.shopId) || !/^\d{4}-\d{2}-\d{2}$/.test(options.startDate) || !/^\d{4}-\d{2}-\d{2}$/.test(options.endDate)) throw new Error("JD task manifest path input is invalid.");
  const file = path.resolve(artifactDir, `${options.dimension.toLowerCase()}-task-${options.shopId}-${options.startDate}-${options.endDate}.json`);
  if (path.relative(artifactDir, file).startsWith("..") || path.dirname(file) !== artifactDir) throw new Error("JD task manifest path escapes artifact directory.");
  return file;
}

export async function readAndAssertJdProductDetailStoreIdentity(
  page: Page,
  expected: Pick<CliOptions, "shopId" | "shopName">,
) {
  const links = page.locator('a[href*="mall.jd.com/index-"]').filter({ visible: true });
  await links.first().waitFor({ state: "visible", timeout: 15_000 });
  const candidates: Array<{ href: string | null; text: string }> = [];
  for (let index = 0; index < await links.count(); index += 1) {
    const link = links.nth(index);
    candidates.push({ href: await link.getAttribute("href"), text: await link.innerText() });
  }
  return assertJdProductDetailStoreIdentity(parseJdProductDetailStoreIdentity(candidates), expected);
}

async function saveFailureScreenshot(page: Page, name: string) {
  await mkdir(artifactDir, { recursive: true });
  await page.screenshot({ path: path.join(artifactDir, `${name}-${Date.now()}.png`), fullPage: true }).catch(() => undefined);
}

async function currentDateEcho(page: Page) {
  const echo = page.locator(".jmt-combo-date-picker-echo-wrap").filter({ visible: true });
  if (await echo.count() !== 1) throw new Error("无法唯一识别京东商智当前日期显示区域。");
  return echo.innerText();
}

async function waitForSelectedDateRange(page: Page, startDate: string, endDate: string, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let echoText = "";
  while (Date.now() < deadline) {
    echoText = await currentDateEcho(page);
    if (isVerifiedJdDateRangeEcho(echoText, startDate, endDate)) return echoText;
    await page.waitForTimeout(100);
  }
  throw new Error(`日期选择未生效：目标 ${startDate} ~ ${endDate}，页面显示 ${echoText.replace(/\s+/g, " ")}`);
}

async function assertDimensionAndDateSelection(page: Page, dimension: CliOptions["dimension"], startDate: string, endDate: string) {
  const dimensionTab = page.getByRole("tab", { name: dimension, exact: true });
  if (await dimensionTab.count() !== 1 || await dimensionTab.getAttribute("aria-selected") !== "true") {
    throw new Error(`${dimension} 维度未处于选中状态，已禁止创建下载任务。`);
  }
  return waitForSelectedDateRange(page, startDate, endDate, 1_000);
}

export function isJdNpsSurveyPointerInterception(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /intercepts pointer events/i.test(message) && /ux-scene-research/i.test(message);
}

export async function clickWithJdNpsSurveyRecovery(
  click: () => Promise<void>,
  dismissSurvey: () => Promise<void>,
) {
  try {
    await click();
    return false;
  } catch (error) {
    // The survey is lazy-mounted and can appear after the initial bounded
    // observation. Recover only from Playwright's exact pointer-interception
    // evidence, revalidate the known opt-out, then replay this reversible UI
    // action once. Any other overlay or second failure remains fail-closed.
    if (!isJdNpsSurveyPointerInterception(error)) throw error;
    await dismissSurvey();
    await click();
    return true;
  }
}

async function selectDateRange(page: Page, startDate: string, endDate: string) {
  await dismissJdNpsSurveyModal(page);
  // New JD sessions keep the custom-range item inside the collapsed date menu.
  // Open the menu first; this is a no-op when it is already expanded.
  const echo = page.locator(".jmt-combo-date-picker-echo-wrap").filter({ visible: true });
  if (await echo.count() !== 1) throw new Error("无法唯一识别京东商智当前时间入口。");
  const customSelector = '[data-event-content="当前时间_自定义"]';
  if (await page.locator(customSelector).filter({ visible: true }).count() === 0) {
    await clickWithJdNpsSurveyRecovery(
      () => echo.click(),
      () => dismissJdNpsSurveyModal(page),
    );
    await page.waitForTimeout(200);
  }
  const custom = page.locator(customSelector).filter({ visible: true });
  if (await custom.count() !== 1) throw new Error("无法唯一识别自定义时间入口。");
  await clickWithJdNpsSurveyRecovery(
    () => custom.click(),
    () => dismissJdNpsSurveyModal(page),
  );
  await page.waitForTimeout(300);

  const monthKey = (date: string) => date.slice(0, 7);
  const cellSelector = (date: string) =>
    `td[data-event-content="当前时间自定义_${date}"]`;
  const popup = page.locator(".jmt-date-picker, .jmt-date-picker-panel, [class*='date-picker']").filter({ visible: true }).first();

  const getHeaderText = async () => {
    const candidates = [
      page.locator(".jmt-date-picker-header").filter({ visible: true }).first(),
      page.locator(".jmt-date-picker-calendar-header").filter({ visible: true }).first(),
      popup.getByText(/\d{4}年\d{1,2}月/).first(),
    ];
    for (const candidate of candidates) {
      if (await candidate.count().catch(() => 0)) return candidate.innerText().catch(() => "");
    }
    return "";
  };

  const clickCalendarNav = async (direction: "prev" | "next") => {
    const selectors = direction === "prev"
      ? ["button[aria-label*='上一月']", "button[title*='上一月']", ".jmt-date-picker-prev-btn", ".jmt-date-picker-calendar-prev-btn"]
      : ["button[aria-label*='下一月']", "button[title*='下一月']", ".jmt-date-picker-next-btn", ".jmt-date-picker-calendar-next-btn"];
    for (const selector of selectors) {
      const button = popup.locator(selector).filter({ visible: true }).first();
      if (await button.count().catch(() => 0)) {
        await button.click().catch(() => undefined);
        return true;
      }
    }
    return false;
  };

  const ensureMonthVisible = async (targetMonth: string) => {
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      const headerText = await getHeaderText();
      if (headerText.includes(targetMonth.replace("-", "年").replace(/-(\d{2})$/, (_, m) => `${Number(m)}月`))) return;
      if (headerText && /\d{4}年\d{1,2}月/.test(headerText)) {
        const current = headerText.match(/(\d{4})年\s*(\d{1,2})月/);
        if (current) {
          const currentMonth = `${current[1]}-${String(Number(current[2])).padStart(2, "0")}`;
          const direction = currentMonth < targetMonth ? "next" : "prev";
          if (!(await clickCalendarNav(direction))) break;
          await page.waitForTimeout(200);
          continue;
        }
      }
      if (await clickCalendarNav("prev") || await clickCalendarNav("next")) {
        await page.waitForTimeout(200);
        continue;
      }
      break;
    }
  };

  const selectDay = async (date: string) => {
    await ensureMonthVisible(monthKey(date));
    const cell = page.locator(cellSelector(date)).filter({ visible: true });
    await cell.waitFor({ state: "visible", timeout: 10_000 });
    if (await cell.count() !== 1) throw new Error(`日期 ${date} 的可选单元格不是唯一元素。`);
    if (jdCalendarDateDispatchDecision(await cell.getAttribute("class")) === "blocked_disabled") {
      throw new Error(`日期 ${date} 尚未开放，已禁止点击日历单元格。`);
    }
    // A browser translation extension can intercept pointer events above the
    // calendar. Dispatching the native click on this unique validated cell
    // reaches JD's date-picker handler without relying on pointer hit-testing.
    await cell.dispatchEvent("click");
  };
  const waitForStartSelected = async (date: string, timeoutMs: number) => {
    const deadline = Date.now() + timeoutMs;
    const cell = page.locator(cellSelector(date)).filter({ visible: true });
    while (Date.now() < deadline) {
      try {
        const state = jdCalendarCellState(await cell.getAttribute("class"));
        if (!state.disabled && state.start && state.selected) return true;
      } catch {
        // JD may replace the calendar cell during the date-picker re-render.
        // Treat that frame as an unconfirmed state and let the caller retry.
      }
      await page.waitForTimeout(50);
    }
    return false;
  };
  const waitForEndSelectionOrEcho = async (timeoutMs: number) => {
    const deadline = Date.now() + timeoutMs;
    let lastClassName: string | null = null;
    let lastEcho = "";
    let observed = false;
    const cell = page.locator(cellSelector(endDate)).filter({ visible: true });
    while (Date.now() < deadline) {
      try {
        lastClassName = await cell.getAttribute("class");
        lastEcho = await currentDateEcho(page);
        observed = true;
        const decision = jdCalendarEndSelectionDecision({ className: lastClassName, echoText: lastEcho, startDate, endDate });
        if (decision === "confirmed_echo" || decision === "blocked_disabled") return decision;
      } catch {
        // JD may replace the cell during re-render.  Missing observation never
        // authorizes another endpoint dispatch.
      }
      await page.waitForTimeout(50);
    }
    if (!observed) return "unconfirmed";
    return jdCalendarEndSelectionDecision({ className: lastClassName, echoText: lastEcho, startDate, endDate });
  };
  const [startSelectionDate, endSelectionDate] = jdDateRangeSelectionPlan(startDate, endDate);
  await selectDay(startSelectionDate);
  if (!await waitForStartSelected(startDate, 1_000)) await selectDay(startDate);
  if (!await waitForStartSelected(startDate, 5_000)) {
    throw new Error(`起始日期 ${startDate} 点击后未进入区间起点状态。`);
  }
  // A single-day range still needs a second click: the first establishes the
  // start, while the second closes the range as its end.
  await selectDay(endSelectionDate);
  const firstEndDecision = await waitForEndSelectionOrEcho(1_000);
  if (firstEndDecision === "blocked_disabled") throw new Error(`结束日期 ${endDate} 尚未开放，已禁止再次点击。`);
  if (firstEndDecision !== "confirmed_echo") {
    // `end+selected` can be a hover-only secondDate decoration.  Re-clicking
    // can turn an ambiguous range into a different range, so strict echo is
    // the only submit gate and failure stays side-effect free after the first
    // validated endpoint dispatch.
    throw new Error(`结束日期 ${endDate} 点击后未获得严格日期回显，已禁止重试点击。`);
  }
  // The picker itself applies the custom range.  Clicking the page-level
  // Query action switches this JD page back to a realtime-summary flow, so it
  // must not be used to validate an offline daily export.
  await waitForSelectedDateRange(page, startDate, endDate);
  // JD's current picker keeps the selected range pending until its own
  // confirm action runs; closing with Escape leaves the calendar portal above
  // the subsequent download action.
  const confirm = page.locator('[data-event-name="confirm"][data-event-content="true"]').filter({ visible: true });
  await confirm.waitFor({ state: "visible", timeout: 5_000 });
  if (await confirm.count() !== 1) throw new Error("无法唯一识别京东商智日期确认按钮。");
  await confirm.dispatchEvent("click");
  await page.waitForTimeout(200);
  const calendar = page.locator('.jmt-date-picker-calendar').filter({ visible: true });
  if (await calendar.count() > 0) await calendar.first().waitFor({ state: "hidden", timeout: 5_000 });
}

async function waitForDataRefresh(page: Page) {
  await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => undefined);
  const deadline = Date.now() + 30_000;
  const loading = page.locator(".jd-spin-spinning, .jmt-spin-spinning, [aria-busy='true']").filter({ visible: true });
  while (Date.now() < deadline && await loading.count() > 0) await page.waitForTimeout(200);
  if (await loading.count() > 0) throw new Error("日期切换后的数据仍在加载，已禁止创建下载任务。");
}

export function isSafeJdNoticeCloseLabel(label: string) {
  return /^(close|关闭|忽略|×|✕)$/i.test(label.trim());
}

export function isJdProductOverviewNpsSurveyText(text: string) {
  const normalized = text.replace(/\s+/g, "");
  return normalized.includes("请您对商品概览整体使用感受打分")
    && normalized.includes("您在使用商品概览时有什么建议")
    && normalized.includes("我不愿作答")
    && normalized.includes("提交");
}

export function isSafeJdNpsSurveySkipLabel(label: string) {
  return label.trim() === "我不愿作答";
}

export type JdNoticeDismissSnapshot = {
  noticeCount: number;
  noticeKey?: string;
  closeControlCount: number;
};

export async function dismissJdNoticeWithBoundedRetry(
  readSnapshot: () => Promise<JdNoticeDismissSnapshot>,
  clickClose: () => Promise<void>,
  sleep: (ms: number) => Promise<void>,
  options: { readinessAttempts?: number; hiddenAttempts?: number; intervalMs?: number; maxClicks?: number } = {},
) {
  const readinessAttempts = options.readinessAttempts ?? 30;
  const hiddenAttempts = options.hiddenAttempts ?? 20;
  const intervalMs = options.intervalMs ?? 100;
  const maxClicks = options.maxClicks ?? 2;
  let originalNoticeKey: string | undefined;
  let clicks = 0;

  while (clicks < maxClicks) {
    let stableReadySamples = 0;
    for (let attempt = 0; attempt < readinessAttempts; attempt += 1) {
      const snapshot = await readSnapshot();
      if (snapshot.noticeCount === 0) return clicks;
      if (snapshot.noticeCount !== 1) throw new Error("京东公告弹窗不唯一，已停止避免误点");
      if (!snapshot.noticeKey) throw new Error("京东公告弹窗缺少稳定身份，已停止避免误点");
      if (originalNoticeKey && snapshot.noticeKey !== originalNoticeKey) {
        throw new Error("京东公告在关闭过程中发生变化，已停止避免连续误点");
      }
      originalNoticeKey ??= snapshot.noticeKey;
      if (snapshot.closeControlCount > 1) throw new Error("京东公告弹窗关闭按钮不唯一，已停止避免误点");
      stableReadySamples = snapshot.closeControlCount === 1 ? stableReadySamples + 1 : 0;
      if (stableReadySamples >= 2) break;
      await sleep(intervalMs);
    }
    if (stableReadySamples < 2) throw new Error("京东公告弹窗关闭按钮未达到稳定可用状态");

    await clickClose();
    clicks += 1;
    for (let attempt = 0; attempt < hiddenAttempts; attempt += 1) {
      const snapshot = await readSnapshot();
      if (snapshot.noticeCount === 0) return clicks;
      if (snapshot.noticeCount !== 1 || snapshot.noticeKey !== originalNoticeKey) {
        throw new Error("京东公告在关闭过程中发生变化，已停止避免连续误点");
      }
      await sleep(intervalMs);
    }
  }
  throw new Error("京东公告弹窗在两次受控关闭后仍然可见");
}

async function dismissJdNoticeModal(page: Page) {
  const notice = () => page.locator('.jd-modal-wrap').filter({ visible: true }).filter({ has: page.locator('img[alt="公告图片"]') });
  const closeControls = () => notice().locator('button[aria-label="Close"], .close-modal').filter({ visible: true });
  // The announcement is lazy-mounted a few seconds after the product page
  // becomes interactive. Observe that bounded window before concluding that
  // no notice exists, otherwise it can appear over the next business click.
  await notice().first().waitFor({ state: "visible", timeout: 5_000 }).catch(() => undefined);
  await dismissJdNoticeWithBoundedRetry(async () => {
    const current = notice();
    const noticeCount = await current.count();
    const noticeKey = noticeCount === 1
      ? await current.locator('img[alt="公告图片"]').getAttribute("src") ?? undefined
      : undefined;
    return { noticeCount, noticeKey, closeControlCount: noticeCount === 1 ? await closeControls().count() : 0 };
  }, async () => {
    const current = notice();
    if (await current.count() !== 1) throw new Error("京东公告弹窗不唯一，已停止避免误点");
    const close = closeControls();
    if (await close.count() !== 1) throw new Error("京东公告弹窗缺少唯一关闭按钮，已停止避免误点");
    await close.click();
  }, (ms) => page.waitForTimeout(ms));
}

async function dismissJdNpsSurveyModal(page: Page) {
  const survey = () => page.locator('#ux-scene-research').filter({ visible: true }).filter({ hasText: /请您对商品概览整体使用感受打分/ });
  const skipControls = () => survey().getByText("我不愿作答", { exact: true }).filter({ visible: true });
  await survey().first().waitFor({ state: "visible", timeout: 5_000 }).catch(() => undefined);
  await dismissJdNoticeWithBoundedRetry(async () => {
    const current = survey();
    const noticeCount = await current.count();
    const text = noticeCount === 1 ? await current.innerText().catch(() => "") : "";
    const noticeKey = noticeCount === 1 && isJdProductOverviewNpsSurveyText(text)
      ? "jd-product-overview-nps-survey"
      : undefined;
    const closeControlCount = noticeKey ? await skipControls().count() : 0;
    return { noticeCount, noticeKey, closeControlCount };
  }, async () => {
    const current = survey();
    if (await current.count() !== 1) throw new Error("京东商品概览评价弹层不唯一，已停止避免误点");
    const skip = skipControls();
    if (await skip.count() !== 1 || !isSafeJdNpsSurveySkipLabel(await skip.innerText())) {
      throw new Error("京东商品概览评价弹层缺少唯一安全退出项，已停止避免误点");
    }
    await skip.click();
  }, (ms) => page.waitForTimeout(ms), { maxClicks: 1 });
}

async function selectDimensionAndWait(page: Page, dimension: CliOptions["dimension"]) {
  await dismissJdNoticeModal(page);
  const dimensionTab = page.getByRole("tab", { name: dimension, exact: true });
  await dimensionTab.waitFor({ state: "visible", timeout: 10_000 });
  if (await dimensionTab.count() !== 1) throw new Error(`无法唯一识别 ${dimension} 维度标签。`);
  if (await dimensionTab.getAttribute("aria-selected") !== "true") await dimensionTab.click();
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline && await dimensionTab.getAttribute("aria-selected") !== "true") {
    await page.waitForTimeout(100);
  }
  if (await dimensionTab.getAttribute("aria-selected") !== "true") throw new Error(`${dimension} 维度切换未生效。`);
  await waitForDataRefresh(page);
}

async function clickAnyVisibleOption(dialog: ReturnType<Page["locator"]>, texts: string[]) {
  for (const text of texts) {
    const exact = dialog.getByText(text, { exact: true }).filter({ visible: true });
    if (await exact.count() === 1) {
      const label = exact.locator("xpath=ancestor::label[1]");
      if (await label.count() === 1) {
        await label.click();
        return text;
      }
      await exact.click();
      return text;
    }
    const partial = dialog.getByText(text).filter({ visible: true });
    if (await partial.count() === 1) {
      const label = partial.locator("xpath=ancestor::label[1]");
      if (await label.count() === 1) {
        await label.click();
        return text;
      }
      await partial.click();
      return text;
    }
  }
  return null;
}

async function selectDialogRadio(dialog: ReturnType<Page["locator"]>, text: string) {
  const labelText = dialog.getByText(text, { exact: true }).filter({ visible: true });
  await labelText.waitFor({ state: "visible", timeout: 10_000 });
  if (await labelText.count() !== 1) throw new Error(`下载弹窗选项不是唯一元素：${text}`);
  const label = labelText.locator("xpath=ancestor::label[1]");
  const radio = label.locator('input[type="radio"]');
  if (await label.count() !== 1 || await radio.count() !== 1) throw new Error(`无法识别下载弹窗单选项：${text}`);
  if (!await radio.isChecked()) await label.click();
  if (!await radio.isChecked()) throw new Error(`下载弹窗选项未成功选中：${text}`);
}

export function isRealtimeSummaryDownloadDialog(dialogText: string) {
  return dialogText.includes("下载设置")
    && !dialogText.includes("分天下载")
    && !dialogText.includes("不包含对比时间");
}

async function configureDownloadDialog(page: Page, beforeConfirm?: () => Promise<void>) {
  const downloadButton = page.getByText("下载数据", { exact: true }).filter({ visible: true });
  if (await downloadButton.count() !== 1) throw new Error("无法唯一识别商品明细区域的下载数据按钮。");
  await downloadButton.click();

  const dialog = page.getByRole("dialog").filter({ visible: true });
  await dialog.waitFor({ state: "visible", timeout: 15_000 });
  if (await dialog.count() !== 1) throw new Error("未出现唯一的商品明细下载弹窗。");
  const dialogText = await dialog.innerText();
  // This compact settings dialog creates a realtime-summary workbook.  It is
  // not an offline daily task, so reject it before touching the confirmation
  // callback (which is what persists a submitting manifest).
  if (isRealtimeSummaryDownloadDialog(dialogText)) {
    throw new Error("当前“下载设置”弹窗会创建实时汇总文件，不是离线分天下载；已禁止确认和写入任务清单。");
  }
  if (!dialogText.includes("分天下载") || !dialogText.includes("不包含对比时间")) {
    throw new Error("当前弹窗不是商品明细分天下载弹窗，已禁止继续。");
  }
  try {
    await selectDialogRadio(dialog, "分天下载");
  } catch {
    const selected = await clickAnyVisibleOption(dialog, ["分天下载", "按天下载", "按日下载"]);
    if (!selected) throw new Error("无法在下载弹窗中找到分天下载选项。");
  }
  try {
    await selectDialogRadio(dialog, "不包含对比时间");
  } catch {
    const selected = await clickAnyVisibleOption(dialog, ["不包含对比时间", "不含对比时间"]);
    if (!selected) throw new Error("无法在下载弹窗中找到不包含对比时间选项。");
  }
  const confirm = dialog.getByRole("button", { name: "确定", exact: true }).filter({ visible: true });
  if (await confirm.count() !== 1) throw new Error("无法唯一识别商品明细下载弹窗的确定按钮。");
  await beforeConfirm?.();
  await confirm.click();
}

async function prepareExport(page: Page, options: CliOptions, beforeConfirm?: () => Promise<void>) {
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);
  const bodyText = await page.locator("body").innerText().catch(() => "");
  if (hasJdInteractivePageGate(bodyText)) {
    throw new Error("京东商智需要人工完成验证码或安全验证。");
  }
  if (/登录|账号|密码|验证码/.test(bodyText) && !/商品明细/.test(bodyText)) {
    throw new Error("京东商智登录状态无效；请先在已打开的专用 Chrome 中登录，再重新运行。");
  }
  await readAndAssertJdProductDetailStoreIdentity(page, options);

  try {
    await selectDimensionAndWait(page, options.dimension);
    await selectDateRange(page, options.startDate, options.endDate);
    await waitForDataRefresh(page);
    await assertDimensionAndDateSelection(page, options.dimension, options.startDate, options.endDate);
    if (options.debug) await page.screenshot({ path: path.join(artifactDir, "01-selected-range.png"), fullPage: true });
    await configureDownloadDialog(page, beforeConfirm);
  } catch (error) {
    await saveFailureScreenshot(page, "selection-gate-failed");
    throw error;
  }
}

async function openDownloadCenter(page: Page) {
  const go = page.getByText("前往查看", { exact: true }).filter({ visible: true });
  await go.waitFor({ state: "visible", timeout: 120_000 });
  if (await go.count() !== 1) throw new Error(`Expected exactly one download-center link; found ${await go.count()}.`);
  const href = await go.getAttribute("href");
  if (href) {
    await page.goto(new URL(href, page.url()).toString(), { waitUntil: "domcontentloaded", timeout: 30_000 });
    return page;
  }

  const popupPromise = page.context().waitForEvent("page", { timeout: 8_000 }).catch(() => null);
  await go.click();
  const popup = await popupPromise;
  const result = popup ?? page;
  await result.waitForLoadState("domcontentloaded", { timeout: 30_000 }).catch(() => undefined);
  return result;
}

async function taskRow(page: Page, expectedPrefix: string) {
  const rows = page.locator("tr, [role='row']").filter({ hasText: expectedPrefix }).filter({ visible: true });
  const count = await rows.count();
  if (count !== 1) throw new Error(`Expected exactly one download-center task row for ${expectedPrefix}; found ${count}.`);
  return rows;
}

async function taskRows(page: Page, expectedPrefix: string) {
  const rows = page.locator("tr, [role='row']").filter({ hasText: expectedPrefix }).filter({ visible: true });
  const result: Array<{ fingerprint: string; taskId?: string; createdAt?: string }> = [];
  for (let index = 0; index < await rows.count(); index += 1) {
    const row = rows.nth(index);
    const text = await row.innerText();
    const taskId = await row.getAttribute("data-task-id") ?? undefined;
    const fingerprint = jdProductDetailTaskFingerprint(text, expectedPrefix, taskId);
    if (!fingerprint) continue;
    result.push({ fingerprint, taskId, createdAt: text.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}/)?.[0] });
  }
  return result;
}

export type TaskBaselineSnapshot<T extends { fingerprint: string }> = {
  rows: T[];
  emptyConfirmed: boolean;
  // The download center may be populated exclusively by other date ranges.
  // That still proves its table has loaded even when this request has no
  // same-range historical rows.
  tableReady?: boolean;
};
export async function waitForStableTaskBaseline<T extends { fingerprint: string }>(
  readSnapshot: () => Promise<TaskBaselineSnapshot<T>>,
  sleep: (ms: number) => Promise<void>,
  // JD's download-center table can remain in a non-confirmed empty/loading
  // state for several seconds after navigation or refresh.  Keep the fast
  // path (two identical snapshots) but give that transient state enough
  // time to settle before refusing a safe submission.
  attempts = 40,
) {
  let previous: TaskBaselineSnapshot<T> | undefined;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const current = await readSnapshot();
    const currentKey = current.rows.map((row) => row.fingerprint).sort().join("\u0000");
    const previousKey = previous?.rows.map((row) => row.fingerprint).sort().join("\u0000");
    // Empty target-range rows are valid only when the table itself has loaded.
    // A true empty table still requires the UI's explicit empty-state signal.
    const ready = current.rows.length > 0
      || (current.emptyConfirmed && previous?.emptyConfirmed)
      || (current.tableReady && previous?.tableReady);
    if (previous && previousKey === currentKey && ready) return current.rows;
    previous = current;
    if (attempt + 1 < attempts) await sleep(500);
  }
  throw new Error("JD download-center task table did not reach a stable baseline; refusing to submit a new task.");
}

async function readReadyTaskBaseline(page: Page, expectedPrefix: string, options: Pick<CliOptions, "shopId" | "shopName">) {
  await page.goto(downloadCenterUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await readAndAssertJdProductDetailStoreIdentity(page, options);
  await waitForDataRefresh(page);
  const refresh = page.getByText("刷新", { exact: true }).filter({ visible: true }).first();
  if (await refresh.count().catch(() => 0) === 1) {
    await refresh.click().catch(() => undefined);
    await waitForDataRefresh(page);
  }
  return waitForStableTaskBaseline(async () => {
    const rows = await taskRows(page, expectedPrefix);
    const body = await page.locator("body").innerText().catch(() => "");
    const allVisibleTaskRows = page.locator("tr, [role='row']")
      .filter({ hasText: /\d{4}-\d{2}-\d{2}/ })
      .filter({ visible: true });
    return {
      rows,
      emptyConfirmed: /暂无数据|暂无记录|暂无下载记录/.test(body),
      tableReady: await allVisibleTaskRows.count() > 0,
    };
  }, (ms) => page.waitForTimeout(ms));
}

type DailyImportResult = { status: "imported" | "duplicate"; batchId: string; rowCount: number; warningCount: number; dateMin: string; dateMax: string; source: "jd_sku_daily"; dataset: "sku_daily" | "spu_daily"; platform: "京东"; shopName: string; batchStatus: "completed" };

export async function importJdProductDetailFile(options: Pick<CliOptions, "baseUrl" | "shopName" | "dimension" | "startDate" | "endDate">, savedPath: string, request: typeof fetch = fetch): Promise<DailyImportResult> {
  const form = new FormData();
  form.set("source", "jd_sku_daily");
  form.set("platform", "京东");
  form.set("shopName", options.shopName);
  form.set("expectedDataset", options.dimension === "SKU" ? "sku_daily" : "spu_daily");
  form.set("expectedStartDate", options.startDate);
  form.set("expectedEndDate", options.endDate);
  form.set("file", new File([await readFile(savedPath)], path.basename(savedPath), { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
  const response = await request(`${options.baseUrl}/api/netshop/import`, { method: "POST", body: form, signal: AbortSignal.timeout(120_000) });
  const payload = await response.json().catch(() => null) as {
    ok?: boolean; status?: "imported" | "duplicate"; message?: string;
    batch?: { id?: string; source?: string; dataset?: string; platform?: string; shopName?: string; status?: string; warningCount?: number; rowCount?: number; dateMin?: string; dateMax?: string };
  } | null;
  const expectedDataset = options.dimension === "SKU" ? "sku_daily" : "spu_daily";
  const batch = payload?.batch;
  const expectedHttpStatus = payload?.status === "imported" ? 201 : payload?.status === "duplicate" ? 200 : 0;
  if (response.status !== expectedHttpStatus || !payload?.ok || (payload.status !== "imported" && payload.status !== "duplicate")
    || batch?.dataset !== expectedDataset || batch.status !== "completed" || batch.warningCount !== 0
    || batch.source !== "jd_sku_daily" || batch.platform !== "京东" || batch.shopName !== options.shopName
    || batch.dateMin !== options.startDate || batch.dateMax !== options.endDate || !batch.id
    || !Number.isSafeInteger(batch.rowCount) || batch.rowCount! <= 0) {
    throw new Error(payload?.message ?? `JD ${options.dimension} daily import failed validation (HTTP ${response.status}).`);
  }
  return { status: payload.status, batchId: batch.id, rowCount: batch.rowCount!, warningCount: batch.warningCount, dateMin: batch.dateMin, dateMax: batch.dateMax, source: "jd_sku_daily", dataset: expectedDataset, platform: "京东", shopName: options.shopName, batchStatus: "completed" };
}

function emitPipelineResult(result: Record<string, unknown>) {
  console.log(`@@JD_PIPELINE_RESULT@@${JSON.stringify(result)}`);
  console.log(JSON.stringify(result, null, 2));
}

export function createSubmittingTaskManifest(
  options: Pick<CliOptions, "dimension" | "storeKey" | "shopId" | "shopName" | "startDate" | "endDate">,
  baseline: Array<{ fingerprint: string }>,
  now = new Date(),
): JdProductDetailTaskManifest {
  return { version: 2, status: "submitting", dimension: options.dimension, storeKey: options.storeKey, shopId: options.shopId, shopName: options.shopName, startDate: options.startDate, endDate: options.endDate, baseline: baseline.map((row) => row.fingerprint), createdAt: now.toISOString() };
}

async function waitForManifestTaskRow(
  page: Page,
  expectedPrefix: string,
  manifest: JdProductDetailTaskManifest,
  timeoutMs = 30_000,
) {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    const matched = selectManifestTaskRow(manifest, await taskRows(page, expectedPrefix));
    if (matched || Date.now() >= deadline) return matched;
    await page.waitForTimeout(500);
  }
}

async function taskRowByFingerprint(page: Page, expectedPrefix: string, fingerprint: string) {
  const candidates = await taskRows(page, expectedPrefix);
  const matching = candidates.filter((item) => item.fingerprint === fingerprint);
  if (matching.length !== 1) throw new Error(`Expected exactly one persisted JD SPU task row; found ${matching.length}.`);
  const rows = page.locator("tr, [role='row']").filter({ hasText: expectedPrefix }).filter({ visible: true });
  for (let index = 0; index < await rows.count(); index += 1) {
    const row = rows.nth(index);
    const text = await row.innerText();
    const taskId = await row.getAttribute("data-task-id") ?? undefined;
    if (jdProductDetailTaskFingerprint(text, expectedPrefix, taskId) === fingerprint) return row;
  }
  throw new Error("Persisted JD SPU task row disappeared while resolving it.");
}

async function waitForTaskDownload(page: Page, expectedPrefix: string, fingerprint?: string) {
  const deadline = Date.now() + 10 * 60_000;
  while (Date.now() < deadline) {
    let row: Awaited<ReturnType<typeof taskRow>> | null = null;
    try {
      row = fingerprint ? await taskRowByFingerprint(page, expectedPrefix, fingerprint) : await taskRow(page, expectedPrefix);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // A row may not have appeared yet, but ambiguity must stop immediately.
      if (!/found 0\./.test(message)) throw error;
    }
    if (row) {
      const rowText = await row.innerText().catch(() => "");
      const download = row.getByText("下载", { exact: true });
      if (
        rowText.includes("已生成")
        && await download.count().catch(() => 0) === 1
        && await download.isEnabled().catch(() => false)
      ) return;
    }
    const refresh = page.getByText("刷新", { exact: true }).filter({ visible: true }).first();
    if (await refresh.count().catch(() => 0)) await refresh.click().catch(() => undefined);
    await page.waitForTimeout(3_000);
  }
  throw new Error("京东下载中心任务在 10 分钟内未生成完成。");
}

async function clickTaskDownload(page: Page, expectedPrefix: string, fingerprint?: string) {
  const row = fingerprint ? await taskRowByFingerprint(page, expectedPrefix, fingerprint) : await taskRow(page, expectedPrefix);
  if (!(await row.innerText()).includes("已生成")) {
    throw new Error("最新同区间京东下载任务尚未生成完成，已禁止点击下载。");
  }
  const download = row.getByText("下载", { exact: true }).filter({ visible: true });
  await download.waitFor({ state: "visible", timeout: 10_000 });
  if (await download.count() !== 1) throw new Error(`Expected exactly one download button in the selected task row; found ${await download.count()}.`);
  if (!await download.isEnabled()) throw new Error("最新同区间京东下载任务的下载按钮尚不可用。");
  await download.click();
}

async function run() {
  const options = await parseArgs(process.argv.slice(2));
  const expectedPrefix = jdProductDetailDownloadPrefix(options);
  const manifestPath = taskManifestPath(options);
  await mkdir(options.downloadDirectory, { recursive: true });

  const recent = await findRecentJdProductDetailDownload({
    downloadDirectory: options.downloadDirectory,
    expectedPrefix,
    maxAgeMs: JD_PRODUCT_DETAIL_REUSE_WINDOW_MS,
    dimension: options.dimension,
    startDate: options.startDate,
    endDate: options.endDate,
  });
  if (recent) {
    const savedPath = await finalizeJdProductDetailDownload(recent, options.dimension, options);
    const importResult = options.autoImport ? await importJdProductDetailFile(options, savedPath) : undefined;
    await rm(manifestPath, { force: true });
    emitPipelineResult({ status: "reused", dimension: options.dimension, savedPath, importResult, batchId: importResult?.batchId, rowCount: importResult?.rowCount, downloadClicks: 0 });
    return;
  }

  if (options.debug) await mkdir(artifactDir, { recursive: true });
  let browser: Awaited<ReturnType<typeof connectPlaywrightBrowser>> | null = null;
  let ownsBrowser = false;
  let revealInteractiveBrowser = false;
  try {
    const launched = await launchDedicatedChrome({
      executablePath: options.executablePath,
      profileDirectory: options.userDataDirectory,
      profileName: options.profileName,
      port: options.port,
      startUrl: targetUrl,
      ...jdBrowserLaunchMode(options.interactiveLogin),
    });
    ownsBrowser = Boolean(launched);
    if (!options.interactiveLogin && !options.visibleRecovery && !ownsBrowser) {
      throw new Error("京东商智静默模式拒绝复用未受本次执行所有权控制的 Chromium 实例。");
    }
    await waitForChrome(options.port);
    browser = await connectPlaywrightBrowser(options.port);
    const { page, client } = await connectPlaywrightJackyunTarget(browser, {
      startUrl: targetUrl,
      workerName: "codex-jdsz-product-detail-worker",
      targetUrlPattern: /jdsz\.jd\.com/i,
    });
    await ensureJdStoreAuthenticatedSession(page, options);
    await client.send("Browser.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: options.downloadDirectory,
      eventsEnabled: true,
    }).catch(async () => client.send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: options.downloadDirectory,
    }));

    // 京东任务名不包含维度，SPU 与 SKU 同区间会重名。两种维度都只
    // 能接管本程序持久化了精确指纹的任务，不能按“最近同名行”猜测。
    let taskFingerprint: string | undefined;
    let taskReused = false;
    const manifest = await readJsonFileOr<JdProductDetailTaskManifest | null>(manifestPath, null);
    if (manifest) {
      assertJdProductDetailTaskManifest(manifest, options);
      await page.goto(downloadCenterUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
      await readAndAssertJdProductDetailStoreIdentity(page, options);
      const matched = await waitForManifestTaskRow(page, expectedPrefix, manifest);
      if (matched) {
        const rowText = await (await taskRowByFingerprint(page, expectedPrefix, matched.fingerprint)).innerText();
        if (rowText.includes("失败")) {
          await rm(manifestPath, { force: true });
        } else {
          taskFingerprint = matched.fingerprint;
          taskReused = true;
          if (!manifest.rowFingerprint) await writeJsonAtomic(manifestPath, { ...manifest, status: "pending", rowFingerprint: matched.fingerprint, taskId: matched.taskId });
        }
      } else {
        throw new Error("Persisted JD product-detail task is not uniquely visible yet; manifest retained and no replacement task was created.");
      }
    }
    let downloadPage = page;
    if (!taskReused) {
      const baseline = await readReadyTaskBaseline(page, expectedPrefix, options);
      let submitting: JdProductDetailTaskManifest | undefined;
      // Persist only after every selection/dialog gate has passed and directly
      // before the irreversible remote confirmation click.
      await prepareExport(page, options, async () => {
        await readAndAssertJdProductDetailStoreIdentity(page, options);
        submitting = createSubmittingTaskManifest(options, baseline);
        await writeJsonAtomic(manifestPath, submitting);
      });
      if (!submitting) throw new Error("JD submitting manifest was not persisted before confirmation click.");
      downloadPage = await openDownloadCenter(page);
      await readAndAssertJdProductDetailStoreIdentity(downloadPage, options);
      const created = await waitForManifestTaskRow(downloadPage, expectedPrefix, submitting);
      if (!created) throw new Error("Submitted JD product-detail task is not uniquely visible in download center; manifest retained and no replacement task will be created.");
      taskFingerprint = created.fingerprint;
      await writeJsonAtomic(manifestPath, { ...submitting, status: "pending", rowFingerprint: created.fingerprint, taskId: created.taskId });
    }
    await readAndAssertJdProductDetailStoreIdentity(downloadPage, options);
    await waitForTaskDownload(downloadPage, expectedPrefix, taskFingerprint);
    const result = await acquireJdProductDetailDownload({
      downloadDirectory: options.downloadDirectory,
      expectedPrefix,
      reuseWindowMs: JD_PRODUCT_DETAIL_REUSE_WINDOW_MS,
      initialWaitMs: 120_000,
      partialGraceMs: 300_000,
      maxRetries: 1,
      dimension: options.dimension,
      startDate: options.startDate,
      endDate: options.endDate,
      triggerDownload: async () => {
        await readAndAssertJdProductDetailStoreIdentity(downloadPage, options);
        await clickTaskDownload(downloadPage, expectedPrefix, taskFingerprint);
      },
    });
    const importResult = options.autoImport ? await importJdProductDetailFile(options, result.filePath) : undefined;
    await rm(manifestPath, { force: true });
    emitPipelineResult({
      status: result.reused ? "reused" : "downloaded",
      taskReused,
      dimension: options.dimension,
      savedPath: result.filePath,
      importResult,
      batchId: importResult?.batchId,
      rowCount: importResult?.rowCount,
      downloadClicks: result.downloadClicks,
    });
    client.close();
  } catch (error) {
    revealInteractiveBrowser = options.visibleRecovery && !options.interactiveLogin && isJdInteractiveBrowserFailure(error);
    throw error;
  } finally {
    await browser?.close().catch(() => undefined);
    if (ownsBrowser && !options.interactiveLogin) await closeChromeBrowser(options.port);
    if (revealInteractiveBrowser) {
      try {
        await revealJdBrowserForInteractiveFailure({
          executablePath: options.executablePath,
          profileDirectory: options.userDataDirectory,
          profileName: options.profileName,
          port: options.port,
          startUrl: targetUrl,
        });
        console.error(`京东交互异常：已打开 ${options.shopName} 对应的 Chromium profile，请完成人工验证后从原任务清单续跑。`);
      } catch (revealError) {
        const bounded = revealError instanceof Error ? revealError.message.slice(0, 500) : String(revealError).slice(0, 500);
        console.error(`京东交互异常，但可见 Chromium 打开失败：${bounded}`);
      }
    }
  }
}

async function main() { return withJdChromiumRunLock("product-detail", () => withJdProductDetailRunLock(run)); }

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.stack ?? error.message : String(error));
    process.exitCode = 1;
  });
}
