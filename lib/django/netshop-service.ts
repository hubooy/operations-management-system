import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createNetshopGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const NETSHOP_IMPORTS_PATH = "/api/netshop/imports";
export const NETSHOP_OVERVIEW_PATH = "/api/netshop/overview";
export const NETSHOP_PRODUCTS_PATH = "/api/netshop/products";
export const NETSHOP_PRODUCT_PERFORMANCE_PATH = "/api/netshop/product-performance";
export const NETSHOP_PROMOTION_PERFORMANCE_PATH = "/api/netshop/promotion-performance";
export const NETSHOP_PROMOTION_OVERVIEW_PATH = "/api/netshop/promotion-performance/overview";
export const NETSHOP_PROMOTION_ITEMS_PATH = "/api/netshop/promotion-performance/items";
export const NETSHOP_PROMOTION_DIAGNOSTIC_PATH = "/api/netshop/promotion-diagnostic";
export const NETSHOP_CONSUMER_QUERY_PATH = "/api/netshop/consumers/query";
export const NETSHOP_ASSET_UPLOADS_PATH = "/api/netshop/asset-uploads";
export const NETSHOP_ANALYSIS_RECORDS_PATH = "/api/netshop/analysis-records";
export const NETSHOP_ANALYSIS_CONTINUATION_PATH = "/api/netshop/analysis-records/continuation";
export const NETSHOP_ANALYSIS_OPTIONS_PATH = "/api/netshop/analysis-options";

const STATIC_PATHS = new Set([
  NETSHOP_ANALYSIS_RECORDS_PATH,
  NETSHOP_ANALYSIS_CONTINUATION_PATH,
  NETSHOP_ANALYSIS_OPTIONS_PATH,
  NETSHOP_IMPORTS_PATH,
  NETSHOP_OVERVIEW_PATH,
  NETSHOP_PRODUCTS_PATH,
  NETSHOP_PRODUCT_PERFORMANCE_PATH,
  NETSHOP_PROMOTION_PERFORMANCE_PATH,
  NETSHOP_PROMOTION_OVERVIEW_PATH,
  NETSHOP_PROMOTION_ITEMS_PATH,
  NETSHOP_PROMOTION_DIAGNOSTIC_PATH,
  NETSHOP_CONSUMER_QUERY_PATH,
  NETSHOP_ASSET_UPLOADS_PATH,
]);
const PRODUCT_IMAGE_METADATA_PATH = /^\/api\/netshop\/product-images\/[a-f0-9]{64}\/metadata$/;
const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_REQUEST_BYTES = 64 * 1024 * 1024;
const MAX_REQUEST_BYTES = 128 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 12 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoNetshopServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoNetshopServiceOptions = {
  config?: DjangoNetshopServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type DjangoNetshopServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string | null;
};

export class DjangoNetshopServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
    readonly upstreamCode: string,
  ) {
    super(status, code, message);
    this.name = "DjangoNetshopServiceResponseError";
  }
}

function unavailable(message = "Django 网店服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) throw unavailable();
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 网店服务配置不完整。");
  return boundedInteger(Number(value), fallback, maximum);
}

async function runtimeEnvironment(): Promise<RuntimeEnvironment> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Unit tests and command-line tools can inject config or use process.env.
  }
  const processEnvironment = (globalThis as typeof globalThis & {
    process?: { env?: RuntimeEnvironment };
  }).process?.env;
  return { ...(processEnvironment ?? {}), ...workerEnvironment };
}

async function loadConfig(): Promise<DjangoNetshopServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_NETSHOP_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_NETSHOP_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(
      environment.TERUISI_DJANGO_NETSHOP_TIMEOUT_MS,
      DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ),
    maxRequestBytes: environmentInteger(
      environment.TERUISI_DJANGO_NETSHOP_MAX_REQUEST_BYTES,
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: environmentInteger(
      environment.TERUISI_DJANGO_NETSHOP_MAX_RESPONSE_BYTES,
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
    throw unavailable("Django 网店服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1"
    || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 网店服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoNetshopServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) {
    throw unavailable("Django 网店服务配置不完整。");
  }
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) {
    throw unavailable("Django 网店读写服务必须使用独立端点。");
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

function validPath(path: string): boolean {
  return STATIC_PATHS.has(path) || PRODUCT_IMAGE_METADATA_PATH.test(path);
}

function upstreamError(status: number, data: unknown): PublicApiError {
  if ([400, 403, 404, 409, 413, 415, 422, 503].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string"
      ? data.message
      : typeof data.error === "string"
        ? data.error
        : "网店请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 415 ? 422 : status) as PublicApiError["status"];
    const publicCode = (["invalid_request", "not_found", "conflict", "version_conflict", "payload_too_large", "service_unavailable", "access_denied"] as const)
      .find((candidate) => candidate === upstreamCode)
      ?? (status === 403 ? "access_denied" : status === 404 ? "not_found" : status === 409 ? "conflict" : status === 413 ? "payload_too_large" : status === 503 ? "service_unavailable" : "invalid_request");
    return new DjangoNetshopServiceResponseError(publicStatus, publicCode, message, data, upstreamCode);
  }
  return unavailable();
}

export async function requestDjangoNetshopService<T>(
  principal: AppPrincipal,
  input: {
    method: "GET" | "POST";
    path: string;
    query?: URLSearchParams;
    payload?: Record<string, unknown>;
    service: "reader" | "writer";
    acceptedErrorStatuses?: readonly number[];
  },
  options: DjangoNetshopServiceOptions = {},
): Promise<DjangoNetshopServiceResult<T>> {
  if (!validPath(input.path)) throw unavailable();
  const readerRequest = input.service === "reader" && (
    input.method === "GET"
    || (input.method === "POST" && input.path === NETSHOP_CONSUMER_QUERY_PATH)
  );
  const writerRequest = input.service === "writer"
    && input.method === "POST"
    && (input.path === NETSHOP_IMPORTS_PATH || input.path === NETSHOP_ASSET_UPLOADS_PATH);
  if (!readerRequest && !writerRequest) throw unavailable();
  if (input.method === "POST" && input.payload === undefined) throw unavailable();
  if (input.method === "GET" && input.payload !== undefined) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const rawQuery = input.query?.toString() ?? "";
  const body = input.payload === undefined ? undefined : encoder.encode(JSON.stringify(input.payload));
  if (body && body.byteLength > config.maxRequestBytes) {
    throw new PublicApiError(413, "payload_too_large", "规范化网店数据超过内部请求上限。");
  }
  const bodySha256 = body ? await salesGatewayBodySha256(body) : EMPTY_SHA256;
  const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
  const headers = await createNetshopGatewayAuthHeaders({
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
    const revision = response.headers.get("x-netshop-data-revision");
    if (readerRequest && response.status >= 200 && response.status < 300
      && (!revision || !/^\d+:[a-f0-9]{12}$/.test(revision))) {
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

export function createDjangoNetshopService(config?: DjangoNetshopServiceConfig) {
  return {
    request: <T>(
      principal: AppPrincipal,
      input: Parameters<typeof requestDjangoNetshopService<T>>[1],
      options: Omit<DjangoNetshopServiceOptions, "config"> = {},
    ) => requestDjangoNetshopService<T>(principal, input, { ...options, config }),
  };
}
