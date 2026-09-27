"use client";

import { useAiPageDetails } from "./ai-page-context-provider";
/* eslint-disable @next/next/no-img-element -- Market ranking thumbnails are imported business assets. */

import { Suspense, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { beginLatestRequest, invalidateLatestRequest } from "@/lib/market/latest-request";
import { readMarketJson } from "@/lib/market/read-request";
import type { ModuleViewKey } from "./shell/navigation-catalog";
import { createReloadableLazy } from "./shell/reloadable-lazy";
import Dialog from "./ui/dialog";

const MARKET_RANKING_PAGE_SIZE = 20;
const { Component: MarketAnnotationView } = createReloadableLazy("market", () => import("./market-annotation-view"));
const { Component: LazyMarketMasterAdminPanel } = createReloadableLazy("market", () => import("./market-master-admin-panel"));

export { canCloseMarketSkuEditor } from "./market-master-admin-contract";

type CurrentUser = { email: string; role: "viewer" | "analyst" | "operator" | "admin" } | null;
type FilterOption = { value: string; count: number; label?: string };
type MarketItem = {
  id: number; periodStart: string; periodEnd: string; category: string; scope: string; rankingDimension: "SKU" | "SPU";
  operationMode: "POP" | "自营" | "未知"; subcategory: string; rank: number | null; previousRank: number | null; rankChange: number | null;
  skuCode: string; productName: string; brand: string; priceCents: number | null; marketPriceCents: number | null; marketPriceSource: string;
  averageTransactionPriceCents: number | null; discountBps: number | null; discountReference: boolean;
  gmvCents: number; quantity: number; pageViews: number; visitors: number; conversionBps: number | null;
  cartCustomers: number; searchClicks: number; imageUrl: string; productUrl: string; sourceImageUrl: string; imageCacheStatus: string;
  periodCount: number; isOwn: boolean; ownSalesCents: number; gmvOutOfBand?: boolean;
};
type MarketCompareIdentity = Pick<MarketItem, "skuCode" | "category" | "scope" | "rankingDimension">;
type MarketCompareSelection = MarketCompareIdentity & Pick<MarketItem, "productName">;
type GrowthFields = { latestPeriod: string | null; monthOverMonthBps: number | null; yearOverYearBps: number | null };
type MarketIndustryReport = {
  definition: {
    title: string; metricScope: string;
    profile: { category: string; coreSubcategories: readonly string[]; adjacentSubcategories: readonly string[]; adjacentCategories: readonly string[] };
    selectedCategories: string[]; selectedScopes: string[]; selectedRankingDimensions: string[];
  };
  period: {
    coverageMonths: number; latestPeriod: string | null; latestGmvCents: number; monthOverMonthBps: number | null; yearOverYearBps: number | null;
    peak: { period: string; gmvCents: number } | null; trough: { period: string; gmvCents: number } | null;
    latestEntryCount: number | null; latestExitCount: number | null; latestExitPeriod: string | null;
  };
  lifecycle: Array<{ period: string; entryCount: number | null; exitCount: number | null }>;
  operationModes: Array<{
    operationMode: string; gmvCents: number; quantity: number; skuCount: number; visitors: number; conversionBps: number | null;
    brandCount: number; gmvShareBps: number; averageTransactionPriceCents: number | null; gmvPerSkuCents: number;
  }>;
  brandConcentrationTrend: Array<{ period: string; gmvCents: number; brandCount: number; cr3Bps: number; cr5Bps: number }>;
  trafficQuadrants: Array<{
    quadrant: "high_traffic_high_conversion" | "high_traffic_low_conversion" | "low_traffic_high_conversion" | "low_traffic_low_conversion";
    productCount: number; gmvCents: number; quantity: number; visitors: number; conversionBps: number | null;
    visitorThreshold: number; conversionThresholdBps: number;
    examples: Array<{ skuCode: string; productName: string; gmvCents: number }>;
  }>;
  productSignals: { sampleSize: number; source: string; signals: Array<{ group: string; label: string; count: number; shareBps: number; examples: string[] }> };
  opportunities: Array<{
    subcategory: string; priceBand: string; scenario: string; gmvCents: number; quantity: number; skuCount: number; visitors: number;
    conversionBps: number | null; brandCount: number; gmvShareBps: number; growthBps: number | null; selfOperatedShareBps: number;
    pendingPriceShareBps: number; score: number; recommendation: "建议进入" | "持续观察" | "谨慎回避"; reasons: string[]; decisionReady: boolean;
  }>;
  dataQuality: {
    categoryCount: number; scopeCount: number; rankingDimensionCount: number; operationModeCount: number;
    unknownBrandSkuCount: number; unclassifiedSkuCount: number; pendingPriceSkuCount: number;
    identityReady: boolean; coverageReady: boolean; comparisonReady: boolean; warnings: string[];
  };
  externalDataGaps: Array<{ key: string; label: string; status: string; note: string }>;
};
type MarketOverview = {
  view: "ranking" | "full";
  salesRevision: string;
  summary: {
    productCount: number; categoryCount: number; brandCount: number; gmvCents: number; quantity: number; pageViews: number; visitors: number;
    ownProductCount: number; activeSkuCount: number; pendingAiCount: number; selfOperatedGmvCents: number; selfOperatedShareBps: number | null;
    medianMarketPriceCents: number | null; weightedMarketPriceCents: number | null; averageTransactionPriceCents: number | null;
  };
  items: MarketItem[];
  pagination: { page: number; pageSize: number; total: number; pageCount: number };
  trend: Array<Record<string, string | number | null>>;
  trendTotal: number;
  trendTruncated: boolean;
  priceBands: FilterOption[];
  priceBandSummary: Array<{ priceBand: string; gmvCents: number; quantity: number; skuCount: number; popGmvCents: number; selfGmvCents: number; gmvShareBps: number; selfOperatedShareBps: number | null; mainBrands: string[] }>;
  priceBandTrend: Array<{ period: string; priceBand: string; gmvCents: number; quantity: number; gmvShareBps: number }>;
  brandAnalysis: { items: Array<{ brand: string; gmvCents: number; quantity: number; skuCount: number; bestRank: number | null; gmvShareBps: number; heroSkuGmvCents: number; heroSkuShareBps: number; priceBands: string[]; subcategories: string[] } & GrowthFields>; cr3Bps: number; cr5Bps: number; concentration: string };
  subcategorySummary: Array<{ subcategory: string; skuCount: number; gmvCents: number; gmvShareBps: number; quantity: number; averageTransactionPriceCents: number | null; selfOperatedShareBps: number | null; pendingSkuCount: number; mainBrands: string[]; mainPriceBands: string[] } & GrowthFields>;
  industryReport: MarketIndustryReport;
  filters: { categories: FilterOption[]; scopes: FilterOption[]; brands: FilterOption[]; rankingDimensions: FilterOption[]; operationModes: FilterOption[]; subcategories: FilterOption[]; priceBands: FilterOption[] };
  dataRange: { startDate: string | null; endDate: string | null };
  batches: Array<{ id: string; fileName: string; sourceType: string; rowCount: number; insertedCount: number; updatedCount: number; warningCount: number; completedAt: string | null }>;
  imageCache: { total: number; cached: number; failed: number; pending: number };
  error?: string;
};
type MarketSettingsStatus = Pick<MarketOverview, "dataRange" | "batches" | "imageCache">;
const EMPTY_MARKET_FILTERS: MarketOverview["filters"] = {
  categories: [], scopes: [], brands: [], rankingDimensions: [{ value: "SKU", count: 0 }, { value: "SPU", count: 0 }],
  operationModes: [], subcategories: [], priceBands: [],
};
type MarketImageCacheJob = MarketOverview["imageCache"] & {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  discoveryComplete: boolean;
  discoveredCount: number;
  propagationPending: number;
  processedCount: number;
  runCount: number;
  errorMessage: string;
};

function imageCacheWorkPending(job: MarketImageCacheJob) {
  return job.pending + job.propagationPending;
}

function cacheStatsFromJob(job: MarketImageCacheJob): MarketOverview["imageCache"] {
  return { total: job.total, cached: job.cached, failed: job.failed, pending: imageCacheWorkPending(job) };
}

function imageCacheJobProgress(job: MarketImageCacheJob) {
  if (!job.discoveryComplete) {
    return `后台扫描/排队中：已发现 ${job.discoveredCount} 张，待缓存 ${job.pending} 张，待传播 ${job.propagationPending} 张`;
  }
  return `已缓存 ${job.cached} 张，待缓存 ${job.pending} 张，待传播 ${job.propagationPending} 张`;
}

function waitForImageCachePoll(signal: AbortSignal, delayMs = 2_000) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => { cleanup(); resolve(); }, delayMs);
    const abort = () => { cleanup(); reject(new DOMException("请求已取消", "AbortError")); };
    const cleanup = () => { window.clearTimeout(timer); signal.removeEventListener("abort", abort); };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

async function pollMarketImageCacheJob(
  initialJob: MarketImageCacheJob,
  signal: AbortSignal,
  onUpdate: (job: MarketImageCacheJob) => void,
  maxPolls = 30,
) {
  let job = initialJob;
  for (let poll = 0; poll < maxPolls && job.status !== "completed" && job.status !== "failed"; poll += 1) {
    await waitForImageCachePoll(signal);
    const response = await fetch(`/api/market/images/cache?jobId=${encodeURIComponent(job.id)}`, {
      cache: "no-store",
      signal,
    });
    const payload = await response.json().catch(() => null) as { error?: string; job?: MarketImageCacheJob } | null;
    if (!response.ok || !payload?.job) throw new Error(payload?.error || "图片缓存任务状态无响应");
    job = payload.job;
    onUpdate(job);
  }
  return { job, timedOut: job.status !== "completed" && job.status !== "failed" };
}
type MarketOverviewClientCacheEntry = { payload: MarketOverview; storedAt: number };
type MarketOverviewSharedRequest = {
  controller: AbortController;
  promise: Promise<MarketOverview>;
  subscribers: number;
  settled: boolean;
};
const MARKET_OVERVIEW_RECENT_PREFETCH_MS = 5_000;
const marketOverviewClientCache = new Map<string, MarketOverviewClientCacheEntry>();
const marketOverviewRequests = new Map<string, MarketOverviewSharedRequest>();

function defaultMarketRankingParams(startDate: string, endDate: string) {
  const params = new URLSearchParams();
  params.set("view", "ranking");
  params.set("includeFilterOptions", "false");
  params.set("page", "1");
  params.set("pageSize", String(MARKET_RANKING_PAGE_SIZE));
  params.append("dimension", "SKU");
  if (startDate) params.set("startDate", startDate);
  if (endDate) params.set("endDate", endDate);
  return params;
}

function cachedMarketOverview(requestKey: string, maximumAgeMs = MARKET_OVERVIEW_RECENT_PREFETCH_MS) {
  const cached = marketOverviewClientCache.get(requestKey);
  if (!cached) return null;
  const ageMs = Date.now() - cached.storedAt;
  if (ageMs <= maximumAgeMs) return cached.payload;
  if (ageMs > MARKET_OVERVIEW_RECENT_PREFETCH_MS) marketOverviewClientCache.delete(requestKey);
  return null;
}

function rememberMarketOverview(requestKey: string, payload: MarketOverview) {
  marketOverviewClientCache.set(requestKey, { payload, storedAt: Date.now() });
  while (marketOverviewClientCache.size > 12) {
    const oldestKey = marketOverviewClientCache.keys().next().value as string | undefined;
    if (!oldestKey) break;
    marketOverviewClientCache.delete(oldestKey);
  }
}

