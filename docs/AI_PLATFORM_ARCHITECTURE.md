# TERUISI AI 平台架构

本文件说明当前代码已经落地的 AI 能力边界。外部架构图只作为设计参考；实际契约以本仓库的代码、迁移和测试为准。Django 候选的正式切换状态与操作证据见 [`DJANGO_AI_ASSISTANT_MIGRATION.md`](DJANGO_AI_ASSISTANT_MIGRATION.md)，源码实现不等于正式发布。

## 端到端链路

```text
当前页面上下文
  -> 中央只读工具注册表
  -> 同步 Chat 工具循环
  -> owner 私有全局记忆（低信任召回）
  -> 确定性分析沙箱（JSON AST）
  -> Django/PostgreSQL Agent 长任务与检查点
  -> 有界 DAG 多 Agent 工作流 + 人工复核
```

### 1. 当前页面与全部业务数据

- 所有主模块和规范子视图都登记在 `lib/ai/page-context.ts`，页面只传模块、视图、统一统计周期和导入来源等白名单上下文。
- 页面上下文不是身份、权限或业务事实，服务端将其作为低信任提示数据注入，用户原问题保持原文，不与系统指令拼接。
- 当前数据能力只在 `lib/ai/tool-registry.ts` 声明一次。页面适配器复用财务、库存、网店、运营事务、导入、市场和设置领域服务；身份、角色和数据 scope 只取服务端 principal。
- 列表、对比和趋势都有服务端硬上限。没有安全持久状态源的自动化运行状态会返回明确的 `unavailable`，不会探测 localhost、Cookie、Profile 或本机路径。

### 2. 同步 Chat Agent 循环

- AI 对话使用现有 OpenAI-compatible / Anthropic 工具循环，保持 provider tool-call ID、每轮和每请求调用上限、超时、取消、响应体积与审计边界。
- 当前运营数据问题先核对数据新鲜度，再调用有界只读工具；最终回答必须披露截止日期、筛选、金额口径和截断状态。
- 客户端请求使用稳定 `clientRequestId`。PostgreSQL receipt 和逐 provider 请求 dispatch ledger 防止网络重试重复付费；结果不确定时失败关闭。
- 表格型结果可以形成 owner/scope 隔离的对话产物；写入、发布、导入和危险动作没有伪装成只读工具。

### 3. 全局记忆

- 记忆仅允许当前 owner 在管理界面显式确认写入，类型为个人偏好、业务术语和稳定业务背景。
- 写入经过来源、敏感/指令内容、精确重复和近似重复四道闸；更新与归档使用 `expectedVersion` CAS，删除为软归档。
- 记忆 mutation 与脱敏摘要审计在同一 PostgreSQL 事务中提交。审计不可用时整批失败。
- 对话召回最多 8 条、4000 字；记忆以 `personal_memory` 附加到最新一轮用户消息，只在本次 provider 请求中存在，不写入聊天历史，也不进入 system prompt。

### 4. 代码执行沙箱

- Cloudflare Worker 内不开放任意 Python、JavaScript、SQL、`eval`、子进程或网络代码执行。
- 既有页面安全沙箱采用白名单数据集和确定性 JSON AST，支持 `filter / select / derive / group / sort / limit`。
- 新增候选 `run_pandas_analysis` 中央工具：经真实权限导出系统数据集，再由独立 Linux/rootless 容器执行 pandas，返回有界表格。源码、配额、签名、防重、资源/清理门禁与尚未执行的真实容器验收见 [pandas 容器分析说明](AI_PANDAS_SANDBOX.md)。未部署独立 broker 时工具失败关闭，不在 Django 或 Worker 内执行代码。
- 数据先按真实 principal 查询，再进入无网络转换阶段；源行、步骤、列、结果行和序列化字符数都有硬上限。
- PostgreSQL 只保存 owner、scope、数据集、操作名、截止日、行数和摘要哈希，不保存完整结果或原始查询内容。
- 若未来需要通用 Python/Node，必须使用独立隔离执行服务，至少具备无默认网络、只读输入挂载、临时工作区、CPU/内存/时长/输出配额、镜像白名单和可验证清理；不能直接在 Worker 内 `eval`。

