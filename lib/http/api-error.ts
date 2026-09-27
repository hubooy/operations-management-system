export type ApiErrorOptions = {
  status: number;
  code?: string;
  message: string;
  details?: unknown;
  cause?: unknown;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly details: unknown;

  constructor({ status, code, message, details, cause }: ApiErrorOptions) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type PublicApiErrorCode =
  | "invalid_request"
  | "access_denied"
  | "cross_site_request_rejected"
  | "unsupported_media_type"
  | "not_found"
  | "conflict"
  | "version_conflict"
  | "payload_too_large"
  | "rate_limited"
  | "ai_chat_not_dispatched"
  | "ai_chat_result_unknown"
  | "ai_request_cancelled"
  | "service_unavailable";

export class PublicApiError extends Error {
  readonly status: 400 | 403 | 404 | 409 | 413 | 415 | 422 | 429 | 499 | 503;
  readonly code: PublicApiErrorCode;

  constructor(
    status: PublicApiError["status"],
    code: PublicApiErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "PublicApiError";
    this.status = status;
    this.code = code;
  }
}

export function parsePositiveIntegerQuery(
  value: string | null,
  fallback: number,
  field: string,
  maximum = Number.MAX_SAFE_INTEGER,
) {
  if (value === null) return fallback;
  if (!/^[1-9]\d*$/.test(value)) {
    throw new PublicApiError(400, "invalid_request", `${field}必须为十进制正整数。`);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > maximum) {
    throw new PublicApiError(400, "invalid_request", `${field}超出允许范围。`);
  }
  return parsed;
}

export function requirePositiveSafeIntegerNumber(value: unknown, field: string) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new PublicApiError(400, "invalid_request", `${field}必须为 JSON 安全正整数。`);
  }
  return value;
}

export function safeApiErrorResponse(
  error: unknown,
  fallback: string,
  init: { shape?: "error" | "import"; headers?: HeadersInit } = {},
): Response {
  const headers = new Headers(init.headers);
  if (!headers.has("cache-control")) headers.set("cache-control", "no-store");
  if (error instanceof PublicApiError) {
    const body = init.shape === "import"
      ? { ok: false, status: "rejected", message: error.message, code: error.code }
      : { error: error.message, code: error.code };
    return Response.json(body, { status: error.status, headers });
  }
  const body = init.shape === "import"
    ? { ok: false, status: "rejected", message: fallback, code: "internal_error" }
    : { error: fallback, code: "internal_error" };
  return Response.json(body, { status: 500, headers });
}

export type ImportExecutionLike = {
  ok?: boolean;
  status?: string;
  code?: unknown;
  errors?: ReadonlyArray<{ code?: unknown }>;
};

const IMPORT_CONFLICT_CODES = new Set([
  "IMPORT_SCOPE_CHANGED",
  "IMPORT_SCOPE_OWNERSHIP_LOST",
  "IMPORT_OWNER_CHANGED",
  "IMPORT_OWNER_MISMATCH",
  "IMPORT_RESERVATION_LOST",
  "IMPORT_VERSION_CONFLICT",
  "VERSION_CONFLICT",
]);

const IMPORT_PAYLOAD_TOO_LARGE_CODES = new Set([
  "FILE_TOO_LARGE",
  "PAYLOAD_TOO_LARGE",
]);

const IMPORT_SERVICE_UNAVAILABLE_CODES = new Set([
  "DEPENDENCY_UNAVAILABLE",
  "MIGRATION_REQUIRED",
  "MISSING_SYSTEM_COST_SNAPSHOT",
  "SCHEMA_NOT_READY",
  "SERVICE_UNAVAILABLE",
]);

/** Keep successful replay semantics and map controlled import failures consistently. */
export function importExecutionHttpStatus(result: ImportExecutionLike): 200 | 201 | 409 | 413 | 422 | 503 {
  if (result.ok) return result.status === "imported" ? 201 : 200;
  const codes = [result.code, ...(result.errors ?? []).map((issue) => issue.code)]
    .filter((code): code is string => typeof code === "string")
    .map((code) => code.toUpperCase());
  if (codes.some((code) => IMPORT_CONFLICT_CODES.has(code))) return 409;
  if (codes.some((code) => IMPORT_PAYLOAD_TOO_LARGE_CODES.has(code))) return 413;
  if (codes.some((code) => IMPORT_SERVICE_UNAVAILABLE_CODES.has(code))) return 503;
  return 422;
}
