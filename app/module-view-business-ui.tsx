"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import {
  type SalesStats,
  type SalesChannel,
  formatCurrencyFromCents,
  formatCount,
  formatRate,
} from "./module-view-shared";

type MultiFilterOption = string | { value: string; label: string };

export function MultiFilterSelect({ label, allLabel, ariaLabel, options, selected, onChange }: { label: string; allLabel: string; ariaLabel: string; options: MultiFilterOption[]; selected: string[]; onChange: (next: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const normalizedOptions = options.map((option) => typeof option === "string" ? { value: option, label: option } : option);
  const visibleOptions = normalizedOptions.filter((option) => option.label.toLowerCase().includes(search.trim().toLowerCase()));
  const toggleOption = (option: string) => onChange(selected.includes(option) ? selected.filter((item) => item !== option) : [...selected, option]);
  const summary = selected.length === 0 ? allLabel : `已选 ${formatCount(selected.length)} 个${label}`;
  return <div className={`multi-filter-select ${open ? "open" : ""}`}><button type="button" className="multi-filter-trigger" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} onClick={() => { setOpen((value) => !value); setSearch(""); }}><span title={summary}>{summary}</span><i>⌄</i></button>{open && <div className="multi-filter-menu" role="listbox" aria-label={`${ariaLabel}多选`} aria-multiselectable="true"><div className="multi-filter-menu-head"><strong>{label}筛选</strong><button type="button" onClick={() => onChange([])} disabled={selected.length === 0}>清空</button></div><label className="multi-filter-search">⌕<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`搜索${label}`} aria-label={`搜索${label}`} /></label><button type="button" className={selected.length === 0 ? "selected" : ""} role="option" aria-selected={selected.length === 0} onClick={() => onChange([])}><i>{selected.length === 0 ? "✓" : ""}</i>{allLabel}</button>{visibleOptions.map((option) => <button type="button" key={option.value} className={selected.includes(option.value) ? "selected" : ""} role="option" aria-selected={selected.includes(option.value)} onClick={() => toggleOption(option.value)}><i>{selected.includes(option.value) ? "✓" : ""}</i><span title={option.label}>{option.label}</span></button>)}{visibleOptions.length === 0 && <p className="multi-filter-menu-empty">没有匹配项</p>}</div>}</div>;
}

