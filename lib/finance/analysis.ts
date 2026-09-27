import type { FinanceDatabase } from "./database";
import type { FinanceTarget } from "./types";
import { PublicApiError } from "@/lib/http/api-error";

type FinanceMonthRow = {
  month: string;
  source_file_name: string;
  imported_at: string;
  shop_count: number;
  subject_count: number;
};

type FinanceLineRow = {
  month: string;
  metric_key: string;
  subject_name: string;
  scope_type: "business" | "shop";
  scope_name: string;
  group_name: string;
  amount_cents: number | null;
  rate_bps: number | null;
  sort_order: number;
};

type TargetRow = {
  id: string;
  period_type: FinanceTarget["periodType"];
  period_key: string;
  platform: string;
  shop_name: string;
  category: string;
  manager: string;
  sales_target_cents: number;
  profit_target_cents: number;
  small_margin_bps: number;
  inventory_cleanup_target_cents: number;
  promotion_fee_ratio_bps: number;
  stagnant_inventory_target_cents: number;
  version: number;
  created_at: string;
  updated_at: string;
};

type TargetAggregateRow = {
  period_type: "month" | "year";
  sales_target_cents: number;
  profit_target_cents: number;
  small_margin_bps: number;
  inventory_cleanup_target_cents: number;
  promotion_fee_ratio_bps: number;
  stagnant_inventory_target_cents: number;
  target_count: number;
};

export type FinanceActualMetrics = {
  grossSalesCents: number;
  returnAmountCents: number;
  netSalesCents: number;
  netCostCents: number;
  grossProfitCents: number;
  grossMarginBps: number;
  returnRateBps: number;
  sellingExpenseCents: number;
  smallProfitCents: number;
  smallMarginBps: number;
  otherExpenseCents: number;
  profitCents: number;
  profitMarginBps: number;
  promotionExpenseCents: number;
  promotionFeeRatioBps: number;
};

export type FinanceAnalysisOptions = {
  requestedMonths?: string[];
  allMonths?: boolean;
  fallbackToLatestCompletedMonth?: boolean;
  platformNames?: string[];
  shopKeys?: string[];
};

export type FinanceTargetTotals = {
  salesTargetCents: number;
  profitTargetCents: number;
  smallMarginBps: number;
  inventoryCleanupTargetCents: number;
  promotionFeeRatioBps: number;
  stagnantInventoryTargetCents: number;
  targetCount: number;
};

const metricKeys = [
  "gross_sales", "return_amount", "net_sales", "net_cost", "gross_profit",
  "gross_margin", "selling_expense_total", "small_profit", "small_margin",
  "other_expense_total", "profit", "profit_margin",
] as const;
export const MAX_FINANCE_ANALYSIS_MONTHS = 24;
export const MAX_FINANCE_MONTH_OPTIONS = 120;
export const MAX_FINANCE_SHOP_OPTIONS = 500;
export const MAX_FINANCE_PLATFORM_OPTIONS = 100;

const emptyMetrics = (): FinanceActualMetrics => ({
  grossSalesCents: 0,
  returnAmountCents: 0,
  netSalesCents: 0,
  netCostCents: 0,
  grossProfitCents: 0,
  grossMarginBps: 0,
  returnRateBps: 0,
  sellingExpenseCents: 0,
  smallProfitCents: 0,
  smallMarginBps: 0,
  otherExpenseCents: 0,
  profitCents: 0,
  profitMarginBps: 0,
  promotionExpenseCents: 0,
  promotionFeeRatioBps: 0,
});

function shiftMonth(month: string, delta: number) {
  const [year, monthNumber] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, monthNumber - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

function isSelectableShopName(name: string) {
  const compact = name.replace(/[\s　]+/g, "");
  if (/^分销[-—]/.test(compact)) return false;
  if (/^(?:[1-9]|1[0-2])月(?:项目费率)?$/.test(compact)) return false;
  return true;
}

export type FinanceShopOption = { key: string; name: string; platform: string };

export class FinanceDimensionFilterError extends PublicApiError {
  readonly invalidPlatforms: string[];
  readonly invalidShops: FinanceShopOption[];
  readonly incompatibleShops: FinanceShopOption[];

  constructor({
    message,
    invalidPlatforms,
    invalidShops,
    incompatibleShops,
  }: {
    message: string;
    invalidPlatforms: string[];
    invalidShops: FinanceShopOption[];
    incompatibleShops: FinanceShopOption[];
  }) {
    super(400, "invalid_request", message);
    this.name = "FinanceDimensionFilterError";
    this.invalidPlatforms = invalidPlatforms;
    this.invalidShops = invalidShops;
    this.incompatibleShops = incompatibleShops;
  }
}

export function financeShopIdentityKey(platform: string, name: string) {
  return JSON.stringify([platform, name]);
}

export function parseFinanceShopIdentityKey(value: string) {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!Array.isArray(parsed) || parsed.length !== 2 || parsed.some((item) => typeof item !== "string")) return null;
    const platform = parsed[0].trim();
    const name = parsed[1].trim();
    if (!platform || !name || platform.length > 100 || name.length > 100) return null;
    return { key: financeShopIdentityKey(platform, name), platform, name };
  } catch {
    return null;
  }
}

function normalizedRequestedNames(values: string[] | undefined, label: string) {
  const normalized = [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))];
  if (normalized.length > 50 || normalized.some((value) => value.length > 100)) {
    throw new PublicApiError(400, "invalid_request", `${label}筛选项数量或长度超出限制。`);
  }
  return normalized;
}

