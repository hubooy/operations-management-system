# 财报 owning 有界页候选

2026-09-24。`finance.business_evidence_page.read_page` 是财务后端内部只读函数，尚未注册 HTTP 路由、AI 工具或证据采集器。它使用真实 `FinanceLine`、`FinanceMonth`、`FinanceImportBatch`、`FinanceDataRevision` 和 `AppUser`，仅向当前 active、无范围限制管理员提供精确月度账本页。不会写入财报、AI 证据或业务设置。

调用方传入且只能传入 `months`（1—24个连续自然月）、完整四字段 `scope`、明确的 `analysisPeriod`；日区间沿现有1—93天合同并必须被所选自然月覆盖。首次 `offset=0, after_id=0`；续页须同时提供上页实际 `nextOffset/nextLastId`、`expected_source_ref/expected_revision`。服务端核对 `after_id` 必须是真实所选行，且之前的精确行数等于 offset；按真实 `FinanceLine.id` 递增读取最多101行，以完整前缀装成最多100行、38,000 UTF-8字节的页。行保留16个原始标量字段及绑定完整 revision 和原值的 `rowId`，另有整页摘要、行数组摘要、实际下页边界、总行数。一个过长的事实使整页拒绝，不裁剪金额、文字或缺口。精确所选总行数超过100,000也拒绝。

每页读前后读取真实账号五列、完整财务 revision、所选自然月到唯一完成批次及其三个摘要、精确 scope 总行数。`sourceRef` 绑定这些标量与查询；任一变化或续页期望值不符则拒绝。未发布月份在 `missingMonths` 明示，精确范围没有行返回零行，不补零金额。`periodAlignment` 仍由既有纯财务来源合同计算：跨月近30天通常是部分月关系，禁止按日摊月金额。`section=summary/kingdee`、`is_total`、NULL、零金额与源比率原样保留；没有会计利润、SKU 利润或推广因果推算。

此页是**读取中的元数据绑定**，不是持久证据或整个来源内容哈希：为避免每页重扫全部账本，`sourceRef` 使用正式财务写入必须推进的完整 revision、完成批次链和精确总行数。若有人绕过财务写入权威且保持 revision、批次和总行数不变地直接改写事实，本页无法跨请求察觉；未来 AI collector 仍须完整保存所有页、验证原始行 ID 顺序与行链，封存后再给五个 Agent 分别读取。当前 `persistentEvidenceVerified=false`，不能从单页或页数声称完整分析。

实际 PostgreSQL 测试在 `finance.tests.test_business_evidence_page`，覆盖两个自然月与多页、真实行ID、NULL与零、经营汇总/金蝶、缺月、精确 scope 零行、错误页边界、错账号、页间/页内撤权和完整 revision/批次变化。测试不调用正式数据库。无新迁移、模型费用或生产变更。
