import { randomUUID } from "node:crypto";
import { evaluateValue, type BrowserAutomationClient } from "./cdp-client";
import { matchJackyunExportTask, selectJackyunExportTask, type JackyunExportTaskBinding, type JackyunExportTaskRecord } from "./export-task";
import type { JackyunModule } from "./post-download";

/** Uses the authenticated website's own validators/signing code; no Cookie is serialized. */
export const jackyunWebSessionTransport = "web_session_batch_v1";
const surfaces: Record<JackyunModule, { path: string; grid: string; parent: string; leaf: string }> = {
  inventory: { path: "/erp_stock/goods_stock/branch_stock_main_v4.html", grid: "datagrid-goods-stock", parent: "export", leaf: "exportAll" },
  combos: { path: "/erp/goods_file_erp/goods_managet_combination_v2.html", grid: "grid-goods_managet", parent: "exportGoodsCombinationDetail", leaf: "exportAll_2" },
  sales: { path: "/oms/order/order_detail_list.html", grid: "gridOrderDetail", parent: "export", leaf: "exportAll" },
  inventory_age: { path: "/report/interface_design/table_form.html", grid: "mainGrid", parent: "export", leaf: "exportAll" },
  products: { path: "/erp/goods_file_erp/goods_managet_v3.html", grid: "grid-goods_managet", parent: "export", leaf: "exportAll" },
};
const labels: Record<JackyunModule, string> = {
  inventory: "分仓库存查询", combos: "组合装及子件导出", sales: "销售单明细账", inventory_age: "库龄分析", products: "货品导出",
};

export async function prepareWebSessionExport(client: BrowserAutomationClient, module: JackyunModule) {
  const token = randomUUID();
  const result = await evaluateValue<boolean>(client, `(() => {
    const spec = ${JSON.stringify(surfaces[module])};
    const candidates = [];
    const visit = doc => {
      if (doc.location.origin !== 'https://web.jackyun.com') return;
      if (doc.location.pathname === spec.path) {
        const win = doc.defaultView;
        const grids = spec.grid ? [win.mini?.get?.(spec.grid)] : Object.values(win.mini?.components || {});
        for (const grid of new Set(grids.filter(Boolean))) {
          if (spec.grid === 'mainGrid') {
            const fields = (grid.getColumns?.() || []).map(c => c.field);
            if (!['warehouseName', 'goodsNo', 'stockAge'].every(f => fields.includes(f))) continue;
          }
          const parents = [...new Set([...(grid.contextMenuItems || []), ...(grid.customMenuItems || [])])].filter(m => m.name === spec.parent);
          if (parents.length !== 1 || parents[0].disabled || parents[0].visible === false) continue;
          const leaves = (parents[0].children || []).filter(m => m.name === spec.leaf && /导出所有页/.test(m.text || '') && !/分表/.test(m.text || ''));
          if (leaves.length !== 1 || leaves[0].disabled || leaves[0].visible === false || typeof leaves[0].click !== 'function') continue;
          if (!Array.isArray(grid.getData?.()) || !grid.getData().length) continue;
          candidates.push({ doc, grid, parent: parents[0], item: leaves[0], fn: leaves[0].click });
        }
      }
      for (const frame of doc.querySelectorAll('iframe,frame')) {
        const r = frame.getBoundingClientRect(), s = doc.defaultView.getComputedStyle(frame);
        if (r.width < 100 || r.height < 100 || s.display === 'none' || s.visibility === 'hidden') continue;
        try { if (frame.contentDocument) visit(frame.contentDocument); } catch {}
      }
    };
    visit(document);
    if (candidates.length !== 1) throw new Error('WEB_EXPORT_NOT_READY: unique enabled module export API required');
    const candidate = candidates[0];
    globalThis.__teruisiWebExport = { token: ${JSON.stringify(token)}, spec, candidate, consumed: false };
    return true;
  })()`);
  if (!result) throw new Error("WEB_EXPORT_NOT_READY：网页导出能力未就绪。");
  return token;
}

/** Caller must atomically persist export intent before invoking this single-use handle. */
export async function submitWebSessionExport(client: BrowserAutomationClient, token: string) {
  return evaluateValue<boolean>(client, `(() => {
    const h = globalThis.__teruisiWebExport;
    if (!h || h.token !== ${JSON.stringify(token)} || h.consumed) throw new Error('WEB_EXPORT_ALREADY_CONSUMED');
    const c = h.candidate;
    if (c.doc.location.origin !== 'https://web.jackyun.com' || c.doc.location.pathname !== h.spec.path
      || c.item.click !== c.fn || c.item.disabled || c.parent.disabled || c.item.visible === false || c.parent.visible === false
      || !c.grid.getData?.().length) throw new Error('WEB_EXPORT_CONTEXT_CHANGED');
    h.consumed = true;
    // The same handler as the enabled all-pages menu runs its original validation.
    // Never replace validators, select a plaintext export, or fall back to another click.
    c.fn.call(c.item, c.grid);
    return true;
  })()`);
}

