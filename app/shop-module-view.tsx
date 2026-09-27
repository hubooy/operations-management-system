"use client";

import { useAiPageDetails } from "./ai-page-context-provider";
import PromotionDiagnosticPanel from "./promotion-diagnostic-panel";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { requestJson } from "@/lib/http/api-client";
import type { ImportSourceKey, ModuleKey, ModuleViewKey } from "./shell/navigation-catalog";
import { SearchableMultiSelect, SearchableSelect } from "./ui/searchable-select";
import {
  type SalesRangeLabel,
  type SalesStats,
  type SalesChannel,
  type SalesSummaryResponse,
  snapshotTokenPattern,
  claimSnapshotRestart,
  effectivePageForScope,
  type JdSkuCatalogResponse,
  type JdSkuCatalogPageResponse,
  type NetshopProductPerformanceDimension,
  type NetshopProductPerformanceItem,
  type NetshopProductPerformancePageResponse,
  type NetshopProductPerformanceResponse,
  type NetshopProductPerformanceSummaryResponse,
  type NetshopPromotionPerformanceResponse,
  type NetshopPromotionItemsResponse,
  type NetshopPromotionOverviewResponse,
  netshopOutletFilterKey,
  salesRangeMap,
  formatCurrencyFromCents,
  formatExactCurrencyFromCents,
  formatMerchantCurrency,
  formatOptionalCurrencyFromCents,
  formatOptionalRate,
  formatCount,
  rateAsPercent,
  formatRate,
  formatNetSalesYearOverYear,
  netSalesYearOverYearTone,
  addIsoDays,
  isoDayDifference,
  skuSalesPeriod,
  type ProductComparisonMode,
  productComparisonPeriod,
  productComparisonRate,
  formatProductComparison,
  useDebouncedValue,
  addIsoYears,
  formatDateTime,
  formatChange,
  comparisonHint,
  Dot,
  MetricCard,
  SectionHeader,
} from "./module-view-shared";

type StoreGranularity = "day" | "week" | "month";
type StoreComparisonMode = "period" | "year";
type StorePeriodRow = SalesStats & {
  key: string;
  label: string;
  promotionSpendCents?: number;
  promotionNetTransactionCents?: number;
  platformPaymentCents?: number;
  promotionClicks?: number;
  promotionSpendRate?: number | null;
  promotionTransactionShare?: number | null;
};

function storePeriodKey(dateValue: string, granularity: StoreGranularity) {
  if (granularity === "day") return dateValue;
  if (granularity === "month") return dateValue.slice(0, 7);
  const date = new Date(`${dateValue}T00:00:00Z`);
  const weekDay = date.getUTCDay();
  date.setUTCDate(date.getUTCDate() + (weekDay === 0 ? -6 : 1 - weekDay));
  return date.toISOString().slice(0, 10);
}

function aggregateStorePeriods(daily: Array<{ date: string } & SalesStats>, granularity: StoreGranularity): StorePeriodRow[] {
  const buckets = new Map<string, StorePeriodRow>();
  for (const item of daily) {
    const key = storePeriodKey(item.date, granularity);
    const label = granularity === "week" ? `${key} 周` : key;
    const row = buckets.get(key) ?? {
      key,
      label,
      grossSalesCents: 0,
      netSalesCents: 0,
      costAmountCents: 0,
      grossProfitCents: 0,
      refundAmountCents: 0,
      orderCount: 0,
      lineCount: 0,
      netQuantity: 0,
      averageOrderValueCents: 0,
      grossMarginRate: 0,
      refundRate: 0,
    };
    row.grossSalesCents += item.grossSalesCents;
    row.netSalesCents += item.netSalesCents;
    row.costAmountCents += item.costAmountCents;
    row.grossProfitCents += item.grossProfitCents;
    row.refundAmountCents += item.refundAmountCents;
    row.orderCount += item.orderCount;
    row.lineCount += item.lineCount;
    row.netQuantity += item.netQuantity;
    row.averageOrderValueCents = row.orderCount === 0 ? 0 : row.netSalesCents / row.orderCount;
    row.grossMarginRate = row.netSalesCents === 0 ? 0 : (row.netSalesCents - row.costAmountCents) / row.netSalesCents;
    row.refundRate = row.grossSalesCents === 0 ? 0 : row.refundAmountCents / row.grossSalesCents;
    buckets.set(key, row);
  }
  return [...buckets.values()].sort((left, right) => left.key.localeCompare(right.key));
}

function mergeStorePromotionPeriods(rows: StorePeriodRow[], daily: NetshopPromotionPerformanceResponse["daily"], granularity: StoreGranularity) {
  const buckets = new Map<string, { spend: number; net: number; payment: number; clicks: number }>();
  for (const item of daily) {
    const key = storePeriodKey(item.date, granularity);
    const current = buckets.get(key) ?? { spend: 0, net: 0, payment: 0, clicks: 0 };
    current.spend += item.spendCents;
    current.net += item.netTransactionAmountCents;
    current.payment += item.platformPaymentAmountCents ?? 0;
    current.clicks += item.clicks;
    buckets.set(key, current);
  }
  return rows.map((row) => {
    const promotion = buckets.get(row.key);
    if (!promotion) return row;
    return {
      ...row,
      promotionSpendCents: promotion.spend,
      promotionNetTransactionCents: promotion.net,
      platformPaymentCents: promotion.payment,
      promotionClicks: promotion.clicks,
      promotionSpendRate: promotion.payment > 0 ? promotion.spend / promotion.payment : null,
      promotionTransactionShare: promotion.payment > 0 ? promotion.net / promotion.payment : null,
    };
  });
}

const storeComparisonRate = (value: number, baseline?: number) => !baseline ? null : (value - baseline) / Math.abs(baseline);
const formatStoreComparison = (value: number, baseline?: number) => {
  const rate = storeComparisonRate(value, baseline);
  return rate === null ? "无可比数据" : `${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(1)}%`;
};

function StoreMetricCard({ label, value, change, note, unavailable = false }: {
  label: string;
  value: string;
  change?: number | null;
  note?: string;
  unavailable?: boolean;
}) {
  return <article className={`store-metric-card ${unavailable ? "unavailable" : ""}`}>
    <div><span>{label}</span>{unavailable && <em>待接入</em>}</div>
    <strong title={value}>{value}</strong>
    <small title={note} className={change === null || change === undefined ? "muted-text" : change < 0 ? "red-text" : "green-text"}>{note ?? (change === null || change === undefined ? "暂无可比数据" : `${change >= 0 ? "+" : ""}${(change * 100).toFixed(1)}%`)}</small>
  </article>;
}

