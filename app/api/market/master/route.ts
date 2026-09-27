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
import { parseMarketMasterView } from "@/lib/market/admin-query-contract";

type JsonRecord = Record<string, unknown>;
const MARKET_WRITE_BODY_BYTES_MAX = 256 * 1024;

function integer(params: URLSearchParams, key: string, fallback: number) {
  const value = params.get(key);
  if (!value) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) {
    throw new PublicApiError(400, "invalid_request", `${key} 必须是整数`);
  }
  return parsed;
}

function selection(value: string): JsonRecord {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new PublicApiError(400, "invalid_request", "商品对比身份格式无效");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new PublicApiError(400, "invalid_request", "商品对比身份格式无效");
  }
  return parsed as JsonRecord;
}

function queryParams(params: URLSearchParams): JsonRecord {
  const section = params.get("section");
  const result: JsonRecord = {
    section: ["database", "brand", "mapping", "subcategory", "data"].includes(section ?? "")
      ? section
      : "all",
    q: params.get("q") ?? "",
    category: params.get("category") ?? "",
    categories: params.getAll("category"),
    scopes: params.getAll("scope"),
    rankingDimensions: params.getAll("rankingDimension"),
    operationModes: params.getAll("operationMode"),
    brands: params.getAll("brand"),
    subcategories: params.getAll("subcategory"),
    priceStatuses: params.getAll("priceStatus"),
    candidatePriceSources: params.getAll("priceSource"),
    annotationStatuses: params.getAll("annotationStatus"),
    page: integer(params, "page", 1),
    pageSize: integer(params, "pageSize", 30),
    pendingPriceCategories: params.getAll("pendingPriceCategory"),
    pendingPriceSources: params.getAll("pendingPriceSource"),
    pendingPricePage: integer(params, "pendingPricePage", 1),
    pendingPricePageSize: integer(params, "pendingPricePageSize", 20),
    skuCodes: params.getAll("skuCode"),
    priceBands: params.getAll("priceBand"),
    startDate: params.get("startDate") ?? "",
    endDate: params.get("endDate") ?? "",
  };
  const selections = params.getAll("selection");
  if (selections.length) result.selections = selections.map(selection);
  return result;
}

export async function GET(request: Request) {
  try {
    const principal = await requireAppPrincipal(["viewer", "analyst", "operator", "admin"]);
    requireUnrestrictedDataScope(principal, "市场主数据");
    const params = new URL(request.url).searchParams;
    const view = parseMarketMasterView(params);
    const result = await requestDjangoMarketService<JsonRecord>(
      principal,
      {
        path: MARKET_QUERIES_PATH,
        service: "reader",
        payload: { operation: "master", view, params: queryParams(params) },
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
    return safeApiErrorResponse(error, "读取市场主数据失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}

export async function POST(request: Request) {
  try {
    const principal = await requireAppPrincipal(["admin"]);
    requireUnrestrictedDataScope(principal, "市场主数据", "修改");
    const command = await readBoundedJsonObject(request, MARKET_WRITE_BODY_BYTES_MAX);
    const result = await requestDjangoMarketService<{ ok: boolean; result: unknown }>(
      principal,
      {
        path: MARKET_COMMANDS_PATH,
        service: "writer",
        payload: {
          contractVersion: "market-command-v1",
          domain: "master",
          command,
        },
      },
      { signal: request.signal },
    );
    return Response.json(result.data, {
      headers: {
        "cache-control": "no-store",
        "x-market-data-revision": result.revision,
        ...(result.replayed ? { "x-teruisi-write-replay": "1" } : {}),
      },
    });
  } catch (error) {
    const auth = authorizationErrorResponse(error);
    if (auth) return auth;
    return safeApiErrorResponse(error, "市场主数据操作失败", {
      headers: { "cache-control": "no-store" },
    });
  }
}
