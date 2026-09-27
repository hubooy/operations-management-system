import type { AppPrincipal } from "@/lib/auth/authorization";
import { BoundedFetchError, fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import {
  createWorkflowGatewayAuthHeaders,
  EMPTY_SHA256,
  salesGatewayBodySha256,
} from "@/lib/django/sales-gateway";
import { PublicApiError } from "@/lib/http/api-error";

export const WORKFLOW_LAUNCH_PROJECTS_PATH = "/api/workflow/launch-projects";
export const WORKFLOW_CONSUMER_QUERY_PATH = "/api/workflow/consumers/query";
export const WORKFLOW_NEW_PRODUCT_LINES_PATH = "/api/workflow/new-product-lines";
export const WORKFLOW_NEW_PRODUCT_LINE_LEARN_PATH = "/api/workflow/new-product-lines/learn";
export const WORKFLOW_NEW_PRODUCT_WEEKLY_FOLLOWUP_PATH = "/api/workflow/new-product-weekly-followup";
export const WORKFLOW_NEW_PRODUCT_WEEKLY_REPORT_CONFIG_PATH = "/api/workflow/new-product-weekly-report-config";
export const WORKFLOW_TASKS_PATH = "/api/workflow/tasks";
export const WORKFLOW_IMPORT_CHAIN_STATUS_PATH = "/api/workflow/import-chain-status";
export const WORKFLOW_TEMPLATES_PATH = "/api/workflow/templates";
export const WORKFLOW_OPERATION_RECORDS_PATH = "/api/workflow/operations-records";
export const WORKFLOW_ATTACHMENT_CLEANUP_PATH = "/api/workflow/attachment-cleanup";
export const WORKFLOW_INVENTORY_WORK_ITEMS_PATH = "/api/workflow/inventory-work-items";

const encoder = new TextEncoder();
const DEFAULT_TIMEOUT_MS = 15_000;
const MAX_TIMEOUT_MS = 60_000;
const DEFAULT_MAX_REQUEST_BYTES = 512 * 1024;
const MAX_REQUEST_BYTES = 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const PROJECT_PATH_RE = /^\/api\/workflow\/launch-projects\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const STAGE_PATH_RE = /^\/api\/workflow\/launch-projects\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\/stages\/(?:modeling|pricing|image|video|listing|stocking|review)$/i;
const PRODUCT_LINE_PATH_RE = /^\/api\/workflow\/new-product-lines\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PRODUCT_LINE_IMAGE_PATH_RE = /^\/api\/workflow\/new-product-lines\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\/image$/i;
const RESOURCE_SEGMENT = "[A-Za-z0-9._:%-]{1,384}";
const TASK_SUBRESOURCE_PATH_RE = new RegExp(`^/api/workflow/tasks/${RESOURCE_SEGMENT}/(?:collaboration|comments|activity|reminders|links|attachments)$`);
const TASK_ATTACHMENT_PATH_RE = new RegExp(`^/api/workflow/tasks/${RESOURCE_SEGMENT}/attachments/${RESOURCE_SEGMENT}$`);
const OPERATION_RECORD_PATH_RE = new RegExp(`^/api/workflow/operations-records/${RESOURCE_SEGMENT}$`);
const OPERATION_RECORD_ACTIVITY_PATH_RE = new RegExp(`^/api/workflow/operations-records/${RESOURCE_SEGMENT}/activity$`);

type RuntimeEnvironment = Record<string, string | undefined>;

export type WorkflowBackendMode = "django";
export type DjangoWorkflowServiceConfig = {
  readerBaseUrl: string;
  writerBaseUrl: string;
  internalSecret: string;
  timeoutMs?: number;
  maxRequestBytes?: number;
  maxResponseBytes?: number;
};
export type DjangoWorkflowServiceOptions = {
  config?: DjangoWorkflowServiceConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  signal?: AbortSignal;
};
export type WorkflowServiceRequest = {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  path: string;
  service: "reader" | "writer";
  rawQuery?: string;
  payload?: Record<string, unknown>;
};
export type DjangoWorkflowServiceResult<T> = {
  status: number;
  data: T;
  replayed: boolean;
  revision: string;
};

export class DjangoWorkflowServiceResponseError extends PublicApiError {
  constructor(
    status: PublicApiError["status"],
    code: PublicApiError["code"],
    message: string,
    readonly payload: Record<string, unknown>,
    readonly upstreamCode: string,
  ) {
    super(status, code, message);
    this.name = "DjangoWorkflowServiceResponseError";
  }
}

function unavailable(message = "Django 运营事务服务暂时不可用，请稍后重试。"): PublicApiError {
  return new PublicApiError(503, "service_unavailable", message);
}

function boundedInteger(value: unknown, fallback: number, maximum: number) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || Number(value) < 1 || Number(value) > maximum) {
    throw unavailable("Django 运营事务服务配置不完整。");
  }
  return Number(value);
}

