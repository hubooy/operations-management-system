import * as XLSX from "xlsx";
import type {
  FinanceImportIssue,
  FinanceLineInput,
  FinanceScopeType,
  FinanceValueType,
  ParsedFinanceMonth,
  ParsedFinanceWorkbook,
} from "./types";
import type { FinanceCellOrigin, FinanceColumnEvidenceInput, FinanceHeaderCell } from "./column-evidence-v2";

type CellValue = string | number | boolean | Date | null | undefined;
type Dimension = {
  columnIndex: number;
  scopeKey: string;
  scopeType: FinanceScopeType;
  scopeName: string;
  groupName: string;
};

const summaryMetricRules: Array<[RegExp, string]> = [
  [/^一①.*销售额/, "gross_sales"],
  [/^一②.*退货金额/, "return_amount"],
  [/^一③.*实际销售金额/, "net_sales"],
  [/^二①.*发货总成本/, "shipping_cost"],
  [/^二②.*退货退回成本/, "return_cost"],
  [/^二③.*包材/, "packaging_cost"],
  [/^二④.*实际发货成本/, "net_cost"],
  [/^三①.*其他业务收入/, "other_income"],
  [/^四①.*大毛利/, "gross_profit"],
  [/^四②.*大毛利率/, "gross_margin"],
  [/^六①.*销售费用合计/, "selling_expense_total"],
  [/^六②.*销售费用.*运营费/, "operation_expense"],
  [/^六③.*销售费用.*工资/, "salary_expense"],
  [/^六④.*小毛利/, "small_profit"],
  [/^六⑤.*小毛利率/, "small_margin"],
  [/^七①.*其他费用合计/, "other_expense_total"],
  [/^七①.*仓库租金/, "warehouse_rent"],
  [/^七②.*管理费用/, "management_expense"],
  [/^七③.*管理部.*工资/, "management_salary"],
  [/^七④.*财务费用/, "finance_expense"],
  [/^七⑤.*税费/, "tax_expense"],
  [/^八.*营业外收入/, "non_operating_income"],
  [/^九.*其他业务支出/, "other_business_expense"],
  [/^十.*利[润潤]/, "profit"],
  [/^利润率/, "profit_margin"],
];

function cleanText(value: CellValue): string {
  return String(value ?? "").replace(/\r/g, "").trim();
}

function canonicalSubject(value: CellValue): string {
  return cleanText(value).replace(/[\s　]+/g, "");
}

function scopeIdentity(value: string): string {
  return value.replace(/[\s　]+/g, "").toLowerCase();
}

function summaryMetricKey(subjectName: string): string {
  for (const [pattern, key] of summaryMetricRules) {
    if (pattern.test(subjectName)) return key;
  }
  return `dynamic:${subjectName}`;
}

function parseMonth(sheetName: string, title: CellValue): string | null {
  const titleMatch = /(20\d{2})\s*年\s*(\d{1,2})\s*月/.exec(cleanText(title));
  const sheetMatch = /^(\d{2}|20\d{2})[.\-/年](\d{1,2})(?:月)?$/.exec(sheetName.trim());
  // Historical workbooks in the same file use compact names such as 201811.
  // Only explicit month-sheet names (26.1 / 2026-01 / 2026年1月) belong to
  // the new monthly-finance import contract.
  if (!sheetMatch) return null;
  const rawYear = Number(titleMatch?.[1] ?? sheetMatch?.[1]);
  const year = rawYear < 100 ? 2000 + rawYear : rawYear;
  const month = Number(titleMatch?.[2] ?? sheetMatch?.[2]);
  if (year < 2000 || year > 2100 || month < 1 || month > 12) return null;
  return `${year}-${String(month).padStart(2, "0")}`;
}

function parseNumeric(value: CellValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/,/g, "");
  if (!normalized) return null;
  const percent = normalized.endsWith("%");
  const parsed = Number(percent ? normalized.slice(0, -1) : normalized);
  if (!Number.isFinite(parsed)) return null;
  return percent ? parsed / 100 : parsed;
}

function valueTypeForSummary(subjectName: string, rawValue: CellValue): FinanceValueType {
  if (/率|占比/.test(subjectName)) return "rate";
  if (/时间|日期/.test(subjectName)) return "text";
  return parseNumeric(rawValue) === null ? "text" : "amount";
}

