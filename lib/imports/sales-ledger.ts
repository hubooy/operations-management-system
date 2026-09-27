import {
  parseXlsxFirstSheet,
  type XlsxCellValue,
  type XlsxParseOptions,
  type XlsxRow,
} from "./xlsx";

export const MAX_SALES_LEDGER_ROWS = 500_000;

export const GEEKCLOUD_SALES_LEDGER_REQUIRED_HEADERS = Object.freeze([
  "网店订单号",
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
] as const);

/**
 * 可选表头：吉客云 v4 导出自 2026-08-27 起不再包含"费用分摊"列（用户确认今后也不再补充）。
 * 列缺失时费用分摊按 0 解析；毛利恒等式校验会退化为 分摊后金额 − 货品成本，仅产生警告。
 */
export const GEEKCLOUD_SALES_LEDGER_OPTIONAL_HEADERS = Object.freeze(["费用分摊"] as const);

/** 吉客云导出中"网店订单号"的已知别名；按优先级排列，第一个为规范名。 */
const ONLINE_ORDER_NO_ALIASES = ["网店订单号", "订单编号"] as const;

export type SalesBusinessType = "sale" | "return";

export interface SalesLedgerRow {
  orderNo: string;
  onlineOrderNo: string;
  salesTime: string;
  orderTime: string;
  shipTime: string | null;
  lineShipTime: string | null;
  channel: string;
  platform: string;
  shopName: string;
  logisticsCompany: string;
  logisticsNo: string;
  warehouse: string;
  customerCode: string;
  productCode: string;
  /** 店铺侧 SKU/规格编码；空值表示源表未补充。 */
  onlineSpecCode: string;
  productName: string;
  specification: string;
  barcode: string;
  quantity: number;
  listUnitPriceCents: number;
  supplier: string;
  /** 吉客云“货品成本”为该明细行的成本总额，而不是单位成本。 */
  costAmountCents: number;
  allocatedUnitPriceCents: number;
  allocatedAmountCents: number;
  feeAllocationCents: number;
  grossProfitCents: number;
  grossMarginBps: number | null;
  untaxedGrossProfitCents: number;
  untaxedGrossMarginBps: number | null;
  category: string;
  customerNote: string;
  sourceRowNumber: number;
  sourceLineKey: string;
  sourceRowHash: string;
  businessType: SalesBusinessType;
}

export type SalesLedgerRowErrorCode =
  | "MISSING_VALUE"
  | "INVALID_NUMBER"
  | "INVALID_DATE";

export interface SalesLedgerRowError {
  code: SalesLedgerRowErrorCode;
  message: string;
  sourceRowNumber: number;
  field: keyof SalesLedgerRow | string;
}

export type SalesLedgerWarningCode =
  | "ALLOCATED_AMOUNT_MISMATCH"
  | "GROSS_PROFIT_MISMATCH";

export interface SalesLedgerWarning {
  code: SalesLedgerWarningCode;
  message: string;
  sourceRowNumber: number;
  expectedCents: number;
  actualCents: number;
  differenceCents: number;
}

export interface SalesLedgerTotals {
  sourceRowCount: number;
  rowCount: number;
  invalidRowCount: number;
  saleRowCount: number;
  returnRowCount: number;
  quantity: number;
  netSalesCents: number;
  costAmountCents: number;
  feeAllocationCents: number;
  grossProfitCents: number;
  untaxedGrossProfitCents: number;
}

export interface SalesLedgerParseResult {
  sheetName: string;
  headers: string[];
  rows: SalesLedgerRow[];
  errors: SalesLedgerRowError[];
  warnings: SalesLedgerWarning[];
  totals: SalesLedgerTotals;
}

export interface SalesLedgerParseOptions {
  /** May lower, but never raise, the hard 10,000-row import limit. */
  maxDataRows?: number;
  /** Formula discrepancies at or below this many cents are treated as rounding. */
  formulaToleranceCents?: number;
  xlsx?: XlsxParseOptions;
}

export type SalesLedgerParseErrorCode =
  | "INVALID_OPTIONS"
  | "HEADER_NOT_FOUND"
  | "MISSING_REQUIRED_HEADERS"
  | "DUPLICATE_HEADERS"
  | "ROW_LIMIT";