export type WebTaskSnapshot = { records: JackyunExportTaskRecord[]; failedIds: string[] };
export async function readWebSessionTasks(client: BrowserAutomationClient, module: JackyunModule, since = new Date().toISOString()): Promise<WebTaskSnapshot> {
  const sinceMs = Math.floor(Date.parse(since) / 1000) * 1000;
  if (!Number.isFinite(sinceMs)) throw new Error("网页任务查询时间无效。");
  return evaluateValue<WebTaskSnapshot>(client, `(async () => {
    if (location.origin !== 'https://web.jackyun.com' || typeof jkUtils?.jkAjax !== 'function') throw new Error('WEB_SESSION_REQUIRED');
    const records = [], failedIds = [];
    // The website's /system/taskList.js uses this GET, pageSize=10 and keyWords.
    // Read the newest-first window through its lower time boundary (at most 100).
    for (let pageIndex = 0; pageIndex < 10; pageIndex++) {
      const res = await jkUtils.jkAjax({ url: '/jkyun/tms/taskmanage/sysTaskInfoList', type: 'get',
        data: { pageIndex, pageSize: 10, timeStamp: Date.now(), keyWords: ${JSON.stringify(labels[module])} }, timeout: 15000 });
      if (!res || !Array.isArray(res.data) || res.data.length > 10 || !Number.isSafeInteger(res.pageInfo?.total)
        || res.pageInfo.total < 0) throw new Error('WEB_TASK_LIST_INVALID_OR_TRUNCATED');
      for (const row of res.data) {
        if (!/^\\d{1,20}$/.test(String(row.id)) || !Number.isFinite(Number(row.gmtCreate)) || typeof row.taskTitle !== 'string') throw new Error('WEB_TASK_RECORD_INVALID');
        const taskId = 'sys-' + row.id;
        if (records.some(r => r.taskId === taskId)) throw new Error('WEB_TASK_PAGINATION_CHANGED');
        if (records.length && Number(row.gmtCreate) > records[records.length - 1].createdAt) throw new Error('WEB_TASK_ORDER_CHANGED');
        records.push({ taskId, label: row.taskTitle.replace(/^【成功】|【失败】/, ''), createdAt: Number(row.gmtCreate),
          completed: Number(row.taskStatus) === 4, urls: (row.attachmentList || []).map(a => String(a.attachmentUrl || '')) });
        if (Number(row.taskStatus) === 5) failedIds.push(taskId);
      }
      if (records.length === res.pageInfo.total || records.some(r => r.createdAt < ${sinceMs})) return { records, failedIds };
      if (res.data.length < 10) throw new Error('WEB_TASK_PAGINATION_CHANGED');
    }
    throw new Error('WEB_TASK_LIST_TRUNCATED');
  })()`, 170_000);
}

export function selectNewWebSessionTask(snapshot: WebTaskSnapshot, expected: {
  module: JackyunModule; sourceRows: number; exportIntentAt: string; observedAt: string;
  allowedHosts: readonly string[]; baselineIds: string[]; pendingTaskId?: string; binding?: JackyunExportTaskBinding;
}) {
  const records = snapshot.records.filter(r => !expected.baselineIds.includes(r.taskId));
  const taskId = matchJackyunExportTask(records, expected)?.taskId;
  if (expected.pendingTaskId && taskId !== expected.pendingTaskId) throw new Error("原网页导出任务消失或被替换。");
  if (taskId && snapshot.failedIds.includes(taskId)) throw new Error("网页导出任务已明确失败，保留任务记录，禁止自动重新提交。");
  const result = selectJackyunExportTask(records, expected);
  return { taskId, result };
}

export async function waitForWebSessionTask(client: BrowserAutomationClient, expected: Parameters<typeof selectNewWebSessionTask>[1],
  options: { timeoutMs: number; onTask: (taskId: string) => Promise<void>; signal?: AbortSignal;
    readTasks?: (module: JackyunModule, since: string) => Promise<WebTaskSnapshot> }) {
  const deadline = Date.now() + options.timeoutMs;
  let poll = 0;
  while (Date.now() < deadline) {
    options.signal?.throwIfAborted();
    const snapshot = await (options.readTasks ? options.readTasks(expected.module, expected.exportIntentAt)
      : readWebSessionTasks(client, expected.module, expected.exportIntentAt));
    const selected = selectNewWebSessionTask(snapshot, { ...expected, observedAt: new Date().toISOString() });
    if (selected.taskId && !expected.pendingTaskId) {
      await options.onTask(selected.taskId);
      expected = { ...expected, pendingTaskId: selected.taskId };
    }
    if (selected.result) return selected.result;
    await new Promise(resolve => setTimeout(resolve, Math.min(1000 + poll++ * 500, 5000)));
  }
  throw new Error("网页导出等待超时：原提交和任务身份已保留，禁止自动重新导出。");
}
