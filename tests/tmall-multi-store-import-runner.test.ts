import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildTmallSpuCoverageUrl,
  datesInRange,
  parseRunnerArgs,
  postTmallImportWithNetworkRetry,
  requestedDatesToPlan,
  selectReceiptForDate,
  shanghaiYesterday,
  validateImportPayload,
  verifiedReceipts,
} from "../tools/tmall-multi-store-import-runner";

test("天猫 SPU 覆盖回查使用平台与店铺复合 outlet，不再发送旧 shop 参数", () => {
  const url = new URL(buildTmallSpuCoverageUrl(
    "http://localhost:3000",
    { shopName: "天猫-志高亿玖专卖店" },
    "2026-08-20",
    "2026-08-20",
  ));

  assert.equal(url.pathname, "/api/netshop/product-performance");
  assert.equal(url.searchParams.get("dimension"), "spu");
  assert.deepEqual(url.searchParams.getAll("platform"), ["天猫"]);
  assert.equal(url.searchParams.get("outlet"), "天猫\u001f天猫-志高亿玖专卖店");
  assert.equal(url.searchParams.has("shop"), false);
  assert.equal(url.searchParams.get("startDate"), "2026-08-20");
  assert.equal(url.searchParams.get("endDate"), "2026-08-20");
});

test("天猫导入日期按上海时区截止昨天并直接采用请求范围，不查询缺口", () => {
  const now = new Date("2026-08-02T04:00:00Z");
  assert.equal(shanghaiYesterday(now), "2026-08-01");
  assert.deepEqual(datesInRange("2026-07-30", "2026-08-01"), ["2026-07-30", "2026-07-31", "2026-08-01"]);
  assert.deepEqual(requestedDatesToPlan("2026-07-30", "2026-08-01"), ["2026-07-30", "2026-07-31", "2026-08-01"]);
  assert.deepEqual(requestedDatesToPlan("2026-07-30", "2026-08-01", ["2026-07-31", "2026-08-01"]), ["2026-07-31", "2026-08-01"]);
  assert.equal(parseRunnerArgs([], now).endDate, "2026-08-01");
  assert.deepEqual(parseRunnerArgs(["--dates", "2026-08-01,2026-07-28,2026-08-01"], now).dates, ["2026-07-28", "2026-08-01"]);
  assert.deepEqual(parseRunnerArgs(["--dates", "2026-08-01", "--force-existing"], now).dates, ["2026-08-01"]);
  assert.throws(() => parseRunnerArgs(["--dates", "2026-07-28", "--start-date", "2026-07-28"], now), /不能与 --start-date/);
  assert.throws(() => parseRunnerArgs(["--end-date", "2026-08-02"], now), /最多补到昨天/);
});

function receipt(hash: string, downloadedAt: string) {
  return {
    receiptPath: `C:\\download\\${hash}.json`,
    filePath: `C:\\download\\${hash}.xls`,
    bytes: new Uint8Array([1]),
    receipt: {
      version: 1 as const,
      storeKey: "tmall-yijiu",
      shopName: "天猫-志高亿玖专卖店",
      businessDate: "2026-07-31",
      fileName: `${hash}.xls`,
      sha256: hash,
      size: 1,
      downloadedAt,
    },
  };
}

test("根目录手工签收同日相同哈希可复用，多个不同文件仍拒绝猜测", () => {
  const hashA = "a".repeat(64);
  const same = selectReceiptForDate([receipt(hashA, "2026-08-01T01:00:00Z"), receipt(hashA, "2026-08-01T02:00:00Z")], "2026-07-31");
  assert.equal(same?.receipt.downloadedAt, "2026-08-01T02:00:00Z");
  assert.throws(() => selectReceiptForDate([receipt(hashA, "2026-08-01T01:00:00Z"), receipt("b".repeat(64), "2026-08-01T02:00:00Z")], "2026-07-31"), /内容不同/);
});

test("Cookie 工作流可把本轮子目录中的唯一签收单显式交给导入器", async () => {
  const downloadDir = await mkdtemp(path.join(tmpdir(), "tmall-explicit-receipt-"));
  const runDirectory = path.join(downloadDir, ".tmall-sycm-runs", "run-one");
  const fileName = "【生意参谋平台】商品_全部_2026-07-31_2026-07-31.xls";
  const filePath = path.join(runDirectory, fileName);
  const receiptPath = `${filePath}.tmall-receipt.json`;
  const bytes = new Uint8Array([1, 2, 3]);
  await mkdir(runDirectory, { recursive: true });
  await writeFile(filePath, bytes);
  await writeFile(receiptPath, JSON.stringify({
    version: 1,
    storeKey: "tmall-yijiu",
    shopName: "天猫-志高亿玖专卖店",
    businessDate: "2026-07-31",
    fileName,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    size: bytes.byteLength,
    downloadedAt: "2026-08-01T02:00:00.000Z",
  }));
  try {
    const receipts = await verifiedReceipts({
      storeKey: "tmall-yijiu",
      shopName: "天猫-志高亿玖专卖店",
      platform: "天猫",
      portalUrl: "https://sycm.taobao.com/portal/home.htm",
      enabled: true,
      initialStartDate: "2026-07-01",
      browser: { profileDir: "unused", debugPort: 9222, downloadDir },
    }, [receiptPath]);
    assert.equal(receipts.length, 1);
    assert.equal(receipts[0]?.filePath, filePath);
    assert.equal(selectReceiptForDate(receipts, "2026-07-31")?.receiptPath, receiptPath);
  } finally {
    await rm(downloadDir, { recursive: true, force: true });
  }
});

