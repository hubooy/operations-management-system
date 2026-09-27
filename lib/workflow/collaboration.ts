import type { D1Database } from "@/lib/database/d1";
import {
  invalidWorkflowRequest,
  missingWorkflowResource,
  WorkflowRequestError,
  workflowVersionConflict,
} from "@/lib/workflow/errors";
import { ensureWorkflowCollaborationSchema } from "@/lib/workflow/schema";

export { ensureWorkflowCollaborationSchema };

export const workflowEntityTypes = ["shop", "product", "campaign", "order", "report", "url"] as const;
export type WorkflowEntityType = (typeof workflowEntityTypes)[number];

export type WorkflowTaskComment = {
  id: string;
  taskId: string;
  content: string;
  createdBy: string;
  createdAt: string;
};

export type WorkflowTaskActivity = {
  id: string;
  taskId: string;
  action: string;
  summary: string;
  metadata: Record<string, unknown>;
  actorEmail: string;
  createdAt: string;
};

export type WorkflowTaskReminder = {
  id: string;
  taskId: string;
  remindAt: string;
  note: string;
  status: "pending" | "dismissed" | "sent";
  createdBy: string;
  createdAt: string;
  updatedAt: string;
};

export type WorkflowTaskEntityLink = {
  id: string;
  taskId: string;
  entityType: WorkflowEntityType;
  entityId: string;
  label: string;
  url: string;
  createdBy: string;
  createdAt: string;
};

export type WorkflowTaskAttachment = {
  id: string;
  taskId: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  createdBy: string;
  createdAt: string;
  downloadUrl: string;
};

export type WorkflowTaskTemplate = {
  id: string;
  name: string;
  description: string;
  title: string;
  workContent: string;
  category: string;
  owner: string;
  shopName: string;
  startOffsetDays: number;
  dueOffsetDays: number;
  priority: "high" | "normal" | "low";
  active: boolean;
  version: number;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

type CommentRow = { id: string; task_id: string; content: string; created_by: string; created_at: string };
type ActivityRow = { id: string; task_id: string; action: string; summary: string; metadata_json: string; actor_email: string; created_at: string };
type ReminderRow = { id: string; task_id: string; remind_at: string; note: string; status: string; created_by: string; created_at: string; updated_at: string };
type LinkRow = { id: string; task_id: string; entity_type: string; entity_id: string; label: string; url: string; created_by: string; created_at: string };
type AttachmentRow = { id: string; task_id: string; file_name: string; mime_type: string; size_bytes: number; sha256: string; object_key: string; created_by: string; created_at: string };
type TemplateRow = { id: string; name: string; description: string; title: string; work_content: string; category: string; owner: string; shop_name: string; start_offset_days: number; due_offset_days: number; priority: string; active: number; created_by: string; updated_by: string; created_at: string; updated_at: string; version: number };

const MAX_COMMENT_LENGTH = 2_000;
export const MAX_WORKFLOW_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const ATTACHMENT_PREFIX = "workflow-attachments";

async function workflowDatabase(db?: D1Database) {
  if (db) return db;
  const { getD1Database } = await import("@/lib/database/d1");
  return getD1Database();
}

function boundedText(value: unknown, field: string, maxLength: number, required = true) {
  const text = typeof value === "string" ? value.trim() : "";
  if (value !== undefined && value !== null && typeof value !== "string") invalidWorkflowRequest(`${field}必须是文本`);
  if (required && !text) invalidWorkflowRequest(`${field}不能为空`);
  if (Array.from(text).length > maxLength) invalidWorkflowRequest(`${field}不能超过 ${maxLength} 个字符`);
  return text;
}

function resourceId(value: unknown, label: string) {
  const id = boundedText(value, label, 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(id)) invalidWorkflowRequest(`${label}格式无效`);
  return id;
}

function strictExpectedVersion(value: unknown) {
  const parsed = typeof value === "number" ? value
    : typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : Number.NaN;
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > 2_147_483_646) {
    invalidWorkflowRequest("预期版本必须是正整数");
  }
  return parsed;
}

function assertInputKeys(input: object, allowed: readonly string[]) {
  const allowlist = new Set(allowed);
  const unknown = Object.keys(input).filter((key) => !allowlist.has(key));
  if (unknown.length > 0) invalidWorkflowRequest(`包含不支持的字段：${unknown.slice(0, 5).join("、")}`);
}

function actorEmail(value: string) {
  return boundedText(value, "操作人", 320);
}

function metadataJson(value: Record<string, unknown> = {}) {
  const json = JSON.stringify(value);
  if (json.length > 4_000) invalidWorkflowRequest("活动详情过长");
  return json;
}

function activityStatement(db: D1Database, taskId: string, action: string, summary: string, actor: string, metadata: Record<string, unknown> = {}) {
  return db.prepare(`INSERT INTO workflow_task_activity_logs
    (id, task_id, action, summary, metadata_json, actor_email) VALUES (?, ?, ?, ?, ?, ?)`)
    .bind(crypto.randomUUID(), taskId, boundedText(action, "活动类型", 80), boundedText(summary, "活动摘要", 300), metadataJson(metadata), actorEmail(actor));
}

