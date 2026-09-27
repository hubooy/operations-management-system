"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import type { AppCurrentUser } from "./shell/view-contract";

type SceneId = "product_main" | "product_detail" | "promotion";
type ImageSize = "1024x1024" | "1024x1536" | "1536x1024";
type ImageProfile = {
  id: string;
  name: string;
  protocol: "openai_images";
  modelName: string;
  baseUrl: string;
  apiKeySuffix: string;
  status: "enabled" | "disabled";
  version: number;
  timeoutMs: number;
  lastSuccessResult: string | null;
  lastSuccessAt: string | null;
  createdAt: string;
  updatedAt: string;
};
type ImageTemplate = {
  id: string;
  scene: SceneId;
  name: string;
  promptTemplate: string;
  size: ImageSize;
  modelProfileId: string | null;
  version: number;
  isEnabled: boolean;
  isDefault: boolean;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};
type ToolCatalogItem = {
  name: string;
  title: string;
  description: string;
  risk: string;
  scopePolicy: string;
  execution: { timeoutMs: number; maxResultCharacters: number; maxCallsPerRequest: number };
};
type ProfileDraft = {
  id?: string;
  expectedVersion?: number;
  name: string;
  modelName: string;
  baseUrl: string;
  apiKey: string;
  status: "enabled" | "disabled";
  timeoutMs: number;
};
type TemplateDraft = {
  id?: string;
  expectedVersion?: number;
  scene: SceneId;
  name: string;
  promptTemplate: string;
  size: ImageSize;
  modelProfileId: string;
  isEnabled: boolean;
  isDefault: boolean;
};

const sceneLabels: Record<SceneId, string> = {
  product_main: "商品主图",
  product_detail: "卖点详情",
  promotion: "活动视觉",
};

function newProfileDraft(): ProfileDraft {
  return { name: "", modelName: "", baseUrl: "", apiKey: "", status: "enabled", timeoutMs: 90_000 };
}

function newTemplateDraft(): TemplateDraft {
  return {
    scene: "product_main",
    name: "",
    promptTemplate: "为{brand}{product_name}（SKU：{sku}）生成专业电商商品视觉。可核验卖点：{selling_points}。",
    size: "1024x1024",
    modelProfileId: "",
    isEnabled: true,
    isDefault: false,
  };
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null) as (T & { error?: string }) | null;
  if (!response.ok) throw new Error(payload?.error || fallback);
  if (!payload) throw new Error(fallback);
  return payload;
}

