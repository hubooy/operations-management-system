import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";
import { parseXlsxFirstSheet, type XlsxCellValue } from "../lib/imports/xlsx";
import {
  SalesImportError,
  assertExactSalesSourceRowCount,
  assertSalesPostImportVerification,
  findMissingPreviouslyLoadedChannels,
  runSalesImport,
  type SalesPostImportVerification,
} from "../tools/sales-import-runner";

const execFileAsync = promisify(execFile);

function sha256(bytes: Uint8Array) {
  return createHash("sha256").update(bytes).digest("hex");
}

function salesSheet(rows: XlsxCellValue[][]) {
  return createXlsxWorkbookBytes([{ name: "sheetTitle", rows }]);
}

function costSheet(rows: XlsxCellValue[][]) {
  return createXlsxWorkbookBytes([{ name: "sheetTitle", rows }]);
}

test("sales completeness guard detects a previously loaded whitelist channel missing from the new snapshot", () => {
  const result = findMissingPreviouslyLoadedChannels(
    ["炊之王淘宝企业店", "天猫-志高丽力专卖店"],
    new Map([["天猫-志高丽力专卖店", 10]]),
    [
      { channel: "炊之王淘宝企业店", platform: "淘宝", shopName: "炊之王淘宝企业店", rowCount: 22, netSalesCents: 317_400 },
      { channel: "白名单外店铺", platform: "其他", shopName: "白名单外店铺", rowCount: 5, netSalesCents: 10_000 },
    ],
  );
  assert.deepEqual(result, [{ channel: "炊之王淘宝企业店", rowCount: 22, netSalesCents: 317_400 }]);
});

test("sales source row count contract is exact and fails closed when absent or ambiguous", () => {
  assert.deepEqual(assertExactSalesSourceRowCount(2, 2), {
    semantic: "xlsx_nonblank_data_rows",
    expected: 2,
    actual: 2,
    verified: true,
  });
  assert.throws(() => assertExactSalesSourceRowCount(undefined, 2), (error) => (
    error instanceof SalesImportError
      && error.failureCode === "FILE_VALIDATION_FAILED"
      && error.stage === "validate_source_row_count"
  ));
  assert.throws(() => assertExactSalesSourceRowCount(1, 2), /行数.*不一致/);
  assert.throws(() => assertExactSalesSourceRowCount(3, 2), /行数.*不一致/);
});

test("sales post-import verification is fail-closed on wrong batches, rows, scope, or policy", () => {
  const verification: SalesPostImportVerification = {
    policyVersion: "sales-v5",
    period: { startDate: "2026-08-01", endDate: "2026-08-05", endExclusive: "2026-08-06" },
    batch: { id: "actual-batch", status: "completed", rowCount: 3808 },
    stats: {
      rowCount: 3808,
      minShipTime: "2026-08-01T00:01:00+08:00",
      maxShipTime: "2026-08-05T23:59:00+08:00",
      excludedWarehouseRows: 0,
      rowsNotOwnedByBatch: 0,
    },
    nonWhitelistChannels: [],
  };
  const input = {
    responseOk: true,
    expectedPolicyVersion: "sales-v5",
    period: {
      startDate: "2026-08-01",
      endDate: "2026-08-05",
      endExclusiveDateTime: "2026-08-06T00:00:00+08:00",
    },
    expectedBatch: { id: "actual-batch", status: "completed", rowCount: 3808 },
    expectedRowCount: 3808,
    verification,
  };
  assert.doesNotThrow(() => assertSalesPostImportVerification(input));
  assert.throws(() => assertSalesPostImportVerification({ ...input, responseOk: false }), /回查请求失败/);
  assert.throws(() => assertSalesPostImportVerification({ ...input, verification: null }), /回查请求失败/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, batch: { ...verification.batch!, id: "processed-file-hash" } },
  }), /精确批次/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, batch: null },
  }), /精确批次/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, batch: { ...verification.batch!, status: "processing" } },
  }), /批次未完成/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, stats: { ...verification.stats!, rowCount: 3807 } },
  }), /期间落库行数不一致/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, stats: { ...verification.stats!, excludedWarehouseRows: 1 } },
  }), /排除仓残留/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, stats: { ...verification.stats!, rowsNotOwnedByBatch: 1 } },
  }), /不属于本轮批次/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, nonWhitelistChannels: ["未知渠道"] },
  }), /非白名单渠道/);
  assert.throws(() => assertSalesPostImportVerification({
    ...input,
    verification: { ...verification, policyVersion: "stale-policy" },
  }), /策略版本不一致/);
});

