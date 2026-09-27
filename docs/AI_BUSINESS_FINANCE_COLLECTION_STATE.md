# 财报页纯续读检查点候选

2026-09-24。`business_analysis.finance_collection_state` 从已提交的财务内部签名页协议读取完整页面，纯函数生成≤32 KiB 的版本化检查点。没有 Django、数据库写入、AI 工具注册、公共路由、模型或报告接线；`ai_assistant.0028` 仍拒绝 v3 事实块、检查点推进与封存。

未来调用方必须从当前 v3 owning 目录取得可信财务查询。首页 `consume(None, page, trusted_query=...)`，之后以 `next_arguments(checkpoint, trusted_query=...)` 得到 `query/offset/afterId/expectedSourceRef/expectedRevision`，将实际签名页交给下一次 `consume`。页必须完整符合 `business-finance-owned-page-v1`：38,000 UTF-8 字节上限、精确月份及 scope、原始日区间与自然月关系、完整 revision、相同 sourceRef、month→唯一完成 batch 及缺月身份、规范整页 SHA、行数组 SHA、真实递增 `FinanceLine.id` 和绑定原值的 `rowId`。`offset` 与已读行数、`nextOffset/nextLastId` 与实际返回前缀必须逐页一致；空页只允许来源真实零行时的唯一首页。

检查点固定保存完整来源版本与发布关系、总行数、已读行数、末行 ID、末页摘要、页链与行链、分月经营汇总/金蝶及合计/明细计数，以及12项核心指标的有限状态。它不保存整份事实；每个科目只保留缺失/唯一原值/多科目歧义所需状态，保留真实零与 NULL 区别。仅 `summary` 进入核心指标覆盖，`kingdee` 独立计数；`source_row_count>1` 只标记源端已合并，不能恢复原单元格。完成后 `result` 才给出逐月 `missing_month/missing_subject/ambiguous_subject/non_numeric/missing_value/present`，不加总月比率、总计与明细，不分摊近30天的月账，也不推 SKU 利润或推广因果。

一次来源最多100,000行、1,999个数据页；在共享64 MiB 上限内为未来完整终结清单预留一块及38,000字节，数据页至多使用剩余字节。到达容量上限整源拒绝，不能裁剪后标完整。缺页、重排、跨版本/跨批次/跨查询、伪造页或行摘要、重复/倒序行和已完成后续页均拒绝。纯检查点自带摘要，用于发现意外改写；任何人均可重算这个摘要，它**不是**数据库权限、独立批次证明或可转让授权。实际账号仅由已签名的 finance reader 及未来 AI owning 的当前 AppUser/CAS 再验证；`persistentEvidenceVerified=false` 和 `businessCoverageVerified=false` 固定。sourceRef 绑定上游真实账号、财报版本、批次与行数，但页内不附账号明文，纯层将其作为不透明来源身份逐页比较；未来封存仍须复核真实账本页链与当前账号。

纯测试：`PYTHONPATH=backend python -m unittest business_analysis.test_finance_collection_state`。覆盖两月两页与24个月缺口、NULL/零、经营汇总与金蝶、源比率、重复科目歧义、真正的行链、错误版本/批次/分页/行摘要、检查点篡改及字节/页数限额。尚未通过持久 AI ledger 或五 Agent 真实读取，不能把该纯结果当经营报告。
