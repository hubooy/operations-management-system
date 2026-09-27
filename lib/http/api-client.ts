import { ApiError } from "@/lib/http/api-error";

type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  | JsonPrimitive
  | { readonly [key: string]: JsonValue }
  | readonly JsonValue[];

export type RequestJsonInit = Omit<RequestInit, "body"> & {
  body?: BodyInit | JsonValue;
};

type ErrorPayload = {
  code?: unknown;
  error?: unknown;
  message?: unknown;
  details?: unknown;
};

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function isNativeBody(value: unknown): value is BodyInit {
  if (typeof value === "string") return true;
  if (typeof Blob !== "undefined" && value instanceof Blob) return true;
  if (typeof FormData !== "undefined" && value instanceof FormData) return true;
  if (typeof URLSearchParams !== "undefined" && value instanceof URLSearchParams) {
    return true;
  }
  if (typeof ArrayBuffer !== "undefined") {
    if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return true;
  }
  if (typeof ReadableStream !== "undefined" && value instanceof ReadableStream) {
    return true;
  }
  return false;
}

function prepareBody(
  body: RequestJsonInit["body"],
  headers: Headers,
): BodyInit | undefined {
  if (body === undefined) return undefined;
  if (isNativeBody(body)) return body;
  if (!headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  return JSON.stringify(body);
}

function parseErrorPayload(value: unknown): ErrorPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as ErrorPayload;
}

function errorMessage(payload: ErrorPayload | null, status: number): string {
  if (typeof payload?.error === "string" && payload.error.trim()) {
    return payload.error.trim();
  }
  if (typeof payload?.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }
  return `请求失败（${status}）`;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch (cause) {
    throw new ApiError({
      status: response.status,
      code: "invalid_json_response",
      message: "服务器返回的数据格式不正确",
      cause,
    });
  }
}

export async function requestJson<T = unknown>(
  input: RequestInfo | URL,
  init: RequestJsonInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("accept")) headers.set("accept", "application/json");

  const body = prepareBody(init.body, headers);
  let response: Response;
  try {
    response = await fetch(input, {
      ...init,
      body,
      headers,
      credentials: init.credentials ?? "same-origin",
      cache: init.cache ?? "no-store",
    });
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: "网络请求失败",
      cause,
    });
  }

  const payload = await readJson(response);
  if (!response.ok) {
    const errorPayload = parseErrorPayload(payload);
    throw new ApiError({
      status: response.status,
      code:
        typeof errorPayload?.code === "string" && errorPayload.code.trim()
          ? errorPayload.code.trim()
          : `http_${response.status}`,
      message: errorMessage(errorPayload, response.status),
      details: errorPayload?.details,
    });
  }

  return payload as T;
}