export function InventoryKpiCard({
  label,
  value,
  note,
  tone,
  icon,
  selected,
  onClick,
}: {
  label: string;
  value: string;
  note: string;
  tone: "blue" | "green" | "orange" | "purple";
  icon: string;
  selected?: boolean;
  onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "article";
  return (
    <Tag className={`inventory-kpi-card${onClick ? " summary-filter-card" : ""}`} type={onClick ? "button" : undefined} aria-pressed={onClick ? selected : undefined} onClick={onClick}>
      <div><span>{label}</span><i className={`inventory-kpi-icon ${tone}`}>{icon}</i></div>
      <strong>{value}</strong>
      <p>{note}</p>
    </Tag>
  );
}

type TrendMetric = "netSales" | "netQuantity" | "grossMargin";
type TrendGranularity = "day" | "week" | "month";
type ProductTrendPoint = { key: string; label: string; netSalesCents: number; netQuantity: number; costAmountCents: number; grossProfitCents: number; grossMarginRate: number };

const trendMetricMeta: Record<TrendMetric, { label: string; color: string }> = {
  netSales: { label: "净销售额", color: "#4776e6" },
  netQuantity: { label: "净销量", color: "#27a978" },
  grossMargin: { label: "大毛利率", color: "#8167d9" },
};

function aggregateProductTrend(daily: Array<{ date: string } & SalesStats>, granularity: TrendGranularity): ProductTrendPoint[] {
  const buckets = new Map<string, Omit<ProductTrendPoint, "grossMarginRate">>();
  for (const item of daily) {
    let key = item.date;
    let label = item.date.slice(5);
    if (granularity === "week") {
      const date = new Date(`${item.date}T00:00:00Z`);
      const weekDay = date.getUTCDay();
      date.setUTCDate(date.getUTCDate() + (weekDay === 0 ? -6 : 1 - weekDay));
      key = date.toISOString().slice(0, 10);
      label = `${key.slice(5)}周`;
    } else if (granularity === "month") {
      key = item.date.slice(0, 7);
      label = key;
    }
    const current = buckets.get(key) ?? { key, label, netSalesCents: 0, netQuantity: 0, costAmountCents: 0, grossProfitCents: 0 };
    current.netSalesCents += item.netSalesCents;
    current.netQuantity += item.netQuantity;
    current.costAmountCents += item.costAmountCents;
    current.grossProfitCents += item.grossProfitCents;
    buckets.set(key, current);
  }
  return [...buckets.values()].sort((left, right) => left.key.localeCompare(right.key)).map((item) => ({
    ...item,
    grossMarginRate: item.netSalesCents === 0 ? 0 : (item.netSalesCents - item.costAmountCents) / item.netSalesCents,
  }));
}

export function ProductSalesTrend({ daily, selectedProductCount }: { daily: Array<{ date: string } & SalesStats>; selectedProductCount: number }) {
  const [granularity, setGranularity] = useState<TrendGranularity>("day");
  const [selectedMetrics, setSelectedMetrics] = useState<TrendMetric[]>(["netSales", "netQuantity", "grossMargin"]);
  const [hoveredPointIndex, setHoveredPointIndex] = useState<number | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const points = useMemo(() => aggregateProductTrend(daily, granularity), [daily, granularity]);
  const activePointIndex = points.length === 0 ? null : Math.min(hoveredPointIndex ?? points.length - 1, points.length - 1);
  const activePoint = activePointIndex === null ? null : points[activePointIndex];
  const totals = useMemo(() => {
    const netSalesCents = points.reduce((sum, item) => sum + item.netSalesCents, 0);
    const netQuantity = points.reduce((sum, item) => sum + item.netQuantity, 0);
    const costAmountCents = points.reduce((sum, item) => sum + item.costAmountCents, 0);
    return { netSalesCents, netQuantity, grossMarginRate: netSalesCents === 0 ? 0 : (netSalesCents - costAmountCents) / netSalesCents };
  }, [points]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const container = canvas.parentElement;
    if (!container) return;
    const draw = () => {
      const width = Math.max(320, Math.floor(container.getBoundingClientRect().width));
      const height = 300;
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const plot = { left: 34, right: width - 18, top: 18, bottom: height - 35 };
      context.lineWidth = 1;
      context.font = "10px sans-serif";
      context.fillStyle = "#94a0b1";
      context.strokeStyle = "#edf0f4";
      for (let index = 0; index <= 4; index += 1) {
        const y = plot.top + (plot.bottom - plot.top) * index / 4;
        context.beginPath(); context.moveTo(plot.left, y); context.lineTo(plot.right, y); context.stroke();
      }
      if (points.length === 0 || selectedMetrics.length === 0) return;
      const xFor = (index: number) => points.length === 1 ? (plot.left + plot.right) / 2 : plot.left + (plot.right - plot.left) * index / (points.length - 1);
      const metricValue = (point: ProductTrendPoint, metric: TrendMetric) => metric === "netSales" ? point.netSalesCents / 100 : metric === "netQuantity" ? point.netQuantity : point.grossMarginRate * 100;
      const showPointLabels = points.length <= 12;
      for (const [metricIndex, metric] of selectedMetrics.entries()) {
        const values = points.map((point) => metricValue(point, metric));
        const minimum = Math.min(0, ...values);
        const maximum = Math.max(0, ...values);
        const span = maximum - minimum || 1;
        context.strokeStyle = trendMetricMeta[metric].color;
        context.lineWidth = 2.4;
        context.lineJoin = "round";
        context.lineCap = "round";
        context.beginPath();
        values.forEach((value, index) => {
          const x = xFor(index);
          const y = plot.bottom - (value - minimum) / span * (plot.bottom - plot.top);
          if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
        });
        context.stroke();
        values.forEach((value, index) => {
          const x = xFor(index);
          const y = plot.bottom - (value - minimum) / span * (plot.bottom - plot.top);
          context.fillStyle = "#fff"; context.strokeStyle = trendMetricMeta[metric].color; context.lineWidth = 2;
          context.beginPath(); context.arc(x, y, 3, 0, Math.PI * 2); context.fill(); context.stroke();
          if (!showPointLabels) return;
          const point = points[index];
          const valueLabel = metric === "netSales" ? formatCurrencyFromCents(point.netSalesCents) : metric === "netQuantity" ? `${formatCount(point.netQuantity)}件` : formatRate(point.grossMarginRate);
          const labelY = Math.max(plot.top + 10, y - 10 - metricIndex * 12);
          context.save();
          context.font = "8px sans-serif";
          context.textAlign = "center";
          const labelWidth = context.measureText(valueLabel).width + 6;
          const labelX = Math.min(plot.right - labelWidth / 2, Math.max(plot.left + labelWidth / 2, x));
          context.fillStyle = "rgba(255,255,255,.9)";
          context.fillRect(labelX - labelWidth / 2, labelY - 8, labelWidth, 11);
          context.fillStyle = trendMetricMeta[metric].color;
          context.fillText(valueLabel, labelX, labelY);
          context.restore();
        });
      }
      if (activePointIndex !== null) {
        const activeX = xFor(activePointIndex);
        context.save();
        context.strokeStyle = "#b9c7dd";
        context.setLineDash([4, 4]);
        context.lineWidth = 1;
        context.beginPath(); context.moveTo(activeX, plot.top); context.lineTo(activeX, plot.bottom); context.stroke();
        context.restore();
      }
      const labelStep = Math.max(1, Math.ceil(points.length / 6));
      context.fillStyle = "#8793a4";
      context.textAlign = "center";
      points.forEach((point, index) => {
        if (index % labelStep !== 0 && index !== points.length - 1) return;
        context.fillText(point.label, xFor(index), height - 12);
      });
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(container);
    return () => observer.disconnect();
  }, [activePointIndex, points, selectedMetrics]);

  const handleTrendPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (points.length === 0) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const plotLeft = 34;
    const plotRight = Math.max(plotLeft + 1, bounds.width - 18);
    const progress = Math.min(1, Math.max(0, (event.clientX - bounds.left - plotLeft) / (plotRight - plotLeft)));
    setHoveredPointIndex(Math.round(progress * (points.length - 1)));
  };

  const toggleMetric = (metric: TrendMetric) => setSelectedMetrics((current) => current.includes(metric)
    ? current.length === 1 ? current : current.filter((item) => item !== metric)
    : [...current, metric]);
  const activePointMetricValue = (metric: TrendMetric) => {
    if (!activePoint) return "—";
    if (metric === "netSales") return formatCurrencyFromCents(activePoint.netSalesCents);
    if (metric === "netQuantity") return `${formatCount(activePoint.netQuantity)} 件`;
    return formatRate(activePoint.grossMarginRate);
  };

  return <section className="panel product-sales-trend-panel">
    <div className="product-trend-toolbar"><div><span className="eyebrow">PRODUCT SITUATION</span><h2>货品情况</h2><p>{selectedProductCount > 0 ? `已按 ${formatCount(selectedProductCount)} 个货品汇总` : "当前统计周期内全部货品的经营趋势"} · 净销售额、净销量与大毛利率可单独或组合查看。</p></div><div className="product-trend-controls"><div className="trend-metric-toggle" role="group" aria-label="趋势指标选择">{(Object.keys(trendMetricMeta) as TrendMetric[]).map((metric) => <button type="button" key={metric} className={selectedMetrics.includes(metric) ? "active" : ""} aria-pressed={selectedMetrics.includes(metric)} onClick={() => toggleMetric(metric)}><i style={{ background: trendMetricMeta[metric].color }} />{trendMetricMeta[metric].label}</button>)}</div><div className="segmented trend-granularity" role="group" aria-label="趋势时间维度"><button type="button" className={granularity === "day" ? "active" : ""} onClick={() => setGranularity("day")}>日维度</button><button type="button" className={granularity === "week" ? "active" : ""} onClick={() => setGranularity("week")}>周维度</button><button type="button" className={granularity === "month" ? "active" : ""} onClick={() => setGranularity("month")}>月维度</button></div></div></div>
    <div className="product-trend-summary"><div className={selectedMetrics.includes("netSales") ? "active" : ""}><span>净销售额</span><strong>{formatCurrencyFromCents(totals.netSalesCents)}</strong></div><div className={selectedMetrics.includes("netQuantity") ? "active" : ""}><span>净销量</span><strong>{formatCount(totals.netQuantity)} 件</strong></div><div className={selectedMetrics.includes("grossMargin") ? "active" : ""}><span>大毛利率</span><strong>{formatRate(totals.grossMarginRate)}</strong></div><small>{granularity === "day" ? "按日" : granularity === "week" ? "按自然周" : "按自然月"}汇总 · {formatCount(points.length)} 个数据点</small></div>
    <div className="product-trend-canvas"><canvas ref={canvasRef} role="img" aria-label={`货品销售趋势，当前显示${selectedMetrics.map((metric) => trendMetricMeta[metric].label).join("、")}`} onPointerMove={handleTrendPointerMove} onPointerLeave={() => setHoveredPointIndex(null)} />{points.length === 0 && <div className="trend-empty">当前统计周期没有可绘制的货品销售数据。</div>}</div>
    {activePoint && <div className="product-trend-detail-area"><div className="product-trend-data-card" aria-live="polite"><small>{activePoint.label} 对应数据</small><div>{(["netSales", "netQuantity", "grossMargin"] as TrendMetric[]).map((metric) => <span key={metric}><i className="trend-data-dot" style={{ background: trendMetricMeta[metric].color }} />{trendMetricMeta[metric].label}<strong>{activePointMetricValue(metric)}</strong></span>)}</div></div></div>}
    {points.length > 0 && <p className="trend-data-hint">移动至每个趋势节点，可查看当前日期的净销售额、净销量和大毛利率。</p>}
  </section>;
}

