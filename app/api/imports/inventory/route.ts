import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_IMPORTS_PATH,
} from "@/lib/django/inventory-service";
import { withCurrentInventoryBatchOwnership } from "@/lib/django/inventory-batch-ownership";
import { importExecutionHttpStatus, parsePositiveIntegerQuery, safeApiErrorResponse } from "@/lib/http/api-error";
import { importInventoryStockToDjango } from "@/lib/inventory/django-import-service";
import { syncLatestInventoryProjection } from "@/lib/products/inventory-projection-sync";

const MAX_DIRECT_INVENTORY_FILE_BYTES = 1024 * 1024;

function errorResponse(status: number, message: string, details: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...details }, { status, headers: { "cache-control": "no-store" } });
}
export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "库存导入历史");
    const incoming = new URL(request.url).searchParams;
    if ([...incoming.keys()].some((key) => !["batchId", "page", "pageSize", "limit"].includes(key))) {
      return errorResponse(400, "库存导入历史包含未知查询参数");
    }
    const batchId = incoming.get("batchId")?.trim() ?? "";
    if (batchId && !/^[a-f0-9]{64}$/i.test(batchId)) return errorResponse(400, "batchId 格式无效");
    const paged = incoming.has("page") || incoming.has("pageSize");
    const page = parsePositiveIntegerQuery(paged ? incoming.get("page") : null, 1, "page", 10_000);
    const pageSize = parsePositiveIntegerQuery(paged ? incoming.get("pageSize") : incoming.get("limit"), 20, paged ? "pageSize" : "limit", 100);
    const query = new URLSearchParams({ dataset: "stock", page: String(page), pageSize: String(pageSize) });
    if (batchId) query.set("batchId", batchId);
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: INVENTORY_IMPORTS_PATH, service: "reader", rawQuery: query.toString() },
      { signal: request.signal },
    );
    const data = await withCurrentInventoryBatchOwnership(principal, "stock", batchId, result, { signal: request.signal });
    return Response.json(data, { headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取库存同步历史失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "库存数据", "导入");
    const contentType = request.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("multipart/form-data")) return errorResponse(415, "请使用 multipart/form-data 上传 .xlsx 文件");
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > MAX_DIRECT_INVENTORY_FILE_BYTES + 256 * 1024) return errorResponse(413, "超过 1MB 的库存报表请使用分片上传接口");
    const formData = await request.formData().catch(() => null);
    const entry = formData?.get("file");
    const snapshotDate = typeof formData?.get("snapshotDate") === "string" ? String(formData?.get("snapshotDate")) : undefined;
    if (!(entry instanceof File)) return errorResponse(400, "缺少名为 file 的 Excel 文件");
    if (!entry.name.toLowerCase().endsWith(".xlsx")) return errorResponse(400, "仅支持 .xlsx 格式的分仓库存查询报表");
    if (entry.size === 0) return errorResponse(400, "上传文件为空");
    if (entry.size > MAX_DIRECT_INVENTORY_FILE_BYTES) return errorResponse(413, "超过 1MB 的库存报表请使用分片上传接口");
    const payload = await importInventoryStockToDjango({
      principal,
      bytes: new Uint8Array(await entry.arrayBuffer()),
      fileName: entry.name,
      fileSizeBytes: entry.size,
      snapshotDateOverride: snapshotDate,
    }, { signal: request.signal });
    const result = payload.ok
      ? { ...payload, inventoryProjection: await syncLatestInventoryProjection(principal, { signal: request.signal }) }
      : payload;
    return Response.json(result, { status: importExecutionHttpStatus(result), headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "库存数据同步失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
