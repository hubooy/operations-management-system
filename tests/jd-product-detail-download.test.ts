import assert from "node:assert/strict";
import { mkdtemp, rm, utimes, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  acquireJdProductDetailDownload,
  findRecentJdProductDetailDownload,
  validateJdProductDetailWorkbook,
} from "../lib/jd/product-detail-download";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";

const expectedPrefix = "701455_商品明细_离线_不包括对比时间_分天下载_2026-07-01_2026-07-19";

async function withTempDirectory(run: (directory: string) => Promise<void>) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "jdsz-download-test-"));
  try {
    await run(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("reuses only a completed same-range file modified within one hour", async () => {
  await withTempDirectory(async (directory) => {
    const now = Date.now();
    const recent = path.join(directory, `${expectedPrefix}_recent.xlsx`);
    const old = path.join(directory, `${expectedPrefix}_old.xlsx`);
    const otherRange = path.join(directory, "701455_商品明细_离线_不包括对比时间_分天下载_2026-07-18_2026-07-18.xlsx");
    await Promise.all([writeFile(recent, "recent"), writeFile(old, "old"), writeFile(otherRange, "other")]);
    await utimes(old, new Date(now - 2 * 60 * 60_000), new Date(now - 2 * 60 * 60_000));

    assert.equal(await findRecentJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      maxAgeMs: 60 * 60_000,
      nowMs: now,
    }), recent);
  });
});

test("skips the browser download click when a recent same-range file exists", async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, `${expectedPrefix}_existing.xlsx`);
    await writeFile(filePath, "ready");
    let clicks = 0;

    const result = await acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      triggerDownload: async () => { clicks += 1; },
    });

    assert.equal(result.filePath, filePath);
    assert.equal(result.reused, true);
    assert.equal(result.downloadClicks, 0);
    assert.equal(clicks, 0);
  });
});

test("checks the directory after timeout and retries the download click at most once", async () => {
  await withTempDirectory(async (directory) => {
    let now = 1_000_000;
    let clicks = 0;
    const completed = path.join(directory, `${expectedPrefix}_new.xlsx`);
    const result = await acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      now: () => now,
      sleep: async (ms) => { now += ms; },
      initialWaitMs: 2,
      pollIntervalMs: 1,
      maxRetries: 1,
      triggerDownload: async () => {
        clicks += 1;
        if (clicks === 2) await writeFile(completed, "ready");
      },
    });

    assert.equal(result.filePath, completed);
    assert.equal(result.downloadClicks, 2);
    assert.equal(clicks, 2);
  });
});

test("does not click again while a matching crdownload is still present", async () => {
  await withTempDirectory(async (directory) => {
    let now = 1_000_000;
    let clicks = 0;
    const partial = path.join(directory, `${expectedPrefix}_pending.xlsx.crdownload`);

    await assert.rejects(acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      now: () => now,
      sleep: async (ms) => { now += ms; },
      initialWaitMs: 2,
      partialGraceMs: 2,
      pollIntervalMs: 1,
      maxRetries: 1,
      triggerDownload: async () => {
        clicks += 1;
        await writeFile(partial, "downloading");
      },
    }), /\.crdownload/);

    assert.equal(clicks, 1);
  });
});

test("does not issue the first click when a previous-process partial is present", async () => {
  await withTempDirectory(async (directory) => {
    let now = 1_000_000;
    await writeFile(path.join(directory, `${expectedPrefix}_pending.xlsx.crdownload`), "downloading");
    let clicks = 0;
    await assert.rejects(acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      now: () => now,
      sleep: async (ms) => { now += ms; },
      partialGraceMs: 2,
      pollIntervalMs: 1,
      triggerDownload: async () => { clicks += 1; },
    }), /Existing JD product-detail/);
    assert.equal(clicks, 0);
  });
});

