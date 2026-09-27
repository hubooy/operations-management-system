"use client";

import { useAiPageDetails } from "./ai-page-context-provider";
/* eslint-disable @next/next/no-img-element -- JD competitor images are external audited sources. */

import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  defaultMarketAnnotationConcurrency,
  MARKET_ANNOTATION_CONCURRENCY_LIMITS,
  MARKET_ANNOTATION_JOB_LIMITS,
  normalizeMarketAnnotationConcurrency,
  type MarketAnnotationExecutor,
} from "@/lib/market/annotation-limits";
import { defaultAnnotationPromptBody } from "@/lib/market/annotation-prompt-template";
import { remainingInferenceUnitsForJob } from "@/lib/market/annotation-progress";
import { AnnotationReadError, createAnnotationReadChannel } from "@/lib/market/annotation-read-channel";
import { canSelectAnnotationReviewItem } from "@/lib/market/annotation-review-state";

type CurrentUser = { email: string; role: "viewer" | "analyst" | "operator" | "admin" } | null;
type Model = { id: string; name: string; protocol: string; modelName: string };
type Prompt = { id: string; category: string; version: number; parentId: string | null; source: string; status: string; segments: string[]; promptBody: string; changeNote: string; metrics: Record<string, unknown>; createdAt: string };
type Job = { id: string; category: string; promptVersionId: string; executor: string; modelId: string | null; localModelName: string; status: string; totalCount: number; completedCount: number; failedCount: number; reviewedCount: number; committedCount: number; remainingInferenceCount: number; createdAt: string };
type Item = { id: string; candidateId: string; jobId: string; category: string; skuCode: string; productName: string; brand: string; sourceImageUrl: string; resolvedImageUrl: string; imageSource: string; status: string; aiSegment: string; aiImagePriceCents: number | null; aiConfidenceBps: number | null; aiReason: string; modelInputBytes: number; imageLoadMs: number; imagePrepareMs: number; modelCallMs: number; totalInferenceMs: number; reviewedSegment: string; reviewedImagePriceCents: number | null; reviewPriceSource: "history_same_image" | "ai" | "manual"; reviewJobReady: boolean; reviewSegments: string[]; snapshotValid?: boolean; selected: boolean; version: number; errorMessage: string; createdAt: string };
type ValidationRun = { id: string; category: string; candidatePromptId: string; baselinePromptId?: string; status: string; sampleCount: number; sampleHash: string; metrics: Record<string, unknown>; gate: { passed?: boolean; reasons?: string[] } };
type ValidationResult = { id: string; runId: string; status: string; skuCode: string; productName: string; goldSegment: string; predictedSegment: string; isCorrect: number; errorMessage: string };
type CloudRun = { jobId: string; state: "running" | "paused" | "completed"; runConcurrency: number; targetConcurrency: number; recovering: boolean; nextRunAt: string | null; lastFailureCode: string; lastFailureMessage: string; lastStartedAt: string | null; lastHeartbeatAt: string | null; completedAt: string | null; updatedAt: string };
type Workspace = { categories: Array<{ value: string; count: number; candidateCount: number | null }>; reviewCategories: Array<{ value: string; jobCount: number; recordCount: number }>; taxonomy: Array<{ category: string; value: string }>; prompts: Prompt[]; jobs: Job[]; concurrencySettings: Array<{ category: string; executor: MarketAnnotationExecutor; concurrency: number; updatedBy: string; updatedAt: string }>; cloudRuns: CloudRun[]; items: Item[]; itemPagination: { page: number; pageSize: number; pageCount: number; total: number }; reviewSummary: { jobCount: number; recordCount: number; uniqueCandidateCount: number }; selection: { filteredReviewableCount: number; filteredSelectedCount: number; scopeSelectedCount: number; staleSelectedCount?: number }; models: Model[]; textModels: Model[]; validationRuns: ValidationRun[]; validationResults: ValidationResult[]; agents: Array<{ id: string; name: string; status: string; lastSeenAt?: string; revokedAt?: string }>; error?: string };
type ReviewWorkspace = Pick<Workspace, "items" | "itemPagination" | "reviewSummary" | "selection"> & { error?: string };
type CandidateCountsPayload = { categories: Array<{ value: string; candidateCount: number }>; error?: string };
type CandidateCountsStatus = "idle" | "loading" | "ready" | "error";

function handleRovingTabKey(event: KeyboardEvent<HTMLButtonElement>) {
  const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
  const index = tabs.indexOf(event.currentTarget);
  const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : -1;
  if (nextIndex >= 0) { event.preventDefault(); tabs[nextIndex]?.focus(); tabs[nextIndex]?.click(); }
}
type JobProgress = { job: Job; activeClaims: number; uniqueInferenceUnits: number; remainingInferenceUnits: number; cloudRun: CloudRun | null; performance: { measuredCount: number; averageImageLoadMs: number; averageImagePrepareMs: number; averageModelCallMs: number; averageTotalInferenceMs: number; averageModelInputBytes: number }; error?: string };
type Draft = { segment: string; price: string; selected: boolean; version: number };

const LOAD_TIMEOUT_MS = 30_000;
const ACTION_TIMEOUT_MS = 110_000;
const MAX_COMMIT_BATCHES = 100;
const money = (cents: number | null | undefined) => cents === null || cents === undefined ? "—" : new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(cents / 100);
const duration = (ms: number | null | undefined) => !ms ? "—" : ms >= 1_000 ? `${(ms / 1_000).toFixed(1)} 秒` : `${ms} 毫秒`;
const bytes = (value: number | null | undefined) => !value ? "—" : value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.round(value / 1024)} KB`;
const yuanInput = (cents: number | null | undefined) => cents === null || cents === undefined ? "" : String(cents / 100);
const centsInput = (yuan: string) => yuan.trim() === "" ? null : Math.round(Number(yuan) * 100);
const annotationProductHref = (skuCode: unknown) => {
  const sku = String(skuCode ?? "").trim();
  return /^\d{6,20}$/.test(sku) ? `https://item.jd.com/${sku}.html` : "";
};
const annotationRecognitionLabel = (item: Pick<Item, "status" | "aiSegment" | "aiImagePriceCents" | "aiConfidenceBps" | "aiReason" | "reviewPriceSource">) => {
  if (item.reviewPriceSource === "history_same_image") return "历史同图价格待审";
  const recognized = Boolean(item.aiSegment || item.aiImagePriceCents !== null || item.aiConfidenceBps !== null || item.aiReason);
  if (recognized) return "AI 已识别";
  return item.status === "failed" ? "AI 识别失败" : "未生成 AI 结果";
};
const annotationResultMessage = (item: Pick<Item, "aiReason" | "errorMessage" | "status" | "reviewPriceSource" | "snapshotValid">) => {
  if (item.snapshotValid === false && ["review_pending", "approved", "rejected"].includes(item.status)) return "图片或对应月份数据已变化，已排除入库；请按当前图片重新识别并复核。";
  if (item.reviewPriceSource === "history_same_image") return "主图与历史已确认图片一致，默认沿用上次标准售价，请人工复核后入库";
  if (item.aiReason) return item.aiReason;
  if (/状态码\s*429/.test(item.errorMessage)) return `${item.errorMessage}：模型供应商限流或额度不足，请稍后重试并检查额度`;
  if (/状态码\s*400/.test(item.errorMessage)) return `${item.errorMessage}：请求被接口拒绝，请先在 AI 助理中重新执行图片能力测试，并核对模型标识及结构化输出兼容性`;
  if (item.errorMessage) return item.errorMessage;
  return item.status === "inferencing" ? "识别处理中" : "等待识别";
};
const annotationTimingMessage = (item: Pick<Item, "totalInferenceMs" | "modelCallMs" | "imageLoadMs" | "imagePrepareMs" | "modelInputBytes">) => item.totalInferenceMs > 0
  ? `耗时：总计 ${duration(item.totalInferenceMs)}，模型 ${duration(item.modelCallMs)}，取图 ${duration(item.imageLoadMs)}，图片处理 ${duration(item.imagePrepareMs)}，输入 ${bytes(item.modelInputBytes)}`
  : "";
const annotationReviewScopeKey = (input: { page: number; pageSize: number; categories: string[]; segments: string[]; storageStatuses: string[]; recognitionSources: string[] }) => JSON.stringify(input);
const annotationCandidateScopeKey = (categories: Array<{ value: string }>) => JSON.stringify(categories.map((item) => item.value).sort());
const annotationConcurrencyKey = (category: string, executor: MarketAnnotationExecutor) => `${category}\u0000${executor}`;
const isValidAnnotationConcurrency = (value: number) => Number.isSafeInteger(value)
  && value >= MARKET_ANNOTATION_CONCURRENCY_LIMITS.minimum
  && value <= MARKET_ANNOTATION_CONCURRENCY_LIMITS.maximum;

