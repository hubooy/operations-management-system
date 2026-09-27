"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import AiBusinessBudgetBuilder from "./ai-business-budget-builder";
import AiBusinessMappingBuilder from "./ai-business-mapping-builder";
import AiBusinessScopePicker from "./ai-business-scope-picker";
import AiBusinessMarketPicker from "./ai-business-market-picker";
import AiBusinessMarketV2Preview from "./ai-business-market-v2-preview";
import AiBusinessSalesPicker from "./ai-business-sales-picker";
import { mappingBindingKey, type MappingSelection } from "@/lib/ai/business-mapping-builder";
import { mergeNetshopOption, type NetshopOptionSelection } from "@/lib/ai/business-scope-options";
import { mergeMarketOption, type MarketOptionSelection } from "@/lib/ai/business-market-options";
import { mergeSalesOption, type SalesOptionSelection } from "@/lib/ai/business-sales-options";
import { getOperationsBusinessDates } from "@/lib/ai/business-time";
import { suggestBusinessQuestionScope, type QuestionSourceScalar, type QuestionSuggestion } from "@/lib/ai/business-question-suggestions";
import { validatePromotionChoices, promotionSelectionPayload, type PromotionChoice, type PromotionSelection } from "@/lib/ai/business-promotion-choices";
import "./ai-business-workbench.css";

type Shop = { platform: string; shop: string; datasets: string[]; salesChannels: string[] };
type Market = { platform: string; category: string; scope: string; rankingDimension: string; priceBandFilter: string };
type Request = { question: string; startDate: string; endDate: string; shops: Shop[]; windows: string[]; markets: Market[] };
type AnalysisMode = "legacy" | "screening-v1" | "screening-promotion-v1";
type Source = { key: string; domain: string; query: Record<string, string> };
type Preview = { schemaVersion: string; principalKey: string; canCollect: boolean; planDigest: string | null; catalogDigest: string | null; request: Request & { schemaVersion: string }; evidenceRequest: Record<string, unknown>; capacity: Record<string, number | null>; limitations: string[]; coverage: { domain: string; query: Record<string, string>; status: string; availability: string; reason: string; sourceKey?: string }[] };
type Collection = { status: string; errorCode?: string; consecutiveFailures?: number; nextAttemptAt?: string };
type Item = { id: string; clientRequestId?: string; question?: string; status: string; version: number; collection: Collection; createdAt: string; storedBytes: number; sourceCount?: number; completedSources?: number; rowCount?: number };
type Detail = Item & { workbenchAnalysisEnabled?: boolean; budgetSupported?: boolean; mappingSupported?: boolean; screeningSupported?: boolean;
  promotionSupported?: boolean; promotionChoices?: PromotionChoice[];
  plan: { schemaVersion?: string; analysisRequest?: { question: string }; sources?: Source[]; sourceCount?: number; catalogDigest?: string; collector?: { version: number; surface: string; pageSize: number } }; sources: Record<string, { pageCount: number; rowCount: number; complete: boolean }> };
type DirectoryPage = { schemaVersion: string; runId: string; evidenceVersion: number; catalogDigest: string; offset: number; total: number; returned: number; nextOffset: number | null; items: (Source & { ordinal: number })[] };
type Report = { id: string; workflowId: string; status: string; createdAt: string };
type Pending = { schemaVersion: 1; principalKey: string; kind: "evidence" | "report"; bodyJson: string; label: string; createdAt: string; outcome: "prepared" | "unknown" };
type MappingIntent = MappingSelection & { intent: boolean };
type BoundQuestionSuggestion = { principalKey: string; businessToday: string; question: string; value: QuestionSuggestion };
const emptyMapping = (): MappingIntent => ({ bindingKey: "", pairs: [], ready: false, reason: "loading", intent: false });
const names: Record<string, string> = { current: "本期", previous: "环比", yearAgo: "同比", promotion: "推广与关键词", master: "商品主数据", sku: "SKU 销售", spu: "SPU 销售", b2b: "B 端销售", collecting: "采集中", queued: "等待后台采集", reading: "后台读取中", paused: "已暂停", sealed: "证据已封存", cancelled: "已取消", completed: "已完成", running: "分析中", planned: "已列入计划", unsupported: "不支持", not_collected: "尚未取数" };
const reportStatus: Record<string, string> = { queued: "待执行", running: "分析中", waiting_review: "待人工复核", completed: "已完成", failed: "失败", paused: "已暂停", cancelled: "已取消" };
const initial = (): Request => ({ question: "", startDate: "", endDate: "", shops: [{ platform: "京东", shop: "", datasets: ["promotion", "master"], salesChannels: [] }], windows: ["current"], markets: [] });
const storageKey = (key: string) => "ai-business-workbench-pending-v1:"+key;
const message = (error: unknown) => error instanceof Error ? error.message : "请求失败，请刷新后核验状态。";
const summary = (query: Record<string, string>) => Object.entries(query).map(([key, value]) => `${({ platform: "平台", shop: "店铺", dataset: "数据集", channel: "ERP渠道", category: "类目", scope: "范围", rankingDimension: "榜单维度", priceBandFilter: "价格带", startDate: "开始", endDate: "结束", window: "周期" } as Record<string, string>)[key] ?? key}：${["dataset", "window"].includes(key) ? names[value] ?? value : value}`).join(" · ");
// New previews are explicitly v2; persisted legacy submissions bypass this
// validator and replay their original, identity-bound body without conversion.
function validatePreview(value: Preview) {
  const bad = (): never => { throw new Error("来源预览协议或完整性不匹配，请重新预览；未发起采集。"); };
  if (value.schemaVersion !== "business-plan-preview-v2" || value.request?.schemaVersion !== "business-plan-request-v2" || value.evidenceRequest?.schemaVersion !== "business-evidence-v2") bad();
  if (!Array.isArray(value.coverage) || !Array.isArray(value.limitations) || !value.limitations.every(item => typeof item === "string") || typeof value.canCollect !== "boolean" || !value.capacity) bad();
  const capacity = value.capacity;
  for (const name of ["sourceCount", "maxSources", "maxPlanBytes", "maxWorkflowBytes", "queryBytes", "maxQueryBytes", "directoryQueryBytes", "maxDirectoryQueryBytes", "factBytes", "factPages"]) if (!Number.isSafeInteger(capacity[name]) || capacity[name]! < 0) bad();
  for (const name of ["planBytes", "workflowBytes"]) if (capacity[name] !== null && (!Number.isSafeInteger(capacity[name]) || capacity[name]! < 0)) bad();
  if (capacity.maxSources !== 48 || capacity.maxPlanBytes !== 16000 || capacity.maxWorkflowBytes !== 8000 || capacity.maxQueryBytes !== 4096 || capacity.maxDirectoryQueryBytes !== 131072 || capacity.factBytes !== 67108864 || capacity.factPages !== 2000) bad();
  const sources = value.evidenceRequest.sources;
  if (!Array.isArray(sources) || sources.length !== capacity.sourceCount || value.evidenceRequest.collectionMode !== "bulk" || value.evidenceRequest.autoCollect !== true) bad();
  const queryKey = (query: Record<string, string>) => {
    if (!query || typeof query !== "object" || Array.isArray(query) || !Object.values(query).every(item => typeof item === "string")) bad();
    return JSON.stringify(Object.entries(query).sort(([left], [right]) => left.localeCompare(right)));
  };
  const sourceKeys = new Set<string>();
  for (const source of sources as Source[]) {
    if (!source || typeof source.key !== "string" || !source.key || sourceKeys.has(source.key) || typeof source.domain !== "string") bad();
    sourceKeys.add(source.key);
    const match = value.coverage.filter(row => row.sourceKey === source.key && row.status === "planned");
    if (match.length !== 1 || match[0].domain !== source.domain || queryKey(match[0].query) !== queryKey(source.query)) bad();
  }
  for (const row of value.coverage) {
    if (!row || !["planned", "unsupported"].includes(row.status) || row.availability !== "not_collected" || typeof row.reason !== "string" || typeof row.domain !== "string") bad();
    queryKey(row.query);
    if (row.status === "planned" && !sourceKeys.has(row.sourceKey!)) bad();
  }
  for (const digest of [value.planDigest, value.catalogDigest]) if (digest !== null && (typeof digest !== "string" || !/^[a-f0-9]{64}$/.test(digest))) bad();
  if (value.canCollect && (!value.planDigest || !value.catalogDigest || capacity.sourceCount === 0 || value.coverage.some(row => row.status !== "planned") || capacity.planBytes === null || capacity.workflowBytes === null || [["sourceCount", "maxSources"], ["planBytes", "maxPlanBytes"], ["workflowBytes", "maxWorkflowBytes"], ["queryBytes", "maxQueryBytes"], ["directoryQueryBytes", "maxDirectoryQueryBytes"]].some(([size, limit]) => capacity[size]! > capacity[limit]!))) bad();
}
function TextInput({ label, value, onChange, type = "text", maxLength = 100 }: { label: string; value: string; onChange: (value: string) => void; type?: string; maxLength?: number }) {
  return <label>{label}<input aria-label={label} required value={value} maxLength={maxLength} type={type} onChange={event => onChange(event.target.value)} /></label>;
}
async function api<T>(url: string, signal: AbortSignal, bodyJson?: string): Promise<T> {
  const { response, data } = await fetchBoundedJson({ url, signal, timeoutMs: 30000, maxBytes: 2*1024*1024,
    init: { method: bodyJson === undefined ? "GET" : "POST", cache: "no-store", ...(bodyJson === undefined ? {} : { headers: { "content-type": "application/json" }, body: bodyJson }) } });
  if (!response.ok) throw Object.assign(new Error(data && typeof data === "object" && "error" in data ? String(data.error) : `请求失败（${response.status}）`), { status: response.status });
  if (!data || typeof data !== "object") throw new Error("服务端回执无效，请核验同一次请求。");
  return data as T;
}

