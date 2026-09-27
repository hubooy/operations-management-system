import type { D1Database } from "@/lib/database/d1";
import { deleteWorkflowTaskWithCollaboration } from "@/lib/workflow/collaboration";
import { invalidWorkflowRequest, workflowVersionConflict } from "@/lib/workflow/errors";
import { ensureWorkflowCollaborationSchema, ensureWorkflowTaskSchema } from "@/lib/workflow/schema";

export { ensureWorkflowTaskSchema };

export const workflowTaskStatuses = ["待开始", "工作中", "已完成"] as const;
export type WorkflowTaskStatus = (typeof workflowTaskStatuses)[number];
export const workflowTaskPriorities = ["high", "normal", "low"] as const;
export type WorkflowTaskPriority = (typeof workflowTaskPriorities)[number];
export const workflowTaskSources = ["系统预置", "手动录入"] as const;
export type WorkflowTaskSource = (typeof workflowTaskSources)[number];

export type WorkflowTask = {
  id: string; title: string; workContent: string; category: string; owner: string; shopName: string;
  startDate: string; due: string; status: WorkflowTaskStatus; priority: WorkflowTaskPriority;
  source: WorkflowTaskSource; version: number; createdAt: string; updatedAt: string;
  /** @deprecated Attachments are paged through the collaboration endpoint. */
  attachments: [];
};

export type CreateWorkflowTaskInput = {
  title?: unknown; workContent?: unknown; category?: unknown; owner?: unknown; shopName?: unknown;
  startDate?: unknown; due?: unknown; priority?: unknown;
};
export type UpdateWorkflowTaskInput = CreateWorkflowTaskInput & { status?: unknown; expectedVersion?: unknown };
export type WorkflowTaskListInput = {
  query?: unknown; statuses?: readonly unknown[]; priorities?: readonly unknown[]; owners?: readonly unknown[];
  shopNames?: readonly unknown[]; categories?: readonly unknown[]; sources?: readonly unknown[];
  dueFrom?: unknown; dueTo?: unknown; page?: unknown; pageSize?: unknown;
};

type WorkflowTaskRow = {
  id: string; title: string; work_content: string; category: string; owner: string; shop_name: string;
  start_date: string; due_date: string; status: string; priority: string; created_by: string;
  created_at: string; updated_at: string; version: number;
};

const TASK_BOOTSTRAP_KEY = "work-plan-v1";
const MAX_PAGE_SIZE = 100;
const MAX_FILTER_VALUES = 20;
const MAX_OFFSET = 100_000;
const taskColumns = `t.id, t.title, t.work_content, t.category, t.owner, t.shop_name,
  t.start_date, t.due_date, t.status, t.priority, t.created_by, t.created_at, t.updated_at, s.version`;

const initialTasks: Array<Omit<WorkflowTask, "attachments" | "source" | "version" | "createdAt" | "updatedAt">> = [
  { id: "task-1", title: "完成 7 月大促价格检查", workContent: "核对重点商品活动价、优惠券和到手价，并整理差异项。", category: "活动运营", owner: "京东自营", shopName: "京东-志高商用设备旗舰店", startDate: "2026-07-18", due: "2026-07-18", status: "待开始", priority: "high" },
  { id: "task-2", title: "新品成分资料归档", workContent: "归档新品成分表、质检资料和平台备案所需文件。", category: "新品上架", owner: "商品组", shopName: "天猫-志高亿用专卖店", startDate: "2026-07-17", due: "2026-07-18", status: "待开始", priority: "normal" },
  { id: "task-3", title: "净透精华主图升级", workContent: "完成主图文案、卖点排版和详情页素材替换。", category: "新品上架", owner: "天猫组", shopName: "天猫-志高亿用专卖店", startDate: "2026-07-16", due: "2026-07-18", status: "工作中", priority: "high" },
  { id: "task-4", title: "POP 店铺巡店检查", workContent: "检查商品链接、库存、活动报名及客服响应情况。", category: "巡店查询", owner: "运营组", shopName: "抖店-志高商业设备旗舰店", startDate: "2026-07-16", due: "2026-07-17", status: "工作中", priority: "normal" },
  { id: "task-5", title: "评价晒图素材第 3 批", workContent: "筛选可用评价并整理晒单图片，提交页面运营使用。", category: "评价维护", owner: "客服组", shopName: "拼多多-志高商用厨电旗舰店", startDate: "2026-07-16", due: "2026-07-18", status: "工作中", priority: "normal" },
  { id: "task-6", title: "天猫周报数据核对", workContent: "复核周度销售、退款和投放数据，完成周报归档。", category: "数据分析", owner: "运营组", shopName: "天猫-志高亿用专卖店", startDate: "2026-07-14", due: "2026-07-17", status: "已完成", priority: "low" },
  { id: "task-7", title: "新品 SKU 映射", workContent: "完成 ERP 与平台规格代码映射并交接给上架同事。", category: "新品上架", owner: "商品组", shopName: "京东-志高商用设备旗舰店", startDate: "2026-07-14", due: "2026-07-17", status: "已完成", priority: "low" },
];

