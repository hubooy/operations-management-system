import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import * as XLSX from "xlsx";

import {
  assertJdMarketImportProof,
  claimExactJdMarketPlan,
  claimRecoverableJdMarketPlan,
  inspectJdMarketSignedCsv,
  validateJdMarketImportResponse,
  type JdMarketImportProof,
  type JdMarketPlanIdentity,
  type JdMarketRecoveryPlanShape,
} from "../lib/jd/market-ranking-import-contract";
import { parseJdMarketResumeRunIdHeader } from "../tools/tmall-sycm-cookie-pipeline";

const identity = {
  category: "商用磨粉机粉碎机",
  scope: "pop",
  rankingDimension: "SKU" as const,
  priceBandFilter: "全部",
};

function csv(rows = [
  ["2026-08-13", "2026-08-13", "商用磨粉机粉碎机", "pop", "SKU", "全部", "1", "SKU-1", "测试商品"],
]) {
  const values = [
    ["period_start", "period_end", "category", "scope", "dimension", "price_band_filter", "rank", "sku_code", "product_name"],
    ...rows,
  ];
  return new TextEncoder().encode(`\uFEFF${values.map((row) => row.join(",")).join("\r\n")}`);
}

function signed(bytes = csv(), dates = ["2026-08-13"]) {
  return inspectJdMarketSignedCsv({
    bytes,
    fileName: "京东商智_交易榜单_SKU_商用磨粉机粉碎机_2026-08-13至2026-08-13.csv",
    expectedFileSizeBytes: bytes.byteLength,
    expectedRawFileSha256: createHash("sha256").update(bytes).digest("hex"),
    dates,
    identity,
  });
}

function nativeXlsx() {
  const workbook = XLSX.utils.book_new();
  const sheet = XLSX.utils.aoa_to_sheet([
    ["排名", "SKU", "商品信息", "成交金额", "成交单量", "成交商品件数", "访客数", "浏览量", "搜索点击次数", "主图", "商品链接"],
    [1, "SKU-1", "测试商品", "¥1万~¥2万", "0~5", "0~5", "10~20", "20~30", "5~10", "https://img10.360buyimg.com/n5/test.jpg", "https://item.jd.com/SKU-1.html"],
  ]);
  XLSX.utils.book_append_sheet(workbook, sheet, "商品榜");
  return new Uint8Array(XLSX.write(workbook, { type: "buffer", bookType: "xlsx" }));
}

function responsePayload(evidence = signed(), patch: Record<string, unknown> = {}) {
  const batchId = "market-batch-1";
  return {
    ok: true,
    status: "imported",
    batch: {
      id: batchId,
      status: "completed",
      sourceType: "market_ranking",
      rowCount: evidence.rowCount,
      warningCount: 0,
      warnings: [],
      periodStart: "2026-08-13",
      periodEnd: "2026-08-13",
    },
    importReceipt: { ...evidence, batchId },
    ...patch,
  };
}

function serviceOrderedResponsePayload(evidence = signed()) {
  const payload = responsePayload(evidence);
  const receipt = payload.importReceipt;
  return {
    ...payload,
    importReceipt: {
      batchId: receipt.batchId,
      rawFileSha256: receipt.rawFileSha256,
      fileName: receipt.fileName,
      fileSizeBytes: receipt.fileSizeBytes,
      sourceType: receipt.sourceType,
      rowCount: receipt.rowCount,
      warningCount: receipt.warningCount,
      ranges: receipt.ranges,
    },
  };
}

test("JD market signed CSV is bound to exact hash, identity, dates, warnings, and row count", () => {
  const evidence = signed();
  assert.equal(evidence.rowCount, 1);
  assert.equal(evidence.warningCount, 0);
  assert.deepEqual(evidence.ranges, [{ ...identity, periodStart: "2026-08-13", periodEnd: "2026-08-13" }]);

  const wrongDate = csv([["2026-08-12", "2026-08-12", "商用磨粉机粉碎机", "pop", "SKU", "全部", "1", "SKU-1", "测试商品"]]);
  assert.throws(() => signed(wrongDate), /来源身份或目标日期/);
  const wrongIdentity = csv([["2026-08-13", "2026-08-13", "其他类目", "pop", "SKU", "全部", "1", "SKU-1", "测试商品"]]);
  assert.throws(() => signed(wrongIdentity), /来源身份或目标日期/);
  const duplicateSku = csv([
    ["2026-08-13", "2026-08-13", "商用磨粉机粉碎机", "pop", "SKU", "全部", "1", "SKU-1", "测试商品"],
    ["2026-08-13", "2026-08-13", "商用磨粉机粉碎机", "pop", "SKU", "全部", "2", "SKU-1", "重复商品"],
  ]);
  assert.throws(() => signed(duplicateSku), /解析产生警告/);
  const bytes = csv();
  assert.throws(() => inspectJdMarketSignedCsv({
    bytes,
    fileName: "signed.csv",
    expectedFileSizeBytes: bytes.byteLength,
    expectedRawFileSha256: "0".repeat(64),
    dates: ["2026-08-13"],
    identity,
  }), /大小或 SHA-256/);
});

