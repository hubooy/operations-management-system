import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";
import { chromium } from "playwright-core";
import { captureDirectExport, readDirectTasks, validateDirectExportPayload } from "../lib/jackyun/direct-export";
import { JackyunHttpSession } from "../lib/jackyun/direct-http";
import { PlaywrightPageClient } from "../lib/jackyun/playwright-client";
import type { Page, Route } from "playwright-core";

const headers = (fields: string[]) => ({ enName: fields, showName: fields });
const inventory = {
  serverName: "erp-stock/erp-stock/export", excelType: "warehouse.stock.sku.export", typeName: "分仓库存查询",
  headersJson: JSON.stringify(headers(["goodsNo", "warehouseName", "currentQuantity", "retailPrice"])),
  conditionJson: JSON.stringify({ version: "2.0", ids: [] }), datasource: "", multiSheet: "false", isSyn: "false", exportTotal: "",
};
const form = (patch = {}) => new URLSearchParams({ ...inventory, ...patch }).toString();

test("captured request preserves all-page, masked, SKU and mother/child schema boundaries", () => {
  assert.equal(validateDirectExportPayload("inventory", form(), "branch_stock").data.excelType, inventory.excelType);
  assert.throws(() => validateDirectExportPayload("inventory", form({ plaintext: "true" }), "branch_stock"), /PARAMETER/);
  assert.equal(validateDirectExportPayload("inventory", form({ isSyn: "true" }), "branch_stock").data.isSyn, "true");
  assert.throws(() => validateDirectExportPayload("inventory", form({ isSyn: "unknown" }), "branch_stock"), /SCHEMA/);
  assert.throws(() => validateDirectExportPayload("inventory", form({ conditionJson: '{"version":"2.0","ids":[1]}' }), "branch_stock"), /SCOPE/);
  assert.throws(() => validateDirectExportPayload("inventory", form() + "&excelType=other", "branch_stock"), /PARAMETER/);
  assert.throws(() => validateDirectExportPayload("inventory", form(), "goods_managet_query"), /SCHEMA/);
  const combo = { ...inventory, serverName: "erp/erp/manysheetexport", excelType: "PackAgeGoodsInfoService,ChartPackage", typeName: "组合装及子件导出", multiSheet: "true",
    headersJson: JSON.stringify([headers(["goodsNo", "retailPrice"]), headers(["mainGoodsNo", "goodsNo", "goodsAmount", "retailPrice"])]),
    conditionJson: JSON.stringify({ version: "2.0", packageGood: "1", skuIds: [] }) };
  validateDirectExportPayload("combos", new URLSearchParams(combo).toString(), "goods_managet_combination");
  assert.throws(() => validateDirectExportPayload("combos", new URLSearchParams({ ...combo, headersJson: JSON.stringify([headers(["goodsNo", "retailPrice"])]) }).toString(), "goods_managet_combination"), /SCHEMA/);
});

