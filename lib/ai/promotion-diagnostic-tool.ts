import jdStoreRegistry from "@/config/jd-store-accounts.json";
import { AuthorizationError } from "@/lib/auth/authorization";
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
import { createDjangoNetshopService, NETSHOP_PROMOTION_DIAGNOSTIC_PATH } from "@/lib/django/netshop-service";
import { promotionSystemSourceReady } from "@/lib/jd/promotion-diagnostic-identity";
import { appendPromotionRelations, linksForPromotionTarget } from "@/lib/jd/promotion-diagnostic-relations";
import {
  buildPromotionDiagnosticReport,
  type DiagnosticPeriod,
  type PromotionDiagnosticReport,
  type ReportTable,
} from "@/lib/jd/promotion-diagnostic-report";
import { netshopOutletsForPrincipal, netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import { netshopOutletKey, resolveNetshopQueryPeriod } from "@/lib/netshop/query-contract";

const SHOP_NAME = "志高商用设备旗舰店";
const MAX_RESULT_CHARS = 36_000;
const TABLE_KEYS = new Set(["summary", "daily", "plans", "products", "keywords", "searchTerms",
  "keywordSku", "planSku", "planKeyword", "searchTermSku", "actions", "coverage"]);
const RELATION_TARGETS = new Set(["plans", "products", "keywords", "searchTerms"]);

export type PromotionDiagnosticToolArgs = {
  shopName: string;
  startDate: string;
  endDate: string;
  sourceRevision?: string;
  mode?: "overview" | "table" | "relations";
  tableKey?: string;
  groupKey?: string;
  filterColumn?: string;
  filterValue?: string;
  page?: number;
  pageSize?: number;
  sortColumn?: string;
  sortDirection?: "asc" | "desc";
};

export type PromotionDiagnosticReader = (
  context: AiToolExecutionContext, startDate: string, endDate: string,
) => Promise<DiagnosticPeriod>;

async function readSystemPeriod(context: AiToolExecutionContext, startDate: string, endDate: string) {
  const query = new URLSearchParams({ platform: "京东", outlet: netshopOutletKey("京东", SHOP_NAME), startDate, endDate });
  const response = await createDjangoNetshopService().request<DiagnosticPeriod>(context.principal,
    { method: "GET", path: NETSHOP_PROMOTION_DIAGNOSTIC_PATH, query, service: "reader" },
    { signal: context.signal });
  return response.data;
}

function invalid(message: string): never {
  throw new Error(`推广诊断参数无效：${message}`);
}

function validateArgs(value: Record<string, unknown>): PromotionDiagnosticToolArgs {
  const allowed = new Set(["shopName", "startDate", "endDate", "sourceRevision", "mode", "tableKey", "groupKey",
    "filterColumn", "filterValue", "page", "pageSize", "sortColumn", "sortDirection"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) invalid("包含未知字段");
  if (value.shopName !== SHOP_NAME) invalid(`当前 AI 对话仅支持京东${SHOP_NAME}；其他店铺尚未接入`);
  if (typeof value.startDate !== "string" || typeof value.endDate !== "string") invalid("须明确起止日期");
  const period = resolveNetshopQueryPeriod(value.startDate, value.endDate, 7);
  if (!period) invalid("须明确1—7个完整自然日");
  const mode = value.mode ?? "overview";
  if (mode !== "overview" && mode !== "table" && mode !== "relations") invalid("mode 不受支持");
  if (mode === "overview" && ["sourceRevision", "tableKey", "groupKey", "filterColumn", "filterValue", "page", "pageSize", "sortColumn", "sortDirection"].some((key) => value[key] !== undefined)) invalid("总览不接受明细筛选");
  if (mode !== "overview" && (typeof value.tableKey !== "string" || !TABLE_KEYS.has(value.tableKey))) invalid("表名不受支持");
  if (mode !== "overview" && (typeof value.sourceRevision !== "string" || !/^[A-Za-z0-9:._-]{1,120}$/.test(value.sourceRevision))) invalid("明细查询须带总览返回的来源修订");
  if (mode === "relations" && (!RELATION_TARGETS.has(value.tableKey as string)
    || typeof value.groupKey !== "string" || !value.groupKey || value.groupKey.length > 240
    || ["filterColumn", "filterValue", "page", "pageSize", "sortColumn", "sortDirection"].some((key) => value[key] !== undefined))) invalid("关系查询须使用计划、商品、关键词或搜索词的精确来源键");
  if (mode === "table") {
    if (value.groupKey !== undefined && (typeof value.groupKey !== "string" || !value.groupKey || value.groupKey.length > 240)) invalid("来源键无效");
    if (value.filterColumn !== undefined && (typeof value.filterColumn !== "string" || !/^[A-Za-z][A-Za-z0-9]{0,39}$/.test(value.filterColumn)
      || typeof value.filterValue !== "string" || value.filterValue.length > 240)) invalid("表筛选无效");
    if (value.filterValue !== undefined && value.filterColumn === undefined) invalid("筛选值须指定列");
    if (value.page !== undefined && (!Number.isSafeInteger(value.page) || Number(value.page) < 1 || Number(value.page) > 100_000)) invalid("页码无效");
    if (value.pageSize !== undefined && (!Number.isSafeInteger(value.pageSize) || Number(value.pageSize) < 1 || Number(value.pageSize) > 20)) invalid("每页最多20行");
    if (value.sortColumn !== undefined && (typeof value.sortColumn !== "string" || !/^[A-Za-z][A-Za-z0-9]{0,39}$/.test(value.sortColumn))) invalid("排序列无效");
    if (value.sortDirection !== undefined && (value.sortDirection !== "asc" && value.sortDirection !== "desc" || value.sortColumn === undefined)) invalid("排序方向无效");
  }
  return value as PromotionDiagnosticToolArgs;
}

function previousPeriod(startDate: string, days: number) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const date = (time: number) => new Date(time).toISOString().slice(0, 10);
  return { startDate: date(start - days * 86_400_000), endDate: date(start - 86_400_000) };
}

function rowsAsObjects(table: ReportTable, rows: ReportTable["rows"]) {
  return rows.map((row) => Object.fromEntries(table.columns.map((column, index) => [column.key, row[index] ?? null])));
}

function boundedRows<T>(base: Record<string, unknown>, rows: T[]): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base, rows };
  const length = JSON.stringify(result).length;
  if (length <= MAX_RESULT_CHARS) return result;
  // Never remove page-tail rows while retaining the original page offset: that
  // would silently skip those rows on the next page.
  return { status: "page_too_large", mode: "table", tableKey: base.tableKey,
    sourceRevision: base.sourceRevision, totalRows: base.totalRows,
    requestedPage: base.page, requestedPageSize: base.pageSize,
    suggestedPageSize: Math.max(1, Math.min(Number(base.pageSize) - 1, Math.floor(Number(base.pageSize) * MAX_RESULT_CHARS / length))),
    restartPage: 1,
    reason: "本页完整证据超过模型工具上限；没有返回任何本页行。请用建议的更小 pageSize 从第1页重新分页，或增加精确筛选。" };
}