export class SalesLedgerParseError extends Error {
  readonly code: SalesLedgerParseErrorCode;
  readonly missingHeaders: string[];

  constructor(code: SalesLedgerParseErrorCode, message: string, missingHeaders: string[] = []) {
    super(message);
    this.name = "SalesLedgerParseError";
    this.code = code;
    this.missingHeaders = missingHeaders;
  }
}

interface HeaderLocation {
  rowIndex: number;
  headers: string[];
  indexByHeader: Map<string, number>;
}

interface RowReader {
  text(header: string): string;
  raw(header: string): XlsxCellValue;
}

type ParsedRowDraft = Omit<SalesLedgerRow, "sourceLineKey" | "sourceRowHash">;

const HEADER_SEARCH_ROWS = 20;
const textEncoder = new TextEncoder();

/** Normalize the first worksheet of a 吉客云“销售单明细账” export. */
export function parseSalesLedgerXlsx(
  input: ArrayBuffer | Uint8Array,
  options: SalesLedgerParseOptions = {},
): SalesLedgerParseResult {
  const maxDataRows = parseMaxRows(options.maxDataRows);
  const formulaToleranceCents = parseTolerance(options.formulaToleranceCents);
  const workbook = parseXlsxFirstSheet(input, options.xlsx);
  const header = locateHeader(workbook.rows);
  const candidateRows = workbook.rows
    .slice(header.rowIndex + 1)
    .filter((row) => row.cells.some((cell) => !isBlank(cell)));

  if (candidateRows.length > maxDataRows) {
    throw new SalesLedgerParseError(
      "ROW_LIMIT",
      `销售明细共有 ${candidateRows.length} 行，超过 ${maxDataRows} 行限制`,
    );
  }

  const drafts: ParsedRowDraft[] = [];
  const errors: SalesLedgerRowError[] = [];
  const warnings: SalesLedgerWarning[] = [];

  for (const sourceRow of candidateRows) {
    const reader = createRowReader(sourceRow, header.indexByHeader);
    const parsed = parseSalesRow(reader, sourceRow.rowNumber, workbook.date1904, errors);
    if (!parsed) continue;

    const expectedAllocatedAmountCents = Math.round(parsed.allocatedUnitPriceCents * parsed.quantity);
    addFormulaWarning(
      warnings,
      "ALLOCATED_AMOUNT_MISMATCH",
      "分摊后金额与分摊后单价 × 数量不一致",
      sourceRow.rowNumber,
      expectedAllocatedAmountCents,
      parsed.allocatedAmountCents,
      formulaToleranceCents,
    );
    const expectedGrossProfitCents =
      parsed.allocatedAmountCents - parsed.costAmountCents - parsed.feeAllocationCents;
    addFormulaWarning(
      warnings,
      "GROSS_PROFIT_MISMATCH",
      "毛利与分摊后金额 − 货品成本 − 费用分摊不一致",
      sourceRow.rowNumber,
      expectedGrossProfitCents,
      parsed.grossProfitCents,
      formulaToleranceCents,
    );
    drafts.push(parsed);
  }

  const occurrenceByIdentity = new Map<string, number>();
  const rows = drafts.map((draft): SalesLedgerRow => {
    const identity = stableJson([
      draft.orderNo,
      draft.onlineOrderNo,
      draft.productCode,
      draft.specification,
      draft.barcode,
      draft.logisticsNo,
      draft.warehouse,
      draft.orderTime,
      draft.quantity,
      draft.listUnitPriceCents,
    ]);
    const occurrence = (occurrenceByIdentity.get(identity) ?? 0) + 1;
    occurrenceByIdentity.set(identity, occurrence);
    const sourceLineKey = `gky:${sha256Hex(identity).slice(0, 32)}:${occurrence}`;
    const sourceRowHash = sha256Hex(
      stableJson([
        draft.orderNo,
        draft.onlineOrderNo,
        draft.salesTime,
        draft.orderTime,
        draft.shipTime,
        draft.lineShipTime,
        draft.channel,
        draft.platform,
        draft.shopName,
        draft.logisticsCompany,
        draft.logisticsNo,
        draft.warehouse,
        draft.customerCode,
        draft.productCode,
        draft.onlineSpecCode,
        draft.productName,
        draft.specification,
        draft.barcode,
        draft.quantity,
        draft.listUnitPriceCents,
        draft.supplier,
        draft.costAmountCents,
        draft.allocatedUnitPriceCents,
        draft.allocatedAmountCents,
        draft.feeAllocationCents,
        draft.grossProfitCents,
        draft.grossMarginBps,
        draft.untaxedGrossProfitCents,
        draft.untaxedGrossMarginBps,
        draft.category,
        draft.customerNote,
        draft.businessType,
      ]),
    );
    return { ...draft, sourceLineKey, sourceRowHash };
  });

  return {
    sheetName: workbook.sheetName,
    headers: header.headers,
    rows,
    errors,
    warnings,
    totals: calculateTotals(candidateRows.length, rows, errors),
  };
}

