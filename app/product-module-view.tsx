"use client";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PRODUCT_SUMMARY_QUERY_MAX_LENGTH } from "@/lib/products/query-contract";
import type { ModuleViewKey } from "./shell/navigation-catalog";
import { SearchableMultiSelect, SearchableSelect } from "./ui/searchable-select";
import { InventoryKpiCard, MultiFilterSelect, ProductPlatformSalesShare, ProductSalesTrend, ShopSalesDistribution } from "./module-view-business-ui";
import {
  type SalesRangeLabel,
  type SalesSummaryResponse,
  type ProductSummaryItem,
  snapshotTokenPattern,
  claimSnapshotRestart,
  effectivePageForScope,
  type ProductSummaryResponse,
  type ProductSummaryPageResponse,
  formatCurrency,
  formatCurrencyFromCents,
  formatCount,
  formatRate,
  useDebouncedValue,
  Dot,
  SectionHeader,
} from "./module-view-shared";

type ProductTab = ModuleViewKey<"product">;
type ProductCalculatorInput = { salePrice: number; unitCost: number; feeRate: number; promotionCost: number };
type ProductMarginFilter = "低于35%" | "35%-40%" | "40%-45%" | "45%以上" | "暂无有效毛利率";
type ProductDetailSnapshot = { productCode: string; detail: SalesSummaryResponse };

function ProductDetailView({
  item,
  detail,
  loading,
  error,
  rangeLabel,
  onBack,
  onRetry,
}: {
  item: ProductSummaryItem;
  detail: SalesSummaryResponse | null;
  loading: boolean;
  error: string;
  rangeLabel: string;
  onBack: () => void;
  onRetry: () => void;
}) {
  return <div className="data-refresh-region" aria-busy={loading}>
    <section className="panel product-detail-heading">
      <button type="button" className="product-detail-back" onClick={onBack}>← 返回商品经营</button>
      <div className="product-detail-heading-main"><div className="product-thumb gradient-thumb">{item.productName.slice(0, 1) || "货"}</div><div><span className="eyebrow">SPECIFICATION DETAIL</span><h2 title={item.productName}>{item.productName}</h2><p><b>规格代码：{item.productCode}</b>{item.specification ? ` · ${item.specification}` : " · 默认规格"}</p></div></div>
      <div className="product-detail-heading-meta"><strong>{rangeLabel}</strong><span>{detail ? `${detail.startDate} 至 ${detail.endDate}` : "正在载入统计周期"}</span><small>可通过上方周期切换查看趋势</small></div>
    </section>

    {loading && !detail && <section className="panel data-state product-detail-state" role="status"><span className="state-spinner" /><strong>正在汇总该规格的销售明细</strong><p>正在读取销量、销售额、平台和店铺分布…</p></section>}
    {!loading && !detail && <section className="panel data-state data-state-error product-detail-state" role="alert"><span className="state-symbol">!</span><strong>规格详情加载失败</strong><p>{error || "暂时无法读取该规格的销售明细"}</p><button className="secondary-button" onClick={onRetry}>重新加载</button></section>}

    {detail && <>
      {error && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>详情刷新失败</strong><p>{error}</p></div><button className="row-action" onClick={onRetry}>重试</button></section>}
      <section className="inventory-kpi-grid product-detail-kpi-grid">
        <InventoryKpiCard label={`${rangeLabel}销量`} value={`${formatCount(detail.current.netQuantity)} 件`} note="按净销量统计，已扣除退货数量" tone="blue" icon="量" />
        <InventoryKpiCard label="销售额（GMV）" value={formatCurrencyFromCents(detail.current.grossSalesCents)} note="正向销售金额，未扣除退货" tone="purple" icon="销" />
        <InventoryKpiCard label="销售净额" value={formatCurrencyFromCents(detail.current.netSalesCents)} note={`退货金额 ${formatCurrencyFromCents(detail.current.refundAmountCents)}`} tone="green" icon="净" />
        <InventoryKpiCard label="退货率" value={formatRate(detail.current.refundRate)} note={`实际大毛利率 ${formatRate(detail.current.grossMarginRate)}`} tone="orange" icon="退" />
      </section>
      <section className="product-detail-insights-grid"><ProductSalesTrend daily={detail.daily ?? []} selectedProductCount={1} /><ProductPlatformSalesShare platforms={detail.platforms ?? []} /></section>
      <section className="product-detail-store-section"><ShopSalesDistribution shops={detail.outlets ?? []} /></section>
    </>}
  </div>;
}

