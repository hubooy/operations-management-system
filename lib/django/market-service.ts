import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createMarketGatewayAuthHeaders,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const MARKET_QUERIES_PATH = "/api/market/queries";
export const MARKET_CONSUMER_QUERY_PATH = "/api/market/consumers/query";
export const MARKET_COMMANDS_PATH = "/api/market/commands";
export const MARKET_IMPORTS_PATH = "/api/market/imports";
export const MARKET_ANALYSIS_OPTIONS_PATH = "/api/market/analysis-options";
export const MARKET_ANALYSIS_CONTINUATION_PATH = "/api/market/analysis-records/continuation";

const READER_PATHS = new Set([MARKET_QUERIES_PATH, MARKET_CONSUMER_QUERY_PATH]);
const WRITER_PATHS = new Set([MARKET_COMMANDS_PATH, MARKET_IMPORTS_PATH]);
const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_REQUEST_BYTES = 64 * 1024 * 1024;
const MAX_REQUEST_BYTES = 128 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoMarketServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoMarketServiceOptions = {
  config?: DjangoMarketServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type DjangoMarketServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string;
};

export class DjangoMarketServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
    readonly upstreamCode: string,
  ) {
    super(status, code, message);
    this.name = "DjangoMarketServiceResponseError";
  }
}

function unavailable(message = "Django 市场服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    throw unavailable("Django 市场服务配置不完整。");
  }
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 市场服务配置不完整。");
  return boundedInteger(Number(value), fallback, maximum);
}

async function runtimeEnvironment(): Promise<RuntimeEnvironment> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Tests and local tools can inject config or use process.env.
  }
  const processEnvironment = (globalThis as typeof globalThis & {
    process?: { env?: RuntimeEnvironment };
  }).process?.env;
  return { ...(processEnvironment ?? {}), ...workerEnvironment };
}

