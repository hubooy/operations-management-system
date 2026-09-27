# v4 推广拥有方页容量测量候选

本切片选择 **575,095 行推广来源的实际分页容量**。参考留存文件可证明历史本期 281,759 行、前期 293,336 行及其自身摘要，但无法证明当前拥有方工具页大小、修订和成功审计。旧 v2 整任务上限 2,000 页/64 MiB 已不足；v4 的 16,384 页/2 GiB 单来源、65,536 页/8 GiB 整任务上限只是算术边界。`evidence_v4.build_plan` 的 `maxRowUtf8Bytes/pageEnvelopeUtf8Bytes` 仍由调用方声明，不可直接作为正式容量批准。店铺 UV 的实际平台字段、业务日与去重定义尚缺，按 [静态缺口](AI_BUSINESS_JD_SHOP_OVERVIEW_UV_GAP.md)不能先造“可信 UV”解析器。

新增 v2 纯 `v4_promotion_page_capacity.measure_complete_pages` 接收一条精确京东推广来源及逐页 `{requestCursor,rawPage}` 迭代器，不负责取数。**已交出**的每页最大 128 KiB，累计最多 16,384 页/2 GiB、1,638,400 行；页间检查 600 秒，不承诺中断阻塞迭代器或限制其在 `yield` 前分配的内存。只保留一页、固定元数据、数值控制和日期集合，不把原始商品行写入结果。它拒绝重复 JSON 键/非规范字节、跨店/错窗口、非法 rowId/哈希/布尔数值、修订变化、调用方声明的请求游标不连续、缺末页、超额与控制汇总不守恒；计算实际页数、行数、字节、最大行 UTF-8 宽度、最大页包络、原页字节链摘要，再用既有 v4 公式作**诊断估算**。若公式给出的页数或字节上界低于已见实际量，`capacityArithmeticSupported=false`，不截断。

此纯层的 `v4Measurement` 固定为 **null**，`capacityPlanMeasurementAvailable=false`；即使页流内部自洽且估算未低于已见量，调用方自报的 requestCursor 也不是拥有方成功工具审计，不可直接喂入 `evidence_v4.build_plan`。`owningSourceAuthorityVerified=false`、`signedRequestCursorAuditVerified=false`、`blockingReadDeadlineVerified=false`、`upstreamAllocationBoundVerified=false`、`upstreamSignatureVerified=false`、`productionRowWidthApprovalRequired=true`、`sealerOrReportAuthorityGranted=false` 固定。后续拥有方桥必须逐页比对签名工具请求参数/游标、成功审计、原字节、当前账号与修订，并在真正有界读取后才可另做版本化测量准入。纯测试含规范页、缺页/重排/错游标/跨店、强类型、真实页数超过算术估计及不能直接建 v4 计划；历史 281,759/293,336 行的 4,776/4,972 页仍仅是用 2,063/2,061 字节行宽与假设 8 KiB 页包络的**非权威估算**。

后续实际门槛：由已授权的拥有方 reader 把当前同店三窗口的每一页原字节、签名请求/成功审计、修订水位和页链交给受保护测量器；量出当前30天与同比完整三期、月财务、ERP、SKU/SPU、B端及市场各来源的真实页/字节，核任务总上限、临时磁盘峰值和 180 秒 claim/600 秒全链耗时。市场 TOP 单日样本不能当市场30日完整覆盖；财报自然月不日摊；店铺区间去重 UV 无源时继续列缺口。未通过这些数据门槛前，不能把三期多域同报告、五 Agent 或 HTML/XLSX 标为完成。

整合分支纯回归 `business_analysis.test_evidence_v4` 与本候选合计 9 项通过；没有当前拥有方真实页或生产规模测量。测量正文始终把来源授权、签名审计和报告准入标为 false，历史参考计数仍只能用于排容量风险，不能转成准入证明。
