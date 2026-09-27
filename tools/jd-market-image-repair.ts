import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Frame, Page } from "playwright-core";

import { closeChromeBrowser, launchDedicatedChrome, waitForChrome } from "../lib/jackyun/cdp-client";
import { connectPlaywrightBrowser, connectPlaywrightJackyunTarget } from "../lib/jackyun/playwright-client";
import { withJdChromiumRunLock } from "../lib/jd/chromium-run-lock";
import { assertJdProductDetailStoreIdentity, parseJdProductDetailStoreIdentity } from "../lib/jd/product-detail-store-identity";
import { getJdStore } from "../lib/jd/store-registry";
import { normalizeJdMarketRepairImageUrl, type MarketImageRepairCandidate, type MarketImageRepairMapping } from "../lib/market/image-repair-contract";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const configPath = path.join(projectRoot, "config", "jd-market-ranking-daily.json");
const targetUrl = "https://jdsz.jd.com/szweb/view/industry/industry-product-rank-temp.html?sz=%2Fszweb%2Fsz%2Fview%2FindustryMarket%2FproductRanks.html";

type CandidatePage = { items: MarketImageRepairCandidate[]; pagination: { page: number; pageSize: number; pageCount: number; total: number } };

type MarketImageCacheTotals = {
  pending: number;
  cached: number;
  failed: number;
  processed: number;
};

type MarketImageCacheJobSnapshot = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  total: number;
  pending: number;
  propagationPending: number;
  cached: number;
  failed: number;
  processedCount: number;
  errorCode: string;
  errorMessage: string;
};

type MarketImageCacheRequest = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

type MarketImageCachePollOptions = {
  signal?: AbortSignal;
  pollIntervalMs?: number;
  maxPolls?: number;
  request?: MarketImageCacheRequest;
};

const DEFAULT_IMAGE_CACHE_POLL_INTERVAL_MS = 2_000;
const DEFAULT_IMAGE_CACHE_MAX_POLLS = 7_200;

function normalizeBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("市场图片修复只允许访问本机运营系统根地址");
  }
  return url.toString().replace(/\/$/, "");
}

async function jsonRequest<T>(url: string, init?: RequestInit, request: MarketImageCacheRequest = fetch) {
  const timeoutSignal = AbortSignal.timeout(120_000);
  const signal = init?.signal ? AbortSignal.any([init.signal, timeoutSignal]) : timeoutSignal;
  const response = await request(url, { ...init, signal });
  const body = await response.json().catch(() => null) as (T & { error?: string }) | null;
  if (!response.ok || !body) throw new Error(body?.error || `运营系统请求失败：HTTP ${response.status}`);
  return body;
}

async function readRepairCandidates(baseUrl: string) {
  const first = await jsonRequest<CandidatePage>(`${baseUrl}/api/market/images/repair?page=1&pageSize=200`);
  const items = [...first.items];
  for (let page = 2; page <= first.pagination.pageCount; page += 1) {
    const next = await jsonRequest<CandidatePage>(`${baseUrl}/api/market/images/repair?page=${page}&pageSize=200`);
    items.push(...next.items);
  }
  return items;
}

async function applyRepairs(baseUrl: string, repairs: MarketImageRepairMapping[]) {
  const totals = { repairCount: 0, rankingRowsUpdated: 0, snapshotsUpdated: 0, inheritedPrices: 0 };
  for (let index = 0; index < repairs.length; index += 20) {
    const body = await jsonRequest<{ result: typeof totals }>(`${baseUrl}/api/market/images/repair`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repairs: repairs.slice(index, index + 20) }),
    });
    for (const key of Object.keys(totals) as Array<keyof typeof totals>) totals[key] += Number(body.result[key] ?? 0);
  }
  return totals;
}

async function assertStoreIdentity(page: Page, expected: { shopId: string; shopName: string }) {
  // Product ranking rows also contain mall links after their data finishes
  // loading. Only the signed-in shop link in the page header is authoritative.
  const links = page.locator('.user-info .shop-name a[href*="mall.jd.com/index-"]').filter({ visible: true });
  await links.first().waitFor({ state: "visible", timeout: 30_000 });
  const candidates: Array<{ href: string | null; text: string }> = [];
  for (let index = 0; index < await links.count(); index += 1) {
    const link = links.nth(index);
    candidates.push({ href: await link.getAttribute("href"), text: await link.innerText() });
  }
  return assertJdProductDetailStoreIdentity(parseJdProductDetailStoreIdentity(candidates), expected);
}

