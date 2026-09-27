"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

type Schedule = { id: string; name: string; prompt: string; cadence: "daily" | "weekly" | "monthly"; hour: number; minute: number; day: number;
  contentType: "text" | "screenshot" | "report_file"; sourceRef: string; targetType: "group" | "person"; targetId: string; senderId: string; enabled: boolean; version: number; nextRunAt: string | null; lastRunAt: string | null };
type Run = { id: string; scheduleId: string; scheduledAt: string; status: string; errorCode: string };
type Draft = Pick<Schedule, "name" | "prompt" | "contentType" | "sourceRef" | "cadence" | "hour" | "minute" | "day" | "targetType" | "targetId" | "senderId" | "enabled"> & { id?: string; expectedVersion?: number };
type Group = { id: string; name: string; enabled: boolean };
type Report = { id: string; name: string; status: string; dryRun: boolean };
const pages = [
  ["workflow:launch-followup", "运营事务 · 上新跟进 · 钉钉周报完整表格（最近完整周）"],
  ["dashboard:overview", "BI 看板 · 总览"], ["shop:analysis", "网店分析 · 总览"],
  ["shop:products", "网店分析 · 商品"], ["shop:promotion", "网店分析 · 推广"],
  ["sales:overview", "销售分析 · 总览"], ["inventory:overview", "库存管理 · 总览"],
  ["inventory:guangdong", "库存管理 · 广东仓"], ["market:ranking", "市场分析 · 榜单"],
] as const;
const empty = (): Draft => ({ name: "", prompt: "", contentType: "text", sourceRef: "", cadence: "daily", hour: 9, minute: 0, day: 1, targetType: "person", targetId: "", senderId: "", enabled: false });
const labels: Record<string, string> = { queued: "等待接收器", running: "执行中", ready: "结果待发送", sending: "发送中", sent: "发送已确认", unknown: "结果不明，未重试", denied: "已拒绝或错过时段", failed: "执行失败" };
const when = (value: string | null) => value ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) : "—";
async function json<T>(response: Response): Promise<T> { const payload = await response.json() as T & { error?: string }; if (!response.ok) throw new Error(payload.error || "定时任务请求失败"); return payload; }