function SourceDirectory({ detail }: { detail: Detail }) {
  const [offset, setOffset] = useState(0), [history, setHistory] = useState<number[]>([]), [retry, setRetry] = useState(0);
  const [page, setPage] = useState<DirectoryPage | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const sourceKeys = JSON.stringify(Object.keys(detail.sources).sort());
  // The parent keys this component by account, run, version and immutable catalog.
  // Every page is replaced, never combined with a different evidence version.
  useEffect(() => {
    const ctl = new AbortController(); let active = true;
    const knownSources = new Set<string>(JSON.parse(sourceKeys));
    setPage(null); setError(""); setLoading(true);
    void (async () => {
      try {
        const value = await api<DirectoryPage>(`/api/ai/business-evidence/${encodeURIComponent(detail.id)}/sources?offset=${offset}&limit=10`, ctl.signal);
        if (!active || ctl.signal.aborted) return;
        if (value.evidenceVersion !== detail.version || value.catalogDigest !== detail.plan.catalogDigest) throw new Error("目录版本已变化，请刷新选中任务后重新查看。");
        if (value.schemaVersion !== "business-evidence-directory-page-v2" || value.runId !== detail.id || value.offset !== offset || value.total !== detail.plan.sourceCount || !Array.isArray(value.items) || value.items.length < 1 || value.items.length > 10 || value.returned !== value.items.length || offset+value.returned > value.total || value.nextOffset !== (offset+value.returned < value.total ? offset+value.returned : null) || new Set(value.items.map(item => item.key)).size !== value.items.length || value.items.some((item, index) => !item || item.ordinal !== offset+index+1 || typeof item.key !== "string" || !knownSources.has(item.key) || typeof item.domain !== "string" || !item.query || typeof item.query !== "object" || Array.isArray(item.query) || Object.values(item.query).some(v => typeof v !== "string"))) throw new Error("来源目录回执无效，请刷新选中任务后核验。");
        setPage(value);
      } catch (caught) { if (active && !ctl.signal.aborted) setError(message(caught)); }
      finally { if (active && !ctl.signal.aborted) setLoading(false); }
    })();
    return () => { active = false; ctl.abort(); };
  }, [detail.id, detail.version, detail.plan.catalogDigest, detail.plan.sourceCount, sourceKeys, offset, retry]);
  // Hide the preceding page immediately, before the next effect begins.
  const visible = page?.offset === offset ? page : null;
  return <section aria-label="精确来源目录"><h4>精确来源目录</h4><p>逐页显示已安排的精确查询条件；本页不代表全部来源。</p>
    {error && <p role="alert" className="bw-error">{error}<button onClick={() => setRetry(value => value+1)}>重试来源目录</button></p>}
    {(loading || (!visible && !error)) && <p role="status">正在读取来源目录…</p>}
    {visible && <><p>来源 {visible.offset+1}–{visible.offset+visible.returned} / {visible.total} · 任务版本 {visible.evidenceVersion}</p><div className="bw-scroll"><table><thead><tr><th>来源编号</th><th>领域</th><th>精确条件</th><th>已保存页数</th><th>行数</th><th>采集完整性</th></tr></thead><tbody>{visible.items.map(source => { const progress = detail.sources[source.key]; return <tr key={source.key}><td>{source.key}</td><td>{source.domain}</td><td>{summary(source.query)}</td><td>{progress.pageCount}</td><td>{progress.rowCount}</td><td>{progress.complete ? "分页采集完成" : "未完成核验"}</td></tr>; })}</tbody></table></div></>}
    <div className="bw-actions"><button disabled={loading || !history.length} onClick={() => { setOffset(history[history.length-1]); setHistory(values => values.slice(0, -1)); }}>上一页来源</button><button disabled={loading || !visible || visible.nextOffset === null} onClick={() => { if (visible?.nextOffset != null) { setHistory(values => [...values, offset]); setOffset(visible.nextOffset); } }}>下一页来源</button></div>
  </section>;
}