function StoreSpuVisitorMetric({
  startDate,
  endDate,
  outlets,
  selectedOutletKeys,
}: {
  startDate: string;
  endDate: string;
  outlets: Array<Pick<SalesChannel, "groupKey" | "name" | "platform">>;
  selectedOutletKeys: string[];
}) {
  const [performance, setPerformance] = useState<NetshopProductPerformanceSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const selectedOutlets = useMemo(
    () => selectedOutletKeys.length === 0 ? [] : outlets.filter((item) => selectedOutletKeys.includes(item.groupKey)),
    [outlets, selectedOutletKeys],
  );
  const scopeOutlets = useMemo(
    () => selectedOutlets.map((item) => netshopOutletFilterKey(item.platform, item.name)),
    [selectedOutlets],
  );

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({ dimension: "spu", view: "summary", startDate, endDate });
        scopeOutlets.forEach((outlet) => params.append("outlet", outlet));
        const response = await fetch(`/api/netshop/product-performance?${params.toString()}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (NetshopProductPerformanceSummaryResponse & { error?: string }) | null;
        if (!response.ok || !payload?.summary || !snapshotTokenPattern.test(payload.snapshotToken) || payload.dimension !== "spu") {
          throw new Error(payload?.error || `SPU 商品访客读取失败（${response.status}）`);
        }
        if (!controller.signal.aborted) setPerformance(payload);
      } catch (requestError) {
        if (!controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "暂时无法读取 SPU 商品访客");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [endDate, scopeOutlets, startDate]);

  if (loading && !performance) return <StoreMetricCard label="商品访客累计" value="同步中…" note="正在汇总已导入 SPU 商品×日访客" />;
  if ((error && !performance) || !performance?.dataCutoffDate) return <StoreMetricCard label="商品访客累计" value="—" note={error ? "未获取到匹配店铺的 SPU 日数据" : "待导入匹配店铺的 SPU 日数据"} unavailable />;

  const sourceVisitors = performance.summary.visitors;
  const scopeNote = selectedOutletKeys.length === 0
    ? `全部已导入 SPU 店铺 · 截止 ${performance.dataCutoffDate}`
    : `已筛选 ${formatCount(scopeOutlets.length)} 个店铺 · 截止 ${performance.dataCutoffDate}`;
  return <StoreMetricCard label="商品访客累计" value={formatCount(sourceVisitors)} note={`商品×日累计，非店铺去重 UV · ${scopeNote}${loading ? " · 正在更新" : error ? " · 刷新失败，保留上次结果" : ""}`} />;
}

function StoreTableMetric({ value, baseline, formatter, showComparison, showActual }: {
  value: number;
  baseline?: number;
  formatter: (value: number) => string;
  showComparison: boolean;
  showActual: boolean;
}) {
  const rate = storeComparisonRate(value, baseline);
  return <div className="store-table-metric"><strong>{formatter(value)}</strong>{showComparison && <small className={rate === null ? "muted-text" : rate < 0 ? "red-text" : "green-text"}>{showActual && baseline !== undefined ? `对比 ${formatter(baseline)}` : rate === null ? "无可比数据" : `${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(1)}%`}</small>}</div>;
}

function StoreUnavailableCell() {
  return <div className="store-unavailable-cell"><strong>—</strong><small>待接入</small></div>;
}

type StoreTableColumnKey =
  | "visitors"
  | "netSales"
  | "averageOrderValue"
  | "uvValue"
  | "conversionRate"
  | "promotionSpend"
  | "promotionShare"
  | "retailShare"
  | "b2bShare"
  | "promotionClicks"
  | "paidVisitors"
  | "freeVisitors"
  | "orderCount"
  | "grossMarginRate"
  | "refundRate";

const storeTableColumns: Array<{ key: StoreTableColumnKey; label: string; available: boolean }> = [
  { key: "visitors", label: "访客", available: false },
  { key: "netSales", label: "销售净额", available: true },
  { key: "averageOrderValue", label: "客单价", available: true },
  { key: "uvValue", label: "UV 价值", available: false },
  { key: "conversionRate", label: "转化率", available: false },
  { key: "promotionSpend", label: "推广花费", available: true },
  { key: "promotionShare", label: "推广费率", available: true },
  { key: "retailShare", label: "零售占比", available: false },
  { key: "b2bShare", label: "B 端占比", available: false },
  { key: "promotionClicks", label: "推广点击数", available: true },
  { key: "paidVisitors", label: "付费访客", available: false },
  { key: "freeVisitors", label: "免费访客", available: false },
  { key: "orderCount", label: "订单量", available: true },
  { key: "grossMarginRate", label: "大毛利率", available: true },
  { key: "refundRate", label: "退货率", available: true },
];

const connectedStoreTableColumns = storeTableColumns.filter((column) => column.available).map((column) => column.key);

function StoreDataCell({ column, row, compared, showComparison, showActual }: {
  column: StoreTableColumnKey;
  row: StorePeriodRow;
  compared?: StorePeriodRow;
  showComparison: boolean;
  showActual: boolean;
}) {
  if (column === "netSales") return <StoreTableMetric value={row.netSalesCents} baseline={compared?.netSalesCents} formatter={formatCurrencyFromCents} showComparison={showComparison} showActual={showActual} />;
  if (column === "averageOrderValue") return <StoreTableMetric value={row.averageOrderValueCents} baseline={compared?.averageOrderValueCents} formatter={formatCurrencyFromCents} showComparison={showComparison} showActual={showActual} />;
  if (column === "orderCount") return <StoreTableMetric value={row.orderCount} baseline={compared?.orderCount} formatter={formatCount} showComparison={showComparison} showActual={showActual} />;
  if (column === "grossMarginRate") return <StoreTableMetric value={row.grossMarginRate} baseline={compared?.grossMarginRate} formatter={formatRate} showComparison={showComparison} showActual={showActual} />;
  if (column === "refundRate") return <StoreTableMetric value={row.refundRate} baseline={compared?.refundRate} formatter={formatRate} showComparison={showComparison} showActual={showActual} />;
  if (column === "promotionSpend" && row.promotionSpendCents !== undefined) return <StoreTableMetric value={row.promotionSpendCents} formatter={formatCurrencyFromCents} showComparison={false} showActual={false} />;
  if (column === "promotionShare" && row.promotionSpendRate !== undefined && row.promotionSpendRate !== null) return <StoreTableMetric value={row.promotionSpendRate} formatter={formatRate} showComparison={false} showActual={false} />;
  if (column === "promotionClicks" && row.promotionClicks !== undefined) return <StoreTableMetric value={row.promotionClicks} formatter={formatCount} showComparison={false} showActual={false} />;
  return <StoreUnavailableCell />;
}

function StoreTrendChart({ rows, comparisonRows, showComparison, comparisonLabel }: {
  rows: StorePeriodRow[];
  comparisonRows: Array<StorePeriodRow | undefined>;
  showComparison: boolean;
  comparisonLabel: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const highlightedIndex = rows.length === 0 ? null : Math.min(activeIndex ?? rows.length - 1, rows.length - 1);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = canvas?.parentElement;
    if (!canvas || !container) return;
    const draw = () => {
      const width = Math.max(360, Math.floor(container.getBoundingClientRect().width));
      const height = 330;
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const plot = { left: 56, right: width - 42, top: 28, bottom: height - 44 };
      context.font = "10px sans-serif";
      context.textAlign = "right";
      const maxSales = Math.max(1, ...rows.map((row) => Math.max(0, row.netSalesCents)), ...(showComparison ? comparisonRows.map((row) => Math.max(0, row?.netSalesCents ?? 0)) : []));
      const maxOrders = Math.max(1, ...rows.map((row) => Math.max(0, row.orderCount)));
      for (let index = 0; index <= 4; index += 1) {
        const y = plot.top + (plot.bottom - plot.top) * index / 4;
        context.strokeStyle = "#e8edf4";
        context.lineWidth = 1;
        context.beginPath(); context.moveTo(plot.left, y); context.lineTo(plot.right, y); context.stroke();
        context.fillStyle = "#8b97a8";
        context.fillText(formatCount(Math.round(maxSales / 100 * (1 - index / 4))), plot.left - 8, y + 3);
      }
      if (!rows.length) return;
      const slot = (plot.right - plot.left) / rows.length;
      const barWidth = Math.max(4, Math.min(26, slot * .52));
      rows.forEach((row, index) => {
        const x = plot.left + slot * index + slot / 2;
        const barHeight = Math.max(2, Math.max(0, row.netSalesCents) / maxSales * (plot.bottom - plot.top));
        const gradient = context.createLinearGradient(0, plot.bottom - barHeight, 0, plot.bottom);
        gradient.addColorStop(0, "#6f91e9"); gradient.addColorStop(1, "#4776e6");
        context.fillStyle = gradient;
        context.fillRect(x - barWidth / 2, plot.bottom - barHeight, barWidth, barHeight);
      });
      context.beginPath();
      rows.forEach((row, index) => {
        const x = plot.left + slot * index + slot / 2;
        const y = plot.bottom - Math.max(0, row.orderCount) / maxOrders * (plot.bottom - plot.top);
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
      });
      context.strokeStyle = "#65b76e"; context.lineWidth = 2.2; context.lineJoin = "round"; context.stroke();
      rows.forEach((row, index) => {
        const x = plot.left + slot * index + slot / 2;
        const y = plot.bottom - Math.max(0, row.orderCount) / maxOrders * (plot.bottom - plot.top);
        context.fillStyle = "#fff"; context.strokeStyle = "#65b76e"; context.lineWidth = 2;
        context.beginPath(); context.arc(x, y, 3.5, 0, Math.PI * 2); context.fill(); context.stroke();
      });
      if (showComparison && comparisonRows.length) {
        context.save(); context.beginPath(); context.setLineDash([5, 4]);
        let segmentStarted = false;
        comparisonRows.slice(0, rows.length).forEach((row, index) => {
          if (!row) { segmentStarted = false; return; }
          const x = plot.left + slot * index + slot / 2;
          const y = plot.bottom - Math.max(0, row.netSalesCents) / maxSales * (plot.bottom - plot.top);
          if (!segmentStarted) context.moveTo(x, y); else context.lineTo(x, y);
          segmentStarted = true;
        });
        context.strokeStyle = "#aab4c3"; context.lineWidth = 1.5; context.stroke(); context.restore();
      }
      const labelStep = Math.max(1, Math.ceil(rows.length / 8));
      context.fillStyle = "#8793a4"; context.textAlign = "center";
      rows.forEach((row, index) => { if (index % labelStep === 0 || index === rows.length - 1) context.fillText(row.label.slice(5), plot.left + slot * index + slot / 2, height - 17); });
      if (highlightedIndex !== null) {
        const x = plot.left + slot * highlightedIndex + slot / 2;
        context.save(); context.strokeStyle = "#b4bfce"; context.setLineDash([3, 4]); context.beginPath(); context.moveTo(x, plot.top); context.lineTo(x, plot.bottom); context.stroke(); context.restore();
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(container);
    return () => observer.disconnect();
  }, [comparisonRows, highlightedIndex, rows, showComparison]);

  const active = highlightedIndex === null ? null : rows[highlightedIndex];
  const comparison = highlightedIndex === null ? undefined : comparisonRows[highlightedIndex];
  return <section className="panel store-trend-panel">
    <div className="store-section-header"><div><span className="eyebrow">STORE TREND</span><h2>趋势图</h2><p>蓝色柱为销售净额，绿色折线为订单量；灰色虚线为{comparisonLabel}销售净额。</p></div><div className="store-trend-legend"><span><i className="sales" />销售净额</span><span><i className="orders" />订单量</span>{showComparison && <span><i className="compare" />{comparisonLabel}</span>}</div></div>
    <div className="store-trend-canvas"><canvas ref={canvasRef} role="img" aria-label="店铺销售净额与订单量趋势图" onPointerMove={(event) => { if (!rows.length) return; const bounds = event.currentTarget.getBoundingClientRect(); const progress = Math.min(1, Math.max(0, (event.clientX - bounds.left - 56) / Math.max(1, bounds.width - 98))); setActiveIndex(Math.round(progress * (rows.length - 1))); }} onPointerLeave={() => setActiveIndex(null)} />{!rows.length && <div className="trend-empty">当前周期没有可绘制的店铺销售数据。</div>}</div>
    {active && <div className="store-trend-summary"><strong>{active.label}</strong><span>销售净额 <b>{formatCurrencyFromCents(active.netSalesCents)}</b></span><span>订单量 <b>{formatCount(active.orderCount)}</b></span><span>客单价 <b>{formatCurrencyFromCents(active.averageOrderValueCents)}</b></span>{showComparison && <span>{comparisonLabel} <b>{comparison ? formatStoreComparison(active.netSalesCents, comparison.netSalesCents) : "无数据"}</b></span>}</div>}
  </section>;
}

function StoreAnalysisView({ summary, outlets, selectedOutletKeys, onSelectOutlets, loading }: {
  summary: SalesSummaryResponse;
  outlets: Array<Pick<SalesChannel, "groupKey" | "name" | "platform">>;
  selectedOutletKeys: string[];
  onSelectOutlets: (keys: string[]) => void;
  loading: boolean;
}) {
  const [granularity, setGranularity] = useState<StoreGranularity>("day");
  const [comparisonMode, setComparisonMode] = useState<StoreComparisonMode>("period");
  const [showComparison, setShowComparison] = useState(true);
  const [showActual, setShowActual] = useState(false);
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [columnPickerSearch, setColumnPickerSearch] = useState("");
  const [visibleColumns, setVisibleColumns] = useState<StoreTableColumnKey[]>(() => storeTableColumns.map((column) => column.key));
  const [promotion, setPromotion] = useState<NetshopPromotionOverviewResponse | null>(null);
  const [promotionLoading, setPromotionLoading] = useState(true);
  const [promotionError, setPromotionError] = useState("");
  const columnPickerRef = useRef<HTMLDivElement>(null);
  const current = summary.current;
  const baseline = comparisonMode === "period" ? summary.previous : summary.yearAgo;
  const comparisonLabel = comparisonMode === "period" ? "环比" : "同比";
  const salesRows = useMemo(() => aggregateStorePeriods(summary.daily ?? [], granularity), [granularity, summary.daily]);
  const rows = useMemo(() => mergeStorePromotionPeriods(salesRows, promotion?.daily ?? [], granularity), [granularity, promotion?.daily, salesRows]);
  const storeDetailFilterScope = summary.trendTruncated === false
    && promotion?.dailyPagination.truncated === false
    ? "full"
    : "none";
  const comparisonRows = useMemo(() => {
    const available = aggregateStorePeriods(comparisonMode === "period" ? summary.previousDaily ?? [] : summary.yearAgoDaily ?? [], granularity);
    const byKey = new Map(available.map((row) => [row.key, row]));
    return rows.map((row) => {
      const targetDate = comparisonMode === "period" && summary.previousStartDate
        ? addIsoDays(summary.previousStartDate, isoDayDifference(summary.startDate, row.key))
        : addIsoYears(row.key.length === 7 ? `${row.key}-01` : row.key, -1);
      return byKey.get(storePeriodKey(targetDate, granularity));
    });
  }, [comparisonMode, granularity, rows, summary.previousDaily, summary.previousStartDate, summary.startDate, summary.yearAgoDaily]);
  const currentAov = current.orderCount === 0 ? 0 : current.netSalesCents / current.orderCount;
  const baselineAov = !baseline?.orderCount ? 0 : baseline.netSalesCents / baseline.orderCount;
  const salesChange = storeComparisonRate(current.netSalesCents, baseline?.netSalesCents);
  const aovChange = storeComparisonRate(currentAov, baselineAov);
  const selectedOutlets = useMemo(() => outlets.filter((item) => selectedOutletKeys.includes(item.groupKey)), [outlets, selectedOutletKeys]);
  useAiPageDetails("shop", {
    period: { startDate: summary.startDate, endDate: summary.endDate },
    filters: { outletKeys: selectedOutletKeys },
  });

  const promotionOutlets = useMemo(
    () => selectedOutlets.map((item) => netshopOutletFilterKey(item.platform, item.name)),
    [selectedOutlets],
  );
  const promotionPlatforms = useMemo(
    () => [...new Set((selectedOutlets.length ? selectedOutlets : outlets).map((item) => item.platform))]
      .filter((platform) => platform === "京东" || platform === "天猫"),
    [outlets, selectedOutlets],
  );
  const selectedOutletLabel = selectedOutlets.length === 0
    ? "全部平台与店铺"
    : selectedOutlets.length === 1
      ? `${selectedOutlets[0].platform} · ${selectedOutlets[0].name}`
      : `已选 ${formatCount(selectedOutlets.length)} 个店铺`;
  const daily = summary.daily ?? [];
  const dataCutoff = summary.dataCutoffDate ?? (daily.length > 0 ? daily[daily.length - 1].date : "暂无");
  const comparisonPeriodNote = summary.periodAdjustedToDataCutoff
    ? `请求周期 ${summary.requestedStartDate ?? summary.startDate} 至 ${summary.requestedEndDate ?? summary.endDate}，销售数据截止 ${dataCutoff}；已自动对齐为 ${summary.startDate} 至 ${summary.endDate}（${summary.comparisonDayCount ?? daily.length} 天），未把未同步尾日计入环比或同比。`
    : `销售数据截止 ${dataCutoff}；当前按 ${summary.startDate} 至 ${summary.endDate}（${summary.comparisonDayCount ?? daily.length} 天）汇总，环比和同比使用等长日期。`;
  const matchedStoreColumns = useMemo(() => {
    const keyword = columnPickerSearch.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return storeTableColumns;
    return storeTableColumns.filter((column) => column.label.toLocaleLowerCase("zh-CN").includes(keyword));
  }, [columnPickerSearch]);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setPromotionLoading(true);
      setPromotionError("");
      try {
        const params = new URLSearchParams({ startDate: summary.startDate, endDate: summary.endDate });
        promotionPlatforms.forEach((platform) => params.append("platform", platform));
        promotionOutlets.forEach((outlet) => params.append("outlet", outlet));
        const response = await fetch(`/api/netshop/promotion-performance/overview?${params.toString()}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (NetshopPromotionOverviewResponse & { error?: string }) | null;
        if (!response.ok || !payload?.summary) throw new Error(payload?.error || `推广数据读取失败（${response.status}）`);
        if (!controller.signal.aborted) setPromotion(payload);
      } catch (requestError) {
        if (!controller.signal.aborted) setPromotionError(requestError instanceof Error ? requestError.message : "推广数据读取失败");
      } finally {
        if (!controller.signal.aborted) setPromotionLoading(false);
      }
    })();
    return () => controller.abort();
  }, [promotionOutlets, promotionPlatforms, summary.endDate, summary.startDate]);

  useEffect(() => {
    if (!columnPickerOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!columnPickerRef.current?.contains(event.target as Node)) setColumnPickerOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setColumnPickerOpen(false);
    };
    window.addEventListener("pointerdown", closeOnOutsideClick);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutsideClick);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [columnPickerOpen]);

  const toggleStoreColumn = (column: StoreTableColumnKey) => setVisibleColumns((currentColumns) => {
    if (currentColumns.includes(column)) return currentColumns.length === 1 ? currentColumns : currentColumns.filter((item) => item !== column);
    return storeTableColumns.map((item) => item.key).filter((key) => currentColumns.includes(key) || key === column);
  });

  return <>
    <section className="panel store-filter-panel" aria-label="店铺分析筛选条件">
      <label className="store-select-field"><span>店铺</span><SearchableMultiSelect values={selectedOutletKeys} onChange={onSelectOutlets} ariaLabel="选择分析店铺" allLabel="全部店铺" searchPlaceholder="搜索店铺或平台" options={outlets.map((outlet) => ({ value: outlet.groupKey, label: `${outlet.name} · ${outlet.platform}`, searchText: `${outlet.name} ${outlet.platform}` }))} /></label>
      <div className="store-period-context"><span>统计周期</span><strong>{summary.startDate} → {summary.endDate}</strong><small>{selectedOutletLabel} · 数据截止 {dataCutoff}</small></div>
      <div className="segmented store-granularity" role="group" aria-label="店铺分析时间粒度"><button type="button" className={granularity === "day" ? "active" : ""} onClick={() => setGranularity("day")}>按日</button><button type="button" className={granularity === "week" ? "active" : ""} onClick={() => setGranularity("week")}>按周</button><button type="button" className={granularity === "month" ? "active" : ""} onClick={() => setGranularity("month")}>按月</button></div>
      <label className="store-check"><input type="checkbox" checked={showComparison} onChange={(event) => setShowComparison(event.target.checked)} /><span>显示对比数据</span></label>
      <div className="segmented store-compare-mode" role="group" aria-label="店铺分析对比口径"><button type="button" className={comparisonMode === "period" ? "active" : ""} disabled={!showComparison} onClick={() => setComparisonMode("period")}>环比</button><button type="button" className={comparisonMode === "year" ? "active" : ""} disabled={!showComparison} onClick={() => setComparisonMode("year")}>同比</button></div>
      <label className="store-check"><input type="checkbox" checked={showActual} disabled={!showComparison} onChange={(event) => setShowActual(event.target.checked)} /><span>显示对比值</span></label>
      <button type="button" className="row-action store-refresh" disabled={loading} onClick={() => onSelectOutlets(selectedOutletKeys)}>{loading ? "刷新中…" : "↻ 刷新"}</button>
    </section>

    <section className="store-source-status" role="note">
      <div><span className="source-status-ready">✓ 已接入</span><strong>销售净额、订单量、客单价、毛利率、退货率、商品级访客累计、推广</strong></div>
      <div><span className="source-status-missing">○ 待接入</span><strong>店铺去重 UV、付费/免费访客、企业购/零售拆分</strong></div>
      <p>{comparisonPeriodNote} 商品访客仅按商品×日累计；推广比例只使用同日数据。</p>
    </section>
    {promotionError && promotion && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>推广数据刷新失败</strong><p>{promotionError}；当前仍显示上一次成功结果。</p></div></section>}

    <section className="store-metrics-grid data-refresh-region" aria-busy={loading || promotionLoading}>
      <StoreSpuVisitorMetric startDate={summary.startDate} endDate={summary.endDate} outlets={outlets} selectedOutletKeys={selectedOutletKeys} />
      <StoreMetricCard label="销售额（净额）" value={formatExactCurrencyFromCents(current.netSalesCents)} change={salesChange} note={showComparison ? `${comparisonLabel} ${formatStoreComparison(current.netSalesCents, baseline?.netSalesCents)} · 对比值 ${baseline ? formatExactCurrencyFromCents(baseline.netSalesCents) : "—"}` : "来自已导入销售明细"} />
      <StoreMetricCard label="客单价" value={formatCurrencyFromCents(currentAov)} change={aovChange} note={showComparison ? `${comparisonLabel} ${formatStoreComparison(currentAov, baselineAov)}` : `${formatCount(current.orderCount)} 笔订单`} />
      <StoreMetricCard label="UV 价值" value="—" note="缺少访客数据，不做推算" unavailable />
      <StoreMetricCard label="转化率" value="—" note="需访客与成交人数" unavailable />
      <StoreMetricCard label="推广花费" value={promotion?.dataCutoffDate ? formatCurrencyFromCents(promotion.summary.spendCents) : promotionLoading ? "同步中…" : "—"} note={promotion?.dataCutoffDate ? `推广成交 ${formatCurrencyFromCents(promotion.summary.netTransactionAmountCents)}` : promotionError || "当前店铺/周期暂无推广报表"} unavailable={!promotionLoading && !promotion?.dataCutoffDate} />
      <StoreMetricCard label="推广费率" value={promotion?.summary.spendRate === null || promotion?.summary.spendRate === undefined ? "—" : formatRate(promotion.summary.spendRate)} note={promotion?.summary.promotionTransactionShare === null || promotion?.summary.promotionTransactionShare === undefined ? "缺少同日平台商品成交金额" : `推广成交占比 ${formatRate(promotion.summary.promotionTransactionShare)}`} unavailable={!promotionLoading && promotion?.summary.spendRate === null} />
      <StoreMetricCard label="零售占比" value="—" note="待接入订单类型标记" unavailable />
      <StoreMetricCard label="B 端占比" value="—" note="待接入企业购明细" unavailable />
      <StoreMetricCard label="推广点击数" value={promotion?.dataCutoffDate ? formatCount(promotion.summary.clicks) : promotionLoading ? "同步中…" : "—"} note={promotion?.dataCutoffDate ? `展现 ${formatCount(promotion.summary.impressions)} · CTR ${formatOptionalRate(promotion.summary.clickThroughRate)}` : "当前店铺/周期暂无推广点击"} unavailable={!promotionLoading && !promotion?.dataCutoffDate} />
      <StoreMetricCard label="付费访客" value="不推算" note="避免用点击数替代访客" unavailable />
      <StoreMetricCard label="免费访客" value="不推算" note="需平台自然流量数据" unavailable />
    </section>

    <section className="store-reliable-strip data-refresh-region" aria-busy={loading}>
      <div><span>订单量</span><strong>{formatCount(current.orderCount)}</strong><small>{showComparison ? `${comparisonLabel} ${formatStoreComparison(current.orderCount, baseline?.orderCount)}` : "笔"}</small></div>
      <div><span>净销量</span><strong>{formatCount(current.netQuantity)}</strong><small>{showComparison ? `${comparisonLabel} ${formatStoreComparison(current.netQuantity, baseline?.netQuantity)}` : "件"}</small></div>
      <div><span>订单毛利</span><strong>{formatCurrencyFromCents(current.grossProfitCents)}</strong><small>{showComparison ? `${comparisonLabel} ${formatStoreComparison(current.grossProfitCents, baseline?.grossProfitCents)}` : "已导入成本口径"}</small></div>
      <div><span>大毛利率</span><strong>{formatRate(current.grossMarginRate)}</strong><small>{showComparison ? `${comparisonLabel} ${formatStoreComparison(current.grossMarginRate, baseline?.grossMarginRate)}` : "毛利 / 净销售额"}</small></div>
      <div><span>退货率</span><strong>{formatRate(current.refundRate)}</strong><small>{showComparison ? `${comparisonLabel} ${formatStoreComparison(current.refundRate, baseline?.refundRate)}` : "退货额 / 正向销售额"}</small></div>
    </section>

    <StoreTrendChart rows={rows} comparisonRows={comparisonRows} showComparison={showComparison} comparisonLabel={comparisonLabel} />

    <section className="panel table-panel store-detail-panel data-refresh-region" aria-busy={loading || promotionLoading}>
      <div className="table-toolbar"><div><h2>数据明细</h2><p>按{granularity === "day" ? "日" : granularity === "week" ? "自然周" : "自然月"}汇总；金额均为人民币元，退货以负值参与净额。</p></div><div className="store-table-toolbar-actions"><span className="soft-tag">{formatCount(rows.length)} 个周期</span><div className={`store-column-picker ${columnPickerOpen ? "open" : ""}`} ref={columnPickerRef}><button type="button" className="store-column-picker-trigger" aria-haspopup="dialog" aria-expanded={columnPickerOpen} onClick={() => { setColumnPickerOpen((open) => !open); setColumnPickerSearch(""); }}><span>☷</span>指标选择 <em>{visibleColumns.length}/{storeTableColumns.length}</em></button>{columnPickerOpen && <div className="store-column-picker-menu" role="dialog" aria-label="选择数据明细指标"><div className="store-column-picker-head"><div><strong>显示指标</strong><small>周期列固定显示，至少保留 1 个指标</small></div><button type="button" onClick={() => setColumnPickerOpen(false)} aria-label="关闭指标选择">×</button></div><div className="store-column-picker-actions"><button type="button" onClick={() => setVisibleColumns(storeTableColumns.map((column) => column.key))}>全选</button><button type="button" onClick={() => setVisibleColumns(connectedStoreTableColumns)}>仅看已接入</button></div><label className="store-column-picker-search">⌕<input autoFocus type="search" value={columnPickerSearch} onChange={(event) => setColumnPickerSearch(event.target.value)} placeholder="搜索指标" aria-label="搜索数据明细指标" /></label><div className="store-column-picker-options">{matchedStoreColumns.map((column) => { const checked = visibleColumns.includes(column.key); return <label key={column.key} className={checked ? "selected" : ""}><input type="checkbox" checked={checked} disabled={checked && visibleColumns.length === 1} onChange={() => toggleStoreColumn(column.key)} /><span>{column.label}</span><em className={column.available ? "available" : "pending"}>{column.available ? "已接入" : "待接入"}</em></label>; })}{matchedStoreColumns.length === 0 && <p className="store-column-picker-empty">没有匹配的指标</p>}</div></div>}</div></div></div>
      <div className="data-table-wrap store-detail-scroll-area"><table className="data-table store-detail-table" data-column-filter-scope={storeDetailFilterScope} style={{ minWidth: `${Math.max(760, 180 + visibleColumns.length * 112)}px` }}><thead><tr><th>周期</th>{storeTableColumns.filter((column) => visibleColumns.includes(column.key)).map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{rows.map((row, index) => { const compared = comparisonRows[index]; return <tr key={row.key}><td><strong>{row.label}</strong></td>{storeTableColumns.filter((column) => visibleColumns.includes(column.key)).map((column) => <td key={column.key}><StoreDataCell column={column.key} row={row} compared={compared} showComparison={showComparison} showActual={showActual} /></td>)}</tr>; })}{rows.length === 0 && <tr><td colSpan={visibleColumns.length + 1}><div className="table-state">当前周期没有可展示的店铺明细。</div></td></tr>}</tbody></table></div>
    </section>
  </>;
}