async function workflowDatabase(db?: D1Database) {
  if (db) return db;
  const { getD1Database } = await import("@/lib/database/d1");
  return getD1Database();
}
function isWorkflowTaskStatus(value: unknown): value is WorkflowTaskStatus {
  return typeof value === "string" && workflowTaskStatuses.includes(value as WorkflowTaskStatus);
}
function isWorkflowTaskPriority(value: unknown): value is WorkflowTaskPriority {
  return typeof value === "string" && workflowTaskPriorities.includes(value as WorkflowTaskPriority);
}
function boundedText(value: unknown, fallback: string, maxLength: number, label: string) {
  if (value !== undefined && typeof value !== "string") invalidWorkflowRequest(`${label}必须是文本`);
  const text = typeof value === "string" ? value.trim() : "";
  if (Array.from(text).length > maxLength) invalidWorkflowRequest(`${label}不能超过 ${maxLength} 个字符`);
  return text || fallback;
}
function validCalendarDate(text: string, label: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) invalidWorkflowRequest(`${label}必须为 YYYY-MM-DD 格式`);
  const [year, month, day] = text.split("-").map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  if (value.getUTCFullYear() !== year || value.getUTCMonth() !== month - 1 || value.getUTCDate() !== day) {
    invalidWorkflowRequest(`${label}不是有效日期`);
  }
  return text;
}
function taskDate(value: unknown, label: string) {
  if (typeof value !== "string") invalidWorkflowRequest(`${label}必须为 YYYY-MM-DD 或待排期`);
  const text = value.trim();
  if (!text || text === "待排期") return "待排期";
  return validCalendarDate(text, label);
}
function resourceId(value: unknown, label = "工作项标识") {
  if (typeof value !== "string") invalidWorkflowRequest(`缺少有效的${label}`);
  const id = value.trim();
  if (!id || id.length > 128 || !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(id)) invalidWorkflowRequest(`缺少有效的${label}`);
  return id;
}
function strictPositiveInteger(value: unknown, fallback: number | undefined, max: number, label: string) {
  if (value === undefined || value === null || value === "") {
    if (fallback !== undefined) return fallback;
    invalidWorkflowRequest(`${label}不能为空`);
  }
  const parsed = typeof value === "number" ? value
    : typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : Number.NaN;
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > max) invalidWorkflowRequest(`${label}必须在 1 到 ${max} 之间`);
  return parsed;
}
function expectedVersion(value: unknown) {
  return strictPositiveInteger(value, undefined, 2_147_483_646, "预期版本");
}
function assertAllowedKeys(input: object, allowed: readonly string[]) {
  const allowedSet = new Set(allowed);
  const unknown = Object.keys(input).filter((key) => !allowedSet.has(key));
  if (unknown.length > 0) invalidWorkflowRequest(`包含不支持的字段：${unknown.slice(0, 5).join("、")}`);
}
function mapTask(row: WorkflowTaskRow): WorkflowTask {
  return {
    id: row.id, title: row.title, workContent: row.work_content, category: row.category, owner: row.owner,
    shopName: row.shop_name, startDate: row.start_date, due: row.due_date,
    status: isWorkflowTaskStatus(row.status) ? row.status : "待开始",
    priority: isWorkflowTaskPriority(row.priority) ? row.priority : "normal",
    source: sourceFromCreatedBy(row.created_by), version: Number(row.version),
    createdAt: row.created_at, updatedAt: row.updated_at, attachments: [],
  };
}
function sourceFromCreatedBy(createdBy: string): WorkflowTaskSource {
  return createdBy === "system" ? "系统预置" : "手动录入";
}
function normalizedCreateInput(input: CreateWorkflowTaskInput) {
  assertAllowedKeys(input, ["title", "workContent", "category", "owner", "shopName", "startDate", "due", "priority"]);
  const startDate = taskDate(input.startDate ?? "待排期", "开始日期");
  const due = taskDate(input.due ?? "待排期", "截止日期");
  if (startDate !== "待排期" && due !== "待排期" && due < startDate) invalidWorkflowRequest("截止时间不能早于开始时间");
  if (input.priority !== undefined && !isWorkflowTaskPriority(input.priority)) invalidWorkflowRequest("工作项紧急程度无效");
  return {
    title: boundedText(input.title, "未命名工作项", 160, "工作事项"),
    workContent: boundedText(input.workContent, "未填写工作内容", 2_000, "工作内容"),
    category: boundedText(input.category, "工作计划", 80, "事项分类"),
    owner: boundedText(input.owner, "未指定跟进人", 120, "跟进人"),
    shopName: boundedText(input.shopName, "未关联店铺", 160, "店铺"),
    startDate, due, priority: (input.priority ?? "normal") as WorkflowTaskPriority,
  };
}