async function ensureSkuDimension(frame: Frame) {
  const surface = frame.locator("#sz-old-version").filter({ visible: true });
  await surface.first().waitFor({ state: "visible", timeout: 30_000 });
  if (await surface.count() !== 1) throw new Error("京东商品榜单受控业务容器不唯一");
  const controls = surface.first().locator(".jmtd-base-input-top").filter({ visible: true });
  await controls.first().waitFor({ state: "visible", timeout: 30_000 });
  if (await controls.count() < 3) throw new Error("京东商品榜单筛选控件不完整");
  if ((await controls.nth(0).innerText()).trim() === "SKU") return;
  await controls.nth(0).click();
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const options = surface.first().locator(".jmtd-dropdown-option").filter({ visible: true }).filter({ hasText: /^SKU$/ });
    if (await options.count() === 1 && await options.first().click({ timeout: 3_000, force: true }).then(() => true).catch(() => false)) return;
    await frame.waitForTimeout(100);
  }
  throw new Error("京东商品榜单无法精确切换到 SKU 维度");
}

async function captureSkuHeaders(page: Page) {
  let networkState: { headers: Record<string, string>; url: string } | null = null;
  let imageHeaders: Record<string, string> | null = null;
  const replayableHeaderNames = new Set(["accept", "p-pin", "user-mnp", "user-mup", "uuid", "x-requested-with"]);
  page.on("request", (request) => {
    const replayableHeaders = Object.fromEntries(Object.entries(request.headers()).filter(([name]) => replayableHeaderNames.has(name.toLowerCase())));
    if (/\/sz\/api\/industry\/getImageURL\.ajax/.test(request.url())) {
      imageHeaders = replayableHeaders;
      return;
    }
    if (/\/sz\/api\/industryMarket\/getProductBillBoardDealData\.ajax/.test(request.url())
      && new URL(request.url()).searchParams.get("unitType") === "1") {
      networkState = { url: request.url(), headers: replayableHeaders };
    }
  });
  await page.addInitScript(() => {
    const target = window as typeof window & { __teruisiImageRepairRank?: { headers: Record<string, string>; url: string } };
    const rawOpen = XMLHttpRequest.prototype.open;
    const invokeOpen = rawOpen as unknown as (
      this: XMLHttpRequest,
      method: string,
      url: string | URL,
      async?: boolean,
      username?: string | null,
      password?: string | null,
    ) => void;
    const rawHeader = XMLHttpRequest.prototype.setRequestHeader;
    const rawSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (
      method: string,
      url: string | URL,
      async?: boolean,
      username?: string | null,
      password?: string | null,
    ) {
      (this as XMLHttpRequest & { __teruisiUrl?: string }).__teruisiUrl = String(url);
      return async === undefined
        ? invokeOpen.call(this, method, url)
        : invokeOpen.call(this, method, url, async, username, password);
    };
    XMLHttpRequest.prototype.setRequestHeader = function (...args: Parameters<XMLHttpRequest["setRequestHeader"]>) {
      const request = this as XMLHttpRequest & { __teruisiHeaders?: Record<string, string> };
      (request.__teruisiHeaders ??= {})[args[0]] = args[1];
      return rawHeader.apply(this, args as never);
    };
    XMLHttpRequest.prototype.send = function (...args: Parameters<XMLHttpRequest["send"]>) {
      const request = this as XMLHttpRequest & { __teruisiUrl?: string; __teruisiHeaders?: Record<string, string> };
      if (/\/sz\/api\/industryMarket\/getProductBillBoardDealData\.ajax/.test(request.__teruisiUrl ?? "") && request.__teruisiHeaders) {
        target.__teruisiImageRepairRank = { headers: { ...request.__teruisiHeaders }, url: request.__teruisiUrl! };
      }
      return rawSend.apply(this, args as never);
    };
  });
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
  let frame = page.frames().find((candidate) => /productRanks\.html/.test(candidate.url()));
  if (!frame) throw new Error("未找到京东商品榜单业务框架");
  await ensureSkuDimension(frame);
  let state: { headers: Record<string, string>; url: string } | null = null;
  for (let attempt = 0; attempt < 300 && (!state || !imageHeaders); attempt += 1) {
    state = await frame.evaluate(() => (window as typeof window & { __teruisiImageRepairRank?: { headers: Record<string, string>; url: string } }).__teruisiImageRepairRank ?? null).catch(() => null) ?? networkState;
    if (!state || !imageHeaders) await frame.waitForTimeout(100);
  }
  if (!state || !imageHeaders || new URL(state.url, targetUrl).searchParams.get("unitType") !== "1") {
    await page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 });
    frame = page.frames().find((candidate) => /productRanks\.html/.test(candidate.url()));
    if (!frame) throw new Error("重新加载后未找到京东商品榜单业务框架");
    await ensureSkuDimension(frame);
    state = null;
    networkState = null;
    imageHeaders = null;
    for (let attempt = 0; attempt < 300 && (!state || !imageHeaders); attempt += 1) {
      state = await frame.evaluate(() => (window as typeof window & { __teruisiImageRepairRank?: { headers: Record<string, string>; url: string } }).__teruisiImageRepairRank ?? null).catch(() => null) ?? networkState;
      if (!state || !imageHeaders) await frame.waitForTimeout(100);
    }
  }
  if (!state || !imageHeaders || new URL(state.url, targetUrl).searchParams.get("unitType") !== "1") throw new Error("未捕获到京东商品榜单 SKU 与图片原生请求头");
  return { headers: imageHeaders, frame };
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function parseJdMarketRepairImageResponse(body: unknown) {
  const root = recordValue(body);
  const content = recordValue(root?.content);
  const candidate = content?.data ?? root?.data ?? root?.content;
  const rows: Array<Record<string, unknown>> = [];
  if (Array.isArray(candidate)) {
    for (const value of candidate) {
      const row = recordValue(value);
      if (row) rows.push(row);
    }
  } else {
    const keyed = recordValue(candidate);
    if (keyed) {
      for (const [skuId, value] of Object.entries(keyed)) {
        const row = recordValue(value);
        rows.push(row ? { skuId, ...row } : { skuId, imgSrc: value });
      }
    }
  }
  const images = new Map<string, string>();
  for (const row of rows) {
    const skuId = String(row.skuId ?? row.skuID ?? row.sku ?? row.id ?? "").trim();
    const imageUrl = normalizeJdMarketRepairImageUrl(row.imgSrc ?? row.imageUrl ?? row.imgUrl);
    if (skuId && imageUrl) images.set(skuId, imageUrl);
  }
  return images;
}

