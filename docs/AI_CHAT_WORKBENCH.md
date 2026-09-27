# AI 对话工作台

本变更把已确认的演示布局接入现有 React 对话控制器，已于 2026-09-10 完成本机生产采用，见 [生产采用证据](evidence/ai-workbench-production-20260910.json)。正式 SSE 通道已用无效输入验证派发前的流式错误返回；真实供应商调用仍以具体端点验收为准。

Worker 使用 `redirect: manual` 并拒绝全部重定向响应，避免 workerd 不支持 `redirect: error` 导致请求在发送前失败。真实隔离 workerd 回归覆盖签名、中文 SSE 与拒绝跟随重定向。

后续模型参数增强的输出、时限、上下文与新增字段以 [模型生成能力配置](AI_MODEL_GENERATION_CAPABILITIES.md) 为准；下文 260 秒、2 MiB、48000 字符及“没有新增字段”描述的是最初工作台提交的范围。

## 页面行为

- 左侧保留当前账号、当前板块的真实会话，支持新建、删除、加载更多及搜索已加载标题。标题取首条问题前 40 个 Unicode 字符。
- 中间显示消息与原有分析产物，下方提供实际可用模型、页面上下文、输入和停止按钮。详情面板仅显示系统已有的模型、筛选和权限信息。
- 用户消息和输入框为 13px，AI 正文为 14px。各业务页面的 AI 抽屉复用同一组件，保留原来的板块会话隔离与后台请求生命周期。
- AI 正文使用按需加载的 `react-markdown` 与 GFM 排版，支持标题、列表、表格、引用和代码。跳过原始 HTML，保留默认安全链接转换；图片引用显示为说明，系统附件仍走既有受控产物入口。复制保留消息源文本。
- 流式排版只处理显示副本，缓冲未完整到达的表格控制行并临时补齐常见格式；不修改数据库中的原文。用户向上浏览时不会被新片段强制拉回底部。

## SSE 与保存

浏览器继续使用同源 `POST /api/ai/chat`，增加 `Accept: text/event-stream`。Worker 完成原有身份、同源、上下文检查并签名转发至配置中的 Django AI writer。供应商请求增加 `stream: true`，OpenAI 兼容 Chat Completions 和 Anthropic Messages 的增量在 Django 中还原为既有 provider 契约。

公开事件按递增 `id` 发送：`status`、`reset`、`delta`、`tool`、`done`、`failure`；等待时发心跳。仅把正文和已完成工具的脱敏标题传给页面，推理、签名与工具参数不作为公开增量输出。进入工具调用或下一轮生成时清空上一轮临时正文。

`done` 仅在既有 PostgreSQL receipt、消息、产物和审计成功提交后发送。临时片段不是第二份持久结果，不支持按 SSE ID 续传。断线后通过原请求回执与已保存消息核对；供应商派发未知时不自动重新请求。未使用 SSE 的内部调用和钉钉保留原 JSON 行为。

停止与断开连接向上游传播取消，并调用既有取消回执接口。供应商 socket 阻塞读取时，取消可能等待该次读取或现有模型超时结束；不会提前释放并发名额或开启下一次派发。沿用 260 秒执行预算、300 秒边缘连接预算、2 MiB 传输边界和 48,000 字符消息边界，不扩大模型参数、工具轮数或权限。

Django 流响应使用最多两个现有 primary 名额，每个名额对应一个请求内 producer。WSGI 读取线程在 producer 启动前关闭自己的数据库连接，producer 独占请求的连接并在退出时关闭。队列最多 32 项、单次入队等待 2 秒；浏览器断开后仍持有名额直至 producer 结束，避免额外并发模型请求或突破连接预算。

## 验证与采用

- Node 测试覆盖中文逐字节解码、事件顺序、异常结束、writer 签名转发、响应边界、安全 Markdown 和取消不重复派发。
- Django 测试覆盖两种协议、真实 HTTP 首段提前到达、断开后的并发名额、真实线程签名请求与数据库提交、receipt 幂等、越权拒绝和失败不重试。
- `tools/ai-postgres-rehearsal.py --tests-only` 在 worktree 独立目录和 55443 端口建立临时 PostgreSQL，运行 AI 全套测试后停止，不连接生产库。旧数据集测试夹具补齐终态 authority 必填验证记录与激活时间，不放宽生产约束。
- `npm run preview:isolated` 使用独立合成数据，拒绝业务写入，不调用真实付费模型；页面预览不能代替生产权限、供应商和发布验收。
- 没有新增数据库字段、迁移、角色权限或中央工具。正式采用需要一起发布 Worker 与 Django AI 代码，并完成真实端点的 SSE、停止、工具与最终保存回查。供应商不接受 SSE 时明确失败，不自动以 JSON 再发一次可能计费请求。

实现入口：`app/ai-chat-workbench.tsx`、`app/ai-markdown.tsx`、`lib/ai/chat-stream.ts`、`lib/django/ai-stream.ts`、`backend/ai_assistant/chat_stream.py`、`backend/ai_assistant/provider_stream.py`。原有控制器、principal、scope、工具注册表和 receipt 仍为权威契约。
