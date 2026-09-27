# 财报原始列证据 v2 候选

现有财报解析以店名形成 `scopeKey`，按 section、scopeKey、科目聚合。不同组的同名店铺可能在发布前合并，因此当前 `FinanceLine`、`source_row_count` 或年度目标别名不能证明每个原始来源列属于哪家网店。

`lib/finance/column-evidence-v2.ts` 是独立的聚合前纯提取器。它按原工作表的行、列、组名和店名记录每个规范财报单元格的金额或比率，并标出旧聚合键会合并的同名列。结果有固定 500 列、100,000 格、32 MiB 上限；重复坐标、表头继承漂移与行列错配整体拒绝。它不改变已发布财报行，也不把组名当作平台身份证明。

显式 `prepareNormalizedFinanceImportV2Candidate` 通过原解析器的可选坐标捕获读取这些原始列，返回旧聚合月表与逐月证据；默认 `prepareNormalizedFinanceImport` 的 v1 JSON 字节保持原样，合成黄金摘要 `09fe2a0ce8e1e56138bbb9fb618f7ee65c287d664f3f7cd13654267d8841db36`。v2 源文件先限 8 MiB 再计算文件摘要，每份候选总体限 32 MiB，超限不返回虚构文件摘要。新旧相关 12 项测试通过。

当前候选虽在 Worker 内对提供的文件字节求摘要，后端尚未独立核原始 XLSX、已发布批次或稳定网店 ID；`rawFileHashVerifiedByBackend`、`completeWorkbookBindingVerified` 与 `financeShopMappingVerified` 恒为 false，`backendImportSupported=false`。未来 v2 导入要以新的内容指纹绑定原文件摘要，在原发布事务内只追加列/格证据；旧月只允许核对原文件和当前批次后附证据，不能调用会删除重写财报行的既有导入路径。没有可核原文件的历史月份继续标记 `legacy_unknown`。旧 v1 导入协议与报表保持原义。
