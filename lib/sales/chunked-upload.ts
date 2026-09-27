import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  SALES_RAW_UPLOAD_CHUNK_PATH,
  SALES_RAW_UPLOADS_PATH,
  requestDjangoSalesService,
} from "@/lib/django/sales-writer";
import { PublicApiError } from "@/lib/http/api-error";
import type { SalesImportExecution } from "@/lib/sales/import-service";

// PostgreSQL is the sole authority for raw bytes, upload identity, state,
// ownership and chunk metadata.
export const SALES_UPLOAD_CHUNK_BYTES = 2 * 1024 * 1024;
export const MAX_CHUNKED_SALES_FILE_BYTES = 128 * 1024 * 1024;

function uploadRequestError(status: 400 | 404 | 409 | 413 | 422, message: string) {
  const code = status === 404
    ? "not_found"
    : status === 409
      ? "conflict"
      : status === 413
        ? "payload_too_large"
        : "invalid_request";
  return new PublicApiError(status, code, message);
}

export type SalesUploadChunk = {
  chunkIndex: number;
  objectKey: string;
  sizeBytes: number;
  sha256: string;
};

export type SalesUploadSession = {
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
  ownerToken?: string | null;
  ownerGeneration: number;
  resultBatchId?: string | null;
  result?: SalesImportExecution;
  chunks?: SalesUploadChunk[];
  discardedObjectKey?: string | null;
};

export type SalesUploadClaim =
  | { kind: "claimed"; session: SalesUploadSession }
  | { kind: "completed"; session: SalesUploadSession; result: SalesImportExecution };

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return crypto.subtle.digest("SHA-256", input);
}

function bytesToBase64(bytes: Uint8Array) {
  let binary = "";
  for (let offset = 0; offset < bytes.byteLength; offset += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32_768));
  }
  return btoa(binary);
}

function base64ToBytes(value: string) {
  if (!value || value.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) {
    throw uploadRequestError(422, "PostgreSQL 分片内容编码无效，请重新上传该文件");
  }
  let binary: string;
  try {
    binary = atob(value);
  } catch {
    throw uploadRequestError(422, "PostgreSQL 分片内容编码无效，请重新上传该文件");
  }
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  if (bytesToBase64(bytes) !== value) {
    throw uploadRequestError(422, "PostgreSQL 分片内容编码无效，请重新上传该文件");
  }
  return bytes;
}

function assertSession(value: unknown): SalesUploadSession {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Django 返回的销售上传会话格式无效");
  }
  const session = value as Partial<SalesUploadSession>;
  if (typeof session.id !== "string" || !session.id
    || typeof session.fileName !== "string"
    || !Number.isSafeInteger(session.fileSizeBytes)
    || !Number.isSafeInteger(session.chunkSizeBytes)
    || !Number.isSafeInteger(session.chunkCount)
    || !Array.isArray(session.receivedChunkIndexes)
    || !Number.isSafeInteger(session.receivedBytes)
    || typeof session.status !== "string"
    || !Number.isSafeInteger(session.ownerGeneration)
    || typeof session.expiresAt !== "string") {
    throw new Error("Django 返回的销售上传会话格式无效");
  }
  return session as SalesUploadSession;
}

async function readSalesUpload(principal: AppPrincipal, uploadId: string): Promise<SalesUploadSession> {
  const result = await requestDjangoSalesService<SalesUploadSession>(principal, {
    method: "GET",
    path: SALES_RAW_UPLOADS_PATH,
    query: new URLSearchParams({ uploadId }),
    // Upload sessions are writer-owned coordination state, even for reads.
    // The projection reader intentionally has no access to these tables.
    service: "writer",
  });
  return assertSession(result.data);
}

export async function beginSalesUpload(principal: AppPrincipal, input: {
  fileName: string;
  fileSizeBytes: number;
  chunkCount: number;
  fingerprint: string;
  expectedStartDate: string;
  expectedEndDate: string;
  expectedChannels: readonly string[] | null;
}): Promise<SalesUploadSession> {
  if (!input.fileName.toLowerCase().endsWith(".xlsx")) throw uploadRequestError(422, "仅支持 .xlsx 格式的销售单明细账");
  if (!Number.isSafeInteger(input.fileSizeBytes) || input.fileSizeBytes <= 0) throw uploadRequestError(400, "文件大小无效");
  if (input.fileSizeBytes > MAX_CHUNKED_SALES_FILE_BYTES) throw uploadRequestError(413, "单个报表最大支持 128MB");
  const expectedCount = Math.ceil(input.fileSizeBytes / SALES_UPLOAD_CHUNK_BYTES);
  if (!Number.isSafeInteger(input.chunkCount) || input.chunkCount !== expectedCount) throw uploadRequestError(400, "分片数量与文件大小不一致");
  if (!input.fingerprint || input.fingerprint.length > 255) throw uploadRequestError(400, "上传指纹无效");

  await sweepExpiredSalesUploads(principal).catch(() => undefined);

  const result = await requestDjangoSalesService<{ upload: SalesUploadSession }>(principal, {
    method: "POST",
    path: SALES_RAW_UPLOADS_PATH,
    service: "writer",
    payload: {
      action: "init",
      fileName: input.fileName,
      fileSizeBytes: input.fileSizeBytes,
      chunkCount: input.chunkCount,
      fingerprint: input.fingerprint,
      expectedStartDate: input.expectedStartDate,
      expectedEndDate: input.expectedEndDate,
      expectedChannels: input.expectedChannels,
    },
  });
  return assertSession(result.data.upload);
}

