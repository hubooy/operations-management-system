import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { D1Database } from "@/lib/database/d1";
import {
  createWorkflowTaskAttachment,
  createWorkflowTaskComment,
  createWorkflowTaskLink,
  createWorkflowTaskReminder,
  createWorkflowTaskTemplate,
  deleteWorkflowTaskTemplate,
  deleteWorkflowTaskLink,
  deleteWorkflowTaskWithCollaboration,
  dismissWorkflowTaskReminder,
  ensureWorkflowCollaborationSchema,
  getWorkflowTaskAttachmentDownload,
  getWorkflowTaskCollaboration,
  listWorkflowTaskActivity,
  listWorkflowTaskReminders,
  listWorkflowTaskTemplates,
  runWorkflowAttachmentCleanup,
  setWorkflowAttachmentBucketForTest,
  updateWorkflowTaskTemplate,
} from "@/lib/workflow/collaboration";
import { createWorkflowTask, listWorkflowTasks, updateWorkflowTask } from "@/lib/workflow/tasks";

test("upgrades an old workflow database and persists collaboration records with bounded contracts", async () => {
  const sqlite = new DatabaseSync(":memory:"); sqlite.exec("PRAGMA foreign_keys = ON");
  const db = sqliteAdapter(sqlite);
  const task = (await listWorkflowTasks(db))[0];
  await ensureWorkflowCollaborationSchema(db);

  const comment = await createWorkflowTaskComment(task.id, "  请核对活动价。  ", "operator@example.com", db);
  assert.equal(comment.content, "请核对活动价。");
  const reminder = await createWorkflowTaskReminder(task.id, { remindAt: "2026-08-21T09:30:00+08:00", note: "晨会前" }, "operator@example.com", db);
  assert.equal(reminder.remindAt, "2026-08-21T01:30:00.000Z");
  const link = await createWorkflowTaskLink(task.id, { entityType: "product", entityId: "SKU-100", label: "测试商品", url: "https://example.com/products/100" }, "operator@example.com", db);

  const collaboration = await getWorkflowTaskCollaboration(task.id, db);
  assert.equal(collaboration.comments[0]?.id, comment.id);
  assert.equal(collaboration.reminders[0]?.id, reminder.id);
  assert.equal(collaboration.links[0]?.id, link.id);
  assert.equal(collaboration.attachments.length, 0);
  assert.deepEqual(new Set(collaboration.activity.map((item) => item.action)), new Set(["comment.created", "reminder.created", "link.created"]));

  assert.equal(await dismissWorkflowTaskReminder(task.id, reminder.id, "operator@example.com", db), true);
  assert.equal(await dismissWorkflowTaskReminder(task.id, reminder.id, "operator@example.com", db), false);
  assert.equal((await listWorkflowTaskReminders(task.id, db)).length, 0);
  assert.equal(await deleteWorkflowTaskLink(task.id, link.id, "operator@example.com", db), true);
  assert.equal(await deleteWorkflowTaskLink(task.id, link.id, "operator@example.com", db), false);
  const activity = await listWorkflowTaskActivity(task.id, db);
  assert.equal(activity.filter((item) => item.action === "reminder.dismissed").length, 1);
  assert.equal(activity.filter((item) => item.action === "link.deleted").length, 1);
  sqlite.close();
});

test("rejects malformed collaboration inputs and duplicate cross-entity identities", async () => {
  const sqlite = new DatabaseSync(":memory:"); const db = sqliteAdapter(sqlite); const task = (await listWorkflowTasks(db))[0];
  await assert.rejects(createWorkflowTaskComment(task.id, " ", "operator@example.com", db), /评论内容不能为空/);
  await assert.rejects(createWorkflowTaskReminder(task.id, { remindAt: "2026-08-21" }, "operator@example.com", db), /ISO 8601/);
  await assert.rejects(createWorkflowTaskLink(task.id, { entityType: "script", entityId: "1", label: "bad" }, "operator@example.com", db), /类型无效/);
  await assert.rejects(createWorkflowTaskLink(task.id, { entityType: "url", entityId: "1", label: "bad", url: "javascript:alert(1)" }, "operator@example.com", db), /HTTP/);
  await createWorkflowTaskLink(task.id, { entityType: "product", entityId: "SKU-1", label: "商品" }, "operator@example.com", db);
  await assert.rejects(createWorkflowTaskLink(task.id, { entityType: "product", entityId: "SKU-1", label: "重复" }, "operator@example.com", db), /已关联/);
  await assert.rejects(createWorkflowTaskReminder(task.id, { remindAt: "2026-02-30T09:00:00+08:00" }, "operator@example.com", db), /有效日期/);
  sqlite.close();
});

