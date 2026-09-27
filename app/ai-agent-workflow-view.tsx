"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { buildAiPageContextPrompt, type AiPageContext } from "@/lib/ai/page-context";
import type { AppCurrentUser } from "./shell/view-contract";

type PassiveJson = null | boolean | number | string | PassiveJson[] | { [key: string]: PassiveJson };
type AgentStatus = "queued" | "running" | "paused" | "completed" | "failed" | "cancelled";
type WorkflowStatus = AgentStatus | "waiting_review";
type NodeStatus = "pending" | "running" | "waiting_review" | "completed" | "rejected" | "skipped" | "failed" | "cancelled";

type AgentJob = {
  id: string;
  task: string;
  input: PassiveJson;
  output: PassiveJson | null;
  status: AgentStatus;
  phase: string;
  stepIndex: number;
  version: number;
  retryable: boolean;
  resumeCount: number;
  attemptCount: number;
  modelId: string;
  modelVersion: number;
  allowedTools: string[];
  toolPolicyDigest: string;
  providerRoundCount: number;
  toolCallCount: number;
  providerDispatchStartedAt: string | null;
  workflowRunId: string | null;
  workflowNodeKey: string | null;
  errorCode: string;
  errorMessage: string;
  createdAt: string;
  updatedAt: string;
};

type WorkflowNode = {
  id: string;
  key: string;
  type: "agent" | "human_review";
  dependsOn: string[];
  instruction: string;
  status: NodeStatus;
  version: number;
  output: PassiveJson | null;
  agentJobId: string | null;
  reviewerEmail: string | null;
  reviewedAt: string | null;
  errorCode: string;
  errorMessage: string;
};

type WorkflowRun = {
  id: string;
  name: string;
  graph: { nodes: Array<{ key: string; type: "agent" | "human_review"; dependsOn: string[]; instruction: string }> };
  input: PassiveJson;
  output: PassiveJson | null;
  dryRun: boolean;
  status: WorkflowStatus;
  currentNodeKey: string | null;
  version: number;
  retryable: boolean;
  resumeCount: number;
  attemptCount: number;
  modelId: string;
  modelVersion: number;
  allowedTools: string[];
  toolPolicyDigest: string;
  providerRoundCount: number;
  toolCallCount: number;
  providerDispatchStartedAt: string | null;
  errorCode: string;
  errorMessage: string;
  createdAt: string;
  updatedAt: string;
};

type WorkflowDetail = WorkflowRun & { nodes: WorkflowNode[] };
type ListResult<T> = { items: T[]; total: number; returned: number; truncated: boolean };

const initialWorkflowGraph = JSON.stringify({
  nodes: [
    { key: "collect", type: "agent", dependsOn: [], instruction: "读取允许范围内的数据，并输出有来源说明的结构化事实。" },
    { key: "review", type: "human_review", dependsOn: ["collect"], instruction: "人工复核上游结构化事实是否可以继续。" },
    { key: "summarize", type: "agent", dependsOn: ["collect", "review"], instruction: "仅基于已复核事实和复核结论生成结构化总结。" },
  ],
}, null, 2);

const statusLabels: Record<AgentStatus | WorkflowStatus | NodeStatus, string> = {
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  waiting_review: "待人工复核",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  pending: "待执行",
  rejected: "未通过",
  skipped: "已跳过",
};

function statusClass(status: AgentStatus | WorkflowStatus | NodeStatus) {
  if (status === "completed" || status === "skipped") return "status-success";
  if (status === "failed" || status === "cancelled" || status === "rejected") return "status-danger";
  if (status === "paused" || status === "waiting_review") return "status-warning";
  if (status === "running") return "status-purple";
  return "status-gray";
}

function parseJson(text: string, label: string): PassiveJson {
  try {
    return JSON.parse(text) as PassiveJson;
  } catch {
    throw new Error(`${label}必须是有效 JSON。`);
  }
}

function compactJson(value: PassiveJson | null, maximum = 220) {
  const serialized = JSON.stringify(value);
  return serialized.length > maximum ? `${serialized.slice(0, maximum)}…` : serialized;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null) as (T & { error?: string }) | null;
  if (!response.ok) throw new Error(payload?.error || fallback);
  if (!payload) throw new Error(fallback);
  return payload;
}

