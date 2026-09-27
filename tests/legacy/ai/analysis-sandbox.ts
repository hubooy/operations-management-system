import type { AppPrincipal } from "@/tests/legacy/auth/authorization";
import {
  aiScopeSnapshotAccessSql,
  serializeAiConversationScope,
} from "@/tests/legacy/ai/conversation-scope";
import { RegistryToolError } from "@/lib/ai/tool-registry-contract";
import type { D1Database } from "@/lib/database/d1";

const MAX_SOURCE_ROWS = 50;
const MAX_STEPS = 8;
const MAX_OUTPUT_ROWS = 100;
const MAX_OUTPUT_COLUMNS = 20;
const MAX_SERIALIZED_CHARACTERS = 32_000;
const UNSAFE_FIELD_NAMES = new Set(["__proto__", "constructor", "prototype"]);

export const AI_ANALYSIS_SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS ai_analysis_runs (
    id TEXT PRIMARY KEY NOT NULL,
    owner_email TEXT NOT NULL COLLATE NOCASE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('viewer', 'analyst', 'operator', 'admin')),
    scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
    dataset TEXT NOT NULL CHECK (dataset IN ('sales_category', 'netshop_product_daily', 'netshop_promotion')),
    query_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    operations_json TEXT NOT NULL CHECK (json_valid(operations_json)),
    data_cutoff_date TEXT,
    source_rows INTEGER NOT NULL CHECK (source_rows >= 0),
    returned_rows INTEGER NOT NULL CHECK (returned_rows >= 0),
    truncated INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    result_digest TEXT NOT NULL,
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS ai_analysis_runs_owner_created_idx
    ON ai_analysis_runs (owner_email, created_at, id)`,
  `CREATE INDEX IF NOT EXISTS ai_analysis_runs_dataset_created_idx
    ON ai_analysis_runs (dataset, created_at, id)`,
] as const;

const analysisSchemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export const aiAnalysisDatasetIds = [
  "sales_category",
  "netshop_product_daily",
  "netshop_promotion",
] as const;

export type AiAnalysisDatasetId = (typeof aiAnalysisDatasetIds)[number];

type JsonScalar = string | number | boolean | null;
type DataRow = Record<string, JsonScalar>;

type FilterStep = {
  op: "filter";
  field: string;
  operator: "eq" | "ne" | "contains" | "gt" | "gte" | "lt" | "lte" | "in";
  textValue?: string;
  numberValue?: number;
  values?: string[];
};

type SelectStep = { op: "select"; fields: string[] };
type DeriveStep = {
  op: "derive";
  as: string;
  operator: "add" | "subtract" | "multiply" | "divide";
  leftField?: string;
  leftValue?: number;
  rightField?: string;
  rightValue?: number;
};
type GroupMetric = { aggregate: "count" | "sum" | "avg" | "min" | "max"; field?: string; as: string };
type GroupStep = { op: "group"; groupBy?: string[]; metrics: GroupMetric[] };
type SortStep = { op: "sort"; field: string; direction?: "asc" | "desc" };
type LimitStep = { op: "limit"; count: number };
export type AiAnalysisStep = FilterStep | SelectStep | DeriveStep | GroupStep | SortStep | LimitStep;

export type AiAnalysisPlanInput = {
  dataset: AiAnalysisDatasetId;
  query?: Record<string, unknown>;
  steps?: AiAnalysisStep[];
};

export type AiAnalysisRunRecord = {
  id: string;
  dataset: AiAnalysisDatasetId;
  operations: string[];
  dataCutoffDate: string | null;
  sourceRows: number;
  returnedRows: number;
  truncated: boolean;
  resultDigest: string;
  createdAt: string;
};

export class AiAnalysisSandboxError extends RegistryToolError {
  constructor(code: string, message: string) {
    super(code, message);
    this.name = "AiAnalysisSandboxError";
  }
}

export async function ensureAiAnalysisSandboxSchema(db: D1Database): Promise<void> {
  const key = db as unknown as object;
  const existing = analysisSchemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(AI_ANALYSIS_SCHEMA_STATEMENTS.map((statement) => db.prepare(statement)))
    .then(() => undefined)
    .catch((error: unknown) => {
      analysisSchemaReadyByDatabase.delete(key);
      throw error;
    });
  analysisSchemaReadyByDatabase.set(key, setup);
  return setup;
}

export function describeAiAnalysisDatasets() {
  return {
    executionEnvironment: "deterministic_json_ast",
    networkAccess: "none_during_transform",
    arbitraryCode: false,
    datasets: [
      {
        id: "sales_category",
        title: "销售品类分析明细",
        allowedRoles: ["analyst", "operator", "admin"],
        query: ["startDate", "endDate", "categories", "channels", "platforms", "productQueries", "sortBy", "direction", "limit"],
        notes: "查询阶段按服务端 principal 数据范围过滤；金额单位为人民币分。",
      },
      {
        id: "netshop_product_daily",
        title: "网店商品日表现",
        allowedRoles: ["viewer", "analyst", "operator", "admin"],
        query: ["startDate", "endDate", "platform", "shop", "query", "limit"],
        notes: "查询阶段按认证账号的平台范围过滤；商品访客是商品×日累计。",
      },
      {
        id: "netshop_promotion",
        title: "网店推广表现",
        allowedRoles: ["viewer", "analyst", "operator", "admin"],
        query: ["startDate", "endDate", "platform", "shop", "query", "limit"],
        notes: "查询阶段按认证账号的平台范围过滤；金额单位为人民币分。",
      },
    ],
    operations: ["filter", "select", "derive", "group", "sort", "limit"],
    limits: {
      maximumSourceRows: MAX_SOURCE_ROWS,
      maximumSteps: MAX_STEPS,
      maximumOutputRows: MAX_OUTPUT_ROWS,
      maximumOutputColumns: MAX_OUTPUT_COLUMNS,
      maximumSerializedCharacters: MAX_SERIALIZED_CHARACTERS,
    },
  };
}

export async function runAiAnalysisPlan(rawInput: unknown, principal: AppPrincipal) {
  const input = normalizePlan(rawInput);
  const loaded = await loadDataset(input.dataset, input.query, principal);
  const transformed = runDeterministicAnalysisTransform(loaded.rows, input.steps);
  const rows = transformed.rows;

  const sourceWasTruncated = loaded.sourceTotal > loaded.rows.length;
  const resultWasTruncated = rows.length > MAX_OUTPUT_ROWS;
  const boundedRows = rows.slice(0, MAX_OUTPUT_ROWS).map(boundOutputColumns);
  const columns = [...new Set(boundedRows.flatMap((row) => Object.keys(row)))].slice(0, MAX_OUTPUT_COLUMNS);
  const result = {
    sandbox: {
      executionEnvironment: "deterministic_json_ast",
      arbitraryCode: false,
      evalUsed: false,
      networkAccessDuringTransform: false,
    },
    dataset: input.dataset,
    dataCutoffDate: loaded.dataCutoffDate,
    filtersApplied: loaded.filtersApplied,
    sourceRows: loaded.rows.length,
    sourceTotal: loaded.sourceTotal,
    stepsApplied: transformed.stepsApplied,
    columns,
    rows: boundedRows,
    returned: boundedRows.length,
    truncated: sourceWasTruncated || resultWasTruncated || boundedRows.some((row) => Object.keys(row).length >= MAX_OUTPUT_COLUMNS),
  };
  if (JSON.stringify(result).length > MAX_SERIALIZED_CHARACTERS) {
    throw new AiAnalysisSandboxError("analysis_result_too_large", "分析结果超过安全字符上限，请增加筛选或 limit。");
  }
  return result;
}

export async function runAndRecordAiAnalysisPlan(
  rawInput: unknown,
  principal: AppPrincipal,
  requestId: string,
  database?: D1Database,
) {
  const db = database ?? (await import("@/lib/database/d1")).getD1Database();
  await ensureAiAnalysisSandboxSchema(db);
  const input = normalizePlan(rawInput);
  const result = await runAiAnalysisPlan(input, principal);
  const id = `ai-analysis-${crypto.randomUUID()}`;
  const queryDigest = await sha256Hex(canonicalJson(input.query));
  const planDigest = await sha256Hex(canonicalJson(input.steps));
  const resultDigest = await sha256Hex(canonicalJson(result));
  const operations = input.steps.map((step) => step.op);
  const inserted = await db.prepare(`INSERT INTO ai_analysis_runs (
      id, owner_email, actor_role, scope_json, dataset, query_digest, plan_digest,
      operations_json, data_cutoff_date, source_rows, returned_rows, truncated,
      result_digest, request_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(
      id,
      principal.email,
      principal.role,
      serializeAiConversationScope(principal.scope),
      input.dataset,
      queryDigest,
      planDigest,
      JSON.stringify(operations),
      typeof result.dataCutoffDate === "string" ? result.dataCutoffDate : null,
      result.sourceRows,
      result.returned,
      result.truncated ? 1 : 0,
      resultDigest,
      normalizeRequestId(requestId),
    ).run();
  if (Number(inserted.meta.changes ?? 0) !== 1) {
    throw new AiAnalysisSandboxError("analysis_audit_unavailable", "分析运行记录未落库，结果已失败关闭。");
  }
  return { ...result, runId: id, resultDigest };
}

