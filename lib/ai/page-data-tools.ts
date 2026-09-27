import { createDjangoFinanceService, FINANCE_ANALYSIS_PATH, FINANCE_TARGETS_PATH, FINANCE_IMPORTS_PATH } from "@/lib/django/finance-service";
import {
  AuthorizationError,
  appRoles,
  requireUnrestrictedDataScope,
  type AppPrincipal,
} from "@/lib/auth/authorization";
import {
  netshopOutletsForPrincipal,
  netshopPlatformsForPrincipal,
} from "@/lib/netshop/access";
import {
  netshopOutletKey,
  normalizeNetshopOutletFilters,
  type NetshopOutletFilter,
} from "@/lib/netshop/query-contract";
import {
  createDjangoNetshopService,
  NETSHOP_IMPORTS_PATH,
  NETSHOP_PRODUCTS_PATH,
  NETSHOP_PRODUCT_PERFORMANCE_PATH,
} from "@/lib/django/netshop-service";
import {
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import {
  createDjangoInventoryService,
  INVENTORY_AGE_ANALYSIS_PATH,
  INVENTORY_IMPORTS_PATH,
  INVENTORY_INBOUND_MONITOR_PATH,
  INVENTORY_GUANGDONG_PATH,
  INVENTORY_SETTINGS_PATH,
} from "@/lib/django/inventory-service";
import {
  createDjangoWorkflowService,
  WORKFLOW_LAUNCH_PROJECTS_PATH,
  WORKFLOW_OPERATION_RECORDS_PATH,
  WORKFLOW_TASKS_PATH,
  WORKFLOW_TEMPLATES_PATH,
  WORKFLOW_IMPORT_CHAIN_STATUS_PATH,
} from "@/lib/django/workflow-service";
import importChainCatalog from "@/lib/imports/chain-catalog.generated.json";
import { validateTodayStatus, type ChainTodayResponse } from "@/lib/imports/chain-status";
import { RegistryToolError } from "@/lib/ai/tool-registry-contract";

/**
 * Bounded, read-only adapters for page data that is not yet represented by the
 * central AI tool registry. The registry remains the sole declaration point;
 * this module only supplies handlers that can be wired there explicitly.
 */

export type PageDataToolContext = {
  principal: AppPrincipal;
  signal?: AbortSignal;
};

export class PageDataToolInputError extends RegistryToolError {
  constructor(message: string) {
    super("invalid_tool_arguments", message);
    this.name = "PageDataToolInputError";
  }
}

export async function getInventoryGuangdongPageData(args: unknown, context: PageDataToolContext) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "广东入仓库存监控");
  const input = inputObject(args);
  assertOnlyKeys(input, ["q", "brands", "categories", "suppliers", "risk", "page", "limit", "warehouses"]);
  const warehouses = stringList(input.warehouses, "warehouses", 10);
  if (warehouses.some(warehouse => warehouse !== "广东仓")) failInput("广东入仓监控固定为广东仓，不能查询其他仓库");
  const pageInput = pagination(input);
  const query = new URLSearchParams({ page: String(pageInput.page), pageSize: String(pageInput.pageSize) });
  const text = optionalText(input.q, "q", 100);
  if (text) query.set("q", text);
  for (const [key, param] of [["brands", "brand"], ["categories", "category"], ["suppliers", "supplier"]]) {
    for (const value of stringList(input[key], key, 20)) query.append(param, value);
  }
  const risk = optionalText(input.risk, "risk", 20);
  if (risk) {
    if (!["no_stock", "urgent", "warning", "stale", "unknown", "healthy"].includes(risk)) failInput("广东风险状态无效");
    query.set("risk", risk);
  }
  const result = resultObject((await createDjangoInventoryService().requestJson<Record<string, unknown>>(principal, {
    method: "GET", path: INVENTORY_GUANGDONG_PATH, service: "reader", rawQuery: query.toString(),
  }, { signal: context.signal })).data);
  return {
    page: "inventory.guangdong", currency: "CNY", monetaryUnit: "cents", version: result.version,
    sync: pickScalars(result.sync, ["inventoryAsOf", "salesThrough", "inventoryStale"]),
    metrics: pickScalars(result.metrics, ["itemCount", "availableQuantity", "inTransitQuantity", "knownStockValueCents", "missingCostCount", "missingStockCount"]),
    distribution: boundedRecords(result.distribution, 6, ["risk", "label", "itemCount", "quantity", "knownStockValueCents", "itemRate", "quantityRate", "valueRate"]),
    pagination: projectPagination(result.pagination),
    items: boundedRecords(result.items, pageInput.pageSize, ["productCode", "productName", "specification", "supplier", "operatorName", "buyer", "warehouse", "availableQuantity", "inTransitQuantity", "outbound7dQuantity", "outbound15dQuantity", "outbound30dQuantity", "turnoverDays", "inventoryAgeDays", "leadDays", "bufferDays", "latestOrderDate", "replenishmentQuantity", "latestReplenishmentOrderDate", "replenishmentStockIncreaseQuantity", "replenishmentRemainingQuantity", "replenishmentRemainingReason", "risk", "riskLabel", "riskReason"]),
    disclosures: boundedStrings(result.disclosures, 8),
  };
}

type FinanceAnalysisInput = {
  requestedMonths: string[];
  allMonths: boolean;
  fallbackToLatestCompletedMonth: boolean;
  platformNames: string[];
  shopKeys: string[];
};

type InventoryAgeInput = {
  query?: string;
  warehouses: string[];
  brands: string[];
  categories: string[];
  statuses: Array<"healthy" | "aged" | "slow" | "stagnant" | "no_stock">;
  ageBuckets: Array<
    | "0-7"
    | "8-15"
    | "16-30"
    | "31-60"
    | "61-90"
    | "91-120"
    | "121-150"
    | "151-180"
    | "181-360"
    | "361+"
  >;
  page: number;
  pageSize: number;
};

type InventoryInboundInput = {
  query?: string;
  warehouses: string[];
  suppliers: string[];
  page: number;
  pageSize: number;
};

type NetshopCatalogInput = {
  query?: string;
  page: number;
  pageSize: number;
  outlets: NetshopOutletFilter[];
  platformNames: string[];
  salesChannels: readonly string[] | null;
  salesStartDate?: string;
  salesEndDate?: string;
};

type NetshopPerformanceInput = {
  dimension: "sku" | "spu";
  query?: string;
  page: number;
  pageSize: number;
  platformNames: string[];
  outlets: NetshopOutletFilter[];
  startDate?: string;
  endDate?: string;
};

type WorkflowTaskListInput = {
  query?: string;
  statuses: string[];
  priorities: string[];
  categories: string[];
  owners: string[];
  shopNames: string[];
  sources: string[];
  dueFrom?: string;
  dueTo?: string;
  page: number;
  pageSize: number;
};

type NewProductProjectListInput = {
  query?: string;
  statuses: string[];
  suppliers: string[];
  owners: string[];
  categories: string[];
  platforms: string[];
  shopNames: string[];
  priorities: string[];
  sources: string[];
  lifecycleStatuses: string[];
  stage?: string;
  stageStatuses: string[];
  proposedFrom?: string;
  proposedTo?: string;
  dueFrom?: string;
  dueTo?: string;
  page: number;
  pageSize: number;
};

type OperationRecordListInput = {
  types: string[];
  statuses: string[];
  shopNames: string[];
  platforms: string[];
  owners: string[];
  query?: string;
  from?: string;
  to?: string;
  page: number;
  pageSize: number;
};

type MarketSelection = {
  skuCode: string;
  category: string;
  scope: string;
  rankingDimension: "SKU" | "SPU";
};

export type PageImportSource =
  | "sales"
  | "inventory"
  | "products"
  | "inventory_age"
  | "combos"
  | "finance"
  | "netshop"
  | "customer_service";

