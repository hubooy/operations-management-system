"use client";

import FinanceAnnualProgressView from "./finance-annual-progress-view";
import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchWithTransientRetry } from "@/lib/http/transient-retry";
import { parseProductQueries, parseProductQueriesStrict } from "@/lib/sales/read-contract";
import type { ModuleViewKey } from "./shell/navigation-catalog";
import { SearchableSelect } from "./ui/searchable-select";
import { ProductSalesTrend, ShopSalesDistribution } from "./module-view-business-ui";
import { createReloadableLazy } from "./shell/reloadable-lazy";
import SalesFilterBar, {
  decideFinanceDimensionReconciliation,
  financeAnalysisPayloadForRequest,
  financeDimensionOptionsToSalesOptions,
  parseFinanceDimensionFilterIssues,
  readSalesSharedFilters,
  reconcileFinanceDimensionFilters,
  salesOutletKeyToFinanceKey,
  writeSalesSharedFilters,
  type SalesSharedFilterOptions,
  type SalesSharedFilters,
} from "./sales-filter-bar";
import {
  type CurrentUser,
  canManageFinanceTargets,
  validateFinanceTargetDeletionReason,
  type SalesRangeLabel,
  type SalesStats,
  type SalesChannel,
  type SalesSummaryResponse,
  type FinanceActualMetrics,
  type FinanceTarget,
  type FinanceAnalysisResponse,
  type FinanceTargetOptions,
  salesRangeMap,
  channelTones,
  channelColors,
  formatCurrencyFromCents,
  formatCount,
  rateAsPercent,
  formatRate,
  formatNetSalesYearOverYear,
  netSalesYearOverYearTone,
  useDebouncedValue,
  formatChange,
  comparisonHint,
  Dot,
  MetricCard,
  SectionHeader,
} from "./module-view-shared";

const { Component: SalesCategoryView } = createReloadableLazy("sales", () => import("./sales-category-view"));

type SalesTab = ModuleViewKey<"sales">;
type ChannelDimension = "channel" | "platform";

function SalesSubnav({ active, onChange }: { active: SalesTab; onChange: (tab: SalesTab) => void }) {
  return (
    <div className="subnav inventory-subnav sales-subnav" role="tablist" aria-label="销售分析子版块">
      <button type="button" role="tab" aria-selected={active === "overview"} className={active === "overview" ? "active" : ""} onClick={() => onChange("overview")}>销售总览</button>
      <button type="button" role="tab" aria-selected={active === "channel"} className={active === "channel" ? "active" : ""} onClick={() => onChange("channel")}>渠道分析</button>
      <button type="button" role="tab" aria-selected={active === "category"} className={active === "category" ? "active" : ""} onClick={() => onChange("category")}>品类分析</button>
      <button type="button" role="tab" aria-selected={active === "finance"} className={active === "finance" ? "active" : ""} onClick={() => onChange("finance")}>财报分析</button>
      <button type="button" role="tab" aria-selected={active === "targets"} className={active === "targets" ? "active" : ""} onClick={() => onChange("targets")}>目标进度情况</button>
    </div>
  );
}

