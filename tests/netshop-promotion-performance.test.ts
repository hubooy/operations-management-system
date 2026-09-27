import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import {
  netshopPromotionMetrics,
  netshopPromotionPaymentSourceSql,
  netshopPromotionProductIdSql,
  netshopPromotionSourceSql,
} from "../lib/netshop/promotion-query";

function createSchema(sqlite: DatabaseSync) {
  sqlite.exec(`
    CREATE TABLE netshop_rows (
      source_row_key TEXT PRIMARY KEY,
      source TEXT NOT NULL,
      dataset TEXT NOT NULL,
      platform TEXT NOT NULL,
      shop_name TEXT NOT NULL,
      business_date TEXT,
      product_name TEXT NOT NULL DEFAULT '',
      sku_id TEXT NOT NULL DEFAULT '',
      spu_id TEXT NOT NULL DEFAULT '',
      metrics_json TEXT NOT NULL DEFAULT '{}',
      raw_json TEXT NOT NULL DEFAULT '{}'
    );
  `);
}

function insertRow(sqlite: DatabaseSync, input: {
  key: string;
  source: string;
  dataset: string;
  platform: string;
  shopName: string;
  date: string;
  skuId?: string;
  spuId?: string;
  productName?: string;
  metrics: Record<string, number>;
  raw?: Record<string, string>;
}) {
  sqlite.prepare(`INSERT INTO netshop_rows
    (source_row_key, source, dataset, platform, shop_name, business_date, product_name, sku_id, spu_id, metrics_json, raw_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(input.key, input.source, input.dataset, input.platform, input.shopName, input.date, input.productName ?? "", input.skuId ?? "", input.spuId ?? "", JSON.stringify(input.metrics), JSON.stringify(input.raw ?? {}));
}

test("promotion performance renders JD report metrics by tracked SKU and intersects JD BI dates", async () => {
  const sqlite = new DatabaseSync(":memory:");
  createSchema(sqlite);
  const common = { source: "jd_promotion", dataset: "ad", platform: "京东", shopName: "志高商用设备旗舰店" };
  insertRow(sqlite, { ...common, key: "jd-1", date: "2026-08-13", skuId: "100001", metrics: { "展现数": 567, "点击数": 26, "花费": 25.97, "总订单行": 1, "总订单金额": 998.75 }, raw: { "产品线": "商用饮水设备" } });
  insertRow(sqlite, { ...common, key: "jd-2", date: "2026-08-14", skuId: "100001", metrics: { "展现数": 300, "点击数": 6, "花费": 10, "总订单行": 0, "总订单金额": 0 }, raw: { "产品线": "商用饮水设备" } });
  insertRow(sqlite, { ...common, key: "jd-3", date: "2026-08-14", skuId: "100002", metrics: { "展现数": 40, "点击数": 4, "花费": 6.81, "总订单行": 0, "总订单金额": 0 }, raw: { "产品线": "商用切肉机" } });
  insertRow(sqlite, { key: "bi-1", source: "jd_sku_daily", dataset: "sku_daily", platform: "京东", shopName: common.shopName, date: "2026-08-13", skuId: "100001", metrics: { transactionAmountCents: 150_000 } });
  insertRow(sqlite, { key: "bi-2", source: "jd_sku_daily", dataset: "sku_daily", platform: "京东", shopName: common.shopName, date: "2026-08-14", skuId: "100001", metrics: { transactionAmountCents: 50_000 } });
  insertRow(sqlite, { key: "tmall-noise", source: "tmall_promotion", dataset: "promotion_daily", platform: "天猫", shopName: "其他店铺", date: "2026-08-13", spuId: "999999", metrics: { spendCents: 999_999, netTransactionAmountCents: 999_999 } });

  const metric = netshopPromotionMetrics;
  const summary = sqlite.prepare(`SELECT
    COUNT(DISTINCT ${netshopPromotionProductIdSql}) AS product_count,
    SUM(${metric.spendCents}) AS spend_cents,
    SUM(${metric.netTransactionAmountCents}) AS transaction_cents,
    SUM(${metric.impressions}) AS impressions,
    SUM(${metric.clicks}) AS clicks,
    SUM(${metric.netOrders}) AS orders
    FROM netshop_rows r
    WHERE ${netshopPromotionSourceSql} AND r.platform = ? AND r.shop_name = ? AND r.business_date BETWEEN ? AND ?`)
    .get("京东", common.shopName, "2026-08-13", "2026-08-14") as Record<string, number>;
  assert.deepEqual({ ...summary }, {
    product_count: 2,
    spend_cents: 4_278,
    transaction_cents: 99_875,
    impressions: 907,
    clicks: 36,
    orders: 1,
  });

  const items = sqlite.prepare(`SELECT
    ${netshopPromotionProductIdSql} AS id,
    MAX(COALESCE(NULLIF(r.product_name, ''), NULLIF(CAST(json_extract(r.raw_json, '$."产品线"') AS TEXT), ''))) AS product_name,
    GROUP_CONCAT(DISTINCT r.business_date) AS coverage_dates
    FROM netshop_rows r
    WHERE ${netshopPromotionSourceSql} AND r.platform = ? AND r.shop_name = ?
    GROUP BY r.platform, r.shop_name, ${netshopPromotionProductIdSql}
    ORDER BY id`).all("京东", common.shopName) as Array<Record<string, string>>;
  assert.deepEqual(items.map((item) => ({ ...item })), [
    { id: "100001", product_name: "商用饮水设备", coverage_dates: "2026-08-13,2026-08-14" },
    { id: "100002", product_name: "商用切肉机", coverage_dates: "2026-08-14" },
  ]);

  const payment = sqlite.prepare(`SELECT SUM(CAST(json_extract(r.metrics_json, '$.transactionAmountCents') AS REAL)) AS payment_cents
    FROM netshop_rows r
    WHERE ${netshopPromotionPaymentSourceSql} AND r.platform = ? AND r.shop_name = ? AND r.business_date BETWEEN ? AND ?`)
    .get("京东", common.shopName, "2026-08-13", "2026-08-14") as { payment_cents: number };
  assert.equal(payment.payment_cents, 200_000);
  sqlite.close();
});
