import type { AiToolExecutionContext } from "@/lib/ai/tool-registry-contract";
import {
  FINANCE_BUSINESS_EVIDENCE_PAGE_PATH,
  requestDjangoFinanceService,
  type DjangoFinanceServiceOptions,
} from "@/lib/django/finance-service";
import { PublicApiError } from "@/lib/http/api-error";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";

/** Registered only for the internal business_collection surface; never exposed to models. */
export async function readBusinessFinanceSourcePage(
  raw: unknown,
  context: AiToolExecutionContext,
  options: DjangoFinanceServiceOptions = {},
): Promise<Record<string, unknown>> {
  requireAnalysisPrincipal(context.principal);
  if (context.surface !== "business_collection") {
    throw new PublicApiError(403, "access_denied", "财报证据分页仅供后台采集准备");
  }
  const invalid = (): never => { throw new PublicApiError(422, "invalid_request", "财报证据分页参数无效"); };
  const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every((key) => key in value);
  if (!record(raw)) invalid();
  const args = raw as Record<string, unknown>;
  const allowed = ["query", "offset", "afterId", "expectedSourceRef", "expectedRevision"];
  if (Object.keys(args).some((key) => !allowed.includes(key)) || !["query", "offset", "afterId"].every((key) => key in args)) invalid();
  if (!record(args.query) || !exact(args.query, ["months", "scope", "analysisPeriod"])) invalid();
  const query = args.query as Record<string, unknown>;
  if (!Array.isArray(query.months) || query.months.length < 1 || query.months.length > 24
    || !query.months.every((item) => typeof item === "string" && /^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$/.test(item))) invalid();
  if (!record(query.scope) || !exact(query.scope, ["scope_key", "scope_type", "scope_name", "group_name"])) invalid();
  const scope = query.scope as Record<string, unknown>;
  if (["scope_key", "scope_type", "scope_name", "group_name"].some((key) => typeof scope[key] !== "string")
    || typeof scope.scope_key !== "string" || !scope.scope_key || !["business", "group", "shop"].includes(String(scope.scope_type))) invalid();
  if (!record(query.analysisPeriod) || !exact(query.analysisPeriod, ["startDate", "endDate"])) invalid();
  const period = query.analysisPeriod as Record<string, unknown>;
  if (["startDate", "endDate"].some((key) => typeof period[key] !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(String(period[key])))) invalid();
  if (!Number.isSafeInteger(args.offset) || Number(args.offset) < 0 || Number(args.offset) > 100_000
    || !Number.isSafeInteger(args.afterId) || Number(args.afterId) < 0
    || (args.offset === 0) !== (args.afterId === 0)) invalid();
  if (Number(args.offset) > 0 && (typeof args.expectedSourceRef !== "string" || typeof args.expectedRevision !== "string")) invalid();
  if (args.expectedSourceRef !== undefined && (typeof args.expectedSourceRef !== "string" || !/^[a-f0-9]{64}$/.test(args.expectedSourceRef))) invalid();
  if (args.expectedRevision !== undefined && (typeof args.expectedRevision !== "string" || !/^\d+:[a-f0-9]{64}$/.test(args.expectedRevision))) invalid();
  const payload = args as Record<string, unknown>;
  if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > 8192) invalid();
  const result = await requestDjangoFinanceService<Record<string, unknown>>(
    context.principal,
    { method: "POST", path: FINANCE_BUSINESS_EVIDENCE_PAGE_PATH, service: "reader", payload },
    { ...options, signal: context.signal ?? options.signal },
  );
  const data = result.data;
  if (!record(data)) throw new PublicApiError(409, "conflict", "财报证据分页回执格式无效");
  const pagination = data.pagination;
  const sourceRevision = data.sourceRevision;
  const echoed = data.query;
  const sameQuery = record(echoed) && exact(echoed, ["months", "scope", "analysisPeriod"])
    && Array.isArray(echoed.months) && JSON.stringify(echoed.months) === JSON.stringify(query.months)
    && record(echoed.scope) && exact(echoed.scope, ["scope_key", "scope_type", "scope_name", "group_name"])
    && ["scope_key", "scope_type", "scope_name", "group_name"].every((key) => (echoed.scope as Record<string, unknown>)[key] === scope[key])
    && record(echoed.analysisPeriod) && exact(echoed.analysisPeriod, ["startDate", "endDate"])
    && ["startDate", "endDate"].every((key) => (echoed.analysisPeriod as Record<string, unknown>)[key] === period[key]);
  const pageEvidence = data.pageEvidence;
  if (data.schemaVersion !== "business-finance-owned-page-v1"
    || typeof data.sourceRef !== "string" || !/^[a-f0-9]{64}$/.test(data.sourceRef)
    || typeof sourceRevision !== "string" || !/^\d+:[a-f0-9]{64}$/.test(sourceRevision)
    || result.revision !== `${sourceRevision.split(":")[0]}:${sourceRevision.split(":")[1].slice(0, 12)}`
    || (args.expectedSourceRef !== undefined && data.sourceRef !== args.expectedSourceRef)
    || (args.expectedRevision !== undefined && sourceRevision !== args.expectedRevision)
    || data.sourceAuthorityVerified !== false || data.persistentEvidenceVerified !== false
    || !sameQuery || !Array.isArray(data.rows) || !record(pagination) || !record(pageEvidence)
    || pageEvidence.rowCount !== data.rows.length || typeof pageEvidence.sha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(pageEvidence.sha256)
    || pagination.offset !== args.offset || pagination.returned !== data.rows.length
    || !Number.isSafeInteger(pagination.total) || Number(pagination.total) < data.rows.length
    || (pagination.nextOffset !== null && (!Number.isSafeInteger(pagination.nextOffset)
      || Number(pagination.nextOffset) !== Number(args.offset) + data.rows.length))
    || (pagination.nextLastId !== null && (!Number.isSafeInteger(pagination.nextLastId)
      || Number(pagination.nextLastId) < 1))
    || (pagination.nextOffset === null) !== (pagination.nextLastId === null)
    || (pagination.nextOffset === null && Number(args.offset) + data.rows.length !== pagination.total)
    || typeof data.pageDigest !== "string" || !/^[a-f0-9]{64}$/.test(data.pageDigest)) {
    throw new PublicApiError(409, "conflict", "财报证据分页回执未通过来源绑定");
  }
  if (new TextEncoder().encode(JSON.stringify(data)).byteLength > 38_000) {
    throw new PublicApiError(413, "payload_too_large", "财报证据页超过完整工具容量");
  }
  return data;
}
