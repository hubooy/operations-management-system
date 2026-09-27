"use client";

import { useEffect, useState } from "react";
import type { FinanceTarget } from "./module-view-shared";
import { formatCurrencyFromCents } from "./module-view-shared";
import { requestJson } from "@/lib/http/api-client";

type AnnualShop = {
  key: string; platform: string; shopName: string; manager: string; target: FinanceTarget | null;
  netSalesCents: number | null; profitCents: number | null; salesProgress: number | null; profitProgress: number | null;
  grossMarginBps: number | null; grossMarginGapBps: number | null;
  promotionFeeRatioBps: number | null; promotionFeeGapBps: number | null;
  availableMonths: string[]; missingMonths: string[]; missingGrossMarginMonths: string[];
};
type AnnualProgress = {
  year: string; cutoffMonth: string | null; missingMonths: string[]; items: AnnualShop[];
  pagination: { total: number; truncated: boolean };
};
const amount = (value: number | null) => value === null ? "暂无财报" : formatCurrencyFromCents(value);
const rate = (value: number | null) => value === null ? "暂无财报" : `${(value / 100).toFixed(1)}%`;
const gap = (value: number | null) => value === null ? "暂无对照" : `${value > 0 ? "+" : ""}${(value / 100).toFixed(1)} 个百分点`;
function Progress({ value, target }: { value: number | null; target: number }) {
  return <div className="table-progress"><span><i style={{ width: `${Math.min(100, Math.max(0, (value ?? 0) * 100))}%` }} /></span><small>{target <= 0 ? "未设目标" : value === null ? "暂无财报" : `${(value * 100).toFixed(1)}%`}</small></div>;
}

function AmountMetric({ target, actual, progress }: { target: number; actual: number | null; progress: number | null }) {
  return <div className="annual-target-metric"><strong>{target > 0 ? amount(target) : "未设目标"}</strong><small>年累计 {amount(actual)}</small><Progress value={progress} target={target} /></div>;
}

function RateMetric({ target, actual, difference, lowerIsBetter = false }: { target: number; actual: number | null; difference: number | null; lowerIsBetter?: boolean }) {
  const favorable = difference === null ? null : lowerIsBetter ? difference <= 0 : difference >= 0;
  return <div className="annual-target-metric annual-rate-metric"><strong>{target > 0 ? `${(target / 100).toFixed(1)}%` : "未设目标"}</strong><small>年累计 {rate(actual)}</small><em className={favorable === null ? "" : favorable ? "favorable" : "unfavorable"}>{gap(difference)}</em></div>;
}

export default function FinanceAnnualProgressView({ year, refreshKey, canManageTargets, busy = false, onEdit, onDelete }: {
  year: string; refreshKey: number; canManageTargets: boolean; busy?: boolean; onEdit: (row: AnnualShop) => void; onDelete: (row: AnnualShop) => void;
}) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AnnualProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => { if (active) { setError("年度进度读取超时，请重试。"); setLoading(false); controller.abort(); } }, 30_000);
    setLoading(true); setError(""); setData(null);
    void requestJson<AnnualProgress>(`/api/finance/targets?view=annual&year=${year}&page=${page}&pageSize=100`, { signal: controller.signal })
      .then((result) => {
        if (!Array.isArray(result.items) || !Array.isArray(result.missingMonths) || !result.pagination || result.year !== year) throw new Error("年度进度响应格式不完整，请重试。");
        if (active && !controller.signal.aborted) setData(result);
      })
      .catch((reason) => { if (active && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : "年度进度读取失败。"); })
      .finally(() => { window.clearTimeout(timeout); if (active && !controller.signal.aborted) setLoading(false); });
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [year, page, refreshKey, retry]);
  return <section className="panel finance-shop-panel" aria-busy={loading}>
    <div className="finance-panel-heading"><div><span className="eyebrow">ANNUAL SHOP TARGETS</span><h2>店铺年度目标进度</h2><p>{year} 年累计财报对照全年目标；金额、比率都按已完成月份汇总后计算。</p></div><span className="soft-tag">{data?.cutoffMonth ? `财报截至 ${data.cutoffMonth}` : "暂无财报截止月份"}</span></div>
    {data && data.missingMonths.length > 0 && <p className="inline-feedback warning">缺少财报月份：{data.missingMonths.join("、")}。当前金额仅累计已导入月份。</p>}
    {error && <div className="inline-feedback error" role="alert">{error}<button type="button" className="row-action" onClick={() => setRetry((value) => value + 1)}>重试</button></div>}
    {loading ? <div className="table-state">正在读取年累计进度…</div> : data && <>
      <div className="data-table-wrap"><table className="data-table finance-shop-table annual-target-progress-table" data-column-filter-scope={page === 1 && !data.pagination.truncated ? "full" : "none"}><thead><tr><th>平台 / 店铺</th><th>负责人</th><th>销售目标进度</th><th>利润目标进度</th><th>大毛利率目标</th><th>推广费目标</th><th>数据覆盖</th>{canManageTargets && <th>操作</th>}</tr></thead><tbody>
        {data.items.map((row) => <tr key={row.key}><td><strong>{row.shopName}</strong><small>{row.platform}</small></td><td>{row.manager || "—"}</td><td><AmountMetric target={row.target?.salesTargetCents ?? 0} actual={row.netSalesCents} progress={row.salesProgress} /></td><td><AmountMetric target={row.target?.profitTargetCents ?? 0} actual={row.profitCents} progress={row.profitProgress} /></td><td><RateMetric target={row.target?.grossMarginBps ?? 0} actual={row.grossMarginBps} difference={row.grossMarginGapBps} /></td><td><RateMetric target={row.target?.promotionFeeRatioBps ?? 0} actual={row.promotionFeeRatioBps} difference={row.promotionFeeGapBps} lowerIsBetter /></td><td><small>{row.availableMonths.length ? `已计 ${row.availableMonths.length} 个月` : "暂无财报"}</small>{row.missingMonths.length > 0 && <small title={row.missingMonths.join("、")}>缺 {row.missingMonths.map((month) => Number(month.slice(5))).join("、")} 月</small>}{row.missingGrossMarginMonths.length > 0 && <small title={row.missingGrossMarginMonths.join("、")}>大毛利率口径不全</small>}</td>{canManageTargets && <td><div className="finance-target-row-actions"><button type="button" disabled={busy} onClick={() => onEdit(row)}>{row.target ? "编辑目标" : "填写目标"}</button>{row.target && <button type="button" className="danger" disabled={busy} onClick={() => onDelete(row)}>删除</button>}</div></td>}</tr>)}
        {data.items.length === 0 && <tr><td colSpan={canManageTargets ? 8 : 7}><div className="table-state">该年份暂无店铺财报或年度目标。</div></td></tr>}
      </tbody></table></div>
      {(page > 1 || data.pagination.truncated) && <div className="customer-service-pagination"><button type="button" className="row-action" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>第 {page} 页 · 共 {data.pagination.total} 家店铺</span><button type="button" className="row-action" disabled={!data.pagination.truncated} onClick={() => setPage((value) => value + 1)}>下一页</button></div>}
    </>}
  </section>;
}
