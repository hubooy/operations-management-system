import {
  findLatestInventoryImportBatch,
  getReplenishmentPlanSummary,
  queryReplenishmentPlans,
  type InventoryDatabase,
} from "@/lib/inventory/database";
import {
  calculateInventoryCostValuation,
  normalizeInventoryPagination,
} from "@/lib/inventory/query-contract";
import { resolveInventorySalesPeriod } from "@/lib/inventory/sales-period";
import { readOperatingSettings } from "@/lib/settings/service";
import {
  assessInventoryOverviewQuality,
  type InventoryDataQuality,
} from "@/lib/inventory/data-quality";
import {
  jdInboundWarehousePredicateSql,
  resolvedGroupedWarehouseTypeSql,
} from "@/lib/inventory/warehouse-classification";
import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
  type SalesConsumerResponseMap,
} from "@/lib/django/sales-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";

export type InventoryHealthStatus = "urgent" | "replenish" | "healthy" | "slow" | "stagnant" | "no_sales";

export type InventoryOverviewItem = {
  key: string;
  productCode: string;
  productName: string;
  specification: string;
  category: string;
  warehouse: string;
  warehouseType: "owned" | "jd_rdc" | "other";
  onHandQuantity: number;
  availableQuantity: number;
  lockedQuantity: number;
  sourceInTransitQuantity: number;
  plannedInTransitQuantity: number;
  totalInTransitQuantity: number;
  unitCostCents: number;
  inventoryAgeDays: number | null;
  stockValueCents: number | null;
  knownStockValueCents: number;
  costCoverageRate: number;
  sales30d: number | null;
  averageDailySales: number | null;
  coverageDays: number | null;
  suggestedQuantity: number | null;
  status: InventoryHealthStatus;
  statusLabel: string;
  reason: string;
  inDraftPlan: boolean;
};

export type InventoryOverviewOptions = {
  query?: string;
  startDate?: string;
  endDate?: string;
  warehouses?: string[];
  brands?: string[];
  categories?: string[];
  warehouseTypes?: InventoryOverviewItem["warehouseType"][];
  statuses?: InventoryHealthStatus[];
  page?: number;
  pageSize?: number;
  exactKey?: string;
  planPage?: number;
  planPageSize?: number;
  planStatus?: "draft" | "confirmed" | "completed" | "cancelled";
  includeCancelledPlans?: boolean;
  signal?: AbortSignal;
};

export type InventoryDashboardOverviewResponse = {
  hasInventory: boolean;
  sync: {
    latestInventoryBatchId: string | null;
    inventoryAsOf: string | null;
    inventorySyncedAt: string | null;
    salesThrough: string | null;
    salesWindowStart: string | null;
    latestInventoryFile: string | null;
    inventoryStale: boolean;
  };
  metrics: {
    skuWarehouseCount: number;
    totalAvailableQuantity: number;
    totalStockValueCents: number;
    knownStockValueCents: number;
    stockValueComplete: boolean;
    costCoverageRate: number;
    salesDemandMatchRate: number;
    averageCoverageDays: number | null;
    urgentCount: number;
    replenishCount: number;
    slowMovingValueCents: number;
    noSalesCount: number;
    recommendationCount: number;
    inventoryAlertsEnabled: boolean;
    recommendationsSuppressed: boolean;
    qualityIssues: InventoryDataQuality["issues"];
  };
  health: {
    urgent: number;
    replenish: number;
    healthy: number;
    slow: number;
    stagnant: number;
    noSales: number;
  };
  controls: {
    autoReplenishmentEnabled: boolean;
    alertsEnabled: boolean;
  };
  quality: InventoryDataQuality;
};

export type InventoryDashboardProjectionResponse = Omit<InventoryDashboardOverviewResponse, "controls" | "quality">;

export type InventoryMappingGap = {
  key: string;
  productCode: string;
  productName: string;
  inventoryWarehouse: string;
  warehouseType: InventoryOverviewItem["warehouseType"];
  availableQuantity: number;
  candidateSalesWarehouses: string[];
};

type InventoryThresholdSettings = {
  targetDays: number;
  criticalDays: number;
  replenishDays: number;
  slowDays: number;
  stagnantDays: number;
  salesWindowDays: number;
  autoReplenishment: boolean;
  inventoryAlert: boolean;
};

type OverviewRow = {
  product_code: string;
  product_name: string;
  resolved_product_name: string;
  specification: string;
  category: string;
  warehouse: string;
  warehouse_type: string;
  on_hand_quantity: number;
  available_quantity: number;
  locked_quantity: number;
  in_transit_quantity: number;
  imported_stock_value_cents: number;
  priced_available_quantity: number;
  inventory_age_days: number | null;
  sales_quantity: number | null;
  absolute_quantity: number;
  absolute_cost_cents: number;
  fallback_unit_cost_cents: number;
  planned_in_transit_quantity: number;
  in_draft_plan: number;
  status: InventoryHealthStatus;
  suggested_quantity: number | null;
};

type OverviewMetricsRow = {
  total: number;
  total_available_quantity: number;
  known_stock_value_cents: number;
  positive_available_quantity: number;
  covered_quantity: number;
  matched_count: number;
  total_daily_sales: number;
  demand_available_quantity: number;
  urgent_count: number;
  replenish_count: number;
  healthy_count: number;
  slow_count: number;
  stagnant_count: number;
  no_sales_count: number;
  slow_moving_value_cents: number;
  recommendation_count: number;
  has_jd_rdc: number;
};

type InventoryOverviewProjectionRow = Record<string, unknown> & {
  section_order: unknown;
  section: unknown;
  section_index: unknown;
  item_json: unknown;
};

type InventoryOverviewContext = {
  latestBatch: Awaited<ReturnType<typeof findLatestInventoryImportBatch>>;
  salesStartDate: string | null;
  salesEndDate: string | null;
  salesWindowDays: number;
  settings: InventoryThresholdSettings;
  salesRevision: string;
  demandRows: Map<string, SalesConsumerResponseMap["inventory_demand"]["rows"][number]>;
};

const INVENTORY_DEMAND_PRODUCT_CHUNK_SIZE = 500;
const INVENTORY_DEMAND_LIMIT = 10_000;
const MAX_INVENTORY_DEMAND_PRODUCTS = 20_000;

const EMPTY_OVERVIEW_METRICS: OverviewMetricsRow = {
  total: 0,
  total_available_quantity: 0,
  known_stock_value_cents: 0,
  positive_available_quantity: 0,
  covered_quantity: 0,
  matched_count: 0,
  total_daily_sales: 0,
  demand_available_quantity: 0,
  urgent_count: 0,
  replenish_count: 0,
  healthy_count: 0,
  slow_count: 0,
  stagnant_count: 0,
  no_sales_count: 0,
  slow_moving_value_cents: 0,
  recommendation_count: 0,
  has_jd_rdc: 0,
};

const EMPTY_PLAN_SUMMARY = {
  draftCount: 0,
  confirmedCount: 0,
  completedCount: 0,
  cancelledCount: 0,
  activeQuantity: 0,
} as const;

const INVENTORY_HEALTH_STATUSES: InventoryHealthStatus[] = [
  "urgent",
  "replenish",
  "healthy",
  "slow",
  "stagnant",
  "no_sales",
];

const INVENTORY_PAGE_ORDER_SQL = `CASE status
  WHEN 'urgent' THEN 0
  WHEN 'replenish' THEN 1
  WHEN 'healthy' THEN 2
  WHEN 'slow' THEN 3
  WHEN 'stagnant' THEN 4
  ELSE 5
END, coverage_days ASC, product_code ASC, warehouse ASC`;

