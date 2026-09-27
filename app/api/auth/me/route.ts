import {
  authorizationErrorResponse,
  requireAppPrincipal,
} from "@/lib/auth/authorization";

const roleLabels = {
  viewer: "查看者",
  analyst: "分析员",
  operator: "运营人员",
  admin: "管理员",
} as const;

export async function GET() {
  try {
    const principal = await requireAppPrincipal();
    return Response.json(
      {
        user: {
          email: principal.email,
          displayName: principal.displayName,
          role: principal.role,
          roleLabel: roleLabels[principal.role],
          scopeRestricted: principal.scope !== null,
        },
      },
      { headers: { "cache-control": "no-store" } },
    );
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return Response.json(
      { error: "读取登录身份失败" },
      { status: 500, headers: { "cache-control": "no-store" } },
    );
  }
}
