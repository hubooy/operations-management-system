import {
  parseXlsxFirstSheets,
  parseXlsxFirstSheet,
  type XlsxCellValue,
  type XlsxRow,
} from "./xlsx";
import { inferInventoryWarehouseType } from "@/lib/inventory/warehouse-classification";

export const ERP_REFERENCE_SOURCE_KEYS = ["products", "inventory_age", "combos"] as const;
export type ErpReferenceSourceKey = (typeof ERP_REFERENCE_SOURCE_KEYS)[number];

export const ERP_REFERENCE_SOURCE_LABELS: Record<ErpReferenceSourceKey, string> = {
  products: "吉客云 ERP · 货品导出",
  inventory_age: "吉客云 ERP · 库龄分析",
  combos: "吉客云 ERP · 组合装及子件",
};

export type ErpReferenceIssue = {
  sourceRowNumber?: number;
  field?: string;
  code: string;
  message: string;
};

export type ProductMasterRow = {
  sourceRowNumber: number;
  productCode: string;
  productName: string;
  brand: string;
  specification: string;
  barcode: string;
  category: string;
  supplier: string;
  productStatus: string;
};

export type InventoryAgeImportRow = {
  sourceRowNumber: number;
  warehouse: string;
  warehouseType: "owned" | "jd_rdc" | "other";
  productCode: string;
  productName: string;
  specification: string;
  category: string;
  availableQuantity: number;
  inventoryAgeDays: number | null;
  sales7dQuantity: number | null;
  sales30dQuantity: number | null;
  unitCostCents: number;
  stockValueCents: number;
};

export type ComboItemImportRow = {
  sourceRowNumber: number;
  parentCode: string;
  parentName: string;
  childCode: string;
  childName: string;
  childQuantityMilli: number;
};

export type ErpReferenceParseResult = {
  source: ErpReferenceSourceKey;
  sheetName: string;
  rows: ProductMasterRow[] | InventoryAgeImportRow[] | ComboItemImportRow[];
  errors: ErpReferenceIssue[];
  warnings: ErpReferenceIssue[];
  totals: Record<string, number>;
};

type AliasMap = Record<string, readonly string[]>;

const PRODUCT_ALIASES = {
  productCode: ["货品编号", "商品编码", "商品编号", "SKU编码", "SKU编号", "商家编码", "货号", "编码"],
  productName: ["货品名称", "商品名称", "商品", "品名", "名称"],
  brand: ["品牌", "品牌名称", "商品品牌", "货品品牌"],
  specification: ["规格", "规格名称", "规格型号", "商品规格", "货品规格"],
  barcode: ["条码", "商品条码", "货品条码", "国际条码"],
  category: ["分类", "货品分类", "商品分类", "末级分类", "类目"],
  supplier: ["供应商", "供应商名称", "默认供应商", "首选供应商"],
  productStatus: ["状态", "货品状态", "商品状态", "启用状态"],
} as const satisfies AliasMap;

const AGE_ALIASES = {
  warehouse: ["仓库名称", "仓库", "分仓", "仓库名"],
  productCode: ["货品编号", "商品编码", "商品编号", "SKU编码", "SKU编号", "商家编码", "货号"],
  productName: ["货品名称", "商品名称", "商品", "品名"],
  specification: ["规格", "规格名称", "规格型号", "商品规格", "货品规格"],
  category: ["分类", "货品分类", "商品分类", "末级分类", "类目"],
  availableQuantity: ["可用库存", "可售库存", "可用数量", "可售数量", "库存数量", "实盘数量", "库存"],
  inventoryAgeDays: ["库龄天数", "库龄(天)", "库存天数", "库龄", "最长库龄", "平均库龄"],
  sales7dQuantity: ["前7天销量", "近7天销量", "7天销量", "最近7天销量"],
  sales30dQuantity: ["前30天销量", "近30天销量", "30天销量", "最近30天销量"],
  unitCost: ["固定成本价", "成本价", "单位成本", "含税成本价"],
  stockValue: ["库存金额", "库存货值", "库存成本", "货值"],
} as const satisfies AliasMap;

