"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { AI_MODEL_TOOL_BUDGET_LIMITS } from "@/lib/ai/model-tool-budget";
import { normalizeAiPageContext, type AiPageModule, type AiPageContext } from "@/lib/ai/page-context";
import { recoverAiWorkspaceRequest } from "@/lib/ai/workspace-recovery";
import { ApiError } from "@/lib/http/api-error";
import type { AppCurrentUser } from "./shell/view-contract";
import { SearchableSelect } from "./ui/searchable-select";
import AiChatWorkbench, { type LiveAiAnswer } from "./ai-chat-workbench";
import { readAiChatStream, AiChatStreamError } from "@/lib/ai/chat-stream";

import AiModelGenerationFields from "./ai-model-generation-fields";
import { DEFAULT_AI_GENERATION, MAX_AI_REPLY_CHARACTERS, type AiGenerationOptions, type AiExecutionInfo } from "@/lib/ai/model-generation";

type CurrentUser = AppCurrentUser;
type AiModelProtocol = "openai_compatible" | "anthropic";
type AiModelType = "text" | "vision";
type AiModelStatus = "enabled" | "disabled";
type AiModelReasoningMode = "auto" | "disabled";
type AiChannelKind = "dingtalk_group_bot" | "dingtalk_app" | "wechat_work_group_bot" | "wechat_work_app";
type AiArtifactCell = string | number | boolean | null;
type AiTableArtifact = { id: string; kind: "table"; title: string; sourceTool: string; columns: string[]; rows: AiArtifactCell[][]; rowCount: number; truncated: boolean; fileName: string; mimeType: "text/csv; charset=utf-8"; contentDigest: string; downloadUrl: string; createdAt: string };
type AiConversationMessage = { execution?: AiExecutionInfo; id: string; conversationId: string; role: "user" | "assistant"; content: string; messageKind: "message" | "context_reset" | "help"; createdAt: string; artifacts: AiTableArtifact[]; contentBytes: number; contentTruncated: boolean };
type AiAvailableChatModel = { id: string; name: string; protocol: AiModelProtocol; modelType: AiModelType; modelName: string; isDefault: boolean };
type AiConversationPagination = { page: number; pageSize: number; total: number; returned: number; truncated: boolean; hasMore: boolean };
type AiMessagePagination = { pageSize: number; total: number; returned: number; truncated: boolean; hasMore: boolean; nextBefore: number | null };
type AiConfigurationState = "idle" | "loading" | "ready" | "error";
type AiFetch = (input: string, init?: RequestInit) => Promise<Response>;

type AiModelRecord = {
  id: string;
  version: number;
  name: string;
  protocol: AiModelProtocol;
  modelType: AiModelType;
  modelName: string;
  baseUrl: string;
  apiKeySuffix: string;
  isDefaultTextModel: boolean;
  status: AiModelStatus;
  maxTokens: number;
  generationOptions: AiGenerationOptions;
  reasoningMode: AiModelReasoningMode;
  temperatureMilli: number;
  maxToolRounds: number;
  maxTotalToolCalls: number;
  lastTestResult: string | null;
  lastTestedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

type AiChannelRecord = {
  id: string;
  name: string;
  kind: AiChannelKind;
  status: "enabled" | "disabled";
  sendEnabled: boolean;
  callbackEnabled: boolean;
  webhookUrlMasked: string;
  callbackTokenMasked: string;
  aesKeyMasked: string;
  receiverId: string;
  lastTestResult: string | null;
  lastTestedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

type AiConversationRecord = {
  id: string;
  title: string;
  modelId: string | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  workspaceModule?: AiPageModule;
  pageContext?: AiPageContext | null;
};

type AiModelDraft = {
  id?: string;
  expectedVersion?: number;
  name: string;
  protocol: AiModelProtocol;
  modelType: AiModelType;
  modelName: string;
  baseUrl: string;
  apiKey: string;
  status: AiModelStatus;
  isDefaultTextModel: boolean;
  maxTokens: number;
  generationOptions: AiGenerationOptions;
  reasoningMode: AiModelReasoningMode;
  temperatureMilli: number;
  maxToolRounds: number;
  maxTotalToolCalls: number;
};

type AiChannelDraft = {
  id?: string;
  name: string;
  kind: AiChannelKind;
  status: "enabled" | "disabled";
  sendEnabled: boolean;
  callbackEnabled: boolean;
  webhookUrl: string;
  callbackToken: string;
  aesKey: string;
  receiverId: string;
};

type AiConversationPagePayload = {
  items?: AiConversationRecord[];
  models?: AiAvailableChatModel[];
  pagination?: AiConversationPagination;
  error?: string;
  code?: string;
};

type AiConversationPageRequestOptions = {
  workspaceModule?: AiPageModule;
  page?: number;
  pageSize?: number;
  signal?: AbortSignal;
};

type AiIdentityPayload = {
  user?: Pick<CurrentUser, "email">;
};

export type AiChatPostPayload = {
  workspaceModule?: AiPageModule;
  conversationId?: string;
  modelId?: string;
  message: string;
  title: string;
  pageContext?: AiPageContext | null;
};

export type PendingAiChatRequest = {
  clientRequestId: string;
  intentKey: string;
  requestPayload: AiChatPostPayload;
  response?: { conversationId: string; assistantMessageId: string };
  synchronized: { conversation: boolean; message: boolean };
};

export function resolvePendingAiChatRequest(
  current: PendingAiChatRequest | null,
  candidate: AiChatPostPayload,
  createId: () => string = () => crypto.randomUUID(),
): PendingAiChatRequest {
  // Conversation/model state can change after a lost response. The user's message and explicit
  // new-topic/model actions control rotation; otherwise retain the exact originally billed payload.
  if (candidate.workspaceModule && current?.requestPayload.workspaceModule === candidate.workspaceModule
    && current.requestPayload.message === candidate.message) return current;
  const intentKey = JSON.stringify([candidate.message, candidate.title, candidate.pageContext ?? null]);
  if (current?.intentKey === intentKey) return current;
  return {
    clientRequestId: createId(),
    intentKey,
    requestPayload: { ...candidate },
    synchronized: { conversation: false, message: false },
  };
}

export function attachPendingAiChatResponse(
  current: PendingAiChatRequest,
  response: { conversationId: string; assistantMessageId: string },
): PendingAiChatRequest {
  const sameResponse = current.response?.conversationId === response.conversationId
    && current.response.assistantMessageId === response.assistantMessageId;
  return {
    ...current,
    response,
    synchronized: sameResponse ? current.synchronized : { conversation: false, message: false },
  };
}

export function markPendingAiChatSynchronized(
  current: PendingAiChatRequest | null,
  evidence:
    | { kind: "conversation"; conversationId: string }
    | { kind: "message"; conversationId: string; assistantMessageId: string },
): PendingAiChatRequest | null {
  if (!current?.response || current.response.conversationId !== evidence.conversationId) return current;
  if (evidence.kind === "message" && current.response.assistantMessageId !== evidence.assistantMessageId) return current;
  const synchronized = { ...current.synchronized, [evidence.kind]: true };
  return synchronized.conversation && synchronized.message ? null : { ...current, synchronized };
}

export function shouldReleasePendingAiChatRequest(errorCode: string | undefined): boolean {
  return errorCode === "ai_chat_not_dispatched" || errorCode === "ai_request_cancelled";
}

export async function requestAiConversationPage(
  options: AiConversationPageRequestOptions = {},
  fetcher: AiFetch = (input, init) => fetch(input, init),
): Promise<AiConversationPagePayload> {
  const page = options.page ?? 1;
  const pageSize = options.pageSize ?? 30;
  const response = await requestAiInitializationResponse(
    () => fetcher(`/api/ai/conversations?page=${page}&pageSize=${pageSize}${options.workspaceModule ? `&workspaceModule=${options.workspaceModule}` : ""}`, {
      cache: "no-store",
      signal: options.signal,
    }),
  );
  const payload = await response.json().catch(() => null) as AiConversationPagePayload | null;
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: payload?.code,
      message: payload?.error || "读取对话记录失败",
    });
  }
  return payload ?? {};
}

export async function requestAiConversationPageWithIdentityRecovery(
  options: AiConversationPageRequestOptions & { currentUser: CurrentUser | null },
  fetcher: AiFetch = (input, init) => fetch(input, init),
): Promise<AiConversationPagePayload> {
  const { currentUser, ...requestOptions } = options;
  return requestAiInitializationWithIdentityRecovery(
    currentUser,
    requestOptions.signal,
    fetcher,
    () => requestAiConversationPage(requestOptions, fetcher),
  );
}

