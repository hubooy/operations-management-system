# 市场 v2 新报告创建候选（默认关闭）

内部 `business_market_v2_report_creation_candidate.prepare()` 只生成可审查的候选字节，不创建报告、工作流、Agent任务、工具派发或文件。它先核现有词货 v1 筛查报告的真实所有者、封存 v2 证据、筛查意图、预算与工作流输入，再按同一报告明确选择的京东市场本期/基期、价格带和两日观察日期，完整重放拥有方三张市场类型表。输出的目标报告ID必须尚未占用，来源报告、证据封存、市场准入和材料摘要都被固定到候选 snapshot/workflow input；返回前再次复验账号和来源。

五个Agent的图、工具顺序、角色读策略及人审依赖直接复用已有 `business_promotion_market_runtime_v2_contract`，不另造第二套规则。候选输出确切的 `snapshotDigest`、`workflowInputDigest`、`workflowGraphDigest`、市场清单和来源绑定，供后续迁移评审；其 schemaVersion 故意不同于现有可创建报告，`registered=false`、`reportCreateSupported=false`、`agentDispatchSupported=false`、`toolRegistered=false`、`renderer8Supported=false`。市场 summary 的完整服务端重放不等于任何Agent本人已读；后续数值引用还须同一Agent的持久工具回执与精确行复算。

候选仅含选定 TOP 市场样本，不证明全行业覆盖、本店商品身份或与ERP/B端销售的可加性。三张材料最多200,000行、64MiB，候选协议最多256KiB；超限或任一观察日缺失直接拒绝，不抽样冒充完整。正式运行仍需独立新报告画像的数据库创建门禁、snapshot/workflow版本化约束、工具注册和角色授权、Agent派发/结果/引用收据、人审以及renderer 8 文件发布与备份恢复验收。本模块不改旧v1报告快照或任何持久状态。