function marketOverviewAbortError() {
  const error = new Error("市场分析请求已取消");
  error.name = "AbortError";
  return error;
}

function subscribeMarketOverview(request: MarketOverviewSharedRequest, signal?: AbortSignal) {
  request.subscribers += 1;
  return new Promise<MarketOverview>((resolve, reject) => {
    let active = true;
    const release = () => {
      if (!active) return false;
      active = false;
      signal?.removeEventListener("abort", onAbort);
      request.subscribers = Math.max(0, request.subscribers - 1);
      if (request.subscribers === 0 && !request.settled) request.controller.abort();
      return true;
    };
    const onAbort = () => {
      if (!release()) return;
      reject(marketOverviewAbortError());
    };
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
    request.promise.then(
      (payload) => { if (release()) resolve(payload); },
      (error: unknown) => { if (release()) reject(error); },
    );
  });
}

export async function requestMarketOverview(requestKey: string, signal?: AbortSignal, maximumCacheAgeMs = 0) {
  if (signal?.aborted) throw marketOverviewAbortError();
  if (maximumCacheAgeMs > 0) {
    const cached = cachedMarketOverview(requestKey, maximumCacheAgeMs);
    if (cached) return cached;
  }
  let request = marketOverviewRequests.get(requestKey);
  if (request?.controller.signal.aborted) {
    if (marketOverviewRequests.get(requestKey) === request) marketOverviewRequests.delete(requestKey);
    request = undefined;
  }
  if (!request) {
    const controller = new AbortController();
    const task = (async () => {
      const payload = await readMarketJson<MarketOverview>(`/api/market/overview?${requestKey}`, controller.signal);
      rememberMarketOverview(requestKey, payload);
      return payload;
    })();
    request = { controller, promise: task, subscribers: 0, settled: false };
    const ownedRequest = request;
    marketOverviewRequests.set(requestKey, ownedRequest);
    task.then(
      () => {
        ownedRequest.settled = true;
        if (marketOverviewRequests.get(requestKey) === ownedRequest) marketOverviewRequests.delete(requestKey);
      },
      () => {
        ownedRequest.settled = true;
        if (marketOverviewRequests.get(requestKey) === ownedRequest) marketOverviewRequests.delete(requestKey);
      },
    );
  }
  return await subscribeMarketOverview(request, signal);
}

export async function prefetchMarketRankingOverview(startDate: string, endDate: string, signal?: AbortSignal) {
  const requestKey = defaultMarketRankingParams(startDate, endDate).toString();
  await requestMarketOverview(requestKey, signal, MARKET_OVERVIEW_RECENT_PREFETCH_MS);
}
type TrendPayload = { items: Array<Record<string, string | number | null>>; totalMonths: number; truncated: boolean; error?: string };
type ComparePayload = {
  items: Array<{
    skuCode: string; productName: string; brand: string; category: string; scope: string; rankingDimension: "SKU" | "SPU";
    gmvCents: number; quantity: number; visitors: number; conversionBps: number | null; bestRank: number | null;
    marketPriceCents: number | null; averageTransactionPriceCents: number | null;
    trend: Array<Record<string, string | number | null>>;
    trendTotalMonths: number; trendTruncated: boolean;
  }>;
  missingSkuCodes: string[];
  missingSelections: MarketCompareIdentity[];
  error?: string;
};
type MarketSectionKey = "ranking" | "overview" | "compare" | "settings";
type MarketSettingsTab = "database" | "subcategory" | "brand" | "mapping" | "data";
type MarketSystemKpis = {
  marketIdentityTotal: number; pendingPriceCount: number; pendingAiCount: number; completedAiCount: number;
  sameImageReuseCount: number; priceOnlyRecognitionCount: number; fullRecognitionCount: number; blockedRecognitionCount: number;
};

const money = (cents?: number | null) => cents === null || cents === undefined
  ? "-"
  : new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(cents / 100);
