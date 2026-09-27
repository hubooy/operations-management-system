import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  importExecutionHttpStatus,
  parsePositiveIntegerQuery,
  safeApiErrorResponse,
} from "@/lib/http/api-error";
import {
  createDjangoProductsService,
  PRODUCTS_IMPORTS_PATH,
} from "@/lib/django/products-service";
import {
  importProductShippingRateBytes,
  type ProductShippingRateImportBatch,
} from "@/lib/products/shipping-rate-import-service";

const MAX_DIRECT_FILE_BYTES = 2 * 1024 * 1024;

function reject(status: number, message: string, extra: Record<string, unknown> = {}) {
  return Response.json(
    { ok: false, status: "rejected", message, ...extra },
    { status, headers: { "cache-control": "no-store" } },
  );
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "SKU 快递费率导入历史");
    const params = new URL(request.url).searchParams;
    const page = parsePositiveIntegerQuery(params.get("page"), 1, "page", 10_000);
    const pageSize = parsePositiveIntegerQuery(params.get("pageSize") ?? params.get("limit"), 50, "pageSize", 100);
    const rawQuery = new URLSearchParams({ page: String(page), pageSize: String(pageSize) }).toString();
    const result = await createDjangoProductsService().requestJson<{
      items: ProductShippingRateImportBatch[];
      pagination: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
    }>(principal, {
      method: "GET",
      path: PRODUCTS_IMPORTS_PATH,
      service: "reader",
      rawQuery,
    }, { signal: request.signal });
    return Response.json(
      result.data,
      { headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取 SKU 快递费率导入历史失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "SKU 快递费率", "导入");
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
    if (!(entry instanceof File)) return reject(400, "缺少名为 file 的 Excel 文件");
    if (!entry.name.toLowerCase().endsWith(".xlsx")) return reject(400, "仅支持 .xlsx 格式的 SKU 快递费率报表");
    if (entry.size === 0) return reject(400, "上传文件为空");
    if (entry.size > MAX_DIRECT_FILE_BYTES) return reject(413, "超过 2MB 的报表请使用分片上传接口");
    const result = await importProductShippingRateBytes({
      bytes: new Uint8Array(await entry.arrayBuffer()),
      fileName: entry.name,
      fileSizeBytes: entry.size,
      principal,
      signal: request.signal,
    });
    return Response.json(result, {
      status: importExecutionHttpStatus(result),
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "SKU 快递费率导入失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
