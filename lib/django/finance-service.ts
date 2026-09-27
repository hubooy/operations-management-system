import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createFinanceGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const FINANCE_IMPORTS_PATH = "/api/finance/imports";
export const FINANCE_RAW_WORKBOOK_ATTEST_PATH = "/api/finance/imports/raw-attest";
export const FINANCE_ANALYSIS_PATH = "/api/finance/analysis";
export const FINANCE_TARGETS_PATH = "/api/finance/targets";
export const FINANCE_TARGET_IMPORT_PATH = "/api/finance/targets/import";
export const FINANCE_CONSUMER_QUERY_PATH = "/api/finance/consumers/query";
export const FINANCE_BUSINESS_EVIDENCE_PAGE_PATH = "/api/finance/business-evidence/page";

const FINANCE_PATHS = new Set([
  FINANCE_IMPORTS_PATH,
  FINANCE_ANALYSIS_PATH,
  FINANCE_TARGETS_PATH,
  FINANCE_TARGET_IMPORT_PATH,
  FINANCE_CONSUMER_QUERY_PATH,
  FINANCE_BUSINESS_EVIDENCE_PAGE_PATH,
]);
const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_REQUEST_BYTES = 16 * 1024 * 1024;
const MAX_REQUEST_BYTES = 16 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type FinanceBackendMode = "django";

export type DjangoFinanceServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoFinanceServiceOptions = {
  config?: DjangoFinanceServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type DjangoFinanceServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string | null;
};

export class DjangoFinanceServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
    readonly upstreamCode: string,
  ) {
    super(status, code, message);
    this.name = "DjangoFinanceServiceResponseError";
  }
}

function unavailable(message = "Django 财务服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) throw unavailable();
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 财务服务配置不完整。");
  return boundedInteger(Number(value), fallback, maximum);
}

async function runtimeEnvironment(): Promise<RuntimeEnvironment> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Tests and command-line tools may inject config or use process.env.
  }
  const processEnvironment = (globalThis as typeof globalThis & {
    process?: { env?: RuntimeEnvironment };
  }).process?.env;
  return { ...(processEnvironment ?? {}), ...workerEnvironment };
}

export function financeBackendModeFromEnvironment(environment: RuntimeEnvironment): FinanceBackendMode {
  const mode = (environment.TERUISI_DJANGO_FINANCE_MODE ?? "django").trim().toLowerCase();
  if (mode === "django") return mode;
  throw unavailable("Django 财务路由模式配置无效。");
}

export async function getFinanceBackendMode(): Promise<FinanceBackendMode> {
  return financeBackendModeFromEnvironment(await runtimeEnvironment());
}

async function loadConfig(): Promise<DjangoFinanceServiceConfig> {
  const environment = await runtimeEnvironment();
  financeBackendModeFromEnvironment(environment);
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_FINANCE_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_FINANCE_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(
      environment.TERUISI_DJANGO_FINANCE_TIMEOUT_MS,
      DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ),
    maxRequestBytes: environmentInteger(
      environment.TERUISI_DJANGO_FINANCE_MAX_REQUEST_BYTES,
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: environmentInteger(
      environment.TERUISI_DJANGO_FINANCE_MAX_RESPONSE_BYTES,
      DEFAULT_MAX_RESPONSE_BYTES,
      MAX_RESPONSE_BYTES,
    ),
  };
}

