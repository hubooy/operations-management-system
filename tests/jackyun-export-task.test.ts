import assert from "node:assert/strict";
import test from "node:test";
import { selectJackyunExportTask, type JackyunExportTaskRecord } from "../lib/jackyun/export-task";

const expected = { module: "inventory" as const, sourceRows: 25709, exportIntentAt: "2026-09-06T13:35:05.712Z",
  observedAt: "2026-09-06T13:36:00.000Z", allowedHosts: ["oss.example.invalid"] };
const record: JackyunExportTaskRecord = { taskId: "sys-12345678", label: "【导出任务-密文】分仓库存查询(25709条)",
  createdAt: Date.parse("2026-09-06T13:35:05.000Z"), completed: true, urls: ["http://oss.example.invalid/fixture.xlsx?signature=fixture"] };

test("task binding selects the exact current module, count and server second; URLs stay out of its durable receipt", () => {
  const selected = selectJackyunExportTask([{ ...record, createdAt: record.createdAt - 1000 }, record], expected)!;
  assert.equal(selected.binding.taskId, record.taskId);
  assert.equal(selected.url, record.urls[0]);
  assert.ok(!JSON.stringify(selected.binding).includes("signature"));
  assert.deepEqual(selectJackyunExportTask([record], { ...expected, binding: selected.binding })?.binding, selected.binding);
  for (const bad of [{ createdAt: record.createdAt - 1000 }, { createdAt: Date.parse(expected.observedAt) + 1 },
    { label: "【导出任务-密文】分仓库存查询(25708条)" }, { label: "【导出任务-密文】货品导出(25709条)" }, { completed: false }]) {
    assert.equal(selectJackyunExportTask([{ ...record, ...bad }], expected), null);
  }
});

test("ambiguous tasks, extra attachments, unsafe URLs and changes to a pinned task stop before download", () => {
  assert.throws(() => selectJackyunExportTask([record, { ...record, taskId: "sys-999" }], expected), /不唯一/);
  const binding = selectJackyunExportTask([record], expected)!.binding;
  for (const bad of [{ taskId: "other" }, { urls: [] }, { urls: [...record.urls, ...record.urls] },
    { urls: ["https://evil.invalid/a.xlsx"] }, { urls: ["file:///tmp/a.xlsx"] },
    { urls: ["https://user:password@oss.example.invalid/a.xlsx"] }, { urls: ["https://oss.example.invalid:1234/a.xlsx"] }]) {
    assert.throws(() => selectJackyunExportTask([{ ...record, ...bad }], expected));
  }
  for (const bad of [{ taskId: "sys-999" }, { urls: ["https://oss.example.invalid/changed.xlsx"] }]) {
    assert.throws(() => selectJackyunExportTask([{ ...record, ...bad }], { ...expected, binding }), /已变化/);
  }
});
