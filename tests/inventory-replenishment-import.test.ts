import assert from "node:assert/strict";
import test from "node:test";
import * as XLSX from "xlsx";

import {
  parseReplenishmentGrid,
  parseReplenishmentWorkbook,
  replenishmentTemplateWorkbook,
  REPLENISHMENT_IMPORT_HEADERS,
} from "../lib/inventory/replenishment-workbook";

test("备货计划导入模板保留文本编码并按默认已确认解析", () => {
  const workbook = XLSX.read(replenishmentTemplateWorkbook(), { type: "array" });
  assert.deepEqual(workbook.SheetNames, ["备货计划导入", "填写说明"]);
  const sheet = workbook.Sheets["备货计划导入"];
  XLSX.utils.sheet_add_aoa(sheet, [[
    "00123", "广东仓", 18, "采购甲", "运营甲", "", "日常备货",
    "2026-09-10", "2026-09-18", 45.5, "", "是", "优先安排",
  ]], { origin: "A2" });
  const bytes = XLSX.write(workbook, { type: "array", bookType: "xlsx" }) as ArrayBuffer;

  const [row] = parseReplenishmentWorkbook(bytes);

  assert.equal(row.productCode, "00123");
  assert.equal(row.status, "confirmed");
  assert.equal(row.department, "志高项目组");
  assert.equal(row.requiresInspection, true);
  assert.equal(row.expectedConsumptionDays, 45.5);
  assert.equal(row.orderDate, "2026-09-10");
  assert.equal(row.expectedArrivalDate, "2026-09-18");
});

test("备货计划 Excel 日期单元格按原日历日期导入，不因时区提前一天", () => {
  const workbook = XLSX.utils.book_new();
  const sheet = XLSX.utils.aoa_to_sheet([
    [...REPLENISHMENT_IMPORT_HEADERS],
    ["P1", "广东仓", 10, "", "", "", "", new Date(2026, 8, 10), new Date(2026, 8, 18), "", "草稿", "", ""],
  ]);
  sheet.H2.z = "yyyy-mm-dd";
  sheet.I2.z = "yyyy-mm-dd";
  XLSX.utils.book_append_sheet(workbook, sheet, "备货计划导入");
  const bytes = XLSX.write(workbook, { type: "array", bookType: "xlsx" }) as ArrayBuffer;

  const [row] = parseReplenishmentWorkbook(bytes);

  assert.equal(row.orderDate, "2026-09-10");
  assert.equal(row.expectedArrivalDate, "2026-09-18");
});

test("备货计划导入拒绝重复规格、公式和模板外字段", () => {
  assert.throws(() => parseReplenishmentGrid([
    [...REPLENISHMENT_IMPORT_HEADERS],
    ["P1", "广东仓", 10],
    ["P1", "广东仓", 20],
  ]), /重复/);

  const workbook = XLSX.utils.book_new();
  const sheet = XLSX.utils.aoa_to_sheet([
    [...REPLENISHMENT_IMPORT_HEADERS],
    ["P1", "广东仓", 10],
  ]);
  sheet.C2 = { t: "n", f: "1+9", v: 10 };
  XLSX.utils.book_append_sheet(workbook, sheet, "备货计划导入");
  const bytes = XLSX.write(workbook, { type: "array", bookType: "xlsx" }) as ArrayBuffer;
  assert.throws(() => parseReplenishmentWorkbook(bytes), /不接受公式/);

  assert.throws(() => parseReplenishmentGrid([
    [...REPLENISHMENT_IMPORT_HEADERS],
    ["P1", "广东仓", 10, "", "", "", "", "", "", "", "", "", "", "多余"],
  ]), /模板外字段/);
});
