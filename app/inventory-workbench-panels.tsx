"use client";

import {
  formatCurrencyFromCents,
  formatCount,
  formatRate,
  type InventoryAgeAnalysisResponse,
  type InventoryAgeStatus,
  type InventoryInboundMonitorResponse,
  type InventoryOverviewResponse,
} from "./module-view-shared";

type AgeDistributionBucket = InventoryAgeAnalysisResponse["fineDistribution"][number];
type PlanSummary = InventoryOverviewResponse["planSummary"];
type AgeMetrics = InventoryAgeAnalysisResponse["metrics"];
type InboundMetrics = InventoryInboundMonitorResponse["metrics"];

export function getInventoryCleanupStrategy(status: InventoryAgeStatus) {
  if (status === "stagnant") {
    return { label: "促销 / 调拨 / 清退", note: "停止补货，比较活动毛利、跨仓需求与退供成本后选择方案。" };
  }
  if (status === "slow") {
    return { label: "控补货并提动销", note: "先控制新增库存，再复盘价格、渠道和投放是否能改善动销。" };
  }
  if (status === "aged") {
    return { label: "调拨 / 人工复核", note: "高库龄但仍有动销，优先核对区域需求、残值和售后风险。" };
  }
  if (status === "no_stock") {
    return { label: "等待快照确认", note: "当前没有可用库存，不进入清理执行。" };
  }
  return { label: "持续观察", note: "库存处于健康区间，继续观察库龄和近 30 日动销。" };
}

export function InventoryAgeSummaryPanel({
  buckets,
  stockValueComplete,
  selectedBuckets,
  onToggleBucket,
}: {
  buckets: AgeDistributionBucket[];
  stockValueComplete: boolean;
  selectedBuckets: string[];
  onToggleBucket: (key: string) => void;
}) {
  const totals = buckets.reduce((current, bucket) => ({
    count: current.count + bucket.count,
    quantity: current.quantity + bucket.quantity,
    valueCents: current.valueCents + bucket.valueCents,
  }), { count: 0, quantity: 0, valueCents: 0 });

  return <section className="panel table-panel inventory-age-summary-panel" aria-labelledby="inventory-age-summary-title">
    <div className="table-toolbar">
      <div><h2 id="inventory-age-summary-title">库龄区间汇总</h2><p>当前筛选范围的全量区间汇总；下方明细仍按服务端分页读取。</p></div>
      <span className="soft-tag">10 个标准区间 · 点击区间继续筛选</span>
    </div>
    <div className="data-table-wrap">
      <table className="data-table inventory-age-summary-table">
        <thead><tr><th>库龄区间</th><th>SKU × 仓库</th><th>库存数量</th><th>数量占比</th><th>{stockValueComplete ? "库存金额" : "已覆盖库存金额"}</th><th>金额占比</th><th>筛选</th></tr></thead>
        <tbody>{buckets.map((bucket) => {
          const selected = selectedBuckets.includes(bucket.key);
          return <tr className={selected ? "selected" : ""} key={bucket.key}><td><strong>{bucket.label}</strong></td><td>{formatCount(bucket.count)}</td><td>{formatCount(bucket.quantity)}</td><td>{formatRate(bucket.quantityShare)}</td><td>{formatCurrencyFromCents(bucket.valueCents)}</td><td>{formatRate(bucket.valueShare)}</td><td><button type="button" className="row-action" aria-pressed={selected} onClick={() => onToggleBucket(bucket.key)}>{selected ? "取消" : "筛选"}</button></td></tr>;
        })}</tbody>
        <tfoot><tr><th>合计</th><th>{formatCount(totals.count)}</th><th>{formatCount(totals.quantity)}</th><th>{totals.quantity > 0 ? "100.0%" : "—"}</th><th>{formatCurrencyFromCents(totals.valueCents)}</th><th>{totals.valueCents > 0 ? "100.0%" : "—"}</th><th>—</th></tr></tfoot>
      </table>
    </div>
    {!stockValueComplete && <p className="inventory-panel-disclosure">金额只包含已有可信固定成本的库存，未覆盖成本不会静默记为 0 元。</p>}
  </section>;
}

export function InventoryPlanWorkflowPanel({ summary }: { summary: PlanSummary }) {
  const stages = [
    { index: "1", label: "风险识别", value: "库存总览", note: "库存、销量、在途和质量门禁共同生成建议。" },
    { index: "2", label: "加入草稿", value: `${formatCount(summary.draftCount)} 项`, note: "同货品、同仓库只保留一个有效草稿。" },
    { index: "3", label: "人工确认", value: `${formatCount(summary.confirmedCount)} 项`, note: "确认数量立即计入计划在途，阻止重复建议。" },
    { index: "4", label: "采购执行", value: `${formatCount(summary.activeQuantity)} 件`, note: "逐项生成采购任务，记录负责人和预计到货。" },
    { index: "5", label: "快照复盘", value: `${formatCount(summary.completedCount)} 项`, note: "完成后等待新库存快照核对实到和覆盖天数。" },
  ];

  return <section className="panel inventory-workflow-panel" aria-labelledby="inventory-plan-workflow-title">
    <header className="inventory-workbench-heading"><div><span className="eyebrow">EXECUTION LOOP</span><h2 id="inventory-plan-workflow-title">备货执行闭环</h2><p>系统建议 → 人工确认 → 采购执行 → 库存快照复盘。</p></div><span className="soft-tag">人工确认 · 不自动下单</span></header>
    <div className="inventory-workflow-rail">{stages.map((stage) => <article key={stage.index}><i>{stage.index}</i><div><span>{stage.label}</span><strong>{stage.value}</strong><p>{stage.note}</p></div></article>)}</div>
    <footer>草稿、已确认以及完成后尚未被新快照覆盖的数量会持续计入计划在途。</footer>
  </section>;
}

