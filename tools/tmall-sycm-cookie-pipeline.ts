import { createHash, randomUUID } from "node:crypto";
import { execFile as execFileCallback } from "node:child_process";
import { mkdir, readFile, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { isMainThread, parentPort, workerData } from "node:worker_threads";
import { isolatedHelperProtocol, isolatedHelperTokenHeader, isolatedRequestIdentity, serveIsolatedHelper, type SlotIdentity } from "./tmall-isolated-helper";

import { closeChromeBrowser, connectChromeBrowser } from "../lib/jackyun/cdp-client";
import { jackyunExportFirstActions, jackyunExportFirstPrefix, runJackyunExportFirstAction } from "./jackyun-export-first-pipeline";
import { recoverPreviousJackyunPreflight } from "../lib/jackyun/automatic-preflight-recovery";
import { writeJsonAtomic } from "../lib/jackyun/json-file";
import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import {
  getRegisteredTmallStore,
  getTmallStore,
  loadTmallStores,
  type TmallStore,
} from "../lib/netshop/tmall-store-registry";
import { createTmallDownloadReceipt } from "./tmall-download-receipt";
import {
  buildTmallSpuCoverageUrl,
  runTmallMultiStoreImport,
  shanghaiYesterday,
} from "./tmall-multi-store-import-runner";
import {
  ensureTmallStoreAuthenticatedSession,
  runTmallProductMasterStage,
} from "./tmall-product-master-export";
import { runTmallPagewiseProductMasterStage } from "./tmall-pagewise-product-master-export";
import { runTmallDirectProductMasterStage } from "./tmall-direct-product-master-export";
import {
  getTmallProductMasterCadenceDecision,
  parseTmallForceProductMasterHeader,
  recordTmallProductMasterCadenceSuccess,
  tmallForceProductMasterHeader,
} from "./tmall-product-master-cadence";
import { fetchTmallPromotionCoverage, runTmallPromotionStage } from "./tmall-promotion-export";
import { planTmallDailyGaps } from "./tmall-daily-gap-plan";
import { beginTmallBackfill, advanceTmallBackfill, publicTmallBackfill, type TmallBackfillState } from "./tmall-daily-backfill";
import { runTmallDirectPromotionStage } from "./tmall-direct-promotion-export";
import {
  isTmallDirectPmRoute,
  tmallDirectPmProtocolError,
  tmallDirectPmProtocolHeader,
  tmallDirectProductMasterRoute,
  tmallDirectPromotionRoute,
  type TmallDirectPmRoute,
} from "./tmall-yijiu-direct-pm-contract";
import {
  getJackyunProfileStatus,
  jackyunN8nFailureDetails,
  jackyunHelperRequestError,
  planJackyunN8nRun,
  publicJackyunPlan,
  runJackyunN8nPlan,
  verifyJackyunN8nPlan,
  type JackyunN8nPlan,
  type JackyunHelperRoute,
} from "./jackyun-n8n-pipeline";
import {
  getJdProfilesStatus,
  jdHelperRequestError,
  planJdN8nRun,
  publicJdPlan,
  runJdN8nPlan,
  verifyJdN8nPlan,
  type JdHelperRoute,
  type JdN8nPlan,
  type JdN8nStage,
} from "./jd-n8n-pipeline";
import { getJdStore, loadJdStores } from "../lib/jd/store-registry";
import {
  jdMarketHelperRequestError,
  loadJdMarketDailyConfig,
  planJdMarketDailyRun,
  publicJdMarketPlan,
  runJdMarketDailyPlan,
  verifyJdMarketDailyPlan,
  type JdMarketDailyPlan,
} from "./jd-market-ranking-daily";
import {
  jdPromotionEndDateHeader,
  jdPromotionHelperRequestError,
  jdPromotionStartDateHeader,
  jdPromotionStoreKeyHeader,
  parseJdPromotionDateHeader,
  parseJdPromotionStoreKeyHeader,
  planJdPromotionN8nRun,
  publicJdPromotionPlan,
  runJdPromotionN8nPlan,
  verifyJdPromotionN8nPlan,
  type JdPromotionHelperRoute,
  type JdPromotionN8nPlan,
  type JdPromotionN8nStage,
} from "./jd-promotion-n8n-pipeline";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifactDirectory = path.join(projectRoot, "outputs", "tmall-sycm-cookie-pipeline");
const maximumDownloadBytes = 25 * 1024 * 1024;
export const maximumDaysPerRun = 1;
export const helperInactivityTimeoutMs = 2 * 60_000;
export const tmallPromotionStageTimeoutMs = 10 * 60_000;
export const n8nExecutionIdHeader = "x-teruisi-n8n-execution-id";
export const workflowCoordinationKeyHeader = "x-teruisi-workflow-key";
export const workflowCoordinationAttemptHeader = "x-teruisi-coordination-attempt";
export const tmallStoreKeyHeader = "x-teruisi-tmall-store-key";
export const tmallPlanStartDateHeader = "x-teruisi-tmall-plan-start-date";
export const tmallPlanEndDateHeader = "x-teruisi-tmall-plan-end-date";
export const maximumWorkflowCoordinationAttempts = 72;
export const jdSilentNoWindowHeader = "x-teruisi-jd-silent-no-window";
export const jdMarketResumeRunIdHeader = "x-teruisi-jd-market-resume-run-id";

const execFile = promisify(execFileCallback);

export function normalizeTmallStoreKey(value: string | string[] | undefined) {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase();
  return /^[a-z0-9][a-z0-9-]{0,63}$/.test(normalized) ? normalized : null;
}

function parseOptionalTmallPlanDateHeader(value: string | string[] | undefined) {
  if (value === undefined || value === "") return undefined;
  if (typeof value !== "string" || !validDate(value)) throw new Error("天猫显式计划日期请求头无效");
  return value;
}

export function parseTmallPlanDateRangeHeaders(
  startValue: string | string[] | undefined,
  endValue: string | string[] | undefined,
) {
  const startDate = parseOptionalTmallPlanDateHeader(startValue);
  const endDate = parseOptionalTmallPlanDateHeader(endValue);
  if (startDate === undefined && endDate === undefined) return null;
  if (!startDate || !endDate) throw new Error("天猫显式计划日期必须同时提供开始日与结束日");
  if (startDate > endDate || endDate > shanghaiYesterday()) throw new Error("天猫显式计划日期范围无效");
  return { startDate, endDate };
}

export function tmallCookiePointerFile(storeKey: string) {
  const normalized = normalizeTmallStoreKey(storeKey);
  if (!normalized) throw new Error("天猫店铺键无效");
  return path.join(projectRoot, ".runtime", `${normalized}-sycm-cookie-path.txt`);
}

export function parseJdSilentNoWindowHeader(value: string | string[] | undefined) {
  if (value === undefined || value === "0") return false;
  if (value === "1") return true;
  throw new Error("京东静默窗口模式请求头无效。");
}

export function parseJdMarketResumeRunIdHeader(value: string | string[] | undefined) {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !/^[A-Za-z0-9._-]{1,96}$/.test(value)) {
    throw new Error("京东市场榜单显式恢复运行编号请求头无效。");
  }
  return value;
}
const sycmOrigin = "https://sycm.taobao.com";
const sycmExportPath = "/cc/item/view/excel/top.json";
const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36";

type PipelineCommand = "master" | "plan" | "fetch" | "import" | "promotion" | "serve";
export type HelperStage = "ready" | "planned" | "fetched" | "imported" | "promoted" | "running" | "executed" | "completed" | "failed";
export type HelperRoute = "/plan" | "/plan-backfill" | "/next-day" | "/fetch" | "/import" | "/promotion" | "/product-master" | TmallDirectPmRoute;
export type CoordinatedWorkflow = "tmall" | "jackyun" | "jd" | "jd-market" | "jd-promotion";
export type CookieSourceStatus = "ready" | "missing" | "invalid";
export type TmallProfileStatus = "ready" | "missing" | "invalid";

type TmallPipelineErrorCode = "SOURCE_NOT_READY" | "INVALID_SOURCE_FILE" | "DOWNLOAD_FAILED";

class TmallPipelineError extends Error {
  readonly code: TmallPipelineErrorCode;

  constructor(code: TmallPipelineErrorCode, message: string) {
    super(message);
    this.name = "TmallPipelineError";
    this.code = code;
  }
}

type PipelinePlan = {
  version: 1;
  runId: string;
  generatedAt: string;
  baseUrl: string;
  storeKey: string;
  shopName: string;
  startDate: string;
  endDate: string;
  dates: string[];
  promotionDates: string[];
  truncated: boolean;
  coverageAuditPath: string;
};

type DownloadedFile = {
  businessDate: string;
  fileName: string;
  filePath: string;
  size: number;
  sha256: string;
  rowCount: number;
  reusedExistingFile: boolean;
};

type PipelineManifest = {
  version: 1;
  runId: string;
  generatedAt: string;
  status: "downloaded" | "failed";
  baseUrl: string;
  storeKey: string;
  shopName: string;
  dates: string[];
  authenticationSource?: "dedicated_browser" | "cookie_file";
  files: DownloadedFile[];
  errors: Array<{ businessDate: string; code: string; message: string }>;
};

type ParsedCookie = {
  header: string;
  values: Map<string, string>;
};

export type ChromeStorageCookie = {
  name: string;
  value: string;
  domain: string;
  path: string;
  secure?: boolean;
  expires?: number;
};

type CoveragePayload = {
  requestedPeriod?: { startDate?: string; endDate?: string };
  coverage?: { actualDates?: unknown };
};

function cliValue(argv: string[], name: string) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

function validDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

function inside(directory: string, filePath: string) {
  const relative = path.relative(directory, filePath);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function normalizeLocalBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname)) {
    throw new Error("本工作流只允许连接本机运营系统，未授权生产或远程导入");
  }
  return url.toString().replace(/\/$/, "");
}

function canonicalDownloadName(businessDate: string) {
  return `【生意参谋平台】商品_全部_${businessDate}_${businessDate}.xls`;
}

