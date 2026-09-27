"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { AppCurrentUser } from "./shell/view-contract";

type MemoryKind = "preference" | "glossary" | "business_context";
type MemoryItem = {
  id: string;
  kind: MemoryKind;
  key: string;
  content: string;
  scopeMode: "owner" | "data_scope";
  status: "active" | "archived";
  version: number;
  updatedAt: string;
};
type MemoryDraft = { id?: string; expectedVersion?: number; kind: MemoryKind; key: string; content: string };

const kindLabels: Record<MemoryKind, string> = {
  preference: "个人偏好",
  glossary: "业务术语",
  business_context: "稳定业务背景",
};

function emptyDraft(): MemoryDraft {
  return { kind: "preference", key: "", content: "" };
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null) as (T & { error?: string }) | null;
  if (!response.ok) throw new Error(payload?.error || fallback);
  if (!payload) throw new Error(fallback);
  return payload;
}

export default function AiMemoryView({ currentUser }: { currentUser: AppCurrentUser | null }) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [draft, setDraft] = useState<MemoryDraft>(() => emptyDraft());
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const canWrite = currentUser?.role !== "viewer" && Boolean(currentUser);

  const load = useCallback(async (search: string) => {
    const generation = ++generationRef.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true); setError("");
    try {
      const response = await fetch(`/api/ai/memories?page=1&pageSize=50${search.trim() ? `&q=${encodeURIComponent(search.trim())}` : ""}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson<{ items: MemoryItem[] }>(response, "读取全局记忆失败");
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setItems(payload.items);
    } catch (reason) {
      if (!controller.signal.aborted && generation === generationRef.current) setError(reason instanceof Error ? reason.message : "读取全局记忆失败");
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (generation === generationRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(""), 0);
    return () => { window.clearTimeout(timer); controllerRef.current?.abort(); generationRef.current += 1; };
  }, [load]);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!window.confirm("确认把这条内容保存为跨页面、跨对话的个人全局记忆吗？请确保它不含密钥、客户原文、当前经营数字或系统指令。")) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const response = await fetch(draft.id ? `/api/ai/memories/${encodeURIComponent(draft.id)}` : "/api/ai/memories", {
        method: draft.id ? "PATCH" : "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(draft.id
          ? { confirmed: true, expectedVersion: draft.expectedVersion, key: draft.key, content: draft.content }
          : { confirmed: true, kind: draft.kind, key: draft.key, content: draft.content }),
      });
      const payload = await readJson<{ item: MemoryItem; duplicate?: boolean }>(response, "保存全局记忆失败");
      setNotice(payload.duplicate ? "相同记忆已经存在，未重复写入。" : `“${payload.item.key}”已通过安全闸并保存。`);
      setDraft(emptyDraft());
      await load("");
      setQuery("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存全局记忆失败");
    } finally {
      setSaving(false);
    }
  };

  const archive = async (item: MemoryItem) => {
    if (!window.confirm(`确认归档记忆“${item.key}”吗？归档后不再进入 AI 上下文，审计记录仍保留。`)) return;
    setBusyId(item.id); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/ai/memories/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ confirmed: true, expectedVersion: item.version }),
      });
      await readJson<{ archived: boolean }>(response, "归档全局记忆失败");
      setNotice(`“${item.key}”已归档。`);
      if (draft.id === item.id) setDraft(emptyDraft());
      await load(query);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "归档全局记忆失败");
    } finally {
      setBusyId("");
    }
  };

  return <section className="ai-memory-workspace">
    <article className="panel ai-memory-hero">
      <div><span className="eyebrow">OWNER-PRIVATE MEMORY</span><h2>全局记忆</h2><p>只保存你明确确认的稳定偏好、术语和业务背景。记忆按 owner 与创建时 scope 隔离，作为低信任数据参与召回，不会升级为系统指令。</p></div>
      <div className="ai-sandbox-badges"><span>不自动沉淀</span><span>不保存密钥</span><span>CAS 版本</span><span>软归档 + 审计</span></div>
    </article>

    {(error || notice) && <div className={`inventory-feedback ${error ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={error ? "alert" : "status"}><span>{error ? "!" : "✓"}</span><div><strong>{error ? "记忆操作失败" : "记忆已更新"}</strong><p>{error || notice}</p></div></div>}

    <div className="ai-memory-grid">
      <article className="panel ai-admin-card">
        <div className="section-header"><div><h3>{draft.id ? "编辑记忆" : "新增记忆"}</h3><p>{canWrite ? "保存前会经过来源、内容、精确重复和近似重复四道闸。" : "当前只读角色可以查看自己的有效记忆，不能新增、编辑或归档。"}</p></div>{draft.id && <button type="button" className="text-button" onClick={() => setDraft(emptyDraft())}>取消编辑</button>}</div>
        <form className="ai-config-form" onSubmit={(event) => void save(event)}>
          <label><span>类型</span><select value={draft.kind} disabled={!canWrite || Boolean(draft.id)} onChange={(event) => setDraft((current) => ({ ...current, kind: event.target.value as MemoryKind }))}>{Object.entries(kindLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label><span>记忆键</span><input required disabled={!canWrite} maxLength={80} value={draft.key} onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value }))} placeholder="例如：周报展示偏好" /></label>
          <label className="ai-form-wide"><span>稳定内容</span><textarea required disabled={!canWrite} maxLength={2000} rows={8} value={draft.content} onChange={(event) => setDraft((current) => ({ ...current, content: event.target.value }))} placeholder="例如：经营周报默认先展示净销售额与大毛利率，并注明人民币分/元换算。" /><small>禁止密钥、Token、客户聊天原文、当前经营数字、绕过权限或改写系统提示的内容。</small></label>
          <div className="ai-form-actions"><button className="primary-button" type="submit" disabled={!canWrite || saving}>{saving ? "校验并保存中…" : draft.id ? "确认更新记忆" : "确认新增记忆"}</button></div>
        </form>
      </article>

      <article className="panel ai-admin-card data-refresh-region" aria-busy={loading}>
        <div className="section-header"><div><h3>我的有效记忆</h3><p>管理员也不能越权读取其他 owner 的个人记忆。</p></div><span className="status status-success">{items.length} 条</span></div>
        <form className="ai-memory-search" onSubmit={(event) => { event.preventDefault(); void load(query); }}><input maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索记忆键或内容" /><button type="submit" className="secondary-button" disabled={loading}>搜索</button><button type="button" className="text-button" onClick={() => { setQuery(""); void load(""); }}>清除</button></form>
        <div className="ai-memory-list">{!loading && items.length === 0 && <div className="empty-state"><strong>暂无匹配记忆</strong><p>只有你明确保存且仍在当前 scope 内的记忆会显示。</p></div>}{items.map((item) => <article key={item.id}><header><div><strong>{item.key}</strong><small>{kindLabels[item.kind]} · {item.scopeMode === "owner" ? "Owner 范围" : "数据 scope 快照"} · v{item.version}</small></div><span className="status status-success">有效</span></header><p>{item.content}</p><footer><small>更新于 {item.updatedAt}</small>{canWrite && <div><button type="button" className="row-action" onClick={() => setDraft({ id: item.id, expectedVersion: item.version, kind: item.kind, key: item.key, content: item.content })}>编辑</button><button type="button" className="row-action danger" disabled={busyId === item.id} onClick={() => void archive(item)}>{busyId === item.id ? "归档中…" : "归档"}</button></div>}</footer></article>)}</div>
      </article>
    </div>
  </section>;
}