async function ensureInitialTasks(db: D1Database) {
  await ensureWorkflowTaskSchema(db);
  const statements = initialTasks.map((task) => db.prepare(`INSERT OR IGNORE INTO workflow_tasks (
    id, title, work_content, category, owner, shop_name, start_date, due_date, status, priority, created_by, updated_by)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'system', 'system'
    WHERE NOT EXISTS (SELECT 1 FROM workflow_task_bootstrap WHERE key = ?)`)
    .bind(task.id, task.title, task.workContent, task.category, task.owner, task.shopName,
      task.startDate, task.due, task.status, task.priority, TASK_BOOTSTRAP_KEY));
  statements.push(
    db.prepare("INSERT OR IGNORE INTO workflow_task_bootstrap (key) VALUES (?)").bind(TASK_BOOTSTRAP_KEY),
    db.prepare("INSERT OR IGNORE INTO workflow_task_states (task_id) SELECT id FROM workflow_tasks"),
  );
  await db.batch(statements);
}
function boundedList(values: readonly unknown[] | undefined, label: string, allowed?: readonly string[]) {
  if (!values) return [];
  if (values.length > MAX_FILTER_VALUES) invalidWorkflowRequest(`${label}最多允许 ${MAX_FILTER_VALUES} 项`);
  const normalized = values.map((value) => boundedText(value, "", 160, label));
  const unique = [...new Set(normalized)];
  if (allowed && unique.some((value) => !allowed.includes(value))) invalidWorkflowRequest(`${label}包含无效值`);
  return unique;
}
function normalizeListInput(input: WorkflowTaskListInput) {
  const page = strictPositiveInteger(input.page, 1, 2_000, "页码");
  const pageSize = strictPositiveInteger(input.pageSize, 50, MAX_PAGE_SIZE, "每页条数");
  const offset = (page - 1) * pageSize;
  if (offset > MAX_OFFSET) invalidWorkflowRequest(`分页偏移不能超过 ${MAX_OFFSET}`);
  const dueFrom = input.dueFrom === undefined || input.dueFrom === null || input.dueFrom === "" ? null
    : validCalendarDate(boundedText(input.dueFrom, "", 10, "截止开始日期"), "截止开始日期");
  const dueTo = input.dueTo === undefined || input.dueTo === null || input.dueTo === "" ? null
    : validCalendarDate(boundedText(input.dueTo, "", 10, "截止结束日期"), "截止结束日期");
  if (dueFrom && dueTo && dueFrom >= dueTo) invalidWorkflowRequest("截止日期范围必须满足开始日期早于结束日期");
  return {
    query: boundedText(input.query, "", 80, "搜索词"),
    statuses: boundedList(input.statuses, "状态", workflowTaskStatuses),
    priorities: boundedList(input.priorities, "紧急程度", workflowTaskPriorities),
    owners: boundedList(input.owners, "跟进人"), shopNames: boundedList(input.shopNames, "店铺"),
    categories: boundedList(input.categories, "事项分类"),
    sources: boundedList(input.sources, "来源", workflowTaskSources),
    dueFrom, dueTo, page, pageSize, offset,
  };
}
function placeholders(values: readonly unknown[]) { return values.map(() => "?").join(", "); }
function escapedLike(value: string) { return `%${value.replace(/\\/g, "\\\\").replace(/%/g, "\\%").replace(/_/g, "\\_")}%`; }
function taskFilters(filters: ReturnType<typeof normalizeListInput>, includeStatuses: boolean) {
  const clauses = ["s.deleted_at IS NULL"];
  const values: unknown[] = [];
  if (filters.query) {
    const like = escapedLike(filters.query);
    clauses.push(`(t.title LIKE ? ESCAPE '\\' COLLATE NOCASE OR t.work_content LIKE ? ESCAPE '\\' COLLATE NOCASE
      OR t.category LIKE ? ESCAPE '\\' COLLATE NOCASE OR t.owner LIKE ? ESCAPE '\\' COLLATE NOCASE
      OR t.shop_name LIKE ? ESCAPE '\\' COLLATE NOCASE)`);
    values.push(like, like, like, like, like);
  }
  const appendList = (column: string, items: string[]) => {
    if (items.length === 0) return;
    clauses.push(`${column} IN (${placeholders(items)})`); values.push(...items);
  };
  if (includeStatuses) appendList("t.status", filters.statuses);
  appendList("t.priority", filters.priorities); appendList("t.owner", filters.owners); appendList("t.shop_name", filters.shopNames);
  appendList("t.category", filters.categories);
  if (filters.sources.length > 0) {
    clauses.push(`(CASE WHEN t.created_by = 'system' THEN '系统预置' ELSE '手动录入' END) IN (${placeholders(filters.sources)})`);
    values.push(...filters.sources);
  }
  if (filters.dueFrom) { clauses.push("t.due_date <> '待排期' AND t.due_date >= ?"); values.push(filters.dueFrom); }
  if (filters.dueTo) { clauses.push("t.due_date <> '待排期' AND t.due_date < ?"); values.push(filters.dueTo); }
  return { where: `WHERE ${clauses.join(" AND ")}`, values };
}

