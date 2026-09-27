"use client";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useRef, useState } from "react";
import { requestJson } from "@/lib/http/api-client";
import Dialog from "./ui/dialog";
import type { SalesSharedFilters } from "./sales-filter-bar";

type CategoryMetric = {
  category: string;
  grossSalesCents: number;
  refundAmountCents: number;
  netSalesCents: number;
  costAmountCents: number;
  shareRate: number;
  positiveQuantity: number;
  returnQuantity: number;
  netQuantity: number;
  refundRate: number;
  grossProfitCents: number;
  grossMarginRate: number;
  productCount: number;
  lineCount: number;
  currentWeekNetSalesCents: number;
  previousWeekNetSalesCents: number;
  yearAgoNetSalesCents: number;
  weekOverWeekRate: number | null;
  yearOverYearRate: number | null;
};

type CategoryDetailMetric = CategoryMetric & {
  trend: {
    points: Array<{ period: string; netSalesCents: number }>;
    changeRate: number | null;
    direction: "up" | "down" | "flat" | "insufficient";
  };
};

type CategoryAnalysisResponse = {
  range: { startDate: string; endDate: string; endExclusive: string; timezone: string };
  comparisonPeriods: {
    weekOverWeek: {
      current: { startDate: string; endDate: string };
      previous: { startDate: string; endDate: string };
    };
    yearAgo: { startDate: string; endDate: string };
  };
  dataCutoffDate: string | null;
  categoryHierarchy: {
    currentLevel: 1;
    levels: Array<{ level: 1; key: string; label: string }>;
    supportsDrilldown: boolean;
    source: { primary: string; fallback: string; joinKey: string; unmatchedLabel: string };
  };
  filtersApplied: { dataScope: { mode: "unrestricted" | "restricted" } };
  summary: {
    grossSalesCents: number; refundAmountCents: number; netSalesCents: number;
    positiveQuantity: number; returnQuantity: number; netQuantity: number;
    costAmountCents: number; grossProfitCents: number; grossMarginRate: number; productCount: number;
    lineCount: number; categoryCount: number;
  };
  uncategorized: { category: string; productCount: number; netSalesCents: number; shareRate: number; visible: boolean };
  structure: { items: Array<CategoryMetric & { rank: number }>; otherNetSalesCents: number; otherShareRate: number; contributionRateTotal: number };
  ranking: Array<CategoryMetric & { rank: number }>;
  trend: {
    granularity: CategoryGranularity;
    categoryLimit: number;
    returned: number;
    truncated: boolean;
    items: Array<{ period: string; category: string; netSalesCents: number; grossProfitCents: number; positiveQuantity: number; returnQuantity: number; refundAmountCents: number }>;
  };
  details: {
    items: CategoryDetailMetric[];
    pagination: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
    sort: { by: CategorySortKey; direction: SortDirection };
    trend: { granularity: CategoryGranularity; periodLimit: number };
  };
  filterOptions: {
    categories: string[];
    channels: string[];
    platforms: string[];
    outlets: Array<{ key: string; platform: string; name: string }>;
    totals: { categories: number; channels: number; platforms: number; outlets: number };
    truncated: boolean;
    limit: number;
  };
};

type CategoryOutletMetric = {
  shop: string;
  grossSalesCents: number;
  refundAmountCents: number;
  netSalesCents: number;
  costAmountCents: number;
  shareRate: number;
  positiveQuantity: number;
  returnQuantity: number;
  netQuantity: number;
  refundRate: number;
  grossProfitCents: number;
  grossMarginRate: number;
  lineCount: number;
};

type CategoryOutletBreakdownResponse = {
  range: { startDate: string; endDate: string; endExclusive: string; timezone: string };
  category: string;
  totals: { netSalesCents: number; platformCount: number; shopCount: number };
  platforms: Array<Omit<CategoryOutletMetric, "shop"> & {
    platform: string;
    shopCount: number;
    shops: CategoryOutletMetric[];
  }>;
  pagination: { total: number; returned: number; truncated: boolean; limit: number };
};