const INVENTORY_RECOMMENDATION_ORDER_SQL = "suggested_quantity DESC, product_code ASC, warehouse ASC";

const INVENTORY_OVERVIEW_METRICS_SELECT_SQL = `SELECT
  COUNT(*) AS total,
  COALESCE(SUM(available_quantity), 0) AS total_available_quantity,
  COALESCE(SUM(known_stock_value_cents), 0) AS known_stock_value_cents,
  COALESCE(SUM(MAX(available_quantity, 0)), 0) AS positive_available_quantity,
  COALESCE(SUM(cost_covered_quantity), 0) AS covered_quantity,
  COALESCE(SUM(CASE WHEN sales_quantity IS NOT NULL THEN 1 ELSE 0 END), 0) AS matched_count,
  COALESCE(SUM(CASE WHEN sales_quantity > 0 THEN sales_quantity * 1.0 / ? ELSE 0 END), 0) AS total_daily_sales,
  COALESCE(SUM(CASE WHEN sales_quantity > 0 THEN MAX(available_quantity, 0) ELSE 0 END), 0) AS demand_available_quantity,
  COALESCE(SUM(CASE WHEN status = 'urgent' THEN 1 ELSE 0 END), 0) AS urgent_count,
  COALESCE(SUM(CASE WHEN status = 'replenish' THEN 1 ELSE 0 END), 0) AS replenish_count,
  COALESCE(SUM(CASE WHEN status = 'healthy' THEN 1 ELSE 0 END), 0) AS healthy_count,
  COALESCE(SUM(CASE WHEN status = 'slow' THEN 1 ELSE 0 END), 0) AS slow_count,
  COALESCE(SUM(CASE WHEN status = 'stagnant' THEN 1 ELSE 0 END), 0) AS stagnant_count,
  COALESCE(SUM(CASE WHEN status = 'no_sales' THEN 1 ELSE 0 END), 0) AS no_sales_count,
  COALESCE(SUM(CASE WHEN status IN ('slow', 'stagnant', 'no_sales') THEN known_stock_value_cents ELSE 0 END), 0) AS slow_moving_value_cents,
  COALESCE(SUM(CASE WHEN suggested_quantity > 0 THEN 1 ELSE 0 END), 0) AS recommendation_count,
  COALESCE(MAX(CASE WHEN warehouse_type = 'jd_rdc' THEN 1 ELSE 0 END), 0) AS has_jd_rdc
FROM filtered`;

const INVENTORY_OVERVIEW_METRIC_FIELDS = [
  "total",
  "total_available_quantity",
  "known_stock_value_cents",
  "positive_available_quantity",
  "covered_quantity",
  "matched_count",
  "total_daily_sales",
  "demand_available_quantity",
  "urgent_count",
  "replenish_count",
  "healthy_count",
  "slow_count",
  "stagnant_count",
  "no_sales_count",
  "slow_moving_value_cents",
  "recommendation_count",
  "has_jd_rdc",
] as const satisfies readonly (keyof OverviewMetricsRow)[];

const INVENTORY_OVERVIEW_COUNT_METRIC_FIELDS = [
  "total",
  "matched_count",
  "urgent_count",
  "replenish_count",
  "healthy_count",
  "slow_count",
  "stagnant_count",
  "no_sales_count",
  "recommendation_count",
  "has_jd_rdc",
] as const satisfies readonly (keyof OverviewMetricsRow)[];

const INVENTORY_OVERVIEW_METRICS_COLUMN_SQL = INVENTORY_OVERVIEW_METRIC_FIELDS.join(", ");
const INVENTORY_OVERVIEW_NULL_METRICS_SQL = INVENTORY_OVERVIEW_METRIC_FIELDS
  .map((field) => `NULL AS ${field}`)
  .join(", ");

// D1 limits every SQL function call to 32 arguments. Keep each json_object at
// no more than 16 key/value pairs, and only put nullable values in the base
// object because RFC 7396 merge-patch treats null values as key deletion.
const INVENTORY_OVERVIEW_ROW_JSON_BASE_SQL = `json_object(
  'product_code', product_code,
  'product_name', COALESCE(product_name, ''),
  'resolved_product_name', resolved_product_name,
  'specification', COALESCE(specification, ''),
  'category', COALESCE(category, ''),
  'warehouse', warehouse,
  'warehouse_type', warehouse_type,
  'inventory_age_days', inventory_age_days,
  'sales_quantity', sales_quantity,
  'suggested_quantity', suggested_quantity,
  'on_hand_quantity', on_hand_quantity,
  'available_quantity', available_quantity,
  'locked_quantity', locked_quantity,
  'in_transit_quantity', in_transit_quantity,
  'imported_stock_value_cents', imported_stock_value_cents
)`;

const INVENTORY_OVERVIEW_ROW_JSON_EXTENSION_SQL = `json_object(
  'priced_available_quantity', priced_available_quantity,
  'absolute_quantity', absolute_quantity,
  'absolute_cost_cents', absolute_cost_cents,
  'fallback_unit_cost_cents', fallback_unit_cost_cents,
  'planned_in_transit_quantity', planned_in_transit_quantity,
  'in_draft_plan', in_draft_plan,
  'status', status
)`;

const INVENTORY_OVERVIEW_ROW_JSON_SQL = `json_patch(
  ${INVENTORY_OVERVIEW_ROW_JSON_BASE_SQL},
  ${INVENTORY_OVERVIEW_ROW_JSON_EXTENSION_SQL}
)`;

const INVENTORY_OVERVIEW_NUMERIC_FIELDS = [
  "on_hand_quantity",
  "available_quantity",
  "locked_quantity",
  "in_transit_quantity",
  "imported_stock_value_cents",
  "priced_available_quantity",
  "absolute_quantity",
  "absolute_cost_cents",
  "fallback_unit_cost_cents",
  "planned_in_transit_quantity",
] as const;

function parseInventoryOverviewRow(value: unknown, label: string): OverviewRow {
  if (typeof value !== "string") throw new Error(`${label}查询没有返回有效 JSON`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label}查询返回了无效 JSON`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label}查询返回的数据不是对象`);
  }
  const row = parsed as Record<string, unknown>;
  if (!["product_code", "product_name", "resolved_product_name", "specification", "category", "warehouse"]
    .every((field) => typeof row[field] === "string")
    || (row.warehouse_type !== "owned" && row.warehouse_type !== "jd_rdc" && row.warehouse_type !== "other")
    || !INVENTORY_OVERVIEW_NUMERIC_FIELDS.every((field) => typeof row[field] === "number" && Number.isFinite(row[field]))
    || (row.in_draft_plan !== 0 && row.in_draft_plan !== 1)
    || !INVENTORY_HEALTH_STATUSES.includes(row.status as InventoryHealthStatus)) {
    throw new Error(`${label}查询返回了无效的数据结构`);
  }
  for (const field of ["inventory_age_days", "sales_quantity", "suggested_quantity"] as const) {
    if (row[field] !== null && (typeof row[field] !== "number" || !Number.isFinite(row[field]))) {
      throw new Error(`${label}查询返回了无效的数据结构`);
    }
  }
  return row as OverviewRow;
}

