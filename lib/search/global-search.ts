import { createDjangoMarketConsumerReader, type MarketConsumerReader, type MarketConsumerResponseMap } from "@/lib/django/market-consumer-reader";
import {
  getGlobalSearchNavigationTarget,
  globalSearchDefaultTargets,
  isGlobalSearchGroupKey,
  type GlobalSearchGroupKey,
  type GlobalSearchNavigationModule,
  type GlobalSearchNavigationTarget,
} from "./target-contract";
import type { AppPrincipal, AppRole } from "@/lib/auth/authorization";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
  type SalesConsumerResponseMap,
} from "@/lib/django/sales-consumer-reader";
import {
  createDjangoFinanceConsumerReader,
  type FinanceConsumerReader,
  type FinanceConsumerResponseMap,
} from "@/lib/django/finance-consumer-reader";
import {
  createDjangoNetshopConsumerReader,
  type NetshopConsumerReader,
  type NetshopConsumerResponseMap,
} from "@/lib/django/netshop-consumer-reader";
import {
  createDjangoProductsConsumerReader,
  type ProductsConsumerReader,
  type ProductsConsumerResponseMap,
} from "@/lib/django/products-consumer-reader";
import {
  createDjangoInventoryConsumerReader,
  type InventoryConsumerReader,
  type InventoryConsumerResponseMap,
} from "@/lib/django/inventory-consumer-reader";
import {
  createDjangoWorkflowConsumerReader,
  type WorkflowConsumerReader,
} from "@/lib/django/workflow-consumer-reader";
import {
  createDjangoCustomerServiceConsumerReader,
  type CustomerServiceConsumerReader,
  type CustomerServiceConsumerResponseMap,
} from "@/lib/django/customer-service-consumer-reader";
import {
  createDjangoErpReferenceConsumerReader,
  type ErpReferenceConsumerReader,
  type ErpReferenceConsumerResponseMap,
} from "@/lib/django/erp-reference-consumer-reader";

export { globalSearchGroupKeys, isGlobalSearchGroupKey } from "./target-contract";
export type { GlobalSearchGroupKey, GlobalSearchNavigationTarget } from "./target-contract";

export const GLOBAL_SEARCH_MIN_QUERY_LENGTH = 2;
export const GLOBAL_SEARCH_MAX_QUERY_LENGTH = 80;
export const GLOBAL_SEARCH_DEFAULT_GROUP_LIMIT = 4;
export const GLOBAL_SEARCH_MAX_GROUP_LIMIT = 8;
export const GLOBAL_SEARCH_DEFAULT_TOTAL_LIMIT = 48;
export const GLOBAL_SEARCH_MAX_TOTAL_LIMIT = 50;
export const GLOBAL_SEARCH_MAX_GROUP_CONCURRENCY = 3;
export const GLOBAL_SEARCH_GROUP_DEADLINE_MS = 2_000;

const GLOBAL_SEARCH_MAX_GROUP_DEADLINE_MS = 10_000;

const GLOBAL_SEARCH_TEXT_LIMITS = {
  id: { characters: 160, bytes: 320 },
  title: { characters: 200, bytes: 768 },
  subtitle: { characters: 240, bytes: 768 },
  detail: { characters: 400, bytes: 1_536 },
  updatedAt: { characters: 48, bytes: 96 },
} as const;

export type GlobalSearchModule = GlobalSearchNavigationModule;

export type GlobalSearchItem = {
  id: string;
  title: string;
  subtitle: string;
  detail: string;
  updatedAt: string;
  amountCents: number | null;
  module: GlobalSearchModule;
  kind: GlobalSearchGroupKey;
  target: GlobalSearchNavigationTarget;
};

export type GlobalSearchGroup = {
  key: GlobalSearchGroupKey;
  label: string;
  icon: string;
  module: GlobalSearchModule;
  available: boolean;
  /** `total` is exact only when this flag is true; otherwise it is a known lower bound. */
  total: number;
  totalExact: boolean;
  hasMore: boolean;
  items: GlobalSearchItem[];
};

export type GlobalSearchRequest = {
  query: string;
  page: number;
  groupLimit: number;
  totalLimit: number;
  group: GlobalSearchGroupKey | null;
};

export type GlobalSearchResponse = {
  query: string;
  page: number;
  limits: { perGroup: number; total: number };
  returned: number;
  dataCutoffDate: string | null;
  filtersApplied: {
    query: string;
    group: GlobalSearchGroupKey | null;
    page: number;
    perGroupLimit: number;
    totalLimit: number;
    dataScope: {
      mode: "unrestricted" | "restricted";
      warehouses: string[] | null;
      channels: string[] | null;
      platforms: string[] | null;
    };
  };
  truncated: boolean;
  deadlineExceeded: boolean;
  timedOutDomains: string[];
  groups: GlobalSearchGroup[];
  coveredDomains: string[];
  unavailableDomains: string[];
};

type SearchRow = {
  result_id: string | number | null;
  title: string | null;
  subtitle: string | null;
  detail: string | null;
  updated_at: string | null;
  amount_cents: number | null;
  target_hint?: string | null;
  total_count?: number;
};

export type GlobalSearchExecutionOptions = {
  /** Test/worker override; callers cannot raise the hard 10-second ceiling. */
  deadlineMs?: number;
  marketReader?: MarketConsumerReader;
  salesReader?: SalesConsumerReader;
  financeReader?: FinanceConsumerReader;
  netshopReader?: NetshopConsumerReader;
  productsReader?: ProductsConsumerReader;
  inventoryReader?: InventoryConsumerReader;
  workflowReader?: WorkflowConsumerReader;
  customerServiceReader?: CustomerServiceConsumerReader;
  erpReferenceReader?: ErpReferenceConsumerReader;
  signal?: AbortSignal;
};

type SearchGroupDefinitionBase = {
  label: string;
  icon: string;
  allowedRoles: readonly AppRole[];
  scopeKind?: "warehouse" | "channel_platform" | "platform" | "finance" | "shop" | "unscoped_only";
};

type SearchGroupDefinition = {
  [K in GlobalSearchGroupKey]: SearchGroupDefinitionBase & {
    key: K;
    module: (typeof globalSearchDefaultTargets)[K]["module"];
  };
}[GlobalSearchGroupKey];

const allRoles = ["viewer", "analyst", "operator", "admin"] as const;
const businessRoles = ["analyst", "operator", "admin"] as const;
const financeRoles = ["analyst", "admin"] as const;
const operatorRoles = ["operator", "admin"] as const;

export class GlobalSearchRequestError extends Error {
  readonly status = 400;

  constructor(message: string) {
    super(message);
    this.name = "GlobalSearchRequestError";
  }
}

const globalSearchQueryKeys = new Set(["q", "group", "page", "pageSize", "limit", "totalLimit"]);