export async function appendWorkflowTaskActivity(input: {
  taskId: string;
  action: string;
  summary: string;
  actorEmail: string;
  metadata?: Record<string, unknown>;
}, db?: D1Database) {
  const database = await workflowDatabase(db);
  await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(input.taskId, "工作项标识");
  await assertTaskExists(database, taskId);
  await activityStatement(database, taskId, input.action, input.summary, input.actorEmail, input.metadata).run();
}

async function assertTaskExists(db: D1Database, taskId: string) {
  const row = await db.prepare(`SELECT t.id FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
    WHERE t.id = ? AND s.deleted_at IS NULL LIMIT 1`).bind(taskId).first<{ id: string }>();
  if (!row) missingWorkflowResource("工作项不存在或已删除");
}

function mapComment(row: CommentRow): WorkflowTaskComment {
  return { id: row.id, taskId: row.task_id, content: row.content, createdBy: row.created_by, createdAt: row.created_at };
}

function mapActivity(row: ActivityRow): WorkflowTaskActivity {
  let metadata: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(row.metadata_json) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) metadata = parsed as Record<string, unknown>;
  } catch { /* Invalid legacy metadata remains safely empty. */ }
  return { id: row.id, taskId: row.task_id, action: row.action, summary: row.summary, metadata, actorEmail: row.actor_email, createdAt: row.created_at };
}

function mapReminder(row: ReminderRow): WorkflowTaskReminder {
  const status = row.status === "dismissed" || row.status === "sent" ? row.status : "pending";
  return { id: row.id, taskId: row.task_id, remindAt: row.remind_at, note: row.note, status, createdBy: row.created_by, createdAt: row.created_at, updatedAt: row.updated_at };
}

function mapLink(row: LinkRow): WorkflowTaskEntityLink {
  const entityType = workflowEntityTypes.includes(row.entity_type as WorkflowEntityType) ? row.entity_type as WorkflowEntityType : "url";
  return { id: row.id, taskId: row.task_id, entityType, entityId: row.entity_id, label: row.label, url: row.url, createdBy: row.created_by, createdAt: row.created_at };
}

function mapAttachment(row: AttachmentRow): WorkflowTaskAttachment {
  return {
    id: row.id, taskId: row.task_id, fileName: row.file_name, mimeType: row.mime_type,
    sizeBytes: Number(row.size_bytes), sha256: row.sha256, createdBy: row.created_by, createdAt: row.created_at,
    downloadUrl: `/api/workflow/tasks/${encodeURIComponent(row.task_id)}/attachments/${encodeURIComponent(row.id)}`,
  };
}

function mapTemplate(row: TemplateRow): WorkflowTaskTemplate {
  const priority = row.priority === "high" || row.priority === "low" ? row.priority : "normal";
  return {
    id: row.id, name: row.name, description: row.description, title: row.title, workContent: row.work_content,
    category: row.category, owner: row.owner, shopName: row.shop_name, startOffsetDays: Number(row.start_offset_days),
    dueOffsetDays: Number(row.due_offset_days), priority, active: Number(row.active) === 1, version: Number(row.version),
    createdBy: row.created_by, updatedBy: row.updated_by, createdAt: row.created_at, updatedAt: row.updated_at,
  };
}

export async function listWorkflowTaskComments(taskIdValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const rows = await database.prepare(`SELECT id, task_id, content, created_by, created_at FROM workflow_task_comments
    WHERE task_id = ? ORDER BY created_at ASC, id ASC LIMIT 500`).bind(taskId).all<CommentRow>();
  return rows.results.map(mapComment);
}

export async function createWorkflowTaskComment(taskIdValue: unknown, contentValue: unknown, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const content = boundedText(contentValue, "评论内容", MAX_COMMENT_LENGTH); const id = crypto.randomUUID();
  await database.batch([
    database.prepare("INSERT INTO workflow_task_comments (id, task_id, content, created_by) VALUES (?, ?, ?, ?)").bind(id, taskId, content, actorEmail(actor)),
    activityStatement(database, taskId, "comment.created", "添加了评论", actor, { commentId: id }),
  ]);
  const row = await database.prepare("SELECT id, task_id, content, created_by, created_at FROM workflow_task_comments WHERE id = ?").bind(id).first<CommentRow>();
  if (!row) throw new Error("评论保存后无法读取");
  return mapComment(row);
}

export async function listWorkflowTaskActivity(taskIdValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const rows = await database.prepare(`SELECT id, task_id, action, summary, metadata_json, actor_email, created_at
    FROM workflow_task_activity_logs WHERE task_id = ? ORDER BY created_at DESC, id DESC LIMIT 500`).bind(taskId).all<ActivityRow>();
  return rows.results.map(mapActivity);
}

function reminderTime(value: unknown) {
  const text = boundedText(value, "提醒时间", 40);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|([+-])(\d{2}):(\d{2}))$/.exec(text);
  if (!match) {
    invalidWorkflowRequest("提醒时间必须是包含时区的 ISO 8601 格式");
  }
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map((item) => Number(item ?? 0));
  const calendar = new Date(Date.UTC(year, month - 1, day));
  const offsetHour = Number(match[10] ?? 0); const offsetMinute = Number(match[11] ?? 0);
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day
    || hour > 23 || minute > 59 || second > 59 || offsetHour > 14 || offsetMinute > 59
    || (offsetHour === 14 && offsetMinute !== 0)) invalidWorkflowRequest("提醒时间不是有效日期时间");
  const timestamp = Date.parse(text);
  if (!Number.isFinite(timestamp)) invalidWorkflowRequest("提醒时间不是有效日期时间");
  return new Date(timestamp).toISOString();
}

