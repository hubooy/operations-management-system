# SKU 三窗口逐来源对照纯候选

`business_analysis.cross_source_sku_window_compare.prepare_candidate` 复用既有单店计划及三个 `cross_source_daily_columns` 材料。它重验计划、材料摘要、店铺日来源控制、每条 SKU 日行的身份/日期/行摘要，再按业务日证明 ERP 店铺列等于已分配 SKU 加未分配池、商智 SKU 列等于原生 SKU 行、推广列等于明确 SKU 与缺身份桶之和。重复 SKU 日身份、篡改行、日期错位或重算摘要后破坏逐日守恒均整份拒绝。材料自身仍不是拥有方数据库授权，结果固定 `authorityVerified=false`。

每个来源独立形成 ERP 已分配 SKU、商智原生 SKU、推广三个列族；商智原生 SPU 支付不汇入 SKU，ERP 未分配池保留在店铺，不摊至商品。推广 `promotedSkuId` 缺失只形成 `skuId=null` 的不可操作桶，该桶仅展示推广指标、不参与真实 SKU 的增长计算。ERP 分配 SPU 与商智 SKU 原生 SPU 分别保留在各个窗口，当前主数据归属不回填成历史所有权。相同 SKU 字符串只供并列观察，不证明订单级广告归因。

对某 SKU、某来源、某指标，仅在本期及对应基期的该 SKU **每个日历日都有该来源事实行、该指标无缺值、两期数值非空且基期为正**时计算差额和整数基点增长。缺来源、来源日缺失、SKU 在来源有记录的日期未出现、部分缺值、零/负基期或缺指标都保持明确状态，不补零。负基期不计算增长是当前保守口径。缺身份推广桶始终不提供增长率。三个窗口中的 ERP/商智/推广数值不互相相加，商品日访客不是店铺去重 UV。

v2 口径补正：上述增长条件仅适用于**商智原生 SKU**与有明确推广 SKU 的推广来源。ERP `erpMatched` 的 SKU 归属由当次当前 master 分配，即使两期字段齐全、SKU 字符串相同，仍不能证明历史商品归属；其环比/同比固定为 `historical_identity_unverified`，差额和增长率都为 null。原生 SKU 与推广各自来源内的可比计算保留。ERP 未匹配/多义事实仍留在店铺层未分配池，并按业务日随已分配 SKU 一同回卷，不摊入任何商品。新结果 schema 为 `business-cross-source-sku-window-comparison-candidate-v2`，旧候选不被重新解释，正式 renderer 仍未注册。

本纯候选最多保留 20,000 个 SKU 身份、50,000 条对照行、64 MiB 完整输出，超过即拒绝，不截断。没有 Agent、正式 renderer、数据库写入、模型或业务调整权限；后续仍须以 owning 已封存来源和正式逐行复核赋予业务权威。

纯回归目标为 `business_analysis.test_cross_source_sku_window_compare`，并联动 `test_cross_source_daily_columns`、`test_erp_fact_rollups`。数据库阶段应新增隔离 PG `ai_assistant.test_business_cross_source_sku_window_compare`：使用同一已封存报告的三窗口 ERP/商智 SKU/推广拥有方页，复验当前账号与来源修订、未分配池和各来源每日行/费用守恒、历史 ERP SKU 增长关闭、原生 SKU 完整覆盖时的可比计算，以及错报告/撤权/末页篡改拒绝。现有纯材料不等于这个 PG 授权测试；参考 575,095 行推广来源仍超过 v2 日材料容量，须另走 v4 版本化接线与真实行宽验收。
