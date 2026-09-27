import {
  SALES_IMPORT_VERIFY_PATH,
  requestDjangoSalesService,
} from "@/lib/django/sales-writer";
import { validateSalesImportDateRange } from "@/lib/sales/import-service";
import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import { safeApiErrorResponse } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "销售导入校验");
    const source = new URL(request.url).searchParams;
    const target = new URLSearchParams();
    if (source.get("policyOnly") === "1") {
      target.set("policyOnly", "1");
    } else {
      const requestedStartDate = source.get("startDate") ?? "";
      const requestedEndDate = source.get("endDate") ?? "";
      const dateRange = validateSalesImportDateRange(requestedStartDate, requestedEndDate);
      if (!dateRange.ok) {
        return Response.json(
          { error: "startDate 和 endDate 必须是有效的 YYYY-MM-DD，且开始日期不能晚于结束日期。" },
          { status: 400, headers: { "cache-control": "no-store" } },
        );
      }
      target.set("startDate", dateRange.startDate);
      target.set("endDate", dateRange.endDate);
      const batchId = source.get("batchId")?.trim();
      if (batchId) target.set("batchId", batchId.slice(0, 200));
    }
    const result = await requestDjangoSalesService(principal, {
      method: "GET",
      path: SALES_IMPORT_VERIFY_PATH,
      query: target,
      service: "reader",
    });
    return Response.json(result.data, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "销售导入校验读取失败。", { headers: { "cache-control": "no-store" } });
  }
}
