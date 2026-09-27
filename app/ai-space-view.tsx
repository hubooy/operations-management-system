"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import Image from "next/image";

type SceneId = "product_main" | "product_detail" | "promotion";
type ImageSize = "1024x1024" | "1024x1536" | "1536x1024";
type SpaceScene = { id: SceneId; label: string; description: string; defaultSize: ImageSize };
type SpaceTemplate = {
  id: string;
  scene: SceneId;
  name: string;
  size: ImageSize;
  modelProfileId: string | null;
  version: number;
  isEnabled: boolean;
  isDefault: boolean;
};
type SpaceProfile = { id: string; name: string; modelName: string; lastSuccessAt: string | null };
type SpaceMeta = {
  scenes: SpaceScene[];
  templates: SpaceTemplate[];
  profiles: SpaceProfile[];
  limits: { maximumImages: number; maximumDailyImagesPerOwner: number; maximumActiveJobsPerOwner: number };
  permissions: { canGenerate: boolean; canManage: boolean };
  safetyNotice: string;
};
type SpaceAsset = {
  id: string;
  jobId: string;
  itemId: string;
  scene: SceneId;
  productName: string;
  brand: string;
  sku: string;
  mimeType: string;
  byteSize: number;
  width: number;
  height: number;
  favorite: boolean;
  generatedByAi: true;
  reviewRequired: true;
  contentUrl: string;
  createdAt: string;
};
type SpaceJobItem = {
  id: string;
  ordinal: number;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  errorCode: string;
  errorMessage: string;
  asset: SpaceAsset | null;
};
type SpaceJob = {
  id: string;
  scene: SceneId;
  templateName: string;
  modelProfileName: string;
  modelName: string;
  productName: string;
  brand: string;
  sku: string;
  size: ImageSize;
  requestedCount: number;
  succeededCount: number;
  failedCount: number;
  cancelledCount: number;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
  cancelRequested: boolean;
  errorMessage: string;
  createdAt: string;
  updatedAt: string;
  items: SpaceJobItem[];
};
type Pagination = { page: number; pageSize: number; total: number; returned: number; hasMore: boolean; truncated: boolean };
type SpaceDraft = {
  scene: SceneId;
  templateId: string;
  modelProfileId: string;
  productName: string;
  brand: string;
  sku: string;
  sellingPoints: string;
  additionalInstructions: string;
  count: number;
};

const emptyPagination: Pagination = { page: 1, pageSize: 20, total: 0, returned: 0, hasMore: false, truncated: false };

function initialDraft(): SpaceDraft {
  return {
    scene: "product_main",
    templateId: "",
    modelProfileId: "",
    productName: "",
    brand: "",
    sku: "",
    sellingPoints: "",
    additionalInstructions: "",
    count: 1,
  };
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null) as (T & { error?: string }) | null;
  if (!response.ok) throw new Error(payload?.error || fallback);
  if (!payload) throw new Error(fallback);
  return payload;
}

function localDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? `${value.replace(" ", "T")}Z` : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

const statusText: Record<SpaceJob["status"], string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

