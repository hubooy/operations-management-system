import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";

import type { AppPrincipal } from "@/lib/auth/authorization";
import type { D1Database } from "@/lib/database/d1";
import {
  createOperationRecord,
  deleteOperationRecord,
  ensureOperationRecordsSchema,
  getOperationRecord,
  listOperationRecordActivities,
  listOperationRecords,
  normalizeOperationRecordListInput,
  updateOperationRecord,
} from "@/lib/workflow/operations-records";

const admin: AppPrincipal = {
  email: "admin@example.com",
  displayName: "Admin",
  role: "admin",
  scope: null,
};
const operator: AppPrincipal = {
  email: "operator@example.com",
  displayName: "Operator",
  role: "operator",
  scope: null,
};

test("persists the remaining D1 operation record types with bounded filters and metadata-only activity", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const inspection = await createOperationRecord({
    type: "inspection",
    title: "价格与库存巡检",
    status: "待处理",
    priority: "high",
    platform: "京东",
    channel: "线上",
    shopName: "京东-测试店",
    owner: "运营组",
    occurredAt: "2026-08-20T10:20:00+08:00",
    dueAt: "2026-08-20",
    content: "2 个 SKU 库存偏低",
    source: "manual",
    sourceRef: "inspection-20",
  }, operator, db);
  await createOperationRecord({
    type: "review", title: "二星评价", status: "待回复", platform: "天猫", channel: "线上",
    shopName: "天猫-测试店", owner: "客服组", occurredAt: "2026-08-19", content: "包装有破损",
  }, operator, db);
  await assert.rejects(createOperationRecord({
    type: "launch", title: "新品净水机", status: "待开始", platform: "京东", channel: "线上",
    shopName: "京东-测试店", owner: "商品组", occurredAt: "2026-08-18",
  }, operator, db), /类型无效/);

  const result = await listOperationRecords({
    types: ["inspection"], statuses: ["待处理"], query: "库存", page: 1, pageSize: 1,
  }, admin, db);
  assert.equal(result.pagination.total, 1);
  assert.equal(result.pagination.returned, 1);
  assert.equal(result.pagination.truncated, false);
  assert.equal(result.items[0]?.id, inspection.id);
  assert.equal(result.items[0]?.source, "manual");
  assert.equal(result.items[0]?.occurredAt, "2026-08-20T02:20:00.000Z");
  assert.equal(result.items[0]?.dueAt, "2026-08-19T16:00:00.000Z");
  const ranged = await listOperationRecords({ from: "2026-08-20", to: "2026-08-21" }, admin, db);
  assert.equal(ranged.items.some((item) => item.id === inspection.id), true);
  const afterStructuredCutover = await listOperationRecords({}, admin, db);
  assert.equal(afterStructuredCutover.pagination.total, 2);
  assert.equal(afterStructuredCutover.items.some((item) => String(item.type) === "launch"), false);
  await assert.rejects(listOperationRecords({ types: ["launch"] }, admin, db), /类型包含无效值/);

  const activity = await listOperationRecordActivities(inspection.id, {}, admin, db);
  assert.equal(activity.items.length, 1);
  assert.equal(activity.items[0]?.action, "created");
  assert.equal(activity.items[0]?.actorRole, "operator");
  const rawDetail = sqlite.prepare("SELECT detail_json FROM workflow_operation_activities WHERE record_id = ?").get(inspection.id) as { detail_json: string };
  assert.doesNotMatch(rawDetail.detail_json, /2 个 SKU/);
  sqlite.close();
});

