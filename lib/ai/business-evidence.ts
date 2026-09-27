import type { AppPrincipal } from "@/lib/auth/authorization";
import { requestDjangoAi } from "@/lib/django/ai-service";
import { PublicApiError } from "@/lib/http/api-error";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";

type IntegratedArguments = { reportId: string; runId: string; offset?: number; mode?: "native" | "mapped"; dimension?: string; sourceKey?: string; baselineKey?: string; pairKey?: string; baselinePairKey?: string };
function integratedArguments(raw: unknown, kind: "directory" | "analysis-table" | "budget"): IntegratedArguments {
  const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "综合经营分析工具参数无效，须使用报告固定来源与关联身份"); };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) invalid();
  const args = raw as IntegratedArguments;
  const allowed = kind === "analysis-table" ? ["reportId", "runId", "offset", "mode", "dimension", "sourceKey", "baselineKey", "pairKey", "baselinePairKey"] : ["reportId", "runId", "offset"];
  if (Object.keys(args).some(key => !allowed.includes(key))) invalid();
  const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
  const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  if (!id(args.runId) || !id(args.reportId) || (args.offset !== undefined && (!Number.isSafeInteger(args.offset) || args.offset < 0 || args.offset > (kind === "directory" ? 47 : kind === "budget" ? 99 : 250000)))) invalid();
  if (kind === "analysis-table") {
    if (!args.dimension || !["shop", "category", "spu", "sku", "keyword", "searchTerm", "daily", "brand"].includes(args.dimension)) invalid();
    if (args.mode === "native") {
      if (!id(args.sourceKey) || (args.baselineKey !== undefined && !id(args.baselineKey)) || "pairKey" in args || "baselinePairKey" in args) invalid();
    } else if (args.mode === "mapped") {
      if (!sha(args.pairKey) || (args.baselinePairKey !== undefined && !sha(args.baselinePairKey)) || "sourceKey" in args || "baselineKey" in args || !["sku", "spu"].includes(args.dimension ?? "")) invalid();
    } else invalid();
  }
  return args;
}
async function integratedRead(raw: unknown, principal: AppPrincipal, kind: "directory" | "analysis-table" | "budget", signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const args = integratedArguments(raw, kind);
  const query = new URLSearchParams({ runId: args.runId, offset: String(args.offset ?? 0) });
  if (kind === "analysis-table") for (const key of ["mode", "dimension", "sourceKey", "baselineKey", "pairKey", "baselinePairKey"] as const) {
    if (args[key] !== undefined) query.set(key, args[key]!);
  }
  // Page size and all report/evidence/plan bindings are fixed and checked by
  // the owning backend. Model arguments never supply digests or authority.
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/reports/${args.reportId}/integrated-${kind}`, method: "GET", query,
  }, { signal });
  if (!result.data || typeof result.data !== "object" || Array.isArray(result.data)) throw new PublicApiError(409, "conflict", "综合经营分析回执格式无效");
  if (new TextEncoder().encode(JSON.stringify(result.data)).byteLength > 38_000) throw new PublicApiError(413, "payload_too_large", "综合经营分析完整页超过工具字节容量，不得截断");
  const expectedSchema = { directory: "business-integrated-directory-v1", "analysis-table": "business-integrated-analysis-v1", budget: "business-integrated-budget-v1" }[kind];
  const reference = result.data.reference;
  if (result.data.schemaVersion !== expectedSchema || !reference || typeof reference !== "object" || Array.isArray(reference)
    || (reference as Record<string, unknown>).evidenceRunId !== args.runId || (reference as Record<string, unknown>).reportId !== args.reportId) {
    throw new PublicApiError(409, "conflict", "综合经营分析回执协议或报告证据身份不一致");
  }
  return result.data;
}
export const readBusinessIntegratedDirectoryV1 = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => integratedRead(raw, principal, "directory", signal);
export const readBusinessIntegratedAnalysisTableV1 = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => integratedRead(raw, principal, "analysis-table", signal);
export const readBusinessIntegratedBudgetV1 = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => integratedRead(raw, principal, "budget", signal);

const screeningRoles = ["commerce", "promotion", "market_b2b", "independent_review", "report"];
async function screeningRead(raw: unknown, principal: AppPrincipal, kind: "package" | "analysis" | "budget", signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "筛查工具参数无效，须使用报告固定身份与角色"); };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) invalid();
  const input = raw as Record<string, unknown>;
  const allowed = ["runId", "reportId", "screeningId", "offset", ...(kind === "package" ? ["role"] : kind === "analysis" ? ["mode", "dimension", "sourceKey", "baselineKey", "pairKey", "baselinePairKey"] : [])];
  if (Object.keys(input).some(key => !allowed.includes(key))) invalid();
  for (const key of ["runId", "reportId", "screeningId"]) if (typeof input[key] !== "string" || !/^[A-Za-z0-9_-]{1,160}$/.test(input[key] as string)) invalid();
  const maximum = kind === "package" ? 9999 : kind === "budget" ? 99 : 250000;
  if (input.offset !== undefined && (!Number.isSafeInteger(input.offset) || (input.offset as number) < 0 || (input.offset as number) > maximum)) invalid();
  if (kind === "package" && (typeof input.role !== "string" || !screeningRoles.includes(input.role))) invalid();
  if (kind === "analysis") {
    const { screeningId: _screeningId, ...analysis } = input;
    void _screeningId;
    integratedArguments(analysis, "analysis-table");
  }
  const query = new URLSearchParams({ runId: input.runId as string, screeningId: input.screeningId as string, offset: String(input.offset ?? 0) });
  for (const key of ["role", "mode", "dimension", "sourceKey", "baselineKey", "pairKey", "baselinePairKey"]) if (input[key] !== undefined) query.set(key, input[key] as string);
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/reports/${input.reportId}/screening/${kind}`, method: "GET", query,
  }, { signal });
  const value = result.data;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new PublicApiError(409, "conflict", "筛查回执格式无效");
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 38_000) throw new PublicApiError(413, "payload_too_large", "筛查完整页超过工具字节容量，不得截断");
  const reference = kind === "package" ? value : value.reference;
  const bound = reference && typeof reference === "object" && !Array.isArray(reference) ? reference as Record<string, unknown> : null;
  const intent = bound?.screeningIntent;
  // Package pages already use the entire byte envelope. Its screening ID is
  // checked by the owning reader, while report/run/role are echoed in the page.
  if (value.schemaVersion !== ({ package: "business-screening-role-package-v1", analysis: "business-screening-analysis-v1", budget: "business-screening-budget-v1" }[kind])
    || bound?.reportId !== input.reportId || bound?.evidenceRunId !== input.runId
    || (kind === "package" ? value.role !== input.role : !intent || typeof intent !== "object" || Array.isArray(intent) || (intent as Record<string, unknown>).id !== input.screeningId)) {
    throw new PublicApiError(409, "conflict", "筛查回执协议或固定身份不一致");
  }
  return value;
}
export const readBusinessScreeningPackageV1 = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => screeningRead(raw, principal, "package", signal);
export const readBusinessScreeningAnalysisTableV1 = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => screeningRead(raw, principal, "analysis", signal);
export const readBusinessScreeningBudgetV1 = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => screeningRead(raw, principal, "budget", signal);