export function encodeArtifactPath(filePath: string) {
  return Buffer.from(filePath, "utf8").toString("base64");
}

export function decodeArtifactPath(encoded: string) {
  const normalized = encoded.trim();
  if (!normalized || !/^[A-Za-z0-9+/]+={0,2}$/.test(normalized) || normalized.length % 4 !== 0) {
    throw new Error("工件路径不是有效的 Base64");
  }
  const bytes = Buffer.from(normalized, "base64");
  if (bytes.length === 0 || bytes.toString("base64") !== normalized) throw new Error("工件路径不是规范的 Base64");
  const decoded = bytes.toString("utf8");
  if (!decoded || Buffer.from(decoded, "utf8").compare(bytes) !== 0 || /[\u0000-\u001f\u007f]/.test(decoded)) {
    throw new Error("工件路径编码无效");
  }
  return path.resolve(decoded);
}

export function parseCookieHeader(rawValue: string): ParsedCookie {
  let header = rawValue.replace(/^\uFEFF/, "").trim();
  if (/^cookie\s*:/i.test(header)) header = header.replace(/^cookie\s*:\s*/i, "");
  if (!header || header.length > 32_768 || /[\r\n\u0000]/.test(header)) {
    throw new Error("Cookie 文件必须是单行、非空且不超过 32KB 的请求头值");
  }
  const values = new Map<string, string>();
  for (const part of header.split(";")) {
    const separator = part.indexOf("=");
    if (separator <= 0) throw new Error("Cookie 文件包含无效键值段");
    const name = part.slice(0, separator).trim();
    const value = part.slice(separator + 1).trim();
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name) || !value || /[\u0000-\u001f\u007f]/.test(value)) {
      throw new Error("Cookie 文件包含无效键名或控制字符");
    }
    if (values.has(name)) throw new Error(`Cookie 文件包含重复键: ${name}`);
    values.set(name, value);
  }
  if (values.size < 2) throw new Error("Cookie 文件不像完整的浏览器请求头");
  return { header, values };
}

function cookieDomainMatches(host: string, domain: string) {
  const normalized = domain.trim().replace(/^\./, "").toLowerCase();
  return normalized.length > 0 && (host === normalized || host.endsWith(`.${normalized}`));
}

export function sycmCookieHeaderFromChromeStorage(
  cookies: readonly ChromeStorageCookie[],
  nowMs = Date.now(),
) {
  const host = "sycm.taobao.com";
  const candidates = cookies.filter((cookie) => (
    cookie.name.length > 0
    && cookie.value.length > 0
    && cookieDomainMatches(host, cookie.domain)
    && sycmExportPath.startsWith(cookie.path || "/")
    && (!(cookie.expires && cookie.expires > 0) || cookie.expires * 1000 > nowMs)
  )).sort((left, right) => (
    (right.path?.length ?? 0) - (left.path?.length ?? 0)
    || Number(right.domain.replace(/^\./, "").toLowerCase() === host)
      - Number(left.domain.replace(/^\./, "").toLowerCase() === host)
  ));
  const selected = new Map<string, string>();
  for (const cookie of candidates) {
    if (!selected.has(cookie.name)) selected.set(cookie.name, cookie.value);
  }
  return [...selected].map(([name, value]) => `${name}=${value}`).join("; ");
}

function decodedCookieValue(values: Map<string, string>, name: string) {
  const value = values.get(name);
  if (!value) return "";
  try {
    return decodeURIComponent(value.replace(/\+/g, "%20"));
  } catch {
    throw new Error(`Cookie 键 ${name} 不是有效的 URL 编码`);
  }
}

export function assertCookieMatchesStore(cookie: ParsedCookie, store: Pick<TmallStore, "shopName">) {
  const required = ["_tb_token_", "cookie2", "unb", "sn"];
  const missing = required.filter((name) => !cookie.values.get(name));
  if (missing.length) throw new Error(`Cookie 缺少必要登录键: ${missing.join(", ")}`);
  const expectedShop = store.shopName.replace(/^天猫-?/, "");
  const signedInName = decodedCookieValue(cookie.values, "sn");
  if (signedInName !== expectedShop && !signedInName.startsWith(`${expectedShop}:`)) {
    throw new Error("Cookie 登录身份与注册店铺不一致，已阻止跨店下载");
  }
  return signedInName;
}

async function cookieFromDedicatedBrowser(store: TmallStore) {
  const browser = await connectChromeBrowser(store.browser.debugPort).catch(() => null);
  if (!browser) return null;
  try {
    const result = await browser.send<{ cookies?: ChromeStorageCookie[] }>("Storage.getCookies", {}, 10_000);
    const header = sycmCookieHeaderFromChromeStorage(result.cookies ?? []);
    if (!header) throw new Error("店铺独立 Chrome 当前没有可用于生意参谋的登录 Cookie");
    const cookie = parseCookieHeader(header);
    assertCookieMatchesStore(cookie, store);
    return cookie;
  } finally {
    browser.close();
  }
}

export function buildSycmExportUrl(businessDate: string, token: string) {
  if (!validDate(businessDate)) throw new Error("生意参谋导出日期必须是 YYYY-MM-DD");
  const params = new URLSearchParams({
    dateType: "day",
    pageSize: "10",
    page: "1",
    order: "desc",
    orderBy: "payAmt",
    device: "0",
    sellerType: "-1",
    indexCode: "payAmt",
    dateRange: `${businessDate}|${businessDate}`,
    token,
  });
  return `${sycmOrigin}${sycmExportPath}?${params}`;
}

export function isLegacyXls(bytes: Uint8Array) {
  return bytes.length >= 4 && bytes[0] === 0xd0 && bytes[1] === 0xcf && bytes[2] === 0x11 && bytes[3] === 0xe0;
}

export function classifySycmInspectionErrors(errors: Array<{ code?: string }>) {
  const codes = [...new Set(errors.map((item) => item.code ?? "UNKNOWN"))].slice(0, 8);
  const sourceNotReadyCodes = new Set(["NO_DATA_ROWS", "MISSING_EXPECTED_DATES"]);
  const sourceNotReady = codes.length === sourceNotReadyCodes.size
    && codes.every((code) => sourceNotReadyCodes.has(code));
  return sourceNotReady
    ? {
        code: "SOURCE_NOT_READY" as const,
        message: `SOURCE_NOT_READY：生意参谋目标日尚未返回可导入数据 (${codes.join(", ")})`,
      }
    : {
        code: "INVALID_SOURCE_FILE" as const,
        message: `生意参谋 XLS 内容校验失败: ${codes.join(", ")}`,
      };
}

function nonXlsError(bytes: Uint8Array, status: number) {
  let code: string | number | undefined;
  let message = "";
  try {
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as { code?: unknown; msg?: unknown; message?: unknown };
    if (typeof payload.code === "string" || typeof payload.code === "number") code = payload.code;
    message = typeof payload.msg === "string" ? payload.msg : typeof payload.message === "string" ? payload.message : "";
  } catch {
    // 非 JSON 内容仍按魔数校验失败处理，不回显原文。
  }
  if (String(code) === "5810" || /login|登录/i.test(message)) return new Error("生意参谋 Cookie 登录态已失效，请更新 Cookie 文件后重跑");
  return new Error(`生意参谋返回的不是 XLS (HTTP ${status}${code === undefined ? "" : `, code=${code}`})`);
}

async function fetchSycmDay(
  store: TmallStore,
  cookie: ParsedCookie,
  businessDate: string,
  request: typeof fetch = fetch,
) {
  const token = cookie.values.get("XSRF-TOKEN") ?? cookie.values.get("_tb_token_") ?? "";
  const response = await request(buildSycmExportUrl(businessDate, token), {
    headers: {
      Cookie: cookie.header,
      "User-Agent": userAgent,
      Referer: `${sycmOrigin}/cc/item_rank?dateRange=${businessDate}%7C${businessDate}&dateType=day`,
      Origin: sycmOrigin,
      Accept: "application/json, text/plain, */*",
      "Accept-Language": "zh-CN,zh;q=0.9",
      "x-xsrf-token": token,
      "X-Requested-With": "XMLHttpRequest",
    },
    redirect: "manual",
    signal: AbortSignal.timeout(60_000),
  });
  const contentLength = Number(response.headers.get("content-length") ?? 0);
  if (contentLength > maximumDownloadBytes) throw new Error("生意参谋响应超过 25MB 上限");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!response.ok || !isLegacyXls(bytes)) throw nonXlsError(bytes, response.status);
  if (bytes.byteLength > maximumDownloadBytes) throw new Error("生意参谋响应超过 25MB 上限");
  // Node/undici 会把部分 Content-Disposition 中文字节按 Latin-1 暴露，直接采用受控单日命名，
  // 避免 mojibake 文件名，同时保留导入器依赖的精确起止日期。
  const fileName = canonicalDownloadName(businessDate);
  const inspection = await inspectTmallImportBytes({
    source: "tmall_product_daily",
    bytes,
    fileName,
    fileSizeBytes: bytes.byteLength,
    shopName: store.shopName,
    expectedStartDate: businessDate,
    expectedEndDate: businessDate,
  });
  if (inspection.errors.length) {
    const failure = classifySycmInspectionErrors(inspection.errors);
    throw new TmallPipelineError(failure.code, failure.message);
  }
  if (inspection.totals.rowCount <= 0) throw new Error("生意参谋 XLS 没有可导入的商品行");
  return { bytes, fileName, rowCount: inspection.totals.rowCount };
}

