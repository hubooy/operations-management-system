import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { createDjangoWorkflowService, getWorkflowBackendMode } from "@/lib/django/workflow-service";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_IMAGE_BYTES = 300 * 1024;

type ImagePayload = {
  image?: {
    fileName?: unknown;
    mimeType?: unknown;
    sizeBytes?: unknown;
    sha256?: unknown;
    dataBase64?: unknown;
  };
};

function decodeBase64(value: string) {
  try { return Uint8Array.from(atob(value), (character) => character.charCodeAt(0)); }
  catch { throw new PublicApiError(409, "conflict", "产品图内容无效，请重新上传。"); }
}

function asciiFileName(name: string) {
  return name.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 120) || "product-image.jpg";
}

function hex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function GET(request: Request, context: { params: Promise<{ lineId: string }> }) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "新品销售跟进");
    await getWorkflowBackendMode();
    const { lineId } = await context.params;
    if (!UUID_RE.test(lineId)) throw new PublicApiError(400, "invalid_request", "新品产品线标识无效。");
    const path = `/api/workflow/new-product-lines/${lineId.toLowerCase()}/image`;
    const result = await createDjangoWorkflowService().requestJson<ImagePayload>(
      principal,
      { method: "GET", path, service: "reader" },
      { signal: request.signal },
    );
    const image = result.data.image;
    if (!image || typeof image.fileName !== "string" || image.mimeType !== "image/jpeg"
      || !Number.isSafeInteger(image.sizeBytes) || Number(image.sizeBytes) < 1 || Number(image.sizeBytes) > MAX_IMAGE_BYTES
      || typeof image.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(image.sha256)
      || typeof image.dataBase64 !== "string" || image.dataBase64.length > 410_000) {
      throw new PublicApiError(409, "conflict", "产品图元数据无效，请重新上传。");
    }
    const bytes = decodeBase64(image.dataBase64);
    if (bytes.byteLength !== image.sizeBytes || hex(await crypto.subtle.digest("SHA-256", bytes)) !== image.sha256) {
      throw new PublicApiError(409, "conflict", "产品图完整性校验失败，请重新上传。");
    }
    const body = new Uint8Array(bytes.byteLength); body.set(bytes);
    const encoded = encodeURIComponent(image.fileName);
    return new Response(body.buffer, { headers: {
      "cache-control": "private, max-age=3600", "content-type": "image/jpeg", "content-length": String(body.byteLength),
      "content-disposition": `inline; filename="${asciiFileName(image.fileName)}"; filename*=UTF-8''${encoded}`,
      "x-content-type-options": "nosniff", "x-workflow-data-revision": result.revision,
    } });
  } catch (error) {
    return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "读取新品产品线图片失败。");
  }
}