export async function listAiAnalysisRuns(
  principal: AppPrincipal,
  input: { page?: number; pageSize?: number } = {},
  database?: D1Database,
) {
  const db = database ?? (await import("@/lib/database/d1")).getD1Database();
  await ensureAiAnalysisSandboxSchema(db);
  const page = boundedInteger(input.page, 1, 10_000, 1);
  const pageSize = boundedInteger(input.pageSize, 1, 50, 20);
  const offset = (page - 1) * pageSize;
  const scopeAccess = aiScopeSnapshotAccessSql(principal.scope, "scope_json");
  const [countRow, rows] = await Promise.all([
    db.prepare(`SELECT COUNT(*) total FROM ai_analysis_runs
      WHERE owner_email = ?${scopeAccess.clause}`)
      .bind(principal.email, ...scopeAccess.values).first<{ total: number }>(),
    db.prepare(`SELECT id, dataset, operations_json, data_cutoff_date, source_rows,
        returned_rows, truncated, result_digest, created_at
      FROM ai_analysis_runs
      WHERE owner_email = ?${scopeAccess.clause}
      ORDER BY created_at DESC, id DESC
      LIMIT ? OFFSET ?`)
      .bind(principal.email, ...scopeAccess.values, pageSize, offset).all<{
        id: string;
        dataset: string;
        operations_json: string;
        data_cutoff_date: string | null;
        source_rows: number;
        returned_rows: number;
        truncated: number;
        result_digest: string;
        created_at: string;
      }>(),
  ]);
  const total = Math.max(0, Number(countRow?.total ?? 0));
  const items = (rows.results ?? []).map((row): AiAnalysisRunRecord => ({
    id: row.id,
    dataset: row.dataset as AiAnalysisDatasetId,
    operations: parseOperations(row.operations_json),
    dataCutoffDate: row.data_cutoff_date,
    sourceRows: Number(row.source_rows),
    returnedRows: Number(row.returned_rows),
    truncated: Boolean(row.truncated),
    resultDigest: row.result_digest,
    createdAt: row.created_at,
  }));
  return {
    items,
    pagination: {
      page,
      pageSize,
      total,
      returned: items.length,
      hasMore: offset + items.length < total,
      truncated: offset + items.length < total,
    },
  };
}

