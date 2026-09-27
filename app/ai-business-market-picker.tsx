"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { marketOptionsQuery, marketOptionSelection, validateMarketOptionsResponse,
  type MarketOption, type MarketOptionSelection, type MarketOptionsPage, type MarketOptionsQuery } from "@/lib/ai/business-market-options";

export type BusinessMarketPickerProps = { principalKey: string; disabled?: boolean;
  onSelect: (selection: MarketOptionSelection) => boolean | void; onIdentityMismatch: () => void };
type Draft = { category: string; scope: string; rankingDimension: string; priceBandFilter: string; q: string };
type PageRequest = { cursor: string | null; previousItem?: MarketOption };
type Loaded = PageRequest & { page: MarketOptionsPage; query: MarketOptionsQuery; history: PageRequest[] };
const blank: Draft = { category: "", scope: "", rankingDimension: "", priceBandFilter: "", q: "" };
const MAX_PAGES = 100;
const message = (error: unknown) => error instanceof Error ? error.message : "来源选项读取失败，请重读。";
class OptionsError extends Error {
  constructor(text: string, readonly status: number, readonly identity = false) { super(text); }
}

export default function AiBusinessMarketPicker(props: BusinessMarketPickerProps) {
  return <Picker key={props.principalKey} {...props} />;
}
function Picker({ principalKey, disabled = false, onSelect, onIdentityMismatch }: BusinessMarketPickerProps) {
  const [draft, setDraft] = useState<Draft>(blank), [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [identityLost, setIdentityLost] = useState(false);
  const controller = useRef<AbortController | null>(null), sequence = useRef(0), alive = useRef(false);
  const latest = useRef({ principalKey, disabled, onSelect, onIdentityMismatch });
  useLayoutEffect(() => { latest.current = { principalKey, disabled, onSelect, onIdentityMismatch }; },
    [principalKey, disabled, onSelect, onIdentityMismatch]);
  const blocked = disabled || identityLost || !/^[a-f0-9]{64}$/.test(principalKey);

  function abortRead() {
    sequence.current++; controller.current?.abort(); controller.current = null;
  }
  function cancel(text = "已取消读取。请从首页重新读取市场来源。") {
    abortRead();
    setBusy(false); setLoaded(null); setError(""); setNotice(text);
  }
  function edit(change: Partial<Draft>) {
    cancel("筛选已修改，请点击读取市场来源。");
    setDraft(old => ({ ...old, ...change }));
  }
  async function load(query: MarketOptionsQuery, request: PageRequest, history: PageRequest[],
    binding?: Pick<MarketOptionsPage, "revision" | "directoryGeneration" | "directoryDigest">, selection?: MarketOption) {
    if (latest.current.disabled || identityLost || !/^[a-f0-9]{64}$/.test(principalKey)) return;
    controller.current?.abort();
    const ctl = new AbortController(), current = ++sequence.current;
    controller.current = ctl; setBusy(true); setError(""); setNotice("");
    if (!selection) setLoaded(null);
    const active = () => alive.current && !ctl.signal.aborted && current === sequence.current
      && controller.current === ctl && latest.current.principalKey === principalKey && !latest.current.disabled;
    try {
      const params = new URLSearchParams({ expectedPrincipalKey: principalKey });
      for (const [key, value] of Object.entries(query)) params.set(key, value);
      if (request.cursor !== null) params.set("cursor", request.cursor);
      const { response, data } = await fetchBoundedJson({ url: `/api/ai/business-plan/market-options?${params}`,
        init: { method: "GET", cache: "no-store" }, signal: ctl.signal, timeoutMs: 30000, maxBytes: 65536 });
      if (!active()) return;
      const raw = data && typeof data === "object" ? data as Record<string, unknown> : {};
      if (raw.code === "principal_mismatch" || typeof raw.principalKey === "string" && raw.principalKey !== principalKey)
        throw new OptionsError("账号已变化，请刷新当前账号后重新选择来源。", 403, true);
      if (response.status === 403) throw new OptionsError("当前账号无权读取市场来源，请刷新账号权限后重试。", 403, true);
      if (!response.ok) throw new OptionsError(response.status === 409 ? "来源目录或游标已变化，请从首页重读并重新选择。"
        : typeof raw.error === "string" ? raw.error : `来源选项读取失败（${response.status}）。`, response.status);
      const page = await validateMarketOptionsResponse(data, { principalKey, query, cursor: request.cursor,
        ...(binding ? { revision: binding.revision, directoryGeneration: binding.directoryGeneration, directoryDigest: binding.directoryDigest } : {}), ...(request.previousItem ? { previousItem: request.previousItem } : {}) });
      if (!active()) return;
      setLoaded({ page, query, ...request, history });
      if (selection) {
        const item = page.items.find(option => option.optionKey === selection.optionKey);
        if (!item) throw new OptionsError("此来源已不在当前候选页，请从首页重新读取。", 409);
        const accepted = latest.current.onSelect(marketOptionSelection(page, item, principalKey));
        setNotice(accepted === false ? "未添加，请查看分析范围中的提示。"
          : `已添加：${item.identity.platform} · ${item.identity.category} · ${item.identity.scope} · ${item.identity.rankingDimension} · ${item.identity.priceBandFilter}。分析日期保持原设置。`);
      }
    } catch (caught) {
      if (!active()) return;
      setLoaded(null); setError(message(caught));
      if (caught instanceof OptionsError && caught.identity) {
        setIdentityLost(true); latest.current.onIdentityMismatch();
      }
    } finally {
      if (alive.current && controller.current === ctl) { controller.current = null; setBusy(false); }
    }
  }
  function first() {
    if (blocked) return;
    try {
      const query = marketOptionsQuery({ platform: "京东", ...Object.fromEntries(Object.entries(draft).filter(([, value]) => value !== "")) });
      void load(query, { cursor: null }, []);
    } catch (caught) { cancel(""); setError(message(caught)); }
  }
  useEffect(() => {
    alive.current = true;
    setLoaded(null); setBusy(false); setError(""); setNotice(""); setDraft(blank);
    if (!disabled && /^[a-f0-9]{64}$/.test(principalKey)) void load({ platform: "京东" }, { cursor: null }, []);
    return () => { alive.current = false; abortRead(); };
    // Initial/permission transitions deliberately reset pagination. Filter reads are explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [principalKey, disabled]);

  const visible = !blocked && loaded ? loaded : null;
  return <section className="bw-card" aria-label="选择市场分析来源" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
    <h4>从已导入市场来源选择</h4>
    <p>平台固定京东。每次只添加一个精确的类目、榜单范围、SKU/SPU 与原始价格带组合。</p>
    <p>导入日期仅供参考，不会修改分析日期，也不证明当前仍有事实或所选日期完整。</p>
    <fieldset disabled={blocked} style={{ minWidth: 0 }}><legend>来源筛选</legend>
      <div className="bw-grid">
        <label>市场平台<input aria-label="市场平台" value="京东" readOnly /></label>
        <label>精确类目<input aria-label="市场精确类目" value={draft.category} maxLength={400}
          placeholder="可留空，不自动改写名称" onChange={event => edit({ category: event.target.value })} /></label>
        <label>精确榜单范围<input aria-label="市场榜单范围" value={draft.scope} maxLength={400}
          placeholder="可留空，使用原始范围" onChange={event => edit({ scope: event.target.value })} /></label>
        <label>排名维度<select aria-label="市场排名维度" value={draft.rankingDimension} onChange={event => edit({ rankingDimension: event.target.value })}>
          <option value="">SKU 与 SPU</option><option value="SKU">SKU</option><option value="SPU">SPU</option>
        </select></label>
        <label>原始价格带<input aria-label="市场原始价格带" value={draft.priceBandFilter} maxLength={400}
          placeholder="可留空，精确匹配原值" onChange={event => edit({ priceBandFilter: event.target.value })} /></label>
        <label>名称搜索<input aria-label="市场名称搜索" value={draft.q} maxLength={200} placeholder="搜索类目、范围或价格带"
          onChange={event => edit({ q: event.target.value })} /></label>
      </div>
      <div className="bw-actions"><button type="button" disabled={busy} onClick={first}>读取市场来源</button>
        <button type="button" disabled={busy} onClick={first}>从首页重读</button></div>
    </fieldset>
    {busy && <div className="bw-actions"><p role="status">正在读取并核验来源…</p><button type="button" disabled={disabled} onClick={() => cancel()}>取消市场来源读取</button></div>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="bw-error" role="alert">{error}</p>}
    {!principalKey && <p>当前账号尚未核验，请先刷新任务列表。</p>}
    {visible && <>
      <p role="status">第 {visible.history.length + 1} 页 · 本页 {visible.page.items.length} 个来源 · {visible.page.pagination.hasMore ? "后面还有来源" : "已到当前筛选末页"}</p>
      {!visible.page.items.length && <p>当前筛选没有已发布来源；这不等于没有经营活动。</p>}
      <ul style={{ paddingLeft: 20 }}>{visible.page.items.map(item => <li key={item.optionKey} style={{ marginBlock: 12 }}>
        <dl style={{ margin: 0 }}>
          <div><dt style={{ display: "inline" }}>平台：</dt><dd style={{ display: "inline", margin: 0 }}>{item.identity.platform}</dd></div>
          <div><dt style={{ display: "inline" }}>类目：</dt><dd style={{ display: "inline", margin: 0 }}>{item.identity.category}</dd></div>
          <div><dt style={{ display: "inline" }}>榜单范围：</dt><dd style={{ display: "inline", margin: 0 }}>{item.identity.scope}</dd></div>
          <div><dt style={{ display: "inline" }}>排名维度：</dt><dd style={{ display: "inline", margin: 0 }}>{item.identity.rankingDimension}</dd></div>
          <div><dt style={{ display: "inline" }}>原始价格带：</dt><dd style={{ display: "inline", margin: 0 }}>{item.identity.priceBandFilter}</dd></div>
        </dl>
        <p>历史导入日期包络：{item.dateMetadata.firstDate} 至 {item.dateMetadata.lastDate}；当前覆盖待采集核验。</p>
        <button type="button" disabled={blocked || busy} aria-label={`添加市场来源 ${item.identity.platform} ${item.identity.category} ${item.identity.scope} ${item.identity.rankingDimension} ${item.identity.priceBandFilter}`}
          onClick={() => { if (!blocked) void load(visible.query, { cursor: visible.cursor, previousItem: visible.previousItem }, visible.history, visible.page, item); }}>添加此市场来源</button>
      </li>)}</ul>
      <div className="bw-actions">
        <button type="button" disabled={busy || !visible.history.length} onClick={() => {
          const previous = visible.history.at(-1); if (!blocked && previous) void load(visible.query, previous, visible.history.slice(0, -1), visible.page);
        }}>上一页市场来源</button>
        <button type="button" disabled={busy || !visible.page.pagination.hasMore || visible.history.length + 1 >= MAX_PAGES} onClick={() => {
          if (!blocked && visible.page.pagination.nextCursor && visible.history.length + 1 < MAX_PAGES) void load(visible.query,
            { cursor: visible.page.pagination.nextCursor, previousItem: visible.page.items.at(-1) },
            [...visible.history, { cursor: visible.cursor, previousItem: visible.previousItem }], visible.page);
        }}>下一页市场来源</button>
      </div>
      {visible.history.length + 1 >= MAX_PAGES && visible.page.pagination.hasMore && <p role="status">已浏览 100 页，仍有来源未展示。请缩小筛选后从首页重读。</p>}
    </>}
  </section>;
}