type ProductPerformanceColumnKey =
  | "pageViews"
  | "visitors"
  | "transactionCustomers"
  | "transactionQuantity"
  | "addCartCustomers"
  | "addCartQuantity"
  | "transactionAmount"
  | "uvValue"
  | "conversionRate"
  | "searchImpressions"
  | "searchClicks"
  | "searchClickRate"
  | "favorites"
  | "refundAmount"
  | "searchVisitors"
  | "searchTransactionCustomers"
  | "promotionSpend"
  | "promotionShare"
  | "promotionTransactionAmount"
  | "promotionRoi"
  | "promotionTransactionShare"
  | "enterpriseAmount"
  | "enterpriseOrderCount"
  | "enterpriseCustomerCount"
  | "enterpriseQuantity"
  | "enterpriseAverageItemValue"
  | "enterpriseSalesShare";

type ProductPerformanceColumn = {
  key: ProductPerformanceColumnKey;
  label: string;
  available: boolean;
};

const productPerformanceColumns: ProductPerformanceColumn[] = [
  { key: "pageViews", label: "商品浏览量", available: true },
  { key: "visitors", label: "商品访客累计", available: true },
  { key: "transactionCustomers", label: "成交客户累计", available: true },
  { key: "transactionQuantity", label: "成交商品件数", available: true },
  { key: "addCartCustomers", label: "加购人数", available: true },
  { key: "addCartQuantity", label: "加购商品件数", available: true },
  { key: "transactionAmount", label: "成交金额", available: true },
  { key: "uvValue", label: "UV价值", available: true },
  { key: "conversionRate", label: "总转化率", available: true },
  { key: "searchImpressions", label: "搜索曝光次数", available: true },
  { key: "searchClicks", label: "搜索点击次数", available: true },
  { key: "searchClickRate", label: "搜索点击率", available: true },
  { key: "favorites", label: "收藏人数", available: true },
  { key: "refundAmount", label: "成功退款金额", available: true },
  { key: "searchVisitors", label: "搜索引导访客", available: true },
  { key: "searchTransactionCustomers", label: "搜索引导支付买家", available: true },
  { key: "promotionSpend", label: "推广花费", available: false },
  { key: "promotionShare", label: "推广占比", available: false },
  { key: "promotionTransactionAmount", label: "推广成交金额", available: false },
  { key: "promotionRoi", label: "推广ROI", available: false },
  { key: "promotionTransactionShare", label: "推广成交占比", available: false },
  { key: "enterpriseAmount", label: "企业购出库金额", available: false },
  { key: "enterpriseOrderCount", label: "企业购出库单量", available: false },
  { key: "enterpriseCustomerCount", label: "企业购出库用户数", available: false },
  { key: "enterpriseQuantity", label: "企业购出库件数", available: false },
  { key: "enterpriseAverageItemValue", label: "企业购件单价", available: false },
  { key: "enterpriseSalesShare", label: "企业购销售占比", available: false },
];

const connectedProductPerformanceColumns = productPerformanceColumns
  .filter((column) => column.available)
  .map((column) => column.key);

function ProductPerformanceMetricCell({
  value,
  baseline,
  formatter,
  showComparison,
  showActual,
  comparisonLabel,
}: {
  value?: number | null;
  baseline?: number | null;
  formatter: (value: number) => string;
  showComparison: boolean;
  showActual: boolean;
  comparisonLabel: string;
}) {
  const change = productComparisonRate(value, baseline);
  const hasBaseline = baseline !== null && baseline !== undefined;
  return <div className="product-performance-cell">
    <strong>{value === null || value === undefined ? "—" : formatter(value)}</strong>
    {showComparison && <>
      {showActual && hasBaseline && <small>{formatter(baseline)}</small>}
      <em className={change === null ? "muted-text" : change < 0 ? "red-text" : "green-text"}>{comparisonLabel} {formatProductComparison(value, baseline)}</em>
    </>}
  </div>;
}

function ProductPerformancePendingCell() {
  return <div className="product-performance-pending"><strong>—</strong><small>待接入报表</small></div>;
}

function ProductPerformanceDataCell({
  column,
  item,
  compared,
  showComparison,
  showActual,
  comparisonLabel,
}: {
  column: ProductPerformanceColumn;
  item: NetshopProductPerformanceItem;
  compared?: NetshopProductPerformanceItem;
  showComparison: boolean;
  showActual: boolean;
  comparisonLabel: string;
}) {
  if (!column.available) return <ProductPerformancePendingCell />;
  const props = { showComparison, showActual, comparisonLabel };
  if (column.key === "pageViews") return <ProductPerformanceMetricCell value={item.pageViews} baseline={compared?.pageViews} formatter={formatCount} {...props} />;
  if (column.key === "visitors") return <ProductPerformanceMetricCell value={item.visitors} baseline={compared?.visitors} formatter={formatCount} {...props} />;
  if (column.key === "transactionCustomers") return <ProductPerformanceMetricCell value={item.transactionCustomers} baseline={compared?.transactionCustomers} formatter={formatCount} {...props} />;
  if (column.key === "transactionQuantity") return <ProductPerformanceMetricCell value={item.transactionQuantity} baseline={compared?.transactionQuantity} formatter={formatCount} {...props} />;
  if (column.key === "addCartCustomers") return <ProductPerformanceMetricCell value={item.addCartCustomers} baseline={compared?.addCartCustomers} formatter={formatCount} {...props} />;
  if (column.key === "addCartQuantity") return <ProductPerformanceMetricCell value={item.addCartQuantity} baseline={compared?.addCartQuantity} formatter={formatCount} {...props} />;
  if (column.key === "transactionAmount") return <ProductPerformanceMetricCell value={item.transactionAmount} baseline={compared?.transactionAmount} formatter={formatMerchantCurrency} {...props} />;
  if (column.key === "uvValue") return <ProductPerformanceMetricCell value={item.uvValue} baseline={compared?.uvValue} formatter={formatMerchantCurrency} {...props} />;
  if (column.key === "conversionRate") return <ProductPerformanceMetricCell value={item.conversionRate} baseline={compared?.conversionRate} formatter={formatRate} {...props} />;
  if (column.key === "searchImpressions") return <ProductPerformanceMetricCell value={item.searchImpressions} baseline={compared?.searchImpressions} formatter={formatCount} {...props} />;
  if (column.key === "searchClicks") return <ProductPerformanceMetricCell value={item.searchClicks} baseline={compared?.searchClicks} formatter={formatCount} {...props} />;
  if (column.key === "searchClickRate") return <ProductPerformanceMetricCell value={item.searchClickRate} baseline={compared?.searchClickRate} formatter={formatRate} {...props} />;
  if (column.key === "favorites") return <ProductPerformanceMetricCell value={item.favorites} baseline={compared?.favorites} formatter={formatCount} {...props} />;
  if (column.key === "refundAmount") return <ProductPerformanceMetricCell value={item.refundAmountCents} baseline={compared?.refundAmountCents} formatter={formatCurrencyFromCents} {...props} />;
  if (column.key === "searchVisitors") return <ProductPerformanceMetricCell value={item.searchVisitors} baseline={compared?.searchVisitors} formatter={formatCount} {...props} />;
  if (column.key === "searchTransactionCustomers") return <ProductPerformanceMetricCell value={item.searchTransactionCustomers} baseline={compared?.searchTransactionCustomers} formatter={formatCount} {...props} />;
  return <ProductPerformancePendingCell />;
}

function netshopPerformanceItemKey(item: NetshopProductPerformanceItem) {
  return `${item.platform}\u001f${item.shopNames.join("\u001e")}\u001f${item.id}`;
}

