import assert from "node:assert/strict";
import test from "node:test";
import * as XLSX from "xlsx";
import { prepareNormalizedFinanceImport } from "../lib/finance/normalized-import";

for (const bookType of ["xlsx", "biff8"] as const) {
  test(`finance ${bookType} emits exact writer fields and preserves source amounts`, async () => {
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([
      ["2026年8月", "测试事业部"],
      [null, null, "测试组"],
      [null, null, "测试店"],
      ["一③实际销售金额", 100.25, 80.25],
      ["金蝶科目名称"],
      ["销售费用", 12.34, 10.12],
      ["销售费用_推广费", 12.34, 10.12],
    ]), "26.8");
    const bytes = new Uint8Array(XLSX.write(workbook, { type: "array", bookType }));
    const parsed = await prepareNormalizedFinanceImport({ bytes, fileName: `report.${bookType === "xlsx" ? "xlsx" : "xls"}`, fileSizeBytes: bytes.length });
    assert.equal(parsed.disposition, "prepared");
    assert.deepEqual(parsed.warnings, []);
    assert.deepEqual(parsed.months?.map(m => m.month), ["2026-08"]);
    const lines = parsed.months![0].lines;
    const expected = ["month", "section", "metricKey", "subjectName", "scopeKey", "scopeType", "scopeName", "groupName", "valueType", "amountCents", "rateBps", "rawValue", "sourceRowCount", "sortOrder", "isTotal"].sort();
    for (const line of lines) assert.deepEqual(Object.keys(line).sort(), expected);
    assert.equal(lines.find(l => l.metricKey === "net_sales" && l.scopeType === "business")?.amountCents, 10025);
    assert.equal(lines.find(l => l.subjectName === "销售费用_推广费" && l.scopeType === "shop")?.amountCents, 1012);
  });
}
