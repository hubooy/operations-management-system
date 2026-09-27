# v4 京东推广三期日期包络候选

`business_analysis.period_bound_plan_v1` 接受一个经既有 v4 容量计划严格重建的京东同店推广三窗口 + 一份自然月财报计划。它不修改旧 `business-evidence-v4-capacity-plan-v1` 的任何字段或摘要。新 `business-v4-jd-period-bound-plan-candidate-v1` 包络明确绑定基础计划摘要、`previous_equal_length_v1` 规则、完整 current/previous/yearAgo 区间，以及每个日来源的原始查询日期、精确来源键/身份/查询摘要、实际窗口日期、预期业务日数量和日期集合摘要。

例如原始本期 `2026-08-16—2026-09-14`，包络固定本期 30 日、环比 `2026-07-17—2026-08-15`、同比 `2025-08-16—2025-09-14`。闰日按已版本化规则收敛至去年月末。本版拒绝 `cutoff_date`，避免来源尚未导入到昨天时悄悄缩短用户要求的 30 天；改期须重新形成用户明确确认的新计划。

预期业务日是**采集义务**，不是“已覆盖”事实。有记录日期不证明缺行日期为零业务；零日须后续拥有方的显式证明。`observedDailyCoverageVerified=false`、`zeroDayCertificationVerified=false`、`agentCitationSupported=false`、`registeredRenderer=false` 均固定为非授权状态。财报仅保留来源原本的 `months/scope/analysisPeriod`，不产生每日日期摘要，不把自然月金额摊到推广、SKU 或 30 日同比/环比。财报精确店铺身份映射仍须独立证明。

此首批候选只处理一个京东店铺的三份完整推广窗口与一个财报来源。ERP 销售、B 端、市场、SKU/SPU、UV 的期间/来源身份需各自版本化扩展；市场榜单单日样本不能冒充 30 日日覆盖。包络目前没有数据库持久计划、拥有方零日回执、Agent 三窗口数值引用或 HTML/XLSX 注册。若要成为真正报告计划，后续必须以新版本接入持久计划摘要、来源采集/验证、封印/消费门禁及文件证明，旧版本验签与旧报告字节保持原样。
