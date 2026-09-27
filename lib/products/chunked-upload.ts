import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoProductsService,
  PRODUCTS_UPLOADS_PATH,
  PRODUCTS_UPLOAD_CHUNK_PATH,
  type DjangoProductsServiceOptions,
} from "@/lib/django/products-service";

export const PRODUCT_UPLOAD_CHUNK_BYTES = 1024 * 1024;
export const MAX_CHUNKED_PRODUCT_FILE_BYTES = 20 * 1024 * 1024;

export type ProductUploadSession = {
  id: string;
  fingerprint: string;
  fileName: string;
  fileSizeBytes: number;
  chunkSizeBytes: number;
  chunkCount: number;
  receivedChunkIndexes: number[];
  receivedBytes: number;
  status: string;
  expiresAt: string;
};

type ProductUploadWriter = ReturnType<typeof createDjangoProductsService>;

async function sha256(bytes: Uint8Array) {
  const exact = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", exact)), (byte) => (
    byte.toString(16).padStart(2, "0")
  )).join("");
}

export async function beginProductUpload(
  principal: AppPrincipal,
  input: { fileName: string; fileSizeBytes: number; chunkCount: number; fingerprint: string },
  options: Omit<DjangoProductsServiceOptions, "config"> & { writer?: ProductUploadWriter } = {},
) {
  const { writer = createDjangoProductsService(), ...requestOptions } = options;
  const result = await writer.requestJson<{ upload: ProductUploadSession }>(principal, {
    method: "POST",
    path: PRODUCTS_UPLOADS_PATH,
    service: "writer",
    payload: { action: "init", ...input },
  }, requestOptions);
  return result.data.upload;
}

export async function receiveProductUploadChunk(
  principal: AppPrincipal,
  input: { uploadId: string; chunkIndex: number; bytes: Uint8Array },
  options: Omit<DjangoProductsServiceOptions, "config"> & { writer?: ProductUploadWriter } = {},
) {
  const { writer = createDjangoProductsService(), ...requestOptions } = options;
  const result = await writer.requestJson<{ upload: ProductUploadSession }>(principal, {
    method: "PUT",
    path: PRODUCTS_UPLOAD_CHUNK_PATH,
    service: "writer",
    bodyBytes: input.bytes,
    additionalHeaders: {
      "x-upload-id": input.uploadId,
      "x-chunk-index": String(input.chunkIndex),
    },
  }, requestOptions);
  return result.data.upload;
}

export type ProductUploadClaim =
  | { kind: "completed"; upload: ProductUploadSession; result: unknown }
  | {
      kind: "claimed";
      upload: ProductUploadSession;
      ownerToken: string;
      chunks: Array<{ chunk_index: number; size_bytes: number; sha256: string }>;
    };

export async function claimProductUpload(
  principal: AppPrincipal,
  uploadId: string,
  options: Omit<DjangoProductsServiceOptions, "config"> & { writer?: ProductUploadWriter } = {},
) {
  const { writer = createDjangoProductsService(), ...requestOptions } = options;
  const result = await writer.requestJson<ProductUploadClaim>(principal, {
    method: "POST",
    path: PRODUCTS_UPLOADS_PATH,
    service: "writer",
    payload: { action: "claim", uploadId },
  }, requestOptions);
  return result.data;
}

export async function assembleProductUpload(
  principal: AppPrincipal,
  claim: Extract<ProductUploadClaim, { kind: "claimed" }>,
  options: Omit<DjangoProductsServiceOptions, "config"> & { writer?: ProductUploadWriter } = {},
) {
  const { writer = createDjangoProductsService(), ...requestOptions } = options;
  if (claim.chunks.length !== claim.upload.chunkCount
    || claim.chunks.some((chunk, index) => chunk.chunk_index !== index)) {
    throw new Error("SKU 快递费率上传分片集合不连续");
  }
  const bytes = new Uint8Array(claim.upload.fileSizeBytes);
  let offset = 0;
  for (const chunk of claim.chunks) {
    const downloaded = await writer.requestBytes(principal, {
      additionalHeaders: {
        "x-upload-id": claim.upload.id,
        "x-chunk-index": String(chunk.chunk_index),
        "x-upload-owner-token": claim.ownerToken,
      },
    }, requestOptions);
    if (downloaded.bytes.byteLength !== chunk.size_bytes
      || downloaded.sha256 !== chunk.sha256
      || await sha256(downloaded.bytes) !== chunk.sha256) {
      throw new Error("SKU 快递费率上传分片完整性校验失败");
    }
    bytes.set(downloaded.bytes, offset);
    offset += downloaded.bytes.byteLength;
  }
  if (offset !== bytes.byteLength) throw new Error("SKU 快递费率合并文件大小不正确");
  return bytes;
}

export async function finishProductUpload(
  principal: AppPrincipal,
  claim: Extract<ProductUploadClaim, { kind: "claimed" }>,
  result: Record<string, unknown>,
  options: Omit<DjangoProductsServiceOptions, "config"> & { writer?: ProductUploadWriter } = {},
) {
  const { writer = createDjangoProductsService(), ...requestOptions } = options;
  await writer.requestJson(principal, {
    method: "POST",
    path: PRODUCTS_UPLOADS_PATH,
    service: "writer",
    payload: {
      action: "finish",
      uploadId: claim.upload.id,
      ownerToken: claim.ownerToken,
      result,
    },
  }, requestOptions);
}

export async function releaseProductUpload(
  principal: AppPrincipal,
  claim: Extract<ProductUploadClaim, { kind: "claimed" }>,
  options: Omit<DjangoProductsServiceOptions, "config"> & { writer?: ProductUploadWriter } = {},
) {
  const { writer = createDjangoProductsService(), ...requestOptions } = options;
  await writer.requestJson(principal, {
    method: "POST",
    path: PRODUCTS_UPLOADS_PATH,
    service: "writer",
    payload: {
      action: "release",
      uploadId: claim.upload.id,
      ownerToken: claim.ownerToken,
    },
  }, requestOptions);
}