function singleParameter(searchParams: URLSearchParams, key: string): string | null {
  const values = searchParams.getAll(key);
  if (values.length > 1) throw new GlobalSearchRequestError(`${key} 参数不能重复。`);
  return values[0] ?? null;
}

function boundedInteger(value: string | null, fallback: number, min: number, max: number, field: string) {
  if (value === null) return fallback;
  if (!/^[1-9]\d*$/.test(value)) throw new GlobalSearchRequestError(`${field} 必须为十进制正整数。`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new GlobalSearchRequestError(`${field} 超出允许范围。`);
  if (parsed < min || parsed > max) throw new GlobalSearchRequestError(`分页参数必须在 ${min} 到 ${max} 之间。`);
  return parsed;
}

export function normalizeGlobalSearchRequest(searchParams: URLSearchParams): GlobalSearchRequest {
  for (const key of searchParams.keys()) {
    if (!globalSearchQueryKeys.has(key)) throw new GlobalSearchRequestError("存在不支持的查询参数。");
  }
  const query = (singleParameter(searchParams, "q") ?? "").trim();
  const queryLength = Array.from(query).length;
  if (queryLength < GLOBAL_SEARCH_MIN_QUERY_LENGTH) {
    throw new GlobalSearchRequestError(`请输入至少 ${GLOBAL_SEARCH_MIN_QUERY_LENGTH} 个字符。`);
  }
  if (queryLength > GLOBAL_SEARCH_MAX_QUERY_LENGTH) {
    throw new GlobalSearchRequestError(`搜索词不能超过 ${GLOBAL_SEARCH_MAX_QUERY_LENGTH} 个字符。`);
  }
  const rawGroup = singleParameter(searchParams, "group");
  const group = rawGroup === null
    ? null
    : isGlobalSearchGroupKey(rawGroup) ? rawGroup : null;
  if (rawGroup !== null && !group) throw new GlobalSearchRequestError("搜索分组不在允许清单中。");
  const pageSize = singleParameter(searchParams, "pageSize");
  const legacyLimit = singleParameter(searchParams, "limit");
  if (pageSize !== null && legacyLimit !== null) {
    throw new GlobalSearchRequestError("pageSize 与 limit 不能同时提供。");
  }
  return {
    query,
    page: boundedInteger(singleParameter(searchParams, "page"), 1, 1, 10_000, "page"),
    groupLimit: boundedInteger(
      pageSize ?? legacyLimit,
      GLOBAL_SEARCH_DEFAULT_GROUP_LIMIT,
      1,
      GLOBAL_SEARCH_MAX_GROUP_LIMIT,
      pageSize !== null ? "pageSize" : "limit",
    ),
    totalLimit: boundedInteger(
      singleParameter(searchParams, "totalLimit"),
      GLOBAL_SEARCH_DEFAULT_TOTAL_LIMIT,
      1,
      GLOBAL_SEARCH_MAX_TOTAL_LIMIT,
      "totalLimit",
    ),
    group,
  };
}

const staticDefinitions: readonly SearchGroupDefinition[] = [
  {
    key: "products",
    label: "货品主数据",
    icon: "品",
    module: "product",
    allowedRoles: allRoles,
  },
  {
    key: "orders",
    label: "销售订单",
    icon: "单",
    module: "sales",
    allowedRoles: allRoles,
    scopeKind: "channel_platform",
  },
  {
    key: "jd_products",
    label: "京东 SKU / SPU / 网店商品",
    icon: "京",
    module: "shop",
    allowedRoles: allRoles,
    scopeKind: "platform",
  },
  {
    key: "inventory",
    label: "库存记录",
    icon: "库",
    module: "inventory",
    allowedRoles: allRoles,
    scopeKind: "warehouse",
  },
  {
    key: "market_skus",
    label: "市场 SKU",
    icon: "市",
    module: "market",
    allowedRoles: allRoles,
    scopeKind: "unscoped_only",
  },
  {
    key: "combos",
    label: "组合装关系",
    icon: "组",
    module: "product",
    allowedRoles: allRoles,
  },
  {
    key: "replenishment",
    label: "备货计划",
    icon: "备",
    module: "inventory",
    allowedRoles: businessRoles,
    scopeKind: "warehouse",
  },
  {
    key: "market_annotations",
    label: "细分品类标注",
    icon: "标",
    module: "market",
    allowedRoles: allRoles,
    scopeKind: "unscoped_only",
  },
  {
    key: "customer_service",
    label: "客服会话",
    icon: "服",
    module: "customer_service",
    allowedRoles: businessRoles,
    scopeKind: "unscoped_only",
  },
  {
    key: "finance",
    label: "财务科目",
    icon: "财",
    module: "sales",
    allowedRoles: financeRoles,
    scopeKind: "finance",
  },
  {
    key: "targets",
    label: "经营目标",
    icon: "目",
    module: "sales",
    allowedRoles: financeRoles,
    scopeKind: "platform",
  },
] as const;

const financeSearchDefinition = staticDefinitions.find((definition) => definition.key === "finance")!;
const targetSearchDefinition = staticDefinitions.find((definition) => definition.key === "targets")!;
const inventorySearchDefinition = staticDefinitions.find((definition) => definition.key === "inventory")!;
const replenishmentSearchDefinition = staticDefinitions.find((definition) => definition.key === "replenishment")!;
const customerServiceSearchDefinition = staticDefinitions.find((definition) => definition.key === "customer_service")!;

const workflowDefinition = {
  key: "workflow" as const,
  label: "运营事务",
  icon: "务",
  module: "workflow" as const,
};

const salesOrderDefinition = {
  key: "orders" as const,
  label: "销售订单",
  icon: "单",
  module: "sales" as const,
};

const inventoryAgeDefinition = {
  key: "inventory_age" as const,
  label: "库龄数据",
  icon: "龄",
  module: "inventory" as const,
};

const importDefinition = {
  key: "imports" as const,
  label: "导入批次",
  icon: "入",
  module: "import" as const,
};

export const GLOBAL_SEARCH_COVERAGE = [
  "货品主数据",
  "销售订单",
  "京东 SKU/SPU 与网店商品",
  "库存记录",
  "库龄数据（仓库库龄与滚动销量）",
  "组合装关系",
  "备货计划",
  "市场 SKU 与细分品类标注",
  "客服会话（仅显式客服分组允许授权角色按正文匹配，且只返回最小摘要）",
  "财务科目与经营目标",
  "运营事务",
  "导入批次",
] as const;

type SearchGroupIdentity = Pick<SearchGroupDefinition, "key" | "label" | "icon" | "module">;

function emptyGroup(definition: SearchGroupIdentity, totalExact = true): GlobalSearchGroup {
  return { ...definition, available: false, total: 0, totalExact, hasMore: false, items: [] };
}

function incompleteGroup(definition: SearchGroupIdentity, available: boolean): GlobalSearchGroup {
  return available
    ? { ...definition, available: true, total: 0, totalExact: false, hasMore: false, items: [] }
    : emptyGroup(definition);
}

function mapSearchRows(
  definition: SearchGroupIdentity,
  rows: SearchRow[],
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  totalOverride?: number,
): GlobalSearchGroup {
  const offset = (request.page - 1) * request.groupLimit;
  const hasMore = totalOverride === undefined
    ? rows.length > request.groupLimit
    : offset + Math.min(rows.length, request.groupLimit) < totalOverride;
  const visibleRows = rows.slice(0, request.groupLimit);
  // LIMIT + 1 proves only that another row exists. On a non-empty terminal
  // page the offset makes the count exact; an out-of-range page has no safe
  // lower bound beyond zero without re-running a count query.
  const total = totalOverride ?? (visibleRows.length === 0 && request.page > 1
    ? 0
    : offset + visibleRows.length + (hasMore ? 1 : 0));
  const totalExact = totalOverride !== undefined
    || (!hasMore && (request.page === 1 || visibleRows.length > 0));
  const items = visibleRows.map((row) => {
    const target = getGlobalSearchNavigationTarget(definition.key, row.target_hint);
    return {
      kind: definition.key,
      id: boundGlobalSearchText(row.result_id, GLOBAL_SEARCH_TEXT_LIMITS.id),
      title: boundGlobalSearchText(row.title, GLOBAL_SEARCH_TEXT_LIMITS.title),
      subtitle: boundGlobalSearchText(row.subtitle, GLOBAL_SEARCH_TEXT_LIMITS.subtitle),
      detail: boundGlobalSearchText(row.detail, GLOBAL_SEARCH_TEXT_LIMITS.detail),
      updatedAt: boundGlobalSearchText(row.updated_at, GLOBAL_SEARCH_TEXT_LIMITS.updatedAt),
      amountCents: principal.role === "viewer" || row.amount_cents === null || row.amount_cents === undefined
        ? null
        : Number(row.amount_cents),
      module: target.module,
      target,
    };
  });
  return {
    ...definition,
    available: true,
    total,
    totalExact,
    hasMore,
    items,
  };
}

function boundGlobalSearchText(
  value: unknown,
  limits: { characters: number; bytes: number },
): string {
  const characterBounded = Array.from(String(value ?? "")).slice(0, limits.characters).join("");
  const encoded = new TextEncoder().encode(characterBounded);
  if (encoded.byteLength <= limits.bytes) return characterBounded;
  let end = limits.bytes;
  while (end > 0) {
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(encoded.slice(0, end));
    } catch {
      end -= 1;
    }
  }
  return "";
}

