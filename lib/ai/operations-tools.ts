import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
} from "@/lib/django/sales-consumer-reader";
import {
  createDjangoProductsConsumerReader,
  type ProductsConsumerReader,
} from "@/lib/django/products-consumer-reader";
import {
  createDjangoInventoryConsumerReader,
  type InventoryConsumerReader,
} from "@/lib/django/inventory-consumer-reader";
import {
  isSalesRange,
} from "@/lib/sales/read-contract";
import { getOperationsBusinessDates } from "@/lib/ai/business-time";
import { PublicApiError } from "@/lib/http/api-error";

type OperationsToolDependencies = {
  salesReader?: SalesConsumerReader;
  productsReader?: ProductsConsumerReader;
  inventoryReader?: InventoryConsumerReader;
  signal?: AbortSignal;
};

function salesConsumerUnavailable(): PublicApiError {
  return new PublicApiError(503, "service_unavailable", "Django 销售读取服务暂时不可用，请稍后重试。");
}

function validTextOrNull(value: unknown, maximum = 500): value is string | null {
  return value === null || (typeof value === "string" && value.length <= maximum);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validFreshnessData(value: unknown): boolean {
  if (!isRecord(value) || !validTextOrNull(value.dataStartDate, 10)
    || !validTextOrNull(value.dataCutoffDate, 10)) return false;
  if (value.latestBatch === null) return true;
  if (!isRecord(value.latestBatch)) return false;
  return typeof value.latestBatch.id === "string" && value.latestBatch.id.length <= 200
    && typeof value.latestBatch.fileName === "string" && value.latestBatch.fileName.length <= 500
    && validTextOrNull(value.latestBatch.completedAt, 80)
    && Number.isSafeInteger(value.latestBatch.rowCount) && Number(value.latestBatch.rowCount) >= 0;
}

function validSummaryData(value: unknown): boolean {
  if (!isRecord(value) || typeof value.range !== "string" || !isSalesRange(value.range)
    || typeof value.startDate !== "string" || typeof value.endDate !== "string"
    || !validTextOrNull(value.dataCutoffDate, 10) || !isRecord(value.current)) return false;
  return ["channels", "outlets", "shops", "platforms", "daily", "previousDaily", "yearAgoDaily"]
    .every((key) => Array.isArray(value[key]));
}

function customSalesPeriod(startDate: string | undefined, endDate: string | undefined) {
  const parse = (value: string | undefined): Date => {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new ToolInputError("custom 必须提供 YYYY-MM-DD 格式的 startDate 和 endDate。");
    }
    const parsed = new Date(`${value}T00:00:00Z`);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value
      || value < "1900-01-01" || value > "2199-12-31") {
      throw new ToolInputError("销售查询日期无效。");
    }
    return parsed;
  };
  const start = parse(startDate);
  const end = parse(endDate);
  const inclusiveDays = (end.getTime() - start.getTime()) / 86_400_000 + 1;
  if (inclusiveDays < 1 || inclusiveDays > 366) {
    throw new ToolInputError("销售查询开始日期不能晚于结束日期，且最多包含 366 天。");
  }
  // AI/page dates include the selected final business day. The internal sales
  // consumer alone expects an exclusive end; do not change its shared contract.
  end.setUTCDate(end.getUTCDate() + 1);
  return { startDate, endDate: end.toISOString().slice(0, 10) };
}

