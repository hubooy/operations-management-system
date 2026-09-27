import * as XLSX from "xlsx";
import { unzipSync } from "fflate";
import type { GuangdongMonitor, GuangdongWatchRow } from "./guangdong-contract";

export const WATCH_HEADERS = ["货品编码", "监控状态", "备注"];
const MAX_ROWS = 5000;

export function parseWatchGrid(grid: unknown[][]): GuangdongWatchRow[] {
  const rows = grid.filter((row) => row.some((cell) => cell !== null && cell !== undefined && String(cell).trim() !== ""));
  if (rows.length < 2 || rows.length > MAX_ROWS + 1) throw new Error("请提供表头及1–5000行数据");
  const headers = rows[0].map((value) => String(value ?? "").trim());
  if (headers.length !== 3 || headers.some((value, index) => value !== WATCH_HEADERS[index])) throw new Error("表头必须依次为：货品编码、监控状态、备注");
  return rows.slice(1).map((row, index) => {
    if (row.length > 3 && row.slice(3).some((cell) => String(cell ?? "").trim())) throw new Error(`第${index + 2}行包含多余字段`);
    const code = row[0];
    if (typeof code !== "string" || !code.trim()) throw new Error(`第${index + 2}行货品编码必须为文本；请使用模板以保留前导零`);
    const state = String(row[1] ?? "").trim();
    if (!["", "启用", "暂停"].includes(state)) throw new Error(`第${index + 2}行监控状态须为启用或暂停`);
    return { productCode: code.trim(), active: state !== "暂停", notes: String(row[2] ?? "").trim() };
  });
}

export function parseWatchWorkbook(bytes: ArrayBuffer): GuangdongWatchRow[] {
  if (bytes.byteLength > 4 * 1024 * 1024) throw new Error("工作簿不能超过4MiB");
  let expanded = 0; let entries = 0;
  const parts = unzipSync(new Uint8Array(bytes), { filter(file) {
    entries++; expanded += file.originalSize;
    if (entries > 100 || expanded > 16 * 1024 * 1024 || file.originalSize > 8 * 1024 * 1024) throw new Error("工作簿解压内容超过安全上限");
    return true;
  } });
  if (Object.values(parts).reduce((sum, part) => sum + part.byteLength, 0) > 16 * 1024 * 1024) throw new Error("工作簿解压内容超过安全上限");
  const book = XLSX.read(bytes, { type: "array", cellFormula: true, sheetRows: MAX_ROWS + 2 });
  if (book.SheetNames.length !== 1) throw new Error("请上传仅包含一个工作表的清单模板");
  const sheet = book.Sheets[book.SheetNames[0]];
  const range = XLSX.utils.decode_range(sheet["!fullref"] || sheet["!ref"] || "A1");
  if (range.e.r > MAX_ROWS || range.e.c > 2) throw new Error("清单超过5000行或包含模板外的列");
  for (const [key, cell] of Object.entries(sheet)) {
    if (!key.startsWith("!") && cell && typeof cell === "object" && "f" in cell) throw new Error("监控清单不接受公式，请粘贴为文本值");
  }
  return parseWatchGrid(XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1, defval: "", raw: true }));
}

export function parseWatchPaste(text: string): GuangdongWatchRow[] {
  const lines = text.replace(/^\uFEFF/, "").replace(/\r/g, "").trim().split("\n").map((line) => line.split("\t"));
  if (lines[0]?.[0]?.trim() !== "货品编码") lines.unshift(WATCH_HEADERS);
  return parseWatchGrid(lines);
}

export function workbookBytes(sheets: Array<{ name: string; rows: Array<Array<string | number | boolean | null>> }>): Uint8Array {
  const book = XLSX.utils.book_new();
  for (const { name, rows } of sheets) {
    const sheet = XLSX.utils.aoa_to_sheet(rows);
    // All strings, including leading =/+/-/@, remain explicit string cells, never formulas.
    for (const [key, cell] of Object.entries(sheet)) {
      if (!key.startsWith("!") && cell && typeof cell === "object" && "v" in cell && typeof cell.v === "string") {
        cell.t = "s"; delete cell.f;
      }
    }
    sheet["!cols"] = (rows[0] ?? []).map((_, index) => ({ wch: index === 0 ? 24 : 20 }));
    if (rows.length > 1) sheet["!autofilter"] = { ref: sheet["!ref"]! };
    XLSX.utils.book_append_sheet(book, sheet, name);
  }
  return XLSX.write(book, { type: "array", bookType: "xlsx" });
}

export function watchWorkbook(rows: GuangdongWatchRow[]): Uint8Array {
  const bytes = workbookBytes([{ name: "广东监控清单", rows: [WATCH_HEADERS, ...rows.map((row) => [row.productCode, row.active ? "启用" : "暂停", row.notes])] }]);
  if (rows.length) return bytes;
  const book = XLSX.read(bytes, { type: "array" });
  const sheet = book.Sheets[book.SheetNames[0]];
  for (let row = 2; row <= MAX_ROWS + 1; row++) sheet[`A${row}`] = { t: "s", v: "", z: "@" };
  sheet["!ref"] = `A1:C${MAX_ROWS + 1}`;
  return XLSX.write(book, { type: "array", bookType: "xlsx" });
}

export function monitorWorkbook(data: GuangdongMonitor): Uint8Array {
  return workbookBytes([
    { name: "广东入仓监控", rows: [
      ["货品编码", "货品名称", "规格编码", "规格", "品牌", "品类", "供应商", "供应商来源", "运营负责人", "采购负责人", "仓库", "可用库存", "在途", "7日出库", "15日出库", "30日出库", "销售周转天数", "库龄天数", "生产周期天", "安全天数", "最晚下单日期", "备货数量", "下单剩余库存", "下单后累计增库", "核算说明", "最新下单日期", "风险", "风险原因", "备注"],
      ...data.items.map((r) => [r.productCode, r.productName, r.productCode, r.specification, r.brand, r.category, r.supplier, r.supplierSource, r.operatorName, r.buyer, r.warehouse, r.availableQuantity, r.inTransitQuantity, r.outbound7dQuantity, r.outbound15dQuantity, r.outbound30dQuantity, r.turnoverDays, r.inventoryAgeDays, r.leadDays, r.bufferDays, r.latestOrderDate, r.replenishmentQuantity, r.replenishmentRemainingQuantity, r.replenishmentStockIncreaseQuantity, r.replenishmentRemainingReason, r.latestReplenishmentOrderDate, r.riskLabel, r.riskReasons.join("；"), r.notes]),
    ] },
    { name: "统计说明", rows: [["项目", "内容"], ["库存截止", data.sync.inventoryAsOf], ["库龄截止", data.sync.inventoryAgeAsOf], ["销售截止", data.sync.salesThrough], ["数据版本", data.version], ["缺成本型号数", data.metrics.missingCostCount], ["缺库存型号数", data.metrics.missingStockCount], ...data.disclosures.map((text) => ["口径", text])] },
    { name: "健康分布", rows: [["风险", "型号数", "型号占比", "正向可用库存", "数量占比", "已覆盖货值元", "货值占比"], ...data.distribution.map((row) => [row.label, row.itemCount, row.itemRate, row.quantity, row.quantityRate, row.knownStockValueCents / 100, row.valueRate])] },
  ]);
}
