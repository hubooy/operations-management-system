import {
  XLSX_CONTENT_TYPE,
  importSalesLedgerBytes,
  validateSalesImportChannels,
  validateSalesImportDateRange,
} from "@/lib/sales/import-service";
import {
  SALES_IMPORTS_PATH,
  requestDjangoSalesService,
} from "@/lib/django/sales-writer";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { importExecutionHttpStatus, parsePositiveIntegerQuery, safeApiErrorResponse } from "@/lib/http/api-error";

const MAX_DIRECT_FILE_BYTES = 2 * 1024 * 1024;

function errorResponse(status: number, message: string, details: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...details }, { status, headers: { "cache-control": "no-store" } });
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "销售导入历史");
    const params = new URL(request.url).searchParams;
    const paged = params.has("page") || params.has("pageSize");
    const page = parsePositiveIntegerQuery(paged ? params.get("page") : null, 1, "page", 10_000);
    const pageSize = parsePositiveIntegerQuery(paged ? params.get("pageSize") : params.get("limit"), 20, paged ? "pageSize" : "limit", 100);
    const payload = await requestDjangoSalesService(principal, {
      method: "GET",
      path: SALES_IMPORTS_PATH,
      query: new URLSearchParams({ page: String(page), pageSize: String(pageSize) }),
      service: "reader",
    });
    return Response.json(payload.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "读取销售导入历史失败。", { headers: { "cache-control": "no-store" } });
  }
}

/** Small reports keep the original single-request import path. Larger files use /chunks. */
export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "销售数据", "导入");
    const contentType = request.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
      return errorResponse(415, "请使用 multipart/form-data 上传 .xlsx 文件");
    }

    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > MAX_DIRECT_FILE_BYTES + 1024 * 1024) {
      return errorResponse(413, "超过 2MB 的报表请使用分片上传接口");
    }

    const formData = await request.formData().catch(() => null);
    const entry = formData?.get("file");
    const expectedStartDate = String(formData?.get("expectedStartDate") ?? "").trim();
    const expectedEndDate = String(formData?.get("expectedEndDate") ?? "").trim();
    const expectedChannels = formData?.get("expectedChannels") ?? null;
    const dateRange = validateSalesImportDateRange(expectedStartDate, expectedEndDate);
    if (!dateRange.ok) return errorResponse(422, "销售导入必须提供有效的权威起止日期", {
      errors: [{ code: dateRange.code, message: dateRange.message }],
      errorCount: 1,
    });
    const channelScope = validateSalesImportChannels(expectedChannels);
    if (!channelScope.ok) return errorResponse(422, "销售导入必须提供有效的权威渠道范围", {
      errors: [{ code: channelScope.code, message: channelScope.message }],
      errorCount: 1,
    });
    if (!(entry instanceof File)) return errorResponse(400, "缺少名为 file 的 Excel 文件");
    if (!entry.name.toLowerCase().endsWith(".xlsx")) return errorResponse(400, "仅支持 .xlsx 格式的销售单明细账");
    if (entry.size === 0) return errorResponse(400, "上传文件为空");
    if (entry.size > MAX_DIRECT_FILE_BYTES) return errorResponse(413, "超过 2MB 的报表请使用分片上传接口");

    const payload = await importSalesLedgerBytes({
      principal,
      bytes: new Uint8Array(await entry.arrayBuffer()),
      fileName: entry.name,
      fileSizeBytes: entry.size,
      expectedStartDate,
      expectedEndDate,
      expectedChannels: channelScope.channels,
    });
    return Response.json(payload, {
      status: importExecutionHttpStatus(payload),
      headers: { "cache-control": "no-store", "x-import-content-type": XLSX_CONTENT_TYPE },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "销售数据导入失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
