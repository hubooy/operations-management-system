import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_IMPORTS_PATH,
  INVENTORY_SETTINGS_PATH,
  INVENTORY_WAREHOUSE_MAPPINGS_PATH,
  type DjangoInventoryServiceOptions,
} from "@/lib/django/inventory-service";
import { validateInventoryImportRows } from "@/lib/inventory/data-quality";
import {
  parseInventoryStockXlsx,
  type InventoryStockIssue,
  type InventoryStockRow,
} from "@/lib/imports/inventory-stock";
import { isXlsxSignature } from "@/lib/sales/import-service";
import {
  parseWarehouseMappingPayload,
  warehouseMappingRecord,
} from "@/lib/inventory/warehouse-mapping-contract";

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return toHex(await crypto.subtle.digest("SHA-256", input));
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "inventory-stock.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function dateFromFileName(fileName: string): string | null {
  const compact = /(?:^|\D)(20\d{2})(\d{2})(\d{2})(?:\D|$)/.exec(fileName);
  const separated = /(?:^|\D)(20\d{2})[-_.年](\d{1,2})[-_.月](\d{1,2})日?(?:\D|$)/.exec(fileName);
  const match = compact ?? separated;
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return `${year.toString().padStart(4, "0")}-${month.toString().padStart(2, "0")}-${day.toString().padStart(2, "0")}`;
}

function isIsoDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function mapIssue(issue: InventoryStockIssue | { row?: number; field?: string; code: string; message: string }) {
  const source = issue as InventoryStockIssue;
  return {
    ...(Number.isSafeInteger(source.sourceRowNumber ?? (issue as { row?: number }).row)
      ? { row: Number(source.sourceRowNumber ?? (issue as { row?: number }).row) }
      : {}),
    ...(source.field ? { field: source.field } : {}),
    code: issue.code,
    message: issue.message,
  };
}

function summarize(rows: InventoryStockRow[], sourceRowCount: number) {
  const warehouses = new Set<string>();
  const products = new Set<string>();
  const totals = {
    sourceRowCount,
    rowCount: rows.length,
    warehouseCount: 0,
    productCount: 0,
    includedInventoryRowCount: 0,
    excludedInventoryRowCount: 0,
    onHandQuantity: 0,
    availableQuantity: 0,
    lockedQuantity: 0,
    inTransitQuantity: 0,
    stockValueCents: 0,
    sales7dQuantity: 0,
    sales30dQuantity: 0,
  };
  for (const row of rows) {
    warehouses.add(row.warehouse);
    products.add(row.productCode);
    totals.onHandQuantity += row.onHandQuantity;
    totals.availableQuantity += row.availableQuantity;
    totals.lockedQuantity += row.lockedQuantity;
    totals.inTransitQuantity += row.inTransitQuantity;
    totals.stockValueCents += Math.max(0, row.availableQuantity) * row.unitCostCents;
    totals.sales7dQuantity += row.sales7dQuantity;
    totals.sales30dQuantity += row.sales30dQuantity;
    if (row.includeInInventory) totals.includedInventoryRowCount += 1;
    else totals.excludedInventoryRowCount += 1;
  }
  totals.warehouseCount = warehouses.size;
  totals.productCount = products.size;
  return totals;
}

export type InventoryImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  batch?: Record<string, unknown>;
  warnings: Array<Record<string, unknown>>;
  errors?: Array<Record<string, unknown>>;
  errorCount?: number;
};