type CategoryGranularity = "day" | "week" | "month";
type SortDirection = "asc" | "desc";
type CategorySortKey = "netSalesCents" | "shareRate" | "netQuantity" | "refundRate" | "refundAmountCents" | "grossProfitCents" | "grossMarginRate" | "weekOverWeekRate" | "yearOverYearRate";

type CategoryUrlState = {
  granularity: CategoryGranularity;
  sortBy: CategorySortKey;
  direction: SortDirection;
  page: number;
  pageSize: number;
};

const categoryOwnedUrlKeys = [
  "salesCategoryLevel", "salesGranularity", "salesSort", "salesDirection", "salesPage", "salesPageSize",
] as const;
// The shell owns `module` and `view=category`; this component only serializes its bounded analysis state.
const validGranularities = new Set<CategoryGranularity>(["day", "week", "month"]);
const validSortKeys = new Set<CategorySortKey>(["netSalesCents", "shareRate", "netQuantity", "refundRate", "refundAmountCents", "grossProfitCents", "grossMarginRate", "weekOverWeekRate", "yearOverYearRate"]);
const chartColors = ["#3f7be0", "#29a77a", "#8a65d6", "#e7943f"];

function positiveInteger(value: string | null, fallback: number, maximum: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(maximum, Math.trunc(parsed)) : fallback;
}

function readCategoryUrl(): CategoryUrlState {
  const params = typeof window === "undefined" ? new URLSearchParams() : new URL(window.location.href).searchParams;
  const granularity = params.get("salesGranularity") as CategoryGranularity | null;
  const sortParam = params.get("salesSort");
  const sortBy = (sortParam === "monthOverMonthRate" ? "weekOverWeekRate" : sortParam) as CategorySortKey | null;
  return {
    granularity: granularity && validGranularities.has(granularity) ? granularity : "day",
    sortBy: sortBy && validSortKeys.has(sortBy) ? sortBy : "netSalesCents",
    direction: params.get("salesDirection") === "asc" ? "asc" : "desc",
    page: positiveInteger(params.get("salesPage"), 1, 100_000),
    pageSize: positiveInteger(params.get("salesPageSize"), 20, 100),
  };
}

function writeCategoryUrl(state: CategoryUrlState, mode: "push" | "replace") {
  const url = new URL(window.location.href);
  for (const key of categoryOwnedUrlKeys) url.searchParams.delete(key);
  url.searchParams.set("salesCategoryLevel", "1");
  if (state.granularity !== "day") url.searchParams.set("salesGranularity", state.granularity);
  if (state.sortBy !== "netSalesCents") url.searchParams.set("salesSort", state.sortBy);
  if (state.direction !== "desc") url.searchParams.set("salesDirection", state.direction);
  if (state.page !== 1) url.searchParams.set("salesPage", String(state.page));
  if (state.pageSize !== 20) url.searchParams.set("salesPageSize", String(state.pageSize));
  const next = `${url.pathname}${url.search}${url.hash}`;
  window.history[mode === "push" ? "pushState" : "replaceState"](null, "", next);
}

function formatCurrency(cents: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(cents / 100);
}

function formatCount(value: number) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}