function normalizeBaseUrl(value: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw unavailable("Django 财务服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1"
    || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 财务服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoFinanceServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) {
    throw unavailable("Django 财务服务配置不完整。");
  }
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) {
    throw unavailable("Django 财务读写服务必须使用独立端点。");
  }
  return {
    readerBaseUrl,
    writerBaseUrl,
    internalSecret: config.internalSecret,
    timeoutMs: boundedInteger(config.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: boundedInteger(config.maxRequestBytes, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: boundedInteger(config.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function jsonContentType(value: string | null): boolean {
  return typeof value === "string"
    && /^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(value);
}

function upstreamError(status: number, data: unknown): PublicApiError {
  if ([400, 404, 409, 413, 415, 422, 503].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string"
      ? data.message
      : typeof data.error === "string"
        ? data.error
        : "财务请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 415 ? 422 : status) as PublicApiError["status"];
    const publicCode = (["invalid_request", "not_found", "conflict", "version_conflict", "payload_too_large", "service_unavailable"] as const)
      .find((candidate) => candidate === upstreamCode)
      ?? (status === 404 ? "not_found" : status === 409 ? "conflict" : status === 413 ? "payload_too_large" : status === 503 ? "service_unavailable" : "invalid_request");
    return new DjangoFinanceServiceResponseError(publicStatus, publicCode, message, data, upstreamCode);
  }
  return unavailable();
}

export async function requestDjangoFinanceService<T>(
  principal: AppPrincipal,
  input: {
    method: "GET" | "POST" | "DELETE";
    path: string;
    query?: URLSearchParams;
    payload?: Record<string, unknown>;
    service: "reader" | "writer";
    acceptedErrorStatuses?: readonly number[];
  },
  options: DjangoFinanceServiceOptions = {},
): Promise<DjangoFinanceServiceResult<T>> {
  if (!FINANCE_PATHS.has(input.path)) throw unavailable();
  const readerRequest = input.service === "reader" && (
    (input.method === "GET" && input.path !== FINANCE_CONSUMER_QUERY_PATH)
    || (input.method === "POST" && (input.path === FINANCE_CONSUMER_QUERY_PATH || input.path === FINANCE_BUSINESS_EVIDENCE_PAGE_PATH))
  );
  const writerRequest = input.service === "writer" && (
    (input.method === "POST" && (input.path === FINANCE_IMPORTS_PATH || input.path === FINANCE_TARGETS_PATH || input.path === FINANCE_TARGET_IMPORT_PATH))
    || (input.method === "DELETE" && input.path === FINANCE_TARGETS_PATH)
  );
  if (!readerRequest && !writerRequest) throw unavailable();
  if (input.method === "POST" && input.payload === undefined) throw unavailable();
  if (input.method !== "POST" && input.payload !== undefined) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const rawQuery = input.query?.toString() ?? "";
  const body = input.payload === undefined ? undefined : encoder.encode(JSON.stringify(input.payload));
  if (body && body.byteLength > config.maxRequestBytes) {
    throw new PublicApiError(413, "payload_too_large", "规范化财务数据超过内部请求上限。");
  }
  const bodySha256 = body ? await salesGatewayBodySha256(body) : EMPTY_SHA256;
  const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
  const headers = await createFinanceGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: input.method,
    path: input.path,
    rawQuery,
    bodySha256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId,
  });
  if (body) headers.set("content-type", "application/json; charset=utf-8");
  const baseUrl = input.service === "writer" ? config.writerBaseUrl : config.readerBaseUrl;
  const target = new URL(`${input.path}${rawQuery ? `?${rawQuery}` : ""}`, baseUrl);
  try {
    const { response, data } = await fetchBoundedJson({
      url: target.toString(),
      init: { method: input.method, headers, body, cache: "no-store" },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!jsonContentType(response.headers.get("content-type")) || !isRecord(data)) throw unavailable();
    const accepted = input.acceptedErrorStatuses?.includes(response.status) ?? false;
    if ((response.status < 200 || response.status >= 300) && !accepted) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-finance-data-revision");
    if (readerRequest && (response.status < 200 || response.status >= 300 || !revision
      || !/^\d+:[a-f0-9]{12}$/.test(revision))) {
      throw unavailable();
    }
    return {
      status: response.status,
      data: data as T,
      replayed: response.headers.get("x-teruisi-write-replay") === "1",
      revision,
    };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}

/** Exact signed byte follow-up after an already completed legacy v1 import.
 * It cannot import, replace, or roll back any finance facts. */
export async function requestDjangoFinanceRawWorkbookAttestation(
  principal: AppPrincipal,
  input: { bytes: Uint8Array; month: string; batchId: string },
  options: DjangoFinanceServiceOptions = {},
): Promise<DjangoFinanceServiceResult<Record<string, unknown>>> {
  if (!(input.bytes instanceof Uint8Array) || input.bytes.byteLength < 1
    || input.bytes.byteLength > 8 * 1024 * 1024
    || !/^\d{4}-(?:0[1-9]|1[0-2])$/.test(input.month)
    || !/^[A-Za-z0-9_-]{1,64}$/.test(input.batchId)) {
    throw new PublicApiError(422, "invalid_request", "财报原始字节验真目标无效。");
  }
  const config = normalizedConfig(options.config ?? await loadConfig());
  const query = new URLSearchParams({ month: input.month, batchId: input.batchId });
  const rawQuery = query.toString();
  const bodySha256 = await salesGatewayBodySha256(input.bytes);
  const rawBody = new ArrayBuffer(input.bytes.byteLength);
  new Uint8Array(rawBody).set(input.bytes);
  const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
  const headers = await createFinanceGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: "POST",
    path: FINANCE_RAW_WORKBOOK_ATTEST_PATH,
    rawQuery,
    bodySha256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId,
  });
  headers.set("content-type", "application/octet-stream");
  const target = new URL(`${FINANCE_RAW_WORKBOOK_ATTEST_PATH}?${rawQuery}`,
    config.writerBaseUrl);
  try {
    const { response, data } = await fetchBoundedJson({
      url: target.toString(),
      init: { method: "POST", headers, body: rawBody, cache: "no-store" },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!jsonContentType(response.headers.get("content-type")) || !isRecord(data)) throw unavailable();
    if ((response.status !== 200 && response.status !== 201) || data.ok !== true
      || data.backendRawBytesObserved !== true || data.reportAuthorityVerified !== false
      || data.stableNetshopShopIdentityVerified !== false) {
      throw upstreamError(response.status, data);
    }
    return { status: response.status, data,
      replayed: response.headers.get("x-teruisi-write-replay") === "1",
      revision: response.headers.get("x-finance-data-revision") };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}

export function createDjangoFinanceService(config?: DjangoFinanceServiceConfig) {
  return {
    request: <T>(
      principal: AppPrincipal,
      input: Parameters<typeof requestDjangoFinanceService<T>>[1],
      options: Omit<DjangoFinanceServiceOptions, "config"> = {},
    ) => requestDjangoFinanceService<T>(principal, input, { ...options, config }),
    attestRawWorkbook: (principal: AppPrincipal,
      input: Parameters<typeof requestDjangoFinanceRawWorkbookAttestation>[1],
      options: Omit<DjangoFinanceServiceOptions, "config"> = {}) =>
      requestDjangoFinanceRawWorkbookAttestation(principal, input,
        { ...options, config }),
  };
}
