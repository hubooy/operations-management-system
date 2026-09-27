import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoNetshopService,
  NETSHOP_ASSET_UPLOADS_PATH,
} from "@/lib/django/netshop-service";
import { PublicApiError } from "@/lib/http/api-error";

export const NETSHOP_ASSET_UPLOAD_CHUNK_BYTES = 2 * 1024 * 1024;
export const MAX_NETSHOP_ASSET_FILE_BYTES = 64 * 1024 * 1024;

const OBJECT_PREFIX = "netshop-asset-upload/v1";

type StoredChunk = {
  chunkIndex: number;
  objectKey: string;
  sizeBytes: number;
  sha256: string;
};

export type NetshopAssetUploadSession = {
  id: string;
  shopName: string;
  snapshotDate: string;
  fileName: string;
  fileSizeBytes: number;
  chunkSizeBytes: number;
  chunkCount: number;
  receivedChunkIndexes: number[];
  receivedBytes: number;
  status: string;
  expiresAt: string;
};

export type NetshopAssetUploadClaim =
  | {
      kind: "claimed";
      ownerToken: string;
      ownerGeneration: number;
      session: NetshopAssetUploadSession;
      chunks: StoredChunk[];
    }
  | { kind: "completed"; session: NetshopAssetUploadSession; result: unknown };

function requestError(status: 400 | 404 | 409 | 413 | 422, message: string) {
  const code = status === 404
    ? "not_found"
    : status === 409
      ? "conflict"
      : status === 413
        ? "payload_too_large"
        : "invalid_request";
  return new PublicApiError(status, code, message);
}

function bytesForDigest(bytes: Uint8Array) {
  if (bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength) return bytes.buffer as ArrayBuffer;
  return bytes.slice().buffer as ArrayBuffer;
}

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256(bytes: Uint8Array) {
  return toHex(await crypto.subtle.digest("SHA-256", bytesForDigest(bytes)));
}

async function uploadBucket() {
  const { env } = await import("cloudflare:workers");
  if (!env.SALES_IMPORT_FILES) throw new Error("R2 分片存储未配置");
  return env.SALES_IMPORT_FILES;
}

async function writer<T>(
  principal: AppPrincipal,
  payload: Record<string, unknown>,
  signal?: AbortSignal,
) {
  const response = await createDjangoNetshopService().request<T>(
    principal,
    { method: "POST", path: NETSHOP_ASSET_UPLOADS_PATH, payload, service: "writer" },
    { signal },
  );
  return response.data;
}

export async function beginNetshopAssetUpload(
  principal: AppPrincipal,
  input: {
    fileName: string;
    fileSizeBytes: number;
    chunkCount: number;
    clientFingerprint: string;
    shopName: string;
    snapshotDate: string;
  },
  signal?: AbortSignal,
) {
  if (!input.fileName.toLowerCase().endsWith(".xlsx")) throw requestError(422, "天猫 SPU 商品图只接受 .xlsx 文件");
  if (!Number.isSafeInteger(input.fileSizeBytes) || input.fileSizeBytes <= 0) throw requestError(400, "文件大小无效");
  if (input.fileSizeBytes > MAX_NETSHOP_ASSET_FILE_BYTES) throw requestError(413, "天猫 SPU 商品图文件最大支持 64MB");
  const expected = Math.ceil(input.fileSizeBytes / NETSHOP_ASSET_UPLOAD_CHUNK_BYTES);
  if (!Number.isSafeInteger(input.chunkCount) || input.chunkCount !== expected) throw requestError(400, "分片数量与文件大小不一致");
  const result = await writer<{
    upload: NetshopAssetUploadSession;
    cleanupObjectKeys: string[];
  }>(principal, {
    action: "init",
    fileName: input.fileName,
    fileSizeBytes: input.fileSizeBytes,
    chunkCount: input.chunkCount,
    clientFingerprint: input.clientFingerprint,
    shopName: input.shopName,
    snapshotDate: input.snapshotDate,
  }, signal);
  if (result.cleanupObjectKeys.length > 0) {
    await (await uploadBucket()).delete(result.cleanupObjectKeys).catch(() => undefined);
  }
  return result.upload;
}