function tablePage(report: PromotionDiagnosticReport, args: PromotionDiagnosticToolArgs,
  locator: Record<string, string>): Record<string, unknown> {
  const table = report.tables.find((item) => item.key === args.tableKey)!;
  const groupColumn = table.columns.findIndex((item) => item.key === "groupKey");
  if (args.groupKey !== undefined && groupColumn < 0) invalid("此表没有来源键");
  const filterColumn = args.filterColumn === undefined ? -1 : table.columns.findIndex((item) => item.key === args.filterColumn);
  if (args.filterColumn !== undefined && filterColumn < 0) invalid("筛选列不属于此表");
  const sortColumn = args.sortColumn === undefined ? -1 : table.columns.findIndex((item) => item.key === args.sortColumn && item.kind !== "text");
  if (args.sortColumn !== undefined && sortColumn < 0) invalid("排序列须是此表的数值指标");
  const rows = table.rows.filter((row) => (args.groupKey === undefined || row[groupColumn] === args.groupKey)
    && (filterColumn < 0 || String(row[filterColumn] ?? "") === args.filterValue));
  if (sortColumn >= 0) rows.sort((a, b) => {
    const left = a[sortColumn], right = b[sortColumn];
    if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1;
    if (typeof left !== "number" || typeof right !== "number") invalid("排序指标不是数值");
    return args.sortDirection === "asc" ? left - right : right - left;
  });
  const page = args.page ?? 1, pageSize = args.pageSize ?? 10;
  const selected = rows.slice((page - 1) * pageSize, page * pageSize);
  return boundedRows({ status: "complete", mode: "table", reportLocator: locator, sourceRevision: report.sourceRevision,
    tableKey: table.key, title: table.title, note: table.note, columns: table.columns,
    totalRows: rows.length, page, pageSize, hasMore: page * pageSize < rows.length,
    appliedFilter: { groupKey: args.groupKey ?? null, column: args.filterColumn ?? null, value: args.filterValue ?? null },
    appliedSort: { column: args.sortColumn ?? null, direction: args.sortColumn ? args.sortDirection ?? "desc" : null },
  }, rowsAsObjects(table, selected));
}