export default function ProductView({ range, customStartDate, customEndDate, moduleView, onModuleViewChange }: { range: SalesRangeLabel; customStartDate: string; customEndDate: string; moduleView: ProductTab; onModuleViewChange: (view: ProductTab) => void }) {
  const activeTab = moduleView;
  const [summary, setSummary] = useState<ProductSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [categoryFilters, setCategoryFilters] = useState<string[]>([]);
  const [platformFilters, setPlatformFilters] = useState<string[]>([]);
  const [shopFilters, setShopFilters] = useState<string[]>([]);
  const [marginFilters, setMarginFilters] = useState<ProductMarginFilter[]>([]);
  const [sortBy, setSortBy] = useState("sales");
  const [selectedCode, setSelectedCode] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<ProductSummaryItem | null>(null);
  const [detailProductCode, setDetailProductCode] = useState("");
  const [calculatorOverrides, setCalculatorOverrides] = useState<Record<string, ProductCalculatorInput>>({});
  const [productDetailSnapshot, setProductDetailSnapshot] = useState<ProductDetailSnapshot | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [productPage, setProductPage] = useState(1);
  const [productSummarySnapshotRecoveryKey, setProductSummarySnapshotRecoveryKey] = useState(0);
  const productSummaryGenerationRef = useRef(0);
  const productSummaryControllerRef = useRef<AbortController | null>(null);
  const productSummaryBootstrapKeyRef = useRef("");
  const productSummarySnapshotTokenRef = useRef("");
  const productSummaryRestartedTokensRef = useRef(new Set<string>());
  const productDetailGenerationRef = useRef(0);
  const productDetailControllerRef = useRef<AbortController | null>(null);
  const debouncedProductQuery = useDebouncedValue(query);
  const marginFilterKeys = useMemo(() => marginFilters.map((value) => ({
    "低于35%": "below35",
    "35%-40%": "35to40",
    "40%-45%": "40to45",
    "45%以上": "atLeast45",
    "暂无有效毛利率": "unavailable",
  }[value])), [marginFilters]);
  const productPageScopeKey = useMemo(() => JSON.stringify({
    startDate: customStartDate,
    endDate: customEndDate,
    query: debouncedProductQuery.trim(),
    categories: [...categoryFilters].sort(),
    marginBands: [...marginFilterKeys].sort(),
    platforms: [...platformFilters].sort(),
    shops: [...shopFilters].sort(),
    sortBy,
  }), [categoryFilters, customEndDate, customStartDate, debouncedProductQuery, marginFilterKeys, platformFilters, shopFilters, sortBy]);
  const [committedProductPageScopeKey, setCommittedProductPageScopeKey] = useState(productPageScopeKey);
  const effectiveProductPage = effectivePageForScope(productPage, productPageScopeKey, committedProductPageScopeKey);

  const loadSummary = useCallback(async (forceFull = false) => {
    const bootstrapKey = JSON.stringify({
      startDate: customStartDate,
      endDate: customEndDate,
      query: debouncedProductQuery.trim(),
      categories: [...categoryFilters].sort(),
      marginBands: [...marginFilterKeys].sort(),
      platforms: [...platformFilters].sort(),
      shops: [...shopFilters].sort(),
    });
    const expectedSnapshotToken = productSummarySnapshotTokenRef.current;
    const pageOnly = !forceFull
      && productSummaryBootstrapKeyRef.current === bootstrapKey
      && snapshotTokenPattern.test(expectedSnapshotToken);
    const generation = productSummaryGenerationRef.current + 1;
    productSummaryGenerationRef.current = generation;
    productSummaryControllerRef.current?.abort();
    const controller = new AbortController();
    productSummaryControllerRef.current = controller;
    setLoading(true);
    setError("");
    let recoveringSnapshot = false;
    try {
      const params = new URLSearchParams({ range: "custom", startDate: customStartDate, endDate: customEndDate, page: String(effectiveProductPage), pageSize: "50" });
      if (debouncedProductQuery.trim()) params.set("q", debouncedProductQuery.trim());
      categoryFilters.forEach((category) => params.append("category", category));
      marginFilterKeys.forEach((margin) => params.append("marginBand", margin));
      params.set("sortBy", sortBy === "margin" ? "grossMarginRate" : sortBy === "profit" ? "grossProfitCents" : sortBy === "refund" ? "refundRate" : "netSalesCents");
      params.set("direction", "desc");
      platformFilters.forEach((platform) => params.append("platform", platform));
      shopFilters.forEach((shop) => params.append("shop", shop));
      if (pageOnly) {
        params.set("view", "page");
        params.set("snapshotToken", expectedSnapshotToken);
      }
      const response = await fetch(`/api/products/summary?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as ((ProductSummaryResponse | ProductSummaryPageResponse) & { error?: string; code?: string }) | null;
      const pageSnapshotMismatch = pageOnly && response.ok && payload?.snapshotToken !== expectedSnapshotToken;
      if (pageOnly
        && (response.status === 503 || pageSnapshotMismatch)
        && !controller.signal.aborted
        && generation === productSummaryGenerationRef.current
        && claimSnapshotRestart(productSummaryRestartedTokensRef.current, expectedSnapshotToken)) {
        recoveringSnapshot = true;
        productSummaryBootstrapKeyRef.current = "";
        productSummarySnapshotTokenRef.current = "";
        setSummary(null);
        setSelectedCode("");
        setProductSummarySnapshotRecoveryKey((value) => value + 1);
        return;
      }
      if (!response.ok || !payload) {
        throw new Error(payload?.error || `商品数据读取失败（${response.status}）`);
      }
      if (!snapshotTokenPattern.test(payload.snapshotToken)) throw new Error("商品汇总响应缺少有效数据版本");
      if (pageOnly && (payload.projection !== "page" || !payload.pagination || !Array.isArray(payload.items))) {
        throw new Error("商品分页响应格式不完整");
      }
      if (pageOnly && payload.snapshotToken !== expectedSnapshotToken) {
        throw new Error("商品分页与汇总数据版本不一致，请重新加载");
      }
      if (!pageOnly && (payload.projection !== "full" || !payload.metrics || !payload.filters || !Array.isArray(payload.items))) {
        throw new Error("商品汇总响应格式不完整");
      }
      if (!controller.signal.aborted && generation === productSummaryGenerationRef.current) {
        if (payload.projection === "page") {
          setSummary((current) => current
            && current.snapshotToken === expectedSnapshotToken
            && productSummarySnapshotTokenRef.current === expectedSnapshotToken
            && productSummaryBootstrapKeyRef.current === bootstrapKey
            ? { ...current, sort: payload.sort, pagination: payload.pagination, items: payload.items }
            : current);
        } else {
          productSummaryBootstrapKeyRef.current = bootstrapKey;
          productSummarySnapshotTokenRef.current = payload.snapshotToken;
          setSummary(payload);
        }
        setSelectedCode((current) => payload.items.some((item) => item.productCode === current) ? current : payload.items[0]?.productCode || "");
      }
    } catch (requestError) {
      if (!controller.signal.aborted && generation === productSummaryGenerationRef.current) setError(requestError instanceof Error ? requestError.message : "暂时无法读取商品数据");
    } finally {
      if (!controller.signal.aborted && generation === productSummaryGenerationRef.current) {
        if (!recoveringSnapshot) setLoading(false);
        if (productSummaryControllerRef.current === controller) productSummaryControllerRef.current = null;
      }
    }
  }, [categoryFilters, customEndDate, customStartDate, debouncedProductQuery, effectiveProductPage, marginFilterKeys, platformFilters, shopFilters, sortBy]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSummary(), 0);
    return () => {
      window.clearTimeout(timer);
      productSummaryGenerationRef.current += 1;
      productSummaryControllerRef.current?.abort();
    };
  }, [loadSummary, productSummarySnapshotRecoveryKey]);

  useEffect(() => {
    setCommittedProductPageScopeKey(productPageScopeKey);
    setProductPage((current) => current === 1 ? current : 1);
  }, [productPageScopeKey]);

  const selectedProduct = useMemo(
    () => summary?.items.find((item) => item.productCode === selectedCode) ?? null,
    [selectedCode, summary?.items],
  );
  const selectedDetailItem = useMemo(
    () => summary?.items.find((item) => item.productCode === detailProductCode)
      ?? (detailItem?.productCode === detailProductCode ? detailItem : null),
    [detailItem, detailProductCode, summary?.items],
  );
  const productDetail = productDetailSnapshot?.productCode === detailProductCode
    ? productDetailSnapshot.detail
    : null;
  const detailStartDate = summary?.sync.salesWindowStart ?? "";
  const detailEndDate = summary?.sync.salesThrough ?? "";
  useAiPageDetails("product", {
    period: detailOpen ? (detailStartDate && detailEndDate ? { startDate: detailStartDate, endDate: detailEndDate } : null) : { startDate: customStartDate, endDate: customEndDate },
    filters: detailOpen
      ? { dataset: "sales_summary", productCodes: detailProductCode ? [detailProductCode] : [] }
      : { dataset: "product_performance", query: debouncedProductQuery.trim(), platforms: platformFilters, shops: shopFilters, categories: categoryFilters, marginFilterKeys,
        ...(activeTab === "calculator" && selectedProduct ? { selectedIds: [selectedProduct.productCode] } : {}) },
  });

  const loadProductDetail = useCallback(async () => {
    if (!detailProductCode || !detailStartDate || !detailEndDate) return;
    const requestedProductCode = detailProductCode;
    const requestedStartDate = detailStartDate;
    const requestedEndDate = detailEndDate;
    const generation = productDetailGenerationRef.current + 1;
    productDetailGenerationRef.current = generation;
    productDetailControllerRef.current?.abort();
    const controller = new AbortController();
    productDetailControllerRef.current = controller;
    setDetailLoading(true);
    setDetailError("");
    try {
      const params = new URLSearchParams({
        range: "custom",
        startDate: requestedStartDate,
        endDate: requestedEndDate,
        productCodes: requestedProductCode,
      });
      const response = await fetch(`/api/sales/summary?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (SalesSummaryResponse & { error?: string }) | null;
      if (!response.ok || !payload || !payload.current || !Array.isArray(payload.daily)) {
        throw new Error(payload?.error || `规格详情读取失败（${response.status}）`);
      }
      if (!controller.signal.aborted && generation === productDetailGenerationRef.current) {
        setProductDetailSnapshot({ productCode: requestedProductCode, detail: payload });
      }
    } catch (requestError) {
      if (!controller.signal.aborted && generation === productDetailGenerationRef.current) setDetailError(requestError instanceof Error ? requestError.message : "暂时无法读取规格详情");
    } finally {
      if (!controller.signal.aborted && generation === productDetailGenerationRef.current) {
        setDetailLoading(false);
        if (productDetailControllerRef.current === controller) productDetailControllerRef.current = null;
      }
    }
  }, [detailEndDate, detailProductCode, detailStartDate]);
  useEffect(() => {
    if (!detailOpen || !detailProductCode || !detailStartDate || !detailEndDate) return;
    void loadProductDetail();
    return () => {
      productDetailGenerationRef.current += 1;
      productDetailControllerRef.current?.abort();
    };
  }, [detailEndDate, detailOpen, detailProductCode, detailStartDate, loadProductDetail]);

  useEffect(() => { setDetailOpen(false); }, [activeTab]);

  const calculator = useMemo<ProductCalculatorInput>(() => {
    if (!selectedProduct) return { salePrice: 0, unitCost: 0, feeRate: 0, promotionCost: 0 };
    return calculatorOverrides[selectedProduct.productCode] ?? {
      salePrice: Number(((selectedProduct.averageSalePriceCents ?? 0) / 100).toFixed(2)),
      unitCost: Number(((selectedProduct.averageCostCents ?? 0) / 100).toFixed(2)),
      feeRate: Number(((selectedProduct.observedFeeRate ?? 0) * 100).toFixed(2)),
      promotionCost: 0,
    };
  }, [calculatorOverrides, selectedProduct]);

  const categories = useMemo(
    () => summary?.filters.categories ?? [],
    [summary?.filters.categories],
  );
  const platformOptions = useMemo(
    () => summary?.filters.platforms ?? [],
    [summary?.filters.platforms],
  );
  const shopOptions = useMemo(
    () => (summary?.filters.shops ?? [])
      .filter((outlet) => platformFilters.length === 0 || platformFilters.includes(outlet.platform))
      .map((outlet) => ({ value: outlet.key, label: `${outlet.platform} · ${outlet.shop}` })),
    [platformFilters, summary?.filters.shops],
  );
  useEffect(() => {
    setShopFilters((current) => {
      const next = current.filter((item) => shopOptions.some((option) => option.value === item));
      return next.length === current.length ? current : next;
    });
  }, [shopOptions]);
  const productItems = summary?.items ?? [];
  const multiCodeQueryCount = useMemo(
    () => query.trim().split(/[\s,，;；]+/).filter(Boolean).length,
    [query],
  );
  const rangeLabel = range;
  const appliedScope = useMemo(() => {
    const applied = summary?.filtersApplied;
    if (!applied || (applied.platforms.length === 0 && applied.shops.length === 0)) return "全渠道、全部店铺";
    const shops = applied.shops.map((shop) => `${shop.platform} · ${shop.shop}`);
    return shops.length > 0 ? shops.join("；") : applied.platforms.join("、");
  }, [summary?.filtersApplied]);
  const marginBuckets = summary?.metrics.marginBuckets ?? {
    below35Count: 0,
    between35And40Count: 0,
    between40And45Count: 0,
    atLeast45Count: 0,
  };
  const marginBucketCards: Array<{ filter: ProductMarginFilter; label: string; value: number; note: string; tone: "blue" | "green" | "orange" | "purple"; icon: string }> = [
    { filter: "低于35%", label: "实际大毛利率低于35%", value: marginBuckets.below35Count, note: "包含低毛利与亏损货品", tone: "orange", icon: "<35" },
    { filter: "35%-40%", label: "实际大毛利率35%–40%", value: marginBuckets.between35And40Count, note: "按当前统计周期实际毛利率", tone: "blue", icon: "35" },
    { filter: "40%-45%", label: "实际大毛利率40%–45%", value: marginBuckets.between40And45Count, note: "按当前统计周期实际毛利率", tone: "green", icon: "40" },
    { filter: "45%以上", label: "实际大毛利率45%以上", value: marginBuckets.atLeast45Count, note: "按当前统计周期实际毛利率", tone: "purple", icon: "45" },
  ];

  const estimatedFee = calculator.salePrice * calculator.feeRate / 100;
  const estimatedProfit = calculator.salePrice - calculator.unitCost - estimatedFee - calculator.promotionCost;
  const estimatedMargin = calculator.salePrice > 0 ? estimatedProfit / calculator.salePrice : null;
  const updateCalculator = (field: keyof ProductCalculatorInput, value: number) => {
    if (!selectedProduct) return;
    setCalculatorOverrides((current) => ({
      ...current,
      [selectedProduct.productCode]: { ...calculator, [field]: Math.max(0, Number.isFinite(value) ? value : 0) },
    }));
  };
  const openProductDetail = (productCode: string) => {
    const item = summary?.items.find((candidate) => candidate.productCode === productCode);
    if (!item) return;
    setSelectedCode(productCode);
    setDetailItem(item);
    setDetailProductCode(productCode);
    setDetailLoading(true);
    setDetailError("");
    setDetailOpen(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const changeProductTab = (view: ProductTab) => {
    setDetailOpen(false);
    onModuleViewChange(view);
  };
  const subnav = <div className="subnav product-subnav" role="tablist" aria-label="商品管理子版块"><button type="button" role="tab" aria-selected={activeTab === "overview" && !detailOpen} className={activeTab === "overview" && !detailOpen ? "active" : ""} onClick={() => changeProductTab("overview")}>商品经营</button><button type="button" role="tab" aria-selected={activeTab === "calculator" && !detailOpen} className={activeTab === "calculator" && !detailOpen ? "active" : ""} onClick={() => changeProductTab("calculator")}>毛利测算</button>{detailOpen && <button type="button" role="tab" aria-selected className="active">规格详情</button>}</div>;

  if (loading && !summary) {
    return <>{subnav}<section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在同步商品与毛利数据</strong><p>正在汇总已导入销售明细与最新库存快照…</p></section></>;
  }
  if (!summary) {
    return <>{subnav}<section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>商品数据加载失败</strong><p>{error || "暂时无法读取商品与毛利数据"}</p><button className="secondary-button" onClick={() => void loadSummary(true)}>重新加载</button></section></>;
  }
  if (!summary.hasSales) {
    return <>{subnav}<section className="panel data-state inventory-empty-state"><span className="state-symbol">品</span><strong>还没有可用于毛利测算的销售明细</strong><p>请先在“数据导入”同步销售单明细账。商品价格、成本、费用和实际毛利会随销售数据同步更新。</p></section></>;
  }

  return (
    <>
      {subnav}
      <section className="product-search-hero product-live-hero"><div><span className="eyebrow">商品经营中心</span><h2>商品表现与实际毛利实时汇总</h2><p>全局统计周期 {customStartDate} 至 {customEndDate} · 实际数据 {summary.sync.salesWindowStart} 至 {summary.sync.salesThrough} · 库存快照 {summary.sync.inventoryAsOf ?? "未同步"}</p></div><div className="product-hero-actions"><span className="global-period-badge">{range} · 全局同步</span><button className="secondary-button product-refresh" onClick={() => void loadSummary(true)} disabled={loading}>{loading ? "同步中…" : "↻ 同步数据"}</button></div></section>

      {error && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>数据刷新失败</strong><p>{error}</p></div><button className="row-action" onClick={() => void loadSummary(true)}>重试</button></section>}

      {activeTab === "overview" && !detailOpen ? <>
        <section className="inventory-kpi-grid product-kpi-grid data-refresh-region" aria-busy={loading}>
          {marginBucketCards.map((bucket) => <button type="button" aria-pressed={marginFilters.includes(bucket.filter)} onClick={() => setMarginFilters((current) => current.includes(bucket.filter) ? current.filter((value) => value !== bucket.filter) : [...current, bucket.filter])} className={`inventory-kpi-card summary-filter-card product-margin-kpi ${marginFilters.includes(bucket.filter) ? "active" : ""}`} key={bucket.filter}><div><span>{bucket.label}</span><i className={`inventory-kpi-icon ${bucket.tone}`}>{bucket.icon}</i></div><strong>{formatCount(bucket.value)} 个</strong><p>{bucket.note}</p><span className="summary-filter-hint">{marginFilters.includes(bucket.filter) ? "取消筛选" : "加入筛选 →"}</span></button>)}
        </section>

        <section className="panel product-filter-panel">
          <div className="table-toolbar"><div><h2>商品经营明细</h2><p>已按 {appliedScope} 汇总；净销量已扣除退货。快递费率来自最近一次“SKU累计”全量导入，其余金额与毛利来自订单明细。</p></div><span className="soft-tag">{multiCodeQueryCount > 1 ? `已查询 ${formatCount(multiCodeQueryCount)} 个规格代码 · ` : ""}显示 {formatCount(productItems.length)} / {formatCount(summary.pagination.total)}</span></div>
          <div className="filter-row product-filter-row"><div className="search-box compact product-multi-query">⌕ <textarea rows={1} maxLength={PRODUCT_SUMMARY_QUERY_MAX_LENGTH} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入或粘贴货品规格代码、名称或规格（空格、逗号或换行分隔，最多1000字符）" aria-label="搜索一个或多个货品规格代码、名称、品牌、供应商、规格或品类" /></div><MultiFilterSelect label="品类" allLabel="全部品类" ariaLabel="商品品类" options={categories} selected={categoryFilters} onChange={setCategoryFilters} /><MultiFilterSelect label="平台" allLabel="全部平台" ariaLabel="销售平台" options={platformOptions} selected={platformFilters} onChange={setPlatformFilters} /><MultiFilterSelect label="店铺" allLabel="全部店铺" ariaLabel="销售店铺" options={shopOptions} selected={shopFilters} onChange={setShopFilters} /><SearchableMultiSelect className="filter-select" values={marginFilters} onChange={(values) => setMarginFilters(values as ProductMarginFilter[])} ariaLabel="实际大毛利率区间" allLabel="全部毛利" searchPlaceholder="搜索毛利率区间" options={["低于35%", "35%-40%", "40%-45%", "45%以上", "暂无有效毛利率"].map((value) => ({ value, label: value }))} /><SearchableSelect className="filter-select" value={sortBy} onChange={setSortBy} ariaLabel="排序方式" searchPlaceholder="搜索排序方式" options={[{ value: "sales", label: "按销售净额" }, { value: "profit", label: "按订单毛利" }, { value: "margin", label: "按毛利率" }, { value: "refund", label: "按退货率" }]} /></div>
          <div className="data-table-wrap data-refresh-region" aria-busy={loading}><table className="data-table product-live-table"><thead><tr><th>货品 / 规格代码</th><th>品牌</th><th>供应商</th><th>品类</th><th>{rangeLabel}销量</th><th>销售净额</th><th>均价 / 均成本</th><th>订单毛利</th><th>实际毛利率</th><th>退货率</th><th>快递费率</th><th>操作</th></tr></thead><tbody>
            {productItems.map((item) => { const loss = item.grossProfitCents < 0; return <tr key={item.productCode}><td><div className="product-cell"><span className="product-thumb gradient-thumb">{item.productName.slice(0, 1) || "货"}</span><span><strong title={item.productName}>{item.productName}</strong><small>规格代码：{item.productCode}{item.specification ? ` · ${item.specification}` : " · 默认规格"}</small></span></div></td><td><span className="product-dimension" title={item.brand || "品牌未同步"}>{item.brand || "—"}</span></td><td><span className="product-dimension" title={item.supplierName || "供应商未同步"}>{item.supplierName || "—"}</span></td><td><span className="soft-tag">{item.category}</span></td><td>{formatCount(item.netQuantity)}</td><td><strong>{formatCurrencyFromCents(item.netSalesCents)}</strong></td><td><div className="product-money-pair"><strong>{item.averageSalePriceCents === null ? "—" : formatCurrencyFromCents(item.averageSalePriceCents)}</strong><small>成本 {item.averageCostCents === null ? "—" : formatCurrencyFromCents(item.averageCostCents)}</small></div></td><td className={loss ? "red-text" : "green-text"}><strong>{formatCurrencyFromCents(item.grossProfitCents)}</strong></td><td><span className={`product-margin ${loss ? "loss" : item.grossMarginRate !== null && item.grossMarginRate < 0.35 ? "low" : ""}`}>{item.grossMarginRate === null ? "—" : formatRate(item.grossMarginRate)}</span></td><td className={item.refundRate > 0.1 ? "orange-text" : ""}><strong>{formatRate(item.refundRate)}</strong></td><td className={item.shippingRate !== null && (item.shippingRate < 0 || item.shippingRate > 1) ? "orange-text" : ""}><strong>{item.shippingRate === null ? "—" : formatRate(item.shippingRate)}</strong></td><td><button className="row-action" onClick={() => openProductDetail(item.productCode)}>详情</button></td></tr>; })}
            {productItems.length === 0 && <tr><td colSpan={12}><div className="table-state">没有符合当前筛选条件的商品。</div></td></tr>}
          </tbody></table></div>
          <footer className="jd-sku-pagination"><span>第 {summary.pagination.page} / {Math.max(1, summary.pagination.totalPages)} 页</span><div><button type="button" className="row-action" disabled={loading || summary.pagination.page <= 1} onClick={() => setProductPage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={loading || summary.pagination.page >= Math.max(1, summary.pagination.totalPages)} onClick={() => setProductPage((value) => value + 1)}>下一页</button></div></footer>
        </section>
      </> : detailOpen && selectedDetailItem ? <ProductDetailView item={selectedDetailItem} detail={productDetail} loading={detailLoading} error={detailError} rangeLabel={rangeLabel} onBack={() => setDetailOpen(false)} onRetry={() => void loadProductDetail()} /> : <>
        <section className="product-calculator-grid data-refresh-region" aria-busy={loading}>
          <article className="panel calculator-input-panel"><SectionHeader title="毛利测算" note="默认带入所选商品近期开单均价、成本与费用率，可按活动方案调整" /><div className="calculator-fields"><label><span>选择商品</span><SearchableSelect value={selectedCode} onChange={setSelectedCode} ariaLabel="选择用于测算的商品" searchPlaceholder="搜索商品名称或规格代码" options={summary.items.map((item) => ({ value: item.productCode, label: `${item.productName} · ${item.productCode}`, searchText: `${item.productName} ${item.productCode} ${item.specification}` }))} /></label><label><span>预计成交价（元）</span><input type="number" min={0} step="0.01" value={calculator.salePrice} onChange={(event) => updateCalculator("salePrice", Number(event.target.value))} /></label><label><span>单位成本（元）</span><input type="number" min={0} step="0.01" value={calculator.unitCost} onChange={(event) => updateCalculator("unitCost", Number(event.target.value))} /></label><label><span>平台综合费率（%）</span><input type="number" min={0} step="0.01" value={calculator.feeRate} onChange={(event) => updateCalculator("feeRate", Number(event.target.value))} /></label><label><span>单件促销/履约成本（元）</span><input type="number" min={0} step="0.01" value={calculator.promotionCost} onChange={(event) => updateCalculator("promotionCost", Number(event.target.value))} /></label></div><div className="calculator-source"><Dot tone="blue" /><span>{selectedProduct ? `${selectedProduct.productName} · 最近实际毛利率 ${selectedProduct.grossMarginRate === null ? "—" : formatRate(selectedProduct.grossMarginRate)}` : "请选择商品"}</span></div></article>
          <article className="panel calculator-result-panel"><SectionHeader title="预计单件收益" note="成交价 − 单位成本 − 平台费 − 促销/履约成本" /><div className="calculator-result"><div><span>预计单件毛利</span><strong className={estimatedProfit < 0 ? "red-text" : "green-text"}>{formatCurrency(estimatedProfit)}</strong></div><div><span>预计毛利率</span><strong className={estimatedMargin === null ? "" : estimatedMargin < 0 ? "red-text" : "green-text"}>{estimatedMargin === null ? "—" : formatRate(estimatedMargin)}</strong></div><div><span>预计平台费用</span><strong>{formatCurrency(estimatedFee)}</strong></div></div><div className={`calculator-decision ${estimatedMargin !== null && estimatedMargin < 0 ? "danger" : estimatedMargin !== null && estimatedMargin < 0.2 ? "warning" : "success"}`}><strong>{estimatedMargin === null ? "请输入成交价" : estimatedMargin < 0 ? "该方案预计亏损" : estimatedMargin < 0.2 ? "该方案毛利偏低" : "该方案毛利健康"}</strong><p>{estimatedMargin === null ? "成交价大于 0 后即可得到测算结果。" : `每售出 1 件，预计保留 ${formatCurrency(estimatedProfit)} 毛利。`}</p></div></article>
        </section>
        <section className="panel product-reference-panel data-refresh-region" aria-busy={loading}><SectionHeader title="实际经营参考" note="用于对照测算方案与近期真实订单表现" /><div className="product-reference-grid"><div><span>{rangeLabel}销售净额</span><strong>{selectedProduct ? formatCurrencyFromCents(selectedProduct.netSalesCents) : "—"}</strong></div><div><span>{rangeLabel}订单毛利</span><strong className={selectedProduct && selectedProduct.grossProfitCents < 0 ? "red-text" : "green-text"}>{selectedProduct ? formatCurrencyFromCents(selectedProduct.grossProfitCents) : "—"}</strong></div><div><span>实际平台费用率</span><strong>{selectedProduct?.observedFeeRate === null || !selectedProduct ? "—" : formatRate(selectedProduct.observedFeeRate)}</strong></div><div><span>当前可用库存</span><strong>{selectedProduct?.availableQuantity === null || !selectedProduct ? "未同步" : `${formatCount(selectedProduct.availableQuantity)} 件`}</strong></div></div></section>
      </>}
    </>
  );
}
