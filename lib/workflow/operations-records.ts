import type { AppPrincipal } from "@/lib/auth/authorization";
import type { D1Database } from "@/lib/database/d1";

export const operationRecordTypes = ["inspection", "review"] as const;
export type OperationRecordType = (typeof operationRecordTypes)[number];

export const operationRecordPriorities = ["high", "normal", "low"] as const;
export type OperationRecordPriority = (typeof operationRecordPriorities)[number];

export const operationRecordSources = ["manual", "system", "import", "integration"] as const;
export type OperationRecordSource = (typeof operationRecordSources)[number];

export const operationRecordStatuses = {
  inspection: ["正常", "待处理", "处理中", "已关闭"],
  review: ["待回复", "处理中", "已回复", "无需回复"],
} as const satisfies Record<OperationRecordType, readonly string[]>;

export type OperationRecord = {
  id: string;
  type: OperationRecordType;
  title: string;
  status: string;
  priority: OperationRecordPriority;
  platform: string;
  channel: string;
  shopName: string;
  owner: string;
  occurredAt: string;
  dueAt: string | null;
  content: string;
  source: OperationRecordSource;
  sourceRef: string;
  referenceCode: string;
  version: number;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

export type OperationRecordActivity = {
  id: string;
  recordId: string;
  action: "created" | "updated" | "status_changed" | "deleted";
  actorEmail: string;
  actorRole: string;
  fromVersion: number | null;
  toVersion: number;
  changedFields: string[];
  fromStatus: string | null;
  toStatus: string | null;
  createdAt: string;
};

export type CreateOperationRecordInput = {
  type?: unknown;
  title?: unknown;
  status?: unknown;
  priority?: unknown;
  platform?: unknown;
  channel?: unknown;
  shopName?: unknown;
  owner?: unknown;
  occurredAt?: unknown;
  dueAt?: unknown;
  content?: unknown;
  source?: unknown;
  sourceRef?: unknown;
  referenceCode?: unknown;
};

export type UpdateOperationRecordInput = Omit<CreateOperationRecordInput, "type" | "source"> & {
  expectedVersion?: unknown;
};

export type OperationRecordListInput = {
  types?: readonly unknown[];
  statuses?: readonly unknown[];
  shopNames?: readonly unknown[];
  platforms?: readonly unknown[];
  owners?: readonly unknown[];
  query?: unknown;
  from?: unknown;
  to?: unknown;
  page?: unknown;
  pageSize?: unknown;
};

export class OperationRecordRequestError extends Error {
  readonly status: 400 | 403 | 404 | 409;
  readonly code: "invalid_request" | "access_denied" | "not_found" | "version_conflict";

  constructor(status: OperationRecordRequestError["status"], code: OperationRecordRequestError["code"], message: string) {
    super(message);
    this.name = "OperationRecordRequestError";
    this.status = status;
    this.code = code;
  }
}

type RecordRow = {
  id: string;
  record_type: string;
  title: string;
  status: string;
  priority: string;
  platform: string;
  channel: string;
  shop_name: string;
  owner: string;
  occurred_at: string;
  due_at: string | null;
  content: string;
  source: string;
  source_ref: string;
  reference_code: string;
  version: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
};

type ActivityRow = {
  id: string;
  record_id: string;
  action: OperationRecordActivity["action"];
  actor_email: string;
  actor_role: string;
  from_version: number | null;
  to_version: number;
  detail_json: string;
  created_at: string;
};

const MAX_PAGE_SIZE = 100;
const MAX_FILTER_VALUES = 20;
const MAX_OFFSET = 100_000;
const recordColumns = `
  id, record_type, title, status, priority, platform, channel, shop_name, owner,
  occurred_at, due_at, content, source, source_ref, reference_code, version,
  created_by, updated_by, created_at, updated_at
`;

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS workflow_operation_records (
    id TEXT PRIMARY KEY NOT NULL,
    record_type TEXT NOT NULL CHECK (record_type IN ('inspection', 'review')),
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high', 'normal', 'low')),
    platform TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    due_at TEXT,
    content TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'system', 'import', 'integration')),
    source_ref TEXT NOT NULL DEFAULT '',
    reference_code TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    mutation_token TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    deleted_by TEXT
  )`,
  `CREATE INDEX IF NOT EXISTS workflow_operation_records_type_status_time_idx
    ON workflow_operation_records (record_type, status, occurred_at DESC, id DESC)`,
  `CREATE INDEX IF NOT EXISTS workflow_operation_records_shop_type_time_idx
    ON workflow_operation_records (shop_name, record_type, occurred_at DESC, id DESC)`,
  `CREATE TABLE IF NOT EXISTS workflow_operation_activities (
    id TEXT PRIMARY KEY NOT NULL,
    record_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('created', 'updated', 'status_changed', 'deleted')),
    actor_email TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    from_version INTEGER,
    to_version INTEGER NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS workflow_operation_activities_record_created_idx
    ON workflow_operation_activities (record_id, to_version DESC, id DESC)`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

async function operationsDatabase(db?: D1Database) {
  if (db) return db;
  const { getD1Database } = await import("@/lib/database/d1");
  return getD1Database();
}

export async function ensureOperationRecordsSchema(db?: D1Database): Promise<void> {
  const database = await operationsDatabase(db);
  const key = database as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = database.batch(schemaStatements.map((sql) => database.prepare(sql)))
    .then(() => undefined)
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

function requestError(message: string): never {
  throw new OperationRecordRequestError(400, "invalid_request", message);
}

function requiredText(value: unknown, label: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim()) requestError(`${label}不能为空`);
  const normalized = value.trim();
  if (Array.from(normalized).length > maxLength) requestError(`${label}不能超过 ${maxLength} 个字符`);
  return normalized;
}

function optionalText(value: unknown, label: string, maxLength: number, fallback = ""): string {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "string") requestError(`${label}必须是文本`);
  const normalized = value.trim();
  if (Array.from(normalized).length > maxLength) requestError(`${label}不能超过 ${maxLength} 个字符`);
  return normalized;
}

function dateTime(value: unknown, label: string, nullable = false): string | null {
  if ((value === null || value === undefined || value === "") && nullable) return null;
  if (typeof value !== "string") requestError(`${label}必须是 ISO 日期或日期时间`);
  const normalized = value.trim();
  const dateMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(normalized);
  const validDateParts = (year: number, month: number, day: number) => {
    const calendar = new Date(Date.UTC(year, month - 1, day));
    return calendar.getUTCFullYear() === year && calendar.getUTCMonth() === month - 1 && calendar.getUTCDate() === day;
  };
  if (dateMatch) {
    const year = Number(dateMatch[1]); const month = Number(dateMatch[2]); const day = Number(dateMatch[3]);
    if (!validDateParts(year, month, day)) requestError(`${label}不是有效日期`);
    return new Date(`${normalized}T00:00:00+08:00`).toISOString();
  }
  const zoned = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|([+-])(\d{2}):(\d{2}))$/.exec(normalized);
  if (!zoned) requestError(`${label}必须是 YYYY-MM-DD 或包含时区的 ISO 日期时间`);
  const [year, month, day, hour, minute, second] = zoned.slice(1, 7).map((item) => Number(item ?? 0));
  const offsetHour = Number(zoned[10] ?? 0); const offsetMinute = Number(zoned[11] ?? 0);
  if (!validDateParts(year, month, day) || hour > 23 || minute > 59 || second > 59
    || offsetHour > 14 || offsetMinute > 59 || (offsetHour === 14 && offsetMinute !== 0)) {
    requestError(`${label}不是有效日期时间`);
  }
  const milliseconds = Date.parse(normalized);
  if (!Number.isFinite(milliseconds)) {
    requestError(`${label}必须是 YYYY-MM-DD 或包含时区的 ISO 日期时间`);
  }
  return new Date(milliseconds).toISOString();
}

function recordType(value: unknown): OperationRecordType {
  if (typeof value !== "string" || !operationRecordTypes.includes(value as OperationRecordType)) {
    requestError("运营记录类型无效");
  }
  return value as OperationRecordType;
}

function recordPriority(value: unknown, fallback: OperationRecordPriority): OperationRecordPriority {
  if (value === undefined) return fallback;
  if (typeof value !== "string" || !operationRecordPriorities.includes(value as OperationRecordPriority)) {
    requestError("优先级无效");
  }
  return value as OperationRecordPriority;
}

function recordSource(value: unknown): OperationRecordSource {
  if (value === undefined) return "manual";
  if (typeof value !== "string" || !operationRecordSources.includes(value as OperationRecordSource)) {
    requestError("记录来源无效");
  }
  return value as OperationRecordSource;
}

function recordStatus(type: OperationRecordType, value: unknown, fallback?: string): string {
  const candidate = value === undefined ? fallback : value;
  if (typeof candidate !== "string" || !(operationRecordStatuses[type] as readonly string[]).includes(candidate)) {
    requestError(`${type} 的状态无效`);
  }
  return candidate;
}

function positiveInteger(value: unknown, fallback: number, max: number, label: string): number {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = typeof value === "number" ? value
    : typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : Number.NaN;
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > max) requestError(`${label}必须在 1 到 ${max} 之间`);
  return parsed;
}

function requiredVersion(value: unknown): number {
  if (value === undefined || value === null || value === "") requestError("预期版本不能为空");
  return positiveInteger(value, 1, 2_147_483_646, "预期版本");
}

function assertAllowedKeys(input: object, allowed: readonly string[]) {
  const allowedSet = new Set(allowed);
  const unknown = Object.keys(input).filter((key) => !allowedSet.has(key));
  if (unknown.length > 0) requestError(`包含不支持的字段：${unknown.slice(0, 5).join("、")}`);
}

function boundedList(values: readonly unknown[] | undefined, label: string, allowed?: readonly string[]): string[] {
  if (!values) return [];
  if (values.length > MAX_FILTER_VALUES) requestError(`${label}最多允许 ${MAX_FILTER_VALUES} 项`);
  const normalized = values.map((value) => requiredText(value, label, 160));
  const unique = [...new Set(normalized)];
  if (allowed && unique.some((value) => !allowed.includes(value))) requestError(`${label}包含无效值`);
  return unique;
}

function mapRecord(row: RecordRow): OperationRecord {
  return {
    id: row.id,
    type: row.record_type as OperationRecordType,
    title: row.title,
    status: row.status,
    priority: row.priority as OperationRecordPriority,
    platform: row.platform,
    channel: row.channel,
    shopName: row.shop_name,
    owner: row.owner,
    occurredAt: row.occurred_at,
    dueAt: row.due_at,
    content: row.content,
    source: row.source as OperationRecordSource,
    sourceRef: row.source_ref,
    referenceCode: row.reference_code,
    version: Number(row.version),
    createdBy: row.created_by,
    updatedBy: row.updated_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function parseActivityDetail(value: string): { changedFields: string[]; fromStatus: string | null; toStatus: string | null } {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    return {
      changedFields: Array.isArray(parsed.changedFields)
        ? parsed.changedFields.filter((item): item is string => typeof item === "string").slice(0, 20)
        : [],
      fromStatus: typeof parsed.fromStatus === "string" ? parsed.fromStatus : null,
      toStatus: typeof parsed.toStatus === "string" ? parsed.toStatus : null,
    };
  } catch {
    return { changedFields: [], fromStatus: null, toStatus: null };
  }
}

function mapActivity(row: ActivityRow): OperationRecordActivity {
  const detail = parseActivityDetail(row.detail_json);
  return {
    id: row.id,
    recordId: row.record_id,
    action: row.action,
    actorEmail: row.actor_email,
    actorRole: row.actor_role,
    fromVersion: row.from_version === null ? null : Number(row.from_version),
    toVersion: Number(row.to_version),
    ...detail,
    createdAt: row.created_at,
  };
}

function placeholders(values: readonly unknown[]) {
  return values.map(() => "?").join(", ");
}

function scopeSql(principal: AppPrincipal, alias = "") {
  if (principal.scope === null) return { clause: "", values: [] as string[] };
  const prefix = alias ? `${alias}.` : "";
  const channels = [...new Set(principal.scope.channels.map((item) => item.trim()).filter(Boolean))];
  const platforms = [...new Set(principal.scope.platforms.map((item) => item.trim()).filter(Boolean))];
  const clauses: string[] = [];
  if (channels.length > 0) clauses.push(`${prefix}channel IN (${placeholders(channels)})`);
  if (platforms.length > 0) clauses.push(`${prefix}platform IN (${placeholders(platforms)})`);
  return clauses.length === 0
    ? { clause: "1 = 0", values: [] as string[] }
    : { clause: `(${clauses.join(" OR ")})`, values: [...channels, ...platforms] };
}

function assertWritableScope(principal: AppPrincipal, platform: string, channel: string) {
  if (principal.scope === null) return;
  const allowed = principal.scope.platforms.includes(platform) || principal.scope.channels.includes(channel);
  if (!allowed) {
    throw new OperationRecordRequestError(403, "access_denied", "当前账号不能写入该平台或渠道的运营记录");
  }
}

function normalizeCreate(input: CreateOperationRecordInput, actor: AppPrincipal) {
  assertAllowedKeys(input, [
    "type", "title", "status", "priority", "platform", "channel", "shopName", "owner",
    "occurredAt", "dueAt", "content", "source", "sourceRef", "referenceCode",
  ]);
  const type = recordType(input.type);
  const source = recordSource(input.source);
  if (source !== "manual" && actor.role !== "admin") {
    throw new OperationRecordRequestError(403, "access_denied", "只有管理员可以登记非手工来源的运营记录");
  }
  const platform = optionalText(input.platform, "平台", 80);
  const channel = optionalText(input.channel, "渠道", 80);
  const occurredAt = dateTime(input.occurredAt, "发生时间") as string;
  const dueAt = dateTime(input.dueAt, "截止时间", true);
  assertWritableScope(actor, platform, channel);
  return {
    type,
    title: requiredText(input.title, "标题", 200),
    status: recordStatus(type, input.status, operationRecordStatuses[type][0]),
    priority: recordPriority(input.priority, "normal"),
    platform,
    channel,
    shopName: requiredText(input.shopName, "店铺", 160),
    owner: optionalText(input.owner, "责任人", 120),
    occurredAt,
    dueAt,
    content: optionalText(input.content, "内容", 4_000),
    source,
    sourceRef: optionalText(input.sourceRef, "来源标识", 300),
    referenceCode: optionalText(input.referenceCode, "业务参考编码", 160),
  };
}

export function normalizeOperationRecordListInput(input: OperationRecordListInput) {
  const page = positiveInteger(input.page, 1, 100_000, "页码");
  const pageSize = positiveInteger(input.pageSize, 30, MAX_PAGE_SIZE, "每页条数");
  const query = optionalText(input.query, "搜索词", 80);
  const from = dateTime(input.from, "开始时间", true);
  const to = dateTime(input.to, "结束时间", true);
  if (from && to && from >= to) requestError("时间范围必须满足开始时间早于结束时间");
  const offset = (page - 1) * pageSize;
  if (offset > MAX_OFFSET) requestError(`分页偏移不能超过 ${MAX_OFFSET}`);
  return {
    types: boundedList(input.types, "类型", operationRecordTypes),
    statuses: boundedList(input.statuses, "状态"),
    shopNames: boundedList(input.shopNames, "店铺"),
    platforms: boundedList(input.platforms, "平台"),
    owners: boundedList(input.owners, "责任人"),
    query,
    from,
    to,
    page,
    pageSize,
  };
}

function escapedLike(value: string) {
  return `%${value.replace(/\\/g, "\\\\").replace(/%/g, "\\%").replace(/_/g, "\\_")}%`;
}

export async function listOperationRecords(input: OperationRecordListInput, principal: AppPrincipal, db?: D1Database) {
  const database = await operationsDatabase(db);
  await ensureOperationRecordsSchema(database);
  const filters = normalizeOperationRecordListInput(input);
  const clauses = ["deleted_at IS NULL", "record_type <> 'launch'"];
  const values: unknown[] = [];
  const appendList = (column: string, items: string[]) => {
    if (items.length === 0) return;
    clauses.push(`${column} IN (${placeholders(items)})`);
    values.push(...items);
  };
  appendList("record_type", filters.types);
  appendList("status", filters.statuses);
  appendList("shop_name", filters.shopNames);
  appendList("platform", filters.platforms);
  appendList("owner", filters.owners);
  if (filters.query) {
    const like = escapedLike(filters.query);
    clauses.push(`(title LIKE ? ESCAPE '\\' COLLATE NOCASE OR content LIKE ? ESCAPE '\\' COLLATE NOCASE
      OR shop_name LIKE ? ESCAPE '\\' COLLATE NOCASE OR owner LIKE ? ESCAPE '\\' COLLATE NOCASE
      OR reference_code LIKE ? ESCAPE '\\' COLLATE NOCASE)`);
    values.push(like, like, like, like, like);
  }
  if (filters.from) {
    clauses.push("occurred_at >= ?");
    values.push(filters.from);
  }
  if (filters.to) {
    clauses.push("occurred_at < ?");
    values.push(filters.to);
  }
  const scope = scopeSql(principal);
  if (scope.clause) {
    clauses.push(scope.clause);
    values.push(...scope.values);
  }
  const where = `WHERE ${clauses.join(" AND ")}`;
  const offset = (filters.page - 1) * filters.pageSize;
  const [count, rows] = await Promise.all([
    database.prepare(`SELECT COUNT(*) AS count FROM workflow_operation_records ${where}`)
      .bind(...values).first<{ count: number }>(),
    database.prepare(`SELECT ${recordColumns} FROM workflow_operation_records ${where}
      ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?`)
      .bind(...values, filters.pageSize, offset).all<RecordRow>(),
  ]);
  const items = (rows.results ?? []).map(mapRecord);
  const total = Number(count?.count ?? 0);
  return {
    items,
    pagination: {
      page: filters.page,
      pageSize: filters.pageSize,
      total,
      returned: items.length,
      truncated: offset + items.length < total,
    },
    filtersApplied: {
      ...filters,
      dataScope: principal.scope === null ? "unrestricted" as const : "restricted" as const,
    },
  };
}

async function findVisibleRecord(database: D1Database, id: string, principal: AppPrincipal, includeDeleted = false) {
  const scope = scopeSql(principal);
  const sql = `SELECT ${recordColumns} FROM workflow_operation_records
    WHERE id = ?${includeDeleted ? "" : " AND deleted_at IS NULL"}${scope.clause ? ` AND ${scope.clause}` : ""} LIMIT 1`;
  return database.prepare(sql).bind(id, ...scope.values).first<RecordRow>();
}

export async function getOperationRecord(id: unknown, principal: AppPrincipal, db?: D1Database) {
  const database = await operationsDatabase(db);
  await ensureOperationRecordsSchema(database);
  const validId = requiredText(id, "记录标识", 128);
  const row = await findVisibleRecord(database, validId, principal);
  return row ? mapRecord(row) : null;
}

export async function createOperationRecord(input: CreateOperationRecordInput, actor: AppPrincipal, db?: D1Database) {
  const database = await operationsDatabase(db);
  await ensureOperationRecordsSchema(database);
  const record = normalizeCreate(input, actor);
  const id = crypto.randomUUID();
  const activityId = crypto.randomUUID();
  const changedFields = [
    "type", "title", "status", "priority", "platform", "channel", "shopName", "owner",
    "occurredAt", "dueAt", "content", "source", "sourceRef", "referenceCode",
  ];
  await database.batch([
    database.prepare(`INSERT INTO workflow_operation_records (
      id, record_type, title, status, priority, platform, channel, shop_name, owner,
      occurred_at, due_at, content, source, source_ref, reference_code, created_by, updated_by
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .bind(id, record.type, record.title, record.status, record.priority, record.platform, record.channel,
        record.shopName, record.owner, record.occurredAt, record.dueAt, record.content, record.source,
        record.sourceRef, record.referenceCode, actor.email, actor.email),
    database.prepare(`INSERT INTO workflow_operation_activities (
      id, record_id, action, actor_email, actor_role, from_version, to_version, detail_json
    ) VALUES (?, ?, 'created', ?, ?, NULL, 1, ?)`)
      .bind(activityId, id, actor.email, actor.role, JSON.stringify({ changedFields, fromStatus: null, toStatus: record.status })),
  ]);
  const saved = await database.prepare(`SELECT ${recordColumns} FROM workflow_operation_records WHERE id = ? LIMIT 1`)
    .bind(id).first<RecordRow>();
  if (!saved) throw new Error("运营记录保存后无法读取");
  return mapRecord(saved);
}

