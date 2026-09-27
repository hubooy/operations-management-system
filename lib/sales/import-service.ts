import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  DjangoSalesServiceResponseError,
  SALES_STAGED_IMPORTS_PATH,
  requestDjangoSalesService,
} from "@/lib/django/sales-writer";
import {
  parseSalesLedgerXlsx,
  type SalesLedgerRow,
  type SalesLedgerTotals,
} from "@/lib/imports/sales-ledger";
import { findLatestAuthoritativeSystemCostSnapshot } from "@/lib/inventory/system-cost-reference";
import {
  sanitizeSalesIssues,
  type SalesImportBatch,
  type SalesImportIssue,
  type SalesLineInput,
} from "@/lib/sales/import-contract";
import {
  isApprovedSalesChannel,
  isExcludedSalesWarehouse,
  isZeroCostProductName,
  salesImportPolicy,
} from "@/lib/sales/import-policy";

export const XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
export const MAX_SALES_IMPORT_RANGE_DAYS = 366;
export const MAX_SALES_IMPORT_CHANNELS = 50;

const MAX_STAGED_ROWS = 500_000;
const MAX_ROWS_PER_STAGED_CHUNK = 1_000;
const TARGET_STAGED_BODY_BYTES = 6 * 1024 * 1024;
const encoder = new TextEncoder();

export function isXlsxSignature(bytes: Uint8Array) {
  return bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04;
}

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return crypto.subtle.digest("SHA-256", input);
}

async function sha256Text(value: string) {
  return toHex(await sha256(encoder.encode(value)));
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "sales-ledger.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function mapAnalysisSafeRow(row: SalesLedgerRow): SalesLineInput {
  // Explicit allow-list: personal/account/logistics details stay outside the
  // analytics boundary before any data leaves the Worker.
  return {
    sourceRowNumber: row.sourceRowNumber,
    sourceLineKey: row.sourceLineKey,
    sourceRowHash: row.sourceRowHash,
    orderNo: row.orderNo,
    onlineOrderNo: row.onlineOrderNo,
    channel: row.channel,
    platform: row.platform,
    shopName: row.shopName,
    logisticsCompany: row.logisticsCompany,
    warehouse: row.warehouse,
    productCode: row.productCode,
    onlineSpecCode: row.onlineSpecCode,
    productName: row.productName,
    specification: row.specification,
    barcode: row.barcode,
    supplier: row.supplier,
    category: row.category,
    quantity: row.quantity,
    listUnitPriceCents: row.listUnitPriceCents,
    costAmountCents: row.costAmountCents,
    allocatedUnitPriceCents: row.allocatedUnitPriceCents,
    allocatedAmountCents: row.allocatedAmountCents,
    feeAllocationCents: row.feeAllocationCents,
    grossProfitCents: row.grossProfitCents,
    grossMarginBps: row.grossMarginBps ?? 0,
    untaxedGrossProfitCents: row.untaxedGrossProfitCents,
    untaxedGrossMarginBps: row.untaxedGrossMarginBps ?? 0,
    orderTime: row.orderTime,
    salesTime: row.salesTime,
    shipTime: row.shipTime ?? row.orderTime,
    lineShipTime: row.lineShipTime ?? "",
    businessType: row.businessType,
  };
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

function addUtcDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function validateSalesImportChannels(value: unknown) {
  if (value === undefined || value === null || value === "") {
    return { ok: true as const, channels: null };
  }
  let parsed = value;
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value);
    } catch {
      return { ok: false as const, code: "INVALID_EXPECTED_CHANNELS", message: "expectedChannels 必须是 JSON 字符串数组" };
    }
  }
  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.length > MAX_SALES_IMPORT_CHANNELS) {
    return { ok: false as const, code: "INVALID_EXPECTED_CHANNELS", message: `expectedChannels 必须包含 1 到 ${MAX_SALES_IMPORT_CHANNELS} 个销售渠道` };
  }
  const channels = parsed.map((item) => typeof item === "string" ? item.trim() : "");
  if (channels.some((channel) => !channel || channel.length > 100)) {
    return { ok: false as const, code: "INVALID_EXPECTED_CHANNELS", message: "expectedChannels 只能包含非空且不超过 100 字符的渠道名" };
  }
  const unique = [...new Set(channels)].sort((left, right) => left.localeCompare(right, "zh-CN"));
  if (unique.length !== channels.length) {
    return { ok: false as const, code: "DUPLICATE_EXPECTED_CHANNELS", message: "expectedChannels 不能包含重复渠道" };
  }
  const unapproved = unique.filter((channel) => !salesImportPolicy.approvedSalesChannels.includes(channel));
  if (unapproved.length > 0) {
    return { ok: false as const, code: "UNAPPROVED_EXPECTED_CHANNELS", message: `expectedChannels 包含未纳入白名单的渠道：${unapproved.join("、")}` };
  }
  return { ok: true as const, channels: unique };
}

function realIsoDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1900 || year > 2199) return null;
  const epochMs = Date.UTC(year, month - 1, day);
  const parsed = new Date(epochMs);
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return null;
  return { value, epochMs };
}

export function validateSalesImportDateRange(startDate: string, endDate: string) {
  const start = realIsoDate(startDate);
  const end = realIsoDate(endDate);
  if (!start || !end || start.epochMs > end.epochMs) {
    return { ok: false as const, code: "INVALID_EXPECTED_DATE_RANGE", message: "起止日期必须为真实的 YYYY-MM-DD 自然日，且开始日期不能晚于结束日期" };
  }
  const dayCount = Math.floor((end.epochMs - start.epochMs) / 86_400_000) + 1;
  if (dayCount > MAX_SALES_IMPORT_RANGE_DAYS) {
    return { ok: false as const, code: "EXPECTED_DATE_RANGE_TOO_LARGE", message: `单次销售导入日期范围最多 ${MAX_SALES_IMPORT_RANGE_DAYS} 天` };
  }
  return {
    ok: true as const,
    startDate: start.value,
    endDate: end.value,
    endExclusive: new Date(end.epochMs + 86_400_000).toISOString().slice(0, 10),
    dayCount,
  };
}

function validateRows(rows: readonly SalesLineInput[]): SalesImportIssue[] {
  const errors: SalesImportIssue[] = [];
  const keys = new Set<string>();
  const integerFields = [
    "sourceRowNumber", "quantity", "listUnitPriceCents", "costAmountCents",
    "allocatedUnitPriceCents", "allocatedAmountCents", "feeAllocationCents",
    "grossProfitCents", "grossMarginBps", "untaxedGrossProfitCents", "untaxedGrossMarginBps",
  ] as const;
  if (rows.length === 0) return [{ code: "NO_DATA_ROWS", message: "工作表中没有可导入的销售明细行" }];
  if (rows.length > MAX_STAGED_ROWS) return [{ code: "ROW_LIMIT", message: `销售明细总行数超过 ${MAX_STAGED_ROWS} 行上限` }];
  for (const row of rows) {
    if (!row.sourceLineKey || !row.sourceRowHash) {
      errors.push({ row: row.sourceRowNumber, code: "MISSING_ROW_KEY", message: "明细行缺少唯一标识" });
    } else if (keys.has(row.sourceLineKey)) {
      errors.push({ row: row.sourceRowNumber, code: "DUPLICATE_ROW_KEY", message: "文件内存在重复的销售明细行" });
    } else keys.add(row.sourceLineKey);
    if (!/^\d{4}-\d{2}-\d{2}/.test(row.salesTime)) {
      errors.push({ row: row.sourceRowNumber, field: "salesTime", code: "INVALID_SALES_TIME", message: "销售时间无效" });
    }
    if (!row.orderNo && !row.onlineOrderNo) {
      errors.push({ row: row.sourceRowNumber, field: "orderNo", code: "MISSING_ORDER_NO", message: "销售单号和线上单号不能同时为空" });
    }
    for (const field of integerFields) {
      if (!Number.isSafeInteger(row[field])) {
        errors.push({ row: row.sourceRowNumber, field, code: "INVALID_INTEGER", message: `${field} 必须是安全整数` });
      }
    }
    if (!( ["sale", "return", "zero"] as const).includes(row.businessType)) {
      errors.push({ row: row.sourceRowNumber, field: "businessType", code: "INVALID_BUSINESS_TYPE", message: "业务类型无效" });
    }
    if (errors.length >= 200) break;
  }
  return errors;
}

