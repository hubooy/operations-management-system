import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createSalesGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const SALES_IMPORTS_PATH = "/api/sales/imports";
export const SALES_RAW_UPLOADS_PATH = "/api/sales/imports/uploads";
export const SALES_RAW_UPLOAD_CHUNK_PATH = "/api/sales/imports/uploads/chunk";
export const SALES_STAGED_IMPORTS_PATH = "/api/sales/imports/staged";
export const SALES_IMPORT_VERIFY_PATH = "/api/sales/imports/verify";

const WRITER_ONLY_SALES_PATHS = new Set([
  SALES_RAW_UPLOADS_PATH,
  SALES_RAW_UPLOAD_CHUNK_PATH,
  SALES_STAGED_IMPORTS_PATH,
]);

const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 15 * 60_000;
const DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const MAX_REQUEST_BYTES = 16 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoSalesServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoSalesServiceOptions = {
  config?: DjangoSalesServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type DjangoSalesServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
};

export class DjangoSalesServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
  ) {
    super(status, code, message);
    this.name = "DjangoSalesServiceResponseError";
  }
}

function unavailable(message = "Django 销售服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) throw unavailable();
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable();
  return boundedInteger(Number(value), fallback, maximum);
}

async function loadConfig(): Promise<DjangoSalesServiceConfig> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Tests and command-line tools may inject a config or use process.env.
  }
  const processEnvironment = globalThis.process?.env as RuntimeEnvironment | undefined;
  const value = (key: string) => workerEnvironment[key] ?? processEnvironment?.[key];
  const readerBaseUrl = value("TERUISI_DJANGO_SALES_READER_BASE_URL")
    ?? value("TERUISI_DJANGO_SALES_BASE_URL")
    ?? "";
  const writerBaseUrl = value("TERUISI_DJANGO_SALES_WRITER_BASE_URL") ?? "";
  return {
    readerBaseUrl,
    writerBaseUrl,
    internalSecret: value("TERUISI_DJANGO_INTERNAL_SECRET") ?? "",
    timeoutMs: environmentInteger(
      value("TERUISI_DJANGO_SALES_WRITER_TIMEOUT_MS"),
      DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ),
    maxRequestBytes: environmentInteger(
      value("TERUISI_DJANGO_SALES_WRITER_MAX_REQUEST_BYTES"),
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: environmentInteger(
      value("TERUISI_DJANGO_SALES_MAX_RESPONSE_BYTES"),
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
    throw unavailable("Django 销售服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1"
    || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 销售服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoSalesServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) {
    throw unavailable("Django 销售服务配置不完整。");
  }
  return {
    readerBaseUrl: normalizeBaseUrl(config.readerBaseUrl),
    writerBaseUrl: normalizeBaseUrl(config.writerBaseUrl),
    internalSecret: config.internalSecret,
    timeoutMs: boundedInteger(config.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: boundedInteger(config.maxRequestBytes, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: boundedInteger(config.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function jsonContentType(value: string | null): boolean {
  return typeof value === "string"
    && /^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function upstreamError(status: number, data: unknown): PublicApiError {
  if ([400, 404, 409, 413, 415, 422].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string"
      ? data.message
      : typeof data.error === "string"
        ? data.error
        : "销售请求未通过校验。";
    const code = typeof data.code === "string" && data.code
      ? data.code
      : status === 404
        ? "not_found"
        : status === 409
          ? "conflict"
          : status === 413
            ? "payload_too_large"
            : "invalid_request";
    const publicStatus = (status === 415 ? 422 : status) as PublicApiError["status"];
    const publicCode = (["invalid_request", "not_found", "conflict", "version_conflict", "payload_too_large", "service_unavailable"] as const)
      .find((candidate) => candidate === code) ?? "invalid_request";
    return new DjangoSalesServiceResponseError(publicStatus, publicCode, message, data);
  }
  return unavailable();
}

export async function requestDjangoSalesService<T>(
  principal: AppPrincipal,
  input: {
    method: "GET" | "POST" | "PUT";
    path: string;
    query?: URLSearchParams;
    payload?: Record<string, unknown>;
    service: "reader" | "writer";
  },
  options: DjangoSalesServiceOptions = {},
): Promise<DjangoSalesServiceResult<T>> {
  if (!input.path.startsWith("/api/sales/")) throw unavailable();
  if (input.service !== "writer" && WRITER_ONLY_SALES_PATHS.has(input.path)) throw unavailable();
  if (input.method === "GET" && input.payload !== undefined) throw unavailable();
  if (input.method !== "GET" && input.payload === undefined) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const rawQuery = input.query?.toString() ?? "";
  const body = input.payload === undefined ? undefined : encoder.encode(JSON.stringify(input.payload));
  if (body && body.byteLength > config.maxRequestBytes) {
    throw new PublicApiError(413, "payload_too_large", "规范化销售分片超过内部请求上限。");
  }
  const bodySha256 = body ? await salesGatewayBodySha256(body) : EMPTY_SHA256;
  const requestId = (options.requestId ?? (() => crypto.randomUUID()))();
  const headers = await createSalesGatewayAuthHeaders({
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
      init: {
        method: input.method,
        headers,
        body,
        cache: "no-store",
      },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!jsonContentType(response.headers.get("content-type")) || data === null) throw unavailable();
    if (response.status < 200 || response.status >= 300) throw upstreamError(response.status, data);
    return {
      status: response.status,
      data: data as T,
      replayed: response.headers.get("x-teruisi-write-replay") === "1",
    };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}

export function createDjangoSalesService(config?: DjangoSalesServiceConfig) {
  return {
    request: <T>(
      principal: AppPrincipal,
      input: Parameters<typeof requestDjangoSalesService<T>>[1],
      options: Omit<DjangoSalesServiceOptions, "config"> = {},
    ) => requestDjangoSalesService<T>(principal, input, { ...options, config }),
  };
}
