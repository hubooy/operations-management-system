import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { importExecutionHttpStatus, safeApiErrorResponse, type ImportExecutionLike } from "@/lib/http/api-error";
import {
  INVENTORY_UPLOAD_CHUNK_BYTES,
  MAX_CHUNKED_INVENTORY_FILE_BYTES,
  assembleDjangoInventoryUpload,
  beginDjangoInventoryUpload,
  claimDjangoInventoryUpload,
  finishDjangoInventoryUpload,
  receiveDjangoInventoryUploadChunk,
  releaseDjangoInventoryUpload,
} from "@/lib/inventory/django-chunked-upload";
import { importInventoryStockToDjango } from "@/lib/inventory/django-import-service";
import { syncLatestInventoryProjection } from "@/lib/products/inventory-projection-sync";

function reject(status: number, message: string, extra: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...extra }, { status, headers: { "cache-control": "no-store" } });
}
function snapshotDateFromBody(body: Record<string, unknown>) {
  const value = typeof body.snapshotDate === "string" ? body.snapshotDate.trim() : "";
  const parsed = new Date(`${value}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value ? value : "";
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "库存数据", "导入");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body) return reject(400, "请求内容无效");
    if (body.action === "init") {
      const snapshotDate = snapshotDateFromBody(body);
      if (!snapshotDate) return reject(400, "分仓库存分片上传必须绑定有效快照日期");
      const upload = await beginDjangoInventoryUpload(principal, {
        dataset: "stock",
        snapshotDate,
        fileName: typeof body.fileName === "string" ? body.fileName : "",
        fileSizeBytes: Number(body.fileSizeBytes),
        chunkCount: Number(body.chunkCount),
        fingerprint: typeof body.fingerprint === "string" ? body.fingerprint : "",
      }, request.signal);
      return Response.json({
        ok: true,
        status: "ready",
        upload,
        limits: { chunkSizeBytes: INVENTORY_UPLOAD_CHUNK_BYTES, maxFileSizeBytes: MAX_CHUNKED_INVENTORY_FILE_BYTES },
      }, { headers: { "cache-control": "no-store" } });
    }
    if (body.action === "complete") {
      const uploadId = typeof body.uploadId === "string" ? body.uploadId : "";
      const snapshotDate = snapshotDateFromBody(body);
      if (!uploadId || !snapshotDate) return reject(400, "缺少上传会话标识或有效快照日期");
      const claim = await claimDjangoInventoryUpload(principal, uploadId, request.signal);
      if (claim.session.dataset !== "stock" || claim.session.snapshotDate !== snapshotDate) {
        if (claim.kind === "claimed") await releaseDjangoInventoryUpload(principal, uploadId, claim.ownerToken, request.signal);
        return reject(409, "上传会话绑定的库存快照日期与本次完成请求不一致");
      }
      if (claim.kind === "completed") {
        const projection = await syncLatestInventoryProjection(principal, { signal: request.signal });
        const completed = { ...claim.result, inventoryProjection: projection };
        return Response.json(completed, { status: importExecutionHttpStatus(completed as ImportExecutionLike), headers: { "cache-control": "no-store" } });
      }
      try {
        const bytes = await assembleDjangoInventoryUpload(principal, claim, request.signal);
        const imported = await importInventoryStockToDjango({
          principal,
          bytes,
          fileName: claim.session.fileName,
          fileSizeBytes: claim.session.fileSizeBytes,
          snapshotDateOverride: snapshotDate,
        }, { signal: request.signal });
        const result = imported.ok
          ? { ...imported, inventoryProjection: await syncLatestInventoryProjection(principal, { signal: request.signal }) }
          : imported;
        await finishDjangoInventoryUpload(principal, uploadId, claim.ownerToken, result, request.signal);
        return Response.json(result, { status: importExecutionHttpStatus(result), headers: { "cache-control": "no-store" } });
      } catch (error) {
        await releaseDjangoInventoryUpload(principal, uploadId, claim.ownerToken, request.signal).catch(() => undefined);
        throw error;
      }
    }
    return reject(400, "未知的分片上传操作");
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "库存分片上传初始化或合并失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}

export async function PUT(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "库存数据", "导入");
    const uploadId = request.headers.get("x-upload-id") ?? "";
    const chunkIndex = Number(request.headers.get("x-chunk-index"));
    if (!uploadId || !Number.isSafeInteger(chunkIndex) || chunkIndex < 0) return reject(400, "缺少有效的分片上传标识");
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > INVENTORY_UPLOAD_CHUNK_BYTES) return reject(413, "单个分片不能超过 1MB");
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (bytes.byteLength === 0) return reject(400, "上传分片为空");
    const upload = await receiveDjangoInventoryUploadChunk(principal, { uploadId, chunkIndex, bytes }, request.signal);
    return Response.json({ ok: true, status: "uploading", upload }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "库存分片上传失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