export type PageDataToolServices = {
  readAutomationStatus(principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readFinanceAnalysis(input: FinanceAnalysisInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readFinanceTargets(input: { page: number; pageSize: number }, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readInventoryAge(input: InventoryAgeInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readInventoryInbound(input: InventoryInboundInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readNetshopCatalog(input: NetshopCatalogInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readNetshopPerformance(input: NetshopPerformanceInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readWorkflowTasks(input: WorkflowTaskListInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readOperationRecords(input: OperationRecordListInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readNewProductProjects(input: NewProductProjectListInput, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readWorkflowTemplates(includeInactive: boolean, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readImportBatches(
    source: PageImportSource,
    input: { page: number; pageSize: number; platforms: string[] },
    principal: AppPrincipal,
    signal?: AbortSignal,
  ): Promise<unknown>;
  readMarketComparison(input: {
    selections: MarketSelection[];
    startDate?: string;
    endDate?: string;
  }, principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readMarketStatus(principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
  readOperatingSettings(principal: AppPrincipal, signal?: AbortSignal): Promise<unknown>;
};

const defaultPageDataToolServices: PageDataToolServices = {
  async readAutomationStatus(principal, signal) {
    return (await createDjangoWorkflowService().requestJson(principal,
      { method: "GET", path: WORKFLOW_IMPORT_CHAIN_STATUS_PATH, service: "reader" }, { signal })).data;
  },
  async readFinanceAnalysis(input, principal, signal) {
    const query = new URLSearchParams();
    if (input.allMonths) query.append("month", "*");
    for (const month of input.requestedMonths) query.append("month", month);
    if (input.fallbackToLatestCompletedMonth) query.set("initialMonthFallback", "latest_completed");
    for (const platform of input.platformNames) query.append("platform", platform);
    for (const shop of input.shopKeys) query.append("shop", shop);
    return (await createDjangoFinanceService().request<Record<string, unknown>>(principal,
      { method: "GET", path: FINANCE_ANALYSIS_PATH, query, service: "reader" }, { signal })).data;
  },
  async readFinanceTargets(input, principal, signal) {
    const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize), view: "items" });
    return (await createDjangoFinanceService().request<Record<string, unknown>>(principal,
      { method: "GET", path: FINANCE_TARGETS_PATH, query, service: "reader" }, { signal })).data;
  },
  async readInventoryAge(input, principal, signal) {
    const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize) });
    if (input.query) query.set("q", input.query);
    for (const value of input.warehouses) query.append("warehouse", value);
    for (const value of input.brands) query.append("brand", value);
    for (const value of input.categories) query.append("category", value);
    for (const value of input.statuses) query.append("status", value);
    for (const value of input.ageBuckets) query.append("ageBucket", value);
    return (await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_AGE_ANALYSIS_PATH, service: "reader", rawQuery: query.toString() },
      { signal },
    )).data;
  },
  async readInventoryInbound(input, principal, signal) {
    const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize) });
    if (input.query) query.set("q", input.query);
    for (const value of input.warehouses) query.append("warehouse", value);
    for (const value of input.suppliers) query.append("supplier", value);
    return (await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_INBOUND_MONITOR_PATH, service: "reader", rawQuery: query.toString() },
      { signal },
    )).data;
  },
  async readNetshopCatalog(input, principal, signal) {
    const query = new URLSearchParams({
      view: "full",
      page: String(input.page),
      pageSize: String(input.pageSize),
    });
    if (input.query) query.set("q", input.query);
    for (const platform of input.platformNames) query.append("platform", platform);
    for (const outlet of input.outlets) query.append("outlet", netshopOutletKey(outlet.platform, outlet.shopName));
    if (input.salesStartDate) query.set("startDate", input.salesStartDate);
    if (input.salesEndDate) query.set("endDate", input.salesEndDate);
    return (await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_PRODUCTS_PATH, query, service: "reader" },
      { signal },
    )).data;
  },
  async readNetshopPerformance(input, principal, signal) {
    const query = new URLSearchParams({
      dimension: input.dimension,
      view: "full",
      page: String(input.page),
      pageSize: String(input.pageSize),
    });
    if (input.query) query.set("q", input.query);
    for (const platform of input.platformNames) query.append("platform", platform);
    for (const outlet of input.outlets) query.append("outlet", netshopOutletKey(outlet.platform, outlet.shopName));
    if (input.startDate) query.set("startDate", input.startDate);
    if (input.endDate) query.set("endDate", input.endDate);
    return (await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_PRODUCT_PERFORMANCE_PATH, query, service: "reader" },
      { signal },
    )).data;
  },
  async readWorkflowTasks(input, principal, signal) {
    const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize) });
    if (input.query) query.set("q", input.query);
    for (const value of input.statuses) query.append("status", value);
    for (const value of input.priorities) query.append("priority", value);
    for (const value of input.categories) query.append("category", value);
    for (const value of input.owners) query.append("owner", value);
    for (const value of input.shopNames) query.append("shopName", value);
    for (const value of input.sources) query.append("source", value);
    if (input.dueFrom) query.set("dueFrom", input.dueFrom);
    if (input.dueTo) query.set("dueTo", input.dueTo);
    return (await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_TASKS_PATH, service: "reader", rawQuery: query.toString() },
      { signal },
    )).data;
  },
  async readOperationRecords(input, principal, signal) {
    const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize) });
    for (const value of input.types) query.append("type", value);
    for (const value of input.statuses) query.append("status", value);
    for (const value of input.shopNames) query.append("shopName", value);
    for (const value of input.platforms) query.append("platform", value);
    for (const value of input.owners) query.append("owner", value);
    if (input.query) query.set("query", input.query);
    if (input.from) query.set("from", input.from);
    if (input.to) query.set("to", input.to);
    return (await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_OPERATION_RECORDS_PATH, service: "reader", rawQuery: query.toString() },
      { signal },
    )).data;
  },
  async readNewProductProjects(input, principal, signal) {
    const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize) });
    if (input.query) query.set("q", input.query);
    for (const value of input.statuses) query.append("status", value);
    for (const value of input.suppliers) query.append("supplier", value);
    for (const value of input.owners) query.append("owner", value);
    for (const value of input.categories) query.append("category", value);
    for (const value of input.platforms) query.append("platform", value);
    for (const value of input.shopNames) query.append("shopName", value);
    for (const value of input.priorities) query.append("priority", value);
    for (const value of input.sources) query.append("source", value);
    for (const value of input.lifecycleStatuses) query.append("lifecycleStatus", value);
    if (input.stage) query.set("stage", input.stage);
    for (const value of input.stageStatuses) query.append("stageStatus", value);
    if (input.proposedFrom) query.set("proposedFrom", input.proposedFrom);
    if (input.proposedTo) query.set("proposedTo", input.proposedTo);
    if (input.dueFrom) query.set("dueFrom", input.dueFrom);
    if (input.dueTo) query.set("dueTo", input.dueTo);
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_LAUNCH_PROJECTS_PATH, service: "reader", rawQuery: query.toString() },
      { signal },
    );
    return { ...result.data, workflowRevision: result.revision };
  },
  async readWorkflowTemplates(includeInactive, principal, signal) {
    return (await createDjangoWorkflowService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: WORKFLOW_TEMPLATES_PATH, service: "reader", rawQuery: includeInactive ? "includeInactive=true" : "" },
      { signal },
    )).data.items;
  },
  async readImportBatches(source, input, principal, signal) {
    if (source === "sales") {
      const { createDjangoSalesConsumerReader } = await import("@/lib/django/sales-consumer-reader");
      const result = await createDjangoSalesConsumerReader().read(principal, {
        operation: "import_batch_search",
        query: "",
        page: input.page,
        pageSize: input.pageSize,
      }, { signal });
      if (!result || typeof result.revision !== "string" || !result.revision
        || !Array.isArray(result.data.items)
        || !Number.isSafeInteger(result.data.total) || result.data.total < 0
        || result.data.items.length > input.pageSize
        || result.data.truncated !== input.page * input.pageSize < result.data.total) {
        throw new RegistryToolError("tool_execution_failed", "Django 销售导入批次读取结果无效");
      }
      return {
        items: result.data.items,
        pagination: {
          page: input.page,
          pageSize: input.pageSize,
          total: result.data.total,
          returned: result.data.items.length,
          totalPages: result.data.total === 0 ? 0 : Math.ceil(result.data.total / input.pageSize),
          truncated: result.data.truncated,
        },
        salesRevision: result.revision,
      };
    }
    if (source === "inventory" || source === "inventory_age") {
      const query = new URLSearchParams({
        dataset: source === "inventory" ? "stock" : "age",
        page: String(input.page),
        pageSize: String(input.pageSize),
      });
      return (await createDjangoInventoryService().requestJson<Record<string, unknown>>(
        principal,
        { method: "GET", path: INVENTORY_IMPORTS_PATH, service: "reader", rawQuery: query.toString() },
        { signal },
      )).data;
    }
    if (source === "products" || source === "combos") {
      const { createDjangoErpReferenceService, ERP_REFERENCE_IMPORTS_PATH } = await import("@/lib/django/erp-reference-service");
      const query = new URLSearchParams({
        source,
        page: String(input.page),
        pageSize: String(input.pageSize),
      });
      const result = await createDjangoErpReferenceService().requestJson<Record<string, unknown>>(
        principal,
        { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader", rawQuery: query.toString() },
        { signal },
      );
      return { ...result.data, erpReferenceRevision: result.revision };
    }
    if (source === "finance") {
      const query = new URLSearchParams({ page: String(input.page), pageSize: String(input.pageSize) });
      return (await createDjangoFinanceService().request<Record<string, unknown>>(principal,
        { method: "GET", path: FINANCE_IMPORTS_PATH, query, service: "reader" }, { signal })).data;
    }
    if (source === "netshop") {
      const query = new URLSearchParams({
        page: String(input.page),
        pageSize: String(input.pageSize),
      });
      for (const platform of input.platforms) query.append("platform", platform);
      return (await createDjangoNetshopService().request<Record<string, unknown>>(
        principal,
        { method: "GET", path: NETSHOP_IMPORTS_PATH, query, service: "reader" },
        { signal },
      )).data;
    }
    const { listCustomerServiceBatches } = await import("@/lib/customer-service/database");
    return listCustomerServiceBatches(input, principal, { signal });
  },
  async readMarketComparison(input, principal, signal) {
    return (await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: {
          operation: "master",
          view: "compare",
          params: input,
        },
      },
      { signal },
    )).data;
  },
  async readMarketStatus(principal, signal) {
    return (await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: {
          operation: "master",
          view: "settings_status",
          params: {},
        },
      },
      { signal },
    )).data;
  },
  async readOperatingSettings(principal, signal) {
    return (await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_SETTINGS_PATH, service: "reader" },
      { signal },
    )).data;
  },
};

