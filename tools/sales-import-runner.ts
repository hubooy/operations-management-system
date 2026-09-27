import { createHash } from "node:crypto";
import { readFile, mkdir, copyFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  parseXlsxFirstSheet,
  type XlsxCellValue,
  type XlsxFirstSheet,
  type XlsxRow,
} from "../lib/imports/xlsx";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";
import { normalizeSalesLedgerDate } from "../lib/imports/sales-ledger";
import { readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { jackyunSalesPeriod } from "../lib/jackyun/sales-period";

type Policy = {
  version: string;
  timeZone: string;
  download: { directory: string; fileNamePattern: string };
  dateRule: { type: "month_to_previous_day"; field: string; fallbackFields: string[] };
  excludedWarehouses: string[];
  approvedSalesChannels: string[];
  costSource: {
    searchRoot: string;
    folderPrefix: string;
    fileName: string;
    productCodeHeader: string;
    productNameHeader: string;
    unitCostHeader: string;
    zeroCostProductNames: string[];
  };
};

type CliOptions = {
  asOfDate: string;
  salesStartDate?: string;
  downloadPath?: string;
  costSourcePath?: string;
  expectedDownloadSha256?: string;
  expectedCostSha256?: string;
  expectedSourceRows?: number;
  auditRootPath?: string;
  baseUrl: string;
  dryRun: boolean;
  /** 以销售明细文件自带的"货品成本"列为最终成本，跳过库存成本源匹配/重算。默认 false。 */
  useFileCost?: boolean;
};

export const salesSourceRowCountSemantic = "xlsx_nonblank_data_rows" as const;

export type SalesImportFailureCode =
  | "FILE_VALIDATION_FAILED"
  | "IMPORT_FAILED"
  | "BATCH_VERIFY_FAILED";

export class SalesImportError extends Error {
  readonly failureCode: SalesImportFailureCode;
  readonly stage: string;
  readonly details?: Record<string, unknown>;
  readonly cause?: unknown;

  constructor(
    failureCode: SalesImportFailureCode,
    stage: string,
    message: string,
    options: { details?: Record<string, unknown>; cause?: unknown } = {},
  ) {
    super(message);
    this.name = "SalesImportError";
    this.failureCode = failureCode;
    this.stage = stage;
    this.details = options.details;
    this.cause = options.cause;
  }
}

export function assertExactSalesSourceRowCount(expected: number | undefined, actual: number) {
  if (!Number.isSafeInteger(expected) || Number(expected) <= 0) {
    throw new SalesImportError(
      "FILE_VALIDATION_FAILED",
      "validate_source_row_count",
      "销售来源计数语义缺失：必须提供页面确认的非空导出明细行数。",
      { details: { semantic: salesSourceRowCountSemantic, expected: expected ?? null, actual } },
    );
  }
  if (!Number.isSafeInteger(actual) || actual <= 0 || actual !== expected) {
    throw new SalesImportError(
      "FILE_VALIDATION_FAILED",
      "validate_source_row_count",
      `销售源文件行数与页面确认的导出明细行数不一致：页面 ${expected}，文件 ${actual}。`,
      { details: { semantic: salesSourceRowCountSemantic, expected, actual } },
    );
  }
  return {
    semantic: salesSourceRowCountSemantic,
    expected,
    actual,
    verified: true,
  } as const;
}

function wrapSalesImportError(
  error: unknown,
  failureCode: SalesImportFailureCode,
  stage: string,
) {
  if (error instanceof SalesImportError) return error;
  return new SalesImportError(
    failureCode,
    stage,
    error instanceof Error ? error.message : String(error),
    { cause: error },
  );
}

export type SalesImportRunOptions = Omit<CliOptions, "downloadPath" | "costSourcePath"> & {
  downloadPath: string;
  costSourcePath?: string | null;
  downloadBytes?: Uint8Array;
  preserveRawCopy?: boolean;
  useFileCost?: boolean;
};

export type SalesImportRunResult = {
  status: "prepared" | "verified_completed";
  recovered?: boolean;
  dryRun?: boolean;
  audit: Record<string, unknown>;
};

type ProcessedRegistryRun = {
  rawSha256: string;
  status: string;
  runId: string;
  periodStart?: string;
  periodEnd?: string;
  auditPath?: string;
  processedSha256?: string;
};

type HeaderRow = {
  rowNumber: number;
  headers: string[];
  indexes: Map<string, number>;
};

type Period = {
  startDate: string;
  endDate: string;
  startDateTime: string;
  endExclusiveDateTime: string;
};

type CostEntry = {
  productName: string;
  costCents: Set<number>;
};

type ServerPeriodShop = {
  channel: string;
  platform: string;
  shopName: string;
  rowCount: number;
  netSalesCents: number;
};

export type SalesPostImportVerification = {
  policyVersion?: string;
  period?: { startDate?: string; endDate?: string; endExclusive?: string };
  batch?: { id?: string; status?: string; rowCount?: number } | null;
  stats?: {
    rowCount?: number;
    minShipTime?: string | null;
    maxShipTime?: string | null;
    excludedWarehouseRows?: number;
    rowsNotOwnedByBatch?: number | null;
  };
  nonWhitelistChannels?: unknown;
  error?: string;
};

export function assertSalesPostImportVerification(input: {
  responseOk: boolean;
  expectedPolicyVersion: string;
  period: Pick<Period, "startDate" | "endDate" | "endExclusiveDateTime">;
  expectedBatch: { id: string; status: string; rowCount: number };
  expectedRowCount: number;
  verification: SalesPostImportVerification | null;
}) {
  const verification = input.verification;
  if (!input.responseOk || !verification) {
    throw new Error(verification?.error ?? "销售导入后的落库回查请求失败。");
  }
  if (verification.policyVersion !== input.expectedPolicyVersion) {
    throw new Error(`销售落库回查策略版本不一致：期望 ${input.expectedPolicyVersion}，实际 ${verification.policyVersion ?? "未知"}。`);
  }
  const expectedEndExclusive = input.period.endExclusiveDateTime.slice(0, 10);
  if (verification.period?.startDate !== input.period.startDate
    || verification.period.endDate !== input.period.endDate
    || verification.period.endExclusive !== expectedEndExclusive) {
    throw new Error("销售落库回查日期范围与本轮导入范围不一致。");
  }
  const batch = verification.batch;
  if (!batch || batch.id !== input.expectedBatch.id) throw new Error("销售落库回查未找到本轮精确批次。");
  if (input.expectedBatch.status !== "completed" || batch.status !== "completed") {
    throw new Error(`销售批次未完成：导入响应=${input.expectedBatch.status}，回查=${batch.status ?? "未知"}。`);
  }
  if (input.expectedBatch.rowCount !== input.expectedRowCount || batch.rowCount !== input.expectedRowCount) {
    throw new Error(`销售批次行数不一致：期望 ${input.expectedRowCount}，导入响应 ${input.expectedBatch.rowCount}，回查 ${batch.rowCount ?? "未知"}。`);
  }
  if (verification.stats?.rowCount !== input.expectedRowCount) {
    throw new Error(`销售期间落库行数不一致：期望 ${input.expectedRowCount}，实际 ${verification.stats?.rowCount ?? "未知"}。`);
  }
  if (verification.stats.excludedWarehouseRows !== 0) throw new Error("销售落库回查发现排除仓残留。");
  if (verification.stats.rowsNotOwnedByBatch !== 0) throw new Error("销售落库回查发现不属于本轮批次的期间数据。");
  if (!Array.isArray(verification.nonWhitelistChannels) || verification.nonWhitelistChannels.length > 0) {
    throw new Error("销售落库回查发现非白名单渠道，或渠道证据缺失。");
  }
  const minDate = verification.stats.minShipTime?.slice(0, 10) ?? "";
  const maxDate = verification.stats.maxShipTime?.slice(0, 10) ?? "";
  if (!minDate || !maxDate || minDate < input.period.startDate || maxDate > input.period.endDate) {
    throw new Error("销售落库回查的发货日期边界超出本轮期间。");
  }
}

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policyPath = path.join(projectRoot, "config", "sales-import-policy.json");
const defaultAuditRoot = path.join(projectRoot, "outputs", "sales-import-runs");
const salesRequiredHeaders: readonly (string | readonly string[])[] = [
  ["网店订单号", "订单编号"],
  "销售渠道",
  "发货仓库",
  "货品编号",
  "货品名称",
  "数量",
  "下单时间",
  "货品成本",
  "分摊后单价",
  "分摊后金额",
  "毛利",
];

function text(value: XlsxCellValue | undefined) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function normalizeHeader(value: XlsxCellValue | undefined) {
  return text(value)
    .replace(/^\uFEFF/, "")
    .replace(/[（]/g, "(")
    .replace(/[）]/g, ")")
    .replace(/\s+/g, "");
}

function isBlankRow(row: XlsxRow) {
  return !row.cells.some((cell) => text(cell) !== "");
}

function parseNumber(value: XlsxCellValue | undefined) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/[￥¥,\s]/g, "");
  if (!normalized || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function roundMoney(value: number) {
  return Math.round((value + Math.sign(value || 1) * Number.EPSILON) * 100) / 100;
}

function moneyToCents(value: number) {
  return Math.round((value + Math.sign(value || 1) * Number.EPSILON) * 100);
}

function sha256(bytes: Uint8Array) {
  return createHash("sha256").update(bytes).digest("hex");
}

function isoDate(value: Date) {
  return value.toISOString().slice(0, 10);
}

function addUtcDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return isoDate(date);
}

function isIsoDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function monthToPreviousDay(asOfDate: string): Period {
  if (!isIsoDate(asOfDate)) throw new Error("--as-of 必须是有效的 YYYY-MM-DD 日期。");
  const startDate = `${asOfDate.slice(0, 8)}01`;
  return {
    startDate,
    endDate: asOfDate,
    startDateTime: `${startDate} 00:00:00`,
    endExclusiveDateTime: `${addUtcDays(asOfDate, 1)} 00:00:00`,
  };
}

function defaultAsOfDate() {
  return addUtcDays(shanghaiToday(), -1);
}

function parseCli(): SalesImportRunOptions {
  const args = process.argv.slice(2);
  const options: CliOptions = {
    asOfDate: defaultAsOfDate(),
    baseUrl: "http://localhost:3000",
    dryRun: false,
  };
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    const next = args[index + 1];
    if (argument === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (argument === "--use-file-cost") {
      options.useFileCost = true;
      continue;
    }
    if (!next) throw new Error(`参数 ${argument} 缺少取值。`);
    if (argument === "--as-of") options.asOfDate = next;
    else if (argument === "--download") options.downloadPath = next;
    else if (argument === "--cost-source") options.costSourcePath = next;
    else if (argument === "--expected-download-sha256") options.expectedDownloadSha256 = next;
    else if (argument === "--expected-cost-sha256") options.expectedCostSha256 = next;
    else if (argument === "--expected-source-rows") options.expectedSourceRows = Number(next);
    else if (argument === "--audit-root") options.auditRootPath = next;
    else if (argument === "--base-url") options.baseUrl = next.replace(/\/$/, "");
    else throw new Error(`不支持的参数：${argument}`);
    index += 1;
  }
  if (!options.downloadPath || (!options.costSourcePath && !options.useFileCost)) {
    throw new Error("正式销售任务必须显式提供 --download，且(--cost-source 或 --use-file-cost) 二选一；不允许自动猜测历史文件。");
  }
  if (!options.useFileCost && !options.costSourcePath) {
    throw new Error("非 --use-file-cost 模式必须显式提供 --cost-source 库存成本源。");
  }
  if (!Number.isSafeInteger(options.expectedSourceRows) || Number(options.expectedSourceRows) <= 0) {
    throw new Error("--expected-source-rows 必须明确提供页面确认的非空导出明细行数。");
  }
  return {
    ...options,
    downloadPath: options.downloadPath,
    costSourcePath: options.costSourcePath,
  };
}

function findHeaderRow(sheet: XlsxFirstSheet, requiredHeaders: readonly (string | readonly string[])[], sourceName: string): HeaderRow {
  for (const row of sheet.rows.slice(0, 20)) {
    const indexes = new Map<string, number>();
    row.cells.forEach((value, index) => {
      const key = normalizeHeader(value);
      if (key && !indexes.has(key)) indexes.set(key, index);
    });
    if (requiredHeaders.every((header) => {
      const alternatives = typeof header === "string" ? [header] : header;
      return alternatives.some((alt) => indexes.has(normalizeHeader(alt)));
    })) {
      return { rowNumber: row.rowNumber, headers: row.cells.map(text), indexes };
    }
  }
  const display = requiredHeaders.map((h) => typeof h === "string" ? h : h.join("/")).join("、");
  throw new Error(`${sourceName} 未找到必需表头：${display}`);
}

function requiredColumn(header: HeaderRow, names: string | readonly string[], sourceName: string) {
  const alternatives = typeof names === "string" ? [names] : names;
  for (const name of alternatives) {
    const index = header.indexes.get(normalizeHeader(name));
    if (index !== undefined) return index;
  }
  throw new Error(`${sourceName} 缺少必需列：${alternatives.join("/")}`);
}

