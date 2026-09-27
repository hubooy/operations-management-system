# 市场 v2 五 Agent 结果校验候选（未启用）

2026-09-25 的隔离源码审查：0060 固定报告与图仍是 paused；0062 的 SQL 同任务已读回执会核 job、provider、tool 与结果，但 0060 不允许该家族真实 job；0064 只在隔离测试库保存 `syntheticOnly` 五角色链，并明确 `externalProviderCalled=false`、`persistedRead=false`；0065 只记录费用需求，预留恒为 0，通用 Agent 的付费前置门禁仍拒绝派发。因此目前没有真实五 Agent 结果可供批准或交付。

`business_market_v2_result_candidate.check` 是独立纯合约，未接运行路由、数据库写入、迁移、模型或 renderer。它要求固定五角色各自的不同 job；每次读取的回执、provider 调用、工具派发和工具结果同 job/role/report，摘要与结果逐项匹配。角色必读工具按原五工具图收窄；市场角色必须有本人 summary，market_b2b 对非空价格带和榜单至少有首个 page。预算缺固定来源时仅接受 `unavailable_no_fixed_budget` 且 payload 为 null。结构化市场数值引用必须指向本人已读精确页/行、summary 表摘要和单元格重算值；观察日缺失、未入 TOP、空值及未知来源不转换成 0，市场 TOP 样本不归本店、ERP 或 B 端销售。

这个纯合约的输入仍可由调用者构造，不能证明数据库记录真实存在、当前封存来源未变化，不能证明自然语言段落内的数字都已结构化引用。结果始终返回 `persistedSourceIndependentlyLoaded=false`、`owningRowsIndependentlyReplayed=false`、`proseNumbersVerified=false`、`numericCitationAllowed=false` 和 `reportPublishAuthorized=false`。后续正向接线须由拥有方在同一稳定快照下读取受保护的 0062 回执及实际 provider/tool 行，逐页重放市场材料，对所有文字数值建立结构化引用，结合真实模型费用原子预留、五角色完成检查点、独立复核和人工审批后，再设计版本化发布门禁；不得把此候选的 `candidateChecksPassed` 当成授权。

隔离纯测试覆盖有/无固定预算、五角色、跨 job/provider、重复派发、缺 summary/首页、行身份/摘要/数值篡改、未知来源及缺观察日；无真实模型、客户数据、生产迁移或文件发布。
