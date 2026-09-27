import {
  INVENTORY_UPLOAD_CHUNK_BYTES,
  MAX_CHUNKED_INVENTORY_FILE_BYTES,
} from "@/lib/inventory/django-chunked-upload";
import { importErpReferenceToDjango } from "@/lib/erp-reference/django-import-service";
import { reconcileNewProductCodesAfterImport } from "@/lib/workflow/new-product-learning";
import { importInventoryAgeToDjango } from "@/lib/inventory/django-age-import-service";
import {
  assembleDjangoInventoryUpload,
  beginDjangoInventoryUpload,
  claimDjangoInventoryUpload,
  finishDjangoInventoryUpload,
  receiveDjangoInventoryUploadChunk,
  releaseDjangoInventoryUpload,
} from "@/lib/inventory/django-chunked-upload";
import {
  assembleDjangoErpUpload,
  beginDjangoErpUpload,
  claimDjangoErpUpload,
  finishDjangoErpUpload,
  receiveDjangoErpUploadChunk,
  releaseDjangoErpUpload,
} from "@/lib/erp-reference/django-chunked-upload";
import { isErpReferenceSourceKey } from "@/lib/imports/erp-reference";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { importExecutionHttpStatus, safeApiErrorResponse, type ImportExecutionLike } from "@/lib/http/api-error";

function reject(status: number, message: string, extra: Record<string, unknown> = {}) {
  return Response.json({ ok: false, status: "rejected", message, ...extra }, { status, headers: { "cache-control": "no-store" } });
}

function headerNumber(request: Request, name: string) {
  const value = Number(request.headers.get(name));
  return Number.isSafeInteger(value) ? value : NaN;
}

function erpUploadScope(body: Record<string, unknown>) {
  if (!isErpReferenceSourceKey(body.source)) return null;
  const snapshotDate = typeof body.snapshotDate === "string" ? body.snapshotDate.trim() : "";
  if (body.source === "inventory_age" && !/^\d{4}-\d{2}-\d{2}$/.test(snapshotDate)) return null;
  return { source: body.source, snapshotDate: body.source === "inventory_age" ? snapshotDate : "" };
}

const AGE_UPLOAD_PREFIX = "inventory-age:";

function publicAgeUpload<T extends { id: string }>(upload: T) {
  return { ...upload, id: `${AGE_UPLOAD_PREFIX}${upload.id}` };
}

