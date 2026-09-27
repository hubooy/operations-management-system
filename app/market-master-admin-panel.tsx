"use client";

import { useAiPageDetails } from "./ai-page-context-provider";
/* eslint-disable @next/next/no-img-element -- Market master thumbnails are imported business assets. */

import { useCallback, useEffect, useRef, useState } from "react";

import { invokeLatestRequest } from "@/lib/market/latest-request";
import {
  annotationRequestRetryKind,
  annotationRetryDelayMs,
} from "@/lib/market/annotation-retry";
import {
  canCloseMarketSkuEditor,
  type MarketMasterAdminPanelProps,
} from "./market-master-admin-contract";
import Dialog from "./ui/dialog";

const PRICE_RECOGNITION_REQUEST_TIMEOUT_MS = 110_000;
const PRICE_RECOGNITION_CONCURRENCY = 2;
const PRICE_RECOGNITION_BATCH_SIZE = 1;

async function postPriceRecognitionAction(body: Record<string, unknown>) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), PRICE_RECOGNITION_REQUEST_TIMEOUT_MS);
  try {
    return await fetch("/api/market/master", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) throw new Error("价格识别请求超时，系统将自动刷新任务状态并续跑原任务");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}


type FilterOption = { value: string; count: number; label?: string };
type BrandRecognitionJob = {
  id: string; modelId: string; query: string; category: string; status: "queued" | "running" | "paused" | "failed" | "completed";
  totalCount: number; processedCount: number; remainingCount: number; recognizedCount: number; emptyCount: number;
  batchSize: number; progressBps: number; createdAt: string; startedAt: string | null; updatedAt: string; completedAt: string | null; lastError: string;
};
type MarketMasterWorkspace = {
  masterData: { items: Array<Record<string, string | number | null>>; pagination: { total: number; page: number; pageSize: number; pageCount: number } };
  pendingPrices: { items: Array<Record<string, string | number | null>>; pagination: { total: number; page: number; pageSize: number; pageCount: number } };
  mappings: { items: Array<Record<string, string | number | null>> };
  priceBands: { items: Array<Record<string, unknown>> };
  downloadTasks: Array<Record<string, string | number | null>>;
  downloadConfigs: Array<Record<string, string | number | null>>;
  coverage: Array<Record<string, string | number | null>>;
  imageCache: { total: number; cached: number; failed: number; pending: number };
  categories: FilterOption[];
  subcategories: FilterOption[];
  priceRecognition: { prompts: Array<{ prompt_id: string; category: string; pending_count: number }> };
  brandRecognitionJob: BrandRecognitionJob | null;
  brandSeeds: {
    dictionary: {
      items: Array<Record<string, string | number | null>>;
      counts: { total: number; enabled: number; system: number; manual: number };
    };
    unknown: {
      items: Array<Record<string, string | number | null>>;
      pagination: { total: number; page: number; pageCount: number };
    };
  };
  statusCounts: { total: number; pendingPrices: number; confirmedPrices: number };
  subcategorySettings: {
    category: string; categories: FilterOption[];
    items: Array<{ subcategory: string; sku_count: number; annotation_count: number; status: string; sort_order: number }>;
  };
  audits: Array<Record<string, string | number | null>>;
  error?: string;
};
type AiModelSummary = { id: string; name: string; modelType: "text" | "vision"; modelName: string; status: "enabled" | "disabled"; isDefaultTextModel: boolean };
type MarketMasterDatabasePrimary = Pick<MarketMasterWorkspace, "masterData">;
type MarketMasterDatabaseFilters = Pick<MarketMasterWorkspace, "categories" | "subcategories">;
type MarketMasterDatabaseSecondary = Pick<MarketMasterWorkspace, "pendingPrices" | "imageCache" | "priceRecognition" | "statusCounts">;

const emptyMarketMasterWorkspace = (): MarketMasterWorkspace => ({
  masterData: { items: [], pagination: { total: 0, page: 1, pageSize: 30, pageCount: 1 } },
  pendingPrices: { items: [], pagination: { total: 0, page: 1, pageSize: 20, pageCount: 1 } },
  mappings: { items: [] },
  priceBands: { items: [] },
  downloadTasks: [],
  downloadConfigs: [],
  coverage: [],
  imageCache: { total: 0, cached: 0, failed: 0, pending: 0 },
  categories: [],
  subcategories: [],
  priceRecognition: { prompts: [] },
  brandRecognitionJob: null,
  brandSeeds: {
    dictionary: { items: [], counts: { total: 0, enabled: 0, system: 0, manual: 0 } },
    unknown: { items: [], pagination: { total: 0, page: 1, pageCount: 1 } },
  },
  statusCounts: { total: 0, pendingPrices: 0, confirmedPrices: 0 },
  subcategorySettings: { category: "", categories: [], items: [] },
  audits: [],
});

const money = (cents?: number | null) => cents === null || cents === undefined
  ? "-"
  : new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(cents / 100);
const count = (value = 0) => new Intl.NumberFormat("zh-CN").format(value);
const percent = (bps?: number | null) => bps === null || bps === undefined ? "-" : `${(bps / 100).toFixed(2)}%`;
const marketProductHref = (productUrl: unknown, skuCode: unknown) => {
  const direct = typeof productUrl === "string" ? productUrl.trim() : "";
  if (/^https:\/\//i.test(direct)) return direct;
  const sku = String(skuCode ?? "").trim();
  return /^\d{6,20}$/.test(sku) ? `https://item.jd.com/${sku}.html` : "";
};
const priceSourceLabel = (source: unknown) => ({
  ai_suggestion: "AI 主图识别",
  source_table: "源表参考价（非 AI）",
  average_transaction: "成交均价（非 AI）",
  missing: "暂无候选价",
}[String(source ?? "")] ?? String(source ?? "未知来源"));
const manualMarketPriceLabel = "人工确认市场定位价（元）";
const brandJobEta = (job: BrandRecognitionJob | null) => {
  if (!job?.startedAt || job.processedCount <= 0 || job.remainingCount <= 0) return job?.status === "completed" ? "已完成" : "运行后计算";
  const elapsedSeconds = Math.max(1, (Date.now() - new Date(job.startedAt).getTime()) / 1000);
  const remainingSeconds = Math.round(job.remainingCount / (job.processedCount / elapsedSeconds));
  if (remainingSeconds < 60) return "约 1 分钟内";
  if (remainingSeconds < 3600) return `约 ${Math.ceil(remainingSeconds / 60)} 分钟`;
  return `约 ${(remainingSeconds / 3600).toFixed(1)} 小时`;
};

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
        <i className={values.includes(option.value) ? "checked" : ""} /><span>{option.label ?? option.value}</span><small>{count(option.count)}</small>
      </button>)}{visible.length === 0 && <p>未找到匹配内容</p>}</div>
    </div>}
  </div>;
}