function parseMaxRows(value: number | undefined): number {
  if (value === undefined) return MAX_SALES_LEDGER_ROWS;
  if (!Number.isSafeInteger(value) || value <= 0 || value > MAX_SALES_LEDGER_ROWS) {
    throw new SalesLedgerParseError(
      "INVALID_OPTIONS",
      `maxDataRows 必须是 1 到 ${MAX_SALES_LEDGER_ROWS} 之间的整数`,
    );
  }
  return value;
}

function parseTolerance(value: number | undefined): number {
  if (value === undefined) return 1;
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new SalesLedgerParseError("INVALID_OPTIONS", "formulaToleranceCents 必须是非负整数");
  }
  return value;
}

function locateHeader(rows: XlsxRow[]): HeaderLocation {
  let bestHeaders: string[] = [];
  let bestMatchedCount = -1;
  const required = new Set<string>(GEEKCLOUD_SALES_LEDGER_REQUIRED_HEADERS);
  /** 将别名映射为规范名，用于匹配和索引。 */
  const aliasToCanonical = new Map<string, string>();
  for (const aliases of [ONLINE_ORDER_NO_ALIASES]) {
    for (let i = 1; i < aliases.length; i += 1) {
      aliasToCanonical.set(aliases[i], aliases[0]);
    }
  }

  for (let rowIndex = 0; rowIndex < Math.min(rows.length, HEADER_SEARCH_ROWS); rowIndex += 1) {
    const sourceHeaders = rows[rowIndex].cells.map(normalizeHeader);
    const hasCanonicalOnlineOrderNo = sourceHeaders.includes(ONLINE_ORDER_NO_ALIASES[0]);
    // 2025 年 ERP 同时导出"货品分类"和更细的"货品细分"时，以货品细分
    // 作为销售分析统一使用的货品分类，避免同名字段发生歧义。
    const hasProductSubdivision = sourceHeaders.includes("货品细分");
    const headers = sourceHeaders.map((header) => {
      if (header === "货品细分") return "货品分类";
      if (hasProductSubdivision && header === "货品分类") return "";
      // 别名归一化："订单编号" → "网店订单号"
      const canonical = aliasToCanonical.get(header);
      if (canonical && !(canonical === ONLINE_ORDER_NO_ALIASES[0] && hasCanonicalOnlineOrderNo)) return canonical;
      return header;
    });
    const matchedCount = new Set(headers.filter((header) => required.has(header))).size;
    if (matchedCount > bestMatchedCount) {
      bestMatchedCount = matchedCount;
      bestHeaders = headers;
    }
    if (matchedCount !== required.size) continue;

    const indexByHeader = new Map<string, number>();
    const duplicates: string[] = [];
    headers.forEach((header, index) => {
      if (!header) return;
      if (indexByHeader.has(header)) duplicates.push(header);
      else indexByHeader.set(header, index);
    });
    if (duplicates.length > 0) {
      throw new SalesLedgerParseError(
        "DUPLICATE_HEADERS",
        `销售明细表头重复: ${[...new Set(duplicates)].join("、")}`,
      );
    }
    return { rowIndex, headers, indexByHeader };
  }

  if (bestMatchedCount <= 0) {
    throw new SalesLedgerParseError("HEADER_NOT_FOUND", "未找到吉客云销售单明细账中文表头");
  }
  const present = new Set(bestHeaders);
  const missing = GEEKCLOUD_SALES_LEDGER_REQUIRED_HEADERS.filter((header) => !present.has(header));
  throw new SalesLedgerParseError(
    "MISSING_REQUIRED_HEADERS",
    `销售明细缺少必要表头: ${missing.join("、")}`,
    [...missing],
  );
}