export async function receiveNetshopAssetUploadChunk(
  principal: AppPrincipal,
  input: { uploadId: string; chunkIndex: number; bytes: Uint8Array },
  signal?: AbortSignal,
) {
  if (!/^[a-f0-9-]{36}$/i.test(input.uploadId)) throw requestError(400, "上传会话标识无效");
  if (!Number.isSafeInteger(input.chunkIndex) || input.chunkIndex < 0 || input.chunkIndex >= 32) {
    throw requestError(400, "分片序号无效");
  }
  if (input.bytes.byteLength <= 0 || input.bytes.byteLength > NETSHOP_ASSET_UPLOAD_CHUNK_BYTES) {
    throw requestError(413, "单个分片大小无效");
  }
  const checksum = await sha256(input.bytes);
  const objectKey = `${OBJECT_PREFIX}/${input.uploadId}/${input.chunkIndex.toString().padStart(6, "0")}-${checksum}`;
  const storage = await uploadBucket();
  await storage.put(objectKey, input.bytes, {
    httpMetadata: { contentType: "application/octet-stream" },
    customMetadata: { sha256: checksum, uploadId: input.uploadId },
  });
  try {
    const result = await writer<{
      upload: NetshopAssetUploadSession;
      previousObjectKey: string | null;
    }>(principal, {
      action: "record_chunk",
      uploadId: input.uploadId,
      chunkIndex: input.chunkIndex,
      objectKey,
      sizeBytes: input.bytes.byteLength,
      sha256: checksum,
    }, signal);
    if (result.previousObjectKey) await storage.delete(result.previousObjectKey).catch(() => undefined);
    return result.upload;
  } catch (error) {
    await storage.delete(objectKey).catch(() => undefined);
    throw error;
  }
}

export async function claimNetshopAssetUpload(
  principal: AppPrincipal,
  uploadId: string,
  signal?: AbortSignal,
): Promise<NetshopAssetUploadClaim> {
  return writer<NetshopAssetUploadClaim>(principal, { action: "claim", uploadId }, signal);
}

export async function assembleNetshopAssetUpload(
  claim: Extract<NetshopAssetUploadClaim, { kind: "claimed" }>,
) {
  const { session, chunks } = claim;
  if (chunks.length !== session.chunkCount
    || chunks.some((chunk, index) => chunk.chunkIndex !== index)) {
    throw requestError(409, "仍有分片尚未上传完成");
  }
  const bytes = new Uint8Array(session.fileSizeBytes);
  const storage = await uploadBucket();
  let offset = 0;
  for (const chunk of chunks) {
    const object = await storage.get(chunk.objectKey);
    if (!object) throw requestError(422, "部分上传分片已丢失，请重新上传文件");
    const part = new Uint8Array(await object.arrayBuffer());
    if (part.byteLength !== chunk.sizeBytes || await sha256(part) !== chunk.sha256) {
      throw requestError(422, "上传分片完整性校验失败");
    }
    bytes.set(part, offset);
    offset += part.byteLength;
  }
  if (offset !== bytes.byteLength) throw requestError(422, "合并后的文件大小不正确");
  return { session, bytes, objectKeys: chunks.map((chunk) => chunk.objectKey) };
}

export async function finishNetshopAssetUpload(
  principal: AppPrincipal,
  claim: Extract<NetshopAssetUploadClaim, { kind: "claimed" }>,
  objectKeys: readonly string[],
  result: unknown,
  signal?: AbortSignal,
) {
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    throw requestError(400, "导入结果无效");
  }
  const completed = await writer<{ objectKeys: string[] }>(principal, {
    action: "complete",
    uploadId: claim.session.id,
    ownerToken: claim.ownerToken,
    ownerGeneration: claim.ownerGeneration,
    result: result as Record<string, unknown>,
  }, signal);
  if (completed.objectKeys.length !== objectKeys.length
    || completed.objectKeys.some((key, index) => key !== objectKeys[index])) {
    throw requestError(409, "上传会话完成后的分片清单不一致");
  }
  try {
    if (objectKeys.length > 0) await (await uploadBucket()).delete([...objectKeys]);
    await writer(principal, {
      action: "prune",
      uploadId: claim.session.id,
      objectKeys: [...objectKeys],
    }, signal);
  } catch {
    // The completed PostgreSQL result is authoritative. A lifecycle sweep can
    // retry deletion of any content-addressed orphan chunks.
  }
}

export async function releaseNetshopAssetUpload(
  principal: AppPrincipal,
  claim: Extract<NetshopAssetUploadClaim, { kind: "claimed" }>,
  signal?: AbortSignal,
) {
  await writer(principal, {
    action: "release",
    uploadId: claim.session.id,
    ownerToken: claim.ownerToken,
    ownerGeneration: claim.ownerGeneration,
  }, signal);
}