function isGroupAuthorized(definition: Pick<SearchGroupDefinition, "allowedRoles" | "scopeKind">, principal: AppPrincipal) {
  return definition.allowedRoles.includes(principal.role)
    && !(definition.scopeKind === "unscoped_only" && principal.scope !== null);
}

async function queryWorkflowGroup(
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  workflowReader: WorkflowConsumerReader,
  signal?: AbortSignal,
) {
  const offset = (request.page - 1) * request.groupLimit;
  try {
    const result = await workflowReader.read(principal, {
      operation: "workflow_search",
      query: request.query,
      offset,
      limit: request.groupLimit,
    }, { signal });
    const rows: SearchRow[] = result.data.items.map((item) => ({
      result_id: item.resultId!,
      title: item.title,
      subtitle: item.subtitle,
      detail: item.detail,
      updated_at: item.updatedAt,
      amount_cents: item.amountCents,
      target_hint: item.targetHint ?? "task",
    }));
    return mapSearchRows(workflowDefinition, rows, request, principal, result.data.total);
  } catch {
    return emptyGroup(workflowDefinition, false);
  }
}

function validCustomerServiceSearch(value: unknown): value is CustomerServiceConsumerResponseMap["search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items) && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean" && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.resultId === "string" && typeof row.title === "string"
        && typeof row.subtitle === "string" && typeof row.detail === "string"
        && typeof row.updatedAt === "string" && row.amountCents === null;
    });
}

async function queryCustomerServiceGroup(
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  reader: CustomerServiceConsumerReader,
  signal?: AbortSignal,
) {
  const offset = (request.page - 1) * request.groupLimit;
  try {
    const result = await reader.read(principal, {
      operation: "search",
      query: request.query,
      offset,
      limit: request.groupLimit,
      includeMessages: request.group === "customer_service",
    }, { signal });
    if (!result.revision || !validCustomerServiceSearch(result.data)) return emptyGroup(customerServiceSearchDefinition, false);
    const rows: SearchRow[] = result.data.items.map((item) => ({
      result_id: item.resultId,
      title: item.title,
      subtitle: item.subtitle,
      detail: item.detail,
      updated_at: item.updatedAt,
      amount_cents: item.amountCents,
    }));
    return mapSearchRows(customerServiceSearchDefinition, rows, request, principal, result.data.total);
  } catch {
    return emptyGroup(customerServiceSearchDefinition, false);
  }
}

function validErpSearch(value: unknown): value is ErpReferenceConsumerResponseMap["product_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items) && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean" && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.resultId === "string" && typeof row.title === "string"
        && typeof row.subtitle === "string" && typeof row.detail === "string"
        && typeof row.updatedAt === "string" && row.amountCents === null;
    });
}

async function queryErpGroup(
  reader: ErpReferenceConsumerReader,
  operation: "product_search" | "combo_search",
  definition: SearchGroupDefinition,
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  signal?: AbortSignal,
) {
  const offset = (request.page - 1) * request.groupLimit;
  try {
    const result = await reader.read(principal, {
      operation, query: request.query, offset, limit: request.groupLimit,
    }, { signal });
    if (!result.revision || !validErpSearch(result.data)
      || !validFinanceConsumerWindow(result.data, offset, request.groupLimit)) {
      return emptyGroup(definition, false);
    }
    return mapSearchRows(definition, result.data.items.map((item) => ({
      result_id: item.resultId, title: item.title, subtitle: item.subtitle,
      detail: item.detail, updated_at: item.updatedAt, amount_cents: null,
    })), request, principal, result.data.total);
  } catch {
    return emptyGroup(definition, false);
  }
}

function validSalesOrderSearch(value: unknown): value is SalesConsumerResponseMap["order_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.title === "string"
        && typeof row.subtitle === "string" && typeof row.detail === "string"
        && typeof row.updatedAt === "string" && Number.isSafeInteger(row.amountCents);
    });
}