export function resolveFinanceDimensionFilters(
  allShopOptions: readonly FinanceShopOption[],
  requestedPlatformNames?: string[],
  requestedShopKeys?: string[],
) {
  const requestedPlatforms = normalizedRequestedNames(requestedPlatformNames, "平台");
  const rawShopKeys = [...new Set((requestedShopKeys ?? []).map((value) => value.trim()).filter(Boolean))];
  if (rawShopKeys.length > 50 || rawShopKeys.some((value) => value.length > 240)) {
    throw new PublicApiError(400, "invalid_request", "店铺筛选项数量或长度超出限制。");
  }
  const requestedShops = rawShopKeys.map((value) => parseFinanceShopIdentityKey(value));
  if (requestedShops.some((item) => item === null)) {
    throw new PublicApiError(400, "invalid_request", "店铺筛选必须使用平台与店铺组成的稳定复合标识。");
  }
  const shopPairs = requestedShops.filter((item): item is FinanceShopOption => item !== null);
  const knownShops = new Set(allShopOptions.map((item) => item.key));
  const knownPlatforms = new Set(allShopOptions.map((item) => item.platform));
  const invalidShops = shopPairs.filter((item) => !knownShops.has(item.key));
  const invalidPlatforms = requestedPlatforms.filter((name) => !knownPlatforms.has(name));
  const incompatibleShops = requestedPlatforms.length
    ? shopPairs.filter((item) => !requestedPlatforms.includes(item.platform))
    : [];
  if (invalidShops.length || invalidPlatforms.length || incompatibleShops.length) {
    const detail = [
      ...(invalidPlatforms.length ? [`平台：${invalidPlatforms.join("、")}`] : []),
      ...(invalidShops.length ? [`店铺：${invalidShops.map((item) => `${item.platform} · ${item.name}`).join("、")}`] : []),
      ...(incompatibleShops.length ? [`店铺不属于所选平台：${incompatibleShops.map((item) => `${item.platform} · ${item.name}`).join("、")}`] : []),
    ].join("；");
    throw new FinanceDimensionFilterError({
      message: `筛选项不存在或不属于当前财务期间（${detail}）。`,
      invalidPlatforms,
      invalidShops,
      incompatibleShops,
    });
  }
  return {
    platformFilter: new Set(requestedPlatforms),
    shopFilter: new Set(shopPairs.map((item) => item.key)),
    shopPairs,
    hasDimensionFilter: requestedPlatforms.length > 0 || shopPairs.length > 0,
  };
}

function changeRate(current: number, comparison: number | null) {
  if (comparison === null || comparison === 0) return null;
  return (current - comparison) / Math.abs(comparison);
}

function metricMap(rows: FinanceLineRow[]) {
  const values = new Map<string, { amount: number; rate: number }>();
  rows.forEach((row) => values.set(row.metric_key, {
    amount: Number(row.amount_cents ?? 0),
    rate: Number(row.rate_bps ?? 0),
  }));
  return values;
}

function metricsFromRows(rows: FinanceLineRow[], promotionExpenseCents = 0): FinanceActualMetrics {
  const values = metricMap(rows);
  const amount = (key: string) => values.get(key)?.amount ?? 0;
  const rate = (key: string) => values.get(key)?.rate ?? 0;
  const netSalesCents = amount("net_sales");
  const grossSalesCents = amount("gross_sales");
  const returnAmountCents = amount("return_amount");
  const grossProfitCents = amount("gross_profit");
  const smallProfitCents = amount("small_profit");
  const otherExpenseCents = amount("other_expense_total");
  // Some WPS templates spell 利润 with the traditional character “潤”.
  // The report formula is small profit minus other expenses, so derive it
  // when an older imported month lacks the canonical metric key.
  const profitCents = values.has("profit") ? amount("profit") : smallProfitCents - otherExpenseCents;
  return {
    grossSalesCents,
    returnAmountCents,
    netSalesCents,
    netCostCents: amount("net_cost"),
    grossProfitCents,
    grossMarginBps: rate("gross_margin") || (netSalesCents === 0 ? 0 : Math.round(grossProfitCents / netSalesCents * 10_000)),
    returnRateBps: grossSalesCents === 0 ? 0 : Math.round(Math.abs(returnAmountCents) / Math.abs(grossSalesCents) * 10_000),
    sellingExpenseCents: amount("selling_expense_total"),
    smallProfitCents,
    smallMarginBps: rate("small_margin") || (netSalesCents === 0 ? 0 : Math.round(smallProfitCents / netSalesCents * 10_000)),
    otherExpenseCents,
    profitCents,
    profitMarginBps: rate("profit_margin") || (netSalesCents === 0 ? 0 : Math.round(profitCents / netSalesCents * 10_000)),
    promotionExpenseCents,
    promotionFeeRatioBps: netSalesCents === 0 ? 0 : Math.round(promotionExpenseCents / netSalesCents * 10_000),
  };
}

function sumMetrics(items: FinanceActualMetrics[]) {
  const total = items.reduce((result, item) => {
    result.grossSalesCents += item.grossSalesCents;
    result.returnAmountCents += item.returnAmountCents;
    result.netSalesCents += item.netSalesCents;
    result.netCostCents += item.netCostCents;
    result.grossProfitCents += item.grossProfitCents;
    result.sellingExpenseCents += item.sellingExpenseCents;
    result.smallProfitCents += item.smallProfitCents;
    result.otherExpenseCents += item.otherExpenseCents;
    result.profitCents += item.profitCents;
    result.promotionExpenseCents += item.promotionExpenseCents;
    return result;
  }, emptyMetrics());
  total.grossMarginBps = total.netSalesCents === 0 ? 0 : Math.round(total.grossProfitCents / total.netSalesCents * 10_000);
  total.returnRateBps = total.grossSalesCents === 0 ? 0 : Math.round(Math.abs(total.returnAmountCents) / Math.abs(total.grossSalesCents) * 10_000);
  total.smallMarginBps = total.netSalesCents === 0 ? 0 : Math.round(total.smallProfitCents / total.netSalesCents * 10_000);
  total.profitMarginBps = total.netSalesCents === 0 ? 0 : Math.round(total.profitCents / total.netSalesCents * 10_000);
  total.promotionFeeRatioBps = total.netSalesCents === 0 ? 0 : Math.round(total.promotionExpenseCents / total.netSalesCents * 10_000);
  return total;
}

