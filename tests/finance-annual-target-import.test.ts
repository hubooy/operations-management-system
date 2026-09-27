import assert from "node:assert/strict";
import test from "node:test";
import * as XLSX from "xlsx";

import { parseAnnualTargetWorkbook } from "../lib/finance/annual-target-import";

function workbookBytes(rows: unknown[][]) {
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(rows), "年度目标");
  return XLSX.write(workbook, { type: "buffer", bookType: "xlsx" }) as Uint8Array;
}

function workbookWithNamedSecondSheet(firstRows: unknown[][]) {
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(firstRows), "年度目标");
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([["非目标数据"]]), "SheetTitle");
  return XLSX.write(workbook, { type: "buffer", bookType: "xlsx" }) as Uint8Array;
}

test("annual target workbook maps the supplied 万元 and rate columns without importing blank stores", () => {
  const bytes = workbookBytes([
    [], [], [],
    [null, "店铺", "负责人", "利润目标", "大毛利率目标", "推广费目标", "调整目标"],
    [null, "天猫-示例店", "张三", 120, 0.45, "5%", 1415],
    [null, "天猫-空白店", null, null, null, null, null],
    [null, "京东-零目标店", "李四", null, 46, 0.055, 0],
  ]);
  const parsed = parseAnnualTargetWorkbook(bytes);
  assert.equal(parsed.sheetName, "年度目标");
  assert.equal(parsed.headerRowNumber, 4);
  assert.equal(parsed.sourceRowCount, 3);
  assert.equal(parsed.skippedRowCount, 1);
  assert.deepEqual(parsed.rows, [
    {
      rowNumber: 5,
      storeLabel: "天猫-示例店",
      manager: "张三",
      salesTargetCents: 1_415_000_000,
      profitTargetCents: 120_000_000,
      grossMarginBps: 4_500,
      promotionFeeRatioBps: 500,
    },
    {
      rowNumber: 7,
      storeLabel: "京东-零目标店",
      manager: "李四",
      salesTargetCents: 0,
      profitTargetCents: 0,
      grossMarginBps: 4_600,
      promotionFeeRatioBps: 550,
    },
  ]);
});

test("annual target workbook rejects duplicate stores and incomplete headers", () => {
  assert.throws(() => parseAnnualTargetWorkbook(workbookBytes([
    ["店铺", "负责人", "利润目标", "大毛利率目标", "推广费目标", "调整目标"],
    ["天猫-示例店", "甲", 1, 0.4, 0.05, 10],
    ["天猫-示例店", "乙", 2, 0.4, 0.05, 20],
  ])), /店铺与第 2 行重复/);
  assert.throws(() => parseAnnualTargetWorkbook(workbookBytes([["店铺", "负责人"]])), /未找到完整表头/);
});

test("annual target workbook always uses workbook order rather than a specially named later sheet", () => {
  const parsed = parseAnnualTargetWorkbook(workbookWithNamedSecondSheet([
    ["店铺", "负责人", "利润目标", "大毛利率目标", "推广费目标", "调整目标"],
    ["天猫-示例店", "负责人", 10, 0.4, 0.05, 100],
  ]));
  assert.equal(parsed.sheetName, "年度目标");
  assert.equal(parsed.rows.length, 1);
});

test("annual target workbook accepts the new 销售目标 header exported by the template", () => {
  const parsed = parseAnnualTargetWorkbook(workbookBytes([
    ["店铺", "负责人", "销售目标", "利润目标", "大毛利率目标", "推广费目标"],
    ["天猫-示例店", "张三", 1415, 120, "45%", "5%"],
  ]));
  assert.equal(parsed.rows.length, 1);
  assert.deepEqual(parsed.rows[0], {
    rowNumber: 2,
    storeLabel: "天猫-示例店",
    manager: "张三",
    salesTargetCents: 1_415_000_000,
    profitTargetCents: 120_000_000,
    grossMarginBps: 4_500,
    promotionFeeRatioBps: 500,
  });
});