export function summarizeJdMarketRepairImageResponse(body: unknown) {
  const root = recordValue(body);
  const content = recordValue(root?.content);
  return {
    rootKeys: Object.keys(root ?? {}).slice(0, 12),
    contentKeys: Object.keys(content ?? {}).slice(0, 12),
    code: String(root?.code ?? root?.errorCode ?? content?.code ?? content?.errorCode ?? "").slice(0, 80),
    status: String(root?.status ?? content?.status ?? "").slice(0, 80),
    message: String(root?.message ?? content?.message ?? "").replace(/[\r\n]+/g, " ").slice(0, 160),
    success: root?.success ?? content?.success ?? null,
  };
}

async function fetchJdImages(frame: Frame, skuIds: string[], headers: Record<string, string>) {
  const images = new Map<string, string>();
  const requestedBatchSize = Number(process.env.JD_MARKET_IMAGE_REPAIR_BATCH_SIZE ?? 50);
  const batchSize = Number.isInteger(requestedBatchSize) && requestedBatchSize >= 1 && requestedBatchSize <= 50 ? requestedBatchSize : 50;
  for (let index = 0; index < skuIds.length; index += batchSize) {
    const chunk = skuIds.slice(index, index + batchSize);
    const body = await frame.evaluate(async ({ ids, requestHeaders }) => {
      const response = await fetch(`/sz/api/industry/getImageURL.ajax?ids=${ids.join(",")}`, {
        credentials: "include",
        headers: requestHeaders,
      });
      const value = await response.json();
      if (!response.ok) throw new Error(`京东商品图片接口请求失败：HTTP ${response.status}`);
      return value as unknown;
    }, { ids: chunk, requestHeaders: headers });
    const parsed = parseJdMarketRepairImageResponse(body);
    if (parsed.size === 0) {
      throw new Error(`京东商品图片接口未返回可验证主图：${JSON.stringify(summarizeJdMarketRepairImageResponse(body))}`);
    }
    for (const [skuId, imageUrl] of parsed) images.set(skuId, imageUrl);
    await frame.waitForTimeout(100);
  }
  return images;
}