test("JD market accepts the new native product-rank XLSX headers under the plan-bound defaults", () => {
  const bytes = nativeXlsx();
  const evidence = inspectJdMarketSignedCsv({
    bytes,
    fileName: "京东商智_行业榜单_商品榜_SKU_商用磨粉机粉碎机_2026-08-13至2026-08-13.xlsx",
    expectedFileSizeBytes: bytes.byteLength,
    expectedRawFileSha256: createHash("sha256").update(bytes).digest("hex"),
    dates: ["2026-08-13"],
    identity,
  });
  assert.equal(evidence.rowCount, 1);
  assert.equal(evidence.warningCount, 0);
  assert.deepEqual(evidence.ranges, [{ ...identity, periodStart: "2026-08-13", periodEnd: "2026-08-13" }]);
});

test("JD market import response accepts only strict 201 imported or 200 duplicate receipts", () => {
  const evidence = signed();
  const imported = validateJdMarketImportResponse(201, responsePayload(evidence), evidence);
  assert.equal(imported.resultStatus, "imported");
  assert.equal(validateJdMarketImportResponse(201, serviceOrderedResponsePayload(evidence), evidence).resultStatus, "imported");
  const duplicatePayload = responsePayload(evidence, { status: "duplicate" });
  const duplicate = validateJdMarketImportResponse(200, duplicatePayload, evidence);
  assert.equal(duplicate.resultStatus, "duplicate");

  for (const [status, payload] of [
    [200, responsePayload(evidence)],
    [201, duplicatePayload],
    [202, responsePayload(evidence)],
    [200, responsePayload(evidence, { status: "processing" })],
    [201, responsePayload(evidence, { ok: false })],
  ] as Array<[number, unknown]>) {
    assert.throws(() => validateJdMarketImportResponse(status, payload, evidence), /严格|不一致/);
  }

  assert.throws(
    () => validateJdMarketImportResponse(500, { error: "  预校验失败\n请检查文件  " }, evidence),
    /HTTP 500：预校验失败 请检查文件/,
  );
  assert.throws(
    () => validateJdMarketImportResponse(200, { ok: true, status: "processing" }, evidence),
    /HTTP 200，status=processing/,
  );

  const batch = responsePayload(evidence).batch;
  const receipt = responsePayload(evidence).importReceipt;
  const invalidPayloads = [
    responsePayload(evidence, { batch: { ...batch, id: "" } }),
    responsePayload(evidence, { batch: { ...batch, status: "processing" } }),
    responsePayload(evidence, { batch: { ...batch, warningCount: 1 } }),
    responsePayload(evidence, { batch: { ...batch, warnings: [{ message: "warning" }] } }),
    responsePayload(evidence, { batch: { ...batch, sourceType: "sku_catalog" } }),
    responsePayload(evidence, { batch: { ...batch, rowCount: 2 } }),
    responsePayload(evidence, { batch: { ...batch, periodEnd: "2026-08-12" } }),
    responsePayload(evidence, { importReceipt: { ...receipt, rawFileSha256: "0".repeat(64) } }),
    responsePayload(evidence, { importReceipt: { ...receipt, ranges: [{ ...receipt.ranges[0], category: "其他类目" }] } }),
  ];
  for (const payload of invalidPayloads) {
    assert.throws(() => validateJdMarketImportResponse(201, payload, evidence), /严格|不一致/);
  }
  const invalidProof = { ...imported, resultStatus: "not-a-real-status" } as unknown as JdMarketImportProof;
  assert.throws(() => assertJdMarketImportProof(invalidProof, evidence), /结果状态无效/);
});

function planIdentity(): JdMarketPlanIdentity {
  return {
    version: 4,
    baseUrl: "http://localhost:3000",
    silentNoWindow: true,
    storeKey: "store-key",
    shopId: "711743",
    shopName: "测试店铺",
    browserProfileName: "Profile 3",
    browserDebugPort: 9227,
    startDate: "2026-01-01",
    endDate: "2026-08-13",
    targets: [{
      key: "grinder",
      categoryPath: ["商用食品机械设备", "商用磨粉机/粉碎机"],
      identity: { ...identity, secondIndId: "44744", thirdIndId: "44811" },
    }],
  };
}

function plan(stage: JdMarketRecoveryPlanShape["stage"], importProof?: JdMarketImportProof): JdMarketRecoveryPlanShape {
  return {
    runId: "jd-market-explicit-resume",
    ...planIdentity(),
    ownerExecutionId: "old-execution",
    stage,
    targets: [{ ...planIdentity().targets[0]!, chunks: [{ importProof }] }],
  };
}