function normalizeHeader(value: XlsxCellValue): string {
  return cellToText(value)
    .replace(/^\uFEFF/, "")
    .trim()
    .replace(/[（]/g, "(")
    .replace(/[）]/g, ")")
    .replace(/\s+/g, "");
}

function createRowReader(row: XlsxRow, indexByHeader: Map<string, number>): RowReader {
  return {
    raw(header) {
      const index = indexByHeader.get(header);
      return index === undefined ? null : (row.cells[index] ?? null);
    },
    text(header) {
      const index = indexByHeader.get(header);
      return index === undefined ? "" : cellToText(row.cells[index] ?? null).trim();
    },
  };
}

function parseSalesRow(
  reader: RowReader,
  sourceRowNumber: number,
  date1904: boolean,
  errors: SalesLedgerRowError[],
): ParsedRowDraft | null {
  const beforeErrors = errors.length;
  const onlineOrderNo = reader.text("网店订单号");
  const orderNo = reader.text("订单编号") || onlineOrderNo || `AUTO-${sourceRowNumber}`;
  const channel = requiredText(reader, "销售渠道", "channel", sourceRowNumber, errors);
  const productName = reader.text("货品名称");
  const sourceProductCode = reader.text("货品编号");
  // 吉客云会把“补差价专用”作为订单金额调整行导出，且不提供货品编号。
  // Keep the revenue/profit in sales analysis while assigning a stable virtual SKU
  // so it neither fails validation nor merges into an actual product.
  const isPriceAdjustment = !sourceProductCode && productName === "补差价专用";
  const productCode = isPriceAdjustment
    ? "ERP_PRICE_ADJUSTMENT"
    : requiredText(reader, "货品编号", "productCode", sourceRowNumber, errors);
  const quantity = requiredNumber(reader.raw("数量"), "quantity", "数量", sourceRowNumber, errors);
  const listUnitPriceCents = requiredMoney(
    reader.raw("单价"),
    "listUnitPriceCents",
    "单价",
    sourceRowNumber,
    errors,
  );
  const sourceCost = reader.raw("货品成本");
  // ERP 的“补差价专用”虚拟行没有实际货品成本，并会导出为 #N/A；
  // 该行仅调整订单金额，按零成本写入才能保留销售额且不阻塞整表导入。
  const costAmountCents = isPriceAdjustment && sourceCost === "#N/A"
    ? 0
    : requiredMoney(sourceCost, "costAmountCents", "货品成本", sourceRowNumber, errors);
  const allocatedUnitPriceCents = requiredMoney(
    reader.raw("分摊后单价"),
    "allocatedUnitPriceCents",
    "分摊后单价",
    sourceRowNumber,
    errors,
  );
  const allocatedAmountCents = requiredMoney(
    reader.raw("分摊后金额"),
    "allocatedAmountCents",
    "分摊后金额",
    sourceRowNumber,
    errors,
  );
  const feeAllocationCents = optionalMoney(
    reader.raw("费用分摊"),
    "feeAllocationCents",
    "费用分摊",
    sourceRowNumber,
    errors,
  );
  const grossProfitCents = requiredMoney(
    reader.raw("毛利"),
    "grossProfitCents",
    "毛利",
    sourceRowNumber,
    errors,
  );
  const untaxedGrossProfitCents = optionalMoney(
    reader.raw("未税毛利"),
    "untaxedGrossProfitCents",
    "未税毛利",
    sourceRowNumber,
    errors,
  );
  const orderTime = requiredDate(
    reader.raw("下单时间"),
    "orderTime",
    "下单时间",
    sourceRowNumber,
    date1904,
    errors,
  );
  const shipTime = optionalDate(
    reader.raw("发货时间"),
    "shipTime",
    "发货时间",
    sourceRowNumber,
    date1904,
    errors,
  );
  const lineShipTime = optionalDate(
    reader.raw("货品级发货时间"),
    "lineShipTime",
    "货品级发货时间",
    sourceRowNumber,
    date1904,
    errors,
  );
  const grossMarginBps = optionalRateBps(
    reader.raw("毛利率"),
    "grossMarginBps",
    "毛利率",
    sourceRowNumber,
    errors,
  );
  const untaxedGrossMarginBps = optionalRateBps(
    reader.raw("未税毛利率(%)"),
    "untaxedGrossMarginBps",
    "未税毛利率(%)",
    sourceRowNumber,
    errors,
  );

  if (errors.length !== beforeErrors) return null;
  const platform = normalizePlatform(channel);
  return {
    orderNo,
    onlineOrderNo,
    // All operating views use the ERP shipment date as their single date
    // dimension. Keep salesTime aligned for downstream consumers that still
    // read this legacy field.
    salesTime: shipTime ?? orderTime,
    orderTime,
    shipTime,
    lineShipTime,
    channel,
    platform,
    shopName: normalizeShopName(channel),
    logisticsCompany: reader.text("物流公司"),
    logisticsNo: reader.text("物流单号"),
    warehouse: reader.text("发货仓库"),
    customerCode: reader.text("客户编号"),
    productCode,
    onlineSpecCode: reader.text("网店规格编码"),
    productName,
    specification: reader.text("规格"),
    barcode: reader.text("货品条码"),
    quantity,
    listUnitPriceCents,
    supplier: reader.text("货品默认供应商"),
    costAmountCents,
    allocatedUnitPriceCents,
    allocatedAmountCents,
    feeAllocationCents,
    grossProfitCents,
    grossMarginBps,
    untaxedGrossProfitCents,
    untaxedGrossMarginBps,
    category: reader.text("货品分类") || (isPriceAdjustment ? "价格补差" : ""),
    customerNote: reader.text("客服备注"),
    sourceRowNumber,
    businessType: quantity < 0 || allocatedAmountCents < 0 ? "return" : "sale",
  };
}

