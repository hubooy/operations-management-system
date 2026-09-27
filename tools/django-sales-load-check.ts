import { createHash, createHmac, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const DEFAULT_TIMEOUT_MS = 10_000;
const MAX_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_CONCURRENCY = 32;
const MAX_ROUNDS = 100;
const MAX_REQUESTS = 1_000;
const MAX_CATEGORY_LENGTH = 100;

export const loadCheckViews = ["full", "dashboard", "category", "category-detail"] as const;
export type LoadCheckView = (typeof loadCheckViews)[number];

export type LoadCheckThresholds = {
  p95Ms?: number;
  p99Ms?: number;
  maxMs?: number;
};

export type LoadCheckConfig = {
  baseUrl: URL;
  startDate: string;
  endDate: string;
  concurrency: number;
  rounds: number;
  views: LoadCheckView[];
  timeoutMs: number;
  maxResponseBytes: number;
  category?: string;
  thresholds?: LoadCheckThresholds;
};

export type LoadCheckSample = {
  view: LoadCheckView;
  round: number;
  worker: number;
  durationMs: number;
  overviewCache: string | null;
  cacheControl: string | null;
  revision: string;
  jsonSha256: string;
  responseBytes: number;
};

export type LoadCheckStatistics = {
  requests: number;
  p50Ms: number;
  p95Ms: number;
  p99Ms: number;
  maxMs: number;
};

export type LoadCheckThresholdViolation = {
  scope: "overall" | LoadCheckView;
  metric: keyof LoadCheckThresholds;
  observedMs: number;
  thresholdMs: number;
};

export type LoadCheckReport = {
  revision: string;
  samples: LoadCheckSample[];
  overall: LoadCheckStatistics;
  byView: Record<LoadCheckView, LoadCheckStatistics | null>;
  thresholds: LoadCheckThresholds | null;
  thresholdViolations: LoadCheckThresholdViolation[];
  passed: boolean;
};

type LoadCheckDependencies = {
  environment?: Record<string, string | undefined>;
  fetchImpl?: typeof fetch;
  now?: () => number;
  requestId?: () => string;
  onSample?: (sample: LoadCheckSample) => void;
};

const principal = {
  email: "django-sales-load-check@internal.invalid",
  displayName: "Django Sales Load Check",
  role: "admin",
  scope: null,
} as const;

export class LoadCheckError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LoadCheckError";
  }
}

export class LoadCheckThresholdError extends LoadCheckError {
  readonly report: LoadCheckReport;

  constructor(report: LoadCheckReport) {
    const details = report.thresholdViolations.map((violation) => (
      `${violation.scope}.${violation.metric}=${violation.observedMs}ms>${violation.thresholdMs}ms`
    )).join(", ");
    super(`latency thresholds exceeded: ${details}`);
    this.name = "LoadCheckThresholdError";
    this.report = report;
  }
}

function usage(): string {
  return [
    "Usage:",
    "  node --import tsx tools/django-sales-load-check.ts \\",
    "    --base-url <http-loopback-or-https-origin> \\",
    "    --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> \\",
    "    --concurrency <1-32> --rounds <1-100> \\",
    "    [--view full,dashboard,category,category-detail] \\",
    "    [--category <exact-category-for-category-detail>] \\",
    "    [--timeout-ms <1-30000>] [--max-response-bytes <1-8388608>] \\",
    "    [--p95-ms <0.01-30000>] [--p99-ms <0.01-30000>] [--max-ms <0.01-30000>]",
    "",
    "TERUISI_DJANGO_INTERNAL_SECRET must be provided through the environment.",
  ].join("\n");
}

function exactPositiveMilliseconds(raw: string, label: string): number {
  if (!/^(?:0\.\d{1,2}|[1-9]\d*(?:\.\d{1,2})?)$/.test(raw)) {
    throw new LoadCheckError(`${label} must be a positive millisecond value with at most two decimals`);
  }
  const value = Number(raw);
  if (!Number.isFinite(value) || value > MAX_TIMEOUT_MS) {
    throw new LoadCheckError(`${label} must be between 0.01 and ${MAX_TIMEOUT_MS}`);
  }
  return value;
}

