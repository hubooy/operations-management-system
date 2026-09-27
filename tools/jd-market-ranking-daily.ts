import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Frame, Locator, Page } from "playwright-core";

import { closeChromeBrowser, launchDedicatedChrome, waitForChrome } from "../lib/jackyun/cdp-client";
import { connectPlaywrightBrowser } from "../lib/jackyun/playwright-client";
import { readJsonFile, writeJsonAtomic } from "../lib/jackyun/json-file";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { getJdStore } from "../lib/jd/store-registry";
import { withJdChromiumRunLock } from "../lib/jd/chromium-run-lock";
import { assertJdProductDetailStoreIdentity, parseJdProductDetailStoreIdentity } from "../lib/jd/product-detail-store-identity";
import { ensureJdStoreAuthenticatedSession } from "./jd-saved-login";
import {
  isVerifiedJdDateRangeEcho,
  jdCalendarCellState,
  jdCalendarDateDispatchDecision,
  jdCalendarEndSelectionDecision,
  jdDateRangeSelectionPlan,
} from "../lib/jd/calendar-range-selection";
import {
  assertJdMarketImportProof,
  claimExactJdMarketPlan,
  claimRecoverableJdMarketPlan,
  inspectJdMarketSignedCsv,
  validateJdMarketImportResponse,
  type JdMarketImportProof,
  type JdMarketSignedFileEvidence,
} from "../lib/jd/market-ranking-import-contract";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const jdMarketRankingPageUrl = "https://jdsz.jd.com/szweb/view/industry/industry-top.html";
const targetUrl = jdMarketRankingPageUrl;
const outputRoot = path.join(projectRoot, "outputs", "jd-market-ranking-daily");
const configPath = path.join(projectRoot, "config", "jd-market-ranking-daily.json");
const lockPath = path.join(outputRoot, "run.lock");
const coverageRequestTimeoutMs = 120_000;
const importRequestTimeoutMs = 900_000;

export function isJdMarketRankingPageUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "jdsz.jd.com"
      && url.pathname === "/szweb/view/industry/industry-top.html"
      && !url.search && !url.hash;
  } catch {
    return false;
  }
}
export type JdMarketNativeDownloadRequest = Readonly<{
  contentType: string;
  method: string;
  postData: string;
  url: string;
  capturedAt: number;
}>;
const capturedNativeDownloadRequests = new WeakMap<Page, JdMarketNativeDownloadRequest>();

export type JdMarketDailyCategoryConfig = {
  key: string;
  categoryPath: [string, string];
  systemCategory: string;
  secondIndId: string;
  thirdIndId: string;
};

export type JdMarketDailyConfig = {
  version: 4;
  enabled: boolean;
  storeKey: string;
  silentNoWindow: true;
  dimension: "SKU";
  categories: JdMarketDailyCategoryConfig[];
  scope: string;
  priceBandFilter: string;
  earliestDate: string;
  requestDelayMs: number;
  maxDaysPerFile: number;
};

type JdMarketDailyChunk = {
  startDate: string;
  endDate: string;
  dates: string[];
  filePath?: string;
  fileHash?: string;
  fileSizeBytes?: number;
  batchId?: string;
  rowCount?: number;
  importProof?: JdMarketImportProof;
};

export type JdMarketDailyTargetPlan = {
  key: string;
  categoryPath: [string, string];
  identity: { category: string; scope: string; rankingDimension: "SKU"; priceBandFilter: string; secondIndId: string; thirdIndId: string };
  missingDates: string[];
  chunks: JdMarketDailyChunk[];
  screenshots?: { filters?: string; downloadReady?: string; imported?: string };
  evidenceWarnings?: string[];
};

export type JdMarketDailyPlan = {
  version: 4;
  runId: string;
  ownerExecutionId: string;
  createdAt: string;
  updatedAt: string;
  baseUrl: string;
  silentNoWindow: boolean;
  stage: "planned" | "running" | "executed" | "completed" | "failed";
  storeKey: string;
  shopId: string;
  shopName: string;
  browserProfileName: string;
  browserDebugPort: number;
  startDate: string;
  endDate: string;
  targets: JdMarketDailyTargetPlan[];
  failure?: { stage: string; message: string; at: string };
};

type Coverage = { ok: boolean; presentDates: string[]; missingDates: string[]; cutoffDate: string | null; rowCounts: Record<string, number> };

function validDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