export async function listWorkflowTaskReminders(taskIdValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const rows = await database.prepare(`SELECT id, task_id, remind_at, note, status, created_by, created_at, updated_at
    FROM workflow_task_reminders WHERE task_id = ? AND status = 'pending' ORDER BY remind_at ASC, id ASC LIMIT 200`).bind(taskId).all<ReminderRow>();
  return rows.results.map(mapReminder);
}

export async function createWorkflowTaskReminder(taskIdValue: unknown, input: { remindAt?: unknown; note?: unknown }, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  assertInputKeys(input, ["remindAt", "note"]);
  const remindAt = reminderTime(input.remindAt); const note = boundedText(input.note, "提醒备注", 500, false); const id = crypto.randomUUID();
  await database.batch([
    database.prepare(`INSERT INTO workflow_task_reminders (id, task_id, remind_at, note, created_by) VALUES (?, ?, ?, ?, ?)`)
      .bind(id, taskId, remindAt, note, actorEmail(actor)),
    activityStatement(database, taskId, "reminder.created", "设置了提醒", actor, { reminderId: id, remindAt }),
  ]);
  const row = await database.prepare(`SELECT id, task_id, remind_at, note, status, created_by, created_at, updated_at
    FROM workflow_task_reminders WHERE id = ?`).bind(id).first<ReminderRow>();
  if (!row) throw new Error("提醒保存后无法读取"); return mapReminder(row);
}

export async function dismissWorkflowTaskReminder(taskIdValue: unknown, reminderIdValue: unknown, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); const id = resourceId(reminderIdValue, "提醒标识"); await assertTaskExists(database, taskId);
  const current = await database.prepare("SELECT id FROM workflow_task_reminders WHERE id = ? AND task_id = ? AND status = 'pending' LIMIT 1")
    .bind(id, taskId).first<{ id: string }>();
  if (!current) return false;
  const results = await database.batch([
    database.prepare(`INSERT INTO workflow_task_activity_logs (id, task_id, action, summary, metadata_json, actor_email)
      SELECT ?, ?, 'reminder.dismissed', '取消了提醒', ?, ?
      WHERE EXISTS (SELECT 1 FROM workflow_task_reminders WHERE id = ? AND task_id = ? AND status = 'pending')`)
      .bind(crypto.randomUUID(), taskId, metadataJson({ reminderId: id }), actorEmail(actor), id, taskId),
    database.prepare(`UPDATE workflow_task_reminders SET status = 'dismissed', updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND task_id = ? AND status = 'pending'`).bind(id, taskId),
  ]);
  return Number(results[1]?.meta?.changes ?? 0) > 0;
}

function safeEntityUrl(value: unknown) {
  const url = boundedText(value, "关联地址", 1_000, false);
  if (!url) return "";
  let parsed: URL;
  try { parsed = new URL(url); } catch { invalidWorkflowRequest("关联地址无效"); }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") invalidWorkflowRequest("关联地址仅支持 HTTP 或 HTTPS");
  if (parsed.username || parsed.password) invalidWorkflowRequest("关联地址不能包含账号或密码");
  return parsed.toString();
}

export async function listWorkflowTaskLinks(taskIdValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const rows = await database.prepare(`SELECT id, task_id, entity_type, entity_id, label, url, created_by, created_at
    FROM workflow_task_entity_links WHERE task_id = ? ORDER BY created_at ASC, id ASC LIMIT 200`).bind(taskId).all<LinkRow>();
  return rows.results.map(mapLink);
}