### 5. Agent 长任务

- `ai_agent_jobs` 保存 owner、不可变 scope 快照、幂等请求、模型 ID/版本、允许工具快照与策略摘要、被动 JSON 输入/状态/输出、版本、租约和终态。
- 正式 runner 每次只允许一个有界微步骤：最多派发一次 provider，或执行一次中央注册表中的只读工具；它使用 lease token、单调 epoch 和过期条件 fencing，迟到执行进程无法覆盖新 owner。
- 每步产生 checkpoint 与追加式事件；任务支持 CAS 取消/恢复，并限制活动任务、最大 64 步和最多 16 次恢复。
- 正式 Agent 创建时固定模型版本和完整工具策略；每轮派发前重新核验 owner 仍有效、角色仍获准、当前 scope 仍覆盖创建快照、模型版本未变化且工具策略摘要完全一致，任一条件漂移都失败关闭。
- provider 与工具调用各有不可变派发/结果 ledger。派发后若无法确定外部调用是否完成，任务进入非重试失败，不会自动重放付费 provider 或工具；已持久化的结果可在检查点丢失后被安全消费。
- Agent executor 本身不运行任意 Python、JavaScript、SQL、浏览器自动化或运营写入；Agent 只能使用中央注册表明确开放给 `ai_agent` surface 的工具。pandas 工具的代码仅由独立容器执行，既有 JSON AST 分析继续保留。

### 6. 多 Agent 工作流

- 工作流只允许 `agent` 和 `human_review` 节点，服务端校验严格字段、节点数、依赖数、深度、拓扑顺序和无环 DAG。
- 同一工作流一次最多一个活动节点。子 Agent 使用稳定的工作流节点幂等身份，崩溃恢复不会重复派生。
- `dryRun` 会验证并持久推进完整 DAG 形状，但不会创建 Agent 子任务或等待人工审批。
- 正式多 Agent DAG 的创建入口、人工复核、取消、恢复和子 Agent 编排状态机均已开放；工作流在创建时固定与子 Agent 相同的模型版本和工具策略，子任务继承该不可变执行契约。
- Cloudflare scheduled 与受保护的本地 scheduled 入口每个 tick 最多推进一个工作流编排微步骤和一个正式 Agent 执行微步骤，并与图片缓存、AI 空间、市场标注 runner 逐项隔离；dry-run 仍可用于只验证和演练 DAG。
- 代码入口已开放不等于生产版本已发布。正式部署必须按 AI Django 迁移文档完成 39 表采用、备份恢复、写权切换、不可变 Worker 发布和退役；本机开发验证不能替代生产迁移与发布。迁移或生产重启均不会由页面自动执行。

## API 与权限

- `GET/POST /api/ai/memories` 与 `/api/ai/memories/:id`
- `GET/POST /api/ai/sandbox`
- `GET/POST /api/ai/agent-jobs`，以及详情、取消、恢复
- `GET/POST /api/ai/workflow-runs`，以及详情、取消、恢复、节点人工复核

所有列表都是 owner-only，并在读取时重新检查当前 scope 是否仍覆盖创建快照。viewer 只读；analyst、operator、admin 可创建和变更自己的记忆、沙箱运行与编排状态。所有非 Webhook AI JSON 写请求要求精确同源证明和 `application/json`。

## 数据迁移

`backend/ai_assistant/migrations/0001–0004` 定义 39 张历史事实/状态表及 5 张控制表、闭合授权、单写 fencing、追加式审计与不可变 authority。历史 `0084–0087` 保留在隔离迁移/测试材料中；生产源码不再运行旧 AI D1 查询、runtime schema 或 fallback。

公开 Worker 仅负责真实身份、权限、同源、HMAC、传输边界、中央只读工具与私有 R2 字节适配。全部 AI 模型循环、任务编排、元数据写入和审计进入 Django。正式步骤必须绑定最新源快照、精确批准 dry-run、备份/恢复证据、effective Worker head 和不可逆退役 receipt；详见 [`DJANGO_AI_ASSISTANT_MIGRATION.md`](DJANGO_AI_ASSISTANT_MIGRATION.md)。