function AnnotationMultiFilter({ label, allLabel, values, options, onChange }: { label: string; allLabel: string; values: string[]; options: Array<{ value: string; label: string }>; onChange: (values: string[]) => void }) {
  const toggle = (value: string) => {
    const next = values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
    onChange(next.length === options.length ? [] : next);
  };
  const summary = values.length === 0 ? allLabel : values.length === 1 ? options.find((item) => item.value === values[0])?.label ?? values[0] : `已选 ${values.length} 项`;
  return <div className="annotation-review-category-filter annotation-review-compact-filter"><span>{label}（可多选）</span><details><summary aria-label={`${label}筛选`}>{summary}</summary><div className="annotation-review-category-menu"><button type="button" onClick={() => onChange([])}>{allLabel}</button>{options.map((option) => <label key={option.value}><input type="checkbox" checked={values.includes(option.value)} onChange={() => toggle(option.value)} /><span>{option.label}</span></label>)}</div></details></div>;
}
export default function MarketAnnotationView({ currentUser, embedded = false }: { currentUser: CurrentUser; embedded?: boolean }) {
  const [data, setData] = useState<Workspace | null>(null);
  const [jobId, setJobId] = useState("");
  const [category, setCategory] = useState("");
  const [categoryQuery, setCategoryQuery] = useState("");
  const [executor, setExecutor] = useState<"cloud" | "local">("cloud");
  const [visionModelId, setVisionModelId] = useState("");
  const [textModelId, setTextModelId] = useState("");
  const [localModelName, setLocalModelName] = useState("gemma4");
  const [limit, setLimit] = useState<number>(MARKET_ANNOTATION_JOB_LIMITS.default);
  const [concurrencyDrafts, setConcurrencyDrafts] = useState<Record<string, number>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [promptId, setPromptId] = useState("");
  const [promptBody, setPromptBody] = useState("");
  const [segmentsText, setSegmentsText] = useState("");
  const [changeNote, setChangeNote] = useState("");
  const [loadedReviewScopeKey, setLoadedReviewScopeKey] = useState("");
  const [itemPage, setItemPage] = useState(1);
  const [itemPageSize, setItemPageSize] = useState(20);
  const [reviewCategories, setReviewCategories] = useState<string[]>([]);
  const [itemSegments, setItemSegments] = useState<string[]>([]);
  const [storageStatuses, setStorageStatuses] = useState<Array<"pending" | "committed">>([]);
  const [recognitionSources, setRecognitionSources] = useState<Array<"ai" | "non_ai">>([]);
  useAiPageDetails("market", {
    period: null,
    filters: { categories: reviewCategories, itemSegments, storageStatuses, recognitionSources },
  });

  const [cloudProgress, setCloudProgress] = useState<JobProgress | null>(null);
  const [reviewView, setReviewView] = useState<"list" | "gallery">("list");
  const [sampleCount, setSampleCount] = useState(50);
  const [seed, setSeed] = useState("market-annotation-v1");
  const [busy, setBusy] = useState("");
  const [savingConcurrencyKey, setSavingConcurrencyKey] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [progressError, setProgressError] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [progressReader] = useState(() => createAnnotationReadChannel({ label: "任务进度读取", onError: setProgressError }));
  const [reviewReader] = useState(() => createAnnotationReadChannel({ label: "复核列表读取", onError: setReviewError }));
  const [initialLoading, setInitialLoading] = useState(true);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [candidateCountsStatus, setCandidateCountsStatus] = useState<CandidateCountsStatus>("idle");
  const [candidateCountsError, setCandidateCountsError] = useState("");
  const [agentToken, setAgentToken] = useState("");
  const dirtyDraftIdsRef = useRef(new Set<string>());
  const loadSequenceRef = useRef(0);
  const reviewLoadSequenceRef = useRef(0);
  const candidateCountsGenerationRef = useRef(0);
  const candidateCountsControllerRef = useRef<AbortController | null>(null);
  const candidateCountsScopeRef = useRef("");
  const mountedRef = useRef(false);
  const initialReadyRef = useRef(false);
  const isAdmin = currentUser?.role === "admin";
  const canEdit = currentUser?.role === "admin" || currentUser?.role === "operator";

  const loadCandidateCounts = useCallback(async (workspaceGeneration: number, categoryScopeKey: string) => {
    const requestGeneration = ++candidateCountsGenerationRef.current;
    candidateCountsControllerRef.current?.abort();
    const controller = new AbortController();
    candidateCountsControllerRef.current = controller;
    candidateCountsScopeRef.current = categoryScopeKey;
    setCandidateCountsStatus("loading");
    setCandidateCountsError("");
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, LOAD_TIMEOUT_MS);
    const isCurrent = () => mountedRef.current
      && requestGeneration === candidateCountsGenerationRef.current
      && workspaceGeneration === loadSequenceRef.current
      && categoryScopeKey === candidateCountsScopeRef.current;
    let response: Response;
    let payload: CandidateCountsPayload | null;
    try {
      response = await fetch("/api/market/annotations?view=candidate_counts", { cache: "no-store", signal: controller.signal });
      payload = await response.json().catch(() => null) as CandidateCountsPayload | null;
      if (!response.ok || !payload || !Array.isArray(payload.categories)) throw new Error(payload?.error || "读取可新建候选数量失败");
    } catch (reason) {
      if (!isCurrent()) return;
      const message = timedOut ? "读取可新建候选数量超时，请重试" : reason instanceof Error ? reason.message : "读取可新建候选数量失败";
      setCandidateCountsStatus("error");
      setCandidateCountsError(message);
      return;
    } finally {
      window.clearTimeout(timeout);
      if (candidateCountsControllerRef.current === controller) candidateCountsControllerRef.current = null;
    }
    if (!isCurrent()) return;
    const counts = new Map(payload.categories.map((item) => [item.value, Number(item.candidateCount ?? 0)]));
    setData((current) => {
      if (!current || annotationCandidateScopeKey(current.categories) !== categoryScopeKey) return current;
      return { ...current, categories: current.categories.map((item) => ({ ...item, candidateCount: counts.get(item.value) ?? 0 })) };
    });
    setCandidateCountsStatus("ready");
    setCandidateCountsError("");
  }, []);

  const load = useCallback(async (nextJobId = jobId, nextItemPage = itemPage, resetDrafts = false, deferCandidateCounts = false) => {
    const loadSequence = ++loadSequenceRef.current;
    candidateCountsGenerationRef.current += 1;
    candidateCountsControllerRef.current?.abort();
    candidateCountsControllerRef.current = null;
    candidateCountsScopeRef.current = "";
    setCandidateCountsStatus("loading");
    setCandidateCountsError("");
    setData((current) => current ? { ...current, categories: current.categories.map((item) => ({ ...item, candidateCount: null })) } : current);
    const params = new URLSearchParams({ view: deferCandidateCounts ? "workspace_fast" : "workspace", itemPage: String(nextItemPage), itemPageSize: String(itemPageSize), aggregateJobs: "1" });
    if (nextJobId) params.set("jobId", nextJobId);
    reviewCategories.forEach((value) => params.append("itemCategory", value));
    itemSegments.forEach((value) => params.append("itemSegment", value));
    storageStatuses.forEach((value) => params.append("storageStatus", value));
    recognitionSources.forEach((value) => params.append("recognitionSource", value));
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
    let response: Response;
    let payload: Workspace | null;
    try {
      response = await fetch("/api/market/annotations?" + params, { cache: "no-store", signal: controller.signal });
      try {
        payload = await response.json() as Workspace;
      } catch (reason) {
        if (controller.signal.aborted) throw reason;
        payload = null;
      }
    } catch (reason) {
      if (loadSequence !== loadSequenceRef.current) return;
      const message = controller.signal.aborted ? "读取标注工作台超时，请重试" : reason instanceof Error ? reason.message : "读取标注工作台失败";
      setCandidateCountsStatus("error");
      setCandidateCountsError(message);
      if (controller.signal.aborted) throw new Error("读取标注工作台超时，请重试");
      throw reason;
    } finally {
      window.clearTimeout(timeout);
    }
    if (loadSequence !== loadSequenceRef.current) return;
    if (!response.ok || !payload) {
      const message = payload?.error || "读取标注工作台失败";
      setCandidateCountsStatus("error");
      setCandidateCountsError(message);
      throw new Error(message);
    }
    setLoadedReviewScopeKey(annotationReviewScopeKey({ page: nextItemPage, pageSize: itemPageSize, categories: reviewCategories, segments: itemSegments, storageStatuses, recognitionSources }));
    setData(payload);
    const candidateScopeKey = annotationCandidateScopeKey(payload.categories);
    if (deferCandidateCounts) void loadCandidateCounts(loadSequence, candidateScopeKey);
    else {
      candidateCountsScopeRef.current = candidateScopeKey;
      setCandidateCountsStatus("ready");
      setCandidateCountsError("");
    }
    const resolvedJobId = nextJobId || payload.jobs[0]?.id || "";
    setJobId(resolvedJobId);
    if (!nextJobId && resolvedJobId) setItemPage(1);
    const resolvedCategory = category;
    setVisionModelId((current) => current || payload.models[0]?.id || "");
    setTextModelId((current) => current || payload.textModels[0]?.id || "");
    if (!promptId) {
      const initialPrompt = payload.prompts.find((item) => item.category === resolvedCategory && item.status === "active") ?? payload.prompts.find((item) => item.category === resolvedCategory);
      const taxonomyText = payload.taxonomy.filter((item) => item.category === resolvedCategory).map((item) => item.value).join("\n");
      if (initialPrompt) { setPromptId(initialPrompt.id); setPromptBody(initialPrompt.promptBody); setSegmentsText(taxonomyText || initialPrompt.segments.join("\n")); }
      else if (resolvedCategory) { setSegmentsText(taxonomyText); setPromptBody((current) => current || defaultAnnotationPromptBody(resolvedCategory, taxonomyText.split("\n").filter(Boolean))); }
    }
    setDrafts((current) => Object.fromEntries(payload.items.map((item) => {
      const serverDraft = { segment: item.reviewedSegment || item.aiSegment, price: yuanInput(item.reviewedImagePriceCents), selected: item.selected, version: item.version };
      const existing = current[item.id];
      const preserve = !resetDrafts && Boolean(existing) && dirtyDraftIdsRef.current.has(item.id) && existing.version === serverDraft.version;
      if (!preserve) dirtyDraftIdsRef.current.delete(item.id);
      return [item.id, preserve ? existing : serverDraft];
    })));
  }, [jobId, itemPage, itemPageSize, category, reviewCategories, promptId, itemSegments, storageStatuses, recognitionSources, loadCandidateCounts]);

  const loadReview = useCallback(async (resetDrafts = false) => {
    const loadSequence = ++reviewLoadSequenceRef.current;
    reviewReader.cancel();
    const params = new URLSearchParams({ view: "review", itemPage: String(itemPage), itemPageSize: String(itemPageSize), aggregateJobs: "1" });
    reviewCategories.forEach((value) => params.append("itemCategory", value));
    itemSegments.forEach((value) => params.append("itemSegment", value));
    storageStatuses.forEach((value) => params.append("storageStatus", value));
    recognitionSources.forEach((value) => params.append("recognitionSource", value));
    setReviewLoading(true);
    try {
      await reviewReader.read<ReviewWorkspace>("/api/market/annotations?" + params,
        (value): value is ReviewWorkspace => Boolean(value && typeof value === "object" && Array.isArray((value as ReviewWorkspace).items) && (value as ReviewWorkspace).itemPagination && (value as ReviewWorkspace).selection),
        (payload) => {
          setLoadedReviewScopeKey(annotationReviewScopeKey({ page: itemPage, pageSize: itemPageSize, categories: reviewCategories, segments: itemSegments, storageStatuses, recognitionSources }));
          setData((current) => current ? { ...current, ...payload } : current);
          setDrafts((current) => Object.fromEntries(payload.items.map((item) => {
            const serverDraft = { segment: item.reviewedSegment || item.aiSegment, price: yuanInput(item.reviewedImagePriceCents), selected: item.selected, version: item.version };
            const existing = current[item.id];
            const preserve = !resetDrafts && Boolean(existing) && dirtyDraftIdsRef.current.has(item.id) && existing.version === serverDraft.version;
            if (!preserve) dirtyDraftIdsRef.current.delete(item.id);
            return [item.id, preserve ? existing : serverDraft];
          })));
        });
    } finally {
      if (loadSequence === reviewLoadSequenceRef.current) setReviewLoading(false);
    }
  }, [itemPage, itemPageSize, reviewCategories, itemSegments, storageStatuses, recognitionSources, reviewReader]);

  const loadJobProgress = useCallback(async (targetJobId: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ view: "progress", jobId: targetJobId });
    return progressReader.read<JobProgress>("/api/market/annotations?" + params,
      (value): value is JobProgress => Boolean(value && typeof value === "object" && (value as JobProgress).job?.id === targetJobId),
      (payload) => {
        setCloudProgress(payload);
        setData((current) => current ? {
          ...current,
          jobs: current.jobs.map((item) => item.id === payload.job.id ? payload.job : item),
          cloudRuns: payload.cloudRun
            ? [...current.cloudRuns.filter((item) => item.jobId !== payload.job.id), payload.cloudRun]
            : current.cloudRuns,
        } : current);
      }, signal);
  }, [progressReader]);

  const loadInitial = useCallback(async () => {
    setInitialLoading(true);
    setError("");
    try {
      await load(jobId, itemPage, false, true);
      initialReadyRef.current = true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    } finally {
      setInitialLoading(false);
    }
  }, [jobId, itemPage, load]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadSequenceRef.current += 1;
      reviewLoadSequenceRef.current += 1;
      reviewReader.cancel();
      candidateCountsGenerationRef.current += 1;
      candidateCountsScopeRef.current = "";
      candidateCountsControllerRef.current?.abort();
      candidateCountsControllerRef.current = null;
      progressReader.cancel();
    };
  }, [progressReader, reviewReader]);
  useEffect(() => { const timer = window.setTimeout(() => void loadInitial(), 0); return () => window.clearTimeout(timer); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!initialReadyRef.current) return;
    const timer = window.setTimeout(() => void loadReview().catch(() => undefined), 0);
    return () => {
      window.clearTimeout(timer);
      reviewLoadSequenceRef.current += 1;
      reviewReader.cancel();
    };
  }, [loadReview, reviewReader]);
  const post = async (body: Record<string, unknown>) => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS);
    let response: Response;
    let payload: { result?: unknown; error?: string } | null;
    try {
      response = await fetch("/api/market/annotations", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
      try {
        payload = await response.json() as { result?: unknown; error?: string };
      } catch (reason) {
        if (controller.signal.aborted) throw reason;
        payload = null;
      }
    } catch (reason) {
      if (controller.signal.aborted) throw new Error("操作等待超时；系统将自动刷新任务状态并续跑原任务");
      throw reason;
    } finally {
      window.clearTimeout(timeout);
    }
    if (!response.ok || !payload) {
      const requestError = new Error(payload?.error || "操作失败") as Error & { status: number };
      requestError.status = response.status;
      throw requestError;
    }
    return payload?.result as Record<string, unknown> | undefined;
  };
  const act = async (name: string, fn: () => Promise<void>) => {
    setBusy(name); setError(""); setNotice("");
    try { await fn(); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "操作失败";
      if (!(reason instanceof AnnotationReadError)) setError(message);
      if ((reason as { status?: number })?.status === 409 && ["commit", "save-review", "select-filtered"].includes(name)) {
        await loadReview(true).catch(() => undefined);
      }
    } finally { setBusy(""); }
  };
  const activePrompt = data?.prompts.find((item) => item.category === category && item.status === "active");
  const selectedPrompt = data?.prompts.find((item) => item.id === promptId && item.category === category) ?? activePrompt;
  const selectedCategorySummary = data?.categories.find((item) => item.value === category);
  const compatibleExistingJob = data?.jobs.find((item) =>
    item.category === category
    && item.promptVersionId === activePrompt?.id
    && item.executor === executor
    && (executor === "cloud" ? item.modelId === visionModelId : item.localModelName === localModelName.trim())
    && ["queued", "running", "failed"].includes(item.status)
    && item.remainingInferenceCount > 0);
  const categoryReviewReadyJob = data?.jobs.find((item) => item.category === category && item.status === "review_ready");
  const currentJob = data?.jobs.find((item) => item.id === jobId);
  const currentCloudRun = data?.cloudRuns.find((item) => item.jobId === currentJob?.id) ?? null;
  const currentRemainingInferenceCount = remainingInferenceUnitsForJob(currentJob, cloudProgress);
  const currentCloudRunHasUnfinishedItems = currentJob?.executor === "cloud" && currentRemainingInferenceCount > 0;
  const currentCloudRunIsRunning = currentCloudRun?.state === "running" && currentCloudRunHasUnfinishedItems;
  const currentCloudRunHasUnknownResult = currentCloudRun?.lastFailureCode === "inference_result_unknown";
  const currentJobPendingReviewCount = currentJob ? Math.max(0, currentJob.totalCount - currentJob.committedCount - currentJob.failedCount) : 0;
  const currentJobSettledMessage = currentCloudRunHasUnknownResult
    ? "有识别结果尚未确认，已暂停并保留原记录；请先核验异常项，避免重复识别"
    : currentJob?.status === "committed"
    ? "已全部正式入库，无需重复识别"
    : currentJob?.status === "review_ready"
      ? `识别已结束${currentJobPendingReviewCount ? `，待复核/入库 ${currentJobPendingReviewCount} 条` : ""}${currentJob.failedCount ? `，失败封顶 ${currentJob.failedCount} 条` : ""}；请在下方复核或归档旧任务`
      : "当前没有待识别项";
  const backgroundJobId = currentJob?.id ?? "";
  const backgroundExecutor = currentJob?.executor ?? "";
  useEffect(() => {
    progressReader.cancel();
    setCloudProgress(null);
    setProgressError("");
    return () => progressReader.cancel();
  }, [backgroundJobId, progressReader]);
  useEffect(() => {
    if (!backgroundJobId || backgroundExecutor !== "cloud" || !currentCloudRunIsRunning) return;
    let disposed = false;
    const controller = new AbortController();
    let polling = false;
    let reviewedCompletedCount = -1;
    const tick = async () => {
      if (disposed || polling) return;
      polling = true;
      try {
        const progress = await loadJobProgress(backgroundJobId, controller.signal);
        if (!progress || disposed) return;
        if (!disposed && (progress.job.completedCount !== reviewedCompletedCount || reviewReader.hasError())) {
          await loadReview();
          reviewedCompletedCount = progress.job.completedCount;
        }
      } catch {
        // Each read channel retains its own failure until that same read succeeds.
      } finally {
        polling = false;
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 5_000);
    return () => { disposed = true; controller.abort(); window.clearInterval(timer); };
  }, [backgroundExecutor, backgroundJobId, currentCloudRunIsRunning, loadJobProgress, loadReview, reviewReader]);
  const concurrencyFor = (targetCategory: string, targetExecutor: MarketAnnotationExecutor) => {
    const key = annotationConcurrencyKey(targetCategory, targetExecutor);
    const stored = data?.concurrencySettings.find((item) => item.category === targetCategory && item.executor === targetExecutor)?.concurrency;
    return concurrencyDrafts[key] ?? stored ?? defaultMarketAnnotationConcurrency(targetExecutor);
  };
  const persistConcurrency = async (targetCategory: string, targetExecutor: MarketAnnotationExecutor, requested: number) => {
    const concurrency = normalizeMarketAnnotationConcurrency(requested, targetExecutor);
    const result = await post({ action: "set_concurrency", category: targetCategory, executor: targetExecutor, concurrency });
    const setting = { category: targetCategory, executor: targetExecutor, concurrency, updatedBy: String(result?.updatedBy ?? currentUser?.email ?? ""), updatedAt: String(result?.updatedAt ?? new Date().toISOString()) };
    setConcurrencyDrafts((current) => ({ ...current, [annotationConcurrencyKey(targetCategory, targetExecutor)]: concurrency }));
    setData((current) => current ? { ...current, concurrencySettings: [...current.concurrencySettings.filter((item) => item.category !== targetCategory || item.executor !== targetExecutor), setting] } : current);
    return concurrency;
  };
  const saveConcurrency = async (targetCategory: string, targetExecutor: MarketAnnotationExecutor) => {
    const key = annotationConcurrencyKey(targetCategory, targetExecutor);
    setSavingConcurrencyKey(key);
    setError("");
    try {
      const saved = await persistConcurrency(targetCategory, targetExecutor, concurrencyFor(targetCategory, targetExecutor));
      setNotice(`${targetCategory}的${targetExecutor === "cloud" ? "云端" : "本地"}模型并发数已保存为 ${saved}${targetExecutor === "cloud" && currentJob?.id ? "，云端后台将在下一批即时应用" : ""}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "并发数保存失败");
    } finally {
      setSavingConcurrencyKey("");
    }
  };
  const reviewableIds = new Set((data?.items ?? []).filter((item) => item.snapshotValid !== false && ["review_pending", "approved", "rejected"].includes(item.status)).map((item) => item.id));
  const activeReviewScopeKey = annotationReviewScopeKey({ page: itemPage, pageSize: itemPageSize, categories: reviewCategories, segments: itemSegments, storageStatuses, recognitionSources });
  const reviewScopeReady = loadedReviewScopeKey === activeReviewScopeKey;
  const importableIds = new Set((data?.items ?? []).filter((item) => {
    return canSelectAnnotationReviewItem(item, drafts[item.id]?.segment ?? "");
  }).map((item) => item.id));
  const updateDraft = (id: string, patch: Partial<Draft>) => { dirtyDraftIdsRef.current.add(id); setDrafts((current) => ({ ...current, [id]: { ...current[id]!, ...patch } })); };

  const choosePrompt = (item: Prompt) => { setPromptId(item.id); setCategory(item.category); setPromptBody(item.promptBody); setSegmentsText((data?.taxonomy ?? []).filter((entry) => entry.category === item.category).map((entry) => entry.value).join("\n") || item.segments.join("\n")); setChangeNote(""); };
  const chooseCategory = (nextCategory: string) => {
    setCategoryQuery("");
    setCategory(nextCategory);
    const nextJob = data?.jobs.find((item) => !nextCategory || item.category === nextCategory);
    if (nextJob) setJobId(nextJob.id);
    const nextPrompt = data?.prompts.find((item) => item.category === nextCategory && item.status === "active") ?? data?.prompts.find((item) => item.category === nextCategory);
    if (nextPrompt) choosePrompt(nextPrompt);
    else {
      const nextSegments = (data?.taxonomy ?? []).filter((item) => item.category === nextCategory).map((item) => item.value);
      setPromptId("");
      setSegmentsText(nextSegments.join("\n"));
      setPromptBody(nextCategory ? defaultAnnotationPromptBody(nextCategory, nextSegments) : "");
    }
  };
  // 创建任务有多个前置条件，早期版本只是把按钮置灰，用户点下去没有任何反馈。
  // 这里统一算出唯一的阻塞原因：页面上直接显示，点击时也用同一句话报错。
  const createJobBlockReason = () => {
    if (!canEdit) return "当前账号只读，创建标注任务需要 operator 或 admin 角色";
    if (!category) return "“全部三级类目”仅用于浏览和筛选，创建任务前请先选择一个具体三级类目";
    if (candidateCountsStatus !== "ready") return candidateCountsStatus === "error"
      ? `可新建候选数量读取失败：${candidateCountsError || "请重试"}`
      : "正在读取可新建候选数量，请稍候";
    if (!activePrompt) return `“${category}”还没有已激活的 Prompt 版本：请在下方“3. Prompt 版本与自动进化”确认正文后点击“保存人工子版本”，再点击“激活此版”`;
    if (executor === "cloud" && !visionModelId) return "没有可用的云端视觉模型：请先在 AI 助理的模型配置中启用一个 vision 模型";
    if (executor === "local" && !localModelName.trim()) return "本地执行器必须填写 Ollama 模型名";
    if (!isValidAnnotationConcurrency(concurrencyFor(category, executor))) {
      return `并发数必须是 ${MARKET_ANNOTATION_CONCURRENCY_LIMITS.minimum} 到 ${MARKET_ANNOTATION_CONCURRENCY_LIMITS.maximum} 的整数`;
    }
    if ((selectedCategorySummary?.candidateCount ?? 0) === 0 && !compatibleExistingJob) {
      if (categoryReviewReadyJob) return `“${category}”当前可新建候选为 0；已有复核任务 ${categoryReviewReadyJob.completedCount}/${categoryReviewReadyJob.totalCount}，请先在下方完成人工复核与批量入库`;
      return `“${category}”当前可新建候选为 0；顶部待 AI 总量包含无图、非 SKU、Prompt 不可用、失败封顶或已由现有任务覆盖的快照，请先处理阻塞原因`;
    }
    return "";
  };
  const retryCandidateCounts = () => {
    if (!data) return;
    void loadCandidateCounts(loadSequenceRef.current, annotationCandidateScopeKey(data.categories));
  };
  const createJob = () => act("create-job", async () => {
    const blocked = createJobBlockReason();
    if (blocked) throw new Error(blocked);
    const concurrency = normalizeMarketAnnotationConcurrency(concurrencyFor(category, executor), executor);
    if (compatibleExistingJob) {
      const id = compatibleExistingJob.id;
      dirtyDraftIdsRef.current.clear();
      setCloudProgress(null);
      setItemPage(1);
      setJobId(id);
      let savedConcurrency = concurrency;
      if (executor === "cloud") {
        savedConcurrency = await persistConcurrency(category, "cloud", concurrency);
        await post({ action: "set_cloud_run_state", jobId: id, state: "running" });
      }
      const progress = await loadJobProgress(id);
      if (!progress) return;
      await load(id, 1);
      setJobId(id);
      setCloudProgress(progress);
      setNotice(progress.remainingInferenceUnits === 0
        ? "兼容任务已处理完毕，现在可以创建下一批任务"
        : executor === "cloud"
          ? `已切换到兼容任务并重新唤醒云端后台（目标并发 ${savedConcurrency}），剩余推理 ${progress.remainingInferenceUnits} 条`
          : `已切换到兼容任务，剩余推理 ${progress.remainingInferenceUnits} 条；本地 Ollama agent 可继续领取`);
      return;
    }
    const result = await post({ action: "create_job", category, promptVersionId: activePrompt?.id, executor, modelId: executor === "cloud" ? visionModelId : undefined, localModelName: executor === "local" ? localModelName : undefined, limit, concurrency });
    const id = String(result?.id || "");
    if (!id) throw new Error("创建任务成功，但服务端没有返回任务 ID；请刷新后重试");
    dirtyDraftIdsRef.current.clear(); setCloudProgress(null); setItemPage(1); setJobId(id);
    setNotice(categoryReviewReadyJob ? "下一批标注任务已创建；上一批可继续人工复核" : "标注任务已创建");
    await load(id, 1);
  });
  const pumpCloud = () => act("run-cloud", async () => {
    if (!currentJob || currentJob.executor !== "cloud") throw new Error("请选择需要继续的云端标注任务");
    const alreadyRunning = currentCloudRunIsRunning;
    const savedConcurrency = await persistConcurrency(currentJob.category, "cloud", concurrencyFor(currentJob.category, "cloud"));
    await post({ action: "set_cloud_run_state", jobId: currentJob.id, state: "running" });
    const progress = await loadJobProgress(currentJob.id);
    if (!progress) return;
    setNotice(progress.cloudRun?.state === "completed" || progress.job.status === "review_ready"
      ? "云端识别队列已经处理完毕，可进入人工复核与批量入库"
      : alreadyRunning
        ? `已安全唤醒云端后台（目标并发 ${savedConcurrency}）；如已有执行器正在工作，本次不会重置并发退避或重复领取。`
        : `云端后台已接管任务（目标并发 ${savedConcurrency}），将由后台定时领取；关闭浏览器可继续，本机部署需要电脑和运营系统保持运行。`);
  });
  const pauseCloud = () => act("pause-cloud", async () => {
    if (!currentJob || currentJob.executor !== "cloud") throw new Error("请选择需要暂停的云端标注任务");
    await post({ action: "set_cloud_run_state", jobId: currentJob.id, state: "paused" });
    await loadJobProgress(currentJob.id);
    setNotice("已请求暂停；在途图片完成后不会再领取新图片，可随时恢复同一任务。 ");
  });
  const saveReviewGroups = async (ids: string[]) => {
    const groups = new Map<string, Array<{ id: string; version: number; segment: string; imagePriceCents: number | null; selected: boolean }>>();
    for (const id of ids) {
      const item = data?.items.find((candidate) => candidate.id === id);
      const draft = drafts[id];
      if (!item || !draft) continue;
      const updates = groups.get(item.jobId) ?? [];
      updates.push({ id, version: draft.version, segment: draft.segment, imagePriceCents: centsInput(draft.price), selected: draft.selected });
      groups.set(item.jobId, updates);
    }
    for (const [reviewJobId, updates] of groups) await post({ action: "review", jobId: reviewJobId, updates });
  };
  const saveReview = (ids?: string[]) => act("save-review", async () => {
    if (!reviewScopeReady) throw new Error("三级品类筛选仍在刷新，请等待列表更新后再保存");
    const targetIds = ids ?? (data?.items ?? []).filter((item) => reviewableIds.has(item.id) && dirtyDraftIdsRef.current.has(item.id)).map((item) => item.id);
    if (!targetIds.length) throw new Error("当前页没有待保存的人工复核修改");
    await saveReviewGroups(targetIds); targetIds.forEach((id) => dirtyDraftIdsRef.current.delete(id)); await loadReview(); setNotice("人工复核已按任务分组保存");
  });
  const setFilteredSelection = (selected: boolean) => act("select-filtered", async () => {
    if (!reviewScopeReady) throw new Error("三级品类筛选仍在刷新，请等待列表更新后再全选");
    const editedIds = (data?.items ?? []).filter((item) => reviewableIds.has(item.id) && dirtyDraftIdsRef.current.has(item.id)).map((item) => item.id);
    if (editedIds.length) await saveReviewGroups(editedIds);
    const result = await post({ action: "select_filtered", aggregateJobs: true, categories: reviewCategories, selected, itemSegments, storageStatuses, recognitionSources });
    dirtyDraftIdsRef.current.clear(); await loadReview(true);
    setNotice(selected ? `已跨页全选当前筛选结果 ${String(result?.changed ?? 0)} 条` : `已清空当前筛选结果 ${String(result?.changed ?? 0)} 条选择`);
  });
  const commit = () => act("commit", async () => {
    if (!reviewScopeReady) throw new Error("三级品类筛选仍在刷新，请等待列表更新后再入库");
    const selectedPageIds = (data?.items ?? []).filter((item) => importableIds.has(item.id) && dirtyDraftIdsRef.current.has(item.id)).map((item) => item.id);
    if (!selectedCount) throw new Error("请先勾选需要入库的候选项");
    if (selectedPageIds.length) await saveReviewGroups(selectedPageIds);
    const operationId = "ui_aggregate_" + Date.now().toString(36);
    let committed = 0;
    let duplicates = 0;
    let skippedStale = 0;
    for (let batch = 1; batch <= MAX_COMMIT_BATCHES; batch += 1) {
      setNotice(`正在分批入库：已完成 ${committed} 条，本批最多处理 500 条`);
      const result = await post({ action: "commit_selected", aggregateJobs: true, categories: reviewCategories, idempotencyKey: `${operationId}_${batch}` });
      committed += Number(result?.committed ?? 0);
      duplicates += Number(result?.duplicates ?? 0);
      skippedStale = Math.max(skippedStale, Number(result?.skippedStaleCount ?? 0));
      if (result?.ok === false || result?.partial) {
        await loadReview(true);
        throw new Error(String(result?.error || `部分成功：已入库 ${committed} 条；页面已刷新，可重新点击续跑`));
      }
      if (!result?.hasMore) break;
      if (batch === MAX_COMMIT_BATCHES) throw new Error(`已连续处理 ${committed} 条，但仍有剩余选择；请再次点击批量入库继续`);
    }
    selectedPageIds.forEach((id) => dirtyDraftIdsRef.current.delete(id));
    await loadReview(true);
    const skippedNotice = skippedStale
      ? `；跳过图片或对应月份数据已变化的候选 ${skippedStale} 条，保留原复核记录，需按当前图片重新识别并复核`
      : "";
    setNotice(`已分批入库 ${committed} 条，重复请求 ${duplicates} 条${skippedNotice}`);
  });
  const savePrompt = (mode: "manual" | "generate") => act("prompt", async () => {
    if (!category) throw new Error("“全部三级类目”仅用于浏览和筛选；保存 Prompt 前请选择一个具体类目");
    const body = { category, segments: segmentsText.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean), parentId: selectedPrompt?.id, changeNote };
    const result = mode === "manual" ? await post({ action: "create_prompt", ...body, promptBody }) : await post({ action: "generate_prompt", ...body, textModelId });
    await load(jobId); setPromptId(String(result?.id || "")); setNotice(mode === "manual" ? "人工 Prompt 子版本已创建" : "AI Prompt 候选已生成");
  });
  const testPrompt = (id = selectedPrompt?.id) => act("validation", async () => {
    if (!id) throw new Error("请选择 Prompt");
    const run = await post({ action: "create_validation", promptId: id, modelId: visionModelId, sampleCount, seed });
    await pumpValidation(String(run?.id || ""));
  });
  const evolve = () => act("evolve", async () => {
    if (!selectedPrompt) throw new Error("请选择需要进化的 Prompt");
    const result = await post({ action: "evolve_prompt", category, parentId: selectedPrompt.id, segments: segmentsText.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean), textModelId, visionModelId, sampleCount, seed, changeNote });
    const validation = result?.validation as Record<string, unknown> | undefined;
    await pumpValidation(String(validation?.id || "")); setNotice("AI 已生成子版本并完成冻结验证");
  });
  const pumpValidation = async (runId: string) => {
    if (!runId) throw new Error("验证运行 ID 为空");
    for (let index = 0; index < 1_100; index += 1) { const result = await post({ action: "run_validation_next", runId }); if (result?.done || result?.waiting) break; }
    await load(jobId);
  };
  const activate = (item: Prompt, rollback = false) => act("activate", async () => {
    const latestRun = data?.validationRuns.find((run) => run.candidatePromptId === item.id);
    const metricsGate = item.metrics?.gate as { passed?: boolean } | undefined;
    const gatePassed = Boolean((latestRun?.status === "completed" && latestRun.gate?.passed) || metricsGate?.passed);
    const message = rollback
      ? "请输入回滚原因（至少6字）"
      : gatePassed
        ? "该版本已通过门禁，可留空直接激活"
        : "该版本尚未通过冻结验证。管理员确认仍要启用时，必须保留至少6字的审计原因";
    const entered = window.prompt(message, rollback ? "回滚到已验证的稳定版本" : gatePassed ? "" : "管理员人工确认启用首个版本");
    if (entered === null) return;
    const reason = entered.trim();
    if ((!gatePassed || rollback) && reason.length < 6) throw new Error("该版本未通过门禁，必须填写至少 6 个字符的管理员确认原因");
    await post({ action: rollback ? "rollback_prompt" : "activate_prompt", promptId: item.id, explicitOverride: !rollback && !gatePassed, reason });
    await load(jobId); setNotice(rollback ? "Prompt 已回滚并重新激活" : "Prompt 已激活");
  });
  const deletePrompt = (item: Prompt) => act("delete-prompt", async () => {
    if (item.status !== "draft") throw new Error("只能删除尚未激活的草稿版本");
    if (!window.confirm(`确认删除 ${item.category} 的 v${item.version} 草稿？已被任务或验证引用的版本会由系统阻止删除。`)) return;
    await post({ action: "delete_prompt", promptId: item.id });
    if (selectedPrompt?.id === item.id) {
      const replacement = data?.prompts.find((prompt) => prompt.id !== item.id && prompt.category === item.category);
      if (replacement) choosePrompt(replacement);
      else { setPromptId(""); setPromptBody(""); setSegmentsText((data?.taxonomy ?? []).filter((entry) => entry.category === item.category).map((entry) => entry.value).join("\n")); }
    }
    await load(jobId); setNotice(`Prompt v${item.version} 草稿已删除`);
  });
  const deleteJob = (item: Job) => act("delete-job", async () => {
    if (!["review_ready", "committed"].includes(item.status)) throw new Error("只能归档推理已结束或已经全部入库的任务记录");
    const pendingReviewCount = Math.max(0, item.totalCount - item.committedCount - item.failedCount);
    const detail = item.status === "committed"
      ? `该任务 ${item.committedCount} 条明细已全部正式入库。`
      : `该任务推理已经结束，仍有 ${pendingReviewCount} 条待复核/入库、${item.failedCount} 条失败封顶。归档后这些未入库候选会退出工作台。`;
    if (!window.confirm(`确认归档“${item.category}”的这条任务记录？\n\n${detail}\n\n已经正式入库的 SKU 标注、价格、入库回执、任务明细和审计记录都会保留。`)) return;
    const result = await post({ action: "delete_job", jobId: item.id });
    dirtyDraftIdsRef.current.clear();
    if (jobId === item.id) { setJobId(""); setCloudProgress(null); }
    await load("", 1, true);
    setNotice(`任务已归档；保留正式入库 ${String(result?.preservedCommittedItems ?? item.committedCount)} 条、审计明细 ${String(result?.preservedItems ?? item.totalCount)} 条，隐藏待复核 ${String(result?.archivedPendingItems ?? pendingReviewCount)} 条`);
  });
  const createAgent = () => act("agent", async () => { const name = window.prompt("本地 agent 名称", "办公室 Ollama") || ""; const result = await post({ action: "create_agent", name }); setAgentToken(String(result?.token || "")); await load(jobId); });

  if (!data) return <section className={`panel data-state ${initialLoading ? "" : "data-state-error"}`} role={initialLoading ? "status" : "alert"} aria-live={initialLoading ? "polite" : undefined}>{initialLoading ? <><span className="state-spinner" /><strong>正在加载 SKU AI 标注工作台</strong></> : <><span className="state-symbol">!</span><strong>SKU AI 标注工作台加载失败</strong><p>{error || "暂时无法读取数据，请稍后重试"}</p><button className="secondary-button" onClick={() => void loadInitial()}>重试</button></>}</section>;
  const currentRun = data.validationRuns.find((run) => !category || run.category === category);
  const normalizedCategoryQuery = categoryQuery.trim().toLocaleLowerCase("zh-CN");
  const filteredCategories = data.categories.filter((item) => !normalizedCategoryQuery || item.value.toLocaleLowerCase("zh-CN").includes(normalizedCategoryQuery));
  const categoryTotal = data.categories.reduce((sum, item) => sum + item.count, 0);
  const currentResults = currentRun ? data.validationResults.filter((item) => item.runId === currentRun.id) : [];
  const importableItems = data.items.filter((item) => importableIds.has(item.id));
  const allChecked = importableItems.length > 0 && importableItems.every((item) => drafts[item.id]?.selected);
  const serverSelectedOnPage = importableItems.filter((item) => item.selected).length;
  const draftSelectedOnPage = importableItems.filter((item) => drafts[item.id]?.selected).length;
  const selectedCount = Math.max(0, data.selection.scopeSelectedCount - serverSelectedOnPage + draftSelectedOnPage);
  const filteredSelectedCount = Math.max(0, data.selection.filteredSelectedCount - serverSelectedOnPage + draftSelectedOnPage);
  const allFilteredChecked = data.selection.filteredReviewableCount > 0 && filteredSelectedCount === data.selection.filteredReviewableCount;
  const filteredSelectionBlockReason = !canEdit ? "当前账号没有人工复核权限"
    : !reviewScopeReady ? "正在应用复核筛选，请等待列表更新"
      : busy !== "" ? "正在处理其他操作，请稍候"
        : !data.selection.filteredReviewableCount ? "当前筛选中没有已完成识别且细分品类有效的候选，可调整筛选或刷新结果"
          : "";
  const hasDirtyDrafts = data.items.some((item) => { const draft = drafts[item.id]; return Boolean(draft) && (draft.segment !== (item.reviewedSegment || item.aiSegment) || draft.price !== yuanInput(item.reviewedImagePriceCents) || draft.selected !== item.selected); });
  const visibleJobs = data.jobs.filter((item) => !category || item.category === category);
  const createBlockReason = createJobBlockReason();
  const formConcurrency = concurrencyFor(category, executor);
  const formConcurrencyKey = annotationConcurrencyKey(category, executor);
  const currentJobExecutor: MarketAnnotationExecutor = currentJob?.executor === "local" ? "local" : "cloud";
  const currentJobConcurrency = currentJob ? concurrencyFor(currentJob.category, currentJobExecutor) : defaultMarketAnnotationConcurrency("cloud");
  const currentJobConcurrencyKey = currentJob ? annotationConcurrencyKey(currentJob.category, currentJobExecutor) : "";
  const reviewSegments = [...new Set(data.taxonomy.filter((item) => !reviewCategories.length || reviewCategories.includes(item.category)).map((item) => item.value))];
  const reviewCategoryLabel = !reviewCategories.length ? "全部三级类目" : reviewCategories.length === 1 ? reviewCategories[0]! : `已选 ${reviewCategories.length} 个三级类目`;
  const toggleReviewCategory = (value: string, checked: boolean) => {
    setItemSegments([]);
    setItemPage(1);
    setReviewCategories((current) => checked ? [...new Set([...current, value])] : current.filter((item) => item !== value));
  };
  const reviewSegmentsFor = (item: Item) => {
    if (!reviewableIds.has(item.id)) return [...new Set([item.reviewedSegment || item.aiSegment, ...(item.reviewSegments ?? [])])].filter(Boolean);
    return item.reviewSegments ?? [];
  };

  return <div className="market-annotation-module">
    {progressError && <div className="market-feedback error" role="alert"><span>{progressError}</span><button className="secondary-button" disabled={!jobId} onClick={() => void loadJobProgress(jobId).catch(() => undefined)}>重新读取进度</button></div>}
    {reviewError && <div className="market-feedback error" role="alert"><span>{reviewError}</span><button className="secondary-button" disabled={reviewLoading} onClick={() => void loadReview().catch(() => undefined)}>重新读取复核列表</button></div>}
    {(error || notice) && <div className={"market-feedback " + (error ? "error" : "success")} role={error ? "alert" : "status"}><span>{error || notice}</span></div>}
    <section className={`panel annotation-hero ${embedded ? "annotation-hero-embedded" : ""}`}><div><span className="eyebrow">HUMAN-IN-THE-LOOP VISION</span><h2>{embedded ? "SKU 数据库 · AI 标注与入库" : "市场 SKU 细分品类 AI 标注"}</h2><p>云端视觉为默认执行器；同一 SKUID 与图片已入库的标准售价会自动沿用，新图片的 AI 候选必须人工复核后才能批量入库。</p></div><div className="annotation-progress"><strong>{currentJob ? currentJob.completedCount + "/" + currentJob.totalCount : "尚未创建"}</strong><span>{currentJob?.status || "等待任务"}</span></div></section>

    <section className="panel annotation-task-card">
      <div className="section-header">
        <div>
          <h3>1. 创建与执行任务</h3>
          <p>新图片按类目配置并发逐张识别。模型或网络偶发异常时先温和降低运行并发并短暂冷却，供应商限流时阶梯减半；连接稳定后每成功 3 张逐步恢复，不再因单次超时直接降到 1。</p>
        </div>
      </div>

      <div className="annotation-task-setup">
        <div className="annotation-task-fields">
          <label><span>筛选三级类目</span><input value={categoryQuery} onChange={(event) => setCategoryQuery(event.target.value)} placeholder="输入类目关键词" /></label>
          <label><span>三级类目</span><select value={category} onChange={(event) => chooseCategory(event.target.value)}><option value="">全部三级类目（{categoryTotal}）</option>{filteredCategories.map((item) => <option key={item.value} value={item.value}>{item.value}（总 {item.count} · 可新建 {item.candidateCount === null ? candidateCountsStatus === "error" ? "读取失败" : "计算中…" : item.candidateCount}）</option>)}{normalizedCategoryQuery && !filteredCategories.length && <option disabled>没有匹配的三级类目</option>}</select></label>
          <label><span>执行器</span><select value={executor} onChange={(event) => setExecutor(event.target.value as "cloud" | "local")}><option value="cloud">云端视觉（默认）</option><option value="local">本地 Ollama（可选容灾）</option></select></label>
          {executor === "cloud" ? <label><span>enabled vision 模型</span><select value={visionModelId} onChange={(event) => setVisionModelId(event.target.value)}>{data.models.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.modelName}</option>)}</select></label> : <label><span>Ollama 模型名</span><input value={localModelName} onChange={(event) => setLocalModelName(event.target.value)} /></label>}
          <label><span>任务上限</span><input type="number" min={1} max={MARKET_ANNOTATION_JOB_LIMITS.maximum} value={limit} onChange={(event) => setLimit(Number(event.target.value))} /><small>单个任务最多 10,000 条，可随时暂停并继续。</small></label>
        </div>

        <aside className="annotation-concurrency-card">
          <div><span>新任务并发</span><strong>{category || "请先选择三级类目"}</strong><small>{executor === "cloud" ? "云端视觉" : "本地 Ollama"} · 按类目记忆</small></div>
          <label><span>同时调用模型的路数</span><div className="annotation-concurrency-control"><input aria-label="AI 标注模型并发数" type="number" min={MARKET_ANNOTATION_CONCURRENCY_LIMITS.minimum} max={MARKET_ANNOTATION_CONCURRENCY_LIMITS.maximum} value={formConcurrency} disabled={!canEdit || !category || savingConcurrencyKey === formConcurrencyKey} onChange={(event) => setConcurrencyDrafts((current) => ({ ...current, [formConcurrencyKey]: Number(event.target.value) }))} /><button className="secondary-button" disabled={!canEdit || !category || !isValidAnnotationConcurrency(formConcurrency) || savingConcurrencyKey !== ""} onClick={() => void saveConcurrency(category, executor)}>{savingConcurrencyKey === formConcurrencyKey ? "保存中…" : "保存"}</button></div></label>
          <small>范围 1–50。{executor === "cloud" ? "云端建议 10–20；过高易触发限流并计入失败。" : "本地 Ollama 建议 1，避免显存争抢。"}</small>
        </aside>
      </div>

      <div className="annotation-task-footer">
        {createBlockReason
          ? <small className="orange-text">无法创建任务：{createBlockReason}</small>
          : <small>{compatibleExistingJob ? `将切换并续跑兼容任务，剩余推理 ${compatibleExistingJob.remainingInferenceCount} 条；完成后可创建下一批` : `当前可新建 ${selectedCategorySummary?.candidateCount ?? 0} 条，单批最多 ${MARKET_ANNOTATION_JOB_LIMITS.maximum} 条`} · 激活 Prompt：v{activePrompt!.version}</small>}
        {candidateCountsStatus === "error" && <button className="secondary-button" disabled={busy !== ""} onClick={retryCandidateCounts}>重新读取候选数量</button>}
        <button className="primary-button" disabled={busy !== "" || candidateCountsStatus !== "ready"} onClick={createJob}>{busy === "create-job" ? (compatibleExistingJob ? (compatibleExistingJob.executor === "cloud" ? "正在恢复并唤醒…" : "正在恢复任务…") : "正在创建任务（候选较多时需几十秒）…") : candidateCountsStatus === "loading" || candidateCountsStatus === "idle" ? "正在计算可新建候选…" : compatibleExistingJob ? (compatibleExistingJob.executor === "cloud" ? "恢复兼容任务并续跑" : "恢复兼容任务") : categoryReviewReadyJob ? "创建下一批任务" : "创建任务"}</button>
      </div>

      {currentJob && <div className="annotation-current-run">
        <div className="annotation-current-run-summary"><span>当前任务</span><strong>{currentJob.category}</strong><small>{currentJob.executor} · {currentJob.status} · {currentJob.completedCount}/{currentJob.totalCount}</small>{cloudProgress?.job.id === currentJob.id && <small>有效租约 {cloudProgress.activeClaims} · 唯一推理单元剩余 {cloudProgress.remainingInferenceUnits}/{cloudProgress.uniqueInferenceUnits}</small>}{currentJob.executor === "cloud" && <small>云端后台：{currentCloudRunHasUnknownResult ? "结果待核验（已暂停）" : currentCloudRunIsRunning ? `运行中（当前 ${currentCloudRun!.runConcurrency}/${currentCloudRun!.targetConcurrency} 路）` : currentCloudRunHasUnfinishedItems ? currentCloudRun?.state === "completed" ? "已停止（仍有未完成项，可恢复）" : "已暂停" : "已完成"}</small>}{cloudProgress?.job.id === currentJob.id && cloudProgress.performance.measuredCount > 0 && <small>最近 {cloudProgress.performance.measuredCount} 张平均：总耗时 {duration(cloudProgress.performance.averageTotalInferenceMs)} · 模型 {duration(cloudProgress.performance.averageModelCallMs)} · 取图 {duration(cloudProgress.performance.averageImageLoadMs)} · 图片处理 {duration(cloudProgress.performance.averageImagePrepareMs)} · 输入 {bytes(cloudProgress.performance.averageModelInputBytes)}</small>}{currentCloudRun?.lastFailureMessage && <small>最近异常：{currentCloudRun.lastFailureMessage}</small>}</div>
        <label><span>当前任务并发（可运行中调整）</span><div className="annotation-concurrency-control"><input aria-label="当前 AI 标注任务模型并发数" type="number" min={MARKET_ANNOTATION_CONCURRENCY_LIMITS.minimum} max={MARKET_ANNOTATION_CONCURRENCY_LIMITS.maximum} value={currentJobConcurrency} disabled={!canEdit || savingConcurrencyKey === currentJobConcurrencyKey} onChange={(event) => setConcurrencyDrafts((current) => ({ ...current, [currentJobConcurrencyKey]: Number(event.target.value) }))} /><button className="secondary-button" disabled={!canEdit || !isValidAnnotationConcurrency(currentJobConcurrency) || savingConcurrencyKey !== ""} onClick={() => void saveConcurrency(currentJob.category, currentJobExecutor)}>{savingConcurrencyKey === currentJobConcurrencyKey ? "保存中…" : "保存并应用"}</button></div></label>
        {currentJob.executor === "cloud" ? currentCloudRunHasUnfinishedItems ? <div className="annotation-current-run-actions"><button className="primary-button" disabled={!canEdit || busy !== ""} onClick={pumpCloud}>{busy === "run-cloud" ? (currentCloudRunIsRunning ? `正在安全唤醒云端后台（目标并发 ${currentJobConcurrency}）…` : `正在交给云端后台（目标并发 ${currentJobConcurrency}）…`) : currentCloudRun?.state === "completed" ? "恢复剩余识别" : currentCloudRunIsRunning ? `重新唤醒云端后台（并发 ${currentJobConcurrency}）` : `开始/恢复云端后台识别（并发 ${currentJobConcurrency}）`}</button><button className="secondary-button" disabled={!canEdit || busy !== "" || !currentCloudRunIsRunning} onClick={pauseCloud}>{busy === "pause-cloud" ? "正在暂停…" : "完成当前条后暂停"}</button></div> : <small className="annotation-current-run-local">{currentJobSettledMessage}</small> : <small className="annotation-current-run-local">本地任务由 Ollama agent 主动领取；保存后新领取会立即按该并发执行。</small>}
      </div>}

      <div className="annotation-job-heading"><strong>任务记录</strong><small>{visibleJobs.length} 个任务；推理已结束或已全部入库的任务可由管理员归档，运行中的任务不能删除，正式入库结果不会受影响</small></div>
      <div className="annotation-job-list">{visibleJobs.map((item) => <div className={`annotation-job-entry ${jobId === item.id ? "active" : ""}`} key={item.id}><button className="annotation-job-select" onClick={() => { dirtyDraftIdsRef.current.clear(); setJobId(item.id); }}><strong>{item.category}</strong><span>{item.executor} · 并发 {concurrencyFor(item.category, item.executor === "local" ? "local" : "cloud")} · {item.status}</span><small>{item.completedCount}/{item.totalCount} · 失败 {item.failedCount} · 入库 {item.committedCount}</small></button>{["review_ready", "committed"].includes(item.status) && <button className="annotation-job-delete" disabled={!isAdmin || busy !== ""} title={isAdmin ? "归档任务记录；正式入库结果、任务明细、回执和审计都会保留" : "仅管理员可归档已结束任务"} onClick={() => void deleteJob(item)}>{busy === "delete-job" ? "归档中…" : item.status === "committed" ? "删除记录" : "归档旧任务"}</button>}</div>)}</div>
    </section>

    <section className="panel annotation-review-card data-refresh-region" aria-busy={reviewLoading}>
      <div className="section-header">
        <div><h3>2. 人工复核与批量入库</h3><p>汇总当前类目全部历史 AI 标注任务；支持跨任务筛选、复核、全选和分组入库。</p></div>
        <div className="annotation-actions"><button className="secondary-button" disabled={!canEdit || !reviewScopeReady || busy !== ""} onClick={() => void saveReview()}>保存复核</button><button className="primary-button" disabled={!isAdmin || !reviewScopeReady || !selectedCount || busy !== ""} onClick={commit}>批量入库（{selectedCount}）</button></div>
      </div>
      {Number(data.selection.staleSelectedCount ?? 0) > 0 && <p className="orange-text" role="status">原勾选中有 {data.selection.staleSelectedCount} 条候选的图片或对应月份数据已变化，已排除本次入库；原复核记录保留，需按当前图片重新识别并复核。</p>}
      <div className="annotation-review-toolbar">
        <div className="annotation-review-category-filter"><span>三级类目（可多选）</span><details><summary aria-label="AI 标注三级类目多选">{reviewCategoryLabel}</summary><div className="annotation-review-category-menu"><button type="button" disabled={!reviewCategories.length} onClick={() => { setReviewCategories([]); setItemSegments([]); setItemPage(1); }}>全部三级类目</button>{data.reviewCategories.map((item) => <label key={item.value}><input type="checkbox" checked={reviewCategories.includes(item.value)} onChange={(event) => toggleReviewCategory(item.value, event.target.checked)} /><span>{item.value}</span><small>{item.jobCount} 个任务 / {item.recordCount} 条</small></label>)}</div></details></div>
        <AnnotationMultiFilter label="细分品类" allLabel="全部细分品类" values={itemSegments} options={reviewSegments.map((value) => ({ value, label: value }))} onChange={(values) => { setItemSegments(values); setItemPage(1); }} />
        <AnnotationMultiFilter label="入库状态" allLabel="全部状态" values={storageStatuses} options={[{ value: "pending", label: "待入库" }, { value: "committed", label: "已入库" }]} onChange={(values) => { setStorageStatuses(values as Array<"pending" | "committed">); setItemPage(1); }} />
        <AnnotationMultiFilter label="AI 标注识别来源" allLabel="全部 AI 结果" values={recognitionSources} options={[{ value: "ai", label: "AI 已识别" }, { value: "non_ai", label: "未生成 AI 结果（含失败）" }]} onChange={(values) => { setRecognitionSources(values as Array<"ai" | "non_ai">); setItemPage(1); }} />
        <label className="annotation-select-page"><input type="checkbox" checked={allChecked} disabled={!canEdit || !reviewScopeReady || !importableItems.length || busy !== ""} onChange={(event) => {
          const selected = event.target.checked;
          importableIds.forEach((id) => dirtyDraftIdsRef.current.add(id));
          setDrafts((current) => Object.fromEntries(Object.entries(current).map(([id, draft]) => [id, importableIds.has(id) ? { ...draft, selected } : draft])));
          setNotice(selected ? `当前页已选择 ${importableItems.length} 条可入库项` : "已清空当前页选择");
        }} />全选当前页可入库项（{importableItems.length} 条）</label>
        <label className="annotation-select-page annotation-select-filtered" title={filteredSelectionBlockReason || "选择所有页面中符合当前筛选的可入库候选"}><input type="checkbox" checked={allFilteredChecked} disabled={Boolean(filteredSelectionBlockReason)} onChange={(event) => void setFilteredSelection(event.target.checked)} />全选筛选结果（跨页 {data.selection.filteredReviewableCount} 条）</label>
        <small>{filteredSelectionBlockReason || "已完成识别的候选可先复核入库，无需等待整项任务结束；保留任务 Prompt 与当前字典校验。跨页最多 50,000 条，入库每批处理 500 条。"}</small>
        <button className="secondary-button" disabled={busy !== "" || reviewLoading} onClick={() => void loadReview().catch(() => undefined)}>{reviewLoading ? "刷新中…" : "刷新复核结果"}</button>
        {!reviewScopeReady && <button className="secondary-button" disabled={busy !== ""} onClick={() => void loadReview(true).catch(() => undefined)}>重新加载筛选结果</button>}
        <div className="market-view-switch" role="tablist" aria-label="AI 标注展示方式"><button type="button" role="tab" id="annotation-review-tab-list" aria-controls="annotation-review-panel-list" aria-selected={reviewView === "list"} tabIndex={reviewView === "list" ? 0 : -1} className={reviewView === "list" ? "active" : ""} onClick={() => setReviewView("list")} onKeyDown={handleRovingTabKey}>列表</button><button type="button" role="tab" id="annotation-review-tab-gallery" aria-controls="annotation-review-panel-gallery" aria-selected={reviewView === "gallery"} tabIndex={reviewView === "gallery" ? 0 : -1} className={reviewView === "gallery" ? "active" : ""} onClick={() => setReviewView("gallery")} onKeyDown={handleRovingTabKey}>大图</button></div>
      </div>
      {recognitionSources.includes("non_ai") && <p className="annotation-filter-note">此筛选不会调用模型；它显示尚未生成 AI 结果的候选，包括等待识别和此前识别失败的记录。</p>}
      <footer className="annotation-pagination annotation-review-pagination">
        <span>{reviewCategoryLabel}：已汇总 {data.reviewSummary.jobCount} 个任务 · 筛选后 {data.itemPagination.total} 条任务记录 · {data.reviewSummary.uniqueCandidateCount} 个不重复候选</span>
        <label>每页 <select aria-label="AI 标注每页条数" value={itemPageSize} disabled={hasDirtyDrafts} title={hasDirtyDrafts ? "请先保存当前页编辑" : ""} onChange={(event) => { setItemPageSize(Number(event.target.value)); setItemPage(1); }}><option value={20}>20 条</option><option value={50}>50 条</option><option value={100}>100 条</option></select></label>
        <button disabled={itemPage <= 1 || hasDirtyDrafts} title={hasDirtyDrafts ? "请先保存当前页编辑" : ""} onClick={() => setItemPage((page) => Math.max(1, page - 1))}>上一页</button>
        <label>第 <select aria-label="AI 标注页码" value={itemPage} disabled={hasDirtyDrafts} title={hasDirtyDrafts ? "请先保存当前页编辑" : ""} onChange={(event) => setItemPage(Number(event.target.value))}>{Array.from({ length: data.itemPagination.pageCount }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select> / {data.itemPagination.pageCount} 页</label>
        <button disabled={itemPage >= data.itemPagination.pageCount || hasDirtyDrafts} title={hasDirtyDrafts ? "请先保存当前页编辑" : ""} onClick={() => setItemPage((page) => Math.min(data.itemPagination.pageCount, page + 1))}>下一页</button>
      </footer>
      {reviewView === "list" ? <div className="data-table-wrap annotation-review-table-wrap" role="tabpanel" id="annotation-review-panel-list" aria-labelledby="annotation-review-tab-list"><table className="data-table annotation-review-table"><thead><tr><th>选择</th><th>大图 / 实际来源</th><th>SKU / 商品链接</th><th>识别批次</th><th>AI 结果</th><th>人工细分品类</th><th>主图价格（元）</th><th>置信度 / 状态</th></tr></thead><tbody>{data.items.map((item) => {
        const draft = drafts[item.id];
        const reviewable = reviewableIds.has(item.id);
        const importable = importableIds.has(item.id);
        const href = annotationProductHref(item.skuCode);
        const imageUrl = item.resolvedImageUrl || item.sourceImageUrl;
        const itemJob = data.jobs.find((entry) => entry.id === item.jobId);
        const itemSegments = reviewSegmentsFor(item);
        return <tr key={item.id}>
          <td><input type="checkbox" checked={importable && (draft?.selected ?? false)} disabled={!canEdit || !reviewScopeReady || !importable} title={item.snapshotValid === false ? "图片或对应月份数据已变化，不能将旧图结果入库" : reviewable && !importable ? "任务已归档，或细分品类不符合任务 Prompt 与当前字典" : undefined} onChange={(event) => updateDraft(item.id, { selected: event.target.checked })} /></td>
          <td>{imageUrl ? (href ? <a className="annotation-image-link" href={href} target="_blank" rel="noreferrer" aria-label={`打开商品 ${item.productName || item.skuCode}`}><img className="annotation-image" src={imageUrl} alt={item.productName || item.skuCode} loading="lazy" /></a> : <img className="annotation-image" src={imageUrl} alt={item.productName || item.skuCode} loading="lazy" />) : <span className="annotation-no-image">无图</span>}<small className={item.imageSource === "imgzone" ? "green-text" : "orange-text"}>{item.imageSource === "imgzone" ? "imgzone 大图" : item.imageSource === "n5" ? "n5 回退" : "未取到安全图片"}</small></td>
          <td>{href ? <a className="annotation-product-link" href={href} target="_blank" rel="noreferrer"><strong>{item.skuCode}</strong><small title={item.productName}>{item.productName || "未命名商品"}</small><span>打开商品链接 ↗</span></a> : <><strong>{item.skuCode}</strong><small title={item.productName}>{item.productName}</small></>}<code>{item.candidateId}</code></td>
          <td><strong>{itemJob?.createdAt?.slice(0, 10) || item.createdAt.slice(0, 10)}</strong><small>{itemJob?.executor || "—"} · {itemJob?.status || "—"}</small><code>{item.jobId.slice(0, 18)}…</code></td>
          <td><strong>{item.aiSegment || "—"}</strong><small>{annotationResultMessage(item)}</small>{annotationTimingMessage(item) && <small>{annotationTimingMessage(item)}</small>}</td>
          <td><select value={draft?.segment ?? ""} disabled={!canEdit || !reviewable} onChange={(event) => updateDraft(item.id, { segment: event.target.value })}><option value="">请选择</option>{itemSegments.map((value) => <option key={value}>{value}</option>)}</select></td>
          <td><input type="number" min={0} step="0.01" value={draft?.price ?? ""} disabled={!canEdit || !reviewable} onChange={(event) => updateDraft(item.id, { price: event.target.value })} /><small>{item.reviewPriceSource === "history_same_image" ? `历史同图：${money(item.reviewedImagePriceCents)}` : `AI：${money(item.aiImagePriceCents)}`}</small></td>
          <td><strong>{item.aiConfidenceBps === null ? "—" : (item.aiConfidenceBps / 100).toFixed(1) + "%"}</strong><small>{annotationRecognitionLabel(item)} · {item.status} · v{item.version}</small></td>
        </tr>;
      })}{!data.items.length && <tr><td colSpan={8}><div className="table-state" role="status">当前筛选范围没有候选项。</div></td></tr>}</tbody></table></div> : <div className="annotation-review-gallery" role="tabpanel" id="annotation-review-panel-gallery" aria-labelledby="annotation-review-tab-gallery">{data.items.map((item) => {
        const draft = drafts[item.id];
        const reviewable = reviewableIds.has(item.id);
        const importable = importableIds.has(item.id);
        const href = annotationProductHref(item.skuCode);
        const imageUrl = item.resolvedImageUrl || item.sourceImageUrl;
        const itemJob = data.jobs.find((entry) => entry.id === item.jobId);
        const itemSegments = reviewSegmentsFor(item);
        return <article key={item.id}>
          <label className="annotation-gallery-select"><input type="checkbox" checked={importable && (draft?.selected ?? false)} disabled={!canEdit || !reviewScopeReady || !importable} title={item.snapshotValid === false ? "图片或对应月份数据已变化，不能将旧图结果入库" : reviewable && !importable ? "任务已归档，或细分品类不符合任务 Prompt 与当前字典" : undefined} onChange={(event) => updateDraft(item.id, { selected: event.target.checked })} />{item.status === "committed" ? "已入库" : importable ? "加入本次入库" : "暂不可入库"}</label>
          <div className="annotation-gallery-image">{imageUrl ? (href ? <a href={href} target="_blank" rel="noreferrer" aria-label={`打开商品 ${item.productName || item.skuCode}`}><img src={imageUrl} alt={item.productName || item.skuCode} loading="lazy" /></a> : <img src={imageUrl} alt={item.productName || item.skuCode} loading="lazy" />) : <span>无图</span>}</div>
          {href ? <a className="annotation-product-link" href={href} target="_blank" rel="noreferrer"><h4>{item.productName || item.skuCode}</h4><span>打开商品链接 ↗</span></a> : <h4>{item.productName || item.skuCode}</h4>}
          <small>{item.skuCode} · {annotationRecognitionLabel(item)} · {item.imageSource}</small>
          <small>批次：{itemJob?.createdAt?.slice(0, 10) || item.createdAt.slice(0, 10)} · {itemJob?.status || "—"}</small>
          <p>{annotationResultMessage(item)}</p>{annotationTimingMessage(item) && <small>{annotationTimingMessage(item)}</small>}
          <label><span>细分品类</span><select value={draft?.segment ?? ""} disabled={!canEdit || !reviewable} onChange={(event) => updateDraft(item.id, { segment: event.target.value })}><option value="">请选择</option>{itemSegments.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label><span>主图价格（元）</span><input type="number" min={0} step="0.01" value={draft?.price ?? ""} disabled={!canEdit || !reviewable} onChange={(event) => updateDraft(item.id, { price: event.target.value })} /><small>{item.reviewPriceSource === "history_same_image" ? `历史同图默认：${money(item.reviewedImagePriceCents)}` : `AI：${money(item.aiImagePriceCents)}`}</small></label>
          <footer><strong>{item.aiSegment || "未识别"}</strong><span>{item.aiConfidenceBps === null ? "—" : (item.aiConfidenceBps / 100).toFixed(1) + "%"}</span></footer>
        </article>;
      })}{!data.items.length && <div className="table-state" role="status">当前筛选范围没有候选项。</div>}</div>}
    </section>

    <section className="annotation-two-column"><article className="panel annotation-prompt-card"><div className="section-header"><div><h3>3. Prompt 版本与自动进化</h3><p>正文与枚举始终明文可见；编辑只创建不可变子版本。未使用的草稿可以删除，激活及归档版本永久保留。</p></div></div>{error && <div className="market-feedback error" role="alert">{error}</div>}
      <div className="annotation-prompt-history">{data.prompts.filter((item) => !category || item.category === category).map((item) => <div key={item.id} className="annotation-prompt-version"><button className={`annotation-prompt-select ${selectedPrompt?.id === item.id ? "active" : ""}`} onClick={() => choosePrompt(item)}><strong>v{item.version} · {item.status}</strong><span>{item.source}</span><small>{item.id}</small></button>{isAdmin && item.status === "draft" && <button className="annotation-prompt-delete" disabled={busy !== ""} title={`删除 v${item.version} 草稿`} onClick={() => void deletePrompt(item)}>删除</button>}</div>)}</div>
      <label><span>细分品类枚举（由细分品类设置统一维护）</span><textarea value={segmentsText} readOnly /></label><label><span>Prompt 正文</span><textarea className="annotation-prompt-body" value={promptBody} onChange={(event) => setPromptBody(event.target.value)} placeholder="写明视觉分类规则、价格识别口径和严格 JSON 输出" /></label><label><span>版本说明</span><input value={changeNote} onChange={(event) => setChangeNote(event.target.value)} /></label>
      <div className="annotation-form-row"><label><span>AI 文本模型</span><select value={textModelId} onChange={(event) => setTextModelId(event.target.value)}>{data.textModels.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label><span>抽样数</span><input type="number" min={1} max={500} value={sampleCount} onChange={(event) => setSampleCount(Number(event.target.value))} /></label><label><span>固定 seed</span><input value={seed} onChange={(event) => setSeed(event.target.value)} /></label></div>
      <div className="annotation-actions"><button className="secondary-button" disabled={!canEdit || !category || busy !== ""} onClick={() => void savePrompt("manual")}>保存人工子版本</button><button className="secondary-button" disabled={!canEdit || !category || !textModelId || busy !== ""} onClick={() => void savePrompt("generate")}>AI 生成</button><button className="secondary-button" disabled={!canEdit || !category || !selectedPrompt || !textModelId || !visionModelId || busy !== ""} onClick={evolve}>AI 进化并测试</button><button className="secondary-button" disabled={!canEdit || !category || !selectedPrompt || !visionModelId || busy !== ""} onClick={() => void testPrompt()}>冻结抽样测试</button>{selectedPrompt?.status === "active" ? <button className="primary-button" disabled>当前激活版本</button> : selectedPrompt && <button className="primary-button" disabled={!isAdmin || busy !== ""} onClick={() => void activate(selectedPrompt, selectedPrompt.status === "archived")}>{selectedPrompt.status === "archived" ? "回滚到此版" : "激活此版"}</button>}</div>
    </article>
    <article className="panel annotation-validation-card"><div className="section-header"><div><h3>冻结抽样验证</h3><p>默认 50 条，按品类分层并以 seed+hash 冻结；逐条错例永久保留。</p></div></div>{currentRun ? <><div className="annotation-validation-summary"><strong>{currentRun.status}</strong><span>{currentRun.sampleCount} 条 · hash {currentRun.sampleHash?.slice(0, 12)}</span><em className={currentRun.gate?.passed ? "green-text" : "orange-text"}>{currentRun.gate?.passed ? "门禁通过" : (currentRun.gate?.reasons || []).join("；") || "待完成"}</em></div><div className="annotation-validation-errors">{currentResults.filter((item) => !item.isCorrect || item.errorMessage).slice(0, 80).map((item) => <div key={item.id}><strong>{item.skuCode} · {item.goldSegment} → {item.predictedSegment || "失败"}</strong><small>{item.productName}</small><span>{item.errorMessage}</span></div>)}</div></> : <p className="soft-text">尚无冻结验证运行。</p>}</article>
    </section>

    <section className="panel annotation-agent-card"><div className="section-header"><div><h3>4. 本地 Ollama 容灾 runner</h3><p>云端模型任务由后台定时接管，关闭浏览器可继续；本机部署需要电脑和运营系统保持运行。本机 runner 只用于 Ollama 等 localhost 模型，通过一次性 token 主动领取带租约的任务。</p></div>{isAdmin && <button className="secondary-button" onClick={createAgent}>创建 agent</button>}</div>{agentToken && <div className="annotation-token"><strong>仅显示一次</strong><code>{agentToken}</code></div>}<pre>TERUISI_SITE_URL=https://你的站点{`\n`}TERUISI_ANNOTATION_AGENT_TOKEN=创建时的一次性token{`\n`}OLLAMA_BASE_URL=http://127.0.0.1:11434{`\n`}npm run market:annotation-agent</pre><div className="annotation-agent-list">{data.agents.map((agent) => <div key={agent.id}><strong>{agent.name}</strong><span>{agent.status} · 最近心跳 {agent.lastSeenAt || "从未"}</span>{isAdmin && agent.status === "enabled" && <button className="row-action danger" onClick={() => void act("revoke", async () => { await post({ action: "revoke_agent", agentId: agent.id }); await load(jobId); })}>撤销</button>}</div>)}</div></section>
  </div>;
}