function toStoredValue(value: CellValue, valueType: FinanceValueType) {
  const numeric = parseNumeric(value);
  return {
    amountCents: valueType === "amount" && numeric !== null ? Math.round(numeric * 100) : null,
    rateBps: valueType === "rate" && numeric !== null ? Math.round(numeric * 10_000) : null,
    rawValue: cleanText(value),
  };
}

function buildDimensions(rows: CellValue[][]): Dimension[] {
  const titleRow = rows[0] ?? [];
  const groupRow = rows[1] ?? [];
  const shopRow = rows[2] ?? [];
  const maxColumns = Math.max(titleRow.length, groupRow.length, shopRow.length);
  const businessName = canonicalSubject(titleRow[1]) || "事业部汇总";
  const dimensions: Dimension[] = [{
    columnIndex: 1,
    scopeKey: `business:${scopeIdentity(businessName)}`,
    scopeType: "business",
    scopeName: businessName,
    groupName: "",
  }];
  let currentGroup = "";

  for (let columnIndex = 2; columnIndex < maxColumns; columnIndex += 1) {
    const rawGroup = canonicalSubject(groupRow[columnIndex]);
    if (rawGroup) currentGroup = rawGroup;
    const rawShop = canonicalSubject(shopRow[columnIndex]);
    if (!rawShop) continue;
    const isGroup = rawShop === "组汇总";
    const scopeName = isGroup ? (currentGroup || `第${columnIndex + 1}列组汇总`) : rawShop;
    dimensions.push({
      columnIndex,
      scopeKey: `${isGroup ? "group" : "shop"}:${scopeIdentity(scopeName)}`,
      scopeType: isGroup ? "group" : "shop",
      scopeName,
      groupName: currentGroup,
    });
  }
  return dimensions;
}

function aggregateLines(lines: FinanceLineInput[]): FinanceLineInput[] {
  const aggregated = new Map<string, FinanceLineInput>();
  for (const line of lines) {
    const key = `${line.section}\u0000${line.scopeKey}\u0000${line.subjectName}`;
    const current = aggregated.get(key);
    if (!current) {
      aggregated.set(key, { ...line });
      continue;
    }
    if (line.amountCents !== null) current.amountCents = (current.amountCents ?? 0) + line.amountCents;
    if (line.rateBps !== null) current.rateBps = (current.rateBps ?? 0) + line.rateBps;
    current.sourceRowCount += line.sourceRowCount;
    current.sortOrder = Math.min(current.sortOrder, line.sortOrder);
    current.isTotal ||= line.isTotal;
  }
  return [...aggregated.values()].sort((left, right) =>
    left.section.localeCompare(right.section)
    || left.sortOrder - right.sortOrder
    || left.scopeType.localeCompare(right.scopeType)
    || left.scopeName.localeCompare(right.scopeName),
  );
}

function recalculateKingdeeTotals(lines: FinanceLineInput[]) {
  const byScope = new Map<string, FinanceLineInput[]>();
  lines.filter((line) => line.section === "kingdee").forEach((line) => {
    const items = byScope.get(line.scopeKey) ?? [];
    items.push(line);
    byScope.set(line.scopeKey, items);
  });
  for (const items of byScope.values()) {
    const total = items.find((line) => line.subjectName === "销售费用");
    if (!total) continue;
    const detailTotal = items
      .filter((line) => line.subjectName.startsWith("销售费用_") && !line.isTotal)
      .reduce((sum, line) => sum + (line.amountCents ?? 0), 0);
    total.amountCents = detailTotal;
    total.rawValue = String(detailTotal / 100);
  }
}

