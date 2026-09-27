"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import "./ai-report-workbench.css";
import { parseReportSkill } from "@/lib/ai/report-skill-file";
import AiBusinessReportFiles from "./ai-business-report-files";
import AiBusinessBudget, { type BudgetResult } from "./ai-business-budget";
import AiBusinessWorkbench from "./ai-business-workbench";
import AiBusinessScreeningDetail from "./ai-business-screening-detail";
import AiBusinessPromotionDetail from "./ai-business-promotion-detail";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { SCREENING_PROFILE, REPORT_DETAIL_BYTES, ORDINARY_RESPONSE_BYTES, isReportDetailPath, allowReportDetailBytes } from "@/lib/ai/business-screening-view";
import { PROMOTION_PROFILE, allowPromotionReportDetailBytes, projectPromotionReport } from "@/lib/ai/business-promotion-view";

type Kind = "templates" | "skills" | "pipelines";
type Item = { id: string; name: string; enabled: boolean; scene?: string; description?: string; keywords?: string[]; domains?: string[]; body?: string; format?: string; sections?: string[]; dataSteps?: string; writingRules?: string; templateId?: string; skillIds?: string[] };
type Library = { version: number; config: Record<Kind, Item[]> };
type Scope = { platform: string; shop: string; startDate: string; endDate: string };
type Run = { id: string; name: string; status: string; dryRun: boolean; workflowId: string; version: number; createdAt: string; template: string; skills: string[]; scope: Scope };
type Node = { id: string; key: string; type: string; status: string; version: number; output?: { answer?: string }; instruction: string };
type Detail = { item: Run; snapshot?: { schemaVersion?: string; question?: string; evidenceProtocol?: string; executionProfile?: string }; screeningPreparation?: unknown; screening?: unknown; professionalAnalyses?: unknown; budget?: BudgetResult; sections?: { title: string; body: string }[]; contentError?: string; delivery: { status: string; channelId: string } | null; workflow: { id: string; status: string; version: number; retryable: boolean; nodes: Node[] } };
const labels = { templates: "报告模板", skills: "Skill 管理", pipelines: "AI 流水线" };
const statuses: Record<string, string> = { queued: "排队中", running: "运行中", waiting_review: "待人工复核", completed: "已完成", failed: "失败", paused: "暂停", cancelled: "已取消", rejected: "已拒绝", pending: "等待前序", skipped: "空跑通过" };
const scenes: Record<string, string> = { weekly: "网店经营周报", inventory: "库存健康报告", promotion: "推广复盘" };
const domains: Record<string, string> = { sales: "销售", inventory: "库存", netshop: "网店", market: "市场", customer_service: "客服", finance: "财务" };
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const detailPath = (options.method ?? "GET") === "GET" && isReportDetailPath(path);
  const { response, data, responseBytes } = await fetchBoundedJson({ url: path, init: { cache: "no-store", ...options }, signal: options.signal ?? undefined, timeoutMs: 30_000, maxBytes: detailPath ? REPORT_DETAIL_BYTES : ORDINARY_RESPONSE_BYTES });
  if (detailPath && !allowReportDetailBytes(data, responseBytes, response.ok)
    && !allowPromotionReportDetailBytes(data, responseBytes, response.ok, REPORT_DETAIL_BYTES))
    throw new Error("报告详情超过此协议的完整响应上限，不能裁剪显示。");
  if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data ? String(data.error) : "请求失败，请刷新查看状态");
  if (!data || typeof data !== "object") throw new Error("报告响应结构无效。");
  return data as T;
}
const post = <T,>(path: string, body: unknown) => api<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
function exportSkill(item: Item) {
  const { body, ...metadata } = item;
  const url = URL.createObjectURL(new Blob(["---\n" + JSON.stringify(metadata, null, 2) + "\n---\n" + body + "\n"], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = item.id + ".md"; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function AiReportWorkbenchView({ kind }: { kind: Kind }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState("");
  const [writeError, setWriteError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Item | null>(null);
  const [draftVersion, setDraftVersion] = useState(0);
  const [selected, setSelected] = useState<Item | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [comment, setComment] = useState("");
  const [filters, setFilters] = useState<Scope>({ platform: "京东", shop: "", startDate: "", endDate: "" });
  const [channels, setChannels] = useState<{ id: string; name: string; sendEnabled: boolean; status: string; targetDigest: string }[]>([]);
  const [channelId, setChannelId] = useState("");
  const [history, setHistory] = useState<{ version: number; created_by: string }[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyMore, setHistoryMore] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historical, setHistorical] = useState<Library | null>(null);
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const mounted = useRef(true);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const focusTarget = draft ? "edit:" + draft.id : selected ? "run:" + selected.id : detail ? "detail:" + detail.item.id : "";
  useEffect(() => {
    const selector = focusTarget.startsWith("detail:") ? ".report-detail" : focusTarget ? ".report-editor" : null;
    if (selector) document.querySelector(selector)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusTarget]);
  const load = useCallback(async () => {
    const current = ++generation.current;
    controller.current?.abort(); controller.current = new AbortController();
    const signal = AbortSignal.any([controller.current.signal, AbortSignal.timeout(30_000)]);
    setLoading(true);
    try {
      const [catalog, runList] = await Promise.all([
        api<{ item: Library; history: { version: number; created_by: string }[]; hasMore: boolean }>("/api/ai/report-library?page=" + historyPage, { signal }),
        kind === "pipelines" ? api<{ items: Run[]; total: number }>("/api/ai/reports?page=" + page + "&pageSize=12", { signal }) : Promise.resolve(null),
      ]);
      if (current !== generation.current || signal.aborted) return;
      setLibrary(catalog.item); setHistory(catalog.history); setHistoryMore(catalog.hasMore); setReadError("");
      if (runList) { setRuns(runList.items); setMore(page * 12 < runList.total); }
    } catch (error) { if (current === generation.current && mounted.current) setReadError(message(error)); }
    finally { if (current === generation.current && mounted.current) setLoading(false); }
  }, [kind, page, historyPage]);
  useEffect(() => {
    const reads = generation, details = detailGeneration;
    mounted.current = true; void load();
    return () => { mounted.current = false; reads.current++; details.current++; controller.current?.abort(); detailController.current?.abort(); };
  }, [load]);
  const openDetail = async (id: string) => {
    const current = ++detailGeneration.current;
    detailController.current?.abort(); detailController.current = new AbortController();
    setDetail(null);
    try {
      const value = await api<Detail>("/api/ai/reports/" + id, { signal: AbortSignal.any([detailController.current.signal, AbortSignal.timeout(30_000)]) });
      if (value.item?.id !== id || !value.workflow || !Array.isArray(value.workflow.nodes)) throw new Error("报告详情与当前请求不一致。");
      if (mounted.current && current === detailGeneration.current) { setDetail(value); setComment(""); setChannels([]); setChannelId(""); }
    } catch (error) { if (mounted.current && current === detailGeneration.current) setReadError(message(error)); }
  };
  const mutate = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true); setWriteError(""); setNotice("");
    try { await action(); if (mounted.current) { setNotice(success); await load(); } }
    catch (error) { if (mounted.current) setWriteError(message(error)); }
    finally { if (mounted.current) setBusy(false); }
  };
  const edit = (item: Item) => { setDraft(structuredClone(item)); setDraftVersion(library?.version ?? 0); setWriteError(""); };
  const add = () => {
    const common = { id: "", name: "", enabled: true };
    edit(kind === "skills" ? { ...common, description: "", keywords: [], domains: ["netshop"], body: "" } : kind === "templates" ? { ...common, scene: "weekly", format: "html", sections: ["经营结论", "数据范围与完整性", "关键指标", "异常与原因", "行动建议"], dataSteps: "", writingRules: "" } : { ...common, scene: "weekly", description: "", templateId: "weekly-report", skillIds: ["shop-diagnosis"] });
  };
  const save = () => mutate(async () => {
    const item = { ...draft! };
    if (item.sections) item.sections = item.sections.map(s => s.trim()).filter(Boolean);
    if (item.keywords) item.keywords = item.keywords.map(s => s.trim()).filter(Boolean);
    const result = await post<{ item: Library }>("/api/ai/report-library", { kind, item, expectedVersion: draftVersion });
    if (mounted.current) { setLibrary(result.item); setDraft(null); setHistorical(null); }
  }, "已保存新版本；正在运行的任务继续使用原版本。");
  const launch = (dryRun: boolean) => mutate(async () => {
    const value = { pipelineId: selected!.id, expectedVersion: library!.version, scope: filters, dryRun };
    const signature = JSON.stringify(value);
    if (pending.current?.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
    const result = await post<{ item: Run }>("/api/ai/reports", { ...value, clientRequestId: pending.current.id });
    pending.current = null;
    if (mounted.current) { setSelected(null); await openDetail(result.item.id); }
  }, dryRun ? "空跑已排队，不调用模型，不发送通知。" : "已进入持久队列，可关闭页面；完成分析后等待人工复核。");
  const importSkill = async (file?: File) => {
    if (!file) return;
    try {
      if (!file.name.endsWith(".md") || file.size > 65536) throw new Error("请导入64 KiB以内的本系统Markdown技能文件。");
      edit(parseReportSkill(await file.text()));
      setNotice("已载入导入草稿；保存前可检查，同标识会追加新版本。");
    } catch (error) { setWriteError(message(error)); }
  };
  const change = (value: Partial<Item>) => setDraft(current => current ? { ...current, ...value } : null);
  const review = (node: Node, decision: string) => mutate(async () => {
    await post("/api/ai/workflow-runs/" + detail!.workflow.id + "/nodes/" + node.key + "/review", { expectedVersion: node.version, decision, comment });
    await openDetail(detail!.item.id);
  }, decision === "approve" ? "复核已通过，等待后台收尾后即可交付。" : "已拒绝，保留本次结果，不自动重跑。");
  const control = (action: string) => mutate(async () => {
    await post("/api/ai/workflow-runs/" + detail!.workflow.id + "/" + action, { expectedVersion: detail!.workflow.version });
    await openDetail(detail!.item.id);
  }, action === "cancel" ? "已取消任务，历史结果保留。" : "已恢复可安全续跑的任务。");
  const link = (format: string, isDraft = false) => "/api/ai/reports/" + detail!.item.id + "/content?format=" + format + (isDraft ? "&draft=true" : "");
  const isBusiness = detail?.snapshot?.schemaVersion === "business-report-v1";
  const isScreening = detail?.snapshot?.executionProfile === SCREENING_PROFILE;
  const isPromotion = detail?.snapshot?.executionProfile === PROMOTION_PROFILE;
  let promotionContentReady = false;
  if (isPromotion && detail?.screening !== undefined) {
    try { promotionContentReady = projectPromotionReport(detail).scan === "verified"; }
    catch { promotionContentReady = false; }
  }
  return <div className="ai-report-workbench">
    <section className="panel report-intro"><div><span className="eyebrow">{kind === "templates" ? "REPORT LIBRARY" : kind === "skills" ? "METHOD LIBRARY" : "REPEATABLE ANALYSIS"}</span><h2>{labels[kind]}</h2><p>{kind === "templates" ? "固定章节与写作要求，让每次交付都有一致结构和可核查来源。" : kind === "skills" ? "沉淀分析方法。按触发词加载正文，聊天与 Agent 共用；每个任务固定一个版本。" : "选择场景和范围，自动取数与分析，人工复核后交付报告。"}</p></div><div className="report-actions"><button className="secondary-button" onClick={() => void load()} disabled={loading}>刷新</button><button className="secondary-button" onClick={() => setHistoryOpen(!historyOpen)}>版本记录</button><button className="primary-button" onClick={add} disabled={!library || busy}>新增{kind === "skills" ? "技能" : kind === "templates" ? "模板" : "流水线"}</button></div></section>
    {kind === "pipelines" && <AiBusinessWorkbench onReportCreated={id => { void openDetail(id); void load(); }} />}
    {readError && <div role="alert" className="report-error">读取失败：{readError}</div>}
    {writeError && <div role="alert" className="report-error">操作未完成：{writeError}</div>}
    {notice && <div role="status" className="report-note">{notice}</div>}
    {loading && !library && <section className="panel" role="status">正在读取资源库…</section>}
    {historyOpen && <section className="panel"><h3>版本记录</h3><p>恢复单项时，先查看旧版本并点“编辑”，保存会追加到当前版本；旧报告保留原快照。</p><div className="report-actions"><button onClick={() => setHistorical(null)}>当前 v{library?.version}</button>{history.map(h => <button key={h.version} onClick={() => void mutate(async () => { const value = await api<{ item: Library }>("/api/ai/report-library?version=" + h.version); if (mounted.current) setHistorical(value.item); }, "正在查看历史版本 v" + h.version)}>v{h.version} · {h.created_by}</button>)}<button disabled={historyPage === 1} onClick={() => setHistoryPage(v => v-1)}>上一页</button><button disabled={!historyMore} onClick={() => setHistoryPage(v => v+1)}>下一页</button></div></section>}
    {kind === "skills" && <div className="report-note">首版支持文字技能，最多按触发词匹配3条；领域需与实际工具目录关联。<label className="report-import">导入 .md<input type="file" accept=".md" onChange={e => { void importSkill(e.target.files?.[0]); e.target.value = ""; }} /></label></div>}
    {historical && <div className="report-note">正在查看 v{historical.version}，启动任务须返回当前版本。<button onClick={() => setHistorical(null)}>返回当前</button></div>}
    <div className="report-grid">{(historical ?? library)?.config[kind].map(item => <article key={item.id} className="panel report-card"><div className="report-card-top"><span className="report-badge">{kind === "skills" ? "分析方法" : scenes[item.scene ?? ""]}</span><span>{item.enabled ? "已启用" : "已停用"}</span></div><h3>{item.name}</h3><p>{item.description || String(item.sections?.length) + " 个章节 · " + (item.format === "xlsx" ? "Excel 工作簿" : "HTML 报告")}</p>{kind === "templates" && <ol>{item.sections?.map(title => <li key={title}>{title}</li>)}</ol>}{kind === "skills" && <p className="report-muted">触发词：{item.keywords?.join("、")}</p>}{kind === "pipelines" && <div className="report-flow" aria-label="流水线步骤"><span>核验取数</span><b>→</b><span>分析报告</span><b>→</b><span>人工复核</span><b>→</b><span>交付</span></div>}<div className="report-actions"><button className="secondary-button" onClick={() => edit(item)} disabled={busy}>编辑</button>{kind === "skills" && <button className="secondary-button" onClick={() => exportSkill(item)}>导出 .md</button>}{kind === "pipelines" && <button className="primary-button" disabled={!item.enabled || !!historical || busy} onClick={() => { setSelected(item); setWriteError(""); }}>配置并运行</button>}</div></article>)}</div>
    {kind === "pipelines" && <section className="panel"><div className="section-header"><h3>我的报告任务</h3><span>第 {page} 页</span></div><div className="report-table"><table><thead><tr><th>报告</th><th>范围</th><th>版本</th><th>状态</th><th>操作</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td>{run.name}<small>{run.dryRun ? "空跑" : "正式分析"} · {new Date(run.createdAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</small></td><td>{run.scope.platform} · {run.scope.shop}<small>{run.scope.startDate} 至 {run.scope.endDate}</small></td><td>v{run.version}</td><td>{statuses[run.status] || run.status}</td><td><button className="row-action" onClick={() => void openDetail(run.id)}>查看与复核</button></td></tr>)}</tbody></table></div>{!runs.length && <p className="report-muted">还没有报告任务。先选择一个场景，空跑验证流程后再启动分析。</p>}<div className="report-actions"><button disabled={page === 1 || loading} onClick={() => setPage(p => p-1)}>上一页</button><button disabled={!more || loading} onClick={() => setPage(p => p+1)}>下一页</button></div></section>}
    {draft && <section className="panel report-editor" aria-label="资源编辑器"><div className="section-header"><h3>编辑{labels[kind]}</h3><button onClick={() => setDraft(null)} disabled={busy}>关闭</button></div><form onSubmit={e => { e.preventDefault(); void save(); }}><div className="report-fields"><label>名称<input required maxLength={80} value={draft.name} onChange={e => change({ name: e.target.value })} /></label><label>标识<input required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={64} value={draft.id} onChange={e => change({ id: e.target.value })} /><small>同标识保存会追加新版本；更改标识会新增一项。</small></label></div><label className="report-checkbox"><input type="checkbox" checked={draft.enabled} onChange={e => change({ enabled: e.target.checked })} />启用</label>
      {kind !== "skills" && <label>场景<select value={draft.scene} onChange={e => change({ scene: e.target.value, ...(kind === "pipelines" ? { templateId: library?.config.templates.find(t => t.scene === e.target.value)?.id ?? "" } : {}) })}>{Object.entries(scenes).map(([id,name]) => <option key={id} value={id}>{name}</option>)}</select></label>}
      {kind === "templates" && <><label>主交付格式<select value={draft.format} onChange={e => change({ format: e.target.value })}><option value="html">HTML 报告 + Excel 明细</option><option value="xlsx">Excel 工作簿 + HTML 报告</option></select></label><label>章节骨架（每行一章，最多8章）<textarea required rows={6} value={draft.sections?.join("\n")} onChange={e => change({ sections: e.target.value.split("\n") })} /></label><label>取数步骤<textarea required rows={4} maxLength={800} value={draft.dataSteps} onChange={e => change({ dataSteps: e.target.value })} /></label><label>写作规范<textarea required rows={4} maxLength={800} value={draft.writingRules} onChange={e => change({ writingRules: e.target.value })} /></label></>}
      {kind === "skills" && <><label>一句话描述<input required maxLength={240} value={draft.description} onChange={e => change({ description: e.target.value })} /></label><label>触发词（每行一词，最多8个）<textarea required rows={3} value={draft.keywords?.join("\n")} onChange={e => change({ keywords: e.target.value.split("\n") })} /></label><fieldset><legend>适用领域</legend>{Object.entries(domains).map(([id,name]) => <label key={id} className="report-checkbox"><input type="checkbox" checked={draft.domains?.includes(id) ?? false} onChange={e => change({ domains: e.target.checked ? [...(draft.domains ?? []), id] : draft.domains?.filter(d => d !== id) })} />{name}</label>)}</fieldset><label>方法正文（Markdown，最多2000字）<textarea required rows={10} maxLength={2000} value={draft.body} onChange={e => change({ body: e.target.value })} /></label></>}
      {kind === "pipelines" && <><label>说明<input required maxLength={240} value={draft.description} onChange={e => change({ description: e.target.value })} /></label><label>报告模板<select value={draft.templateId} onChange={e => change({ templateId: e.target.value })}>{library?.config.templates.filter(t => t.scene === draft.scene).map(t => <option key={t.id} value={t.id}>{t.name}{!t.enabled ? "（已停用）" : ""}</option>)}</select></label><fieldset><legend>使用技能（1–3项）</legend>{library?.config.skills.map(s => <label className="report-checkbox" key={s.id}><input type="checkbox" checked={draft.skillIds?.includes(s.id) ?? false} onChange={e => change({ skillIds: e.target.checked ? [...(draft.skillIds ?? []), s.id] : draft.skillIds?.filter(id => id !== s.id) })} />{s.name}{!s.enabled ? "（已停用）" : ""}</label>)}</fieldset><p className="report-note">每次手动启动。人工复核是固定步骤，通知须在复核后单独确认。</p></>}
      <button className="primary-button" disabled={busy}>{busy ? "保存中…" : "保存为新版本（基于 v" + draftVersion + "）"}</button></form></section>}
    {selected && <section className="panel report-editor"><div className="section-header"><h3>{selected.name} · 运行范围</h3><button onClick={() => setSelected(null)} disabled={busy}>关闭</button></div><form onSubmit={e => { e.preventDefault(); void launch(false); }}><div className="report-fields"><label>平台<input required maxLength={100} value={filters.platform} onChange={e => setFilters({ ...filters, platform: e.target.value })} /></label><label>{selected.scene === "inventory" ? "仓别或监控范围" : "精确店铺名称"}<input required maxLength={100} value={filters.shop} onChange={e => setFilters({ ...filters, shop: e.target.value })} /></label><label>开始日期（含）<input type="date" required value={filters.startDate} onChange={e => setFilters({ ...filters, startDate: e.target.value })} /></label><label>结束日期（含）<input type="date" required value={filters.endDate} onChange={e => setFilters({ ...filters, endDate: e.target.value })} /></label></div><p>正式运行会调用当前默认模型，使用原有额度与权限；无需保持网页打开。空跑只检查流程结构，不代表数据或模型已验收。</p><div className="report-actions"><button type="button" className="secondary-button" disabled={busy || !filters.shop || !filters.startDate || !filters.endDate} onClick={() => void launch(true)}>空跑（不调用模型）</button><button className="primary-button" disabled={busy}>开始分析</button></div></form></section>}
    {detail && <section className="panel report-detail"><div className="section-header"><div><h3>{detail.item.name}</h3><small>{detail.item.id} · {statuses[detail.item.status]}</small></div><div className="report-actions"><button onClick={() => void openDetail(detail.item.id)}>刷新详情</button><button onClick={() => { detailGeneration.current++; detailController.current?.abort(); setDetail(null); }}>关闭</button></div></div><p>{detail.item.scope.platform} · {detail.item.scope.shop} · {detail.item.scope.startDate} 至 {detail.item.scope.endDate}</p><p>模板：{detail.item.template} · 资源库 v{detail.item.version} · 方法：{detail.item.skills.join("、")}</p><div className="report-flow">{detail.workflow.nodes.map(n => <span key={n.id}>{n.key} · {statuses[n.status] || n.status}</span>)}</div>{detail.contentError && <p className="report-note">{detail.contentError}</p>}{(!isPromotion || promotionContentReady) && detail.sections?.map(s => <section key={s.title}><h4>{s.title}</h4><p className="report-body">{s.body}</p></section>)}{!isScreening && !isPromotion && <details><summary>原始节点输出与复核依据</summary>{detail.workflow.nodes.map(n => <div key={n.id}><h4>{n.key}</h4><pre>{JSON.stringify(n.output ?? {}, null, 2)}</pre></div>)}</details>}
      {isScreening && <AiBusinessScreeningDetail key={detail.item.id+":"+detail.workflow.version} reportId={detail.item.id} preparation={detail.screeningPreparation} screening={detail.screening} professionalAnalyses={detail.professionalAnalyses} budget={detail.budget} />}
      {isPromotion && <AiBusinessPromotionDetail key={detail.item.id+":"+detail.workflow.version} detail={detail} />}
      {!isScreening && !isPromotion && detail.budget && detail.snapshot?.question && <AiBusinessBudget key={detail.item.id} reportId={detail.item.id} question={detail.snapshot.question} budget={detail.budget} onCreated={id => void openDetail(id)} />}
      {detail.sections && (!isPromotion || promotionContentReady) && !detail.item.dryRun && (isBusiness ? <AiBusinessReportFiles key={detail.item.id} reportId={detail.item.id} allowFormal={detail.item.status === "completed"} volumeMode={detail.snapshot?.evidenceProtocol === "reference-v2"} formalOnly={isPromotion} promotionTrial={isPromotion} /> : <div className="report-actions"><a className="secondary-button" href={link("html", true)}>下载草稿 HTML</a><a className="secondary-button" href={link("xlsx", true)}>下载草稿与取数明细</a></div>)}
      {detail.workflow.nodes.filter(n => n.type === "human_review" && n.status === "waiting_review" && detail.workflow.status === "waiting_review").map(n => <div key={n.id} className="report-review"><label>复核意见<textarea rows={3} maxLength={2000} value={comment} onChange={e => setComment(e.target.value)} /></label><div className="report-actions"><button className="primary-button" disabled={busy || !detail.sections || (isPromotion && !promotionContentReady)} onClick={() => void review(n, "approve")}>通过复核</button><button className="secondary-button" disabled={busy} onClick={() => void review(n, "reject")}>拒绝</button></div></div>)}
      {detail.item.status === "completed" && !detail.item.dryRun && <div className="report-review"><h4>交付报告</h4>{!isBusiness && <p className="report-note">取数明细仅含本次实际收到的有界结果。数据覆盖、分页和截断以来源回执为准。</p>}<div className="report-actions">{!isBusiness && <><a className="primary-button" href={link("html")}>下载正式 HTML</a><a className="secondary-button" href={link("xlsx")}>下载 Excel 与明细</a></>}<button disabled={busy || !!detail.delivery} onClick={() => void mutate(async () => { const data = await api<{ items: typeof channels }>("/api/ai/channels"); if (mounted.current) setChannels(data.items.filter(c => c.sendEnabled && c.status === "enabled")); }, "已读取可用通知渠道。")}>选择通知渠道</button></div>{channels.length > 0 && !detail.delivery && <div className="report-actions"><select aria-label="通知渠道" value={channelId} onChange={e => setChannelId(e.target.value)}><option value="">请选择接收渠道</option>{channels.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select><button disabled={!channelId || busy} onClick={() => void mutate(async () => { await post("/api/ai/reports/" + detail.item.id + "/send", { channelId, targetDigest: channels.find(c => c.id === channelId)?.targetDigest }); await openDetail(detail.item.id); }, "已发送报告完成通知。")}>确认发送完成通知</button></div>}{detail.delivery && <p>通知状态：{detail.delivery.status === "sent" ? "已发送" : "已预留或结果未知，不重复发送"}</p>}</div>}
      {["queued","running","paused","waiting_review"].includes(detail.item.status) && <button disabled={busy} onClick={() => void control("cancel")}>取消任务</button>}
      {detail.workflow.retryable && ["paused","failed"].includes(detail.item.status) && <button disabled={busy} onClick={() => void control("resume")}>安全恢复</button>}
    </section>}
  </div>;
}
