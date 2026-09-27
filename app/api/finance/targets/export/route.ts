import { requireAppPrincipal, requireUnrestrictedDataScope, authorizationErrorResponse } from "@/lib/auth/authorization";
import { createDjangoFinanceService, FINANCE_TARGETS_PATH } from "@/lib/django/finance-service";
import { buildAnnualTargetExportWorkbook, type AnnualTargetExportRow } from "@/lib/finance/annual-target-workbook";
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";
import { requireDjangoRecord } from "@/lib/django/response-contract";

const XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const MAX_EXPORT_TARGETS = 10_000;
const PAGE_SIZE = 100;

type ExportedFinanceTarget = {
  id?: unknown;
  periodType?: unknown;
  platform?: unknown;
  shopName?: unknown;
  manager?: unknown;
  salesTargetCents?: unknown;
  profitTargetCents?: unknown;
  grossMarginBps?: unknown;
  promotionFeeRatioBps?: unknown;
};

function readExportRow(item: ExportedFinanceTarget): AnnualTargetExportRow | null {
  if (String(item.periodType ?? "") !== "year") return null;
  return {
    platform: String(item.platform ?? ""),
    shopName: String(item.shopName ?? ""),
    manager: String(item.manager ?? ""),
    salesTargetCents: Number(item.salesTargetCents ?? 0) || 0,
    profitTargetCents: Number(item.profitTargetCents ?? 0) || 0,
    grossMarginBps: Number(item.grossMarginBps ?? 0) || 0,
    promotionFeeRatioBps: Number(item.promotionFeeRatioBps ?? 0) || 0,
  };
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "经营目标");
    const params = new URL(request.url).searchParams;
    const yearValues = params.getAll("year");
    if (yearValues.length !== 1 || !/^(?:19|20|21)\d{2}$/.test(yearValues[0])) {
      throw new PublicApiError(400, "invalid_request", "导出年份必须且只能是 YYYY");
    }
    const year = yearValues[0];

    const rows: AnnualTargetExportRow[] = [];
    let page = 1;
    for (;;) {
      const query = new URLSearchParams({ view: "items", page: String(page), pageSize: String(PAGE_SIZE), year });
      const result = await createDjangoFinanceService().request<Record<string, unknown>>(
        principal,
        { method: "GET", path: FINANCE_TARGETS_PATH, query, service: "reader" },
        { signal: request.signal },
      );
      const payload = requireDjangoRecord(result.data);
      const items = payload.items;
      const pagination = payload.pagination as { truncated?: unknown } | undefined;
      if (!Array.isArray(items) || !pagination || typeof pagination !== "object") {
        throw new PublicApiError(503, "service_unavailable", "年度目标导出读取失败，请稍后重试。");
      }
      for (const item of items) {
        const row = readExportRow(item as ExportedFinanceTarget);
        if (row) rows.push(row);
      }
      if (rows.length > MAX_EXPORT_TARGETS) {
        throw new PublicApiError(413, "payload_too_large", `年度目标数量超过 ${MAX_EXPORT_TARGETS} 条导出上限。`);
      }
      if (pagination.truncated !== true || items.length === 0) break;
      page += 1;
      if (page > 200) {
        throw new PublicApiError(503, "service_unavailable", "年度目标导出分页异常，请稍后重试。");
      }
    }

    const workbook = buildAnnualTargetExportWorkbook(year, rows);
    return new Response(new Uint8Array(workbook), {
      headers: {
        "content-type": XLSX_CONTENT_TYPE,
        "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(`${year}年度目标导出.xlsx`)}`,
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    const authResponse = authorizationErrorResponse(error);
    if (authResponse) return authResponse;
    return safeApiErrorResponse(error, "年度目标导出失败。", { headers: { "cache-control": "no-store" } });
  }
}