test("sales dry-run uses the stable price-adjustment product code", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-price-adjustment-test-"));
  try {
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-PRICE", "天猫-志高丽力专卖店", "主仓", "", "补差价专用", 1, "2026-07-10 09:00:00", "2026-07-10 10:00:00", 0, 20, 20, 0, 0],
    ]);
    const costBytes = costSheet([["货品编号", "固定成本价", "货品名称"]]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓.xlsx");
    await Promise.all([writeFile(salesPath, salesBytes), writeFile(costPath, costBytes)]);

    const result = await execFileAsync(process.execPath, [
      "--import", "tsx", path.resolve("tools/sales-import-runner.ts"),
      "--download", salesPath,
      "--cost-source", costPath,
      "--expected-source-rows", "1",
      "--as-of", "2026-07-15",
      "--audit-root", path.join(directory, "audit"),
      "--dry-run",
    ], { cwd: path.resolve("."), encoding: "utf8", timeout: 30_000 });

    const output = JSON.parse(result.stdout) as { outputPath?: string };
    assert.ok(output.outputPath);
    const workbook = parseXlsxFirstSheet(new Uint8Array(await readFile(output.outputPath)));
    assert.equal(workbook.rows[1]?.cells[3], "ERP_PRICE_ADJUSTMENT");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales dry-run --use-file-cost keeps the workbook cost column as final cost", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-file-cost-test-"));
  try {
    // 文件自带货品成本列（整行成本）为 50，库存成本源固定成本价为 20。
    // --use-file-cost 应以文件 50 为准，而不是成本源的 20×数量。
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-1", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-1", "测试货品", 1, "2026-07-10 09:00:00", "2026-07-10 10:00:00", 50, 100, 100, 5, 45],
    ]);
    const costBytes = costSheet([
      ["货品编号", "固定成本价", "货品名称"],
      ["SKU-1", 20, "测试货品"],
    ]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓.xlsx");
    await Promise.all([writeFile(salesPath, salesBytes), writeFile(costPath, costBytes)]);

    // 以文件成本为准：不传 --cost-source
    const result = await execFileAsync(process.execPath, [
      "--import", "tsx", path.resolve("tools/sales-import-runner.ts"),
      "--download", salesPath,
      "--use-file-cost",
      "--expected-source-rows", "1",
      "--as-of", "2026-07-15",
      "--audit-root", path.join(directory, "audit"),
      "--dry-run",
    ], { cwd: path.resolve("."), encoding: "utf8", timeout: 30_000 });

    const output = JSON.parse(result.stdout) as { status?: string; auditPath?: string; outputPath?: string };
    assert.equal(output.status, "prepared");
    assert.ok(output.outputPath);
    const workbook = parseXlsxFirstSheet(new Uint8Array(await readFile(output.outputPath)));
    const head = workbook.rows[0]?.cells.map((cell) => String(cell)) ?? [];
    const costIdx = head.indexOf("货品成本");
    const grossIdx = head.indexOf("毛利");
    assert.ok(costIdx >= 0);
    // 文件成本列保留为 50（整行成本），毛利保留文件值 45
    assert.equal(Number(workbook.rows[1]?.cells[costIdx]), 50);
    assert.equal(Number(workbook.rows[1]?.cells[grossIdx]), 45);
    const audit = JSON.parse(await readFile(output.auditPath!, "utf8")) as {
      sources?: { costSource?: { mode?: string } };
      totals?: { costAmountCents?: number };
    };
    assert.equal(audit.sources?.costSource?.mode, "file_cost");
    assert.equal(audit.totals?.costAmountCents, 5000);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales dry-run accepts the current sales export shape", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-runner-test-"));
  try {
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-1", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-1", "测试货品", 1, "2026-07-15 09:00:00", "2026-07-15 10:00:00", 0, 100, 100, 5, 0],
    ]);
    const costBytes = costSheet([
      ["货品编号", "固定成本价", "货品名称"],
      ["SKU-1", 20, "测试货品"],
    ]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓.xlsx");
    await Promise.all([writeFile(salesPath, salesBytes), writeFile(costPath, costBytes)]);

    const result = await execFileAsync(process.execPath, [
      "--import", "tsx", path.resolve("tools/sales-import-runner.ts"),
      "--download", salesPath,
      "--cost-source", costPath,
      "--expected-download-sha256", sha256(salesBytes),
      "--expected-cost-sha256", sha256(costBytes),
      "--expected-source-rows", "1",
      "--as-of", "2026-07-15",
      "--audit-root", path.join(directory, "audit"),
      "--dry-run",
    ], { cwd: path.resolve("."), encoding: "utf8", timeout: 30_000 });

    const output = JSON.parse(result.stdout) as { status?: string; auditPath?: string };
    assert.equal(output.status, "prepared");
    assert.ok(output.auditPath);
    const audit = JSON.parse(await readFile(output.auditPath, "utf8")) as {
      period?: unknown;
      filtering?: { retainedRows?: number; excludedTodayRows?: number };
      validation?: { processedChecks?: { rowCount?: number; dateProblemRows?: number } };
    };
    assert.deepEqual(audit.period, {
      startDate: "2026-07-01",
      endDate: "2026-07-15",
      startDateTime: "2026-07-01 00:00:00",
      endExclusiveDateTime: "2026-07-16 00:00:00",
    });
    assert.equal(audit.filtering?.retainedRows, 1);
    assert.equal(audit.validation?.processedChecks?.rowCount, 1);
    assert.equal(audit.validation?.processedChecks?.dateProblemRows, 0);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales dry-run excludes only execution-day rows before later validation", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-today-filter-test-"));
  try {
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-1", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-1", "测试货品", 1, "2026-07-10 09:00:00", "2026-07-10 10:00:00", 0, 100, 100, 5, 0],
      ["ON-TODAY", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-NO-COST", "当日货品", 1, "2026-07-16 09:00:00", "2026-07-16 10:00:00", 0, 100, 100, 5, 0],
    ]);
    const costBytes = costSheet([
      ["货品编号", "固定成本价", "货品名称"],
      ["SKU-1", 20, "测试货品"],
    ]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓及零成本.xlsx");
    await Promise.all([writeFile(salesPath, salesBytes), writeFile(costPath, costBytes)]);

    const result = await execFileAsync(process.execPath, [
      "--import", "tsx", path.resolve("tools/sales-import-runner.ts"),
      "--download", salesPath,
      "--cost-source", costPath,
      "--expected-source-rows", "2",
      "--as-of", "2026-07-15",
      "--audit-root", path.join(directory, "audit"),
      "--dry-run",
    ], { cwd: path.resolve("."), encoding: "utf8", timeout: 30_000 });

    const output = JSON.parse(result.stdout) as { status?: string; auditPath?: string };
    assert.equal(output.status, "prepared");
    assert.ok(output.auditPath);
    const audit = JSON.parse(await readFile(output.auditPath, "utf8")) as {
      filtering?: { retainedRows?: number; excludedTodayRows?: number; nonWhitelistRows?: number };
    };
    assert.equal(audit.filtering?.retainedRows, 1);
    assert.equal(audit.filtering?.excludedTodayRows, 1);
    assert.equal(audit.filtering?.nonWhitelistRows, 0);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales dry-run ignores line ship time when shipment time is missing", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-line-ship-ignore-test-"));
  try {
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品级发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-1", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-1", "测试货品", 1, "2026-07-10 09:00:00", "", "2026-07-16 10:00:00", 0, 100, 100, 5, 0],
    ]);
    const costBytes = costSheet([
      ["货品编号", "固定成本价", "货品名称"],
      ["SKU-1", 20, "测试货品"],
    ]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓.xlsx");
    await Promise.all([writeFile(salesPath, salesBytes), writeFile(costPath, costBytes)]);

    const result = await execFileAsync(process.execPath, [
      "--import", "tsx", path.resolve("tools/sales-import-runner.ts"),
      "--download", salesPath,
      "--cost-source", costPath,
      "--expected-download-sha256", sha256(salesBytes),
      "--expected-cost-sha256", sha256(costBytes),
      "--expected-source-rows", "1",
      "--as-of", "2026-07-15",
      "--audit-root", path.join(directory, "audit"),
      "--dry-run",
    ], { cwd: path.resolve("."), encoding: "utf8", timeout: 30_000 });

    const output = JSON.parse(result.stdout) as { status?: string; auditPath?: string };
    assert.equal(output.status, "prepared");
    assert.ok(output.auditPath);
    const audit = JSON.parse(await readFile(output.auditPath, "utf8")) as {
      filtering?: { retainedRows?: number; excludedTodayRows?: number };
    };
    assert.equal(audit.filtering?.retainedRows, 1);
    assert.equal(audit.filtering?.excludedTodayRows, 0);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales upload failures are persisted as failed audits before they are rethrown", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-upload-failure-audit-test-"));
  const originalFetch = globalThis.fetch;
  try {
    const policy = JSON.parse(await readFile(path.resolve("config/sales-import-policy.json"), "utf8")) as { version: string };
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-1", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-1", "测试货品", 1, "2026-07-10 09:00:00", "2026-07-10 10:00:00", 0, 100, 100, 5, 0],
    ]);
    const costBytes = costSheet([
      ["货品编号", "固定成本价", "货品名称"],
      ["SKU-1", 20, "测试货品"],
    ]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓.xlsx");
    const auditRoot = path.join(directory, "audit");
    await Promise.all([writeFile(salesPath, salesBytes), writeFile(costPath, costBytes)]);

    globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
      if (url.searchParams.get("policyOnly") === "1") {
        return Response.json({ policyVersion: policy.version });
      }
      if (url.pathname === "/api/imports/sales/verify") {
        return Response.json({ shops: [] });
      }
      if (url.pathname === "/api/imports/sales/chunks" && init?.method === "POST") {
        const payload = JSON.parse(String(init.body)) as { action?: string };
        assert.equal(payload.action, "init");
        return Response.json({
          ok: true,
          upload: { id: "upload-failure-test", receivedChunkIndexes: [] },
        });
      }
      if (url.pathname === "/api/imports/sales/chunks" && init?.method === "PUT") {
        return Response.json({
          ok: false,
          message: "Django 销售服务暂时不可用，请稍后重试。",
        }, { status: 503 });
      }
      throw new Error(`unexpected request: ${init?.method ?? "GET"} ${url}`);
    }) as typeof fetch;

    await assert.rejects(
      runSalesImport({
        asOfDate: "2026-07-15",
        downloadPath: salesPath,
        costSourcePath: costPath,
        expectedDownloadSha256: sha256(salesBytes),
        expectedCostSha256: sha256(costBytes),
        expectedSourceRows: 1,
        auditRootPath: auditRoot,
        baseUrl: "http://localhost:3000",
        dryRun: false,
      }),
      (error: unknown) => error instanceof SalesImportError
        && error.failureCode === "IMPORT_FAILED"
        && error.stage === "chunk_upload_and_import",
    );

    const runDirectories = (await readdir(auditRoot, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
    assert.equal(runDirectories.length, 1);
    const audit = JSON.parse(
      await readFile(path.join(auditRoot, runDirectories[0]!, "audit.json"), "utf8"),
    ) as {
      ok?: boolean;
      import?: unknown;
      postImportVerification?: unknown;
      failure?: { code?: string; stage?: string; message?: string; failedAt?: string };
    };
    assert.equal(audit.ok, false);
    assert.equal(audit.import, null);
    assert.equal(audit.postImportVerification, null);
    assert.equal(audit.failure?.code, "IMPORT_FAILED");
    assert.equal(audit.failure?.stage, "chunk_upload_and_import");
    assert.match(audit.failure?.message ?? "", /Django 销售服务暂时不可用/);
    assert.ok(audit.failure?.failedAt);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales recovery performs a fresh exact-batch verification before reporting completion", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-live-recovery-test-"));
  const originalFetch = globalThis.fetch;
  try {
    const policy = JSON.parse(await readFile(path.resolve("config/sales-import-policy.json"), "utf8")) as { version: string };
    const salesBytes = new TextEncoder().encode("already-processed-sales-source");
    const costBytes = new TextEncoder().encode("bound-cost-source");
    const salesPath = path.join(directory, "sales.xlsx");
    const costPath = path.join(directory, "cost.xlsx");
    const auditRoot = path.join(directory, "audit");
    const previousRunId = "previous-verified-run";
    const previousAuditPath = path.join(auditRoot, previousRunId, "audit.json");
    await mkdir(path.dirname(previousAuditPath), { recursive: true });
    await Promise.all([
      writeFile(salesPath, salesBytes),
      writeFile(costPath, costBytes),
      writeFile(previousAuditPath, JSON.stringify({
        ok: true,
        policyVersion: policy.version,
        period: { startDate: "2026-07-01", endDate: "2026-07-15" },
        sourceCountContract: {
          semantic: "xlsx_nonblank_data_rows",
          expected: 1,
          actual: 1,
          verified: true,
        },
        sources: { costSource: { sha256: sha256(costBytes) } },
        filtering: { sourceRows: 1, retainedRows: 1 },
        import: { batch: { id: "sales-batch-1", status: "completed", rowCount: 1 } },
        postImportVerification: { verified: true },
      })),
      writeFile(path.join(auditRoot, "processed-downloads.json"), JSON.stringify({
        runs: [{
          rawSha256: sha256(salesBytes),
          status: "verified_completed",
          runId: previousRunId,
          periodStart: "2026-07-01",
          periodEnd: "2026-07-15",
          auditPath: previousAuditPath,
        }],
      })),
    ]);

    let exactBatchChecks = 0;
    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
      if (url.searchParams.get("policyOnly") === "1") return Response.json({ policyVersion: policy.version });
      assert.equal(url.searchParams.get("batchId"), "sales-batch-1");
      exactBatchChecks += 1;
      return Response.json({
        policyVersion: policy.version,
        period: { startDate: "2026-07-01", endDate: "2026-07-15", endExclusive: "2026-07-16" },
        batch: { id: "sales-batch-1", status: "completed", rowCount: 1 },
        stats: {
          rowCount: 1,
          minShipTime: "2026-07-10 10:00:00",
          maxShipTime: "2026-07-10 10:00:00",
          excludedWarehouseRows: 0,
          rowsNotOwnedByBatch: 0,
        },
        nonWhitelistChannels: [],
      });
    }) as typeof fetch;

    const result = await runSalesImport({
      asOfDate: "2026-07-15",
      downloadPath: salesPath,
      costSourcePath: costPath,
      expectedDownloadSha256: sha256(salesBytes),
      expectedCostSha256: sha256(costBytes),
      expectedSourceRows: 1,
      auditRootPath: auditRoot,
      baseUrl: "http://localhost:3000",
      dryRun: false,
    });
    assert.equal(result.status, "verified_completed");
    assert.equal(result.recovered, true);
    assert.equal(exactBatchChecks, 1);
    const refreshedAudit = JSON.parse(await readFile(previousAuditPath, "utf8")) as {
      postImportVerification?: { recoveryReverified?: boolean };
    };
    assert.equal(refreshedAudit.postImportVerification?.recoveryReverified, true);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(directory, { recursive: true, force: true });
  }
});

