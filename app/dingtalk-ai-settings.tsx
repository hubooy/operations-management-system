"use client";

import { useCallback, useEffect, useState } from "react";
import { requestJson } from "@/lib/http/api-client";

type Group = { id: string; name: string; enabled: boolean };
type Config = { configured: boolean; enabled: boolean; robotName: string; version: number; groups: Group[]; canWrite: boolean };
const endpoint = "/api/ai/dingtalk-settings";

export default function DingTalkAiSettings() {
  const [draft, setDraft] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try { setDraft((await requestJson<{ config: Config }>(endpoint)).config); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "AI 对话设置读取失败"); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const save = async () => {
    if (!draft || !draft.configured || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { config } = await requestJson<{ config: Config }>(endpoint, {
        method: "PATCH", body: { enabled: draft.enabled, groups: draft.groups, expectedVersion: draft.version },
      });
      setDraft(config);
      setNotice("AI 对话设置已保存。接收器运行时会即时采用；已排队的旧配置请求将停止。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  };
  const update = (index: number, change: Partial<Group>) => {
    if (draft) setDraft({ ...draft, groups: draft.groups.map((g, i) => i === index ? { ...g, ...change } : g) });
  };
  return <section className="panel launch-followup-config launch-followup-robot dingtalk-settings-form" aria-label="AI 双向对话设置" aria-busy={busy}>
    <header><div><h3>AI 双向对话</h3><p>群内 @ 机器人，查询回执与答案由同一机器人回复到该群；私聊提问在原私聊回复。</p></div><span>全部系统板块 · 只读查询</span></header>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!draft && <button type="button" className="secondary-button" disabled={busy} onClick={() => void load()}>{busy ? "正在加载…" : "重新加载 AI 设置"}</button>}
    {draft && <>
      {!draft.configured && <p role="status">尚未采用机器人连接配置。请在受控发布后启动钉钉接收器，已有机器人和群会自动载入。</p>}
      <p>机器人：{draft.robotName}。仅已绑定系统账号的成员可提问，查询遵循该账号的权限和数据范围。群内答案对该群成员可见。</p>
      <p>可查询销售、库存、网店、市场、财务、商品、ERP、运营事务、客服、导入、工作流、系统设置、AI 与 BI 数据；凭据、原始客户聊天和其他用户私有内容除外。</p>
      <fieldset disabled={busy || !draft.configured || !draft.canWrite}>
        <legend>对话群设置（仅管理员可修改）</legend>
        <label className="workflow-checkbox-field"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /><span>启用 AI 双向对话</span></label>
        {draft.groups.map((group, index) => <div className="launch-followup-robot-grid" key={index}>
          <label><span>群名称 {index + 1}</span><input maxLength={100} value={group.name} onChange={e => update(index, { name: e.target.value })} /></label>
          <label><span>群 ID（openConversationId）</span><input maxLength={256} value={group.id} onChange={e => update(index, { id: e.target.value })} /><small>使用钉钉查询返回的精确群 ID；回复前核验群名称、ID 与机器人入群关系。</small></label>
          <label className="workflow-checkbox-field"><input type="checkbox" checked={group.enabled} onChange={e => update(index, { enabled: e.target.checked })} /><span>允许本群 AI 对话并在群内回复</span></label>
          <button type="button" className="secondary-button" onClick={() => setDraft({ ...draft, groups: draft.groups.filter((_, i) => i !== index) })}>移除群 {index + 1}</button>
        </div>)}
        <button type="button" className="secondary-button" disabled={draft.groups.length >= 30} onClick={() => setDraft({ ...draft, groups: [...draft.groups, { id: "", name: "", enabled: false }] })}>添加对话群</button>
      </fieldset>
      <p>每个群、每位提问者与私聊的上下文分别保存。发送“新话题”可重新开始。</p>
      <div className="launch-followup-robot-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => void load()}>重新加载 AI 设置</button><button type="button" className="primary-button" disabled={busy || !draft.configured || !draft.canWrite} onClick={() => void save()}>{busy ? "处理中…" : "保存 AI 对话设置"}</button></div>
    </>}
  </section>;
}
