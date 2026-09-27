import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_OVERVIEW_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  InventoryQueryContractError,
  normalizeInventorySelections,
  parseInventoryOverviewView,
  parseInventoryPaginationParameter,
} from "@/lib/inventory/query-contract";

const allowedKeys = new Set([
  "view", "startDate", "endDate", "q", "warehouse", "brand", "category",
  "warehouseType", "status", "page", "pageSize", "planPage", "planPageSize",
  "planStatus", "includeCancelledPlans",
]);

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "库存健康数据");
    const params = new URL(request.url).searchParams;
    if ([...params.keys()].some((key) => !allowedKeys.has(key))) {
      throw new InventoryQueryContractError("库存总览包含未知查询参数");
    }
    parseInventoryOverviewView(params);
    for (const field of ["page", "pageSize", "planPage", "planPageSize"] as const) {
      parseInventoryPaginationParameter(params.get(field), field.endsWith("Size") ? "pageSize" : "page");
    }
    const query = params.get("q")?.trim();
    if (query && query.length > 100) throw new InventoryQueryContractError("搜索词不能超过 100 个字符");
    normalizeInventorySelections(params.getAll("warehouse"), { maximum: 10, label: "仓库" });
    normalizeInventorySelections(params.getAll("brand"), { maximum: 20, label: "品牌" });
    normalizeInventorySelections(params.getAll("category"), { maximum: 20, label: "品类" });
    normalizeInventorySelections(params.getAll("warehouseType"), {
      maximum: 3, allowed: ["owned", "jd_rdc", "other"], label: "仓库类型",
    });
    normalizeInventorySelections(params.getAll("status"), {
      maximum: 6,
      allowed: ["no_stock", "urgent", "warning", "stale", "slow", "healthy"],
      label: "库存状态",
    });
    const planStatus = params.get("planStatus");
    if (planStatus !== null && !["draft", "confirmed", "completed", "cancelled"].includes(planStatus)) {
      throw new InventoryQueryContractError("planStatus 必须是 draft、confirmed、completed 或 cancelled");
    }
    const includeCancelled = params.get("includeCancelledPlans");
    if (includeCancelled !== null && includeCancelled !== "true" && includeCancelled !== "false") {
      throw new InventoryQueryContractError("includeCancelledPlans 必须是 true 或 false");
    }
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_OVERVIEW_PATH, service: "reader", rawQuery: params.toString() },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof InventoryQueryContractError) {
      return Response.json({ error: error.message }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    return safeApiErrorResponse(error, "读取库存健康数据失败", { headers: { "cache-control": "no-store" } });
  }
}
