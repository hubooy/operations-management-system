import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { readJsonFile, writeJsonAtomic } from "../lib/jackyun/json-file";
import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import { netshopOutletKey } from "../lib/netshop/query-contract";
import { loadTmallStores, type TmallStore } from "../lib/netshop/tmall-store-registry";
import type { TmallDownloadReceipt } from "./tmall-download-receipt";
import {
  hasExactTmallImportVerification,
  type TmallImportVerificationProof,
} from "./tmall-import-verification";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const auditDirectory = path.join(projectRoot, "outputs", "tmall-multi-store-import");

type ItemStatus = "planned" | "waiting_download" | "imported" | "duplicate" | "completed_with_warnings" | "failed";
export type TmallImportAuditItem = {
  storeKey: string;
  shopName: string;
  businessDate: string;
  status: ItemStatus;
  filePath?: string;
  receiptPath?: string;
  batchId?: string;
  rowCount?: number;
  warningCount?: number;
  warnings?: string[];
  error?: string;
};

export type TmallImportAudit = {
  version: 1;
  baseUrl: string;
  startedAt: string;
  updatedAt: string;
  dryRun: boolean;
  items: TmallImportAuditItem[];
};

export type RunnerOptions = {
  baseUrl: string;
  storeKey?: string;
  startDate?: string;
  endDate: string;
  dates?: string[];
  receiptPaths?: string[];
  dryRun: boolean;
};

type CoveragePayload = {
  requestedPeriod?: { startDate?: string; endDate?: string };
  coverage?: { actualDates?: unknown };
};

type ImportPayload = {
  ok?: boolean;
  status?: "imported" | "duplicate";
  message?: string;
  warnings?: Array<{ message?: string }>;
  batch?: {
    id?: string;
    source?: string;
    dataset?: string;
    platform?: string;
    shopName?: string;
    status?: string;
    warningCount?: number;
    rowCount?: number;
    dateMin?: string | null;
    dateMax?: string | null;
  };
  verification?: TmallImportVerificationProof;
};

type VerifiedReceipt = { receiptPath: string; filePath: string; receipt: TmallDownloadReceipt; bytes: Uint8Array };

function value(argv: string[], name: string) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

function validDate(date: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(date) && !Number.isNaN(Date.parse(`${date}T00:00:00Z`));
}

export function addDays(date: string, days: number) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

export function shanghaiYesterday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return addDays(`${part("year")}-${part("month")}-${part("day")}`, -1);
}

export function datesInRange(startDate: string, endDate: string) {
  const dates: string[] = [];
  for (let current = startDate; current <= endDate; current = addDays(current, 1)) dates.push(current);
  return dates;
}

export function requestedDatesToPlan(startDate: string, endDate: string, requestedDates?: string[]) {
  return requestedDates ? [...requestedDates] : datesInRange(startDate, endDate);
}

