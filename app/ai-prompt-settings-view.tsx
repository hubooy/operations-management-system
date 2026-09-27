"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Rule = { id: string; name: string; trigger: string; domains: string[]; enabled: boolean; source: string; body: string };
type Config = { globalPrompt: string; commonRules: string; rules: Rule[] };
type Item = { version: number; config: Config; createdAt: string | null; createdBy: string; restoredFrom: number | null };
type History = { version: number; created_by: string; created_at: string; restored_from: number | null };
type ResponseData = { item: Item; defaults: Config; domains: Record<string, string>; history: History[]; hasMore: boolean; page: number };
const date = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "尚未保存";

export default function AiPromptSettingsView() {
  const [data, setData] = useState<ResponseData | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [tab, setTab] = useState<"global" | "business" | "history">("global");
  const [selected, setSelected] = useState<string | null>(null);
  const [past, setPast] = useState<Item | null>(null);
  const [preview, setPreview] = useState(false);
  const [domain, setDomain] = useState("inventory");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    abort.current?.abort();
    const controller = new AbortController(); abort.current = controller;
    const current = ++generation.current;
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/ai/prompt-settings", { cache: "no-store", signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "配置读取失败");
      if (generation.current !== current) return;
      setSelected(result.item.config.rules[0]?.id ?? null); setData(result); setDraft(structuredClone(result.item.config)); setPast(null); setUncertain(false);
    } catch (reason) { if (!controller.signal.aborted && generation.current === current) setError(reason instanceof Error ? reason.message : "配置读取失败"); }
    finally { if (generation.current === current) setBusy(false); }
  }, []);
  useEffect(() => {
    const requestGeneration = generation; const requestAbort = abort;
    void load();
    return () => { ++requestGeneration.current; requestAbort.current?.abort(); };
  }, [load]);
  async function save(restore?: number) {
    if (!data || !draft || busy || uncertain) return;
    setBusy(true); setError(""); setNotice("");
    const current = generation.current;
    try {
      const response = await fetch("/api/ai/prompt-settings", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(restore === undefined ? { action: "save", expectedVersion: data.item.version, config: draft } : { action: "restore", expectedVersion: data.item.version, restoreVersion: restore }) });
      const result = await response.json();
      if (current !== generation.current) return;
      if (!response.ok) { if (response.status === 409 || response.status >= 500) setUncertain(true); throw new Error(result.error || "保存失败"); }
      await load();
      setNotice(`已保存为 v${result.item.version}，从下一次提问开始采用。正在执行的回答沿用原版本。`);
    } catch (reason) { if (current === generation.current) { setError(reason instanceof Error ? reason.message : "保存结果未确认，请重新加载核对版本"); setUncertain(true); } }
    finally { if (current === generation.current) setBusy(false); }
  }
  async function history(version?: number) {
    if (!data || busy) return;
    setBusy(true); setError("");
    const current = generation.current;
    try {
      const response = await fetch(`/api/ai/prompt-settings?${version === undefined ? `page=${data.page + 1}` : `version=${version}`}`, { cache: "no-store", signal: abort.current?.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "历史读取失败");
      if (current !== generation.current) return;
      if (version === undefined) setData({ ...data, history: [...data.history, ...result.history], page: result.page, hasMore: result.hasMore });
      else setPast(result.item);
    } catch (reason) { if (current === generation.current) setError(reason instanceof Error ? reason.message : "历史读取失败"); }
    finally { if (current === generation.current) setBusy(false); }
  }
  if (!data || !draft) return <section className="panel data-state" role={error ? "alert" : "status"}><h2>配置设置</h2><p>{error || "正在读取全局提示词与业务口径…"}</p>{error && <button className="secondary-button" onClick={() => void load()}>重新加载</button>}</section>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.item.config);
  const rule = draft.rules.find(r => r.id === selected);
  const update = (patch: Partial<Rule>) => setDraft({ ...draft, rules: draft.rules.map(r => r.id === selected ? { ...r, ...patch } : r) });
  const add = () => {
    const id = `rule-${crypto.randomUUID()}`;
    setDraft({ ...draft, rules: [...draft.rules, { id, name: "新业务口径", trigger: "", domains: [domain], enabled: false, source: "", body: "" }] });
    setSelected(id);
  };
  const previewRules = draft.rules.filter(r => r.enabled && r.domains.includes(domain));
  return <section className="panel ai-guidance">
    <header className="ai-guidance-heading"><div><h2>配置设置</h2><p>统一小特的工作方式与业务口径</p></div><div className="ai-guidance-actions"><span className="ai-guidance-version">{data.item.version ? `当前 v${data.item.version}` : "系统默认"}{dirty ? " · 有未保存修改" : ""}</span><button className="secondary-button" disabled={busy} onClick={() => setPreview(!preview)}>{preview ? "关闭预览" : "预览配置内容"}</button><button className="primary-button" disabled={busy || !dirty || uncertain} onClick={() => void save()}>{busy ? "处理中…" : "保存配置"}</button></div></header>
    {error && <div role="alert" className="ai-guidance-feedback is-error">{error}{uncertain && "。重新加载会用服务器版本替换当前草稿，请先保留需要的文字。"}<button className="secondary-button" disabled={busy} onClick={() => void load()}>重新加载</button></div>}
    {notice && <p role="status" className="ai-guidance-feedback">{notice}</p>}
    <div className="ai-guidance-layout"><nav aria-label="AI 配置分组">{([ ["global", "全局提示词", "角色与工作方式"], ["business", "业务口径", "通用规则与领域规则"], ["history", "版本记录", "查看与恢复"] ] as const).map(([key, label, hint]) => <button key={key} className={tab === key ? "active" : ""} aria-current={tab === key ? "page" : undefined} onClick={() => { setTab(key); setPast(null); }}><strong>{label}</strong><small>{hint}</small></button>)}<p>网页、各板块对话、钉钉问数及 AI 定时任务共用。模型的特殊要求仍在 AI 管理中维护。</p></nav>
    <div className="ai-guidance-content">
      {preview ? <><div className="ai-guidance-section"><h3>当前草稿预览</h3><label>查看领域<select value={domain} onChange={e => setDomain(e.target.value)}>{Object.entries(data.domains).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div><p className="ai-guidance-hint">下面展示草稿中该领域的启用内容。实际回答还会核对问题、页面与工具权限；这里不调用模型。</p><h4>全局提示词</h4><pre>{draft.globalPrompt || data.defaults.globalPrompt}</pre><h4>通用口径</h4><pre>{draft.commonRules || "未配置"}</pre>{previewRules.map(r => <div key={r.id}><h4>{r.name}</h4><pre>{r.body}</pre><small>来源：{r.source}</small></div>)}</> : <>
      {tab === "global" && <><h3>全局提示词</h3><p className="ai-guidance-hint">描述小特的角色、回答风格和处理问题的顺序。指标定义与计算口径放在“业务口径”。</p><label className="ai-guidance-field"><span>角色与工作方式 <small>{draft.globalPrompt.length} / 8000</small></span><textarea rows={13} maxLength={8000} value={draft.globalPrompt} disabled={busy} onChange={e => setDraft({ ...draft, globalPrompt: e.target.value })} /><small>留空采用系统默认提示词。配置不能改变账号权限、工具能力或后台计算公式。</small></label><button className="secondary-button" disabled={busy} onClick={() => setDraft({ ...draft, globalPrompt: data.defaults.globalPrompt })}>将系统默认内容填入草稿</button></>}
      {tab === "business" && <><h3>业务口径</h3><label className="ai-guidance-field"><span>通用规则 · 每次提问采用 <small>{draft.commonRules.length} / 4000</small></span><textarea rows={4} maxLength={4000} disabled={busy} value={draft.commonRules} onChange={e => setDraft({ ...draft, commonRules: e.target.value })} /><small>只放跨领域共同适用的规则；店铺、库存和财务等具体定义放在下面。</small></label><div className="ai-guidance-section"><div><h3>领域规则</h3><p>按相关问题、页面与可用工具加载；停用后不再采用。</p></div><button className="secondary-button" disabled={busy || draft.rules.length >= 24} onClick={add}>＋ 新增口径</button></div><div className="ai-guidance-rule-grid"><div className="ai-guidance-rule-list">{draft.rules.map(r => <button key={r.id} className={r.id === selected ? "active" : ""} onClick={() => setSelected(r.id)}><strong>{r.name}<small>{r.enabled ? "启用" : "停用"}</small></strong><span>{r.domains.map(d => data.domains[d]).join(" / ")}</span></button>)}</div><div className="ai-guidance-rule-editor">{rule ? <><div className="ai-guidance-section"><h4>编辑口径</h4><label className="ai-guidance-toggle"><input type="checkbox" checked={rule.enabled} disabled={busy} onChange={e => update({ enabled: e.target.checked })} />启用</label></div><label className="ai-guidance-field"><span>名称</span><input value={rule.name} maxLength={80} disabled={busy} onChange={e => update({ name: e.target.value })} /></label><label className="ai-guidance-field"><span>适用场景</span><input value={rule.trigger} maxLength={240} disabled={busy} placeholder="例如：广东入仓、补货风险与库存周转" onChange={e => update({ trigger: e.target.value })} /></label><fieldset><legend>关联领域</legend>{Object.entries(data.domains).map(([key, label]) => <label key={key}><input type="checkbox" checked={rule.domains.includes(key)} disabled={busy} onChange={e => update({ domains: e.target.checked ? [...rule.domains, key] : rule.domains.filter(d => d !== key) })} />{label}</label>)}</fieldset><label className="ai-guidance-field"><span>规则正文</span><textarea rows={8} maxLength={4000} disabled={busy} value={rule.body} onChange={e => update({ body: e.target.value })} /></label><label className="ai-guidance-field"><span>来源与依据</span><input value={rule.source} maxLength={240} disabled={busy} placeholder="业务文档或确认依据" onChange={e => update({ source: e.target.value })} /></label><button className="secondary-button" disabled={busy} onClick={() => { setDraft({ ...draft, rules: draft.rules.filter(r => r.id !== selected) }); setSelected(null); }}>从当前草稿移除</button></> : <p className="ai-guidance-hint">选择一条口径查看和编辑，或新增领域规则。</p>}</div></div></>}
      {tab === "history" && <><div className="ai-guidance-section"><div><h3>版本记录</h3><p>恢复会创建新版本，保留原有记录。时间为上海时间。</p></div><button className="secondary-button" disabled={busy} onClick={() => void history(0)}>查看系统默认</button></div>{past ? <div className="ai-guidance-past"><h4>{past.version ? `历史版本 v${past.version}` : "系统默认"}</h4><p>{past.createdBy} · {date(past.createdAt)}</p><h4>全局提示词</h4><pre>{past.config.globalPrompt}</pre><h4>通用口径</h4><pre>{past.config.commonRules}</pre>{past.config.rules.map(r => <div key={r.id}><h4>{r.name} · {r.enabled ? "启用" : "停用"}</h4><p>{r.trigger} · {r.domains.map(d => data.domains[d]).join(" / ")}</p><pre>{r.body}</pre><small>{r.source}</small></div>)}<button className="primary-button" disabled={busy || uncertain || dirty || past.version === data.item.version} onClick={() => void save(past.version)}>恢复此版本为新版本</button>{dirty && <p>请先保存当前草稿，或重新加载后再恢复。</p>}</div> : <><div className="ai-guidance-history">{data.history.map(row => <button key={row.version} disabled={busy} onClick={() => void history(row.version)}><strong>v{row.version}{row.version === data.item.version ? " · 当前" : ""}</strong><span>{row.created_by}</span><time>{date(row.created_at)}</time><span>{row.restored_from !== null ? `从 v${row.restored_from} 恢复` : "保存配置"}</span></button>)}{!data.history.length && <p>尚无修改记录，目前采用系统默认内容。</p>}</div>{data.hasMore && <button className="secondary-button" disabled={busy} onClick={() => void history()}>加载更早版本</button>}</>}</>}
      </>}
    </div></div>
  </section>;
}
