import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_REPLENISHMENT_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  InventoryQueryContractError,
  parseInventoryPaginationParameter,
} from "@/lib/inventory/query-contract";

function errorResponse(status: number, message: string) {
  return Response.json({ ok: false, message }, { status, headers: { "cache-control": "no-store" } });
}

const planTextLimits = {
  buyer: 200,
  operatorName: 200,
  department: 200,
  planType: 100,
  notes: 1_000,
} as const;

function validOptionalDate(value: unknown) {
  if (value === undefined || value === "") return true;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function validOptionalConsumptionDays(value: unknown) {
  if (value === undefined || value === null) return true;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 3_650) return false;
  return Math.abs(value * 10 - Math.round(value * 10)) < 1e-9;
}
export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划");
    const params = new URL(request.url).searchParams;
    if ([...params.keys()].some((key) => !["status", "includeCancelled", "q", "page", "pageSize"].includes(key))) {
      throw new InventoryQueryContractError("备货计划包含未知查询参数");
    }
    const status = params.get("status");
    if (status !== null && !["draft", "confirmed", "completed", "cancelled"].includes(status)) {
      throw new InventoryQueryContractError("备货计划状态不在允许清单中");
    }
    const includeCancelled = params.get("includeCancelled");
    if (includeCancelled !== null && includeCancelled !== "true" && includeCancelled !== "false") {
      throw new InventoryQueryContractError("includeCancelled 必须是 true 或 false");
    }
    const query = params.get("q")?.trim();
    if (query && query.length > 100) throw new InventoryQueryContractError("搜索词不能超过 100 个字符");
    parseInventoryPaginationParameter(params.get("page"), "page");
    parseInventoryPaginationParameter(params.get("pageSize"), "pageSize");
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_REPLENISHMENT_PATH, service: "reader", rawQuery: params.toString() },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof InventoryQueryContractError) return errorResponse(400, error.message);
    return safeApiErrorResponse(error, "读取备货计划失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划", "修改");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body || typeof body.key !== "string" || !body.key.trim()) return errorResponse(400, "缺少库存建议标识");
    if (Object.keys(body).some((key) => ![
      "key", "plannedQuantity", "acknowledgeStale", "manual", "startDate", "endDate", "buyer",
      "operatorName", "department", "planType", "orderDate", "expectedArrivalDate",
      "expectedConsumptionDays", "status", "requiresInspection", "notes",
    ].includes(key))) {
      return errorResponse(400, "创建备货计划包含未知字段");
    }
    const parts = body.key.split("\u001f");
    if (parts.length !== 2 || parts.some((value) => !value.trim() || value.length > 100)) {
      return errorResponse(400, "库存建议标识必须精确包含仓库与货品编码");
    }
    if (body.plannedQuantity !== undefined
      && (!Number.isSafeInteger(body.plannedQuantity) || Number(body.plannedQuantity) < 1 || Number(body.plannedQuantity) > 10_000_000)) {
      return errorResponse(400, "计划补货量必须是 1 到 10,000,000 之间的整数");
    }
    if (body.acknowledgeStale !== undefined && typeof body.acknowledgeStale !== "boolean") {
      return errorResponse(400, "acknowledgeStale 必须是 JSON 布尔值");
    }
    if (body.manual !== undefined && typeof body.manual !== "boolean") {
      return errorResponse(400, "manual 必须是 JSON 布尔值");
    }
    for (const [key, maximum] of Object.entries(planTextLimits)) {
      const value = body[key];
      if (value !== undefined && (typeof value !== "string" || value.length > maximum)) {
        return errorResponse(400, `${key} 字段无效`);
      }
    }
    if (!validOptionalDate(body.orderDate) || !validOptionalDate(body.expectedArrivalDate)) {
      return errorResponse(400, "备货计划日期无效");
    }
    if (!validOptionalConsumptionDays(body.expectedConsumptionDays)) {
      return errorResponse(400, "预计消耗周期必须是 0 到 3,650 天之间、最多一位小数的数字");
    }
    if (body.status !== undefined && body.status !== "draft" && body.status !== "confirmed") {
      return errorResponse(400, "新建备货计划状态只能是草稿或已确认");
    }
    if (body.requiresInspection !== undefined && typeof body.requiresInspection !== "boolean") {
      return errorResponse(400, "是否验货必须是 JSON 布尔值");
    }
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "POST", path: INVENTORY_REPLENISHMENT_PATH, service: "writer", payload: body },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "创建备货计划失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function PATCH(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划", "修改");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body || typeof body.id !== "string" || !body.id.trim() || body.id.length > 128) return errorResponse(400, "缺少有效的备货计划 ID");
    if (Object.keys(body).some((key) => !["id", "status", "plannedQuantity"].includes(key))
      || typeof body.status !== "string" || !["draft", "confirmed", "completed", "cancelled"].includes(body.status)) {
      return errorResponse(400, "备货计划更新内容无效");
    }
    if (body.plannedQuantity !== undefined
      && (!Number.isSafeInteger(body.plannedQuantity) || Number(body.plannedQuantity) < 1 || Number(body.plannedQuantity) > 10_000_000)) {
      return errorResponse(400, "计划补货量必须是 1 到 10,000,000 之间的整数");
    }
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "PATCH", path: INVENTORY_REPLENISHMENT_PATH, service: "writer", payload: body },
      { signal: request.signal },
    );
    return Response.json(result.data, { status: result.status, headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "更新备货计划失败。", { headers: { "cache-control": "no-store" } });
  }
}
