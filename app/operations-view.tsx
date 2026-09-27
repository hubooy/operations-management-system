"use client";
import { toggleSingleFilter, workCardStatuses, applyWorkCardDates, type WorkCard } from "@/lib/ui/summary-filter";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { requestJson } from "@/lib/http/api-client";
import type { ModuleViewKey } from "./shell/navigation-catalog";
import Dialog from "./ui/dialog";
import NewProductLaunchView from "./new-product-launch-view";
import NewProductSalesFollowupView from "./new-product-sales-followup-view";

type Role = "viewer" | "analyst" | "operator" | "admin";
type Status = "待开始" | "工作中" | "已完成";
type Priority = "high" | "normal" | "low";
type OperationsTab = "plan" | "inspection" | "reviews" | "launch" | "launch-followup" | "variables";
type RecordType = "inspection" | "review";

type Task = {
  id: string;
  title: string;
  workContent: string;
  category: string;
  owner: string;
  shopName: string;
  startDate: string;
  due: string;
  status: Status;
  priority: Priority;
  source: string;
  createdAt: string;
  updatedAt: string;
  version?: number;
};

type TaskComment = { id: string; content: string; createdBy: string; createdAt: string };
type TaskActivity = { id: string; action?: string; summary?: string; actorEmail?: string; createdAt: string };
type TaskReminder = { id: string; remindAt: string; note?: string; status?: string; createdAt?: string };
type TaskLink = { id: string; entityType: string; entityId: string; label: string; url?: string; createdAt?: string };
type TaskAttachment = { id: string; taskId: string; fileName: string; mimeType: string; sizeBytes: number; sha256: string; createdBy: string; createdAt: string; downloadUrl: string };
type Collaboration = { comments: TaskComment[]; activity: TaskActivity[]; reminders: TaskReminder[]; links: TaskLink[]; attachments: TaskAttachment[] };

type TaskTemplate = {
  id: string;
  name: string;
  description?: string;
  title?: string;
  workContent?: string;
  category?: string;
  owner?: string;
  shopName?: string;
  startOffsetDays?: number;
  dueOffsetDays?: number;
  priority?: Priority;
  active?: boolean;
  version?: number;
};

type OperationsRecord = {
  id: string;
  type: RecordType;
  title: string;
  status: string;
  platform: string;
  channel: string;
  shopName: string;
  owner: string;
  occurredAt: string;
  dueAt: string | null;
  content: string;
  source: "manual" | "system" | "import" | "integration";
  sourceRef: string;
  referenceCode: string;
  priority: Priority;
  version: number;
  createdAt: string;
  updatedAt: string;
};

type DraftTask = Pick<Task, "title" | "workContent" | "category" | "owner" | "shopName" | "startDate" | "due" | "priority">;
type DetailSection = "comments" | "activity" | "reminders" | "links" | "attachments";
type Pagination = { page: number; pageSize: number; total: number; returned?: number; truncated?: boolean };
type TaskSummary = {
  total?: number;
  pending?: number;
  notStarted?: number;
  active?: number;
  inProgress?: number;
  completed?: number;
  open?: number;
  overdue?: number;
  dueToday?: number;
  todayDue?: number;
  priorities?: Array<{ priority?: Priority; value?: Priority; count?: number }>;
  priorityCounts?: Partial<Record<Priority, number>>;
  owners?: Array<{ owner: string; pending?: number; active?: number; completed?: number; total?: number }>;
  ownerWorkload?: Array<{ owner: string; pending?: number; active?: number; completed?: number; total?: number }>;
};
type TaskFacets = { categories?: string[]; owners?: string[]; shopNames?: string[]; sources?: string[] };
type TaskListPayload = { items: Task[]; pagination?: Partial<Pagination>; summary?: TaskSummary; facets?: TaskFacets };
type OperationActivity = { id: string; action: string; actorEmail: string; actorRole: Role; fromVersion: number | null; toVersion: number; changedFields?: string[]; fromStatus?: string | null; toStatus?: string | null; createdAt: string };
type OperationRecordDraft = {
  title: string;
  status: string;
  platform: string;
  channel: string;
  shopName: string;
  owner: string;
  occurredAt: string;
  dueAt: string;
  content: string;
  referenceCode: string;
  priority: Priority;
};
type TaskTemplateDraft = {
  name: string;
  description: string;
  title: string;
  workContent: string;
  category: string;
  owner: string;
  shopName: string;
  startOffsetDays: number;
  dueOffsetDays: number;
  priority: Priority;
  active: boolean;
};
type TemplateRequestLifecycle = {
  generation: number;
  requestKey: string;
  controller: AbortController | null;
};
type TemplateRequest = {
  generation: number;
  requestKey: string;
  controller: AbortController;
};

const EMPTY_COLLABORATION: Collaboration = { comments: [], activity: [], reminders: [], links: [], attachments: [] };
const EMPTY_TASK: DraftTask = { title: "", workContent: "", category: "工作计划", owner: "", shopName: "", startDate: "", due: "", priority: "normal" };
const STATUS_OPTIONS: Status[] = ["待开始", "工作中", "已完成"];
const TASK_PAGE_SIZE = 50;

export function createTemplateRequestLifecycle(): TemplateRequestLifecycle {
  return { generation: 0, requestKey: "", controller: null };
}

export function beginTemplateRequest(lifecycle: TemplateRequestLifecycle, requestKey: string): TemplateRequest {
  lifecycle.controller?.abort();
  const controller = new AbortController();
  lifecycle.generation += 1;
  lifecycle.requestKey = requestKey;
  lifecycle.controller = controller;
  return { generation: lifecycle.generation, requestKey, controller };
}

export function isCurrentTemplateRequest(lifecycle: TemplateRequestLifecycle, request: TemplateRequest): boolean {
  return lifecycle.generation === request.generation
    && lifecycle.requestKey === request.requestKey
    && lifecycle.controller === request.controller;
}

export function cancelTemplateRequest(lifecycle: TemplateRequestLifecycle): void {
  lifecycle.controller?.abort();
  lifecycle.generation += 1;
  lifecycle.requestKey = "";
  lifecycle.controller = null;
}

const RECORD_META: Record<RecordType, {
  eyebrow: string;
  title: string;
  note: string;
  create: string;
  empty: string;
  statuses: string[];
  activeStatuses: string[];
  terminalStatuses: string[];
}> = {
  inspection: { eyebrow: "STORE INSPECTION", title: "巡店检查", note: "按店铺沉淀巡检结论、异常事项和后续责任人。", create: "新增巡店记录", empty: "暂无巡店记录", statuses: ["正常", "待处理", "处理中", "已关闭"], activeStatuses: ["待处理", "处理中"], terminalStatuses: ["正常", "已关闭"] },
  review: { eyebrow: "REVIEW CARE", title: "评价维护", note: "集中跟进评价内容、回复状态和处理责任人。", create: "新增评价记录", empty: "暂无评价记录", statuses: ["待回复", "处理中", "已回复", "无需回复"], activeStatuses: ["待回复", "处理中"], terminalStatuses: ["已回复", "无需回复"] },
};

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

function toShanghaiLocalInput(value: string | null | undefined) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 16);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(parsed);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

function currentShanghaiLocalInput() {
  return toShanghaiLocalInput(new Date().toISOString());
}

function toShanghaiApiDateTime(value: string) {
  if (!value) return null;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return `${value}:00+08:00`;
  return value;
}

export function validateOperationRecordDraft(draft: OperationRecordDraft): string {
  if (!draft.title.trim()) return "请填写事项名称。";
  if (!draft.shopName.trim()) return "请填写店铺；尚未确定时可填写“待确认”。";
  if (!draft.occurredAt) return "请选择发生时间。";
  if (draft.dueAt && draft.dueAt < draft.occurredAt) return "截止时间不能早于发生时间。";
  return "";
}

export function validateTaskDraft(draft: DraftTask): string {
  if (!draft.title.trim()) return "请填写工作事项。";
  if (draft.startDate && draft.due && draft.due < draft.startDate) return "截止时间不能早于开始时间。";
  return "";
}

export function validateTaskTemplateDraft(draft: TaskTemplateDraft): string {
  if (!draft.name.trim()) return "请填写模板名称。";
  if (!Number.isSafeInteger(draft.startOffsetDays) || draft.startOffsetDays < -365 || draft.startOffsetDays > 365) return "开始偏移天数必须是 -365 至 365 的整数。";
  if (!Number.isSafeInteger(draft.dueOffsetDays) || draft.dueOffsetDays < -365 || draft.dueOffsetDays > 365) return "截止偏移天数必须是 -365 至 365 的整数。";
  if (draft.dueOffsetDays < draft.startOffsetDays) return "截止偏移天数不能早于开始偏移天数。";
  return "";
}

export function shanghaiDateWithOffset(offsetDays = 0, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const read = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((item) => item.type === type)?.value ?? 0);
  const target = new Date(Date.UTC(read("year"), read("month") - 1, read("day") + offsetDays));
  return target.toISOString().slice(0, 10);
}

export function calendarDateWithOffset(value: string, offsetDays: number) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + offsetDays)).toISOString().slice(0, 10);
}