function validSalesImportSearch(value: unknown): value is SalesConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.fileName === "string" && typeof row.status === "string"
        && Number.isSafeInteger(row.rowCount) && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

function validConsumerPage(
  data: { items: unknown[]; total: number; truncated: boolean },
  page: number,
  pageSize: number,
) {
  const offset = (page - 1) * pageSize;
  const expectedLength = offset >= data.total ? 0 : Math.min(pageSize, data.total - offset);
  return data.items.length === expectedLength && data.truncated === page * pageSize < data.total;
}

async function querySalesOrderGroup(
  salesReader: SalesConsumerReader,
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  signal?: AbortSignal,
) {
  try {
    const result = await salesReader.read(principal, {
      operation: "order_search",
      query: request.query,
      page: request.page,
      pageSize: request.groupLimit,
    }, { signal });
    if (!result || typeof result.revision !== "string" || !result.revision
      || !validSalesOrderSearch(result.data)
      || !validConsumerPage(result.data, request.page, request.groupLimit)) {
      return emptyGroup(salesOrderDefinition, false);
    }
    const rows: SearchRow[] = result.data.items.map((item) => ({
      result_id: item.id,
      title: item.title,
      subtitle: item.subtitle,
      detail: item.detail,
      updated_at: item.updatedAt,
      amount_cents: item.amountCents,
    }));
    return mapSearchRows(salesOrderDefinition, rows, request, principal, result.data.total);
  } catch {
    return emptyGroup(salesOrderDefinition, false);
  }
}

function validNetshopSearch(
  value: unknown,
): value is NetshopConsumerResponseMap["row_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.title === "string"
        && typeof row.subtitle === "string" && typeof row.detail === "string"
        && typeof row.updatedAt === "string" && row.amountCents === null;
    });
}

function validNetshopImportSearch(
  value: unknown,
): value is NetshopConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.dataset === "string" && typeof row.platform === "string"
        && typeof row.shopName === "string" && typeof row.fileName === "string"
        && typeof row.status === "string" && Number.isSafeInteger(row.rowCount)
        && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

function validProductsImportSearch(
  value: unknown,
): value is ProductsConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.fileName === "string" && typeof row.status === "string"
        && Number.isSafeInteger(row.rowCount) && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

async function queryNetshopSearchGroup(
  netshopReader: NetshopConsumerReader,
  definition: SearchGroupDefinition,
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  signal?: AbortSignal,
) {
  try {
    const offset = (request.page - 1) * request.groupLimit;
    const result = await netshopReader.read(principal, {
      operation: "row_search",
      query: request.query,
      offset,
      limit: request.groupLimit,
    }, { signal });
    if (!result.revision || !validNetshopSearch(result.data)
      || !validFinanceConsumerWindow(result.data, offset, request.groupLimit)) {
      return emptyGroup(definition, false);
    }
    return mapSearchRows(
      definition,
      result.data.items.map((item) => ({
        result_id: String(item.id),
        title: String(item.title),
        subtitle: String(item.subtitle),
        detail: String(item.detail),
        updated_at: String(item.updatedAt),
        amount_cents: item.amountCents === null ? null : Number(item.amountCents),
      })),
      request,
      principal,
      result.data.total,
    );
  } catch {
    return emptyGroup(definition, false);
  }
}

type FinanceSearchOperation = "line_search" | "target_search";

async function queryMarketSearchGroup(
  reader: MarketConsumerReader,
  operation: "sku_search" | "annotation_search",
  definition: SearchGroupIdentity,
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  signal?: AbortSignal,
) {
  try {
    const offset = (request.page - 1) * request.groupLimit;
    const result = await reader.read(principal, { operation, query: request.query, offset, limit: request.groupLimit }, { signal });
    if (!result.revision || !validFinanceSearch(result.data)
      || !validFinanceConsumerWindow(result.data, offset, request.groupLimit)) return emptyGroup(definition, false);
    return mapSearchRows(definition, result.data.items.map((item) => ({
      result_id: item.id, title: item.title, subtitle: item.subtitle, detail: item.detail,
      updated_at: item.updatedAt, amount_cents: item.amountCents,
    })), request, principal, result.data.total);
  } catch {
    return emptyGroup(definition, false);
  }
}

function validMarketImportSearch(value: unknown): value is MarketConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items) && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean" && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.sourceType === "string"
        && typeof row.fileName === "string" && typeof row.status === "string"
        && Number.isSafeInteger(row.rowCount) && Number(row.rowCount) >= 0
        && typeof row.periodStart === "string" && typeof row.periodEnd === "string"
        && typeof row.createdAt === "string" && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

function validFinanceSearch(
  value: unknown,
): value is FinanceConsumerResponseMap[FinanceSearchOperation] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.title === "string"
        && typeof row.subtitle === "string" && typeof row.detail === "string"
        && typeof row.updatedAt === "string"
        && (row.amountCents === null || Number.isSafeInteger(row.amountCents));
    });
}

function validFinanceImportSearch(
  value: unknown,
): value is FinanceConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.fileName === "string" && typeof row.status === "string"
        && Number.isSafeInteger(row.rowCount) && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

function validFinanceConsumerWindow(
  data: { items: unknown[]; total: number; truncated: boolean },
  offset: number,
  limit: number,
) {
  const expectedLength = offset >= data.total ? 0 : Math.min(limit, data.total - offset);
  return data.items.length === expectedLength
    && data.truncated === offset + limit < data.total;
}

async function queryFinanceSearchGroup(
  financeReader: FinanceConsumerReader,
  operation: FinanceSearchOperation,
  definition: SearchGroupDefinition,
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  signal?: AbortSignal,
) {
  try {
    const offset = (request.page - 1) * request.groupLimit;
    const result = await financeReader.read(principal, {
      operation,
      query: request.query,
      offset,
      limit: request.groupLimit,
    }, { signal });
    if (!result || typeof result.revision !== "string" || !result.revision
      || !validFinanceSearch(result.data)
      || !validFinanceConsumerWindow(result.data, offset, request.groupLimit)) {
      return emptyGroup(definition, false);
    }
    const rows: SearchRow[] = result.data.items.map((item) => ({
      result_id: item.id,
      title: item.title,
      subtitle: item.subtitle,
      detail: item.detail,
      updated_at: item.updatedAt,
      amount_cents: item.amountCents,
      total_count: result.data.total,
    }));
    return mapSearchRows(definition, rows, request, principal, result.data.total);
  } catch {
    return emptyGroup(definition, false);
  }
}

type InventorySearchOperation = "inventory_search" | "age_search" | "replenishment_search";

function validInventorySearch(
  value: unknown,
): value is InventoryConsumerResponseMap[InventorySearchOperation] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.title === "string"
        && typeof row.subtitle === "string" && typeof row.detail === "string"
        && typeof row.updatedAt === "string"
        && (row.amountCents === null || Number.isSafeInteger(row.amountCents));
    });
}