function effectiveRowsForCost(
  rows: readonly SalesLineInput[],
  startDate: string,
  endDate: string,
  expectedChannels: readonly string[] | null,
) {
  const cutoff = addUtcDays(shanghaiToday(), -1);
  const channels = expectedChannels ? new Set(expectedChannels) : null;
  return rows.filter((row) => {
    const businessDate = row.shipTime.slice(0, 10);
    return businessDate >= startDate && businessDate <= endDate && businessDate <= cutoff
      && !isExcludedSalesWarehouse(row.warehouse)
      && isApprovedSalesChannel(row.channel)
      && (!channels || channels.has(row.channel));
  });
}

function hasCleanableZeroCostRows(rows: readonly SalesLineInput[]) {
  return rows.some((row) => row.costAmountCents === 0
    && row.productCode !== "ERP_PRICE_ADJUSTMENT"
    && !isZeroCostProductName(row.productName));
}

function splitRowsForStaging(rows: readonly SalesLineInput[]): SalesLineInput[][] {
  const chunks: SalesLineInput[][] = [];
  let current: SalesLineInput[] = [];
  let bytes = 2;
  for (const row of rows) {
    const rowBytes = encoder.encode(JSON.stringify(row)).byteLength + (current.length === 0 ? 0 : 1);
    if (rowBytes + 64 > TARGET_STAGED_BODY_BYTES) {
      throw new Error("单行销售明细超过规范化分片请求上限");
    }
    if (current.length >= MAX_ROWS_PER_STAGED_CHUNK || bytes + rowBytes > TARGET_STAGED_BODY_BYTES) {
      chunks.push(current);
      current = [];
      bytes = 2;
    }
    current.push(row);
    bytes += rowBytes;
  }
  if (current.length > 0) chunks.push(current);
  return chunks;
}

export type SalesImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  code?: string;
  batch?: SalesImportBatch;
  warnings: SalesImportIssue[];
  errors?: SalesImportIssue[];
  errorCount?: number;
};

function rejected(message: string, code: string, warnings: SalesImportIssue[] = []): SalesImportExecution {
  return {
    ok: false,
    status: "rejected",
    message,
    code,
    warnings,
    errors: [{ code, message }],
    errorCount: 1,
  };
}

function upstreamImportPayload(error: unknown): SalesImportExecution | null {
  if (!(error instanceof DjangoSalesServiceResponseError)) return null;
  const payload = error.payload;
  if (payload.status !== "rejected" || payload.ok !== false || typeof payload.message !== "string") return null;
  return {
    ok: false,
    status: "rejected",
    message: payload.message,
    ...(typeof payload.code === "string" ? { code: payload.code } : {}),
    warnings: sanitizeSalesIssues(Array.isArray(payload.warnings) ? payload.warnings : []),
    errors: sanitizeSalesIssues(Array.isArray(payload.errors) ? payload.errors : []),
    errorCount: Number.isSafeInteger(payload.errorCount) ? Number(payload.errorCount) : undefined,
  };
}

