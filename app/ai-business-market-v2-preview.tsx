"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { correspondingMarketBaselineDate, marketPairs, marketSelector, validateMarketPreview, validateParkedCreate,
  MARKET_V2_VIEWS, type MarketPair, type MarketPreview, type MarketSource, type MarketView } from "@/lib/ai/business-market-v2-preview";

type Report = { id: string; status: string; createdAt: string };
type DirectoryPage = { schemaVersion: string; runId: string; evidenceVersion: number; catalogDigest: string;
  offset: number; total: number; returned: number; nextOffset: number | null;
  items: (MarketSource & { ordinal: number })[] };
type Pending = { schemaVersion: 1; principalKey: string; evidenceRunId: string; sourceReportId: string; body: string };
type LastPreview = { schemaVersion: 1; principalKey: string; evidenceRunId: string; sourceReportId: string; reportId: string };
const viewNames: Record<MarketView, string> = { price_band_summary: "价格带汇总", price_band_members: "价格带成员", rank_entry_exit: "双日进出榜" };
const stateKey = (principalKey: string, runId: string) => `ai-market-v2-parked-pending-v1:${principalKey}:${runId}`;
const lastKey = (principalKey: string, runId: string) => `ai-market-v2-parked-last-v1:${principalKey}:${runId}`;
const message = (error: unknown) => error instanceof Error ? error.message : "请求失败，请核验当前状态。";
const queryLabel = (query: Record<string, string>) => ["category", "scope", "rankingDimension", "priceBandFilter", "startDate", "endDate"]
  .map(key => query[key]).filter(Boolean).join(" · ");
const cell = (value: unknown) => value === null ? "缺失" : typeof value === "string" ? value : JSON.stringify(value);

async function get<T>(url: string, signal: AbortSignal): Promise<T> {
  const { response, data } = await fetchBoundedJson({ url, signal, timeoutMs: 30_000, maxBytes: 2 * 1024 * 1024,
    init: { method: "GET", cache: "no-store" } });
  if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data ? String(data.error) : `读取失败（${response.status}）`);
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("市场预览回执无效。");
  return data as T;
}
async function post<T>(url: string, body: string, signal: AbortSignal): Promise<T> {
  const { response, data } = await fetchBoundedJson({ url, signal, timeoutMs: 40_000, maxBytes: 2 * 1024 * 1024,
    init: { method: "POST", cache: "no-store", headers: { "content-type": "application/json" }, body } });
  if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data ? String(data.error) : `创建失败（${response.status}）`);
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("市场停放报告回执无效。");
  return data as T;
}