export async function requestAiAssistantConfiguration(
  signal?: AbortSignal,
  fetcher: AiFetch = (input, init) => fetch(input, init),
): Promise<{ models: AiModelRecord[]; channels: AiChannelRecord[] }> {
  const [modelsResponse, channelsResponse] = await requestAiInitializationResponse(() => Promise.all([
    fetcher("/api/ai/models", { cache: "no-store", signal }),
    fetcher("/api/ai/channels", { cache: "no-store", signal }),
  ]));
  const [modelsPayload, channelsPayload] = await Promise.all([
    modelsResponse.json().catch(() => null) as Promise<{ items?: AiModelRecord[]; error?: string; code?: string } | null>,
    channelsResponse.json().catch(() => null) as Promise<{ items?: AiChannelRecord[]; error?: string; code?: string } | null>,
  ]);
  if (!modelsResponse.ok) {
    throw new ApiError({
      status: modelsResponse.status,
      code: modelsPayload?.code,
      message: modelsPayload?.error || "读取模型配置失败",
    });
  }
  if (!channelsResponse.ok) {
    throw new ApiError({
      status: channelsResponse.status,
      code: channelsPayload?.code,
      message: channelsPayload?.error || "读取渠道配置失败",
    });
  }
  return { models: modelsPayload?.items ?? [], channels: channelsPayload?.items ?? [] };
}

export async function requestAiAssistantConfigurationWithIdentityRecovery(
  options: { currentUser: CurrentUser | null; signal?: AbortSignal },
  fetcher: AiFetch = (input, init) => fetch(input, init),
): Promise<{ models: AiModelRecord[]; channels: AiChannelRecord[] }> {
  return requestAiInitializationWithIdentityRecovery(
    options.currentUser,
    options.signal,
    fetcher,
    () => requestAiAssistantConfiguration(options.signal, fetcher),
  );
}

async function requestAiInitializationResponse<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError({
      status: 0,
      code: "network_error",
      message: "网络请求失败",
      cause: error,
    });
  }
}

async function requestAiInitializationWithIdentityRecovery<T>(
  currentUser: CurrentUser | null,
  signal: AbortSignal | undefined,
  fetcher: AiFetch,
  operation: () => Promise<T>,
): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (!currentUser || signal?.aborted || !isRecoverableAiInitializationError(error)) throw error;
    const identityMatches = await revalidateAiInitializationIdentity(currentUser, signal, fetcher);
    if (!identityMatches) throw error;
    return operation();
  }
}

function isRecoverableAiInitializationError(error: unknown): boolean {
  return error instanceof ApiError
    && (error.status === 0 || error.status === 401 || error.status >= 500);
}

async function revalidateAiInitializationIdentity(
  currentUser: CurrentUser,
  signal: AbortSignal | undefined,
  fetcher: AiFetch,
): Promise<boolean> {
  try {
    const response = await fetcher("/api/auth/me", { cache: "no-store", signal });
    if (!response.ok) return false;
    const payload = await response.json().catch(() => null) as AiIdentityPayload | null;
    return payload?.user?.email.trim().toLowerCase() === currentUser.email.trim().toLowerCase();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    return false;
  }
}


const formatDateTime = (value?: string | null) => {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
};

function newAiModelDraft(): AiModelDraft {
  return {
    name: "",
    protocol: "openai_compatible",
    modelType: "text",
    modelName: "",
    baseUrl: "",
    apiKey: "",
    status: "enabled",
    isDefaultTextModel: false,
    maxTokens: 65536,
    generationOptions: { ...DEFAULT_AI_GENERATION, temperatureMode: "default" },
    reasoningMode: "auto",
    temperatureMilli: 200,
    maxToolRounds: 6,
    maxTotalToolCalls: 12,
  };
}

function aiModelTypeLabel(type: AiModelType): string {
  return type === "vision" ? "视觉识别（读取图片）" : "文本对话";
}

function newAiChannelDraft(): AiChannelDraft {
  return { name: "", kind: "dingtalk_group_bot", status: "enabled", sendEnabled: true, callbackEnabled: false, webhookUrl: "", callbackToken: "", aesKey: "", receiverId: "" };
}

function channelKindLabel(kind: AiChannelKind): string {
  return ({ dingtalk_group_bot: "钉钉群机器人", wechat_work_group_bot: "企业微信群机器人", dingtalk_app: "钉钉应用（暂未启用）", wechat_work_app: "企业微信应用回调" })[kind];
}