export async function saveDownload(
  store: Pick<TmallStore, "browser">,
  runId: string,
  businessDate: string,
  bytes: Uint8Array,
  fileName: string,
) {
  await mkdir(store.browser.downloadDir, { recursive: true });
  const runDirectory = path.resolve(
    store.browser.downloadDir,
    ".tmall-sycm-runs",
    createHash("sha256").update(runId).digest("hex").slice(0, 24),
  );
  if (!inside(store.browser.downloadDir, runDirectory)) throw new Error("本轮下载目录越过店铺独立目录");
  await mkdir(runDirectory, { recursive: true });
  const targetPath = path.resolve(runDirectory, fileName);
  if (!inside(store.browser.downloadDir, targetPath)) throw new Error("下载文件名越过店铺独立目录");
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const existing = await stat(targetPath).catch(() => null);
  if (existing) {
    if (!existing.isFile() || existing.size !== bytes.byteLength) {
      throw new Error(`日期 ${businessDate} 已存在同名不同内容文件，禁止静默覆盖`);
    }
    const existingBytes = new Uint8Array(await readFile(targetPath));
    if (createHash("sha256").update(existingBytes).digest("hex") !== sha256) {
      throw new Error(`日期 ${businessDate} 已存在同名不同内容文件，禁止静默覆盖`);
    }
    return { filePath: targetPath, sha256, reusedExistingFile: true };
  }
  const tempPath = `${targetPath}.${runId}.${process.pid}.part`;
  await writeFile(tempPath, bytes, { flag: "wx" });
  try {
    await rename(tempPath, targetPath);
  } catch (error) {
    await rm(tempPath, { force: true });
    throw error;
  }
  return { filePath: targetPath, sha256, reusedExistingFile: false };
}

async function artifactPathFromBase64(encoded: string, expectedPrefix: string) {
  await mkdir(artifactDirectory, { recursive: true });
  const [root, decoded] = await Promise.all([realpath(artifactDirectory), realpath(decodeArtifactPath(encoded))]);
  if (!inside(root, decoded) || !path.basename(decoded).startsWith(expectedPrefix) || !decoded.endsWith(".json")) {
    throw new Error("工件路径不属于本工作流的受控输出目录");
  }
  return decoded;
}

function validatePlan(plan: PipelinePlan) {
  if (plan.version !== 1 || !plan.runId || !plan.storeKey || !plan.shopName || !validDate(plan.startDate)
    || !validDate(plan.endDate) || !Array.isArray(plan.dates) || plan.dates.length > maximumDaysPerRun
    || plan.dates.some((date) => !validDate(date) || date < plan.startDate || date > plan.endDate)
    || new Set(plan.dates).size !== plan.dates.length
    || !Array.isArray(plan.promotionDates) || plan.promotionDates.length > maximumDaysPerRun
    || plan.promotionDates.some((date) => !validDate(date) || date < plan.startDate || date > plan.endDate)
    || new Set(plan.promotionDates).size !== plan.promotionDates.length
    || plan.dates.some((date) => !plan.promotionDates.includes(date))) {
    throw new Error("目标日计划格式无效");
  }
  return plan;
}

function validateManifest(manifest: PipelineManifest) {
  if (manifest.version !== 1 || !manifest.runId || manifest.status !== "downloaded" || !manifest.storeKey || !manifest.shopName
    || !Array.isArray(manifest.dates) || manifest.dates.length > maximumDaysPerRun
    || manifest.dates.some((date) => !validDate(date)) || new Set(manifest.dates).size !== manifest.dates.length
    || !Array.isArray(manifest.files) || manifest.files.length !== manifest.dates.length || manifest.errors.length !== 0) {
    throw new Error("下载清单格式或状态无效");
  }
  const fileDates = manifest.files.map((item) => item.businessDate);
  if (new Set(fileDates).size !== fileDates.length || manifest.dates.some((date) => !fileDates.includes(date))) {
    throw new Error("下载清单的日期与文件不一一对应");
  }
  return manifest;
}

async function getActualDates(baseUrl: string, store: TmallStore, startDate: string, endDate: string) {
  const response = await fetch(buildTmallSpuCoverageUrl(baseUrl, store, startDate, endDate), { signal: AbortSignal.timeout(30_000) });
  const payload = await response.json().catch(() => null) as CoveragePayload | null;
  const actualDates = payload?.coverage?.actualDates;
  if (!response.ok || payload?.requestedPeriod?.startDate !== startDate || payload.requestedPeriod.endDate !== endDate
    || !Array.isArray(actualDates) || actualDates.some((date) => typeof date !== "string" || !validDate(date))) {
    throw new Error(`无法回查 ${store.shopName} 的 SPU 日期覆盖 (HTTP ${response.status})`);
  }
  return actualDates as string[];
}

