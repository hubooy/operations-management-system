# 市场 v2 内部只读工具预览

`ai_assistant.business_promotion_market_tool_preview.read` 目前只在内部调用，不注册到模型工具目录，也不创建 Agent 派发、工具结果或本人读取回执。调用必须带当前报告、精确市场 selector 和本次 owning 准入摘要；服务重新执行 `business_promotion_market_admission.require_observed`，固定价格带来源、同类别与榜单范围的基期来源及两个有记录的观察日。模型参数只可选择 `summary`、固定 20 行的 `page` 或给出行位置与完整 ID 的 `row`，不能覆盖报告来源、日期或价格带。

每次调用都先由 `business_market_composite_export.prepare` 完整重放区间价格带和两日榜单来源，再用 `market_report_tables_v2.tables` 校验三份完整 NDJSON 的字段、数值、行数、页数及 SHA。`summary` 返回三张表的行数/页数/摘要、两源有记录日期覆盖和双日观察状态；`page`/`row` 则从同一来源的 `business_market_dynamics` 或 `business_market_observation` 精确拥有方视图重新读取，并核对其表根与完整三表清单。返回前再次核验账号、报告及准入摘要；超出 38,000 UTF-8 字节整次拒绝，不截断单行。

价格带汇总与商品日成员是同一 TOP 样本，不相加；市场金额、SKU 或 SPU 都不归属本店、ERP 或 B 端销售。缺指定观察日拒绝准入，不能把 `date_not_covered`、未进入 TOP 和零销量合并。结果始终 `agentReadPersisted=false`、`actualAgentBound=false`、`authorityVerified=false`：服务完整重放不等于某个市场专业 Agent 亲自读完；下一步必须由实际 job/provider dispatch/result 账本持久证明本人 summary 与引用页/行。
