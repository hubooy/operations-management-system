import {
  parseXlsxFirstSheet,
  type XlsxCellValue,
  type XlsxParseOptions,
  type XlsxRow,
} from "./xlsx";
import { normalizeSalesLedgerDate } from "./sales-ledger";
import {
  classifyInventoryWarehouse,
  type InventoryWarehouseMapping,
  type InventoryWarehouseCategory,
} from "@/lib/inventory/warehouse-classification";

export const MAX_INVENTORY_STOCK_ROWS = 100_000;

export type InventoryWarehouseType = "owned" | "jd_rdc" | "other";

export type InventoryStockRow = {
  sourceRowNumber: number;
  rowKey: string;
  snapshotDate: string | null;
  warehouse: string;
  warehouseType: InventoryWarehouseType;
  warehouseCategory: InventoryWarehouseCategory;
  includeInInventory: boolean;
  productCode: string;
  productName: string;
  brand: string;
  supplier: string;
  specification: string;
  barcode: string;
  category: string;
  onHandQuantity: number;
  availableQuantity: number;
  lockedQuantity: number;
  inTransitQuantity: number;
  unitCostCents: number;
  inventoryAgeDays: number | null;
  sales7dQuantity: number;
  sales30dQuantity: number;
};

export type InventoryStockIssue = {
  code: "MISSING_VALUE" | "INVALID_NUMBER" | "INVALID_DATE";
  message: string;
  sourceRowNumber: number;
  field: string;
};

export type InventoryStockParseResult = {
  sheetName: string;
  headers: string[];
  rows: InventoryStockRow[];
  errors: InventoryStockIssue[];
  coverage: {
    hasOnHandQuantity: boolean;
    hasAvailableQuantity: boolean;
    hasLockedQuantity: boolean;
    hasInTransitQuantity: boolean;
    hasUnitCost: boolean;
    hasBrand: boolean;
    hasSupplier: boolean;
    hasInventoryAgeDays: boolean;
    hasSales7dQuantity: boolean;
    hasSales30dQuantity: boolean;
    hasSnapshotDate: boolean;
  };
  totals: {
    sourceRowCount: number;
    rowCount: number;
    warehouseCount: number;
    productCount: number;
    includedInventoryRowCount: number;
    excludedInventoryRowCount: number;
    onHandQuantity: number;
    availableQuantity: number;
    lockedQuantity: number;
    inTransitQuantity: number;
    stockValueCents: number;
    sales7dQuantity: number;
    sales30dQuantity: number;
  };
};

export type InventoryStockParseOptions = {
  maxDataRows?: number;
  xlsx?: XlsxParseOptions;
  warehouseMapping?: InventoryWarehouseMapping;
};

export type InventoryStockParseErrorCode =
  | "INVALID_OPTIONS"
  | "HEADER_NOT_FOUND"
  | "MISSING_REQUIRED_HEADERS"
  | "ROW_LIMIT";

export class InventoryStockParseError extends Error {
  readonly code: InventoryStockParseErrorCode;

  constructor(code: InventoryStockParseErrorCode, message: string) {
    super(message);
    this.name = "InventoryStockParseError";
    this.code = code;
  }
}

type CanonicalHeader =
  | "warehouse"
  | "productCode"
  | "productName"
  | "brand"
  | "supplier"
  | "specification"
  | "barcode"
  | "category"
  | "onHandQuantity"
  | "availableQuantity"
  | "lockedQuantity"
  | "inTransitQuantity"
  | "unitCost"
  | "inventoryAgeDays"
  | "sales7dQuantity"
  | "sales30dQuantity"
  | "snapshotDate";

type HeaderLocation = {
  rowIndex: number;
  headers: string[];
  indexByCanonical: Map<CanonicalHeader, number>;
};