function nextRecord(current: RecordRow, input: UpdateOperationRecordInput, actor: AppPrincipal) {
  const type = current.record_type as OperationRecordType;
  const editable = [
    "title", "status", "priority", "platform", "channel", "shopName", "owner", "occurredAt",
    "dueAt", "content", "sourceRef", "referenceCode",
  ] as const;
  assertAllowedKeys(input, [...editable, "expectedVersion"]);
  if (!editable.some((field) => input[field] !== undefined)) requestError("缺少可更新字段");
  const next = {
    title: input.title === undefined ? current.title : requiredText(input.title, "标题", 200),
    status: recordStatus(type, input.status, current.status),
    priority: recordPriority(input.priority, current.priority as OperationRecordPriority),
    platform: input.platform === undefined ? current.platform : optionalText(input.platform, "平台", 80),
    channel: input.channel === undefined ? current.channel : optionalText(input.channel, "渠道", 80),
    shopName: input.shopName === undefined ? current.shop_name : requiredText(input.shopName, "店铺", 160),
    owner: input.owner === undefined ? current.owner : optionalText(input.owner, "责任人", 120),
    occurredAt: input.occurredAt === undefined ? current.occurred_at : dateTime(input.occurredAt, "发生时间") as string,
    dueAt: input.dueAt === undefined ? current.due_at : dateTime(input.dueAt, "截止时间", true),
    content: input.content === undefined ? current.content : optionalText(input.content, "内容", 4_000),
    sourceRef: input.sourceRef === undefined ? current.source_ref : optionalText(input.sourceRef, "来源标识", 300),
    referenceCode: input.referenceCode === undefined ? current.reference_code : optionalText(input.referenceCode, "业务参考编码", 160),
  };
  assertWritableScope(actor, next.platform, next.channel);
  return next;
}

