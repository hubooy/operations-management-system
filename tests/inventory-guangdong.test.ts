import test from "node:test";
import assert from "node:assert/strict";
import * as XLSX from "xlsx";
import { monitorWorkbook, parseWatchGrid, parseWatchPaste, parseWatchWorkbook, watchWorkbook, workbookBytes } from "../lib/inventory/guangdong-workbook";
import { createDjangoInventoryService, INVENTORY_GUANGDONG_PATH } from "../lib/django/inventory-service";

test("广东清单保留前导零、状态、备注及公式文本，导出可回导", () => {
  const rows = [{ productCode: "00123", active: false, notes: "=HYPERLINK(\"https://example.invalid\")" }];
  const bytes = watchWorkbook(rows);
  assert.deepEqual(parseWatchWorkbook(new Uint8Array(bytes).buffer), rows);
  const sheet = XLSX.read(bytes, { type: "array" }).Sheets["广东监控清单"];
  assert.equal(sheet.A2.t, "s"); assert.equal(sheet.C2.t, "s"); assert.equal(sheet.C2.f, undefined);
});

test("广东多行粘贴支持单列及完整表头，不猜测无效状态或数字编码", () => {
  assert.deepEqual(parseWatchPaste("00123\n00567"), [{ productCode: "00123", active: true, notes: "" }, { productCode: "00567", active: true, notes: "" }]);
  assert.equal(parseWatchPaste("货品编码\t监控状态\t备注\n00123\t暂停\t夏季")[0].active, false);
  assert.throws(() => parseWatchGrid([["货品编码", "监控状态", "备注"], [123, "启用", ""]]), /文本/);
  assert.throws(() => parseWatchPaste("00123\t未知\t"), /状态/);
  assert.throws(() => parseWatchGrid([["编码", "监控状态", "备注"], ["123", "", ""]]), /表头/);
});

test("广东文件拒绝公式、多表、超限与空集合", () => {
  const book = XLSX.utils.book_new(); const sheet = XLSX.utils.aoa_to_sheet([["货品编码", "监控状态", "备注"], ["P", "启用", ""]]);
  sheet.A2.f = 'TEXT(123,"00000")'; XLSX.utils.book_append_sheet(book, sheet, "清单");
  assert.throws(() => parseWatchWorkbook(XLSX.write(book, { type: "array", bookType: "xlsx" })), /公式/);
  assert.throws(() => parseWatchWorkbook(new Uint8Array(workbookBytes([{ name: "a", rows: [["a"]] }, { name: "b", rows: [["b"]] }])).buffer), /一个工作表/);
  assert.throws(() => parseWatchPaste(Array.from({ length: 5001 }, (_, i) => String(i)).join("\n")), /5000/);
  assert.throws(() => parseWatchWorkbook(new Uint8Array(watchWorkbook([])).buffer), /1–5000/);
});

test("广东监控导出使用7/15/30、销售周转、生产安全与备货字段并删除明细成本", () => {
  const bytes = monitorWorkbook({
    version: "1/sales:1",
    hasInventory: true,
    watchCount: 1,
    sync: { inventoryAsOf: "2026-09-09", inventoryAgeAsOf: "2026-09-08", salesThrough: "2026-09-09", latestInventoryBatchId: "stock", inventoryStale: false },
    filters: { brands: [], categories: [], suppliers: [] },
    metrics: { itemCount: 1, availableQuantity: 10, inTransitQuantity: 0, knownStockValueCents: 5000, missingCostCount: 0, missingStockCount: 0 },
    distribution: [], pagination: { page: 1, pageSize: 50, total: 1, totalPages: 1 }, disclosures: [],
    items: [{ productCode: "SKU-1", productName: "商品", specification: "白色", brand: "", category: "", supplier: "工厂", supplierSource: "ERP档案", warehouse: "广东仓", notes: "", availableQuantity: 10, inTransitQuantity: 0, inventoryAgeDays: 20, unitCostCents: 500, knownStockValueCents: 5000, costMissing: false, outbound7dQuantity: 7, outbound15dQuantity: 15, outbound30dQuantity: 30, leadDays: 10, bufferDays: 7, supplierLeadDays: 10, supplierBufferDays: 7, leadDaysOverride: null, bufferDaysOverride: null, cycleSource: "供应商设置", inventoryStale: false, replenishmentQuantity: 25, replenishmentRemainingQuantity: -5, replenishmentStockIncreaseQuantity: 30, replenishmentRemainingReason: "", latestReplenishmentOrderDate: "2026-09-08", operatorName: "运营甲", operatorNameOverride: null, planOperatorName: "运营甲", operatorNameSource: "最新备货计划", buyer: "采购甲", buyerOverride: null, planBuyer: "采购甲", buyerSource: "最新备货计划", turnoverDays: 10, latestOrderDate: "2026-09-09", risk: "urgent", riskLabel: "紧急补货", riskReasons: ["销售周转不超过生产周期"], autoRisk: "urgent", autoRiskLabel: "紧急补货", autoRiskReasons: ["销售周转不超过生产周期"], riskOverride: null, riskReasonOverride: null, riskSource: "系统判定" }],
  });
  const book = XLSX.read(bytes, { type: "array" });
  const rows = XLSX.utils.sheet_to_json<unknown[]>(book.Sheets["广东入仓监控"], { header: 1, defval: "" });
  assert.deepEqual(rows[0], ["货品编码", "货品名称", "规格编码", "规格", "品牌", "品类", "供应商", "供应商来源", "运营负责人", "采购负责人", "仓库", "可用库存", "在途", "7日出库", "15日出库", "30日出库", "销售周转天数", "库龄天数", "生产周期天", "安全天数", "最晚下单日期", "备货数量", "下单剩余库存", "下单后累计增库", "核算说明", "最新下单日期", "风险", "风险原因", "备注"]);
  assert.equal(rows[0].includes("成本元"), false);
  assert.equal(rows[0].includes("已覆盖货值元"), false);
  assert.equal(rows[1][8], "运营甲");
  assert.equal(rows[1][9], "采购甲");
  assert.equal(rows[1][14], 15);
  assert.equal(rows[1][21], 25);
  assert.equal(rows[1][22], -5);
  assert.equal(rows[1][23], 30);
});

test("广东网关允许有界reader读取，拒绝向reader提交清单写入", async () => {
  const service = createDjangoInventoryService({ readerBaseUrl: "http://127.0.0.1:8051", writerBaseUrl: "http://127.0.0.1:8052", internalSecret: "gd-local-isolated-signing-0123456789abcdef" });
  const principal = { email: "test@example.invalid", displayName: "Test", role: "admin" as const, scope: null };
  let requests = 0;
  const fetchImpl = async () => { requests++; return Response.json({ items: [] }, { headers: { "x-inventory-data-revision": "1:abcdef123456" } }); };
  await service.requestJson(principal, { method: "GET", path: INVENTORY_GUANGDONG_PATH, service: "reader" }, { fetchImpl });
  await assert.rejects(() => service.requestJson(principal, { method: "POST", path: INVENTORY_GUANGDONG_PATH + "/import", service: "reader", payload: {} }, { fetchImpl }));
  assert.equal(requests, 1);
});