async function loadConfig(): Promise<DjangoMarketServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_MARKET_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_MARKET_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(
      environment.TERUISI_DJANGO_MARKET_TIMEOUT_MS,
      DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ),
    maxRequestBytes: environmentInteger(
      environment.TERUISI_DJANGO_MARKET_MAX_REQUEST_BYTES,
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: environmentInteger(
      environment.TERUISI_DJANGO_MARKET_MAX_RESPONSE_BYTES,
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
    throw unavailable("Django 市场服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1"
    || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 市场服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoMarketServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) {
    throw unavailable("Django 市场服务配置不完整。");
  }
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) {
    throw unavailable("Django 市场读写服务必须使用独立端点。");
  }
  return {
    readerBaseUrl,
    writerBaseUrl,
    internalSecret: config.internalSecret,
    timeoutMs: boundedInteger(config.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: boundedInteger(
      config.maxRequestBytes,
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: boundedInteger(
      config.maxResponseBytes,
      DEFAULT_MAX_RESPONSE_BYTES,
      MAX_RESPONSE_BYTES,
    ),
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
  if ([400, 403, 404, 409, 413, 415, 422, 503].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string"
      ? data.message
      : typeof data.error === "string"
        ? data.error
        : "市场请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 415 ? 422 : status) as PublicApiError["status"];
    const publicCode = ([
      "invalid_request",
      "not_found",
      "conflict",
      "version_conflict",
      "payload_too_large",
      "service_unavailable",
      "access_denied",
    ] as const).find((candidate) => candidate === upstreamCode)
      ?? (status === 403 ? "access_denied"
        : status === 404 ? "not_found"
          : status === 409 ? "conflict"
            : status === 413 ? "payload_too_large"
              : status === 503 ? "service_unavailable"
                : "invalid_request");
    return new DjangoMarketServiceResponseError(
      publicStatus,
      publicCode,
      message,
      data,
      upstreamCode,
    );
  }
  return unavailable();
}

export async function requestDjangoMarketService<T>(
  principal: AppPrincipal,
  input: {
    path: string;
    payload: Record<string, unknown>;
    service: "reader" | "writer";
    acceptedErrorStatuses?: readonly number[];
  },
  options: DjangoMarketServiceOptions = {},
): Promise<DjangoMarketServiceResult<T>> {
  const pathAllowed = input.service === "reader"
    ? READER_PATHS.has(input.path)
    : WRITER_PATHS.has(input.path);
  if (!pathAllowed) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const body = encoder.encode(JSON.stringify(input.payload));
  if (body.byteLength > config.maxRequestBytes) {
    throw new PublicApiError(413, "payload_too_large", "规范化市场数据超过内部请求上限。");
  }
  const bodySha256 = await salesGatewayBodySha256(body);
  const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
  const headers = await createMarketGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: "POST",
    path: input.path,
    rawQuery: "",
    bodySha256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId,
  });
  headers.set("content-type", "application/json; charset=utf-8");
  const baseUrl = input.service === "writer" ? config.writerBaseUrl : config.readerBaseUrl;
  const target = new URL(input.path, baseUrl);
  try {
    const { response, data } = await fetchBoundedJson({
      url: target.toString(),
      init: { method: "POST", headers, body, cache: "no-store" },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!jsonContentType(response.headers.get("content-type")) || !isRecord(data)) {
      throw unavailable();
    }
    const accepted = input.acceptedErrorStatuses?.includes(response.status) ?? false;
    if ((response.status < 200 || response.status >= 300) && !accepted) {
      throw upstreamError(response.status, data);
    }
    const revision = response.headers.get("x-market-data-revision") ?? "";
    if (response.status >= 200 && response.status < 300 && !/^\d+:[a-f0-9]{12}$/.test(revision)) {
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

export function createDjangoMarketService(config?: DjangoMarketServiceConfig) {
  return {
    request: <T>(
      principal: AppPrincipal,
      input: Parameters<typeof requestDjangoMarketService<T>>[1],
      options: Omit<DjangoMarketServiceOptions, "config"> = {},
    ) => requestDjangoMarketService<T>(principal, input, { ...options, config }),
  };
}

/** A fixed read-only endpoint; existing POST query/command contracts stay closed. */
export async function requestMarketAnalysisOptions<T>(
  principal: AppPrincipal, query: URLSearchParams, options: DjangoMarketServiceOptions = {},
): Promise<DjangoMarketServiceResult<T>> {
  const config = normalizedConfig(options.config ?? await loadConfig());
  const rawQuery = query.toString();
  const headers = await createMarketGatewayAuthHeaders({
    secret: config.internalSecret, principal, method: "GET", path: MARKET_ANALYSIS_OPTIONS_PATH,
    rawQuery, bodySha256: await salesGatewayBodySha256(new Uint8Array()),
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId: (options.requestId ?? (() => crypto.randomUUID()))(),
  });
  const target = new URL(MARKET_ANALYSIS_OPTIONS_PATH, config.readerBaseUrl);
  target.search = rawQuery;
  try {
    const { response, data } = await fetchBoundedJson({
      url: target.toString(), init: { method: "GET", headers, cache: "no-store" },
      timeoutMs: config.timeoutMs, maxBytes: Math.min(config.maxResponseBytes, 38_000),
      fetcher: options.fetchImpl, signal: options.signal,
    });
    if (!jsonContentType(response.headers.get("content-type")) || !isRecord(data)) throw unavailable();
    if (!response.ok) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-market-data-revision") ?? "";
    if (!/^\d+:[a-f0-9]{12}$/.test(revision) || revision !== data.revision) throw unavailable();
    return { status: response.status, data: data as T, replayed: false, revision };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    throw unavailable();
  }
}

/** Dedicated GET, not a widening of the generic POST reader allowlist. */
export async function requestMarketAnalysisContinuation(
  principal: AppPrincipal, query: URLSearchParams, options: DjangoMarketServiceOptions = {},
): Promise<Record<string, unknown>> {
  const rawQuery = query.toString(), fixed = new URLSearchParams(rawQuery);
  if (options.signal?.aborted) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const headers = await createMarketGatewayAuthHeaders({ secret: config.internalSecret, principal, method: "GET",
    path: MARKET_ANALYSIS_CONTINUATION_PATH, rawQuery, bodySha256: await salesGatewayBodySha256(new Uint8Array()),
    timestamp: Math.floor((options.now ?? Date.now)() / 1000), requestId: (options.requestId ?? (() => crypto.randomUUID()))() });
  const target = new URL(MARKET_ANALYSIS_CONTINUATION_PATH, config.readerBaseUrl); target.search = rawQuery;
  try {
    const { response, data } = await fetchBoundedJson({ url: target.toString(),
      init: { method: "GET", headers, cache: "no-store", redirect: "manual" }, timeoutMs: config.timeoutMs,
      maxBytes: Math.min(config.maxResponseBytes, 131_072), fetcher: options.fetchImpl, signal: options.signal });
    if (options.signal?.aborted || !jsonContentType(response.headers.get("content-type")) || !isRecord(data)) throw unavailable();
    if (!response.ok) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-market-data-revision") ?? "";
    if (!/^(0|[1-9]\d*):[a-f0-9]{12}$/.test(revision) || revision !== data.sourceRevision
      || revision !== fixed.get("expectedRevision") || data.sourceRef !== fixed.get("expectedSourceRef"))
      throw new PublicApiError(409, "conflict", "市场续读来源版本或绑定与检查点不一致");
    return data;
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError && error.code === "response_too_large")
      throw new PublicApiError(413, "payload_too_large", "市场续读完整页超过容量");
    throw unavailable();
  }
}