function exactPositiveInteger(raw: string, label: string, maximum: number): number {
  if (!/^[1-9]\d*$/.test(raw)) throw new LoadCheckError(`${label} must be a positive integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value > maximum) {
    throw new LoadCheckError(`${label} must be between 1 and ${maximum}`);
  }
  return value;
}

function validIsoDate(raw: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return false;
  const parsed = new Date(`${raw}T00:00:00.000Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === raw;
}

export function validateLoadCheckBaseUrl(raw: string): URL {
  let value: URL;
  try {
    value = new URL(raw);
  } catch {
    throw new LoadCheckError("--base-url must be a valid URL");
  }
  if (value.username || value.password || value.search || value.hash || (value.pathname !== "/" && value.pathname !== "")) {
    throw new LoadCheckError("--base-url must be an origin without credentials, path, query, or fragment");
  }
  const hostname = value.hostname.toLowerCase();
  const loopback = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname === "[::1]";
  if (value.protocol !== "https:" && !(value.protocol === "http:" && loopback)) {
    throw new LoadCheckError("--base-url must use HTTPS, except for exact localhost/127.0.0.1/[::1] loopback URLs");
  }
  return value;
}

function parseViews(raw: string): LoadCheckView[] {
  const values = [...new Set(raw.split(",").map((value) => value.trim()).filter(Boolean))];
  if (values.length === 0 || values.some((value) => !loadCheckViews.includes(value as LoadCheckView))) {
    throw new LoadCheckError(`--view must contain only ${loadCheckViews.join(",")}`);
  }
  return values as LoadCheckView[];
}

function validateCategory(views: LoadCheckView[], raw: string | undefined): string | undefined {
  const detailSelected = views.includes("category-detail");
  if (raw === undefined) {
    if (detailSelected) throw new LoadCheckError("--category is required when --view includes category-detail");
    return undefined;
  }
  if (!detailSelected) throw new LoadCheckError("--category is only allowed when --view includes category-detail");
  if (raw !== raw.trim() || raw.length === 0 || raw.length > MAX_CATEGORY_LENGTH || /[\u0000-\u001f\u007f]/.test(raw)) {
    throw new LoadCheckError(`--category must be an exact non-empty value of at most ${MAX_CATEGORY_LENGTH} characters without surrounding whitespace or control characters`);
  }
  return raw;
}

function validateThresholds(thresholds: LoadCheckThresholds | undefined, timeoutMs: number): LoadCheckThresholds | undefined {
  if (thresholds === undefined) return undefined;
  const normalized: LoadCheckThresholds = {};
  for (const [metric, label] of [
    ["p95Ms", "--p95-ms"],
    ["p99Ms", "--p99-ms"],
    ["maxMs", "--max-ms"],
  ] as const) {
    const value = thresholds[metric];
    if (value === undefined) continue;
    if (!Number.isFinite(value) || value <= 0 || value > MAX_TIMEOUT_MS || Number(value.toFixed(2)) !== value) {
      throw new LoadCheckError(`${label} must be between 0.01 and ${MAX_TIMEOUT_MS} with at most two decimals`);
    }
    if (value > timeoutMs) throw new LoadCheckError(`${label} must not exceed --timeout-ms`);
    normalized[metric] = value;
  }
  if (normalized.p95Ms !== undefined && normalized.p99Ms !== undefined && normalized.p95Ms > normalized.p99Ms) {
    throw new LoadCheckError("--p95-ms must not exceed --p99-ms");
  }
  if (normalized.p95Ms !== undefined && normalized.maxMs !== undefined && normalized.p95Ms > normalized.maxMs) {
    throw new LoadCheckError("--p95-ms must not exceed --max-ms");
  }
  if (normalized.p99Ms !== undefined && normalized.maxMs !== undefined && normalized.p99Ms > normalized.maxMs) {
    throw new LoadCheckError("--p99-ms must not exceed --max-ms");
  }
  return Object.keys(normalized).length > 0 ? normalized : undefined;
}

function validateRuntimeConfig(config: LoadCheckConfig): void {
  validateLoadCheckBaseUrl(config.baseUrl.href);
  if (!validIsoDate(config.startDate) || !validIsoDate(config.endDate) || config.startDate > config.endDate) {
    throw new LoadCheckError("startDate and endDate must be a valid ordered YYYY-MM-DD range");
  }
  if (!Number.isInteger(config.concurrency) || config.concurrency < 1 || config.concurrency > MAX_CONCURRENCY) {
    throw new LoadCheckError(`concurrency must be between 1 and ${MAX_CONCURRENCY}`);
  }
  if (!Number.isInteger(config.rounds) || config.rounds < 1 || config.rounds > MAX_ROUNDS) {
    throw new LoadCheckError(`rounds must be between 1 and ${MAX_ROUNDS}`);
  }
  if (config.views.length === 0 || new Set(config.views).size !== config.views.length
    || config.views.some((view) => !loadCheckViews.includes(view))) {
    throw new LoadCheckError(`views must be unique and contain only ${loadCheckViews.join(",")}`);
  }
  if (config.concurrency * config.rounds * config.views.length > MAX_REQUESTS) {
    throw new LoadCheckError(`planned request count exceeds ${MAX_REQUESTS}`);
  }
  if (!Number.isInteger(config.timeoutMs) || config.timeoutMs < 1 || config.timeoutMs > MAX_TIMEOUT_MS) {
    throw new LoadCheckError(`timeoutMs must be between 1 and ${MAX_TIMEOUT_MS}`);
  }
  if (!Number.isInteger(config.maxResponseBytes) || config.maxResponseBytes < 1 || config.maxResponseBytes > MAX_RESPONSE_BYTES) {
    throw new LoadCheckError(`maxResponseBytes must be between 1 and ${MAX_RESPONSE_BYTES}`);
  }
  validateCategory(config.views, config.category);
  validateThresholds(config.thresholds, config.timeoutMs);
}

export function parseLoadCheckArgs(argv: string[]): LoadCheckConfig {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--help" || flag === "-h") throw new LoadCheckError(usage());
    if (!flag?.startsWith("--")) throw new LoadCheckError(`unexpected argument: ${flag ?? ""}`);
    const allowed = new Set([
      "--base-url", "--start-date", "--end-date", "--concurrency", "--rounds", "--view",
      "--timeout-ms", "--max-response-bytes", "--category", "--p95-ms", "--p99-ms", "--max-ms",
    ]);
    if (!allowed.has(flag)) throw new LoadCheckError(`unknown option: ${flag}`);
    if (values.has(flag)) throw new LoadCheckError(`option cannot be repeated: ${flag}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new LoadCheckError(`${flag} requires a value`);
    values.set(flag, value);
    index += 1;
  }

  const required = (flag: string) => {
    const value = values.get(flag);
    if (!value) throw new LoadCheckError(`${flag} is required`);
    return value;
  };
  const startDate = required("--start-date");
  const endDate = required("--end-date");
  if (!validIsoDate(startDate) || !validIsoDate(endDate)) {
    throw new LoadCheckError("--start-date and --end-date must be valid YYYY-MM-DD dates");
  }
  if (startDate > endDate) throw new LoadCheckError("--start-date must not be after --end-date");

  const concurrency = exactPositiveInteger(required("--concurrency"), "--concurrency", MAX_CONCURRENCY);
  const rounds = exactPositiveInteger(required("--rounds"), "--rounds", MAX_ROUNDS);
  const views = parseViews(values.get("--view") ?? "full");
  if (concurrency * rounds * views.length > MAX_REQUESTS) {
    throw new LoadCheckError(`planned request count exceeds ${MAX_REQUESTS}`);
  }

  const timeoutMs = values.has("--timeout-ms")
    ? exactPositiveInteger(required("--timeout-ms"), "--timeout-ms", MAX_TIMEOUT_MS)
    : DEFAULT_TIMEOUT_MS;
  const thresholds = validateThresholds({
    p95Ms: values.has("--p95-ms") ? exactPositiveMilliseconds(required("--p95-ms"), "--p95-ms") : undefined,
    p99Ms: values.has("--p99-ms") ? exactPositiveMilliseconds(required("--p99-ms"), "--p99-ms") : undefined,
    maxMs: values.has("--max-ms") ? exactPositiveMilliseconds(required("--max-ms"), "--max-ms") : undefined,
  }, timeoutMs);

  return {
    baseUrl: validateLoadCheckBaseUrl(required("--base-url")),
    startDate,
    endDate,
    concurrency,
    rounds,
    views,
    timeoutMs,
    maxResponseBytes: values.has("--max-response-bytes")
      ? exactPositiveInteger(required("--max-response-bytes"), "--max-response-bytes", MAX_RESPONSE_BYTES)
      : DEFAULT_MAX_RESPONSE_BYTES,
    category: validateCategory(views, values.get("--category")),
    thresholds,
  };
}

function requireSecret(environment: Record<string, string | undefined>): string {
  const secret = environment.TERUISI_DJANGO_INTERNAL_SECRET;
  if (!secret || Buffer.byteLength(secret, "utf8") < 32) {
    throw new LoadCheckError("TERUISI_DJANGO_INTERNAL_SECRET must contain at least 32 UTF-8 bytes");
  }
  return secret;
}

function targetFor(config: LoadCheckConfig, view: LoadCheckView): URL {
  const path = view === "category"
    ? "/api/sales/category-analysis"
    : view === "category-detail"
      ? "/api/sales/category-analysis/detail"
      : "/api/sales/summary";
  const target = new URL(path, config.baseUrl);
  if (view === "full" || view === "dashboard") target.searchParams.set("range", "custom");
  target.searchParams.set("startDate", config.startDate);
  target.searchParams.set("endDate", config.endDate);
  if (view === "dashboard") target.searchParams.set("view", "dashboard");
  if (view === "category") {
    target.searchParams.set("page", "1");
    target.searchParams.set("pageSize", "100");
  }
  if (view === "category-detail") target.searchParams.set("category", config.category!);
  return target;
}

function signedHeaders(secret: string, target: URL, timestamp: number, requestId: string): Headers {
  if (!Number.isSafeInteger(timestamp) || timestamp <= 0 || !/^[A-Za-z0-9._:-]{1,128}$/.test(requestId)) {
    throw new LoadCheckError("generated request identity is invalid");
  }
  const principalBase64 = Buffer.from(JSON.stringify(principal), "utf8").toString("base64url");
  const canonical = [
    "v1",
    String(timestamp),
    requestId,
    "GET",
    target.pathname,
    target.search.slice(1),
    EMPTY_BODY_SHA256,
    principalBase64,
  ].join("\n");
  const signature = createHmac("sha256", secret).update(canonical, "utf8").digest("hex");
  return new Headers({
    accept: "application/json",
    "x-teruisi-content-sha256": EMPTY_BODY_SHA256,
    "x-teruisi-principal": principalBase64,
    "x-teruisi-request-id": requestId,
    "x-teruisi-signature": `v1=${signature}`,
    "x-teruisi-timestamp": String(timestamp),
  });
}

async function readBoundedJson(response: Response, maximum: number): Promise<{ value: unknown; bytes: number }> {
  const contentType = response.headers.get("content-type");
  if (!contentType || !/^(?:application\/json|application\/[a-z0-9.+-]+\+json)(?:\s*;|$)/i.test(contentType)) {
    await response.body?.cancel();
    throw new LoadCheckError("response content-type is not JSON");
  }
  const declaredRaw = response.headers.get("content-length");
  if (declaredRaw !== null) {
    if (!/^\d+$/.test(declaredRaw)) {
      await response.body?.cancel();
      throw new LoadCheckError("response content-length is invalid");
    }
    if (Number(declaredRaw) > maximum) {
      await response.body?.cancel();
      throw new LoadCheckError(`response exceeds ${maximum} bytes`);
    }
  }
  if (!response.body) throw new LoadCheckError("response body is empty");

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    total += result.value.byteLength;
    if (total > maximum) {
      await reader.cancel();
      throw new LoadCheckError(`response exceeds ${maximum} bytes`);
    }
    chunks.push(result.value);
  }
  if (total === 0) throw new LoadCheckError("response body is empty");
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return { value: JSON.parse(text) as unknown, bytes: total };
  } catch {
    throw new LoadCheckError("response body is not valid UTF-8 JSON");
  }
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validateCategoryDetailPayload(config: LoadCheckConfig, value: unknown): void {
  if (!isJsonObject(value) || value.category !== config.category) {
    throw new LoadCheckError("category-detail response category does not match the requested category");
  }
  const range = value.range;
  if (!isJsonObject(range)
    || range.startDate !== config.startDate
    || range.endDate !== config.endDate) {
    throw new LoadCheckError("category-detail response range does not match the requested range");
  }
  const pagination = value.pagination;
  const totals = value.totals;
  const returned = isJsonObject(pagination) ? pagination.returned : undefined;
  const shopCount = isJsonObject(totals) ? totals.shopCount : undefined;
  if (typeof returned !== "number"
    || !Number.isSafeInteger(returned)
    || returned < 1
    || typeof shopCount !== "number"
    || !Number.isSafeInteger(shopCount)
    || shopCount < 1
    || shopCount !== returned) {
    throw new LoadCheckError("category-detail response has no non-empty, internally consistent shop coverage");
  }
}

function validateViewPayload(config: LoadCheckConfig, view: LoadCheckView, value: unknown): void {
  if (view === "category-detail") validateCategoryDetailPayload(config, value);
}

async function sample(
  config: LoadCheckConfig,
  view: LoadCheckView,
  round: number,
  worker: number,
  secret: string,
  dependencies: LoadCheckDependencies,
): Promise<LoadCheckSample> {
  const target = targetFor(config, view);
  const timestamp = Math.floor((dependencies.now ?? Date.now)() / 1_000);
  const requestId = (dependencies.requestId ?? (() => `load-check-${randomUUID()}`))();
  const headers = signedHeaders(secret, target, timestamp, requestId);
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, config.timeoutMs);
  const started = performance.now();
  try {
    const response = await (dependencies.fetchImpl ?? fetch)(target, {
      method: "GET",
      headers,
      redirect: "manual",
      cache: "no-store",
      signal: controller.signal,
    });
    if (response.status !== 200) {
      await response.body?.cancel();
      throw new LoadCheckError(`${view} request returned HTTP ${response.status}`);
    }
    const dataRevision = response.headers.get("x-sales-data-revision");
    const sourceRevision = response.headers.get("x-sales-source-revision");
    if (!dataRevision || dataRevision !== sourceRevision || !/^\d+:\d+$/.test(dataRevision)) {
      await response.body?.cancel();
      throw new LoadCheckError(`${view} response revision headers are missing or inconsistent`);
    }
    const body = await readBoundedJson(response, config.maxResponseBytes);
    validateViewPayload(config, view, body.value);
    const durationMs = performance.now() - started;
    return {
      view,
      round,
      worker,
      durationMs: Number(durationMs.toFixed(2)),
      overviewCache: response.headers.get("x-sales-overview-cache"),
      cacheControl: response.headers.get("cache-control"),
      revision: dataRevision,
      jsonSha256: createHash("sha256").update(stableJson(body.value), "utf8").digest("hex"),
      responseBytes: body.bytes,
    };
  } catch (error) {
    if (timedOut || controller.signal.aborted) throw new LoadCheckError(`${view} request timed out after ${config.timeoutMs}ms`);
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function percentile(sorted: number[], fraction: number): number {
  const index = Math.max(0, Math.ceil(sorted.length * fraction) - 1);
  return Number((sorted[index] ?? 0).toFixed(2));
}

function statistics(samples: LoadCheckSample[]): LoadCheckStatistics {
  const durations = samples.map((value) => value.durationMs).sort((left, right) => left - right);
  return {
    requests: durations.length,
    p50Ms: percentile(durations, 0.5),
    p95Ms: percentile(durations, 0.95),
    p99Ms: percentile(durations, 0.99),
    maxMs: Number((durations.at(-1) ?? 0).toFixed(2)),
  };
}

function thresholdViolations(
  overall: LoadCheckStatistics,
  byView: Record<LoadCheckView, LoadCheckStatistics | null>,
  views: LoadCheckView[],
  thresholds: LoadCheckThresholds | undefined,
): LoadCheckThresholdViolation[] {
  if (thresholds === undefined) return [];
  const scopes: Array<{ scope: "overall" | LoadCheckView; statistics: LoadCheckStatistics }> = [
    { scope: "overall", statistics: overall },
    ...views.map((view) => ({ scope: view, statistics: byView[view]! })),
  ];
  const violations: LoadCheckThresholdViolation[] = [];
  for (const { scope, statistics: selected } of scopes) {
    for (const metric of ["p95Ms", "p99Ms", "maxMs"] as const) {
      const thresholdMs = thresholds[metric];
      if (thresholdMs !== undefined && selected[metric] > thresholdMs) {
        violations.push({ scope, metric, observedMs: selected[metric], thresholdMs });
      }
    }
  }
  return violations;
}

export async function runLoadCheck(config: LoadCheckConfig, dependencies: LoadCheckDependencies = {}): Promise<LoadCheckReport> {
  validateRuntimeConfig(config);
  const secret = requireSecret(dependencies.environment ?? process.env);
  const samples: LoadCheckSample[] = [];
  const expectedDigest = new Map<LoadCheckView, string>();
  let expectedRevision: string | null = null;

  for (const view of config.views) {
    for (let round = 1; round <= config.rounds; round += 1) {
      const batch = await Promise.all(Array.from(
        { length: config.concurrency },
        (_, index) => sample(config, view, round, index + 1, secret, dependencies),
      ));
      for (const result of batch) {
        const digest = expectedDigest.get(view);
        if (digest !== undefined && digest !== result.jsonSha256) {
          throw new LoadCheckError(`${view} JSON digest changed during the load check`);
        }
        expectedDigest.set(view, result.jsonSha256);
        if (expectedRevision !== null && expectedRevision !== result.revision) {
          throw new LoadCheckError(`revision changed during the load check: ${expectedRevision} -> ${result.revision}`);
        }
        expectedRevision = result.revision;
        samples.push(result);
        dependencies.onSample?.(result);
      }
    }
  }
  if (expectedRevision === null || samples.length === 0) throw new LoadCheckError("load check produced no samples");

  const overall = statistics(samples);
  const byView = Object.fromEntries(loadCheckViews.map((view) => {
    const selected = samples.filter((sampleValue) => sampleValue.view === view);
    return [view, selected.length > 0 ? statistics(selected) : null];
  })) as Record<LoadCheckView, LoadCheckStatistics | null>;
  const thresholds = validateThresholds(config.thresholds, config.timeoutMs);
  const violations = thresholdViolations(overall, byView, config.views, thresholds);
  const report: LoadCheckReport = {
    revision: expectedRevision,
    samples,
    overall,
    byView,
    thresholds: thresholds ?? null,
    thresholdViolations: violations,
    passed: violations.length === 0,
  };
  if (!report.passed) throw new LoadCheckThresholdError(report);
  return report;
}

function writeSummary(config: LoadCheckConfig, report: LoadCheckReport): void {
  process.stdout.write(`${JSON.stringify({
    type: "summary",
    baseUrl: config.baseUrl.origin,
    startDate: config.startDate,
    endDate: config.endDate,
    views: config.views,
    category: config.category,
    concurrency: config.concurrency,
    rounds: config.rounds,
    revision: report.revision,
    overall: report.overall,
    byView: report.byView,
    thresholds: report.thresholds,
    thresholdViolations: report.thresholdViolations,
    passed: report.passed,
  })}\n`);
}

async function main(): Promise<void> {
  if (process.argv.slice(2).some((value) => value === "--help" || value === "-h")) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  const config = parseLoadCheckArgs(process.argv.slice(2));
  try {
    const report = await runLoadCheck(config, {
      onSample(sampleValue) {
        process.stdout.write(`${JSON.stringify({ type: "sample", ...sampleValue })}\n`);
      },
    });
    writeSummary(config, report);
  } catch (error) {
    if (error instanceof LoadCheckThresholdError) writeSummary(config, error.report);
    throw error;
  }
}

const directEntry = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (directEntry === import.meta.url) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
