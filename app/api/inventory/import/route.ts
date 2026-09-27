import {
  prepareNormalizedNetshopImport,
  readNetshopForm,
} from "@/lib/netshop/normalized-import";
import {
  createDjangoNetshopService,
  NETSHOP_IMPORTS_PATH,
} from "@/lib/django/netshop-service";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { safeApiErrorResponse } from "@/lib/http/api-error";

const MAX_DIRECT_FILE_BYTES = 25 * 1024 * 1024;

function errorResponse(status: number, message: string, details: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...details }, { status, headers: { "cache-control": "no-store" } });
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "京东自营库存", "导入");
    const contentType = request.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
      return errorResponse(415, "请使用 multipart/form-data 上传库存快照文件");
    }

    const formData = await request.formData().catch(() => null);
    if (!formData) return errorResponse(400, "无法读取上传表单");
    const parsed = readNetshopForm(formData);
    const file = parsed.file;
    if (!(file instanceof File)) return errorResponse(400, "缺少名为 file 的文件");
    if (file.size === 0) return errorResponse(400, "上传文件为空");
    if (file.size > MAX_DIRECT_FILE_BYTES) return errorResponse(413, "文件超过 25MB，请拆分后上传");

    const source = parsed.source ?? "inv_selfop";
    if (source !== "inv_selfop") {
      return errorResponse(400, "/api/inventory/import/ 仅用于 source=inv_selfop 的京东自营库存快照");
    }

    const normalized = await prepareNormalizedNetshopImport({
      bytes: new Uint8Array(await file.arrayBuffer()),
      fileName: file.name,
      fileSizeBytes: file.size,
      source,
      note: parsed.note,
      snapshotDate: parsed.snapshotDate,
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
    return safeApiErrorResponse(error, "京东自营库存快照导入失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