const count = (value = 0) => new Intl.NumberFormat("zh-CN").format(value);
const percent = (bps?: number | null) => bps === null || bps === undefined ? "-" : `${(bps / 100).toFixed(2)}%`;
const growthPercent = (bps?: number | null) => bps === null || bps === undefined ? "暂无可比" : `${bps > 0 ? "+" : ""}${(bps / 100).toFixed(2)}%`;
const monthText = (start: string | null, end: string | null) => start && end ? `${start.slice(0, 7)} 至 ${end.slice(0, 7)}` : "暂无月份";
const shanghaiToday = () => {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
};
const twelveMonthReportPeriod = (referenceDate = shanghaiToday()) => {
  const match = /^(\d{4})-(\d{2})-\d{2}$/.exec(referenceDate);
  if (!match) return { startDate: referenceDate, endDate: referenceDate };
  const currentMonth = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  const start = new Date(Date.UTC(currentMonth.getUTCFullYear(), currentMonth.getUTCMonth() - 12, 1));
  const end = new Date(Date.UTC(currentMonth.getUTCFullYear(), currentMonth.getUTCMonth(), 0));
  const iso = (date: Date) => `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
  return { startDate: iso(start), endDate: iso(end) };
};
const marketProductHref = (productUrl: unknown, skuCode: unknown) => {
  const direct = typeof productUrl === "string" ? productUrl.trim() : "";
  if (/^https:\/\//i.test(direct)) return direct;
  const sku = String(skuCode ?? "").trim();
  return /^\d{6,20}$/.test(sku) ? `https://item.jd.com/${sku}.html` : "";
};
const marketCompareSelectionKey = (selection: MarketCompareIdentity) => JSON.stringify([
  selection.category,
  selection.scope,
  selection.rankingDimension,
  selection.skuCode,
]);
function SearchMultiFilter({ label, values, options, onChange }: { label: string; values: string[]; options: FilterOption[]; onChange: (values: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const visible = options.filter((option) => `${option.label ?? ""} ${option.value}`.toLowerCase().includes(query.trim().toLowerCase()));
  const toggle = (value: string) => onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  return <div className="market-filter" ref={root}>
    <button type="button" className={values.length ? "active" : ""} onClick={() => setOpen((current) => !current)} aria-expanded={open}>
      <span>{label}</span><strong>{values.length ? `已选 ${values.length}` : `全部${label}`}</strong><em>⌄</em>
    </button>
    {open && <div className="market-filter-popover">
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索${label}`} aria-label={`搜索${label}`} autoFocus />
      <button type="button" className="market-filter-all" onClick={() => onChange([])}><i className={!values.length ? "checked" : ""} />全部{label}</button>
      <div>{visible.map((option) => <button type="button" key={option.value} onClick={() => toggle(option.value)}>
        <i className={values.includes(option.value) ? "checked" : ""} /><span>{option.label ?? option.value}</span>{option.count > 0 && <small>{count(option.count)}</small>}
      </button>)}{visible.length === 0 && <p>未找到匹配内容</p>}</div>
    </div>}
  </div>;
}

function KpiCard({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) {
  return <article className="panel"><span className={`market-kpi-dot ${tone}`} /><small>{label}</small><strong>{value}</strong><p>{note}</p></article>;
}

function marketReportDimensionLabel(data: MarketOverview) {
  const selected = data.industryReport.definition.selectedRankingDimensions;
  if (selected.length === 1) return selected[0];
  if (data.industryReport.dataQuality.rankingDimensionCount > 1) return "商品";
  return data.items[0]?.rankingDimension ?? "商品";
}

function MarketSectionNav({ active, compareCount, onChange }: { active: MarketSectionKey; compareCount: number; onChange: (section: MarketSectionKey) => void }) {
  const sections: Array<{ key: MarketSectionKey; number: string; label: string; note: string }> = [
    { key: "ranking", number: "01", label: "商品榜单", note: "TOP 商品、成交数据与单品趋势" },
    { key: "overview", number: "02", label: "行业汇报", note: "趋势、结构、竞争与机会矩阵" },
    { key: "compare", number: "03", label: "竞品对比", note: "2–5 个 SKU 的指标与趋势对照" },
    { key: "settings", number: "04", label: "系统和 AI 设置", note: "主数据、导入、映射与 AI 工作流" },
  ];
  return <nav className="panel market-section-nav" role="tablist" aria-label="市场分析子板块">
    {sections.map((section) => <button
      type="button"
      role="tab"
      id={`market-tab-${section.key}`}
      aria-controls={`market-panel-${section.key}`}
      aria-selected={active === section.key}
      tabIndex={active === section.key ? 0 : -1}
      key={section.key}
      className={active === section.key ? "active" : ""}
      onClick={() => onChange(section.key)}
      onKeyDown={(event) => {
        const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
        const index = tabs.indexOf(event.currentTarget);
        const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : -1;
        if (nextIndex >= 0) { event.preventDefault(); tabs[nextIndex]?.focus(); tabs[nextIndex]?.click(); }
      }}
    >
      <span>{section.number}</span><div><strong>{section.label}</strong><small>{section.note}</small></div>{section.key === "compare" && compareCount > 0 ? <em>{compareCount}</em> : <i>›</i>}
    </button>)}
  </nav>;
}

function MarketKpis({ data }: { data: MarketOverview }) {
  const dimensionLabel = marketReportDimensionLabel(data);
  return <section className="market-kpi-grid market-kpi-grid-seven">
    <KpiCard label="主图价格中位数" value={money(data.summary.medianMarketPriceCents)} note="行业价格 KPI · 当前 TOP 榜单覆盖口径" tone="blue" />
    <KpiCard label="销售额加权主图价" value={money(data.summary.weightedMarketPriceCents)} note="按榜单销售额加权" tone="green" />
    <KpiCard label="榜单覆盖销售额" value={money(data.summary.gmvCents)} note="不能视为完整行业市场" tone="purple" />
    <KpiCard label="成交件数" value={count(data.summary.quantity)} note={`成交均价 ${money(data.summary.averageTransactionPriceCents)}`} tone="orange" />
    <KpiCard label="自营销售额占比" value={percent(data.summary.selfOperatedShareBps)} note={`${money(data.summary.selfOperatedGmvCents)} / 当前筛选销售额`} tone="green" />
    <KpiCard label={`${dimensionLabel} 数`} value={count(data.summary.productCount)} note={`${count(data.summary.brandCount)} 个品牌`} tone="blue" />
    <KpiCard label="品牌数" value={count(data.summary.brandCount)} note={`CR3 ${percent(data.brandAnalysis.cr3Bps)} · CR5 ${percent(data.brandAnalysis.cr5Bps)}`} tone="purple" />
  </section>;
}

function IndustryExecutiveSummary({ data }: { data: MarketOverview }) {
  const report = data.industryReport;
  const topSubcategory = data.subcategorySummary[0];
  const topBrand = data.brandAnalysis.items[0];
  const topOpportunity = report.opportunities[0];
  const profile = report.definition.profile;
  return <section className="panel market-industry-executive">
    <header><div><span className="eyebrow">EXECUTIVE SUMMARY</span><h2>京东商用直饮机行业汇报</h2><p>所有金额与份额均为“当前 TOP 榜单覆盖市场”，不外推为京东完整行业大盘。</p></div><div className="market-industry-scope-state"><strong className={report.dataQuality.identityReady ? "ready" : "warning"}>{report.dataQuality.identityReady ? "分析身份已锁定" : "分析身份待锁定"}</strong><small>{report.dataQuality.categoryCount} 个类目 · {report.dataQuality.scopeCount} 个榜单范围 · {report.dataQuality.rankingDimensionCount} 个榜单维度</small></div></header>
    <div className="market-industry-insight-grid">
      <article><small>市场趋势</small><strong>{report.period.latestPeriod ?? "暂无月份"} · {money(report.period.latestGmvCents)}</strong><span>环比 {growthPercent(report.period.monthOverMonthBps)} · 同比 {growthPercent(report.period.yearOverYearBps)}</span></article>
      <article><small>主力细分</small><strong>{topSubcategory?.subcategory ?? "暂无数据"}</strong><span>{topSubcategory ? `销售占比 ${percent(topSubcategory.gmvShareBps)} · 环比 ${growthPercent(topSubcategory.monthOverMonthBps)}` : "等待细分类目数据"}</span></article>
      <article><small>竞争格局</small><strong>{topBrand?.brand ?? "暂无品牌"}</strong><span>CR3 {percent(data.brandAnalysis.cr3Bps)} · CR5 {percent(data.brandAnalysis.cr5Bps)}{topBrand ? ` · 爆款依赖 ${percent(topBrand.heroSkuShareBps)}` : ""}</span></article>
      <article><small>市场机会信号</small><strong>{topOpportunity ? `${topOpportunity.subcategory} × ${topOpportunity.priceBand}` : "暂无可评级单元"}</strong><span>{topOpportunity ? `${topOpportunity.recommendation} · ${topOpportunity.score} 分${topOpportunity.decisionReady ? "" : " · 待补齐口径/价格"}` : "等待连续月份与正式价格"}</span></article>
    </div>
    <div className="market-industry-boundary"><div><strong>核心范围</strong>{profile.coreSubcategories.map((item) => <span key={item}>{item}</span>)}</div><div><strong>相邻范围（单独分析）</strong>{profile.adjacentSubcategories.map((item) => <span key={item}>{item}</span>)}{profile.adjacentCategories.map((item) => <span key={item}>{item}</span>)}</div></div>
    {report.dataQuality.warnings.length > 0 && <div className="market-industry-warnings">{report.dataQuality.warnings.map((warning) => <p key={warning}>⚠ {warning}</p>)}</div>}
  </section>;
}

function TrendSection({ data }: { data: MarketOverview }) {
  const dimensionLabel = marketReportDimensionLabel(data);
  const metrics = [
    ["gmv_cents", "销售额", (value: number | null) => money(value)],
    ["quantity", "成交件数", (value: number | null) => count(value ?? 0)],
    ["weighted_market_price_cents", "市场定位价", (value: number | null) => money(value)],
    ["average_transaction_price_cents", "成交均价", (value: number | null) => money(value)],
    ["self_gmv_cents", "POP/自营销售额", (value: number | null) => money(value)],
    ["product_count", `${dimensionLabel} 数`, (value: number | null) => count(value ?? 0)],
    ["brand_count", "品牌数", (value: number | null) => count(value ?? 0)],
  ] as const;
  const [metric, setMetric] = useState<(typeof metrics)[number][0]>("gmv_cents");
  const selected = metrics.find((item) => item[0] === metric) ?? metrics[0];
  const max = Math.max(1, ...data.trend.map((row) => Number(row[metric] ?? 0)));
  return <section className="panel market-section">
    <div className="section-header"><div><h2>月度趋势</h2><p>由服务端按完整筛选结果汇总，最多展示最近 60 个月{data.trendTruncated ? `（共 ${count(data.trendTotal)} 个月）` : ""}。</p></div><select value={metric} onChange={(event) => setMetric(event.target.value as typeof metric)}>{metrics.map((item) => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></div>
    <div className="market-trend-chart">{data.trend.map((row) => {
      const value = Number(row[metric] ?? 0);
      return <div key={String(row.period)}><span style={{ height: `${Math.max(5, value / max * 100)}%` }} /><strong>{selected[2](row[metric] === null ? null : value)}</strong><small>{String(row.period)}</small></div>;
    })}{!data.trend.length && <p>暂无趋势数据</p>}</div>
    <div className="market-lifecycle-strip">{data.industryReport.lifecycle.slice(-12).map((row) => <article key={row.period}><strong>{row.period}</strong><span>进入榜单 {row.entryCount === null ? "—" : count(row.entryCount)}</span><span>退出榜单 {row.exitCount === null ? "—" : count(row.exitCount)}</span></article>)}{!data.industryReport.lifecycle.length && <p>暂无连续月份，不能判断商品进入与退出。</p>}</div>
    <small className="market-method-note">首月无法识别新增、末月无法识别退出；进入/退出均按类目、榜单范围、SKU/SPU 维度和商品编码判断。</small>
  </section>;
}

function PriceBandSection({ data }: { data: MarketOverview }) {
  const max = Math.max(1, ...data.priceBandSummary.map((item) => item.gmvCents));
  const dimensionLabel = marketReportDimensionLabel(data);
  return <section className="panel market-section">
    <div className="section-header"><div><h2>价格带分析</h2><p>优先使用对应月份的人工确认市场定位价；尚未确认时，以源表价格区间中位数兜底划分。</p></div></div>
    <div className="market-price-band-grid">{data.priceBandSummary.map((item) => <article key={item.priceBand}>
      <div><strong>{item.priceBand}</strong><span>{money(item.gmvCents)}</span></div>
      <i><b style={{ width: `${item.gmvCents / max * 100}%` }} /></i>
      <small>销售额占比 {percent(item.gmvShareBps)} · 成交 {count(item.quantity)} · {dimensionLabel} {count(item.skuCount)} · 自营 {percent(item.selfOperatedShareBps)}</small>
      <em>{item.mainBrands.slice(0, 3).join(" / ") || "暂无主要品牌"}</em>
    </article>)}</div>
    <div className="data-table-wrap"><table className="data-table"><thead><tr><th>月份</th><th>价格带</th><th>销售额</th><th>当月销售占比</th><th>成交件数</th></tr></thead><tbody>{data.priceBandTrend.map((item) => <tr key={`${item.period}-${item.priceBand}`}><td>{item.period}</td><td>{item.priceBand}</td><td>{money(item.gmvCents)}</td><td>{percent(item.gmvShareBps)}</td><td>{count(item.quantity)}</td></tr>)}{!data.priceBandTrend.length && <tr><td colSpan={5}><div className="table-state">暂无价格带月度变化数据。</div></td></tr>}</tbody></table></div>
  </section>;
}

function BrandSection({ data }: { data: MarketOverview }) {
  const max = Math.max(1, ...data.brandAnalysis.items.map((item) => item.gmvCents));
  const concentrationTrend = data.industryReport.brandConcentrationTrend.slice(-12);
  const dimensionLabel = marketReportDimensionLabel(data);
  return <section className="panel market-section">
    <div className="section-header"><div><h2>品牌竞争及品牌份额</h2><p>品牌份额为当前 TOP 榜单覆盖口径；集中度：{data.brandAnalysis.concentration}。</p></div></div>
    <div className="market-brand-list">{data.brandAnalysis.items.slice(0, 12).map((item) => <article key={item.brand}>
      <label><strong>{item.brand}</strong><span>{percent(item.gmvShareBps)}</span></label>
      <i><b style={{ width: `${item.gmvCents / max * 100}%` }} /></i>
      <small>{money(item.gmvCents)} · 成交 {count(item.quantity)} · {dimensionLabel} {count(item.skuCount)} · 最好排名 {item.bestRank ? `#${item.bestRank}` : "-"}</small>
      <small>环比 {growthPercent(item.monthOverMonthBps)} · 同比 {growthPercent(item.yearOverYearBps)} · 头部单品贡献 {percent(item.heroSkuShareBps)}</small>
      <em>{[...item.priceBands.slice(0, 2), ...item.subcategories.slice(0, 2)].join(" / ")}</em>
    </article>)}</div>
    <div className="market-concentration-trend">{concentrationTrend.map((item) => <article key={item.period}><strong>{item.period}</strong><div><span>CR3 {percent(item.cr3Bps)}</span><i><b style={{ width: `${Math.min(100, item.cr3Bps / 100)}%` }} /></i></div><div><span>CR5 {percent(item.cr5Bps)}</span><i><b style={{ width: `${Math.min(100, item.cr5Bps / 100)}%` }} /></i></div><small>{count(item.brandCount)} 个品牌</small></article>)}{!concentrationTrend.length && <p>暂无品牌集中度月度变化数据。</p>}</div>
  </section>;
}

function SubcategorySection({ data }: { data: MarketOverview }) {
  const dimensionLabel = marketReportDimensionLabel(data);
  return <section className="panel market-section market-subcategory-panel">
    <div className="section-header"><div><h2>细分类目拆分汇总</h2><p>服务端按销售额汇总展示前 60 个细分类目，覆盖 {dimensionLabel} 数、成交件数、价格和待确认数据。</p></div></div>
    <div className="data-table-wrap"><table className="data-table" data-column-filter-scope="none"><thead><tr><th>细分类目</th><th>{dimensionLabel} 数</th><th>销售额</th><th>销售占比</th><th>环比</th><th>同比</th><th>成交件数</th><th>成交均价</th><th>自营占比</th><th>主要品牌</th><th>主力价格带</th><th>待确认{dimensionLabel}</th></tr></thead><tbody>
      {data.subcategorySummary.map((item) => <tr key={item.subcategory}><td>{item.subcategory || "未分类"}</td><td>{count(item.skuCount)}</td><td>{money(item.gmvCents)}</td><td>{percent(item.gmvShareBps)}</td><td>{growthPercent(item.monthOverMonthBps)}</td><td>{growthPercent(item.yearOverYearBps)}</td><td>{count(item.quantity)}</td><td>{money(item.averageTransactionPriceCents)}</td><td>{percent(item.selfOperatedShareBps)}</td><td>{item.mainBrands.join(" / ") || "-"}</td><td>{item.mainPriceBands.join(" / ") || "-"}</td><td>{count(item.pendingSkuCount)}</td></tr>)}
      {!data.subcategorySummary.length && <tr><td colSpan={12}><div className="table-state">当前筛选范围暂无细分类目数据。</div></td></tr>}
    </tbody></table></div>
  </section>;
}

function OperationModeSection({ data }: { data: MarketOverview }) {
  const dimensionLabel = marketReportDimensionLabel(data);
  const monthly = data.trend.slice(-12).map((row) => {
    const popGmvCents = Number(row.pop_gmv_cents ?? 0);
    const selfGmvCents = Number(row.self_gmv_cents ?? 0);
    const total = popGmvCents + selfGmvCents;
    return { period: String(row.period ?? ""), popGmvCents, selfGmvCents, selfShareBps: total > 0 ? Math.max(0, Math.min(10_000, Math.round(selfGmvCents / total * 10_000))) : 0 };
  });
  return <section className="panel market-section">
    <div className="section-header"><div><h2>自营与 POP 经营结构</h2><p>对比销售规模、商品效率、价格与转化，判断平台信用和商家专业服务的相对作用。</p></div></div>
    <div className="market-operation-grid">{data.industryReport.operationModes.map((item) => <article key={item.operationMode}><header><strong>{item.operationMode}</strong><span>{percent(item.gmvShareBps)}</span></header><b>{money(item.gmvCents)}</b><small>成交 {count(item.quantity)} · {dimensionLabel} {count(item.skuCount)} · 品牌 {count(item.brandCount)}</small><small>成交均价 {money(item.averageTransactionPriceCents)} · 单 {dimensionLabel} 销售额 {money(item.gmvPerSkuCents)}</small><small>访客 {count(item.visitors)} · 转化率 {percent(item.conversionBps)}</small></article>)}{!data.industryReport.operationModes.length && <p>暂无经营模式数据。</p>}</div>
    <div className="market-operation-trend">{monthly.map((item) => <article key={item.period}><strong>{item.period}</strong><i><b style={{ width: `${item.selfShareBps / 100}%` }} /><span style={{ width: `${100 - item.selfShareBps / 100}%` }} /></i><small>自营 {money(item.selfGmvCents)}（{percent(item.selfShareBps)}） · POP {money(item.popGmvCents)}</small></article>)}{!monthly.length && <p>暂无自营与 POP 月度结构数据。</p>}</div>
  </section>;
}

const quadrantMeta: Record<MarketIndustryReport["trafficQuadrants"][number]["quadrant"], { title: string; note: string }> = {
  high_traffic_high_conversion: { title: "高流量 · 高转化", note: "重点守住排名、库存和服务体验" },
  high_traffic_low_conversion: { title: "高流量 · 低转化", note: "优先检查价格、卖点和评价阻力" },
  low_traffic_high_conversion: { title: "低流量 · 高转化", note: "具备放量潜力，适合验证流量增量" },
  low_traffic_low_conversion: { title: "低流量 · 低转化", note: "谨慎投入，先验证产品定位" },
};

function TrafficQuadrantSection({ data }: { data: MarketOverview }) {
  const threshold = data.industryReport.trafficQuadrants[0];
  return <section className="panel market-section">
    <div className="section-header"><div><h2>商品流量 × 转化象限</h2><p>以当前筛选商品的平均访客与平均转化率为高低分界；样例商品来自当前 TOP 榜单样本。</p></div><small>阈值：访客 {count(threshold?.visitorThreshold ?? 0)} · 转化 {percent(threshold?.conversionThresholdBps)}</small></div>
    <div className="market-quadrant-grid">{data.industryReport.trafficQuadrants.map((item) => <article className={item.quadrant} key={item.quadrant}><header><strong>{quadrantMeta[item.quadrant].title}</strong><span>{count(item.productCount)} 个商品</span></header><b>{money(item.gmvCents)}</b><small>{quadrantMeta[item.quadrant].note}</small><small>象限转化 {percent(item.conversionBps)} · 访客 {count(item.visitors)}</small><div>{item.examples.map((example) => <span key={example.skuCode} title={example.productName}>{example.productName || example.skuCode}</span>)}</div></article>)}{!data.industryReport.trafficQuadrants.length && <p>当前数据不足以形成流量转化象限。</p>}</div>
  </section>;
}

function ProductSignalSection({ data }: { data: MarketOverview }) {
  const signals = data.industryReport.productSignals;
  const groups = [...new Set(signals.signals.map((item) => item.group))];
  return <section className="panel market-section">
    <div className="section-header"><div><h2>爆款标题与产品特征信号</h2><p>从 {count(signals.sampleSize)} 个去重商品身份的标题和已确认细分类目提取，不代表评价口碑或真实服务履约。</p></div></div>
    <div className="market-signal-groups">{groups.map((group) => <article key={group}><strong>{group}</strong><div>{signals.signals.filter((item) => item.group === group).map((item) => <span key={item.label} title={item.examples.join(" / ")}>{item.label}<b>{percent(item.shareBps)}</b><small>{count(item.count)}</small></span>)}</div></article>)}{!groups.length && <p>当前商品标题尚未识别到可汇总特征。</p>}</div>
  </section>;
}

function OpportunityMatrixSection({ data }: { data: MarketOverview }) {
  const dimensionLabel = marketReportDimensionLabel(data);
  return <section className="panel market-section market-opportunity-panel">
    <div className="section-header"><div><h2>细分类目 × 价格带 × 场景机会矩阵</h2><p>评分综合规模、最新月增长、单 {dimensionLabel} 效率、转化和自营门槛；未包含成本、推广、退货、复购与真实售后，不能直接替代投资决策。</p></div></div>
    <div className="data-table-wrap"><table className="data-table" data-column-filter-scope="none"><thead><tr><th>建议</th><th>评分</th><th>细分类目</th><th>价格带</th><th>场景</th><th>销售额/占比</th><th>最新月增长</th><th>转化率</th><th>{dimensionLabel}/品牌</th><th>自营占比</th><th>价格完整性</th><th>主要依据</th></tr></thead><tbody>{data.industryReport.opportunities.map((item) => <tr key={`${item.subcategory}-${item.priceBand}`}><td><span className={`market-opportunity-status ${item.recommendation === "建议进入" ? "enter" : item.recommendation === "谨慎回避" ? "avoid" : "watch"}`}>{item.recommendation}</span>{!item.decisionReady && <small>待补齐数据</small>}</td><td><strong>{item.score}</strong></td><td>{item.subcategory}</td><td>{item.priceBand}</td><td>{item.scenario}</td><td>{money(item.gmvCents)}<small>{percent(item.gmvShareBps)}</small></td><td>{growthPercent(item.growthBps)}</td><td>{percent(item.conversionBps)}</td><td>{count(item.skuCount)} / {count(item.brandCount)}</td><td>{percent(item.selfOperatedShareBps)}</td><td>{item.pendingPriceShareBps ? `待确认 ${percent(item.pendingPriceShareBps)}` : "已覆盖"}</td><td>{item.reasons.join("；") || "暂无显著信号"}</td></tr>)}{!data.industryReport.opportunities.length && <tr><td colSpan={12}><div className="table-state">当前筛选范围没有可评级的细分类目与价格带组合。</div></td></tr>}</tbody></table></div>
  </section>;
}

function IndustryDataGapSection({ data }: { data: MarketOverview }) {
  return <section className="panel market-section">
    <div className="section-header"><div><h2>消费者、服务、利润与合规补充清单</h2><p>榜单数据无法回答的部分明确保持“待补充”，不使用标题、样例或估算冒充真实结论。</p></div></div>
    <div className="market-data-gap-grid">{data.industryReport.externalDataGaps.map((item) => <article key={item.key}><header><strong>{item.label}</strong><span>{item.status}</span></header><p>{item.note}</p></article>)}</div>
  </section>;
}

function RankingTable({ data, compareKeys, loadingMore, onLoadMore, onPrevious, onToggleCompare, onTrend, onOpenCompare }: {
  data: MarketOverview;
  compareKeys: string[];
  loadingMore: boolean;
  onLoadMore: () => void;
  onPrevious: () => void;
  onToggleCompare: (item: MarketItem) => void;
  onTrend: (item: MarketItem) => void;
  onOpenCompare: () => void;
}) {
  const { items, pagination } = data;
  const hasMore = pagination.page < pagination.pageCount;
  return <section className="panel market-table-panel">
    <div className="section-header"><div><h2>商品榜单</h2><p>每页读取 20 条；翻页时保留已选择的竞品。标题下方展示周期、SKU ID、POP/自营、品牌、细分类目和确认状态。</p></div><div className="market-ranking-actions"><span className="soft-tag">本页 {count(items.length)} 条 · 共 {count(pagination.total)} 条</span><button type="button" className="secondary-button" disabled={compareKeys.length < 2} onClick={onOpenCompare}>进入竞品对比{compareKeys.length ? `（${compareKeys.length}）` : ""}</button></div></div>
    <div className="data-table-wrap"><table className="data-table market-ranking-table market-ranking-table-v2"><thead><tr>
      <th>对比</th><th>排名</th><th>商品主图和标题</th><th>销售额</th><th>成交件数</th><th>市场定位价（主图）</th><th>成交均价</th><th>访客</th><th>转化率</th><th>排名变化</th><th>趋势操作</th>
    </tr></thead><tbody>{items.map((item) => <tr key={item.id}>
      <td><button type="button" className={`market-compare-check ${compareKeys.includes(marketCompareSelectionKey(item)) ? "active" : ""}`} onClick={() => onToggleCompare(item)} aria-label={`选择对比 ${item.productName || item.skuCode}`}>{compareKeys.includes(marketCompareSelectionKey(item)) ? "✓" : "+"}</button></td>
      <td><strong>{item.rank ? `#${item.rank}` : "-"}</strong><small>{item.rankingDimension}</small></td>
      <td><div className="market-product-cell">{item.imageUrl ? (marketProductHref(item.productUrl, item.skuCode) ? <a href={marketProductHref(item.productUrl, item.skuCode)} target="_blank" rel="noreferrer"><img src={item.imageUrl} alt={item.productName || item.skuCode} loading="lazy" /></a> : <span><img src={item.imageUrl} alt={item.productName || item.skuCode} loading="lazy" /></span>) : <span>图</span>}<div>{marketProductHref(item.productUrl, item.skuCode) ? <a className="market-product-title-link" href={marketProductHref(item.productUrl, item.skuCode)} target="_blank" rel="noreferrer">{item.productName || "未命名商品"}</a> : <strong>{item.productName || "未命名商品"}</strong>}<small>{item.periodStart} 至 {item.periodEnd} · 上榜 {count(item.periodCount)} 期 · {item.rankingDimension} {item.skuCode}</small><small>{item.operationMode} · {item.brand || "品牌待识别"} · {item.subcategory || "未分类"} · {item.marketPriceSource}</small></div></div></td>
      <td><strong>{money(item.gmvCents)}</strong><small>当前 TOP 榜单覆盖口径</small></td>
      <td>{count(item.quantity)}</td>
      <td><strong>{money(item.marketPriceCents)}</strong><small>{item.marketPriceSource}</small></td>
      <td><strong>{money(item.averageTransactionPriceCents)}</strong><small>{item.discountBps === null ? "无折让率" : `${item.discountReference ? "参考" : "成交"}折让率 ${percent(item.discountBps)}`}</small></td>
      <td>{count(item.visitors)}</td><td>{percent(item.conversionBps)}</td>
      <td>{item.rankChange === null ? "-" : item.rankChange > 0 ? `↑${item.rankChange}` : item.rankChange < 0 ? `↓${Math.abs(item.rankChange)}` : "持平"}</td>
      <td><button type="button" className="row-action" onClick={() => onTrend(item)}>查看趋势</button></td>
    </tr>)}{items.length === 0 && <tr><td colSpan={11}><div className="table-state">当前市场周期和筛选条件下暂无商品数据，请调整条件或选择“全部时间”。</div></td></tr>}</tbody></table></div>
    {pagination.total > 0 && <footer className="market-ranking-load-more"><span>第 {pagination.page} / {pagination.pageCount} 页{loadingMore ? " · 正在读取…" : ""}</span><button type="button" className="secondary-button" disabled={pagination.page <= 1 || loadingMore} onClick={onPrevious}>上一页</button><button type="button" className="secondary-button" disabled={!hasMore || loadingMore} onClick={onLoadMore}>下一页</button></footer>}
  </section>;
}

function TrendDrawer({ item, onClose }: { item: MarketItem; onClose: () => void }) {
  const [data, setData] = useState<TrendPayload | null>(null);
  const [error, setError] = useState("");
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ skuCode: item.skuCode, category: item.category, scope: item.scope, dimension: item.rankingDimension });
    void fetch(`/api/market/trend?${params}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json().catch(() => null) as TrendPayload | null;
        if (!response.ok || !payload) throw new Error(payload?.error || "趋势读取失败");
        setData(payload);
      })
      .catch((reason) => { if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "趋势读取失败"); });
    return () => controller.abort();
  }, [item]);
  return <Dialog open onClose={onClose} dialogId="market-trend-dialog" ariaLabel="商品月度趋势" className="market-trend-drawer" initialFocusRef={closeButtonRef}>
    <header><div><span>{item.skuCode}</span><h3>{item.productName || item.skuCode}</h3><small>{item.category} · {item.rankingDimension}</small></div><button ref={closeButtonRef} type="button" onClick={onClose} aria-label="关闭商品月度趋势">×</button></header>
    {error && <div className="market-feedback error">{error}</div>}
    {!data && !error && <div className="table-state"><span className="state-spinner" />正在读取最近 120 个月的月度趋势…</div>}
    {data && <><small>{data.truncated ? `展示最近 ${count(data.items.length)} / 共 ${count(data.totalMonths)} 个月` : `展示全部 ${count(data.totalMonths)} 个月`}</small><div className="data-table-wrap"><table className="data-table" data-column-filter-scope={data.truncated ? "none" : "full"} data-column-filter-total={data.totalMonths}><thead><tr><th>月份</th><th>销售额</th><th>成交件数</th><th>市场定位价</th><th>成交均价</th><th>排名</th><th>POP/自营</th><th>价格确认状态</th></tr></thead><tbody>{data.items.map((row) => <tr key={`${row.month}-${row.rank}`}>
      <td>{String(row.month)}</td><td>{money(Number(row.gmvCents ?? 0))}</td><td>{count(Number(row.quantity ?? 0))}</td><td>{money(row.marketPriceCents === null ? null : Number(row.marketPriceCents))}</td><td>{money(row.averageTransactionPriceCents === null ? null : Number(row.averageTransactionPriceCents))}</td><td>{row.rank === null ? "-" : `#${row.rank}`}</td><td>{String(row.operationMode)}</td><td>{String(row.priceStatus)} · {String(row.confirmationStatus)}</td>
    </tr>)}</tbody></table></div></>}
  </Dialog>;
}