export function runDeterministicAnalysisTransform(rawRows: unknown[], rawSteps: unknown[]) {
  if (!Array.isArray(rawRows) || rawRows.length > MAX_SOURCE_ROWS) {
    throw new AiAnalysisSandboxError("invalid_analysis_input", `数据行必须是数组且最多 ${MAX_SOURCE_ROWS} 行。`);
  }
  if (!Array.isArray(rawSteps) || rawSteps.length > MAX_STEPS) {
    throw new AiAnalysisSandboxError("invalid_analysis_plan", `steps 必须是数组且最多 ${MAX_STEPS} 步。`);
  }
  let rows = normalizeRows(rawRows);
  const steps = rawSteps.map((step, index) => normalizeStep(step, index));
  const stepsApplied: Array<Record<string, unknown>> = [];
  for (const step of steps) {
    if (step.op === "filter") rows = applyFilter(rows, step);
    else if (step.op === "select") rows = applySelect(rows, step);
    else if (step.op === "derive") rows = applyDerive(rows, step);
    else if (step.op === "group") rows = applyGroup(rows, step);
    else if (step.op === "sort") rows = applySort(rows, step);
    else rows = rows.slice(0, step.count);
    stepsApplied.push({ op: step.op, rowsAfter: rows.length });
  }
  return { rows, stepsApplied };
}

