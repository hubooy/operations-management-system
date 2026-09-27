# 词货报告市场动态候选（纯协议，未接入正式运行）

现有 v2 封存目录要求全部来源共享同一原始起止日期。近 30 天报告不能另塞一个原始日期为单日的榜单来源；旧 `market-daily-dynamics-v1` 进出榜也只接受原始单日来源。本候选保留同一原始 1—93 天范围：京东市场 `current` 来源用于区间价格带，同时从它与同查询的 `previous` 或 `yearAgo` 来源中选择两个明确观察日。上一等长期间按相同日序号配对；同比按前一年同月同日、闰日收敛到月末。来源的类目、经营范围、SKU/SPU 榜单粒度和价格筛选必须相同。

`business_promotion_market_runtime_contract.prepare_candidate` 固定所选来源、非重叠价格带、观察日及两份覆盖回执摘要。回执中的日期状态须与声明的有记录日期一致，但纯函数不能证明回执真的来自封存页，所以结果恒为 `authorityVerified=false`、`sourceCoverageVerified=false`、`registered=false`。将来 owning 服务必须经报告身份和封存 Reader 读取完整页，重建 `PageReconciler` 结果，并与候选的来源引用、摘要及日期覆盖逐项核对后才可注册工具或发布报告。不得把客户端提供的候选摘要当权限。

`market_dynamics_v2.rank_entry_exit` 已在纯层对两期**完整**多日来源逐页核验，然后仅比较所选两日。只有两日各有 TOP 样本记录，才把单边出现标为“进入/离开所观察 TOP 样本”；缺少观察日记为覆盖不足。商品某日未在 TOP 样本中出现时，名次与指标为 `null`，绝不补零或推断退市。市场 SKU/SPU、价格带汇总与成员行都不能直接记为本店、ERP 或 B 端销售；市场区间下界、上界、缺失桶分别保留。

单侧缺少观察日使用 `date_not_covered`，该日有榜单记录但某商品未出现才使用 `not_observed_in_top_sample`；任何一侧日期缺失时整行仍为 `insufficient_date_coverage`。v2 类型表按两侧覆盖布尔逐项核验这两个状态，即使行、NDJSON 与清单摘要被一并重算也不能把缺日伪装为商品未入榜。SKU/SPU 必须恰有一个非空有界文本身份，布尔值或整数即使摘要自洽也拒绝。

本批只有新纯协议、算法和测试。未修改旧 promotion profile、四工具目录、持久报告/迁移、人审、renderer7、公共创建入口或生产系统。后续正式交付须另行版本化完成 Agent 派发和本人已读回执、市场数值引用、人审、三张类型表与多卷证明；旧报告和文件保持原义。

后续内部 owning Reader `business_market_observation` 已可针对同一报告中的本期/基期市场来源完整重放封存页，返回固定两日的有界表、分页与精确 `rowIndex + rowId`，并在交接后复核报告/当前角色。签名 Django reader GET `reports/<reportId>/market-observation` 与服务端 TS 只读适配器仅用于内部开发；它们没有加入 Next.js 公开路径、工作台入口或 Agent 工具目录。回执证明所选封存来源的完整读取及观察日覆盖，不证明全市场覆盖或本店、ERP、B 端销售归属。

内部 `business_market_composite_export.prepare` 将同报告当期**区间**价格带表与固定**两日**进出榜表分开读取，形成 `business-market-composite-materials-v2` 三份完整 NDJSON 材料。价格带汇总/成员绑定同一来源表摘要；进出榜另有本期、基期来源与观察日绑定。`market_report_tables_v2.tables` 验证每份材料的来源绑定、行位置/摘要、规范字节、分片 SHA 和总容量，再生成三张可一次消费的类型表；进出榜保留本期/基期源行摘要，日期缺失时数值仍为 `null`。两种价格带呈现不可相加，市场样本也不可归入本店销量。此数据材料仍为 `registeredRenderer=false`，不提供五 Agent 审核或正式报告发布权限。

候选市场数值引用仅接受已固定的价格带汇总指标或本期/基期名次、样本成交区间；引用中没有可由 Agent 填写的数值，必须绑定 `jobId/role/reportId`、同报告来源、价格带摘要或两观察日、表摘要及完整 `rowIndex+rowId`。进出榜名次改善仅在两日均有记录且两个名次都被观察到时可引用；`date_not_covered` 和 `not_observed_in_top_sample` 均不能补零。`business_market_numeric_claims` 的进程内准备会将候选回执与实际 job 身份及 owning `read_row` 当前结果重新比较，但现阶段**没有市场工具的持久 Agent 派发/结果账本**，也不验证回执声明中的请求签名摘要。因此结果明确为 `signedTransportVerified=false`、`agentReadPersisted=false`、`authorityVerified=false`，不允许据此通过人审、发布正式文件或归属本店/ERP/B端销售。未来 v2 派发账本须先证明该 Agent 本人确实读取相同页或精确行。