export async function readBusinessEvidenceDirectoryV2(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const args = raw as { runId: string; offset?: number };
  if (!args || typeof args.runId !== "string" || !/^[A-Za-z0-9_-]{1,160}$/.test(args.runId)
    || !Number.isSafeInteger(args.offset ?? 0) || (args.offset ?? 0) < 0 || (args.offset ?? 0) > 47) {
    throw new PublicApiError(400, "invalid_request", "来源目录参数无效");
  }
  // Version and digest are checked against the immutable report reference by
  // the Django runtime, never accepted as model-controlled trust anchors here.
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/business-evidence/${args.runId}/sources`, method: "GET",
    query: new URLSearchParams({ offset: String(args.offset ?? 0), limit: "20" }),
  }, { signal });
  if (new TextEncoder().encode(JSON.stringify(result.data)).byteLength > 38_000) {
    throw new PublicApiError(413, "payload_too_large", "来源目录页超过工具字节容量，不得截断");
  }
  return result.data;
}

/** Unregistered internal v3 reader bridge. A paused intent is never Agent dispatch. */
function v3ReadArguments(raw: unknown, kind: "directory" | "page") {
  const invalid = (): never => { throw new PublicApiError(400, "invalid_request", "v3 封存来源读取参数无效"); };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) invalid();
  const args = raw as Record<string, unknown>;
  const allowed = kind === "directory" ? ["intentId", "offset", "handle"] : ["intentId", "sourceKey", "sequence", "rowOffset", "rowLimit", "handle"];
  if (Object.keys(args).some(key => !allowed.includes(key))) invalid();
  const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_-]{1,160}$/.test(value);
  const handle = (value: unknown) => typeof value === "string" && value.length <= 2400 && /^[A-Za-z0-9_-]+\.[0-9a-f]{64}$/.test(value);
  if (!id(args.intentId) || (args.handle !== undefined && !handle(args.handle))) invalid();
  if (kind === "directory") {
    if (args.offset !== undefined && (!Number.isSafeInteger(args.offset) || (args.offset as number) < 0 || (args.offset as number) > 47)) invalid();
    if ((args.offset ?? 0) !== 0 && args.handle === undefined) invalid();
  } else {
    if (!id(args.sourceKey) || !handle(args.handle)
      || !Number.isSafeInteger(args.sequence) || (args.sequence as number) < 1 || (args.sequence as number) > 1999
      || (args.rowOffset !== undefined && (!Number.isSafeInteger(args.rowOffset) || (args.rowOffset as number) < 0 || (args.rowOffset as number) > 100))
      || (args.rowLimit !== undefined && (!Number.isSafeInteger(args.rowLimit) || (args.rowLimit as number) < 1 || (args.rowLimit as number) > 10))) invalid();
  }
  return args;
}

async function v3Read(raw: unknown, principal: AppPrincipal, kind: "directory" | "page", signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const args = v3ReadArguments(raw, kind);
  const path = `/api/ai/business-v3-source-read/${args.intentId}/${kind === "directory" ? "directory" : `pages/${args.sourceKey}`}`;
  const payload = kind === "directory" ? { offset: args.offset ?? 0, ...(args.handle ? { handle: args.handle } : {}) }
    : { handle: args.handle, sequence: args.sequence, rowOffset: args.rowOffset ?? 0, rowLimit: args.rowLimit ?? 10 };
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path, method: "POST", service: "writer", payload,
  }, { signal });
  const value = result.data;
  if (!value || typeof value !== "object" || Array.isArray(value)
    || new TextEncoder().encode(JSON.stringify(value)).byteLength > 38_000
    || value.schemaVersion !== (kind === "directory" ? "business-v3-source-directory-v1" : "business-v3-source-slice-v1")
    || value.intentId !== args.intentId || value.readOnlyOperationOnWriterProcess !== true
    || value.agentReadReceiptRecorded !== false
    || kind === "page" && value.sourceKey !== args.sourceKey) {
    throw new PublicApiError(409, "conflict", "v3 来源页协议、身份或容量与固定意图不一致");
  }
  return value;
}

export const readBusinessV3DirectoryInternal = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => v3Read(raw, principal, "directory", signal);
export const readBusinessV3PageInternal = (raw: unknown, principal: AppPrincipal, signal?: AbortSignal) => v3Read(raw, principal, "page", signal);

export async function readBusinessEvidence(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const args = raw as { runId: string; sourceKey?: string; sequence?: number; rowOffset?: number; rowLimit?: number };
  if ((args.sourceKey === undefined) !== (args.sequence === undefined)) throw new PublicApiError(400, "invalid_request", "读取分块须同时指定 sourceKey 和 sequence");
  if (!args.sourceKey && (args.rowOffset !== undefined || args.rowLimit !== undefined)) throw new PublicApiError(400, "invalid_request", "行分页须指定分块");
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/business-evidence/${args.runId}${args.sourceKey ? `/chunks/${args.sourceKey}` : ""}`,
    method: "GET", ...(args.sourceKey ? { query: new URLSearchParams({ sequence: String(args.sequence), rowOffset: String(args.rowOffset ?? 0), rowLimit: String(args.rowLimit ?? 10) }) } : {}),
  }, { signal });
  if (!args.sourceKey) {
    const item = result.data.item as { plan?: { schemaVersion?: string } } | undefined;
    if (item?.plan?.schemaVersion !== "business-evidence-v1") throw new PublicApiError(409, "conflict", "此证据版本使用分页来源目录，当前工具尚未接入，不能声明已读取完整来源");
  }
  if (JSON.stringify(result.data).length > 38_000) throw new PublicApiError(413, "payload_too_large", "证据响应超过工具容量，不得截断；请从任务接口读取完整内容");
  return result.data;
}