export async function updateOperationRecord(id: unknown, input: UpdateOperationRecordInput, actor: AppPrincipal, db?: D1Database) {
  const database = await operationsDatabase(db);
  await ensureOperationRecordsSchema(database);
  const validId = requiredText(id, "记录标识", 128);
  const expectedVersion = requiredVersion(input.expectedVersion);
  const current = await findVisibleRecord(database, validId, actor);
  if (!current) throw new OperationRecordRequestError(404, "not_found", "运营记录不存在或不可访问");
  if (Number(current.version) !== expectedVersion) {
    throw new OperationRecordRequestError(409, "version_conflict", "运营记录已被其他人更新，请刷新后重试");
  }
  const next = nextRecord(current, input, actor);
  const fieldMap = {
    title: [current.title, next.title], status: [current.status, next.status], priority: [current.priority, next.priority],
    platform: [current.platform, next.platform], channel: [current.channel, next.channel], shopName: [current.shop_name, next.shopName],
    owner: [current.owner, next.owner], occurredAt: [current.occurred_at, next.occurredAt], dueAt: [current.due_at, next.dueAt],
    content: [current.content, next.content], sourceRef: [current.source_ref, next.sourceRef], referenceCode: [current.reference_code, next.referenceCode],
  } as const;
  const changedFields = Object.entries(fieldMap).filter(([, pair]) => pair[0] !== pair[1]).map(([field]) => field);
  if (changedFields.length === 0) requestError("运营记录没有发生变化");
  const nextVersion = expectedVersion + 1;
  const activityId = crypto.randomUUID();
  const action = current.status === next.status ? "updated" : "status_changed";
  const results = await database.batch([
    database.prepare(`UPDATE workflow_operation_records SET
      title = ?, status = ?, priority = ?, platform = ?, channel = ?, shop_name = ?, owner = ?,
      occurred_at = ?, due_at = ?, content = ?, source_ref = ?, reference_code = ?, version = ?, mutation_token = ?,
      updated_by = ?, updated_at = CURRENT_TIMESTAMP
      WHERE id = ? AND version = ? AND deleted_at IS NULL`)
      .bind(next.title, next.status, next.priority, next.platform, next.channel, next.shopName, next.owner,
        next.occurredAt, next.dueAt, next.content, next.sourceRef, next.referenceCode, nextVersion, activityId,
        actor.email, validId, expectedVersion),
    database.prepare(`INSERT INTO workflow_operation_activities (
      id, record_id, action, actor_email, actor_role, from_version, to_version, detail_json
    ) SELECT ?, id, ?, ?, ?, ?, version, ? FROM workflow_operation_records
      WHERE id = ? AND version = ? AND mutation_token = ? AND deleted_at IS NULL`)
      .bind(activityId, action, actor.email, actor.role, expectedVersion,
        JSON.stringify({ changedFields, fromStatus: current.status, toStatus: next.status }), validId, nextVersion, activityId),
  ]);
  if (Number(results[0]?.meta?.changes ?? 0) === 0) {
    throw new OperationRecordRequestError(409, "version_conflict", "运营记录已被其他人更新，请刷新后重试");
  }
  const saved = await database.prepare(`SELECT ${recordColumns} FROM workflow_operation_records WHERE id = ? LIMIT 1`)
    .bind(validId).first<RecordRow>();
  if (!saved) throw new Error("运营记录更新后无法读取");
  return mapRecord(saved);
}

