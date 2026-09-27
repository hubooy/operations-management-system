import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoErpReferenceService,
  ERP_REFERENCE_UPLOADS_PATH,
  ERP_REFERENCE_UPLOAD_CHUNK_PATH,
} from "@/lib/django/erp-reference-service";
import { PublicApiError } from "@/lib/http/api-error";

export const ERP_UPLOAD_CHUNK_BYTES = 1024 * 1024;
export const MAX_CHUNKED_ERP_FILE_BYTES = 20 * 1024 * 1024;
export type ErpUploadSource = "products" | "combos";

export type DjangoErpUploadSession = {
  id: string;
  source: ErpUploadSource;
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
  upload: DjangoErpUploadSession;
  chunks?: Array<{ chunk_index: number; size_bytes: number; sha256: string }>;
  result?: Record<string, unknown>;
};

function unavailable(message: string) {
  return new PublicApiError(503, "service_unavailable", message);
}

function checkedSession(value: unknown): DjangoErpUploadSession {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw unavailable("ERP 上传会话响应无效");
  const item = value as Record<string, unknown>;
  if (typeof item.id !== "string" || !/^[0-9a-f-]{36}$/i.test(item.id)
    || (item.source !== "products" && item.source !== "combos")
    || typeof item.fileName !== "string"
    || !["fileSizeBytes", "chunkSizeBytes", "chunkCount", "receivedChunkCount", "receivedBytes", "ownerGeneration"]
      .every((key) => Number.isSafeInteger(item[key]) && Number(item[key]) >= 0)
    || !["uploading", "ready", "processing", "completed"].includes(String(item.status))) {
    throw unavailable("ERP 上传会话响应无效");
  }
  return item as unknown as DjangoErpUploadSession;
}

export async function beginDjangoErpUpload(principal: AppPrincipal, input: {
  source: ErpUploadSource; fileName: string; fileSizeBytes: number;
  chunkCount: number; fingerprint: string;
}, signal?: AbortSignal) {
  const result = await createDjangoErpReferenceService().requestJson<{ upload: DjangoErpUploadSession }>(principal, {
    method: "POST", path: ERP_REFERENCE_UPLOADS_PATH, service: "writer",
    payload: { action: "init", ...input },
  }, { signal });
  return checkedSession(result.data.upload);
}

export async function receiveDjangoErpUploadChunk(principal: AppPrincipal, input: {
  uploadId: string; chunkIndex: number; bytes: Uint8Array;
}, signal?: AbortSignal) {
  const result = await createDjangoErpReferenceService().requestJson<{ upload: DjangoErpUploadSession }>(principal, {
    method: "PUT", path: ERP_REFERENCE_UPLOAD_CHUNK_PATH, service: "writer",
    bodyBytes: input.bytes,
    additionalHeaders: { "x-upload-id": input.uploadId, "x-chunk-index": String(input.chunkIndex) },
  }, { signal });
  return checkedSession(result.data.upload);
}

export async function claimDjangoErpUpload(
  principal: AppPrincipal, uploadId: string, source: ErpUploadSource, signal?: AbortSignal,
) {
  const result = await createDjangoErpReferenceService().requestJson<ClaimResult>(principal, {
    method: "POST", path: ERP_REFERENCE_UPLOADS_PATH, service: "writer",
    payload: { action: "claim", uploadId, source },
  }, { signal });
  const session = checkedSession(result.data.upload);
  if (session.source !== source) throw unavailable("ERP 上传会话来源不一致");
  if (result.data.kind === "completed") {
    if (!result.data.result || typeof result.data.result !== "object") throw unavailable("ERP 上传完成结果无效");
    return { kind: "completed" as const, session, result: result.data.result };
  }
  if (!result.data.ownerToken || !Array.isArray(result.data.chunks)
    || result.data.chunks.length !== session.chunkCount) throw unavailable("ERP 上传接管响应无效");
  return { kind: "claimed" as const, session, ownerToken: result.data.ownerToken, chunks: result.data.chunks };
}

export async function assembleDjangoErpUpload(
  principal: AppPrincipal,
  claim: Extract<Awaited<ReturnType<typeof claimDjangoErpUpload>>, { kind: "claimed" }>,
  signal?: AbortSignal,
) {
  const output = new Uint8Array(claim.session.fileSizeBytes);
  let offset = 0;
  for (const chunk of [...claim.chunks].sort((left, right) => left.chunk_index - right.chunk_index)) {
    if (!Number.isSafeInteger(chunk.chunk_index) || chunk.chunk_index < 0
      || !Number.isSafeInteger(chunk.size_bytes) || chunk.size_bytes < 1
      || !/^[a-f0-9]{64}$/.test(chunk.sha256)) throw unavailable("ERP 上传分片清单无效");
    const received = await createDjangoErpReferenceService().requestBytes(principal, {
      "x-upload-id": claim.session.id, "x-chunk-index": String(chunk.chunk_index),
      "x-upload-owner-token": claim.ownerToken,
    }, { signal });
    if (received.bytes.byteLength !== chunk.size_bytes || received.sha256 !== chunk.sha256
      || offset + received.bytes.byteLength > output.byteLength) throw unavailable("ERP 上传分片回读校验失败");
    output.set(received.bytes, offset);
    offset += received.bytes.byteLength;
  }
  if (offset !== output.byteLength) throw unavailable("ERP 上传文件合并长度不一致");
  return output;
}

export async function finishDjangoErpUpload(principal: AppPrincipal, input: {
  uploadId: string; source: ErpUploadSource; ownerToken: string; result: Record<string, unknown>;
}, signal?: AbortSignal) {
  await createDjangoErpReferenceService().requestJson(principal, {
    method: "POST", path: ERP_REFERENCE_UPLOADS_PATH, service: "writer",
    payload: { action: "finish", ...input },
  }, { signal });
}

export async function releaseDjangoErpUpload(principal: AppPrincipal, input: {
  uploadId: string; source: ErpUploadSource; ownerToken: string;
}, signal?: AbortSignal) {
  await createDjangoErpReferenceService().requestJson(principal, {
    method: "POST", path: ERP_REFERENCE_UPLOADS_PATH, service: "writer",
    payload: { action: "release", ...input },
  }, { signal });
}