test("天猫导入结果必须精确匹配店铺、日期、数据集与 HTTP 状态", () => {
  const payload = {
    ok: true as const,
    status: "imported" as const,
    warnings: [],
    batch: {
      id: "batch-1",
      source: "tmall_product_daily",
      dataset: "spu_daily",
      platform: "天猫",
      shopName: "天猫-志高亿玖专卖店",
      status: "completed",
      warningCount: 0,
      rowCount: 10,
      dateMin: "2026-07-31",
      dateMax: "2026-07-31",
    },
    verification: {
      verified: true,
      parsedRowCount: 10,
      readbackRowCount: 10,
      dataset: "spu_daily",
      platform: "天猫",
      shopName: "天猫-志高亿玖专卖店",
      dateMin: "2026-07-31",
      dateMax: "2026-07-31",
    },
  };
  const store = { shopName: "天猫-志高亿玖专卖店" };
  assert.equal(validateImportPayload(payload, 201, store, "2026-07-31", 10).batchId, "batch-1");
  assert.equal(validateImportPayload({ ...payload, status: "duplicate" }, 200, store, "2026-07-31", 10).batchId, "batch-1");
  const djangoPayload = {
    ...payload,
    verification: {
      verified: true,
      rowCount: 10,
      dataset: "spu_daily",
      platform: "天猫",
      shopName: "天猫-志高亿玖专卖店",
      dateMin: "2026-07-31",
      dateMax: "2026-07-31",
    },
  };
  assert.equal(validateImportPayload(djangoPayload, 201, store, "2026-07-31", 10).batchId, "batch-1");
  assert.equal(validateImportPayload({
    ...djangoPayload,
    status: "duplicate",
    verification: { verified: true, rowCount: 10 },
  }, 200, store, "2026-07-31", 10).batchId, "batch-1");
  assert.throws(() => validateImportPayload({ ...payload, batch: { ...payload.batch, shopName: "B店" } }, 201, store, "2026-07-31", 10), /回查不一致/);
  assert.throws(() => validateImportPayload(payload, 200, store, "2026-07-31", 10), /回查不一致/);
  assert.throws(() => validateImportPayload({ ...payload, status: "duplicate" }, 201, store, "2026-07-31", 10), /回查不一致/);

  const failures = [
    { label: "缺少 verification", payload: { ...payload, verification: undefined } },
    { label: "verification 未通过", payload: { ...payload, verification: { ...payload.verification, verified: false } } },
    { label: "批次行数少于预检", payload: { ...payload, batch: { ...payload.batch, rowCount: 9 } } },
    { label: "解析行数不一致", payload: { ...payload, verification: { ...payload.verification, parsedRowCount: 9 } } },
    { label: "回查行数不一致", payload: { ...payload, verification: { ...payload.verification, readbackRowCount: 9 } } },
    { label: "Django 回查行数不一致", payload: { ...djangoPayload, verification: { ...djangoPayload.verification, rowCount: 9 } } },
    { label: "两种行数证明相互矛盾", payload: { ...djangoPayload, verification: { ...payload.verification, rowCount: 9 } } },
    { label: "Django 新导入不得省略验证身份", payload: { ...djangoPayload, verification: { verified: true, rowCount: 10 } } },
    { label: "回查店铺不一致", payload: { ...payload, verification: { ...payload.verification, shopName: "B店" } } },
    { label: "回查日期不一致", payload: { ...payload, verification: { ...payload.verification, dateMax: "2026-07-30" } } },
  ];
  for (const failure of failures) {
    assert.throws(
      () => validateImportPayload(failure.payload, 201, store, "2026-07-31", 10),
      /回查不一致/,
      failure.label,
    );
  }
  assert.throws(() => validateImportPayload({
    ...djangoPayload,
    status: "duplicate",
    verification: { verified: true, rowCount: 10, shopName: "B店" },
  }, 200, store, "2026-07-31", 10), /回查不一致/);
});

test("天猫导入只对无 HTTP 响应的网络错误重建表单并有限重试", async () => {
  let requests = 0;
  let forms = 0;
  const waits: number[] = [];
  const response = await postTmallImportWithNetworkRetry({
    url: "http://localhost:3000/api/netshop/import",
    buildForm: () => {
      forms += 1;
      return new FormData();
    },
    request: async () => {
      requests += 1;
      if (requests === 1) throw new TypeError("fetch failed");
      return Response.json({ ok: true }, { status: 200 });
    },
    wait: async (delayMs) => { waits.push(delayMs); },
  });
  assert.equal(response.status, 200);
  assert.equal(requests, 2);
  assert.equal(forms, 2);
  assert.deepEqual(waits, [500]);

  requests = 0;
  const rejectedResponse = await postTmallImportWithNetworkRetry({
    url: "http://localhost:3000/api/netshop/import",
    buildForm: () => new FormData(),
    request: async () => {
      requests += 1;
      return Response.json({ ok: false }, { status: 500 });
    },
    wait: async () => { throw new Error("HTTP 响应不得重试"); },
  });
  assert.equal(rejectedResponse.status, 500);
  assert.equal(requests, 1);

  await assert.rejects(postTmallImportWithNetworkRetry({
    url: "http://localhost:3000/api/netshop/import",
    buildForm: () => new FormData(),
    request: async () => { throw new DOMException("timed out", "TimeoutError"); },
    wait: async () => { throw new Error("超时不得重试"); },
  }), (error: unknown) => error instanceof DOMException && error.name === "TimeoutError");
});