export function parseRunnerArgs(argv: string[], now = new Date()): RunnerOptions {
  const startDate = value(argv, "--start-date");
  const endDate = value(argv, "--end-date") ?? shanghaiYesterday(now);
  const datesValue = value(argv, "--dates");
  const dates = datesValue ? [...new Set(datesValue.split(",").map((date) => date.trim()).filter(Boolean))].sort() : undefined;
  if ((startDate && !validDate(startDate)) || !validDate(endDate)) throw new Error("起止日期必须是有效的 YYYY-MM-DD");
  if (dates && (dates.length === 0 || dates.some((date) => !validDate(date)))) throw new Error("--dates 必须是逗号分隔的 YYYY-MM-DD 日期");
  if (dates?.some((date) => date > shanghaiYesterday(now))) throw new Error("天猫商品日数据最多补到昨天");
  if (startDate && startDate > endDate) throw new Error("--start-date 不能晚于 --end-date");
  if (dates && startDate) throw new Error("--dates 不能与 --start-date 同时使用");
  if (endDate > shanghaiYesterday(now)) throw new Error("天猫商品日数据最多补到昨天");
  return {
    baseUrl: (value(argv, "--base-url") ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000").replace(/\/$/, ""),
    storeKey: value(argv, "--store-key"),
    startDate,
    endDate,
    dates,
    dryRun: argv.includes("--dry-run"),
  };
}

export function buildTmallSpuCoverageUrl(baseUrl: string, store: Pick<TmallStore, "shopName">, startDate: string, endDate: string) {
  const params = new URLSearchParams({
    dimension: "spu",
    platform: "天猫",
    outlet: netshopOutletKey("天猫", store.shopName),
    startDate,
    endDate,
    page: "1",
    pageSize: "1",
  });
  return `${baseUrl}/api/netshop/product-performance?${params}`;
}

async function getActualDates(baseUrl: string, store: TmallStore, startDate: string, endDate: string, request: typeof fetch = fetch) {
  const response = await request(buildTmallSpuCoverageUrl(baseUrl, store, startDate, endDate), { signal: AbortSignal.timeout(30_000) });
  const payload = await response.json().catch(() => null) as CoveragePayload | null;
  const actualDates = payload?.coverage?.actualDates;
  if (!response.ok || payload?.requestedPeriod?.startDate !== startDate || payload.requestedPeriod.endDate !== endDate || !Array.isArray(actualDates)
    || actualDates.some((date) => typeof date !== "string" || !validDate(date))) {
    throw new Error(`无法读取 ${store.shopName} 的 SPU 日期覆盖 (HTTP ${response.status})`);
  }
  return actualDates as string[];
}

function inside(directory: string, filePath: string) {
  const relative = path.relative(directory, filePath);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export async function verifiedReceipts(store: TmallStore, requestedReceiptPaths?: string[]): Promise<VerifiedReceipt[]> {
  const names = requestedReceiptPaths
    ? requestedReceiptPaths
    : (await readdir(store.browser.downloadDir).catch(() => [] as string[]))
      .filter((item) => item.endsWith(".tmall-receipt.json"))
      .map((name) => path.join(store.browser.downloadDir, name));
  const downloadRoot = await realpath(store.browser.downloadDir);
  const receipts: VerifiedReceipt[] = [];
  for (const candidatePath of names) {
    const receiptPath = await realpath(path.resolve(candidatePath));
    if (!inside(downloadRoot, receiptPath) || !receiptPath.endsWith(".tmall-receipt.json")) {
      throw new Error(`天猫下载签收单越过店铺目录: ${candidatePath}`);
    }
    const receipt = await readJsonFile<TmallDownloadReceipt>(receiptPath);
    if (receipt.version !== 1 || receipt.storeKey !== store.storeKey || receipt.shopName !== store.shopName || !validDate(receipt.businessDate)
      || !/^[a-f0-9]{64}$/.test(receipt.sha256) || !Number.isFinite(receipt.size) || receipt.size <= 0 || !/\.xls$/i.test(receipt.fileName)
      || path.basename(receipt.fileName) !== receipt.fileName || Number.isNaN(Date.parse(receipt.downloadedAt))) {
      throw new Error(`天猫下载签收单无效: ${receiptPath}`);
    }
    const filePath = await realpath(path.resolve(path.dirname(receiptPath), receipt.fileName));
    if (!inside(downloadRoot, filePath)) throw new Error(`天猫下载签收单越过店铺目录: ${receiptPath}`);
    const info = await stat(filePath).catch(() => null);
    if (!info?.isFile() || info.size !== receipt.size) throw new Error(`天猫下载签收文件缺失或大小变化: ${filePath}`);
    const bytes = new Uint8Array(await readFile(filePath));
    if (createHash("sha256").update(bytes).digest("hex") !== receipt.sha256) throw new Error(`天猫下载签收文件哈希变化: ${filePath}`);
    receipts.push({ receiptPath, filePath, receipt, bytes });
  }
  return receipts;
}

export function selectReceiptForDate(receipts: VerifiedReceipt[], businessDate: string) {
  const matches = receipts.filter((item) => item.receipt.businessDate === businessDate);
  const hashes = new Set(matches.map((item) => item.receipt.sha256));
  if (hashes.size > 1) throw new Error(`日期 ${businessDate} 存在多个内容不同的已验证文件，禁止猜测导入`);
  return matches.sort((a, b) => b.receipt.downloadedAt.localeCompare(a.receipt.downloadedAt))[0] ?? null;
}

export function validateImportPayload(
  payload: ImportPayload | null,
  httpStatus: number,
  store: Pick<TmallStore, "shopName">,
  businessDate: string,
  expectedRowCount: number,
) {
  const batch = payload?.batch;
  const verification = payload?.verification;
  const importStatus = payload?.status === "imported" || payload?.status === "duplicate" ? payload.status : null;
  const expectedStatus = importStatus === "imported" ? 201 : importStatus === "duplicate" ? 200 : 0;
  if (httpStatus !== expectedStatus || !payload?.ok || importStatus === null
    || !batch?.id || batch.source !== "tmall_product_daily" || batch.dataset !== "spu_daily" || batch.platform !== "天猫"
    || batch.shopName !== store.shopName || batch.status !== "completed" || batch.dateMin !== businessDate || batch.dateMax !== businessDate
    || !Number.isInteger(expectedRowCount) || expectedRowCount <= 0 || batch.rowCount !== expectedRowCount
    || !Number.isFinite(batch.warningCount)
    || !hasExactTmallImportVerification(verification, {
      status: importStatus,
      rowCount: expectedRowCount,
      dataset: "spu_daily",
      platform: "天猫",
      shopName: store.shopName,
      dateMin: businessDate,
      dateMax: businessDate,
    })) {
    throw new Error(payload?.message ?? `天猫 SPU 导入回查不一致 (HTTP ${httpStatus})`);
  }
  return {
    status: importStatus,
    batchId: batch.id,
    rowCount: batch.rowCount!,
    warningCount: batch.warningCount!,
    warnings: (payload.warnings ?? []).map((warning) => warning.message ?? "").filter(Boolean),
  };
}

export async function postTmallImportWithNetworkRetry(options: {
  url: string;
  buildForm: () => FormData;
  request?: typeof fetch;
  wait?: (delayMs: number) => Promise<void>;
  timeoutMs?: number;
}) {
  const request = options.request ?? fetch;
  const wait = options.wait ?? ((delayMs: number) => new Promise<void>((resolve) => {
    setTimeout(resolve, delayMs);
  }));
  const timeoutMs = options.timeoutMs ?? 120_000;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      return await request(options.url, {
        method: "POST",
        body: options.buildForm(),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      if (attempt >= 2 || !(error instanceof TypeError)) throw error;
      await wait(500);
    }
  }
  throw new Error("天猫导入网络重试未返回结果");
}

async function importReceipt(baseUrl: string, store: TmallStore, businessDate: string, candidate: VerifiedReceipt, request: typeof fetch = fetch) {
  const inspected = await inspectTmallImportBytes({
    source: "tmall_product_daily",
    bytes: candidate.bytes,
    fileName: path.basename(candidate.filePath),
    fileSizeBytes: candidate.bytes.byteLength,
    shopName: store.shopName,
    expectedStartDate: businessDate,
    expectedEndDate: businessDate,
  });
  if (inspected.errors.length) throw new Error(inspected.errors.map((issue) => issue.message).join("；"));
  const fileBuffer = candidate.bytes.buffer.slice(candidate.bytes.byteOffset, candidate.bytes.byteOffset + candidate.bytes.byteLength) as ArrayBuffer;
  const buildForm = () => {
    const form = new FormData();
    form.set("source", "tmall_product_daily");
    form.set("platform", "天猫");
    form.set("shopName", store.shopName);
    form.set("expectedDataset", "spu_daily");
    form.set("expectedStartDate", businessDate);
    form.set("expectedEndDate", businessDate);
    form.set("file", new File([fileBuffer], path.basename(candidate.filePath), { type: "application/vnd.ms-excel" }));
    return form;
  };
  const response = await postTmallImportWithNetworkRetry({
    url: `${baseUrl}/api/netshop/import`,
    buildForm,
    request,
  });
  const payload = await response.json().catch(() => null) as ImportPayload | null;
  const imported = validateImportPayload(payload, response.status, store, businessDate, inspected.totals.rowCount);
  const actualDates = await getActualDates(baseUrl, store, businessDate, businessDate, request);
  if (!actualDates.includes(businessDate)) throw new Error("导入接口返回成功，但同店铺同日期覆盖回查未命中");
  return imported;
}

export async function runTmallMultiStoreImport(options: RunnerOptions, stores?: TmallStore[], request: typeof fetch = fetch) {
  const enabled = (stores ?? await loadTmallStores()).filter((store) => store.enabled);
  const selected = options.storeKey ? enabled.filter((store) => store.storeKey === options.storeKey) : enabled;
  if (options.storeKey && selected.length !== 1) throw new Error(`未找到启用的天猫店铺注册键: ${options.storeKey}`);
  if (selected.length === 0) throw new Error("没有已启用的天猫店铺");
  if (options.receiptPaths && selected.length !== 1) throw new Error("显式签收单路径只能用于单个受控天猫店铺");
  await mkdir(auditDirectory, { recursive: true });
  const auditPath = path.join(auditDirectory, `run-${Date.now()}.json`);
  let audit: TmallImportAudit = { version: 1, baseUrl: options.baseUrl, startedAt: new Date().toISOString(), updatedAt: new Date().toISOString(), dryRun: options.dryRun, items: [] };
  const persist = async () => { audit = { ...audit, updatedAt: new Date().toISOString() }; await writeJsonAtomic(auditPath, audit); };
  await persist();

  for (const store of selected) {
    const requestedDates = options.dates;
    const startDate = requestedDates?.[0] ?? options.startDate ?? options.endDate;
    const endDate = requestedDates?.at(-1) ?? options.endDate;
    if (store.initialStartDate && startDate < store.initialStartDate) {
      throw new Error(`${store.storeKey} 的目标日期不能早于注册起始日 ${store.initialStartDate}`);
    }
    if (startDate > endDate) continue;
    for (const businessDate of requestedDatesToPlan(startDate, endDate, requestedDates)) {
      audit.items.push({ storeKey: store.storeKey, shopName: store.shopName, businessDate, status: "planned" });
    }
  }
  await persist();
  if (options.dryRun) return { ok: audit.items.every((item) => item.status === "planned"), auditPath, audit };

  for (const store of selected) {
    const plannedItems = audit.items.filter((candidate) => candidate.storeKey === store.storeKey && candidate.status === "planned");
    let receipts: VerifiedReceipt[];
    try {
      receipts = await verifiedReceipts(store, options.receiptPaths);
    } catch (error) {
      for (const item of plannedItems) {
        item.status = "failed";
        item.error = error instanceof Error ? error.message : String(error);
      }
      await persist();
      continue;
    }
    for (const item of plannedItems) {
      let candidate: VerifiedReceipt | null;
      try { candidate = selectReceiptForDate(receipts, item.businessDate); } catch (error) {
        item.status = "failed";
        item.error = error instanceof Error ? error.message : String(error);
        await persist();
        continue;
      }
      if (!candidate) {
        item.status = "waiting_download";
        item.error = "缺少由该店铺 RPA 生成并签收的逐日 .xls 文件";
        await persist();
        continue;
      }
      item.filePath = candidate.filePath;
      item.receiptPath = candidate.receiptPath;
      try {
        const result = await importReceipt(options.baseUrl, store, item.businessDate, candidate, request);
        item.status = result.warningCount > 0 ? "completed_with_warnings" : result.status;
        item.batchId = result.batchId;
        item.rowCount = result.rowCount;
        item.warningCount = result.warningCount;
        item.warnings = result.warnings;
      } catch (error) {
        item.status = "failed";
        item.error = error instanceof Error ? error.message : String(error);
      }
      await persist();
    }
  }
  const ok = audit.items.every((item) => item.status === "imported" || item.status === "duplicate" || item.status === "completed_with_warnings");
  return { ok, auditPath, audit };
}

async function main() {
  const result = await runTmallMultiStoreImport(parseRunnerArgs(process.argv.slice(2)));
  const counts = Object.fromEntries(["planned", "waiting_download", "imported", "duplicate", "completed_with_warnings", "failed"].map((status) => [status, result.audit.items.filter((item) => item.status === status).length]));
  console.log(JSON.stringify({ ok: result.ok, auditPath: result.auditPath, counts }, null, 2));
  if (!result.ok) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.stack ?? error.message : String(error));
    process.exitCode = 1;
  });
}
