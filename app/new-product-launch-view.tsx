/* eslint-disable @next/next/no-img-element -- 用户提供的商品图片域名不固定，不能安全配置 Next Image 远端允许清单。 */
"use client";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { requestJson } from "@/lib/http/api-client";
import Dialog from "./ui/dialog";

type Priority = "high" | "normal" | "low";
type ProjectStatus = "not_started" | "in_progress" | "blocked" | "completed" | "paused" | "cancelled";
type StageStatus = "not_started" | "in_progress" | "blocked" | "completed" | "not_applicable";
type StageKey = "modeling" | "pricing" | "image" | "video" | "listing" | "stocking" | "review";
type TargetStatus = "pending" | "ready" | "listed" | "paused";

type LaunchTarget = {
  id?: string;
  platform: string;
  shopName: string;
  channel: string;
  listingSku: string;
  listingUrl: string;
  status: TargetStatus;
};
type LaunchStage = {
  id: string;
  stageKey: StageKey;
  label: string;
  status: StageStatus;
  owner: string;
  plannedDueDate: string | null;
  completedAt: string | null;
  blocker: string;
  notes: string;
  evidenceUrl: string;
  evidenceLabel: string;
  version: number;
  updatedBy: string;
  updatedAt: string;
};
type LaunchActivity = {
  id: string;
  action: string;
  actorEmail: string;
  actorRole: string;
  stageKey: StageKey | null;
  fromStatus: string | null;
  toStatus: string | null;
  changedFields: string[];
  createdAt: string;
};
type LaunchProject = {
  id: string;
  productName: string;
  supplierName: string;
  brand: string;
  category: string;
  erpProductCode: string;
  skuCode: string;
  spuCode: string;
  productImageUrl: string;
  proposedBy: string;
  proposedDate: string;
  owner: string;
  targetLaunchDate: string | null;
  lifecycleStatus: "active" | "paused" | "cancelled";
  status: ProjectStatus;
  priority: Priority;
  recommendedPriceCents: number | null;
  approvedPriceCents: number | null;
  estimatedGrossMarginBps: number | null;
  source: "manual" | "system" | "import" | "integration";
  sourceRef: string;
  shopPlan: string | null;
  notes: string;
  version: number;
  progressPercent: number;
  currentStageKey: StageKey | null;
  overdue: boolean;
  overdueStageCount: number;
  targets: LaunchTarget[];
  stages: LaunchStage[];
  activity?: LaunchActivity[];
  createdAt: string;
  updatedAt: string;
};
type LaunchSummary = {
  total: number;
  notStarted: number;
  inProgress: number;
  blocked: number;
  completed: number;
  paused: number;
  cancelled: number;
  overdue: number;
  stageSummary: Array<Record<StageStatus, number> & { stageKey: StageKey; label: string }>;
};
type LaunchFacets = { suppliers: string[]; owners: string[]; categories: string[]; platforms: string[]; shopNames: string[]; sources: string[] };
type LaunchPayload = {
  structured: true;
  backendMode: "django";
  items?: LaunchProject[];
  pagination?: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
  summary?: LaunchSummary;
  quickSummary?: Pick<LaunchSummary, 'total' | 'inProgress' | 'blocked' | 'completed' | 'overdue'>;
  facets?: LaunchFacets;
};

type LaunchDraft = {
  productName: string;
  supplierName: string;
  brand: string;
  category: string;
  erpProductCode: string;
  skuCode: string;
  spuCode: string;
  productImageUrl: string;
  proposedBy: string;
  proposedDate: string;
  owner: string;
  targetLaunchDate: string;
  lifecycleStatus: "active" | "paused" | "cancelled";
  priority: Priority;
  recommendedPriceYuan: string;
  approvedPriceYuan: string;
  estimatedGrossMarginPercent: string;
  notes: string;
  shopPlan: string;
};
type StageDraft = {
  status: StageStatus;
  owner: string;
  plannedDueDate: string;
  blocker: string;
  notes: string;
  evidenceUrl: string;
  evidenceLabel: string;
};

