import { parseXlsxFirstSheets, type XlsxCellValue } from "@/lib/imports/xlsx";

export const ANNUAL_TARGET_IMPORT_SCHEMA_VERSION = "finance-annual-target-import-v1" as const;
export const MAX_ANNUAL_TARGET_FILE_BYTES = 2 * 1024 * 1024;
export const MAX_ANNUAL_TARGET_ROWS = 500;

export type AnnualTargetImportRow = {
  rowNumber: number;
  storeLabel: string;
  manager: string;
  salesTargetCents: number;
  profitTargetCents: number;
  grossMarginBps: number;
  promotionFeeRatioBps: number;
};

export type ParsedAnnualTargetWorkbook = {
  schemaVersion: typeof ANNUAL_TARGET_IMPORT_SCHEMA_VERSION;
  sheetName: string;
  headerRowNumber: number;
  sourceRowCount: number;
  skippedRowCount: number;
  rows: AnnualTargetImportRow[];
};

const REQUIRED_HEADERS = ["店铺", "负责人", "利润目标", "大毛利率目标", "推广费目标", "调整目标", "销售目标"] as const;
const MAX_TARGET_AMOUNT_CENTS = 10_000_000_000_000;

function normalizedText(value: XlsxCellValue | undefined): string {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[\s　]+/g, " ").trim();
}

function parseNumber(value: XlsxCellValue | undefined, rowNumber: number, label: string): { present: boolean; value: number } {
  if (value === null || value === undefined || (typeof value === "string" && value.trim() === "")) {
    return { present: false, value: 0 };
  }
  if (typeof value === "boolean") throw new Error(`第 ${rowNumber} 行“${label}”必须是数字`);
  const normalized = typeof value === "string"
    ? value.replace(/[,，\s￥¥]/g, "").replace(/万元$/u, "")
    : value;
  const numeric = typeof normalized === "number" ? normalized : Number(normalized);
  if (!Number.isFinite(numeric) || numeric < 0) throw new Error(`第 ${rowNumber} 行“${label}”必须是非负数字`);
  return { present: true, value: numeric };
}

function moneyWanToCents(value: XlsxCellValue | undefined, rowNumber: number, label: string) {
  const parsed = parseNumber(value, rowNumber, label);
  const cents = Math.round(parsed.value * 1_000_000);
  if (!Number.isSafeInteger(cents) || cents > MAX_TARGET_AMOUNT_CENTS) {
    throw new Error(`第 ${rowNumber} 行“${label}”超出允许范围`);
  }
  return { present: parsed.present, value: cents };
}

function rateToBps(value: XlsxCellValue | undefined, rowNumber: number, label: string) {
  const text = typeof value === "string" ? value.trim() : "";
  const percentageText = text.endsWith("%");
  const parsed = parseNumber(percentageText ? text.slice(0, -1) : value, rowNumber, label);
  const ratio = percentageText ? parsed.value / 100 : parsed.value;
  const bps = Math.round((ratio <= 1 ? ratio : ratio / 100) * 10_000);
  if (!Number.isSafeInteger(bps) || bps > 10_000) throw new Error(`第 ${rowNumber} 行“${label}”必须在 0% 到 100% 之间`);
  return { present: parsed.present, value: bps };
}

export function parseAnnualTargetWorkbook(input: ArrayBuffer | Uint8Array): ParsedAnnualTargetWorkbook {
  const [sheet] = parseXlsxFirstSheets(input, 1, {
    maxCompressedBytes: MAX_ANNUAL_TARGET_FILE_BYTES,
    maxUncompressedBytes: 8 * 1024 * 1024,
    maxWorksheetBytes: 6 * 1024 * 1024,
    maxRows: MAX_ANNUAL_TARGET_ROWS + 50,
  });
  if (!sheet) throw new Error("工作簿中没有可导入的工作表");
  const header = sheet.rows.slice(0, 30).map((row) => {
    const indices = new Map<string, number>();
    row.cells.forEach((cell, index) => {
      const label = normalizedText(cell);
      if (label && !indices.has(label)) indices.set(label, index);
    });
    return { row, indices };
  }).find(({ indices }) => REQUIRED_HEADERS.slice(0, 5).every((label) => indices.has(label)) && (indices.has("调整目标") || indices.has("销售目标")));
  if (!header) throw new Error(`未找到完整表头：${REQUIRED_HEADERS.slice(0, 5).join("、")}、调整目标或销售目标`);

  const rows: AnnualTargetImportRow[] = [];
  const seenLabels = new Map<string, number>();
  let skippedRowCount = 0;
  let sourceRowCount = 0;
  for (const row of sheet.rows) {
    if (row.rowNumber <= header.row.rowNumber) continue;
    const storeLabel = normalizedText(row.cells[header.indices.get("店铺")!]);
    if (!storeLabel) continue;
    sourceRowCount += 1;
    if (storeLabel.length > 200) throw new Error(`第 ${row.rowNumber} 行店铺名称超过 200 字`);
    const manager = normalizedText(row.cells[header.indices.get("负责人")!]);
    if (manager.length > 120) throw new Error(`第 ${row.rowNumber} 行负责人超过 120 字`);
    const profit = moneyWanToCents(row.cells[header.indices.get("利润目标")!], row.rowNumber, "利润目标");
    const grossMargin = rateToBps(row.cells[header.indices.get("大毛利率目标")!], row.rowNumber, "大毛利率目标");
    const promotion = rateToBps(row.cells[header.indices.get("推广费目标")!], row.rowNumber, "推广费目标");
    const salesColumnIndex = header.indices.get("销售目标") ?? header.indices.get("调整目标")!;
    const sales = moneyWanToCents(row.cells[salesColumnIndex], row.rowNumber, header.indices.get("销售目标") !== undefined ? "销售目标" : "调整目标");
    if (!manager && !profit.present && !grossMargin.present && !promotion.present && !sales.present) {
      skippedRowCount += 1;
      continue;
    }
    const previousRow = seenLabels.get(storeLabel);
    if (previousRow !== undefined) throw new Error(`第 ${row.rowNumber} 行店铺与第 ${previousRow} 行重复：${storeLabel}`);
    seenLabels.set(storeLabel, row.rowNumber);
    rows.push({
      rowNumber: row.rowNumber,
      storeLabel,
      manager,
      salesTargetCents: sales.value,
      profitTargetCents: profit.value,
      grossMarginBps: grossMargin.value,
      promotionFeeRatioBps: promotion.value,
    });
    if (rows.length > MAX_ANNUAL_TARGET_ROWS) throw new Error(`有效店铺超过 ${MAX_ANNUAL_TARGET_ROWS} 行上限`);
  }
  if (rows.length === 0) throw new Error("表格中没有可导入的店铺年度目标");
  return {
    schemaVersion: ANNUAL_TARGET_IMPORT_SCHEMA_VERSION,
    sheetName: sheet.sheetName,
    headerRowNumber: header.row.rowNumber,
    sourceRowCount,
    skippedRowCount,
    rows,
  };
}