async function queryInventorySearchGroup(
  reader: InventoryConsumerReader,
  operation: InventorySearchOperation,
  definition: SearchGroupIdentity,
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  signal?: AbortSignal,
) {
  try {
    const offset = (request.page - 1) * request.groupLimit;
    const result = operation === "replenishment_search"
      ? await reader.read(principal, {
          operation,
          query: request.query,
          status: null,
          warehouse: null,
          offset,
          limit: request.groupLimit,
        }, { signal })
      : await reader.read(principal, {
          operation,
          query: request.query,
          offset,
          limit: request.groupLimit,
        }, { signal });
    if (!result.revision || !validInventorySearch(result.data)
      || !validFinanceConsumerWindow(result.data, offset, request.groupLimit)) {
      return emptyGroup(definition, false);
    }
    return mapSearchRows(
      definition,
      result.data.items.map((item) => ({
        result_id: String(item.id),
        title: String(item.title),
        subtitle: String(item.subtitle),
        detail: String(item.detail),
        updated_at: String(item.updatedAt),
        amount_cents: item.amountCents === null ? null : Number(item.amountCents),
      })),
      request,
      principal,
      result.data.total,
    );
  } catch {
    return emptyGroup(definition, false);
  }
}

function validInventoryImportSearch(
  value: unknown,
): value is InventoryConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.dataset === "string" && typeof row.fileName === "string"
        && typeof row.status === "string" && Number.isSafeInteger(row.rowCount)
        && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

function validCustomerServiceImportSearch(
  value: unknown,
): value is CustomerServiceConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.fileName === "string" && typeof row.status === "string"
        && Number.isSafeInteger(row.rowCount) && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

function validErpImportSearch(
  value: unknown,
): value is ErpReferenceConsumerResponseMap["import_batch_search"] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return Array.isArray(data.items)
    && Number.isSafeInteger(data.total) && Number(data.total) >= 0
    && typeof data.truncated === "boolean"
    && data.items.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.id === "string" && typeof row.source === "string"
        && typeof row.fileName === "string" && typeof row.status === "string"
        && Number.isSafeInteger(row.rowCount) && typeof row.createdAt === "string"
        && (row.completedAt === null || typeof row.completedAt === "string");
    });
}