async function planCommand(argv: string[]) {
  const storeKey = cliValue(argv, "--store-key") ?? "tmall-yijiu";
  const store = await getTmallStore(storeKey);
  const endDate = cliValue(argv, "--end-date") ?? shanghaiYesterday();
  const startDate = cliValue(argv, "--start-date") ?? store.initialStartDate;
  if (!startDate || !store.initialStartDate || startDate < store.initialStartDate
    || !validDate(startDate) || !validDate(endDate) || startDate > endDate || endDate > shanghaiYesterday()) {
    throw new Error("目标导入日期必须位于店铺注册起始日至昨天之间");
  }
  const requestedMaximum = Number(cliValue(argv, "--max-days") ?? maximumDaysPerRun);
  if (!Number.isInteger(requestedMaximum) || requestedMaximum < 1 || requestedMaximum > maximumDaysPerRun) {
    throw new Error(`--max-days 必须是 1..${maximumDaysPerRun} 的整数`);
  }
  const baseUrl = normalizeLocalBaseUrl(cliValue(argv, "--base-url") ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const coverage = await fetchTmallPromotionCoverage({ baseUrl, store, startDate, endDate });
  const gaps = planTmallDailyGaps({ startDate, endDate, ...coverage, maximumDays: requestedMaximum });
  const dates = gaps.productDownloadDates;
  const runId = randomUUID();
  const coverageAuditPath = path.join(artifactDirectory, `coverage-${runId}.json`);
  await writeJsonAtomic(coverageAuditPath, { storeKey: store.storeKey, shopName: store.shopName,
    startDate, endDate, generatedAt: new Date().toISOString(), ...coverage, ...gaps });
  const plan: PipelinePlan = {
    version: 1,
    runId,
    generatedAt: new Date().toISOString(),
    baseUrl,
    storeKey: store.storeKey,
    shopName: store.shopName,
    startDate,
    endDate,
    dates,
    promotionDates: gaps.selectedDates,
    truncated: gaps.truncated,
    coverageAuditPath,
  };
  const planPath = path.join(artifactDirectory, `plan-${runId}.json`);
  await writeJsonAtomic(planPath, plan);
  return { ok: true, stage: "plan", planPath, planPathBase64: encodeArtifactPath(planPath), dates,
    startDate, endDate, promotionDates: gaps.selectedDates, ...gaps };
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function configuredCookieFilePath(storeKey: string) {
  const legacyEnvironmentFile = storeKey === "tmall-yijiu"
    ? process.env.TMALL_SYCM_COOKIE_FILE
    : undefined;
  return (legacyEnvironmentFile
    ?? await readFile(tmallCookiePointerFile(storeKey), "utf8").catch(() => "")).trim();
}

async function cookieFromConfiguredFile(store: TmallStore) {
  const cookieFile = await configuredCookieFilePath(store.storeKey);
  if (!cookieFile || !path.isAbsolute(cookieFile)) {
    const fallback = store.storeKey === "tmall-yijiu"
      ? "TMALL_SYCM_COOKIE_FILE 或本店 .runtime 指针"
      : "本店 .runtime 指针";
    throw new Error(`店铺独立 Chromium 不可用，且未通过${fallback}提供绝对 Cookie 文件路径`);
  }
  if (await getCookieSourceStatus(cookieFile) !== "ready") {
    throw new Error("Cookie 原文件不存在或不是普通文件，请更新本机 .runtime 指针后重试");
  }
  const cookie = parseCookieHeader(await readFile(cookieFile, "utf8"));
  assertCookieMatchesStore(cookie, store);
  return cookie;
}

export async function loadSycmCookieForStore(store: TmallStore) {
  const browserCookie = await cookieFromDedicatedBrowser(store);
  if (browserCookie) return { cookie: browserCookie, source: "dedicated_browser" as const };
  return { cookie: await cookieFromConfiguredFile(store), source: "cookie_file" as const };
}

export async function getCookieSourceStatus(
  cookieFile?: string,
  storeKey = "tmall-yijiu",
): Promise<CookieSourceStatus> {
  const resolvedCookieFile = cookieFile ?? await configuredCookieFilePath(storeKey);
  if (!resolvedCookieFile) return "missing";
  if (!path.isAbsolute(resolvedCookieFile)) return "invalid";
  return stat(resolvedCookieFile)
    .then((entry) => entry.isFile() ? "ready" as const : "invalid" as const)
    .catch(() => "missing" as const);
}

export async function getTmallProfileStatus(store: Pick<TmallStore, "browser">): Promise<TmallProfileStatus> {
  const { executablePath, userDataDir, profileName, profileDir } = store.browser;
  if (!executablePath || !userDataDir || !profileName) return "invalid";
  const [executable, userData, profile, localState] = await Promise.all([
    stat(executablePath).catch(() => null),
    stat(userDataDir).catch(() => null),
    stat(profileDir).catch(() => null),
    stat(path.join(userDataDir, "Local State")).catch(() => null),
  ]);
  if (!executable || !userData || !profile || !localState) return "missing";
  return executable.isFile() && userData.isDirectory() && profile.isDirectory() && localState.isFile()
    ? "ready"
    : "invalid";
}

export async function getTmallProfilesStatus(
  stores: readonly Pick<TmallStore, "browser">[],
): Promise<TmallProfileStatus> {
  if (stores.length === 0) return "invalid";
  const statuses = await Promise.all(stores.map(getTmallProfileStatus));
  if (statuses.includes("invalid")) return "invalid";
  if (statuses.includes("missing")) return "missing";
  return "ready";
}

export function shouldLoadCookieForPlan(dates: readonly string[]) {
  return dates.length > 0;
}

export function createInitialDownloadManifest(
  plan: Pick<PipelinePlan, "runId" | "baseUrl" | "dates">,
  store: Pick<TmallStore, "storeKey" | "shopName">,
  generatedAt = new Date().toISOString(),
): PipelineManifest {
  return {
    version: 1,
    runId: plan.runId,
    generatedAt,
    status: "downloaded",
    baseUrl: plan.baseUrl,
    storeKey: store.storeKey,
    shopName: store.shopName,
    dates: [...plan.dates],
    files: [],
    errors: [],
  };
}

export function getTmallPromotionStageOptions(
  storeKey = "tmall-yijiu",
  dates?: readonly string[],
  planRange?: { startDate: string; endDate: string },
) {
  const normalized = normalizeTmallStoreKey(storeKey);
  if (!normalized) throw new Error("天猫店铺键无效");
  return {
    storeKey: normalized,
    maximumDays: maximumDaysPerRun,
    ...(dates ? { dates: [...dates] } : {}),
    ...(planRange ? { planStartDate: planRange.startDate, planEndDate: planRange.endDate } : {}),
  };
}

async function fetchCommand(argv: string[], expectedStoreKey?: string) {
  const encodedPlan = cliValue(argv, "--plan-base64");
  if (!encodedPlan) throw new Error("fetch 阶段缺少 --plan-base64");
  const planPath = await artifactPathFromBase64(encodedPlan, "plan-");
  const plan = validatePlan(JSON.parse(await readFile(planPath, "utf8")) as PipelinePlan);
  if (expectedStoreKey && plan.storeKey !== expectedStoreKey) {
    throw new Error("计划店铺与当前 helper owner 不一致");
  }
  const store = await getTmallStore(plan.storeKey);
  if (plan.shopName !== store.shopName || plan.baseUrl !== normalizeLocalBaseUrl(plan.baseUrl)) throw new Error("计划店铺或系统地址与注册表不一致");
  const manifestPath = path.join(artifactDirectory, `manifest-${plan.runId}.json`);
  const manifest = createInitialDownloadManifest(plan, store);
  const files = manifest.files;
  if (!shouldLoadCookieForPlan(plan.dates)) {
    await writeJsonAtomic(manifestPath, manifest);
    return {
      ok: true,
      stage: "fetch",
      manifestPath,
      manifestPathBase64: encodeArtifactPath(manifestPath),
      downloaded: 0,
      rows: 0,
      reusedExistingFiles: 0,
    };
  }

  const authentication = await loadSycmCookieForStore(store);
  const cookie = authentication.cookie;
  manifest.authenticationSource = authentication.source;

  try {
    for (let index = 0; index < plan.dates.length; index += 1) {
      const businessDate = plan.dates[index]!;
      const downloaded = await fetchSycmDay(store, cookie, businessDate);
      const saved = await saveDownload(store, plan.runId, businessDate, downloaded.bytes, downloaded.fileName);
      files.push({
        businessDate,
        fileName: downloaded.fileName,
        filePath: saved.filePath,
        size: downloaded.bytes.byteLength,
        sha256: saved.sha256,
        rowCount: downloaded.rowCount,
        reusedExistingFile: saved.reusedExistingFile,
      });
      if (index + 1 < plan.dates.length) await delay(1_200 + Math.floor(Math.random() * 1_201));
    }
  } catch (error) {
    manifest.status = "failed";
    const businessDate = plan.dates[files.length] ?? "unknown";
    const code = error instanceof TmallPipelineError ? error.code : "DOWNLOAD_FAILED";
    manifest.errors.push({ businessDate, code, message: error instanceof Error ? error.message : String(error) });
    await writeJsonAtomic(manifestPath, manifest);
    throw error;
  }
  await writeJsonAtomic(manifestPath, manifest);
  return {
    ok: true,
    stage: "fetch",
    manifestPath,
    manifestPathBase64: encodeArtifactPath(manifestPath),
    downloaded: files.length,
    rows: files.reduce((sum, item) => sum + item.rowCount, 0),
    reusedExistingFiles: files.filter((item) => item.reusedExistingFile).length,
  };
}

async function importCommand(argv: string[], expectedStoreKey?: string) {
  const encodedManifest = cliValue(argv, "--manifest-base64");
  if (!encodedManifest) throw new Error("import 阶段缺少 --manifest-base64");
  const manifestPath = await artifactPathFromBase64(encodedManifest, "manifest-");
  const manifest = validateManifest(JSON.parse(await readFile(manifestPath, "utf8")) as PipelineManifest);
  if (expectedStoreKey && manifest.storeKey !== expectedStoreKey) {
    throw new Error("下载清单店铺与当前 helper owner 不一致");
  }
  const store = await getTmallStore(manifest.storeKey);
  const baseUrl = normalizeLocalBaseUrl(manifest.baseUrl);
  if (manifest.shopName !== store.shopName) throw new Error("下载清单店铺与注册表不一致");

  const receiptPaths: string[] = [];
  for (const item of manifest.files) {
    const filePath = path.resolve(item.filePath);
    if (!inside(store.browser.downloadDir, filePath) || path.basename(filePath) !== item.fileName
      || item.fileName !== canonicalDownloadName(item.businessDate) || !/^[a-f0-9]{64}$/.test(item.sha256)
      || !Number.isFinite(item.size) || item.size <= 0 || item.size > maximumDownloadBytes) {
      throw new Error(`下载清单文件身份无效: ${item.businessDate}`);
    }
    const info = await stat(filePath);
    if (!info.isFile() || info.size !== item.size) throw new Error(`下载文件缺失或大小变化: ${item.businessDate}`);
    const bytes = new Uint8Array(await readFile(filePath));
    if (createHash("sha256").update(bytes).digest("hex") !== item.sha256) throw new Error(`下载文件哈希变化: ${item.businessDate}`);
    const receipt = await createTmallDownloadReceipt({ storeKey: store.storeKey, businessDate: item.businessDate, filePath });
    receiptPaths.push(receipt.receiptPath);
  }

  const dates = [...manifest.dates].sort();
  if (dates.length === 0) {
    const resultPath = path.join(artifactDirectory, `result-${manifest.runId}.json`);
    const summary = {
      ok: true,
      stage: "import",
      resultPath,
      dates: [],
      counts: {},
      targetDateCount: 0,
      processedDateCount: 0,
      coverageConfirmed: true,
    };
    await writeJsonAtomic(resultPath, summary);
    return summary;
  }
  const imported = await runTmallMultiStoreImport({
    baseUrl,
    storeKey: store.storeKey,
    endDate: dates.at(-1)!,
    dates,
    receiptPaths,
    dryRun: false,
  });
  const actualDates = await getActualDates(baseUrl, store, dates[0]!, dates.at(-1)!);
  const missingAfterImport = dates.filter((date) => !actualDates.includes(date));
  const failedItems = imported.audit.items.filter((item) => !["imported", "duplicate", "completed_with_warnings"].includes(item.status));
  const counts = Object.fromEntries(["imported", "duplicate", "completed_with_warnings", "waiting_download", "failed"].map((status) => [
    status,
    imported.audit.items.filter((item) => item.status === status).length,
  ]));
  const ok = failedItems.length === 0 && missingAfterImport.length === 0;
  const resultPath = path.join(artifactDirectory, `result-${manifest.runId}.json`);
  const summary = {
    ok,
    stage: "import",
    resultPath,
    importAuditPath: imported.auditPath,
    dates,
    counts,
    targetDateCount: dates.length,
    processedDateCount: imported.audit.items.length,
    coverageConfirmed: missingAfterImport.length === 0,
    missingAfterImport,
  };
  await writeJsonAtomic(resultPath, summary);
  if (!ok) throw new Error("导入未全部完成或覆盖回查未命中，详见结果清单");
  return summary;
}

function integerPort(value: string | undefined) {
  const port = Number(value ?? 5791);
  if (!Number.isInteger(port) || port < 1024 || port > 65_535) throw new Error("--port 必须是 1024..65535 的整数");
  return port;
}

export function normalizeN8nExecutionId(value: string | string[] | undefined) {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized.length >= 1 && normalized.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(normalized)
    ? normalized
    : null;
}

export function parseWorkflowCoordinationKey(value: string | string[] | undefined): CoordinatedWorkflow | null {
  return typeof value === "string" && ["tmall", "jackyun", "jd", "jd-market", "jd-promotion"].includes(value)
    ? value as CoordinatedWorkflow
    : null;
}

export function parseWorkflowCoordinationAttempt(value: string | string[] | undefined) {
  if (value === undefined) return 0;
  if (typeof value !== "string" || !/^\d{1,3}$/.test(value)) return null;
  const attempt = Number(value);
  return Number.isSafeInteger(attempt) && attempt >= 0 ? attempt : null;
}

export function workflowCoordinationWaitExpired(
  coordinationAttempt: number,
  coordinationStatus: "granted" | "waiting",
) {
  return coordinationStatus === "waiting" && coordinationAttempt >= maximumWorkflowCoordinationAttempts;
}

export function workflowClaimDecision(input: {
  stage: HelperStage;
  busy: boolean;
  activeWorkflow: CoordinatedWorkflow | null;
  requestedWorkflow: CoordinatedWorkflow | null;
  requestExecutionId: string | null;
  claimedExecutionId: string | null;
  requestedTmallStoreKey?: string | null;
  claimedTmallStoreKey?: string | null;
}) {
  if (!input.requestedWorkflow) return { error: "missing_or_invalid_workflow_key" as const };
  if (!input.requestExecutionId) return { error: "missing_or_invalid_execution_id" as const };
  if (input.requestedWorkflow === "tmall") {
    if (!input.requestedTmallStoreKey) return { error: "missing_or_invalid_tmall_store_key" as const };
    if (input.claimedExecutionId === input.requestExecutionId
      && input.claimedTmallStoreKey
      && input.claimedTmallStoreKey !== input.requestedTmallStoreKey) {
      return { error: "tmall_store_context_mismatch" as const };
    }
  }
  if (input.activeWorkflow === input.requestedWorkflow && input.claimedExecutionId === input.requestExecutionId) {
    return { coordinationStatus: "granted" as const, shouldClaim: false };
  }
  if (input.activeWorkflow !== null) {
    return {
      coordinationStatus: "waiting" as const,
      reason: "active_workflow" as const,
      activeWorkflow: input.activeWorkflow,
    };
  }
  if (input.busy) {
    return { coordinationStatus: "waiting" as const, reason: "pipeline_busy" as const, activeWorkflow: null };
  }
  if (input.stage !== "ready" || input.claimedExecutionId !== null) {
    return {
      coordinationStatus: "waiting" as const,
      reason: "helper_not_ready" as const,
      activeWorkflow: null,
    };
  }
  return { coordinationStatus: "granted" as const, shouldClaim: true };
}

export function tmallStoreContextError(
  requestStoreKey: string | null,
  claimedStoreKey: string | null,
) {
  if (!requestStoreKey) return { error: "missing_or_invalid_tmall_store_key" as const };
  if (!claimedStoreKey) return { error: "tmall_store_not_claimed" as const };
  return requestStoreKey === claimedStoreKey
    ? null
    : { error: "tmall_store_context_mismatch" as const };
}

export function tmallStageAfterRoute(route: HelperRoute): HelperStage {
  if (route === "/plan" || route === "/plan-backfill") return "planned";
  if (route === "/fetch") return "fetched";
  if (route === "/import") return "imported";
  if (route === "/promotion" || route === tmallDirectPromotionRoute) return "promoted";
  return "completed";
}

export function helperRequestError(
  stage: HelperStage,
  busy: boolean,
  route: HelperRoute,
  requestExecutionId: string | null,
  claimedExecutionId: string | null,
) {
  if (!requestExecutionId) return { error: "missing_or_invalid_execution_id" as const };
  if (claimedExecutionId && requestExecutionId !== claimedExecutionId) {
    return { error: "execution_mismatch" as const };
  }
  if (!claimedExecutionId) {
    return { error: "execution_not_claimed" as const, expected: "/coordination/claim" as const };
  }
  if (busy) return { error: "pipeline_busy" as const };
  if (route === "/plan" || route === "/plan-backfill") {
    return stage === "ready"
      ? null
      : { error: "invalid_stage" as const, expected: "ready" as const, actual: stage };
  }
  if (route === "/next-day" || route === "/product-master" || route === tmallDirectProductMasterRoute) {
    return stage === "promoted"
      ? null
      : { error: "invalid_stage" as const, expected: "promoted" as const, actual: stage };
  }
  const expected: HelperStage = route === "/fetch"
    ? "planned"
    : route === "/import"
      ? "fetched"
      : "imported";
  return stage === expected ? null : { error: "invalid_stage" as const, expected, actual: stage };
}

export function createHelperInactivityReaper({
  close,
  isBusy = () => false,
  timeoutMs = helperInactivityTimeoutMs,
  schedule = (callback: () => void, delayMs: number): unknown => setTimeout(callback, delayMs),
  cancel = (handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>),
}: {
  close: () => void;
  isBusy?: () => boolean;
  timeoutMs?: number;
  schedule?: (callback: () => void, delayMs: number) => unknown;
  cancel?: (handle: unknown) => void;
}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("helper inactivity timeout 必须是正数");
  let timer: unknown = null;
  const clear = () => {
    if (timer === null) return;
    cancel(timer);
    timer = null;
  };
  const expire = () => {
    timer = null;
    if (isBusy()) {
      timer = schedule(expire, timeoutMs);
      return;
    }
    close();
  };
  return {
    arm() {
      clear();
      timer = schedule(expire, timeoutMs);
    },
    clear,
    isArmed() {
      return timer !== null;
    },
  };
}

export function helperHealthCorsHeaders(
  origin: string | undefined,
  allowPrivateNetwork = false,
): Record<string, string> {
  if (origin !== "http://localhost:3000" && origin !== "http://127.0.0.1:3000") return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
    ...(allowPrivateNetwork ? { "Access-Control-Allow-Private-Network": "true" } : {}),
  };
}

export function closeOneShotServer(server: Pick<Server, "close" | "closeAllConnections">) {
  server.close();
  // server.close() waits for keep-alive sockets. The health poller can retain
  // one after the final response, which used to strand the one-shot child and
  // prevent its supervisor from rearming the next run.
  server.closeAllConnections();
}

export type TmallBrowserProcessIdentity = {
  processId: number;
  executablePath: string;
  commandLine: string;
};

function normalizedWindowsPath(value: string) {
  return path.win32.normalize(value).replace(/[\\/]+$/, "").toLowerCase();
}

export function assertTmallBrowserProcessOwnership(store: TmallStore, processInfo: TmallBrowserProcessIdentity) {
  const expectedExecutablePath = store.browser.executablePath;
  const expectedUserDataDir = store.browser.userDataDir;
  if (!expectedExecutablePath || !expectedUserDataDir || !store.browser.profileName) {
    throw new Error("天猫店铺缺少进程级关闭所需的 Chromium 注册信息");
  }
  if (!Number.isSafeInteger(processInfo.processId) || processInfo.processId <= 0) {
    throw new Error("天猫受控 Chromium 监听进程 PID 无效");
  }
  if (normalizedWindowsPath(processInfo.executablePath) !== normalizedWindowsPath(expectedExecutablePath)) {
    throw new Error("天猫 Chromium 可执行文件与店铺注册项不一致，拒绝结束进程");
  }
  const commandLine = processInfo.commandLine.toLowerCase();
  const requiredArguments = [
    `--remote-debugging-port=${store.browser.debugPort}`.toLowerCase(),
    `--user-data-dir=${normalizedWindowsPath(expectedUserDataDir)}`,
    `--profile-directory=${store.browser.profileName}`.toLowerCase(),
  ];
  if (requiredArguments.some((argument) => !commandLine.includes(argument))) {
    throw new Error("天猫 Chromium 端口、用户目录或 Profile 与店铺注册项不一致，拒绝结束进程");
  }
  return processInfo.processId;
}

async function windowsListeningProcessIds(port: number) {
  if (process.platform !== "win32") throw new Error("天猫 Chromium 进程级关闭仅支持 Windows");
  const script = [
    `$listenerPids = @(Get-NetTCPConnection -State Listen -LocalPort ${port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)`,
    `[pscustomobject]@{ pids = @($listenerPids | ForEach-Object { [int]$_ }) } | ConvertTo-Json -Compress`,
  ].join("; ");
  const result = await execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf8",
    timeout: 5_000,
    windowsHide: true,
  });
  const parsed = JSON.parse(String(result.stdout || "{}")) as { pids?: unknown };
  const values = Array.isArray(parsed.pids) ? parsed.pids : [];
  return values.map(Number).filter((pid) => Number.isSafeInteger(pid) && pid > 0);
}