const MAX_PAGE = 10_000;
const MAX_RESULT_ITEMS = 20;
const MAX_TEXT_RESULT_CHARS = 500;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const FINANCE_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;
const financeMetricKeys = [
  "grossSalesCents",
  "returnAmountCents",
  "netSalesCents",
  "netCostCents",
  "grossProfitCents",
  "grossMarginBps",
  "returnRateBps",
  "sellingExpenseCents",
  "smallProfitCents",
  "smallMarginBps",
  "otherExpenseCents",
  "profitCents",
  "profitMarginBps",
  "promotionExpenseCents",
  "promotionFeeRatioBps",
] as const;
const financeTargetKeys = [
  "id",
  "periodType",
  "periodKey",
  "platform",
  "shopName",
  "category",
  "manager",
  "salesTargetCents",
  "profitTargetCents",
  "smallMarginBps",
  "inventoryCleanupTargetCents",
  "promotionFeeRatioBps",
  "stagnantInventoryTargetCents",
  "targetCount",
  "version",
  "createdAt",
  "updatedAt",
] as const;
const importBatchKeys = [
  "id",
  "source",
  "sourceKey",
  "sourceLabel",
  "sourceType",
  "dataset",
  "platform",
  "shopName",
  "fileName",
  "sessionFileName",
  "chatFileName",
  "sheetName",
  "snapshotDate",
  "dateMin",
  "dateMax",
  "periodStart",
  "periodEnd",
  "businessDateStart",
  "businessDateEnd",
  "status",
  "rowCount",
  "insertedCount",
  "updatedCount",
  "duplicateCount",
  "excludedCount",
  "warningCount",
  "parsedMonthCount",
  "importedMonthCount",
  "skippedMonthCount",
  "subjectCount",
  "conversationCount",
  "matchedCount",
  "sessionOnlyCount",
  "chatOnlyCount",
  "ambiguousCount",
  "createdAt",
  "completedAt",
] as const;

function serviceSet(overrides?: Partial<PageDataToolServices>): PageDataToolServices {
  return { ...defaultPageDataToolServices, ...overrides };
}

function failInput(message: string): never {
  throw new PageDataToolInputError(message);
}

function inputObject(value: unknown): Record<string, unknown> {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) failInput("工具参数必须是对象");
  return value as Record<string, unknown>;
}

function resultObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function assertOnlyKeys(input: Record<string, unknown>, allowed: readonly string[]) {
  const allowedSet = new Set(allowed);
  const unknown = Object.keys(input).filter((key) => !allowedSet.has(key));
  if (unknown.length > 0) failInput(`包含不支持的字段：${unknown.slice(0, 5).join("、")}`);
}

function requirePrincipal(context: PageDataToolContext): AppPrincipal {
  const principal = context?.principal;
  if (
    !principal
    || typeof principal.email !== "string"
    || !principal.email.trim()
    || !appRoles.includes(principal.role)
    || (principal.scope !== null && (
      !Array.isArray(principal.scope.warehouses)
      || !Array.isArray(principal.scope.channels)
      || !Array.isArray(principal.scope.platforms)
    ))
  ) {
    throw new AuthorizationError(401, "authentication_required", "缺少真实的应用身份");
  }
  return principal;
}

function optionalText(value: unknown, label: string, maximum: number): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") failInput(`${label}必须是文本`);
  const text = value.trim();
  if (!text) return undefined;
  if (Array.from(text).length > maximum || CONTROL_CHARACTERS.test(text)) {
    failInput(`${label}格式无效或超过 ${maximum} 个字符`);
  }
  return text;
}

function requiredText(value: unknown, label: string, maximum: number): string {
  const text = optionalText(value, label, maximum);
  if (!text) failInput(`${label}不能为空`);
  return text;
}

function stringList(
  value: unknown,
  label: string,
  maximumItems: number,
  maximumLength = 120,
  allowed?: readonly string[],
): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) failInput(`${label}必须是数组`);
  if (value.length > maximumItems) failInput(`${label}最多允许 ${maximumItems} 项`);
  const normalized = value.map((item) => requiredText(item, label, maximumLength));
  const unique = [...new Set(normalized)];
  if (allowed && unique.some((item) => !allowed.includes(item))) failInput(`${label}包含无效值`);
  return unique;
}

function booleanValue(value: unknown, label: string, fallback: boolean): boolean {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "boolean") failInput(`${label}必须是布尔值`);
  return value;
}

function integerValue(value: unknown, label: string, fallback: number, maximum: number): number {
  if (value === undefined || value === null) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    failInput(`${label}必须为 1 到 ${maximum} 的整数`);
  }
  return Number(value);
}

