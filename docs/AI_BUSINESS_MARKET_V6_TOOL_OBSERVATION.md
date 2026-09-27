# 市场 v6 工具读取观察候选

`backend/ai_assistant/business_market_v2_tool_observation_v6.py` 只在隔离 `DJANGO_ENVIRONMENT=test` 且显式打开观察与 v6 计划开关时工作。拥有方从已封存的市场执行根重读计划与零预留，以新报告/流程的拟建角色槽位构造**未持久化**观察身份，通过现有内部市场工具读取真实封存来源两次，并核对两次结果、角色/任务/参数/来源摘要与末尾计划状态。响应只含有界摘要与行数，不发布原始市场行。

隔离 PostgreSQL 的真实角色测试 2 项通过：市场 `summary` 与价格带 `page` 均有来源读取，模型和远程工具调用被断言为零，六张 Agent 派发/结果/已读表前后行数不变；默认关闭、错误角色和跨账号均拒绝。测试集群停机。此观察的 provider ID、job ID 只是尚未持久化的候选标签；`providerResponseAuthenticated`、`jobPersisted`、`toolDispatchPersisted`、`observationPersisted`、`agentReadPersisted`、`numericCitationAllowed` 和发布/人审始终为 false。

真实五 Agent 本人已读仍需独立可核验的模型/provider 派发、同任务持久回执、数值单元格复核，以及权威人民币费率、人工单报告上限和逐轮原子预留。0072 的待核提案与本观察均不能代替付费调用许可或业务建议发布。
