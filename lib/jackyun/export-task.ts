import { createHash } from "node:crypto";
import type { JackyunModule } from "./post-download";

export type JackyunExportTaskBinding = {
  version: 1; taskId: string; label: string; createdAt: string; observedAt: string;
  module: JackyunModule; sourceRows: number; sourceUrlHash: string;
};
export type JackyunExportTaskRecord = { taskId: string; label: string; createdAt: number;
  completed: boolean; urls: string[] };
const labels: Record<JackyunModule, RegExp> = {
  inventory: /^【导出任务-(?:明文|密文)】分仓库存查询\((\d+)条\)$/,
  combos: /^【导出任务-(?:明文|密文)】组合装及子件导出\((\d+)条\)$/,
  sales: /^【导出任务-(?:明文|密文)】销售单明细账\((\d+)条\)$/,
  inventory_age: /^【导出任务-(?:明文|密文)】库龄分析(?:\(正式勿删\))?\((\d+)条\)$/,
  products: /^【导出任务-(?:明文|密文)】货品导出\((\d+)条\)$/,
};

export function matchJackyunExportTask(records: JackyunExportTaskRecord[], expected: {
  module: JackyunModule; sourceRows: number; exportIntentAt: string; observedAt: string;
}) {
  const intent = Date.parse(expected.exportIntentAt), observed = Date.parse(expected.observedAt);
  if (!Number.isFinite(intent) || !Number.isFinite(observed) || observed < intent
    || !Number.isSafeInteger(expected.sourceRows) || expected.sourceRows <= 0) throw new Error("导出任务绑定条件无效。");
  // The platform exposes gmtCreate with one-second precision.
  const start = Math.floor(intent / 1000) * 1000;
  const matches = records.filter(record => {
    const match = labels[expected.module].exec(record.label.replace(/[（）]/g, c => c === "（" ? "(" : ")").trim());
    return match && Number(match[1]) === expected.sourceRows && Number.isFinite(record.createdAt)
      && record.createdAt >= start && record.createdAt <= observed;
  });
  if (matches.length > 1) throw new Error("本轮同模块同数量导出任务不唯一，禁止自动选择文件。");
  return matches[0];
}

export function selectJackyunExportTask(records: JackyunExportTaskRecord[], expected: {
  module: JackyunModule; sourceRows: number; exportIntentAt: string; observedAt: string;
  allowedHosts: readonly string[]; binding?: JackyunExportTaskBinding;
}) {
  const task = matchJackyunExportTask(records, expected);
  if (!task || !task.completed) return null;
  if (!/^sys-\d{1,20}$/.test(task.taskId) || task.urls.length !== 1) throw new Error("导出任务身份或附件数量异常。");
  let url: URL;
  try { url = new URL(task.urls[0]); } catch { throw new Error("导出任务附件地址无效。"); }
  // The existing OSS downloader upgrades an observed HTTP link to HTTPS
  // before any network request; preserve its original URL hash for binding.
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.port || !expected.allowedHosts.includes(url.hostname)) {
    throw new Error("导出任务附件不属于允许的下载来源。");
  }
  const sourceUrlHash = createHash("sha256").update(url.toString()).digest("hex");
  const binding: JackyunExportTaskBinding = { version: 1, taskId: task.taskId, label: task.label,
    createdAt: new Date(task.createdAt).toISOString(), observedAt: expected.observedAt,
    module: expected.module, sourceRows: expected.sourceRows, sourceUrlHash };
  if (expected.binding && ["version", "taskId", "label", "createdAt", "module", "sourceRows", "sourceUrlHash"].some(
    key => expected.binding![key as keyof JackyunExportTaskBinding] !== binding[key as keyof JackyunExportTaskBinding])) {
    throw new Error("原导出任务或附件已变化，禁止替换。");
  }
  return { url: task.urls[0], binding: expected.binding ?? binding };
}