async function queryImportGroup(
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  salesReader: SalesConsumerReader,
  financeReader: FinanceConsumerReader,
  netshopReader: NetshopConsumerReader,
  productsReader: ProductsConsumerReader,
  inventoryReader: InventoryConsumerReader,
  customerServiceReader: CustomerServiceConsumerReader,
  erpReferenceReader: ErpReferenceConsumerReader,
  marketReader: MarketConsumerReader,
  signal?: AbortSignal,
) {
  try {
    const salesHead = await salesReader.read(principal, {
      operation: "import_batch_search",
      query: request.query,
      page: 1,
      pageSize: 1,
    }, { signal });
    if (!salesHead || typeof salesHead.revision !== "string" || !salesHead.revision
      || !validSalesImportSearch(salesHead.data)
      || !validConsumerPage(salesHead.data, 1, 1)) return emptyGroup(importDefinition, false);
    const salesTotal = salesHead.data.total;
    let financeRevision = "";
    let financeTotal = 0;
    {
      const financeHead = await financeReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        offset: 0,
        limit: 1,
      }, { signal });
      if (!financeHead || typeof financeHead.revision !== "string" || !financeHead.revision
        || !validFinanceImportSearch(financeHead.data)
        || !validFinanceConsumerWindow(financeHead.data, 0, 1)) {
        return emptyGroup(importDefinition, false);
      }
      financeRevision = financeHead.revision;
      financeTotal = financeHead.data.total;
    }
    const netshopHead = await netshopReader.read(principal, {
      operation: "import_batch_search",
      query: request.query,
      offset: 0,
      limit: 1,
    }, { signal });
    if (!netshopHead.revision || !validNetshopImportSearch(netshopHead.data)
      || !validFinanceConsumerWindow(netshopHead.data, 0, 1)) {
      return emptyGroup(importDefinition, false);
    }
    const netshopRevision = netshopHead.revision;
    const netshopTotal = netshopHead.data.total;
    const productsHead = await productsReader.read(principal, {
      operation: "import_batch_search",
      query: request.query,
      offset: 0,
      limit: 1,
    }, { signal });
    if (!productsHead.revision || !validProductsImportSearch(productsHead.data)
      || !validFinanceConsumerWindow(productsHead.data, 0, 1)) {
      return emptyGroup(importDefinition, false);
    }
    const productsRevision = productsHead.revision;
    const productsTotal = productsHead.data.total;
    const inventoryHead = await inventoryReader.read(principal, {
      operation: "import_batch_search",
      dataset: null,
      query: request.query,
      offset: 0,
      limit: 1,
    }, { signal });
    if (!inventoryHead.revision || !validInventoryImportSearch(inventoryHead.data)
      || !validFinanceConsumerWindow(inventoryHead.data, 0, 1)) {
      return emptyGroup(importDefinition, false);
    }
    const inventoryRevision = inventoryHead.revision;
    const inventoryTotal = inventoryHead.data.total;
    const customerServiceHead = await customerServiceReader.read(principal, {
      operation: "import_batch_search",
      query: request.query,
      offset: 0,
      limit: 1,
    }, { signal });
    if (!customerServiceHead.revision || !validCustomerServiceImportSearch(customerServiceHead.data)
      || !validFinanceConsumerWindow(customerServiceHead.data, 0, 1)) {
      return emptyGroup(importDefinition, false);
    }
    const customerServiceRevision = customerServiceHead.revision;
    const customerServiceTotal = customerServiceHead.data.total;
    const erpHead = await erpReferenceReader.read(principal, {
      operation: "import_batch_search",
      query: request.query,
      offset: 0,
      limit: 1,
    }, { signal });
    if (!erpHead.revision || !validErpImportSearch(erpHead.data)
      || !validFinanceConsumerWindow(erpHead.data, 0, 1)) {
      return emptyGroup(importDefinition, false);
    }
    const erpRevision = erpHead.revision;
    const erpTotal = erpHead.data.total;
    const globalOffset = (request.page - 1) * request.groupLimit;
    const salesTake = globalOffset < salesTotal ? Math.min(request.groupLimit, salesTotal - globalOffset) : 0;
    let salesItems: SalesConsumerResponseMap["import_batch_search"]["items"] = [];
    if (salesTake > 0) {
      const salesPage = await salesReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        page: request.page,
        pageSize: request.groupLimit,
      }, { signal });
      if (!salesPage || salesPage.revision !== salesHead.revision
        || !validSalesImportSearch(salesPage.data) || salesPage.data.total !== salesTotal
        || !validConsumerPage(salesPage.data, request.page, request.groupLimit)
        || salesPage.data.items.length !== salesTake) return emptyGroup(importDefinition, false);
      salesItems = salesPage.data.items;
    }
    const financeOffset = Math.max(0, globalOffset - salesTotal);
    const financeTake = financeOffset < financeTotal
      ? Math.min(request.groupLimit - salesItems.length, financeTotal - financeOffset)
      : 0;
    let financeItems: FinanceConsumerResponseMap["import_batch_search"]["items"] = [];
    if (financeTake > 0) {
      const financePage = await financeReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        offset: financeOffset,
        limit: financeTake,
      }, { signal });
      if (!financePage || financePage.revision !== financeRevision
        || !validFinanceImportSearch(financePage.data)
        || !validFinanceConsumerWindow(financePage.data, financeOffset, financeTake)
        || financePage.data.total !== financeTotal
        || financePage.data.items.length !== financeTake) {
        return emptyGroup(importDefinition, false);
      }
      financeItems = financePage.data.items;
    }
    const netshopOffset = Math.max(0, globalOffset - salesTotal - financeTotal);
    const netshopTake = netshopOffset < netshopTotal
      ? Math.min(request.groupLimit - salesItems.length - financeItems.length, netshopTotal - netshopOffset)
      : 0;
    let netshopItems: NetshopConsumerResponseMap["import_batch_search"]["items"] = [];
    if (netshopTake > 0) {
      const netshopPage = await netshopReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        offset: netshopOffset,
        limit: netshopTake,
      }, { signal });
      if (netshopPage.revision !== netshopRevision
        || !validNetshopImportSearch(netshopPage.data)
        || !validFinanceConsumerWindow(netshopPage.data, netshopOffset, netshopTake)
        || netshopPage.data.total !== netshopTotal
        || netshopPage.data.items.length !== netshopTake) {
        return emptyGroup(importDefinition, false);
      }
      netshopItems = netshopPage.data.items;
    }
    const productsOffset = Math.max(0, globalOffset - salesTotal - financeTotal - netshopTotal);
    const productsTake = productsOffset < productsTotal
      ? Math.min(
          request.groupLimit - salesItems.length - financeItems.length - netshopItems.length,
          productsTotal - productsOffset,
        )
      : 0;
    let productsItems: ProductsConsumerResponseMap["import_batch_search"]["items"] = [];
    if (productsTake > 0) {
      const productsPage = await productsReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        offset: productsOffset,
        limit: productsTake,
      }, { signal });
      if (productsPage.revision !== productsRevision
        || !validProductsImportSearch(productsPage.data)
        || !validFinanceConsumerWindow(productsPage.data, productsOffset, productsTake)
        || productsPage.data.total !== productsTotal
        || productsPage.data.items.length !== productsTake) {
        return emptyGroup(importDefinition, false);
      }
      productsItems = productsPage.data.items;
    }
    const inventoryOffset = Math.max(
      0,
      globalOffset - salesTotal - financeTotal - netshopTotal - productsTotal,
    );
    const inventoryTake = inventoryOffset < inventoryTotal
      ? Math.min(
          request.groupLimit - salesItems.length - financeItems.length - netshopItems.length - productsItems.length,
          inventoryTotal - inventoryOffset,
        )
      : 0;
    let inventoryItems: InventoryConsumerResponseMap["import_batch_search"]["items"] = [];
    if (inventoryTake > 0) {
      const inventoryPage = await inventoryReader.read(principal, {
        operation: "import_batch_search",
        dataset: null,
        query: request.query,
        offset: inventoryOffset,
        limit: inventoryTake,
      }, { signal });
      if (inventoryPage.revision !== inventoryRevision
        || !validInventoryImportSearch(inventoryPage.data)
        || !validFinanceConsumerWindow(inventoryPage.data, inventoryOffset, inventoryTake)
        || inventoryPage.data.total !== inventoryTotal
        || inventoryPage.data.items.length !== inventoryTake) {
        return emptyGroup(importDefinition, false);
      }
      inventoryItems = inventoryPage.data.items;
    }
    const customerServiceOffset = Math.max(
      0,
      globalOffset - salesTotal - financeTotal - netshopTotal - productsTotal - inventoryTotal,
    );
    const customerServiceTake = customerServiceOffset < customerServiceTotal
      ? Math.min(
          request.groupLimit - salesItems.length - financeItems.length - netshopItems.length - productsItems.length - inventoryItems.length,
          customerServiceTotal - customerServiceOffset,
        )
      : 0;
    let customerServiceItems: CustomerServiceConsumerResponseMap["import_batch_search"]["items"] = [];
    if (customerServiceTake > 0) {
      const customerServicePage = await customerServiceReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        offset: customerServiceOffset,
        limit: customerServiceTake,
      }, { signal });
      if (customerServicePage.revision !== customerServiceRevision
        || !validCustomerServiceImportSearch(customerServicePage.data)
        || !validFinanceConsumerWindow(customerServicePage.data, customerServiceOffset, customerServiceTake)
        || customerServicePage.data.total !== customerServiceTotal
        || customerServicePage.data.items.length !== customerServiceTake) {
        return emptyGroup(importDefinition, false);
      }
      customerServiceItems = customerServicePage.data.items;
    }
    const erpOffset = Math.max(
      0,
      globalOffset - salesTotal - financeTotal - netshopTotal - productsTotal
        - inventoryTotal - customerServiceTotal,
    );
    const erpTake = erpOffset < erpTotal
      ? Math.min(
          request.groupLimit - salesItems.length - financeItems.length - netshopItems.length
            - productsItems.length - inventoryItems.length - customerServiceItems.length,
          erpTotal - erpOffset,
        )
      : 0;
    let erpItems: ErpReferenceConsumerResponseMap["import_batch_search"]["items"] = [];
    if (erpTake > 0) {
      const erpPage = await erpReferenceReader.read(principal, {
        operation: "import_batch_search",
        query: request.query,
        offset: erpOffset,
        limit: erpTake,
      }, { signal });
      if (erpPage.revision !== erpRevision
        || !validErpImportSearch(erpPage.data)
        || !validFinanceConsumerWindow(erpPage.data, erpOffset, erpTake)
        || erpPage.data.total !== erpTotal
        || erpPage.data.items.length !== erpTake) {
        return emptyGroup(importDefinition, false);
      }
      erpItems = erpPage.data.items;
    }
    const marketHead = await marketReader.read(principal, {
      operation: "import_batch_search", query: request.query, offset: 0, limit: 1,
    }, { signal });
    if (!marketHead.revision || !validMarketImportSearch(marketHead.data)
      || !validFinanceConsumerWindow(marketHead.data, 0, 1)) return emptyGroup(importDefinition, false);
    const marketOffset = Math.max(0, globalOffset - salesTotal - financeTotal - netshopTotal
      - productsTotal - inventoryTotal - customerServiceTotal - erpTotal);
    const marketTake = Math.min(request.groupLimit - salesItems.length - financeItems.length
      - netshopItems.length - productsItems.length - inventoryItems.length - customerServiceItems.length
      - erpItems.length, Math.max(0, marketHead.data.total - marketOffset));
    let marketItems: MarketConsumerResponseMap["import_batch_search"]["items"] = [];
    if (marketTake > 0) {
      const page = await marketReader.read(principal, {
        operation: "import_batch_search", query: request.query, offset: marketOffset, limit: marketTake,
      }, { signal });
      if (page.revision !== marketHead.revision || !validMarketImportSearch(page.data)
        || page.data.total !== marketHead.data.total
        || !validFinanceConsumerWindow(page.data, marketOffset, marketTake)
        || page.data.items.length !== marketTake) return emptyGroup(importDefinition, false);
      marketItems = page.data.items;
    }
    const combinedTotal = salesTotal + financeTotal + netshopTotal + productsTotal
      + inventoryTotal + customerServiceTotal + erpTotal + marketHead.data.total;
    const rows: SearchRow[] = [
      ...salesItems.map((item) => ({
        result_id: item.id,
        title: item.fileName,
        subtitle: "销售明细",
        detail: item.status,
        updated_at: item.completedAt ?? item.createdAt,
        amount_cents: null,
      })),
      ...financeItems.map((item) => ({
        result_id: item.id,
        title: item.fileName,
        subtitle: item.source,
        detail: item.status,
        updated_at: item.completedAt ?? item.createdAt,
        amount_cents: null,
      })),
      ...netshopItems.map((item) => ({
        result_id: item.id,
        title: item.fileName,
        subtitle: `网店 · ${item.platform} · ${item.source}`,
        detail: item.status,
        updated_at: item.completedAt ?? item.createdAt,
        amount_cents: null,
      })),
      ...productsItems.map((item) => ({
        result_id: item.id,
        title: item.fileName,
        subtitle: item.source,
        detail: item.status,
        updated_at: item.completedAt ?? item.createdAt,
        amount_cents: null,
      })),
      ...inventoryItems.map((item) => ({
        result_id: String(item.id),
        title: String(item.fileName),
        subtitle: String(item.source),
        detail: String(item.status),
        updated_at: String(item.completedAt ?? item.createdAt),
        amount_cents: null,
      })),
      ...customerServiceItems.map((item) => ({
        result_id: item.id,
        title: item.fileName,
        subtitle: item.source,
        detail: item.status,
        updated_at: item.completedAt ?? item.createdAt,
        amount_cents: null,
      })),
      ...erpItems.map((item) => ({
        result_id: item.id,
        title: item.fileName,
        subtitle: item.source,
        detail: item.status,
        updated_at: item.completedAt ?? item.createdAt,
        amount_cents: null,
      })),
      ...marketItems.map((item) => ({
        result_id: item.id, title: item.fileName, subtitle: `市场 · ${item.sourceType}`,
        detail: item.status, updated_at: item.completedAt ?? item.createdAt, amount_cents: null,
      })),
    ];
    return mapSearchRows(importDefinition, rows, request, principal, combinedTotal);
  } catch {
    return emptyGroup(importDefinition, false);
  }
}