export async function createWorkflowTaskLink(taskIdValue: unknown, input: { entityType?: unknown; entityId?: unknown; label?: unknown; url?: unknown }, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  assertInputKeys(input, ["entityType", "entityId", "label", "url"]);
  if (!workflowEntityTypes.includes(input.entityType as WorkflowEntityType)) invalidWorkflowRequest("业务实体类型无效");
  const entityType = input.entityType as WorkflowEntityType;
  const entityId = boundedText(input.entityId, "业务实体标识", 240); const label = boundedText(input.label, "业务实体名称", 240);
  const url = safeEntityUrl(input.url); const id = crypto.randomUUID();
  try {
    await database.batch([
      database.prepare(`INSERT INTO workflow_task_entity_links (id, task_id, entity_type, entity_id, label, url, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(id, taskId, entityType, entityId, label, url, actorEmail(actor)),
      activityStatement(database, taskId, "link.created", "关联了业务对象", actor, { linkId: id, entityType, entityId }),
    ]);
  } catch (error) {
    if (error instanceof Error && /unique|constraint/i.test(error.message)) {
      throw new WorkflowRequestError(409, "conflict", "该业务对象已关联到当前工作事项");
    }
    throw error;
  }
  const row = await database.prepare(`SELECT id, task_id, entity_type, entity_id, label, url, created_by, created_at
    FROM workflow_task_entity_links WHERE id = ?`).bind(id).first<LinkRow>();
  if (!row) throw new Error("业务关联保存后无法读取"); return mapLink(row);
}

export async function deleteWorkflowTaskLink(taskIdValue: unknown, linkIdValue: unknown, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); const id = resourceId(linkIdValue, "关联标识"); await assertTaskExists(database, taskId);
  const current = await database.prepare("SELECT id FROM workflow_task_entity_links WHERE id = ? AND task_id = ? LIMIT 1")
    .bind(id, taskId).first<{ id: string }>();
  if (!current) return false;
  const results = await database.batch([
    database.prepare(`INSERT INTO workflow_task_activity_logs (id, task_id, action, summary, metadata_json, actor_email)
      SELECT ?, ?, 'link.deleted', '移除了业务关联', ?, ?
      WHERE EXISTS (SELECT 1 FROM workflow_task_entity_links WHERE id = ? AND task_id = ?)`)
      .bind(crypto.randomUUID(), taskId, metadataJson({ linkId: id }), actorEmail(actor), id, taskId),
    database.prepare("DELETE FROM workflow_task_entity_links WHERE id = ? AND task_id = ?").bind(id, taskId),
  ]);
  return Number(results[1]?.meta?.changes ?? 0) > 0;
}

const attachmentTypes: Record<string, { mimeTypes: readonly string[]; magic?: (bytes: Uint8Array) => boolean }> = {
  pdf: { mimeTypes: ["application/pdf"], magic: (b) => b[0] === 0x25 && b[1] === 0x50 && b[2] === 0x44 && b[3] === 0x46 },
  png: { mimeTypes: ["image/png"], magic: (b) => b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 },
  jpg: { mimeTypes: ["image/jpeg"], magic: (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  jpeg: { mimeTypes: ["image/jpeg"], magic: (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  webp: { mimeTypes: ["image/webp"], magic: (b) => String.fromCharCode(...b.slice(0, 4)) === "RIFF" && String.fromCharCode(...b.slice(8, 12)) === "WEBP" },
  xls: { mimeTypes: ["application/vnd.ms-excel"], magic: (b) => [0xd0, 0xcf, 0x11, 0xe0].every((v, i) => b[i] === v) },
  xlsx: { mimeTypes: ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"], magic: (b) => b[0] === 0x50 && b[1] === 0x4b },
  docx: { mimeTypes: ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"], magic: (b) => b[0] === 0x50 && b[1] === 0x4b },
  txt: { mimeTypes: ["text/plain"] },
  csv: { mimeTypes: ["text/csv", "application/csv", "text/plain"] },
};

function containsAscii(bytes: Uint8Array, text: string) {
  const target = new TextEncoder().encode(text);
  outer: for (let index = 0; index <= bytes.length - target.length; index += 1) {
    for (let offset = 0; offset < target.length; offset += 1) if (bytes[index + offset] !== target[offset]) continue outer;
    return true;
  }
  return false;
}

function safeFileName(value: unknown) {
  const raw = boundedText(value, "附件名称", 255);
  if (raw !== raw.split(/[\\/]/).pop() || /[\u0000-\u001f\u007f]/.test(raw) || raw === "." || raw === "..") invalidWorkflowRequest("附件名称无效");
  return raw;
}

let attachmentBucketOverride: R2Bucket | undefined;

/** Test seam: production callers never need to set this override. */
export function setWorkflowAttachmentBucketForTest(bucket?: R2Bucket) {
  attachmentBucketOverride = bucket;
}

async function attachmentBucket() {
  if (attachmentBucketOverride) return attachmentBucketOverride;
  const { env } = await import("cloudflare:workers");
  if (!env.SALES_IMPORT_FILES) throw new Error("附件存储暂不可用");
  return env.SALES_IMPORT_FILES;
}

function hex(buffer: ArrayBuffer) { return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join(""); }

async function validatedAttachment(file: File) {
  const fileName = safeFileName(file.name);
  if (!Number.isSafeInteger(file.size) || file.size <= 0) invalidWorkflowRequest("附件不能为空");
  if (file.size > MAX_WORKFLOW_ATTACHMENT_BYTES) throw new WorkflowRequestError(413, "payload_too_large", "单个附件不能超过 10MB");
  const extension = fileName.includes(".") ? fileName.split(".").pop()!.toLowerCase() : "";
  const rule = attachmentTypes[extension];
  if (!rule || !rule.mimeTypes.includes(file.type.toLowerCase())) invalidWorkflowRequest("附件格式不受支持或文件类型与扩展名不一致");
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (rule.magic && !rule.magic(bytes)) invalidWorkflowRequest("附件内容与声明的文件类型不一致");
  if (extension === "xlsx" && (!containsAscii(bytes, "[Content_Types].xml") || !containsAscii(bytes, "xl/"))) invalidWorkflowRequest("附件内容不是有效的 XLSX 工作簿");
  if (extension === "docx" && (!containsAscii(bytes, "[Content_Types].xml") || !containsAscii(bytes, "word/"))) invalidWorkflowRequest("附件内容不是有效的 DOCX 文档");
  if (extension === "txt" || extension === "csv") {
    if (bytes.slice(0, Math.min(bytes.length, 4_096)).includes(0)) invalidWorkflowRequest("文本附件内容无效");
    try { new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { invalidWorkflowRequest("文本附件必须使用 UTF-8 编码"); }
  }
  return { fileName, mimeType: file.type.toLowerCase(), bytes, sha256: hex(await crypto.subtle.digest("SHA-256", bytes)) };
}

function cleanupErrorSummary(error: unknown) {
  const name = error instanceof Error && error.name ? error.name : "storage_cleanup_failed";
  return Array.from(name).slice(0, 120).join("") || "storage_cleanup_failed";
}

async function enqueueAttachmentCleanup(db: D1Database, objectKey: string) {
  if (!objectKey.startsWith(`${ATTACHMENT_PREFIX}/`)) return;
  await db.prepare(`INSERT INTO workflow_attachment_cleanup_queue (object_key)
    VALUES (?) ON CONFLICT(object_key) DO UPDATE SET updated_at = CURRENT_TIMESTAMP`).bind(objectKey).run();
}

async function processAttachmentCleanupKeys(db: D1Database, objectKeys: string[]) {
  const safeKeys = [...new Set(objectKeys.filter((key) => key.startsWith(`${ATTACHMENT_PREFIX}/`)))].slice(0, 100);
  if (safeKeys.length === 0) return { attempted: 0, deleted: 0, failed: 0 };
  let bucket: R2Bucket;
  try {
    bucket = await attachmentBucket();
  } catch (error) {
    const summary = cleanupErrorSummary(error);
    await db.batch(safeKeys.map((objectKey) => db.prepare(`UPDATE workflow_attachment_cleanup_queue
      SET attempts = attempts + 1, last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE object_key = ?`)
      .bind(summary, objectKey))).catch(() => undefined);
    return { attempted: safeKeys.length, deleted: 0, failed: safeKeys.length };
  }
  let deleted = 0;
  let failed = 0;
  for (const objectKey of safeKeys) {
    try {
      await bucket.delete(objectKey);
      await db.prepare("DELETE FROM workflow_attachment_cleanup_queue WHERE object_key = ?").bind(objectKey).run();
      deleted += 1;
    } catch (error) {
      failed += 1;
      await db.prepare(`UPDATE workflow_attachment_cleanup_queue
        SET attempts = attempts + 1, last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE object_key = ?`)
        .bind(cleanupErrorSummary(error), objectKey).run().catch(() => undefined);
    }
  }
  return { attempted: safeKeys.length, deleted, failed };
}

async function drainAttachmentCleanup(db: D1Database, objectKeys: string[]) {
  return processAttachmentCleanupKeys(db, objectKeys);
}

/** Bounded outbox consumer that can be invoked by a scheduled worker or an admin maintenance action. */
export async function runWorkflowAttachmentCleanup(input: { limit?: unknown } = {}, db?: D1Database) {
  const database = await workflowDatabase(db);
  await ensureWorkflowCollaborationSchema(database);
  const rawLimit = input.limit ?? 50;
  const limit = typeof rawLimit === "number" ? rawLimit
    : typeof rawLimit === "string" && /^\d+$/.test(rawLimit.trim()) ? Number(rawLimit) : Number.NaN;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) invalidWorkflowRequest("清理批次必须在 1 到 100 之间");
  const rows = await database.prepare(`SELECT object_key FROM workflow_attachment_cleanup_queue
    ORDER BY updated_at ASC, object_key ASC LIMIT ?`).bind(limit).all<{ object_key: string }>();
  const result = await processAttachmentCleanupKeys(database, rows.results.map((item) => item.object_key));
  const remaining = await database.prepare("SELECT COUNT(*) AS total FROM workflow_attachment_cleanup_queue")
    .first<{ total: number }>();
  return { ...result, remaining: Number(remaining?.total ?? 0) };
}

export async function listWorkflowTaskAttachments(taskIdValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const rows = await database.prepare(`SELECT id, task_id, file_name, mime_type, size_bytes, sha256, object_key, created_by, created_at
    FROM workflow_task_attachments WHERE task_id = ? ORDER BY created_at ASC, id ASC LIMIT 100`).bind(taskId).all<AttachmentRow>();
  return rows.results.map(mapAttachment);
}

export async function createWorkflowTaskAttachment(taskIdValue: unknown, file: File, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); await assertTaskExists(database, taskId);
  const stale = await database.prepare("SELECT object_key FROM workflow_attachment_cleanup_queue ORDER BY updated_at ASC, object_key ASC LIMIT 50")
    .all<{ object_key: string }>();
  await drainAttachmentCleanup(database, stale.results.map((item) => item.object_key));
  const count = await database.prepare("SELECT COUNT(*) AS total FROM workflow_task_attachments WHERE task_id = ?")
    .bind(taskId).first<{ total: number }>();
  if (Number(count?.total ?? 0) >= 100) throw new WorkflowRequestError(409, "conflict", "每个工作事项最多保存 100 个附件");
  const validated = await validatedAttachment(file); const id = crypto.randomUUID();
  const objectKey = `${ATTACHMENT_PREFIX}/${taskId}/${id}`;
  const bucket = await attachmentBucket();
  await bucket.put(objectKey, validated.bytes, {
    httpMetadata: { contentType: validated.mimeType, cacheControl: "private, no-store" },
    customMetadata: { attachmentId: id, taskId },
  });
  try {
    await database.batch([
      database.prepare(`INSERT INTO workflow_task_attachments
        (id, task_id, file_name, mime_type, size_bytes, sha256, object_key, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
        .bind(id, taskId, validated.fileName, validated.mimeType, validated.bytes.byteLength, validated.sha256, objectKey, actorEmail(actor)),
      activityStatement(database, taskId, "attachment.created", "上传了附件", actor, { attachmentId: id, fileName: validated.fileName, sizeBytes: validated.bytes.byteLength }),
    ]);
  } catch (error) {
    try {
      await bucket.delete(objectKey);
    } catch {
      await enqueueAttachmentCleanup(database, objectKey).catch(() => undefined);
    }
    throw error;
  }
  const row = await database.prepare(`SELECT id, task_id, file_name, mime_type, size_bytes, sha256, object_key, created_by, created_at
    FROM workflow_task_attachments WHERE id = ?`).bind(id).first<AttachmentRow>();
  if (!row) throw new Error("附件保存后无法读取"); return mapAttachment(row);
}

export async function getWorkflowTaskAttachmentDownload(taskIdValue: unknown, attachmentIdValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); const id = resourceId(attachmentIdValue, "附件标识"); await assertTaskExists(database, taskId);
  const row = await database.prepare(`SELECT id, task_id, file_name, mime_type, size_bytes, sha256, object_key, created_by, created_at
    FROM workflow_task_attachments WHERE id = ? AND task_id = ? LIMIT 1`).bind(id, taskId).first<AttachmentRow>();
  if (!row || !row.object_key.startsWith(`${ATTACHMENT_PREFIX}/${taskId}/`)) return null;
  const object = await (await attachmentBucket()).get(row.object_key); if (!object) return null;
  const bytes = new Uint8Array(await object.arrayBuffer());
  if (bytes.byteLength !== Number(row.size_bytes) || hex(await crypto.subtle.digest("SHA-256", bytes)) !== row.sha256) {
    throw new WorkflowRequestError(409, "integrity_error", "附件完整性校验失败，请联系管理员");
  }
  return { attachment: mapAttachment(row), bytes };
}

export async function deleteWorkflowTaskAttachment(taskIdValue: unknown, attachmentIdValue: unknown, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识"); const id = resourceId(attachmentIdValue, "附件标识"); await assertTaskExists(database, taskId);
  const row = await database.prepare("SELECT object_key, file_name FROM workflow_task_attachments WHERE id = ? AND task_id = ? LIMIT 1")
    .bind(id, taskId).first<{ object_key: string; file_name: string }>();
  if (!row || !row.object_key.startsWith(`${ATTACHMENT_PREFIX}/${taskId}/`)) return false;
  const results = await database.batch([
    database.prepare(`INSERT INTO workflow_attachment_cleanup_queue (object_key)
      VALUES (?) ON CONFLICT(object_key) DO NOTHING`).bind(row.object_key),
    database.prepare(`INSERT INTO workflow_task_activity_logs (id, task_id, action, summary, metadata_json, actor_email)
      SELECT ?, ?, 'attachment.deleted', '删除了附件', ?, ?
      WHERE EXISTS (SELECT 1 FROM workflow_task_attachments WHERE id = ? AND task_id = ?)`)
      .bind(crypto.randomUUID(), taskId, metadataJson({ attachmentId: id, fileName: row.file_name }), actorEmail(actor), id, taskId),
    database.prepare("DELETE FROM workflow_task_attachments WHERE id = ? AND task_id = ?").bind(id, taskId),
  ]);
  if (Number(results[2]?.meta?.changes ?? 0) === 0) return false;
  await drainAttachmentCleanup(database, [row.object_key]);
  return true;
}

/** Soft-deletes task facts with optimistic concurrency, preserving its audit history. */
export async function deleteWorkflowTaskWithCollaboration(
  taskIdValue: unknown,
  expectedVersionValue: unknown,
  actor: string,
  db?: D1Database,
) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const taskId = resourceId(taskIdValue, "工作项标识");
  const expectedVersion = strictExpectedVersion(expectedVersionValue);
  const deletedBy = actorEmail(actor);
  const task = await database.prepare(`SELECT t.id, s.version FROM workflow_tasks t
    JOIN workflow_task_states s ON s.task_id = t.id WHERE t.id = ? AND s.deleted_at IS NULL LIMIT 1`)
    .bind(taskId).first<{ id: string; version: number }>();
  if (!task) return false;
  if (Number(task.version) !== expectedVersion) workflowVersionConflict("工作事项已被其他人更新，请刷新后重试");
  const attachments = await database.prepare("SELECT object_key FROM workflow_task_attachments WHERE task_id = ?")
    .bind(taskId).all<{ object_key: string }>();
  const objectKeys = attachments.results.map((item) => item.object_key).filter((key) => key.startsWith(`${ATTACHMENT_PREFIX}/${taskId}/`));
  const nextVersion = expectedVersion + 1;
  const mutationToken = crypto.randomUUID();
  const results = await database.batch([
    database.prepare(`UPDATE workflow_task_states SET version = ?, mutation_token = ?,
      deleted_at = CURRENT_TIMESTAMP, deleted_by = ?
      WHERE task_id = ? AND version = ? AND deleted_at IS NULL`)
      .bind(nextVersion, mutationToken, deletedBy, taskId, expectedVersion),
    ...objectKeys.map((key) => database.prepare(`INSERT INTO workflow_attachment_cleanup_queue (object_key)
      SELECT ? WHERE EXISTS (SELECT 1 FROM workflow_task_states
        WHERE task_id = ? AND version = ? AND mutation_token = ? AND deleted_at IS NOT NULL)
      ON CONFLICT(object_key) DO UPDATE SET updated_at = CURRENT_TIMESTAMP`)
      .bind(key, taskId, nextVersion, mutationToken)),
    database.prepare(`DELETE FROM workflow_task_attachments WHERE task_id = ?
      AND EXISTS (SELECT 1 FROM workflow_task_states
        WHERE task_id = ? AND version = ? AND mutation_token = ? AND deleted_at IS NOT NULL)`)
      .bind(taskId, taskId, nextVersion, mutationToken),
    database.prepare(`INSERT INTO workflow_task_activity_logs
      (id, task_id, action, summary, metadata_json, actor_email)
      SELECT ?, t.id, 'task.deleted', '删除了工作事项', ?, ?
      FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
      WHERE t.id = ? AND s.version = ? AND s.mutation_token = ? AND s.deleted_at IS NOT NULL`)
      .bind(crypto.randomUUID(), metadataJson({ version: nextVersion }), deletedBy, taskId, nextVersion, mutationToken),
  ]);
  if (Number(results[0]?.meta?.changes ?? 0) === 0) workflowVersionConflict("工作事项已被其他人更新，请刷新后重试");
  await drainAttachmentCleanup(database, objectKeys);
  return true;
}

