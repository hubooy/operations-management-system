import assert from "node:assert/strict";
import test from "node:test";
import * as XLSX from "xlsx";

import {
  ANNUAL_TARGET_TEMPLATE_HEADERS,
  annualTargetTemplateWorkbook,
  buildAnnualTargetExportWorkbook,
} from "../lib/finance/annual-target-workbook";
import { parseAnnualTargetWorkbook } from "../lib/finance/annual-target-import";

function readSheetNames(bytes: Uint8Array): string[] {
  return XLSX.read(bytes, { type: "array" }).SheetNames;
}

test("annual target template contains the sales/profit/margin/promotion headers and instructions", () => {
  const bytes = annualTargetTemplateWorkbook();
  const workbook = XLSX.read(bytes, { type: "array" });
  assert.deepEqual(workbook.SheetNames, ["年度目标导入", "填写说明"]);
  const grid = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets["年度目标导入"], { header: 1 });
  assert.deepEqual(grid[0], [...ANNUAL_TARGET_TEMPLATE_HEADERS]);
  assert.equal(
    ANNUAL_TARGET_TEMPLATE_HEADERS.join(","),
    "店铺,负责人,销售目标,利润目标,大毛利率目标,推广费目标",
  );
  const instructions = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets["填写说明"], { header: 1 });
  assert.ok(instructions.some((row) => String(row[0]).includes("金额单位")));
});

test("annual target template is blank and cannot be imported until filled", () => {
  const bytes = annualTargetTemplateWorkbook();
  assert.throws(() => parseAnnualTargetWorkbook(bytes), /没有可导入的店铺年度目标|表头/);
});

test("annual target template pre-fills shop labels and only imports rows with targets", () => {
  const bytes = annualTargetTemplateWorkbook([
    { platform: "天猫", name: "示例店" },
    { platform: "京东", name: "旗舰店" },
    { platform: "未分组", name: "自营店" },
    { platform: "", name: "空平台店" },
    { platform: "拼多多", name: "" },
    { platform: "天猫", name: "示例店" },
  ]);
  const workbook = XLSX.read(bytes, { type: "array" });
  const grid = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets["年度目标导入"], { header: 1 });
  assert.deepEqual(grid.slice(0, 5).map((row) => row[0]), [
    "店铺",
    "天猫-示例店",
    "京东-旗舰店",
    "自营店",
    "空平台店",
  ]);
  // 预填店铺但目标为空的模板仍然无法导入（整行目标空白）
  assert.throws(() => parseAnnualTargetWorkbook(bytes), /没有可导入的店铺年度目标/);
});

test("annual target template with pre-filled shops plus targets imports correctly", () => {
  const bytes = annualTargetTemplateWorkbook([{ platform: "天猫", name: "示例店" }]);
  const workbook = XLSX.read(bytes, { type: "array" });
  const sheet = workbook.Sheets["年度目标导入"];
  sheet["C2"] = { t: "n", v: 1415 };
  sheet["D2"] = { t: "n", v: 120 };
  sheet["E2"] = { t: "s", v: "45%" };
  sheet["F2"] = { t: "s", v: "5%" };
  const parsed = parseAnnualTargetWorkbook(XLSX.write(workbook, { type: "array", bookType: "xlsx" }));
  assert.equal(parsed.sourceRowCount, 1);
  assert.deepEqual(parsed.rows, [
    {
      rowNumber: 2,
      storeLabel: "天猫-示例店",
      manager: "",
      salesTargetCents: 1_415_000_000,
      profitTargetCents: 120_000_000,
      grossMarginBps: 4_500,
      promotionFeeRatioBps: 500,
    },
  ]);
});

test("annual target export workbook keeps the template layout and round-trips through the importer", () => {
  const rows = [
    {
      platform: "天猫",
      shopName: "示例店",
      manager: "张三",
      salesTargetCents: 1_415_000_000,
      profitTargetCents: 120_000_000,
      grossMarginBps: 4_500,
      promotionFeeRatioBps: 500,
    },
    {
      platform: "京东",
      shopName: "零目标店",
      manager: "李四",
      salesTargetCents: 0,
      profitTargetCents: 0,
      grossMarginBps: 0,
      promotionFeeRatioBps: 0,
    },
  ];
  const bytes = buildAnnualTargetExportWorkbook("2026", rows);
  assert.deepEqual(readSheetNames(bytes), ["年度目标导入", "导出信息"]);
  const parsed = parseAnnualTargetWorkbook(bytes);
  assert.equal(parsed.sourceRowCount, 2);
  assert.deepEqual(parsed.rows, [
    {
      rowNumber: 2,
      storeLabel: "天猫-示例店",
      manager: "张三",
      salesTargetCents: 1_415_000_000,
      profitTargetCents: 120_000_000,
      grossMarginBps: 4_500,
      promotionFeeRatioBps: 500,
    },
    {
      rowNumber: 3,
      storeLabel: "京东-零目标店",
      manager: "李四",
      salesTargetCents: 0,
      profitTargetCents: 0,
      grossMarginBps: 0,
      promotionFeeRatioBps: 0,
    },
  ]);
});