test("enforces type-specific status, hard page limits, source permission and platform scope", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await assert.rejects(createOperationRecord({
    type: "review", title: "评价", status: "正常", shopName: "测试店", occurredAt: "2026-08-20",
  }, operator, db), /review 的状态无效/);
  await assert.rejects(createOperationRecord({
    type: "review", title: "评价", status: "待回复", shopName: "测试店", occurredAt: "2026-08-20", source: "import",
  }, operator, db), /只有管理员/);
  assert.throws(() => normalizeOperationRecordListInput({ pageSize: 101 }), /1 到 100/);
  assert.throws(() => normalizeOperationRecordListInput({ page: true }), /1 到/);
  assert.throws(() => normalizeOperationRecordListInput({ types: Array.from({ length: 21 }, () => "review") }), /最多允许 20 项/);
  assert.throws(() => normalizeOperationRecordListInput({ from: "2026-02-30" }), /不是有效日期/);
  assert.throws(() => normalizeOperationRecordListInput({ from: "2026-08-21", to: "2026-08-20" }), /开始时间早于/);
  assert.throws(() => normalizeOperationRecordListInput({ page: 2_000, pageSize: 100 }), /分页偏移/);
  await assert.rejects(createOperationRecord({
    type: "review", title: "无时区评价", status: "待回复", shopName: "测试店", occurredAt: "2026-08-20T10:20",
  }, operator, db), /包含时区/);
  await assert.rejects(createOperationRecord({
    type: "review", title: "未知字段", status: "待回复", shopName: "测试店", occurredAt: "2026-08-20",
    unexpected: "value",
  } as never, operator, db), /不支持的字段/);

  const jdScope: AppPrincipal = {
    ...operator,
    scope: { warehouses: [], channels: [], platforms: ["京东"] },
  };
  const record = await createOperationRecord({
    type: "review", title: "京东评价", status: "待回复", platform: "京东",
    shopName: "京东-测试店", occurredAt: "2026-08-20",
  }, jdScope, db);
  await assert.rejects(createOperationRecord({
    type: "review", title: "天猫评价", status: "待回复", platform: "天猫",
    shopName: "天猫-测试店", occurredAt: "2026-08-20",
  }, jdScope, db), /不能写入该平台/);
  await assert.rejects(createOperationRecord({
    type: "review", title: "空平台评价", status: "待回复", platform: "", channel: "",
    shopName: "未绑定店", occurredAt: "2026-08-20T10:20:00+08:00",
  }, jdScope, db), /不能写入该平台/);
  assert.equal((await listOperationRecords({}, jdScope, db)).items.map((item) => item.id).includes(record.id), true);
  const emptyScope: AppPrincipal = { ...operator, scope: { warehouses: ["一号仓"], channels: [], platforms: [] } };
  assert.equal((await listOperationRecords({}, emptyScope, db)).pagination.total, 0);
  assert.equal(await getOperationRecord(record.id, emptyScope, db), null);
  const manyPlatforms = Array.from({ length: 25 }, (_, index) => `P${index + 1}`);
  const lastPlatformRecord = await createOperationRecord({
    type: "review", title: "第二十五个平台", status: "待回复", platform: "P25",
    shopName: "P25-测试店", occurredAt: "2026-08-20",
  }, admin, db);
  const wideScope: AppPrincipal = { ...operator, scope: { warehouses: [], channels: [], platforms: manyPlatforms } };
  assert.equal((await listOperationRecords({}, wideScope, db)).items.some((item) => item.id === lastPlatformRecord.id), true);
  sqlite.close();
});

