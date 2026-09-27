import {
  MAX_NETSHOP_ASSET_FILE_BYTES,
  NETSHOP_ASSET_UPLOAD_CHUNK_BYTES,
  assembleNetshopAssetUpload,
  beginNetshopAssetUpload,
  claimNetshopAssetUpload,
  finishNetshopAssetUpload,
  receiveNetshopAssetUploadChunk,
  releaseNetshopAssetUpload,
} from "@/lib/netshop/product-asset-upload";
import {
  prepareNormalizedNetshopImport,
  TMALL_PLATFORM,
} from "@/lib/netshop/normalized-import";
import {
  createDjangoNetshopService,
  NETSHOP_IMPORTS_PATH,
} from "@/lib/django/netshop-service";
import { resolveEnabledTmallShop } from "@/lib/netshop/tmall-store-catalog";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
} from "@/lib/auth/authorization";
import { netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import {
  importExecutionHttpStatus,
  safeApiErrorResponse,
  type ImportExecutionLike,
} from "@/lib/http/api-error";

function reject(status: number, message: string, extra: Record<string, unknown> = {}) {
  return Response.json(
    { ok: false, status: "rejected", message, ...extra },
    { status, headers: { "cache-control": "no-store" } },
  );
}

function bodyString(body: Record<string, unknown>, name: string) {
  return typeof body[name] === "string" ? body[name].trim() : "";
}

function snapshotDate(body: Record<string, unknown>) {
  const value = bodyString(body, "snapshotDate");
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
}

function headerInteger(request: Request, name: string) {
  const value = Number(request.headers.get(name));
  return Number.isSafeInteger(value) ? value : NaN;
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    netshopPlatformsForPrincipal(principal, [TMALL_PLATFORM]);
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body) return reject(400, "请求内容无效");
    if (bodyString(body, "source") !== "tmall_product_assets") {
      return reject(400, "该分片接口仅接受天猫 SPU 商品图来源");
    }

    if (body.action === "init") {
      const effectiveSnapshotDate = snapshotDate(body);
      if (!effectiveSnapshotDate) return reject(400, "天猫 SPU 商品图分片上传必须绑定有效快照日期");
      const shop = resolveEnabledTmallShop(bodyString(body, "shopName"));
      const upload = await beginNetshopAssetUpload(principal, {
        fileName: bodyString(body, "fileName"),
        fileSizeBytes: Number(body.fileSizeBytes),
        chunkCount: Number(body.chunkCount),
        clientFingerprint: bodyString(body, "fingerprint"),
        shopName: shop.shopName,
        snapshotDate: effectiveSnapshotDate,
      }, request.signal);
      return Response.json({
        ok: true,
        status: "ready",
        upload,
        limits: {
          chunkSizeBytes: NETSHOP_ASSET_UPLOAD_CHUNK_BYTES,
          maxFileSizeBytes: MAX_NETSHOP_ASSET_FILE_BYTES,
        },
      }, { headers: { "cache-control": "no-store" } });
    }

    if (body.action === "complete") {
      const uploadId = bodyString(body, "uploadId");
      const effectiveSnapshotDate = snapshotDate(body);
      if (!uploadId || !effectiveSnapshotDate) return reject(400, "缺少上传会话标识或有效快照日期");
      const shop = resolveEnabledTmallShop(bodyString(body, "shopName"));
      const claim = await claimNetshopAssetUpload(principal, uploadId, request.signal);
      if (claim.session.shopName !== shop.shopName || claim.session.snapshotDate !== effectiveSnapshotDate) {
        if (claim.kind === "claimed") await releaseNetshopAssetUpload(principal, claim, request.signal);
        return reject(409, "上传会话绑定的店铺或快照日期与本次请求不一致");
      }
      if (claim.kind === "completed") {
        return Response.json(
          claim.result,
          {
            status: importExecutionHttpStatus(claim.result as ImportExecutionLike),
            headers: { "cache-control": "no-store" },
          },
        );
      }
      try {
        const assembled = await assembleNetshopAssetUpload(claim);
        const normalized = await prepareNormalizedNetshopImport({
          bytes: assembled.bytes,
          fileName: assembled.session.fileName,
          fileSizeBytes: assembled.session.fileSizeBytes,
          source: "tmall_product_assets",
          platform: TMALL_PLATFORM,
          shopName: shop.shopName,
          snapshotDate: effectiveSnapshotDate,
        });
        const django = await createDjangoNetshopService().request<Record<string, unknown>>(
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
        await finishNetshopAssetUpload(
          principal,
          claim,
          assembled.objectKeys,
          django.data,
          request.signal,
        );
        return Response.json(django.data, {
          status: django.status,
          headers: { "cache-control": "no-store" },
        });
      } catch (error) {
        await releaseNetshopAssetUpload(principal, claim, request.signal).catch(() => undefined);
        throw error;
      }
    }
    return reject(400, "未知的分片上传操作");
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "天猫 SPU 商品图分片上传初始化或合并失败。", {
      shape: "import",
      headers: { "cache-control": "no-store" },
    });
  }
}

export async function PUT(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    netshopPlatformsForPrincipal(principal, [TMALL_PLATFORM]);
    const uploadId = request.headers.get("x-upload-id")?.trim() ?? "";
    const chunkIndex = headerInteger(request, "x-chunk-index");
    if (!uploadId || !Number.isSafeInteger(chunkIndex)) return reject(400, "缺少有效的分片上传标识");
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > NETSHOP_ASSET_UPLOAD_CHUNK_BYTES) return reject(413, "单个分片不能超过 2MB");
    const bytes = new Uint8Array(await request.arrayBuffer());
    if (bytes.byteLength === 0) return reject(400, "上传分片为空");
    const upload = await receiveNetshopAssetUploadChunk(
      principal,
      { uploadId, chunkIndex, bytes },
      request.signal,
    );
    return Response.json({ ok: true, status: "uploading", upload }, {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "天猫 SPU 商品图分片上传失败。", {
      shape: "import",
      headers: { "cache-control": "no-store" },
    });
  }
}