function requiredText(
  reader: RowReader,
  header: string,
  field: string,
  rowNumber: number,
  errors: SalesLedgerRowError[],
): string {
  const value = reader.text(header);
  if (!value) errors.push({ code: "MISSING_VALUE", message: `${header}不能为空`, sourceRowNumber: rowNumber, field });
  return value;
}

function requiredNumber(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: SalesLedgerRowError[],
): number {
  if (isBlank(value)) {
    errors.push({ code: "MISSING_VALUE", message: `${label}不能为空`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  const parsed = parseDecimal(value);
  if (parsed === null || parsed === 0) {
    errors.push({ code: "INVALID_NUMBER", message: `${label}必须是非零有效数字`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  return parsed;
}

function requiredMoney(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: SalesLedgerRowError[],
): number {
  if (isBlank(value)) {
    errors.push({ code: "MISSING_VALUE", message: `${label}不能为空`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  const cents = moneyToCents(value);
  if (cents === null) {
    errors.push({ code: "INVALID_NUMBER", message: `${label}不是有效金额`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  return cents;
}

function optionalMoney(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: SalesLedgerRowError[],
): number {
  if (isBlank(value)) return 0;
  const cents = moneyToCents(value);
  if (cents === null) {
    errors.push({ code: "INVALID_NUMBER", message: `${label}不是有效金额`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  return cents;
}

function moneyToCents(value: XlsxCellValue): number | null {
  const amount = parseDecimal(value);
  if (amount === null) return null;
  const cents = Math.round((amount + Math.sign(amount) * Number.EPSILON) * 100);
  return Number.isSafeInteger(cents) ? cents : null;
}

function parseDecimal(value: XlsxCellValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/[,，\s￥¥]/g, "");
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function requiredDate(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  date1904: boolean,
  errors: SalesLedgerRowError[],
): string {
  if (isBlank(value)) {
    errors.push({ code: "MISSING_VALUE", message: `${label}不能为空`, sourceRowNumber: rowNumber, field });
    return "";
  }
  const parsed = normalizeDate(value, date1904);
  if (!parsed) {
    errors.push({ code: "INVALID_DATE", message: `${label}不是有效日期`, sourceRowNumber: rowNumber, field });
    return "";
  }
  return parsed;
}

function optionalDate(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  date1904: boolean,
  errors: SalesLedgerRowError[],
): string | null {
  if (isBlank(value)) return null;
  const parsed = normalizeDate(value, date1904);
  if (!parsed) errors.push({ code: "INVALID_DATE", message: `${label}不是有效日期`, sourceRowNumber: rowNumber, field });
  return parsed;
}

/** Convert supported Excel/text date values to timezone-neutral YYYY-MM-DD HH:mm:ss. */
export function normalizeSalesLedgerDate(value: XlsxCellValue, date1904 = false): string | null {
  return normalizeDate(value, date1904);
}

function normalizeDate(value: XlsxCellValue, date1904: boolean): string | null {
  if (typeof value === "number") return excelSerialToDate(value, date1904);
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text) return null;
  const match = /^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:[ T](\d{1,2})(?::(\d{1,2}))?(?::(\d{1,2})(?:\.\d+)?)?)?$/.exec(text);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4] ?? 0);
  const minute = Number(match[5] ?? 0);
  const second = Number(match[6] ?? 0);
  if (!validDateParts(year, month, day, hour, minute, second)) return null;
  return formatDateParts(year, month, day, hour, minute, second);
}

function excelSerialToDate(serial: number, date1904: boolean): string | null {
  if (!Number.isFinite(serial) || serial < 0) return null;
  const wholeDays = Math.floor(serial);
  const adjustedDays = date1904 ? wholeDays : wholeDays - (wholeDays >= 60 ? 1 : 0);
  const epoch = date1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, 31);
  const seconds = Math.round((serial - wholeDays) * 86_400);
  const date = new Date(epoch + adjustedDays * 86_400_000 + seconds * 1_000);
  if (!Number.isFinite(date.getTime())) return null;
  return formatDateParts(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate(),
    date.getUTCHours(),
    date.getUTCMinutes(),
    date.getUTCSeconds(),
  );
}

function validDateParts(year: number, month: number, day: number, hour: number, minute: number, second: number): boolean {
  if (year < 1 || year > 9999 || month < 1 || month > 12 || hour < 0 || hour > 23 || minute < 0 || minute > 59 || second < 0 || second > 59) return false;
  return day >= 1 && day <= new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function formatDateParts(year: number, month: number, day: number, hour: number, minute: number, second: number): string {
  return `${pad(year, 4)}-${pad(month)}-${pad(day)} ${pad(hour)}:${pad(minute)}:${pad(second)}`;
}

function pad(value: number, width = 2): string {
  return String(value).padStart(width, "0");
}

function optionalRateBps(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: SalesLedgerRowError[],
): number | null {
  if (isBlank(value)) return null;
  let basisPoints: number;
  if (typeof value === "number") basisPoints = Math.round(Math.abs(value) <= 1 ? value * 10_000 : value * 100);
  else if (typeof value === "string") {
    const text = value.trim();
    const hasPercent = text.endsWith("%");
    const numeric = parseDecimal(hasPercent ? text.slice(0, -1) : text);
    if (numeric === null) {
      errors.push({ code: "INVALID_NUMBER", message: `${label}不是有效百分比`, sourceRowNumber: rowNumber, field });
      return null;
    }
    basisPoints = Math.round(numeric * 100);
  } else return null;
  if (!Number.isSafeInteger(basisPoints)) {
    errors.push({ code: "INVALID_NUMBER", message: `${label}不是有效百分比`, sourceRowNumber: rowNumber, field });
    return null;
  }
  return basisPoints;
}

export function normalizeSalesPlatform(channel: string): string {
  return normalizePlatform(channel);
}

function normalizePlatform(channel: string): string {
  const value = channel.trim().toLowerCase();
  if (value.includes("京东")) return "京东";
  if (value.includes("天猫")) return "天猫";
  if (value.includes("拼多多") || value.includes("多多")) return "拼多多";
  if (value.includes("抖店") || value.includes("抖音")) return "抖音";
  if (value.includes("淘宝")) return "淘宝";
  if (value.includes("快手")) return "快手";
  if (value.includes("小红书")) return "小红书";
  if (value.includes("唯品会")) return "唯品会";
  if (value.includes("苏宁")) return "苏宁";
  if (value.includes("1688") || value.includes("阿里巴巴")) return "1688";
  if (value.includes("有赞")) return "有赞";
  if (value.includes("微信") || value.includes("微店") || value.includes("视频号")) return "微信";
  if (value.includes("美团")) return "美团";
  if (value.includes("饿了么")) return "饿了么";
  if (value.includes("线下") || value.includes("门店") || value.includes("经销")) return "线下";
  return "其他";
}

function normalizeShopName(channel: string): string {
  const parts = channel.split(/[-—–:：]/, 2);
  return (parts.length > 1 ? parts[1] : channel).trim();
}

function addFormulaWarning(
  warnings: SalesLedgerWarning[],
  code: SalesLedgerWarningCode,
  message: string,
  sourceRowNumber: number,
  expectedCents: number,
  actualCents: number,
  toleranceCents: number,
): void {
  const differenceCents = actualCents - expectedCents;
  if (Math.abs(differenceCents) <= toleranceCents) return;
  warnings.push({ code, message, sourceRowNumber, expectedCents, actualCents, differenceCents });
}

function calculateTotals(
  sourceRowCount: number,
  rows: SalesLedgerRow[],
  errors: SalesLedgerRowError[],
): SalesLedgerTotals {
  const invalidRows = new Set(errors.map((error) => error.sourceRowNumber));
  const totals: SalesLedgerTotals = {
    sourceRowCount,
    rowCount: rows.length,
    invalidRowCount: invalidRows.size,
    saleRowCount: 0,
    returnRowCount: 0,
    quantity: 0,
    netSalesCents: 0,
    costAmountCents: 0,
    feeAllocationCents: 0,
    grossProfitCents: 0,
    untaxedGrossProfitCents: 0,
  };
  for (const row of rows) {
    if (row.businessType === "return") totals.returnRowCount += 1;
    else totals.saleRowCount += 1;
    totals.quantity += row.quantity;
    totals.netSalesCents = safeAdd(totals.netSalesCents, row.allocatedAmountCents);
    totals.costAmountCents = safeAdd(totals.costAmountCents, row.costAmountCents);
    totals.feeAllocationCents = safeAdd(totals.feeAllocationCents, row.feeAllocationCents);
    totals.grossProfitCents = safeAdd(totals.grossProfitCents, row.grossProfitCents);
    totals.untaxedGrossProfitCents = safeAdd(totals.untaxedGrossProfitCents, row.untaxedGrossProfitCents);
  }
  return totals;
}

function safeAdd(left: number, right: number): number {
  const result = left + right;
  if (!Number.isSafeInteger(result)) throw new SalesLedgerParseError("INVALID_OPTIONS", "销售金额汇总超出安全整数范围");
  return result;
}

function isBlank(value: XlsxCellValue | undefined): boolean {
  return value === null || value === undefined || (typeof value === "string" && value.trim() === "");
}

function cellToText(value: XlsxCellValue): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return String(value);
}

function stableJson(value: readonly unknown[]): string {
  return JSON.stringify(value);
}

// Synchronous, dependency-free SHA-256 keeps the parser Worker-compatible and
// lets source keys be produced in the same pass as normalization.
function sha256Hex(value: string): string {
  const bytes = textEncoder.encode(value);
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
  const message = new Uint8Array(paddedLength);
  message.set(bytes);
  message[bytes.length] = 0x80;
  const view = new DataView(message.buffer);
  const bitLength = bytes.length * 8;
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x1_0000_0000), false);
  view.setUint32(paddedLength - 4, bitLength >>> 0, false);

  const hash = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const schedule = new Uint32Array(64);

  for (let offset = 0; offset < message.length; offset += 64) {
    for (let index = 0; index < 16; index += 1) schedule[index] = view.getUint32(offset + index * 4, false);
    for (let index = 16; index < 64; index += 1) {
      const x = schedule[index - 15];
      const y = schedule[index - 2];
      const sigma0 = rotateRight(x, 7) ^ rotateRight(x, 18) ^ (x >>> 3);
      const sigma1 = rotateRight(y, 17) ^ rotateRight(y, 19) ^ (y >>> 10);
      schedule[index] = (schedule[index - 16] + sigma0 + schedule[index - 7] + sigma1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const sigma1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choose = (e & f) ^ (~e & g);
      const temp1 = (h + sigma1 + choose + SHA256_CONSTANTS[index] + schedule[index]) >>> 0;
      const sigma0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (sigma0 + majority) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    hash[0] = (hash[0] + a) >>> 0; hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0; hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0; hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0; hash[7] = (hash[7] + h) >>> 0;
  }
  return [...hash].map((part) => part.toString(16).padStart(8, "0")).join("");
}

function rotateRight(value: number, amount: number): number {
  return (value >>> amount) | (value << (32 - amount));
}

const SHA256_CONSTANTS = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);
