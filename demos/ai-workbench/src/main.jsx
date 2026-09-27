import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { scenarios } from "./fixtures.mjs";
import { consumeStream } from "./stream.mjs";
import { visibleMarkdown } from "./stream-markdown.mjs";
import "./style.css";

const paths = {
  plus: "M12 5v14M5 12h14",
  search: "m21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  chat: "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H3l1.8-4A8.5 8.5 0 1 1 21 11.5Z",
  settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
  arrow: "M12 19V5m-6 6 6-6 6 6",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  close: "m6 6 12 12M6 18 18 6",
  panel: "M3 4h18v16H3ZM9 4v16",
  check: "m5 12 4 4L19 6",
  stop: "M6 6h12v12H6Z",
  copy: "M9 9h11v12H9ZM15 5V2H2v14h3",
  file: "M14 2H5v20h14V7ZM14 2v6h5M8 12h8M8 16h6",
  chart: "M4 3v17h17M8 15V9m5 6V5m5 10v-7",
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z",
  clock: "M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  database:
    "M20 5c0 2-16 2-16 0s16-2 16 0v14c0 3-16 3-16 0V5M4 12c0 3 16 3 16 0",
  external: "M13 3h8v8m0-8L10 14M9 3H3v18h18v-6",
};
function Icon({ name, size = 18, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={paths[name] || paths.spark} />
    </svg>
  );
}
function Mark({ small = false }) {
  return (
    <span className={`brand-mark ${small ? "small" : ""}`}>
      <Icon name="spark" size={small ? 18 : 24} />
    </span>
  );
}
function RichText({ content, streaming }) {
  return (
    <div className="prose">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          table: ({ children }) => (
            <div className="table-scroll">
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: ({ alt }) => (
            <span className="muted">[图片：{alt || "未加载"}]</span>
          ),
        }}
      >
        {visibleMarkdown(content, streaming)}
      </Markdown>
    </div>
  );
}
function Metrics({ items }) {
  if (!items?.length) return null;
  return (
    <div className="metrics">
      {items.map(([name, value, change], i) => (
        <div className="metric" key={name}>
          <span>{name}</span>
          <div>
            <strong>{value}</strong>
            <span className="metric-change">{change}</span>
          </div>
          <svg
            viewBox="0 0 180 26"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path
              d={
                i === 0
                  ? "M0 24L16 21L32 23L48 12L64 17L80 12L96 16L112 5L128 10L144 8L162 2L180 3"
                  : i === 1
                    ? "M0 24L18 20L36 22L54 16L72 19L90 13L108 15L126 9L144 12L162 4L180 2"
                    : "M0 23L20 22L40 18L60 20L80 14L100 16L120 10L140 11L160 7L180 3"
              }
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
            />
          </svg>
        </div>
      ))}
    </div>
  );
}
const defaultConfig = {
  name: "经营分析",
  modelId: "example-analysis-model",
  protocol: "openai",
  modality: "text",
  baseUrl: "",
  maxOutput: 65536,
  contextWindow: 128000,
  reasoning: "auto",
  temperature: "",
  toolRounds: 12,
  toolCalls: 24,
  firstTimeout: 120,
  idleTimeout: 60,
  totalTimeout: 600,
  inputPrice: "",
  outputPrice: "",
  prompt:
    "先给出结论，再说明依据。经营分析标注数据来源、时间范围和口径；信息不足时明确说明。",
};
function seed(key) {
  const s = scenarios[key];
  return {
    id: key,
    title: s.title,
    messages: [
      { id: `${key}-user`, role: "user", content: s.prompt },
      {
        id: `${key}-assistant`,
        role: "assistant",
        content: s.body,
        status: "complete",
        sources: s.sources,
        metrics: s.metrics,
        scenario: key,
      },
    ],
  };
}
function Settings({ config, onSave, onBack, notify }) {
  const [draft, setDraft] = useState({ ...config });
  const [tab, setTab] = useState("capacity");
  const [error, setError] = useState("");
  const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));
  const num = (key, label, suffix, min = 1, max = 10000000) => (
    <label className="field">
      <span>{label}</span>
      <div className="unit-input">
        <input
          type="number"
          min={min}
          max={max}
          required
          value={draft[key]}
          onChange={(e) =>
            set(key, e.target.value === "" ? "" : Number(e.target.value))
          }
        />
        <span>{suffix}</span>
      </div>
    </label>
  );
  function save(e) {
    e.preventDefault();
    if (!draft.name.trim() || !draft.modelId.trim()) {
      setError("请填写配置名称和模型 ID。");
      setTab("connection");
      return;
    }
    for (const key of [
      "maxOutput",
      "contextWindow",
      "toolRounds",
      "toolCalls",
      "firstTimeout",
      "idleTimeout",
      "totalTimeout",
    ]) {
      if (
        !Number.isInteger(draft[key]) ||
        draft[key] < 1 ||
        draft[key] > 10000000
      ) {
        setError("容量、工具次数和超时须填写有效正整数。");
        return;
      }
    }
    if (draft.maxOutput >= draft.contextWindow) {
      setError("输出预算必须小于上下文窗口，给输入和工具结果留出空间。");
      setTab("capacity");
      return;
    }
    if (
      draft.toolRounds > 1000 ||
      draft.toolCalls > 1000 ||
      draft.firstTimeout > 3600 ||
      draft.idleTimeout > 3600 ||
      draft.totalTimeout > 7200
    ) {
      setError(
        "工具次数不超过 1,000；首段与空闲时限不超过 3,600 秒，总时限不超过 7,200 秒。这些仅为演示范围。",
      );
      setTab("execution");
      return;
    }
    if (draft.totalTimeout < Math.max(draft.firstTimeout, draft.idleTimeout)) {
      setError("总时限不能小于首段等待或流空闲时限。");
      setTab("execution");
      return;
    }
    if (
      draft.temperature !== "" &&
      (!Number.isFinite(Number(draft.temperature)) ||
        Number(draft.temperature) < 0 ||
        Number(draft.temperature) > 2)
    ) {
      setError(
        "演示温度范围为 0–2，留空表示供应商默认；实际范围需按接口验证。",
      );
      return;
    }
    if (
      [draft.inputPrice, draft.outputPrice].some(
        (v) => v !== "" && (!Number.isFinite(Number(v)) || Number(v) < 0),
      )
    ) {
      setError("价格须为非负数，或留空不估算。");
      return;
    }
    setError("");
    onSave(draft);
    notify("演示配置已更新，仅在当前页面生效。");
  }
  return (
    <div className="settings-scroll">
      <div className="settings-page">
        <button className="text-button back" onClick={onBack}>
          ← 返回对话
        </button>
        <div className="page-title">
          <div>
            <div className="eyebrow">MODEL STUDIO</div>
            <h1>让模型，发挥应有的能力</h1>
            <p>按真实模型能力配置容量，再为每次任务分配合适的预算。</p>
          </div>
          <span className="badge">配置演示</span>
        </div>
        <form onSubmit={save} className="settings-grid">
          <div className="settings-main">
            <div className="model-heading">
              <div className="model-avatar">
                <Icon name="spark" size={26} />
              </div>
              <div>
                <h2>{draft.name || "未命名模型"}</h2>
                <span>
                  文本分析 ·{" "}
                  {draft.protocol === "openai"
                    ? "OpenAI 兼容协议"
                    : "Anthropic Messages"}
                </span>
              </div>
              <span className="outline-badge">待验证能力</span>
            </div>
            <div
              className="settings-tabs"
              role="tablist"
              aria-label="模型配置分类"
            >
              {[
                ["capacity", "容量与生成"],
                ["connection", "模型与连接"],
                ["execution", "工具与执行"],
                ["style", "风格与费用"],
              ].map(([id, title]) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === id}
                  key={id}
                  onClick={() => {
                    setTab(id);
                    setError("");
                  }}
                >
                  {title}
                </button>
              ))}
            </div>
            <div className="settings-body">
              {tab === "capacity" && (
                <>
                  <div className="section-intro">
                    <h3>容量不再一刀切</h3>
                    <p>以下数值是演示预算，不代表所选接口已支持该容量。</p>
                  </div>
                  <div className="preset-row">
                    <button
                      type="button"
                      onClick={() =>
                        setDraft((x) => ({
                          ...x,
                          maxOutput: 8192,
                          reasoning: "auto",
                        }))
                      }
                    >
                      日常问答 <span>8,192 输出</span>
                    </button>
                    <button
                      className={draft.maxOutput === 65536 ? "selected" : ""}
                      type="button"
                      onClick={() =>
                        setDraft((x) => ({
                          ...x,
                          maxOutput: 65536,
                          contextWindow: Math.max(
                            128000,
                            Number(x.contextWindow),
                          ),
                          reasoning: "auto",
                        }))
                      }
                    >
                      深度分析 <span>65,536 输出</span>
                    </button>
                  </div>
                  <div className="field-grid">
                    {num("maxOutput", "最大输出预算", "tokens")}
                    {num("contextWindow", "模型上下文窗口", "tokens")}
                    <label className="field">
                      <span>推理模式</span>
                      <select
                        value={draft.reasoning}
                        onChange={(e) => set("reasoning", e.target.value)}
                      >
                        <option value="auto">跟随供应商默认</option>
                        <option value="off">关闭 · 需接口支持</option>
                        <option value="high">高强度 · 需接口支持</option>
                      </select>
                      <small>
                        能力以供应商和具体端点为准，不按模型名称猜测。
                      </small>
                    </label>
                    <label className="field">
                      <span>
                        温度 <em>可选</em>
                      </span>
                      <input
                        type="number"
                        min="0"
                        max="2"
                        step="0.05"
                        placeholder="留空，跟随供应商默认"
                        value={draft.temperature}
                        onChange={(e) => set("temperature", e.target.value)}
                      />
                      <small>直接填写 0.2；不支持该参数时应省略。</small>
                    </label>
                  </div>
                  <div className="note">
                    <Icon name="file" />
                    <p>
                      <strong>输出上限 ≠ 模型能力</strong>
                      <br />
                      上下文需要同时容纳问题、历史、工具结果及模型输出。正式接入还需验证推理预算与输出预算的计算方式。
                    </p>
                  </div>
                </>
              )}
              {tab === "connection" && (
                <>
                  <div className="section-intro">
                    <h3>模型身份与连接</h3>
                    <p>配置应绑定供应商、完整地址、协议与模型 ID。</p>
                  </div>
                  <div className="field-grid">
                    <label className="field">
                      <span>配置名称</span>
                      <input
                        maxLength="60"
                        required
                        value={draft.name}
                        onChange={(e) => set("name", e.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>模型 ID</span>
                      <input
                        maxLength="160"
                        required
                        value={draft.modelId}
                        onChange={(e) => set("modelId", e.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>协议</span>
                      <select
                        value={draft.protocol}
                        onChange={(e) => set("protocol", e.target.value)}
                      >
                        <option value="openai">OpenAI 兼容</option>
                        <option value="anthropic">Anthropic Messages</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>模态</span>
                      <select
                        value={draft.modality}
                        onChange={(e) => set("modality", e.target.value)}
                      >
                        <option value="text">纯文本</option>
                        <option value="vision">文本 + 视觉 · 待验证</option>
                      </select>
                    </label>
                    <label className="field full">
                      <span>API 地址</span>
                      <input
                        type="url"
                        placeholder="https://api.example.com/v1"
                        value={draft.baseUrl}
                        onChange={(e) => set("baseUrl", e.target.value)}
                      />
                      <small>此 demo 不向该地址发送请求。</small>
                    </label>
                    <label className="field full">
                      <span>API Key</span>
                      <input
                        disabled
                        type="password"
                        placeholder="演示环境无需填写真实密钥"
                      />
                      <small>
                        正式接入沿用现有服务端加密保存，仅显示末四位。
                      </small>
                    </label>
                  </div>
                </>
              )}
              {tab === "execution" && (
                <>
                  <div className="section-intro">
                    <h3>给复杂任务更合理的空间</h3>
                    <p>独立区分工具预算、首段等待、流空闲与整个任务时限。</p>
                  </div>
                  <div className="field-grid">
                    {num("toolRounds", "最大工具轮数", "轮", 1, 1000)}
                    {num("toolCalls", "工具调用总数", "次", 1, 1000)}
                    {num("firstTimeout", "首段等待", "秒", 1, 3600)}
                    {num("idleTimeout", "流空闲时限", "秒", 1, 3600)}
                    {num("totalTimeout", "整个任务时限", "秒", 1, 7200)}
                  </div>
                  <div className="note">
                    <Icon name="clock" />
                    <p>
                      这些是后续接入的配置设计。当前 demo
                      只演示传输体验；正式执行仍需 Django
                      持久化任务、权限、审计及恢复机制。
                    </p>
                  </div>
                </>
              )}
              {tab === "style" && (
                <>
                  <div className="section-intro">
                    <h3>回复风格与费用估算</h3>
                    <p>风格指令只影响表达；单价用于估算，不会提升模型能力。</p>
                  </div>
                  <label className="field">
                    <span>附加系统提示词</span>
                    <textarea
                      rows="6"
                      maxLength="6000"
                      value={draft.prompt}
                      onChange={(e) => set("prompt", e.target.value)}
                    />
                  </label>
                  <div className="field-grid">
                    <label className="field">
                      <span>输入单价</span>
                      <div className="unit-input">
                        <input
                          type="number"
                          min="0"
                          step="0.0001"
                          placeholder="不估算"
                          value={draft.inputPrice}
                          onChange={(e) => set("inputPrice", e.target.value)}
                        />
                        <span>元 / 百万 tokens</span>
                      </div>
                    </label>
                    <label className="field">
                      <span>输出单价</span>
                      <div className="unit-input">
                        <input
                          type="number"
                          min="0"
                          step="0.0001"
                          placeholder="不估算"
                          value={draft.outputPrice}
                          onChange={(e) => set("outputPrice", e.target.value)}
                        />
                        <span>元 / 百万 tokens</span>
                      </div>
                    </label>
                  </div>
                </>
              )}
            </div>
            <div className="settings-footer">
              {error && (
                <span role="alert" className="error-text">
                  {error}
                </span>
              )}
              <span className="muted">仅更新当前演示</span>
              <button className="primary" type="submit">
                保存演示配置 <Icon name="check" size={16} />
              </button>
            </div>
          </div>
          <aside className="budget-panel">
            <div className="eyebrow">CONTEXT BUDGET</div>
            <h3>上下文分配</h3>
            <div className="budget-number">
              {Number(draft.contextWindow || 0).toLocaleString()}
              <span>tokens</span>
            </div>
            <div className="budget-track">
              <span
                style={{
                  width: `${Math.min(100, (draft.maxOutput / (draft.contextWindow || 1)) * 100)}%`,
                }}
              />
            </div>
            <div className="budget-legend">
              <span>预留输出</span>
              <strong>{Number(draft.maxOutput || 0).toLocaleString()}</strong>
            </div>
            <div className="budget-legend">
              <span>输入与历史可用</span>
              <strong>
                {Math.max(
                  0,
                  draft.contextWindow - draft.maxOutput,
                ).toLocaleString()}
              </strong>
            </div>
            <p>
              这是容量示意，并非实际 token
              用量。正式接入需考虑推理、工具与协议开销。
            </p>
            <hr />
            <h4>接入前需要验证</h4>
            <ul>
              <li>模型真实容量与输出上限</li>
              <li>推理与采样参数兼容性</li>
              <li>工具调用与 SSE 事件</li>
              <li>超时、截断和中断恢复</li>
            </ul>
            <div className="budget-bottom">
              <Icon name="settings" />
              <span>与正式模型配置隔离</span>
            </div>
          </aside>
        </form>
      </div>
    </div>
  );
}

function App() {
  const [view, setView] = useState("chat");
  const [conversations, setConversations] = useState([
    seed("sales"),
    seed("inventory"),
    seed("format"),
  ]);
  const [active, setActive] = useState("sales");
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [sidebar, setSidebar] = useState(false);
  const [details, setDetails] = useState(false);
  const [config, setConfig] = useState(defaultConfig);
  const [model, setModel] = useState("analysis");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [mode, setMode] = useState("normal");
  const controller = useRef(null);
  const generation = useRef(0);
  const scroller = useRef(null);
  const pinned = useRef(true);
  const textarea = useRef(null);
  const current =
    conversations.find((c) => c.id === active) || conversations[0];
  const messages = current.messages;
  const latest = [...messages].reverse().find((m) => m.role === "assistant");
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (busy && pinned.current && scroller.current)
      scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [messages, busy]);
  useEffect(() => () => controller.current?.abort(), []);
  function updateMessage(conversationId, messageId, patch) {
    setConversations((list) =>
      list.map((c) =>
        c.id !== conversationId
          ? c
          : {
              ...c,
              messages: c.messages.map((m) =>
                m.id === messageId
                  ? {
                      ...m,
                      ...(typeof patch === "function" ? patch(m) : patch),
                    }
                  : m,
              ),
            },
      ),
    );
  }
  function stop() {
    controller.current?.abort();
  }
  function selectConversation(id) {
    stop();
    generation.current++;
    setBusy(false);
    setActive(id);
    setView("chat");
    setDraft("");
    setSidebar(false);
    if (scroller.current) scroller.current.scrollTop = 0;
  }
  function newChat() {
    stop();
    generation.current++;
    setBusy(false);
    const id = crypto.randomUUID();
    setConversations((list) => [
      { id, title: "新的对话", messages: [] },
      ...list,
    ]);
    setActive(id);
    setView("chat");
    setDraft("");
    setSidebar(false);
    setTimeout(() => textarea.current?.focus(), 0);
  }
  async function send(prompt = draft) {
    prompt = prompt.trim();
    if (!prompt || busy || prompt.length > 4000) return;
    const conversationId = active;
    const assistantId = crypto.randomUUID();
    const requestGeneration = ++generation.current;
    const abort = new AbortController();
    controller.current = abort;
    pinned.current = true;
    setConversations((list) =>
      list.map((c) =>
        c.id === conversationId
          ? {
              ...c,
              title: c.messages.length ? c.title : prompt.slice(0, 18),
              messages: [
                ...c.messages,
                { id: crypto.randomUUID(), role: "user", content: prompt },
                {
                  id: assistantId,
                  role: "assistant",
                  modelName: model === "quick" ? "快速问答" : config.name,
                  content: "",
                  status: "streaming",
                  stage: "正在连接",
                  sources: [],
                },
              ],
            }
          : c,
      ),
    );
    setDraft("");
    setBusy(true);
    try {
      const response = await fetch("/demo-api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, mode }),
        signal: abort.signal,
      });
      await consumeStream(
        response,
        (event, payload) => {
          if (generation.current !== requestGeneration) return;
          if (event === "delta")
            updateMessage(conversationId, assistantId, (m) => ({
              content: m.content + payload.content,
            }));
          if (event === "status")
            updateMessage(conversationId, assistantId, payload);
          if (event === "done")
            updateMessage(conversationId, assistantId, {
              ...payload,
              status: "complete",
            });
        },
        abort.signal,
      );
    } catch (error) {
      updateMessage(conversationId, assistantId, {
        status: abort.signal.aborted ? "stopped" : "error",
        stage: abort.signal.aborted
          ? "已停止生成，保留部分回答"
          : error.message,
      });
    } finally {
      if (generation.current === requestGeneration) {
        setBusy(false);
        controller.current = null;
      }
    }
  }
  async function copy(content) {
    try {
      await navigator.clipboard.writeText(content);
      setToast("已复制回答");
    } catch {
      setToast("复制不可用，请选择正文手动复制。");
    }
  }
  const suggestions = messages.length
    ? ["展开补货建议", "看看格式化回复"]
    : ["帮我复盘本周经营情况", "哪些商品需要优先补货？", "展示表格和代码排版"];
  return (
    <div className="app-shell">
      {sidebar && (
        <button
          className="sidebar-scrim"
          aria-label="关闭导航"
          onClick={() => setSidebar(false)}
        />
      )}
      <aside className={`sidebar ${sidebar ? "mobile-open" : ""}`}>
        <div className="brand">
          <Mark />
          <div>
            <strong>TERUISI</strong>
            <span>运营智能工作台</span>
          </div>
          <button
            className="icon-button mobile-only"
            aria-label="关闭导航"
            onClick={() => setSidebar(false)}
          >
            <Icon name="close" />
          </button>
        </div>
        <button className="new-chat" onClick={newChat}>
          <Icon name="plus" /> 新建对话 <span>＋</span>
        </button>
        <div className="nav-group">
          <button
            className={view === "chat" ? "nav-item active" : "nav-item"}
            onClick={() => {
              setView("chat");
              setSidebar(false);
            }}
          >
            <Icon name="chat" />
            AI 对话<span className="nav-pill">小特</span>
          </button>
          <button
            className={view === "settings" ? "nav-item active" : "nav-item"}
            onClick={() => {
              setView("settings");
              setSidebar(false);
            }}
          >
            <Icon name="settings" />
            AI 管理
          </button>
        </div>
        <div className="history-header">
          <span>最近对话</span>
          <span>{conversations.length}</span>
        </div>
        <label className="history-search">
          <Icon name="search" size={15} />
          <input
            aria-label="搜索对话"
            value={search}
            placeholder="搜索对话"
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="history-list">
          {conversations
            .filter((c) => c.title.includes(search))
            .map((c, index) => (
              <button
                className={`history-item ${active === c.id && view === "chat" ? "selected" : ""}`}
                key={c.id}
                onClick={() => selectConversation(c.id)}
              >
                <Icon name="chat" size={16} />
                <span>{c.title}</span>
                {index === 0 && <span className="history-dot" />}
              </button>
            ))}
          {!conversations.some((c) => c.title.includes(search)) && (
            <p className="search-empty">没有匹配的对话</p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="workspace-note">
            <Icon name="database" size={17} />
            <div>
              <strong>演示工作区</strong>
              <span>合成数据 · 不连接正式系统</span>
            </div>
          </div>
          <div className="profile">
            <span className="avatar">T</span>
            <div>
              <strong>TERUISI 团队</strong>
              <span>AI 工作台预览</span>
            </div>
            <span className="version">V2</span>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-only"
              aria-label="打开导航"
              onClick={() => setSidebar(true)}
            >
              <Icon name="panel" />
            </button>
            <span>AI 助理</span>
            <Icon name="chevron" size={13} />
            <strong>{view === "chat" ? "AI 对话" : "AI 管理"}</strong>
          </div>
          <div className="top-actions">
            <span className="demo-badge">
              DEMO <span>交互预览</span>
            </span>
            {view === "chat" && (
              <button
                className={`text-button details-toggle ${details ? "is-on" : ""}`}
                onClick={() => setDetails(!details)}
              >
                <Icon name="panel" size={16} />
                任务详情
              </button>
            )}
          </div>
        </header>
        {view === "settings" ? (
          <Settings
            config={config}
            onSave={setConfig}
            notify={setToast}
            onBack={() => setView("chat")}
          />
        ) : (
          <div className="chat-layout">
            <section className="chat-workspace">
              <div className="conversation-bar">
                <div>
                  <h1>{current.title}</h1>
                  <span>
                    {messages.length ? "个人会话" : "一个问题，开启新的思路"}{" "}
                    <span className="separator">/</span> 仅本页保留
                  </span>
                </div>
                <button
                  className="text-button"
                  onClick={() => setView("settings")}
                >
                  <Icon name="settings" size={16} />
                  <span>模型设置</span>
                </button>
              </div>
              <div
                className="messages-scroll"
                ref={scroller}
                onScroll={() => {
                  const el = scroller.current;
                  pinned.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                }}
              >
                <div className="messages-inner">
                  {!messages.length && (
                    <div className="empty-state">
                      <Mark />
                      <div className="eyebrow">YOUR OPERATIONS COPILOT</div>
                      <h2>有什么需要一起解决？</h2>
                      <p>复盘经营、发现机会，或把一个问题理清楚。</p>
                      <div className="starter-grid">
                        {[
                          ["chart", "经营复盘", "帮我复盘本周经营情况"],
                          ["database", "补货建议", "哪些商品需要优先补货？"],
                          ["file", "排版体验", "展示表格和代码排版"],
                        ].map(([icon, label, prompt]) => (
                          <button key={label} onClick={() => send(prompt)}>
                            <Icon name={icon} size={22} />
                            <strong>{label}</strong>
                            <span>{prompt}</span>
                            <Icon name="chevron" size={15} />
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {messages.map((message) =>
                    message.role === "user" ? (
                      <div className="user-message" key={message.id}>
                        <div>{message.content}</div>
                        <span className="avatar user-avatar">我</span>
                      </div>
                    ) : (
                      <article className="assistant-message" key={message.id}>
                        <div className="assistant-heading">
                          <Mark small />
                          <strong>小特</strong>
                          <span>{message.modelName || "经营分析"} · 演示</span>
                          {message.status === "streaming" && (
                            <span className="live-pill">
                              <i />
                              正在生成
                            </span>
                          )}
                        </div>
                        <div className="answer-body">
                          <details className="process">
                            <summary>
                              <span
                                className={
                                  message.status === "streaming"
                                    ? "spinner"
                                    : "process-check"
                                }
                              >
                                {message.status !== "streaming" && (
                                  <Icon
                                    name={
                                      message.status === "complete"
                                        ? "check"
                                        : "clock"
                                    }
                                    size={14}
                                  />
                                )}
                              </span>
                              {message.status === "complete"
                                ? `已整理 ${message.sources?.length || 0} 个演示来源`
                                : message.stage || "正在生成"}
                              <Icon name="down" size={13} />
                            </summary>
                            <div>
                              {message.sources?.map((source) => (
                                <span key={source}>
                                  <Icon name="file" size={13} />
                                  {source}
                                </span>
                              ))}
                              <p>
                                本轮使用固定演示文本，不查询业务数据或调用真实模型。
                              </p>
                            </div>
                          </details>
                          <Metrics items={message.metrics} />
                          <RichText
                            content={message.content}
                            streaming={message.status !== "complete"}
                          />
                          {message.status === "streaming" && (
                            <span
                              className="stream-cursor"
                              aria-label="生成中"
                            />
                          )}
                          {(message.status === "error" ||
                            message.status === "stopped") && (
                            <div className="partial-notice" role="status">
                              <Icon name="clock" size={16} />
                              {message.stage}
                            </div>
                          )}
                          {message.status === "complete" && (
                            <div className="answer-actions">
                              <button
                                className="text-button"
                                onClick={() => copy(message.content)}
                              >
                                <Icon name="copy" size={15} />
                                复制
                              </button>
                              <button
                                className="text-button"
                                disabled={busy}
                                onClick={() =>
                                  send(
                                    scenarios[message.scenario || "sales"]
                                      .prompt,
                                  )
                                }
                              >
                                <Icon name="spark" size={15} />
                                重播流式效果
                              </button>
                              <span>合成数据示例</span>
                            </div>
                          )}
                        </div>
                      </article>
                    ),
                  )}
                </div>
              </div>
              <div className="composer-area">
                <div className="composer-inner">
                  <div className="followups">
                    {suggestions.map((s) => (
                      <button key={s} disabled={busy} onClick={() => send(s)}>
                        {s}
                        <Icon name="chevron" size={13} />
                      </button>
                    ))}
                  </div>
                  <div className="composer">
                    <textarea
                      ref={textarea}
                      aria-label="输入问题"
                      placeholder="向小特提问，或继续追问…"
                      value={draft}
                      maxLength={4000}
                      rows={2}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (
                          e.key === "Enter" &&
                          !e.shiftKey &&
                          !e.nativeEvent.isComposing &&
                          !e.repeat
                        ) {
                          e.preventDefault();
                          send();
                        }
                      }}
                    />
                    <div className="composer-toolbar">
                      <div className="composer-options">
                        <label className="model-picker">
                          <Icon name="spark" size={15} />
                          <select
                            aria-label="选择演示模型"
                            value={model}
                            disabled={busy}
                            onChange={(e) => {
                              setModel(e.target.value);
                              setToast(
                                "已切换演示预算；回答仍使用固定演示文本。",
                              );
                            }}
                          >
                            <option value="analysis">{config.name}</option>
                            <option value="quick">快速问答</option>
                          </select>
                        </label>
                        <span className="context-pill">
                          <Icon name="database" size={13} />
                          演示数据
                        </span>
                      </div>
                      <div className="send-group">
                        <span className="key-hint">
                          {busy ? "接收中…" : "Enter 发送"}
                        </span>
                        <button
                          className={`send-button ${busy ? "stop" : ""}`}
                          title={busy ? "停止生成" : "发送"}
                          aria-label={busy ? "停止生成" : "发送"}
                          disabled={!busy && !draft.trim()}
                          onClick={() => (busy ? stop() : send())}
                        >
                          <Icon name={busy ? "stop" : "arrow"} size={20} />
                        </button>
                      </div>
                    </div>
                  </div>
                  <div className="composer-footnote">
                    <span>演示回答采用预设场景，刷新后重置</span>
                    <span>
                      <span className="connection-dot" />
                      SSE 流式传输
                    </span>
                  </div>
                </div>
              </div>
            </section>
            {details && (
              <aside className="detail-panel">
                <div className="detail-title">
                  <h3>任务详情</h3>
                  <button
                    className="icon-button"
                    aria-label="关闭任务详情"
                    onClick={() => setDetails(false)}
                  >
                    <Icon name="close" size={17} />
                  </button>
                </div>
                <div className="detail-section">
                  <span className="eyebrow">本次会话</span>
                  <h4>{model === "quick" ? "快速问答" : config.name}</h4>
                  <dl>
                    <div>
                      <dt>上下文窗口</dt>
                      <dd>{Number(config.contextWindow).toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt>输出预算</dt>
                      <dd>
                        {(model === "quick"
                          ? 8192
                          : Number(config.maxOutput)
                        ).toLocaleString()}
                      </dd>
                    </div>
                    <div>
                      <dt>回答方式</dt>
                      <dd>流式 · 格式化</dd>
                    </div>
                  </dl>
                  <small>容量为演示配置，非实际模型用量。</small>
                </div>
                <div className="detail-section">
                  <span className="eyebrow">演示来源</span>
                  {latest?.sources?.length ? (
                    latest.sources.map((s, i) => (
                      <div className="source-card" key={s}>
                        <span>0{i + 1}</span>
                        <div>
                          <strong>{s.split(" · ")[0]}</strong>
                          <small>{s.split(" · ")[1]}</small>
                        </div>
                        <Icon name="check" size={14} />
                      </div>
                    ))
                  ) : (
                    <p className="muted">发送问题后显示来源。</p>
                  )}
                </div>
                <div className="detail-section">
                  <span className="eyebrow">连接演示</span>
                  <label className="field">
                    <span>下次请求场景</span>
                    <select
                      aria-label="传输演示场景"
                      value={mode}
                      disabled={busy}
                      onChange={(e) => setMode(e.target.value)}
                    >
                      <option value="normal">正常流式输出</option>
                      <option value="disconnect">中途断开连接</option>
                      <option value="error">生成阶段失败</option>
                    </select>
                  </label>
                  <p>可体验异常时保留部分回答。不会自动重新发起请求。</p>
                </div>
              </aside>
            )}
          </div>
        )}
      </main>
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" size={16} />
          {toast}
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
