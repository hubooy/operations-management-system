import { PublicApiError } from "@/lib/http/api-error";

export const MAX_WORKFLOW_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const ATTACHMENT_PREFIX = "workflow-attachments";

const attachmentTypes: Record<string, { mimeTypes: readonly string[]; magic?: (bytes: Uint8Array) => boolean }> = {
  pdf: { mimeTypes: ["application/pdf"], magic: (b) => b[0] === 0x25 && b[1] === 0x50 && b[2] === 0x44 && b[3] === 0x46 },
  png: { mimeTypes: ["image/png"], magic: (b) => b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 },
  jpg: { mimeTypes: ["image/jpeg"], magic: (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  jpeg: { mimeTypes: ["image/jpeg"], magic: (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  webp: { mimeTypes: ["image/webp"], magic: (b) => String.fromCharCode(...b.slice(0, 4)) === "RIFF" && String.fromCharCode(...b.slice(8, 12)) === "WEBP" },
  xls: { mimeTypes: ["application/vnd.ms-excel"], magic: (b) => [0xd0, 0xcf, 0x11, 0xe0].every((v, i) => b[i] === v) },
  xlsx: { mimeTypes: ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"], magic: (b) => b[0] === 0x50 && b[1] === 0x4b },
  docx: { mimeTypes: ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"], magic: (b) => b[0] === 0x50 && b[1] === 0x4b },
  txt: { mimeTypes: ["text/plain"] },
  csv: { mimeTypes: ["text/csv", "application/csv", "text/plain"] },
};

let bucketOverride: R2Bucket | undefined;

export function setWorkflowAttachmentBucketForTest(bucket?: R2Bucket) {
  bucketOverride = bucket;
}

async function bucket() {
  if (bucketOverride) return bucketOverride;
  const { env } = await import("cloudflare:workers");
  if (!env.SALES_IMPORT_FILES) throw new PublicApiError(503, "service_unavailable", "附件存储暂不可用。");
  return env.SALES_IMPORT_FILES;
}

function invalid(message: string): never {
  throw new PublicApiError(400, "invalid_request", message);
}

function safeFileName(value: unknown) {
  if (typeof value !== "string" || !value.trim() || Array.from(value.trim()).length > 255) invalid("附件名称无效。");
  const raw = value.trim();
  if (raw !== raw.split(/[\\/]/).pop() || /[\u0000-\u001f\u007f]/.test(raw) || raw === "." || raw === "..") invalid("附件名称无效。");
  return raw;
}

function containsAscii(bytes: Uint8Array, text: string) {
  const target = new TextEncoder().encode(text);
  outer: for (let index = 0; index <= bytes.length - target.length; index += 1) {
    for (let offset = 0; offset < target.length; offset += 1) if (bytes[index + offset] !== target[offset]) continue outer;
    return true;
  }
  return false;
}

function hex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function validateWorkflowAttachment(file: File) {
  const fileName = safeFileName(file.name);
  if (!Number.isSafeInteger(file.size) || file.size <= 0) invalid("附件不能为空。");
  if (file.size > MAX_WORKFLOW_ATTACHMENT_BYTES) throw new PublicApiError(413, "payload_too_large", "单个附件不能超过 10MB。");
  const extension = fileName.includes(".") ? fileName.split(".").pop()!.toLowerCase() : "";
  const rule = attachmentTypes[extension]; const mimeType = file.type.toLowerCase();
  if (!rule || !rule.mimeTypes.includes(mimeType)) invalid("附件格式不受支持或文件类型与扩展名不一致。");
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (rule.magic && !rule.magic(bytes)) invalid("附件内容与声明的文件类型不一致。");
  if (extension === "xlsx" && (!containsAscii(bytes, "[Content_Types].xml") || !containsAscii(bytes, "xl/"))) invalid("附件内容不是有效的 XLSX 工作簿。");
  if (extension === "docx" && (!containsAscii(bytes, "[Content_Types].xml") || !containsAscii(bytes, "word/"))) invalid("附件内容不是有效的 DOCX 文档。");
  if (extension === "txt" || extension === "csv") {
    if (bytes.slice(0, Math.min(bytes.length, 4_096)).includes(0)) invalid("文本附件内容无效。");
    try { new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { invalid("文本附件必须使用 UTF-8 编码。"); }
  }
  return { fileName, mimeType, bytes, sha256: hex(await crypto.subtle.digest("SHA-256", bytes)) };
}

export function workflowAttachmentObjectKey(taskId: string, attachmentId: string) {
  return `${ATTACHMENT_PREFIX}/${taskId}/${attachmentId}`;
}

export function validWorkflowAttachmentObjectKey(taskId: string, attachmentId: string, objectKey: string) {
  return objectKey === workflowAttachmentObjectKey(taskId, attachmentId);
}

export async function putWorkflowAttachment(objectKey: string, bytes: Uint8Array, mimeType: string, taskId: string, attachmentId: string) {
  await (await bucket()).put(objectKey, bytes, {
    httpMetadata: { contentType: mimeType, cacheControl: "private, no-store" },
    customMetadata: { taskId, attachmentId },
  });
}

export async function deleteWorkflowAttachmentObject(objectKey: string) {
  if (!objectKey.startsWith(`${ATTACHMENT_PREFIX}/`)) throw new PublicApiError(409, "conflict", "附件对象键超出允许范围。");
  await (await bucket()).delete(objectKey);
}

export async function readVerifiedWorkflowAttachment(input: { objectKey: string; taskId: string; attachmentId: string; sizeBytes: number; sha256: string }) {
  if (!validWorkflowAttachmentObjectKey(input.taskId, input.attachmentId, input.objectKey)) {
    throw new PublicApiError(409, "conflict", "附件元数据与对象键不一致。");
  }
  const object = await (await bucket()).get(input.objectKey);
  if (!object) return null;
  const bytes = new Uint8Array(await object.arrayBuffer());
  const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
  if (bytes.byteLength !== input.sizeBytes || digest !== input.sha256) {
    throw new PublicApiError(409, "conflict", "附件完整性校验失败，请联系管理员。");
  }
  return bytes;
}
