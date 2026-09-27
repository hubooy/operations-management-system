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
import { PublicApiError, safeApiErrorResponse } from "@/lib/http/api-error";
import { readBoundedJsonObject } from "@/lib/http/bounded-json";
import type { MarketImageRepairMapping } from "@/lib/market/image-repair";

const MARKET_IMAGE_REPAIR_BODY_BYTES_MAX = 512 * 1024;

function integerParam(value: string | null, fallback: number) {
  if (value === null || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) {
    throw new PublicApiError(400, "invalid_request", "分页参数必须是整数");
  }
  return parsed;
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场商品图片修复");
    const params = new URL(request.url).searchParams;
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: {
          operation: "image_repair_candidates",
          params: {
            page: integerParam(params.get("page"), 1),
            pageSize: integerParam(params.get("pageSize"), 100),
          },
        },
      },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      headers: {
        "cache-control": "no-store",
        "x-market-data-revision": result.revision,
      },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "读取缺图商品失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场商品图片修复", "修改");
    const body = await readBoundedJsonObject(
      request,
      MARKET_IMAGE_REPAIR_BODY_BYTES_MAX,
    ) as { repairs?: MarketImageRepairMapping[] };
    const result = await requestDjangoMarketService<{
      ok: boolean;
      result: Record<string, unknown>;
    }>(
      principal,
      {
        path: MARKET_COMMANDS_PATH,
        service: "writer",
        payload: {
          contractVersion: "market-command-v1",
          domain: "images",
          command: { action: "apply_image_repairs", repairs: body.repairs ?? [] },
        },
      },
      { signal: request.signal },
    );
    return Response.json(
      { ok: true, result: result.data.result },
      {
        headers: {
          "cache-control": "no-store",
          "x-market-data-revision": result.revision,
        },
      },
    );
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "修复商品图片失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