type SalesDistributionDimension = "shop" | "platform";
type SalesDistributionRow = { name: string; platform: string; shopCount: number; grossSalesCents: number; netSalesCents: number; grossProfitCents: number; refundAmountCents: number; netQuantity: number; refundRate: number };

export function ShopSalesDistribution({ shops }: { shops: SalesChannel[] }) {
  const [dimension, setDimension] = useState<SalesDistributionDimension>("shop");
  const distributionRows = useMemo<SalesDistributionRow[]>(() => {
    if (dimension === "shop") return shops.map((shop) => ({ name: shop.name || "未分类", platform: shop.platform || "未分类平台", shopCount: 1, grossSalesCents: shop.grossSalesCents, netSalesCents: shop.netSalesCents, grossProfitCents: shop.grossProfitCents, refundAmountCents: shop.refundAmountCents, netQuantity: shop.netQuantity, refundRate: shop.refundRate }));
    const grouped = new Map<string, SalesDistributionRow>();
    shops.forEach((shop) => {
      const platform = shop.platform || "未分类平台";
      const row = grouped.get(platform) ?? { name: platform, platform, shopCount: 0, grossSalesCents: 0, netSalesCents: 0, grossProfitCents: 0, refundAmountCents: 0, netQuantity: 0, refundRate: 0 };
      row.shopCount += 1;
      row.grossSalesCents += shop.grossSalesCents;
      row.netSalesCents += shop.netSalesCents;
      row.grossProfitCents += shop.grossProfitCents;
      row.refundAmountCents += shop.refundAmountCents;
      row.netQuantity += shop.netQuantity;
      row.refundRate = row.grossSalesCents === 0 ? 0 : row.refundAmountCents / row.grossSalesCents;
      grouped.set(platform, row);
    });
    return [...grouped.values()];
  }, [dimension, shops]);
  const sortedRows = useMemo(() => [...distributionRows].sort((left, right) => right.netSalesCents - left.netSalesCents), [distributionRows]);
  const maxNetSales = Math.max(1, ...sortedRows.map((item) => Math.max(0, item.netSalesCents)));
  const label = dimension === "shop" ? "店铺" : "平台";
  const countLabel = dimension === "shop" ? "家店铺" : "个平台";
  return <section className="panel shop-sales-distribution"><div className="shop-distribution-header"><div><span className="eyebrow">SALES DISTRIBUTION</span><h2>销售分布</h2><p>可切换店铺或平台维度；净销量已按退货后的销量计算。</p></div><div className="shop-distribution-actions"><div className="segmented shop-distribution-dimension" role="group" aria-label="销售分布维度"><button type="button" className={dimension === "shop" ? "active" : ""} onClick={() => setDimension("shop")}>店铺维度</button><button type="button" className={dimension === "platform" ? "active" : ""} onClick={() => setDimension("platform")}>平台维度</button></div><span className="soft-tag">{formatCount(sortedRows.length)} {countLabel}</span></div></div><div className="shop-distribution-columns"><span>{label}</span><span>净销售额</span><span>净销量</span><span>退货率</span></div><div className="shop-distribution-list">{sortedRows.map((row) => <article key={`${dimension}-${row.platform}-${row.name}`}><div className="shop-distribution-name"><strong title={row.name}>{row.name}</strong><small>{dimension === "shop" ? row.platform : `${formatCount(row.shopCount)} 家店铺`}<i><b style={{ width: `${Math.max(2, Math.max(0, row.netSalesCents) / maxNetSales * 100)}%` }} /></i></small></div><strong>{formatCurrencyFromCents(row.netSalesCents)}</strong><span>{formatCount(row.netQuantity)}</span><em className={row.refundRate > .1 ? "orange-text" : ""}>{formatRate(row.refundRate)}</em></article>)}{sortedRows.length === 0 && <div className="shop-distribution-empty">当前筛选条件没有{label}销售记录。</div>}</div></section>;
}