export async function getWorkflowTaskCollaboration(taskId: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  return {
    comments: await listWorkflowTaskComments(taskId, database),
    activity: await listWorkflowTaskActivity(taskId, database),
    reminders: await listWorkflowTaskReminders(taskId, database),
    links: await listWorkflowTaskLinks(taskId, database),
    attachments: await listWorkflowTaskAttachments(taskId, database),
  };
}

export type WorkflowTaskTemplateInput = {
  name?: unknown; description?: unknown; title?: unknown; workContent?: unknown; category?: unknown;
  owner?: unknown; shopName?: unknown; startOffsetDays?: unknown; dueOffsetDays?: unknown; priority?: unknown; active?: unknown;
  expectedVersion?: unknown;
};

const templateEditableFields = [
  "name", "description", "title", "workContent", "category", "owner", "shopName",
  "startOffsetDays", "dueOffsetDays", "priority", "active",
] as const;

function assertTemplateKeys(input: WorkflowTaskTemplateInput, update: boolean) {
  const allowed = new Set<string>(update ? [...templateEditableFields, "expectedVersion"] : templateEditableFields);
  const unknown = Object.keys(input).filter((key) => !allowed.has(key));
  if (unknown.length > 0) invalidWorkflowRequest(`包含不支持的字段：${unknown.slice(0, 5).join("、")}`);
  if (update && !templateEditableFields.some((field) => input[field] !== undefined)) invalidWorkflowRequest("缺少可更新的模板字段");
}