test("sales recovery does not reuse a completed audit from an older policy version", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "sales-policy-recovery-test-"));
  try {
    const salesBytes = salesSheet([
      ["网店订单号", "销售渠道", "发货仓库", "货品编号", "货品名称", "数量", "下单时间", "发货时间", "货品成本", "分摊后单价", "分摊后金额", "费用分摊", "毛利"],
      ["ON-1", "京东-志高切肉机旗舰店（志高迈德豪）", "主仓", "SKU-1", "测试货品", 1, "2026-07-10 09:00:00", "2026-07-10 10:00:00", 0, 100, 100, 5, 0],
    ]);
    const costBytes = costSheet([
      ["货品编号", "固定成本价", "货品名称"],
      ["SKU-1", 20, "测试货品"],
    ]);
    const salesPath = path.join(directory, "销售单明细账.xlsx");
    const costPath = path.join(directory, "分仓库存查询_已剔除刷刷仓.xlsx");
    const auditRoot = path.join(directory, "audit");
    const previousAuditPath = path.join(auditRoot, "old-audit.json");
    await mkdir(auditRoot, { recursive: true });
    await Promise.all([
      writeFile(salesPath, salesBytes),
      writeFile(costPath, costBytes),
      writeFile(previousAuditPath, JSON.stringify({
        ok: true,
        policyVersion: "old-policy",
        period: { startDate: "2026-07-01", endDate: "2026-07-15" },
        sources: { costSource: { sha256: sha256(costBytes) } },
        filtering: { sourceRows: 1 },
        import: { batch: { status: "completed" } },
      })),
      writeFile(path.join(auditRoot, "processed-downloads.json"), JSON.stringify({
        runs: [{
          rawSha256: sha256(salesBytes),
          status: "imported",
          runId: "old-run",
          periodStart: "2026-07-01",
          periodEnd: "2026-07-15",
          auditPath: previousAuditPath,
        }],
      })),
    ]);

    const result = await execFileAsync(process.execPath, [
      "--import", "tsx", path.resolve("tools/sales-import-runner.ts"),
      "--download", salesPath,
      "--cost-source", costPath,
      "--expected-download-sha256", sha256(salesBytes),
      "--expected-cost-sha256", sha256(costBytes),
      "--expected-source-rows", "1",
      "--as-of", "2026-07-15",
      "--audit-root", auditRoot,
      "--dry-run",
    ], { cwd: path.resolve("."), encoding: "utf8", timeout: 30_000 });

    const output = JSON.parse(result.stdout) as { status?: string; recovered?: boolean };
    assert.equal(output.status, "prepared");
    assert.notEqual(output.recovered, true);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