function shanghaiYesterday(now = new Date()) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const date = new Date(`${today}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function inside(parent: string, child: string) {
  const relative = path.relative(parent, child);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function normalizeBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname) || (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("市场榜单工作流只允许访问本机运营系统根地址");
  }
  return url.toString().replace(/\/$/, "");
}

export function validateJdMarketDailyConfig(value: unknown): JdMarketDailyConfig {
  const config = value as Partial<JdMarketDailyConfig>;
  const categories = Array.isArray(config.categories) ? config.categories : [];
  const categoryKeys = categories.map((target) => target?.key);
  const systemCategories = categories.map((target) => target?.systemCategory);
  const categoryPaths = categories.map((target) => JSON.stringify(target?.categoryPath));
  const industryIds = categories.map((target) => `${target?.secondIndId}:${target?.thirdIndId}`);
  if (config.version !== 4 || !config.enabled || config.silentNoWindow !== true || config.dimension !== "SKU" || categories.length !== 7
    || categories.some((target) => !target || !/^[a-z0-9-]{1,80}$/.test(target.key)
      || !Array.isArray(target.categoryPath) || target.categoryPath.length !== 2 || !target.categoryPath.every(Boolean) || !target.systemCategory
      || !/^\d+$/.test(String(target.secondIndId ?? "")) || !/^\d+$/.test(String(target.thirdIndId ?? "")))
    || new Set(categoryKeys).size !== categories.length || new Set(systemCategories).size !== categories.length || new Set(categoryPaths).size !== categories.length
    || new Set(industryIds).size !== categories.length
    || !config.storeKey || config.scope !== "pop" || config.priceBandFilter !== "全部" || !validDate(String(config.earliestDate ?? ""))
    || !Number.isInteger(config.requestDelayMs) || Number(config.requestDelayMs) < 300 || Number(config.requestDelayMs) > 10_000
    || config.maxDaysPerFile !== 1) {
    throw new Error("京东市场商品榜单日补齐配置无效");
  }
  return config as JdMarketDailyConfig;
}

export async function loadJdMarketDailyConfig() {
  const value = validateJdMarketDailyConfig(await readJsonFile<unknown>(configPath));
  const store = await getJdStore(value.storeKey);
  if (!store.enabled) throw new Error("京东市场商品榜单受控店铺未启用");
  return value;
}

function coverageUrl(baseUrl: string, config: JdMarketDailyConfig, target: JdMarketDailyCategoryConfig, startDate: string, endDate: string) {
  const url = new URL("/api/market/daily-coverage", baseUrl);
  url.searchParams.set("category", target.systemCategory);
  url.searchParams.set("scope", config.scope);
  url.searchParams.set("rankingDimension", config.dimension);
  url.searchParams.set("priceBandFilter", config.priceBandFilter);
  url.searchParams.set("startDate", startDate);
  url.searchParams.set("endDate", endDate);
  return url;
}

async function readCoverage(baseUrl: string, config: JdMarketDailyConfig, target: JdMarketDailyCategoryConfig, startDate: string, endDate: string, request: typeof fetch = fetch) {
  const response = await request(coverageUrl(baseUrl, config, target, startDate, endDate), { headers: { accept: "application/json" }, signal: AbortSignal.timeout(coverageRequestTimeoutMs) });
  const body = await response.json().catch(() => null) as Coverage | { error?: string } | null;
  if (!response.ok || !body || !("missingDates" in body)) throw new Error(`读取市场日覆盖失败：${body && "error" in body ? body.error : `HTTP ${response.status}`}`);
  return body as Coverage;
}

function chunksOfMissingDates(dates: string[], maxDays: number, cutoffDate: string) {
  const chunks: JdMarketDailyChunk[] = [];
  const readyDates = dates.at(-1) === cutoffDate ? dates.slice(0, -1) : dates;
  for (let index = 0; index < readyDates.length; index += maxDays) {
    const chunk = readyDates.slice(index, index + maxDays);
    chunks.push({ startDate: chunk[0]!, endDate: chunk.at(-1)!, dates: chunk });
  }
  if (dates.at(-1) === cutoffDate) chunks.push({ startDate: cutoffDate, endDate: cutoffDate, dates: [cutoffDate] });
  return chunks;
}

function planFile(plan: Pick<JdMarketDailyPlan, "runId">) {
  const file = path.join(outputRoot, `plan-${plan.runId}.json`);
  if (!inside(outputRoot, file) || !/^plan-[A-Za-z0-9._-]+\.json$/.test(path.basename(file))) throw new Error("市场榜单计划路径无效");
  return file;
}

async function persistPlan(plan: JdMarketDailyPlan) {
  plan.updatedAt = new Date().toISOString();
  await mkdir(outputRoot, { recursive: true });
  await writeJsonAtomic(planFile(plan), plan);
}

async function saveEvidenceScreenshot(page: Page, plan: JdMarketDailyPlan, target: JdMarketDailyTargetPlan, name: "filters" | "downloadReady" | "imported") {
  const evidenceDirectory = path.join(outputRoot, plan.runId, "evidence");
  await mkdir(evidenceDirectory, { recursive: true });
  const filePath = path.join(evidenceDirectory, `${target.key}-${name}.png`);
  if (!inside(evidenceDirectory, filePath)) throw new Error("市场榜单截图证据路径无效");
  try {
    await page.screenshot({ path: filePath, fullPage: false, timeout: 30_000, animations: "disabled" });
    (target.screenshots ??= {})[name] = filePath;
  } catch (error) {
    await rm(filePath, { force: true });
    const message = error instanceof Error ? error.message.split("\n", 1)[0] : String(error);
    (target.evidenceWarnings ??= []).push(`${name}:${message.slice(0, 300)}`);
  }
  await persistPlan(plan);
  return target.screenshots?.[name] ?? null;
}

async function loadPersistedJdMarketPlans() {
  const entries = await readdir(outputRoot, { withFileTypes: true }).catch(() => []);
  const files = entries.filter((entry) => entry.isFile() && /^plan-[A-Za-z0-9._-]+\.json$/.test(entry.name));
  const plans: JdMarketDailyPlan[] = [];
  for (const entry of files) {
    const plan = await readJsonFile<unknown>(path.join(outputRoot, entry.name));
    if (!plan || typeof plan !== "object" || !Array.isArray((plan as Partial<JdMarketDailyPlan>).targets)) continue;
    plans.push(plan as JdMarketDailyPlan);
  }
  return plans;
}

function expectedJdMarketPlanIdentity(
  config: JdMarketDailyConfig,
  store: Awaited<ReturnType<typeof getJdStore>>,
  baseUrl: string,
  endDate: string,
) {
  return {
    version: 4,
    baseUrl,
    silentNoWindow: true,
    storeKey: store.storeKey,
    shopId: store.shopId,
    shopName: store.shopName,
    browserProfileName: store.browser.profileName,
    browserDebugPort: store.browser.debugPort,
    startDate: config.earliestDate,
    endDate,
    targets: config.categories.map((target) => ({
      key: target.key,
      categoryPath: target.categoryPath,
      identity: {
        category: target.systemCategory,
        scope: config.scope,
        rankingDimension: config.dimension,
        priceBandFilter: config.priceBandFilter,
        secondIndId: target.secondIndId,
        thirdIndId: target.thirdIndId,
      },
    })),
  } as const;
}

export async function planJdMarketDailyRun(options: {
  executionId: string;
  baseUrl?: string;
  now?: Date;
  request?: typeof fetch;
  runId?: string;
  resumeRunId?: string;
  silentNoWindow?: boolean;
}) {
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(options.executionId)) throw new Error("n8n execution ID 无效");
  const config = await loadJdMarketDailyConfig();
  const store = await getJdStore(config.storeKey);
  const baseUrl = normalizeBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const endDate = shanghaiYesterday(options.now);
  if (options.silentNoWindow === false) throw new Error("京东市场榜单计划必须使用隐藏 Chromium");
  return withRunLock(`jd-market-plan-${randomUUID()}`, async () => {
    const identity = expectedJdMarketPlanIdentity(config, store, baseUrl, endDate);
    const persistedPlans = await loadPersistedJdMarketPlans();
    const recovered = options.resumeRunId
      ? claimExactJdMarketPlan(persistedPlans, identity, options.executionId, options.resumeRunId)
      : claimRecoverableJdMarketPlan(persistedPlans, identity, options.executionId);
    if (recovered) {
      await persistPlan(recovered);
      return recovered;
    }
    const runId = options.runId ?? `jd-market-${randomUUID()}`;
    const targets: JdMarketDailyTargetPlan[] = [];
    for (const target of config.categories) {
      const coverage = await readCoverage(baseUrl, config, target, config.earliestDate, endDate, options.request);
      targets.push({
        key: target.key,
        categoryPath: target.categoryPath,
        identity: { category: target.systemCategory, scope: config.scope, rankingDimension: "SKU", priceBandFilter: config.priceBandFilter,
          secondIndId: target.secondIndId, thirdIndId: target.thirdIndId },
        missingDates: coverage.missingDates,
        chunks: chunksOfMissingDates(coverage.missingDates, config.maxDaysPerFile, endDate),
      });
    }
    const plan: JdMarketDailyPlan = {
      version: 4, runId, ownerExecutionId: options.executionId, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      baseUrl, silentNoWindow: true, stage: "planned",
      storeKey: store.storeKey, shopId: store.shopId, shopName: store.shopName,
      browserProfileName: store.browser.profileName, browserDebugPort: store.browser.debugPort,
      startDate: config.earliestDate, endDate,
      targets,
    };
    await persistPlan(plan);
    return plan;
  });
}

export function publicJdMarketPlan(plan: JdMarketDailyPlan) {
  return {
    ok: true, stage: "plan", runId: plan.runId, silentNoWindow: plan.silentNoWindow, startDate: plan.startDate, endDate: plan.endDate,
    targetCount: plan.targets.length,
    pendingTargetCount: plan.targets.filter((target) => target.chunks.length > 0).length,
    missingDateCount: plan.targets.reduce((sum, target) => sum + target.missingDates.length, 0),
    chunkCount: plan.targets.reduce((sum, target) => sum + target.chunks.length, 0),
    categories: plan.targets.map((target) => ({ key: target.key, category: target.identity.category, missingDateCount: target.missingDates.length, chunkCount: target.chunks.length })),
  };
}

async function assertStoreIdentity(page: Page, expected: { shopId: string; shopName: string }) {
  const links = page.locator('a[href*="mall.jd.com/index-"]').filter({ visible: true });
  await links.first().waitFor({ state: "visible", timeout: 30_000 });
  const candidates: Array<{ href: string | null; text: string }> = [];
  for (let index = 0; index < await links.count(); index += 1) {
    const link = links.nth(index);
    candidates.push({ href: await link.getAttribute("href"), text: await link.innerText() });
  }
  return assertJdProductDetailStoreIdentity(parseJdProductDetailStoreIdentity(candidates), expected);
}

async function installRequestCapture(page: Page) {
  page.on("request", (request) => {
    if (/\/api\/lowcode\/industryTop\/indProductRank\/downloadProductRank\.ajax(?:\?|$)/.test(request.url())) {
      capturedNativeDownloadRequests.set(page, Object.freeze({
        contentType: request.headers()["content-type"] ?? "",
        method: request.method(),
        postData: request.postData() ?? "",
        url: request.url(),
        capturedAt: Date.now(),
      }));
    }
  });
}

function nativeDownloadPayload(request: Pick<JdMarketNativeDownloadRequest, "contentType" | "postData" | "url">) {
  const result: Record<string, unknown> = {};
  const url = new URL(request.url, targetUrl);
  for (const key of new Set([...url.searchParams.keys()])) {
    const values = url.searchParams.getAll(key);
    result[key] = values.length === 1 ? values[0] : values;
  }
  const source = request.postData.trim();
  if (!source) return result;
  if (/json/i.test(request.contentType) || source.startsWith("{") || source.startsWith("[")) {
    const parsed = JSON.parse(source) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("京东行业榜单下载请求体不是对象");
    return { ...result, ...parsed as Record<string, unknown> };
  }
  const form = new URLSearchParams(source);
  for (const key of new Set([...form.keys()])) {
    const values = form.getAll(key);
    result[key] = values.length === 1 ? values[0] : values;
  }
  return result;
}

function flattenedStrings(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(flattenedStrings);
  if (value === null || value === undefined || value === "") return [];
  if (typeof value === "string" && value.trim().startsWith("[")) {
    try { return flattenedStrings(JSON.parse(value)); } catch { /* Treat it as a scalar below. */ }
  }
  return [String(value)];
}

export function assertJdMarketNativeDownloadRequest(
  request: JdMarketNativeDownloadRequest,
  target: Pick<JdMarketDailyCategoryConfig, "secondIndId" | "thirdIndId">,
  date: string,
) {
  const url = new URL(request.url, targetUrl);
  if (request.method !== "POST" || url.protocol !== "https:" || url.hostname !== "szgateway.jd.com"
    || url.pathname !== "/api/lowcode/industryTop/indProductRank/downloadProductRank.ajax") {
    throw new Error("京东行业榜单下载请求地址或方法已变化");
  }
  const payload = nativeDownloadPayload(request);
  const scalar = (key: string) => flattenedStrings(payload[key]);
  const exact = (key: string, expected: string) => scalar(key).length === 1 && scalar(key)[0] === expected;
  if (!exact("skuSpuType", "sku") || !exact("rankTab", "hot")
    || !exact("startDate", date) || !exact("endDate", date)
    || !exact("saleOrdCate3", target.thirdIndId)
    || (scalar("saleOrdCate2").length > 0 && !exact("saleOrdCate2", target.secondIndId))
    || payload.realtime !== false || !exact("interval", "DAY")
    || !exact("dateType", "custom") || !exact("channel", "all")) {
    throw new Error("京东行业榜单下载请求的商品榜、热销排名、SKU、类目或日期身份不一致");
  }
  if (!exact("popBusiness", "pop")) {
    throw new Error("京东行业榜单下载请求不是 POP 经营模式");
  }
  return Object.freeze({ payload });
}

export function jdMarketDropdownClickMode(input: { hitInsideControl: boolean; hitTagNames: string[]; hitClassNames?: string[] }) {
  const hitIsJdMenuList = input.hitTagNames.some((tagName, index) => tagName.toUpperCase() === "UL"
    && String(input.hitClassNames?.[index] ?? "").split(/\s+/).includes("menu-list"));
  const hitIsJdAiHelper = input.hitTagNames.some((tagName) => /^AIHELPER-/i.test(tagName))
    || input.hitClassNames?.some((className) => String(className).split(/\s+/)
      .some((token) => /^AiHelperOpenExtension(?:-|$)/i.test(token)));
  return !input.hitInsideControl && (hitIsJdAiHelper || hitIsJdMenuList)
    ? "native_dispatch" as const
    : "pointer" as const;
}

async function readDropdownHitTest(locator: Locator) {
  return locator.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    const hitTagNames: string[] = [];
    const hitClassNames: string[] = [];
    let current = hit as HTMLElement | null;
    for (let depth = 0; current && current !== document.body && depth < 8; depth += 1) {
      hitTagNames.push(current.tagName);
      hitClassNames.push(String(current.className));
      current = current.parentElement;
    }
    return { hitInsideControl: hit === element || Boolean(hit && element.contains(hit)), hitTagNames, hitClassNames };
  });
}

async function clickDropdownControl(control: Locator) {
  const count = await control.count();
  const className = count === 1 ? String(await control.getAttribute("class") ?? "") : "";
  const eventName = count === 1 ? String(await control.getAttribute("data-event-name") ?? "") : "";
  if (count !== 1 || !className.split(/\s+/).includes("jmt-selector") || eventName !== "open") {
    throw new Error("京东商品榜单下拉控件真实触发层不唯一或契约已变化");
  }
  const hitTest = await readDropdownHitTest(control);
  if (jdMarketDropdownClickMode(hitTest) === "native_dispatch") {
    // 京东 AI 助手或京东自身的顶层 UL.menu-list 偶尔覆盖类目控件并吞掉
    // 坐标点击。控件已经通过唯一性、组件类型和 data-event-name 契约校验；
    // 仅对这两种已知遮挡派发原生 click，不修改或关闭平台 DOM。
    await control.dispatchEvent("click");
    return;
  }
  await control.click({ timeout: 3_000, force: true });
}

async function clickUniqueCategoryOption(option: Locator, label: string) {
  const className = String(await option.getAttribute("class") ?? "");
  if (!className.split(/\s+/).includes("jmt-dropdown-option") || (await option.innerText()).trim() !== label) {
    throw new Error("京东新版三级类目选项契约已变化");
  }
  if (jdMarketDropdownClickMode(await readDropdownHitTest(option)) === "native_dispatch") {
    await option.dispatchEvent("click");
    return;
  }
  try {
    await option.click({ timeout: 5_000 });
  } catch (error) {
    if (jdMarketDropdownClickMode(await readDropdownHitTest(option)) !== "native_dispatch") throw error;
    await option.dispatchEvent("click");
  }
}

async function selectUniqueCategoryPath(surface: Locator, frame: Frame, control: Locator, categoryPath: [string, string]) {
  await clickDropdownControl(control);
  const panel = surface.locator(".jmt-cascader-panel").filter({ visible: true });
  await panel.waitFor({ state: "visible", timeout: 5_000 });
  if (await panel.count() !== 1) throw new Error("京东新版类目菜单不唯一");
  const option = (label: string) => panel.locator(".jmt-dropdown-option")
    .filter({ has: frame.getByText(label, { exact: true }) });
  const parent = option(categoryPath[0]);
  if (await parent.count() !== 1) throw new Error("京东新版二级类目不唯一");
  await parent.hover({ timeout: 5_000 });
  const child = option(categoryPath[1]);
  await child.waitFor({ state: "visible", timeout: 5_000 });
  if (await child.count() !== 1) throw new Error("京东新版三级类目不唯一");
  await child.scrollIntoViewIfNeeded();
  await clickUniqueCategoryOption(child, categoryPath[1]);
}

async function waitForSelectorText(control: Locator, frame: Frame, expected: string, exact: boolean) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const actual = (await control.innerText().catch(() => "")).trim();
    if (exact ? actual === expected : actual.includes(expected)) return;
    await frame.waitForTimeout(100);
  }
  throw new Error(`京东商品榜单筛选未生效：${expected}`);
}

export async function withSingleJdMarketFilterSelectionRetry(
  select: () => Promise<void>,
  verify: () => Promise<void>,
) {
  let firstError: unknown;
  for (let selectionAttempt = 0; selectionAttempt < 2; selectionAttempt += 1) {
    try {
      await select();
      await verify();
      return { retried: selectionAttempt === 1 } as const;
    } catch (error) {
      if (selectionAttempt === 0) {
        firstError = error;
        continue;
      }
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(`京东商品榜单筛选连续两次未精确生效：${message}`, { cause: firstError });
    }
  }
  throw new Error("京东商品榜单筛选重试状态异常");
}

async function waitForRankingSurface(frame: Frame) {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const surfaces = frame.locator("#jdsz-container").filter({ visible: true });
    const title = frame.getByText("行业榜单", { exact: true }).filter({ visible: true });
    if (await surfaces.count() === 1 && await title.count() >= 1) return surfaces.first();
    await frame.waitForTimeout(100);
  }
  throw new Error("京东新版行业榜单受控业务容器未就绪或不唯一");
}

async function waitForRankingIdentityControls(surface: Locator, frame: Frame) {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const filterContent = surface.locator(".industry-top-head-filter-content").filter({ visible: true });
    const dimensionArea = filterContent.locator(".industry-top-head-filter-sku-spu").filter({ visible: true });
    const dimensionControl = dimensionArea.getByText("SKU", { exact: true }).filter({ visible: true });
    const categoryControl = filterContent.locator('.jmt-selector[data-component-name="Selector"][data-event-name="open"]')
      .filter({ visible: true }).filter({ hasText: /商用/ });
    if (await filterContent.count() === 1 && await dimensionArea.count() === 1
      && await dimensionControl.count() === 1 && await categoryControl.count() === 1) {
      return { dimensionControl, categoryControl };
    }
    await frame.waitForTimeout(100);
  }
  throw new Error("京东新版行业榜单商品榜的 SKU 或商用类目控件未在有界时间内唯一稳定");
}

export async function dismissRankingNotice(page: Page, waitMs = 5_000) {
  const notice = page.locator(".jd-modal-wrap")
    .filter({ has: page.locator('img[alt="公告图片"]') }).filter({ visible: true });
  await notice.first().waitFor({ state: "visible", timeout: waitMs }).catch(() => undefined);
  if (await notice.count() === 0) return;
  if (await notice.count() !== 1) throw new Error("京东行业榜单公告不唯一");
  const close = notice.locator('button[aria-label="Close"], .close-modal').filter({ visible: true });
  if (await close.count() !== 1) throw new Error("京东行业榜单公告关闭按钮不唯一");
  await close.click({ timeout: 3_000 });
  await notice.waitFor({ state: "hidden", timeout: 5_000 });
}

export async function selectRankingIdentity(page: Page, target: JdMarketDailyCategoryConfig) {
  const frame = page.mainFrame();
  if (!isJdMarketRankingPageUrl(frame.url())) throw new Error("京东新版行业榜单主框架地址无效");
  const surface = await waitForRankingSurface(frame);
  const productTab = surface.getByText("商品榜", { exact: true }).filter({ visible: true });
  await productTab.waitFor({ state: "visible", timeout: 30_000 });
  if (await productTab.count() !== 1) throw new Error("京东新版行业榜单无法唯一识别商品榜入口");
  try {
    await productTab.click({ timeout: 3_000 });
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes("公告图片")) throw error;
    await dismissRankingNotice(page, 1_000);
    await productTab.click({ timeout: 3_000 });
  }
  const { dimensionControl, categoryControl } = await waitForRankingIdentityControls(surface, frame);
  const categoryLabel = target.categoryPath.join(" > ");
  const currentCategory = (await categoryControl.innerText()).trim();
  if (!currentCategory.includes(categoryLabel)) {
    await selectUniqueCategoryPath(frame.locator("body"), frame, categoryControl, target.categoryPath);
    await waitForSelectorText(categoryControl, frame, categoryLabel, false);
  }
  await dimensionControl.click();
  await frame.waitForTimeout(1_000);
  if (await dimensionControl.getAttribute("aria-selected") !== "true" || !(await categoryControl.innerText()).includes(categoryLabel)) throw new Error("京东商品榜单 SKU 或类目选择未精确生效");
  const hotRanking = surface.getByText("热销排名", { exact: true }).filter({ visible: true });
  await hotRanking.waitFor({ state: "visible", timeout: 30_000 });
  if (await hotRanking.count() !== 1) throw new Error("京东新版行业榜单无法唯一识别热销排名");
  await hotRanking.click();
  const query = surface.getByText("查询", { exact: true }).filter({ visible: true });
  if (await query.count() !== 1) throw new Error("京东新版行业榜单查询按钮不唯一");
  await query.click();
  await frame.waitForTimeout(500);
  const downloadButton = surface.getByText("下载数据", { exact: true }).filter({ visible: true });
  await downloadButton.waitFor({ state: "visible", timeout: 30_000 });
  if (await downloadButton.count() !== 1) throw new Error("京东新版行业榜单无法唯一识别下载数据按钮");
  return Object.freeze({ frame, surface, dimensionControl, categoryControl, query, downloadButton });
}

async function currentRankingDateEcho(frame: Frame) {
  const echo = frame.locator(".jmt-combo-date-picker-echo-wrap").filter({ visible: true });
  if (await echo.count() !== 1) throw new Error("无法唯一识别京东新版行业榜单当前日期显示区域");
  return echo.innerText();
}

async function waitForRankingDateEcho(frame: Frame, date: string, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let observed = "";
  while (Date.now() < deadline) {
    observed = await currentRankingDateEcho(frame);
    if (isVerifiedJdDateRangeEcho(observed, date, date)) return observed;
    await frame.waitForTimeout(100);
  }
  throw new Error(`京东新版行业榜单自定义日期未生效：目标 ${date}，页面显示 ${observed.replace(/\s+/g, " ")}`);
}

export async function selectRankingDate(frame: Frame, date: string) {
  const echo = frame.locator(".jmt-combo-date-picker-echo-wrap").filter({ visible: true });
  if (await echo.count() !== 1) throw new Error("无法唯一识别京东新版行业榜单当前时间入口");
  const customSelector = '[data-event-content="当前时间_自定义"]';
  if (await frame.locator(customSelector).filter({ visible: true }).count() === 0) {
    await echo.click();
    await frame.waitForTimeout(200);
  }
  const custom = frame.locator(customSelector).filter({ visible: true });
  if (await custom.count() !== 1) throw new Error("无法唯一识别京东新版行业榜单自定义时间入口");
  await custom.click();
  await frame.waitForTimeout(300);

  const cellSelector = `td[data-event-content="当前时间自定义_${date}"]`;
  const popup = frame.locator(".jmt-date-picker-dropdown-wrapper").filter({ visible: true });
  if (await popup.count() !== 1) throw new Error("京东新版自定义日期面板不唯一");
  // The range picker shows two months; a unique visible target day is sufficient.
  // Navigate only when neither month contains that exact date.
  const deadline = Date.now() + 15_000;
  while (await frame.locator(cellSelector).filter({ visible: true }).count() === 0) {
    if (Date.now() >= deadline) throw new Error("京东新版行业榜单日历月份切换超时");
    const headers = await popup.locator(".jmt-date-picker-header-date-content").allTextContents();
    const months = headers.map(text => {
      const m = text.match(/(\d{4})年\s*(\d{1,2})月/);
      return m ? m[1] + "-" + m[2]!.padStart(2, "0") : "";
    }).filter(Boolean).sort();
    if (months.length !== 2) throw new Error("京东新版行业榜单日历月份无法识别");
    const direction = date.slice(0, 7) < months[0]! ? "prev" : "next";
    const nav = popup.locator(".jmt-date-picker-header-" + direction + "-month-icon")
      .filter({ visible: true }).filter({ hasNot: frame.locator("[disabled]") });
    const enabled: Locator[] = [];
    for (let i = 0; i < await nav.count(); i++) {
      if (!(await nav.nth(i).getAttribute("class") ?? "").includes("btn-disabled")) enabled.push(nav.nth(i));
    }
    if (enabled.length !== 1) throw new Error("京东新版行业榜单日历月份切换按钮不唯一");
    await enabled[0]!.click();
    await frame.waitForTimeout(200);
  }

  const cell = frame.locator(cellSelector).filter({ visible: true });
  await cell.waitFor({ state: "visible", timeout: 10_000 });
  if (await cell.count() !== 1) throw new Error(`京东新版行业榜单日期 ${date} 的可选单元格不唯一`);
  if (jdCalendarDateDispatchDecision(await cell.getAttribute("class")) === "blocked_disabled") {
    throw new Error(`京东新版行业榜单日期 ${date} 尚未开放`);
  }
  const [startDate, endDate] = jdDateRangeSelectionPlan(date, date);
  await cell.dispatchEvent("click");
  const waitForStart = async (timeoutMs: number) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const state = jdCalendarCellState(await cell.getAttribute("class").catch(() => ""));
      if (!state.disabled && state.start && state.selected) return state;
      await frame.waitForTimeout(50);
    }
    return jdCalendarCellState(await cell.getAttribute("class").catch(() => ""));
  };
  let startState = await waitForStart(1_000);
  if (!startState.disabled && (!startState.start || !startState.selected)) {
    await cell.dispatchEvent("click");
    startState = await waitForStart(5_000);
  }
  if (startDate !== date || !startState.start || !startState.selected || startState.disabled) {
    throw new Error(`京东新版行业榜单起始日期 ${date} 未进入已选状态`);
  }
  await cell.dispatchEvent("click");
  const endDeadline = Date.now() + 5_000;
  let endDecision = "unconfirmed" as ReturnType<typeof jdCalendarEndSelectionDecision>;
  while (Date.now() < endDeadline) {
    endDecision = jdCalendarEndSelectionDecision({
      className: await cell.getAttribute("class").catch(() => ""),
      echoText: await currentRankingDateEcho(frame).catch(() => ""),
      startDate,
      endDate,
    });
    if (endDecision === "confirmed_echo" || endDecision === "blocked_disabled") break;
    await frame.waitForTimeout(50);
  }
  if (endDecision !== "confirmed_echo") throw new Error(`京东新版行业榜单结束日期 ${date} 未获得严格日期回显`);
  const confirm = frame.locator('[data-event-name="confirm"][data-event-content="true"]').filter({ visible: true });
  const confirmCount = await confirm.count();
  if (confirmCount > 1) throw new Error("京东新版行业榜单日期确认按钮不唯一");
  if (confirmCount === 1) await confirm.click();
  // Current industry picker applies the two endpoint clicks immediately.
  // Only the strict date echo authorizes download when no confirmation exists.
  await frame.getByRole("heading", { name: "行业榜单", exact: true }).hover();
  const closeDeadline = Date.now() + 5_000;
  while (await popup.count() > 0 && Date.now() < closeDeadline) await frame.waitForTimeout(100);
  if (await popup.count() > 0) throw new Error("京东新版日期浮层尚未关闭");
  await frame.waitForTimeout(200);
  await waitForRankingDateEcho(frame, date);
  const loading = frame.locator(".jd-spin-spinning, .jmt-spin-spinning, [aria-busy='true']").filter({ visible: true });
  const loadingDeadline = Date.now() + 30_000;
  while (Date.now() < loadingDeadline && await loading.count() > 0) await frame.waitForTimeout(200);
  if (await loading.count() > 0) throw new Error("京东新版行业榜单日期切换后仍在加载");
}

async function downloadRankingWorkbook(
  page: Page,
  identity: Awaited<ReturnType<typeof selectRankingIdentity>>,
  target: JdMarketDailyCategoryConfig,
  chunk: JdMarketDailyChunk,
  runDirectory: string,
) {
  if (chunk.startDate !== chunk.endDate || chunk.dates.length !== 1 || chunk.dates[0] !== chunk.startDate) {
    throw new Error("京东新版行业榜单原生下载只允许单个自然日分块");
  }
  const date = chunk.startDate;
  await selectRankingDate(identity.frame, date);
  await waitForSelectorText(identity.dimensionControl, identity.frame, "SKU", true);
  await waitForSelectorText(identity.categoryControl, identity.frame, target.categoryPath.join(" > "), false);
  if (await identity.dimensionControl.getAttribute("aria-selected") !== "true") {
    throw new Error("京东商品榜单选择日期后 SKU 维度未保持选中");
  }
  // The date picker can update its echo without applying the new day to the ranking.
  await identity.query.click();
  await identity.frame.waitForTimeout(500);
  const loading = identity.frame.locator(".jd-spin-spinning, .jmt-spin-spinning, [aria-busy='true']").filter({ visible: true });
  const loadingDeadline = Date.now() + 30_000;
  while (Date.now() < loadingDeadline && await loading.count() > 0) await identity.frame.waitForTimeout(200);
  if (await loading.count() > 0) throw new Error("京东新版行业榜单目标日期查询后仍在加载");
  await waitForRankingDateEcho(identity.frame, date);
  const empty = identity.surface.getByText("暂无数据", { exact: true }).filter({ visible: true });
  if (await empty.count() > 0) throw new Error(`京东新版行业榜单 ${date} 的 SKU 查询结果暂无数据`);
  capturedNativeDownloadRequests.delete(page);
  const startedAt = Date.now();
  const downloadPromise = page.waitForEvent("download", { timeout: 180_000 });
  await identity.downloadButton.click();
  const download = await downloadPromise;
  const nativeRequest = capturedNativeDownloadRequests.get(page);
  if (!nativeRequest || nativeRequest.capturedAt < startedAt) throw new Error("未捕获到京东新版行业榜单原生下载请求");
  assertJdMarketNativeDownloadRequest(nativeRequest, target, date);
  const suggestedName = download.suggestedFilename();
  if (!/\.xlsx$/i.test(suggestedName)) throw new Error(`京东新版行业榜单下载文件不是 XLSX：${suggestedName.slice(0, 120)}`);
  const filePath = path.join(runDirectory, canonicalChunkFileName(target, chunk));
  if (!inside(runDirectory, filePath)) throw new Error("市场榜单下载文件路径越界");
  await download.saveAs(filePath);
  const failure = await download.failure();
  if (failure) throw new Error(`京东新版行业榜单 XLSX 下载失败：${failure.slice(0, 240)}`);
  const bytes = new Uint8Array(await readFile(filePath));
  if (bytes.byteLength < 150 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    throw new Error("京东新版行业榜单下载文件不是有效的 XLSX 容器");
  }
  return { filePath, bytes };
}

function canonicalChunkFileName(target: JdMarketDailyCategoryConfig, chunk: JdMarketDailyChunk) {
  return `京东商智_行业榜单_商品榜_SKU_${target.systemCategory}_${chunk.startDate}至${chunk.endDate}.xlsx`;
}

async function inspectSignedChunk(
  plan: JdMarketDailyPlan,
  config: JdMarketDailyConfig,
  target: JdMarketDailyCategoryConfig,
  chunk: JdMarketDailyChunk,
) {
  const runDirectory = path.join(outputRoot, plan.runId);
  const expectedPath = path.join(runDirectory, canonicalChunkFileName(target, chunk));
  const evidenceFields = [chunk.filePath, chunk.fileHash, chunk.fileSizeBytes];
  if (evidenceFields.some((value) => value !== undefined) && evidenceFields.some((value) => value === undefined)) {
    throw new Error("市场榜单计划包含不完整的签收文件证据");
  }
  if (!chunk.filePath || !chunk.fileHash || !chunk.fileSizeBytes || path.resolve(chunk.filePath) !== path.resolve(expectedPath)
    || !inside(runDirectory, chunk.filePath)) {
    throw new Error("市场榜单签收文件路径或规范文件名与计划不一致");
  }
  const info = await stat(chunk.filePath);
  if (!info.isFile()) throw new Error("市场榜单签收文件不是普通文件");
  const bytes = new Uint8Array(await readFile(chunk.filePath));
  const evidence = inspectJdMarketSignedCsv({
    bytes,
    fileName: path.basename(chunk.filePath),
    expectedFileSizeBytes: chunk.fileSizeBytes,
    expectedRawFileSha256: chunk.fileHash,
    dates: chunk.dates,
    identity: {
      category: target.systemCategory,
      scope: config.scope,
      rankingDimension: config.dimension,
      priceBandFilter: config.priceBandFilter,
    },
  });
  return { bytes, evidence };
}

async function importRankingFile(
  plan: JdMarketDailyPlan,
  config: JdMarketDailyConfig,
  target: JdMarketDailyCategoryConfig,
  chunk: JdMarketDailyChunk,
  bytes: Uint8Array,
  evidence: JdMarketSignedFileEvidence,
  request: typeof fetch = fetch,
) {
  const form = new FormData();
  const uploadBytes = new Uint8Array(bytes.byteLength);
  uploadBytes.set(bytes);
  form.set("file", new Blob([uploadBytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), evidence.fileName);
  form.set("sourceType", "market_ranking");
  form.set("periodStart", chunk.startDate);
  form.set("periodEnd", chunk.endDate);
  form.set("category", target.systemCategory);
  form.set("scope", config.scope);
  form.set("priceBandFilter", config.priceBandFilter);
  const response = await request(`${plan.baseUrl}/api/market/import`, { method: "POST", body: form, signal: AbortSignal.timeout(importRequestTimeoutMs) });
  const body = await response.json().catch(() => null);
  return validateJdMarketImportResponse(response.status, body, evidence);
}

async function withRunLock<T>(runId: string, task: () => Promise<T>) {
  await mkdir(outputRoot, { recursive: true });
  return withJackyunRunLock({
    runId,
    purpose: "jd-market-ranking-daily",
    lockDirectory: lockPath,
  }, task);
}

export async function runJdMarketDailyPlan(plan: JdMarketDailyPlan, options: { request?: typeof fetch } = {}) {
  return withJdChromiumRunLock("market-ranking", () => withRunLock(plan.runId, async () => {
    const config = await loadJdMarketDailyConfig();
    const store = await getJdStore(plan.storeKey);
    if (plan.stage === "executed" || plan.stage === "completed") return { ok: true, stage: "run", verificationOnly: true, runId: plan.runId };
    if (plan.stage !== "planned" || store.shopId !== plan.shopId || store.shopName !== plan.shopName
      || store.browser.profileName !== plan.browserProfileName || store.browser.debugPort !== plan.browserDebugPort) {
      throw new Error("市场榜单计划状态、店铺身份或 Chromium profile 无效");
    }
    const configuredTargets = config.categories.map((target) => ({
      key: target.key, categoryPath: target.categoryPath, systemCategory: target.systemCategory,
      secondIndId: target.secondIndId, thirdIndId: target.thirdIndId,
      scope: config.scope, rankingDimension: config.dimension, priceBandFilter: config.priceBandFilter,
    }));
    const plannedTargets = plan.targets.map((target) => ({
      key: target.key, categoryPath: target.categoryPath, systemCategory: target.identity.category,
      secondIndId: target.identity.secondIndId, thirdIndId: target.identity.thirdIndId,
      scope: target.identity.scope, rankingDimension: target.identity.rankingDimension, priceBandFilter: target.identity.priceBandFilter,
    }));
    if (plan.version !== 4 || !plan.silentNoWindow || JSON.stringify(configuredTargets) !== JSON.stringify(plannedTargets)) {
      throw new Error("市场榜单计划类目清单或隐藏 Chromium 约束与当前受控配置不一致");
    }
    const totalChunks = plan.targets.reduce((sum, target) => sum + target.chunks.length, 0);
    if (!totalChunks) { plan.stage = "executed"; await persistPlan(plan); return { ok: true, stage: "run", runId: plan.runId, importedFiles: 0 }; }
    plan.stage = "running"; await persistPlan(plan);
    let browser: Awaited<ReturnType<typeof connectPlaywrightBrowser>> | null = null;
    let ownsBrowser = false;
    let activeTargetPlan: JdMarketDailyTargetPlan | null = null;
    let activePage: Page | null = null;
    try {
      const runDirectory = path.join(outputRoot, plan.runId);
      await mkdir(runDirectory, { recursive: true });
      for (const targetPlan of plan.targets) {
        const target = config.categories.find((candidate) => candidate.key === targetPlan.key);
        if (!target) throw new Error(`市场榜单受控类目不存在：${targetPlan.key}`);
        for (const chunk of targetPlan.chunks) {
          const hasSignedEvidence = chunk.filePath !== undefined || chunk.fileHash !== undefined || chunk.fileSizeBytes !== undefined;
          if (!chunk.importProof && !chunk.batchId && !hasSignedEvidence) continue;
          const { bytes, evidence } = await inspectSignedChunk(plan, config, target, chunk);
          if (chunk.importProof) {
            assertJdMarketImportProof(chunk.importProof, evidence);
            if (chunk.batchId !== chunk.importProof.batchId || chunk.rowCount !== chunk.importProof.rowCount) {
              throw new Error("市场榜单计划批次摘要与严格导入证明不一致");
            }
            continue;
          }
          const proof = await importRankingFile(plan, config, target, chunk, bytes, evidence, options.request);
          chunk.importProof = proof;
          chunk.batchId = proof.batchId;
          chunk.rowCount = proof.rowCount;
          await persistPlan(plan);
        }
      }
      if (plan.targets.every((target) => target.chunks.every((chunk) => Boolean(chunk.importProof)))) {
        plan.stage = "executed";
        delete plan.failure;
        await persistPlan(plan);
        return {
          ok: true, stage: "run", runId: plan.runId, recoveredWithoutBrowser: true,
          importedFiles: totalChunks,
          rowCount: plan.targets.reduce((sum, target) => sum + target.chunks.reduce((targetSum, chunk) => targetSum + Number(chunk.rowCount ?? 0), 0), 0),
        };
      }
      const launched = await launchDedicatedChrome({
        executablePath: store.browser.executablePath,
        profileDirectory: store.browser.userDataDir,
        profileName: store.browser.profileName,
        port: store.browser.debugPort, startUrl: "about:blank",
        headless: false, visible: false, startMinimized: true,
        keepWindowHidden: plan.silentNoWindow,
      });
      ownsBrowser = Boolean(launched);
      if (plan.silentNoWindow && !ownsBrowser) throw new Error("京东市场榜单静默模式拒绝复用未受本次窗口守护控制的 Chromium 实例。");
      await waitForChrome(store.browser.debugPort);
      browser = await connectPlaywrightBrowser(store.browser.debugPort);
      const context = browser.contexts()[0];
      if (!context) throw new Error("京东商品榜单专用 Chromium 没有可用的浏览器上下文。");
      const blankPages = context.pages().filter((candidate) => candidate.url() === "about:blank");
      if (blankPages.length !== 1) throw new Error("京东商品榜单专用 Chromium 本轮空白启动页不唯一。");
      const page = blankPages[0]!;
      await page.evaluate(() => { window.name = "teruisi-jd-market-ranking"; });
      activePage = page;
      await installRequestCapture(page);
      const navigation = await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
      if (!navigation?.ok()) {
        throw new Error(`京东商品榜单目标页面导航失败：HTTP ${navigation?.status() ?? "unknown"}`);
      }
      await ensureJdStoreAuthenticatedSession(page, store);
      if (!isJdMarketRankingPageUrl(page.url())) {
        const resumedNavigation = await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
        if (!resumedNavigation?.ok()) {
          throw new Error(`京东商品榜单登录后目标页面导航失败：HTTP ${resumedNavigation?.status() ?? "unknown"}`);
        }
        await ensureJdStoreAuthenticatedSession(page, store);
      }
      if (!isJdMarketRankingPageUrl(page.url())) {
        throw new Error("京东商品榜单登录后未回到唯一受控榜单地址。");
      }
      await assertStoreIdentity(page, plan);
      await dismissRankingNotice(page);
      for (const targetPlan of plan.targets) {
        if (!targetPlan.chunks.length) continue;
        activeTargetPlan = targetPlan;
        const target = config.categories.find((candidate) => candidate.key === targetPlan.key);
        if (!target) throw new Error(`市场榜单受控类目不存在：${targetPlan.key}`);
        await assertStoreIdentity(page, plan);
        const identity = await selectRankingIdentity(page, target);
        await saveEvidenceScreenshot(page, plan, targetPlan, "filters");
        let evidencePrepared = false;
        for (const chunk of targetPlan.chunks) {
          if (chunk.importProof) continue;
          await assertStoreIdentity(page, plan);
          const { filePath, bytes } = await downloadRankingWorkbook(page, identity, target, chunk, runDirectory);
          chunk.filePath = filePath;
          chunk.fileHash = createHash("sha256").update(bytes).digest("hex");
          chunk.fileSizeBytes = bytes.byteLength;
          await persistPlan(plan);
          if (!evidencePrepared) {
            await saveEvidenceScreenshot(page, plan, targetPlan, "downloadReady");
            evidencePrepared = true;
          }
          const signed = await inspectSignedChunk(plan, config, target, chunk);
          const proof = await importRankingFile(plan, config, target, chunk, signed.bytes, signed.evidence, options.request);
          chunk.importProof = proof;
          chunk.batchId = proof.batchId;
          chunk.rowCount = proof.rowCount;
          await persistPlan(plan);
          await identity.frame.waitForTimeout(config.requestDelayMs);
        }
        await saveEvidenceScreenshot(page, plan, targetPlan, "imported");
      }
      plan.stage = "executed"; delete plan.failure; await persistPlan(plan);
      return {
        ok: true, stage: "run", runId: plan.runId,
        importedFiles: plan.targets.reduce((sum, target) => sum + target.chunks.length, 0),
        rowCount: plan.targets.reduce((sum, target) => sum + target.chunks.reduce((targetSum, chunk) => targetSum + Number(chunk.rowCount ?? 0), 0), 0),
      };
    } catch (error) {
      if (activePage && activeTargetPlan) await saveEvidenceScreenshot(activePage, plan, activeTargetPlan, "filters");
      plan.stage = "failed";
      plan.failure = { stage: "run", message: (error instanceof Error ? error.message : String(error)).slice(0, 1000), at: new Date().toISOString() };
      await persistPlan(plan);
      throw error;
    } finally {
      await browser?.close().catch(() => undefined);
      if (ownsBrowser) await closeChromeBrowser(store.browser.debugPort);
    }
  }));
}

export async function verifyJdMarketDailyPlan(plan: JdMarketDailyPlan, request: typeof fetch = fetch) {
  const config = await loadJdMarketDailyConfig();
  if (plan.stage === "completed") return { ok: true, stage: "verify", runId: plan.runId, missingAfterImport: [] };
  if (plan.stage !== "executed") throw new Error("市场榜单计划尚未进入可核验阶段");
  const configuredTargets = new Map(config.categories.map((target) => [target.key, target]));
  const missingAfterImport: Array<{ key: string; category: string; date: string }> = [];
  for (const targetPlan of plan.targets) {
    const target = configuredTargets.get(targetPlan.key);
    if (!target || target.systemCategory !== targetPlan.identity.category || JSON.stringify(target.categoryPath) !== JSON.stringify(targetPlan.categoryPath)
      || target.secondIndId !== targetPlan.identity.secondIndId || target.thirdIndId !== targetPlan.identity.thirdIndId
      || targetPlan.identity.scope !== config.scope || targetPlan.identity.rankingDimension !== config.dimension || targetPlan.identity.priceBandFilter !== config.priceBandFilter) {
      throw new Error(`市场榜单核验类目与当前受控配置不一致：${targetPlan.key}`);
    }
    for (const chunk of targetPlan.chunks) {
      if (!chunk.batchId || !chunk.rowCount || !chunk.importProof) throw new Error("市场榜单计划缺少严格导入批次证明");
      const { evidence } = await inspectSignedChunk(plan, config, target, chunk);
      assertJdMarketImportProof(chunk.importProof, evidence);
      if (chunk.batchId !== chunk.importProof.batchId || chunk.rowCount !== chunk.importProof.rowCount) {
        throw new Error("市场榜单计划批次摘要与严格导入证明不一致");
      }
    }
    const coverage = await readCoverage(plan.baseUrl, config, target, plan.startDate, plan.endDate, request);
    for (const date of targetPlan.missingDates.filter((candidate) => coverage.missingDates.includes(candidate))) {
      missingAfterImport.push({ key: target.key, category: target.systemCategory, date });
    }
  }
  if (missingAfterImport.length) throw new Error(`市场榜单导入后仍缺少 ${missingAfterImport.length} 个目标日`);
  plan.stage = "completed"; delete plan.failure; await persistPlan(plan);
  return { ok: true, stage: "verify", runId: plan.runId, importedDateCount: plan.targets.reduce((sum, target) => sum + target.missingDates.length, 0), missingAfterImport };
}

export function jdMarketHelperRequestError(stage: string, busy: boolean, route: string, requestExecutionId: string | null, claimedExecutionId: string | null) {
  if (!requestExecutionId) return { error: "missing_or_invalid_execution_id" as const };
  if (claimedExecutionId && requestExecutionId !== claimedExecutionId) return { error: "execution_mismatch" as const };
  if (!claimedExecutionId && route !== "/jd-market/plan") return { error: "execution_not_claimed" as const, expected: "/jd-market/plan" as const };
  if (busy) return { error: "pipeline_busy" as const };
  if (route === "/jd-market/plan") {
    return ["ready", "planned", "executed", "completed"].includes(stage)
      ? null
      : { error: "invalid_stage" as const, expected: "ready|planned|executed|completed", actual: stage };
  }
  if (route === "/jd-market/run") {
    return stage === "planned" || stage === "executed"
      ? null
      : { error: "invalid_stage" as const, expected: "planned|executed", actual: stage };
  }
  return stage === "executed" || stage === "completed"
    ? null
    : { error: "invalid_stage" as const, expected: "executed|completed", actual: stage };
}