function formatRate(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function formatComparison(value: number | null) {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${formatRate(value)}`;
}

function comparisonTone(value: number | null) {
  return value === null || value === 0 ? "muted-text" : value > 0 ? "green-text" : "red-text";
}

function CategoryDetailTrend({ item }: { item: CategoryDetailMetric }) {
  const values = item.trend.points.map((point) => point.netSalesCents);
  if (values.length === 0) return <span className="category-detail-trend-empty">—</span>;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = Math.max(1, maximum - minimum);
  const x = (index: number) => values.length === 1 ? 52 : 3 + index / (values.length - 1) * 98;
  const y = (value: number) => 25 - (value - minimum) / span * 22;
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const tone = item.trend.direction === "up" ? "up" : item.trend.direction === "down" ? "down" : "flat";
  return <div className={`category-detail-trend ${tone}`} title={`${item.trend.points[0]?.period} 至 ${item.trend.points.at(-1)?.period}：${formatComparison(item.trend.changeRate)}`}>
    <svg viewBox="0 0 104 28" role="img" aria-label={`${item.category}品类趋势 ${formatComparison(item.trend.changeRate)}`}>
      {values.length === 1
        ? <circle cx="52" cy="14" r="2.5" />
        : <polyline points={points} fill="none" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />}
    </svg>
    <small>{formatComparison(item.trend.changeRate)}</small>
  </div>;
}

function MultiFilter({ label, values, selected, onChange, display }: {
  label: string;
  values: string[];
  selected: string[];
  onChange: (values: string[]) => void;
  display?: (value: string) => string;
}) {
  const [query, setQuery] = useState("");
  const matches = values.filter((value) => !query.trim() || (display?.(value) ?? value).toLocaleLowerCase("zh-CN").includes(query.trim().toLocaleLowerCase("zh-CN")));
  return <details className="category-filter-menu">
    <summary><span>{label}</span><strong>{selected.length ? `已选 ${selected.length}` : "全部"}</strong></summary>
    <div className="category-filter-popover">
      <label className="category-filter-search">⌕<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索${label}`} aria-label={`搜索${label}`} /></label>
      <div className="category-filter-options">
        {matches.slice(0, 200).map((value) => <label key={value} className={selected.includes(value) ? "selected" : ""}>
          <input type="checkbox" checked={selected.includes(value)} onChange={() => onChange(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value])} />
          <span title={display?.(value) ?? value}>{display?.(value) ?? value}</span>
        </label>)}
        {matches.length === 0 && <p>没有匹配选项</p>}
      </div>
      {selected.length > 0 && <button type="button" onClick={() => onChange([])}>清空{label}</button>}
    </div>
  </details>;
}

