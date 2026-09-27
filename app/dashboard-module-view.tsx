"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { requestJson } from "@/lib/http/api-client";
import {
  type SalesRangeLabel,
  type BiDashboardResponse,
  salesRangeMap,
  formatCurrencyFromCents,
  formatCount,
  rateAsPercent,
  formatRate,
  formatYearOverYear,
  formatDateTime,
  formatChange,
  comparisonHint,
  Dot,
  MetricCard,
  SectionHeader,
} from "./module-view-shared";

export default function DashboardView({
  range,
  customStartDate,
  customEndDate,
}: {
  range: SalesRangeLabel;
  customStartDate: string;
  customEndDate: string;
}) {
  const apiRange = salesRangeMap[range];
  const [dashboard, setDashboard] = useState<BiDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const requestGenerationRef = useRef(0);
  const requestControllerRef = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    requestControllerRef.current?.abort();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ range: apiRange });
      if (apiRange === "custom") {
        query.set("startDate", customStartDate);
        query.set("endDate", customEndDate);
      }
      const payload = await requestJson<BiDashboardResponse>(`/api/bi/overview?${query}`, {
        signal: controller.signal,
      });
      if (
        payload?.projection !== "dashboard" ||
        payload.contractVersion !== "bi-dashboard-read-model-v1" ||
        !payload.sales?.current ||
        !payload.inventory?.metrics
      )
        throw new Error("经营数据读取失败");
      if (generation !== requestGenerationRef.current) return;
      setDashboard(payload);
    } catch (reason) {
      if (
        controller.signal.aborted ||
        generation !== requestGenerationRef.current
      )
        return;
      controller.abort();
      setError(
        reason instanceof Error ? reason.message : "暂时无法读取经营看板",
      );
    } finally {
      if (generation === requestGenerationRef.current) setLoading(false);
    }
  }, [apiRange, customEndDate, customStartDate]);
  useEffect(() => {
    void load();
    return () => requestControllerRef.current?.abort();
  }, [load, retryKey]);
  if (loading && !dashboard)
    return (
      <section className="panel data-state" role="status">
        <span className="state-spinner" />
        <strong>正在同步 BI 经营看板</strong>
        <p>正在读取销售与库存的一致性经营投影…</p>
      </section>
    );
  if (!dashboard)
    return (
      <section className="panel data-state data-state-error" role="alert">
        <span className="state-symbol">!</span>
        <strong>BI 看板加载失败</strong>
        <p>{error || "暂时无法读取经营数据"}</p>
        <button
          className="secondary-button"
          onClick={() => setRetryKey((value) => value + 1)}
        >
          重新加载
        </button>
      </section>
    );
  const sales = dashboard.sales;
  const inventory = dashboard.inventory;
  const current = sales.current;
  const previous = sales.previous;
  const yearAgo = sales.yearAgo;
  const daily = sales.daily ?? [];
  const maxSales = Math.max(
    1,
    ...daily.map((item) => Math.max(0, item.netSalesCents)),
  );
  const maxProfit = Math.max(
    1,
    ...daily.map((item) => Math.max(0, item.grossProfitCents)),
  );
  const outlets = [...(sales.outlets ?? [])]
    .sort((left, right) => right.netSalesCents - left.netSalesCents)
    .slice(0, 5);
  const inventoryAlertsAvailable =
    inventory.metrics.inventoryAlertsEnabled &&
    !inventory.metrics.recommendationsSuppressed;
  const healthScore = dashboard.inventoryHealthScore;
  return (
    <>
      <section className="dashboard-sync-bar">
        <span>
          <Dot tone="green" />
          已同步经营数据
        </span>
        <strong>
          {sales.startDate} 至 {sales.endDate}
        </strong>
        <small>
          销售批次 {sales.latestBatch?.fileName ?? "暂无"} · 库存快照{" "}
          {inventory.sync.inventoryAsOf ?? "暂无"}
        </small>
        <button
          className="row-action"
          onClick={() => void load()}
          disabled={loading}
        >
          {loading ? "同步中…" : "↻ 刷新"}
        </button>
      </section>
      {error && (
        <section className="inventory-feedback inventory-feedback-error" role="alert">
          <span>!</span>
          <div><strong>经营看板刷新失败</strong><p>{error}；当前仍显示上一次成功结果。</p></div>
          <button className="row-action" onClick={() => setRetryKey((value) => value + 1)}>重试</button>
        </section>
      )}
      <section className="metrics-grid data-refresh-region" aria-busy={loading}>
        <MetricCard
          label="净销售额"
          value={formatCurrencyFromCents(current.netSalesCents)}
          change={formatChange(current.netSalesCents, previous?.netSalesCents)}
          hint={comparisonHint(
            current.netSalesCents,
            previous?.netSalesCents,
            yearAgo?.netSalesCents,
          )}
          tone="blue"
        />
        <MetricCard
          label="订单毛利"
          value={formatCurrencyFromCents(current.grossProfitCents)}
          change={formatChange(
            current.grossProfitCents,
            previous?.grossProfitCents,
          )}
          hint={comparisonHint(
            current.grossProfitCents,
            previous?.grossProfitCents,
            yearAgo?.grossProfitCents,
          )}
          tone="green"
        />
        <MetricCard
          label="综合大毛利率"
          value={formatRate(current.grossMarginRate)}
          change={formatChange(
            rateAsPercent(current.grossMarginRate),
            rateAsPercent(previous?.grossMarginRate),
          )}
          hint={comparisonHint(
            rateAsPercent(current.grossMarginRate),
            rateAsPercent(previous?.grossMarginRate),
            rateAsPercent(yearAgo?.grossMarginRate),
          )}
          tone="purple"
        />
        <MetricCard
          label="销售退货率"
          value={formatRate(current.refundRate)}
          change={formatChange(
            rateAsPercent(current.refundRate),
            rateAsPercent(previous?.refundRate),
          )}
          hint={comparisonHint(
            rateAsPercent(current.refundRate),
            rateAsPercent(previous?.refundRate),
            rateAsPercent(yearAgo?.refundRate),
          )}
          tone="orange"
        />
      </section>
      <section className="dashboard-main-grid data-refresh-region" aria-busy={loading}>
        <article className="panel trend-panel">
          <SectionHeader
            title="销售与毛利趋势"
            note="按当前统计周期内的已导入日度明细汇总"
          />
          <div className="chart-legend">
            <span>
              <Dot tone="blue" />
              净销售额
            </span>
            <span>
              <Dot tone="green" />
              订单毛利
            </span>
          </div>
          <div className="bar-chart">
            {daily.map((item, index) => (
              <div className="bar-group" key={item.date}>
                <div className="bar-stack">
                  <span
                    className="bar sales-bar"
                    style={{
                      height: `${Math.max(2, (Math.max(0, item.netSalesCents) / maxSales) * 100)}%`,
                    }}
                  />
                  <span
                    className="bar profit-bar"
                    style={{
                      height: `${Math.max(2, (Math.max(0, item.grossProfitCents) / maxProfit) * 100)}%`,
                    }}
                  />
                </div>
                <small>
                  {daily.length <= 7 ||
                  index % Math.ceil(daily.length / 7) === 0
                    ? item.date.slice(5)
                    : ""}
                </small>
              </div>
            ))}
          </div>
          <div className="chart-summary">
            <div>
              <span>日均净销售额</span>
              <strong>
                {formatCurrencyFromCents(
                  daily.length ? current.netSalesCents / daily.length : 0,
                )}
              </strong>
            </div>
            <div>
              <span>活跃网店</span>
              <strong>{formatCount((sales.outlets ?? []).length)} 个</strong>
            </div>
            <div>
              <span>库存健康度</span>
              <strong
                className={
                  healthScore === null || healthScore < 70
                    ? "orange-text"
                    : "green-text"
                }
              >
                {healthScore === null ? "待校验" : `${healthScore} 分`}
              </strong>
            </div>
          </div>
        </article>
        <article className="panel alert-panel">
          <SectionHeader
            title="库存预警中心"
            note={
              inventory.metrics.inventoryAlertsEnabled
                ? "来自最新库存快照与销售需求联动"
                : "系统设置已关闭库存异常提醒"
            }
          />
          {inventoryAlertsAvailable ? (
            <>
              <div className="alert-score">
                <div className="score-ring">
                  <strong>{healthScore}</strong>
                  <small>健康分</small>
                </div>
                <div>
                  <strong>
                    {(healthScore ?? 0) >= 80
                      ? "整体经营稳定"
                      : "建议关注库存风险"}
                  </strong>
                  <p>库存快照 {inventory.sync.inventoryAsOf ?? "未同步"}</p>
                </div>
              </div>
              <div className="alert-list">
                <button>
                  <span className="alert-icon danger">!</span>
                  <span>
                    <b>紧急补货</b>
                    <small>可售天数低于预警线的货品</small>
                  </span>
                  <em>{formatCount(inventory.metrics.urgentCount)}</em>
                </button>
                <button>
                  <span className="alert-icon warning">↓</span>
                  <span>
                    <b>建议补货</b>
                    <small>销量需求与可用库存计算得出</small>
                  </span>
                  <em>{formatCount(inventory.metrics.replenishCount)}</em>
                </button>
                <button>
                  <span className="alert-icon purple">◷</span>
                  <span>
                    <b>低动销库存</b>
                    <small>当前未匹配销售需求的库存商品</small>
                  </span>
                  <em>{formatCount(inventory.metrics.noSalesCount)}</em>
                </button>
              </div>
            </>
          ) : (
            <div className="data-state dashboard-inventory-alert-paused">
              <span className="state-symbol">!</span>
              <strong>
                {inventory.metrics.inventoryAlertsEnabled
                  ? "库存数据质量待校验"
                  : "库存异常提醒已关闭"}
              </strong>
              <p>
                {inventory.metrics.inventoryAlertsEnabled
                  ? inventory.metrics.qualityIssues
                      .map((issue) => issue.message)
                      .join("；")
                  : "管理员可在系统设置中重新开启；关闭期间不展示精确预警分数和补货数量。"}
              </p>
            </div>
          )}
        </article>
      </section>
      <section className="dashboard-bottom-grid data-refresh-region" aria-busy={loading}>
        <article className="panel">
          <SectionHeader title="网店经营排行" note="按销售净额排序" />
          <div className="rank-list">
            {outlets.map((outlet, index) => (
              <div className="rank-row" key={outlet.name}>
                <span className={`rank-number rank-${index + 1}`}>
                  {index + 1}
                </span>
                <div className="shop-avatar">{outlet.platform.slice(0, 1)}</div>
                <div className="rank-name">
                  <strong>{outlet.name}</strong>
                  <small>
                    {outlet.platform} · {formatCount(outlet.orderCount)} 单
                  </small>
                </div>
                <div className="mini-progress">
                  <i
                    style={{ width: `${Math.max(4, outlet.shareRate * 100)}%` }}
                  />
                </div>
                <div className="rank-value">
                  <strong>
                    {formatCurrencyFromCents(outlet.netSalesCents)}
                  </strong>
                  <small
                    className={
                      outlet.salesYearOverYearRate !== null &&
                      outlet.salesYearOverYearRate < 0
                        ? "green-text"
                        : "red-text"
                    }
                  >
                    {formatYearOverYear(outlet.salesYearOverYearRate)}
                  </small>
                </div>
              </div>
            ))}
            {outlets.length === 0 && (
              <div className="table-state">当前周期没有可展示的网店数据。</div>
            )}
          </div>
        </article>
        <article className="panel todo-panel">
          <SectionHeader
            title="数据同步状态"
            note="所有分析以最近成功导入为准"
          />
          <div className="dashboard-data-status">
            <div>
              <span>销售明细</span>
              <strong>{sales.latestBatch?.fileName ?? "未导入"}</strong>
              <small>
                {sales.latestBatch?.completedAt
                  ? formatDateTime(sales.latestBatch.completedAt)
                  : "请前往数据导入"}
              </small>
            </div>
            <div>
              <span>库存快照</span>
              <strong>{inventory.sync.latestInventoryFile ?? "未导入"}</strong>
              <small>
                {inventory.sync.inventoryAsOf ?? "请前往库存管理同步"}
              </small>
            </div>
            <div>
              <span>销售需求匹配</span>
              <strong>
                {formatRate(inventory.metrics.salesDemandMatchRate)}
              </strong>
              <small>库存商品已匹配销售需求的比例</small>
            </div>
          </div>
        </article>
      </section>
    </>
  );
}