function relationView(report: PromotionDiagnosticReport, current: DiagnosticPeriod, args: PromotionDiagnosticToolArgs,
  locator: Record<string, string>): Record<string, unknown> {
  const target = { tableKey: args.tableKey!, groupKey: args.groupKey! };
  const targetTable = report.tables.find((item) => item.key === target.tableKey)!;
  const groupColumn = targetTable.columns.findIndex((item) => item.key === "groupKey");
  const targetRow = targetTable.rows.find((row) => row[groupColumn] === target.groupKey);
  if (!targetRow) invalid("找不到当前修订下的来源对象，请先查询表格取得来源键");
  const stateColumn = targetTable.columns.findIndex((item) => item.key === "state");
  const previousOnly = stateColumn >= 0 && targetRow[stateColumn] === "消失";
  if (previousOnly) return { status: "complete", mode: "relations", reportLocator: locator,
    target, targetEvidence: rowsAsObjects(targetTable, [targetRow])[0], relationCoverage: "previous_only_not_queried",
    relations: [], note: "此对象仅在前等长周期出现；本期关系表不适用，前期 SKU/词关系未在本次关系查询中读取。空列表不表示该对象没有关联。" };
  const links = linksForPromotionTarget(current, target.tableKey, target.groupKey);
  const relations = links.map((link) => {
    const table = report.tables.find((item) => item.key === link.tableKey)!;
    const column = table.columns.findIndex((item) => item.key === link.columnKey);
    const rows = table.rows.filter((row) => row[column] === link.value);
    return { ...link, totalRows: rows.length, columns: table.columns, rows: rowsAsObjects(table, rows.slice(0, 5)),
      hasMore: rows.length > 5 };
  });
  const result = { status: "complete", mode: "relations", reportLocator: locator,
    target, targetEvidence: rowsAsObjects(targetTable, [targetRow])[0], relationCoverage: "current_source", relations,
    note: "关系仅表示同一推广来源行中实际共现的身份；各视角不可相加。更多明细请按链接表名与列值使用 table 模式分页。" };
  if (JSON.stringify(result).length > MAX_RESULT_CHARS) throw new Error("推广关系证据超过对话工具上限，请改用 table 模式精确筛选");
  return result;
}

