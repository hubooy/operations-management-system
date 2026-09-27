import { createHash } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { validateDirectExportPayload } from "./direct-export";
import type { JackyunHttpSession, JackyunHttpOperation } from "./direct-http";
import { jackyunExportOrder, jackyunCaptureDate } from "./run-contract";
import type { JackyunModule } from "./post-download";
import { jackyunSalesPeriod } from "./sales-period";

export const jackyunApiTransport = "session_api_v1";
export type ApiTemplate = { moduleCode: string; query: Record<string, string>; export: Record<string, string> };
export type ApiTemplates = { version: 1; calibratedAt: string; permissionFieldsSha256: string; modules: Record<JackyunModule, ApiTemplate> };
type Result = { data: unknown; pageInfo?: unknown; noPrivilegeItem?: unknown; desensitizationItem?: unknown };
type Http = Pick<JackyunHttpSession, "request">;
export type ApiScope = { warehouseIds: string[]; ownerId: string; permissionSha256: string; observedAt: string };
const code: Record<JackyunModule, string> = { inventory: "branch_stock", combos: "goods_managet_combination", sales: "order_detail_list", inventory_age: "warehouse_age_analysis", products: "goods_managet_query" };
const counts: Record<JackyunModule, JackyunHttpOperation> = { inventory: "inventoryCount", combos: "goodsCount", products: "goodsCount", sales: "salesCount", inventory_age: "ageCount" };
const minimum: Record<JackyunModule, number> = { inventory: 20000, combos: 1000, products: 5000, sales: 1, inventory_age: 4000 };

/** Permission arrays are sets; response context IDs and labels do not enter the fingerprint. */
export function permissionCanonical(value: unknown): string {
  return JSON.stringify(value && typeof value === "object" ? Array.isArray(value)
    ? value.map(item => JSON.parse(permissionCanonical(item))).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
    : Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(permissionCanonical((value as Record<string, unknown>)[key]))])) : value);
}
export const apiSha = (value: string) => createHash("sha256").update(value).digest("hex");
function record(value: unknown): Record<string, unknown> {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("API_RESPONSE_SCHEMA_CHANGED");
  return value as Record<string, unknown>;
}
function rows(result: Result, maximum: number) {
  if (!Array.isArray(result.data) || !result.data.length || result.data.length > maximum || result.pageInfo != null) throw new Error("API_METADATA_INCOMPLETE");
  return result.data.map(record);
}
function id(value: unknown) {
  if (typeof value !== "string" || !/^[1-9]\d{0,24}$/.test(value)) throw new Error("API_ID_INVALID");
  return value;
}

export async function readApiScope(http: Http, templates: ApiTemplates, tenantId: string): Promise<ApiScope> {
  if (templates.version !== 1 || !/^[a-f0-9]{64}$/.test(templates.permissionFieldsSha256)) throw new Error("API_TEMPLATE_INVALID");
  const modules = rows(await http.request<Result>("rolePermissions", {}), 5000);
  for (const moduleKey of jackyunExportOrder) {
    const match = modules.filter(row => row.moduleCode === code[moduleKey]);
    if (match.length !== 1 || match[0].hasPermission !== 1 || match[0].isBuy !== 1) throw new Error("API_MODULE_PERMISSION_DENIED");
  }
  const functions = rows(await http.request<Result>("roleFunctions", {}), 10000);
  for (const funCode of ["branch_stock_export", "goods_managet_combination_export", "goods_managet_query_export", "oms_order_detail_list_export"]) {
    if (functions.filter(row => row.funCode === funCode).length !== 1) throw new Error("API_EXPORT_PERMISSION_DENIED");
  }
  const fields = await http.request<Result>("dataFieldPermissions", {});
  const permissionSha256 = apiSha(permissionCanonical(record(fields.data)));
  if (permissionSha256 !== templates.permissionFieldsSha256) throw new Error("API_FIELD_PERMISSIONS_CHANGED_RECALIBRATE");
  const warehouses = rows(await http.request<Result>("warehouses", { fieldName: "warehouseId,warehouseName,warehouseCode,warehouseTypeCode,isPositonStock,warehouseCompanyId,warehouseGroupName", warehouseTypeCode: "1,2,5", isQueryStock: "1" }), 20000);
  const warehouseIds = warehouses.map(row => {
    if (!["1", "2", "5"].includes(String(row.warehouseTypeCode))) throw new Error("API_WAREHOUSE_SCOPE_CHANGED");
    return id(row.warehouseId);
  });
  if (warehouseIds.length < 200 || new Set(warehouseIds).size !== warehouseIds.length) throw new Error("API_WAREHOUSE_SCOPE_INCOMPLETE");
  const owners = rows(await http.request<Result>("owners", { reportCode: "warehouse_age_analysis" }), 10000);
  const self = owners.filter(row => row.name === "自营" && row.memberName === tenantId);
  if (self.length !== 1 || self[0].id !== self[0].jlinkOwnerId) throw new Error("API_OWNER_SCOPE_CHANGED");
  return { warehouseIds: warehouseIds.sort(), ownerId: id(self[0].id), permissionSha256, observedAt: new Date().toISOString() };
}