function createClientRequestId() {
  return crypto.randomUUID();
}

export function resolveAgentPageContextDraft(input: {
  currentTask: string;
  currentInput: string;
  initialContextPrompt?: string;
  initialPageContext?: AiPageContext | null;
}) {
  const task = Array.from(
    input.initialContextPrompt?.trim() || (input.initialPageContext ? buildAiPageContextPrompt(input.initialPageContext) : ""),
  ).slice(0, 4_000).join("");
  const currentInput = input.currentInput.trim();
  if (!task || input.currentTask.trim() || (currentInput && currentInput !== "{}")) return null;
  return {
    task,
    structuredInput: input.initialPageContext
      ? JSON.stringify({ pageContext: input.initialPageContext }, null, 2)
      : "{}",
  };
}

export function hasActiveAgentWork(
  jobs: readonly { status: AgentStatus }[],
  runs: readonly { status: WorkflowStatus }[],
) {
  return jobs.some((item) => item.status === "queued" || item.status === "running")
    || runs.some((item) => item.status === "queued" || item.status === "running");
}

export default function AiAgentWorkflowView({
  currentUser,
  initialContextPrompt = "",
  initialPageContext = null,
}: {
  currentUser: AppCurrentUser | null;
  initialContextPrompt?: string;
  initialPageContext?: AiPageContext | null;
}) {
  const canMutate = currentUser?.role === "analyst" || currentUser?.role === "operator" || currentUser?.role === "admin";
  const agentExecutorAvailable = true;
  const [jobs, setJobs] = useState<AgentJob[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [selectedRun, setSelectedRun] = useState<WorkflowDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [agentTask, setAgentTask] = useState("");
  const [agentInput, setAgentInput] = useState("{}");
  const [workflowName, setWorkflowName] = useState("");
  const [workflowGraph, setWorkflowGraph] = useState(initialWorkflowGraph);
  const [workflowInput, setWorkflowInput] = useState("{}");
  const [dryRun, setDryRun] = useState(true);
  const [reviewComment, setReviewComment] = useState("");
  const listControllerRef = useRef<AbortController | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const detailGenerationRef = useRef(0);
  const appliedPageContextRef = useRef("");
  const agentSubmissionRef = useRef<{ signature: string; clientRequestId: string } | null>(null);
  const workflowSubmissionRef = useRef<{ signature: string; clientRequestId: string } | null>(null);

  const loadRunDetail = useCallback(async (runId: string, options: { background?: boolean } = {}) => {
    const generation = ++detailGenerationRef.current;
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    if (!options.background) setDetailLoading(true);
    try {
      const response = await fetch(`/api/ai/workflow-runs/${encodeURIComponent(runId)}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      const payload = await readJson<{ item: WorkflowDetail }>(response, "读取 AI 工作流详情失败");
      if (!controller.signal.aborted && generation === detailGenerationRef.current) setSelectedRun(payload.item);
    } catch (reason) {
      if (!controller.signal.aborted && generation === detailGenerationRef.current) {
        setError(reason instanceof Error ? reason.message : "读取 AI 工作流详情失败");
      }
    } finally {
      if (detailControllerRef.current === controller) detailControllerRef.current = null;
      if (!options.background && generation === detailGenerationRef.current) setDetailLoading(false);
    }
  }, []);

  const load = useCallback(async (options: { background?: boolean } = {}) => {
    const generation = ++generationRef.current;
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    if (!options.background) {
      setLoading(true);
      setError("");
    }
    try {
      const [jobResponse, runResponse] = await Promise.all([
        fetch("/api/ai/agent-jobs?page=1&pageSize=30", { cache: "no-store", signal: controller.signal }),
        fetch("/api/ai/workflow-runs?page=1&pageSize=30", { cache: "no-store", signal: controller.signal }),
      ]);
      const [jobPayload, runPayload] = await Promise.all([
        readJson<ListResult<AgentJob>>(jobResponse, "读取 AI Agent 任务失败"),
        readJson<ListResult<WorkflowRun>>(runResponse, "读取 AI 工作流失败"),
      ]);
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setJobs(jobPayload.items);
      setRuns(runPayload.items);
      if (selectedRunId) {
        if (runPayload.items.some((item) => item.id === selectedRunId)) {
          await loadRunDetail(selectedRunId, { background: options.background });
        }
        else { setSelectedRunId(""); setSelectedRun(null); }
      }
    } catch (reason) {
      if (!controller.signal.aborted && generation === generationRef.current) {
        setError(reason instanceof Error ? reason.message : "读取 AI Agent 与工作流失败");
      }
    } finally {
      if (listControllerRef.current === controller) listControllerRef.current = null;
      if (!options.background && generation === generationRef.current) setLoading(false);
    }
  }, [loadRunDetail, selectedRunId]);

  useEffect(() => {
    const prompt = Array.from(initialContextPrompt.trim()).slice(0, 4_000).join("");
    const applicationKey = `${prompt}\n${JSON.stringify(initialPageContext ?? null)}`;
    if ((!prompt && !initialPageContext) || applicationKey === appliedPageContextRef.current) return;
    appliedPageContextRef.current = applicationKey;
    const draft = resolveAgentPageContextDraft({
      currentTask: agentTask,
      currentInput: agentInput,
      initialContextPrompt: prompt,
      initialPageContext,
    });
    if (!draft) return;
    setAgentTask(draft.task);
    setAgentInput(draft.structuredInput);
    setNotice(initialPageContext
      ? `已带入“${initialPageContext.moduleLabel} / ${initialPageContext.view}”页面上下文；请确认任务与输入后再创建，不会自动提交。`
      : "已带入页面问题草稿；请确认任务与输入后再创建，不会自动提交。");
  }, [agentInput, agentTask, initialContextPrompt, initialPageContext]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      listControllerRef.current?.abort();
      detailControllerRef.current?.abort();
      generationRef.current += 1;
      detailGenerationRef.current += 1;
    };
  }, [load]);

  const hasActiveWork = hasActiveAgentWork(jobs, runs);
  useEffect(() => {
    if (!hasActiveWork || loading || busyKey) return;
    let disposed = false;
    let timer = 0;
    const tick = async () => {
      await load({ background: true });
      if (!disposed) timer = window.setTimeout(() => void tick(), 4_000);
    };
    timer = window.setTimeout(() => void tick(), 4_000);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [busyKey, hasActiveWork, load, loading]);

  const createAgent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canMutate || !agentExecutorAvailable) return;
    if (!window.confirm("确认创建正式 Agent 任务吗？任务会按轮次调用当前默认模型，可能产生模型费用；取消只能阻止尚未派发的下一步。")) return;
    setBusyKey("create-agent"); setError(""); setNotice("");
    try {
      const input = parseJson(agentInput, "Agent 输入");
      const signature = JSON.stringify({ task: agentTask.trim(), input });
      const pending = agentSubmissionRef.current?.signature === signature
        ? agentSubmissionRef.current
        : { signature, clientRequestId: createClientRequestId() };
      agentSubmissionRef.current = pending;
      const response = await fetch("/api/ai/agent-jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ clientRequestId: pending.clientRequestId, task: agentTask, input }),
      });
      const payload = await readJson<{ item: AgentJob; replayed: boolean }>(response, "创建 AI Agent 任务失败");
      setNotice(payload.replayed ? "已找回同一幂等请求对应的 Agent 任务。" : "Agent 任务已进入持久队列；创建成功不代表业务结果已完成。");
      setAgentTask(""); setAgentInput("{}"); agentSubmissionRef.current = null;
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建 AI Agent 任务失败");
    } finally {
      setBusyKey("");
    }
  };

  const createWorkflow = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canMutate) return;
    if (!dryRun && !window.confirm("确认创建正式多 Agent 工作流吗？每个 Agent 节点都可能产生模型费用，人工复核节点会暂停等待确认。")) return;
    setBusyKey("create-workflow"); setError(""); setNotice("");
    try {
      const graph = parseJson(workflowGraph, "工作流 DAG");
      const input = parseJson(workflowInput, "工作流输入");
      const signature = JSON.stringify({ name: workflowName.trim(), graph, input, dryRun });
      const pending = workflowSubmissionRef.current?.signature === signature
        ? workflowSubmissionRef.current
        : { signature, clientRequestId: createClientRequestId() };
      workflowSubmissionRef.current = pending;
      const response = await fetch("/api/ai/workflow-runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ clientRequestId: pending.clientRequestId, name: workflowName, graph, input, dryRun }),
      });
      const payload = await readJson<{ item: WorkflowDetail; replayed: boolean }>(response, "创建 AI 工作流失败");
      setNotice(payload.replayed ? "已找回同一幂等请求对应的工作流。" : dryRun ? "DAG dry-run 已进入持久队列。" : "工作流已进入持久队列；节点会按依赖和人工复核门禁推进。");
      workflowSubmissionRef.current = null;
      setWorkflowName("");
      setSelectedRunId(payload.item.id); setSelectedRun(payload.item);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建 AI 工作流失败");
    } finally {
      setBusyKey("");
    }
  };

  const mutate = async (key: string, path: string, body: PassiveJson, success: string) => {
    if (!canMutate) return;
    setBusyKey(key); setError(""); setNotice("");
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      await readJson<{ item: AgentJob | WorkflowDetail }>(response, "更新持久任务状态失败");
      setNotice(success);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新持久任务状态失败");
    } finally {
      setBusyKey("");
    }
  };

  const review = async (node: WorkflowNode, decision: "approve" | "reject") => {
    if (!selectedRun || !canMutate) return;
    const action = decision === "approve" ? "通过" : "拒绝";
    if (!window.confirm(`确认${action}人工复核节点“${node.key}”吗？`)) return;
    await mutate(
      `review-${node.key}`,
      `/api/ai/workflow-runs/${encodeURIComponent(selectedRun.id)}/nodes/${encodeURIComponent(node.key)}/review`,
      { decision, comment: reviewComment.trim(), expectedVersion: node.version },
      `人工复核节点“${node.key}”已${action}。`,
    );
    setReviewComment("");
  };

  return <section className="ai-sandbox-workspace">
    <article className="panel ai-sandbox-hero">
      <div><span className="eyebrow">DURABLE AGENT ORCHESTRATION</span><h2>Agent 与多 Agent 工作流</h2><p>正式 Agent 以一次模型调用或一次只读工具调用为一个持久微步；多 Agent DAG 支持依赖传递和人工复核。</p></div>
      <div className="ai-sandbox-badges"><span>逐轮派发账本</span><span>持久任务状态</span><span>最多 24 节点</span><span>支持 dry-run</span></div>
    </article>

    <div className="inventory-feedback inventory-feedback-warning" role="note"><span>i</span><div><strong>当前执行边界</strong><p>正式 Agent 只使用中央注册表中的有界只读工具；每轮都会重验账号、数据范围、模型版本和工具策略。任一外部调用结果未知时会失败关闭且不自动重试。JSON 分析沙箱继续保留；pandas 临时分析需要独立容器服务就绪，只能使用当前账号获准的数据，不修改业务记录。</p></div></div>
    {!canMutate && <section className="panel ai-permission-card" role="status"><h3>当前为只读模式</h3><p>viewer 可以查看和刷新自己的任务与工作流；创建、取消、恢复和人工复核需要 analyst、operator 或 admin。</p></section>}
    {(error || notice) && <div className={`inventory-feedback ${error ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={error ? "alert" : "status"}><span>{error ? "!" : "✓"}</span><div><strong>{error ? "Agent 平台操作失败" : "Agent 平台已更新"}</strong><p>{error || notice}</p></div></div>}

    <div className="ai-sandbox-grid">
      <article className="panel ai-admin-card">
        <div className="section-header"><div><h3>创建单 Agent 任务</h3><p>任务绑定创建时的模型版本和只读工具白名单，并在后台逐微步推进。</p></div></div>
        <form className="ai-config-form" onSubmit={(event) => void createAgent(event)}>
          <label className="ai-form-wide"><span>任务说明</span><textarea required maxLength={8000} rows={6} disabled={!canMutate} value={agentTask} onChange={(event) => setAgentTask(event.target.value)} placeholder="例如：查询本月网店、库存和推广数据，给出有来源的异常清单。" /></label>
          <label className="ai-form-wide"><span>结构化输入（JSON）</span><textarea required rows={7} spellCheck={false} disabled={!canMutate} value={agentInput} onChange={(event) => setAgentInput(event.target.value)} /><small>输入作为低信任数据进入任务；模型只能调用当前账号范围内的只读工具。</small></label>
          <div className="ai-form-actions"><button className="primary-button" type="submit" disabled={!canMutate || busyKey === "create-agent"}>{busyKey === "create-agent" ? "创建中…" : "创建正式 Agent"}</button></div>
        </form>
      </article>

      <article className="panel ai-admin-card">
        <div className="section-header"><div><h3>创建有界 DAG 工作流</h3><p>节点仅允许 agent / human_review；服务端校验无环、依赖、深度与节点数。</p></div></div>
        <form className="ai-config-form" onSubmit={(event) => void createWorkflow(event)}>
          <label><span>工作流名称</span><input required maxLength={120} disabled={!canMutate} value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder="例如：周报事实复核" /></label>
          <label className="ai-check-field"><input type="checkbox" checked={dryRun} disabled={!canMutate} onChange={(event) => setDryRun(event.target.checked)} /><span>{dryRun ? "dry-run（不调用模型）" : "正式编排（Agent 节点会调用模型）"}</span></label>
          <label className="ai-form-wide"><span>DAG 定义（JSON）</span><textarea required rows={12} spellCheck={false} disabled={!canMutate} value={workflowGraph} onChange={(event) => setWorkflowGraph(event.target.value)} /></label>
          <label className="ai-form-wide"><span>工作流输入（JSON）</span><textarea required rows={5} spellCheck={false} disabled={!canMutate} value={workflowInput} onChange={(event) => setWorkflowInput(event.target.value)} /></label>
          <div className="ai-form-actions"><button className="primary-button" type="submit" disabled={!canMutate || busyKey === "create-workflow"}>{busyKey === "create-workflow" ? "创建中…" : dryRun ? "创建 dry-run" : "创建正式工作流"}</button></div>
        </form>
      </article>
    </div>

    <div className="ai-sandbox-grid">
      <article className="panel ai-admin-card data-refresh-region" aria-busy={loading}>
        <div className="section-header"><div><h3>我的 Agent 任务</h3><p>包括独立任务和工作流子任务；工作流子任务只能从所属工作流变更。</p></div><button className="secondary-button" type="button" disabled={loading} onClick={() => void load()}>{loading ? "刷新中…" : "刷新"}</button></div>
        <div className="ai-config-list">{!loading && jobs.length === 0 && <div className="empty-state"><strong>暂无 Agent 任务</strong><p>创建后会在这里显示真实持久状态。</p></div>}{jobs.map((job) => <div className="ai-config-card" key={job.id}><div><strong>{job.task}</strong><small>{job.id} · step {job.stepIndex} · v{job.version} · 尝试 {job.attemptCount}</small><small>{job.modelId ? `${job.modelId} · profile v${job.modelVersion} · ${job.allowedTools.length} 工具` : "确定性任务（无供应商派发）"} · provider {job.providerRoundCount} 轮 / tool {job.toolCallCount} 次</small><small title={compactJson(job.output ?? job.input)}>{job.errorMessage || compactJson(job.output ?? job.input)}</small></div><span className={`status ${statusClass(job.status)}`}>{statusLabels[job.status]}</span><div className="ai-card-actions">{canMutate && !job.workflowRunId && (job.status === "queued" || job.status === "running" || job.status === "paused") && <button type="button" className="row-action danger" disabled={Boolean(busyKey)} onClick={() => { if (window.confirm("确认取消这个 Agent 任务吗？")) void mutate(`cancel-${job.id}`, `/api/ai/agent-jobs/${encodeURIComponent(job.id)}/cancel`, { expectedVersion: job.version }, "Agent 任务已取消。"); }}>取消</button>}{canMutate && !job.workflowRunId && job.retryable && (job.status === "paused" || job.status === "failed") && <button type="button" className="row-action" disabled={Boolean(busyKey)} onClick={() => void mutate(`resume-${job.id}`, `/api/ai/agent-jobs/${encodeURIComponent(job.id)}/resume`, { expectedVersion: job.version }, "Agent 任务已安全恢复到队列。")}>恢复</button>}</div></div>)}</div>
      </article>

      <article className="panel ai-admin-card data-refresh-region" aria-busy={loading}>
        <div className="section-header"><div><h3>我的多 Agent 工作流</h3><p>选择一项查看 DAG 节点和人工复核门禁。</p></div><span className="status status-gray">{runs.length} 条</span></div>
        <div className="ai-config-list">{!loading && runs.length === 0 && <div className="empty-state"><strong>暂无工作流</strong><p>建议先用 dry-run 验证 DAG 拓扑。</p></div>}{runs.map((run) => <div className="ai-config-card" key={run.id}><div><strong>{run.name}</strong><small>{run.graph.nodes.length} 节点 · {run.dryRun ? "dry-run" : "正式编排"} · v{run.version}</small><small>{run.modelId ? `${run.modelId} · profile v${run.modelVersion} · ${run.allowedTools.length} 工具` : "dry-run（无供应商派发）"} · provider {run.providerRoundCount} 轮 / tool {run.toolCallCount} 次</small><small>{run.currentNodeKey ? `当前节点 ${run.currentNodeKey}` : run.errorMessage || run.updatedAt}</small></div><span className={`status ${statusClass(run.status)}`}>{statusLabels[run.status]}</span><div className="ai-card-actions"><button type="button" className="row-action" onClick={() => { setSelectedRunId(run.id); setReviewComment(""); void loadRunDetail(run.id); }}>详情</button>{canMutate && (run.status === "queued" || run.status === "running" || run.status === "waiting_review" || run.status === "paused") && <button type="button" className="row-action danger" disabled={Boolean(busyKey)} onClick={() => { if (window.confirm("确认取消整个工作流吗？运行中的子任务也会被取消。")) void mutate(`cancel-${run.id}`, `/api/ai/workflow-runs/${encodeURIComponent(run.id)}/cancel`, { expectedVersion: run.version }, "工作流已取消。"); }}>取消</button>}{canMutate && run.retryable && (run.status === "paused" || run.status === "failed") && <button type="button" className="row-action" disabled={Boolean(busyKey)} onClick={() => void mutate(`resume-${run.id}`, `/api/ai/workflow-runs/${encodeURIComponent(run.id)}/resume`, { expectedVersion: run.version }, "工作流及其当前子任务已安全恢复。")}>恢复</button>}</div></div>)}</div>
      </article>
    </div>

    {selectedRunId && <article className="panel ai-admin-card data-refresh-region" aria-busy={detailLoading}>
      <div className="section-header"><div><h3>工作流详情：{selectedRun?.name ?? selectedRunId}</h3><p>{selectedRun ? `${selectedRun.id} · ${selectedRun.dryRun ? "dry-run" : "正式编排"} · v${selectedRun.version}` : "正在读取 owner-only 节点详情…"}</p></div><button type="button" className="secondary-button" disabled={detailLoading} onClick={() => void loadRunDetail(selectedRunId)}>{detailLoading ? "刷新中…" : "刷新详情"}</button></div>
      <div className="ai-config-list">{selectedRun?.nodes.map((node) => <div className="ai-config-card" key={node.id}><div><strong>{node.key} · {node.type === "agent" ? "Agent" : "人工复核"}</strong><small>依赖：{node.dependsOn.length ? node.dependsOn.join("、") : "无"} · v{node.version}{node.agentJobId ? ` · 子任务 ${node.agentJobId}` : ""}</small><small title={node.output ? compactJson(node.output, 1000) : node.instruction}>{node.errorMessage || (node.output ? compactJson(node.output) : node.instruction)}</small></div><span className={`status ${statusClass(node.status)}`}>{statusLabels[node.status]}</span><div className="ai-card-actions">{canMutate && node.type === "human_review" && node.status === "waiting_review" && selectedRun.status === "waiting_review" && selectedRun.currentNodeKey === node.key && <><button type="button" className="row-action" disabled={Boolean(busyKey)} onClick={() => void review(node, "approve")}>通过</button><button type="button" className="row-action danger" disabled={Boolean(busyKey)} onClick={() => void review(node, "reject")}>拒绝</button></>}{node.reviewerEmail && <small>{node.reviewerEmail}</small>}</div></div>)}</div>
      {selectedRun?.nodes.some((node) => node.type === "human_review" && node.status === "waiting_review") && canMutate && <form className="ai-config-form" onSubmit={(event) => event.preventDefault()}><label className="ai-form-wide"><span>人工复核备注</span><textarea rows={4} maxLength={2000} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder="写明通过依据或拒绝原因；提交时使用节点 expectedVersion 防止误审旧状态。" /></label></form>}
    </article>}
  </section>;
}
