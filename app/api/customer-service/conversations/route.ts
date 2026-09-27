import { deleteCustomerServiceConversation, getCustomerServiceConversationById, listCustomerServiceConversations, updateCustomerServiceConversationAnnotation } from "@/lib/customer-service/database";
import type { CustomerServiceAnnotationInput } from "@/lib/customer-service/contracts";
import { authorizationErrorResponse, requireAppPrincipal, requireUnrestrictedDataScope } from "@/lib/auth/authorization";
import { parsePositiveIntegerQuery, PublicApiError, requirePositiveSafeIntegerNumber, safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "客服会话");
    const url = new URL(request.url);
    const detailId = url.searchParams.get("id");
    if (detailId !== null) {
      const id = parsePositiveIntegerQuery(detailId, 1, "会话 ID");
      return Response.json({ item: await getCustomerServiceConversationById(id, principal, { signal: request.signal }) }, { headers: { "cache-control": "no-store" } });
    }
    const page = url.searchParams.get("page");
    const pageSize = url.searchParams.get("pageSize");
    const payload = await listCustomerServiceConversations({
      shopNames: url.searchParams.getAll("shopName"),
      startDate: url.searchParams.get("startDate"),
      endDate: url.searchParams.get("endDate"),
      agents: url.searchParams.getAll("agent"),
      statuses: url.searchParams.getAll("status"),
      robotScopes: url.searchParams.getAll("robotScope"),
      problemTypes: url.searchParams.getAll("problemType"),
      conversionStatuses: url.searchParams.getAll("conversionStatus"),
      categories: url.searchParams.getAll("category"),
      query: url.searchParams.get("query"),
      skuIds: url.searchParams.get("skuIds"),
      spuIds: url.searchParams.get("spuIds"),
      page: parsePositiveIntegerQuery(page, 1, "page", 10_000),
      pageSize: parsePositiveIntegerQuery(pageSize, 30, "pageSize", 100),
      includeOptions: url.searchParams.get("includeOptions") !== "false",
    }, principal, { signal: request.signal });
    return Response.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) { const auth = authorizationErrorResponse(error); if (auth) return auth; return safeApiErrorResponse(error, "读取客服会话失败。", { headers: { "cache-control": "no-store" } }); }
}

export async function PATCH(request: Request) {
  try {
    const principal = await requireAppPrincipal(["operator", "admin"]);
    requireUnrestrictedDataScope(principal, "客服会话", "修改");
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    if (!body) throw new PublicApiError(400, "invalid_request", "请提供客服标注内容。");
    const id = requirePositiveSafeIntegerNumber(body.id, "会话 ID");
    const expectedVersion = requirePositiveSafeIntegerNumber(body.expectedVersion, "expectedVersion");
    const annotationKeys = ["robotScope", "problemType", "conversionStatus", "serviceIssues", "summaryText"] as const;
    if (!annotationKeys.some((key) => body[key] !== undefined)) {
      throw new PublicApiError(400, "invalid_request", "没有可保存的标注内容。");
    }
    const annotation = {
      ...(typeof body.robotScope === "string" ? { robotScope: body.robotScope } : {}),
      ...(typeof body.problemType === "string" ? { problemType: body.problemType } : {}),
      ...(typeof body.conversionStatus === "string" ? { conversionStatus: body.conversionStatus } : {}),
      ...(typeof body.serviceIssues === "string" ? { serviceIssues: body.serviceIssues } : {}),
      ...(typeof body.summaryText === "string" ? { summaryText: body.summaryText } : {}),
      analysisSource: "manual" as const,
    };
    return Response.json({ ok: true, ...(await updateCustomerServiceConversationAnnotation(id, annotation as CustomerServiceAnnotationInput, expectedVersion, principal, { signal: request.signal })) }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "保存客服标注失败。", { headers: { "cache-control": "no-store" } });
  }
}

export async function DELETE(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "客服会话", "删除");
    const body = await request.json().catch(() => null) as { id?: unknown; expectedVersion?: unknown } | null;
    if (!body) throw new PublicApiError(400, "invalid_request", "请提供需要删除的客服会话。");
    const id = requirePositiveSafeIntegerNumber(body.id, "会话 ID");
    const expectedVersion = requirePositiveSafeIntegerNumber(body.expectedVersion, "expectedVersion");
    const reason = typeof (body as Record<string, unknown>).reason === "string" ? String((body as Record<string, unknown>).reason) : "";
    return Response.json({ ok: true, ...(await deleteCustomerServiceConversation(id, expectedVersion, principal, reason, { signal: request.signal })) }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const auth = authorizationErrorResponse(error); if (auth) return auth;
    return safeApiErrorResponse(error, "删除客服会话失败。", { headers: { "cache-control": "no-store" } });
  }
}