function normalizePlan(rawInput: unknown): { dataset: AiAnalysisDatasetId; query: Record<string, unknown>; steps: AiAnalysisStep[] } {
  const input = requireRecord(rawInput, "分析计划");
  rejectUnexpectedKeys(input, new Set(["dataset", "query", "steps"]), "分析计划");
  const dataset = input.dataset;
  if (typeof dataset !== "string" || !aiAnalysisDatasetIds.includes(dataset as AiAnalysisDatasetId)) {
    throw new AiAnalysisSandboxError("invalid_analysis_plan", "dataset 不在允许的数据集中。");
  }
  const query = input.query === undefined ? {} : requireRecord(input.query, "query");
  validateQuery(dataset as AiAnalysisDatasetId, query);
  const rawSteps = input.steps === undefined ? [] : input.steps;
  if (!Array.isArray(rawSteps) || rawSteps.length > MAX_STEPS) {
    throw new AiAnalysisSandboxError("invalid_analysis_plan", `steps 必须是数组且最多 ${MAX_STEPS} 步。`);
  }
  return {
    dataset: dataset as AiAnalysisDatasetId,
    query,
    steps: rawSteps.map((step, index) => normalizeStep(step, index)),
  };
}

function validateQuery(dataset: AiAnalysisDatasetId, query: Record<string, unknown>) {
  const salesKeys = new Set(["startDate", "endDate", "categories", "channels", "platforms", "productQueries", "sortBy", "direction", "limit"]);
  const netshopKeys = new Set(["startDate", "endDate", "platform", "shop", "query", "limit"]);
  rejectUnexpectedKeys(query, dataset === "sales_category" ? salesKeys : netshopKeys, "query");
  if (dataset === "sales_category") {
    if (!dateText(query.startDate) || !dateText(query.endDate)) {
      throw new AiAnalysisSandboxError("invalid_analysis_query", "销售品类数据集必须提供 YYYY-MM-DD 的 startDate 和 endDate。");
    }
  }
  for (const [key, value] of Object.entries(query)) {
    if (["categories", "channels", "platforms", "productQueries"].includes(key)) {
      if (!Array.isArray(value) || value.length > 20 || value.some((item) => typeof item !== "string" || item.length > 120)) {
        throw new AiAnalysisSandboxError("invalid_analysis_query", `${key} 必须是最多 20 项的短字符串数组。`);
      }
    } else if (key === "limit") {
      if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > MAX_SOURCE_ROWS) {
        throw new AiAnalysisSandboxError("invalid_analysis_query", `limit 必须是 1-${MAX_SOURCE_ROWS} 的整数。`);
      }
    } else if (typeof value !== "string" || value.length > 120) {
      throw new AiAnalysisSandboxError("invalid_analysis_query", `${key} 必须是短字符串。`);
    }
  }
}

