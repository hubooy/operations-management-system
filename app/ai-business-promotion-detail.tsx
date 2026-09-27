"use client";
import { projectPromotionReport } from "@/lib/ai/business-promotion-view";

export default function AiBusinessPromotionDetail({ detail }: { detail: unknown }) {
  let view: ReturnType<typeof projectPromotionReport>;
  try { view = projectPromotionReport(detail); }
  catch (error) { return <section className="report-review" aria-label="词货专项进度"><p role="alert">词货专项详情无法核验：{error instanceof Error ? error.message : "结构无效"} 请刷新报告；未展示未经核验的进度。</p></section>; }
  return <section className="report-review" aria-label="词货专项进度">
    <h4>京东推广关键词 × SKU 专项</h4><p>工作流：{view.reportStatus}。创建报告只表示已列入任务；各步骤以当前回执为准。</p>
    <ol>{view.nodes.map(node => <li key={node.key}>{node.label}：{node.status}</li>)}</ol>
    {view.scan === "verified" ? <>
      <p>服务端已返回经核验的规则筛查内容，包含 {view.coverageCount} 条覆盖记录与五角色独立读取证明。候选只保留重点记录，两种词货视图的费用不可相加。</p>
      <ul>{view.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul>
      <h5>专业分析摘要</h5>{view.professionals.map(role => <section key={role.role}><h5>{role.label}</h5><p className="report-body">{role.summary}</p></section>)}
    </> : <p role="status">当前详情尚未返回独立的筛查准备回执或完整筛查内容；节点状态不证明规则扫描已完成。</p>}
    <p>人工复核：{view.humanReview}。模型的独立复核不能替代人工批准。正式 HTML / XLSX 只以文件任务“可下载”及完整分卷校验为准。</p>
  </section>;
}