function CompareWorkspace({ selections, onClear, onRemoveCompare, onGoRanking, query, categories, scopes, rankingDimensions, operationModes, brands, subcategories, priceBands, startDate, endDate }: {
  selections: MarketCompareSelection[]; onClear: () => void; onRemoveCompare: (sku: string) => void; onGoRanking: () => void;
  query: string; categories: string[]; scopes: string[]; rankingDimensions: string[]; operationModes: string[]; brands: string[]; subcategories: string[]; priceBands: string[];
  startDate: string; endDate: string;
}) {
  const request = useMemo(() => {
    const params = new URLSearchParams({ view: "compare" });
    selections.forEach((selection) => params.append("selection", JSON.stringify(selection)));
    if (query.trim()) params.set("q", query.trim());
    categories.forEach((value) => params.append("category", value));
    scopes.forEach((value) => params.append("scope", value));
    rankingDimensions.forEach((value) => params.append("rankingDimension", value));
    operationModes.forEach((value) => params.append("operationMode", value));
    brands.forEach((value) => params.append("brand", value));
    subcategories.forEach((value) => params.append("subcategory", value));
    priceBands.forEach((value) => params.append("priceBand", value));
    if (startDate) params.set("startDate", startDate);
    if (endDate) params.set("endDate", endDate);
    const requestKey = params.toString();
    return { requestKey, url: selections.length < 2 ? null : `/api/market/master?${requestKey}` };
  }, [selections, query, categories, scopes, rankingDimensions, operationModes, brands, subcategories, priceBands, startDate, endDate]);
  const [result, setResult] = useState<{ requestKey: string; payload: ComparePayload | null; error: string } | null>(null);
  const currentResult = result?.requestKey === request.requestKey ? result : null;
  const data = currentResult?.payload ?? result?.payload ?? null;
  const error = currentResult?.error ?? "";
  const loading = Boolean(request.url && result?.requestKey !== request.requestKey);
  const requestGeneration = useRef(0);
  useEffect(() => {
    const requestId = beginLatestRequest(requestGeneration);
    if (!request.url) return;
    const controller = new AbortController();
    void fetch(request.url, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json().catch(() => null) as ComparePayload | null;
        if (!response.ok || !payload) throw new Error(payload?.error || "商品对比读取失败");
        if (requestId !== requestGeneration.current) return;
        setResult({ requestKey: request.requestKey, payload, error: "" });
      })
      .catch((reason) => {
        if (requestId !== requestGeneration.current) return;
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setResult((current) => ({ requestKey: request.requestKey, payload: current?.payload ?? null, error: reason instanceof Error ? reason.message : "商品对比读取失败" }));
        }
      });
    return () => { invalidateLatestRequest(requestGeneration); controller.abort(); };
  }, [request]);
  if (selections.length < 2) return <section className="panel market-compare-workspace market-compare-empty">
    <div><span className="eyebrow">COMPETITOR BENCHMARK</span><h2>竞品对比工作区</h2><p>请先从商品榜单勾选 2–5 个 SKU。系统将使用当前筛选口径，对比销售额、成交件数、主图价格、成交均价、访客、转化率、排名和月度趋势。</p></div>
    <div className="market-compare-selection"><strong>已选择 {selections.length} / 5</strong>{selections.map((item) => <button type="button" key={marketCompareSelectionKey(item)} onClick={() => onRemoveCompare(marketCompareSelectionKey(item))}>{item.productName || item.skuCode}<span>×</span></button>)}</div>
    <div><button type="button" className="primary-button" onClick={onGoRanking}>前往商品榜单选择</button>{selections.length > 0 && <button type="button" className="row-action" onClick={onClear}>清空</button>}</div>
  </section>;
  const compared = selections.map((selection) => data?.items.find((item) => marketCompareSelectionKey(item) === marketCompareSelectionKey(selection))).filter(Boolean) as NonNullable<ComparePayload>["items"];
  const missingSelections = data?.missingSelections ?? [];
  const maxTrend = Math.max(1, ...compared.flatMap((item) => item.trend.slice(-12).map((row) => Number(row.gmvCents ?? 0))));
  return <section className="panel market-compare-workspace data-refresh-region" aria-busy={loading}>
    <header><div><span className="eyebrow">COMPETITOR BENCHMARK</span><h2>竞品对比工作区</h2><p>主指标按当前筛选范围完整汇总；月度火花图只展示最近 12 个月。</p></div><div><strong>已选择 {selections.length} / 5</strong><button type="button" className="secondary-button" onClick={onGoRanking}>继续选择</button><button type="button" className="row-action" onClick={onClear}>清空</button></div></header>
    <div className="market-compare-selection">{selections.map((item) => <button type="button" key={marketCompareSelectionKey(item)} onClick={() => onRemoveCompare(marketCompareSelectionKey(item))}>{item.productName || item.skuCode}<span>×</span></button>)}</div>
    {error && <small className="red-text">{error}</small>}
    {loading && !data && !error && <small>正在读取对比数据...</small>}
    {data && missingSelections.length > 0 && <small className="red-text">当前筛选范围无数据：{missingSelections.map((item) => `${item.skuCode}（${item.scope}）`).join("、")}。可调整筛选，或从上方移除。</small>}
    {data && <div className="market-compare-grid market-compare-grid-live">
      <div className="metric-labels"><strong>指标</strong>{["销售额", "成交件数", "市场定位价", "成交均价", "访客", "转化率", "最好排名", "月度趋势"].map((label) => <span key={label}>{label}</span>)}</div>
      {compared.map((item) => <article key={marketCompareSelectionKey(item)}>
        <strong title={item.productName}>{item.productName || item.skuCode}</strong><small>{item.skuCode} · {item.scope} · {item.brand || "-"} · {item.rankingDimension}{item.trendTruncated && <><br />服务端趋势最近 120 / 共 {count(item.trendTotalMonths)} 个月</>}</small><button type="button" aria-label={`移除 ${item.productName || item.skuCode}`} onClick={() => onRemoveCompare(marketCompareSelectionKey(item))}>×</button>
        <span>{money(item.gmvCents)}</span><span>{count(item.quantity)}</span><span>{money(item.marketPriceCents)}</span><span>{money(item.averageTransactionPriceCents)}</span>
        <span>{count(item.visitors)}</span><span>{percent(item.conversionBps)}</span><span>{item.bestRank ? `#${item.bestRank}` : "-"}</span>
        <span><i className="market-compare-spark">{item.trend.slice(-12).map((row) => <b key={String(row.month)} style={{ height: `${Math.max(4, Number(row.gmvCents ?? 0) / maxTrend * 28)}px` }} title={`${String(row.month)} ${money(Number(row.gmvCents ?? 0))}`} />)}</i></span>
      </article>)}
    </div>}
  </section>;
}