function AiMessageArtifacts({ artifacts }: { artifacts: AiTableArtifact[] }) {
  if (artifacts.length === 0) return null;
  return <div className="ai-artifact-list" aria-label="本条回复的数据产物">
    {artifacts.map((artifact) => <section key={artifact.id} className="ai-artifact-card">
      <header><div><strong>{artifact.title}</strong><small>来源工具：{artifact.sourceTool} · 展示 {artifact.rows.length}/{artifact.rowCount} 行{artifact.truncated ? " · 已截断" : ""}</small></div><a href={artifact.downloadUrl} download={artifact.fileName}>下载 CSV</a></header>
      <div className="ai-artifact-table-wrap">
        <table data-column-filter-scope={artifact.truncated ? "none" : "full"} data-column-filter-total={artifact.rowCount}><thead><tr>{artifact.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead><tbody>{artifact.rows.map((row, rowIndex) => <tr key={`${artifact.id}-${rowIndex}`}>{artifact.columns.map((column, columnIndex) => <td key={`${column}-${columnIndex}`}>{formatAiArtifactCell(row[columnIndex])}</td>)}</tr>)}</tbody></table>
      </div>
    </section>)}
  </div>;
}

function AiPromotionReportLinks({ message }: { message: AiConversationMessage }) {
  const [busy, setBusy] = useState<"html" | "xlsx" | null>(null);
  const [error, setError] = useState("");
  const locator = message.execution?.promotionReport;
  if (message.role !== "assistant" || !locator || locator.shopName !== "志高商用设备旗舰店"
    || !/^20\d\d-\d\d-\d\d$/.test(locator.startDate)
    || !/^20\d\d-\d\d-\d\d$/.test(locator.endDate)
    || !/^\d+:[0-9a-f]{12}$/.test(locator.sourceRevision)) return null;
  const query = new URLSearchParams({ conversationId: message.conversationId, messageId: message.id });
  const link = (format: "html" | "xlsx") => `/api/netshop/promotion-diagnostic/chat-report?${query.toString()}&format=${format}`;
  async function download(format: "html" | "xlsx") {
    if (!locator || busy) return;
    setBusy(format); setError("");
    try {
      const response = await fetch(link(format), { cache: "no-store" });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(body?.error || `下载失败（${response.status}），请重新生成诊断。`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${locator.shopName}_${locator.startDate}_${locator.endDate}_推广诊断.${format}`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "下载失败，请刷新并核对来源。");
    } finally { setBusy(null); }
  }
  return <section className="ai-artifact-card" aria-label="本条回复的推广诊断报告">
    <header><div><strong>京东推广深度诊断报告</strong><small>{locator.shopName} · {locator.startDate} 至 {locator.endDate} · 来源修订 {locator.sourceRevision}</small></div>
      <span><button type="button" className="secondary-button" disabled={busy !== null} onClick={() => void download("html")}>{busy === "html" ? "正在下载…" : "下载 HTML"}</button>{" "}<button type="button" className="secondary-button" disabled={busy !== null} onClick={() => void download("xlsx")}>{busy === "xlsx" ? "正在下载…" : "下载 XLSX"}</button></span></header>
    <p className="muted">文件由系统已导入数据生成；下载时重新核对权限与来源修订。来源变更后请重新发起诊断。</p>
    {error && <p role="alert" className="inventory-feedback inventory-feedback-error">{error}</p>}
  </section>;
}

const promotionEvidenceColumns = [
  ["name", "对象"], ["state", "对照状态"], ["spendCurrent", "本期花费（元）"],
  ["spendPrevious", "前期花费（元）"], ["clicksCurrent", "本期点击"],
  ["clicksPrevious", "前期点击"], ["ordersCurrent", "本期归因订单行"],
  ["ordersPrevious", "前期归因订单行"], ["orderRateCurrent", "本期点击→订单行率（%）"],
  ["orderRatePrevious", "前期点击→订单行率（%）"], ["roasCurrent", "本期归因 ROAS"],
  ["planKey", "计划来源键"], ["planId", "计划 ID"], ["skuId", "SKU"],
  ["keyword", "关键词"], ["searchTerm", "搜索词"], ["groupKey", "来源键"],
] as const;

function PromotionEvidenceRows({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  if (!rows.length) return <p className="muted">当前证据页没有匹配行。</p>;
  const columns = promotionEvidenceColumns.filter(([key]) => rows.some((row) => Object.hasOwn(row, key)));
  return <div className="ai-artifact-table-wrap"><table><thead><tr>{columns.map(([key, label]) => <th key={key} scope="col">{label}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{columns.map(([key]) => <td key={key}>{formatAiArtifactCell(row[key])}</td>)}</tr>)}</tbody></table></div>;
}

function AiPromotionEvidenceCard({ message }: { message: AiConversationMessage }) {
  const evidence = message.execution?.promotionEvidence;
  if (message.role !== "assistant" || !evidence || evidence.shopName !== "志高商用设备旗舰店"
    || !/^20\d\d-\d\d-\d\d$/.test(evidence.startDate) || !/^20\d\d-\d\d-\d\d$/.test(evidence.endDate)
    || !/^\d+:[0-9a-f]{12}$/.test(evidence.sourceRevision)) return null;
  return <section className="ai-artifact-card" aria-label="本条回复的推广对象依据"><details>
    <summary>查看本条回答的对象依据 · {evidence.startDate} 至 {evidence.endDate} · 来源修订 {evidence.sourceRevision}</summary>
    {evidence.mode === "table" ? <>
      <p className="muted">{evidence.title || evidence.tableKey} · 第 {evidence.page ?? 1} 页 · 展示最多 5 行 / 匹配 {evidence.totalRows ?? 0} 行{evidence.hasMore ? "；其余可继续追问分页" : ""}</p>
      <PromotionEvidenceRows rows={evidence.rows ?? []} />
    </> : <>
      <p className="muted">精确对象：{evidence.target?.tableKey} · {evidence.target?.groupKey}。关联仅表示同一来源行的真实关系，各视角不可相加。</p>
      <PromotionEvidenceRows rows={evidence.targetEvidence ? [evidence.targetEvidence] : []} />
      {evidence.relationCoverage === "previous_only_not_queried" && <p role="status" className="muted">此对象仅在前期存在；本期关系不适用，前期 SKU/词关系未查询，不能把空列表当作没有关联。</p>}
      {(evidence.relations ?? []).map((item, index) => <div key={`${item.tableKey}-${index}`}>
        <p className="muted">{item.label} · 展示最多 2 行 / 匹配 {item.totalRows} 行</p><PromotionEvidenceRows rows={item.rows} />
      </div>)}
    </>}
  </details></section>;
}

function formatAiArtifactCell(value: AiArtifactCell | undefined) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

function AiAssistantView({
  currentUser,
  initialContextPrompt = "",
  initialPageContext = null,
  workspace = "chat",
  workspaceModule,
  compact = false,
  contextRequestId = 0,
  contextError = "",
}: {
  currentUser: CurrentUser | null;
  initialContextPrompt?: string;
  initialPageContext?: AiPageContext | null;
  workspace?: "chat" | "management";
  workspaceModule?: AiPageModule;
  compact?: boolean;
  contextRequestId?: number;
  contextError?: string;
}) {
  const showChat = workspace === "chat";
  const showManagement = workspace === "management";
  const isAdmin = currentUser?.role === "admin" && !currentUser.scopeRestricted;
  const canChat = Boolean(currentUser && currentUser.role !== "viewer");
  const [modelItems, setModelItems] = useState<AiModelRecord[]>([]);
  const [availableChatModels, setAvailableChatModels] = useState<AiAvailableChatModel[]>([]);
  const [channelItems, setChannelItems] = useState<AiChannelRecord[]>([]);
  const [conversationItems, setConversationItems] = useState<AiConversationRecord[]>([]);
  const [conversationLoaded, setConversationLoaded] = useState(false);
  const [conversationPagination, setConversationPagination] = useState<AiConversationPagination>({ page: 1, pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false });
  const [activeConversationId, setActiveConversationId] = useState("");
  const [messages, setMessages] = useState<AiConversationMessage[]>([]);
  const [messagePagination, setMessagePagination] = useState<AiMessagePagination>({ pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false, nextBefore: null });
  const [messageDraft, setMessageDraft] = useState("");
  const [pageContext, setPageContext] = useState<AiPageContext | null>(initialPageContext);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [modelDraft, setModelDraft] = useState<AiModelDraft>(() => newAiModelDraft());
  const [modelBaseUrlDirty, setModelBaseUrlDirty] = useState(false);
  const [channelDraft, setChannelDraft] = useState<AiChannelDraft>(() => newAiChannelDraft());
  const [chatLoading, setChatLoading] = useState(showChat);
  const [configurationState, setConfigurationState] = useState<AiConfigurationState>("idle");
  const [configurationLoaded, setConfigurationLoaded] = useState(false);
  const [configurationError, setConfigurationError] = useState("");
  const [configurationNotice, setConfigurationNotice] = useState("");
  const [sending, setSending] = useState(false);
  const [liveAnswer, setLiveAnswer] = useState<LiveAiAnswer | null>(null);
  const [busyConversationId, setBusyConversationId] = useState("");
  const [switchingModel, setSwitchingModel] = useState(false);
  const [savingModel, setSavingModel] = useState(false);
  const [savingChannel, setSavingChannel] = useState(false);
  const [busyConfigId, setBusyConfigId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loadingMoreConversations, setLoadingMoreConversations] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const sendControllerRef = useRef<AbortController | null>(null);
  const refreshControllerRef = useRef<AbortController | null>(null);
  const conversationControllerRef = useRef<AbortController | null>(null);
  const messageControllerRef = useRef<AbortController | null>(null);
  const configurationControllerRef = useRef<AbortController | null>(null);
  const pendingChatRequestRef = useRef<PendingAiChatRequest | null>(null);
  const refreshGenerationRef = useRef(0);
  const conversationGenerationRef = useRef(0);
  const messageGenerationRef = useRef(0);
  const configurationGenerationRef = useRef(0);
  const appliedContextPromptRef = useRef("");
  const mountedRef = useRef(true);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [activatingConversation, setActivatingConversation] = useState(false);
  const conversationMutationRef = useRef(false);
  const conversationLoadedRef = useRef(false);
  const navigationGenerationRef = useRef(0);
  const currentContextRef = useRef(initialPageContext);
  useEffect(() => { currentContextRef.current = initialPageContext; }, [initialPageContext]);
  const conversationBusy = sending || chatLoading || activatingConversation || switchingModel || Boolean(busyConversationId);
  const recoveryKey = workspaceModule && currentUser ? `teruisi.ai.pending.v1:${currentUser.email.toLowerCase()}:${workspaceModule}` : "";
  const rememberPending = useCallback((id: string | null) => {
    if (!recoveryKey) return;
    try { if (id) sessionStorage.setItem(recoveryKey, id); else sessionStorage.removeItem(recoveryKey); } catch { /* Server receipts remain authoritative. */ }
  }, [recoveryKey]);
  useEffect(() => {
    if (initialPageContext) setPageContext(initialPageContext);
  }, [initialPageContext]);

  useEffect(() => {
    const prompt = Array.from(initialContextPrompt.trim()).slice(0, 4_000).join("");
    const contextKey = JSON.stringify(initialPageContext ?? null);
    const applicationKey = `${prompt}\n${contextRequestId || contextKey}`;
    if (!prompt || sending || applicationKey === appliedContextPromptRef.current) return;
    appliedContextPromptRef.current = applicationKey;
    setMessageDraft(prompt);
    setPageContext(initialPageContext);
    setNotice(initialPageContext
      ? `已带入“${initialPageContext.moduleLabel} / ${initialPageContext.view}”页面上下文；请确认问题后发送。`
      : "已带入页面问题草稿；请确认后发送。");
  }, [initialContextPrompt, initialPageContext, contextRequestId, sending]);

  const loadConfiguration = useCallback(async () => {
    const generation = ++configurationGenerationRef.current;
    configurationControllerRef.current?.abort();
    if (!isAdmin) {
      setModelItems([]);
      setChannelItems([]);
      setConfigurationLoaded(false);
      setConfigurationState("idle");
      setConfigurationError("");
      return;
    }
    const controller = new AbortController();
    configurationControllerRef.current = controller;
    setConfigurationState("loading");
    setConfigurationError("");
    try {
      const payload = await requestAiAssistantConfigurationWithIdentityRecovery({
        currentUser,
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation !== configurationGenerationRef.current) return;
      setModelItems(payload.models);
      setChannelItems(payload.channels);
      setConfigurationLoaded(true);
      setConfigurationState("ready");
    } catch (reason) {
      if (controller.signal.aborted || generation !== configurationGenerationRef.current) return;
      setConfigurationError(reason instanceof Error ? reason.message : "读取 AI 配置失败");
      setConfigurationState("error");
    } finally {
      if (configurationControllerRef.current === controller) configurationControllerRef.current = null;
    }
  }, [currentUser, isAdmin]);

  const loadConversations = useCallback(async (options: { page?: number; append?: boolean; signal?: AbortSignal } = {}) => {
    const page = options.page ?? 1;
    const generation = ++conversationGenerationRef.current;
    conversationControllerRef.current?.abort();
    const controller = new AbortController();
    conversationControllerRef.current = controller;
    const relayAbort = () => controller.abort();
    if (options.signal?.aborted) controller.abort();
    else options.signal?.addEventListener("abort", relayAbort, { once: true });
    try {
      const payload = await requestAiConversationPageWithIdentityRecovery({
        currentUser,
        workspaceModule,
        page,
        pageSize: 30,
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation !== conversationGenerationRef.current) return;
      const items = payload.items ?? [];
      const models = payload.models ?? [];
      const firstLoad = !conversationLoadedRef.current;
      if (!options.append) { setConversationLoaded(true); conversationLoadedRef.current = true; }
      setConversationItems((current) => {
        if (!options.append) return items;
        const merged = new Map(current.map((item) => [item.id, item]));
        items.forEach((item) => merged.set(item.id, item));
        return Array.from(merged.values());
      });
      setConversationPagination(payload.pagination ?? { page, pageSize: 30, total: items.length, returned: items.length, truncated: false, hasMore: false });
      setAvailableChatModels(models);
      setSelectedModelId((current) => models.some((model) => model.id === current) ? current : models.find((model) => model.isDefault)?.id || models[0]?.id || "");
      if (!options.append && firstLoad) setActiveConversationId(current => current || items[0]?.id || "");
      const pending = pendingChatRequestRef.current;
      if (pending?.response && items.some((item) => item.id === pending.response!.conversationId)) {
        const synchronized = markPendingAiChatSynchronized(pending, {
          kind: "conversation",
          conversationId: pending.response.conversationId,
        });
        pendingChatRequestRef.current = synchronized;
        if (!synchronized) { rememberPending(null); setRecoveryBlocked(false); setNotice("消息已发送并完成服务端同步。"); }
      }
    } catch (reason) {
      if (controller.signal.aborted || generation !== conversationGenerationRef.current) return;
      throw reason;
    } finally {
      options.signal?.removeEventListener("abort", relayAbort);
      if (conversationControllerRef.current === controller) conversationControllerRef.current = null;
    }
  }, [currentUser, workspaceModule, rememberPending]);

  const loadMessages = useCallback(async (conversationId: string, options: { before?: number | null; appendOlder?: boolean } = {}) => {
    const generation = ++messageGenerationRef.current;
    messageControllerRef.current?.abort();
    const controller = new AbortController();
    messageControllerRef.current = controller;
    try {
      const params = new URLSearchParams({ conversationId, pageSize: "30" });
      if (workspaceModule) params.set("workspaceModule", workspaceModule);
      if (options.before) params.set("before", String(options.before));
      const response = await fetch(`/api/ai/chat?${params.toString()}`, { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as { items?: AiConversationMessage[]; conversation?: AiConversationRecord; pagination?: AiMessagePagination; error?: string } | null;
      if (!response.ok) throw new Error(payload?.error || "读取对话失败");
      if (controller.signal.aborted || generation !== messageGenerationRef.current) return;
      const items = payload?.items ?? [];
      if ((workspaceModule && payload?.conversation?.id !== conversationId)
        || items.some(item => item.conversationId !== conversationId)) throw new Error("会话响应不匹配");
      if (!currentContextRef.current && !options.appendOlder) setPageContext(normalizeAiPageContext(payload?.conversation?.pageContext));
      setMessages((current) => {
        if (!options.appendOlder) return items;
        const merged = new Map<string, AiConversationMessage>();
        items.forEach((item) => merged.set(item.id, item));
        current.forEach((item) => merged.set(item.id, item));
        return Array.from(merged.values());
      });
      setMessagePagination(payload?.pagination ?? { pageSize: 30, total: items.length, returned: items.length, truncated: false, hasMore: false, nextBefore: null });
      const pending = pendingChatRequestRef.current;
      if (pending?.response && pending.response.conversationId === conversationId
        && items.some((item) => item.id === pending.response!.assistantMessageId)) {
        const synchronized = markPendingAiChatSynchronized(pending, {
          kind: "message",
          conversationId,
          assistantMessageId: pending.response.assistantMessageId,
        });
        pendingChatRequestRef.current = synchronized;
        if (!synchronized) { rememberPending(null); setRecoveryBlocked(false); setNotice("消息已发送并完成服务端同步。"); }
      }
    } catch (reason) {
      if (controller.signal.aborted || generation !== messageGenerationRef.current) return;
      throw reason;
    } finally {
      if (messageControllerRef.current === controller) messageControllerRef.current = null;
    }
  }, [workspaceModule, rememberPending]);

  const expandMessage = async (id: string) => {
    const conversationId = activeConversationId;
    const generation = messageGenerationRef.current;
    try {
      const params = new URLSearchParams({ conversationId, messageId: id });
      if (workspaceModule) params.set("workspaceModule", workspaceModule);
      const response = await fetch(`/api/ai/chat?${params}`, { cache: "no-store" });
      const payload = await response.json() as { items?: AiConversationMessage[]; error?: string };
      if (!response.ok) throw new Error(payload.error || "读取完整回复失败");
      const item = payload.items?.find(message => message.id === id && message.conversationId === conversationId);
      if (!item) throw new Error("消息响应不匹配");
      if (mountedRef.current && generation === messageGenerationRef.current) setMessages(current => current.map(message => message.id === id ? item : message));
    } catch (reason) {
      if (mountedRef.current && generation === messageGenerationRef.current) setError(reason instanceof Error ? reason.message : "读取失败");
    }
  };

  const recoverPending = useCallback(async (signal: AbortSignal) => {
    if (!recoveryKey || !workspaceModule) return;
    let id: string | null = null;
    try { id = sessionStorage.getItem(recoveryKey); } catch { return; }
    if (!id) return;
    const generation = navigationGenerationRef.current;
    const isCurrent = () => {
      if (!mountedRef.current || generation !== navigationGenerationRef.current) return false;
      try { return sessionStorage.getItem(recoveryKey) === id; } catch { return false; }
    };
    setRecoveryBlocked(true);
    const recovered = await recoverAiWorkspaceRequest({ clientRequestId: id, module: workspaceModule, signal, isCurrent });
    if (signal.aborted || !isCurrent()) return;
    if (recovered) {
      rememberPending(null);
      pendingChatRequestRef.current = null;
      setRecoveryBlocked(false);
      setMessages([]);
      setActiveConversationId(recovered.conversationId);
      await loadMessages(recovered.conversationId);
      if (signal.aborted || !mountedRef.current || generation !== navigationGenerationRef.current) return;
      setNotice("上次回复已恢复，可继续提问。");
    } else setNotice("上次请求尚未确认完成。请刷新查看结果；如需另开话题，请新建对话。系统不会自动重发。");
  }, [recoveryKey, workspaceModule, rememberPending, loadMessages]);

  const refresh = useCallback(async () => {
    if (sendControllerRef.current || conversationMutationRef.current) return;
    const generation = ++refreshGenerationRef.current;
    refreshControllerRef.current?.abort();
    const controller = new AbortController();
    refreshControllerRef.current = controller;
    setChatLoading(true); setError("");
    try {
      await loadConversations({ signal: controller.signal });
      await recoverPending(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted && generation === refreshGenerationRef.current) setError(reason instanceof Error ? reason.message : "AI 助理加载失败");
    } finally {
      if (refreshControllerRef.current === controller) refreshControllerRef.current = null;
      if (generation === refreshGenerationRef.current) setChatLoading(false);
    }
  }, [loadConversations, recoverPending]);

  useEffect(() => {
    if (!showChat) return;
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh, showChat]);

  useEffect(() => {
    if (!showManagement || !isAdmin || configurationState !== "idle") return;
    void loadConfiguration();
  }, [configurationState, isAdmin, loadConfiguration, showManagement]);

  useEffect(() => {
    if (!showChat) return;
    if (!activeConversationId) {
      messageControllerRef.current?.abort();
      messageGenerationRef.current += 1;
      const timer = window.setTimeout(() => {
        setMessages([]);
        setMessagePagination({ pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false, nextBefore: null });
      }, 0);
      return () => window.clearTimeout(timer);
    }
    void loadMessages(activeConversationId)
      .catch((reason: unknown) => { if (!(reason instanceof Error && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "读取对话失败"); });
    return () => {
      messageControllerRef.current?.abort();
      messageGenerationRef.current += 1;
    };
  }, [activeConversationId, loadMessages, showChat]);

  useEffect(() => {
    const activeConversation = conversationItems.find((item) => item.id === activeConversationId);
    if (!activeConversation?.modelId || !availableChatModels.some((model) => model.id === activeConversation.modelId)) return;
    const timer = window.setTimeout(() => setSelectedModelId(activeConversation.modelId ?? ""), 0);
    return () => window.clearTimeout(timer);
  }, [activeConversationId, availableChatModels, conversationItems]);

  useEffect(() => { mountedRef.current = true; return () => {
    mountedRef.current = false;
    sendControllerRef.current?.abort();
    refreshControllerRef.current?.abort();
    conversationControllerRef.current?.abort();
    messageControllerRef.current?.abort();
    configurationControllerRef.current?.abort();
    configurationGenerationRef.current += 1;
  }; }, []);

  const sendMessage = async () => {
    const text = messageDraft.trim();
    if (!text || conversationBusy || conversationMutationRef.current || sendControllerRef.current || recoveryBlocked || (contextError && pageContext) || !canChat) return;
    const requestPayload = {
      conversationId: activeConversationId || undefined,
      modelId: selectedModelId || undefined,
      message: text,
      title: Array.from(text).slice(0, 40).join(""),
      workspaceModule,
      pageContext: workspaceModule ? pageContext : pageContext ?? undefined,
    } satisfies AiChatPostPayload;
    const pendingRequest = resolvePendingAiChatRequest(pendingChatRequestRef.current, requestPayload);
    rememberPending(pendingRequest.clientRequestId);
    pendingChatRequestRef.current = pendingRequest;
    const clientRequestId = pendingRequest.clientRequestId;
    const controller = new AbortController();
    sendControllerRef.current = controller;
    setSending(true); setError(""); setNotice("");
    setLiveAnswer({ prompt: text, content: "", stage: "正在连接", tools: [] });
    let deltaBuffer = "";
    let deltaTimer: ReturnType<typeof setTimeout> | undefined;
    const flushDelta = () => {
      clearTimeout(deltaTimer); deltaTimer = undefined;
      const content = deltaBuffer; deltaBuffer = "";
      if (content && mountedRef.current) setLiveAnswer(current => current ? { ...current, content: (current.content + content).slice(0, MAX_AI_REPLY_CHARACTERS) } : null);
    };
    try {
      const response = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ ...pendingRequest.requestPayload, clientRequestId }),
        signal: controller.signal,
      });
      const payload = (response.ok && response.headers.get("content-type")?.includes("text/event-stream")
        ? await readAiChatStream(response, (event, value) => {
          if (!mountedRef.current) return;
          if (event === "delta") {
            deltaBuffer += value.content as string;
            if (!deltaTimer) deltaTimer = setTimeout(flushDelta, 80);
            return;
          }
          if (event === "reset") { clearTimeout(deltaTimer); deltaTimer = undefined; deltaBuffer = ""; }
          setLiveAnswer(current => {
            if (!current) return current;
            if (event === "reset") return { ...current, content: "", stage: typeof value.stage === "string" ? value.stage : "正在生成" };
            if (event === "status") return { ...current, stage: typeof value.stage === "string" ? value.stage : current.stage };
            if (event === "tool" && value.ok === true && typeof value.title === "string") return { ...current, tools: [...current.tools, value.title].slice(0, 74) };
            return current;
          });
        }, controller.signal)
        : await response.json().catch(() => null)) as {
        conversationId?: string;
        assistantMessageId?: string;
        reply?: string;
        execution?: AiExecutionInfo;
        modelId?: string | null;
        outcome?: AiConversationMessage["messageKind"] | "answered";
        artifacts?: AiTableArtifact[];
        error?: string;
        code?: string;
      } | null;
      if (!mountedRef.current) return;
      if (!response.ok) {
        if (shouldReleasePendingAiChatRequest(payload?.code)
          && pendingChatRequestRef.current?.clientRequestId === clientRequestId) {
          pendingChatRequestRef.current = null;
          rememberPending(null);
        }
        throw new Error(payload?.error || "发送失败");
      }
      if (!payload?.conversationId || !payload.assistantMessageId || typeof payload.reply !== "string") {
        throw new Error("模型已响应，但服务端消息回执不完整；已保留请求号，请刷新后确认。");
      }
      const conversationId = payload.conversationId;
      pendingChatRequestRef.current = attachPendingAiChatResponse(pendingRequest, {
        conversationId,
        assistantMessageId: payload.assistantMessageId,
      });
      clearTimeout(deltaTimer); deltaBuffer = "";
      setMessageDraft("");
      setLiveAnswer(null);
      if (payload?.modelId) setSelectedModelId(payload.modelId);
      setActiveConversationId(conversationId);
      const visibleMessage: AiConversationMessage = {
        id: payload.assistantMessageId,
        conversationId,
        role: "assistant",
        content: payload.reply,
        messageKind: payload.outcome === "context_reset" || payload.outcome === "help" ? payload.outcome : "message",
        createdAt: new Date().toISOString(),
        artifacts: payload.artifacts ?? [],
        execution: payload.execution,
        contentBytes: new TextEncoder().encode(payload.reply).byteLength,
        contentTruncated: false,
      };
      setMessages((current) => {
        const scoped = current.every((item) => item.conversationId === conversationId) ? current : [];
        return scoped.some((item) => item.id === visibleMessage.id) ? scoped : [...scoped, visibleMessage];
      });
      setNotice("消息已发送，正在核对服务端对话记录；核对完成前会保留原请求号。");
      await Promise.allSettled([
        loadConversations(),
        loadMessages(conversationId),
      ]);
      if (pendingChatRequestRef.current?.clientRequestId === clientRequestId) {
        setNotice("消息已发送，但服务端同步尚未完整确认；原请求号已保留，再试只会读取原结果，不会重复调用模型。");
      }
    } catch (reason) {
      if (!mountedRef.current) { clearTimeout(deltaTimer); return; }
      flushDelta();
      if (reason instanceof AiChatStreamError && shouldReleasePendingAiChatRequest(reason.code)) {
        pendingChatRequestRef.current = null; rememberPending(null);
      }
      setLiveAnswer(current => current ? { ...current, incomplete: true, stage: controller.signal.aborted ? "已停止生成" : "生成中断" } : null);
      if (controller.signal.aborted) {
        setNotice("已停止等待；请求号已保留，系统会阻止同一消息重复调用模型。请刷新对话记录确认结果。");
        if (activeConversationId) await loadMessages(activeConversationId).catch(() => undefined);
        await loadConversations().catch(() => undefined);
      } else setError(reason instanceof Error ? reason.message : "发送失败");
    } finally {
      if (sendControllerRef.current === controller) sendControllerRef.current = null;
      if (mountedRef.current) setSending(false);
    }
  };

  const startNewConversation = () => {
    if (conversationBusy || conversationMutationRef.current) return;
    navigationGenerationRef.current += 1;
    rememberPending(null);
    setRecoveryBlocked(false);
    messageControllerRef.current?.abort();
    messageGenerationRef.current += 1;
    setActiveConversationId("");
    setMessages([]);
    setLiveAnswer(null);
    setMessagePagination({ pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false, nextBefore: null });
    setMessageDraft("");
    pendingChatRequestRef.current = null;
    setError("");
    setNotice("");
    setSelectedModelId(availableChatModels.find((model) => model.isDefault)?.id || availableChatModels[0]?.id || "");
  };

  const openConversation = async (conversationId: string) => {
    if (conversationBusy || conversationMutationRef.current || conversationId === activeConversationId) return;
    conversationMutationRef.current = true;
    setActivatingConversation(true);
    navigationGenerationRef.current += 1;
    messageControllerRef.current?.abort();
    messageGenerationRef.current += 1;
    setError(""); setNotice("");
    try {
      if (workspaceModule && canChat) {
        const response = await fetch("/api/ai/conversations", {
          method: "PATCH", headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: "activate", conversationId, workspaceModule }),
        });
        if (!response.ok) throw new Error("未能保存本次会话选择，请重试。");
      }
      if (!mountedRef.current) return;
      pendingChatRequestRef.current = null;
      rememberPending(null); setRecoveryBlocked(false);
      setMessages([]);
      setMessagePagination({ pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false, nextBefore: null });
      setActiveConversationId(conversationId);
    } catch (reason) {
      if (mountedRef.current) setError(reason instanceof Error ? reason.message : "打开对话失败");
    } finally {
      conversationMutationRef.current = false;
      if (mountedRef.current) setActivatingConversation(false);
    }
  };

  const loadMoreConversations = async () => {
    if (conversationBusy || !conversationPagination.hasMore || loadingMoreConversations) return;
    setLoadingMoreConversations(true); setError("");
    try {
      await loadConversations({ page: conversationPagination.page + 1, append: true });
    } catch (reason) {
      if (!(reason instanceof Error && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "读取更多对话失败");
    } finally {
      setLoadingMoreConversations(false);
    }
  };

  const loadOlderMessages = async () => {
    if (!activeConversationId || !messagePagination.hasMore || !messagePagination.nextBefore || loadingOlderMessages) return;
    setLoadingOlderMessages(true); setError("");
    try {
      await loadMessages(activeConversationId, { before: messagePagination.nextBefore, appendOlder: true });
    } catch (reason) {
      if (!(reason instanceof Error && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "读取更早消息失败");
    } finally {
      setLoadingOlderMessages(false);
    }
  };

  const changeConversationModel = async (modelId: string) => {
    if (conversationBusy || conversationMutationRef.current || recoveryBlocked) return;
    pendingChatRequestRef.current = null;
    rememberPending(null);
    if (!activeConversationId) {
      setSelectedModelId(modelId);
      return;
    }
    conversationMutationRef.current = true;
    setSwitchingModel(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/ai/conversations", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ conversationId: activeConversationId, modelId }),
      });
      const payload = await response.json().catch(() => null) as { item?: AiConversationRecord; error?: string } | null;
      if (!mountedRef.current) return;
      if (!response.ok || !payload?.item || payload.item.id !== activeConversationId) throw new Error(payload?.error || "切换对话模型失败");
      setSelectedModelId(modelId);
      setConversationItems((items) => items.map((item) => item.id === payload.item?.id ? payload.item : item));
      setNotice(`本对话后续消息将使用“${availableChatModels.find((model) => model.id === modelId)?.name || "所选模型"}”。`);
    } catch (reason) {
      if (mountedRef.current) setError(reason instanceof Error ? reason.message : "切换对话模型失败");
    } finally {
      conversationMutationRef.current = false;
      if (mountedRef.current) setSwitchingModel(false);
    }
  };

  const deleteConversation = async (item: AiConversationRecord) => {
    if (conversationBusy || conversationMutationRef.current || !canChat || !window.confirm(`确定删除对话“${item.title}”吗？对话消息和生成的数据产物将一并删除，此操作无法撤销。`)) return;
    conversationMutationRef.current = true;
    setBusyConversationId(item.id); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/ai/conversations?id=${encodeURIComponent(item.id)}`, { method: "DELETE" });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!mountedRef.current) return;
      if (!response.ok) throw new Error(payload?.error || "删除对话失败");
      const remaining = conversationItems.filter((conversation) => conversation.id !== item.id);
      setConversationItems(current => current.filter(conversation => conversation.id !== item.id));
      if (activeConversationId === item.id) {
        messageControllerRef.current?.abort();
        messageGenerationRef.current += 1;
        setActiveConversationId(remaining[0]?.id || "");
        setMessages([]);
        setMessagePagination({ pageSize: 30, total: 0, returned: 0, truncated: false, hasMore: false, nextBefore: null });
      }
      setNotice(`对话“${item.title}”已删除。`);
      await loadConversations();
    } catch (reason) {
      if (mountedRef.current) setError(reason instanceof Error ? reason.message : "删除对话失败");
    } finally {
      conversationMutationRef.current = false;
      if (mountedRef.current) setBusyConversationId("");
    }
  };

  const saveModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSavingModel(true); setConfigurationError(""); setConfigurationNotice("");
    try {
      const { baseUrl, ...draftWithoutBaseUrl } = modelDraft;
      const requestPayload = modelDraft.id && !modelBaseUrlDirty
        ? draftWithoutBaseUrl
        : { ...draftWithoutBaseUrl, baseUrl };
      const response = await fetch("/api/ai/models", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(requestPayload) });
      const payload = await response.json().catch(() => null) as { item?: AiModelRecord; error?: string } | null;
      if (!response.ok || !payload?.item) throw new Error(payload?.error || "保存模型失败");
      setConfigurationNotice(`模型“${payload.item.name}”已保存。`);
      setModelDraft(newAiModelDraft());
      setModelBaseUrlDirty(false);
      await loadConfiguration();
    } catch (reason) { setConfigurationError(reason instanceof Error ? reason.message : "保存模型失败"); }
    finally { setSavingModel(false); }
  };

  const saveChannel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSavingChannel(true); setConfigurationError(""); setConfigurationNotice("");
    try {
      const response = await fetch("/api/ai/channels", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(channelDraft) });
      const payload = await response.json().catch(() => null) as { item?: AiChannelRecord; error?: string } | null;
      if (!response.ok || !payload?.item) throw new Error(payload?.error || "保存渠道失败");
      setConfigurationNotice(`渠道“${payload.item.name}”已保存。`);
      setChannelDraft(newAiChannelDraft());
      await loadConfiguration();
    } catch (reason) { setConfigurationError(reason instanceof Error ? reason.message : "保存渠道失败"); }
    finally { setSavingChannel(false); }
  };

  const testConfiguration = async (type: "model" | "channel", id: string) => {
    setBusyConfigId(`${type}:${id}`); setConfigurationError(""); setConfigurationNotice("");
    try {
      const response = await fetch(type === "model" ? "/api/ai/models" : "/api/ai/channels", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "test", id }) });
      const payload = await response.json().catch(() => null) as { message?: string; error?: string } | null;
      if (!response.ok) throw new Error(payload?.error || "连通性测试失败");
      setConfigurationNotice(payload?.message || "连通性测试成功。");
      await loadConfiguration();
    } catch (reason) { setConfigurationError(reason instanceof Error ? reason.message : "连通性测试失败"); }
    finally { setBusyConfigId(""); }
  };

  const deleteConfiguration = async (type: "model" | "channel", id: string, name: string, expectedVersion?: number) => {
    if (!window.confirm(`确定删除“${name}”吗？已保存的密钥和回调配置将一并删除。`)) return;
    setBusyConfigId(`${type}:${id}`); setConfigurationError(""); setConfigurationNotice("");
    try {
      const query = type === "model"
        ? `?id=${encodeURIComponent(id)}&expectedVersion=${encodeURIComponent(String(expectedVersion))}`
        : `?id=${encodeURIComponent(id)}`;
      const response = await fetch(`${type === "model" ? "/api/ai/models" : "/api/ai/channels"}${query}`, { method: "DELETE" });
      const payload = await response.json().catch(() => null) as { error?: string } | null;
      if (!response.ok) throw new Error(payload?.error || "删除失败");
      setConfigurationNotice(`“${name}”已删除。`);
      await loadConfiguration();
    } catch (reason) { setConfigurationError(reason instanceof Error ? reason.message : "删除失败"); }
    finally { setBusyConfigId(""); }
  };

  const editModel = (item: AiModelRecord) => {
    setModelBaseUrlDirty(false);
    setModelDraft({
      id: item.id,
      expectedVersion: item.version,
      name: item.name,
      protocol: item.protocol,
      modelType: item.modelType,
      modelName: item.modelName,
      baseUrl: item.baseUrl,
      apiKey: "",
      status: item.status,
      isDefaultTextModel: item.isDefaultTextModel,
      maxTokens: item.maxTokens,
      generationOptions: item.generationOptions ?? { ...DEFAULT_AI_GENERATION, taskTimeoutMs: 260000 },
      reasoningMode: item.reasoningMode,
      temperatureMilli: item.temperatureMilli,
      maxToolRounds: item.maxToolRounds,
      maxTotalToolCalls: item.maxTotalToolCalls,
    });
  };
  const editChannel = (item: AiChannelRecord) => setChannelDraft({ id: item.id, name: item.name, kind: item.kind, status: item.status, sendEnabled: item.sendEnabled, callbackEnabled: item.callbackEnabled, webhookUrl: "", callbackToken: "", aesKey: "", receiverId: item.receiverId });

  const isEditingModel = Boolean(modelDraft.id);
  const isEditingChannel = Boolean(channelDraft.id);
  const channelSupportsOutbound = channelDraft.kind === "dingtalk_group_bot" || channelDraft.kind === "wechat_work_group_bot";
  const channelNeedsWebhook = channelDraft.kind === "dingtalk_group_bot" || channelDraft.kind === "wechat_work_group_bot" || channelDraft.sendEnabled;
  if (showChat && chatLoading && !conversationLoaded) return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取 AI 助理</strong><p>正在加载可用对话…</p></section>;

  return <section className={`ai-assistant-grid${compact ? " ai-chat-compact" : ""}`}>
    {contextError && pageContext && <p className="inventory-feedback inventory-feedback-error" role="alert">{contextError}</p>}
    {showChat && <AiChatWorkbench
      compact={compact} currentTitle={conversationItems.find(item => item.id === activeConversationId)?.title || ""}
      activeConversationId={activeConversationId} conversations={conversationItems} conversationTotal={conversationPagination.total}
      hasMoreConversations={conversationPagination.hasMore} loadingMoreConversations={loadingMoreConversations}
      messages={messages} hasOlderMessages={messagePagination.hasMore} loadingOlderMessages={loadingOlderMessages}
      models={availableChatModels} selectedModelId={selectedModelId} canChat={canChat} busy={conversationBusy}
      sending={sending} loading={chatLoading} recoveryBlocked={recoveryBlocked} draft={messageDraft}
      error={error} notice={notice} context={pageContext} contextError={contextError} liveAnswer={liveAnswer}
      onDraft={setMessageDraft} onSend={() => void sendMessage()} onStop={() => sendControllerRef.current?.abort()}
      onNew={startNewConversation} onOpen={id => { setLiveAnswer(null); void openConversation(id); }}
      onDelete={id => { const item = conversationItems.find(item => item.id === id); if (item) void deleteConversation(item); }}
      onModel={id => void changeConversationModel(id)} onMore={() => void loadMoreConversations()}
      onExpand={id => void expandMessage(id)} onOlder={() => void loadOlderMessages()} onRefresh={() => { setLiveAnswer(null); void refresh(); }}
      onRemoveContext={() => setPageContext(null)}
      renderArtifacts={id => { const message = messages.find(item => item.id === id); return <><AiMessageArtifacts artifacts={message?.artifacts ?? []} />{message && <><AiPromotionEvidenceCard message={message} /><AiPromotionReportLinks message={message} /></>}</>; }}
    />}
    {showManagement && (isAdmin ? <>
      <article className="panel ai-permission-card">
        <div className="section-header"><div><h2>AI 管理</h2><p>集中维护对话模型与聊天渠道；密钥始终加密保存，列表仅显示脱敏信息。</p></div><button type="button" className="secondary-button" onClick={() => void loadConfiguration()} disabled={configurationState === "loading"}>{configurationState === "loading" ? "刷新中…" : "刷新配置"}</button></div>
      </article>
      {!configurationLoaded && (configurationState === "idle" || configurationState === "loading") && <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取模型与渠道配置</strong><p>正在加载脱敏后的管理配置…</p></section>}
      {!configurationLoaded && configurationState === "error" && <section className="panel data-state data-state-error" role="alert"><span className="state-symbol">!</span><strong>配置管理暂时不可用</strong><p>{configurationError}</p><button type="button" className="secondary-button" onClick={() => void loadConfiguration()}>重新加载配置</button></section>}
      {(configurationState === "ready" || configurationLoaded) && <>
      <article className="panel ai-admin-card data-refresh-region" aria-busy={configurationState === "loading"}>
        {(configurationError || configurationNotice) && <div className={`inventory-feedback ${configurationError ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={configurationError ? "alert" : "status"}><span>{configurationError ? "!" : "✓"}</span><div><strong>{configurationError ? "配置操作失败" : "配置操作成功"}</strong><p>{configurationError || configurationNotice}</p></div></div>}
        <div className="section-header"><div><h3>{isEditingModel ? "编辑模型配置" : "新增模型配置"}</h3><p>支持 OpenAI 兼容接口和 Anthropic Messages 接口。密钥加密保存，列表只显示末四位。</p></div>{isEditingModel && <button type="button" className="text-button" onClick={() => { setModelDraft(newAiModelDraft()); setModelBaseUrlDirty(false); }}>取消编辑</button>}</div>
        <form className="ai-config-form" onSubmit={(event) => void saveModel(event)}>
          <label><span>配置名称</span><input value={modelDraft.name} required maxLength={100} onChange={(event) => setModelDraft((current) => ({ ...current, name: event.target.value }))} placeholder="例如：生产文本模型" /></label>
          <label><span>协议</span><SearchableSelect value={modelDraft.protocol} onChange={(value) => setModelDraft((current) => ({ ...current, protocol: value as AiModelProtocol, reasoningMode: value === "openai_compatible" ? current.reasoningMode : "auto" }))} ariaLabel="模型协议" searchPlaceholder="搜索模型协议" options={[{ value: "openai_compatible", label: "OpenAI 兼容" }, { value: "anthropic", label: "Anthropic" }]} /></label>
          <label><span>能力类型</span><SearchableSelect value={modelDraft.modelType} onChange={(value) => setModelDraft((current) => ({ ...current, modelType: value as AiModelType, isDefaultTextModel: value === "text" ? current.isDefaultTextModel : false }))} ariaLabel="模型能力类型" searchPlaceholder="搜索模型能力" options={[{ value: "text", label: "文本对话（不读取图片）" }, { value: "vision", label: "视觉识别（读取图片）" }]} /><small>市场主图价格识别必须选择“视觉识别”；连接测试会实际发送一张测试图。</small></label>
          <label><span>模型标识</span><input value={modelDraft.modelName} required maxLength={100} onChange={(event) => setModelDraft((current) => ({ ...current, modelName: event.target.value }))} placeholder="例如：gpt-4.1-mini" /></label>
          <label className="ai-form-wide"><span>API 地址</span><input value={modelDraft.baseUrl} required type="url" onChange={(event) => { setModelBaseUrlDirty(true); setModelDraft((current) => ({ ...current, baseUrl: event.target.value })); }} placeholder="https://api.example.com/v1" /><small>生产环境仅接受 HTTPS，且地址查询参数不能携带 API Key 或 Token；编辑时未修改地址会保留原配置。</small></label>
          <label><span>API Key</span><input value={modelDraft.apiKey} type="password" autoComplete="new-password" onChange={(event) => setModelDraft((current) => ({ ...current, apiKey: event.target.value }))} placeholder={isEditingModel ? "同一协议与 origin 可留空保留" : "输入模型密钥"} /><small>{isEditingModel ? "更换协议或服务 origin 时必须同时填写新密钥；原密钥不会转发。" : "保存后仅显示掩码。"}</small></label>
          <label><span>状态</span><SearchableSelect value={modelDraft.status} onChange={(value) => setModelDraft((current) => ({ ...current, status: value as AiModelStatus }))} ariaLabel="模型状态" searchPlaceholder="搜索模型状态" options={[{ value: "enabled", label: "启用" }, { value: "disabled", label: "停用" }]} /></label>
          <label><span>最大输出 Token</span><input type="number" min={128} max={131072} step={1} value={modelDraft.maxTokens} onChange={(event) => setModelDraft((current) => ({ ...current, maxTokens: Number(event.target.value) }))} /></label>
          <label><span>推理开关</span><SearchableSelect value={modelDraft.reasoningMode} onChange={(value) => setModelDraft((current) => ({ ...current, reasoningMode: value as AiModelReasoningMode }))} ariaLabel="文本推理模式" searchPlaceholder="搜索推理模式" disabled={modelDraft.protocol !== "openai_compatible"} options={[{ value: "auto", label: "跟随供应商默认" }, { value: "disabled", label: "关闭推理（端点须支持）" }]} /><small>仅在端点明确支持时关闭；需要更多思考空间可调整输出额度与下方推理参数。</small></label>
          <AiModelGenerationFields value={modelDraft.generationOptions} protocol={modelDraft.protocol} temperatureMilli={modelDraft.temperatureMilli} onTemperatureChange={temperatureMilli => setModelDraft(current => ({ ...current, temperatureMilli }))} onChange={generationOptions => setModelDraft(current => ({ ...current, generationOptions }))} />
          <label><span>最大工具轮数</span><input type="number" min={1} max={AI_MODEL_TOOL_BUDGET_LIMITS.maximumRounds} disabled={modelDraft.modelType !== "text"} value={modelDraft.maxToolRounds} onChange={(event) => setModelDraft((current) => ({ ...current, maxToolRounds: Number(event.target.value) }))} /></label>
          <label><span>工具调用总数</span><input type="number" min={1} max={AI_MODEL_TOOL_BUDGET_LIMITS.maximumTotalCalls} disabled={modelDraft.modelType !== "text"} value={modelDraft.maxTotalToolCalls} onChange={(event) => setModelDraft((current) => ({ ...current, maxTotalToolCalls: Number(event.target.value) }))} /><small>单轮不再另限 4 次；仍以此总数、执行时长和取消机制防止死循环，不能设置为真正无限。</small></label>
          <label className="ai-check-field"><input type="checkbox" checked={modelDraft.isDefaultTextModel} disabled={modelDraft.modelType !== "text" || modelDraft.status !== "enabled"} onChange={(event) => setModelDraft((current) => ({ ...current, isDefaultTextModel: event.target.checked }))} /><span>设为默认文本模型</span></label>
          <div className="ai-form-actions"><button type="submit" className="primary-button" disabled={savingModel}>{savingModel ? "保存中…" : isEditingModel ? "保存修改" : "新增模型"}</button></div>
        </form>
        <div className="ai-config-list">{modelItems.length === 0 && <p className="soft-text">暂无模型配置。新增并测试成功后，小特才能对话。</p>}{modelItems.map((item) => <div key={item.id} className="ai-config-card"><div><strong>{item.name}</strong><small>{aiModelTypeLabel(item.modelType)} · {item.protocol === "anthropic" ? "Anthropic" : "OpenAI 兼容"} · {item.modelName} · 密钥 {item.apiKeySuffix || "未配置"}</small>{<small>输出 {item.maxTokens} · 上下文 {(item.generationOptions?.contextWindowTokens ?? DEFAULT_AI_GENERATION.contextWindowTokens).toLocaleString()} · 推理 {item.reasoningMode === "disabled" ? "关闭" : item.generationOptions?.reasoningFormat && item.generationOptions.reasoningFormat !== "default" ? "自定义" : "供应商默认"} · 温度 {item.generationOptions?.temperatureMode === "default" ? "供应商默认" : (item.temperatureMilli / 1000).toFixed(2)} · 工具 {item.maxToolRounds} 轮/{item.maxTotalToolCalls} 次</small>}<small>{item.isDefaultTextModel ? "默认文本模型 · " : ""}{item.lastTestedAt ? `最近测试：${formatDateTime(item.lastTestedAt)} · ${item.lastTestResult || "完成"}` : "尚未测试"}</small></div><span className={`status ${item.status === "enabled" ? "status-success" : "status-warning"}`}>{item.status === "enabled" ? "启用" : "停用"}</span><div className="ai-card-actions"><button type="button" className="row-action" onClick={() => editModel(item)}>编辑</button><button type="button" className="row-action" disabled={busyConfigId === `model:${item.id}`} onClick={() => void testConfiguration("model", item.id)}>{busyConfigId === `model:${item.id}` ? "测试中…" : item.modelType === "vision" ? "测试图片识别" : "测试连接"}</button><button type="button" className="row-action danger" disabled={busyConfigId === `model:${item.id}`} onClick={() => void deleteConfiguration("model", item.id, item.name, item.version)}>删除</button></div></div>)}</div>
      </article>
      <article className="panel ai-admin-card data-refresh-region" aria-busy={configurationState === "loading"}>
        <div className="section-header"><div><h3>{isEditingChannel ? "编辑聊天渠道" : "新增聊天渠道"}</h3><p>钉钉和企业微信群机器人可主动发送测试消息；企业微信应用回调会验签、解密并只记录去重凭据，不会自动执行消息内容。</p></div>{isEditingChannel && <button type="button" className="text-button" onClick={() => setChannelDraft(newAiChannelDraft())}>取消编辑</button>}</div>
        <form className="ai-config-form" onSubmit={(event) => void saveChannel(event)}>
          <label><span>渠道名称</span><input value={channelDraft.name} required maxLength={100} onChange={(event) => setChannelDraft((current) => ({ ...current, name: event.target.value }))} placeholder="例如：运营群通知" /></label>
          <label><span>渠道类型</span><SearchableSelect value={channelDraft.kind} onChange={(value) => { const kind = value as AiChannelKind; setChannelDraft((current) => ({ ...current, kind, sendEnabled: kind === "dingtalk_group_bot" || kind === "wechat_work_group_bot" ? current.sendEnabled : false, callbackEnabled: kind === "wechat_work_app" ? current.callbackEnabled : false })); }} ariaLabel="渠道类型" searchPlaceholder="搜索渠道类型" options={[{ value: "dingtalk_group_bot", label: "钉钉群机器人" }, { value: "wechat_work_group_bot", label: "企业微信群机器人" }, { value: "wechat_work_app", label: "企业微信应用回调" }]} /></label>
          <label><span>状态</span><SearchableSelect value={channelDraft.status} onChange={(value) => setChannelDraft((current) => ({ ...current, status: value as "enabled" | "disabled" }))} ariaLabel="渠道状态" searchPlaceholder="搜索渠道状态" options={[{ value: "enabled", label: "启用" }, { value: "disabled", label: "停用" }]} /></label>
          <label className="ai-check-field"><input type="checkbox" checked={channelDraft.sendEnabled} disabled={!channelSupportsOutbound} onChange={(event) => setChannelDraft((current) => ({ ...current, sendEnabled: event.target.checked }))} /><span>{channelSupportsOutbound ? "允许主动发送" : "应用回调不支持主动发送"}</span></label>
          <label className="ai-check-field"><input type="checkbox" checked={channelDraft.callbackEnabled} disabled={channelDraft.kind !== "wechat_work_app"} onChange={(event) => setChannelDraft((current) => ({ ...current, callbackEnabled: event.target.checked }))} /><span>{channelDraft.kind === "wechat_work_app" ? "启用企业微信签名回调" : "企业微信应用可启用回调"}</span></label>
          <label className="ai-form-wide"><span>Webhook 地址{channelNeedsWebhook ? "（必填）" : "（可选）"}</span><input value={channelDraft.webhookUrl} required={channelNeedsWebhook && !isEditingChannel} type="url" onChange={(event) => setChannelDraft((current) => ({ ...current, webhookUrl: event.target.value }))} placeholder={isEditingChannel ? "留空保留现有 Webhook" : "https://..."} /><small>{isEditingChannel ? "当前地址已掩码保存；留空不会覆盖。" : "仅接受 HTTPS，群机器人或启用主动发送时必须配置。"}</small></label>
          <label><span>签名密钥 / 回调 Token</span><input value={channelDraft.callbackToken} type="password" autoComplete="new-password" onChange={(event) => setChannelDraft((current) => ({ ...current, callbackToken: event.target.value }))} placeholder={isEditingChannel ? "留空保留现有密钥" : "按平台填写"} /></label>
          <label><span>企业微信 EncodingAESKey</span><input value={channelDraft.aesKey} type="password" autoComplete="new-password" onChange={(event) => setChannelDraft((current) => ({ ...current, aesKey: event.target.value }))} placeholder={isEditingChannel ? "留空保留现有密钥" : "企业微信回调时填写"} /></label>
          <label><span>接收方 ID</span><input value={channelDraft.receiverId} maxLength={160} onChange={(event) => setChannelDraft((current) => ({ ...current, receiverId: event.target.value }))} placeholder="企业微信 CorpID / SuiteID" /></label>
          {channelDraft.callbackEnabled && channelDraft.id && <div className="ai-callback-path"><strong>回调地址</strong><code>/api/ai/webhooks/{channelDraft.id}</code><small>将完整站点域名与该路径填入聊天平台；回调需使用同一渠道的 Token/AESKey。</small></div>}
          <div className="ai-form-actions"><button type="submit" className="primary-button" disabled={savingChannel}>{savingChannel ? "保存中…" : isEditingChannel ? "保存修改" : "新增渠道"}</button></div>
        </form>
        <div className="ai-config-list">{channelItems.length === 0 && <p className="soft-text">暂无聊天渠道配置。</p>}{channelItems.map((item) => <div key={item.id} className="ai-config-card"><div><strong>{item.name}</strong><small>{channelKindLabel(item.kind)} · {item.webhookUrlMasked} · {item.sendEnabled ? "允许发送" : "仅回调"}</small><small>{item.callbackEnabled ? `回调：/api/ai/webhooks/${item.id}` : "未启用回调"}{item.lastTestedAt ? ` · 最近测试：${formatDateTime(item.lastTestedAt)} · ${item.lastTestResult || "完成"}` : ""}</small></div><span className={`status ${item.status === "enabled" ? "status-success" : "status-warning"}`}>{item.status === "enabled" ? "启用" : "停用"}</span><div className="ai-card-actions"><button type="button" className="row-action" onClick={() => editChannel(item)}>编辑</button><button type="button" className="row-action" disabled={!item.sendEnabled || busyConfigId === `channel:${item.id}`} onClick={() => void testConfiguration("channel", item.id)}>{busyConfigId === `channel:${item.id}` ? "测试中…" : "测试发送"}</button><button type="button" className="row-action danger" disabled={busyConfigId === `channel:${item.id}`} onClick={() => void deleteConfiguration("channel", item.id, item.name)}>删除</button></div></div>)}</div>
      </article>
      </>}
    </> : <article className="panel ai-permission-card"><h2>AI 管理</h2><p>仅无数据范围限制的管理员可查看和维护模型密钥、Webhook 及聊天平台回调。</p></article>)}
  </section>;
}


export default AiAssistantView;
