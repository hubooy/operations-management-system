import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createAccessControlGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";

export const ACCESS_CONTROL_RESOLVE_PATH = "/api/access-control/principal/resolve";
export const ACCESS_CONTROL_BACKGROUND_PATH = "/api/access-control/principal/authorize-background";
export const ACCESS_CONTROL_ROLES_PATH = "/api/access-control/roles";
export const ACCESS_CONTROL_USERS_PATH = "/api/access-control/users";
export const ACCESS_CONTROL_AUDITS_PATH = "/api/access-control/audits";

const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 8_000;
const MAX_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_REQUEST_BYTES = 256 * 1024;
const MAX_REQUEST_BYTES = 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoAccessControlServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};

export type AccessControlServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string;
};

export class AccessControlServiceError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "AccessControlServiceError";
  }
}

function unavailable(message = "Django 权限服务暂时不可用，请稍后重试。") {
  return new AccessControlServiceError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) throw unavailable();
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 权限服务配置不完整。");
  return boundedInteger(Number(value), fallback, maximum);
}

async function runtimeEnvironment(): Promise<RuntimeEnvironment> {
  let workerEnvironment: RuntimeEnvironment = {};
  try {
    const cloudflare = await import("cloudflare:workers");
    workerEnvironment = cloudflare.env as unknown as RuntimeEnvironment;
  } catch {
    // Tests and command-line tools may use process.env or inject config.
  }
  const processEnvironment = globalThis.process?.env as RuntimeEnvironment | undefined;
  return { ...(processEnvironment ?? {}), ...workerEnvironment };
}

async function loadConfig(): Promise<DjangoAccessControlServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_ACCESS_CONTROL_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_ACCESS_CONTROL_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(environment.TERUISI_DJANGO_ACCESS_CONTROL_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: environmentInteger(environment.TERUISI_DJANGO_ACCESS_CONTROL_MAX_REQUEST_BYTES, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: environmentInteger(environment.TERUISI_DJANGO_ACCESS_CONTROL_MAX_RESPONSE_BYTES, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function normalizeBaseUrl(value: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw unavailable("Django 权限服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 权限服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoAccessControlServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) throw unavailable("Django 权限服务配置不完整。");
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) throw unavailable("Django 权限读写服务必须使用独立端点。");
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

function allowedRequest(input: { method: string; path: string; service: "reader" | "writer" }): boolean {
  if (input.service === "reader") {
    return input.method === "GET" && [ACCESS_CONTROL_ROLES_PATH, ACCESS_CONTROL_USERS_PATH, ACCESS_CONTROL_AUDITS_PATH].includes(input.path)
      || input.method === "POST" && [ACCESS_CONTROL_RESOLVE_PATH, ACCESS_CONTROL_BACKGROUND_PATH].includes(input.path);
  }
  return (input.method === "POST" || input.method === "PUT")
    && input.path === ACCESS_CONTROL_USERS_PATH;
}

export async function requestDjangoAccessControl<T>(
  principal: AppPrincipal,
  input: {
    method: "GET" | "POST" | "PUT";
    path: string;
    query?: URLSearchParams;
    payload?: Record<string, unknown>;
    service: "reader" | "writer";
  },
  options: {
    config?: DjangoAccessControlServiceConfig;
    fetchImpl?: typeof fetch;
    now?: () => number;
    requestId?: () => string;
    signal?: AbortSignal;
  } = {},
): Promise<AccessControlServiceResult<T>> {
  if (!allowedRequest(input)) throw unavailable();
  if (input.method === "GET" && input.payload !== undefined) throw unavailable();
  if (input.method !== "GET" && input.payload === undefined) throw unavailable();
  const config = normalizedConfig(options.config ?? await loadConfig());
  const rawQuery = input.query?.toString() ?? "";
  const body = input.payload === undefined ? undefined : encoder.encode(JSON.stringify(input.payload));
  if (body && body.byteLength > config.maxRequestBytes) {
    throw new AccessControlServiceError(413, "payload_too_large", "权限请求超过内部传输上限。");
  }
  const bodySha256 = body ? await salesGatewayBodySha256(body) : EMPTY_SHA256;
  const headers = await createAccessControlGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: input.method,
    path: input.path,
    rawQuery,
    bodySha256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId: (options.requestId ?? (() => crypto.randomUUID()))(),
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
    if (!isRecord(data) || !/^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "")) {
      throw unavailable();
    }
    if (response.status < 200 || response.status >= 300) {
      const message = typeof data.error === "string" ? data.error : "权限请求未通过校验。";
      const code = typeof data.code === "string" ? data.code : "invalid_request";
      if (![400, 403, 404, 409, 413, 415, 503].includes(response.status)) throw unavailable();
      throw new AccessControlServiceError(response.status, code, message);
    }
    const revision = response.headers.get("x-access-control-revision") ?? "";
    if (!/^\d+:[a-f0-9]{12}$/.test(revision)) throw unavailable();
    return { status: response.status, data: data as T, replayed: response.headers.get("x-teruisi-write-replay") === "1", revision };
  } catch (error) {
    if (error instanceof AccessControlServiceError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}

export function createDjangoAccessControlService(config?: DjangoAccessControlServiceConfig) {
  return {
    request: <T>(
      principal: AppPrincipal,
      input: Parameters<typeof requestDjangoAccessControl<T>>[1],
      options: Omit<Parameters<typeof requestDjangoAccessControl<T>>[2], "config"> = {},
    ) => requestDjangoAccessControl<T>(principal, input, { ...options, config }),
  };
}
