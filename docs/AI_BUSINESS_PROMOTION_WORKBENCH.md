# 京东推广词货专项工作台入口

经营分析工作台在已封存的 v2 证据详情中展示 `screening-promotion-v1`。AI reader 对当前账号的真实封存目录完成摘要与状态核验后，只返回京东 `promotion`、`current` 的精确 `sourceKey`；可选基期仅列同平台、同店、同数据集、同原始日期区间的 `previous` 或 `yearAgo` 来源。响应包含 `promotionSupported` 和 `promotionChoices`，未开放或无合格来源时分别为 `false` 与空数组。v1 和未封存任务不能启用。

用户需要明确选择本期来源，可选择一个基期。前端把所选键、`analysisMode=screening-promotion-v1`、`expectedPrincipalKey` 和原始问题交给既有 `/api/ai/business-reports` 创建入口；前端不生成精确来源身份、不创建新的 API 或模型调用。创建服务仍独立复验账号、封存证据、完整目录、来源配对和运行时开关。详情页候选只用于展示，不能当成创建授权。账号、任务或证据版本变化后，选择失效。

`AI_PROMOTION_AGENT_RUNTIME_ENABLED` 必须在 AI reader 与 AI writer 两个进程上由同一受控发布显式设为 `True`。reader 开启但 writer 关闭时，界面可能显示可选来源，而创建会被 writer 拒绝；这属于安全拒绝，应检查两端配置，不得按前端标记绕过 writer 门禁。默认关闭时，选项保持禁用并提示原因。

工作台只说明创建后要经历规则筛查、五个 Agent、人工复核和正式 HTML/XLSX 文件阶段。创建成功只表示任务排队；真实状态在报告详情的节点进度、复核和文件区显示。此入口不自动投放、调价或发送通知，也不因候选存在而断言有完整期间数据。现有多 Agent 与 `screening-v1` 的提交体和未知结果原样重试保留。

验证：`tests/business-promotion-choices.test.ts` 检查账号、任务、版本、精确来源与基期；`backend/ai_assistant/test_business_promotion_workbench_detail.py` 用隔离 PostgreSQL 检查签名详情、禁用开关、跨店/错期间基期及 reader 开而 writer 关的拒绝；`tools/business-promotion-workbench-ui-rehearsal.mjs` 使用合成 loopback API 检查按钮、人工选择、迟到请求、未知结果重试和 390px 布局。生产模型调用与真实业务验收不在本批。

报告详情的推广专项只读面板使用已签名报告详情里的工作流节点状态；在服务端返回 `business-promotion-completed-screening-v1` 前，规则扫描状态明确显示“未核验”。内容返回后显示覆盖记录数量、五角色读取证明存在性、必要口径限制和三类专业分析摘要。原始 Agent 输出不在推广面板直接展示；人工复核状态取工作流节点，正式文件由原文件组件按文件任务 `ready` 与分卷校验展示，报告 `completed` 本身不等于文件可下载。旧筛查详情与旧多 Agent 页面保持原流程。

现有报告详情对推广专项**没有单独的 `screeningPreparation` 字段**，因此在五角色完整内容返回前，页面只能显示工作流节点进度，不能精确显示规则扫描已保存的时间或进度百分比。若未来需要独立扫描进度，须先在服务端提供经过根数据核验的只读回执，再扩展前端；当前面板不据 Agent 节点反推扫描成功。新的纯投影与合成浏览器测试见 `tests/business-promotion-view.test.ts` 和 `tools/business-promotion-report-view-ui-rehearsal.mjs`。
