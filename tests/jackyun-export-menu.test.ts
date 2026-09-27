import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";
import { chromium } from "playwright-core";
import { PlaywrightPageClient } from "../lib/jackyun/playwright-client";
import { clickPreparedExportAllPages, findExportMenuTarget, prepareExportAllPagesMenu,
  rightClickDataRow, runController, setShipmentTimeType } from "../tools/jackyun-browser-controller";

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
test("real pointer menu preparation, data row selection and durable export click in an isolated local frame", {
  skip: process.platform !== "win32" || !existsSync(chromePath), timeout: 30000,
}, async t => {
  const browser = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    await page.addInitScript("window.__name = (value) => value");
    // Local fulfilled fixture only: no Jackyun, account, export or import access.
    await page.route("**/*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: new URL(route.request().url()).pathname === "/"
      ? '<body style="margin:0"><iframe src="/order_detail_fixture" style="border:0;width:1200px;height:900px"></iframe></body>'
      : `<body style="margin:0"><style>
        #grid-goods_managet{width:900px} table{width:900px} th,td{height:45px}
        .mini-menuitem{height:36px;background:#eee;width:220px}
        .mini-menuitem-text{display:inline-block;width:190px;height:30px}
        .mini-menuitem-allow{display:inline-block;width:10px;height:12px}
        #parent,#leaf{position:absolute;display:none} #parent{left:380px;top:160px} #leaf{left:600px;top:200px}
        </style><div id="grid-goods_managet"><table><thead><tr><th>货品编号</th><th>货品名称</th></tr></thead>
        <tbody><tr class="mini-grid-row"><td>fixture-SKU</td><td>测试货品数据行</td></tr></tbody></table></div>
        <div id="parent" class="mini-menuitem"><span class="mini-menuitem-text">导出</span><i class="mini-menuitem-allow"></i></div>
        <div id="leaf" class="mini-menuitem"><span class="mini-menuitem-text">导出所有页（限500000行）</span></div>
        <script>
        window.trace=[];
        document.addEventListener('contextmenu',e=>{e.preventDefault();trace.push(e.target.closest('.mini-grid-row')?'data-row':'header');document.querySelector('#parent').style.display='block'});
        document.querySelector('#parent').addEventListener('mouseenter',()=>{trace.push('parent-enter');document.querySelector('#leaf').style.display='block'});
        document.querySelector('#leaf').addEventListener('click',()=>trace.push('export-click'));
        document.addEventListener('mousedown',e=>trace.push('down-'+e.button));
        </script></body>` }));
    await page.goto("https://menu-fixture.invalid/");
    const frame = page.frames().find(f => f.url().includes("order_detail_fixture"))!;
    const client = new PlaywrightPageClient(page, await page.context().newCDPSession(page));
    const trace = () => frame.evaluate(() => (window as unknown as { trace: string[] }).trace);
    const resetTrace = () => frame.evaluate(() => { (window as unknown as { trace: string[] }).trace = []; });
    const hints = ["order_detail_fixture"];
    await t.test("preferred grid header cannot win; submenu preparation sends no mouse buttons", async () => {
      await rightClickDataRow(client, hints, true);
      assert.ok((await trace()).includes("data-row")); assert.ok(!(await trace()).includes("header"));
      await resetTrace();
      const target = await prepareExportAllPagesMenu(client, "inventory", hints, 3000, 50);
      assert.match(target.text, /^导出所有页/);
      assert.deepEqual(await trace(), ["parent-enter"]);
      assert.equal(await findExportMenuTarget(client, ["foreign_module"]), null);
    });
    await t.test("intent is persisted before exactly one final click", async () => {
      await resetTrace();
      await clickPreparedExportAllPages(client, hints, async () => { await frame.evaluate(() => (window as unknown as { trace: string[] }).trace.push("intent")); });
      assert.deepEqual(await trace(), ["intent", "down-0", "export-click"]);
      await resetTrace();
      await assert.rejects(clickPreparedExportAllPages(client, hints, async () => { throw new Error("disk failure"); }), /disk failure/);
      assert.deepEqual(await trace(), []);
    });
    await t.test("disabled, covered or duplicate targets cannot record intent or click", async () => {
      let armed = 0;
      for (const fault of ["disabled", "covered", "duplicate"]) {
        await resetTrace();
        await frame.evaluate(fault => {
          const leaf = document.querySelector<HTMLElement>("#leaf")!;
          if (fault === "disabled") leaf.classList.add("mini-disabled");
          if (fault === "covered") {
            const cover = document.createElement("div"); cover.id = "extra";
            cover.style.cssText = "position:absolute;left:600px;top:200px;width:220px;height:36px;z-index:99;background:red";
            document.body.append(cover);
          }
          if (fault === "duplicate") {
            const copy = leaf.cloneNode(true) as HTMLElement; copy.id = "extra"; copy.style.top = "260px"; document.body.append(copy);
          }
        }, fault);
        await assert.rejects(clickPreparedExportAllPages(client, hints, async () => { armed++; }));
        assert.equal(armed, 0); assert.deepEqual(await trace(), []);
        await frame.evaluate(() => { document.querySelector("#leaf")!.classList.remove("mini-disabled"); document.querySelector("#extra")?.remove(); });
      }
    });
    await t.test("target movement on hover stops before intent", async () => {
      await page.mouse.move(20, 20); await resetTrace();
      await frame.evaluate(() => document.querySelector("#leaf")!.addEventListener("mouseenter", () => {
        (document.querySelector("#leaf") as HTMLElement).style.top = "320px";
      }, { once: true }));
      let armed = false;
      await assert.rejects(clickPreparedExportAllPages(client, hints, async () => { armed = true; }), /尚未发送/);
      assert.equal(armed, false); assert.deepEqual(await trace(), []);
    });
    await t.test("observed custom time selector chooses actual shipment option and verifies readback", async () => {
      await frame.evaluate(() => {
        const el = document.createElement("span"); el.id = "selectTimeStr"; el.className = "mini-buttonedit";
        el.style.cssText = "display:block;width:150px;height:30px"; document.body.append(el);
        let value = "created", text = "建单时间";
        const control = { valueField: "code", textField: "label", getData: () => [{ code: "created", label: "建单时间" }, { code: "shipment", label: "发货时间" }],
          setValue: (v: string) => { value = v; }, getValue: () => value,
          setText: (v: string) => { text = v; }, getText: () => text, doValueChanged: () => {} };
        (window as unknown as { mini: unknown }).mini = { get: (id: string) => id === "selectTimeStr" ? control : null };
      });
      assert.equal(await setShipmentTimeType(client), "发货时间");
      await frame.evaluate(() => {
        const duplicate = document.createElement("span"); duplicate.id = "other-time"; duplicate.className = "mini-combobox";
        duplicate.style.cssText = "display:block;width:150px;height:30px"; document.body.append(duplicate);
        const win = window as unknown as { mini: { get: (id: string) => unknown } }, control = win.mini.get("selectTimeStr");
        win.mini.get = () => control;
      });
      await assert.rejects(setShipmentTimeType(client), /不唯一/);
    });
  } finally { await browser.close(); }
});

test("menu diagnosis cannot share a business run ID or business output directory", async () => {
  for (const patch of [{ runId: "n8n-export-first-844" }, { outputRoot: "D:/运营管理系统/outputs/jackyun-import-runs" }]) {
    await assert.rejects(runController({ runId: "inspect-menu-fixture", outputRoot: "invalid", eventRoot: "invalid",
      snapshotDate: "2026-09-06", asOfDate: "2026-09-05", headless: true, launchOnly: false, checkLoginOnly: false,
      exportOnlyModule: "inventory", inspectExportMenuOnly: true, ...patch }), /独立诊断目录/);
  }
});
