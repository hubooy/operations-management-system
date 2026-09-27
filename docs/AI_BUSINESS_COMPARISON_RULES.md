# 经营分析比较期间规则候选

`backend/business_analysis/comparison_rules.py` 是纯日期解析模块，尚未接入计划、来源读取、Agent、封存或报告。它不改动旧 `contracts.comparison_periods`：已有证据、报告及其摘要仍使用 `previous_equal_length_and_previous_year_clamped` 原协议。

新任务须明确指定一种规则，不能凭报告标题“环比”猜测：

| 规则 ID | 本期 2026-09-05 至 2026-09-20 的环比基期 | 含义 |
| --- | --- | --- |
| `previous_equal_length_v1` | 2026-08-20 至 2026-09-04 | 旧 AI 分析口径，紧邻本期的前 16 天 |
| `sales_custom_calendar_month_v1` | 2026-08-05 至 2026-08-20 | 与当前销售页面的自定义同月多日口径一致 |

`sales_custom_calendar_month_v1` 的边界同当前 `sales.summary._custom_comparison_period`：单日比较前一天；同月多日逐端点移到上月并夹到月底；完整自然月比较上一个完整自然月；跨月区间比较紧邻前一段等长日期。例如 9 月整月比较 8 月整月，天数分别为 30 和 31；3 月 30—31 日比较非闰年 2 月 28 日，天数为 2 和 1。两种规则的同比均把起止日期各向前一年，闰日夹到 2 月末，因此两期天数也可能不同。输出完整保留各期 `startDate/endDate/endExclusive/days`，不能把不同天数的总量解释成日均值。

解析器只接受严格 `YYYY-MM-DD`、2000—2098 年的请求日期和连续 1—93 天的请求区间。可提供明确的数据截止日：它落在请求区间内时缩短本期并重算环比、同比；早于请求开始日直接拒绝，避免无本期事实仍给出可比增长；等于或晚于请求结束日不调整。返回固定的 `schemaVersion/timezone/comparisonRule/requested/dataCutoffDate/periodAdjustedToDataCutoff/current/previous/yearAgo` 字段；`validate_resolved_periods` 对完整持久化结果按规则重算并拒绝缺字段、额外字段或任何值变化。93 天上限属于 AI 来源契约；销售页面可支持更长自定义区间，此候选不会把它们自动纳入 AI 任务。

后续整合须新建明确版本的计划请求和分析上下文，将规则 ID 与完整解析结果固定在计划、来源查询、证据目录及报告快照中，并纳入相应摘要。销售、市场、网店三个 owning reader，封存身份核验，筛查、商品关联、推广词与 SKU、市场计算、Agent 工具及 HTML/XLSX 导出都必须以同一固定结果校验。原 v1/v2 输入及旧报告继续走原函数；新结果不得被默认为旧报告字段，也不能重新解释已封存的旧页。正式页面与 AI 报告若使用不同规则，展示各自规则名称和实际基期日期。

比较金额前仍要检查每个来源实际日期覆盖和指标口径。当前 ERP 45 天滚动销售不保证上月同期或同比有事实；来源缺失时显示不可比，不能补零。完整自然月天数不同是日历规则的正常现象，报告需展示两期天数，并明确增长率基于原期间总量；若业务需要日均增长率，必须另立指标及规则版本。
