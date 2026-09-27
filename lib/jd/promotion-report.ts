import { createHash } from "node:crypto";

export const JD_PROMOTION_REQUIRED_HEADERS = [
  "日期",
  "跟单SKU ID",
  "产品线",
  "账户昵称",
  "展现数",
  "点击数",
  "花费",
  "总订单行",
  "总订单金额",
] as const;

export type JdPromotionReportInspection = {
  rowCount: number;
  columnCount: number;
  dateMin: string;
  dateMax: string;
  accountNicknames: string[];
  productLines: string[];
  uniqueSkuCount: number;
  impressions: number;
  clicks: number;
  spendYuan: number;
  totalOrders: number;
  totalOrderAmountYuan: number;
  sha256: string;
};

export type JdPromotionDownloadTask = {
  fingerprint: string;
  reportName: string;
  status: string;
  startDate: string;
  endDate: string;
  createdAt: string;
};

function normalize(value: unknown) {
  return String(value ?? "").replace(/\uFEFF/g, "").trim();
}

function compactDate(value: string) {
  const match = /^(20\d{2})[-/.]?(\d{2})[-/.]?(\d{2})$/.exec(normalize(value));
  if (!match) return "";
  const date = `${match[1]}-${match[2]}-${match[3]}`;
  const parsed = new Date(`${date}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date ? "" : date;
}

function numeric(value: string, header: string, rowNumber: number) {
  const text = normalize(value).replace(/[,￥¥%]/g, "");
  if (!text || text === "-") return 0;
  const number = Number(text);
  if (!Number.isFinite(number)) throw new Error(`京准通 CSV 第 ${rowNumber} 行“${header}”不是有效数字`);
  return number;
}

export function parseJdPromotionCsv(text: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]!;
    const next = text[index + 1];
    if (character === '"') {
      if (quoted && next === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (!quoted && character === ",") {
      row.push(cell);
      cell = "";
    } else if (!quoted && (character === "\n" || character === "\r")) {
      if (character === "\r" && next === "\n") index += 1;
      row.push(cell);
      if (row.some((value) => normalize(value))) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += character;
    }
  }
  if (quoted) throw new Error("京准通 CSV 存在未闭合的引号");
  if (cell || row.length) {
    row.push(cell);
    if (row.some((value) => normalize(value))) rows.push(row);
  }
  return rows;
}

export function jdPromotionReportPrefix(accountLabel: string, startDate: string, endDate: string) {
  const start = compactDate(startDate);
  const end = compactDate(endDate);
  if (!start || !end || start > end || !normalize(accountLabel)) throw new Error("京准通报表前缀缺少有效账号或日期范围");
  return `${normalize(accountLabel)}_AI推广数据自动下载_${start.replaceAll("-", "")}_${end.replaceAll("-", "")}`;
}

function inclusiveDates(startDate: string, endDate: string) {
  const dates: string[] = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  while (cursor <= end) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
    if (dates.length > 31) throw new Error("京准通推广报表单次日期范围不能超过 31 天");
  }
  return dates;
}

export function inspectJdPromotionCsv(bytes: Uint8Array, expectedStartDate: string, expectedEndDate: string): JdPromotionReportInspection {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("京准通推广报表必须是 UTF-8 CSV");
  }
  const matrix = parseJdPromotionCsv(text);
  if (matrix.length < 2) throw new Error("京准通推广报表没有业务数据行");
  if (matrix.length > 50_001) throw new Error("京准通推广报表超过 50000 行上限");
  const headers = matrix[0]!.map(normalize);
  if (new Set(headers).size !== headers.length) throw new Error("京准通推广报表存在重复表头");
  for (const header of JD_PROMOTION_REQUIRED_HEADERS) {
    if (!headers.includes(header)) throw new Error(`京准通推广报表缺少表头：${header}`);
  }
  const headerIndex = new Map(headers.map((header, index) => [header, index]));
  const value = (row: string[], header: string) => row[headerIndex.get(header)!] ?? "";
  const expectedStart = compactDate(expectedStartDate);
  const expectedEnd = compactDate(expectedEndDate);
  if (!expectedStart || !expectedEnd || expectedStart > expectedEnd) throw new Error("京准通推广报表目标日期范围无效");
  const expectedDates = inclusiveDates(expectedStart, expectedEnd);
  const dates = new Set<string>();
  const accounts = new Set<string>();
  const productLines = new Set<string>();
  const skuIds = new Set<string>();
  let impressions = 0;
  let clicks = 0;
  let spendYuan = 0;
  let totalOrders = 0;
  let totalOrderAmountYuan = 0;
  for (const [index, row] of matrix.slice(1).entries()) {
    const rowNumber = index + 2;
    if (row.length > headers.length) throw new Error(`京准通 CSV 第 ${rowNumber} 行列数超过表头`);
    const date = compactDate(value(row, "日期"));
    if (!date) throw new Error(`京准通 CSV 第 ${rowNumber} 行日期无效`);
    dates.add(date);
    const account = normalize(value(row, "账户昵称"));
    if (!account || account === "-") throw new Error(`京准通 CSV 第 ${rowNumber} 行账户昵称为空`);
    accounts.add(account);
    const productLine = normalize(value(row, "产品线"));
    if (productLine && productLine !== "-") productLines.add(productLine);
    const skuId = normalize(value(row, "跟单SKU ID"));
    if (skuId && skuId !== "-") skuIds.add(skuId);
    impressions += numeric(value(row, "展现数"), "展现数", rowNumber);
    clicks += numeric(value(row, "点击数"), "点击数", rowNumber);
    spendYuan += numeric(value(row, "花费"), "花费", rowNumber);
    totalOrders += numeric(value(row, "总订单行"), "总订单行", rowNumber);
    totalOrderAmountYuan += numeric(value(row, "总订单金额"), "总订单金额", rowNumber);
  }
  const actualDates = [...dates].sort();
  if (actualDates.length !== expectedDates.length || actualDates.some((date, index) => date !== expectedDates[index])) {
    throw new Error(`京准通推广报表必须完整覆盖 ${expectedStart} 至 ${expectedEnd}，实际为 ${actualDates.join("、") || "空"}`);
  }
  return {
    rowCount: matrix.length - 1,
    columnCount: headers.length,
    dateMin: expectedStart,
    dateMax: expectedEnd,
    accountNicknames: [...accounts].sort(),
    productLines: [...productLines].sort(),
    uniqueSkuCount: skuIds.size,
    impressions,
    clicks,
    spendYuan: Math.round(spendYuan * 100) / 100,
    totalOrders,
    totalOrderAmountYuan: Math.round(totalOrderAmountYuan * 100) / 100,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}

export function selectJdPromotionDownloadTask(
  tasks: readonly JdPromotionDownloadTask[],
  expectedPrefix: string,
  expectedStartDate: string,
  expectedEndDate: string,
  baselineFingerprints: ReadonlySet<string> = new Set(),
) {
  const matches = tasks.filter((task) => task.reportName.startsWith(expectedPrefix)
    && task.startDate === expectedStartDate && task.endDate === expectedEndDate
    && !baselineFingerprints.has(task.fingerprint));
  if (matches.length > 1) throw new Error("京准通下载中心出现多个本轮候选任务，拒绝猜测接管");
  return matches[0] ?? null;
}

export function validateJdPromotionImportProof(input: {
  payload: Record<string, unknown>;
  shopName: string;
  startDate: string;
  endDate: string;
  rowCount: number;
  rawFileHash: string;
}) {
  const batch = input.payload.batch as Record<string, unknown> | undefined;
  const verification = input.payload.verification as Record<string, unknown> | undefined;
  const totals = batch?.totals as Record<string, unknown> | undefined;
  if (input.payload.ok !== true || !["imported", "duplicate"].includes(String(input.payload.status))
    || !batch || batch.status !== "completed" || batch.source !== "jd_promotion" || batch.dataset !== "ad"
    || batch.platform !== "京东" || batch.shopName !== input.shopName || batch.rowCount !== input.rowCount
    || batch.warningCount !== 0 || batch.dateMin !== input.startDate || batch.dateMax !== input.endDate
    || totals?.rawFileHash !== input.rawFileHash || !verification || verification.verified !== true
    || verification.readbackRowCount !== input.rowCount || verification.dateMin !== input.startDate
    || verification.dateMax !== input.endDate || verification.shopName !== input.shopName) {
    throw new Error("京准通推广导入返回缺少精确批次、文件哈希、日期或落库回查证明");
  }
  if (typeof batch.id !== "string" || !batch.id) throw new Error("京准通推广导入批次 ID 缺失");
  return {
    status: input.payload.status as "imported" | "duplicate",
    batchId: batch.id,
    rowCount: input.rowCount,
    warningCount: 0 as const,
    source: "jd_promotion" as const,
    dataset: "ad" as const,
    platform: "京东" as const,
    shopName: input.shopName,
    dateMin: input.startDate,
    dateMax: input.endDate,
    rawFileHash: input.rawFileHash,
  };
}
