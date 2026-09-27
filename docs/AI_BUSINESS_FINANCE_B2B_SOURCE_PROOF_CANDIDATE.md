# 财报自然月与京东 B 端三期来源证明表候选

此切片复用现有 `business_finance_v3_report_material.prepare` 的已封存自然月完整页链，以及 `business_b2b_report_material.prepare` 的同店 B 端本期/环比/同比完整页链。新增纯 `finance_b2b_source_proof.build_candidate/as_table` 投影最多 28 行、64 KiB 的类型表；默认关闭的 `business_finance_b2b_source_proof.prepare` 仅在提供精确 report/intent/sourceKey 时读取相应拥有方材料，不调用模型、导入、生产或文件发布。没有输入时状态为 `not_supplied`，**不是**业务零值、数据库或平台全局无数据。

财报每个自然月一行，逐月区分发布、缺月和核心指标缺值；月份的起止日只是自然月标签，财务金额不拆成经营业务日或 SKU 利润。B 端每个比较窗口一行，封存目录无来源写 `catalogue_only_missing_source`，有来源保留页数、行数、日期覆盖、缺日和证据摘要；封存目录缺 B 端来源不证明数据库或平台没有成交。表只展示来源状态/指标有值状态，不投影可以跨域加总的金额。

两个 owning 入口分别属于 v3 财报意图与 v2 综合报告。当前没有可验证的共同报告根或 ERP 店铺别名映射，因此 `sameReportAuthorityVerified=false`、`sameShopIdentityVerified=false`、`agentReadPersisted=false`、`registeredRenderer=false` 固定。B 端是否包含在 ERP 净销售或平台 SKU 支付中仍为 `unknown`，B 端占比、增量均为 null；广告归因金额、ERP 销售和 B 端金额不得相加。两份输入即使同一管理员读取，也不自动变成同一快照或正式 Agent 引用。

纯合成测试验证未提供、有发布月/缺月、有 B 端/目录缺源、错误摘要、伪造日摊和重叠结论拒绝。进入正式同报告前仍需：同一权威店铺/时间身份及来源修订绑定、B 端与 ERP/平台支付包含关系实证、财报自然月与 30 日经营期的并列口径、人审后的五 Agent 数值引用、renderer 新版本全量表与双格式同数，以及真实业务规模和权限/备份验收。

整合隔离验收：纯候选三项及拥有方 PostgreSQL 五项 `.runtime/ai-pg-5a3999e43c36/tests.log`（24.908 秒）通过，含财报与 B 端分别封存的有源、目录缺源、错身份及账号撤权。首轮组合 `TransactionTestCase` 缺 v3 `TestCase` 原有 AI 修订初值，夹具按正式 development 初值补回后通过；未修改生产 mutation/权限或把两份报告合成共同权威。无真实店铺数据或正式 Agent/文件采用。

## ERP 店铺销售并列切片（隔离候选）

同一入口可另外提供完整 `erp_pair_key/erp_sales_key/erp_master_key`，且必须同时给出 B 端的精确报告与京东店铺。服务复用 `business_erp_rollup_materials.prepare` 的封存销售和主数据逐页重放、映射归属与五层回卷；B 端继续由 `business_b2b_report_material.prepare` 独立重放。两份拥有方的 `reportBinding`、ERP 销售查询的店铺和本期起止日、来源控制摘要必须逐项一致，才新增一条 ERP 本期销售来源行。输出只显示来源状态、页/行数、指标有值状态与证据摘要，不输出可跨域加总的金额。ERP 净销售与 B 端支付的包含关系仍为 `unknown`，B 端占比、增量为 `null`。

`erpB2bSameReportOwningVerified=true` 只说明本次两个来源经同一已封存 v2 报告和当前账号重放；财报仍来自另一份 v3 自然月报告意图，整体 `sameReportAuthorityVerified=false`、`sameShopIdentityVerified=false`。精确京东店名不等于财报 scope 的别名映射。此切片不注册 Agent 工具或 renderer，不产生正式经营诊断，也不改变原 B 端目录缺源的 `null` 语义。正式同报告还需 v3/v4 与 v2 间的权威共同报告根、月份/30 日窗口并列、真实 B 端订单包含关系和原生 Excel/大数据量验收。

离线验收：纯合同 5 项通过；隔离 PostgreSQL 拥有方 6 项通过，日志 `.runtime/ai-pg-c943ef06ed21/tests.log`。新增用例核同一封存报告的 ERP/B 端与独立财报、错误 pair/来源/店铺和残缺 ERP 参数；无生产库写入、模型调用或正式迁移。
