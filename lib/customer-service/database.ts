import { requireUnrestrictedDataScope, type AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoCustomerService,
  CUSTOMER_SERVICE_CONVERSATIONS_PATH,
  CUSTOMER_SERVICE_IMPORTS_PATH,
  CUSTOMER_SERVICE_SNAPSHOTS_PATH,
} from "@/lib/django/customer-service";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
  type SalesConsumerResponseMap,
} from "@/lib/django/sales-consumer-reader";
import {
  createDjangoNetshopConsumerReader,
  type NetshopConsumerReader,
  type NetshopConsumerResponseMap,
} from "@/lib/django/netshop-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";
import {
  CustomerServiceImportError,
  summarizeCustomerServiceWarnings,
  validateCustomerServiceConversationMessages,
  type CustomerServiceParseResult,
} from "./import-service";
import {
  type CustomerServiceAnnotationInput,
  type CustomerServiceConversionStatus,
  type CustomerServiceProblemType,
  type CustomerServiceRobotScope,
} from "./contracts";
import {
  buildCustomerServiceProductMappings,
  customerServiceOnlineSpecCodes,
  type CustomerServiceMasterProductRow,
  type CustomerServiceProductMapping,
  type CustomerServiceSalesProductRow,
} from "./product-mapping";

export { customerServiceConversionStatuses, customerServiceProblemTypes, customerServiceRobotScopes } from "./contracts";
export type { CustomerServiceAnnotationInput, CustomerServiceConversionStatus, CustomerServiceProblemType, CustomerServiceRobotScope } from "./contracts";

export type CustomerServiceImportBatch = {
  id: string; shopName: string; sessionFileName: string; chatFileName: string; fileHash: string;
  status: string; conversationCount: number; matchedCount: number; sessionOnlyCount: number;
  chatOnlyCount: number; ambiguousCount: number; warnings: string[]; warningTotalCount: number;
  warningsTruncated: boolean; createdAt: string; completedAt: string | null;
};

export type CustomerServiceConversation = {
  id: number; shopName: string; consultedAt: string; customerId: string; customerAlias: string;
  consultationType: string; agent: string; transferredAgent: string; skillGroup: string;
  productSku: string; matchedSkuId: string; productSpuId: string; erpProductCode: string;
  productCategory: string; productName: string; firstResponseAt: string; responseSeconds: number | null;
  durationMinutes: number | null; customerMessageCount: number | null; agentMessageCount: number | null;
  satisfaction: string; resolved: string; conversationId: string; matchStatus: string; matchConfidence: string;
  chatStartedAt: string; chatEndedAt: string; chatCustomerAlias: string;
  messages: Array<{ sender: string; sentAt: string; content: string }>;
  messageTotalCount: number; messageReturnedCount: number; messagesTruncated: boolean;
  robotScope: CustomerServiceRobotScope | ""; problemType: CustomerServiceProblemType | "";
  conversionStatus: CustomerServiceConversionStatus | ""; serviceIssues: string; summaryText: string;
  analysisSource: "ai" | "manual" | ""; analyzedAt: string | null; annotatedAt: string | null;
  version: number; updatedAt: string;
};

export const CUSTOMER_SERVICE_MESSAGE_LIMIT = 200;
export const CUSTOMER_SERVICE_AI_MESSAGE_LIMIT = 24;
export const CUSTOMER_SERVICE_MESSAGE_CONTENT_LIMIT = 1_000;
export const CUSTOMER_SERVICE_MESSAGE_BYTES_LIMIT = 64 * 1024;
export const CUSTOMER_SERVICE_IMPORT_PAYLOAD_BYTES_LIMIT = 16 * 1024 * 1024;
export const CUSTOMER_SERVICE_IMPORT_TOTAL_PAYLOAD_BYTES_LIMIT = 16 * 1024 * 1024;
export const CUSTOMER_SERVICE_IMPORT_BATCH_STATEMENT_LIMIT = 1;

type CustomerServiceService = ReturnType<typeof createDjangoCustomerService>;
type CustomerServiceOptions = {
  service?: CustomerServiceService;
  salesReader?: SalesConsumerReader;
  netshopReader?: NetshopConsumerReader;
  signal?: AbortSignal;
};

