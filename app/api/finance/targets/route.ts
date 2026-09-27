import type { FinanceTargetInput, FinanceTargetPeriodType } from "@/lib/finance/types";
import { requireDjangoRecord } from "@/lib/django/response-contract";
import {
  createDjangoFinanceService,
  FINANCE_TARGETS_PATH,
} from "@/lib/django/finance-service";
import { createDjangoSalesConsumerReader } from "@/lib/django/sales-consumer-reader";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { parsePositiveIntegerQuery, PublicApiError, requirePositiveSafeIntegerNumber, safeApiErrorResponse } from "@/lib/http/api-error";

const MAX_FINANCE_TARGET_AMOUNT_CENTS = 10_000_000_000_000;
type FinanceTargetReadView = "full" | "items" | "options" | "annual";

function readFinanceTargetView(values: string[]): FinanceTargetReadView {
  if (values.length === 0) return "full";
  if (values.length !== 1 || !(["full", "items", "options", "annual"] as const).includes(values[0] as FinanceTargetReadView)) {
    throw new PublicApiError(400, "invalid_request", "view 必须且只能是 full、items、options 或 annual");
  }
  return values[0] as FinanceTargetReadView;
}

function finiteInteger(value: unknown, field: string, maximum = MAX_FINANCE_TARGET_AMOUNT_CENTS) {
  const number = value === undefined ? 0 : value;
  if (typeof number !== "number" || !Number.isSafeInteger(number) || number < 0 || number > maximum) {
    throw new PublicApiError(400, "invalid_request", `${field}必须是范围内的 JSON 非负安全整数`);
  }
  return number;
}

function parseTarget(body: Record<string, unknown>): FinanceTargetInput {
  const periodType = String(body.periodType ?? "") as FinanceTargetPeriodType;
  const periodKey = String(body.periodKey ?? "").trim();
  if (!(["month", "year", "project"] as const).includes(periodType)) throw new PublicApiError(400, "invalid_request", "目标周期类型无效");
  if (periodType === "month" && !/^\d{4}-(0[1-9]|1[0-2])$/.test(periodKey)) throw new PublicApiError(400, "invalid_request", "月度目标周期应为真实的 YYYY-MM");
  if (periodType === "year" && !/^(19|20|21)\d{2}$/.test(periodKey)) throw new PublicApiError(400, "invalid_request", "年度目标周期应为 YYYY");
  if (periodType === "project" && (!periodKey || periodKey.length > 100)) throw new PublicApiError(400, "invalid_request", "请输入不超过 100 字的项目名称");
  const shopName = String(body.shopName ?? "").trim();
  const platform = String(body.platform ?? "").trim();
  if (periodType !== "project" && (!shopName || shopName.length > 100)) throw new PublicApiError(400, "invalid_request", "月度或年度目标必须选择有效店铺");
  if (periodType !== "project" && (!platform || platform.length > 100)) throw new PublicApiError(400, "invalid_request", "月度或年度目标必须选择有效平台");
  if (body.id !== undefined && typeof body.id !== "string") throw new PublicApiError(400, "invalid_request", "目标 ID 必须为字符串");
  const id = typeof body.id === "string" ? body.id.trim() : "";
  if (id.length > 128) throw new PublicApiError(400, "invalid_request", "目标 ID 长度超出限制");
  if (!id && body.expectedVersion !== undefined) throw new PublicApiError(400, "invalid_request", "新建目标不能携带 expectedVersion");
  const expectedVersion = id ? requirePositiveSafeIntegerNumber(body.expectedVersion, "expectedVersion") : undefined;
  const category = periodType === "project" ? "" : String(body.category ?? "").trim();
  const manager = String(body.manager ?? "").trim();
  if (category.length > 100) throw new PublicApiError(400, "invalid_request", "品类名称不能超过 100 字");
  if (manager.length > 120) throw new PublicApiError(400, "invalid_request", "负责人不能超过 120 字");
  return {
    id: id || undefined,
    expectedVersion,
    periodType,
    periodKey,
    platform: periodType === "project" ? "" : platform,
    shopName: periodType === "project" ? "" : shopName,
    category,
    manager,
    salesTargetCents: finiteInteger(body.salesTargetCents, "销售额目标"),
    profitTargetCents: finiteInteger(body.profitTargetCents, "利润目标"),
    grossMarginBps: finiteInteger(body.grossMarginBps, "大毛利率目标", 10_000),
    smallMarginBps: finiteInteger(body.smallMarginBps, "小毛利率目标", 10_000),
    inventoryCleanupTargetCents: finiteInteger(body.inventoryCleanupTargetCents, "库存清理目标"),
    promotionFeeRatioBps: finiteInteger(body.promotionFeeRatioBps, "推广费占比目标", 10_000),
    stagnantInventoryTargetCents: finiteInteger(body.stagnantInventoryTargetCents, "呆滞库存目标"),
  };
}

