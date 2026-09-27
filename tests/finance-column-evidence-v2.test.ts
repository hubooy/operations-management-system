import assert from "node:assert/strict";
import test from "node:test";
import { extractFinanceColumnEvidenceV2, type FinanceColumnEvidenceInput } from "../lib/finance/column-evidence-v2";
import type { FinanceLineInput } from "../lib/finance/types";

const month = "2026-08";
const sheetName = "26.8";

function line(column: 1 | 2 | 3, rowIndex: number, amount: number,
  subjectName = "一③实际销售金额"): FinanceLineInput {
  const business = column === 1;
  const groupName = column === 2 ? "京东" : column === 3 ? "天猫" : "";
  return { month, section: "summary", metricKey: "net_sales", subjectName,
    scopeKey: business ? "business:测试事业部" : "shop:同名店",
    scopeType: business ? "business" : "shop",
    scopeName: business ? "测试事业部" : "同名店", groupName,
    valueType: "amount", amountCents: amount, rateBps: null,
    rawValue: String(amount / 100), sourceRowCount: 1,
    sortOrder: rowIndex + 1, isTotal: false };
}

function fixture(): FinanceColumnEvidenceInput {
  return { month, sheetName,
    dimensions: [
      { columnIndex: 1, scopeKey: "business:测试事业部", scopeType: "business",
        scopeName: "测试事业部", groupName: "" },
      { columnIndex: 2, scopeKey: "shop:同名店", scopeType: "shop",
        scopeName: "同名店", groupName: "京东" },
      { columnIndex: 3, scopeKey: "shop:同名店", scopeType: "shop",
        scopeName: "同名店", groupName: "天猫" },
    ],
    headerCells: [
      { columnIndex: 1, rawGroupCell: null, rawShopCell: null },
      { columnIndex: 2, rawGroupCell: "京 东", rawShopCell: "同 名 店" },
      { columnIndex: 3, rawGroupCell: "天猫", rawShopCell: "同名店" },
    ],
    rawLines: [line(1, 3, 30000), line(2, 3, 10000), line(3, 3, 20000)],
    origins: [{ rowIndex: 3, columnIndex: 1 }, { rowIndex: 3, columnIndex: 2 },
      { rowIndex: 3, columnIndex: 3 }],
  };
}

test("pre-aggregation cells preserve cross-group same-name values and origin", async () => {
  const result = await extractFinanceColumnEvidenceV2(fixture());
  assert.equal(result.cellCount, 3);
  assert.deepEqual(result.cells.map(cell => [cell.rowIndex, cell.columnIndex,
    cell.amountCents]), [[3, 1, 30000], [3, 2, 10000], [3, 3, 20000]]);
  assert.equal(result.columns[1].rawGroupCell, "京 东");
  assert.equal(result.collisions[0].kind, "cross_group_same_name");
  assert.deepEqual(result.collisions[0].columnIndices, [2, 3]);
  assert.equal(result.collisions[0].actualLegacyMergedSubjectCount, 1);
  assert.equal(result.crossGroupSameNameRisk, true);
  assert.equal(result.sourceWorkbookBytesVerified, false);
  assert.equal(result.financeShopMappingVerified, false);
  assert.match(result.evidenceDigest, /^[0-9a-f]{64}$/);
});

test("digest is deterministic for equivalent source-coordinate order", async () => {
  const first = fixture();
  const second = fixture();
  second.rawLines = [...second.rawLines].reverse();
  second.origins = [...second.origins].reverse();
  second.dimensions = [...second.dimensions].reverse();
  second.headerCells = [...second.headerCells].reverse();
  const a = await extractFinanceColumnEvidenceV2(first);
  const b = await extractFinanceColumnEvidenceV2(second);
  assert.equal(a.evidenceDigest, b.evidenceDigest);
  second.rawLines = second.rawLines.map((item, index) =>
    index === 0 ? { ...item, amountCents: 20001 } : item);
  const changed = await extractFinanceColumnEvidenceV2(second);
  assert.notEqual(changed.evidenceDigest, a.evidenceDigest);
});

test("same-group duplicate columns remain a separate ambiguity", async () => {
  const input = fixture();
  input.dimensions = input.dimensions.map((item, index) =>
    index === 2 ? { ...item, groupName: "京东" } : item);
  input.headerCells = input.headerCells.map((item, index) =>
    index === 2 ? { ...item, rawGroupCell: "京东" } : item);
  input.rawLines = input.rawLines.map((item, index) =>
    index === 2 ? { ...item, groupName: "京东" } : item);
  const result = await extractFinanceColumnEvidenceV2(input);
  assert.equal(result.collisions[0].kind, "duplicate_shop_columns");
  assert.equal(result.crossGroupSameNameRisk, false);
});

test("blank middle shop column still carries a source group header", async () => {
  const input = fixture();
  input.dimensions = [input.dimensions[0], { ...input.dimensions[1], columnIndex: 3 }];
  input.headerCells = [input.headerCells[0],
    { columnIndex: 2, rawGroupCell: "京东", rawShopCell: null },
    { columnIndex: 3, rawGroupCell: null, rawShopCell: "同名店" }];
  input.rawLines = [input.rawLines[0], input.rawLines[1]];
  input.origins = [input.origins[0], { rowIndex: 3, columnIndex: 3 }];
  const result = await extractFinanceColumnEvidenceV2(input);
  assert.equal(result.headerCells[1].rawShopCell, null);
  assert.equal(result.columns[1].groupName, "京东");
  input.headerCells = [input.headerCells[0],
    { columnIndex: 2, rawGroupCell: "天猫", rawShopCell: null },
    input.headerCells[2]];
  await assert.rejects(extractFinanceColumnEvidenceV2(input));
});

test("origin and header mismatch or duplicate cells fail before a digest", async () => {
  const wrongColumn = fixture();
  wrongColumn.origins = wrongColumn.origins.map((item, index) =>
    index === 1 ? { rowIndex: 3, columnIndex: 3 } : item);
  await assert.rejects(extractFinanceColumnEvidenceV2(wrongColumn));
  const wrongRow = fixture();
  wrongRow.origins = wrongRow.origins.map((item, index) =>
    index === 1 ? { rowIndex: 4, columnIndex: 2 } : item);
  await assert.rejects(extractFinanceColumnEvidenceV2(wrongRow));
  const wrongHeader = fixture();
  wrongHeader.headerCells = wrongHeader.headerCells.map((item, index) =>
    index === 1 ? { ...item, rawShopCell: "另一店" } : item);
  await assert.rejects(extractFinanceColumnEvidenceV2(wrongHeader));
  const repeated = fixture();
  repeated.rawLines = [...repeated.rawLines, repeated.rawLines[1]];
  repeated.origins = [...repeated.origins, repeated.origins[1]];
  await assert.rejects(extractFinanceColumnEvidenceV2(repeated));
});

test("bounded column and cell inputs refuse truncation", async () => {
  const input = fixture();
  input.rawLines = Array.from({ length: 100_001 }, () => input.rawLines[1]);
  input.origins = Array.from({ length: 100_001 }, () => input.origins[1]);
  await assert.rejects(extractFinanceColumnEvidenceV2(input));
  const wide = fixture();
  wide.dimensions = Array.from({ length: 501 }, (_, index) => ({
    ...wide.dimensions[1], columnIndex: index + 1 }));
  wide.headerCells = Array.from({ length: 501 }, (_, index) => ({
    ...wide.headerCells[1], columnIndex: index + 1 }));
  await assert.rejects(extractFinanceColumnEvidenceV2(wide));
});