function offsetDays(value: unknown, field: string, fallback: number) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < -365 || Number(value) > 365) invalidWorkflowRequest(`${field}必须是 -365 至 365 的整数`);
  return Number(value);
}

function normalizedTemplate(input: WorkflowTaskTemplateInput, current?: TemplateRow) {
  const priority = input.priority === undefined ? current?.priority ?? "normal" : input.priority;
  if (priority !== "high" && priority !== "normal" && priority !== "low") invalidWorkflowRequest("模板紧急程度无效");
  const active = input.active === undefined ? (current ? Number(current.active) === 1 : true) : input.active;
  if (typeof active !== "boolean") invalidWorkflowRequest("模板启用状态无效");
  const value = {
    name: input.name === undefined && current ? current.name : boundedText(input.name, "模板名称", 120),
    description: input.description === undefined && current ? current.description : boundedText(input.description, "模板说明", 500, false),
    title: input.title === undefined && current ? current.title : boundedText(input.title, "工作事项", 160, false),
    workContent: input.workContent === undefined && current ? current.work_content : boundedText(input.workContent, "工作内容", 2_000, false),
    category: input.category === undefined && current ? current.category : boundedText(input.category, "事项分类", 80, false) || "工作计划",
    owner: input.owner === undefined && current ? current.owner : boundedText(input.owner, "跟进人", 120, false),
    shopName: input.shopName === undefined && current ? current.shop_name : boundedText(input.shopName, "店铺", 160, false),
    startOffsetDays: offsetDays(input.startOffsetDays, "开始偏移天数", current ? Number(current.start_offset_days) : 0),
    dueOffsetDays: offsetDays(input.dueOffsetDays, "截止偏移天数", current ? Number(current.due_offset_days) : 0),
    priority, active,
  };
  if (value.dueOffsetDays < value.startOffsetDays) invalidWorkflowRequest("截止偏移天数不能早于开始偏移天数");
  return value;
}