export function planCustomerServiceImportPayloads(shopName: string, conversations: CustomerServiceParseResult["conversations"]) {
  if (!shopName.trim() || shopName.trim().length > 100) throw new PublicApiError(422, "invalid_request", "客服导入店铺名称必须为 1 到 100 字");
  try { validateCustomerServiceConversationMessages(conversations); }
  catch (error) {
    if (error instanceof CustomerServiceImportError) throw new PublicApiError(422, "invalid_request", error.message);
    throw error;
  }
  const serialized = JSON.stringify(conversations);
  const totalBytes = new TextEncoder().encode(serialized).byteLength;
  if (totalBytes > CUSTOMER_SERVICE_IMPORT_TOTAL_PAYLOAD_BYTES_LIMIT) throw new PublicApiError(422, "invalid_request", "客服会话规范化数据超过 16MB 安全预算，请拆分业务范围后重试");
  return { payloads: [serialized], totalBytes, totalBoundBytes: totalBytes, statementCount: 1 };
}

function service(options?: CustomerServiceOptions) {
  return options?.service ?? createDjangoCustomerService();
}

function queryString(values: Array<[string, string]>) {
  const params = new URLSearchParams();
  for (const [key, value] of values) if (value !== "") params.append(key, value);
  return params.toString();
}

export async function listCustomerServiceBatches(
  input: { page?: number; pageSize?: number } = {},
  principal: AppPrincipal,
  options: CustomerServiceOptions = {},
) {
  requireUnrestrictedDataScope(principal, "客服导入历史");
  const page = input.page ?? 1;
  const pageSize = input.pageSize ?? 20;
  const result = await service(options).requestJson<{ items: CustomerServiceImportBatch[]; pagination: Record<string, number | boolean> }>(principal, {
    method: "GET", path: CUSTOMER_SERVICE_IMPORTS_PATH, service: "reader",
    rawQuery: queryString([["page", String(page)], ["pageSize", String(pageSize)]]),
  }, { signal: options.signal });
  return result.data;
}

export async function recordRejectedCustomerServiceImport(
  principal: AppPrincipal,
  input: { rawFileHash: string; scopeHint: Record<string, unknown>; errorCode: string; issues: Array<{ code: string; message: string }>; fileName: string; fileSizeBytes: number },
  options: CustomerServiceOptions = {},
) {
  const result = await service(options).requestJson<Record<string, unknown>>(principal, {
    method: "POST", path: CUSTOMER_SERVICE_IMPORTS_PATH, service: "writer",
    payload: { action: "reject", ...input },
  }, { signal: options.signal });
  return result.data;
}

export async function saveCustomerServiceImport(
  input: { shopName: string; sessionFileName: string; chatFileName: string; fileHash: string; fileSizeBytes?: number; parsed: CustomerServiceParseResult },
  principal: AppPrincipal,
  options: CustomerServiceOptions = {},
) {
  if (input.parsed.conversations.length === 0) throw new PublicApiError(422, "invalid_request", "客服导入没有可保存的会话资料。");
  planCustomerServiceImportPayloads(input.shopName, input.parsed.conversations);
  const warning = summarizeCustomerServiceWarnings(input.parsed.warnings, input.parsed.warningTotalCount);
  const result = await service(options).requestJson<{
    status: "imported" | "duplicate"; batch: CustomerServiceImportBatch;
    warnings?: string[]; warningTotalCount?: number; warningsTruncated?: boolean;
  }>(principal, {
    method: "POST", path: CUSTOMER_SERVICE_IMPORTS_PATH, service: "writer",
    payload: {
      action: "import", shopName: input.shopName, sessionFileName: input.sessionFileName,
      chatFileName: input.chatFileName, rawFileHash: input.fileHash,
      fileSizeBytes: input.fileSizeBytes ?? 0, summary: input.parsed.summary,
      warnings: warning.warnings, warningTotalCount: warning.warningTotalCount,
      conversations: input.parsed.conversations,
    },
  }, { signal: options.signal });
  return {
    status: result.data.status,
    batch: result.data.batch,
    warningSummary: {
      warnings: result.data.warnings ?? warning.warnings,
      warningTotalCount: result.data.warningTotalCount ?? warning.warningTotalCount,
      warningsTruncated: result.data.warningsTruncated ?? warning.warningsTruncated,
    },
  };
}

function filterValues(values?: readonly string[], fallback?: string | null, label = "筛选项", max = 50, maxLength = 120) {
  const raw = [...(values ?? []), fallback ?? ""].map((value) => value.trim()).filter(Boolean);
  if (raw.length > max || raw.some((value) => value.length > maxLength)) throw new PublicApiError(400, "invalid_request", `${label}最多 ${max} 项，且每项不能超过 ${maxLength} 字。`);
  return [...new Set(raw)];
}