function CategoryOutletDrawer({ category, data, loading, error, onClose }: {
  category: string;
  data: CategoryOutletBreakdownResponse | null;
  loading: boolean;
  error: string;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  return <Dialog
    open
    onClose={onClose}
    dialogId="category-outlet-detail"
    ariaLabel={`${category}平台店铺详情`}
    className="category-outlet-drawer"
    initialFocusRef={closeButtonRef}
  >
      <header>
        <div><span className="eyebrow">PLATFORM &amp; SHOP DETAIL</span><h2 id="category-outlet-title">{category} · 平台店铺详情</h2><p>{data ? `${data.range.startDate} 至 ${data.range.endDate} · ${data.range.timezone}` : "正在读取当前筛选范围"}</p></div>
        <button ref={closeButtonRef} type="button" aria-label="关闭品类详情" onClick={onClose}>×</button>
      </header>
      {loading && !data ? <div className="category-drawer-state" role="status"><span className="state-spinner" /><strong>正在汇总平台与店铺数据</strong></div>
        : error && !data ? <div className="category-drawer-state error" role="alert"><strong>详情加载失败</strong><p>{error}</p></div>
          : data ? <>
            {error && <div className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>详情刷新失败</strong><p>{error}；当前仍显示上一次成功结果。</p></div></div>}
            <section className="category-drawer-summary data-refresh-region" aria-busy={loading}>
              <div><span>品类净销售额</span><strong>{formatCurrency(data.totals.netSalesCents)}</strong></div>
              <div><span>平台</span><strong>{formatCount(data.totals.platformCount)} 个</strong></div>
              <div><span>店铺</span><strong>{formatCount(data.totals.shopCount)} 个</strong></div>
            </section>
            <div className="category-platform-list">
              {data.platforms.map((platform) => <section key={platform.platform} className="category-platform-group">
                <div className="category-platform-heading"><div><strong>{platform.platform}</strong><span>{platform.shopCount} 个店铺 · 贡献 {formatRate(platform.shareRate)}</span></div><div><strong>{formatCurrency(platform.netSalesCents)}</strong><span>毛利率 {formatRate(platform.grossMarginRate)}</span></div></div>
                <div className="data-table-wrap"><table className="data-table category-outlet-table" data-column-filter-scope={data.pagination.truncated ? "none" : "full"}><thead><tr><th>店铺</th><th>净销售额</th><th>贡献率</th><th>净销量</th><th>退款金额</th><th>退货率</th><th>毛利额</th><th>毛利率</th></tr></thead><tbody>
                  {platform.shops.map((shop) => <tr key={`${platform.platform}-${shop.shop}`}><td><strong>{shop.shop}</strong></td><td>{formatCurrency(shop.netSalesCents)}</td><td>{formatRate(shop.shareRate)}</td><td>{formatCount(shop.netQuantity)}</td><td>{formatCurrency(shop.refundAmountCents)}</td><td>{formatRate(shop.refundRate)}</td><td>{formatCurrency(shop.grossProfitCents)}</td><td className={shop.grossMarginRate < 0 ? "orange-text" : "green-text"}>{formatRate(shop.grossMarginRate)}</td></tr>)}
                </tbody></table></div>
              </section>)}
              {data.platforms.length === 0 && <div className="category-drawer-state"><strong>当前筛选下没有平台或店铺数据</strong></div>}
            </div>
            <footer>净销售额包含负值退款；店铺身份按“平台 + 店铺”隔离。{data.pagination.truncated ? `仅展示前 ${data.pagination.limit} 个店铺。` : "已展示全部匹配店铺。"}</footer>
          </> : null}
  </Dialog>;
}

function CategoryTrend({ data }: { data: CategoryAnalysisResponse }) {
  const categories = data.ranking.slice(0, 4).map((item) => item.category);
  const periods = [...new Set(data.trend.items.map((item) => item.period))];
  const visibleValues = data.trend.items.filter((item) => categories.includes(item.category)).map((item) => item.netSalesCents);
  const minimum = Math.min(0, ...visibleValues);
  const maximum = Math.max(0, ...visibleValues);
  const span = Math.max(1, maximum - minimum);
  const x = (index: number) => periods.length <= 1 ? 30 : 30 + index / (periods.length - 1) * 640;
  const y = (value: number) => 30 + (maximum - value) / span * 140;
  const zeroY = y(0);
  return <section className="panel category-trend-panel">
    <div className="category-section-heading"><div><span className="eyebrow">CATEGORY TREND</span><h2>品类统计周期趋势</h2><p>服务端按{data.trend.granularity === "day" ? "日" : data.trend.granularity === "week" ? "自然周" : "自然月"}汇总净销售额，最多展示前 {data.trend.categoryLimit} 个品类。</p></div><div className="category-trend-legend">{categories.map((category, index) => <span key={category}><i style={{ background: chartColors[index] }} />{category}</span>)}</div></div>
    {periods.length > 0 ? <div className="category-trend-chart"><svg viewBox="0 0 700 210" role="img" aria-label="品类净销售额统计周期趋势">
      <line x1="30" y1={zeroY} x2="670" y2={zeroY} className="category-chart-axis" />
      {categories.map((category, categoryIndex) => {
        const values = new Map(data.trend.items.filter((item) => item.category === category).map((item) => [item.period, item.netSalesCents]));
        const points = periods.map((period, index) => `${x(index)},${y(values.get(period) ?? 0)}`).join(" ");
        return <polyline key={category} points={points} fill="none" stroke={chartColors[categoryIndex]} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />;
      })}
      {periods.map((period, index) => (periods.length <= 8 || index % Math.ceil(periods.length / 8) === 0) && <text key={period} x={x(index)} y="195" textAnchor="middle">{period.slice(5)}</text>)}
    </svg></div> : <div className="table-state" role="status">当前筛选下没有可绘制的品类趋势。</div>}
  </section>;
}

const sortableColumns: Array<{ key: CategorySortKey; label: string }> = [
  { key: "netSalesCents", label: "净销售额" },
  { key: "shareRate", label: "贡献率" },
  { key: "netQuantity", label: "净销量" },
  { key: "refundRate", label: "退货率" },
  { key: "refundAmountCents", label: "退款金额" },
  { key: "grossProfitCents", label: "毛利额" },
  { key: "grossMarginRate", label: "毛利率" },
  { key: "yearOverYearRate", label: "同比" },
  { key: "weekOverWeekRate", label: "环比上周" },
];

export default function SalesCategoryView({ startDate, endDate, filters, onFiltersChange }: { startDate: string; endDate: string; filters: SalesSharedFilters; onFiltersChange: (filters: SalesSharedFilters) => void }) {
  const [urlState, setUrlState] = useState<CategoryUrlState>(readCategoryUrl);
  const [data, setData] = useState<CategoryAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [detailCategory, setDetailCategory] = useState<string | null>(null);
  const [detailData, setDetailData] = useState<CategoryOutletBreakdownResponse | null>(null);
  const [detailDataCategory, setDetailDataCategory] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const requestGenerationRef = useRef(0);
  const detailRequestGenerationRef = useRef(0);
  useAiPageDetails("sales", {
    period: { startDate, endDate },
    filters: { dataset: "sales_category", query: filters.productQuery.trim(), platforms: filters.platforms, outletKeys: filters.outletKeys, channels: filters.channels, categories: detailCategory ? [detailCategory] : filters.categories },
  });


  const closeCategoryDetail = useCallback(() => {
    setDetailCategory(null);
    setDetailData(null);
    setDetailDataCategory(null);
    setDetailError("");
  }, []);

  const updateUrlState = useCallback((patch: Partial<CategoryUrlState>, mode: "push" | "replace" = "push") => {
    const next = { ...urlState, ...patch, page: patch.page ?? 1 };
    writeCategoryUrl(next, mode);
    setUrlState(next);
  }, [urlState]);

  useEffect(() => {
    const onPopState = () => setUrlState(readCategoryUrl());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGenerationRef.current;
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const query = new URLSearchParams({
          startDate,
          endDate,
          level: "1",
          granularity: urlState.granularity,
          sortBy: urlState.sortBy,
          direction: urlState.direction,
          page: String(urlState.page),
          pageSize: String(urlState.pageSize),
        });
        filters.categories.forEach((value) => query.append("category", value));
        filters.channels.forEach((value) => query.append("channel", value));
        filters.platforms.forEach((value) => query.append("platform", value));
        filters.outletKeys.forEach((value) => query.append("outlet", value));
        if (filters.productQuery.trim()) query.append("productQuery", filters.productQuery.trim());
        const payload = await requestJson<CategoryAnalysisResponse>(`/api/sales/category-analysis?${query}`, { signal: controller.signal });
        if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
        setData(payload);
      } catch (reason) {
        if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
        setError(reason instanceof Error ? reason.message : "暂时无法读取品类分析");
      } finally {
        if (!controller.signal.aborted && generation === requestGenerationRef.current) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [endDate, filters.categories, filters.channels, filters.outletKeys, filters.platforms, filters.productQuery, retryKey, startDate, urlState]);

  useEffect(() => {
    if (!detailCategory) return;
    const controller = new AbortController();
    const generation = ++detailRequestGenerationRef.current;
    void (async () => {
      setDetailLoading(true);
      setDetailError("");
      try {
        const query = new URLSearchParams({ startDate, endDate, category: detailCategory });
        filters.channels.forEach((value) => query.append("channel", value));
        filters.platforms.forEach((value) => query.append("platform", value));
        filters.outletKeys.forEach((value) => query.append("outlet", value));
        if (filters.productQuery.trim()) query.append("productQuery", filters.productQuery.trim());
        const payload = await requestJson<CategoryOutletBreakdownResponse>(`/api/sales/category-analysis/detail?${query}`, { signal: controller.signal });
        if (!controller.signal.aborted && generation === detailRequestGenerationRef.current) {
          setDetailData(payload);
          setDetailDataCategory(detailCategory);
        }
      } catch (reason) {
        if (!controller.signal.aborted && generation === detailRequestGenerationRef.current) {
          setDetailError(reason instanceof Error ? reason.message : "暂时无法读取平台店铺详情");
        }
      } finally {
        if (!controller.signal.aborted && generation === detailRequestGenerationRef.current) setDetailLoading(false);
      }
    })();
    return () => controller.abort();
  }, [detailCategory, endDate, filters.channels, filters.outletKeys, filters.platforms, filters.productQuery, startDate]);

  const filterOptions = data?.filterOptions ?? { categories: [], channels: [], platforms: [], outlets: [], totals: { categories: 0, channels: 0, platforms: 0, outlets: 0 }, truncated: false, limit: 200 };
  const hasChannelFilter = filters.channels.length > 0;

  if (loading && !data) return <section className="panel data-state sales-data-state" role="status"><span className="state-spinner" /><strong>正在汇总品类经营数据</strong><p>正在按商品主数据映射销售、退款和毛利明细…</p></section>;
  if (error && !data) return <section className="panel data-state sales-data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>品类分析加载失败</strong><p>{error}</p><button className="secondary-button" onClick={() => setRetryKey((value) => value + 1)}>重新加载</button></section>;
  if (!data) return null;

  const summary = data.summary;
  const maxRankingSales = Math.max(1, ...data.ranking.map((item) => Math.max(0, item.netSalesCents)));
  const visibleDetailData = detailDataCategory === detailCategory ? detailData : null;
  return <div className="sales-category-view data-refresh-region" aria-busy={loading}>
    <section className="panel category-filter-panel category-analysis-settings" aria-label="品类分析设置">
      <div className="category-filter-heading"><div><span className="eyebrow">CATEGORY SETTINGS</span><h2>品类分析设置</h2><p>{data.range.startDate} 至 {data.range.endDate} · 公共筛选已同步应用，渠道与趋势粒度为品类页专属设置。</p></div><span className="soft-tag">{data.filtersApplied.dataScope.mode === "restricted" ? "已应用账号数据范围" : "全部授权范围"}</span></div>
      <div className="category-filter-layout">
        <div className="category-level-field"><span>当前分析层级</span><strong>一级品类</strong><small>品类以 ERP 商品主数据为准，暂无下级类目</small></div>
        <div className="category-filter-controls">
          <div className="category-filter-fields">
            <MultiFilter label="渠道" values={filterOptions.channels} selected={filters.channels} onChange={(channels) => onFiltersChange({ ...filters, channels })} />
          </div>
          <div className="category-filter-actions">
            <span>趋势粒度</span>
            <div className="segmented category-granularity" role="group" aria-label="品类趋势统计粒度">{(["day", "week", "month"] as const).map((value) => <button key={value} type="button" className={urlState.granularity === value ? "active" : ""} onClick={() => updateUrlState({ granularity: value })}>{value === "day" ? "按日" : value === "week" ? "按周" : "按月"}</button>)}</div>
            {hasChannelFilter && <button type="button" className="secondary-button category-filter-reset" onClick={() => onFiltersChange({ ...filters, channels: [] })}>清空渠道</button>}
          </div>
        </div>
      </div>
      {(filterOptions.truncated || loading || error) && <div className={`category-filter-note ${error ? "error" : ""}`} role={error ? "alert" : "status"}>{error || (loading ? "正在更新筛选结果，旧结果会保留到新请求完成。" : `筛选选项每类最多返回 ${filterOptions.limit} 个，当前已截断。`)}</div>}
    </section>

    <section className="category-kpi-grid">
      <article><span>品类净销售额</span><strong>{formatCurrency(summary.netSalesCents)}</strong><small>销售额扣除退款，退款以负值参与</small></article>
      <article><span>正向销量</span><strong>{formatCount(summary.positiveQuantity)}</strong><small>仅数量大于 0 的销售行</small></article>
      <article><span>退货量 / 退款</span><strong>{formatCount(summary.returnQuantity)} <em>件</em></strong><small>{formatCurrency(summary.refundAmountCents)}</small></article>
      <article><span>品类毛利额</span><strong>{formatCurrency(summary.grossProfitCents)}</strong><small>毛利率 {formatRate(summary.grossMarginRate)}</small></article>
      <article className={data.uncategorized.visible ? "warning" : ""}><span>未分类商品</span><strong>{formatCount(data.uncategorized.productCount)}</strong><small>{formatCurrency(data.uncategorized.netSalesCents)} · 占 {formatRate(data.uncategorized.shareRate)}</small></article>
    </section>

    {data.uncategorized.visible && <section className="category-unclassified-notice" role="note"><span>!</span><div><strong>存在未匹配品类的商品</strong><p>{formatCount(data.uncategorized.productCount)} 个商品未在 ERP 商品主数据或销售明细中找到品类，已统一归入“未分类”并参与总计、贡献率、趋势和明细核对。</p></div></section>}

    {summary.categoryCount === 0 ? <section className="panel data-state" role="status"><span className="state-symbol">∅</span><strong>当前筛选没有品类销售数据</strong><p>可清空筛选或切换顶部统计周期；系统不会用模拟数据填充空结果。</p></section> : <>
      <section className="category-main-grid">
        <article className="panel category-structure-panel"><div className="category-section-heading"><div><span className="eyebrow">SALES MIX</span><h2>品类结构占比</h2><p>贡献率由服务端按完整筛选范围计算，合计 {formatRate(data.structure.contributionRateTotal)}。</p></div></div><div className="category-structure-list">{data.structure.items.map((item, index) => <div key={item.category}><span className={`table-rank ${index < 3 ? `top-${index + 1}` : ""}`}>{item.rank}</span><div><strong>{item.category}</strong><small>{formatCurrency(item.netSalesCents)} · {formatRate(item.shareRate)}</small><i><b style={{ width: `${Math.max(0, Math.min(100, item.shareRate * 100))}%` }} /></i></div></div>)}{data.structure.otherNetSalesCents !== 0 && <div><span className="table-rank">+</span><div><strong>其他品类</strong><small>{formatCurrency(data.structure.otherNetSalesCents)} · {formatRate(data.structure.otherShareRate)}</small><i><b style={{ width: `${Math.max(0, Math.min(100, data.structure.otherShareRate * 100))}%` }} /></i></div></div>}</div></article>
        <article className="panel category-ranking-panel"><div className="category-section-heading"><div><span className="eyebrow">CATEGORY RANKING</span><h2>品类销售排名</h2><p>按净销售额排序，退款负值已计入。</p></div></div><div className="category-ranking-list">{data.ranking.slice(0, 8).map((item) => <div key={item.category}><span>{item.rank}</span><div><strong>{item.category}</strong><i><b style={{ width: `${Math.max(2, Math.max(0, item.netSalesCents) / maxRankingSales * 100)}%` }} /></i></div><div><strong>{formatCurrency(item.netSalesCents)}</strong><small>毛利率 {formatRate(item.grossMarginRate)}</small></div></div>)}</div></article>
      </section>
      <CategoryTrend data={data} />
      <section className="panel table-panel category-detail-panel">
        <div className="table-toolbar"><div><h2>品类经营明细</h2><p>同比为去年同期净销售额变化；环比上周按截止日近 7 天对比此前 7 天；退货率按退款金额 ÷ 正向销售额计算。</p></div><span className="soft-tag">显示 {formatCount(data.details.pagination.returned)} / {formatCount(data.details.pagination.total)}{data.details.pagination.truncated ? " · 后续页未加载" : ""}</span></div>
        <div className="data-table-wrap"><table className="data-table category-detail-table"><thead><tr><th>排名</th><th>品类</th>{sortableColumns.map((column) => <th key={column.key}><button type="button" onClick={() => updateUrlState({ sortBy: column.key, direction: urlState.sortBy === column.key && urlState.direction === "desc" ? "asc" : "desc" })}>{column.label}{urlState.sortBy === column.key ? (urlState.direction === "desc" ? " ↓" : " ↑") : ""}</button></th>)}<th>品类趋势</th><th>详情</th></tr></thead><tbody>{data.details.items.map((item, index) => <tr key={item.category}><td>{(data.details.pagination.page - 1) * data.details.pagination.pageSize + index + 1}</td><td><strong>{item.category}</strong>{item.category === "未分类" && <small className="category-unclassified-tag">需补充映射</small>}</td><td><strong>{formatCurrency(item.netSalesCents)}</strong></td><td>{formatRate(item.shareRate)}</td><td>{formatCount(item.netQuantity)}</td><td>{formatRate(item.refundRate)}</td><td>{formatCurrency(item.refundAmountCents)}</td><td>{formatCurrency(item.grossProfitCents)}</td><td className={item.grossMarginRate < 0 ? "orange-text" : "green-text"}>{formatRate(item.grossMarginRate)}</td><td className={comparisonTone(item.yearOverYearRate)} title={`去年同期 ${formatCurrency(item.yearAgoNetSalesCents)}`}>{formatComparison(item.yearOverYearRate)}</td><td className={comparisonTone(item.weekOverWeekRate)} title={`近7天 ${formatCurrency(item.currentWeekNetSalesCents)}；此前7天 ${formatCurrency(item.previousWeekNetSalesCents)}`}>{formatComparison(item.weekOverWeekRate)}</td><td><CategoryDetailTrend item={item} /></td><td><button type="button" className="category-detail-button" onClick={() => setDetailCategory(item.category)}>查看详情</button></td></tr>)}</tbody></table></div>
        <footer className="category-pagination"><span>第 {data.details.pagination.page} 页 · 每页 {data.details.pagination.pageSize} 条</span><div><button type="button" disabled={urlState.page <= 1 || loading} onClick={() => updateUrlState({ page: Math.max(1, urlState.page - 1) })}>上一页</button><button type="button" disabled={!data.details.pagination.truncated || loading} onClick={() => updateUrlState({ page: urlState.page + 1 })}>下一页</button></div></footer>
      </section>
    </>}
    <section className="category-source-note"><strong>数据来源与口径</strong><span>品类：ERP 商品主数据优先，销售明细品类兜底，以商品编码关联；未命中归“未分类”。</span><span>销售：吉客云销售单明细账，排除“刷刷仓”；净销量沿用销售总览口径，排除配件、赠品配件、补差价专用和销售行未分类，退货率 = 退款金额 / 正向销售额。</span><span>同比：{data.comparisonPeriods.yearAgo.startDate} 至 {data.comparisonPeriods.yearAgo.endDate}；环比上周：近 7 天 {data.comparisonPeriods.weekOverWeek.current.startDate} 至 {data.comparisonPeriods.weekOverWeek.current.endDate}，对比此前 7 天 {data.comparisonPeriods.weekOverWeek.previous.startDate} 至 {data.comparisonPeriods.weekOverWeek.previous.endDate}。</span><span>品类趋势：按当前{data.details.trend.granularity === "day" ? "日" : data.details.trend.granularity === "week" ? "周" : "月"}粒度展示最近 {data.details.trend.periodLimit} 个有数据周期；数据截止 {data.dataCutoffDate ?? "暂无"}。</span></section>
    {detailCategory && <CategoryOutletDrawer category={detailCategory} data={visibleDetailData} loading={detailLoading} error={detailError} onClose={closeCategoryDetail} />}
  </div>;
}
