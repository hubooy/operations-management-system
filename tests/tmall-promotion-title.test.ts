import assert from "node:assert/strict";
import test from "node:test";
import { strToU8, zipSync } from "fflate";
import { inspectTmallImportBytes, prepareTmallPromotionRows } from "../lib/netshop/normalized-import";

async function inspect(lines: string[], shopName = "天猫-志高炊之王专卖店") {
  const bytes = zipSync({ "商品报表_20260905.csv": strToU8([
    "日期,商品ID,商品名称,计划ID,计划名称,花费,净成交金额,总成交金额,展现量,点击量",
    ...lines,
  ].join("\r\n")) });
  return inspectTmallImportBytes({
    source: "tmall_promotion", bytes, fileName: "fixture.zip", fileSizeBytes: bytes.byteLength,
    shopName, expectedStartDate: "2026-09-05", expectedEndDate: "2026-09-05",
  });
}

test("one product with different plan titles preserves all spend and stable title provenance", async () => {
  const lines = [
    "2026-09-05,10001,商用设备新版标题,plan-a,计划A,10,20,30,100,10",
    "2026-09-05,10001,商用设备原版标题,plan-b,计划B,20,40,70,200,20",
  ];
  const result = await inspect(lines);
  const reversed = await inspect([...lines].reverse());
  assert.deepEqual(result.errors, []);
  assert.equal(result.totals.rowCount, 1);
  assert.equal(result.totals.spendCents, 3000);
  assert.equal(result.totals.netTransactionAmountCents, 6000);
  assert.equal(result.rows[0]!.metrics.clickThroughRate, 0.1);
  assert.equal(result.rows[0]!.spuId, "10001");
  assert.deepEqual(JSON.parse(String(result.rows[0]!.raw["主体名称列表"])), ["商用设备新版标题", "商用设备原版标题"].sort());
  assert.equal(result.rows[0]!.productName, reversed.rows[0]!.productName);
  assert.deepEqual(result.rows[0]!.metrics, reversed.rows[0]!.metrics);
  assert.deepEqual(result.rows[0]!.raw, reversed.rows[0]!.raw);
});

test("equal titles cannot merge different product IDs or shops, and out-of-range dates remain invalid", async () => {
  const lines = [
    "2026-09-05,10001,同名商品,plan-a,计划A,10,20,30,100,10",
    "2026-09-05,10002,同名商品,plan-b,计划B,20,40,70,200,20",
  ];
  const first = await inspect(lines);
  const second = await inspect(lines, "天猫-志高丽力专卖店");
  assert.equal(first.totals.rowCount, 2);
  assert.notEqual(first.rows[0]!.sourceRowKey, second.rows[0]!.sourceRowKey);
  const outside = await inspect([lines[0]!, lines[1]!.replace("2026-09-05", "2026-09-04")]);
  assert.ok(outside.errors.length > 0);
});

test("title reconciliation still rejects missing IDs and non-product subjects", () => {
  const row = (type: string, id: string, title: string) => ({ rowNumber: 2, raw: {
    "日期": "2026-09-05", "主体ID": id, "主体类型": type, "主体名称": title, "花费": 1,
  } });
  assert.throws(() => prepareTmallPromotionRows([row("商品", "", "A"), row("商品", "", "B")]), /有效商品 ID/);
  assert.throws(() => prepareTmallPromotionRows([row("商品", "10001", "A"), row("店铺", "10001", "B")]), /非商品主体/);
});