function formatRecordedAt(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

function statusLabel(status: Status) {
  return status === "待开始" ? "未开始" : status === "工作中" ? "进行中" : "已完成";
}

function priorityLabel(priority: Priority) {
  return priority === "high" ? "紧急" : priority === "low" ? "低" : "普通";
}

function csvCell(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

function downloadCsvFile(name: string, rows: string[][]) {
  const content = `\ufeff${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
  const anchor = document.createElement("a");
  anchor.href = `data:text/csv;charset=utf-8,${encodeURIComponent(content)}`;
  anchor.download = name;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

function MultiSelectFilter({ values, onChange, options, ariaLabel, allLabel }: {
  values: string[];
  onChange: (values: string[]) => void;
  options: Array<{ value: string; label: string }>;
  ariaLabel: string;
  allLabel: string;
}) {
  const [search, setSearch] = useState("");
  const visible = options.filter((option) => `${option.label} ${option.value}`.toLocaleLowerCase("zh-CN").includes(search.trim().toLocaleLowerCase("zh-CN")));
  const summary = values.length === 0 ? allLabel : values.length === 1 ? options.find((option) => option.value === values[0])?.label ?? values[0] : `已选 ${values.length}`;
  return <details className="operations-multi-filter">
    <summary aria-label={ariaLabel}><span>{summary}</span><i>⌄</i></summary>
    <div className="operations-multi-filter-menu">
      <label><span className="sr-only">搜索{ariaLabel}</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`搜索${ariaLabel}`} /></label>
      <button type="button" className="operations-filter-clear" onClick={() => onChange([])}>全部清除</button>
      <div>{visible.map((option) => <label key={option.value}><input type="checkbox" checked={values.includes(option.value)} onChange={(event) => onChange(event.target.checked ? [...values, option.value] : values.filter((value) => value !== option.value))} /><span>{option.label}</span></label>)}</div>
      {visible.length === 0 && <p>没有匹配选项</p>}
    </div>
  </details>;
}

function TaskTransitionActions({ task, disabled, onTransition }: { task: Task; disabled: boolean; onTransition: (status: Status) => void }) {
  const actions: Array<{ status: Status; label: string; primary?: boolean }> = task.status === "待开始"
    ? [{ status: "工作中", label: "标记工作中", primary: true }]
    : task.status === "工作中"
      ? [{ status: "待开始", label: "退回未开始" }, { status: "已完成", label: "标记完成", primary: true }]
      : [{ status: "待开始", label: "返还未开始" }, { status: "工作中", label: "返还进行中", primary: true }];
  return <div className="workflow-transition-actions" aria-label={`${task.title}快捷状态操作`}>{actions.map((action) => <button type="button" className={`row-action workflow-transition-button${action.primary ? " primary-row-action" : ""}`} disabled={disabled} key={action.status} onClick={() => onTransition(action.status)}>{action.label}</button>)}</div>;
}

function StatusBadge({ status }: { status: string }) {
  const tone = status === "已取消" ? "status-gray" : ["已完成", "正常", "已回复", "已关闭", "无需回复"].includes(status) ? "status-success" : ["工作中", "进行中", "处理中"].includes(status) ? "status-info" : "status-warning";
  return <span className={`status ${tone}`}>{status === "待开始" ? "未开始" : status}</span>;
}

function DataState({ kind, title, note, onRetry }: { kind: "loading" | "empty" | "error" | "permission"; title: string; note: string; onRetry?: () => void }) {
  return <section className={`panel data-state operations-data-state operations-data-state-${kind}`} role={kind === "error" ? "alert" : "status"}>
    {kind === "loading" ? <span className="state-spinner" /> : <span className="state-symbol">{kind === "error" ? "!" : kind === "permission" ? "锁" : "空"}</span>}
    <strong>{title}</strong><p>{note}</p>{onRetry && <button type="button" className="secondary-button" onClick={onRetry}>重新加载</button>}
  </section>;
}

function OperationsRecordWorkspace({ type, canWrite }: { type: RecordType; canWrite: boolean }) {
  const meta = RECORD_META[type];
  const requestGeneration = useRef(0);
  const [items, setItems] = useState<OperationsRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [recordCard, setRecordCard] = useState<"" | "active" | "terminal" | "urgent">("");
  const [pagination, setPagination] = useState<Pagination>({ page: 1, pageSize: TASK_PAGE_SIZE, total: 0 });
  const [editing, setEditing] = useState<OperationsRecord | null>(null);
  const [activityRecord, setActivityRecord] = useState<OperationsRecord | null>(null);
  useAiPageDetails("workflow", {
    period: null,
    filters: { dataset: "workflow_operations", query: query.trim(), status: recordCard === "active" ? meta.activeStatuses : recordCard === "terminal" ? meta.terminalStatuses : status, priorities: recordCard === "urgent" ? ["high"] : [], selectedIds: activityRecord ? [activityRecord.id] : [] },
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [editorError, setEditorError] = useState("");
  const [draft, setDraft] = useState<OperationRecordDraft>({ title: "", status: meta.statuses[0], platform: "", channel: "", shopName: "", owner: "", occurredAt: "", dueAt: "", content: "", referenceCode: "", priority: "normal" });

  const load = useCallback(async (signal?: AbortSignal, targetPage = 1, append = false) => {
    const generation = ++requestGeneration.current;
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ type, page: String(targetPage), pageSize: String(TASK_PAGE_SIZE) });
      if (query.trim()) params.set("query", query.trim());
      if (status) params.append("status", status);
      if (recordCard === "active") meta.activeStatuses.forEach(value => params.append("status", value));
      if (recordCard === "terminal") meta.terminalStatuses.forEach(value => params.append("status", value));
      if (recordCard === "urgent") params.append("priority", "high");
      const payload = await requestJson<{ items: OperationsRecord[]; pagination?: Partial<Pagination> }>(`/api/workflow/operations-records?${params}`, { signal });
      if (generation !== requestGeneration.current) return;
      const nextItems = Array.isArray(payload.items) ? payload.items : [];
      setItems((current) => {
        if (!append) return nextItems;
        const unique = new Map(current.map((item) => [item.id, item]));
        nextItems.forEach((item) => unique.set(item.id, item));
        return Array.from(unique.values());
      });
      setPagination({
        page: payload.pagination?.page ?? targetPage,
        pageSize: payload.pagination?.pageSize ?? TASK_PAGE_SIZE,
        total: payload.pagination?.total ?? nextItems.length,
        returned: payload.pagination?.returned ?? nextItems.length,
        truncated: payload.pagination?.truncated,
      });
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (generation !== requestGeneration.current) return;
      setError(messageOf(reason, `${meta.title}读取失败`));
    } finally {
      if (generation === requestGeneration.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [meta, query, status, type, recordCard]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), query ? 250 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [load, query]);

  const save = async () => {
    if (!canWrite || saving) return;
    const validationError = validateOperationRecordDraft(draft);
    if (validationError) {
      setEditorError(validationError);
      return;
    }
    setSaving(true); setFeedback(""); setEditorError("");
    try {
      if (editing) {
        await requestJson<{ item: OperationsRecord }>(`/api/workflow/operations-records/${encodeURIComponent(editing.id)}`, { method: "PATCH", body: { ...draft, occurredAt: toShanghaiApiDateTime(draft.occurredAt), dueAt: toShanghaiApiDateTime(draft.dueAt), expectedVersion: editing.version } });
        setFeedback("记录已保存，并写入活动日志。");
      } else {
        await requestJson<{ item: OperationsRecord }>("/api/workflow/operations-records", { method: "POST", body: { ...draft, occurredAt: toShanghaiApiDateTime(draft.occurredAt), dueAt: toShanghaiApiDateTime(draft.dueAt), type, source: "manual", sourceRef: "" } });
        setFeedback("记录已创建并持久化保存。");
      }
      setCreateOpen(false); setEditing(null);
      await load(undefined, 1, false);
    } catch (reason) { setEditorError(messageOf(reason, "记录保存失败，请稍后重试。")); }
    finally { setSaving(false); }
  };

  const openEditor = (item?: OperationsRecord) => {
    setEditing(item ?? null);
    setDraft(item ? { title: item.title, status: item.status, platform: item.platform, channel: item.channel, shopName: item.shopName, owner: item.owner, occurredAt: toShanghaiLocalInput(item.occurredAt), dueAt: toShanghaiLocalInput(item.dueAt), content: item.content, referenceCode: item.referenceCode, priority: item.priority } : { title: "", status: meta.statuses[0], platform: "", channel: "", shopName: "", owner: "", occurredAt: currentShanghaiLocalInput(), dueAt: "", content: "", referenceCode: "", priority: "normal" });
    setEditorError("");
    setFeedback("");
    setCreateOpen(true);
  };

  const loadedCount = items.length;
  const activeCount = items.filter((item) => meta.activeStatuses.includes(item.status)).length;
  const terminalCount = items.filter((item) => meta.terminalStatuses.includes(item.status)).length;
  const urgentCount = items.filter((item) => item.priority === "high").length;
  const hasMore = pagination.truncated ?? loadedCount < pagination.total;

  return <>
    <section className="workflow-toolbar workflow-section-hero"><div><span className="eyebrow">{meta.eyebrow}</span><h2>{meta.title}</h2><p>{meta.note}</p></div><button type="button" className="primary-button" disabled={!canWrite} onClick={() => openEditor()}>{canWrite ? `＋ ${meta.create}` : "仅查看"}</button></section>
    {feedback && <div className="workflow-feedback" role="status"><span>i</span><p>{feedback}</p><button type="button" aria-label="关闭提示" onClick={() => setFeedback("")}>×</button></div>}
    <section className="workflow-summary-grid operations-record-summary">
      <button type="button" className={`tone-slate${recordCard === "" && !status ? " active" : ""}`} aria-pressed={recordCard === "" && !status} onClick={() => { setRecordCard(current => toggleSingleFilter(current, "", "")); setStatus(""); }}><span>全部记录</span><strong>{pagination.total}</strong><small>符合当前服务端筛选</small></button>
      <button type="button" className={`tone-blue${recordCard === "active" && !status ? " active" : ""}`} aria-pressed={recordCard === "active" && !status} onClick={() => { setRecordCard(current => toggleSingleFilter(current, "active", "")); setStatus(""); }}><span>处理中</span><strong>{activeCount}</strong><small>当前已加载记录</small></button>
      <button type="button" className={`tone-green${recordCard === "terminal" && !status ? " active" : ""}`} aria-pressed={recordCard === "terminal" && !status} onClick={() => { setRecordCard(current => toggleSingleFilter(current, "terminal", "")); setStatus(""); }}><span>已闭环</span><strong>{terminalCount}</strong><small>当前已加载，含所有终态</small></button>
      <button type="button" className={`tone-orange${recordCard === "urgent" && !status ? " active" : ""}`} aria-pressed={recordCard === "urgent" && !status} onClick={() => { setRecordCard(current => toggleSingleFilter(current, "urgent", "")); setStatus(""); }}><span>紧急事项</span><strong>{urgentCount}</strong><small>当前已加载，优先处理</small></button>
    </section>
    <section className="panel workflow-table-panel data-refresh-region" aria-busy={loading}>
      <div className="table-toolbar"><div><h2>{meta.title}记录</h2><p>不展示演示数据；筛选、搜索与分页均由服务端执行。</p></div><div className="workflow-filter-row"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索${meta.title}记录`} aria-label={`搜索${meta.title}记录`} /><select value={status} onChange={(event) => { setStatus(event.target.value); setRecordCard(""); }} aria-label={`${meta.title}状态`}><option value="">全部状态</option>{meta.statuses.map((value) => <option key={value}>{value}</option>)}</select><button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>刷新</button></div></div>
      {error && items.length > 0 && <div className="workflow-feedback workflow-feedback-error" role="alert"><span>!</span><p>{error}，已保留当前已加载记录。</p><button type="button" aria-label="重试加载记录" onClick={() => void load()}>重试</button></div>}
      {loading && items.length === 0 ? <DataState kind="loading" title={`正在读取${meta.title}`} note="正在同步最新记录…" /> : error && items.length === 0 ? <DataState kind="error" title={`${meta.title}加载失败`} note={error} onRetry={() => void load()} /> : items.length === 0 ? <DataState kind="empty" title={query || status ? "没有符合筛选条件的记录" : meta.empty} note={query || status ? "调整搜索词或状态后重试。" : canWrite ? `点击“${meta.create}”开始沉淀真实业务记录。` : "当前账号可以查看记录，但没有新增权限。"} /> : <>
        <div className="data-table-wrap">
          <table className="data-table workflow-data-table">
            <thead><tr><th>事项</th><th>店铺 / 平台</th><th>内容与编号</th><th>责任人</th><th>发生时间</th><th>截止时间</th><th>优先级</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>{items.map((item) => <tr key={item.id}>
              <td><strong>{item.title}</strong></td>
              <td><strong>{item.shopName || "未关联店铺"}</strong><small className="operations-cell-note">{[item.platform, item.channel].filter(Boolean).join(" · ") || "未设置平台"}</small></td>
              <td><span>{item.content || "未填写内容"}</span><small className="operations-cell-note">{item.referenceCode || item.sourceRef || "无业务编号"}</small></td>
              <td>{item.owner || "未指定"}</td>
              <td>{formatDateTime(item.occurredAt)}</td>
              <td>{formatDateTime(item.dueAt)}</td>
              <td><b className={`workflow-priority priority-${item.priority}`}>{priorityLabel(item.priority)}</b></td>
              <td><StatusBadge status={item.status} /></td>
              <td><div className="workflow-plan-actions"><button type="button" className="row-action" onClick={() => setActivityRecord(item)}>活动</button><button type="button" className="row-action" disabled={!canWrite} onClick={() => openEditor(item)}>编辑</button></div></td>
            </tr>)}</tbody>
          </table>
        </div>
        {hasMore && <div className="operations-load-more"><button type="button" className="secondary-button" disabled={loadingMore} onClick={() => void load(undefined, pagination.page + 1, true)}>{loadingMore ? "加载中…" : `继续加载（${loadedCount} / ${pagination.total}）`}</button></div>}
      </>}
    </section>
    {activityRecord && <OperationActivityDialog record={activityRecord} onClose={() => setActivityRecord(null)} />}
    <Dialog open={createOpen} onClose={() => !saving && setCreateOpen(false)} dialogId={`operations-${type}-editor`} ariaLabel={editing ? `编辑${meta.title}记录` : meta.create} className="workflow-edit-modal operations-record-modal">
      <button type="button" className="workflow-modal-close" aria-label="关闭记录编辑" onClick={() => setCreateOpen(false)} disabled={saving}>×</button><span className="eyebrow">PERSISTED RECORD</span><h2>{editing ? `编辑${meta.title}记录` : meta.create}</h2><p>保存后写入服务端，刷新页面不会丢失。</p>
      <form className="workflow-edit-form" onSubmit={(event) => { event.preventDefault(); void save(); }} noValidate><label className="workflow-edit-title-field"><span>事项名称（必填）</span><input autoFocus required maxLength={200} value={draft.title} placeholder={`请输入${meta.title}事项名称`} onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, title: event.target.value })); }} /></label><label className="workflow-edit-content-field"><span>具体内容</span><textarea rows={4} maxLength={4000} value={draft.content} onChange={(event) => setDraft((current) => ({ ...current, content: event.target.value }))} /></label><label><span>店铺（必填）</span><input required maxLength={160} value={draft.shopName} placeholder="请输入店铺名称" onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, shopName: event.target.value })); }} /></label><label><span>平台</span><input maxLength={80} value={draft.platform} onChange={(event) => setDraft((current) => ({ ...current, platform: event.target.value }))} /></label><label><span>渠道</span><input maxLength={80} value={draft.channel} onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))} /></label><label><span>负责人</span><input maxLength={120} value={draft.owner} onChange={(event) => setDraft((current) => ({ ...current, owner: event.target.value }))} /></label><label><span>业务编号</span><input maxLength={160} value={draft.referenceCode} onChange={(event) => setDraft((current) => ({ ...current, referenceCode: event.target.value }))} /></label><label><span>状态</span><select value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}>{meta.statuses.map((value) => <option key={value}>{value}</option>)}</select></label><label><span>发生时间（必填）</span><input required type="datetime-local" value={draft.occurredAt} onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, occurredAt: event.target.value })); }} /></label><label><span>截止时间</span><input type="datetime-local" min={draft.occurredAt || undefined} value={draft.dueAt} onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, dueAt: event.target.value })); }} /></label><label><span>优先级</span><select value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value as Priority }))}><option value="high">紧急</option><option value="normal">普通</option><option value="low">低</option></select></label>{editorError && <p className="workflow-edit-validation" role="alert">{editorError}</p>}<div className="workflow-modal-actions workflow-edit-actions"><button type="button" className="secondary-button" onClick={() => setCreateOpen(false)} disabled={saving}>取消</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : editing ? "保存修改" : type === "review" ? "创建评价记录" : "创建巡店记录"}</button></div></form>
    </Dialog>
  </>;
}