function environmentInteger(value: string | undefined, fallback: number, maximum: number) {
  if (value === undefined || value.trim() === "") return fallback;
  if (!/^[1-9]\d*$/.test(value.trim())) throw unavailable("Django 运营事务服务配置不完整。");
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

export function workflowBackendModeFromEnvironment(environment: RuntimeEnvironment): WorkflowBackendMode {
  const value = (environment.TERUISI_DJANGO_WORKFLOW_MODE ?? "").trim().toLowerCase();
  if (value === "django") return value;
  throw unavailable("新品上新已终态切换到 Django，路由模式必须显式配置为 django。");
}

export async function getWorkflowBackendMode(): Promise<WorkflowBackendMode> {
  return workflowBackendModeFromEnvironment(await runtimeEnvironment());
}

async function loadConfig(): Promise<DjangoWorkflowServiceConfig> {
  const environment = await runtimeEnvironment();
  return {
    readerBaseUrl: environment.TERUISI_DJANGO_WORKFLOW_READER_BASE_URL ?? "",
    writerBaseUrl: environment.TERUISI_DJANGO_WORKFLOW_WRITER_BASE_URL ?? "",
    internalSecret: environment.TERUISI_DJANGO_INTERNAL_SECRET ?? "",
    timeoutMs: environmentInteger(environment.TERUISI_DJANGO_WORKFLOW_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxRequestBytes: environmentInteger(environment.TERUISI_DJANGO_WORKFLOW_MAX_REQUEST_BYTES, DEFAULT_MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
    maxResponseBytes: environmentInteger(environment.TERUISI_DJANGO_WORKFLOW_MAX_RESPONSE_BYTES, DEFAULT_MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES),
  };
}

function normalizeBaseUrl(value: string) {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw unavailable("Django 运营事务服务配置不完整。");
  }
  const hostname = parsed.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname === "[::1]";
  if (!/^https?:$/.test(parsed.protocol) || (parsed.protocol === "http:" && !loopback)
    || parsed.username || parsed.password || parsed.search || parsed.hash
    || (parsed.pathname !== "" && parsed.pathname !== "/")) {
    throw unavailable("Django 运营事务服务配置不完整。");
  }
  return parsed;
}

function normalizedConfig(config: DjangoWorkflowServiceConfig) {
  if (encoder.encode(config.internalSecret).byteLength < 32) throw unavailable("Django 运营事务服务配置不完整。");
  const readerBaseUrl = normalizeBaseUrl(config.readerBaseUrl);
  const writerBaseUrl = normalizeBaseUrl(config.writerBaseUrl);
  if (readerBaseUrl.origin === writerBaseUrl.origin) throw unavailable("Django 运营事务读写服务必须使用独立端点。");
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
    const message = typeof data.error === "string" ? data.error : "运营事务请求未通过校验。";
    const upstreamCode = typeof data.code === "string" ? data.code : "invalid_request";
    const publicStatus = (status === 401 ? 503 : status === 415 ? 422 : status) as PublicApiError["status"];
    const code = (["invalid_request", "not_found", "conflict", "version_conflict", "payload_too_large", "service_unavailable", "access_denied"] as const)
      .find((candidate) => candidate === upstreamCode)
      ?? (status === 403 ? "access_denied" : status === 404 ? "not_found" : status === 409 ? "conflict" : status === 413 ? "payload_too_large" : status === 503 || status === 401 ? "service_unavailable" : "invalid_request");
    return new DjangoWorkflowServiceResponseError(publicStatus, code, message, data, upstreamCode);
  }
  return unavailable();
}

function validateRequest(input: WorkflowServiceRequest) {
  const collection = input.path === WORKFLOW_LAUNCH_PROJECTS_PATH;
  const consumer = input.path === WORKFLOW_CONSUMER_QUERY_PATH;
  const project = PROJECT_PATH_RE.test(input.path);
  const stage = STAGE_PATH_RE.test(input.path);
  const productLines = input.path === WORKFLOW_NEW_PRODUCT_LINES_PATH;
  const productLine = PRODUCT_LINE_PATH_RE.test(input.path);
  const productLineImage = PRODUCT_LINE_IMAGE_PATH_RE.test(input.path);
  const productLineLearning = input.path === WORKFLOW_NEW_PRODUCT_LINE_LEARN_PATH;
  const weeklyFollowup = input.path === WORKFLOW_NEW_PRODUCT_WEEKLY_FOLLOWUP_PATH;
  const weeklyReportConfig = input.path === WORKFLOW_NEW_PRODUCT_WEEKLY_REPORT_CONFIG_PATH;
  const tasks = input.path === WORKFLOW_TASKS_PATH;
  const taskSubresource = TASK_SUBRESOURCE_PATH_RE.test(input.path);
  const taskAttachment = TASK_ATTACHMENT_PATH_RE.test(input.path);
  const taskWritableCollection = taskSubresource && /\/(?:comments|reminders|links|attachments)$/.test(input.path);
  const taskDeletableCollection = taskSubresource && /\/(?:reminders|links)$/.test(input.path);
  const templates = input.path === WORKFLOW_TEMPLATES_PATH;
  const records = input.path === WORKFLOW_OPERATION_RECORDS_PATH;
  const record = OPERATION_RECORD_PATH_RE.test(input.path);
  const recordActivity = OPERATION_RECORD_ACTIVITY_PATH_RE.test(input.path);
  const attachmentCleanup = input.path === WORKFLOW_ATTACHMENT_CLEANUP_PATH;
  const inventoryWorkItems = input.path === WORKFLOW_INVENTORY_WORK_ITEMS_PATH;
  const allowed = input.service === "reader"
    ? (input.method === "GET" && (collection || project || productLines || productLineImage || productLineLearning || weeklyFollowup || weeklyReportConfig
      || input.path === WORKFLOW_IMPORT_CHAIN_STATUS_PATH || tasks || taskSubresource || taskAttachment || templates || records || record || recordActivity))
      || (input.method === "POST" && consumer)
    : (input.method === "GET" && attachmentCleanup)
      || (input.method === "POST" && (collection || productLines || productLineLearning || tasks || taskWritableCollection
        || templates || records || attachmentCleanup || inventoryWorkItems))
      || (input.method === "PATCH" && (project || stage || productLine || weeklyReportConfig || tasks || templates || record))
      || (input.method === "DELETE" && (project || tasks || taskDeletableCollection || taskAttachment || templates || record));
  if (!allowed) throw unavailable();
  if ((input.method === "POST" || input.method === "PATCH") !== (input.payload !== undefined)) throw unavailable();
  if ((input.method === "GET" || input.method === "DELETE") && input.payload !== undefined) throw unavailable();
}

export async function requestDjangoWorkflowJson<T>(
  principal: AppPrincipal,
  input: WorkflowServiceRequest,
  options: DjangoWorkflowServiceOptions = {},
): Promise<DjangoWorkflowServiceResult<T>> {
  validateRequest(input);
  const config = normalizedConfig(options.config ?? await loadConfig());
  const rawQuery = input.rawQuery ?? "";
  if (rawQuery.startsWith("?") || rawQuery.length > 16_384 || /[\r\n]/.test(rawQuery)) throw unavailable();
  const body = input.payload === undefined ? new Uint8Array() : encoder.encode(JSON.stringify(input.payload));
  if (body.byteLength > config.maxRequestBytes) throw new PublicApiError(413, "payload_too_large", "运营事务内部请求超过安全上限。");
  const headers = await createWorkflowGatewayAuthHeaders({
    secret: config.internalSecret,
    principal,
    method: input.method,
    path: input.path,
    rawQuery,
    bodySha256: body.byteLength ? await salesGatewayBodySha256(body) : EMPTY_SHA256,
    timestamp: Math.floor((options.now ?? Date.now)() / 1_000),
    requestId: (options.requestId ?? (() => crypto.randomUUID()))(),
  });
  if (body.byteLength) headers.set("content-type", "application/json; charset=utf-8");
  const baseUrl = input.service === "writer" ? config.writerBaseUrl : config.readerBaseUrl;
  const target = new URL(input.path, baseUrl);
  target.search = rawQuery;
  try {
    const { response, data } = await fetchBoundedJson({
      url: target.toString(),
      init: {
        method: input.method,
        headers,
        body: body.byteLength ? body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer : undefined,
        cache: "no-store",
      },
      timeoutMs: config.timeoutMs,
      maxBytes: config.maxResponseBytes,
      fetcher: options.fetchImpl,
      signal: options.signal,
    });
    if (!isRecord(data) || !/^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "")) throw unavailable();
    if (!response.ok) throw upstreamError(response.status, data);
    const revision = response.headers.get("x-workflow-data-revision") ?? "";
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

export function createDjangoWorkflowService(config?: DjangoWorkflowServiceConfig) {
  return {
    requestJson: <T>(
      principal: AppPrincipal,
      input: WorkflowServiceRequest,
      options: Omit<DjangoWorkflowServiceOptions, "config"> = {},
    ) => requestDjangoWorkflowJson<T>(principal, input, { ...options, config }),
  };
}