export async function readBusinessAnalysisTable(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const { runId, ...args } = raw as { runId: string; sourceKey: string; dimension: string; baselineKey?: string; offset?: number; limit?: number };
  const query = new URLSearchParams({ limit: "10" });
  for (const [key, value] of Object.entries(args)) query.set(key, String(value));
  const result = await requestDjangoAi<Record<string, unknown>>(principal,
    { path: `/api/ai/business-evidence/${runId}/analysis`, method: "GET", query }, { signal });
  if (JSON.stringify(result.data).length > 38_000) throw new PublicApiError(413, "payload_too_large", "分析表页过大，请减小页长，不得截断");
  return result.data;
}

export async function readBusinessAnalysisTableV2(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  const result = await readBusinessAnalysisTable(raw, principal, signal);
  if (new TextEncoder().encode(JSON.stringify(result)).byteLength > 38_000) {
    throw new PublicApiError(413, "payload_too_large", "分析表页超过工具字节容量，请减小页长，不得截断");
  }
  return result;
}

export async function readBusinessBudget(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const { reportId, ...args } = raw as { reportId: string; runId: string; offset?: number; limit?: number };
  const query = new URLSearchParams({ limit: "10" });
  for (const [key, value] of Object.entries(args)) query.set(key, String(value));
  const result = await requestDjangoAi<Record<string, unknown>>(principal,
    { path: `/api/ai/reports/${reportId}/budget`, method: "GET", query }, { signal });
  if (JSON.stringify(result.data).length > 38_000) throw new PublicApiError(413, "payload_too_large", "预算情景页过大，请减小页长，不得截断");
  return result.data;
}

