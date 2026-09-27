import { utils, write } from "xlsx";

export const ANNUAL_TARGET_TEMPLATE_HEADERS = [
  "店铺",
  "负责人",
  "销售目标",
  "利润目标",
  "大毛利率目标",
  "推广费目标",
] as const;

export const ANNUAL_TARGET_TEMPLATE_MAX_ROWS = 500;

export type AnnualTargetExportRow = {
  platform: string;
  shopName: string;
  manager: string;
  salesTargetCents: number;
  profitTargetCents: number;
  grossMarginBps: number;
  promotionFeeRatioBps: number;
};

export type AnnualTargetTemplateShop = {
  platform: string;
  name: string;
};

export function annualTargetShopLabel(platform: string, shopName: string): string {
  return platform && platform !== "未分组" ? `${platform}-${shopName}` : shopName;
}

const wan = (cents: number) => Math.round(cents) / 1_000_000;
const percentText = (bps: number) => `${(bps / 100).toFixed(2)}%`;

/**
 * Build the import template for annual shop targets. When `shops` is provided,
 * the 店铺 column is pre-filled with every known finance shop label so users
 * only fill in target values; rows with blank targets are skipped on import.
 */
export function annualTargetTemplateWorkbook(shops: AnnualTargetTemplateShop[] = []): Uint8Array {
  const prefill = shops
    .map((shop) => ({ platform: String(shop.platform ?? ""), name: String(shop.name ?? "") }))
    .filter((shop) => shop.name !== "")
    .slice(0, ANNUAL_TARGET_TEMPLATE_MAX_ROWS);
  const prefillLabels = Array.from(new Set(prefill.map((shop) => annualTargetShopLabel(shop.platform, shop.name))));
  const workbook = utils.book_new();
  const importSheet = utils.aoa_to_sheet([
    [...ANNUAL_TARGET_TEMPLATE_HEADERS],
    ...prefillLabels.map((label) => [label, "", "", "", "", ""]),
  ]);
  for (let row = prefillLabels.length + 2; row <= ANNUAL_TARGET_TEMPLATE_MAX_ROWS + 1; row += 1) {
    for (let column = 0; column < ANNUAL_TARGET_TEMPLATE_HEADERS.length; column += 1) {
      importSheet[`${columnReference(column)}${row}`] = { t: "s", v: "", z: "@" };
    }
  }
  importSheet["!ref"] = `A1:${columnReference(ANNUAL_TARGET_TEMPLATE_HEADERS.length - 1)}${ANNUAL_TARGET_TEMPLATE_MAX_ROWS + 1}`;
  importSheet["!cols"] = [
    { wch: 26 },
    { wch: 14 },
    { wch: 16 },
    { wch: 16 },
    { wch: 16 },
    { wch: 16 },
  ];
  importSheet["!autofilter"] = { ref: `A1:${columnReference(ANNUAL_TARGET_TEMPLATE_HEADERS.length - 1)}1` };
  utils.book_append_sheet(workbook, importSheet, "年度目标导入");

  const instructions = utils.aoa_to_sheet([
    ["填写说明", "内容"],
    ["必填字段", "店铺；其余列可按需填写，整行目标空白将跳过"],
    ["店铺", prefillLabels.length > 0
      ? `已预填当前系统内 ${prefillLabels.length} 个店铺（按“平台-店铺名”格式）；可增删行，但店铺名称必须能匹配系统店铺`
      : "填写“平台-店铺名”或财报中的店铺名称；系统导入时自动匹配平台店铺"],
    ["金额单位", "销售目标、利润目标单位为万元，例如 1415 表示 1415 万元"],
    ["比率格式", "大毛利率目标、推广费目标使用百分比，例如 45% 或 4.5%"],
    ["年份", "模板不含年份；导入时按页面上选择的目标年份写入"],
    ["更新规则", "导入只更新文件中出现的店铺；未出现的店铺原目标保持不变"],
    ["安全限制", `单次最多 ${ANNUAL_TARGET_TEMPLATE_MAX_ROWS} 行；店铺名称不能重复`],
  ]);
  instructions["!cols"] = [{ wch: 18 }, { wch: 80 }];
  utils.book_append_sheet(workbook, instructions, "填写说明");
  return write(workbook, { type: "array", bookType: "xlsx" });
}

/**
 * Build an export workbook from existing annual targets. The generated sheet
 * reuses the import template layout so the file can be edited and re-imported.
 */
export function buildAnnualTargetExportWorkbook(
  year: string,
  rows: AnnualTargetExportRow[],
): Uint8Array {
  const workbook = utils.book_new();
  const tableRows: (string | number)[][] = [
    [...ANNUAL_TARGET_TEMPLATE_HEADERS],
    ...rows.map((row) => [
      annualTargetShopLabel(row.platform, row.shopName),
      row.manager,
      row.salesTargetCents > 0 ? wan(row.salesTargetCents) : "",
      row.profitTargetCents > 0 ? wan(row.profitTargetCents) : "",
      row.grossMarginBps > 0 ? percentText(row.grossMarginBps) : "",
      row.promotionFeeRatioBps > 0 ? percentText(row.promotionFeeRatioBps) : "",
    ]),
  ];
  const exportSheet = utils.aoa_to_sheet(tableRows);
  exportSheet["!cols"] = [
    { wch: 26 },
    { wch: 14 },
    { wch: 16 },
    { wch: 16 },
    { wch: 16 },
    { wch: 16 },
  ];
  exportSheet["!autofilter"] = { ref: `A1:${columnReference(ANNUAL_TARGET_TEMPLATE_HEADERS.length - 1)}1` };
  utils.book_append_sheet(workbook, exportSheet, "年度目标导入");

  const summary = utils.aoa_to_sheet([
    ["导出信息", "内容"],
    ["目标年份", year],
    ["目标数", String(rows.length)],
    ["金额单位", "万元"],
    ["回导说明", "本文件与导入模板格式一致；修改后可在“目标进度情况”页导入到所选年份"],
  ]);
  summary["!cols"] = [{ wch: 18 }, { wch: 80 }];
  utils.book_append_sheet(workbook, summary, "导出信息");
  return write(workbook, { type: "array", bookType: "xlsx" });
}

function columnReference(index: number): string {
  let label = "";
  let value = index + 1;
  while (value > 0) {
    const remainder = (value - 1) % 26;
    label = String.fromCharCode(65 + remainder) + label;
    value = Math.floor((value - 1) / 26);
  }
  return label;
}
