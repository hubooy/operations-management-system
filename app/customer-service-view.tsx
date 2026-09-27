"use client";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { AppCurrentUser, AppNavigate } from "./shell/view-contract";
import CustomerServiceImportCard from "./customer-service-import-card";
import Dialog from "./ui/dialog";
import { SearchableMultiSelect, SearchableSelect } from "./ui/searchable-select";
import { effectivePageForScope } from "./module-view-shared";

type CurrentUser = AppCurrentUser;
type CustomerServiceMessage = { sender: string; sentAt: string; content: string };
type CustomerServiceConversation = {
  id: number; shopName: string; consultedAt: string; customerId: string; customerAlias: string; consultationType: string; agent: string; transferredAgent: string; skillGroup: string; productSku: string; matchedSkuId: string; productSpuId: string; erpProductCode: string; productCategory: string; productName: string; firstResponseAt: string; responseSeconds: number | null; durationMinutes: number | null; customerMessageCount: number | null; agentMessageCount: number | null; satisfaction: string; resolved: string; conversationId: string; matchStatus: "matched" | "session_only" | "chat_only" | "ambiguous"; matchConfidence: "exact" | "time_only" | "review" | "none"; chatStartedAt: string; chatEndedAt: string; chatCustomerAlias: string; messages: CustomerServiceMessage[]; messageTotalCount: number; messageReturnedCount: number; messagesTruncated: boolean; robotScope: "robot_only" | "contains_robot" | "exclude_robot" | ""; problemType: "商品咨询" | "价格优惠" | "物流发货" | "售后维修" | "退换货" | "安装使用" | "发票开票" | "催单改单" | "其他" | ""; conversionStatus: "converted" | "not_converted" | "unknown" | ""; serviceIssues: string; summaryText: string; analysisSource: "ai" | "manual" | ""; analyzedAt: string | null; annotatedAt: string | null; version: number; updatedAt: string;
};
type CustomerServiceData = {
  items: CustomerServiceConversation[]; agents: string[]; shops: string[]; categories: string[]; summary: { total: number; matched: number; sessionOnly: number; chatOnly: number }; pagination: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
};


const formatCount = (value = 0) => new Intl.NumberFormat("zh-CN").format(value);
const formatDateTime = (value?: string | null) => {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
};

function useDebouncedValue<T>(value: T, delay = 260) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [delay, value]);
  return debounced;
}

function customerServiceStatusLabel(status: CustomerServiceConversation["matchStatus"]) {
  return ({ matched: "已匹配", session_only: "缺聊天记录", chat_only: "缺会话记录", ambiguous: "待核对" })[status];
}

const customerProblemTypes = ["商品咨询", "价格优惠", "物流发货", "售后维修", "退换货", "安装使用", "发票开票", "催单改单", "其他"] as const;
const customerRobotOptions = [{ value: "robot_only", label: "仅机器人" }, { value: "contains_robot", label: "包含机器人" }, { value: "exclude_robot", label: "排除机器人" }] as const;
const customerConversionOptions = [{ value: "converted", label: "已转化" }, { value: "not_converted", label: "未转化" }, { value: "unknown", label: "未知" }] as const;

