import { PublicApiError } from "@/lib/http/api-error";

export const NETSHOP_QUERY_MAX_DAYS = 730;
export const NETSHOP_QUERY_MAX_PAGE = 10_000;
export const NETSHOP_QUERY_MAX_PAGE_SIZE = 100;
export const NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE = 500;
export const NETSHOP_OUTLET_MAX_ITEMS = 50;
export const NETSHOP_OUTLET_SEPARATOR = "\u001f";

export type NetshopOutletFilter = {
  platform: string;
  shopName: string;
};

export type NetshopProductCatalogView = "full" | "page";
export type NetshopProductPerformanceView = "summary" | "full" | "page";

export class NetshopQueryError extends Error {
  readonly status = 400;
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "NetshopQueryError";
    this.code = code;
  }
}

export type NetshopQueryPeriod = {
  startDate: string;
  endDate: string;
  endExclusive: string;
  days: number;
};

function invalidOutletFilter(): never {
  throw new NetshopQueryError(
    "invalid_outlet_filter",
    "outlet 必须使用有效的平台与店铺复合键",
  );
}

function normalizeOutletParts(platformValue: unknown, shopNameValue: unknown): NetshopOutletFilter {
  if (typeof platformValue !== "string" || typeof shopNameValue !== "string") {
    return invalidOutletFilter();
  }
  const platform = platformValue.trim();
  const shopName = shopNameValue.trim();
  if (
    !platform
    || !shopName
    || platform.length > 100
    || shopName.length > 100
    || /[\u0000-\u001f\u007f]/.test(platform)
    || /[\u0000-\u001f\u007f]/.test(shopName)
  ) {
    return invalidOutletFilter();
  }
  return { platform, shopName };
}

export function netshopOutletKey(platform: string, shopName: string) {
  return `${platform}${NETSHOP_OUTLET_SEPARATOR}${shopName}`;
}

export function readNetshopOutletFilters(values: readonly string[]): NetshopOutletFilter[] {
  if (values.length > NETSHOP_OUTLET_MAX_ITEMS) {
    throw new NetshopQueryError(
      "too_many_outlet_filters",
      `outlet 筛选最多 ${NETSHOP_OUTLET_MAX_ITEMS} 项`,
    );
  }
  const parsed = values.map((value) => {
    const separatorIndex = value.indexOf(NETSHOP_OUTLET_SEPARATOR);
    if (
      separatorIndex <= 0
      || separatorIndex !== value.lastIndexOf(NETSHOP_OUTLET_SEPARATOR)
    ) {
      return invalidOutletFilter();
    }
    return normalizeOutletParts(
      value.slice(0, separatorIndex),
      value.slice(separatorIndex + NETSHOP_OUTLET_SEPARATOR.length),
    );
  });
  return [...new Map(parsed.map((value) => [netshopOutletKey(value.platform, value.shopName), value])).values()];
}

export function normalizeNetshopOutletFilters(
  values: readonly NetshopOutletFilter[] | undefined,
): NetshopOutletFilter[] {
  if (!values) return [];
  if (values.length > NETSHOP_OUTLET_MAX_ITEMS) {
    throw new NetshopQueryError(
      "too_many_outlet_filters",
      `outlet 筛选最多 ${NETSHOP_OUTLET_MAX_ITEMS} 项`,
    );
  }
  const normalized = values.map((value) => normalizeOutletParts(value?.platform, value?.shopName));
  return [...new Map(normalized.map((value) => [netshopOutletKey(value.platform, value.shopName), value])).values()];
}

export function isNetshopIsoDate(value: string | undefined | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function addIsoDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  const result = date.toISOString().slice(0, 10);
  if (!isNetshopIsoDate(result)) {
    throw new NetshopQueryError("invalid_date_range", "endDate 超出支持范围");
  }
  return result;
}