export function InventoryStalePlaybookPanel({ metrics, hasAgeSales }: { metrics: AgeMetrics; hasAgeSales: boolean }) {
  const nonStagnantCleanupCount = Math.max(0, metrics.cleanupCount - metrics.stagnantCount);
  const cards = [
    {
      tone: "red",
      label: "滞销清理",
      value: hasAgeSales ? `${formatCount(metrics.stagnantCount)} 项` : "待补销量",
      note: hasAgeSales ? "库龄≥90天且前30天销量为0：停止补货，评估促销、调拨或清退。" : "报表缺少前30天销量，暂不输出精确滞销结论。",
    },
    {
      tone: "purple",
      label: "低动销 / 高库龄",
      value: `${formatCount(nonStagnantCleanupCount)} 项`,
      note: "仍有动销时先控补货，比较区域需求与活动空间，再确定处理方式。",
    },
    {
      tone: "orange",
      label: "90天以上暴露",
      value: `${formatCount(metrics.aged90Count)} 项`,
      note: `已覆盖货值 ${formatCurrencyFromCents(metrics.aged90ValueCents)}，缺失成本不计入金额。`,
    },
    {
      tone: "blue",
      label: "执行边界",
      value: "人工确认",
      note: "系统只创建运营事项，不会自动删除、调减或重导库存。",
    },
  ];
  const stages = [
    ["1", "识别候选", "按库龄、动销、库存和货值形成有界清单。"],
    ["2", "选择方案", "促销、调拨、退供或人工复核，明确负责人。"],
    ["3", "事项跟进", "在运营事务记录截止日、沟通和执行进展。"],
    ["4", "快照复盘", "比较开始与现存库存，决定关闭或续期。"],
  ];

  return <section className="panel inventory-stale-playbook" aria-labelledby="inventory-stale-playbook-title">
    <header className="inventory-workbench-heading"><div><span className="eyebrow">CLEANUP PLAYBOOK</span><h2 id="inventory-stale-playbook-title">清理策略与执行闭环</h2><p>先区分风险，再把清理方案、负责人和复盘证据落到运营事项。</p></div><span className="soft-tag">不自动修改库存</span></header>
    <div className="inventory-playbook-grid">{cards.map((card) => <article className={`tone-${card.tone}`} key={card.label}><span>{card.label}</span><strong>{card.value}</strong><p>{card.note}</p></article>)}</div>
    <div className="inventory-cleanup-rail">{stages.map(([index, label, note]) => <article key={index}><i>{index}</i><div><strong>{label}</strong><p>{note}</p></div></article>)}</div>
  </section>;
}

export function InventoryInboundActionPanel({
  metrics,
  nativeComparisonAvailable,
  onShowMissingSuppliers,
  onOpenStale,
}: {
  metrics: InboundMetrics;
  nativeComparisonAvailable: boolean;
  onShowMissingSuppliers: () => void;
  onOpenStale: () => void;
}) {
  const actions = [
    {
      tone: metrics.missingSupplierCount > 0 ? "orange" : "green",
      label: "供应商映射",
      value: metrics.missingSupplierCount > 0 ? `${formatCount(metrics.missingSupplierCount)} 项待补` : "已完整",
      note: "先补齐 ERP 货品供应商，才能把采购和退供责任落到具体对象。",
      action: metrics.missingSupplierCount > 0 ? <button type="button" className="row-action" onClick={onShowMissingSuppliers}>查看缺口</button> : null,
    },
    {
      tone: metrics.salesMatchRate >= 0.6 ? "green" : "red",
      label: "同仓销量匹配",
      value: formatRate(metrics.salesMatchRate),
      note: "7/30/90 日出库只使用同货品、同仓库的正向销量；退款不算出库。",
      action: null,
    },
    {
      tone: metrics.staleItemCount > 0 ? "red" : "green",
      label: "滞销 / 长库龄",
      value: `${formatCount(metrics.staleItemCount)} 项`,
      note: `已覆盖风险货值 ${formatCurrencyFromCents(metrics.staleValueCents)}，建议转入清理事项。`,
      action: metrics.staleItemCount > 0 ? <button type="button" className="row-action" onClick={onOpenStale}>转到滞销清理</button> : null,
    },
    {
      tone: nativeComparisonAvailable ? "blue" : "gray",
      label: "京东原生指标",
      value: nativeComparisonAvailable ? "已接入" : "未接入",
      note: nativeComparisonAvailable ? "可并排核对原生与系统计算结果。" : "当前不展示伪造差异；接入前先固定字段、快照、RDC/DC 范围和单位。",
      action: null,
    },
  ];

  return <section className="panel inventory-inbound-actions" aria-labelledby="inventory-inbound-actions-title">
    <header className="inventory-workbench-heading"><div><span className="eyebrow">NEXT ACTIONS</span><h2 id="inventory-inbound-actions-title">入仓监控建议下一步</h2><p>先补数据缺口，再处理风险，最后才做原生指标对账。</p></div><span className="soft-tag">计算口径，不替代京东原生口径</span></header>
    <div className="inventory-action-grid">{actions.map((action) => <article className={`tone-${action.tone}`} key={action.label}><span>{action.label}</span><strong>{action.value}</strong><p>{action.note}</p>{action.action}</article>)}</div>
  </section>;
}