test("creates and updates reusable templates without accepting invalid offsets or states", async () => {
  const sqlite = new DatabaseSync(":memory:"); const db = sqliteAdapter(sqlite);
  const created = await createWorkflowTaskTemplate({ name: "巡店模板", category: "巡店检查", dueOffsetDays: 1, priority: "high" }, "operator@example.com", db);
  assert.ok(created); assert.equal(created?.active, true); assert.equal(created?.version, 1);
  const updated = await updateWorkflowTaskTemplate(created!.id, { expectedVersion: 1, active: false, description: "每日检查" }, "admin@example.com", db);
  assert.equal(updated?.active, false); assert.equal(updated?.description, "每日检查");
  assert.equal(updated?.version, 2);
  await assert.rejects(updateWorkflowTaskTemplate(created!.id, { expectedVersion: 1, description: "过期修改" }, "admin@example.com", db), /其他人更新/);
  assert.equal((await listWorkflowTaskTemplates(false, db)).length, 0);
  assert.equal((await listWorkflowTaskTemplates(true, db)).length, 1);
  await assert.rejects(createWorkflowTaskTemplate({ name: "坏模板", dueOffsetDays: 366 }, "operator@example.com", db), /-365 至 365/);
  await assert.rejects(createWorkflowTaskTemplate({ name: "错位模板", startOffsetDays: 3, dueOffsetDays: 2 }, "operator@example.com", db), /不能早于/);
  assert.equal(await deleteWorkflowTaskTemplate(created!.id, updated!.version, db), true);
  sqlite.close();
});

test("task creation and status changes atomically append lifecycle activity", async () => {
  const sqlite = new DatabaseSync(":memory:"); const db = sqliteAdapter(sqlite);
  const task = await createWorkflowTask({ title: "核对促销", startDate: "2026-08-20", due: "2026-08-21" }, "operator@example.com", db);
  await updateWorkflowTask(task.id, { expectedVersion: task.version, status: "工作中", owner: "运营组" }, "operator@example.com", db);
  const activity = await listWorkflowTaskActivity(task.id, db);
  assert.deepEqual(activity.map((item) => item.action).sort(), ["task.created", "task.status_changed"]);
  const statusActivity = activity.find((item) => item.action === "task.status_changed");
  assert.deepEqual(statusActivity?.metadata, { changedFields: ["owner", "status"], status: "工作中", version: 2 });
  sqlite.close();
});

test("validates attachment content, hides object keys, verifies downloads, and removes blobs with the task", async () => {
  const sqlite = new DatabaseSync(":memory:"); sqlite.exec("PRAGMA foreign_keys = ON"); const db = sqliteAdapter(sqlite); const task = (await listWorkflowTasks(db))[0];
  const objects = new Map<string, Uint8Array>();
  setWorkflowAttachmentBucketForTest({
    async put(key: string, value: ArrayBuffer | Uint8Array) { objects.set(key, value instanceof Uint8Array ? value.slice() : new Uint8Array(value)); },
    async get(key: string) { const value = objects.get(key); return value ? { body: new ReadableStream(), httpEtag: "test", async arrayBuffer() { return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength); } } : null; },
    async head(key: string) { return objects.has(key) ? {} : null; },
    async delete(keys: string | string[]) { for (const key of Array.isArray(keys) ? keys : [keys]) objects.delete(key); },
  } as unknown as R2Bucket);
  const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34])], "检查报告.pdf", { type: "application/pdf" });
  const attachment = await createWorkflowTaskAttachment(task.id, file, "operator@example.com", db);
  assert.equal(Object.hasOwn(attachment, "objectKey"), false);
  assert.doesNotMatch(JSON.stringify(attachment), /workflow-attachments/);
  assert.equal(objects.size, 1);
  const download = await getWorkflowTaskAttachmentDownload(task.id, attachment.id, db);
  assert.equal(download?.attachment.fileName, "检查报告.pdf"); assert.equal(download?.bytes.byteLength, 8);
  await assert.rejects(createWorkflowTaskAttachment(task.id, new File(["bad"], "../bad.txt", { type: "text/plain" }), "operator@example.com", db), /名称无效/);
  await assert.rejects(createWorkflowTaskAttachment(task.id, new File(["not pdf"], "bad.pdf", { type: "application/pdf" }), "operator@example.com", db), /内容与声明/);
  assert.equal(await deleteWorkflowTaskWithCollaboration(task.id, task.version, "operator@example.com", db), true); assert.equal(objects.size, 0);
  const deletedState = sqlite.prepare("SELECT version, deleted_at, deleted_by FROM workflow_task_states WHERE task_id = ?").get(task.id) as { version: number; deleted_at: string; deleted_by: string };
  assert.equal(Number(deletedState.version), task.version + 1); assert.ok(deletedState.deleted_at); assert.equal(deletedState.deleted_by, "operator@example.com");
  assert.equal(Number((sqlite.prepare("SELECT COUNT(*) AS count FROM workflow_tasks WHERE id = ?").get(task.id) as { count: number }).count), 1);
  assert.equal(Number((sqlite.prepare("SELECT COUNT(*) AS count FROM workflow_task_activity_logs WHERE task_id = ? AND action = 'task.deleted'").get(task.id) as { count: number }).count), 1);
  sqlite.close(); setWorkflowAttachmentBucketForTest(undefined);
});

