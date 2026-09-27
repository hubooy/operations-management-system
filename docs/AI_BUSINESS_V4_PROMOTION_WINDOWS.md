# v4 京东推广三窗口精确选择（纯候选）

`business_analysis.business_promotion_v4_plan.prepare_candidate` 接收现有 `evidence_v4.build_plan` 结果及本期、环比、同比的显式来源键。它先按 v3 目录合同重建全部日/月来源身份、requestedWindows 和原始日期，复用 v4 每来源 `_estimate` 重新核对容量字段、来源摘要、任务摘要及完整计划摘要；随后只接受同一京东店铺、同一推广数据集、同一原始区间的 `current`/`previous`/`yearAgo` 对应键。环比采用前一等长期间，同比采用前一年同月同日且闰日收敛到月末。

本期必须明确选择。可选基期即使已在计划中但用户未选，也明确返回 `missing_requested_source`；原请求没有该窗口时返回 `missing_source_not_requested`。两者的 `sourceKey` 和数值比较能力均为空，不用零替代。未请求窗口携带来源、重复键、错窗口、跨店或错日期均拒绝。计划容量不支持时仍可保留纯候选及其 `runCapacitySupported=false`/逐来源 `capacityStatus=unsupported`，绝不推断可采集。

这只验证候选计划的自洽性。`sourceAuthorityVerified=false`、`measurementAuthorityVerified=false`、`persistentEvidenceVerified=false`，不授权拥有方取数、Agent、renderer 或报告生成。v4 原计划仅保存 `sampleRowCount` 和 `sampleMaxUtf8Bytes`，没有原始 `sampleRows`，无法用现有 `build_plan` 无损重建输入；本函数复用来源规范化与 `_estimate`，另行校验顶层摘要/容量字段。未来若原 v4 合同开放按已验证测量摘要重建计划的公共入口，应改为共享该入口，避免顶层规则维护漂移。
