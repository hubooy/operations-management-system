import { requireAppPrincipal, requireUnrestrictedDataScope, authorizationErrorResponse } from "@/lib/auth/authorization";
import { createDjangoInventoryService, INVENTORY_GUANGDONG_PATH, DjangoInventoryServiceResponseError } from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { parseWatchWorkbook, monitorWorkbook, watchWorkbook } from "./guangdong-workbook";
import type { GuangdongMonitor, GuangdongWatchRow } from "./guangdong-contract";

const READ = new Set(["", "watchlist", "products", "suppliers", "export", "template"]);
const MAX_BYTES = 4 * 1024 * 1024;

export async function guangdongRoute(request: Request, operation = "") {
  try {
    const method = request.method;
    const allowed = method === "GET" ? READ.has(operation) : method === "POST" ? ["preview", "import", "file-preview"].includes(operation) : method === "PATCH" && ["suppliers", "items"].includes(operation);
    if (!allowed) return Response.json({ error: "不支持的广东监控操作" }, { status: 405 });
    const writeAccess = method !== "GET";
    const principal = await requireAppPrincipal(writeAccess ? ["operator", "admin"] : ["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "广东入仓库存监控");
    const params = new URL(request.url).searchParams;
    if (operation === "template") return excelResponse(watchWorkbook([]), "广东监控清单模板.xlsx");
    const gateway = createDjangoInventoryService();
    let payload: Record<string, unknown> | undefined;
    if (method !== "GET") {
      if (Number(request.headers.get("content-length")) > MAX_BYTES) return Response.json({ error: "文件或请求不能超过4MiB" }, { status: 413 });
      const reader = request.body?.getReader();
      if (!reader) return Response.json({ error: "请求不能为空" }, { status: 400 });
      const chunks: Uint8Array[] = []; let length = 0;
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        length += value.length;
        if (length > MAX_BYTES) { await reader.cancel(); return Response.json({ error: "文件或请求不能超过4MiB" }, { status: 413 }); }
        chunks.push(value);
      }
      const bytes = new Uint8Array(length); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      if (operation === "file-preview") {
        const rawHash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((v) => v.toString(16).padStart(2, "0")).join("");
        try { payload = { rows: parseWatchWorkbook(bytes.buffer) }; }
        catch (error) {
          // Rejections have no business fingerprint or watchlist writes, only an audit attempt.
          try { await gateway.requestJson(principal, { method: "POST", path: `${INVENTORY_GUANGDONG_PATH}/import`, service: "writer", payload: { action: "reject", source: "Excel预校验", rawHash } }); }
          catch (auditError) { if (!(auditError instanceof DjangoInventoryServiceResponseError) || auditError.status !== 400) throw auditError; }
          return Response.json({ error: error instanceof Error ? error.message : "工作簿格式无效" }, { status: 400 });
        }
        const result = await gateway.requestJson<Record<string, unknown>>(principal, { method: "POST", path: `${INVENTORY_GUANGDONG_PATH}/preview`, service: "reader", payload }, { signal: request.signal });
        return Response.json({ ...result.data, rawHash }, { headers: { "cache-control": "no-store" } });
      }
      if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ error: "请求须使用JSON" }, { status: 415 });
      try { payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { return Response.json({ error: "JSON内容无效" }, { status: 400 }); }
    }
    const result = await gateway.requestJson<Record<string, unknown>>(principal, {
      method: method as "GET" | "POST" | "PATCH", path: INVENTORY_GUANGDONG_PATH + (operation ? `/${operation}` : ""),
      service: method === "GET" || operation === "preview" ? "reader" : "writer", rawQuery: params.toString(), payload,
    }, { signal: request.signal });
    if (operation === "export") {
      const output = params.get("kind") === "watchlist" ? watchWorkbook(result.data.items as GuangdongWatchRow[]) : monitorWorkbook(result.data as unknown as GuangdongMonitor);
      return excelResponse(output, params.get("kind") === "watchlist" ? "广东监控清单.xlsx" : "广东入仓监控.xlsx");
    }
    return Response.json(result.data, { headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision } });
  } catch (error) {
    return authorizationErrorResponse(error) ?? safeApiErrorResponse(error, "广东监控操作失败", { headers: { "cache-control": "no-store" } });
  }
}

function excelResponse(bytes: Uint8Array, filename: string) {
  return new Response(new Uint8Array(bytes), { headers: { "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`, "cache-control": "no-store" } });
}
