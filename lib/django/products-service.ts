import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createProductsGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const PRODUCTS_SUMMARY_PATH = "/api/products/summary";
export const PRODUCTS_IMPORTS_PATH = "/api/products/imports";
export const PRODUCTS_CONSUMER_QUERY_PATH = "/api/products/consumers/query";
export const PRODUCTS_UPLOADS_PATH = "/api/products/uploads";
export const PRODUCTS_UPLOAD_CHUNK_PATH = "/api/products/uploads/chunk";
export const PRODUCTS_INVENTORY_PROJECTION_PATH = "/api/products/inventory-projection";

const readerMethods = new Set([
  `GET ${PRODUCTS_SUMMARY_PATH}`,
  `GET ${PRODUCTS_IMPORTS_PATH}`,
  `POST ${PRODUCTS_CONSUMER_QUERY_PATH}`,
]);
const writerMethods = new Set([
  `POST ${PRODUCTS_IMPORTS_PATH}`,
  `POST ${PRODUCTS_UPLOADS_PATH}`,
  `GET ${PRODUCTS_UPLOAD_CHUNK_PATH}`,
  `PUT ${PRODUCTS_UPLOAD_CHUNK_PATH}`,
  `POST ${PRODUCTS_INVENTORY_PROJECTION_PATH}`,
]);
const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024;
const MAX_REQUEST_BYTES = 64 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoProductsServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoProductsServiceOptions = {
  config?: DjangoProductsServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type DjangoProductsServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string;
};

export class DjangoProductsServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
    readonly upstreamCode: string,
  ) {
    super(status, code, message);
    this.name = "DjangoProductsServiceResponseError";
  }
}

function unavailable(message = "Django 商品经营服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    throw unavailable("Django 商品经营服务配置不完整。");
  }
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 商品经营服务配置不完整。");
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

async function loadConfig(): Promise<DjangoProductsServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_PRODUCTS_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_PRODUCTS_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(
      environment.TERUISI_DJANGO_PRODUCTS_TIMEOUT_MS,
      DEFAULT_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ),
    maxRequestBytes: environmentInteger(
      environment.TERUISI_DJANGO_PRODUCTS_MAX_REQUEST_BYTES,
      DEFAULT_MAX_REQUEST_BYTES,
      MAX_REQUEST_BYTES,
    ),
    maxResponseBytes: environmentInteger(
      environment.TERUISI_DJANGO_PRODUCTS_MAX_RESPONSE_BYTES,
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
    throw unavailable("Django 商品经营服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1"
    || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 商品经营服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoProductsServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) {
    throw unavailable("Django 商品经营服务配置不完整。");
  }
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) {
    throw unavailable("Django 商品经营读写服务必须使用独立端点。");
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
  if ([400, 403, 404, 409, 413, 415, 422, 503].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string"
      ? data.message
      : typeof data.error === "string"
        ? data.error
        : "商品经营请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 415 ? 422 : status) as PublicApiError["status"];
    const publicCode = ([
      "invalid_request", "not_found", "conflict", "version_conflict",
      "payload_too_large", "service_unavailable", "access_denied",
    ] as const).find((candidate) => candidate === upstreamCode)
      ?? (status === 403 ? "access_denied"
        : status === 404 ? "not_found"
          : status === 409 ? "conflict"
            : status === 413 ? "payload_too_large"
              : status === 503 ? "service_unavailable"
                : "invalid_request");
    return new DjangoProductsServiceResponseError(
      publicStatus, publicCode, message, data, upstreamCode,
    );
  }
  return unavailable();
}

type ProductsRequestInput = {
  method: "GET" | "POST" | "PUT";
  path: string;
  service: "reader" | "writer";
  rawQuery?: string;
  payload?: Record<string, unknown>;
  bodyBytes?: Uint8Array;
  additionalHeaders?: Record<string, string>;
};

