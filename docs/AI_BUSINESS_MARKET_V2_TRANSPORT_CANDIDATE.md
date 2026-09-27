# 市场 v2 第五工具未注册传输合同

本切片只定义可审查的内部候选，**不添加中央工具目录条目或模型可调用 surface**，也不新增路由、Agent job、provider/tool 派发与结果、数据库迁移或报告文件。旧词货 v1 四工具目录、renderer7/9 语义不变。

候选名称固定 `get_business_promotion_market_v2`，独立 surface 为 `business_agent_screening_promotion_market_v2`，当前绑定 0053 的 `business-agent-screening-promotion-market-admitted-v2` 停放 profile。允许无范围管理员下的 `market_b2b`、`independent_review`、`report` 角色；`commerce` 与 `promotion` 不得调用。模型参数与注入身份分开：参数按固定 `reportId`、`marketContextDigest`、`mode` 校验；summary 不能加字段，page 必须加 `view`/`offset`/固定 `limit=20`，row 必须加 `view`/`rowIndex`/64位 `rowId`，页/行参数不可混用。注入身份的 job/provider ID 仍只是候选声明，0053 仍禁止真实持久派发。

候选适配器复用 0056 窄读函数与拥有方三表预览，保留精确市场材料摘要、样本边界和服务端选出的行引用基础字段（实际数值引用还需补 `metric`、`field` 并由未来已持久同 job 回执逐行重算）。响应固定 `persistedRead=false`、`sameJobProviderPersisted=false`、`registeredTool=false`。完整响应的 UTF-8 字节数和 UTF-16 代码单元数都不得超过 **38,000**；超过则整次报错，没有截断或缩减页内容。协作式单调时钟在各阶段/来源 checkpoint 和返回前核 **12,000 ms**；即使底层阻塞超过12秒后才返回，也不将过期结果送给模型。未来注册工具还需外层执行超时才能硬取消正在阻塞的操作。

现有拥有方预览自身最多约38KB，追加传输身份与行引用后可能越界。若某页或宽行超限，此版明确拒绝；不得静默丢列、改费用、只交前N行却标完整。下一版本须先设计并验证有摘要的更小页/字段投影，再改变模型工具合同。市场上限约20万行/64MB材料，每次读会重放三表，正式12秒可用性仍须参考形状与真实数据规模测量。

2026-09-25 纯合同测试 `ai_assistant.test_business_market_v2_transport_contract` **3/3** 通过；隔离 PostgreSQL 目标 `ai_assistant.test_business_market_v2_transport_candidate` **2/2** 通过，证据 `.runtime/ai-pg-6ec02269c117/tests.log`。首轮容量负例的模拟结果摘要错误，被更早的拥有方摘要门禁按预期拒绝；修正模拟摘要后重新运行，容量/超时负例与三种读取正例均通过，失败轮不计成功。当前并无中央目录注册、真实模型、同 Agent/provider 持久已读、市场业务验收或生产采用。
