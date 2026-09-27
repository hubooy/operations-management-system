"use client";
import { useState, type ReactNode } from "react";
import { entityLabel, preparationNames, projectScreening, roleNames, type CoverageLine } from "@/lib/ai/business-screening-view";
import type { BudgetResult } from "./ai-business-budget";

function Pages<T>({ label, rows, render }: { label: string; rows: T[]; render: (row: T, index: number) => ReactNode }) {
  const [page, setPage] = useState(0), start = page*20;
  return <details className="screening-records"><summary>{label} · {rows.length} 条完整记录</summary>
    {rows.length ? <><ol start={start+1}>{rows.slice(start,start+20).map((row,index) => <li key={start+index}>{render(row,start+index)}</li>)}</ol><div className="report-actions"><button disabled={page===0} onClick={() => setPage(value=>value-1)}>{label}上一页</button><span>{start+1}–{Math.min(start+20,rows.length)} / {rows.length}</span><button disabled={start+20>=rows.length} onClick={() => setPage(value=>value+1)}>{label}下一页</button></div></> : <p>无此类记录。</p>}
  </details>;
}
const coverage = (row: CoverageLine) => <><strong>{row.title}</strong><p>{row.status}</p><p>{row.details}</p></>;

export default function AiBusinessScreeningDetail({ reportId, preparation, screening, professionalAnalyses, budget }: { reportId: string; preparation?: unknown; screening?: unknown; professionalAnalyses?: unknown; budget?: BudgetResult }) {
  const state = preparation && typeof preparation === "object" && !Array.isArray(preparation) ? preparation as Record<string,unknown> : null;
  const status = typeof state?.status === "string" && Object.hasOwn(preparationNames,state.status) ? preparationNames[state.status] : undefined;
  let view: ReturnType<typeof projectScreening> | null = null, error = "";
  if (screening !== undefined) {
    try { view = projectScreening(screening,professionalAnalyses,reportId); }
    catch (caught) { error = caught instanceof Error ? caught.message : "筛查详情无法核验，请刷新。"; }
  }
  return <section className="screening-detail" aria-label="完整规则筛查详情">
    <h4>完整规则筛查与多 Agent 分析</h4>
    <p role="status">{status ?? "准备状态尚未确认，请刷新详情。"}</p>
    {state && <p>筛查结果：{state.scanPublished === true ? "已完整保存" : "尚未确认完整保存"}；Agent：{state.agentsStarted === true ? "已开始" : "尚未开始"}。保存成功不代表模型已读或人工复核通过。</p>}
    {typeof state?.error === "string" && state.error && <p className="report-error" role="alert">{state.error.length<=2000 ? state.error : "错误说明超出显示范围，请核验任务状态。"}</p>}
    <p>候选是完整规则扫描后保留的重点记录，并非全量明细；容量不足会停止，不能自动缩小范围。</p>
    {error && <p className="report-error" role="alert">{error} 未展示无法核验的覆盖或读取证明。</p>}
    {!view && !error && <p>专业分析完成后可查看服务端覆盖记录、五个 Agent 的独立读取证明及候选省略数量。</p>}
    {view && <>
      <h5>范围与数据局限</h5><p>请求能力覆盖：{view.planned ? "计划支持" : "存在缺口"}；请求表执行：{view.executed ? "全部完成" : "存在未执行范围"}；来源日期：{view.dates ? "查询日期有记录" : "存在缺日、空表或不支持的范围"}。不证明每个 SKU 每日完整，也不证明因果效果。</p>
      <ul>{view.limitations.map((text,index) => <li key={index}>{text}</li>)}</ul>
      <Pages label="全部来源" rows={view.sources} render={coverage} />
      <Pages label="范围分组" rows={view.families} render={coverage} />
      <Pages label="全部请求覆盖" rows={view.requested} render={coverage} />
      <Pages label="全部分析表" rows={view.tables} render={coverage} />
      <Pages label="规则分区与候选" rows={view.partitions} render={row => <><strong>{row.title}</strong><p>扫描 {row.scanned} 行；命中 {row.matched}；保留 {row.retained}；省略 {row.omitted}。</p><p>{row.eligibility}。</p><p>{row.supported ? "规则支持" : "规则不支持"}{row.reason ? `：${row.reason}` : ""}。分区可能重复引用同一事实，金额不可相加为总损失。</p></>} />
      <h5>五个 Agent 的独立读取证明</h5><div className="report-table"><table><thead><tr><th>角色</th><th>固定角色包</th><th>固定预算</th></tr></thead><tbody>{view.proofs.map(proof => <tr key={proof.role}><td>{roleNames[proof.role]}</td><td>{proof.complete && proof.pages===proof.expected ? "已完整读取角色包" : "读取未完成"} · {proof.pages}/{proof.expected} 页</td><td>{proof.budgetRequired || proof.budgetStarted ? proof.budgetComplete ? "本次预算已完整读取" : "预算读取未完成" : "无必读预算"}</td></tr>)}</tbody></table></div><p>这些证明只针对各自固定包，不能称为已读全量明细，也不能替代人工复核。</p>
      <h5>专业分析</h5>{view.professionals.map(analysis => <section key={analysis.role}><h5>{roleNames[analysis.role]}</h5><p className="report-body">{analysis.summary}</p>{analysis.findings.map((finding,index) => <article key={index}><strong>{finding.title}</strong><p className="report-body">{finding.explanation}</p>{finding.facts.length ? <ul>{finding.facts.map((fact,i)=><li key={i}>{fact}</li>)}</ul> : <p>数据缺口结论，无可用数值引用。</p>}{finding.action.length>0 && <ul>{finding.action.map((action,i)=><li key={i}>{action}</li>)}</ul>}</article>)}</section>)}
    </>}
    {budget && <section aria-label="本报告固定预算"><h5>本报告固定预算（只读）</h5><p>总额 {budget.allocation.totalBudgetCents} 分；预留 {budget.allocation.reservedCents} 分；已分配 {budget.allocation.allocatedCents} 分；未分配 {budget.allocation.unallocatedCents} 分。</p>{budget.scenarios.map((scenario,index)=><section key={index}><h5>{scenario.assumptions.name}</h5><p>假设归因成交：{scenario.summary.projectedAttributedGmvCents ?? "不可合计或测算"} 分；假设贡献扣推广：{scenario.summary.assumedContributionAfterAdCents ?? "不可合计或测算"} 分；不可测算目标：{scenario.summary.unavailableTargets}。不是实际利润或收益承诺。</p><Pages label={`情景${index+1}固定目标`} rows={scenario.rows} render={row=><p>{entityLabel(row.entity)}；分配 {row.budgetCents} 分；消耗 {row.reviewAfterSpendCents} 分复核；负责人：{row.ownerRole}；测算状态：{row.status}。</p>} /></section>)}<p>如需新参数，请回到经营分析工作台明确选择完整规则筛查模式及范围，再配置固定预算；不会将本报告自动改为旧分析模式。</p></section>}
  </section>;
}
