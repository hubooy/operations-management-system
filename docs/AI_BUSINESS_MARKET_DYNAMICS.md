# 市场样本价格带与进出榜：纯计算候选

2026-09-18。新增 `backend/business_analysis/market_dynamics.py` 与独立纯测试；未接 owning reader、Agent、报告、文件或生产。`authorityVerified=false` 固定，调用者提供的来源和摘要不能授予访问权限。

## 真实可用字段

核查 `backend/market/analysis.py`：规范来源为 `market_daily_top`，按固定京东/类目/scope/rankingDimension/priceBandFilter，SQL 明确筛 `period_start=period_end`，再按来源窗口读单日记录。每行保留 SKU 或 SPU、date、sample.rank、priceLowerCents、priceUpperCents、priceEstimated，及成交金额、数量、商品日访客六项上下界指标；首页含 control、coverage、excludedOverlappingPeriodRows。

纯层接受这一规范形状，完整 PageReconciler 对账行数、逐页哈希、指标控制数和调用者固定 expected；验证范围、页间来源版本、真实日期覆盖、SKU/SPU身份、合法非负区间以及商品日唯一性。周/月附加字段、错误日期、不同榜单范围及重复商品日拒绝。已经被 reader 排除的重叠周/月数量保留披露，不再计入。

必须说明：规范页没有原始 period_start，纯层无法独立证明输入确实曾经经过该 SQL。仅有一个 date 不能把伪造的月汇总变为单日事实；未来 owning 接入必须从真实规范 reader 和封存链提供来源。当前不声称原文件或业务来源真实性已验证。

## 两个视图

`price_band(source, pages, expected, bands)`：bands 必须预声明，1—20个不重叠、升序的 `[lowerCents, upperExclusiveCents)`，最后一段可无上界。源价格上下界都已知、非估算且整个闭区间完全落入一段才归入；跨段/段外为 `unallocated_interval`，缺界为 `unallocated_missing`，估算价为 `unallocated_estimated`。不取中点，不拆分成交，也不按比例分摊。

每段各指标上下界独立累计，并记录 presentRows/missingRows；全部缺失保留 null，部分已知之和只是已知行小计，不能宣称完整样本区间。输出 members 保留每个商品日的 SKU/SPU、日期、排名/价格区间及源行哈希，按日期和精确商品标识排序。支持多日价格样本，但重复出现商品保留为不同商品日；不是独立商品销量或去重UV。`shareEstimated=false`，不估算份额。

`rank_entry_exit(source, pages, expected, baseline_source, baseline_pages, baseline_expected)`：首片严格支持**一个当前日对一个明确 previous/yearAgo 日**；两来源原始日期和五项榜单身份须一致，窗口标签由调用方指定，按既有 comparison_periods 解释。多日窗口拒绝，不能把多日排名合并为一期排名或暗猜期末日期。多日价格分析不受此限制。

同一 SKU/SPU 输出 current/baseline 的观察状态、排名及完整六项指标。缺席只写 `not_observed_in_top_sample`，排名和指标为 null。仅两日来源都有记录时标记 `entered_observed_top_sample` / `left_observed_top_sample`；整日来源缺口则为 `insufficient_date_coverage`。两个日期都观察到且排名存在才计算 `rankImprovement=baselineRank-currentRank`；不把进榜当新品上市，也不把离榜当退市或零销量。

## 容量及可复核性

每页最多100行、完整规范JSON至多128 KiB；输入最多2000页、20万行、64 MiB，进出榜两期合计也受相同限制。完整结果至多20万行、64 MiB，超过上限整表拒绝，不截字段、不丢未分配桶。结果当前是有界内存对象，不承诺峰值进程内存仅64 MiB，未来 provider/文件适配还需自己的38 KB分页或分卷。

输入在接触处理前有界复制；结果规范JSON回读，不与输入共享嵌套对象。表、行摘要绑定算法版本、完整来源范围/元数据、价格段和输出数值；排序确定。同一金额可用于两种视图但不可相加，两个视图均不能代表全行业规模或真实份额。

## 验证