function mapTarget(row: TargetRow): FinanceTarget {
  return {
    id: row.id,
    periodType: row.period_type,
    periodKey: row.period_key,
    platform: row.platform,
    shopName: row.shop_name,
    category: row.category,
    manager: row.manager,
    salesTargetCents: Number(row.sales_target_cents),
    profitTargetCents: Number(row.profit_target_cents),
    grossMarginBps: 0,
    smallMarginBps: Number(row.small_margin_bps),
    inventoryCleanupTargetCents: Number(row.inventory_cleanup_target_cents),
    promotionFeeRatioBps: Number(row.promotion_fee_ratio_bps),
    stagnantInventoryTargetCents: Number(row.stagnant_inventory_target_cents),
    version: Number(row.version || 1),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function aggregateTargets(targets: FinanceTarget[]): FinanceTargetTotals {
  const salesTargetCents = targets.reduce((sum, item) => sum + item.salesTargetCents, 0);
  const weightedRate = (key: "smallMarginBps" | "promotionFeeRatioBps") => {
    const weighted = targets.reduce((sum, item) => sum + item[key] * Math.max(0, item.salesTargetCents), 0);
    if (salesTargetCents > 0) return Math.round(weighted / salesTargetCents);
    const rates = targets.map((item) => item[key]).filter((value) => value !== 0);
    return rates.length ? Math.round(rates.reduce((sum, value) => sum + value, 0) / rates.length) : 0;
  };
  return {
    salesTargetCents,
    profitTargetCents: targets.reduce((sum, item) => sum + item.profitTargetCents, 0),
    smallMarginBps: weightedRate("smallMarginBps"),
    inventoryCleanupTargetCents: targets.reduce((sum, item) => sum + item.inventoryCleanupTargetCents, 0),
    promotionFeeRatioBps: weightedRate("promotionFeeRatioBps"),
    stagnantInventoryTargetCents: targets.reduce((sum, item) => sum + item.stagnantInventoryTargetCents, 0),
    targetCount: targets.length,
  };
}

function mapTargetAggregate(row: TargetAggregateRow | undefined): FinanceTargetTotals {
  if (!row) return aggregateTargets([]);
  return {
    salesTargetCents: Number(row.sales_target_cents ?? 0),
    profitTargetCents: Number(row.profit_target_cents ?? 0),
    smallMarginBps: Number(row.small_margin_bps ?? 0),
    inventoryCleanupTargetCents: Number(row.inventory_cleanup_target_cents ?? 0),
    promotionFeeRatioBps: Number(row.promotion_fee_ratio_bps ?? 0),
    stagnantInventoryTargetCents: Number(row.stagnant_inventory_target_cents ?? 0),
    targetCount: Number(row.target_count ?? 0),
  };
}

function progress(actual: FinanceActualMetrics, target: FinanceTargetTotals) {
  return {
    sales: target.salesTargetCents > 0 ? actual.netSalesCents / target.salesTargetCents : null,
    profit: target.profitTargetCents > 0 ? actual.profitCents / target.profitTargetCents : null,
    smallMarginGapBps: target.smallMarginBps > 0 ? actual.smallMarginBps - target.smallMarginBps : null,
    promotionFeeGapBps: target.promotionFeeRatioBps > 0 ? actual.promotionFeeRatioBps - target.promotionFeeRatioBps : null,
  };
}

export async function getFinanceAnalysis(db: FinanceDatabase, options: FinanceAnalysisOptions = {}) {
  const requestedMonths = [...new Set(options.requestedMonths ?? [])].sort();
  if (requestedMonths.length > MAX_FINANCE_ANALYSIS_MONTHS) {
    throw new PublicApiError(400, "invalid_request", `单次最多分析 ${MAX_FINANCE_ANALYSIS_MONTHS} 个财务月份。`);
  }
  if (requestedMonths.some((month) => !/^\d{4}-(0[1-9]|1[0-2])$/.test(month))) {
    throw new PublicApiError(400, "invalid_request", "财务月份必须使用 YYYY-MM。");
  }
  const [monthResult, monthCount, requestedMonthResult] = await Promise.all([
    db.prepare(
      `SELECT month, source_file_name, imported_at, shop_count, subject_count
       FROM finance_months WHERE status = 'completed'
       ORDER BY month DESC LIMIT ?`,
    ).bind(MAX_FINANCE_MONTH_OPTIONS + 1).all<FinanceMonthRow>(),
    db.prepare("SELECT COUNT(*) AS total FROM finance_months WHERE status = 'completed'")
      .first<{ total: number }>(),
    requestedMonths.length
      ? db.prepare(`SELECT month, source_file_name, imported_at, shop_count, subject_count
          FROM finance_months WHERE status = 'completed'
            AND month IN (SELECT CAST(value AS TEXT) FROM json_each(?))`)
        .bind(JSON.stringify(requestedMonths)).all<FinanceMonthRow>()
      : Promise.resolve({ results: [] as FinanceMonthRow[] }),
  ]);
  const monthByKey = new Map<string, FinanceMonthRow>();
  monthResult.results.slice(0, MAX_FINANCE_MONTH_OPTIONS).forEach((row) => monthByKey.set(row.month, row));
  requestedMonthResult.results.forEach((row) => monthByKey.set(row.month, row));
  const months = [...monthByKey.values()].sort((left, right) => left.month.localeCompare(right.month));
  if (months.length === 0) {
    if (requestedMonths.length && !options.fallbackToLatestCompletedMonth) {
      throw new PublicApiError(400, "invalid_request", `以下财务月份尚未导入：${requestedMonths.join("、")}`);
    }
    return {
      hasData: false,
      months: [],
      selectedMonth: null,
      selectedMonths: [],
      anomalies: [],
      expenses: [],
      shops: [],
      timeline: [],
      filters: { platforms: [], shops: [] },
    };
  }
  const monthKeys = months.map((item) => item.month);
  const missingMonths = requestedMonths.filter((month) => !monthKeys.includes(month));
  const fallbackApplied = Boolean(options.fallbackToLatestCompletedMonth && requestedMonths.length && missingMonths.length);
  if (missingMonths.length && !fallbackApplied) {
    throw new PublicApiError(400, "invalid_request", `以下财务月份尚未导入：${missingMonths.join("、")}`);
  }
  const selectedMonths = fallbackApplied
    ? [monthKeys.at(-1)!]
    : options.allMonths
    ? monthKeys.slice(-MAX_FINANCE_ANALYSIS_MONTHS)
    : requestedMonths.length > 0
      ? requestedMonths
      : [monthKeys.at(-1)!];
  const selectedMonth = selectedMonths.at(-1)!;
  const firstSelectedMonth = selectedMonths[0];
  const previousPeriodMonths = Array.from(
    { length: selectedMonths.length },
    (_, index) => shiftMonth(firstSelectedMonth, index - selectedMonths.length),
  );
  const yearAgoMonths = selectedMonths.map((month) => shiftMonth(month, -12));
  const previousPeriodAvailable = previousPeriodMonths.every((month) => monthKeys.includes(month));
  const yearAgoPeriodAvailable = yearAgoMonths.every((month) => monthKeys.includes(month));
  const selectedYear = selectedMonth.slice(0, 4);
  const yearMonths = monthKeys.filter((month) => month.startsWith(`${selectedYear}-`) && month <= selectedMonth);
  const timelineMonths = selectedMonths.length === 1
    ? monthKeys.filter((month) => month <= selectedMonth).slice(-MAX_FINANCE_ANALYSIS_MONTHS)
    : selectedMonths;
  const queryMonths = [...new Set([
    ...selectedMonths,
    ...(previousPeriodAvailable ? previousPeriodMonths : []),
    ...(yearAgoPeriodAvailable ? yearAgoMonths : []),
    ...yearMonths,
    ...timelineMonths,
  ])].sort();
  const placeholders = queryMonths.map(() => "?").join(",");
  const requestedPlatforms = normalizedRequestedNames(options.platformNames, "平台");
  const requestedShopKeys = options.shopKeys ?? [];
  const requestedShopPairs = requestedShopKeys.map((value) => parseFinanceShopIdentityKey(value));
  if (requestedShopPairs.some((item) => item === null)) {
    throw new PublicApiError(400, "invalid_request", "店铺筛选必须使用平台与店铺组成的稳定复合标识。");
  }
  const requestedShopPairValues = requestedShopPairs.filter((item): item is FinanceShopOption => item !== null);
  const [shopOptionResult, shopOptionCount, platformOptionResult, platformOptionCount, requestedShopResult, requestedPlatformResult] = await Promise.all([
    db.prepare(`SELECT scope_name, COALESCE(NULLIF(group_name, ''), '未分组') AS group_name
      FROM finance_lines
      WHERE section = 'summary' AND scope_type = 'shop' AND scope_name <> ''
        AND month IN (${placeholders})
      GROUP BY COALESCE(NULLIF(group_name, ''), '未分组'), scope_name
      ORDER BY group_name COLLATE NOCASE, scope_name COLLATE NOCASE LIMIT ?`)
      .bind(...queryMonths, MAX_FINANCE_SHOP_OPTIONS + 1).all<{ scope_name: string; group_name: string }>(),
    db.prepare(`SELECT COUNT(*) AS total FROM (
      SELECT COALESCE(NULLIF(group_name, ''), '未分组'), scope_name FROM finance_lines
      WHERE section = 'summary' AND scope_type = 'shop' AND scope_name <> ''
        AND month IN (${placeholders})
      GROUP BY COALESCE(NULLIF(group_name, ''), '未分组'), scope_name
    )`).bind(...queryMonths).first<{ total: number }>(),
    db.prepare(`SELECT DISTINCT COALESCE(NULLIF(group_name, ''), '未分组') AS group_name
      FROM finance_lines
      WHERE section = 'summary' AND scope_type = 'shop' AND month IN (${placeholders})
      ORDER BY group_name COLLATE NOCASE LIMIT ?`)
      .bind(...queryMonths, MAX_FINANCE_PLATFORM_OPTIONS + 1).all<{ group_name: string }>(),
    db.prepare(`SELECT COUNT(*) AS total FROM (
      SELECT COALESCE(NULLIF(group_name, ''), '未分组')
      FROM finance_lines
      WHERE section = 'summary' AND scope_type = 'shop' AND month IN (${placeholders})
      GROUP BY COALESCE(NULLIF(group_name, ''), '未分组')
    )`).bind(...queryMonths).first<{ total: number }>(),
    requestedShopPairValues.length
      ? db.prepare(`SELECT scope_name, COALESCE(NULLIF(group_name, ''), '未分组') AS group_name
          FROM finance_lines
          WHERE section = 'summary' AND scope_type = 'shop' AND month IN (${placeholders})
            AND EXISTS (SELECT 1 FROM json_each(?) selected_shop
              WHERE json_extract(selected_shop.value, '$.platform') = COALESCE(NULLIF(group_name, ''), '未分组')
                AND json_extract(selected_shop.value, '$.name') = scope_name)
          GROUP BY COALESCE(NULLIF(group_name, ''), '未分组'), scope_name`)
        .bind(...queryMonths, JSON.stringify(requestedShopPairValues)).all<{ scope_name: string; group_name: string }>()
      : Promise.resolve({ results: [] as Array<{ scope_name: string; group_name: string }> }),
    requestedPlatforms.length
      ? db.prepare(`SELECT DISTINCT COALESCE(NULLIF(group_name, ''), '未分组') AS group_name
          FROM finance_lines
          WHERE section = 'summary' AND scope_type = 'shop' AND month IN (${placeholders})
            AND COALESCE(NULLIF(group_name, ''), '未分组') IN (SELECT CAST(value AS TEXT) FROM json_each(?))`)
        .bind(...queryMonths, JSON.stringify(requestedPlatforms)).all<{ group_name: string }>()
      : Promise.resolve({ results: [] as Array<{ group_name: string }> }),
  ]);
  const knownDimensionOptions = [
    ...shopOptionResult.results,
    ...requestedShopResult.results,
  ].filter((row) => isSelectableShopName(row.scope_name)).map((row) => {
    const platform = row.group_name || "未分组";
    return { key: financeShopIdentityKey(platform, row.scope_name), name: row.scope_name, platform };
  });
  requestedPlatformResult.results.forEach((row, index) => knownDimensionOptions.push({
    key: financeShopIdentityKey(row.group_name, `__platform_${index}`),
    name: `__platform_${index}`,
    platform: row.group_name,
  }));
  const { shopFilter, shopPairs, platformFilter, hasDimensionFilter } = resolveFinanceDimensionFilters(
    knownDimensionOptions,
    requestedPlatforms,
    requestedShopKeys,
  );
  const dimensionConditions = hasDimensionFilter ? ["scope_type = 'shop'"] : ["scope_type = 'business'"];
  const dimensionBindings: unknown[] = [];
  if (shopFilter.size) {
    dimensionConditions.push(`EXISTS (SELECT 1 FROM json_each(?) selected_shop
      WHERE json_extract(selected_shop.value, '$.platform') = COALESCE(NULLIF(group_name, ''), '未分组')
        AND json_extract(selected_shop.value, '$.name') = scope_name)`);
    dimensionBindings.push(JSON.stringify(shopPairs));
  }
  if (platformFilter.size) {
    dimensionConditions.push("COALESCE(NULLIF(group_name, ''), '未分组') IN (SELECT CAST(value AS TEXT) FROM json_each(?))");
    dimensionBindings.push(JSON.stringify([...platformFilter]));
  }
  const dimensionWhere = dimensionConditions.join(" AND ");
  const [summaryResult, promotionResult, shopRankingResult] = await Promise.all([
    db.prepare(
    `SELECT month, metric_key, MAX(subject_name) AS subject_name,
            'business' AS scope_type, '' AS scope_name, '' AS group_name,
            COALESCE(SUM(amount_cents), 0) AS amount_cents,
            NULL AS rate_bps, MIN(sort_order) AS sort_order
     FROM finance_lines
     WHERE section = 'summary' AND ${dimensionWhere}
       AND metric_key IN (${metricKeys.map(() => "?").join(",")})
       AND month IN (${placeholders})
     GROUP BY month, metric_key
     ORDER BY month, sort_order`,
    ).bind(...dimensionBindings, ...metricKeys, ...queryMonths).all<FinanceLineRow>(),
    db.prepare(
    `SELECT month, 'business' AS scope_type, '' AS scope_name, '' AS group_name,
            COALESCE(SUM(amount_cents), 0) AS amount_cents
     FROM finance_lines
     WHERE section = 'kingdee' AND ${dimensionWhere}
       AND subject_name LIKE '销售费用_推广费用_%'
       AND month IN (${placeholders})
     GROUP BY month`,
    ).bind(...dimensionBindings, ...queryMonths).all<{
      month: string;
      scope_type: "business" | "shop";
      scope_name: string;
      group_name: string;
      amount_cents: number;
    }>(),
    db.prepare(`SELECT scope_name, COALESCE(NULLIF(group_name, ''), '未分组') AS group_name,
        COALESCE(SUM(amount_cents), 0) AS net_sales_cents
      FROM finance_lines
      WHERE section = 'summary' AND scope_type = 'shop' AND metric_key = 'net_sales'
        AND month IN (${selectedMonths.map(() => "?").join(",")})
        ${shopFilter.size ? `AND EXISTS (SELECT 1 FROM json_each(?) selected_shop
          WHERE json_extract(selected_shop.value, '$.platform') = COALESCE(NULLIF(group_name, ''), '未分组')
            AND json_extract(selected_shop.value, '$.name') = scope_name)` : ""}
        ${platformFilter.size ? "AND COALESCE(NULLIF(group_name, ''), '未分组') IN (SELECT CAST(value AS TEXT) FROM json_each(?))" : ""}
      GROUP BY COALESCE(NULLIF(group_name, ''), '未分组'), scope_name
      ORDER BY net_sales_cents DESC, group_name COLLATE NOCASE, scope_name COLLATE NOCASE
      LIMIT ?`)
      .bind(
        ...selectedMonths,
        ...(shopFilter.size ? [JSON.stringify(shopPairs)] : []),
        ...(platformFilter.size ? [JSON.stringify([...platformFilter])] : []),
        MAX_FINANCE_SHOP_OPTIONS + 1,
      ).all<{ scope_name: string; group_name: string; net_sales_cents: number }>(),
  ]);

  const shopOptionsByKey = new Map<string, FinanceShopOption>();
  shopOptionResult.results.slice(0, MAX_FINANCE_SHOP_OPTIONS).forEach((row) => {
    if (!isSelectableShopName(row.scope_name)) return;
    const platform = row.group_name || "未分组";
    const key = financeShopIdentityKey(platform, row.scope_name);
    shopOptionsByKey.set(key, { key, name: row.scope_name, platform });
  });
  const allShopOptions = shopRankingResult.results.slice(0, MAX_FINANCE_SHOP_OPTIONS)
    .filter((row) => isSelectableShopName(row.scope_name))
    .map((row) => {
      const platform = row.group_name || "未分组";
      return { key: financeShopIdentityKey(platform, row.scope_name), name: row.scope_name, platform };
    });
  const platforms = platformOptionResult.results.slice(0, MAX_FINANCE_PLATFORM_OPTIONS).map((row) => row.group_name);
  requestedShopResult.results.forEach((row) => {
    if (!isSelectableShopName(row.scope_name)) return;
    const platform = row.group_name || "未分组";
    const key = financeShopIdentityKey(platform, row.scope_name);
    shopOptionsByKey.set(key, { key, name: row.scope_name, platform });
  });
  const shopOptions = [...shopOptionsByKey.values()].sort((left, right) => (
    left.platform.localeCompare(right.platform, "zh-CN") || left.name.localeCompare(right.name, "zh-CN")
  ));
  const matchesShop = (shopName: string, platformName: string) => (
    (shopFilter.size === 0 || shopFilter.has(financeShopIdentityKey(platformName || "未分组", shopName)))
    && (platformFilter.size === 0 || platformFilter.has(platformName || "未分组"))
  );

  const rankedShopPairs = allShopOptions.map((shop) => ({ platform: shop.platform, name: shop.name }));
  const [rankedShopSummary, rankedShopPromotions] = rankedShopPairs.length
    ? await Promise.all([
      db.prepare(`SELECT month, metric_key, subject_name, scope_type, scope_name, group_name,
            amount_cents, rate_bps, sort_order
        FROM finance_lines
        WHERE section = 'summary' AND scope_type = 'shop'
          AND metric_key IN (${metricKeys.map(() => "?").join(",")})
          AND month IN (${selectedMonths.map(() => "?").join(",")})
          AND EXISTS (SELECT 1 FROM json_each(?) selected_shop
            WHERE json_extract(selected_shop.value, '$.platform') = COALESCE(NULLIF(group_name, ''), '未分组')
              AND json_extract(selected_shop.value, '$.name') = scope_name)
        ORDER BY month, group_name, scope_name, sort_order`)
        .bind(...metricKeys, ...selectedMonths, JSON.stringify(rankedShopPairs)).all<FinanceLineRow>(),
      db.prepare(`SELECT month, scope_type, scope_name, group_name,
            COALESCE(SUM(amount_cents), 0) AS amount_cents
        FROM finance_lines
        WHERE section = 'kingdee' AND scope_type = 'shop'
          AND subject_name LIKE '销售费用_推广费用_%'
          AND month IN (${selectedMonths.map(() => "?").join(",")})
          AND EXISTS (SELECT 1 FROM json_each(?) selected_shop
            WHERE json_extract(selected_shop.value, '$.platform') = COALESCE(NULLIF(group_name, ''), '未分组')
              AND json_extract(selected_shop.value, '$.name') = scope_name)
        GROUP BY month, scope_name, group_name`)
        .bind(...selectedMonths, JSON.stringify(rankedShopPairs)).all<{
          month: string; scope_type: "shop"; scope_name: string; group_name: string; amount_cents: number;
        }>(),
    ])
    : [{ results: [] as FinanceLineRow[] }, { results: [] as Array<{ month: string; scope_type: "shop"; scope_name: string; group_name: string; amount_cents: number }> }];

  const businessRowsByMonth = new Map<string, FinanceLineRow[]>();
  const shopRowsByMonthAndName = new Map<string, FinanceLineRow[]>();
  [...summaryResult.results, ...rankedShopSummary.results].forEach((row) => {
    if (row.scope_type === "business") {
      const items = businessRowsByMonth.get(row.month) ?? [];
      items.push(row);
      businessRowsByMonth.set(row.month, items);
      return;
    }
    const key = `${row.month}\u0000${row.group_name || "未分组"}\u0000${row.scope_name}`;
    const items = shopRowsByMonthAndName.get(key) ?? [];
    items.push(row);
    shopRowsByMonthAndName.set(key, items);
  });

  const businessPromotions = new Map<string, number>();
  const shopPromotions = new Map<string, number>();
  [...promotionResult.results, ...rankedShopPromotions.results].forEach((row) => {
    if (row.scope_type === "business") {
      businessPromotions.set(row.month, (businessPromotions.get(row.month) ?? 0) + Number(row.amount_cents));
      return;
    }
    const key = `${row.month}\u0000${row.group_name || "未分组"}\u0000${row.scope_name}`;
    shopPromotions.set(key, (shopPromotions.get(key) ?? 0) + Number(row.amount_cents));
  });

  const shopActualByMonthAndName = new Map<string, FinanceActualMetrics>();
  shopRowsByMonthAndName.forEach((rows, key) => {
    shopActualByMonthAndName.set(key, metricsFromRows(rows, shopPromotions.get(key) ?? 0));
  });

  const actualByMonth = new Map<string, FinanceActualMetrics>();
  queryMonths.forEach((month) => {
    actualByMonth.set(month, metricsFromRows(businessRowsByMonth.get(month) ?? [], businessPromotions.get(month) ?? 0));
  });

  const sumPeriod = (periodMonths: string[]) => sumMetrics(periodMonths.map((month) => actualByMonth.get(month) ?? emptyMetrics()));
  const current = sumPeriod(selectedMonths);
  const previous = previousPeriodAvailable ? sumPeriod(previousPeriodMonths) : null;
  const yearAgo = yearAgoPeriodAvailable ? sumPeriod(yearAgoMonths) : null;
  const selectedYears = [...new Set(selectedMonths.map((month) => month.slice(0, 4)))];
  const yearToDate = sumPeriod(yearMonths);

  const monthTargetPlaceholders = selectedMonths.map(() => "?").join(",");
  const yearTargetPlaceholders = selectedYears.map(() => "?").join(",");
  const legacyTargetPlatformSql = `(SELECT MIN(COALESCE(NULLIF(target_scope.group_name, ''), '未分组'))
    FROM finance_lines target_scope
    WHERE target_scope.scope_type = 'shop'
      AND target_scope.scope_name = finance_targets_scoped.shop_name)`;
  const compatibleTargetCondition = `(finance_targets_scoped.platform <> '' OR (
    SELECT COUNT(DISTINCT COALESCE(NULLIF(target_scope.group_name, ''), '未分组'))
    FROM finance_lines target_scope
    WHERE target_scope.scope_type = 'shop'
      AND target_scope.scope_name = finance_targets_scoped.shop_name
  ) = 1)`;
  const targetDimensionConditions: string[] = [];
  const targetDimensionBindings: unknown[] = [];
  if (shopFilter.size) {
    targetDimensionConditions.push(`EXISTS (SELECT 1 FROM json_each(?) selected_shop
      WHERE json_extract(selected_shop.value, '$.name') = finance_targets_scoped.shop_name
        AND json_extract(selected_shop.value, '$.platform') = COALESCE(
          NULLIF(finance_targets_scoped.platform, ''), ${legacyTargetPlatformSql}
        ))`);
    targetDimensionBindings.push(JSON.stringify(shopPairs));
  }
  if (platformFilter.size) {
    targetDimensionConditions.push(`COALESCE(
      NULLIF(finance_targets_scoped.platform, ''), ${legacyTargetPlatformSql}
    ) IN (SELECT CAST(value AS TEXT) FROM json_each(?))`);
    targetDimensionBindings.push(JSON.stringify([...platformFilter]));
  }
  const targetDimensionWhere = ` AND ${compatibleTargetCondition}${targetDimensionConditions.length ? ` AND ${targetDimensionConditions.join(" AND ")}` : ""}`;
  const targetQueryBindings = [...selectedMonths, ...selectedYears, ...targetDimensionBindings];
  const [targetResult, targetCount, targetAggregateResult, projectTargetResult, projectTargetCount, legacyTargetGap] = await Promise.all([
    db.prepare(
    `SELECT id, period_type, period_key, platform, shop_name, category, manager,
             sales_target_cents, profit_target_cents, small_margin_bps,
             inventory_cleanup_target_cents, promotion_fee_ratio_bps,
             stagnant_inventory_target_cents,
             COALESCE((SELECT version FROM finance_target_scoped_versions version_state
               WHERE version_state.target_id = finance_targets_scoped.id), 1) AS version,
             created_at, updated_at
     FROM finance_targets_scoped
     WHERE ((period_type = 'month' AND period_key IN (${monthTargetPlaceholders}))
        OR (period_type = 'year' AND period_key IN (${yearTargetPlaceholders})))
       ${targetDimensionWhere}
     ORDER BY period_type, period_key DESC, platform, shop_name, category
     LIMIT 1001`,
    ).bind(...targetQueryBindings).all<TargetRow>(),
    db.prepare(`SELECT COUNT(*) AS total FROM finance_targets_scoped
      WHERE ((period_type = 'month' AND period_key IN (${monthTargetPlaceholders}))
        OR (period_type = 'year' AND period_key IN (${yearTargetPlaceholders})))
      ${targetDimensionWhere}`)
      .bind(...targetQueryBindings).first<{ total: number }>(),
    db.prepare(`SELECT period_type,
        COALESCE(SUM(sales_target_cents), 0) AS sales_target_cents,
        COALESCE(SUM(profit_target_cents), 0) AS profit_target_cents,
        CASE WHEN SUM(sales_target_cents) > 0
          THEN ROUND(SUM(small_margin_bps * MAX(0, sales_target_cents)) * 1.0 / SUM(sales_target_cents))
          ELSE COALESCE(ROUND(AVG(NULLIF(small_margin_bps, 0))), 0) END AS small_margin_bps,
        COALESCE(SUM(inventory_cleanup_target_cents), 0) AS inventory_cleanup_target_cents,
        CASE WHEN SUM(sales_target_cents) > 0
          THEN ROUND(SUM(promotion_fee_ratio_bps * MAX(0, sales_target_cents)) * 1.0 / SUM(sales_target_cents))
          ELSE COALESCE(ROUND(AVG(NULLIF(promotion_fee_ratio_bps, 0))), 0) END AS promotion_fee_ratio_bps,
        COALESCE(SUM(stagnant_inventory_target_cents), 0) AS stagnant_inventory_target_cents,
        COUNT(*) AS target_count
      FROM finance_targets_scoped
      WHERE ((period_type = 'month' AND period_key IN (${monthTargetPlaceholders}))
        OR (period_type = 'year' AND period_key IN (${yearTargetPlaceholders})))
        ${targetDimensionWhere}
      GROUP BY period_type`)
      .bind(...targetQueryBindings).all<TargetAggregateRow>(),
    db.prepare(
      `SELECT id, period_type, period_key, platform, shop_name, category, manager,
              sales_target_cents, profit_target_cents, small_margin_bps,
              inventory_cleanup_target_cents, promotion_fee_ratio_bps,
              stagnant_inventory_target_cents,
              COALESCE((SELECT version FROM finance_target_scoped_versions version_state
                WHERE version_state.target_id = finance_targets_scoped.id), 1) AS version,
              created_at, updated_at
       FROM finance_targets_scoped
       WHERE period_type = 'project'
       ORDER BY updated_at DESC, id DESC
       LIMIT 100`,
    ).all<TargetRow>(),
    db.prepare("SELECT COUNT(*) AS total FROM finance_targets_scoped WHERE period_type = 'project'")
      .first<{ total: number }>(),
    db.prepare(`SELECT COUNT(*) AS total FROM finance_targets_scoped
      WHERE platform = ''
        AND ((period_type = 'month' AND period_key IN (${monthTargetPlaceholders}))
          OR (period_type = 'year' AND period_key IN (${yearTargetPlaceholders})))
        AND (SELECT COUNT(DISTINCT COALESCE(NULLIF(target_scope.group_name, ''), '未分组'))
          FROM finance_lines target_scope
          WHERE target_scope.scope_type = 'shop'
            AND target_scope.scope_name = finance_targets_scoped.shop_name) <> 1`)
      .bind(...selectedMonths, ...selectedYears).first<{ total: number }>(),
  ]);
  const targets = targetResult.results.slice(0, 1_000).map(mapTarget);
  const projectTargets = projectTargetResult.results.map(mapTarget);
  const monthTargets = mapTargetAggregate(targetAggregateResult.results.find((row) => row.period_type === "month"));
  const yearTargets = mapTargetAggregate(targetAggregateResult.results.find((row) => row.period_type === "year"));

  const [expenseNameResult, expenseSubjectCount] = await Promise.all([
    db.prepare(`SELECT subject_name, ABS(COALESCE(SUM(amount_cents), 0)) AS magnitude
      FROM finance_lines
      WHERE section = 'kingdee' AND ${dimensionWhere}
        AND is_total = 0 AND month IN (${selectedMonths.map(() => "?").join(",")})
      GROUP BY subject_name
      ORDER BY magnitude DESC, subject_name COLLATE NOCASE
      LIMIT 501`)
      .bind(...dimensionBindings, ...selectedMonths).all<{ subject_name: string; magnitude: number }>(),
    db.prepare(`SELECT COUNT(DISTINCT subject_name) AS total
      FROM finance_lines
      WHERE section = 'kingdee' AND ${dimensionWhere}
        AND is_total = 0 AND month IN (${selectedMonths.map(() => "?").join(",")})`)
      .bind(...dimensionBindings, ...selectedMonths).first<{ total: number }>(),
  ]);
  const expenseSubjectNames = expenseNameResult.results.slice(0, 500).map((row) => row.subject_name);
  const expenseResult = expenseSubjectNames.length
    ? await db.prepare(
      `SELECT month, '' AS metric_key, subject_name,
              ${hasDimensionFilter ? "'shop'" : "'business'"} AS scope_type,
              '' AS scope_name, '' AS group_name,
              COALESCE(SUM(amount_cents), 0) AS amount_cents,
              NULL AS rate_bps, MIN(sort_order) AS sort_order
       FROM finance_lines
       WHERE section = 'kingdee' AND ${dimensionWhere}
         AND is_total = 0 AND month IN (${placeholders})
         AND subject_name IN (SELECT CAST(value AS TEXT) FROM json_each(?))
       GROUP BY month, subject_name
       ORDER BY month, sort_order`,
    ).bind(...dimensionBindings, ...queryMonths, JSON.stringify(expenseSubjectNames)).all<FinanceLineRow>()
    : { results: [] as FinanceLineRow[] };
  const expenseTotals = (periodMonths: string[]) => {
    const period = new Set(periodMonths);
    const totals = new Map<string, { amount: number; sortOrder: number }>();
    expenseResult.results.forEach((row) => {
      if (!period.has(row.month)) return;
      const existing = totals.get(row.subject_name) ?? { amount: 0, sortOrder: row.sort_order };
      existing.amount += Number(row.amount_cents ?? 0);
      existing.sortOrder = Math.min(existing.sortOrder, row.sort_order);
      totals.set(row.subject_name, existing);
    });
    return totals;
  };
  const currentExpenses = expenseTotals(selectedMonths);
  const previousExpenses = previousPeriodAvailable ? expenseTotals(previousPeriodMonths) : null;
  const yearAgoExpenses = yearAgoPeriodAvailable ? expenseTotals(yearAgoMonths) : null;
  const expenseNames = new Set([
    ...currentExpenses.keys(),
    ...(previousExpenses?.keys() ?? []),
    ...(yearAgoExpenses?.keys() ?? []),
  ]);
  const allExpenses = [...expenseNames].map((name) => {
    const currentAmount = currentExpenses.get(name)?.amount ?? 0;
    const previousAmount = previousExpenses ? previousExpenses.get(name)?.amount ?? 0 : null;
    const yearAgoAmount = yearAgoExpenses ? yearAgoExpenses.get(name)?.amount ?? 0 : null;
    const momRate = changeRate(currentAmount, previousAmount);
    return {
      name,
      current: currentAmount,
      previous: previousAmount,
      yearAgo: yearAgoAmount,
      sortOrder: currentExpenses.get(name)?.sortOrder ?? previousExpenses?.get(name)?.sortOrder ?? yearAgoExpenses?.get(name)?.sortOrder ?? 0,
      feeRateBps: current.netSalesCents === 0 ? 0 : Math.round(currentAmount / current.netSalesCents * 10_000),
      yearAgoFeeRateBps: yearAgoExpenses && yearAgo && yearAgo.netSalesCents !== 0
        ? Math.round(yearAgoAmount! / yearAgo.netSalesCents * 10_000)
        : null,
      momRate,
      yoyRate: changeRate(currentAmount, yearAgoAmount),
      abnormal: previousAmount !== null
        && Math.abs(currentAmount - previousAmount) >= 100_000
        && Math.abs(momRate ?? 0) >= 0.3,
    };
  })
    .filter((item) => item.current !== 0 || item.previous !== null || item.yearAgo !== null)
    .sort((left, right) => Math.abs(right.current) - Math.abs(left.current));
  const expenses = allExpenses;

  const shops = allShopOptions.filter((shop) => matchesShop(shop.name, shop.platform)).map((shop) => {
    const actuals = selectedMonths
      .map((month) => shopActualByMonthAndName.get(`${month}\u0000${shop.platform}\u0000${shop.name}`))
      .filter((item): item is FinanceActualMetrics => Boolean(item));
    const actual = sumMetrics(actuals);
    const targetRows = targets.filter((target) => target.periodType === "month"
      && selectedMonths.includes(target.periodKey)
      && target.shopName === shop.name
      && (target.platform === shop.platform || target.platform === ""));
    const target = aggregateTargets(targetRows);
    return {
      name: shop.name,
      key: shop.key,
      groupName: shop.platform,
      manager: targetRows.find((item) => item.manager)?.manager ?? "",
      actual,
      target,
      progress: progress(actual, target),
    };
  }).filter((shop) => shop.actual.netSalesCents !== 0 || shop.actual.grossSalesCents !== 0 || shop.target.targetCount > 0)
    .sort((left, right) => right.actual.netSalesCents - left.actual.netSalesCents)
    .slice(0, MAX_FINANCE_SHOP_OPTIONS);

  const anomalies: Array<{ level: "critical" | "warning" | "info"; title: string; detail: string }> = [];
  if (current.profitCents < 0) anomalies.push({ level: "critical", title: "所选期间利润为负", detail: "建议优先检查销售费用、退货和异常成本科目。" });
  if (monthTargets.smallMarginBps > 0 && current.smallMarginBps < monthTargets.smallMarginBps) {
    anomalies.push({ level: "warning", title: "小毛利率低于目标", detail: `低于目标 ${((monthTargets.smallMarginBps - current.smallMarginBps) / 100).toFixed(1)} 个百分点。` });
  }
  const monthProgress = progress(current, monthTargets);
  if (monthProgress.sales !== null && monthProgress.sales < 0.9) {
    anomalies.push({ level: "warning", title: "销售目标完成度偏低", detail: `所选期间销售目标完成 ${(monthProgress.sales * 100).toFixed(1)}%。` });
  }
  expenses.filter((item) => item.abnormal && item.momRate !== null).slice(0, 5).forEach((item) => {
    anomalies.push({
      level: item.momRate! > 0 ? "warning" : "info",
      title: `${item.name.replace(/^销售费用_/, "")}环比${item.momRate! > 0 ? "上升" : "下降"}`,
      detail: `较上月${item.momRate! > 0 ? "增加" : "减少"} ${(Math.abs(item.momRate!) * 100).toFixed(1)}%。`,
    });
  });
  if (anomalies.length === 0) anomalies.push({ level: "info", title: "暂未发现明显异常", detail: "当前月份利润、目标进度与费用波动均在规则阈值内。" });

  const selectedMonthMeta = months.find((item) => item.month === selectedMonth)!;
  const periodLabel = selectedMonths.length === 1
    ? selectedMonth
    : `${selectedMonths[0]} 至 ${selectedMonths.at(-1)}（${selectedMonths.length}个月）`;
  return {
    hasData: true,
    months: months.map((item) => ({
      month: item.month,
      fileName: item.source_file_name,
      importedAt: item.imported_at,
      shopCount: Number(item.shop_count),
      subjectCount: Number(item.subject_count),
    })),
    monthPagination: {
      total: Number(monthCount?.total ?? 0),
      returned: months.length,
      truncated: monthResult.results.length > MAX_FINANCE_MONTH_OPTIONS,
    },
    selectedMonth,
    selectedMonths,
    periodLabel,
    previousMonth: previousPeriodAvailable && previousPeriodMonths.length === 1 ? previousPeriodMonths[0] : null,
    previousMonths: previousPeriodAvailable ? previousPeriodMonths : [],
    yearAgoMonth: yearAgoPeriodAvailable && yearAgoMonths.length === 1 ? yearAgoMonths[0] : null,
    yearAgoMonths: yearAgoPeriodAvailable ? yearAgoMonths : [],
    current,
    previous,
    yearAgo,
    yearToDate,
    timeline: timelineMonths.map((month) => ({ month, ...(actualByMonth.get(month) ?? emptyMetrics()) })),
    targets: {
      month: monthTargets,
      year: yearTargets,
      projects: projectTargets,
      projectPagination: {
        total: Number(projectTargetCount?.total ?? 0),
        returned: projectTargets.length,
        truncated: projectTargets.length < Number(projectTargetCount?.total ?? 0),
      },
      periodPagination: {
        total: Number(targetCount?.total ?? 0),
        returned: targets.length,
        truncated: targetResult.results.length > 1_000,
      },
      legacyCompatibility: {
        excluded: Number(legacyTargetGap?.total ?? 0),
        reason: "旧目标缺少平台，仅在全部财务数据中店铺名称只属于一个平台时兼容；跨平台同名目标已停止参与 KPI。",
      },
    },
    progress: { month: monthProgress, year: progress(yearToDate, yearTargets) },
    expenses,
    expensePagination: {
      total: Number(expenseSubjectCount?.total ?? 0),
      returned: expenses.length,
      truncated: expenseNameResult.results.length > 500,
    },
    shops,
    shopPagination: {
      total: Number(shopOptionCount?.total ?? 0),
      returned: shops.length,
      truncated: shopRankingResult.results.length > MAX_FINANCE_SHOP_OPTIONS,
    },
    anomalies,
    filters: {
      platforms,
      shops: shopOptions,
      pagination: {
        platforms: {
          total: Number(platformOptionCount?.total ?? 0),
          returned: platforms.length,
          truncated: platforms.length < Number(platformOptionCount?.total ?? 0),
        },
        shops: {
          total: Number(shopOptionCount?.total ?? 0),
          returned: shopOptions.length,
          truncated: shopOptions.length < Number(shopOptionCount?.total ?? 0),
        },
      },
    },
    selection: {
      allMonths: Boolean(options.allMonths),
      truncated: Boolean(options.allMonths && monthKeys.length > MAX_FINANCE_ANALYSIS_MONTHS),
      availableMonthCount: Number(monthCount?.total ?? monthKeys.length),
      months: selectedMonths,
      requestedMonths,
      fallbackApplied,
      platforms: [...platformFilter],
      shops: [...shopFilter],
    },
    sync: {
      dataCutoffMonth: selectedMonth,
      sourceFileName: selectedMonthMeta.source_file_name,
      importedAt: selectedMonthMeta.imported_at,
    },
  };
}
