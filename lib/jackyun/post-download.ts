import {
  parseXlsxFirstSheet,
  parseXlsxFirstSheets,
  type XlsxCellValue,
  type XlsxFirstSheet,
  type XlsxRow,
} from "../imports/xlsx";
import { createXlsxWorkbookBytes } from "../imports/xlsx-write";

export const jackyunModuleOrder = ["products", "inventory", "inventory_age", "sales", "combos"] as const;
export type JackyunModule = (typeof jackyunModuleOrder)[number];
export type JackyunWorkbookModule = Exclude<JackyunModule, "sales">;

type HeaderRow = {
  row: XlsxRow;
  headers: string[];
  indexes: Map<string, number>;
};

export type JackyunValidationSummary = {
  sheetNames: string[];
  headers: Record<string, string[]>;
  requiredHeaders: Record<string, string[]>;
  sourceRowCount: number;
  importRowCount: number;
  minimumExpectedRows: number;
  parentRowCount?: number;
  uniqueParentCount?: number;
  childParentCount?: number;
  duplicateParentRows?: number;
  duplicateKeyRows?: number;
  duplicateRelationRows?: number;
};

export type JackyunPreprocessingSummary = {
  kind: "none" | "exact_warehouse_filter";
  excludedBrushWarehouseRows: number;
  retainedZeroCostRows: number;
  excludedInvalidCostRows: number;
  /** @deprecated Kept for compatibility with historical audit readers. */
  excludedZeroCostRows: number;
  /** 单行库存货值超过门禁上限（¥10亿）的占位数据行，适用于 inventory 与 inventory_age。 */
  excludedImplausibleValueRows: number;
  /** 实盘/可用库存为负数且系统未允许负库存的行，仅 inventory 模块。 */
  excludedNegativeQuantityRows: number;
  retainedRows: number;
  similarWarehouseNames: string[];
};

export type PreparedJackyunWorkbook = {
  module: JackyunWorkbookModule;
  importBytes: Uint8Array;
  importFileName: string;
  expectedBatchRowCount: number;
  validation: JackyunValidationSummary;
  preprocessing: JackyunPreprocessingSummary;
};

export type PrepareJackyunWorkbookOptions = {
  minimumRows?: number;
  snapshotDate?: string;
};

export class JackyunValidationError extends Error {
  readonly details: Record<string, unknown>;

  constructor(message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "JackyunValidationError";
    this.details = details;
  }
}

export type ComboRelationBaseline = {
  id: string;
  rowCount: number;
};

export function assertComboRelationBaseline(
  currentRowCount: number,
  previous: ComboRelationBaseline | null,
) {
  if (!previous) {
    throw new JackyunValidationError(
      "运营管理系统没有可用的组合装成功批次基线；替换性导入已停止。",
      { currentRowCount, previousBatchId: null, previousRowCount: null },
    );
  }
  const minimumAllowedRowCount = Math.ceil(previous.rowCount * 0.95);
  if (currentRowCount < minimumAllowedRowCount) {
    throw new JackyunValidationError(
      "组合装唯一母子关系数较最近成功批次下降超过 5%；为避免截断文件删除历史关系，已停止导入。",
      {
        currentRowCount,
        previousRowCount: previous.rowCount,
        previousBatchId: previous.id,
        decreasedRows: previous.rowCount - currentRowCount,
        minimumAllowedRowCount,
        retainedRatio: currentRowCount / previous.rowCount,
      },
    );
  }
  return {
    status: "passed" as const,
    previousBatchId: previous.id,
    previousRowCount: previous.rowCount,
    currentRowCount,
    minimumAllowedRowCount,
    retainedRatio: currentRowCount / previous.rowCount,
  };
}

const moduleDefaults: Record<JackyunWorkbookModule, { minimumRows: number }> = {
  products: { minimumRows: 5_000 },
  inventory: { minimumRows: 20_000 },
  inventory_age: { minimumRows: 4_000 },
  combos: { minimumRows: 1_000 },
};