export async function sweepExpiredSalesUploads(principal: AppPrincipal) {
  const result = await requestDjangoSalesService<{
    sweep: { items: Array<{
      id: string;
      ownerGeneration: number;
      cleanupToken: string;
    }> };
  }>(principal, {
    method: "POST",
    path: SALES_RAW_UPLOADS_PATH,
    service: "writer",
    payload: { action: "sweep", limit: 10 },
  });
  const items = result.data.sweep?.items;
  if (!Array.isArray(items)) throw new Error("Django 返回的过期上传清单无效");
  for (const item of items) {
    if (!item || typeof item.id !== "string"
      || !Number.isSafeInteger(item.ownerGeneration)
      || typeof item.cleanupToken !== "string"
      || !/^[a-f0-9]{32}$/.test(item.cleanupToken)) continue;
    try {
      await requestDjangoSalesService(principal, {
        method: "POST",
        path: SALES_RAW_UPLOADS_PATH,
        service: "writer",
        payload: {
          action: "purge",
          uploadId: item.id,
          ownerGeneration: item.ownerGeneration,
          cleanupToken: item.cleanupToken,
        },
      });
    } catch {
      // Keep the PostgreSQL manifest unless the generation-fenced purge
      // completes. The next init retries the same cleanup lease.
    }
  }
}

export async function receiveSalesUploadChunk(principal: AppPrincipal, input: {
  uploadId: string;
  chunkIndex: number;
  bytes: Uint8Array;
}): Promise<SalesUploadSession> {
  const upload = await readSalesUpload(principal, input.uploadId);
  if (new Date(upload.expiresAt).getTime() <= Date.now()) throw uploadRequestError(404, "上传会话已过期，请重新选择文件");
  if (upload.status === "completed") throw uploadRequestError(409, "该上传会话已完成");
  if (upload.status === "processing") throw uploadRequestError(409, "销售文件正在合并处理，不能继续覆盖分片");
  if (upload.status !== "uploading" && upload.status !== "ready") throw uploadRequestError(409, "上传会话状态无效，请重新选择文件");
  if (!Number.isSafeInteger(input.chunkIndex) || input.chunkIndex < 0 || input.chunkIndex >= upload.chunkCount) throw uploadRequestError(400, "分片序号无效");
  const expectedBytes = input.chunkIndex === upload.chunkCount - 1
    ? upload.fileSizeBytes - upload.chunkSizeBytes * (upload.chunkCount - 1)
    : upload.chunkSizeBytes;
  if (input.bytes.byteLength !== expectedBytes) throw uploadRequestError(422, "分片大小与预期不一致");

  const checksum = toHex(await sha256(input.bytes));
  const objectKey = `sales-upload/${upload.id}/${input.chunkIndex.toString().padStart(6, "0")}-${checksum}-${crypto.randomUUID()}`;
  try {
    const result = await requestDjangoSalesService<{ upload: SalesUploadSession }>(principal, {
      method: "PUT",
      path: SALES_RAW_UPLOADS_PATH,
      service: "writer",
      payload: {
        uploadId: upload.id,
        chunkIndex: input.chunkIndex,
        objectKey,
        sizeBytes: input.bytes.byteLength,
        sha256: checksum,
        contentBase64: bytesToBase64(input.bytes),
      },
    });
    const adopted = assertSession(result.data.upload);
    return adopted;
  } catch (error) {
    // The writer may have committed the bytes while its response was lost.
    // PostgreSQL metadata is authoritative for response-loss reconciliation.
    try {
      const reconciled = await readSalesUpload(principal, upload.id);
      const adopted = (reconciled.chunks ?? []).find((chunk) => chunk.chunkIndex === input.chunkIndex);
      if (adopted?.sha256 === checksum
        && adopted.sizeBytes === input.bytes.byteLength) {
        return reconciled;
      }
    } catch {
      // Preserve the original error when writer reconciliation is ambiguous.
    }
    throw error;
  }
}