export function MarketMasterAdminPanel({ currentUser, mode = "database" }: MarketMasterAdminPanelProps) {
  const [data, setData] = useState<MarketMasterWorkspace | null>(null);
  const [aiModels, setAiModels] = useState<AiModelSummary[]>([]);
  const [brandModelId, setBrandModelId] = useState("");
  const [brandDrafts, setBrandDrafts] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [masterCategories, setMasterCategories] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [masterPageSize, setMasterPageSize] = useState(30);
  const [databaseView, setDatabaseView] = useState<"cards" | "table">("cards");
  const [rankingDimensions, setRankingDimensions] = useState<string[]>([]);
  const [operationModes, setOperationModes] = useState<string[]>([]);
  const [subcategoryFilters, setSubcategoryFilters] = useState<string[]>([]);
  const [priceStatuses, setPriceStatuses] = useState<string[]>([]);
  const [annotationStatuses, setAnnotationStatuses] = useState<string[]>([]);
  const [visionModelId, setVisionModelId] = useState("");
  const [priceCategory, setPriceCategory] = useState("");
  const [masterCandidatePriceSources, setMasterCandidatePriceSources] = useState<string[]>([]);
  const [pendingPriceSources, setPendingPriceSources] = useState<string[]>([]);
  const [pendingPricePage, setPendingPricePage] = useState(1);
  const [pendingPricePageSize, setPendingPricePageSize] = useState(20);
  const [editingSku, setEditingSku] = useState<Record<string, string | number | null> | null>(null);
  const [skuDraft, setSkuDraft] = useState({ category: "", productName: "", brand: "", operationMode: "POP", subcategory: "", priceYuan: "", priceType: "标准售价" });
  const [subcategoryDrafts, setSubcategoryDrafts] = useState<Record<string, string>>({});
  const [newSubcategory, setNewSubcategory] = useState("");
  const [brandJob, setBrandJob] = useState<BrandRecognitionJob | null>(null);
  const brandRunnerStop = useRef(false);
  const primaryLoadControllerRef = useRef<AbortController | null>(null);
  const primaryLoadGenerationRef = useRef(0);
  const databaseFiltersControllerRef = useRef<AbortController | null>(null);
  const databaseFiltersGenerationRef = useRef(0);
  const secondaryLoadControllerRef = useRef<AbortController | null>(null);
  const secondaryLoadGenerationRef = useRef(0);
  const aiModelsControllerRef = useRef<AbortController | null>(null);
  const aiModelsRequestedRef = useRef(false);
  const lifecycleGenerationRef = useRef(0);
  const latestLoadRef = useRef<() => Promise<void>>(async () => undefined);
  const skuEditorInitialFocusRef = useRef<HTMLInputElement>(null);
  const busyActionRef = useRef("");
  const [busy, setBusy] = useState("");
  const [databaseSecondaryRequested, setDatabaseSecondaryRequested] = useState(false);
  const [databaseSecondaryLoaded, setDatabaseSecondaryLoaded] = useState(false);
  const [databaseSecondaryLoading, setDatabaseSecondaryLoading] = useState(false);
  const [primaryLoading, setPrimaryLoading] = useState(false);
  const [databaseFiltersScope, setDatabaseFiltersScope] = useState<string | null>(null);
  const [databaseFiltersLoading, setDatabaseFiltersLoading] = useState(false);
  const [aiModelsLoading, setAiModelsLoading] = useState(false);
  busyActionRef.current = busy;
  const [error, setError] = useState("");
  const [primaryError, setPrimaryError] = useState("");
  const [databaseFiltersError, setDatabaseFiltersError] = useState("");
  const [secondaryError, setSecondaryError] = useState("");
  const [aiModelsError, setAiModelsError] = useState("");
  const [notice, setNotice] = useState("");
  const isAdmin = currentUser?.role === "admin";
  const skuEditorSaving = !canCloseMarketSkuEditor(busy);
  useAiPageDetails("market", {
    period: null,
    filters: {
      masterSection: mode,
      ...(mode === "database" ? {
        query: query.trim(), categories: masterCategories, rankingDimension: rankingDimensions,
        operationMode: operationModes, subcategories: subcategoryFilters, priceStatuses,
        candidatePriceSources: masterCandidatePriceSources, annotationStatuses,
        ...(databaseSecondaryRequested ? { pendingPriceSources } : {}),
      } : mode === "brand" ? { query: query.trim(), categories: category ? [category] : [] }
        : mode === "subcategory" ? { categories: category ? [category] : [] } : {}),
    },
  });
  const closeSkuEditor = useCallback(() => {
    if (!canCloseMarketSkuEditor(busyActionRef.current)) return;
    setEditingSku(null);
  }, []);
  const load = useCallback(async () => {
    primaryLoadControllerRef.current?.abort();
    const controller = new AbortController();
    primaryLoadControllerRef.current = controller;
    const generation = primaryLoadGenerationRef.current + 1;
    primaryLoadGenerationRef.current = generation;
    setPrimaryLoading(true);
    const params = new URLSearchParams();
    params.set("view", mode === "database" ? "database_primary" : "workspace");
    if (mode !== "database") params.set("section", mode);
    if (query.trim()) params.set("q", query.trim());
    if (mode === "database") masterCategories.forEach((value) => params.append("category", value));
    else if (category) params.set("category", category);
    rankingDimensions.forEach((value) => params.append("rankingDimension", value));
    operationModes.forEach((value) => params.append("operationMode", value));
    subcategoryFilters.forEach((value) => params.append("subcategory", value));
    priceStatuses.forEach((value) => params.append("priceStatus", value));
    masterCandidatePriceSources.forEach((value) => params.append("priceSource", value));
    annotationStatuses.forEach((value) => params.append("annotationStatus", value));
    params.set("page", String(page));
    params.set("pageSize", String(masterPageSize));
    try {
      const response = await fetch(`/api/market/master?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (MarketMasterWorkspace & MarketMasterDatabasePrimary) | null;
      if (controller.signal.aborted || generation !== primaryLoadGenerationRef.current) return;
      if (!response.ok || !payload) throw new Error(payload?.error || "市场主数据读取失败");
      setPrimaryError("");
      if (mode === "database") {
        const primary = payload as MarketMasterDatabasePrimary;
        setData((current) => ({ ...(current ?? emptyMarketMasterWorkspace()), ...primary }));
        setPage(primary.masterData.pagination.page);
        setDatabaseFiltersScope(JSON.stringify([...new Set(masterCategories)].sort()));
        return;
      }
      const workspace = payload as MarketMasterWorkspace;
      setData(workspace);
      setPage(workspace.masterData.pagination.page);
      setBrandJob(workspace.brandRecognitionJob);
      setSubcategoryDrafts(Object.fromEntries(workspace.subcategorySettings.items.map((item) => [String(item.subcategory), String(item.subcategory)])));
    } catch (reason) {
      if (controller.signal.aborted || generation !== primaryLoadGenerationRef.current) return;
      throw reason;
    } finally {
      if (generation === primaryLoadGenerationRef.current) setPrimaryLoading(false);
      if (primaryLoadControllerRef.current === controller) primaryLoadControllerRef.current = null;
    }
  }, [query, category, masterCategories, page, masterPageSize, masterCandidatePriceSources, rankingDimensions, operationModes, subcategoryFilters, priceStatuses, annotationStatuses, mode]);
  const loadDatabaseFilters = useCallback(async () => {
    if (mode !== "database") return;
    const scopeKey = JSON.stringify([...new Set(masterCategories)].sort());
    if (databaseFiltersScope !== scopeKey) return;
    databaseFiltersControllerRef.current?.abort();
    const controller = new AbortController();
    databaseFiltersControllerRef.current = controller;
    const generation = databaseFiltersGenerationRef.current + 1;
    databaseFiltersGenerationRef.current = generation;
    const params = new URLSearchParams({ view: "database_filters" });
    masterCategories.forEach((value) => params.append("category", value));
    setDatabaseFiltersLoading(true);
    try {
      const response = await fetch(`/api/market/master?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (MarketMasterDatabaseFilters & { error?: string }) | null;
      if (controller.signal.aborted || generation !== databaseFiltersGenerationRef.current) return;
      if (!response.ok || !payload) throw new Error(payload?.error || "市场分类筛选项读取失败");
      setDatabaseFiltersError("");
      setData((current) => ({ ...(current ?? emptyMarketMasterWorkspace()), ...payload }));
    } catch (reason) {
      if (controller.signal.aborted || generation !== databaseFiltersGenerationRef.current) return;
      throw reason;
    } finally {
      if (generation === databaseFiltersGenerationRef.current) setDatabaseFiltersLoading(false);
      if (databaseFiltersControllerRef.current === controller) databaseFiltersControllerRef.current = null;
    }
  }, [databaseFiltersScope, masterCategories, mode]);
  const loadDatabaseSecondary = useCallback(async () => {
    if (mode !== "database" || !databaseSecondaryRequested) return;
    secondaryLoadControllerRef.current?.abort();
    const controller = new AbortController();
    secondaryLoadControllerRef.current = controller;
    const generation = secondaryLoadGenerationRef.current + 1;
    secondaryLoadGenerationRef.current = generation;
    const params = new URLSearchParams({
      view: "database_secondary",
      pendingPricePage: String(pendingPricePage),
      pendingPricePageSize: String(pendingPricePageSize),
    });
    masterCategories.forEach((value) => params.append("pendingPriceCategory", value));
    pendingPriceSources.forEach((value) => params.append("pendingPriceSource", value));
    setDatabaseSecondaryLoading(true);
    try {
      const response = await fetch(`/api/market/master?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (MarketMasterDatabaseSecondary & { error?: string }) | null;
      if (controller.signal.aborted || generation !== secondaryLoadGenerationRef.current) return;
      if (!response.ok || !payload) throw new Error(payload?.error || "价格审核数据读取失败");
      setSecondaryError("");
      setData((current) => ({ ...(current ?? emptyMarketMasterWorkspace()), ...payload }));
      setPendingPricePage(payload.pendingPrices.pagination.page);
      setDatabaseSecondaryLoaded(true);
    } catch (reason) {
      if (controller.signal.aborted || generation !== secondaryLoadGenerationRef.current) return;
      throw reason;
    } finally {
      if (generation === secondaryLoadGenerationRef.current) setDatabaseSecondaryLoading(false);
      if (secondaryLoadControllerRef.current === controller) secondaryLoadControllerRef.current = null;
    }
  }, [databaseSecondaryRequested, masterCategories, mode, pendingPricePage, pendingPricePageSize, pendingPriceSources]);
  const reloadLatest = useCallback(async () => {
    await Promise.all([
      load().catch((reason) => setPrimaryError(reason instanceof Error ? reason.message : "市场主数据读取失败")),
      loadDatabaseFilters().catch((reason) => setDatabaseFiltersError(reason instanceof Error ? reason.message : "市场分类筛选项读取失败")),
      loadDatabaseSecondary().catch((reason) => setSecondaryError(reason instanceof Error ? reason.message : "价格审核数据读取失败")),
    ]);
  }, [load, loadDatabaseFilters, loadDatabaseSecondary]);
  latestLoadRef.current = reloadLatest;
  const loadLatest = useCallback(() => invokeLatestRequest(latestLoadRef), []);
  useEffect(() => {
    const timer = window.setTimeout(() => { void load().catch((reason) => setPrimaryError(reason instanceof Error ? reason.message : "市场主数据读取失败")); }, 200);
    return () => {
      window.clearTimeout(timer);
      primaryLoadGenerationRef.current += 1;
      primaryLoadControllerRef.current?.abort();
    };
  }, [load]);
  useEffect(() => {
    if (mode !== "database") return;
    const scopeKey = JSON.stringify([...new Set(masterCategories)].sort());
    if (databaseFiltersScope !== scopeKey) return;
    const timer = window.setTimeout(() => { void loadDatabaseFilters().catch((reason) => setDatabaseFiltersError(reason instanceof Error ? reason.message : "市场分类筛选项读取失败")); }, 50);
    return () => {
      window.clearTimeout(timer);
      databaseFiltersGenerationRef.current += 1;
      databaseFiltersControllerRef.current?.abort();
    };
  }, [databaseFiltersScope, loadDatabaseFilters, masterCategories, mode]);
  useEffect(() => {
    if (!databaseSecondaryRequested || mode !== "database") return;
    setDatabaseSecondaryLoading(true);
    const timer = window.setTimeout(() => { void loadDatabaseSecondary().catch((reason) => setSecondaryError(reason instanceof Error ? reason.message : "价格审核数据读取失败")); }, 200);
    return () => {
      window.clearTimeout(timer);
      secondaryLoadGenerationRef.current += 1;
      secondaryLoadControllerRef.current?.abort();
    };
  }, [databaseSecondaryRequested, loadDatabaseSecondary, mode]);
  useEffect(() => {
    const shouldLoad = isAdmin && (mode === "brand" || (mode === "database" && databaseSecondaryRequested));
    if (!shouldLoad || aiModelsRequestedRef.current) return;
    aiModelsRequestedRef.current = true;
    const controller = new AbortController();
    aiModelsControllerRef.current = controller;
    setAiModelsError("");
    setAiModelsLoading(true);
    void (async () => {
      try {
        const response = await fetch("/api/ai/models", { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as { items?: AiModelSummary[]; error?: string } | null;
        if (controller.signal.aborted) return;
        if (!response.ok) throw new Error(payload?.error || "运营管理系统 AI 算力读取失败");
        const models = payload?.items ?? [];
        setAiModels(models);
        setBrandModelId((current) => current || models.find((item) => item.status === "enabled" && item.modelType === "text" && item.isDefaultTextModel)?.id || models.find((item) => item.status === "enabled" && item.modelType === "text")?.id || "");
        setVisionModelId((current) => current || models.find((item) => item.status === "enabled" && item.modelType === "vision")?.id || "");
      } catch (reason) {
        if (!controller.signal.aborted) setAiModelsError(reason instanceof Error ? reason.message : "运营管理系统 AI 算力读取失败");
      } finally {
        if (!controller.signal.aborted) setAiModelsLoading(false);
      }
    })();
  }, [databaseSecondaryRequested, isAdmin, mode]);
  useEffect(() => {
    const generation = lifecycleGenerationRef.current + 1;
    lifecycleGenerationRef.current = generation;
    return () => {
      queueMicrotask(() => {
        if (generation !== lifecycleGenerationRef.current) return;
        brandRunnerStop.current = true;
        primaryLoadControllerRef.current?.abort();
        databaseFiltersControllerRef.current?.abort();
        secondaryLoadControllerRef.current?.abort();
        aiModelsControllerRef.current?.abort();
      });
    };
  }, []);
  const post = async (body: Record<string, unknown>) => {
    const busyAction = String(body.action ?? "action");
    busyActionRef.current = busyAction;
    setBusy(busyAction); setError(""); setNotice("");
    try {
      const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!response.ok) throw new Error(payload?.error || "市场主数据操作失败");
      setNotice("操作已保存");
      await loadLatest();
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "市场主数据操作失败"); return false; }
    finally { busyActionRef.current = ""; setBusy(""); }
  };
  const brandRowKey = (row: Record<string, string | number | null>) => `${row.category}|${row.scope}|${row.rankingDimension}|${row.skuCode}`;
  const inferBrand = async (row: Record<string, string | number | null>) => {
    if (!brandModelId) { setError("请先在 AI 助理配置中启用一个文本模型"); return; }
    const key = brandRowKey(row);
    setBusy(`infer_brand:${key}`); setError(""); setNotice("");
    try {
      const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "infer_brand", modelId: brandModelId, productName: row.productName }) });
      const payload = await response.json().catch(() => null) as { error?: string; result?: { brand?: string } } | null;
      if (!response.ok) throw new Error(payload?.error || "AI 品牌识别失败");
      const brand = payload?.result?.brand?.trim() ?? "";
      setBrandDrafts((current) => ({ ...current, [key]: brand }));
      setNotice(brand ? `AI 建议品牌：${brand}，请人工确认后保存` : "AI 无法可靠识别，已按要求留空，可手工填写");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "AI 品牌识别失败"); }
    finally { setBusy(""); }
  };
  const confirmBrand = (row: Record<string, string | number | null>) => {
    const key = brandRowKey(row);
    const brand = (brandDrafts[key] ?? String(row.brand ?? "")).trim();
    if (!brand) { setError("品牌为空时保持待识别；如需确认，请先填写品牌"); return; }
    void post({ action: "confirm_brand", category: row.category, scope: row.scope, rankingDimension: row.rankingDimension, skuCode: row.skuCode, brand });
  };
  const editSku = (row: Record<string, string | number | null>) => {
    setEditingSku(row);
    setSkuDraft({
      category: String(row.category ?? ""), productName: String(row.productName ?? ""), brand: String(row.brand ?? ""),
      operationMode: String(row.operationMode ?? "POP"), subcategory: String(row.subcategory ?? ""),
      priceYuan: row.officialMarketPriceCents === null ? (row.candidatePriceCents === null ? "" : String(Number(row.candidatePriceCents) / 100)) : String(Number(row.officialMarketPriceCents) / 100),
      priceType: ["标准售价", "到手价", "券后价", "起售价", "价格区间", "最低规格价格"].includes(String(row.aiPriceType)) ? String(row.aiPriceType) : "标准售价",
    });
  };
  const modifyProductBrand = editSku;
  const confirmPrice = editSku;
  const saveSku = async () => {
    if (!editingSku) return;
    const priceYuan = skuDraft.priceYuan.trim() === "" ? null : Number(skuDraft.priceYuan);
    if (priceYuan !== null && (!Number.isFinite(priceYuan) || priceYuan < 0 || priceYuan > 1_000_000)) { setError(`${manualMarketPriceLabel}必须是 0 到 1,000,000 之间的数字`); return; }
    const saved = await post({ action: "update_sku_master", originalCategory: editingSku.category, category: skuDraft.category, scope: editingSku.scope,
      rankingDimension: editingSku.rankingDimension, skuCode: editingSku.skuCode, month: editingSku.month, productName: skuDraft.productName,
      brand: skuDraft.brand, operationMode: skuDraft.operationMode, subcategory: skuDraft.subcategory,
      imageContentSha256: editingSku.imageContentSha256,
      priceCents: priceYuan === null ? null : Math.round(priceYuan * 100), priceType: skuDraft.priceType });
    if (saved) setEditingSku(null);
  };
  const saveSubcategories = async () => {
    if (!category) { setError("请先选择一个三级类目"); return; }
    const renames = Object.entries(subcategoryDrafts).filter(([source, target]) => target.trim() && source !== target.trim()).map(([source, target]) => ({ source, target: target.trim() }));
    const additions = newSubcategory.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
    const saved = await post({ action: "save_subcategory_settings", category, renames, additions });
    if (saved) setNewSubcategory("");
  };
  const runBrandRecognitionJob = async (jobId: string) => {
    brandRunnerStop.current = false;
    setBusy("recognize_brand_all"); setError("");
    try {
      while (!brandRunnerStop.current) {
        const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "run_brand_recognition_job_batch", jobId }) });
        const payload = await response.json().catch(() => null) as { error?: string; result?: { job?: BrandRecognitionJob | null; done?: boolean; paused?: boolean; waiting?: boolean } } | null;
        if (!response.ok) throw new Error(payload?.error || "批量品牌识别失败");
        const nextJob = payload?.result?.job ?? null;
        if (nextJob) {
          setBrandJob(nextJob);
          setNotice(`品牌识别 ${percent(nextJob.progressBps)}：已处理 ${count(nextJob.processedCount)} / ${count(nextJob.totalCount)}，剩余 ${count(nextJob.remainingCount)}`);
        }
        if (payload?.result?.done || payload?.result?.paused || nextJob?.status === "completed" || nextJob?.status === "paused") break;
        if (payload?.result?.waiting) await new Promise((resolve) => window.setTimeout(resolve, 800));
      }
      if (!brandRunnerStop.current) {
        const latest = await fetch(`/api/market/master?view=brand_job&q=${encodeURIComponent(query.trim())}&category=${encodeURIComponent(category)}`, { cache: "no-store" }).then((response) => response.json()) as BrandRecognitionJob | null;
        setBrandJob(latest);
        if (latest?.status === "completed") setNotice(`全页品牌识别已完成：共处理 ${count(latest.processedCount)} 个商品，生成 ${count(latest.recognizedCount)} 个品牌候选。`);
      }
      await loadLatest();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "批量品牌识别失败"); }
    finally { setBusy(""); }
  };
  const recognizeAllBrands = async () => {
    if (!brandModelId) { setError("请先选择已启用的文本模型"); return; }
    setBusy("recognize_brand_all"); setError(""); setNotice("正在创建可恢复的全页品牌识别任务…");
    try {
      let job = brandJob;
      if (job && ["queued", "running", "paused", "failed"].includes(job.status)) {
        const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "resume_brand_recognition_job", jobId: job.id }) });
        const payload = await response.json().catch(() => null) as { error?: string; result?: BrandRecognitionJob } | null;
        if (!response.ok || !payload?.result) throw new Error(payload?.error || "品牌识别任务恢复失败");
        job = payload.result;
      } else {
        const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "create_brand_recognition_job", modelId: brandModelId, q: query.trim(), category, batchSize: 40 }) });
        const payload = await response.json().catch(() => null) as { error?: string; result?: BrandRecognitionJob } | null;
        if (!response.ok || !payload?.result) throw new Error(payload?.error || "品牌识别任务创建失败");
        job = payload.result;
      }
      setBrandJob(job);
      if (job.status === "completed") { setNotice("当前筛选范围没有待识别商品。"); setBusy(""); return; }
      await runBrandRecognitionJob(job.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "批量品牌识别失败"); setBusy(""); }
  };
  const pauseBrandRecognition = async () => {
    if (!brandJob) return;
    brandRunnerStop.current = true;
    setNotice("正在暂停；当前 40 条完成后停止…");
    try {
      const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "pause_brand_recognition_job", jobId: brandJob.id }) });
      const payload = await response.json().catch(() => null) as { error?: string; result?: BrandRecognitionJob } | null;
      if (!response.ok || !payload?.result) throw new Error(payload?.error || "品牌识别任务暂停失败");
      setBrandJob(payload.result); setNotice(`任务已暂停，进度 ${percent(payload.result.progressBps)}，刷新或关闭页面不会丢失。`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "品牌识别任务暂停失败"); }
    finally { setBusy(""); }
  };
  const confirmAllBrandSuggestions = async () => {
    setBusy("confirm_brand_all"); setError(""); setNotice("正在确认当前筛选下的全部 AI 品牌候选…");
    let confirmed = 0;
    try {
      for (;;) {
        const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "confirm_brand_suggestions_batch", q: query.trim(), category, batchSize: 25 }) });
        const payload = await response.json().catch(() => null) as { error?: string; result?: { confirmed?: number; done?: boolean } } | null;
        if (!response.ok) throw new Error(payload?.error || "批量确认品牌失败");
        confirmed += Number(payload?.result?.confirmed ?? 0);
        setNotice(`已确认 ${count(confirmed)} 个品牌候选`);
        if (payload?.result?.done || !payload?.result?.confirmed) break;
      }
      setNotice(`一键确认完成，共写入 ${count(confirmed)} 个品牌规则。`);
      await loadLatest();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "批量确认品牌失败"); }
    finally { setBusy(""); }
  };
  const refreshBrandSeeds = async () => {
    setBusy("refresh_brand_seeds"); setError(""); setNotice("正在从 ERP、库存、店铺商品和已确认市场品牌刷新系统词典…");
    try {
      const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "refresh_brand_seeds" }) });
      const payload = await response.json().catch(() => null) as { error?: string; result?: { discovered?: number; inserted?: number; refreshed?: number; disabled?: number; manualPreserved?: number } } | null;
      if (!response.ok) throw new Error(payload?.error || "系统品牌刷新失败");
      const result = payload?.result;
      setNotice(`系统品牌刷新完成：发现 ${count(result?.discovered ?? 0)} 个，新增 ${count(result?.inserted ?? 0)} 个，更新 ${count(result?.refreshed ?? 0)} 个，停用失效系统种子 ${count(result?.disabled ?? 0)} 个，保留人工种子 ${count(result?.manualPreserved ?? 0)} 个。`);
      await loadLatest();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "系统品牌刷新失败"); }
    finally { setBusy(""); }
  };
  const upsertBrandSeed = async (row?: Record<string, string | number | null>) => {
    const suggestedBrand = row ? String(row.productName ?? "").split(/[（(\s]/)[0]?.slice(0, 30) ?? "" : "";
    const canonicalBrand = window.prompt("标准品牌名称", suggestedBrand)?.trim() ?? "";
    if (!canonicalBrand) return;
    const seedText = window.prompt("标题中的品牌种子词（可与标准品牌不同）", canonicalBrand)?.trim() ?? "";
    if (!seedText) return;
    setBusy("upsert_brand_seed"); setError(""); setNotice("");
    try {
      const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({
        action: "upsert_brand_seed", canonicalBrand, seedText,
        category: row?.category, scope: row?.scope, rankingDimension: row?.rankingDimension, skuCode: row?.skuCode,
      }) });
      const payload = await response.json().catch(() => null) as { error?: string; result?: { appliedRows?: number } } | null;
      if (!response.ok) throw new Error(payload?.error || "品牌种子保存失败");
      setNotice(`品牌种子“${seedText} → ${canonicalBrand}”已保存${row ? `，当前 SKU 的 ${count(payload?.result?.appliedRows ?? 0)} 条历史记录已补齐` : ""}。`);
      await loadLatest();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "品牌种子保存失败"); }
    finally { setBusy(""); }
  };
  const matchSystemBrandSeeds = async () => {
    setBusy("match_brand_seeds"); setError(""); setNotice("正在按 B店/京东自营前缀规则与 C店/POP任意位置规则匹配未知品牌…");
    try {
      const response = await fetch("/api/market/master", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "match_brand_seeds", category }) });
      const payload = await response.json().catch(() => null) as { error?: string; result?: { scanned?: number; matchedSkuCount?: number; changedRows?: number; remainingSkuCount?: number; prefixMatched?: number; anywhereMatched?: number } } | null;
      if (!response.ok) throw new Error(payload?.error || "系统品牌匹配失败");
      const result = payload?.result;
      setNotice(`系统品牌匹配完成：扫描 ${count(result?.scanned ?? 0)} 个未知 SKU，匹配 ${count(result?.matchedSkuCount ?? 0)} 个（标题前缀 ${count(result?.prefixMatched ?? 0)}、任意位置 ${count(result?.anywhereMatched ?? 0)}），更新 ${count(result?.changedRows ?? 0)} 条历史记录，剩余 ${count(result?.remainingSkuCount ?? 0)} 个。`);
      await loadLatest();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "系统品牌匹配失败"); }
    finally { setBusy(""); }
  };
  const recognizePrices = async () => {
    if (!priceCategory || !visionModelId) { setError("请选择类目，并先在 AI 助理配置中启用一个视觉模型"); return; }
    setBusy("recognize_prices"); setError(""); setNotice("正在创建价格识别任务…");
    try {
      const createResponse = await postPriceRecognitionAction({ action: "create_price_recognition_job", category: priceCategory, modelId: visionModelId, limit: 100 });
      const created = await createResponse.json().catch(() => null) as { error?: string; result?: { id?: string; totalCount?: number } } | null;
      if (!createResponse.ok || !created?.result?.id) throw new Error(created?.error || "价格识别任务创建失败");
      const jobId = created.result.id;
      const total = Number(created.result.totalCount ?? 0);
      let processed = 0;
      let failed = 0;
      let done = false;
      let workerLimit = PRICE_RECOGNITION_CONCURRENCY;
      let blockedUntil = 0;
      let retryFailures = 0;
      let successesSinceFailure = 0;
      let fatalError: unknown = null;
      const refreshRecognitionProgress = loadLatest;
      const waitForRetryWindow = async () => {
        while (!done && Date.now() < blockedUntil) await new Promise<void>((resolve) => window.setTimeout(resolve, Math.min(1_000, blockedUntil - Date.now())));
      };
      const scheduleRetry = (kind: "waiting" | "transient" | "rate_limit", retryAfterMs = 0, failureMessage = "") => {
        workerLimit = 1;
        successesSinceFailure = 0;
        if (kind !== "waiting") retryFailures += 1;
        const delayMs = annotationRetryDelayMs(kind, retryFailures, retryAfterMs);
        blockedUntil = Math.max(blockedUntil, Date.now() + delayMs);
        const seconds = Math.ceil(delayMs / 1_000);
        const cause = failureMessage.trim() ? `（${failureMessage.trim().slice(0, 300)}）` : "";
        setNotice(kind === "waiting"
          ? `已有图片正在识别，系统将在 ${seconds} 秒后自动检查。`
          : kind === "rate_limit"
            ? `模型供应商限流${cause}，已降为单通道，系统将在 ${seconds} 秒后自动续跑。`
            : `模型或网络超时${cause}，已降为单通道，系统将在 ${seconds} 秒后自动刷新并续跑。`);
      };
      const worker = async (workerIndex: number) => {
        while (!done && !fatalError) {
          if (workerIndex >= workerLimit) { await new Promise<void>((resolve) => window.setTimeout(resolve, 1_000)); continue; }
          await waitForRetryWindow();
          if (done || fatalError || workerIndex >= workerLimit) continue;
          let response: Response;
          try {
            response = await postPriceRecognitionAction({ action: "run_price_recognition_batch", jobId, limit: PRICE_RECOGNITION_BATCH_SIZE });
          } catch (reason) {
            const retryKind = annotationRequestRetryKind(reason);
            if (retryKind) {
              scheduleRetry(retryKind);
              await refreshRecognitionProgress().catch(() => undefined);
              continue;
            }
            fatalError = reason; break;
          }
          const payload = await response.json().catch(() => null) as { error?: string; result?: { done?: boolean; waiting?: boolean; processedCount?: number; reusedCount?: number; failedCount?: number; failureKind?: string; failureCode?: string; failureMessage?: string; retryAfterMs?: number } } | null;
          if (!response.ok) {
            const retryKind = annotationRequestRetryKind({ status: response.status, message: payload?.error || "AI 价格识别失败" });
            if (retryKind) { scheduleRetry(retryKind); await refreshRecognitionProgress().catch(() => undefined); continue; }
            fatalError = new Error(payload?.error || "AI 价格识别失败"); break;
          }
          const processedThisCall = Math.max(0, Number(payload?.result?.processedCount ?? 0));
          const reusedThisCall = Math.max(0, Number(payload?.result?.reusedCount ?? 0));
          processed += processedThisCall;
          failed += Math.max(0, Number(payload?.result?.failedCount ?? 0));
          if (payload?.result?.done) done = true;
          if (payload?.result?.waiting) { scheduleRetry("waiting"); await refreshRecognitionProgress().catch(() => undefined); }
          else if (payload?.result?.failureKind === "rate_limit") { scheduleRetry("rate_limit", Number(payload.result.retryAfterMs ?? 0), payload.result.failureMessage); await refreshRecognitionProgress().catch(() => undefined); }
          else if (payload?.result?.failureKind === "transient") { scheduleRetry("transient", Number(payload.result.retryAfterMs ?? 0), payload.result.failureMessage); await refreshRecognitionProgress().catch(() => undefined); }
          else if (payload?.result?.failureKind === "permanent" && payload.result.failureMessage) setNotice(`当前图片识别失败（${payload.result.failureMessage}），系统已记录失败并继续处理其他图片。`);
          else if (!payload?.result?.failureKind && processedThisCall > reusedThisCall) {
            successesSinceFailure += processedThisCall - reusedThisCall;
            if (workerLimit === 1 && successesSinceFailure >= 3) {
              workerLimit = PRICE_RECOGNITION_CONCURRENCY;
              retryFailures = 0;
              setNotice("模型连接已稳定，系统已自动恢复双通道价格识别。");
            } else setNotice(`AI 价格识别 ${Math.min(processed, total)} / ${total}${failed ? `，失败 ${failed}` : ""}`);
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(PRICE_RECOGNITION_CONCURRENCY, total || 1) }, (_, index) => worker(index)));
      if (fatalError) throw fatalError;
      setNotice(`AI 价格识别完成，已处理 ${count(processed)} 次，结果已进入待确认候选价。`);
      await loadLatest();
    } catch (reason) {
      await loadLatest().catch(() => undefined);
      setError(reason instanceof Error ? reason.message : "AI 价格识别失败");
      setNotice("自动识别遇到不可恢复错误，已完成结果仍会保留。请检查模型配置或权限后重试。");
    }
    finally { setBusy(""); }
  };
  const createMapping = (kind: string) => {
    const sourceValue = window.prompt("来源值") ?? "";
    const targetValue = window.prompt("目标值") ?? "";
    if (!sourceValue || !targetValue) return;
    void post({ action: "upsert_mapping", kind, sourceValue, targetValue, status: "published" });
  };
  const editMapping = (row: Record<string, string | number | null>) => {
    const targetValue = window.prompt("新的目标值", String(row.target_value ?? "")) ?? "";
    if (!targetValue.trim()) return;
    void post({
      action: "upsert_mapping",
      id: row.id,
      kind: row.kind,
      category: row.category,
      sourceValue: row.source_value,
      targetValue,
      status: row.status,
      effectiveFrom: row.effective_from,
    });
  };
  const createDownloadConfig = () => {
    const category = window.prompt("类目") ?? "";
    const scope = window.prompt("榜单口径（例如：全部、POP、自营）", "全部") ?? "全部";
    const rankingDimension = window.prompt("榜单维度 SKU/SPU", "SKU") ?? "SKU";
    const monthStart = window.prompt("起始月份 YYYY-MM") ?? "";
    const monthEnd = window.prompt("结束月份 YYYY-MM", monthStart) ?? monthStart;
    if (!category || !monthStart || !monthEnd) return;
    void post({ action: "upsert_download_config", category, scope, rankingDimension, monthStart, monthEnd, status: "enabled" });
  };
  const createPriceBandDraft = () => {
    const category = window.prompt("类目，留空表示全部", "*") ?? "*";
    const raw = window.prompt("价格带配置：label:min-max，每行一条", "0-499:0-50000\n500-999:50000-100000\n1000+:100000-") ?? "";
    const items = raw.split(/\n+/).map((line) => {
      const [label, range] = line.split(":");
      const [min, max] = (range ?? "").split("-");
      return { label: (label ?? "").trim(), minCents: min ? Number(min) : null, maxCents: max ? Number(max) : null };
    }).filter((item) => item.label);
    if (!items.length) return;
    void post({ action: "create_price_band_version", category, items });
  };
  const importDownloadedTask = async (row: Record<string, string | number | null>, file: File) => {
    setBusy("execute_download_task"); setError(""); setNotice("");
    try {
      const form = new FormData();
      form.set("taskId", String(row.id));
      form.set("file", file);
      const response = await fetch("/api/market/master/execute", { method: "POST", body: form });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!response.ok) throw new Error(payload?.error || "榜单文件校验导入失败");
      setNotice("文件已按任务口径校验并导入");
      await loadLatest();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "榜单文件校验导入失败"); }
    finally { setBusy(""); }
  };
  const relevantDatabaseFiltersError = mode === "database" ? databaseFiltersError : "";
  const relevantSecondaryError = mode === "database" && databaseSecondaryRequested ? secondaryError : "";
  const relevantAiModelsError = mode === "brand" || (mode === "database" && databaseSecondaryRequested) ? aiModelsError : "";
  const fatalError = primaryError;
  const visibleError = error || fatalError || relevantDatabaseFiltersError || relevantSecondaryError || relevantAiModelsError;
  if (!data && fatalError) return <section className="panel data-state data-state-error" role="alert"><span className="state-symbol" aria-hidden="true">!</span><strong>TOP SKU 主数据中心加载失败</strong><p>{fatalError}</p><button className="secondary-button" onClick={() => void load().catch((reason) => setPrimaryError(reason instanceof Error ? reason.message : "市场主数据读取失败"))}>重新加载</button></section>;
  if (!data) return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取 TOP SKU 主数据中心</strong></section>;
  const enabledModels = aiModels.filter((item) => item.status === "enabled");
  const textModels = enabledModels.filter((item) => item.modelType === "text");
  const visionModels = enabledModels.filter((item) => item.modelType === "vision");
  const priceRecognitionBlocker = !isAdmin
    ? "仅管理员可以创建价格识别任务。"
    : aiModelsLoading
      ? "正在读取已启用的视觉模型……"
    : !visionModels.length
      ? "当前没有已启用的视觉模型：请先到 AI 助理配置新增并启用视觉模型；文本模型不能代替主图识别。"
      : !priceCategory
        ? "请选择需要识别价格的类目。"
        : "";
  return <section className="settings-market-master-live data-refresh-region" aria-busy={primaryLoading || (mode === "database" && (databaseFiltersLoading || databaseSecondaryLoading))}>
    {(visibleError || notice) && <div className={`market-feedback ${visibleError ? "error" : "success"}`}>{visibleError || notice}{primaryError && <button className="row-action" onClick={() => void load().catch((reason) => setPrimaryError(reason instanceof Error ? reason.message : "市场主数据读取失败"))}>重试主数据</button>}{databaseFiltersError && !primaryError && <button className="row-action" onClick={() => void loadDatabaseFilters().catch((reason) => setDatabaseFiltersError(reason instanceof Error ? reason.message : "市场分类筛选项读取失败"))}>重试筛选项</button>}</div>}
    {mode === "database" && <article className="panel market-master-unified-toolbar"><div className="section-header"><div><h2>SKU 数据库与价格审核</h2><p>待确认价格已合并到 TOP SKU/SPU 主数据；同一张列表可筛选、查看候选价并直接编辑完整 SKU 数据。</p></div><div className="market-view-switch"><button className={databaseView === "cards" ? "active" : ""} onClick={() => setDatabaseView("cards")}>大图</button><button className={databaseView === "table" ? "active" : ""} onClick={() => setDatabaseView("table")}>列表</button></div></div><div className="market-master-filter-grid">
      <input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="搜索 SKU、标题或品牌" />
      <SearchMultiFilter label="三级类目" values={masterCategories} options={data.categories} onChange={(values) => { setMasterCategories(values); setPriceCategory(values.length === 1 ? values[0] : ""); setSubcategoryFilters([]); setPage(1); setPendingPricePage(1); }} />
      <SearchMultiFilter label="细分品类" values={subcategoryFilters} options={data.subcategories} onChange={(values) => { setSubcategoryFilters(values); setPage(1); }} />
      <SearchMultiFilter label="榜单维度" values={rankingDimensions} options={[{ value: "SKU", count: 0 }, { value: "SPU", count: 0 }]} onChange={(values) => { setRankingDimensions(values); setPage(1); }} />
      <SearchMultiFilter label="经营模式" values={operationModes} options={[{ value: "POP", count: 0 }, { value: "自营", count: 0 }, { value: "未知", count: 0 }]} onChange={(values) => { setOperationModes(values); setPage(1); }} />
      <SearchMultiFilter label="价格状态" values={priceStatuses} options={[{ value: "pending", label: "待确认价格", count: 0 }, { value: "confirmed", label: "已确认价格", count: 0 }, { value: "missing", label: "缺少价格", count: 0 }]} onChange={(values) => { setPriceStatuses(values); setPage(1); }} />
      <SearchMultiFilter label="候选价来源" values={masterCandidatePriceSources} options={[{ value: "ai", label: "AI 识别价", count: 0 }, { value: "non_ai", label: "非 AI 识别价", count: 0 }]} onChange={(values) => { setMasterCandidatePriceSources(values); setPage(1); }} />
      <SearchMultiFilter label="入库状态" values={annotationStatuses} options={[{ value: "pending", label: "待入库", count: 0 }, { value: "committed", label: "已入库", count: 0 }]} onChange={(values) => { setAnnotationStatuses(values); setPage(1); }} />
      <select aria-label="SKU 数据库每页条数" value={masterPageSize} onChange={(event) => { setMasterPageSize(Number(event.target.value)); setPage(1); }}><option value={20}>每页 20 条</option><option value={30}>每页 30 条</option><option value={50}>每页 50 条</option><option value={100}>每页 100 条</option></select>
    </div><div className="market-price-recognition-inline"><button className="secondary-button" disabled={databaseSecondaryRequested} onClick={() => { setDatabaseSecondaryLoading(true); setDatabaseSecondaryRequested(true); }}>{databaseSecondaryRequested ? "价格审核已展开" : "展开价格审核与缓存统计"}</button><span>{databaseFiltersLoading ? "当前页已就绪，分类筛选项正在后台补齐；" : "分类筛选项已就绪；"}待确认价格、图片统计和 AI 算力按需读取。</span></div></article>}
    {mode === "database" && <>{databaseSecondaryRequested && (databaseSecondaryLoaded ? <><article className="panel settings-master-overview">
      <div className="settings-master-cards"><div><strong>{count(data.masterData.pagination.total)}</strong><span>主数据</span></div><div><strong>{count(data.pendingPrices.pagination.total)}</strong><span>待确认价格</span></div><div><strong>{count(data.imageCache.cached)} / {count(data.imageCache.total)}</strong><span>图片缓存</span></div><div><strong>{count(data.statusCounts.confirmedPrices)}</strong><span>已确认价格</span></div></div>
    </article>
    <article className="panel data-refresh-region" aria-busy={databaseSecondaryLoading}><div className="section-header"><div><h3>待确认价格</h3><p>本表沿用上方三级类目多选，并可叠加候选价来源多选；AI 识别目标仍保持单类目，避免写入任务范围含糊。</p></div><div className="market-master-toolbar"><select aria-label="AI 价格识别目标类目" value={priceCategory} onChange={(event) => setPriceCategory(event.target.value)}><option value="">AI 识别目标类目</option>{data.priceRecognition.prompts.map((item) => <option key={item.category} value={item.category}>{item.category}（可识别 {count(Number(item.pending_count))}）</option>)}</select><SearchMultiFilter label="待确认价来源" values={pendingPriceSources} options={[{ value: "ai", label: "AI 识别价", count: 0 }, { value: "non_ai", label: "非 AI 识别价", count: 0 }]} onChange={(values) => { setPendingPriceSources(values); setPendingPricePage(1); }} /><select aria-label="每页条数" value={pendingPricePageSize} onChange={(event) => { setPendingPricePageSize(Number(event.target.value)); setPendingPricePage(1); }}><option value={20}>每页 20 条</option><option value={50}>每页 50 条</option><option value={100}>每页 100 条</option></select><select value={visionModelId} onChange={(event) => setVisionModelId(event.target.value)} disabled={!visionModels.length}><option value="">{visionModels.length ? "选择视觉模型" : "暂无已启用视觉模型"}</option>{visionModels.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.modelName}</option>)}</select><button className="primary-button" disabled={Boolean(priceRecognitionBlocker) || !visionModelId || busy !== ""} onClick={() => void recognizePrices()}>{busy === "recognize_prices" ? "AI 识别中…" : "AI 一键识别价格（最多100条）"}</button></div></div>{priceRecognitionBlocker && <p className="market-price-blocker">{priceRecognitionBlocker}</p>}<div className="data-table-wrap"><table className="data-table market-price-review-table"><thead><tr><th>主图</th><th>SKU / 商品链接</th><th>榜单口径</th><th>月份</th><th>候选价</th><th>来源 / AI 依据</th><th>操作</th></tr></thead><tbody>{data.pendingPrices.items.map((row) => { const href = marketProductHref(row.productUrl, row.skuCode); return <tr key={`pending-price-${row.id}`}><td>{href && row.displayImageUrl ? <a href={href} target="_blank" rel="noreferrer"><img className="market-review-image" src={String(row.displayImageUrl)} alt={String(row.productName ?? row.skuCode)} loading="lazy" /></a> : row.displayImageUrl ? <img className="market-review-image" src={String(row.displayImageUrl)} alt="" loading="lazy" /> : <span className="annotation-no-image">无图</span>}</td><td>{href ? <a href={href} target="_blank" rel="noreferrer"><strong>{String(row.skuCode)}</strong><small>{String(row.productName ?? "")}</small></a> : <><strong>{String(row.skuCode)}</strong><small>{String(row.productName ?? "")}</small></>}<code>{String(row.imageContentSha256 ?? "").slice(0, 16)}</code></td><td>{String(row.scope || row.operationMode || "-")}</td><td>{String(row.month)}</td><td>{money(Number(row.candidatePriceCents ?? 0) || null)}</td><td><strong>{priceSourceLabel(row.candidatePriceSource)}</strong>{row.candidatePriceSource === "ai_suggestion" && <small>{String(row.aiPriceType || "待判断")} · 置信度 {percent(row.aiConfidenceBps === null ? null : Number(row.aiConfidenceBps))}<br />{String(row.aiReason || "未返回识别依据")}</small>}</td><td><button className="row-action" disabled={!isAdmin || busy !== ""} onClick={() => confirmPrice(row)}>修改 / 确认价格</button></td></tr>; })}{!data.pendingPrices.items.length && <tr><td colSpan={7}><div className="table-state">当前筛选范围没有待确认价格。</div></td></tr>}</tbody></table></div><div className="market-pagination"><button disabled={pendingPricePage <= 1} onClick={() => setPendingPricePage((current) => Math.max(1, current - 1))}>上一页</button><label>第 <select aria-label="待确认价格页码" value={pendingPricePage} onChange={(event) => setPendingPricePage(Number(event.target.value))}>{Array.from({ length: data.pendingPrices.pagination.pageCount }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select> / {data.pendingPrices.pagination.pageCount} 页</label><span>共 {count(data.pendingPrices.pagination.total)} 条</span><button disabled={pendingPricePage >= data.pendingPrices.pagination.pageCount} onClick={() => setPendingPricePage((current) => Math.min(data.pendingPrices.pagination.pageCount, current + 1))}>下一页</button></div></article>
    </> : <article className="panel data-state" role="status">{databaseSecondaryLoading && <span className="state-spinner" />}<strong>{databaseSecondaryLoading ? "正在按需读取价格审核与图片统计" : "价格审核数据暂未就绪"}</strong>{!databaseSecondaryLoading && <button className="secondary-button" onClick={() => void loadDatabaseSecondary().catch((reason) => setSecondaryError(reason instanceof Error ? reason.message : "价格审核数据读取失败"))}>重试价格审核</button>}</article>)}
    <article className="panel"><div className="section-header"><div><h3>TOP SKU/SPU 数据库</h3><p>卡片完整呈现商品主图、标题、价格与标签，也可切换为带图片和商品链接的表格。</p></div><div className="market-view-switch"><button className={databaseView === "cards" ? "active" : ""} onClick={() => setDatabaseView("cards")}>卡片</button><button className={databaseView === "table" ? "active" : ""} onClick={() => setDatabaseView("table")}>表格</button></div></div>{databaseView === "cards" ? <div className="market-master-product-grid">{data.masterData.items.map((row) => { const href = marketProductHref(row.productUrl, row.skuCode); return <article key={String(row.id)}><a className="market-master-product-image" href={href || undefined} target={href ? "_blank" : undefined} rel={href ? "noreferrer" : undefined}>{row.displayImageUrl ? <img src={String(row.displayImageUrl)} alt={String(row.productName ?? row.skuCode)} loading="lazy" /> : <span>暂无主图</span>}</a><div className="market-master-product-body">{href ? <a href={href} target="_blank" rel="noreferrer"><h4>{String(row.productName || row.skuCode)}</h4></a> : <h4>{String(row.productName || row.skuCode)}</h4>}<strong className="market-master-price">{money(row.officialMarketPriceCents === null ? Number(row.candidatePriceCents ?? 0) || null : Number(row.officialMarketPriceCents))}</strong><div className="market-master-tags"><span>{String(row.category)}</span><span>{String(row.operationMode)}</span><span>{String(row.rankingDimension)}</span><span>{String(row.brand || "待识别品牌")}</span><span>{String(row.priceBand || "待确认价格")}</span></div><small>#{String(row.rank ?? "-")} · {String(row.skuCode)} · {String(row.scope)}</small><footer><button className="row-action" disabled={!isAdmin || busy !== ""} onClick={() => modifyProductBrand(row)}>编辑 SKU 数据</button>{href && <a href={href} target="_blank" rel="noreferrer">商品链接</a>}</footer></div></article>; })}</div> : <div className="data-table-wrap"><table className="data-table market-master-database-table"><thead><tr><th>主图</th><th>商品 / 链接</th><th>维度</th><th>POP/自营</th><th>品牌</th><th>细分类目</th><th>确认价</th><th>价格带</th><th>操作</th></tr></thead><tbody>{data.masterData.items.map((row) => { const href = marketProductHref(row.productUrl, row.skuCode); return <tr key={String(row.id)}><td className="market-master-table-image">{row.displayImageUrl ? (href ? <a href={href} target="_blank" rel="noreferrer"><img src={String(row.displayImageUrl)} alt={String(row.productName ?? row.skuCode)} loading="lazy" /></a> : <img src={String(row.displayImageUrl)} alt={String(row.productName ?? row.skuCode)} loading="lazy" />) : <span className="annotation-no-image">无图</span>}</td><td>{href ? <a href={href} target="_blank" rel="noreferrer"><strong>{String(row.skuCode)}</strong><small>{String(row.productName ?? "")}</small></a> : <><strong>{String(row.skuCode)}</strong><small>{String(row.productName ?? "")}</small></>}{href && <a className="market-master-table-link" href={href} target="_blank" rel="noreferrer">打开商品链接 ↗</a>}</td><td>{String(row.rankingDimension)}</td><td>{String(row.operationMode)}</td><td>{String(row.brand || "待识别")}</td><td>{String(row.subcategory ?? "")}</td><td>{money(row.officialMarketPriceCents === null ? null : Number(row.officialMarketPriceCents))}</td><td>{String(row.priceBand ?? "")}</td><td><button className="row-action" disabled={!isAdmin || busy !== ""} onClick={() => modifyProductBrand(row)}>编辑 SKU 数据</button></td></tr>; })}</tbody></table></div>}<div className="market-pagination"><button disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>上一页</button><span>第 {page} / {data.masterData.pagination.pageCount} 页</span><button disabled={page >= data.masterData.pagination.pageCount} onClick={() => setPage((current) => current + 1)}>下一页</button></div></article></>}
    {mode === "brand" && <><article className="panel market-ai-capacity"><div className="section-header"><div><h2>运营管理系统 AI 算力</h2><p>这里直接读取“AI 助理配置”中已启用的模型，不再维护独立密钥或重复配置。</p></div><select value={brandModelId} onChange={(event) => setBrandModelId(event.target.value)} disabled={!textModels.length}>{textModels.length ? textModels.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.modelName}{item.isDefaultTextModel ? "（默认）" : ""}</option>) : <option value="">暂无已启用文本模型</option>}</select></div><div className="market-ai-model-grid">{enabledModels.map((item) => <div key={item.id}><strong>{item.name}</strong><span>{item.modelType} · {item.modelName}</span><small>{item.isDefaultTextModel ? "默认文本算力" : "已接入系统算力"}</small></div>)}{!enabledModels.length && <p>尚未配置可用模型，请先到 AI 助理配置中新增并测试模型。</p>}</div></article>
    <article className="panel"><div className="section-header"><div><h3>品牌种子词典</h3><p>从 ERP、库存、店铺商品和已确认市场品牌刷新系统品牌；B店/京东自营仅匹配标题前缀，C店/POP可匹配标题任意位置。</p></div><div className="annotation-actions"><button className="secondary-button" disabled={!isAdmin || busy !== ""} onClick={() => void upsertBrandSeed()}>新增品牌种子</button><button className="secondary-button" disabled={!isAdmin || busy !== ""} onClick={() => void refreshBrandSeeds()}>{busy === "refresh_brand_seeds" ? "刷新中…" : "刷新系统品牌"}</button><button className="primary-button" disabled={!isAdmin || busy !== "" || !data.brandSeeds.dictionary.counts.enabled} onClick={() => void matchSystemBrandSeeds()}>{busy === "match_brand_seeds" ? "匹配中…" : "按种子匹配未知 SKU"}</button></div></div><div className="settings-master-cards"><div><strong>{count(data.brandSeeds.dictionary.counts.enabled)}</strong><span>启用种子</span></div><div><strong>{count(data.brandSeeds.dictionary.counts.system)}</strong><span>系统品牌</span></div><div><strong>{count(data.brandSeeds.dictionary.counts.manual)}</strong><span>人工补录</span></div><div><strong>{count(data.brandSeeds.unknown.pagination.total)}</strong><span>未知品牌 SKU</span></div></div><div className="data-table-wrap"><table className="data-table" data-column-filter-scope="none"><thead><tr><th>标准品牌</th><th>种子词</th><th>来源</th><th>状态</th><th>更新时间</th></tr></thead><tbody>{data.brandSeeds.dictionary.items.map((row) => <tr key={String(row.id)}><td><strong>{String(row.canonical_brand)}</strong></td><td>{String(row.seed_text)}</td><td>{row.source === "manual" ? "人工补录" : "系统刷新"}<small>{String(row.source_ref || "")}</small></td><td>{row.status === "enabled" ? "启用" : "停用"}</td><td>{String(row.updated_at || "-")}</td></tr>)}{!data.brandSeeds.dictionary.items.length && <tr><td colSpan={5}><div className="table-state">词典为空，请先刷新系统品牌或新增种子。</div></td></tr>}</tbody></table></div></article>
    <article className="panel"><div className="section-header"><div><h3>未知品牌 SKU 清单</h3><p>这里只列出当前仍未匹配的去重 SKU；补录种子时会立即补齐当前 SKU，之后导入也会自动复用。</p></div><strong>{count(data.brandSeeds.unknown.pagination.total)} 个待处理</strong></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>SKU / 商品标题</th><th>店铺</th><th>匹配规则</th><th>类目</th><th>操作</th></tr></thead><tbody>{data.brandSeeds.unknown.items.map((row) => { const href = marketProductHref(row.productUrl, row.skuCode); return <tr key={`${row.category}-${row.scope}-${row.rankingDimension}-${row.skuCode}`}><td>{href ? <a href={href} target="_blank" rel="noreferrer"><strong>{String(row.skuCode)}</strong><small>{String(row.productName)}</small></a> : <><strong>{String(row.skuCode)}</strong><small>{String(row.productName)}</small></>}</td><td>{String(row.storeName || "-")}<small>{String(row.storeType || row.scope || "-")}</small></td><td>{row.matchPolicy === "title_prefix" ? "B店 / 自营：标题前缀" : "C店 / POP：任意位置"}</td><td>{String(row.category)}</td><td><button className="row-action" disabled={!isAdmin || busy !== ""} onClick={() => void upsertBrandSeed(row)}>补录品牌种子</button></td></tr>})}{!data.brandSeeds.unknown.items.length && <tr><td colSpan={5}><div className="table-state">当前筛选范围没有未知品牌 SKU。</div></td></tr>}</tbody></table></div><div className="market-pagination"><button disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>上一页</button><span>第 {page} / {data.brandSeeds.unknown.pagination.pageCount} 页</span><button disabled={page >= data.brandSeeds.unknown.pagination.pageCount} onClick={() => setPage((current) => current + 1)}>下一页</button></div></article>
    <article className="panel"><div className="section-header market-brand-batch-header"><div><h3>品牌识别与人工确认</h3><p>“所有页”按当前搜索和类目筛选处理去重商品；任务可暂停、恢复，刷新或关闭页面不会丢失已完成批次。</p></div><div className="market-master-toolbar"><select value={category} disabled={busy === "recognize_brand_all"} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">全部类目</option>{data.categories.map((item) => <option key={item.value} value={item.value}>{item.value}（{count(item.count)}）</option>)}</select><input value={query} disabled={busy === "recognize_brand_all"} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="搜索 SKU、标题或品牌" />{busy === "recognize_brand_all" ? <button className="secondary-button" disabled={!isAdmin} onClick={() => void pauseBrandRecognition()}>暂停识别</button> : <button className="primary-button" disabled={!isAdmin || !brandModelId || busy !== ""} onClick={() => void recognizeAllBrands()}>{brandJob && ["queued", "running", "paused", "failed"].includes(brandJob.status) ? "继续识别" : "AI 一键识别品牌（所有页）"}</button>}<button className="secondary-button" disabled={!isAdmin || busy !== ""} onClick={() => void confirmAllBrandSuggestions()}>{busy === "confirm_brand_all" ? "批量确认中…" : "一键确认全部候选"}</button></div></div>{brandJob && <div className="market-brand-job-progress"><header><div><strong>{brandJob.status === "completed" ? "识别完成" : brandJob.status === "paused" ? "已暂停" : brandJob.status === "failed" ? "可重试" : "识别任务进行中"}</strong><small>任务 {brandJob.id.slice(-8)} · 每批 {brandJob.batchSize} 个</small></div><b>{percent(brandJob.progressBps)}</b></header><span><i style={{ width: `${Math.min(100, brandJob.progressBps / 100)}%` }} /></span><div><label><strong>{count(brandJob.processedCount)}</strong><small>已处理</small></label><label><strong>{count(brandJob.remainingCount)}</strong><small>剩余</small></label><label><strong>{count(brandJob.recognizedCount)}</strong><small>识别出品牌</small></label><label><strong>{brandJobEta(brandJob)}</strong><small>预计剩余</small></label></div>{brandJob.lastError && <p>{brandJob.lastError}</p>}</div>}<div className="data-table-wrap"><table className="data-table market-brand-review-table"><thead><tr><th>主图</th><th>商品标题 / 类目</th><th>当前品牌</th><th>AI / 人工品牌</th><th>操作</th></tr></thead><tbody>{data.masterData.items.map((row) => { const key = brandRowKey(row); const href = marketProductHref(row.productUrl, row.skuCode); const suggested = String(row.suggestedBrand || "") || String(row.brand || ""); return <tr key={`${key}-${row.id}`}><td>{row.displayImageUrl ? <img className="market-review-image" src={String(row.displayImageUrl)} alt="" loading="lazy" /> : <span className="annotation-no-image">无图</span>}</td><td>{href ? <a href={href} target="_blank" rel="noreferrer"><strong>{String(row.productName || row.skuCode)}</strong></a> : <strong>{String(row.productName || row.skuCode)}</strong>}<small>{String(row.skuCode)} · 类目：{String(row.category)} · {String(row.scope)}</small></td><td>{String(row.brand || "待识别")}</td><td><input value={brandDrafts[key] ?? suggested} onChange={(event) => setBrandDrafts((current) => ({ ...current, [key]: event.target.value }))} placeholder="识别不了可留空" />{row.brandSuggestionStatus === "ai_pending" && <small className="market-ai-suggestion">AI 候选，待确认</small>}</td><td><div className="annotation-actions"><button className="row-action" disabled={!isAdmin || !brandModelId || busy !== ""} onClick={() => void inferBrand(row)}>{busy === `infer_brand:${key}` ? "识别中…" : "重新识别"}</button><button className="row-action" disabled={!isAdmin || busy !== ""} onClick={() => confirmBrand(row)}>修改 / 确认</button></div></td></tr>; })}</tbody></table></div><div className="market-pagination"><button disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>上一页</button><span>第 {page} / {data.masterData.pagination.pageCount} 页</span><button disabled={page >= data.masterData.pagination.pageCount} onClick={() => setPage((current) => current + 1)}>下一页</button></div></article></>}
    {mode === "mapping" && <article className="panel"><div className="section-header"><div><h3>映射与价格带</h3><p>细分类目、品牌别名、单品品牌确认、POP/自营映射和价格带配置均持久化并审计。</p></div><div className="annotation-actions"><button className="secondary-button" disabled={!isAdmin} onClick={() => createMapping("subcategory")}>新增细分类目映射</button><button className="secondary-button" disabled={!isAdmin} onClick={() => createMapping("brand_alias")}>新增品牌别名</button><button className="secondary-button" disabled={!isAdmin} onClick={() => createMapping("operation_mode")}>新增经营模式规则</button><button className="secondary-button" disabled={!isAdmin || busy !== ""} onClick={() => void post({ action: "apply_mappings" })}>重算并应用映射</button><button className="secondary-button" disabled={!isAdmin} onClick={createPriceBandDraft}>新建价格带版本</button></div></div>
      <div className="data-table-wrap"><table className="data-table" data-column-filter-scope="none"><thead><tr><th>类型</th><th>来源</th><th>目标</th><th>状态</th><th>版本</th><th>操作</th></tr></thead><tbody>{data.mappings.items.map((row) => <tr key={String(row.id)}><td>{String(row.kind)}</td><td>{String(row.source_value)}</td><td>{String(row.target_value)}</td><td>{String(row.status)}</td><td>{String(row.version)}</td><td><button className="row-action" disabled={!isAdmin || busy !== ""} onClick={() => editMapping(row)}>编辑</button></td></tr>)}</tbody></table></div>
      <div className="market-brand-list">{data.priceBands.items.map((row) => <article key={String(row.id)}><label><strong>{String(row.category)} v{String(row.version)}</strong><span>{String(row.status)}</span></label><small>{String(row.effective_from)} · {String(row.note ?? "")}</small><div className="annotation-actions"><button className="row-action" disabled={!isAdmin || busy !== "" || row.status === "published"} onClick={() => void post({ action: "publish_price_band_version", id: row.id })}>发布</button><button className="row-action" disabled={!isAdmin || busy !== "" || row.status !== "archived"} onClick={() => void post({ action: "rollback_price_band_version", targetVersionId: row.id })}>回滚到此版本</button></div></article>)}</div>
    </article>}
    {mode === "data" && <><article className="panel"><div className="section-header"><div><h3>自动下载与导入工作流</h3><p>计算缺失范围、创建或复用下载任务，登录态未验证时保持 waiting_login。</p></div><div className="annotation-actions"><button className="secondary-button" disabled={!isAdmin} onClick={createDownloadConfig}>新增下载配置</button><button className="primary-button" disabled={!isAdmin || busy !== ""} onClick={() => void post({ action: "plan_downloads" })}>计算缺失任务</button></div></div>
      <div className="data-table-wrap"><table className="data-table" data-column-filter-scope="none"><thead><tr><th>类目/口径/月/维度</th><th>状态</th><th>次数</th><th>文件</th><th>错误</th><th>执行</th></tr></thead><tbody>{data.downloadTasks.map((row) => <tr key={String(row.id)}><td>{String(row.category)} · {String(row.scope)} · {String(row.month)} · {String(row.ranking_dimension)}</td><td>{String(row.status)}</td><td>{String(row.attempt_count)}</td><td>{String(row.source_file_name ?? "")}</td><td>{String(row.error_message ?? "")}</td><td><div className="annotation-actions"><label className="row-action">上传并校验导入<input type="file" accept=".xls,.xlsx,.csv" hidden disabled={!isAdmin || busy !== "" || row.status === "imported" || row.status === "published"} onChange={(event) => { const file = event.target.files?.[0]; if (file) void importDownloadedTask(row, file); event.currentTarget.value = ""; }} /></label><button className="row-action" disabled={!isAdmin || busy !== "" || row.status === "imported" || row.status === "published"} onClick={() => void post({ action: "record_download_attempt", taskId: row.id, status: "waiting_login", errorCode: "waiting_login", errorMessage: "等待京东登录验证" })}>等待登录</button></div></td></tr>)}</tbody></table></div>
    </article>
    <article className="panel"><div className="section-header"><div><h3>数据覆盖、图片缓存与审计</h3><p>覆盖检查和完整审计记录来自市场主数据审计表。</p></div></div><div className="settings-master-cards">{data.coverage.slice(0, 8).map((row) => <div key={`${row.category}-${row.scope}-${row.ranking_dimension}`}><strong>{String(row.month_min ?? "-")}~{String(row.month_max ?? "-")}</strong><span>{String(row.category)} · {String(row.scope)} · {String(row.ranking_dimension)} · SKU {String(row.sku_count)}</span></div>)}</div><div className="data-table-wrap"><table className="data-table" data-column-filter-scope="none"><thead><tr><th>时间</th><th>人员</th><th>动作</th><th>对象</th></tr></thead><tbody>{data.audits.map((row) => <tr key={String(row.id)}><td>{String(row.created_at)}</td><td>{String(row.actor_email)}</td><td>{String(row.action)}</td><td>{String(row.entity_type)} · {String(row.entity_id)}</td></tr>)}</tbody></table></div></article>
    </>}
    {mode === "subcategory" && <article className="panel market-subcategory-settings"><div className="section-header"><div><h2>细分品类设置</h2><p>按三级类目维护统一细分品类。保存后会同步刷新榜单、SKU 入库标注和待复核候选，并发布映射供后续导入复用。</p></div><select value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">请选择三级类目</option>{data.subcategorySettings.categories.map((item) => <option key={item.value} value={item.value}>{item.value}（{count(item.count)}）</option>)}</select></div>{category ? <><div className="data-table-wrap"><table className="data-table"><thead><tr><th>当前细分品类</th><th>关联 SKU</th><th>已入库标注</th><th>修改为</th></tr></thead><tbody>{data.subcategorySettings.items.map((item) => <tr key={item.subcategory}><td><strong>{item.subcategory}</strong></td><td>{count(Number(item.sku_count))}</td><td>{count(Number(item.annotation_count))}</td><td><input value={subcategoryDrafts[item.subcategory] ?? item.subcategory} onChange={(event) => setSubcategoryDrafts((current) => ({ ...current, [item.subcategory]: event.target.value }))} /></td></tr>)}{!data.subcategorySettings.items.length && <tr><td colSpan={4}><div className="table-state">该三级类目尚无细分品类，可直接新增。</div></td></tr>}</tbody></table></div><label className="market-subcategory-add"><span>新增细分品类（每行一个）</span><textarea value={newSubcategory} onChange={(event) => setNewSubcategory(event.target.value)} placeholder="例如：台式净饮机&#10;商用直饮机" /></label><div className="annotation-actions"><button className="primary-button" disabled={!isAdmin || busy !== ""} onClick={() => void saveSubcategories()}>{busy === "save_subcategory_settings" ? "刷新关联数据中…" : "保存并刷新全部关联数据"}</button></div></> : <div className="table-state">请先选择三级类目。</div>}</article>}
    {editingSku && <Dialog
      open
      onClose={closeSkuEditor}
      dialogId="market-sku-editor-dialog"
      ariaLabel="编辑 SKU 全部数据"
      className="panel market-sku-editor"
      initialFocusRef={skuEditorInitialFocusRef}
    >
      <div className="section-header"><div><h2>编辑 SKU 全部数据</h2><p>{String(editingSku.skuCode)} · {String(editingSku.scope)} · {String(editingSku.month)}</p></div><button type="button" className="row-action" disabled={skuEditorSaving} onClick={closeSkuEditor} aria-label="关闭 SKU 全部数据编辑">关闭</button></div>
      <div className="market-sku-editor-grid">
        <label><span>三级类目</span><input ref={skuEditorInitialFocusRef} value={skuDraft.category} onChange={(event) => setSkuDraft((current) => ({ ...current, category: event.target.value }))} /></label>
        <label><span>细分品类</span><select value={skuDraft.subcategory} onChange={(event) => setSkuDraft((current) => ({ ...current, subcategory: event.target.value }))}><option value="">未分类</option>{data.subcategories.map((item) => <option key={item.value} value={item.value}>{item.value}</option>)}</select></label>
        <label className="wide"><span>商品标题</span><input value={skuDraft.productName} onChange={(event) => setSkuDraft((current) => ({ ...current, productName: event.target.value }))} /></label>
        <label><span>品牌</span><input value={skuDraft.brand} onChange={(event) => setSkuDraft((current) => ({ ...current, brand: event.target.value }))} /></label>
        <label><span>经营模式</span><select value={skuDraft.operationMode} onChange={(event) => setSkuDraft((current) => ({ ...current, operationMode: event.target.value }))}><option value="POP">POP</option><option value="自营">自营</option><option value="未知">未知</option></select></label>
        <label><span>市场定位价（元）</span><input type="number" min={0} step="0.01" value={skuDraft.priceYuan} onChange={(event) => setSkuDraft((current) => ({ ...current, priceYuan: event.target.value }))} /></label>
        <label><span>价格类型</span><select value={skuDraft.priceType} onChange={(event) => setSkuDraft((current) => ({ ...current, priceType: event.target.value }))}>{["标准售价", "到手价", "券后价", "起售价", "价格区间", "最低规格价格"].map((item) => <option key={item}>{item}</option>)}</select></label>
      </div>
      <footer><span>类目、品牌、经营模式和细分品类会同步更新该 SKU 的关联历史；价格仅更新当前月份。</span><button type="button" className="primary-button" disabled={busy !== ""} onClick={() => void saveSku()}>{skuEditorSaving ? "保存中…" : "保存全部数据"}</button></footer>
    </Dialog>}
  </section>;
}


export default MarketMasterAdminPanel;
