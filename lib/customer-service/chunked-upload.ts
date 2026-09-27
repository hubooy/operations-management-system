import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoCustomerService,
  CUSTOMER_SERVICE_UPLOAD_CHUNK_PATH,
  CUSTOMER_SERVICE_UPLOADS_PATH,
} from "@/lib/django/customer-service";
import { PublicApiError } from "@/lib/http/api-error";

export const CUSTOMER_SERVICE_UPLOAD_CHUNK_BYTES = 1024 * 1024;

export type CustomerServiceUploadSession = {
  id: string; kind: "session" | "chat"; fingerprint: string; fileName: string;
  fileSizeBytes: number; chunkSizeBytes: number; chunkCount: number;
  receivedChunkCount: number; receivedBytes: number; status: string;
  ownerGeneration: number; expiresAt: string;
};

type CustomerServiceUploadClaim =
  | { kind: "claimed"; ownerToken: string; upload: CustomerServiceUploadSession }
  | { kind: "completed"; upload: CustomerServiceUploadSession; result: unknown };

function client() { return createDjangoCustomerService(); }

export async function beginCustomerServiceUpload(principal: AppPrincipal, input: { kind: "session" | "chat"; fileName: string; fileSizeBytes: number; chunkCount: number; fingerprint: string }) {
  const result = await client().requestJson<{ upload: CustomerServiceUploadSession }>(principal, {
    method: "POST", path: CUSTOMER_SERVICE_UPLOADS_PATH, service: "writer", payload: { action: "init", ...input },
  });
  return result.data.upload;
}

export async function receiveCustomerServiceUploadChunk(principal: AppPrincipal, input: { uploadId: string; chunkIndex: number; bytes: Uint8Array }) {
  const result = await client().requestJson<{ upload: CustomerServiceUploadSession }>(principal, {
    method: "PUT", path: CUSTOMER_SERVICE_UPLOAD_CHUNK_PATH, service: "writer", bodyBytes: input.bytes,
    additionalHeaders: { "x-upload-id": input.uploadId, "x-chunk-index": String(input.chunkIndex) },
  });
  return result.data.upload;
}

export async function claimCustomerServiceUpload(principal: AppPrincipal, uploadId: string): Promise<CustomerServiceUploadClaim> {
  const result = await client().requestJson<CustomerServiceUploadClaim>(principal, {
    method: "POST", path: CUSTOMER_SERVICE_UPLOADS_PATH, service: "writer", payload: { action: "claim", uploadId },
  });
  return result.data;
}

async function digest(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", input))].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export async function assembleCustomerServiceUpload(principal: AppPrincipal, claim: Extract<CustomerServiceUploadClaim, { kind: "claimed" }>) {
  const bytes = new Uint8Array(claim.upload.fileSizeBytes);
  let offset = 0;
  for (let index = 0; index < claim.upload.chunkCount; index += 1) {
    const chunk = await client().requestBytes(principal, { "x-upload-id": claim.upload.id, "x-chunk-index": String(index), "x-upload-owner-token": claim.ownerToken });
    if (await digest(chunk.bytes) !== chunk.sha256 || offset + chunk.bytes.byteLength > bytes.byteLength) throw new PublicApiError(422, "invalid_request", "客服上传分片完整性校验失败，请重新上传。");
    bytes.set(chunk.bytes, offset);
    offset += chunk.bytes.byteLength;
  }
  if (offset !== bytes.byteLength) throw new PublicApiError(422, "invalid_request", "客服上传文件大小校验失败，请重新上传。");
  return bytes;
}

export async function finishCustomerServiceUpload(principal: AppPrincipal, uploadId: string, ownerToken: string, resultPayload: Record<string, unknown>) {
  await client().requestJson(principal, { method: "POST", path: CUSTOMER_SERVICE_UPLOADS_PATH, service: "writer", payload: { action: "finish", uploadId, ownerToken, result: resultPayload } });
}

export async function releaseCustomerServiceUpload(principal: AppPrincipal, uploadId: string, ownerToken: string) {
  await client().requestJson(principal, { method: "POST", path: CUSTOMER_SERVICE_UPLOADS_PATH, service: "writer", payload: { action: "release", uploadId, ownerToken } });
}
