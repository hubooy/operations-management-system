import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_REPLENISHMENT_IMPORT_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import {
  MAX_REPLENISHMENT_IMPORT_BYTES,
  parseReplenishmentWorkbook,
  replenishmentTemplateWorkbook,
} from "@/lib/inventory/replenishment-workbook";

const XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function errorResponse(status: number, message: string) {
  return Response.json({ ok: false, message }, { status, headers: { "cache-control": "no-store" } });
}

function templateResponse() {
  return new Response(new Uint8Array(replenishmentTemplateWorkbook()), {
    headers: {
      "content-type": XLSX_CONTENT_TYPE,
      "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent("备货计划导入模板.xlsx")}`,
      "cache-control": "no-store",
    },
  });
}

async function sha256Hex(bytes: ArrayBuffer | Uint8Array) {
  const source = bytes instanceof Uint8Array
    ? bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
    : bytes;
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", source))]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function readBoundedBody(request: Request) {
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_REPLENISHMENT_IMPORT_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes.buffer;
}

export async function GET() {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划", "导入");
    return templateResponse();
  } catch (error) {
    return authorizationErrorResponse(error)
      ?? safeApiErrorResponse(error, "下载备货计划导入模板失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划", "导入");
    const params = new URL(request.url).searchParams;
    if ([...params.keys()].some((key) => key !== "acknowledgeStale")) {
      return errorResponse(400, "备货计划导入包含未知参数");
    }
    const acknowledgeStale = params.get("acknowledgeStale") === "true";
    if (params.has("acknowledgeStale") && !["true", "false"].includes(params.get("acknowledgeStale") ?? "")) {
      return errorResponse(400, "acknowledgeStale 必须是 true 或 false");
    }
    const declared = Number(request.headers.get("content-length"));
    if (Number.isFinite(declared) && declared > MAX_REPLENISHMENT_IMPORT_BYTES) {
      return errorResponse(413, "工作簿不能超过4MiB");
    }
    const bytes = await readBoundedBody(request);
    if (!bytes) {
      return errorResponse(413, "工作簿不能超过4MiB");
    }
    if (bytes.byteLength < 1) {
      return errorResponse(400, "请选择要导入的工作簿");
    }
    let rows;
    try {
      rows = parseReplenishmentWorkbook(bytes);
    } catch (error) {
      return errorResponse(400, error instanceof Error ? error.message : "备货计划工作簿格式无效");
    }
    const fileSha256 = await sha256Hex(bytes);
    const actorBinding = await sha256Hex(new TextEncoder().encode(
      `${principal.email.trim().toLowerCase()}\n${fileSha256}\n${acknowledgeStale ? "stale-ok" : "fresh-only"}`,
    ));
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      {
        method: "POST",
        path: INVENTORY_REPLENISHMENT_IMPORT_PATH,
        service: "writer",
        payload: { rows, acknowledgeStale, fileSha256 },
      },
      { signal: request.signal, requestId: () => `replenishment-import:${actorBinding}` },
    );
    return Response.json(result.data, {
      status: result.status,
      headers: {
        "cache-control": "no-store",
        "x-inventory-data-revision": result.revision,
        ...(result.replayed ? { "x-teruisi-write-replay": "1" } : {}),
      },
    });
  } catch (error) {
    return authorizationErrorResponse(error)
      ?? safeApiErrorResponse(error, "导入备货计划失败。", { headers: { "cache-control": "no-store" } });
  }
}
