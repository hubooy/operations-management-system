import manifest from "@/backend/system_datasets/manifest.json";
import { aiEnvironment, aiHeaders } from "@/lib/django/ai-service";
import { salesGatewayConfigFromEnvironment } from "@/lib/django/sales-gateway";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { RegistryToolError } from "@/lib/ai/tool-registry-contract";
import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";

const readerKeys: Record<string, string> = {
  sales: "TERUISI_DJANGO_SALES_READER_BASE_URL",
  erp_reference: "TERUISI_DJANGO_ERP_READER_BASE_URL",
  finance: "TERUISI_DJANGO_FINANCE_READER_BASE_URL",
  netshop: "TERUISI_DJANGO_NETSHOP_READER_BASE_URL",
  market: "TERUISI_DJANGO_MARKET_READER_BASE_URL",
  products: "TERUISI_DJANGO_PRODUCTS_READER_BASE_URL",
  inventory: "TERUISI_DJANGO_INVENTORY_READER_BASE_URL",
  workflow: "TERUISI_DJANGO_WORKFLOW_READER_BASE_URL",
  customer_service: "TERUISI_DJANGO_CUSTOMER_SERVICE_READER_BASE_URL",
  access_control: "TERUISI_DJANGO_ACCESS_CONTROL_READER_BASE_URL",
  ai_assistant: "TERUISI_DJANGO_AI_READER_BASE_URL",
  bi: "TERUISI_DJANGO_BI_READER_BASE_URL",
};

export async function queryDatasetRecords(args: Record<string, unknown>, context: AiToolExecutionContext) {
  context.signal?.throwIfAborted();
  const spec = manifest.datasets.find(item => item.id === args.dataset);
  if (!spec || context.principal.role !== "admin" || context.principal.scope !== null) {
    throw new RegistryToolError("forbidden", "该记录数据集不可访问。");
  }
  let query: unknown;
  try { query = JSON.parse(String(args.queryJson)); } catch { throw new RegistryToolError("invalid_arguments", "queryJson 无效。"); }
  if (!query || typeof query !== "object" || Array.isArray(query)) throw new RegistryToolError("invalid_arguments", "query 必须为对象。");
  const environment = await aiEnvironment();
  const endpoint = spec.domain === "sales"
    ? salesGatewayConfigFromEnvironment(environment).djangoBaseUrl
    : environment[readerKeys[spec.domain]];
  const unavailable = () => new RegistryToolError("service_unavailable", "所属领域数据集 reader 未就绪。");
  let base: URL;
  try { base = new URL(endpoint ?? ""); } catch { throw unavailable(); }
  if (base.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(base.hostname)
      || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw unavailable();
  const path = "/api/ai/dataset-records";
  const body = JSON.stringify({ dataset: spec.id, query });
  if (new TextEncoder().encode(body).length > 18000) throw new RegistryToolError("payload_too_large", "数据集请求超限。");
  const headers = await aiHeaders({ secret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "", principal: context.principal,
    method: "POST", path, query: "", body, requestId: context.requestId });
  const result = await fetchBoundedJson({ url: new URL(path, base).toString(), init: { method: "POST", headers, body, cache: "no-store" },
    signal: context.signal, timeoutMs: 10000, maxBytes: 180000 });
  context.signal?.throwIfAborted();
  if (!result.response.ok) {
    if (result.response.status === 400) throw new RegistryToolError("invalid_arguments", "字段、筛选条件或分页游标无效。");
    if (result.response.status === 403) throw new RegistryToolError("forbidden", "数据集读取权限不足。");
    if (result.response.status === 413) throw new RegistryToolError("payload_too_large", "请缩小字段范围或分页大小。");
  }
  if (!result.response.ok || !result.data || typeof result.data !== "object" || Array.isArray(result.data)) throw unavailable();
  const data = result.data as Record<string, unknown>;
  if (data.dataset !== spec.id || data.sourceDomain !== spec.domain || !Array.isArray(data.rows)
      || data.rows.length > 100 || data.returned !== data.rows.length || data.consistency !== "live_per_page") throw unavailable();
  return data;
}
