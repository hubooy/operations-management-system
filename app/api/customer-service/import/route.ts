import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { CustomerServiceImportError, parseCustomerServiceImport } from "@/lib/customer-service/import-service";
import { planCustomerServiceImportPayloads, recordRejectedCustomerServiceImport, saveCustomerServiceImport } from "@/lib/customer-service/database";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";

const MAX_FILE_BYTES = 25 * 1024 * 1024;
async function digest(bytes: Uint8Array) { const copy = new Uint8Array(bytes); const hash = await crypto.subtle.digest("SHA-256", copy.buffer as ArrayBuffer); return [...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, "0")).join(""); }

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "客服数据", "导入");
    const form = await request.formData();
    const sessionFile = form.get("sessionFile"); const chatFile = form.get("chatFile"); const shopName = String(form.get("shopName") ?? "").trim();
    if (!shopName || shopName.length > 100) return Response.json({ ok: false, message: "请填写客服数据所属店铺。" }, { status: 400, headers: { "cache-control": "no-store" } });
    if (!(sessionFile instanceof File) || !(chatFile instanceof File)) return Response.json({ ok: false, message: "请同时选择会话记录 Excel 和聊天记录 LOG 文件。" }, { status: 400, headers: { "cache-control": "no-store" } });
    if (sessionFile.size === 0 || chatFile.size === 0 || sessionFile.size > MAX_FILE_BYTES || chatFile.size > MAX_FILE_BYTES) return Response.json({ ok: false, message: "文件不能为空且单个文件不得超过 25MB。" }, { status: 413, headers: { "cache-control": "no-store" } });
    if (!/\.xlsx$/i.test(sessionFile.name) || !/\.(log|txt)$/i.test(chatFile.name)) return Response.json({ ok: false, message: "会话记录必须为 .xlsx，聊天记录必须为 .log 或 .txt。" }, { status: 422, headers: { "cache-control": "no-store" } });
    const sessionBytes = new Uint8Array(await sessionFile.arrayBuffer()); const chatBytes = new Uint8Array(await chatFile.arrayBuffer());
    const sessionHash = await digest(sessionBytes); const chatHash = await digest(chatBytes);
    const requestedFileHash = await digest(new TextEncoder().encode(`${shopName}:${sessionHash}:${chatHash}`));
    let parsed: ReturnType<typeof parseCustomerServiceImport>;
    try {
      parsed = parseCustomerServiceImport(sessionBytes, new TextDecoder("utf-8", { fatal: true }).decode(chatBytes));
      if (parsed.conversations.length === 0) throw new CustomerServiceImportError("客服导入没有可保存的会话资料");
    } catch (error) {
      const message = error instanceof CustomerServiceImportError ? error.message : "客服文件解析失败";
      await recordRejectedCustomerServiceImport(principal, {
        rawFileHash: requestedFileHash,
        scopeHint: { shopName },
        errorCode: "CUSTOMER_SERVICE_PARSE_REJECTED",
        issues: [{ code: "CUSTOMER_SERVICE_PARSE_REJECTED", message }],
        fileName: `${sessionFile.name} + ${chatFile.name}`,
        fileSizeBytes: sessionFile.size + chatFile.size,
      });
      if (error instanceof CustomerServiceImportError) throw new PublicApiError(422, "invalid_request", message);
      throw error;
    }
    const resolvedShopName = parsed.conversations.some((item) => item.agent.startsWith("志高厨电")) ? "志高厨电" : shopName;
    const fileHash = await digest(new TextEncoder().encode(`${resolvedShopName}:${await digest(sessionBytes)}:${await digest(chatBytes)}`));
    try {
      planCustomerServiceImportPayloads(resolvedShopName, parsed.conversations);
    } catch (error) {
      if (!(error instanceof PublicApiError) || error.status !== 422) throw error;
      await recordRejectedCustomerServiceImport(principal, {
        rawFileHash: fileHash,
        scopeHint: { shopName: resolvedShopName },
        errorCode: "CUSTOMER_SERVICE_PUBLISH_BUDGET_REJECTED",
        issues: [{ code: "CUSTOMER_SERVICE_PUBLISH_BUDGET_REJECTED", message: error.message }],
        fileName: `${sessionFile.name} + ${chatFile.name}`.slice(0, 500),
        fileSizeBytes: sessionFile.size + chatFile.size,
      });
      throw error;
    }
    const saved = await saveCustomerServiceImport({ shopName: resolvedShopName, sessionFileName: sessionFile.name, chatFileName: chatFile.name, fileHash, fileSizeBytes: sessionFile.size + chatFile.size, parsed }, principal);
    return Response.json({ ok: true, status: saved.status, batch: saved.batch, summary: parsed.summary, ...saved.warningSummary, message: saved.status === "duplicate" ? "全部标准化客服资料与当前数据一致，未重复写入。" : `已导入 ${parsed.conversations.length} 条客服会话，其中 ${parsed.summary.matchedCount + parsed.summary.timeOnlyMatchedCount} 条已关联聊天记录。` }, { status: saved.status === "imported" ? 201 : 200, headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "客服数据导入失败。", { shape: "import", headers: { "cache-control": "no-store" } });
  }
}