function boundedInteger(value: number | undefined, fallback: number, minimum: number, maximum: number, label: string) {
  const candidate = value ?? fallback;
  if (!Number.isInteger(candidate) || candidate < minimum || candidate > maximum) {
    throw new Error(`${label} 必须是 ${minimum}–${maximum} 的整数`);
  }
  return candidate;
}

function numericField(record: Record<string, unknown>, key: string, fallback?: number) {
  if (!Object.hasOwn(record, key) && fallback !== undefined) return fallback;
  const value = Number(record[key]);
  if (!Number.isFinite(value) || value < 0) throw new Error(`图片缓存响应字段 ${key} 无效`);
  return value;
}

function parseMarketImageCacheJob(value: unknown): MarketImageCacheJobSnapshot | null {
  const job = recordValue(value);
  if (!job) return null;
  const id = typeof job.id === "string" ? job.id.trim() : "";
  const status = typeof job.status === "string" ? job.status : "";
  if (!id || !["queued", "running", "completed", "failed"].includes(status)) {
    throw new Error("图片缓存任务响应缺少有效的 job id 或 status");
  }
  return {
    id,
    status: status as MarketImageCacheJobSnapshot["status"],
    total: numericField(job, "total"),
    pending: numericField(job, "pending"),
    propagationPending: numericField(job, "propagationPending", 0),
    cached: numericField(job, "cached"),
    failed: numericField(job, "failed"),
    processedCount: numericField(job, "processedCount", 0),
    errorCode: typeof job.errorCode === "string" ? job.errorCode.slice(0, 120) : "",
    errorMessage: typeof job.errorMessage === "string" ? job.errorMessage.slice(0, 500) : "",
  };
}

function parseLegacyMarketImageCacheResult(value: unknown): MarketImageCacheTotals | null {
  const result = recordValue(value);
  if (!result) return null;
  return {
    pending: numericField(result, "pending"),
    cached: numericField(result, "cached"),
    failed: numericField(result, "failed"),
    processed: numericField(result, "processed", 0),
  };
}

function interruptedImageCacheError(signal?: AbortSignal) {
  const suffix = typeof signal?.reason === "string"
    ? signal.reason
    : signal?.reason instanceof Error && signal.reason.message !== "This operation was aborted"
      ? signal.reason.message
      : "";
  const error = new Error(`市场图片缓存任务轮询已中断${suffix ? `：${suffix}` : ""}`);
  error.name = "AbortError";
  return error;
}

function assertImageCachePollingActive(signal?: AbortSignal) {
  if (signal?.aborted) throw interruptedImageCacheError(signal);
}

function waitForImageCachePoll(signal: AbortSignal | undefined, delayMs: number) {
  assertImageCachePollingActive(signal);
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, delayMs);
    const abort = () => {
      cleanup();
      reject(interruptedImageCacheError(signal));
    };
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    };
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
  });
}

function completedCacheTotals(job: MarketImageCacheJobSnapshot): MarketImageCacheTotals {
  return { pending: job.pending + job.propagationPending, cached: job.cached, failed: job.failed, processed: job.processedCount };
}

function assertImageCacheJobSucceeded(job: MarketImageCacheJobSnapshot) {
  if (job.status !== "failed") return;
  const details = job.errorMessage || job.errorCode || "后台任务未提供失败原因";
  throw new Error(`市场图片缓存任务失败（${job.id}）：${details}`);
}

