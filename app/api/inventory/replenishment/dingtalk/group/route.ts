import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import {
  createDjangoInventoryService,
  INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH,
} from "@/lib/django/inventory-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";

const INVENTORY_REPLENISHMENT_DINGTALK_GROUP = "志高/特睿思备货计划群";
const INVENTORY_REPLENISHMENT_DINGTALK_ROBOT = "志高助手";

type GroupRequest = {
  action?: unknown;
  planIds?: unknown;
  previewToken?: unknown;
};

function invalid(message: string) {
  return Response.json({ ok: false, message }, { status: 400, headers: { "cache-control": "no-store" } });
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "备货计划", "发送钉钉群消息");
    const body = await request.json().catch(() => null) as GroupRequest | null;
    if (!body || typeof body !== "object" || !Array.isArray(body.planIds)
      || !["preview", "send"].includes(String(body.action))) {
      return invalid("备货群消息请求无效");
    }
    const planIds = body.planIds;
    if (planIds.length < 1 || planIds.length > 50
      || planIds.some((id) => typeof id !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(id.trim()))) {
      return invalid("请选择 1 到 50 条有效备货计划");
    }
    if (body.action === "send" && (typeof body.previewToken !== "string" || !/^[0-9a-f]{64}$/.test(body.previewToken))) {
      return invalid("请先预览并确认本次钉钉消息");
    }

    const payload = {
      action: body.action as "preview" | "send",
      planIds: planIds.map((id) => String(id).trim()),
      targetGroupName: INVENTORY_REPLENISHMENT_DINGTALK_GROUP,
      robotName: INVENTORY_REPLENISHMENT_DINGTALK_ROBOT,
      ...(body.action === "send" ? { previewToken: body.previewToken as string } : {}),
    };
    const result = await createDjangoInventoryService().requestJson<Record<string, unknown>>(
      principal,
      { method: "POST", path: INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH, service: "writer", payload },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      status: result.status,
      headers: { "cache-control": "no-store", "x-inventory-data-revision": result.revision },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "备货群消息处理失败。", { headers: { "cache-control": "no-store" } });
  }
}