function parseInventoryOverviewProjection(
  rows: readonly InventoryOverviewProjectionRow[],
  pagination: ReturnType<typeof normalizeInventoryPagination>,
) {
  if (rows.length === 0 || rows.length > 151) throw new Error("库存总览投影返回行数无效");
  let metrics: OverviewMetricsRow | null = null;
  const items: OverviewRow[] = [];
  const recommendations: OverviewRow[] = [];
  const seenIndexes = new Set<string>();
  let previousSectionOrder = -1;
  let previousSectionIndex = -1;

  for (const row of rows) {
    const section = row.section;
    const sectionOrder = row.section_order;
    const sectionIndex = row.section_index;
    const expectedSectionOrder = section === "metrics" ? 0 : section === "page" ? 1 : section === "recommendation" ? 2 : -1;
    if (expectedSectionOrder < 0
      || typeof sectionOrder !== "number"
      || sectionOrder !== expectedSectionOrder
      || !Number.isSafeInteger(sectionIndex)
      || Number(sectionIndex) < 0) {
      throw new Error("库存总览投影包含无效分区或索引");
    }
    const numericIndex = Number(sectionIndex);
    const identity = `${section}:${numericIndex}`;
    if (seenIndexes.has(identity)) throw new Error("库存总览投影包含重复索引");
    seenIndexes.add(identity);
    if (sectionOrder < previousSectionOrder || (sectionOrder === previousSectionOrder && numericIndex <= previousSectionIndex)) {
      throw new Error("库存总览投影顺序无效");
    }
    previousSectionOrder = sectionOrder;
    previousSectionIndex = numericIndex;

    if (section === "metrics") {
      if (metrics || numericIndex !== 0 || row.item_json !== null) throw new Error("库存总览投影指标分区无效");
      const metricEntries = INVENTORY_OVERVIEW_METRIC_FIELDS.map((field) => {
        const value = row[field];
        if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("库存总览投影指标无效");
        return [field, value] as const;
      });
      metrics = Object.fromEntries(metricEntries) as unknown as OverviewMetricsRow;
      if (INVENTORY_OVERVIEW_COUNT_METRIC_FIELDS.some((field) => !Number.isSafeInteger(metrics?.[field]) || Number(metrics?.[field]) < 0)
        || metrics.has_jd_rdc > 1
        || metrics.matched_count > metrics.total
        || metrics.recommendation_count > metrics.total
        || metrics.urgent_count + metrics.replenish_count + metrics.healthy_count
          + metrics.slow_count + metrics.stagnant_count + metrics.no_sales_count !== metrics.total) {
        throw new Error("库存总览投影计数指标无效");
      }
      continue;
    }

    if (INVENTORY_OVERVIEW_METRIC_FIELDS.some((field) => row[field] !== null)) {
      throw new Error("库存总览投影业务行混入指标");
    }
    if (section === "page") {
      if (numericIndex !== items.length || items.length >= pagination.pageSize) throw new Error("库存分页投影索引无效");
      items.push(parseInventoryOverviewRow(row.item_json, "库存分页"));
    } else {
      if (numericIndex !== recommendations.length || recommendations.length >= 50) throw new Error("库存推荐投影索引无效");
      recommendations.push(parseInventoryOverviewRow(row.item_json, "库存推荐"));
    }
  }

  if (!metrics) throw new Error("库存总览投影缺少唯一指标行");
  const expectedItems = Math.min(pagination.pageSize, Math.max(0, Number(metrics.total) - pagination.offset));
  const expectedRecommendations = Math.min(50, Math.max(0, Number(metrics.recommendation_count)));
  if (items.length !== expectedItems || recommendations.length !== expectedRecommendations) {
    throw new Error("库存总览投影业务行数量与指标不一致");
  }
  return { metrics, items, recommendations };
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function dayDifference(start: string, end: string) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000);
}

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function normalizeWarehouseType(value: string): InventoryOverviewItem["warehouseType"] {
  return value === "owned" || value === "jd_rdc" ? value : "other";
}

function salesConsumerUnavailable(): PublicApiError {
  return new PublicApiError(
    503,
    "service_unavailable",
    "Django 销售读取服务返回的数据不完整，请稍后重试。",
  );
}

function isIsoDateOrNull(value: unknown): value is string | null {
  if (value === null) return true;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value;
}

function validateSalesFreshness(data: SalesConsumerResponseMap["freshness"]) {
  if (!data || !isIsoDateOrNull(data.dataStartDate) || !isIsoDateOrNull(data.dataCutoffDate)) {
    throw salesConsumerUnavailable();
  }
  const batch = data.latestBatch;
  if (batch !== null && (!batch || typeof batch !== "object"
    || typeof batch.id !== "string" || !batch.id
    || typeof batch.fileName !== "string" || !batch.fileName
    || (batch.completedAt !== null && typeof batch.completedAt !== "string")
    || !Number.isSafeInteger(batch.rowCount) || batch.rowCount < 0)) {
    throw salesConsumerUnavailable();
  }
}

function normalizedWarehouseKey(value: string) {
  let normalized = value.trim().toLowerCase();
  for (const token of ["配送中心", "仓库", "库房", "仓", " ", "（", "）", "(", ")", "-"]) {
    normalized = normalized.split(token).join("");
  }
  return normalized;
}

async function readInventoryDemand(
  reader: SalesConsumerReader,
  principal: AppPrincipal,
  input: {
    productCodes: string[];
    startDate: string | null;
    endDateExclusive: string | null;
    expectedRevision: string;
    signal?: AbortSignal;
  },
) {
  const rows = new Map<string, SalesConsumerResponseMap["inventory_demand"]["rows"][number]>();
  for (let offset = 0; offset < input.productCodes.length; offset += INVENTORY_DEMAND_PRODUCT_CHUNK_SIZE * 4) {
    const chunks: string[][] = [];
    for (let chunkOffset = offset;
      chunkOffset < Math.min(input.productCodes.length, offset + INVENTORY_DEMAND_PRODUCT_CHUNK_SIZE * 4);
      chunkOffset += INVENTORY_DEMAND_PRODUCT_CHUNK_SIZE) {
      chunks.push(input.productCodes.slice(chunkOffset, chunkOffset + INVENTORY_DEMAND_PRODUCT_CHUNK_SIZE));
    }
    const results = await Promise.all(chunks.map((productCodes) => reader.read(principal, {
      operation: "inventory_demand",
      startDate: input.startDate,
      endDate: input.endDateExclusive,
      productCodes,
      limit: INVENTORY_DEMAND_LIMIT,
    }, { signal: input.signal })));
    for (let index = 0; index < results.length; index += 1) {
      const result = results[index];
      const requested = new Set(chunks[index]);
      if (!result || result.revision !== input.expectedRevision || result.data.truncated !== false
        || !Array.isArray(result.data.rows)
        || !isIsoDateOrNull(result.data.dataStartDate)
        || !isIsoDateOrNull(result.data.dataCutoffDate)) {
        throw salesConsumerUnavailable();
      }
      for (const row of result.data.rows) {
        if (!row || typeof row.productCode !== "string" || !requested.has(row.productCode)
          || typeof row.productName !== "string" || typeof row.warehouseKey !== "string"
          || row.warehouseKey !== normalizedWarehouseKey(row.warehouseKey)
          || !Number.isFinite(row.salesQuantity)
          || !Number.isFinite(row.absoluteQuantity) || row.absoluteQuantity < 0
          || !Number.isFinite(row.absoluteCostCents) || row.absoluteCostCents < 0) {
          throw salesConsumerUnavailable();
        }
        const key = `${row.productCode}\u001f${row.warehouseKey}`;
        if (rows.has(key)) throw salesConsumerUnavailable();
        rows.set(key, row);
      }
    }
  }
  return rows;
}

