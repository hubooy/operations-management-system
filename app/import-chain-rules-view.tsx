"use client";

import { useEffect, useRef, useState } from "react";
import catalog from "@/lib/imports/chain-catalog.generated.json";
import { describeSchedule } from "@/lib/imports/run-presentation";
import { requestJson } from "@/lib/http/api-client";
import { completedAtLabel, formatChainStatusTime, todayStatusLabel, validateTodayStatus, type ChainTodayResponse } from "@/lib/imports/chain-status";
import { shanghaiIsoToday, type CurrentUser } from "./module-view-shared";

type Rule = typeof catalog.rules[number];

export default function ImportChainRulesView({ currentUser }: { currentUser: CurrentUser | null }) {
  const [platform, setPlatform] = useState("全部");
  const [selection, setSelection] = useState<{ rule: Rule; entityKey: string } | null>(null);
  const [status, setStatus] = useState<ChainTodayResponse | null>(null);
  const [statusError, setStatusError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const canOpenN8n = currentUser?.role === "operator" || currentUser?.role === "admin";
  const entities = catalog.entities.filter((e) => platform === "全部" || e.platform === platform);
  const chains = catalog.chains.filter((c) => platform === "全部" || c.platform === platform);
  const selectedChain = catalog.chains.find((c) => c.key === selection?.rule.chainKey);
  const selectedEntity = catalog.entities.find((e) => e.key === selection?.entityKey);
  const currentStatus = status?.date === shanghaiIsoToday() ? status : null;
  const selectedStatus = currentStatus?.items.find(i => i.workflowId === selection?.rule.workflowId);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12_000);
    let disposed = false;
    async function load() {
      setLoading(true);
      try {
        const payload = await requestJson<ChainTodayResponse>("/api/imports/chain-status", { signal: controller.signal });
        if (!payload || !validateTodayStatus(payload) || payload.date !== shanghaiIsoToday()) throw new Error("状态响应无效或日期已变化，请重新读取。");
        if (!disposed) { setStatus(payload); setStatusError(""); }
      } catch (error) {
        if (!disposed) { setStatus(null); setStatusError(error instanceof Error ? error.message : "无法核实今天状态"); }
      } finally {
        clearTimeout(timeout);
        if (!disposed) setLoading(false);
      }
    }
    void load();
    return () => { disposed = true; clearTimeout(timeout); controller.abort(); };
  }, [refresh, currentUser?.email, currentUser?.role]);

  useEffect(() => {
    const reload = () => { if (!document.hidden) setRefresh(value => value + 1); };
    const timer = setInterval(reload, 30_000);
    document.addEventListener("visibilitychange", reload);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", reload); };
  }, []);

  useEffect(() => {
    if (selection) dialog.current?.showModal();
    else dialog.current?.close();
  }, [selection]);

  return <div className="import-monitor">
    <div className="import-monitor-note" role="note"><strong>横向看工作流，纵向看店铺</strong><p>今天状态按上海日期读取 n8n 自动执行记录，每 30 秒更新。共用工作流显示整链结果；配置频率与下次调度以 n8n 已发布版本为准。</p></div>
    <section className="panel table-panel import-chain-panel">
      <div className="import-monitor-toolbar"><div><h2>链路规则</h2><span className="import-monitor-muted">{catalog.chains.length} 条链路 · {catalog.rules.length} 份工作流配置 · {currentStatus?.date || shanghaiIsoToday()}{currentStatus?.source === "synthetic_n8n" ? " · 合成状态演示" : ""}</span></div><div className="import-chain-actions"><label>平台<select aria-label="链路平台" value={platform} onChange={(e) => setPlatform(e.target.value)}>{["全部", "ERP", "京东", "天猫"].map((p) => <option key={p}>{p}</option>)}</select></label><button type="button" className="text-button" disabled={loading} onClick={() => setRefresh(v => v + 1)}>{loading ? "读取中…" : "刷新状态 ↻"}</button></div></div>
      {statusError && <div className="import-monitor-error" role="alert">今日状态暂时无法核实：{statusError}</div>}
      <div className="data-table-wrap import-matrix-scroll" tabIndex={0} role="region" aria-label="链路与店铺规则矩阵，可横向滚动">
        <table className="data-table import-chain-matrix" data-column-filter-scope="none"><thead>
          <tr><th rowSpan={2} scope="col">吉客云 / 店铺</th><th colSpan={chains.length} scope="colgroup" className="import-matrix-group">n8n 工作流</th></tr>
          <tr>{chains.map((chain) => <th scope="col" key={chain.key}><strong>{chain.label}</strong><small>{chain.modules.length} 个数据模块</small></th>)}</tr>
        </thead><tbody>{entities.map((entity) => <tr key={entity.key}>
          <th scope="row"><strong>{entity.name}</strong><small>{entity.platform}</small></th>
          {chains.map((chain) => {
            const rules = catalog.rules.filter((r) => r.chainKey === chain.key && r.entityKeys.includes(entity.key));
            return <td key={chain.key}>{rules.length ? rules.map((rule) => {
              const item = currentStatus?.items.find(i => i.workflowId === rule.workflowId);
              const label = todayStatusLabel(item);
              return <div className="import-rule-cell" key={rule.workflowId}>
              <span className={`import-run-badge is-${label.tone}`}>{loading && !currentStatus ? "读取今天状态…" : label.label}</span>
              {item?.state === "failed" && <small>仅统计定时与自动重试；手动恢复请查 n8n 执行及导入批次。</small>}
              {item?.completedToday && <small>{completedAtLabel(item)} {formatChainStatusTime(item.completedAt || "")}{item.state !== "completed" ? " · 后续执行状态见上" : ""}</small>}
              {rule.entityKeys.length > 1 && <small>共用 {rule.entityKeys.length} 店 · 整链状态</small>}
              <span>{rule.schedules.map(describeSchedule).join("、") || "手动触发"}</span>
              {rule.masterIntervalDays && <small>主数据每 {rule.masterIntervalDays} 天更新</small>}
              <small>{item?.active === true ? "工作流已启用" : item?.active === false ? "工作流已停用" : "启停状态无法核实"}</small>
              <button type="button" className="text-button" onClick={() => setSelection({ rule, entityKey: entity.key })}>查看规则 →</button>
            </div>; }) : <span className="import-monitor-muted" title={entity.platform === chain.platform ? "仓库没有为此主体配置该链路" : "此链路不适用于该平台"}>{entity.platform === chain.platform ? "未配置" : "—"}</span>}</td>;
          })}
        </tr>)}</tbody></table>
      </div>
      <footer className="import-monitor-footer"><span>只统计自动触发及完整自动重试，手动调试不计入今天完成。</span><span>{currentStatus ? `最近核查 ${formatChainStatusTime(currentStatus.checkedAt)}` : "尚未取得今天状态"}</span></footer>
    </section>
    <dialog ref={dialog} className="import-rule-dialog" aria-labelledby="import-rule-title" onClose={() => setSelection(null)} onCancel={() => setSelection(null)}>
      {selection && selectedChain && <>
        <header><div><span className="import-monitor-muted">链路规则详情</span><h2 id="import-rule-title">{selectedChain.label}</h2><p>{selectedEntity?.name}</p></div><button type="button" className="secondary-button" onClick={() => setSelection(null)} aria-label="关闭规则详情">关闭</button></header>
        <div className="import-rule-dialog-body">
          <dl className="import-run-facts"><div><dt>工作流</dt><dd>{selection.rule.name}</dd></div><div><dt>配置频率</dt><dd>{selection.rule.schedules.map(describeSchedule).join("、") || "手动触发"}</dd></div><div><dt>时区</dt><dd>{selection.rule.timezone}</dd></div><div><dt>今天自动状态</dt><dd>{todayStatusLabel(selectedStatus).label}{selectedStatus?.executionId ? ` · 执行 #${selectedStatus.executionId}` : ""}</dd></div><div><dt>自动完成时间</dt><dd>{selectedStatus?.completedAt ? `${completedAtLabel(selectedStatus)} ${formatChainStatusTime(selectedStatus.completedAt)}` : "未查到自动完成记录"}</dd></div><div><dt>下次运行</dt><dd>在 n8n 查看已发布调度</dd></div>{selection.rule.masterIntervalDays && <div><dt>主数据更新周期</dt><dd>每 {selection.rule.masterIntervalDays} 天；到期情况由执行记录决定</dd></div>}</dl>
          <h3>数据模块</h3><div className="import-rule-modules">{selectedChain.modules.map((m) => <span key={m}>{m}</span>)}</div>
          <h3>执行步骤</h3><ol className="import-rule-steps">{selectedChain.steps.map((step) => <li key={step}>{step}</li>)}</ol>
          <h3>共用此工作流的主体</h3><p>{catalog.entities.filter((e) => selection.rule.entityKeys.includes(e.key)).map((e) => e.name).join("、")}</p>
          <details className="import-rule-provenance"><summary>配置来源</summary><p>{selection.rule.definitionFile}</p><p>工作流 ID：{selection.rule.workflowId}</p><p>仓库定义不能证明当前已发布，也不能证明某次执行成功。</p></details>
        </div>
        <footer>{canOpenN8n ? <><a className="secondary-button" href={`http://localhost:5678/workflow/${encodeURIComponent(selection.rule.workflowId)}/executions`} target="_blank" rel="noreferrer">在 n8n 查看运行记录 ↗</a><a className="primary-button" href={`http://localhost:5678/workflow/${encodeURIComponent(selection.rule.workflowId)}`} target="_blank" rel="noreferrer">打开 n8n 工作流 ↗</a></> : <span className="import-monitor-muted">操作员和管理员可打开 n8n 工作流。</span>}</footer>
      </>}
    </dialog>
  </div>;
}
