# 店铺逐来源三窗口对照候选

`business_analysis.cross_source_window_compare.prepare_candidate` 只接收同一个固定单店计划及其本期、前一等长期间、去年同期的三份 `cross_source_daily_columns` 材料。它重新运行计划合同，逐份核对完整材料摘要、报告与计划摘要、窗口与日期、来源键及来源绑定，并逐日核对店铺列的行 ID、来源状态、指标有值/缺值计数及来源控制汇总。输出是未注册的纯数据候选，`authorityVerified=false`，不能当作数据库、Agent 或文件交付授权。

ERP 销售、ERP 未分配子集、商智原生 SKU、商智原生 SPU、推广分别成列；ERP 未分配明确标记为 ERP 总额的子集。任何 ERP、商智、推广或平台归因金额不跨域求和，商品日访客不命名为店铺去重 UV。各窗口保留 `missing_source`、`selected_no_records`、`date_not_covered`、`partial_metric_coverage` 和 `null`；`dayStatusCounts` 同时保留缺日与部分缺值日的数量，汇总状态优先提示缺日。如果一个来源的其他指标缺值，`sourceStatus` 仍显示部分缺值；当前指标自身有值且覆盖完整时，`status` 可为 `observed_rows`，只比较这个同名指标。

增长率采用整数基点（10,000 基点为 100%），只有本期与基期的该指标均覆盖全部日、无缺值、数值非空、且基期严格为正时才给出。零基期、负基期、任一缺日、缺来源、部分缺值、缺指标或无损整数边界超限均不给增长率。**负基期不计算增长率是当前候选的保守口径选择**，并非把负数改为零；差额若在无损范围内仍保留。同比为原区间总量比较，闰日夹止可能造成基期天数不同，未按天归一。

本层逐行验证店铺日列。输入 SKU 日行被包含在材料摘要中，但此函数不重新逐行核验 SKU 日结构或与店铺的回卷；输出明确标记 `skuDayRowsIndependentlyValidated=false`，不能据此宣称 SKU 维度结论已获授权。上游三份材料的拥有方封存、SKU 日明细复核、正式 Agent 和 HTML/XLSX 接线仍是独立工作。