function optionalColumn(header: HeaderRow, name: string) {
  return header.indexes.get(normalizeHeader(name));
}

function cellAt(row: XlsxRow, column: number | undefined): XlsxCellValue {
  return column === undefined ? null : (row.cells[column] ?? null);
}

function effectiveShipTime(row: XlsxRow, date1904: boolean, columns: { ship: number; lineShip?: number; order: number }) {
  return normalizeSalesLedgerDate(cellAt(row, columns.ship), date1904)
    ?? normalizeSalesLedgerDate(cellAt(row, columns.order), date1904);
}

function outputRowsFor(row: XlsxRow, width: number) {
  const cells = [...row.cells];
  while (cells.length < width) cells.push(null);
  return cells;
}

async function writeJson(filePath: string, value: unknown) {
  await writeJsonAtomic(filePath, value);
}

function fetchWithTimeout(input: string | URL | Request, init: RequestInit = {}, timeoutMs = 60_000) {
  return fetch(input, { ...init, signal: init.signal ?? AbortSignal.timeout(timeoutMs) });
}

async function assertServerPolicyVersion(baseUrl: string, expectedVersion: string) {
  const response = await fetchWithTimeout(`${baseUrl}/api/imports/sales/verify?policyOnly=1`, { cache: "no-store" });
  const body = await response.json().catch(() => null) as { policyVersion?: string; error?: string } | null;
  if (!response.ok || body?.policyVersion !== expectedVersion) {
    throw new Error(
      body?.error
        ?? `运营管理系统销售策略版本不一致：runner=${expectedVersion}，server=${body?.policyVersion ?? "未知"}。`,
    );
  }
}

async function readServerPeriodShops(baseUrl: string, period: Period) {
  const response = await fetchWithTimeout(
    `${baseUrl}/api/imports/sales/verify?${new URLSearchParams({ startDate: period.startDate, endDate: period.endDate })}`,
    { cache: "no-store" },
  );
  const body = await response.json().catch(() => null) as { shops?: ServerPeriodShop[]; error?: string } | null;
  if (!response.ok || !body?.shops) {
    throw new Error(body?.error ?? "无法读取销售导入前的系统期间店铺快照。");
  }
  return body.shops;
}

async function readSalesPostImportVerification(input: {
  baseUrl: string;
  period: Period;
  batchId: string;
}) {
  const response = await fetchWithTimeout(
    `${input.baseUrl}/api/imports/sales/verify?${new URLSearchParams({
      startDate: input.period.startDate,
      endDate: input.period.endDate,
      batchId: input.batchId,
    })}`,
    { cache: "no-store" },
    2 * 60_000,
  );
  const verification = await response.json().catch(() => null) as SalesPostImportVerification | null;
  return { responseOk: response.ok, verification };
}

export function findMissingPreviouslyLoadedChannels(
  approvedChannels: readonly string[],
  currentShopCounts: ReadonlyMap<string, number>,
  previousShops: readonly ServerPeriodShop[],
) {
  const approved = new Set(approvedChannels);
  const grouped = new Map<string, { channel: string; rowCount: number; netSalesCents: number }>();
  for (const shop of previousShops) {
    const channel = shop.channel.trim();
    if (!approved.has(channel) || currentShopCounts.has(channel)) continue;
    const existing = grouped.get(channel) ?? { channel, rowCount: 0, netSalesCents: 0 };
    existing.rowCount += Number(shop.rowCount) || 0;
    existing.netSalesCents += Number(shop.netSalesCents) || 0;
    grouped.set(channel, existing);
  }
  return [...grouped.values()].sort((left, right) => left.channel.localeCompare(right.channel, "zh-CN"));
}

async function uploadInChunks(baseUrl: string, bytes: Uint8Array, fileName: string, fingerprint: string, period: Pick<Period, "startDate" | "endDate">) {
  const chunkSizeBytes = 2 * 1024 * 1024;
  const chunkCount = Math.ceil(bytes.byteLength / chunkSizeBytes);
  const init = await fetchWithTimeout(`${baseUrl}/api/imports/sales/chunks`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "init", fileName, fileSizeBytes: bytes.byteLength, chunkCount, fingerprint, expectedStartDate: period.startDate, expectedEndDate: period.endDate }),
  });
  const initBody = await init.json() as { ok?: boolean; upload?: { id: string; receivedChunkIndexes: number[] }; message?: string };
  if (!init.ok || !initBody.ok || !initBody.upload) throw new Error(initBody.message ?? "无法创建销售导入分片会话。");
  const received = new Set(initBody.upload.receivedChunkIndexes);
  // Upload chunks in parallel with limited concurrency to reduce total upload time.
  const pendingIndexes: number[] = [];
  for (let index = 0; index < chunkCount; index += 1) {
    if (!received.has(index)) pendingIndexes.push(index);
  }
  const uploadId = initBody.upload.id;
  const uploadConcurrency = 3;
  for (let batchStart = 0; batchStart < pendingIndexes.length; batchStart += uploadConcurrency) {
    const batch = pendingIndexes.slice(batchStart, batchStart + uploadConcurrency);
    await Promise.all(batch.map((index) => {
      const start = index * chunkSizeBytes;
      const end = Math.min(start + chunkSizeBytes, bytes.byteLength);
      return fetchWithTimeout(`${baseUrl}/api/imports/sales/chunks`, {
        method: "PUT",
        headers: {
          "content-type": "application/octet-stream",
          "x-upload-id": uploadId,
          "x-chunk-index": String(index),
        },
        body: bytes.slice(start, end),
      }).then(async (response) => {
        const body = await response.json() as { ok?: boolean; message?: string };
        if (!response.ok || !body.ok) throw new Error(body.message ?? `第 ${index + 1} 个分片上传失败。`);
      });
    }));
  }
  const complete = await fetchWithTimeout(`${baseUrl}/api/imports/sales/chunks`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "complete", uploadId: initBody.upload.id, expectedStartDate: period.startDate, expectedEndDate: period.endDate }),
  }, 10 * 60_000);
  const completeBody = await complete.json() as { ok?: boolean; status?: string; message?: string; batch?: { id: string; status: string; rowCount: number; totals: unknown } };
  if (!complete.ok || !completeBody.ok || !completeBody.batch) throw new Error(completeBody.message ?? "销售导入完成确认失败。");
  return completeBody as typeof completeBody & { batch: NonNullable<typeof completeBody.batch> };
}