type SearchGroupTask = {
  definition: SearchGroupIdentity;
  available: boolean;
  run: () => Promise<GlobalSearchGroup>;
};

const groupDeadlineToken = Symbol("global-search-group-deadline");

function boundedGroupDeadlineMs(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value) || value <= 0) return GLOBAL_SEARCH_GROUP_DEADLINE_MS;
  return Math.min(Math.max(1, Math.floor(value)), GLOBAL_SEARCH_MAX_GROUP_DEADLINE_MS);
}

async function runBoundedGroupTasks(
  tasks: readonly SearchGroupTask[],
  deadlineMs: number,
): Promise<{ groups: GlobalSearchGroup[]; deadlineExceeded: boolean; timedOutDomains: string[] }> {
  if (tasks.length === 0) return { groups: [], deadlineExceeded: false, timedOutDomains: [] };
  const deadlineAt = Date.now() + deadlineMs;
  const groups: Array<GlobalSearchGroup | undefined> = new Array(tasks.length);
  const timedOutDomains = new Set<string>();
  let nextIndex = 0;
  let deadlineExceeded = false;

  const worker = async () => {
    while (nextIndex < tasks.length) {
      if (Date.now() >= deadlineAt) {
        deadlineExceeded = true;
        return;
      }
      const taskIndex = nextIndex;
      nextIndex += 1;
      const task = tasks[taskIndex];
      const remainingMs = Math.max(1, deadlineAt - Date.now());
      let timer: ReturnType<typeof setTimeout> | undefined;
      const deadline = new Promise<typeof groupDeadlineToken>((resolve) => {
        timer = setTimeout(() => resolve(groupDeadlineToken), remainingMs);
      });
      const result = await Promise.race([
        Promise.resolve().then(task.run).catch(() => emptyGroup(task.definition, false)),
        deadline,
      ]);
      if (timer !== undefined) clearTimeout(timer);
      if (result === groupDeadlineToken) {
        deadlineExceeded = true;
        timedOutDomains.add(task.definition.label);
        groups[taskIndex] = incompleteGroup(task.definition, task.available);
        return;
      }
      groups[taskIndex] = result;
    }
  };

  await Promise.all(Array.from(
    { length: Math.min(GLOBAL_SEARCH_MAX_GROUP_CONCURRENCY, tasks.length) },
    () => worker(),
  ));

  for (let index = 0; index < tasks.length; index += 1) {
    if (groups[index]) continue;
    deadlineExceeded = true;
    timedOutDomains.add(tasks[index].definition.label);
    groups[index] = incompleteGroup(tasks[index].definition, tasks[index].available);
  }

  return {
    groups: groups as GlobalSearchGroup[],
    deadlineExceeded,
    timedOutDomains: [...timedOutDomains],
  };
}

function trimToTotalLimit(groups: GlobalSearchGroup[], totalLimit: number) {
  let remaining = totalLimit;
  return groups.map((group) => {
    if (group.items.length <= remaining) {
      remaining -= group.items.length;
      return group;
    }
    const items = group.items.slice(0, Math.max(0, remaining));
    remaining = 0;
    return { ...group, items, hasMore: group.hasMore || items.length < group.items.length };
  });
}

