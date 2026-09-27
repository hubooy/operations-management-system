import assert from "node:assert/strict";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { D1Database } from "@/lib/database/d1";
import {
  createWorkflowTask,
  listWorkflowTasksPage,
  listWorkflowTasks,
  updateWorkflowTask,
} from "@/lib/workflow/tasks";

test("updates every editable work-item field and persists completed classification", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const original = (await listWorkflowTasks(db))[0];

  const updated = await updateWorkflowTask(original.id, {
    expectedVersion: original.version,
    title: "8 月运营复盘",
    workContent: "复核销售、退款与推广数据并完成复盘。",
    category: "数据分析",
    owner: "运营负责人",
    shopName: "京东-测试店铺",
    startDate: "2026-08-18",
    due: "2026-08-20",
    status: "已完成",
    priority: "high",
  }, "admin@example.com", db);

  assert.ok(updated);
  assert.equal(updated.version, original.version + 1);
  assert.deepEqual({
    title: updated.title,
    workContent: updated.workContent,
    category: updated.category,
    owner: updated.owner,
    shopName: updated.shopName,
    startDate: updated.startDate,
    due: updated.due,
    status: updated.status,
    priority: updated.priority,
  }, {
    title: "8 月运营复盘",
    workContent: "复核销售、退款与推广数据并完成复盘。",
    category: "数据分析",
    owner: "运营负责人",
    shopName: "京东-测试店铺",
    startDate: "2026-08-18",
    due: "2026-08-20",
    status: "已完成",
    priority: "high",
  });
  assert.equal((await listWorkflowTasks(db)).find((item) => item.id === original.id)?.status, "已完成");
  sqlite.close();
});

test("rejects invalid edit values without changing the saved work item", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const original = (await listWorkflowTasks(db))[0];

  await assert.rejects(
    updateWorkflowTask(original.id, { expectedVersion: original.version, startDate: "2026-08-20", due: "2026-08-19" }, "admin@example.com", db),
    /截止时间不能早于开始时间/,
  );
  await assert.rejects(
    updateWorkflowTask(original.id, { expectedVersion: original.version, priority: "urgent" }, "admin@example.com", db),
    /工作项紧急程度无效/,
  );

  const saved = (await listWorkflowTasks(db)).find((item) => item.id === original.id);
  assert.equal(saved?.startDate, original.startDate);
  assert.equal(saved?.due, original.due);
  assert.equal(saved?.priority, original.priority);
  sqlite.close();
});

test("filters and paginates tasks on the server while summary ignores only the status filter", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const marker = "唯一分页标记-20260820";
  await createWorkflowTask({ title: marker, owner: "分页测试人", due: "2026-08-25" }, "operator@example.com", db);
  const result = await listWorkflowTasksPage({
    query: marker,
    statuses: ["已完成"],
    dueFrom: "2026-08-25",
    dueTo: "2026-08-26",
    page: 1,
    pageSize: 1,
  }, db);
  assert.deepEqual(result.items, []);
  assert.deepEqual(result.pagination, { page: 1, pageSize: 1, total: 0, returned: 0, truncated: false });
  assert.deepEqual(result.summary, { total: 1, pending: 1, inProgress: 0, completed: 0, open: 1 });
  assert.deepEqual(result.filtersApplied.statuses, ["已完成"]);
  await assert.rejects(listWorkflowTasksPage({ dueFrom: "2026-02-30" }, db), /不是有效日期/);
  await assert.rejects(listWorkflowTasksPage({ page: 2_000, pageSize: 100 }, db), /分页偏移/);
  sqlite.close();
});

test("filters work items by category, shop and source and returns reusable facets", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const created = await createWorkflowTask({
    title: "新品节点检查",
    category: "新品上新",
    owner: "新品负责人",
    shopName: "京东-标准店铺",
    due: "2026-09-12",
  }, "operator@example.com", db);
  const manual = await listWorkflowTasksPage({
    categories: ["新品上新"],
    shopNames: ["京东-标准店铺"],
    sources: ["手动录入"],
    page: 1,
    pageSize: 20,
  }, db);
  assert.equal(manual.pagination.total, 1);
  assert.equal(manual.items[0]?.id, created.id);
  assert.ok(manual.facets.categories.includes("新品上新"));
  assert.ok(manual.facets.owners.includes("新品负责人"));
  assert.ok(manual.facets.shopNames.includes("京东-标准店铺"));
  assert.deepEqual(manual.facets.sources, ["系统预置", "手动录入"]);

  const systemOnly = await listWorkflowTasksPage({ sources: ["系统预置"], page: 1, pageSize: 100 }, db);
  assert.equal(systemOnly.items.some((item) => item.id === created.id), false);
  await assert.rejects(listWorkflowTasksPage({ sources: ["未知来源"] }, db), /来源包含无效值/);
  sqlite.close();
});

test("rejects stale and non-integer optimistic versions", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const original = (await listWorkflowTasks(db))[0];
  const updated = await updateWorkflowTask(original.id, {
    expectedVersion: original.version,
    owner: "新负责人",
  }, "admin@example.com", db);
  assert.equal(updated?.version, original.version + 1);
  await assert.rejects(updateWorkflowTask(original.id, {
    expectedVersion: original.version,
    owner: "过期请求",
  }, "admin@example.com", db), /其他人更新/);
  await assert.rejects(updateWorkflowTask(original.id, {
    expectedVersion: true,
    owner: "布尔版本",
  }, "admin@example.com", db), /预期版本/);
  sqlite.close();
});

function sqliteAdapter(sqlite: DatabaseSync): D1Database {
  return {
    prepare(sql: string) {
      let values: Array<string | number | bigint | Uint8Array | null> = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as typeof values; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() { const result = sqlite.prepare(sql).run(...values); return { meta: { changes: Number(result.changes) } }; },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  } as unknown as D1Database;
}