export async function importSalesLedgerBytes(input: {
  principal: AppPrincipal;
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  expectedStartDate: string;
  expectedEndDate: string;
  expectedChannels?: unknown;
  rawUploadId?: string;
  rawUploadOwnerToken?: string;
  fingerprint?: string;
}): Promise<SalesImportExecution> {
  const dateRange = validateSalesImportDateRange(input.expectedStartDate, input.expectedEndDate);
  if (!dateRange.ok) return rejected("销售导入必须提供有效的权威起止日期", dateRange.code);
  const channelScope = validateSalesImportChannels(input.expectedChannels);
  if (!channelScope.ok) return rejected("销售导入必须提供有效的权威渠道范围", channelScope.code);
  const expectedChannels = channelScope.channels;
  const rawFileHash = toHex(await sha256(input.bytes));
  const fileName = safeFileName(input.fileName);

  let sourceTotals: SalesLedgerTotals = {} as SalesLedgerTotals;
  let sheetName = "销售单明细账";
  let rows: SalesLineInput[] = [];
  let parserWarnings: SalesImportIssue[] = [];
  let parserErrors: SalesImportIssue[] = [];
  if (!isXlsxSignature(input.bytes)) {
    parserErrors = [{ code: "INVALID_XLSX_SIGNATURE", message: "文件签名不是有效的 .xlsx（ZIP）格式" }];
  } else {
    try {
      const parsed = await parseSalesLedgerXlsx(input.bytes);
      sourceTotals = parsed.totals;
      sheetName = parsed.sheetName || sheetName;
      rows = parsed.rows.map(mapAnalysisSafeRow);
      parserWarnings = sanitizeSalesIssues(parsed.warnings ?? []);
      parserErrors = sanitizeSalesIssues([...(parsed.errors ?? []), ...validateRows(rows)]);
    } catch {
      parserErrors = [{ code: "XLSX_PARSE_ERROR", message: "销售 Excel 文件解析失败，请确认文件格式和模板" }];
    }
  }

  let systemCostSnapshot: Record<string, unknown> | null = null;
  if (parserErrors.length === 0
    && hasCleanableZeroCostRows(effectiveRowsForCost(rows, dateRange.startDate, dateRange.endDate, expectedChannels))) {
    const snapshot = await findLatestAuthoritativeSystemCostSnapshot(input.principal);
    if (snapshot) {
      systemCostSnapshot = {
        sourceBatchId: snapshot.batchId,
        snapshotDate: snapshot.snapshotDate,
        costs: snapshot.costs,
      };
    }
  }

  const chunks = parserErrors.length === 0 ? splitRowsForStaging(rows) : [];
  const scopeDigest = await sha256Text(JSON.stringify({
    startDate: dateRange.startDate,
    endDate: dateRange.endDate,
    channels: expectedChannels,
  }));
  const fingerprint = input.fingerprint?.trim()
    || `sales-stage-v1:${rawFileHash}:${scopeDigest}`;
  try {
    const initialized = await requestDjangoSalesService<{ session: { id: string; status: string; receivedChunkIndexes: number[] } }>(input.principal, {
      method: "POST",
      path: SALES_STAGED_IMPORTS_PATH,
      service: "writer",
      payload: {
        action: "init",
        ...(input.rawUploadId ? { rawUploadId: input.rawUploadId } : {}),
        ...(input.rawUploadOwnerToken ? { rawUploadOwnerToken: input.rawUploadOwnerToken } : {}),
        fingerprint,
        fileName,
        fileSizeBytes: input.fileSizeBytes,
        rawFileHash,
        sheetName,
        expectedStartDate: dateRange.startDate,
        expectedEndDate: dateRange.endDate,
        expectedChannels,
        chunkCount: Math.max(chunks.length, 1),
        sourceTotals,
        parserWarnings,
        parserErrors,
        systemCostSnapshot,
      },
    });
    const session = initialized.data.session;
    if (!session || typeof session.id !== "string" || !Array.isArray(session.receivedChunkIndexes)) {
      throw new Error("Django 返回的规范化导入会话无效");
    }
    const received = new Set(session.receivedChunkIndexes);
    for (let index = 0; index < chunks.length; index += 1) {
      if (received.has(index)) continue;
      await requestDjangoSalesService(input.principal, {
        method: "PUT",
        path: SALES_STAGED_IMPORTS_PATH,
        service: "writer",
        payload: {
          sessionId: session.id,
          chunkIndex: index,
          rows: chunks[index],
          ...(input.rawUploadOwnerToken ? { rawUploadOwnerToken: input.rawUploadOwnerToken } : {}),
        },
      });
    }
    const completed = await requestDjangoSalesService<SalesImportExecution>(input.principal, {
      method: "POST",
      path: SALES_STAGED_IMPORTS_PATH,
      service: "writer",
      payload: {
        action: "complete",
        sessionId: session.id,
        ...(input.rawUploadOwnerToken ? { rawUploadOwnerToken: input.rawUploadOwnerToken } : {}),
      },
    });
    return {
      ...completed.data,
      warnings: sanitizeSalesIssues(completed.data.warnings ?? []),
    };
  } catch (error) {
    const payload = upstreamImportPayload(error);
    if (payload) return payload;
    throw error;
  }
}