async function windowsProcessIdentity(processId: number): Promise<TmallBrowserProcessIdentity> {
  const script = [
    `$targetProcess = Get-CimInstance Win32_Process -Filter \"ProcessId = ${processId}\" -ErrorAction SilentlyContinue`,
    `if ($null -eq $targetProcess) { [pscustomobject]@{ found = $false } | ConvertTo-Json -Compress } else { [pscustomobject]@{ found = $true; processId = [int]$targetProcess.ProcessId; executablePath = [string]$targetProcess.ExecutablePath; commandLine = [string]$targetProcess.CommandLine } | ConvertTo-Json -Compress }`,
  ].join("; ");
  const result = await execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf8",
    timeout: 5_000,
    windowsHide: true,
  });
  const parsed = JSON.parse(String(result.stdout || "{}")) as Partial<TmallBrowserProcessIdentity> & { found?: boolean };
  if (!parsed.found || !parsed.processId || !parsed.executablePath || !parsed.commandLine) {
    throw new Error("天猫受控 Chromium 监听进程已消失或无法核验");
  }
  return {
    processId: Number(parsed.processId),
    executablePath: String(parsed.executablePath),
    commandLine: String(parsed.commandLine),
  };
}

export async function forceCloseRegisteredTmallBrowser(store: TmallStore) {
  const listeningPids = await windowsListeningProcessIds(store.browser.debugPort);
  if (listeningPids.length === 0) return false;
  if (listeningPids.length !== 1) throw new Error("天猫受控 Chromium 调试端口存在多个监听进程，拒绝结束进程");
  const processId = assertTmallBrowserProcessOwnership(store, await windowsProcessIdentity(listeningPids[0]!));
  try {
    await execFile("taskkill.exe", ["/PID", String(processId), "/T", "/F"], {
      encoding: "utf8",
      timeout: 10_000,
      windowsHide: true,
    });
  } catch (error) {
    if ((await windowsListeningProcessIds(store.browser.debugPort)).length > 0) throw error;
  }
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if ((await windowsListeningProcessIds(store.browser.debugPort)).length === 0) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("天猫受控 Chromium 进程级关闭后调试端口仍未释放");
}

export async function closeTmallWorkflowBrowser(
  target: number | TmallStore,
  closeBrowser: (port: number) => Promise<boolean> = closeChromeBrowser,
  forceCloseBrowser: (store: TmallStore) => Promise<boolean> = forceCloseRegisteredTmallBrowser,
) {
  const debugPort = typeof target === "number" ? target : target.browser.debugPort;
  if (!Number.isInteger(debugPort) || debugPort < 1 || debugPort > 65_535) {
    throw new Error("天猫工作流 Chromium 调试端口无效");
  }
  try {
    const closed = await closeBrowser(debugPort);
    return { ok: true as const, status: closed ? "closed" as const : "already_closed" as const };
  } catch (error) {
    if (typeof target === "number") throw error;
    const forced = await forceCloseBrowser(target);
    return { ok: true as const, status: forced ? "force_closed" as const : "already_closed" as const };
  }
}

