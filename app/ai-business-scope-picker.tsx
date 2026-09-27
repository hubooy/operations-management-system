"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { scopeOptionsQuery, scopeOptionSelection, validateNetshopOptionsResponse,
  type NetshopOption, type NetshopOptionSelection, type NetshopOptionsPage, type NetshopOptionsQuery } from "@/lib/ai/business-scope-options";

export type BusinessScopePickerProps = { principalKey: string; disabled?: boolean;
  onSelect: (selection: NetshopOptionSelection) => boolean | void; onIdentityMismatch: () => void };
type Draft = { platform: string; shop: string; dataset: string; q: string };
type PageRequest = { cursor: string | null; previousItem?: NetshopOption };
type Loaded = PageRequest & { page: NetshopOptionsPage; query: NetshopOptionsQuery; history: PageRequest[] };
const datasets = { promotion: "推广", sku: "SKU 日销售", spu: "SPU 日销售", b2b: "B 端销售", master: "商品主数据" };
const blank: Draft = { platform: "", shop: "", dataset: "", q: "" };
const MAX_PAGES = 100;
const message = (error: unknown) => error instanceof Error ? error.message : "来源选项读取失败，请重读。";
class OptionsError extends Error {
  constructor(text: string, readonly status: number, readonly identity = false) { super(text); }
}

