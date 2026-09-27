import assert from "node:assert/strict";
import test from "node:test";
import { parseSalesLedgerXlsx } from "../lib/imports/sales-ledger";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";

test("sales ledger parser keeps order number when export also has online order number", () => {
  const bytes = createXlsxWorkbookBytes([{
    name: "sheetTitle",
    rows: [
      [
        "订单编号",
        "网店订单号",
        "销售渠道",
        "物流公司",
        "物流单号",
        "发货仓库",
        "客户编号",
        "货品编号",
        "货品名称",
        "规格",
        "货品条码",
        "数量",
        "单价",
        "货品默认供应商",
        "下单时间",
        "货品成本",
        "分摊后单价",
        "分摊后金额",
        "费用分摊",
        "毛利",
        "毛利率",
        "未税毛利",
        "未税毛利率(%)",
        "货品分类",
        "发货时间",
        "网店规格编码",
      ],
      [
        "JY202607160001",
        "3561428013514429",
        "阿里巴巴-炊之王店",
        "京东快递",
        "JDVC1",
        "广东仓",
        "C1",
        "SKU-1",
        "测试货品",
        "默认规格",
        "BAR-1",
        1,
        100,
        "测试供应商",
        "2026-07-16 10:00:00",
        30,
        100,
        100,
        5,
        65,
        "65.00%",
        65,
        65,
        "测试分类",
        "2026-07-16 11:00:00",
        "JD-SKU-1",
      ],
    ],
  }]);

  const parsed = parseSalesLedgerXlsx(bytes);

  assert.equal(parsed.errors.length, 0);
  assert.equal(parsed.rows.length, 1);
  assert.equal(parsed.rows[0].orderNo, "JY202607160001");
  assert.equal(parsed.rows[0].onlineOrderNo, "3561428013514429");
  assert.equal(parsed.rows[0].onlineSpecCode, "JD-SKU-1");
  assert.equal(parsed.rows[0].platform, "1688");
  assert.equal(parsed.rows[0].shopName, "炊之王店");
  assert.ok(parsed.headers.includes("订单编号"));
  assert.ok(parsed.headers.includes("网店订单号"));
});

test("sales ledger parser tolerates missing fee allocation column since Jikexyun v4 export", () => {
  // 2026-08-27 起吉客云 v4 导出不再包含"费用分摊"列（被"商品链接SkuId"替代），
  // 用户确认今后也不再补充；列缺失时费用分摊按 0 解析，不应产生错误。
  const bytes = createXlsxWorkbookBytes([{
    name: "sheetTitle",
    rows: [
      [
        "订单编号",
        "网店订单号",
        "销售渠道",
        "发货仓库",
        "货品编号",
        "货品名称",
        "数量",
        "单价",
        "货品成本",
        "分摊后单价",
        "分摊后金额",
        "商品链接SkuId",
        "毛利",
        "下单时间",
        "发货时间",
      ],
      [
        "JY202608270001",
        "3602285006132510",
        "京东-志高商用设备旗舰店（亿用）",
        "广东仓",
        "SKU-1",
        "测试货品",
        1,
        100,
        30,
        100,
        100,
        "10033418533445",
        70,
        "2026-08-26 10:00:00",
        "2026-08-26 11:00:00",
      ],
    ],
  }]);

  const parsed = parseSalesLedgerXlsx(bytes);

  assert.equal(parsed.errors.length, 0);
  assert.equal(parsed.rows.length, 1);
  assert.equal(parsed.rows[0].feeAllocationCents, 0);
  assert.equal(parsed.rows[0].grossProfitCents, 7000);
});
