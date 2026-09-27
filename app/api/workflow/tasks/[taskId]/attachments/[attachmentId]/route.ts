import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { readVerifiedWorkflowAttachment } from "@/lib/workflow/attachment-storage";
import { encodedWorkflowResource } from "@/lib/workflow/django-api";

function asciiFileName(name: string) { return name.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 120) || "attachment"; }
function metadata(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = (value as { item?: unknown }).item;
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const row = item as Record<string, unknown>;
  if (typeof row.fileName !== "string" || typeof row.mimeType !== "string" || typeof row.objectKey !== "string"
    || typeof row.sha256 !== "string" || !Number.isSafeInteger(row.sizeBytes)) return null;
  return row as { fileName: string; mimeType: string; objectKey: string; sha256: string; sizeBytes: number };
}

export async function GET(request: Request, context: { params: Promise<{ taskId: string; attachmentId: string }> }) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]); requireUnrestrictedDataScope(principal, "工作事项附件"); await getWorkflowBackendMode();
    const { taskId, attachmentId } = await context.params;
    const path = `/api/workflow/tasks/${encodedWorkflowResource(taskId)}/attachments/${encodedWorkflowResource(attachmentId)}`;
    const result = await createDjangoWorkflowService().requestJson<Record<string, unknown>>(principal, { method: "GET", path, service: "reader" }, { signal: request.signal });
    const item = metadata(result.data);
    if (!item) throw new Error("workflow_attachment_metadata_invalid");
    const bytes = await readVerifiedWorkflowAttachment({ objectKey: item.objectKey, taskId, attachmentId, sizeBytes: item.sizeBytes, sha256: item.sha256 });
    if (!bytes) return Response.json({ error: "附件不存在", code: "not_found" }, { status: 404, headers: { "cache-control": "private, no-store" } });
    const body = new Uint8Array(bytes.byteLength); body.set(bytes); const encoded = encodeURIComponent(item.fileName);
    return new Response(body.buffer, { headers: {
      "cache-control": "private, no-store", "content-type": item.mimeType, "content-length": String(body.byteLength),
      "content-disposition": `attachment; filename="${asciiFileName(item.fileName)}"; filename*=UTF-8''${encoded}`,
      "x-content-type-options": "nosniff", "x-workflow-data-revision": result.revision,
    } });
  } catch (error) { return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "下载附件失败。"); }
}