export function MarketDataImportPanel({ currentUser, data, onImported }: { currentUser: CurrentUser; data: MarketSettingsStatus | null; onImported?: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [sourceType, setSourceType] = useState("market_ranking");
  const [category, setCategory] = useState("");
  const [scope, setScope] = useState("全部SKU");
  const [priceBandFilter, setPriceBandFilter] = useState("全部");
  const [periodStart, setPeriodStart] = useState(data?.dataRange.startDate ?? shanghaiToday());
  const [periodEnd, setPeriodEnd] = useState(data?.dataRange.endDate ?? shanghaiToday());
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const importRequestRef = useRef<AbortController | null>(null);
  const imageCachePollRef = useRef<AbortController | null>(null);
  const imageCachePollGenerationRef = useRef(0);
  useEffect(() => () => {
    importRequestRef.current?.abort();
    imageCachePollRef.current?.abort();
    imageCachePollGenerationRef.current += 1;
  }, []);
  const startImageCachePolling = (initialJob: MarketImageCacheJob, importMessage: string) => {
    imageCachePollRef.current?.abort();
    const controller = new AbortController();
    const generation = imageCachePollGenerationRef.current + 1;
    imageCachePollGenerationRef.current = generation;
    imageCachePollRef.current = controller;
    void (async () => {
      try {
        const polled = await pollMarketImageCacheJob(initialJob, controller.signal, (job) => {
          if (imageCachePollGenerationRef.current !== generation || imageCachePollRef.current !== controller) return;
          setFeedback(`${importMessage}；${imageCacheJobProgress(job)}`);
        });
        if (imageCachePollGenerationRef.current !== generation || imageCachePollRef.current !== controller) return;
        if (polled.job.status === "failed") {
          setFeedback(`${importMessage}；后台图片缓存失败：${polled.job.errorMessage || "请在系统设置中重试"}`);
        } else if (polled.timedOut) {
          setFeedback(`${importMessage}；图片缓存仍在后台运行，${imageCacheJobProgress(polled.job)}`);
        } else {
          setFeedback(`${importMessage}；商品图处理完成：已缓存 ${polled.job.cached} 张，失败 ${polled.job.failed} 张`);
          onImported?.();
        }
      } catch (reason) {
        if (controller.signal.aborted || imageCachePollGenerationRef.current !== generation) return;
        setFeedback(`${importMessage}；后台进度读取中断：${reason instanceof Error ? reason.message : "请稍后刷新查看"}`);
      } finally {
        if (imageCachePollRef.current === controller) imageCachePollRef.current = null;
      }
    })();
  };
  const upload = async () => {
    if (!file) { setError("请先选择 XLS、XLSX 或 CSV 文件"); return; }
    imageCachePollRef.current?.abort();
    imageCachePollGenerationRef.current += 1;
    const controller = new AbortController();
    importRequestRef.current = controller;
    setBusy(true); setError(""); setFeedback("");
    try {
      const form = new FormData();
      form.set("file", file); form.set("sourceType", sourceType); form.set("category", category);
      form.set("scope", scope); form.set("priceBandFilter", priceBandFilter); form.set("periodStart", periodStart); form.set("periodEnd", periodEnd);
      const response = await fetch("/api/market/import", { method: "POST", body: form, signal: controller.signal });
      const payload = await response.json().catch(() => null) as {
        error?: string;
        message?: string;
        batch?: { id: string };
        imageCacheJob?: MarketImageCacheJob & { maintenanceFailed?: boolean };
      } | null;
      if (!response.ok) throw new Error(payload?.error || "导入失败");
      const importMessage = payload?.message || "市场数据导入完成";
      setFeedback(importMessage);
      setFile(null); onImported?.();
      if (payload?.imageCacheJob?.maintenanceFailed) {
        setFeedback(`${importMessage}；图片缓存任务创建失败，可在系统设置中重试`);
      } else if (payload?.imageCacheJob?.id) {
        const initialJob = payload.imageCacheJob;
        setFeedback(`${importMessage}；图片缓存已转入后台，${imageCacheJobProgress(initialJob)}`);
        startImageCachePolling(initialJob, importMessage);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "导入失败");
    } finally {
      if (importRequestRef.current === controller) {
        importRequestRef.current = null;
        setBusy(false);
      }
    }
  };
  return <section className="panel market-import-card"><div className="section-header"><div><h2>市场数据导入</h2><p>位于市场分析 → 系统和 AI 设置；导入会保留原榜单、图片缓存和已确认价格。</p></div></div>
    {!currentUser || currentUser.role !== "admin" ? <div className="market-import-permission">仅管理员可导入市场数据。</div> : <>
      {(feedback || error) && <div className={`market-feedback ${error ? "error" : "success"}`}>{error || feedback}</div>}
      <div className="market-import-form">
        <label><span>数据类型</span><div className="market-segmented"><button type="button" className={sourceType === "market_ranking" ? "active" : ""} onClick={() => setSourceType("market_ranking")}>商品榜单</button><button type="button" className={sourceType === "sku_catalog" ? "active" : ""} onClick={() => setSourceType("sku_catalog")}>SKU 资料</button></div></label>
        <label><span>默认类目</span><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="文件无类目列时使用" /></label>
        <label><span>默认口径</span><input value={scope} onChange={(event) => setScope(event.target.value)} placeholder="全部SKU / POP SKU / 自营 SPU" /></label>
        <label><span>榜单价格段</span><input value={priceBandFilter} onChange={(event) => setPriceBandFilter(event.target.value)} placeholder="全部 / 0-500 / 500-1000" /></label>
        <label><span>周期开始</span><input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} /></label>
        <label><span>周期结束</span><input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} /></label>
        <label className="market-file-picker"><span>数据文件</span><input type="file" accept=".xls,.xlsx,.csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><strong>{file ? file.name : "选择 XLS / XLSX / CSV"}</strong><small>最大 25MB、5000 条数据；SKU/SPU 维度会独立保存。</small></label>
      </div><button type="button" className="primary-button" disabled={busy} onClick={() => void upload()}>{busy ? "正在导入…" : "开始导入"}</button>
    </>}
  </section>;
}