function OperationActivityDialog({ record, onClose }: { record: OperationsRecord; onClose: () => void }) {
  const [items, setItems] = useState<OperationActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await requestJson<{ items: OperationActivity[] }>(`/api/workflow/operations-records/${encodeURIComponent(record.id)}/activity?page=1&pageSize=50`);
      setItems(Array.isArray(payload.items) ? payload.items : []);
    } catch (reason) {
      setError(messageOf(reason, "活动记录读取失败"));
    } finally {
      setLoading(false);
    }
  }, [record.id]);
  useEffect(() => { void load(); }, [load]);

  const actionLabel = (item: OperationActivity) => item.action === "created"
    ? "创建记录"
    : item.action === "deleted"
      ? "删除记录"
      : item.fromStatus !== item.toStatus
        ? `状态由“${item.fromStatus ?? "无"}”调整为“${item.toStatus ?? "无"}”`
        : "更新记录";

  return <Dialog open onClose={onClose} dialogId="operations-record-activity" ariaLabel={`${record.title}活动记录`} className="workflow-edit-modal operations-record-modal">
    <button type="button" className="workflow-modal-close" aria-label="关闭活动记录" onClick={onClose}>×</button>
    <span className="eyebrow">AUDIT ACTIVITY</span>
    <h2>{record.title} · 活动记录</h2>
    <p>展示服务端保存的版本变更与操作人，便于复核协作过程。</p>
    {loading ? <DataState kind="loading" title="正在读取活动记录" note="同步最新审计活动…" /> : error ? <DataState kind="error" title="活动记录加载失败" note={error} onRetry={() => void load()} /> : <div className="operations-activity-list">
      {items.map((item) => <article key={item.id}><i /><div><strong>{actionLabel(item)}</strong><p>{item.actorEmail || "系统"} · 版本 {item.fromVersion ?? 0} → {item.toVersion} · {formatDateTime(item.createdAt)}</p>{item.changedFields?.length ? <small>变更：{item.changedFields.join("、")}</small> : null}</div></article>)}
      {items.length === 0 && <p className="operations-empty-line">暂无活动记录。</p>}
    </div>}
  </Dialog>;
}