export function buildApiParameters(module: JackyunModule, template: ApiTemplate, scope: ApiScope, asOfDate: string, salesStartDate?: string) {
  if (template.moduleCode !== code[module] || !/^\d{4}-\d{2}-\d{2}$/.test(asOfDate)
    || new Date(asOfDate + "T00:00:00Z").toISOString().slice(0, 10) !== asOfDate) throw new Error("API_TEMPLATE_BINDING_INVALID");
  const query = structuredClone(template.query), data = structuredClone(template.export);
  const condition = record(JSON.parse(data.conditionJson));
  if (module === "inventory") { if (query.warehouseId !== "@warehouse_ids" || condition.warehouseId !== "@warehouse_ids") throw new Error("API_TEMPLATE_SCOPE_INVALID"); query.warehouseId = condition.warehouseId = scope.warehouseIds.join(","); }
  if (module === "inventory_age") { if (query.ownerId !== "@owner_id" || condition.ownerId !== "@owner_id" || condition.includeBlockWarehouse !== "0") throw new Error("API_TEMPLATE_SCOPE_INVALID"); query.ownerId = condition.ownerId = scope.ownerId; }
  if (module === "sales") {
    const filter = record(condition.filterOrderDetailDto), countFilter = record(JSON.parse(query.jsonStr));
    if (!isDeepStrictEqual(filter, countFilter) || filter.timeBegin !== "@sales_begin" || filter.timeEnd !== "@sales_end") throw new Error("API_SALES_SCOPE_INVALID");
    filter.timeBegin = `${jackyunSalesPeriod(asOfDate, salesStartDate).startDate} 00:00:00`; filter.timeEnd = `${asOfDate} 23:59:59`;
    query.jsonStr = JSON.stringify(filter);
  }
  data.conditionJson = JSON.stringify(condition);
  const countCondition = module === "sales" ? record(JSON.parse(query.jsonStr)) : query;
  if (module !== "sales") for (const [key, value] of Object.entries(query)) {
    if (!Object.hasOwn(condition, key)) throw new Error("API_COUNT_EXPORT_SCOPE_MISMATCH");
    const normalized = typeof condition[key] === "object" ? JSON.stringify(condition[key]) : String(condition[key]);
    if (normalized !== value) throw new Error("API_COUNT_EXPORT_SCOPE_MISMATCH");
  }
  if (Object.keys(countCondition).some(key => /token|password|secret|sign|appkey/i.test(key))) throw new Error("API_TEMPLATE_SECRET_FIELD");
  validateDirectExportPayload(module, new URLSearchParams({ ...data, exportTotal: "1" }).toString(), template.moduleCode, asOfDate, salesStartDate);
  return { query, data, moduleCode: template.moduleCode };
}

export async function prepareApiExport(http: Http, templates: ApiTemplates, scope: ApiScope, module: JackyunModule, runDate: string, asOfDate: string, salesStartDate?: string) {
  const queryIntentAt = new Date().toISOString();
  if (jackyunCaptureDate(queryIntentAt) !== runDate) throw new Error("API_CAPTURE_DATE_CHANGED");
  const prepared = buildApiParameters(module, templates.modules[module], scope, asOfDate, salesStartDate);
  const response = await http.request<Result>(counts[module], prepared.query, prepared.moduleCode);
  const rawCount = response.data;
  if ((typeof rawCount !== "number" && (typeof rawCount !== "string" || !/^[1-9]\d*$/.test(rawCount)))
    || response.noPrivilegeItem != null || response.desensitizationItem != null) throw new Error("API_COUNT_UNVERIFIED");
  const sourceRows = Number(rawCount);
  if (!Number.isSafeInteger(sourceRows) || sourceRows < minimum[module] || sourceRows > 500000) throw new Error("API_SOURCE_COUNT_OUT_OF_BOUNDS");
  const headers = JSON.parse(prepared.data.headersJson);
  if (module === "combos" && (headers as { enName: string[] }[]).some(sheet => sheet.enName.some(field => /imgurl/i.test(field))) && sourceRows > 2000) throw new Error("API_COMBO_IMAGE_EXPORT_WOULD_TRUNCATE");
  prepared.data.exportTotal = String(sourceRows);
  const validated = validateDirectExportPayload(module, new URLSearchParams(prepared.data).toString(), prepared.moduleCode, asOfDate, salesStartDate);
  const queryCompletedAt = new Date().toISOString();
  if (jackyunCaptureDate(queryCompletedAt) !== runDate) throw new Error("API_CAPTURE_DATE_CHANGED");
  return { ...validated, sourceRows, queryIntentAt, queryCompletedAt, querySha256: apiSha(JSON.stringify(prepared.query)), permissionSha256: scope.permissionSha256 };
}
