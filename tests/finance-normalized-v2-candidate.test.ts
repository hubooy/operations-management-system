import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as XLSX from "xlsx";
import { prepareNormalizedFinanceImport,
  prepareNormalizedFinanceImportV2Candidate } from "../lib/finance/normalized-import";
import { parseFinanceWorkbook,
  parseFinanceWorkbookWithColumnEvidence } from "../lib/finance/parser";

const LEGACY_V1_JSON_SHA256 = "09fe2a0ce8e1e56138bbb9fb618f7ee65c287d664f3f7cd13654267d8841db36";
const RAW_BYTES_GOLDEN = new URL("../backend/finance/tests/fixtures/raw_bytes_v2_cross_group.xlsx", import.meta.url);

test("backend and SheetJS replay one fixed cross-group source with identical v2 digests", async () => {
  const bytes = new Uint8Array(readFileSync(RAW_BYTES_GOLDEN));
  assert.equal(createHash("sha256").update(bytes).digest("hex"),
    "098c31d5f64b19347baea3b33b5daa059cc7cd7aadfde8466170943a87680f54");
  const candidate = await prepareNormalizedFinanceImportV2Candidate({ bytes,
    fileName: "synthetic.xlsx", fileSizeBytes: bytes.length });
  assert.equal(candidate.disposition, "candidate_only");
  if (candidate.disposition !== "candidate_only") return;
  assert.equal(candidate.candidateDigest,
    "673f6e3ff8f91cd3a9d79b4dd034569f638f00571faf8f1d7835fb96c07ae58a");
  assert.equal(candidate.columnEvidence[0].evidenceDigest,
    "0b9f6f0021cad6b680c743995b0db96fbd1e769971cc3b729be8a731378f681d");
});

function workbookBytes() {
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([
    ["2026年8月", "测试事业部"],
    [null, null, "京东", "天猫"],
    [null, null, "同名店", "同名店"],
    ["一③实际销售金额", 300, 100, 200],
    ["金蝶科目名称"],
    ["销售费用", 30, 10, 20],
    ["销售费用_推广费", 30, 10, 20],
  ]), "26.8");
  return new Uint8Array(XLSX.write(workbook, { type: "array", bookType: "xlsx" }));
}

const input = (bytes: Uint8Array) => ({ bytes, fileName: "report.xlsx",
  fileSizeBytes: bytes.length });
const hash = (value: unknown) => createHash("sha256")
  .update(JSON.stringify(value)).digest("hex");

test("default normalized v1 JSON bytes stay at the pre-v2 golden digest", async () => {
  const bytes = workbookBytes();
  const before = await prepareNormalizedFinanceImport(input(bytes));
  assert.equal(before.disposition, "prepared");
  assert.equal(hash(before), LEGACY_V1_JSON_SHA256);
  const v2 = await prepareNormalizedFinanceImportV2Candidate(input(bytes));
  assert.equal(v2.disposition, "candidate_only");
  const after = await prepareNormalizedFinanceImport(input(bytes));
  assert.equal(JSON.stringify(after), JSON.stringify(before));
  assert.equal(hash(after), LEGACY_V1_JSON_SHA256);
  assert.equal(JSON.stringify(parseFinanceWorkbook(bytes)),
    JSON.stringify(parseFinanceWorkbookWithColumnEvidence(bytes).parsed));
});

test("explicit v2 records the two source columns before legacy same-name merge", async () => {
  const result = await prepareNormalizedFinanceImportV2Candidate(input(workbookBytes()));
  assert.equal(result.disposition, "candidate_only");
  if (result.disposition !== "candidate_only") return;
  assert.equal(result.schemaVersion, "finance-normalized-v2-candidate");
  assert.equal(result.backendImportSupported, false);
  assert.equal(result.rawFileHashVerifiedByBackend, false);
  assert.equal(result.completeWorkbookBindingVerified, false);
  assert.equal(result.financeShopMappingVerified, false);
  assert.equal(result.columnEvidence.length, 1);
  const evidence = result.columnEvidence[0];
  assert.equal(evidence.crossGroupSameNameRisk, true);
  assert.equal(evidence.collisions[0].kind, "cross_group_same_name");
  assert.equal(evidence.collisions[0].actualLegacyMergedSubjectCount, 3);
  const sourceCells = evidence.cells.filter(cell => cell.section === "summary"
    && cell.subjectName === "一③实际销售金额" && cell.columnIndex > 1);
  assert.deepEqual(sourceCells.map(cell => cell.amountCents), [10000, 20000]);
  const oldShop = result.months[0].lines.find(line => line.section === "summary"
    && line.scopeType === "shop" && line.subjectName === "一③实际销售金额");
  assert.equal(oldShop?.amountCents, 30000);
  assert.equal(result.candidateDigest, hash(Object.fromEntries(
    Object.entries(result).filter(([key]) => key !== "candidateDigest"))));
});

test("v2 candidate refuses mismatched byte length and never becomes v1 import", async () => {
  const bytes = workbookBytes();
  const bad = await prepareNormalizedFinanceImportV2Candidate({
    ...input(bytes), fileSizeBytes: bytes.length - 1 });
  assert.equal(bad.disposition, "rejected");
  assert.equal(bad.backendImportSupported, false);
  const good = await prepareNormalizedFinanceImportV2Candidate(input(bytes));
  assert.notEqual(good.schemaVersion, "finance-normalized-v1");
  assert.notEqual(good.disposition, "prepared");
});

test("v2 oversized source refuses before a raw-file digest is returned", async () => {
  const bytes = new Uint8Array(8 * 1024 * 1024 + 1);
  await assert.rejects(
    prepareNormalizedFinanceImportV2Candidate(input(bytes)),
    /超过8 MiB上限，未读取或计算文件摘要/,
  );
});