test("sales HTTP request must use shipment time and exactly the accepted month-to-yesterday range", () => {
  const filter = { selectTimeStr: "tradeOrder.consign_time", timeBegin: "2026-09-01 00:00:00", timeEnd: "2026-09-06 23:59:59" };
  const sales = { ...inventory, serverName: "oms/oms/excel", excelType: "11", typeName: "销售单明细账",
    headersJson: JSON.stringify(headers(["goodsNo", "cost", "consignTime", "sellCount", "afterShareFee"])),
    conditionJson: JSON.stringify({ version: "2.0", filterOrderDetailDto: filter }) };
  validateDirectExportPayload("sales", new URLSearchParams(sales).toString(), "order_detail_list", "2026-09-06");
  const currentFilter = { timeType: 4, timeBegin: filter.timeBegin, timeEnd: filter.timeEnd };
  const current = (patch = {}) => new URLSearchParams({ ...sales, conditionJson: JSON.stringify({ version: "2.0", filterOrderDetailDto: { ...currentFilter, ...patch } }) }).toString();
  validateDirectExportPayload("sales", current(), "order_detail_list", "2026-09-06");
  validateDirectExportPayload("sales", current({ selectTimeStr: filter.selectTimeStr }), "order_detail_list", "2026-09-06");
  for (const timeType of [0, 1, 2, 3, 9, 12, "4", null, undefined]) {
    assert.throws(() => validateDirectExportPayload("sales", current({ timeType }), "order_detail_list", "2026-09-06"), /SCOPE/);
  }
  assert.throws(() => validateDirectExportPayload("sales", current({ selectTimeStr: "tradeOrder.pay_time" }), "order_detail_list", "2026-09-06"), /SCOPE/);
  assert.throws(() => validateDirectExportPayload("sales", current({ timeEnd: "2026-09-07 23:59:59" }), "order_detail_list", "2026-09-06"), /DATE/);
  const missingCost = new URLSearchParams(current()); missingCost.set("headersJson", JSON.stringify(headers(["goodsNo", "consignTime", "sellCount", "afterShareFee"])));
  assert.throws(() => validateDirectExportPayload("sales", missingCost.toString(), "order_detail_list", "2026-09-06"), /SCOPE/);
  assert.throws(() => validateDirectExportPayload("sales", new URLSearchParams(sales).toString(), "order_detail_list", "2026-09-07"), /DATE/);
  sales.conditionJson = JSON.stringify({ version: "2.0", filterOrderDetailDto: { ...filter, timeBegin: "2026-07-24 00:00:00" } });
  assert.throws(() => validateDirectExportPayload("sales", new URLSearchParams(sales).toString(), "order_detail_list", "2026-09-06"), /DATE/);
});

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
test("final browser POST is held until HTTP completion; failure destroys late browser callbacks", {
  skip: process.platform !== "win32" || !existsSync(chromePath), timeout: 60000,
}, async t => {
  const browser = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    let backendSubmissions = 0;
    const page = await browser.newPage();
    await page.route("**/*", route => {
      if (route.request().url().includes("startExcelExport")) { backendSubmissions++; return route.fulfill({ json: { code: 200, result: {} } }); }
      return route.fulfill({ contentType: "text/html; charset=utf-8", body: `<script>
        window.payload=${JSON.stringify(form())};window.finished=false;
        const leaf={name:'exportAll',text:'导出所有页',click:()=>fetch('/jkyun/excel-service/manager/startExcelExport',{method:'POST',headers:{'module_code':'branch_stock','Content-Type':'application/x-www-form-urlencoded'},body:payload}).then(r=>r.json()).then(()=>finished=true)};
        window.grid={getData:()=>[{}],contextMenuItems:[{name:'export',children:[leaf]}]};window.mini={get:()=>grid};
        </script>` });
    });
    const client = new PlaywrightPageClient(page, await page.context().newCDPSession(page));
    const open = () => page.goto("https://web.jackyun.com/erp_stock/goods_stock/branch_stock_main_v4.html");
    await t.test("captured submission never reaches the website, even when completion is signalled twice", async () => {
      await open();
      const captured = await captureDirectExport(client, page, "inventory");
      assert.equal(backendSubmissions, 0); assert.equal(await page.evaluate("finished"), false);
      await page.context().setOffline(true);
      await captured.complete({ data: {} }); await captured.complete({ data: {} });
      await page.waitForFunction("finished===true");
      await page.context().setOffline(false);
      assert.equal(backendSubmissions, 0);
    });
    await t.test("uncertain HTTP submission cancels the browser request before interception is released", async () => {
      await open(); const captured = await captureDirectExport(client, page, "inventory");
      await captured.cancel(); assert.equal(page.url(), "about:blank");
      assert.equal(backendSubmissions, 0);
    });
    await t.test("unknown schema also fails before any export request reaches the server", async () => {
      await open(); await page.evaluate("payload+='&plaintext=true'");
      await assert.rejects(captureDirectExport(client, page, "inventory"), /UNEXPECTED_PARAMETER/);
      assert.equal(page.url(), "about:blank"); assert.equal(backendSubmissions, 0);
    });
    await t.test("a failed browser acknowledgement cannot release its original POST after HTTP submission", async () => {
      await open();
      const failedAck: Pick<Page, "route" | "unroute" | "goto"> = {
        goto: page.goto.bind(page), unroute: pattern => page.unroute(pattern),
        route: (pattern, handler) => page.route(pattern, route => handler(new Proxy(route, {
          get(target, key) {
            if (key === "fulfill") return async () => { throw new Error("synthetic acknowledgement failure"); };
            const value = Reflect.get(target, key);
            return typeof value === "function" ? value.bind(target) : value;
          },
        }) as Route, route.request())),
      };
      const captured = await captureDirectExport(client, failedAck, "inventory");
      await assert.rejects(captured.complete({ data: {} }), /ACK_FAILED/);
      assert.equal(page.url(), "about:blank"); assert.equal(backendSubmissions, 0);
    });
    client.close();
  } finally { await browser.close(); }
});

test("direct polling rejects reordered or truncated task windows and pins failures", async () => {
  let response: object = { data: [{ id: "101", gmtCreate: 100, taskTitle: "【失败】fixture", taskStatus: 5, attachmentList: [] }], pageInfo: { total: 1 } };
  const http = new JackyunHttpSession({ accessToken: "fixture", refreshToken: "fixture", appkey: "fixture", signingSecret: "fixture" }, {
    publishSession: async () => assert.fail(), fetch: (async () => new Response(JSON.stringify({ code: 200, result: response }), { headers: { "content-type": "application/json" } })) as typeof fetch,
  });
  assert.deepEqual((await readDirectTasks(http, "inventory")).failedIds, ["sys-101"]);
  response = { data: [{ id: "9007199254740993", gmtCreate: 100, taskTitle: "pending", taskStatus: 1, attachmentList: null }], pageInfo: { total: 1 } };
  assert.equal((await readDirectTasks(http, "inventory")).records[0].taskId, "sys-9007199254740993");
  response = { data: [{ id: Number.MAX_SAFE_INTEGER + 1, gmtCreate: 100, taskTitle: "unsafe numeric ID" }], pageInfo: { total: 1 } };
  await assert.rejects(readDirectTasks(http, "inventory"), /RECORD_INVALID/);
  response = { data: [{ id: "101", gmtCreate: 100, taskTitle: "first" }, { id: "102", gmtCreate: 200, taskTitle: "newer" }], pageInfo: { total: 2 } };
  await assert.rejects(readDirectTasks(http, "inventory"), /PAGINATION/);
  response = { data: [], pageInfo: { total: 2 } };
  await assert.rejects(readDirectTasks(http, "inventory"), /PAGINATION/);
});