const COMBO_ALIASES = {
  parentCode: ["组合装编号", "组合装编码", "组合商品编码", "母件编号", "母件编码", "主件编号", "主件编码", "套装编码"],
  parentName: ["组合装名称", "组合商品名称", "母件名称", "主件名称", "套装名称"],
  childCode: ["子件编号", "子件编码", "子商品编码", "明细货品编号", "成分货品编号", "子货品编号"],
  childName: ["子件名称", "子商品名称", "明细货品名称", "成分货品名称", "子货品名称"],
  childQuantity: ["子件数量", "子件用量", "数量", "用量", "组成数量", "配比数量"],
} as const satisfies AliasMap;

const JKY_COMBO_PARENT_ALIASES = {
  parentCode: ["货品编号"],
  parentName: ["货品名称"],
} as const satisfies AliasMap;

const JKY_COMBO_CHILD_ALIASES = {
  parentCode: ["母件编号"],
  childCode: ["编号"],
  childName: ["名称"],
  childQuantity: ["数量"],
} as const satisfies AliasMap;

const HEADER_SEARCH_ROWS = 30;
const MAX_REFERENCE_ROWS = 100_000;
const MAX_ISSUES = 200;

function normalizeHeader(value: XlsxCellValue) {
  return cellText(value).toLowerCase().replace(/[\s\u3000()（）【】\[\]_.·:：/\\-]+/g, "");
}

function cellText(value: XlsxCellValue) {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value).trim();
}

function headerIndex(row: XlsxRow, aliases: AliasMap) {
  const normalizedAliases = new Map<string, string>();
  for (const [field, values] of Object.entries(aliases)) {
    for (const value of values) normalizedAliases.set(normalizeHeader(value), field);
  }
  const result = new Map<string, number>();
  row.cells.forEach((cell, index) => {
    const field = normalizedAliases.get(normalizeHeader(cell));
    if (field && !result.has(field)) result.set(field, index);
  });
  return result;
}

function findHeader(rows: XlsxRow[], aliases: AliasMap, required: string[]) {
  let best: { rowIndex: number; fields: Map<string, number> } | null = null;
  for (let rowIndex = 0; rowIndex < Math.min(rows.length, HEADER_SEARCH_ROWS); rowIndex += 1) {
    const fields = headerIndex(rows[rowIndex], aliases);
    if (!best || fields.size > best.fields.size) best = { rowIndex, fields };
    if (required.every((field) => fields.has(field))) return { rowIndex, fields };
  }
  const missing = required.filter((field) => !best?.fields.has(field));
  throw new Error(`未识别到报表表头，缺少必要列：${missing.join("、")}`);
}

function hasHeader(rows: XlsxRow[], aliases: AliasMap, required: string[]) {
  for (let rowIndex = 0; rowIndex < Math.min(rows.length, HEADER_SEARCH_ROWS); rowIndex += 1) {
    const fields = headerIndex(rows[rowIndex], aliases);
    if (required.every((field) => fields.has(field))) return true;
  }
  return false;
}

function candidateRows(rows: XlsxRow[], headerRowIndex: number) {
  return rows.slice(headerRowIndex + 1).filter((row) => row.cells.some((cell) => cellText(cell) !== ""));
}

function read(row: XlsxRow, fields: Map<string, number>, field: string) {
  const index = fields.get(field);
  return index === undefined ? "" : cellText(row.cells[index]);
}