export async function listWorkflowTasksPage(input: WorkflowTaskListInput = {}, db?: D1Database) {
  const database = await workflowDatabase(db);
  await ensureInitialTasks(database);
  const filters = normalizeListInput(input);
  const selected = taskFilters(filters, true);
  const summaryFilter = taskFilters(filters, false);
  const [count, summary, result, categoryRows, ownerRows, shopRows] = await Promise.all([
    database.prepare(`SELECT COUNT(*) AS total FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id ${selected.where}`)
      .bind(...selected.values).first<{ total: number }>(),
    database.prepare(`SELECT COUNT(*) AS total,
      SUM(CASE WHEN t.status = '待开始' THEN 1 ELSE 0 END) AS pending,
      SUM(CASE WHEN t.status = '工作中' THEN 1 ELSE 0 END) AS in_progress,
      SUM(CASE WHEN t.status = '已完成' THEN 1 ELSE 0 END) AS completed
      FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id ${summaryFilter.where}`)
      .bind(...summaryFilter.values).first<{ total: number; pending: number; in_progress: number; completed: number }>(),
    database.prepare(`SELECT ${taskColumns} FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
      ${selected.where} ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?`)
      .bind(...selected.values, filters.pageSize, filters.offset).all<WorkflowTaskRow>(),
    database.prepare(`SELECT DISTINCT t.category AS value FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
      WHERE s.deleted_at IS NULL AND t.category <> '' ORDER BY t.category COLLATE NOCASE LIMIT 200`).all<{ value: string }>(),
    database.prepare(`SELECT DISTINCT t.owner AS value FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
      WHERE s.deleted_at IS NULL AND t.owner <> '' ORDER BY t.owner COLLATE NOCASE LIMIT 200`).all<{ value: string }>(),
    database.prepare(`SELECT DISTINCT t.shop_name AS value FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
      WHERE s.deleted_at IS NULL AND t.shop_name <> '' ORDER BY t.shop_name COLLATE NOCASE LIMIT 200`).all<{ value: string }>(),
  ]);
  const items = (result.results ?? []).map(mapTask);
  const total = Number(count?.total ?? 0);
  const pending = Number(summary?.pending ?? 0);
  const inProgress = Number(summary?.in_progress ?? 0);
  const completed = Number(summary?.completed ?? 0);
  return {
    items,
    pagination: { page: filters.page, pageSize: filters.pageSize, total, returned: items.length, truncated: filters.offset + items.length < total },
    summary: { total: Number(summary?.total ?? 0), pending, inProgress, completed, open: pending + inProgress },
    facets: {
      categories: (categoryRows.results ?? []).map((row) => row.value),
      owners: (ownerRows.results ?? []).map((row) => row.value),
      shopNames: (shopRows.results ?? []).map((row) => row.value),
      sources: [...workflowTaskSources],
    },
    filtersApplied: {
      query: filters.query, statuses: filters.statuses, priorities: filters.priorities,
      owners: filters.owners, shopNames: filters.shopNames, categories: filters.categories,
      sources: filters.sources, dueFrom: filters.dueFrom, dueTo: filters.dueTo,
    },
  };
}

