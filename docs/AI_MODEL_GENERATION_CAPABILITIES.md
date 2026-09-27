# 模型生成能力配置

本变更于 2026-09-10 完成本机生产采用，Django AI `0009` 已应用，见 [生产采用证据](evidence/ai-workbench-production-20260910.json)。未改写既有 5 个模型及 69 条历史消息的原有字段；真实供应商付费问答未作为发布验收。新建页面默认输出 65536、对话总时限 1000000 秒、温度跟随供应商，已有配置保留原值。API 老客户端省略新增选项时保留已有选项。

## 管理界面

| 参数 | 系统允许范围 / 行为 |
| --- | --- |
| 最大输出 Token | 128–131072；仍须符合实际端点限制 |
| 上下文窗口 | 8192–2000000；管理员声明输入与输出总预算，不扩展模型本身的窗口 |
| 对话总时限 | 30–1000000 秒，默认 1000000 秒；统一约束生成与工具查询，嵌套渠道时限不能被放大 |
| 输出参数格式 | OpenAI 兼容端点明确选择 `max_tokens` 或 `max_completion_tokens`；Anthropic 固定 `max_tokens` |
| 温度 | 温度设置与自定义输入合并，只在选择自定义后显示数值；默认不发送。自定义 OpenAI 兼容 0–2、Anthropic 0–1，模型可能有更窄限制 |
| 推理 | 保持原有方式、Thinking 开关、reasoning_effort、Anthropic 固定预算或自适应；不按模型名称猜测 |
| 工具额度 | 保留现有 1–62 轮、1–74 次，默认 6/12；中央工具自身上限继续有效 |
| 流式用量 | OpenAI 兼容端点可显式请求 `stream_options.include_usage`；不支持时不得勾选 |
| 附加系统提示词 | 最多 8000 字符，只用于业务背景和回复风格，不扩展权限 |

切换协议会清除不适用的参数格式。固定思考预算必须小于最大输出；Anthropic 思考选项要求跟随默认温度。供应商拒绝所选参数时明确报错，不自动修改参数再次调用或重发可能计费的请求。连接测试按保存参数执行，实际模型的最大值和推理档位仍须由管理员按所用服务端点文档确认。

单轮超时已从管理界面和文本/视觉 provider 执行中移除。历史 `timeout_ms` 列仅为旧客户端/迁移兼容保留，省略时不改旧值，不再约束文本/视觉生成；图片生成配置仍属于独立模型配置。新建默认最大输出为 65536、任务总时限为 1000000 秒（约 11.6 天），旧模型保留已保存的总时限，尚无选项的旧模型沿用 260 秒总预算。连接测试保留独立的 610 秒诊断预算。Web 转发上限同步为 1000000 秒加 30 秒收尾，活动请求配额窗口同步覆盖最大任务时限；这不是持久后台任务，供应商、浏览器或代理中断、进程退出仍会结束连接。取消会关闭当前请求拥有的 socket，避免长时限下继续阻塞等待；不自动续跑付费请求。

## 上下文、长回复和用量

历史候选最多 200 条、合计 4 MiB，按需逐条读取。每次供应商派发前，以 UTF-8 字节数 / 2 的保守启发式估算输入 Token，扣除最大输出与至少 2048 Token / 5% 窗口的余量；超限时从最早的完整用户轮次开始移出，保留当前问题及其工具调用和结果。若当前轮次仍放不下，明确返回上下文预算不足。此过程没有额外付费摘要调用，不修改历史记录，也不能保证与供应商分词完全一致。

Web AI 对话正文上限提高为 524288 字符；供应商 JSON 响应 8 MiB，供应商 SSE 原始传输 64 MiB，应用 SSE 32 MiB。SSE 包含逐片重复的协议元数据，传输上限与正文上限分别约束。客户端每 80 毫秒批量刷新正文，避免每个片段都重排大段 Markdown。历史列表仍有分页与预览限制，通过已鉴权的指定消息读取显示“展开完整回复”，不会刷新后丢失长回复。

消息保存实际已报告的输入/输出/思考 Token、模型与工具调用次数、耗时、停止原因、输出截断和上下文估算。未报告用量显示为缺失，部分调用缺失时不宣称完整总计；不把估算用作计费。统计与消息、receipt 在同一事务内保存，成功重放返回原统计，不重新计数。

模型参数由共享 provider 使用，Agent、分析 consumer、钉钉仍保留各自的任务租约、结果体积、渠道发送和执行时限。不能把 Web 对话的总预算理解为所有渠道的预算。钉钉超长正文按现有渠道范围截断并提示，外发仍遵守原有规则。

## 数据与验证

首版隔离验证结果见 [2026-09-10 验证记录](evidence/ai-model-generation-capabilities-20260910.json)，后续默认值与统一时限调整见 [表单调整验证记录](evidence/ai-model-form-adjustments-20260910.json)。这两份保留为候选验证，正式采用、数据回查及备份恢复结果以本页顶部的生产采用证据为准。

AI `0009` 仅给 `ai_models` 增加 `generation_options_json`、给 `ai_conversation_messages` 增加 `execution_json`，默认均为 `{}`。PostgreSQL 限制其为 JSON 对象并分别限制 64 KiB / 16 KiB。沿用同表 writer、principal、scope、revision 和 mutation 审计；历史迁移兼容代码明确排除新列，不恢复旧业务读写。

复现命令（独立 worktree）：

```powershell
python tools/ai-postgres-rehearsal.py --tests-only --generation-upgrade --port 55981
npm run build
npm run test:unit
npm run lint
npm run check:backend-boundary
```

升级演练只允许新的隔离 PostgreSQL 数据库：先迁至 `0008`，写入合成旧模型与消息，再迁至 `0009`，逐字段验证旧值及不透明密钥占位符保持不变；第二次迁移为无操作。正式发布须一起采用 Django `0009`、AI reader/writer 代码与 Worker，复验权限和恢复，再按实际端点进行长回答、SSE、工具、停止及保存回查。没有自动部署、真实付费调用或机器人发送。

参数参考：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Anthropic 思考预算与自适应迁移](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)、[智谱 Thinking](https://docs.bigmodel.cn/cn/guide/capabilities/thinking)。其他托管平台应以该平台端点文档为准。
