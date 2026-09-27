import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { importErpReferenceToDjango } from "@/lib/erp-reference/django-import-service";
import { importInventoryAgeToDjango } from "@/lib/inventory/django-age-import-service";
import { isErpReferenceSourceKey } from "@/lib/imports/erp-reference";
import { importExecutionHttpStatus, parsePositiveIntegerQuery, safeApiErrorResponse } from "@/lib/http/api-error";
import {
  createDjangoInventoryService,
  INVENTORY_IMPORTS_PATH,
} from "@/lib/django/inventory-service";
import { withCurrentInventoryBatchOwnership } from "@/lib/django/inventory-batch-ownership";
import {
  createDjangoErpReferenceService,
  ERP_REFERENCE_IMPORTS_PATH,
} from "@/lib/django/erp-reference-service";
import { reconcileNewProductCodesAfterImport } from "@/lib/workflow/new-product-learning";

const MAX_DIRECT_FILE_BYTES = 2 * 1024 * 1024;

function reject(status: number, message: string, extra: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...extra }, { status, headers: { "cache-control": "no-store" } });
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "ERP 导入历史");
    const params = new URL(request.url).searchParams;
    const requestedSource = params.get("source");
    if (requestedSource && !isErpReferenceSourceKey(requestedSource)) {
      return reject(400, "source 必须为 products、inventory_age 或 combos");
    }
    const source = requestedSource && isErpReferenceSourceKey(requestedSource) ? requestedSource : undefined;
    const batchId = params.get("batchId")?.trim() ?? "";
    if (batchId && !source) return reject(400, "按精确批次查询时必须提供 source");
    const batchHash = source && batchId.startsWith(`${source}:`) ? batchId.slice(source.length + 1) : "";
    if (batchId && (!/^[a-f0-9]{64}$/i.test(batchHash))) return reject(400, "batchId 与 source 不匹配或格式无效");
    const paged = params.has("page") || params.has("pageSize");
    const page = parsePositiveIntegerQuery(paged ? params.get("page") : null, 1, "page", 10_000);
    const pageSize = parsePositiveIntegerQuery(paged ? params.get("pageSize") : params.get("limit"), 50, paged ? "pageSize" : "limit", 100);
    if (source === "inventory_age") {
      const query = new URLSearchParams({ dataset: "age", page: String(page), pageSize: String(pageSize) });
      if (batchId) query.set("batchId", batchId);
      const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
        principal,
        { method: "GET", path: INVENTORY_IMPORTS_PATH, service: "reader", rawQuery: query.toString() },
        { signal: request.signal },
      );
      const data = await withCurrentInventoryBatchOwnership(principal, "age", batchId, result, { signal: request.signal });
      return Response.json(data, { headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
    }
    if (!source && !batchId) {
      const combinedLimit = page * pageSize;
      if (combinedLimit > 100) {
        return reject(400, "跨域 ERP 导入历史最多联合翻阅前 100 条；更早记录请先选择具体来源");
      }
      const ageQuery = new URLSearchParams({ dataset: "age", page: "1", pageSize: String(combinedLimit) });
      const erpService = createDjangoErpReferenceService();
      const [productsResult, combosResult, age] = await Promise.all([
        erpService.requestJson<{ items: Array<Record<string, unknown>>; pagination: { total: number } }>(
          principal,
          { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader", rawQuery: new URLSearchParams({ source: "products", page: "1", pageSize: String(combinedLimit) }).toString() },
          { signal: request.signal },
        ),
        erpService.requestJson<{ items: Array<Record<string, unknown>>; pagination: { total: number } }>(
          principal,
          { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader", rawQuery: new URLSearchParams({ source: "combos", page: "1", pageSize: String(combinedLimit) }).toString() },
          { signal: request.signal },
        ),
        createDjangoInventoryService().requestJson<{
          items: Array<Record<string, unknown>>;
          pagination: { total: number };
        }>(principal, {
          method: "GET",
          path: INVENTORY_IMPORTS_PATH,
          service: "reader",
          rawQuery: ageQuery.toString(),
        }, { signal: request.signal }),
      ]);
      const combined = [
        ...productsResult.data.items,
        ...combosResult.data.items,
        ...age.data.items,
      ].sort((left, right) => {
        const byTime = String(right.createdAt ?? "").localeCompare(String(left.createdAt ?? ""));
        return byTime || String(right.id ?? "").localeCompare(String(left.id ?? ""));
      });
      const offset = (page - 1) * pageSize;
      const items = combined.slice(offset, offset + pageSize);
      const total = productsResult.data.pagination.total + combosResult.data.pagination.total + age.data.pagination.total;
      return Response.json({
        items,
        pagination: {
          page,
          pageSize,
          total,
          returned: items.length,
          totalPages: Math.ceil(total / pageSize),
          truncated: offset + items.length < total,
        },
      }, { headers: { "cache-control": "no-store" } });
    }
    if (!source) return reject(400, "ERP 导入来源无效");
    const query = new URLSearchParams({ source, page: String(page), pageSize: String(pageSize) });
    if (batchId) query.set("batchId", batchId);
    const result = await createDjangoErpReferenceService().requestJson<Record<string, unknown>>(
      principal,
      { method: "GET", path: ERP_REFERENCE_IMPORTS_PATH, service: "reader", rawQuery: query.toString() },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-erp-reference-data-revision": result.revision } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取 ERP 导入历史失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "ERP 参照数据", "导入");
    const contentType = request.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
      return reject(415, "请使用 multipart/form-data 上传 .xlsx 文件");
    }
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > MAX_DIRECT_FILE_BYTES + 512 * 1024) {
      return reject(413, "超过 2MB 的报表请使用分片上传接口");
    }

    const formData = await request.formData().catch(() => null);
    const entry = formData?.get("file");
    const source = formData?.get("source");
    const snapshotDate = typeof formData?.get("snapshotDate") === "string"
      ? String(formData?.get("snapshotDate"))
      : undefined;
    if (!isErpReferenceSourceKey(source)) return reject(400, "缺少有效的数据来源");
    if (!(entry instanceof File)) return reject(400, "缺少名为 file 的 Excel 文件");
    if (!entry.name.toLowerCase().endsWith(".xlsx")) return reject(400, "仅支持 .xlsx 格式的吉客云报表");
    if (entry.size === 0) return reject(400, "上传文件为空");
    if (entry.size > MAX_DIRECT_FILE_BYTES) return reject(413, "超过 2MB 的报表请使用分片上传接口");

    const bytes = new Uint8Array(await entry.arrayBuffer());
    const payload = source === "inventory_age"
      ? await importInventoryAgeToDjango({
          principal,
          bytes,
          fileName: entry.name,
          fileSizeBytes: entry.size,
          snapshotDate,
        }, { signal: request.signal })
      : await importErpReferenceToDjango({
          principal,
          source,
          bytes,
          fileName: entry.name,
          fileSizeBytes: entry.size,
        }, { signal: request.signal });
    const productBatchId = (payload as { batch?: { id?: unknown } }).batch?.id;
    const responsePayload = source === "products"
      ? { ...payload, newProductLearning: await reconcileNewProductCodesAfterImport(principal, typeof productBatchId === "string" ? productBatchId : "", request.signal) }
      : payload;
    return Response.json(responsePayload, { status: importExecutionHttpStatus(payload), headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "ERP 数据导入失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
