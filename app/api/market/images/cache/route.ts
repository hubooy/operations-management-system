import {
  authorizationErrorResponse,
  requireAppPrincipal,
  requireUnrestrictedDataScope,
} from "@/lib/auth/authorization";
import {
  MARKET_COMMANDS_PATH,
  MARKET_QUERIES_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";
import { safeApiErrorResponse } from "@/lib/http/api-error";
import { readBoundedJsonObject } from "@/lib/http/bounded-json";
import {
  parseMarketImageCacheGetQuery,
  parseMarketImageCachePostBody,
} from "@/lib/market/image-cache-request";

const MARKET_IMAGE_CACHE_BODY_BYTES_MAX = 16 * 1024;

function noStore(payload: unknown, init: ResponseInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("cache-control", "no-store");
  return Response.json(payload, { ...init, headers });
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场商品图片缓存", "查看");
    const query = parseMarketImageCacheGetQuery(new URL(request.url).searchParams);
    if (!query.ok) return noStore({ error: query.error }, { status: 400 });
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: { operation: "image_cache_job", params: query.value },
      },
      { signal: request.signal },
    );
    return noStore(result.data, {
      headers: { "x-market-data-revision": result.revision },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "市场商品图片缓存状态读取失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场商品图片缓存", "修改");
    const body = await readBoundedJsonObject(request, MARKET_IMAGE_CACHE_BODY_BYTES_MAX);
    const parsed = parseMarketImageCachePostBody(body);
    if (!parsed.ok) return noStore({ error: parsed.error }, { status: 400 });
    const result = await requestDjangoMarketService<{
      ok: boolean;
      result: { ok: boolean; job: { status?: string } };
    }>(
      principal,
      {
        path: MARKET_COMMANDS_PATH,
        service: "writer",
        payload: {
          contractVersion: "market-command-v1",
          domain: "images",
          command: { action: "create_image_cache_job", ...parsed.value },
        },
      },
      { signal: request.signal },
    );
    const job = result.data.result.job;
    return noStore(
      { ok: true, job },
      {
        status: job.status === "completed" ? 200 : 202,
        headers: { "x-market-data-revision": result.revision },
      },
    );
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "市场商品图片缓存失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
