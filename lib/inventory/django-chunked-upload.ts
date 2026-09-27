import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_UPLOADS_PATH,
  INVENTORY_UPLOAD_CHUNK_PATH,
} from "@/lib/django/inventory-service";
import { PublicApiError } from "@/lib/http/api-error";

export const INVENTORY_UPLOAD_CHUNK_BYTES = 1024 * 1024;
export const MAX_CHUNKED_INVENTORY_FILE_BYTES = 20 * 1024 * 1024;

export type DjangoInventoryUploadSession = {
  id: string;
  dataset: "stock" | "age";
  snapshotDate: string;
  fileName: string;
  fileSizeBytes: number;
  chunkSizeBytes: number;
  chunkCount: number;
  receivedChunkCount: number;
  receivedBytes: number;
  status: "uploading" | "ready" | "processing" | "completed";
  ownerGeneration: number;
  expiresAt: string;
};

type ClaimResult = {
  kind: "claimed" | "completed";
  ownerToken?: string;
  upload: DjangoInventoryUploadSession;
  chunks?: Array<{ chunk_index: number; size_bytes: number; sha256: string }>;
  result?: Record<string, unknown>;
};

function unavailable(message: string) {
  return new PublicApiError(503, "service_unavailable", message);
}

function validSession(value: unknown): value is DjangoInventoryUploadSession {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const session = value as Record<string, unknown>;
  return typeof session.id === "string" && /^[0-9a-f-]{36}$/i.test(session.id)
    && (session.dataset === "stock" || session.dataset === "age")
    && typeof session.snapshotDate === "string" && /^\d{4}-\d{2}-\d{2}$/.test(session.snapshotDate)
    && typeof session.fileName === "string"
    && ["fileSizeBytes", "chunkSizeBytes", "chunkCount", "receivedChunkCount", "receivedBytes", "ownerGeneration"]
      .every((key) => Number.isSafeInteger(session[key]) && Number(session[key]) >= 0)
    && ["uploading", "ready", "processing", "completed"].includes(String(session.status));
}

function checkedSession(value: unknown) {
  if (!validSession(value)) throw unavailable("库存上传会话响应无效");
  return value;
}

export async function beginDjangoInventoryUpload(
  principal: AppPrincipal,
  input: {
    dataset: "stock" | "age";
    snapshotDate: string;
    fileName: string;
    fileSizeBytes: number;
    chunkCount: number;
    fingerprint: string;
  },
  signal?: AbortSignal,
) {
  const result = await createDjangoInventoryService().requestJson<{ upload: DjangoInventoryUploadSession }>(principal, {
    method: "POST",
    path: INVENTORY_UPLOADS_PATH,
    service: "writer",
    payload: { action: "init", ...input },
  }, { signal });
  return checkedSession(result.data.upload);
}

export async function receiveDjangoInventoryUploadChunk(
  principal: AppPrincipal,
  input: { uploadId: string; chunkIndex: number; bytes: Uint8Array },
  signal?: AbortSignal,
) {
  const result = await createDjangoInventoryService().requestJson<{ upload: DjangoInventoryUploadSession }>(principal, {
    method: "PUT",
    path: INVENTORY_UPLOAD_CHUNK_PATH,
    service: "writer",
    bodyBytes: input.bytes,
    additionalHeaders: {
      "x-upload-id": input.uploadId,
      "x-chunk-index": String(input.chunkIndex),
    },
  }, { signal });
  return checkedSession(result.data.upload);
}

export async function claimDjangoInventoryUpload(
  principal: AppPrincipal,
  uploadId: string,
  signal?: AbortSignal,
) {
  const result = await createDjangoInventoryService().requestJson<ClaimResult>(principal, {
    method: "POST",
    path: INVENTORY_UPLOADS_PATH,
    service: "writer",
    payload: { action: "claim", uploadId },
  }, { signal });
  const session = checkedSession(result.data.upload);
  if (result.data.kind === "completed") {
    if (!result.data.result || typeof result.data.result !== "object") throw unavailable("库存上传完成结果无效");
    return { kind: "completed" as const, session, result: result.data.result };
  }
  if (!result.data.ownerToken || !Array.isArray(result.data.chunks)
    || result.data.chunks.length !== session.chunkCount) throw unavailable("库存上传接管响应无效");
  return {
    kind: "claimed" as const,
    session,
    ownerToken: result.data.ownerToken,
    chunks: result.data.chunks,
  };
}

export async function assembleDjangoInventoryUpload(
  principal: AppPrincipal,
  claim: Extract<Awaited<ReturnType<typeof claimDjangoInventoryUpload>>, { kind: "claimed" }>,
  signal?: AbortSignal,
) {
  const output = new Uint8Array(claim.session.fileSizeBytes);
  let offset = 0;
  for (const chunk of [...claim.chunks].sort((left, right) => left.chunk_index - right.chunk_index)) {
    if (!Number.isSafeInteger(chunk.chunk_index) || chunk.chunk_index < 0
      || !Number.isSafeInteger(chunk.size_bytes) || chunk.size_bytes < 1
      || !/^[a-f0-9]{64}$/.test(chunk.sha256)) throw unavailable("库存上传分片清单无效");
    const received = await createDjangoInventoryService().requestBytes(principal, {
      "x-upload-id": claim.session.id,
      "x-chunk-index": String(chunk.chunk_index),
      "x-upload-owner-token": claim.ownerToken,
    }, { signal });
    if (received.bytes.byteLength !== chunk.size_bytes || received.sha256 !== chunk.sha256
      || offset + received.bytes.byteLength > output.byteLength) throw unavailable("库存上传分片回读校验失败");
    output.set(received.bytes, offset);
    offset += received.bytes.byteLength;
  }
  if (offset !== output.byteLength) throw unavailable("库存上传文件合并长度不一致");
  return output;
}

export async function finishDjangoInventoryUpload(
  principal: AppPrincipal,
  uploadId: string,
  ownerToken: string,
  result: Record<string, unknown>,
  signal?: AbortSignal,
) {
  await createDjangoInventoryService().requestJson(principal, {
    method: "POST",
    path: INVENTORY_UPLOADS_PATH,
    service: "writer",
    payload: { action: "finish", uploadId, ownerToken, result },
  }, { signal });
}

export async function releaseDjangoInventoryUpload(
  principal: AppPrincipal,
  uploadId: string,
  ownerToken: string,
  signal?: AbortSignal,
) {
  await createDjangoInventoryService().requestJson(principal, {
    method: "POST",
    path: INVENTORY_UPLOADS_PATH,
    service: "writer",
    payload: { action: "release", uploadId, ownerToken },
  }, { signal });
}
