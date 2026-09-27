import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createBiGatewayAuthHeaders,
  EMPTY_SHA256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";


export const BI_OVERVIEW_PATH = "/api/bi/overview";

const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

type RuntimeEnvironment = Record<string, string | undefined>;

export type DjangoBiServiceConfig = {
  readerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxResponseBytes?: number;
};

export type DjangoBiServiceOptions = {
  config?: DjangoBiServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};

export type DjangoBiServiceResult<T> = {
  status: number;
  data: T;
  revision: string;
};

function unavailable(message = "Django BI 服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    throw unavailable("Django BI 服务配置不完整。");
  }
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number) {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django BI 服务配置不完整。");
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

async function loadConfig(): Promise<DjangoBiServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_BI_READER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(environment.TERUISI_DJANGO_BI_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxResponseBytes: environmentInteger(
      environment.TERUISI_DJANGO_BI_MAX_RESPONSE_BYTES,
      DEFAULT_MAX_RESPONSE_BYTES,
      MAX_RESPONSE_BYTES,
    ),
  };
}

function normalizedConfig(config: DjangoBiServiceConfig) {
  if (new TextEncoder().encode(config.internalSecret).byteLength < 32) {
    throw unavailable("Django BI 服务配置不完整。");
  }
  let readerBaseUrl: URL;
  try {
    readerBaseUrl = new URL(config.readerBaseUrl);
  } catch {
    throw unavailable("Django BI 服务配置不完整。");
  }
  const hostname = readerBaseUrl.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1"
    || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(readerBaseUrl.protocol)
    || (readerBaseUrl.protocol === "http:" && !loopback)
    || readerBaseUrl.username || readerBaseUrl.password
    || readerBaseUrl.search || readerBaseUrl.hash
    || (readerBaseUrl.pathname !== "" && readerBaseUrl.pathname !== "/")) {
    throw unavailable("Django BI 服务配置不完整。");
  }
  return {
    readerBaseUrl,
    internalSecret: config.internalSecret,
    timeoutMs: boundedInteger(config.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxResponseBytes: boundedInteger(config.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export async function requestDjangoBiOverview<T>(
  principal: AppPrincipal,
  rawQuery: string,
  options: DjangoBiServiceOptions = {},
): Promise<DjangoBiServiceResult<T>> {
  if (rawQuery.startsWith("?") || rawQuery.length > 2_048 || /[\r\n]/.test(rawQuery)) {
    throw unavailable();
  }
  const config = normalizedConfig(options.config ?? await loadConfig());
  const headers = await createBiGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: "GET",
    path: BI_OVERVIEW_PATH,
    rawQuery,
    bodySha256: EMPTY_SHA256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId: (options.requestId ?? (() => crypto.randomUUID()))(),
  });
  const target = new URL(BI_OVERVIEW_PATH, config.readerBaseUrl);
  target.search = rawQuery;
  try {
    const { response, data } = await fetchBoundedJson({
      url: target.toString(),
      init: { method: "GET", headers, cache: "no-store" },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!isRecord(data)
      || !/^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "")) {
      throw unavailable();
    }
    if (!response.ok) {
      const message = typeof data.error === "string" ? data.error : "BI 请求未通过校验。";
      const code = typeof data.code === "string" ? data.code : "invalid_request";
      if (response.status === 400) throw new PublicApiError(400, "invalid_request", message);
      if (response.status === 403) throw new PublicApiError(403, "access_denied", message);
      if (response.status === 503 || response.status === 401) throw unavailable();
      throw unavailable(code === "service_unavailable" ? message : undefined);
    }
    if (response.status !== 200) throw unavailable();
    const revision = response.headers.get("x-bi-data-revision") ?? "";
    if (!/^\d+:\d+\|\d+:[a-f0-9]{12}$/.test(revision)
      || data.revision !== revision
      || data.contractVersion !== "bi-dashboard-read-model-v1"
      || data.projection !== "dashboard") {
      throw unavailable();
    }
    return { status: response.status, data: data as T, revision };
  } catch (error) {
    if (error instanceof PublicApiError) throw error;
    if (error instanceof BoundedFetchError) throw unavailable();
    throw unavailable();
  }
}