export function MarketWorkflowPanel({ data }: { data: MarketSettingsStatus | null }) {
  return <section className="panel market-batch-list"><div className="section-header"><div><h2>AI 数据工作流与任务记录</h2><p>自动下载导入、结构化校验、缺失字段识别、人工确认和发布均在此跟踪。</p></div></div>
    <div className="market-workflow-steps">{["自动下载导入", "结构化校验", "缺失字段识别", "人工确认", "发布到分析"].map((step, index) => <article key={step}><strong>{index + 1}</strong><span>{step}</span></article>)}</div>
    {data?.batches.map((batch) => <article key={batch.id}><div><strong>{batch.fileName}</strong><small>{batch.sourceType} · {batch.completedAt ? new Date(batch.completedAt).toLocaleString("zh-CN") : "处理中"}</small></div><span>{count(batch.rowCount)} 行</span><small>新增 {count(batch.insertedCount)} · 更新 {count(batch.updatedCount)} · 告警 {count(batch.warningCount)}</small></article>)}
    {!data?.batches.length && <p className="soft-text">暂无市场数据任务记录。</p>}
  </section>;
}

export function MarketMasterAdminPanel(props: {
  currentUser: CurrentUser;
  mode?: MarketSettingsTab;
}) {
  return <Suspense fallback={<section className="panel data-state" role="status">
    <span className="state-spinner" aria-hidden="true" />
    <strong>正在加载市场主数据管理…</strong>
  </section>}>
    <LazyMarketMasterAdminPanel {...props} />
  </Suspense>;
}

const EMPTY_IMAGE_CACHE_STATS: MarketOverview["imageCache"] = { total: 0, cached: 0, failed: 0, pending: 0 };

function MarketSettingsWorkspace({ currentUser, data, onImported }: { currentUser: CurrentUser; data: MarketSettingsStatus | null; onImported: () => void }) {
  const [tab, setTab] = useState<MarketSettingsTab>("database");
  const [databaseArea, setDatabaseArea] = useState<"master" | "annotation">("master");
  const [systemKpis, setSystemKpis] = useState<MarketSystemKpis | null>(null);
  const [systemKpisError, setSystemKpisError] = useState("");
  const [cacheStats, setCacheStats] = useState(data?.imageCache ?? EMPTY_IMAGE_CACHE_STATS);
  const [cacheRunning, setCacheRunning] = useState(false);
  const [cacheNotice, setCacheNotice] = useState(data ? "" : "图片统计将在后台维护任务启动后更新");
  const [cacheError, setCacheError] = useState("");
  const cachePollControllerRef = useRef<AbortController | null>(null);
  const cachePollStoppedRef = useRef(false);
  const isAdmin = currentUser?.role === "admin";
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch("/api/market/master?view=system_kpis", { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (MarketSystemKpis & { error?: string }) | null;
        if (!response.ok || !payload) throw new Error(payload?.error || "全库统计读取失败");
        setSystemKpis(payload);
        setSystemKpisError("");
      } catch (reason) {
        if (controller.signal.aborted) return;
        setSystemKpisError(reason instanceof Error ? reason.message : "全库统计读取失败");
      }
    })();
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!cacheRunning && data?.imageCache) setCacheStats(data.imageCache);
  }, [cacheRunning, data?.imageCache]);
  useEffect(() => () => cachePollControllerRef.current?.abort(), []);
  const refreshImageCache = async () => {
    if (!isAdmin || cacheRunning) return;
    cachePollControllerRef.current?.abort();
    const controller = new AbortController();
    cachePollControllerRef.current = controller;
    cachePollStoppedRef.current = false;
    setCacheRunning(true);
    setCacheError("");
    try {
      setCacheNotice("正在创建或续接后台图片缓存任务…");
      const response = await fetch("/api/market/images/cache", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => null) as { error?: string; job?: MarketImageCacheJob } | null;
      if (!response.ok || !payload?.job) throw new Error(payload?.error || "图片缓存任务创建失败");
      setCacheStats(cacheStatsFromJob(payload.job));
      setCacheNotice(payload.job.status === "completed"
        ? `图片后台维护已是最新状态：已缓存 ${count(payload.job.cached)} 张`
        : `后台任务已${payload.job.status === "running" ? "续接" : "排队"}：${imageCacheJobProgress(payload.job)}`);
      const polled = await pollMarketImageCacheJob(payload.job, controller.signal, (job) => {
        setCacheStats(cacheStatsFromJob(job));
        setCacheNotice(`后台任务${job.status === "running" ? "正在执行" : "已排队"}：${imageCacheJobProgress(job)}`);
      });
      if (polled.job.status === "failed") throw new Error(polled.job.errorMessage || "后台图片缓存任务失败");
      setCacheNotice(polled.timedOut
        ? `图片缓存仍在后台运行；${imageCacheJobProgress(polled.job)}`
        : `图片缓存完成：已缓存 ${count(polled.job.cached)} 张，失败 ${count(polled.job.failed)} 张`);
    } catch (reason) {
      if (controller.signal.aborted) {
        if (cachePollStoppedRef.current) setCacheNotice("已停止查看进度；后台缓存任务仍会继续运行");
      } else {
        setCacheError(reason instanceof Error ? reason.message : "图片缓存刷新失败");
      }
    } finally {
      if (cachePollControllerRef.current === controller) cachePollControllerRef.current = null;
      setCacheRunning(false);
      onImported();
    }
  };
  const cachePercent = cacheStats.total > 0 ? Math.min(100, Math.round(cacheStats.cached / cacheStats.total * 100)) : 0;
  const systemKpiCards = [
    { key: "marketIdentityTotal", label: "市场商品身份", note: "类目、范围、SKU/SPU 维度与编码全库去重" },
    { key: "pendingPriceCount", label: "待确认价格", note: "仍有未确认月度价格的商品身份" },
    { key: "pendingAiCount", label: "待 AI 标注总量", note: "尚无完整 AI 结果的商品身份；不等于当前可新建任务数" },
    { key: "completedAiCount", label: "已生成 AI 结果", note: "已有模型输出，含待人工复核" },
    { key: "sameImageReuseCount", label: "同图直接复用", note: "已有同图标准价，不调用 AI" },
    { key: "priceOnlyRecognitionCount", label: "新图仅识别价格", note: "同 SKUID 有有效历史分类" },
    { key: "fullRecognitionCount", label: "完整分类和价格", note: "新 SKUID 或没有有效历史分类" },
    { key: "blockedRecognitionCount", label: "暂不可自动识别", note: "无图、非 SKU、Prompt 不可用或失败封顶" },
  ] as const;
  const tabs: Array<{ key: MarketSettingsTab; label: string; note: string }> = [
    { key: "database", label: "SKU 数据库", note: "主数据、价格与 AI 入库" },
    { key: "subcategory", label: "细分品类设置", note: "按三级类目统一维护" },
    { key: "brand", label: "品牌确认", note: "标题识别与人工修订" },
    { key: "mapping", label: "映射配置", note: "别名、品类与价格带" },
    { key: "data", label: "数据配置", note: "导入、下载与审计" },
  ];
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(':scope > [role="tab"]') ?? []);
    const index = tabs.indexOf(event.currentTarget);
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : -1;
    if (nextIndex >= 0) { event.preventDefault(); tabs[nextIndex]?.focus(); tabs[nextIndex]?.click(); }
  };
  return <section className="market-settings-workspace">
    <article className="panel market-settings-intro"><div><span className="eyebrow">MARKET OPERATIONS & AI</span><h2>系统和 AI 设置</h2><p>以下指标按市场数据全库独立统计，不受商品榜单的日期、类目、范围或维度筛选影响；“待 AI 标注总量”按最高算力需求归入下方四种互斥路径，四项合计与总量一致。</p></div><div>{systemKpiCards.map((item) => <span className="market-system-kpi" key={item.key} title={systemKpisError}><strong>{systemKpis ? count(systemKpis[item.key]) : "—"}</strong><em>{item.label}</em><small>{systemKpisError ? "全库统计读取失败" : item.note}</small></span>)}<div className="market-image-cache-card"><strong>{count(cacheStats.pending)}</strong><em>待后台处理图片</em><div className="market-image-cache-progress" role="progressbar" aria-label="图片缓存进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={cachePercent}><b style={{ width: `${cachePercent}%` }} /></div><small className={cacheError ? "error" : ""}>{cacheError || cacheNotice || `已缓存 ${count(cacheStats.cached)} / ${count(cacheStats.total)}，失败 ${count(cacheStats.failed)}`}</small><div className="market-image-cache-actions"><button type="button" className="primary-button" disabled={!isAdmin || cacheRunning} onClick={() => void refreshImageCache()}>{cacheRunning ? "正在查看后台进度…" : cacheStats.pending > 0 ? "启动后台图片缓存" : "检查后台图片维护"}</button>{cacheRunning && <button type="button" className="secondary-button" onClick={() => { cachePollStoppedRef.current = true; cachePollControllerRef.current?.abort(); }}>停止查看</button>}</div></div></div></article>
    <nav className="panel market-settings-tabs" role="tablist" aria-label="市场系统和 AI 设置子板块">{tabs.map((item) => <button type="button" role="tab" id={`market-settings-tab-${item.key}`} aria-controls={`market-settings-panel-${item.key}`} aria-selected={tab === item.key} tabIndex={tab === item.key ? 0 : -1} key={item.key} className={tab === item.key ? "active" : ""} onClick={() => setTab(item.key)} onKeyDown={onTabKeyDown}><strong>{item.label}</strong><small>{item.note}</small></button>)}</nav>
    <div role="tabpanel" id={`market-settings-panel-${tab}`} aria-labelledby={`market-settings-tab-${tab}`}>
      {tab === "database" ? <>
        <nav className="panel market-database-areas" role="tablist" aria-label="SKU 数据库工作区"><button type="button" role="tab" id="market-database-tab-master" aria-controls="market-database-panel-master" aria-selected={databaseArea === "master"} tabIndex={databaseArea === "master" ? 0 : -1} className={databaseArea === "master" ? "active" : ""} onClick={() => setDatabaseArea("master")} onKeyDown={onTabKeyDown}><strong>主数据与价格</strong><small>统一筛选、查看和编辑 SKU/SPU</small></button><button type="button" role="tab" id="market-database-tab-annotation" aria-controls="market-database-panel-annotation" aria-selected={databaseArea === "annotation"} tabIndex={databaseArea === "annotation" ? 0 : -1} className={databaseArea === "annotation" ? "active" : ""} onClick={() => setDatabaseArea("annotation")} onKeyDown={onTabKeyDown}><strong>AI 标注与批量入库</strong><small>筛选候选、列表/大图复核并入库</small></button></nav>
        <div role="tabpanel" id={`market-database-panel-${databaseArea}`} aria-labelledby={`market-database-tab-${databaseArea}`}>
          {databaseArea === "annotation" ? <Suspense fallback={<div className="panel data-state" role="status"><span className="state-spinner" aria-hidden="true" /><strong>正在加载 AI 标注工作区…</strong></div>}><MarketAnnotationView currentUser={currentUser} embedded /></Suspense> : <MarketMasterAdminPanel currentUser={currentUser} mode="database" />}
        </div>
      </> : <MarketMasterAdminPanel currentUser={currentUser} mode={tab} />}
      {tab === "data" && <><MarketDataImportPanel currentUser={currentUser} data={data} onImported={onImported} /><MarketWorkflowPanel data={data} /></>}
    </div>
  </section>;
}