function ChannelAnalysisView({
  channels,
  platforms,
  current,
  pagination,
}: {
  channels: SalesChannel[];
  platforms: SalesChannel[];
  current: SalesStats;
  pagination?: {
    channel?: { total: number; returned: number; truncated: boolean };
    platform?: { total: number; returned: number; truncated: boolean };
  };
}) {
  const [dimension, setDimension] = useState<ChannelDimension>("channel");
  const rows = useMemo(
    () => [...(dimension === "channel" ? channels : platforms)].sort((a, b) => b.netSalesCents - a.netSalesCents),
    [channels, dimension, platforms],
  );

  if (!rows.length) {
    return (
      <section className="panel data-state channel-empty-state">
        <span className="state-symbol" aria-hidden="true">渠</span>
        <strong>暂未识别到渠道数据</strong>
        <p>当前销售明细已有成交记录，但渠道字段为空。请检查导入文件中的渠道或平台映射。</p>
      </section>
    );
  }

  const topChannel = rows[0];
  const marginLeader = rows.reduce((best, item) => item.grossMarginRate > best.grossMarginRate ? item : best, rows[0]);
  const refundLeader = rows.reduce((highest, item) => {
    const itemRate = item.grossSalesCents === 0 ? 0 : item.refundAmountCents / item.grossSalesCents;
    const highestRate = highest.grossSalesCents === 0 ? 0 : highest.refundAmountCents / highest.grossSalesCents;
    return itemRate > highestRate ? item : highest;
  }, rows[0]);
  const topThreeShare = Math.min(1, Math.max(0, rows.slice(0, 3).reduce((sum, item) => sum + item.shareRate, 0)));
  const concentrationLabel = topThreeShare >= .75 ? "集中度较高" : topThreeShare >= .5 ? "集中度适中" : "渠道较均衡";
  const maxSales = Math.max(1, ...rows.map((item) => Math.max(0, item.netSalesCents)));
  const dimensionLabel = dimension === "channel" ? "销售渠道" : "平台";
  const dimensionPagination = dimension === "channel" ? pagination?.channel : pagination?.platform;
  const refundLeaderRate = refundLeader.grossSalesCents === 0 ? 0 : refundLeader.refundAmountCents / refundLeader.grossSalesCents;

  return (
    <>
      <section className="channel-analysis-toolbar" aria-label="渠道分析维度">
        <div>
          <span className="eyebrow">渠道经营诊断</span>
          <h2>看清渠道贡献与经营质量</h2>
          <p>从销售规模、利润质量和退货风险三个角度，识别核心渠道与改善机会。</p>
        </div>
        <div className="segmented" role="group" aria-label="渠道分析口径">
          <button type="button" className={dimension === "channel" ? "active" : ""} aria-pressed={dimension === "channel"} onClick={() => setDimension("channel")}>销售渠道</button>
          <button type="button" className={dimension === "platform" ? "active" : ""} aria-pressed={dimension === "platform"} onClick={() => setDimension("platform")}>平台汇总</button>
        </div>
      </section>

      <section className="channel-kpi-grid">
        <article className="channel-kpi-card">
          <div><span>有效{dimensionLabel}</span><i className="channel-kpi-icon blue">渠</i></div>
          <strong>{formatCount(rows.length)}<small> 个</small></strong>
          <p>本周期产生销售净额的{dimensionLabel}</p>
        </article>
        <article className="channel-kpi-card">
          <div><span>头部{dimensionLabel}</span><i className="channel-kpi-icon purple">冠</i></div>
          <strong>{formatCurrencyFromCents(topChannel.netSalesCents)}</strong>
          <p title={topChannel.name}>{topChannel.name || "未分类"} · 占比 {formatRate(topChannel.shareRate)}</p>
        </article>
        <article className="channel-kpi-card">
          <div><span>Top 3 集中度</span><i className="channel-kpi-icon orange">集</i></div>
          <strong>{formatRate(topThreeShare)}</strong>
          <p>{concentrationLabel} · 按销售净额计算</p>
        </article>
        <article className="channel-kpi-card">
          <div><span>毛利表现最佳</span><i className="channel-kpi-icon green">利</i></div>
          <strong>{formatRate(marginLeader.grossMarginRate)}</strong>
          <p title={marginLeader.name}>{marginLeader.name || "未分类"} · 综合 {formatRate(current.grossMarginRate)}</p>
        </article>
      </section>

      <section className="channel-analysis-grid">
        <article className="panel channel-ranking-panel">
          <SectionHeader title={`${dimensionLabel}贡献排行`} note="销售净额、占比与毛利率综合查看" />
          <div className="channel-ranking-list">
            {rows.slice(0, 8).map((item, index) => (
              <div className="channel-ranking-row" key={item.name}>
                <span className={`channel-rank-number ${index < 3 ? `top-${index + 1}` : ""}`}>{index + 1}</span>
                <div className="channel-ranking-main">
                  <div><strong title={item.name}>{item.name || "未分类"}</strong><small>{formatCurrencyFromCents(item.netSalesCents)} · 占比 {formatRate(item.shareRate)} · 净销售同比 {formatNetSalesYearOverYear(item.salesYearOverYearRate)}</small></div>
                  <span><i style={{ width: `${Math.max(2, Math.max(0, item.netSalesCents) / maxSales * 100)}%` }} /></span>
                </div>
                <div className="channel-ranking-margin"><small>毛利率</small><strong className={item.grossMarginRate < current.grossMarginRate ? "orange-text" : "green-text"}>{formatRate(item.grossMarginRate)}</strong></div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel channel-insight-panel">
          <SectionHeader title="经营洞察" note="基于当前周期自动识别" />
          <div className="channel-insight-list">
            <article>
              <span className="insight-badge blue">集中度</span>
              <div><strong>{concentrationLabel}</strong><p>Top 3 {dimensionLabel}贡献 {formatRate(topThreeShare)} 的销售净额。</p></div>
            </article>
            <article>
              <span className="insight-badge green">利润</span>
              <div><strong title={marginLeader.name}>{marginLeader.name || "未分类"}</strong><p>毛利率 {formatRate(marginLeader.grossMarginRate)}，高于综合水平 {formatRate(marginLeader.grossMarginRate - current.grossMarginRate)}。</p></div>
            </article>
            <article>
              <span className="insight-badge orange">退货</span>
              <div><strong title={refundLeader.name}>{refundLeader.name || "未分类"}</strong><p>退货率 {formatRate(refundLeaderRate)}，为当前{dimensionLabel}中的最高值。</p></div>
            </article>
          </div>
          <div className="channel-benchmark">
            <div><span>综合毛利率</span><strong>{formatRate(current.grossMarginRate)}</strong></div>
            <div><span>综合退货率</span><strong>{formatRate(current.refundRate)}</strong></div>
            <div><span>订单总量</span><strong>{formatCount(current.orderCount)}</strong></div>
          </div>
        </article>
      </section>

      <section className="panel table-panel channel-detail-panel">
        <div className="table-toolbar">
          <div><h2>{dimensionLabel}经营明细</h2><p>按销售净额从高到低排列，数据随顶部统计周期同步更新</p></div>
          <span className="soft-tag">共 {formatCount(rows.length)} 个{dimensionLabel}</span>
        </div>
        <div className="data-table-wrap">
          <table className="data-table channel-data-table" data-column-filter-scope={dimensionPagination?.truncated === false ? "full" : "none"}>
            <thead><tr><th>排名</th><th>{dimensionLabel}</th><th>销售额（GMV）</th><th>销售净额</th><th>净额占比</th><th>净销售同比</th><th>订单毛利</th><th>毛利率</th><th>订单量</th><th>退货率</th><th>经营状态</th></tr></thead>
            <tbody>{rows.map((item, index) => {
              const refundRate = item.grossSalesCents === 0 ? 0 : item.refundAmountCents / item.grossSalesCents;
              const needsAttention = item.grossMarginRate < current.grossMarginRate - .05 || refundRate > current.refundRate + .03;
              const isCore = index < 3 && item.shareRate >= .1;
              const statusText = needsAttention ? "需要关注" : isCore ? "核心渠道" : "经营稳健";
              const statusTone = needsAttention ? "warning" : "success";
              return <tr key={item.name}>
                <td><span className={`table-rank ${index < 3 ? `top-${index + 1}` : ""}`}>{index + 1}</span></td>
                <td><div className="channel-name-cell"><span>{(item.name || "未").slice(0, 1)}</span><strong title={item.name}>{item.name || "未分类"}</strong></div></td>
                <td>{formatCurrencyFromCents(item.grossSalesCents)}</td>
                 <td><strong>{formatCurrencyFromCents(item.netSalesCents)}</strong></td>
                 <td><div className="share-cell"><strong>{formatRate(item.shareRate)}</strong><span><i style={{ width: `${Math.max(0, Math.min(100, rateAsPercent(item.shareRate)))}%` }} /></span></div></td>
                 <td className={netSalesYearOverYearTone(item.salesYearOverYearRate)}>{formatNetSalesYearOverYear(item.salesYearOverYearRate)}</td>
                 <td>{formatCurrencyFromCents(item.grossProfitCents)}</td>
                <td className={item.grossMarginRate < current.grossMarginRate ? "orange-text" : "green-text"}><strong>{formatRate(item.grossMarginRate)}</strong></td>
                <td>{formatCount(item.orderCount)}</td>
                <td className={refundRate > current.refundRate ? "orange-text" : ""}>{formatRate(refundRate)}</td>
                <td><span className={`status status-${statusTone}`}><Dot tone={statusTone === "warning" ? "orange" : "green"} />{statusText}</span></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      </section>
    </>
  );
}

const formatFinanceBps = (value: number | null | undefined) => value === null || value === undefined ? "—" : `${(value / 100).toFixed(1)}%`;
const formatFinanceChange = (current: number, comparison: number | null | undefined, points = false) => {
  if (comparison === null || comparison === undefined) return "暂无可比数据";
  if (points) {
    const difference = (current - comparison) / 100;
    return `${difference >= 0 ? "+" : ""}${difference.toFixed(1)} 个百分点`;
  }
  if (comparison === 0) return "基期为 0";
  const rate = (current - comparison) / Math.abs(comparison);
  return `${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(1)}%`;
};
const financeChangeTone = (current: number, comparison: number | null | undefined, inverse = false) => {
  if (comparison === null || comparison === undefined || current === comparison) return "muted-text";
  const positive = current > comparison;
  return positive !== inverse ? "green-text" : "red-text";
};
const financeMonthLabel = (month: string) => `${month.slice(0, 4)}年${Number(month.slice(5))}月`;
const formatFinanceWan = (value: number) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value / 1_000_000);
const financeProgressWidth = (value: number | null) => `${Math.max(0, Math.min(100, (value ?? 0) * 100))}%`;

function FinanceTrendChart({ rows }: { rows: Array<{ month: string } & FinanceActualMetrics> }) {
  const width = Math.max(760, rows.length * 92);
  const height = 270;
  const plot = { left: 42, right: width - 24, top: 38, bottom: 216 };
  const valuesFor = (key: "netSalesCents" | "profitCents") => rows.map((row) => row[key]);
  const coordinateSeries = (key: "netSalesCents" | "profitCents") => {
    const values = valuesFor(key);
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    return values.map((value, index) => ({
      x: rows.length <= 1 ? (plot.left + plot.right) / 2 : plot.left + (plot.right - plot.left) * index / (rows.length - 1),
      y: plot.bottom - (value - minimum) / span * (plot.bottom - plot.top),
      value,
    }));
  };
  const salesPoints = coordinateSeries("netSalesCents");
  const profitPoints = coordinateSeries("profitCents");
  const pointsText = (points: Array<{ x: number; y: number }>) => points.map((point) => `${point.x},${point.y}`).join(" ");
  return <div className="finance-trend-chart">
    <div className="finance-chart-legend"><span><i className="blue" />净销售额（万）</span><span><i className="green" />利润（万）</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} style={{ minWidth: `${width}px` }} role="img" aria-label="月度净销售额与利润趋势，节点单位万元">
      {[0, 1, 2, 3, 4].map((index) => { const y = plot.top + (plot.bottom - plot.top) * index / 4; return <line key={index} x1={plot.left} x2={plot.right} y1={y} y2={y} className="finance-grid-line" />; })}
      {salesPoints.length > 0 && <polyline points={pointsText(salesPoints)} className="finance-line sales" />}
      {profitPoints.length > 0 && <polyline points={pointsText(profitPoints)} className="finance-line profit" />}
      {salesPoints.map((point, index) => <g key={`sales-${rows[index].month}`}><circle cx={point.x} cy={point.y} r="4" className="finance-point sales"><title>{`${financeMonthLabel(rows[index].month)} 净销售额 ${formatCurrencyFromCents(point.value)}`}</title></circle><text x={point.x} y={Math.max(16, point.y - 10)} textAnchor="middle" className="finance-node-label sales">{formatFinanceWan(point.value)}</text></g>)}
      {profitPoints.map((point, index) => <g key={`profit-${rows[index].month}`}><circle cx={point.x} cy={point.y} r="4" className="finance-point profit"><title>{`${financeMonthLabel(rows[index].month)} 利润 ${formatCurrencyFromCents(point.value)}`}</title></circle><text x={point.x} y={Math.min(234, point.y + 17)} textAnchor="middle" className="finance-node-label profit">{formatFinanceWan(point.value)}</text></g>)}
      {rows.map((row, index) => { const x = rows.length <= 1 ? (plot.left + plot.right) / 2 : plot.left + (plot.right - plot.left) * index / (rows.length - 1); return <text key={row.month} x={x} y="258" textAnchor="middle" className="finance-axis-label">{row.month.slice(2)}</text>; })}
    </svg>
  </div>;
}

function FinanceKpiCard({ label, value, targetLabel, progress, mom, yoy, tone, icon }: {
  label: string;
  value: string;
  targetLabel: string;
  progress: number | null;
  mom: { text: string; tone: string };
  yoy: { text: string; tone: string };
  tone: "blue" | "green" | "purple" | "orange" | "cyan" | "red";
  icon?: string;
}) {
  return <article className={`panel finance-kpi-card finance-kpi-${tone}`}>
    <div><span>{label}</span><i>{icon ?? (tone === "blue" ? "销" : tone === "green" ? "利" : tone === "purple" ? "毛" : "费")}</i></div>
    <strong>{value}</strong>
    <div className="finance-kpi-comparison"><small className={mom.tone}>环比 {mom.text}</small><small className={yoy.tone}>同比 {yoy.text}</small></div>
    <footer><span>{targetLabel}</span>{progress !== null && <b>{(progress * 100).toFixed(1)}%</b>}</footer>
    {progress !== null && <div className="finance-progress-track"><span style={{ width: financeProgressWidth(progress) }} /></div>}
  </article>;
}

type FinanceFilterOption = string | { value: string; label: string };

function FinanceMultiFilterSelect({ label, allLabel, options, selected, onChange }: {
  label: string;
  allLabel: string;
  options: FinanceFilterOption[];
  selected: string[] | null;
  onChange: (next: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeWhenOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeWhenOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeWhenOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
  const normalizedOptions = options.map((option) => typeof option === "string" ? { value: option, label: option } : option);
  const visibleOptions = normalizedOptions.filter((option) => option.label.toLowerCase().includes(search.trim().toLowerCase()));
  const isAll = selected === null;
  const selectedValues = selected ?? [];
  const summary = isAll ? allLabel : `已选 ${formatCount(selectedValues.length)} 个${label}`;
  const toggleOption = (value: string) => {
    if (isAll) {
      onChange([value]);
      return;
    }
    const next = selectedValues.includes(value) ? selectedValues.filter((item) => item !== value) : [...selectedValues, value];
    if (next.length === 0 || next.length === normalizedOptions.length) onChange(null);
    else onChange(next);
  };
  return <div ref={rootRef} className={`multi-filter-select finance-multi-filter ${open ? "open" : ""}`}>
    <button type="button" className="multi-filter-trigger" aria-label={`${label}多选`} aria-haspopup="listbox" aria-expanded={open} onClick={() => { setOpen((value) => !value); setSearch(""); }}><span title={summary}>{summary}</span><i>⌄</i></button>
    {open && <div className="multi-filter-menu" role="listbox" aria-label={`${label}多选`} aria-multiselectable="true">
      <div className="multi-filter-menu-head"><strong>{label}筛选</strong><button type="button" onClick={() => onChange(null)} disabled={isAll}>全选</button></div>
      <label className="multi-filter-search">⌕<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`搜索${label}`} aria-label={`搜索${label}`} /></label>
      <button type="button" className={isAll ? "selected" : ""} role="option" aria-selected={isAll} onClick={() => onChange(null)}><i>{isAll ? "✓" : ""}</i><span>{allLabel}</span></button>
      {visibleOptions.map((option) => { const checked = isAll || selectedValues.includes(option.value); return <button type="button" key={option.value} className={checked ? "selected" : ""} role="option" aria-selected={checked} onClick={() => toggleOption(option.value)}><i>{checked ? "✓" : ""}</i><span title={option.label}>{option.label}</span></button>; })}
      {visibleOptions.length === 0 && <p className="multi-filter-menu-empty">没有匹配项</p>}
    </div>}
  </div>;
}

type FinanceExpenseSortKey = "name" | "current" | "feeRateBps" | "previous" | "momRate" | "yearAgo" | "yearAgoFeeRateBps" | "yoyRate" | "abnormal";

function FinanceSortButton({ label, column, activeColumn, direction, onSort }: {
  label: string;
  column: FinanceExpenseSortKey;
  activeColumn: FinanceExpenseSortKey;
  direction: "asc" | "desc";
  onSort: (column: FinanceExpenseSortKey) => void;
}) {
  const active = column === activeColumn;
  return <button type="button" className={`finance-sort-button ${active ? "active" : ""}`} onClick={() => onSort(column)} title={active ? (direction === "desc" ? "当前从高到低，点击切换为从低到高" : "当前从低到高，点击切换为从高到低") : `按${label}排序`}><span>{label}</span><i aria-hidden="true">{active ? (direction === "desc" ? "↓" : "↑") : "⇅"}</i></button>;
}

function isoMonthsBetween(startDate: string, endDate: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(startDate) || !/^\d{4}-\d{2}-\d{2}$/.test(endDate) || startDate > endDate) return [];
  const [startYear, startMonth] = startDate.split("-").map(Number);
  const [endYear, endMonth] = endDate.split("-").map(Number);
  const values: string[] = [];
  for (let year = startYear, month = startMonth; year < endYear || (year === endYear && month <= endMonth);) {
    values.push(`${year}-${String(month).padStart(2, "0")}`);
    month += 1;
    if (month > 12) { year += 1; month = 1; }
  }
  return values.slice(0, 24);
}

function FinanceAnalysisView({
  customStartDate,
  customEndDate,
  selectedPlatforms,
  selectedShopKeys,
  onDimensionFiltersChange,
  onFilterOptionsChange,
}: {
  customStartDate: string;
  customEndDate: string;
  selectedPlatforms: string[];
  selectedShopKeys: string[];
  onDimensionFiltersChange: (platforms: string[], outletKeys: string[]) => void;
  onFilterOptionsChange: (options: SalesSharedFilterOptions) => void;
}) {
  const globalMonths = useMemo(() => isoMonthsBetween(customStartDate, customEndDate), [customEndDate, customStartDate]);
  const [selectedMonths, setSelectedMonths] = useState<string[] | null>(globalMonths);
  const [allowInitialMonthFallback, setAllowInitialMonthFallback] = useState(true);
  const [expenseSearch, setExpenseSearch] = useState("");
  const [expenseSort, setExpenseSort] = useState<{ column: FinanceExpenseSortKey; direction: "asc" | "desc" }>({ column: "current", direction: "desc" });
  const [dataResult, setDataResult] = useState<{ requestSignature: string; payload: FinanceAnalysisResponse } | null>(null);
  // Filter options survive a refresh; financial results still require an exact request signature.
  const [monthOptions, setMonthOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filterReconciliationNotice, setFilterReconciliationNotice] = useState("");
  const [pendingDimensionChange, setPendingDimensionChange] = useState<{
    platforms: string[];
    outletKeys: string[];
    notice: string;
  } | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const analysisRequestGenerationRef = useRef(0);
  const reconciledDimensionSignatureRef = useRef<string | null>(null);
  const dimensionSignature = JSON.stringify([selectedPlatforms, selectedShopKeys]);
  const requestSignature = JSON.stringify([
    globalMonths,
    selectedMonths,
    allowInitialMonthFallback,
    selectedPlatforms,
    selectedShopKeys,
  ]);
  const data = financeAnalysisPayloadForRequest(dataResult, requestSignature);
  useAiPageDetails("sales", {
    period: null,
    filters: { dataset: "finance_analysis", months: data?.selection?.months ?? data?.selectedMonths ?? (selectedMonths === null ? ["*"] : selectedMonths), platforms: selectedPlatforms, outletKeys: selectedShopKeys },
  });


  useEffect(() => {
    setSelectedMonths(globalMonths);
    setAllowInitialMonthFallback(true);
  }, [globalMonths]);

  useEffect(() => {
    if (filterReconciliationNotice && reconciledDimensionSignatureRef.current !== dimensionSignature) {
      reconciledDimensionSignatureRef.current = null;
      setFilterReconciliationNotice("");
    }
  }, [dimensionSignature, filterReconciliationNotice]);

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++analysisRequestGenerationRef.current;
    void (async () => {
      setLoading(true);
      setError("");
      setDataResult(null);
      setPendingDimensionChange(null);
      try {
        const query = new URLSearchParams();
        if (selectedMonths === null) query.append("month", "*");
        else selectedMonths.forEach((month) => query.append("month", month));
        if (allowInitialMonthFallback && selectedMonths !== null && selectedMonths.length > 0) {
          query.set("initialMonthFallback", "latest_completed");
        }
        const validSelectedShopKeys = selectedShopKeys.filter((shopKey) => salesOutletKeyToFinanceKey(shopKey) !== null);
        if (validSelectedShopKeys.length !== selectedShopKeys.length) {
          const removedCount = selectedShopKeys.length - validSelectedShopKeys.length;
          setPendingDimensionChange({
            platforms: selectedPlatforms,
            outletKeys: validSelectedShopKeys,
            notice: `已按你的确认从当前链接移除 ${removedCount} 个无法识别的店铺筛选；财报仍按平台与店铺复合身份严格校验。`,
          });
          setError(`当前链接包含 ${removedCount} 个无法识别的店铺筛选。为避免未经服务端确认扩大查询范围，系统没有自动清除。`);
          return;
        }
        selectedPlatforms.forEach((platform) => query.append("platform", platform));
        validSelectedShopKeys.map(salesOutletKeyToFinanceKey).filter((value): value is string => value !== null).forEach((shop) => query.append("shop", shop));
        const queryText = query.toString();
        const response = await fetch(`/api/finance/analysis${queryText ? `?${queryText}` : ""}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (FinanceAnalysisResponse & { code?: string }) | null;
        const isDimensionFailure = response.status === 400 && payload?.code === "finance_dimension_filter_out_of_scope";
        if (isDimensionFailure && (selectedPlatforms.length > 0 || validSelectedShopKeys.length > 0)) {
          const issues = parseFinanceDimensionFilterIssues(payload);
          if (!issues) throw new Error("财报筛选校验响应格式不完整，请重试。");
          const reconciliation = reconcileFinanceDimensionFilters(
            selectedPlatforms,
            validSelectedShopKeys,
            issues,
          );
          const decision = decideFinanceDimensionReconciliation(reconciliation);
          if (decision === "reject") {
            throw new Error(payload.error || "财报筛选与当前财务期间不一致，请清空筛选后重试。");
          }
          if (controller.signal.aborted || generation !== analysisRequestGenerationRef.current) return;
          const removedParts = [
            reconciliation.removedPlatforms.length ? `平台“${reconciliation.removedPlatforms.join("、")}”` : "",
            reconciliation.removedShops.length ? `店铺“${reconciliation.removedShops.join("、")}”` : "",
          ].filter(Boolean);
          const reconciliationNotice = `${removedParts.join("及")}不在当前财报范围，已同步从公共筛选和当前链接中移除。`;
          if (decision === "require_confirmation") {
            setPendingDimensionChange({
              platforms: reconciliation.platforms,
              outletKeys: reconciliation.outletKeys,
              notice: reconciliationNotice,
            });
            setError(`${payload.error || "当前筛选不在财报范围。"} 为避免自动扩大到全部财报，系统没有自动清除；请确认后再继续。`);
            return;
          }
          reconciledDimensionSignatureRef.current = JSON.stringify([reconciliation.platforms, reconciliation.outletKeys]);
          setFilterReconciliationNotice(reconciliationNotice);
          onDimensionFiltersChange(reconciliation.platforms, reconciliation.outletKeys);
          return;
        }
        if (!response.ok || !payload) throw new Error(payload?.error || `财报分析读取失败（${response.status}）`);
        if (controller.signal.aborted || generation !== analysisRequestGenerationRef.current) return;
        if (payload.filters) onFilterOptionsChange(financeDimensionOptionsToSalesOptions(payload.filters));
        setMonthOptions(payload.months.map((item) => ({ value: item.month, label: financeMonthLabel(item.month) })));
        setDataResult({ requestSignature, payload });
      } catch (requestError) {
        if (controller.signal.aborted || generation !== analysisRequestGenerationRef.current
          || (requestError instanceof DOMException && requestError.name === "AbortError")) return;
        setError(requestError instanceof Error ? requestError.message : "暂时无法读取财报分析");
      } finally {
        if (!controller.signal.aborted && generation === analysisRequestGenerationRef.current) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [allowInitialMonthFallback, onDimensionFiltersChange, onFilterOptionsChange, requestSignature, retryKey, selectedMonths, selectedPlatforms, selectedShopKeys]);

  const confirmPendingDimensionChange = () => {
    if (!pendingDimensionChange) return;
    reconciledDimensionSignatureRef.current = JSON.stringify([
      pendingDimensionChange.platforms,
      pendingDimensionChange.outletKeys,
    ]);
    setFilterReconciliationNotice(pendingDimensionChange.notice);
    setPendingDimensionChange(null);
    setError("");
    setDataResult(null);
    onDimensionFiltersChange(pendingDimensionChange.platforms, pendingDimensionChange.outletKeys);
  };

  const resultState = !data && (loading || !error) ? <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在生成财报分析</strong><p>可继续搜索并多选月份、店铺，结果按最新选择更新。</p></section>
    : error && !data ? <section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>财报分析加载失败</strong><p>{error}</p>{pendingDimensionChange
    ? <button className="secondary-button" onClick={confirmPendingDimensionChange}>{pendingDimensionChange.platforms.length || pendingDimensionChange.outletKeys.length ? "移除异常筛选并继续" : "清除筛选并查看全部财报"}</button>
    : <button className="secondary-button" onClick={() => setRetryKey((key) => key + 1)}>重新加载</button>}</section>
    : !data?.hasData || !data.current || !data.targets || !data.progress || !data.yearToDate ? <section className="panel data-state finance-empty-state"><span className="state-symbol">财</span><strong>当前筛选没有月度财报数据</strong><p>可调整月份和店铺筛选，或到“数据导入”上传月度财报。</p></section> : null;

  const current = data?.current;
  const previous = data?.previous;
  const yearAgo = data?.yearAgo;
  const targets = data?.targets?.month;
  const progress = data?.progress?.month;
  const selectedPeriodName = !data?.selectedMonth ? "等待所选期间结果" : data.selectedMonths && data.selectedMonths.length > 1
    ? `${financeMonthLabel(data.selectedMonths[0])}—${financeMonthLabel(data.selectedMonths.at(-1)!)}（${data.selectedMonths.length}个月）`
    : financeMonthLabel(data.selectedMonth!);
  const activeMonthSelection = allowInitialMonthFallback && data?.selection?.fallbackApplied
    ? data.selectedMonths ?? selectedMonths
    : selectedMonths;
  const normalizedExpenseSearch = expenseSearch.trim().toLocaleLowerCase("zh-CN");
  const expenseRows = (data?.expenses ?? []).filter((item) => {
    if (!normalizedExpenseSearch) return true;
    const displayName = item.name.replace(/^销售费用_/, "").replaceAll("_", " / ");
    return `${item.name} ${displayName}`.toLocaleLowerCase("zh-CN").includes(normalizedExpenseSearch);
  }).sort((left, right) => {
    if (expenseSort.column === "name") {
      const result = left.name.localeCompare(right.name, "zh-CN");
      return expenseSort.direction === "asc" ? result : -result;
    }
    const leftValue = left[expenseSort.column];
    const rightValue = right[expenseSort.column];
    if (leftValue === null && rightValue === null) return 0;
    if (leftValue === null) return 1;
    if (rightValue === null) return -1;
    const result = Number(leftValue) - Number(rightValue);
    return expenseSort.direction === "asc" ? result : -result;
  });
  const updateExpenseSort = (column: FinanceExpenseSortKey) => setExpenseSort((currentSort) => ({
    column,
    direction: currentSort.column === column ? (currentSort.direction === "desc" ? "asc" : "desc") : column === "name" ? "asc" : "desc",
  }));
  const selectMonthsStrictly = (months: string[] | null) => {
    setAllowInitialMonthFallback(false);
    setSelectedMonths(months);
  };
  const resetMonthsStrictly = () => {
    setAllowInitialMonthFallback(false);
    setSelectedMonths(globalMonths);
  };

  return <div className="finance-analysis-page data-refresh-region" aria-busy={loading}>
    <section className="finance-analysis-hero">
      <div><span className="eyebrow">FINANCIAL PERFORMANCE</span><h2>财报经营分析</h2><p>以月度财报与经营目标为口径，追踪销售、利润、毛利和动态费用异常。</p></div>
      <div className="finance-period-control"><div className="finance-hero-filter-row"><div className="finance-filter-field"><span>分析月份</span><FinanceMultiFilterSelect label="月份" allLabel="全部月份" options={monthOptions} selected={activeMonthSelection} onChange={selectMonthsStrictly} /></div></div><small>平台与店铺继承销售分析公共筛选 · 全局周期 {customStartDate} 至 {customEndDate} · 财报按涵盖月份汇总 · 数据截止 {data?.sync?.dataCutoffMonth ?? "—"}</small></div>
    </section>
    {resultState}
    {allowInitialMonthFallback && data?.selection?.fallbackApplied && <div className="inline-feedback warning" role="status"><strong>已显示最新可用财报</strong><span>全局月份 {data.selection.requestedMonths?.join("、") || globalMonths.join("、")} 尚未导入，已安全回退至 {data.selectedMonth}；手动选择月份后将严格按选择读取。</span></div>}
    {filterReconciliationNotice && <div className="inline-feedback warning" role="status"><strong>已调整财报筛选</strong><span>{filterReconciliationNotice}</span></div>}
    {data && data.selection?.truncated && <div className="inline-feedback warning" role="status"><strong>分析范围已设上限</strong><span>当前共有 {data.selection.availableMonthCount} 个可用月份，“全部月份”仅分析最近 {data.selection.months.length} 个月；如需更早月份，请在月份筛选中明确选择。</span></div>}
    {data?.hasData && current && targets && progress && data.yearToDate && <>
    {Boolean(data.targets?.legacyCompatibility?.excluded) && <div className="inline-feedback warning" role="status"><strong>旧目标缺少平台身份</strong><span>{data.targets?.legacyCompatibility?.reason} 当前有 {data.targets?.legacyCompatibility?.excluded} 项未参与本次 KPI。</span></div>}
    <section className="finance-kpi-grid">
      <FinanceKpiCard label="净销售额" value={formatCurrencyFromCents(current.netSalesCents)} targetLabel={targets.salesTargetCents > 0 ? `目标 ${formatCurrencyFromCents(targets.salesTargetCents)}` : "尚未设置销售目标"} progress={progress.sales} mom={{ text: formatFinanceChange(current.netSalesCents, previous?.netSalesCents), tone: financeChangeTone(current.netSalesCents, previous?.netSalesCents) }} yoy={{ text: formatFinanceChange(current.netSalesCents, yearAgo?.netSalesCents), tone: financeChangeTone(current.netSalesCents, yearAgo?.netSalesCents) }} tone="blue" />
      <FinanceKpiCard label="利润" value={formatCurrencyFromCents(current.profitCents)} targetLabel={targets.profitTargetCents > 0 ? `目标 ${formatCurrencyFromCents(targets.profitTargetCents)}` : "尚未设置利润目标"} progress={progress.profit} mom={{ text: formatFinanceChange(current.profitCents, previous?.profitCents), tone: financeChangeTone(current.profitCents, previous?.profitCents) }} yoy={{ text: formatFinanceChange(current.profitCents, yearAgo?.profitCents), tone: financeChangeTone(current.profitCents, yearAgo?.profitCents) }} tone="green" />
      <FinanceKpiCard label="大毛利率" value={formatFinanceBps(current.grossMarginBps)} targetLabel="大毛利 ÷ 净销售额" progress={null} mom={{ text: formatFinanceChange(current.grossMarginBps, previous?.grossMarginBps, true), tone: financeChangeTone(current.grossMarginBps, previous?.grossMarginBps) }} yoy={{ text: formatFinanceChange(current.grossMarginBps, yearAgo?.grossMarginBps, true), tone: financeChangeTone(current.grossMarginBps, yearAgo?.grossMarginBps) }} tone="cyan" icon="大" />
      <FinanceKpiCard label="小毛利率" value={formatFinanceBps(current.smallMarginBps)} targetLabel={targets.smallMarginBps > 0 ? `目标 ${formatFinanceBps(targets.smallMarginBps)}` : "尚未设置小毛利率目标"} progress={targets.smallMarginBps > 0 ? current.smallMarginBps / targets.smallMarginBps : null} mom={{ text: formatFinanceChange(current.smallMarginBps, previous?.smallMarginBps, true), tone: financeChangeTone(current.smallMarginBps, previous?.smallMarginBps) }} yoy={{ text: formatFinanceChange(current.smallMarginBps, yearAgo?.smallMarginBps, true), tone: financeChangeTone(current.smallMarginBps, yearAgo?.smallMarginBps) }} tone="purple" />
      <FinanceKpiCard label="推广费占比" value={formatFinanceBps(current.promotionFeeRatioBps)} targetLabel={targets.promotionFeeRatioBps > 0 ? `目标不高于 ${formatFinanceBps(targets.promotionFeeRatioBps)}` : "尚未设置推广费占比目标"} progress={targets.promotionFeeRatioBps > 0 ? current.promotionFeeRatioBps / targets.promotionFeeRatioBps : null} mom={{ text: formatFinanceChange(current.promotionFeeRatioBps, previous?.promotionFeeRatioBps, true), tone: financeChangeTone(current.promotionFeeRatioBps, previous?.promotionFeeRatioBps, true) }} yoy={{ text: formatFinanceChange(current.promotionFeeRatioBps, yearAgo?.promotionFeeRatioBps, true), tone: financeChangeTone(current.promotionFeeRatioBps, yearAgo?.promotionFeeRatioBps, true) }} tone="orange" />
      <FinanceKpiCard label="退货率" value={formatFinanceBps(current.returnRateBps)} targetLabel={`退货额 ${formatCurrencyFromCents(current.returnAmountCents)}`} progress={null} mom={{ text: formatFinanceChange(current.returnRateBps, previous?.returnRateBps, true), tone: financeChangeTone(current.returnRateBps, previous?.returnRateBps, true) }} yoy={{ text: formatFinanceChange(current.returnRateBps, yearAgo?.returnRateBps, true), tone: financeChangeTone(current.returnRateBps, yearAgo?.returnRateBps, true) }} tone="red" icon="退" />
    </section>
    <section className="panel finance-profit-bridge" aria-label="利润结构">
      <div><span>大毛利</span><strong>{formatCurrencyFromCents(current.grossProfitCents)}</strong><small>大毛利率 {formatFinanceBps(current.grossMarginBps)}</small></div><i>−</i>
      <div><span>销售费用</span><strong>{formatCurrencyFromCents(current.sellingExpenseCents)}</strong><small>占净销售 {current.netSalesCents ? formatFinanceBps(Math.round(current.sellingExpenseCents / current.netSalesCents * 10_000)) : "—"}</small></div><i>=</i>
      <div><span>小毛利</span><strong>{formatCurrencyFromCents(current.smallProfitCents)}</strong><small>小毛利率 {formatFinanceBps(current.smallMarginBps)}</small></div><i>−</i>
      <div><span>其他费用</span><strong>{formatCurrencyFromCents(current.otherExpenseCents)}</strong><small>管理、财务及税费</small></div><i>=</i>
      <div className={current.profitCents < 0 ? "loss" : "profit"}><span>利润</span><strong>{formatCurrencyFromCents(current.profitCents)}</strong><small>利润率 {formatFinanceBps(current.profitMarginBps)}</small></div>
    </section>
    <section className="finance-overview-grid">
      <article className="panel finance-trend-panel"><div className="finance-panel-heading"><div><span className="eyebrow">MONTHLY TREND</span><h2>销售与利润趋势</h2><p>每个节点直接展示净销售额与利润，数值单位为万元。</p></div><span className="soft-tag">{data.timeline.length} 个月</span></div><FinanceTrendChart rows={data.timeline} /><div className="finance-ytd-summary"><span>本年累计净销售<strong>{formatCurrencyFromCents(data.yearToDate.netSalesCents)}</strong></span><span>本年累计利润<strong>{formatCurrencyFromCents(data.yearToDate.profitCents)}</strong></span><span>累计小毛利率<strong>{formatFinanceBps(data.yearToDate.smallMarginBps)}</strong></span></div></article>
      <article className="panel finance-anomaly-panel"><div className="finance-panel-heading"><div><span className="eyebrow">EXCEPTION WATCH</span><h2>{selectedPeriodName}异常雷达</h2><p>按利润、目标差距及费用环比阈值自动识别。</p></div><span className="soft-tag">{data.anomalies.length} 项</span></div><div className="finance-anomaly-list">{data.anomalies.map((item, index) => <div className={`finance-anomaly ${item.level}`} key={`${item.title}-${index}`}><i>{item.level === "critical" ? "!" : item.level === "warning" ? "△" : "i"}</i><span><strong>{item.title}</strong><small>{item.detail}</small></span></div>)}</div></article>
    </section>
    </>}
    <section className="panel finance-expense-panel">
      <div className="finance-panel-heading"><div><span className="eyebrow">DYNAMIC EXPENSES</span><h2>费用同环比与异常点</h2><p>字段直接来自金蝶科目名称；同名科目已合并，新增科目会自动出现。</p></div><span className="soft-tag">{expenseSearch.trim() ? `显示 ${expenseRows.length} / ${(data?.expenses.length ?? 0)} 项` : `共 ${expenseRows.length} 项`}</span></div>
      <div className="finance-expense-filter-bar" aria-label="费用明细筛选"><div><strong>费用筛选</strong><small>月份与上方公共平台、店铺筛选同步更新所有指标</small></div><FinanceMultiFilterSelect label="月份" allLabel="全部月份" options={monthOptions} selected={activeMonthSelection} onChange={selectMonthsStrictly} /><button type="button" className="finance-filter-reset" onClick={resetMonthsStrictly}>重置月份</button></div>
      {data?.hasData && <div className="data-table-wrap finance-expense-scroll">
        <table className="data-table finance-expense-table" data-column-filter-scope={data.expensePagination?.truncated === false ? "full" : "none"}>
          <thead><tr>
            <th><div className="finance-expense-name-head"><FinanceSortButton label="费用科目" column="name" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /><label className="finance-expense-name-search"><span aria-hidden="true">⌕</span><input type="search" value={expenseSearch} onChange={(event) => setExpenseSearch(event.target.value)} placeholder="搜索费用名称" aria-label="搜索费用名称" /></label></div></th>
            <th><FinanceSortButton label={(data.selectedMonths?.length ?? 1) > 1 ? "所选期间金额" : "本月金额"} column="current" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label="费用率" column="feeRateBps" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label={(data.selectedMonths?.length ?? 1) > 1 ? "上期金额" : "上月金额"} column="previous" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label="环比" column="momRate" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label="去年同期" column="yearAgo" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label="同期费率" column="yearAgoFeeRateBps" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label="同比" column="yoyRate" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
            <th><FinanceSortButton label="状态" column="abnormal" activeColumn={expenseSort.column} direction={expenseSort.direction} onSort={updateExpenseSort} /></th>
          </tr></thead>
          <tbody>{expenseRows.map((item) => <tr key={item.name}>
            <td><strong title={item.name}>{item.name.replace(/^销售费用_/, "").replaceAll("_", " / ")}</strong></td>
            <td>{formatCurrencyFromCents(item.current)}</td>
            <td><strong className="finance-fee-rate">{formatFinanceBps(item.feeRateBps)}</strong></td>
            <td>{item.previous === null ? "—" : formatCurrencyFromCents(item.previous)}</td>
            <td className={item.momRate === null ? "muted-text" : item.momRate > 0 ? "orange-text" : "green-text"}>{item.momRate === null ? "—" : `${item.momRate >= 0 ? "+" : ""}${(item.momRate * 100).toFixed(1)}%`}</td>
            <td>{item.yearAgo === null ? "—" : formatCurrencyFromCents(item.yearAgo)}</td>
            <td><strong className="finance-fee-rate">{item.yearAgoFeeRateBps === null ? "—" : formatFinanceBps(item.yearAgoFeeRateBps)}</strong></td>
            <td>{item.yoyRate === null ? "—" : `${item.yoyRate >= 0 ? "+" : ""}${(item.yoyRate * 100).toFixed(1)}%`}</td>
            <td><span className={`status ${item.abnormal ? "status-warning" : "status-success"}`}><Dot tone={item.abnormal ? "orange" : "green"} />{item.abnormal ? "波动异常" : "正常"}</span></td>
          </tr>)}</tbody>
        </table>
      </div>}
    </section>

  </div>;
}

type FinanceTargetFormState = {
  id: string;
  expectedVersion: number | null;
  periodType: "month" | "year" | "project";
  periodKey: string;
  shopKey: string;
  platform: string;
  shopName: string;
  category: string;
  manager: string;
  salesTarget: string;
  profitTarget: string;
  grossMargin: string;
  smallMargin: string;
  inventoryCleanupTarget: string;
  promotionFeeRatio: string;
  stagnantInventoryTarget: string;
};

const currentShanghaiMonth = () => new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 7);
const emptyFinanceTargetForm = (): FinanceTargetFormState => ({
  id: "",
  expectedVersion: null,
  periodType: "year",
  periodKey: currentShanghaiMonth().slice(0, 4),
  shopKey: "",
  platform: "",
  shopName: "",
  category: "",
  manager: "",
  salesTarget: "",
  profitTarget: "",
  grossMargin: "",
  smallMargin: "",
  inventoryCleanupTarget: "",
  promotionFeeRatio: "",
  stagnantInventoryTarget: "",
});

function FinanceTargetSettingsView({ canManageTargets }: { canManageTargets: boolean }) {
  const [items, setItems] = useState<FinanceTarget[]>([]);
  const [options, setOptions] = useState<FinanceTargetOptions>({ shops: [], categories: [], projects: ["8系列"] });
  const [form, setForm] = useState<FinanceTargetFormState>(emptyFinanceTargetForm);
  const [targetPage, setTargetPage] = useState(1);
  const [targetYear, setTargetYear] = useState(() => currentShanghaiMonth().slice(0, 4));
  const [annualRefresh, setAnnualRefresh] = useState(0);
  const [targetPagination, setTargetPagination] = useState({ page: 1, pageSize: 100, total: 0, returned: 0, truncated: false });
  const [loading, setLoading] = useState(true);
  const [targetsLoaded, setTargetsLoaded] = useState(false);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [optionsError, setOptionsError] = useState("");
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [deletingTargetId, setDeletingTargetId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const targetRequestGenerationRef = useRef(0);
  const targetRequestControllerRef = useRef<AbortController | null>(null);
  const optionsRequestGenerationRef = useRef(0);
  const optionsRequestControllerRef = useRef<AbortController | null>(null);
  const optionsLoadedRef = useRef(false);
  const annualTargetFileRef = useRef<HTMLInputElement | null>(null);

  const loadTargets = useCallback(async () => {
    targetRequestControllerRef.current?.abort();
    const controller = new AbortController();
    targetRequestControllerRef.current = controller;
    const generation = ++targetRequestGenerationRef.current;
    setLoading(true);
    try {
      const response = await fetch(`/api/finance/targets?view=items&page=${targetPage}&pageSize=100&year=${targetYear}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as { items?: FinanceTarget[]; pagination?: { page: number; pageSize: number; total: number; returned: number; truncated: boolean }; error?: string } | null;
      if (!response.ok || !Array.isArray(payload?.items) || !payload.pagination) throw new Error(payload?.error || "目标进度情况读取失败");
      if (controller.signal.aborted || generation !== targetRequestGenerationRef.current) return;
      setItems(payload.items);
      setTargetPagination(payload.pagination);
      setTargetsLoaded(true);
    } catch (error) {
      if (controller.signal.aborted || generation !== targetRequestGenerationRef.current) return;
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "目标进度情况读取失败" });
    } finally {
      if (!controller.signal.aborted && generation === targetRequestGenerationRef.current) {
        setLoading(false);
        if (targetRequestControllerRef.current === controller) targetRequestControllerRef.current = null;
      }
    }
  }, [targetPage, targetYear]);

  const loadOptions = useCallback(async () => {
    if (!canManageTargets) return;
    optionsRequestControllerRef.current?.abort();
    const controller = new AbortController();
    optionsRequestControllerRef.current = controller;
    const generation = ++optionsRequestGenerationRef.current;
    setOptionsLoading(true);
    setOptionsError("");
    try {
      const response = await fetch("/api/finance/targets?view=options", { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as { options?: FinanceTargetOptions; error?: string } | null;
      if (!response.ok || !payload?.options || !Array.isArray(payload.options.shops) || !Array.isArray(payload.options.categories)) {
        throw new Error(payload?.error || "目标管理选项读取失败");
      }
      if (controller.signal.aborted || generation !== optionsRequestGenerationRef.current) return;
      setOptions(payload.options);
      optionsLoadedRef.current = true;
    } catch (error) {
      if (controller.signal.aborted || generation !== optionsRequestGenerationRef.current) return;
      setOptionsError(error instanceof Error ? error.message : "目标管理选项读取失败");
    } finally {
      if (!controller.signal.aborted && generation === optionsRequestGenerationRef.current) {
        setOptionsLoading(false);
        if (optionsRequestControllerRef.current === controller) optionsRequestControllerRef.current = null;
      }
    }
  }, [canManageTargets]);

  useEffect(() => { void loadTargets(); return () => targetRequestControllerRef.current?.abort(); }, [loadTargets]);
  useEffect(() => {
    if (!canManageTargets) {
      optionsRequestControllerRef.current?.abort();
      optionsLoadedRef.current = false;
      setOptionsLoading(false);
      setOptionsError("");
      return;
    }
    if (loading || optionsLoadedRef.current) return;
    void loadOptions();
    return () => optionsRequestControllerRef.current?.abort();
  }, [canManageTargets, loadOptions, loading]);
  const patchForm = (patch: Partial<FinanceTargetFormState>) => setForm((current) => ({ ...current, ...patch }));
  const toCents = (value: string) => Number.isFinite(Number(value)) ? Math.max(0, Math.round(Number(value) * 100)) : 0;
  const toBps = (value: string) => Number.isFinite(Number(value)) ? Math.max(0, Math.round(Number(value) * 100)) : 0;
  const saveTarget = async () => {
    if (!canManageTargets || saving || importing || deletingTargetId !== null) return;
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch("/api/finance/targets", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: form.id || undefined,
          expectedVersion: form.id ? form.expectedVersion : undefined,
          periodType: form.periodType,
          periodKey: form.periodKey,
          platform: form.platform,
          shopName: form.shopName,
          category: form.category,
          manager: form.manager,
          salesTargetCents: toCents(form.salesTarget),
          profitTargetCents: toCents(form.profitTarget),
          grossMarginBps: toBps(form.grossMargin),
          smallMarginBps: toBps(form.smallMargin),
          inventoryCleanupTargetCents: toCents(form.inventoryCleanupTarget),
          promotionFeeRatioBps: toBps(form.promotionFeeRatio),
          stagnantInventoryTargetCents: toCents(form.stagnantInventoryTarget),
        }),
      });
      const payload = await response.json().catch(() => null) as { item?: FinanceTarget; error?: string } | null;
      if (!response.ok || !payload?.item) {
        if (response.status === 409) {
          await loadTargets();
          throw new Error("目标已被其他人更新，列表已刷新；请重新进入编辑后再保存。");
        }
        throw new Error(payload?.error || "目标保存失败");
      }
      setMessage({ tone: "success", text: "全年目标已保存，年累计进度已同步更新。" });
      setAnnualRefresh((value) => value + 1);
      setForm({ ...emptyFinanceTargetForm(), periodKey: targetYear });
      await loadTargets();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "目标保存失败" });
    } finally {
      setSaving(false);
    }
  };
  const editTarget = (item: FinanceTarget) => {
    if (!canManageTargets || saving || importing || deletingTargetId !== null) return;
    setForm({
      id: item.id,
      expectedVersion: item.version,
      periodType: item.periodType,
      periodKey: item.periodKey,
      shopKey: item.platform && item.shopName ? JSON.stringify([item.platform, item.shopName]) : "",
      platform: item.platform,
      shopName: item.shopName,
      category: item.category,
      manager: item.manager,
      salesTarget: item.salesTargetCents ? String(item.salesTargetCents / 100) : "",
      profitTarget: item.profitTargetCents ? String(item.profitTargetCents / 100) : "",
      grossMargin: item.grossMarginBps ? String(item.grossMarginBps / 100) : "",
      smallMargin: item.smallMarginBps ? String(item.smallMarginBps / 100) : "",
      inventoryCleanupTarget: item.inventoryCleanupTargetCents ? String(item.inventoryCleanupTargetCents / 100) : "",
      promotionFeeRatio: item.promotionFeeRatioBps ? String(item.promotionFeeRatioBps / 100) : "",
      stagnantInventoryTarget: item.stagnantInventoryTargetCents ? String(item.stagnantInventoryTargetCents / 100) : "",
    });
  };
  const removeTarget = async (item: FinanceTarget) => {
    if (!canManageTargets || saving || importing || deletingTargetId !== null) return;
    const confirmed = window.confirm(`确认删除“${item.periodKey}”经营目标？此操作不可撤销。`);
    if (!confirmed) return;
    const providedReason = window.prompt("请输入删除原因（1—200 个字符）：", "");
    const reasonResult = validateFinanceTargetDeletionReason(providedReason);
    if (reasonResult.status === "cancelled") return;
    if (reasonResult.status === "invalid") {
      setMessage({ tone: "error", text: "删除原因必填，且长度必须为 1—200 个字符。" });
      return;
    }

    const query = new URLSearchParams({
      id: item.id,
      expectedVersion: String(item.version),
      reason: reasonResult.reason,
    });
    setDeletingTargetId(item.id);
    setMessage(null);
    try {
      const response = await fetch(`/api/finance/targets?${query.toString()}`, { method: "DELETE" });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!response.ok) {
        if (response.status === 409) {
          await loadTargets();
          setMessage({ tone: "error", text: "目标已被其他人更新，列表已刷新；请核对最新内容后重试。" });
          return;
        }
        setMessage({ tone: "error", text: payload?.error || "目标删除失败" });
        return;
      }
      setMessage({ tone: "success", text: "目标已删除。" });
      setAnnualRefresh((value) => value + 1);
      if (items.length === 1 && targetPage > 1) setTargetPage((value) => value - 1);
      else await loadTargets();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "目标删除失败" });
    } finally {
      setDeletingTargetId(null);
    }
  };

  const importAnnualTargets = async (file: File) => {
    if (!canManageTargets || importing || saving || deletingTargetId !== null) return;
    if (!/\.xlsx$/i.test(file.name)) {
      setMessage({ tone: "error", text: "年度目标仅支持 .xlsx 文件。" });
      return;
    }
    if (file.size <= 0 || file.size > 2 * 1024 * 1024) {
      setMessage({ tone: "error", text: "年度目标文件必须大于 0 且不超过 2MB。" });
      return;
    }
    setImporting(true);
    setMessage(null);
    try {
      const body = new FormData();
      body.set("year", targetYear);
      body.set("file", file);
      const response = await fetch("/api/finance/targets/import", { method: "POST", body });
      const payload = await response.json().catch(() => null) as {
        importedCount?: number; createdCount?: number; updatedCount?: number; skippedCount?: number; error?: string;
      } | null;
      if (!response.ok || !Number.isSafeInteger(payload?.importedCount)) {
        throw new Error(payload?.error || "年度目标导入失败");
      }
      setMessage({
        tone: "success",
        text: `${targetYear} 年目标已导入 ${payload?.importedCount ?? 0} 家店铺（新增 ${payload?.createdCount ?? 0}、更新 ${payload?.updatedCount ?? 0}、空白目标跳过 ${payload?.skippedCount ?? 0}）。未出现在文件中的原目标保持不变。`,
      });
      setAnnualRefresh((value) => value + 1);
      if (targetPage !== 1) setTargetPage(1);
      else await loadTargets();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "年度目标导入失败" });
    } finally {
      setImporting(false);
    }
  };

  const exportAnnualTargets = async () => {
    if (exporting || saving || importing || deletingTargetId !== null) return;
    setExporting(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/finance/targets/export?year=${targetYear}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(payload?.error || "年度目标导出失败");
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") ?? "";
      const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
      const fileName = match ? decodeURIComponent(match[1]) : `${targetYear}年度目标导出.xlsx`;
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = fileName;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
      setMessage({ tone: "success", text: `${targetYear} 年度目标已导出。` });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "年度目标导出失败" });
    } finally {
      setExporting(false);
    }
  };

  return <div className="finance-target-page">
    <section className="finance-analysis-hero target-hero"><div><span className="eyebrow">ANNUAL TARGET PROGRESS</span><h2>目标进度情况</h2><p>导入或填写全年销售、利润、大毛利率和推广费率目标，实际完成情况自动累计财报数据。</p></div><div className="annual-target-hero-actions"><label>目标年份 <select aria-label="目标年份" value={targetYear} disabled={saving || importing || deletingTargetId !== null} onChange={(event) => { setItems([]); setTargetsLoaded(false); setTargetPagination({ page: 1, pageSize: 100, total: 0, returned: 0, truncated: false }); setTargetYear(event.target.value); setTargetPage(1); setForm({ ...emptyFinanceTargetForm(), periodKey: event.target.value }); }}>{Array.from({ length: 201 }, (_, index) => String(1900 + index)).map((year) => <option key={year} value={year}>{year} 年</option>)}</select></label><a className="secondary-button" href="/api/finance/targets/import" download>下载导入模板</a><button type="button" className="secondary-button" disabled={saving || importing || deletingTargetId !== null} onClick={() => void exportAnnualTargets()}>{exporting ? "导出中…" : "导出年度目标"}</button>{canManageTargets && <><input ref={annualTargetFileRef} className="file-input-hidden" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ""; if (file) void importAnnualTargets(file); }} /><button type="button" className="secondary-button" disabled={importing || saving || deletingTargetId !== null} onClick={() => annualTargetFileRef.current?.click()}>{importing ? "导入中…" : "导入年度目标"}</button><small>模板金额单位：万元；导入到当前年份</small></>}</div></section>
    <FinanceAnnualProgressView key={targetYear} year={targetYear} refreshKey={annualRefresh} canManageTargets={canManageTargets} busy={saving || importing || deletingTargetId !== null} onEdit={(row) => { if (saving || importing || deletingTargetId !== null) return; if (row.target) editTarget(row.target); else setForm({ ...emptyFinanceTargetForm(), periodKey: targetYear, shopKey: row.key, platform: row.platform, shopName: row.shopName }); document.getElementById("annual-target-editor")?.scrollIntoView({ behavior: "smooth", block: "center" }); }} onDelete={(row) => { if (!row.target) return; void removeTarget(row.target); }} />
    {message && <div className={`inline-feedback ${message.tone}`}><strong>{message.tone === "success" ? "操作成功" : "操作失败"}</strong><span>{message.text}</span></div>}
    {canManageTargets && optionsLoading && <div className="inline-feedback" role="status"><strong>管理选项加载中</strong><span>目标列表已独立读取；正在后台加载店铺和品类选项…</span></div>}
    {canManageTargets && optionsError && <div className="inline-feedback error" role="alert"><strong>管理选项加载失败</strong><span>{optionsError}</span><button type="button" className="row-action" onClick={() => void loadOptions()}>重试加载</button></div>}
    {options.pagination?.shops.truncated && <div className="inline-feedback warning" role="status"><strong>店铺选项已设上限</strong><span>当前展示 {options.pagination.shops.returned} / {options.pagination.shops.total} 个平台店铺，请先在财报数据中核对目标店铺或缩小历史数据范围。</span></div>}
    {canManageTargets ? <section className="panel finance-target-form-panel">
      <div id="annual-target-editor" className="finance-panel-heading"><div><span className="eyebrow">{form.id ? "EDIT TARGET" : "NEW TARGET"}</span><h2>{form.id ? "编辑目标" : "新增目标"}</h2><p>金额单位为元；同年份、同平台、同店铺只设置一组全年目标。</p></div>{form.id && <button className="secondary-button" onClick={() => setForm({ ...emptyFinanceTargetForm(), periodKey: targetYear })}>取消编辑</button>}</div>
      <div className="finance-target-form-grid">
        <label><span>目标年份</span><input type="text" value={form.periodKey} readOnly /></label>
        <label><span>平台 · 店铺</span><SearchableSelect value={form.shopKey} onChange={(value) => { const selected = options.shops.find((item) => item.key === value); patchForm({ shopKey: value, platform: selected?.platform ?? "", shopName: selected?.name ?? "" }); }} ariaLabel="经营目标平台与店铺" searchPlaceholder="搜索平台或店铺" options={options.shops.map((item) => ({ value: item.key, label: `${item.platform} · ${item.name}` }))} /></label>
        <label><span>店长 / 负责人</span><input value={form.manager} onChange={(event) => patchForm({ manager: event.target.value })} placeholder="输入姓名" /></label>
        <label><span>全年销售目标（元）</span><input type="number" min="0" step="0.01" value={form.salesTarget} onChange={(event) => patchForm({ salesTarget: event.target.value })} /></label>
        <label><span>全年利润目标（元）</span><input type="number" min="0" step="0.01" value={form.profitTarget} onChange={(event) => patchForm({ profitTarget: event.target.value })} /></label>
        <label><span>大毛利率目标（%）</span><input type="number" min="0" max="100" step="0.01" value={form.grossMargin} onChange={(event) => patchForm({ grossMargin: event.target.value })} /></label>
        <label><span>推广费目标（%）</span><input type="number" min="0" max="100" step="0.01" value={form.promotionFeeRatio} onChange={(event) => patchForm({ promotionFeeRatio: event.target.value })} /></label>
      </div>
      <datalist id="finance-category-options">{options.categories.map((item) => <option key={item} value={item} />)}</datalist><datalist id="finance-project-options">{options.projects.map((item) => <option key={item} value={item} />)}</datalist>
      <div className="finance-target-actions"><span>全年目标按整店统计，实际完成额自动读取财报</span><button type="button" className="primary-button" disabled={saving || importing || (form.periodType !== "project" && !form.shopKey)} onClick={() => void saveTarget()}>{saving ? "保存中…" : form.id ? "保存修改" : "保存目标"}</button></div>
    </section> : <div className="inline-feedback warning" role="status"><strong>当前为只读模式</strong><span>仅管理员可新增、编辑或删除经营目标；你仍可查看全部目标并使用分页。</span></div>}
    <section className="panel finance-target-list-panel data-refresh-region" aria-busy={loading}>
      <div className="finance-panel-heading"><div><span className="eyebrow">TARGET LIST</span><h2>已设置目标</h2><p>本年度整店目标；保存后立即更新上方年累计进度。</p></div><span className="soft-tag">本页 {targetPagination.returned} / 共 {targetPagination.total} 项</span></div>
      {loading && !targetsLoaded ? <div className="table-state">正在读取目标…</div> : <>
        <div className="data-table-wrap"><table className="data-table finance-target-table"><thead><tr><th>目标年份</th><th>店铺</th><th>负责人</th><th>全年销售目标</th><th>全年利润目标</th><th>大毛利率目标</th><th>推广费目标</th><th>{canManageTargets ? "操作" : "权限"}</th></tr></thead><tbody>
          {items.map((item) => <tr key={item.id}><td><strong>{item.periodType === "month" ? "月度" : item.periodType === "year" ? "年度" : "项目"}</strong><small>{item.periodKey}</small></td><td><strong>{item.periodType === "project" ? item.periodKey : item.shopName}</strong><small>{item.periodType === "project" ? "呆滞库存" : `${item.platform || "旧目标 · 平台待确认"}${item.category ? ` · ${item.category}` : " · 整店"}`}</small></td><td>{item.manager || "—"}</td><td>{item.periodType === "project" ? "—" : formatCurrencyFromCents(item.salesTargetCents)}</td><td>{item.periodType === "project" ? "—" : formatCurrencyFromCents(item.profitTargetCents)}</td><td>{item.periodType === "project" || item.grossMarginBps <= 0 ? "—" : `${(item.grossMarginBps / 100).toFixed(1)}%`}</td><td>{item.periodType === "project" || item.promotionFeeRatioBps <= 0 ? "—" : `${(item.promotionFeeRatioBps / 100).toFixed(1)}%`}</td><td>{canManageTargets ? <div className="finance-target-row-actions"><button type="button" disabled={saving || importing || deletingTargetId !== null} onClick={() => editTarget(item)}>编辑</button><button type="button" className="danger" disabled={saving || importing || deletingTargetId !== null} onClick={() => void removeTarget(item)}>{deletingTargetId === item.id ? "删除中…" : "删除"}</button></div> : <span className="soft-text">只读</span>}</td></tr>)}
          {items.length === 0 && <tr><td colSpan={8}><div className="table-state">{canManageTargets ? "还没有目标，先在上方新增或导入一份年度目标。" : "当前没有可查看的经营目标。"}</div></td></tr>}
        </tbody></table></div>
        {(targetPage > 1 || targetPagination.truncated) && <div className="customer-service-pagination"><button type="button" className="row-action" disabled={targetPage <= 1} onClick={() => setTargetPage((value) => value - 1)}>上一页</button><span>第 {targetPage} 页 · 每页最多 100 项</span><button type="button" className="row-action" disabled={!targetPagination.truncated} onClick={() => setTargetPage((value) => value + 1)}>下一页</button></div>}
      </>}
    </section>
  </div>;
}

