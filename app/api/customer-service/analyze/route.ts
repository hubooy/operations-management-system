import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { analyzeCustomerServiceConversations } from "@/lib/customer-service/analysis";
import { resolveChatModel } from "@/lib/ai/assistant-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET() {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "客服 AI 分析");
    return Response.json({ configured: Boolean(await resolveChatModel(principal)) }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "读取 AI 分析状态失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "客服 AI 分析", "修改");
    const body = await request.json().catch(() => null) as { ids?: unknown } | null;
    const result = await analyzeCustomerServiceConversations(body?.ids, principal);
    const partial = result.incomplete > 0;
    return Response.json(
      { ok: !partial, ...result },
      { status: partial ? 207 : 200, headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "AI 客服分析失败。", { headers: { "cache-control": "no-store" } });
  }
}