function ageUploadId(value: unknown) {
  return typeof value === "string" && value.startsWith(AGE_UPLOAD_PREFIX)
    ? value.slice(AGE_UPLOAD_PREFIX.length)
    : "";
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "ERP 参照数据", "导入");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body) return reject(400, "请求内容无效");
    if (!isErpReferenceSourceKey(body.source)) return reject(400, "缺少有效的数据来源");
    const scope = erpUploadScope(body);
    if (!scope) return reject(400, "库龄分片上传必须绑定有效快照日期");

    if (body.source === "inventory_age") {
      if (body.action === "init") {
        const upload = await beginDjangoInventoryUpload(principal, {
          dataset: "age",
          snapshotDate: scope.snapshotDate,
          fileName: typeof body.fileName === "string" ? body.fileName : "",
          fileSizeBytes: Number(body.fileSizeBytes),
          chunkCount: Number(body.chunkCount),
          fingerprint: typeof body.fingerprint === "string" ? body.fingerprint : "",
        }, request.signal);
        return Response.json({
          ok: true,
          status: "ready",
          upload: publicAgeUpload(upload),
          limits: { chunkSizeBytes: INVENTORY_UPLOAD_CHUNK_BYTES, maxFileSizeBytes: MAX_CHUNKED_INVENTORY_FILE_BYTES },
        }, { headers: { "cache-control": "no-store" } });
      }
      if (body.action === "complete") {
        const uploadId = ageUploadId(body.uploadId);
        if (!uploadId) return reject(400, "缺少库存库龄上传会话标识");
        const claim = await claimDjangoInventoryUpload(principal, uploadId, request.signal);
        if (claim.session.dataset !== "age" || claim.session.snapshotDate !== scope.snapshotDate) {
          if (claim.kind === "claimed") await releaseDjangoInventoryUpload(principal, uploadId, claim.ownerToken, request.signal);
          return reject(409, "上传会话绑定的库龄来源或快照日期与本次请求不一致");
        }
        if (claim.kind === "completed") {
          return Response.json(claim.result, { status: importExecutionHttpStatus(claim.result as ImportExecutionLike), headers: { "cache-control": "no-store" } });
        }
        try {
          const bytes = await assembleDjangoInventoryUpload(principal, claim, request.signal);
          const result = await importInventoryAgeToDjango({
            principal,
            bytes,
            fileName: claim.session.fileName,
            fileSizeBytes: claim.session.fileSizeBytes,
            snapshotDate: scope.snapshotDate,
          }, { signal: request.signal });
          await finishDjangoInventoryUpload(principal, uploadId, claim.ownerToken, result, request.signal);
          return Response.json(result, { status: importExecutionHttpStatus(result), headers: { "cache-control": "no-store" } });
        } catch (error) {
          await releaseDjangoInventoryUpload(principal, uploadId, claim.ownerToken, request.signal).catch(() => undefined);
          throw error;
        }
      }
      return reject(400, "未知的库龄分片上传操作");
    }

    if (body.action === "init") {
      const clientFingerprint = typeof body.fingerprint === "string" ? body.fingerprint : "";
      const upload = await beginDjangoErpUpload(principal, {
        source: body.source,
        fileName: typeof body.fileName === "string" ? body.fileName : "",
        fileSizeBytes: Number(body.fileSizeBytes),
        chunkCount: Number(body.chunkCount),
        fingerprint: clientFingerprint,
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
      if (!uploadId) return reject(400, "缺少上传会话标识");
      const claim = await claimDjangoErpUpload(principal, uploadId, body.source, request.signal);
      if (claim.kind === "completed") {
        return Response.json(claim.result, { status: importExecutionHttpStatus(claim.result as ImportExecutionLike), headers: { "cache-control": "no-store" } });
      }
      try {
        const bytes = await assembleDjangoErpUpload(principal, claim, request.signal);
        const imported = await importErpReferenceToDjango({
          principal,
          source: body.source,
          bytes,
          fileName: claim.session.fileName,
          fileSizeBytes: claim.session.fileSizeBytes,
        }, { signal: request.signal });
        const productBatchId = (imported as { batch?: { id?: unknown } }).batch?.id;
        const result = body.source === "products"
          ? { ...imported, newProductLearning: await reconcileNewProductCodesAfterImport(principal, typeof productBatchId === "string" ? productBatchId : "", request.signal) }
          : imported;
        await finishDjangoErpUpload(principal, {
          uploadId, source: body.source, ownerToken: claim.ownerToken, result,
        }, request.signal);
        return Response.json(result, { status: importExecutionHttpStatus(result), headers: { "cache-control": "no-store" } });
      } catch (error) {
        await releaseDjangoErpUpload(principal, {
          uploadId, source: body.source, ownerToken: claim.ownerToken,
        }, request.signal).catch(() => undefined);
        throw error;
      }
    }
    return reject(400, "未知的分片上传操作");
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "ERP 分片上传失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}

export async function PUT(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "ERP 参照数据", "导入");
    const uploadId = request.headers.get("x-upload-id") ?? "";
    const chunkIndex = headerNumber(request, "x-chunk-index");
    if (!uploadId || !Number.isSafeInteger(chunkIndex)) return reject(400, "缺少有效的分片上传标识");
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > INVENTORY_UPLOAD_CHUNK_BYTES) return reject(413, "单个分片不能超过 1MB");
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (bytes.byteLength === 0) return reject(400, "上传分片为空");
    const djangoAgeUploadId = ageUploadId(uploadId);
    if (djangoAgeUploadId) {
      const upload = await receiveDjangoInventoryUploadChunk(
        principal,
        { uploadId: djangoAgeUploadId, chunkIndex, bytes },
        request.signal,
      );
      return Response.json({ ok: true, status: "uploading", upload: publicAgeUpload(upload) }, { headers: { "cache-control": "no-store" } });
    }
    const upload = await receiveDjangoErpUploadChunk(
      principal, { uploadId, chunkIndex, bytes }, request.signal,
    );
    return Response.json({ ok: true, status: "uploading", upload }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "ERP 分片上传失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