async function pollMarketImageCacheJob(
  baseUrl: string,
  initialJob: MarketImageCacheJobSnapshot,
  options: Required<Pick<MarketImageCachePollOptions, "pollIntervalMs" | "maxPolls" | "request">> & Pick<MarketImageCachePollOptions, "signal">,
) {
  let job = initialJob;
  assertImageCacheJobSucceeded(job);
  if (job.status === "completed") return completedCacheTotals(job);
  for (let poll = 0; poll < options.maxPolls; poll += 1) {
    await waitForImageCachePoll(options.signal, options.pollIntervalMs);
    assertImageCachePollingActive(options.signal);
    let body: { job?: unknown };
    try {
      body = await jsonRequest<{ job?: unknown }>(
        `${baseUrl}/api/market/images/cache?jobId=${encodeURIComponent(job.id)}`,
        { cache: "no-store", signal: options.signal },
        options.request,
      );
    } catch (error) {
      if (options.signal?.aborted) throw interruptedImageCacheError(options.signal);
      throw error;
    }
    const nextJob = parseMarketImageCacheJob(body.job);
    if (!nextJob) throw new Error("图片缓存任务状态响应缺少 job");
    if (nextJob.id !== job.id) throw new Error(`图片缓存任务身份不一致：期望 ${job.id}，实际 ${nextJob.id}`);
    job = nextJob;
    assertImageCacheJobSucceeded(job);
    if (job.status === "completed") return completedCacheTotals(job);
  }
  throw new Error(
    `市场图片缓存任务轮询超时（${job.id}）：状态 ${job.status}，待缓存 ${job.pending}，待传播 ${job.propagationPending}；后台任务仍会继续运行`,
  );
}

/** Submit the background cache job and wait for its bounded terminal state. */
export async function cacheRepairedImages(baseUrl: string, options: MarketImageCachePollOptions = {}) {
  const pollOptions = {
    signal: options.signal,
    pollIntervalMs: boundedInteger(options.pollIntervalMs, DEFAULT_IMAGE_CACHE_POLL_INTERVAL_MS, 0, 60_000, "轮询间隔"),
    maxPolls: boundedInteger(options.maxPolls, DEFAULT_IMAGE_CACHE_MAX_POLLS, 1, 10_000, "最大轮询次数"),
    request: options.request ?? fetch,
  };
  let noProgressRounds = 0;
  let latest: MarketImageCacheTotals = { pending: 0, cached: 0, failed: 0, processed: 0 };
  for (let round = 0; round < 250; round += 1) {
    assertImageCachePollingActive(pollOptions.signal);
    let body: { job?: unknown; result?: unknown };
    try {
      body = await jsonRequest<{ job?: unknown; result?: unknown }>(
        `${baseUrl}/api/market/images/cache`,
        { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ limit: 24 }), signal: pollOptions.signal },
        pollOptions.request,
      );
    } catch (error) {
      if (pollOptions.signal?.aborted) throw interruptedImageCacheError(pollOptions.signal);
      throw error;
    }
    const job = parseMarketImageCacheJob(body.job);
    if (job) return pollMarketImageCacheJob(baseUrl, job, pollOptions);
    const legacyResult = parseLegacyMarketImageCacheResult(body.result);
    if (!legacyResult) throw new Error("图片缓存响应既不包含异步 job，也不包含兼容 result");
    latest = legacyResult;
    if (latest.pending <= 0) break;
    noProgressRounds = latest.processed === 0 ? noProgressRounds + 1 : 0;
    if (noProgressRounds >= 3) break;
  }
  return latest;
}