function splitIds(value?: string | null) {
  const raw = (value ?? "").split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean);
  if (raw.length > 100 || raw.some((item) => !/^[A-Za-z0-9_-]{2,80}$/.test(item))) throw new PublicApiError(400, "invalid_request", "SKU/SPU 筛选最多 100 项，且每项须为 2 到 80 位字母、数字、下划线或连字符。");
  return [...new Set(raw)];
}

async function loadMasterRows(principal: AppPrincipal, reader: NetshopConsumerReader, input: { lookupCodes?: readonly string[]; spuIds?: readonly string[]; limit: number; signal?: AbortSignal }) {
  const result = await reader.read(principal, { operation: "product_master_lookup", lookupCodes: [...new Set(input.lookupCodes ?? [])], spuIds: [...new Set(input.spuIds ?? [])], limit: input.limit }, { signal: input.signal });
  const data = result?.data as NetshopConsumerResponseMap["product_master_lookup"] | undefined;
  if (!result?.revision || !data || !Array.isArray(data.rows) || data.truncated || data.rows.length > input.limit) throw new PublicApiError(503, "service_unavailable", "Django 网店读取服务返回了无效的货品映射。");
  return data.rows.map<CustomerServiceMasterProductRow>((row) => ({ sku_id: row.skuId, spu_id: row.spuId, product_code: row.productCode, raw_json: JSON.stringify(row.raw) }));
}

function validSalesRow(value: unknown): value is SalesConsumerResponseMap["customer_service_products"]["rows"][number] {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    && typeof (value as Record<string, unknown>).onlineSpecCode === "string"
    && typeof (value as Record<string, unknown>).productCode === "string"
    && typeof (value as Record<string, unknown>).category === "string";
}

async function readSalesProducts(principal: AppPrincipal, reader: SalesConsumerReader, input: { onlineSpecCodes?: string[]; categories?: string[]; signal?: AbortSignal }): Promise<CustomerServiceSalesProductRow[]> {
  const codes = [...new Set(input.onlineSpecCodes ?? [])];
  const chunks = codes.length ? Array.from({ length: Math.ceil(codes.length / 2_000) }, (_, index) => codes.slice(index * 2_000, (index + 1) * 2_000)) : [[]];
  const rows: CustomerServiceSalesProductRow[] = [];
  let revision: string | null = null;
  for (const chunk of chunks) {
    const result = await reader.read(principal, { operation: "customer_service_products", ...(chunk.length ? { onlineSpecCodes: chunk } : {}), ...(input.categories?.length ? { categories: input.categories } : {}), limit: 5_000 }, { signal: input.signal });
    if (!result?.revision || (revision && revision !== result.revision) || !Array.isArray(result.data?.rows) || result.data.truncated || !result.data.rows.every(validSalesRow)) throw new PublicApiError(503, "service_unavailable", "Django 销售读取服务暂时不可用，请稍后重试。");
    revision = result.revision;
    rows.push(...result.data.rows.map((row) => ({ online_spec_code: row.onlineSpecCode, product_code: row.productCode, category: row.category })));
  }
  return rows;
}

function lookupCodesForOnlineSpecs(rows: CustomerServiceMasterProductRow[]) {
  const output = new Set<string>();
  for (const row of rows) {
    let raw: Record<string, unknown> = {};
    try { raw = JSON.parse(row.raw_json) as Record<string, unknown>; } catch { /* invalid upstream rows are ignored */ }
    const online = String(raw["商家SKU"] ?? "").trim();
    if (row.sku_id) output.add(row.sku_id);
    if (online) output.add(online);
    if (row.product_code) output.add(row.product_code);
  }
  return [...output];
}

type CustomerServiceConversationFilters = {
  shopNames?: string[]; shopName?: string | null; startDate?: string | null; endDate?: string | null;
  agents?: string[]; agent?: string | null; statuses?: string[]; status?: string | null;
  robotScopes?: string[]; robotScope?: string | null; problemTypes?: string[]; problemType?: string | null;
  conversionStatuses?: string[]; conversionStatus?: string | null; categories?: string[]; category?: string | null;
  query?: string | null; skuIds?: string | null; spuIds?: string | null; page?: number | null;
  pageSize?: number | null; includeOptions?: boolean;
};