async function getDjangoTargets(
  principal: Awaited<ReturnType<typeof requireAppPrincipal>>,
  params: URLSearchParams,
  signal: AbortSignal,
) {
  const view = readFinanceTargetView(params.getAll("view"));
  if (view === "annual") {
    const result = await createDjangoFinanceService().request<Record<string, unknown>>(
      principal, { method: "GET", path: FINANCE_TARGETS_PATH, query: params, service: "reader" }, { signal },
    );
    return requireDjangoRecord(result.data);
  }
  if (view === "items") {
    const result = await createDjangoFinanceService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: FINANCE_TARGETS_PATH, query: params, service: "reader" },
      { signal },
    );
    return requireDjangoRecord(result.data);
  }
  const [financeResult, salesCategories] = await Promise.all([
    createDjangoFinanceService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: FINANCE_TARGETS_PATH, query: params, service: "reader" },
      { signal },
    ),
    createDjangoSalesConsumerReader().read(
      principal,
      { operation: "category_options", limit: 300 },
      { signal },
    ),
  ]);
  const payload = requireDjangoRecord(financeResult.data);
  const financeOptions = requireDjangoRecord(payload.financeOptions);
  if (!Array.isArray(financeOptions.shops) || !Array.isArray(financeOptions.projects)
    || !financeOptions.shops.every((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const row = item as Record<string, unknown>;
      return typeof row.key === "string" && typeof row.platform === "string" && typeof row.name === "string";
    })
    || !salesCategories || typeof salesCategories.revision !== "string" || !salesCategories.revision
    || !salesCategories.data || !Array.isArray(salesCategories.data.categories)
    || typeof salesCategories.data.truncated !== "boolean"
    || salesCategories.data.categories.some((item) => typeof item !== "string" || item.length > 200)) {
    throw new PublicApiError(503, "service_unavailable", "Django 财务读取服务暂时不可用，请稍后重试。");
  }
  const base = { ...payload };
  delete base.financeOptions;
  return {
    ...base,
    options: {
      ...financeOptions,
      categories: salesCategories.data.categories,
    },
  };
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "经营目标");
    const params = new URL(request.url).searchParams;
    readFinanceTargetView(params.getAll("view"));
    parsePositiveIntegerQuery(params.get("page"), 1, "page", 10_000);
    parsePositiveIntegerQuery(params.get("pageSize"), 50, "pageSize", 100);
    return Response.json(await getDjangoTargets(principal, params, request.signal), { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "目标进度情况读取失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "经营目标", "修改");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body) return Response.json({ error: "请求内容不是有效 JSON" }, { status: 400, headers: { "cache-control": "no-store" } });
    const input = parseTarget(body);
    const result = await createDjangoFinanceService().request<Record<string, unknown>>(
      principal,
      {
        method: "POST",
        path: FINANCE_TARGETS_PATH,
        payload: input as unknown as Record<string, unknown>,
        service: "writer",
      },
      { signal: request.signal },
    );
    const headers = new Headers({ "cache-control": "no-store" });
    if (result.replayed) headers.set("x-teruisi-write-replay", "1");
    return Response.json(result.data, { status: result.status, headers });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "目标保存失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function DELETE(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "经营目标", "删除");
    const params = new URL(request.url).searchParams;
    const id = params.get("id")?.trim();
    if (!id) return Response.json({ error: "缺少目标 ID" }, { status: 400, headers: { "cache-control": "no-store" } });
    const expectedVersionValue = params.get("expectedVersion");
    if (expectedVersionValue === null) throw new PublicApiError(400, "invalid_request", "缺少 expectedVersion");
    parsePositiveIntegerQuery(expectedVersionValue, 1, "expectedVersion");
    const result = await createDjangoFinanceService().request<Record<string, unknown>>(
      principal,
      { method: "DELETE", path: FINANCE_TARGETS_PATH, query: params, service: "writer" },
      { signal: request.signal },
    );
    const headers = new Headers({ "cache-control": "no-store" });
    if (result.replayed) headers.set("x-teruisi-write-replay", "1");
    return Response.json(result.data, { status: result.status, headers });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "目标删除失败。", { headers: { "cache-control": "no-store" } });
  }
}