export async function deleteOperationRecord(id: unknown, expectedVersionInput: unknown, actor: AppPrincipal, db?: D1Database) {
  const database = await operationsDatabase(db);
  await ensureOperationRecordsSchema(database);
  const validId = requiredText(id, "记录标识", 128);
  const expectedVersion = requiredVersion(expectedVersionInput);
  const current = await findVisibleRecord(database, validId, actor);
  if (!current) throw new OperationRecordRequestError(404, "not_found", "运营记录不存在或不可访问");
  if (Number(current.version) !== expectedVersion) {
    throw new OperationRecordRequestError(409, "version_conflict", "运营记录已被其他人更新，请刷新后重试");
  }
  const nextVersion = expectedVersion + 1;
  const activityId = crypto.randomUUID();
  const results = await database.batch([
    database.prepare(`UPDATE workflow_operation_records SET
      version = ?, mutation_token = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?
      WHERE id = ? AND version = ? AND deleted_at IS NULL`)
      .bind(nextVersion, activityId, actor.email, actor.email, validId, expectedVersion),
    database.prepare(`INSERT INTO workflow_operation_activities (
      id, record_id, action, actor_email, actor_role, from_version, to_version, detail_json
    ) SELECT ?, id, 'deleted', ?, ?, ?, version, ? FROM workflow_operation_records
      WHERE id = ? AND version = ? AND mutation_token = ? AND deleted_at IS NOT NULL`)
      .bind(activityId, actor.email, actor.role, expectedVersion,
        JSON.stringify({ changedFields: ["deletedAt"], fromStatus: current.status, toStatus: current.status }), validId, nextVersion, activityId),
  ]);
  if (Number(results[0]?.meta?.changes ?? 0) === 0) {
    throw new OperationRecordRequestError(409, "version_conflict", "运营记录已被其他人更新，请刷新后重试");
  }
  return { id: validId, deleted: true, version: nextVersion };
}