export function ProductPlatformSalesShare({ platforms }: { platforms: SalesChannel[] }) {
  const rows = useMemo(
    () => [...platforms].sort((left, right) => right.grossSalesCents - left.grossSalesCents),
    [platforms],
  );
  const totalSalesCents = rows.reduce((sum, item) => sum + item.grossSalesCents, 0);

  return <section className="panel product-platform-sales-share">
    <div className="product-platform-share-header"><div><span className="eyebrow">PLATFORM MIX</span><h2>平台销售占比</h2><p>按正向销售额计算；退货额在下方店铺销售分布中单独展示。</p></div><span className="soft-tag">{formatCount(rows.length)} 个平台</span></div>
    <div className="product-platform-share-columns"><span>平台</span><span>销售额</span><span>占比</span></div>
    <div className="product-platform-share-list">{rows.map((row) => {
      const shareRate = totalSalesCents > 0 ? row.grossSalesCents / totalSalesCents : 0;
      return <article key={`${row.platform}-${row.groupKey}`}><div><strong title={row.name}>{row.name || "未分类"}</strong><i><b style={{ width: `${Math.max(2, Math.min(100, shareRate * 100))}%` }} /></i></div><strong>{formatCurrencyFromCents(row.grossSalesCents)}</strong><span>{formatRate(shareRate)}</span></article>;
    })}{rows.length === 0 && <div className="product-platform-share-empty">当前规格在所选周期内没有平台销售记录。</div>}</div>
  </section>;
}