export function resolveNetshopQueryPeriod(
  rawStartDate: string | undefined | null,
  rawEndDate: string | undefined | null,
  maximumDays = NETSHOP_QUERY_MAX_DAYS,
): NetshopQueryPeriod | null {
  const startDate = rawStartDate?.trim() ?? "";
  const endDate = rawEndDate?.trim() ?? "";
  if (!startDate && !endDate) return null;
  if (!startDate || !endDate) {
    throw new NetshopQueryError("invalid_date_range", "startDate 和 endDate 必须同时提供");
  }
  if (!isNetshopIsoDate(startDate) || !isNetshopIsoDate(endDate)) {
    throw new NetshopQueryError("invalid_date", "统计日期必须是有效的 YYYY-MM-DD 自然日");
  }
  if (startDate > endDate) {
    throw new NetshopQueryError("invalid_date_range", "startDate 不能晚于 endDate");
  }
  const startMs = Date.parse(`${startDate}T00:00:00Z`);
  const endMs = Date.parse(`${endDate}T00:00:00Z`);
  const days = Math.round((endMs - startMs) / 86_400_000) + 1;
  if (!Number.isSafeInteger(maximumDays) || maximumDays < 1 || days > maximumDays) {
    throw new NetshopQueryError("date_range_too_large", `网店统计周期最多支持 ${maximumDays} 天`);
  }
  return { startDate, endDate, endExclusive: addIsoDays(endDate, 1), days };
}

export function boundedNetshopInteger(
  value: number | undefined,
  name: "page" | "pageSize",
  fallback: number,
  minimum: number,
  maximum: number,
) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new NetshopQueryError("invalid_pagination", `${name} 必须是 ${minimum} 到 ${maximum} 的整数`);
  }
  return value;
}

export function readNetshopQueryInteger(
  value: string | null,
  name: "page" | "pageSize",
  fallback: number,
  minimum: number,
  maximum: number,
) {
  if (value === null || value === "") return fallback;
  if (!/^[1-9]\d*$/.test(value)) {
    throw new NetshopQueryError("invalid_pagination", `${name} 必须是 ${minimum} 到 ${maximum} 的整数`);
  }
  const parsed = Number(value);
  return boundedNetshopInteger(parsed, name, fallback, minimum, maximum);
}

function readStrictNetshopView<T extends string>(
  values: readonly string[],
  allowed: readonly T[],
  fallback: T,
) {
  if (values.length === 0) return fallback;
  if (values.length !== 1 || !allowed.includes(values[0] as T)) {
    throw new NetshopQueryError("invalid_view", `view 必须且只能是 ${allowed.join("、")} 之一`);
  }
  return values[0] as T;
}

export function readNetshopProductCatalogView(values: readonly string[]): NetshopProductCatalogView {
  return readStrictNetshopView(values, ["full", "page"] as const, "full");
}

export function readNetshopProductPerformanceView(values: readonly string[]): NetshopProductPerformanceView {
  return readStrictNetshopView(values, ["summary", "full", "page"] as const, "full");
}

export function readNetshopSnapshotToken(values: readonly string[], required: boolean) {
  if (values.length === 0) {
    if (required) throw new NetshopQueryError("snapshot_token_required", "page 视图必须提供 snapshotToken");
    return undefined;
  }
  if (!required) {
    throw new NetshopQueryError("unexpected_snapshot_token", "只有 page 视图可以提供 snapshotToken");
  }
  if (values.length !== 1 || !/^[a-fA-F0-9]{64}$/.test(values[0] ?? "")) {
    throw new NetshopQueryError("invalid_snapshot_token", "snapshotToken 必须是唯一的 64 位十六进制版本令牌");
  }
  return values[0].toLowerCase();
}

export function netshopQueryErrorPayload(error: unknown, fallback: string) {
  if (error instanceof NetshopQueryError) {
    return { body: { error: error.message, code: error.code }, status: error.status };
  }
  if (error instanceof PublicApiError) {
    return { body: { error: error.message, code: error.code }, status: error.status };
  }
  return { body: { error: fallback, code: "internal_error" }, status: 500 };
}