export async function listOperationRecordActivities(
  id: unknown,
  input: { page?: unknown; pageSize?: unknown },
  principal: AppPrincipal,
  db?: D1Database,
) {
  const database = await operationsDatabase(db);
  await ensureOperationRecordsSchema(database);
  const validId = requiredText(id, "记录标识", 128);
  const visible = await findVisibleRecord(database, validId, principal, true);
  if (!visible) throw new OperationRecordRequestError(404, "not_found", "运营记录不存在或不可访问");
  const page = positiveInteger(input.page, 1, 100_000, "页码");
  const pageSize = positiveInteger(input.pageSize, 30, MAX_PAGE_SIZE, "每页条数");
  const offset = (page - 1) * pageSize;
  if (offset > MAX_OFFSET) requestError(`分页偏移不能超过 ${MAX_OFFSET}`);
  const [count, rows] = await Promise.all([
    database.prepare("SELECT COUNT(*) AS count FROM workflow_operation_activities WHERE record_id = ?")
      .bind(validId).first<{ count: number }>(),
    database.prepare(`SELECT id, record_id, action, actor_email, actor_role, from_version, to_version, detail_json, created_at
      FROM workflow_operation_activities WHERE record_id = ? ORDER BY to_version DESC, id DESC LIMIT ? OFFSET ?`)
      .bind(validId, pageSize, offset).all<ActivityRow>(),
  ]);
  const items = (rows.results ?? []).map(mapActivity);
  const total = Number(count?.count ?? 0);
  return { items, pagination: { page, pageSize, total, returned: items.length, truncated: offset + items.length < total } };
}
