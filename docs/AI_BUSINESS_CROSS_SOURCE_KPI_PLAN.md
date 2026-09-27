# 单店跨来源 KPI 选择候选

`business_analysis.cross_source_kpi_plan` 是纯合同，尚不读取事实、不分配 ERP 销售、不计算跨来源金额，也不注册报告、Agent 或文件。调用者必须从**同一个已封存来源目录**提供实际 Reader 的 `sources` 与每个所选来源的 `info`；纯函数仅验证声明的结构、比较日期、来源引用与覆盖相互一致，结果恒为 `authorityVerified=false`、`identityAssignmentVerified=false`、`numericTotalsVerified=false`。

首版固定一个平台、一个精确店铺和同一原始分析起止日期。来源角色分别是 ERP `sales`、网店原生 `sku`、网店原生 `spu`、网店 `promotion`，以及一个本期最新 `master`。四个经营角色都要求 `current` 来源；`previous` 与 `yearAgo` 必须分别给精确来源 key 或 `null`。只有完整目录确无同身份比较来源，才允许 `null` 并标 `missing_source`；已有来源不能人为略过再称缺源，不产生零值或同比环比。每个已选来源有独立 `sourceRef`、`evidenceDigest`、行数、来源 revision 与实际日期覆盖；当前主数据还须明确快照或无记录状态。

ERP 各期间均显式与**同一个本期主数据**建立 `mapping_plan` pair，并由既有 `validate_baseline_pair` 核本期/上期/去年同期的来源范围。当前主数据不是历史 SKU 归属。原生 SPU 支付与 SKU 汇卷分列，推广归因成交不是 ERP 净销售，商品日访客不是店铺区间去重 UV；歧义和未匹配 ERP 事实将进入后续待分配桶，而本候选不分配任何一条事实。ERP `sales.analysis` 的净销售、退款绝对额、成本与大毛利是将来销售层的权威；网店商品支付和广告费用/归因各用自身来源，不能跨来源直接相加。月度财报不可拆成日利润。

当前 v2 收集器每来源最多 **2,000 页×100 行**。参考推广源约 **575,095 行**，超出这一物理上限。纯候选允许这个已声明的元数据行数进入检查，但明确返回 `reference_scale_capacity_gap`、`unsupported_current_v2_collector` 与 `currentV2CollectorSupported=false`，绝不把它冒充现有 v2 能完整采集。此标记没有放宽实际收集、存储或 Reader 限额；需另立经验证的 v3/分片采集与报告绑定后才能做真实规模交付。

下一切片先建逐 ERP 源行的版本化身份分配账本，保留业务日期、源行 ID、线上规格码、产品编码、ERP 类目和完整 SKU/SPU 候选及原金额。既有 `identity_partitioned` 只把同编码的前两个不同候选用于证明歧义，并按匹配状态/SKU/SPU 汇总，不能直接形成逐日/品类异常表；不得自动把同 SPU 的多个 SKU 或缺线上规格码的 ERP 行分配为唯一 SKU。历史归属、成本质量和推广三类 SKU 身份均须单独核实。随后才能以分区、有界、单事实单次的方式生成店铺/逐日/品类/SPU/SKU 和待分配表，并对每个来源及各层回卷独立对账；v3 混合封存当前仍不授权报告生成。
