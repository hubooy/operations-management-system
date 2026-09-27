# 财报店铺映射只读诊断候选

`backend/finance/shop_mapping_diagnostic.py` 新增默认关闭的内部 `inspect(principal, months, enabled=True)`。它只允许当前无范围限制的管理员，以当前财报修订为前后栅栏，读取所选连续自然月的 `FinanceMonth`、完成 `FinanceImportBatch` 与 `FinanceLine` 店铺范围身份分组。返回逐月 `completed_metadata` / `missing_month` / `incomplete_publication`，以及每月精确 `scope_key`、`scope_name`、`group_name`、规范化科目行数和最大 `source_row_count`。只输出身份与状态，不读取金额、比率、原文、客户或商品事实；最多 24 个月、10 万规范店铺行、1,000 个候选、128 KiB，超限整体拒绝。

诊断标记同月同名落在不同财务范围/组、同一 `scope_key` 对应多个名字/组，以及 `source_row_count>1` 的**可能**先期合并。平台提示仅在组名恰好为“京东”或“天猫”时给出，其他组保持 `null`；它只是财报组文本，不能证明平台身份。`shopNameCandidate` 也只是财报 `scope_name`，不是网店主数据的稳定店铺 ID。所有结果固定 `netshopStableIdentity=null`、`financeShopMappingVerified=false`、`mappingAuthorityVerified=false`；没有 MAC、映射表、迁移、公共路由、Agent 工具、renderer 或文件发布。

现有 `lib/finance/parser.ts` 按店名生成 `shop:<规范店名>`，`aggregateLines` 按 section/scopeKey/科目合并，未把 group 放入键。若源文件的跨组同名列已在导入前合并，当前规范账本不能还原原始组；`source_row_count>1` 只能指出风险，不能断定哪些源列发生冲突。合成财报夹具包含 `shop:京东:同名店`、`shop:天猫:同名店`，不代表正式解析器必然产生带平台的键。

纯测试使用现有财报合成夹具，覆盖同名双组、缺月、先期合并风险、同 `scope_key` 跨组、平台未知、重复/容量拒绝及默认关闭，6 项通过。隔离 PostgreSQL 2 项使用真实导入后的 FinanceMonth/Batch/Line，证明双读无金额/比率/原文字段与写入、返回前撤权拒绝；目标数据库已停止。仍需原始财报列身份、已发布月份与网店稳定店铺的一对一业务核对，才可以另建独立保护映射证明。当前诊断不能传给可报告 v4 signer 当作财报映射权威。