test("JD market explicit resume header and exact run ID hydrate only fully missing industry IDs", () => {
  assert.equal(parseJdMarketResumeRunIdHeader(undefined), undefined);
  assert.equal(parseJdMarketResumeRunIdHeader("jd-market-explicit-resume"), "jd-market-explicit-resume");
  assert.throws(() => parseJdMarketResumeRunIdHeader(["jd-market-explicit-resume"]), /请求头无效/);
  assert.throws(() => parseJdMarketResumeRunIdHeader("../escape"), /请求头无效/);

  const legacy = plan("failed");
  delete legacy.targets[0]!.identity.secondIndId;
  delete legacy.targets[0]!.identity.thirdIndId;
  legacy.endDate = "2026-08-12";
  const claimed = claimExactJdMarketPlan([legacy, { ...plan("failed"), runId: "unrelated-failed" }], planIdentity(), "new-execution", legacy.runId);
  assert.equal(claimed, legacy);
  assert.equal(claimed.ownerExecutionId, "new-execution");
  assert.equal(claimed.stage, "planned");
  assert.equal(claimed.targets[0]?.identity.secondIndId, "44744");
  assert.equal(claimed.targets[0]?.identity.thirdIndId, "44811");

  const partial = plan("failed");
  delete partial.targets[0]!.identity.thirdIndId;
  assert.throws(() => claimExactJdMarketPlan([partial], planIdentity(), "new-execution", partial.runId), /仅部分存在/);

  const emptyId = plan("failed");
  emptyId.targets[0]!.identity.secondIndId = "";
  emptyId.targets[0]!.identity.thirdIndId = "";
  assert.throws(() => claimExactJdMarketPlan([emptyId], planIdentity(), "new-execution", emptyId.runId), /行业 ID 与当前受控配置不一致/);
  const wrongId = plan("failed");
  wrongId.targets[0]!.identity.thirdIndId = "99999";
  assert.throws(() => claimExactJdMarketPlan([wrongId], planIdentity(), "new-execution", wrongId.runId), /行业 ID 与当前受控配置不一致/);
  const future = plan("failed");
  future.endDate = "2026-08-14";
  assert.throws(() => claimExactJdMarketPlan([future], { ...planIdentity(), endDate: "2026-08-13" }, "new-execution", future.runId), /日期或隐藏模式身份不一致/);
  assert.throws(() => claimExactJdMarketPlan([plan("running")], planIdentity(), "new-execution", "jd-market-explicit-resume"), /running/);
  assert.throws(() => claimExactJdMarketPlan([plan("completed")], planIdentity(), "new-execution", "jd-market-explicit-resume"), /completed/);
  const otherRunning = { ...plan("running"), runId: "other-running" };
  assert.throws(() => claimExactJdMarketPlan([plan("failed"), otherRunning], planIdentity(), "new-execution", "jd-market-explicit-resume"), /其他 running/);
  assert.throws(() => claimExactJdMarketPlan([], planIdentity(), "new-execution", "missing-run"), /不存在/);
});

test("JD market failed plan is claimed by exact new execution while running or ambiguous plans fail closed", () => {
  const failed = plan("failed");
  failed.endDate = "2026-08-12";
  const claimed = claimRecoverableJdMarketPlan([failed], planIdentity(), "new-execution");
  assert.equal(claimed, failed);
  assert.equal(claimed?.ownerExecutionId, "new-execution");
  assert.equal(claimed?.stage, "planned");

  const proof = validateJdMarketImportResponse(201, responsePayload(), signed());
  const executed = claimRecoverableJdMarketPlan([plan("failed", proof)], planIdentity(), "new-execution");
  assert.equal(executed?.stage, "executed");

  assert.throws(() => claimRecoverableJdMarketPlan([plan("running")], planIdentity(), "new-execution"), /running/);
  assert.throws(() => claimRecoverableJdMarketPlan([plan("failed"), plan("planned")], planIdentity(), "new-execution"), /多个未闭环/);
  assert.equal(claimRecoverableJdMarketPlan([plan("completed")], planIdentity(), "new-execution"), null);
  const doneA = { ...plan("completed"), ownerExecutionId: "same-execution", runId: "done-a" };
  const doneB = { ...plan("completed"), ownerExecutionId: "same-execution", runId: "done-b" };
  assert.throws(() => claimRecoverableJdMarketPlan([doneA, doneB], planIdentity(), "same-execution"), /多个已完成/);
});

test("JD market runner retries signed evidence before any Chromium launch and C requires strict proof", async () => {
  const [runner, importService] = await Promise.all([
    readFile(new URL("../tools/jd-market-ranking-daily.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/import-service.ts", import.meta.url), "utf8"),
  ]);
  const recovery = runner.indexOf("const { bytes, evidence } = await inspectSignedChunk");
  const launch = runner.indexOf("const launched = await launchDedicatedChrome");
  assert.ok(recovery >= 0 && launch > recovery);
  assert.match(runner.slice(recovery, launch), /importRankingFile[\s\S]*chunk\.importProof = proof/);
  assert.match(runner, /assertJdMarketImportProof\(chunk\.importProof, evidence\)/);
  assert.match(runner, /targetPlan\.missingDates\.filter/);
  assert.doesNotMatch(runner, /if \(chunk\.batchId\) continue/);
  assert.match(importService, /const importReceipt = \(batchId: string\)/);
  assert.equal(importService.match(/importReceipt: importReceipt\(/g)?.length, 2);
});