export default function AiBusinessWorkbench({ onReportCreated }: { onReportCreated: (id: string) => void }) {
  const [form, setForm] = useState<Request>(initial), [preview, setPreview] = useState<Preview | null>(null), [confirmed, setConfirmed] = useState(false);
  const [items, setItems] = useState<Item[]>([]), [page, setPage] = useState(1), [total, setTotal] = useState(0);
  const [listedPage, setListedPage] = useState(0), [principalKey, setPrincipalKey] = useState("");
  const [selected, setSelected] = useState(""), [detail, setDetail] = useState<Detail | null>(null), [reports, setReports] = useState<Report[]>([]);
  const [moreReports, setMoreReports] = useState(false);
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("legacy"), analysisModeRef = useRef<AnalysisMode>("legacy");
  const [promotionSelection, setPromotionSelection] = useState<PromotionSelection | null>(null);
  const [listError, setListError] = useState(""), [detailError, setDetailError] = useState(""), [previewError, setPreviewError] = useState(""), [writeError, setWriteError] = useState(""), [storageError, setStorageError] = useState("");
  const [pending, setPending] = useState<Pending | null>(null), [ready, setReady] = useState(false), [busy, setBusy] = useState(false), [previewBusy, setPreviewBusy] = useState(false), [notice, setNotice] = useState("");
  const [mapping, setMapping] = useState<MappingIntent>(emptyMapping);
  const [mappingReset, setMappingReset] = useState(0);
  const [scopeOpen, setScopeOpen] = useState(false), [scopeError, setScopeError] = useState("");
  const [marketOpen, setMarketOpen] = useState(false), [marketError, setMarketError] = useState("");
  const [salesOpen, setSalesOpen] = useState(false), [salesError, setSalesError] = useState("");
  const [questionSuggestion, setQuestionSuggestion] = useState<BoundQuestionSuggestion | null>(null), [questionSuggestionError, setQuestionSuggestionError] = useState("");
  const formRef = useRef(form), scopeLocked = useRef(true);
  const verifiedQuestionSources = useRef<{ principalKey: string; sources: QuestionSourceScalar[] }>({ principalKey: "", sources: [] });
  const mappingRef = useRef<MappingIntent>(emptyMapping()), detailRef = useRef<Detail | null>(null), detailFailed = useRef(false);
  const live = useRef(true), actor = useRef(""), pendingRef = useRef<Pending | null>(null), selectedRef = useRef("");
  const listController = useRef<AbortController | null>(null), detailController = useRef<AbortController | null>(null), previewController = useRef<AbortController | null>(null), writeController = useRef<AbortController | null>(null);
  const updateMapping = useCallback((value: MappingIntent) => { mappingRef.current = value; setMapping(value); }, []);
  const mappingChanged = useCallback((value: MappingSelection) => {
    const current = detailRef.current;
    if (!live.current || !current || selectedRef.current !== current.id || value.bindingKey !== mappingBindingKey(current, actor.current) || current.mappingSupported !== true) return;
    // Only the explicit clear action discards an earlier selection intent.
    // Directory reloads (including successful empty selection) cannot downgrade it.
    const intent = value.reason === "cleared" ? false : mappingRef.current.intent || value.pairs.length > 0;
    updateMapping({ ...value, pairs: value.pairs.map(pair => ({ ...pair })), ready: value.ready && !detailFailed.current, intent });
  }, [updateMapping]);
  const restore = useCallback((key: string) => {
    setReady(false); setStorageError("");
    try {
      const probe = storageKey(key)+":probe"; sessionStorage.setItem(probe, "1"); sessionStorage.removeItem(probe);
      const raw = sessionStorage.getItem(storageKey(key));
      let value: Pending | null = null;
      if (raw) {
        if (raw.length > 100000) throw new Error("待确认提交记录超出容量。");
        value = JSON.parse(raw) as Pending;
        if (!value || value.schemaVersion !== 1 || value.principalKey !== key || !["evidence", "report"].includes(value.kind) || !["prepared", "unknown"].includes(value.outcome) || typeof value.bodyJson !== "string" || typeof value.label !== "string") throw new Error("待确认提交记录损坏。");
        const body = JSON.parse(value.bodyJson);
        if (!body || typeof body.clientRequestId !== "string" || !body.clientRequestId || body.expectedPrincipalKey !== key) throw new Error("待确认请求身份无效。");
      }
      pendingRef.current = value; setPending(value); setReady(true);
    } catch (error) { setStorageError(message(error)+" 浏览器无法可靠保存提交；已阻止新建，请恢复当前标签页的会话存储后重试检查。"); }
  }, []);
  const principal = useCallback((key: string) => {
    if (!/^[a-f0-9]{64}$/.test(key)) throw new Error("账号身份回执无效。");
    if (actor.current !== key) {
      const changed = Boolean(actor.current); actor.current = key; setPrincipalKey(key);
      if (changed) {
        analysisModeRef.current = "legacy"; setAnalysisMode("legacy"); setPromotionSelection(null);
        writeController.current?.abort(); previewController.current?.abort(); detailController.current?.abort(); listController.current?.abort(); listController.current = null;
        setPreview(null); setConfirmed(false); setDetail(null); setReports([]); setItems([]);
        formRef.current = initial(); setForm(formRef.current); setScopeOpen(false); setScopeError(""); setMarketOpen(false); setMarketError(""); setSalesOpen(false); setSalesError("");
        verifiedQuestionSources.current = { principalKey: key, sources: [] }; setQuestionSuggestion(null); setQuestionSuggestionError("");
        detailRef.current = null; detailFailed.current = false; updateMapping(emptyMapping());
        selectedRef.current = ""; setSelected(""); setPage(1); setMoreReports(false); setWriteError("账号已变化，请重新核验当前账号的范围及待确认提交。");
      }
      pendingRef.current = null; setPending(null); restore(key);
    }
  }, [restore, updateMapping]);
  const loadList = useCallback(async (force = false) => {
    if (listController.current && !force) return;
    listController.current?.abort(); const ctl = new AbortController(); listController.current = ctl;
    const current = () => live.current && listController.current === ctl && !ctl.signal.aborted;
    try {
      const result = await api<{ items: Item[]; principalKey: string; total?: number; pagination?: { total: number } }>(`/api/ai/business-evidence?page=${page}&pageSize=10`, ctl.signal);
      if (!current()) return;
      if (!Array.isArray(result.items)) throw new Error("任务列表响应无效。");
      principal(result.principalKey); setItems(result.items); setListedPage(page); setTotal(result.pagination?.total ?? result.total ?? 0); setListError("");
    } catch (error) { if (current()) setListError(message(error)); }
    finally { if (listController.current === ctl) listController.current = null; }
  }, [page, principal]);
  const loadDetail = useCallback(async (id: string, force = false) => {
    if (!id || (detailController.current && !force)) return;
    detailController.current?.abort(); const ctl = new AbortController(); detailController.current = ctl;
    const current = () => live.current && detailController.current === ctl && selectedRef.current === id && !ctl.signal.aborted;
    try {
      const result = await api<{ item: Detail; reports: Report[]; principalKey: string; reportsPagination?: { hasMore: boolean } }>(`/api/ai/business-evidence/${encodeURIComponent(id)}`, ctl.signal);
      if (!current()) return;
      const item = result.item, v2 = item?.plan?.schemaVersion === "business-evidence-v2";
      const validPlan = v2 ? Number.isInteger(item.plan.sourceCount) && item.plan.sourceCount! >= 1 && item.plan.sourceCount! <= 48 && /^[a-f0-9]{64}$/.test(item.plan.catalogDigest ?? "") && item.sources && !Array.isArray(item.sources) && Object.keys(item.sources).length === item.plan.sourceCount && Object.values(item.sources).every(value => value && Number.isSafeInteger(value.pageCount) && value.pageCount >= 0 && Number.isSafeInteger(value.rowCount) && value.rowCount >= 0 && typeof value.complete === "boolean") : (!item?.plan?.schemaVersion || item.plan.schemaVersion === "business-evidence-v1") && Array.isArray(item?.plan?.sources);
      if (item?.id !== id || !Number.isSafeInteger(item.version) || item.version < 1 || !validPlan || !Array.isArray(result.reports)) throw new Error("任务详情回执无效。");
      validatePromotionChoices(item);
      principal(result.principalKey); if (!current()) return;
      const binding = mappingBindingKey(item, result.principalKey);
      if (mappingRef.current.bindingKey !== binding || item.mappingSupported !== true) updateMapping({ ...emptyMapping(), bindingKey: binding, intent: mappingRef.current.intent });
      detailRef.current = item; detailFailed.current = false;
      setDetail(result.item); setReports(result.reports); setMoreReports(result.reportsPagination?.hasMore === true); setDetailError("");
    } catch (error) { if (current()) { detailFailed.current = true; if (mappingRef.current.intent) updateMapping({ ...mappingRef.current, ready: false, pairs: [] }); setDetailError(message(error)); } }
    finally { if (detailController.current === ctl) detailController.current = null; }
  }, [principal, updateMapping]);
  const choose = useCallback((id: string) => {
    if (selectedRef.current !== id) { updateMapping(emptyMapping()); analysisModeRef.current = "legacy"; setAnalysisMode("legacy"); setPromotionSelection(null); }
    detailRef.current = null; detailFailed.current = false;
    detailController.current?.abort(); selectedRef.current = id; setSelected(id); setDetail(null); setReports([]); setMoreReports(false); setDetailError("");
    void loadDetail(id, true);
  }, [loadDetail, updateMapping]);
  useEffect(() => { live.current = true; return () => { live.current = false; for (const ref of [listController, detailController, previewController, writeController]) ref.current?.abort(); }; }, []);
  useEffect(() => { void loadList(true); return () => { listController.current?.abort(); listController.current = null; }; }, [loadList]);
  useEffect(() => {
    if (!items.some(item => item.status === "collecting") && detail?.status !== "collecting") return;
    const timer = window.setInterval(() => { void loadList(); if (selectedRef.current) void loadDetail(selectedRef.current); }, 5000);
    return () => window.clearInterval(timer);
  }, [items, detail?.status, loadList, loadDetail]);
  function edit(next: Request) {
    previewController.current?.abort(); previewController.current = null; setPreviewBusy(false); setPreview(null); setConfirmed(false);
    // A hand edit cannot turn an arbitrary matching string into a validated directory choice.
    const owned = verifiedQuestionSources.current;
    if (owned.principalKey === actor.current) owned.sources = owned.sources.filter(source => source.domain === "market"
      ? next.markets.some(market => market.platform === source.platform && market.category === source.category
        && market.scope === source.scope && market.rankingDimension === source.rankingDimension && market.priceBandFilter === source.priceBandFilter)
      : next.shops.some(shop => shop.platform === source.platform && shop.shop === source.shop &&
        (source.domain === "sales" ? shop.salesChannels.includes(source.channel) : shop.datasets.includes(source.dataset))));
    setQuestionSuggestion(null); setQuestionSuggestionError("");
    formRef.current = next; setForm(next);
  }
  function suggestScope() {
    if (!live.current || scopeLocked.current || !actor.current || actor.current !== principalKey) return;
    try {
      const businessToday = getOperationsBusinessDates(new Date()).today;
      const current = formRef.current;
      const owned = verifiedQuestionSources.current;
      const value = suggestBusinessQuestionScope({ question: current.question, shanghaiToday: businessToday,
        form: { startDate: current.startDate, endDate: current.endDate, windows: current.windows,
          shops: current.shops.map(({ platform, shop }) => ({ platform, shop })), markets: current.markets.map(market => ({ ...market })) },
        directory: owned.principalKey === actor.current ? owned.sources.map(source => ({ ...source })) : [] });
      setQuestionSuggestion({ principalKey: actor.current, businessToday, question: current.question, value });
      setQuestionSuggestionError("");
    } catch (error) { setQuestionSuggestion(null); setQuestionSuggestionError(message(error)); }
  }
  function applySuggestedDates() {
    const bound = questionSuggestion, current = formRef.current;
    if (!bound || !bound.value.dates || bound.principalKey !== actor.current || bound.question !== current.question
      || scopeLocked.current || pendingRef.current || writeController.current) return;
    edit({ ...current, startDate: bound.value.dates.startDate, endDate: bound.value.dates.endDate,
      windows: [...bound.value.windows] });
  }
  async function plan(event: React.FormEvent) {
    event.preventDefault(); previewController.current?.abort(); const ctl = new AbortController(); previewController.current = ctl;
    setPreviewBusy(true); setPreviewError(""); setPreview(null); setConfirmed(false);
    const current = () => live.current && previewController.current === ctl && !ctl.signal.aborted;
    try {
      const result = await api<Preview>("/api/ai/business-plan/preview", ctl.signal, JSON.stringify({ ...form, schemaVersion: "business-plan-request-v2" }));
      if (!current()) return;
      validatePreview(result);
      principal(result.principalKey); if (!current()) return;
      setPreview(result);
    } catch (error) { if (current()) setPreviewError(message(error)); }
    finally { if (previewController.current === ctl) { previewController.current = null; if (live.current) setPreviewBusy(false); } }
  }
  function clearPending(value: Pending) {
    sessionStorage.removeItem(storageKey(value.principalKey));
    if (sessionStorage.getItem(storageKey(value.principalKey)) !== null) throw new Error("待确认提交记录未能清除；请重试核验同一请求。");
    pendingRef.current = null; setPending(null);
  }
  async function send(value: Pending) {
    if (writeController.current || !ready || actor.current !== value.principalKey) return;
    const ctl = new AbortController(); writeController.current = ctl; setBusy(true); setWriteError(""); setNotice("");
    let submitted = false;
    const previouslyUnknown = value.outcome === "unknown";
    const current = () => live.current && writeController.current === ctl && actor.current === value.principalKey && !ctl.signal.aborted;
    try {
      // Recheck the server identity before replaying an account-bound payload.
      const identity = await api<{ principalKey: string }>("/api/ai/business-evidence?page=1&pageSize=1", ctl.signal);
      if (!current()) return;
      if (identity.principalKey !== value.principalKey) { principal(identity.principalKey); return; }
      try {
        const uncertain: Pending = { ...value, outcome: "unknown" }, serialized = JSON.stringify(uncertain);
        sessionStorage.setItem(storageKey(value.principalKey), serialized);
        if (sessionStorage.getItem(storageKey(value.principalKey)) !== serialized) throw new Error("写入前提交状态未能持久保存。");
        pendingRef.current = uncertain; setPending(uncertain);
      } catch (error) { setReady(false); setStorageError(message(error)+" 已阻止本次请求发送。"); throw error; }
      submitted = true;
      const result = await api<{ item: { id: string } }>(value.kind === "evidence" ? "/api/ai/business-evidence" : "/api/ai/business-reports", ctl.signal, value.bodyJson);
      if (!current()) return;
      if (typeof result.item?.id !== "string" || !result.item.id) throw new Error("提交回执无效。");
      clearPending(value); setNotice(value.kind === "evidence" ? "证据任务已保存，后台继续采集。" : "分析任务已创建。");
      void loadList(true);
      if (value.kind === "evidence") choose(result.item.id);
      else { if (selectedRef.current) void loadDetail(selectedRef.current, true); onReportCreated(result.item.id); }
    } catch (error) {
      if (current()) {
        const status = error && typeof error === "object" && "status" in error ? Number(error.status) : 0;
        let text = message(error);
        if (submitted && !previouslyUnknown && status >= 400 && status < 500 && status !== 408 && status !== 429) {
          try { clearPending(value); } catch (storage) { text += " "+message(storage); setReady(false); setStorageError(message(storage)); }
        }
        setWriteError(text+(pendingRef.current ? " 提交结果待确认；请重试同一次提交，完整参数和请求编号已保留。" : ""));
      }
    } finally { if (writeController.current === ctl) { writeController.current = null; if (live.current) setBusy(false); } }
  }
  function create(kind: Pending["kind"], payload: Record<string, unknown>, label: string) {
    if (pendingRef.current || !ready || busy || !actor.current) return;
    let value: Pending, serialized: string;
    try {
      if (kind === "report" && analysisModeRef.current === "screening-promotion-v1") {
        const current = detailRef.current, selected = promotionSelection;
        if (!current || current.id !== selectedRef.current || detailFailed.current || current.status !== "sealed"
          || current.plan.schemaVersion !== "business-evidence-v2" || current.workbenchAnalysisEnabled !== true
          || current.promotionSupported !== true || payload.evidenceRunId !== current.id || payload.dryRun !== false
          || !selected || selected.principalKey !== actor.current || selected.runId !== current.id
          || selected.version !== current.version)
          throw new Error("词货专项须从当前账号已封存的京东推广本期来源中明确选择主来源与可选同店基期。请刷新核验；不会改走其他报告模式。");
        payload = { ...payload, analysisMode: "screening-promotion-v1",
          ...promotionSelectionPayload(current, selected, actor.current) };
        label += " · 词货五角色专项";
      } else if (kind === "report" && analysisModeRef.current === "screening-v1") {
        const current = detailRef.current;
        if (!current || current.id !== selectedRef.current || payload.evidenceRunId !== current.id || detailFailed.current || current.status !== "sealed" || current.plan.schemaVersion !== "business-evidence-v2" || current.workbenchAnalysisEnabled !== true || current.screeningSupported !== true || !Object.values(current.sources).every(source => source.complete) || payload.dryRun !== false) throw new Error("完整规则筛查需要当前账号下已封存、来源全部就绪且服务端支持的范围，并且不支持模拟分析。不会降级为旧模式。");
        payload = { ...payload, analysisMode: "screening-v1" };
        label += " · 完整规则筛查";
      }
      if (kind === "report" && mappingRef.current.intent) {
        const current = detailRef.current, selectedMapping = mappingRef.current;
        if (!current || current.id !== selectedRef.current || payload.evidenceRunId !== current.id || detailFailed.current || current.mappingSupported !== true || !selectedMapping.ready || !selectedMapping.pairs.length || selectedMapping.bindingKey !== mappingBindingKey(current, actor.current)) throw new Error("原商品关联选择尚未重新核验。请重新选择或明确清空关联，不能自动改为无关联报告。");
        payload = { ...payload, mappingPairs: selectedMapping.pairs.map(pair => ({ ...pair })) };
      }
      value = { schemaVersion: 1, principalKey: actor.current, kind, bodyJson: JSON.stringify({ ...payload, expectedPrincipalKey: actor.current, clientRequestId: "workbench-"+crypto.randomUUID() }), label, createdAt: new Date().toISOString(), outcome: "prepared" };
      if (kind === "report" && new TextEncoder().encode(value.bodyJson).byteLength > 65536) throw new Error("完整报告请求超过 65536 UTF-8 字节。请明确缩小关联或预算参数后重试；尚未保存或发送请求。");
      serialized = JSON.stringify(value);
      if (serialized.length > 100000) throw new Error("完整待确认提交记录超过 100000 字符。请明确缩小参数后重试；尚未保存或发送请求。");
    } catch (error) { setWriteError(message(error)); return; }
    try {
      sessionStorage.setItem(storageKey(value.principalKey), serialized);
      if (sessionStorage.getItem(storageKey(value.principalKey)) !== serialized) throw new Error("提交记录写入后校验失败。");
      pendingRef.current = value; setPending(value); void send(value);
    } catch (error) { setReady(false); setStorageError(message(error)+" 无法保存待确认请求，未发起提交。请检查会话存储。"); }
  }
  async function control(action: "pause" | "resume" | "cancel") {
    if (!detail || writeController.current || pendingRef.current) return;
    const item = detail, key = actor.current, ctl = new AbortController(); writeController.current = ctl; setBusy(true); setWriteError("");
    const current = () => live.current && writeController.current === ctl && actor.current === key && !ctl.signal.aborted;
    try {
      const identity = await api<{ principalKey: string }>("/api/ai/business-evidence?page=1&pageSize=1", ctl.signal);
      if (!current()) return;
      if (identity.principalKey !== key) { principal(identity.principalKey); return; }
      await api(`/api/ai/business-evidence/${encodeURIComponent(item.id)}/${action === "cancel" ? "finish" : "control"}`, ctl.signal, JSON.stringify({ expectedVersion: item.version, action }));
      if (current()) { void loadList(true); if (selectedRef.current === item.id) void loadDetail(item.id, true); setNotice("操作已保存。"); }
    } catch (error) { if (current()) setWriteError(message(error)+" 请刷新任务状态后再操作，控制请求不会自动重放。"); }
    finally { if (writeController.current === ctl) { writeController.current = null; if (live.current) setBusy(false); } }
  }
  const locked = busy || Boolean(pending) || !ready;
  useLayoutEffect(() => { scopeLocked.current = locked; }, [locked]);
  function addScope(selection: NetshopOptionSelection) {
    if (!live.current || scopeLocked.current || pendingRef.current || writeController.current || !actor.current || selection.principalKey !== actor.current) return false;
    try {
      const current = formRef.current;
      const shops = mergeNetshopOption(current.shops, selection.identity);
      edit({ ...current, shops });
      if (verifiedQuestionSources.current.principalKey !== actor.current) verifiedQuestionSources.current = { principalKey: actor.current, sources: [] };
      const source: QuestionSourceScalar = { domain: "netshop", ...selection.identity };
      verifiedQuestionSources.current.sources = [...verifiedQuestionSources.current.sources.filter(value => JSON.stringify(value) !== JSON.stringify(source)), source];
      setScopeError(""); return true;
    } catch (error) { setScopeError(message(error)); return false; }
  }
  function addMarket(selection: MarketOptionSelection) {
    if (!live.current || scopeLocked.current || pendingRef.current || writeController.current || !actor.current || selection.principalKey !== actor.current) return false;
    try {
      const current = formRef.current;
      const markets = mergeMarketOption(current.markets, selection, actor.current);
      edit({ ...current, markets });
      if (verifiedQuestionSources.current.principalKey !== actor.current) verifiedQuestionSources.current = { principalKey: actor.current, sources: [] };
      const source: QuestionSourceScalar = { domain: "market", ...selection.identity };
      verifiedQuestionSources.current.sources = [...verifiedQuestionSources.current.sources.filter(value => JSON.stringify(value) !== JSON.stringify(source)), source];
      setMarketError(""); return true;
    } catch (error) { setMarketError(message(error)); return false; }
  }
  function addSales(selection: SalesOptionSelection) {
    if (!live.current || scopeLocked.current || pendingRef.current || writeController.current || !actor.current || selection.principalKey !== actor.current) return false;
    try {
      const current = formRef.current;
      const shops = mergeSalesOption(current.shops, selection, actor.current);
      edit({ ...current, shops });
      if (verifiedQuestionSources.current.principalKey !== actor.current) verifiedQuestionSources.current = { principalKey: actor.current, sources: [] };
      const source: QuestionSourceScalar = { domain: "sales", ...selection.identity };
      verifiedQuestionSources.current.sources = [...verifiedQuestionSources.current.sources.filter(value => JSON.stringify(value) !== JSON.stringify(source)), source];
      setSalesError(""); return true;
    } catch (error) { setSalesError(message(error)); return false; }
  }
  const shopEdit = (i: number, change: Partial<Shop>) => edit({ ...form, shops: form.shops.map((shop, j) => j === i ? { ...shop, ...change } : shop) });
  const visibleItems = listedPage === page ? items : [];
  const detailV2 = detail?.plan.schemaVersion === "business-evidence-v2";
  const analysisAllowed = Boolean(detail && detail.status === "sealed" && detail.plan.analysisRequest?.question && (!detailV2 || detail.workbenchAnalysisEnabled === true));
  const screeningMode = analysisMode === "screening-v1";
  const promotionMode = analysisMode === "screening-promotion-v1";
  const screeningBlocked = screeningMode && (!detailV2 || !detail || Boolean(detailError) || detail.screeningSupported !== true || !Object.values(detail.sources).every(source => source.complete));
  const promotionChoice = detail?.promotionChoices?.find(choice => choice.sourceKey === promotionSelection?.sourceKey);
  const promotionSelectionCurrent = Boolean(detail && promotionSelection && promotionSelection.principalKey === principalKey
    && promotionSelection.runId === detail.id && promotionSelection.version === detail.version && promotionChoice
    && (!promotionSelection.baselineKey || promotionChoice.baselineChoices.some(choice => choice.sourceKey === promotionSelection.baselineKey)));
  const promotionBlocked = promotionMode && (!detailV2 || !detail || Boolean(detailError) || detail.status !== "sealed"
    || detail.workbenchAnalysisEnabled !== true || detail.promotionSupported !== true || !promotionSelectionCurrent);
  const mappingBlocked = mapping.intent && (!detail || Boolean(detailError) || detail.mappingSupported !== true || mapping.bindingKey !== mappingBindingKey(detail, principalKey) || !mapping.ready || !mapping.pairs.length);
  return <section className="business-workbench" aria-label="经营分析工作台">
    <h3>经营分析工作台</h3><p>先描述问题并确认精确范围，再采集证据。可从问题生成日期和数据目的建议；来源计划与数据可用性仍须单独核验。</p>
    {storageError && <div role="alert" className="bw-error">{storageError}<button onClick={() => actor.current && restore(actor.current)}>重新检查会话存储</button></div>}
    {writeError && <p role="alert" className="bw-error" data-testid="write-error">{writeError}</p>}{notice && <p role="status">{notice}</p>}
    {pending && <div className="bw-pending" role="status"><strong>有一次提交等待确认</strong><p>{pending.label} · {pending.createdAt}</p><p>已冻结完整参数与请求编号。刷新后不会自动重发，请手动确认同一次提交；确认前不能另建任务。</p><details><summary>查看已保存的提交范围</summary><pre>{JSON.stringify(JSON.parse(pending.bodyJson), null, 2)}</pre></details><button disabled={busy || !ready} onClick={() => void send(pending)}>确认并重试同一次提交</button></div>}
    {busy && <button onClick={() => { writeController.current?.abort(); setWriteError("已取消等待；服务器可能已接收。新建请求会保留待确认记录，控制请求请刷新核验。"); }}>取消等待响应</button>}
    <form onSubmit={event => void plan(event)}><fieldset disabled={locked}><legend>问题与范围</legend>
      <label>分析问题<textarea aria-label="分析问题" required maxLength={1000} value={form.question} onChange={event => edit({ ...form, question: event.target.value })} placeholder="例如：推广费用上升但销售未增长，哪些商品和关键词需要调整？" /></label>
      <button type="button" disabled={!principalKey} onClick={suggestScope}>从问题生成范围建议</button>
      {questionSuggestionError && <p role="alert" className="bw-error">{questionSuggestionError}</p>}
      {questionSuggestion && questionSuggestion.principalKey === principalKey && <section className="bw-card" aria-label="问题范围建议">
        <h4>待确认的范围建议</h4><p>上海业务日期：{questionSuggestion.businessToday}。近30天截至昨天；这不证明来源已导入到昨天。</p>
        <p>日期：{questionSuggestion.value.dates ? `${questionSuggestion.value.dates.startDate} 至 ${questionSuggestion.value.dates.endDate}` : "尚未确定"}；比较窗口：{questionSuggestion.value.windows.map(value => names[value]).join("、")}；数据目的：{questionSuggestion.value.purposes.length ? questionSuggestion.value.purposes.map(value => names[value] ?? ({ market: "市场" } as Record<string, string>)[value] ?? value).join("、") : "尚未确定"}。</p>
        <p>已选择目录中的店铺候选：{questionSuggestion.value.shopCandidates.length ? questionSuggestion.value.shopCandidates.map(value => `${value.platform} · ${value.shop}`).join("；") : "无"}；市场候选：{questionSuggestion.value.marketCandidates.length ? questionSuggestion.value.marketCandidates.map(value => `${value.platform} · ${value.category} · ${value.scope} · ${value.rankingDimension} · ${value.priceBandFilter}`).join("；") : "无"}。</p>
        {questionSuggestion.value.comparison.previousEqualLength && <p>经营分析环比基期（前一等长区间）：{questionSuggestion.value.comparison.previousEqualLength.startDate} 至 {questionSuggestion.value.comparison.previousEqualLength.endDate}。{questionSuggestion.value.comparison.previousMonthSameDates && `销售概览同月上月同期：${questionSuggestion.value.comparison.previousMonthSameDates.startDate} 至 ${questionSuggestion.value.comparison.previousMonthSameDates.endDate}。`}</p>}
        <p>{questionSuggestion.value.comparison.note}</p>
        {questionSuggestion.value.unresolved.length > 0 && <div><strong>待澄清</strong><ul>{questionSuggestion.value.unresolved.map((value, i) => <li key={i}>{value}</li>)}</ul></div>}
        {questionSuggestion.value.missingSources.length > 0 && <div><strong>缺少精确来源</strong><ul>{questionSuggestion.value.missingSources.map((value, i) => <li key={i}>{value}</li>)}</ul></div>}
        <p>本建议没有来源授权，不会添加店铺或市场条件。请在来源选择器中逐项确认，并重新预览完整来源计划。</p>
        <div className="bw-actions"><button type="button" disabled={!questionSuggestion.value.dates} onClick={applySuggestedDates}>应用日期与比较窗口</button><button type="button" onClick={() => { setQuestionSuggestion(null); setQuestionSuggestionError(""); }}>关闭建议</button></div>
      </section>}
      <div className="bw-grid"><TextInput label="开始日期" value={form.startDate} onChange={startDate => edit({ ...form, startDate })} type="date" /><TextInput label="结束日期" value={form.endDate} onChange={endDate => edit({ ...form, endDate })} type="date" /></div>
      <div className="bw-checks">{["current", "previous", "yearAgo"].map(window => <label key={window}><input type="checkbox" checked={form.windows.includes(window)} disabled={window === "current"} onChange={event => edit({ ...form, windows: event.target.checked ? [...form.windows, window] : form.windows.filter(w => w !== window) })} />{names[window]}</label>)}</div>
      <p>当前经营分析的环比使用本期之前的等长日期段；销售概览的同月自定义区间使用上月同期。请核对预览中的具体日期后再创建任务。</p>
      <button type="button" disabled={!principalKey} onClick={() => setScopeOpen(value => !value)}>{scopeOpen ? "收起网店来源选择" : "从历史导入选择网店来源"}</button>
      {scopeOpen && principalKey && <><p>只添加你选择的店铺和数据集。日期、ERP 渠道及市场条件仍需自行确认；历史导入不表示所选期间完整。</p><AiBusinessScopePicker key={principalKey} principalKey={principalKey} disabled={locked} onSelect={addScope} onIdentityMismatch={() => void loadList(true)} /></>}
      {scopeError && <p role="alert" className="bw-error">{scopeError}</p>}
      <button type="button" disabled={!principalKey} onClick={() => setSalesOpen(value => !value)}>{salesOpen ? "收起ERP来源选择" : "选择ERP店铺与销售渠道"}</button>
      {salesOpen && principalKey && <AiBusinessSalesPicker key={principalKey} principalKey={principalKey} disabled={locked} onSelect={addSales} onIdentityMismatch={() => void loadList(true)} />}
      {salesError && <p role="alert" className="bw-error">{salesError}</p>}
      {form.shops.map((shop, i) => <fieldset className="bw-card" key={i}><legend>店铺 {i+1}</legend><div className="bw-grid"><TextInput label={`店铺 ${i+1} 平台`} value={shop.platform} onChange={platform => shopEdit(i, { platform })} /><TextInput label={`店铺 ${i+1} 精确名称`} value={shop.shop} onChange={value => shopEdit(i, { shop: value })} /></div>
        <div className="bw-checks">{["promotion", "master", "sku", "spu", "b2b"].map(dataset => <label key={dataset}><input type="checkbox" checked={shop.datasets.includes(dataset)} onChange={event => shopEdit(i, { datasets: event.target.checked ? [...shop.datasets, dataset] : shop.datasets.filter(d => d !== dataset) })} />{names[dataset]}</label>)}</div><p>商品主数据仅使用本期最新快照。ERP 渠道须逐项输入系统中的精确身份；不会按店铺名称猜测关联。</p>
        {shop.salesChannels.map((channel, j) => <div className="bw-inline" key={j}><TextInput label={`店铺 ${i+1} ERP 渠道 ${j+1}`} value={channel} onChange={value => shopEdit(i, { salesChannels: shop.salesChannels.map((c, k) => j === k ? value : c) })} /><button type="button" onClick={() => shopEdit(i, { salesChannels: shop.salesChannels.filter((_, k) => k !== j) })}>删除渠道 {j+1}</button></div>)}
        <div className="bw-actions"><button type="button" disabled={shop.salesChannels.length >= 10} onClick={() => shopEdit(i, { salesChannels: [...shop.salesChannels, ""] })}>添加 ERP 渠道</button><button type="button" onClick={() => edit({ ...form, shops: form.shops.filter((_, j) => j !== i) })}>删除店铺 {i+1}</button></div>
      </fieldset>)}
      <button type="button" disabled={form.shops.length >= 4} onClick={() => edit({ ...form, shops: [...form.shops, { platform: "京东", shop: "", datasets: ["promotion", "master"], salesChannels: [] }] })}>添加店铺（最多 4 家）</button>
      <h4>市场榜单条件</h4><p>仅按下列精确条件查询榜单样本，不代表全市场规模；不猜测类目、范围和价格带。</p>
      <button type="button" disabled={!principalKey} onClick={() => setMarketOpen(value => !value)}>{marketOpen ? "收起市场来源选择" : "从历史导入选择市场来源"}</button>
      {marketOpen && principalKey && <AiBusinessMarketPicker key={principalKey} principalKey={principalKey} disabled={locked} onSelect={addMarket} onIdentityMismatch={() => void loadList(true)} />}
      {marketError && <p role="alert" className="bw-error">{marketError}</p>}
      {form.markets.map((market, i) => <fieldset className="bw-card" key={i}><legend>市场条件 {i+1}</legend><div className="bw-grid">{(["platform", "category", "scope", "rankingDimension", "priceBandFilter"] as const).map(key => <TextInput key={key} label={`市场 ${i+1} ${{ platform: "平台", category: "精确类目", scope: "精确范围", rankingDimension: "榜单维度", priceBandFilter: "精确价格带" }[key]}`} value={market[key]} onChange={value => edit({ ...form, markets: form.markets.map((m, j) => i === j ? { ...m, [key]: value } : m) })} maxLength={key === "platform" ? 100 : 200} />)}</div><button type="button" onClick={() => edit({ ...form, markets: form.markets.filter((_, j) => i !== j) })}>删除市场条件 {i+1}</button></fieldset>)}
      <button type="button" disabled={form.markets.length >= 7} onClick={() => edit({ ...form, markets: [...form.markets, { platform: "", category: "", scope: "", rankingDimension: "", priceBandFilter: "" }] })}>添加市场条件（最多 7 项）</button>
      <div className="bw-actions"><button type="submit" disabled={previewBusy}>预览完整来源计划</button>{previewBusy && <button type="button" onClick={() => { previewController.current?.abort(); previewController.current = null; setPreviewBusy(false); }}>取消预览</button>}</div>
    </fieldset></form>
    {previewError && <p role="alert" className="bw-error">{previewError}</p>}
    {preview && <section className="bw-card" aria-label="来源范围预览"><h4>完整来源矩阵</h4><p>拟采集 {preview.capacity.sourceCount} / {preview.capacity.maxSources} 个来源；计划 {preview.capacity.planBytes ?? "尚不可测算"} / {preview.capacity.maxPlanBytes} 字节；分析输入估算 {preview.capacity.workflowBytes ?? "尚不可测算"} / {preview.capacity.maxWorkflowBytes} 字节。</p><p>单来源查询最大 {preview.capacity.queryBytes} / {preview.capacity.maxQueryBytes} 字节；目录查询合计 {preview.capacity.directoryQueryBytes} / {preview.capacity.maxDirectoryQueryBytes} 字节。事实采集仍限 {preview.capacity.factBytes! / 1024 / 1024} MiB / {preview.capacity.factPages} 总页；更多来源不代表事实容量增加，轻量分析输入估算也不代表模型上下文已经通过核验。</p>
      <div className="bw-scroll" tabIndex={0}><table><thead><tr><th>领域</th><th>精确查询条件</th><th>支持状态</th><th>数据状态</th><th>说明</th></tr></thead><tbody>{preview.coverage.map((row, i) => <tr key={i}><td>{row.domain}</td><td>{summary(row.query)}</td><td>{names[row.status] ?? row.status}</td><td>{names[row.availability] ?? row.availability}</td><td>{row.reason}</td></tr>)}</tbody></table></div>
      <ul>{preview.limitations.map((limit, i) => <li key={i}>{limit}</li>)}</ul>{!preview.canCollect && <p className="bw-error" role="alert">范围存在缺口或超过容量，不能开始采集。请明确调整范围后重新预览；系统不会截断来源。</p>}
      <label className="bw-confirm"><input type="checkbox" checked={confirmed} disabled={locked || !preview.canCollect} onChange={event => setConfirmed(event.target.checked)} />我已核对精确范围与限制，确认采集以上全部来源</label><button disabled={locked || !confirmed || !preview.canCollect || preview.principalKey !== principalKey} onClick={() => create("evidence", preview.evidenceRequest, preview.request.question)}>确认范围并开始后台采集</button>
    </section>}
    <section aria-label="已有证据任务"><div className="bw-actions"><h4>已有证据任务</h4><button onClick={() => void loadList(true)}>刷新任务列表</button></div>{listError && <p role="alert" className="bw-error">{listError}</p>}
      {visibleItems.map(item => <button className={`bw-task ${selected === item.id ? "bw-selected" : ""}`} key={item.id} onClick={() => choose(item.id)}><strong>{item.question || "未附分析问题"}</strong><span>{names[item.collection.status] ?? names[item.status] ?? item.status} · 来源 {item.completedSources ?? 0}/{item.sourceCount ?? 0} · {item.rowCount ?? 0} 行 · {(item.storedBytes/1024).toFixed(1)} KiB</span><small>{item.createdAt}</small></button>)}
      {!visibleItems.length && !listError && <p>{listedPage === page ? "当前页暂无任务。" : "正在读取任务列表…"}</p>}<div className="bw-actions"><button disabled={page <= 1} onClick={() => setPage(p => p-1)}>上一页</button><span>第 {page} 页 · 共 {total} 项</span><button disabled={page*10 >= total} onClick={() => setPage(p => p+1)}>下一页</button></div>
    </section>
    {selected && <section className="bw-card" aria-label="选中任务详情"><div className="bw-actions"><h4>任务详情</h4><button onClick={() => void loadDetail(selected, true)}>刷新选中任务</button></div>{detailError && <p role="alert" className="bw-error">{detailError}</p>}
      {!detail && !detailError && <p role="status">正在读取任务…</p>}{detail && <><p className="bw-question">{detail.plan.analysisRequest?.question || "此历史证据任务未保存分析问题。"}</p><p>{names[detail.collection.status] ?? detail.collection.status} · 版本 {detail.version} · {(detail.storedBytes/1024).toFixed(1)} KiB</p>{detail.collection.errorCode && <p role="alert">采集错误：{detail.collection.errorCode}；连续失败 {detail.collection.consecutiveFailures ?? 0} 次。请核验来源后恢复。</p>}
        <p>下列窗口是已安排的查询范围。分页采集完成不等于业务日期齐全，缺日期与缺字段仍须在分析中核验。</p>{detailV2 ? <><p>来源总数 {detail.plan.sourceCount} · 分页采集完成 {Object.values(detail.sources).filter(source => source.complete).length} · 已保存 {Object.values(detail.sources).reduce((sum, source) => sum+source.pageCount, 0)} 页 / {Object.values(detail.sources).reduce((sum, source) => sum+source.rowCount, 0)} 行</p><SourceDirectory key={`${principalKey}:${detail.id}:${detail.version}:${detail.plan.catalogDigest}`} detail={detail} /></> : <div className="bw-scroll"><table><thead><tr><th>来源</th><th>条件</th><th>已保存页数</th><th>行数</th><th>采集完整性</th></tr></thead><tbody>{detail.plan.sources!.map(source => { const progress = detail.sources[source.key]; return <tr key={source.key}><td>{source.domain}</td><td>{summary(source.query)}</td><td>{progress?.pageCount ?? 0}</td><td>{progress?.rowCount ?? 0}</td><td>{progress?.complete ? "分页采集完成" : "未完成核验"}</td></tr>; })}</tbody></table></div>}
        {detail.status === "collecting" && !detail.plan.collector && <p>此历史任务使用手动采集模式，不提供后台暂停或恢复。</p>}<div className="bw-actions">{detail.status === "collecting" && <>{detail.plan.collector && <button disabled={locked} onClick={() => void control(detail.collection.status === "paused" ? "resume" : "pause")}>{detail.collection.status === "paused" ? "恢复后台采集" : "暂停后台采集"}</button>}<button disabled={locked} onClick={() => void control("cancel")}>取消采集任务</button></>}</div>
        {detailV2 && detail.mappingSupported === true && analysisAllowed && <AiBusinessMappingBuilder key={`${principalKey}:${detail.id}:${detail.version}:${detail.plan.catalogDigest}:${detailError ? "unverified" : "verified"}:${mappingReset}`} run={detail} principalKey={principalKey} disabled={locked} onChange={mappingChanged} />}
        {mappingBlocked && <div className="bw-error" role="alert"><p>原商品关联选择已失效或尚未重新核验。请重新选择关联，或明确清空后继续；不会自动降级为无关联报告。</p><button disabled={locked} onClick={() => { updateMapping({ ...emptyMapping(), bindingKey: detail ? mappingBindingKey(detail, principalKey) : "", reason: "cleared" }); setMappingReset(value => value+1); }}>明确清空原关联意图</button></div>}
        {detailV2 && <label>分析方式<select aria-label="分析方式" value={analysisMode} disabled={locked} onChange={event => {
          if (pendingRef.current || writeController.current) return;
          const mode = event.target.value as AnalysisMode;
          if (!["legacy", "screening-v1", "screening-promotion-v1"].includes(mode)
            || (mode === "screening-v1" && detail.screeningSupported !== true)
            || (mode === "screening-promotion-v1" && detail.promotionSupported !== true)) return;
          analysisModeRef.current = mode; setAnalysisMode(mode); setPromotionSelection(null);
        }}><option value="legacy">现有多 Agent 分析</option><option value="screening-v1" disabled={detail.screeningSupported !== true}>完整规则筛查后分析</option><option value="screening-promotion-v1" disabled={detail.promotionSupported !== true}>京东推广关键词 × SKU 专项筛查</option></select></label>}
        {screeningMode && <p role="status">先在后台完整执行支持的规则，再由五个 Agent 分别读取固定候选与覆盖记录。候选不是全量明细；缺口和省略数量会保留。此模式不支持模拟分析，容量不足将停止，不自动缩小范围或切换模式。</p>}
        {detailV2 && detail.promotionSupported !== true && <p role="status">京东推广专项尚未开放，或当前封存目录没有符合条件的京东推广本期来源。请核对来源、封存状态及运行配置；不会使用手填名称推断来源。</p>}
        {promotionMode && <section className="bw-card" aria-label="推广专项来源选择"><h4>选择已封存的推广来源</h4>
          <label>本期京东推广来源<select aria-label="本期京东推广来源" value={promotionSelectionCurrent ? promotionSelection!.sourceKey : ""} disabled={locked || detail.promotionSupported !== true} onChange={event => {
            const exact = detail.promotionChoices?.find(choice => choice.sourceKey === event.target.value);
            setPromotionSelection(exact ? { principalKey, runId: detail.id, version: detail.version, sourceKey: exact.sourceKey, baselineKey: "" } : null);
          }}><option value="">请选择精确来源</option>{detail.promotionChoices?.map(choice => <option key={choice.sourceKey} value={choice.sourceKey}>{choice.platform} · {choice.shop} · {choice.startDate} 至 {choice.endDate} · {choice.sourceKey}</option>)}</select></label>
          <label>可选比较基期<select aria-label="可选比较基期" value={promotionSelectionCurrent ? promotionSelection!.baselineKey : ""} disabled={locked || !promotionSelectionCurrent} onChange={event => {
            if (!promotionSelectionCurrent || !promotionChoice || (event.target.value && !promotionChoice.baselineChoices.some(choice => choice.sourceKey === event.target.value))) return;
            setPromotionSelection(value => value ? { ...value, baselineKey: event.target.value } : null);
          }}><option value="">不使用基期</option>{promotionChoice?.baselineChoices.map(choice => <option key={choice.sourceKey} value={choice.sourceKey}>{names[choice.window]} · {choice.sourceKey}</option>)}</select></label>
          <p>这里只列出服务端从当前已封存目录核验的京东推广来源。选择基期时要求同店、同原始日期区间，且窗口为环比或同比；创建时服务端会再次核对。</p>
          <p>创建成功仅表示任务排队。请在报告详情查看规则筛查、五个 Agent、人工复核和正式 HTML / XLSX 文件的实际进度；未完成的阶段不会显示为已交付。</p>
        </section>}
        {screeningBlocked && <p role="alert">当前筛查范围或目录尚未就绪，或服务端未开放此能力。请刷新核验；不会自动改为旧模式。</p>}
        {promotionBlocked && <p role="alert">词货专项需要当前账号的已封存来源和明确的本期京东推广选择；请刷新或选择来源后再启动。</p>}
        {detailV2 && detail.workbenchAnalysisEnabled !== true ? <p role="status">服务端尚未开放此任务的工作台分析启动。可继续查看与管理采集任务；证据封存不代表报告已生成。</p> : <p>证据封存后才能手动启动分析。{analysisMode === "legacy" && "模拟分析不调用模型；"}正式多 Agent 分析会调用已配置模型，可能产生费用，需要独立复核。{detailV2 && (detail.budgetSupported === true ? "可直接启动分析，或先在下方配置固定预算。报告页面支持多卷交付。" : "此版本支持无固定预算分析，报告页面可选择多卷交付；当前未开放固定预算分析。")}</p>}<div className="bw-actions">{(analysisMode === "legacy" ? [true, false] : [false]).map(dryRun => <button key={String(dryRun)} disabled={locked || !analysisAllowed || mappingBlocked || screeningBlocked || promotionBlocked} onClick={() => { if (analysisAllowed && !mappingBlocked && !screeningBlocked && !promotionBlocked) create("report", { evidenceRunId: detail.id, question: detail.plan.analysisRequest!.question, dryRun }, `${detail.plan.analysisRequest!.question} · ${dryRun ? "模拟分析" : "多 Agent 分析"}`); }}>{dryRun ? "模拟分析（不调用模型）" : promotionMode ? "启动词货五角色专项分析（调用模型）" : screeningMode ? "启动完整筛查与多 Agent 分析（调用模型）" : "启动多 Agent 分析（调用模型）"}</button>)}</div>
        {detailV2 && detail.budgetSupported === true && analysisAllowed && !promotionMode && <AiBusinessBudgetBuilder key={`${principalKey}:${detail.id}:${detail.version}:${detail.plan.catalogDigest}`} run={detail} principalKey={principalKey} allowDryRun={!screeningMode} disabled={locked || mappingBlocked || screeningBlocked} onSubmit={(budgetPlan, dryRun) => {
          if (actor.current === principalKey && selectedRef.current === detail.id && analysisAllowed && !locked && !mappingBlocked && !screeningBlocked) create("report", { evidenceRunId: detail.id, question: detail.plan.analysisRequest!.question, dryRun, budgetPlan }, `${detail.plan.analysisRequest!.question} · 固定预算${dryRun ? "模拟" : "分析"}`);
        }} />}
        <h4>关联分析报告</h4>{reports.length ? reports.map(report => <button className="bw-task" key={report.id} onClick={() => onReportCreated(report.id)}><strong>打开报告 · {reportStatus[report.status] ?? report.status}</strong><small>{report.createdAt} · {report.id}</small></button>) : <p>尚无关联报告。</p>}{moreReports && <p>这里只显示最近 10 份关联报告；更多历史报告请在下方报告列表查看。</p>}
        {detailV2 && detail.status === "sealed" && reports.length > 0 && detail.plan.catalogDigest && detail.plan.sourceCount && <AiBusinessMarketV2Preview
          key={`${principalKey}:${detail.id}:${detail.version}:${detail.plan.catalogDigest}`}
          principalKey={principalKey} evidenceRunId={detail.id} evidenceVersion={detail.version}
          catalogDigest={detail.plan.catalogDigest} sourceCount={detail.plan.sourceCount}
          reports={reports} disabled={locked || Boolean(detailError)} />}
      </>}
    </section>}
  </section>;
}