test("optimistic updates and soft deletion remain atomic and auditable", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const item = await createOperationRecord({
    type: "review", title: "评价项目", status: "待回复", platform: "京东", shopName: "京东-测试店",
    owner: "客服组", occurredAt: "2026-08-20", dueAt: "2026-08-25", content: "等待资料",
  }, operator, db);
  await assert.rejects(updateOperationRecord(item.id, { title: "缺版本" }, operator, db), /预期版本不能为空/);
  await assert.rejects(updateOperationRecord(item.id, { expectedVersion: true, title: "布尔版本" }, operator, db), /预期版本/);
  const updated = await updateOperationRecord(item.id, {
    expectedVersion: 1, status: "处理中", content: "资料审核中",
  }, operator, db);
  assert.equal(updated.version, 2);
  assert.equal(updated.status, "处理中");
  await assert.rejects(updateOperationRecord(item.id, {
    expectedVersion: 1, owner: "旧请求",
  }, operator, db), /其他人更新/);
  assert.equal(Number((sqlite.prepare("SELECT COUNT(*) AS count FROM workflow_operation_activities WHERE record_id = ?").get(item.id) as { count: number }).count), 2);

  await assert.rejects(deleteOperationRecord(item.id, 1, operator, db), /其他人更新/);
  const deleted = await deleteOperationRecord(item.id, 2, operator, db);
  assert.deepEqual(deleted, { id: item.id, deleted: true, version: 3 });
  assert.equal(await getOperationRecord(item.id, admin, db), null);
  assert.equal((await listOperationRecords({}, admin, db)).pagination.total, 0);
  assert.equal((await listOperationRecordActivities(item.id, {}, admin, db)).items[0]?.action, "deleted");
  const actions = (sqlite.prepare("SELECT action, to_version FROM workflow_operation_activities WHERE record_id = ? ORDER BY to_version").all(item.id) as Array<{ action: string; to_version: number }>)
    .map((row) => ({ action: row.action, to_version: row.to_version }));
  assert.deepEqual(actions, [
    { action: "created", to_version: 1 },
    { action: "status_changed", to_version: 2 },
    { action: "deleted", to_version: 3 },
  ]);
  sqlite.close();
});

test("runtime schema upgrades an old workflow database without changing existing tasks", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("CREATE TABLE workflow_tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL); INSERT INTO workflow_tasks VALUES ('old', '保留事项')");
  const db = sqliteAdapter(sqlite);
  await ensureOperationRecordsSchema(db);
  assert.equal((sqlite.prepare("SELECT title FROM workflow_tasks WHERE id = 'old'").get() as { title: string }).title, "保留事项");
  const tables = sqlite.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'workflow_operation_%' ORDER BY name").all() as Array<{ name: string }>;
  assert.deepEqual(tables.map((row) => row.name), ["workflow_operation_activities", "workflow_operation_records"]);
  await ensureOperationRecordsSchema(db);
  sqlite.close();
});

test("migration and API routes preserve schema, role and no-store contracts", async () => {
  const [migration, collectionRoute, itemRoute, activityRoute] = await Promise.all([
    readFile(new URL("../drizzle/0059_workflow_operations_records.sql", import.meta.url), "utf8"),
    readFile(new URL("../app/api/workflow/operations-records/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/workflow/operations-records/[id]/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/workflow/operations-records/[id]/activity/route.ts", import.meta.url), "utf8"),
  ]);
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(migration);
  sqlite.exec(migration);
  assert.equal(Number((sqlite.prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name LIKE 'workflow_operation_%'").get() as { count: number }).count), 2);
  sqlite.close();
  const runtimeSqlite = new DatabaseSync(":memory:");
  await ensureOperationRecordsSchema(sqliteAdapter(runtimeSqlite));
  runtimeSqlite.exec(migration);
  runtimeSqlite.exec(migration);
  assert.equal(Number((runtimeSqlite.prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name LIKE 'workflow_operation_%'").get() as { count: number }).count), 2);
  runtimeSqlite.close();
  assert.match(collectionRoute, /requireAppPrincipal\(\["viewer", "analyst", "operator", "admin"\]\)/);
  assert.match(collectionRoute, /requireAppPrincipal\(\["operator", "admin"\]\)/);
  assert.match(itemRoute, /requireAppPrincipal\(\["operator", "admin"\]\)/);
  assert.match(activityRoute, /requireAppPrincipal\(\["viewer", "analyst", "operator", "admin"\]\)/);
  for (const route of [collectionRoute, itemRoute, activityRoute]) {
    assert.match(route, /getWorkflowBackendMode/);
    assert.match(route, /createDjangoWorkflowService/);
    assert.match(route, /workflowServiceResponse/);
    assert.doesNotMatch(route, /getD1Database|@\/lib\/workflow\/operations-records/);
  }
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
