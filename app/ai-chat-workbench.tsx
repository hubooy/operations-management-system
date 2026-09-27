"use client";

import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import type { AiPageContext } from "@/lib/ai/page-context";
import { aiPageFilterSummary } from "@/lib/ai/page-context";
import { SearchableSelect } from "./ui/searchable-select";

const MarkdownContent = lazy(() => import("./ai-markdown"));

import type { AiExecutionInfo } from "@/lib/ai/model-generation";

type Message = { execution?: AiExecutionInfo; id: string; conversationId: string; role: "user" | "assistant"; content: string; messageKind: string; createdAt: string; contentTruncated: boolean };
type Conversation = { id: string; title: string; updatedAt: string };
type ChatModel = { id: string; name: string; modelType: string; isDefault: boolean };
export type LiveAiAnswer = { prompt: string; content: string; stage: string; tools: string[]; incomplete?: boolean };
export type AiWorkbenchProps = {
  compact: boolean; currentTitle: string; activeConversationId: string;
  conversations: Conversation[]; conversationTotal: number; hasMoreConversations: boolean; loadingMoreConversations: boolean;
  messages: Message[]; hasOlderMessages: boolean; loadingOlderMessages: boolean;
  models: ChatModel[]; selectedModelId: string; canChat: boolean; busy: boolean; sending: boolean; loading: boolean; recoveryBlocked: boolean;
  draft: string; error: string; notice: string; context: AiPageContext | null; contextError: string; liveAnswer: LiveAiAnswer | null;
  onDraft: (value: string) => void; onSend: () => void; onStop: () => void; onNew: () => void;
  onOpen: (id: string) => void; onDelete: (id: string) => void; onModel: (id: string) => void;
  onExpand: (id: string) => void; onMore: () => void; onOlder: () => void; onRefresh: () => void; onRemoveContext: () => void;
  renderArtifacts: (messageId: string) => ReactNode;
};

