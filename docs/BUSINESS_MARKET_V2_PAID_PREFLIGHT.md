# 市场 v2 真实 Agent 调用前门禁（开发候选）

0064 只在隔离测试库建立五个合成 job/provider/tool 持久链，`externalProviderCalled=false`，没有真实 Agent 已读。0065 成本表的 `reserved_cents` 数据库约束恒为 0，费率和人工上限摘要只是声明。不能把该表视为已预留资金或付费调用许可。

新增 `business_market_v2_paid_gate` 识别市场报告家族或五工具名称，并在通用 `agent_tick` 的每个 provider/tool 派发事务之前拒绝市场 job。只读 `inspect` 结合当前账号的 0063 计划及 0065 窄回执给出未满足的五项权威；其返回值始终 `providerCallsAllowed=false`。新 `AI_MARKET_V2_PAID_RUNTIME_ENABLED` 默认关闭；即使打开，缺少持久原子预留的现阶段仍拒绝，每次尝试都不会执行模型调用。旧词货 v1 与普通 Agent 不受该门禁拦截。

真正启用还须另建版本化活跃报告及实际执行 profile，并在受保护数据库中核对独立采纳且当前有效的供应商价格、CNY 汇率、**所有收费类别**和管理员针对该报告的精确费用上限；同一事务把每轮上界从报告余额原子预留并绑定 job、lease、模型、轮次和 provider dispatch。调用结果未知时只留未知状态，禁止自动重试；已知用量还须核价与结算。仅有进程 flag、传入的摘要或本模块的只读结果均不能授予调用。届时应替换本模块的硬关闭分支，并以真实角色权限、并发竞争、超额、过期、未知结果、取消及恢复测试核验。

本切片没有迁移、凭据、网络调用或生产启用。纯测试：`ai_assistant.test_business_market_v2_paid_gate`。隔离真实角色目标测试：`ai_assistant.test_business_market_v2_paid_gate_role`；它只复用 0064 的合成链并断言五 job 均被拒绝，不把合成结果标为真实分析。