/** Model-callable read-only view. The model never receives the full source payload. */
export async function getJdPromotionDiagnosticForChat(
  rawArgs: Record<string, unknown>, context: AiToolExecutionContext,
  reader: PromotionDiagnosticReader = readSystemPeriod,
): Promise<Record<string, unknown>> {
  if (context.principal.role !== "admin" || context.principal.scope !== null
    || (context.surface !== "ai_chat" && context.surface !== "test")) {
    throw new AuthorizationError(403, "access_denied", "当前账号或入口无权使用京东推广深度诊断");
  }
  const args = validateArgs(rawArgs);
  const store = jdStoreRegistry.stores.find((item) => item.storeKey === "jd-yiyong-director");
  if (!store || store.platform !== "京东" || store.shopName !== SHOP_NAME || store.shopId !== "701455") {
    throw new Error("运营系统受控店铺注册信息与推广诊断范围不一致");
  }
  netshopPlatformsForPrincipal(context.principal, ["京东"]);
  netshopOutletsForPrincipal(context.principal, [{ platform: "京东", shopName: SHOP_NAME }], ["京东"]);
  const period = resolveNetshopQueryPeriod(args.startDate, args.endDate, 7)!;
  const previous = previousPeriod(period.startDate, period.days);
  const [current, baseline] = await Promise.all([
    reader(context, period.startDate, period.endDate),
    reader(context, previous.startDate, previous.endDate),
  ]);
  const availability = { currentMissingDates: current.coverage?.missingDates ?? [],
    previousMissingDates: baseline.coverage?.missingDates ?? [] };
  if (!promotionSystemSourceReady(current, baseline)) {
    return { status: "source_unavailable", shopName: SHOP_NAME, period: { startDate: period.startDate, endDate: period.endDate },
      previousPeriod: previous, ...availability,
      reason: "本期或前期的系统导入日期、批次归属或零警告条件尚未完整对账；不能形成可比诊断。" };
  }
  const report = appendPromotionRelations(buildPromotionDiagnosticReport(current, baseline), current);
  if (!report.comparisonAvailable) return { status: "source_unavailable", shopName: SHOP_NAME,
    period: report.period, previousPeriod: previous,
    reason: "两个周期的来源修订、天数或覆盖不可比；不能形成环比结论。" };
  if (args.sourceRevision !== undefined && args.sourceRevision !== report.sourceRevision) {
    return { status: "source_changed", shopName: SHOP_NAME, period: report.period,
      reason: "推广来源修订已变化，请重新运行总览并按新来源键定位对象。" };
  }
  const locator = { shopName: SHOP_NAME, startDate: period.startDate, endDate: period.endDate,
    sourceRevision: report.sourceRevision };
  if (args.mode === "table") return tablePage(report, args, locator);
  if (args.mode === "relations") return relationView(report, current, args, locator);
  const result = {
    status: "complete", mode: "overview", reportLocator: locator,
    analysisType: report.analysisType, platform: "京东", period: report.period,
    previousPeriod: report.previousPeriod, comparisonAvailable: report.comparisonAvailable,
    metrics: report.metrics, previousMetrics: report.previousMetrics, coverage: report.coverage,
    sourceBatches: {
      current: current.sourceBatches.map((day) => ({ date: day.date, batchIds: day.batchIds, aggregateBatchId: day.aggregateBatchId, rowCount: day.rowCount })),
      previous: baseline.sourceBatches.map((day) => ({ date: day.date, batchIds: day.batchIds, aggregateBatchId: day.aggregateBatchId, rowCount: day.rowCount })),
    },
    summary: report.tables.find((table) => table.key === "summary")?.rows.map((row) => ({
      metric: row[0], current: row[1], previous: row[2], change: row[3], unit: row[4],
    })) ?? [],
    findings: report.findings.slice(0, 10), actions: report.actions.slice(0, 10),
    tables: report.tables.map((table) => ({ key: table.key, title: table.title, rowCount: table.rows.length,
      evidenceKey: table.columns.some((column) => column.key === "groupKey") ? "groupKey" : null })),
    limitations: report.limitations,
    guidance: "数值是系统代码计算的京准通平台归因口径。用 table 模式分页读取计划、商品、关键词和搜索词；用 relations 模式追溯对象关联。不可推断利润、B端销售、同比或真实增量；建议需人工复核。",
  };
  if (JSON.stringify(result).length > MAX_RESULT_CHARS) throw new Error("推广总览超过对话工具上限");
  return result;
}