function statusFor(input: {
  available: number;
  dailySales: number | null;
  coverageDays: number | null;
  inventoryAgeDays: number | null;
}, settings: Readonly<InventoryThresholdSettings>) {
  if (input.dailySales === null) {
    return { status: "no_sales" as const, label: "未匹配销量", reason: `所选 ${settings.salesWindowDays} 日周期未匹配到同货品、同仓库的销售明细，暂不生成补货量` };
  }
  if (input.dailySales <= 0) {
    if (input.available > 0 && (input.inventoryAgeDays ?? 0) >= settings.stagnantDays) {
      return { status: "stagnant" as const, label: "呆滞风险", reason: `库龄已达到 ${input.inventoryAgeDays} 天且所选 ${settings.salesWindowDays} 日周期无有效销量` };
    }
    return { status: "no_sales" as const, label: "无销量数据", reason: input.available > 0 ? `所选 ${settings.salesWindowDays} 日周期无有效销量，暂不生成补货量` : `暂无库存且所选 ${settings.salesWindowDays} 日周期无有效销量` };
  }
  if (input.available <= 0 || (input.coverageDays ?? Infinity) <= settings.criticalDays) {
    return { status: "urgent" as const, label: input.available <= 0 ? "库存告急" : "紧急补货", reason: `预计可售不超过 ${settings.criticalDays} 天` };
  }
  if ((input.coverageDays ?? Infinity) < settings.replenishDays) {
    return { status: "replenish" as const, label: "建议补货", reason: `预计可售低于 ${settings.replenishDays} 天` };
  }
  if ((input.coverageDays ?? 0) >= settings.stagnantDays) {
    return { status: "stagnant" as const, label: "呆滞风险", reason: `预计可售达到 ${settings.stagnantDays} 天以上` };
  }
  if ((input.coverageDays ?? 0) >= settings.slowDays) {
    return { status: "slow" as const, label: "低周转", reason: `预计可售达到 ${settings.slowDays} 天以上` };
  }
  return { status: "healthy" as const, label: "库存健康", reason: "库存覆盖处于目标区间" };
}

function normalizedWarehouseSql(column: string) {
  return `REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(LOWER(TRIM(${column})), '配送中心', ''), '仓库', ''), '库房', ''), '仓', ''), ' ', ''), '（', ''), '）', ''), '(', ''), ')', ''), '-', '')`;
}

function uniqueStrings(values: readonly string[] | undefined, max = 20) {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, max);
}

function buildInventoryCte(input: {
  batchId: string;
  demandRows: Map<string, SalesConsumerResponseMap["inventory_demand"]["rows"][number]>;
  salesWindowDays: number;
  settings: InventoryThresholdSettings;
}) {
  const sql = `WITH stock AS (
    SELECT
      product_code,
      MAX(NULLIF(product_name, '')) AS product_name,
      MAX(NULLIF(specification, '')) AS specification,
      COALESCE(MAX(NULLIF(brand, '')), '') AS brand,
      COALESCE(MAX(NULLIF(category, '')), '未分类') AS category,
      warehouse,
      ${resolvedGroupedWarehouseTypeSql("warehouse", "warehouse_type")} AS warehouse_type,
      SUM(on_hand_quantity) AS on_hand_quantity,
      SUM(available_quantity) AS available_quantity,
      SUM(locked_quantity) AS locked_quantity,
      SUM(in_transit_quantity) AS in_transit_quantity,
      SUM(CASE WHEN unit_cost_cents >= 0 THEN MAX(available_quantity, 0) * unit_cost_cents ELSE 0 END) AS imported_stock_value_cents,
      SUM(CASE WHEN unit_cost_cents >= 0 THEN MAX(available_quantity, 0) ELSE 0 END) AS priced_available_quantity,
      MAX(inventory_age_days) AS inventory_age_days,
      ${normalizedWarehouseSql("warehouse")} AS warehouse_key
    FROM inventory_stock_lines
    WHERE batch_id = ? AND TRIM(warehouse) <> '刷刷仓'
    GROUP BY product_code, warehouse
  ), sales AS (
    SELECT
      CAST(json_extract(value, '$.productCode') AS TEXT) AS product_code,
      CAST(json_extract(value, '$.warehouseKey') AS TEXT) AS warehouse_key,
      CAST(json_extract(value, '$.productName') AS TEXT) AS product_name,
      CAST(json_extract(value, '$.salesQuantity') AS REAL) AS sales_quantity,
      CAST(json_extract(value, '$.absoluteQuantity') AS REAL) AS absolute_quantity,
      CAST(json_extract(value, '$.absoluteCostCents') AS REAL) AS absolute_cost_cents
    FROM json_each(?)
  ), active_plans AS (
    SELECT
      product_code,
      warehouse,
      COALESCE(SUM(planned_quantity), 0) AS planned_in_transit_quantity,
      MAX(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) AS in_draft_plan
    FROM replenishment_plan_items
    WHERE status IN ('draft', 'confirmed') OR (status = 'completed' AND source_batch_id = ?)
    GROUP BY product_code, warehouse
  ), base AS (
    SELECT
      st.*,
      COALESCE(NULLIF(st.product_name, ''), sa.product_name, st.product_code) AS resolved_product_name,
      sa.sales_quantity,
      COALESCE(sa.absolute_quantity, 0) AS absolute_quantity,
      COALESCE(sa.absolute_cost_cents, 0) AS absolute_cost_cents,
      CASE WHEN COALESCE(sa.absolute_quantity, 0) > 0 THEN sa.absolute_cost_cents * 1.0 / sa.absolute_quantity ELSE 0 END AS fallback_unit_cost_cents,
      COALESCE(ap.planned_in_transit_quantity, 0) AS planned_in_transit_quantity,
      COALESCE(ap.in_draft_plan, 0) AS in_draft_plan
    FROM stock st
    LEFT JOIN sales sa ON sa.product_code = st.product_code AND sa.warehouse_key = st.warehouse_key
    LEFT JOIN active_plans ap ON ap.product_code = st.product_code AND ap.warehouse = st.warehouse
  ), valued AS (
    SELECT
      *,
      imported_stock_value_cents
        + CASE WHEN fallback_unit_cost_cents > 0
          THEN MAX(0, MAX(available_quantity, 0) - MAX(priced_available_quantity, 0)) * fallback_unit_cost_cents
          ELSE 0 END AS known_stock_value_cents,
      MIN(MAX(available_quantity, 0), MAX(priced_available_quantity, 0)
        + CASE WHEN fallback_unit_cost_cents > 0 THEN MAX(0, MAX(available_quantity, 0) - MAX(priced_available_quantity, 0)) ELSE 0 END
      ) AS cost_covered_quantity,
      CASE WHEN sales_quantity IS NOT NULL AND sales_quantity > 0
        THEN MAX(available_quantity, 0) * ? * 1.0 / sales_quantity ELSE NULL END AS coverage_days
    FROM base
  ), classified AS (
    SELECT
      *,
      CASE
        WHEN sales_quantity IS NULL THEN 'no_sales'
        WHEN sales_quantity <= 0 AND available_quantity > 0 AND COALESCE(inventory_age_days, 0) >= ? THEN 'stagnant'
        WHEN sales_quantity <= 0 THEN 'no_sales'
        WHEN available_quantity <= 0 OR coverage_days <= ? THEN 'urgent'
        WHEN coverage_days < ? THEN 'replenish'
        WHEN coverage_days >= ? THEN 'stagnant'
        WHEN coverage_days >= ? THEN 'slow'
        ELSE 'healthy'
      END AS status,
      CASE WHEN sales_quantity IS NULL OR sales_quantity <= 0 THEN NULL ELSE
        MAX(0, CAST((sales_quantity * ? * 1.0 / ? - available_quantity - in_transit_quantity - planned_in_transit_quantity) + 0.999999 AS INTEGER))
      END AS suggested_quantity
    FROM valued
  )`;
  return {
    sql,
    values: [
      input.batchId,
      JSON.stringify([...input.demandRows.values()]),
      input.batchId,
      input.salesWindowDays,
      input.settings.stagnantDays,
      input.settings.criticalDays,
      input.settings.replenishDays,
      input.settings.stagnantDays,
      input.settings.slowDays,
      input.settings.targetDays,
      input.salesWindowDays,
    ],
  };
}