function normalizeStep(rawStep: unknown, index: number): AiAnalysisStep {
  const step = requireRecord(rawStep, `steps[${index}]`);
  const op = step.op;
  if (op === "filter") {
    rejectUnexpectedKeys(step, new Set(["op", "field", "operator", "textValue", "numberValue", "values"]), `steps[${index}]`);
    const field = safeField(step.field, `steps[${index}].field`);
    const operator = step.operator;
    if (!["eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"].includes(String(operator))) throw invalidStep(index);
    if (operator === "in") {
      if (!Array.isArray(step.values) || step.values.length < 1 || step.values.length > 20 || step.values.some((value) => typeof value !== "string" || value.length > 120)) throw invalidStep(index);
    } else if (["gt", "gte", "lt", "lte"].includes(String(operator))) {
      if (typeof step.numberValue !== "number" || !Number.isFinite(step.numberValue)) throw invalidStep(index);
    } else if (typeof step.textValue !== "string" || step.textValue.length > 240) throw invalidStep(index);
    return { op, field, operator: operator as FilterStep["operator"], textValue: step.textValue as string | undefined, numberValue: step.numberValue as number | undefined, values: step.values as string[] | undefined };
  }
  if (op === "select") {
    rejectUnexpectedKeys(step, new Set(["op", "fields"]), `steps[${index}]`);
    return { op, fields: safeFieldList(step.fields, `steps[${index}].fields`, 1) };
  }
  if (op === "derive") {
    rejectUnexpectedKeys(step, new Set(["op", "as", "operator", "leftField", "leftValue", "rightField", "rightValue"]), `steps[${index}]`);
    const operator = step.operator;
    if (!["add", "subtract", "multiply", "divide"].includes(String(operator))) throw invalidStep(index);
    const left = normalizeOperand(step.leftField, step.leftValue, `steps[${index}].left`);
    const right = normalizeOperand(step.rightField, step.rightValue, `steps[${index}].right`);
    return { op, as: safeField(step.as, `steps[${index}].as`), operator: operator as DeriveStep["operator"], ...left, ...right } as DeriveStep;
  }
  if (op === "group") {
    rejectUnexpectedKeys(step, new Set(["op", "groupBy", "metrics"]), `steps[${index}]`);
    const groupBy = step.groupBy === undefined ? [] : safeFieldList(step.groupBy, `steps[${index}].groupBy`, 0);
    if (!Array.isArray(step.metrics) || step.metrics.length < 1 || step.metrics.length > 10) throw invalidStep(index);
    const metrics = step.metrics.map((rawMetric, metricIndex) => {
      const metric = requireRecord(rawMetric, `steps[${index}].metrics[${metricIndex}]`);
      rejectUnexpectedKeys(metric, new Set(["aggregate", "field", "as"]), `steps[${index}].metrics[${metricIndex}]`);
      const aggregate = metric.aggregate;
      if (!["count", "sum", "avg", "min", "max"].includes(String(aggregate))) throw invalidStep(index);
      const field = aggregate === "count" && metric.field === undefined ? undefined : safeField(metric.field, `steps[${index}].metrics[${metricIndex}].field`);
      return { aggregate: aggregate as GroupMetric["aggregate"], field, as: safeField(metric.as, `steps[${index}].metrics[${metricIndex}].as`) };
    });
    if (groupBy.length + metrics.length > MAX_OUTPUT_COLUMNS) throw invalidStep(index);
    return { op, groupBy, metrics };
  }
  if (op === "sort") {
    rejectUnexpectedKeys(step, new Set(["op", "field", "direction"]), `steps[${index}]`);
    if (step.direction !== undefined && step.direction !== "asc" && step.direction !== "desc") throw invalidStep(index);
    return { op, field: safeField(step.field, `steps[${index}].field`), direction: step.direction as SortStep["direction"] };
  }
  if (op === "limit") {
    rejectUnexpectedKeys(step, new Set(["op", "count"]), `steps[${index}]`);
    if (!Number.isSafeInteger(step.count) || Number(step.count) < 1 || Number(step.count) > MAX_OUTPUT_ROWS) throw invalidStep(index);
    return { op, count: Number(step.count) };
  }
  throw invalidStep(index);
}

