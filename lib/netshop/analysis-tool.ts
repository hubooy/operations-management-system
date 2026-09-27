import type { AppPrincipal } from "@/lib/auth/authorization";
import { PublicApiError } from "@/lib/http/api-error";
import { createDjangoNetshopService, NETSHOP_ANALYSIS_RECORDS_PATH } from "@/lib/django/netshop-service";

export function requireAnalysisPrincipal(principal: AppPrincipal) {
  if (principal.role !== "admin" || principal.scope !== null) {
    throw new PublicApiError(403, "access_denied", "第一阶段分析明细仅向无范围限制管理员开放");
  }
}

export async function getNetshopAnalysisRecords(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const args = raw as Record<string, string | number>;
  const query = new URLSearchParams({ limit: "10" });
  for (const [key, value] of Object.entries(args)) query.set(key, String(value));
  const result = await createDjangoNetshopService().request<Record<string, unknown>>(
    principal, { method: "GET", path: NETSHOP_ANALYSIS_RECORDS_PATH, service: "reader", query }, { signal },
  );
  if (JSON.stringify(result.data).length > 38_000) {
    throw new PublicApiError(422, "payload_too_large", "分析页超过工具容量，请减小 limit 并从首页重新读取；不得截断后使用");
  }
  return result.data;
}