export async function runSalesImport(options: SalesImportRunOptions): Promise<SalesImportRunResult> {
  if (!Number.isSafeInteger(options.expectedSourceRows) || Number(options.expectedSourceRows) <= 0) {
    throw new SalesImportError(
      "FILE_VALIDATION_FAILED",
      "validate_source_row_count_contract",
      "销售来源计数合同缺失：expectedSourceRows 必须是页面确认的非空导出明细行数。",
      { details: { semantic: salesSourceRowCountSemantic, expected: options.expectedSourceRows ?? null } },
    );
  }
  const policy = await readJsonFileOr<Policy>(policyPath, {} as Policy);
  if (!policy.version || policy.dateRule.type !== "month_to_previous_day") throw new Error("销售导入策略文件无效。");
  if (!options.dryRun) await assertServerPolicyVersion(options.baseUrl, policy.version);
  const period = monthToPreviousDay(options.asOfDate);
  if (options.salesStartDate !== undefined) {
    const rolling = jackyunSalesPeriod(options.asOfDate, options.salesStartDate);
    period.startDate = rolling.startDate;
    period.startDateTime = `${rolling.startDate} 00:00:00`;
  }
  const auditRoot = path.resolve(options.auditRootPath ?? defaultAuditRoot);
  const downloadPath = path.resolve(options.downloadPath);
  const useFileCost = options.useFileCost === true;
  const costSourcePath = options.costSourcePath ? path.resolve(options.costSourcePath) : null;
  if (!useFileCost && !costSourcePath) {
    throw new Error("非 --use-file-cost 模式必须提供 --cost-source 库存成本源。");
  }
  const rawBytes = options.downloadBytes ?? new Uint8Array(await readFile(downloadPath));
  const rawHash = sha256(rawBytes);
  if (options.expectedDownloadSha256 && rawHash !== options.expectedDownloadSha256) {
    throw new Error("销售原始文件 SHA 与统一 runner 绑定值不一致。");
  }
  const runId = `${period.startDate}_${period.endDate}_${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}`;
  const runDir = path.join(auditRoot, runId);
  const registryPath = path.join(auditRoot, "processed-downloads.json");
  const registry = await readJsonFileOr<{ runs: ProcessedRegistryRun[] }>(registryPath, { runs: [] });
  for (const previous of registry.runs.filter((run) => run.rawSha256 === rawHash && run.status === "verified_completed")) {
    const previousAuditPath = previous.auditPath ?? path.join(auditRoot, previous.runId, "audit.json");
    const previousAudit = await readJsonFileOr<Record<string, unknown> | null>(previousAuditPath, null);
    if (previousAudit?.policyVersion !== policy.version) continue;
    const previousPeriod = previousAudit?.period as Record<string, unknown> | undefined;
    const samePeriod = (previous.periodStart ?? previousPeriod?.startDate) === period.startDate
      && (previous.periodEnd ?? previousPeriod?.endDate) === period.endDate;
    if (!samePeriod) continue;
    const previousSources = previousAudit?.sources as Record<string, unknown> | undefined;
    const previousCost = previousSources?.costSource as Record<string, unknown> | undefined;
    const previousFiltering = previousAudit?.filtering as Record<string, unknown> | undefined;
    // 成本口径必须一致：useFileCost 只能恢复 file_cost 历史批次，不能复用库存成本源旧批次；
    // 反之亦然。成本口径变化必须创建新批次替换旧事实。
    const previousFileCostMode = (previousCost as { mode?: string } | undefined)?.mode === "file_cost";
    if (previousFileCostMode !== useFileCost) continue;
    if (options.expectedCostSha256 && previousCost?.sha256 !== options.expectedCostSha256) continue;
    if (previousFiltering?.sourceRows !== options.expectedSourceRows) continue;
    const previousCountContract = previousAudit?.sourceCountContract as Record<string, unknown> | undefined;
    if (previousCountContract?.semantic !== salesSourceRowCountSemantic
      || previousCountContract.expected !== options.expectedSourceRows
      || previousCountContract.actual !== options.expectedSourceRows
      || previousCountContract.verified !== true) {
      throw new SalesImportError(
        "BATCH_VERIFY_FAILED",
        "recover_source_row_count_contract",
        `销售成功审计缺少可验证的精确来源计数合同：${previousAuditPath}`,
      );
    }
    const previousImport = previousAudit?.import as Record<string, unknown> | undefined;
    const previousBatch = previousImport?.batch as Record<string, unknown> | undefined;
    const previousVerification = previousAudit?.postImportVerification as Record<string, unknown> | undefined;
    if (!previousAudit || previousAudit.ok !== true || previousBatch?.status !== "completed" || previousVerification?.verified !== true) {
      throw new Error(`销售处理登记显示已完成，但成功审计不可恢复：${previousAuditPath}`);
    }
    const expectedBatch = {
      id: typeof previousBatch.id === "string" ? previousBatch.id : "",
      status: typeof previousBatch.status === "string" ? previousBatch.status : "",
      rowCount: Number(previousBatch.rowCount),
    };
    const expectedRowCount = Number(previousFiltering?.retainedRows);
    if (!expectedBatch.id || !Number.isSafeInteger(expectedBatch.rowCount) || expectedBatch.rowCount <= 0
      || !Number.isSafeInteger(expectedRowCount) || expectedRowCount <= 0) {
      throw new Error(`销售处理登记缺少可实时回查的精确批次或行数：${previousAuditPath}`);
    }
    const recoveredVerification = await readSalesPostImportVerification({
      baseUrl: options.baseUrl,
      period,
      batchId: expectedBatch.id,
    });
    assertSalesPostImportVerification({
      responseOk: recoveredVerification.responseOk,
      expectedPolicyVersion: policy.version,
      period,
      expectedBatch,
      expectedRowCount,
      verification: recoveredVerification.verification,
    });
    previousAudit.postImportVerification = {
      ...recoveredVerification.verification,
      verified: true,
      verifiedAt: new Date().toISOString(),
      recoveryReverified: true,
    };
    await writeJson(previousAuditPath, previousAudit);
    return { status: "verified_completed", recovered: true, audit: previousAudit };
  }
  // Fire the server period-shop request only after duplicate recovery is ruled
  // out, so an early return cannot leave a rejected promise unobserved. It can
  // still overlap with workbook parsing and filtering below.
  const serverPeriodShopsPromise = options.dryRun
    ? Promise.resolve([] as ServerPeriodShop[])
    : readServerPeriodShops(options.baseUrl, period);
  await mkdir(runDir, { recursive: true });
  if (options.preserveRawCopy !== false) {
    await mkdir(path.join(runDir, "raw"), { recursive: true });
    await copyFile(downloadPath, path.join(runDir, "raw", path.basename(downloadPath)));
  }

  // Start reading cost source file in parallel with synchronous xlsx parsing below.
  // When useFileCost is set, the sales workbook's own "货品成本" column is authoritative,
  // so no inventory cost source is read or matched.
  const costBytesPromise = useFileCost
    ? Promise.resolve(new Uint8Array(0))
    : readFile(costSourcePath!);
  const salesWorkbook = parseXlsxFirstSheet(rawBytes, {
    maxCompressedBytes: 256 * 1024 * 1024,
    maxUncompressedBytes: 2 * 1024 * 1024 * 1024,
    maxWorksheetBytes: 2 * 1024 * 1024 * 1024,
    maxRows: 500_001,
  });
  const salesHeader = findHeaderRow(salesWorkbook, salesRequiredHeaders, "销售明细文件");
  const columns = {
    warehouse: requiredColumn(salesHeader, "发货仓库", "销售明细文件"),
    channel: requiredColumn(salesHeader, "销售渠道", "销售明细文件"),
    productCode: requiredColumn(salesHeader, "货品编号", "销售明细文件"),
    productName: requiredColumn(salesHeader, "货品名称", "销售明细文件"),
    quantity: requiredColumn(salesHeader, "数量", "销售明细文件"),
    cost: requiredColumn(salesHeader, "货品成本", "销售明细文件"),
    allocatedAmount: requiredColumn(salesHeader, "分摊后金额", "销售明细文件"),
    // 吉客云 v4 导出自 2026-08-27 起不再包含"费用分摊"列；缺失时按 0 处理（用户已确认）
    fee: optionalColumn(salesHeader, "费用分摊"),
    gross: requiredColumn(salesHeader, "毛利", "销售明细文件"),
    grossMargin: optionalColumn(salesHeader, "毛利率"),
    untaxedGross: optionalColumn(salesHeader, "未税毛利"),
    untaxedGrossMargin: optionalColumn(salesHeader, "未税毛利率(%)"),
    ship: requiredColumn(salesHeader, policy.dateRule.field, "销售明细文件"),
    order: requiredColumn(salesHeader, policy.dateRule.fallbackFields[0] ?? "下单时间", "销售明细文件"),
  };
  const sourceRows = salesWorkbook.rows.filter((row) => row.rowNumber > salesHeader.rowNumber && !isBlankRow(row));
  const sourceCountContract = assertExactSalesSourceRowCount(options.expectedSourceRows, sourceRows.length);
  const today = addUtcDays(period.endDate, 1);
  const dateProblems: Array<{ row: number; value: string | null; reason: string }> = [];
  const outOfPeriodRows: Array<{ row: number; value: string | null; reason: string }> = [];
  const futureDateRows: Array<{ row: number; value: string | null }> = [];
  const periodRows: XlsxRow[] = [];
  // Cache effectiveShipTime results from the first pass to avoid recomputing in the second pass.
  // effectiveShipTime calls normalizeSalesLedgerDate twice per row; caching saves ~17000 × 2 calls.
  const effectiveDateCache = new WeakMap<XlsxRow, string | null>();
  for (const sourceRow of sourceRows) {
    const effectiveDate = effectiveShipTime(sourceRow, salesWorkbook.date1904, columns);
    effectiveDateCache.set(sourceRow, effectiveDate);
    const date = effectiveDate?.slice(0, 10) ?? "";
    if (!effectiveDate) {
      dateProblems.push({ row: sourceRow.rowNumber, value: effectiveDate, reason: "missing_date" });
    } else if (date < period.startDate) {
      outOfPeriodRows.push({ row: sourceRow.rowNumber, value: effectiveDate, reason: "before_period" });
    } else if (date === today) {
      futureDateRows.push({ row: sourceRow.rowNumber, value: effectiveDate });
    } else if (date > period.endDate) {
      dateProblems.push({ row: sourceRow.rowNumber, value: effectiveDate, reason: "after_execution_day" });
    } else {
      periodRows.push(sourceRow);
    }
  }
  const brushRows: XlsxRow[] = [];
  const excludedShopRows: XlsxRow[] = [];
  const retainedRows: XlsxRow[] = [];
  for (const row of periodRows) {
    if (policy.excludedWarehouses.includes(text(cellAt(row, columns.warehouse)))) brushRows.push(row);
    else if (!policy.approvedSalesChannels.includes(text(cellAt(row, columns.channel)))) excludedShopRows.push(row);
    else retainedRows.push(row);
  }

  if (dateProblems.length || retainedRows.length === 0) {
    const failedAuditPath = path.join(runDir, "audit.json");
    await writeJson(failedAuditPath, {
      ok: false,
      period,
      sourceCountContract,
      filtering: {
        sourceRows: sourceRows.length,
        excludedOutOfPeriodRows: outOfPeriodRows.length,
        excludedTodayRows: futureDateRows.length,
        periodRows: periodRows.length,
        excludedWarehouseRows: brushRows.length,
        nonWhitelistRows: excludedShopRows.length,
        retainedRows: retainedRows.length,
      },
      validation: {
        dateProblems: { count: dateProblems.length, samples: dateProblems.slice(0, 20) },
        excludedOutOfPeriodRows: { count: outOfPeriodRows.length, samples: outOfPeriodRows.slice(0, 20) },
        excludedTodayRows: { count: futureDateRows.length, samples: futureDateRows.slice(0, 20) },
      },
    });
    throw new Error(`销售日期或过滤结果校验未通过：${failedAuditPath}`);
  }

  // Await the cost source file that was started in parallel with xlsx parsing above.
  const costBytes = new Uint8Array(await costBytesPromise);
  const costHash = sha256(costBytes);
  if (!useFileCost && options.expectedCostSha256 && costHash !== options.expectedCostSha256) {
    throw new Error("销售成本源 SHA 与本轮 inventory 清单不一致。");
  }
  let costWorkbook: XlsxFirstSheet | null = null;
  let costHeader: HeaderRow | null = null;
  let costCodeColumn: number | null = null;
  let costNameColumn: number | null = null;
  let unitCostColumn: number | null = null;
  let costWarehouseColumn: number | undefined;
  let excludedCostWarehouseRows = 0;
  const costEntries = new Map<string, CostEntry>();
  const costConflicts: Array<{ code: string; costs: number[] }> = [];
  const costMap = new Map<string, number>();
  if (!useFileCost) {
    costWorkbook = parseXlsxFirstSheet(costBytes);
    costHeader = findHeaderRow(costWorkbook, [policy.costSource.productCodeHeader, policy.costSource.productNameHeader, policy.costSource.unitCostHeader], "成本源文件");
    costCodeColumn = requiredColumn(costHeader, policy.costSource.productCodeHeader, "成本源文件");
    costNameColumn = requiredColumn(costHeader, policy.costSource.productNameHeader, "成本源文件");
    unitCostColumn = requiredColumn(costHeader, policy.costSource.unitCostHeader, "成本源文件");
    costWarehouseColumn = optionalColumn(costHeader, "仓库");
    const costRows = costWorkbook.rows.filter((item) => item.rowNumber > costHeader!.rowNumber && !isBlankRow(item));
    excludedCostWarehouseRows = costWarehouseColumn === undefined
      ? 0
      : costRows.filter((row) => policy.excludedWarehouses.includes(text(cellAt(row, costWarehouseColumn!)))).length;
    for (const row of costRows) {
      if (costWarehouseColumn !== undefined
        && policy.excludedWarehouses.includes(text(cellAt(row, costWarehouseColumn)))) continue;
      const code = text(cellAt(row, costCodeColumn!));
      const unitCost = parseNumber(cellAt(row, unitCostColumn!));
      if (!code || unitCost === null) continue;
      const entry = costEntries.get(code) ?? { productName: text(cellAt(row, costNameColumn!)), costCents: new Set<number>() };
      entry.costCents.add(moneyToCents(unitCost));
      if (!entry.productName) entry.productName = text(cellAt(row, costNameColumn!));
      costEntries.set(code, entry);
    }
    for (const [code, entry] of costEntries.entries()) {
      if (entry.costCents.size > 1) {
        costConflicts.push({ code, costs: [...entry.costCents].sort((a, b) => a - b).map((value) => value / 100) });
      }
    }
    for (const [code, entry] of costEntries.entries()) {
      if (entry.costCents.size === 1) {
        costMap.set(code, [...entry.costCents][0] / 100);
      }
    }
  }

  const numericProblems: Array<{ row: number; field: string }> = [];
  const unmatchedCosts = new Map<string, { productName: string; rows: number[] }>();
  const outputRows: XlsxCellValue[][] = [];
  let minDate = "";
  let maxDate = "";
  let allocatedTotal = 0;
  let costTotal = 0;
  let feeTotal = 0;
  let grossTotal = 0;
  let blankCodeZeroCostRows = 0;
  for (const sourceRow of retainedRows) {
    // Reuse cached effectiveDate from the first pass instead of recomputing.
    const effectiveDate = effectiveDateCache.get(sourceRow) ?? null;
    const date = effectiveDate?.slice(0, 10) ?? "";
    minDate = !minDate || date < minDate ? date : minDate;
    maxDate = !maxDate || date > maxDate ? date : maxDate;
    const code = text(cellAt(sourceRow, columns.productCode));
    const productName = text(cellAt(sourceRow, columns.productName));
    const quantity = parseNumber(cellAt(sourceRow, columns.quantity));
    const allocated = parseNumber(cellAt(sourceRow, columns.allocatedAmount));
    // "费用分摊"列缺失（吉客云 v4 导出不再包含）时按 0 处理
    const fee = columns.fee === undefined ? 0 : parseNumber(cellAt(sourceRow, columns.fee));
    const sourceCost = parseNumber(cellAt(sourceRow, columns.cost));
    const feeInvalid = columns.fee !== undefined && fee === null;
    if (quantity === null || quantity === 0) numericProblems.push({ row: sourceRow.rowNumber, field: "数量" });
    if (allocated === null) numericProblems.push({ row: sourceRow.rowNumber, field: "分摊后金额" });
    if (feeInvalid) numericProblems.push({ row: sourceRow.rowNumber, field: "费用分摊" });
    if (quantity === null || quantity === 0 || allocated === null || feeInvalid) continue;
    const isPriceAdjustment = policy.costSource.zeroCostProductNames.includes(productName);
    const isBlankCodeZeroCost = !code && sourceCost === 0;
    if (isBlankCodeZeroCost) blankCodeZeroCostRows += 1;
    // useFileCost 模式：以销售明细文件自带的"货品成本"列为整行成本，不做库存成本源匹配/重算。
    let lineCost: number;
    if (useFileCost) {
      if (sourceCost === null || !Number.isFinite(sourceCost)) {
        const current = unmatchedCosts.get(code || "") ?? { productName, rows: [] };
        if (current.rows.length < 10) current.rows.push(sourceRow.rowNumber);
        unmatchedCosts.set(code || "", current);
        continue;
      }
      lineCost = roundMoney(sourceCost);
    } else {
      const unitCost = isPriceAdjustment || isBlankCodeZeroCost ? 0 : costMap.get(code);
      if (unitCost === undefined) {
        const current = unmatchedCosts.get(code) ?? { productName, rows: [] };
        if (current.rows.length < 10) current.rows.push(sourceRow.rowNumber);
        unmatchedCosts.set(code, current);
        continue;
      }
      lineCost = roundMoney(unitCost * quantity);
    }
    const grossProfit = roundMoney(allocated - lineCost - fee);
    const grossMargin = allocated === 0 ? "" : `${(grossProfit / allocated * 100).toFixed(2)}%`;
    const row = outputRowsFor(sourceRow, salesHeader.headers.length);
    if (effectiveDate && columns.ship !== undefined) {
      row[columns.ship] = effectiveDate;
    }
    if (isBlankCodeZeroCost) {
      if (isPriceAdjustment) {
        row[columns.productCode] = "ERP_PRICE_ADJUSTMENT";
      } else {
        const virtualCode = createHash("sha1").update(productName || "blank-code-zero-cost").digest("hex").slice(0, 10).toUpperCase();
        row[columns.productCode] = `ERP_ZERO_COST_${virtualCode}`;
      }
    }
    row[columns.cost] = lineCost;
    row[columns.gross] = grossProfit;
    if (columns.grossMargin !== undefined) row[columns.grossMargin] = grossMargin;
    if (columns.untaxedGross !== undefined) row[columns.untaxedGross] = grossProfit;
    if (columns.untaxedGrossMargin !== undefined) row[columns.untaxedGrossMargin] = grossMargin;
    outputRows.push(row);
    allocatedTotal = roundMoney(allocatedTotal + allocated);
    costTotal = roundMoney(costTotal + lineCost);
    feeTotal = roundMoney(feeTotal + fee);
    grossTotal = roundMoney(grossTotal + grossProfit);
  }

  if (costConflicts.length || unmatchedCosts.size || numericProblems.length || outputRows.length === 0) {
    const failedAudit = {
      ok: false,
      period,
      validation: {
        minDate,
        maxDate,
        dateProblems: { count: dateProblems.length, samples: dateProblems.slice(0, 20) },
        excludedOutOfPeriodRows: { count: outOfPeriodRows.length, samples: outOfPeriodRows.slice(0, 20) },
        excludedTodayRows: { count: futureDateRows.length, samples: futureDateRows.slice(0, 20) },
        numericProblems: { count: numericProblems.length, samples: numericProblems.slice(0, 20) },
        blankCodeZeroCostRows,
        costConflicts: { count: costConflicts.length, samples: costConflicts.slice(0, 20) },
        unmatchedCosts: {
          count: unmatchedCosts.size,
          samples: [...unmatchedCosts.entries()].slice(0, 20).map(([code, value]) => ({ code, ...value })),
        },
      },
    };
    const failedAuditPath = path.join(runDir, "audit.json");
    await writeJson(failedAuditPath, failedAudit);
    throw new Error(`导入前自动校验未通过：${failedAuditPath}`);
  }

  const retainedShopCounts = new Map<string, number>();
  for (const row of outputRows) {
    const channel = text(row[columns.channel]);
    retainedShopCounts.set(channel, (retainedShopCounts.get(channel) ?? 0) + 1);
  }
  const previousPeriodShops = await serverPeriodShopsPromise;
  const missingPreviouslyLoadedChannels = findMissingPreviouslyLoadedChannels(
    policy.approvedSalesChannels,
    retainedShopCounts,
    previousPeriodShops,
  );
  if (missingPreviouslyLoadedChannels.length > 0) {
    const failedAuditPath = path.join(runDir, "audit.json");
    await writeJson(failedAuditPath, {
      ok: false,
      period,
      policyVersion: policy.version,
      sourceCountContract,
      filtering: {
        sourceRows: sourceRows.length,
        periodRows: periodRows.length,
        excludedWarehouseRows: brushRows.length,
        nonWhitelistRows: excludedShopRows.length,
        retainedRows: outputRows.length,
        retainedShopCounts: Object.fromEntries(retainedShopCounts),
      },
      validation: {
        missingPreviouslyLoadedChannels,
      },
    });
    const details = missingPreviouslyLoadedChannels
      .map((item) => `${item.channel}（系统已有 ${item.rowCount} 行，净销售额 ${(item.netSalesCents / 100).toFixed(2)} 元）`)
      .join("、");
    throw new Error(`本轮完整期间文件疑似漏选已有白名单店铺：${details}。已停止导入，审计：${failedAuditPath}`);
  }
  const excludedShopCounts = new Map<string, number>();
  for (const row of excludedShopRows) {
    const channel = text(cellAt(row, columns.channel)) || "（空销售渠道）";
    excludedShopCounts.set(channel, (excludedShopCounts.get(channel) ?? 0) + 1);
  }
  const usedCodes = [...new Set(outputRows
    .filter((row) => !policy.costSource.zeroCostProductNames.includes(text(row[columns.productName])))
    .map((row) => text(row[columns.productCode]))
    .filter(Boolean))].sort((left, right) => left.localeCompare(right, "zh-CN"));
  const costSheetRows: XlsxCellValue[][] = useFileCost
    ? [
      ["货品编号", "货品成本(文件)", "货品名称", "成本来源"],
      ...usedCodes.map((code) => {
        const firstRow = outputRows.find((row) => text(row[columns.productCode]) === code);
        return [code, firstRow ? parseNumber(firstRow[columns.cost]) ?? null : null, text(firstRow?.[columns.productName] ?? ""), "销售明细文件货品成本列"];
      }),
    ]
    : [
      ["货品编号", "固定成本价", "货品名称", "成本来源"],
      ...usedCodes.map((code) => [code, costMap.get(code) ?? null, costEntries.get(code)?.productName ?? "", path.basename(costSourcePath!)]),
    ];
  const whitelistRows: XlsxCellValue[][] = [["保留店铺（销售渠道精确匹配）", "保留行数", "状态", "被剔除店铺", "剔除行数"]];
  const excludedShopEntries = [...excludedShopCounts.entries()].sort((left, right) => right[1] - left[1]);
  const auditRowCount = Math.max(policy.approvedSalesChannels.length, excludedShopEntries.length);
  for (let index = 0; index < auditRowCount; index += 1) {
    const approved = policy.approvedSalesChannels[index] ?? "";
    const excluded = excludedShopEntries[index];
    whitelistRows.push([
      approved,
      approved ? retainedShopCounts.get(approved) ?? 0 : null,
      approved ? "保留" : "",
      excluded?.[0] ?? "",
      excluded?.[1] ?? null,
    ]);
  }
  const processedBytes = createXlsxWorkbookBytes([
    { name: salesWorkbook.sheetName || "sheetTitle", rows: [salesHeader.headers, ...outputRows] },
    { name: "成本匹配", rows: costSheetRows },
    { name: "店铺白名单", rows: whitelistRows },
    { name: "导入元数据", rows: [["字段", "值"], ["开始日期", period.startDate], ["截止日期", period.endDate]] },
  ]);
  const processedHash = sha256(processedBytes);
  const outputPath = path.join(runDir, `销售单明细账_${period.startDate}_${period.endDate}_已筛选并匹配成本.xlsx`);
  await writeFile(outputPath, processedBytes);

  // Single-pass post-processing check: replaces 5 separate filter() calls that each
  // iterated all outputRows. Using Set lookups and a single loop saves ~4× iterations.
  const excludedWarehouseSet = new Set(policy.excludedWarehouses);
  const approvedChannelSet = new Set(policy.approvedSalesChannels);
  let processedExcludedWarehouseRows = 0;
  let processedNonWhitelistRows = 0;
  let processedMissingCostRows = 0;
  let processedDateProblemRows = 0;
  for (const row of outputRows) {
    if (excludedWarehouseSet.has(text(row[columns.warehouse]))) processedExcludedWarehouseRows++;
    if (!approvedChannelSet.has(text(row[columns.channel]))) processedNonWhitelistRows++;
    if (parseNumber(row[columns.cost]) === null) processedMissingCostRows++;
    // row[columns.ship] was set to effectiveDate (already normalized) during output building,
    // so we can check the date string directly without calling normalizeSalesLedgerDate again.
    const shipDate = text(row[columns.ship]).slice(0, 10);
    if (!shipDate || shipDate < period.startDate || shipDate > period.endDate) processedDateProblemRows++;
  }
  const processedChecks = {
    rowCount: outputRows.length,
    excludedWarehouseRows: processedExcludedWarehouseRows,
    nonWhitelistRows: processedNonWhitelistRows,
    missingCostRows: processedMissingCostRows,
    dateProblemRows: processedDateProblemRows,
  };
  if (processedChecks.rowCount !== outputRows.length || processedChecks.excludedWarehouseRows || processedChecks.nonWhitelistRows || processedChecks.missingCostRows || processedChecks.dateProblemRows) {
    throw new SalesImportError(
      "FILE_VALIDATION_FAILED",
      "verify_processed_workbook",
      `销售处理后复核未通过：${JSON.stringify(processedChecks)}`,
      { details: processedChecks },
    );
  }

  const audit: Record<string, unknown> = {
    ok: true,
    runId,
    auditPath: path.join(runDir, "audit.json"),
    policyVersion: policy.version,
    period,
    sourceCountContract,
    sources: {
      rawDownload: { path: downloadPath, sha256: rawHash, bytes: rawBytes.byteLength },
      costSource: useFileCost
        ? { mode: "file_cost", path: null, note: "以销售明细文件自带货品成本列为最终成本" }
        : {
          path: costSourcePath,
          sha256: costHash,
          bytes: costBytes.byteLength,
          uniqueCosts: costMap.size,
          excludedWarehouseRows: excludedCostWarehouseRows,
        },
    },
    filtering: {
      sourceRows: sourceRows.length,
      excludedOutOfPeriodRows: outOfPeriodRows.length,
      periodRows: periodRows.length,
      excludedWarehouseRows: brushRows.length,
      nonWhitelistRows: excludedShopRows.length,
      excludedTodayRows: futureDateRows.length,
      retainedRows: outputRows.length,
      retainedShopCounts: Object.fromEntries(retainedShopCounts),
      whitelistWithNoData: policy.approvedSalesChannels.filter((channel) => !retainedShopCounts.has(channel)),
    },
    validation: {
      costConflicts: { count: 0, samples: [] },
      unmatchedCosts: { count: 0, samples: [] },
      dateProblems: { count: 0, samples: [] },
      excludedOutOfPeriodRows: { count: outOfPeriodRows.length, samples: outOfPeriodRows.slice(0, 20) },
      excludedTodayRows: { count: futureDateRows.length, samples: futureDateRows.slice(0, 20) },
      numericProblems: { count: 0, samples: [] },
      processedChecks,
      blankCodeZeroCostRows,
      missingPreviouslyLoadedChannels,
    },
    totals: {
      netSalesCents: moneyToCents(allocatedTotal),
      costAmountCents: moneyToCents(costTotal),
      feeAllocationCents: moneyToCents(feeTotal),
      grossProfitCents: moneyToCents(grossTotal),
    },
    output: { path: outputPath, sha256: processedHash, bytes: processedBytes.byteLength },
    import: null,
    postImportVerification: null,
  };
  await writeJson(path.join(runDir, "audit.json"), audit);
  if (options.dryRun) {
    return { status: "prepared", dryRun: true, audit };
  }

  let imported: Awaited<ReturnType<typeof uploadInChunks>>;
  try {
    imported = await uploadInChunks(options.baseUrl, processedBytes, path.basename(outputPath), processedHash, period);
  } catch (error) {
    const failure = wrapSalesImportError(error, "IMPORT_FAILED", "chunk_upload_and_import");
    audit.ok = false;
    audit.import = null;
    audit.postImportVerification = null;
    audit.failure = {
      code: failure.failureCode,
      stage: failure.stage,
      message: failure.message,
      failedAt: new Date().toISOString(),
    };
    await writeJson(path.join(runDir, "audit.json"), audit);
    throw failure;
  }
  let verification: SalesPostImportVerification | null = null;
  try {
    const verified = await readSalesPostImportVerification({
      baseUrl: options.baseUrl,
      period,
      batchId: imported.batch.id,
    });
    verification = verified.verification;
    assertSalesPostImportVerification({
      responseOk: verified.responseOk,
      expectedPolicyVersion: policy.version,
      period,
      expectedBatch: imported.batch,
      expectedRowCount: outputRows.length,
      verification,
    });
  } catch (error) {
    const failure = wrapSalesImportError(error, "BATCH_VERIFY_FAILED", "verify_exact_import_batch");
    audit.ok = false;
    audit.import = imported;
    audit.postImportVerification = verification;
    audit.failure = {
      code: failure.failureCode,
      stage: failure.stage,
      message: failure.message,
      failedAt: new Date().toISOString(),
    };
    await writeJson(path.join(runDir, "audit.json"), audit);
    throw failure;
  }
  audit.import = imported;
  audit.postImportVerification = { ...verification, verified: true, verifiedAt: new Date().toISOString() };
  await writeJson(path.join(runDir, "audit.json"), audit);
  const auditPath = path.join(runDir, "audit.json");
  registry.runs.push({
    rawSha256: rawHash,
    status: "verified_completed",
    runId,
    periodStart: period.startDate,
    periodEnd: period.endDate,
    auditPath,
    processedSha256: processedHash,
  });
  await mkdir(auditRoot, { recursive: true });
  await writeJson(registryPath, registry);
  return { status: "verified_completed", audit };
}