function CustomerServiceView({ customStartDate, customEndDate, currentUser, onNavigate }: { customStartDate: string; customEndDate: string; currentUser: CurrentUser | null; onNavigate: AppNavigate }) {
  const startDate = customStartDate;
  const endDate = customEndDate;
  const [agents, setAgents] = useState<string[]>([]);
  const [shopNames, setShopNames] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [robotScopes, setRobotScopes] = useState<string[]>([]);
  const [problemTypes, setProblemTypes] = useState<string[]>([]);
  const [conversionStatuses, setConversionStatuses] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [skuIds, setSkuIds] = useState("");
  const [spuIds, setSpuIds] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CustomerServiceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [annotationError, setAnnotationError] = useState("");
  const annotationSavingRef = useRef(false);
  const [selected, setSelected] = useState<CustomerServiceConversation | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | "batch" | null>(null);
  const [analysisProgress, setAnalysisProgress] = useState("");
  const [analysisNotice, setAnalysisNotice] = useState("");
  const [analysisReady, setAnalysisReady] = useState<boolean | null>(null);
  const [detailDraft, setDetailDraft] = useState({ serviceIssues: "", summaryText: "" });
  const [detailSaveNotice, setDetailSaveNotice] = useState("");
  const optionsLoadedRef = useRef(false);
  const listControllerRef = useRef<AbortController | null>(null);
  const listGenerationRef = useRef(0);
  const listRequestKeyRef = useRef("");
  const detailControllerRef = useRef<AbortController | null>(null);
  const detailGenerationRef = useRef(0);
  const customerDialogCloseRef = useRef<HTMLButtonElement>(null);
  const debouncedCustomerQuery = useDebouncedValue(query);
  const debouncedSkuIds = useDebouncedValue(skuIds);
  const debouncedSpuIds = useDebouncedValue(spuIds);
  useAiPageDetails("customer_service", {
    period: selected ? null : { startDate, endDate },
    blockedReason: !selected && debouncedCustomerQuery.trim() ? "顾客搜索条件不会转交 AI。请清除该搜索条件，或移除页面上下文后提问。" : undefined,
    filters: selected ? { selectedIds: [String(selected.id)] }
      : { dataset: "customer_service", agents, shops: shopNames, status: statuses, robotScopes, problemTypes, conversionStatuses, categories, skus: debouncedSkuIds.trim(), spus: debouncedSpuIds.trim() },
  });

  const customerListScopeKey = useMemo(() => JSON.stringify({
    agents: [...agents].sort(),
    categories: [...categories].sort(),
    conversionStatuses: [...conversionStatuses].sort(),
    customerQuery: debouncedCustomerQuery.trim(),
    endDate,
    problemTypes: [...problemTypes].sort(),
    robotScopes: [...robotScopes].sort(),
    shopNames: [...shopNames].sort(),
    skuIds: debouncedSkuIds.trim(),
    spuIds: debouncedSpuIds.trim(),
    startDate,
    statuses: [...statuses].sort(),
  }), [agents, categories, conversionStatuses, debouncedCustomerQuery, debouncedSkuIds, debouncedSpuIds, endDate, problemTypes, robotScopes, shopNames, startDate, statuses]);
  const [committedCustomerListScopeKey, setCommittedCustomerListScopeKey] = useState(customerListScopeKey);
  const effectivePage = effectivePageForScope(page, customerListScopeKey, committedCustomerListScopeKey);
  const canAnnotate = currentUser?.role === "operator" || currentUser?.role === "admin";
  const canImport = currentUser?.role === "admin";

  useEffect(() => {
    if (!canAnnotate) return;
    const controller = new AbortController();
    void fetch("/api/customer-service/analyze", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json().catch(() => null) as { configured?: boolean } | null;
        if (!controller.signal.aborted) setAnalysisReady(response.ok && payload?.configured === true);
      })
      .catch(() => { if (!controller.signal.aborted) setAnalysisReady(false); });
    return () => controller.abort();
  }, [canAnnotate]);

  const load = useCallback(async () => {
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    const generation = ++listGenerationRef.current;
    setLoading(true); setError("");
    try {
      const params = new URLSearchParams({ page: String(effectivePage), pageSize: "30" });
      params.set("includeOptions", optionsLoadedRef.current ? "false" : "true");
      if (startDate) params.set("startDate", startDate);
      if (endDate) params.set("endDate", endDate);
      agents.forEach((value) => params.append("agent", value));
      shopNames.forEach((value) => params.append("shopName", value));
      statuses.forEach((value) => params.append("status", value));
      robotScopes.forEach((value) => params.append("robotScope", value));
      problemTypes.forEach((value) => params.append("problemType", value));
      conversionStatuses.forEach((value) => params.append("conversionStatus", value));
      categories.forEach((value) => params.append("category", value));
      if (debouncedCustomerQuery.trim()) params.set("query", debouncedCustomerQuery.trim());
      if (debouncedSkuIds.trim()) params.set("skuIds", debouncedSkuIds.trim());
      if (debouncedSpuIds.trim()) params.set("spuIds", debouncedSpuIds.trim());
      const requestKey = params.toString();
      listRequestKeyRef.current = requestKey;
      const isLatestRequest = () => !controller.signal.aborted
        && listGenerationRef.current === generation
        && listRequestKeyRef.current === requestKey;
      const response = await fetch(`/api/customer-service/conversations?${requestKey}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as CustomerServiceData & { error?: string } | null;
      if (!response.ok || !payload) throw new Error(payload?.error || "读取客服会话失败");
      if (isLatestRequest()) {
        setData((current) => ({
          ...payload,
          agents: payload.agents.length ? payload.agents : current?.agents ?? [],
          shops: payload.shops.length ? payload.shops : current?.shops ?? [],
          categories: payload.categories.length ? payload.categories : current?.categories ?? [],
        }));
        if (payload.agents.length || payload.shops.length || payload.categories.length) optionsLoadedRef.current = true;
      }
    } catch (reason) {
      if (!controller.signal.aborted && listGenerationRef.current === generation) {
        setError(reason instanceof Error ? reason.message : "读取客服会话失败");
      }
    } finally {
      if (!controller.signal.aborted && listGenerationRef.current === generation) setLoading(false);
    }
  }, [agents, categories, conversionStatuses, debouncedCustomerQuery, debouncedSkuIds, debouncedSpuIds, effectivePage, endDate, problemTypes, robotScopes, shopNames, startDate, statuses]);

  const latestLoadRef = useRef(load);
  useEffect(() => { latestLoadRef.current = load; }, [load]);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  useEffect(() => () => {
    listGenerationRef.current += 1;
    listControllerRef.current?.abort();
    detailGenerationRef.current += 1;
    detailControllerRef.current?.abort();
  }, []);
  useEffect(() => {
    setCommittedCustomerListScopeKey(customerListScopeKey);
    setPage((current) => current === 1 ? current : 1);
  }, [customerListScopeKey]);
  const pageCount = Math.max(1, Math.ceil((data?.pagination.total ?? 0) / (data?.pagination.pageSize ?? 30)));

  const openConversation = useCallback(async (id: number, summary?: CustomerServiceConversation, preservedDraft?: { serviceIssues: string; summaryText: string }) => {
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    const generation = ++detailGenerationRef.current;
    setDetailLoadingId(id);
    setError("");
    try {
      const response = await fetch(`/api/customer-service/conversations?id=${id}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as { item?: CustomerServiceConversation; error?: string } | null;
      if (!response.ok || !payload?.item) throw new Error(payload?.error || "读取客服会话详情失败");
      if (!controller.signal.aborted && detailGenerationRef.current === generation) {
        const detail = {
          ...summary,
          ...payload.item,
          matchedSkuId: payload.item.matchedSkuId || summary?.matchedSkuId || "",
          productSpuId: payload.item.productSpuId || summary?.productSpuId || "",
          erpProductCode: payload.item.erpProductCode || summary?.erpProductCode || "",
          productCategory: payload.item.productCategory || summary?.productCategory || "",
        };
        setSelected(detail);
        setDetailDraft(preservedDraft ?? { serviceIssues: detail.serviceIssues, summaryText: detail.summaryText });
        if (!preservedDraft) setDetailSaveNotice("");
      }
    } catch (reason) {
      if (!controller.signal.aborted && detailGenerationRef.current === generation) {
        setError(reason instanceof Error ? reason.message : "读取客服会话详情失败");
      }
    } finally {
      if (!controller.signal.aborted && detailGenerationRef.current === generation) setDetailLoadingId(null);
    }
  }, []);

  const closeConversation = () => {
    if (selected && busyId === selected.id) return;
    detailGenerationRef.current += 1;
    detailControllerRef.current?.abort();
    detailControllerRef.current = null;
    setDetailLoadingId(null);
    setSelected(null);
    setDetailDraft({ serviceIssues: "", summaryText: "" });
    setDetailSaveNotice("");
  };

  const saveAnnotation = async (item: CustomerServiceConversation, patch: Partial<Pick<CustomerServiceConversation, "robotScope" | "problemType" | "conversionStatus" | "serviceIssues" | "summaryText">>) => {
    if (!canAnnotate || busyId !== null || annotationSavingRef.current) return;
    annotationSavingRef.current = true;
    setBusyId(item.id);
    const listGeneration = listGenerationRef.current;
    const next = { ...item, ...patch, analysisSource: "manual" as const };
    setData((current) => current ? { ...current, items: current.items.map((row) => row.id === item.id ? next : row) } : current);
    setSelected((current) => current?.id === item.id ? next : current);
    try {
      const response = await fetch("/api/customer-service/conversations", { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: item.id, expectedVersion: item.version, ...patch }) });
      const payload = await response.json().catch(() => null) as { version?: number; error?: string } | null;
      const nextVersion = payload?.version;
      if (!response.ok || typeof nextVersion !== "number" || !Number.isSafeInteger(nextVersion)) {
        if (response.status === 409) {
          const hadDetailOpen = selected?.id === item.id;
          const preservedDraft = hadDetailOpen ? { ...detailDraft } : undefined;
          await latestLoadRef.current();
          if (hadDetailOpen) await openConversation(item.id, item, preservedDraft);
          throw new Error("该会话已被其他操作更新，数据已刷新；请核对后重新修改。");
        }
        throw new Error(payload?.error || "保存客服标注失败");
      }
      const confirmed = { ...next, version: nextVersion };
      if (listGenerationRef.current === listGeneration) {
        setData((current) => current ? { ...current, items: current.items.map((row) => row.id === item.id ? confirmed : row) } : current);
      } else {
        await latestLoadRef.current();
      }
      setSelected((current) => current?.id === item.id ? { ...current, ...confirmed } : current);
      setAnnotationError("");
    } catch (reason) {
      setAnnotationError(reason instanceof Error ? reason.message : "保存客服标注失败");
      if (!(reason instanceof Error) || !reason.message.includes("已被其他操作更新")) {
        setSelected((current) => current?.id === item.id ? item : current);
        if (listGenerationRef.current === listGeneration) {
          setData((current) => current ? { ...current, items: current.items.map((row) => row.id === item.id ? item : row) } : current);
        }
        await latestLoadRef.current();
      }
    }
    finally { annotationSavingRef.current = false; setBusyId(null); }
  };

  const saveDetailAnnotation = async () => {
    if (!selected || !canAnnotate || busyId !== null) return;
    const item = selected;
    const draft = { ...detailDraft };
    setBusyId(item.id);
    setDetailSaveNotice("");
    try {
      const response = await fetch("/api/customer-service/conversations", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: item.id,
          expectedVersion: item.version,
          serviceIssues: draft.serviceIssues,
          summaryText: draft.summaryText,
        }),
      });
      const payload = await response.json().catch(() => null) as { version?: number; error?: string } | null;
      if (response.status === 409) {
        await load();
        await openConversation(item.id, item, draft);
        setDetailSaveNotice("该会话已被其他操作更新：服务端版本已刷新，你的两项草稿均已保留。请核对后再次保存。");
        return;
      }
      if (!response.ok || typeof payload?.version !== "number" || !Number.isSafeInteger(payload.version)) {
        throw new Error(payload?.error || "保存客服详情标注失败");
      }
      const confirmed = {
        ...item,
        ...draft,
        analysisSource: "manual" as const,
        version: payload.version,
      };
      setData((current) => current ? {
        ...current,
        items: current.items.map((row) => row.id === item.id ? { ...row, ...confirmed, messages: row.messages } : row),
      } : current);
      setSelected((current) => current?.id === item.id ? { ...current, ...confirmed } : current);
      setDetailSaveNotice("详情标注已保存。");
    } catch (reason) {
      setDetailSaveNotice(`${reason instanceof Error ? reason.message : "保存客服详情标注失败"}；草稿仍保留在当前窗口。`);
    } finally {
      setBusyId(null);
    }
  };

  const analyze = async (ids: number[], marker: number | "batch") => {
    if (!ids.length || !canAnnotate) return;
    setBusyId(marker); setError(""); setAnalysisNotice("");
    let analyzedCount = 0;
    let requestedCount = 0;
    let conflictCount = 0;
    let failedCount = 0;
    let incomplete = false;
    try {
      for (let offset = 0; offset < ids.length; offset += 8) {
        const batch = ids.slice(offset, offset + 8);
        setAnalysisProgress(`${Math.min(offset + batch.length, ids.length)}/${ids.length}`);
        const response = await fetch("/api/customer-service/analyze", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ids: batch }) });
        const payload = await response.json().catch(() => null) as { analyzed?: number; requested?: number; conflicts?: number; failed?: number; incomplete?: boolean; results?: Array<{ id: number; status: "updated" | "conflict" | "not_found" | "failed" | "not_returned" }>; error?: string } | null;
        if (!response.ok || !payload || !Array.isArray(payload.results)) throw new Error(payload?.error || "AI 客服分析失败");
        analyzedCount += Number(payload.analyzed ?? 0);
        requestedCount += Number(payload.requested ?? batch.length);
        conflictCount += Number(payload.conflicts ?? payload.results.filter((item) => item.status === "conflict").length);
        failedCount += Number(payload.failed ?? payload.results.filter((item) => ["not_found", "failed", "not_returned"].includes(item.status)).length);
        incomplete ||= payload.incomplete === true;
      }
      await load();
      setAnalysisNotice(incomplete || conflictCount > 0 || failedCount > 0
        ? `AI 分析完成 ${analyzedCount}/${requestedCount}，冲突 ${conflictCount}、失败 ${failedCount}；列表已刷新，请重试未完成项。`
        : `AI 分析已完成 ${analyzedCount}/${requestedCount}，列表已刷新。`);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "AI 客服分析失败";
      if (message.includes("尚未配置可用的文本模型")) setAnalysisReady(false);
      setError(message);
    }
    finally { setBusyId(null); setAnalysisProgress(""); }
  };

  return <section className="customer-service-page">
    <div className="customer-service-heading"><div><span className="eyebrow">网店分析 / 客服分析</span><h2>会话与聊天记录</h2><p>按时间和顾客标识关联会话，支持机器人、问题类型、订单转化、AI 服务质检和小结标注。</p></div><div className="customer-service-heading-actions">{canAnnotate && <button type="button" className="primary-button" onClick={() => void analyze((data?.items ?? []).filter((item) => !item.analyzedAt).map((item) => item.id), "batch")} disabled={analysisReady !== true || busyId !== null || !(data?.items ?? []).some((item) => !item.analyzedAt)}>{busyId === "batch" ? `AI分析中${analysisProgress ? ` ${analysisProgress}` : "…"}` : analysisReady === false ? "请先配置文本模型" : "AI分析本页未标注"}</button>}<button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>{loading ? "刷新中…" : "↻ 刷新数据"}</button></div></div>
    <CustomerServiceImportCard canImport={canImport} onCompleted={load} />
    <section className="customer-service-data-source panel"><strong>客服会话数据</strong><span>可在本页直接导入；「数据导入 → 客服会话」也保留相同入口。</span><div className="customer-service-shop-select"><span>店铺</span><SearchableMultiSelect values={shopNames} onChange={setShopNames} ariaLabel="客服店铺筛选" allLabel="全部店铺" searchPlaceholder="搜索店铺" options={(data?.shops ?? []).map((value) => ({ value, label: value }))} /></div></section>
    {analysisNotice && <section className="customer-service-feedback" role="status">{analysisNotice}</section>}
    {(annotationError || error) && <section className="customer-service-feedback error" role="alert">{[annotationError, error].filter(Boolean).join("；")}</section>}
    {canAnnotate && analysisReady === false && <section className="customer-service-feedback error customer-service-analysis-setup" role="status"><span>客服会话已导入；AI 标注尚缺文本模型。配置并测试成功后即可分批分析本页全部未标注记录。</span><button type="button" className="row-action" onClick={() => onNavigate("ai")}>前往 AI 助理配置</button></section>}
    <section className="customer-service-filters panel">
      <div className="global-period-context customer-global-period"><span>全局统计周期</span><strong>{startDate} 至 {endDate}</strong></div>
      <label><span>客服</span><SearchableMultiSelect values={agents} onChange={setAgents} ariaLabel="客服筛选" allLabel="全部客服" searchPlaceholder="搜索客服" options={(data?.agents ?? []).map((value) => ({ value, label: value }))} /></label>
      <label><span>匹配状态</span><SearchableMultiSelect values={statuses} onChange={setStatuses} ariaLabel="匹配状态筛选" allLabel="全部状态" searchPlaceholder="搜索匹配状态" options={[{ value: "matched", label: "已匹配" }, { value: "session_only", label: "缺聊天记录" }, { value: "chat_only", label: "缺会话记录" }]} /></label>
      <label><span>机器人</span><SearchableMultiSelect values={robotScopes} onChange={setRobotScopes} ariaLabel="机器人内容筛选" allLabel="全部" searchPlaceholder="搜索机器人标注" options={[...customerRobotOptions]} /></label>
      <label><span>问题类型</span><SearchableMultiSelect values={problemTypes} onChange={setProblemTypes} ariaLabel="问题类型筛选" allLabel="全部" searchPlaceholder="搜索问题类型" options={customerProblemTypes.map((value) => ({ value, label: value }))} /></label>
      <label><span>吉客云类目</span><SearchableMultiSelect values={categories} onChange={setCategories} ariaLabel="吉客云类目筛选" allLabel="全部类目" searchPlaceholder="搜索吉客云类目" options={(data?.categories ?? []).map((value) => ({ value, label: value }))} /></label>
      <label><span>订单转化</span><SearchableMultiSelect values={conversionStatuses} onChange={setConversionStatuses} ariaLabel="订单转化筛选" allLabel="全部" searchPlaceholder="搜索转化状态" options={[...customerConversionOptions]} /></label>
      <label className="customer-service-id-search"><span>SKU ID（可多项）</span><input value={skuIds} onChange={(event) => setSkuIds(event.target.value)} placeholder="逗号、空格或换行分隔" /></label>
      <label className="customer-service-id-search"><span>SPU ID（可多项）</span><input value={spuIds} onChange={(event) => setSpuIds(event.target.value)} placeholder="逗号、空格或换行分隔" /></label>
      <label className="customer-service-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索顾客、客服、商品、服务问题或小结" /></label>
    </section>
    <section className="panel table-panel customer-service-table-panel data-refresh-region" aria-busy={loading}><div className="section-header"><div><h2>会话列表</h2><p>正向按 SKUID → 商家SKU → 吉客云网店规格编码匹配；未命中时从吉客云网店规格编码唯一反查 SKUID，最终展示 SKUID、吉客云货品编号与品类。</p></div><span className="soft-tag">{formatCount(data?.pagination.total ?? 0)} 条</span></div><div className="data-table-wrap"><table className="data-table customer-service-table customer-service-analysis-table"><thead><tr><th>时间 / 顾客</th><th>客服</th><th>SKUID / 吉客云编号</th><th>吉客云类目</th><th>消息数</th><th>机器人内容</th><th>问题类型</th><th>订单转化</th><th>AI服务问题 / 小结</th><th>匹配状态</th><th aria-label="操作" /></tr></thead><tbody>
      {loading && !data && <tr><td colSpan={11}><div className="table-state"><span className="state-spinner" />正在读取客服会话…</div></td></tr>}
      {!loading && error && !data && <tr><td colSpan={11}><div className="table-state table-state-error">{error}</div></td></tr>}
      {!loading && !error && data?.items.length === 0 && <tr><td colSpan={11}><div className="table-state">暂无会话记录。请在数据导入模块完成客服会话导入。</div></td></tr>}
      {data?.items.map((item) => <tr key={item.id}>
        <td><div className="customer-time"><small>{item.consultedAt}</small><strong>{item.customerId || item.chatCustomerAlias || "未知顾客"}</strong><small>{item.shopName}</small></div></td>
        <td><strong>{item.agent || "—"}</strong><small>{item.skillGroup || item.transferredAgent || ""}</small></td>
        <td><strong>{item.matchedSkuId ? `SKUID ${item.matchedSkuId}` : item.productSku || "—"}</strong><small>{item.productSpuId ? `SPU ${item.productSpuId}` : item.productName || "未关联商品"}</small>{item.erpProductCode && <small>吉客云编号 {item.erpProductCode}</small>}{item.matchedSkuId && item.productSku !== item.matchedSkuId && <small>会话规格 {item.productSku}</small>}</td>
        <td><span className="customer-category" title={item.productCategory}>{item.productCategory || "未匹配类目"}</span></td>
        <td><strong>{item.messageTotalCount}</strong><small>会话消息总数</small></td>
        <td><SearchableSelect className="customer-annotation-select" value={item.robotScope} disabled={!canAnnotate || busyId !== null} ariaLabel={`${item.id}机器人内容`} searchPlaceholder="搜索机器人标注" options={[{ value: "", label: "待标注", disabled: true }, ...customerRobotOptions]} onChange={(value) => void saveAnnotation(item, { robotScope: value as CustomerServiceConversation["robotScope"] })} /></td>
        <td><SearchableSelect className="customer-annotation-select" value={item.problemType} disabled={!canAnnotate || busyId !== null} ariaLabel={`${item.id}问题类型`} searchPlaceholder="搜索问题类型" options={[{ value: "", label: "待标注", disabled: true }, ...customerProblemTypes.map((value) => ({ value, label: value }))]} onChange={(value) => void saveAnnotation(item, { problemType: value as CustomerServiceConversation["problemType"] })} /></td>
        <td><SearchableSelect className="customer-annotation-select" value={item.conversionStatus} disabled={!canAnnotate || busyId !== null} ariaLabel={`${item.id}订单转化`} searchPlaceholder="搜索转化状态" options={[{ value: "", label: "待标注", disabled: true }, ...customerConversionOptions]} onChange={(value) => void saveAnnotation(item, { conversionStatus: value as CustomerServiceConversation["conversionStatus"] })} /></td>
        <td><div className="customer-ai-summary"><strong title={item.serviceIssues}>{item.serviceIssues || "待 AI 分析服务问题"}</strong><small title={item.summaryText}>{item.summaryText || "暂无小结"}</small>{item.analyzedAt && <em>AI · {formatDateTime(item.analyzedAt)}</em>}</div></td>
        <td><span className={`customer-match customer-match-${item.matchStatus}`}>{customerServiceStatusLabel(item.matchStatus)}<small>{item.matchConfidence === "exact" ? "时间 + 顾客" : item.matchConfidence === "time_only" ? "仅时间" : "待补充"}</small></span></td>
        <td><div className="customer-row-actions">{canAnnotate && <button type="button" className="row-action" disabled={busyId !== null} onClick={() => void analyze([item.id], item.id)}>{busyId === item.id ? "分析中…" : "AI分析"}</button>}<button type="button" className="row-action" disabled={detailLoadingId !== null || busyId !== null} onClick={() => void openConversation(item.id, item)}>{detailLoadingId === item.id ? "读取中…" : item.messageTotalCount > 0 ? "查看会话" : "查看详情"}</button></div></td>
      </tr>)}
    </tbody></table></div>{pageCount > 1 && <div className="customer-service-pagination"><button type="button" className="row-action" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>第 {page} / {pageCount} 页</span><button type="button" className="row-action" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</button></div>}</section>
    <Dialog
      open={Boolean(selected)}
      onClose={closeConversation}
      dialogId="customer-service-conversation-detail"
      ariaLabel="客服会话详情"
      className="customer-transcript"
      initialFocusRef={customerDialogCloseRef}
    >
      {selected && <>
        <header><div><span>{selected.consultedAt}</span><h3>{selected.customerId || selected.chatCustomerAlias || "未知顾客"} · {selected.agent || "未识别客服"}</h3><small>{selected.matchedSkuId ? `SKUID ${selected.matchedSkuId}` : selected.productSku ? `商品规格 ${selected.productSku}` : "未关联商品"} · {customerServiceStatusLabel(selected.matchStatus)}</small></div><button ref={customerDialogCloseRef} type="button" onClick={closeConversation} disabled={busyId === selected.id} aria-label="关闭">×</button></header>
        <div>
          <div className="customer-transcript-metrics"><span>咨询类型：{selected.consultationType || "—"}</span><span>吉客云编号：{selected.erpProductCode || "未匹配"}</span><span>吉客云类目：{selected.productCategory || "未匹配"}</span><span>响应：{selected.responseSeconds === null ? "—" : `${selected.responseSeconds}s`}</span><span>时长：{selected.durationMinutes === null ? "—" : `${selected.durationMinutes} 分钟`}</span></div>
          {selected.messagesTruncated && <div className="customer-service-feedback error" role="status">会话共 {selected.messageTotalCount} 条消息，详情受 200 条 / 64KB 上限保护，本次展示 {selected.messageReturnedCount} 条。</div>}
          {detailSaveNotice && <div className={`customer-service-feedback ${detailSaveNotice === "详情标注已保存。" ? "" : "error"}`} role="status">{detailSaveNotice}</div>}
        </div>
        <div className="customer-analysis-editor">
          <label><span>服务问题</span><textarea value={detailDraft.serviceIssues} disabled={!canAnnotate || busyId === selected.id} onChange={(event) => setDetailDraft((current) => ({ ...current, serviceIssues: event.target.value }))} placeholder="AI 分析或人工补充客服服务问题" /></label>
          <label><span>会话小结</span><textarea value={detailDraft.summaryText} disabled={!canAnnotate || busyId === selected.id} onChange={(event) => setDetailDraft((current) => ({ ...current, summaryText: event.target.value }))} placeholder="概括顾客诉求、客服处理与结果" /></label>
          {canAnnotate && <button type="button" className="primary-button" onClick={() => void saveDetailAnnotation()} disabled={busyId !== null || (detailDraft.serviceIssues === selected.serviceIssues && detailDraft.summaryText === selected.summaryText)}>{busyId === selected.id ? "保存中…" : "保存详情标注"}</button>}
        </div>
        <div className="customer-transcript-messages">{selected.messages.length ? selected.messages.map((message, index) => <article key={`${message.sentAt}-${index}`} className={message.sender === selected.agent ? "agent" : "customer"}><strong>{message.sender || "未知"}</strong><small>{message.sentAt}</small><p>{message.content || "（无文字内容）"}</p></article>) : <p className="soft-text">此会话未匹配到聊天记录；会话表中的结构化字段仍已完整导入。</p>}</div>
      </>}
    </Dialog>
  </section>;
}


export default CustomerServiceView;