/** Compatibility helper for bounded domain callers. API callers must use listWorkflowTasksPage. */
export async function listWorkflowTasks(db?: D1Database) {
  return (await listWorkflowTasksPage({ page: 1, pageSize: MAX_PAGE_SIZE }, db)).items;
}

export async function createWorkflowTask(input: CreateWorkflowTaskInput, updatedBy: string, db?: D1Database) {
  const database = await workflowDatabase(db);
  await ensureInitialTasks(database);
  const task = normalizedCreateInput(input);
  const actor = boundedText(updatedBy, "", 320, "操作人");
  if (!actor) invalidWorkflowRequest("操作人不能为空");
  const id = crypto.randomUUID();
  await ensureWorkflowCollaborationSchema(database);
  await database.batch([
    database.prepare(`INSERT INTO workflow_tasks (
      id, title, work_content, category, owner, shop_name, start_date, due_date, status, priority, created_by, updated_by)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, '待开始', ?, ?, ?)`).bind(
      id, task.title, task.workContent, task.category, task.owner, task.shopName,
      task.startDate, task.due, task.priority, actor, actor),
    database.prepare("INSERT INTO workflow_task_states (task_id, version) VALUES (?, 1)").bind(id),
    database.prepare(`INSERT INTO workflow_task_activity_logs
      (id, task_id, action, summary, metadata_json, actor_email)
      VALUES (?, ?, 'task.created', '创建了工作事项', ?, ?)`)
      .bind(crypto.randomUUID(), id, JSON.stringify({ version: 1 }), actor),
  ]);
  const row = await database.prepare(`SELECT ${taskColumns} FROM workflow_tasks t
    JOIN workflow_task_states s ON s.task_id = t.id WHERE t.id = ? AND s.deleted_at IS NULL LIMIT 1`)
    .bind(id).first<WorkflowTaskRow>();
  if (!row) throw new Error("workflow_task_create_readback_failed");
  return mapTask(row);
}