const HEADER_SEARCH_ROWS = 30;
const headerAliases: Record<CanonicalHeader, readonly string[]> = {
  warehouse: ["仓库名称", "仓库", "分仓", "仓库名"],
  productCode: ["货品编号", "商品编码", "商品编号", "SKU编码", "SKU编号", "商家编码", "货号"],
  productName: ["货品名称", "商品名称", "商品", "品名"],
  brand: ["品牌", "品牌名称", "商品品牌", "货品品牌"],
  supplier: ["规格默认供应商", "货品默认供应商", "默认供应商", "供应商名称", "供应商"],
  specification: ["规格", "规格名称", "货品规格", "商品规格"],
  barcode: ["条码", "商品条码", "货品条码", "国际条码"],
  category: ["货品分类", "商品分类", "分类", "品类", "货品细分"],
  onHandQuantity: ["库存数量", "实际库存", "现有库存", "账面库存", "库存数", "总库存", "库存总量", "实盘数量"],
  availableQuantity: ["可用库存", "可售库存", "可配库存", "可配货库存", "可用数量", "可用数", "可销库存", "实际可用库存"],
  lockedQuantity: ["锁定数量", "锁定库存", "占用库存", "待出库数", "待发货数", "冻结库存"],
  inTransitQuantity: ["采购在途数", "在途数量", "在途库存", "采购在途", "调拨在途", "在途数"],
  unitCost: ["成本价", "单位成本", "库存成本价", "采购价", "含税成本价", "固定成本价"],
  inventoryAgeDays: ["吉客云库龄", "库龄(天)", "库龄天数", "库龄"],
  sales7dQuantity: ["前7天销量", "近7天销量", "7天销量"],
  sales30dQuantity: ["前30天销量", "近30天销量", "30天销量"],
  snapshotDate: ["库存日期", "快照日期", "统计日期", "数据日期", "日期"],
};

const aliasToCanonical = new Map<string, CanonicalHeader>();
for (const [canonical, aliases] of Object.entries(headerAliases) as [CanonicalHeader, readonly string[]][]) {
  for (const alias of aliases) aliasToCanonical.set(normalizeHeader(alias), canonical);
}

export function parseInventoryStockXlsx(
  input: ArrayBuffer | Uint8Array,
  options: InventoryStockParseOptions = {},
): InventoryStockParseResult {
  const maxDataRows = options.maxDataRows ?? MAX_INVENTORY_STOCK_ROWS;
  if (!Number.isSafeInteger(maxDataRows) || maxDataRows <= 0 || maxDataRows > MAX_INVENTORY_STOCK_ROWS) {
    throw new InventoryStockParseError(
      "INVALID_OPTIONS",
      `maxDataRows 必须是 1 到 ${MAX_INVENTORY_STOCK_ROWS} 之间的整数`,
    );
  }

  const workbook = parseXlsxFirstSheet(input, {
    maxRows: maxDataRows + HEADER_SEARCH_ROWS + 1,
    ...options.xlsx,
  });
  const header = locateHeader(workbook.rows);
  const candidateRows = workbook.rows
    .slice(header.rowIndex + 1)
    .filter((row) => row.cells.some((cell) => !isBlank(cell)));

  if (candidateRows.length > maxDataRows) {
    throw new InventoryStockParseError(
      "ROW_LIMIT",
      `库存明细共有 ${candidateRows.length} 行，超过 ${maxDataRows} 行限制`,
    );
  }

  const rows: InventoryStockRow[] = [];
  const errors: InventoryStockIssue[] = [];
  for (const row of candidateRows) {
    const parsed = parseRow(row, header.indexByCanonical, workbook.date1904, errors, options.warehouseMapping);
    if (parsed) rows.push(parsed);
    if (errors.length >= 200) break;
  }

  const warehouses = new Set(rows.map((row) => row.warehouse));
  const products = new Set(rows.map((row) => row.productCode));
  const totals = rows.reduce(
    (result, row) => {
      result.onHandQuantity += row.onHandQuantity;
      result.availableQuantity += row.availableQuantity;
      result.lockedQuantity += row.lockedQuantity;
      result.inTransitQuantity += row.inTransitQuantity;
      result.stockValueCents += Math.max(0, row.availableQuantity) * row.unitCostCents;
      result.sales7dQuantity += row.sales7dQuantity;
      result.sales30dQuantity += row.sales30dQuantity;
      if (row.includeInInventory) result.includedInventoryRowCount += 1;
      else result.excludedInventoryRowCount += 1;
      return result;
    },
    {
      sourceRowCount: candidateRows.length,
      rowCount: rows.length,
      warehouseCount: warehouses.size,
      productCount: products.size,
      includedInventoryRowCount: 0,
      excludedInventoryRowCount: 0,
      onHandQuantity: 0,
      availableQuantity: 0,
      lockedQuantity: 0,
      inTransitQuantity: 0,
      stockValueCents: 0,
      sales7dQuantity: 0,
      sales30dQuantity: 0,
    },
  );

  const coverage = {
    hasOnHandQuantity: header.indexByCanonical.has("onHandQuantity"),
    hasAvailableQuantity: header.indexByCanonical.has("availableQuantity"),
    hasLockedQuantity: header.indexByCanonical.has("lockedQuantity"),
    hasInTransitQuantity: header.indexByCanonical.has("inTransitQuantity"),
    hasUnitCost: header.indexByCanonical.has("unitCost"),
    hasBrand: header.indexByCanonical.has("brand"),
    hasSupplier: header.indexByCanonical.has("supplier"),
    hasInventoryAgeDays: header.indexByCanonical.has("inventoryAgeDays"),
    hasSales7dQuantity: header.indexByCanonical.has("sales7dQuantity"),
    hasSales30dQuantity: header.indexByCanonical.has("sales30dQuantity"),
    hasSnapshotDate: header.indexByCanonical.has("snapshotDate"),
  };

  return { sheetName: workbook.sheetName, headers: header.headers, rows, errors, coverage, totals };
}

