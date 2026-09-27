import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createCustomerServiceGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const CUSTOMER_SERVICE_IMPORTS_PATH = "/api/customer-service/imports";
export const CUSTOMER_SERVICE_CONVERSATIONS_PATH = "/api/customer-service/conversations";
export const CUSTOMER_SERVICE_SNAPSHOTS_PATH = "/api/customer-service/conversations/snapshots";
export const CUSTOMER_SERVICE_CONSUMER_PATH = "/api/customer-service/consumers/query";
export const CUSTOMER_SERVICE_UPLOADS_PATH = "/api/customer-service/uploads";
export const CUSTOMER_SERVICE_UPLOAD_CHUNK_PATH = "/api/customer-service/uploads/chunk";

const readerMethods = new Set([
  `GET ${CUSTOMER_SERVICE_IMPORTS_PATH}`,
  `GET ${CUSTOMER_SERVICE_CONVERSATIONS_PATH}`,
  `POST ${CUSTOMER_SERVICE_SNAPSHOTS_PATH}`,
  `POST ${CUSTOMER_SERVICE_CONSUMER_PATH}`,
]);
const writerMethods = new Set([
  `POST ${CUSTOMER_SERVICE_IMPORTS_PATH}`,
  `PATCH ${CUSTOMER_SERVICE_CONVERSATIONS_PATH}`,
  `DELETE ${CUSTOMER_SERVICE_CONVERSATIONS_PATH}`,
  `POST ${CUSTOMER_SERVICE_UPLOADS_PATH}`,
  `GET ${CUSTOMER_SERVICE_UPLOAD_CHUNK_PATH}`,
  `PUT ${CUSTOMER_SERVICE_UPLOAD_CHUNK_PATH}`,
]);

const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_REQUEST_BYTES = 16 * 1024 * 1024;
const MAX_REQUEST_BYTES = 32 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoCustomerServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoCustomerServiceOptions = {
  config?: DjangoCustomerServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type CustomerServiceRequest = {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  service: "reader" | "writer";
  rawQuery?: string;
  payload?: Record<string, unknown>;
  bodyBytes?: Uint8Array;
  additionalHeaders?: Record<string, string>;
};

export type DjangoCustomerServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string;
};

function unavailable(message = "Django 客服服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) throw unavailable("Django 客服服务配置不完整。");
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number) {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 客服服务配置不完整。");
  return boundedInteger(Number(value), fallback, maximum);
}

async function runtimeEnvironment(): Promise<RuntimeEnvironment> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Tests and local tools may inject config or use process.env.
  }
  const processEnvironment = (globalThis as typeof globalThis & { process?: { env?: RuntimeEnvironment } }).process?.env;
  return { ...(processEnvironment ?? {}), ...workerEnvironment };
}

export function customerServiceBackendModeFromEnvironment(environment: RuntimeEnvironment): "django" {
  if ((environment.TERUISI_DJANGO_CUSTOMER_SERVICE_MODE ?? "").trim().toLowerCase() === "django") return "django";
  throw unavailable("客服域已终态切换到 Django，路由模式必须显式配置为 django。");
}

export async function getCustomerServiceBackendMode(): Promise<"django"> {
  return customerServiceBackendModeFromEnvironment(await runtimeEnvironment());
}

