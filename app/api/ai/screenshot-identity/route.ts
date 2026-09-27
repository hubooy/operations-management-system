import { requireAppPrincipal, requireUnrestrictedDataScope, authorizationErrorResponse } from "@/lib/auth/authorization";

/** Only the authenticated, unrestricted administrator may identify a capture profile. */
export async function GET() {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "系统页面截图");
    return Response.json({ email: principal.email }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return authorizationErrorResponse(error) ?? Response.json({ error: "身份核验失败" }, { status: 503 });
  }
}