export async function updateWorkflowTask(id: unknown, input: UpdateWorkflowTaskInput, updatedBy: string, db?: D1Database) {
  const database = await workflowDatabase(db);
  await ensureInitialTasks(database);
  assertAllowedKeys(input, ["title", "workContent", "category", "owner", "shopName", "startDate", "due", "status", "priority", "expectedVersion"]);
  const validId = resourceId(id);
  const version = expectedVersion(input.expectedVersion);
  const editableFields: Array<keyof UpdateWorkflowTaskInput> = ["title", "workContent", "category", "owner", "shopName", "startDate", "due", "status", "priority"];
  if (!editableFields.some((field) => input[field] !== undefined)) invalidWorkflowRequest("缺少可更新的工作项字段");
  if (input.status !== undefined && !isWorkflowTaskStatus(input.status)) invalidWorkflowRequest("工作项状态无效");
  if (input.priority !== undefined && !isWorkflowTaskPriority(input.priority)) invalidWorkflowRequest("工作项紧急程度无效");
  const current = await database.prepare(`SELECT ${taskColumns} FROM workflow_tasks t
    JOIN workflow_task_states s ON s.task_id = t.id WHERE t.id = ? AND s.deleted_at IS NULL LIMIT 1`)
    .bind(validId).first<WorkflowTaskRow>();
  if (!current) return null;
  if (Number(current.version) !== version) workflowVersionConflict("工作事项已被其他人更新，请刷新后重试");
  const next = {
    title: input.title === undefined ? current.title : boundedText(input.title, "未命名工作项", 160, "工作事项"),
    workContent: input.workContent === undefined ? current.work_content : boundedText(input.workContent, "未填写工作内容", 2_000, "工作内容"),
    category: input.category === undefined ? current.category : boundedText(input.category, "工作计划", 80, "事项分类"),
    owner: input.owner === undefined ? current.owner : boundedText(input.owner, "未指定跟进人", 120, "跟进人"),
    shopName: input.shopName === undefined ? current.shop_name : boundedText(input.shopName, "未关联店铺", 160, "店铺"),
    startDate: input.startDate === undefined ? current.start_date : taskDate(input.startDate, "开始日期"),
    due: input.due === undefined ? current.due_date : taskDate(input.due, "截止日期"),
    status: input.status === undefined ? current.status as WorkflowTaskStatus : input.status,
    priority: input.priority === undefined ? current.priority as WorkflowTaskPriority : input.priority,
  };
  if (next.startDate !== "待排期" && next.due !== "待排期" && next.due < next.startDate) invalidWorkflowRequest("截止时间不能早于开始时间");
  const changedFields = [
    ["title", current.title, next.title], ["workContent", current.work_content, next.workContent],
    ["category", current.category, next.category], ["owner", current.owner, next.owner],
    ["shopName", current.shop_name, next.shopName], ["startDate", current.start_date, next.startDate],
    ["due", current.due_date, next.due], ["status", current.status, next.status], ["priority", current.priority, next.priority],
  ].filter(([, before, after]) => before !== after).map(([field]) => field);
  if (changedFields.length === 0) invalidWorkflowRequest("工作事项没有发生变化");
  const actor = boundedText(updatedBy, "", 320, "操作人");
  if (!actor) invalidWorkflowRequest("操作人不能为空");
  const statusChanged = current.status !== next.status;
  const nextVersion = version + 1;
  const mutationToken = crypto.randomUUID();
  await ensureWorkflowCollaborationSchema(database);
  const results = await database.batch([
    database.prepare(`UPDATE workflow_task_states SET version = ?, mutation_token = ?
      WHERE task_id = ? AND version = ? AND deleted_at IS NULL`).bind(nextVersion, mutationToken, validId, version),
    database.prepare(`UPDATE workflow_tasks SET title = ?, work_content = ?, category = ?, owner = ?, shop_name = ?,
      start_date = ?, due_date = ?, status = ?, priority = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND EXISTS (SELECT 1 FROM workflow_task_states
        WHERE task_id = ? AND version = ? AND mutation_token = ? AND deleted_at IS NULL)`)
      .bind(next.title, next.workContent, next.category, next.owner, next.shopName, next.startDate, next.due,
        next.status, next.priority, actor, validId, validId, nextVersion, mutationToken),
    database.prepare(`INSERT INTO workflow_task_activity_logs
      (id, task_id, action, summary, metadata_json, actor_email)
      SELECT ?, t.id, ?, ?, ?, ? FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id = t.id
      WHERE t.id = ? AND s.version = ? AND s.mutation_token = ? AND s.deleted_at IS NULL`)
      .bind(crypto.randomUUID(), statusChanged ? "task.status_changed" : "task.updated",
        statusChanged ? "更新了工作事项状态" : "更新了工作事项",
        JSON.stringify({ changedFields, ...(statusChanged ? { status: next.status } : {}), version: nextVersion }),
        actor, validId, nextVersion, mutationToken),
  ]);
  if (Number(results[0]?.meta?.changes ?? 0) === 0) workflowVersionConflict("工作事项已被其他人更新，请刷新后重试");
  const row = await database.prepare(`SELECT ${taskColumns} FROM workflow_tasks t
    JOIN workflow_task_states s ON s.task_id = t.id WHERE t.id = ? AND s.deleted_at IS NULL LIMIT 1`)
    .bind(validId).first<WorkflowTaskRow>();
  if (!row) throw new Error("workflow_task_update_readback_failed");
  return mapTask(row);
}

export async function deleteWorkflowTask(id: unknown, version: unknown, actor: string, db?: D1Database) {
  return deleteWorkflowTaskWithCollaboration(id, version, actor, db);
}
