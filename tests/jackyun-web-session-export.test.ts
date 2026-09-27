import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";
import { chromium } from "playwright-core";
import { PlaywrightPageClient } from "../lib/jackyun/playwright-client";
import { prepareWebSessionExport, submitWebSessionExport, readWebSessionTasks, selectNewWebSessionTask } from "../lib/jackyun/web-session-export";
import type { JackyunExportTaskRecord } from "../lib/jackyun/export-task";

const timestamp = "2026-09-07T00:00:00.400Z";
const expected = { module: "inventory" as const, sourceRows: 20000, exportIntentAt: timestamp,
  observedAt: "2026-09-07T00:00:05Z", allowedHosts: ["exports.invalid"], baselineIds: ["sys-100"] };
const record = (id: string, completed = true): JackyunExportTaskRecord => ({ taskId: id,
  label: "【导出任务-密文】分仓库存查询(20000条)", createdAt: Date.parse(timestamp) - 400,
  completed, urls: ["https://exports.invalid/file.xlsx"] });

test("web task baseline excludes same-second history, pins pending identity and refuses ambiguous results", () => {
  const old = record("sys-100"), pending = record("sys-101", false);
  const snapshot = { records: [old, pending], failedIds: [] };
  assert.equal(selectNewWebSessionTask(snapshot, expected).taskId, pending.taskId);
  assert.equal(selectNewWebSessionTask(snapshot, expected).result, null);
  assert.throws(() => selectNewWebSessionTask({ ...snapshot, records: [...snapshot.records, record("sys-102")] }, expected), /不唯一/);
  assert.throws(() => selectNewWebSessionTask(snapshot, { ...expected, pendingTaskId: "sys-102" }), /替换/);
  assert.throws(() => selectNewWebSessionTask({ ...snapshot, failedIds: ["sys-101"] }, expected), /明确失败/);
  const completed = selectNewWebSessionTask({ records: [old, record("sys-101")], failedIds: [] }, { ...expected, pendingTaskId: "sys-101" });
  assert.equal(completed.result?.binding.taskId, "sys-101");
  assert.throws(() => selectNewWebSessionTask({ records: [{ ...record("sys-101"), urls: ["https://foreign.invalid/other.xlsx"] }], failedIds: [] }, expected), /允许/);
  assert.equal(selectNewWebSessionTask({ records: [{ ...record("sys-101"), label: "【导出任务-密文】货品导出(20000条)" }], failedIds: [] }, expected).taskId, undefined);
});

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
test("web-session export uses the original enabled handler exactly once and never opens a context menu", {
  skip: process.platform !== "win32" || !existsSync(chromePath), timeout: 60000,
}, async t => {
  const browser = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    const page = await browser.newPage();
    // Every request is fulfilled locally, including the production-looking origin.
    await page.route("**/*", route => route.fulfill({ contentType: "text/html; charset=utf-8", body: `<script>
      window.trace=[]; window.leaf={name:'exportAll',text:'导出所有页（限500000行）',click:function(grid){trace.push('validate');if(grid!==window.grid)throw Error('grid mismatch');trace.push('submit')}};
      window.parentItem={name:'export',children:[leaf]};window.grid={getData:()=>[{}],contextMenuItems:[parentItem]};
      window.mini={get:()=>grid};document.addEventListener('contextmenu',()=>trace.push('contextmenu'));
      </script>` }));
    await page.goto("https://web.jackyun.com/erp_stock/goods_stock/branch_stock_main_v4.html");
    const client = new PlaywrightPageClient(page, await page.context().newCDPSession(page));
    const trace = () => page.evaluate("window.trace") as Promise<string[]>;
    await t.test("original validation runs after the durable intent and handle is single-use", async () => {
      const token = await prepareWebSessionExport(client, "inventory");
      assert.deepEqual(await trace(), []);
      await page.evaluate("trace.push('persisted-intent')");
      assert.equal(await submitWebSessionExport(client, token), true);
      assert.deepEqual(await trace(), ["persisted-intent", "validate", "submit"]);
      await assert.rejects(submitWebSessionExport(client, token), /CONSUMED/);
    });
    await t.test("wrong module, disabled, duplicate, or changed handlers do not submit", async () => {
      await assert.rejects(prepareWebSessionExport(client, "sales"), /NOT_READY/);
      await page.evaluate("leaf.disabled=true");
      await assert.rejects(prepareWebSessionExport(client, "inventory"), /NOT_READY/);
      await page.evaluate("leaf.disabled=false;parentItem.children.push({...leaf})");
      await assert.rejects(prepareWebSessionExport(client, "inventory"), /NOT_READY/);
      await page.evaluate("parentItem.children.pop()");
      const token = await prepareWebSessionExport(client, "inventory");
      await page.evaluate("leaf.click=()=>trace.push('wrong')");
      await assert.rejects(submitWebSessionExport(client, token), /CHANGED/);
      assert.deepEqual(await trace(), ["persisted-intent", "validate", "submit"]);
    });
    await t.test("task reader performs only bounded GETs and validates ordering and response shape", async () => {
      await page.evaluate(`window.jkUtils={jkAjax:async p=>{trace.push(p.type+':'+p.url);return {data:[{id:1,gmtCreate:1,taskTitle:'fixture',taskStatus:4,attachmentList:[]}],pageInfo:{total:500}}}}`);
      assert.equal((await readWebSessionTasks(client, "inventory", timestamp)).records.length, 1);
      assert.equal((await trace()).at(-1), "get:/jkyun/tms/taskmanage/sysTaskInfoList");
      await page.evaluate(`jkUtils.jkAjax=async()=>({data:[{id:1,gmtCreate:10,taskTitle:'first'},{id:2,gmtCreate:20,taskTitle:'newer'}],pageInfo:{total:2}})`);
      await assert.rejects(readWebSessionTasks(client, "inventory", timestamp), /ORDER_CHANGED/);
      await page.evaluate("jkUtils.jkAjax=async()=>({data:'login page'})");
      await assert.rejects(readWebSessionTasks(client, "inventory", timestamp), /INVALID/);
    });
    client.close();
  } finally { await browser.close(); }
});