export default function MarketView({ customStartDate, customEndDate, currentUser, moduleView, onModuleViewChange, onApplyPeriod }: {
  customStartDate: string;
  customEndDate: string;
  currentUser: CurrentUser;
  moduleView: ModuleViewKey<"market">;
  onModuleViewChange: (view: ModuleViewKey<"market">) => void;
  onApplyPeriod?: (startDate: string, endDate: string) => void;
}) {
  const activeSection: MarketSectionKey = moduleView;
  const initialRequestKey = defaultMarketRankingParams(customStartDate, customEndDate).toString();
  const initialOverview = cachedMarketOverview(initialRequestKey);
  const [loadedData, setData] = useState<MarketOverview | null>(initialOverview);
  const [dataRequestKey, setDataRequestKey] = useState(initialRequestKey);
  const [loadedAt, setLoadedAt] = useState("");
  const [filterOptions, setFilterOptions] = useState<MarketOverview["filters"]>(initialOverview?.filters ?? EMPTY_MARKET_FILTERS);
  const [filterError, setFilterError] = useState("");
  const [draftStart, setDraftStart] = useState(customStartDate);
  const [draftEnd, setDraftEnd] = useState(customEndDate);
  const [loading, setLoading] = useState(activeSection !== "settings" && activeSection !== "compare" && !initialOverview);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [scopes, setScopes] = useState<string[]>([]);
  const [dimensions, setDimensions] = useState<string[]>(["SKU"]);
  const [operationModes, setOperationModes] = useState<string[]>([]);
  const [brands, setBrands] = useState<string[]>([]);
  const [subcategories, setSubcategories] = useState<string[]>([]);
  const [priceBands, setPriceBands] = useState<string[]>([]);
  const marketStartDate = customStartDate;
  const marketEndDate = customEndDate;
  const [compareSelections, setCompareSelections] = useState<MarketCompareSelection[]>([]);
  const compareKeys = useMemo(() => compareSelections.map(marketCompareSelectionKey), [compareSelections]);
  const [trendItem, setTrendItem] = useState<MarketItem | null>(null);
  useAiPageDetails("market", {
    period: { startDate: marketStartDate, endDate: marketEndDate },
    filters: {
      query: query.trim(), categories, scope: scopes, rankingDimension: dimensions, operationMode: operationModes, brands, subcategories, priceBands,
      selectedIds: trendItem ? [marketCompareSelectionKey(trendItem)] : activeSection === "compare" ? compareSelections.map(marketCompareSelectionKey) : [],
    },
  }, activeSection !== "settings");

  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => { setDraftStart(customStartDate); setDraftEnd(customEndDate); }, [customStartDate, customEndDate]);
  useEffect(() => {
    if (activeSection === "settings" || activeSection === "compare") return;
    const controller = new AbortController();
    void readMarketJson<{ filters: MarketOverview["filters"] }>("/api/market/filters", controller.signal).then((payload) => {
      if (!controller.signal.aborted) { setFilterOptions(payload.filters); setFilterError(""); }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setFilterError(reason instanceof Error ? reason.message : "筛选选项读取失败");
    });
    return () => controller.abort();
  }, [activeSection, reloadKey]);
  // One read refresh after connectivity returns; no timers that replay writes
  // or continuously poll an idle tab.
  useEffect(() => {
    const recover = () => setReloadKey((key) => key + 1);
    window.addEventListener("online", recover);
    return () => window.removeEventListener("online", recover);
  }, []);
  const [settingsStatus, setSettingsStatus] = useState<MarketSettingsStatus | null>(null);
  const [settingsStatusLoading, setSettingsStatusLoading] = useState(false);
  const [settingsStatusError, setSettingsStatusError] = useState("");
  const [settingsStatusRequestKey, setSettingsStatusRequestKey] = useState("");
  const [settingsStatusScopeKey, setSettingsStatusScopeKey] = useState("");
  const [settingsStatusReloadScope, setSettingsStatusReloadScope] = useState(-1);
  const settingsStatusGenerationRef = useRef(0);
  const loadRequestId = useRef(0);
  const loadMoreController = useRef<AbortController | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const initialLoad = useRef(true);
  const selectMarketSection = useCallback((section: MarketSectionKey) => onModuleViewChange(section), [onModuleViewChange]);
  const requestedView = activeSection === "overview" ? "full" : "ranking";
  const buildOverviewParams = useCallback((view: "ranking" | "full", page = 1) => {
    const params = new URLSearchParams();
    params.set("view", view);
    params.set("includeFilterOptions", "false");
    if (view === "ranking") {
      params.set("page", String(page));
      params.set("pageSize", String(MARKET_RANKING_PAGE_SIZE));
    }
    if (query.trim()) params.set("q", query.trim());
    categories.forEach((value) => params.append("category", value));
    scopes.forEach((value) => params.append("scope", value));
    dimensions.forEach((value) => params.append("dimension", value));
    operationModes.forEach((value) => params.append("operationMode", value));
    brands.forEach((value) => params.append("brand", value));
    subcategories.forEach((value) => params.append("subcategory", value));
    priceBands.forEach((value) => params.append("priceBand", value));
    if (marketStartDate) params.set("startDate", marketStartDate);
    if (marketEndDate) params.set("endDate", marketEndDate);
    return params;
  }, [query, categories, scopes, dimensions, operationModes, brands, subcategories, priceBands, marketStartDate, marketEndDate]);
  const currentRequestKey = buildOverviewParams(requestedView, 1).toString();
  const data = dataRequestKey === currentRequestKey ? loadedData : null;
  const load = useCallback(async (signal?: AbortSignal, maximumCacheAgeMs = 0) => {
    const requestId = ++loadRequestId.current;
    loadMoreController.current?.abort();
    setLoadingMore(false);
    setLoading(true); setError("");
    try {
      const params = buildOverviewParams(requestedView, 1);
      const payload = await requestMarketOverview(params.toString(), signal, maximumCacheAgeMs);
      if (signal?.aborted || requestId !== loadRequestId.current) return;
      setData(payload);
      setDataRequestKey(params.toString());
      setLoadedAt(new Date().toLocaleString("zh-CN", { hour12: false }));
    } catch (reason) {
      if (signal?.aborted || requestId !== loadRequestId.current) return;
      setError(reason instanceof Error ? reason.message : "市场分析数据读取失败");
    } finally {
      if (!signal?.aborted && requestId === loadRequestId.current) setLoading(false);
    }
  }, [buildOverviewParams, requestedView]);
  useEffect(() => {
    if (activeSection === "settings" || activeSection === "compare") {
      loadMoreController.current?.abort();
      loadRequestId.current += 1;
      setLoadingMore(false);
      setLoading(false);
      setError("");
      return;
    }
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadRequestId.current += 1;
    setLoadingMore(false);
    const isInitialLoad = initialLoad.current;
    const delay = isInitialLoad ? 0 : 350;
    initialLoad.current = false;
    const timer = window.setTimeout(() => void load(
      controller.signal,
      isInitialLoad ? MARKET_OVERVIEW_RECENT_PREFETCH_MS : 0,
    ), delay);
    return () => { window.clearTimeout(timer); controller.abort(); loadMoreController.current?.abort(); };
  }, [activeSection, load, reloadKey]);
  useEffect(() => {
    if (activeSection !== "settings") {
      settingsStatusGenerationRef.current += 1;
      setSettingsStatus(null);
      setSettingsStatusRequestKey("");
      setSettingsStatusScopeKey("");
      setSettingsStatusReloadScope(-1);
      setSettingsStatusLoading(false);
      setSettingsStatusError("");
      return;
    }
    const controller = new AbortController();
    const generation = settingsStatusGenerationRef.current + 1;
    settingsStatusGenerationRef.current = generation;
    const requestKey = `settings:${reloadKey}:${generation}`;
    setSettingsStatusRequestKey(requestKey);
    setSettingsStatusLoading(true);
    setSettingsStatusError("");
    void (async () => {
      try {
        const response = await fetch("/api/market/master?view=settings_status", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json().catch(() => null) as (MarketSettingsStatus & { error?: string }) | null;
        if (!response.ok || !payload) throw new Error(payload?.error || "市场设置状态读取失败");
        if (controller.signal.aborted || generation !== settingsStatusGenerationRef.current) return;
        setSettingsStatus(payload);
        setSettingsStatusScopeKey(requestKey);
        setSettingsStatusReloadScope(reloadKey);
      } catch (reason) {
        if (controller.signal.aborted || generation !== settingsStatusGenerationRef.current) return;
        setSettingsStatusError(reason instanceof Error ? reason.message : "市场设置状态读取失败");
      } finally {
        if (!controller.signal.aborted && generation === settingsStatusGenerationRef.current) setSettingsStatusLoading(false);
      }
    })();
    return () => {
      settingsStatusGenerationRef.current += 1;
      controller.abort();
    };
  }, [activeSection, reloadKey]);
  const loadMore = useCallback(async (direction = 1) => {
    if (!data || data.view !== "ranking" || loadingMore) return;
    const nextPage = data.pagination.page + direction;
    if (nextPage < 1 || nextPage > data.pagination.pageCount) return;
    loadMoreController.current?.abort();
    const controller = new AbortController();
    loadMoreController.current = controller;
    const requestId = loadRequestId.current;
    setLoadingMore(true); setError("");
    try {
      const payload = await readMarketJson<MarketOverview>(`/api/market/overview?${buildOverviewParams("ranking", nextPage)}`, controller.signal);
      if (controller.signal.aborted || requestId !== loadRequestId.current) return;
      setData(payload);
      setLoadedAt(new Date().toLocaleString("zh-CN", { hour12: false }));
    } catch (reason) {
      if (!controller.signal.aborted && requestId === loadRequestId.current) setError(reason instanceof Error ? reason.message : "更多榜单数据读取失败");
    } finally {
      if (!controller.signal.aborted && requestId === loadRequestId.current) setLoadingMore(false);
    }
  }, [data, loadingMore, buildOverviewParams]);
  const toggleCompare = (item: MarketItem) => setCompareSelections((current) => current.some((selection) => marketCompareSelectionKey(selection) === marketCompareSelectionKey(item))
    ? current.filter((selection) => marketCompareSelectionKey(selection) !== marketCompareSelectionKey(item))
    : current.length >= 5 ? current : [...current, {
      skuCode: item.skuCode,
      productName: item.productName,
      category: item.category,
      scope: item.scope,
      rankingDimension: item.rankingDimension,
    }]);
  const removeCompare = (selectionKey: string) => setCompareSelections((current) => current.filter((item) => marketCompareSelectionKey(item) !== selectionKey));
  const applyCommercialDirectDrinkingProfile = () => {
    if (!data) return;
    const profile = data.industryReport.definition.profile;
    const preferredScope = filterOptions.scopes.find((item) => item.value === "整体SKU")
      ?? filterOptions.scopes.find((item) => item.value.includes("整体") && item.value.toUpperCase().includes("SKU"))
      ?? filterOptions.scopes.find((item) => item.value === "全部SKU")
      ?? filterOptions.scopes[0];
    setQuery("");
    setCategories([profile.category]);
    setScopes([preferredScope?.value ?? "整体SKU"]);
    setDimensions(["SKU"]);
    setOperationModes([]);
    setBrands([]);
    setSubcategories([...profile.coreSubcategories]);
    setPriceBands([]);
    const reportPeriod = twelveMonthReportPeriod();
    onApplyPeriod?.(reportPeriod.startDate, reportPeriod.endDate);
    selectMarketSection("overview");
  };
  const operationOptions = useMemo(() => [{ value: "POP", count: 0 }, { value: "自营", count: 0 }, { value: "未知", count: 0 }, ...filterOptions.operationModes].filter((item, index, array) => array.findIndex((next) => next.value === item.value) === index), [filterOptions]);
  const reportDimensionLabel = data ? marketReportDimensionLabel(data) : "商品";
  const sectionCopy: Record<Exclude<MarketSectionKey, "settings">, { eyebrow: string; title: string; note: string }> = {
    ranking: { eyebrow: "PRODUCT RANKING", title: "商品榜单工作台", note: "查看 TOP 商品表现、成交均价、主图价格、排名变化和单品趋势。" },
    overview: { eyebrow: "INDUSTRY REPORT", title: "行业汇报", note: "按当前 TOP 榜单覆盖口径回答市场趋势、增长结构、竞争胜负和进入机会。" },
    compare: { eyebrow: "COMPETITOR BENCHMARK", title: "竞品对比", note: "使用统一筛选口径挑选 2–5 个 SKU，进行核心指标和月度趋势对照。" },
  };
  const activeCopy = activeSection === "settings" ? null : sectionCopy[activeSection];
  // Settings must mount from its own unfiltered status response. Reusing the
  // ranking projection here would permanently seed import dates from whichever
  // market filter happened to be active before the user opened settings.
  const settingsStatusIsCurrent = settingsStatusRequestKey !== ""
    && settingsStatusScopeKey === settingsStatusRequestKey
    && settingsStatusReloadScope === reloadKey;
  const settingsData: MarketSettingsStatus | null = activeSection === "settings"
    && settingsStatus
    && (settingsStatusIsCurrent || settingsStatusLoading || Boolean(settingsStatusError) || settingsStatusReloadScope < reloadKey)
    ? settingsStatus
    : null;
  return <div className="market-module data-refresh-region" aria-busy={loading || settingsStatusLoading}>
    <MarketSectionNav active={activeSection} compareCount={compareSelections.length} onChange={selectMarketSection} />
    <div role="tabpanel" id={`market-panel-${activeSection}`} aria-labelledby={`market-tab-${activeSection}`}>
    {activeCopy && <section className="panel market-filter-bar market-filter-bar-v2">
      <div><span className="eyebrow">{activeCopy.eyebrow}</span><h2>{activeCopy.title}</h2><p>{activeCopy.note}</p>{activeSection === "overview" && <button type="button" className="secondary-button market-report-preset" onClick={applyCommercialDirectDrinkingProfile}>应用商用直饮机核心口径 · 近12月</button>}</div>
      <div className="market-filter-controls market-filter-controls-v2">
        <div className="market-overview-period market-global-period"><span>全局统计周期</span><input type="date" aria-label="市场开始日期" value={draftStart} onChange={(event) => setDraftStart(event.target.value)} /><span>至</span><input type="date" aria-label="市场结束日期" value={draftEnd} onChange={(event) => setDraftEnd(event.target.value)} /><button type="button" className="secondary-button" disabled={!onApplyPeriod || !draftStart || !draftEnd || draftStart > draftEnd} onClick={() => onApplyPeriod?.(draftStart, draftEnd)}>应用日期</button></div>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索商品标题或 SKU" aria-label="搜索商品标题或 SKU" />
        <SearchMultiFilter label="类目" values={categories} options={filterOptions.categories} onChange={setCategories} />
        <SearchMultiFilter label="榜单范围" values={scopes} options={filterOptions.scopes} onChange={setScopes} />
        <SearchMultiFilter label="榜单维度" values={dimensions} options={filterOptions.rankingDimensions.length ? filterOptions.rankingDimensions : EMPTY_MARKET_FILTERS.rankingDimensions} onChange={setDimensions} />
        <SearchMultiFilter label="经营模式" values={operationModes} options={operationOptions} onChange={setOperationModes} />
        <SearchMultiFilter label="品牌" values={brands} options={filterOptions.brands} onChange={setBrands} />
        <SearchMultiFilter label="细分类目" values={subcategories} options={filterOptions.subcategories} onChange={setSubcategories} />
        <SearchMultiFilter label="价格带" values={priceBands} options={filterOptions.priceBands} onChange={setPriceBands} />
        <button type="button" className="secondary-button" disabled={loading} onClick={() => setReloadKey((key) => key + 1)}>重新加载</button>
      </div>
      {data && <footer><span className="status status-success">当前 TOP 榜单覆盖口径</span><strong>截止 {data.dataRange.endDate ?? "暂无日期"} · 覆盖 {monthText(data.dataRange.startDate, data.dataRange.endDate)}</strong><small>有效 {reportDimensionLabel} {count(data.summary.activeSkuCount)} · 待确认 AI 数据 {count(data.summary.pendingAiCount)} · 图片缓存 {count(data.imageCache.cached)}/{count(data.imageCache.total)}{data.imageCache.pending ? ` · 待处理 ${count(data.imageCache.pending)}` : ""}{loadedAt ? ` · 读取于 ${loadedAt}` : ""}</small></footer>}
    </section>}
    {activeSection !== "settings" && activeSection !== "compare" && loading && !data && <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取市场数据</strong><p>可以继续调整筛选或切换子页面。</p></section>}
    {filterError && activeSection !== "settings" && activeSection !== "compare" && <div className="market-feedback error" role="alert">{filterError}；仍可调整日期或搜索商品。</div>}
    {error && activeSection !== "settings" && activeSection !== "compare" && <div className="market-feedback error" role="alert">{error}。请调整上方筛选或重新加载。{data ? "当前保留上次成功读取的数据。" : ""}</div>}
    {activeSection === "ranking" && data && <RankingTable data={data} compareKeys={compareKeys} loadingMore={loadingMore || loading} onLoadMore={() => void loadMore()} onPrevious={() => void loadMore(-1)} onToggleCompare={toggleCompare} onTrend={setTrendItem} onOpenCompare={() => selectMarketSection("compare")} />}
    {activeSection === "overview" && data && data.view !== "full" ? loading && !error && <section className="panel data-state" role="status" aria-live="polite"><span className="state-spinner" /><strong>正在生成行业汇报</strong><p>商品榜单已可用，趋势、结构、竞争和机会矩阵正在按需汇总…</p></section> : activeSection === "overview" && data && <>
      <IndustryExecutiveSummary data={data} />
      <MarketKpis data={data} />
      <TrendSection data={data} />
      <PriceBandSection data={data} />
      <BrandSection data={data} />
      <SubcategorySection data={data} />
      <OperationModeSection data={data} />
      <TrafficQuadrantSection data={data} />
      <ProductSignalSection data={data} />
      <OpportunityMatrixSection data={data} />
      <IndustryDataGapSection data={data} />
    </>}
    {activeSection === "compare" && <CompareWorkspace selections={compareSelections} onClear={() => setCompareSelections([])} onRemoveCompare={removeCompare} onGoRanking={() => selectMarketSection("ranking")} query={query} categories={categories} scopes={scopes} rankingDimensions={dimensions} operationModes={operationModes} brands={brands} subcategories={subcategories} priceBands={priceBands} startDate={marketStartDate} endDate={marketEndDate} />}
    {activeSection === "settings" && settingsStatusLoading && !settingsData && <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取市场设置状态</strong><p>正在同步导入记录、图片缓存和业务日期…</p></section>}
    {activeSection === "settings" && settingsStatusError && <div className="market-feedback error" role="alert">{settingsStatusError}<button type="button" className="row-action" onClick={() => setReloadKey((key) => key + 1)}>重新加载</button></div>}
    {activeSection === "settings" && settingsData && <MarketSettingsWorkspace currentUser={currentUser} data={settingsData} onImported={() => setReloadKey((key) => key + 1)} />}
    {trendItem && <TrendDrawer item={trendItem} onClose={() => setTrendItem(null)} />}
    </div>
  </div>;
}