export default function AiBusinessScopePicker(props: BusinessScopePickerProps) {
  return <Picker key={props.principalKey} {...props} />;
}
function Picker({ principalKey, disabled = false, onSelect, onIdentityMismatch }: BusinessScopePickerProps) {
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
  function cancel(text = "已取消读取。请从首页重新读取来源。") {
    abortRead();
    setBusy(false); setLoaded(null); setError(""); setNotice(text);
  }
  function edit(change: Partial<Draft>) {
    cancel("筛选已修改，请点击读取来源。");
    setDraft(old => ({ ...old, ...change }));
  }
  async function load(query: NetshopOptionsQuery, request: PageRequest, history: PageRequest[],
    revision?: string, selection?: NetshopOption) {
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
      const { response, data } = await fetchBoundedJson({ url: `/api/ai/business-plan/netshop-options?${params}`,
        init: { method: "GET", cache: "no-store" }, signal: ctl.signal, timeoutMs: 30000, maxBytes: 65536 });
      if (!active()) return;
      const raw = data && typeof data === "object" ? data as Record<string, unknown> : {};
      if (raw.code === "principal_mismatch" || typeof raw.principalKey === "string" && raw.principalKey !== principalKey)
        throw new OptionsError("账号已变化，请刷新当前账号后重新选择来源。", 403, true);
      if (!response.ok) throw new OptionsError(response.status === 409 ? "来源目录或游标已变化，请从首页重读并重新选择。"
        : typeof raw.error === "string" ? raw.error : `来源选项读取失败（${response.status}）。`, response.status);
      const page = await validateNetshopOptionsResponse(data, { principalKey, query, cursor: request.cursor,
        ...(revision ? { revision } : {}), ...(request.previousItem ? { previousItem: request.previousItem } : {}) });
      if (!active()) return;
      setLoaded({ page, query, ...request, history });
      if (selection) {
        const item = page.items.find(option => option.optionKey === selection.optionKey);
        if (!item) throw new OptionsError("此来源已不在当前候选页，请从首页重新读取。", 409);
        const accepted = latest.current.onSelect(scopeOptionSelection(page, item, principalKey));
        setNotice(accepted === false ? "未添加，请查看分析范围中的提示。"
          : `已添加：${item.identity.platform} · ${item.identity.shop} · ${datasets[item.identity.dataset]}。分析日期保持原设置。`);
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
      const query = scopeOptionsQuery(Object.fromEntries(Object.entries(draft).filter(([, value]) => value !== "")));
      void load(query, { cursor: null }, []);
    } catch (caught) { cancel(""); setError(message(caught)); }
  }
  useEffect(() => {
    alive.current = true;
    setLoaded(null); setBusy(false); setError(""); setNotice(""); setDraft(blank);
    if (!disabled && /^[a-f0-9]{64}$/.test(principalKey)) void load({}, { cursor: null }, []);
    return () => { alive.current = false; abortRead(); };
    // Initial/permission transitions deliberately reset pagination. Filter reads are explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [principalKey, disabled]);

  const visible = !blocked && loaded ? loaded : null;
  return <section className="bw-card" aria-label="选择经营分析来源" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
    <h4>从已导入来源选择</h4>
    <p>每次只添加一个精确网店来源。ERP 渠道与市场条件请在工作台对应位置确认。</p>
    <p>导入日期仅供参考，不会修改分析日期，也不证明当前仍有事实或所选日期完整。</p>
    <fieldset disabled={blocked} style={{ minWidth: 0 }}><legend>来源筛选</legend>
      <div className="bw-grid">
        <label>来源平台<select aria-label="来源平台" value={draft.platform} onChange={event => edit({ platform: event.target.value, shop: "", dataset: "" })}>
          <option value="">全部支持平台</option><option value="京东">京东</option><option value="天猫">天猫</option>
        </select></label>
        <label>精确店铺<input aria-label="来源精确店铺" value={draft.shop} maxLength={100} disabled={!draft.platform}
          placeholder="先选择平台，可留空" onChange={event => edit({ shop: event.target.value })} /></label>
        <label>数据集<select aria-label="来源数据集" value={draft.dataset} onChange={event => edit({ dataset: event.target.value })}>
          <option value="">全部支持数据集</option>{Object.entries(datasets).map(([key, name]) => <option key={key} value={key}
            disabled={draft.platform === "天猫" && (key === "sku" || key === "b2b")}>{name}</option>)}
        </select></label>
        <label>名称搜索<input aria-label="来源名称搜索" value={draft.q} maxLength={100} placeholder="按平台或店铺名称筛选"
          onChange={event => edit({ q: event.target.value })} /></label>
      </div>
      <div className="bw-actions"><button type="button" disabled={busy} onClick={first}>读取来源</button>
        <button type="button" disabled={busy} onClick={first}>从首页重读</button></div>
    </fieldset>
    {busy && <div className="bw-actions"><p role="status">正在读取并核验来源…</p><button type="button" disabled={disabled} onClick={() => cancel()}>取消来源读取</button></div>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="bw-error" role="alert">{error}</p>}
    {!principalKey && <p>当前账号尚未核验，请先刷新任务列表。</p>}
    {visible && <>
      <p role="status">第 {visible.history.length + 1} 页 · 本页 {visible.page.items.length} 个来源 · {visible.page.pagination.hasMore ? "后面还有来源" : "已到当前筛选末页"}</p>
      {!visible.page.items.length && <p>当前筛选没有已发布来源；这不等于没有经营活动。</p>}
      <ul style={{ paddingLeft: 20 }}>{visible.page.items.map(item => <li key={item.optionKey} style={{ marginBlock: 12 }}>
        <p>{item.identity.platform} · {item.identity.shop} · {datasets[item.identity.dataset]}</p>
        <p>{item.identity.dataset === "master" ? `历史已发布快照：${item.dateMetadata.snapshotDate ?? "未知"}`
          : `历史导入日期包络：${item.dateMetadata.firstDate ?? "未知"} 至 ${item.dateMetadata.lastDate ?? "未知"}`}；当前覆盖待采集核验。</p>
        <button type="button" disabled={blocked || busy} aria-label={`添加来源 ${item.identity.platform} ${item.identity.shop} ${item.identity.dataset}`}
          onClick={() => { if (!blocked) void load(visible.query, { cursor: visible.cursor, previousItem: visible.previousItem }, visible.history, visible.page.revision, item); }}>添加此来源</button>
      </li>)}</ul>
      <div className="bw-actions">
        <button type="button" disabled={busy || !visible.history.length} onClick={() => {
          const previous = visible.history.at(-1); if (!blocked && previous) void load(visible.query, previous, visible.history.slice(0, -1), visible.page.revision);
        }}>上一页来源</button>
        <button type="button" disabled={busy || !visible.page.pagination.hasMore || visible.history.length + 1 >= MAX_PAGES} onClick={() => {
          if (!blocked && visible.page.pagination.nextCursor && visible.history.length + 1 < MAX_PAGES) void load(visible.query,
            { cursor: visible.page.pagination.nextCursor, previousItem: visible.page.items.at(-1) },
            [...visible.history, { cursor: visible.cursor, previousItem: visible.previousItem }], visible.page.revision);
        }}>下一页来源</button>
      </div>
      {visible.history.length + 1 >= MAX_PAGES && visible.page.pagination.hasMore && <p role="status">已浏览 100 页，仍有来源未展示。请缩小筛选后从首页重读。</p>}
    </>}
  </section>;
}