export async function claimSalesUpload(principal: AppPrincipal, uploadId: string): Promise<SalesUploadClaim> {
  const result = await requestDjangoSalesService<{ upload: SalesUploadSession }>(principal, {
    method: "POST",
    path: SALES_RAW_UPLOADS_PATH,
    service: "writer",
    payload: { action: "claim", uploadId },
  });
  const session = assertSession(result.data.upload);
  if (session.status === "completed") {
    if (!session.result || session.result.ok !== true || !session.result.batch?.id) {
      throw new Error("Django 未返回已完成销售上传的权威结果");
    }
    return { kind: "completed", session, result: session.result };
  }
  if (typeof session.ownerToken !== "string" || !/^[a-f0-9]{32}$/.test(session.ownerToken)) {
    throw new Error("Django 未返回有效的销售上传 owner token");
  }
  return { kind: "claimed", session };
}

export async function assembleSalesUpload(principal: AppPrincipal, claimed: SalesUploadSession): Promise<{
  session: SalesUploadSession;
  bytes: Uint8Array;
}> {
  if (claimed.status !== "processing" || typeof claimed.ownerToken !== "string") {
    throw uploadRequestError(409, "销售上传会话尚未由本次请求接管");
  }
  const upload = await readSalesUpload(principal, claimed.id);
  if (new Date(upload.expiresAt).getTime() <= Date.now()) throw uploadRequestError(404, "上传会话已过期，请重新选择文件");
  if (upload.status !== "processing") throw uploadRequestError(409, "销售上传会话尚未进入处理状态");
  const chunks = upload.chunks ?? [];
  if (chunks.length !== upload.chunkCount || chunks.some((chunk, index) => chunk.chunkIndex !== index)) {
    throw uploadRequestError(409, "仍有分片尚未上传完成");
  }
  const bytes = new Uint8Array(upload.fileSizeBytes);
  let offset = 0;
  for (const chunk of chunks) {
    const result = await requestDjangoSalesService<{
      chunk: {
        chunkIndex: number;
        sizeBytes: number;
        sha256: string;
        contentBase64: string;
      };
    }>(principal, {
      method: "POST",
      path: SALES_RAW_UPLOAD_CHUNK_PATH,
      service: "writer",
      payload: {
        uploadId: upload.id,
        chunkIndex: chunk.chunkIndex,
        ownerToken: claimed.ownerToken,
      },
    });
    const stored = result.data.chunk;
    if (!stored || stored.chunkIndex !== chunk.chunkIndex
      || stored.sizeBytes !== chunk.sizeBytes || stored.sha256 !== chunk.sha256
      || typeof stored.contentBase64 !== "string") {
      throw uploadRequestError(422, "PostgreSQL 分片元数据不一致，请重新上传该文件");
    }
    const part = base64ToBytes(stored.contentBase64);
    if (part.byteLength !== chunk.sizeBytes) throw uploadRequestError(422, "分片完整性校验失败，请重新上传该文件");
    if (toHex(await sha256(part)) !== chunk.sha256) throw uploadRequestError(422, "分片校验码不一致，请重新上传该文件");
    bytes.set(part, offset);
    offset += part.byteLength;
  }
  if (offset !== bytes.byteLength) throw uploadRequestError(422, "合并后的文件大小不正确");
  return {
    session: { ...upload, ownerToken: claimed.ownerToken },
    bytes,
  };
}

export async function finishSalesUpload(
  principal: AppPrincipal,
  uploadId: string,
  ownerToken: string,
  completed: boolean,
  resultBatchId?: string,
) {
  try {
    await requestDjangoSalesService<{ upload: SalesUploadSession }>(principal, {
      method: "POST",
      path: SALES_RAW_UPLOADS_PATH,
      service: "writer",
      payload: {
        action: "finish",
        uploadId,
        ownerToken,
        completed,
        ...(resultBatchId ? { resultBatchId } : {}),
      },
    });
  } catch (error) {
    const reconciled = await readSalesUpload(principal, uploadId).catch(() => null);
    const committed = completed
      ? reconciled?.status === "completed" && Boolean(resultBatchId)
        && reconciled.resultBatchId === resultBatchId
      : reconciled?.status === "ready";
    if (!committed) throw error;
  }
  if (!completed) return;
  await cleanupCompletedSalesUpload(principal, uploadId);
}

export async function cleanupCompletedSalesUpload(
  principal: AppPrincipal,
  uploadId: string,
) {
  try {
    await requestDjangoSalesService(principal, {
      method: "POST",
      path: SALES_RAW_UPLOADS_PATH,
      service: "writer",
      payload: { action: "cleanup", uploadId },
    });
  } catch {
    // PostgreSQL keeps the terminal session/chunk manifest. A completed replay
    // retries the same idempotent metadata-and-payload cleanup.
  }
}