async function loadDataset(dataset: AiAnalysisDatasetId, query: Record<string, unknown>, principal: AppPrincipal) {
  if (dataset === "sales_category") {
    if (principal.role === "viewer") throw new AiAnalysisSandboxError("forbidden", "当前角色无权分析销售品类数据。");
    const { getSalesCategoryAnalysisForAi } = await import("@/lib/sales/category-ai-tool");
    const payload = await getSalesCategoryAnalysisForAi({ ...query, limit: boundedSourceLimit(query.limit, 50) }, principal);
    const items = Array.isArray(payload.items) ? payload.items : [];
    return {
      rows: normalizeRows(items),
      sourceTotal: Number(payload.totalMatched ?? items.length),
      dataCutoffDate: payload.dataCutoffDate ?? null,
      filtersApplied: payload.filtersApplied ?? {},
    };
  }
  const { getNetshopPerformanceForAi } = await import("@/lib/netshop/ai-tool");
  const payload = await getNetshopPerformanceForAi({
    ...query,
    dataset: dataset === "netshop_promotion" ? "promotion" : "product_daily",
    limit: boundedSourceLimit(query.limit, 20),
  }, principal);
  const items = Array.isArray(payload.items) ? payload.items : [];
  return {
    rows: normalizeRows(items),
    sourceTotal: payload.truncated ? Math.max(items.length + 1, Number(payload.returned ?? items.length)) : Number(payload.returned ?? items.length),
    dataCutoffDate: payload.dataCutoffDate ?? null,
    filtersApplied: { dataset: payload.dataset, requestedPeriod: payload.requestedPeriod, coverage: payload.coverage },
  };
}

function normalizeRows(values: unknown[]): DataRow[] {
  return values.slice(0, MAX_SOURCE_ROWS).map((value) => {
    const source = requireRecord(value, "数据行");
    const row: DataRow = {};
    for (const [key, item] of Object.entries(source)) {
      if (Object.keys(row).length >= MAX_OUTPUT_COLUMNS || UNSAFE_FIELD_NAMES.has(key)) break;
      if (item === null || typeof item === "string" || typeof item === "boolean") row[key] = typeof item === "string" ? item.slice(0, 500) : item;
      else if (typeof item === "number" && Number.isFinite(item)) row[key] = item;
    }
    return row;
  });
}

function applyFilter(rows: DataRow[], step: FilterStep): DataRow[] {
  return rows.filter((row) => {
    const value = row[step.field];
    if (step.operator === "in") return step.values!.includes(String(value ?? ""));
    if (["gt", "gte", "lt", "lte"].includes(step.operator)) {
      if (typeof value !== "number") return false;
      const target = step.numberValue!;
      if (step.operator === "gt") return value > target;
      if (step.operator === "gte") return value >= target;
      if (step.operator === "lt") return value < target;
      return value <= target;
    }
    const actual = String(value ?? "");
    const target = step.textValue!;
    if (step.operator === "contains") return actual.toLocaleLowerCase("zh-CN").includes(target.toLocaleLowerCase("zh-CN"));
    return step.operator === "eq" ? actual === target : actual !== target;
  });
}

function applySelect(rows: DataRow[], step: SelectStep): DataRow[] {
  return rows.map((row) => Object.fromEntries(step.fields.filter((field) => Object.hasOwn(row, field)).map((field) => [field, row[field]!])) as DataRow);
}

function applyDerive(rows: DataRow[], step: DeriveStep): DataRow[] {
  return rows.map((row) => {
    const left = step.leftField ? numeric(row[step.leftField]) : step.leftValue!;
    const right = step.rightField ? numeric(row[step.rightField]) : step.rightValue!;
    let value: number | null = null;
    if (left !== null && right !== null) {
      if (step.operator === "add") value = left + right;
      else if (step.operator === "subtract") value = left - right;
      else if (step.operator === "multiply") value = left * right;
      else if (right !== 0) value = left / right;
    }
    return { ...row, [step.as]: value !== null && Number.isFinite(value) ? value : null };
  });
}

function applyGroup(rows: DataRow[], step: GroupStep): DataRow[] {
  const groups = new Map<string, DataRow[]>();
  for (const row of rows) {
    const values = (step.groupBy ?? []).map((field) => row[field] ?? null);
    const key = JSON.stringify(values);
    const group = groups.get(key) ?? [];
    group.push(row);
    groups.set(key, group);
  }
  return [...groups.entries()].map(([key, group]) => {
    const values = JSON.parse(key) as JsonScalar[];
    const row: DataRow = {};
    (step.groupBy ?? []).forEach((field, index) => { row[field] = values[index] ?? null; });
    for (const metric of step.metrics) {
      const numbers = metric.field === undefined ? [] : group.map((item) => numeric(item[metric.field!])).filter((value): value is number => value !== null);
      if (metric.aggregate === "count") row[metric.as] = metric.field ? numbers.length : group.length;
      else if (numbers.length === 0) row[metric.as] = null;
      else if (metric.aggregate === "sum") row[metric.as] = numbers.reduce((sum, value) => sum + value, 0);
      else if (metric.aggregate === "avg") row[metric.as] = numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
      else if (metric.aggregate === "min") row[metric.as] = Math.min(...numbers);
      else row[metric.as] = Math.max(...numbers);
    }
    return row;
  });
}

