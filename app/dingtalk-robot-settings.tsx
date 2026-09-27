"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import DingTalkAiSettings from "./dingtalk-ai-settings";

import { requestJson } from "@/lib/http/api-client";

type ReportConfig = {
  enabled: boolean;
  connectionMode: "dws_stream";
  credentialsManagedExternally: boolean;
  deliveryMode: "png_drive_preview_by_bot";
  targetGroupName: string;
  robotName: string;
  sendWeekday: number;
  sendLocalTime: string;
  version: number;
  lastDelivery: null | {
    weekStart: string;
    weekEnd: string;
    status: string;
    deliveredAt: string | null;
  };
};

const WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"];

function messageOf(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function formatDateTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

export default function DingTalkRobotSettings({ canWrite }: { canWrite: boolean }) {
  const [config, setConfig] = useState<ReportConfig | null>(null);
  const [draft, setDraft] = useState<ReportConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await requestJson<{ config: ReportConfig }>("/api/workflow/new-product-weekly-report-config");
      setConfig(payload.config);
      setDraft(payload.config);
    } catch (reason) {
      setError(messageOf(reason, "钉钉机器人配置读取失败。"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!canWrite || !config || !draft || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload = await requestJson<{ config: ReportConfig }>("/api/workflow/new-product-weekly-report-config", {
        method: "PATCH",
        body: {
          enabled: draft.enabled,
          targetGroupName: draft.targetGroupName,
          robotName: draft.robotName,
          sendWeekday: draft.sendWeekday,
          sendLocalTime: draft.sendLocalTime,
          expectedVersion: config.version,
        },
      });
      setConfig(payload.config);
      setDraft(payload.config);
      setNotice(payload.config.enabled
        ? "钉钉图片周报已启用，将按本机时间执行。"
        : "机器人配置已保存，定时发送保持停用。");
    } catch (reason) {
      setError(messageOf(reason, "钉钉机器人配置保存失败。"));
    } finally {
      setSaving(false);
    }
  };

  if (loading && !draft) {
    return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取钉钉机器人配置</strong><p>正在连接运营事务周报配置…</p></section>;
  }
  if (!draft) {
    return <><DingTalkAiSettings /><section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>钉钉周报配置读取失败</strong><p>{error || "暂时无法读取配置"}</p><button type="button" className="secondary-button" onClick={() => void load()}>重新加载</button></section></>;
  }

  return <div className="dingtalk-settings-workspace data-refresh-region" aria-busy={loading || saving}>
    <DingTalkAiSettings />
    {(error || notice) && <section className={`inventory-feedback ${error ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={error ? "alert" : "status"}><span>{error ? "!" : "✓"}</span><div><strong>{error ? "处理失败" : "保存成功"}</strong><p>{error || notice}</p></div></section>}

    <section className="panel dingtalk-settings-intro">
      <div><span className="eyebrow">DINGTALK STREAM ROBOT</span><h2>钉钉图片周报</h2><p>管理上新销售图片周报的目标群和发送时间，与 AI 对话群独立配置。机器人密钥由本机 DWS 安全凭据库托管。</p></div>
      <span className={`status ${draft.enabled ? "status-success" : "status-gray"}`}>{draft.enabled ? "已启用" : "未启用"}</span>
    </section>

    <section className="panel dingtalk-settings-guide" aria-label="钉钉机器人接入说明">
      <strong>接入方式</strong>
      <ol><li>钉钉开放平台使用企业内部应用的 Stream 机器人。</li><li>本机 DWS 授权负责保管 AppKey、AppSecret 与机器人身份。</li><li>系统生成 PNG、上传钉盘，并由机器人向目标群发送在线预览链接。</li></ol>
    </section>

    <section className="panel launch-followup-config launch-followup-robot dingtalk-settings-form">
      <header><div><h3>连接与投递配置</h3><p>保存只更新配置和调度，不会立即上传图片或发送消息。</p></div><span>{draft.connectionMode === "dws_stream" ? "Stream 已配置" : "等待配置"}</span></header>
      {!canWrite && <section className="inventory-feedback" role="note"><span>i</span><div><strong>当前为只读模式</strong><p>仅运营人员或管理员可修改机器人配置。</p></div></section>}
      <div className="launch-followup-robot-grid">
        <label><span>接入模式</span><input readOnly value="Stream 模式（DWS 授权连接）" /></label>
        <label><span>凭据保管</span><input readOnly value={draft.credentialsManagedExternally ? "本机 DWS 安全凭据库" : "未配置"} /></label>
        <label><span>机器人名称</span><input readOnly value={draft.robotName} /><small>安全边界固定为唯一企业机器人“志高助手”。</small></label>
        <label><span>目标群名称</span><input readOnly value={draft.targetGroupName} /><small>安全边界固定为唯一群聊“测试群聊”。</small></label>
        <label className="dingtalk-settings-delivery"><span>图片投递方式</span><input readOnly value="PNG 上传钉盘 + 机器人在线预览链接" /><small>机器人发出的消息可直接打开钉钉在线预览。</small></label>
      </div>
      <div className="launch-followup-robot-notice"><strong>发送门禁</strong><span>每次发送前动态唯一核验机器人、目标群及机器人入群关系，并用投递账本阻止同一报告周重复发送。</span></div>
      <div className="launch-followup-schedule">
        <label><span>发送日</span><select disabled={!canWrite} value={draft.sendWeekday} onChange={(event) => setDraft({ ...draft, sendWeekday: Number(event.target.value) })}>{WEEKDAYS.map((label, index) => <option key={label} value={index}>{label}</option>)}</select></label>
        <label><span>本机时间</span><input type="time" disabled={!canWrite} value={draft.sendLocalTime} onChange={(event) => setDraft({ ...draft, sendLocalTime: event.target.value })} /></label>
        <label className="workflow-checkbox-field"><input type="checkbox" disabled={!canWrite} checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>启用每周钉钉图片周报</span></label>
      </div>
      {draft.lastDelivery && <small>最近投递：{draft.lastDelivery.weekStart} 至 {draft.lastDelivery.weekEnd} · {draft.lastDelivery.status} · {formatDateTime(draft.lastDelivery.deliveredAt)}</small>}
      <div className="launch-followup-robot-actions"><Link className="secondary-button" href="/?module=workflow&view=launch-followup">查看周报图片预览</Link><span>当前目标：{draft.robotName || "未配置机器人"} → {draft.targetGroupName || "未配置目标群"}</span><button type="button" className="primary-button" disabled={!canWrite || saving} onClick={() => void save()}>{saving ? "保存中…" : "保存机器人配置"}</button></div>
    </section>
  </div>;
}