function TaskCollaborationDialog({ task, canWrite, onClose }: { task: Task; canWrite: boolean; onClose: () => void }) {
  const [data, setData] = useState<Collaboration>(EMPTY_COLLABORATION);
  const [loaded, setLoaded] = useState(false);
  const [section, setSection] = useState<DetailSection>("comments");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [comment, setComment] = useState("");
  const [remindAt, setRemindAt] = useState("");
  const [reminderNote, setReminderNote] = useState("");
  const [linkDraft, setLinkDraft] = useState({ entityType: "shop", entityId: "", label: "", url: "" });
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const loadGenerationRef = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    setLoading(true); setError("");
    try {
      const payload = await requestJson<Collaboration>(`/api/workflow/tasks/${encodeURIComponent(task.id)}/collaboration`, { signal: controller.signal });
      if (controller.signal.aborted || generation !== loadGenerationRef.current) return;
      setData(payload);
      setLoaded(true);
    }
    catch (reason) {
      if (controller.signal.aborted || generation !== loadGenerationRef.current || (reason instanceof DOMException && reason.name === "AbortError")) return;
      setError(messageOf(reason, "协作详情读取失败"));
    }
    finally {
      if (loadControllerRef.current === controller) loadControllerRef.current = null;
      if (generation === loadGenerationRef.current) setLoading(false);
    }
  }, [task.id]);
  useEffect(() => {
    void load();
    return () => {
      loadGenerationRef.current += 1;
      loadControllerRef.current?.abort();
      loadControllerRef.current = null;
    };
  }, [load]);

  const post = async (path: string, body: Record<string, string | number | boolean | null>) => {
    setSaving(true); setError("");
    try { await requestJson(`/api/workflow/tasks/${encodeURIComponent(task.id)}/${path}`, { method: "POST", body }); await load(); return true; }
    catch (reason) { setError(messageOf(reason, "协作信息保存失败")); return false; }
    finally { setSaving(false); }
  };
  const submitComment = async () => {
    if (!comment.trim()) { setError("请填写评论内容。"); return; }
    if (await post("comments", { content: comment })) setComment("");
  };
  const submitReminder = async () => {
    if (!remindAt) { setError("请选择提醒时间。"); return; }
    if (await post("reminders", { remindAt: toShanghaiApiDateTime(remindAt) ?? "", note: reminderNote })) {
      setRemindAt(""); setReminderNote("");
    }
  };
  const submitLink = async () => {
    if (!linkDraft.label.trim()) { setError("请填写关联对象显示名称。"); return; }
    if (!linkDraft.entityId.trim()) { setError("请填写关联对象业务 ID。"); return; }
    if (await post("links", linkDraft)) setLinkDraft({ entityType: "shop", entityId: "", label: "", url: "" });
  };
  const remove = async (path: string, id: string) => {
    setSaving(true); setError("");
    try { await requestJson(`/api/workflow/tasks/${encodeURIComponent(task.id)}/${path}?id=${encodeURIComponent(id)}`, { method: "DELETE" }); await load(); }
    catch (reason) { setError(messageOf(reason, path === "reminders" ? "取消提醒失败" : "删除失败")); }
    finally { setSaving(false); }
  };
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    if (files.length > 8) {
      setError("每次最多上传 8 个附件，未上传任何文件。");
      return;
    }
    const selected = Array.from(files);
    const oversized = selected.find((file) => file.size > 10 * 1024 * 1024);
    if (oversized) {
      setError(`“${oversized.name}”超过 10MB，未上传任何文件。`);
      return;
    }
    setSaving(true); setError("");
    let uploaded = 0;
    try {
      for (const file of selected) {
        const form = new FormData();
        form.set("file", file);
        await requestJson(`/api/workflow/tasks/${encodeURIComponent(task.id)}/attachments`, { method: "POST", body: form });
        uploaded += 1;
      }
      await load();
    } catch (reason) {
      if (uploaded > 0) await load();
      const prefix = uploaded > 0 ? `已有 ${uploaded} 个附件上传成功并已刷新；` : "";
      setError(`${prefix}${messageOf(reason, "附件上传失败")}`);
    }
    finally { setSaving(false); }
  };

  const sections: Array<[DetailSection, string, number]> = [["comments", "评论", data.comments.length], ["activity", "活动", data.activity.length], ["reminders", "提醒", data.reminders.length], ["links", "关联对象", data.links.length], ["attachments", "附件", data.attachments.length]];
  const selectTab = (index: number) => {
    const normalized = (index + sections.length) % sections.length;
    setSection(sections[normalized][0]);
    tabRefs.current[normalized]?.focus();
  };
  const reminderStatus = (value?: string) => value === "dismissed" ? "已取消" : value === "sent" ? "已发送" : "待提醒";

  return <Dialog open onClose={() => !saving && onClose()} dialogId="task-collaboration-dialog" ariaLabel={`${task.title}协作详情`} className="workflow-edit-modal operations-collaboration-modal">
    <button type="button" className="workflow-modal-close" aria-label="关闭协作详情" disabled={saving} onClick={onClose}>×</button><span className="eyebrow">TASK COLLABORATION</span><h2>{task.title}</h2><p>{task.workContent}</p>
    <div className="operations-detail-meta"><StatusBadge status={statusLabel(task.status)} /><span>{task.owner || "未指定跟进人"}</span><span>{task.shopName || "未关联店铺"}</span><span>截止 {task.due}</span></div>
    <div className="operations-detail-tabs" role="tablist" aria-label="任务协作详情">{sections.map(([value, label, count], index) => <button
      type="button"
      role="tab"
      id={`task-collaboration-tab-${value}`}
      aria-controls={`task-collaboration-panel-${value}`}
      aria-selected={section === value}
      tabIndex={section === value ? 0 : -1}
      className={section === value ? "active" : ""}
      key={value}
      ref={(node) => { tabRefs.current[index] = node; }}
      onClick={() => setSection(value)}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); selectTab(index + 1); }
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); selectTab(index - 1); }
        if (event.key === "Home") { event.preventDefault(); selectTab(0); }
        if (event.key === "End") { event.preventDefault(); selectTab(sections.length - 1); }
      }}
    >{label}<small>{count}</small></button>)}</div>
    {error && loaded && <div className="workflow-feedback workflow-feedback-error" role="alert"><span>!</span><p>{error}；当前仍显示上一次成功结果。</p></div>}
    {loading && !loaded ? <DataState kind="loading" title="正在读取协作详情" note="同步评论、活动、提醒、关联对象和附件…" /> : error && !loaded ? <DataState kind="error" title="协作详情加载失败" note={error} onRetry={() => void load()} /> : <div className="operations-detail-body data-refresh-region" aria-busy={loading} role="tabpanel" id={`task-collaboration-panel-${section}`} aria-labelledby={`task-collaboration-tab-${section}`}>
      {section === "comments" && <><div className="operations-thread">{data.comments.map((item) => <article key={item.id}><header><strong>{item.createdBy}</strong><time>{formatDateTime(item.createdAt)}</time></header><p>{item.content}</p></article>)}{data.comments.length === 0 && <p className="operations-empty-line">还没有评论。</p>}</div>{canWrite && <form className="operations-inline-form" noValidate onSubmit={(event) => { event.preventDefault(); void submitComment(); }}><textarea aria-label="评论内容" value={comment} onChange={(event) => { setError(""); setComment(event.target.value); }} placeholder="补充进展、风险或交接说明" maxLength={2000} /><button className="primary-button" disabled={saving}>发表评论</button></form>}</>}
      {section === "activity" && <div className="operations-activity-list">{data.activity.map((item) => <article key={item.id}><i /><div><strong>{item.summary || item.action || "任务发生变更"}</strong><p>{item.actorEmail || "系统"} · {formatDateTime(item.createdAt)}</p></div></article>)}{data.activity.length === 0 && <p className="operations-empty-line">暂无活动记录。</p>}</div>}
      {section === "reminders" && <><div className="operations-object-list">{data.reminders.map((item) => <article key={item.id}><div><strong>{formatDateTime(item.remindAt)}</strong><p>{item.note || "任务提醒"} · {reminderStatus(item.status)}</p></div>{canWrite && (item.status ?? "pending") === "pending" && <button type="button" className="row-action danger" disabled={saving} onClick={() => void remove("reminders", item.id)}>取消提醒</button>}</article>)}{data.reminders.length === 0 && <p className="operations-empty-line">还没有提醒。</p>}</div>{canWrite && <form className="operations-inline-form operations-reminder-form" noValidate onSubmit={(event) => { event.preventDefault(); void submitReminder(); }}><input type="datetime-local" aria-label="提醒时间" value={remindAt} onChange={(event) => { setError(""); setRemindAt(event.target.value); }} /><input aria-label="提醒说明" value={reminderNote} onChange={(event) => setReminderNote(event.target.value)} placeholder="提醒说明（可选）" maxLength={500} /><button className="primary-button" disabled={saving}>添加提醒</button></form>}</>}
      {section === "links" && <><div className="operations-object-list">{data.links.map((item) => <article key={item.id}><div><strong>{item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.label}</a> : item.label}</strong><p>{item.entityType} · {item.entityId}</p></div>{canWrite && <button type="button" className="row-action danger" disabled={saving} onClick={() => void remove("links", item.id)}>移除</button>}</article>)}{data.links.length === 0 && <p className="operations-empty-line">还没有关联店铺、商品、活动或报表。</p>}</div>{canWrite && <form className="operations-inline-form operations-link-form" noValidate onSubmit={(event) => { event.preventDefault(); void submitLink(); }}><select aria-label="关联对象类型" value={linkDraft.entityType} onChange={(event) => setLinkDraft((current) => ({ ...current, entityType: event.target.value }))}><option value="shop">店铺</option><option value="product">商品</option><option value="campaign">活动</option><option value="order">订单</option><option value="report">报表</option><option value="url">链接</option></select><input aria-label="关联对象显示名称" value={linkDraft.label} maxLength={240} onChange={(event) => { setError(""); setLinkDraft((current) => ({ ...current, label: event.target.value })); }} placeholder="显示名称" /><input aria-label="关联对象业务 ID" value={linkDraft.entityId} maxLength={240} onChange={(event) => { setError(""); setLinkDraft((current) => ({ ...current, entityId: event.target.value })); }} placeholder="业务 ID" /><input aria-label="关联对象链接" value={linkDraft.url} maxLength={1000} onChange={(event) => setLinkDraft((current) => ({ ...current, url: event.target.value }))} placeholder="链接（可选）" /><button className="primary-button" disabled={saving}>添加关联</button></form>}</>}
      {section === "attachments" && <><div className="operations-attachment-list">{data.attachments.map((item) => <article key={item.id}><div><strong>{item.fileName}</strong><p>{(item.sizeBytes / 1024).toFixed(1)} KB · {item.createdBy} · {formatDateTime(item.createdAt)}</p></div><div><a className="row-action" href={item.downloadUrl}>下载</a>{canWrite && <button type="button" className="row-action danger" disabled={saving} onClick={() => void remove("attachments", item.id)}>删除</button>}</div></article>)}{data.attachments.length === 0 && <p className="operations-empty-line">还没有持久化附件。</p>}</div>{canWrite && <div className="operations-upload-area"><label className="operations-upload-button">{saving ? "上传中…" : "＋ 上传附件"}<input type="file" aria-label="上传任务附件" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.xls,.xlsx,.docx,.txt,.csv" disabled={saving} onChange={(event) => { void upload(event.currentTarget.files); event.currentTarget.value = ""; }} /></label><small>支持 PDF、图片、Excel、DOCX、TXT、CSV；每次最多 8 个，单个不超过 10MB。</small></div>}</>}
    </div>}
  </Dialog>;
}

function TemplateWorkspace({ templates, loading, error, canWrite, onReload, onUse }: { templates: TaskTemplate[]; loading: boolean; error: string; canWrite: boolean; onReload: () => void; onUse: (template: TaskTemplate) => void }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<TaskTemplate | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [editorError, setEditorError] = useState("");
  const [draft, setDraft] = useState<TaskTemplateDraft>({ name: "", description: "", title: "", workContent: "", category: "工作计划", owner: "", shopName: "", startOffsetDays: 0, dueOffsetDays: 3, priority: "normal", active: true });
  const emptyDraft: TaskTemplateDraft = { name: "", description: "", title: "", workContent: "", category: "工作计划", owner: "", shopName: "", startOffsetDays: 0, dueOffsetDays: 3, priority: "normal", active: true };
  const openEditor = (template?: TaskTemplate) => {
    setEditing(template ?? null);
    setDraft(template ? {
      name: template.name,
      description: template.description ?? "",
      title: template.title ?? "",
      workContent: template.workContent ?? "",
      category: template.category ?? "工作计划",
      owner: template.owner ?? "",
      shopName: template.shopName ?? "",
      startOffsetDays: template.startOffsetDays ?? 0,
      dueOffsetDays: template.dueOffsetDays ?? 3,
      priority: template.priority ?? "normal",
      active: template.active !== false,
    } : emptyDraft);
    setEditorError("");
    setFeedback("");
    setOpen(true);
  };
  const save = async () => {
    const validationError = validateTaskTemplateDraft(draft);
    if (validationError) { setEditorError(validationError); return; }
    setSaving(true); setFeedback(""); setEditorError("");
    try {
      if (editing) {
        await requestJson(`/api/workflow/templates?id=${encodeURIComponent(editing.id)}`, { method: "PATCH", body: { ...draft, ...(editing.version !== undefined ? { expectedVersion: editing.version } : {}) } });
      } else {
        await requestJson("/api/workflow/templates", { method: "POST", body: draft });
      }
      setOpen(false);
      setEditing(null);
      setFeedback(editing ? "模板已更新。" : "模板已保存。");
      onReload();
    }
    catch (reason) { setEditorError(messageOf(reason, "模板保存失败，请稍后重试。")); }
    finally { setSaving(false); }
  };
  const remove = async (template: TaskTemplate) => {
    if (!window.confirm(`确认删除模板“${template.name}”？`)) return;
    setSaving(true); setFeedback("");
    try {
      const params = new URLSearchParams({ id: template.id, expectedVersion: String(template.version ?? 1) });
      await requestJson(`/api/workflow/templates?${params}`, { method: "DELETE" });
      setFeedback("模板已删除。");
      onReload();
    }
    catch (reason) { setFeedback(messageOf(reason, "模板删除失败")); }
    finally { setSaving(false); }
  };
  return <>
    <section className="workflow-toolbar workflow-section-hero"><div><span className="eyebrow">WORKSPACE SETTINGS</span><h2>变量配置</h2><p>管理可复用任务模板；店铺、负责人和业务对象以真实任务记录为准。</p></div><button type="button" className="primary-button" disabled={!canWrite} onClick={() => openEditor()}>{canWrite ? "＋ 新建任务模板" : "仅查看"}</button></section>
    {feedback && <div className="workflow-feedback" role="status"><span>i</span><p>{feedback}</p><button type="button" aria-label="关闭模板提示" onClick={() => setFeedback("")}>×</button></div>}
    {error && templates.length > 0 && <div className="workflow-feedback workflow-feedback-error" role="alert"><span>!</span><p>{error}，已保留当前已加载模板。</p><button type="button" aria-label="重试加载任务模板" onClick={onReload}>重试</button></div>}
    {loading && templates.length === 0 ? <DataState kind="loading" title="正在读取任务模板" note="同步任务模板…" /> : error && templates.length === 0 ? <DataState kind="error" title="模板加载失败" note={error} onRetry={onReload} /> : templates.length === 0 ? <DataState kind="empty" title="暂无任务模板" note={canWrite ? "建立模板后，可以快速生成标准化工作事项。" : "当前没有可用模板。"} /> : <section className="operations-template-grid data-refresh-region" aria-busy={loading}>{templates.map((item) => <article className="panel" key={item.id}><header><span className={`status ${item.active === false ? "status-gray" : "status-success"}`}>{item.active === false ? "已停用" : "已启用"}</span><small>{priorityLabel(item.priority ?? "normal")}</small></header><h3>{item.name}</h3><p>{item.description || item.workContent || "未填写模板说明"}</p><dl><div><dt>默认分类</dt><dd>{item.category || "工作计划"}</dd></div><div><dt>默认负责人</dt><dd>{item.owner || "使用时填写"}</dd></div><div><dt>计划周期</dt><dd>第 {item.startOffsetDays ?? 0} 天至第 {item.dueOffsetDays ?? 0} 天</dd></div></dl><footer><button type="button" className="row-action" disabled={item.active === false} onClick={() => onUse(item)}>填入工作项</button>{canWrite && <div className="workflow-plan-actions"><button type="button" className="row-action" disabled={saving} onClick={() => openEditor(item)}>编辑</button><button type="button" className="row-action danger" disabled={saving} onClick={() => void remove(item)}>删除</button></div>}</footer></article>)}</section>}
    <Dialog open={open} onClose={() => !saving && setOpen(false)} dialogId="operations-template-editor" ariaLabel={editing ? "编辑任务模板" : "新建任务模板"} className="workflow-edit-modal"><button type="button" className="workflow-modal-close" aria-label="关闭模板编辑" disabled={saving} onClick={() => setOpen(false)}>×</button><span className="eyebrow">TASK TEMPLATE</span><h2>{editing ? "编辑任务模板" : "新建任务模板"}</h2><form className="workflow-edit-form" noValidate onSubmit={(event) => { event.preventDefault(); void save(); }}><label className="workflow-edit-title-field"><span>模板名称（必填）</span><input autoFocus required maxLength={120} value={draft.name} onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, name: event.target.value })); }} /></label><label className="workflow-edit-content-field"><span>模板说明</span><textarea maxLength={500} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} /></label><label><span>默认事项</span><input maxLength={160} value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} /></label><label><span>默认分类</span><input maxLength={80} value={draft.category} onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))} /></label><label className="workflow-edit-content-field"><span>默认工作内容</span><textarea maxLength={2000} value={draft.workContent} onChange={(event) => setDraft((current) => ({ ...current, workContent: event.target.value }))} /></label><label><span>默认负责人</span><input maxLength={120} value={draft.owner} onChange={(event) => setDraft((current) => ({ ...current, owner: event.target.value }))} /></label><label><span>默认店铺</span><input maxLength={160} value={draft.shopName} onChange={(event) => setDraft((current) => ({ ...current, shopName: event.target.value }))} /></label><label><span>开始偏移（天）</span><input type="number" min={-365} max={365} step={1} value={draft.startOffsetDays} onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, startOffsetDays: Number(event.target.value) })); }} /></label><label><span>截止偏移（天）</span><input type="number" min={draft.startOffsetDays} max={365} step={1} value={draft.dueOffsetDays} onChange={(event) => { setEditorError(""); setDraft((current) => ({ ...current, dueOffsetDays: Number(event.target.value) })); }} /></label><label><span>紧急程度</span><select value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value as Priority }))}><option value="high">紧急</option><option value="normal">普通</option><option value="low">低</option></select></label><label><span>模板状态</span><select value={draft.active ? "active" : "inactive"} onChange={(event) => setDraft((current) => ({ ...current, active: event.target.value === "active" }))}><option value="active">启用</option><option value="inactive">停用</option></select></label>{editorError && <p className="workflow-edit-validation" role="alert">{editorError}</p>}<div className="workflow-modal-actions workflow-edit-actions"><button type="button" className="secondary-button" disabled={saving} onClick={() => setOpen(false)}>取消</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : editing ? "保存修改" : "保存模板"}</button></div></form></Dialog>
  </>;
}