`PYTHONPATH=backend python -m unittest business_analysis.test_market_dynamics -v`：11项纯测试通过（0.034秒）。覆盖价格边界、跨段/缺界/估算桶、上下界独立累计、单日进出榜/缺日/SPU/明确去年同期、重复商品日、周月字段拒绝、区间和安全整数、来源与控制摘要、真实多页链、两期合计输入及输出容量、确定哈希与输入副本隔离。没有初始化数据库、读取正式数据或调用模型。

## 2026-09-23：内部封存来源服务候选

新增 `backend/ai_assistant/business_market_dynamics.py`，复用 `business_diagnostic_screening._load` 的真实报告/工作流/当前管理员绑定及 `business_sealed.Reader` 的完整封存链。支持 `page(report_id, params, principal)` 和固定位置/完整 rowId 的 `read_row`，未注册公开路由、模型工具、profile 或工程文件。

价格带要求完整显式 bands，不接受临时基期；进出榜要求同一固定榜单身份的明确 current 与 previous/yearAgo 单日来源，不接受 bands。读取的是选定封存来源，完整消费后及返回前均复验当前报告与账号。没有实时市场查询、重新下载、模型调用或业务写入。内部 context 给出的纯表副本仍不具授权，正常退出后 `page/read_row` 才返回选定来源完整遍历与报告绑定证明。

返回 binding 固定 report/source/baseline/view/algorithm/bandsDigest/tableBindingDigest。完整 pure tableDigest 保留为根摘要；分页结果另有 pageDigest，外层 responseDigest 绑定完整响应。每页最多20行，包含外层的规范UTF-8 JSON最多38,000字节；仅减少完整行前缀，不截单行、members 或未分配价格桶。单行或元数据超限时413，不发布部分成功。read_row 校验同视图、同价格段、同基期下的行位置与摘要，便于后续模型引用，当前尚未注册引用解析。

authority明确 `wholeMarketCoverageVerified=false`、`ownProductIdentityVerified=false`；市场单日资格来自原 owning SQL 所生成的已封存 market_daily_top，服务不将竞品解释为自家商品、不声称全行业销量或真实份额。

在最新main整合worktree静态编译通过，原pure11项再次通过（0.042秒）。新增 `ai_assistant.test_business_market_dynamics` 7项真实隔离PG测试通过（88.797秒），日志 `.runtime/ai-pg-c074849f3aa6/tests.log`：原市场SQL→多页封存→报告绑定、两视图与缺席状态、无外部调用/无live事实查询、错源/基期/账号范围、篡改/迟到源错误、最终撤权、精确行引用、完整38KB容量。未执行生产操作或模型调用；市场 Agent 工具和工程文件仍待接入。

2026-09-23 新增固定报告的只读 GET `/api/ai/reports/<reportId>/market-dynamics`，只允许当前 AI reader 签名与真实无范围管理员。价格段 JSON 完整解析并拒绝重复键/非数值，进出榜需明确基期，页模式与精确行模式互斥。新路由7项与旧 screening 路由4项在隔离 PostgreSQL 合跑11项通过，日志 `.runtime/ai-pg-8f22cb2594bd/tests.log`。此时内部只读服务已可被系统调用，但尚未注册 Agent 工具、节点证据引用或 HTML/XLSX 文件目录。

### 内部只读HTTP候选

上述owning服务7项隔离PG已由root验证通过，整合提交 `a14d6924`。新增精确 GET `/api/ai/reports/<reportId>/market-dynamics`，沿原签名、当前账号及 `ai_reader` 进程门禁，响应保留原完整封套与 `no-store`，未新增Agent工具或profile。

价格段选择必须给 sourceKey、view=price_band、bands JSON（最多8192 UTF-8字节），进出榜必须给 sourceKey、view=rank_entry_exit、baselineKey。列表默认offset=0/固定limit=20；精确行使用rowIndex和rowId，二者必须一起提供并与offset/limit互斥。未知/重复查询参数、JSON重复键和非有限数拒绝；bands仅允许明确段名与安全整数边界，完整范围规则继续交已有pure/owning核验。源范围、报告和末次账号复验不因HTTP接线而弱化。

新增 `business_market_runtime_tools.py` 与 `test_business_market_runtime_tools.py`，7项新增真实封存报告HTTP测试待root串行执行。静态编译通过；覆盖两视图/精确行、严格参数和JSON、签名/角色/范围/错误方法、完整38KB及撤权、无外部调用和旧screening门禁。没有运行生产或数据库迁移。