function netshopProductUrl(platform: string, productId: string, explicitUrl = "") {
  const provided = explicitUrl.trim();
  if (/^https?:\/\//i.test(provided)) return provided;
  if (provided && !/^[a-z][a-z\d+.-]*:/i.test(provided)) return `https://${provided.replace(/^\/+/, "")}`;
  const normalizedId = productId.trim();
  if (!/^\d{5,}$/.test(normalizedId)) return "";
  return platform === "天猫"
    ? `https://detail.tmall.com/item.htm?id=${encodeURIComponent(normalizedId)}`
    : `https://item.jd.com/${encodeURIComponent(normalizedId)}.html`;
}

function ShopDailyProductPerformanceView({
  dimension,
  onOpenImport,
  range,
  customStartDate,
  customEndDate,
}: {
  dimension: NetshopProductPerformanceDimension;
  onOpenImport: (dimension: NetshopProductPerformanceDimension) => void;
  range: SalesRangeLabel;
  customStartDate: string;
  customEndDate: string;
}) {
  const [currentPerformanceResponse, setCurrentPerformanceResponse] = useState<NetshopProductPerformanceResponse | null>(null);
  const [currentPerformanceLoadedScopeKey, setCurrentPerformanceLoadedScopeKey] = useState("");
  const [comparisonPerformanceResponse, setComparisonPerformanceResponse] = useState<NetshopProductPerformanceResponse | null>(null);
  const [comparisonPerformanceLoadedScopeKey, setComparisonPerformanceLoadedScopeKey] = useState("");
  const [query, setQuery] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [selectedOutletKeys, setSelectedOutletKeys] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [showComparison, setShowComparison] = useState(false);
  const [showActual, setShowActual] = useState(true);
  const [comparisonMode, setComparisonMode] = useState<ProductComparisonMode>("period");
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [columnPickerSearch, setColumnPickerSearch] = useState("");
  const [visibleColumns, setVisibleColumns] = useState<ProductPerformanceColumnKey[]>(connectedProductPerformanceColumns);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [snapshotRecoveryKey, setSnapshotRecoveryKey] = useState(0);
  const columnPickerRef = useRef<HTMLDivElement>(null);
  const productPerformanceGenerationRef = useRef(0);
  const productPerformanceControllerRef = useRef<AbortController | null>(null);
  const currentPerformanceBootstrapKeyRef = useRef("");
  const currentPerformanceSnapshotTokenRef = useRef("");
  const comparisonPerformanceBootstrapKeyRef = useRef("");
  const comparisonPerformanceSnapshotTokenRef = useRef("");
  const productPerformanceRestartedTokensRef = useRef(new Set<string>());
  const debouncedQuery = useDebouncedValue(query, 280);
  const basePeriod = useMemo(
    () => skuSalesPeriod(range, customStartDate, customEndDate),
    [customEndDate, customStartDate, range],
  );
  const selectedPeriod = basePeriod;
  useAiPageDetails("shop", {
    period: selectedPeriod,
    filters: { dataset: dimension === "sku" ? "sku_daily" : "spu_daily", query: debouncedQuery.trim(), platforms: selectedPlatforms, outletKeys: selectedOutletKeys },
  });

  const comparisonPeriod = useMemo(
    () => showComparison ? productComparisonPeriod(selectedPeriod, comparisonMode) : null,
    [comparisonMode, selectedPeriod, showComparison],
  );
  const currentPerformanceScopeKey = useMemo(() => JSON.stringify({
    dimension,
    startDate: selectedPeriod.startDate,
    endDate: selectedPeriod.endDate,
    query: debouncedQuery.trim(),
    platforms: [...selectedPlatforms].sort(),
    outlets: [...selectedOutletKeys].sort(),
  }), [debouncedQuery, dimension, selectedOutletKeys, selectedPeriod.endDate, selectedPeriod.startDate, selectedPlatforms]);
  const comparisonPerformanceScopeKey = useMemo(() => comparisonPeriod ? JSON.stringify({
    dimension,
    startDate: comparisonPeriod.startDate,
    endDate: comparisonPeriod.endDate,
    query: debouncedQuery.trim(),
    platforms: [...selectedPlatforms].sort(),
    outlets: [...selectedOutletKeys].sort(),
  }) : "", [comparisonPeriod, debouncedQuery, dimension, selectedOutletKeys, selectedPlatforms]);
  const currentPerformancePageScopeKey = currentPerformanceScopeKey;
  const [committedPerformancePageScopeKey, setCommittedPerformancePageScopeKey] = useState(currentPerformancePageScopeKey);
  const effectivePerformancePage = effectivePageForScope(page, currentPerformancePageScopeKey, committedPerformancePageScopeKey);
  const scopedCurrentPerformance = currentPerformanceLoadedScopeKey === currentPerformanceScopeKey ? currentPerformanceResponse : null;
  const currentPerformance = scopedCurrentPerformance ?? currentPerformanceResponse;
  const scopedComparisonPerformance = showComparison
    && comparisonPerformanceLoadedScopeKey === comparisonPerformanceScopeKey
    ? comparisonPerformanceResponse
    : null;
  const comparisonPerformance = showComparison
    ? scopedComparisonPerformance ?? comparisonPerformanceResponse
    : null;
  const dimensionLabel = dimension === "sku" ? "SKU" : "SPU";
  const importLabel = dimension === "sku" ? "京东商品 SKU 日数据" : "网店商品 SPU 日数据";
  const comparisonLabel = comparisonMode === "period" ? "环比" : "同比";
  const comparisonItemById = useMemo(
    () => new Map((comparisonPerformance?.items ?? []).map((item) => [netshopPerformanceItemKey(item), item])),
    [comparisonPerformance],
  );
  const matchedProductColumns = useMemo(() => {
    const keyword = columnPickerSearch.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return productPerformanceColumns;
    return productPerformanceColumns.filter((column) => column.label.toLocaleLowerCase("zh-CN").includes(keyword));
  }, [columnPickerSearch]);
  const visibleProductColumns = useMemo(
    () => productPerformanceColumns.filter((column) => visibleColumns.includes(column.key)),
    [visibleColumns],
  );

  useEffect(() => {
    setCommittedPerformancePageScopeKey(currentPerformancePageScopeKey);
    setPage((current) => current === 1 ? current : 1);
  }, [currentPerformancePageScopeKey]);

  useEffect(() => {
    if (!columnPickerOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!columnPickerRef.current?.contains(event.target as Node)) setColumnPickerOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setColumnPickerOpen(false);
    };
    window.addEventListener("pointerdown", closeOnOutsideClick);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutsideClick);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [columnPickerOpen]);

  const toggleProductColumn = (column: ProductPerformanceColumnKey) => setVisibleColumns((currentColumns) => {
    if (currentColumns.includes(column)) return currentColumns.length === 1 ? currentColumns : currentColumns.filter((item) => item !== column);
    return productPerformanceColumns.map((item) => item.key).filter((key) => currentColumns.includes(key) || key === column);
  });

  const load = useCallback(async (forceFull = false) => {
    const generation = productPerformanceGenerationRef.current + 1;
    productPerformanceGenerationRef.current = generation;
    productPerformanceControllerRef.current?.abort();
    const controller = new AbortController();
    productPerformanceControllerRef.current = controller;
    const requestPerformance = async (
      role: "current" | "comparison",
      requestedPeriod: { startDate: string; endDate: string },
      scopeKey: string,
    ) => {
      const bootstrapKeyRef = role === "current" ? currentPerformanceBootstrapKeyRef : comparisonPerformanceBootstrapKeyRef;
      const snapshotTokenRef = role === "current" ? currentPerformanceSnapshotTokenRef : comparisonPerformanceSnapshotTokenRef;
      const expectedSnapshotToken = snapshotTokenRef.current;
      const pageOnly = !forceFull
        && bootstrapKeyRef.current === scopeKey
        && snapshotTokenPattern.test(expectedSnapshotToken);
      const params = new URLSearchParams({
        dimension,
        page: String(effectivePerformancePage),
        pageSize: "50",
        startDate: requestedPeriod.startDate,
        endDate: requestedPeriod.endDate,
      });
      if (debouncedQuery) params.set("q", debouncedQuery);
      selectedPlatforms.forEach((platform) => params.append("platform", platform));
      selectedOutletKeys.forEach((outlet) => params.append("outlet", outlet));
      if (pageOnly) {
        params.set("view", "page");
        params.set("snapshotToken", expectedSnapshotToken);
      }
      const response = await fetch(`/api/netshop/product-performance?${params.toString()}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as ((NetshopProductPerformanceResponse | NetshopProductPerformancePageResponse) & { error?: string }) | null;
      const pageSnapshotMismatch = pageOnly && response.ok && payload?.snapshotToken !== expectedSnapshotToken;
      if (pageOnly && (response.status === 503 || pageSnapshotMismatch)) {
        return { kind: "restart" as const, snapshotToken: expectedSnapshotToken };
      }
      if (!response.ok || !payload) {
        throw new Error(payload?.error || `${dimensionLabel} 商品表现读取失败（${response.status}）`);
      }
      if (!snapshotTokenPattern.test(payload.snapshotToken)) throw new Error(`${dimensionLabel} 商品表现响应缺少有效数据版本`);
      if (pageOnly) {
        const pagePayload = payload as NetshopProductPerformancePageResponse;
        if (pagePayload.snapshotToken !== expectedSnapshotToken || !Array.isArray(pagePayload.items) || !pagePayload.pagination) {
          throw new Error(`${dimensionLabel} 商品分页与汇总数据版本不一致，请重新加载`);
        }
        return { kind: "page" as const, role, scopeKey, expectedSnapshotToken, payload: pagePayload };
      }
      const fullPayload = payload as NetshopProductPerformanceResponse;
      if (fullPayload.dimension !== dimension || !fullPayload.summary || !Array.isArray(fullPayload.items)
        || !Array.isArray(fullPayload.platforms) || !Array.isArray(fullPayload.shops) || !fullPayload.pagination) {
        throw new Error(`${dimensionLabel} 商品汇总响应格式不完整`);
      }
      return { kind: "full" as const, role, scopeKey, payload: fullPayload };
    };

    setLoading(true);
    setError("");
    let recoveringSnapshot = false;
    try {
      const [currentResult, comparisonResult] = await Promise.all([
        requestPerformance("current", selectedPeriod, currentPerformanceScopeKey),
        comparisonPeriod ? requestPerformance("comparison", comparisonPeriod, comparisonPerformanceScopeKey) : Promise.resolve(null),
      ]);
      const restartResult = [currentResult, comparisonResult].find((result) => result?.kind === "restart");
      if (restartResult?.kind === "restart") {
        if (!controller.signal.aborted
          && generation === productPerformanceGenerationRef.current
          && claimSnapshotRestart(productPerformanceRestartedTokensRef.current, restartResult.snapshotToken)) {
          recoveringSnapshot = true;
          currentPerformanceBootstrapKeyRef.current = "";
          currentPerformanceSnapshotTokenRef.current = "";
          comparisonPerformanceBootstrapKeyRef.current = "";
          comparisonPerformanceSnapshotTokenRef.current = "";
          setCurrentPerformanceLoadedScopeKey("");
          setComparisonPerformanceLoadedScopeKey("");
          setSnapshotRecoveryKey((value) => value + 1);
          return;
        }
        throw new Error(`${dimensionLabel} 商品数据版本持续变化，请稍后重试`);
      }
      if (!controller.signal.aborted && generation === productPerformanceGenerationRef.current) {
        if (currentResult.kind === "full") {
          currentPerformanceBootstrapKeyRef.current = currentResult.scopeKey;
          currentPerformanceSnapshotTokenRef.current = currentResult.payload.snapshotToken;
          setCurrentPerformanceLoadedScopeKey(currentResult.scopeKey);
          setCurrentPerformanceResponse(currentResult.payload);
        } else {
          setCurrentPerformanceResponse((current) => current
            && current.snapshotToken === currentResult.expectedSnapshotToken
            && currentPerformanceSnapshotTokenRef.current === currentResult.expectedSnapshotToken
            && currentPerformanceBootstrapKeyRef.current === currentResult.scopeKey
            ? { ...current, items: currentResult.payload.items, pagination: currentResult.payload.pagination }
            : current);
        }
        if (comparisonResult?.kind === "full") {
          comparisonPerformanceBootstrapKeyRef.current = comparisonResult.scopeKey;
          comparisonPerformanceSnapshotTokenRef.current = comparisonResult.payload.snapshotToken;
          setComparisonPerformanceLoadedScopeKey(comparisonResult.scopeKey);
          setComparisonPerformanceResponse(comparisonResult.payload);
        } else if (comparisonResult?.kind === "page") {
          setComparisonPerformanceResponse((current) => current
            && current.snapshotToken === comparisonResult.expectedSnapshotToken
            && comparisonPerformanceSnapshotTokenRef.current === comparisonResult.expectedSnapshotToken
            && comparisonPerformanceBootstrapKeyRef.current === comparisonResult.scopeKey
            ? { ...current, items: comparisonResult.payload.items, pagination: comparisonResult.payload.pagination }
            : current);
        }
      }
    } catch (requestError) {
      if (!controller.signal.aborted && generation === productPerformanceGenerationRef.current) setError(requestError instanceof Error ? requestError.message : `暂时无法读取 ${dimensionLabel} 商品表现`);
    } finally {
      if (!controller.signal.aborted && generation === productPerformanceGenerationRef.current) {
        if (!recoveringSnapshot) setLoading(false);
        if (productPerformanceControllerRef.current === controller) productPerformanceControllerRef.current = null;
      }
    }
  }, [comparisonPerformanceScopeKey, comparisonPeriod, currentPerformanceScopeKey, debouncedQuery, dimension, dimensionLabel, effectivePerformancePage, selectedOutletKeys, selectedPeriod, selectedPlatforms]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      productPerformanceGenerationRef.current += 1;
      productPerformanceControllerRef.current?.abort();
    };
  }, [load, retryKey, snapshotRecoveryKey]);

  if (loading && !currentPerformance) {
    return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取网店 {dimensionLabel} 日数据</strong><p>正在按当前统计周期汇总商品、店铺与经营指标…</p></section>;
  }
  if (error && !currentPerformance) {
    return <section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>{dimensionLabel} 商品表现加载失败</strong><p>{error}</p><button className="secondary-button" onClick={() => setRetryKey((value) => value + 1)}>重新加载</button></section>;
  }
  if (!currentPerformance) return null;

  const current = currentPerformance;
  const comparisonSummary = comparisonPerformance?.summary;
  const currentAverageTransactionValue = current.summary.transactionCustomers > 0
    ? current.summary.transactionAmount / current.summary.transactionCustomers
    : null;
  const comparisonAverageTransactionValue = comparisonSummary && comparisonSummary.transactionCustomers > 0
    ? comparisonSummary.transactionAmount / comparisonSummary.transactionCustomers
    : null;
  const productKpiNote = (source: string, value?: number | null, baseline?: number | null) => showComparison
    ? `${comparisonLabel} ${formatProductComparison(value, baseline)}`
    : source;
  const { pagination } = current;
  const totalPages = Math.max(1, Math.ceil(pagination.total / pagination.pageSize));
  const periodLabel = current.dateMin && current.dataCutoffDate
    ? `${current.dateMin} 至 ${current.dataCutoffDate}`
    : "暂无覆盖日期";
  const platformOptions = current.platforms.map((platform) => ({ value: platform, label: platform }));
  const availableShopOptions = current.shops
    .filter((shop) => selectedPlatforms.length === 0 || selectedPlatforms.includes(shop.platform))
    .map((shop) => ({
      value: netshopOutletFilterKey(shop.platform, shop.shopName),
      label: `${shop.shopName} · ${shop.platform}`,
      searchText: `${shop.shopName} ${shop.platform}`,
    }));
  const tableColSpan = visibleProductColumns.length + 6 + (dimension === "spu" ? 1 : 0);
  const availableCoverageLabel = current.coverage.availableDateMin && current.coverage.availableDateMax
    ? current.coverage.availableDateMin === current.coverage.availableDateMax
      ? current.coverage.availableDateMin
      : `${current.coverage.availableDateMin} 至 ${current.coverage.availableDateMax}`
    : "尚无已导入覆盖日期";
  const filterPanel = <section className="panel product-performance-filter-panel" aria-label="商品数据筛选条件">
    <label className="product-performance-select-field"><span>平台</span><SearchableMultiSelect values={selectedPlatforms} onChange={(values) => {
      const allowedOutlets = new Set(current.shops
        .filter((shop) => values.length === 0 || values.includes(shop.platform))
        .map((shop) => netshopOutletFilterKey(shop.platform, shop.shopName)));
      setSelectedPlatforms(values);
      setSelectedOutletKeys((outlets) => outlets.filter((outlet) => allowedOutlets.has(outlet)));
      setPage(1);
    }} ariaLabel={`选择${dimensionLabel}分析平台`} allLabel="全部平台" searchPlaceholder="搜索平台" options={platformOptions} /></label>
    <label className="product-performance-select-field"><span>店铺</span><SearchableMultiSelect values={selectedOutletKeys} onChange={(values) => { setSelectedOutletKeys(values); setPage(1); }} ariaLabel={`选择${dimensionLabel}分析店铺`} allLabel="全部店铺" searchPlaceholder="搜索店铺或平台" options={availableShopOptions} /></label>
    <div className="product-period-control global-period-context"><span>全局统计周期</span><strong>{selectedPeriod.startDate} 至 {selectedPeriod.endDate}</strong></div>
    <label className="product-performance-check"><input type="checkbox" checked={showComparison} onChange={(event) => setShowComparison(event.target.checked)} /><span>显示对比数据</span></label>
    <label className="product-performance-check"><input type="checkbox" checked={showActual} disabled={!showComparison} onChange={(event) => setShowActual(event.target.checked)} /><span>显示对比值</span></label>
    <div className="product-compare-control"><span>对比时间</span><div className="segmented" role="group" aria-label="商品数据对比口径"><button type="button" className={comparisonMode === "period" ? "active" : ""} disabled={!showComparison} onClick={() => setComparisonMode("period")}>环比</button><button type="button" className={comparisonMode === "year" ? "active" : ""} disabled={!showComparison} onClick={() => setComparisonMode("year")}>同比</button></div></div>
  </section>;

  if (!current.dataCutoffDate) {
    return <>
      {filterPanel}
      <section className="panel data-state data-refresh-region" aria-busy={loading}><span className="state-symbol">网</span><strong>当前筛选周期暂无{dimensionLabel}商品日数据</strong><p>当前筛选周期 {selectedPeriod.startDate} 至 {selectedPeriod.endDate}；系统数据覆盖 {availableCoverageLabel}。请调整平台或日期，未导入时可前往数据导入。</p><button className="primary-button" onClick={() => onOpenImport(dimension)}>前往导入并同步{importLabel}</button></section>
    </>;
  }

  return <>
    {filterPanel}

    <section className="panel netshop-performance-hero data-refresh-region" aria-busy={loading}>
      <div><span className="eyebrow">NETSHOP PRODUCT INTELLIGENCE</span><h2>{dimensionLabel} 商品表现</h2><p>汇总已导入的京东商智与天猫生意参谋商品日数据；接口金额统一以分返回，页面按元展示，不以销售订单明细替代。</p></div>
      <div className="netshop-performance-actions"><span><Dot tone="green" />数据截止 {current.dataCutoffDate}</span><button type="button" className="secondary-button" onClick={() => void load(true)} disabled={loading}>{loading ? "刷新中…" : "↻ 刷新"}</button><button type="button" className="primary-button" onClick={() => onOpenImport(dimension)}>＋ 导入并同步{dimensionLabel}日数据</button></div>
    </section>
    <section className="netshop-performance-source data-refresh-region" aria-busy={loading}><span><Dot tone="blue" />已关联 {current.dataset === "sku_daily" ? "商智 SKU" : "商智 SPU"} 日数据</span><strong>{periodLabel}</strong><small>当前筛选周期 {selectedPeriod.startDate} 至 {selectedPeriod.endDate}{comparisonPeriod ? ` · ${comparisonLabel} ${comparisonPeriod.startDate} 至 ${comparisonPeriod.endDate}` : ""}；系统数据覆盖 {availableCoverageLabel}。</small></section>
    <section className="store-source-status product-performance-source-status" role="note">
      <div><span className="source-status-ready">✓ 已接入</span><strong>商品访客累计、成交、退款、收藏、加购、搜索、UV 价值、转化率</strong></div>
      <div><span className="source-status-missing">○ 不推算</span><strong>店铺去重 UV、付费访客、免费访客</strong></div>
      <p>以下 KPI 随当前 {dimensionLabel}、平台、店铺和日期筛选同步汇总；商品访客是商品×日累计，不能当作店铺去重 UV。</p>
    </section>
    <section className="store-metrics-grid product-performance-kpi-grid data-refresh-region" aria-label={`${dimensionLabel} 商品数据 KPI`} aria-busy={loading}>
      <StoreMetricCard label="商品访客累计" value={formatCount(current.summary.visitors)} change={showComparison ? productComparisonRate(current.summary.visitors, comparisonSummary?.visitors) : null} note={productKpiNote("商品×日访客累计，非店铺去重 UV", current.summary.visitors, comparisonSummary?.visitors)} />
      <StoreMetricCard label="成交金额" value={formatMerchantCurrency(current.summary.transactionAmount)} change={showComparison ? productComparisonRate(current.summary.transactionAmount, comparisonSummary?.transactionAmount) : null} note={productKpiNote("商智成交金额，不等同销售净额", current.summary.transactionAmount, comparisonSummary?.transactionAmount)} />
      <StoreMetricCard label="客单价" value={formatMerchantCurrency(currentAverageTransactionValue)} change={showComparison ? productComparisonRate(currentAverageTransactionValue, comparisonAverageTransactionValue) : null} note={currentAverageTransactionValue === null ? "当前周期没有成交人数" : productKpiNote("成交金额 / 成交人数", currentAverageTransactionValue, comparisonAverageTransactionValue)} unavailable={currentAverageTransactionValue === null} />
      <StoreMetricCard label="UV 价值" value={formatMerchantCurrency(current.summary.uvValue)} change={showComparison ? productComparisonRate(current.summary.uvValue, comparisonSummary?.uvValue) : null} note={current.summary.uvValue === null ? "当前导入日数据未提供" : productKpiNote("商智 UV 价值", current.summary.uvValue, comparisonSummary?.uvValue)} unavailable={current.summary.uvValue === null} />
      <StoreMetricCard label="转化率" value={formatOptionalRate(current.summary.conversionRate)} change={showComparison ? productComparisonRate(current.summary.conversionRate, comparisonSummary?.conversionRate) : null} note={current.summary.conversionRate === null ? "当前导入日数据未提供" : productKpiNote("商智总转化率", current.summary.conversionRate, comparisonSummary?.conversionRate)} unavailable={current.summary.conversionRate === null} />
      <StoreMetricCard label="推广花费" value="查看推广分析" note="按京东/天猫推广报表独立汇总" />
      <StoreMetricCard label="推广占比" value="查看推广分析" note="只按推广与支付金额日期交集计算" />
      <StoreMetricCard label="零售占比" value="—" note="待接入订单类型标记" unavailable />
      <StoreMetricCard label="B 端占比" value="—" note="待接入企业购明细" unavailable />
      <StoreMetricCard label="推广点击数" value="查看推广分析" note="不替代付费访客" />
      <StoreMetricCard label="付费访客" value="不推算" note="避免用点击数替代访客" unavailable />
      <StoreMetricCard label="免费访客" value="不推算" note="需平台自然流量数据" unavailable />
    </section>
    {error && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>数据刷新失败</strong><p>{error}</p></div><button className="row-action" onClick={() => setRetryKey((value) => value + 1)}>重试</button></section>}
    <section className="panel table-panel netshop-performance-table-panel data-refresh-region" aria-busy={loading}>
      <div className="table-toolbar netshop-performance-toolbar"><div><h2>{dimensionLabel} 商品明细</h2><p>商智已接入指标可显示{comparisonLabel}百分比；推广与企业购指标保留为待接入列，不会以零值替代。</p></div><div className="netshop-performance-toolbar-actions"><span className="soft-tag">{formatCount(current.summary.productCount)} 个商品</span><label className="jd-sku-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索 ${dimensionLabel}、商品编码或名称`} aria-label={`搜索${dimensionLabel}商品表现`} /></label><div className={`store-column-picker product-performance-column-picker ${columnPickerOpen ? "open" : ""}`} ref={columnPickerRef}><button type="button" className="store-column-picker-trigger" aria-haspopup="dialog" aria-expanded={columnPickerOpen} onClick={() => { setColumnPickerOpen((open) => !open); setColumnPickerSearch(""); }}><span>☷</span>列设置 <em>{visibleColumns.length}/{productPerformanceColumns.length}</em></button>{columnPickerOpen && <div className="store-column-picker-menu" role="dialog" aria-label="选择商品明细指标"><div className="store-column-picker-head"><div><strong>显示指标</strong><small>商品信息、店铺名称和数据覆盖固定显示，至少保留 1 个指标</small></div><button type="button" onClick={() => setColumnPickerOpen(false)} aria-label="关闭列设置">×</button></div><div className="store-column-picker-actions"><button type="button" onClick={() => setVisibleColumns(productPerformanceColumns.map((column) => column.key))}>全选</button><button type="button" onClick={() => setVisibleColumns(connectedProductPerformanceColumns)}>仅商智已接入</button></div><label className="store-column-picker-search">⌕<input autoFocus type="search" value={columnPickerSearch} onChange={(event) => setColumnPickerSearch(event.target.value)} placeholder="搜索指标" aria-label="搜索商品明细指标" /></label><div className="store-column-picker-options">{matchedProductColumns.map((column) => { const checked = visibleColumns.includes(column.key); return <label key={column.key} className={checked ? "selected" : ""}><input type="checkbox" checked={checked} disabled={checked && visibleColumns.length === 1} onChange={() => toggleProductColumn(column.key)} /><span>{column.label}</span><em className={column.available ? "available" : "pending"}>{column.available ? "商智已接入" : "待接入"}</em></label>; })}{matchedProductColumns.length === 0 && <p className="store-column-picker-empty">没有匹配的指标</p>}</div></div>}</div></div></div>
      <div className="data-table-wrap netshop-performance-detail-scroll">
        <table className="data-table netshop-performance-data-table" style={{ minWidth: `${Math.max(dimension === "spu" ? 1750 : 1680, 850 + visibleProductColumns.length * 116 + (dimension === "spu" ? 70 : 0))}px` }}>
          <thead><tr>{dimension === "spu" && <th>商品图</th>}<th>{dimensionLabel} ID</th><th>商品名称 / 编码</th><th>平台 / 店铺</th><th>类目</th>{visibleProductColumns.map((column) => <th key={column.key}>{column.label}</th>)}<th>数据覆盖</th><th>操作</th></tr></thead>
          <tbody>
            {current.items.map((item) => {
              const productUrl = netshopProductUrl(item.platform, item.id, item.productUrl ?? "");
              const relatedIdLabel = dimension === "sku" && item.spuId ? `SPU ${item.spuId}` : dimension === "spu" && item.skuId ? `SKU ${item.skuId}` : "平台商品标识";
              const compared = comparisonItemById.get(netshopPerformanceItemKey(item));
              const thumb = item.imageUrl
                ? <img className="jd-sku-thumb" src={item.imageUrl} alt={item.productName ? `${item.productName} SPU 主图` : "SPU 主图"} loading="lazy" referrerPolicy="no-referrer" />
                : <span className="jd-sku-thumb jd-sku-thumb-missing">暂无主图</span>;
              return <tr key={netshopPerformanceItemKey(item)}>
                {dimension === "spu" && <td>{productUrl ? <a className="jd-sku-thumb-link" href={productUrl} target="_blank" rel="noreferrer">{thumb}</a> : thumb}</td>}
                <td><div className="netshop-product-id"><strong>{productUrl ? <a className="jd-sku-link" href={productUrl} target="_blank" rel="noreferrer">{item.id || "—"}</a> : item.id || "—"}</strong><small>{productUrl && relatedIdLabel !== "平台商品标识" ? <a className="jd-sku-link" href={productUrl} target="_blank" rel="noreferrer">{relatedIdLabel}</a> : relatedIdLabel}</small></div></td>
                <td><div className="jd-sku-product-name"><strong title={item.productName}>{productUrl ? <a className="jd-sku-link" href={productUrl} target="_blank" rel="noreferrer">{item.productName || "未命名商品"}</a> : item.productName || "未命名商品"}</strong><small>{item.productCode || "未提供商品编码"}</small></div></td>
                <td><span className="netshop-shop-list" title={`${item.platform} · ${item.shopNames.join("、")}`}>{item.platform || "—"} · {item.shopNames.join("、") || "—"}</span></td>
                <td><span className="jd-sku-category" title={item.category}>{item.category || "—"}</span></td>
                {visibleProductColumns.map((column) => <td key={column.key}><ProductPerformanceDataCell column={column} item={item} compared={compared} showComparison={showComparison} showActual={showActual} comparisonLabel={comparisonLabel} /></td>)}
                <td><div className="netshop-data-coverage"><strong>{item.dateMin && item.dateMax ? `${item.dateMin.slice(5)} ~ ${item.dateMax.slice(5)}` : "—"}</strong><small>{formatCount(item.dataDays)} 天</small></div></td>
                <td>{productUrl ? <a className="netshop-product-link" href={productUrl} target="_blank" rel="noreferrer">打开商品 ↗</a> : <span className="muted-text">无可用链接</span>}</td>
              </tr>;
            })}
            {!loading && current.items.length === 0 && <tr><td colSpan={tableColSpan}><div className="table-state">当前筛选条件下没有可展示的 {dimensionLabel} 商品数据。</div></td></tr>}
            {loading && current.items.length === 0 && <tr><td colSpan={tableColSpan}><div className="table-state"><span className="state-spinner" />正在刷新商品表现…</div></td></tr>}
          </tbody>
        </table>
      </div>
      <footer className="jd-sku-pagination"><span>第 {pagination.page} / {totalPages} 页</span><div><button type="button" className="row-action" disabled={loading || pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={loading || pagination.page >= totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))}>下一页</button></div></footer>
    </section>
  </>;
}

type ShopProductDataTab = "catalog" | "sku" | "spu";

function ShopProductDataView({
  onOpenImport,
  onOpenCatalogImport,
  onOpenCatalogAssetImport,
  range,
  customStartDate,
  customEndDate,
}: {
  onOpenImport: (dimension: NetshopProductPerformanceDimension) => void;
  onOpenCatalogImport: () => void;
  onOpenCatalogAssetImport: () => void;
  range: SalesRangeLabel;
  customStartDate: string;
  customEndDate: string;
}) {
  const [activeTab, setActiveTab] = useState<ShopProductDataTab>("spu");
  return <>
    <section className="shop-product-data-tabs" role="tablist" aria-label="商品数据维度">
      <button type="button" role="tab" aria-selected={activeTab === "catalog"} className={activeTab === "catalog" ? "active" : ""} onClick={() => setActiveTab("catalog")}>货品目录</button>
      <button type="button" role="tab" aria-selected={activeTab === "sku"} className={activeTab === "sku" ? "active" : ""} onClick={() => setActiveTab("sku")}>SKU（京东）</button>
      <button type="button" role="tab" aria-selected={activeTab === "spu"} className={activeTab === "spu" ? "active" : ""} onClick={() => setActiveTab("spu")}>SPU</button>
    </section>
    {activeTab === "catalog"
      ? <ShopSkuView onOpenImport={onOpenCatalogImport} onOpenAssetImport={onOpenCatalogAssetImport} range={range} customStartDate={customStartDate} customEndDate={customEndDate} />
      : <ShopDailyProductPerformanceView key={activeTab} dimension={activeTab} range={range} customStartDate={customStartDate} customEndDate={customEndDate} onOpenImport={onOpenImport} />}
  </>;
}

function ShopSkuView({
  onOpenImport,
  onOpenAssetImport,
  range,
  customStartDate,
  customEndDate,
}: {
  onOpenImport: () => void;
  onOpenAssetImport: () => void;
  range: SalesRangeLabel;
  customStartDate: string;
  customEndDate: string;
}) {
  const [catalogResponse, setCatalog] = useState<JdSkuCatalogResponse | null>(null);
  const [catalogResponseScopeKey, setCatalogResponseScopeKey] = useState("");
  const [query, setQuery] = useState("");
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [selectedOutletKeys, setSelectedOutletKeys] = useState<string[]>([]);
  const debouncedQuery = useDebouncedValue(query, 280);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [snapshotRecoveryKey, setSnapshotRecoveryKey] = useState(0);
  const skuCatalogGenerationRef = useRef(0);
  const skuCatalogControllerRef = useRef<AbortController | null>(null);
  const skuCatalogBootstrapKeyRef = useRef("");
  const skuCatalogSnapshotTokenRef = useRef("");
  const skuCatalogRestartedTokensRef = useRef(new Set<string>());
  const salesPeriod = useMemo(
    () => skuSalesPeriod(range, customStartDate, customEndDate),
    [customEndDate, customStartDate, range],
  );
  useAiPageDetails("shop", {
    period: salesPeriod,
    filters: { dataset: "netshop_catalog", query: debouncedQuery.trim(), platforms: selectedPlatforms, outletKeys: selectedOutletKeys },
  });

  const catalogBootstrapScopeKey = useMemo(() => JSON.stringify({
    startDate: salesPeriod.startDate,
    endDate: salesPeriod.endDate,
    platforms: [...selectedPlatforms].sort(),
    outlets: [...selectedOutletKeys].sort(),
  }), [salesPeriod.endDate, salesPeriod.startDate, selectedOutletKeys, selectedPlatforms]);
  const catalogPageScopeKey = useMemo(() => JSON.stringify({
    bootstrap: catalogBootstrapScopeKey,
    query: debouncedQuery.trim(),
  }), [catalogBootstrapScopeKey, debouncedQuery]);
  const [committedCatalogPageScopeKey, setCommittedCatalogPageScopeKey] = useState(catalogPageScopeKey);
  const effectiveCatalogPage = effectivePageForScope(page, catalogPageScopeKey, committedCatalogPageScopeKey);
  const scopedCatalog = catalogResponseScopeKey === catalogBootstrapScopeKey ? catalogResponse : null;
  const catalog = scopedCatalog ?? catalogResponse;

  const load = useCallback(async (forceFull = false) => {
    const expectedSnapshotToken = skuCatalogSnapshotTokenRef.current;
    const pageOnly = !forceFull
      && skuCatalogBootstrapKeyRef.current === catalogBootstrapScopeKey
      && snapshotTokenPattern.test(expectedSnapshotToken);
    const generation = skuCatalogGenerationRef.current + 1;
    skuCatalogGenerationRef.current = generation;
    skuCatalogControllerRef.current?.abort();
    const controller = new AbortController();
    skuCatalogControllerRef.current = controller;
    setLoading(true);
    setError("");
    let recoveringSnapshot = false;
    try {
      const params = new URLSearchParams({ page: String(effectiveCatalogPage), pageSize: "50" });
      params.set("startDate", salesPeriod.startDate);
      params.set("endDate", salesPeriod.endDate);
      if (debouncedQuery) params.set("q", debouncedQuery);
      selectedPlatforms.forEach((platform) => params.append("platform", platform));
      selectedOutletKeys.forEach((outlet) => params.append("outlet", outlet));
      if (pageOnly) {
        params.set("view", "page");
        params.set("snapshotToken", expectedSnapshotToken);
      }
      const response = await fetch("/api/netshop/products?" + params.toString(), { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as ((JdSkuCatalogResponse | JdSkuCatalogPageResponse) & { error?: string }) | null;
      const pageSnapshotMismatch = pageOnly && response.ok && payload?.snapshotToken !== expectedSnapshotToken;
      if (pageOnly
        && (response.status === 503 || pageSnapshotMismatch)
        && !controller.signal.aborted
        && generation === skuCatalogGenerationRef.current
        && claimSnapshotRestart(skuCatalogRestartedTokensRef.current, expectedSnapshotToken)) {
        recoveringSnapshot = true;
        skuCatalogBootstrapKeyRef.current = "";
        skuCatalogSnapshotTokenRef.current = "";
        setCatalogResponseScopeKey("");
        setSnapshotRecoveryKey((value) => value + 1);
        return;
      }
      if (!response.ok || !payload) {
        throw new Error(payload?.error || "网店货品数据读取失败（" + response.status + "）");
      }
      if (!snapshotTokenPattern.test(payload.snapshotToken)) throw new Error("网店货品响应缺少有效数据版本");
      if (pageOnly) {
        const pagePayload = payload as JdSkuCatalogPageResponse;
        if (pagePayload.snapshotToken !== expectedSnapshotToken || !Array.isArray(pagePayload.items) || !pagePayload.pagination) {
          throw new Error("网店货品分页与汇总数据版本不一致，请重新加载");
        }
        if (!controller.signal.aborted && generation === skuCatalogGenerationRef.current) {
          setCatalog((current) => current
            && current.snapshotToken === expectedSnapshotToken
            && skuCatalogSnapshotTokenRef.current === expectedSnapshotToken
            && skuCatalogBootstrapKeyRef.current === catalogBootstrapScopeKey
            ? { ...current, items: pagePayload.items, pagination: pagePayload.pagination }
            : current);
        }
      } else {
        const fullPayload = payload as JdSkuCatalogResponse;
        if (!fullPayload.summary || !Array.isArray(fullPayload.shops) || !Array.isArray(fullPayload.items) || !fullPayload.pagination) {
          throw new Error("网店货品汇总响应格式不完整");
        }
        if (!controller.signal.aborted && generation === skuCatalogGenerationRef.current) {
          skuCatalogBootstrapKeyRef.current = catalogBootstrapScopeKey;
          skuCatalogSnapshotTokenRef.current = fullPayload.snapshotToken;
          setCatalogResponseScopeKey(catalogBootstrapScopeKey);
          setCatalog(fullPayload);
        }
      }
    } catch (requestError) {
      if (!controller.signal.aborted && generation === skuCatalogGenerationRef.current) setError(requestError instanceof Error ? requestError.message : "暂时无法读取网店货品数据");
    } finally {
      if (!controller.signal.aborted && generation === skuCatalogGenerationRef.current) {
        if (!recoveringSnapshot) setLoading(false);
        if (skuCatalogControllerRef.current === controller) skuCatalogControllerRef.current = null;
      }
    }
  }, [catalogBootstrapScopeKey, debouncedQuery, effectiveCatalogPage, salesPeriod.endDate, salesPeriod.startDate, selectedOutletKeys, selectedPlatforms]);

  useEffect(() => {
    setCommittedCatalogPageScopeKey(catalogPageScopeKey);
    setPage((current) => current === 1 ? current : 1);
  }, [catalogPageScopeKey]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      skuCatalogGenerationRef.current += 1;
      skuCatalogControllerRef.current?.abort();
    };
  }, [load, retryKey, snapshotRecoveryKey]);

  if (loading && !catalog) {
    return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取网店货品目录</strong><p>正在加载各平台最近一次成功导入的商品快照…</p></section>;
  }
  if (error && !catalog) {
    return <section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>网店货品数据加载失败</strong><p>{error}</p><button className="secondary-button" onClick={() => setRetryKey((value) => value + 1)}>重新加载</button></section>;
  }
  if (!catalog?.batch) {
    return <section className="panel data-state"><span className="state-symbol">品</span><strong>尚未导入网店货品快照</strong><p>请先上传京东 SKU 或天猫店铺商品发布模板；导入完成后会在这里展示商品与 SKU。</p><button className="primary-button" onClick={onOpenImport}>前往导入货品</button></section>;
  }

  const { summary, pagination } = catalog;
  const totalPages = Math.max(1, Math.ceil(pagination.total / pagination.pageSize));
  const shops = catalog.shops ?? [];
  const sales = catalog.sales;
  return <>
    <section className="panel jd-sku-hero data-refresh-region" aria-busy={loading}>
      <div><span className="eyebrow">NETSHOP PRODUCT CATALOG</span><h2>网店货品与 SKU</h2><p>按平台与店铺读取最近一次成功导入的货品快照；商家编码仅作映射，不用于合并重复 SKU。</p></div>
      <div className="jd-sku-hero-actions"><span><Dot tone="green" />已同步 {catalog.batch.snapshotDate ?? "最新"} 快照</span><button type="button" className="secondary-button" onClick={() => void load(true)} disabled={loading}>{loading ? "刷新中…" : "↻ 刷新"}</button><button type="button" className="secondary-button" onClick={onOpenAssetImport}>＋ 导入天猫 SPU 图</button><button type="button" className="primary-button" onClick={onOpenImport}>＋ 导入天猫货品</button></div>
    </section>
    <section className="metrics-grid jd-sku-metrics data-refresh-region" aria-busy={loading}>
      <MetricCard label="SKU 总数" value={formatCount(summary.totalSkus) + " 个"} change="已同步" hint={"文件：" + catalog.batch.fileName} tone="blue" />
      <MetricCard label="已标记上架 SKU" value={formatCount(summary.onSaleSkus) + " 个"} change="平台字段" hint="未提供状态的平台不做推算" tone="green" />
      <MetricCard label="商品总库存" value={formatCount(summary.totalInventory)} change="最新快照" hint="SKU 商品总库存汇总" tone="purple" />
      <MetricCard label="商品可用库存" value={formatCount(summary.availableInventory)} change="最新快照" hint={"导入完成：" + formatDateTime(catalog.batch.completedAt)} tone="orange" />
    </section>
    {sales?.periodStart && sales?.periodEnd && <section className="jd-sku-sales-context"><strong>经营指标口径</strong><span>{sales.platform}平台已导入销售明细 · 统计周期 {sales.periodStart} 至 {sales.periodEnd} · 数据截止 {sales.dataCutoffDate ?? "暂无"}</span><small>成本价为当前周期销量加权成本；净销售额、毛利率与退货率均不按店铺名称推算。</small></section>}
    {error && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>数据刷新失败</strong><p>{error}</p></div><button className="row-action" onClick={() => setRetryKey((value) => value + 1)}>重试</button></section>}
    <section className="panel table-panel jd-sku-table-panel">
      <div className="table-toolbar jd-sku-toolbar"><div><h2>SKU 商品目录</h2><p>共 {formatCount(pagination.total)} 条；可按店铺、商品 ID、SKU、商家编码或名称搜索。</p></div><div className="jd-sku-toolbar-actions"><label className="jd-sku-store-select"><span>平台</span><SearchableMultiSelect values={selectedPlatforms} onChange={(values) => { const allowedOutlets = new Set(shops.filter((shop) => values.length === 0 || values.includes(shop.platform)).map((shop) => netshopOutletFilterKey(shop.platform, shop.shopName))); setSelectedPlatforms(values); setSelectedOutletKeys((current) => current.filter((outlet) => allowedOutlets.has(outlet))); setPage(1); }} ariaLabel="按平台筛选货品" allLabel="全部平台" searchPlaceholder="搜索平台" options={[...new Set(shops.map((shop) => shop.platform))].map((platform) => ({ value: platform, label: platform }))} /></label><label className="jd-sku-store-select"><span>店铺</span><SearchableMultiSelect values={selectedOutletKeys} onChange={(values) => { setSelectedOutletKeys(values); setPage(1); }} ariaLabel="按店铺名称筛选" allLabel="全部店铺" searchPlaceholder="搜索店铺或平台" options={shops.filter((shop) => selectedPlatforms.length === 0 || selectedPlatforms.includes(shop.platform)).map((shop) => ({ value: netshopOutletFilterKey(shop.platform, shop.shopName), label: `${shop.shopName || "未命名店铺"} · ${shop.platform}`, searchText: `${shop.shopName} ${shop.platform}` }))} /></label><label className="jd-sku-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索商品 ID、SKU、商家编码或名称" aria-label="搜索网店商品 SKU" /></label></div></div>
      <div className="data-table-wrap data-refresh-region" aria-busy={loading}><table className="data-table jd-sku-data-table"><thead><tr><th>SKU图</th><th>平台 / 店铺</th><th>商品 ID</th><th>SKU ID</th><th>商家编码</th><th>商品名称 / 销售属性</th><th>平台售价</th><th>库存</th><th>净销售额</th><th>大毛利率</th><th>类目</th><th>快照 / 状态</th></tr></thead><tbody>{catalog.items.map((item) => { const link = netshopProductUrl(item.platform, item.spuId, item.productUrl); const isOnSale = item.status === "上架"; const thumb = item.imageUrl ? <img className="jd-sku-thumb" src={item.imageUrl} alt={item.productName ? `${item.productName} SKU 主图` : "SKU 主图"} loading="lazy" referrerPolicy="no-referrer" /> : <span className="jd-sku-thumb jd-sku-thumb-missing">暂无主图</span>; return <tr key={`${item.platform}-${item.shopName}-${item.spuId}-${item.skuId}-${item.saleAttribute}`}><td>{link ? <a className="jd-sku-thumb-link" href={link} target="_blank" rel="noreferrer">{thumb}</a> : thumb}</td><td><span className="soft-tag">{item.platform}</span><small className="jd-sku-shop-name" title={item.shopName}>{item.shopName || "未命名店铺"}</small></td><td>{link ? <a className="jd-sku-link" href={link} target="_blank" rel="noreferrer">{item.spuId || "—"}</a> : item.spuId || "—"}</td><td>{link && item.skuId ? <a className="jd-sku-link" href={link} target="_blank" rel="noreferrer">{item.skuId}</a> : item.skuId || "—"}</td><td>{item.productCode || "—"}</td><td><div className="jd-sku-product-name"><strong title={item.productName}>{link ? <a className="jd-sku-link" href={link} target="_blank" rel="noreferrer">{item.productName || "未命名商品"}</a> : item.productName || "未命名商品"}</strong><small>{item.saleAttribute || item.brand || "—"}</small></div></td><td><strong>{formatOptionalCurrencyFromCents(item.priceCents)}</strong></td><td><strong>{item.availableInventory === null ? "—" : formatCount(item.availableInventory)}</strong></td><td className="jd-sku-money-cell"><strong>{formatOptionalCurrencyFromCents(item.netSalesCents)}</strong></td><td className={item.grossMarginRate !== null && item.grossMarginRate < .35 ? "orange-text" : "green-text"}><strong>{formatOptionalRate(item.grossMarginRate)}</strong></td><td><span className="jd-sku-category" title={item.category}>{item.category || "—"}</span></td><td><small>{item.snapshotDate || "—"}</small><span className={"status " + (isOnSale ? "status-success" : "status-warning")}><Dot tone={isOnSale ? "green" : "orange"} />{item.status || "未标记"}</span></td></tr>; })}{!loading && catalog.items.length === 0 && <tr><td colSpan={12}><div className="table-state">没有符合当前筛选条件的 SKU 数据。</div></td></tr>}{loading && catalog.items.length === 0 && <tr><td colSpan={12}><div className="table-state"><span className="state-spinner" />正在刷新 SKU 目录…</div></td></tr>}</tbody></table></div>
      <footer className="jd-sku-pagination"><span>第 {pagination.page} / {totalPages} 页</span><div><button type="button" className="row-action" disabled={loading || pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={loading || pagination.page >= totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))}>下一页</button></div></footer>
    </section>
  </>;
}

type PromotionPageKey = "jd" | "tmall";
type PromotionPageSize = 20 | 50 | 100 | 500;

const promotionPageSizes: PromotionPageSize[] = [20, 50, 100, 500];

const promotionPageConfig: Record<PromotionPageKey, {
  platform: "京东" | "天猫";
  eyebrow: string;
  title: string;
  description: string;
  paymentSource: string;
  transactionLabel: string;
  transactionShortLabel: string;
  ordersLabel: string;
}> = {
  jd: {
    platform: "京东",
    eyebrow: "JD PROMOTION",
    title: "京东推广",
    description: "按京准通 AI 推广报表中的跟单 SKU 汇总；总订单金额、总订单行、花费、展现和点击均从来源字段重新聚合。",
    paymentSource: "京东商智成交金额",
    transactionLabel: "总订单金额",
    transactionShortLabel: "订单金额",
    ordersLabel: "总订单行",
  },
  tmall: {
    platform: "天猫",
    eyebrow: "TMALL PROMOTION",
    title: "天猫推广",
    description: "按天猫推广商品报表汇总；比例只使用推广报表与生意参谋商品日数据的日期交集，各行投产比不会被直接平均。",
    paymentSource: "生意参谋支付金额",
    transactionLabel: "推广净成交",
    transactionShortLabel: "推广净成交",
    ordersLabel: "净成交笔数",
  },
};

function ShopPromotionView({
  range,
  customStartDate,
  customEndDate,
  onOpenTmallImport,
}: {
  range: SalesRangeLabel;
  customStartDate: string;
  customEndDate: string;
  onOpenTmallImport: () => void;
}) {
  const period = useMemo(() => skuSalesPeriod(range, customStartDate, customEndDate), [customEndDate, customStartDate, range]);
  const [promotionPage, setPromotionPage] = useState<PromotionPageKey>("jd");
  const [promotionItems, setPromotionItems] = useState<NetshopPromotionItemsResponse | null>(null);
  const [promotionItemsScopeKey, setPromotionItemsScopeKey] = useState("");
  const [promotionOverview, setPromotionOverview] = useState<NetshopPromotionOverviewResponse | null>(null);
  const [promotionOverviewScopeKey, setPromotionOverviewScopeKey] = useState("");
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 280);
  const promotionQuery = query.trim() === "" ? "" : debouncedQuery.trim();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PromotionPageSize>(20);
  const [selectedOutletKeys, setSelectedOutletKeys] = useState<string[]>([]);
  const [itemsLoading, setItemsLoading] = useState(true);
  const [itemsError, setItemsError] = useState("");
  const [itemsRetryKey, setItemsRetryKey] = useState(0);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overviewError, setOverviewError] = useState("");
  const [overviewRetryKey, setOverviewRetryKey] = useState(0);
  const [promotionDisplayPair, setPromotionDisplayPair] = useState<{ items: NetshopPromotionItemsResponse; overview: NetshopPromotionOverviewResponse | null } | null>(null);
  const promotionItemsGenerationRef = useRef(0);
  const promotionItemsControllerRef = useRef<AbortController | null>(null);
  const promotionOverviewGenerationRef = useRef(0);
  const promotionOverviewControllerRef = useRef<AbortController | null>(null);
  const promotionSnapshotRestartedTokensRef = useRef(new Set<string>());
  const pageConfig = promotionPageConfig[promotionPage];
  useAiPageDetails("shop", {
    period,
    filters: { dataset: "netshop_promotion", platforms: [pageConfig.platform], outletKeys: selectedOutletKeys, query: promotionQuery },
  });

  const promotionScopeKey = useMemo(() => JSON.stringify({
    startDate: period.startDate,
    endDate: period.endDate,
    platform: pageConfig.platform,
    outlets: [...selectedOutletKeys].sort(),
  }), [pageConfig.platform, period.endDate, period.startDate, selectedOutletKeys]);
  const promotionPageScopeKey = useMemo(() => JSON.stringify({
    bootstrap: promotionScopeKey,
    query: promotionQuery,
    pageSize,
  }), [pageSize, promotionQuery, promotionScopeKey]);
  const [committedPromotionPageScopeKey, setCommittedPromotionPageScopeKey] = useState(promotionPageScopeKey);
  const effectivePromotionPage = effectivePageForScope(page, promotionPageScopeKey, committedPromotionPageScopeKey);
  const scopedItems = promotionItemsScopeKey === promotionScopeKey ? promotionItems : null;
  const promotionItemsSnapshotToken = scopedItems?.snapshotToken ?? "";
  const scopedOverview = promotionOverviewScopeKey === promotionScopeKey
    && promotionOverview?.snapshotToken === promotionItemsSnapshotToken
    ? promotionOverview
    : null;
  const hasCompleteScopedPair = Boolean(scopedItems && scopedOverview);
  const currentItems = hasCompleteScopedPair || !promotionDisplayPair
    ? scopedItems
    : promotionDisplayPair.items;
  const currentOverview = hasCompleteScopedPair || !promotionDisplayPair
    ? scopedOverview
    : promotionDisplayPair.overview;

  useEffect(() => {
    if (scopedItems && scopedOverview) {
      setPromotionDisplayPair({ items: scopedItems, overview: scopedOverview });
      return;
    }
    if (scopedItems) setPromotionDisplayPair((current) => current ?? { items: scopedItems, overview: null });
  }, [scopedItems, scopedOverview]);
  const promotionShops = (currentOverview?.filterOptions.shops ?? [])
    .filter((shop) => shop.platform === pageConfig.platform);

  const selectPromotionPage = (nextPage: PromotionPageKey) => {
    if (nextPage === promotionPage) return;
    setPromotionDisplayPair(null);
    setPromotionPage(nextPage);
    setPromotionItems(null);
    setPromotionItemsScopeKey("");
    setPromotionOverview(null);
    setPromotionOverviewScopeKey("");
    setQuery("");
    setPage(1);
    setSelectedOutletKeys([]);
    setItemsError("");
    setOverviewError("");
  };

  useEffect(() => {
    setCommittedPromotionPageScopeKey(promotionPageScopeKey);
    setPage((current) => current === 1 ? current : 1);
  }, [promotionPageScopeKey]);

  useEffect(() => {
    const generation = promotionItemsGenerationRef.current + 1;
    promotionItemsGenerationRef.current = generation;
    promotionItemsControllerRef.current?.abort();
    const controller = new AbortController();
    promotionItemsControllerRef.current = controller;
    void (async () => {
      setItemsLoading(true);
      setItemsError("");
      try {
        const params = new URLSearchParams({
          startDate: period.startDate,
          endDate: period.endDate,
          platform: pageConfig.platform,
          page: String(effectivePromotionPage),
          pageSize: String(pageSize),
        });
        selectedOutletKeys.forEach((outlet) => params.append("outlet", outlet));
        if (promotionQuery) params.set("q", promotionQuery);
        const response = await fetch(`/api/netshop/promotion-performance/items?${params.toString()}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (NetshopPromotionItemsResponse & { error?: string; code?: string }) | null;
        if (!response.ok || !payload?.pagination || !Array.isArray(payload.items)) throw new Error(payload?.error || `推广商品读取失败（${response.status}）`);
        if (!snapshotTokenPattern.test(payload.snapshotToken)) throw new Error("推广商品响应缺少有效数据版本");
        if (!controller.signal.aborted && generation === promotionItemsGenerationRef.current) {
          setPromotionItems(payload);
          setPromotionItemsScopeKey(promotionScopeKey);
        }
      } catch (requestError) {
        if (!controller.signal.aborted && generation === promotionItemsGenerationRef.current) setItemsError(requestError instanceof Error ? requestError.message : "暂时无法读取推广商品");
      } finally {
        if (!controller.signal.aborted && generation === promotionItemsGenerationRef.current) {
          setItemsLoading(false);
          if (promotionItemsControllerRef.current === controller) promotionItemsControllerRef.current = null;
        }
      }
    })();
    return () => {
      promotionItemsGenerationRef.current += 1;
      controller.abort();
    };
  }, [effectivePromotionPage, itemsRetryKey, pageConfig.platform, pageSize, period.endDate, period.startDate, promotionQuery, promotionScopeKey, selectedOutletKeys]);

  useEffect(() => {
    if (!promotionItemsSnapshotToken) {
      setOverviewLoading(itemsLoading);
      setOverviewError("");
      return;
    }
    const generation = promotionOverviewGenerationRef.current + 1;
    promotionOverviewGenerationRef.current = generation;
    promotionOverviewControllerRef.current?.abort();
    const controller = new AbortController();
    promotionOverviewControllerRef.current = controller;
    let restartingPair = false;
    void (async () => {
      setOverviewLoading(true);
      setOverviewError("");
      try {
        const params = new URLSearchParams({
          startDate: period.startDate,
          endDate: period.endDate,
          platform: pageConfig.platform,
        });
        selectedOutletKeys.forEach((outlet) => params.append("outlet", outlet));
        params.set("snapshotToken", promotionItemsSnapshotToken);
        const response = await fetch(`/api/netshop/promotion-performance/overview?${params.toString()}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (NetshopPromotionOverviewResponse & { error?: string; code?: string }) | null;
        const tokenMismatch = response.ok && payload?.snapshotToken !== promotionItemsSnapshotToken;
        if ((response.status === 503 || tokenMismatch)
          && !controller.signal.aborted
          && generation === promotionOverviewGenerationRef.current
          && claimSnapshotRestart(promotionSnapshotRestartedTokensRef.current, promotionItemsSnapshotToken)) {
          restartingPair = true;
          setPromotionItems(null);
          setPromotionItemsScopeKey("");
          setPromotionOverview(null);
          setPromotionOverviewScopeKey("");
          setItemsError("");
          setOverviewError("");
          setOverviewLoading(true);
          setItemsRetryKey((value) => value + 1);
          return;
        }
        if (!response.ok || !payload?.summary || !Array.isArray(payload.daily) || !payload.filterOptions) throw new Error(payload?.error || `推广概览读取失败（${response.status}）`);
        if (!snapshotTokenPattern.test(payload.snapshotToken) || payload.snapshotToken !== promotionItemsSnapshotToken) {
          throw new Error("推广商品与概览数据版本不一致，请重新加载");
        }
        if (!controller.signal.aborted && generation === promotionOverviewGenerationRef.current) {
          setPromotionOverview(payload);
          setPromotionOverviewScopeKey(promotionScopeKey);
        }
      } catch (requestError) {
        if (!controller.signal.aborted && generation === promotionOverviewGenerationRef.current) setOverviewError(requestError instanceof Error ? requestError.message : "暂时无法读取推广概览");
      } finally {
        if (!controller.signal.aborted && generation === promotionOverviewGenerationRef.current) {
          if (!restartingPair) setOverviewLoading(false);
          if (promotionOverviewControllerRef.current === controller) promotionOverviewControllerRef.current = null;
        }
      }
    })();
    return () => {
      promotionOverviewGenerationRef.current += 1;
      controller.abort();
    };
  }, [itemsLoading, overviewRetryKey, pageConfig.platform, period.endDate, period.startDate, promotionItemsSnapshotToken, promotionScopeKey, selectedOutletKeys]);

  const platformSubnav = <div className="subnav promotion-platform-subnav" role="tablist" aria-label="推广分析平台">
    <button type="button" role="tab" aria-selected={promotionPage === "jd"} className={promotionPage === "jd" ? "active" : ""} onClick={() => selectPromotionPage("jd")}>京东推广</button>
    <button type="button" role="tab" aria-selected={promotionPage === "tmall"} className={promotionPage === "tmall" ? "active" : ""} onClick={() => selectPromotionPage("tmall")}>天猫推广</button>
  </div>;

  const selectedPromotionShops = promotionShops.filter((shop) => selectedOutletKeys.includes(netshopOutletFilterKey(shop.platform, shop.shopName)));
  const selectedShopLabel = selectedOutletKeys.length === 0
    ? `全部 ${formatCount(promotionShops.length)} 家店铺`
    : selectedPromotionShops.length === 1 && selectedOutletKeys.length === 1
      ? selectedPromotionShops[0].shopName
      : `已选 ${formatCount(selectedOutletKeys.length)} 家店铺`;
  const promotionFilterPanel = <section className="panel store-filter-panel" aria-label={`${pageConfig.title}店铺筛选`}>
    <label className="store-select-field"><span>店铺</span><SearchableMultiSelect values={selectedOutletKeys} onChange={(values) => { setSelectedOutletKeys(values); setPage(1); }} ariaLabel={`筛选${pageConfig.title}店铺`} allLabel="全部店铺" searchPlaceholder="搜索店铺" options={promotionShops.map((shop) => ({ value: netshopOutletFilterKey(shop.platform, shop.shopName), label: shop.shopName || "未命名店铺", searchText: `${shop.shopName} ${shop.platform}` }))} /></label>
    <div className="store-period-context"><span>统计范围</span><strong>{selectedShopLabel}</strong><small>{period.startDate} 至 {period.endDate}{overviewLoading && promotionShops.length === 0 ? " · 店铺载入中" : ""}</small></div>
  </section>;
  const refreshPromotion = () => {
    setPromotionItemsScopeKey("");
    setPromotionOverviewScopeKey("");
    setItemsRetryKey((value) => value + 1);
  };

  if (itemsLoading && !currentItems && !currentOverview) return <>{platformSubnav}<section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取{pageConfig.title}商品排行</strong><p>先加载首批 {pageSize} 条商品，汇总和趋势将在商品出现后继续加载。</p></section></>;
  if (!currentItems && !currentOverview && itemsError) return <>{platformSubnav}{promotionFilterPanel}<section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>{pageConfig.title}商品加载失败</strong><p>{itemsError}；推广概览将在商品排行重新加载成功后继续。</p><button className="secondary-button" onClick={() => setItemsRetryKey((value) => value + 1)}>重试商品排行</button></section></>;

  const dataCutoffDate = currentItems?.dataCutoffDate ?? currentOverview?.dataCutoffDate ?? null;
  if (!itemsLoading && !overviewLoading && currentItems && !dataCutoffDate) return <>{platformSubnav}{promotionFilterPanel}<section className="panel data-state data-refresh-region" aria-busy={itemsLoading || overviewLoading}><span className="state-symbol">推</span><strong>当前周期尚未导入{pageConfig.title}数据</strong><p>{promotionPage === "jd" ? "请先运行京准通 AI 推广数据工作流并完成导入回查；推广费率和推广成交占比还需要同日期京东商智 SKU 日数据。" : "请上传对应店铺的天猫推广商品报表 ZIP；推广费率和推广成交占比还需要同日期生意参谋商品日数据。"}</p>{promotionPage === "tmall" ? <button className="primary-button" onClick={onOpenTmallImport}>前往导入推广数据</button> : <button className="secondary-button" onClick={refreshPromotion}>重新检查</button>}</section></>;

  const pagination = currentItems?.pagination ?? { page, pageSize, total: 0, returned: 0, truncated: false };
  const items = currentItems?.items ?? [];
  const maxTrend = Math.max(1, ...(currentOverview?.daily ?? []).map((item) => Math.max(item.spendCents, item.netTransactionAmountCents)));
  const promotionDiagnosticDays = Math.round((Date.parse(`${period.endDate}T00:00:00Z`) - Date.parse(`${period.startDate}T00:00:00Z`)) / 86_400_000) + 1;
  const totalPages = Math.max(1, Math.ceil(pagination.total / pagination.pageSize));
  return <>
    {platformSubnav}
    {promotionFilterPanel}
    <section className="panel jd-sku-hero data-refresh-region" aria-busy={itemsLoading || overviewLoading}>
      <div><span className="eyebrow">{pageConfig.eyebrow}</span><h2>{pageConfig.title}</h2><p>{pageConfig.description}</p></div>
      <div className="jd-sku-hero-actions"><span><Dot tone="green" />{selectedShopLabel} · 数据截止 {dataCutoffDate ?? "载入中"}</span><button type="button" className="secondary-button" onClick={refreshPromotion} disabled={itemsLoading || overviewLoading}>{itemsLoading || overviewLoading ? "刷新中…" : "↻ 刷新"}</button>{promotionPage === "tmall" && <button type="button" className="primary-button" onClick={onOpenTmallImport}>＋ 导入推广报表</button>}</div>
    </section>
    {promotionPage === "jd" && (selectedPromotionShops.length === 1 && selectedPromotionShops[0].shopName === "志高商用设备旗舰店" && hasCompleteScopedPair && promotionDiagnosticDays >= 1 && promotionDiagnosticDays <= 7
      ? <PromotionDiagnosticPanel key={`${selectedPromotionShops[0].shopName}:${period.startDate}:${period.endDate}`} shopName={selectedPromotionShops[0].shopName} startDate={period.startDate} endDate={period.endDate} />
      : <section className="panel"><h2>推广深度诊断</h2><p>首版支持京东志高商用设备旗舰店的单店 1—7 个完整自然日；选择该店与周期后，可核对来源并生成 HTML 和 XLSX。</p></section>)}
    {overviewLoading && !currentOverview && <section className="panel promotion-overview-state" role="status"><span className="state-spinner" /><div><strong>商品排行已就绪，正在加载推广概览</strong><p>花费、成交、覆盖和日趋势将在这里补充显示。</p></div></section>}
    {overviewError && !currentOverview && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>推广概览加载失败</strong><p>{overviewError}；商品排行仍可继续搜索和翻页。</p></div><button className="row-action" onClick={() => setOverviewRetryKey((value) => value + 1)}>重试概览</button></section>}
    {currentOverview && <>
      <section className="metrics-grid data-refresh-region" aria-busy={itemsLoading || overviewLoading}>
        <MetricCard label="推广花费" value={formatCurrencyFromCents(currentOverview.summary.spendCents)} change={formatOptionalRate(currentOverview.summary.spendRate)} hint={`花费 / ${pageConfig.paymentSource}`} tone="blue" />
        <MetricCard label={pageConfig.transactionLabel} value={formatCurrencyFromCents(currentOverview.summary.netTransactionAmountCents)} change={formatOptionalRate(currentOverview.summary.promotionTransactionShare)} hint={`${pageConfig.transactionShortLabel} / ${pageConfig.paymentSource}`} tone="purple" />
        <MetricCard label="推广 ROAS" value={currentOverview.summary.roas === null ? "—" : currentOverview.summary.roas.toFixed(2)} change={`${pageConfig.transactionShortLabel} / 花费`} hint={`${formatCount(currentOverview.summary.netOrders)} ${pageConfig.ordersLabel}`} tone="green" />
        <MetricCard label="展现 / 点击" value={`${formatCount(currentOverview.summary.impressions)} / ${formatCount(currentOverview.summary.clicks)}`} change={formatOptionalRate(currentOverview.summary.clickThroughRate)} hint={`平均点击花费 ${formatOptionalCurrencyFromCents(currentOverview.summary.averageClickCostCents)}`} tone="orange" />
      </section>
      <section className="store-source-status" role="note">
        <div><span className="source-status-ready">✓ 比例交集</span><strong>{currentOverview.coverage.intersectionDates.length ? `${currentOverview.coverage.intersectionDates[0]} 至 ${currentOverview.coverage.intersectionDates[currentOverview.coverage.intersectionDates.length - 1]}` : `暂无同日${pageConfig.paymentSource}`}</strong></div>
        <div><span className={currentOverview.coverage.missingProductDailyDates.length ? "source-status-missing" : "source-status-ready"}>{currentOverview.coverage.missingProductDailyDates.length ? "○ 覆盖缺口" : "✓ 覆盖完整"}</span><strong>{currentOverview.coverage.missingProductDailyDates.length ? `缺${pageConfig.paymentSource}：${currentOverview.coverage.missingProductDailyDates.join("、")}` : `${currentOverview.coverage.intersectionDates.length} 个共同业务日`}</strong></div>
        <p>{promotionPage === "jd" ? "京准通总订单金额不是退款后的销售净额；比例不与 ERP 销售净额混算。" : "推广费率与推广成交占比均采用平台报表口径，不与 ERP 销售净额混算。"}</p>
      </section>
      <section className="panel trend-panel data-refresh-region" aria-busy={overviewLoading}>
        <SectionHeader title="推广日趋势" note={`蓝色为花费，紫色为${pageConfig.transactionShortLabel}；金额单位为人民币元。`} />
        <div className="chart-legend"><span><Dot tone="blue" />推广花费</span><span><Dot tone="purple" />{pageConfig.transactionShortLabel}</span></div>
        <div className="bar-chart">{currentOverview.daily.map((item) => <div className="bar-group" key={item.date}><div className="bar-stack"><span className="bar sales-bar" style={{ height: `${Math.max(2, item.spendCents / maxTrend * 100)}%` }} /><span className="bar profit-bar" style={{ height: `${Math.max(2, item.netTransactionAmountCents / maxTrend * 100)}%` }} /></div><small>{item.date.slice(5)}</small></div>)}</div>
      </section>
      {overviewError && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>推广概览刷新失败</strong><p>{overviewError}；当前仍显示上一次成功结果。</p></div><button className="row-action" onClick={() => setOverviewRetryKey((value) => value + 1)}>重试概览</button></section>}
    </>}
    {itemsError && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>推广商品加载失败</strong><p>{itemsError}{currentItems ? "；当前仍显示上一次成功结果。" : ""}</p></div><button className="row-action" onClick={() => setItemsRetryKey((value) => value + 1)}>重试商品排行</button></section>}
    <section className="panel table-panel data-refresh-region" aria-busy={itemsLoading}>
      <div className="table-toolbar"><div><h2>推广商品排行</h2><p>共 {formatCount(pagination.total)} 个{promotionPage === "jd" ? "跟单 SKU" : "商品"}；搜索只筛选排行，顶部平台汇总保持稳定。</p></div><label className="jd-sku-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={promotionPage === "jd" ? "搜索跟单 SKU 或产品线" : "搜索商品 ID 或名称"} aria-label="搜索推广商品" /></label></div>
      <div className="data-table-wrap"><table className="data-table" aria-busy={itemsLoading}><thead><tr><th>{promotionPage === "jd" ? "跟单 SKU / 产品线" : "商品"}</th><th>推广花费</th><th>{pageConfig.transactionLabel}</th><th>ROAS</th><th>展现</th><th>点击 / CTR</th><th>平均点击花费</th><th>{pageConfig.ordersLabel}</th>{promotionPage === "tmall" && <th>收藏 / 加购</th>}<th>覆盖日期</th></tr></thead><tbody>{items.map((item) => { const productLink = netshopProductUrl(item.platform, item.id); return <tr key={`${item.platform}-${item.shopName}-${item.id}`}><td><div className="jd-sku-product-name">{productLink ? <a className="jd-sku-link" href={productLink} target="_blank" rel="noreferrer">{item.id}</a> : <strong>{item.id}</strong>}<strong title={item.productName}>{item.productName || (promotionPage === "jd" ? "未提供产品线" : "未命名商品")}</strong></div></td><td>{formatCurrencyFromCents(item.spendCents)}</td><td><strong>{formatCurrencyFromCents(item.netTransactionAmountCents)}</strong></td><td>{item.roas === null ? "—" : item.roas.toFixed(2)}</td><td>{formatCount(item.impressions)}</td><td>{formatCount(item.clicks)} / {formatOptionalRate(item.clickThroughRate)}</td><td>{formatOptionalCurrencyFromCents(item.averageClickCostCents)}</td><td>{formatCount(item.netOrders)}</td>{promotionPage === "tmall" && <td>{formatCount(item.favorites)} / {formatCount(item.cartQuantity)}</td>}<td data-column-filter-values={item.dates?.join("\u001f")}>{item.dateMin ?? "—"}{item.dateMax && item.dateMax !== item.dateMin ? ` 至 ${item.dateMax}` : ""}</td></tr>; })}{itemsLoading && items.length === 0 && <tr><td colSpan={promotionPage === "jd" ? 9 : 10}><div className="table-state"><span className="state-spinner" />正在加载推广商品…</div></td></tr>}{!itemsLoading && !itemsError && items.length === 0 && <tr><td colSpan={promotionPage === "jd" ? 9 : 10}><div className="table-state">没有符合当前搜索条件的推广商品。</div></td></tr>}</tbody></table></div>
      <footer className="jd-sku-pagination promotion-pagination"><div className="promotion-page-size"><span>每页加载</span><SearchableSelect value={String(pageSize)} disabled={itemsLoading} onChange={(value) => { setPageSize(Number(value) as PromotionPageSize); setPage(1); }} ariaLabel="每页加载条数" searchPlaceholder="搜索加载条数" options={promotionPageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} /></div><span>第 {pagination.page} / {totalPages} 页 · 共 {formatCount(pagination.total)} 条</span><div><button type="button" className="row-action" disabled={itemsLoading || pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={itemsLoading || pagination.page >= totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))}>下一页</button></div></footer>
    </section>
  </>;
}