function reconciliationWarnings(month: ParsedFinanceMonth): FinanceImportIssue[] {
  const warnings: FinanceImportIssue[] = [];
  const byScope = new Map<string, FinanceLineInput[]>();
  month.lines.forEach((line) => {
    const items = byScope.get(line.scopeKey) ?? [];
    items.push(line);
    byScope.set(line.scopeKey, items);
  });

  for (const lines of byScope.values()) {
    const kingdeeTotal = lines.find((line) => line.section === "kingdee" && line.subjectName === "销售费用");
    const detailTotal = lines
      .filter((line) => line.section === "kingdee" && line.subjectName.startsWith("销售费用_") && !line.isTotal)
      .reduce((sum, line) => sum + (line.amountCents ?? 0), 0);
    const reference = kingdeeTotal?.amountCents ?? 0;
    const tolerance = Math.max(100, Math.round(Math.abs(reference) * 0.005));
    if (kingdeeTotal?.amountCents != null && Math.abs(kingdeeTotal.amountCents - detailTotal) > tolerance) {
      warnings.push({
        sheet: month.sheetName,
        month: month.month,
        code: "KINGDEE_DETAIL_MISMATCH",
        message: `${kingdeeTotal.scopeName}的销售费用总额与明细合计相差¥${(Math.abs(kingdeeTotal.amountCents - detailTotal) / 100).toFixed(2)}`,
      });
    }
  }
  return warnings;
}

function parseMonthSheet(sheetName: string, sheet: XLSX.WorkSheet, captureOrigins = false): {
  month: ParsedFinanceMonth | null;
  warnings: FinanceImportIssue[];
  evidenceInput?: FinanceColumnEvidenceInput;
} {
  const rows = XLSX.utils.sheet_to_json<CellValue[]>(sheet, {
    header: 1,
    raw: true,
    defval: null,
    blankrows: true,
  });
  const month = parseMonth(sheetName, rows[0]?.[0]);
  if (!month) return { month: null, warnings: [] };
  const warnings: FinanceImportIssue[] = [];
  const kingdeeHeaderIndex = rows.findIndex((row) => canonicalSubject(row?.[0]) === "金蝶科目名称");
  if (kingdeeHeaderIndex < 4) {
    return {
      month: null,
      warnings: [{ sheet: sheetName, month, code: "MISSING_KINGDEE_HEADER", message: "未找到“金蝶科目名称”明细区" }],
    };
  }
  const dimensions = buildDimensions(rows);
  if (dimensions.length < 2) {
    return {
      month: null,
      warnings: [{ sheet: sheetName, month, code: "MISSING_SHOP_HEADER", message: "未识别到店铺列" }],
    };
  }
  const businessName = dimensions.find((item) => item.scopeType === "business")?.scopeName ?? "事业部汇总";
  const rawLines: FinanceLineInput[] = [];
  const origins: FinanceCellOrigin[] | null = captureOrigins ? [] : null;

  for (let rowIndex = 1; rowIndex < kingdeeHeaderIndex; rowIndex += 1) {
    const subjectName = canonicalSubject(rows[rowIndex]?.[0]);
    if (!subjectName) continue;
    const metricKey = summaryMetricKey(subjectName);
    for (const dimension of dimensions) {
      const rawValue = rows[rowIndex]?.[dimension.columnIndex];
      if (rawValue === null || rawValue === undefined || rawValue === "") continue;
      const valueType = valueTypeForSummary(subjectName, rawValue);
      const stored = toStoredValue(rawValue, valueType);
      rawLines.push({
        month,
        section: "summary",
        metricKey,
        subjectName,
        scopeKey: dimension.scopeKey,
        scopeType: dimension.scopeType,
        scopeName: dimension.scopeName,
        groupName: dimension.groupName,
        valueType,
        ...stored,
        sourceRowCount: 1,
        sortOrder: rowIndex + 1,
        isTotal: /合计/.test(subjectName),
      });
      origins?.push({ rowIndex, columnIndex: dimension.columnIndex });
    }
  }

  for (let rowIndex = kingdeeHeaderIndex + 1; rowIndex < rows.length; rowIndex += 1) {
    const subjectName = canonicalSubject(rows[rowIndex]?.[0]);
    if (!subjectName) continue;
    for (const dimension of dimensions) {
      const rawValue = rows[rowIndex]?.[dimension.columnIndex];
      const numeric = parseNumeric(rawValue);
      if (numeric === null) continue;
      rawLines.push({
        month,
        section: "kingdee",
        metricKey: subjectName === "销售费用" ? "selling_expense_total" : `subject:${subjectName}`,
        subjectName,
        scopeKey: dimension.scopeKey,
        scopeType: dimension.scopeType,
        scopeName: dimension.scopeName,
        groupName: dimension.groupName,
        valueType: "amount",
        ...toStoredValue(numeric, "amount"),
        sourceRowCount: 1,
        sortOrder: rowIndex + 1,
        isTotal: subjectName === "销售费用",
      });
      origins?.push({ rowIndex, columnIndex: dimension.columnIndex });
    }
  }

  const lines = aggregateLines(rawLines);
  const parsed: ParsedFinanceMonth = {
    month,
    sheetName,
    businessName,
    shopCount: new Set(dimensions.filter((item) => item.scopeType === "shop").map((item) => item.scopeName)).size,
    subjectCount: new Set(lines.filter((line) => line.section === "kingdee").map((line) => line.subjectName)).size,
    lines,
  };
  warnings.push(...reconciliationWarnings(parsed));
  // “销售费用” is the parent total. Once duplicate detail subjects have been
  // merged, calculate the parent from those children so it always reconciles.
  recalculateKingdeeTotals(lines);
  if (origins === null) return { month: parsed, warnings };
  const lastColumn = Math.max(...dimensions.map(item => item.columnIndex));
  const rawHeader = (value: CellValue): string | null => value === null || value === undefined ? null : String(value);
  const headerCells: FinanceHeaderCell[] = Array.from({ length: lastColumn }, (_, offset) => {
    const columnIndex = offset + 1;
    return { columnIndex, rawGroupCell: rawHeader(rows[1]?.[columnIndex]),
      rawShopCell: rawHeader(rows[2]?.[columnIndex]) };
  });
  return { month: parsed, warnings,
    evidenceInput: { month, sheetName, dimensions, rawLines, origins, headerCells } };
}