function applySort(rows: DataRow[], step: SortStep): DataRow[] {
  const direction = step.direction === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => compareScalars(left[step.field] ?? null, right[step.field] ?? null) * direction);
}

function compareScalars(left: JsonScalar, right: JsonScalar) {
  if (left === right) return 0;
  if (left === null) return -1;
  if (right === null) return 1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), "zh-CN");
}

function boundOutputColumns(row: DataRow): DataRow {
  return Object.fromEntries(Object.entries(row).slice(0, MAX_OUTPUT_COLUMNS)) as DataRow;
}

function normalizeOperand(field: unknown, value: unknown, path: string) {
  if ((field === undefined) === (value === undefined)) throw new AiAnalysisSandboxError("invalid_analysis_plan", `${path} 必须且只能提供 field 或 value。`);
  if (field !== undefined) return path.endsWith("left") ? { leftField: safeField(field, `${path}Field`) } : { rightField: safeField(field, `${path}Field`) };
  if (typeof value !== "number" || !Number.isFinite(value)) throw new AiAnalysisSandboxError("invalid_analysis_plan", `${path}Value 必须是有限数字。`);
  return path.endsWith("left") ? { leftValue: value } : { rightValue: value };
}

function safeFieldList(value: unknown, path: string, minimum: number): string[] {
  if (!Array.isArray(value) || value.length < minimum || value.length > MAX_OUTPUT_COLUMNS) throw new AiAnalysisSandboxError("invalid_analysis_plan", `${path} 必须是 ${minimum}-${MAX_OUTPUT_COLUMNS} 项字段数组。`);
  return [...new Set(value.map((item) => safeField(item, path)))];
}

function safeField(value: unknown, path: string): string {
  if (typeof value !== "string" || !/^[\p{L}\p{N}_.$-]{1,80}$/u.test(value) || UNSAFE_FIELD_NAMES.has(value)) {
    throw new AiAnalysisSandboxError("invalid_analysis_plan", `${path} 不是安全字段名。`);
  }
  return value;
}

function rejectUnexpectedKeys(value: Record<string, unknown>, allowed: Set<string>, path: string) {
  const unexpected = Object.keys(value).filter((key) => !allowed.has(key) || UNSAFE_FIELD_NAMES.has(key));
  if (unexpected.length) throw new AiAnalysisSandboxError("invalid_analysis_plan", `${path} 包含未声明字段：${unexpected.join("、")}。`);
}

function requireRecord(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AiAnalysisSandboxError("invalid_analysis_plan", `${path} 必须是 JSON 对象。`);
  return value as Record<string, unknown>;
}

function invalidStep(index: number) {
  return new AiAnalysisSandboxError("invalid_analysis_plan", `steps[${index}] 的操作参数无效。`);
}

function boundedSourceLimit(value: unknown, datasetMaximum: number) {
  return Math.min(datasetMaximum, MAX_SOURCE_ROWS, Number.isSafeInteger(value) ? Number(value) : datasetMaximum);
}

function numeric(value: JsonScalar | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function dateText(value: unknown) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function parseOperations(value: string): string[] {
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string").slice(0, MAX_STEPS) : [];
  } catch {
    return [];
  }
}

function normalizeRequestId(value: string) {
  const normalized = value.trim().slice(0, 160);
  return normalized || `ai-analysis-request-${crypto.randomUUID()}`;
}

function boundedInteger(value: number | undefined, minimum: number, maximum: number, fallback: number) {
  return Number.isSafeInteger(value) ? Math.min(maximum, Math.max(minimum, Number(value))) : fallback;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

async function sha256Hex(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