export async function readBusinessBudgetReferenceV1(raw: unknown, principal: AppPrincipal, signal?: AbortSignal) {
  requireAnalysisPrincipal(principal);
  const args = raw as { reportId: string; runId: string; offset?: number };
  if (!args || typeof args !== "object" || Array.isArray(args)
    || Object.keys(args).some(key => !["reportId", "runId", "offset"].includes(key))
    || typeof args.reportId !== "string" || !/^[A-Za-z0-9_-]{1,160}$/.test(args.reportId)
    || typeof args.runId !== "string" || !/^[A-Za-z0-9_-]{1,160}$/.test(args.runId)
    || (args.offset !== undefined && (!Number.isSafeInteger(args.offset) || args.offset < 0 || args.offset > 99))) {
    throw new PublicApiError(400, "invalid_request", "固定预算引用参数无效");
  }
  const result = await requestDjangoAi<Record<string, unknown>>(principal, {
    path: `/api/ai/reports/${args.reportId}/budget-reference`, method: "GET",
    query: new URLSearchParams({ runId: args.runId, offset: String(args.offset ?? 0), limit: "20" }),
  }, { signal });
  if (new TextEncoder().encode(JSON.stringify(result.data)).byteLength > 38_000) {
    throw new PublicApiError(413, "payload_too_large", "固定预算页超过工具字节容量，不得截断");
  }
  if (result.data?.schemaVersion !== "business-budget-page-v1") {
    throw new PublicApiError(409, "conflict", "固定预算页协议不匹配");
  }
  return result.data;
}