export default function OperationsView({ currentUser, moduleView, onModuleViewChange }: {
  currentUser: { role: Role } | null;
  moduleView: ModuleViewKey<"workflow">;
  onModuleViewChange: (view: ModuleViewKey<"workflow">) => void;
}) {
  const canWrite = currentUser?.role === "operator" || currentUser?.role === "admin";
  const taskGeneration = useRef(0);
  const templateRequestLifecycle = useRef<TemplateRequestLifecycle>(createTemplateRequestLifecycle());
  const activeTab: OperationsTab = moduleView;
  const [tasks, setTasks] = useState<Task[]>([]);
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [error, setError] = useState("");
  const [templatesError, setTemplatesError] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<WorkCard>("all");
  const [taskStatuses, setTaskStatuses] = useState<Status[]>([]);
  const [taskPriorities, setTaskPriorities] = useState<Priority[]>([]);
  const [taskOwners, setTaskOwners] = useState<string[]>([]);
  const [taskShops, setTaskShops] = useState<string[]>([]);
  const [taskCategories, setTaskCategories] = useState<string[]>([]);
  const [taskSources, setTaskSources] = useState<string[]>([]);
  const [taskFacets, setTaskFacets] = useState<TaskFacets>({});
  const [taskDueFrom, setTaskDueFrom] = useState("");
  const [taskDueTo, setTaskDueTo] = useState("");
  const [taskViewMode, setTaskViewMode] = useState<"table" | "timeline">("table");
  const [pagination, setPagination] = useState<Pagination>({ page: 1, pageSize: TASK_PAGE_SIZE, total: 0 });
  const [taskSummary, setTaskSummary] = useState<TaskSummary>({});
  const [dueKpis, setDueKpis] = useState({ overdue: 0, dueToday: 0 });
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [draft, setDraft] = useState<DraftTask>(EMPTY_TASK);
  const [taskEditorError, setTaskEditorError] = useState("");
  const [exporting, setExporting] = useState(false);
  const aiTaskStatusScope: Status[] = workCardStatuses(statusFilter);
  const aiTaskStatuses = taskStatuses.length > 0 ? aiTaskStatusScope.filter(value => taskStatuses.includes(value)) : aiTaskStatusScope;
  const aiCardDates = new URLSearchParams({ dueFrom: taskDueFrom, dueTo: taskDueTo ? calendarDateWithOffset(taskDueTo, 1) : "" });
  applyWorkCardDates(aiCardDates, statusFilter, shanghaiDateWithOffset(), shanghaiDateWithOffset(1));
  useAiPageDetails("workflow", {
    period: null,
    blockedReason: activeTab === "plan" && aiTaskStatuses.length === 0 ? "当前状态筛选没有交集。请调整筛选，或移除页面上下文后提问。" : undefined,
    filters: activeTab === "variables" ? { dataset: "workflow_templates" } : {
      dataset: "workflow_tasks", query: query.trim(), status: aiTaskStatuses, priorities: taskPriorities, owner: taskOwners, shops: taskShops, categories: taskCategories, sources: taskSources,
      dueFrom: aiCardDates.get("dueFrom") || "", dueTo: aiCardDates.get("dueTo") || "", selectedIds: selectedTask ? [selectedTask.id] : [],
    },
  }, activeTab === "plan" || activeTab === "variables");


  const selectOperationsTab = useCallback((tab: OperationsTab) => {
    onModuleViewChange(tab);
    setSelectedTask(null);
  }, [onModuleViewChange]);
  useEffect(() => setSelectedTask(null), [activeTab]);

  const loadTasks = useCallback(async (signal?: AbortSignal, targetPage = 1, append = false) => {
    const generation = ++taskGeneration.current;
    const scopeStatuses: Status[] = workCardStatuses(statusFilter);
    const effectiveStatuses = taskStatuses.length > 0 ? scopeStatuses.filter((value) => taskStatuses.includes(value)) : scopeStatuses;
    if (effectiveStatuses.length === 0) {
      setTasks([]);
      setPagination({ page: 1, pageSize: TASK_PAGE_SIZE, total: 0, returned: 0, truncated: false });
      setLoading(false);
      setLoadingMore(false);
      setError("");
      return;
    }
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError("");
    const commonParams = new URLSearchParams();
    if (query.trim()) commonParams.set("q", query.trim());
    taskPriorities.forEach((value) => commonParams.append("priority", value));
    taskOwners.forEach((value) => commonParams.append("owner", value));
    taskShops.forEach((value) => commonParams.append("shopName", value));
    taskCategories.forEach((value) => commonParams.append("category", value));
    taskSources.forEach((value) => commonParams.append("source", value));
    if (taskDueFrom) commonParams.set("dueFrom", taskDueFrom);
    if (taskDueTo) commonParams.set("dueTo", calendarDateWithOffset(taskDueTo, 1));
    const listParams = new URLSearchParams(commonParams);
    applyWorkCardDates(listParams, statusFilter, shanghaiDateWithOffset(), shanghaiDateWithOffset(1));
    if (listParams.get("dueFrom") && listParams.get("dueTo") && listParams.get("dueFrom")! >= listParams.get("dueTo")!) {
      setTasks([]); setPagination({ page: 1, pageSize: TASK_PAGE_SIZE, total: 0 }); setLoading(false); setLoadingMore(false); setError(""); return;
    }
    effectiveStatuses.forEach((value) => listParams.append("status", value));
    listParams.set("page", String(targetPage));
    listParams.set("pageSize", String(TASK_PAGE_SIZE));

    const today = shanghaiDateWithOffset();
    const tomorrow = calendarDateWithOffset(today, 1);
    const overdueParams = new URLSearchParams(commonParams);
    const todayParams = new URLSearchParams(commonParams);
    if (query.trim()) { overdueParams.set("q", query.trim()); todayParams.set("q", query.trim()); }
    taskPriorities.forEach((value) => { overdueParams.append("priority", value); todayParams.append("priority", value); });
    taskOwners.forEach((value) => { overdueParams.append("owner", value); todayParams.append("owner", value); });
    taskShops.forEach((value) => { overdueParams.append("shopName", value); todayParams.append("shopName", value); });
    taskCategories.forEach((value) => { overdueParams.append("category", value); todayParams.append("category", value); });
    taskSources.forEach((value) => { overdueParams.append("source", value); todayParams.append("source", value); });
    (["待开始", "工作中"] as Status[]).forEach((value) => { overdueParams.append("status", value); todayParams.append("status", value); });
    applyWorkCardDates(overdueParams, "overdue", today, tomorrow);
    applyWorkCardDates(todayParams, "today", today, tomorrow);
    for (const params of [overdueParams, todayParams]) { params.set("page", "1"); params.set("pageSize", "1"); }

    try {
      const summaryParams = new URLSearchParams(commonParams);
      summaryParams.set("page", "1"); summaryParams.set("pageSize", "1");
      const [payload, overduePayload, todayPayload, commonSummaryPayload] = await Promise.all([
        requestJson<TaskListPayload>(`/api/workflow/tasks?${listParams}`, { signal }),
        (overdueParams.get("dueFrom") && overdueParams.get("dueTo") && overdueParams.get("dueFrom")! >= overdueParams.get("dueTo")! ? Promise.resolve({ items: [], pagination: { total: 0 } } as TaskListPayload) : requestJson<TaskListPayload>(`/api/workflow/tasks?${overdueParams}`, { signal })),
        (todayParams.get("dueFrom") && todayParams.get("dueTo") && todayParams.get("dueFrom")! >= todayParams.get("dueTo")! ? Promise.resolve({ items: [], pagination: { total: 0 } } as TaskListPayload) : requestJson<TaskListPayload>(`/api/workflow/tasks?${todayParams}`, { signal })),
        statusFilter === "overdue" || statusFilter === "today" ? requestJson<TaskListPayload>(`/api/workflow/tasks?${summaryParams}`, { signal }) : Promise.resolve(null),
      ]);
      if (generation !== taskGeneration.current) return;
      const rawItems = Array.isArray(payload.items) ? payload.items : [];
      if (payload.facets) setTaskFacets(payload.facets);
      const hasServerPagination = Boolean(payload.pagination);
      const queryNeedle = query.trim().toLocaleLowerCase("zh-CN");
      const matchesCommon = (item: Task) => {
        const matchesQuery = !queryNeedle || [item.title, item.workContent, item.category, item.owner, item.shopName].join(" ").toLocaleLowerCase("zh-CN").includes(queryNeedle);
        const matchesPriority = taskPriorities.length === 0 || taskPriorities.includes(item.priority);
        const matchesOwner = taskOwners.length === 0 || taskOwners.includes(item.owner);
        const matchesShop = taskShops.length === 0 || taskShops.includes(item.shopName);
        const matchesCategory = taskCategories.length === 0 || taskCategories.includes(item.category);
        const matchesSource = taskSources.length === 0 || taskSources.includes(item.source);
        const matchesDueFrom = !taskDueFrom || (item.due !== "待排期" && item.due >= taskDueFrom);
        const matchesDueTo = !taskDueTo || (item.due !== "待排期" && item.due <= taskDueTo);
        return matchesQuery && matchesPriority && matchesOwner && matchesShop && matchesCategory && matchesSource && matchesDueFrom && matchesDueTo;
      };
      const legacyPool = rawItems.filter(matchesCommon);
      const legacyVisible = legacyPool.filter((item) => effectiveStatuses.includes(item.status));
      const nextItems = hasServerPagination ? rawItems : legacyVisible.slice((targetPage - 1) * TASK_PAGE_SIZE, targetPage * TASK_PAGE_SIZE);
      setTasks((current) => {
        if (!append) return nextItems;
        const unique = new Map(current.map((item) => [item.id, item]));
        nextItems.forEach((item) => unique.set(item.id, item));
        return Array.from(unique.values());
      });
      setPagination({
        page: payload.pagination?.page ?? targetPage,
        pageSize: payload.pagination?.pageSize ?? TASK_PAGE_SIZE,
        total: payload.pagination?.total ?? legacyVisible.length,
        returned: payload.pagination?.returned ?? nextItems.length,
        truncated: payload.pagination?.truncated ?? targetPage * TASK_PAGE_SIZE < legacyVisible.length,
      });
      const fallbackSummary = {
        total: legacyPool.length,
        pending: legacyPool.filter((item) => item.status === "待开始").length,
        inProgress: legacyPool.filter((item) => item.status === "工作中").length,
        completed: legacyPool.filter((item) => item.status === "已完成").length,
        open: legacyPool.filter((item) => item.status !== "已完成").length,
      };
      setTaskSummary(commonSummaryPayload?.summary ?? payload.summary ?? fallbackSummary);
      const overdueFallback = (overduePayload.items ?? []).filter((item) => item.status !== "已完成" && item.due !== "待排期" && item.due < today).length;
      const todayFallback = (todayPayload.items ?? []).filter((item) => item.status !== "已完成" && item.due === today).length;
      setDueKpis({
        overdue: overduePayload.pagination?.total ?? overdueFallback,
        dueToday: todayPayload.pagination?.total ?? todayFallback,
      });
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (generation !== taskGeneration.current) return;
      setError(messageOf(reason, "工作计划读取失败"));
    } finally {
      if (generation === taskGeneration.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [query, statusFilter, taskCategories, taskDueFrom, taskDueTo, taskOwners, taskPriorities, taskShops, taskSources, taskStatuses]);

  const loadTemplates = useCallback(async () => {
    const requestKey = `/api/workflow/templates${canWrite ? "?includeInactive=true" : ""}`;
    const request = beginTemplateRequest(templateRequestLifecycle.current, requestKey);
    setTemplatesLoading(true);
    setTemplatesError("");
    try {
      const payload = await requestJson<{ items: TaskTemplate[] }>(requestKey, { signal: request.controller.signal });
      if (!isCurrentTemplateRequest(templateRequestLifecycle.current, request)) return;
      setTemplates(Array.isArray(payload.items) ? payload.items : []);
    }
    catch (reason) {
      if (!isCurrentTemplateRequest(templateRequestLifecycle.current, request)) return;
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setTemplatesError(messageOf(reason, "任务模板读取失败"));
    }
    finally {
      if (isCurrentTemplateRequest(templateRequestLifecycle.current, request)) setTemplatesLoading(false);
    }
  }, [canWrite]);
  useEffect(() => {
    if (activeTab !== "plan") {
      taskGeneration.current += 1;
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => void loadTasks(controller.signal), query.trim() ? 250 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [activeTab, loadTasks, query]);
  useEffect(() => {
    const lifecycle = templateRequestLifecycle.current;
    if (activeTab !== "plan" && activeTab !== "variables") {
      cancelTemplateRequest(lifecycle);
      return;
    }
    void loadTemplates();
    return () => cancelTemplateRequest(lifecycle);
  }, [activeTab, loadTemplates]);

  const counts = {
    pending: taskSummary.pending ?? taskSummary.notStarted ?? 0,
    active: taskSummary.inProgress ?? taskSummary.active ?? 0,
    completed: taskSummary.completed ?? 0,
    total: taskSummary.total ?? 0,
    open: taskSummary.open ?? (taskSummary.pending ?? 0) + (taskSummary.inProgress ?? 0),
  };
  const completedShare = counts.total > 0 ? counts.completed / counts.total * 100 : 0;
  const activeShare = counts.total > 0 ? counts.active / counts.total * 100 : 0;
  const ownerOptions = useMemo(() => Array.from(new Set([...taskOwners, ...(taskFacets.owners ?? []), ...tasks.map((item) => item.owner), ...templates.map((item) => item.owner ?? "")].filter(Boolean))).sort((left, right) => left.localeCompare(right, "zh-CN")), [taskFacets.owners, taskOwners, tasks, templates]);
  const shopOptions = useMemo(() => Array.from(new Set([...taskShops, ...(taskFacets.shopNames ?? []), ...tasks.map((item) => item.shopName), ...templates.map((item) => item.shopName ?? "")].filter(Boolean))).sort((left, right) => left.localeCompare(right, "zh-CN")), [taskFacets.shopNames, taskShops, tasks, templates]);
  const categoryOptions = useMemo(() => Array.from(new Set([...taskCategories, ...(taskFacets.categories ?? []), ...tasks.map((item) => item.category), ...templates.map((item) => item.category ?? "")].filter(Boolean))).sort((left, right) => left.localeCompare(right, "zh-CN")), [taskCategories, taskFacets.categories, tasks, templates]);
  const sourceOptions = useMemo(() => Array.from(new Set([...taskSources, ...(taskFacets.sources ?? []), ...tasks.map((item) => item.source)].filter(Boolean))).sort((left, right) => left.localeCompare(right, "zh-CN")), [taskFacets.sources, taskSources, tasks]);
  const openLoadedTasks = tasks.filter((item) => item.status !== "已完成");
  const prioritySummary = (["high", "normal", "low"] as Priority[]).map((priority) => ({ priority, label: priorityLabel(priority), count: openLoadedTasks.filter((item) => item.priority === priority).length }));
  const priorityMax = Math.max(1, ...prioritySummary.map((item) => item.count));
  const ownerWorkload = Array.from(tasks.reduce((result, item) => {
    if (!item.owner) return result;
    const current = result.get(item.owner) ?? { owner: item.owner, pending: 0, active: 0, completed: 0, total: 0 };
    if (item.status === "待开始") current.pending += 1;
    else if (item.status === "工作中") current.active += 1;
    else current.completed += 1;
    current.total += 1;
    result.set(item.owner, current);
    return result;
  }, new Map<string, { owner: string; pending: number; active: number; completed: number; total: number }>()).values()).sort((left, right) => right.total - left.total).slice(0, 6);
  const ownerWorkloadMax = Math.max(1, ...ownerWorkload.map((item) => item.total));
  const hasMore = pagination.truncated ?? tasks.length < pagination.total;

  const saveTask = async () => {
    if (!canWrite || saving) return;
    const validationError = validateTaskDraft(draft);
    if (validationError) { setTaskEditorError(validationError); return; }
    setSaving(true); setFeedback(""); setTaskEditorError("");
    try {
      if (editingTask) {
        await requestJson<{ item: Task }>(`/api/workflow/tasks?id=${encodeURIComponent(editingTask.id)}`, { method: "PATCH", body: { ...draft, startDate: draft.startDate || "待排期", due: draft.due || "待排期", ...(editingTask.version !== undefined ? { expectedVersion: editingTask.version } : {}) } });
        setFeedback("工作事项已保存。");
      } else {
        await requestJson<{ item: Task }>("/api/workflow/tasks", { method: "POST", body: { ...draft, startDate: draft.startDate || "待排期", due: draft.due || "待排期" } });
        setFeedback("工作事项已创建并持久化保存。");
      }
      setDraft(EMPTY_TASK); setEditingTask(null);
      await loadTasks(undefined, 1, false);
    } catch (reason) { setTaskEditorError(messageOf(reason, "工作事项保存失败，请稍后重试。")); }
    finally { setSaving(false); }
  };
  const patchTask = async (task: Task, changes: Partial<Pick<Task, "status" | "due">>) => {
    if (!canWrite || saving) return;
    setSaving(true); setFeedback("");
    try {
      await requestJson<{ item: Task }>(`/api/workflow/tasks?id=${encodeURIComponent(task.id)}`, { method: "PATCH", body: { ...changes, ...(task.version !== undefined ? { expectedVersion: task.version } : {}) } });
      setFeedback(changes.status ? `“${task.title}”已调整为${statusLabel(changes.status)}。` : `“${task.title}”截止时间已更新。`);
      await loadTasks(undefined, 1, false);
    }
    catch (reason) { setFeedback(messageOf(reason, "工作事项保存失败")); }
    finally { setSaving(false); }
  };
  const removeTask = async (task: Task) => {
    if (!canWrite || saving || !window.confirm(`确认删除“${task.title}”？`)) return;
    setSaving(true);
    try {
      const params = new URLSearchParams({ id: task.id });
      if (task.version !== undefined) params.set("expectedVersion", String(task.version));
      await requestJson(`/api/workflow/tasks?${params}`, { method: "DELETE" });
      setFeedback("工作事项已删除。");
      await loadTasks(undefined, 1, false);
    }
    catch (reason) { setFeedback(messageOf(reason, "工作事项删除失败")); }
    finally { setSaving(false); }
  };
  const useTemplate = (template: TaskTemplate) => {
    setDraft({ title: template.title ?? template.name, workContent: template.workContent ?? "", category: template.category ?? "工作计划", owner: template.owner ?? "", shopName: template.shopName ?? "", startDate: shanghaiDateWithOffset(template.startOffsetDays ?? 0), due: shanghaiDateWithOffset(template.dueOffsetDays ?? 0), priority: template.priority ?? "normal" });
    setEditingTask(null); setTaskEditorError(""); setFeedback(`已填入模板“${template.name}”，请核对后点击“添加工作项”。`);
    selectOperationsTab("plan"); window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const edit = (task: Task) => {
    setEditingTask(task);
    setDraft({ title: task.title, workContent: task.workContent, category: task.category, owner: task.owner, shopName: task.shopName, startDate: task.startDate === "待排期" ? "" : task.startDate, due: task.due === "待排期" ? "" : task.due, priority: task.priority });
    setTaskEditorError("");
    window.requestAnimationFrame(() => document.getElementById("operations-task-editor")?.scrollIntoView({ behavior: "smooth", block: "center" }));
  };
  const downloadTasks = async () => {
    if (exporting) return;
    setExporting(true); setFeedback("");
    try {
      const scopeStatuses: Status[] = workCardStatuses(statusFilter);
      const effectiveStatuses = taskStatuses.length > 0 ? scopeStatuses.filter((value) => taskStatuses.includes(value)) : scopeStatuses;
      const params = new URLSearchParams({ page: "1", pageSize: "100" });
      if (query.trim()) params.set("q", query.trim());
      effectiveStatuses.forEach((value) => params.append("status", value));
      taskPriorities.forEach((value) => params.append("priority", value));
      taskOwners.forEach((value) => params.append("owner", value));
      taskShops.forEach((value) => params.append("shopName", value));
      taskCategories.forEach((value) => params.append("category", value));
      taskSources.forEach((value) => params.append("source", value));
      if (taskDueFrom) params.set("dueFrom", taskDueFrom);
      if (taskDueTo) params.set("dueTo", calendarDateWithOffset(taskDueTo, 1));
      applyWorkCardDates(params, statusFilter, shanghaiDateWithOffset(), shanghaiDateWithOffset(1));
      if (params.get("dueFrom") && params.get("dueTo") && params.get("dueFrom")! >= params.get("dueTo")!) { setFeedback("当前筛选没有可导出的事项。"); return; }
      const exported: Task[] = [];
      let page = 1;
      let total = 0;
      do {
        params.set("page", String(page));
        const payload = await requestJson<TaskListPayload>(`/api/workflow/tasks?${params}`);
        const nextItems = Array.isArray(payload.items) ? payload.items : [];
        total = payload.pagination?.total ?? nextItems.length;
        if (total > 100_000) throw new Error("当前筛选结果超过 100,000 条，请缩小范围后导出。");
        exported.push(...nextItems);
        page += 1;
        if (nextItems.length === 0) break;
      } while (exported.length < total);
      downloadCsvFile(`运营事务-工作计划-${shanghaiDateWithOffset()}.csv`, [
        ["事项分类", "工作事项", "工作内容", "店铺", "紧急程度", "跟进人", "开始时间", "截止时间", "状态", "来源", "录入时间"],
        ...exported.map((task) => [task.category, task.title, task.workContent, task.shopName, priorityLabel(task.priority), task.owner, task.startDate, task.due, statusLabel(task.status), task.source, formatRecordedAt(task.createdAt)]),
      ]);
      setFeedback(`已导出当前筛选下的完整清单，共 ${exported.length} 项。`);
    } catch (reason) {
      setFeedback(messageOf(reason, "工作计划导出失败"));
    } finally {
      setExporting(false);
    }
  };

  const subnav = <div className="subnav workflow-subnav" role="tablist" aria-label="运营事务子版块">{([["plan", "工作计划"], ["inspection", "巡店检查"], ["reviews", "评价维护"], ["launch", "新品上架"], ["launch-followup", "上新跟进"], ["variables", "变量配置"]] as Array<[OperationsTab, string]>).map(([value, label]) => <button
    type="button"
    role="tab"
    id={`operations-tab-${value}`}
    aria-controls={`operations-panel-${value}`}
    aria-selected={activeTab === value}
    tabIndex={activeTab === value ? 0 : -1}
    className={activeTab === value ? "active" : ""}
    key={value}
    onClick={() => selectOperationsTab(value)}
    onKeyDown={(event) => {
      const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
      const index = tabs.indexOf(event.currentTarget);
      const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : -1;
      if (nextIndex >= 0) { event.preventDefault(); tabs[nextIndex]?.focus(); tabs[nextIndex]?.click(); }
    }}
  >{label}</button>)}</div>;
  if (activeTab === "inspection" || activeTab === "reviews") return <>{subnav}<div role="tabpanel" id={`operations-panel-${activeTab}`} aria-labelledby={`operations-tab-${activeTab}`}><OperationsRecordWorkspace key={activeTab} type={activeTab === "reviews" ? "review" : activeTab} canWrite={canWrite} /></div></>;
  if (activeTab === "launch") return <>{subnav}<div role="tabpanel" id="operations-panel-launch" aria-labelledby="operations-tab-launch"><NewProductLaunchView canWrite={canWrite} /></div></>;
  if (activeTab === "launch-followup") return <>{subnav}<div role="tabpanel" id="operations-panel-launch-followup" aria-labelledby="operations-tab-launch-followup"><NewProductSalesFollowupView canWrite={canWrite} /></div></>;
  if (activeTab === "variables") return <>{subnav}<div role="tabpanel" id="operations-panel-variables" aria-labelledby="operations-tab-variables"><TemplateWorkspace templates={templates} loading={templatesLoading} error={templatesError} canWrite={canWrite} onReload={() => void loadTemplates()} onUse={useTemplate} /></div></>;

  const listTitle = statusFilter === "all" ? "全部工作事项" : statusFilter === "overdue" ? "已逾期事项" : statusFilter === "today" ? "今日到期事项" : statusFilter === "open" ? "工作事项清单" : statusFilter === "pending" ? "未开始事项" : statusFilter === "active" ? "进行中事项" : "已完成事项";
  const listNote = statusFilter === "completed" ? "已完成事项统一归档在这里，可按需返还到未开始或进行中。" : "按当前状态与日期条件展示；再次点击已选卡片可取消筛选。";
  const loadMore = hasMore ? <div className="operations-load-more"><button type="button" className="secondary-button" disabled={loadingMore} onClick={() => void loadTasks(undefined, pagination.page + 1, true)}>{loadingMore ? "加载中…" : `继续加载（${tasks.length} / ${pagination.total}）`}</button></div> : null;

  return <>{subnav}<div role="tabpanel" id="operations-panel-plan" aria-labelledby="operations-tab-plan">
    <section className="workflow-toolbar workflow-section-hero workflow-plan-hero">
      <div><span className="eyebrow">OPERATION COLLABORATION</span><h2>工作计划</h2><p>集中管理责任人和截止节点；任务、协作与附件均由服务端持久化保存。</p></div>
      <div className="workflow-hero-actions"><span>{canWrite ? "可协作编辑" : "只读访问"}</span><button type="button" className="secondary-button" disabled={pagination.total === 0 || exporting} onClick={() => void downloadTasks()}>{exporting ? "正在导出…" : "⇩ 导出筛选完整清单"}</button><button type="button" className="secondary-button" onClick={() => void loadTasks()} disabled={loading}>刷新</button></div>
    </section>
    {feedback && <div className="workflow-feedback" role="status"><span>i</span><p>{feedback}</p><button type="button" aria-label="关闭工作计划提示" onClick={() => setFeedback("")}>×</button></div>}

    <section className="panel workflow-plan-controls workflow-reference-controls">
      <div className="workflow-filter-row workflow-filter-row-rich">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工作事项、内容、店铺或跟进人" aria-label="搜索工作计划" />
        <MultiSelectFilter values={taskStatuses} onChange={(values) => setTaskStatuses(values as Status[])} ariaLabel="工作计划状态" allLabel="全部状态" options={STATUS_OPTIONS.map((value) => ({ value, label: statusLabel(value) }))} />
        <MultiSelectFilter values={taskPriorities} onChange={(values) => setTaskPriorities(values as Priority[])} ariaLabel="工作计划紧急程度" allLabel="全部紧急程度" options={[{ value: "high", label: "紧急" }, { value: "normal", label: "普通" }, { value: "low", label: "低" }]} />
        <MultiSelectFilter values={taskOwners} onChange={setTaskOwners} ariaLabel="工作计划跟进人" allLabel="全部跟进人" options={ownerOptions.map((owner) => ({ value: owner, label: owner }))} />
        <MultiSelectFilter values={taskShops} onChange={setTaskShops} ariaLabel="工作计划店铺" allLabel="全部店铺" options={shopOptions.map((shop) => ({ value: shop, label: shop }))} />
        <MultiSelectFilter values={taskCategories} onChange={setTaskCategories} ariaLabel="工作计划事项分类" allLabel="全部分类" options={categoryOptions.map((category) => ({ value: category, label: category }))} />
        <MultiSelectFilter values={taskSources} onChange={setTaskSources} ariaLabel="工作计划来源" allLabel="全部来源" options={sourceOptions.map((source) => ({ value: source, label: source }))} />
        <label className="workflow-date-filter"><span>截止日期</span><input type="date" value={taskDueFrom} max={taskDueTo || undefined} onChange={(event) => setTaskDueFrom(event.target.value)} aria-label="截止从" /><i>至</i><input type="date" value={taskDueTo} min={taskDueFrom || undefined} onChange={(event) => setTaskDueTo(event.target.value)} aria-label="截止到（含）" /></label>
        <button type="button" className="row-action operations-filter-reset" onClick={() => { setQuery(""); setStatusFilter("all"); setTaskStatuses([]); setTaskPriorities([]); setTaskOwners([]); setTaskShops([]); setTaskCategories([]); setTaskSources([]); setTaskDueFrom(""); setTaskDueTo(""); }}>清除筛选</button>
      </div>
      <div className="workflow-view-switch" role="radiogroup" aria-label="工作计划视图"><button type="button" role="radio" aria-checked={taskViewMode === "table"} className={taskViewMode === "table" ? "active" : ""} onClick={() => setTaskViewMode("table")}>☷ 表格</button><button type="button" role="radio" aria-checked={taskViewMode === "timeline"} className={taskViewMode === "timeline" ? "active" : ""} onClick={() => setTaskViewMode("timeline")}>⌁ 时间轴</button></div>
    </section>

    <section className="workflow-summary-grid">
      <button type="button" className={`tone-blue${statusFilter === "active" ? " active" : ""}`} aria-pressed={statusFilter === "active"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "active", "all")); setTaskStatuses([]); }}><span>进行中</span><strong>{counts.active}</strong><small>正在推进</small></button>
      <button type="button" className={`tone-red${statusFilter === "overdue" ? " active" : ""}`} aria-pressed={statusFilter === "overdue"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "overdue", "all")); setTaskStatuses([]); }}><span>已逾期</span><strong>{dueKpis.overdue}</strong><small>上海时区，需优先闭环</small></button>
      <button type="button" className={`tone-orange${statusFilter === "today" ? " active" : ""}`} aria-pressed={statusFilter === "today"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "today", "all")); setTaskStatuses([]); }}><span>今日到期</span><strong>{dueKpis.dueToday}</strong><small>当日未完成事项</small></button>
      <button type="button" className={`tone-green${statusFilter === "completed" ? " active" : ""}`} aria-pressed={statusFilter === "completed"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "completed", "all")); setTaskStatuses([]); }}><span>已完成</span><strong>{counts.completed}</strong><small>已完成事项</small></button>
      <button type="button" className={`tone-slate${statusFilter === "all" ? " active" : ""}`} aria-pressed={statusFilter === "all"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "all", "all")); setTaskStatuses([]); }}><span>合计</span><strong>{counts.total}</strong><small>全部状态</small></button>
    </section>

    <section className="workflow-insight-grid">
      <article className="panel workflow-chart-card"><header><div><h3>状态分布</h3><p>当前筛选上下文，服务端汇总</p></div><span>{counts.total} 项</span></header><div className="workflow-donut-layout"><div className="workflow-donut" style={{ background: `conic-gradient(#2f72ef 0 ${activeShare}%, #35b779 ${activeShare}% ${activeShare + completedShare}%, #d9dee7 ${activeShare + completedShare}% 100%)` }}><i><strong>{Math.round(completedShare)}%</strong><small>完成率</small></i></div><div className="workflow-chart-legend"><span><i className="legend-gray" />未开始 <strong>{counts.pending}</strong></span><span><i className="legend-blue" />进行中 <strong>{counts.active}</strong></span><span><i className="legend-green" />已完成 <strong>{counts.completed}</strong></span></div></div></article>
      <article className="panel workflow-chart-card"><header><div><h3>未完成 · 按紧急程度</h3><p>基于当前已加载清单</p></div><span>{openLoadedTasks.length} 项</span></header><div className="workflow-priority-chart">{prioritySummary.map((item) => <div key={item.priority}><label><span>{item.label}</span><strong>{item.count}</strong></label><i><b className={`priority-${item.priority}`} style={{ width: `${item.count / priorityMax * 100}%` }} /></i></div>)}</div></article>
      <article className="panel workflow-chart-card"><header><div><h3>按跟进人工作量</h3><p>当前已加载，最多 6 位</p></div><span>{ownerWorkload.length} 人</span></header><div className="workflow-owner-chart">{ownerWorkload.length > 0 ? ownerWorkload.map((item) => <div key={item.owner}><label><span title={item.owner}>{item.owner}</span><strong>{item.total}</strong></label><i><b className="owner-pending" style={{ width: `${item.pending / ownerWorkloadMax * 100}%` }} /><b className="owner-active" style={{ width: `${item.active / ownerWorkloadMax * 100}%` }} /><b className="owner-completed" style={{ width: `${item.completed / ownerWorkloadMax * 100}%` }} /></i></div>) : <p className="workflow-chart-empty">暂无跟进人数据</p>}</div><footer><span><i className="legend-gray" />未开始</span><span><i className="legend-blue" />进行中</span><span><i className="legend-green" />已完成</span></footer></article>
    </section>

    {canWrite ? <section id="operations-task-editor" className="panel workflow-quick-create"><header><div><span className="eyebrow">{editingTask ? "EDIT WORK ITEM" : "QUICK ENTRY"}</span><h3>{editingTask ? "编辑工作事项" : "快速录入工作项"}</h3></div>{editingTask && <button type="button" className="row-action" onClick={() => { setEditingTask(null); setDraft(EMPTY_TASK); setTaskEditorError(""); }}>取消编辑</button>}</header><form className="workflow-task-create-fields" noValidate onSubmit={(event) => { event.preventDefault(); void saveTask(); }}><label><span>事项分类</span><input list="workflow-category-options" maxLength={80} value={draft.category} onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))} /></label><label><span>工作事项（必填）</span><input required maxLength={160} value={draft.title} onChange={(event) => { setTaskEditorError(""); setDraft((current) => ({ ...current, title: event.target.value })); }} /></label><label className="workflow-create-content"><span>工作内容</span><input maxLength={2000} value={draft.workContent} onChange={(event) => setDraft((current) => ({ ...current, workContent: event.target.value }))} /></label><label><span>店铺名称</span><input list="workflow-shop-options" maxLength={160} value={draft.shopName} onChange={(event) => setDraft((current) => ({ ...current, shopName: event.target.value }))} /></label><label><span>紧急程度</span><select value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value as Priority }))}><option value="high">紧急</option><option value="normal">普通</option><option value="low">低</option></select></label><label><span>开始时间</span><input type="date" max={draft.due || undefined} value={draft.startDate} onChange={(event) => { setTaskEditorError(""); setDraft((current) => ({ ...current, startDate: event.target.value })); }} /></label><label><span>截止时间</span><input type="date" min={draft.startDate || undefined} value={draft.due} onChange={(event) => { setTaskEditorError(""); setDraft((current) => ({ ...current, due: event.target.value })); }} /></label><label><span>跟进人</span><input list="workflow-owner-options" maxLength={120} value={draft.owner} onChange={(event) => setDraft((current) => ({ ...current, owner: event.target.value }))} /></label>{taskEditorError && <p className="workflow-edit-validation" role="alert">{taskEditorError}</p>}<button type="submit" className="primary-button" disabled={saving}>{saving ? "保存中…" : editingTask ? "保存修改" : "＋ 添加工作项"}</button></form><datalist id="workflow-category-options">{categoryOptions.map((value) => <option key={value} value={value} />)}</datalist><datalist id="workflow-shop-options">{shopOptions.map((value) => <option key={value} value={value} />)}</datalist><datalist id="workflow-owner-options">{ownerOptions.map((value) => <option key={value} value={value} />)}</datalist><small>分类、店铺和负责人会优先复用现有标准选项；状态自动记录，逾期按 Asia/Shanghai 判定。编辑保存使用版本校验，冲突时请刷新后重试。</small></section> : <DataState kind="permission" title="当前为只读模式" note="你可以查看任务与协作详情；新增、编辑、评论和附件上传需要运营或管理员权限。" />}

    <section className="workflow-task-buckets" aria-label="工作事项分类">
      <button type="button" className={statusFilter === "open" ? "active" : ""} aria-pressed={statusFilter === "open"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "open", "all")); setTaskStatuses([]); }}><span>工作事项</span><strong>{counts.open}</strong><small>未开始 + 进行中</small></button>
      <button type="button" className={statusFilter === "pending" ? "active" : ""} aria-pressed={statusFilter === "pending"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "pending", "all")); setTaskStatuses([]); }}><span>未开始</span><strong>{counts.pending}</strong><small>等待启动</small></button>
      <button type="button" className={statusFilter === "active" ? "active" : ""} aria-pressed={statusFilter === "active"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "active", "all")); setTaskStatuses([]); }}><span>进行中</span><strong>{counts.active}</strong><small>正在推进</small></button>
      <button type="button" className={statusFilter === "completed" ? "active completed" : "completed"} aria-pressed={statusFilter === "completed"} onClick={() => { setStatusFilter(current => toggleSingleFilter(current, "completed", "all")); setTaskStatuses([]); }}><span>已完成</span><strong>{counts.completed}</strong><small>完成归档</small></button>
    </section>

    {error && tasks.length > 0 && <div className="workflow-feedback workflow-feedback-error" role="alert"><span>!</span><p>{error}，已保留当前已加载清单。</p><button type="button" aria-label="重试加载工作计划" onClick={() => void loadTasks()}>重试</button></div>}
    {loading && tasks.length === 0 ? <DataState kind="loading" title="正在读取工作计划" note="正在应用服务端筛选并同步任务状态…" /> : error && tasks.length === 0 ? <DataState kind="error" title="工作计划加载失败" note={error} onRetry={() => void loadTasks()} /> : taskViewMode === "table" ? <section className="panel workflow-plan-table-panel data-refresh-region" aria-busy={loading}>
      <div className="workflow-list-heading"><div><h3>{listTitle}</h3><p>{listNote}</p></div><span>已加载 {tasks.length} / {pagination.total} 项</span></div>
      <div className="data-table-wrap"><table className="data-table workflow-data-table workflow-plan-table operations-plan-table">
        <thead><tr><th>工作事项</th><th>工作内容</th><th>店铺 / 来源</th><th>紧急程度</th><th>跟进人</th><th>截止 / 录入</th><th>状态</th><th>协作</th><th>操作</th></tr></thead>
        <tbody>{tasks.map((task) => <tr key={task.id}>
          <td><div className="workflow-plan-title"><strong>{task.title}</strong><small>{task.category}</small></div></td>
          <td><p className="workflow-plan-content" title={task.workContent}>{task.workContent}</p></td>
          <td><span className="workflow-plan-shop" title={task.shopName}>{task.shopName || "未关联店铺"}</span><small className="operations-cell-note">来源：{task.source || "未知"}</small></td>
          <td><b className={`workflow-priority priority-${task.priority}`}>{priorityLabel(task.priority)}</b></td>
          <td>{task.owner || "未指定"}</td>
          <td><input className="workflow-due-input" type="date" min={task.startDate === "待排期" ? undefined : task.startDate} value={task.due === "待排期" ? "" : task.due} aria-label={`调整${task.title}截止时间`} disabled={!canWrite || saving} onChange={(event) => void patchTask(task, { due: event.target.value || "待排期" })} /><small className="operations-cell-note">录入：{formatRecordedAt(task.createdAt)}</small></td>
          <td><select className="operations-status-select" aria-label={`${task.title}状态`} value={task.status} disabled={!canWrite || saving} onChange={(event) => void patchTask(task, { status: event.target.value as Status })}>{STATUS_OPTIONS.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></td>
          <td><button type="button" className="row-action operations-collaboration-button" onClick={() => setSelectedTask(task)}>评论 · 提醒 · 附件</button></td>
          <td><div className="workflow-plan-actions"><TaskTransitionActions task={task} disabled={!canWrite || saving} onTransition={(status) => void patchTask(task, { status })} /><button type="button" className="row-action" disabled={!canWrite || saving} onClick={() => edit(task)}>编辑</button><button type="button" className="row-action danger" disabled={!canWrite || saving} onClick={() => void removeTask(task)}>删除</button></div></td>
        </tr>)}{tasks.length === 0 && <tr><td colSpan={9}><div className="table-state">{counts.total === 0 ? "暂无工作事项。" : "没有符合当前分类和筛选条件的事项。"}</div></td></tr>}</tbody>
      </table></div>{loadMore}
    </section> : <section className="panel workflow-timeline-panel data-refresh-region" aria-busy={loading}>
      <div className="workflow-list-heading"><div><h3>{listTitle} · 时间轴</h3><p>{listNote}</p></div><span>已加载 {tasks.length} / {pagination.total} 项</span></div>
      <div className="workflow-timeline">{tasks.map((task) => <article key={task.id} className={task.status === "已完成" ? "is-completed" : task.due !== "待排期" && task.due < shanghaiDateWithOffset() ? "is-overdue" : ""}><time>{task.due === "待排期" ? "待排期" : task.due}</time><i /><div><header><strong>{task.title}</strong><StatusBadge status={statusLabel(task.status)} /></header><p>{task.workContent}</p><footer><span>{task.shopName || "未关联店铺"}</span><span>{task.owner || "未指定"}</span><span>{task.source || "未知来源"}</span><b className={`workflow-priority priority-${task.priority}`}>{priorityLabel(task.priority)}</b><button type="button" className="row-action" onClick={() => setSelectedTask(task)}>查看协作</button><button type="button" className="row-action" disabled={!canWrite || saving} onClick={() => edit(task)}>编辑</button></footer></div></article>)}{tasks.length === 0 && <div className="table-state">没有符合当前分类和筛选条件的事项。</div>}</div>{loadMore}
    </section>}
    {selectedTask && <TaskCollaborationDialog task={selectedTask} canWrite={canWrite} onClose={() => setSelectedTask(null)} />}
  </div></>;
}