const STAGES: Array<{ key: StageKey; label: string }> = [
  { key: "modeling", label: "建模" },
  { key: "pricing", label: "分析定价" },
  { key: "image", label: "图片" },
  { key: "video", label: "视频" },
  { key: "listing", label: "上架" },
  { key: "stocking", label: "备货" },
  { key: "review", label: "上新复盘" },
];
const STAGE_STATUS_OPTIONS: Array<{ value: StageStatus; label: string }> = [
  { value: "not_started", label: "未开始" },
  { value: "in_progress", label: "进行中" },
  { value: "blocked", label: "受阻" },
  { value: "completed", label: "已完成" },
  { value: "not_applicable", label: "不适用" },
];
const STATUS_ONLY_STAGE_KEYS = new Set<StageKey>(["modeling", "pricing", "image", "video", "stocking"]);
const PROJECT_STATUS_OPTIONS: Array<{ value: ProjectStatus; label: string }> = [
  { value: "not_started", label: "未开始" },
  { value: "in_progress", label: "进行中" },
  { value: "blocked", label: "受阻" },
  { value: "paused", label: "已暂停" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已取消" },
];
const PAGE_SIZE = 50;

function shanghaiToday() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const read = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${read("year")}-${read("month")}-${read("day")}`;
}

function datePlus(value: string, days: number) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days)).toISOString().slice(0, 10);
}

function emptyDraft(): LaunchDraft {
  const today = shanghaiToday();
  return {
    productName: "", supplierName: "", brand: "", category: "", erpProductCode: "", skuCode: "", spuCode: "",
    productImageUrl: "", proposedBy: "", proposedDate: today, owner: "", targetLaunchDate: datePlus(today, 14),
    lifecycleStatus: "active", priority: "normal", recommendedPriceYuan: "", approvedPriceYuan: "",
    estimatedGrossMarginPercent: "", notes: "", shopPlan: "",
  };
}

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

function priorityLabel(value: Priority) {
  return value === "high" ? "紧急" : value === "low" ? "低" : "普通";
}

function projectStatusLabel(value: ProjectStatus) {
  return PROJECT_STATUS_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function stageStatusLabel(value: StageStatus) {
  return STAGE_STATUS_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function sourceLabel(value: LaunchProject["source"]) {
  return value === "manual" ? "手工录入" : value === "system" ? "系统生成" : value === "import" ? "批量导入" : "系统集成";
}

function centsToYuan(value: number | null) {
  return value === null ? "" : (value / 100).toFixed(2).replace(/\.00$/, "");
}

function decimalToScaledInteger(value: string, scale: number, label: string, maximum: number): number | null {
  const text = value.trim();
  if (!text) return null;
  const decimals = Math.round(Math.log10(scale));
  const match = new RegExp(`^(0|[1-9]\\d*)(?:\\.(\\d{1,${decimals}}))?$`).exec(text);
  if (!match) throw new Error(`${label}格式无效，最多保留 ${decimals} 位小数。`);
  const result = Number(match[1]) * scale + Number((match[2] ?? "").padEnd(decimals, "0"));
  if (!Number.isSafeInteger(result) || result > maximum) throw new Error(`${label}超出允许范围。`);
  return result;
}

export function validateNewProductDraft(draft: LaunchDraft): string {
  if (!draft.productName.trim()) return "请填写商品名称。";
  if (!draft.proposedDate) return "请选择提出日期。";
  if (draft.targetLaunchDate && draft.targetLaunchDate < draft.proposedDate) return "目标上架日期不能早于提出日期。";
  try {
    decimalToScaledInteger(draft.recommendedPriceYuan, 100, "建议售价", 10_000_000_000_000);
    decimalToScaledInteger(draft.approvedPriceYuan, 100, "核准售价", 10_000_000_000_000);
    decimalToScaledInteger(draft.estimatedGrossMarginPercent, 100, "预估毛利率", 10_000);
  } catch (error) {
    return messageOf(error, "价格或毛利率格式无效。");
  }
  return "";
}

export function validateStageDraft(draft: StageDraft): string {
  if (draft.evidenceUrl && !/^https?:\/\//i.test(draft.evidenceUrl)) return "证据链接必须以 http:// 或 https:// 开头。";
  return "";
}

function projectDraft(project: LaunchProject): LaunchDraft {
  return {
    productName: project.productName, supplierName: project.supplierName, brand: project.brand, category: project.category,
    erpProductCode: project.erpProductCode, skuCode: project.skuCode, spuCode: project.spuCode,
    productImageUrl: project.productImageUrl, proposedBy: project.proposedBy, proposedDate: project.proposedDate,
    owner: project.owner, targetLaunchDate: project.targetLaunchDate ?? "", lifecycleStatus: project.lifecycleStatus,
    priority: project.priority, recommendedPriceYuan: centsToYuan(project.recommendedPriceCents),
    approvedPriceYuan: centsToYuan(project.approvedPriceCents),
    estimatedGrossMarginPercent: project.estimatedGrossMarginBps === null ? "" : (project.estimatedGrossMarginBps / 100).toFixed(2).replace(/\.00$/, ""),
    notes: project.notes, shopPlan: shopPlanText(project),
  };
}

function stageDraft(stage: LaunchStage): StageDraft {
  return {
    status: stage.status, owner: stage.owner, plannedDueDate: stage.plannedDueDate ?? "", blocker: stage.blocker,
    notes: stage.notes, evidenceUrl: stage.evidenceUrl, evidenceLabel: stage.evidenceLabel,
  };
}

function StatusBadge({ status }: { status: ProjectStatus }) {
  const tone = status === "completed" ? "status-success" : status === "in_progress" ? "status-info" : status === "blocked" ? "status-danger" : status === "paused" || status === "cancelled" ? "status-gray" : "status-warning";
  return <span className={`status ${tone}`}>{projectStatusLabel(status)}</span>;
}

function StageBadge({ stage, compact = false }: { stage: LaunchStage; compact?: boolean }) {
  return <span className={`launch-stage-badge stage-${stage.status}`} title={`${stage.label}：${stageStatusLabel(stage.status)}${stage.owner ? ` · ${stage.owner}` : ""}${stage.blocker ? ` · ${stage.blocker}` : ""}`}>
    <i />{compact ? stageStatusLabel(stage.status).slice(0, 2) : stageStatusLabel(stage.status)}
  </span>;
}

function LoadingState() {
  return <section className="panel data-state operations-data-state" role="status"><span className="state-spinner" /><strong>正在读取新品项目</strong><p>同步项目、目标店铺与阶段状态…</p></section>;
}

function shopPlanText(project: LaunchProject): string {
  return project.shopPlan ?? project.targets.map((target) => `${target.platform} · ${target.shopName}`).join("\n");
}

function ProjectTextEditor({ project, field, saving, onClose, onSave }: {
  project: LaunchProject; field: "shopPlan" | "notes"; saving: boolean;
  onClose: () => void; onSave: (value: string) => Promise<void>;
}) {
  const initial = field === "shopPlan" ? shopPlanText(project) : project.notes;
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");
  const label = field === "shopPlan" ? "店铺规划" : "备注";
  return <Dialog open onClose={() => !saving && onClose()} dialogId="launch-project-text-editor" ariaLabel={`编辑${label}`} className="workflow-edit-modal launch-text-modal">
    <h2>编辑{label}</h2><p>{project.productName}</p>
    <form className="workflow-edit-form" onSubmit={async (event) => {
      event.preventDefault(); setError("");
      if (value === initial) { onClose(); return; }
      try { await onSave(value); } catch (reason) { setError(messageOf(reason, `${label}保存失败`)); }
    }}>
      <label className="workflow-edit-content-field"><span>{label}</span><textarea autoFocus rows={7} maxLength={4000} value={value} onChange={(event) => setValue(event.target.value)} /></label>
      {error && <p role="alert">{error}</p>}
      <div className="workflow-edit-actions"><button type="button" className="secondary-button" disabled={saving} onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : `保存${label}`}</button></div>
    </form>
  </Dialog>;
}

function ProjectEditor({ project, facets, saving, onClose, onSave }: {
  project: LaunchProject | null;
  facets: LaunchFacets;
  saving: boolean;
  onClose: () => void;
  onSave: (draft: LaunchDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<LaunchDraft>(() => project ? projectDraft(project) : emptyDraft());
  const [error, setError] = useState("");
  const submit = async () => {
    const validation = validateNewProductDraft(draft);
    if (validation) { setError(validation); return; }
    setError("");
    try { await onSave(draft); } catch (reason) { setError(messageOf(reason, "新品项目保存失败，请稍后重试。")); }
  };
  return <Dialog open onClose={() => !saving && onClose()} dialogId="new-product-project-editor" ariaLabel={project ? "编辑新品项目" : "新建新品项目"} className="workflow-edit-modal launch-project-modal">
    <button type="button" className="workflow-modal-close" aria-label="关闭新品项目编辑" disabled={saving} onClick={onClose}>×</button>
    <span className="eyebrow">NEW PRODUCT PROJECT</span><h2>{project ? "编辑新品项目" : "新建新品项目"}</h2><p className="launch-modal-intro">先建立商品与店铺规划，再在阶段矩阵中维护各节点状态。</p>
    <form className="workflow-edit-form launch-project-form" noValidate onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label className="workflow-edit-title-field"><span>商品名称（必填）</span><input autoFocus required maxLength={200} value={draft.productName} onChange={(event) => { setError(""); setDraft((current) => ({ ...current, productName: event.target.value })); }} /></label>
      <label><span>供应商</span><input list="launch-supplier-options" maxLength={200} value={draft.supplierName} onChange={(event) => setDraft((current) => ({ ...current, supplierName: event.target.value }))} /></label>
      <label><span>品牌</span><input maxLength={120} value={draft.brand} onChange={(event) => setDraft((current) => ({ ...current, brand: event.target.value }))} /></label>
      <label><span>品类</span><input list="launch-category-options" maxLength={120} value={draft.category} onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))} /></label>
      <label><span>ERP 货品编码</span><input maxLength={160} value={draft.erpProductCode} onChange={(event) => setDraft((current) => ({ ...current, erpProductCode: event.target.value }))} /></label>
      <label><span>SKU 编码</span><input maxLength={160} value={draft.skuCode} onChange={(event) => setDraft((current) => ({ ...current, skuCode: event.target.value }))} /></label>
      <label><span>SPU 编码</span><input maxLength={160} value={draft.spuCode} onChange={(event) => setDraft((current) => ({ ...current, spuCode: event.target.value }))} /></label>
      <label><span>商品图片链接</span><input type="url" maxLength={1000} value={draft.productImageUrl} placeholder="https://…" onChange={(event) => setDraft((current) => ({ ...current, productImageUrl: event.target.value }))} /></label>
      <label><span>提出人</span><input maxLength={120} value={draft.proposedBy} onChange={(event) => setDraft((current) => ({ ...current, proposedBy: event.target.value }))} /></label>
      <label><span>提出日期（必填）</span><input required type="date" max={draft.targetLaunchDate || undefined} value={draft.proposedDate} onChange={(event) => { setError(""); setDraft((current) => ({ ...current, proposedDate: event.target.value })); }} /></label>
      <label><span>项目负责人</span><input list="launch-owner-options" maxLength={120} value={draft.owner} onChange={(event) => setDraft((current) => ({ ...current, owner: event.target.value }))} /></label>
      <label><span>目标上架日期</span><input type="date" min={draft.proposedDate || undefined} value={draft.targetLaunchDate} onChange={(event) => { setError(""); setDraft((current) => ({ ...current, targetLaunchDate: event.target.value })); }} /></label>
      <label><span>优先级</span><select value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value as Priority }))}><option value="high">紧急</option><option value="normal">普通</option><option value="low">低</option></select></label>
      <label><span>项目状态</span><select value={draft.lifecycleStatus} onChange={(event) => setDraft((current) => ({ ...current, lifecycleStatus: event.target.value as LaunchDraft["lifecycleStatus"] }))}><option value="active">正常推进</option><option value="paused">暂停</option><option value="cancelled">取消</option></select></label>
      <label><span>建议售价（元）</span><input inputMode="decimal" value={draft.recommendedPriceYuan} onChange={(event) => setDraft((current) => ({ ...current, recommendedPriceYuan: event.target.value }))} /></label>
      <label><span>核准售价（元）</span><input inputMode="decimal" value={draft.approvedPriceYuan} onChange={(event) => setDraft((current) => ({ ...current, approvedPriceYuan: event.target.value }))} /></label>
      <label><span>预估毛利率（%）</span><input inputMode="decimal" value={draft.estimatedGrossMarginPercent} onChange={(event) => setDraft((current) => ({ ...current, estimatedGrossMarginPercent: event.target.value }))} /></label>
      <label className="workflow-edit-content-field"><span>工作状态备注</span><textarea rows={3} maxLength={4000} value={draft.notes} onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))} /></label>
      <label className="workflow-edit-content-field"><span>店铺规划</span><textarea rows={4} maxLength={4000} value={draft.shopPlan} onChange={(event) => setDraft((current) => ({ ...current, shopPlan: event.target.value }))} /></label>
      <datalist id="launch-supplier-options">{facets.suppliers.map((value) => <option key={value} value={value} />)}</datalist>
      <datalist id="launch-category-options">{facets.categories.map((value) => <option key={value} value={value} />)}</datalist>
      <datalist id="launch-owner-options">{facets.owners.map((value) => <option key={value} value={value} />)}</datalist>
      <datalist id="launch-platform-options">{facets.platforms.map((value) => <option key={value} value={value} />)}</datalist>
      <datalist id="launch-shop-options">{facets.shopNames.map((value) => <option key={value} value={value} />)}</datalist>
      {error && <p className="workflow-edit-validation" role="alert">{error}</p>}
      <div className="workflow-modal-actions workflow-edit-actions"><button type="button" className="secondary-button" disabled={saving} onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : project ? "保存项目" : "创建并生成阶段"}</button></div>
    </form>
  </Dialog>;
}

function StageEditor({ project, stage, saving, onClose, onSave }: {
  project: LaunchProject;
  stage: LaunchStage;
  saving: boolean;
  onClose: () => void;
  onSave: (draft: StageDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<StageDraft>(() => stageDraft(stage));
  const [error, setError] = useState("");
  const statusOnly = STATUS_ONLY_STAGE_KEYS.has(stage.stageKey);
  const submit = async () => {
    const validation = validateStageDraft(draft);
    if (validation) { setError(validation); return; }
    setError("");
    try { await onSave(draft); } catch (reason) { setError(messageOf(reason, "阶段保存失败，请稍后重试。")); }
  };
  return <Dialog open onClose={() => !saving && onClose()} dialogId="new-product-stage-editor" ariaLabel={`编辑${stage.label}阶段`} className="workflow-edit-modal launch-stage-modal">
    <button type="button" className="workflow-modal-close" aria-label="关闭阶段编辑" disabled={saving} onClick={onClose}>×</button>
    <span className="eyebrow">STAGE DELIVERY</span><h2>{stage.label} · {project.productName}</h2>
    {statusOnly && <p className="launch-modal-intro">此节点只需选择当前状态，其他资料无需填写。</p>}
    <form className="workflow-edit-form" noValidate onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label className={statusOnly ? "workflow-edit-content-field" : undefined}><span>状态</span><select autoFocus value={draft.status} onChange={(event) => { setError(""); setDraft((current) => ({ ...current, status: event.target.value as StageStatus })); }}>{STAGE_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      {!statusOnly && <>
        <label><span>负责人（选填）</span><input maxLength={120} value={draft.owner} onChange={(event) => setDraft((current) => ({ ...current, owner: event.target.value }))} /></label>
        <label><span>计划截止日（选填）</span><input type="date" value={draft.plannedDueDate} onChange={(event) => setDraft((current) => ({ ...current, plannedDueDate: event.target.value }))} /></label>
        <label className="workflow-edit-content-field"><span>阻塞原因（选填）</span><textarea rows={2} maxLength={500} value={draft.blocker} onChange={(event) => { setError(""); setDraft((current) => ({ ...current, blocker: event.target.value })); }} /></label>
        <label className="workflow-edit-content-field"><span>节点说明 / 交付结果（选填）</span><textarea rows={4} maxLength={2000} value={draft.notes} onChange={(event) => setDraft((current) => ({ ...current, notes: event.target.value }))} /></label>
        <label><span>证据名称（选填）</span><input maxLength={160} value={draft.evidenceLabel} placeholder="例如：主图定稿" onChange={(event) => setDraft((current) => ({ ...current, evidenceLabel: event.target.value }))} /></label>
        <label><span>证据链接（选填）</span><input type="url" maxLength={1000} value={draft.evidenceUrl} placeholder="https://…" onChange={(event) => { setError(""); setDraft((current) => ({ ...current, evidenceUrl: event.target.value })); }} /></label>
      </>}
      {error && <p className="workflow-edit-validation" role="alert">{error}</p>}
      <div className="workflow-modal-actions workflow-edit-actions"><button type="button" className="secondary-button" disabled={saving} onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : "保存阶段"}</button></div>
    </form>
  </Dialog>;
}

function ProjectDetail({ project, canWrite, saving, onClose, onEdit, onEditStage }: {
  project: LaunchProject;
  canWrite: boolean;
  saving: boolean;
  onClose: () => void;
  onEdit: () => void;
  onEditStage: (stage: LaunchStage) => void;
}) {
  return <Dialog open onClose={onClose} dialogId="new-product-project-detail" ariaLabel={`${project.productName}项目详情`} className="workflow-edit-modal launch-detail-modal">
    <button type="button" className="workflow-modal-close" aria-label="关闭项目详情" onClick={onClose}>×</button>
    <div className="launch-detail-header">{project.productImageUrl ? <img src={project.productImageUrl} alt="" /> : <span aria-hidden="true">新</span>}<div><span className="eyebrow">PROJECT DETAIL</span><h2>{project.productName}</h2><p>{project.supplierName || "供应商待补充"} · {project.category || "品类待补充"}</p></div><StatusBadge status={project.status} /></div>
    <dl className="launch-detail-facts"><div><dt>项目负责人</dt><dd>{project.owner || "未指定"}</dd></div><div><dt>提出 / 目标</dt><dd>{project.proposedDate} → {project.targetLaunchDate || "待排期"}</dd></div><div><dt>编码</dt><dd>{project.erpProductCode || project.skuCode || project.spuCode || "未填写"}</dd></div><div><dt>定价</dt><dd>{project.approvedPriceCents !== null ? `核准 ¥${(project.approvedPriceCents / 100).toFixed(2)}` : project.recommendedPriceCents !== null ? `建议 ¥${(project.recommendedPriceCents / 100).toFixed(2)}` : "待分析"}</dd></div><div><dt>来源</dt><dd>{sourceLabel(project.source)}{project.sourceRef ? ` · ${project.sourceRef}` : ""}</dd></div><div><dt>整体进度</dt><dd>{project.progressPercent}%</dd></div></dl>
    <section className="launch-detail-targets"><h3>店铺规划</h3>{project.shopPlan != null ? <p className="launch-visible-text">{project.shopPlan || "暂未填写"}</p> : <div>{project.targets.map((target) => <article key={`${target.platform}-${target.shopName}`}><strong>{target.platform} · {target.shopName}</strong><span>{target.status === "listed" ? "已上架" : target.status === "ready" ? "可上架" : target.status === "paused" ? "暂停" : "待准备"}</span><small>{target.listingSku || "SKU 待补充"}</small>{target.listingUrl && <a href={target.listingUrl} target="_blank" rel="noreferrer">打开商品链接</a>}</article>)}</div>}</section>
    <section className="launch-detail-stages"><header><h3>阶段交付</h3><span>{project.stages.some((stage) => stage.status === "blocked") ? "有阻塞节点" : project.overdueStageCount ? `${project.overdueStageCount} 个阶段已逾期` : "节点正常"}</span></header><div>{project.stages.map((stage) => <article key={stage.stageKey} className={`stage-${stage.status}`}><header><strong>{stage.label}</strong><StageBadge stage={stage} /></header><dl><div><dt>负责人</dt><dd>{stage.owner || "未指定"}</dd></div><div><dt>截止</dt><dd>{stage.plannedDueDate || "待排期"}</dd></div></dl>{stage.blocker && <p className="launch-stage-blocker">阻塞：{stage.blocker}</p>}{stage.notes && <p>{stage.notes}</p>}<footer>{stage.evidenceUrl ? <a href={stage.evidenceUrl} target="_blank" rel="noreferrer">{stage.evidenceLabel || "查看交付证据"}</a> : <span>暂无交付证据</span>}{canWrite && <button type="button" className="row-action" disabled={saving} onClick={() => onEditStage(stage)}>编辑节点</button>}</footer></article>)}</div></section>
    {project.notes && <section className="launch-detail-notes"><h3>工作状态备注</h3><p>{project.notes}</p></section>}
    {project.activity && <section className="launch-detail-activity"><h3>最近活动</h3><ol>{project.activity.map((activity) => <li key={activity.id}><i /><div><strong>{activity.action === "project.created" ? "创建项目" : activity.action === "project.deleted" ? "删除项目" : activity.action === "stage.updated" ? `更新${STAGES.find((item) => item.key === activity.stageKey)?.label ?? "阶段"}` : "更新项目"}</strong><p>{activity.actorEmail} · {formatDateTime(activity.createdAt)}</p></div></li>)}</ol></section>}
    <div className="workflow-modal-actions"><button type="button" className="secondary-button" onClick={onClose}>关闭</button>{canWrite && <button type="button" className="primary-button" onClick={onEdit}>编辑项目</button>}</div>
  </Dialog>;
}

export default function NewProductLaunchView({ canWrite }: { canWrite: boolean }) {
  const requestGeneration = useRef(0);
  const [items, setItems] = useState<LaunchProject[]>([]);
  const [summary, setSummary] = useState<LaunchSummary>({ total: 0, notStarted: 0, inProgress: 0, blocked: 0, completed: 0, paused: 0, cancelled: 0, overdue: 0, stageSummary: [] });
  const [quickSummary, setQuickSummary] = useState({ total: 0, inProgress: 0, blocked: 0, completed: 0, overdue: 0 });
  const [facets, setFacets] = useState<LaunchFacets>({ suppliers: [], owners: [], categories: [], platforms: [], shopNames: [], sources: [] });
  const [pagination, setPagination] = useState({ page: 1, pageSize: PAGE_SIZE, total: 0, returned: 0, truncated: false });
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<ProjectStatus | "">("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [supplier, setSupplier] = useState("");
  const [owner, setOwner] = useState("");
  const [stageKey, setStageKey] = useState<StageKey | "">("");
  const [stageStatus, setStageStatus] = useState<StageStatus | "">("");
  const [proposedFrom, setProposedFrom] = useState("");
  const [dueTo, setDueTo] = useState("");
  const [view, setView] = useState<"matrix" | "kanban">("matrix");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [editor, setEditor] = useState<LaunchProject | "create" | null>(null);
  const [textEditor, setTextEditor] = useState<{ project: LaunchProject; field: "shopPlan" | "notes" } | null>(null);
  const [detail, setDetail] = useState<LaunchProject | null>(null);
  const [stageEditor, setStageEditor] = useState<{ project: LaunchProject; stage: LaunchStage } | null>(null);
  useAiPageDetails("workflow", {
    period: null,
    filters: { dataset: "workflow_launch_projects", query: query.trim(), status, suppliers: supplier ? [supplier] : [], owner, stageKey, stageStatus, proposedFrom, dueTo: dueTo ? datePlus(dueTo, 1) : "", selectedIds: detail ? [detail.id] : [] },
  });


  const buildParams = useCallback((page: number) => {
    const params = new URLSearchParams({ page: String(page), pageSize: String(PAGE_SIZE) });
    if (query.trim()) params.set("q", query.trim());
    if (status) params.append("status", status);
    if (overdueOnly) params.set("overdue", "true");
    if (supplier) params.append("supplier", supplier);
    if (owner) params.append("owner", owner);
    if (stageKey) params.set("stage", stageKey);
    if (stageStatus) params.append("stageStatus", stageStatus);
    if (proposedFrom) params.set("proposedFrom", proposedFrom);
    if (dueTo) params.set("dueTo", datePlus(dueTo, 1));
    return params;
  }, [dueTo, owner, proposedFrom, query, stageKey, stageStatus, status, supplier, overdueOnly]);

  const load = useCallback(async (signal?: AbortSignal, page = 1, append = false) => {
    const generation = ++requestGeneration.current;
    if (append) setLoadingMore(true); else setLoading(true);
    setError("");
    try {
      const payload = await requestJson<LaunchPayload>(`/api/workflow/launch-projects?${buildParams(page)}`, { signal });
      if (generation !== requestGeneration.current) return;
      const next = Array.isArray(payload.items) ? payload.items : [];
      setItems((current) => append ? Array.from(new Map([...current, ...next].map((item) => [item.id, item])).values()) : next);
      if (payload.summary) setSummary(payload.summary);
      if (payload.quickSummary) setQuickSummary(payload.quickSummary);
      if (payload.facets) setFacets((current) => ({
        suppliers: Array.from(new Set([...current.suppliers, ...payload.facets!.suppliers])).sort((left, right) => left.localeCompare(right, "zh-CN")),
        owners: Array.from(new Set([...current.owners, ...payload.facets!.owners])).sort((left, right) => left.localeCompare(right, "zh-CN")),
        categories: Array.from(new Set([...current.categories, ...payload.facets!.categories])).sort((left, right) => left.localeCompare(right, "zh-CN")),
        platforms: Array.from(new Set([...current.platforms, ...payload.facets!.platforms])).sort((left, right) => left.localeCompare(right, "zh-CN")),
        shopNames: Array.from(new Set([...current.shopNames, ...payload.facets!.shopNames])).sort((left, right) => left.localeCompare(right, "zh-CN")),
        sources: Array.from(new Set([...current.sources, ...payload.facets!.sources])).sort(),
      }));
      setPagination(payload.pagination ?? { page, pageSize: PAGE_SIZE, total: next.length, returned: next.length, truncated: false });
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (generation !== requestGeneration.current) return;
      setError(messageOf(reason, "新品项目读取失败"));
    } finally {
      if (generation === requestGeneration.current) { setLoading(false); setLoadingMore(false); }
    }
  }, [buildParams]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), query.trim() ? 250 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [load, query]);

  const refreshProject = async (projectId: string) => {
    const payload = await requestJson<{ item: LaunchProject }>(`/api/workflow/launch-projects/${encodeURIComponent(projectId)}`);
    setItems((current) => current.map((item) => item.id === projectId ? payload.item : item));
    setDetail((current) => current?.id === projectId ? payload.item : current);
    return payload.item;
  };

  const saveText = async (value: string) => {
    if (!textEditor) return;
    const { project, field } = textEditor;
    setSaving(true);
    try {
      await requestJson(`/api/workflow/launch-projects/${encodeURIComponent(project.id)}`, {
        method: "PATCH", body: { expectedVersion: project.version, [field]: value },
      });
      setTextEditor(null);
      setFeedback(`${field === "shopPlan" ? "店铺规划" : "备注"}已保存。`);
      await load(undefined, 1, false);
    } finally { setSaving(false); }
  };

  const saveProject = async (draft: LaunchDraft) => {
    const payload = {
      productName: draft.productName, supplierName: draft.supplierName, brand: draft.brand, category: draft.category,
      erpProductCode: draft.erpProductCode, skuCode: draft.skuCode, spuCode: draft.spuCode,
      productImageUrl: draft.productImageUrl, proposedBy: draft.proposedBy, proposedDate: draft.proposedDate,
      owner: draft.owner, targetLaunchDate: draft.targetLaunchDate || null, lifecycleStatus: draft.lifecycleStatus,
      priority: draft.priority,
      recommendedPriceCents: decimalToScaledInteger(draft.recommendedPriceYuan, 100, "建议售价", 10_000_000_000_000),
      approvedPriceCents: decimalToScaledInteger(draft.approvedPriceYuan, 100, "核准售价", 10_000_000_000_000),
      estimatedGrossMarginBps: decimalToScaledInteger(draft.estimatedGrossMarginPercent, 100, "预估毛利率", 10_000),
      notes: draft.notes,
      shopPlan: draft.shopPlan,
      ...((editor && editor !== "create") ? { expectedVersion: editor.version } : { source: "manual" }),
    };
    setSaving(true);
    try {
      if (editor && editor !== "create") {
        await requestJson(`/api/workflow/launch-projects/${encodeURIComponent(editor.id)}`, { method: "PATCH", body: payload });
        setFeedback(`“${draft.productName}”项目资料已更新。`);
      } else {
        await requestJson("/api/workflow/launch-projects", { method: "POST", body: payload });
        setFeedback(`“${draft.productName}”已创建，并生成 7 个标准阶段。`);
      }
      setEditor(null);
      await load(undefined, 1, false);
    } finally { setSaving(false); }
  };

  const saveStage = async (draft: StageDraft) => {
    if (!stageEditor) return;
    setSaving(true);
    try {
      const { project, stage } = stageEditor;
      const body = STATUS_ONLY_STAGE_KEYS.has(stage.stageKey)
        ? { status: draft.status, expectedVersion: stage.version }
        : { ...draft, plannedDueDate: draft.plannedDueDate || null, expectedVersion: stage.version };
      await requestJson(`/api/workflow/launch-projects/${encodeURIComponent(project.id)}/stages/${stage.stageKey}`, {
        method: "PATCH",
        body,
      });
      const updated = await refreshProject(project.id);
      setFeedback(`${updated.productName} · ${stage.label}已更新。`);
      setStageEditor(null);
      await load(undefined, 1, false);
    } finally { setSaving(false); }
  };

  const openDetail = async (project: LaunchProject) => {
    setDetail(project);
    try { await refreshProject(project.id); } catch (reason) { setFeedback(messageOf(reason, "项目活动读取失败")); }
  };

  const deleteProject = async (project: LaunchProject) => {
    if (!window.confirm(`确认删除新品项目“${project.productName}”？`)) return;
    setSaving(true);
    try {
      await requestJson(`/api/workflow/launch-projects/${encodeURIComponent(project.id)}?expectedVersion=${project.version}`, { method: "DELETE" });
      setFeedback(`“${project.productName}”已删除。`);
      setDetail(null);
      await load(undefined, 1, false);
    } catch (reason) { setFeedback(messageOf(reason, "新品项目删除失败")); }
    finally { setSaving(false); }
  };

  const stageMax = Math.max(1, ...summary.stageSummary.map((item) => Object.values(item).filter((value): value is number => typeof value === "number").reduce((total, value) => total + value, 0)));
  const kanban = useMemo(() => PROJECT_STATUS_OPTIONS.map((column) => ({ ...column, items: items.filter((item) => item.status === column.value) })), [items]);

  if (loading && items.length === 0) return <LoadingState />;
  return <div className="launch-workspace">
    <section className="workflow-toolbar workflow-section-hero launch-hero"><div><span className="eyebrow">NEW PRODUCT PIPELINE</span><h2>新品上新</h2><p>从提出到复盘统一管理商品、店铺规划、负责人、工作状态备注与节点状态。</p></div><div className="workflow-hero-actions"><span>{canWrite ? "可协作编辑" : "只读访问"}</span><button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>刷新</button><button type="button" className="primary-button" disabled={!canWrite} onClick={() => setEditor("create")}>{canWrite ? "＋ 新建新品项目" : "仅查看"}</button></div></section>
    {feedback && <div className="workflow-feedback" role="status"><span>i</span><p>{feedback}</p><button type="button" aria-label="关闭新品提示" onClick={() => setFeedback("")}>×</button></div>}
    <section className="panel launch-controls"><div><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索商品、供应商、编码、店铺或负责人" aria-label="搜索新品项目" /><select value={supplier} onChange={(event) => setSupplier(event.target.value)} aria-label="供应商筛选"><option value="">全部供应商</option>{facets.suppliers.map((value) => <option key={value}>{value}</option>)}</select><select value={owner} onChange={(event) => setOwner(event.target.value)} aria-label="负责人筛选"><option value="">全部负责人</option>{facets.owners.map((value) => <option key={value}>{value}</option>)}</select><select value={status} onChange={(event) => { setStatus(event.target.value as ProjectStatus | ""); setOverdueOnly(false); }} aria-label="整体状态筛选"><option value="">全部状态</option>{PROJECT_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><select value={stageKey} onChange={(event) => setStageKey(event.target.value as StageKey | "")} aria-label="阶段筛选"><option value="">全部阶段</option>{STAGES.map((stage) => <option key={stage.key} value={stage.key}>{stage.label}</option>)}</select><select value={stageStatus} onChange={(event) => setStageStatus(event.target.value as StageStatus | "")} aria-label="阶段状态筛选"><option value="">全部节点状态</option>{STAGE_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><label><span>提出日期起</span><input type="date" value={proposedFrom} onChange={(event) => setProposedFrom(event.target.value)} /></label><label><span>目标上架止</span><input type="date" value={dueTo} onChange={(event) => setDueTo(event.target.value)} /></label><button type="button" className="row-action" onClick={() => { setQuery(""); setStatus(""); setOverdueOnly(false); setSupplier(""); setOwner(""); setStageKey(""); setStageStatus(""); setProposedFrom(""); setDueTo(""); }}>清除筛选</button></div><div className="workflow-view-switch" role="radiogroup" aria-label="新品项目视图"><button type="button" role="radio" aria-checked={view === "matrix"} className={view === "matrix" ? "active" : ""} onClick={() => setView("matrix")}>☷ 阶段矩阵</button><button type="button" role="radio" aria-checked={view === "kanban"} className={view === "kanban" ? "active" : ""} onClick={() => setView("kanban")}>▥ 看板</button></div></section>
    <section className="workflow-summary-grid launch-summary-grid" aria-label="新品项目快捷筛选">
      {([
        { key: "in_progress", label: "进行中", count: quickSummary.inProgress, note: "正在推进", tone: "blue" },
        { key: "blocked", label: "受阻", count: quickSummary.blocked, note: "需优先排障", tone: "red" },
        { key: "overdue", label: "阶段逾期", count: quickSummary.overdue, note: "存在逾期阶段", tone: "orange" },
        { key: "completed", label: "已完成", count: quickSummary.completed, note: "已含上新复盘", tone: "green" },
        { key: "", label: "全部项目", count: quickSummary.total, note: "全部状态", tone: "slate" },
      ] as const).map((item) => { const selected = item.key === "overdue" ? overdueOnly : !overdueOnly && status === item.key; return <button type="button" key={item.key} aria-pressed={selected} className={`tone-${item.tone}${selected ? " active" : ""}`} onClick={() => { setOverdueOnly(!selected && item.key === "overdue"); setStatus(selected || item.key === "overdue" ? "" : item.key); }}><span>{item.label}</span><strong>{item.count}</strong><small>{item.note}</small></button>; })}
    </section>
    <section className="panel launch-stage-overview"><header><div><h3>各阶段推进</h3><p>展示当前筛选下每个节点的完成、推进与阻塞数量。</p></div><span>{summary.total} 个项目</span></header><div>{summary.stageSummary.map((stage) => { const total = STAGE_STATUS_OPTIONS.reduce((sum, option) => sum + Number(stage[option.value] ?? 0), 0); return <article key={stage.stageKey}><header><strong>{stage.label}</strong><span>{stage.completed}/{total}</span></header><i><b className="completed" style={{ width: `${Number(stage.completed ?? 0) / stageMax * 100}%` }} /><b className="active" style={{ width: `${Number(stage.in_progress ?? 0) / stageMax * 100}%` }} /><b className="blocked" style={{ width: `${Number(stage.blocked ?? 0) / stageMax * 100}%` }} /></i><small>完成 {stage.completed} · 进行 {stage.in_progress} · 受阻 {stage.blocked}</small></article>; })}</div></section>
    {error && <section className="panel data-state operations-data-state operations-data-state-error" role="alert"><span className="state-symbol">!</span><strong>新品项目加载失败</strong><p>{error}</p><button type="button" className="secondary-button" onClick={() => void load()}>重新加载</button></section>}
    {loading && items.length === 0 ? <LoadingState /> : view === "matrix" ? <section className="panel launch-matrix-panel data-refresh-region" aria-busy={loading}>
      <header className="workflow-list-heading"><div><h3>新品阶段矩阵</h3><p>店铺规划可自由填写，备注直接展示；建模、分析定价、图片、视频和备货只需选择状态。</p></div><span>已加载 {items.length} / {pagination.total}</span></header>
      <div className="data-table-wrap"><table className="data-table launch-matrix-table"><thead><tr><th>商品 / 供应商</th><th data-column-filter-disabled="true">店铺规划</th><th>提出 / 上架</th><th>负责人</th>{STAGES.map((stage) => <th key={stage.key}>{stage.label}</th>)}<th>整体</th><th>备注</th><th>操作</th></tr></thead><tbody>
        {items.map((project) => <tr key={project.id}>
          <td><div className="launch-product-cell">{project.productImageUrl ? <img src={project.productImageUrl} alt="" /> : <span>新</span>}<div><strong>{project.productName}</strong><small>{project.supplierName || "供应商待补充"}</small><em>{project.erpProductCode || project.skuCode || project.category || "编码待补充"}</em></div></div></td>
          <td><button type="button" className="launch-planning-cell" aria-label={`编辑店铺规划：${project.productName}`} disabled={!canWrite || saving} onClick={() => setTextEditor({ project, field: "shopPlan" })}><span className="launch-visible-text">{shopPlanText(project)}</span><small>{canWrite ? "点击填写店铺规划" : "店铺规划"}</small></button></td>
          <td><time>{project.proposedDate}</time><small>目标 {project.targetLaunchDate || "待排期"}</small></td>
          <td><span className="launch-owner-cell">{project.owner || "未指定"}</span></td>
          {STAGES.map((definition) => { const stage = project.stages.find((item) => item.stageKey === definition.key); const statusOnly = STATUS_ONLY_STAGE_KEYS.has(definition.key); return <td key={definition.key}>{stage ? <button type="button" className="launch-stage-cell" disabled={!canWrite} onClick={() => canWrite && setStageEditor({ project, stage })}><StageBadge stage={stage} compact />{!statusOnly && <><small>{stage.owner || "未指定"}</small><time>{stage.plannedDueDate || "待排期"}</time>{stage.evidenceUrl && <em>有证据</em>}</>}</button> : "—"}</td>; })}
          <td><div className="launch-overall-progress"><StatusBadge status={project.status} /><i><b style={{ width: `${project.progressPercent}%` }} /></i><small>{project.progressPercent}%{project.overdue ? ` · ${project.overdueStageCount} 项逾期` : ""}</small></div></td>
          <td><button type="button" className="launch-planning-cell launch-notes-cell" aria-label={`编辑备注：${project.productName}`} disabled={!canWrite || saving} onClick={() => setTextEditor({ project, field: "notes" })}><span className="launch-visible-text">{project.notes || "暂无备注"}</span><small>{canWrite ? "编辑备注" : "备注"}</small></button></td>
          <td><div className="workflow-plan-actions"><button type="button" className="row-action" onClick={() => void openDetail(project)}>详情</button><button type="button" className="row-action" disabled={!canWrite || saving} onClick={() => setEditor(project)}>编辑</button><button type="button" className="row-action danger" disabled={!canWrite || saving} onClick={() => void deleteProject(project)}>删除</button></div></td>
        </tr>)}
        {items.length === 0 && <tr><td colSpan={14}><div className="table-state">暂无符合条件的新品项目。</div></td></tr>}
      </tbody></table></div>
    </section> : <section className="launch-kanban data-refresh-region" aria-busy={loading}>{kanban.map((column) => <article className={`panel kanban-${column.value}`} key={column.value}><header><span>{column.label}</span><strong>{column.items.length}</strong></header><div>{column.items.map((project) => <button type="button" key={project.id} onClick={() => void openDetail(project)}><header><b className={`priority-${project.priority}`}>{priorityLabel(project.priority)}</b><small>{project.targetLaunchDate || "待排期"}</small></header><strong>{project.productName}</strong><p>{project.supplierName || "供应商待补充"}</p><div className="launch-mini-stages">{project.stages.map((stage) => <i key={stage.stageKey} className={`stage-${stage.status}`} title={`${stage.label}：${stageStatusLabel(stage.status)}`} />)}</div><footer><span>{project.owner || "未指定负责人"}</span><em>{project.progressPercent}%</em></footer><small className="launch-visible-text">{shopPlanText(project)}</small></button>)}{column.items.length === 0 && <p className="launch-kanban-empty">暂无项目</p>}</div></article>)}</section>}
    {pagination.truncated && <div className="operations-load-more"><button type="button" className="secondary-button" disabled={loadingMore} onClick={() => void load(undefined, pagination.page + 1, true)}>{loadingMore ? "加载中…" : `继续加载（${items.length} / ${pagination.total}）`}</button></div>}
    {textEditor && <ProjectTextEditor key={`${textEditor.project.id}-${textEditor.field}-${textEditor.project.version}`} project={textEditor.project} field={textEditor.field} saving={saving} onClose={() => setTextEditor(null)} onSave={saveText} />}
    {editor && <ProjectEditor key={editor === "create" ? "create" : `${editor.id}-${editor.version}`} project={editor === "create" ? null : editor} facets={facets} saving={saving} onClose={() => setEditor(null)} onSave={saveProject} />}
    {stageEditor && <StageEditor key={`${stageEditor.project.id}-${stageEditor.stage.stageKey}-${stageEditor.stage.version}`} project={stageEditor.project} stage={stageEditor.stage} saving={saving} onClose={() => setStageEditor(null)} onSave={saveStage} />}
    {detail && !stageEditor && <ProjectDetail project={detail} canWrite={canWrite} saving={saving} onClose={() => setDetail(null)} onEdit={() => { setEditor(detail); setDetail(null); }} onEditStage={(stage) => setStageEditor({ project: detail, stage })} />}
  </div>;
}
