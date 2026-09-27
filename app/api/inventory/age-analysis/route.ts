import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_AGE_ANALYSIS_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  InventoryQueryContractError,
  inventoryAgeBuckets,
  normalizeInventorySelections,
  parseInventoryPaginationParameter,
} from "@/lib/inventory/query-contract";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "库龄分析数据");
    const params = new URL(request.url).searchParams;
    const allowed = new Set(["cardFilter", "q", "warehouse", "brand", "category", "status", "ageBucket", "page", "pageSize"]);
    if ([...params.keys()].some((key) => !allowed.has(key))) throw new InventoryQueryContractError("库龄分析包含未知查询参数");
    if (params.getAll("cardFilter").length > 1 || (params.get("cardFilter") && !["stagnant", "aged90", "zero_sales"].includes(params.get("cardFilter")!))) throw new InventoryQueryContractError("统计卡片筛选无效");
    const query = params.get("q")?.trim();
    if (query && query.length > 100) throw new InventoryQueryContractError("搜索词不能超过 100 个字符");
    parseInventoryPaginationParameter(params.get("page"), "page");
    parseInventoryPaginationParameter(params.get("pageSize"), "pageSize");
    normalizeInventorySelections(params.getAll("warehouse"), { maximum: 10, label: "仓库" });
    normalizeInventorySelections(params.getAll("brand"), { maximum: 20, label: "品牌" });
    normalizeInventorySelections(params.getAll("category"), { maximum: 20, label: "品类" });
    normalizeInventorySelections(params.getAll("status"), {
      maximum: 5, allowed: ["healthy", "aged", "slow", "stagnant", "no_stock"], label: "库龄状态",
    });
    normalizeInventorySelections(params.getAll("ageBucket"), {
      maximum: inventoryAgeBuckets.length,
      allowed: inventoryAgeBuckets.map((bucket) => bucket.key),
      label: "库龄区间",
    });
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_AGE_ANALYSIS_PATH, service: "reader", rawQuery: params.toString() },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    if (error instanceof InventoryQueryContractError) {
      return Response.json({ error: error.message }, { status: 400, headers: { "cache-control": "no-store" } });
    }
    return safeApiErrorResponse(error, "读取库龄分析数据失败", { headers: { "cache-control": "no-store" } });
  }
}