test("chooses a recent workbook by verified dimension instead of newest mtime alone", async () => {
  await withTempDirectory(async (directory) => {
    const now = Date.now();
    const sku = path.join(directory, `${expectedPrefix}_sku.xlsx`);
    const spu = path.join(directory, `${expectedPrefix}_SPU.xlsx`);
    await writeFile(sku, createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SKU", "SKU名称"], ["2026-07-01", "1", "sku"]] }]));
    await writeFile(spu, createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SPU", "SPU名称"], ["2026-07-01", "2", "spu"]] }]));
    await utimes(sku, new Date(now - 2_000), new Date(now - 2_000));
    await utimes(spu, new Date(now - 1_000), new Date(now - 1_000));

    assert.equal(await findRecentJdProductDetailDownload({ downloadDirectory: directory, expectedPrefix, nowMs: now, dimension: "SKU" }), sku);
    assert.equal(await findRecentJdProductDetailDownload({ downloadDirectory: directory, expectedPrefix, nowMs: now, dimension: "SPU" }), spu);
  });
});

test("validates exact daily coverage and rejects an out-of-range product-detail workbook", () => {
  const valid = createXlsxWorkbookBytes([{ name: "data", rows: [
    ["时间", "SKU", "SKU名称"],
    ["2026-07-01", "1", "a"],
    ["2026-07-02", "2", "b"],
  ] }]);
  assert.deepEqual(validateJdProductDetailWorkbook(valid, "SKU", { startDate: "2026-07-01", endDate: "2026-07-02" }).dates, ["2026-07-01", "2026-07-02"]);
  const invalid = createXlsxWorkbookBytes([{ name: "data", rows: [
    ["时间", "SKU", "SKU名称"],
    ["2026-07-01", "1", "a"],
    ["2026-07-03", "2", "b"],
  ] }]);
  assert.throws(() => validateJdProductDetailWorkbook(invalid, "SKU", { startDate: "2026-07-01", endDate: "2026-07-02" }), /outside/);
});

test("workbook validation handles a single day, cross-month coverage, exact aggregate skip, and empty IDs", () => {
  const single = createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SPU", "SPU名称"], ["2026-07-31", "合计", "合计"], ["2026-07-31", "1", "a"]] }]);
  assert.equal(validateJdProductDetailWorkbook(single, "SPU", { startDate: "2026-07-31", endDate: "2026-07-31" }).rowCount, 2);
  const crossMonth = createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SKU", "SKU名称"], ["2026-07-31", "1", "a"], ["2026-08-01", "2", "b"]] }]);
  assert.deepEqual(validateJdProductDetailWorkbook(crossMonth, "SKU", { startDate: "2026-07-31", endDate: "2026-08-01" }).dates, ["2026-07-31", "2026-08-01"]);
  const missing = createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SKU", "SKU名称"], ["2026-07-01", "1", "a"], ["2026-07-03", "3", "c"]] }]);
  assert.throws(() => validateJdProductDetailWorkbook(missing, "SKU", { startDate: "2026-07-01", endDate: "2026-07-03" }), /missing/);
  const emptyId = createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SKU", "SKU名称"], ["2026-07-01", "", "a"]] }]);
  assert.throws(() => validateJdProductDetailWorkbook(emptyId, "SKU", { startDate: "2026-07-01", endDate: "2026-07-01" }), /empty SKU/);
});

test("workbook validation rejects mixed dimensions and normalized impossible calendar days", () => {
  const mixed = createXlsxWorkbookBytes([{ name: "data", rows: [
    ["时间", "SKU", "SKU名称", "SPU"],
    ["2026-07-01", "sku-1", "a", "spu-1"],
  ] }]);
  assert.throws(
    () => validateJdProductDetailWorkbook(mixed, "SKU", { startDate: "2026-07-01", endDate: "2026-07-01" }),
    /both SKU and SPU/,
  );
  const impossible = createXlsxWorkbookBytes([{ name: "data", rows: [
    ["时间", "SKU", "SKU名称"],
    ["2026-02-30", "sku-1", "a"],
  ] }]);
  assert.throws(
    () => validateJdProductDetailWorkbook(impossible, "SKU", { startDate: "2026-02-28", endDate: "2026-03-02" }),
    /invalid detail date/,
  );
  assert.throws(
    () => validateJdProductDetailWorkbook(impossible, "SKU", { startDate: "2026-02-30", endDate: "2026-02-30" }),
    /真实存在|自然日/,
  );
});

test("atomically marks a newly downloaded SPU workbook and verifies its header", async () => {
  await withTempDirectory(async (directory) => {
    let now = Date.now();
    const generic = path.join(directory, `${expectedPrefix}_new.xlsx`);
    const result = await acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      dimension: "SPU",
      now: () => now,
      sleep: async (ms) => { now += ms; },
      pollIntervalMs: 1,
      triggerDownload: async () => {
        await writeFile(generic, createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SPU", "SPU名称"], ["2026-07-01", "2", "spu"]] }]));
      },
    });
    assert.match(path.basename(result.filePath), /_SPU\.xlsx$/);
  });
});

test("recovers and marks a completed generic SPU workbook without another click", async () => {
  await withTempDirectory(async (directory) => {
    const generic = path.join(directory, `${expectedPrefix}_crash-window.xlsx`);
    await writeFile(generic, createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SPU", "SPU名称"], ["2026-07-01", "2", "spu"]] }]));
    let clicks = 0;
    const result = await acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      dimension: "SPU",
      triggerDownload: async () => { clicks += 1; },
    });
    assert.equal(result.reused, true);
    assert.equal(clicks, 0);
    assert.match(path.basename(result.filePath), /_SPU\.xlsx$/);
  });
});

test("waits for the requested dimension when a newer wrong-dimension workbook exists", async () => {
  await withTempDirectory(async (directory) => {
    let now = Date.now();
    const spu = path.join(directory, `${expectedPrefix}_wanted.xlsx`);
    const sku = path.join(directory, `${expectedPrefix}_newer-wrong.xlsx`);
    const result = await acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      dimension: "SPU",
      now: () => now,
      sleep: async (ms) => { now += ms; },
      pollIntervalMs: 1,
      triggerDownload: async () => {
        await writeFile(spu, createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SPU", "SPU名称"], ["2026-07-01", "2", "spu"]] }]));
        await writeFile(sku, createXlsxWorkbookBytes([{ name: "data", rows: [["时间", "SKU", "SKU名称"], ["2026-07-01", "1", "sku"]] }]));
      },
    });
    assert.match(path.basename(result.filePath), /wanted_SPU\.xlsx$/);
  });
});

test("ignores an abandoned partial older than the reuse window", async () => {
  await withTempDirectory(async (directory) => {
    const now = Date.now();
    const stale = path.join(directory, `${expectedPrefix}_stale.xlsx.crdownload`);
    const completed = path.join(directory, `${expectedPrefix}_fresh.xlsx`);
    await writeFile(stale, "abandoned");
    await utimes(stale, new Date(now - 2 * 60 * 60_000), new Date(now - 2 * 60 * 60_000));
    let clock = now;
    let clicks = 0;
    const result = await acquireJdProductDetailDownload({
      downloadDirectory: directory,
      expectedPrefix,
      now: () => clock,
      sleep: async (ms) => { clock += ms; },
      pollIntervalMs: 1,
      triggerDownload: async () => { clicks += 1; await writeFile(completed, "ready"); },
    });
    assert.equal(result.filePath, completed);
    assert.equal(clicks, 1);
  });
});