function locateHeader(rows: XlsxRow[]): HeaderLocation {
  let best: { headers: string[]; matched: Set<CanonicalHeader> } | null = null;

  for (let rowIndex = 0; rowIndex < Math.min(rows.length, HEADER_SEARCH_ROWS); rowIndex += 1) {
    const headers = rows[rowIndex].cells.map(normalizeHeader);
    const indexByCanonical = new Map<CanonicalHeader, number>();
    headers.forEach((header, index) => {
      const canonical = aliasToCanonical.get(header);
      if (canonical && !indexByCanonical.has(canonical)) indexByCanonical.set(canonical, index);
    });

    const matched = new Set(indexByCanonical.keys());
    if (!best || matched.size > best.matched.size) best = { headers, matched };
    const hasQuantity = matched.has("availableQuantity") || matched.has("onHandQuantity");
    if (matched.has("warehouse") && matched.has("productCode") && hasQuantity) {
      return { rowIndex, headers, indexByCanonical };
    }
  }

  if (!best || best.matched.size === 0) {
    throw new InventoryStockParseError("HEADER_NOT_FOUND", "未找到库存明细中文表头");
  }
  const missing = [
    ...(best.matched.has("warehouse") ? [] : ["仓库名称"]),
    ...(best.matched.has("productCode") ? [] : ["货品编号"]),
    ...(best.matched.has("availableQuantity") || best.matched.has("onHandQuantity") ? [] : ["可用库存或库存数量"]),
  ];
  throw new InventoryStockParseError(
    "MISSING_REQUIRED_HEADERS",
    `库存明细缺少必要表头: ${missing.join("、")}`,
  );
}

function parseRow(
  row: XlsxRow,
  indexes: Map<CanonicalHeader, number>,
  date1904: boolean,
  errors: InventoryStockIssue[],
  warehouseMapping?: InventoryWarehouseMapping,
): InventoryStockRow | null {
  const beforeErrors = errors.length;
  const raw = (field: CanonicalHeader) => {
    const index = indexes.get(field);
    return index === undefined ? null : (row.cells[index] ?? null);
  };
  const text = (field: CanonicalHeader) => cellToText(raw(field)).trim();
  const warehouse = requiredText(text("warehouse"), "warehouse", "仓库名称", row.rowNumber, errors);
  const productCode = requiredText(text("productCode"), "productCode", "货品编号", row.rowNumber, errors);
  const lockedQuantity = optionalInteger(raw("lockedQuantity"), "lockedQuantity", "锁定数量", row.rowNumber, errors, true);
  const inTransitQuantity = optionalInteger(raw("inTransitQuantity"), "inTransitQuantity", "在途数量", row.rowNumber, errors, true);
  let onHandQuantity = optionalInteger(raw("onHandQuantity"), "onHandQuantity", "库存数量", row.rowNumber, errors);
  let availableQuantity = optionalInteger(raw("availableQuantity"), "availableQuantity", "可用库存", row.rowNumber, errors);

  if (!indexes.has("availableQuantity") && indexes.has("onHandQuantity")) {
    availableQuantity = onHandQuantity - lockedQuantity;
  }
  if (!indexes.has("onHandQuantity") && indexes.has("availableQuantity")) {
    onHandQuantity = availableQuantity + lockedQuantity;
  }

  let snapshotDate: string | null = null;
  if (!isBlank(raw("snapshotDate"))) {
    snapshotDate = normalizeSalesLedgerDate(raw("snapshotDate"), date1904)?.slice(0, 10) ?? null;
    if (!snapshotDate) {
      errors.push({ code: "INVALID_DATE", message: "库存日期不是有效日期", sourceRowNumber: row.rowNumber, field: "snapshotDate" });
    }
  }
  const unitCostValue = raw("unitCost");
  if (!indexes.has("unitCost") || isBlank(unitCostValue)) {
    errors.push({
      code: "MISSING_VALUE",
      message: "成本价不能为空；明确填写 0 可作为有效零成本",
      sourceRowNumber: row.rowNumber,
      field: "unitCost",
    });
  }
  const unitCostCents = optionalMoneyCents(unitCostValue, "unitCost", "成本价", row.rowNumber, errors);
  const inventoryAgeDays = isBlank(raw("inventoryAgeDays"))
    ? null
    : optionalDays(raw("inventoryAgeDays"), "inventoryAgeDays", "库龄", row.rowNumber, errors);
  const sales7dQuantity = optionalInteger(raw("sales7dQuantity"), "sales7dQuantity", "前7天销量", row.rowNumber, errors);
  const sales30dQuantity = optionalInteger(raw("sales30dQuantity"), "sales30dQuantity", "前30天销量", row.rowNumber, errors);

  if (errors.length > beforeErrors) return null;
  const classification = classifyInventoryWarehouse(warehouse, warehouseMapping);
  return {
    sourceRowNumber: row.rowNumber,
    rowKey: `${warehouse}\u001f${productCode}`,
    snapshotDate,
    warehouse,
    warehouseType: classification.warehouseType,
    warehouseCategory: classification.warehouseCategory,
    includeInInventory: classification.includeInInventory,
    productCode,
    productName: text("productName"),
    brand: text("brand"),
    supplier: text("supplier"),
    specification: text("specification"),
    barcode: text("barcode"),
    category: text("category"),
    onHandQuantity,
    availableQuantity,
    lockedQuantity,
    inTransitQuantity,
    unitCostCents,
    inventoryAgeDays,
    sales7dQuantity,
    sales30dQuantity,
  };
}