export async function searchAllBusinessData(
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  options: GlobalSearchExecutionOptions = {},
): Promise<GlobalSearchResponse> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  try {
    if (controller.signal.aborted) throw new Error("Search cancelled");
    return await searchDjangoBusinessData(request, principal, { ...options, signal: controller.signal });
  } finally {
    // Cancel any in-flight reads when the bounded scheduler returns a partial
    // response; a timeout must not leave abandoned PostgreSQL work queued.
    abort();
    options.signal?.removeEventListener("abort", abort);
  }
}

async function searchDjangoBusinessData(
  request: GlobalSearchRequest,
  principal: AppPrincipal,
  options: GlobalSearchExecutionOptions = {},
): Promise<GlobalSearchResponse> {
  const salesReader = options.salesReader ?? createDjangoSalesConsumerReader();
  const financeReader = options.financeReader ?? createDjangoFinanceConsumerReader();
  const netshopReader = options.netshopReader ?? createDjangoNetshopConsumerReader();
  const productsReader = options.productsReader ?? createDjangoProductsConsumerReader();
  const inventoryReader = options.inventoryReader ?? createDjangoInventoryConsumerReader();
  const workflowReader = options.workflowReader ?? createDjangoWorkflowConsumerReader();
  const customerServiceReader = options.customerServiceReader ?? createDjangoCustomerServiceConsumerReader();
  const erpReferenceReader = options.erpReferenceReader ?? createDjangoErpReferenceConsumerReader();
  const marketReader = options.marketReader ?? createDjangoMarketConsumerReader();
  const selectedStaticDefinitions = staticDefinitions.filter((definition) =>
    (!request.group || definition.key === request.group) && isGroupAuthorized(definition, principal));
  const groupTasks: SearchGroupTask[] = selectedStaticDefinitions.map((definition) => {
    if (definition.key === "products" || definition.key === "combos") {
      return {
        definition,
        available: true,
        run: () => queryErpGroup(
          erpReferenceReader,
          definition.key === "products" ? "product_search" : "combo_search",
          definition,
          request,
          principal,
          options.signal,
        ),
      };
    }
    if (definition.key === "orders") {
      return {
        definition: salesOrderDefinition,
        available: true,
        run: () => querySalesOrderGroup(salesReader, request, principal, options.signal),
      };
    }
    if (definition.key === "jd_products") {
      return {
        definition,
        available: true,
        run: () => queryNetshopSearchGroup(
          netshopReader,
          definition,
          request,
          principal,
          options.signal,
        ),
      };
    }
    if (definition.key === "inventory") {
      return {
        definition: inventorySearchDefinition,
        available: true,
        run: () => queryInventorySearchGroup(
          inventoryReader,
          "inventory_search",
          inventorySearchDefinition,
          request,
          principal,
          options.signal,
        ),
      };
    }
    if (definition.key === "replenishment") {
      return {
        definition: replenishmentSearchDefinition,
        available: true,
        run: () => queryInventorySearchGroup(
          inventoryReader,
          "replenishment_search",
          replenishmentSearchDefinition,
          request,
          principal,
          options.signal,
        ),
      };
    }
    if (definition.key === "customer_service") {
      return {
        definition: customerServiceSearchDefinition,
        available: true,
        run: () => queryCustomerServiceGroup(
          request,
          principal,
          customerServiceReader,
          options.signal,
        ),
      };
    }
    if (definition.key === "finance") {
      return {
        definition: financeSearchDefinition,
        available: true,
        run: () => queryFinanceSearchGroup(
          financeReader,
          "line_search",
          financeSearchDefinition,
          request,
          principal,
          options.signal,
        ),
      };
    }
    if (definition.key === "targets") {
      return {
        definition: targetSearchDefinition,
        available: true,
        run: () => queryFinanceSearchGroup(
          financeReader,
          "target_search",
          targetSearchDefinition,
          request,
          principal,
          options.signal,
        ),
      };
    }
    if (definition.key === "market_skus" || definition.key === "market_annotations") {
      return {
        definition, available: true,
        run: () => queryMarketSearchGroup(marketReader,
          definition.key === "market_skus" ? "sku_search" : "annotation_search",
          definition, request, principal, options.signal),
      };
    }
    throw new Error(`Unmapped search group: ${definition.key}`);
  });
  if ((!request.group || request.group === "workflow") && allRoles.includes(principal.role)) {
    groupTasks.push({
      definition: workflowDefinition,
      available: true,
      run: () => queryWorkflowGroup(
        request,
        principal,
        workflowReader,
        options.signal,
      ),
    });
  }
  if ((!request.group || request.group === "inventory_age") && allRoles.includes(principal.role)) {
    groupTasks.push({
      definition: inventoryAgeDefinition,
      available: true,
      run: () => queryInventorySearchGroup(
        inventoryReader,
        "age_search",
        inventoryAgeDefinition,
        request,
        principal,
        options.signal,
      ),
    });
  }
  if ((!request.group || request.group === "imports") && operatorRoles.some((role) => role === principal.role) && principal.scope === null) {
    groupTasks.push({
      definition: importDefinition,
      available: true,
      run: () => queryImportGroup(
        request,
        principal,
        salesReader,
        financeReader,
        netshopReader,
        productsReader,
        inventoryReader,
        customerServiceReader,
        erpReferenceReader,
        marketReader,
        options.signal,
      ),
    });
  }
  const execution = await runBoundedGroupTasks(groupTasks, boundedGroupDeadlineMs(options.deadlineMs));
  const groups = trimToTotalLimit(execution.groups, request.totalLimit);
  const returned = groups.reduce((sum, group) => sum + group.items.length, 0);
  const latestMatchedDate = groups
    .flatMap((group) => group.items.map((item) => item.updatedAt).filter(Boolean))
    .sort((left, right) => right.localeCompare(left))[0] ?? null;
  return {
    query: request.query,
    page: request.page,
    limits: { perGroup: request.groupLimit, total: request.totalLimit },
    returned,
    dataCutoffDate: latestMatchedDate,
    filtersApplied: {
      query: request.query,
      group: request.group,
      page: request.page,
      perGroupLimit: request.groupLimit,
      totalLimit: request.totalLimit,
      dataScope: principal.scope === null
        ? { mode: "unrestricted", warehouses: null, channels: null, platforms: null }
        : {
            mode: "restricted",
            warehouses: [...principal.scope.warehouses],
            channels: [...principal.scope.channels],
            platforms: [...principal.scope.platforms],
          },
    },
    truncated: execution.deadlineExceeded || groups.some((group) => group.hasMore) || returned >= request.totalLimit,
    deadlineExceeded: execution.deadlineExceeded,
    timedOutDomains: execution.timedOutDomains,
    groups,
    coveredDomains: [...GLOBAL_SEARCH_COVERAGE],
    unavailableDomains: groups.filter((group) => !group.available).map((group) => group.label),
  };
}
