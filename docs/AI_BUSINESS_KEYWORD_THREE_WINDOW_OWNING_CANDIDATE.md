# 三期关键词×明确推广 SKU 拥有方候选

`backend/ai_assistant/business_keyword_three_window_materials.py` 增加默认关闭的内部 `prepare(report_id, source_keys, principal, enabled=True)`。它从同一 sealed-v2 集成报告选定的京东推广来源，先完整读取本期，再分别用既有 `business_promotion_keyword_sku.table` 完整读取本期对前期、本期对去年同期；未选基期标记为 `missing_source`。它不通过公开 API、Agent 工具或 renderer 暴露，也不修改现有 13 表 v1 的任何字节或表头语义。

各次读取必须通过现有报告拥有方、封存页链、逐页控制总额、来源修订与本期/基期指标口径核验。候选按原始 `keyword` 加**明确推广 SKU** 的完整实体身份合并，核对两次基期读取中的本期分组和指标完全一致，最后重新核验报告与管理员。搜索词、触发 SKU、跟单 SKU、通用商品 SKU 和商品名称均不能替代这两个身份字段。缺身份桶仍保留来源金额但不可当具体词货执行；缺来源、实体未出现、指标 null 和缺日不补零。推广归因金额不当 ERP 净销售或利润，旧店铺 UV 缺口与 ERP 历史 SKU 归属缺口保持不变。

结果含精确报告绑定、三期来源键、来源证据摘要及修订、计划与期间摘要、每次完整表摘要、按实体的三期状态/指标、两基期比较、总行数和规范行摘要。`agentReadPersisted`、`registeredRenderer`、`authorityVerified`、`published` 恒为 false。实体上限 50,000 行、完整候选 UTF-8 上限 32 MiB；超限整体拒绝，不裁剪。当前 v2 来源每个仍限 2,000 页/64 MiB，不能覆盖参考推广合计 575,095 行；本切片没有让旧 v4 封存获得报告生成资格。

纯测试覆盖三期合并、缺源/缺身份、双遍本期漂移、指标口径漂移、默认关闭与最终报告复验。隔离 PostgreSQL 目标测试使用真实 sealed-v2 集成报告和拥有方 Reader，核当前推广来源、缺基期、不调用模型/工具以及所有权威标志关闭。正式三期大容量报告仍需新版本可报告 v4 HMAC seal、财报店铺拥有方映射、0071 创建时 SQL 链接的受限页读取、版本化分卷 writer、真实两店全量行核对和原生 Excel 验收。