export default function AiSpaceView({
  onOpenManagement,
}: {
  onOpenManagement: () => void;
}) {
  const [meta, setMeta] = useState<SpaceMeta | null>(null);
  const [jobs, setJobs] = useState<SpaceJob[]>([]);
  const [jobsPagination, setJobsPagination] = useState<Pagination>(emptyPagination);
  const [assets, setAssets] = useState<SpaceAsset[]>([]);
  const [assetsPagination, setAssetsPagination] = useState<Pagination>({ ...emptyPagination, pageSize: 24 });
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [draft, setDraft] = useState<SpaceDraft>(() => initialDraft());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [syncWarning, setSyncWarning] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const assetsPageControllerRef = useRef<AbortController | null>(null);
  const assetsPageGenerationRef = useRef(0);
  const favoritesOnlyRef = useRef(false);
  const jobsRef = useRef<SpaceJob[]>([]);
  const submissionRef = useRef<{ signature: string; clientRequestId: string; acceptedJobId?: string } | null>(null);

  const upsertAcceptedJob = useCallback((job: SpaceJob, replayed: boolean) => {
    const existed = jobsRef.current.some((item) => item.id === job.id);
    const nextJobs = [job, ...jobsRef.current.filter((item) => item.id !== job.id)].slice(0, 20);
    jobsRef.current = nextJobs;
    setJobs(nextJobs);
    setJobsPagination((current) => {
      const total = existed
        ? Math.max(current.total, nextJobs.length)
        : Math.max(nextJobs.length, current.total + (replayed ? 0 : 1));
      return {
        ...current,
        page: 1,
        returned: nextJobs.length,
        total,
        hasMore: total > nextJobs.length,
        truncated: total > nextJobs.length,
      };
    });
  }, []);

  const alignDraftWithMeta = useCallback((nextMeta: SpaceMeta) => {
    setDraft((current) => {
      const sceneTemplates = nextMeta.templates.filter((item) => item.scene === current.scene && item.isEnabled);
      const template = sceneTemplates.find((item) => item.id === current.templateId)
        ?? sceneTemplates.find((item) => item.isDefault)
        ?? sceneTemplates[0];
      const profileId = nextMeta.profiles.some((item) => item.id === current.modelProfileId)
        ? current.modelProfileId
        : template?.modelProfileId && nextMeta.profiles.some((item) => item.id === template.modelProfileId)
          ? template.modelProfileId
          : nextMeta.profiles[0]?.id ?? "";
      return { ...current, templateId: template?.id ?? "", modelProfileId: profileId };
    });
  }, []);

  const loadWorkspace = useCallback(async (options: { quiet?: boolean; favorites?: boolean } = {}) => {
    const generation = ++generationRef.current;
    controllerRef.current?.abort();
    assetsPageControllerRef.current?.abort();
    assetsPageGenerationRef.current += 1;
    const controller = new AbortController();
    controllerRef.current = controller;
    if (options.quiet) setRefreshing(true);
    else setLoading(true);
    setError("");
    const favorites = options.favorites ?? favoritesOnly;
    try {
      const [metaResponse, jobsResponse, assetsResponse] = await Promise.all([
        fetch("/api/ai/space/meta", { cache: "no-store", signal: controller.signal }),
        fetch("/api/ai/space/jobs?page=1&pageSize=20", { cache: "no-store", signal: controller.signal }),
        fetch(`/api/ai/space/assets?page=1&pageSize=24${favorites ? "&favorites=1" : ""}`, { cache: "no-store", signal: controller.signal }),
      ]);
      const [nextMeta, nextJobs, nextAssets] = await Promise.all([
        readJson<SpaceMeta>(metaResponse, "读取 AI 空间配置失败"),
        readJson<{ items: SpaceJob[]; pagination: Pagination }>(jobsResponse, "读取生成任务失败"),
        readJson<{ items: SpaceAsset[]; pagination: Pagination }>(assetsResponse, "读取图片资产失败"),
      ]);
      if (controller.signal.aborted || generation !== generationRef.current) return false;
      setMeta(nextMeta);
      jobsRef.current = nextJobs.items;
      setJobs(nextJobs.items);
      setJobsPagination(nextJobs.pagination);
      setAssets(nextAssets.items);
      setAssetsPagination(nextAssets.pagination);
      alignDraftWithMeta(nextMeta);
      const pendingSubmission = submissionRef.current;
      if (pendingSubmission?.acceptedJobId && nextJobs.items.some((item) => item.id === pendingSubmission.acceptedJobId)) {
        submissionRef.current = null;
      }
      setSyncWarning("");
      return true;
    } catch (reason) {
      if (controller.signal.aborted || generation !== generationRef.current) return false;
      const message = reason instanceof Error ? reason.message : "AI 空间加载失败";
      if (options.quiet) {
        setSyncWarning(submissionRef.current?.acceptedJobId
          ? `任务已经受理，但状态同步失败：${message}。当前任务仍保留在列表中；在当前页面重新同步或重试相同内容不会创建重复任务。`
          : `状态同步失败：${message}。当前仍显示上一次成功读取的任务与图片。`);
      } else {
        setError(message);
      }
      return false;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (generation === generationRef.current) { setLoading(false); setRefreshing(false); }
    }
  }, [alignDraftWithMeta, favoritesOnly]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadWorkspace(), 0);
    return () => {
      window.clearTimeout(timer);
      controllerRef.current?.abort();
      assetsPageControllerRef.current?.abort();
      generationRef.current += 1;
      assetsPageGenerationRef.current += 1;
    };
  }, [loadWorkspace]);

  const hasActiveJob = jobs.some((job) => job.status === "queued" || job.status === "running");
  const pollActiveJobs = useCallback(async () => {
    const generation = ++generationRef.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const response = await fetch("/api/ai/space/jobs?page=1&pageSize=20", { cache: "no-store", signal: controller.signal });
      const payload = await readJson<{ items: SpaceJob[]; pagination: Pagination }>(response, "读取生成任务失败");
      if (controller.signal.aborted || generation !== generationRef.current) return;
      jobsRef.current = payload.items;
      setJobs(payload.items);
      setJobsPagination(payload.pagination);
      const pendingSubmission = submissionRef.current;
      if (pendingSubmission?.acceptedJobId && payload.items.some((item) => item.id === pendingSubmission.acceptedJobId)) {
        submissionRef.current = null;
      }
      if (!payload.items.some((job) => job.status === "queued" || job.status === "running")) {
        await loadWorkspace({ quiet: true });
      }
    } catch (reason) {
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setSyncWarning(`任务状态轮询失败：${reason instanceof Error ? reason.message : "请手动重新同步"}。`);
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, [loadWorkspace]);

  useEffect(() => {
    if (!hasActiveJob || loading || refreshing) return;
    let disposed = false;
    let timer = 0;
    const tick = async () => {
      await pollActiveJobs();
      if (!disposed) timer = window.setTimeout(() => void tick(), 4_000);
    };
    timer = window.setTimeout(() => void tick(), 4_000);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [hasActiveJob, loading, pollActiveJobs, refreshing]);

  const sceneTemplates = useMemo(
    () => meta?.templates.filter((item) => item.scene === draft.scene && item.isEnabled) ?? [],
    [draft.scene, meta?.templates],
  );
  const selectedTemplate = sceneTemplates.find((item) => item.id === draft.templateId) ?? null;

  const chooseScene = (scene: SceneId) => {
    const candidates = meta?.templates.filter((item) => item.scene === scene && item.isEnabled) ?? [];
    const template = candidates.find((item) => item.isDefault) ?? candidates[0];
    setDraft((current) => ({
      ...current,
      scene,
      templateId: template?.id ?? "",
      modelProfileId: template?.modelProfileId && meta?.profiles.some((item) => item.id === template.modelProfileId)
        ? template.modelProfileId
        : current.modelProfileId,
    }));
  };

  const chooseSceneWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>, current: SceneId) => {
    if (!(event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "Home" || event.key === "End")) return;
    const scenes = meta?.scenes ?? [];
    if (scenes.length === 0) return;
    event.preventDefault();
    const index = scenes.findIndex((scene) => scene.id === current);
    const next = event.key === "Home"
      ? scenes[0]!
      : event.key === "End"
        ? scenes.at(-1)!
        : scenes[(index + (event.key === "ArrowRight" ? 1 : -1) + scenes.length) % scenes.length]!;
    chooseScene(next.id);
    window.setTimeout(() => document.getElementById(`ai-space-scene-${next.id}`)?.focus(), 0);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!meta?.permissions.canGenerate || submitting) return;
    const requestBody = {
      scene: draft.scene,
      templateId: draft.templateId,
      modelProfileId: draft.modelProfileId,
      productName: draft.productName.trim(),
      brand: draft.brand.trim(),
      sku: draft.sku.trim(),
      sellingPoints: draft.sellingPoints.trim(),
      additionalInstructions: draft.additionalInstructions.trim(),
      count: draft.count,
    };
    const signature = JSON.stringify(requestBody);
    const prior = submissionRef.current;
    const recoveringAcceptedJob = prior?.signature === signature && Boolean(prior.acceptedJobId);
    const confirmation = recoveringAcceptedJob
      ? `重新确认“${requestBody.productName}”的同一生成请求吗？系统会沿用已受理任务，不会创建新的付费任务。`
      : `确认提交生成 ${requestBody.count} 张图片吗？图片模型供应商可能按实际生成数量计费；取消任务也无法撤回已经派发的一张。`;
    if (!window.confirm(confirmation)) return;
    setSubmitting(true); setError(""); setNotice("");
    if (!recoveringAcceptedJob) setSyncWarning("");
    const clientRequestId = prior?.signature === signature ? prior.clientRequestId : crypto.randomUUID();
    submissionRef.current = {
      signature,
      clientRequestId,
      ...(recoveringAcceptedJob && prior?.acceptedJobId ? { acceptedJobId: prior.acceptedJobId } : {}),
    };
    try {
      const response = await fetch("/api/ai/space/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...requestBody, clientRequestId }),
      });
      const payload = await readJson<{ item: SpaceJob; replayed: boolean }>(response, "创建图片生成任务失败");
      submissionRef.current = { signature, clientRequestId, acceptedJobId: payload.item.id };
      upsertAcceptedJob(payload.item, payload.replayed);
      setNotice(payload.replayed
        ? "已恢复同一生成请求，没有创建新的付费任务。"
        : `任务已受理：共 ${payload.item.requestedCount} 张图片，后台将按顺序生成。`);
      await loadWorkspace({ quiet: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建图片生成任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  const cancelJob = async (job: SpaceJob) => {
    if (busyId || !window.confirm(`取消“${job.productName}”尚未派发的图片吗？正在生成的一张可能仍会完成。`)) return;
    setBusyId(job.id); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/ai/space/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
      await readJson<{ item: SpaceJob }>(response, "取消生成任务失败");
      setNotice("已取消尚未派发的图片；已派发的生成不会重复提交。");
      await loadWorkspace({ quiet: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消生成任务失败");
    } finally {
      setBusyId("");
    }
  };

  const toggleFavorite = async (asset: SpaceAsset) => {
    if (busyId) return;
    setBusyId(asset.id); setError("");
    try {
      const response = await fetch(`/api/ai/space/assets/${encodeURIComponent(asset.id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ favorite: !asset.favorite }),
      });
      const payload = await readJson<{ item: SpaceAsset }>(response, "更新收藏失败");
      setAssets((current) => favoritesOnly && !payload.item.favorite
        ? current.filter((item) => item.id !== asset.id)
        : current.map((item) => item.id === asset.id ? payload.item : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新收藏失败");
    } finally {
      setBusyId("");
    }
  };

  const changeFavoritesFilter = (next: boolean) => {
    favoritesOnlyRef.current = next;
    assetsPageControllerRef.current?.abort();
    assetsPageGenerationRef.current += 1;
    setFavoritesOnly(next);
    void loadWorkspace({ quiet: true, favorites: next });
  };

  const loadMoreAssets = async () => {
    if (!assetsPagination.hasMore || refreshing) return;
    const generation = ++assetsPageGenerationRef.current;
    assetsPageControllerRef.current?.abort();
    const controller = new AbortController();
    assetsPageControllerRef.current = controller;
    const favoritesAtStart = favoritesOnlyRef.current;
    setRefreshing(true); setError("");
    try {
      const nextPage = assetsPagination.page + 1;
      const response = await fetch(`/api/ai/space/assets?page=${nextPage}&pageSize=24${favoritesAtStart ? "&favorites=1" : ""}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson<{ items: SpaceAsset[]; pagination: Pagination }>(response, "读取更多图片失败");
      if (controller.signal.aborted || generation !== assetsPageGenerationRef.current || favoritesOnlyRef.current !== favoritesAtStart) return;
      setAssets((current) => {
        const merged = new Map(current.map((item) => [item.id, item]));
        payload.items.forEach((item) => merged.set(item.id, item));
        return [...merged.values()];
      });
      setAssetsPagination(payload.pagination);
    } catch (reason) {
      if (controller.signal.aborted || generation !== assetsPageGenerationRef.current) return;
      setError(reason instanceof Error ? reason.message : "读取更多图片失败");
    } finally {
      if (assetsPageControllerRef.current === controller) assetsPageControllerRef.current = null;
      if (generation === assetsPageGenerationRef.current) setRefreshing(false);
    }
  };

  if (loading && !meta) return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在打开 AI 空间</strong><p>正在读取场景、任务和私有图片资产…</p></section>;

  return <section className="ai-space-workspace">
    <article className="panel ai-space-hero data-refresh-region" aria-busy={refreshing}>
      <div>
        <span className="eyebrow">AI SPACE · PRODUCT VISUALS</span>
        <h2>把商品信息变成可控的电商视觉</h2>
        <p>选择场景、模板和独立图片模型后提交。任务在后台串行执行，PNG 完整解码且尺寸一致后才进入私有资产库。</p>
      </div>
      <div className="ai-space-hero-actions">
        <span><strong>{jobsPagination.total}</strong> 个任务</span>
        <span><strong>{assetsPagination.total}</strong> 张图片</span>
        <button type="button" className="secondary-button" onClick={() => void loadWorkspace({ quiet: true })} disabled={refreshing}>{refreshing ? "同步中…" : "同步状态"}</button>
      </div>
    </article>

    {error && <div className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>AI 空间操作失败</strong><p>{error}</p></div></div>}
    {notice && <div className="inventory-feedback inventory-feedback-success" role="status"><span>✓</span><div><strong>任务已受理</strong><p>{notice}</p></div></div>}
    {syncWarning && <div className="inventory-feedback inventory-feedback-warning" role="status"><span>!</span><div><strong>任务已保留，状态同步待恢复</strong><p>{syncWarning}</p></div><button type="button" className="row-action" disabled={refreshing} onClick={() => void loadWorkspace({ quiet: true })}>{refreshing ? "同步中…" : "重新同步"}</button></div>}

    <div className="ai-space-create-grid">
      <article className="panel ai-space-scene-panel">
        <div className="section-header"><div><h3>1. 选择生成场景</h3><p>首版聚焦三类可审计的商品视觉，不生成买家秀或真人背书。</p></div></div>
        <div className="ai-space-scenes" role="radiogroup" aria-label="图片生成场景">
          {(meta?.scenes ?? []).map((scene) => <button key={scene.id} id={`ai-space-scene-${scene.id}`} type="button" role="radio" aria-checked={draft.scene === scene.id} tabIndex={draft.scene === scene.id ? 0 : -1} className={draft.scene === scene.id ? "active" : ""} onClick={() => chooseScene(scene.id)} onKeyDown={(event) => chooseSceneWithKeyboard(event, scene.id)}>
            <span aria-hidden="true">{scene.id === "product_main" ? "◇" : scene.id === "product_detail" ? "▤" : "✦"}</span>
            <strong>{scene.label}</strong>
            <small>{scene.description}</small>
          </button>)}
        </div>
        <div className="ai-space-safety"><span>安全边界</span><p>{meta?.safetyNotice ?? "禁止请求虚构背书、认证、价格或销量；生成物仅作草稿并须人工复核。"}</p></div>
      </article>

      <article className="panel ai-space-create-panel">
        <div className="section-header"><div><h3>2. 填写商品信息</h3><p>提示词由模板渲染并附加安全约束；后台每次只向供应商请求一张。</p></div></div>
        <form className="ai-space-form" onSubmit={(event) => void submit(event)}>
          <label><span>商品名称 *</span><input required maxLength={200} value={draft.productName} onChange={(event) => setDraft((current) => ({ ...current, productName: event.target.value }))} placeholder="例如：商用全自动切片机" /></label>
          <label><span>品牌</span><input maxLength={100} value={draft.brand} onChange={(event) => setDraft((current) => ({ ...current, brand: event.target.value }))} placeholder="例如：志高" /></label>
          <label><span>SKU</span><input maxLength={120} value={draft.sku} onChange={(event) => setDraft((current) => ({ ...current, sku: event.target.value }))} placeholder="用于资产检索，不会凭空展示" /></label>
          <label><span>生成数量</span><select value={draft.count} onChange={(event) => setDraft((current) => ({ ...current, count: Number(event.target.value) }))}>{Array.from({ length: meta?.limits.maximumImages ?? 4 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1} 张</option>)}</select></label>
          <label className="ai-space-form-wide"><span>可核验卖点</span><textarea maxLength={800} value={draft.sellingPoints} onChange={(event) => setDraft((current) => ({ ...current, sellingPoints: event.target.value }))} placeholder="用分号分隔；不要填写尚未核实的参数、认证、价格或销量" /></label>
          <label className="ai-space-form-wide"><span>补充构图要求</span><textarea maxLength={800} value={draft.additionalInstructions} onChange={(event) => setDraft((current) => ({ ...current, additionalInstructions: event.target.value }))} placeholder="例如：左侧柔光，右侧保留活动文案留白" /></label>
          <label><span>场景模板 *</span><select required value={draft.templateId} onChange={(event) => {
            const template = sceneTemplates.find((item) => item.id === event.target.value);
            setDraft((current) => ({ ...current, templateId: event.target.value, modelProfileId: template?.modelProfileId ?? current.modelProfileId }));
          }}><option value="">请选择模板</option>{sceneTemplates.map((template) => <option key={template.id} value={template.id}>{template.name} · v{template.version}</option>)}</select></label>
          <label><span>图片生成模型 *</span><select required value={draft.modelProfileId} onChange={(event) => setDraft((current) => ({ ...current, modelProfileId: event.target.value }))}><option value="">请选择模型</option>{(meta?.profiles ?? []).map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.modelName}</option>)}</select></label>
          <div className="ai-space-submit-row ai-space-form-wide">
            <div><strong>{selectedTemplate?.size ?? "—"}</strong><small>模板输出尺寸 · 每日最多 {meta?.limits.maximumDailyImagesPerOwner ?? 40} 张</small></div>
            <button type="submit" className="primary-button" disabled={!meta?.permissions.canGenerate || submitting || !draft.templateId || !draft.modelProfileId}>{submitting ? "正在提交…" : meta?.permissions.canGenerate ? `生成 ${draft.count} 张图片` : "当前身份仅可浏览"}</button>
          </div>
        </form>
        {(meta?.profiles.length ?? 0) === 0 && <div className="ai-space-config-needed"><div><strong>尚未配置图片生成模型</strong><p>图片生成模型与对话/视觉识别模型相互独立，管理员需要先配置 OpenAI Images 兼容端点。</p></div>{meta?.permissions.canManage && <button type="button" className="secondary-button" onClick={onOpenManagement}>前往 AI 管理</button>}</div>}
      </article>
    </div>

    <article className="panel ai-space-jobs">
      <div className="section-header"><div><h3>生成任务</h3><p>离开页面后任务仍会在后台继续；取消只影响尚未派发的图片。</p></div><span className="ai-space-live"><i />{hasActiveJob ? "后台处理中" : "队列空闲"}</span></div>
      <div className="ai-space-job-list">
        {jobs.length === 0 && <div className="ai-space-empty"><strong>还没有生成任务</strong><p>填写上方商品信息并提交，任务会出现在这里。</p></div>}
        {jobs.map((job) => {
          const settled = job.succeededCount + job.failedCount + job.cancelledCount;
          const progress = Math.round((settled / Math.max(1, job.requestedCount)) * 100);
          const active = job.status === "queued" || job.status === "running";
          return <section key={job.id} className="ai-space-job-card">
            <header><div><strong>{job.productName}</strong><small>{job.brand || "未填品牌"}{job.sku ? ` · ${job.sku}` : ""} · {job.templateName}</small></div><span className={`status ${job.status === "succeeded" ? "status-success" : job.status === "failed" ? "status-danger" : "status-warning"}`}>{statusText[job.status]}</span></header>
            <div className="ai-space-progress" role="progressbar" aria-label={`${job.productName}生成进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${active ? Math.max(progress, 8) : progress}%` }} /></div>
            <div className="ai-space-job-meta"><span>{job.succeededCount} 成功</span><span>{job.failedCount} 失败</span><span>{job.cancelledCount} 取消</span><span>{job.modelProfileName} · {job.size}</span><time>{localDateTime(job.createdAt)}</time></div>
            {job.items.some((item) => item.errorMessage) && <p className="ai-space-job-error">{job.items.find((item) => item.errorMessage)?.errorMessage}</p>}
            {active && <button type="button" className="row-action danger" disabled={busyId === job.id} onClick={() => void cancelJob(job)}>{busyId === job.id ? "取消中…" : "取消未派发图片"}</button>}
          </section>;
        })}
      </div>
    </article>

    <article className="panel ai-space-library">
      <div className="section-header"><div><h3>私有图片资产</h3><p>仅任务创建者且当前数据范围覆盖提交快照时可查看或下载。</p></div><div className="segmented"><button type="button" className={!favoritesOnly ? "active" : ""} onClick={() => changeFavoritesFilter(false)}>全部</button><button type="button" className={favoritesOnly ? "active" : ""} onClick={() => changeFavoritesFilter(true)}>已收藏</button></div></div>
      <div className="ai-space-gallery">
        {assets.length === 0 && <div className="ai-space-empty"><strong>{favoritesOnly ? "还没有收藏图片" : "还没有可用图片"}</strong><p>只有通过格式、尺寸与 R2 回查的生成结果才会显示。</p></div>}
        {assets.map((asset) => <figure key={asset.id} className="ai-space-asset-card">
          <div className="ai-space-image-frame"><Image src={asset.contentUrl} alt={`${asset.productName} AI 生成视觉`} width={asset.width} height={asset.height} sizes="(max-width: 760px) 100vw, (max-width: 1180px) 33vw, 25vw" unoptimized /><button type="button" aria-label={asset.favorite ? "取消收藏" : "收藏图片"} className={asset.favorite ? "favorite active" : "favorite"} disabled={busyId === asset.id} onClick={() => void toggleFavorite(asset)}>★</button></div>
          <figcaption><div><strong>{asset.productName}</strong><small>AI 草稿 · 待人工复核 · {asset.width}×{asset.height} · {(asset.byteSize / 1024).toFixed(0)} KB · {localDateTime(asset.createdAt)}</small></div><a className="row-action" href={asset.contentUrl} download>下载</a></figcaption>
        </figure>)}
      </div>
      {assetsPagination.hasMore && <div className="operations-load-more"><button type="button" className="secondary-button" disabled={refreshing} onClick={() => void loadMoreAssets()}>{refreshing ? "加载中…" : `加载更多（${assets.length}/${assetsPagination.total}）`}</button></div>}
    </article>
  </section>;
}