export async function listWorkflowTaskTemplates(includeInactive: boolean, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  const rows = await database.prepare(`SELECT t.id, t.name, t.description, t.title, t.work_content, t.category, t.owner, t.shop_name,
    t.start_offset_days, t.due_offset_days, t.priority, t.active, t.created_by, t.updated_by, t.created_at, t.updated_at, s.version
    FROM workflow_task_templates t JOIN workflow_task_template_states s ON s.template_id = t.id
    ${includeInactive ? "" : "WHERE t.active = 1"} ORDER BY t.active DESC, t.updated_at DESC, t.id DESC LIMIT 200`).all<TemplateRow>();
  return rows.results.map(mapTemplate);
}

export async function createWorkflowTaskTemplate(input: WorkflowTaskTemplateInput, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database);
  assertTemplateKeys(input, false);
  const value = normalizedTemplate(input); const id = crypto.randomUUID(); const createdBy = actorEmail(actor);
  await database.batch([
    database.prepare(`INSERT INTO workflow_task_templates
      (id, name, description, title, work_content, category, owner, shop_name, start_offset_days, due_offset_days, priority, active, created_by, updated_by)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(id, value.name, value.description, value.title, value.workContent, value.category,
        value.owner, value.shopName, value.startOffsetDays, value.dueOffsetDays, value.priority, value.active ? 1 : 0, createdBy, createdBy),
    database.prepare("INSERT INTO workflow_task_template_states (template_id, version) VALUES (?, 1)").bind(id),
  ]);
  return getTemplate(database, id);
}

async function getTemplate(db: D1Database, id: string) {
  const row = await db.prepare(`SELECT t.id, t.name, t.description, t.title, t.work_content, t.category, t.owner, t.shop_name,
    t.start_offset_days, t.due_offset_days, t.priority, t.active, t.created_by, t.updated_by, t.created_at, t.updated_at, s.version
    FROM workflow_task_templates t JOIN workflow_task_template_states s ON s.template_id = t.id
    WHERE t.id = ? LIMIT 1`).bind(id).first<TemplateRow>();
  return row ? mapTemplate(row) : null;
}

export async function updateWorkflowTaskTemplate(idValue: unknown, input: WorkflowTaskTemplateInput, actor: string, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database); const id = resourceId(idValue, "模板标识");
  assertTemplateKeys(input, true);
  const expectedVersion = strictExpectedVersion(input.expectedVersion);
  const current = await database.prepare(`SELECT t.id, t.name, t.description, t.title, t.work_content, t.category, t.owner, t.shop_name,
    t.start_offset_days, t.due_offset_days, t.priority, t.active, t.created_by, t.updated_by, t.created_at, t.updated_at, s.version
    FROM workflow_task_templates t JOIN workflow_task_template_states s ON s.template_id = t.id WHERE t.id = ? LIMIT 1`)
    .bind(id).first<TemplateRow>();
  if (!current) return null;
  if (Number(current.version) !== expectedVersion) workflowVersionConflict("工作模板已被其他人更新，请刷新后重试");
  const value = normalizedTemplate(input, current);
  const changed = value.name !== current.name || value.description !== current.description || value.title !== current.title
    || value.workContent !== current.work_content || value.category !== current.category || value.owner !== current.owner
    || value.shopName !== current.shop_name || value.startOffsetDays !== Number(current.start_offset_days)
    || value.dueOffsetDays !== Number(current.due_offset_days) || value.priority !== current.priority
    || value.active !== (Number(current.active) === 1);
  if (!changed) invalidWorkflowRequest("工作模板没有发生变化");
  const nextVersion = expectedVersion + 1;
  const mutationToken = crypto.randomUUID();
  const results = await database.batch([
    database.prepare(`UPDATE workflow_task_template_states SET version = ?, mutation_token = ?
      WHERE template_id = ? AND version = ?`).bind(nextVersion, mutationToken, id, expectedVersion),
    database.prepare(`UPDATE workflow_task_templates SET name = ?, description = ?, title = ?, work_content = ?, category = ?, owner = ?, shop_name = ?,
      start_offset_days = ?, due_offset_days = ?, priority = ?, active = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND EXISTS (SELECT 1 FROM workflow_task_template_states
        WHERE template_id = ? AND version = ? AND mutation_token = ?)`)
      .bind(value.name, value.description, value.title, value.workContent, value.category, value.owner, value.shopName, value.startOffsetDays,
        value.dueOffsetDays, value.priority, value.active ? 1 : 0, actorEmail(actor), id, id, nextVersion, mutationToken),
  ]);
  if (Number(results[0]?.meta?.changes ?? 0) === 0) workflowVersionConflict("工作模板已被其他人更新，请刷新后重试");
  return getTemplate(database, id);
}

export async function deleteWorkflowTaskTemplate(idValue: unknown, expectedVersionValue: unknown, db?: D1Database) {
  const database = await workflowDatabase(db); await ensureWorkflowCollaborationSchema(database); const id = resourceId(idValue, "模板标识");
  const expectedVersion = strictExpectedVersion(expectedVersionValue);
  const current = await database.prepare(`SELECT s.version FROM workflow_task_templates t
    JOIN workflow_task_template_states s ON s.template_id = t.id WHERE t.id = ? LIMIT 1`)
    .bind(id).first<{ version: number }>();
  if (!current) return false;
  if (Number(current.version) !== expectedVersion) workflowVersionConflict("工作模板已被其他人更新，请刷新后重试");
  const nextVersion = expectedVersion + 1;
  const mutationToken = crypto.randomUUID();
  const results = await database.batch([
    database.prepare(`UPDATE workflow_task_template_states SET version = ?, mutation_token = ?
      WHERE template_id = ? AND version = ?`).bind(nextVersion, mutationToken, id, expectedVersion),
    database.prepare(`DELETE FROM workflow_task_templates WHERE id = ?
      AND EXISTS (SELECT 1 FROM workflow_task_template_states
        WHERE template_id = ? AND version = ? AND mutation_token = ?)`)
      .bind(id, id, nextVersion, mutationToken),
  ]);
  if (Number(results[0]?.meta?.changes ?? 0) === 0) workflowVersionConflict("工作模板已被其他人更新，请刷新后重试");
  return Number(results[1]?.meta?.changes ?? 0) > 0;
}