async function prepareRequest(
  principal: AppPrincipal,
  input: ProductsRequestInput,
  options: DjangoProductsServiceOptions,
) {
  const allowlist = input.service === "reader" ? readerMethods : writerMethods;
  if (!allowlist.has(`${input.method} ${input.path}`)) throw unavailable();
  if (input.payload !== undefined && input.bodyBytes !== undefined) throw unavailable();
  if (input.method === "GET" && (input.payload !== undefined || input.bodyBytes !== undefined)) {
    throw unavailable();
  }
  const config = normalizedConfig(options.config ?? await loadConfig());
  const body = input.payload !== undefined
    ? encoder.encode(JSON.stringify(input.payload))
    : input.bodyBytes ?? new Uint8Array();
  if (body.byteLength > config.maxRequestBytes) {
    throw new PublicApiError(413, "payload_too_large", "商品经营内部请求超过安全上限。");
  }
  const rawQuery = input.rawQuery ?? "";
  if (rawQuery.startsWith("?") || rawQuery.length > 16_384 || /[\r\n]/.test(rawQuery)) throw unavailable();
  const headers = await createProductsGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: input.method,
    path: input.path,
    rawQuery,
    bodySha256: body.byteLength ? await salesGatewayBodySha256(body) : EMPTY_SHA256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId: (options.requestId ?? (() => crypto.randomUUID()))(),
  });
  if (input.payload !== undefined) headers.set("content-type", "application/json; charset=utf-8");
  if (input.bodyBytes !== undefined) headers.set("content-type", "application/octet-stream");
  for (const [name, value] of Object.entries(input.additionalHeaders ?? {})) {
    const lower = name.toLowerCase();
    if (!["x-upload-id", "x-chunk-index", "x-upload-owner-token"].includes(lower)
      || !value || value.length > 200 || /[\r\n]/.test(value)) throw unavailable();
    headers.set(lower, value);
  }
  const baseUrl = input.service === "writer" ? config.writerBaseUrl : config.readerBaseUrl;
  const target = new URL(input.path, baseUrl);
  target.search = rawQuery;
  const init: RequestInit = {
    method: input.method,
    headers,
    body: input.method === "GET" ? undefined : body.buffer.slice(
      body.byteOffset,
      body.byteOffset + body.byteLength,
    ) as ArrayBuffer,
    cache: "no-store",
  };
  return { config, target, init };
}

export async function requestDjangoProductsJson<T>(
  principal: AppPrincipal,
  input: ProductsRequestInput,
  options: DjangoProductsServiceOptions = {},
): Promise<DjangoProductsServiceResult<T>> {
  const prepared = await prepareRequest(principal, input, options);
  try {
    const { response, data } = await fetchBoundedJson({
      url: prepared.target.toString(),
      init: prepared.init,
      timeoutMs: prepared.config.timeoutMs,
      maxBytes: prepared.config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!jsonContentType(response.headers.get("content-type")) || !isRecord(data)) {
      throw unavailable();
    }
    if (response.status < 200 || response.status >= 300) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-product-data-revision") ?? "";
    if (!/^\d+:[a-f0-9]{12}$/.test(revision)) throw unavailable();
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

async function readBoundedBytes(response: Response, maximum: number): Promise<Uint8Array> {
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maximum) throw unavailable();
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > maximum) {
        await reader.cancel("response_too_large").catch(() => undefined);
        throw unavailable();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

export async function requestDjangoProductsBytes(
  principal: AppPrincipal,
  input: Omit<ProductsRequestInput, "method" | "path" | "service"> & { additionalHeaders: Record<string, string> },
  options: DjangoProductsServiceOptions = {},
): Promise<{ bytes: Uint8Array; sha256: string }> {
  const prepared = await prepareRequest(principal, {
    ...input,
    method: "GET",
    path: PRODUCTS_UPLOAD_CHUNK_PATH,
    service: "writer",
  }, options);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), prepared.config.timeoutMs);
  const external = options.signal;
  const abort = () => controller.abort(external?.reason);
  if (external?.aborted) abort();
  else external?.addEventListener("abort", abort, { once: true });
  try {
    const response = await (options.fetchImpl ?? fetch)(prepared.target.toString(), {
      ...prepared.init,
      redirect: "manual",
      signal: controller.signal,
    });
    if (response.status < 200 || response.status >= 300) {
      const data = await response.json().catch(() => null);
      throw upstreamError(response.status, data);
    }
    if (response.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/octet-stream") {
      throw unavailable();
    }
    const bytes = await readBoundedBytes(response, 1024 * 1024);
    const sha256 = response.headers.get("x-chunk-sha256") ?? "";
    if (!/^[a-f0-9]{64}$/.test(sha256)) throw unavailable();
    return { bytes, sha256 };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    throw unavailable();
  } finally {
    clearTimeout(timer);
    external?.removeEventListener("abort", abort);
  }
}

export function createDjangoProductsService(config?: DjangoProductsServiceConfig) {
  return {
    requestJson: <T>(
      principal: AppPrincipal,
      input: ProductsRequestInput,
      options: Omit<DjangoProductsServiceOptions, "config"> = {},
    ) => requestDjangoProductsJson<T>(principal, input, { ...options, config }),
    requestBytes: (
      principal: AppPrincipal,
      input: Parameters<typeof requestDjangoProductsBytes>[1],
      options: Omit<DjangoProductsServiceOptions, "config"> = {},
    ) => requestDjangoProductsBytes(principal, input, { ...options, config }),
  };
}
