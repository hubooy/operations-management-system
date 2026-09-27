import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  MARKET_CONSUMER_QUERY_PATH,
  requestDjangoMarketService,
} from "@/lib/django/market-service";

function optionalString(value: unknown, maxLength: number) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") throw new Error("string argument expected");
  const normalized = value.trim();
  if (!normalized) return undefined;
  if (Array.from(normalized).length > maxLength) throw new Error("string argument too long");
  return normalized;
}

function stringArg(value: unknown, name: string, maxLength: number) {
  const normalized = optionalString(value, maxLength);
  if (!normalized) throw new Error(`${name} is required`);
  return normalized;
}

function integer(value: unknown, fallback: number, min: number, max: number) {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new Error(`integer argument must be between ${min} and ${max}`);
  }
  return value;
}

function list(value: unknown, maximum = 50): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > maximum) throw new Error("array argument is invalid");
  return [...new Set(value.map((item) => optionalString(item, 200)).filter(Boolean) as string[])];
}

export function marketAiFilters(args: Record<string, unknown>) {
  const startDate = optionalString(args.startDate, 10);
  const endDate = optionalString(args.endDate, 10);
  if ((startDate && !/^\d{4}-\d{2}-\d{2}$/.test(startDate))
    || (endDate && !/^\d{4}-\d{2}-\d{2}$/.test(endDate))) {
    throw new Error("date argument must use YYYY-MM-DD");
  }
  function selection(singleKey: string, listKey: string) {
    const single = optionalString(args[singleKey], 200);
    const multiple = list(args[listKey]);
    if (single && multiple.length && (multiple.length !== 1 || multiple[0] !== single)) {
      throw new Error(`conflicting ${singleKey} filters`);
    }
    return single ? [single] : multiple;
  }
  return {
    query: optionalString(args.query, 120) ?? "",
    categories: selection("category", "categories"),
    scopes: selection("scope", "scopes"),
    brands: selection("brand", "brands"),
    priceBands: list(args.priceBands),
    rankingDimensions: selection("rankingDimension", "rankingDimensions"),
    operationModes: selection("operationMode", "operationModes"),
    subcategories: selection("subcategory", "subcategories"),
    startDate: startDate ?? null,
    endDate: endDate ?? null,
  };
}

async function overview(args: Record<string, unknown>, principal: AppPrincipal) {
  return requestDjangoMarketService<Record<string, unknown>>(
    principal,
    {
      path: MARKET_CONSUMER_QUERY_PATH,
      service: "reader",
      payload: {
        operation: "overview",
        view: "full",
        page: 1,
        pageSize: integer(args.limit, 20, 10, 50),
        filters: marketAiFilters(args),
      },
    },
  );
}

export function marketAiOverview(data: Record<string, unknown>) {
  const rows = (value: unknown) => Array.isArray(value) ? value : [];
  const brands = data.brandAnalysis && typeof data.brandAnalysis === "object"
    ? data.brandAnalysis as Record<string, unknown> : {};
  return {
    basis: "current_top_ranking_coverage",
    summary: data.summary,
    dataRange: data.dataRange,
    salesRevision: data.salesRevision,
    trend: rows(data.trend).slice(0, 24),
    trendTotal: data.trendTotal,
    trendTruncated: data.trendTruncated || rows(data.trend).length > 24,
    brandAnalysis: {
      items: rows(brands.items).slice(0, 10),
      cr3Bps: brands.cr3Bps, cr5Bps: brands.cr5Bps, concentration: brands.concentration,
    },
    priceBandSummary: rows(data.priceBandSummary).slice(0, 10),
    subcategorySummary: rows(data.subcategorySummary).slice(0, 10),
    limits: {
      brands: 10, priceBands: 10, subcategories: 10, trendMonths: 24,
      brandItemsTruncated: rows(brands.items).length > 10,
      priceBandsTruncated: rows(data.priceBandSummary).length > 10,
      subcategoriesTruncated: rows(data.subcategorySummary).length > 10,
      dashboardDetailsOmitted: true,
    },
  };
}

export async function callMarketTool(
  name: string,
  args: Record<string, unknown>,
  principal: AppPrincipal,
): Promise<Record<string, unknown>> {
  if (name === "get_market_overview") {
    const result = await overview(args, principal);
    return { ...marketAiOverview(result.data), dataRevision: result.revision };
  }
  if (name === "get_market_brand_analysis") {
    const result = await overview(args, principal);
    return {
      brandAnalysis: result.data.brandAnalysis,
      dataRange: result.data.dataRange,
      basis: "current_top_ranking_coverage",
      dataRevision: result.revision,
    };
  }
  if (name === "get_market_price_band_analysis") {
    const result = await overview(args, principal);
    return {
      priceBandSummary: result.data.priceBandSummary,
      priceBandTrend: result.data.priceBandTrend,
      dataRange: result.data.dataRange,
      basis: "confirmed_market_position_price",
      dataRevision: result.revision,
    };
  }
  if (name === "get_market_pending_review_summary") {
    const category = optionalString(args.category, 200) ?? "";
    const result = await requestDjangoMarketService<Record<string, unknown>>(
      principal,
      {
        path: MARKET_CONSUMER_QUERY_PATH,
        service: "reader",
        payload: { operation: "pending_review_summary", category },
      },
    );
    return { ...result.data, dataRevision: result.revision };
  }
  if (name === "get_market_sku_trend") {
    const skuCode = stringArg(args.skuCode, "skuCode", 80);
    const category = stringArg(args.category, "category", 120);
    const scope = stringArg(args.scope, "scope", 120);
    const rankingDimension = stringArg(args.rankingDimension, "rankingDimension", 3);
    if (rankingDimension !== "SKU" && rankingDimension !== "SPU") {
      throw new Error("rankingDimension must be SKU or SPU");
    }
    const result = await requestDjangoMarketService<{ items?: unknown[] }>(
      principal,
      {
        path: MARKET_CONSUMER_QUERY_PATH,
        service: "reader",
        payload: {
          operation: "sku_trend",
          skuCode,
          category,
          scope,
          rankingDimension,
        },
      },
    );
    const limit = integer(args.limit, 24, 1, 60);
    const items = Array.isArray(result.data.items) ? result.data.items : [];
    return {
      skuCode,
      category,
      scope,
      rankingDimension,
      returned: Math.min(items.length, limit),
      truncated: items.length > limit,
      items: items.slice(-limit),
      currency: "CNY",
      monetaryUnit: "cents",
      basis: "current_top_ranking_coverage",
      dataRevision: result.revision,
    };
  }
  throw new Error("market tool is not registered");
}