function parseNumber(value: string) {
  if (!value) return null;
  const normalized = value.replace(/[,，\s￥¥元件天]/g, "").replace(/[()（）]/g, "");
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function integer(value: string, field: string, label: string, row: XlsxRow, errors: ErpReferenceIssue[]) {
  const parsed = parseNumber(value);
  if (parsed === null || !Number.isSafeInteger(Math.trunc(parsed))) {
    errors.push({ sourceRowNumber: row.rowNumber, field, code: "INVALID_NUMBER", message: `${label}不是有效数字` });
    return 0;
  }
  return Math.trunc(parsed);
}

function optionalInteger(value: string) {
  const parsed = parseNumber(value);
  return parsed === null ? null : Math.trunc(parsed);
}

function moneyCents(value: string) {
  const parsed = parseNumber(value);
  return parsed === null ? 0 : Math.round(parsed * 100);
}

function requiredText(value: string, field: string, label: string, row: XlsxRow, errors: ErpReferenceIssue[]) {
  if (!value) errors.push({ sourceRowNumber: row.rowNumber, field, code: "MISSING_VALUE", message: `${label}不能为空` });
  return value;
}

function pushDuplicateWarnings(
  values: string[],
  label: string,
  warnings: ErpReferenceIssue[],
) {
  const duplicateCount = values.length - new Set(values).size;
  if (duplicateCount > 0) {
    warnings.push({ code: "DUPLICATE_KEYS", message: `${duplicateCount} 行${label}重复，已采用文件中最后一行` });
  }
}

function parseProducts(rows: XlsxRow[]) {
  const { rowIndex, fields } = findHeader(rows, PRODUCT_ALIASES, ["productCode"]);
  const errors: ErpReferenceIssue[] = [];
  const warnings: ErpReferenceIssue[] = [];
  const parsed: ProductMasterRow[] = [];
  for (const row of candidateRows(rows, rowIndex)) {
    const before = errors.length;
    const productCode = requiredText(read(row, fields, "productCode"), "productCode", "货品编号", row, errors);
    if (errors.length > before) {
      if (errors.length >= MAX_ISSUES) break;
      continue;
    }
    parsed.push({
      sourceRowNumber: row.rowNumber,
      productCode,
      productName: read(row, fields, "productName"),
      brand: read(row, fields, "brand"),
      specification: read(row, fields, "specification"),
      barcode: read(row, fields, "barcode"),
      category: read(row, fields, "category"),
      supplier: read(row, fields, "supplier"),
      productStatus: read(row, fields, "productStatus"),
    });
  }
  pushDuplicateWarnings(parsed.map((row) => row.productCode), "货品编号", warnings);
  const deduplicated = [...new Map(parsed.map((row) => [row.productCode, row])).values()];
  return { rows: deduplicated, errors, warnings, totals: { sourceRowCount: parsed.length, productCount: deduplicated.length } };
}

function parseInventoryAge(rows: XlsxRow[]) {
  const { rowIndex, fields } = findHeader(rows, AGE_ALIASES, ["warehouse", "productCode", "availableQuantity"]);
  const errors: ErpReferenceIssue[] = [];
  const warnings: ErpReferenceIssue[] = [];
  const parsed: InventoryAgeImportRow[] = [];
  for (const row of candidateRows(rows, rowIndex)) {
    const before = errors.length;
    const warehouse = requiredText(read(row, fields, "warehouse"), "warehouse", "仓库名称", row, errors);
    const productCode = requiredText(read(row, fields, "productCode"), "productCode", "货品编号", row, errors);
    const availableQuantity = integer(read(row, fields, "availableQuantity"), "availableQuantity", "可用库存", row, errors);
    if (errors.length > before) {
      if (errors.length >= MAX_ISSUES) break;
      continue;
    }
    const unitCostCents = moneyCents(read(row, fields, "unitCost"));
    const explicitStockValueCents = moneyCents(read(row, fields, "stockValue"));
    parsed.push({
      sourceRowNumber: row.rowNumber,
      warehouse,
      warehouseType: inferInventoryWarehouseType(warehouse),
      productCode,
      productName: read(row, fields, "productName"),
      specification: read(row, fields, "specification"),
      category: read(row, fields, "category"),
      availableQuantity,
      inventoryAgeDays: optionalInteger(read(row, fields, "inventoryAgeDays")),
      sales7dQuantity: optionalInteger(read(row, fields, "sales7dQuantity")),
      sales30dQuantity: optionalInteger(read(row, fields, "sales30dQuantity")),
      unitCostCents,
      stockValueCents: explicitStockValueCents || Math.max(0, availableQuantity) * unitCostCents,
    });
  }
  pushDuplicateWarnings(parsed.map((row) => `${row.warehouse}\u001f${row.productCode}`), "仓库与货品组合", warnings);
  const deduplicated = [...new Map(parsed.map((row) => [`${row.warehouse}\u001f${row.productCode}`, row])).values()];
  return {
    rows: deduplicated,
    errors,
    warnings,
    totals: {
      sourceRowCount: parsed.length,
      rowCount: deduplicated.length,
      warehouseCount: new Set(deduplicated.map((row) => row.warehouse)).size,
      productCount: new Set(deduplicated.map((row) => row.productCode)).size,
    },
  };
}

function parseCombos(rows: XlsxRow[]) {
  const { rowIndex, fields } = findHeader(rows, COMBO_ALIASES, ["parentCode", "childCode", "childQuantity"]);
  const errors: ErpReferenceIssue[] = [];
  const warnings: ErpReferenceIssue[] = [];
  const parsed: ComboItemImportRow[] = [];
  for (const row of candidateRows(rows, rowIndex)) {
    const before = errors.length;
    const parentCode = requiredText(read(row, fields, "parentCode"), "parentCode", "组合装编号", row, errors);
    const childCode = requiredText(read(row, fields, "childCode"), "childCode", "子件编号", row, errors);
    const childQuantity = parseNumber(read(row, fields, "childQuantity"));
    if (childQuantity === null || childQuantity <= 0) {
      errors.push({ sourceRowNumber: row.rowNumber, field: "childQuantity", code: "INVALID_NUMBER", message: "子件数量必须大于 0" });
    }
    if (errors.length > before) {
      if (errors.length >= MAX_ISSUES) break;
      continue;
    }
    parsed.push({
      sourceRowNumber: row.rowNumber,
      parentCode,
      parentName: read(row, fields, "parentName"),
      childCode,
      childName: read(row, fields, "childName"),
      childQuantityMilli: Math.round((childQuantity ?? 0) * 1000),
    });
  }
  pushDuplicateWarnings(parsed.map((row) => `${row.parentCode}\u001f${row.childCode}`), "母件与子件组合", warnings);
  const deduplicated = [...new Map(parsed.map((row) => [`${row.parentCode}\u001f${row.childCode}`, row])).values()];
  return {
    rows: deduplicated,
    errors,
    warnings,
    totals: {
      sourceRowCount: parsed.length,
      comboItemCount: deduplicated.length,
      parentCount: new Set(deduplicated.map((row) => row.parentCode)).size,
      childCount: new Set(deduplicated.map((row) => row.childCode)).size,
    },
  };
}

function parseJkyComboWorkbook(parentRows: XlsxRow[], childRows: XlsxRow[]) {
  const parentHeader = findHeader(parentRows, JKY_COMBO_PARENT_ALIASES, ["parentCode"]);
  const childHeader = findHeader(childRows, JKY_COMBO_CHILD_ALIASES, ["parentCode", "childCode", "childQuantity"]);
  const errors: ErpReferenceIssue[] = [];
  const warnings: ErpReferenceIssue[] = [];
  const parentDataRows = candidateRows(parentRows, parentHeader.rowIndex);
  const childDataRows = candidateRows(childRows, childHeader.rowIndex);
  const parentNames = new Map<string, string>();
  let parentRowsMissingCode = 0;

  for (const row of parentDataRows) {
    const parentCode = read(row, parentHeader.fields, "parentCode");
    if (!parentCode) {
      parentRowsMissingCode += 1;
      continue;
    }
    parentNames.set(parentCode, read(row, parentHeader.fields, "parentName"));
  }
  if (parentRowsMissingCode > 0) {
    warnings.push({ code: "MISSING_PARENT_CODE", message: `母件表中有 ${parentRowsMissingCode} 行缺少货品编号，未用于名称映射` });
  }

  const parsed: ComboItemImportRow[] = [];
  for (const row of childDataRows) {
    const before = errors.length;
    const parentCode = requiredText(read(row, childHeader.fields, "parentCode"), "parentCode", "母件编号", row, errors);
    const childCode = requiredText(read(row, childHeader.fields, "childCode"), "childCode", "子件编号", row, errors);
    const childQuantity = parseNumber(read(row, childHeader.fields, "childQuantity"));
    if (childQuantity === null || childQuantity <= 0) {
      errors.push({ sourceRowNumber: row.rowNumber, field: "childQuantity", code: "INVALID_NUMBER", message: "子件数量必须大于 0" });
    }
    if (errors.length > before) {
      if (errors.length >= MAX_ISSUES) break;
      continue;
    }
    parsed.push({
      sourceRowNumber: row.rowNumber,
      parentCode,
      parentName: parentNames.get(parentCode) ?? "",
      childCode,
      childName: read(row, childHeader.fields, "childName"),
      childQuantityMilli: Math.round((childQuantity ?? 0) * 1000),
    });
  }

  const unresolvedParentCodes = new Set(parsed.filter((row) => !parentNames.has(row.parentCode)).map((row) => row.parentCode));
  if (unresolvedParentCodes.size > 0) {
    warnings.push({
      code: "MISSING_PARENT_NAME",
      message: `子件表中有 ${unresolvedParentCodes.size} 个母件编号未在母件表找到名称，已保留编号`,
    });
  }
  pushDuplicateWarnings(parsed.map((row) => `${row.parentCode}\u001f${row.childCode}`), "母件与子件组合", warnings);
  const deduplicated = [...new Map(parsed.map((row) => [`${row.parentCode}\u001f${row.childCode}`, row])).values()];
  return {
    rows: deduplicated,
    errors,
    warnings,
    totals: {
      parentSheetRowCount: parentDataRows.length,
      childSheetRowCount: childDataRows.length,
      sourceRowCount: parsed.length,
      comboItemCount: deduplicated.length,
      parentCount: new Set(deduplicated.map((row) => row.parentCode)).size,
      childCount: new Set(deduplicated.map((row) => row.childCode)).size,
    },
  };
}

export function isErpReferenceSourceKey(value: unknown): value is ErpReferenceSourceKey {
  return typeof value === "string" && (ERP_REFERENCE_SOURCE_KEYS as readonly string[]).includes(value);
}

function normalizeComboDisplayNames<T extends { rows: ComboItemImportRow[]; warnings: ErpReferenceIssue[] }>(parsed: T): T {
  let changedFields = 0;
  for (const row of parsed.rows) {
    for (const field of ["parentName", "childName"] as const) {
      if (/[\t\r\n]/.test(row[field])) {
        row[field] = row[field].replace(/[ \t\r\n]+/g, " ").trim();
        changedFields += 1;
      }
    }
  }
  if (changedFields) parsed.warnings.push({ code: "NORMALIZED_DISPLAY_WHITESPACE",
    message: `组合装名称中 ${changedFields} 个字段的制表或换行空白已规范为空格，编号和子件数量保持原值` });
  return parsed;
}

export function parseErpReferenceXlsx(
  source: ErpReferenceSourceKey,
  input: ArrayBuffer | Uint8Array,
): ErpReferenceParseResult {
  if (source === "combos") {
    const sheets = parseXlsxFirstSheets(input, 2, { maxRows: MAX_REFERENCE_ROWS + 1 });
    const parentSheet = sheets[0];
    const childSheet = sheets[1];
    if (
      parentSheet
      && childSheet
      && hasHeader(parentSheet.rows, JKY_COMBO_PARENT_ALIASES, ["parentCode"])
      && hasHeader(childSheet.rows, JKY_COMBO_CHILD_ALIASES, ["parentCode", "childCode", "childQuantity"])
    ) {
      const parsed = normalizeComboDisplayNames(parseJkyComboWorkbook(parentSheet.rows, childSheet.rows));
      return { source, sheetName: `${parentSheet.sheetName} + ${childSheet.sheetName}`, ...parsed };
    }
    const fallbackSheet = parseXlsxFirstSheet(input, { maxRows: MAX_REFERENCE_ROWS + 1 });
    return { source, sheetName: fallbackSheet.sheetName, ...normalizeComboDisplayNames(parseCombos(fallbackSheet.rows)) };
  }

  const sheet = parseXlsxFirstSheet(input, { maxRows: MAX_REFERENCE_ROWS + 1 });
  const parsed = source === "products"
    ? parseProducts(sheet.rows)
    : source === "inventory_age"
      ? parseInventoryAge(sheet.rows)
      : parseCombos(sheet.rows);
  return { source, sheetName: sheet.sheetName, ...parsed };
}
