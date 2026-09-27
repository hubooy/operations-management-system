# 市场五角色同报告已读链：0073 前置阻断合同

状态：设计与负向验收；**没有创建 0073 迁移，没有启动实际模型或将任何报告标为已读**。

0062 的 `ai_market_v2_read_claim` 绑定 `business-agent-screening-promotion-market-execution-v2` 的原报告、该报告的 0061 context proof、同一流程节点和 `running/completed` job、成功的 provider 派发及响应中精确匹配的工具调用、成功的工具结果。0060 的报告和流程永久 `paused`，明确禁止节点/job；0061 证明也要求原流程无节点/job。因此旧执行报告永远不能满足 0062 正例。

0064 从旧计划另建 `business-agent-screening-promotion-market-synthetic-v4` 报告。五个 job 虽各有同 job 的 provider/tool 行，但全部处于 `paused`，provider 响应是由数据库造出的 `syntheticOnly=true`、`externalProviderCalled=false`，工具结果是 `persistedRead=false` 的固定占位内容，且这些行不属于 0062 查询的旧执行报告。即使有相同来源摘要、五个角色及工具名，也不能把这些占位行翻译为“模型本人已读”；当前真实角色目标测试逐一对五个工具调用 0062 attestor 并要求零回执。

0073 的真实正例需要先有以下**同一个新报告/流程/任务**的受保护、版本化能力；缺任一条件即维持关闭：

1. 新 profile 用不可变来源根引用已验证的旧报告和 0061 context proof，但新报告的拥有者、账号版本、来源清单、五工具目录和六节点图须由 SQL 重验，不能让调用方只提交摘要或把旧证明直接改挂。旧 0060/0061/0062/0064 行、ACL 和 paused 守卫保持不变。
2. 每个角色的 job、provider dispatch/result、tool dispatch/result 必须属于该新流程及本角色节点；provider 响应中的 call ID、名称、规范化参数与工具派发完全一致，并证明响应来自实际允许的模型调用。`syntheticOnly`、`dry_run`、`market-v2-synthetic-only`、`externalProviderCalled=false` 或未知调用状态均拒绝“Agent 本人已读”。
3. 现有 owning 工具必须真的完成对应模式的来源回放，工具结果携带报告、角色、市场 selector/manifest/context、页或行、完整结果摘要；工具结果成功入账后才可由独立 NOLOGIN attestor 写同 job 回执。仅有通用 `succeeded` 行和自报的 `auditStatus=recorded` 不足以证明 owning 读取。
4. 费用来源、汇率和人工上限、逐轮持久预留与未知结果处理先独立通过。0065 预留为 0；0069 仍是隔离的无外部调用彩排；0072 的来源与人工上限仍是待核提案。没有付费许可时，不进入真实 provider 调用，也不能制造替代 provider 响应。
5. 数值引用另需同任务精确行/字段二次拥有方复算与独立持久单元格证明。即使后续已读回执成立，`numericCitationAllowed` 和发布权限仍应保持 false，直到独立门禁验收。

可以先做“同任务工具执行已持久化”的**单独**测试合同，但它必须明确 `agentReadPersisted=false`，不能复用 0062 的回执名称或 `persistedRead=true` 字段。此切片没有安全的 0073 SQL 正例；为避免用合成回执越权，暂不新增表、角色、函数或运行入口。下一次开发应先完成实际模型调用授权与拥有方工具结果合同，再以新 profile、精确数据库守卫和独立 PG 升级/恢复演练实现 0073。

后续新增 `business-market-v2-read-admission-v5-candidate-v1` **只读准入诊断**，由 `AI_MARKET_V2_READ_ADMISSION_V5_ENABLED` 且 Django 隔离 `test` 环境双重限制，默认关闭。拥有方读取 0063 计划/账号当前版本及候选报告真实行，对旧执行报告核空 job，对 0064 报告核六节点、五角色 job→provider→tool 的精确外键/调用/摘要与合成标志，然后交纯版本合同分类。它能显示“同任务结构已观察”，但始终返回 `agentReadPersisted=false`、`providerCallsAllowed=false`、`numericCitationAllowed=false`；未知 profile、跨报告、改挂、付费标志或自称真实调用均拒绝。这个诊断不调用 0062 attestor，不创建新报告或任何回执，亦不把 0064 固定工具结果说成拥有方真实读取。隔离 PG 目标为 `ai_assistant.test_business_market_v2_read_admission_v5_role`，纯目标为 `ai_assistant.test_business_market_v2_read_admission_v5_contract`。
