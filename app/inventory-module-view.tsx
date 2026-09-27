"use client";
import { toggleSingleFilter, toggleFilterGroup } from "@/lib/ui/summary-filter";
import { inventoryWarehouseCategoryLabel } from "@/lib/inventory/warehouse-classification";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ModuleViewKey } from "./shell/navigation-catalog";
import { InventoryKpiCard } from "./module-view-business-ui";
import GuangdongInventoryView from "./inventory-guangdong-view";
import InventoryFilterBar, {
  readInventorySharedFilters,
  writeInventorySharedFilters,
  type InventorySharedFilters,
  type InventorySharedFilterOptions,
} from "./inventory-filter-bar";
import {
  getInventoryCleanupStrategy,
  InventoryAgeSummaryPanel,
  InventoryInboundActionPanel,
  InventoryPlanWorkflowPanel,
  InventoryStalePlaybookPanel,
} from "./inventory-workbench-panels";
import {
  type CurrentUser,
  type InventoryHealthStatus,
  type InventoryAgeStatus,
  type ReplenishmentPlanItem,
  type InventoryOverviewResponse,
  type InventoryAgeAnalysisResponse,
  type InventoryAgeItem,
  type InventoryInboundMonitorResponse,
  type InventoryImportResponse,
  type InventoryChunkUploadResponse,
  MAX_INVENTORY_FILE_SIZE,
  DIRECT_INVENTORY_FILE_SIZE,
  INVENTORY_UPLOAD_CHUNK_SIZE,
  formatCurrencyFromCents,
  formatCount,
  formatRate,
  formatFileSize,
  shanghaiIsoToday,
  useDebouncedValue,
  effectivePageForScope,
  Dot,
  SectionHeader,
} from "./module-view-shared";
import { confirmAndSyncDraftPlans, retainFailedPlanSelections } from "../lib/inventory/replenishment-batch";

type InventoryTab = ModuleViewKey<"inventory">;

type InventoryWorkItemDraft = {
  kind: "procurement" | "stale_cleanup";
  label: string;
  planId?: string;
  inventoryKey?: string;
  owner: string;
  dueDate: string;
  expectedArrivalDate: string;
  planType: "daily" | "new_product";
  cleanupStrategy: "promotion" | "transfer" | "return" | "review";
  expectedConsumptionDays: number;
  notes: string;
};

type InventoryMappingItem = InventoryOverviewResponse["mapping"]["samples"][number];
type InventoryPlanWarehouseOption = InventoryMappingItem["warehouseOptions"][number];

type ReplenishmentPlanDraft = {
  productCode: string;
  productName: string;
  brand: string;
  category: string;
  supplier: string;
  warehouseOptions: InventoryPlanWarehouseOption[];
  warehouse: string;
  key: string;
  currentStockQuantity: number;
  sales30dQuantity: number | null;
  suggestedQuantity: number | null;
  plannedQuantity: number;
  expectedConsumptionDays: number | null;
  buyer: string;
  orderDate: string;
  planType: string;
  operatorName: string;
  department: string;
  expectedArrivalDate: string;
  status: "draft" | "confirmed";
  requiresInspection: boolean;
  notes: string;
};

type DingTalkSyncOutcome = "created" | "updated" | "already_synced";
type DingTalkGroupPreview = {
  ok: true;
  status: "preview" | "delivered" | "already_delivered";
  planIds: string[];
  planCount: number;
  targetGroupName: string;
  robotName: string;
  message: string;
  buyerNames: string[];
  previewToken: string;
};

type ReplenishmentImportResponse = {
  ok: boolean;
  status?: "imported" | "rejected";
  importedCount?: number;
  confirmedCount?: number;
  draftCount?: number;
  confirmedPlanIds?: string[];
  inventoryAsOf?: string | null;
  message?: string;
};

async function requestPlanDingTalkSync(planId: string) {
  const response = await fetch("/api/inventory/replenishment/dingtalk", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id: planId }),
  });
  const payload = await response.json().catch(() => null) as {
    ok?: boolean;
    outcome?: DingTalkSyncOutcome;
    message?: string;
    error?: string;
  } | null;
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.message || payload?.error || "提交钉钉多维表失败");
  }
  return payload.outcome ?? "created";
}

async function requestPlanStatusUpdate(
  planId: string,
  status: ReplenishmentPlanItem["status"],
  plannedQuantity?: number,
) {
  const response = await fetch("/api/inventory/replenishment", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      id: planId,
      status,
      ...(plannedQuantity === undefined ? {} : { plannedQuantity }),
    }),
  });
  const payload = await response.json().catch(() => null) as {
    ok?: boolean;
    item?: ReplenishmentPlanItem;
    message?: string;
  } | null;
  if (!response.ok || !payload?.ok || !payload.item?.id) {
    throw new Error(payload?.message || "更新备货计划失败");
  }
  return payload.item;
}

async function requestDingTalkGroupMessage(
  action: "preview" | "send",
  planIds: string[],
  previewToken?: string,
) {
  const response = await fetch("/api/inventory/replenishment/dingtalk/group", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, planIds, ...(previewToken ? { previewToken } : {}) }),
  });
  const payload = await response.json().catch(() => null) as (Partial<DingTalkGroupPreview> & {
    message?: string;
    error?: string;
  }) | null;
  if (!response.ok || !payload?.ok || !payload.previewToken || !payload.targetGroupName
    || !payload.robotName || !Array.isArray(payload.planIds) || !Array.isArray(payload.buyerNames)) {
    throw new Error(payload?.message || payload?.error || "备货群消息处理失败");
  }
  return payload as DingTalkGroupPreview;
}

function addCalendarDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function csvCell(value: string | number | null) {
  let text = value === null ? "" : String(value);
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function downloadInventoryCsv(fileName: string, rows: Array<Array<string | number | null>>) {
  const content = `\ufeff${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
  const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

const inventoryStatusMeta: Record<InventoryHealthStatus, { label: string; tone: string }> = {
  no_stock: { label: "无库存可用", tone: "danger" },
  urgent: { label: "紧急补货", tone: "danger" },
  warning: { label: "补货预警", tone: "warning" },
  stale: { label: "积压风险", tone: "danger" },
  slow: { label: "低周转", tone: "purple" },
  healthy: { label: "库存健康", tone: "success" },
};

const inventoryAgeStatusMeta: Record<InventoryAgeStatus, { label: string; tone: string }> = {
  healthy: { label: "库龄健康", tone: "success" },
  aged: { label: "高库龄", tone: "warning" },
  slow: { label: "低动销", tone: "purple" },
  stagnant: { label: "滞销清理", tone: "danger" },
  no_stock: { label: "无可用库存", tone: "gray" },
};

type InventoryAgeDistributionBucket = InventoryAgeAnalysisResponse["fineDistribution"][number];

function niceChartMaximum(value: number) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const roughStep = value / 4;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalizedStep = roughStep / magnitude;
  const niceStep = normalizedStep <= 1 ? 1 : normalizedStep <= 2 ? 2 : normalizedStep <= 2.5 ? 2.5 : normalizedStep <= 5 ? 5 : 10;
  const step = niceStep * magnitude;
  return Math.ceil(value / step) * step;
}

function chartNumber(value: number) {
  return Math.round(value).toLocaleString("zh-CN");
}

function chartAmount(value: number) {
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function warehouseTurnoverLabel(metric: { inventoryQuantity: number; salesQuantity: number | null; turnoverDays: number | null }) {
  if (metric.turnoverDays !== null) return `${metric.turnoverDays.toFixed(1)}天`;
  if (metric.salesQuantity === 0 && metric.inventoryQuantity > 0) return "∞";
  return "—";
}

function planDraftFromCandidate(candidate: {
  productCode: string;
  productName: string;
  brand: string;
  category: string;
  supplier: string;
  warehouseOptions: InventoryPlanWarehouseOption[];
  totalSalesQuantity: number | null;
}, preferredKey?: string): ReplenishmentPlanDraft {
  const option = candidate.warehouseOptions.find((item) => item.key === preferredKey)
    ?? candidate.warehouseOptions.find((item) => !item.inDraftPlan)
    ?? candidate.warehouseOptions[0];
  const sales = candidate.totalSalesQuantity;
  const consumptionDays = sales !== null && sales > 0 && option
    ? Math.min(3650, Math.round((Math.max(0, option.availableQuantity) / (sales / 30)) * 10) / 10)
    : null;
  return {
    productCode: candidate.productCode,
    productName: candidate.productName,
    brand: candidate.brand,
    category: candidate.category,
    supplier: candidate.supplier,
    warehouseOptions: candidate.warehouseOptions,
    warehouse: option?.warehouse ?? "",
    key: option?.key ?? "",
    currentStockQuantity: option?.availableQuantity ?? 0,
    sales30dQuantity: sales,
    suggestedQuantity: option?.suggestedQuantity ?? null,
    plannedQuantity: Math.max(0, option?.suggestedQuantity ?? 0),
    expectedConsumptionDays: consumptionDays,
    buyer: "",
    orderDate: shanghaiIsoToday(),
    planType: "",
    operatorName: "",
    department: "志高项目组",
    expectedArrivalDate: "",
    status: "draft",
    requiresInspection: false,
    notes: "",
  };
}

function InventoryThirtyDayTable({
  samples,
  loading,
  canManageInventory,
  planActionId,
  onCreatePlan,
}: {
  samples: InventoryMappingItem[];
  loading: boolean;
  canManageInventory: boolean;
  planActionId: string;
  onCreatePlan: (productCode: string) => void;
}) {
  return <div className="data-table-wrap data-refresh-region" aria-busy={loading}><table className="data-table inventory-mapping-table"><thead><tr>
    <th>货品编号</th><th>货品名称</th><th>品牌</th><th>分类</th><th>供应商</th>
    <th>京东仓库</th><th>京东仓销量</th><th>京东仓周转</th><th>京东仓在途</th>
    <th>供应商库存</th><th>供应商销量</th><th>供应商周转</th><th>供应商在途</th>
    <th>售后仓库存</th><th>售后仓销量</th>
    <th>广东仓库存</th><th>广东仓销量</th><th>广东仓周转</th><th>广东仓在途</th>
    <th>样品仓库存</th><th>样品仓销量</th><th>样品仓在途</th>
    <th>菜鸟仓库存</th><th>菜鸟仓销量</th><th>菜鸟仓周转</th><th>菜鸟仓在途</th>
    <th>海外仓库存</th><th>海外仓销量</th>
    <th>虚拟仓库存</th><th>虚拟仓销量</th>
    <th>异常仓库存</th><th>异常仓销量</th>
    <th>自营库存</th><th>自营销量</th><th>自营周转</th><th>自营在途</th>
    <th>总库存</th><th>总库存金额</th><th>总在途</th><th>总销量</th><th>总周转天数</th><th>建议补货</th><th>预警</th><th>创建备货计划</th>
  </tr></thead><tbody>{samples.map((gap) => {
    const alert = inventoryStatusMeta[gap.alertStatus];
    const metric = gap.warehouses;
    const quantity = (value: number | null) => value === null ? "—" : formatCount(value);
    return <tr key={gap.key}>
      <td><strong>{gap.productCode}</strong></td><td className="inventory-mapping-name" title={gap.productName}>{gap.productName}</td><td>{gap.brand || "—"}</td><td>{gap.category || "—"}</td><td>{gap.supplier || "未映射供应商"}</td>
      <td>{formatCount(metric.jd.inventoryQuantity)}</td><td>{quantity(metric.jd.salesQuantity)}</td><td>{warehouseTurnoverLabel(metric.jd)}</td><td>{formatCount(metric.jd.inTransitQuantity)}</td>
      <td>{formatCount(metric.dropship.inventoryQuantity)}</td><td>{quantity(metric.dropship.salesQuantity)}</td><td>{warehouseTurnoverLabel(metric.dropship)}</td><td>{formatCount(metric.dropship.inTransitQuantity)}</td>
      <td>{formatCount(metric.afterSales.inventoryQuantity)}</td><td>{quantity(metric.afterSales.salesQuantity)}</td>
      <td>{formatCount(metric.guangdong.inventoryQuantity)}</td><td>{quantity(metric.guangdong.salesQuantity)}</td><td>{warehouseTurnoverLabel(metric.guangdong)}</td><td>{formatCount(metric.guangdong.inTransitQuantity)}</td>
      <td>{formatCount(metric.sample.inventoryQuantity)}</td><td>{quantity(metric.sample.salesQuantity)}</td><td>{formatCount(metric.sample.inTransitQuantity)}</td>
      <td>{formatCount(metric.cainiao.inventoryQuantity)}</td><td>{quantity(metric.cainiao.salesQuantity)}</td><td>{warehouseTurnoverLabel(metric.cainiao)}</td><td>{formatCount(metric.cainiao.inTransitQuantity)}</td>
      <td>{formatCount(metric.overseas.inventoryQuantity)}</td><td>{quantity(metric.overseas.salesQuantity)}</td>
      <td>{formatCount(metric.virtual.inventoryQuantity)}</td><td>{quantity(metric.virtual.salesQuantity)}</td>
      <td>{formatCount(metric.exception.inventoryQuantity)}</td><td>{quantity(metric.exception.salesQuantity)}</td>
      <td>{formatCount(metric.selfOperated.inventoryQuantity)}</td><td>{quantity(metric.selfOperated.salesQuantity)}</td><td>{warehouseTurnoverLabel(metric.selfOperated)}</td><td>{formatCount(metric.selfOperated.inTransitQuantity)}</td>
      <td><strong>{formatCount(gap.totalInventoryQuantity)}</strong></td><td>{formatCurrencyFromCents(gap.totalStockValueCents)}</td><td>{formatCount(gap.totalInTransitQuantity)}</td><td>{quantity(gap.totalSalesQuantity)}</td><td>{warehouseTurnoverLabel({ inventoryQuantity: gap.totalInventoryQuantity, salesQuantity: gap.totalSalesQuantity, turnoverDays: gap.totalTurnoverDays })}</td><td className={(gap.suggestedQuantity ?? 0) > 0 ? "orange-text" : ""}><strong>{quantity(gap.suggestedQuantity)}</strong></td><td><span className={`status status-${alert.tone}`} title={`${gap.alertReason}；${gap.unmatchedWarehouseCount} 个仓库未匹配销量`}><Dot tone={alert.tone === "danger" ? "red" : alert.tone === "warning" ? "orange" : alert.tone === "success" ? "green" : alert.tone} />{gap.alertLabel}</span></td><td>{canManageInventory ? <button type="button" className="row-action primary-row-action" disabled={gap.warehouseOptions.every((option) => option.inDraftPlan) || planActionId === gap.key} onClick={() => onCreatePlan(gap.productCode)}>{gap.warehouseOptions.every((option) => option.inDraftPlan) ? "已在草稿" : "创建备货计划"}</button> : <span className="soft-text">只读</span>}</td>
    </tr>;
  })}{samples.length === 0 && <tr><td colSpan={44}><div className="table-state">当前筛选范围没有近30天销量或库存记录。</div></td></tr>}</tbody></table></div>;
}

function inventoryThirtyDayCsvRows(samples: InventoryMappingItem[]): Array<Array<string | number | null>> {
  const rows: Array<Array<string | number | null>> = [[
    "货品编号", "货品名称", "品牌", "分类", "供应商",
    "京东仓库", "京东仓销量", "京东仓周转", "京东仓在途",
    "供应商库存", "供应商销量", "供应商周转", "供应商在途",
    "售后仓库存", "售后仓销量",
    "广东仓库存", "广东仓销量", "广东仓周转", "广东仓在途",
    "样品仓库存", "样品仓销量", "样品仓在途",
    "菜鸟仓库存", "菜鸟仓销量", "菜鸟仓周转", "菜鸟仓在途",
    "海外仓库存", "海外仓销量",
    "虚拟仓库存", "虚拟仓销量",
    "异常仓库存", "异常仓销量",
    "自营库存", "自营销量", "自营周转", "自营在途",
    "总库存", "总库存金额（元）", "总在途", "总销量", "总周转天数", "建议补货", "预警", "预警说明",
  ]];
  for (const item of samples) {
    const metric = item.warehouses;
    rows.push([
      item.productCode, item.productName, item.brand, item.category, item.supplier,
      metric.jd.inventoryQuantity, metric.jd.salesQuantity, metric.jd.turnoverDays, metric.jd.inTransitQuantity,
      metric.dropship.inventoryQuantity, metric.dropship.salesQuantity, metric.dropship.turnoverDays, metric.dropship.inTransitQuantity,
      metric.afterSales.inventoryQuantity, metric.afterSales.salesQuantity,
      metric.guangdong.inventoryQuantity, metric.guangdong.salesQuantity, metric.guangdong.turnoverDays, metric.guangdong.inTransitQuantity,
      metric.sample.inventoryQuantity, metric.sample.salesQuantity, metric.sample.inTransitQuantity,
      metric.cainiao.inventoryQuantity, metric.cainiao.salesQuantity, metric.cainiao.turnoverDays, metric.cainiao.inTransitQuantity,
      metric.overseas.inventoryQuantity, metric.overseas.salesQuantity,
      metric.virtual.inventoryQuantity, metric.virtual.salesQuantity,
      metric.exception.inventoryQuantity, metric.exception.salesQuantity,
      metric.selfOperated.inventoryQuantity, metric.selfOperated.salesQuantity, metric.selfOperated.turnoverDays, metric.selfOperated.inTransitQuantity,
      item.totalInventoryQuantity, (item.totalStockValueCents / 100).toFixed(2), item.totalInTransitQuantity, item.totalSalesQuantity,
      item.totalTurnoverDays, item.suggestedQuantity, item.alertLabel, item.alertReason,
    ]);
  }
  return rows;
}

function InventoryAgeDistributionChart({ buckets, stockValueComplete }: { buckets: InventoryAgeDistributionBucket[]; stockValueComplete: boolean }) {
  const width = 1200;
  const height = 350;
  const plotLeft = 66;
  const plotRight = 1124;
  const plotTop = 42;
  const plotBottom = 292;
  const plotWidth = plotRight - plotLeft;
  const plotHeight = plotBottom - plotTop;
  const quantityMaximum = niceChartMaximum(Math.max(0, ...buckets.map((bucket) => bucket.quantity)));
  const valueMaximum = niceChartMaximum(Math.max(0, ...buckets.map((bucket) => bucket.valueCents / 100)));
  const slotWidth = buckets.length > 0 ? plotWidth / buckets.length : plotWidth;
  const barWidth = Math.min(64, slotWidth * 0.62);
  const xFor = (index: number) => plotLeft + slotWidth * (index + 0.5);
  const quantityY = (value: number) => plotBottom - Math.max(0, value) / quantityMaximum * plotHeight;
  const valueY = (valueCents: number) => plotBottom - Math.max(0, valueCents / 100) / valueMaximum * plotHeight;
  const linePoints = buckets.map((bucket, index) => `${xFor(index)},${valueY(bucket.valueCents)}`).join(" ");
  const ticks = [0, 1, 2, 3, 4];

  return <div className="age-distribution-chart" role="group" aria-label="库龄分布双轴组合图">
    <div className="age-distribution-svg-wrap">
      <svg className="age-distribution-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="inventory-age-chart-title inventory-age-chart-description">
        <title id="inventory-age-chart-title">库龄库存数量与库存金额分布</title>
        <desc id="inventory-age-chart-description">蓝色柱形表示各库龄区间的可用库存数量，橙色折线表示按固定成本计算的库存金额。</desc>
        <text className="age-chart-axis-title" x={plotLeft - 12} y={24}>数量（件）</text>
        <text className="age-chart-axis-title" x={plotRight + 12} y={24} textAnchor="end">金额（元）</text>
        {ticks.map((tick) => {
          const y = plotBottom - plotHeight * tick / 4;
          return <g key={tick}>
            <line className="age-chart-grid-line" x1={plotLeft} x2={plotRight} y1={y} y2={y} />
            <text className="age-chart-axis-label" x={plotLeft - 12} y={y + 4} textAnchor="end">{chartNumber(quantityMaximum * tick / 4)}</text>
            <text className="age-chart-axis-label" x={plotRight + 12} y={y + 4}>{chartAmount(valueMaximum * tick / 4)}</text>
          </g>;
        })}
        {buckets.map((bucket, index) => {
          const center = xFor(index);
          const top = quantityY(bucket.quantity);
          const barHeight = Math.max(0, plotBottom - top);
          const labelInside = barHeight >= 28;
          return <g key={bucket.key}>
            <title>{`${bucket.label}：库存数量 ${chartNumber(bucket.quantity)} 件；库存金额 ${formatCurrencyFromCents(bucket.valueCents)}；${chartNumber(bucket.count)} 个 SKU × 仓库`}</title>
            <rect className="age-chart-bar" x={center - barWidth / 2} y={top} width={barWidth} height={barHeight} rx="2" />
            {bucket.quantity > 0 && <text className={labelInside ? "age-chart-bar-label inside" : "age-chart-bar-label"} x={center} y={labelInside ? top + 20 : Math.max(plotTop + 11, top - 7)} textAnchor="middle">{chartNumber(bucket.quantity)}</text>}
            <text className="age-chart-x-label" x={center} y={plotBottom + 24} textAnchor="middle">{bucket.label.replace(" 天", "天")}</text>
          </g>;
        })}
        {buckets.length > 0 && <polyline className="age-chart-value-line" points={linePoints} />}
        {buckets.map((bucket, index) => {
          const x = xFor(index);
          const y = valueY(bucket.valueCents);
          const labelY = y > plotBottom - 28 ? plotBottom - 34 : Math.max(plotTop + 12, y - 10);
          return <g key={`value-${bucket.key}`}>
            <circle className="age-chart-value-point" cx={x} cy={y} r="4" />
            <text className="age-chart-value-label" x={x} y={labelY} textAnchor="middle">{chartAmount(bucket.valueCents / 100)}</text>
          </g>;
        })}
      </svg>
    </div>
    {!stockValueComplete && <p className="age-chart-coverage-note">库存金额仅统计已有可信固定成本的库存；缺失成本未按 0 元处理。</p>}
  </div>;
}

const inboundRiskMeta: Record<InventoryInboundMonitorResponse["items"][number]["risk"], { label: string; tone: string; dot: string }> = {
  stale: { label: "滞销/长库龄", tone: "danger", dot: "red" },
  normal: { label: "正常", tone: "success", dot: "green" },
  unknown: { label: "数据不足", tone: "gray", dot: "gray" },
  no_stock: { label: "无可用库存", tone: "gray", dot: "gray" },
};

export default function InventoryView({ customStartDate, customEndDate, currentUser, moduleView, onModuleViewChange, onAskAi }: { customStartDate: string; customEndDate: string; currentUser: CurrentUser | null; moduleView: InventoryTab; onModuleViewChange: (view: InventoryTab) => void; onAskAi: (prompt: string) => void }) {
  const syncInputRef = useRef<HTMLInputElement>(null);
  const planImportInputRef = useRef<HTMLInputElement>(null);
  const activeTab = moduleView;
  const usesInventoryOverview = activeTab === "overview" || activeTab === "plan";
  const usesInventoryAgeAnalysis = activeTab === "age" || activeTab === "stale";
  const usesInboundMonitor = activeTab === "inbound";
  const activeInventoryTabRef = useRef(activeTab);
  const [overviewResponse, setOverviewResponse] = useState<InventoryOverviewResponse | null>(null);
  const overview = usesInventoryOverview && overviewResponse?.projection === activeTab ? overviewResponse : null;
  const [ageAnalysis, setAgeAnalysis] = useState<InventoryAgeAnalysisResponse | null>(null);
  const [ageLoading, setAgeLoading] = useState(false);
  const [ageError, setAgeError] = useState("");
  const [ageRetryKey, setAgeRetryKey] = useState(0);
  const [inboundMonitor, setInboundMonitor] = useState<InventoryInboundMonitorResponse | null>(null);
  const [inboundLoading, setInboundLoading] = useState(false);
  const [inboundError, setInboundError] = useState("");
  const [inboundRetryKey, setInboundRetryKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncProgress, setSyncProgress] = useState(0);
  const [syncStage, setSyncStage] = useState("");
  const [snapshotDate, setSnapshotDate] = useState(shanghaiIsoToday);
  const [syncFeedback, setSyncFeedback] = useState<{ tone: "success" | "warning" | "error"; title: string; message: string } | null>(null);
  const [filters, setFilters] = useState<InventorySharedFilters>(readInventorySharedFilters);
  const [planActionId, setPlanActionId] = useState("");
  const [planQuantities, setPlanQuantities] = useState<Record<string, number>>({});
  const [planDraft, setPlanDraft] = useState<ReplenishmentPlanDraft | null>(null);
  const [planSaving, setPlanSaving] = useState(false);
  const [selectedPlanIds, setSelectedPlanIds] = useState<Set<string>>(() => new Set());
  const [groupPreview, setGroupPreview] = useState<DingTalkGroupPreview | null>(null);
  const [groupMessageLoading, setGroupMessageLoading] = useState(false);
  const [planImporting, setPlanImporting] = useState(false);
  const [batchDingTalkLoading, setBatchDingTalkLoading] = useState(false);
  const [batchDraftConfirmLoading, setBatchDraftConfirmLoading] = useState(false);
  const [overviewPage, setOverviewPage] = useState(1);
  const [agePage, setAgePage] = useState(1);
  const [planPage, setPlanPage] = useState(1);
  const [inboundPage, setInboundPage] = useState(1);
  const [workItemDraft, setWorkItemDraft] = useState<InventoryWorkItemDraft | null>(null);
  const [workItemSaving, setWorkItemSaving] = useState(false);
  const overviewGenerationRef = useRef(0);
  const overviewControllerRef = useRef<AbortController | null>(null);
  const ageGenerationRef = useRef(0);
  const ageControllerRef = useRef<AbortController | null>(null);
  const inboundGenerationRef = useRef(0);
  const inboundControllerRef = useRef<AbortController | null>(null);
  const debouncedInventoryQuery = useDebouncedValue(filters.productQuery);
  const aiInventoryAgeStatuses = activeTab === "stale"
    ? (filters.ageStatuses.filter(value => ["stagnant", "slow", "aged"].includes(value)).length > 0
      ? filters.ageStatuses.filter(value => ["stagnant", "slow", "aged"].includes(value))
      : ["stagnant", "slow", "aged"])
    : filters.ageStatuses;
  const [ageCard, setAgeCard] = useState<"" | "stagnant" | "aged90" | "zero_sales">("");
  const [inboundCard, setInboundCard] = useState<"" | "stale">("");
  useAiPageDetails("inventory", {
    period: null,
    blockedReason: ((activeTab === "age" || activeTab === "stale") && ageCard) || (activeTab === "inbound" && inboundCard) ? "当前统计卡片的精确条件尚未接入 AI 页面取数；请取消卡片筛选后提问，或移除页面上下文。" : undefined,
    filters: {
      query: debouncedInventoryQuery.trim(), warehouses: filters.warehouses, brands: filters.brands, categories: filters.categories,
      ...(activeTab === "overview" ? { warehouseTypes: filters.warehouseTypes, healthStatuses: filters.healthStatuses } : {}),
      ...(activeTab === "plan" ? { status: filters.planStatus, selectedIds: [...selectedPlanIds] } : {}),
      ...(activeTab === "age" || activeTab === "stale" ? { status: aiInventoryAgeStatuses, ageBuckets: filters.ageBuckets } : {}),
      ...(activeTab === "inbound" ? { suppliers: filters.suppliers } : {}),
    },
  }, activeTab !== "guangdong");

  const overviewPageScopeKey = useMemo(() => JSON.stringify({
    query: debouncedInventoryQuery.trim(),
    warehouses: [...filters.warehouses].sort(),
    brands: [...filters.brands].sort(),
    categories: [...filters.categories].sort(),
    statuses: [...filters.healthStatuses].sort(),
    types: [...filters.warehouseTypes].sort(),
  }), [debouncedInventoryQuery, filters.brands, filters.categories, filters.healthStatuses, filters.warehouses, filters.warehouseTypes]);
  const planPageScopeKey = JSON.stringify({
    query: debouncedInventoryQuery.trim(),
    warehouses: [...filters.warehouses].sort(),
    brands: [...filters.brands].sort(),
    categories: [...filters.categories].sort(),
    status: filters.planStatus,
  });
  const agePageScopeKey = usesInventoryAgeAnalysis ? JSON.stringify({
    cardFilter: ageCard,
    tab: activeTab,
    query: debouncedInventoryQuery.trim(),
    warehouses: [...filters.warehouses].sort(),
    brands: [...filters.brands].sort(),
    categories: [...filters.categories].sort(),
    statuses: [...filters.ageStatuses].sort(),
    ageBuckets: [...filters.ageBuckets].sort(),
  }) : "inactive";
  const inboundPageScopeKey = usesInboundMonitor ? JSON.stringify({
    cardFilter: inboundCard,
    query: debouncedInventoryQuery.trim(),
    warehouses: [...filters.warehouses].sort(),
    brands: [...filters.brands].sort(),
    categories: [...filters.categories].sort(),
    suppliers: [...filters.suppliers].sort(),
  }) : "inactive";
  const [committedOverviewPageScopeKey, setCommittedOverviewPageScopeKey] = useState(overviewPageScopeKey);
  const [committedPlanPageScopeKey, setCommittedPlanPageScopeKey] = useState(planPageScopeKey);
  const [committedAgePageScopeKey, setCommittedAgePageScopeKey] = useState(agePageScopeKey);
  const [committedInboundPageScopeKey, setCommittedInboundPageScopeKey] = useState(inboundPageScopeKey);
  const effectiveOverviewPage = effectivePageForScope(overviewPage, overviewPageScopeKey, committedOverviewPageScopeKey);
  const effectivePlanPage = effectivePageForScope(planPage, planPageScopeKey, committedPlanPageScopeKey);
  const effectiveAgePage = effectivePageForScope(agePage, agePageScopeKey, committedAgePageScopeKey);
  const effectiveInboundPage = effectivePageForScope(inboundPage, inboundPageScopeKey, committedInboundPageScopeKey);
  const canSyncInventory = currentUser?.role === "admin";
  const canManageInventory = currentUser?.role === "admin" || currentUser?.role === "operator";
  const selectablePlans = useMemo(
    () => canManageInventory
      ? (overview?.plans ?? []).filter((plan) => plan.status === "draft" || plan.status === "confirmed")
      : [],
    [canManageInventory, overview?.plans],
  );
  const selectablePlanIds = useMemo(() => new Set(selectablePlans.map((plan) => plan.id)), [selectablePlans]);
  const selectedPlans = useMemo(
    () => selectablePlans.filter((plan) => selectedPlanIds.has(plan.id)),
    [selectablePlans, selectedPlanIds],
  );
  const selectedDraftPlans = useMemo(
    () => selectedPlans.filter((plan) => plan.status === "draft"),
    [selectedPlans],
  );
  const selectedConfirmedPlans = useMemo(
    () => selectedPlans.filter((plan) => plan.status === "confirmed"),
    [selectedPlans],
  );
  const groupSelectionReady = selectedConfirmedPlans.length > 0
    && selectedConfirmedPlans.length === selectedPlanIds.size
    && selectedConfirmedPlans.every((plan) => plan.buyer.trim() && plan.supplier.trim());
  const allSelectablePlansSelected = selectablePlans.length > 0
    && selectablePlans.every((plan) => selectedPlanIds.has(plan.id));

  useEffect(() => {
    setSelectedPlanIds((current) => {
      const next = new Set([...current].filter((id) => selectablePlanIds.has(id)));
      return next.size === current.size && [...next].every((id) => current.has(id)) ? current : next;
    });
    setGroupPreview(null);
  }, [planPageScopeKey, selectablePlanIds]);

  const updateFilters = useCallback((next: InventorySharedFilters) => {
    setFilters(next);
    writeInventorySharedFilters(next);
  }, []);

  const toggleAgeBucket = useCallback((key: string) => {
    updateFilters({
      ...filters,
      ageBuckets: filters.ageBuckets.includes(key)
        ? filters.ageBuckets.filter((bucket) => bucket !== key)
        : [...filters.ageBuckets, key],
    });
  }, [filters, updateFilters]);

  useEffect(() => {
    const onPopState = () => setFilters(readInventorySharedFilters());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    activeInventoryTabRef.current = activeTab;
  }, [activeTab]);

  const loadOverview = useCallback(async () => {
    const generation = overviewGenerationRef.current + 1;
    overviewGenerationRef.current = generation;
    overviewControllerRef.current?.abort();
    const controller = new AbortController();
    overviewControllerRef.current = controller;
    setLoading(true);
    setError("");
    try {
      const projection = activeTab === "plan" ? "plan" : "overview";
      const params = new URLSearchParams({ view: projection });
      if (debouncedInventoryQuery.trim()) params.set("q", debouncedInventoryQuery.trim());
      filters.warehouses.forEach((value) => params.append("warehouse", value));
      filters.brands.forEach((value) => params.append("brand", value));
      filters.categories.forEach((value) => params.append("category", value));
      if (projection === "plan") {
        params.set("planPage", String(effectivePlanPage));
        params.set("planPageSize", "50");
        if (filters.planStatus) params.set("planStatus", filters.planStatus);
      } else {
        params.set("page", String(effectiveOverviewPage));
        params.set("pageSize", "50");
        filters.warehouseTypes.forEach((value) => params.append("warehouseType", value));
        filters.healthStatuses.forEach((value) => params.append("status", value));
      }
      const response = await fetch(`/api/inventory/overview?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (InventoryOverviewResponse & { error?: string; message?: string }) | null;
      if (!response.ok) throw new Error(payload?.error || payload?.message || `库存数据读取失败（${response.status}）`);
      if (!payload || payload.projection !== projection || !payload.metrics || !payload.sync) throw new Error("库存响应格式不完整");
      if (projection === "overview" && !Array.isArray(payload.items)) throw new Error("库存总览响应格式不完整");
      if (projection === "plan" && (!Array.isArray(payload.plans) || !payload.planSummary)) throw new Error("备货计划响应格式不完整");
      if (!controller.signal.aborted && generation === overviewGenerationRef.current) setOverviewResponse(payload);
    } catch (requestError) {
      if (!controller.signal.aborted && generation === overviewGenerationRef.current) setError(requestError instanceof Error ? requestError.message : "暂时无法读取库存数据");
    } finally {
      if (!controller.signal.aborted && generation === overviewGenerationRef.current) {
        setLoading(false);
        if (overviewControllerRef.current === controller) overviewControllerRef.current = null;
      }
    }
  }, [activeTab, debouncedInventoryQuery, effectiveOverviewPage, effectivePlanPage, filters.brands, filters.categories, filters.healthStatuses, filters.planStatus, filters.warehouses, filters.warehouseTypes]);

  useEffect(() => {
    if (!usesInventoryOverview) return;
    const timer = window.setTimeout(() => void loadOverview(), 0);
    return () => {
      window.clearTimeout(timer);
      overviewGenerationRef.current += 1;
      overviewControllerRef.current?.abort();
    };
  }, [loadOverview, retryKey, usesInventoryOverview]);

  const loadAgeAnalysis = useCallback(async (tab: "age" | "stale") => {
    const generation = ageGenerationRef.current + 1;
    ageGenerationRef.current = generation;
    ageControllerRef.current?.abort();
    const controller = new AbortController();
    ageControllerRef.current = controller;
    setAgeLoading(true);
    setAgeError("");
    try {
      const params = new URLSearchParams({ page: String(effectiveAgePage), pageSize: "50" });
      if (debouncedInventoryQuery.trim()) params.set("q", debouncedInventoryQuery.trim());
      filters.warehouses.forEach((warehouse) => params.append("warehouse", warehouse));
      filters.brands.forEach((brand) => params.append("brand", brand));
      filters.categories.forEach((category) => params.append("category", category));
      if (ageCard) params.set("cardFilter", ageCard);
      filters.ageBuckets.forEach((bucket) => params.append("ageBucket", bucket));
      const cleanupStatuses = ["stagnant", "slow", "aged"];
      const selectedCleanupStatuses = filters.ageStatuses.filter((status) => cleanupStatuses.includes(status));
      const selectedAgeStatuses = tab === "stale"
        ? (selectedCleanupStatuses.length > 0 ? selectedCleanupStatuses : cleanupStatuses)
        : filters.ageStatuses;
      selectedAgeStatuses.forEach((status) => params.append("status", status));
      const response = await fetch(`/api/inventory/age-analysis?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (InventoryAgeAnalysisResponse & { error?: string }) | null;
      if (!response.ok || !payload || !Array.isArray(payload.items) || !payload.metrics) {
        throw new Error(payload?.error || `库龄数据读取失败（${response.status}）`);
      }
      if (!controller.signal.aborted && generation === ageGenerationRef.current) setAgeAnalysis(payload);
    } catch (requestError) {
      if (!controller.signal.aborted && generation === ageGenerationRef.current) setAgeError(requestError instanceof Error ? requestError.message : "暂时无法读取库龄分析数据");
    } finally {
      if (!controller.signal.aborted && generation === ageGenerationRef.current) {
        setAgeLoading(false);
        if (ageControllerRef.current === controller) ageControllerRef.current = null;
      }
    }
  }, [ageCard, debouncedInventoryQuery, effectiveAgePage, filters.ageBuckets, filters.ageStatuses, filters.brands, filters.categories, filters.warehouses]);

  useEffect(() => {
    if (!usesInventoryAgeAnalysis) return;
    const timer = window.setTimeout(() => void loadAgeAnalysis(activeTab === "stale" ? "stale" : "age"), 0);
    return () => {
      window.clearTimeout(timer);
      ageGenerationRef.current += 1;
      ageControllerRef.current?.abort();
    };
  }, [activeTab, ageRetryKey, loadAgeAnalysis, usesInventoryAgeAnalysis]);

  const loadInboundMonitor = useCallback(async () => {
    const generation = inboundGenerationRef.current + 1;
    inboundGenerationRef.current = generation;
    inboundControllerRef.current?.abort();
    const controller = new AbortController();
    inboundControllerRef.current = controller;
    setInboundLoading(true);
    setInboundError("");
    try {
      const params = new URLSearchParams({ page: String(effectiveInboundPage), pageSize: "50" });
      if (debouncedInventoryQuery.trim()) params.set("q", debouncedInventoryQuery.trim());
      filters.warehouses.forEach((warehouse) => params.append("warehouse", warehouse));
      filters.brands.forEach((brand) => params.append("brand", brand));
      filters.categories.forEach((category) => params.append("category", category));
      if (inboundCard) params.set("cardFilter", inboundCard);
      filters.suppliers.forEach((supplier) => params.append("supplier", supplier));
      const response = await fetch(`/api/inventory/inbound-monitor?${params}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as (InventoryInboundMonitorResponse & { error?: string }) | null;
      if (!response.ok || !payload || !Array.isArray(payload.items) || !Array.isArray(payload.regions) || !payload.metrics) {
        throw new Error(payload?.error || `京东入仓监控读取失败（${response.status}）`);
      }
      if (!controller.signal.aborted && generation === inboundGenerationRef.current) setInboundMonitor(payload);
    } catch (requestError) {
      if (!controller.signal.aborted && generation === inboundGenerationRef.current) setInboundError(requestError instanceof Error ? requestError.message : "暂时无法读取京东入仓监控");
    } finally {
      if (!controller.signal.aborted && generation === inboundGenerationRef.current) {
        setInboundLoading(false);
        if (inboundControllerRef.current === controller) inboundControllerRef.current = null;
      }
    }
  }, [inboundCard, debouncedInventoryQuery, effectiveInboundPage, filters.brands, filters.categories, filters.suppliers, filters.warehouses]);

  useEffect(() => {
    if (!usesInboundMonitor) return;
    const timer = window.setTimeout(() => void loadInboundMonitor(), 0);
    return () => {
      window.clearTimeout(timer);
      inboundGenerationRef.current += 1;
      inboundControllerRef.current?.abort();
    };
  }, [inboundRetryKey, loadInboundMonitor, usesInboundMonitor]);

  useEffect(() => {
    setCommittedOverviewPageScopeKey(overviewPageScopeKey);
    setOverviewPage((current) => current === 1 ? current : 1);
  }, [overviewPageScopeKey]);
  useEffect(() => {
    setCommittedAgePageScopeKey(agePageScopeKey);
    setAgePage((current) => current === 1 ? current : 1);
  }, [agePageScopeKey]);
  useEffect(() => {
    setCommittedPlanPageScopeKey(planPageScopeKey);
    setPlanPage((current) => current === 1 ? current : 1);
  }, [planPageScopeKey]);
  useEffect(() => {
    setCommittedInboundPageScopeKey(inboundPageScopeKey);
    setInboundPage((current) => current === 1 ? current : 1);
  }, [inboundPageScopeKey]);

  const recommendations = useMemo(
    () => overview?.recommendations ?? [],
    [overview?.recommendations],
  );

  const refreshActiveInventoryTab = useCallback(async () => {
    const tab = activeInventoryTabRef.current;
    if (tab === "age" || tab === "stale") await loadAgeAnalysis(tab);
    else if (tab === "inbound") await loadInboundMonitor();
    else await loadOverview();
  }, [loadAgeAnalysis, loadInboundMonitor, loadOverview]);

  const syncInventory = useCallback(async (file?: File) => {
    if (!file || !canSyncInventory) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setSyncFeedback({ tone: "error", title: "文件格式不支持", message: "请选择吉客云导出的 .xlsx 分仓库存查询报表。" });
      if (syncInputRef.current) syncInputRef.current.value = "";
      return;
    }
    if (file.size > MAX_INVENTORY_FILE_SIZE) {
      setSyncFeedback({ tone: "error", title: "文件超过 20MB", message: `当前文件为 ${formatFileSize(file.size)}，请拆分后再同步。` });
      if (syncInputRef.current) syncInputRef.current.value = "";
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(snapshotDate)) {
      setSyncFeedback({ tone: "error", title: "请填写快照日期", message: "库龄报表未包含日期时，需要填写 YYYY-MM-DD 格式的库存快照日期。" });
      return;
    }
    setSyncing(true);
    setSyncProgress(0);
    setSyncStage(file.size > DIRECT_INVENTORY_FILE_SIZE ? "正在分片上传库存报表…" : "正在上传库存报表…");
    setSyncFeedback(null);
    try {
      let payload: InventoryImportResponse | null;
      let responseStatus = 0;
      if (file.size <= DIRECT_INVENTORY_FILE_SIZE) {
        const formData = new FormData();
        formData.append("file", file, file.name);
        formData.append("snapshotDate", snapshotDate);
        const response = await fetch("/api/imports/inventory", { method: "POST", body: formData });
        responseStatus = response.status;
        payload = await response.json().catch(() => null) as InventoryImportResponse | null;
      } else {
        const chunkCount = Math.ceil(file.size / INVENTORY_UPLOAD_CHUNK_SIZE);
        const fingerprint = `inventory:${file.name}:${file.size}:${file.lastModified}:${INVENTORY_UPLOAD_CHUNK_SIZE}`;
        const initResponse = await fetch("/api/imports/inventory/chunks", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: "init", fileName: file.name, fileSizeBytes: file.size, chunkCount, fingerprint }),
        });
        const initPayload = await initResponse.json().catch(() => null) as InventoryChunkUploadResponse | null;
        if (!initResponse.ok || !initPayload?.ok || !initPayload.upload) throw new Error(initPayload?.message || "无法初始化库存分片上传");
        const uploadId = initPayload.upload.id;
        const received = new Set(initPayload.upload.receivedChunkIndexes ?? []);
        setSyncProgress(Math.round((initPayload.upload.receivedBytes / file.size) * 85));
        for (let index = 0; index < chunkCount; index += 1) {
          if (received.has(index)) continue;
          const start = index * INVENTORY_UPLOAD_CHUNK_SIZE;
          const end = Math.min(start + INVENTORY_UPLOAD_CHUNK_SIZE, file.size);
          const chunkResponse = await fetch("/api/imports/inventory/chunks", {
            method: "PUT",
            headers: {
              "content-type": "application/octet-stream",
              "x-upload-id": uploadId,
              "x-chunk-index": String(index),
            },
            body: file.slice(start, end),
          });
          const chunkPayload = await chunkResponse.json().catch(() => null) as InventoryChunkUploadResponse | null;
          if (!chunkResponse.ok || !chunkPayload?.ok) throw new Error(chunkPayload?.message || `第 ${index + 1} 个库存分片上传失败`);
          setSyncProgress(Math.min(85, Math.round((end / file.size) * 85)));
        }
        setSyncProgress(90);
        setSyncStage("分片已上传，正在合并并计算库存健康…");
        const completeResponse = await fetch("/api/imports/inventory/chunks", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: "complete", uploadId, snapshotDate }),
        });
        responseStatus = completeResponse.status;
        payload = await completeResponse.json().catch(() => null) as InventoryImportResponse | null;
      }
      if (responseStatus < 200 || responseStatus >= 300 || !payload?.ok) {
        const firstError = payload?.errors?.[0];
        const errorRow = firstError?.row ?? firstError?.sourceRowNumber;
        const errorDetail = firstError
          ? `${errorRow ? `第 ${errorRow} 行：` : ""}${firstError.message}${(payload?.errors?.length ?? 0) > 1 ? `（另有 ${(payload?.errors?.length ?? 1) - 1} 项）` : ""}`
          : payload?.message;
        throw new Error(errorDetail || `库存同步失败（${responseStatus}）`);
      }
      setSyncProgress(100);
      const warningText = payload.warnings?.length ? `；${payload.warnings[0].message}` : "";
      setSyncFeedback({
        tone: payload.status === "duplicate" ? "warning" : "success",
        title: payload.status === "duplicate" ? "库存快照已同步" : "库存数据同步成功",
        message: `${payload.batch?.snapshotDate ?? "最新"} · ${formatCount(payload.batch?.insertedCount ?? payload.batch?.rowCount ?? 0)} 行库存明细${warningText}`,
      });
      await refreshActiveInventoryTab();
    } catch (requestError) {
      setSyncFeedback({ tone: "error", title: "库存同步失败", message: requestError instanceof Error ? requestError.message : "请检查文件后重试" });
    } finally {
      setSyncing(false);
      setSyncStage("");
      if (syncInputRef.current) syncInputRef.current.value = "";
    }
  }, [canSyncInventory, refreshActiveInventoryTab, snapshotDate]);

  const planCandidates = useMemo(() => {
    const candidates = new Map<string, Parameters<typeof planDraftFromCandidate>[0]>();
    for (const item of overview?.mapping.samples ?? []) {
      candidates.set(item.productCode, {
        productCode: item.productCode,
        productName: item.productName,
        brand: item.brand,
        category: item.category,
        supplier: item.supplier,
        warehouseOptions: [...item.warehouseOptions],
        totalSalesQuantity: item.totalSalesQuantity,
      });
    }
    for (const item of overview?.items ?? []) {
      const current = candidates.get(item.productCode);
      const option: InventoryPlanWarehouseOption = {
        key: item.key,
        warehouse: item.warehouse,
        availableQuantity: item.availableQuantity,
        salesQuantity: item.sales30d,
        coverageDays: item.coverageDays,
        suggestedQuantity: item.suggestedQuantity,
        inDraftPlan: item.inDraftPlan,
      };
      if (current) {
        if (!current.warehouseOptions.some((candidate) => candidate.key === item.key)) current.warehouseOptions.push(option);
        continue;
      }
      candidates.set(item.productCode, {
        productCode: item.productCode,
        productName: item.productName,
        brand: item.brand,
        category: item.category,
        supplier: item.supplier,
        warehouseOptions: [option],
        totalSalesQuantity: item.productSales30d,
      });
    }
    return [...candidates.values()].sort((left, right) => left.productCode.localeCompare(right.productCode, "zh-CN"));
  }, [overview]);

  const openPlanModal = useCallback((productCode: string, preferredKey?: string) => {
    if (!canManageInventory) return;
    const candidate = planCandidates.find((item) => item.productCode === productCode);
    if (!candidate) return;
    setPlanDraft(planDraftFromCandidate(candidate, preferredKey));
    setSyncFeedback(null);
  }, [canManageInventory, planCandidates]);

  const submitPlan = useCallback(async () => {
    if (!planDraft || planSaving || !canManageInventory) return;
    if (!planDraft.key || planDraft.plannedQuantity < 1) {
      setSyncFeedback({ tone: "error", title: "备货计划未保存", message: "请选择入库库房，并填写大于 0 的备货数量。" });
      return;
    }
    if (overview?.sync.inventoryStale) {
      const confirmed = window.confirm(`库存快照日期为 ${overview.sync.inventoryAsOf ?? "未知"}，已超过 3 天。建议先同步最新库存；是否仍按当前快照保存备货计划？`);
      if (!confirmed) return;
    }
    setPlanSaving(true);
    setPlanActionId(planDraft.key);
    setSyncFeedback(null);
    try {
      const response = await fetch("/api/inventory/replenishment", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          key: planDraft.key,
          plannedQuantity: planDraft.plannedQuantity,
          manual: true,
          acknowledgeStale: Boolean(overview?.sync.inventoryStale),
          buyer: planDraft.buyer,
          operatorName: planDraft.operatorName,
          department: planDraft.department,
          planType: planDraft.planType,
          orderDate: planDraft.orderDate,
          expectedArrivalDate: planDraft.expectedArrivalDate,
          expectedConsumptionDays: planDraft.expectedConsumptionDays,
          status: planDraft.status,
          requiresInspection: planDraft.requiresInspection,
          notes: planDraft.notes,
        }),
      });
      const payload = await response.json().catch(() => null) as { ok?: boolean; item?: ReplenishmentPlanItem; message?: string } | null;
      if (!response.ok || !payload?.ok || !payload.item?.id) throw new Error(payload?.message || "创建备货计划失败");
      if (planDraft.status === "confirmed") {
        try {
          await requestPlanDingTalkSync(payload.item.id);
          setSyncFeedback({
            tone: "success",
            title: "备货计划已确认并提交钉钉多维表",
            message: `${planDraft.productName} · ${planDraft.warehouse} · ${formatCount(planDraft.plannedQuantity)} 件`,
          });
        } catch (syncError) {
          setSyncFeedback({
            tone: "warning",
            title: "备货计划已确认，钉钉提交失败",
            message: `${syncError instanceof Error ? syncError.message : "请稍后重试"}；可在“备货计划”中重试提交。`,
          });
        }
      } else {
        setSyncFeedback({
          tone: "success",
          title: "备货草稿已保存",
          message: `${planDraft.productName} · ${planDraft.warehouse} · ${formatCount(planDraft.plannedQuantity)} 件`,
        });
      }
      setPlanDraft(null);
      await refreshActiveInventoryTab();
    } catch (requestError) {
      setSyncFeedback({ tone: "error", title: "备货计划创建失败", message: requestError instanceof Error ? requestError.message : "请稍后重试" });
    } finally {
      setPlanSaving(false);
      setPlanActionId("");
    }
  }, [canManageInventory, overview, planDraft, planSaving, refreshActiveInventoryTab]);

  const updatePlanStatus = useCallback(async (plan: ReplenishmentPlanItem, status: ReplenishmentPlanItem["status"]) => {
    if (!canManageInventory) return;
    const confirmationText = status === "confirmed"
      ? `确认 ${plan.productName} 的备货计划、计入执行在途，并同时提交到钉钉多维表？`
      : status === "completed"
        ? `确认 ${plan.productName} 的备货已完成？完成数量会持续扣减建议，直到下一次库存同步。`
        : status === "cancelled"
          ? `确认取消 ${plan.productName} 的备货计划？`
          : "";
    if (confirmationText && !window.confirm(confirmationText)) return;
    setPlanActionId(plan.id);
    try {
      const updatedPlan = await requestPlanStatusUpdate(
        plan.id,
        status,
        plan.status === "draft" ? planQuantities[plan.id] ?? plan.plannedQuantity : undefined,
      );
      if (status === "confirmed") {
        try {
          await requestPlanDingTalkSync(updatedPlan.id);
          setSyncFeedback({
            tone: "success",
            title: "备货计划已确认并提交钉钉多维表",
            message: `${plan.productName} · ${formatCount(planQuantities[plan.id] ?? plan.plannedQuantity)} 件`,
          });
        } catch (syncError) {
          setSyncFeedback({
            tone: "warning",
            title: "备货计划已确认，钉钉提交失败",
            message: `${syncError instanceof Error ? syncError.message : "请稍后重试"}；本地计划已保留，可重试提交。`,
          });
        }
      } else {
        setSyncFeedback({
          tone: "success",
          title: status === "completed" ? "备货计划已完成" : "备货草稿已取消",
          message: `${plan.productName} · ${formatCount(planQuantities[plan.id] ?? plan.plannedQuantity)} 件`,
        });
      }
      await refreshActiveInventoryTab();
    } catch (requestError) {
      setSyncFeedback({ tone: "error", title: "备货计划更新失败", message: requestError instanceof Error ? requestError.message : "请稍后重试" });
    } finally {
      setPlanActionId("");
    }
  }, [canManageInventory, planQuantities, refreshActiveInventoryTab]);

  const syncPlanToDingTalk = useCallback(async (plan: ReplenishmentPlanItem) => {
    if (!canManageInventory || plan.status !== "confirmed") return;
    setPlanActionId(plan.id);
    try {
      const outcome = await requestPlanDingTalkSync(plan.id);
      setSyncFeedback({
        tone: "success",
        title: outcome === "updated" ? "钉钉备货计划已更新" : outcome === "already_synced" ? "钉钉备货计划已是最新" : "钉钉备货计划已创建",
        message: `${plan.productName} · ${plan.warehouse} · ${formatCount(plan.plannedQuantity)} 件`,
      });
    } catch (requestError) {
      setSyncFeedback({ tone: "error", title: "本地计划已确认，钉钉同步失败", message: requestError instanceof Error ? requestError.message : "请稍后重试原备货计划" });
    } finally {
      // Refresh once on either outcome so the persisted failure stays visible.
      await refreshActiveInventoryTab().catch(() => undefined);
      setPlanActionId("");
    }
  }, [canManageInventory, refreshActiveInventoryTab]);

  const importReplenishmentPlans = useCallback(async (file?: File) => {
    if (!file || !canManageInventory || planImporting || batchDraftConfirmLoading || batchDingTalkLoading || groupMessageLoading) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setSyncFeedback({ tone: "error", title: "文件格式不支持", message: "请使用下载的 .xlsx 备货计划导入模板。" });
      if (planImportInputRef.current) planImportInputRef.current.value = "";
      return;
    }
    if (file.size < 1 || file.size > 4 * 1024 * 1024) {
      setSyncFeedback({ tone: "error", title: "文件大小无效", message: "备货计划工作簿必须小于4MiB。" });
      if (planImportInputRef.current) planImportInputRef.current.value = "";
      return;
    }
    const acknowledgeStale = Boolean(overview?.sync.inventoryStale);
    if (acknowledgeStale && !window.confirm(`库存快照日期为 ${overview?.sync.inventoryAsOf ?? "未知"}，已超过3天。是否仍按当前快照批量创建备货计划？`)) {
      if (planImportInputRef.current) planImportInputRef.current.value = "";
      return;
    }
    setPlanImporting(true);
    setSyncFeedback(null);
    try {
      const response = await fetch(`/api/inventory/replenishment/import?acknowledgeStale=${acknowledgeStale ? "true" : "false"}`, {
        method: "POST",
        headers: { "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
        body: file,
      });
      const payload = await response.json().catch(() => null) as ReplenishmentImportResponse | null;
      if (!response.ok || !payload?.ok) throw new Error(payload?.message || "备货计划导入失败");
      await refreshActiveInventoryTab();
      const confirmedIds = Array.isArray(payload.confirmedPlanIds) ? payload.confirmedPlanIds : [];
      setSelectedPlanIds(new Set(confirmedIds));
      setGroupPreview(null);
      setSyncFeedback({
        tone: "success",
        title: `已导入 ${payload.importedCount ?? 0} 条备货计划`,
        message: `${payload.inventoryAsOf ?? "最新库存"} · 已确认 ${payload.confirmedCount ?? 0} 条、草稿 ${payload.draftCount ?? 0} 条${confirmedIds.length ? "；已自动勾选本页可见的已确认计划，可一键提交钉钉表" : ""}`,
      });
    } catch (requestError) {
      setSyncFeedback({ tone: "error", title: "备货计划导入失败", message: requestError instanceof Error ? requestError.message : "请检查模板后重试" });
    } finally {
      setPlanImporting(false);
      if (planImportInputRef.current) planImportInputRef.current.value = "";
    }
  }, [batchDingTalkLoading, batchDraftConfirmLoading, canManageInventory, groupMessageLoading, overview?.sync.inventoryAsOf, overview?.sync.inventoryStale, planImporting, refreshActiveInventoryTab]);

  const batchConfirmDraftPlans = useCallback(async () => {
    if (!canManageInventory || batchDraftConfirmLoading || batchDingTalkLoading || groupMessageLoading || planImporting || selectedDraftPlans.length === 0) return;
    const plans = selectedDraftPlans.slice(0, 50).map((plan) => ({
      id: plan.id,
      plannedQuantity: planQuantities[plan.id] ?? plan.plannedQuantity,
    }));
    const preservedConfirmedIds = selectedConfirmedPlans.map((plan) => plan.id);
    if (!window.confirm(`确认将所选 ${plans.length} 条草稿计入执行在途，并逐条提交到钉钉多维表？`)) return;
    setBatchDraftConfirmLoading(true);
    setGroupPreview(null);
    setSyncFeedback(null);
    try {
      const results = await confirmAndSyncDraftPlans(
        plans,
        async (plan) => { await requestPlanStatusUpdate(plan.id, "confirmed", plan.plannedQuantity); },
        async (plan) => { await requestPlanDingTalkSync(plan.id); },
        (message) => /授权已失效|访问权限不足|运行组件不可用/.test(message),
      );
      const failed = results.filter((result) => !result.ok);
      const succeeded = results.length - failed.length;
      const confirmedButUnsynced = failed.filter((result) => result.confirmed).length;
      setSelectedPlanIds(new Set(retainFailedPlanSelections(preservedConfirmedIds, results)));
      setSyncFeedback({
        tone: failed.length ? "warning" : "success",
        title: failed.length
          ? `已确认并提交 ${succeeded} 条，待处理 ${failed.length} 条`
          : `已一键确认并提交 ${succeeded} 条钉钉备货计划`,
        message: failed[0]?.error
          ? `${failed[0].error}${confirmedButUnsynced ? `；其中 ${confirmedButUnsynced} 条已确认但钉钉未完成` : ""}${failed.length > 1 ? "；未完成项已保留勾选" : ""}`
          : "所有所选草稿均已确认、幂等写入并回查钉钉备货管理表。",
      });
      await refreshActiveInventoryTab();
    } finally {
      setBatchDraftConfirmLoading(false);
    }
  }, [batchDingTalkLoading, batchDraftConfirmLoading, canManageInventory, groupMessageLoading, planImporting, planQuantities, refreshActiveInventoryTab, selectedConfirmedPlans, selectedDraftPlans]);

  const batchSyncPlansToDingTalk = useCallback(async () => {
    if (!canManageInventory || batchDraftConfirmLoading || batchDingTalkLoading || groupMessageLoading || planImporting || selectedConfirmedPlans.length === 0) return;
    const planIds = selectedConfirmedPlans.slice(0, 50).map((plan) => plan.id);
    const preservedDraftIds = selectedDraftPlans.map((plan) => plan.id);
    setBatchDingTalkLoading(true);
    setGroupPreview(null);
    setSyncFeedback(null);
    const results: Array<{ id: string; ok: boolean; error?: string }> = [];
    try {
      for (let index = 0; index < planIds.length; index += 1) {
        const id = planIds[index];
        try {
          await requestPlanDingTalkSync(id);
          results.push({ id, ok: true });
        } catch (error) {
          const message = error instanceof Error ? error.message : "提交失败";
          results.push({ id, ok: false, error: message });
          if (/授权已失效|访问权限不足|运行组件不可用/.test(message)) {
            for (const remainingId of planIds.slice(index + 1)) {
              results.push({ id: remainingId, ok: false, error: `批量提交已停止：${message}` });
            }
            break;
          }
        }
      }
      const failed = results.filter((result) => !result.ok);
      const succeeded = results.length - failed.length;
      setSelectedPlanIds(new Set(retainFailedPlanSelections(preservedDraftIds, results)));
      setSyncFeedback({
        tone: failed.length ? "warning" : "success",
        title: failed.length ? `钉钉表已提交 ${succeeded} 条，失败 ${failed.length} 条` : `已批量提交 ${succeeded} 条钉钉备货计划`,
        message: failed[0]?.error ? `${failed[0].error}${failed.length > 1 ? `（另有${failed.length - 1}条失败，已保留勾选）` : ""}` : "所有所选规格均已幂等写入并回查钉钉备货管理表。",
      });
      await refreshActiveInventoryTab();
    } finally {
      setBatchDingTalkLoading(false);
    }
  }, [batchDingTalkLoading, batchDraftConfirmLoading, canManageInventory, groupMessageLoading, planImporting, refreshActiveInventoryTab, selectedConfirmedPlans, selectedDraftPlans]);

  const togglePlanSelection = useCallback((planId: string) => {
    if (!selectablePlanIds.has(planId)) return;
    setSelectedPlanIds((current) => {
      const next = new Set(current);
      if (next.has(planId)) next.delete(planId);
      else next.add(planId);
      return next;
    });
    setGroupPreview(null);
  }, [selectablePlanIds]);

  const toggleAllPlanSelections = useCallback(() => {
    setSelectedPlanIds(allSelectablePlansSelected ? new Set() : new Set(selectablePlans.map((plan) => plan.id)));
    setGroupPreview(null);
  }, [allSelectablePlansSelected, selectablePlans]);

  const previewDingTalkGroupMessage = useCallback(async () => {
    if (!canManageInventory || !groupSelectionReady || groupMessageLoading) return;
    setGroupMessageLoading(true);
    try {
      const preview = await requestDingTalkGroupMessage("preview", [...selectedPlanIds]);
      setGroupPreview(preview);
    } catch (requestError) {
      setSyncFeedback({
        tone: "error",
        title: "备货群消息预览失败",
        message: requestError instanceof Error ? requestError.message : "请检查采购、工厂和钉钉设置后重试",
      });
    } finally {
      setGroupMessageLoading(false);
    }
  }, [canManageInventory, groupMessageLoading, groupSelectionReady, selectedPlanIds]);

  const sendDingTalkGroupMessage = useCallback(async () => {
    if (!canManageInventory || !groupPreview || groupMessageLoading) return;
    setGroupMessageLoading(true);
    try {
      const result = await requestDingTalkGroupMessage("send", groupPreview.planIds, groupPreview.previewToken);
      setSyncFeedback({
        tone: "success",
        title: result.status === "already_delivered" ? "该批备货消息已发送" : "备货消息已发送到钉钉群",
        message: `${result.targetGroupName} · ${result.planCount} 条计划 · 已提醒 ${result.buyerNames.join("、")}`,
      });
      setGroupPreview(null);
      setSelectedPlanIds(new Set());
      await refreshActiveInventoryTab();
    } catch (requestError) {
      setSyncFeedback({
        tone: "error",
        title: "备货群消息发送失败",
        message: requestError instanceof Error ? requestError.message : "发送结果不明确，请勿重复发送并联系管理员核对",
      });
    } finally {
      setGroupMessageLoading(false);
    }
  }, [canManageInventory, groupMessageLoading, groupPreview, refreshActiveInventoryTab]);

  const openProcurementWorkItem = useCallback((plan: ReplenishmentPlanItem) => {
    const expectedArrivalDate = addCalendarDays(shanghaiIsoToday(), 7);
    setWorkItemDraft({
      kind: "procurement",
      label: `${plan.productName} · ${plan.warehouse} · ${formatCount(plan.plannedQuantity)} 件`,
      planId: plan.id,
      owner: currentUser?.email ?? "",
      dueDate: expectedArrivalDate,
      expectedArrivalDate,
      planType: "daily",
      cleanupStrategy: "review",
      expectedConsumptionDays: 30,
      notes: "",
    });
  }, [currentUser?.email]);

  const openCleanupWorkItem = useCallback((item: InventoryAgeItem) => {
    setWorkItemDraft({
      kind: "stale_cleanup",
      label: `${item.productName} · ${item.warehouse}`,
      inventoryKey: item.key,
      owner: currentUser?.email ?? "",
      dueDate: addCalendarDays(shanghaiIsoToday(), 14),
      expectedArrivalDate: "",
      planType: "daily",
      cleanupStrategy: item.status === "stagnant" ? "promotion" : "review",
      expectedConsumptionDays: 30,
      notes: "",
    });
  }, [currentUser?.email]);

  const submitInventoryWorkItem = useCallback(async () => {
    if (!workItemDraft || workItemSaving || !canManageInventory) return;
    setWorkItemSaving(true);
    try {
      const response = await fetch("/api/inventory/work-items", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(workItemDraft),
      });
      const payload = await response.json().catch(() => null) as { ok?: boolean; created?: boolean; message?: string; task?: { title?: string } } | null;
      if (!response.ok || !payload?.ok) throw new Error(payload?.message || `创建执行事项失败（${response.status}）`);
      setSyncFeedback({
        tone: "success",
        title: payload.created ? "执行事项已创建" : "已有未完成执行事项",
        message: payload.task?.title || workItemDraft.label,
      });
      setWorkItemDraft(null);
    } catch (requestError) {
      setSyncFeedback({ tone: "error", title: "创建执行事项失败", message: requestError instanceof Error ? requestError.message : "请稍后重试" });
    } finally {
      setWorkItemSaving(false);
    }
  }, [canManageInventory, workItemDraft, workItemSaving]);

  const askInventoryAi = useCallback((intent: "analyze" | "explain" | "export") => {
    const viewLabel = activeTab === "overview" ? "库存总览" : activeTab === "age" ? "库龄分析" : activeTab === "plan" ? "备货计划" : activeTab === "stale" ? "滞销清理" : "京东入仓监控";
    const summarizeSelections = (values: string[]) => values.length > 0
      ? `${values.slice(0, 5).join("、")}${values.length > 5 ? `等 ${values.length} 项` : ""}`
      : "全部";
    const filterContext = `公共筛选=货品${filters.productQuery.trim() ? `“${filters.productQuery.trim()}”` : "全部"}；仓库=${summarizeSelections(filters.warehouses)}；品牌=${summarizeSelections(filters.brands)}；品类=${summarizeSelections(filters.categories)}`;
    const context = activeTab === "overview" && overview
      ? `库存快照=${overview.sync.inventoryAsOf ?? "未知"}；销售范围=${overview.sync.salesWindowStart ?? "未知"}至${overview.sync.salesThrough ?? "未知"}；SKU×仓库=${overview.metrics.skuWarehouseCount}；可用库存=${overview.metrics.totalAvailableQuantity}；销量匹配率=${formatRate(overview.metrics.salesDemandMatchRate)}；数据质量=${overview.quality.status}；质量问题=${overview.quality.issues.map((issue) => issue.message).join("、") || "无"}`
      : (activeTab === "age" || activeTab === "stale") && ageAnalysis
        ? `库存快照=${ageAnalysis.sync.inventoryAsOf ?? "未知"}；SKU×仓库=${ageAnalysis.metrics.skuWarehouseCount}；90天以上=${ageAnalysis.metrics.aged90Count}；滞销项=${ageAnalysis.metrics.stagnantCount}；缺库龄项=${ageAnalysis.coverage.unagedStockCount}`
        : activeTab === "inbound" && inboundMonitor
          ? `库存快照=${inboundMonitor.sync.inventoryAsOf ?? "未知"}；销售截至=${inboundMonitor.sync.salesThrough ?? "未知"}；京东入仓SKU×仓库=${inboundMonitor.metrics.itemCount}；可用库存=${inboundMonitor.metrics.availableQuantity}；30日计算周转=${inboundMonitor.metrics.turnoverDays ?? "未知"}天；销量匹配率=${formatRate(inboundMonitor.metrics.salesMatchRate)}；原生对账能力=${inboundMonitor.scope.nativeComparisonAvailable ? "已接入" : "未接入"}`
          : activeTab === "overview" || activeTab === "plan"
            ? "销量范围=最新销售截止日向前30个自然日"
            : `统计周期=${customStartDate}至${customEndDate}`;
    const request = intent === "analyze"
      ? "请分析本页最重要的库存风险、优先级和可执行动作，并明确数据缺口。"
      : intent === "explain"
        ? "请解释本页指标口径、彼此关系、适用边界，以及哪些数字不能直接用于决策。"
        : "请使用中央只读工具重新核验可获得的数据，并生成一个有界 CSV 表格产物；无法导出的字段请明确说明，不要估算。";
    onAskAi(`当前页面：库存管理 / ${viewLabel}\n页面上下文：${filterContext}；${context}\n要求：${request}\n必须遵守：刷刷仓排除；金额为人民币；销量口径需明确；低可信或缺少原生口径时不得输出精确执行结论。`);
  }, [activeTab, ageAnalysis, customEndDate, customStartDate, filters.brands, filters.categories, filters.productQuery, filters.warehouses, inboundMonitor, onAskAi, overview]);

  const sharedFilterOptions = useMemo<InventorySharedFilterOptions>(() => {
    const unique = (values: string[]) => [...new Set(values.filter(Boolean))].sort((left, right) => left.localeCompare(right, "zh-CN"));
    return {
      warehouses: unique([
        ...(overviewResponse?.filters.warehouses ?? []),
        ...(ageAnalysis?.filters.warehouses ?? []),
        ...(inboundMonitor?.filters.warehouses ?? []),
      ]),
      brands: unique([
        ...(overviewResponse?.filters.brands ?? []),
        ...(ageAnalysis?.filters.brands ?? []),
        ...(inboundMonitor?.filters.brands ?? []),
      ]),
      categories: unique([
        ...(overviewResponse?.filters.categories ?? []),
        ...(ageAnalysis?.filters.categories ?? []),
        ...(inboundMonitor?.filters.categories ?? []),
      ]),
      suppliers: unique(inboundMonitor?.filters.suppliers ?? []),
      ageBuckets: ageAnalysis?.filters.ageBuckets ?? [],
    };
  }, [ageAnalysis, inboundMonitor, overviewResponse]);
  const sharedFilterBar = <InventoryFilterBar
    activeTab={activeTab}
    filters={filters}
    options={sharedFilterOptions}
    updating={usesInventoryAgeAnalysis ? ageLoading : usesInboundMonitor ? inboundLoading : loading}
    extraFilterActive={usesInventoryAgeAnalysis ? Boolean(ageCard) : usesInboundMonitor && Boolean(inboundCard)}
    onResetExtra={() => { if (usesInventoryAgeAnalysis) setAgeCard(""); if (usesInboundMonitor) setInboundCard(""); }}
    onChange={updateFilters}
  />;

  const subnav = (
    <div className="subnav inventory-subnav" role="tablist" aria-label="库存管理子版块">
      <button type="button" role="tab" aria-selected={activeTab === "overview"} className={activeTab === "overview" ? "active" : ""} onClick={() => onModuleViewChange("overview")}>库存总览</button>
      <button type="button" role="tab" aria-selected={activeTab === "age"} className={activeTab === "age" ? "active" : ""} onClick={() => onModuleViewChange("age")}>库龄分析</button>
      <button type="button" role="tab" aria-selected={activeTab === "plan"} className={activeTab === "plan" ? "active" : ""} onClick={() => onModuleViewChange("plan")}>备货计划</button>
      <button type="button" role="tab" aria-selected={activeTab === "stale"} className={activeTab === "stale" ? "active" : ""} onClick={() => onModuleViewChange("stale")}>滞销清理</button>
      <button type="button" role="tab" aria-selected={activeTab === "inbound"} className={activeTab === "inbound" ? "active" : ""} onClick={() => onModuleViewChange("inbound")}>京东入仓监控</button>
      <button type="button" role="tab" aria-selected={activeTab === "guangdong"} className={activeTab === "guangdong" ? "active" : ""} onClick={() => onModuleViewChange("guangdong")}>广东入仓监控</button>
    </div>
  );

  if (activeTab === "guangdong") return <>{subnav}<GuangdongInventoryView canManage={currentUser?.role === "operator" || currentUser?.role === "admin"} filters={filters} onFiltersChange={updateFilters} onAskAi={onAskAi} /></>;

  const activeInventoryHasData = usesInventoryAgeAnalysis ? ageAnalysis?.hasInventory : usesInboundMonitor ? inboundMonitor?.hasInventory : overview?.hasInventory;
  const activeInventoryAsOf = usesInventoryAgeAnalysis ? ageAnalysis?.sync.inventoryAsOf : usesInboundMonitor ? inboundMonitor?.sync.inventoryAsOf : overview?.sync.inventoryAsOf;

  const syncBar = (
    <section className="inventory-sync-bar">
      <div className="inventory-sync-title">
        <span className={`sync-pulse ${usesInventoryOverview && overview?.sync.inventoryStale ? "stale" : activeInventoryHasData ? "ready" : ""}`} aria-hidden="true" />
        <div><strong>{usesInventoryAgeAnalysis
          ? activeInventoryHasData ? "库龄与动销数据已同步" : "等待首次库龄库存同步"
          : usesInboundMonitor ? activeInventoryHasData ? "京东入仓库存监控已就绪" : "等待包含京东 RDC / DC 的库存快照"
          : overview?.hasInventory ? overview.sync.salesThrough ? "库存与近30天销量已联动" : "库存已同步，近30天暂无销售数据" : "等待首次库存同步"}</strong><small>{usesInventoryAgeAnalysis
            ? activeInventoryHasData ? `库存快照 ${activeInventoryAsOf ?? "—"} · ${ageAnalysis?.sync.hasAgeSales ? "包含前 7 天与前 30 天销量" : "当前报表未提供销量列"}` : "上传包含库龄字段的分仓库存报表后生成库龄与滞销分析"
            : usesInboundMonitor ? activeInventoryHasData ? `京东入仓快照 ${activeInventoryAsOf ?? "—"} · 销售截至 ${inboundMonitor?.sync.salesThrough ?? "暂无"}` : "同步包含京东 RDC / DC 仓库的库存报表后生成监控"
            : overview?.hasInventory ? `近30天正向销量 ${overview.sync.salesWindowStart ?? "暂无"} 至 ${overview.sync.salesThrough ?? "暂无"} · 库存快照 ${overview.sync.inventoryAsOf}` : "上传分仓库存报表后，按最新销售截止日计算近30天库存健康与备货建议"}</small></div>
      </div>
      <div className="inventory-source-status" aria-label="库存数据源状态">
        {usesInventoryOverview && (overview?.sources ?? []).map((source) => <span className={`source-status source-status-${source.status}`} key={source.key}><Dot tone={source.status === "ready" ? "green" : source.status === "stale" ? "orange" : "gray"} />{source.label}<small>{source.status === "ready" ? "已同步" : source.status === "stale" ? "待更新" : "未接入"}</small></span>)}
      </div>
      <div className="inventory-ai-actions" aria-label="库存上下文 AI"><button type="button" className="row-action" onClick={() => askInventoryAi("analyze")}>问问小特 · 分析</button><button type="button" className="row-action" onClick={() => askInventoryAi("explain")}>指标解读</button><button type="button" className="row-action" onClick={() => askInventoryAi("export")}>导出表格</button></div>
      {canSyncInventory ? <>
        <label className="inventory-snapshot-input"><span>快照日期</span><input type="date" value={snapshotDate} onChange={(event) => setSnapshotDate(event.target.value)} disabled={syncing} /></label>
        <input ref={syncInputRef} className="file-input-hidden" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => void syncInventory(event.currentTarget.files?.[0])} />
        <button type="button" className="primary-button inventory-sync-button" disabled={syncing} onClick={() => syncInputRef.current?.click()}>{syncing ? `${syncProgress}%` : "↻ 同步库存"}</button>
      </> : <span className="soft-tag">仅管理员可同步库存；运营可维护执行计划</span>}
      {syncing && <div className="inventory-sync-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={syncProgress} aria-label={syncStage}><span style={{ width: `${syncProgress}%` }} /><small>{syncStage}</small></div>}
    </section>
  );

  const feedback = syncFeedback && <section className={`inventory-feedback inventory-feedback-${syncFeedback.tone}`} role={syncFeedback.tone === "error" ? "alert" : "status"}><span>{syncFeedback.tone === "success" ? "✓" : syncFeedback.tone === "warning" ? "!" : "×"}</span><div><strong>{syncFeedback.title}</strong><p>{syncFeedback.message}</p></div></section>;
  const refreshError = usesInventoryOverview && error && overview && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>最新数据刷新失败</strong><p>{error}</p></div><button className="row-action" onClick={() => setRetryKey((key) => key + 1)}>重试</button></section>;
  const ageRefreshError = usesInventoryAgeAnalysis && ageError && ageAnalysis && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>库龄数据刷新失败</strong><p>{ageError}；当前仍显示上一次成功结果。</p></div><button className="row-action" onClick={() => setAgeRetryKey((key) => key + 1)}>重试</button></section>;
  const inboundRefreshError = usesInboundMonitor && inboundError && inboundMonitor && <section className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>京东入仓监控刷新失败</strong><p>{inboundError}；当前仍显示上一次成功结果。</p></div><button className="row-action" onClick={() => setInboundRetryKey((key) => key + 1)}>重试</button></section>;

  if (usesInventoryAgeAnalysis && !ageAnalysis) {
    if (!ageLoading && ageError) {
      return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>库龄数据加载失败</strong><p>{ageError}</p><button className="secondary-button" onClick={() => setAgeRetryKey((key) => key + 1)}>重新加载</button></section></>;
    }
    return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state" role="status"><span className="state-spinner" /><strong>正在汇总库龄与动销数据</strong><p>正在读取最新库存快照中的库龄、前 7 天与前 30 天销量…</p></section></>;
  }

  if (usesInventoryAgeAnalysis && ageAnalysis && !ageAnalysis.hasInventory) {
    return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state inventory-empty-state"><span className="state-symbol">龄</span><strong>还没有可分析的库存快照</strong><p>请同步包含库龄字段的库存报表后再查看库龄分析和滞销清理。</p>{canSyncInventory && <button className="primary-button" onClick={() => syncInputRef.current?.click()}>选择库存报表</button>}</section></>;
  }

  if (usesInboundMonitor && !inboundMonitor) {
    if (!inboundLoading && inboundError) {
      return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>京东入仓监控加载失败</strong><p>{inboundError}</p><button className="secondary-button" onClick={() => setInboundRetryKey((key) => key + 1)}>重新加载</button></section></>;
    }
    return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state" role="status"><span className="state-spinner" /><strong>正在汇总京东入仓库存</strong><p>正在关联 RDC / DC 库存、供应商和近 7/30/90 日正向出库…</p></section></>;
  }

  if (usesInboundMonitor && inboundMonitor && !inboundMonitor.hasInventory) {
    return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state inventory-empty-state"><span className="state-symbol">仓</span><strong>暂无京东入仓库存</strong><p>当前快照中没有京东 RDC / DC 或可识别的京东区域平台仓。请核对仓名规则并同步最新库存。</p>{canSyncInventory && <button className="primary-button" onClick={() => syncInputRef.current?.click()}>选择库存报表</button>}</section></>;
  }

  if (usesInventoryOverview && loading && !overview) {
    return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state" role="status"><span className="state-spinner" /><strong>正在同步库存健康数据</strong><p>正在关联最新库存快照与近30天正向销售明细…</p></section></>;
  }

  if (usesInventoryOverview && !overview) {
    return <>{subnav}{syncBar}{feedback}{sharedFilterBar}<section className="panel data-state inventory-data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>库存数据加载失败</strong><p>{error || "暂时无法读取库存数据"}</p><button className="secondary-button" onClick={() => setRetryKey((key) => key + 1)}>重新加载</button></section></>;
  }

  if (usesInventoryOverview && overview && !overview.hasInventory) {
    return <>{subnav}{syncBar}{feedback}{refreshError}{sharedFilterBar}<section className="panel data-state inventory-data-state inventory-empty-state"><span className="state-symbol">库</span><strong>还没有库存快照</strong><p>请上传吉客云“分仓库存查询” .xlsx 报表。系统会保留批次、自动读取实盘库存与成本，并联动销售生成备货建议。</p>{canSyncInventory && <button className="primary-button" onClick={() => syncInputRef.current?.click()}>选择库存报表</button>}</section></>;
  }

  const totalHealth = Math.max(1, overview ? overview.health.noStock + overview.health.urgent
    + overview.health.warning + overview.health.stale + overview.health.slow + overview.health.healthy : 0);
  const planStatusLabel: Record<ReplenishmentPlanItem["status"], string> = {
    draft: "草稿",
    confirmed: "已确认",
    completed: "已完成",
    cancelled: "已取消",
  };
  const cleanupItems = (ageAnalysis?.items ?? []).filter((item) => item.status === "stagnant" || item.status === "slow" || item.status === "aged");
  const ageDistribution = ageAnalysis?.fineDistribution ?? [];
  const hasKnownAgeDistribution = ageDistribution.some((bucket) => bucket.quantity > 0);

  return (
    <>
      {subnav}
      {syncBar}
      {feedback}
      {refreshError}
      {ageRefreshError}
      {inboundRefreshError}
      {sharedFilterBar}
      {activeTab === "overview" && overview ? <>
        <section className="inventory-diagnosis-grid">
          <article className="panel inventory-health-panel">
            <SectionHeader title="库存健康分布" note="仅统计京东仓、天猫仓、广东仓、自营仓" />
            <div className="health-stack" aria-label="库存健康状态分布">
              {([
                ["no_stock", overview.health.noStock], ["urgent", overview.health.urgent], ["warning", overview.health.warning],
                ["stale", overview.health.stale], ["slow", overview.health.slow], ["healthy", overview.health.healthy],
              ] as [InventoryHealthStatus, number][]).map(([status, count]) => count > 0 && <i className={`health-${status}`} style={{ width: `${count / totalHealth * 100}%` }} title={`${inventoryStatusMeta[status].label} ${count}`} key={status} />)}
            </div>
            <div className="health-legend">
              {([
                ["no_stock", overview.health.noStock], ["urgent", overview.health.urgent], ["warning", overview.health.warning],
                ["stale", overview.health.stale], ["slow", overview.health.slow], ["healthy", overview.health.healthy],
              ] as [InventoryHealthStatus, number][]).map(([status, count]) => <button type="button" aria-pressed={filters.healthStatuses.length === 1 && filters.healthStatuses[0] === status} onClick={() => updateFilters({ ...filters, healthStatuses: toggleFilterGroup(filters.healthStatuses, [status]) })} key={status}><span className={`health-swatch health-${status}`} /><div><small>{inventoryStatusMeta[status].label}</small><strong>{formatCount(count)}</strong></div></button>)}
            </div>
            <div className="inventory-health-note"><span>积压风险与低周转货值</span><strong>{formatCurrencyFromCents(overview.metrics.slowMovingValueCents)}</strong><small>低周转：库存周转大于 180 天；工厂代发仓不计入</small></div>
          </article>

          <article className="panel replenishment-opportunity-panel">
            <SectionHeader title="优先补货建议" note="已扣减库存、报表在途和备货计划" />
            <div className="replenishment-opportunity-list">
              {recommendations.slice(0, 5).map((item, index) => <div key={item.key}><span className={`opportunity-rank ${index < 3 ? `top-${index + 1}` : ""}`}>{index + 1}</span><div><strong title={item.productName}>{item.productName}</strong><small>{item.warehouse} · 可售 {item.coverageDays?.toFixed(1) ?? "—"} 天</small></div><em>+{formatCount(item.suggestedQuantity ?? 0)}</em></div>)}
              {recommendations.length === 0 && <div className="inventory-mini-empty">{overview.quality.recommendationsSuppressed ? "先修复数据质量或仓库映射，再生成补货建议" : "当前没有需要补货的货品"}</div>}
            </div>
            <button className="inventory-plan-link" onClick={() => onModuleViewChange("plan")}>查看备货计划 <span>→</span></button>
          </article>
        </section>

        <section className="panel table-panel inventory-mapping-panel inventory-health-thirty-day-panel">
          <div className="table-toolbar"><div><h2>库存健康明细（近30天）</h2><p>与“销量近30天”保持同一货品汇总口径和列布局，库存、销量、周转、在途及补货预警可直接横向核对</p></div><span className="soft-tag">展示 {formatCount(overview.mapping.samples.length)} 个货品</span><button type="button" className="row-action" disabled={overview.mapping.samples.length === 0} onClick={() => downloadInventoryCsv(`库存健康_近30天_${overview.sync.inventoryAsOf ?? "snapshot"}.csv`, inventoryThirtyDayCsvRows(overview.mapping.samples))}>导出当前明细 CSV</button></div>
          <InventoryThirtyDayTable samples={overview.mapping.samples} loading={loading} canManageInventory={canManageInventory} planActionId={planActionId} onCreatePlan={openPlanModal} />
        </section>
      </> : activeTab === "plan" && overview ? <>
        <section className="inventory-kpi-grid inventory-plan-kpis data-refresh-region" aria-busy={loading}>
          <InventoryKpiCard selected={filters.planStatus === "draft"} onClick={() => updateFilters({ ...filters, planStatus: toggleSingleFilter(filters.planStatus, "draft", "") })} label="待确认草稿" value={`${formatCount(overview.planSummary.draftCount)} 项`} note="确认后进入执行队列" tone="orange" icon="草" />
          <InventoryKpiCard selected={filters.planStatus === "confirmed"} onClick={() => updateFilters({ ...filters, planStatus: toggleSingleFilter(filters.planStatus, "confirmed", "") })} label="已确认计划" value={`${formatCount(overview.planSummary.confirmedCount)} 项`} note="已计入在途库存" tone="blue" icon="确" />
          <InventoryKpiCard label="计划待回写量" value={`${formatCount(overview.planSummary.activeQuantity)} 件`} note="含完成后等待库存快照回写的数量" tone="purple" icon="途" />
          <InventoryKpiCard selected={filters.planStatus === "completed"} onClick={() => updateFilters({ ...filters, planStatus: toggleSingleFilter(filters.planStatus, "completed", "") })} label="已完成计划" value={`${formatCount(overview.planSummary.completedCount)} 项`} note="保留历史执行记录" tone="green" icon="完" />
        </section>

        <InventoryPlanWorkflowPanel summary={overview.planSummary} />

        <section className="panel table-panel replenishment-plan-panel">
          <div className="table-toolbar">
            <div><h2>备货计划</h2><p>草稿可多选后一键确认并提交钉钉；已确认计划可批量重试钉钉表或发送群消息</p></div>
            <span className="soft-tag">本页 {overview.plansPagination.returned} / 共 {overview.plansPagination.total} 项</span>
            <div className="inventory-toolbar-actions">
              {canManageInventory && <>
                <a className="row-action" href="/api/inventory/replenishment/import" download>下载导入模板</a>
                <input ref={planImportInputRef} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden onChange={(event) => void importReplenishmentPlans(event.target.files?.[0])} />
                <button type="button" className="row-action" disabled={planImporting || batchDraftConfirmLoading || batchDingTalkLoading || groupMessageLoading} onClick={() => planImportInputRef.current?.click()}>{planImporting ? "正在导入…" : "导入备货计划"}</button>
                <button type="button" className="primary-button" disabled={selectedDraftPlans.length === 0 || batchDraftConfirmLoading || batchDingTalkLoading || groupMessageLoading || planImporting} onClick={() => void batchConfirmDraftPlans()}>{batchDraftConfirmLoading ? "正在确认并提交…" : `一键确认并提交钉钉（${selectedDraftPlans.length}）`}</button>
                <button type="button" className="primary-button" disabled={selectedConfirmedPlans.length === 0 || batchDraftConfirmLoading || batchDingTalkLoading || groupMessageLoading || planImporting} onClick={() => void batchSyncPlansToDingTalk()}>{batchDingTalkLoading ? "正在批量提交…" : `批量提交钉钉表（${selectedConfirmedPlans.length}）`}</button>
                <button type="button" className="primary-button plan-group-send-button" title={groupSelectionReady ? "按采购与工厂汇总后发送群消息" : "发送群消息要求全部所选计划均为已确认，且填写对应采购和供应商"} disabled={!groupSelectionReady || groupMessageLoading || batchDraftConfirmLoading || batchDingTalkLoading || planImporting} onClick={() => void previewDingTalkGroupMessage()}>{groupMessageLoading && !groupPreview ? "正在核验钉钉…" : `发送钉钉群（${selectedConfirmedPlans.length}）`}</button>
              </>}
              <button type="button" className="row-action" disabled={overview.plans.length === 0} onClick={() => downloadInventoryCsv(`备货计划_${overview.sync.inventoryAsOf ?? "snapshot"}_第${overview.plansPagination.page}页.csv`, [["计划ID", "货品编号", "货品名称", "品牌", "分类", "供应商", "入库库房", "对应采购", "对应运营", "部门", "备货类型", "现有库存", "近30天销量", "预计消耗周期(天)", "系统建议", "备货数量", "下单日期", "预计到货日", "状态", "是否验货", "备注", "创建时间", "更新时间"], ...overview.plans.map((plan) => [plan.id, plan.productCode, plan.productName, plan.brand, plan.category, plan.supplier, plan.warehouse, plan.buyer, plan.operatorName, plan.department, plan.planType, plan.currentStockQuantity, plan.sales30dQuantity, plan.coverageDays, plan.suggestedQuantity, plan.plannedQuantity, plan.orderDate, plan.expectedArrivalDate, planStatusLabel[plan.status], plan.requiresInspection ? "是" : "否", plan.notes, plan.createdAt, plan.updatedAt])])}>导出当前页 CSV</button>
              <button className="secondary-button" onClick={() => onModuleViewChange("overview")}>返回库存明细</button>
            </div>
          </div>
          <div className="data-table-wrap data-refresh-region" aria-busy={loading}><table className="data-table replenishment-plan-table"><thead><tr><th className="plan-selection-cell"><input type="checkbox" aria-label="全选本页草稿和已确认备货计划" checked={allSelectablePlansSelected} disabled={selectablePlans.length === 0 || batchDraftConfirmLoading || batchDingTalkLoading} onChange={toggleAllPlanSelections} /></th><th>货品</th><th>品牌 / 供应商</th><th>入库库房</th><th>采购 / 运营</th><th>类型 / 部门</th><th>库存 / 近30天销量</th><th>预计消耗周期</th><th>备货数量</th><th>下单 / 到货</th><th>状态 / 验货</th><th>操作</th></tr></thead><tbody>
            {overview.plans.map((plan) => {
              const selectable = selectablePlanIds.has(plan.id);
              const rowBusy = planActionId === plan.id || batchDraftConfirmLoading || batchDingTalkLoading;
              return <tr key={plan.id}>
                <td className="plan-selection-cell"><input
                  type="checkbox"
                  aria-label={`选择 ${plan.productName}`}
                  checked={selectedPlanIds.has(plan.id)}
                  disabled={!selectable || rowBusy}
                  title={selectable ? (plan.status === "draft" ? "加入批量确认并提交钉钉" : "加入批量钉钉操作") : "只有草稿或已确认计划可批量操作"}
                  onChange={() => togglePlanSelection(plan.id)}
                /></td>
                <td><div className="product-cell"><span className="product-thumb">{plan.productName.slice(0, 1) || "货"}</span><span><strong>{plan.productName}</strong><small>{plan.productCode} · {plan.category || "未分类"}</small></span></div></td>
                <td><div className="inventory-number-cell"><strong>{plan.brand || "—"}</strong><small>{plan.supplier || "未映射供应商"}</small></div></td>
                <td>{plan.warehouse}</td>
                <td><div className="inventory-number-cell"><strong>{plan.buyer || "—"}</strong><small>运营 {plan.operatorName || "—"}</small></div></td>
                <td><div className="inventory-number-cell"><strong>{plan.planType || "—"}</strong><small>{plan.department || "—"}</small></div></td>
                <td><div className="inventory-number-cell"><strong>{formatCount(plan.currentStockQuantity)}</strong><small>销量 {plan.sales30dQuantity === null ? "—" : formatCount(plan.sales30dQuantity)}</small></div></td>
                <td>{plan.coverageDays === null ? "—" : `${plan.coverageDays.toFixed(1)} 天`}</td>
                <td>{plan.status === "draft" && canManageInventory ? <input
                  className="plan-quantity-input"
                  type="number"
                  min={1}
                  max={10000000}
                  disabled={rowBusy}
                  value={planQuantities[plan.id] ?? plan.plannedQuantity}
                  onChange={(event) => setPlanQuantities((current) => ({ ...current, [plan.id]: Math.max(1, Math.trunc(Number(event.target.value) || 1)) }))}
                  aria-label={`${plan.productName}计划数量`}
                /> : <strong>{formatCount(plan.plannedQuantity)}</strong>}<small className="cell-note">建议 {formatCount(plan.suggestedQuantity)}</small></td>
                <td><div className="inventory-number-cell"><strong>{plan.orderDate || "—"}</strong><small>到货 {plan.expectedArrivalDate || "—"}</small></div></td>
                <td><div className="inventory-number-cell"><span className={`status status-${plan.status === "draft" ? "warning" : plan.status === "confirmed" ? "success" : "purple"}`}><Dot tone={plan.status === "draft" ? "orange" : plan.status === "confirmed" ? "green" : "purple"} />{planStatusLabel[plan.status]}</span><small>{plan.requiresInspection ? "需要验货" : "无需验货"}</small>{plan.dingTalkSync.status === "failed" && <small className="plan-sync-error">钉钉同步失败：{plan.dingTalkSync.error || "请稍后重试原备货计划"}</small>}</div></td>
                <td><div className="plan-row-actions">{canManageInventory ? <>
                  {plan.status === "draft" && <><button className="row-action primary-row-action" disabled={rowBusy} onClick={() => void updatePlanStatus(plan, "confirmed")}>确认并提交钉钉</button><button className="row-action" disabled={rowBusy} onClick={() => void updatePlanStatus(plan, "cancelled")}>取消</button></>}
                  {plan.status === "confirmed" && <>{plan.dingTalkSync.status === "synced" ? <><span className="plan-done">✓ 钉钉已提交</span><button className="row-action" title="把历史标记更新为运营管理系统，并回查现有记录" disabled={rowBusy} onClick={() => void syncPlanToDingTalk(plan)}>{planActionId === plan.id ? "更新中…" : "更新钉钉记录"}</button></> : <button className="row-action primary-row-action" title={plan.dingTalkSync.error || "提交到钉钉备货管理表"} disabled={rowBusy || plan.dingTalkSync.status === "syncing"} onClick={() => void syncPlanToDingTalk(plan)}>{planActionId === plan.id || plan.dingTalkSync.status === "syncing" ? "提交中…" : plan.dingTalkSync.status === "failed" ? "重试提交钉钉" : "提交钉钉"}</button>}<button className="row-action" disabled={rowBusy} onClick={() => openProcurementWorkItem(plan)}>生成采购任务</button><button className="row-action" disabled={rowBusy} onClick={() => void updatePlanStatus(plan, "completed")}>完成</button><button className="row-action" disabled={rowBusy} onClick={() => void updatePlanStatus(plan, "cancelled")}>取消</button></>}
                  {plan.status === "completed" && <><span className="plan-done">✓ 已完成</span>{plan.dingTalkSync.status === "synced" && <span className="plan-done">✓ 钉钉已提交</span>}</>}
                  {plan.status === "cancelled" && <span className="soft-text">已取消</span>}
                </> : <span className="soft-text">只读</span>}</div></td>
              </tr>;
            })}
            {overview.plans.length === 0 && <tr><td colSpan={12}><div className="table-state">暂无备货计划。请在“库存总览”中创建备货计划。</div></td></tr>}
          </tbody></table></div>
          <footer className="jd-sku-pagination"><span>第 {overview.plansPagination.page} / {Math.max(1, overview.plansPagination.totalPages)} 页</span><div><button type="button" className="row-action" disabled={loading || overview.plansPagination.page <= 1} onClick={() => setPlanPage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={loading || overview.plansPagination.page >= Math.max(1, overview.plansPagination.totalPages)} onClick={() => setPlanPage((value) => value + 1)}>下一页</button></div></footer>
        </section>

      </> : activeTab === "inbound" && inboundMonitor ? <>
        <section className="inventory-kpi-grid inbound-kpi-grid data-refresh-region" aria-busy={inboundLoading}>
          <InventoryKpiCard label="京东入仓库存" value={`${formatCount(inboundMonitor.metrics.availableQuantity)} 件`} note={`${formatCount(inboundMonitor.metrics.itemCount)} 个 SKU × 仓库 · ${formatCount(inboundMonitor.metrics.warehouseCount)} 个仓`} tone="blue" icon="仓" />
          <InventoryKpiCard label="固定成本货值" value={formatCurrencyFromCents(inboundMonitor.metrics.knownStockValueCents)} note={`成本覆盖 ${formatRate(inboundMonitor.metrics.costCoverageRate)} · 暂无供应价口径`} tone="purple" icon="值" />
          <InventoryKpiCard label="30日计算周转" value={inboundMonitor.metrics.turnoverDays === null ? "待匹配" : `${inboundMonitor.metrics.turnoverDays.toFixed(1)} 天`} note={`出库 ${formatCount(inboundMonitor.metrics.outbound30dQuantity)} 件 · 销量匹配 ${formatRate(inboundMonitor.metrics.salesMatchRate)}`} tone="green" icon="转" />
          <InventoryKpiCard selected={inboundCard === "stale"} onClick={() => setInboundCard(current => toggleSingleFilter(current, "stale", ""))} label="滞销 / 长库龄" value={`${formatCount(inboundMonitor.metrics.staleItemCount)} 项`} note={`${formatCurrencyFromCents(inboundMonitor.metrics.staleValueCents)} · 供应商缺口 ${formatCount(inboundMonitor.metrics.missingSupplierCount)}`} tone="orange" icon="险" />
        </section>
        <section className="inventory-feedback inventory-feedback-warning inbound-disclosure" role="status"><span>!</span><div><strong>口径边界</strong><p>{inboundMonitor.disclosures.join("；")}</p></div></section>
        <section className="panel inbound-region-panel data-refresh-region" aria-busy={inboundLoading}><div className="table-toolbar"><div><h2>RDC / DC 区域概览</h2><p>库存、在途、固定成本货值和 30 日计算周转按入仓仓库拆分</p></div><span className="soft-tag">计算口径，非京东原生指标</span></div><div className="inbound-region-grid">{inboundMonitor.regions.map((region) => <article key={region.warehouse}><span>{region.warehouse}</span><strong>{formatCount(region.availableQuantity)} 件</strong><small>{formatCurrencyFromCents(region.knownStockValueCents)} · 在途 {formatCount(region.inTransitQuantity)}</small><div><em>30日出库 {formatCount(region.outbound30dQuantity)}</em><em>周转 {region.turnoverDays === null ? "—" : `${region.turnoverDays.toFixed(1)}天`}</em></div><small>销量匹配 {formatRate(region.salesMatchRate)}</small></article>)}</div></section>
        <InventoryInboundActionPanel
          metrics={inboundMonitor.metrics}
          nativeComparisonAvailable={inboundMonitor.scope.nativeComparisonAvailable}
          onShowMissingSuppliers={() => updateFilters({ ...filters, suppliers: ["未映射供应商"] })}
          onOpenStale={() => {
            updateFilters({ ...filters, ageStatuses: ["stagnant", "slow", "aged"], ageBuckets: [] });
            onModuleViewChange("stale");
          }}
        />
        <section className="panel table-panel inbound-detail-panel data-refresh-region" aria-busy={inboundLoading}><div className="table-toolbar"><div><h2>京东入仓 SKU 明细</h2><p>7/30/90 日出库只统计正向销量；退款不计为出库</p></div><span className="soft-tag">显示 {formatCount(inboundMonitor.pagination.returned)} / {formatCount(inboundMonitor.pagination.total)}</span><button type="button" className="row-action" disabled={inboundMonitor.items.length === 0} onClick={() => downloadInventoryCsv(`京东入仓监控_${inboundMonitor.sync.inventoryAsOf ?? "snapshot"}_第${inboundMonitor.pagination.page}页.csv`, [["货品编码", "货品名称", "品类", "供应商", "RDC/DC", "可用库存", "在途", "7日出库", "30日出库", "90日出库", "计算周转天数", "库龄天数", "固定成本分", "库存货值分", "风险"], ...inboundMonitor.items.map((item) => [item.productCode, item.productName, item.category, item.supplier, item.warehouse, item.availableQuantity, item.inTransitQuantity, item.outbound7dQuantity, item.outbound30dQuantity, item.outbound90dQuantity, item.turnoverDays, item.inventoryAgeDays, item.unitCostCents, item.knownStockValueCents, inboundRiskMeta[item.risk].label])])}>导出当前页 CSV</button></div><div className="data-table-wrap"><table className="data-table inbound-detail-table"><thead><tr><th>货品</th><th>供应商</th><th>RDC / DC</th><th>库存 / 在途</th><th>7日出库</th><th>30日出库</th><th>90日出库</th><th>计算周转</th><th>库龄</th><th>固定成本 / 货值</th><th>风险</th></tr></thead><tbody>{inboundMonitor.items.map((item) => { const risk = inboundRiskMeta[item.risk]; return <tr key={item.key}><td><div className="product-cell inventory-product-cell"><span className="product-thumb">{item.productName.slice(0, 1) || "货"}</span><span><strong title={item.productName}>{item.productName}</strong><small>{item.productCode} · {item.category}</small></span></div></td><td>{item.supplier}<small className="cell-note">{item.supplier === "未映射供应商" ? "待补映射" : "ERP 货品档案"}</small></td><td>{item.warehouse}</td><td><strong>{formatCount(item.availableQuantity)}</strong><small className="cell-note">在途 {formatCount(item.inTransitQuantity)}</small></td><td>{item.outbound7dQuantity === null ? "—" : formatCount(item.outbound7dQuantity)}</td><td>{item.outbound30dQuantity === null ? "—" : formatCount(item.outbound30dQuantity)}</td><td>{item.outbound90dQuantity === null ? "—" : formatCount(item.outbound90dQuantity)}</td><td>{item.turnoverDays === null ? "—" : `${item.turnoverDays.toFixed(1)} 天`}</td><td>{item.inventoryAgeDays === null ? "—" : `${formatCount(item.inventoryAgeDays)} 天`}</td><td>{item.unitCostCents === null ? "—" : formatCurrencyFromCents(item.unitCostCents)}<small className="cell-note">货值 {formatCurrencyFromCents(item.knownStockValueCents)}</small></td><td><span className={`status status-${risk.tone}`}><Dot tone={risk.dot} />{risk.label}</span></td></tr>; })}{inboundMonitor.items.length === 0 && <tr><td colSpan={11}><div className="table-state">没有符合当前筛选条件的京东入仓库存。</div></td></tr>}</tbody></table></div><footer className="jd-sku-pagination"><span>第 {inboundMonitor.pagination.page} / {Math.max(1, inboundMonitor.pagination.totalPages)} 页</span><div><button type="button" className="row-action" disabled={inboundLoading || inboundMonitor.pagination.page <= 1} onClick={() => setInboundPage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={inboundLoading || inboundMonitor.pagination.page >= Math.max(1, inboundMonitor.pagination.totalPages)} onClick={() => setInboundPage((value) => value + 1)}>下一页</button></div></footer></section>
      </> : <>
        {ageLoading && !ageAnalysis && <section className="panel data-state inventory-data-state" role="status"><span className="state-spinner" /><strong>正在汇总库龄与动销数据</strong><p>正在读取最新库存快照中的库龄、前 7 天与前 30 天销量…</p></section>}
        {!ageLoading && !ageError && ageAnalysis && !ageAnalysis.hasInventory && <section className="panel data-state inventory-data-state inventory-empty-state"><span className="state-symbol">龄</span><strong>还没有可分析的库存快照</strong><p>请同步包含库龄字段的库存报表后再查看库龄分析和滞销清理。</p></section>}
        {ageAnalysis?.hasInventory && activeTab === "age" && <>
          <section className="inventory-kpi-grid age-kpi-grid data-refresh-region" aria-busy={ageLoading}><InventoryKpiCard label="库龄明细" value={`${formatCount(ageAnalysis.metrics.skuWarehouseCount)} 条`} note={`快照日期 ${ageAnalysis.sync.inventoryAsOf ?? "—"}`} tone="blue" icon="龄" /><InventoryKpiCard label={ageAnalysis.metrics.stockValueComplete ? "90天以上货值" : "已覆盖90天以上货值"} value={formatCurrencyFromCents(ageAnalysis.metrics.aged90ValueCents)} note={ageAnalysis.metrics.stockValueComplete ? `${formatCount(ageAnalysis.metrics.aged90Count)} 个 SKU × 仓库` : "缺少成本的库存未计入货值"} tone="orange" icon="90" /><InventoryKpiCard selected={ageCard === "stagnant"} onClick={() => setAgeCard(current => toggleSingleFilter(current, "stagnant", ""))} label="滞销清理" value={`${formatCount(ageAnalysis.metrics.stagnantCount)} 项`} note={ageAnalysis.sync.hasAgeSales ? "库龄≥90天且前30天销量为0" : "报表未提供前30天销量"} tone="purple" icon="清" /><InventoryKpiCard selected={ageCard === "zero_sales"} onClick={() => setAgeCard(current => toggleSingleFilter(current, "zero_sales", ""))} label="30天零销量" value={ageAnalysis.sync.hasAgeSales ? `${formatCount(ageAnalysis.metrics.zeroSalesCount)} 项` : "—"} note="仅统计有可用库存的商品" tone="green" icon="零" /></section>
          {ageAnalysis.coverage.unagedStockCount > 0 && <section className="inventory-feedback inventory-feedback-warning" role="status"><span>!</span><div><strong>部分库存缺少库龄</strong><p>{formatCount(ageAnalysis.coverage.unagedStockCount)} 个 SKU × 仓库、{formatCount(ageAnalysis.coverage.unagedQuantity)} 件库存未进入库龄区间占比。</p></div></section>}
          <section className="panel inventory-age-distribution-panel data-refresh-region" aria-labelledby="inventory-age-distribution-title" aria-busy={ageLoading}>
            <div className="table-toolbar inventory-age-distribution-heading"><div><h2 id="inventory-age-distribution-title">库龄分布图</h2><p>库存数量按可用库存件数统计；库存金额按固定成本价 × 可用库存计算</p></div><div className="age-distribution-legend" aria-label="库龄分布图图例"><span><i className="quantity" />库存数量</span><span><i className="value" />库存金额</span></div></div>
            {!hasKnownAgeDistribution && <div className="inventory-age-distribution-empty" role="status"><strong>当前筛选范围没有已识别的库龄数量</strong><p>10 个库龄区间和成本金额口径已启用；请确认当前库龄快照已正确导入“库龄(天)”列。</p></div>}
            {hasKnownAgeDistribution && <InventoryAgeDistributionChart buckets={ageDistribution} stockValueComplete={ageAnalysis.metrics.stockValueComplete} />}
          </section>
          <InventoryAgeSummaryPanel buckets={ageDistribution} stockValueComplete={ageAnalysis.metrics.stockValueComplete} selectedBuckets={filters.ageBuckets} onToggleBucket={toggleAgeBucket} />
          <section className="panel table-panel inventory-age-table-panel data-refresh-region" aria-busy={ageLoading}>
            <div className="table-toolbar">
              <div><h2>库龄分析明细</h2><p>{ageAnalysis.sync.hasAgeSales ? "库龄、前 7 天销量与前 30 天销量来自本次库龄报表" : "当前报表未提供销量列，系统仅展示库龄风险"}</p></div>
              <span className="soft-tag">显示 {formatCount(ageAnalysis.items.length)} / {formatCount(ageAnalysis.pagination.total)}</span>
              <button type="button" className="row-action" disabled={ageAnalysis.items.length === 0} onClick={() => downloadInventoryCsv(`库龄分析_${ageAnalysis.sync.inventoryAsOf ?? "snapshot"}_第${ageAnalysis.pagination.page}页.csv`, [["货品编码", "货品名称", "品牌", "品类", "仓库类型", "仓库", "计入库存", "库龄分布", "库龄天数", "库存数", "库存金额（元）", "前7天销量", "前30天销量", "状态"], ...ageAnalysis.items.map((item) => [item.productCode, item.productName, item.brand, item.category, item.warehouseLabel, item.warehouse, item.includedInInventory ? "是" : "否", item.ageBucketLabel, item.inventoryAgeDays, item.availableQuantity, item.stockValueCents === null ? null : (item.stockValueCents / 100).toFixed(2), item.sales7dQuantity, item.sales30dQuantity, item.statusLabel])])}>导出当前页 CSV</button>
            </div>
            <div className="data-table-wrap"><table className="data-table inventory-age-table"><thead><tr><th>货品</th><th>品牌 / 品类</th><th>库存类型 / 仓库</th><th>库龄分布</th><th>库龄天数</th><th>库存数</th><th>库存金额</th><th>前7天销量</th><th>前30天销量</th><th>状态</th></tr></thead><tbody>{ageAnalysis.items.map((item) => {
              const meta = inventoryAgeStatusMeta[item.status];
              const tone = meta.tone === "danger" ? "red" : meta.tone === "warning" ? "orange" : meta.tone === "success" ? "green" : meta.tone;
              return <tr key={item.key}>
                <td><div className="product-cell inventory-product-cell"><span className="product-thumb">{item.productName.slice(0, 1) || "货"}</span><span><strong title={item.productName}>{item.productName}</strong><small>{item.productCode}{item.specification ? ` · ${item.specification}` : ""}</small></span></div></td>
                <td><div className="inventory-dimension-cell"><strong>{item.brand || "未设置品牌"}</strong><small>{item.category || "未分类"}</small></div></td>
                <td><div className="inventory-warehouse-cell"><span className={`warehouse-type warehouse-type-${item.warehouseType}`}>{item.warehouseLabel || inventoryWarehouseCategoryLabel(item.warehouseCategory)}</span><small>{item.warehouse} · {item.includedInInventory ? "计入库存" : "排除库存"}</small></div></td>
                <td><span className={`age-bucket-tag ${item.ageBucketKey ? "" : "unknown"}`}>{item.ageBucketLabel}</span></td>
                <td><strong>{item.inventoryAgeDays === null ? "—" : `${formatCount(item.inventoryAgeDays)} 天`}</strong></td>
                <td><strong>{formatCount(item.availableQuantity)}</strong></td>
                <td>{item.stockValueCents === null ? "—" : formatCurrencyFromCents(item.stockValueCents)}</td>
                <td>{item.sales7dQuantity === null ? "—" : formatCount(item.sales7dQuantity)}</td>
                <td>{item.sales30dQuantity === null ? "—" : formatCount(item.sales30dQuantity)}</td>
                <td><span className={`status status-${meta.tone}`} title={item.recommendation}><Dot tone={tone} />{item.statusLabel}</span></td>
              </tr>;
            })}{ageAnalysis.items.length === 0 && <tr><td colSpan={10}><div className="table-state">当前快照没有可展示的库龄记录。</div></td></tr>}</tbody></table></div>
          </section>
          <footer className="jd-sku-pagination"><span>第 {ageAnalysis.pagination.page} / {Math.max(1, ageAnalysis.pagination.totalPages)} 页</span><div><button type="button" className="row-action" disabled={ageLoading || ageAnalysis.pagination.page <= 1} onClick={() => setAgePage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={ageLoading || ageAnalysis.pagination.page >= Math.max(1, ageAnalysis.pagination.totalPages)} onClick={() => setAgePage((value) => value + 1)}>下一页</button></div></footer>
        </>}
        {ageAnalysis?.hasInventory && activeTab === "stale" && <>
          <section className="inventory-kpi-grid age-kpi-grid data-refresh-region" aria-busy={ageLoading}><InventoryKpiCard selected={ageCard === "stagnant"} onClick={() => setAgeCard(current => toggleSingleFilter(current, "stagnant", ""))} label="优先清理项" value={`${formatCount(ageAnalysis.metrics.stagnantCount)} 项`} note="库龄≥90天且近30日无销量" tone="orange" icon="清" /><InventoryKpiCard label={ageAnalysis.metrics.stockValueComplete ? "待处理货值" : "已覆盖待处理货值"} value={formatCurrencyFromCents(ageAnalysis.metrics.stagnantValueCents)} note={ageAnalysis.metrics.stockValueComplete ? "按固定成本价与可用库存计算" : "缺少成本的库存未计入货值"} tone="purple" icon="值" /><InventoryKpiCard selected={ageCard === "aged90"} onClick={() => setAgeCard(current => toggleSingleFilter(current, "aged90", ""))} label="高库龄商品" value={`${formatCount(ageAnalysis.metrics.aged90Count)} 项`} note="库龄超过90天且仍有可用库存" tone="blue" icon="龄" /><InventoryKpiCard selected={ageCard === "zero_sales"} onClick={() => setAgeCard(current => toggleSingleFilter(current, "zero_sales", ""))} label="零销量库存" value={ageAnalysis.sync.hasAgeSales ? `${formatCount(ageAnalysis.metrics.zeroSalesCount)} 项` : "—"} note="前30天销量为0" tone="green" icon="零" /></section>
          <InventoryStalePlaybookPanel metrics={ageAnalysis.metrics} hasAgeSales={ageAnalysis.sync.hasAgeSales} />
          <section className="panel table-panel stale-cleanup-panel data-refresh-region" aria-busy={ageLoading}>
            <div className="table-toolbar">
              <div><h2>滞销清理清单</h2><p>创建清理事项后进入“运营事务”跟进；系统不会自动改库存或删除数据。</p></div>
              <span className="soft-tag">优先处理 {formatCount(ageAnalysis.metrics.cleanupCount)} 项</span>
              <button type="button" className="row-action" disabled={cleanupItems.length === 0} onClick={() => downloadInventoryCsv(`滞销清理_${ageAnalysis.sync.inventoryAsOf ?? "snapshot"}_第${ageAnalysis.pagination.page}页.csv`, [["货品编码", "货品名称", "品牌", "品类", "仓库类型", "仓库", "计入库存", "库龄天数", "前30天销量", "可用库存", "库存货值（元）", "清理策略", "清理建议", "风险状态"], ...cleanupItems.map((item) => {
                const strategy = getInventoryCleanupStrategy(item.status);
                return [item.productCode, item.productName, item.brand, item.category, item.warehouseLabel, item.warehouse, item.includedInInventory ? "是" : "否", item.inventoryAgeDays, item.sales30dQuantity, item.availableQuantity, item.stockValueCents === null ? null : (item.stockValueCents / 100).toFixed(2), strategy.label, item.recommendation, item.statusLabel];
              })])}>导出当前页 CSV</button>
            </div>
            <div className="data-table-wrap"><table className="data-table stale-cleanup-table"><thead><tr><th>货品</th><th>品牌 / 品类</th><th>库存类型 / 仓库</th><th>库龄</th><th>前30天销量</th><th>可用库存</th><th>库存货值</th><th>清理策略</th><th>清理建议</th><th>风险状态</th><th>执行</th></tr></thead><tbody>{cleanupItems.map((item) => {
              const meta = inventoryAgeStatusMeta[item.status];
              const tone = meta.tone === "danger" ? "red" : meta.tone === "warning" ? "orange" : meta.tone;
              const strategy = getInventoryCleanupStrategy(item.status);
              return <tr key={item.key}>
                <td><div className="product-cell inventory-product-cell"><span className="product-thumb">{item.productName.slice(0, 1) || "货"}</span><span><strong title={item.productName}>{item.productName}</strong><small>{item.productCode}</small></span></div></td>
                <td><div className="inventory-dimension-cell"><strong>{item.brand || "未设置品牌"}</strong><small>{item.category || "未分类"}</small></div></td>
                <td><div className="inventory-warehouse-cell"><span className={`warehouse-type warehouse-type-${item.warehouseType}`}>{item.warehouseLabel || inventoryWarehouseCategoryLabel(item.warehouseCategory)}</span><small>{item.warehouse} · {item.includedInInventory ? "计入库存" : "排除库存"}</small></div></td>
                <td>{item.inventoryAgeDays === null ? "—" : `${formatCount(item.inventoryAgeDays)} 天`}</td>
                <td>{item.sales30dQuantity === null ? "—" : formatCount(item.sales30dQuantity)}</td>
                <td>{formatCount(item.availableQuantity)}</td>
                <td>{item.stockValueCents === null ? "—" : formatCurrencyFromCents(item.stockValueCents)}</td>
                <td><div className="inventory-strategy-cell"><strong>{strategy.label}</strong><small>{strategy.note}</small></div></td>
                <td><span className="cleanup-recommendation">{item.recommendation}</span></td>
                <td><span className={`status status-${meta.tone}`}><Dot tone={tone} />{item.statusLabel}</span></td>
                <td>{canManageInventory ? <button className="row-action primary-row-action" onClick={() => openCleanupWorkItem(item)}>创建清理事项</button> : <span className="soft-text">只读</span>}</td>
              </tr>;
            })}{cleanupItems.length === 0 && <tr><td colSpan={11}><div className="table-state">当前没有需要优先清理的滞销或高库龄商品。</div></td></tr>}</tbody></table></div>
          </section>
          <footer className="jd-sku-pagination"><span>第 {ageAnalysis.pagination.page} / {Math.max(1, ageAnalysis.pagination.totalPages)} 页</span><div><button type="button" className="row-action" disabled={ageLoading || ageAnalysis.pagination.page <= 1} onClick={() => setAgePage((value) => Math.max(1, value - 1))}>上一页</button><button type="button" className="row-action" disabled={ageLoading || ageAnalysis.pagination.page >= Math.max(1, ageAnalysis.pagination.totalPages)} onClick={() => setAgePage((value) => value + 1)}>下一页</button></div></footer>
        </>}
      </>}
      {groupPreview && <div className="modal-backdrop inventory-work-item-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !groupMessageLoading) setGroupPreview(null); }}>
        <section className="inventory-work-item-modal inventory-dingtalk-group-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-dingtalk-group-title">
          <header><div><small>发送前确认</small><h2 id="inventory-dingtalk-group-title">钉钉备货群消息</h2><p>备货计划群与机器人已完成动态精确匹配</p></div><button type="button" aria-label="关闭" disabled={groupMessageLoading} onClick={() => setGroupPreview(null)}>×</button></header>
          <div className="inventory-dingtalk-group-summary"><span><small>目标群</small><strong>{groupPreview.targetGroupName}</strong></span><span><small>机器人</small><strong>{groupPreview.robotName}</strong></span><span><small>备货计划</small><strong>{groupPreview.planCount} 条</strong></span></div>
          <div className="inventory-dingtalk-message-preview"><span>将发送以下内容</span><pre>{groupPreview.message}</pre></div>
          <footer><span>确认后会先校验并更新所选钉钉多维表记录，再向群内发送一次消息并 @ 对应采购。</span><div><button type="button" className="secondary-button" disabled={groupMessageLoading} onClick={() => setGroupPreview(null)}>取消</button><button type="button" className="primary-button" disabled={groupMessageLoading} onClick={() => void sendDingTalkGroupMessage()}>{groupMessageLoading ? "正在发送…" : "确认发送"}</button></div></footer>
        </section>
      </div>}

      {planDraft && <div className="modal-backdrop inventory-work-item-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !planSaving) setPlanDraft(null); }}>
        <form className="inventory-work-item-modal inventory-plan-create-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-plan-create-title" onSubmit={(event) => { event.preventDefault(); void submitPlan(); }}>
          <header><div><h2 id="inventory-plan-create-title">创建备货计划</h2><p>库存来自所选入库库房，销量固定为货品近30天正向总销量；人工字段会随计划保存。</p></div><button type="button" aria-label="关闭" disabled={planSaving} onClick={() => setPlanDraft(null)}>×</button></header>
          <div className="inventory-work-item-fields inventory-plan-create-fields">
            <label><span className="required-field">货品编号</span><select required value={planDraft.productCode} onChange={(event) => { const candidate = planCandidates.find((item) => item.productCode === event.target.value); if (candidate) setPlanDraft(planDraftFromCandidate(candidate)); }}>{planCandidates.map((candidate) => <option key={candidate.productCode} value={candidate.productCode}>{candidate.productCode}</option>)}</select></label>
            <label className="inventory-plan-name-field"><span>货品名称</span><input readOnly value={planDraft.productName} /></label>
            <label><span>品牌</span><input readOnly value={planDraft.brand || "—"} /></label>
            <label><span>供应商</span><input readOnly value={planDraft.supplier || "未映射供应商"} /></label>
            <label><span>对应采购</span><input maxLength={200} value={planDraft.buyer} onChange={(event) => setPlanDraft((current) => current ? { ...current, buyer: event.target.value } : current)} placeholder="填写采购负责人" /></label>
            <label><span className="required-field">入库库房</span><select required value={planDraft.warehouse} onChange={(event) => setPlanDraft((current) => {
              if (!current) return current;
              const option = current.warehouseOptions.find((item) => item.warehouse === event.target.value);
              if (!option) return current;
              const expectedConsumptionDays = current.sales30dQuantity !== null && current.sales30dQuantity > 0
                ? Math.min(3650, Math.round((Math.max(0, option.availableQuantity) / (current.sales30dQuantity / 30)) * 10) / 10)
                : null;
              return { ...current, warehouse: option.warehouse, key: option.key, currentStockQuantity: option.availableQuantity, suggestedQuantity: option.suggestedQuantity, plannedQuantity: Math.max(0, option.suggestedQuantity ?? 0), expectedConsumptionDays };
            })}><option value="" disabled>选择库房</option>{planDraft.warehouseOptions.map((option) => <option key={option.key} value={option.warehouse}>{option.warehouse}{option.inDraftPlan ? "（已有草稿）" : ""}</option>)}</select></label>
            <label><span>现有库存</span><input readOnly value={formatCount(planDraft.currentStockQuantity)} /></label>
            <label><span>近30天总销量</span><input readOnly value={planDraft.sales30dQuantity === null ? "—" : formatCount(planDraft.sales30dQuantity)} /></label>
            <label><span className="required-field">备货数量</span><input required type="number" min={1} max={10000000} value={planDraft.plannedQuantity} onChange={(event) => setPlanDraft((current) => current ? { ...current, plannedQuantity: Math.max(0, Math.min(10000000, Math.trunc(Number(event.target.value) || 0))) } : current)} /></label>
            <label><span>预计消耗周期(天)</span><div className="inventory-plan-unit-input"><input type="number" min={0} max={3650} step={0.1} value={planDraft.expectedConsumptionDays ?? ""} onChange={(event) => { const value = event.target.valueAsNumber; setPlanDraft((current) => current ? { ...current, expectedConsumptionDays: Number.isFinite(value) ? Math.max(0, Math.min(3650, Math.round(value * 10) / 10)) : null } : current); }} placeholder="可手工调整" /><em>天</em></div></label>
            <label><span>下单日期</span><input type="date" value={planDraft.orderDate} onChange={(event) => setPlanDraft((current) => current ? { ...current, orderDate: event.target.value } : current)} /></label>
            <label><span>备货类型</span><input maxLength={100} value={planDraft.planType} onChange={(event) => setPlanDraft((current) => current ? { ...current, planType: event.target.value } : current)} placeholder="如 常规/促销" /></label>
            <label><span>对应运营</span><input maxLength={200} value={planDraft.operatorName} onChange={(event) => setPlanDraft((current) => current ? { ...current, operatorName: event.target.value } : current)} placeholder="填写运营负责人" /></label>
            <label><span>部门（选填）</span><input maxLength={200} value={planDraft.department} onChange={(event) => setPlanDraft((current) => current ? { ...current, department: event.target.value } : current)} placeholder="默认志高项目组，可留空" /></label>
            <label><span>预计到货日</span><input type="date" value={planDraft.expectedArrivalDate} onChange={(event) => setPlanDraft((current) => current ? { ...current, expectedArrivalDate: event.target.value } : current)} /></label>
            <label><span>状态</span><select value={planDraft.status} onChange={(event) => setPlanDraft((current) => current ? { ...current, status: event.target.value as ReplenishmentPlanDraft["status"] } : current)}><option value="draft">草稿</option><option value="confirmed">已确认</option></select></label>
            <label><span>是否验货</span><select value={planDraft.requiresInspection ? "yes" : "no"} onChange={(event) => setPlanDraft((current) => current ? { ...current, requiresInspection: event.target.value === "yes" } : current)}><option value="no">否</option><option value="yes">是</option></select></label>
            <label className="inventory-work-item-notes"><span>备注</span><textarea rows={3} maxLength={1000} value={planDraft.notes} onChange={(event) => setPlanDraft((current) => current ? { ...current, notes: event.target.value } : current)} /></label>
          </div>
          <footer><span>{planDraft.suggestedQuantity === null || planDraft.suggestedQuantity <= 0 ? "当前无精确系统建议，可填写人工备货量并保留审计。" : `系统建议 ${formatCount(planDraft.suggestedQuantity)} 件；可按实际采购情况调整。`}</span><div><button type="button" className="secondary-button" disabled={planSaving} onClick={() => setPlanDraft(null)}>取消</button><button type="submit" className="primary-button" disabled={planSaving || !planDraft.key || planDraft.plannedQuantity < 1}>{planSaving ? (planDraft.status === "confirmed" ? "正在确认并提交…" : "正在保存…") : (planDraft.status === "confirmed" ? "确认并提交钉钉" : "保存草稿")}</button></div></footer>
        </form>
      </div>}
      {workItemDraft && <div className="modal-backdrop inventory-work-item-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !workItemSaving) setWorkItemDraft(null); }}>
        <form className="inventory-work-item-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-work-item-title" onSubmit={(event) => { event.preventDefault(); void submitInventoryWorkItem(); }}>
          <header><div><small>{workItemDraft.kind === "procurement" ? "补货 → 采购执行" : "滞销 → 清理执行"}</small><h2 id="inventory-work-item-title">{workItemDraft.kind === "procurement" ? "创建采购备货任务" : "创建滞销清理事项"}</h2><p>{workItemDraft.label}</p></div><button type="button" aria-label="关闭" disabled={workItemSaving} onClick={() => setWorkItemDraft(null)}>×</button></header>
          <div className="inventory-work-item-fields">
            <label><span>负责人</span><input required maxLength={120} value={workItemDraft.owner} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, owner: event.target.value } : current)} /></label>
            <label><span>截止日期</span><input required type="date" value={workItemDraft.dueDate} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, dueDate: event.target.value } : current)} /></label>
            {workItemDraft.kind === "procurement" ? <>
              <label><span>计划类型</span><select value={workItemDraft.planType} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, planType: event.target.value as InventoryWorkItemDraft["planType"] } : current)}><option value="daily">日常补货</option><option value="new_product">新品首单</option></select></label>
              <label><span>预计到货日期</span><input required type="date" value={workItemDraft.expectedArrivalDate} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, expectedArrivalDate: event.target.value } : current)} /></label>
            </> : <>
              <label><span>清理方案</span><select value={workItemDraft.cleanupStrategy} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, cleanupStrategy: event.target.value as InventoryWorkItemDraft["cleanupStrategy"] } : current)}><option value="promotion">促销清理</option><option value="transfer">渠道 / 仓间调拨</option><option value="return">退供或清退评估</option><option value="review">人工复核后定方案</option></select></label>
              <label><span>期望消耗天数</span><input required type="number" min={1} max={365} value={workItemDraft.expectedConsumptionDays} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, expectedConsumptionDays: Math.max(1, Math.min(365, Math.trunc(Number(event.target.value) || 1))) } : current)} /></label>
            </>}
            <label className="inventory-work-item-notes"><span>备注</span><textarea rows={4} maxLength={800} value={workItemDraft.notes} onChange={(event) => setWorkItemDraft((current) => current ? { ...current, notes: event.target.value } : current)} placeholder="补充供应商沟通、价格、调拨渠道或清理要求" /></label>
          </div>
          <footer><span>保存后自动进入“运营事务”，并关联本次库存对象。</span><div><button type="button" className="secondary-button" disabled={workItemSaving} onClick={() => setWorkItemDraft(null)}>取消</button><button type="submit" className="primary-button" disabled={workItemSaving || !workItemDraft.owner.trim()}>{workItemSaving ? "正在创建…" : "创建执行事项"}</button></div></footer>
        </form>
      </div>}
    </>
  );
}