export async function runTmallProductMasterTerminalStage(input: {
  store: TmallStore;
  forced: boolean;
  getDecision?: typeof getTmallProductMasterCadenceDecision;
  runProductManager?: typeof runTmallProductMasterStage;
  runPagewise?: typeof runTmallPagewiseProductMasterStage;
  mode?: "registered" | "direct_mtop";
  runDirect?: typeof runTmallDirectProductMasterStage;
  recordSuccess?: typeof recordTmallProductMasterCadenceSuccess;
  closeBrowser?: typeof closeTmallWorkflowBrowser;
}) {
  const getDecision = input.getDecision ?? getTmallProductMasterCadenceDecision;
  const runProductManager = input.runProductManager ?? runTmallProductMasterStage;
  const runPagewise = input.runPagewise ?? runTmallPagewiseProductMasterStage;
  const runDirect = input.runDirect ?? runTmallDirectProductMasterStage;
  const recordSuccess = input.recordSuccess ?? recordTmallProductMasterCadenceSuccess;
  const closeBrowser = input.closeBrowser ?? closeTmallWorkflowBrowser;
  const cadenceDecision = await getDecision({ store: input.store, forced: input.forced });
  let result: Record<string, unknown>;
  if (cadenceDecision.due) {
    const productMasterResult = input.mode === "direct_mtop"
      ? await runDirect({ storeKey: input.store.storeKey })
      : input.store.productMasterExportMode === "on_sale_pagewise_excel"
        ? await runPagewise({ storeKey: input.store.storeKey })
        : await runProductManager({ storeKey: input.store.storeKey });
    const cadenceState = await recordSuccess({
      store: input.store,
      decision: cadenceDecision,
      snapshotDate: productMasterResult.snapshotDate,
    });
    result = {
      ...productMasterResult,
      cadence: cadenceState
        ? {
            configured: true,
            due: true,
            forced: cadenceDecision.forced,
            reason: cadenceDecision.reason,
            intervalDays: cadenceState.intervalDays,
            lastSuccessDate: cadenceState.lastSuccessDate,
            lastSnapshotDate: cadenceState.lastSnapshotDate,
            nextDueDate: cadenceState.nextDueDate,
          }
        : cadenceDecision,
    };
  } else {
    result = {
      ok: true,
      stage: "product_master",
      status: "not_due",
      storeKey: input.store.storeKey,
      shopName: input.store.shopName,
      cadence: cadenceDecision,
    };
  }
  const browserClosure = await closeBrowser(input.store);
  return { ...result, browserClosure };
}

export async function runTmallPromotionStageWithTimeout<T>(
  run: (signal: AbortSignal) => Promise<T>,
  options: {
    timeoutMs?: number;
    onTimeout?: () => Promise<void>;
    schedule?: (callback: () => void, delayMs: number) => unknown;
    cancel?: (handle: unknown) => void;
  } = {},
) {
  const timeoutMs = options.timeoutMs ?? tmallPromotionStageTimeoutMs;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("天猫推广阶段硬超时必须是正数");
  const schedule = options.schedule ?? ((callback: () => void, delayMs: number) => setTimeout(callback, delayMs));
  const cancel = options.cancel ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  const controller = new AbortController();
  let timer: unknown = null;
  let settled = false;
  let timedOut = false;
  return new Promise<T>((resolve, reject) => {
    const finish = () => {
      if (timer !== null) cancel(timer);
      timer = null;
      settled = true;
    };
    const timeoutError = new Error(`天猫推广阶段超过 ${Math.ceil(timeoutMs / 60_000)} 分钟未完成，已失败关闭`);
    timer = schedule(() => {
      if (settled) return;
      timedOut = true;
      controller.abort(timeoutError);
      void Promise.resolve(options.onTimeout?.()).then(() => {
        if (settled) return;
        finish();
        reject(timeoutError);
      }, () => {
        if (settled) return;
        finish();
        reject(new Error(`${timeoutError.message}；受控 Chromium 关闭失败`));
      });
    }, timeoutMs);
    void Promise.resolve().then(() => run(controller.signal)).then((value) => {
      if (timedOut || settled) return;
      finish();
      resolve(value);
    }, (error) => {
      if (timedOut || settled) return;
      finish();
      reject(error);
    });
  });
}

function scheduleOneShotServerClose(server: Pick<Server, "close" | "closeAllConnections">, delayMs: number) {
  return setTimeout(() => closeOneShotServer(server), delayMs);
}