export async function importInventoryStockToDjango(
  input: {
    principal: AppPrincipal;
    bytes: Uint8Array;
    fileName: string;
    fileSizeBytes: number;
    snapshotDateOverride?: string;
  },
  options: Omit<DjangoInventoryServiceOptions, "config"> = {},
): Promise<InventoryImportExecution> {
  const service = createDjangoInventoryService();
  const rawFileHash = await sha256(input.bytes);
  const fileName = safeFileName(input.fileName);
  const reject = async (result: InventoryImportExecution) => {
    const audit = await service.requestJson<InventoryImportExecution>(input.principal, {
      method: "POST",
      path: INVENTORY_IMPORTS_PATH,
      service: "writer",
      payload: {
        action: "reject",
        dataset: "stock",
        file: { name: fileName, sizeBytes: input.fileSizeBytes, rawFileHash },
        snapshotDate: isIsoDate(input.snapshotDateOverride) ? input.snapshotDateOverride : null,
        message: result.message,
        errors: (result.errors ?? []).slice(0, 200),
        warnings: result.warnings.slice(0, 200),
      },
    }, options);
    return audit.data;
  };
  if (!isXlsxSignature(input.bytes)) {
    return reject({ ok: false, status: "rejected", message: "文件签名不是有效的 .xlsx（ZIP）格式", warnings: [], errors: [{ code: "INVALID_XLSX_SIGNATURE", message: "文件签名无效" }], errorCount: 1 });
  }
  const mapping = await service.requestJson<Record<string, unknown>>(
    input.principal,
    { method: "GET", path: INVENTORY_WAREHOUSE_MAPPINGS_PATH, service: "reader" },
    options,
  );
  const warehouseMapping = warehouseMappingRecord(parseWarehouseMappingPayload(mapping.data).rows);
  let parsed: ReturnType<typeof parseInventoryStockXlsx>;
  try {
    parsed = parseInventoryStockXlsx(input.bytes, { warehouseMapping });
  } catch {
    return reject({ ok: false, status: "rejected", message: "库存 Excel 文件解析失败，请确认文件格式和模板", warnings: [], errors: [{ code: "XLSX_PARSE_ERROR", message: "库存 Excel 文件解析失败，请确认文件格式和模板" }], errorCount: 1 });
  }
  const parseErrors = parsed.errors.map(mapIssue).slice(0, 200);
  if (parsed.rows.length === 0 && parseErrors.length === 0) parseErrors.push({ code: "NO_DATA_ROWS", message: "工作表中没有可导入的库存明细行" });
  if (parseErrors.length > 0) {
    return reject({ ok: false, status: "rejected", message: "文件校验未通过，未写入任何库存数据", warnings: [], errors: parseErrors, errorCount: parsed.errors.length || parseErrors.length });
  }
  let excludedBrushWarehouseRows = 0;
  let retainedZeroCostRows = 0;
  const qualityRows: InventoryStockRow[] = [];
  const rows: InventoryStockRow[] = [];
  for (const row of parsed.rows) {
    if (row.warehouse.trim() === "刷刷仓") excludedBrushWarehouseRows += 1;
    else {
      qualityRows.push(row);
      if (row.unitCostCents === 0) retainedZeroCostRows += 1;
      rows.push(row);
    }
  }
  const settings = await service.requestJson<{ allowNegativeInventory: boolean }>(
    input.principal,
    { method: "GET", path: INVENTORY_SETTINGS_PATH, service: "reader" },
    options,
  );
  const qualityErrors = validateInventoryImportRows(qualityRows, {
    allowNegativeInventory: settings.data.allowNegativeInventory === true,
  }).map(mapIssue);
  if (qualityErrors.length > 0) {
    return reject({ ok: false, status: "rejected", message: "库存数据质量门禁未通过，未写入任何库存数据", warnings: [], errors: qualityErrors.slice(0, 200), errorCount: qualityErrors.length });
  }
  if (rows.length === 0) {
    return reject({ ok: false, status: "rejected", message: "剔除刷刷仓后没有可导入的库存数据", warnings: excludedBrushWarehouseRows ? [{ code: "EXCLUDED_BRUSH_WAREHOUSE", message: `已识别刷刷仓 ${excludedBrushWarehouseRows} 行` }] : [], errors: [{ code: "NO_DATA_ROWS_AFTER_FILTER", message: "没有符合经营分析口径的库存明细行" }], errorCount: 1 });
  }
  const duplicateKeys = rows.map((row) => row.rowKey).filter((key, index, all) => all.indexOf(key) !== index);
  if (duplicateKeys.length > 0) {
    return reject({ ok: false, status: "rejected", message: "库存报表包含重复的仓库与货品组合，未写入任何数据", warnings: [], errors: [{ code: "DUPLICATE_INVENTORY_IDENTITY", message: `检测到 ${new Set(duplicateKeys).size} 个重复的仓库与货品组合，请合并或修正后重试` }], errorCount: new Set(duplicateKeys).size });
  }
  const rowDates = [...new Set(rows.map((row) => row.snapshotDate).filter((value): value is string => Boolean(value)))];
  if (rowDates.length > 1) {
    return reject({ ok: false, status: "rejected", message: "库存报表包含多个快照日期，不能合并为同一库存批次", warnings: [], errors: [{ code: "MIXED_SNAPSHOT_DATES", message: `检测到 ${rowDates.length} 个不同的库存日期，请按日期拆分后重新上传` }], errorCount: 1 });
  }
  const supplied = input.snapshotDateOverride?.trim();
  if (supplied && !isIsoDate(supplied)) {
    return reject({ ok: false, status: "rejected", message: "手工填写的快照日期无效", warnings: [], errors: [{ code: "INVALID_SNAPSHOT_DATE", message: "快照日期必须为 YYYY-MM-DD" }], errorCount: 1 });
  }
  const fileNameDate = dateFromFileName(fileName);
  const snapshotDate = rowDates[0] ?? fileNameDate ?? supplied;
  if (!snapshotDate) {
    return reject({ ok: false, status: "rejected", message: "无法确定库存快照日期", warnings: [], errors: [{ code: "MISSING_SNAPSHOT_DATE", message: "报表没有库存日期，请在同步时填写快照日期，或在文件名中加入日期" }], errorCount: 1 });
  }
  const missingNames = rows.filter((row) => !row.productName).length;
  const missingSuppliers = rows.filter((row) => !row.supplier).length;
  const excludedByWarehouseMapping = rows.filter((row) => !row.includeInInventory).length;
  const warnings = [
    ...(retainedZeroCostRows ? [{ code: "RETAINED_ZERO_UNIT_COST", message: `${retainedZeroCostRows} 行明确零成本库存已保留，库存金额按 0 计并参与补货计算` }] : []),
    ...(missingNames ? [{ code: "MISSING_PRODUCT_NAME", message: `${missingNames} 行缺少货品名称，页面将使用销售明细中的名称补全` }] : []),
    ...(missingSuppliers ? [{ code: "MISSING_SPEC_SUPPLIER", message: `${missingSuppliers} 行缺少规格默认供应商，页面将回退 ERP 货品档案并标明来源` }] : []),
    ...(excludedByWarehouseMapping ? [{ code: "WAREHOUSE_NOT_COUNTED_IN_OVERVIEW", message: `${excludedByWarehouseMapping} 行按仓库类型映射保留为供应商/仓别明细，但不计入总览库存和备货计划` }] : []),
    ...(!parsed.coverage.hasAvailableQuantity ? [{ code: "DERIVED_AVAILABLE_QUANTITY", message: "报表未提供可用库存，系统已按实盘数量减锁定数量推导" }] : []),
    ...(!parsed.coverage.hasLockedQuantity ? [{ code: "MISSING_LOCKED_QUANTITY", message: "报表未提供锁定数量，本批次暂按 0 处理" }] : []),
    ...(!parsed.coverage.hasInTransitQuantity ? [{ code: "MISSING_IN_TRANSIT_QUANTITY", message: "报表未提供采购在途，本批次暂按 0 处理" }] : []),
    ...(rowDates[0] && fileNameDate && rowDates[0] !== fileNameDate
      ? [{ code: "SNAPSHOT_DATE_FILENAME_MISMATCH", message: `报表日期 ${rowDates[0]} 与文件名日期 ${fileNameDate} 不一致，已采用报表日期` }]
      : []),
    ...(!rowDates[0] && !fileNameDate && supplied
      ? [{ code: "MANUAL_SNAPSHOT_DATE", message: `报表未提供库存日期，已采用手工填写的快照日期 ${supplied}` }]
      : []),
    ...(!parsed.coverage.hasSales30dQuantity ? [{ code: "MISSING_AGE_SALES", message: "报表未提供前30天销量，滞销清理将只显示库龄风险" }] : []),
    ...(excludedBrushWarehouseRows ? [{ code: "EXCLUDED_BRUSH_WAREHOUSE", message: `已剔除刷刷仓 ${excludedBrushWarehouseRows} 行，不写入经营分析数据` }] : []),
  ];
  const result = await service.requestJson<InventoryImportExecution>(input.principal, {
    method: "POST",
    path: INVENTORY_IMPORTS_PATH,
    service: "writer",
    payload: {
      dataset: "stock",
      file: { name: fileName, sizeBytes: input.fileSizeBytes, rawFileHash, sheetName: parsed.sheetName },
      snapshotDate,
      sourceRowCount: parsed.totals.sourceRowCount,
      excludedCount: excludedBrushWarehouseRows,
      rows,
      warnings,
      totals: { ...parsed.totals, ...summarize(rows, parsed.totals.sourceRowCount), coverage: parsed.coverage, excludedBrushWarehouseRows, excludedZeroCostRows: 0, retainedZeroCostRows },
    },
  }, options);
  return result.data;
}