const requiredHeaders = {
  products: ["货品编号", "货品名称", "固定成本价", "基础单位"],
  inventory: ["货品编号", "货品名称", "规格", "单位", "仓库", "固定成本价", "库存数量"],
  inventory_age: ["仓库", "货品编号", "货品名称", "库存数量", "库龄(天)"],
  comboParents: ["货品编号", "货品名称"],
  comboChildren: ["母件编号", "编号", "名称", "数量"],
} as const;

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

function findHeaderRow(sheet: XlsxFirstSheet, required: readonly string[], label: string): HeaderRow {
  for (const row of sheet.rows.slice(0, 20)) {
    const indexes = new Map<string, number>();
    row.cells.forEach((value, index) => {
      const key = normalizeHeader(value);
      if (key && !indexes.has(key)) indexes.set(key, index);
    });
    const missing = required.filter((header) => !indexes.has(normalizeHeader(header)));
    if (!missing.length) {
      return { row, headers: row.cells.map(text), indexes };
    }
  }
  throw new JackyunValidationError(`${label}缺少必需列。`, { requiredHeaders: required });
}

function requiredColumn(header: HeaderRow, name: string, label: string) {
  const index = header.indexes.get(normalizeHeader(name));
  if (index === undefined) throw new JackyunValidationError(`${label}缺少必需列：${name}`, { header: name });
  return index;
}

function dataRows(sheet: XlsxFirstSheet, header: HeaderRow) {
  return sheet.rows.filter((row) => row.rowNumber > header.row.rowNumber && !isBlankRow(row));
}

function assertMinimumRows(module: JackyunWorkbookModule, actual: number, options: PrepareJackyunWorkbookOptions) {
  const minimum = options.minimumRows ?? moduleDefaults[module].minimumRows;
  if (actual < minimum) {
    throw new JackyunValidationError(`${module} 数据量异常：${actual} 行，低于最低量级 ${minimum} 行。`, {
      actualRows: actual,
      minimumExpectedRows: minimum,
    });
  }
  return minimum;
}

function assertRequiredValues(
  rows: XlsxRow[],
  columns: Array<{ index: number; label: string }>,
  sourceLabel: string,
) {
  const invalidRows = rows.flatMap((row) => columns
    .filter((column) => !text(row.cells[column.index]))
    .map((column) => ({ rowNumber: row.rowNumber, field: column.label })));
  if (invalidRows.length) {
    throw new JackyunValidationError(`${sourceLabel}存在必填编码或仓库为空的记录。`, {
      invalidValueCount: invalidRows.length,
      samples: invalidRows.slice(0, 20),
    });
  }
}

function noPreprocessing(rowCount: number): JackyunPreprocessingSummary {
  return {
    kind: "none",
    excludedBrushWarehouseRows: 0,
    retainedZeroCostRows: 0,
    excludedInvalidCostRows: 0,
    excludedZeroCostRows: 0,
    excludedImplausibleValueRows: 0,
    excludedNegativeQuantityRows: 0,
    retainedRows: rowCount,
    similarWarehouseNames: [],
  };
}