function buildFilter(options: InventoryOverviewOptions) {
  const clauses: string[] = [];
  const values: Array<string | number> = [];
  const query = options.query?.trim().slice(0, 100);
  if (query) {
    const keywords = uniqueStrings(query.split(/[\s,，;；]+/), 8).map((value) => value.toLowerCase());
    if (keywords.length > 0) {
      clauses.push(`(${keywords.map(() => "(INSTR(LOWER(product_code), ?) > 0 OR INSTR(LOWER(resolved_product_name), ?) > 0 OR INSTR(LOWER(brand), ?) > 0 OR INSTR(LOWER(specification), ?) > 0 OR INSTR(LOWER(category), ?) > 0 OR INSTR(LOWER(warehouse), ?) > 0)").join(" OR ")})`);
      for (const keyword of keywords) values.push(keyword, keyword, keyword, keyword, keyword, keyword);
    }
  }
  const warehouses = uniqueStrings(options.warehouses, 10);
  if (warehouses.length > 0) {
    clauses.push("warehouse IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(warehouses));
  }
  const warehouseTypes = uniqueStrings(options.warehouseTypes)
    .filter((value) => ["owned", "jd_rdc", "other"].includes(value));
  if (warehouseTypes.length > 0) {
    clauses.push("warehouse_type IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(warehouseTypes));
  }
  const brands = uniqueStrings(options.brands, 20);
  if (brands.length > 0) {
    clauses.push("brand IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(brands));
  }
  const categories = uniqueStrings(options.categories, 20);
  if (categories.length > 0) {
    clauses.push("category IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(categories));
  }
  const statuses = uniqueStrings(options.statuses)
    .filter((value) => ["urgent", "replenish", "healthy", "slow", "stagnant", "no_sales"].includes(value));
  if (statuses.length > 0) {
    clauses.push("status IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(statuses));
  }
  if (options.exactKey) {
    clauses.push("warehouse || char(31) || product_code = ?");
    values.push(options.exactKey);
  }
  return { sql: clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "", values };
}

function mapItem(
  row: OverviewRow,
  settings: InventoryThresholdSettings,
  suppressRecommendations = false,
): InventoryOverviewItem {
  const available = Number(row.available_quantity ?? 0);
  const salesQuantity = row.sales_quantity === null ? null : Math.max(0, Number(row.sales_quantity));
  const dailySales = salesQuantity === null ? null : salesQuantity / settings.salesWindowDays;
  const coverageDays = dailySales && dailySales > 0 ? Math.max(0, available) / dailySales : null;
  const inventoryAgeDays = row.inventory_age_days === null ? null : Number(row.inventory_age_days);
  const health = statusFor({ available, dailySales, coverageDays, inventoryAgeDays }, settings);
  const valuation = calculateInventoryCostValuation({
    availableQuantity: available,
    importedValueCents: Number(row.imported_stock_value_cents ?? 0),
    importedPricedQuantity: Number(row.priced_available_quantity ?? 0),
    fallbackUnitCostCents: Number(row.fallback_unit_cost_cents ?? 0),
  });
  const sourceInTransit = Number(row.in_transit_quantity ?? 0);
  const plannedInTransit = Number(row.planned_in_transit_quantity ?? 0);
  return {
    key: `${row.warehouse}\u001f${row.product_code}`,
    productCode: row.product_code,
    productName: row.resolved_product_name || row.product_name || row.product_code,
    specification: row.specification || "",
    category: row.category || "",
    warehouse: row.warehouse,
    warehouseType: normalizeWarehouseType(row.warehouse_type),
    onHandQuantity: Number(row.on_hand_quantity ?? 0),
    availableQuantity: available,
    lockedQuantity: Number(row.locked_quantity ?? 0),
    sourceInTransitQuantity: sourceInTransit,
    plannedInTransitQuantity: plannedInTransit,
    totalInTransitQuantity: sourceInTransit + plannedInTransit,
    unitCostCents: valuation.unitCostCents,
    inventoryAgeDays,
    stockValueCents: valuation.completeStockValueCents,
    knownStockValueCents: valuation.knownStockValueCents,
    costCoverageRate: valuation.coverageRate,
    sales30d: salesQuantity,
    averageDailySales: dailySales,
    coverageDays,
    suggestedQuantity: suppressRecommendations || row.suggested_quantity === null
      ? null
      : Number(row.suggested_quantity),
    status: health.status,
    statusLabel: health.label,
    reason: suppressRecommendations && row.suggested_quantity !== null
      ? "库存数据质量门禁未通过，已暂停输出精确补货量；请先修复仓库/货品映射或数据单位"
      : health.reason,
    inDraftPlan: Number(row.in_draft_plan ?? 0) > 0,
  };
}

async function loadInventoryOverviewContext(
  db: InventoryDatabase,
  principal: AppPrincipal,
  options: Pick<InventoryOverviewOptions, "startDate" | "endDate" | "signal">,
  salesReader: SalesConsumerReader,
): Promise<InventoryOverviewContext> {
  const [latestBatch, persistedSettings, freshness] = await Promise.all([
    findLatestInventoryImportBatch(db),
    readOperatingSettings(db),
    salesReader.read(principal, { operation: "freshness" }, { signal: options.signal }),
  ]);
  if (!freshness || typeof freshness.revision !== "string" || !freshness.revision || freshness.revision.length > 128) {
    throw salesConsumerUnavailable();
  }
  validateSalesFreshness(freshness.data);
  const { salesStartDate, salesEndDate, salesWindowDays } = resolveInventorySalesPeriod(options, {
    startDate: freshness.data.dataStartDate,
    endDate: freshness.data.dataCutoffDate,
  });
  const settings: InventoryThresholdSettings = {
    targetDays: persistedSettings.targetDays,
    criticalDays: persistedSettings.criticalDays,
    replenishDays: persistedSettings.targetDays,
    slowDays: persistedSettings.slowDays,
    stagnantDays: persistedSettings.stagnantDays,
    salesWindowDays,
    autoReplenishment: persistedSettings.autoReplenishment,
    inventoryAlert: persistedSettings.inventoryAlert,
  };
  let demandRows = new Map<string, SalesConsumerResponseMap["inventory_demand"]["rows"][number]>();
  if (latestBatch && salesStartDate && salesEndDate) {
    const productResult = await db.prepare(
      `SELECT product_code
       FROM inventory_stock_lines
       WHERE batch_id = ? AND TRIM(warehouse) <> '刷刷仓' AND TRIM(product_code) <> ''
       GROUP BY product_code
       ORDER BY product_code
       LIMIT ?`,
    ).bind(latestBatch.id, MAX_INVENTORY_DEMAND_PRODUCTS + 1).all<{ product_code: string }>();
    if (productResult.results.length > MAX_INVENTORY_DEMAND_PRODUCTS) throw salesConsumerUnavailable();
    const productCodes = productResult.results.map((row) => row.product_code);
    if (productCodes.length > 0) {
      demandRows = await readInventoryDemand(salesReader, principal, {
        productCodes,
        startDate: salesStartDate,
        endDateExclusive: addDays(salesEndDate, 1),
        expectedRevision: freshness.revision,
        signal: options.signal,
      });
    }
  }
  return {
    latestBatch,
    salesStartDate,
    salesEndDate,
    salesWindowDays,
    settings,
    salesRevision: freshness.revision,
    demandRows,
  };
}

async function readInventoryOverviewMetrics(
  db: InventoryDatabase,
  cte: ReturnType<typeof buildInventoryCte>,
  filter: ReturnType<typeof buildFilter>,
  salesWindowDays: number,
) {
  return db.prepare(`${cte.sql}, filtered AS (SELECT * FROM classified ${filter.sql})
    ${INVENTORY_OVERVIEW_METRICS_SELECT_SQL}`)
    .bind(...cte.values, ...filter.values, salesWindowDays)
    .first<OverviewMetricsRow>();
}

async function readInventoryOverviewProjection(
  db: InventoryDatabase,
  cte: ReturnType<typeof buildInventoryCte>,
  filter: ReturnType<typeof buildFilter>,
  pagination: ReturnType<typeof normalizeInventoryPagination>,
  salesWindowDays: number,
) {
  const result = await db.prepare(`${cte.sql},
    filtered AS MATERIALIZED (SELECT * FROM classified ${filter.sql}),
    metrics AS MATERIALIZED (
      ${INVENTORY_OVERVIEW_METRICS_SELECT_SQL}
    ),
    page_rows AS MATERIALIZED (
      SELECT filtered.*
      FROM filtered
      ORDER BY ${INVENTORY_PAGE_ORDER_SQL}
      LIMIT ? OFFSET ?
    ),
    recommendation_rows AS MATERIALIZED (
      SELECT filtered.*
      FROM filtered
      WHERE suggested_quantity > 0
      ORDER BY ${INVENTORY_RECOMMENDATION_ORDER_SQL}
      LIMIT 50 OFFSET 0
    ),
    page_projection AS MATERIALIZED (
      SELECT
        ROW_NUMBER() OVER (ORDER BY ${INVENTORY_PAGE_ORDER_SQL}) - 1 AS section_index,
        ${INVENTORY_OVERVIEW_ROW_JSON_SQL} AS item_json
      FROM page_rows
    ),
    recommendation_projection AS MATERIALIZED (
      SELECT
        ROW_NUMBER() OVER (ORDER BY ${INVENTORY_RECOMMENDATION_ORDER_SQL}) - 1 AS section_index,
        ${INVENTORY_OVERVIEW_ROW_JSON_SQL} AS item_json
      FROM recommendation_rows
    )
    SELECT
      0 AS section_order,
      'metrics' AS section,
      0 AS section_index,
      ${INVENTORY_OVERVIEW_METRICS_COLUMN_SQL},
      NULL AS item_json
    FROM metrics
    UNION ALL
    SELECT
      1 AS section_order,
      'page' AS section,
      section_index,
      ${INVENTORY_OVERVIEW_NULL_METRICS_SQL},
      item_json
    FROM page_projection
    UNION ALL
    SELECT
      2 AS section_order,
      'recommendation' AS section,
      section_index,
      ${INVENTORY_OVERVIEW_NULL_METRICS_SQL},
      item_json
    FROM recommendation_projection
    ORDER BY section_order, section_index`)
    .bind(...cte.values, ...filter.values, salesWindowDays, pagination.pageSize, pagination.offset)
    .all<InventoryOverviewProjectionRow>();
  return parseInventoryOverviewProjection(result.results, pagination);
}

async function readInventoryFilterOptions(db: InventoryDatabase, batchId: string) {
  const result = await db.prepare(
    `SELECT 'warehouse' AS kind, warehouse AS value
     FROM inventory_stock_lines
     WHERE batch_id = ? AND TRIM(warehouse) <> '刷刷仓'
     GROUP BY warehouse
     UNION ALL
     SELECT 'brand' AS kind, brand AS value
     FROM inventory_stock_lines
     WHERE batch_id = ? AND TRIM(warehouse) <> '刷刷仓' AND TRIM(brand) <> ''
     GROUP BY brand
     UNION ALL
     SELECT 'category' AS kind, COALESCE(NULLIF(TRIM(category), ''), '未分类') AS value
     FROM inventory_stock_lines
     WHERE batch_id = ? AND TRIM(warehouse) <> '刷刷仓'
     GROUP BY COALESCE(NULLIF(TRIM(category), ''), '未分类')
     ORDER BY kind, value
     LIMIT 1500`,
  ).bind(batchId, batchId, batchId).all<{ kind: "warehouse" | "brand" | "category"; value: string }>();
  return {
    warehouses: result.results.filter((row) => row.kind === "warehouse").map((row) => row.value),
    brands: result.results.filter((row) => row.kind === "brand").map((row) => row.value),
    categories: result.results.filter((row) => row.kind === "category").map((row) => row.value),
  };
}

async function readInventoryMappingGaps(
  db: InventoryDatabase,
  cte: ReturnType<typeof buildInventoryCte>,
  context: InventoryOverviewContext,
  metrics: OverviewMetricsRow,
) {
  const unmatchedCount = Math.max(0, Number(metrics.total ?? 0) - Number(metrics.matched_count ?? 0));
  if (unmatchedCount <= 0) {
    return { matchedCount: Number(metrics.matched_count ?? 0), unmatchedCount: 0, samples: [] as InventoryMappingGap[] };
  }
  const gaps = await db.prepare(`${cte.sql}
    SELECT product_code, resolved_product_name, warehouse, warehouse_type, available_quantity
    FROM classified
    WHERE sales_quantity IS NULL
    ORDER BY MAX(available_quantity, 0) DESC, product_code ASC, warehouse ASC
    LIMIT 50`)
    .bind(...cte.values).all<{
      product_code: string;
      resolved_product_name: string;
      warehouse: string;
      warehouse_type: string;
      available_quantity: number;
    }>();
  const productCodes = [...new Set(gaps.results.map((row) => row.product_code))];
  const candidateMap = new Map<string, string[]>();
  const requestedProducts = new Set(productCodes);
  for (const row of context.demandRows.values()) {
    if (!requestedProducts.has(row.productCode)) continue;
    const list = candidateMap.get(row.productCode) ?? [];
    if (list.length < 8 && !list.includes(row.warehouseKey)) list.push(row.warehouseKey);
    candidateMap.set(row.productCode, list);
  }
  return {
    matchedCount: Number(metrics.matched_count ?? 0),
    unmatchedCount,
    samples: gaps.results.map((row) => ({
      key: `${row.warehouse}\u001f${row.product_code}`,
      productCode: row.product_code,
      productName: row.resolved_product_name || row.product_code,
      inventoryWarehouse: row.warehouse,
      warehouseType: normalizeWarehouseType(row.warehouse_type),
      availableQuantity: Number(row.available_quantity ?? 0),
      candidateSalesWarehouses: candidateMap.get(row.product_code) ?? [],
    })),
  };
}

async function inventoryBatchHasJdRdc(db: InventoryDatabase, batchId: string | null) {
  if (!batchId) return false;
  const row = await db.prepare(
    `SELECT 1 AS found
     FROM inventory_stock_lines
     WHERE batch_id = ? AND ${jdInboundWarehousePredicateSql("warehouse", "warehouse_type")} AND TRIM(warehouse) <> '刷刷仓'
     LIMIT 1`,
  ).bind(batchId).first<{ found: number }>();
  return Number(row?.found ?? 0) === 1;
}

function emptyInventoryPagination(page = 1, pageSize = 50) {
  return { page, pageSize, limit: pageSize, total: 0, returned: 0, totalPages: 0, truncated: false };
}

function buildInventorySources(context: InventoryOverviewContext, hasJdRdc: boolean) {
  const { latestBatch, salesEndDate, settings } = context;
  if (!latestBatch) {
    return [
      { key: "warehouse_stock", label: "吉客云分仓库存", status: "missing" as const, asOfDate: null },
      { key: "sales_demand", label: `所选 ${settings.salesWindowDays} 日销售需求`, status: salesEndDate ? "ready" as const : "missing" as const, asOfDate: salesEndDate },
      { key: "jd_rdc", label: "京东 RDC / DC", status: "missing" as const, asOfDate: null },
    ];
  }
  const inventoryStale = dayDifference(latestBatch.snapshotDate, shanghaiToday()) > 3;
  return [
    { key: "warehouse_stock", label: "吉客云分仓库存", status: inventoryStale ? "stale" as const : "ready" as const, asOfDate: latestBatch.snapshotDate },
    { key: "sales_demand", label: `所选 ${settings.salesWindowDays} 日销售需求`, status: salesEndDate ? "ready" as const : "missing" as const, asOfDate: salesEndDate },
    { key: "jd_rdc", label: "京东 RDC / DC", status: hasJdRdc ? (inventoryStale ? "stale" as const : "ready" as const) : "missing" as const, asOfDate: hasJdRdc ? latestBatch.snapshotDate : null },
  ];
}

function buildInventoryDashboardResponse(
  context: InventoryOverviewContext,
  metricsRow?: OverviewMetricsRow | null,
): InventoryDashboardOverviewResponse {
  const { latestBatch, salesStartDate, salesEndDate } = context;
  if (!latestBatch) {
    const quality = assessInventoryOverviewQuality({
      hasInventory: false,
      salesDemandMatchRate: 0,
      skuWarehouseCount: 0,
      totalAvailableQuantity: 0,
      knownStockValueCents: 0,
      inventoryStale: false,
      autoReplenishment: context.settings.autoReplenishment,
    });
    return {
      hasInventory: false,
      sync: {
        latestInventoryBatchId: null,
        inventoryAsOf: null,
        inventorySyncedAt: null,
        salesThrough: salesEndDate,
        salesWindowStart: salesStartDate,
        latestInventoryFile: null,
        inventoryStale: false,
      },
      metrics: {
        skuWarehouseCount: 0,
        totalAvailableQuantity: 0,
        totalStockValueCents: 0,
        knownStockValueCents: 0,
        stockValueComplete: true,
        costCoverageRate: 0,
        salesDemandMatchRate: 0,
        averageCoverageDays: null,
        urgentCount: 0,
        replenishCount: 0,
        slowMovingValueCents: 0,
        noSalesCount: 0,
        recommendationCount: 0,
        inventoryAlertsEnabled: context.settings.inventoryAlert,
        recommendationsSuppressed: quality.recommendationsSuppressed,
        qualityIssues: quality.issues,
      },
      health: { urgent: 0, replenish: 0, healthy: 0, slow: 0, stagnant: 0, noSales: 0 },
      controls: {
        autoReplenishmentEnabled: context.settings.autoReplenishment,
        alertsEnabled: context.settings.inventoryAlert,
      },
      quality,
    };
  }

  const metrics = metricsRow ?? EMPTY_OVERVIEW_METRICS;
  const total = Number(metrics.total ?? 0);
  const positiveAvailable = Number(metrics.positive_available_quantity ?? 0);
  const coveredQuantity = Number(metrics.covered_quantity ?? 0);
  const totalDailySales = Number(metrics.total_daily_sales ?? 0);
  const inventoryStale = dayDifference(latestBatch.snapshotDate, shanghaiToday()) > 3;
  const hasMetrics = metricsRow !== undefined && metricsRow !== null;
  const salesDemandMatchRate = hasMetrics
    ? (total > 0 ? Number(metrics.matched_count ?? 0) / total : 0)
    : 1;
  const totalAvailableQuantity = Number(metrics.total_available_quantity ?? 0);
  const knownStockValueCents = Number(metrics.known_stock_value_cents ?? 0);
  const quality = assessInventoryOverviewQuality({
    hasInventory: true,
    salesDemandMatchRate,
    skuWarehouseCount: total,
    totalAvailableQuantity,
    knownStockValueCents,
    inventoryStale,
    autoReplenishment: context.settings.autoReplenishment,
  });
  return {
    hasInventory: true,
    sync: {
      latestInventoryBatchId: latestBatch.id,
      inventoryAsOf: latestBatch.snapshotDate,
      inventorySyncedAt: latestBatch.completedAt,
      salesThrough: salesEndDate,
      salesWindowStart: salesStartDate,
      latestInventoryFile: latestBatch.fileName,
      inventoryStale,
    },
    metrics: {
      skuWarehouseCount: total,
      totalAvailableQuantity,
      totalStockValueCents: knownStockValueCents,
      knownStockValueCents,
      stockValueComplete: positiveAvailable <= 0 || coveredQuantity >= positiveAvailable,
      costCoverageRate: positiveAvailable > 0 ? coveredQuantity / positiveAvailable : 1,
      salesDemandMatchRate,
      averageCoverageDays: quality.recommendationsSuppressed || totalDailySales <= 0
        ? null
        : Number(metrics.demand_available_quantity ?? 0) / totalDailySales,
      urgentCount: Number(metrics.urgent_count ?? 0),
      replenishCount: Number(metrics.replenish_count ?? 0),
      slowMovingValueCents: Number(metrics.slow_moving_value_cents ?? 0),
      noSalesCount: Number(metrics.no_sales_count ?? 0),
      recommendationCount: quality.recommendationsSuppressed ? 0 : Number(metrics.recommendation_count ?? 0),
      inventoryAlertsEnabled: context.settings.inventoryAlert,
      recommendationsSuppressed: quality.recommendationsSuppressed,
      qualityIssues: quality.issues,
    },
    health: {
      urgent: Number(metrics.urgent_count ?? 0),
      replenish: Number(metrics.replenish_count ?? 0),
      healthy: Number(metrics.healthy_count ?? 0),
      slow: Number(metrics.slow_count ?? 0),
      stagnant: Number(metrics.stagnant_count ?? 0),
      noSales: Number(metrics.no_sales_count ?? 0),
    },
    controls: {
      autoReplenishmentEnabled: context.settings.autoReplenishment,
      alertsEnabled: context.settings.inventoryAlert,
    },
    quality,
  };
}

function toInventoryDashboardProjection(
  response: InventoryDashboardOverviewResponse,
): InventoryDashboardProjectionResponse {
  return {
    hasInventory: response.hasInventory,
    sync: response.sync,
    metrics: response.metrics,
    health: response.health,
  };
}

export async function getInventoryDashboardOverview(
  db: InventoryDatabase,
  principal: AppPrincipal,
  options: Pick<InventoryOverviewOptions, "startDate" | "endDate" | "signal"> = {},
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
): Promise<InventoryDashboardProjectionResponse> {
  const context = await loadInventoryOverviewContext(db, principal, options, salesReader);
  if (!context.latestBatch) {
    return toInventoryDashboardProjection(buildInventoryDashboardResponse(context));
  }
  const cte = buildInventoryCte({
    batchId: context.latestBatch.id,
    demandRows: context.demandRows,
    salesWindowDays: context.salesWindowDays,
    settings: context.settings,
  });
  const metricsRow = await readInventoryOverviewMetrics(db, cte, buildFilter({}), context.salesWindowDays);
  return toInventoryDashboardProjection(buildInventoryDashboardResponse(context, metricsRow));
}

export async function getInventoryOverview(
  db: InventoryDatabase,
  principal: AppPrincipal,
  options: InventoryOverviewOptions = {},
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
) {
  const pagination = normalizeInventoryPagination(options);
  const context = await loadInventoryOverviewContext(db, principal, options, salesReader);
  const { latestBatch, salesStartDate, salesEndDate, salesWindowDays, settings } = context;

  if (!latestBatch) {
    const dashboard = buildInventoryDashboardResponse(context);
    return {
      projection: "overview" as const,
      ...dashboard,
      settings,
      sources: buildInventorySources(context, false),
      filters: { warehouses: [], brands: [], categories: [], statuses: INVENTORY_HEALTH_STATUSES },
      mapping: { matchedCount: 0, unmatchedCount: 0, samples: [] as InventoryMappingGap[] },
      pagination: emptyInventoryPagination(pagination.page, pagination.pageSize),
      recommendations: [] as InventoryOverviewItem[],
      items: [] as InventoryOverviewItem[],
      plans: [],
      plansPagination: { page: 1, pageSize: 50, total: 0, returned: 0, totalPages: 0, truncated: false },
      planSummary: EMPTY_PLAN_SUMMARY,
    };
  }

  const cte = buildInventoryCte({ batchId: latestBatch.id, demandRows: context.demandRows, salesWindowDays, settings });
  const filter = buildFilter(options);
  const [projection, filterOptions, qualityMetrics] = await Promise.all([
    readInventoryOverviewProjection(db, cte, filter, pagination, salesWindowDays),
    readInventoryFilterOptions(db, latestBatch.id),
    filter.sql
      ? readInventoryOverviewMetrics(db, cte, buildFilter({}), salesWindowDays)
      : Promise.resolve(null),
  ]);

  const metrics = projection.metrics;
  const total = Number(metrics.total ?? 0);
  const filteredDashboard = buildInventoryDashboardResponse(context, metrics);
  const quality = qualityMetrics
    ? buildInventoryDashboardResponse(context, qualityMetrics).quality
    : filteredDashboard.quality;
  const suppressRecommendations = quality.recommendationsSuppressed;
  const dashboard = suppressRecommendations
    ? {
      ...filteredDashboard,
      quality,
      metrics: {
        ...filteredDashboard.metrics,
        averageCoverageDays: null,
        recommendationCount: 0,
        recommendationsSuppressed: true,
        qualityIssues: quality.issues,
      },
    }
    : {
      ...filteredDashboard,
      quality,
      metrics: {
        ...filteredDashboard.metrics,
        recommendationsSuppressed: false,
        qualityIssues: quality.issues,
      },
    };
  const hasJdRdc = Number((qualityMetrics ?? metrics).has_jd_rdc ?? 0) > 0;
  const mapping = await readInventoryMappingGaps(db, cte, context, qualityMetrics ?? metrics);
  return {
    projection: "overview" as const,
    ...dashboard,
    settings,
    sources: buildInventorySources(context, hasJdRdc),
    filters: { ...filterOptions, statuses: INVENTORY_HEALTH_STATUSES },
    mapping,
    pagination: { page: pagination.page, pageSize: pagination.pageSize, limit: pagination.pageSize, total, returned: projection.items.length, totalPages: total === 0 ? 0 : Math.ceil(total / pagination.pageSize), truncated: pagination.offset + projection.items.length < total },
    recommendations: suppressRecommendations
      ? []
      : projection.recommendations.map((row) => mapItem(row, settings)),
    items: projection.items.map((row) => mapItem(row, settings, suppressRecommendations)),
    plans: [],
    plansPagination: { page: 1, pageSize: 50, total: 0, returned: 0, totalPages: 0, truncated: false },
    planSummary: EMPTY_PLAN_SUMMARY,
  };
}

export async function getInventoryFullOverview(
  db: InventoryDatabase,
  principal: AppPrincipal,
  options: InventoryOverviewOptions = {},
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
) {
  const [overview, planPage] = await Promise.all([
    getInventoryOverview(db, principal, options, salesReader),
    queryReplenishmentPlans(db, {
      page: options.planPage,
      pageSize: options.planPageSize ?? 50,
      status: options.planStatus,
      includeCancelled: options.includeCancelledPlans,
      query: options.query,
      warehouses: options.warehouses,
      brands: options.brands,
      categories: options.categories,
    }),
  ]);
  const planSummary = await getReplenishmentPlanSummary(db, overview.sync.latestInventoryBatchId, {
    query: options.query,
    warehouses: options.warehouses,
    brands: options.brands,
    categories: options.categories,
  });
  const { projection, ...legacyOverview } = overview;
  if (projection !== "overview") throw new Error("库存完整响应收到了错误的投影类型");
  return {
    ...legacyOverview,
    plans: planPage.items,
    plansPagination: planPage.pagination,
    planSummary,
  };
}

export async function getInventoryPlanOverview(
  db: InventoryDatabase,
  principal: AppPrincipal,
  options: InventoryOverviewOptions = {},
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
) {
  const [context, planPage] = await Promise.all([
    loadInventoryOverviewContext(db, principal, options, salesReader),
    queryReplenishmentPlans(db, {
      page: options.planPage,
      pageSize: options.planPageSize ?? 50,
      status: options.planStatus,
      includeCancelled: options.includeCancelledPlans,
      query: options.query,
      warehouses: options.warehouses,
      brands: options.brands,
      categories: options.categories,
    }),
  ]);
  const [planSummary, hasJdRdc, filterOptions] = await Promise.all([
    getReplenishmentPlanSummary(db, context.latestBatch?.id ?? null, {
      query: options.query,
      warehouses: options.warehouses,
      brands: options.brands,
      categories: options.categories,
    }),
    inventoryBatchHasJdRdc(db, context.latestBatch?.id ?? null),
    context.latestBatch
      ? readInventoryFilterOptions(db, context.latestBatch.id)
      : Promise.resolve({ warehouses: [], brands: [], categories: [] }),
  ]);
  const dashboard = buildInventoryDashboardResponse(context);
  return {
    projection: "plan" as const,
    ...dashboard,
    settings: context.settings,
    sources: buildInventorySources(context, hasJdRdc),
    filters: { ...filterOptions, statuses: INVENTORY_HEALTH_STATUSES },
    mapping: { matchedCount: 0, unmatchedCount: 0, samples: [] as InventoryMappingGap[] },
    pagination: emptyInventoryPagination(),
    recommendations: [] as InventoryOverviewItem[],
    items: [] as InventoryOverviewItem[],
    plans: planPage.items,
    plansPagination: planPage.pagination,
    planSummary,
  };
}