export default function AiBusinessMarketV2Preview({ principalKey, evidenceRunId, evidenceVersion, catalogDigest,
  sourceCount, reports, disabled }: { principalKey: string; evidenceRunId: string; evidenceVersion: number;
  catalogDigest: string; sourceCount: number; reports: Report[]; disabled: boolean }) {
  const [enabled, setEnabled] = useState(false), [sources, setSources] = useState<MarketSource[]>([]);
  const [directoryError, setDirectoryError] = useState(""), [loading, setLoading] = useState(false), [retry, setRetry] = useState(0);
  const [sourceReportId, setSourceReportId] = useState(""), [pairIndex, setPairIndex] = useState(0);
  const [currentDate, setCurrentDate] = useState(""), [baselineDate, setBaselineDate] = useState(""), [boundary, setBoundary] = useState("1000.00");
  const [pending, setPending] = useState<Pending | null>(null), [createdId, setCreatedId] = useState("");
  const [last, setLast] = useState<LastPreview | null>(null);
  const [summary, setSummary] = useState<MarketPreview | null>(null), [page, setPage] = useState<MarketPreview | null>(null);
  const [view, setView] = useState<MarketView>("price_band_summary"), [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const op = useRef<AbortController | null>(null);
  const pairs = useMemo(() => marketPairs(sources), [sources]);
  const chosen: MarketPair | undefined = pairs[pairIndex];
  const locked = disabled || busy || !!pending;

  useEffect(() => {
    const ctl = new AbortController();
    void get<{ schemaVersion: string; enabled: boolean }>("/api/ai/market-v2-preview-status", ctl.signal)
      .then(value => { if (!ctl.signal.aborted && value.schemaVersion === "business-market-v2-preview-status-v1") setEnabled(value.enabled === true); })
      .catch(() => { if (!ctl.signal.aborted) setEnabled(false); });
    return () => ctl.abort();
  }, []);
  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController(); let active = true;
    setSources([]); setDirectoryError(""); setLoading(true);
    void (async () => {
      try {
        const collected: MarketSource[] = [];
        let next: number | null = 0;
        while (next !== null) {
          const offset: number = next;
          const value: DirectoryPage = await get<DirectoryPage>(`/api/ai/business-evidence/${encodeURIComponent(evidenceRunId)}/sources?offset=${offset}&limit=10`, ctl.signal);
          if (!active || ctl.signal.aborted) return;
          if (value.schemaVersion !== "business-evidence-directory-page-v2" || value.runId !== evidenceRunId
            || value.evidenceVersion !== evidenceVersion || value.catalogDigest !== catalogDigest
            || value.offset !== offset || value.total !== sourceCount || !Array.isArray(value.items)
            || value.returned !== value.items.length || value.returned < 1 || value.returned > 10
            || value.items.some((item, index) => item.ordinal !== offset + index + 1)
            || value.nextOffset !== (offset + value.returned < sourceCount ? offset + value.returned : null))
            throw new Error("封存来源目录版本或分页发生变化，请刷新任务后重读。");
          collected.push(...value.items);
          next = value.nextOffset;
          if (collected.length > 48) throw new Error("来源目录超出固定容量。");
        }
        const matches = marketPairs(collected);
        if (matches.length === 0) throw new Error("当前封存证据没有同条件的京东市场本期与比较基期来源。");
        setSources(collected); setPairIndex(0);
      } catch (caught) { if (active && !ctl.signal.aborted) setDirectoryError(message(caught)); }
      finally { if (active && !ctl.signal.aborted) setLoading(false); }
    })();
    return () => { active = false; ctl.abort(); };
  }, [enabled, evidenceRunId, evidenceVersion, catalogDigest, sourceCount, retry]);
  useEffect(() => {
    if (!enabled) return;
    try {
      const previous = sessionStorage.getItem(lastKey(principalKey, evidenceRunId));
      if (previous) {
        const value = JSON.parse(previous) as LastPreview;
        if (value.schemaVersion === 1 && value.principalKey === principalKey && value.evidenceRunId === evidenceRunId
          && /^[A-Za-z0-9_-]{1,160}$/.test(value.reportId)
          && reports.some(report => report.id === value.sourceReportId)) setLast(value);
      }
      const raw = sessionStorage.getItem(stateKey(principalKey, evidenceRunId));
      if (!raw) return;
      const value = JSON.parse(raw) as Pending;
      const body = JSON.parse(value.body) as Record<string, unknown>;
      if (value.schemaVersion !== 1 || value.principalKey !== principalKey || value.evidenceRunId !== evidenceRunId
        || !reports.some(report => report.id === value.sourceReportId) || body.sourceReportId !== value.sourceReportId
        || body.schemaVersion !== "business-market-v2-parked-create-v1" || typeof body.clientRequestId !== "string")
        throw new Error("保留的市场创建请求与当前账号或报告不一致。");
      setPending(value); setSourceReportId(value.sourceReportId);
    } catch (caught) { setError(message(caught) + " 请核验会话存储；未重复创建报告。"); }
  }, [enabled, principalKey, evidenceRunId, reports]);
  useEffect(() => () => op.current?.abort(), []);

  async function readPreview(reportId: string, sourceId: string, selected?: MarketView, start = 0) {
    op.current?.abort(); const ctl = new AbortController(); op.current = ctl; setBusy(true); setError("");
    setPage(null);
    if (!selected) { setSummary(null); setCreatedId(""); }
    try {
      const query = selected ? `?view=${selected}&offset=${start}&limit=20` : "";
      const raw = await get<unknown>(`/api/ai/market-v2-parked-reports/${encodeURIComponent(reportId)}/preview${query}`, ctl.signal);
      if (ctl.signal.aborted || op.current !== ctl) return;
      const value = validateMarketPreview(raw, { reportId, sourceReportId: sourceId, ...(selected ? { view: selected, offset: start } : {}) });
      if (selected && summary && (value.marketManifestDigest !== summary.marketManifestDigest
        || JSON.stringify(value.tables) !== JSON.stringify(summary.tables)))
        throw new Error("市场样本材料版本已变化，请从停放报告摘要重新读取。");
      if (selected) { setPage(value); setView(selected); setOffset(start); }
      else { setSummary(value); setPage(null); setCreatedId(reportId); }
    } catch (caught) { if (!ctl.signal.aborted && op.current === ctl) setError(message(caught)); }
    finally { if (op.current === ctl) { op.current = null; setBusy(false); } }
  }
  async function submit(value: Pending) {
    op.current?.abort(); const ctl = new AbortController(); op.current = ctl; setBusy(true); setError("");
    try {
      const raw = await post<unknown>("/api/ai/market-v2-parked-reports", value.body, ctl.signal);
      if (ctl.signal.aborted || op.current !== ctl) return;
      const reportId = validateParkedCreate(raw, value.sourceReportId);
      try { sessionStorage.removeItem(stateKey(principalKey, evidenceRunId)); } catch { /* The same request can be replayed after reload. */ }
      setPending(null); setCreatedId(reportId);
      const saved: LastPreview = { schemaVersion: 1, principalKey, evidenceRunId,
        sourceReportId: value.sourceReportId, reportId };
      try { sessionStorage.setItem(lastKey(principalKey, evidenceRunId), JSON.stringify(saved)); } catch { /* Visible report ID remains in this session. */ }
      setLast(saved);
      // This GET replays owner-bound market material; it still does not dispatch Agents.
      void readPreview(reportId, value.sourceReportId);
    } catch (caught) { if (!ctl.signal.aborted && op.current === ctl) setError(message(caught) + " 已保留同一请求编号，可重试核验，不会切换为正式分析。"); }
    finally { if (op.current === ctl) { op.current = null; setBusy(false); } }
  }
  function create() {
    try {
      if (!chosen || !sourceReportId || !reports.some(report => report.id === sourceReportId)) throw new Error("请选择此证据关联的来源报告和市场双窗口。");
      const selector = marketSelector(chosen, currentDate, baselineDate, boundary);
      const body = JSON.stringify({ schemaVersion: "business-market-v2-parked-create-v1",
        clientRequestId: `market-preview-${crypto.randomUUID()}`, sourceReportId, marketSelector: selector });
      const value: Pending = { schemaVersion: 1, principalKey, evidenceRunId, sourceReportId, body };
      const saved = JSON.stringify(value);
      sessionStorage.setItem(stateKey(principalKey, evidenceRunId), saved);
      if (sessionStorage.getItem(stateKey(principalKey, evidenceRunId)) !== saved) throw new Error("同次提交记录未能可靠保存。");
      setPending(value); setSummary(null); setPage(null); setCreatedId("");
      void submit(value);
    } catch (caught) { setError(message(caught) + " 未创建报告。"); }
  }

  if (!enabled) return null;
  const visiblePage = page?.page && page.reportId === createdId && page.page.view === view && page.page.offset === offset ? page.page : null;
  return <section className="bw-card" aria-label="市场样本预览"><h4>市场样本预览</h4>
    <p role="status">市场样本预览，未生成 Agent 诊断/正式文件。价格带汇总与成员来自同一 TOP 样本，不可相加；市场数据也不能计入本店、ERP 或 B 端销售。</p>
    {loading && <p role="status">正在核验封存市场来源目录…</p>}
    {directoryError && <p role="alert" className="bw-error">{directoryError} <button type="button" onClick={() => setRetry(value => value + 1)}>重读来源目录</button></p>}
    {pairs.length > 0 && <><label>已关联的词货来源报告<select aria-label="市场预览来源报告" disabled={locked} value={sourceReportId} onChange={event => { setSourceReportId(event.target.value); setSummary(null); setPage(null); setCreatedId(""); }}>
      <option value="">请选择报告</option>{reports.map(report => <option key={report.id} value={report.id}>{report.createdAt} · {report.id} · {report.status}</option>)}
    </select></label><label>同条件本期与比较基期<select aria-label="市场双窗口来源" disabled={locked} value={pairIndex} onChange={event => { setPairIndex(Number(event.target.value)); setCurrentDate(""); setBaselineDate(""); setSummary(null); setPage(null); setCreatedId(""); }}>
      {pairs.map((pair, index) => <option key={`${pair.current.key}:${pair.baseline.key}`} value={index}>{pair.current.key}（本期）/ {pair.baseline.key}（{pair.baseline.query.window === "previous" ? "环比" : "同比"}） · {queryLabel(pair.current.query)}</option>)}
    </select></label>{chosen && <div className="bw-grid"><label>本期市场观察日<input aria-label="本期市场观察日" type="date" min={chosen.current.query.startDate} max={chosen.current.query.endDate} disabled={locked} value={currentDate} onChange={event => {
        const selected = event.target.value; setCurrentDate(selected);
        try { setBaselineDate(selected ? correspondingMarketBaselineDate(selected, chosen.current.query.startDate,
          chosen.current.query.endDate, chosen.baseline.query.window) : ""); }
        catch { setBaselineDate(""); }
        setSummary(null); setPage(null); setCreatedId("");
      }} /></label>
      <label>基期对应观察日<input aria-label="基期对应观察日" type="date" readOnly value={baselineDate} /></label></div>}
      <label>价格两档分界（元）<input aria-label="价格两档分界" type="text" inputMode="decimal" disabled={locked} value={boundary} onChange={event => { setBoundary(event.target.value); setSummary(null); setPage(null); setCreatedId(""); }} /></label>
      <p>基期观察日按本期选中日期和固定环比/同比规则对应；处于比较窗口不代表当天有榜单事实。价格分界只用于本次 TOP 样本分组。</p>
      <div className="bw-actions"><button type="button" disabled={locked || !chosen || !sourceReportId || !currentDate || !baselineDate} onClick={create}>创建停放报告并读取样本</button></div></>}
    {pending && <div className="bw-pending"><p>同一次创建请求已保留，结果可能尚未确认。仅重试原请求，不启动 Agent。</p><button type="button" disabled={busy || disabled} onClick={() => void submit(pending)}>重试同一次市场预览创建</button></div>}
    {last && !pending && !summary && <button type="button" disabled={busy || disabled} onClick={() => { setSourceReportId(last.sourceReportId); void readPreview(last.reportId, last.sourceReportId); }}>重读上次停放报告的市场样本</button>}
    {error && <p role="alert" className="bw-error">{error}</p>}
    {busy && <p role="status">正在核验市场样本…</p>}
    {createdId && <p>停放报告：{createdId}；状态：已暂停，市场材料未准入。</p>}
    {summary && <><p>三张完整类型表已经在服务端复核，仅提供只读样本分页；没有同 Agent 已读回执，也没有正式报告权限。</p>
      <button type="button" disabled={busy} onClick={() => void readPreview(createdId, sourceReportId)}>重新读取停放报告摘要</button>
      <div className="bw-scroll"><table><thead><tr><th>样本表</th><th>行数</th><th>查看</th></tr></thead><tbody>{summary.tables.map(table => <tr key={table.view}><td>{table.title}</td><td>{table.rowCount}</td><td><button type="button" disabled={busy || table.rowCount === 0} onClick={() => void readPreview(createdId, sourceReportId, table.view as MarketView, 0)}>查看前 20 行</button></td></tr>)}</tbody></table></div>
      {visiblePage && <section aria-label="市场预览分页"><h5>{viewNames[view]} · {visiblePage.total} 行样本</h5><div className="bw-scroll"><table><thead><tr>{visiblePage.columns.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{visiblePage.rows.map((row, index) => <tr key={`${offset}:${index}`}>{row.map((value, column) => <td key={visiblePage.columns[column].key}>{cell(value)}</td>)}</tr>)}</tbody></table></div>
        <div className="bw-actions"><button type="button" disabled={busy || offset === 0} onClick={() => void readPreview(createdId, sourceReportId, view, Math.max(0, offset - 20))}>上一页样本</button><span>{visiblePage.total ? `${offset + 1}–${offset + visiblePage.rows.length} / ${visiblePage.total}` : "0 行"}</span><button type="button" disabled={busy || offset + visiblePage.rows.length >= visiblePage.total} onClick={() => void readPreview(createdId, sourceReportId, view, offset + 20)}>下一页样本</button></div>
      </section>}</>}
  </section>;
}
