import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createInventoryGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const INVENTORY_OVERVIEW_PATH = "/api/inventory/overview";
export const INVENTORY_AGE_ANALYSIS_PATH = "/api/inventory/age-analysis";
export const INVENTORY_INBOUND_MONITOR_PATH = "/api/inventory/inbound-monitor";
export const INVENTORY_GUANGDONG_PATH = "/api/inventory/guangdong-monitor";
export const INVENTORY_IMPORTS_PATH = "/api/inventory/imports";
export const INVENTORY_REPLENISHMENT_PATH = "/api/inventory/replenishment";
export const INVENTORY_REPLENISHMENT_IMPORT_PATH = "/api/inventory/replenishment/import";
export const INVENTORY_REPLENISHMENT_DINGTALK_PATH = "/api/inventory/replenishment/dingtalk";
export const INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH = "/api/inventory/replenishment/dingtalk/group";
export const INVENTORY_SETTINGS_PATH = "/api/inventory/settings";
export const INVENTORY_WAREHOUSE_MAPPINGS_PATH = "/api/inventory/warehouse-mappings";
export const INVENTORY_CONSUMER_QUERY_PATH = "/api/inventory/consumers/query";
export const INVENTORY_UPLOADS_PATH = "/api/inventory/uploads";
export const INVENTORY_UPLOAD_CHUNK_PATH = "/api/inventory/uploads/chunk";

const readerMethods = new Set([
  `GET ${INVENTORY_GUANGDONG_PATH}`,
  `GET ${INVENTORY_GUANGDONG_PATH}/watchlist`,
  `GET ${INVENTORY_GUANGDONG_PATH}/products`,
  `GET ${INVENTORY_GUANGDONG_PATH}/suppliers`,
  `GET ${INVENTORY_GUANGDONG_PATH}/export`,
  `POST ${INVENTORY_GUANGDONG_PATH}/preview`,
  `GET ${INVENTORY_OVERVIEW_PATH}`,
  `GET ${INVENTORY_AGE_ANALYSIS_PATH}`,
  `GET ${INVENTORY_INBOUND_MONITOR_PATH}`,
  `GET ${INVENTORY_IMPORTS_PATH}`,
  `GET ${INVENTORY_REPLENISHMENT_PATH}`,
  `GET ${INVENTORY_SETTINGS_PATH}`,
  `GET ${INVENTORY_WAREHOUSE_MAPPINGS_PATH}`,
  `POST ${INVENTORY_CONSUMER_QUERY_PATH}`,
]);
const writerMethods = new Set([
  `POST ${INVENTORY_GUANGDONG_PATH}/import`,
  `PATCH ${INVENTORY_GUANGDONG_PATH}/suppliers`,
  `PATCH ${INVENTORY_GUANGDONG_PATH}/items`,
  `POST ${INVENTORY_IMPORTS_PATH}`,
  `POST ${INVENTORY_UPLOADS_PATH}`,
  `GET ${INVENTORY_UPLOAD_CHUNK_PATH}`,
  `PUT ${INVENTORY_UPLOAD_CHUNK_PATH}`,
  `POST ${INVENTORY_REPLENISHMENT_PATH}`,
  `PATCH ${INVENTORY_REPLENISHMENT_PATH}`,
  `POST ${INVENTORY_REPLENISHMENT_IMPORT_PATH}`,
  `POST ${INVENTORY_REPLENISHMENT_DINGTALK_PATH}`,
  `POST ${INVENTORY_REPLENISHMENT_DINGTALK_GROUP_PATH}`,
  `PUT ${INVENTORY_SETTINGS_PATH}`,
  `PUT ${INVENTORY_WAREHOUSE_MAPPINGS_PATH}`,
]);

const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024;
const MAX_REQUEST_BYTES = 64 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoInventoryServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type DjangoInventoryServiceOptions = {
  config?: DjangoInventoryServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type InventoryServiceRequest = {
  method: "GET" | "POST" | "PUT" | "PATCH";
  path: string;
  service: "reader" | "writer";
  rawQuery?: string;
  payload?: Record<string, unknown>;
  bodyBytes?: Uint8Array;
  additionalHeaders?: Record<string, string>;
};

export type DjangoInventoryServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string;
};

export class DjangoInventoryServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
    readonly upstreamCode: string,
  ) {
    super(status, code, message);
    this.name = "DjangoInventoryServiceResponseError";
  }
}

function unavailable(message = "Django 库存服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    throw unavailable("Django 库存服务配置不完整。");
  }
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number) {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 库存服务配置不完整。");
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
  const processEnvironment = (globalThis as typeof globalThis & {
    process?: { env?: RuntimeEnvironment };
  }).process?.env;
  return { ...(processEnvironment ?? {}), ...workerEnvironment };
}