function isoDate(value: unknown, label: string): string | undefined {
  const text = optionalText(value, label, 10);
  if (!text) return undefined;
  if (!ISO_DATE.test(text)) failInput(`${label}必须为 YYYY-MM-DD`);
  const date = new Date(`${text}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== text) failInput(`${label}不是有效日期`);
  return text;
}

function datePair(
  input: Record<string, unknown>,
  startKey: string,
  endKey: string,
  maximumDays = 730,
) {
  const startDate = isoDate(input[startKey], startKey);
  const endDate = isoDate(input[endKey], endKey);
  if (Boolean(startDate) !== Boolean(endDate)) failInput(`${startKey} 和 ${endKey} 必须同时提供`);
  if (!startDate || !endDate) return { startDate: undefined, endDate: undefined };
  if (startDate > endDate) failInput(`${startKey}不能晚于${endKey}`);
  const days = Math.round((Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / 86_400_000) + 1;
  if (days > maximumDays) failInput(`日期范围最多支持 ${maximumDays} 天`);
  return { startDate, endDate };
}

function optionalIsoDateTime(value: unknown, label: string): string | undefined {
  const text = optionalText(value, label, 40);
  if (!text) return undefined;
  if (ISO_DATE.test(text)) {
    isoDate(text, label);
    return text;
  }
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(text) || !Number.isFinite(Date.parse(text))) {
    failInput(`${label}必须为 YYYY-MM-DD 或包含时区的 ISO 日期时间`);
  }
  return text;
}

function pagination(input: Record<string, unknown>) {
  return {
    page: integerValue(input.page, "page", 1, MAX_PAGE),
    pageSize: integerValue(input.limit, "limit", MAX_RESULT_ITEMS, MAX_RESULT_ITEMS),
  };
}

function safeScalar(value: unknown): string | number | boolean | null | undefined {
  if (value === null) return null;
  if (typeof value === "string") return Array.from(value).slice(0, MAX_TEXT_RESULT_CHARS).join("");
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "boolean") return value;
  return undefined;
}

function pickScalars(value: unknown, keys: readonly string[]) {
  const source = resultObject(value);
  const projected: Record<string, string | number | boolean | null> = {};
  keys.forEach((key) => {
    const scalar = safeScalar(source[key]);
    if (scalar !== undefined) projected[key] = scalar;
  });
  return projected;
}

function boundedStrings(value: unknown, maximum: number) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, maximum).flatMap((item) => {
    const scalar = safeScalar(item);
    return typeof scalar === "string" ? [scalar] : [];
  });
}

function boundedRecords(value: unknown, maximum: number, keys: readonly string[]) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, maximum).map((item) => pickScalars(item, keys));
}

function projectNewProductProject(value: unknown) {
  const source = resultObject(value);
  return {
    ...pickScalars(source, [
      "id", "productName", "supplierName", "brand", "category", "erpProductCode", "skuCode", "spuCode",
      "productImageUrl", "proposedBy", "proposedDate", "owner", "targetLaunchDate", "lifecycleStatus",
      "status", "priority", "recommendedPriceCents", "approvedPriceCents", "estimatedGrossMarginBps",
      "source", "sourceRef", "notes", "version", "progressPercent", "currentStageKey", "overdue",
      "overdueStageCount", "createdAt", "updatedAt",
    ]),
    targets: boundedRecords(source.targets, 20, [
      "id", "platform", "shopName", "channel", "listingSku", "listingUrl", "status",
    ]),
    stages: boundedRecords(source.stages, 7, [
      "id", "stageKey", "label", "status", "owner", "plannedDueDate", "completedAt", "blocker", "notes",
      "evidenceUrl", "evidenceLabel", "version", "updatedAt",
    ]),
  };
}

function projectPagination(value: unknown) {
  return pickScalars(value, ["page", "pageSize", "limit", "total", "returned", "totalPages", "truncated"]);
}

function projectFinanceMetrics(value: unknown) {
  return pickScalars(value, financeMetricKeys);
}

function projectFinanceTarget(value: unknown) {
  return pickScalars(value, financeTargetKeys);
}

function projectFinanceAnalysis(value: unknown) {
  const result = resultObject(value);
  const targets = resultObject(result.targets);
  const progress = resultObject(result.progress);
  const selection = resultObject(result.selection);
  const sync = resultObject(result.sync);
  return {
    page: "bi.finance",
    available: true,
    hasData: result.hasData === true,
    currency: "CNY",
    monetaryUnit: "cents",
    selectedMonth: safeScalar(result.selectedMonth) ?? null,
    selectedMonths: boundedStrings(result.selectedMonths, 24),
    periodLabel: safeScalar(result.periodLabel) ?? null,
    current: projectFinanceMetrics(result.current),
    previous: result.previous === null ? null : projectFinanceMetrics(result.previous),
    yearAgo: result.yearAgo === null ? null : projectFinanceMetrics(result.yearAgo),
    yearToDate: projectFinanceMetrics(result.yearToDate),
    timeline: Array.isArray(result.timeline)
      ? result.timeline.slice(-24).map((item) => ({ ...pickScalars(item, ["month"]), ...projectFinanceMetrics(item) }))
      : [],
    targets: {
      month: projectFinanceTarget(targets.month),
      year: projectFinanceTarget(targets.year),
      projects: Array.isArray(targets.projects) ? targets.projects.slice(0, MAX_RESULT_ITEMS).map(projectFinanceTarget) : [],
      projectPagination: projectPagination(targets.projectPagination),
      periodPagination: projectPagination(targets.periodPagination),
    },
    progress: {
      month: pickScalars(progress.month, ["sales", "profit", "smallMarginGapBps", "promotionFeeGapBps"]),
      year: pickScalars(progress.year, ["sales", "profit", "smallMarginGapBps", "promotionFeeGapBps"]),
    },
    expenses: boundedRecords(result.expenses, MAX_RESULT_ITEMS, [
      "name", "current", "previous", "yearAgo", "feeRateBps", "yearAgoFeeRateBps", "momRate", "yoyRate", "abnormal",
    ]),
    shops: Array.isArray(result.shops) ? result.shops.slice(0, MAX_RESULT_ITEMS).map((item) => {
      const shop = resultObject(item);
      return {
        ...pickScalars(shop, ["name", "key", "groupName", "manager"]),
        actual: projectFinanceMetrics(shop.actual),
        target: projectFinanceTarget(shop.target),
        progress: pickScalars(shop.progress, ["sales", "profit", "smallMarginGapBps", "promotionFeeGapBps"]),
      };
    }) : [],
    anomalies: boundedRecords(result.anomalies, 10, ["level", "title", "detail"]),
    selection: {
      ...pickScalars(selection, ["allMonths", "truncated", "availableMonthCount", "fallbackApplied"]),
      months: boundedStrings(selection.months, 24),
      requestedMonths: boundedStrings(selection.requestedMonths, 24),
      platforms: boundedStrings(selection.platforms, 20),
      shops: boundedStrings(selection.shops, 20),
    },
    sync: pickScalars(sync, ["dataCutoffMonth", "sourceFileName", "importedAt"]),
    responseLimits: { timeline: 24, expenses: MAX_RESULT_ITEMS, shops: MAX_RESULT_ITEMS, projects: MAX_RESULT_ITEMS },
  };
}

export async function getFinanceAnalysisPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "财报分析");
  const input = inputObject(args);
  assertOnlyKeys(input, ["months", "allMonths", "fallbackToLatestCompletedMonth", "platforms", "shopKeys"]);
  const requestedMonths = stringList(input.months, "months", 24, 7);
  if (requestedMonths.some((month) => !FINANCE_MONTH.test(month))) failInput("months 必须使用 YYYY-MM");
  const allMonths = booleanValue(input.allMonths, "allMonths", false);
  const fallbackToLatestCompletedMonth = booleanValue(
    input.fallbackToLatestCompletedMonth,
    "fallbackToLatestCompletedMonth",
    false,
  );
  if (allMonths && requestedMonths.length > 0) failInput("allMonths 不能与 months 同时使用");
  if (fallbackToLatestCompletedMonth && requestedMonths.length === 0) failInput("回退到最近已完成月份时必须提供 months");
  const result = await serviceSet(overrides).readFinanceAnalysis({
    requestedMonths,
    allMonths,
    fallbackToLatestCompletedMonth,
    platformNames: stringList(input.platforms, "platforms", 20),
    shopKeys: stringList(input.shopKeys, "shopKeys", 20, 240),
  }, principal, context.signal);
  return projectFinanceAnalysis(result);
}

export async function listFinanceTargetsPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "经营目标");
  const input = inputObject(args);
  assertOnlyKeys(input, ["page", "limit"]);
  const pageInput = pagination(input);
  const result = resultObject(await serviceSet(overrides).readFinanceTargets(pageInput, principal, context.signal));
  return {
    page: "bi.finance-targets",
    available: true,
    currency: "CNY",
    monetaryUnit: "cents",
    items: Array.isArray(result.items) ? result.items.slice(0, pageInput.pageSize).map(projectFinanceTarget) : [],
    pagination: projectPagination(result.pagination),
  };
}

export async function getInventoryAgePageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "库龄分析数据");
  const input = inputObject(args);
  assertOnlyKeys(input, ["q", "warehouses", "brands", "categories", "statuses", "ageBuckets", "page", "limit"]);
  const pageInput = pagination(input);
  const filters = {
    query: optionalText(input.q, "q", 100),
    warehouses: stringList(input.warehouses, "warehouses", 10),
    brands: stringList(input.brands, "brands", 20),
    categories: stringList(input.categories, "categories", 20),
    statuses: stringList(input.statuses, "statuses", 5, 20, ["healthy", "aged", "slow", "stagnant", "no_stock"]) as InventoryAgeInput["statuses"],
    ageBuckets: stringList(input.ageBuckets, "ageBuckets", 10, 20, ["0-7", "8-15", "16-30", "31-60", "61-90", "91-120", "121-150", "151-180", "181-360", "361+"]) as InventoryAgeInput["ageBuckets"],
  };
  const result = resultObject(await serviceSet(overrides).readInventoryAge(
    { ...filters, ...pageInput },
    principal,
    context.signal,
  ));
  const filterOptions = resultObject(result.filters);
  return {
    page: "inventory.age",
    available: true,
    hasInventory: result.hasInventory === true,
    currency: "CNY",
    monetaryUnit: "cents",
    filtersApplied: filters,
    sync: pickScalars(result.sync, ["inventoryAsOf", "latestInventoryBatchId", "sourceKey", "hasAgeSales"]),
    metrics: pickScalars(result.metrics, ["skuWarehouseCount", "stockValueComplete", "aged90Count", "aged90ValueCents", "stagnantCount", "stagnantValueCents", "zeroSalesCount", "cleanupCount"]),
    coverage: pickScalars(result.coverage, ["unagedStockCount", "unagedQuantity"]),
    distribution: boundedRecords(result.distribution, 10, ["key", "label", "count", "valueCents"]),
    fineDistribution: boundedRecords(result.fineDistribution, 10, ["key", "label", "count", "quantity", "valueCents", "quantityShare", "valueShare"]),
    filterOptions: {
      warehouses: boundedStrings(filterOptions.warehouses, 50),
      brands: boundedStrings(filterOptions.brands, 50),
      categories: boundedStrings(filterOptions.categories, 50),
      statuses: boundedStrings(filterOptions.statuses, 5),
      ageBuckets: Array.isArray(filterOptions.ageBuckets)
        ? filterOptions.ageBuckets.slice(0, 10).map((item) => pickScalars(item, ["value", "label"]))
        : [],
    },
    pagination: projectPagination(result.pagination),
    items: boundedRecords(result.items, pageInput.pageSize, [
      "key", "productCode", "productName", "brand", "specification", "category", "warehouse", "warehouseType",
      "availableQuantity", "stockValueCents", "inventoryAgeDays", "ageBucketKey", "ageBucketLabel", "sales7dQuantity", "sales30dQuantity",
      "status", "statusLabel", "recommendation",
    ]),
  };
}

export async function getInventoryInboundPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "京东入仓库存监控");
  const input = inputObject(args);
  assertOnlyKeys(input, ["q", "warehouses", "suppliers", "page", "limit"]);
  const filters = {
    query: optionalText(input.q, "q", 100),
    warehouses: stringList(input.warehouses, "warehouses", 10),
    suppliers: stringList(input.suppliers, "suppliers", 20),
  };
  const pageInput = pagination(input);
  const result = resultObject(await serviceSet(overrides).readInventoryInbound({
    ...filters,
    ...pageInput,
  }, principal, context.signal));
  const filterOptions = resultObject(result.filters);
  return {
    page: "inventory.inbound",
    available: true,
    hasInventory: result.hasInventory === true,
    currency: "CNY",
    monetaryUnit: "cents",
    filtersApplied: filters,
    sync: pickScalars(result.sync, ["inventoryAsOf", "salesThrough", "latestInventoryBatchId"]),
    scope: pickScalars(result.scope, ["warehouseType", "valuationBasis", "supplyPriceAvailable", "nativeComparisonAvailable"]),
    metrics: pickScalars(result.metrics, [
      "itemCount", "warehouseCount", "availableQuantity", "inTransitQuantity", "knownStockValueCents",
      "costCoverageRate", "salesMatchRate", "outbound30dQuantity", "turnoverDays", "staleItemCount",
      "staleValueCents", "missingSupplierCount",
    ]),
    filterOptions: {
      warehouses: boundedStrings(filterOptions.warehouses, 50),
      suppliers: boundedStrings(filterOptions.suppliers, 50),
    },
    pagination: projectPagination(result.pagination),
    regions: boundedRecords(result.regions, MAX_RESULT_ITEMS, [
      "warehouse", "itemCount", "availableQuantity", "inTransitQuantity", "knownStockValueCents",
      "outbound30dQuantity", "turnoverDays", "salesMatchRate",
    ]),
    items: boundedRecords(result.items, pageInput.pageSize, [
      "key", "productCode", "productName", "category", "supplier", "warehouse", "availableQuantity",
      "inTransitQuantity", "inventoryAgeDays", "knownStockValueCents", "costCoverageRate", "unitCostCents",
      "outbound7dQuantity", "outbound30dQuantity", "outbound90dQuantity", "turnoverDays", "risk",
    ]),
    disclosures: boundedStrings(result.disclosures, 8),
  };
}

function parseOutlets(value: unknown): NetshopOutletFilter[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) failInput("outlets 必须是数组");
  if (value.length > 20) failInput("outlets 最多允许 20 项");
  const raw = value.map((item) => {
    const outlet = inputObject(item);
    assertOnlyKeys(outlet, ["platform", "shopName"]);
    return {
      platform: requiredText(outlet.platform, "outlet.platform", 100),
      shopName: requiredText(outlet.shopName, "outlet.shopName", 100),
    };
  });
  return normalizeNetshopOutletFilters(raw);
}

function scopedNetshopPlatforms(principal: AppPrincipal, requestedPlatforms: string[]) {
  const resolved = netshopPlatformsForPrincipal(principal, requestedPlatforms)
    .filter((platform) => platform === "京东" || platform === "天猫");
  if (principal.scope !== null && resolved.length === 0) {
    throw new AuthorizationError(403, "access_denied", "当前账号没有可读取的网店平台范围");
  }
  return resolved;
}

function projectCatalogItem(value: unknown) {
  return pickScalars(value, [
    "platform", "shopName", "spuId", "skuId", "productCode", "productName", "saleAttribute", "category",
    "brand", "price", "priceCents", "totalInventory", "availableInventory", "status", "snapshotDate",
    "costPriceCents", "netSalesCents", "grossMarginRate", "refundRate", "salesMatched",
  ]);
}

export async function getNetshopProductCatalogPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  const input = inputObject(args);
  assertOnlyKeys(input, ["q", "platforms", "outlets", "startDate", "endDate", "page", "limit"]);
  const requestedPlatforms = stringList(input.platforms, "platforms", 20, 100, ["京东", "天猫"]);
  const platformNames = scopedNetshopPlatforms(principal, requestedPlatforms);
  const outlets = netshopOutletsForPrincipal(principal, parseOutlets(input.outlets), requestedPlatforms);
  const period = datePair(input, "startDate", "endDate");
  const filters = {
    query: optionalText(input.q, "q", 120),
    platformNames,
    outlets,
    salesStartDate: period.startDate,
    salesEndDate: period.endDate,
    salesChannels: principal.scope === null ? null : principal.scope.channels,
    ...pagination(input),
  };
  const result = resultObject(await serviceSet(overrides).readNetshopCatalog(filters, principal, context.signal));
  const batch = result.batch === null ? null : pickScalars(result.batch, importBatchKeys);
  return {
    page: "netshop.products",
    available: true,
    currency: "CNY",
    monetaryUnit: "cents",
    filtersApplied: {
      query: filters.query,
      platforms: platformNames,
      outlets,
      salesChannels: filters.salesChannels,
      startDate: period.startDate,
      endDate: period.endDate,
    },
    snapshotToken: safeScalar(result.snapshotToken) ?? null,
    batch,
    summary: pickScalars(result.summary, ["totalSkus", "onSaleSkus", "totalInventory", "availableInventory"]),
    shops: boundedRecords(result.shops, MAX_RESULT_ITEMS, ["shopName", "platform", "snapshotDate", "completedAt"]),
    sales: pickScalars(result.sales, ["periodStart", "periodEnd", "dataCutoffDate", "platform"]),
    items: Array.isArray(result.items) ? result.items.slice(0, filters.pageSize).map(projectCatalogItem) : [],
    pagination: projectPagination(result.pagination),
  };
}

function projectPerformanceItem(value: unknown) {
  const item = resultObject(value);
  return {
    ...pickScalars(item, [
      "id", "platform", "skuId", "spuId", "productCode", "productName", "category", "dateMin", "dateMax",
      "dataDays", "pageViews", "visitors", "searchImpressions", "searchClicks", "searchClickRate",
      "addCartCustomers", "addCartQuantity", "orderCustomers", "orderQuantity", "orderAmountCents",
      "transactionOrders", "transactionAmountCents", "transactionQuantity", "transactionCustomers", "favorites",
      "refundAmountCents", "searchVisitors", "searchTransactionCustomers", "uvValue", "conversionRate",
    ]),
    shopNames: boundedStrings(item.shopNames, 20),
  };
}

export async function getNetshopProductPerformancePageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  const input = inputObject(args);
  assertOnlyKeys(input, ["dimension", "q", "platforms", "outlets", "startDate", "endDate", "page", "limit"]);
  const dimension = input.dimension === undefined ? "sku" : input.dimension;
  if (dimension !== "sku" && dimension !== "spu") failInput("dimension 必须为 sku 或 spu");
  const requestedPlatforms = stringList(input.platforms, "platforms", 20, 100, ["京东", "天猫"]);
  if (dimension === "sku" && requestedPlatforms.some((platform) => platform !== "京东")) {
    failInput("SKU 日表现仅支持京东平台；天猫请使用 SPU 维度");
  }
  const effectiveRequestedPlatforms = dimension === "sku" && requestedPlatforms.length === 0
    ? ["京东"]
    : requestedPlatforms;
  const platformNames = scopedNetshopPlatforms(principal, effectiveRequestedPlatforms);
  const parsedOutlets = parseOutlets(input.outlets);
  if (dimension === "sku" && parsedOutlets.some((outlet) => outlet.platform !== "京东")) {
    failInput("SKU 日表现的 outlet 必须属于京东平台");
  }
  const outlets = netshopOutletsForPrincipal(principal, parsedOutlets, effectiveRequestedPlatforms);
  const period = datePair(input, "startDate", "endDate");
  const filters: NetshopPerformanceInput = {
    dimension,
    query: optionalText(input.q, "q", 120),
    platformNames,
    outlets,
    startDate: period.startDate,
    endDate: period.endDate,
    ...pagination(input),
  };
  const result = resultObject(await serviceSet(overrides).readNetshopPerformance(
    filters,
    principal,
    context.signal,
  ));
  const coverage = resultObject(result.coverage);
  return {
    page: "netshop.performance",
    available: true,
    currency: "CNY",
    monetaryUnit: "cents",
    filtersApplied: {
      dimension,
      query: filters.query,
      platforms: platformNames,
      outlets,
      startDate: period.startDate,
      endDate: period.endDate,
    },
    snapshotToken: safeScalar(result.snapshotToken) ?? null,
    dimension: safeScalar(result.dimension) ?? dimension,
    dataset: safeScalar(result.dataset) ?? null,
    requestedPeriod: pickScalars(result.requestedPeriod, ["startDate", "endDate"]),
    dateMin: safeScalar(result.dateMin) ?? null,
    dataCutoffDate: safeScalar(result.dataCutoffDate) ?? null,
    visitorAggregation: safeScalar(result.visitorAggregation) ?? null,
    coverage: {
      ...pickScalars(coverage, ["availableDateMin", "availableDateMax", "total", "returned", "truncated"]),
      actualDates: boundedStrings(coverage.actualDates, 90),
      missingDates: boundedStrings(coverage.missingDates, 90),
    },
    summary: pickScalars(result.summary, [
      "productCount", "pageViews", "visitors", "searchImpressions", "searchClicks", "searchClickRate",
      "addCartCustomers", "addCartQuantity", "orderCustomers", "orderQuantity", "orderAmountCents",
      "transactionOrders", "transactionAmountCents", "transactionQuantity", "transactionCustomers", "favorites",
      "refundAmountCents", "searchVisitors", "searchTransactionCustomers", "uvValue", "conversionRate",
    ]),
    shops: boundedRecords(result.shops, MAX_RESULT_ITEMS, ["shopName", "platform", "productCount"]),
    daily: boundedRecords(result.daily, 90, [
      "date", "pageViews", "visitors", "transactionCustomers", "transactionQuantity", "transactionAmountCents",
      "refundAmountCents", "favorites", "addCartCustomers", "addCartQuantity",
    ]),
    dailyPagination: projectPagination(result.dailyPagination),
    items: Array.isArray(result.items) ? result.items.slice(0, filters.pageSize).map(projectPerformanceItem) : [],
    pagination: projectPagination(result.pagination),
  };
}

export async function listWorkflowTasksPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "工作事项");
  const input = inputObject(args);
  assertOnlyKeys(input, ["q", "statuses", "priorities", "categories", "owners", "shopNames", "sources", "dueFrom", "dueTo", "page", "limit"]);
  const dueFrom = isoDate(input.dueFrom, "dueFrom");
  const dueTo = isoDate(input.dueTo, "dueTo");
  if (dueFrom && dueTo && dueFrom >= dueTo) failInput("截止日期范围必须满足 dueFrom 早于 dueTo");
  const filters: WorkflowTaskListInput = {
    query: optionalText(input.q, "q", 80),
    statuses: stringList(input.statuses, "statuses", 3, 20, ["待开始", "工作中", "已完成"]),
    priorities: stringList(input.priorities, "priorities", 3, 20, ["high", "normal", "low"]),
    categories: stringList(input.categories, "categories", 20, 120),
    owners: stringList(input.owners, "owners", 20, 120),
    shopNames: stringList(input.shopNames, "shopNames", 20, 160),
    sources: stringList(input.sources, "sources", 2, 40, ["系统预置", "手动录入"]),
    dueFrom,
    dueTo,
    ...pagination(input),
  };
  const result = resultObject(await serviceSet(overrides).readWorkflowTasks(filters, principal, context.signal));
  return {
    page: "workflow.tasks",
    available: true,
    filtersApplied: {
      query: filters.query,
      statuses: filters.statuses,
      priorities: filters.priorities,
      categories: filters.categories,
      owners: filters.owners,
      shopNames: filters.shopNames,
      sources: filters.sources,
      dueFrom,
      dueTo,
    },
    summary: pickScalars(result.summary, ["total", "pending", "inProgress", "completed", "open"]),
    pagination: projectPagination(result.pagination),
    items: boundedRecords(result.items, filters.pageSize, [
      "id", "title", "workContent", "category", "owner", "shopName", "startDate", "due", "status",
      "priority", "source", "version", "createdAt", "updatedAt",
    ]),
  };
}

export async function listNewProductProjectsPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "新品项目");
  const input = inputObject(args);
  assertOnlyKeys(input, [
    "q", "statuses", "suppliers", "owners", "categories", "platforms", "shopNames", "priorities", "sources",
    "lifecycleStatuses", "stage", "stageStatuses", "proposedFrom", "proposedTo", "dueFrom", "dueTo", "page", "limit",
  ]);
  const proposedFrom = isoDate(input.proposedFrom, "proposedFrom");
  const proposedTo = isoDate(input.proposedTo, "proposedTo");
  const dueFrom = isoDate(input.dueFrom, "dueFrom");
  const dueTo = isoDate(input.dueTo, "dueTo");
  if (proposedFrom && proposedTo && proposedFrom >= proposedTo) failInput("提出日期范围必须满足 proposedFrom 早于 proposedTo");
  if (dueFrom && dueTo && dueFrom >= dueTo) failInput("上架日期范围必须满足 dueFrom 早于 dueTo");
  const filters: NewProductProjectListInput = {
    query: optionalText(input.q, "q", 80),
    statuses: stringList(input.statuses, "statuses", 6, 40, ["not_started", "in_progress", "blocked", "completed", "paused", "cancelled"]),
    suppliers: stringList(input.suppliers, "suppliers", 20, 200),
    owners: stringList(input.owners, "owners", 20, 120),
    categories: stringList(input.categories, "categories", 20, 120),
    platforms: stringList(input.platforms, "platforms", 20, 80),
    shopNames: stringList(input.shopNames, "shopNames", 20, 160),
    priorities: stringList(input.priorities, "priorities", 3, 40, ["high", "normal", "low"]),
    sources: stringList(input.sources, "sources", 4, 40, ["manual", "system", "import", "integration"]),
    lifecycleStatuses: stringList(input.lifecycleStatuses, "lifecycleStatuses", 3, 40, ["active", "paused", "cancelled"]),
    stage: optionalText(input.stage, "stage", 40),
    stageStatuses: stringList(input.stageStatuses, "stageStatuses", 5, 40, ["not_started", "in_progress", "blocked", "completed", "not_applicable"]),
    proposedFrom,
    proposedTo,
    dueFrom,
    dueTo,
    ...pagination(input),
  };
  if (filters.stage && !["modeling", "pricing", "image", "video", "listing", "stocking", "review"].includes(filters.stage)) {
    failInput("stage 包含无效值");
  }
  const result = resultObject(await serviceSet(overrides).readNewProductProjects(filters, principal, context.signal));
  const summary = resultObject(result.summary);
  const facets = resultObject(result.facets);
  return {
    page: "workflow.launch_projects",
    available: true,
    backendMode: "django",
    workflowRevision: safeScalar(result.workflowRevision) ?? null,
    monetaryUnit: "cents",
    marginUnit: "basis_points",
    filtersApplied: {
      query: filters.query,
      statuses: filters.statuses,
      suppliers: filters.suppliers,
      owners: filters.owners,
      categories: filters.categories,
      platforms: filters.platforms,
      shopNames: filters.shopNames,
      priorities: filters.priorities,
      sources: filters.sources,
      lifecycleStatuses: filters.lifecycleStatuses,
      stage: filters.stage,
      stageStatuses: filters.stageStatuses,
      proposedFrom,
      proposedTo,
      dueFrom,
      dueTo,
    },
    summary: {
      ...pickScalars(summary, ["total", "notStarted", "inProgress", "blocked", "completed", "paused", "cancelled", "overdue"]),
      stageSummary: boundedRecords(summary.stageSummary, 7, [
        "stageKey", "label", "not_started", "in_progress", "blocked", "completed", "not_applicable",
      ]),
    },
    pagination: projectPagination(result.pagination),
    facets: {
      suppliers: boundedStrings(facets.suppliers, 50),
      owners: boundedStrings(facets.owners, 50),
      categories: boundedStrings(facets.categories, 50),
      platforms: boundedStrings(facets.platforms, 50),
      shopNames: boundedStrings(facets.shopNames, 50),
      sources: boundedStrings(facets.sources, 10),
    },
    items: Array.isArray(result.items) ? result.items.slice(0, filters.pageSize).map(projectNewProductProject) : [],
  };
}

export async function listOperationsRecordsPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  const input = inputObject(args);
  assertOnlyKeys(input, ["types", "statuses", "shopNames", "platforms", "owners", "q", "from", "to", "page", "limit"]);
  const from = optionalIsoDateTime(input.from, "from");
  const to = optionalIsoDateTime(input.to, "to");
  if (from && to && Date.parse(from) >= Date.parse(to)) failInput("时间范围必须满足 from 早于 to");
  const filters: OperationRecordListInput = {
    types: stringList(input.types, "types", 2, 20, ["inspection", "review"]),
    statuses: stringList(input.statuses, "statuses", 20, 40),
    shopNames: stringList(input.shopNames, "shopNames", 20, 160),
    platforms: stringList(input.platforms, "platforms", 20, 120),
    owners: stringList(input.owners, "owners", 20, 120),
    query: optionalText(input.q, "q", 80),
    from,
    to,
    ...pagination(input),
  };
  const result = resultObject(await serviceSet(overrides).readOperationRecords(filters, principal, context.signal));
  return {
    page: "workflow.operations",
    available: true,
    filtersApplied: {
      types: filters.types,
      statuses: filters.statuses,
      shopNames: filters.shopNames,
      platforms: filters.platforms,
      owners: filters.owners,
      query: filters.query,
      from,
      to,
      dataScope: principal.scope === null ? "unrestricted" : "restricted",
    },
    pagination: projectPagination(result.pagination),
    items: boundedRecords(result.items, filters.pageSize, [
      "id", "type", "title", "status", "priority", "platform", "channel", "shopName", "owner",
      "occurredAt", "dueAt", "content", "source", "sourceRef", "referenceCode", "version", "createdAt", "updatedAt",
    ]),
  };
}

export async function listWorkflowTemplatesPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "工作事项模板");
  const input = inputObject(args);
  assertOnlyKeys(input, ["includeInactive", "limit"]);
  const requestedIncludeInactive = booleanValue(input.includeInactive, "includeInactive", false);
  const includeInactive = requestedIncludeInactive && (principal.role === "operator" || principal.role === "admin");
  const limit = integerValue(input.limit, "limit", MAX_RESULT_ITEMS, 50);
  const rows = await serviceSet(overrides).readWorkflowTemplates(includeInactive, principal, context.signal);
  const items = Array.isArray(rows) ? rows : [];
  return {
    page: "workflow.templates",
    available: true,
    includeInactive,
    returned: Math.min(items.length, limit),
    truncated: items.length > limit,
    items: boundedRecords(items, limit, [
      "id", "name", "description", "title", "workContent", "category", "owner", "shopName",
      "startOffsetDays", "dueOffsetDays", "priority", "active", "version", "createdAt", "updatedAt",
    ]),
  };
}

function projectImportBatch(value: unknown) {
  return pickScalars(value, importBatchKeys);
}

export async function getImportStatusPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  const input = inputObject(args);
  assertOnlyKeys(input, ["source", "platforms", "page", "limit"]);
  const source = requiredText(input.source, "source", 40) as PageImportSource;
  const allowedSources: readonly PageImportSource[] = [
    "sales", "inventory", "products", "inventory_age", "combos", "finance", "netshop", "customer_service",
  ];
  if (!allowedSources.includes(source)) failInput("source 不受支持");
  let platforms: string[] = [];
  if (source === "netshop") {
    if (principal.role !== "admin") {
      throw new AuthorizationError(403, "insufficient_role", "只有管理员可以读取网店导入历史");
    }
    platforms = scopedNetshopPlatforms(
      principal,
      stringList(input.platforms, "platforms", 20, 100, ["京东", "天猫"]),
    );
  } else {
    if (input.platforms !== undefined) failInput("platforms 仅适用于 netshop 导入历史");
    requireUnrestrictedDataScope(principal, `${source} 导入历史`);
  }
  const pageInput = pagination(input);
  const result = resultObject(await serviceSet(overrides).readImportBatches(source, {
    ...pageInput,
    platforms,
  }, principal, context.signal));
  return {
    page: "imports.status",
    available: true,
    source,
    filtersApplied: { platforms },
    items: Array.isArray(result.items) ? result.items.slice(0, pageInput.pageSize).map(projectImportBatch) : [],
    pagination: projectPagination(result.pagination),
  };
}

export async function getAutomationRunStatusPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "自动导入运行状态");
  const input = inputObject(args);
  assertOnlyKeys(input, ["workflowKey"]);
  const workflowKey = requiredText(input.workflowKey, "workflowKey", 40);
  const allowed = ["jackyun", "tmall", "jd", "jd_market", "jd_promotion", "jd_promotion_cut_meat"] as const;
  if (!allowed.includes(workflowKey as (typeof allowed)[number])) failInput("workflowKey 不受支持");
  const payload = await serviceSet(overrides).readAutomationStatus(principal, context.signal) as ChainTodayResponse;
  if (!validateTodayStatus(payload)) throw new Error("自动运行状态响应无效，无法核实今天是否完成。");
  const ids = new Set(importChainCatalog.rules.filter(rule => workflowKey === "jd_promotion_cut_meat"
    ? rule.workflowId === "JdPromotionCutMeat2026" : workflowKey === "jd_promotion"
      ? rule.workflowId === "JdPromotionDaily2026" : rule.chainKey === workflowKey).map(rule => rule.workflowId));
  return { page: "workflow.automation", available: true, workflowKey, date: payload.date, checkedAt: payload.checkedAt,
    source: payload.source, timezone: payload.timezone,
    items: boundedRecords(payload.items.filter(item => ids.has(item.workflowId)), 11,
      ["workflowId", "active", "state", "completedToday", "completedAt", "completedMode", "executionId", "executionMode", "startedAt", "finishedAt"]),
    message: "仅上海今天自动触发或完整自动重试的 n8n 记录；共用工作流表示整链状态，手动调试不计入。" };
}

function parseMarketSelections(value: unknown): MarketSelection[] {
  if (!Array.isArray(value)) failInput("selections 必须是数组");
  if (value.length < 2 || value.length > 5) failInput("商品对比必须选择 2 到 5 个精确身份");
  const selections = value.map((item) => {
    const selection = inputObject(item);
    assertOnlyKeys(selection, ["skuCode", "category", "scope", "rankingDimension"]);
    const rawRankingDimension = selection.rankingDimension;
    if (rawRankingDimension !== "SKU" && rawRankingDimension !== "SPU") failInput("rankingDimension 必须为 SKU 或 SPU");
    const rankingDimension: MarketSelection["rankingDimension"] = rawRankingDimension;
    return {
      skuCode: requiredText(selection.skuCode, "skuCode", 80),
      category: requiredText(selection.category, "category", 120),
      scope: requiredText(selection.scope, "scope", 120),
      rankingDimension,
    };
  });
  const unique = new Map(selections.map((item) => [JSON.stringify([item.category, item.scope, item.rankingDimension, item.skuCode]), item]));
  if (unique.size !== selections.length) failInput("selections 不能包含重复身份");
  return selections;
}

export async function compareMarketItemsPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "市场商品对比");
  const input = inputObject(args);
  assertOnlyKeys(input, ["selections", "startDate", "endDate"]);
  const selections = parseMarketSelections(input.selections);
  const period = datePair(input, "startDate", "endDate");
  const result = resultObject(await serviceSet(overrides).readMarketComparison(
    { selections, ...period },
    principal,
    context.signal,
  ));
  return {
    page: "market.comparison",
    available: true,
    currency: "CNY",
    monetaryUnit: "cents",
    filtersApplied: { selections, ...period },
    items: Array.isArray(result.items) ? result.items.slice(0, 5).map((value) => {
      const item = resultObject(value);
      return {
        ...pickScalars(item, [
          "skuCode", "productName", "brand", "category", "scope", "rankingDimension", "gmvCents",
          "quantity", "visitors", "conversionBps", "bestRank", "marketPriceCents", "averageTransactionPriceCents",
          "trendTotalMonths", "trendTruncated",
        ]),
        trend: Array.isArray(item.trend)
          ? item.trend.slice(-24).map((point) => pickScalars(point, [
            "month", "periodStart", "periodEnd", "rank", "gmvCents", "quantity", "pageViews", "visitors",
            "conversionBps", "marketPriceCents", "averageTransactionPriceCents",
          ]))
          : [],
      };
    }) : [],
    missingSelections: Array.isArray(result.missingSelections)
      ? result.missingSelections.slice(0, 5).map((item) => pickScalars(item, ["skuCode", "category", "scope", "rankingDimension"]))
      : [],
    responseLimits: { selections: 5, trendMonths: 24 },
  };
}

export async function getMarketWorkspaceStatusPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "市场数据状态");
  const input = inputObject(args);
  assertOnlyKeys(input, []);
  const result = resultObject(await serviceSet(overrides).readMarketStatus(principal, context.signal));
  return {
    page: "market.status",
    available: true,
    dataRange: pickScalars(result.dataRange, ["startDate", "endDate"]),
    batches: Array.isArray(result.batches) ? result.batches.slice(0, 8).map(projectImportBatch) : [],
    imageCache: pickScalars(result.imageCache, ["total", "cached", "failed", "pending"]),
  };
}

export async function getOperatingSettingsSummaryPageData(
  args: unknown,
  context: PageDataToolContext,
  overrides?: Partial<PageDataToolServices>,
) {
  const principal = requirePrincipal(context);
  requireUnrestrictedDataScope(principal, "系统运营设置");
  const input = inputObject(args);
  assertOnlyKeys(input, []);
  const settings = await serviceSet(overrides).readOperatingSettings(principal, context.signal);
  return {
    page: "settings.operating",
    available: true,
    settings: pickScalars(settings, [
      "targetDays", "criticalDays", "slowDays", "stagnantDays", "autoReplenishment",
      "inventoryAlert", "allowNegativeInventory", "updatedAt",
    ]),
  };
}