async function catalogFor(principal: AppPrincipal, productSkus: string[], options: CustomerServiceOptions) {
  if (!productSkus.length) return new Map<string, CustomerServiceProductMapping>();
  const netshop = options.netshopReader ?? createDjangoNetshopConsumerReader();
  const sales = options.salesReader ?? createDjangoSalesConsumerReader();
  const master = await loadMasterRows(principal, netshop, { lookupCodes: productSkus, limit: 5_000, signal: options.signal });
  const online = customerServiceOnlineSpecCodes(master);
  const salesRows = online.length ? await readSalesProducts(principal, sales, { onlineSpecCodes: online, signal: options.signal }) : [];
  return buildCustomerServiceProductMappings(productSkus, master, salesRows);
}

export async function listCustomerServiceConversations(filters: CustomerServiceConversationFilters, principal: AppPrincipal, options: CustomerServiceOptions = {}) {
  requireUnrestrictedDataScope(principal, "客服会话");
  const sales = options.salesReader ?? createDjangoSalesConsumerReader();
  const netshop = options.netshopReader ?? createDjangoNetshopConsumerReader();
  const categories = filterValues(filters.categories, filters.category, "品类筛选", 50, 120);
  const productCodes = new Set(splitIds(filters.skuIds));
  const spuIds = splitIds(filters.spuIds);
  if (spuIds.length) {
    const master = await loadMasterRows(principal, netshop, { spuIds, limit: 5_000, signal: options.signal });
    lookupCodesForOnlineSpecs(master).forEach((value) => productCodes.add(value));
  }
  if (categories.length) {
    const categorySales = await readSalesProducts(principal, sales, { categories, signal: options.signal });
    const master = await loadMasterRows(principal, netshop, { lookupCodes: categorySales.map((item) => item.online_spec_code), limit: 5_000, signal: options.signal });
    lookupCodesForOnlineSpecs(master).forEach((value) => productCodes.add(value));
    if (productCodes.size === 0) productCodes.add("__customer_service_no_match__");
  }
  const values: Array<[string, string]> = [];
  filterValues(filters.shopNames, filters.shopName, "店铺筛选", 50, 100).forEach((value) => values.push(["shopName", value]));
  filterValues(filters.agents, filters.agent, "客服筛选", 50, 200).forEach((value) => values.push(["agent", value]));
  filterValues(filters.statuses, filters.status, "匹配状态筛选", 20, 32).forEach((value) => values.push(["status", value]));
  filterValues(filters.robotScopes, filters.robotScope, "机器人筛选", 20, 32).forEach((value) => values.push(["robotScope", value]));
  filterValues(filters.problemTypes, filters.problemType, "问题类型筛选", 20, 32).forEach((value) => values.push(["problemType", value]));
  filterValues(filters.conversionStatuses, filters.conversionStatus, "转化状态筛选", 20, 32).forEach((value) => values.push(["conversionStatus", value]));
  productCodes.forEach((value) => values.push(["productSku", value]));
  values.push(["startDate", filters.startDate ?? ""], ["endDate", filters.endDate ?? ""], ["query", filters.query?.trim() ?? ""], ["page", String(filters.page ?? 1)], ["pageSize", String(filters.pageSize ?? 30)], ["includeOptions", filters.includeOptions === false ? "false" : "true"]);
  const result = await service(options).requestJson<{ items: CustomerServiceConversation[]; agents: string[]; shops: string[]; productSkus: string[]; summary: Record<string, number>; pagination: Record<string, number | boolean> }>(principal, { method: "GET", path: CUSTOMER_SERVICE_CONVERSATIONS_PATH, service: "reader", rawQuery: queryString(values) }, { signal: options.signal });
  const itemCodes = [...new Set(result.data.items.map((item) => item.productSku).filter(Boolean))];
  const catalog = await catalogFor(principal, itemCodes, { ...options, salesReader: sales, netshopReader: netshop });
  let categoryOptions: string[] = [];
  if (filters.includeOptions !== false && result.data.productSkus.length) {
    const optionMaster = await loadMasterRows(principal, netshop, { lookupCodes: result.data.productSkus, limit: 5_000, signal: options.signal });
    const optionSales = await readSalesProducts(principal, sales, { onlineSpecCodes: customerServiceOnlineSpecCodes(optionMaster), signal: options.signal });
    categoryOptions = [...new Set(optionSales.map((item) => item.category.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b)).slice(0, 100);
  }
  return {
    ...result.data,
    items: result.data.items.map((item) => { const matched = catalog.get(item.productSku); return { ...item, matchedSkuId: matched?.matchedSkuId ?? "", productSpuId: matched?.spuId ?? "", erpProductCode: matched?.erpProductCode ?? "", productCategory: matched?.category ?? "" }; }),
    categories: categoryOptions,
  };
}

export async function getCustomerServiceConversationById(id: number, principal: AppPrincipal, options: CustomerServiceOptions = {}) {
  const result = await service(options).requestJson<{ item: CustomerServiceConversation }>(principal, { method: "GET", path: CUSTOMER_SERVICE_CONVERSATIONS_PATH, service: "reader", rawQuery: queryString([["id", String(id)]]) }, { signal: options.signal });
  const catalog = await catalogFor(principal, result.data.item.productSku ? [result.data.item.productSku] : [], options);
  const matched = catalog.get(result.data.item.productSku);
  return { ...result.data.item, matchedSkuId: matched?.matchedSkuId ?? "", productSpuId: matched?.spuId ?? "", erpProductCode: matched?.erpProductCode ?? "", productCategory: matched?.category ?? "" };
}

export async function getCustomerServiceConversationsByIds(ids: number[], principal: AppPrincipal, options: CustomerServiceOptions = {}) {
  const normalized = [...new Set(ids.filter((id) => Number.isSafeInteger(id) && id > 0))].slice(0, 20);
  if (!normalized.length) return [];
  const result = await service(options).requestJson<{ items: CustomerServiceConversation[] }>(principal, { method: "POST", path: CUSTOMER_SERVICE_SNAPSHOTS_PATH, service: "reader", payload: { ids: normalized } }, { signal: options.signal });
  return result.data.items;
}

export async function updateCustomerServiceConversationAnnotation(id: number, input: CustomerServiceAnnotationInput, expectedVersion: number, principal: AppPrincipal, options: CustomerServiceOptions = {}) {
  const result = await service(options).requestJson<{ id: number; updated: boolean; version: number; updatedAt: string }>(principal, { method: "PATCH", path: CUSTOMER_SERVICE_CONVERSATIONS_PATH, service: "writer", payload: { id, expectedVersion, ...input } }, { signal: options.signal });
  return result.data;
}

export async function deleteCustomerServiceConversation(id: number, expectedVersion: number, principal: AppPrincipal, reason: string, options: CustomerServiceOptions = {}) {
  const result = await service(options).requestJson<{ id: number; deleted: boolean; auditId: string }>(principal, { method: "DELETE", path: CUSTOMER_SERVICE_CONVERSATIONS_PATH, service: "writer", payload: { id, expectedVersion, reason } }, { signal: options.signal });
  return result.data;
}

export async function getCustomerServiceConversationsForAi(args: Record<string, unknown>, principal: AppPrincipal, options: CustomerServiceOptions = {}) {
  const payload = await listCustomerServiceConversations({ startDate: typeof args.startDate === "string" ? args.startDate : null, endDate: typeof args.endDate === "string" ? args.endDate : null, agent: typeof args.agent === "string" ? args.agent : null, problemType: typeof args.problemType === "string" ? args.problemType : null, conversionStatus: typeof args.conversionStatus === "string" ? args.conversionStatus : null, category: typeof args.category === "string" ? args.category : null, query: typeof args.query === "string" ? args.query : null, page: 1, pageSize: Math.max(1, Math.min(50, Number(args.limit) || 20)), includeOptions: false }, principal, options);
  return { filtersApplied: { startDate: args.startDate ?? null, endDate: args.endDate ?? null, agent: args.agent ?? null, problemType: args.problemType ?? null, conversionStatus: args.conversionStatus ?? null, category: args.category ?? null, query: args.query ?? null }, returned: payload.items.length, totalMatched: Number(payload.pagination.total), truncated: Number(payload.pagination.total) > payload.items.length, items: payload.items.map((item) => ({ id: item.id, shopName: item.shopName, consultedAt: item.consultedAt, agent: item.agent, sourceProductCode: item.productSku, matchedSkuId: item.matchedSkuId, productSpuId: item.productSpuId, erpProductCode: item.erpProductCode, productCategory: item.productCategory, robotScope: item.robotScope, problemType: item.problemType, conversionStatus: item.conversionStatus, serviceIssues: item.serviceIssues, summary: item.summaryText, matchStatus: item.matchStatus })) };
}