function parseWorkbook(input: ArrayBuffer | Uint8Array, captureOrigins: boolean): {
  parsed: ParsedFinanceWorkbook;
  evidenceInputs: FinanceColumnEvidenceInput[];
} {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  let workbook: XLSX.WorkBook;
  try {
    workbook = XLSX.read(bytes, {
      type: "array",
      cellDates: false,
      cellFormula: false,
      cellHTML: false,
      cellText: false,
      dense: false,
    });
  } catch (error) {
    throw new Error(`财报文件无法解析：${error instanceof Error ? error.message : "未知格式错误"}`);
  }

  const warnings: FinanceImportIssue[] = [];
  const months = new Map<string, ParsedFinanceMonth>();
  const evidenceInputs = new Map<string, FinanceColumnEvidenceInput>();
  for (const sheetName of workbook.SheetNames) {
    const sheet = workbook.Sheets[sheetName];
    if (!sheet) continue;
    const parsed = parseMonthSheet(sheetName, sheet, captureOrigins);
    warnings.push(...parsed.warnings);
    if (!parsed.month) continue;
    if (months.has(parsed.month.month)) {
      warnings.push({
        sheet: sheetName,
        month: parsed.month.month,
        code: "DUPLICATE_MONTH_SHEET",
        message: `工作簿内月份 ${parsed.month.month} 重复，已保留第一个有效工作表`,
      });
      continue;
    }
    months.set(parsed.month.month, parsed.month);
    if (captureOrigins) {
      if (!parsed.evidenceInput) throw new Error("财报聚合前来源格缺少完整原始坐标");
      evidenceInputs.set(parsed.month.month, parsed.evidenceInput);
    }
  }

  if (months.size === 0) {
    throw new Error("未识别到有效月度财报；工作表名称应类似“26.1”，且需要包含经营汇总和金蝶科目明细区");
  }
  const sorted = [...months.keys()].sort((left, right) => left.localeCompare(right));
  return { parsed: { months: sorted.map(key => months.get(key)!), warnings,
    sourceSheetCount: workbook.SheetNames.length },
    evidenceInputs: captureOrigins ? sorted.map(key => evidenceInputs.get(key)!) : [] };
}

export function parseFinanceWorkbook(input: ArrayBuffer | Uint8Array): ParsedFinanceWorkbook {
  return parseWorkbook(input, false).parsed;
}

/** Explicit opt-in raw coordinate material for the unregistered v2 candidate. */
export function parseFinanceWorkbookWithColumnEvidence(input: ArrayBuffer | Uint8Array) {
  return parseWorkbook(input, true);
}
