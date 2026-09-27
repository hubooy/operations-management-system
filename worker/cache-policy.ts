const NO_STORE = "no-store";
const HTML_NO_STORE = "no-store, must-revalidate";
const ONE_YEAR_SECONDS = "31536000";
const HASHED_PRIVATE_IMAGE_PATH = /^\/api\/(?:market\/images|netshop\/product-images)\/[a-f0-9]{64}$/;

export interface DynamicCachePolicyInput {
  pathname: string;
  method: string;
  status: number;
  requestAccept: string | null;
  requestRsc: string | null;
  responseCacheControl: string | null;
  responseContentType: string | null;
}

function hasCacheDirective(cacheControl: string | null, directive: string) {
  return (cacheControl ?? "")
    .split(",")
    .map((part) => part.trim().toLowerCase())
    .includes(directive);
}

function hasOneYearMaxAge(cacheControl: string | null) {
  return (cacheControl ?? "")
    .split(",")
    .map((part) => part.trim().toLowerCase())
    .some((part) => {
      const match = /^max-age\s*=\s*"?(\d+)"?$/.exec(part);
      return match?.[1] === ONE_YEAR_SECONDS;
    });
}

function isApiPath(pathname: string) {
  return pathname === "/api" || pathname.startsWith("/api/");
}

function isHtmlOrRsc(input: DynamicCachePolicyInput) {
  const contentType = input.responseContentType?.toLowerCase() ?? "";
  return contentType.includes("text/html")
    || contentType.includes("text/x-component")
    || input.requestRsc === "1"
    || (input.requestAccept?.toLowerCase().includes("text/html") ?? false);
}

/** Returns the cache-control override, or null when the response must be left untouched. */
export function resolveDynamicCacheControl(input: DynamicCachePolicyInput): string | null {
  if (input.pathname === "/assets" || input.pathname.startsWith("/assets/")) return null;

  if (isApiPath(input.pathname)) {
    const method = input.method.toUpperCase();
    const canKeepImmutablePrivateImage = (method === "GET" || method === "HEAD")
      && input.status === 200
      && HASHED_PRIVATE_IMAGE_PATH.test(input.pathname)
      && hasCacheDirective(input.responseCacheControl, "private")
      && hasCacheDirective(input.responseCacheControl, "immutable")
      && hasOneYearMaxAge(input.responseCacheControl);
    return canKeepImmutablePrivateImage ? null : NO_STORE;
  }

  return isHtmlOrRsc(input) ? HTML_NO_STORE : null;
}

export function enforceDynamicCachePolicy(request: Request, response: Response): Response {
  const url = new URL(request.url);
  const cacheControl = resolveDynamicCacheControl({
    pathname: url.pathname,
    method: request.method,
    status: response.status,
    requestAccept: request.headers.get("accept"),
    requestRsc: request.headers.get("rsc"),
    responseCacheControl: response.headers.get("cache-control"),
    responseContentType: response.headers.get("content-type"),
  });

  if (cacheControl === null || response.headers.get("cache-control") === cacheControl) return response;

  const headers = new Headers(response.headers);
  headers.set("cache-control", cacheControl);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}