function Symbol({ name }: { name: "spark" | "plus" | "chat" | "arrow" | "stop" | "panel" | "copy" | "refresh" | "search" | "close" | "check" }) {
  const paths = { spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z", plus: "M12 5v14M5 12h14", chat: "M21 11a8 8 0 0 1-8 8H3l2-4A8 8 0 1 1 21 11Z", arrow: "M12 19V5m-6 6 6-6 6 6", stop: "M6 6h12v12H6Z", panel: "M3 4h18v16H3ZM9 4v16", copy: "M9 9h11v12H9ZM15 5V2H2v14h3", refresh: "M20 7A9 9 0 1 0 21 14M20 3v5h-5", search: "m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0", close: "m6 6 12 12M6 18 18 6", check: "m5 12 4 4L19 6" };
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

export function AiMarkdown({ content, partial = false }: { content: string; partial?: boolean }) {
  return <Suspense fallback={<span className="ai-workbench-stage" role="status">正在排版…</span>}><MarkdownContent content={content} partial={partial} /></Suspense>;
}

function ExecutionDetails({ value }: { value?: AiExecutionInfo }) {
  if (!value?.durationMs && !value?.providerCalls) return null;
  const number = (n: number | null | undefined) => n == null ? "未报告" : n.toLocaleString();
  const stops: Record<string, string> = { stop: "正常完成", end_turn: "正常完成", completed: "正常完成", length: "达到输出上限", max_tokens: "达到输出上限", output_limit: "达到输出上限", shortcut: "快捷回复", stop_sequence: "停止序列", tool_calls: "工具调用", tool_use: "工具调用" };
  return <details className="ai-workbench-execution"><summary>{((value.durationMs ?? 0) / 1000).toFixed(1)} 秒 · {value.outputTruncated ? "达到输出上限" : stops[value.stopReason ?? ""] ?? "已完成"} · 执行详情</summary><dl>
    <div><dt>已报告输入 / 输出 Token</dt><dd>{number(value.inputTokens)} / {number(value.outputTokens)}</dd></div>
    <div><dt>已报告思考 Token</dt><dd>{number(value.reasoningTokens)}</dd></div>
    <div><dt>模型 / 工具调用</dt><dd>{value.providerCalls ?? 0} / {value.toolCalls ?? 0}</dd></div>
    <div><dt>本轮估算输入 / 上下文预算</dt><dd>{number(value.context?.estimatedInputTokens)} / {number(value.context?.contextWindowTokens)}</dd></div>
    {!!value.context?.droppedMessages && <div><dt>因预算移出上下文的旧消息</dt><dd>{value.context.droppedMessages} 条（历史记录保留）</dd></div>}
    {value.guidance && <div><dt>配置版本</dt><dd>{value.guidance.version === 0 ? "系统默认" : `v${value.guidance.version}`}</dd></div>}
    {value.guidance && <div><dt>本次业务口径</dt><dd>{value.guidance.rules.length ? value.guidance.rules.map(rule => rule.name).join("、") : "通用口径"}</dd></div>}
    {value.skills && <div><dt>本次技能 · v{value.skills.version}</dt><dd>{value.skills.skills.length ? value.skills.skills.map(skill => skill.name).join("、") : "未匹配技能"}</dd></div>}
  </dl><small>上下文为估算值，实际用量以供应商返回为准。{(value.usageReportedCalls ?? 0) < (value.providerCalls ?? 0) ? "部分调用未报告完整用量，以上不代表总计。" : ""}</small></details>;
}

function date(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }

export default function AiChatWorkbench(props: AiWorkbenchProps) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState("");
  const scroll = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const media = window.matchMedia("(max-width:760px)");
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => { pinned.current = true; }, [props.activeConversationId]);
  useEffect(() => {
    if (pinned.current && scroll.current && !props.loadingOlderMessages) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [props.messages, props.liveAnswer, props.activeConversationId, props.loadingOlderMessages]);
  useEffect(() => { if (!copied) return; const timer = setTimeout(() => setCopied(""), 2200); return () => clearTimeout(timer); }, [copied]);
  useEffect(() => {
    const element = input.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 120)}px`;
  }, [props.draft]);
  const visible = props.messages.filter(m => m.conversationId === props.activeConversationId);
  const lastMessage = visible.at(-1);
  const latestGuidance = visible.filter(m => m.role === "assistant").at(-1)?.execution?.guidance;
  const showPendingPrompt = lastMessage?.role !== "user" || lastMessage.content !== props.liveAnswer?.prompt;
  const canSend = props.canChat && !props.busy && !props.recoveryBlocked && !(props.contextError && props.context) && !!props.draft.trim();
  async function copy(message: Message) {
    try { await navigator.clipboard.writeText(message.content); setCopied(message.id); } catch { setCopied("failed"); }
  }
  function newChat() { props.onNew(); setHistoryOpen(false); input.current?.focus(); }
  function suggest(value: string) { props.onDraft(value); input.current?.focus(); }
  return <article className={`ai-workbench ${props.compact ? "ai-workbench-compact" : ""}`} aria-label="AI 对话工作台">
    <header className="ai-workbench-top"><div><button type="button" className="ai-workbench-icon ai-workbench-history-toggle" aria-label="显示或收起对话记录" aria-expanded={props.compact || narrow ? historyOpen : !historyCollapsed} onClick={() => { if (props.compact || narrow) setHistoryOpen(!historyOpen); else setHistoryCollapsed(!historyCollapsed); }}><Symbol name="panel" /></button><span className="ai-workbench-brand"><Symbol name="spark" /></span><strong>小特</strong><span className="ai-workbench-subtitle">{props.currentTitle || "新对话"}</span></div><div>{!props.compact && <div className="ai-workbench-top-model"><SearchableSelect value={props.selectedModelId} onChange={props.onModel} ariaLabel="本对话模型" searchPlaceholder="搜索对话模型" disabled={props.busy || props.recoveryBlocked || !props.models.length} options={props.models.map(model => ({ value: model.id, label: `${model.name}${model.isDefault ? " · 默认" : ""}` }))} /></div>}<button type="button" className="ai-workbench-text" disabled={props.loading || props.sending} onClick={props.onRefresh}><Symbol name="refresh" />{props.loading ? "刷新中" : "刷新"}</button><button type="button" className="ai-workbench-text" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(!detailsOpen)}><Symbol name="panel" />对话详情</button></div></header>
    <div className="ai-workbench-layout">
      <aside className={`ai-workbench-history ${historyOpen ? "is-open" : ""} ${historyCollapsed ? "is-collapsed" : ""}`} aria-label="个人会话">
        <button type="button" className="ai-workbench-new" onClick={newChat} disabled={props.busy}><Symbol name="plus" />新建对话</button>
        <div className="ai-workbench-history-title"><span>最近对话</span><small>{props.conversations.length} / {props.conversationTotal}</small></div>
        <label className="ai-workbench-search"><Symbol name="search" /><input aria-label="搜索已加载对话" placeholder="搜索已加载对话" value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div className="ai-workbench-history-list">{props.conversations.filter(c => c.title.toLowerCase().includes(query.toLowerCase())).map(c => <div key={c.id} className={`ai-workbench-history-row ${c.id === props.activeConversationId ? "is-selected" : ""}`}><button type="button" disabled={props.busy} onClick={() => { props.onOpen(c.id); setHistoryOpen(false); }}><Symbol name="chat" /><span><strong>{c.title}</strong><small>{date(c.updatedAt)}</small></span></button>{props.canChat && <button type="button" className="ai-workbench-delete" aria-label={`删除对话 ${c.title}`} disabled={props.busy} onClick={() => props.onDelete(c.id)}><Symbol name="close" /></button>}</div>)}
          {!props.conversations.length && <p>发送第一条消息后，会自动保存到这里。</p>}{query && !props.conversations.some(c => c.title.toLowerCase().includes(query.toLowerCase())) && <p>已加载记录中没有匹配结果。</p>}
        </div>{props.hasMoreConversations && <button type="button" className="ai-workbench-text" disabled={props.loadingMoreConversations || props.busy} onClick={props.onMore}>{props.loadingMoreConversations ? "加载中…" : "加载更多对话"}</button>}
        <div className="ai-workbench-history-foot">个人会话 · 按账号权限查询</div>
      </aside>
      <section className="ai-workbench-conversation">
        {props.compact && <div className="ai-workbench-conversation-title"><h3>{props.currentTitle || "新对话"}</h3></div>}
        {(props.error || props.notice) && <div className={`ai-workbench-feedback ${props.error ? "is-error" : ""}`} role={props.error ? "alert" : "status"}>{props.error || props.notice}</div>}
        <div className="ai-workbench-scroll" ref={scroll} onScroll={() => { const element = scroll.current; if (element) pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }}>
          <div className="ai-workbench-messages">
            {props.hasOlderMessages && <button type="button" className="ai-workbench-older" disabled={props.loadingOlderMessages || props.sending} onClick={() => { pinned.current = false; props.onOlder(); }}>{props.loadingOlderMessages ? "加载中…" : "加载更早消息"}</button>}
            {!visible.length && !props.liveAnswer && <div className="ai-workbench-empty"><span className="ai-workbench-brand"><Symbol name="spark" /></span><h3>有什么需要一起解决？</h3><p>结合系统数据复盘经营，或把一个问题理清楚。</p><div>{["当前有哪些数据可以查询？", "帮我分析当前页面，先确认数据范围", "帮助"].map((suggestion, i) => <button key={suggestion} type="button" disabled={!props.canChat || props.busy} onClick={() => suggest(suggestion)}><Symbol name={i === 0 ? "chat" : i === 1 ? "spark" : "search"} /><strong>{["发现数据", "分析当前页面", "查看可用工具"][i]}</strong><span>{suggestion}</span></button>)}</div></div>}
            {visible.map(message => <div key={message.id} className={`ai-workbench-message ai-workbench-${message.role} ${message.messageKind === "context_reset" ? "ai-workbench-reset" : ""}`}>
              {message.role === "assistant" && <div className="ai-workbench-message-heading"><span className="ai-workbench-brand"><Symbol name="spark" /></span><strong>{message.messageKind === "context_reset" ? "上下文断点" : "小特"}</strong></div>}
              <div className="ai-workbench-message-content">{message.role === "assistant" ? <AiMarkdown content={message.content} /> : <p>{message.content}</p>}
                {message.contentTruncated && <small role="status">历史消息较长。<button type="button" className="ai-workbench-text" onClick={() => props.onExpand(message.id)}>展开完整回复</button></small>}{props.renderArtifacts(message.id)}
                <ExecutionDetails value={message.execution} /><div className="ai-workbench-message-actions">{message.role === "assistant" && <button type="button" className="ai-workbench-text" onClick={() => void copy(message)}><Symbol name={copied === message.id ? "check" : "copy"} />{copied === message.id ? "已复制" : "复制"}</button>}<time dateTime={message.createdAt}>{date(message.createdAt)}</time></div>
              </div>{message.role === "user" && <span className="ai-workbench-user-avatar">我</span>}
            </div>)}
            {props.liveAnswer && <>{showPendingPrompt && <div className="ai-workbench-message ai-workbench-user"><div className="ai-workbench-message-content"><p>{props.liveAnswer.prompt}</p></div><span className="ai-workbench-user-avatar">我</span></div>}<div className="ai-workbench-message ai-workbench-assistant"><div className="ai-workbench-message-heading"><span className="ai-workbench-brand"><Symbol name="spark" /></span><strong>小特</strong><span className="ai-workbench-stage" role="status">{props.sending && <i className="ai-workbench-spinner" />}{props.liveAnswer.stage}</span></div><div className="ai-workbench-message-content">{props.liveAnswer.tools.length > 0 && <details className="ai-workbench-tools"><summary>本次已查询 {props.liveAnswer.tools.length} 项</summary><ul>{props.liveAnswer.tools.map((tool, index) => <li key={`${tool}-${index}`}>{tool}</li>)}</ul></details>}<AiMarkdown content={props.liveAnswer.content} partial />{props.sending && <span className="ai-workbench-cursor" />}{props.liveAnswer.incomplete && <p className="ai-workbench-incomplete">部分内容尚未确认完整保存，请刷新核对服务端记录。</p>}</div></div></>}
          </div>
        </div>
        <div className="ai-workbench-composer-area">{props.context && <div className="ai-workbench-context"><span>{props.context.moduleLabel}{props.context.period ? ` · ${props.context.period.startDate} 至 ${props.context.period.endDate}` : " · 日期以查询结果为准"}</span><button type="button" aria-label="移除当前页面上下文" onClick={props.onRemoveContext} disabled={props.sending}><Symbol name="close" /></button></div>}
          <div className="ai-workbench-composer"><textarea ref={input} aria-label="输入 AI 问题" value={props.draft} maxLength={12000} rows={1} placeholder={props.canChat ? "向小特提问，或继续追问…" : "登录并获得操作权限后可发送消息"} disabled={!props.canChat || props.sending} onChange={e => props.onDraft(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && !e.repeat) { e.preventDefault(); if (canSend) props.onSend(); } }} />
            <div className="ai-workbench-composer-toolbar">{props.compact && <div className="ai-workbench-model"><Symbol name="spark" /><SearchableSelect value={props.selectedModelId} onChange={props.onModel} ariaLabel="本对话模型" searchPlaceholder="搜索对话模型" disabled={props.busy || props.recoveryBlocked || !props.models.length} options={props.models.map(model => ({ value: model.id, label: `${model.name}${model.modelType === "vision" ? " · 视觉" : ""}${model.isDefault ? " · 默认" : ""}` }))} /></div>}<div><span className="ai-workbench-enter">Enter 发送</span><button type="button" className="ai-workbench-send" aria-label={props.sending ? "停止生成" : "发送消息"} disabled={!props.sending && !canSend} onClick={props.sending ? props.onStop : props.onSend}><Symbol name={props.sending ? "stop" : "arrow"} /></button></div></div>
          </div><div className="ai-workbench-composer-note"><span>回答请结合数据来源与统计口径复核</span><span>Shift + Enter 换行</span></div>
        </div>
      </section>
      {detailsOpen && <aside className="ai-workbench-details"><header><h3>对话详情</h3><button type="button" className="ai-workbench-icon" aria-label="关闭对话详情" onClick={() => setDetailsOpen(false)}><Symbol name="close" /></button></header><h4>当前模型</h4><p>{props.models.find(model => model.id === props.selectedModelId)?.name || "尚未选择"}</p><small>切换模型后从下一条消息起生效。</small><h4>页面上下文</h4>{props.context ? <><p>{props.context.moduleLabel}</p><ul>{aiPageFilterSummary(props.context.filters).map(item => <li key={item}>{item}</li>)}</ul><small>页面筛选不是查询结果，回复以实际读取的数据为准。</small></> : <p>未附加页面条件。</p>}<h4>最近回复采用的配置</h4>{latestGuidance ? <p>{latestGuidance.version === 0 ? "系统默认" : `版本 v${latestGuidance.version}`} · {latestGuidance.rules.map(r => r.name).join("、") || "通用口径"}</p> : <p>暂无配置版本记录。新回复将在执行详情中记录。</p>}<h4>会话与权限</h4><p>仅显示你有权访问的个人会话。查询按当前账号的数据范围执行。</p><small>断线后核对原请求回执，不自动重发模型请求。</small></aside>}
    </div>{copied === "failed" && <p role="status" className="ai-workbench-feedback">复制不可用，请选择正文手动复制。</p>}
  </article>;
}