function localDateTime(value: string | null) {
  if (!value) return "尚无真实成功记录";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? `${value.replace(" ", "T")}Z` : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export default function AiSpaceManagementView({ currentUser }: { currentUser: AppCurrentUser | null }) {
  const canManage = currentUser?.role === "admin" && !currentUser.scopeRestricted;
  const [profiles, setProfiles] = useState<ImageProfile[]>([]);
  const [templates, setTemplates] = useState<ImageTemplate[]>([]);
  const [tools, setTools] = useState<ToolCatalogItem[]>([]);
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>(() => newProfileDraft());
  const [templateDraft, setTemplateDraft] = useState<TemplateDraft>(() => newTemplateDraft());
  const [loading, setLoading] = useState(canManage);
  const [saving, setSaving] = useState<"profile" | "template" | "">("");
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);

  const load = useCallback(async () => {
    if (!canManage) return;
    const generation = ++generationRef.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true); setError("");
    try {
      const [profilesResponse, templatesResponse, toolsResponse] = await Promise.all([
        fetch("/api/ai/space/profiles", { cache: "no-store", signal: controller.signal }),
        fetch("/api/ai/space/templates", { cache: "no-store", signal: controller.signal }),
        fetch("/api/ai/tools", { cache: "no-store", signal: controller.signal }),
      ]);
      const [profilePayload, templatePayload, toolPayload] = await Promise.all([
        readJson<{ items: ImageProfile[] }>(profilesResponse, "读取图片生成模型失败"),
        readJson<{ items: ImageTemplate[] }>(templatesResponse, "读取图片模板失败"),
        readJson<{ items: ToolCatalogItem[] }>(toolsResponse, "读取中央工具清单失败"),
      ]);
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setProfiles(profilePayload.items);
      setTemplates(templatePayload.items);
      setTools(toolPayload.items);
    } catch (reason) {
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setError(reason instanceof Error ? reason.message : "AI 空间管理配置加载失败");
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (generation === generationRef.current) setLoading(false);
    }
  }, [canManage]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => { window.clearTimeout(timer); controllerRef.current?.abort(); generationRef.current += 1; };
  }, [load]);

  const saveProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving("profile"); setError(""); setNotice("");
    try {
      const { baseUrl, ...rest } = profileDraft;
      const payload = profileDraft.id && !baseUrl.trim()
        ? rest
        : { ...rest, baseUrl: baseUrl.trim() };
      const response = await fetch("/api/ai/space/profiles", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const saved = await readJson<{ item: ImageProfile }>(response, "保存图片生成模型失败");
      setNotice(`图片生成模型“${saved.item.name}”已保存。新配置供后续新任务使用，历史任务继续保留原版本快照。`);
      setProfileDraft(newProfileDraft());
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存图片生成模型失败");
    } finally {
      setSaving("");
    }
  };

  const saveTemplate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving("template"); setError(""); setNotice("");
    try {
      const response = await fetch("/api/ai/space/templates", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...templateDraft, modelProfileId: templateDraft.modelProfileId || null }),
      });
      const saved = await readJson<{ item: ImageTemplate }>(response, "保存 AI 空间模板失败");
      setNotice(`模板“${saved.item.name}”已保存为 v${saved.item.version}；历史任务继续保留原快照。`);
      setTemplateDraft(newTemplateDraft());
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存 AI 空间模板失败");
    } finally {
      setSaving("");
    }
  };

  const remove = async (kind: "profile" | "template", id: string, name: string, version: number) => {
    if (!window.confirm(`确定删除“${name}”吗？已被模板或历史任务引用时，系统会拒绝删除。`)) return;
    setBusyId(`${kind}:${id}`); setError(""); setNotice("");
    try {
      const path = kind === "profile" ? "/api/ai/space/profiles" : "/api/ai/space/templates";
      const response = await fetch(`${path}?id=${encodeURIComponent(id)}&expectedVersion=${version}`, { method: "DELETE" });
      await readJson<{ ok: boolean }>(response, "删除配置失败");
      setNotice(`“${name}”已删除。`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除配置失败");
    } finally {
      setBusyId("");
    }
  };

  const editProfile = (item: ImageProfile) => setProfileDraft({
    id: item.id,
    expectedVersion: item.version,
    name: item.name,
    modelName: item.modelName,
    baseUrl: "",
    apiKey: "",
    status: item.status,
    timeoutMs: item.timeoutMs,
  });

  const editTemplate = (item: ImageTemplate) => setTemplateDraft({
    id: item.id,
    expectedVersion: item.version,
    scene: item.scene,
    name: item.name,
    promptTemplate: item.promptTemplate,
    size: item.size,
    modelProfileId: item.modelProfileId ?? "",
    isEnabled: item.isEnabled,
    isDefault: item.isDefault,
  });

  if (!canManage) return <article className="panel ai-permission-card"><h3>图片生成与工具治理</h3><p>仅无数据范围限制的管理员可维护图片模型、提示词模板和查看当前身份可调用的中央工具目录。</p></article>;

  return <section className="ai-space-management">
    <article className="panel ai-management-summary data-refresh-region" aria-busy={loading}>
      <div><span className="eyebrow">GOVERNED AI CONTROL PLANE</span><h2>图片生成与工具治理</h2><p>图片生成模型独立于文本对话和视觉识别模型；模板版本、真实成功记录与中央只读工具在这里统一核验。当前为治理 MVP，供应商币种成本换算与费用仪表盘尚未开放。</p></div>
      <div><span><strong>{profiles.length}</strong> 图片模型</span><span><strong>{templates.length}</strong> 模板</span><span><strong>{tools.length}</strong> 可用工具</span><button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>{loading ? "刷新中…" : "刷新"}</button></div>
    </article>

    {(error || notice) && <div className={`inventory-feedback ${error ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={error ? "alert" : "status"}><span>{error ? "!" : "✓"}</span><div><strong>{error ? "配置操作失败" : "配置已更新"}</strong><p>{error || notice}</p></div></div>}

    <article className="panel ai-admin-card">
      <div className="section-header"><div><h3>{profileDraft.id ? "编辑图片生成模型" : "新增图片生成模型"}</h3><p>仅支持 OpenAI Images 兼容协议；API Key 加密保存且不会回显。</p></div>{profileDraft.id && <button type="button" className="text-button" onClick={() => setProfileDraft(newProfileDraft())}>取消编辑</button>}</div>
      <form className="ai-config-form" onSubmit={(event) => void saveProfile(event)}>
        <label><span>配置名称</span><input required maxLength={100} value={profileDraft.name} onChange={(event) => setProfileDraft((current) => ({ ...current, name: event.target.value }))} placeholder="例如：生产图片模型" /></label>
        <label><span>模型标识</span><input required maxLength={120} value={profileDraft.modelName} onChange={(event) => setProfileDraft((current) => ({ ...current, modelName: event.target.value }))} placeholder="例如：gpt-image-2" /></label>
        <label className="ai-form-wide"><span>API 地址</span><input required={!profileDraft.id} type="url" value={profileDraft.baseUrl} onChange={(event) => setProfileDraft((current) => ({ ...current, baseUrl: event.target.value }))} placeholder={profileDraft.id ? "留空保留现有地址" : "https://api.openai.com/v1"} /><small>系统固定调用 /images/generations；生产自定义来源还必须进入精确 HTTPS origin 白名单。</small></label>
        <label><span>API Key</span><input required={!profileDraft.id} type="password" autoComplete="new-password" value={profileDraft.apiKey} onChange={(event) => setProfileDraft((current) => ({ ...current, apiKey: event.target.value }))} placeholder={profileDraft.id ? "同一服务 origin 可留空保留" : "输入图片模型密钥"} /><small>更换服务 origin 时必须同时填写新服务的 API Key，原密钥不会转发。</small></label>
        <label><span>状态</span><select value={profileDraft.status} onChange={(event) => setProfileDraft((current) => ({ ...current, status: event.target.value as ProfileDraft["status"] }))}><option value="enabled">启用</option><option value="disabled">停用</option></select></label>
        <label><span>请求超时（毫秒）</span><input type="number" min={3000} max={120000} step={1000} value={profileDraft.timeoutMs} onChange={(event) => setProfileDraft((current) => ({ ...current, timeoutMs: Number(event.target.value) }))} /></label>
        <div className="ai-form-actions"><button type="submit" className="primary-button" disabled={saving === "profile"}>{saving === "profile" ? "保存中…" : profileDraft.id ? "保存模型修改" : "新增图片模型"}</button></div>
      </form>
      <div className="ai-config-list">{profiles.length === 0 && <p className="soft-text">暂无图片生成模型。AI 空间会保持只读，直到新增可用模型。</p>}{profiles.map((item) => <div key={item.id} className="ai-config-card"><div><strong>{item.name}</strong><small>OpenAI Images · {item.modelName} · v{item.version} · 密钥 {item.apiKeySuffix || "未配置"} · 超时 {item.timeoutMs}ms</small><small>{item.lastSuccessAt ? `最近真实成功：${localDateTime(item.lastSuccessAt)} · ${item.lastSuccessResult || "完成"}` : "尚无真实图片生成成功记录；保存配置不等于连接成功"}</small></div><span className={`status ${item.status === "enabled" ? "status-success" : "status-warning"}`}>{item.status === "enabled" ? "启用" : "停用"}</span><div className="ai-card-actions"><button type="button" className="row-action" onClick={() => editProfile(item)}>编辑</button><button type="button" className="row-action danger" disabled={busyId === `profile:${item.id}`} onClick={() => void remove("profile", item.id, item.name, item.version)}>{busyId === `profile:${item.id}` ? "删除中…" : "删除"}</button></div></div>)}</div>
    </article>

    <article className="panel ai-admin-card">
      <div className="section-header"><div><h3>{templateDraft.id ? "编辑图片模板" : "新增图片模板"}</h3><p>模板更新自动递增版本；任务保存最终提示词与模板版本，历史生成不会被回写。</p></div>{templateDraft.id && <button type="button" className="text-button" onClick={() => setTemplateDraft(newTemplateDraft())}>取消编辑</button>}</div>
      <form className="ai-config-form" onSubmit={(event) => void saveTemplate(event)}>
        <label><span>场景</span><select value={templateDraft.scene} onChange={(event) => setTemplateDraft((current) => ({ ...current, scene: event.target.value as SceneId }))}>{Object.entries(sceneLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span>模板名称</span><input required maxLength={100} value={templateDraft.name} onChange={(event) => setTemplateDraft((current) => ({ ...current, name: event.target.value }))} /></label>
        <label><span>输出尺寸</span><select value={templateDraft.size} onChange={(event) => setTemplateDraft((current) => ({ ...current, size: event.target.value as ImageSize }))}><option value="1024x1024">1024×1024</option><option value="1024x1536">1024×1536</option><option value="1536x1024">1536×1024</option></select></label>
        <label><span>默认图片模型</span><select value={templateDraft.modelProfileId} onChange={(event) => setTemplateDraft((current) => ({ ...current, modelProfileId: event.target.value }))}><option value="">提交时选择</option>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
        <label className="ai-form-wide"><span>提示词模板</span><textarea required maxLength={3000} value={templateDraft.promptTemplate} onChange={(event) => setTemplateDraft((current) => ({ ...current, promptTemplate: event.target.value }))} /><small>必须包含 {"{product_name}"}；可用占位符：{"{brand}"}、{"{sku}"}、{"{selling_points}"}、{"{scene}"}。安全约束由服务端额外附加。</small></label>
        <label className="ai-check-field"><input type="checkbox" checked={templateDraft.isEnabled} onChange={(event) => setTemplateDraft((current) => ({ ...current, isEnabled: event.target.checked, isDefault: event.target.checked ? current.isDefault : false }))} /><span>启用模板</span></label>
        <label className="ai-check-field"><input type="checkbox" checked={templateDraft.isDefault} disabled={!templateDraft.isEnabled} onChange={(event) => setTemplateDraft((current) => ({ ...current, isDefault: event.target.checked }))} /><span>设为该场景默认模板</span></label>
        <div className="ai-form-actions"><button type="submit" className="primary-button" disabled={saving === "template"}>{saving === "template" ? "保存中…" : templateDraft.id ? "保存模板修改" : "新增模板"}</button></div>
      </form>
      <div className="ai-config-list">{templates.map((item) => <div key={item.id} className="ai-config-card"><div><strong>{item.name}</strong><small>{sceneLabels[item.scene]} · v{item.version} · {item.size} · {item.modelProfileId ? profiles.find((profile) => profile.id === item.modelProfileId)?.name ?? "模型已停用" : "提交时选择模型"}</small><small>{item.isDefault ? "场景默认 · " : ""}更新人 {item.updatedBy}</small></div><span className={`status ${item.isEnabled ? "status-success" : "status-warning"}`}>{item.isEnabled ? "启用" : "停用"}</span><div className="ai-card-actions"><button type="button" className="row-action" onClick={() => editTemplate(item)}>编辑</button><button type="button" className="row-action danger" disabled={busyId === `template:${item.id}`} onClick={() => void remove("template", item.id, item.name, item.version)}>{busyId === `template:${item.id}` ? "删除中…" : "删除"}</button></div></div>)}</div>
    </article>

    <article className="panel ai-tool-catalog">
      <div className="section-header"><div><h3>中央 AI 工具目录</h3><p>这里只展示当前管理员在网页 AI 对话 surface 上真正可执行的有界只读工具；不从路由或数据库自动暴露能力。</p></div><span className="status status-success">{tools.length} 个可用</span></div>
      <div className="ai-tool-grid">{tools.map((tool) => <section key={tool.name}><header><strong>{tool.title}</strong><code>{tool.name}</code></header><p>{tool.description}</p><footer><span>只读</span><span>{tool.execution.timeoutMs}ms</span><span>最多 {tool.execution.maxCallsPerRequest} 次/请求</span><span>{tool.scopePolicy}</span></footer></section>)}</div>
    </article>
  </section>;
}