test("persists an R2 cleanup outbox entry when both metadata save and immediate blob cleanup fail", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  let failNextBatch = false;
  const db = {
    prepare: base.prepare.bind(base),
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      if (failNextBatch) { failNextBatch = false; throw new Error("simulated_d1_failure"); }
      return base.batch(statements as never);
    },
  } as unknown as D1Database;
  const task = await createWorkflowTask({ title: "附件 outbox 回归" }, "operator@example.com", db);
  await ensureWorkflowCollaborationSchema(db);
  const objects = new Map<string, Uint8Array>();
  let failDelete = true;
  setWorkflowAttachmentBucketForTest({
    async put(key: string, value: ArrayBuffer | Uint8Array) { objects.set(key, value instanceof Uint8Array ? value.slice() : new Uint8Array(value)); },
    async get() { return null; }, async head() { return null; },
    async delete(key: string | string[]) {
      if (failDelete) throw new Error("private-storage-detail");
      for (const item of Array.isArray(key) ? key : [key]) objects.delete(item);
    },
  } as unknown as R2Bucket);
  failNextBatch = true;
  const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])], "outbox.pdf", { type: "application/pdf" });
  await assert.rejects(createWorkflowTaskAttachment(task.id, file, "operator@example.com", db), /simulated_d1_failure/);
  assert.equal(objects.size, 1);
  assert.equal(Number((sqlite.prepare("SELECT COUNT(*) AS count FROM workflow_attachment_cleanup_queue").get() as { count: number }).count), 1);
  failDelete = false;
  assert.deepEqual(await runWorkflowAttachmentCleanup({ limit: 10 }, db), { attempted: 1, deleted: 1, failed: 0, remaining: 0 });
  assert.equal(objects.size, 0);
  sqlite.close(); setWorkflowAttachmentBucketForTest(undefined);
});

test("0058 migration contains all collaboration tables and keeps object keys private", async () => {
  const migration = await readFile(new URL("../drizzle/0058_workflow_collaboration.sql", import.meta.url), "utf8");
  for (const table of ["workflow_task_states", "workflow_task_comments", "workflow_task_activity_logs", "workflow_task_reminders", "workflow_task_templates", "workflow_task_template_states", "workflow_task_entity_links", "workflow_task_attachments", "workflow_attachment_cleanup_queue"]) {
    assert.match(migration, new RegExp("CREATE TABLE IF NOT EXISTS `" + table + "`"));
  }
  assert.match(migration, /object_key/);
});

test("workflow collaboration routes are authenticated, operator-writable, and fail closed for restricted scopes", async () => {
  const routePaths = [
    "../app/api/workflow/tasks/route.ts",
    "../app/api/workflow/tasks/[taskId]/collaboration/route.ts",
    "../app/api/workflow/tasks/[taskId]/comments/route.ts",
    "../app/api/workflow/tasks/[taskId]/activity/route.ts",
    "../app/api/workflow/tasks/[taskId]/reminders/route.ts",
    "../app/api/workflow/tasks/[taskId]/links/route.ts",
    "../app/api/workflow/tasks/[taskId]/attachments/route.ts",
    "../app/api/workflow/tasks/[taskId]/attachments/[attachmentId]/route.ts",
    "../app/api/workflow/templates/route.ts",
  ];
  for (const path of routePaths) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.match(source, /requireAppPrincipal\(/, `${path} must authenticate`);
    assert.match(source, /requireUnrestrictedDataScope\(principal,/, `${path} must reject unsupported restricted scope`);
    assert.match(source, /authorizationErrorResponse\(error\)/, `${path} must preserve 401\/403 responses`);
  }
  for (const path of [
    "../app/api/workflow/tasks/route.ts",
    "../app/api/workflow/tasks/[taskId]/comments/route.ts",
    "../app/api/workflow/tasks/[taskId]/reminders/route.ts",
    "../app/api/workflow/tasks/[taskId]/links/route.ts",
    "../app/api/workflow/tasks/[taskId]/attachments/route.ts",
    "../app/api/workflow/templates/route.ts",
  ]) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.match(source, /requireAppPrincipal\(\["operator", "admin"\]\)/, `${path} must allow operator and admin writes`);
  }
  const taskDomain = await readFile(new URL("../lib/workflow/tasks.ts", import.meta.url), "utf8");
  const taskRoute = await readFile(new URL("../app/api/workflow/tasks/route.ts", import.meta.url), "utf8");
  assert.match(taskRoute, /rawQuery: new URL\(request\.url\)\.searchParams\.toString\(\)/,
    "the thin edge route must preserve the exact query string for Django validation");
  assert.doesNotMatch(taskRoute, /params\.get\("q"\)|params\.get\("query"\)/,
    "the edge route must not duplicate Django search parsing");
  assert.match(taskDomain, /task\.created/);
  assert.match(taskDomain, /task\.status_changed/);
  assert.match(taskDomain, /changedFields/);
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
      try { const results = []; for (const statement of statements) results.push(await statement.run()); sqlite.exec("COMMIT"); return results; }
      catch (error) { sqlite.exec("ROLLBACK"); throw error; }
    },
  } as unknown as D1Database;
}