async function loadConfig(): Promise<DjangoInventoryServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_INVENTORY_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_INVENTORY_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(environment.TERUISI_DJANGO_INVENTORY_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: environmentInteger(environment.TERUISI_DJANGO_INVENTORY_MAX_REQUEST_BYTES, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: environmentInteger(environment.TERUISI_DJANGO_INVENTORY_MAX_RESPONSE_BYTES, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function normalizeBaseUrl(value: string) {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw unavailable("Django 库存服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 库存服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoInventoryServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) throw unavailable("Django 库存服务配置不完整。");
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) throw unavailable("Django 库存读写服务必须使用独立端点。");
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

function upstreamError(status: number, data: unknown): PublicApiError {
  if ([400, 401, 403, 404, 409, 413, 415, 422, 503].includes(status) && isRecord(data)) {
    const message = typeof data.message === "string" ? data.message
      : typeof data.error === "string" ? data.error : "库存请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 401 ? 503 : status === 415 ? 422 : status) as PublicApiError["status"];
    const code = (["invalid_request", "not_found", "conflict", "version_conflict", "payload_too_large", "service_unavailable", "access_denied"] as const)
      .find((candidate) => candidate === upstreamCode)
      ?? (status === 403 ? "access_denied" : status === 404 ? "not_found" : status === 409 ? "conflict" : status === 413 ? "payload_too_large" : status === 503 || status === 401 ? "service_unavailable" : "invalid_request");
    return new DjangoInventoryServiceResponseError(publicStatus, code, message, data, upstreamCode);
  }
  return unavailable();
}

async function prepare(
  principal: AppPrincipal,
  input: InventoryServiceRequest,
  options: DjangoInventoryServiceOptions,
) {
  const allowlist = input.service === "reader" ? readerMethods : writerMethods;
  if (!allowlist.has(`${input.method} ${input.path}`)) throw unavailable();
  if (input.payload !== undefined && input.bodyBytes !== undefined) throw unavailable();
  if (input.method === "GET" && (input.payload !== undefined || input.bodyBytes !== undefined)) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const body = input.payload !== undefined ? encoder.encode(JSON.stringify(input.payload)) : input.bodyBytes ?? new Uint8Array();
  if (body.byteLength > config.maxRequestBytes) throw new PublicApiError(413, "payload_too_large", "库存内部请求超过安全上限。");
  const rawQuery = input.rawQuery ?? "";
  if (rawQuery.startsWith("?") || rawQuery.length > 16_384 || /[\r\n]/.test(rawQuery)) throw unavailable();
  const headers = await createInventoryGatewayAuthHeaders({
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
    if (!["x-upload-id", "x-chunk-index", "x-upload-owner-token"].includes(name.toLowerCase())
      || !value || value.length > 200 || /[\r\n]/.test(value)) throw unavailable();
    headers.set(name.toLowerCase(), value);
  }
  const target = new URL(input.path, input.service === "writer" ? config.writerBaseUrl : config.readerBaseUrl);
  target.search = rawQuery;
  return {
    config,
    target,
    init: {
      method: input.method,
      headers,
      body: input.method === "GET" ? undefined : body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer,
      cache: "no-store",
    } satisfies RequestInit,
  };
}

export async function requestDjangoInventoryJson<T>(
  principal: AppPrincipal,
  input: InventoryServiceRequest,
  options: DjangoInventoryServiceOptions = {},
): Promise<DjangoInventoryServiceResult<T>> {
  const prepared = await prepare(principal, input, options);
  try {
    const { response, data } = await fetchBoundedJson({
      url: prepared.target.toString(), init: prepared.init,
      timeoutMs: prepared.config.timeoutMs,
      maxBytes: prepared.config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!isRecord(data) || !/^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "")) throw unavailable();
    if (!response.ok) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-inventory-data-revision") ?? "";
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

export async function requestDjangoInventoryBytes(
  principal: AppPrincipal,
  additionalHeaders: Record<string, string>,
  options: DjangoInventoryServiceOptions = {},
) {
  const prepared = await prepare(principal, {
    method: "GET", path: INVENTORY_UPLOAD_CHUNK_PATH, service: "writer", additionalHeaders,
  }, options);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("response_timeout")), prepared.config.timeoutMs);
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
    if (!response.ok) throw upstreamError(response.status, await response.json().catch(() => null));
    if (response.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/octet-stream") throw unavailable();
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

export function createDjangoInventoryService(config?: DjangoInventoryServiceConfig) {
  return {
    requestJson: <T>(principal: AppPrincipal, input: InventoryServiceRequest, options: Omit<DjangoInventoryServiceOptions, "config"> = {}) => requestDjangoInventoryJson<T>(principal, input, { ...options, config }),
    requestBytes: (principal: AppPrincipal, additionalHeaders: Record<string, string>, options: Omit<DjangoInventoryServiceOptions, "config"> = {}) => requestDjangoInventoryBytes(principal, additionalHeaders, { ...options, config }),
  };
}
