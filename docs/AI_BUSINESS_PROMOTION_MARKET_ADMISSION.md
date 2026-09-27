# 市场 v2 报告准入 owning 候选

`ai_assistant.business_promotion_market_admission.describe` 从**一份当前账号拥有的既有词货报告**及其同一已封存 v2 证据出发，重新读取 `Reader.sources` 与两条市场来源的 `Reader.info`，自行构造 `sourceRef`、证据摘要、行数和日期覆盖证明；调用者只提交价格带、明确市场当前/基期 sourceKey 及两个观察日，不能提交覆盖回执或结果数字。它复用纯 `business_promotion_market_runtime_contract.prepare_candidate`，要求区间价格带和排名本期使用同一来源，基期为另一个同类别、榜单范围、榜单粒度、价格筛选和原始日期范围的 previous/yearAgo 来源。绑定结果还固定当前账号版本、报告/证据版本、完整目录及 Reader.info 根摘要，并在返回前重新核验账号和报告。

`describe` 对指定日没有封存行覆盖时返回 `date_not_covered` 与 `candidateEligible=false`，便于显示真实缺口；`require_observed` 在任一观察日缺覆盖时拒绝后续创建输入。**缺日期不是未进入 TOP，未进入 TOP 也不是零销量。** 本步骤只使用完整来源目录和检查点，不扫描三张市场事实表，因此结果始终 `sourceCoverageVerified=false`、`marketRowsReplayed=false`、`authorityVerified=false`；实际三表仍由 `business_market_composite_export.prepare` 在后续版本完整重放。

这只是为下一版 profile 准备的内部候选：当前仍使用既有 v1 词货报告作固定封存根，没有新 profile 持久化、Agent 工具、模型派发、renderer 或公开创建。价格带汇总与成员不可加总；市场 TOP 样本的 SKU/SPU 或金额不归属本店、ERP、B 端销售或利润。下一批需要给新报告 snapshot/workflow 增不可变 `marketSelector` 与来源证明、独立迁移守卫，并让 `business_diagnostic_screening._load` 识别该新 profile；旧 v1 路径保持不变。