function requiredText(
  value: string,
  field: string,
  label: string,
  rowNumber: number,
  errors: InventoryStockIssue[],
) {
  if (!value) {
    errors.push({ code: "MISSING_VALUE", message: `${label}不能为空`, sourceRowNumber: rowNumber, field });
  }
  return value;
}

function optionalInteger(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: InventoryStockIssue[],
  nonNegative = false,
) {
  if (isBlank(value)) return 0;
  const parsed = parseDecimal(value);
  if (parsed === null || !Number.isSafeInteger(parsed) || (nonNegative && parsed < 0)) {
    errors.push({
      code: "INVALID_NUMBER",
      message: nonNegative && parsed !== null && parsed < 0 ? `${label}不能为负数` : `${label}不是有效整数`,
      sourceRowNumber: rowNumber,
      field,
    });
    return 0;
  }
  return parsed;
}

function optionalMoneyCents(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: InventoryStockIssue[],
) {
  if (isBlank(value)) return 0;
  const parsed = parseDecimal(value);
  const cents = parsed === null ? Number.NaN : Math.round(parsed * 100);
  if (!Number.isSafeInteger(cents) || cents < 0) {
    errors.push({ code: "INVALID_NUMBER", message: `${label}不是有效金额`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  return cents;
}

function optionalDays(
  value: XlsxCellValue,
  field: string,
  label: string,
  rowNumber: number,
  errors: InventoryStockIssue[],
) {
  const parsed = parseDecimal(value);
  const days = parsed === null ? Number.NaN : Math.floor(parsed);
  if (!Number.isSafeInteger(days) || days < 0) {
    errors.push({ code: "INVALID_NUMBER", message: `${label}不是有效天数`, sourceRowNumber: rowNumber, field });
    return 0;
  }
  return days;
}

function parseDecimal(value: XlsxCellValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  let text = value.trim().replace(/[￥¥,，\s]/g, "").replace(/件$/, "");
  if (!text) return null;
  if (/^\(.*\)$/.test(text)) text = `-${text.slice(1, -1)}`;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeHeader(value: XlsxCellValue): string {
  return cellToText(value)
    .replace(/^\uFEFF/, "")
    .trim()
    .replace(/[（]/g, "(")
    .replace(/[）]/g, ")")
    .replace(/\s+/g, "");
}

function isBlank(value: XlsxCellValue | undefined): boolean {
  return value === null || value === undefined || (typeof value === "string" && value.trim() === "");
}

function cellToText(value: XlsxCellValue): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return String(value);
}