async function loadConfig(): Promise<DjangoCustomerServiceConfig> {
  const environment = await runtimeEnvironment();
  customerServiceBackendModeFromEnvironment(environment);
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_CUSTOMER_SERVICE_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_CUSTOMER_SERVICE_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(environment.TERUISI_DJANGO_CUSTOMER_SERVICE_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: environmentInteger(environment.TERUISI_DJANGO_CUSTOMER_SERVICE_MAX_REQUEST_BYTES, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: environmentInteger(environment.TERUISI_DJANGO_CUSTOMER_SERVICE_MAX_RESPONSE_BYTES, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function normalizeBaseUrl(value: string) {
  let parsed: URL;
  try { parsed = new URL(value); } catch { throw unavailable("Django 客服服务配置不完整。"); }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) throw unavailable("Django 客服服务配置不完整。");
  return parsed;
}

function normalizedConfig(config: DjangoCustomerServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) throw unavailable("Django 客服服务配置不完整。");
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) throw unavailable("Django 客服读写服务必须使用独立端点。");
  return {
    readerBaseUrl, writerBaseUrl, internalSecret: config.internalSecret,
    timeoutMs: boundedInteger(config.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: boundedInteger(config.maxRequestBytes, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: boundedInteger(config.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function upstreamError(status: number, data: unknown): PublicApiError {
  if ([400, 401, 403, 404, 409, 413, 415, 422, 503].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string" ? data.message : typeof data.error === "string" ? data.error : "客服请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 401 ? 503 : status === 415 ? 422 : status) as PublicApiError["status"];
    const code = (["invalid_request", "not_found", "conflict", "version_conflict", "payload_too_large", "service_unavailable", "access_denied"] as const)
      .find((candidate) => candidate === upstreamCode)
      ?? (status === 403 ? "access_denied" : status === 404 ? "not_found" : status === 409 ? "conflict" : status === 413 ? "payload_too_large" : status === 503 || status === 401 ? "service_unavailable" : "invalid_request");
    return new PublicApiError(publicStatus, code, message);
  }
  return unavailable();
}

async function prepare(principal: AppPrincipal, input: CustomerServiceRequest, options: DjangoCustomerServiceOptions) {
  const allowlist = input.service === "reader" ? readerMethods : writerMethods;
  if (!allowlist.has(`${input.method} ${input.path}`) || (input.payload !== undefined && input.bodyBytes !== undefined)) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const body = input.payload !== undefined ? encoder.encode(JSON.stringify(input.payload)) : input.bodyBytes ?? new Uint8Array();
  if (body.byteLength > config.maxRequestBytes) throw new PublicApiError(413, "payload_too_large", "客服内部请求超过安全上限。");
  const rawQuery = input.rawQuery ?? "";
  if (rawQuery.startsWith("?") || rawQuery.length > 16_384 || /[\r\n]/.test(rawQuery)) throw unavailable();
  const headers = await createCustomerServiceGatewayAuthHeaders({
    secret: config.internalSecret, principal, method: input.method, path: input.path, rawQuery,
    bodySha256: body.byteLength ? await salesGatewayBodySha256(body) : EMPTY_SHA256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId: (options.requestId ?? (() => crypto.randomUUID()))(),
  });
  if (input.payload !== undefined) headers.set("content-type", "application/json; charset=utf-8");
  if (input.bodyBytes !== undefined) headers.set("content-type", "application/octet-stream");
  for (const [name, value] of Object.entries(input.additionalHeaders ?? {})) {
    if (!["x-upload-id", "x-chunk-index", "x-upload-owner-token"].includes(name.toLowerCase()) || !value || value.length > 200 || /[\r\n]/.test(value)) throw unavailable();
    headers.set(name.toLowerCase(), value);
  }
  const target = new URL(input.path, input.service === "writer" ? config.writerBaseUrl : config.readerBaseUrl);
  target.search = rawQuery;
  return { config, target, init: { method: input.method, headers, body: body.byteLength ? body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer : undefined, cache: "no-store" } satisfies RequestInit };
}

export async function requestDjangoCustomerServiceJson<T>(principal: AppPrincipal, input: CustomerServiceRequest, options: DjangoCustomerServiceOptions = {}): Promise<DjangoCustomerServiceResult<T>> {
  const prepared = await prepare(principal, input, options);
  try {
    const { response, data } = await fetchBoundedJson({ url: prepared.target.toString(), init: prepared.init, timeoutMs: prepared.config.timeoutMs, maxBytes: prepared.config.maxResponseBytes, fetcher: options.fetchImpl, signal: options.signal });
    if (!isRecord(data) || !/^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "")) throw unavailable();
    if (!response.ok) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-customer-service-data-revision") ?? "";
    if (!/^\d+:[a-f0-9]{12}$/.test(revision)) throw unavailable();
    return { status: response.status, data: data as T, replayed: response.headers.get("x-teruisi-write-replay") === "1", revision };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}

async function readBoundedBytes(response: Response, maximum: number) {
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maximum) throw unavailable();
  const source = new Uint8Array(await response.arrayBuffer());
  if (source.byteLength > maximum) throw unavailable();
  return source;
}

export async function requestDjangoCustomerServiceBytes(principal: AppPrincipal, additionalHeaders: Record<string, string>, options: DjangoCustomerServiceOptions = {}) {
  const prepared = await prepare(principal, { method: "GET", path: CUSTOMER_SERVICE_UPLOAD_CHUNK_PATH, service: "writer", additionalHeaders }, options);
  try {
    const response = await (options.fetchImpl ?? fetch)(prepared.target.toString(), { ...prepared.init, redirect: "manual", signal: options.signal });
    if (!response.ok) throw upstreamError(response.status, await response.json().catch(() => null));
    if (response.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/octet-stream") throw unavailable();
    const bytes = await readBoundedBytes(response, 1024 * 1024);
    const sha256 = response.headers.get("x-chunk-sha256") ?? "";
    if (!/^[a-f0-9]{64}$/.test(sha256)) throw unavailable();
    return { bytes, sha256 };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    throw unavailable();
  }
}

export function createDjangoCustomerService(config?: DjangoCustomerServiceConfig) {
  return {
    requestJson: <T>(principal: AppPrincipal, input: CustomerServiceRequest, options: Omit<DjangoCustomerServiceOptions, "config"> = {}) => requestDjangoCustomerServiceJson<T>(principal, input, { ...options, config }),
    requestBytes: (principal: AppPrincipal, headers: Record<string, string>, options: Omit<DjangoCustomerServiceOptions, "config"> = {}) => requestDjangoCustomerServiceBytes(principal, headers, { ...options, config }),
  };
}