function prepareSingleSheet(
  module: "products" | "inventory" | "inventory_age",
  bytes: Uint8Array,
  options: PrepareJackyunWorkbookOptions,
): PreparedJackyunWorkbook {
  const sheet = parseXlsxFirstSheet(bytes);
  const required = requiredHeaders[module];
  const header = findHeaderRow(sheet, required, `${module} 工作表`);
  const sourceRows = dataRows(sheet, header);

  if (module === "products") {
    const productCodeColumn = requiredColumn(header, "货品编号", "products 工作表");
    assertRequiredValues(sourceRows, [{ index: productCodeColumn, label: "货品编号" }], "products 工作表");
    const uniqueProductCount = new Set(sourceRows.map((row) => text(row.cells[productCodeColumn]))).size;
    const minimumExpectedRows = assertMinimumRows(module, uniqueProductCount, options);
    return {
      module,
      importBytes: bytes,
      importFileName: "货品导出.xlsx",
      expectedBatchRowCount: uniqueProductCount,
      validation: {
        sheetNames: [sheet.sheetName],
        headers: { [sheet.sheetName]: header.headers },
        requiredHeaders: { [sheet.sheetName]: [...required] },
        sourceRowCount: sourceRows.length,
        importRowCount: uniqueProductCount,
        minimumExpectedRows,
        duplicateKeyRows: sourceRows.length - uniqueProductCount,
      },
      preprocessing: noPreprocessing(sourceRows.length),
    };
  }

  const warehouseColumn = requiredColumn(header, "仓库", `${module} 工作表`);
  const productCodeColumn = requiredColumn(header, "货品编号", `${module} 工作表`);
  const costColumn = module === "inventory"
    ? requiredColumn(header, "固定成本价", `${module} 工作表`)
    : header.indexes.get(normalizeHeader("固定成本价"));
  const quantityColumn = requiredColumn(header, "库存数量", `${module} 工作表`);
  // 吉客云分仓库存通常包含“可用库存”，但它不是导入文件的基础必需列。
  // 若存在，必须与实盘库存同时满足库存质量门禁，避免服务端在完成阶段整批拒绝。
  const availableQuantityColumn = module === "inventory"
    ? header.indexes.get(normalizeHeader("可用库存"))
    : undefined;
  const excludedRows: XlsxRow[] = [];
  const retainedZeroCostRows: XlsxRow[] = [];
  const excludedInvalidCostRows: XlsxRow[] = [];
  const excludedImplausibleValueRows: XlsxRow[] = [];
  const excludedNegativeQuantityRows: XlsxRow[] = [];
  const retainedRows: XlsxRow[] = [];
  for (const row of sourceRows) {
    if (text(row.cells[warehouseColumn]) === "刷刷仓") excludedRows.push(row);
    else if (module === "inventory" && costColumn !== undefined && nonNegativeNumber(row.cells[costColumn]) === null) {
      excludedInvalidCostRows.push(row);
    }
    else if (module === "inventory" && quantityColumn !== undefined
      && hasNegativeInventoryQuantity(row.cells[quantityColumn], availableQuantityColumn === undefined ? undefined : row.cells[availableQuantityColumn])) {
      excludedNegativeQuantityRows.push(row);
    }
    else if (costColumn !== undefined
      && isImplausibleInventoryRow(row.cells[quantityColumn], row.cells[costColumn])) {
      // 吉客云 ERP 供应商仓存在数量≈百万/十万的占位库存（单行货值可达数十亿元），
      // 这些行会被服务端库存或库龄质量门禁整批拒绝，
      // 在预处理阶段剔除以保证其余真实数据可按快照日全量替换入库。
      excludedImplausibleValueRows.push(row);
    }
    else {
      if (module === "inventory" && costColumn !== undefined && nonNegativeNumber(row.cells[costColumn]) === 0) {
        retainedZeroCostRows.push(row);
      }
      retainedRows.push(row);
    }
  }
  assertRequiredValues(retainedRows, [
    { index: warehouseColumn, label: "仓库" },
    { index: productCodeColumn, label: "货品编号" },
  ], `${module} 工作表`);
  const uniqueKeyCount = module === "inventory_age"
    ? new Set(retainedRows.map((row) => `${text(row.cells[warehouseColumn])}\u001f${text(row.cells[productCodeColumn])}`)).size
    : retainedRows.length;
  const minimumExpectedRows = assertMinimumRows(module, uniqueKeyCount, options);
  const similarWarehouseNames = [...new Set(sourceRows
    .map((row) => text(row.cells[warehouseColumn]))
    .filter((warehouse) => warehouse.includes("刷刷") && warehouse !== "刷刷仓"))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"));
  const importFileName = module === "inventory"
    ? "分仓库存查询_已剔除刷刷仓及无效成本.xlsx"
    : "库龄分析_已剔除刷刷仓及异常货值.xlsx";
  if (!options.snapshotDate || !/^\d{4}-\d{2}-\d{2}$/.test(options.snapshotDate)) {
    throw new JackyunValidationError(`${module} 必须提供快照日期以生成确定的本轮导入文件。`);
  }
  const importBytes = createXlsxWorkbookBytes([
    {
      name: sheet.sheetName,
      rows: [header.row.cells, ...retainedRows.map((row) => row.cells)],
    },
    {
      name: "导入元数据",
      rows: [["字段", "值"], ["快照日期", options.snapshotDate], ["模块", module]],
    },
  ]);

  return {
    module,
    importBytes,
    importFileName,
    expectedBatchRowCount: uniqueKeyCount,
    validation: {
      sheetNames: [sheet.sheetName],
      headers: { [sheet.sheetName]: header.headers },
      requiredHeaders: { [sheet.sheetName]: [...required] },
      sourceRowCount: sourceRows.length,
      importRowCount: uniqueKeyCount,
      minimumExpectedRows,
      duplicateKeyRows: retainedRows.length - uniqueKeyCount,
    },
    preprocessing: {
      kind: "exact_warehouse_filter",
      excludedBrushWarehouseRows: excludedRows.length,
      retainedZeroCostRows: retainedZeroCostRows.length,
      excludedInvalidCostRows: excludedInvalidCostRows.length,
      excludedZeroCostRows: 0,
      excludedImplausibleValueRows: excludedImplausibleValueRows.length,
      excludedNegativeQuantityRows: excludedNegativeQuantityRows.length,
      retainedRows: retainedRows.length,
      similarWarehouseNames,
    },
  };
}

function nonNegativeNumber(value: XlsxCellValue | undefined) {
  if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/,/g, "");
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function positiveNumber(value: XlsxCellValue | undefined) {
  const parsed = nonNegativeNumber(value);
  return parsed !== null && parsed > 0 ? parsed : null;
}

function parseNumberOrNull(value: XlsxCellValue | undefined) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.trim().replace(/,/g, "");
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function isImplausibleInventoryRow(quantityValue: XlsxCellValue | undefined, costValue: XlsxCellValue | undefined) {
  const quantity = parseNumberOrNull(quantityValue);
  const cost = parseNumberOrNull(costValue);
  if (quantity === null || cost === null) return false;
  // 单行货值超过 10 亿元通常是 ERP 占位/异常值。
  return Math.abs(quantity * cost) > 1_000_000_000;
}

function hasNegativeInventoryQuantity(
  onHandValue: XlsxCellValue | undefined,
  availableValue: XlsxCellValue | undefined,
) {
  return [onHandValue, availableValue]
    .map(parseNumberOrNull)
    .some((quantity) => quantity !== null && quantity < 0);
}

function prepareCombos(bytes: Uint8Array, options: PrepareJackyunWorkbookOptions): PreparedJackyunWorkbook {
  const [parentSheet, childSheet] = parseXlsxFirstSheets(bytes, 2);
  if (!parentSheet || !childSheet) {
    throw new JackyunValidationError("组合装文件必须包含至少两张工作表。", { sheetCount: parentSheet ? 1 : 0 });
  }
  const parentHeader = findHeaderRow(parentSheet, requiredHeaders.comboParents, "组合装母件表");
  const childHeader = findHeaderRow(childSheet, requiredHeaders.comboChildren, "组合装子件表");
  const parentRows = dataRows(parentSheet, parentHeader);
  const childRows = dataRows(childSheet, childHeader);
  if (!parentRows.length) throw new JackyunValidationError("组合装母件表没有真实数据行。");
  const parentProductCodeColumn = requiredColumn(parentHeader, "货品编号", "组合装母件表");
  assertRequiredValues(parentRows, [{ index: parentProductCodeColumn, label: "货品编号" }], "组合装母件表");
  const parentCodeColumn = requiredColumn(childHeader, "母件编号", "组合装子件表");
  const childCodeColumn = requiredColumn(childHeader, "编号", "组合装子件表");
  const quantityColumn = requiredColumn(childHeader, "数量", "组合装子件表");
  assertRequiredValues(childRows, [
    { index: parentCodeColumn, label: "母件编号" },
    { index: childCodeColumn, label: "编号" },
  ], "组合装子件表");
  const invalidQuantityRows = childRows
    .filter((row) => positiveNumber(row.cells[quantityColumn]) === null)
    .map((row) => ({ rowNumber: row.rowNumber, value: row.cells[quantityColumn] ?? null }));
  if (invalidQuantityRows.length) {
    throw new JackyunValidationError("组合装子件数量存在空值、非数字或小于等于 0 的记录。", {
      invalidQuantityRowCount: invalidQuantityRows.length,
      samples: invalidQuantityRows.slice(0, 20),
    });
  }
  const parentCodes = parentRows.map((row) => text(row.cells[parentProductCodeColumn]));
  const uniqueParentCodes = new Set(parentCodes);
  const childParentCodes = new Set(childRows.map((row) => text(row.cells[parentCodeColumn])));
  const missingRelationParents = [...uniqueParentCodes].filter((code) => !childParentCodes.has(code));
  const orphanRelationParents = [...childParentCodes].filter((code) => !uniqueParentCodes.has(code));
  if (missingRelationParents.length || orphanRelationParents.length) {
    throw new JackyunValidationError(
      "组合装母件表与子件关系表的母件覆盖不一致；文件可能被截断，已停止导入。",
      {
        uniqueParentCount: uniqueParentCodes.size,
        childParentCount: childParentCodes.size,
        missingRelationParentCount: missingRelationParents.length,
        missingRelationParentSamples: missingRelationParents.slice(0, 20),
        orphanRelationParentCount: orphanRelationParents.length,
        orphanRelationParentSamples: orphanRelationParents.slice(0, 20),
      },
    );
  }
  const relationKeys = childRows.map((row) => `${text(row.cells[parentCodeColumn])}\u0000${text(row.cells[childCodeColumn])}`);
  const uniqueRelations = new Set(relationKeys);
  const minimumExpectedRows = assertMinimumRows("combos", uniqueRelations.size, options);

  return {
    module: "combos",
    importBytes: bytes,
    importFileName: "组合装及子件导出.xlsx",
    expectedBatchRowCount: uniqueRelations.size,
    validation: {
      sheetNames: [parentSheet.sheetName, childSheet.sheetName],
      headers: {
        [parentSheet.sheetName]: parentHeader.headers,
        [childSheet.sheetName]: childHeader.headers,
      },
      requiredHeaders: {
        [parentSheet.sheetName]: [...requiredHeaders.comboParents],
        [childSheet.sheetName]: [...requiredHeaders.comboChildren],
      },
      sourceRowCount: childRows.length,
      importRowCount: uniqueRelations.size,
      minimumExpectedRows,
      parentRowCount: parentRows.length,
      uniqueParentCount: uniqueParentCodes.size,
      childParentCount: childParentCodes.size,
      duplicateParentRows: parentRows.length - uniqueParentCodes.size,
      duplicateRelationRows: childRows.length - uniqueRelations.size,
    },
    preprocessing: noPreprocessing(childRows.length),
  };
}

export function prepareJackyunWorkbook(
  module: JackyunWorkbookModule,
  input: ArrayBuffer | Uint8Array,
  options: PrepareJackyunWorkbookOptions = {},
): PreparedJackyunWorkbook {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (module === "combos") return prepareCombos(bytes, options);
  return prepareSingleSheet(module, bytes, options);
}