async function serveCommand(argv: string[]) {
  if (isMainThread) {
    const stores = (await loadTmallStores()).filter(store => store.enabled);
    return serveIsolatedHelper({
      port: integerPort(cliValue(argv, "--port")), entryFile: process.argv[1]!,
      allowedStores: new Set(stores.map(store => store.storeKey)),
      health: helperProfileHealth, cors: helperHealthCorsHeaders,
    });
  }
  if (workerData?.protocol !== isolatedHelperProtocol || typeof workerData.token !== "string"
    || !/^[a-f0-9]{64}$/.test(workerData.token) || !workerData.identity) throw new Error("invalid_helper_slot_bootstrap");
  const slotIdentity = workerData.identity as SlotIdentity;
  const port = 0; // OS-assigned loopback port; only the parent receives it via IPC.
  let stage: HelperStage = "ready";
  let busy = false;
  let activeWorkflow: CoordinatedWorkflow | null = null;
  let planPathBase64 = "";
  let tmallPlanDates: string[] = [];
  let tmallPlanRange: { startDate: string; endDate: string } | undefined;
  let tmallBackfill: TmallBackfillState | null = null;
  const persistBackfill = async (state: TmallBackfillState) => {
    // Immutable progress evidence. Recovery starts a new execution from A and
    // queries authoritative coverage; this journal never authorizes an import.
    const file = path.join(artifactDirectory, `backfill-${state.executionId}-${state.storeKey}-${state.cycle}.json`);
    await mkdir(artifactDirectory, { recursive: true });
    await writeFile(file, JSON.stringify(state), { encoding: "utf8", flag: "wx" });
  };
  let manifestPathBase64 = "";
  let jackyunPlan: JackyunN8nPlan | null = null;
  let jdPlan: JdN8nPlan | null = null;
  let jdMarketPlan: JdMarketDailyPlan | null = null;
  let jdPromotionPlan: JdPromotionN8nPlan | null = null;
  let claimedTmallExecutionId: string | null = null;
  let claimedTmallStoreKey: string | null = null;
  let tmallBrowserMayBeOpen = false;
  let claimedJackyunExecutionId: string | null = null;
  let claimedJdExecutionId: string | null = null;
  let claimedJdMarketExecutionId: string | null = null;
  let claimedJdPromotionExecutionId: string | null = null;
  let inactivityReaper: ReturnType<typeof createHelperInactivityReaper> | null = null;
  const server = createServer(async (request, response) => {
    const reply = (status: number, payload: unknown) => {
      const body = JSON.stringify(payload);
      response.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": Buffer.byteLength(body),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      });
      response.end(body);
    };
    const incomingIdentity = isolatedRequestIdentity(request.url ?? "", request.headers);
    if (request.headers[isolatedHelperTokenHeader] !== workerData.token || request.method !== "POST"
      || !incomingIdentity || incomingIdentity.key !== slotIdentity.key
      || incomingIdentity.executionId !== slotIdentity.executionId || incomingIdentity.workflow !== slotIdentity.workflow) {
      reply(403, { ok: false, error: "helper_slot_identity_mismatch" });
      return;
    }
    if (request.method === "POST" && request.url === "/coordination/claim") {
      const requestedWorkflow = parseWorkflowCoordinationKey(request.headers[workflowCoordinationKeyHeader]);
      const requestExecutionId = normalizeN8nExecutionId(request.headers[n8nExecutionIdHeader]);
      const coordinationAttempt = parseWorkflowCoordinationAttempt(request.headers[workflowCoordinationAttemptHeader]);
      if (coordinationAttempt === null) {
        reply(400, { ok: false, error: "missing_or_invalid_coordination_attempt" });
        return;
      }
      const requestedTmallStoreKey = requestedWorkflow === "tmall"
        ? normalizeTmallStoreKey(request.headers[tmallStoreKeyHeader])
        : null;
      if (requestedWorkflow === "tmall" && !requestedTmallStoreKey) {
        reply(400, { ok: false, error: "missing_or_invalid_tmall_store_key" });
        return;
      }
      if (requestedWorkflow === "tmall") {
        try {
          await getTmallStore(requestedTmallStoreKey!);
        } catch {
          reply(400, { ok: false, error: "tmall_store_not_enabled_or_registered" });
          return;
        }
      }
      const claimedExecutionId = requestedWorkflow === "tmall"
        ? claimedTmallExecutionId
        : requestedWorkflow === "jackyun"
          ? claimedJackyunExecutionId
          : requestedWorkflow === "jd"
            ? claimedJdExecutionId
            : requestedWorkflow === "jd-market"
              ? claimedJdMarketExecutionId
              : requestedWorkflow === "jd-promotion"
                ? claimedJdPromotionExecutionId
                : null;
      const decision = workflowClaimDecision({
        stage,
        busy,
        activeWorkflow,
        requestedWorkflow,
        requestExecutionId,
        claimedExecutionId,
        requestedTmallStoreKey,
        claimedTmallStoreKey,
      });
      if ("error" in decision) {
        reply(400, { ok: false, ...decision });
        return;
      }
      if (decision.coordinationStatus === "waiting"
        && workflowCoordinationWaitExpired(coordinationAttempt, decision.coordinationStatus)) {
        reply(409, {
          ok: false,
          error: "coordination_wait_expired",
          workflow: requestedWorkflow,
          attempts: coordinationAttempt,
          maxWaitMinutes: maximumWorkflowCoordinationAttempts * 5,
          reason: decision.reason,
          activeWorkflow: decision.activeWorkflow,
        });
        return;
      }
      const coordinatedWorkflow = requestedWorkflow as CoordinatedWorkflow;
      const coordinatedExecutionId = requestExecutionId as string;
      if (decision.coordinationStatus === "granted" && decision.shouldClaim) {
        activeWorkflow = coordinatedWorkflow;
        if (coordinatedWorkflow === "tmall") {
          claimedTmallExecutionId = coordinatedExecutionId;
          claimedTmallStoreKey = requestedTmallStoreKey;
        }
        if (coordinatedWorkflow === "jackyun") claimedJackyunExecutionId = coordinatedExecutionId;
        if (coordinatedWorkflow === "jd") claimedJdExecutionId = coordinatedExecutionId;
        if (coordinatedWorkflow === "jd-market") claimedJdMarketExecutionId = coordinatedExecutionId;
        if (coordinatedWorkflow === "jd-promotion") claimedJdPromotionExecutionId = coordinatedExecutionId;
        inactivityReaper?.arm();
      }
      reply(200, {
        ok: true,
        coordinationStatus: decision.coordinationStatus,
        workflow: coordinatedWorkflow,
        ...(decision.coordinationStatus === "waiting"
          ? { reason: decision.reason, activeWorkflow: decision.activeWorkflow }
          : {}),
      });
      return;
    }
    const tmallRoutes: HelperRoute[] = [
      "/product-master",
      tmallDirectProductMasterRoute,
      "/plan",
      "/plan-backfill",
      "/next-day",
      "/fetch",
      "/import",
      "/promotion",
      tmallDirectPromotionRoute,
    ];
    const jackyunExportFirstRoutes = jackyunExportFirstActions.map(action => `${jackyunExportFirstPrefix}${action}`);
    const jackyunRoutes = ["/jackyun/plan", "/jackyun/run", "/jackyun/verify", ...jackyunExportFirstRoutes];
    const jdRoutes = ["/jd/plan", "/jd/run", "/jd/verify"];
    const jdMarketRoutes = ["/jd-market/plan", "/jd-market/run", "/jd-market/verify"];
    const jdPromotionRoutes = ["/jd-promotion/plan", "/jd-promotion-cut-meat/plan", "/jd-promotion/run", "/jd-promotion/verify"];
    if (request.method !== "POST" || ![...tmallRoutes, ...jackyunRoutes, ...jdRoutes, ...jdMarketRoutes, ...jdPromotionRoutes].includes(request.url ?? "")) {
      reply(404, { ok: false, error: "not_found" });
      return;
    }
    const isJackyun = jackyunRoutes.includes(request.url ?? "");
    const isJackyunExportFirst = jackyunExportFirstRoutes.includes(request.url ?? "");
    const isJd = jdRoutes.includes(request.url ?? "");
    const isJdMarket = jdMarketRoutes.includes(request.url ?? "");
    const isJdPromotion = jdPromotionRoutes.includes(request.url ?? "");
    const workflow = isJackyun ? "jackyun" : isJd ? "jd" : isJdMarket ? "jd-market" : isJdPromotion ? "jd-promotion" : "tmall";
    if (activeWorkflow && activeWorkflow !== workflow) {
      reply(409, { ok: false, error: "workflow_conflict", activeWorkflow });
      return;
    }
    const route = (request.url === "/jd-promotion-cut-meat/plan" ? "/jd-promotion/plan" : request.url) as HelperRoute | JackyunHelperRoute | JdHelperRoute | JdPromotionHelperRoute | "/jd-market/plan" | "/jd-market/run" | "/jd-market/verify";
    const requestExecutionId = normalizeN8nExecutionId(request.headers[n8nExecutionIdHeader]);
    const requestTmallStoreKey = workflow === "tmall"
      ? normalizeTmallStoreKey(request.headers[tmallStoreKeyHeader])
      : null;
    const requestStateError = isJackyunExportFirst
      ? (!requestExecutionId ? { error: "missing_or_invalid_execution_id" }
        : !claimedJackyunExecutionId ? { error: "execution_not_claimed", expected: "/coordination/claim" }
          : requestExecutionId !== claimedJackyunExecutionId ? { error: "execution_mismatch" }
            : busy ? { error: "pipeline_busy" } : null)
      : isJackyun
      ? jackyunHelperRequestError(
          stage,
          busy,
          route as JackyunHelperRoute,
          requestExecutionId,
          claimedJackyunExecutionId,
        )
      : isJd
        ? jdHelperRequestError(stage as "ready" | JdN8nStage, busy, route as JdHelperRoute, requestExecutionId, claimedJdExecutionId)
        : isJdMarket
          ? jdMarketHelperRequestError(stage, busy, route, requestExecutionId, claimedJdMarketExecutionId)
          : isJdPromotion
            ? jdPromotionHelperRequestError(stage as "ready" | JdPromotionN8nStage, busy, route as JdPromotionHelperRoute, requestExecutionId, claimedJdPromotionExecutionId)
        : helperRequestError(stage, busy, route as HelperRoute, requestExecutionId, claimedTmallExecutionId);
    const stateError = requestStateError ?? (workflow === "tmall"
      ? tmallStoreContextError(requestTmallStoreKey, claimedTmallStoreKey)
        ?? tmallDirectPmProtocolError({
          route: request.url ?? "",
          storeKey: requestTmallStoreKey,
          protocol: request.headers[tmallDirectPmProtocolHeader],
        })
      : null);
    if (stateError) {
      reply(409, { ok: false, ...stateError });
      return;
    }
    if (isJackyun && !claimedJackyunExecutionId) claimedJackyunExecutionId = requestExecutionId;
    if (isJd && !claimedJdExecutionId) claimedJdExecutionId = requestExecutionId;
    if (isJdMarket && !claimedJdMarketExecutionId) claimedJdMarketExecutionId = requestExecutionId;
    if (isJdPromotion && !claimedJdPromotionExecutionId) claimedJdPromotionExecutionId = requestExecutionId;
    inactivityReaper?.clear();
    activeWorkflow = workflow;
    busy = true;
    let tmallBrowserClosure: Awaited<ReturnType<typeof closeTmallWorkflowBrowser>> | null = null;
    try {
      if (isJackyunExportFirst) {
        const action = request.url!.slice(jackyunExportFirstPrefix.length);
        const result = await runJackyunExportFirstAction(action, requestExecutionId!, { root: projectRoot,
          recoverPreviousPreflight: (previousId, replacementId, at) => recoverPreviousJackyunPreflight(projectRoot, previousId, replacementId, at) });
        stage = result.phase === "completed" ? "completed" : result.phase === "imported" ? "executed" : "planned";
        reply(200, result);
        if (stage === "completed") scheduleOneShotServerClose(server, 500);
        else inactivityReaper?.arm();
      } else if (request.url === "/jd-promotion/plan" || request.url === "/jd-promotion-cut-meat/plan") {
        jdPromotionPlan = await planJdPromotionN8nRun({
          executionId: requestExecutionId!,
          storeKey: parseJdPromotionStoreKeyHeader(request.headers[jdPromotionStoreKeyHeader]),
          startDate: parseJdPromotionDateHeader(request.headers[jdPromotionStartDateHeader]),
          endDate: parseJdPromotionDateHeader(request.headers[jdPromotionEndDateHeader]),
        });
        stage = jdPromotionPlan.stage;
        reply(200, publicJdPromotionPlan(jdPromotionPlan));
        inactivityReaper?.arm();
      } else if (request.url === "/jd-promotion/run") {
        if (!jdPromotionPlan) throw new Error("京准通推广 n8n 计划已丢失，拒绝无计划执行");
        stage = "running";
        const result = await runJdPromotionN8nPlan(jdPromotionPlan);
        stage = "executed";
        reply(200, result);
        inactivityReaper?.arm();
      } else if (request.url === "/jd-promotion/verify") {
        if (!jdPromotionPlan) throw new Error("京准通推广 n8n 计划已丢失，拒绝无计划核验");
        const result = await verifyJdPromotionN8nPlan(jdPromotionPlan);
        stage = "completed";
        reply(200, result);
        scheduleOneShotServerClose(server, 500);
      } else if (request.url === "/jd-market/plan") {
        jdMarketPlan = await planJdMarketDailyRun({
          executionId: requestExecutionId!,
          resumeRunId: parseJdMarketResumeRunIdHeader(request.headers[jdMarketResumeRunIdHeader]),
          silentNoWindow: parseJdSilentNoWindowHeader(request.headers[jdSilentNoWindowHeader]),
        });
        stage = jdMarketPlan.stage;
        reply(200, publicJdMarketPlan(jdMarketPlan));
        inactivityReaper?.arm();
      } else if (request.url === "/jd-market/run") {
        if (!jdMarketPlan) throw new Error("京东市场榜单计划已丢失，拒绝无计划执行");
        stage = "running";
        const result = await runJdMarketDailyPlan(jdMarketPlan);
        stage = "executed";
        reply(200, result);
        inactivityReaper?.arm();
      } else if (request.url === "/jd-market/verify") {
        if (!jdMarketPlan) throw new Error("京东市场榜单计划已丢失，拒绝无计划核验");
        const result = await verifyJdMarketDailyPlan(jdMarketPlan);
        stage = "completed";
        reply(200, result);
        scheduleOneShotServerClose(server, 500);
      } else if (request.url === "/jd/plan") {
        jdPlan = await planJdN8nRun({
          executionId: requestExecutionId!,
          silentNoWindow: parseJdSilentNoWindowHeader(request.headers[jdSilentNoWindowHeader]),
        });
        // Preserve the persisted JD stage when the same execution retries A
        // after losing a response; never move executed/completed back to planned.
        stage = jdPlan.stage;
        reply(200, publicJdPlan(jdPlan));
        inactivityReaper?.arm();
      } else if (request.url === "/jd/run") {
        if (!jdPlan) throw new Error("京东 n8n 计划已丢失，拒绝无计划执行");
        stage = "running";
        const result = await runJdN8nPlan(jdPlan);
        stage = "executed";
        reply(200, result);
        inactivityReaper?.arm();
      } else if (request.url === "/jd/verify") {
        if (!jdPlan) throw new Error("京东 n8n 计划已丢失，拒绝无计划核验");
        const result = await verifyJdN8nPlan(jdPlan);
        stage = "completed";
        reply(200, result);
        scheduleOneShotServerClose(server, 500);
      } else if (request.url === "/jackyun/plan") {
        jackyunPlan = await planJackyunN8nRun();
        stage = "planned";
        reply(200, publicJackyunPlan(jackyunPlan));
        // The plan is already persisted. Expiry only releases this helper's
        // execution owner; the replacement helper must claim from A again and
        // can reuse only the exact persisted RUN_ID under the normal resume checks.
        inactivityReaper?.arm();
      } else if (request.url === "/jackyun/run") {
        if (!jackyunPlan) throw new Error("吉客云 n8n 计划已丢失，拒绝无计划执行");
        stage = "running";
        const result = await runJackyunN8nPlan(jackyunPlan);
        stage = "executed";
        reply(200, result);
        // Bound a lost C-stage response without interrupting the active B request.
        inactivityReaper?.arm();
      } else if (request.url === "/jackyun/verify") {
        if (!jackyunPlan) throw new Error("吉客云 n8n 计划已丢失，拒绝无计划核验");
        const result = await verifyJackyunN8nPlan(jackyunPlan);
        stage = "completed";
        reply(200, result);
        scheduleOneShotServerClose(server, 500);
      } else if (request.url === "/product-master" || request.url === tmallDirectProductMasterRoute) {
        if (tmallBackfill?.status === "running") throw new Error("天猫逐日补缺尚未完成最终覆盖核验，不能进入 M");
        const store = await getTmallStore(claimedTmallStoreKey!);
        const result = await runTmallProductMasterTerminalStage({
          store,
          forced: parseTmallForceProductMasterHeader(request.headers[tmallForceProductMasterHeader]),
          mode: request.url === tmallDirectProductMasterRoute ? "direct_mtop" : "registered",
        });
        tmallBrowserClosure = result.browserClosure;
        stage = tmallStageAfterRoute(request.url);
        reply(200, { ...result, ...(tmallBackfill ? { dailyBackfill: publicTmallBackfill(tmallBackfill) } : {}) });
        inactivityReaper?.clear();
        scheduleOneShotServerClose(server, 500);
      } else if (request.url === "/next-day") {
        if (!tmallBackfill || !tmallPlanRange) throw new Error("缺少同一 execution 的逐日补缺计划");
        const next = await planCommand(["--store-key", claimedTmallStoreKey!, "--max-days", "1",
          "--start-date", tmallPlanRange.startDate, "--end-date", tmallPlanRange.endDate]);
        const advanced = advanceTmallBackfill(tmallBackfill, {
          executionId: requestExecutionId!, storeKey: claimedTmallStoreKey!,
          cycle: request.headers["x-teruisi-tmall-backfill-cycle"], plan: next, now: Date.now(),
        });
        await persistBackfill(advanced);
        tmallBackfill = advanced;
        const continueBackfill = advanced.status === "running";
        if (continueBackfill) {
          planPathBase64 = next.planPathBase64;
          tmallPlanDates = [...next.promotionDates];
          manifestPathBase64 = "";
        }
        stage = continueBackfill ? "planned" : "promoted";
        reply(200, { ok: true, stage: "next_day", continueBackfill, dailyBackfill: publicTmallBackfill(advanced) });
        inactivityReaper?.arm();
      } else if (request.url === "/plan" || request.url === "/plan-backfill") {
        const startedAt = Date.now();
        tmallBrowserMayBeOpen = true;
        const authentication = await ensureTmallStoreAuthenticatedSession(claimedTmallStoreKey!);
        const explicitDates = parseTmallPlanDateRangeHeaders(
          request.headers[tmallPlanStartDateHeader],
          request.headers[tmallPlanEndDateHeader],
        );
        const planArguments = ["--store-key", claimedTmallStoreKey!, "--max-days", String(maximumDaysPerRun)];
        if (explicitDates) planArguments.push("--start-date", explicitDates.startDate, "--end-date", explicitDates.endDate);
        const result = await planCommand(planArguments);
        if (request.url === "/plan-backfill") {
          const initial = beginTmallBackfill(requestExecutionId!, claimedTmallStoreKey!, result, startedAt);
          await persistBackfill(initial);
          tmallBackfill = initial;
        }
        planPathBase64 = result.planPathBase64;
        tmallPlanDates = [...result.promotionDates];
        tmallPlanRange = { startDate: result.startDate, endDate: result.endDate };
        stage = tmallStageAfterRoute("/plan");
        reply(200, { ...result, authentication, ...(tmallBackfill ? { dailyBackfill: publicTmallBackfill(tmallBackfill) } : {}) });
        inactivityReaper?.arm();
      } else if (request.url === "/fetch") {
        const result = await fetchCommand(["--plan-base64", planPathBase64], claimedTmallStoreKey!);
        manifestPathBase64 = result.manifestPathBase64;
        stage = tmallStageAfterRoute("/fetch");
        reply(200, result);
        inactivityReaper?.arm();
      } else if (request.url === "/import") {
        const result = await importCommand(["--manifest-base64", manifestPathBase64], claimedTmallStoreKey!);
        stage = tmallStageAfterRoute("/import");
        reply(200, result);
        inactivityReaper?.arm();
      } else if (request.url === "/promotion" || request.url === tmallDirectPromotionRoute) {
        if (!planPathBase64) throw new Error("天猫推广阶段缺少同一 execution 的目标日期计划");
        const store = await getTmallStore(claimedTmallStoreKey!);
        const runPromotion = isTmallDirectPmRoute(request.url)
          ? runTmallDirectPromotionStage
          : runTmallPromotionStage;
        const result = await runTmallPromotionStageWithTimeout(
          (signal) => runPromotion({
            ...getTmallPromotionStageOptions(claimedTmallStoreKey!, tmallPlanDates, tmallPlanRange),
            signal,
          }),
          {
            onTimeout: async () => {
              tmallBrowserClosure = await closeTmallWorkflowBrowser(store);
            },
          },
        );
        stage = tmallStageAfterRoute(request.url);
        reply(200, result);
        inactivityReaper?.arm();
      } else {
        throw new Error("天猫 helper 路由未实现");
      }
    } catch (error) {
      stage = "failed";
      inactivityReaper?.clear();
      let tmallBrowserCloseError: string | null = null;
      if (workflow === "tmall" && !tmallBrowserClosure) {
        try {
          const store = await getRegisteredTmallStore(claimedTmallStoreKey!);
          tmallBrowserClosure = await closeTmallWorkflowBrowser(store);
        } catch (closeError) {
          tmallBrowserCloseError = closeError instanceof Error ? closeError.message : String(closeError);
        }
      }
      const jackyunFailure = isJackyun
        ? jackyunN8nFailureDetails(error, "PIPELINE_FAILED", "helper")
        : null;
      reply(500, {
        ok: false,
        stage,
        ...(jackyunFailure ? { failureStage: jackyunFailure.stage } : {}),
        code: jackyunFailure?.code ?? (error instanceof TmallPipelineError ? error.code : "PIPELINE_FAILED"),
        error: error instanceof Error ? error.message : String(error),
        ...(workflow === "tmall" ? {
          browserClosure: tmallBrowserClosure ?? { ok: false, status: "failed", error: tmallBrowserCloseError },
        } : {}),
      });
      scheduleOneShotServerClose(server, 500);
    } finally {
      busy = false;
    }
  });
  inactivityReaper = createHelperInactivityReaper({
    close: () => closeOneShotServer(server),
    isBusy: () => busy,
  });
  server.once("close", () => {
    inactivityReaper?.clear();
    const finish = async () => {
      if (slotIdentity.storeKey && tmallBrowserMayBeOpen) await closeTmallWorkflowBrowser(await getRegisteredTmallStore(slotIdentity.storeKey));
    };
    const timeout = setTimeout(() => parentPort?.postMessage({ type: "finished", clean: false }), 30_000);
    void finish().then(() => {
      clearTimeout(timeout);
      parentPort?.postMessage({ type: "finished", clean: true });
    }, () => {
      clearTimeout(timeout);
      parentPort?.postMessage({ type: "finished", clean: false });
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("helper_slot_address_invalid");
  parentPort?.postMessage({ type: "ready", port: address.port });
  return { ok: true, stage: "serve", address: "127.0.0.1", port, oneShot: true };
}

async function helperProfileHealth() {
  const [cookieSource, tmallProfile, jackyunProfile, jdProfiles, jdMarketProfile, jdPromotionProfile, jdPromotionCutMeatProfile] = await Promise.all([
    getCookieSourceStatus(),
    loadTmallStores().then(stores => getTmallProfilesStatus(stores.filter(store => store.enabled))).catch(() => "invalid" as const),
    getJackyunProfileStatus(),
    loadJdStores().then(stores => getJdProfilesStatus(stores.filter(store => store.enabled))).catch(() => "invalid" as const),
    loadJdMarketDailyConfig().then(config => getJdStore(config.storeKey)).then(store => getJdProfilesStatus([store])).catch(() => "invalid" as const),
    getJdStore("jd-yiyong-director").then(store => getJdProfilesStatus([store])).catch(() => "invalid" as const),
    getJdStore("jd-maidehao-operator1").then(store => getJdProfilesStatus([store])).catch(() => "invalid" as const),
  ]);
  return { cookieSource, tmallProfile, jackyunProfile, jdProfiles, jdMarketProfile, jdPromotionProfile, jdPromotionCutMeatProfile };
}

async function main() {
  const argv = isMainThread ? process.argv.slice(2) : ["serve"];
  const command = argv[0] as PipelineCommand | undefined;
  if (!command || !["master", "plan", "fetch", "import", "promotion", "serve"].includes(command)) {
    throw new Error("用法: tmall-sycm-cookie-pipeline.ts <master|plan|fetch|import|promotion|serve> [参数]");
  }
  const result = command === "master"
    ? await runTmallProductMasterStage({
        storeKey: cliValue(argv.slice(1), "--store-key") ?? "tmall-yijiu",
        baseUrl: cliValue(argv.slice(1), "--base-url"),
        snapshotDate: cliValue(argv.slice(1), "--snapshot-date"),
      })
    : command === "plan"
    ? await planCommand(argv.slice(1))
    : command === "fetch"
      ? await fetchCommand(argv.slice(1))
      : command === "import"
        ? await importCommand(argv.slice(1))
        : command === "promotion"
          ? await runTmallPromotionStage({
              storeKey: cliValue(argv.slice(1), "--store-key") ?? "tmall-yijiu",
              baseUrl: cliValue(argv.slice(1), "--base-url"),
            })
          : await serveCommand(argv.slice(1));
  console.log(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
