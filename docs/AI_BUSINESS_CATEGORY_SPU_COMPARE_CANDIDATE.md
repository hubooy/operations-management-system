# 品类与 SPU 三窗口对照候选

`business_analysis.cross_source_category_spu_compare.prepare_candidate` 是纯计算合同，不读取数据库、不调用 Agent、不生成报告文件。它要求同一已固定跨来源计划的三个窗口，以及每个已选 ERP 窗口的五层回卷 manifest/完整 NDJSON、每个已选商智原生 SPU 窗口的完整封存页。缺源窗口明确传 `None`。输入的封存目录与 Reader 信息由上游提供，本函数不会据此授予权威；正式接入仍需 owning Reader 重放及账号/报告栅栏。

函数复算计划，核对 ERP manifest 的报告、账号、mapping pair、精确销售与主数据来源证明，读完并核验五层逐日流、行 ID、各表摘要、每日类目/SPU 与已分配 SKU 的**同身份事实数及十一项指标守恒**。商智原生 SPU 从自身完整分页与来源控制总额核验，只使用原生 `spuId`，不把 SKU 回卷当原生 SPU。

ERP 的 `category_day`、`spu_day` 使用导出时的**当前主数据**归属，并没有历史归属快照。候选仅展示各窗口各自的观察值、缺源/缺日/实体未出现状态；即使两期类目或 SPU 文本相同，也返回 `historical_identity_unverified`，不提供 ERP 跨期差额和增长率。历史品类/SPU 趋势需要另建可校验的日期有效归属源，不能事后拿当前 SKU→SPU 关系回填。

商智原生 SPU 仅在相同原生 ID 两期均逐日可见、所有该指标事实非 null、基期严格为正时计算差额和增长率（bps，四舍五入）。缺源、缺日、空记录、实体在某日未出现、部分 null、零或负基期均保留明确状态，不将其补零。ERP 退款后销售、商智支付和推广金额从不跨域相加；商品日访客不是店铺去重 UV。输出 `authorityVerified=false`、`registeredRenderer=false`，不表示可发布工程报告。
