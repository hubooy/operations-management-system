import {
  prepareNormalizedNetshopImport,
  readNetshopForm,
  TMALL_PLATFORM,
} from "@/lib/netshop/normalized-import";
import {
  createDjangoNetshopService,
  NETSHOP_IMPORTS_PATH,
} from "@/lib/django/netshop-service";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
} from "@/lib/auth/authorization";
import { netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  NETSHOP_QUERY_MAX_PAGE,
  NETSHOP_QUERY_MAX_PAGE_SIZE,
  netshopQueryErrorPayload,
  readNetshopQueryInteger,
} from "@/lib/netshop/query-contract";

const MAX_DIRECT_FILE_BYTES = 25 * 1024 * 1024;

function errorResponse(status: number, message: string, details: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...details }, { status, headers: { "cache-control": "no-store" } });
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    const params = new URL(request.url).searchParams;
    readNetshopQueryInteger(params.get("page"), "page", 1, 1, NETSHOP_QUERY_MAX_PAGE);
    readNetshopQueryInteger(
      params.get("pageSize") ?? params.get("limit"),
      "pageSize",
      20,
      1,
      NETSHOP_QUERY_MAX_PAGE_SIZE,
    );
    netshopPlatformsForPrincipal(principal, params.getAll("platform"));
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      { method: "GET", path: NETSHOP_IMPORTS_PATH, query: params, service: "reader" },
      { signal: request.signal },
    );
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    const failure = netshopQueryErrorPayload(error, "读取网店导入历史失败");
    return Response.json(failure.body, { status: failure.status, headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    const contentType = request.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
      return errorResponse(415, "请使用 multipart/form-data 上传文件");
    }

    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > MAX_DIRECT_FILE_BYTES + 1024 * 1024) {
      return errorResponse(413, "文件超过 25MB，请拆分后上传");
    }

    const formData = await request.formData().catch(() => null);
    if (!formData) return errorResponse(400, "无法读取上传表单");
    const parsed = readNetshopForm(formData);
    if (!parsed.source) return errorResponse(400, "缺少或不支持 source 参数");
    const effectivePlatform = parsed.source.startsWith("tmall_") ? TMALL_PLATFORM : parsed.platform?.trim() || "京东";
    netshopPlatformsForPrincipal(principal, [effectivePlatform]);
    if (!(parsed.file instanceof File)) return errorResponse(400, "缺少名为 file 的文件");
    if (parsed.file.size === 0) return errorResponse(400, "上传文件为空");
    if (parsed.file.size > MAX_DIRECT_FILE_BYTES) return errorResponse(413, "文件超过 25MB，请拆分后上传");

    const normalized = await prepareNormalizedNetshopImport({
      bytes: new Uint8Array(await parsed.file.arrayBuffer()),
      fileName: parsed.file.name,
      fileSizeBytes: parsed.file.size,
      source: parsed.source,
      platform: parsed.platform,
      shopName: parsed.shopName,
      note: parsed.note,
      snapshotDate: parsed.snapshotDate,
      expectedDataset: parsed.expectedDataset === "sku_daily" || parsed.expectedDataset === "spu_daily" ? parsed.expectedDataset : undefined,
      expectedStartDate: parsed.expectedStartDate,
      expectedEndDate: parsed.expectedEndDate,
    });
    const result = await createDjangoNetshopService().request<Record<string, unknown>>(
      principal,
      {
        method: "POST",
        path: NETSHOP_IMPORTS_PATH,
        payload: normalized as unknown as Record<string, unknown>,
        service: "writer",
        acceptedErrorStatuses: [422],
      },
      { signal: request.signal },
    );
    const headers = new Headers({ "cache-control": "no-store" });
    if (result.replayed) headers.set("x-teruisi-write-replay", "1");
    return Response.json(result.data, { status: result.status, headers });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "网店数据导入失败", {
      shape: "import",
      headers: { "cache-control": "no-store" },
    });
  }
}
