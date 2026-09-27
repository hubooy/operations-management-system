import * as XLSX from "xlsx";

import {
  inventoryWarehouseCategoryLabels,
  type InventoryWarehouseCategory,
} from "@/lib/inventory/warehouse-classification";
import type { WarehouseMappingRow } from "@/lib/inventory/warehouse-mapping-contract";


const HEADERS = ["仓库", "自定义仓库类型", "计入库存", "确认状态", "备注"] as const;
const MAX_ROWS = 2_000;
const categoryByLabel = new Map(
  Object.entries(inventoryWarehouseCategoryLabels)
    .map(([category, label]) => [label, category as WarehouseMappingRow["category"]]),
);

export type WarehouseMappingWorkbookRow = Pick<
  WarehouseMappingRow,
  "warehouse" | "category" | "includeInInventory"
>;

export class WarehouseMappingWorkbookError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WarehouseMappingWorkbookError";
  }
}

function text(value: unknown) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function included(value: unknown, rowNumber: number) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && (value === 0 || value === 1)) return value === 1;
  const normalized = text(value).toLocaleLowerCase("zh-CN");
  if (["是", "计入", "true", "yes", "y", "1"].includes(normalized)) return true;
  if (["否", "不计入", "排除", "false", "no", "n", "0"].includes(normalized)) return false;
  throw new WarehouseMappingWorkbookError(`第 ${rowNumber} 行“计入库存”必须填写“是”或“否”`);
}

export function parseWarehouseMappingWorkbook(input: ArrayBuffer | Uint8Array): WarehouseMappingWorkbookRow[] {
  let workbook: XLSX.WorkBook;
  try {
    workbook = XLSX.read(input, { type: "array", cellDates: false, dense: false });
  } catch {
    throw new WarehouseMappingWorkbookError("无法读取 Excel 文件，请使用系统导出的 .xlsx 模板");
  }
  const first = workbook.SheetNames[0];
  const sheet = first ? workbook.Sheets[first] : undefined;
  if (!sheet) throw new WarehouseMappingWorkbookError("Excel 文件没有可读取的工作表");
  const rows = XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1, defval: "", raw: true });
  const header = (rows[0] ?? []).map(text);
  const indexes = Object.fromEntries(HEADERS.map((name) => [name, header.indexOf(name)])) as Record<(typeof HEADERS)[number], number>;
  for (const required of HEADERS.slice(0, 3)) {
    if (indexes[required] < 0) throw new WarehouseMappingWorkbookError(`Excel 缺少表头“${required}”`);
  }
  const dataRows = rows.slice(1).filter((row) => row.some((cell) => text(cell) !== ""));
  if (!dataRows.length) throw new WarehouseMappingWorkbookError("Excel 中没有仓库映射数据");
  if (dataRows.length > MAX_ROWS) throw new WarehouseMappingWorkbookError(`仓库映射不能超过 ${MAX_ROWS} 行`);
  const seen = new Set<string>();
  return dataRows.map((row, index) => {
    const rowNumber = index + 2;
    const warehouse = text(row[indexes["仓库"]]);
    const categoryValue = text(row[indexes["自定义仓库类型"]]);
    const category = categoryByLabel.get(categoryValue)
      ?? (Object.hasOwn(inventoryWarehouseCategoryLabels, categoryValue)
        ? categoryValue as InventoryWarehouseCategory
        : undefined);
    if (!warehouse || warehouse.length > 240 || /[\x00-\x1f\x7f]/.test(warehouse)) {
      throw new WarehouseMappingWorkbookError(`第 ${rowNumber} 行仓库名称无效`);
    }
    if (seen.has(warehouse)) throw new WarehouseMappingWorkbookError(`第 ${rowNumber} 行仓库“${warehouse}”重复`);
    if (!category) throw new WarehouseMappingWorkbookError(`第 ${rowNumber} 行仓库类型“${categoryValue || "空"}”无效`);
    const includeInInventory = included(row[indexes["计入库存"]], rowNumber);
    if (warehouse === "刷刷仓" && includeInInventory) {
      throw new WarehouseMappingWorkbookError("刷刷仓是固定业务排除仓，不能设置为计入库存");
    }
    seen.add(warehouse);
    return { warehouse, category, includeInInventory };
  });
}

export function buildWarehouseMappingWorkbook(rows: readonly WarehouseMappingRow[]): Uint8Array {
  const data = [
    [...HEADERS],
    ...rows.map((row) => [
      row.warehouse,
      row.label,
      row.includeInInventory ? "是" : "否",
      row.pendingConfirmation ? "待确认" : "已确认",
      "",
    ]),
  ];
  const sheet = XLSX.utils.aoa_to_sheet(data);
  sheet["!cols"] = [{ wch: 42 }, { wch: 18 }, { wch: 14 }, { wch: 14 }, { wch: 30 }];
  sheet["!autofilter"] = { ref: `A1:D${data.length}` };
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, sheet, "mapping");
  return XLSX.write(workbook, { type: "array", bookType: "xlsx", compression: true }) as Uint8Array;
}
