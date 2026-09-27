import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { requestDjangoAi } from "@/lib/django/ai-service";
import { createDjangoNetshopService, NETSHOP_PROMOTION_DIAGNOSTIC_PATH } from "@/lib/django/netshop-service";
import { netshopOutletsForPrincipal, netshopPlatformsForPrincipal } from "@/lib/netshop/access";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";
import { attachPromotionChatInterpretation, buildPromotionChatReport, interpretationFromChatMessage, previousPromotionChatPeriod } from "@/lib/jd/promotion-chat-delivery";
import { promotionDiagnosticHtml, promotionDiagnosticXlsx, type DiagnosticPeriod } from "@/lib/jd/promotion-diagnostic-report";

const ID = /^[A-Za-z0-9_-]{1,160}$/;
const MAX_FILE_BYTES = 12 * 1024 * 1024;

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "推广诊断对话报告");
    const params = new URL(request.url).searchParams;
    if ([...params.keys()].sort().join(",") !== "conversationId,format,messageId"
      || [...params.keys()].some((key) => params.getAll(key).length !== 1)) {
      throw new PublicApiError(400, "invalid_request", "推广诊断下载参数无效。");
    }
    const conversationId = params.get("conversationId") ?? "";
    const messageId = params.get("messageId") ?? "";
    const format = params.get("format");
    if (!ID.test(conversationId) || !ID.test(messageId) || (format !== "html" && format !== "xlsx")) {
      throw new PublicApiError(400, "invalid_request", "推广诊断下载标识或格式无效。");
    }
    const messages = await requestDjangoAi<Record<string, unknown>>(principal, {
      path: "/api/ai/chat", service: "reader",
      query: new URLSearchParams({ conversationId, messageId, pageSize: "1" }),
    }, { signal: request.signal });
    const { locator, text: interpretation } = interpretationFromChatMessage(messages.data, conversationId, messageId);
    netshopPlatformsForPrincipal(principal, ["京东"]);
    netshopOutletsForPrincipal(principal, [{ platform: "京东", shopName: locator.shopName }], ["京东"]);
    const reader = createDjangoNetshopService();
    const read = async (startDate: string, endDate: string) => {
      const query = new URLSearchParams({ platform: "京东", outlet: `京东\u001f${locator.shopName}`, startDate, endDate });
      return (await reader.request<DiagnosticPeriod>(principal,
        { method: "GET", path: NETSHOP_PROMOTION_DIAGNOSTIC_PATH, query, service: "reader" },
        { signal: request.signal })).data;
    };
    const previous = previousPromotionChatPeriod(locator);
    const [current, baseline] = await Promise.all([
      read(locator.startDate, locator.endDate),
      read(previous.startDate, previous.endDate),
    ]);
    const report = attachPromotionChatInterpretation(buildPromotionChatReport(locator, current, baseline), interpretation, messageId);
    const content = format === "html" ? promotionDiagnosticHtml(report) : promotionDiagnosticXlsx(report);
    const bytes = typeof content === "string" ? new TextEncoder().encode(content) : content;
    if (bytes.byteLength > MAX_FILE_BYTES) throw new PublicApiError(413, "payload_too_large", "推广诊断文件超过下载上限，请缩短日期范围。");
    const responseBody = new Uint8Array(bytes.byteLength);
    responseBody.set(bytes);
    const filename = `${locator.shopName}_${locator.startDate}_${locator.endDate}_推广诊断.${format}`;
    return new Response(responseBody.buffer, { headers: {
      "content-type": format === "html" ? "text/html; charset=utf-8" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`,
      "cache-control": "private, no-store",
      "x-content-type-options": "nosniff",
      "content-security-policy": "default-src 'none'; sandbox",
    } });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取推广诊断对话报告失败");
  }
}