export default function AiDingTalkSchedulesView() {
  const [items, setItems] = useState<Schedule[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [draft, setDraft] = useState<Draft>(empty);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const load = useCallback(async () => {
    const current = ++generation.current;
    controller.current?.abort();
    const next = new AbortController(); controller.current = next;
    try {
      const [schedules, settings, reportList] = await Promise.all([
        fetch("/api/ai/dingtalk-schedules", { cache: "no-store", signal: next.signal }).then(json<{ items: Schedule[]; runs: Run[] }>),
        fetch("/api/ai/dingtalk-settings", { cache: "no-store", signal: next.signal }).then(json<{ config: { groups: Group[] } }>),
        fetch("/api/ai/reports?page=1&pageSize=50", { cache: "no-store", signal: next.signal }).then(json<{ items: Report[] }>).catch(() => ({ items: [] as Report[] })),
      ]);
      if (current !== generation.current || next.signal.aborted) return;
      setItems(schedules.items); setRuns(schedules.runs); setGroups(settings.config.groups); setReports(reportList.items.filter(r => r.status === "completed" && !r.dryRun));
    } catch (reason) { if (!next.signal.aborted && current === generation.current) setError(reason instanceof Error ? reason.message : "读取定时任务失败"); }
  }, []);
  useEffect(() => { void load(); return () => { controller.current?.abort(); }; }, [load]);
  const save = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      await json(await fetch("/api/ai/dingtalk-schedules", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(draft) }));
      setDraft(empty()); setEditing(false); setNotice("任务已保存；启用任务将在接收器运行且身份核验通过后执行。"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); } finally { setBusy(false); }
  };
  const run = async (item: Schedule) => {
    const effect = item.contentType === "screenshot" ? (item.prompt ? "截取指定系统页面并由机器人发送一条标题与图片合并的图文消息" : "截取指定系统页面并由机器人发送图片") : item.contentType === "report_file" ? "读取已复核报告并由机器人发送 Excel 文件" : "调用付费 AI 模型并由机器人发送文字";
    if (!window.confirm(`确认立即执行“${item.name}”？将${effect}到钉钉${item.targetType === "group" ? "群" : "个人"}。`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await json(await fetch("/api/ai/dingtalk-schedules/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: item.id, expectedVersion: item.version }) }));
      setNotice("已入队；刷新可查看执行状态。结果不明时不会自动重发。"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "执行失败"); } finally { setBusy(false); }
  };
  const edit = (item: Schedule) => { setDraft({ id: item.id, expectedVersion: item.version, name: item.name, prompt: item.prompt, contentType: item.contentType || "text", sourceRef: item.sourceRef || "", cadence: item.cadence, hour: item.hour, minute: item.minute, day: item.day, targetType: item.targetType, targetId: item.targetId, senderId: item.senderId, enabled: item.enabled }); setEditing(true); };
  return <section className="panel ai-admin-card ai-ding-schedules">
    <div className="section-header"><div><h3>AI定时任务</h3><p>按上海时间发送只读 AI 结果、指定系统页面截图或已复核的 Excel 报告。截图和文件由机器人身份发送；创建任务不会直接发送。</p></div><button type="button" className="secondary-button" onClick={() => { setDraft(empty()); setEditing(true); }} disabled={busy}>新增任务</button></div>
    {(error || notice) && <p role={error ? "alert" : "status"} className={error ? "ai-ding-error" : "ai-ding-notice"}>{error || notice}</p>}
    <div className="ai-ding-table-wrap"><table><thead><tr><th>名称</th><th>周期</th><th>执行内容</th><th>推送到</th><th>状态</th><th>下次执行</th><th>最近执行</th><th>操作</th></tr></thead><tbody>
      {items.map(item => { const latest = runs.find(run => run.scheduleId === item.id); return <tr key={item.id}><td>{item.name}</td><td>{item.cadence === "daily" ? "每天" : item.cadence === "weekly" ? `每周${item.day}` : `每月${item.day}日`} {String(item.hour).padStart(2, "0")}:{String(item.minute).padStart(2, "0")}</td><td className="ai-ding-prompt" title={item.prompt || item.sourceRef}>{item.contentType === "screenshot" ? `图片：${pages.find(p => p[0] === item.sourceRef)?.[1] || item.sourceRef}${item.prompt ? `；文案：${item.prompt}` : ""}` : item.contentType === "report_file" ? `Excel 报告 · ${item.sourceRef}` : item.prompt}</td><td>{item.targetType === "group" ? groups.find(g => g.id === item.targetId)?.name || item.targetId : `本人 · ${item.targetId}`}</td><td>{item.enabled ? "启用" : "停用"}</td><td>{when(item.nextRunAt)}</td><td>{latest ? `${when(latest.scheduledAt)} · ${latest.errorCode === "caption_sent_image_unknown" ? "文案已发送，图片未确认，未重发" : labels[latest.status] || latest.status}` : "尚未执行"}</td><td><button type="button" className="row-action" onClick={() => edit(item)}>编辑</button><button type="button" className="row-action" disabled={!item.enabled || busy} onClick={() => void run(item)}>立即执行</button></td></tr>; })}
      {!items.length && <tr><td colSpan={8}>暂无定时任务；新增后默认停用，请核对目标和执行账号再启用。</td></tr>}
    </tbody></table></div>
    {editing && <div className="ai-ding-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setEditing(false); }}><div className="ai-ding-modal" role="dialog" aria-modal="true" aria-label={draft.id ? "编辑定时任务" : "新增定时任务"}>
      <div className="section-header"><h3>{draft.id ? "编辑定时任务" : "新增定时任务"}</h3><button type="button" className="text-button" onClick={() => setEditing(false)} aria-label="关闭">×</button></div>
      <form className="ai-config-form" onSubmit={event => void save(event)}>
        <label className="ai-form-wide"><span>任务名称</span><input required maxLength={100} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
        <label><span>发送内容</span><select value={draft.contentType} onChange={e => setDraft({ ...draft, contentType: e.target.value as Draft["contentType"], sourceRef: "", prompt: "" })}><option value="text">只读 AI 文字</option><option value="screenshot">运营系统页面截图（可附文案）</option><option value="report_file">已复核的 Excel 报告</option></select></label>
        {draft.contentType === "text" && <label className="ai-form-wide"><span>执行内容（只读 AI 指令）</span><textarea required maxLength={4000} rows={5} value={draft.prompt} onChange={e => setDraft({ ...draft, prompt: e.target.value })} /><small>此类型生成文字回复。要发送表格截图，请将“发送内容”改为“运营系统页面截图”，再选择对应表格。</small></label>}
        {draft.contentType === "screenshot" && <label className="ai-form-wide"><span>截图页面</span><select required value={draft.sourceRef} onChange={e => setDraft({ ...draft, sourceRef: e.target.value })}><option value="">选择页面</option>{pages.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><small>{draft.sourceRef === "workflow:launch-followup" ? "按上海时间取最近一个完整周（周一至周日），截取周报标题、产品图、趋势、全部行和累计周列；超出图片上限会报错，不会截掉内容。" : "使用专用已登录浏览器截取该页面当前内容。"} 登录账号必须与任务创建人一致。</small></label>}
        {draft.contentType === "screenshot" && <label className="ai-form-wide"><span>附带文案（可选）</span><textarea maxLength={4000} rows={4} placeholder="例如：新品周销量趋势数据" value={draft.prompt} onChange={e => setDraft({ ...draft, prompt: e.target.value })} /><small>每次将这段原文和最新完整截图合并为一条图文消息，标题在上、图片在下；留空只发图片。这里填写要发送的文案，不执行 AI 分析指令。编辑保存后从下一次执行生效。</small></label>}
        {draft.contentType === "report_file" && <label className="ai-form-wide"><span>报告文件</span><select required value={draft.sourceRef} onChange={e => setDraft({ ...draft, sourceRef: e.target.value })}><option value="">选择已复核报告</option>{draft.sourceRef && !reports.some(r => r.id === draft.sourceRef) && <option value={draft.sourceRef}>{draft.sourceRef}（不在最近 50 条列表中）</option>}{reports.map(report => <option key={report.id} value={report.id}>{report.name} · {report.id}</option>)}</select><small>每次发送从该报告已保存的证据生成 Excel；报告必须仍归任务创建人所有。</small></label>}
        <label><span>周期</span><select value={draft.cadence} onChange={e => setDraft({ ...draft, cadence: e.target.value as Draft["cadence"], day: 1 })}><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月（1–28日）</option></select></label>
        {draft.cadence !== "daily" && <label><span>{draft.cadence === "weekly" ? "星期（1=周一）" : "日期（1–28）"}</span><input type="number" min={1} max={draft.cadence === "weekly" ? 7 : 28} value={draft.day} onChange={e => setDraft({ ...draft, day: Number(e.target.value) })} /></label>}
        <label><span>小时 · 上海时间</span><input type="number" min={0} max={23} value={draft.hour} onChange={e => setDraft({ ...draft, hour: Number(e.target.value) })} /></label>
        <label><span>分钟</span><input type="number" min={0} max={59} value={draft.minute} onChange={e => setDraft({ ...draft, minute: Number(e.target.value) })} /></label>
        <label><span>投递目标</span><select value={draft.targetType} onChange={e => setDraft({ ...draft, targetType: e.target.value as Draft["targetType"], targetId: e.target.value === "person" ? draft.senderId : "" })}><option value="person">执行账号本人私聊</option><option value="group">已批准的钉钉群</option></select></label>
        {draft.targetType === "group" && <label><span>目标群</span><select required value={draft.targetId} onChange={e => setDraft({ ...draft, targetId: e.target.value })}><option value="">选择已启用群</option>{groups.filter(g => g.enabled).map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>}
        <label className="ai-form-wide"><span>执行账号 staffId（必须已绑定系统账号）</span><input required maxLength={160} value={draft.senderId} onChange={e => setDraft({ ...draft, senderId: e.target.value, targetId: draft.targetType === "person" ? e.target.value : draft.targetId })} /><small>文字任务按此人的现行权限查询。截图和报告要求该账号为创建任务的无范围限制管理员；个人投递仅发给该账号本人。</small></label>
        <label className="ai-check-field"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /><span>启用定时执行</span></label>
        <div className="ai-form-actions"><button type="button" className="secondary-button" onClick={() => setEditing(false)}>取消</button><button className="primary-button" disabled={busy}>{busy ? "保存中…" : "保存任务"}</button></div>
      </form>
    </div></div>}
  </section>;
}
