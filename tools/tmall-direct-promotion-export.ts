import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import type { APIRequestContext, APIResponse, Page, Request } from "playwright-core";

import { writeJsonAtomic } from "../lib/jackyun/json-file";
import { connectPlaywrightBrowser } from "../lib/jackyun/playwright-client";
import { getTmallStore, type TmallStore } from "../lib/netshop/tmall-store-registry";
import {
  TMALL_PROMOTION_DOWNLOAD_LIST_URL,
  chooseTmallPromotionEntryPageIndex,
  importTmallPromotionFile,
  inspectTmallPromotionFile,
  runTmallPromotionStage,
  verifyTmallPromotionCoverageAfterImport,
  waitForAlimamaIdentity,
  type PromotionDatePlan,
  type PromotionFileEvidence,
} from "./tmall-promotion-export";
import {
  ensureTmallStoreAuthenticatedSession,
  tmallAutomationProjectRoot,
} from "./tmall-product-master-export";
import { TMALL_YIJIU_STORE_KEY, assertTmallDirectPmStore } from "./tmall-yijiu-direct-pm-contract";

export const TMALL_ALIMAMA_CREATE_REPORT_URL = "https://one.alimama.com/report/createDownLoadTask.json";
export const TMALL_ALIMAMA_FIND_REPORT_URL = "https://bpcommon.alimama.com/commonapi/report/async/findPage.json";
export const TMALL_ALIMAMA_DOWNLOAD_URL = "https://bpcommon.alimama.com/commonapi/report/async/getDownloadUrl.json";
export const TMALL_DIRECT_PROMOTION_PROTOCOL = "alimama_item_promotion_direct_v1" as const;
export const TMALL_DIRECT_PROMOTION_BIZ_CODES = [
  "onebpSite",
  "onebpSearch",
  "onebpDisplay",
  "onebpStarShop",
] as const;
export const TMALL_DIRECT_PROMOTION_FIELDS = [
  "adPv",
  "click",
  "charge",
  "ctr",
  "ecpc",
  "alipayInshopAmt",
  "alipayInshopNum",
  "cvr",
  "cartInshopNum",
  "itemColInshopNum",
  "shopColDirNum",
  "colNum",
  "itemColInshopCost",
] as const;

const defaultAuditDirectory = path.join(tmallAutomationProjectRoot, "outputs", "tmall-direct-promotion-export");
const legacyAuditDirectory = path.join(tmallAutomationProjectRoot, "outputs", "tmall-promotion-export");
const maximumDownloadBytes = 25 * 1024 * 1024;
const taskPollTimeoutMs = 7 * 60_000;
const taskPollIntervalMs = 8_000;

type DirectPromotionStage =
  | "planned"
  | "session_ready"
  | "report_submitting"
  | "report_submitted"
  | "downloading"
  | "downloaded"
  | "importing"
  | "completed"
  | "failed";

type DirectPromotionAudit = {
  version: 1;
  protocol: typeof TMALL_DIRECT_PROMOTION_PROTOCOL;
  runId: string;
  storeKey: string;
  shopName: string;
  baseUrl: string;
  startDate: string;
  endDate: string;
  startedAt: string;
  updatedAt: string;
  stage: DirectPromotionStage;
  resumeStage?: Exclude<DirectPromotionStage, "completed" | "failed">;
  reportName: string;
  taskId?: string;
  file?: PromotionFileEvidence;
  importResult?: { status: "imported" | "duplicate"; batchId: string; warningCount: number };
  error?: string;
};

type AlimamaIdentifiers = { csrfId: string; loginPointId: string };

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function safeSegment(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/\s+/g, "-").slice(0, 80);
}

function activeAuditPath(storeKey: string, directory: string) {
  return path.join(directory, `active-${safeSegment(storeKey)}.json`);
}

function assertActive(signal?: AbortSignal) {
  if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error("天猫推广直连阶段已终止");
}

function normalizeLocalBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname)) {
    throw new Error("天猫推广直连导入只允许连接本机运营系统");
  }
  return url.toString().replace(/\/$/, "");
}

function shanghaiDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((entry) => entry.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function shiftDate(date: string, days: number) {
  const shifted = new Date(`${date}T12:00:00Z`);
  shifted.setUTCDate(shifted.getUTCDate() + days);
  return shifted.toISOString().slice(0, 10);
}

function resumableStage(audit: DirectPromotionAudit) {
  return audit.stage === "failed" ? audit.resumeStage ?? "planned" : audit.stage;
}

export function buildTmallDirectPromotionRequestBody(input: {
  date: string;
  reportName: string;
  csrfId: string;
  loginPointId: string;
}) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.date)) throw new Error("推广直连日期必须是 YYYY-MM-DD");
  if (!input.reportName.trim() || input.reportName.length > 120) throw new Error("推广直连报表名无效");
  return {
    excelName: input.reportName,
    pageSize: 20,
    offset: 0,
    havingList: [],
    startTime: input.date,
    endTime: input.date,
    splitType: "day",
    fieldType: "all",
    byPage: false,
    fromRealTime: false,
    source: "async_dowdload",
    bizCode: "universalBP",
    csrfId: input.csrfId,
    loginPointId: input.loginPointId,
    vsType: "week",
    vsTime: input.date,
    from: "pcBaseReport",
    unifyType: "last_click_by_effect_time",
    effectEqual: 15,
    bizCodeIn: [...TMALL_DIRECT_PROMOTION_BIZ_CODES],
    queryFieldIn: [...TMALL_DIRECT_PROMOTION_FIELDS],
    searchValue: "",
    searchKey: "itemIdOrName",
    queryDomains: ["promotion", "campaign"],
    rptType: "item_promotion",
    parentAdcName: "report_frame_item_promotion",
  };
}

export function parseTmallAlimamaIdentifiers(value: string): AlimamaIdentifiers | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "bpcommon.alimama.com"
      || url.pathname !== "/commonapi/report/async/findPage.json") return null;
    const csrfId = url.searchParams.get("csrfId")?.trim() ?? "";
    const loginPointId = url.searchParams.get("loginPointId")?.trim() ?? "";
    const safe = (token: string) => token.length >= 1 && token.length <= 512 && !/[\s\u0000-\u001f]/.test(token);
    return safe(csrfId) && safe(loginPointId) ? { csrfId, loginPointId } : null;
  } catch {
    return null;
  }
}

export function assertTmallSignedDownloadUrl(value: unknown, label = "天猫下载") {
  if (typeof value !== "string") throw new Error(`${label}未返回下载链接`);
  const url = new URL(value);
  if (url.protocol !== "https:" || !/(^|\.)aliyuncs\.com$/i.test(url.hostname) || url.username || url.password) {
    throw new Error(`${label}链接不在受控 HTTPS 阿里云 OSS 域名`);
  }
  return url.toString();
}

export function directPromotionLegacyAuditBlocks(value: unknown) {
  const audit = asRecord(value);
  const stage = String(audit.stage ?? "");
  const resumeStage = String(audit.resumeStage ?? "");
  const businessStages = new Set([
    "report_submitting",
    "report_submitted",
    "downloaded_unverified",
    "downloaded",
    "importing",
  ]);
  return businessStages.has(stage) || stage === "failed" && businessStages.has(resumeStage);
}

async function readJsonResponse(response: APIResponse, label: string) {
  const bytes = await response.body();
  if (!response.ok() || bytes.byteLength === 0) {
    throw new Error(`${label}失败（HTTP ${response.status()}，响应为空或状态异常）`);
  }
  try {
    return JSON.parse(bytes.toString("utf8")) as unknown;
  } catch {
    throw new Error(`${label}未返回 JSON`);
  }
}

function taskIdFromCreatePayload(value: unknown) {
  const payload = asRecord(value);
  const data = asRecord(payload.data);
  const taskId = String(data.taskId ?? "").trim();
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(taskId) || payload.success === false) {
    throw new Error("阿里妈妈创建商品报表未返回唯一 taskId");
  }
  return taskId;
}

function reportRows(value: unknown) {
  const list = asRecord(asRecord(value).data).list;
  if (!Array.isArray(list)) throw new Error("阿里妈妈下载任务列表结构无效");
  return list.map(asRecord);
}

export function findTmallDirectPromotionTask(value: unknown, taskId: string) {
  const matching = reportRows(value).filter((row) => String(row.id ?? "") === taskId);
  if (matching.length > 1) throw new Error("阿里妈妈下载任务列表出现重复 taskId");
  return matching[0] ?? null;
}