export default function SalesView({ range, customStartDate, customEndDate, currentUser, moduleView, onModuleViewChange }: { range: SalesRangeLabel; customStartDate: string; customEndDate: string; currentUser: CurrentUser | null; moduleView: SalesTab; onModuleViewChange: (view: SalesTab) => void }) {
  const apiRange = salesRangeMap[range];
  const activeTab = moduleView;
  const usesSalesSummary = activeTab === "overview" || activeTab === "channel";
  const canManageTargets = canManageFinanceTargets(currentUser);
  const [summary, setSummary] = useState<SalesSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [filters, setFilters] = useState<SalesSharedFilters>(readSalesSharedFilters);
  const [financeFilterOptions, setFinanceFilterOptions] = useState<SalesSharedFilterOptions | null>(null);
  const debouncedProductQuery = useDebouncedValue(filters.productQuery);
  const productQueries = useMemo(() => parseProductQueries(debouncedProductQuery), [debouncedProductQuery]);
  useAiPageDetails("sales", {
    period: activeTab === "targets" ? null : { startDate: customStartDate, endDate: customEndDate },
    filters: activeTab === "targets" ? {} : { query: debouncedProductQuery.trim(), platforms: filters.platforms, outletKeys: filters.outletKeys, categories: filters.categories },
  }, activeTab !== "category" && activeTab !== "finance");


  const updateFilters = useCallback((next: SalesSharedFilters) => {
    setFilters(next);
    writeSalesSharedFilters(next);
  }, []);

  const updateFinanceDimensionFilters = useCallback((platforms: string[], outletKeys: string[]) => {
    updateFilters({ ...filters, platforms, outletKeys });
  }, [filters, updateFilters]);

  useEffect(() => {
    setFinanceFilterOptions(null);
  }, [customEndDate, customStartDate]);

  const changeSalesTab = useCallback((tab: SalesTab) => {
    onModuleViewChange(tab);
  }, [onModuleViewChange]);

  useEffect(() => {
    const onPopState = () => setFilters(readSalesSharedFilters());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!usesSalesSummary) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void (async () => {
        if (controller.signal.aborted) return;
        setLoading(true);
        setError("");

        try {
          const query = new URLSearchParams({ range: apiRange });
          if (apiRange === "custom") {
            query.set("startDate", customStartDate);
            query.set("endDate", customEndDate);
          }
          const queries = parseProductQueriesStrict(debouncedProductQuery);
          if (queries.length > 0) query.set("productQuery", queries.join(","));
          filters.platforms.forEach((platform) => query.append("platform", platform));
          filters.outletKeys.forEach((shopKey) => query.append("outlet", shopKey));
          filters.categories.forEach((category) => query.append("category", category));
          const response = await fetchWithTransientRetry(
            `/api/sales/summary?${query.toString()}`,
            { cache: "no-store", signal: controller.signal },
            { delaysMs: [1_000, 2_000, 4_000, 8_000, 12_000, 15_000] },
          );
          const payload = await response.json().catch(() => null) as (SalesSummaryResponse & { message?: string; error?: string }) | null;
          if (!response.ok) throw new Error(payload?.message || payload?.error || `销售汇总读取失败（${response.status}）`);
          if (!payload?.current || !Array.isArray(payload.channels)) throw new Error("销售汇总响应格式不完整");
          setSummary(payload);
        } catch (requestError) {
          if (requestError instanceof DOMException && requestError.name === "AbortError") return;
          setError(requestError instanceof Error ? requestError.message : "暂时无法读取销售汇总");
        } finally {
          if (!controller.signal.aborted) setLoading(false);
        }
      })();
    }, 0);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [apiRange, customEndDate, customStartDate, debouncedProductQuery, filters.categories, filters.outletKeys, filters.platforms, retryKey, usesSalesSummary]);

  const current = summary?.current;
  const previous = summary?.previous;
  const yearAgo = summary?.yearAgo;
  const channels = useMemo(() => summary?.channels ?? [], [summary?.channels]);
  const salesChannels = summary?.shops?.length ? summary.shops : channels;
  const platforms = summary?.platforms?.length ? summary.platforms : channels;
  const salesFilterOptions = summary?.filterOptions ?? { platforms: [], shops: [], categories: [] };
  const hasData = Boolean(current && (current.lineCount > 0 || current.orderCount > 0 || current.grossSalesCents !== 0 || current.netSalesCents !== 0));
  const donutBackground = useMemo(() => {
    if (!channels.length) return "#eef1f5";
    let cursor = 0;
    const stops = channels.map((channel, index) => {
      const start = cursor;
      cursor += Math.max(0, rateAsPercent(channel.shareRate));
      return `${channelColors[index % channelColors.length]} ${start}% ${Math.min(cursor, 100)}%`;
    });
    if (cursor < 100) stops.push(`#eef1f5 ${cursor}% 100%`);
    return `conic-gradient(${stops.join(",")})`;
  }, [channels]);
  const salesSubnav = <SalesSubnav active={activeTab} onChange={changeSalesTab} />;
  const sharedFilterBar = (capabilities?: { categories?: boolean; product?: boolean }, options: SalesSharedFilterOptions = salesFilterOptions) => <SalesFilterBar filters={filters} options={options} capabilities={capabilities} updating={usesSalesSummary && loading} scopeLabel={activeTab === "finance" ? "财报分析" : activeTab === "category" ? "品类分析" : activeTab === "channel" ? "渠道分析" : "销售总览"} onChange={updateFilters} />;

  if (activeTab === "category") return <>{salesSubnav}{sharedFilterBar()}<SalesCategoryView startDate={customStartDate} endDate={customEndDate} filters={filters} onFiltersChange={updateFilters} /></>;
  if (activeTab === "finance") return <>{salesSubnav}{sharedFilterBar({ categories: false, product: false }, financeFilterOptions ?? salesFilterOptions)}<FinanceAnalysisView customStartDate={customStartDate} customEndDate={customEndDate} selectedPlatforms={filters.platforms} selectedShopKeys={filters.outletKeys} onDimensionFiltersChange={updateFinanceDimensionFilters} onFilterOptionsChange={setFinanceFilterOptions} /></>;
  if (activeTab === "targets") return <>{salesSubnav}<FinanceTargetSettingsView canManageTargets={canManageTargets} /></>;

  if (loading && !summary) {
    return (
      <>{salesSubnav}<section className="panel data-state sales-data-state" role="status" aria-live="polite">
          <span className="state-spinner" aria-hidden="true" />
          <strong>正在读取{range}销售数据</strong>
          <p>正在汇总销售额、毛利与渠道构成…</p>
        </section></>
    );
  }

  if (error && !summary) {
    return (
      <>{salesSubnav}<section className="panel data-state sales-data-state data-state-error" role="alert">
          <span className="state-symbol" aria-hidden="true">!</span>
          <strong>销售数据加载失败</strong>
          <p>{error}</p>
          <button className="secondary-button" onClick={() => setRetryKey((key) => key + 1)}>重新加载</button>
        </section></>
    );
  }

  if (!hasData || !current) {
    return (
      <>{salesSubnav}<section className="panel data-state sales-data-state">
          <span className="state-symbol" aria-hidden="true">∅</span>
          <strong>{range}暂无销售数据</strong>
          <p>{productQueries.length > 0 ? "当前货品编码或名称在该统计周期内没有销售记录，可修改或清空下方查询。" : "请先在“数据导入”中上传吉客云销售单明细账，或切换其他统计周期。"}</p>
        </section>{sharedFilterBar()}</>
    );
  }

  const rangeNote = summary?.startDate && summary?.endDate
    ? `${summary.startDate} 至 ${summary.endDate}`
    : `${range}实时汇总`;
  const comparisonPeriodNote = summary?.previousStartDate && summary?.previousEndDate
    ? `环比：${summary.previousStartDate} 至 ${summary.previousEndDate}`
    : "";
  const sourceNote = [comparisonPeriodNote, summary?.latestBatch?.fileName ? `最近批次：${summary.latestBatch.fileName}` : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      {salesSubnav}
      <div className="sales-period-note">
        <span><Dot tone="green" />已加载真实明细</span>
        <strong>{rangeNote}</strong>
        {sourceNote && <small title={sourceNote}>{sourceNote}</small>}
      </div>
      {sharedFilterBar()}
      {error && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>销售数据刷新失败</strong><p>{error}；当前仍显示上一次成功结果。</p></div><button className="row-action" onClick={() => setRetryKey((key) => key + 1)}>重试</button></section>}
      {activeTab === "channel" ? (
        <div className="data-refresh-region" aria-busy={loading}><ChannelAnalysisView channels={salesChannels} platforms={platforms} current={current} pagination={{ channel: summary.groupPagination?.shops, platform: summary.groupPagination?.platforms }} /></div>
      ) : <>
        <section className="metrics-grid sales-metrics-grid data-refresh-region" aria-busy={loading}>
          <MetricCard label="销售额（GMV）" value={formatCurrencyFromCents(current.grossSalesCents)} change={formatChange(current.grossSalesCents, previous?.grossSalesCents)} hint={comparisonHint(current.grossSalesCents, previous?.grossSalesCents, yearAgo?.grossSalesCents)} tone="blue" />
          <MetricCard label="销售净额" value={formatCurrencyFromCents(current.netSalesCents)} change={formatChange(current.netSalesCents, previous?.netSalesCents)} hint={comparisonHint(current.netSalesCents, previous?.netSalesCents, yearAgo?.netSalesCents)} tone="green" />
          <MetricCard label="订单毛利" value={formatCurrencyFromCents(current.grossProfitCents)} change={formatChange(current.grossProfitCents, previous?.grossProfitCents)} hint={comparisonHint(current.grossProfitCents, previous?.grossProfitCents, yearAgo?.grossProfitCents)} tone="purple" />
          <MetricCard label="退货金额" value={formatCurrencyFromCents(current.refundAmountCents)} change={formatChange(current.refundAmountCents, previous?.refundAmountCents)} hint={comparisonHint(current.refundAmountCents, previous?.refundAmountCents, yearAgo?.refundAmountCents)} tone="orange" />
          <MetricCard label="净销量" value={formatCount(current.netQuantity)} change={formatChange(current.netQuantity, previous?.netQuantity)} hint={comparisonHint(current.netQuantity, previous?.netQuantity, yearAgo?.netQuantity)} tone="blue" />
          <MetricCard label="客单价" value={formatCurrencyFromCents(current.averageOrderValueCents)} change={formatChange(current.averageOrderValueCents, previous?.averageOrderValueCents)} hint={comparisonHint(current.averageOrderValueCents, previous?.averageOrderValueCents, yearAgo?.averageOrderValueCents)} tone="purple" />
          <MetricCard label="退货率" value={formatRate(current.refundRate)} change={formatChange(rateAsPercent(current.refundRate), rateAsPercent(previous?.refundRate))} hint={comparisonHint(rateAsPercent(current.refundRate), rateAsPercent(previous?.refundRate), rateAsPercent(yearAgo?.refundRate))} tone="orange" />
          <MetricCard label="大毛利率" value={formatRate(current.grossMarginRate)} change={formatChange(rateAsPercent(current.grossMarginRate), rateAsPercent(previous?.grossMarginRate))} hint={comparisonHint(rateAsPercent(current.grossMarginRate), rateAsPercent(previous?.grossMarginRate), rateAsPercent(yearAgo?.grossMarginRate))} tone="green" />
        </section>
        <section className="split-panels data-refresh-region" aria-busy={loading}>
          <article className="panel">
            <SectionHeader title="渠道销售构成" note="按销售净额统计渠道占比，并展示各渠道净销售同比" />
            <div className="channel-chart">
              <div className="donut" style={{ background: donutBackground }}><div><strong>{(current.netSalesCents / 1000000).toFixed(1)}</strong><small>万元净额</small></div></div>
              <div className="channel-list channel-sales-list"><div className="channel-list-head"><span>渠道</span><strong>净销售额</strong><em>占比</em><small>净销售同比</small></div>{channels.map((item, index) => <div key={item.name}><span><Dot tone={channelTones[index % channelTones.length]} />{item.name || "未分类"}</span><strong>{formatCurrencyFromCents(item.netSalesCents)}</strong><em>{formatRate(item.shareRate)}</em><small className={netSalesYearOverYearTone(item.salesYearOverYearRate)}>{formatNetSalesYearOverYear(item.salesYearOverYearRate)}</small></div>)}</div>
            </div>
          </article>
          <article className="panel">
            <SectionHeader title="渠道毛利表现" note="平台大毛利率、净销售同比与退货率" />
            <div className="progress-list">{channels.map((item, index) => {
              const margin = Math.max(0, Math.min(rateAsPercent(item.grossMarginRate), 100));
              const tone = channelTones[index % channelTones.length];
              return <div key={item.name}><div><span>{item.name || "未分类"}<small>净销售同比 {formatNetSalesYearOverYear(item.salesYearOverYearRate)} · 退货率 {formatRate(item.refundRate)}</small></span><strong className="platform-margin"><b>{formatRate(item.grossMarginRate)}</b><small>大毛利率</small></strong></div><span className="progress-track"><i className={`bg-${tone}`} style={{ width: `${margin}%` }} /></span></div>;
            })}</div>
            <div className="insight-card"><span>数据口径</span><p>渠道构成与订单行数来自当前周期销售明细；大毛利率统一按（分摊后金额 − 货品成本）÷ 分摊后金额计算，不扣费用分摊。</p></div>
          </article>
        </section>
        <section className="product-situation-grid data-refresh-region" aria-busy={loading}><ProductSalesTrend daily={summary?.daily ?? []} selectedProductCount={productQueries.length} /><ShopSalesDistribution shops={summary?.outlets ?? []} /></section>
      </>}
    </>
  );
}