export async function runJdMarketImageRepair(options: { baseUrl?: string; signal?: AbortSignal } = {}) {
  const baseUrl = normalizeBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://127.0.0.1:3000");
  const candidates = await readRepairCandidates(baseUrl);
  if (!candidates.length) return { ok: true, candidateCount: 0, repairedCount: 0, unresolvedCount: 0 };

  const reusable = candidates.filter((candidate) => normalizeJdMarketRepairImageUrl(candidate.reusableImageUrl));
  const external = candidates.filter((candidate) => !normalizeJdMarketRepairImageUrl(candidate.reusableImageUrl));
  const reusableResult = reusable.length ? await applyRepairs(baseUrl, reusable.map((candidate) => ({
    category: candidate.category, scope: candidate.scope, rankingDimension: candidate.rankingDimension,
    skuCode: candidate.skuCode, imageUrl: candidate.reusableImageUrl,
  }))) : { repairCount: 0, rankingRowsUpdated: 0, snapshotsUpdated: 0, inheritedPrices: 0 };

  let resolvedImages = new Map<string, string>();
  if (external.length) {
    const config = JSON.parse(await readFile(configPath, "utf8")) as { storeKey?: string };
    if (!config.storeKey) throw new Error("京东市场榜单配置缺少受控店铺");
    const store = await getJdStore(config.storeKey);
    resolvedImages = await withJdChromiumRunLock("market-image-repair", async () => {
      const launched = await launchDedicatedChrome({
        executablePath: store.browser.executablePath,
        profileDirectory: store.browser.userDataDir,
        profileName: store.browser.profileName,
        port: store.browser.debugPort,
        startUrl: "about:blank",
        headless: false,
        visible: false,
        startMinimized: true,
        keepWindowHidden: true,
      });
      if (!launched) throw new Error("京东图片修复拒绝复用不受本次窗口守护控制的 Chromium 实例");
      let browser: Awaited<ReturnType<typeof connectPlaywrightBrowser>> | null = null;
      try {
        await waitForChrome(store.browser.debugPort);
        browser = await connectPlaywrightBrowser(store.browser.debugPort);
        const { page } = await connectPlaywrightJackyunTarget(browser, {
          workerName: "teruisi-jd-market-image-repair",
          targetUrlPattern: /jdsz\.jd\.com/i,
          requireMini: false,
        });
        const { headers, frame } = await captureSkuHeaders(page);
        await assertStoreIdentity(page, store);
        return await fetchJdImages(frame, [...new Set(external.map((candidate) => candidate.skuCode))], headers);
      } finally {
        await browser?.close().catch(() => undefined);
        await closeChromeBrowser(store.browser.debugPort);
      }
    });
  }

  const resolved = external.flatMap((candidate) => {
    const imageUrl = resolvedImages.get(candidate.skuCode);
    return imageUrl ? [{
      category: candidate.category, scope: candidate.scope, rankingDimension: candidate.rankingDimension,
      skuCode: candidate.skuCode, imageUrl,
    }] : [];
  });
  const resolvedResult = resolved.length ? await applyRepairs(baseUrl, resolved) : { repairCount: 0, rankingRowsUpdated: 0, snapshotsUpdated: 0, inheritedPrices: 0 };
  const cache = await cacheRepairedImages(baseUrl, { signal: options.signal });
  const repairedCount = reusableResult.repairCount + resolvedResult.repairCount;
  return {
    ok: true,
    candidateCount: candidates.length,
    historicalReuseCount: reusableResult.repairCount,
    jdResolvedCount: resolvedResult.repairCount,
    repairedCount,
    unresolvedCount: candidates.length - repairedCount,
    rankingRowsUpdated: reusableResult.rankingRowsUpdated + resolvedResult.rankingRowsUpdated,
    snapshotsUpdated: reusableResult.snapshotsUpdated + resolvedResult.snapshotsUpdated,
    inheritedPrices: reusableResult.inheritedPrices + resolvedResult.inheritedPrices,
    cache,
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  let interruptedExitCode = 0;
  const interrupt = (signal: "SIGINT" | "SIGTERM") => {
    interruptedExitCode ||= signal === "SIGINT" ? 130 : 143;
    if (!controller.signal.aborted) controller.abort(`收到 ${signal}`);
  };
  const onSigint = () => interrupt("SIGINT");
  const onSigterm = () => interrupt("SIGTERM");
  process.once("SIGINT", onSigint);
  process.once("SIGTERM", onSigterm);
  runJdMarketImageRepair({ signal: controller.signal }).then((result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
    if (result.unresolvedCount > 0) process.exitCode = 2;
  }).catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = interruptedExitCode || 1;
  }).finally(() => {
    process.removeListener("SIGINT", onSigint);
    process.removeListener("SIGTERM", onSigterm);
  });
}