function taskStatus(row: Record<string, unknown>) {
  return String(row.status ?? row.taskStatus ?? "").trim().toUpperCase();
}

function taskSucceeded(status: string) {
  return ["SUCCESS", "SUCCEEDED", "DONE", "FINISH", "FINISHED", "已完成", "生成成功"].includes(status);
}

function taskFailed(status: string) {
  return /FAIL|ERROR|CANCEL|失败|取消/.test(status);
}

function reportDownloadUrl(value: unknown) {
  const result = asRecord(asRecord(asRecord(value).data).result);
  return assertTmallSignedDownloadUrl(result.downloadUrl, "阿里妈妈商品报表");
}

async function wait(delayMs: number, signal?: AbortSignal) {
  assertActive(signal);
  await new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason instanceof Error ? signal.reason : new Error("天猫推广直连阶段已终止"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function boundedDownload(url: string, signal?: AbortSignal, request: typeof fetch = fetch) {
  const response = await request(url, { redirect: "follow", signal });
  if (!response.ok || !response.body) throw new Error(`下载天猫报表失败（HTTP ${response.status}）`);
  const declared = Number(response.headers.get("content-length") ?? 0);
  if (Number.isFinite(declared) && declared > maximumDownloadBytes) throw new Error("天猫报表超过 25MB 上限");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    assertActive(signal);
    const part = await reader.read();
    if (part.done) break;
    total += part.value.byteLength;
    if (total > maximumDownloadBytes) {
      await reader.cancel();
      throw new Error("天猫报表超过 25MB 上限");
    }
    chunks.push(part.value);
  }
  const bytes = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
  if (bytes.byteLength < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    throw new Error("阿里妈妈商品报表缺少 ZIP 魔数");
  }
  return bytes;
}

export async function discoverTmallAlimamaIdentifiers(
  page: Page,
  store: TmallStore,
  options: {
    waitForIdentity?: typeof waitForAlimamaIdentity;
    captureTimeoutMs?: number;
  } = {},
) {
  const waitForIdentity = options.waitForIdentity ?? waitForAlimamaIdentity;
  const captureTimeoutMs = options.captureTimeoutMs ?? 60_000;
  let identifiers: AlimamaIdentifiers | null = null;
  const captureIdentifiers = (request: Request) => {
    identifiers ??= parseTmallAlimamaIdentifiers(request.url());
  };

  // The download-list request can fire during the first authenticated
  // navigation. Listen before navigation, but only use a captured value after
  // the store identity has passed the existing DPAPI-backed login guard.
  page.on("request", captureIdentifiers);
  try {
    await page.goto(TMALL_PROMOTION_DOWNLOAD_LIST_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await waitForIdentity(page, store);
    if (!identifiers) {
      const requestPromise = page.waitForRequest(
        (request) => parseTmallAlimamaIdentifiers(request.url()) !== null,
        { timeout: captureTimeoutMs },
      ).catch(() => null);
      await page.goto(TMALL_PROMOTION_DOWNLOAD_LIST_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
      const observed = await requestPromise;
      await waitForIdentity(page, store);
      identifiers = observed ? parseTmallAlimamaIdentifiers(observed.url()) : identifiers;
    }
    if (!identifiers) throw new Error("未捕获到阿里妈妈下载列表的 csrfId/loginPointId");
    return identifiers;
  } finally {
    page.off("request", captureIdentifiers);
  }
}

async function apiCreateTask(api: APIRequestContext, identifiers: AlimamaIdentifiers, audit: DirectPromotionAudit) {
  const url = new URL(TMALL_ALIMAMA_CREATE_REPORT_URL);
  url.searchParams.set("csrfId", identifiers.csrfId);
  url.searchParams.set("bizCode", "universalBP");
  const response = await api.post(url.toString(), {
    data: buildTmallDirectPromotionRequestBody({
      date: audit.startDate,
      reportName: audit.reportName,
      ...identifiers,
    }),
    headers: {
      accept: "application/json, text/plain, */*",
      origin: "https://one.alimama.com",
      referer: "https://one.alimama.com/index.html",
    },
    timeout: 60_000,
  });
  return taskIdFromCreatePayload(await readJsonResponse(response, "创建阿里妈妈商品报表"));
}

async function apiFindTask(api: APIRequestContext, taskId: string, taskStartedAt: string) {
  const today = shanghaiDate();
  const parsedStartedAt = new Date(taskStartedAt);
  if (Number.isNaN(parsedStartedAt.getTime())) throw new Error("阿里妈妈商品报表活动清单开始时间无效");
  const startedDate = shanghaiDate(parsedStartedAt);
  if (startedDate < shiftDate(today, -30)) {
    throw new Error("阿里妈妈商品报表活动清单已超过 30 天，必须人工核对任务");
  }
  const url = new URL(TMALL_ALIMAMA_FIND_REPORT_URL);
  url.searchParams.set("relationType", "1");
  url.searchParams.set("bizCode", "universalBP");
  url.searchParams.set("startTime", startedDate);
  url.searchParams.set("endTime", today);
  url.searchParams.set("pageSize", "20");
  url.searchParams.set("offset", "0");
  const response = await api.get(url.toString(), {
    headers: { accept: "application/json, text/plain, */*", referer: "https://one.alimama.com/index.html" },
    timeout: 60_000,
  });
  return findTmallDirectPromotionTask(await readJsonResponse(response, "查询阿里妈妈下载任务"), taskId);
}

async function apiWaitForTask(api: APIRequestContext, taskId: string, taskStartedAt: string, signal?: AbortSignal) {
  const deadline = Date.now() + taskPollTimeoutMs;
  await wait(taskPollIntervalMs, signal);
  while (Date.now() < deadline) {
    assertActive(signal);
    const row = await apiFindTask(api, taskId, taskStartedAt);
    if (row) {
      const status = taskStatus(row);
      if (taskFailed(status)) throw new Error(`阿里妈妈商品报表任务 ${taskId} 失败（status=${status}）`);
      if (taskSucceeded(status)) return;
    }
    await wait(taskPollIntervalMs, signal);
  }
  throw new Error(`阿里妈妈商品报表任务 ${taskId} 在 7 分钟内未完成`);
}

async function apiGetDownloadUrl(api: APIRequestContext, taskId: string) {
  const url = new URL(TMALL_ALIMAMA_DOWNLOAD_URL);
  url.searchParams.set("taskId", taskId);
  url.searchParams.set("bizCode", "universalBP");
  const response = await api.get(url.toString(), {
    headers: { accept: "application/json, text/plain, */*", referer: "https://one.alimama.com/index.html" },
    timeout: 60_000,
  });
  return reportDownloadUrl(await readJsonResponse(response, "获取阿里妈妈商品报表下载链接"));
}

async function readAudit(store: TmallStore, directory: string) {
  const filePath = activeAuditPath(store.storeKey, directory);
  try {
    const audit = JSON.parse(await readFile(filePath, "utf8")) as DirectPromotionAudit;
    if (audit.version !== 1 || audit.protocol !== TMALL_DIRECT_PROMOTION_PROTOCOL
      || audit.storeKey !== store.storeKey || audit.shopName !== store.shopName || !audit.runId || !audit.stage
      || !audit.reportName || audit.reportName.length > 120
      || audit.taskId !== undefined && !/^[A-Za-z0-9_-]{1,128}$/.test(audit.taskId)) {
      throw new Error("推广直连活动清单结构或店铺身份无效");
    }
    return { filePath, audit };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

async function persistAudit(audit: DirectPromotionAudit, directory: string) {
  audit.updatedAt = new Date().toISOString();
  await mkdir(directory, { recursive: true });
  await writeJsonAtomic(activeAuditPath(audit.storeKey, directory), audit);
}

async function assertNoLegacyBusinessAction(store: TmallStore) {
  const filePath = activeAuditPath(store.storeKey, legacyAuditDirectory);
  try {
    const value = JSON.parse(await readFile(filePath, "utf8")) as unknown;
    if (directPromotionLegacyAuditBlocks(value)) {
      throw new Error("原 P 节点存在已提交/下载/导入的活动清单，拒绝切换到直连协议");
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
}

async function openPromotionPage(store: TmallStore) {
  await ensureTmallStoreAuthenticatedSession(store.storeKey);
  const browser = await connectPlaywrightBrowser(store.browser.debugPort);
  const context = browser.contexts()[0];
  if (!context) throw new Error(`${store.shopName} 独立 Chromium 没有可用上下文`);
  const pages = context.pages();
  const index = chooseTmallPromotionEntryPageIndex(pages.map((page) => page.url()));
  const page = index >= 0 ? pages[index]! : await context.newPage();
  page.setDefaultTimeout(15_000);
  return { browser, context, page };
}

async function runDirectPromotionDate(options: {
  store: TmallStore;
  baseUrl: string;
  request: typeof fetch;
  auditDirectory: string;
  plan: PromotionDatePlan;
  signal?: AbortSignal;
  downloadRequest?: typeof fetch;
}) {
  const { store, plan, signal } = options;
  assertTmallDirectPmStore(store.storeKey);
  if (plan.startDate !== plan.endDate || plan.dates.length !== 1 || plan.dates[0] !== plan.startDate) {
    throw new Error("推广直连报表必须按单个业务日执行");
  }
  assertActive(signal);
  await assertNoLegacyBusinessAction(store);
  await mkdir(options.auditDirectory, { recursive: true });
  const existing = await readAudit(store, options.auditDirectory);
  if (existing && (existing.audit.startDate !== plan.startDate || existing.audit.endDate !== plan.endDate)) {
    throw new Error(`存在未完成的推广直连清单 ${existing.audit.startDate}，拒绝覆盖为 ${plan.startDate}`);
  }
  const now = new Date().toISOString();
  const audit: DirectPromotionAudit = existing?.audit ?? {
    version: 1,
    protocol: TMALL_DIRECT_PROMOTION_PROTOCOL,
    runId: randomUUID(),
    storeKey: store.storeKey,
    shopName: store.shopName,
    baseUrl: options.baseUrl,
    startDate: plan.startDate,
    endDate: plan.endDate,
    startedAt: now,
    updatedAt: now,
    stage: "planned",
    reportName: `商品报表_${plan.startDate.replaceAll("-", "")}_${randomUUID().slice(0, 8)}`,
  };
  if (audit.baseUrl !== options.baseUrl) throw new Error("推广直连活动清单的运营系统地址不一致");
  await persistAudit(audit, options.auditDirectory);
  try {
    let file = audit.file;
    const resume = resumableStage(audit);
    if (resume === "report_submitting") {
      throw new Error("推广直连建任务响应未决，必须人工按活动清单核对 taskId，禁止自动重提");
    }
    if (["report_submitted", "downloading"].includes(resume) && !audit.taskId) {
      throw new Error(`推广直连恢复清单处于 ${resume} 但缺少 taskId，禁止重新创建任务`);
    }
    if (["downloaded", "importing"].includes(resume) && !audit.file) {
      throw new Error(`推广直连恢复清单处于 ${resume} 但缺少文件证据，禁止重新下载或创建任务`);
    }
    let api: APIRequestContext | undefined;
    let browser: Awaited<ReturnType<typeof connectPlaywrightBrowser>> | undefined;
    if (!file) {
      const connected = await openPromotionPage(store);
      browser = connected.browser;
      api = connected.context.request;
      try {
        const identifiers = audit.taskId
          ? null
          : await discoverTmallAlimamaIdentifiers(connected.page, store);
        if (audit.taskId) {
          await connected.page.goto(TMALL_PROMOTION_DOWNLOAD_LIST_URL, {
            waitUntil: "domcontentloaded",
            timeout: 60_000,
          });
          await waitForAlimamaIdentity(connected.page, store);
        }
        audit.stage = "session_ready";
        await persistAudit(audit, options.auditDirectory);
        if (!audit.taskId) {
          if (!identifiers) throw new Error("创建阿里妈妈商品报表缺少临时会话标识");
          audit.stage = "report_submitting";
          await persistAudit(audit, options.auditDirectory);
          audit.taskId = await apiCreateTask(api, identifiers, audit);
          audit.stage = "report_submitted";
          await persistAudit(audit, options.auditDirectory);
        }
        assertActive(signal);
        await apiWaitForTask(api, audit.taskId, audit.startedAt, signal);
        audit.stage = "downloading";
        await persistAudit(audit, options.auditDirectory);
        const downloadUrl = await apiGetDownloadUrl(api, audit.taskId);
        const targetPath = path.resolve(store.browser.downloadDir,
          `${safeSegment(store.shopName)}-商品报表-${plan.startDate}-${safeSegment(audit.runId)}.zip`);
        const exists = await stat(targetPath).then(() => true).catch(() => false);
        if (!exists) {
          const bytes = await boundedDownload(downloadUrl, signal, options.downloadRequest);
          await writeFile(targetPath, bytes, { flag: "wx" });
        }
        file = await inspectTmallPromotionFile(targetPath, store, plan);
        audit.file = file;
        audit.stage = "downloaded";
        await persistAudit(audit, options.auditDirectory);
      } finally {
        await browser.close().catch(() => undefined);
      }
    } else {
      const checked = await inspectTmallPromotionFile(file.filePath, store, plan);
      if (checked.sha256 !== file.sha256 || checked.size !== file.size || checked.rowCount !== file.rowCount) {
        throw new Error("推广直连恢复文件与活动清单证据不一致");
      }
      file = checked;
    }
    assertActive(signal);
    audit.stage = "importing";
    await persistAudit(audit, options.auditDirectory);
    const imported = await importTmallPromotionFile({
      baseUrl: options.baseUrl,
      store,
      plan,
      file,
      request: options.request,
    });
    await verifyTmallPromotionCoverageAfterImport({
      baseUrl: options.baseUrl,
      store,
      startDate: plan.startDate,
      endDate: plan.endDate,
      dates: plan.dates,
      request: options.request,
    });
    audit.stage = "completed";
    audit.importResult = imported;
    delete audit.error;
    delete audit.resumeStage;
    await persistAudit(audit, options.auditDirectory);
    const finalAuditPath = path.join(options.auditDirectory, `run-${safeSegment(audit.runId)}.json`);
    await rename(activeAuditPath(store.storeKey, options.auditDirectory), finalAuditPath);
    return {
      ok: true as const,
      stage: "promotion_day" as const,
      status: imported.status,
      storeKey: store.storeKey,
      shopName: store.shopName,
      date: plan.startDate,
      startDate: plan.startDate,
      endDate: plan.endDate,
      dates: plan.dates,
      metrics: "全部数据指标" as const,
      fileName: file.fileName,
      sha256: file.sha256,
      rowCount: file.rowCount,
      batchId: imported.batchId,
      warningCount: imported.warningCount,
      coverageConfirmed: true,
      auditPath: finalAuditPath,
    };
  } catch (error) {
    const current = audit.stage;
    audit.resumeStage = current === "failed" || current === "completed" ? audit.resumeStage : current;
    audit.stage = "failed";
    audit.error = (error instanceof Error ? error.message : String(error)).replace(/[\r\n]+/g, " ").slice(0, 500);
    await persistAudit(audit, options.auditDirectory).catch(() => undefined);
    throw error;
  }
}

export async function runTmallDirectPromotionStage(options: {
  storeKey?: string;
  baseUrl?: string;
  request?: typeof fetch;
  auditDirectory?: string;
  dates?: readonly string[];
  planStartDate?: string;
  planEndDate?: string;
  maximumDays?: number;
  signal?: AbortSignal;
  downloadRequest?: typeof fetch;
} = {}) {
  const storeKey = options.storeKey ?? TMALL_YIJIU_STORE_KEY;
  const store = await getTmallStore(storeKey);
  assertTmallDirectPmStore(store.storeKey);
  const baseUrl = normalizeLocalBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const auditDirectory = path.resolve(options.auditDirectory ?? defaultAuditDirectory);
  const request = options.request ?? fetch;
  await assertNoLegacyBusinessAction(store);
  return runTmallPromotionStage({
    storeKey,
    baseUrl,
    request,
    auditDirectory,
    dates: options.dates,
    planStartDate: options.planStartDate,
    planEndDate: options.planEndDate,
    pendingAuditDirectories: [auditDirectory],
    maximumDays: options.maximumDays,
    signal: options.signal,
    // Direct task IDs and their audit protocol remain owned by this runner.
    // Never interpret a direct audit as a UI report recovery manifest.
    resolveRecovery: async () => null,
    executeDate: (input) => runDirectPromotionDate({ ...input, downloadRequest: options.downloadRequest }),
  });
}
