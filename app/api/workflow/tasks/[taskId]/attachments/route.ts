import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { drainWorkflowAttachmentCleanup, enqueueWorkflowAttachmentCleanup } from "@/lib/workflow/attachment-cleanup";
import {
  deleteWorkflowAttachmentObject,
  MAX_WORKFLOW_ATTACHMENT_BYTES,
  putWorkflowAttachment,
  validateWorkflowAttachment,
  workflowAttachmentObjectKey,
} from "@/lib/workflow/attachment-storage";
import { encodedWorkflowResource, workflowServiceResponse } from "@/lib/workflow/django-api";

type Context = { params: Promise<{ taskId: string }> };
const collectionPath = (taskId: string) => `/api/workflow/tasks/${encodedWorkflowResource(taskId)}/attachments`;
const itemPath = (taskId: string, attachmentId: string) => `${collectionPath(taskId)}/${encodedWorkflowResource(attachmentId)}`;
const routeError = (error: unknown, fallback: string) => authorizationErrorResponse(error) ?? safeApiErrorResponse(error, fallback);

export async function GET(request: Request, context: Context) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项附件"); await getWorkflowBackendMode();
    const { taskId } = await context.params;
    return workflowServiceResponse(await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path: collectionPath(taskId), service: "reader" }, { signal: request.signal }));
  } catch (error) { return routeError(error, "读取附件失败。"); }
}

export async function POST(request: Request, context: Context) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项附件", "修改"); await getWorkflowBackendMode();
    const rawContentLength = request.headers.get("content-length");
    if (rawContentLength === null) return Response.json({ error: "上传附件必须声明内容长度", code: "length_required" }, { status: 411, headers: { "cache-control": "no-store" } });
    if (!/^\d+$/.test(rawContentLength)) return Response.json({ error: "内容长度无效", code: "invalid_request" }, { status: 400, headers: { "cache-control": "no-store" } });
    const contentLength = Number(rawContentLength);
    if (!Number.isSafeInteger(contentLength) || contentLength > MAX_WORKFLOW_ATTACHMENT_BYTES + 64 * 1024) return Response.json({ error: "单个附件不能超过 10MB", code: "payload_too_large" }, { status: 413, headers: { "cache-control": "no-store" } });
    await drainWorkflowAttachmentCleanup(principal, undefined, { signal: request.signal });
    const form = await request.formData(); const file = form.get("file");
    if (!(file instanceof File)) return Response.json({ error: "请选择要上传的附件", code: "invalid_request" }, { status: 400, headers: { "cache-control": "no-store" } });
    const { taskId } = await context.params; const validated = await validateWorkflowAttachment(file); const attachmentId = crypto.randomUUID();
    const objectKey = workflowAttachmentObjectKey(taskId, attachmentId);
    await putWorkflowAttachment(objectKey, validated.bytes, validated.mimeType, taskId, attachmentId);
    try {
      const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, {
        method: "POST", path: collectionPath(taskId), service: "writer", payload: {
          id: attachmentId, fileName: validated.fileName, mimeType: validated.mimeType,
          sizeBytes: validated.bytes.byteLength, sha256: validated.sha256, objectKey,
        },
      }, { signal: request.signal });
      return workflowServiceResponse(result);
    } catch (error) {
      try { await deleteWorkflowAttachmentObject(objectKey); }
      catch { await enqueueWorkflowAttachmentCleanup(principal, objectKey).catch(() => undefined); }
      throw error;
    }
  } catch (error) { return routeError(error, "上传附件失败。"); }
}

export async function DELETE(request: Request, context: Context) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项附件", "修改"); await getWorkflowBackendMode();
    const { taskId } = await context.params; const attachmentId = new URL(request.url).searchParams.get("id") ?? "";
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, {
      method: "DELETE", path: itemPath(taskId, attachmentId), service: "writer",
    }, { signal: request.signal });
    const objectKey = typeof result.data.cleanupObjectKey === "string" ? result.data.cleanupObjectKey : "";
    if (objectKey) await drainWorkflowAttachmentCleanup(principal, [objectKey], { signal: request.signal }).catch(() => undefined);
    return workflowServiceResponse(result, { ok: true });
  } catch (error) { return routeError(error, "删除附件失败。"); }
}