type OutletTab = ModuleViewKey<"shop">;

export default function ShopView({ range, customStartDate, customEndDate, onNavigate, moduleView, onModuleViewChange }: { range: SalesRangeLabel; customStartDate: string; customEndDate: string; onNavigate: (key: ModuleKey, importSource?: ImportSourceKey) => void; moduleView: OutletTab; onModuleViewChange: (view: OutletTab) => void }) {
  const apiRange = salesRangeMap[range];
  const activeTab = moduleView;
  const [summary, setSummary] = useState<SalesSummaryResponse | null>(null);
  const [analysisSummary, setAnalysisSummary] = useState<SalesSummaryResponse | null>(null);
  const [selectedOutletKeys, setSelectedOutletKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [platformFilters, setPlatformFilters] = useState<string[]>([]);
  useAiPageDetails("shop", {
    period: skuSalesPeriod(range, customStartDate, customEndDate),
    filters: activeTab === "outlets" ? { platforms: platformFilters } : {},
  }, activeTab === "outlets" || activeTab === "platforms");

  const requestGenerationRef = useRef(0);
  const requestControllerRef = useRef<AbortController | null>(null);

  const loadOutlets = useCallback(async () => {
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    requestControllerRef.current?.abort();
    requestControllerRef.current = null;
    if (activeTab === "products" || activeTab === "promotion") {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ range: apiRange });
      if (apiRange === "custom") {
        query.set("startDate", customStartDate);
        query.set("endDate", customEndDate);
      }
      if (activeTab === "analysis") selectedOutletKeys.forEach((key) => query.append("outlet", key));
      const payload = await requestJson<SalesSummaryResponse>(`/api/sales/summary?${query.toString()}`, { signal: controller.signal });
      if (!payload?.current || !Array.isArray(payload.outlets)) throw new Error("网店数据响应格式不完整");
      if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
      const availableOutletKeys = new Set((payload.filterOptions?.shops ?? payload.outlets).map((item) => "key" in item ? item.key : item.groupKey));
      const validOutletKeys = selectedOutletKeys.filter((key) => availableOutletKeys.has(key));
      setSummary(payload);
      setAnalysisSummary(payload);
      if (validOutletKeys.length !== selectedOutletKeys.length) setSelectedOutletKeys(validOutletKeys);
    } catch (requestError) {
      if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
      setError(requestError instanceof Error ? requestError.message : "暂时无法读取网店数据");
    } finally {
      if (!controller.signal.aborted && generation === requestGenerationRef.current) {
        setLoading(false);
        if (requestControllerRef.current === controller) requestControllerRef.current = null;
      }
    }
  }, [activeTab, apiRange, customEndDate, customStartDate, selectedOutletKeys]);

  useEffect(() => {
    void loadOutlets();
    return () => requestControllerRef.current?.abort();
  }, [loadOutlets, retryKey]);

  const current = summary?.current;
  const previous = summary?.previous;
  const yearAgo = summary?.yearAgo;
  const outlets = useMemo(() => summary?.outlets ?? [], [summary?.outlets]);
  const filterOutletOptions = summary?.filterOptions?.shops;
  const outletOptions = useMemo<Array<Pick<SalesChannel, "groupKey" | "name" | "platform">>>(
    () => filterOutletOptions?.length
      ? filterOutletOptions.map((item) => ({ groupKey: item.key, name: item.name, platform: item.platform }))
      : outlets.map((item) => ({ groupKey: item.groupKey, name: item.name, platform: item.platform })),
    [filterOutletOptions, outlets],
  );
  const platforms = useMemo(() => summary?.platforms ?? [], [summary?.platforms]);
  const platformOptions = useMemo(
    () => [...new Set(outlets.map((item) => item.platform).filter((item) => item && item !== "未分类"))].sort((left, right) => left.localeCompare(right, "zh-CN")),
    [outlets],
  );
  const displayedRows = useMemo(() => {
    const source = activeTab === "outlets" ? outlets : platforms;
    return source
      .filter((item) => activeTab !== "outlets" || platformFilters.length === 0 || platformFilters.includes(item.platform))
      .sort((left, right) => right.netSalesCents - left.netSalesCents);
  }, [activeTab, outlets, platformFilters, platforms]);
  const hasData = Boolean(current && (current.lineCount > 0 || current.orderCount > 0 || current.netSalesCents !== 0));
  const rowLabel = activeTab === "outlets" ? "网店" : "平台";
  const dimensionPagination = activeTab === "outlets"
    ? summary?.groupPagination?.outlets
    : summary?.groupPagination?.platforms;
  const rangeNote = summary ? `${summary.startDate} 至 ${summary.endDate}` : range;

  const subnav = <div className="subnav outlet-subnav" role="tablist" aria-label="网店分析子版块"><button type="button" role="tab" aria-selected={activeTab === "analysis"} className={activeTab === "analysis" ? "active" : ""} onClick={() => onModuleViewChange("analysis")}>店铺分析</button><button type="button" role="tab" aria-selected={activeTab === "outlets"} className={activeTab === "outlets" ? "active" : ""} onClick={() => onModuleViewChange("outlets")}>网店总览</button><button type="button" role="tab" aria-selected={activeTab === "platforms"} className={activeTab === "platforms" ? "active" : ""} onClick={() => onModuleViewChange("platforms")}>平台对比</button><button type="button" role="tab" aria-selected={activeTab === "products"} className={activeTab === "products" ? "active" : ""} onClick={() => onModuleViewChange("products")}>商品数据</button><button type="button" role="tab" aria-selected={activeTab === "promotion"} className={activeTab === "promotion" ? "active" : ""} onClick={() => onModuleViewChange("promotion")}>推广分析</button><button type="button" disabled title="待接入企业购明细">企业购分析</button><button type="button" disabled title="待接入客服报表">客服分析</button></div>;

  if (activeTab === "products") return <>{subnav}<ShopProductDataView range={range} customStartDate={customStartDate} customEndDate={customEndDate} onOpenCatalogImport={() => onNavigate("import", "tmall_product_master")} onOpenCatalogAssetImport={() => onNavigate("import", "tmall_product_assets")} onOpenImport={(dimension) => onNavigate("import", dimension === "sku" ? "jd_sku_daily" : "tmall_product_daily")} /></>;
  if (activeTab === "promotion") return <>{subnav}<ShopPromotionView range={range} customStartDate={customStartDate} customEndDate={customEndDate} onOpenTmallImport={() => onNavigate("import", "tmall_promotion")} /></>;

  if (loading && !summary) return <>{subnav}<section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在同步网店经营数据</strong><p>正在汇总已导入销售明细中的网店、平台、毛利与退货信息…</p></section></>;
  if (!summary || !analysisSummary) return <>{subnav}<section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>网店数据加载失败</strong><p>{error || "暂时无法读取网店数据"}</p><button className="secondary-button" onClick={() => setRetryKey((value) => value + 1)}>重新加载</button></section></>;
  if (!current || (!hasData && !(activeTab === "analysis" && selectedOutletKeys.length > 0))) return <>{subnav}<section className="panel data-state"><span className="state-symbol">店</span><strong>{range}暂无网店销售数据</strong><p>请先在“数据导入”同步销售单明细账；系统会优先按店铺名称汇总，缺失时回退为渠道或平台。</p></section></>;

  if (activeTab === "analysis") return <>{subnav}<StoreAnalysisView summary={analysisSummary} outlets={outletOptions} selectedOutletKeys={selectedOutletKeys} onSelectOutlets={(keys) => { const unchanged = keys.length === selectedOutletKeys.length && keys.every((key, index) => key === selectedOutletKeys[index]); setSelectedOutletKeys(keys); if (unchanged) setRetryKey((value) => value + 1); }} loading={loading} /></>;

  return <>
    {subnav}
    <section className="outlet-sync-bar"><span><Dot tone="green" />已同步销售明细</span><strong>{rangeNote}</strong>{summary.latestBatch?.fileName && <small>最近批次：{summary.latestBatch.fileName}</small>}<button type="button" className="row-action" onClick={() => void loadOutlets()} disabled={loading}>{loading ? "同步中…" : "↻ 同步数据"}</button></section>
    {error && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>数据刷新失败</strong><p>{error}</p></div><button className="row-action" onClick={() => setRetryKey((value) => value + 1)}>重试</button></section>}
    <section className="metrics-grid outlet-metrics-grid data-refresh-region" aria-busy={loading}>
      <MetricCard label="活跃网店" value={`${formatCount(outlets.length)} 个`} change={formatChange(outlets.length, 0)} hint={`覆盖 ${formatCount(platformOptions.length)} 个平台`} tone="blue" />
      <MetricCard label="网店销售净额" value={formatCurrencyFromCents(current.netSalesCents)} change={formatChange(current.netSalesCents, previous?.netSalesCents)} hint={comparisonHint(current.netSalesCents, previous?.netSalesCents, yearAgo?.netSalesCents)} tone="purple" />
      <MetricCard label="订单毛利" value={formatCurrencyFromCents(current.grossProfitCents)} change={formatChange(current.grossProfitCents, previous?.grossProfitCents)} hint={`综合大毛利率 ${formatRate(current.grossMarginRate)}`} tone="green" />
      <MetricCard label="退货率" value={formatRate(current.refundRate)} change={formatChange(rateAsPercent(current.refundRate), rateAsPercent(previous?.refundRate))} hint={comparisonHint(rateAsPercent(current.refundRate), rateAsPercent(previous?.refundRate), rateAsPercent(yearAgo?.refundRate))} tone="orange" />
    </section>
    <section className="panel table-panel outlet-table-panel data-refresh-region" aria-busy={loading}>
      <div className="table-toolbar"><div><h2>{rowLabel}经营明细</h2><p>销售额、同比、毛利率和退货率随顶部统计周期及最新导入批次自动更新。</p></div>{activeTab === "outlets" && <SearchableMultiSelect className="filter-select outlet-platform-filter" values={platformFilters} onChange={setPlatformFilters} ariaLabel="平台筛选" allLabel="全部平台" searchPlaceholder="搜索平台" options={platformOptions.map((value) => ({ value, label: value }))} />}</div>
      <div className="data-table-wrap"><table className="data-table outlet-data-table" data-column-filter-scope={dimensionPagination?.truncated === false ? "full" : "none"}><thead><tr><th>排名</th><th>{rowLabel}</th>{activeTab === "outlets" && <th>所属平台</th>}<th>销售净额</th><th>净额占比</th><th>净销售同比</th><th>订单量</th><th>大毛利率</th><th>退货率</th><th>经营状态</th></tr></thead><tbody>{displayedRows.map((item, index) => { const needsAttention = item.grossMarginRate < current.grossMarginRate - .05 || item.refundRate > current.refundRate + .03; const statusText = needsAttention ? "需要关注" : index < 3 && item.shareRate >= .1 ? "核心网店" : "经营稳健"; return <tr key={`${activeTab}-${item.platform}-${item.name}`}><td><span className={`table-rank ${index < 3 ? `top-${index + 1}` : ""}`}>{index + 1}</span></td><td><div className="channel-name-cell"><span>{(item.name || "未").slice(0, 1)}</span><strong title={item.name}>{item.name || "未分类"}</strong></div></td>{activeTab === "outlets" && <td><span className="soft-tag">{item.platform || "未分类"}</span></td>}<td><strong>{formatCurrencyFromCents(item.netSalesCents)}</strong></td><td>{formatRate(item.shareRate)}</td><td className={netSalesYearOverYearTone(item.salesYearOverYearRate)}>{formatNetSalesYearOverYear(item.salesYearOverYearRate)}</td><td>{formatCount(item.orderCount)}</td><td className={item.grossMarginRate < current.grossMarginRate ? "orange-text" : "green-text"}>{formatRate(item.grossMarginRate)}</td><td className={item.refundRate > current.refundRate ? "orange-text" : ""}>{formatRate(item.refundRate)}</td><td><span className={`status ${needsAttention ? "status-warning" : "status-success"}`}><Dot tone={needsAttention ? "orange" : "green"} />{statusText}</span></td></tr>; })}{displayedRows.length === 0 && <tr><td colSpan={activeTab === "outlets" ? 10 : 9}><div className="table-state">当前筛选条件下没有可展示的{rowLabel}数据。</div></td></tr>}</tbody></table></div>
    </section>
  </>;
}