export async function callOperationsTool(
  name: string,
  rawArguments: unknown,
  principal: AppPrincipal,
  dependencies: OperationsToolDependencies = {},
): Promise<Record<string, unknown>> {
  const args = asRecord(rawArguments);
  const salesReader = dependencies.salesReader ?? createDjangoSalesConsumerReader();
  const productsReader = dependencies.productsReader ?? createDjangoProductsConsumerReader();
  const inventoryReader = dependencies.inventoryReader ?? createDjangoInventoryConsumerReader();

  if (name === "get_data_freshness") {
    assertOnlyKeys(args, []);
    const [sales, inventory] = await Promise.all([
      salesReader.read(
        principal,
        { operation: "freshness" },
        { signal: dependencies.signal },
      ),
      inventoryReader.read(
        principal,
        { operation: "freshness" },
        { signal: dependencies.signal },
      ),
    ]);
    if (!sales || typeof sales.revision !== "string" || !sales.revision
      || !validFreshnessData(sales.data)) {
      throw salesConsumerUnavailable();
    }
    const businessDates = getOperationsBusinessDates();
    return {
      sales: {
        through: sales.data.dataCutoffDate,
        importedAt: sales.data.latestBatch?.completedAt ?? null,
        fileName: sales.data.latestBatch?.fileName ?? null,
      },
      inventory: {
        asOf: inventory.data.stock?.snapshotDate ?? null,
        importedAt: inventory.data.stock?.completedAt ?? null,
        fileName: inventory.data.stock?.fileName ?? null,
      },
      timezone: businessDates.timeZone,
      currentBusinessDate: businessDates.today,
      yesterdayBusinessDate: businessDates.yesterday,
    };
  }

  if (name === "get_sales_summary") {
    assertOnlyKeys(args, ["range", "startDate", "endDate"]);
    const requestedRange = optionalString(args.range) ?? "month";
    if (!isSalesRange(requestedRange)) throw new ToolInputError("range 参数无效");
    const startDate = optionalString(args.startDate);
    const endDate = optionalString(args.endDate);
    const period = requestedRange === "custom"
      ? customSalesPeriod(startDate, endDate)
      : { startDate, endDate };
    const summary = await salesReader.read(principal, {
      operation: "summary",
      range: requestedRange,
      ...period,
    }, { signal: dependencies.signal });
    if (!summary || typeof summary.revision !== "string" || !summary.revision
      || !validSummaryData(summary.data)) throw salesConsumerUnavailable();
    return { ...summary.data, currency: "CNY", monetaryUnit: "cents" };
  }

  if (name === "get_inventory_health") {
    assertOnlyKeys(args, ["warehouse", "category", "status", "query", "limit"]);
    const status = optionalEnum(args.status, ["no_stock", "urgent", "warning", "stale", "slow", "healthy"] as const);
    const warehouse = optionalString(args.warehouse);
    const category = optionalString(args.category);
    const query = optionalString(args.query);
    const limit = integer(args.limit, 20, 1, 100);
    const overview = await inventoryReader.read(principal, {
      operation: "inventory_health",
      warehouse: warehouse ?? null,
      category: category ?? null,
      status: status ?? null,
      query: query ?? null,
      limit,
    }, { signal: dependencies.signal });
    return overview.data;
  }

  if (name === "get_product_performance") {
    assertOnlyKeys(args, ["days", "category", "query", "sortBy", "direction", "limit"]);
    const days = integer(args.days, 30, 7, 365);
    const category = optionalString(args.category);
    const query = optionalString(args.query);
    const sortBy = optionalEnum(args.sortBy, ["netSalesCents", "grossProfitCents", "grossMarginRate", "stockValueCents", "netQuantity"] as const) ?? "netSalesCents";
    const direction = optionalEnum(args.direction, ["asc", "desc"] as const) ?? "desc";
    const limit = integer(args.limit, 20, 1, 100);
    const summary = await productsReader.read(principal, {
      operation: "product_performance",
      days,
      category: category ?? null,
      query: query ?? null,
      sortBy,
      direction,
      limit,
    }, { signal: dependencies.signal });
    return summary.data;
  }

  if (name !== "list_replenishment_plans") throw new ToolInputError("工具未注册");
  assertOnlyKeys(args, ["status", "warehouse", "query", "limit"]);
  const status = optionalEnum(args.status, ["draft", "confirmed", "completed", "cancelled"] as const);
  const warehouse = optionalString(args.warehouse);
  const query = optionalString(args.query);
  const limit = integer(args.limit, 20, 1, 100);
  const plans = await inventoryReader.read(principal, {
    operation: "replenishment_search",
    offset: 0,
    limit,
    status: status ?? null,
    warehouse: warehouse ?? null,
    query: query ?? "",
  }, { signal: dependencies.signal });
  return {
    filtersApplied: { status, warehouse, query: query ?? null },
    totalMatched: plans.data.total,
    returned: plans.data.items.length,
    truncated: plans.data.truncated,
    items: plans.data.items,
  };
}

export class ToolInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolInputError";
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ToolInputError("工具参数必须是 JSON 对象");
  }
  return value as Record<string, unknown>;
}

function assertOnlyKeys(record: Record<string, unknown>, allowed: readonly string[]) {
  const unexpected = Object.keys(record).filter((key) => !allowed.includes(key));
  if (unexpected.length > 0) {
    throw new ToolInputError(`不支持的参数：${unexpected.join(", ")}`);
  }
}

function optionalString(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") throw new ToolInputError("字符串参数格式无效");
  const normalized = value.trim();
  if (normalized.length > 200) throw new ToolInputError("字符串参数过长");
  return normalized || undefined;
}

function optionalEnum<const T extends readonly string[]>(
  value: unknown,
  allowed: T,
): T[number] | undefined {
  const normalized = optionalString(value);
  if (normalized === undefined) return undefined;
  if (!allowed.includes(normalized)) throw new ToolInputError(`参数必须是 ${allowed.join(", ")} 之一`);
  return normalized as T[number];
}

function integer(value: unknown, fallback: number, minimum: number, maximum: number) {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new ToolInputError(`整数参数必须在 ${minimum} 到 ${maximum} 之间`);
  }
  return value;
}