function compactResult(result: SalesImportRunResult) {
  const audit = result.audit;
  const filtering = audit.filtering as Record<string, unknown> | undefined;
  const output = audit.output as Record<string, unknown> | undefined;
  const imported = audit.import as Record<string, unknown> | undefined;
  const batch = imported?.batch as Record<string, unknown> | undefined;
  return {
    status: result.status,
    recovered: result.recovered ?? false,
    dryRun: result.dryRun ?? false,
    runId: audit.runId ?? null,
    sourceRows: filtering?.sourceRows ?? null,
    retainedRows: filtering?.retainedRows ?? null,
    excludedWarehouseRows: filtering?.excludedWarehouseRows ?? null,
    nonWhitelistRows: filtering?.nonWhitelistRows ?? null,
    excludedTodayRows: filtering?.excludedFutureDateRows ?? null,
    outputPath: output?.path ?? null,
    batchId: batch?.id ?? null,
    batchStatus: batch?.status ?? null,
    auditPath: audit.auditPath ?? null,
  };
}

async function main() {
  const result = await runSalesImport(parseCli());
  console.log(JSON.stringify(compactResult(result)));
}

if (path.resolve(process.argv[1] ?? "") === path.resolve(fileURLToPath(import.meta.url))) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    process.exitCode = 1;
  });
}
